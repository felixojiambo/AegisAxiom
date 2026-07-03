"""Source-level wiring + algorithm tests for the sidebar
active-state matcher.

May 24, 2026 (revised) UX bug: clicking Operations → Contract
Approvals (URL /contracts/approval-queue) caused BOTH the
"Contracts" item and the "Contract Approvals" item to highlight
in the sidebar. Root cause: NavItem rendered <NavLink end={...}>
without `end` for any item, and React Router NavLink without
`end` matches any pathname that starts with the link's `to` + '/'.
So '/contracts' matched '/contracts/approval-queue' too.

The fix is a longest-match rule implemented in
frontend/src/lib/navActive.js::computeActiveNavPath, computed
once in Layout.js and threaded down to NavItem as a boolean prop.
NavItem no longer reads NavLink's isActive callback for styling.

These tests have two halves:

  (1) Algorithm contract — re-implements computeActiveNavPath in
      Python and pins the seven expected-active-state rows from
      the user's table. The Python re-implementation is the
      readable spec; the JS source is verified via source-level
      assertion in part (2). If the JS algorithm ever drifts
      from this Python copy, the wiring half catches it.

  (2) Source-level wiring — confirms Layout.js actually uses the
      helper, threads the result through SidebarContent and
      NavGroup, drives NavItem's active className from the prop
      (not from NavLink's isActive callback), and auto-opens
      the active group.

yarn / node_modules unavailable in this build sandbox — pure
source inspection on the wiring half, pure Python on the
algorithm half.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def _frontend_root() -> Path:
    return Path(__file__).resolve().parents[1].parent / "frontend" / "src"


def _helper_src() -> str:
    path = _frontend_root() / "lib" / "navActive.js"
    if not path.exists():  # pragma: no cover — sandbox without frontend
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def _layout_src() -> str:
    path = _frontend_root() / "components" / "Layout.js"
    if not path.exists():  # pragma: no cover — sandbox without frontend
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# (1) Algorithm contract — Python re-implementation
# ---------------------------------------------------------------------------
#
# The Python copy below MUST stay byte-for-byte equivalent to
# computeActiveNavPath in navActive.js. If you change the JS, change
# this too — the test fixtures pin both halves to the same answer.


def _py_compute_active_nav_path(items, pathname):
    if not pathname or not isinstance(items, list):
        return None
    if len(pathname) > 1 and pathname.endswith("/"):
        p = pathname[:-1]
    else:
        p = pathname
    best = None
    best_len = -1
    for item in items:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str) or len(path) == 0:
            continue
        matches = (
            p == path
            or p.startswith(path + "/")
            or p.startswith(path + "?")
        )
        if matches and len(path) > best_len:
            best = path
            best_len = len(path)
    return best


# The fixture mirrors the Operations + relevant Admin items declared
# in Layout.js NAV_GROUPS. Order is intentional and irrelevant — the
# matcher chooses by longest path, not by array order.
_NAV_FIXTURE = [
    {"path": "/contracts", "label": "Contracts"},
    {"path": "/contracts/approval-queue", "label": "Contract Approvals"},
    {"path": "/operations/contracts", "label": "Contract Financials"},
    {"path": "/projects", "label": "Projects"},
    {"path": "/time-management", "label": "Time Management"},
    {"path": "/admin/contract-approvers", "label": "Contract Approvers"},
    {"path": "/admin/users", "label": "User & Resource Mgmt"},
    {"path": "/dashboard", "label": "Dashboard"},
]


@pytest.mark.parametrize("pathname,expected_path", [
    # Direct rows from the user's spec table.
    ("/contracts",                       "/contracts"),
    ("/contracts?id=abc-123",            "/contracts"),
    ("/contracts/approval-queue",        "/contracts/approval-queue"),
    ("/operations/contracts",            "/operations/contracts"),
    ("/admin/contract-approvers",        "/admin/contract-approvers"),
    ("/projects/projects",               "/projects"),
    ("/time-management",                 "/time-management"),
    # Extra coverage that flows from the same rule:
    ("/contracts/approval-queue/x",      "/contracts/approval-queue"),
    ("/contracts/approval-queue?id=z",   "/contracts/approval-queue"),
    ("/admin/contract-approvers/edit",   "/admin/contract-approvers"),
    ("/admin/users",                     "/admin/users"),
    ("/dashboard",                       "/dashboard"),
    # Path that no nav item covers — must return None, not partial.
    ("/somewhere-unrelated",             None),
    # Trailing slash normalisation.
    ("/contracts/",                      "/contracts"),
    # Root must not light up anything when no item is at '/'.
    ("/",                                None),
])
def test_compute_active_nav_path_matches_spec_table(pathname, expected_path):
    """The seven rows from the spec table, plus the obvious edge
    cases the algorithm must cover for free."""
    assert _py_compute_active_nav_path(_NAV_FIXTURE, pathname) == expected_path


def test_longest_match_wins_when_two_items_share_prefix():
    """The whole reason this helper exists: when the pathname
    starts with /contracts AND equals /contracts/approval-queue,
    the longer one wins."""
    items = [
        {"path": "/contracts", "label": "Contracts"},
        {"path": "/contracts/approval-queue", "label": "Contract Approvals"},
    ]
    # The bug: BOTH used to be marked active.
    assert _py_compute_active_nav_path(items, "/contracts/approval-queue") == (
        "/contracts/approval-queue"
    )
    # And /contracts alone still wins for /contracts.
    assert _py_compute_active_nav_path(items, "/contracts") == "/contracts"


def test_only_one_match_per_pathname():
    """For any given pathname there is at most one winning path.
    Test by counting matches against the full fixture."""
    for pathname in (
        "/contracts",
        "/contracts/approval-queue",
        "/operations/contracts",
        "/admin/contract-approvers",
        "/projects/projects",
        "/time-management",
    ):
        match = _py_compute_active_nav_path(_NAV_FIXTURE, pathname)
        assert match is not None
        # Sanity: no other fixture path is identical to the winning
        # one, so the "only one match" property holds trivially. The
        # important defence is the longest-match rule itself, pinned
        # in the previous test.
        winners = [it for it in _NAV_FIXTURE if it["path"] == match]
        assert len(winners) == 1


def test_no_match_returns_none_not_empty_string():
    assert _py_compute_active_nav_path(_NAV_FIXTURE, "/no-such-route") is None
    assert _py_compute_active_nav_path([], "/contracts") is None
    assert _py_compute_active_nav_path(_NAV_FIXTURE, "") is None
    assert _py_compute_active_nav_path(_NAV_FIXTURE, None) is None


def test_invalid_items_are_skipped_not_crashing():
    """Defensive: items missing `path`, or with non-string `path`,
    must not crash the matcher — they're skipped."""
    items = [
        {"label": "no path"},
        {"path": None, "label": "null path"},
        {"path": 42, "label": "number path"},
        {"path": "", "label": "empty path"},
        {"path": "/contracts", "label": "real"},
    ]
    assert _py_compute_active_nav_path(items, "/contracts") == "/contracts"


