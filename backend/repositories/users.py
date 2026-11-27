"""Users repository (Phase 2a) — the one data-access path for `users`.

Converts ``UserRow`` ⇄ ``models.py:User`` at the boundary. Two conventions this
module establishes for the rest of the migration:

  * **Secret material never rides on the returned DTO.** ``password_hash`` is a
    column on ``UserRow`` but not on the ``User`` Pydantic model. Credential
    checks call ``get_password_hash_by_email`` (or ``get_auth_record_by_email``)
    which returns the hash as a *separate* value — the ``User`` DTO handed to
    every other caller never carries it.
  * ``permissions``/``effective_permissions`` are **not stored** — they are
    request-time derivations. ``_to_user`` leaves them at their model defaults;
    the auth path fills them in per request.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy import update as sa_update

from db.models.users import UserRow, UserResourceTypeAssignmentRow
from models import User
from repositories.base import BaseRepository

# Columns that exist on UserRow but must never be copied onto the User DTO.
_SECRET_COLUMNS = {"password_hash", "invite_token"}


def _to_user(row: UserRow) -> User:
    """ORM row → Pydantic ``User`` (secrets and request-time fields excluded).

    ``User.model_validate(..., from_attributes=True)`` reads only the attributes
    the model declares, so ``password_hash``/``invite_token``/``budget_reset_at``/
    ``is_test_data`` (row-only columns) are naturally dropped, and
    ``permissions``/``effective_permissions`` stay at their empty defaults.
    """
    return User.model_validate(row, from_attributes=True)


class UserRepository(BaseRepository):
    # ── single-row reads ────────────────────────────────────────────────────
    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        row = await self.session.get(UserRow, user_id)
        return _to_user(row) if row is not None else None

    async def get_user_by_email(self, email: str) -> Optional[User]:
        row = await self._row_by_email(email)
        return _to_user(row) if row is not None else None

    async def _row_by_email(self, email: str) -> Optional[UserRow]:
        return (
            await self.session.execute(
                select(UserRow).where(UserRow.email == email.lower())
            )
        ).scalar_one_or_none()

    # ── credential path (secret returned separately, never on the DTO) ──────
    async def get_password_hash_by_email(self, email: str) -> Optional[str]:
        return (
            await self.session.execute(
                select(UserRow.password_hash).where(UserRow.email == email.lower())
            )
        ).scalar_one_or_none()

    async def get_auth_record_by_email(
        self, email: str
    ) -> Optional[tuple[User, Optional[str]]]:
        """Return ``(User, password_hash)`` for login, or None if no such user."""
        row = await self._row_by_email(email)
        if row is None:
            return None
        return _to_user(row), row.password_hash

    # ── budget projection (services/ai/llm.py:373 caller) ───────────────────
    async def get_user_budget_fields(
        self, user_id: str
    ) -> Optional[tuple[str, Optional[datetime]]]:
        """Return ``(role, budget_reset_at)`` — the fields llm.py reads."""
        result = (
            await self.session.execute(
                select(UserRow.role, UserRow.budget_reset_at).where(
                    UserRow.user_id == user_id
                )
            )
        ).one_or_none()
        return (result[0], result[1]) if result is not None else None

    # ── multi-read ──────────────────────────────────────────────────────────
    async def get_users_by_ids(self, user_ids: list[str]) -> list[User]:
        if not user_ids:
            return []
        rows = (
            await self.session.execute(
                select(UserRow).where(UserRow.user_id.in_(list(user_ids)))
            )
        ).scalars().all()
        return [_to_user(r) for r in rows]

    async def list_users(
        self,
        *,
        role: Optional[str] = None,
        role_in: Optional[list[str]] = None,
        tenant_id: Optional[str] = None,
        account_id: Optional[str] = None,
        contractor_business_id: Optional[str] = None,
        contractor_business_id_in: Optional[list[str]] = None,
        contractor_business_id_not_null: bool = False,
        is_active: Optional[bool] = None,
        is_test_data: Optional[bool] = None,
        user_ids: Optional[list[str]] = None,
        sort_by: str = "name",
        descending: bool = False,
        limit: int = 1000,
    ) -> list[User]:
        """Flexible user listing covering the cross-domain caller filter patterns.

        Pass only the filters a call site uses; unset filters are ignored. Sort
        defaults to name ascending (the dominant pattern); pass ``sort_by``/
        ``descending`` for the few sites that order by ``created_at``.
        """
        stmt = select(UserRow)
        if role is not None:
            stmt = stmt.where(UserRow.role == role)
        if role_in is not None:
            stmt = stmt.where(UserRow.role.in_(list(role_in)))
        if tenant_id is not None:
            stmt = stmt.where(UserRow.tenant_id == tenant_id)
        if account_id is not None:
            stmt = stmt.where(UserRow.account_id == account_id)
        if contractor_business_id is not None:
            stmt = stmt.where(UserRow.contractor_business_id == contractor_business_id)
        if contractor_business_id_in is not None:
            stmt = stmt.where(
                UserRow.contractor_business_id.in_(list(contractor_business_id_in))
            )
        if contractor_business_id_not_null:
            stmt = stmt.where(UserRow.contractor_business_id.isnot(None))
        if is_active is not None:
            stmt = stmt.where(UserRow.is_active == is_active)
        if is_test_data is not None:
            stmt = stmt.where(UserRow.is_test_data == is_test_data)
        if user_ids is not None:
            stmt = stmt.where(UserRow.user_id.in_(list(user_ids)))
        sort_col = getattr(UserRow, sort_by, UserRow.name)
        stmt = stmt.order_by(sort_col.desc() if descending else sort_col.asc()).limit(limit)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_to_user(r) for r in rows]

    async def list_resource_pool(
        self, *, resource_type_id: Optional[str] = None
    ) -> list[dict]:
        """Resource picker (solutions_design.py:677 — $unwind → JOIN).

        Active contractors joined to their **active** resource-type assignments,
        one row per (user, assignment). Returns plain dicts matching the old
        aggregation's projected shape (the caller enriches with business name).
        """
        stmt = (
            select(
                UserRow.user_id,
                UserRow.name,
                UserRow.email,
                UserRow.contractor_business_id,
                UserResourceTypeAssignmentRow.resource_type_id,
                UserResourceTypeAssignmentRow.resource_type_name,
                UserResourceTypeAssignmentRow.cost_rate,
            )
            .join(
                UserResourceTypeAssignmentRow,
                UserResourceTypeAssignmentRow.user_id == UserRow.user_id,
            )
            .where(
                UserRow.role == "contractor",
                UserRow.is_active.is_(True),
                UserResourceTypeAssignmentRow.is_active.is_(True),
            )
        )
        if resource_type_id:
            stmt = stmt.where(
                UserResourceTypeAssignmentRow.resource_type_id == resource_type_id
            )
        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "user_id": r.user_id,
                "name": r.name,
                "email": r.email,
                "contractor_business_id": r.contractor_business_id,
                "resource_type_id": r.resource_type_id,
                "resource_type_name": r.resource_type_name,
                "cost_rate": float(r.cost_rate) if r.cost_rate is not None else None,
            }
            for r in rows
        ]

    async def search_users_by_name(self, term: str, *, limit: int = 50) -> list[User]:
        """Case-insensitive substring search on name (ILIKE → trigram GIN, §A4)."""
        pattern = f"%{term}%"
        rows = (
            await self.session.execute(
                select(UserRow)
                .where(UserRow.name.ilike(pattern))
                .order_by(UserRow.name.asc())
                .limit(limit)
            )
        ).scalars().all()
        return [_to_user(r) for r in rows]

    async def count_users(
        self, *, role: Optional[str] = None, tenant_id: Optional[str] = None
    ) -> int:
        stmt = select(func.count()).select_from(UserRow)
        if role is not None:
            stmt = stmt.where(UserRow.role == role)
        if tenant_id is not None:
            stmt = stmt.where(UserRow.tenant_id == tenant_id)
        return (await self.session.execute(stmt)).scalar_one()

    async def admin_exists(self) -> bool:
        """True iff any admin user exists (admin auto-init / setup-admin gate)."""
        return (
            await self.session.execute(
                select(UserRow.user_id).where(UserRow.role == "admin").limit(1)
            )
        ).first() is not None

    # ── aggregation port (contractor_businesses.py:54 — $group → GROUP BY) ──
    async def count_contractors_by_business(
        self, business_ids: list[str]
    ) -> dict[str, int]:
        """``{contractor_business_id: active_contractor_count}`` for the given ids.

        Mongo `$match` + `$group` → SQL `GROUP BY`. Trigram/B-tree indexes make
        the ``role``/``contractor_business_id`` filter cheap (see gate EXPLAIN).
        """
        if not business_ids:
            return {}
        rows = (
            await self.session.execute(
                select(
                    UserRow.contractor_business_id, func.count().label("count")
                )
                .where(
                    UserRow.contractor_business_id.in_(list(business_ids)),
                    UserRow.role == "contractor",
                    UserRow.is_active.is_(True),
                )
                .group_by(UserRow.contractor_business_id)
            )
        ).all()
        return {cbid: count for cbid, count in rows}

    # ── projects/AI/finance cross-domain callers (2a completion) ────────────
    #
    # Phase 2a's census inventoried only the ATTRIBUTE form ``db.users`` and so
    # missed nine files that reach the collection through the SUBSCRIPT form
    # ``db["users"]`` — 28 live sites, including a write to ``pm_capacity_tier``.
    # The methods below are those callers' intents; the seam is closed by the
    # zero-grep that verify builds from ``db/migrated_collections.json``, which
    # covers the dot, subscript and get_collection forms.

    @staticmethod
    def _user_card(row: UserRow) -> dict:
        """``{user_id, name, email}`` — the AI mention-resolver projection."""
        return {"user_id": row.user_id, "name": row.name, "email": row.email}

    @staticmethod
    def _capacity_card(row: UserRow) -> dict:
        """Capacity-admin projection (projects_capacity ``_PROJECTION``).

        Every key is always present. The Mongo projection omitted keys the
        document lacked and the route patched four of them back with
        ``setdefault``; columns cannot be absent, so the shape is now
        deterministic (``picture`` is explicitly ``None`` rather than missing).
        """
        return {
            "user_id": row.user_id,
            "name": row.name,
            "email": row.email,
            "role": row.role,
            "pm_capacity_tier": row.pm_capacity_tier,
            "is_assignable_as_pm": row.is_assignable_as_pm,
            "is_assignable_as_member": row.is_assignable_as_member,
            "is_active": row.is_active,
            "picture": row.picture,
        }

    @staticmethod
    def _assignable_card(row: UserRow) -> dict:
        """Assignable-users picker projection — as ``_capacity_card`` less email."""
        card = UserRepository._capacity_card(row)
        card.pop("email")
        return card

    @staticmethod
    def _ilike_pattern(term: str, *, prefix_only: bool) -> str:
        """Escape LIKE wildcards, then wrap.

        The term arrives from an LLM and can be prompt-injected, so ``%``/``_``/
        ``\\`` are escaped rather than interpreted — the ILIKE equivalent of the
        ``re.escape`` the Mongo ``$regex`` path used to defuse ReDoS.
        """
        safe = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"{safe}%" if prefix_only else f"%{safe}%"

    async def user_exists_in_tenant(self, user_id: str, tenant_id: str) -> bool:
        """Tenant-scoped FK check for pm_id / assignee_id / pm_user_id.

        Cross-tenant users must read as "does not match any user" so a caller
        cannot probe for the existence of another tenant's user_ids.
        """
        return (
            await self.session.execute(
                select(UserRow.user_id).where(
                    UserRow.user_id == user_id, UserRow.tenant_id == tenant_id
                )
            )
        ).first() is not None

    async def count_users_in_tenant(self, user_ids: list[str], tenant_id: str) -> int:
        """Count how many of ``user_ids`` exist in ``tenant_id`` (bulk FK check)."""
        ids = list(set(user_ids))
        if not ids:
            return 0
        return (
            await self.session.execute(
                select(func.count()).select_from(UserRow).where(
                    UserRow.user_id.in_(ids), UserRow.tenant_id == tenant_id
                )
            )
        ).scalar_one()

    async def get_user_id_by_email_in_tenant(
        self, email: str, tenant_id: str
    ) -> Optional[str]:
        """Resolve an email to a user_id within one tenant (CSV import)."""
        return (
            await self.session.execute(
                select(UserRow.user_id).where(
                    UserRow.email == email.strip().lower(),
                    UserRow.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()

    async def get_emails_by_ids(
        self, user_ids: list[str], *, tenant_id: Optional[str] = None
    ) -> dict[str, str]:
        """``{user_id: email}`` for the given ids (CSV export lookup).

        Users with no row are simply absent from the mapping, matching the old
        cursor behaviour. Missing emails collapse to ``""``, as before.
        """
        ids = list(set(user_ids))
        if not ids:
            return {}
        stmt = select(UserRow.user_id, UserRow.email).where(UserRow.user_id.in_(ids))
        if tenant_id is not None:
            stmt = stmt.where(UserRow.tenant_id == tenant_id)
        rows = (await self.session.execute(stmt)).all()
        return {uid: (email or "") for uid, email in rows}

    async def list_assignable_users(self, *, tenant_id: str, tier: str) -> list[dict]:
        """PM/member picker, filtered by capacity tier and scoped to a tenant.

        Tier semantics are carried over verbatim from the Mongo filter:
        ``main`` → main_pm; ``overflow``/``all_pms`` → main_pm or overflow_pm;
        ``members`` → tier is not "none"; ``all`` → no tier filter. ``is_active``
        is enforced for every tier.

        Note ``members``: Mongo's ``{"$ne": "none"}`` also matched documents
        where the field was absent, so the faithful SQL is ``IS DISTINCT FROM``
        (which keeps NULL rows) — not ``!=`` (which would silently drop them).
        """
        stmt = select(UserRow).where(
            UserRow.tenant_id == tenant_id, UserRow.is_active.is_(True)
        )
        if tier == "main":
            stmt = stmt.where(UserRow.pm_capacity_tier == "main_pm")
        elif tier in {"overflow", "all_pms"}:
            stmt = stmt.where(UserRow.pm_capacity_tier.in_(["main_pm", "overflow_pm"]))
        elif tier == "members":
            stmt = stmt.where(UserRow.pm_capacity_tier.is_distinct_from("none"))
        rows = (
            await self.session.execute(stmt.order_by(UserRow.name.asc()))
        ).scalars().all()
        return [self._assignable_card(r) for r in rows]

    async def list_capacity_matrix(self, tenant_id: str) -> list[dict]:
        """Every user's capacity tier for one tenant, name-ascending."""
        rows = (
            await self.session.execute(
                select(UserRow)
                .where(UserRow.tenant_id == tenant_id)
                .order_by(UserRow.name.asc())
            )
        ).scalars().all()
        return [self._capacity_card(r) for r in rows]

    async def get_capacity_row(self, user_id: str, tenant_id: str) -> Optional[dict]:
        """One user's capacity card, tenant-scoped (the before/after snapshots)."""
        row = (
            await self.session.execute(
                select(UserRow).where(
                    UserRow.user_id == user_id, UserRow.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()
        return self._capacity_card(row) if row is not None else None

    async def set_capacity_tier(
        self,
        user_id: str,
        tenant_id: str,
        *,
        tier: str,
        is_assignable_as_pm: bool,
        is_assignable_as_member: bool,
    ) -> bool:
        """Flip a user's capacity tier and its two derived flags together.

        One tenant-scoped statement (§A6): a tenant-admin cannot reach across
        tenants, and the tier never lands without its derived flags. Returns
        whether a row was actually updated.
        """
        result = await self.session.execute(
            sa_update(UserRow)
            .where(UserRow.user_id == user_id, UserRow.tenant_id == tenant_id)
            .values(
                pm_capacity_tier=tier,
                is_assignable_as_pm=is_assignable_as_pm,
                is_assignable_as_member=is_assignable_as_member,
            )
            .returning(UserRow.user_id)
        )
        return result.first() is not None

    async def search_user_cards(
        self,
        term: str,
        *,
        prefix_only: bool = True,
        user_ids: Optional[list[str]] = None,
        limit: int = 10,
    ) -> list[dict]:
        """Name-or-email search returning ``{user_id, name, email}`` cards.

        ``$regex`` → ILIKE per §A4. The caller keeps the two-step
        prefix-then-contains behaviour (a prefix hit is a better answer than a
        substring hit), so this takes ``prefix_only`` rather than deciding for
        it. ``user_ids`` narrows the search to a project's membership.
        """
        pattern = self._ilike_pattern(term, prefix_only=prefix_only)
        stmt = select(UserRow).where(
            UserRow.name.ilike(pattern, escape="\\")
            | UserRow.email.ilike(pattern, escape="\\")
        )
        if user_ids is not None:
            if not user_ids:
                return []
            stmt = stmt.where(UserRow.user_id.in_(list(user_ids)))
        rows = (
            await self.session.execute(stmt.order_by(UserRow.name.asc()).limit(limit))
        ).scalars().all()
        return [self._user_card(r) for r in rows]

    async def list_user_cards_by_ids(
        self, user_ids: list[str], *, limit: int = 20
    ) -> list[dict]:
        """``{user_id, name, email}`` cards for a fixed id set (member lists)."""
        ids = list(user_ids)
        if not ids:
            return []
        rows = (
            await self.session.execute(
                select(UserRow)
                .where(UserRow.user_id.in_(ids))
                .order_by(UserRow.name.asc())
                .limit(limit)
            )
        ).scalars().all()
        return [self._user_card(r) for r in rows]

    async def list_capacity_backfill_rows(self, *, limit: int = 100_000) -> list[dict]:
        """``{user_id, role, is_active, pm_capacity_tier}`` for every user.

        Feeds the one-shot ``seeds/seed_assignable_flags`` backfill, which needs
        the tier-deriving inputs and nothing else.
        """
        rows = (
            await self.session.execute(
                select(
                    UserRow.user_id,
                    UserRow.role,
                    UserRow.is_active,
                    UserRow.pm_capacity_tier,
                ).order_by(UserRow.user_id.asc()).limit(limit)
            )
        ).all()
        return [
            {
                "user_id": r.user_id,
                "role": r.role,
                "is_active": r.is_active,
                "pm_capacity_tier": r.pm_capacity_tier,
            }
            for r in rows
        ]

    # ── writes ──────────────────────────────────────────────────────────────
    async def create_user(
        self, user_fields: dict, *, assignments: Optional[list[dict]] = None
    ) -> User:
        """Insert a user (+ optional resource-type-assignment child rows).

        Multi-write cluster (§A6.2): the user row and its child rows are added in
        one ``flush`` and committed together by the request transaction — the
        invite-accept / user-creation cluster is atomic. ``user_fields`` may carry
        ``password_hash`` (stored on the row, never returned on the DTO).
        """
        row = UserRow(**{k: v for k, v in user_fields.items() if hasattr(UserRow, k)})
        self.session.add(row)
        for a in assignments or []:
            self.session.add(
                UserResourceTypeAssignmentRow(
                    user_id=row.user_id,
                    **{k: v for k, v in a.items()
                       if hasattr(UserResourceTypeAssignmentRow, k)},
                )
            )
        await self.session.flush()
        return _to_user(row)

    async def update_user(self, user_id: str, updates: dict) -> Optional[User]:
        """Patch mutable user fields (including ``password_hash`` on reset)."""
        row = await self.session.get(UserRow, user_id)
        if row is None:
            return None
        for key, value in updates.items():
            if hasattr(UserRow, key):
                setattr(row, key, value)
        await self.session.flush()
        return _to_user(row)

    async def set_password_by_email(self, email: str, password_hash: str) -> bool:
        """Set a new password hash by email (reset flow). Returns hit/miss."""
        row = await self._row_by_email(email)
        if row is None:
            return False
        row.password_hash = password_hash
        await self.session.flush()
        return True

    async def delete_user(self, user_id: str) -> bool:
        row = await self.session.get(UserRow, user_id)
        if row is None:
            return False
        await self.session.delete(row)  # child rows cascade (FK ON DELETE CASCADE)
        await self.session.flush()
        return True

    async def list_tenant_users_admin(self, tenant_id: str) -> list[dict]:
        """Admin user list for a tenant (ai_employee_catalog list_tenant_users).

        Returns the exact projected shape the old endpoint returned, including a
        ``has_password`` flag derived from the (never-exposed) password hash.
        ``last_login_at`` is not a canonical column → returned as None (it was
        an optional Mongo field, absent for fresh Postgres rows).
        """
        rows = (
            await self.session.execute(
                select(UserRow)
                .where(UserRow.tenant_id == tenant_id)
                .order_by(UserRow.created_at.desc())
                .limit(1000)
            )
        ).scalars().all()
        return [
            {
                "user_id": r.user_id,
                "email": r.email,
                "name": r.name,
                "role": r.role,
                "is_active": r.is_active,
                "is_email_verified": r.is_email_verified,
                "created_at": r.created_at,
                "last_login_at": None,
                "has_password": bool(r.password_hash),
            }
            for r in rows
        ]

    # ── resource-type-assignment child (sales_ops_procurement callers) ──────
    @staticmethod
    def _assignment_to_dict(row: UserResourceTypeAssignmentRow) -> dict:
        return {
            "resource_type_id": row.resource_type_id,
            "resource_type_name": row.resource_type_name,
            "cost_rate": float(row.cost_rate) if row.cost_rate is not None else None,
            "contractor_business_rate": (
                float(row.contractor_business_rate)
                if row.contractor_business_rate is not None else None
            ),
            "approval_status": row.approval_status,
            "approved_by": row.approved_by,
            "approved_by_name": row.approved_by_name,
            "approved_date": row.approved_date,
            "rejection_reason": row.rejection_reason,
            "is_active": row.is_active,
            "assigned_at": row.assigned_at,
            "assigned_by": row.assigned_by,
        }

    async def list_contractor_users_with_assignments(
        self, business_id: str
    ) -> list[dict]:
        """Contractors for a business + their resource-type assignments (nested).

        Shape: ``[{user_id, name, email, resource_type_assignments: [...]}]`` —
        matches the old ``db.users.find({contractor_business_id, role})`` projection.
        """
        rows = (
            await self.session.execute(
                select(UserRow)
                .where(
                    UserRow.contractor_business_id == business_id,
                    UserRow.role == "contractor",
                )
                .limit(100)
            )
        ).scalars().all()
        return [
            {
                "user_id": u.user_id,
                "name": u.name,
                "email": u.email,
                "resource_type_assignments": [
                    self._assignment_to_dict(a) for a in u.resource_type_assignments
                ],
            }
            for u in rows
        ]

    async def list_contractors_with_pending_assignments(self) -> list[dict]:
        """Contractors having ≥1 pending resource-type assignment (all their
        assignments nested). Replaces the ``$elemMatch`` pending filter.
        """
        rows = (
            await self.session.execute(
                select(UserRow)
                .join(
                    UserResourceTypeAssignmentRow,
                    UserResourceTypeAssignmentRow.user_id == UserRow.user_id,
                )
                .where(
                    UserRow.role == "contractor",
                    UserResourceTypeAssignmentRow.approval_status == "pending",
                )
                .distinct()
                .limit(500)
            )
        ).scalars().all()
        return [
            {
                "user_id": u.user_id,
                "name": u.name,
                "email": u.email,
                "contractor_business_id": u.contractor_business_id,
                "resource_type_assignments": [
                    self._assignment_to_dict(a) for a in u.resource_type_assignments
                ],
            }
            for u in rows
        ]

    async def list_user_resource_assignments(self, user_id: str) -> list[dict]:
        """All resource-type assignments for one user (any role)."""
        rows = (
            await self.session.execute(
                select(UserResourceTypeAssignmentRow).where(
                    UserResourceTypeAssignmentRow.user_id == user_id
                )
            )
        ).scalars().all()
        return [self._assignment_to_dict(r) for r in rows]

    async def upsert_resource_assignment(
        self, user_id: str, resource_type_id: str, fields: dict
    ) -> dict:
        """Insert or replace a user's assignment for one resource type."""
        row = (
            await self.session.execute(
                select(UserResourceTypeAssignmentRow).where(
                    UserResourceTypeAssignmentRow.user_id == user_id,
                    UserResourceTypeAssignmentRow.resource_type_id == resource_type_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = UserResourceTypeAssignmentRow(
                user_id=user_id, resource_type_id=resource_type_id
            )
            self.session.add(row)
        for key, value in fields.items():
            if hasattr(row, key) and key not in ("user_id", "resource_type_id"):
                setattr(row, key, value)
        await self.session.flush()
        return self._assignment_to_dict(row)

    async def delete_resource_assignment(
        self, user_id: str, resource_type_id: str
    ) -> bool:
        row = (
            await self.session.execute(
                select(UserResourceTypeAssignmentRow).where(
                    UserResourceTypeAssignmentRow.user_id == user_id,
                    UserResourceTypeAssignmentRow.resource_type_id == resource_type_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

    async def list_users_by_resource_type(self, resource_type_id: str) -> list[dict]:
        """Active users holding an active assignment for a resource type.

        Returns ``{user_id, name, email, role, cost_rate, assigned_at}`` — the
        old ``get_users_with_resource_type`` projected shape.
        """
        rows = (
            await self.session.execute(
                select(UserRow, UserResourceTypeAssignmentRow)
                .join(
                    UserResourceTypeAssignmentRow,
                    UserResourceTypeAssignmentRow.user_id == UserRow.user_id,
                )
                .where(
                    UserResourceTypeAssignmentRow.resource_type_id == resource_type_id,
                    UserResourceTypeAssignmentRow.is_active.is_(True),
                    UserRow.is_active.is_(True),
                )
                .limit(500)
            )
        ).all()
        return [
            {
                "user_id": u.user_id,
                "name": u.name,
                "email": u.email,
                "role": u.role,
                "cost_rate": float(a.cost_rate) if a.cost_rate is not None else None,
                "assigned_at": a.assigned_at,
            }
            for (u, a) in rows
        ]

    async def get_resource_assignment(
        self, user_id: str, resource_type_id: str
    ) -> Optional[dict]:
        row = (
            await self.session.execute(
                select(UserResourceTypeAssignmentRow).where(
                    UserResourceTypeAssignmentRow.user_id == user_id,
                    UserResourceTypeAssignmentRow.resource_type_id == resource_type_id,
                )
            )
        ).scalar_one_or_none()
        return self._assignment_to_dict(row) if row is not None else None

    async def update_resource_assignment(
        self, user_id: str, resource_type_id: str, updates: dict
    ) -> Optional[dict]:
        """Patch a single resource-type assignment (approve/reject rate flow)."""
        row = (
            await self.session.execute(
                select(UserResourceTypeAssignmentRow).where(
                    UserResourceTypeAssignmentRow.user_id == user_id,
                    UserResourceTypeAssignmentRow.resource_type_id == resource_type_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        for key, value in updates.items():
            if hasattr(row, key):
                setattr(row, key, value)
        await self.session.flush()
        return self._assignment_to_dict(row)

    async def update_tenant_id_where_missing(self, tenant_id: str) -> int:
        """Seed backfill (``seed_tenants.py:87``): set tenant_id on rows missing it.

        Returns the number of rows updated. Uses a single bulk UPDATE.
        """
        from sqlalchemy import update

        result = await self.session.execute(
            update(UserRow)
            .where(UserRow.tenant_id.is_(None))
            .values(tenant_id=tenant_id)
        )
        await self.session.flush()
        return result.rowcount or 0
