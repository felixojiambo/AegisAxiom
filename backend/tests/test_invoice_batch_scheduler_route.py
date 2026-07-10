"""Closes the second remaining Contracts blocker: tests for the
scheduler-callable POST /api/invoice-batches/build-biweekly/scheduled.

The route lives in backend/routes/invoice_batches.py — it reuses
services.invoice_batch_builder.build_invoice_batches verbatim, so
these tests only cover the new surface (token auth, default period,
audit shape, builder-reuse) and not the builder logic itself
(which has its own suite in test_invoice_batch_builder.py).

Token decision
--------------
Env var:  INVOICE_BATCH_SCHEDULER_TOKEN
Header:   X-Scheduler-Token
Pattern:  shared-token compared in constant time via
          hmac.compare_digest in
          services/invoice_batch_scheduler_security.py — mirrors
          procurement_webhook_security.py for consistency.

Test contract
-------------
* 503 scheduler_not_configured when env var unset (fail closed)
* 401 invalid scheduler token when header missing
* 401 invalid scheduler token when header wrong
* 200 + builder reuse when token matches
* Response envelope shape: period_start, period_end, batches,
  skipped, warnings, dry_run, source="external_scheduler"
* Default period falls back to last complete 14-day window
  ending yesterday (UTC) when body omits dates
* Audit row stamps actor_id="system_scheduler" and
  details.source="external_scheduler"
* Manual /build route still stamps the human user_id (no
  regression)
* No in-process cron / interval loop / startup scheduler added
  (negative source-level lock)
"""

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import os
import pytest

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def test_scheduler_security_helpers_use_canonical_env_and_header(monkeypatch):
    """The env var and header names must stay stable — they are
    operator-facing config. Renaming either breaks every deploy
    that already wired a scheduler against this route."""
    from services.invoice_batch_scheduler_security import (
        SCHEDULER_TOKEN_ENV_VAR,
        SCHEDULER_TOKEN_HEADER,
        is_scheduler_configured,
        verify_scheduler_token,
    )

    assert SCHEDULER_TOKEN_ENV_VAR == "INVOICE_BATCH_SCHEDULER_TOKEN"
    assert SCHEDULER_TOKEN_HEADER == "X-Scheduler-Token"

    monkeypatch.delenv(SCHEDULER_TOKEN_ENV_VAR, raising=False)
    assert is_scheduler_configured() is False
    assert verify_scheduler_token("anything") is False, (
        "verify_scheduler_token must fail closed when the env var "
        "is unset, even if the header carries a non-empty value."
    )

    monkeypatch.setenv(SCHEDULER_TOKEN_ENV_VAR, "  ")  # whitespace-only
    assert is_scheduler_configured() is False

    monkeypatch.setenv(SCHEDULER_TOKEN_ENV_VAR, "abc123token")
    assert is_scheduler_configured() is True
    assert verify_scheduler_token("abc123token") is True
    assert verify_scheduler_token("  abc123token  ") is True, (
        "verify_scheduler_token must strip whitespace before "
        "comparing so a scheduler config with stray spaces still "
        "authenticates."
    )
    assert verify_scheduler_token("wrong") is False
    assert verify_scheduler_token("") is False
    assert verify_scheduler_token(None) is False


# ---------------------------------------------------------------------------
# Route behavior — uses the same fake-DB pattern as
# test_invoice_batch_routes.py so the suites stay shape-compatible.
# ---------------------------------------------------------------------------


class _FakeBatches:
    def __init__(self):
        self.docs = []

    def find(self, *a, **k):
        return _FakeCursor(list(self.docs))


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *a, **k):
        return self

    async def to_list(self, *a, **k):
        return self._rows


class _FakeDb:
    def __init__(self):
        self.invoice_batches = _FakeBatches()
        self.contracts = _FakeBatches()
        self.time_entries = _FakeBatches()
        self.finance_drafts = _FakeBatches()


