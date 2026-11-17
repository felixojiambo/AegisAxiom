"""Phase 2a completion — the users-repository methods the subscript-form
call sites needed.

Phase 2a's census inventoried only the attribute form ``db.users`` and missed
nine files reaching the collection as ``db["users"]`` — 28 live sites across the
projects routes, the AI mention resolver, the AP-draft builder and the
capacity-flags seed. These tests cover the repository methods those callers were
switched onto, with particular attention to the two places where a naive SQL
translation would silently change behaviour:

  * ``list_assignable_users(tier="members")`` — Mongo's ``{"$ne": "none"}`` also
    matched documents with NO ``pm_capacity_tier`` field. Plain SQL ``!=`` drops
    NULLs; ``IS DISTINCT FROM`` keeps them, which is the faithful port.
  * ``search_user_cards`` — the term comes from a promptable LLM. The Mongo path
    used ``re.escape``; the ILIKE path must escape ``%``/``_``/``\\`` or a
    prompt-injected ``%`` matches every user in the tenant.

``pg_session`` gives real Postgres with rollback-per-test.
"""
import pytest

from models import Tenant
from repositories.tenants import TenantRepository
from repositories.users import UserRepository

TEN_A = "ten_pa"
TEN_B = "ten_pb"


async def _tenants(session):
    repo = TenantRepository(session)
    await repo.create_tenant(
        Tenant(tenant_id=TEN_A, name="A", slug="pa", tenant_type="root", status="active")
    )
    await repo.create_tenant(
        Tenant(tenant_id=TEN_B, name="B", slug="pb", tenant_type="partner", status="active")
    )


async def _seed(session):
    """Four users in tenant A spanning every tier state, one in tenant B."""
    await _tenants(session)
    users = UserRepository(session)
    await users.create_user({
        "user_id": "u_main", "email": "grace@x.io", "name": "Grace Holloway",
        "role": "project_manager", "tenant_id": TEN_A, "is_active": True,
        "pm_capacity_tier": "main_pm", "is_assignable_as_pm": True,
        "is_assignable_as_member": True,
    })
    await users.create_user({
        "user_id": "u_over", "email": "gregory@x.io", "name": "Gregory Vance",
        "role": "project_manager", "tenant_id": TEN_A, "is_active": True,
        "pm_capacity_tier": "overflow_pm", "is_assignable_as_pm": True,
        "is_assignable_as_member": True,
    })
    await users.create_user({
        "user_id": "u_none", "email": "nina@x.io", "name": "Nina Cole",
        "role": "contractor", "tenant_id": TEN_A, "is_active": True,
        "pm_capacity_tier": "none", "is_assignable_as_pm": False,
        "is_assignable_as_member": False,
    })
    # No pm_capacity_tier at all — the row Mongo's `$ne: "none"` used to match.
    await users.create_user({
        "user_id": "u_null", "email": "pat@x.io", "name": "Pat Untiered",
        "role": "project_contributor", "tenant_id": TEN_A, "is_active": True,
    })
    # Inactive: must never appear in the picker regardless of tier.
    await users.create_user({
        "user_id": "u_off", "email": "old@x.io", "name": "Olive Retired",
        "role": "project_manager", "tenant_id": TEN_A, "is_active": False,
        "pm_capacity_tier": "main_pm",
    })
    # Other tenant.
    await users.create_user({
        "user_id": "u_other", "email": "eve@x.io", "name": "Eve Foreign",
        "role": "project_manager", "tenant_id": TEN_B, "is_active": True,
        "pm_capacity_tier": "main_pm",
    })
    return users


# ── tenant-scoped FK checks (projects_project / _task / _template) ──────────

@pytest.mark.asyncio
async def test_user_exists_in_tenant_is_tenant_scoped(pg_session):
    users = await _seed(pg_session)
    assert await users.user_exists_in_tenant("u_main", TEN_A) is True
    # A real user in ANOTHER tenant must read as "does not match any user" —
    # otherwise the 422 leaks the existence of a cross-tenant user_id.
    assert await users.user_exists_in_tenant("u_other", TEN_A) is False
    assert await users.user_exists_in_tenant("u_nope", TEN_A) is False


