"""Source + behavior locks for the PR #228 security review blockers.

Each test pins one of the seven blockers raised on PR #228 so the
fix cannot regress silently. Failures here mean the merge is unsafe
to re-land.

Index:

  B1. ``contracts.py`` every @router.* carries
      ``require_ai_permission``; chosen permission keys match the
      route's human has_perm intent.

  B2. ``admin.py`` GET /admin/contract-approval-access carries
      ``require_ai_permission``; the contract_approval_access service
      import has been hoisted to module-level.

  B3. ``nova.py`` GET /nova/cashflow-planner carries
      ``require_ai_permission``; the inline ``require_perm`` check is
      preserved; the file ends in a newline.

  B4. ``contracts.py`` POST /contracts/recalculate-all-financials
      rejects plain ``contracts.edit`` callers, admits root admins,
      uses ``get_contract_filter`` (no global ``find({})``), and runs
      via ``bulk_write`` batches (no per-row ``update_one`` loop).

  B5. ``contracts.py`` GET /contracts caps over-large ``limit`` and
      echoes the capped value, not the raw request value.

  B6. ``operations_contracts.py`` GET /operations/contracts/summary
      composes ``get_contract_filter`` into the query so totals only
      reflect contracts the caller could see in /api/contracts.

  B7. ``operations_contracts.py`` GET /operations/contracts/hours
      composes ``get_contract_filter`` into the base query so items,
      total, total_unfiltered, summary, and health_counts are
      visibility-scoped — and the existing filter order is preserved
      (visibility -> exact filters -> q -> classification -> health
      -> summary -> pagination).
"""
from __future__ import annotations

import asyncio
import inspect
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production",
)


_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


ROUTES_DIR = _BACKEND / "routes"
CONTRACTS_PATH = ROUTES_DIR / "contracts.py"
ADMIN_PATH = ROUTES_DIR / "admin.py"
NOVA_PATH = ROUTES_DIR / "nova.py"
OPS_CONTRACTS_PATH = ROUTES_DIR / "operations_contracts.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _route_blocks(source: str) -> list[str]:
    """Return one block of text per ``@router.*`` decorator + its
    handler signature. Used by every B1 / B2 / B3 source-level test
    so the assertion can scope to the block under inspection."""
    # Split on @router. so each block starts with the decorator and
    # runs up to (but not including) the next decorator/EOF.
    parts = re.split(r"(?m)^@router\.", source)
    # parts[0] is everything before the first decorator; drop it.
    return ["@router." + p for p in parts[1:]]


def _block_for(source: str, http_method: str, path: str) -> str | None:
    """Find the @router.<method>('<path>') block in source."""
    for block in _route_blocks(source):
        # The decorator opens with @router.<method>( and the path
        # appears as the first quoted argument on the same or next line.
        head = block.splitlines()[:5]
        head_text = "\n".join(head)
        if f"@router.{http_method.lower()}(" not in head_text:
            continue
        if f'"{path}"' not in head_text and f"'{path}'" not in head_text:
            continue
        return block
    return None


# ---------------------------------------------------------------------------
# B1. contracts.py — AI authz on every route
# ---------------------------------------------------------------------------


def test_b1_contracts_imports_require_ai_permission():
    src = CONTRACTS_PATH.read_text(encoding="utf-8")
    assert "from middleware.ai_authz import require_ai_permission" in src, (
        "contracts.py must import require_ai_permission at module level."
    )


def test_b1_every_contracts_router_route_has_ai_authz_dependency():
    """Every @router.* route in contracts.py must declare a
    ``dependencies=[Depends(require_ai_permission(...))]`` entry.
    The decorator is mandatory for AI-caller auth — the in-handler
    has_perm check is for humans only.
    """
    src = CONTRACTS_PATH.read_text(encoding="utf-8")
    blocks = _route_blocks(src)
    assert blocks, "contracts.py has no @router.* routes — file moved?"
    missing: list[str] = []
    for block in blocks:
        head = "\n".join(block.splitlines()[:8])
        if "require_ai_permission(" not in head:
            # Surface just the decorator line + opening paren context so
            # the failure message is actionable.
            missing.append(head.splitlines()[0])
    assert not missing, (
        "contracts.py @router routes missing require_ai_permission "
        "dependency:\n  " + "\n  ".join(missing)
    )


