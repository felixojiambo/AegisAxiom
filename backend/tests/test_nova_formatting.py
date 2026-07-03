"""Tests for the Nova finance-answer formatting policy.

The audited bug: Nova was wrapping ordinary finance metadata
(basis values, category names, source labels, formulas) inside
fenced code blocks. The chat UI then rendered each short token as a
standalone box and long technical identifiers got cut off
horizontally. The fix has two layers:

  * The Nova prompt's new ``## Finance answer formatting`` section
    forbids fence-wrapping for ordinary metadata and prescribes
    comma-separated lists / plain-text formulas / bullet-list query
    parameters.
  * Several tool descriptions reinforce the same rule so the LLM
    sees it both at the system-prompt level and in the per-tool
    description it consults before each call.

These tests pin both layers. No production dates / amounts / names
are used — every assertion is on the prompt / schema text itself.
"""
from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production")


# ---------------------------------------------------------------------------
# Nova prompt: the canonical formatting policy section
# ---------------------------------------------------------------------------


def _prompt() -> str:
    from services.ai.agents.nova import _SYSTEM_PROMPT

    return _SYSTEM_PROMPT


def test_prompt_has_finance_answer_formatting_section():
    text = _prompt()
    assert "## Finance answer formatting" in text


def test_prompt_forbids_fenced_code_blocks_for_ordinary_metadata():
    """Rule 1 — the rule whose absence caused the audited bug."""
    text = _prompt().lower()
    assert "do not render ordinary finance metadata as fenced code blocks" in text


def test_prompt_says_category_filter_lists_are_comma_separated():
    """Rule 4 — comma-separated text, not bullets/code-blocks."""
    text = _prompt().lower()
    # Must call out the comma-separated convention explicitly.
    assert "comma-separated text" in text
    # And specifically for category / filter / column lists.
    assert "category / filter / column lists" in text


def test_prompt_says_formulas_are_plain_text():
    """Rule 5 — formula lines are prose, not fenced blocks."""
    text = _prompt().lower()
    assert "render formulas as plain text" in text


def test_prompt_says_quote_exact_dates_and_labels_returned_by_tools():
    """Rule 7 — never paraphrase tool-returned labels."""
    text = _prompt().lower()
    assert "quote exact dates, labels, formulas, source metadata" in text


def test_prompt_says_no_calendar_month_relabel_for_non_matching_range():
    """Rule 7 echoes the comparison-mode rule from the driver section
    — a non-calendar-month range must not be renamed to a calendar
    month. This is the second-leg defense alongside the explicit
    rule in the Driver Explanations section."""
    text = _prompt().lower()
    assert "do not invent a calendar-month name" in text


def test_prompt_says_query_parameters_are_a_compact_bullet_list():
    """Rule 6 — pin the bulleted-disclosure shape so the LLM doesn't
    fall back to a one-row markdown table or a fenced JSON block."""
    text = _prompt()
    assert "compact bullet list" in text.lower()
    # The example shape is documented inline so the LLM has a model
    # to copy.
    assert "**Basis:**" in text
    assert "**Current period:**" in text
    assert "**Comparison period:**" in text


def test_prompt_reserves_fenced_code_blocks_for_explicit_technical_asks():
    text = _prompt().lower()
    assert "fenced code blocks (triple backticks) are reserved" in text
    # The explicit-ask exceptions are listed.
    for exception in ("code", "json", "api payload", "configuration"):
        assert exception in text


# ---------------------------------------------------------------------------
# Tool-description reinforcement
# ---------------------------------------------------------------------------


def _tool_description(name: str) -> str:
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    schema = next(t for t in NOVA_TOOL_SCHEMAS if t["name"] == name)
    return schema["description"]


def test_explain_finance_drivers_description_says_categories_are_comma_separated():
    description = _tool_description("explain_finance_drivers").lower()
    assert "comma-separated text" in description
    # And the no-fenced-code rule.
    assert "not as a fenced code block" in description or (
        "not as a fenced code block or one-bullet-per-category list" in description
    )


def test_explain_finance_drivers_description_says_formulas_are_plain_text():
    description = _tool_description("explain_finance_drivers").lower()
    assert "plain-text equation lines" in description
    assert "not as fenced code blocks" in description


def test_get_gl_location_coverage_description_says_no_fenced_blocks_for_metadata():
    description = _tool_description("get_gl_location_coverage").lower()
    assert "comma-separated" in description
    assert "not as fenced code blocks" in description


def test_query_finance_data_description_says_filter_metadata_is_bullets_or_csv():
    description = _tool_description("query_finance_data").lower()
    assert "comma-separated lists" in description
    assert "not as fenced code blocks" in description


def test_create_finance_export_description_says_dont_dump_full_json_in_fence():
    description = _tool_description("create_finance_export").lower()
    assert "do not dump the full json response in a" in description
    # The artifact-on-its-own-line convention is documented as the
    # alternative.
    assert "[[artifact:" in description


# ---------------------------------------------------------------------------
# Sanity: the prompt's existing section markers still exist (no
# accidental removal of upstream sections by the new section insert)
# ---------------------------------------------------------------------------


def test_prompt_still_has_neighbour_sections():
    text = _prompt()
    assert "## Output style" in text
    assert "## Exports" in text
    assert "## Driver explanations" in text
    assert "## Location coverage" in text


def test_prompt_finance_formatting_section_appears_before_exports():
    """The new section is positioned between Output style and Exports
    so the formatting rules are read before the LLM hits the
    export / driver / location-specific guidance below."""
    text = _prompt()
    formatting_idx = text.index("## Finance answer formatting")
    exports_idx = text.index("## Exports")
    output_style_idx = text.index("## Output style")
    assert output_style_idx < formatting_idx < exports_idx
