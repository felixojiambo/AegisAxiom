"""Phase 2a — tenants repository + tenant-isolation tests (real Postgres, §A11).

Covers the tenant tree CRUD and the isolation guarantee the repository provides:
a tenant-scoped read returns ONLY that tenant's rows, never another tenant's.
Uses the ``pg_session`` fixture (rollback isolation).
"""
from datetime import datetime, timezone

import pytest

from models import Tenant
from repositories.tenants import TenantRepository
from repositories.users import UserRepository


def _t(tid, ttype, slug, parent=None):
    return Tenant(
        tenant_id=tid, name=tid, slug=slug, tenant_type=ttype,
        parent_tenant_id=parent, status="active",
    )


@pytest.mark.asyncio
async def test_tenant_tree_crud(pg_session):
    repo = TenantRepository(pg_session)
    root = await repo.create_tenant(_t("ten_root", "root", "root"))
    partner = await repo.create_tenant(_t("ten_partner", "partner", "partner", "ten_root"))
    acct = await repo.create_tenant(_t("ten_acct", "account", "acct", "ten_partner"))

    assert (await repo.resolve_tenant("ten_root")).tenant_type == "root"
    assert (await repo.get_by_slug("partner")).tenant_id == "ten_partner"
    assert (await repo.get_tenant("ten_acct")).parent_tenant_id == "ten_partner"

    # children listing + count
    assert await repo.count_children("ten_partner") == 1
    kids = await repo.list_tenants(parent_tenant_id="ten_root")
    assert [k.tenant_id for k in kids] == ["ten_partner"]

    # update + archive
    updated = await repo.update_tenant("ten_acct", {"name": "Renamed", "status": "archived"})
    assert updated.name == "Renamed" and updated.status == "archived"
    active_accts = await repo.list_tenants(tenant_type="account", status="active")
    assert all(t.tenant_id != "ten_acct" for t in active_accts)


@pytest.mark.asyncio
async def test_missing_tenant_returns_none(pg_session):
    repo = TenantRepository(pg_session)
    assert await repo.resolve_tenant("ten_nope") is None
    assert await repo.get_by_slug("nope") is None


@pytest.mark.asyncio
async def test_upsert_root_tenant_idempotent(pg_session):
    repo = TenantRepository(pg_session)
    a = await repo.upsert_root_tenant(_t("ten_simplimeta_root", "root", "simplimeta"))
    b = await repo.upsert_root_tenant(_t("ten_simplimeta_root", "root", "simplimeta"))
    assert a.tenant_id == b.tenant_id == "ten_simplimeta_root"
    assert len(await repo.list_tenants(tenant_type="root")) == 1


@pytest.mark.asyncio
async def test_tenant_isolation_scoped_reads(pg_session):
    """Two tenants; a tenant-scoped user read must never leak the other's rows."""
    tenants = TenantRepository(pg_session)
    users = UserRepository(pg_session)
    await tenants.create_tenant(_t("ten_A", "account", "tenant-a"))
    await tenants.create_tenant(_t("ten_B", "account", "tenant-b"))

    for i in range(3):
        await users.create_user({
            "user_id": f"user_A{i}", "email": f"a{i}@x.io", "name": f"A{i}",
            "role": "sales_rep", "tenant_id": "ten_A", "is_active": True,
        })
    for i in range(2):
        await users.create_user({
            "user_id": f"user_B{i}", "email": f"b{i}@x.io", "name": f"B{i}",
            "role": "sales_rep", "tenant_id": "ten_B", "is_active": True,
        })

    a_users = await users.list_users(tenant_id="ten_A")
    b_users = await users.list_users(tenant_id="ten_B")

    assert {u.user_id for u in a_users} == {"user_A0", "user_A1", "user_A2"}
    assert {u.user_id for u in b_users} == {"user_B0", "user_B1"}
    # No cross-tenant leakage in either direction.
    assert all(u.tenant_id == "ten_A" for u in a_users)
    assert all(u.tenant_id == "ten_B" for u in b_users)
    assert await users.count_users(tenant_id="ten_A") == 3
    assert await users.count_users(tenant_id="ten_B") == 2
    # Admin list scoped to a tenant is likewise isolated.
    admin_rows = await users.list_tenant_users_admin("ten_B")
    assert {r["user_id"] for r in admin_rows} == {"user_B0", "user_B1"}
