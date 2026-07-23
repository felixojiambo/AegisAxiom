"""Tenants repository (Phase 2a) — the one data-access path for `tenants`.

Converts ``TenantRow`` ⇄ ``models.py:Tenant`` at the boundary (CONVENTIONS.md).
Callers (routes, middleware, other domains) receive the existing Pydantic
``Tenant`` shape, unchanged from the Mongo era.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select

from db.models.tenants import TenantRow
from models import Tenant
from repositories.base import BaseRepository


def _to_tenant(row: TenantRow) -> Tenant:
    """ORM row → Pydantic ``Tenant`` (the boundary conversion)."""
    return Tenant.model_validate(row, from_attributes=True)


class TenantRepository(BaseRepository):
    async def resolve_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """Return the tenant for ``tenant_id`` or None (the middleware/auth path)."""
        row = await self.session.get(TenantRow, tenant_id)
        return _to_tenant(row) if row is not None else None

    # Intention alias — reads identically, named for cross-domain callers.
    async def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        return await self.resolve_tenant(tenant_id)

    async def get_by_slug(self, slug: str) -> Optional[Tenant]:
        row = (
            await self.session.execute(
                select(TenantRow).where(TenantRow.slug == slug)
            )
        ).scalar_one_or_none()
        return _to_tenant(row) if row is not None else None

    async def list_tenants(
        self,
        *,
        tenant_type: Optional[str] = None,
        parent_tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 500,
    ) -> list[Tenant]:
        stmt = select(TenantRow)
        if tenant_type is not None:
            stmt = stmt.where(TenantRow.tenant_type == tenant_type)
        if parent_tenant_id is not None:
            stmt = stmt.where(TenantRow.parent_tenant_id == parent_tenant_id)
        if status is not None:
            stmt = stmt.where(TenantRow.status == status)
        stmt = stmt.order_by(TenantRow.created_at.desc()).limit(limit)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_to_tenant(r) for r in rows]

    async def count_children(self, parent_tenant_id: str) -> int:
        return (
            await self.session.execute(
                select(func.count())
                .select_from(TenantRow)
                .where(TenantRow.parent_tenant_id == parent_tenant_id)
            )
        ).scalar_one()

    async def create_tenant(self, tenant: Tenant) -> Tenant:
        """Insert a new tenant from a fully-formed Pydantic ``Tenant``."""
        row = TenantRow(
            tenant_id=tenant.tenant_id,
            name=tenant.name,
            slug=tenant.slug,
            tenant_type=tenant.tenant_type,
            parent_tenant_id=tenant.parent_tenant_id,
            status=tenant.status,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
            created_by=tenant.created_by,
        )
        self.session.add(row)
        await self.session.flush()
        return _to_tenant(row)

    async def update_tenant(self, tenant_id: str, updates: dict) -> Optional[Tenant]:
        """Patch mutable tenant fields; bumps ``updated_at``. Returns the row."""
        row = await self.session.get(TenantRow, tenant_id)
        if row is None:
            return None
        for key, value in updates.items():
            if hasattr(row, key):
                setattr(row, key, value)
        row.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return _to_tenant(row)

    async def upsert_root_tenant(self, tenant: Tenant) -> Tenant:
        """Idempotent seed helper (``seed_tenants``): insert if absent, else no-op.

        Returns the persisted tenant either way. Keyed on ``tenant_id``.
        """
        existing = await self.session.get(TenantRow, tenant.tenant_id)
        if existing is not None:
            return _to_tenant(existing)
        return await self.create_tenant(tenant)
