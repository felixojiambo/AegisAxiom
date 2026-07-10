"""Production observability for the scheduled invoice-batch trigger.

Locks the telemetry collection + the two new endpoints:

  GET /api/invoice-batches/scheduler/status   (user-authenticated)
  GET /api/invoice-batches/scheduler/health   (X-Scheduler-Token)

The scheduled POST itself now writes a row into
``invoice_batch_scheduler_runs`` at start, then patches it to
``succeeded`` (with counts) or ``failed`` (with sanitized error).
This file pins:

* Started + succeeded telemetry row shape.
* Failed telemetry row shape (sanitized — no stack traces).
* Status endpoint reports last success / last failure / health.
* Health endpoint requires the scheduler token.
* Health endpoint returns 503 when configured but stale.
* Health endpoint returns 200 when recent success exists.
* Manual /build route does NOT write external_scheduler telemetry.
* No in-process cron / loop / startup task introduced.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


# ---------------------------------------------------------------------------
# Fake invoice_batch_scheduler_runs collection — minimal subset of
# Motor needed by the new code paths.
# ---------------------------------------------------------------------------


class _FakeSchedulerRuns:
    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None):
        self.rows: List[Dict[str, Any]] = list(rows or [])

    @staticmethod
    def _match(row, clause):
        if isinstance(clause, dict):
            for k, v in clause.items():
                if row.get(k) != v:
                    return False
            return True
        return False

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def update_one(self, query, update):
        for i, row in enumerate(self.rows):
            if self._match(row, query):
                if "$set" in update:
                    row.update(update["$set"])
                self.rows[i] = row
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    def find(self, query, projection=None):
        matched = [dict(r) for r in self.rows if self._match(r, query)]
        return _FakeCursor(matched)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, key, direction=1):
        self._rows.sort(
            key=lambda r: r.get(key) or "",
            reverse=(direction == -1),
        )
        return self

    async def to_list(self, *_a, **_k):
        return self._rows


class _FakeDb:
    def __init__(self):
        self.invoice_batch_scheduler_runs = _FakeSchedulerRuns()


def _user_with_finance_portal():
    return SimpleNamespace(
        user_id="finance_1",
        email="finance@example.test",
        name="Finance User",
        role="finance",
        permissions=["finance.portal"],
        tenant_id=None,
        account_id=None,
    )


# ---------------------------------------------------------------------------
# Scheduled POST writes started + succeeded telemetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduled_run_writes_started_then_succeeded_telemetry(monkeypatch):
    import routes.invoice_batches as rib
    from routes.invoice_batches import build_biweekly_scheduled

    monkeypatch.setenv("INVOICE_BATCH_SCHEDULER_TOKEN", "good-token")
    db = _FakeDb()
    monkeypatch.setattr(rib, "get_db", lambda: db)
    monkeypatch.setattr(rib, "log_action", AsyncMock(return_value=None))

    async def _fake_builder(_db, **_kw):
        return (
            [{"batch_id": "b1"}, {"batch_id": "b2"}],
            [{"contract_id": "c1", "reason": "no_unbatched_hours"}],
            [],
        )

    # build_invoice_batches is now imported at the top of
    # routes.invoice_batches; patch the bound name at the route
    # module's symbol table (not on the source module) so the
    # scheduled handler sees the fake.
    monkeypatch.setattr(rib, "build_invoice_batches", _fake_builder)

    result = await build_biweekly_scheduled(
        data={"period_start": "2026-04-01", "period_end": "2026-04-14"},
        x_scheduler_token="good-token",
    )

    # Exactly one telemetry row exists — the started row was patched
    # in place to succeeded (not duplicated).
    assert len(db.invoice_batch_scheduler_runs.rows) == 1
    row = db.invoice_batch_scheduler_runs.rows[0]
    assert row["status"] == "succeeded"
    assert row["source"] == "external_scheduler"
    assert row["period_start"] == "2026-04-01"
    assert row["period_end"] == "2026-04-14"
    assert row["batches_count"] == 2
    assert row["skipped_count"] == 1
    assert row["warnings_count"] == 0
    assert row["error"] is None
    assert row["started_at"] is not None
    assert row["finished_at"] is not None
    # The response now also carries the run_id so the caller can
    # correlate logs.
    assert result["run_id"] == row["run_id"]


@pytest.mark.asyncio
async def test_scheduled_run_writes_failed_telemetry_on_builder_exception(monkeypatch):
    """On a builder exception the route patches the started row to
    status=failed with a sanitized error string (the exception
    class name, not the full traceback) and re-raises so the
    scheduler caller sees the failure on the wire."""
    import routes.invoice_batches as rib
    from routes.invoice_batches import build_biweekly_scheduled

    monkeypatch.setenv("INVOICE_BATCH_SCHEDULER_TOKEN", "good-token")
    db = _FakeDb()
    monkeypatch.setattr(rib, "get_db", lambda: db)
    monkeypatch.setattr(rib, "log_action", AsyncMock(return_value=None))

    class _BuilderExploded(RuntimeError):
        pass

    async def _failing_builder(_db, **_kw):
        raise _BuilderExploded("boom — internal detail that must not surface")

    monkeypatch.setattr(rib, "build_invoice_batches", _failing_builder)

    with pytest.raises(_BuilderExploded):
        await build_biweekly_scheduled(
            data={}, x_scheduler_token="good-token",
        )

    assert len(db.invoice_batch_scheduler_runs.rows) == 1
    row = db.invoice_batch_scheduler_runs.rows[0]
    assert row["status"] == "failed"
    assert row["finished_at"] is not None
    # Sanitized — only the exception class name surfaces, not the
    # internal-detail message.
    assert row["error"] == "_BuilderExploded"
    assert "internal detail" not in (row.get("error") or "")


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_endpoint_reports_not_configured_when_env_unset(monkeypatch):
    import routes.invoice_batches as rib
    from routes.invoice_batches import get_scheduler_status

    monkeypatch.delenv("INVOICE_BATCH_SCHEDULER_TOKEN", raising=False)
    db = _FakeDb()
    monkeypatch.setattr(rib, "get_db", lambda: db)

    result = await get_scheduler_status(current_user=_user_with_finance_portal())
    assert result["configured"] is False
    assert result["health"] == "not_configured"
    assert result["last_success"] is None
    assert result["last_failure"] is None


@pytest.mark.asyncio
async def test_status_endpoint_reports_last_success_and_failure(monkeypatch):
    import routes.invoice_batches as rib
    from routes.invoice_batches import get_scheduler_status

    monkeypatch.setenv("INVOICE_BATCH_SCHEDULER_TOKEN", "good-token")
    db = _FakeDb()
    now = datetime.now(timezone.utc)
    db.invoice_batch_scheduler_runs.rows = [
        {
            "run_id": "schedrun_succ_1",
            "source": "external_scheduler",
            "status": "succeeded",
            "started_at": now - timedelta(hours=1),
            "finished_at": now - timedelta(minutes=58),
            "period_start": "2026-04-01",
            "period_end": "2026-04-14",
            "batches_count": 3,
            "skipped_count": 0,
            "warnings_count": 1,
            "error": None,
        },
        {
            "run_id": "schedrun_fail_1",
            "source": "external_scheduler",
            "status": "failed",
            "started_at": now - timedelta(days=2),
            "finished_at": now - timedelta(days=2, minutes=1),
            "period_start": "2026-03-18",
            "period_end": "2026-03-31",
            "error": "TimeoutError",
        },
    ]
    monkeypatch.setattr(rib, "get_db", lambda: db)

    result = await get_scheduler_status(current_user=_user_with_finance_portal())
    assert result["configured"] is True
    assert result["health"] == "ok"
    assert result["last_success"]["batches_count"] == 3
    assert result["last_failure"]["error"] == "TimeoutError"
    assert result["last_success_age_hours"] is not None
    assert result["last_success_age_hours"] < 2.0


@pytest.mark.asyncio
async def test_status_endpoint_returns_critical_when_stale(monkeypatch):
    import routes.invoice_batches as rib
    from routes.invoice_batches import get_scheduler_status

    monkeypatch.setenv("INVOICE_BATCH_SCHEDULER_TOKEN", "good-token")
    # Set stale-after to 1 hour so an old success counts as critical.
    monkeypatch.setenv("INVOICE_BATCH_SCHEDULER_STALE_AFTER_HOURS", "1")

    db = _FakeDb()
    now = datetime.now(timezone.utc)
    db.invoice_batch_scheduler_runs.rows = [
        {
            "run_id": "schedrun_old",
            "source": "external_scheduler",
            "status": "succeeded",
            "started_at": now - timedelta(hours=72),
            "finished_at": now - timedelta(hours=72),
            "period_start": "2026-03-18",
            "period_end": "2026-03-31",
            "batches_count": 1,
            "skipped_count": 0,
            "warnings_count": 0,
        },
    ]
    monkeypatch.setattr(rib, "get_db", lambda: db)

    result = await get_scheduler_status(current_user=_user_with_finance_portal())
    assert result["health"] == "critical"
    assert result["last_success_age_hours"] > 1


@pytest.mark.asyncio
async def test_status_endpoint_requires_view_or_build_permission(monkeypatch):
    import routes.invoice_batches as rib
    from routes.invoice_batches import get_scheduler_status
    from fastapi import HTTPException

    monkeypatch.setenv("INVOICE_BATCH_SCHEDULER_TOKEN", "good-token")
    monkeypatch.setattr(rib, "get_db", lambda: _FakeDb())

    no_perm = SimpleNamespace(
        user_id="np", email="np@example.test", name="NP",
        role="sales_ops", permissions=[], tenant_id=None, account_id=None,
    )
    with pytest.raises(HTTPException) as ei:
        await get_scheduler_status(current_user=no_perm)
    assert ei.value.status_code == 403


# ---------------------------------------------------------------------------
# Health endpoint (X-Scheduler-Token auth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_endpoint_requires_scheduler_token(monkeypatch):
    import routes.invoice_batches as rib
    from routes.invoice_batches import get_scheduler_health
    from fastapi import HTTPException

    monkeypatch.setenv("INVOICE_BATCH_SCHEDULER_TOKEN", "good-token")
    monkeypatch.setattr(rib, "get_db", lambda: _FakeDb())

    # Missing header → 401
    with pytest.raises(HTTPException) as ei:
        await get_scheduler_health(x_scheduler_token=None)
    assert ei.value.status_code == 401
    # Wrong header → 401
    with pytest.raises(HTTPException) as ei:
        await get_scheduler_health(x_scheduler_token="wrong")
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_health_endpoint_returns_503_when_not_configured(monkeypatch):
    import routes.invoice_batches as rib
    from routes.invoice_batches import get_scheduler_health
    from fastapi import HTTPException

    monkeypatch.delenv("INVOICE_BATCH_SCHEDULER_TOKEN", raising=False)
    monkeypatch.setattr(rib, "get_db", lambda: _FakeDb())

    with pytest.raises(HTTPException) as ei:
        await get_scheduler_health(x_scheduler_token="anything")
    assert ei.value.status_code == 503
    assert ei.value.detail["code"] == "scheduler_not_configured"


@pytest.mark.asyncio
async def test_health_endpoint_returns_200_when_recent_success(monkeypatch):
    import routes.invoice_batches as rib
    from routes.invoice_batches import get_scheduler_health

    monkeypatch.setenv("INVOICE_BATCH_SCHEDULER_TOKEN", "good-token")
    db = _FakeDb()
    now = datetime.now(timezone.utc)
    db.invoice_batch_scheduler_runs.rows = [
        {
            "run_id": "schedrun_ok",
            "source": "external_scheduler",
            "status": "succeeded",
            "started_at": now - timedelta(hours=1),
            "finished_at": now - timedelta(minutes=59),
        },
    ]
    monkeypatch.setattr(rib, "get_db", lambda: db)

    result = await get_scheduler_health(x_scheduler_token="good-token")
    assert result["health"] == "ok"
    assert result["last_success_age_hours"] < 2


@pytest.mark.asyncio
async def test_health_endpoint_returns_503_when_stale(monkeypatch):
    import routes.invoice_batches as rib
    from routes.invoice_batches import get_scheduler_health
    from fastapi import HTTPException

    monkeypatch.setenv("INVOICE_BATCH_SCHEDULER_TOKEN", "good-token")
    monkeypatch.setenv("INVOICE_BATCH_SCHEDULER_STALE_AFTER_HOURS", "1")
    db = _FakeDb()
    now = datetime.now(timezone.utc)
    db.invoice_batch_scheduler_runs.rows = [
        {
            "run_id": "schedrun_stale",
            "source": "external_scheduler",
            "status": "succeeded",
            "started_at": now - timedelta(hours=72),
            "finished_at": now - timedelta(hours=72),
        },
    ]
    monkeypatch.setattr(rib, "get_db", lambda: db)

    with pytest.raises(HTTPException) as ei:
        await get_scheduler_health(x_scheduler_token="good-token")
    assert ei.value.status_code == 503
    assert ei.value.detail["code"] == "scheduler_stale"
    assert ei.value.detail["health"] == "critical"


@pytest.mark.asyncio
async def test_health_endpoint_returns_200_when_no_success_yet(monkeypatch):
    """Pre-first-run state: configured + token valid + no
    succeeded row should NOT be stale — return 200 with
    health=no_success_yet so a fresh deploy doesn't page on its
    first morning."""
    import routes.invoice_batches as rib
    from routes.invoice_batches import get_scheduler_health

    monkeypatch.setenv("INVOICE_BATCH_SCHEDULER_TOKEN", "good-token")
    monkeypatch.setattr(rib, "get_db", lambda: _FakeDb())

    result = await get_scheduler_health(x_scheduler_token="good-token")
    assert result["health"] == "no_success_yet"
    assert result["last_success_age_hours"] is None


# ---------------------------------------------------------------------------
# Source-level negative locks
# ---------------------------------------------------------------------------


def test_manual_build_route_does_not_write_external_scheduler_telemetry():
    """Manual /build path must not stamp external_scheduler runs.
    The telemetry collection is specifically for cron-driven
    visibility — manual runs already have an audit row stamped
    with the human actor."""
    from pathlib import Path

    routes_py = Path(__file__).resolve().parents[1] / "routes" / "invoice_batches.py"
    src = routes_py.read_text(encoding="utf-8")
    # Find the manual /build handler block. The decorator may carry a
    # dependencies=[...] block (ai-authz dep) so locate by path
    # substring rather than the full one-line decorator.
    idx = src.find('"/invoice-batches/build"')
    assert idx != -1
    end = src.find('"/invoice-batches/build-biweekly"', idx)
    assert end != -1
    block = src[idx:end]
    # The manual /build block must NOT insert into the scheduler
    # runs collection — that would falsely show up in the status
    # card as a scheduler-driven sweep.
    assert "invoice_batch_scheduler_runs.insert_one" not in block, (
        "Manual /build must not write to invoice_batch_scheduler_runs. "
        "That collection is the external-scheduler telemetry feed."
    )


def test_no_in_process_scheduler_added_in_observability_layer():
    """Defensive negative lock against an in-process cron / loop
    creeping in alongside the telemetry layer."""
    from pathlib import Path

    routes_py = Path(__file__).resolve().parents[1] / "routes" / "invoice_batches.py"
    src = routes_py.read_text(encoding="utf-8")
    forbidden_patterns = (
        "@app.on_event('startup')",
        '@app.on_event("startup")',
        "@router.on_event",
        "asyncio.create_task(",
        "asyncio.ensure_future(",
        "threading.Timer",
        "while True:",
    )
    for pattern in forbidden_patterns:
        assert pattern not in src, (
            f"routes/invoice_batches.py must not contain {pattern!r}. "
            "The observability layer reads from the telemetry "
            "collection — it must not start an internal cron loop."
        )


def test_status_and_health_routes_mounted_at_canonical_paths():
    """Lock the URL paths so a refactor can't silently rename
    them and break the operator's bookmarks / monitor config."""
    from pathlib import Path

    routes_py = Path(__file__).resolve().parents[1] / "routes" / "invoice_batches.py"
    src = routes_py.read_text(encoding="utf-8")
    # The decorator may carry dependencies=[...] for the ai-authz dep,
    # so match on the URL substring rather than the full decorator.
    assert (
        '"/invoice-batches/scheduler/status"' in src
    ), "Status endpoint must mount at /invoice-batches/scheduler/status."
    assert (
        '"/invoice-batches/scheduler/health"' in src
    ), "Health endpoint must mount at /invoice-batches/scheduler/health."


