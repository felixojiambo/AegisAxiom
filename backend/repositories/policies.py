"""Policies + policy_targets repositories (Phase 2b).

``PolicyRepository`` returns the existing ``models.py:Policy``. Soft delete is
``deleted_at``; "live" means ``deleted_at IS NULL`` (the Mongo
``{"deleted_at": None}`` filter). The enabled toggle is a single
``UPDATE … SET enabled = NOT enabled RETURNING`` (§A6) instead of the Mongo
read-then-write, so two concurrent toggles cannot both read the same state.

``PolicyTargetRepository`` is the ingested-app target registry keyed on
(app_id, module, entity, event); ``app_id`` holds the app SLUG. No FK to
policies (operator ruling 2026-11-25).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from pydantic import BaseModel
from sqlalchemy import literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models.policies import PolicyRow, PolicyTargetRow
from models import Policy
from repositories.base import BaseRepository

# Fields a definition upsert / patch may write. Identity + audit columns excluded.
_DEFINITION = (
    "name", "description", "module", "entity", "enabled", "is_system", "priority",
    "trigger", "condition_logic", "condition_pattern", "conditions", "actions",
)


def _to_policy(row: PolicyRow) -> Policy:
    return Policy.model_validate(row, from_attributes=True)


def policy_to_api_dict(policy: Policy) -> dict:
    """The Mongo-era response document for a policy.

    ``condition_pattern`` was written only by the admin create/update path
    (the system seeds never set it), so the raw documents the API returned
    carried the key only on those policies. Emit it only when set, so the
    pinned ``policies_list`` contract (seeded policies) keeps its shape.
    """
    doc = policy.model_dump()
    if doc.get("condition_pattern") is None:
        doc.pop("condition_pattern", None)
    return doc


def _jsonable(value: Any) -> Any:
    """Pydantic sub-models → plain dicts for JSONB columns."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _ilike_contains(term: str) -> str:
    """``re.escape`` + ``$regex`` (case-insensitive substring) → ILIKE pattern."""
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class PolicyRepository(BaseRepository):
    # ── reads ──────────────────────────────────────────────────────────────
    async def list_active_for_engine(self, limit: int = 1000) -> list[Policy]:
        """Enabled, live policies by priority DESC — the engine's evaluation set."""
        rows = (
            await self.session.execute(
                select(PolicyRow)
                .where(PolicyRow.enabled.is_(True), PolicyRow.deleted_at.is_(None))
                .order_by(PolicyRow.priority.desc(), PolicyRow.created_at, PolicyRow.policy_id)
                .limit(limit)
            )
        ).scalars().all()
        return [_to_policy(r) for r in rows]

    async def list_policies(
        self,
        *,
        module: Optional[str] = None,
        entity: Optional[str] = None,
        enabled: Optional[bool] = None,
        search: Optional[str] = None,
        limit: int = 1000,
    ) -> list[Policy]:
        """Live policies for the admin list, priority DESC. ``search`` is a
        case-insensitive substring over name/description (trigram-served)."""
        stmt = select(PolicyRow).where(PolicyRow.deleted_at.is_(None))
        if module:
            stmt = stmt.where(PolicyRow.module == module)
        if entity:
            stmt = stmt.where(PolicyRow.entity == entity)
        if enabled is not None:
            stmt = stmt.where(PolicyRow.enabled.is_(enabled))
        if search:
            pattern = _ilike_contains(search)
            stmt = stmt.where(
                or_(
                    PolicyRow.name.ilike(pattern, escape="\\"),
                    PolicyRow.description.ilike(pattern, escape="\\"),
                )
            )
        stmt = stmt.order_by(PolicyRow.priority.desc(), PolicyRow.created_at, PolicyRow.policy_id)
        rows = (await self.session.execute(stmt.limit(limit))).scalars().all()
        return [_to_policy(r) for r in rows]

    async def get_policy(self, policy_id: str, *, include_deleted: bool = False) -> Optional[Policy]:
        stmt = select(PolicyRow).where(PolicyRow.policy_id == policy_id)
        if not include_deleted:
            stmt = stmt.where(PolicyRow.deleted_at.is_(None))
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return _to_policy(row) if row is not None else None

    # ── writes ─────────────────────────────────────────────────────────────
    async def create_policy(self, policy: Policy) -> Policy:
        row = PolicyRow(
            policy_id=policy.policy_id,
            **{f: _jsonable(getattr(policy, f)) for f in _DEFINITION},
            created_by=policy.created_by,
            created_at=policy.created_at,
            updated_at=policy.updated_at,
            deleted_at=policy.deleted_at,
        )
        self.session.add(row)
        await self.session.flush()
        return _to_policy(row)

    async def update_policy(self, policy_id: str, updates: dict[str, Any]) -> Optional[Policy]:
        """Patch definition fields of a LIVE policy; bumps ``updated_at``."""
        values = {k: _jsonable(v) for k, v in updates.items() if k in _DEFINITION}
        values["updated_at"] = datetime.now(timezone.utc)
        row = (
            await self.session.execute(
                update(PolicyRow)
                .where(PolicyRow.policy_id == policy_id, PolicyRow.deleted_at.is_(None))
                .values(**values)
                .returning(PolicyRow)
            )
        ).scalar_one_or_none()
        return _to_policy(row) if row is not None else None

    async def soft_delete_policy(self, policy_id: str) -> Optional[Policy]:
        """Mark a live policy deleted (``deleted_at`` only, as the Mongo
        ``$set`` did). Returns the policy as deleted, or None if not live."""
        row = (
            await self.session.execute(
                update(PolicyRow)
                .where(PolicyRow.policy_id == policy_id, PolicyRow.deleted_at.is_(None))
                .values(deleted_at=datetime.now(timezone.utc))
                .returning(PolicyRow)
            )
        ).scalar_one_or_none()
        return _to_policy(row) if row is not None else None

    async def toggle_enabled(self, policy_id: str) -> Optional[Policy]:
        """Flip ``enabled`` on a live policy atomically; returns the new state."""
        row = (
            await self.session.execute(
                update(PolicyRow)
                .where(PolicyRow.policy_id == policy_id, PolicyRow.deleted_at.is_(None))
                .values(enabled=~PolicyRow.enabled, updated_at=datetime.now(timezone.utc))
                .returning(PolicyRow)
            )
        ).scalar_one_or_none()
        return _to_policy(row) if row is not None else None

    async def upsert_system_policy(self, definition: dict[str, Any]) -> bool:
        """Seed one system policy, keyed on ``policy_id``. True iff inserted.

        Insert: enabled=true, created_by='system'. Conflict: rewrite the
        definition, force is_system / un-delete, and PRESERVE the operator's
        ``enabled`` plus the original ``created_by`` / ``created_at`` — the
        ``seed_system_policies`` contract, in one statement.
        """
        now = datetime.now(timezone.utc)
        defn = {k: _jsonable(definition[k]) for k in _DEFINITION if k in definition}
        defn.pop("enabled", None)
        stmt = pg_insert(PolicyRow).values(
            policy_id=definition["policy_id"],
            **defn,
            is_system=True,
            enabled=True,
            created_by="system",
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[PolicyRow.policy_id],
            set_={
                **{k: stmt.excluded[k] for k in defn},
                "is_system": True,
                "deleted_at": None,
                "updated_at": now,
            },
        ).returning(literal_column("(xmax = 0)").label("inserted"))
        return bool((await self.session.execute(stmt)).scalar_one())