@pytest.mark.parametrize(
    "method,path,expected_key",
    [
        ("get",    "/contracts",                                              "contracts.view"),
        ("post",   "/contracts",                                              "contracts.create"),
        ("get",    "/contracts/warnings",                                     "contracts.view.all"),
        ("get",    "/contracts/closed-won-deals",                             "contracts.view"),
        ("get",    "/contracts/deal-pipeline",                                "contracts.view"),
        ("post",   "/contracts/import-deal",                                  "contracts.edit"),
        ("get",    "/contracts/approval-queue",                               "contracts.approve"),
        ("post",   "/contracts/{contract_id}/submit-for-approval",            "contracts.edit"),
        ("post",   "/contracts/{contract_id}/approve",                        "contracts.approve"),
        ("post",   "/contracts/{contract_id}/reject",                         "contracts.approve"),
        ("post",   "/contracts/{contract_id}/request-changes",                "contracts.approve"),
        ("post",   "/contracts/{contract_id}/projects",                       "contracts.edit"),
        ("delete", "/contracts/{contract_id}/projects/{project_id}",          "contracts.edit"),
        ("get",    "/contracts/{contract_id}",                                "contracts.view"),
        ("put",    "/contracts/{contract_id}",                                "contracts.edit"),
        ("delete", "/contracts/{contract_id}",                                "contracts.delete"),
        ("post",   "/contracts/{contract_id}/deals",                          "contracts.edit"),
        ("delete", "/contracts/{contract_id}/deals/{deal_id}",                "contracts.edit"),
        ("post",   "/contracts/{contract_id}/milestones/{milestone_id}/complete", "contracts.edit"),
        ("post",   "/contracts/{contract_id}/adjust-hours",                   "contracts.edit"),
        ("post",   "/contracts/{contract_id}/recalculate-financials",         "contracts.edit"),
        ("post",   "/contracts/recalculate-all-financials",                   "contracts.recalculate_all"),
        ("get",    "/contracts/{contract_id}/approver-overrides",             "contracts.approve"),
        ("post",   "/contracts/{contract_id}/approver-overrides",             "users.edit"),
        ("delete", "/contracts/{contract_id}/approver-overrides/{user_id}",   "users.edit"),
    ],
)
def test_b1_contracts_routes_use_expected_permission_key(method, path, expected_key):
    """Each contracts.py route must declare the permission key that
    matches its existing human authorization intent (list/read →
    view, create → create, edit → edit, delete → delete, approval
    → approve, override admin → users.edit, recalc-all → admin)."""
    src = CONTRACTS_PATH.read_text(encoding="utf-8")
    block = _block_for(src, method, path)
    assert block, f"Cannot find route block for {method.upper()} {path}"
    head = "\n".join(block.splitlines()[:8])
    assert f'require_ai_permission("{expected_key}")' in head, (
        f"{method.upper()} {path} must gate AI access with "
        f"require_ai_permission({expected_key!r}). Decorator head:\n{head}"
    )


# ---------------------------------------------------------------------------
# B2. admin.py — /contract-approval-access AI authz + import hoist
# ---------------------------------------------------------------------------


def test_b2_admin_imports_require_ai_permission():
    src = ADMIN_PATH.read_text(encoding="utf-8")
    assert "from middleware.ai_authz import require_ai_permission" in src


def test_b2_contract_approval_access_has_ai_authz_dependency():
    src = ADMIN_PATH.read_text(encoding="utf-8")
    block = _block_for(src, "get", "/contract-approval-access")
    assert block, "GET /contract-approval-access route block not found"
    head = "\n".join(block.splitlines()[:8])
    assert 'require_ai_permission("admin.users.view")' in head, (
        "GET /admin/contract-approval-access must gate AI access with "
        "require_ai_permission('admin.users.view'). Decorator head:\n" + head
    )


def test_b2_contract_approval_access_keeps_human_users_view_gate():
    """Decorator is for AI callers; humans still gated by has_perm."""
    src = ADMIN_PATH.read_text(encoding="utf-8")
    block = _block_for(src, "get", "/contract-approval-access")
    assert block is not None
    assert 'has_perm(current_user, "users.view")' in block, (
        "The human-side users.view gate must remain inside the handler "
        "body — the AI authz decorator does not replace it."
    )


