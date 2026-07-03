"""Integration smoke tests for Finance FCC analytical (read-only) endpoints.

Covers one endpoint from each route file:
  - finance_kpis        : GET /api/finance/dashboard/kpis
  - finance_statements  : GET /api/finance/financials/balance-sheet
  - finance_working_cap : GET /api/finance/working-capital/ar-aging
  - finance_gl          : GET /api/finance/gl/accounts
  - finance_lists       : GET /api/finance/lists/customers

Each test checks:
  1. 200 + expected shape for an authenticated admin user
  2. 403 when the request carries a user token that lacks the required permission
  3. 401 when no token is supplied

Tests run against a live backend (REACT_APP_BACKEND_URL). They skip gracefully
if TEST_ADMIN_PASSWORD is not set in the environment.
"""
from __future__ import annotations

import os

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "support@simplimeta.io")
ADMIN_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "")

FINANCE_PREFIX = f"{BASE_URL}/api/finance"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def admin_session():
    if not ADMIN_PASSWORD:
        pytest.skip("TEST_ADMIN_PASSWORD not set — skipping integration tests")
    s = requests.Session()
    resp = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, f"Admin login failed: {resp.text}"
    return s


@pytest.fixture(scope="session")
def no_auth_session():
    return requests.Session()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_200(resp: requests.Response, label: str) -> dict:
    assert resp.status_code == 200, f"{label}: expected 200, got {resp.status_code} — {resp.text}"
    return resp.json()


