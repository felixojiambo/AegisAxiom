"""Permissions repository (Phase 2b) — the persisted permission registry.

No Pydantic model existed in the Mongo era, so ``PermissionRecord`` is the local
DTO (CONVENTIONS §2). ``to_api_dict()`` reproduces the Mongo document: the
``implies`` key is present only on keys whose declaration carried it (the
seeder never persisted an empty list), so ``GET /api/roles/permissions`` keeps
its shape.

``get_implication_graph`` is the read behind ``validate_dag`` and the 60s auth
cache; it is one ``SELECT key, implies WHERE is_active``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models.permissions import PermissionRow
from repositories.base import BaseRepository

# Sentinel: "the caller did not declare implies" (leave the column untouched),
# distinct from an explicit list.
_UNSET: object = object()


class PermissionRecord(BaseModel):
    key: str
    group: Optional[str] = None
    label: Optional[str] = None
    source: str
    app_id: Optional[str] = None
    is_active: bool = True
    implies: Optional[list[str]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_api_dict(self) -> dict:
        doc = self.model_dump()
        if doc.get("implies") is None:
            doc.pop("implies", None)
        return doc


def _to_record(row: PermissionRow) -> PermissionRecord:
    return PermissionRecord.model_validate(row, from_attributes=True)


def _like_prefix(prefix: str) -> str:
    """Escape LIKE metacharacters so ``prefix`` matches literally (re.escape twin)."""
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class PermissionsRepository(BaseRepository):
    # ── reads ──────────────────────────────────────────────────────────────
    async def get_permission(self, key: str) -> Optional[PermissionRecord]:
        row = await self.session.get(PermissionRow, key)
        return _to_record(row) if row is not None else None

    async def list_permissions(
        self,
        *,
        source: Optional[str] = None,
        group: Optional[str] = None,
        app_id: Optional[str] = None,
        is_active: Optional[bool] = True,
    ) -> list[PermissionRecord]:
        """Registry query with optional filters, sorted by key."""
        stmt = select(PermissionRow)
        if is_active is not None:
            stmt = stmt.where(PermissionRow.is_active == is_active)
        if source:
            stmt = stmt.where(PermissionRow.source == source)
        if group:
            stmt = stmt.where(PermissionRow.group == group)
        if app_id:
            stmt = stmt.where(PermissionRow.app_id == app_id)
        rows = (await self.session.execute(stmt.order_by(PermissionRow.key))).scalars().all()
        return [_to_record(r) for r in rows]

    async def get_valid_keys(self, *, is_active: Optional[bool] = True) -> set[str]:
        stmt = select(PermissionRow.key)
        if is_active is not None:
            stmt = stmt.where(PermissionRow.is_active == is_active)
        return set((await self.session.execute(stmt)).scalars().all())

    async def list_keys_for_app(self, app_id: str, *, only_active: bool = False) -> set[str]:
        stmt = select(PermissionRow.key).where(PermissionRow.app_id == app_id)
        if only_active:
            stmt = stmt.where(PermissionRow.is_active.is_(True))
        return set((await self.session.execute(stmt)).scalars().all())

    async def count_active_for_app(self, app_id: str, *, key_prefix: Optional[str] = None) -> int:
        """Active keys registered for ``app_id``, optionally under ``key_prefix``
        (the former anchored ``$regex`` → ``LIKE 'prefix%'``, trigram-served)."""
        stmt = (
            select(func.count())
            .select_from(PermissionRow)
            .where(PermissionRow.app_id == app_id, PermissionRow.is_active.is_(True))
        )
        if key_prefix:
            stmt = stmt.where(PermissionRow.key.like(_like_prefix(key_prefix), escape="\\"))
        return (await self.session.execute(stmt)).scalar_one()

    async def get_implication_graph(self) -> dict[str, list[str]]:
        """``{key: [implied, …]}`` for every ACTIVE key; ``[]`` where undeclared.

        Non-string / empty edges are dropped defensively, as before.
        """
        rows = await self.session.execute(
            select(PermissionRow.key, PermissionRow.implies).where(PermissionRow.is_active.is_(True))
        )
        return {
            key: [e for e in (implies or []) if isinstance(e, str) and e]
            for key, implies in rows
        }

    # ── writes ─────────────────────────────────────────────────────────────
    async def upsert_permission(
        self,
        key: str,
        *,
        source: str,
        group: Optional[str] = None,
        label: Optional[str] = None,
        app_id: Optional[str] = None,
        implies: object = _UNSET,
    ) -> None:
        """``update_one({key}, {$set…, $setOnInsert: {created_at}}, upsert=True)``.

        Always (re)activates the key. ``implies`` is written only when the
        caller passes it (a list or None); omitted → existing edges kept.
        """
        now = datetime.now(timezone.utc)
        values = {
            "key": key,
            "group": group,
            "label": label,
            "source": source,
            "app_id": app_id,
            "is_active": True,
            "updated_at": now,
        }
        if implies is not _UNSET:
            values["implies"] = list(implies) if implies is not None else None
        stmt = pg_insert(PermissionRow).values(**values, created_at=now)
        stmt = stmt.on_conflict_do_update(
            index_elements=[PermissionRow.key],
            set_={k: stmt.excluded[k] for k in values if k != "key"},
        )
        await self.session.execute(stmt)

    async def deactivate_keys(self, keys: Iterable[str]) -> int:
        """Retire ``keys`` (is_active=false) without deleting rows. Idempotent:
        already-inactive rows are not touched. Returns newly deactivated count."""
        keys = list(set(keys))
        if not keys:
            return 0
        result = await self.session.execute(
            update(PermissionRow)
            .where(PermissionRow.key.in_(keys), PermissionRow.is_active.is_(True))
            .values(is_active=False, updated_at=datetime.now(timezone.utc))
        )
        return result.rowcount or 0

    async def deactivate_app_key(self, key: str, app_id: str) -> bool:
        """Deactivate one active key owned by ``app_id``. True iff it changed."""
        result = await self.session.execute(
            update(PermissionRow)
            .where(
                PermissionRow.key == key,
                PermissionRow.app_id == app_id,
                PermissionRow.is_active.is_(True),
            )
            .values(is_active=False, updated_at=datetime.now(timezone.utc))
        )
        return (result.rowcount or 0) > 0

    async def deactivate_stale_ingestion_keys(
        self, app_id: str, keys_to_keep: Iterable[str], *, ingestion_source: str
    ) -> int:
        """Re-ingestion sweep: deactivate ``app_id``'s active INGESTION-sourced
        keys not in ``keys_to_keep``. manual_override / reconcile_auto rows are
        never matched (source equality)."""
        keep = list(set(keys_to_keep or ()))
        stmt = (
            update(PermissionRow)
            .where(
                PermissionRow.app_id == app_id,
                PermissionRow.is_active.is_(True),
                PermissionRow.source == ingestion_source,
            )
            .values(is_active=False, updated_at=datetime.now(timezone.utc))
        )
        if keep:
            stmt = stmt.where(PermissionRow.key.not_in(keep))
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def delete_keys(self, keys: Iterable[str]) -> list[str]:
        """Hard-delete registry rows (the orphaned-key sweep). Returns keys deleted.

        Callers own the companion role strip and DAG re-validation, in the same
        transaction (params sweep ``orphaned-nova-permissions``).
        """
        keys = list(set(keys))
        if not keys:
            return []
        rows = await self.session.execute(
            delete(PermissionRow).where(PermissionRow.key.in_(keys)).returning(PermissionRow.key)
        )
        return sorted(r[0] for r in rows)