def test_env_example_documents_stale_threshold():
    """backend/.env.example must document
    INVOICE_BATCH_SCHEDULER_STALE_AFTER_HOURS alongside the token
    so a fresh deploy can find the var without reading code."""
    from pathlib import Path

    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    if not env_example.exists():  # pragma: no cover
        pytest.skip(".env.example not present in this test image")
    src = env_example.read_text(encoding="utf-8")
    assert "INVOICE_BATCH_SCHEDULER_STALE_AFTER_HOURS" in src, (
        "backend/.env.example must declare "
        "INVOICE_BATCH_SCHEDULER_STALE_AFTER_HOURS."
    )
    # The default should be documented as 384 so an operator
    # changing it knows the reference point.
    assert "384" in src, (
        "backend/.env.example must document the 384h default for "
        "the stale threshold."
    )


def test_invoice_batches_page_renders_scheduler_status_card():
    """Source-level UI lock: the page must fetch the status
    endpoint and render the status card with the testid anchors
    a future browser smoke depends on."""
    from pathlib import Path

    page_py = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "pages"
        / "InvoiceBatchesPage.jsx"
    )
    if not page_py.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    src = page_py.read_text(encoding="utf-8")
    assert "/api/invoice-batches/scheduler/status" in src, (
        "InvoiceBatchesPage must fetch the scheduler status endpoint."
    )
    for testid in (
        "invoice-batch-scheduler-status-card",
        "invoice-batch-scheduler-health",
        "invoice-batch-scheduler-last-success",
        "invoice-batch-scheduler-last-failure",
        "invoice-batch-scheduler-refresh",
    ):
        assert testid in src, (
            f"InvoiceBatchesPage scheduler status card must expose "
            f"{testid!r} for browser smoke."
        )
