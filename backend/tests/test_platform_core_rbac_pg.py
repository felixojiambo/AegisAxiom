"""Phase 2b — roles + permissions against real Postgres.

Covers RolesRepository / PermissionsRepository methods that have callers, the
Postgres-backed seeders (seed_default_roles, seed_system_permissions,
register_permissions), the auth permission builder that replaced the
hardcoded role table, the orphaned-Nova-permission sweep (incl. the
finance.cashflow.view trap), and concurrency on the atomic role-array updates.

Most tests use the rolled-back ``pg_session``. The concurrency tests need
COMMITTED rows on SEPARATE connections, so they build their own sessionmaker
from ``pg_migrated_url`` and clean up after themselves (2a pattern).
"""
import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from models import Role, User
from repositories.permissions import PermissionsRepository
from repositories.roles import RolesRepository


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _role(name: str, perms=None, **kw) -> Role:
    return Role(role_id=kw.pop("role_id", _uid("role")), name=name,
                display_name=kw.pop("display_name", name.title()),
                permissions=list(perms or []), **kw)


@pytest_asyncio.fixture
async def sessionmaker_pg(pg_migrated_url):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(pg_migrated_url, pool_size=5, max_overflow=5)
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


# ── RolesRepository ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_role_crud_roundtrip(pg_session):
    repo = RolesRepository(pg_session)
    t0 = datetime.now(timezone.utc)
    a = await repo.create_role(_role("zz_alpha", ["a.view"], is_default=False,
                                     created_at=t0, updated_at=t0))
    b = await repo.create_role(_role("zz_beta", ["b.view"], is_default=True,
                                     created_at=t0 + timedelta(seconds=1),
                                     updated_at=t0 + timedelta(seconds=1)))

    assert (await repo.get_role(a.role_id)).name == "zz_alpha"
    assert (await repo.get_role_by_name("zz_beta")).role_id == b.role_id
    assert await repo.role_name_exists("zz_alpha")
    assert not await repo.role_name_exists("zz_nope")
    assert {"zz_alpha", "zz_beta"} <= await repo.list_role_names()

    # default order: is_default DESC first (the Mongo sort), then creation order
    names = [r.name for r in await repo.list_roles()]
    assert names.index("zz_beta") < names.index("zz_alpha")
    by_name = [r.name for r in await repo.list_roles(order="name")]
    assert by_name == sorted(by_name)

    upd = await repo.update_role(a.role_id, {"color": "#000000", "not_a_column": 1,
                                             "permissions": ["x.view"]})
    assert upd.color == "#000000" and upd.permissions == ["x.view"]
    assert upd.updated_at >= t0
    assert await repo.update_role("role_missing", {"color": "#111111"}) is None

    assert await repo.delete_role(a.role_id)
    assert not await repo.delete_role(a.role_id)
    assert await repo.get_role(a.role_id) is None


@pytest.mark.asyncio
async def test_role_api_dict_matches_mongo_document_shape(pg_session):
    repo = RolesRepository(pg_session)
    plain = await repo.create_role(_role("zz_plain", ["a.view"]))
    gen = await repo.create_role(_role("zz_generated", ["a.view"]), app_id="app_x")
    expected = {"role_id", "name", "display_name", "description", "color", "is_system",
                "is_default", "permissions", "created_at", "updated_at"}
    # Mongo role docs never carried app_id unless the ingestion generator set it.
    assert set(plain.to_api_dict()) == expected
    assert set(gen.to_api_dict()) == expected | {"app_id"}
    assert gen.to_api_dict()["app_id"] == "app_x"


@pytest.mark.asyncio
async def test_insert_role_if_absent_is_keyed_on_name(pg_session):
    repo = RolesRepository(pg_session)
    assert await repo.insert_role_if_absent(_role("zz_seeded", ["a"]))
    assert not await repo.insert_role_if_absent(_role("zz_seeded", ["b"]))
    assert await repo.get_role_permissions("zz_seeded") == ["a"]