def test_b2_contract_approval_access_imports_hoisted_to_module():
    """The contract_approval_access helper imports must live at the
    top of the file, not inside the handler body."""
    src = ADMIN_PATH.read_text(encoding="utf-8")
    # Module-level import present (matches multi-line import too).
    assert "from services.contract_approval_access import" in src
    # The handler body must NOT re-import — that was the symptom the
    # blocker called out. We detect a re-import by scanning for the
    # in-function-indented form.
    body_reimport = re.search(
        r"^[ \t]+from services\.contract_approval_access import",
        src,
        re.MULTILINE,
    )
    assert body_reimport is None, (
        "Found in-handler re-import of services.contract_approval_access; "
        "this should live at module top instead."
    )


# ---------------------------------------------------------------------------
# B3. nova.py — /cashflow-planner AI authz + EOF newline
# ---------------------------------------------------------------------------


def test_b3_nova_imports_require_ai_permission():
    src = NOVA_PATH.read_text(encoding="utf-8")
    assert "from middleware.ai_authz import require_ai_permission" in src


def test_b3_cashflow_planner_has_ai_authz_dependency():
    src = NOVA_PATH.read_text(encoding="utf-8")
    block = _block_for(src, "get", "/cashflow-planner")
    assert block, "GET /cashflow-planner route block not found"
    head = "\n".join(block.splitlines()[:8])
    assert 'require_ai_permission("finance.cashflow.view")' in head, (
        "GET /nova/cashflow-planner must gate AI access with "
        "require_ai_permission('finance.cashflow.view'). "
        "Decorator head:\n" + head
    )


def test_b3_cashflow_planner_keeps_inline_require_perm():
    """The decorator only enforces AI auth — the inline await
    require_perm(...) must remain for human callers + audit."""
    src = NOVA_PATH.read_text(encoding="utf-8")
    block = _block_for(src, "get", "/cashflow-planner")
    assert block is not None
    assert 'require_perm(' in block and '"finance.cashflow.view"' in block, (
        "The inline await require_perm(..., 'finance.cashflow.view', ...) "
        "check must remain inside the handler body."
    )


def test_b3_nova_file_ends_with_newline():
    raw = NOVA_PATH.read_bytes()
    assert raw.endswith(b"\n"), "nova.py must end with a newline."


# ---------------------------------------------------------------------------
# B4. recalculate-all-financials — admin-only + scoped + bulk_write
# ---------------------------------------------------------------------------


def test_b4_recalc_all_rejects_plain_contracts_edit_user():
    """A user with contracts.edit but no admin permission must
    receive 403. Pre-fix the gate accepted contracts.edit, which
    let a sales rep with edit-own trigger a database-wide fan-out."""
    import routes.contracts as rc
    from fastapi import HTTPException

    class _U:
        user_id = "u_editor"
        email = "editor@example.com"
        role = "sales_rep"
        permissions = ["contracts.edit"]
        effective_permissions = ["contracts.edit"]
        account_id = None

    user = _U()
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(rc.recalculate_all_contract_financials(current_user=user))
    assert excinfo.value.status_code == 403


def test_b4_recalc_all_admits_root_admin_and_uses_bulk_write():
    """Admin caller succeeds; the implementation uses bulk_write
    against the contracts collection (not a per-row update_one
    loop)."""
    import routes.contracts as rc

    class _FakeCursor:
        def __init__(self, docs): self._docs = list(docs)
        def __aiter__(self): self._it = iter(self._docs); return self
        async def __anext__(self):
            try: return next(self._it)
            except StopIteration: raise StopAsyncIteration

    class _FakeContracts:
        def __init__(self, docs):
            self.docs = list(docs)
            self.bulk_write_calls: list = []
            self.update_one_calls: list = []
            self.find_queries: list = []
        def find(self, query, _proj=None):
            self.find_queries.append(query)
            return _FakeCursor(self.docs)
        async def bulk_write(self, ops, ordered=False):
            self.bulk_write_calls.append((list(ops), ordered))
            return SimpleNamespace(modified_count=len(ops))
        async def update_one(self, *a, **kw):
            self.update_one_calls.append((a, kw))

    class _FakeDB:
        def __init__(self, docs): self.contracts = _FakeContracts(docs)

    db = _FakeDB([
        {"contract_id": "c1", "name": "A", "contract_type": "boh"},
        {"contract_id": "c2", "name": "B", "contract_type": "fixed_fee"},
    ])

    class _Admin:
        user_id = "u_admin"
        email = "admin@example.com"
        role = "admin"
        permissions = ["*"]
        effective_permissions = ["*"]
        account_id = None

    orig_db = rc.get_db
    rc.get_db = lambda: db
    try:
        result = asyncio.run(
            rc.recalculate_all_contract_financials(current_user=_Admin()),
        )
    finally:
        rc.get_db = orig_db

    assert result["success"] is True
    assert result["total_contracts"] == 2
    # B4 requires bulk_write; the prior per-row update_one loop is gone.
    assert db.contracts.bulk_write_calls, (
        "recalculate-all must use bulk_write batches, not update_one loops."
    )
    assert not db.contracts.update_one_calls, (
        "recalculate-all must not call update_one per row."
    )


