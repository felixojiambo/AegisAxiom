"""Tests for finance draft auto-generation endpoints + service.

File: backend/routes/finance_draft_autogen.py
Service: backend/services/finance_draft_autogen.py

Coverage:
    Endpoints (via FastAPI TestClient):
        - AR: empty time entries -> 0 drafts, no error
        - AR: 5 approved entries on one contract -> 1 draft, summed amount, 5 line items
        - AR: dry_run=true does NOT insert to DB
        - AR: 403 when permission missing
        - AP: empty time entries -> 0 drafts, no error
        - AP: 3 approved entries for one contractor -> 1 draft, rolled-up line items
        - AP: dry_run=true does NOT insert to DB
        - AP: 403 when permission missing
        - Both: period_start > period_end -> 400

    Service (unit, direct calls to build_ar_drafts / build_ap_drafts):
        - Deduplication: second call returns skipped, no duplicate inserted
        - Correct field mapping on draft doc
"""
from __future__ import annotations

import os

# auth_helper raises ValueError at import time if JWT_SECRET is absent.
# Set a test-only value before any route or auth module is imported.
os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-only")

import pytest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

from fake_user_repo import patched_load_user_map, set_current_user_map
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

def _make_user(user_id="u_finance", email="finance@aegisaxiom.io", permissions=None):
    perms = permissions if permissions is not None else [
        "finance.draft.create",
        "finance.draft.view",
    ]
    return SimpleNamespace(
        user_id=user_id,
        email=email,
        role="finance_admin",
        permissions=perms,
    )


@asynccontextmanager
async def _fake_envelope(*args, **kwargs):
    ctx = MagicMock()
    ctx.set_after = MagicMock()
    yield ctx


def _build_app(fake_user):
    from routes.finance_draft_autogen import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/finance")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return app


def _make_entry(
    entry_id="te_001",
    user_id="u_cont_1",
    user_name="Alice",
    contract_id="cont_abc",
    date="2026-04-15",
    hours=8.0,
    cost_rate=75.0,
    status="pm_approved",
    is_billable=True,
) -> dict:
    return {
        "time_entry_id": entry_id,
        "entry_id": entry_id,  # legacy ar_invoices used this field name
        "user_id": user_id,
        "user_name": user_name,
        "contract_id": contract_id,
        "date": date,
        "hours": hours,
        "duration_hours": hours,
        "cost_rate": cost_rate,
        "status": status,
        "is_billable": is_billable,
        "notes": "Regular hours",
        "project_id": None,
        "is_test_data": False,
    }


def _make_contract(contract_id="cont_abc", account_id="acc_xyz", account_name="Acme Corp") -> dict:
    return {
        "contract_id": contract_id,
        "account_id": account_id,
        "account_name": account_name,
        "name": "Acme Contract",
        "resource_types": [
            {
                "name": "Senior Dev",
                "billable_rate": 150.0,
                "assigned_users": [{"user_id": "u_cont_1"}],
            }
        ],
    }


def _make_db_with_ar(time_entries, contract=None, existing_draft=None):
    """Build a MagicMock DB for AR tests."""
    db = MagicMock()
    db.__getitem__ = MagicMock(side_effect=_make_collection_selector({
        "time_entries": time_entries,
        "contracts": [contract] if contract else [],
        "finance_drafts": [existing_draft] if existing_draft else [],
    }))
    return db


@pytest.fixture(autouse=True)
def _users_on_postgres():
    """Phase 2a completion: the AP builder prefetches users from Postgres via
    `_load_user_map`, not `db["users"]`. Serve the docs each test declares."""
    with patched_load_user_map():
        yield


def _make_db_with_ap(time_entries, users=None, businesses=None, existing_draft=None):
    """Build a MagicMock DB for AP tests."""
    set_current_user_map(users)
    db = MagicMock()
    db.__getitem__ = MagicMock(side_effect=_make_collection_selector({
        "time_entries": time_entries,
        "users": users or [],
        "contractor_businesses": businesses or [],
        "projects": [],
        "finance_drafts": [existing_draft] if existing_draft else [],
    }))
    return db