@pytest.mark.asyncio
async def test_returns_503_when_scheduler_token_unset(monkeypatch):
    """An unconfigured deploy MUST NOT accept any scheduler call —
    even with a header present. The route returns 503 with a
    structured envelope so the operator can debug the missing env."""
    from routes.invoice_batches import build_biweekly_scheduled

    monkeypatch.delenv("INVOICE_BATCH_SCHEDULER_TOKEN", raising=False)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        await build_biweekly_scheduled(
            data={}, x_scheduler_token="any-token-at-all",
        )
    assert ei.value.status_code == 503
    detail = ei.value.detail
    assert isinstance(detail, dict)
    assert detail.get("code") == "scheduler_not_configured"
    assert "INVOICE_BATCH_SCHEDULER_TOKEN" in detail.get("message", "")


@pytest.mark.asyncio
async def test_returns_401_when_token_missing(monkeypatch):
    from routes.invoice_batches import build_biweekly_scheduled
    from fastapi import HTTPException

    monkeypatch.setenv("INVOICE_BATCH_SCHEDULER_TOKEN", "good-token")
    with pytest.raises(HTTPException) as ei:
        await build_biweekly_scheduled(data={}, x_scheduler_token=None)
    assert ei.value.status_code == 401
    assert ei.value.detail == "Invalid scheduler token"


@pytest.mark.asyncio
async def test_returns_401_when_token_wrong(monkeypatch):
    from routes.invoice_batches import build_biweekly_scheduled
    from fastapi import HTTPException

    monkeypatch.setenv("INVOICE_BATCH_SCHEDULER_TOKEN", "good-token")
    with pytest.raises(HTTPException) as ei:
        await build_biweekly_scheduled(data={}, x_scheduler_token="bad-token")
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_correct_token_calls_builder_and_returns_envelope(monkeypatch):
    """The route must call services.invoice_batch_builder.build_invoice_batches
    verbatim (no logic duplication) and return the canonical
    envelope shape."""
    import routes.invoice_batches as rib
    from routes.invoice_batches import build_biweekly_scheduled

    monkeypatch.setenv("INVOICE_BATCH_SCHEDULER_TOKEN", "good-token")
    monkeypatch.setattr(rib, "get_db", lambda: _FakeDb())
    monkeypatch.setattr(rib, "log_action", AsyncMock(return_value=None))

    builder_calls: list[dict] = []

    async def _fake_builder(db, *, period_start, period_end, contract_id_filter,
                            actor_user_id, dry_run):
        builder_calls.append({
            "period_start": period_start,
            "period_end": period_end,
            "contract_id_filter": contract_id_filter,
            "actor_user_id": actor_user_id,
            "dry_run": dry_run,
        })
        return (
            [{"batch_id": "bat_1", "contract_id": "contract_1"}],
            [{"contract_id": "contract_2", "reason": "no_unbatched_hours"}],
            [],
        )

    # build_invoice_batches is imported at the top of
    # routes.invoice_batches now (was previously deferred); patch the
    # symbol on the route module so the scheduled handler sees the fake.
    monkeypatch.setattr(rib, "build_invoice_batches", _fake_builder)
    # The route imports build_invoice_batches inside the function body,
    # so the monkeypatch above is what actually runs.

    result = await build_biweekly_scheduled(
        data={
            "period_start": "2026-04-01",
            "period_end": "2026-04-14",
            "contract_id": "contract_1",
            "dry_run": True,
        },
        x_scheduler_token="good-token",
    )

    # Builder reused — exactly one call with our pinned args.
    assert len(builder_calls) == 1
    call = builder_calls[0]
    assert call["period_start"] == "2026-04-01"
    assert call["period_end"] == "2026-04-14"
    assert call["contract_id_filter"] == "contract_1"
    assert call["actor_user_id"] == "system_scheduler"
    assert call["dry_run"] is True

    # Envelope shape matches the spec in the docstring.
    assert result["period_start"] == "2026-04-01"
    assert result["period_end"] == "2026-04-14"
    assert result["batches"] == [{"batch_id": "bat_1", "contract_id": "contract_1"}]
    assert result["skipped"] == [{"contract_id": "contract_2", "reason": "no_unbatched_hours"}]
    assert result["warnings"] == []
    assert result["dry_run"] is True
    assert result["source"] == "external_scheduler"


