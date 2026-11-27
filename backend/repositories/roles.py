"""Roles repository (Phase 2b) — the one data-access path for ``roles``.

Converts ``RoleRow`` ⇄ ``models.py:Role`` at the boundary (CONVENTIONS §2).
``RoleRecord`` is ``Role`` plus ``app_id`` — the one column the ingestion RBAC
generator writes that the Pydantic model never carried. ``to_api_dict()`` gives
the Mongo-era response document: ``app_id`` appears only on roles that have one,
exactly as the raw Mongo documents did.

``roles.permissions`` is a JSONB string array. The Mongo ``$addToSet`` /
``$pull`` / ``$pullAll`` sweeps become single-statement ``UPDATE``s that rebuild
the array server-side (order-preserving, de-duplicated) — never
read-modify-write, so concurrent grants/revocations on the same role cannot
lose each other's keys (§A6: the row is the concurrency unit).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models.roles import RoleRow
from models import Role
from repositories.base import BaseRepository

# Columns a caller may patch through ``update_role``. ``role_id`` is identity.
_MUTABLE = frozenset(
    {"name", "display_name", "description", "color", "is_system", "is_default", "permissions"}
)


class RoleRecord(Role):
    """``Role`` + the ingestion-generator ``app_id`` (None on every other role)."""

    app_id: Optional[str] = None

    def to_api_dict(self) -> dict:
        """The Mongo-era response document for this role."""
        doc = self.model_dump()
        if doc.get("app_id") is None:
            doc.pop("app_id", None)
        return doc


def _to_record(row: RoleRow) -> RoleRecord:
    return RoleRecord.model_validate(row, from_attributes=True)


def _dedupe(keys: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


# Append the not-yet-present keys of :keys (first-occurrence order) to a role's
# array. Shared by the by-id and by-name variants below.
_ADD_SQL = """
UPDATE roles
SET permissions = roles.permissions || COALESCE((
        SELECT jsonb_agg(k ORDER BY ord)
        FROM unnest(CAST(:keys AS text[])) WITH ORDINALITY AS u(k, ord)
        WHERE NOT jsonb_exists(roles.permissions, u.k)
    ), '[]'::jsonb),
    updated_at = now()
WHERE {where}
RETURNING role_id
"""

# Remove every occurrence of :keys from the array, preserving the order of the
# rest. Only touches rows that actually hold one of the keys (GIN-served ?|).
_REMOVE_SQL = """
UPDATE roles
SET permissions = COALESCE((
        SELECT jsonb_agg(e ORDER BY ord)
        FROM jsonb_array_elements_text(roles.permissions) WITH ORDINALITY AS t(e, ord)
        WHERE NOT (t.e = ANY(CAST(:keys AS text[])))
    ), '[]'::jsonb),
    updated_at = now()
