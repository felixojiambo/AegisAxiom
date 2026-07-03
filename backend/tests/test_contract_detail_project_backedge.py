"""Tests for the contract → project back-edge enrichment.

The Zoho importer stored the contract↔project relationship on the
project side (``project.contract_id``), not the contract side
(``contract.project_ids[]``). Pre-fix, ``GET /api/contracts/{id}``
only enriched via the forward list, so all 94 imported contracts
appeared to have zero linked projects in the UI even though 101
imported projects pointed at them.

This test pins the merged enrichment behaviour:

  * Forward project_ids[] still works.
  * Back-edge (project.contract_id) is fanned in.
  * Both sources deduplicate by project_id.
  * The contract document's stored project_ids[] is NOT mutated by
    the read path — promotion to the canonical list is a separate
    operator step.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production",
)

import pytest


_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# ---------------------------------------------------------------------------
# Fake Mongo — covers the exact surface routes/contracts.get_contract uses.
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    async def to_list(self, _n):
        return self._docs

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as e:
            raise StopAsyncIteration from e


class _Coll:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query=None, _projection=None):
        rows = [d for d in self.docs if _match(d, query or {})]
        return _Cursor(rows)

    async def find_one(self, query, _projection=None):
        for d in self.docs:
            if _match(d, query):
                return {k: v for k, v in d.items() if k != "_id"}
        return None


def _match(doc, q):
    for k, v in q.items():
        if k == "$and":
            if not all(_match(doc, sub) for sub in v):
                return False
            continue
        if isinstance(v, dict):
            if "$in" in v and doc.get(k) not in v["$in"]:
                return False
            if "$ne" in v and doc.get(k) == v["$ne"]:
                return False
        else:
            if doc.get(k) != v:
                return False
    return True


class _FakeDB:
    def __init__(self, *, contracts, projects, deals=None):
        self.contracts = _Coll(contracts)
        self.projects = _Coll(projects)
        self.deals = _Coll(deals or [])


def _admin_user():
    return SimpleNamespace(
        user_id="u_admin", role="admin", email="a@x", name="Admin",
        permissions=["*"], effective_permissions=["*"], account_id=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_back_edge_only_imported_contract_returns_related_projects():
    """The pre-fix bug: an imported contract with project_ids=[] but
    projects pointing at it via project.contract_id rendered as
    "no projects linked". After the fix the back-edge is merged."""
    from routes import contracts as cmod

    contract = {
        "contract_id": "contract_imp_1",
        "import_source": "zoho",
        "project_ids": [],
    }
    projects = [
        {"project_id": "proj_a", "name": "Imported Alpha",
         "contract_id": "contract_imp_1", "status": "planning"},
        {"project_id": "proj_b", "name": "Imported Beta",
         "contract_id": "contract_imp_1", "status": "planning"},
        {"project_id": "proj_other", "name": "Unrelated",
         "contract_id": "contract_other", "status": "active"},
    ]
    db = _FakeDB(contracts=[contract], projects=projects)
    original_get_db = cmod.get_db
    cmod.get_db = lambda: db
    original_filter = cmod.get_contract_filter

    async def _no_filter(_u):
        return {}

    cmod.get_contract_filter = _no_filter
    try:
        result = asyncio.run(cmod.get_contract("contract_imp_1", current_user=_admin_user()))
    finally:
        cmod.get_db = original_get_db
        cmod.get_contract_filter = original_filter

    enriched_ids = sorted(p["project_id"] for p in result.get("projects", []))
    assert enriched_ids == ["proj_a", "proj_b"]
    # The contract's stored project_ids[] must NOT be mutated by the
    # read path — that promotion is a separate operator step.
    assert result.get("project_ids") == []


def test_forward_project_ids_still_resolved():
    """Native contracts use the forward project_ids[] list; the
    back-edge merge must not break that path."""
    from routes import contracts as cmod

    contract = {
        "contract_id": "contract_native",
        "project_ids": ["proj_x", "proj_y"],
    }
    projects = [
        {"project_id": "proj_x", "name": "X", "contract_id": None,
         "status": "active"},
        {"project_id": "proj_y", "name": "Y", "contract_id": None,
         "status": "active"},
    ]
    db = _FakeDB(contracts=[contract], projects=projects)
    original_get_db = cmod.get_db
    cmod.get_db = lambda: db
    original_filter = cmod.get_contract_filter

    async def _no_filter(_u):
        return {}

    cmod.get_contract_filter = _no_filter
    try:
        result = asyncio.run(cmod.get_contract("contract_native", current_user=_admin_user()))
    finally:
        cmod.get_db = original_get_db
        cmod.get_contract_filter = original_filter

    enriched_ids = sorted(p["project_id"] for p in result.get("projects", []))
    assert enriched_ids == ["proj_x", "proj_y"]


def test_forward_and_back_edge_dedupe_by_project_id():
    """A project that lives on BOTH sides (forward list AND back-edge
    contract_id) must appear exactly once in the enriched response."""
    from routes import contracts as cmod

    contract = {
        "contract_id": "contract_mixed",
        "project_ids": ["proj_both"],  # forward
    }
    projects = [
        {"project_id": "proj_both", "name": "Both", "contract_id": "contract_mixed"},
        {"project_id": "proj_back_only", "name": "Back only",
         "contract_id": "contract_mixed"},
    ]
    db = _FakeDB(contracts=[contract], projects=projects)
    original_get_db = cmod.get_db
    cmod.get_db = lambda: db
    original_filter = cmod.get_contract_filter

    async def _no_filter(_u):
        return {}

    cmod.get_contract_filter = _no_filter
    try:
        result = asyncio.run(cmod.get_contract("contract_mixed", current_user=_admin_user()))
    finally:
        cmod.get_db = original_get_db
        cmod.get_contract_filter = original_filter

    enriched_ids = sorted(p["project_id"] for p in result.get("projects", []))
    # Dedup'd: proj_both appears once.
    assert enriched_ids == ["proj_back_only", "proj_both"]
    assert len(result["projects"]) == 2


def test_contract_with_no_linked_projects_has_no_projects_key():
    """When neither side has projects, the route does not synthesize
    an empty projects[] key (preserves the prior wire shape)."""
    from routes import contracts as cmod

    contract = {"contract_id": "contract_empty", "project_ids": []}
    db = _FakeDB(contracts=[contract], projects=[])
    original_get_db = cmod.get_db
    cmod.get_db = lambda: db
    original_filter = cmod.get_contract_filter

    async def _no_filter(_u):
        return {}

    cmod.get_contract_filter = _no_filter
    try:
        result = asyncio.run(cmod.get_contract("contract_empty", current_user=_admin_user()))
    finally:
        cmod.get_db = original_get_db
        cmod.get_contract_filter = original_filter

    # Pre-existing callers tolerated the absence of `projects`; keep it.
    assert "projects" not in result


def test_back_edge_skips_projects_with_no_project_id():
    """Defensive: a project row missing `project_id` must not crash
    the merge and must not appear in the enriched list."""
    from routes import contracts as cmod

    contract = {"contract_id": "contract_z", "project_ids": []}
    projects = [
        {"name": "Orphan no-id", "contract_id": "contract_z"},
        {"project_id": "proj_ok", "name": "OK", "contract_id": "contract_z"},
    ]
    db = _FakeDB(contracts=[contract], projects=projects)
    original_get_db = cmod.get_db
    cmod.get_db = lambda: db
    original_filter = cmod.get_contract_filter

    async def _no_filter(_u):
        return {}

    cmod.get_contract_filter = _no_filter
    try:
        result = asyncio.run(cmod.get_contract("contract_z", current_user=_admin_user()))
    finally:
        cmod.get_db = original_get_db
        cmod.get_contract_filter = original_filter

    enriched_ids = [p["project_id"] for p in result.get("projects", [])]
    assert enriched_ids == ["proj_ok"]
