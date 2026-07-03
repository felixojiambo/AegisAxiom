"""Tests for the numerical drift guard in proposal_synthesis.

The guard protects AI-polished proposal output from accidentally
changing factual numbers (prices, hours, percentages, dates,
quantities) that came from the deterministic skeleton.

No real customer / financial / tenant data appears in these
fixtures.

See docs/sales-ops/PROPOSAL_LIFECYCLE_V2.md.
"""

import pytest


# ---------------------------------------------------------------------------
# extract_numeric_facts
# ---------------------------------------------------------------------------


def test_extract_currency_with_dollar_sign():
    from services.proposal_synthesis import extract_numeric_facts

    facts = extract_numeric_facts("Total: $12,345.67")
    assert len(facts) == 1
    assert facts[0].kind == "currency"
    assert facts[0].value == 12345.67


def test_extract_currency_with_usd_suffix():
    from services.proposal_synthesis import extract_numeric_facts

    facts = extract_numeric_facts("Total 1,000.00 USD please.")
    assert len(facts) == 1
    assert facts[0].kind == "currency"
    assert facts[0].value == 1000.00


def test_extract_hours_short_form():
    from services.proposal_synthesis import extract_numeric_facts

    facts = extract_numeric_facts("Total: 12h.")
    assert len(facts) == 1
    assert facts[0].kind == "hours"
    assert facts[0].value == 12.0


def test_extract_hours_long_form():
    from services.proposal_synthesis import extract_numeric_facts

    facts = extract_numeric_facts("It will take 12 hours.")
    assert len(facts) == 1
    assert facts[0].kind == "hours"
    assert facts[0].value == 12.0


def test_extract_hours_decimal():
    from services.proposal_synthesis import extract_numeric_facts

    facts = extract_numeric_facts("Effort: 12.5h.")
    assert facts[0].value == 12.5


def test_extract_percentages():
    from services.proposal_synthesis import extract_numeric_facts

    facts = extract_numeric_facts("Margin 15% on contract.")
    assert len(facts) == 1
    assert facts[0].kind == "percentage"
    assert facts[0].value == 15.0


def test_extract_iso_date():
    from services.proposal_synthesis import extract_numeric_facts

    facts = extract_numeric_facts("Start date: 2099-01-15.")
    assert len(facts) == 1
    assert facts[0].kind == "date"
    assert facts[0].value == "2099-01-15"


def test_extract_slash_date():
    from services.proposal_synthesis import extract_numeric_facts

    facts = extract_numeric_facts("Kicks off 1/15/2099.")
    assert len(facts) == 1
    assert facts[0].kind == "date"
    assert facts[0].value == "2099-01-15"


def test_extract_quantity_with_recognized_unit():
    from services.proposal_synthesis import extract_numeric_facts

    facts = extract_numeric_facts("Supports 25 users.")
    assert len(facts) == 1
    assert facts[0].kind == "quantity"
    assert facts[0].value == (25, "user")


def test_extract_quantity_ignores_bare_integer_without_unit():
    from services.proposal_synthesis import extract_numeric_facts

    facts = extract_numeric_facts("Team of 5 worked hard.")
    # 'worked' is not in the unit allowlist so the integer is ignored.
    quantity_facts = [f for f in facts if f.kind == "quantity"]
    assert quantity_facts == []


def test_extract_multiple_facts_in_order():
    from services.proposal_synthesis import extract_numeric_facts

    text = (
        "Total $5,000 for 40 hours of work starting 2099-02-01. "
        "Margin 20%. Supports 10 endpoints."
    )
    facts = extract_numeric_facts(text)
    kinds = [f.kind for f in facts]
    assert "currency" in kinds
    assert "hours" in kinds
    assert "date" in kinds
    assert "percentage" in kinds
    assert "quantity" in kinds


# ---------------------------------------------------------------------------
# compare_numeric_facts
# ---------------------------------------------------------------------------