@pytest.mark.asyncio
async def test_count_users_in_tenant_dedupes_and_scopes(pg_session):
    users = await _seed(pg_session)
    assert await users.count_users_in_tenant(["u_main", "u_over"], TEN_A) == 2
    # Duplicates collapse, exactly as `{"$in": list(set(...))}` did — the
    # caller compares this against len(set(ids)).
    assert await users.count_users_in_tenant(["u_main", "u_main"], TEN_A) == 1
    assert await users.count_users_in_tenant(["u_main", "u_other"], TEN_A) == 1
    assert await users.count_users_in_tenant([], TEN_A) == 0


# ── CSV import/export lookups (projects_import_export) ─────────────────────

@pytest.mark.asyncio
async def test_email_lookups_are_case_insensitive_and_scoped(pg_session):
    users = await _seed(pg_session)
    assert await users.get_user_id_by_email_in_tenant("  GRACE@X.IO ", TEN_A) == "u_main"
    assert await users.get_user_id_by_email_in_tenant("eve@x.io", TEN_A) is None

    emails = await users.get_emails_by_ids(["u_main", "u_over", "ghost"], tenant_id=TEN_A)
    assert emails == {"u_main": "grace@x.io", "u_over": "gregory@x.io"}
    assert await users.get_emails_by_ids([], tenant_id=TEN_A) == {}


# ── the assignable-users picker (projects_assignable_users) ────────────────

@pytest.mark.asyncio
async def test_assignable_tiers(pg_session):
    users = await _seed(pg_session)

    async def ids(tier):
        return [u["user_id"] for u in
                await users.list_assignable_users(tenant_id=TEN_A, tier=tier)]

    assert await ids("main") == ["u_main"]
    assert sorted(await ids("overflow")) == ["u_main", "u_over"]
    assert await ids("all_pms") == await ids("overflow")
    # `all` is every ACTIVE user in the tenant — u_off is inactive, u_other is
    # another tenant.
    assert sorted(await ids("all")) == ["u_main", "u_none", "u_null", "u_over"]


@pytest.mark.asyncio
async def test_assignable_members_keeps_null_tier_rows(pg_session):
    """The `$ne: "none"` → IS DISTINCT FROM translation.

    Mongo matched documents missing the field entirely. `!= 'none'` in SQL would
    drop u_null, silently removing untiered users from every member picker.
    """
    users = await _seed(pg_session)
    got = sorted(u["user_id"] for u in
                 await users.list_assignable_users(tenant_id=TEN_A, tier="members"))
    assert got == ["u_main", "u_null", "u_over"]
    assert "u_none" not in got


@pytest.mark.asyncio
async def test_assignable_card_shape_is_complete_and_sorted(pg_session):
    users = await _seed(pg_session)
    rows = await users.list_assignable_users(tenant_id=TEN_A, tier="all")
    assert [r["name"] for r in rows] == sorted(r["name"] for r in rows)
    # Every key always present — the Mongo projection omitted absent fields and
    # the route patched four back with setdefault; columns cannot be absent.
    assert set(rows[0]) == {
        "user_id", "name", "role", "pm_capacity_tier", "is_assignable_as_pm",
        "is_assignable_as_member", "is_active", "picture",
    }


# ── the capacity admin surface (projects_capacity) ─────────────────────────

@pytest.mark.asyncio
async def test_capacity_matrix_and_row(pg_session):
    users = await _seed(pg_session)
    matrix = await users.list_capacity_matrix(TEN_A)
    # Every user in the tenant, active or not — unlike the picker.
    assert sorted(r["user_id"] for r in matrix) == [
        "u_main", "u_none", "u_null", "u_off", "u_over",
    ]
    assert set(matrix[0]) == {
        "user_id", "name", "email", "role", "pm_capacity_tier",
        "is_assignable_as_pm", "is_assignable_as_member", "is_active", "picture",
    }
    assert await users.get_capacity_row("u_main", TEN_A) is not None
    assert await users.get_capacity_row("u_other", TEN_A) is None