def _make_collection_selector(collections: Dict[str, List[dict]]):
    """Return a side_effect function for db[collection_name]."""
    def _select(name):
        docs = collections.get(name, [])
        coll = MagicMock()

        # find() returns an async cursor that yields docs
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=docs)
        cursor.find = MagicMock(return_value=cursor)
        coll.find = MagicMock(return_value=cursor)

        # find_one: return first matching doc or None (simplified)
        # Tests that need specific matching override coll.find_one directly
        coll.find_one = AsyncMock(return_value=docs[0] if docs else None)
        coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id="fake_oid"))
        return coll

    return _select


# ---------------------------------------------------------------------------
# AR endpoint tests
# ---------------------------------------------------------------------------

class TestAutoGenerateAREndpoint:
    def _url(self):
        return "/api/finance/drafts/auto-generate-ar"

    def _valid_payload(self, **kwargs):
        base = {"period_start": "2026-04-01", "period_end": "2026-04-30"}
        base.update(kwargs)
        return base

    def test_empty_entries_returns_zero_drafts(self):
        user = _make_user()
        app = _build_app(user)

        with patch("routes.finance_draft_autogen.get_db", return_value=MagicMock()), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_draft_autogen.build_ar_drafts",
                   new=AsyncMock(return_value=([], [{"reason": "no_approved_billable_entries", "period": "2026-04-01/2026-04-30"}], []))):
            client = TestClient(app)
            resp = client.post(self._url(), json=self._valid_payload())

        assert resp.status_code == 200
        data = resp.json()
        assert data["drafts_created"] == 0
        assert data["drafts"] == []
        assert len(data["skipped"]) >= 1

    def test_five_entries_one_contract_creates_one_draft(self):
        user = _make_user()
        entries = [
            _make_entry(entry_id=f"te_{i:03d}", hours=8.0)
            for i in range(5)
        ]
        contract = _make_contract()
        app = _build_app(user)

        # We patch the service functions directly to control output
        expected_draft = {
            "draft_id": "fcc_draft_abc",
            "kind": "ar_invoice",
            "counterparty": "Acme Corp",
            "counterparty_id": "acc_xyz",
            "amount": 6000.0,
            "line_items": entries,  # 5 items (we return them as-is for this test)
            "sync_status": "Draft",
            "source_origin": "auto_time_entries",
            "source_contract_id": "cont_abc",
            "source_period_start": "2026-04-01",
            "source_period_end": "2026-04-30",
        }

        with patch("routes.finance_draft_autogen.get_db", return_value=MagicMock()), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_draft_autogen.build_ar_drafts",
                   new=AsyncMock(return_value=([expected_draft], [], []))), \
             patch("routes.finance_draft_autogen.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.post(self._url(), json=self._valid_payload())

        assert resp.status_code == 200
        data = resp.json()
        assert data["drafts_created"] == 1
        assert len(data["drafts"]) == 1
        assert data["drafts"][0]["counterparty"] == "Acme Corp"
        assert data["drafts"][0]["amount"] == 6000.0

    def test_five_entries_summed_amount_and_line_count(self):
        """Verify the line_count in summary matches actual line items."""
        user = _make_user()
        app = _build_app(user)

        # 5 line items, each 8h @ $150 = $1200 each, total $6000
        five_line_items = [
            {"description": f"Alice: day {i}", "quantity": 8.0, "rate": 150.0, "amount": 1200.0}
            for i in range(5)
        ]
        expected_draft = {
            "draft_id": "fcc_draft_xyz",
            "counterparty": "Acme Corp",
            "amount": 6000.0,
            "line_items": five_line_items,
        }

        with patch("routes.finance_draft_autogen.get_db", return_value=MagicMock()), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_draft_autogen.build_ar_drafts",
                   new=AsyncMock(return_value=([expected_draft], [], []))), \
             patch("routes.finance_draft_autogen.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.post(self._url(), json=self._valid_payload())

        assert resp.status_code == 200
        data = resp.json()
        assert data["drafts"][0]["line_count"] == 5
        assert data["drafts"][0]["amount"] == 6000.0

    def test_dry_run_does_not_call_crud_envelope(self):
        user = _make_user()
        app = _build_app(user)

        draft_candidate = {
            "draft_id": "fcc_draft_dry",
            "counterparty": "Acme",
            "amount": 1500.0,
            "line_items": [{"description": "x", "quantity": 1, "rate": 1500, "amount": 1500}],
        }

        mock_envelope = MagicMock()

        with patch("routes.finance_draft_autogen.get_db", return_value=MagicMock()), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_draft_autogen.build_ar_drafts",
                   new=AsyncMock(return_value=([draft_candidate], [], []))), \
             patch("routes.finance_draft_autogen.crud_envelope", side_effect=_fake_envelope) as mock_env:
            client = TestClient(app)
            resp = client.post(self._url(), json=self._valid_payload(dry_run=True))

        assert resp.status_code == 200
        data = resp.json()
        assert data["drafts_created"] == 0   # dry_run suppresses count
        assert data["dry_run"] is True
        # crud_envelope must NOT have been called (no writes)
        mock_env.assert_not_called()

    def test_missing_permission_returns_403(self):
        user = _make_user(permissions=[])
        app = _build_app(user)

        with patch("routes.finance_draft_autogen.get_db", return_value=MagicMock()), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post(self._url(), json=self._valid_payload())

        assert resp.status_code == 403

    def test_period_start_after_period_end_returns_400(self):
        user = _make_user()
        app = _build_app(user)

        with patch("routes.finance_draft_autogen.get_db", return_value=MagicMock()), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post(self._url(), json={
                "period_start": "2026-04-30",
                "period_end": "2026-04-01",
            })

        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# AP endpoint tests
