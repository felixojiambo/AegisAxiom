"""Phase 2b — policies + policy_targets against real Postgres.

Covers PolicyRepository (admin CRUD, ILIKE search, soft delete, atomic
toggle, system-policy upsert), the Postgres seed_system_policies, the
response-shape helper pinned by contracts/captures/policies_list.json, the
policy_targets target registry, and the engine's policy/target loaders.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from models import Policy
from repositories.policies import (
    PolicyRepository, PolicyTargetRepository, policy_to_api_dict,
)


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _policy(**kw) -> Policy:
    base = dict(policy_id=_uid("policy"), name="Guard", description="d", module="sales",
                entity="deal", trigger={"event": "on_update"},
                conditions=[{"field": "amount", "operator": "greater_than", "value": 5,
                             "value_type": "number"}],
                actions=[{"type": "block", "message": "no"}])
    base.update(kw)
    return Policy(**base)


@pytest_asyncio.fixture
async def sessionmaker_pg(pg_migrated_url):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(pg_migrated_url, pool_size=8, max_overflow=4)
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def global_engine(pg_migrated_url, monkeypatch):
    """Point db.session's lazy process-wide engine at the test DB for code that
    opens its own sessions; rebuilt per test (one event loop per test)."""
    import db.session as dbs

    monkeypatch.setenv("DATABASE_URL", pg_migrated_url)
    monkeypatch.setattr(dbs, "_engine", None)
    monkeypatch.setattr(dbs, "_sessionmaker", None)
    yield dbs
    if dbs._engine is not None:
        await dbs._engine.dispose()


# ── PolicyRepository ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_policy_crud_search_and_soft_delete(pg_session):
    repo = PolicyRepository(pg_session)
    await pg_session.execute(text("DELETE FROM policies"))
    a = await repo.create_policy(_policy(name="Big 100% discount guard", priority=5))
    b = await repo.create_policy(_policy(name="Plain", description="under_score", priority=1,
                                         module="time", entity="entry"))

    got = await repo.get_policy(a.policy_id)
    assert got.trigger.event == "on_update"
    assert got.conditions[0].field == "amount" and got.actions[0].type == "block"

    assert [p.policy_id for p in await repo.list_policies()] == [a.policy_id, b.policy_id]
    assert [p.policy_id for p in await repo.list_policies(module="time")] == [b.policy_id]
    assert [p.policy_id for p in await repo.list_policies(enabled=True)] == [a.policy_id, b.policy_id]
    # search = escaped case-insensitive substring over name/description:
    # `%` and `_` are literals, as re.escape made them for the old $regex.
    assert [p.policy_id for p in await repo.list_policies(search="100%")] == [a.policy_id]
    assert await repo.list_policies(search="1000") == []
    assert [p.policy_id for p in await repo.list_policies(search="UNDER_SCORE")] == [b.policy_id]
    assert await repo.list_policies(search="underXscore") == []

    upd = await repo.update_policy(a.policy_id, {
        "priority": 9, "conditions": [], "unknown": "ignored", "condition_pattern": "1"})
    assert upd.priority == 9 and upd.conditions == [] and upd.condition_pattern == "1"

    before = await repo.get_policy(b.policy_id)
    deleted = await repo.soft_delete_policy(b.policy_id)
    assert deleted.deleted_at is not None
    assert deleted.updated_at == before.updated_at          # Mongo $set touched deleted_at only
    assert await repo.get_policy(b.policy_id) is None
    assert (await repo.get_policy(b.policy_id, include_deleted=True)).deleted_at is not None
    assert await repo.soft_delete_policy(b.policy_id) is None
    assert await repo.update_policy(b.policy_id, {"priority": 2}) is None


@pytest.mark.asyncio
async def test_toggle_and_engine_list(pg_session):
    repo = PolicyRepository(pg_session)
    await pg_session.execute(text("DELETE FROM policies"))
    p = await repo.create_policy(_policy(priority=3))
    q = await repo.create_policy(_policy(priority=7))
    assert (await repo.toggle_enabled(p.policy_id)).enabled is False
    assert [x.policy_id for x in await repo.list_active_for_engine()] == [q.policy_id]
    assert (await repo.toggle_enabled(p.policy_id)).enabled is True
    assert [x.policy_id for x in await repo.list_active_for_engine()] == [q.policy_id, p.policy_id]
    assert await repo.toggle_enabled("policy_missing") is None


@pytest.mark.asyncio
async def test_upsert_system_policy_preserves_operator_state(pg_session):
    repo = PolicyRepository(pg_session)
    pid = _uid("sys")
    defn = {"policy_id": pid, "name": "Lock", "description": "v1", "module": "time",
            "entity": "entry", "trigger": {"event": "on_update"}, "conditions": [], "actions": [],
            "priority": 1}
    assert await repo.upsert_system_policy(defn) is True
    first = await repo.get_policy(pid)
    assert first.is_system and first.enabled and first.created_by == "system"

    await repo.toggle_enabled(pid)                       # operator disables it
    await repo.soft_delete_policy(pid)                   # ...and deletes it
    assert await repo.upsert_system_policy({**defn, "description": "v2", "priority": 4}) is False
    after = await repo.get_policy(pid)
    assert after is not None                             # un-deleted
    assert after.enabled is False                        # operator's choice kept
    assert after.description == "v2" and after.priority == 4
    assert after.created_at == first.created_at and after.created_by == "system"


@pytest.mark.asyncio
async def test_seed_system_policies_postgres_idempotent(pg_session):
    from services.policy_seeds import SYSTEM_POLICIES, seed_system_policies

    await pg_session.execute(text("DELETE FROM policies"))
    assert await seed_system_policies(session=pg_session) == {"seeded": len(SYSTEM_POLICIES), "updated": 0}
    assert await seed_system_policies(session=pg_session) == {"seeded": 0, "updated": len(SYSTEM_POLICIES)}
    assert len(await PolicyRepository(pg_session).list_policies()) == len(SYSTEM_POLICIES)


@pytest.mark.asyncio
async def test_seeded_policy_api_shape_matches_pinned_contract(pg_session):
    """contracts/captures/policies_list.json pinned the Mongo item shape."""
    from services.policy_seeds import seed_system_policies

    await pg_session.execute(text("DELETE FROM policies"))
    await seed_system_policies(session=pg_session)
    item = policy_to_api_dict((await PolicyRepository(pg_session).list_policies())[0])
    assert set(item) == {
        "actions", "condition_logic", "conditions", "created_at", "created_by", "deleted_at",
        "description", "enabled", "entity", "is_system", "module", "name", "policy_id",
        "priority", "trigger", "updated_at",
    }
    assert set(item["trigger"]) == {"event"}
    if item["conditions"]:
        assert set(item["conditions"][0]) == {"field", "operator", "value", "value_type"}
    if item["actions"]:
        assert set(item["actions"][0]) == {"field", "message", "type"}
    # condition_pattern is emitted only when an admin set one
    custom = _policy(condition_pattern="(1 AND 2)")
    assert policy_to_api_dict(custom)["condition_pattern"] == "(1 AND 2)"


@pytest.mark.asyncio
async def test_concurrent_toggles_never_lose_a_flip(sessionmaker_pg):
    pid = _uid("tog")
    async with sessionmaker_pg() as s:
        await PolicyRepository(s).create_policy(_policy(policy_id=pid))
        await s.commit()

    async def toggle():
        async with sessionmaker_pg() as s:
            p = await PolicyRepository(s).toggle_enabled(pid)
            await s.commit()
            return p.enabled

    try:
        results = await asyncio.gather(*(toggle() for _ in range(6)))
        # Read-then-write would let two toggles read the same state and both
        # write the same value. The single UPDATE serializes: 6 flips, each
        # state seen exactly 3 times, and the policy ends where it started.
        assert results.count(True) == 3 and results.count(False) == 3, results
        async with sessionmaker_pg() as s:
            assert (await PolicyRepository(s).get_policy(pid)).enabled is True
    finally:
        async with sessionmaker_pg() as s:
            await s.execute(text("DELETE FROM policies WHERE policy_id = :p"), {"p": pid})
            await s.commit()


# ── policy_targets ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_policy_target_registry(pg_session):
    repo = PolicyTargetRepository(pg_session)
    slug = _uid("slug")
    assert await repo.upsert_targets(slug, "m", "e", ["on_create", "on_update", "on_create"]) == 2
    first = {t.event: t for t in await repo.list_active_targets(app_slug=slug)}
    assert set(first) == {"on_create", "on_update"}

    assert await repo.deactivate_for_app(slug) == 2
    assert await repo.list_active_targets(app_slug=slug) == []
    # re-registration flips back to active and keeps the original created_at
    await repo.upsert_targets(slug, "m", "e", ["on_create"])
    again = await repo.list_active_targets(app_slug=slug)
    assert [t.event for t in again] == ["on_create"]
    assert again[0].created_at == first["on_create"].created_at
    assert await repo.deactivate_for_app(slug) == 1


@pytest.mark.asyncio
async def test_admin_targets_include_ingested_rows(pg_session):
    from services.policy_engine import list_targets_for_admin_ui

    slug = _uid("slug")
    await PolicyTargetRepository(pg_session).upsert_targets(
        slug, "zz_ingested", "widget", ["on_create", "on_update"])
    targets = await list_targets_for_admin_ui(session=pg_session)
    ingested = [t for t in targets if t["module"] == "zz_ingested"]
    assert ingested == [{
        "module": "zz_ingested", "entity": "widget", "events": ["on_create", "on_update"],
        "source": "ingestion", "label": "Zz Ingested / Widget / on_create, on_update",
    }]


@pytest.mark.asyncio
async def test_engine_loads_policies_as_dicts_from_postgres(sessionmaker_pg, global_engine):
    import services.policy_engine as pe

    pid = _uid("eng")
    async with sessionmaker_pg() as s:
        await PolicyRepository(s).create_policy(_policy(policy_id=pid, priority=999))
        await s.commit()
    pe.invalidate_policy_cache()
    try:
        loaded = await pe._get_policies()
        mine = [p for p in loaded if p["policy_id"] == pid]
        assert mine and isinstance(mine[0], dict)
        assert mine[0]["trigger"] == {"event": "on_update"}
        assert mine[0].get("applies_to_roles") is None    # engine's role filter: absent
    finally:
        pe.invalidate_policy_cache()
        async with sessionmaker_pg() as s:
            await s.execute(text("DELETE FROM policies WHERE policy_id = :p"), {"p": pid})
            await s.commit()