@pytest.mark.asyncio
async def test_default_period_is_last_complete_14_day_window(monkeypatch):
    """When body omits period_start / period_end, the route defaults
    to the most recent complete 14-day window ending yesterday (UTC).
    Locks the computation so a scheduler firing without a body gets
    the fortnight that just closed."""
    import routes.invoice_batches as rib
    from routes.invoice_batches import build_biweekly_scheduled

    monkeypatch.setenv("INVOICE_BATCH_SCHEDULER_TOKEN", "good-token")
    monkeypatch.setattr(rib, "get_db", lambda: _FakeDb())
    monkeypatch.setattr(rib, "log_action", AsyncMock(return_value=None))

    captured: dict = {}

    async def _fake_builder(db, **kw):
        captured.update(kw)
        return ([], [], [])

    # build_invoice_batches is imported at the top of
    # routes.invoice_batches now (was previously deferred); patch the
    # symbol on the route module so the scheduled handler sees the fake.
    monkeypatch.setattr(rib, "build_invoice_batches", _fake_builder)

    result = await build_biweekly_scheduled(
        data={}, x_scheduler_token="good-token",
    )

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    expected_end = (today - timedelta(days=1)).isoformat()
    expected_start = (today - timedelta(days=14)).isoformat()

    assert captured["period_start"] == expected_start
    assert captured["period_end"] == expected_end
    assert result["period_start"] == expected_start
    assert result["period_end"] == expected_end


@pytest.mark.asyncio
async def test_audit_row_stamps_system_scheduler_actor(monkeypatch):
    """Scheduler-driven sweeps must be distinguishable from manual
    /build calls in the audit log. The route stamps the synthetic
    actor_id 'system_scheduler' and an external_scheduler source
    discriminator in the details dict."""
    import routes.invoice_batches as rib
    from routes.invoice_batches import build_biweekly_scheduled

    monkeypatch.setenv("INVOICE_BATCH_SCHEDULER_TOKEN", "good-token")
    monkeypatch.setattr(rib, "get_db", lambda: _FakeDb())

    audit_calls: list[tuple] = []

    async def _capture_audit(*args, **kwargs):
        audit_calls.append((args, kwargs))

    monkeypatch.setattr(rib, "log_action", _capture_audit)

    async def _fake_builder(db, **kw):
        return ([], [], [])

    # build_invoice_batches is imported at the top of
    # routes.invoice_batches now (was previously deferred); patch the
    # symbol on the route module so the scheduled handler sees the fake.
    monkeypatch.setattr(rib, "build_invoice_batches", _fake_builder)

    await build_biweekly_scheduled(data={}, x_scheduler_token="good-token")

    assert len(audit_calls) == 1
    args, _kwargs = audit_calls[0]
    # log_action signature: (action, actor_id, actor_email,
    #                        resource_type, resource_id, details)
    action, actor_id, _actor_email, _resource_type, _resource_id, details = args
    assert action == "invoice_batch.build_biweekly_scheduled"
    assert actor_id == "system_scheduler"
    assert details.get("source") == "external_scheduler"


def test_no_in_process_scheduler_added():
    """Source-level negative lock: the dispatch explicitly forbids
    in-process interval loops, FastAPI startup cron tasks, and
    workerless polling loops. Lock the absence of those patterns
    in the routes file so a future refactor cannot quietly add a
    background-task scheduler."""
    from pathlib import Path

    routes_py = Path(__file__).resolve().parents[1] / "routes" / "invoice_batches.py"
    src = routes_py.read_text(encoding="utf-8")

    forbidden_patterns = (
        # No FastAPI startup events that kick off scheduled work
        "@app.on_event('startup')",
        '@app.on_event("startup")',
        "@router.on_event",
        # No asyncio interval loops
        "asyncio.create_task(",
        "asyncio.ensure_future(",
        # No threading-based timers
        "threading.Timer",
        # No while True polling loops inside the module
        "while True:",
    )
    for pattern in forbidden_patterns:
        assert pattern not in src, (
            f"routes/invoice_batches.py must not contain {pattern!r} — "
            "an in-process scheduler / interval loop is explicitly "
            "out of scope. Use the external scheduler that calls "
            "/api/invoice-batches/build-biweekly/scheduled."
        )


