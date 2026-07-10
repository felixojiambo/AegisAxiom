"""Tests for the GET /api/nova/cashflow-planner route.

Covers:
  * Route imports cleanly under the existing Nova router.
  * Forecast rows carry week_ending / expected_ar / expected_ap /
    net_cash_flow.
  * Past-due unpaid A/R rolls forward onto the active Friday.
  * Past-due unpaid A/P rolls forward onto the active Friday.
  * Synced (terminal) past-due rows do NOT roll forward.
  * Past-due unpaid items are not dropped.
  * Audit rows are written exactly once per rolled item; a repeat
    call against the same data does not duplicate them.
  * Permission gate (finance.cashflow.view) is enforced.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

# Required at import time by routes.nova → auth_helper.
os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production",
)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_helper import get_current_user
from routes.nova import router as nova_router


# ---------------------------------------------------------------------------
# Fake Mongo
# ---------------------------------------------------------------------------


class _FakeAsyncCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _InsertManyResult:
    def __init__(self, n):
        self.inserted_ids = list(range(n))


class _FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find(self, query, projection=None):
        if "kind" in (query or {}):
            kinds = query["kind"].get("$in", [])
            matched = [d for d in self.docs if d.get("kind") in kinds]
        elif "audit_key" in (query or {}):
            keys = query["audit_key"].get("$in", [])
            matched = [d for d in self.docs if d.get("audit_key") in keys]
        else:
            matched = list(self.docs)
        return _FakeAsyncCursor(matched)

    async def insert_many(self, docs):
        self.docs.extend(docs)
        return _InsertManyResult(len(docs))


class _FakeDB:
    def __init__(self, drafts=None):
        self._collections = {
            "finance_drafts": _FakeCollection(drafts),
            "nova_cashflow_audit": _FakeCollection(),
        }

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _FakeCollection()
        return self._collections[name]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _user(perms=("finance.cashflow.view",), user_id="user_test"):
    return SimpleNamespace(
        user_id=user_id,
        email="tester@simplimeta.com",
        account_id=None,
        permissions=list(perms),
    )


def _make_app(user) -> FastAPI:
    app = FastAPI()
    app.include_router(nova_router, prefix="/api/nova")
    app.dependency_overrides[get_current_user] = lambda: user
    return app


def _draft(
    *,
    draft_id: str,
    kind: str = "ar_invoice",
    amount: float = 100.0,
    due_date: str = "2026-01-09",
    sync_status: str = "Draft",
    counterparty: str = "Acme Co",
) -> dict:
    return {
        "draft_id": draft_id,
        "kind": kind,
        "counterparty": counterparty,
        "counterparty_id": "acc_1",
        "amount": amount,
        "memo": None,
        "sync_status": sync_status,
        "due_date": due_date,
        "qbo_id": None,
        "error": None,
        "source_origin": "manual",
        "source_period_start": None,
        "source_period_end": None,
        "source_contractor_id": None,
        "source_contract_id": None,
        "created_at": "2026-01-02T09:00:00+00:00",
        "updated_at": "2026-01-03T09:00:00+00:00",
        "created_by": "user_tester",
    }


@pytest.fixture(autouse=True)
def _mute_audit(monkeypatch):
    """The route calls require_perm which writes security log events
    on failure; mute the network-bound side effect (matches
    test_nova_route's pattern).
    """
    monkeypatch.setattr(
        "services.security_logger.log_security_event",
        AsyncMock(return_value=None),
    )


@pytest.fixture
def patch_db(monkeypatch):
    """Returns a builder: pass the rows you want in finance_drafts,
    get back the FakeDB so the test can inspect nova_cashflow_audit
    afterwards.
    """
    holder: dict = {}

    def _install(drafts):
        db = _FakeDB(drafts)
        holder["db"] = db
        monkeypatch.setattr("routes.nova.get_db", lambda: db)
        return db

    return _install


# ---------------------------------------------------------------------------
# Sanity
# ---------------------------------------------------------------------------


def test_route_is_registered_under_nova_router():
    paths = {r.path for r in nova_router.routes}
    assert "/cashflow-planner" in paths


def test_route_imports_cleanly():
    # Re-import to confirm nothing in the slice breaks module init.
    import importlib

    import routes.nova as nova_module

    importlib.reload(nova_module)
    assert hasattr(nova_module, "router")


# ---------------------------------------------------------------------------
# Forecast shape
# ---------------------------------------------------------------------------


def test_forecast_carries_week_ending_ar_ap_net(patch_db):
    patch_db([
        _draft(draft_id="d1", kind="ar_invoice", amount=500.0,
               due_date="2026-02-05"),
        _draft(draft_id="d2", kind="ap_bill", amount=150.0,
               due_date="2026-02-05"),
    ])
    app = _make_app(_user())
    r = TestClient(app).get(
        "/api/nova/cashflow-planner?as_of=2026-02-04",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["as_of"] == "2026-02-04"
    # 2026-02-04 is a Wednesday → active Friday is 2026-02-06.
    assert body["active_week_ending"] == "2026-02-06"
    assert body["horizon_weeks"] == 13
    assert body["items_count"] == 2
    assert body["rolled_forward_count"] == 0
    assert isinstance(body["forecast"], list)
    assert len(body["forecast"]) >= 1
    week = body["forecast"][0]
    for key in ("week_ending", "expected_ar", "expected_ap", "net_cash_flow"):
        assert key in week
    # 2026-02-05 Thursday → Friday 2026-02-06.
    feb6 = next(w for w in body["forecast"] if w["week_ending"] == "2026-02-06")
    assert feb6["expected_ar"] == 500.0
    assert feb6["expected_ap"] == 150.0
    assert feb6["net_cash_flow"] == 350.0


# ---------------------------------------------------------------------------
# Roll-forward — A/R and A/P
# ---------------------------------------------------------------------------


def test_unpaid_past_due_ar_rolls_forward(patch_db):
    db = patch_db([
        _draft(draft_id="ar_old", kind="ar_invoice", amount=400.0,
               due_date="2026-01-08", sync_status="Sent"),
    ])
    app = _make_app(_user())
    r = TestClient(app).get(
        "/api/nova/cashflow-planner?as_of=2026-02-04",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["rolled_forward_count"] == 1
    rolled = body["rolled_forward_items"][0]
    assert rolled["item_id"] == "ar_old"
    assert rolled["item_type"] == "ar"
    assert rolled["old_week_ending_date"] == "2026-01-09"
    assert rolled["new_week_ending_date"] == "2026-02-06"
    assert rolled["reason"] == "unpaid_past_due_roll_forward"
    # Forecast shows the rolled-forward amount on the active Friday.
    feb6 = next(
        w for w in body["forecast"] if w["week_ending"] == "2026-02-06"
    )
    assert feb6["expected_ar"] == 400.0
    # And an audit row was written.
    assert len(db["nova_cashflow_audit"].docs) == 1


def test_unpaid_past_due_ap_rolls_forward(patch_db):
    patch_db([
        _draft(draft_id="ap_old", kind="ap_bill", amount=75.0,
               due_date="2026-01-08", sync_status="Draft"),
    ])
    app = _make_app(_user())
    r = TestClient(app).get(
        "/api/nova/cashflow-planner?as_of=2026-02-04",
    )
    body = r.json()
    assert body["rolled_forward_count"] == 1
    rolled = body["rolled_forward_items"][0]
    assert rolled["item_type"] == "ap"
    feb6 = next(
        w for w in body["forecast"] if w["week_ending"] == "2026-02-06"
    )
    assert feb6["expected_ap"] == 75.0


def test_synced_past_due_item_does_not_roll_forward(patch_db):
    db = patch_db([
        _draft(draft_id="ar_paid", kind="ar_invoice", amount=999.0,
               due_date="2026-01-08", sync_status="Synced"),
    ])
    app = _make_app(_user())
    r = TestClient(app).get(
        "/api/nova/cashflow-planner?as_of=2026-02-04",
    )
    body = r.json()
    assert body["rolled_forward_count"] == 0
    # Terminal status → excluded from the forecast entirely.
    assert body["forecast"] == [] or all(
        w["expected_ar"] == 0 for w in body["forecast"]
    )
    assert db["nova_cashflow_audit"].docs == []


def test_past_due_unpaid_items_are_not_dropped(patch_db):
    """Even when the original due date sits weeks before the active
    Friday, the item must show up in the forecast (rolled, not
    dropped).
    """
    patch_db([
        _draft(draft_id="ar_ancient", kind="ar_invoice", amount=1234.0,
               due_date="2025-12-05", sync_status="Sent"),
    ])
    app = _make_app(_user())
    r = TestClient(app).get(
        "/api/nova/cashflow-planner?as_of=2026-02-04",
    )
    body = r.json()
    assert body["items_count"] == 1
    feb6 = next(
        w for w in body["forecast"] if w["week_ending"] == "2026-02-06"
    )
    assert feb6["expected_ar"] == 1234.0


# ---------------------------------------------------------------------------
# Audit idempotency
# ---------------------------------------------------------------------------


def test_repeated_route_call_does_not_duplicate_audit_rows(patch_db):
    db = patch_db([
        _draft(draft_id="ar_old", kind="ar_invoice", amount=400.0,
               due_date="2026-01-08", sync_status="Sent"),
    ])
    app = _make_app(_user())
    client = TestClient(app)

    r1 = client.get("/api/nova/cashflow-planner?as_of=2026-02-04")
    r2 = client.get("/api/nova/cashflow-planner?as_of=2026-02-04")

    assert r1.status_code == 200
    assert r2.status_code == 200
    # First call writes exactly one audit row.
    assert r1.json()["audit_rows_written"] == 1
    # Second call collapses on audit_key → zero new rows.
    assert r2.json()["audit_rows_written"] == 0
    assert len(db["nova_cashflow_audit"].docs) == 1
    audit_row = db["nova_cashflow_audit"].docs[0]
    assert audit_row["reason"] == "unpaid_past_due_roll_forward"
    assert audit_row["audit_key"] == (
        "unpaid_past_due_roll_forward:ar:ar_old:2026-01-09:2026-02-06"
    )


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def test_route_rejects_user_without_finance_cashflow_view(patch_db):
    patch_db([])
    app = _make_app(_user(perms=()))  # no permissions
    r = TestClient(app).get("/api/nova/cashflow-planner")
    assert r.status_code == 403


def test_route_accepts_user_with_view_permission(patch_db):
    patch_db([])
    app = _make_app(_user(perms=("finance.cashflow.view",)))
    r = TestClient(app).get("/api/nova/cashflow-planner")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Query parsing
# ---------------------------------------------------------------------------


def test_invalid_as_of_returns_400(patch_db):
    patch_db([])
    app = _make_app(_user())
    r = TestClient(app).get("/api/nova/cashflow-planner?as_of=not-a-date")
    assert r.status_code == 400
    assert "as_of" in r.json()["detail"].lower()


def test_horizon_weeks_must_be_in_range(patch_db):
    patch_db([])
    app = _make_app(_user())
    r = TestClient(app).get("/api/nova/cashflow-planner?horizon_weeks=0")
    assert r.status_code == 422  # FastAPI Query validation


# ---------------------------------------------------------------------------
# Source mutation guard
# ---------------------------------------------------------------------------


def test_source_finance_drafts_is_not_mutated(patch_db):
    db = patch_db([
        _draft(draft_id="ar_old", kind="ar_invoice", amount=400.0,
               due_date="2026-01-08", sync_status="Sent"),
    ])
    app = _make_app(_user())
    TestClient(app).get("/api/nova/cashflow-planner?as_of=2026-02-04")

    # The single source doc should still carry the original due_date
    # and sync_status — the planner only rolls forward in its output
    # and in the audit log.
    src = db["finance_drafts"].docs[0]
    assert src["due_date"] == "2026-01-08"
    assert src["sync_status"] == "Sent"