WHERE permissions ?| CAST(:keys AS text[]){extra}
RETURNING name
"""


class RolesRepository(BaseRepository):
    # ── reads ──────────────────────────────────────────────────────────────
    async def get_role_permissions(self, role_name: str) -> list[str]:
        """The stored permission keys of role ``role_name``; ``[]`` if no such role.

        The auth hot path (``get_current_user``, login/me) — one indexed
        column read, no DTO construction.
        """
        perms = (
            await self.session.execute(
                select(RoleRow.permissions).where(RoleRow.name == role_name)
            )
        ).scalar_one_or_none()
        return list(perms) if perms else []

    async def get_role(self, role_id: str) -> Optional[RoleRecord]:
        row = await self.session.get(RoleRow, role_id)
        return _to_record(row) if row is not None else None

    async def get_role_by_name(self, name: str, *, for_update: bool = False) -> Optional[RoleRecord]:
        """``for_update=True`` takes a row lock (``SELECT … FOR UPDATE``) for a
        read-modify-write inside the caller's transaction."""
        stmt = select(RoleRow).where(RoleRow.name == name)
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return _to_record(row) if row is not None else None

    async def role_name_exists(self, name: str) -> bool:
        return (
            await self.session.execute(select(RoleRow.role_id).where(RoleRow.name == name))
        ).first() is not None

    async def list_roles(self, *, order: str = "default", limit: int = 1000) -> list[RoleRecord]:
        """All roles.

        ``order="default"``: default roles first, then creation order (the
        Mongo ``sort("is_default", -1)`` + natural order). ``order="name"``:
        alphabetical (the policy-builder role picker).
        """
        stmt = select(RoleRow)
        if order == "name":
            stmt = stmt.order_by(RoleRow.name)
        else:
            stmt = stmt.order_by(RoleRow.is_default.desc(), RoleRow.created_at, RoleRow.role_id)
        rows = (await self.session.execute(stmt.limit(limit))).scalars().all()
        return [_to_record(r) for r in rows]

    async def list_role_names(self) -> set[str]:
        return set((await self.session.execute(select(RoleRow.name))).scalars().all())

    async def list_role_names_with_any_permission(self, keys: Iterable[str]) -> list[str]:
        """Names of roles whose permissions contain at least one of ``keys``, sorted."""
        keys = _dedupe(keys)
        if not keys:
            return []
        rows = await self.session.execute(
            text(
                "SELECT name FROM roles WHERE permissions ?| CAST(:keys AS text[]) ORDER BY name"
            ),
            {"keys": keys},
        )
        return [r[0] for r in rows]

    # ── writes ─────────────────────────────────────────────────────────────
    async def create_role(self, role: Role, *, app_id: Optional[str] = None) -> RoleRecord:
        """Insert a fully-formed role. A duplicate ``name`` raises IntegrityError."""
        row = RoleRow(
            role_id=role.role_id,
            name=role.name,
            display_name=role.display_name,
            description=role.description,
            color=role.color,
            is_system=role.is_system,
            is_default=role.is_default,
            permissions=list(role.permissions),
            app_id=app_id if app_id is not None else getattr(role, "app_id", None),
            created_at=role.created_at,
            updated_at=role.updated_at,
        )
        self.session.add(row)
        await self.session.flush()
        return _to_record(row)

    async def insert_role_if_absent(self, role: Role, *, app_id: Optional[str] = None) -> bool:
        """Idempotent seed insert keyed on ``name``. True iff this call inserted.

        ``INSERT … ON CONFLICT (name) DO NOTHING`` — race-free, unlike the
        Mongo find-then-insert it replaces.
        """
        stmt = (
            pg_insert(RoleRow)
            .values(
                role_id=role.role_id,
                name=role.name,
                display_name=role.display_name,
                description=role.description,
                color=role.color,
                is_system=role.is_system,
                is_default=role.is_default,
                permissions=list(role.permissions),
                app_id=app_id if app_id is not None else getattr(role, "app_id", None),
                created_at=role.created_at,
                updated_at=role.updated_at,
            )
            .on_conflict_do_nothing(index_elements=[RoleRow.name])
            .returning(RoleRow.role_id)
        )
        return (await self.session.execute(stmt)).first() is not None

    async def update_role(self, role_id: str, updates: dict[str, Any]) -> Optional[RoleRecord]:
        """Patch mutable fields (unknown keys ignored); bumps ``updated_at``."""
        values = {k: v for k, v in updates.items() if k in _MUTABLE}
        if "permissions" in values:
            values["permissions"] = list(values["permissions"] or [])
        values["updated_at"] = updates.get("updated_at") or datetime.now(timezone.utc)
        row = (
            await self.session.execute(
                update(RoleRow)
                .where(RoleRow.role_id == role_id)
                .values(**values)
                .returning(RoleRow)
            )
        ).scalar_one_or_none()
        return _to_record(row) if row is not None else None

    async def set_role_permissions_by_name(self, name: str, permissions: list[str]) -> bool:
        """Replace a role's whole permission list. True iff the role exists."""
        result = await self.session.execute(
            update(RoleRow)
            .where(RoleRow.name == name)
            .values(permissions=list(permissions), updated_at=datetime.now(timezone.utc))
            .returning(RoleRow.role_id)
        )
        return result.first() is not None

    async def add_permissions(
        self, keys: Iterable[str], *, role_id: Optional[str] = None, name: Optional[str] = None
    ) -> bool:
        """``$addToSet`` with ``$each``: append missing keys atomically.

        Address the role by exactly one of ``role_id`` / ``name``. Returns True
        iff the role exists (whether or not any key was new).
        """
        where, params = self._target(role_id, name)
        keys = _dedupe(keys)
        if not keys:
            return await self._exists(role_id, name)
        rows = await self.session.execute(
            text(_ADD_SQL.format(where=where)), {**params, "keys": keys}
        )
        return rows.first() is not None

    async def remove_permissions(
        self, keys: Iterable[str], *, role_id: Optional[str] = None, name: Optional[str] = None
    ) -> bool:
        """``$pullAll`` on one role. True iff the role held at least one key."""
        where, params = self._target(role_id, name)
        keys = _dedupe(keys)
        if not keys:
            return False
        rows = await self.session.execute(
            text(_REMOVE_SQL.format(extra=f" AND {where}")), {**params, "keys": keys}
        )
        return rows.first() is not None

    async def strip_permissions_from_all_roles(self, keys: Iterable[str]) -> list[str]:
        """``update_many({permissions: {$in}}, {$pull})``: remove ``keys`` from
        every role in one statement. Returns the names of the roles modified."""
        keys = _dedupe(keys)
        if not keys:
            return []
        rows = await self.session.execute(text(_REMOVE_SQL.format(extra="")), {"keys": keys})
        return sorted(r[0] for r in rows)

    async def delete_role(self, role_id: str) -> bool:
        result = await self.session.execute(
            delete(RoleRow).where(RoleRow.role_id == role_id).returning(RoleRow.role_id)
        )
        return result.first() is not None

    # ── helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _target(role_id: Optional[str], name: Optional[str]) -> tuple[str, dict]:
        if (role_id is None) == (name is None):
            raise ValueError("address the role by exactly one of role_id / name")
        if role_id is not None:
            return "roles.role_id = :role_id", {"role_id": role_id}
        return "roles.name = :name", {"name": name}

    async def _exists(self, role_id: Optional[str], name: Optional[str]) -> bool:
        col, val = (RoleRow.role_id, role_id) if role_id is not None else (RoleRow.name, name)
        return (await self.session.execute(select(RoleRow.role_id).where(col == val))).first() is not None
