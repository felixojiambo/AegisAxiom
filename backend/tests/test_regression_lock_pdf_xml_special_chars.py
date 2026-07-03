"""Regression lock. Nova driver report PDF must escape every XML
special character before handing strings to reportlab Paragraph.

Commit A introduced _xml_escape_text in finance_report_pdf.py and
a single guard test covered ampersand and less than sign. This
file is a per character parametrized lock so any future edit that
re routes one of the affected fields around the escape helper
fails loudly. Each parameter exercises the same character in two
fields. The formula block which feeds Paragraph directly and the
top_accounts account_name which is rendered via the same code
path through the table builder.

Synthetic only. Far future dates, fake account labels.
"""
from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest")

import pytest


_BASE_TOOL_RESULT = {
    "metric": "revenue",
    "scope": {
        "metric": "revenue",
        "basis": "accrual",
        "current_period_start": "2099-04-01",
        "current_period_end": "2099-04-30",
        "comparison_period_start": "2099-03-01",
        "comparison_period_end": "2099-03-31",
        "comparison_mode": "previous_month",
        "comparison_label": "previous calendar month",
        "included_revenue_categories": ["SYNTH_REV_A"],
    },
    "summary": {
        "current_period_total": 100.0,
        "comparison_period_total": 80.0,
        "variance_amount": 20.0,
        "variance_percent": 0.25,
    },
    "monthly": [
        {"month": "2099-03", "amount": 0.0, "comparison_amount": 80.0},
        {"month": "2099-04", "amount": 100.0, "comparison_amount": 0.0},
    ],
    "top_customers": [],
    "top_vendors": [],
    "drivers": [],
}


@pytest.mark.parametrize(
    "special_char",
    [
        pytest.param("<", id="less_than"),
        pytest.param(">", id="greater_than"),
        pytest.param("&", id="ampersand"),
        pytest.param("'", id="apostrophe"),
        pytest.param('"', id="double_quote"),
    ],
)
def test_pdf_renders_when_special_char_in_account_name_and_formula(special_char):
    """The PDF must build for every XML special char in both the
    formula block and the top accounts account name. A raw value
    of any of these would otherwise reach reportlab Paragraph and
    raise XMLSyntaxError."""
    from services.nova_engine.finance_report_pdf import (
        render_driver_explanation_pdf,
    )

    tool_result = dict(_BASE_TOOL_RESULT)
    tool_result["formula"] = {
        "variance_amount": "variance " + special_char + " comparison_total",
        "variance_percent": "ratio of variance " + special_char + " total",
    }
    tool_result["top_accounts"] = [
        {
            "account_name": "Synth " + special_char + " Account A",
            "category": "SYNTH_REV_A",
            "amount": 60.0,
            "share_pct": 0.6,
        },
    ]
    tool_result["warnings"] = [
        "warning bullet with " + special_char + " in body",
    ]
    body = render_driver_explanation_pdf(tool_result)
    assert body[:4] == b"%PDF", (
        "PDF magic missing for special char "
        + repr(special_char)
    )


@pytest.mark.parametrize(
    "special_char,expected_entity",
    [
        pytest.param("<", "&lt;", id="less_than"),
        pytest.param(">", "&gt;", id="greater_than"),
        pytest.param("&", "&amp;", id="ampersand"),
    ],
)
def test_xml_escape_helper_emits_entity_for_xml_critical_char(
    special_char, expected_entity,
):
    """Direct unit assertion on _xml_escape_text. The helper escapes
    the three XML critical chars angle brackets and ampersand which
    reportlab Paragraph parses as markup. Apostrophe and double
    quote are intentionally left unescaped because Paragraph treats
    them as body text. See the helper docstring for the contract."""
    from services.nova_engine.finance_report_pdf import _xml_escape_text

    escaped = _xml_escape_text("prefix " + special_char + " suffix")
    assert isinstance(escaped, str)
    assert expected_entity in escaped, (
        "expected entity " + expected_entity + " in escaped output got "
        + repr(escaped)
    )
