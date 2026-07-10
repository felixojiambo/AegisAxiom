"""Tests for QBO account → category mapping. All run, no skips."""
import pytest
from services.nova_engine import account_categories


def test_module_imports():
    assert hasattr(account_categories, "categorize")
    assert hasattr(account_categories, "is_revenue")
    assert hasattr(account_categories, "is_cost")


def test_ps_revenue_account():
    assert account_categories.categorize(
        "50101 Professional Services Income:Professional Services Income - Services"
    ) == "PS_REVENUE"


def test_fs_revenue_account():
    assert account_categories.categorize(
        "50201 Field Income:Field Income - Service"
    ) == "FS_REVENUE"


def test_ps_cogs_account():
    assert account_categories.categorize(
        "60302 Cost of Goods Sold:Professional Services:Contractor"
    ) == "PS_COGS"


def test_fs_cogs_account():
    assert account_categories.categorize(
        "60201 Cost of Goods Sold:Field Expenses:Field Agent Commission"
    ) == "FS_COGS"


def test_wholesale_revenue():
    assert account_categories.categorize(
        "50400 Other Income:Wholesale  Income"
    ) == "WHOLESALE_REVENUE"


def test_mrr_account_only_format():
    """Bare account-name form: '50300 Contract Commission Income'."""
    assert account_categories.categorize(
        "50300 Contract Commission Income"
    ) == "MRR"


def test_mrr_hierarchical_format():
    """Hierarchical form QBO emits when an account sits under a parent:
    '50300 Other Income:Contract Commission Income'. The 50300 prefix
    rule must match both formats - the colon-separated parent path
    after the prefix is irrelevant to categorization."""
    assert account_categories.categorize(
        "50300 Other Income:Contract Commission Income"
    ) == "MRR"


def test_mrr_does_not_match_other_50000_accounts():
    """The 50300 rule must not bleed into 50101 / 50201 / 50400 buckets.
    Lock that in so a future prefix collision is caught."""
    assert account_categories.categorize(
        "50101 Professional Services Income"
    ) == "PS_REVENUE"
    assert account_categories.categorize(
        "50201 Field Income"
    ) == "FS_REVENUE"
    assert account_categories.categorize(
        "50400 Other Income:Wholesale Income"
    ) == "WHOLESALE_REVENUE"


def test_payroll():
    assert account_categories.categorize(
        "70601 Payroll Expenses:Wages"
    ) == "PAYROLL"


def test_unknown_returns_uncategorized():
    assert account_categories.categorize(
        "Random Made Up Account"
    ) == "UNCATEGORIZED"


def test_empty_string():
    assert account_categories.categorize("") == "UNCATEGORIZED"


def test_is_revenue_classification():
    assert account_categories.is_revenue("PS_REVENUE")
    assert account_categories.is_revenue("FS_REVENUE")
    assert account_categories.is_revenue("MRR")
    assert account_categories.is_revenue("WHOLESALE_REVENUE")
    assert not account_categories.is_revenue("PS_COGS")
    assert not account_categories.is_revenue("PAYROLL")


def test_is_cost_classification():
    assert account_categories.is_cost("PS_COGS")
    assert account_categories.is_cost("PAYROLL")
    assert account_categories.is_cost("ADMIN_EXPENSES")
    assert not account_categories.is_cost("PS_REVENUE")


# Embedded-code matching (Bug B) ---------------------------------------

def test_fs_revenue_embedded_code():
    """Hierarchical name with the 5-digit code embedded after the parent
    path: 'Field Income:50209 Trenching Services'. The matcher extracts
    50209 and the 502 fallback rule routes it to FS_REVENUE — without
    this fallback, sub-accounts not enumerated in CATEGORY_RULES used
    to fall through to OTHER_REVENUE via the 'INCOME' heuristic."""
    assert account_categories.categorize(
        "Field Income:50209 Trenching Services"
    ) == "FS_REVENUE"


def test_ps_revenue_hierarchical_format_still_matches():
    """The leading-code form must continue to map via the specific
    5-digit rule (50101) even after the embedded-code extractor was
    added — extraction yields the same '50101' the original startswith
    saw, so the specific rule still wins over the 501 fallback."""
    assert account_categories.categorize(
        "50101 Professional Services Income:Professional Services Income - Services"
    ) == "PS_REVENUE"


def test_no_numeric_code_falls_through_to_income_heuristic():
    """Accounts with no 5-digit code (e.g. legacy QBO names that were
    never numbered) keep the original behavior — classified by the
    'INCOME' substring heuristic, landing in OTHER_REVENUE."""
    assert account_categories.categorize(
        "Other Income:SimpliComply"
    ) == "OTHER_REVENUE"


def test_six_prefix_does_not_classify_as_revenue():
    """6xxxx codes are COGS/expense, never revenue. The exact category
    (FS_COGS via the 60201 rule, OTHER_OPEX via heuristic, or
    UNCATEGORIZED) is left to existing rules; this test only locks the
    non-revenue invariant so the new fallback table can never bleed
    into revenue buckets."""
    cat = account_categories.categorize("60201 Field Agent Commission")
    assert cat not in (
        "PS_REVENUE", "FS_REVENUE", "MRR", "WHOLESALE_REVENUE",
    )


def test_wholesale_revenue_embedded_code():
    """504xx sub-account routes via the 504 fallback when the exact
    50400 rule does not match the extracted code."""
    assert account_categories.categorize(
        "Other Income:50409 Some Wholesale Subaccount"
    ) == "WHOLESALE_REVENUE"


def test_mrr_embedded_code():
    """503xx sub-account routes to MRR via the 503 fallback."""
    assert account_categories.categorize(
        "Other Income:50399 Recurring Revenue"
    ) == "MRR"