# ---------------------------------------------------------------------------

class TestAutoGenerateAPEndpoint:
    def _url(self):
        return "/api/finance/drafts/auto-generate-ap"

    def _valid_payload(self, **kwargs):
        base = {"period_start": "2026-04-01", "period_end": "2026-04-30"}
        base.update(kwargs)
        return base

    def test_empty_entries_returns_zero_drafts(self):
        user = _make_user()
        db = MagicMock()
        te_coll = MagicMock()
        te_cursor = MagicMock()
        te_cursor.to_list = AsyncMock(return_value=[])
        te_coll.find = MagicMock(return_value=te_cursor)
        db.__getitem__ = MagicMock(return_value=te_coll)
        app = _build_app(user)

        with patch("routes.finance_draft_autogen.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_draft_autogen.build_ap_drafts",
                   new=AsyncMock(return_value=([], [{"reason": "no_approved_entries", "period": "2026-04-01/2026-04-30"}], []))):
            client = TestClient(app)
            resp = client.post(self._url(), json=self._valid_payload())

        assert resp.status_code == 200
        data = resp.json()
        assert data["drafts_created"] == 0
        assert data["drafts"] == []
        assert len(data["skipped"]) >= 1

    def test_contractor_entries_create_one_draft(self):
        user = _make_user()
        app = _build_app(user)

        expected_draft = {
            "draft_id": "fcc_draft_ap1",
            "kind": "ap_bill",
            "counterparty": "Bob's LLC",
            "counterparty_id": "biz_001",
            "amount": 1800.0,
            "line_items": [
                {"description": "Bob Smith — Project Alpha", "quantity": 24.0, "rate": 75.0, "amount": 1800.0}
            ],
            "sync_status": "Draft",
            "source_origin": "auto_time_entries",
            "source_contractor_id": "biz_001",
        }

        with patch("routes.finance_draft_autogen.get_db", return_value=MagicMock()), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_draft_autogen.build_ap_drafts",
                   new=AsyncMock(return_value=([expected_draft], [], []))), \
             patch("routes.finance_draft_autogen.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.post(self._url(), json=self._valid_payload())

        assert resp.status_code == 200
        data = resp.json()
        assert data["drafts_created"] == 1
        assert data["drafts"][0]["counterparty"] == "Bob's LLC"

    def test_dry_run_does_not_write(self):
        user = _make_user()
        app = _build_app(user)

        draft_candidate = {
            "draft_id": "fcc_draft_ap_dry",
            "counterparty": "Bob",
            "amount": 900.0,
            "line_items": [{"description": "x", "quantity": 1, "rate": 900, "amount": 900}],
        }

        with patch("routes.finance_draft_autogen.get_db", return_value=MagicMock()), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_draft_autogen.build_ap_drafts",
                   new=AsyncMock(return_value=([draft_candidate], [], []))), \
             patch("routes.finance_draft_autogen.crud_envelope", side_effect=_fake_envelope) as mock_env:
            client = TestClient(app)
            resp = client.post(self._url(), json=self._valid_payload(dry_run=True))

        assert resp.status_code == 200
        data = resp.json()
        assert data["drafts_created"] == 0
        assert data["dry_run"] is True
        mock_env.assert_not_called()

    def test_missing_permission_returns_403(self):
        user = _make_user(permissions=[])
        app = _build_app(user)

        with patch("routes.finance_draft_autogen.get_db", return_value=MagicMock()), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post(self._url(), json=self._valid_payload())

        assert resp.status_code == 403

    def test_period_start_after_period_end_returns_400(self):
        user = _make_user()
        app = _build_app(user)

        with patch("routes.finance_draft_autogen.get_db", return_value=MagicMock()), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post(self._url(), json={
                "period_start": "2026-05-01",
                "period_end": "2026-04-01",
            })

        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Service unit tests (direct calls to build_ar_drafts / build_ap_drafts)