def test_b4_recalc_all_scopes_query_via_get_contract_filter():
    """An admin call with no extra context must not issue
    ``find({})`` against the whole collection — the implementation
    must compose ``get_contract_filter`` (returns ``{}`` for full-
    admin, a constrained predicate for scoped roles). We assert by
    inspecting the function source: a literal ``find({})`` would be
    the regression."""
    import ast
    import routes.contracts as rc
    src = inspect.getsource(rc.recalculate_all_contract_financials)
    # Strip the docstring so docstring prose mentioning the legacy
    # ``find({})`` shape doesn't trigger a false positive.
    tree = ast.parse(src)
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)
            and isinstance(fn.body[0].value.value, str)):
        fn.body = fn.body[1:]
    code_only = ast.unparse(fn)

    assert "get_contract_filter" in code_only, (
        "recalculate-all-financials must compose get_contract_filter "
        "into its query."
    )
    assert "find({}" not in code_only, (
        "recalculate-all-financials must NOT issue find({}) — that "
        "rewrites every contract in the database regardless of "
        "caller visibility."
    )
    assert "to_list(10000)" not in code_only, (
        "recalculate-all-financials must NOT load the whole result "
        "set into memory; use a streaming cursor instead."
    )


# ---------------------------------------------------------------------------
# B5. GET /contracts — limit cap echo
# ---------------------------------------------------------------------------


def test_b5_get_contracts_echoes_capped_limit_when_request_exceeds_500():
    """A caller asking for limit=1000 must receive limit=500 in the
    response envelope (the cursor cap) and at most 500 items."""
    import routes.contracts as rc

    class _Cursor:
        def __init__(self, docs): self._docs = list(docs)
        def sort(self, *a, **kw): return self
        def skip(self, *a, **kw): return self
        def limit(self, n): self._docs = self._docs[:n]; return self
        async def to_list(self, n): return self._docs[:n]

    class _Contracts:
        def __init__(self, docs): self.docs = list(docs)
        def find(self, *a, **kw): return _Cursor(self.docs)
        async def count_documents(self, *a, **kw): return len(self.docs)

    class _DB:
        def __init__(self, docs): self.contracts = _Contracts(docs)

    # 700 rows ensures even the capped page is filled and there is
    # a real next page to skip into.
    rows = [{"contract_id": f"c{i:03d}"} for i in range(700)]
    db = _DB(rows)

    class _Admin:
        user_id = "u"
        email = "a"
        role = "admin"
        permissions = ["*"]
        effective_permissions = ["*"]
        account_id = None

    orig_db = rc.get_db
    rc.get_db = lambda: db
    try:
        resp = asyncio.run(rc.get_contracts(limit=1000, current_user=_Admin()))
    finally:
        rc.get_db = orig_db

    assert resp["limit"] == 500, (
        f"limit=1000 must be capped to 500 in the response envelope, "
        f"got {resp['limit']}"
    )
    assert len(resp["items"]) <= 500


# ---------------------------------------------------------------------------
# B6 + B7. operations_contracts — get_contract_filter composition
# ---------------------------------------------------------------------------


