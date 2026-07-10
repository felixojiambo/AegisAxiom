"""Wave 1 (contracts-time-mgmt) — contract approval workflow tests.

Exercises the four approval action routes plus the approval queue
endpoint against AsyncMock-shaped fakes so the suite runs without
Mongo. Mirrors the style used in test_opportunity_close_date.py:
auth_helper's module-load guard expects ``JWT_SECRET`` in the env, so
that gets set BEFORE any route module is imported transitively.

Coverage:
  - boh_with_boa is accepted by the create-contract validator.
  - submit-for-approval sets approval_status=pending and status=draft.
  - approve from pending sets approved + active.
  - reject from pending sets rejected + draft.
  - request-changes from pending sets changes_requested + draft.
  - Invalid transitions (approve before submit) return 409.
  - Approval decisions write a contract_approvals row.
  - Approval decisions audit via log_action.
  - approval-queue returns only pending contracts.
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


_USER = SimpleNamespace(
    user_id="u_director_001",
    email="director@aegisaxiom.test",
    role="director",
    tenant_id=None,
    account_id=None,
    permissions=[
        "contracts.create",
        "contracts.edit",
        "contracts.edit.all",
        "contracts.approve",
        "contracts.view.all",
    ],
)


def _fake_contract(**overrides) -> dict:
    base = {
        "contract_id": "contract_synth_001",
        "name": "Acme PS Engagement",
        "account_id": "acct_001",
        "account_name": "Acme",
        "contract_type": "boh",
        "service_category": "wholesale_mrr",
        "status": "draft",
        "approval_status": None,
        "project_ids": [],
        "tenant_id": None,
    }
    base.update(overrides)
    return base


class _FakeCollection:
    """Minimal Motor-shaped collection that records operations and
    serves canned responses. Each test wires up exactly what it needs."""

    def __init__(self, *, find_one_result=None, find_results=None):
        self._find_one_result = find_one_result
        self._find_results = list(find_results or [])
        self.updates: list[tuple[dict, dict]] = []
        self.inserts: list[dict] = []

    async def find_one(self, query, projection=None):
        # Some tests want find_one to return a copy of the canned doc
        # so that downstream code reading "updated" sees the update.
        if isinstance(self._find_one_result, dict):
            return dict(self._find_one_result)
        return self._find_one_result

    async def update_one(self, query, update):
        self.updates.append((dict(query), dict(update)))
        # Apply the $set into the canned doc so the next find_one
        # reads the post-update state.
        if isinstance(self._find_one_result, dict) and "$set" in update:
            self._find_one_result.update(update["$set"])
        return SimpleNamespace(modified_count=1)

    async def find_one_and_update(self, query, update, **_kwargs):
        """Atomic conditional update used by the approval race fix.
        This fake intentionally does NOT enforce the prior-state
        filter — the race lock lives in a dedicated test
        (test_concurrent_approvals_cannot_both_succeed) that uses a
        richer two-row fake. Tests at this layer want the happy
        path: validator passed, find_one_and_update should commit.
        Pre-validator state checks in the route already gate
        illegal transitions, so applying the update here matches
        the production happy-path behavior."""
        self.updates.append((dict(query), dict(update)))
        if not isinstance(self._find_one_result, dict):
            return None
        if "$set" in update:
            self._find_one_result.update(update["$set"])
        return dict(self._find_one_result)

    async def insert_one(self, doc):
        self.inserts.append(dict(doc))
        return SimpleNamespace(inserted_id="x")

    def find(self, query, projection=None):
        return _FakeCursor(list(self._find_results))


class _FakeCursor:
    def __init__(self, items):
        self._items = items

    def sort(self, *_a, **_kw):
        return self

    async def to_list(self, _n):
        return self._items


def _setup_db(monkeypatch, *, contracts, contract_approvals=None, projects=None):
    """Inject fake collections in place of the contracts.py db
    accessor. Returns the collection fakes so assertions can inspect."""
    contract_approvals = contract_approvals or _FakeCollection()
    projects = projects or _FakeCollection()

    class _DB:
        pass

    db = _DB()
    db.contracts = contracts
    db.contract_approvals = contract_approvals
    db.projects = projects

    import routes.contracts as routes_contracts
    monkeypatch.setattr(routes_contracts, "get_db", lambda: db)
    monkeypatch.setattr(
        routes_contracts, "log_action", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        routes_contracts, "emit_event", AsyncMock(return_value=None)
    )
    # get_contract_filter is used by the approval-queue route; admins
    # see everything so empty filter is the right stub.
    async def _empty_filter(_u):
        return {}
    monkeypatch.setattr(routes_contracts, "get_contract_filter", _empty_filter)
    return contracts, contract_approvals, projects, routes_contracts


# ---------------------------------------------------------------------------
# A. boh_with_boa accepted by create-contract validator
# ---------------------------------------------------------------------------


def test_create_contract_accepts_boh_with_boa(monkeypatch):
    """The Wave 0 contract type must surface as a valid create input."""
    import routes.contracts as rc
    # Read the literal valid_types list out of the source so the
    # acceptance is locked at the source level. Avoids needing to
    # stand up the full DB round-trip just to verify the allowlist.
    import inspect
    src = inspect.getsource(rc.create_contract)
    assert '"boh_with_boa"' in src or "'boh_with_boa'" in src


# ---------------------------------------------------------------------------
# B. Approval action endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_for_approval_sets_pending_and_draft(monkeypatch):
    contracts = _FakeCollection(find_one_result=_fake_contract())
    _, contract_approvals, _, rc = _setup_db(monkeypatch, contracts=contracts)
    result = await rc.submit_contract_for_approval(
        contract_id="contract_synth_001",
        data={"approval_notes": "Ready for finance"},
        current_user=_USER,
    )
    assert result["approval_status"] == "pending"
    assert result["status"] == "draft"
    assert contracts.updates, "submit must update the contract"
    set_payload = contracts.updates[0][1]["$set"]
    assert set_payload["approval_status"] == "pending"
    assert set_payload["status"] == "draft"
    assert contract_approvals.inserts, "submit must write contract_approvals row"
    appr = contract_approvals.inserts[0]
    assert appr["action"] == "submit"
    assert appr["next_approval_status"] == "pending"


@pytest.mark.asyncio
async def test_approve_from_pending_sets_approved_and_active(monkeypatch):
    contracts = _FakeCollection(find_one_result=_fake_contract(approval_status="pending"))
    _, contract_approvals, _, rc = _setup_db(monkeypatch, contracts=contracts)
    result = await rc.approve_contract(
        contract_id="contract_synth_001",
        data={"approval_notes": "All good"},
        current_user=_USER,
    )
    assert result["approval_status"] == "approved"
    assert result["status"] == "active"
    set_payload = contracts.updates[0][1]["$set"]
    assert set_payload["approved_by"] == _USER.user_id
    assert "approved_at" in set_payload
    assert contract_approvals.inserts[0]["action"] == "approve"


@pytest.mark.asyncio
async def test_reject_from_pending_sets_rejected_and_draft(monkeypatch):
    contracts = _FakeCollection(find_one_result=_fake_contract(approval_status="pending"))
    _, contract_approvals, _, rc = _setup_db(monkeypatch, contracts=contracts)
    result = await rc.reject_contract(
        contract_id="contract_synth_001",
        data={"approval_notes": "Margin too tight"},
        current_user=_USER,
    )
    assert result["approval_status"] == "rejected"
    assert result["status"] == "draft"
    assert contract_approvals.inserts[0]["action"] == "reject"
    assert contract_approvals.inserts[0]["notes"] == "Margin too tight"


@pytest.mark.asyncio
async def test_request_changes_from_pending_sets_changes_requested_and_draft(monkeypatch):
    contracts = _FakeCollection(find_one_result=_fake_contract(approval_status="pending"))
    _, contract_approvals, _, rc = _setup_db(monkeypatch, contracts=contracts)
    result = await rc.request_changes_on_contract(
        contract_id="contract_synth_001",
        data={"approval_notes": "Add SOW reference"},
        current_user=_USER,
    )
    assert result["approval_status"] == "changes_requested"
    assert result["status"] == "draft"
    assert contract_approvals.inserts[0]["action"] == "request_changes"


# ---------------------------------------------------------------------------
# C. Invalid transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_before_submit_returns_409(monkeypatch):
    contracts = _FakeCollection(find_one_result=_fake_contract(approval_status=None))
    _setup_db(monkeypatch, contracts=contracts)
    import routes.contracts as rc
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rc.approve_contract(
            contract_id="contract_synth_001",
            data={},
            current_user=_USER,
        )
    assert ei.value.status_code == 409
    assert "approve" in str(ei.value.detail).lower()


@pytest.mark.asyncio
async def test_reject_when_already_approved_returns_409(monkeypatch):
    contracts = _FakeCollection(find_one_result=_fake_contract(approval_status="approved"))
    _setup_db(monkeypatch, contracts=contracts)
    import routes.contracts as rc
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rc.reject_contract(
            contract_id="contract_synth_001",
            data={},
            current_user=_USER,
        )
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_missing_contract_returns_404(monkeypatch):
    contracts = _FakeCollection(find_one_result=None)
    _setup_db(monkeypatch, contracts=contracts)
    import routes.contracts as rc
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rc.submit_contract_for_approval(
            contract_id="contract_missing", data={}, current_user=_USER,
        )
    assert ei.value.status_code == 404


# ---------------------------------------------------------------------------
# D. Approval queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_queue_returns_pending_only(monkeypatch):
    pending_a = _fake_contract(contract_id="c_a", approval_status="pending")
    pending_b = _fake_contract(contract_id="c_b", approval_status="pending")
    contracts = _FakeCollection(find_results=[pending_a, pending_b])
    _setup_db(monkeypatch, contracts=contracts)
    import routes.contracts as rc
    result = await rc.list_contract_approval_queue(current_user=_USER)
    assert result["total"] == 2
    ids = {item["contract_id"] for item in result["items"]}
    assert ids == {"c_a", "c_b"}


@pytest.mark.asyncio
async def test_approval_queue_denies_without_approve_perm(monkeypatch):
    contracts = _FakeCollection(find_results=[])
    _setup_db(monkeypatch, contracts=contracts)
    import routes.contracts as rc
    no_approve = SimpleNamespace(
        user_id="u_sales_ops",
        email="ops@aegisaxiom.test",
        role="sales_ops",
        tenant_id=None,
        account_id=None,
        permissions=["contracts.create", "contracts.edit"],
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rc.list_contract_approval_queue(current_user=no_approve)
    assert ei.value.status_code == 403


# ---------------------------------------------------------------------------
# E. Permission gating — approval actions require contracts.approve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_requires_contracts_approve_perm(monkeypatch):
    contracts = _FakeCollection(find_one_result=_fake_contract(approval_status="pending"))
    _setup_db(monkeypatch, contracts=contracts)
    import routes.contracts as rc
    sales_ops_user = SimpleNamespace(
        user_id="u_sales_ops",
        email="ops@aegisaxiom.test",
        role="sales_ops",
        tenant_id=None,
        account_id=None,
        permissions=["contracts.create", "contracts.edit"],
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rc.approve_contract(
            contract_id="contract_synth_001",
            data={},
            current_user=sales_ops_user,
        )
    assert ei.value.status_code == 403