@pytest.mark.asyncio
async def test_permission_array_ops_preserve_order_and_dedupe(pg_session):
    repo = RolesRepository(pg_session)
    await repo.create_role(_role("zz_arr", ["a", "b"]))
    assert await repo.add_permissions(["b", "c", "c", "d"], name="zz_arr")
    assert await repo.get_role_permissions("zz_arr") == ["a", "b", "c", "d"]
    assert await repo.add_permissions([], name="zz_arr")            # exists, nothing new
    assert not await repo.add_permissions(["x"], name="zz_missing")  # no such role
    assert await repo.remove_permissions(["b", "zz"], name="zz_arr")
    assert not await repo.remove_permissions(["zz"], name="zz_arr")  # held none
    assert await repo.get_role_permissions("zz_arr") == ["a", "c", "d"]

    await repo.create_role(_role("zz_arr2", ["d", "q"]))
    assert await repo.list_role_names_with_any_permission(["d", "nope"]) == ["zz_arr", "zz_arr2"]
    assert await repo.strip_permissions_from_all_roles(["d"]) == ["zz_arr", "zz_arr2"]
    assert await repo.get_role_permissions("zz_arr") == ["a", "c"]
    assert await repo.strip_permissions_from_all_roles(["a", "c"]) == ["zz_arr"]
    assert await repo.get_role_permissions("zz_arr") == []
    assert await repo.get_role_permissions("zz_missing") == []

    with pytest.raises(ValueError):
        await repo.add_permissions(["x"], name="zz_arr", role_id="r")


# ── PermissionsRepository ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_permission_registry_repository(pg_session):
    repo = PermissionsRepository(pg_session)
    await pg_session.execute(text("DELETE FROM permissions"))

    await repo.upsert_permission("x.view", source="system", group="X", label="View")
    await repo.upsert_permission("x.edit", source="system", group="X", implies=["x.view"])
    # A re-upsert that omits implies must keep the declared edges (the Mongo
    # $set only wrote implies when declared).
    await repo.upsert_permission("x.edit", source="system", group="X", label="Edit")
    edit = await repo.get_permission("x.edit")
    assert edit.implies == ["x.view"] and edit.label == "Edit"
    assert "implies" not in (await repo.get_permission("x.view")).to_api_dict()
    assert edit.to_api_dict()["implies"] == ["x.view"]

    await repo.upsert_permission("app_y.pm_a.view", source="ingestion", app_id="app_y")
    await repo.upsert_permission("app_y.pmXa.view", source="ingestion", app_id="app_y")
    await repo.upsert_permission("app_y.keep", source="manual_override", app_id="app_y")
    await repo.upsert_permission("app_y.auto", source="reconcile_auto", app_id="app_y")

    assert [p.key for p in await repo.list_permissions(group="X")] == ["x.edit", "x.view"]
    assert {p.key for p in await repo.list_permissions(source="ingestion")} == {
        "app_y.pm_a.view", "app_y.pmXa.view"}
    assert await repo.get_implication_graph() == {
        "x.view": [], "x.edit": ["x.view"], "app_y.pm_a.view": [],
        "app_y.pmXa.view": [], "app_y.keep": [], "app_y.auto": []}

    # anchored-prefix count: `_` is literal (the old re.escape), not a wildcard
    assert await repo.count_active_for_app("app_y") == 4
    assert await repo.count_active_for_app("app_y", key_prefix="app_y.pm_") == 1

    # re-ingestion sweep: only source=ingestion rows not kept are retired
    assert await repo.deactivate_stale_ingestion_keys(
        "app_y", {"app_y.pm_a.view"}, ingestion_source="ingestion") == 1
    assert await repo.list_keys_for_app("app_y", only_active=True) == {
        "app_y.pm_a.view", "app_y.keep", "app_y.auto"}
    assert await repo.list_keys_for_app("app_y") == {
        "app_y.pm_a.view", "app_y.pmXa.view", "app_y.keep", "app_y.auto"}
    assert "app_y.pmXa.view" not in await repo.get_valid_keys()
    assert "app_y.pmXa.view" in await repo.get_valid_keys(is_active=None)

    assert await repo.deactivate_keys({"x.view"}) == 1
    assert await repo.deactivate_keys({"x.view"}) == 0          # idempotent
    assert await repo.deactivate_app_key("app_y.keep", "app_y")
    assert not await repo.deactivate_app_key("app_y.keep", "app_y")
    assert await repo.delete_keys(["x.edit", "nope"]) == ["x.edit"]


