"""Unit tests for GET /api/time/my-earnings.

Uses FastAPI TestClient + unittest.mock to avoid hitting a live database or auth
service.  Four cases as specified in the Wave 2H brief:

  1. Authenticated contractor with no time entries → empty result (zeros, empty by_status)
  2. Authenticated contractor with mixed approved/pending entries + assigned contracts
     → correct earnings calculation
  3. Unauthenticated request → 401
  4. Authenticated user without payments.view.own (and not *) → 403
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.time_my_earnings import router

# ---------------------------------------------------------------------------
# App fixture — mount the router under /api/time so paths match production.
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(router, prefix="/api/time")


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_user(user_id: str, name: str, perms: list[str]) -> SimpleNamespace:
    """Create a minimal user object compatible with has_perm expectations."""
    return SimpleNamespace(
        user_id=user_id,
        name=name,
        permissions=perms,
    )


def _async_cursor(docs: list) -> AsyncMock:
    """Return a mock that behaves like a Motor cursor chain (.sort().to_list())."""
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=docs)
    return cursor


def _async_find(docs: list) -> MagicMock:
    """Return a mock for db.<collection>.find(...) that ends in .to_list()."""
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=docs)
    return cursor


# ---------------------------------------------------------------------------
# Shared sample data
# ---------------------------------------------------------------------------

CONTRACT_ID = "ctr_abc123"
PROJECT_ID = "proj_xyz789"
USER_ID = "usr_contractor01"

SAMPLE_CONTRACT = {
    "contract_id": CONTRACT_ID,
    "name": "Acme Support Contract",
    "contract_type": "time_and_materials",
    "account_name": "Acme Corp",
    "resources": [
        {
            "user_id": USER_ID,
            "cost_rate": 100.0,
            "role": "engineer",
        }
    ],
}

SAMPLE_PROJECT = {
    "project_id": PROJECT_ID,
    "name": "Acme Phase 1",
}

# 90-minute approved entry → 1.5 h × $100 = $150 earned_approved
ENTRY_APPROVED = {
    "time_entry_id": "te_001",
    "user_id": USER_ID,
    "contract_id": CONTRACT_ID,
    "project_id": PROJECT_ID,
    "date": "2026-04-15",
    "duration": 90,       # minutes
    "status": "approved",
    "notes": "Code review session",
}

# 60-minute pending entry → 1.0 h × $100 = $100 earned_pending
ENTRY_PENDING = {
    "time_entry_id": "te_002",
    "user_id": USER_ID,
    "contract_id": CONTRACT_ID,
    "project_id": PROJECT_ID,
    "date": "2026-04-16",
    "duration": 60,
    "status": "pending",
    "notes": "Bug triage",
}

# 30-minute rejected entry → 0.5 h (excluded from earned totals)
ENTRY_REJECTED = {
    "time_entry_id": "te_003",
    "user_id": USER_ID,
    "contract_id": CONTRACT_ID,
    "project_id": PROJECT_ID,
    "date": "2026-04-17",
    "duration": 30,
    "status": "rejected",
    "notes": "Duplicate entry",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetMyEarnings:

    # ------------------------------------------------------------------
    # Case 1: Authenticated contractor — no time entries
    # ------------------------------------------------------------------

    def test_empty_result_when_no_time_entries(self):
        """Contractor with no entries gets zero totals and empty by_status lists."""
        user = _make_user(USER_ID, "Alice Contractor", ["payments.view.own"])

        mock_db = MagicMock()
        # time_entries.find().sort().to_list() → []
        mock_db.time_entries.find.return_value = _async_cursor([])

        with (
            patch("routes.time_my_earnings.get_current_user", return_value=user),
            patch("routes.time_my_earnings.has_perm", side_effect=lambda u, p: p in u.permissions),
            patch("routes.time_my_earnings.get_db", return_value=mock_db),
            patch("routes.time_my_earnings.require_ai_permission", return_value=lambda: None),
        ):
            client = TestClient(app)
            resp = client.get(
                "/api/time/my-earnings",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["user_id"] == USER_ID
        assert body["user_name"] == "Alice Contractor"

        summary = body["summary"]
        assert summary["total_hours_logged"] == 0.0
        assert summary["total_earned_approved"] == 0.0
        assert summary["total_earned_pending"] == 0.0
        assert summary["total_earned"] == 0.0

        by_status = body["entries_by_status"]
        assert by_status["pending"] == []
        assert by_status["approved"] == []
        assert by_status["rejected"] == []

        assert body["assigned_contracts"] == []

    # ------------------------------------------------------------------
    # Case 2: Mixed entries + assigned contracts → correct calculations
    # ------------------------------------------------------------------

    def test_earnings_calculation_with_mixed_entries(self):
        """
        Approved (90 min) + pending (60 min) + rejected (30 min) entries
        across a single contract at $100/h should produce:

          total_hours_logged    = 1.5 + 1.0 + 0.5 = 3.0
          total_earned_approved = 1.5 × 100 = 150.0
          total_earned_pending  = 1.0 × 100 = 100.0
          total_earned          = 250.0

        Rejected entries appear in entries_by_status but NOT in totals.
        assigned_contracts should list Acme Support Contract.
        """
        user = _make_user(USER_ID, "Alice Contractor", ["payments.view.own"])
        entries = [ENTRY_APPROVED, ENTRY_PENDING, ENTRY_REJECTED]

        mock_db = MagicMock()
        mock_db.time_entries.find.return_value = _async_cursor(entries)
        mock_db.contracts.find.return_value = _async_find([SAMPLE_CONTRACT])
        mock_db.projects.find.return_value = _async_find([SAMPLE_PROJECT])

        with (
            patch("routes.time_my_earnings.get_current_user", return_value=user),
            patch("routes.time_my_earnings.has_perm", side_effect=lambda u, p: p in u.permissions),
            patch("routes.time_my_earnings.get_db", return_value=mock_db),
            patch("routes.time_my_earnings.require_ai_permission", return_value=lambda: None),
        ):
            client = TestClient(app)
            resp = client.get(
                "/api/time/my-earnings",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()

        summary = body["summary"]
        assert summary["total_hours_logged"] == pytest.approx(3.0)
        assert summary["total_earned_approved"] == pytest.approx(150.0)
        assert summary["total_earned_pending"] == pytest.approx(100.0)
        assert summary["total_earned"] == pytest.approx(250.0)

        by_status = body["entries_by_status"]
        assert len(by_status["approved"]) == 1
        assert len(by_status["pending"]) == 1
        assert len(by_status["rejected"]) == 1

        # Spot-check a detail shape
        approved_entry = by_status["approved"][0]
        assert approved_entry["time_entry_id"] == "te_001"
        assert approved_entry["duration_hours"] == pytest.approx(1.5)
        assert approved_entry["cost_rate"] == pytest.approx(100.0)
        assert approved_entry["earned"] == pytest.approx(150.0)
        assert approved_entry["project_name"] == "Acme Phase 1"
        assert approved_entry["contract_name"] == "Acme Support Contract"

        # assigned_contracts
        contracts = body["assigned_contracts"]
        assert len(contracts) == 1
        assert contracts[0]["contract_id"] == CONTRACT_ID
        assert contracts[0]["contract_name"] == "Acme Support Contract"
        assert contracts[0]["cost_rate"] == 100.0
        assert contracts[0]["role"] == "engineer"

    # ------------------------------------------------------------------
    # Case 3: Unauthenticated → 401
    # ------------------------------------------------------------------

    def test_unauthenticated_returns_401(self):
        """Request without a valid user token should be rejected with 401."""

        def _raise_401():
            from fastapi import HTTPException as _HTTPException
            raise _HTTPException(status_code=401, detail="Not authenticated")

        with patch(
            "routes.time_my_earnings.get_current_user",
            side_effect=_raise_401,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/time/my-earnings")

        assert resp.status_code == 401

    # ------------------------------------------------------------------
    # Case 4: Authenticated but missing payments.view.own → 403
    # ------------------------------------------------------------------

    def test_missing_permission_returns_403(self):
        """
        A user who is authenticated but holds neither payments.view.own
        nor * should receive 403 before any DB read.
        """
        user = _make_user("usr_noperm", "Bob NoPerm", ["some.other.perm"])

        mock_db = MagicMock()

        with (
            patch("routes.time_my_earnings.get_current_user", return_value=user),
            patch(
                "routes.time_my_earnings.has_perm",
                side_effect=lambda u, p: p in u.permissions,
            ),
            patch("routes.time_my_earnings.get_db", return_value=mock_db),
            patch("routes.time_my_earnings.require_ai_permission", return_value=lambda: None),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(
                "/api/time/my-earnings",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 403
        # DB must not have been touched
        mock_db.time_entries.find.assert_not_called()
