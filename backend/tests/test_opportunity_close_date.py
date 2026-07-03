"""Locks the Opportunity close_date end-to-end contract.

The Opportunity Management close-date alert (see
``frontend/src/components/OpportunityManagement.js``) reads only the
real close-date column on the payload. These tests lock the backend
half of that contract:

  1. Create persists ``close_date`` verbatim.
  2. Create with no ``close_date`` defaults to ``None`` (legacy rows
     and minimal forms still work).
  3. Update writes ``close_date`` when supplied.
  4. Update with ``close_date=None`` does NOT clobber an existing
     value (Pydantic's exclude_unset semantics — the route only
     mutates fields the caller actually supplied).
  5. ``desired_proposal_date`` never auto-populates ``close_date``.
  6. GET returns the persisted ``close_date`` verbatim.
  7. Existing legacy rows with no ``close_date`` field still load.

All tests exercise the route functions directly with AsyncMock-shaped
fakes so the suite runs without Mongo. Synthetic identifiers only.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


_OPP_ID = "opp_synth_close_date_001"
_ACCOUNT_ID = "acct_synth_close_date_001"
_FUTURE_DATE = "2099-08-15"
_OTHER_DATE = "2099-09-20"


def _user(*perms: str, role: str = "sales_rep") -> SimpleNamespace:
    return SimpleNamespace(
        user_id="u_synth_close_date_tester",
        email="close-date-tester@aegisaxiom.test",
        name="Close Date Tester",
        role=role,
        permissions=list(perms),
    )


class _OpportunitiesCollection:
    """Minimal in-memory replacement for ``db.opportunities``."""

    def __init__(self, seed: list[dict] | None = None) -> None:
        self.docs: list[dict] = list(seed or [])

    async def insert_one(self, doc: dict) -> Any:
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id="fake_oid")

    async def find_one(self, query: dict, projection: dict | None = None) -> dict | None:
        for d in self.docs:
            if _matches(d, query):
                return dict(d)
        return None

    async def update_one(self, query: dict, update: dict):
        for d in self.docs:
            if _matches(d, query):
                d.update(update.get("$set", {}))
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


def _matches(doc: dict, query: dict) -> bool:
    """Subset matcher covering the equality / $ne / $in / $exists shapes the
    opportunities route uses. Soft-delete filters land here as $exists/$ne,
    so honour those minimally instead of treating them as literal values."""
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$exists" in expected:
                exists = expected["$exists"]
                if exists and key not in doc:
                    return False
                if not exists and key in doc:
                    return False
                continue
            if "$ne" in expected:
                if actual == expected["$ne"]:
                    return False
                continue
            if "$in" in expected:
                if actual not in expected["$in"]:
                    return False
                continue
            # Unknown operator — treat as no-op so the doc still matches
            # by other keys; the test fixtures don't exercise it.
            continue
        if actual != expected:
            return False
    return True


def _make_db(*, seed: list[dict] | None = None) -> SimpleNamespace:
    db = SimpleNamespace()
    db.opportunities = _OpportunitiesCollection(seed)
    db.accounts = SimpleNamespace(
        find_one=AsyncMock(
            return_value={"account_id": _ACCOUNT_ID, "name": "Synth Account"}
        )
    )
    db.contacts = SimpleNamespace(find_one=AsyncMock(return_value=None))
    db.users = SimpleNamespace(find_one=AsyncMock(return_value=None))
    return db


def _seed_opportunity(*, close_date: str | None = None, desired_proposal_date: str | None = None) -> dict:
    return {
        "opportunity_id": _OPP_ID,
        "tracking_number": "OPP-2099-000001",
        "name": "Synth Opportunity",
        "account_id": _ACCOUNT_ID,
        "account_name": "Synth Account",
        "description": None,
        "desired_proposal_date": desired_proposal_date,
        # Some seeds intentionally omit close_date (legacy rows).
        **({"close_date": close_date} if close_date is not None else {}),
        "priority": "medium",
        "amount": 1000.0,
        "stage": "proposal_development",
        "commission_payment_method": None,
        "lines_of_business": ["professional_services"],
        "opportunity_owner_id": "u_synth_close_date_tester",
        "opportunity_owner_name": "Close Date Tester",
        "is_test_data": True,
    }


# ---------------------------------------------------------------------------
# Model field presence — locks the Pydantic schema
# ---------------------------------------------------------------------------


def test_opportunity_models_carry_close_date():
    from models import Opportunity, OpportunityCreate, OpportunityUpdate

    assert "close_date" in Opportunity.model_fields
    assert "close_date" in OpportunityCreate.model_fields
    assert "close_date" in OpportunityUpdate.model_fields
    # The field is independent of desired_proposal_date.
    assert "desired_proposal_date" in Opportunity.model_fields


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_persists_close_date(monkeypatch):
    from routes import opportunities as opp_routes
    from models import OpportunityCreate

    db = _make_db()
    monkeypatch.setattr(opp_routes, "get_db", lambda: db)
    # Skip the counter trip — the test doesn't care about tracking_number.
    monkeypatch.setattr(
        opp_routes, "generate_opportunity_tracking_number",
        AsyncMock(return_value="OPP-2099-000001"),
    )
    # The route emits a domain event; stub it so no broker is required.
    monkeypatch.setattr(opp_routes, "emit_event", AsyncMock(return_value=None))

    body = OpportunityCreate(
        name="Synth Opp",
        account_id=_ACCOUNT_ID,
        amount=5000.0,
        close_date=_FUTURE_DATE,
        is_test_data=True,
    )
    result = await opp_routes.create_opportunity(
        data=body, current_user=_user("opportunities.create"),
    )
    assert result.close_date == _FUTURE_DATE
    # Persisted on the underlying doc too.
    persisted = await db.opportunities.find_one({"opportunity_id": result.opportunity_id})
    assert persisted["close_date"] == _FUTURE_DATE


@pytest.mark.asyncio
async def test_create_without_close_date_defaults_to_none(monkeypatch):
    from routes import opportunities as opp_routes
    from models import OpportunityCreate

    db = _make_db()
    monkeypatch.setattr(opp_routes, "get_db", lambda: db)
    monkeypatch.setattr(
        opp_routes, "generate_opportunity_tracking_number",
        AsyncMock(return_value="OPP-2099-000002"),
    )
    monkeypatch.setattr(opp_routes, "emit_event", AsyncMock(return_value=None))

    body = OpportunityCreate(
        name="Synth Opp No Close Date",
        account_id=_ACCOUNT_ID,
        amount=1.0,
        is_test_data=True,
    )
    result = await opp_routes.create_opportunity(
        data=body, current_user=_user("opportunities.create"),
    )
    assert result.close_date is None


@pytest.mark.asyncio
async def test_create_does_not_copy_desired_proposal_date_into_close_date(monkeypatch):
    """Regression lock for the prior alert-helper bug. Even when the
    caller supplies desired_proposal_date, close_date must remain
    independently null until set explicitly."""
    from routes import opportunities as opp_routes
    from models import OpportunityCreate

    db = _make_db()
    monkeypatch.setattr(opp_routes, "get_db", lambda: db)
    monkeypatch.setattr(
        opp_routes, "generate_opportunity_tracking_number",
        AsyncMock(return_value="OPP-2099-000003"),
    )
    monkeypatch.setattr(opp_routes, "emit_event", AsyncMock(return_value=None))

    body = OpportunityCreate(
        name="Synth Opp DP Only",
        account_id=_ACCOUNT_ID,
        desired_proposal_date=_FUTURE_DATE,
        is_test_data=True,
    )
    result = await opp_routes.create_opportunity(
        data=body, current_user=_user("opportunities.create"),
    )
    assert result.desired_proposal_date == _FUTURE_DATE
    assert result.close_date is None


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_sets_close_date(monkeypatch):
    from routes import opportunities as opp_routes
    from models import OpportunityUpdate

    db = _make_db(seed=[_seed_opportunity()])
    monkeypatch.setattr(opp_routes, "get_db", lambda: db)
    monkeypatch.setattr(opp_routes, "emit_event", AsyncMock(return_value=None))

    updated = await opp_routes.update_opportunity(
        opportunity_id=_OPP_ID,
        data=OpportunityUpdate(close_date=_FUTURE_DATE),
        current_user=_user("opportunities.edit.all"),
    )
    assert updated["close_date"] == _FUTURE_DATE


@pytest.mark.asyncio
async def test_update_omitting_close_date_keeps_existing_value(monkeypatch):
    """``OpportunityUpdate(...)`` without ``close_date`` must NOT clear
    a persisted value — only fields the caller actually supplied get
    written. This matches Pydantic's exclude_unset semantics that the
    route relies on for partial updates."""
    from routes import opportunities as opp_routes
    from models import OpportunityUpdate

    db = _make_db(seed=[_seed_opportunity(close_date=_FUTURE_DATE)])
    monkeypatch.setattr(opp_routes, "get_db", lambda: db)
    monkeypatch.setattr(opp_routes, "emit_event", AsyncMock(return_value=None))

    updated = await opp_routes.update_opportunity(
        opportunity_id=_OPP_ID,
        data=OpportunityUpdate(amount=99999.0),
        current_user=_user("opportunities.edit.all"),
    )
    assert updated["amount"] == 99999.0
    assert updated["close_date"] == _FUTURE_DATE


@pytest.mark.asyncio
async def test_update_can_clear_close_date_with_empty_string(monkeypatch):
    """The UI sends an empty string when the user removes the date.
    The route normalises that to ``None`` on the stored doc so the
    alert helper's ``empty-string returns normal`` test in the
    frontend suite matches the persisted shape."""
    from routes import opportunities as opp_routes
    from models import OpportunityUpdate

    db = _make_db(seed=[_seed_opportunity(close_date=_FUTURE_DATE)])
    monkeypatch.setattr(opp_routes, "get_db", lambda: db)
    monkeypatch.setattr(opp_routes, "emit_event", AsyncMock(return_value=None))

    updated = await opp_routes.update_opportunity(
        opportunity_id=_OPP_ID,
        data=OpportunityUpdate(close_date=""),
        current_user=_user("opportunities.edit.all"),
    )
    assert updated["close_date"] is None


@pytest.mark.asyncio
async def test_update_close_date_does_not_touch_desired_proposal_date(monkeypatch):
    from routes import opportunities as opp_routes
    from models import OpportunityUpdate

    db = _make_db(
        seed=[_seed_opportunity(desired_proposal_date=_OTHER_DATE)],
    )
    monkeypatch.setattr(opp_routes, "get_db", lambda: db)
    monkeypatch.setattr(opp_routes, "emit_event", AsyncMock(return_value=None))

    updated = await opp_routes.update_opportunity(
        opportunity_id=_OPP_ID,
        data=OpportunityUpdate(close_date=_FUTURE_DATE),
        current_user=_user("opportunities.edit.all"),
    )
    assert updated["close_date"] == _FUTURE_DATE
    assert updated["desired_proposal_date"] == _OTHER_DATE


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_close_date(monkeypatch):
    from routes import opportunities as opp_routes

    db = _make_db(seed=[_seed_opportunity(close_date=_FUTURE_DATE)])
    monkeypatch.setattr(opp_routes, "get_db", lambda: db)

    result = await opp_routes.get_opportunity(
        opportunity_id=_OPP_ID,
        current_user=_user("opportunities.view.all"),
    )
    assert result["close_date"] == _FUTURE_DATE


@pytest.mark.asyncio
async def test_get_legacy_opportunity_without_close_date_loads(monkeypatch):
    """Backwards compatibility: rows persisted before this feature have
    no ``close_date`` key. They must still load — the helper resolves a
    missing key to ``normal`` (no alert), which is the correct UX."""
    from routes import opportunities as opp_routes

    legacy = _seed_opportunity()  # no close_date key at all
    assert "close_date" not in legacy
    db = _make_db(seed=[legacy])
    monkeypatch.setattr(opp_routes, "get_db", lambda: db)

    result = await opp_routes.get_opportunity(
        opportunity_id=_OPP_ID,
        current_user=_user("opportunities.view.all"),
    )
    # Mongo find_one will simply omit the key — the route returns the
    # doc verbatim, which means close_date is absent from the response.
    # The frontend alert helper treats absent the same as None.
    assert "close_date" not in result