@pytest.mark.asyncio
async def test_set_capacity_tier_moves_tier_and_flags_together(pg_session):
    users = await _seed(pg_session)
    ok = await users.set_capacity_tier(
        "u_null", TEN_A, tier="main_pm",
        is_assignable_as_pm=True, is_assignable_as_member=True,
    )
    assert ok is True
    row = await users.get_capacity_row("u_null", TEN_A)
    assert row["pm_capacity_tier"] == "main_pm"
    assert row["is_assignable_as_pm"] is True
    assert row["is_assignable_as_member"] is True
    # ...and the picker agrees immediately: no window where a tier exists
    # without its derived flags.
    assert "u_null" in [
        u["user_id"] for u in
        await users.list_assignable_users(tenant_id=TEN_A, tier="main")
    ]


@pytest.mark.asyncio
async def test_set_capacity_tier_refuses_cross_tenant(pg_session):
    users = await _seed(pg_session)
    ok = await users.set_capacity_tier(
        "u_other", TEN_A, tier="none",
        is_assignable_as_pm=False, is_assignable_as_member=False,
    )
    assert ok is False
    # The other tenant's row is untouched — a tenant-admin cannot reach across.
    assert (await users.get_capacity_row("u_other", TEN_B))["pm_capacity_tier"] == "main_pm"


# ── the AI mention resolver (conversational_tools) ─────────────────────────

@pytest.mark.asyncio
async def test_search_user_cards_prefix_then_contains(pg_session):
    users = await _seed(pg_session)
    prefix = await users.search_user_cards("Gr", prefix_only=True)
    assert sorted(u["user_id"] for u in prefix) == ["u_main", "u_over"]
    # "ollow" is inside "Holloway" but starts nothing — prefix misses, the
    # contains fallback finds it. That two-step is the caller's behaviour.
    assert await users.search_user_cards("ollow", prefix_only=True) == []
    contains = await users.search_user_cards("ollow", prefix_only=False)
    assert [u["user_id"] for u in contains] == ["u_main"]
    assert set(contains[0]) == {"user_id", "name", "email"}


@pytest.mark.asyncio
async def test_search_user_cards_matches_email_and_scopes_to_ids(pg_session):
    users = await _seed(pg_session)
    by_email = await users.search_user_cards("nina@", prefix_only=True)
    assert [u["user_id"] for u in by_email] == ["u_none"]
    # Project-membership scoping: same term, restricted id set.
    scoped = await users.search_user_cards(
        "Gr", prefix_only=True, user_ids=["u_over"]
    )
    assert [u["user_id"] for u in scoped] == ["u_over"]
    assert await users.search_user_cards("Gr", prefix_only=True, user_ids=[]) == []


@pytest.mark.asyncio
async def test_search_user_cards_escapes_like_wildcards(pg_session):
    """A prompt-injected `%` or `_` must be a literal, not a wildcard.

    Without escaping, an LLM emitting "%" would return every user — the ILIKE
    equivalent of the ReDoS the Mongo path defused with re.escape.
    """
    users = await _seed(pg_session)
    assert await users.search_user_cards("%", prefix_only=False) == []
    assert await users.search_user_cards("_", prefix_only=False) == []
    assert await users.search_user_cards("%race", prefix_only=False) == []
    # The real substring still works, so the escaping did not over-reach.
    assert len(await users.search_user_cards("race", prefix_only=False)) == 1


@pytest.mark.asyncio
async def test_list_user_cards_by_ids(pg_session):
    users = await _seed(pg_session)
    cards = await users.list_user_cards_by_ids(["u_over", "u_main", "ghost"])
    assert [c["user_id"] for c in cards] == ["u_main", "u_over"]  # name-ascending
    assert await users.list_user_cards_by_ids([]) == []


# ── the capacity-flags backfill seed (seeds/seed_assignable_flags) ─────────

@pytest.mark.asyncio
async def test_capacity_backfill_rows_carry_the_deriving_inputs(pg_session):
    users = await _seed(pg_session)
    rows = {r["user_id"]: r for r in await users.list_capacity_backfill_rows()}
    # Every tenant's users — the backfill is global, not tenant-scoped.
    assert "u_other" in rows
    assert set(rows["u_main"]) == {"user_id", "role", "is_active", "pm_capacity_tier"}
    assert rows["u_main"]["role"] == "project_manager"
    assert rows["u_null"]["pm_capacity_tier"] is None
    assert rows["u_off"]["is_active"] is False