@pytest.mark.asyncio
async def test_register_permissions_respects_override_and_reconcile_auto(pg_session):
    from services.permission_registry import register_permissions

    repo = PermissionsRepository(pg_session)
    await repo.upsert_permission("ov.key", source="manual_override", app_id="a1", label="Kept")
    await repo.upsert_permission("ra.key", source="reconcile_auto", app_id="a1")
    n = await register_permissions(
        [{"key": "ov.key", "label": "Clobber"}, {"key": "ra.key"}, {"key": "new.key", "implies": ["ov.key"]}],
        source="ingestion", app_id="a1", session=pg_session,
    )
    assert n == 1
    assert (await repo.get_permission("ov.key")).label == "Kept"
    assert (await repo.get_permission("ra.key")).source == "reconcile_auto"
    assert (await repo.get_permission("new.key")).implies == ["ov.key"]
    with pytest.raises(ValueError):
        await register_permissions([{"key": "bad", "implies": "nope"}], source="ingestion",
                                   session=pg_session)


# ── Seeders ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_system_permissions_is_postgres_and_idempotent(pg_session):
    import routes.nova  # noqa: F401 — registers the nova module permissions
    import routes.roles  # noqa: F401 — registers the core permissions
    from services.permission_registry import (
        get_registered_module_permissions, seed_system_permissions,
    )

    await pg_session.execute(text("DELETE FROM permissions"))
    expected = {p["key"] for p in get_registered_module_permissions()}
    assert await seed_system_permissions(session=pg_session) == len(get_registered_module_permissions())
    assert await seed_system_permissions(session=pg_session) == len(get_registered_module_permissions())
    keys = await PermissionsRepository(pg_session).get_valid_keys()
    assert keys == expected
    assert "finance.cashflow.view" in keys            # the trap: still registered
    for orphan in ("finance.cashflow.export", "finance.nova.run_projection",
                   "finance.cashflow.edit_assumptions"):
        assert orphan not in keys


@pytest.mark.asyncio
async def test_seed_default_roles_inserts_then_reconciles(pg_session):
    from routes.roles import DEFAULT_ROLES, seed_default_roles

    await pg_session.execute(text("DELETE FROM roles"))
    await seed_default_roles(session=pg_session)
    repo = RolesRepository(pg_session)
    assert await repo.list_role_names() == {r["name"] for r in DEFAULT_ROLES}

    # second run: no change to an untouched role
    finance = next(r for r in DEFAULT_ROLES if r["name"] == "finance")
    await seed_default_roles(session=pg_session)
    assert await repo.get_role_permissions("finance") == list(finance["permissions"])

    # an admin customization (extra key) is preserved; a deleted default comes back
    # only if the remaining set no longer implies it
    custom = [p for p in finance["permissions"] if p != "finance.cashflow.view"] + ["zz.custom"]
    await repo.set_role_permissions_by_name("finance", custom)
    await seed_default_roles(session=pg_session)
    after = await repo.get_role_permissions("finance")
    assert "zz.custom" in after
    assert "finance.cashflow.view" in after


def test_default_role_seed_lists_carry_no_orphaned_nova_keys():
    from routes.roles import DEFAULT_ROLES, _ORPHANED_NOVA_KEYS

    assert "finance.cashflow.view" not in _ORPHANED_NOVA_KEYS
    assert "nova.use" not in _ORPHANED_NOVA_KEYS
    for role in DEFAULT_ROLES:
        assert not (_ORPHANED_NOVA_KEYS & set(role["permissions"])), role["name"]
    # The two roles whose removed keys implied the view now grant it explicitly.
    by_name = {r["name"]: r for r in DEFAULT_ROLES}
    for name in ("director", "finance"):
        assert "finance.cashflow.view" in by_name[name]["permissions"], name


# ── Orphaned Nova sweep ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retire_orphaned_keys_preserves_implied_access(pg_session):
    from routes.roles import _ORPHANED_NOVA_KEYS
    from services.permission_registry import retire_orphaned_keys

    perms = PermissionsRepository(pg_session)
    roles = RolesRepository(pg_session)
    await pg_session.execute(text("DELETE FROM permissions"))
    await perms.upsert_permission("finance.cashflow.view", source="system")
    await perms.upsert_permission("finance.cashflow.edit_assumptions", source="system",
                                  implies=["finance.cashflow.view"])
    await perms.upsert_permission("finance.cashflow.export", source="system",
                                  implies=["finance.cashflow.view"])
    await perms.upsert_permission("finance.nova.run_projection", source="system",
                                  implies=["finance.cashflow.view", "finance.cashflow.edit_assumptions"])
    await perms.upsert_permission("nova.use", source="system")
    await roles.create_role(_role("zz_director", ["finance.nova.run_projection", "nova.use"]))
    await roles.create_role(_role("zz_rep", ["nova.export.create", "nova.use"]))
    await roles.create_role(_role("zz_viewer", ["finance.cashflow.view"]))

    out = await retire_orphaned_keys(_ORPHANED_NOVA_KEYS, session=pg_session)

    assert out["deleted"] == sorted(["finance.cashflow.edit_assumptions", "finance.cashflow.export",
                                     "finance.nova.run_projection"])
    assert out["granted"] == {"zz_director": ["finance.cashflow.view"]}
    assert out["stripped_from"] == ["zz_director", "zz_rep"]
    # the director keeps the Cashflow Planner; nobody lost nova.use
    assert await roles.get_role_permissions("zz_director") == ["nova.use", "finance.cashflow.view"]
    assert await roles.get_role_permissions("zz_rep") == ["nova.use"]
    assert await roles.get_role_permissions("zz_viewer") == ["finance.cashflow.view"]
    assert await perms.get_valid_keys() == {"finance.cashflow.view", "nova.use"}

    again = await retire_orphaned_keys(_ORPHANED_NOVA_KEYS, session=pg_session)
    assert again == {"deleted": [], "stripped_from": [], "granted": {}}


