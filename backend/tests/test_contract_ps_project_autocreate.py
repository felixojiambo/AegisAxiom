"""Wave 1 (contracts-time-mgmt) — Professional Services auto-create tests.

Verifies the side effect of approving a PS contract that has no
linked projects: the route auto-creates one project, stamps the
contract back-edge, and is idempotent on a second approve. Field
Services contracts must NOT trigger the auto-create branch in this
wave.

Coverage:
  - Approving a PS contract with empty project_ids auto-creates one
    project (writes db.projects.insert_one with contract_id back-edge)
    and appends the new project_id onto contract.project_ids.
  - Approving the same PS contract twice does not create a duplicate;
    the second approve is a 409 because the state has already moved
    out of pending.
  - Approving a PS contract that already has linked projects does NOT
    create another one.
  - Approving a Field Services contract does NOT create any project.
  - Approving a PS contract with missing required project fields
    (no project_type / service_line) returns 409 with the actionable
    message naming the missing fields.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
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


def _fake_pending_contract(**overrides) -> dict:
    base = {
        "contract_id": "contract_ps_001",
        "name": "Acme PS Engagement",
        "account_id": "acct_acme",
        "account_name": "Acme",
        "contract_type": "fixed_fee",
        "service_category": "professional_services",
        "status": "draft",
        "approval_status": "pending",
        "project_ids": [],
        "tenant_id": None,
    }
    base.update(overrides)
    return base


class _FakeContractsCollection:
    def __init__(self, doc):
        self._doc = dict(doc) if isinstance(doc, dict) else None
        self.updates: list = []

    async def find_one(self, query, projection=None):
        return dict(self._doc) if isinstance(self._doc, dict) else None

    async def update_one(self, query, update):
        self.updates.append((dict(query), dict(update)))
        if isinstance(self._doc, dict) and "$set" in update:
            self._doc.update(update["$set"])
        return SimpleNamespace(modified_count=1)

    async def find_one_and_update(self, query, update, **_kwargs):
        """Atomic conditional update used by the approval race fix."""
        self.updates.append((dict(query), dict(update)))
        if not isinstance(self._doc, dict):
            return None
        if "$set" in update:
            self._doc.update(update["$set"])
        return dict(self._doc)


class _FakeProjectsCollection:
    def __init__(self):
        self.inserts: list = []

    async def insert_one(self, doc):
        self.inserts.append(dict(doc))
        return SimpleNamespace(inserted_id="x")


class _FakeApprovalsCollection:
    def __init__(self):
        self.inserts: list = []

    async def insert_one(self, doc):
        self.inserts.append(dict(doc))
        return SimpleNamespace(inserted_id="x")


def _setup(monkeypatch, contract_doc):
    contracts = _FakeContractsCollection(contract_doc)
    projects = _FakeProjectsCollection()
    approvals = _FakeApprovalsCollection()

    class _DB:
        pass
    db = _DB()
    db.contracts = contracts
    db.projects = projects
    db.contract_approvals = approvals

    import routes.contracts as routes_contracts
    monkeypatch.setattr(routes_contracts, "get_db", lambda: db)
    monkeypatch.setattr(routes_contracts, "log_action", AsyncMock(return_value=None))
    monkeypatch.setattr(routes_contracts, "emit_event", AsyncMock(return_value=None))

    async def _empty_filter(_u):
        return {}
    monkeypatch.setattr(routes_contracts, "get_contract_filter", _empty_filter)

    return contracts, projects, approvals, routes_contracts


@pytest.mark.asyncio
async def test_approve_ps_contract_auto_creates_one_project(monkeypatch):
    contracts, projects, approvals, rc = _setup(monkeypatch, _fake_pending_contract())
    result = await rc.approve_contract(
        contract_id="contract_ps_001",
        data={
            "approval_notes": "All good",
            "project_defaults": {
                "project_type": "waterfall",
                "service_line": "ucaas",
            },
        },
        current_user=_USER,
    )
    assert projects.inserts, "PS approve must insert one project"
    assert len(projects.inserts) == 1
    inserted = projects.inserts[0]
    assert inserted["contract_id"] == "contract_ps_001"
    assert inserted["account_id"] == "acct_acme"
    assert inserted["project_type"] == "waterfall"
    assert inserted["service_line"] == "ucaas"
    assert inserted["status"] == "planning"  # default
    assert inserted["auto_created_from_contract"] is True
    # Contract's project_ids back-edge.
    assert inserted["project_id"] in (result.get("project_ids") or [])
    # Response includes the auto-created project id signal for the UI.
    assert result.get("_auto_created_project_id") == inserted["project_id"]


@pytest.mark.asyncio
async def test_approve_ps_contract_with_existing_project_does_not_create(monkeypatch):
    contracts, projects, _, rc = _setup(
        monkeypatch,
        _fake_pending_contract(project_ids=["proj_pre_existing"]),
    )
    await rc.approve_contract(
        contract_id="contract_ps_001",
        data={"approval_notes": "Already has a project"},
        current_user=_USER,
    )
    assert projects.inserts == [], (
        "Approval must not create a duplicate project when one is already linked"
    )


@pytest.mark.asyncio
async def test_double_approve_does_not_create_duplicate(monkeypatch):
    """First approve succeeds and auto-creates. Second approve fails
    409 because the contract is no longer in 'pending' state. No
    duplicate project is written."""
    contracts, projects, _, rc = _setup(monkeypatch, _fake_pending_contract())
    await rc.approve_contract(
        contract_id="contract_ps_001",
        data={
            "approval_notes": "First",
            "project_defaults": {"project_type": "waterfall", "service_line": "ucaas"},
        },
        current_user=_USER,
    )
    # Sanity: one insert occurred.
    assert len(projects.inserts) == 1

    # Second approve attempt — fails because the contract is now in
    # approval_status='approved', not 'pending'.
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rc.approve_contract(
            contract_id="contract_ps_001",
            data={"approval_notes": "Second"},
            current_user=_USER,
        )
    assert ei.value.status_code == 409
    # No second project insert.
    assert len(projects.inserts) == 1


@pytest.mark.asyncio
async def test_approve_field_services_contract_does_not_create_project(monkeypatch):
    contracts, projects, _, rc = _setup(
        monkeypatch,
        _fake_pending_contract(service_category="field_services"),
    )
    await rc.approve_contract(
        contract_id="contract_ps_001",
        data={"approval_notes": "FS lane"},
        current_user=_USER,
    )
    assert projects.inserts == [], "Field Services approve must not auto-create a project"


@pytest.mark.asyncio
async def test_approve_ps_contract_missing_project_fields_returns_409(monkeypatch):
    contracts, projects, _, rc = _setup(monkeypatch, _fake_pending_contract())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rc.approve_contract(
            contract_id="contract_ps_001",
            data={"approval_notes": "No defaults"},  # no project_defaults
            current_user=_USER,
        )
    assert ei.value.status_code == 409
    detail = str(ei.value.detail)
    assert "Professional Services" in detail
    assert "project_type" in detail
    assert "service_line" in detail
    # No project was created.
    assert projects.inserts == []


@pytest.mark.asyncio
async def test_lob_marker_also_triggers_ps_branch(monkeypatch):
    """line_of_business='professional_services' is an equally valid PS
    designation; the auto-create branch must respect it."""
    contracts, projects, _, rc = _setup(
        monkeypatch,
        _fake_pending_contract(
            service_category="",
            line_of_business="professional_services",
        ),
    )
    await rc.approve_contract(
        contract_id="contract_ps_001",
        data={
            "approval_notes": "via line_of_business",
            "project_defaults": {"project_type": "waterfall", "service_line": "other"},
        },
        current_user=_USER,
    )
    assert len(projects.inserts) == 1
    assert projects.inserts[0]["service_line"] == "other"