# ---------------------------------------------------------------------------
# Source-level locks — make sure the route + .env wiring are
# discoverable by future ops without re-reading the source.
# ---------------------------------------------------------------------------


def test_env_example_documents_scheduler_token():
    """backend/.env.example must document INVOICE_BATCH_SCHEDULER_TOKEN
    so a fresh deploy can find the var by name without grepping the
    code. The header name must also appear alongside the var for the
    same reason."""
    from pathlib import Path

    # The canonical config example lives at backend/.env.example,
    # not the repo root. tests/ is at parents[0]; backend/ is
    # parents[1]; the example sits there.
    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    assert env_example.exists(), (
        "backend/.env.example must exist — the scheduler env var "
        "is operator-facing config."
    )
    src = env_example.read_text(encoding="utf-8")
    assert "INVOICE_BATCH_SCHEDULER_TOKEN" in src, (
        "backend/.env.example must declare "
        "INVOICE_BATCH_SCHEDULER_TOKEN."
    )
    assert "X-Scheduler-Token" in src, (
        "backend/.env.example must reference the X-Scheduler-Token "
        "header next to the env var so operators don't have to read "
        "code to find the header name."
    )


def test_route_mounted_at_canonical_scheduled_path():
    """Source-level lock on the route path so a refactor that
    splits invoice_batches.py cannot silently change the URL."""
    from pathlib import Path

    routes_py = Path(__file__).resolve().parents[1] / "routes" / "invoice_batches.py"
    src = routes_py.read_text(encoding="utf-8")
    assert (
        '@router.post("/invoice-batches/build-biweekly/scheduled")'
        in src
    ), (
        "Scheduler route must be mounted at "
        "/invoice-batches/build-biweekly/scheduled (full external "
        "path /api/invoice-batches/build-biweekly/scheduled)."
    )
    # Negative lock against a path that would collide with the
    # existing user-session /build-biweekly alias.
    assert (
        'POST("/invoice-batches/build-biweekly"' not in src
        or 'POST("/invoice-batches/build-biweekly/scheduled")' in src
    ), (
        "Scheduler route must be DISTINCT from the user-session "
        "/build-biweekly alias."
    )


def test_manual_build_route_still_stamps_human_actor():
    """Regression lock — manual /build path stays unchanged so it
    continues to stamp the human actor's user_id in the audit row.
    This is what lets ops tell scheduler-driven sweeps apart from
    human-driven sweeps post-hoc."""
    from pathlib import Path

    routes_py = Path(__file__).resolve().parents[1] / "routes" / "invoice_batches.py"
    src = routes_py.read_text(encoding="utf-8")
    # The manual /build handler must keep using current_user.user_id
    # in the audit row. The decorator may carry a dependencies=[...]
    # block (ai-authz dep) so locate the path string instead of
    # matching the full one-line decorator form.
    idx = src.find('"/invoice-batches/build"')
    assert idx != -1, "Manual /build route must still exist."
    # The block from the route-decorator path through the next
    # /invoice-batches/build-biweekly path marker must contain the
    # human-actor audit shape.
    end = src.find('"/invoice-batches/build-biweekly"', idx)
    assert end != -1
    block = src[idx:end]
    assert "current_user.user_id" in block, (
        "Manual /build route must stamp current_user.user_id in its "
        "audit row so scheduler vs human sweeps remain "
        "distinguishable."
    )
    assert '"invoice_batch.build"' in block, (
        "Manual /build route must keep stamping action "
        "'invoice_batch.build', distinct from the scheduler's "
        "'invoice_batch.build_biweekly_scheduled'."
    )
