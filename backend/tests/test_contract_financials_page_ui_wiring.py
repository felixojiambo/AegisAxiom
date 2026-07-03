"""Source-level wiring contract for the Contract Financials page.

The May 24, 2026 build-out replaces the placeholder copy and routes
the page through GET /api/operations/contracts/hours (new endpoint).
This test pins the wiring so the page can't silently revert to the
placeholder or stop reading the hydrated Zoho fields.
"""
from pathlib import Path

import pytest


def _component_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend" / "src" / "pages" / "OperationsContractsPage.jsx"
    )
    if not path.exists():  # pragma: no cover — sandbox without frontend
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def test_placeholder_copy_is_gone():
    """Regression guard for the user's specific complaint."""
    src = _component_src()
    assert "planned for a follow-up feature branch" not in src, (
        "Contract Financials must not show the developer placeholder. "
        "The May 24, 2026 build-out shipped a real implementation."
    )


def test_page_calls_new_hours_endpoint():
    src = _component_src()
    assert "/api/operations/contracts/hours" in src
    # And the old summary/financials endpoints are no longer fetched —
    # the new endpoint returns both items + summary in one envelope.
    assert "/api/operations/contracts/summary" not in src
    assert "/api/operations/contracts/financials" not in src


def test_page_reads_hydrated_zoho_fields_from_response():
    """The table cells read the hydrated fields the apply script
    landed in 14a78a8c: total_hours, remaining_hours,
    consumed_hours, consumed_hours_percent, contract_status_raw,
    invoice_type_raw, ap_issues_raw, source_created_by,
    source_created_at."""
    src = _component_src()
    for field in (
        "c.total_hours", "c.remaining_hours", "c.consumed_hours",
        "c.consumed_hours_percent", "c.contract_status_raw",
        "c.invoice_type_raw", "c.ap_issues_raw",
        "c.source_created_by", "c.source_created_at",
    ):
        assert field in src, f"Table must read {field}"


def test_summary_cards_present():
    src = _component_src()
    for testid in (
        "summary-card-total",
        "summary-card-over-consumed",
        "summary-card-warning",
        "summary-card-healthy",
        "summary-card-missing-data",
        "summary-card-purchased",
        "summary-card-consumed",
        "summary-card-remaining",
    ):
        assert testid in src


def test_health_badges_rendered_for_every_classification():
    """Per-row Health column renders all 6 documented states. The
    backend's hour_health value drives the badge. May 25 reshape
    added `missing_data` as a real bucket."""
    src = _component_src()
    assert "HealthBadge" in src
    for state in ("over_consumed", "warning", "healthy",
                  "missing_data", "n_a", "unknown"):
        assert state in src, f"Health badge state {state!r} missing"


def test_ar_ap_disclaimer_present():
    """The page must say AR/AP isn't connected so operators don't
    read empty AR/AP columns as real zeroes. May 25, 2026 rewrite:
    copy is factual + neutral and does NOT promise a "follow-up
    release" (the investigation found that wording was aspirational
    with no docs backing it up)."""
    src = _component_src()
    assert 'data-testid="contract-financials-arap-notice"' in src
    # New neutral copy must be present.
    assert "AR/AP data is not connected on this page." in src
    # Old aspirational copy must be gone.
    assert "follow-up release" not in src
    assert "Receivables and payables roll up" not in src
    assert "AR/AP data not connected yet." not in src


def test_count_uses_backend_total_not_items_length():
    """May 25 reshape: the chip reads from the response envelope's
    top-level `total` (filtered count) and `total_unfiltered`,
    NOT `items.length` (current page only). The wording switches
    to '... matching contracts' when filters are active."""
    src = _component_src()
    assert "setTotal(" in src and "data?.total" in src
    assert "setTotalUnfiltered(" in src
    assert "data?.total_unfiltered" in src
    assert "contract-financials-count" in src
    # When filters are active the chip suffixes "matching contracts".
    assert "matching contracts" in src


def test_negative_remaining_hours_displayed_with_warning_style():
    """Operator must see the negative remaining hours in red rather
    than clamped to zero — confirms the UI doesn't hide over-
    consumed contracts."""
    src = _component_src()
    # The cell-class branches on `remainingNegative` and uses
    # `text-red-400`.
    assert "remainingNegative" in src
    assert "text-red-400" in src