# ---------------------------------------------------------------------------

class TestBuildARDraftsService:
    """Tests that patch the DB directly and call the service functions."""

    def _make_mock_db(self, time_entries, contract=None, existing_draft=None):
        db = MagicMock()

        te_coll = MagicMock()
        te_cursor = MagicMock()
        te_cursor.to_list = AsyncMock(return_value=time_entries)
        te_coll.find = MagicMock(return_value=te_cursor)

        contract_coll = MagicMock()
        contract_coll.find_one = AsyncMock(return_value=contract)

        drafts_coll = MagicMock()
        drafts_coll.find_one = AsyncMock(return_value=existing_draft)
        drafts_coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id="oid"))

        def _getitem(name):
            if name == "time_entries":
                return te_coll
            if name == "contracts":
                return contract_coll
            if name == "finance_drafts":
                return drafts_coll
            m = MagicMock()
            m.find_one = AsyncMock(return_value=None)
            return m

        db.__getitem__ = MagicMock(side_effect=_getitem)
        return db, te_coll, contract_coll, drafts_coll

    @pytest.mark.asyncio
    async def test_empty_time_entries(self):
        from services.finance_draft_autogen import build_ar_drafts

        db, _, _, _ = self._make_mock_db(time_entries=[])
        drafts, skipped, warnings = await build_ar_drafts(
            db=db,
            period_start="2026-04-01",
            period_end="2026-04-30",
            contract_id_filter=None,
            created_by="u_test",
            dry_run=False,
        )
        assert drafts == []
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "no_approved_billable_entries"

    @pytest.mark.asyncio
    async def test_draft_fields_are_correct(self):
        from services.finance_draft_autogen import build_ar_drafts

        entries = [_make_entry(hours=8.0)]
        contract = _make_contract()
        db, _, _, drafts_coll = self._make_mock_db(
            time_entries=entries, contract=contract, existing_draft=None
        )
        drafts_coll.find_one = AsyncMock(return_value=None)

        drafts, skipped, warnings = await build_ar_drafts(
            db=db,
            period_start="2026-04-01",
            period_end="2026-04-30",
            contract_id_filter=None,
            created_by="u_finance",
            dry_run=False,
        )

        assert len(drafts) == 1
        d = drafts[0]
        assert d["kind"] == "ar_invoice"
        assert d["sync_status"] == "Draft"
        assert d["source_origin"] == "auto_time_entries"
        assert d["source_contract_id"] == "cont_abc"
        assert d["counterparty"] == "Acme Corp"
        assert d["counterparty_id"] == "acc_xyz"
        # 8h @ $150 = $1200
        assert d["amount"] == 1200.0
        assert len(d["line_items"]) == 1
        assert d["draft_id"].startswith("fcc_draft_")

    @pytest.mark.asyncio
    async def test_deduplication_skips_on_second_run(self):
        from services.finance_draft_autogen import build_ar_drafts

        entries = [_make_entry(hours=8.0)]
        contract = _make_contract()

        # First run: no existing draft
        db, _, _, drafts_coll = self._make_mock_db(
            time_entries=entries, contract=contract, existing_draft=None
        )
        drafts_coll.find_one = AsyncMock(return_value=None)
        drafts, skipped, _ = await build_ar_drafts(
            db=db, period_start="2026-04-01", period_end="2026-04-30",
            contract_id_filter=None, created_by="u_test", dry_run=False,
        )
        assert len(drafts) == 1
        assert drafts_coll.insert_one.called

        # Second run: existing draft is now found
        existing_draft = {"draft_id": "fcc_draft_existing", "source_origin": "auto_time_entries"}
        db2, _, _, drafts_coll2 = self._make_mock_db(
            time_entries=entries, contract=contract, existing_draft=existing_draft
        )
        drafts_coll2.find_one = AsyncMock(return_value=existing_draft)
        drafts2, skipped2, _ = await build_ar_drafts(
            db=db2, period_start="2026-04-01", period_end="2026-04-30",
            contract_id_filter=None, created_by="u_test", dry_run=False,
        )
        assert drafts2 == []
        assert len(skipped2) == 1
        assert skipped2[0]["reason"] == "draft_already_exists"
        drafts_coll2.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_does_not_insert(self):
        from services.finance_draft_autogen import build_ar_drafts

        entries = [_make_entry(hours=8.0)]
        contract = _make_contract()
        db, _, _, drafts_coll = self._make_mock_db(
            time_entries=entries, contract=contract, existing_draft=None
        )
        drafts_coll.find_one = AsyncMock(return_value=None)

        drafts, _, _ = await build_ar_drafts(
            db=db, period_start="2026-04-01", period_end="2026-04-30",
            contract_id_filter=None, created_by="u_test", dry_run=True,
        )

        assert len(drafts) == 1
        drafts_coll.insert_one.assert_not_called()