def test_compare_equivalent_currency_formatting_passes():
    """The polished output formats $1,000 differently but the
    normalized value is the same. The guard must NOT trip on
    formatting-only changes."""
    from services.proposal_synthesis import extract_numeric_facts, compare_numeric_facts

    skeleton = "Total: $1,000.00 for the engagement."
    polished = "The engagement totals 1000 USD."
    sk = extract_numeric_facts(skeleton)
    po = extract_numeric_facts(polished)
    result = compare_numeric_facts(sk, po)
    assert result.equivalent is True
    assert not result.added and not result.removed and not result.changed


def test_compare_equivalent_hours_formatting_passes():
    from services.proposal_synthesis import extract_numeric_facts, compare_numeric_facts

    skeleton = "Effort: 12h total."
    polished = "Effort: 12 hours total."
    result = compare_numeric_facts(
        extract_numeric_facts(skeleton),
        extract_numeric_facts(polished),
    )
    assert result.equivalent is True


def test_compare_catches_changed_price():
    from services.proposal_synthesis import extract_numeric_facts, compare_numeric_facts

    sk = extract_numeric_facts("Total: $1,000.00.")
    po = extract_numeric_facts("Total: $1,500.00.")
    result = compare_numeric_facts(sk, po)
    assert result.equivalent is False
    assert len(result.changed) == 1
    assert result.changed[0][0].value == 1000.0
    assert result.changed[0][1].value == 1500.0


def test_compare_catches_changed_hours():
    from services.proposal_synthesis import extract_numeric_facts, compare_numeric_facts

    sk = extract_numeric_facts("Effort 12h.")
    po = extract_numeric_facts("Effort 20h.")
    result = compare_numeric_facts(sk, po)
    assert result.equivalent is False
    assert any(c[0].value == 12.0 and c[1].value == 20.0 for c in result.changed)


def test_compare_catches_changed_date():
    from services.proposal_synthesis import extract_numeric_facts, compare_numeric_facts

    sk = extract_numeric_facts("Start: 2099-01-15.")
    po = extract_numeric_facts("Start: 2099-02-15.")
    result = compare_numeric_facts(sk, po)
    assert result.equivalent is False
    assert any(
        c[0].value == "2099-01-15" and c[1].value == "2099-02-15" for c in result.changed
    )


def test_compare_catches_changed_percentage():
    from services.proposal_synthesis import extract_numeric_facts, compare_numeric_facts

    sk = extract_numeric_facts("Margin 15%.")
    po = extract_numeric_facts("Margin 20%.")
    result = compare_numeric_facts(sk, po)
    assert result.equivalent is False
    assert any(c[0].value == 15.0 and c[1].value == 20.0 for c in result.changed)


def test_compare_catches_changed_quantity():
    from services.proposal_synthesis import extract_numeric_facts, compare_numeric_facts

    sk = extract_numeric_facts("Supports 10 users.")
    po = extract_numeric_facts("Supports 25 users.")
    result = compare_numeric_facts(sk, po)
    assert result.equivalent is False
    assert any(
        c[0].value == (10, "user") and c[1].value == (25, "user") for c in result.changed
    )


def test_compare_flags_added_fact_invented_by_polish():
    """The polished output invents a percentage that was not in
    the skeleton. The guard must trip on invented facts."""
    from services.proposal_synthesis import extract_numeric_facts, compare_numeric_facts

    sk = extract_numeric_facts("Project description, no numbers.")
    po = extract_numeric_facts("Project description with a 15% discount baked in.")
    result = compare_numeric_facts(sk, po)
    assert result.equivalent is False
    assert len(result.added) == 1
    assert result.added[0].kind == "percentage"


def test_compare_flags_removed_fact_dropped_by_polish():
    from services.proposal_synthesis import extract_numeric_facts, compare_numeric_facts

    sk = extract_numeric_facts("Total $5,000 due 2099-02-01.")
    po = extract_numeric_facts("Total $5,000 due (date TBD).")
    result = compare_numeric_facts(sk, po)
    assert result.equivalent is False
    # date dropped, no replacement → removed list non-empty
    removed_kinds = [f.kind for f in result.removed]
    assert "date" in removed_kinds


