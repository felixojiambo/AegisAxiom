"""Phase 2b — validate_dag port (plan §A7) against real Postgres.

The three required behaviours (params validate_dag_port.tests_required):
  * cycle detection still raises on a real cycle;
  * SKIP_STARTUP_DAG_VALIDATION=true short-circuits and logs;
  * an unreachable / empty DB at startup logs and continues (boot survives).
Plus the pure DFS (find_cycle) and the Postgres-backed implication graph.
"""
import logging

import pytest
import pytest_asyncio
from sqlalchemy import text

from repositories.permissions import PermissionsRepository
from services.permission_registry import (
    find_cycle, get_implication_graph, validate_dag, validate_dag_at_startup,
)


@pytest_asyncio.fixture
async def unreachable_db(monkeypatch):
    """Point the lazy process-wide engine at a port nothing listens on."""
    import db.session as dbs

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none")
    monkeypatch.setattr(dbs, "_engine", None)
    monkeypatch.setattr(dbs, "_sessionmaker", None)
    yield
    if dbs._engine is not None:
        await dbs._engine.dispose()


# ── Pure DFS ───────────────────────────────────────────────────────────────

def test_find_cycle_pure():
    assert find_cycle({}) is None
    assert find_cycle({"a": ["b"], "b": ["c"], "c": []}) is None
    assert find_cycle({"a": ["ghost"]}) is None                  # unknown keys are terminal
    assert find_cycle({"a": ["b"], "b": ["c"], "c": ["a"]}) == ["a", "b", "c", "a"]
    assert find_cycle({"x": ["x"]}) == ["x", "x"]
    # diamond is not a cycle
    assert find_cycle({"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}) is None


def test_find_cycle_deep_chain_has_no_recursion_limit():
    n = 20000
    chain = {f"k{i}": [f"k{i + 1}"] for i in range(n)}
    chain[f"k{n}"] = []
    assert find_cycle(chain) is None
    chain[f"k{n}"] = ["k0"]
    cycle = find_cycle(chain)
    assert cycle[0] == cycle[-1] and len(cycle) == n + 2


# ── Postgres-backed graph + validate_dag ───────────────────────────────────

async def _seed(session, edges: dict, inactive=()):
    repo = PermissionsRepository(session)
    await session.execute(text("DELETE FROM permissions"))
    for key, implies in edges.items():
        kwargs = {"implies": implies} if implies is not None else {}
        await repo.upsert_permission(key, source="system", **kwargs)
    if inactive:
        await repo.deactivate_keys(set(inactive))


@pytest.mark.asyncio
async def test_graph_reads_active_keys_from_postgres(pg_session):
    await _seed(pg_session, {"a": ["b"], "b": None, "c": ["a"]}, inactive=["c"])
    assert await get_implication_graph(session=pg_session) == {"a": ["b"], "b": []}


@pytest.mark.asyncio
async def test_validate_dag_raises_on_a_real_cycle(pg_session):
    await _seed(pg_session, {"p.a": ["p.b"], "p.b": ["p.c"], "p.c": ["p.a"]})
    with pytest.raises(RuntimeError, match=r"cycle detected .*p\.a -> p\.b -> p\.c -> p\.a"):
        await validate_dag(session=pg_session)


@pytest.mark.asyncio
async def test_validate_dag_passes_on_a_dag(pg_session, caplog):
    await _seed(pg_session, {"p.a": ["p.b"], "p.b": [], "p.c": ["p.a", "p.b"]})
    with caplog.at_level(logging.INFO, logger="services.permission_registry"):
        await validate_dag(session=pg_session)
    assert "DAG validated (3 nodes)" in caplog.text


# ── Startup wrapper (boot-survivable) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_startup_ok(pg_session):
    await _seed(pg_session, {"p.a": ["p.b"], "p.b": []})
    assert await validate_dag_at_startup(session=pg_session) == "ok"


@pytest.mark.asyncio
async def test_startup_real_cycle_still_fails_boot(pg_session):
    await _seed(pg_session, {"p.a": ["p.b"], "p.b": ["p.a"]})
    with pytest.raises(RuntimeError, match="cycle detected"):
        await validate_dag_at_startup(session=pg_session)


@pytest.mark.asyncio
async def test_startup_empty_registry_logs_and_continues(pg_session, caplog):
    await pg_session.execute(text("DELETE FROM permissions"))
    with caplog.at_level(logging.WARNING, logger="services.permission_registry"):
        assert await validate_dag_at_startup(session=pg_session) == "empty"
    assert "empty registry" in caplog.text


@pytest.mark.asyncio
async def test_startup_unreachable_db_logs_and_continues(unreachable_db, caplog):
    with caplog.at_level(logging.ERROR, logger="services.permission_registry"):
        assert await validate_dag_at_startup() == "unavailable"
    assert "could not read the registry" in caplog.text
    assert "continuing boot" in caplog.text


@pytest.mark.asyncio
async def test_skip_flag_short_circuits_and_logs(unreachable_db, monkeypatch, caplog):
    # The DB is unreachable on purpose: a skip must not even try to read it.
    monkeypatch.setenv("SKIP_STARTUP_DAG_VALIDATION", "true")
    with caplog.at_level(logging.WARNING, logger="services.permission_registry"):
        assert await validate_dag_at_startup() == "skipped"
    assert "SKIPPED" in caplog.text and "SKIP_STARTUP_DAG_VALIDATION" in caplog.text
    assert "could not read" not in caplog.text


@pytest.mark.asyncio
async def test_skip_flag_off_values_do_not_skip(pg_session, monkeypatch):
    await _seed(pg_session, {"p.a": []})
    for value in ("", "false", "0", "no"):
        monkeypatch.setenv("SKIP_STARTUP_DAG_VALIDATION", value)
        assert await validate_dag_at_startup(session=pg_session) == "ok"