class TestBuildAPDraftsService:
    """Tests for build_ap_drafts service."""

    def _make_ap_db(self, time_entries, user=None, business=None, existing_draft=None):
        # Phase 2a completion: the builder prefetches users from Postgres, so the
        # single `user` doc these tests declare is published to the prefetch fake
        # as well as to the (now unused) Mongo users collection below.
        set_current_user_map([user] if user else [])
        db = MagicMock()

        te_coll = MagicMock()
        te_cursor = MagicMock()
        te_cursor.to_list = AsyncMock(return_value=time_entries)
        te_coll.find = MagicMock(return_value=te_cursor)

        users_coll = MagicMock()
        users_coll.find_one = AsyncMock(return_value=user)

        biz_coll = MagicMock()
        biz_coll.find_one = AsyncMock(return_value=business)

        projects_coll = MagicMock()
        projects_coll.find_one = AsyncMock(return_value=None)

        drafts_coll = MagicMock()
        drafts_coll.find_one = AsyncMock(return_value=existing_draft)
        drafts_coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id="oid"))

        def _getitem(name):
            if name == "time_entries":
                return te_coll
            if name == "users":
                return users_coll
            if name == "contractor_businesses":
                return biz_coll
            if name == "projects":
                return projects_coll
            if name == "finance_drafts":
                return drafts_coll
            m = MagicMock()
            m.find_one = AsyncMock(return_value=None)
            return m

        db.__getitem__ = MagicMock(side_effect=_getitem)
        return db, drafts_coll

    @pytest.mark.asyncio
    async def test_empty_entries(self):
        from services.finance_draft_autogen import build_ap_drafts

        db = MagicMock()
        te_coll = MagicMock()
        te_cursor = MagicMock()
        te_cursor.to_list = AsyncMock(return_value=[])
        te_coll.find = MagicMock(return_value=te_cursor)
        db.__getitem__ = MagicMock(return_value=te_coll)

        drafts, skipped, _ = await build_ap_drafts(
            db=db, period_start="2026-04-01", period_end="2026-04-30",
            contractor_user_id_filter=None, created_by="u_test", dry_run=False,
        )
        assert drafts == []
        assert skipped[0]["reason"] == "no_approved_entries"

    @pytest.mark.asyncio
    async def test_ap_draft_fields_correct(self):
        from services.finance_draft_autogen import build_ap_drafts

        user = {
            "user_id": "u_cont_1",
            "name": "Bob Smith",
            "contractor_business_id": None,
        }
        entry = {
            "user_id": "u_cont_1",
            "duration_hours": 8.0,
            "cost_rate": 75.0,
            "date": "2026-04-10",
            "status": "approved",
            "project_id": None,
            "time_entry_id": "te_001",
        }
        db, drafts_coll = self._make_ap_db(
            time_entries=[entry], user=user, existing_draft=None
        )
        drafts_coll.find_one = AsyncMock(return_value=None)

        drafts, skipped, _ = await build_ap_drafts(
            db=db, period_start="2026-04-01", period_end="2026-04-30",
            contractor_user_id_filter=None, created_by="u_finance", dry_run=False,
        )

        assert len(drafts) == 1
        d = drafts[0]
        assert d["kind"] == "ap_bill"
        assert d["sync_status"] == "Draft"
        assert d["source_origin"] == "auto_time_entries"
        assert d["counterparty"] == "Bob Smith"
        assert d["source_contractor_id"] == "u_cont_1"
        assert d["amount"] == 600.0  # 8h @ 75
        assert d["draft_id"].startswith("fcc_draft_")

    @pytest.mark.asyncio
    async def test_deduplication_skips_on_second_run(self):
        from services.finance_draft_autogen import build_ap_drafts

        user = {"user_id": "u_cont_1", "name": "Bob", "contractor_business_id": None}
        entry = {
            "user_id": "u_cont_1", "duration_hours": 8.0, "cost_rate": 50.0,
            "date": "2026-04-10", "status": "approved", "project_id": None,
            "time_entry_id": "te_001",
        }

        # Run 1: no existing draft
        db, drafts_coll = self._make_ap_db(time_entries=[entry], user=user, existing_draft=None)
        drafts_coll.find_one = AsyncMock(return_value=None)
        drafts1, _, _ = await build_ap_drafts(
            db=db, period_start="2026-04-01", period_end="2026-04-30",
            contractor_user_id_filter=None, created_by="u_t", dry_run=False,
        )
        assert len(drafts1) == 1

        # Run 2: existing draft present
        existing = {"draft_id": "fcc_draft_exists", "source_origin": "auto_time_entries"}
        db2, drafts_coll2 = self._make_ap_db(time_entries=[entry], user=user, existing_draft=existing)
        drafts_coll2.find_one = AsyncMock(return_value=existing)
        drafts2, skipped2, _ = await build_ap_drafts(
            db=db2, period_start="2026-04-01", period_end="2026-04-30",
            contractor_user_id_filter=None, created_by="u_t", dry_run=False,
        )
        assert drafts2 == []
        assert skipped2[0]["reason"] == "draft_already_exists"
        drafts_coll2.insert_one.assert_not_called()