def test_blank_hours_render_em_dash_not_zero():
    """fmtHours returns em dash when value is null/undefined so
    operators can tell missing data from explicit zero."""
    src = _component_src()
    # fmtHours helper exists.
    assert "const fmtHours" in src
    # Em dash literal is in the source.
    assert "'—'" in src


def test_filter_bar_supports_q_type_status_health():
    src = _component_src()
    for testid in (
        "contract-financials-search",
        "contract-financials-type-filter",
        "contract-financials-status-filter",
        "contract-financials-health-filter",
    ):
        assert testid in src


def test_search_is_debounced_before_fetch():
    """Each keystroke must not fan out a fetch. May 25 rewrite:
    debounce is driven from a local `qDraft` state that mirrors
    the URL-backed `q` after a 250ms idle window."""
    src = _component_src()
    assert "setQDraft" in src
    assert "setTimeout(() => {" in src
    assert "setQuery({ q: qDraft, page: 1 });" in src, (
        "Debounced commit must reset to page 1 so paging on a "
        "stale query doesn't return an empty page."
    )


def test_empty_state_distinguishes_no_data_from_no_matches():
    """An empty result set should explain WHY — true empty vs
    filtered to nothing."""
    src = _component_src()
    assert "No contracts available." in src
    assert "No contracts match the current filters." in src


def test_subtitle_does_not_name_backend_source_file():
    """May 24 (revised) review: the subtitle named the import file
    ('Hydrated from Zoho Contract.csv (14a78a8c)') which is internal
    bookkeeping, not user-facing copy. Pin the cleaned subtitle."""
    src = _component_src()
    assert "Hydrated from Zoho Contract.csv" not in src
    assert "(14a78a8c)" not in src
    assert "Hours, consumption, and contract health at a glance." in src


def test_arap_disclaimer_does_not_enumerate_backend_field_names():
    """May 24 (revised) review: the disclaimer listed seven raw
    backend field names (total_hours, remaining_hours, etc.). User
    copy should describe what they see, not the schema."""
    src = _component_src()
    # The seven enumerated backend field names must not be inside
    # the disclaimer block. They're allowed elsewhere in the source
    # (the table cells genuinely read them via c.total_hours etc.) —
    # we ban them from inside the notice's surface text.
    notice_start = src.find('data-testid="contract-financials-arap-notice"')
    assert notice_start != -1
    notice_end = src.find("</div>", src.find("</div>", notice_start) + 1)
    notice = src[notice_start:notice_end]
    for raw_field in (
        "<code>total_hours",
        "<code>remaining_hours",
        "<code>consumed_hours_percent",
        "<code>invoice_type_raw",
        "<code>contract_status_raw",
        "<code>ap_issues_raw",
    ):
        assert raw_field not in notice, (
            f"AR/AP disclaimer must not show {raw_field!r} — that's "
            "a schema name, not user-facing copy."
        )


def test_page_does_not_touch_protected_fields_in_render():
    """Defensive: page should never mention resources / resource_types
    / hourly_rate / project_ids as table cells — those are the
    operator-managed fields the apply script promises never to touch."""
    src = _component_src()
    for tok in ("c.resources", "c.resource_types", "c.hourly_rate", "c.project_ids"):
        assert tok not in src, (
            f"Contract Financials table must not render {tok!r} — "
            "those are operator-managed fields, not financials."
        )


# ---------------------------------------------------------------------------
# May 25, 2026 reshape — pagination + tooltip + new cards
# ---------------------------------------------------------------------------


def test_page_uses_server_side_pagination():
    """The page must send page + page_size to the backend and
    must NOT fetch all contracts and paginate client-side."""
    src = _component_src()
    # URL-backed pagination state.
    assert "useSearchParams" in src
    for key in ("page", "page_size"):
        assert f"searchParams.get('{key}')" in src
    # GET request includes page + page_size.
    assert "params.set('page'" in src
    assert "params.set('page_size'" in src
    # Default page size.
    assert "DEFAULT_PAGE_SIZE = 50" in src
    assert "PAGE_SIZE_OPTIONS = [25, 50, 100]" in src


def test_pagination_controls_render():
    """Prev/Next + Page X of Y + page-size select must all be in
    the source and tagged for browser smoke."""
    src = _component_src()
    for testid in (
        "contract-financials-pagination",
        "contract-financials-prev",
        "contract-financials-next",
        "contract-financials-range",
        "contract-financials-page-size",
    ):
        assert testid in src, f"Pagination testid {testid!r} missing."
    # Prev disabled at page 1, Next disabled at last page.
    assert "page <= 1" in src
    assert "page >= totalPages" in src