def test_b6_summary_composes_get_contract_filter():
    """The operations summary endpoint must compose the same
    visibility helper /api/contracts uses. Source-level lock so a
    refactor cannot quietly go back to ``find({})``."""
    import ast
    from routes import operations_contracts as oc

    src = OPS_CONTRACTS_PATH.read_text(encoding="utf-8")
    assert "from auth_helper import" in src and "get_contract_filter" in src

    # Look at the function's actual code (not docstrings/comments) so
    # prose mentioning the legacy ``find({})`` shape doesn't match.
    fn_src = inspect.getsource(oc.get_operations_contracts_summary)
    tree = ast.parse(fn_src)
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)
            and isinstance(fn.body[0].value.value, str)):
        fn.body = fn.body[1:]
    code_only = ast.unparse(fn)
    assert "find({}" not in code_only, (
        "GET /operations/contracts/summary must not call find({})."
    )
    assert "_compose_with_visibility" in code_only or "get_contract_filter" in code_only, (
        "GET /operations/contracts/summary must compose get_contract_filter."
    )


def test_b7_hours_composes_get_contract_filter_before_filters():
    """The hours endpoint must apply caller visibility BEFORE the
    cheap exact-match filters so total / total_unfiltered /
    health_counts are visibility-scoped. Source check confirms the
    composition is present and lands before the to_list call."""
    src = OPS_CONTRACTS_PATH.read_text(encoding="utf-8")
    hours_match = re.search(
        r"async def get_operations_contracts_hours\b.*?(?=\nasync def |\n@router|\Z)",
        src,
        re.DOTALL,
    )
    assert hours_match, "Cannot locate get_operations_contracts_hours body"
    body = hours_match.group(0)
    assert "_compose_with_visibility" in body or "get_contract_filter" in body, (
        "GET /operations/contracts/hours must compose get_contract_filter "
        "into its base query."
    )
    # The compose call must precede the to_list call so the
    # visibility scope makes it onto the cursor.
    compose_idx = body.find("_compose_with_visibility")
    if compose_idx < 0:
        compose_idx = body.find("get_contract_filter")
    tolist_idx = body.find(".to_list(20000)")
    assert compose_idx >= 0 and tolist_idx >= 0, (
        "Both the visibility composition and the to_list(20000) must "
        "be present in the hours handler body."
    )
    assert compose_idx < tolist_idx, (
        "Visibility composition must run BEFORE the to_list cursor — "
        "otherwise total / total_unfiltered / health_counts include "
        "contracts the caller cannot see."
    )


def test_b7_hours_items_exclude_invisible_contracts_for_scoped_caller():
    """Behaviour lock: a sales rep with contracts.view.own only
    sees contracts whose assigned_sales_rep_id matches their
    user_id (or whose account_id matches their account_id). The
    hours endpoint must honour that — pre-fix it returned every
    contract regardless of caller."""
    from routes import operations_contracts as oc

    class _Cursor:
        def __init__(self, docs): self._docs = list(docs)
        async def to_list(self, _n): return list(self._docs)

    class _Contracts:
        def __init__(self, docs): self.docs = list(docs)
        def find(self, query, _proj=None):
            return _Cursor(_match_all(self.docs, query or {}))

    class _DB:
        def __init__(self, docs): self.contracts = _Contracts(docs)

    docs = [
        # Visible to rep_a — account match.
        {"contract_id": "v1", "name": "Visible",
         "contract_type": "boh", "status": "active",
         "is_test_data": False,
         "account_id": "acct_a", "assigned_sales_rep_id": "other",
         "total_hours": 100, "hours_consumed": 10,
         "remaining_hours": 90, "consumed_hours_percent": 10},
        # Visible to rep_a — sales-rep match.
        {"contract_id": "v2", "name": "Visible-Rep",
         "contract_type": "boh", "status": "active",
         "is_test_data": False,
         "account_id": "acct_other", "assigned_sales_rep_id": "rep_a",
         "total_hours": 100, "hours_consumed": 50,
         "remaining_hours": 50, "consumed_hours_percent": 50},
        # NOT visible — different account + different rep.
        {"contract_id": "x1", "name": "Hidden",
         "contract_type": "boh", "status": "active",
         "is_test_data": False,
         "account_id": "acct_other", "assigned_sales_rep_id": "other",
         "total_hours": 100, "hours_consumed": 110,
         "remaining_hours": -10, "consumed_hours_percent": 110},
    ]
    db = _DB(docs)

    # A sales rep — only sees contracts assigned to them OR matching
    # their account_id. Matches get_contract_filter's "scoped" branch.
    # Rep needs operations.contracts.view to pass the route gate AND
    # contracts.view.own so get_contract_filter narrows the visible set.
    rep_perms = ["operations.contracts.view", "contracts.view.own"]
    rep = SimpleNamespace(
        user_id="rep_a", role="sales_rep", email="rep@example.com", name="R",
        permissions=rep_perms,
        effective_permissions=rep_perms,
        account_id="acct_a",
        tenant_id=None,
    )

    orig_db = oc.get_db
    oc.get_db = lambda: db
    try:
        resp = asyncio.run(oc.get_operations_contracts_hours(
            current_user=rep,
            q=None, contract_type=None, status=None, health=None,
            page=1, page_size=50,
        ))
    finally:
        oc.get_db = orig_db

    ids = {it["contract_id"] for it in resp["items"]}
    assert ids == {"v1", "v2"}, (
        f"Hidden contract leaked through visibility scope: items={ids!r}"
    )
    assert resp["total"] == 2
    assert resp["total_unfiltered"] == 2
    assert sum(resp["summary"]["health_counts"].values()) == resp["total"]