def test_compare_multiplicity_change_is_drift():
    """The skeleton mentions a $5,000 line twice. The polished
    output collapses to one line. The guard treats this as drift
    so a polish cannot quietly drop a duplicate currency line."""
    from services.proposal_synthesis import extract_numeric_facts, compare_numeric_facts

    sk = extract_numeric_facts("Line A: $5,000. Line B: $5,000.")
    po = extract_numeric_facts("Both lines: $5,000.")
    result = compare_numeric_facts(sk, po)
    assert result.equivalent is False
    assert len(result.removed) == 1


def test_compare_asymmetric_pairing_accounts_for_extra_polished_fact():
    """Code-review edge case. Skeleton has two distinct prices, the
    polish replaces them with three identical prices. The pairing
    heuristic pairs two prior→new transitions; the third polished
    price remains as an unpaired addition. Identity-based remaining
    set arithmetic ensures the third duplicate is not silently
    absorbed by value-equality on the paired list."""
    from services.proposal_synthesis import extract_numeric_facts, compare_numeric_facts

    sk = extract_numeric_facts("Tier A $1,000. Tier B $2,000.")
    po = extract_numeric_facts("Tier A $1,500. Tier B $1,500. Tier C $1,500.")
    result = compare_numeric_facts(sk, po)
    assert result.equivalent is False
    # Two changed pairs (1000 -> 1500, 2000 -> 1500), one extra add.
    assert len(result.changed) == 2
    assert len(result.added) == 1
    assert result.added[0].kind == "currency"
    assert result.added[0].value == 1500.0


# ---------------------------------------------------------------------------
# apply_polish_with_drift_guard
# ---------------------------------------------------------------------------


def test_apply_with_none_polish_returns_skeleton_unchanged():
    """The polish callable is None today. The guard returns the
    skeleton untouched and flags neither drift nor fallback."""
    from services.proposal_synthesis import apply_polish_with_drift_guard

    result = apply_polish_with_drift_guard(
        skeleton="Synthetic skeleton text.",
        polish=None,
    )
    assert result.content == "Synthetic skeleton text."
    assert result.drift_detected is False
    assert result.fallback_used is False
    assert result.drift_details is None


def test_apply_with_safe_polish_returns_polish_output():
    """The polished output preserves every numeric fact. The
    guard accepts the polish."""
    from services.proposal_synthesis import apply_polish_with_drift_guard

    def safe_polish(text):
        return text.replace("Total", "Grand total")

    result = apply_polish_with_drift_guard(
        skeleton="Total: $1,000 for 12h work.",
        polish=safe_polish,
    )
    assert "Grand total" in result.content
    assert result.drift_detected is False
    assert result.fallback_used is False


def test_apply_with_drifty_polish_falls_back_to_skeleton():
    """The polished output changes a price. The guard falls back
    to the skeleton and reports the drift in drift_details."""
    from services.proposal_synthesis import apply_polish_with_drift_guard

    def drifty_polish(text):
        return text.replace("$1,000", "$1,500")

    result = apply_polish_with_drift_guard(
        skeleton="Total: $1,000.",
        polish=drifty_polish,
    )
    assert result.content == "Total: $1,000."
    assert result.drift_detected is True
    assert result.fallback_used is True
    assert result.drift_details is not None
    assert result.drift_details["changed"], "drift details should list the changed fact"


def test_apply_with_exception_in_polish_falls_back_safely():
    """If the polish callable raises, the guard returns the
    skeleton without leaking the exception. drift_detected is
    False because no comparison happened; fallback_used is True."""
    from services.proposal_synthesis import apply_polish_with_drift_guard

    def broken_polish(_text):
        raise RuntimeError("polish backend down")

    result = apply_polish_with_drift_guard(
        skeleton="Synthetic skeleton.",
        polish=broken_polish,
    )
    assert result.content == "Synthetic skeleton."
    assert result.drift_detected is False
    assert result.fallback_used is True


def test_apply_with_empty_polish_output_falls_back():
    """An empty string from polish is treated as a fallback so the
    proposal never ends up content-less."""
    from services.proposal_synthesis import apply_polish_with_drift_guard

    result = apply_polish_with_drift_guard(
        skeleton="Synthetic skeleton.",
        polish=lambda _t: "",
    )
    assert result.content == "Synthetic skeleton."
    assert result.fallback_used is True
