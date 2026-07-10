"""Wave 1 (contracts-time-mgmt) — contract <-> project linking tests.

Exercises POST /contracts/{contract_id}/projects (attach) and
DELETE /contracts/{contract_id}/projects/{project_id} (detach) directly
through the route functions, using AsyncMock-shaped fakes. No Mongo
required.

Coverage:
  - Attach multiple projects to one contract; dedupe; back-edge set.
  - Detach removes from contract.project_ids and clears project's
    contract_id when the project pointed at this contract.
  - Duplicate project ids in the request body are deduped.
  - Cross-tenant project ids return 404 when the user has a tenant.
  - Missing contract returns 404.
  - Missing project returns 404 with the missing id named.
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
        "contracts.view.all",
    ],
)


def _fake_contract(**overrides) -> dict:
    base = {
        "contract_id": "contract_synth_001",
        "name": "Acme",
        "account_id": "acct_001",
        "project_ids": [],
        "tenant_id": None,
    }
    base.update(overrides)
    return base


def _fake_project(project_id: str, **overrides) -> dict:
    base = {
        "project_id": project_id,
        "name": f"Project {project_id}",
        "account_id": "acct_001",
        "tenant_id": None,
        "contract_id": None,
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
        if isinstance(self._doc, dict):
            if "$set" in update:
                self._doc.update(update["$set"])
            if "$pull" in update:
                for field, value in update["$pull"].items():
                    current = list(self._doc.get(field) or [])
                    self._doc[field] = [v for v in current if v != value]
        return SimpleNamespace(modified_count=1)


class _FakeProjectsCollection:
    def __init__(self, docs):
        self._by_id = {p["project_id"]: dict(p) for p in docs}
        self.update_many_calls: list = []
        self.update_one_calls: list = []

    def find(self, query, projection=None):
        ids = query.get("project_id", {}).get("$in", [])
        items = [dict(self._by_id[i]) for i in ids if i in self._by_id]
        return _FakeProjectsCursor(items)

    async def update_many(self, query, update):
        self.update_many_calls.append((dict(query), dict(update)))
        ids = query.get("project_id", {}).get("$in", [])
        for i in ids:
            if i in self._by_id and "$set" in update:
                self._by_id[i].update(update["$set"])
        return SimpleNamespace(modified_count=len(ids))

    async def update_one(self, query, update):
        self.update_one_calls.append((dict(query), dict(update)))
        pid = query.get("project_id")
        if pid in self._by_id and "$set" in update:
            self._by_id[pid].update(update["$set"])
        return SimpleNamespace(modified_count=1)


class _FakeProjectsCursor:
    def __init__(self, items):
        self._items = items

    async def to_list(self, _n):
        return self._items


def _setup_db(monkeypatch, *, contract_doc, project_docs):
    contracts = _FakeContractsCollection(contract_doc)
    projects = _FakeProjectsCollection(project_docs)

    class _DB:
        pass
    db = _DB()
    db.contracts = contracts
    db.projects = projects
    db.contract_approvals = SimpleNamespace(insert_one=AsyncMock(return_value=None))

    import routes.contracts as routes_contracts
    monkeypatch.setattr(routes_contracts, "get_db", lambda: db)
    monkeypatch.setattr(routes_contracts, "log_action", AsyncMock(return_value=None))
    monkeypatch.setattr(routes_contracts, "emit_event", AsyncMock(return_value=None))
    return contracts, projects, routes_contracts


@pytest.mark.asyncio
async def test_attach_multiple_projects(monkeypatch):
    contracts, projects, rc = _setup_db(
        monkeypatch,
        contract_doc=_fake_contract(),
        project_docs=[_fake_project("proj_a"), _fake_project("proj_b")],
    )
    result = await rc.attach_projects_to_contract(
        contract_id="contract_synth_001",
        data={"project_ids": ["proj_a", "proj_b"]},
        current_user=_USER,
    )
    assert sorted(result["project_ids"]) == ["proj_a", "proj_b"]
    # Back-edge set on both projects.
    assert projects.update_many_calls, "attach must set contract_id on projects"
    target_set = projects.update_many_calls[0][1]["$set"]
    assert target_set["contract_id"] == "contract_synth_001"


@pytest.mark.asyncio
async def test_attach_dedupes_body_and_existing(monkeypatch):
    contracts, projects, rc = _setup_db(
        monkeypatch,
        contract_doc=_fake_contract(project_ids=["proj_a"]),
        project_docs=[
            _fake_project("proj_a"),
            _fake_project("proj_b"),
        ],
    )
    result = await rc.attach_projects_to_contract(
        contract_id="contract_synth_001",
        data={"project_ids": ["proj_a", "proj_a", "proj_b", "proj_b"]},
        current_user=_USER,
    )
    assert result["project_ids"] == ["proj_a", "proj_b"]
    # update_many only fires for newly-linked ids.
    if projects.update_many_calls:
        ids = projects.update_many_calls[0][0]["project_id"]["$in"]
        assert ids == ["proj_b"]


@pytest.mark.asyncio
async def test_attach_missing_project_returns_404(monkeypatch):
    contracts, projects, rc = _setup_db(
        monkeypatch,
        contract_doc=_fake_contract(),
        project_docs=[_fake_project("proj_a")],
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rc.attach_projects_to_contract(
            contract_id="contract_synth_001",
            data={"project_ids": ["proj_a", "proj_missing"]},
            current_user=_USER,
        )
    assert ei.value.status_code == 404
    assert "proj_missing" in str(ei.value.detail)


@pytest.mark.asyncio
async def test_attach_missing_contract_returns_404(monkeypatch):
    contracts, projects, rc = _setup_db(
        monkeypatch,
        contract_doc=None,
        project_docs=[_fake_project("proj_a")],
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rc.attach_projects_to_contract(
            contract_id="contract_missing",
            data={"project_ids": ["proj_a"]},
            current_user=_USER,
        )
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_attach_cross_tenant_project_returns_404(monkeypatch):
    tenant_user = SimpleNamespace(
        user_id="u_tenant",
        email="tenant@aegisaxiom.test",
        role="director",
        tenant_id="tenant_a",
        account_id=None,
        permissions=["contracts.edit", "contracts.edit.all"],
    )
    contracts, projects, rc = _setup_db(
        monkeypatch,
        contract_doc=_fake_contract(tenant_id="tenant_a"),
        project_docs=[
            _fake_project("proj_a", tenant_id="tenant_a"),
            _fake_project("proj_b", tenant_id="tenant_b"),
        ],
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rc.attach_projects_to_contract(
            contract_id="contract_synth_001",
            data={"project_ids": ["proj_a", "proj_b"]},
            current_user=tenant_user,
        )
    assert ei.value.status_code == 404
    assert "proj_b" in str(ei.value.detail)


@pytest.mark.asyncio
async def test_attach_empty_body_returns_400(monkeypatch):
    contracts, projects, rc = _setup_db(
        monkeypatch,
        contract_doc=_fake_contract(),
        project_docs=[],
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rc.attach_projects_to_contract(
            contract_id="contract_synth_001",
            data={"project_ids": []},
            current_user=_USER,
        )
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_detach_removes_project_and_clears_back_edge(monkeypatch):
    contracts, projects, rc = _setup_db(
        monkeypatch,
        contract_doc=_fake_contract(project_ids=["proj_a", "proj_b"]),
        project_docs=[
            _fake_project("proj_a", contract_id="contract_synth_001"),
            _fake_project("proj_b", contract_id="contract_synth_001"),
        ],
    )
    result = await rc.detach_project_from_contract(
        contract_id="contract_synth_001",
        project_id="proj_a",
        current_user=_USER,
    )
    assert "proj_a" not in (result["project_ids"] or [])
    # Back-edge cleared (filtered on contract_id match so a re-pointed
    # project doesn't get clobbered).
    assert projects.update_one_calls
    q = projects.update_one_calls[0][0]
    assert q == {"project_id": "proj_a", "contract_id": "contract_synth_001"}


@pytest.mark.asyncio
async def test_detach_unknown_project_is_idempotent(monkeypatch):
    contracts, projects, rc = _setup_db(
        monkeypatch,
        contract_doc=_fake_contract(project_ids=["proj_a"]),
        project_docs=[_fake_project("proj_a", contract_id="contract_synth_001")],
    )
    # Detach a project that was never linked — should NOT raise.
    result = await rc.detach_project_from_contract(
        contract_id="contract_synth_001",
        project_id="proj_never_linked",
        current_user=_USER,
    )
    assert result["project_ids"] == ["proj_a"]
    # Back-edge update is skipped when the project wasn't in project_ids.
    assert not projects.update_one_calls