def test_filter_changes_reset_page_to_1():
    """Type / status / health / page-size filter changes all reset
    page to 1 so the user doesn't land on an out-of-bounds page."""
    src = _component_src()
    for change_line in (
        "setQuery({ contract_type: e.target.value, page: 1 })",
        "setQuery({ status: e.target.value, page: 1 })",
        "setQuery({ health: e.target.value, page: 1 })",
        "page: 1",  # page-size change also resets
    ):
        assert change_line in src


def test_refresh_preserves_url_backed_state():
    """The Refresh path (re-running `load`) reads URL-backed state
    via the closure on `q`/typeFilter/statusFilter/healthFilter/
    page/pageSize. Refresh must not clear or reset filters."""
    src = _component_src()
    # The load callback depends on every URL-backed field.
    assert "[canView, q, typeFilter, statusFilter, healthFilter, page, pageSize]" in src


def test_health_summary_renders_missing_data_card():
    """The May 25 reshape promotes `missing_data` to a real health
    bucket. It always renders (even when zero) because operators
    need to know when nothing is missing."""
    src = _component_src()
    assert "summary-card-missing-data" in src
    assert 'label="Missing data"' in src


def test_health_summary_renders_unknown_and_na_cards_when_nonzero():
    """Hidden health buckets caused the original 41+14+38=93 vs
    96 reconciliation gap. Now `unknown` and `n_a` get their own
    cards, shown only when count > 0 to avoid clutter."""
    src = _component_src()
    # Conditional render on count > 0.
    assert "showBucket('unknown')" in src
    assert "showBucket('n_a')" in src
    # Test ids for browser smoke.
    assert "summary-card-unknown" in src
    assert "summary-card-na" in src


def test_summary_cards_read_from_health_counts():
    """All bucket cards read from `summary.health_counts.<bucket>`
    instead of the legacy top-level mirrors. Pinning this keeps
    the cards aligned with the server's single source of truth so
    reconciliation can't drift."""
    src = _component_src()
    assert "healthCounts.over_consumed" in src
    assert "healthCounts.warning" in src
    assert "healthCounts.healthy" in src
    assert "healthCounts.missing_data" in src


def test_remaining_hours_tooltip_explains_formula():
    """The investigation flagged that Purchased − Consumed often
    does NOT equal Remaining, which surprises operators. The new
    card carries a tooltip explaining why and a sub-line showing
    the derived value."""
    src = _component_src()
    # Tooltip copy verbatim from the spec.
    assert (
        "Remaining Hours is summed from imported contract remaining-hour values"
        in src
    )
    # Sub-line shows the derived value.
    assert "Purchased − Consumed:" in src or "Purchased - Consumed:" in src
    assert "derived_remaining_hours" in src


def test_ap_issues_column_carries_tooltip_about_imported_note():
    """The AP Issues column is a free-text passthrough from Zoho,
    not a structured AR/AP rollup. The column header carries a
    tooltip explaining that so operators don't read it as
    structured AP."""
    src = _component_src()
    assert "Imported AP note. Structured AR/AP rollups are not connected" in src


def test_chip_uses_matching_contracts_when_filters_active():
    """The chip distinguishes filtered vs portfolio-wide counts —
    when filters are active it suffixes 'matching contracts' and
    optionally shows the unfiltered total in parentheses."""
    src = _component_src()
    assert "matching contracts" in src
    assert "totalUnfiltered" in src
    assert "filtersActive" in src


def test_health_filter_select_includes_missing_data_option():
    """The <select> the user picks from must include every
    health bucket the backend can return — including the new
    `missing_data` and the long-hidden `n_a` / `unknown`."""
    src = _component_src()
    # Find the health filter select block and assert all 6 options.
    idx = src.find('data-testid="contract-financials-health-filter"')
    assert idx != -1
    # Look around the select for the 6 option values.
    window = src[max(0, idx - 400):idx + 600]
    for value in ("over_consumed", "warning", "healthy",
                  "missing_data", "unknown", "n_a"):
        assert f'value="{value}"' in window, (
            f"Health filter <select> must include {value!r} option."
        )