# ---------------------------------------------------------------------------
# (1b) JS source must match the Python re-implementation
# ---------------------------------------------------------------------------


def test_helper_file_exports_compute_active_nav_path():
    src = _helper_src()
    assert "export function computeActiveNavPath(items, pathname)" in src, (
        "navActive.js must export computeActiveNavPath(items, pathname)."
    )


def test_helper_file_exports_find_active_group_id():
    src = _helper_src()
    assert "export function findActiveGroupId(filteredGroups, activePath)" in src, (
        "navActive.js must export findActiveGroupId for the auto-open "
        "useEffect in Layout.js."
    )


def test_helper_uses_longest_match_rule_not_first_match():
    """Pin the algorithm shape: it tracks bestLen and only updates
    when path.length is strictly greater. First-match-wins would
    silently reintroduce the original bug."""
    src = _helper_src()
    assert "bestLen" in src
    assert "path.length > bestLen" in src, (
        "Helper must compare path.length against bestLen so the "
        "LONGEST matching path wins (not the first one seen)."
    )


def test_helper_match_predicate_covers_exact_slash_and_query():
    """The matcher accepts three shapes: exact equality, path+'/'
    prefix (child route), path+'?' prefix (root with query)."""
    src = _helper_src()
    # Pull the matches expression. The triple OR is small enough to
    # match literally with whitespace tolerance.
    normalised = re.sub(r"\s+", " ", src)
    assert (
        "p === path || p.startsWith(path + '/') || p.startsWith(path + '?')"
        in normalised
    ), (
        "Helper match predicate must be exactly: exact equality OR "
        "path+'/' prefix OR path+'?' prefix. Anything broader (e.g. "
        "p.startsWith(path)) reintroduces the parent-route bug."
    )