class PolicyTargetRecord(BaseModel):
    app_id: str  # app SLUG
    module: str
    entity: str
    event: str
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PolicyTargetRepository(BaseRepository):
    async def upsert_targets(self, app_slug: str, module: str, entity: str, events: Iterable[str]) -> int:
        """One active row per (app, module, entity, event); idempotent.
        ``created_at`` is set on insert only. Returns rows written."""
        now = datetime.now(timezone.utc)
        written = 0
        for event in dict.fromkeys(events):  # de-dupe, keep order
            stmt = pg_insert(PolicyTargetRow).values(
                app_id=app_slug, module=module, entity=entity, event=event,
                is_active=True, created_at=now, updated_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    PolicyTargetRow.app_id, PolicyTargetRow.module,
                    PolicyTargetRow.entity, PolicyTargetRow.event,
                ],
                set_={"is_active": True, "updated_at": now},
            )
            await self.session.execute(stmt)
            written += 1
        return written

    async def deactivate_for_app(self, app_slug: str) -> int:
        """Mark every active target of ``app_slug`` inactive (re-ingestion prelude)."""
        result = await self.session.execute(
            update(PolicyTargetRow)
            .where(PolicyTargetRow.app_id == app_slug, PolicyTargetRow.is_active.is_(True))
            .values(is_active=False, updated_at=datetime.now(timezone.utc))
        )
        return result.rowcount or 0

    async def list_active_targets(
        self, *, app_slug: Optional[str] = None, limit: int = 5000
    ) -> list[PolicyTargetRecord]:
        """Active targets (optionally for one app), in a stable order."""
        stmt = select(PolicyTargetRow).where(PolicyTargetRow.is_active.is_(True))
        if app_slug is not None:
            stmt = stmt.where(PolicyTargetRow.app_id == app_slug)
        stmt = stmt.order_by(
            PolicyTargetRow.module, PolicyTargetRow.entity, PolicyTargetRow.created_at,
            PolicyTargetRow.event,
        ).limit(limit)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [PolicyTargetRecord.model_validate(r, from_attributes=True) for r in rows]
