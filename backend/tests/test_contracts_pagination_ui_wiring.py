"""Source-level wiring contract for the Contracts list pagination.

The Contracts page (``frontend/src/components/Contracts.js``) was
previously fetching ``/api/contracts`` with no pagination at all, so
every contract returned by the backend default (capped at 500) was
rendered in one scroll. Once the Zoho importer landed 94 imported
contracts, the page was unusable.

This test pins the pagination wiring: the component imports the
shared ``PaginationControls`` and sends both ``skip`` and ``limit``
to the backend. yarn / node_modules are unavailable in the sandbox,
so the assertion is on the source string — same pattern every other
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


def test_contracts_page_imports_pagination_controls():
    src = _component_src()
    assert "PaginationControls" in src, (
        "Contracts.js must import the shared PaginationControls "
        "component from common/PaginationControls."
    )
    assert "common/PaginationControls" in src


def test_contracts_page_sends_skip_and_limit():
    src = _component_src()
    # The route accepts skip+limit, and the page builds those into a
    # URLSearchParams. Pin both names so a future "skip=..." string
    # concat doesn't regress to a no-pagination call.
    assert "contractsParams.set('skip'" in src
    assert "contractsParams.set('limit'" in src


def test_contracts_page_reads_total_from_response_envelope():
    src = _component_src()
    # PaginationControls renders "Showing X-Y of Z" from the backend
    # `total` field. The component must read that value.
    assert "setContractsTotal" in src
    assert "raw.total" in src or "data.total" in src


def test_contracts_page_resets_to_first_page_when_filters_change():
    src = _component_src()
    # The filter-change effect must call setPage(1). We assert via
    # the literal `setPage(1)` since the component has no other path
    # that needs page=1 specifically.
    assert "setPage(1)" in src
