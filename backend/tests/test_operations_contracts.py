"""Smoke tests for operations contract financial analytics endpoints.

Covers:
  GET /api/operations/contracts/financials  — with and without filters
  GET /api/operations/contracts/summary     — quick summary shape
  403 path for both endpoints               — missing permission

Uses FastAPI TestClient with dependency_overrides (canonical AEGISAXIOM pattern) and
mocked DB to avoid hitting a live Mongo instance or auth service.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(user_id: str = "usr_finance", perms: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        name="Test User",
        email="test@aegisaxiom.io",
        role="finance_admin",
        permissions=perms if perms is not None else ["operations.contracts.view"],
    )


def _async_find(docs: list) -> MagicMock:
    """Motor cursor stub: .find(..., ...).to_list(n) -> docs."""
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=docs)
    return cursor


def _build_app(user):
    from routes.operations_contracts import router
    from auth_helper import get_current_user

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/operations")
    test_app.dependency_overrides[get_current_user] = lambda: user
    return test_app


def _mock_db(contracts: list | None = None) -> MagicMock:
    db = MagicMock()
    db.contracts.find.return_value = _async_find(contracts or [])
    return db


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

CONTRACT_ACTIVE_EXCELLENT = {
    "contract_id": "contract_aaa111",
    "name": "Acme BOH",
    "status": "active",
    "contract_type": "boh",
    "account_id": "acc_001",
    "account_name": "Acme Corp",
    "resources": [{"user_id": "usr_001", "cost_rate": 60.0, "resource_type_id": "rt_001"}],
    "resource_types": [{"id": "rt_001", "name": "Engineer", "sell_rate": 120.0, "hours_purchased": 100.0}],
    "ar_summary": {"total_contract_value": 12000.0, "invoiced_to_date": 0.0, "outstanding": 12000.0},
    "ap_summary": {"total_cost_estimate": 6000.0, "billed_to_date": 0.0, "outstanding": 6000.0},
    "margin_summary": {
        "gross_margin_dollars": 6000.0,
        "gross_margin_percent": 50.0,
        "margin_health": "excellent",
        "health_color": "green",
    },
}

CONTRACT_ACTIVE_NEGATIVE = {
    "contract_id": "contract_bbb222",
    "name": "Beta Fixed Fee",
    "status": "active",
    "contract_type": "fixed_fee",
    "account_id": "acc_002",
    "account_name": "Beta LLC",
    "resources": [],
    "resource_types": [],
    "ar_summary": {"total_contract_value": 5000.0, "invoiced_to_date": 1000.0, "outstanding": 4000.0},
    "ap_summary": {"total_cost_estimate": 7000.0, "billed_to_date": 0.0, "outstanding": 7000.0},
    "margin_summary": {
        "gross_margin_dollars": -2000.0,
        "gross_margin_percent": -40.0,
        "margin_health": "negative",
        "health_color": "red",
    },
}

CONTRACT_COMPLETED_GOOD = {
    "contract_id": "contract_ccc333",
    "name": "Gamma Monthly",
    "status": "completed",
    "contract_type": "monthly",
    "account_id": "acc_003",
    "account_name": "Gamma Inc",
    "resources": [],
    "resource_types": [],
    "ar_summary": {"total_contract_value": 20000.0, "invoiced_to_date": 20000.0, "outstanding": 0.0},
    "ap_summary": {"total_cost_estimate": 14000.0, "billed_to_date": 14000.0, "outstanding": 0.0},
    "margin_summary": {
        "gross_margin_dollars": 6000.0,
        "gross_margin_percent": 30.0,
        "margin_health": "good",
        "health_color": "green",
    },
}

ALL_CONTRACTS = [CONTRACT_ACTIVE_EXCELLENT, CONTRACT_ACTIVE_NEGATIVE, CONTRACT_COMPLETED_GOOD]


# ---------------------------------------------------------------------------
# Tests: GET /api/operations/contracts/financials
# ---------------------------------------------------------------------------


class TestGetOperationsContractFinancials:

    def test_200_returns_all_contracts_and_summary(self):
        """Authenticated user with operations.contracts.view gets full analytics payload."""
        user = _make_user()
        app = _build_app(user)
        db = _mock_db(ALL_CONTRACTS)

        with patch("routes.operations_contracts.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get(
                "/api/operations/contracts/financials",
                headers={"Authorization": "Bearer test-token"},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert "contracts" in body
        assert "summary" in body
        assert "top_performers" in body
        assert "bottom_performers" in body
        assert "at_risk" in body

        summary = body["summary"]
        assert summary["total_contracts"] == 3
        # total_ar = 12000 + 5000 + 20000 = 37000
        assert summary["total_ar"] == pytest.approx(37000.0)
        # total_ap = 6000 + 7000 + 14000 = 27000
        assert summary["total_ap"] == pytest.approx(27000.0)
        assert summary["total_margin_dollars"] == pytest.approx(10000.0)

        hc = summary["health_counts"]
        assert hc["excellent"] == 1
        assert hc["negative"] == 1
        assert hc["good"] == 1

    def test_200_filter_by_status_passes_to_db(self):
        """status=active is passed through to the MongoDB query."""
        user = _make_user()
        active_only = [CONTRACT_ACTIVE_EXCELLENT, CONTRACT_ACTIVE_NEGATIVE]
        app = _build_app(user)
        db = _mock_db(active_only)

        with patch("routes.operations_contracts.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get(
                "/api/operations/contracts/financials?status=active",
                headers={"Authorization": "Bearer test-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["total_contracts"] == 2

        # Verify the status filter was included in the DB call
        call_args = db.contracts.find.call_args
        assert call_args[0][0].get("status") == "active"

    def test_200_filter_by_margin_health(self):
        """margin_health=negative post-filters to only at-risk contracts."""
        user = _make_user()
        app = _build_app(user)
        db = _mock_db(ALL_CONTRACTS)

        with patch("routes.operations_contracts.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get(
                "/api/operations/contracts/financials?margin_health=negative",
                headers={"Authorization": "Bearer test-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["total_contracts"] == 1
        assert body["contracts"][0]["contract_id"] == "contract_bbb222"

    def test_at_risk_includes_negative_health(self):
        """at_risk list includes contracts with margin_health in [low, negative]."""
        user = _make_user()
        app = _build_app(user)
        db = _mock_db(ALL_CONTRACTS)

        with patch("routes.operations_contracts.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get(
                "/api/operations/contracts/financials",
                headers={"Authorization": "Bearer test-token"},
            )

        body = resp.json()
        at_risk_ids = {c["contract_id"] for c in body["at_risk"]}
        assert "contract_bbb222" in at_risk_ids
        assert "contract_aaa111" not in at_risk_ids

    def test_403_missing_permission(self):
        """User without operations.contracts.view (and not *) gets 403."""
        user = _make_user(perms=["some.other.perm"])
        app = _build_app(user)
        db = _mock_db(ALL_CONTRACTS)

        with patch("routes.operations_contracts.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(
                "/api/operations/contracts/financials",
                headers={"Authorization": "Bearer test-token"},
            )

        assert resp.status_code == 403
        db.contracts.find.assert_not_called()

    def test_admin_wildcard_bypasses_permission(self):
        """User with * admin permission can access without explicit view perm."""
        user = _make_user(perms=["*"])
        app = _build_app(user)
        db = _mock_db([])

        with patch("routes.operations_contracts.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get(
                "/api/operations/contracts/financials",
                headers={"Authorization": "Bearer test-token"},
            )

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: GET /api/operations/contracts/summary
# ---------------------------------------------------------------------------


class TestGetOperationsContractsSummary:

    def test_200_summary_shape_and_totals(self):
        """Summary endpoint returns correct keys and calculated totals."""
        user = _make_user()
        app = _build_app(user)
        db = _mock_db(ALL_CONTRACTS)

        with patch("routes.operations_contracts.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get(
                "/api/operations/contracts/summary",
                headers={"Authorization": "Bearer test-token"},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Keys must match legacy shape exactly (Thread 2I frontend depends on this)
        for key in ("total_contracts", "total_ar", "total_ap", "total_margin",
                    "ar_outstanding", "ap_outstanding", "avg_margin_percent"):
            assert key in body, f"Missing key in summary response: {key}"

        assert body["total_contracts"] == 3
        assert body["total_ar"] == pytest.approx(37000.0)
        assert body["total_ap"] == pytest.approx(27000.0)
        assert body["total_margin"] == pytest.approx(10000.0)
        # ar_outstanding = 12000 + 4000 + 0 = 16000
        assert body["ar_outstanding"] == pytest.approx(16000.0)
        # ap_outstanding = 6000 + 7000 + 0 = 13000
        assert body["ap_outstanding"] == pytest.approx(13000.0)

    def test_200_empty_contracts_no_division_error(self):
        """Summary with no contracts returns zeros and does not divide by zero."""
        user = _make_user()
        app = _build_app(user)
        db = _mock_db([])

        with patch("routes.operations_contracts.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get(
                "/api/operations/contracts/summary",
                headers={"Authorization": "Bearer test-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_contracts"] == 0
        assert body["total_ar"] == 0
        assert body["avg_margin_percent"] == 0

    def test_403_missing_permission(self):
        """User without view permission gets 403 before any DB access."""
        user = _make_user(perms=["other.perm"])
        app = _build_app(user)
        db = _mock_db(ALL_CONTRACTS)

        with patch("routes.operations_contracts.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(
                "/api/operations/contracts/summary",
                headers={"Authorization": "Bearer test-token"},
            )

        assert resp.status_code == 403
        db.contracts.find.assert_not_called()