def test_b7_hours_q_search_cannot_leak_invisible_contracts():
    """q='Hidden' must not return the hidden contract for a scoped
    caller — visibility runs before q, so the hidden contract is
    already out of the candidate set."""
    from routes import operations_contracts as oc

    class _Cursor:
        def __init__(self, docs): self._docs = list(docs)
        async def to_list(self, _n): return list(self._docs)

    class _Contracts:
        def __init__(self, docs): self.docs = list(docs)
        def find(self, query, _proj=None):
            return _Cursor(_match_all(self.docs, query or {}))

    class _DB:
        def __init__(self, docs): self.contracts = _Contracts(docs)

    docs = [
        {"contract_id": "v1", "name": "VisibleAlpha",
         "contract_type": "boh", "status": "active", "is_test_data": False,
         "account_id": "acct_a", "assigned_sales_rep_id": "other",
         "total_hours": 100, "hours_consumed": 10,
         "remaining_hours": 90, "consumed_hours_percent": 10},
        {"contract_id": "x1", "name": "HiddenAlpha",
         "contract_type": "boh", "status": "active", "is_test_data": False,
         "account_id": "acct_other", "assigned_sales_rep_id": "other",
         "total_hours": 100, "hours_consumed": 50, "remaining_hours": 50,
         "consumed_hours_percent": 50},
    ]
    db = _DB(docs)

    # Rep needs operations.contracts.view to pass the route gate AND
    # contracts.view.own so get_contract_filter narrows the visible set.
    rep_perms = ["operations.contracts.view", "contracts.view.own"]
    rep = SimpleNamespace(
        user_id="rep_a", role="sales_rep", email="rep@example.com", name="R",
        permissions=rep_perms,
        effective_permissions=rep_perms,
        account_id="acct_a",
        tenant_id=None,
    )

    orig_db = oc.get_db
    oc.get_db = lambda: db
    try:
        resp = asyncio.run(oc.get_operations_contracts_hours(
            current_user=rep, q="Alpha",
            contract_type=None, status=None, health=None,
            page=1, page_size=50,
        ))
    finally:
        oc.get_db = orig_db

    assert {it["contract_id"] for it in resp["items"]} == {"v1"}, (
        "q='Alpha' must not leak the hidden contract through the search."
    )


# ---------------------------------------------------------------------------
# Tiny query matcher (fake Mongo predicate evaluator) used by the
# B7 visibility tests above. Supports the subset the route emits:
# equality, $ne, $or, $and, $in, $exists.
# ---------------------------------------------------------------------------


def _match_all(docs, query):
    return [d for d in docs if _match(d, query)]


def _match(doc, q):
    for k, v in q.items():
        if k == "$and":
            if not all(_match(doc, sub) for sub in v):
                return False
            continue
        if k == "$or":
            if not any(_match(doc, sub) for sub in v):
                return False
            continue
        if isinstance(v, dict):
            if "$ne" in v:
                if doc.get(k) == v["$ne"]:
                    return False
                continue
            if "$in" in v:
                if doc.get(k) not in v["$in"]:
                    return False
                continue
            if "$exists" in v:
                if (k in doc) != bool(v["$exists"]):
                    return False
                continue
            # Unknown operator — fall through to direct compare.
        if doc.get(k) != v:
            return False
    return True