# ---------------------------------------------------------------------------
# (2) Source-level wiring — Layout.js
# ---------------------------------------------------------------------------


def test_layout_imports_helper():
    src = _layout_src()
    assert (
        "import { computeActiveNavPath, findActiveGroupId } from '@/lib/navActive';"
        in src
    ), (
        "Layout.js must import computeActiveNavPath and "
        "findActiveGroupId from @/lib/navActive."
    )


def test_layout_imports_use_location():
    src = _layout_src()
    assert (
        "import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom';"
        in src
    )


def test_layout_calls_compute_active_nav_path_with_pathname():
    src = _layout_src()
    # The computation lives in a useMemo so it doesn't re-flatten on
    # every render. Pin the call shape and the dependency array.
    assert "computeActiveNavPath(flat, location.pathname)" in src, (
        "Layout.js must call computeActiveNavPath(flat, "
        "location.pathname) inside a useMemo so the active path "
        "stays stable across unrelated re-renders."
    )


def test_layout_auto_opens_group_containing_active_item():
    src = _layout_src()
    # The auto-open effect uses findActiveGroupId + setOpenGroupId.
    assert "findActiveGroupId(filteredGroups, activePath)" in src
    assert "if (groupId) setOpenGroupId(groupId);" in src, (
        "Layout.js must auto-open the group containing the active "
        "nav item so deep-linking to e.g. /contracts/approval-queue "
        "doesn't leave the user staring at a collapsed Operations."
    )


def test_auto_open_effect_depends_only_on_active_path():
    """The auto-open useEffect MUST depend on [activePath] only —
    NOT on [activePath, filteredGroups]. usePermissions returns
    fresh function refs every render, which makes filteredGroups
    a new identity every render, which would re-fire the effect
    on every render and snap openGroupId back to the active
    group's id. That makes manual accordion toggling impossible
    (clicking Sales would immediately snap back to Operations,
    clicking Operations to collapse would immediately re-open).

    The fix: derive the open group from the active path on
    navigation only, then leave the open group entirely under
    the user's manual control via the accordion header clicks."""
    src = _layout_src()
    # The effect's dep array contains exactly activePath. The
    # exact closing `}, [activePath]);` pattern is the lock.
    assert "}, [activePath]);" in src, (
        "Auto-open useEffect must depend on [activePath] alone "
        "so clicks on accordion headers persist."
    )
    # And the bug shape — depending on filteredGroups too — must
    # NOT be in the source.
    assert "}, [activePath, filteredGroups]);" not in src, (
        "Auto-open useEffect must NOT depend on filteredGroups — "
        "that identity changes every render and re-fires the "
        "effect, which prevents the user from toggling accordion "
        "groups manually."
    )


