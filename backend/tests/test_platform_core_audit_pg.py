"""Phase 2b — audit_logs against real Postgres.

Covers AuditRepository (the hot write path, the admin filter set, the
distinct() -> SELECT DISTINCT ports, the recent-changes predicate), the
response-shape rule that reproduces each Mongo writer's document keys, the
independent-commit guarantee of write_audit_entry, idempotent retries, and
EXPLAIN ANALYZE on the hot read queries.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from repositories.audit import AuditFilter, AuditRepository


def _uid(prefix: str = "audit") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _logger_enabled():
    """Re-enable the logger under test. The in-process Alembic run behind
    ``pg_migrated_url`` calls ``fileConfig()`` with ``disable_existing_loggers=True``."""
    logging.getLogger("repositories.audit").disabled = False
    yield


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
    import db.session as dbs

    monkeypatch.setenv("DATABASE_URL", pg_migrated_url)
    monkeypatch.setattr(dbs, "_engine", None)
    monkeypatch.setattr(dbs, "_sessionmaker", None)
    yield dbs
    if dbs._engine is not None:
        await dbs._engine.dispose()


async def _purge(maker, *ids):
    async with maker() as s:
        await s.execute(text("DELETE FROM audit_logs WHERE audit_id = ANY(:ids)"), {"ids": list(ids)})
        await s.commit()


# ── Writer document shapes (the API returned the raw Mongo documents) ──────

def _writer_entries(now):
    """One entry per writer, exactly as each writer builds it today."""
    return {
        # services/security_logger.log_security_event
        "security": {
            "audit_id": _uid(), "event_category": "security", "event_type": "authz_failed",
            "severity": "warning", "actor_id": "u1", "actor_email": None, "resource_type": None,
            "resource_id": None, "action": "check", "outcome": "denied", "details": {},
            "timestamp": now,
        },
        # services/audit_service.log_action
        "activity": {
            "audit_id": _uid(), "event_category": "activity", "action": "role_create",
            "actor_id": "u1", "actor_email": "a@example.test", "resource_type": "role",
            "resource_id": "role_x", "details": {"name": "x"}, "timestamp": now,
        },
        # services/audit_service.log_ingested_crud (tenant-bearing, AI actor)
        "ingested_crud": {
            "audit_id": _uid(), "event_category": "activity", "event_type": "ingested_app.updated",
            "action": "updated", "resource_type": "projects.task", "resource_id": "t1",
            "actor_id": "u1", "actor_email": "a@example.test",
            "details": {"module": "projects", "entity": "task", "diff": {}}, "timestamp": now,
            "tenant_id": "ten1", "actor_type": "pmo_ai", "tool_name": "update_task_status",
        },
        # services/ai/agents/tools/conversational_tools (failed AI action)
        "ai_action": {
            "audit_id": _uid(), "event_category": "ai_action", "actor_id": "u1",
            "actor_user_id": "u1", "actor_type": "pmo_ai", "actor_email": None,
            "tool_name": "update_task_status", "tier": 2, "params": {"x": 1},
            "resource_type": "projects.task", "resource_id": None, "action": "update_task_status",
            "result_status": "error", "error_detail": None, "session_id": None,
            "details": {}, "timestamp": now,
        },
        # services/ingestion/repository.log_stage mirror
        "ingestion_stage": {
            "audit_id": _uid(), "event_category": "security", "event_type": "ingestion_stage",
            "action": "ingestion_started", "actor_id": "system",
            "actor_email": "system@aegisaxiom.internal", "resource_type": "app_ingestion",
            "resource_id": "app_1", "details": {"stage": "extract"}, "timestamp": now,
        },
        # pre-unification row: no event_category key at all
        "legacy": {
            "audit_id": _uid(), "action": "legacy_action", "actor_id": None, "actor_email": None,
            "resource_type": "contract", "resource_id": None, "details": {}, "timestamp": now,
        },
    }


@pytest.mark.asyncio
async def test_api_dict_reproduces_each_writers_document_keys(pg_session):
    repo = AuditRepository(pg_session)
    entries = _writer_entries(_now())
    for entry in entries.values():
        assert await repo.record(entry)
    rows = {r.audit_id: r.to_api_dict() for r in await repo.list_logs(AuditFilter(), limit=200)}
    for writer, entry in entries.items():
        assert set(rows[entry["audit_id"]]) == set(entry), writer


@pytest.mark.asyncio
async def test_record_is_idempotent_and_json_safe(pg_session, caplog):
    repo = AuditRepository(pg_session)
    aid = _uid()
    ts = _now()
    entry = {"audit_id": aid, "event_category": "activity", "action": "x",
             "details": {"after": {"due": ts}}, "timestamp": ts, "not_a_column": 1}
    with caplog.at_level(logging.WARNING, logger="repositories.audit"):
        assert await repo.record(entry) is True
    assert "not_a_column" in caplog.text
    assert await repo.record(entry) is False          # PK conflict -> DO NOTHING
    assert await repo.exists(aid)
    stored = (await repo.list_logs(AuditFilter(action="x")))[0]
    assert stored.details["after"]["due"] == ts.isoformat()


# ── Admin filters + distinct() ports ───────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_filters_and_distinct(pg_session):
    await pg_session.execute(text("DELETE FROM audit_logs"))
    repo = AuditRepository(pg_session)
    now = _now()
    rows = [
        dict(audit_id=_uid(), event_category="activity", action="a", resource_type="mod_x.thing",
             event_type="e1", timestamp=now),
        dict(audit_id=_uid(), event_category="security", action="b", resource_type="mod_x.other",
             event_type="e2", severity="critical", outcome="denied", timestamp=now - timedelta(seconds=1)),
        dict(audit_id=_uid(), action="legacy", resource_type="modYx.thing",       # no category
             timestamp=now - timedelta(seconds=2)),
        dict(audit_id=_uid(), event_category="activity", action="a", resource_type="mod_x.thing",
             event_type="e1", timestamp=now - timedelta(seconds=3)),
        dict(audit_id=_uid(), event_category="activity", action="c", resource_type=None,
             timestamp=now - timedelta(seconds=4)),
    ]
    for r in rows:
        await repo.record(r)

    act = AuditFilter(event_category="activity")
    assert await repo.count_logs(act) == 4               # legacy NULL counts as activity
    assert [r.action for r in await repo.list_logs(act)] == ["a", "legacy", "a", "c"]
    # module prefix: `_` must be literal, so "modYx." is NOT a match
    assert await repo.count_logs(AuditFilter(resource_type="mod_x.")) == 3
    assert await repo.count_logs(AuditFilter(resource_type="mod_x.thing")) == 2
    assert await repo.count_logs(AuditFilter(severity="critical", outcome="denied")) == 1
    assert await repo.count_logs(AuditFilter(action="a", event_type="e1")) == 2
    assert [r.action for r in await repo.list_logs(AuditFilter(), skip=1, limit=2)] == ["b", "legacy"]

    # distinct("resource_type") -> SELECT DISTINCT (NULL excluded, sorted)
    assert await repo.distinct_resource_types() == ["mod_x.other", "mod_x.thing", "modYx.thing"]
    # distinct("event_type", {"resource_type": rt})
    assert await repo.distinct_event_types("mod_x.thing") == ["e1"]
    assert await repo.distinct_event_types("modYx.thing") == []


# ── Recent-changes predicate ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recent_activity_predicate(pg_session):
    await pg_session.execute(text("DELETE FROM audit_logs"))
    repo = AuditRepository(pg_session)
    now = _now()

    def row(name, **kw):
        base = dict(audit_id=name, event_category="activity", action="updated",
                    resource_type="projects.task", resource_id="t1", actor_id="u1",
                    details={}, timestamp=now - timedelta(minutes=1))
        base.update(kw)
        return base

    for r in [
        row("top_tenant", tenant_id="ten1"),
        row("after_tenant", details={"after": {"tenant_id": "ten1", "project_id": "p1"}},
            timestamp=now - timedelta(minutes=2)),
        row("before_tenant", details={"before": {"tenant_id": "ten1", "project_id": "p1"}},
            timestamp=now - timedelta(minutes=3), actor_id=None, actor_user_id="u9"),
        row("ai_row", event_category="ai_action", tenant_id="ten1", timestamp=now - timedelta(minutes=4)),
        row("legacy_row", event_category=None, tenant_id="ten1", timestamp=now - timedelta(minutes=5)),
        row("security_row", event_category="security", tenant_id="ten1"),     # wrong category
        row("other_tenant", tenant_id="ten2"),
        row("no_tenant"),                                                      # pre-multitenant
        row("too_old", tenant_id="ten1", timestamp=now - timedelta(hours=3)),
        row("other_type", tenant_id="ten1", resource_type="projects.sprint"),
        row("project_row", tenant_id="ten1", resource_type="projects.project", resource_id="p1",
            timestamp=now - timedelta(minutes=6)),
    ]:
        await repo.record(r)

    common = dict(since=now - timedelta(hours=1), until=now, tenant_id="ten1")
    got = await repo.list_recent_activity(resource_types=["projects.task"], **common)
    assert [r.audit_id for r in got] == [
        "top_tenant", "after_tenant", "before_tenant", "ai_row", "legacy_row"]

    by_actor = await repo.list_recent_activity(resource_types=["projects.task"], actor_user_id="u9", **common)
    assert [r.audit_id for r in by_actor] == ["before_tenant"]

    by_project = await repo.list_recent_activity(
        resource_types=["projects.task", "projects.project"], project_id="p1", **common)
    assert [r.audit_id for r in by_project] == ["after_tenant", "before_tenant", "project_row"]

    assert len(await repo.list_recent_activity(resource_types=["projects.task"], limit=2, **common)) == 2
    assert await repo.list_recent_activity(resource_types=[], **common) == []


# ── Independent commit + idempotent retries (service level) ────────────────

@pytest.mark.asyncio
async def test_audit_row_survives_caller_rollback(sessionmaker_pg, global_engine):
    """A security denial is logged right before an HTTPException; the request
    transaction then rolls back. The audit row must survive (§A6.2)."""
    from services.audit_service import write_audit_entry

    aid = _uid()
    try:
        async with sessionmaker_pg() as caller:
            await caller.execute(text("SELECT 1"))            # caller txn open
            assert await write_audit_entry({"audit_id": aid, "event_category": "security",
                                            "action": "check", "timestamp": _now()})
            await caller.rollback()
        async with sessionmaker_pg() as s:
            assert await AuditRepository(s).exists(aid)
    finally:
        await _purge(sessionmaker_pg, aid)


@pytest.mark.asyncio
async def test_security_logger_writes_postgres(sessionmaker_pg, global_engine):
    from services.security_logger import log_security_event

    marker = _uid("res")
    await log_security_event(event_type="role_created", severity="info", user_id="u1",
                             user_email="a@example.test", resource="role", resource_id=marker,
                             action="create", outcome="success", details={"k": "v"})
    async with sessionmaker_pg() as s:
        ids = [r[0] for r in await s.execute(
            text("SELECT audit_id FROM audit_logs WHERE resource_id = :m"), {"m": marker})]
        row = (await AuditRepository(s).list_logs(AuditFilter(event_type="role_created")))
    try:
        assert len(ids) == 1
        doc = next(r for r in row if r.resource_id == marker).to_api_dict()
        assert doc["event_category"] == "security" and doc["outcome"] == "success"
    finally:
        await _purge(sessionmaker_pg, *ids)


@pytest.mark.asyncio
async def test_concurrent_retries_write_one_row(sessionmaker_pg):
    aid = _uid()

    async def write():
        async with sessionmaker_pg() as s:
            ok = await AuditRepository(s).record({"audit_id": aid, "action": "retry", "timestamp": _now()})
            await s.commit()
            return ok

    try:
        results = await asyncio.gather(*(write() for _ in range(5)))
        assert results.count(True) == 1, results
    finally:
        await _purge(sessionmaker_pg, aid)


@pytest.mark.asyncio
async def test_log_ingested_crud_retry_is_a_noop(sessionmaker_pg, global_engine):
    from services.audit_service import log_ingested_crud

    class _Actor:
        user_id = "u1"
        email = "a@example.test"

    req = _uid("req")
    kw = dict(module="projects", entity="task", action="updated", resource_id="t1",
              actor=_Actor(), before={"tenant_id": "ten1", "title": "a"},
              after={"tenant_id": "ten1", "title": "b"}, request_id=req)
    aid1 = await log_ingested_crud(**kw)
    aid2 = await log_ingested_crud(**kw)
    try:
        assert aid1 == aid2
        async with sessionmaker_pg() as s:
            n = (await s.execute(text("SELECT count(*) FROM audit_logs WHERE audit_id = :a"),
                                 {"a": aid1})).scalar_one()
            doc = (await AuditRepository(s).list_logs(AuditFilter(event_type="ingested_app.updated"), limit=200))
        assert n == 1
        mine = next(r for r in doc if r.audit_id == aid1).to_api_dict()
        assert mine["tenant_id"] == "ten1"
        assert mine["details"]["diff"] == {"title": {"before": "a", "after": "b"}}
    finally:
        await _purge(sessionmaker_pg, aid1)


# ── EXPLAIN ANALYZE on the hot read paths ──────────────────────────────────

async def _plan(session, sql, params=None) -> str:
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    rows = await session.execute(text("EXPLAIN (ANALYZE, COSTS OFF) " + sql), params or {})
    plan = "\n".join(r[0] for r in rows)
    print(plan)
    return plan


@pytest.mark.asyncio
async def test_explain_analyze_hot_queries_use_indexes(pg_session):
    repo = AuditRepository(pg_session)
    now = _now()
    for i in range(50):
        await repo.record({"audit_id": _uid(), "event_category": "activity", "action": "a",
                           "resource_type": f"mod{i % 5}.thing", "resource_id": f"r{i}",
                           "tenant_id": "ten1", "actor_id": "u1",
                           "timestamp": now - timedelta(minutes=i)})
    # Realistic category mix. Without other categories the event_category
    # filter is non-selective and the planner is right to walk
    # ix_audit_logs_timestamp. Here, 3000 NEWER security rows sit ahead of the
    # activity rows in timestamp order, as security events do in production,
    # so a timestamp walk would have to filter through all of them.
    await pg_session.execute(text(
        "INSERT INTO audit_logs (audit_id, event_category, event_type, action, timestamp) "
        "SELECT 'audit_noise_' || g, 'security', 'permission_denied', 'deny', "
        "       CAST(:t AS timestamptz) + g * interval '1 second' "
        "FROM generate_series(1, 3000) AS g"
    ), {"t": now})
    await pg_session.execute(text("ANALYZE audit_logs"))

    admin = await _plan(pg_session,
        "SELECT * FROM audit_logs WHERE event_category = 'activity' ORDER BY timestamp DESC LIMIT 50")
    assert "ix_audit_logs_category_ts" in admin, admin

    recent = await _plan(pg_session,
        "SELECT * FROM audit_logs WHERE resource_type IN ('mod1.thing','mod2.thing') "
        "AND timestamp >= :s AND timestamp <= :u AND tenant_id = 'ten1' "
        "ORDER BY timestamp DESC LIMIT 100",
        {"s": now - timedelta(hours=1), "u": now})
    assert "Index" in recent and "audit_logs" in recent, recent

    prefix = await _plan(pg_session,
        "SELECT count(*) FROM audit_logs WHERE resource_type LIKE 'mod1.%'")
    assert "ix_audit_logs_resource_type_trgm" in prefix or "Index" in prefix, prefix

    distinct = await _plan(pg_session, "SELECT DISTINCT resource_type FROM audit_logs")
    assert "Index" in distinct, distinct
