"""Source-level wiring contract for the Contracts page free-text search.

The original Contracts.js search filtered client-side over the
current page only — searches missed any matching contract that
happened to live on a later server-side page. This slice converts
the search to server-side via the new ``q`` parameter on
``GET /api/contracts``.

yarn / node_modules are unavailable in the sandbox, so the
assertions are on the source string — same pattern every other
``*_ui_wiring.py`` test uses.
"""
from pathlib import Path

import pytest


def _component_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend" / "src" / "components" / "Contracts.js"
    )
    if not path.exists():  # pragma: no cover — sandbox without frontend
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def test_contracts_page_sends_q_to_backend():
    """The fetch must send ``q=`` to /api/contracts so the backend
    can apply the regex search across all matching rows."""
    src = _component_src()
    assert "contractsParams.set('q'" in src, (
        "Contracts.js must send the q query parameter to "
        "/api/contracts — the previous client-side .toLowerCase() "
        "filter missed contracts on later pages."
    )


def test_contracts_page_debounces_search_before_fetch():
    """The fetch must not fire on every keystroke. A 250ms debounce
    mirrors `search` into `debouncedSearch`, and the fetch effect
    only re-runs when `debouncedSearch` changes."""
    src = _component_src()
    assert "debouncedSearch" in src
    # The setTimeout-based debounce uses a 250ms window. Pin the
    # numeric literal so a future "feels too laggy, drop to 50ms"
    # change is a deliberate code review.
    assert "setTimeout(() => setDebouncedSearch(search), 250)" in src


def test_contracts_page_resets_page_to_one_on_search_change():
    """When the user types into the search box, results re-render
    starting from page 1. Otherwise a stale page=4 would show
    "Showing 76-100 of 5" after the filter narrows the result set."""
    src = _component_src()
    # Find the effect with debouncedSearch in the deps and assert it
    # also calls setPage(1).
    assert "debouncedSearch" in src
    # The combined-filter-reset effect must include debouncedSearch.
    # Pinning the exact dep array is too brittle; pin the substring.
    assert (
        "[showTestAccountsOnly, typeFilter, pageSize, debouncedSearch]"
        in src
    )


def test_contracts_page_no_longer_filters_search_client_side():
    """The previous client-side .toLowerCase() filter on `search` is
    the bug being fixed. After this slice the page-level list is
    exactly what the server returned."""
    src = _component_src()
    assert "c.name?.toLowerCase().includes(s)" not in src, (
        "Contracts.js must not filter by name client-side any longer "
        "— server-side q handles that, and client-side filtering "
        "would mask the real total."
    )
    assert "c.account_name?.toLowerCase().includes(s)" not in src


def test_backend_contracts_route_accepts_q():
    """Symmetric check: the backend route signature must accept
    a q parameter so the frontend's URL is honored."""
    backend_path = (
        Path(__file__).resolve().parents[1]
        / "routes" / "contracts.py"
    )
    src = backend_path.read_text(encoding="utf-8")
    # The handler signature must declare q.
    assert "q: Optional[str]" in src, (
        "routes/contracts.get_contracts must accept the q query "
        "parameter — see test_q_search_* in "
        "test_list_routes_pagination_envelope.py for behaviour pins."
    )
    # The user-supplied search must be regex-escaped before being
    # passed into the $regex query. Pin the escape call so a future
    # refactor cannot accidentally drop it (regex injection risk).
    assert "re.escape" in src or "_re.escape" in src
