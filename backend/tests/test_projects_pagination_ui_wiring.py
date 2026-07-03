"""Source-level wiring contract for the Projects list pagination.

The Projects tab (``frontend/src/components/projects/ProjectsTab.js``)
used an "infinite-load via visibleRows" pattern that re-fetched
0..visibleRows on each click. With 101 imported Zoho projects the
page became unwieldy. This slice replaces visibleRows with real
server-side pagination via skip+limit and the shared
``PaginationControls`` component.

The Time Management Quick Add picker calls
``/api/projects/projects?limit=500`` directly and must keep working;
``test_projects_route_preserves_picker_limit_500`` in
``test_list_routes_pagination_envelope.py`` covers the backend side.
"""
from pathlib import Path

import pytest


def _component_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend" / "src" / "components" / "projects" / "ProjectsTab.js"
    )
    if not path.exists():  # pragma: no cover — sandbox without frontend
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def test_projects_tab_imports_pagination_controls():
    src = _component_src()
    assert "PaginationControls" in src
    assert "common/PaginationControls" in src


def test_projects_tab_sends_skip_and_limit():
    src = _component_src()
    assert "params.set('limit'" in src
    assert "params.set('skip'" in src


def test_projects_tab_resets_page_on_filter_change():
    src = _component_src()
    assert "setPage(1)" in src


def test_projects_tab_drops_visible_rows_load_more_pattern():
    """The old infinite-load via visibleRows / Load more must be
    fully gone — leaving it behind would dual-control the page state."""
    src = _component_src()
    assert "setVisibleRows" not in src, (
        "ProjectsTab.js must not call setVisibleRows; pagination is "
        "now driven by skip+limit through PaginationControls."
    )
    assert "data-testid=\"load-more\"" not in src, (
        "ProjectsTab.js must not render the legacy Load-more button."
    )


def test_projects_tab_picker_limit_unchanged():
    """The picker callers (TimeManagement.js) keep calling with
    limit=500. The Projects list's own default page size is
    independent — confirm both stay distinct in code."""
    src = _component_src()
    # The page itself uses pageSize state, NOT a hardcoded limit=500.
    assert "DEFAULT_PAGE_SIZE = 25" in src
    # The picker comment is the canonical hook for documenting the
    # independence; keep the explanation visible to future readers.
    assert "Time Management" in src or "limit=500" in src