@pytest.mark.asyncio
async def test_retire_orphaned_keys_rejects_a_cycle(pg_session):
    from services.permission_registry import retire_orphaned_keys

    perms = PermissionsRepository(pg_session)
    await pg_session.execute(text("DELETE FROM permissions"))
    await perms.upsert_permission("c.a", source="system", implies=["c.b"])
    await perms.upsert_permission("c.b", source="system", implies=["c.a"])
    await perms.upsert_permission("c.gone", source="system")
    with pytest.raises(RuntimeError, match="cycle"):
        await retire_orphaned_keys({"c.gone"}, session=pg_session)


# ── Auth: one source for role permissions ──────────────────────────────────

@pytest.mark.asyncio
async def test_build_user_with_permissions_reads_the_roles_table(pg_session, monkeypatch):
    import auth_helper
    from routes.auth import _build_user_with_permissions

    # Pre-warm the 60s implication cache so the builder needs no second DB.
    monkeypatch.setitem(auth_helper._implication_graph_cache, "graph", {})
    monkeypatch.setitem(auth_helper._implication_graph_cache, "fetched_at", time.monotonic())

    await RolesRepository(pg_session).create_role(_role("zz_ops", ["deals.view", "accounts.view"]))
    user = User(user_id=_uid("user"), email="ops@example.test", name="Ops", role="zz_ops",
                extra_permissions=["reports.view"])
    built = await _build_user_with_permissions(user, pg_session)
    assert set(built.permissions) == {"deals.view", "accounts.view"}
    assert {"deals.view", "accounts.view", "reports.view"} <= set(built.effective_permissions)

    nobody = User(user_id=_uid("user"), email="n@example.test", name="N", role="zz_no_such_role")
    assert (await _build_user_with_permissions(nobody, pg_session)).permissions == []


# ── Concurrency (committed rows, separate connections) ─────────────────────

@pytest.mark.asyncio
async def test_concurrent_grants_on_one_role_lose_nothing(sessionmaker_pg):
    name = _uid("zz_race")
    async with sessionmaker_pg() as s:
        await RolesRepository(s).create_role(_role(name, ["base"]))
        await s.commit()

    async def grant(key):
        async with sessionmaker_pg() as s:
            ok = await RolesRepository(s).add_permissions([key], name=name)
            await s.commit()
            return ok

    try:
        keys = [f"k{i}" for i in range(8)]
        assert all(await asyncio.gather(*(grant(k) for k in keys)))
        async with sessionmaker_pg() as s:
            final = await RolesRepository(s).get_role_permissions(name)
        # A read-modify-write would drop keys under this race; the single
        # UPDATE serializes on the row lock and keeps every one.
        assert final[0] == "base" and sorted(final[1:]) == keys
    finally:
        async with sessionmaker_pg() as s:
            await s.execute(text("DELETE FROM roles WHERE name = :n"), {"n": name})
            await s.commit()


@pytest.mark.asyncio
async def test_concurrent_grant_and_revoke_both_apply(sessionmaker_pg):
    name = _uid("zz_race2")
    async with sessionmaker_pg() as s:
        await RolesRepository(s).create_role(_role(name, ["keep", "drop"]))
        await s.commit()

    async def grant():
        async with sessionmaker_pg() as s:
            await RolesRepository(s).add_permissions(["new"], name=name)
            await s.commit()

    async def revoke():
        async with sessionmaker_pg() as s:
            await RolesRepository(s).remove_permissions(["drop"], name=name)
            await s.commit()

    try:
        await asyncio.gather(grant(), revoke(), grant(), revoke())
        async with sessionmaker_pg() as s:
            assert sorted(await RolesRepository(s).get_role_permissions(name)) == ["keep", "new"]
    finally:
        async with sessionmaker_pg() as s:
            await s.execute(text("DELETE FROM roles WHERE name = :n"), {"n": name})
            await s.commit()