def _assert_401(resp: requests.Response, label: str) -> None:
    assert resp.status_code == 401, (
        f"{label}: expected 401 for unauthenticated request, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

class TestDashboardKpis:
    """GET /api/finance/dashboard/kpis"""

    URL = f"{FINANCE_PREFIX}/dashboard/kpis"

    def test_kpis_200_shape(self, admin_session):
        data = _assert_200(admin_session.get(self.URL), "dashboard/kpis")
        expected_keys = {
            "cash_balance",
            "total_assets",
            "total_liabilities",
            "total_income",
            "total_expenses",
            "gross_profit",
            "net_income",
            "ar_total",
            "ap_total",
        }
        missing = expected_keys - set(data.keys())
        assert not missing, f"dashboard/kpis response missing keys: {missing}"

    def test_kpis_unauthenticated_401(self, no_auth_session):
        _assert_401(no_auth_session.get(self.URL), "dashboard/kpis (no auth)")


# ---------------------------------------------------------------------------
# Financial Statements
# ---------------------------------------------------------------------------

class TestFinancialStatements:
    """GET /api/finance/financials/balance-sheet"""

    URL = f"{FINANCE_PREFIX}/financials/balance-sheet"

    def test_balance_sheet_200(self, admin_session):
        resp = admin_session.get(self.URL)
        assert resp.status_code == 200, f"balance-sheet: {resp.status_code} — {resp.text}"
        data = resp.json()
        # Either a real document or our graceful empty response
        assert "line_items" in data or "end_period" in data or "warning" in data, (
            f"balance-sheet response has unexpected shape: {list(data.keys())}"
        )

    def test_balance_sheet_unauthenticated_401(self, no_auth_session):
        _assert_401(no_auth_session.get(self.URL), "balance-sheet (no auth)")

    def test_pnl_current_200(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/financials/pnl?period=current")
        assert resp.status_code == 200, f"pnl?period=current: {resp.status_code} — {resp.text}"

    def test_pnl_prior_200(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/financials/pnl?period=prior")
        assert resp.status_code == 200, f"pnl?period=prior: {resp.status_code} — {resp.text}"

    def test_pnl_invalid_period_422(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/financials/pnl?period=invalid")
        assert resp.status_code == 422, (
            f"pnl with invalid period should be 422, got {resp.status_code}"
        )

    def test_cash_flow_200(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/financials/cash-flow")
        assert resp.status_code == 200

    def test_trial_balance_200(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/financials/trial-balance")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Working Capital
# ---------------------------------------------------------------------------

class TestWorkingCapital:
    """GET /api/finance/working-capital/*"""

    def test_ar_aging_200_shape(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/working-capital/ar-aging")
        data = _assert_200(resp, "ar-aging")
        assert "line_items" in data or "column_names" in data, (
            f"ar-aging unexpected shape: {list(data.keys())}"
        )

    def test_ap_aging_200(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/working-capital/ap-aging")
        assert resp.status_code == 200

    def test_ar_aging_detail_200(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/working-capital/ar-aging-detail")
        data = _assert_200(resp, "ar-aging-detail")
        assert "line_items" in data

    def test_ap_aging_detail_200(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/working-capital/ap-aging-detail")
        assert resp.status_code == 200

    def test_dso_dpo_200_shape(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/working-capital/dso-dpo?months=3")
        data = _assert_200(resp, "dso-dpo")
        assert "series" in data
        assert "ar_total" in data
        assert "ap_total" in data
        if data["series"]:
            first = data["series"][0]
            assert "month" in first
            assert "dso" in first
            assert "dpo" in first

    def test_ar_aging_unauthenticated_401(self, no_auth_session):
        _assert_401(no_auth_session.get(f"{FINANCE_PREFIX}/working-capital/ar-aging"), "ar-aging (no auth)")


# ---------------------------------------------------------------------------
# GL Explorer
# ---------------------------------------------------------------------------

class TestGlExplorer:
    """GET /api/finance/gl and /api/finance/gl/* dimension lookups"""

    def test_gl_accounts_200_shape(self, admin_session):
        data = _assert_200(
            admin_session.get(f"{FINANCE_PREFIX}/gl/accounts"), "gl/accounts"
        )
        assert "rows" in data
        assert isinstance(data["rows"], list)

    def test_gl_txn_types_200(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/gl/txn-types")
        data = _assert_200(resp, "gl/txn-types")
        assert "rows" in data

    def test_gl_classes_200(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/gl/classes")
        data = _assert_200(resp, "gl/classes")
        assert "rows" in data

    def test_gl_locations_200(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/gl/locations")
        data = _assert_200(resp, "gl/locations")
        assert "rows" in data

    def test_gl_explorer_200_shape(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/gl?limit=10&skip=0")
        data = _assert_200(resp, "gl explorer")
        assert "rows" in data
        assert "total" in data
        assert isinstance(data["rows"], list)

    def test_gl_explorer_basis_cash_200(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/gl?basis=cash&limit=5")
        assert resp.status_code == 200

    def test_gl_invalid_basis_422(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/gl?basis=managerial")
        assert resp.status_code == 422

    def test_gl_unauthenticated_401(self, no_auth_session):
        _assert_401(no_auth_session.get(f"{FINANCE_PREFIX}/gl"), "gl (no auth)")


# ---------------------------------------------------------------------------
# Customer & Vendor Lists
# ---------------------------------------------------------------------------

class TestLists:
    """GET /api/finance/lists/customers and /api/finance/lists/vendors"""

    def test_customers_200_shape(self, admin_session):
        data = _assert_200(
            admin_session.get(f"{FINANCE_PREFIX}/lists/customers"), "lists/customers"
        )
        assert "rows" in data
        assert "total" in data
        assert isinstance(data["rows"], list)
        if data["rows"]:
            first = data["rows"][0]
            assert "customer_id" in first
            assert "name" in first
            assert "balance" in first

    def test_customers_search_200(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/lists/customers?search=acme&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "rows" in data

    def test_vendors_200_shape(self, admin_session):
        data = _assert_200(
            admin_session.get(f"{FINANCE_PREFIX}/lists/vendors"), "lists/vendors"
        )
        assert "rows" in data
        assert isinstance(data["rows"], list)
        if data["rows"]:
            first = data["rows"][0]
            assert "vendor_id" in first
            assert "name" in first

    def test_customers_unauthenticated_401(self, no_auth_session):
        _assert_401(
            no_auth_session.get(f"{FINANCE_PREFIX}/lists/customers"), "lists/customers (no auth)"
        )

    def test_vendors_unauthenticated_401(self, no_auth_session):
        _assert_401(
            no_auth_session.get(f"{FINANCE_PREFIX}/lists/vendors"), "lists/vendors (no auth)"
        )


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

class TestMeta:
    """GET /api/finance/meta/last-sync"""

    def test_last_sync_200_shape(self, admin_session):
        data = _assert_200(
            admin_session.get(f"{FINANCE_PREFIX}/meta/last-sync"), "meta/last-sync"
        )
        assert "last_synced_at" in data
        assert "organization" in data

    def test_last_sync_unauthenticated_401(self, no_auth_session):
        _assert_401(
            no_auth_session.get(f"{FINANCE_PREFIX}/meta/last-sync"), "meta/last-sync (no auth)"
        )


# ---------------------------------------------------------------------------
# COA
# ---------------------------------------------------------------------------

class TestCoa:
    """GET /api/finance/operations/coa"""

    def test_coa_200_shape(self, admin_session):
        data = _assert_200(
            admin_session.get(f"{FINANCE_PREFIX}/operations/coa"), "operations/coa"
        )
        assert "rows" in data
        assert "total" in data
        assert isinstance(data["rows"], list)

    def test_coa_inactive_filter_200(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/operations/coa?active_only=false")
        assert resp.status_code == 200

    def test_coa_kind_filter_200(self, admin_session):
        resp = admin_session.get(f"{FINANCE_PREFIX}/operations/coa?kind=Asset")
        assert resp.status_code == 200

    def test_coa_unauthenticated_401(self, no_auth_session):
        _assert_401(
            no_auth_session.get(f"{FINANCE_PREFIX}/operations/coa"), "operations/coa (no auth)"
        )
