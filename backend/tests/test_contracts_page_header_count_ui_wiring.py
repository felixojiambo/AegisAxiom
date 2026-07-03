"""Source-level wiring contract for the Contracts page header count.

May 24, 2026 (revised) review: the top-of-page header showed the
current-page count ("Contracts 50") instead of the global total
("Contracts 217"), and each type-filter tab showed its own
per-page count, both of which contradicted the pagination footer
on the same page. The pagination envelope already exposes a
truthful `total` and the page already pulls it into `contractsTotal`
— this test pins that contractsTotal is what the header renders
and that the misleading per-page type tab counts are removed.

yarn / node_modules unavailable in sandbox; source inspection.
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


def test_header_contracts_count_uses_backend_total():
    """The 'Contracts <n>' tab in the page-view toggle must read
    contractsTotal (the global total from the pagination envelope),
    not contracts.length (which is only the current page)."""
    src = _component_src()
    # Pin the exact JSX so a regression can't sneak the wrong source
    # back. The match is whitespace-tolerant only in the obvious
    # places.
    assert (
        'Contracts <span className="text-aegisaxiom-text-muted ml-1">{contractsTotal}</span>'
        in src
    ), (
        "Header 'Contracts <n>' must render contractsTotal, not "
        "contracts.length (current page only)."
    )
    # And the old wrong source must be gone.
    assert (
        'Contracts <span className="text-aegisaxiom-text-muted ml-1">{contracts.length}</span>'
        not in src
    )


def test_type_tab_counts_are_not_misleading():
    """Per-type tabs ('All', 'BoH', 'BoA', etc.) used to show
    typeCounts[tab.key], which is the count of items on the
    current page (post-pagination). That makes the numbers contradict
    the pagination footer on a 217-item list paged at 50/page.
    The All tab can show contractsTotal (truthful); per-type tabs
    have no truthful number available client-side and therefore
    must not render a count."""
    src = _component_src()
    # The misleading source must be gone.
    assert "typeCounts[tab.key]" not in src
    # And the typeCounts useMemo itself must be removed (dead code).
    assert "const typeCounts = useMemo" not in src
    # The All tab still shows the truthful global count.
    assert (
        "tab.key === 'all'" in src
        and "{contractsTotal}" in src
    ), (
        "The All tab must still render contractsTotal so users see "
        "the global total even when no filter is active."
    )
