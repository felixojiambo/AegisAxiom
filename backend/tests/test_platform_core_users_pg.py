"""Phase 2a — users repository: filters, aggregation ports, child-table CRUD.

Validates the 2a.4 cross-domain surfaces against real Postgres: the ILIKE
trigram search, the ported GROUP BY aggregation, the $unwind→JOIN resource pool,
and the resource-type-assignment child-table methods. ``pg_session`` (rollback).
"""
import pytest

from models import Tenant
from repositories.tenants import TenantRepository
from repositories.users import UserRepository


async def _root(session):
    await TenantRepository(session).create_tenant(
        Tenant(tenant_id="ten_u", name="U", slug="u-root", tenant_type="root", status="active")
    )


@pytest.mark.asyncio
async def test_lookup_filters_and_search(pg_session):
    await _root(pg_session)
    users = UserRepository(pg_session)
    await users.create_user({"user_id": "u1", "email": "alice@x.io", "name": "Alice Johnson",
                             "role": "sales_rep", "tenant_id": "ten_u", "is_active": True})
    await users.create_user({"user_id": "u2", "email": "bob@x.io", "name": "Bob Smith",
                             "role": "agent", "tenant_id": "ten_u", "is_active": True})
    await users.create_user({"user_id": "u3", "email": "carol@x.io", "name": "Carol Johnson",
                             "role": "agent", "tenant_id": "ten_u", "is_active": False})

    assert {u.user_id for u in await users.get_users_by_ids(["u1", "u3", "zzz"])} == {"u1", "u3"}
    assert {u.user_id for u in await users.list_users(role_in=["agent"])} == {"u2", "u3"}
    assert {u.user_id for u in await users.list_users(role="agent", is_active=True)} == {"u2"}
    # Trigram ILIKE substring search on name.
    johnsons = await users.search_users_by_name("johnson")
    assert {u.user_id for u in johnsons} == {"u1", "u3"}
    assert await users.count_users(role="agent") == 2
    assert await users.admin_exists() is False


@pytest.mark.asyncio
async def test_credential_path_secret_separation(pg_session):
    await _root(pg_session)
    users = UserRepository(pg_session)
    await users.create_user({"user_id": "us", "email": "sec@x.io", "name": "Sec",
                             "role": "admin", "tenant_id": "ten_u", "is_active": True,
                             "password_hash": "PH"})
    rec = await users.get_auth_record_by_email("sec@x.io")
    assert rec is not None
    dto, ph = rec
    assert ph == "PH" and "password_hash" not in dto.model_dump()
    assert await users.get_password_hash_by_email("sec@x.io") == "PH"
    assert await users.admin_exists() is True


@pytest.mark.asyncio
async def test_count_contractors_by_business_aggregation(pg_session):
    """Ported GROUP BY aggregation (contractor_businesses.py:54)."""
    await _root(pg_session)
    users = UserRepository(pg_session)
    # cb_1: 2 active contractors; cb_2: 1 active + 1 inactive; cb_3: none active.
    seed = [
        ("c1", "cb_1", True), ("c2", "cb_1", True),
        ("c3", "cb_2", True), ("c4", "cb_2", False),
        ("c5", "cb_3", False),
    ]
    for uid, cb, active in seed:
        await users.create_user({"user_id": uid, "email": f"{uid}@x.io", "name": uid,
                                 "role": "contractor", "tenant_id": "ten_u",
                                 "contractor_business_id": cb, "is_active": active})
    counts = await users.count_contractors_by_business(["cb_1", "cb_2", "cb_3", "cb_none"])
    assert counts == {"cb_1": 2, "cb_2": 1}  # inactive + empty businesses excluded
    assert await users.count_contractors_by_business([]) == {}


@pytest.mark.asyncio
async def test_resource_assignment_crud_and_pool(pg_session):
    """Child-table CRUD + $unwind→JOIN resource pool + pending filter."""
    await _root(pg_session)
    users = UserRepository(pg_session)
    await users.create_user({"user_id": "rc", "email": "rc@x.io", "name": "RC",
                             "role": "contractor", "tenant_id": "ten_u",
                             "contractor_business_id": "cb_9", "is_active": True})

    # upsert (insert then update-in-place)
    a = await users.upsert_resource_assignment("rc", "rt_1", {
        "resource_type_name": "Tech", "cost_rate": 75.0, "is_active": True,
        "approval_status": "pending",
    })
    assert a["resource_type_name"] == "Tech" and a["approval_status"] == "pending"
    a2 = await users.upsert_resource_assignment("rc", "rt_1", {"cost_rate": 80.0})
    assert a2["cost_rate"] == 80.0
    assert len(await users.list_user_resource_assignments("rc")) == 1  # updated, not duplicated

    # pending filter surfaces the contractor
    pending = await users.list_contractors_with_pending_assignments()
    assert any(c["user_id"] == "rc" for c in pending)

    # resource pool ($unwind→JOIN) — only ACTIVE assignments of ACTIVE contractors
    pool = await users.list_resource_pool(resource_type_id="rt_1")
    assert [r["user_id"] for r in pool] == ["rc"]
    assert pool[0]["cost_rate"] == 80.0

    # approve, then a rt-scoped user lookup
    await users.update_resource_assignment("rc", "rt_1", {"approval_status": "approved"})
    got = await users.get_resource_assignment("rc", "rt_1")
    assert got["approval_status"] == "approved"
    by_type = await users.list_users_by_resource_type("rt_1")
    assert by_type[0]["user_id"] == "rc" and by_type[0]["cost_rate"] == 80.0

    # delete
    assert await users.delete_resource_assignment("rc", "rt_1") is True
    assert await users.list_user_resource_assignments("rc") == []