def test_nav_item_uses_isactive_prop_not_navlink_callback():
    """The NavLink className must be a string driven by the
    longest-match prop — NOT the `({ isActive }) => ...` callback
    that caused the original bug."""
    src = _layout_src()
    # The NavItem function signature must declare isActive.
    assert "function NavItem({ item, collapsed, onNavigate, isActive })" in src
    # The old buggy callback shape must be gone from NavItem. (The
    # check is anchored to the className= prop on a NavLink so we
    # don't accidentally trip on isActive references elsewhere.)
    assert "className={({ isActive }) =>" not in src, (
        "NavItem must not use NavLink's isActive callback — it does "
        "prefix matching by default and re-introduces the bug. "
        "Use the longest-match isActive prop instead."
    )
    # And the aria-current attribute is driven by the same prop so
    # screen readers see exactly one current page.
    assert "aria-current={isActive ? 'page' : undefined}" in src


def test_nav_item_no_longer_uses_end_prop_on_navlink():
    """The old `end={item.exact}` was a half-fix that depended on
    every nav item opting in. The longest-match prop replaces it
    and works for all items uniformly."""
    src = _layout_src()
    assert "end={item.exact}" not in src, (
        "NavItem must not rely on `end={item.exact}` — no nav item "
        "in NAV_GROUPS sets `exact: true`, so the prop was always "
        "undefined. Use the longest-match isActive prop instead."
    )


def test_sidebar_content_threads_active_path_through():
    src = _layout_src()
    # SidebarContent signature accepts activePath.
    assert "isRoot,\n  activePath,\n}) {" in src or "isRoot,\n  activePath" in src
    # SidebarContent passes activePath to NavGroup.
    assert "activePath={activePath}" in src
    # Both SidebarContent invocations (desktop + mobile drawer) pass
    # activePath. Count the prop occurrences in the component tree.
    assert src.count("activePath={activePath}") >= 3, (
        "activePath must flow Layout -> SidebarContent (desktop) -> "
        "SidebarContent (mobile drawer) -> NavGroup. Expected at "
        "least 3 occurrences of the prop pass-through."
    )


def test_nav_group_routes_active_path_into_render_item():
    src = _layout_src()
    # NavGroup signature accepts activePath.
    assert "function NavGroup({ group, collapsed, visibleItems, isOpen, onToggle, onNavigate, activePath })" in src
    # And the renderItem closure passes item.path === activePath
    # down to NavItem as isActive.
    assert "isActive={item.path === activePath}" in src


# ---------------------------------------------------------------------------
# (3) Negative locks — the literal bug must not regress
# ---------------------------------------------------------------------------


def test_no_broad_startswith_contracts_used_for_active_state():
    """Defensive sweep — Layout.js must not ever do
    pathname.startsWith('/contracts') as an active-state predicate.
    The longest-match helper is the only sanctioned path."""
    src = _layout_src()
    forbidden_patterns = [
        "pathname.startsWith('/contracts')",
        'pathname.startsWith("/contracts")',
        "pathname.includes('/contracts')",
        'pathname.includes("/contracts")',
        # Same idea for other parent routes.
        "pathname.startsWith('/admin')",
        "pathname.startsWith('/projects')",
    ]
    for pat in forbidden_patterns:
        assert pat not in src, (
            f"Layout.js must not use {pat!r} for active matching — "
            "use computeActiveNavPath instead (longest-match)."
        )


def test_operations_nav_still_contains_all_expected_items():
    """Final guard: nobody removed or renamed Contracts or
    Contract Approvals while wiring the matcher."""
    src = _layout_src()
    # Items are still declared in NAV_GROUPS with the canonical
    # path strings the matcher fixture above expects.
    assert "path: '/contracts'," in src
    assert "path: '/contracts/approval-queue'," in src
    assert "path: '/operations/contracts'," in src
    assert "path: '/admin/contract-approvers'," in src
    assert "path: '/projects'," in src
    assert "path: '/time-management'," in src