@pytest.mark.asyncio
async def test_insert_role_if_absent_race_has_one_winner(sessionmaker_pg):
    name = _uid("zz_seedrace")

    async def seed():
        async with sessionmaker_pg() as s:
            won = await RolesRepository(s).insert_role_if_absent(_role(name, ["a"]))
            await s.commit()
            return won

    try:
        results = await asyncio.gather(*(seed() for _ in range(5)))
        assert results.count(True) == 1, results
    finally:
        async with sessionmaker_pg() as s:
            await s.execute(text("DELETE FROM roles WHERE name = :n"), {"n": name})
            await s.commit()


@pytest.mark.asyncio
async def test_for_update_serializes_read_modify_write(sessionmaker_pg):
    """reassign_app_role_perms pattern: lock, read, rewrite. The second writer
    must see the first writer's result, so neither change is lost."""
    name = _uid("zz_rmw")
    async with sessionmaker_pg() as s:
        await RolesRepository(s).create_role(_role(name, ["base"]))
        await s.commit()

    first_locked = asyncio.Event()

    async def writer(key, hold):
        async with sessionmaker_pg() as s:
            repo = RolesRepository(s)
            role = await repo.get_role_by_name(name, for_update=True)
            if hold:
                first_locked.set()
                await asyncio.sleep(0.3)  # keep the lock while the other waits
            await repo.set_role_permissions_by_name(name, list(role.permissions) + [key])
            await s.commit()

    async def second():
        await first_locked.wait()
        await writer("second", hold=False)

    try:
        await asyncio.gather(writer("first", hold=True), second())
        async with sessionmaker_pg() as s:
            assert await RolesRepository(s).get_role_permissions(name) == ["base", "first", "second"]
    finally:
        async with sessionmaker_pg() as s:
            await s.execute(text("DELETE FROM roles WHERE name = :n"), {"n": name})
            await s.commit()


# ── workflow_executor guard (dynamic db[collection] path, binding decision 4) ──
# field_registry maps the `roles` and `audit` modules onto two Postgres-migrated
# collections; the static zero-grep cannot see workflow record actions resolving
# them to db[collection]. The guard must refuse every WRITE action BEFORE any
# Mongo access, and must leave unmigrated modules alone.

class _ExplodingDb:
    """Any Mongo access through this db is a test failure."""

    def __getitem__(self, name):
        raise AssertionError(f"workflow action touched Mongo collection {name!r}")

    def __getattr__(self, name):
        raise AssertionError(f"workflow action touched Mongo attribute {name!r}")


@pytest.mark.parametrize("module", ["roles", "audit"])
@pytest.mark.parametrize("action_name", ["update_field", "create_record", "delete_record"])
@pytest.mark.asyncio
async def test_workflow_write_actions_refuse_migrated_modules(module, action_name):
    import routes.audit  # noqa: F401  — registers module "audit" -> audit_logs
    import routes.roles  # noqa: F401  — registers module "roles" -> roles
    from services import workflow_executor as wx

    action = {
        "type": action_name,
        "module": module,
        "field": "name",
        "value": "x",
        "fields": {"name": "x"},
        "filter": {"name": "x"},
    }
    context = {"record": {"role_id": "role_x", "audit_id": "audit_x"}}
    handler = getattr(wx, f"_action_{action_name}")
    with pytest.raises(ValueError, match="PostgreSQL"):
        await handler(action, context, _ExplodingDb())


def test_workflow_guard_covers_every_2b_collection_and_spares_mongo_modules():
    from services import workflow_executor as wx
    from services.field_registry import register_module_config

    assert wx._POSTGRES_MIGRATED_COLLECTIONS >= {
        "roles", "permissions", "policies", "policy_targets", "audit_logs",
    }
    from services import field_registry

    register_module_config("_wx_guard_probe", "contracts", "contract_id", "Test")
    try:
        assert wx._get_collection("_wx_guard_probe") == "contracts"
    finally:
        field_registry._module_config.pop("_wx_guard_probe", None)
