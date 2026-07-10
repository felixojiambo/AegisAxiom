"""Locks the {items,total,skip,limit} envelope + skip/limit behaviour
on the two list endpoints the freshly-paginated frontend pages call:

  GET /api/contracts          (routes/contracts.py:get_contracts)
  GET /api/projects/projects  (routes/projects_project.py:list_projects)

Both pages were converted from "load 100 default / no pagination" or
"infinite-load" to real server-side pagination. The frontend reads
``total`` to render "Showing X-Y of Z" and to enable/disable the
prev/next buttons; if the backend drifts to ``items.length`` only,
the controls silently misbehave. These tests pin the contract.

The Time Management Quick Add picker calls
``/api/projects/projects?limit=500`` directly — the projects route
caps internally at 500 and the test for limit=500 confirms the cap
holds, so the picker still loads its full set.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production",
)

import pytest


_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Minimal async cursor/collection fakes — covers the route's exact surface
# (``find().sort().skip().limit().to_list()`` + ``count_documents()``).
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._skip = 0
        self._limit = None

    def sort(self, *_args, **_kwargs):
        return self

    def skip(self, n):
        self._skip = n
        return self

    def limit(self, n):
        self._limit = n
        return self

    async def to_list(self, _n):
        sliced = self._docs[self._skip:]
        if self._limit is not None:
            sliced = sliced[: self._limit]
        return sliced


class _Coll:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query=None, _projection=None):
        rows = [d for d in self.docs if _matches(d, query or {})]
        return _Cursor(rows)

    async def count_documents(self, query):
        if not query:
            return len(self.docs)
        return sum(1 for d in self.docs if _matches(d, query))


def _doc_field(doc, dotted):
    cur = doc
    for seg in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(seg)
    return cur


def _matches(doc, q):
    import re as _re
    if not q:
        return True
    # The routes use equality, $ne, $in, $nin, $and, $or, and $regex.
    for k, v in q.items():
        if k == "$and":
            if not all(_matches(doc, sub) for sub in v):
                return False
            continue
        if k == "$or":
            if not any(_matches(doc, sub) for sub in v):
                return False
            continue
        if isinstance(v, dict):
            field_val = _doc_field(doc, k)
            if "$ne" in v and field_val == v["$ne"]:
                return False
            if "$in" in v and field_val not in v["$in"]:
                return False
            if "$nin" in v and field_val in v["$nin"]:
                return False
            if "$regex" in v:
                flags = _re.IGNORECASE if "i" in (v.get("$options") or "") else 0
                if not isinstance(field_val, str):
                    return False
                if not _re.search(v["$regex"], field_val, flags):
                    return False
        else:
            if doc.get(k) != v:
                return False
    return True


class _FakeDB:
    def __init__(self, *, contracts=None, projects=None, users=None):
        self.contracts = _Coll(contracts or [])
        self.projects = _Coll(projects or [])
        self.users = _Coll(users or [])
        self.accounts = _Coll([])
        self.deals = _Coll([])

    def __getitem__(self, name):
        return getattr(self, name)


# ---------------------------------------------------------------------------
# Tests — contracts route
# ---------------------------------------------------------------------------


def _admin_user():
    return SimpleNamespace(
        user_id="u_admin", role="admin", email="a@x", name="Admin",
        permissions=["*"], effective_permissions=["*"], account_id=None,
    )


def _make_contracts(n):
    return [
        {"contract_id": f"c_{i}", "name": f"Contract {i}",
         "contract_type": "boh", "status": "draft", "is_test_data": False,
         "created_at": f"2024-01-{(i % 28) + 1:02d}T00:00:00Z"}
        for i in range(n)
    ]


def test_contracts_route_returns_envelope():
    from database import get_db, init as db_init  # noqa: WPS433
    from routes import contracts as contracts_mod

    db = _FakeDB(contracts=_make_contracts(60))
    # Monkey-patch get_db inside the route module so the handler uses
    # our fake instead of a real Mongo client.
    original = contracts_mod.get_db
    contracts_mod.get_db = lambda: db
    try:
        result = asyncio.run(contracts_mod.get_contracts(
            skip=0, limit=25, contract_type=None, status=None,
            account_id=None, test_only=False, current_user=_admin_user(),
        ))
    finally:
        contracts_mod.get_db = original

    assert set(result.keys()) >= {"items", "total", "skip", "limit"}
    assert result["total"] == 60
    assert result["skip"] == 0
    assert result["limit"] == 25
    assert len(result["items"]) == 25


def test_contracts_route_paginates_via_skip():
    from routes import contracts as contracts_mod

    db = _FakeDB(contracts=_make_contracts(60))
    original = contracts_mod.get_db
    contracts_mod.get_db = lambda: db
    try:
        page1 = asyncio.run(contracts_mod.get_contracts(
            skip=0, limit=25, contract_type=None, status=None,
            account_id=None, test_only=False, current_user=_admin_user(),
        ))
        page2 = asyncio.run(contracts_mod.get_contracts(
            skip=25, limit=25, contract_type=None, status=None,
            account_id=None, test_only=False, current_user=_admin_user(),
        ))
    finally:
        contracts_mod.get_db = original

    p1_ids = {c["contract_id"] for c in page1["items"]}
    p2_ids = {c["contract_id"] for c in page2["items"]}
    assert p1_ids.isdisjoint(p2_ids)
    assert len(p2_ids) == 25
    # Both pages report the same total.
    assert page1["total"] == page2["total"] == 60


def test_contracts_route_filters_imported_test_data_by_default():
    """A real-data list should not return is_test_data=True rows."""
    from routes import contracts as contracts_mod

    docs = _make_contracts(10)
    for d in docs[:3]:
        d["is_test_data"] = True
    db = _FakeDB(contracts=docs)
    original = contracts_mod.get_db
    contracts_mod.get_db = lambda: db
    try:
        result = asyncio.run(contracts_mod.get_contracts(
            skip=0, limit=25, contract_type=None, status=None,
            account_id=None, test_only=False, current_user=_admin_user(),
        ))
    finally:
        contracts_mod.get_db = original
    assert result["total"] == 7  # 10 - 3 test_data rows


# ---------------------------------------------------------------------------
# Tests — q search across the documented fields (May 22, 2026 review)
# ---------------------------------------------------------------------------


def _searchable_contracts():
    """Build a varied set so q can hit each documented field."""
    return [
        {"contract_id": "c_aa", "contract_number": "CN-100008",
         "name": "Reading Bakery PSA", "account_name": "Reading Bakery Co",
         "external_contract_id": "zcrm_4761",
         "external_ids": {"zoho_contract_id": "zcrm_4761"},
         "contract_type": "boh", "status": "draft", "is_test_data": False,
         "created_at": "2024-04-01T00:00:00Z"},
        {"contract_id": "c_bb", "contract_number": "CN-100009",
         "name": "Acme Networking BOH", "account_name": "Acme Industries",
         "external_contract_id": "zcrm_9000",
         "external_ids": {"zoho_contract_id": "zcrm_9000"},
         "contract_type": "boh_with_boa", "status": "draft", "is_test_data": False,
         "created_at": "2024-04-02T00:00:00Z"},
        {"contract_id": "c_cc", "contract_number": "CN-100010",
         "name": "MSP Block of Hours", "account_name": "Globex",
         "external_contract_id": "zcrm_5555",
         "external_ids": {"zoho_contract_id": "zcrm_5555"},
         "contract_type": "fixed_fee", "status": "active", "is_test_data": False,
         "created_at": "2024-04-03T00:00:00Z"},
    ]


def test_q_search_matches_contract_number():
    from routes import contracts as contracts_mod
    db = _FakeDB(contracts=_searchable_contracts())
    original = contracts_mod.get_db
    contracts_mod.get_db = lambda: db
    try:
        result = asyncio.run(contracts_mod.get_contracts(
            skip=0, limit=25, contract_type=None, status=None,
            account_id=None, test_only=False, q="CN-100008",
            current_user=_admin_user(),
        ))
    finally:
        contracts_mod.get_db = original
    assert result["total"] == 1
    assert result["items"][0]["contract_id"] == "c_aa"


def test_q_search_matches_name_substring_case_insensitive():
    from routes import contracts as contracts_mod
    db = _FakeDB(contracts=_searchable_contracts())
    original = contracts_mod.get_db
    contracts_mod.get_db = lambda: db
    try:
        # lowercase "block" should hit "MSP Block of Hours"; substring
        # match on "BOH" hits "Acme Networking BOH" via the name field.
        result_block = asyncio.run(contracts_mod.get_contracts(
            skip=0, limit=25, contract_type=None, status=None,
            account_id=None, test_only=False, q="block",
            current_user=_admin_user(),
        ))
    finally:
        contracts_mod.get_db = original
    ids = {c["contract_id"] for c in result_block["items"]}
    assert "c_cc" in ids


def test_q_search_matches_account_name():
    from routes import contracts as contracts_mod
    db = _FakeDB(contracts=_searchable_contracts())
    original = contracts_mod.get_db
    contracts_mod.get_db = lambda: db
    try:
        result = asyncio.run(contracts_mod.get_contracts(
            skip=0, limit=25, contract_type=None, status=None,
            account_id=None, test_only=False, q="Globex",
            current_user=_admin_user(),
        ))
    finally:
        contracts_mod.get_db = original
    assert result["total"] == 1
    assert result["items"][0]["contract_id"] == "c_cc"


def test_q_search_matches_external_contract_id():
    from routes import contracts as contracts_mod
    db = _FakeDB(contracts=_searchable_contracts())
    original = contracts_mod.get_db
    contracts_mod.get_db = lambda: db
    try:
        result = asyncio.run(contracts_mod.get_contracts(
            skip=0, limit=25, contract_type=None, status=None,
            account_id=None, test_only=False, q="zcrm_9000",
            current_user=_admin_user(),
        ))
    finally:
        contracts_mod.get_db = original
    assert result["total"] == 1
    assert result["items"][0]["contract_id"] == "c_bb"


def test_q_search_returns_full_filtered_total_not_just_current_page():
    """Regression for the May 22 bug: search must return ALL matches
    across the dataset, not just the rows that happened to be on the
    current page. Build 30 matches and verify total=30 even when
    paginated at limit=10."""
    from routes import contracts as contracts_mod
    rows = []
    for i in range(30):
        rows.append({
            "contract_id": f"c_match_{i}",
            "name": f"Alpha Project {i}",  # contains "Alpha"
            "account_name": "Acme",
            "contract_type": "boh", "status": "draft",
            "is_test_data": False,
            "created_at": f"2024-04-{(i % 28) + 1:02d}T00:00:00Z",
        })
    for i in range(20):
        rows.append({
            "contract_id": f"c_other_{i}",
            "name": f"Beta Thing {i}",  # does NOT contain "Alpha"
            "account_name": "Acme",
            "contract_type": "boh", "status": "draft",
            "is_test_data": False,
            "created_at": f"2024-05-{(i % 28) + 1:02d}T00:00:00Z",
        })
    db = _FakeDB(contracts=rows)
    original = contracts_mod.get_db
    contracts_mod.get_db = lambda: db
    try:
        result = asyncio.run(contracts_mod.get_contracts(
            skip=0, limit=10, contract_type=None, status=None,
            account_id=None, test_only=False, q="Alpha",
            current_user=_admin_user(),
        ))
    finally:
        contracts_mod.get_db = original
    # Total reflects the filtered universe, NOT the page size.
    assert result["total"] == 30
    assert len(result["items"]) == 10
    # The pagination derived fields still describe the filtered set.
    assert result["pages"] == 3
    assert result["has_next"] is True


def test_q_search_combines_with_type_and_status_filters():
    from routes import contracts as contracts_mod
    db = _FakeDB(contracts=_searchable_contracts())
    original = contracts_mod.get_db
    contracts_mod.get_db = lambda: db
    try:
        # q=BOH matches c_aa (boh), c_bb (boh_with_boa via type), and
        # c_bb name has "BOH". Restrict to contract_type=boh_with_boa
        # -> only c_bb survives.
        result = asyncio.run(contracts_mod.get_contracts(
            skip=0, limit=25, contract_type="boh_with_boa",
            status=None, account_id=None, test_only=False, q="BOH",
            current_user=_admin_user(),
        ))
    finally:
        contracts_mod.get_db = original
    assert result["total"] == 1
    assert result["items"][0]["contract_id"] == "c_bb"


def test_q_search_with_regex_special_characters_is_escaped():
    """A search containing regex metacharacters must NOT be
    interpreted as a regex. The route escapes the user input before
    building the $regex query — the user typing '.*' should match
    only contracts whose field literally contains '.*'."""
    from routes import contracts as contracts_mod
    rows = [
        {"contract_id": "c_meta", "name": "Has .* in name",
         "account_name": "Acme",
         "contract_type": "boh", "status": "draft", "is_test_data": False,
         "created_at": "2024-04-01T00:00:00Z"},
        {"contract_id": "c_plain", "name": "Plain Name",
         "account_name": "Acme",
         "contract_type": "boh", "status": "draft", "is_test_data": False,
         "created_at": "2024-04-02T00:00:00Z"},
    ]
    db = _FakeDB(contracts=rows)
    original = contracts_mod.get_db
    contracts_mod.get_db = lambda: db
    try:
        result = asyncio.run(contracts_mod.get_contracts(
            skip=0, limit=25, contract_type=None, status=None,
            account_id=None, test_only=False, q=".*",
            current_user=_admin_user(),
        ))
    finally:
        contracts_mod.get_db = original
    assert result["total"] == 1
    assert result["items"][0]["contract_id"] == "c_meta"


def test_q_search_empty_string_is_ignored():
    """An empty / whitespace-only q must not narrow the result set."""
    from routes import contracts as contracts_mod
    db = _FakeDB(contracts=_searchable_contracts())
    original = contracts_mod.get_db
    contracts_mod.get_db = lambda: db
    try:
        result_no_q = asyncio.run(contracts_mod.get_contracts(
            skip=0, limit=25, contract_type=None, status=None,
            account_id=None, test_only=False, q=None,
            current_user=_admin_user(),
        ))
        result_empty_q = asyncio.run(contracts_mod.get_contracts(
            skip=0, limit=25, contract_type=None, status=None,
            account_id=None, test_only=False, q="",
            current_user=_admin_user(),
        ))
    finally:
        contracts_mod.get_db = original
    assert result_no_q["total"] == result_empty_q["total"] == 3


# ---------------------------------------------------------------------------
# Tests — projects list route
# ---------------------------------------------------------------------------


def _make_projects(n):
    return [
        {"project_id": f"p_{i}", "name": f"Project {i}",
         "project_type": "waterfall", "service_line": "other",
         "status": "planning", "pm_id": None,
         "assigned_user_ids": [], "import_source": "zoho",
         # Tenant scoping was added upstream — every project row
         # carries tenant_id and the route filters by it. Tests
         # supply the same fixed tenant in all calls below.
         "tenant_id": "t_test",
         "created_at": f"2024-01-{(i % 28) + 1:02d}T00:00:00Z"}
        for i in range(n)
    ]


def test_projects_route_returns_envelope():
    from routes import projects_project as pmod

    db = _FakeDB(projects=_make_projects(101))
    original = pmod.get_db
    pmod.get_db = lambda: db
    try:
        result = asyncio.run(pmod.list_projects(
            skip=0, limit=25, project_type=None, status=None,
            service_line=None, pm_id=None, assigned_user_id=None,
            q=None, current_user=_admin_user(),
            tenant_id="t_test",
        ))
    finally:
        pmod.get_db = original

    assert set(result.keys()) >= {"items", "total", "skip", "limit"}
    assert result["total"] == 101
    assert result["skip"] == 0
    assert result["limit"] == 25
    assert len(result["items"]) == 25


def test_projects_route_paginates_via_skip():
    from routes import projects_project as pmod

    db = _FakeDB(projects=_make_projects(101))
    original = pmod.get_db
    pmod.get_db = lambda: db
    try:
        p1 = asyncio.run(pmod.list_projects(
            skip=0, limit=25, project_type=None, status=None,
            service_line=None, pm_id=None, assigned_user_id=None,
            q=None, current_user=_admin_user(),
            tenant_id="t_test",
        ))
        p5 = asyncio.run(pmod.list_projects(
            skip=100, limit=25, project_type=None, status=None,
            service_line=None, pm_id=None, assigned_user_id=None,
            q=None, current_user=_admin_user(),
            tenant_id="t_test",
        ))
    finally:
        pmod.get_db = original

    assert p1["total"] == p5["total"] == 101
    # Last page should contain the single remaining row (101 - 100).
    assert len(p5["items"]) == 1


def test_projects_route_preserves_picker_limit_500():
    """The Time Management Quick Add picker calls
    /api/projects/projects?limit=500. The route caps internally at
    500; passing 500 must return up to 500 rows in a single page."""
    from routes import projects_project as pmod

    db = _FakeDB(projects=_make_projects(300))
    original = pmod.get_db
    pmod.get_db = lambda: db
    try:
        result = asyncio.run(pmod.list_projects(
            skip=0, limit=500, project_type=None, status=None,
            service_line=None, pm_id=None, assigned_user_id=None,
            q=None, current_user=_admin_user(),
            tenant_id="t_test",
        ))
    finally:
        pmod.get_db = original

    assert result["total"] == 300
    assert result["limit"] == 500
    assert len(result["items"]) == 300


def test_projects_route_caps_limit_at_500():
    """A caller asking for more than 500 must be capped silently —
    the cap is documented internally in the route and pins the
    expected behaviour from upstream callers."""
    from routes import projects_project as pmod

    db = _FakeDB(projects=_make_projects(800))
    original = pmod.get_db
    pmod.get_db = lambda: db
    try:
        result = asyncio.run(pmod.list_projects(
            skip=0, limit=10_000, project_type=None, status=None,
            service_line=None, pm_id=None, assigned_user_id=None,
            q=None, current_user=_admin_user(),
            tenant_id="t_test",
        ))
    finally:
        pmod.get_db = original

    assert result["limit"] == 500
    assert len(result["items"]) == 500
    assert result["total"] == 800  # total is the unbounded count


def test_contracts_route_emits_derived_pagination_fields():
    """page / pages / has_next / has_prev are additive fields the
    May 22, 2026 stabilization review asked for. Skip / limit stay
    so the existing PaginationControls consumer keeps working."""
    from routes import contracts as contracts_mod

    db = _FakeDB(contracts=_make_contracts(57))
    original = contracts_mod.get_db
    contracts_mod.get_db = lambda: db
    try:
        page1 = asyncio.run(contracts_mod.get_contracts(
            skip=0, limit=25, contract_type=None, status=None,
            account_id=None, test_only=False, current_user=_admin_user(),
        ))
        page3 = asyncio.run(contracts_mod.get_contracts(
            skip=50, limit=25, contract_type=None, status=None,
            account_id=None, test_only=False, current_user=_admin_user(),
        ))
    finally:
        contracts_mod.get_db = original

    for key in ("page", "pages", "has_prev", "has_next"):
        assert key in page1
    assert page1["page"] == 1
    assert page1["pages"] == 3  # ceil(57 / 25)
    assert page1["has_prev"] is False
    assert page1["has_next"] is True

    assert page3["page"] == 3
    assert page3["has_prev"] is True
    assert page3["has_next"] is False


def test_projects_route_emits_derived_pagination_fields():
    from routes import projects_project as pmod

    db = _FakeDB(projects=_make_projects(101))
    original = pmod.get_db
    pmod.get_db = lambda: db
    try:
        p1 = asyncio.run(pmod.list_projects(
            skip=0, limit=50, project_type=None, status=None,
            service_line=None, pm_id=None, assigned_user_id=None,
            q=None, current_user=_admin_user(),
            tenant_id="t_test",
        ))
        p_last = asyncio.run(pmod.list_projects(
            skip=100, limit=50, project_type=None, status=None,
            service_line=None, pm_id=None, assigned_user_id=None,
            q=None, current_user=_admin_user(),
            tenant_id="t_test",
        ))
    finally:
        pmod.get_db = original

    assert p1["page"] == 1
    assert p1["pages"] == 3  # ceil(101/50)
    assert p1["has_prev"] is False
    assert p1["has_next"] is True

    assert p_last["page"] == 3
    assert p_last["has_prev"] is True
    assert p_last["has_next"] is False


def test_projects_route_filters_by_status():
    """Imported projects land status='planning'. Filtering by
    status=active must return zero of them."""
    from routes import projects_project as pmod

    projects = _make_projects(50)  # all planning by default
    projects.append({"project_id": "p_active", "name": "Real Active",
                     "project_type": "waterfall", "service_line": "other",
                     "status": "active", "tenant_id": "t_test"})
    db = _FakeDB(projects=projects)
    original = pmod.get_db
    pmod.get_db = lambda: db
    try:
        active = asyncio.run(pmod.list_projects(
            skip=0, limit=25, project_type=None, status="active",
            service_line=None, pm_id=None, assigned_user_id=None,
            q=None, current_user=_admin_user(),
            tenant_id="t_test",
        ))
    finally:
        pmod.get_db = original

    assert active["total"] == 1
    assert active["items"][0]["project_id"] == "p_active"
