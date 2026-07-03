"""Maps QBO account names to projection categories.

Edited independently of engine math. When Asad wants to change which
accounts roll up into PS_REVENUE vs FS_COGS, this is the only file that
changes.

Categorization is by account-number prefix because QBO uses a numbered
chart of accounts (50101, 60302, etc.). Prefixes are based on actual
GL data from the AEGISAXIOM database as of April 14, 2026.

QBO emits two account-name shapes in live data:
    "50101 Professional Services Income"            (code at start)
    "Field Income:50209 Trenching Services"         (code embedded after parent path)
The matcher extracts the 5-digit code from anywhere in the name so both
shapes hit the same prefix rule. Names with no embedded code (e.g.
legacy "Other Income:SimpliComply") fall through to the string
heuristics at the end.
"""
from __future__ import annotations

import re
from typing import Literal


Category = Literal[
    "PS_REVENUE", "FS_REVENUE", "MRR", "WHOLESALE_REVENUE", "OTHER_REVENUE",
    "PS_COGS", "FS_COGS", "OTHER_COGS",
    "PAYROLL", "ADMIN_EXPENSES", "OTHER_OPEX",
    "BALANCE_SHEET", "UNCATEGORIZED",
]


# Word-bounded so a stray 8-digit number elsewhere in the name does not
# get sliced into a phantom 5-digit code.
_ACCOUNT_CODE_RE = re.compile(r"\b(\d{5})\b")


# Order matters — first matching prefix wins.
CATEGORY_RULES: list[tuple[str, Category]] = [
    # Revenue (50xxx)
    ("50101", "PS_REVENUE"),
    ("50104", "PS_REVENUE"),
    ("50201", "FS_REVENUE"),
    ("50202", "FS_REVENUE"),
    # 50300 = "Other Income:Contract Commission Income". Asad confirmed
    # this is where MRR is booked (AppDirect + telco contract residuals).
    # Two QBO formats exist in live data: the bare "50300 Contract
    # Commission Income" account-only form, and the hierarchical
    # "50300 Other Income:Contract Commission Income" which inlines the
    # parent-account path. Both start with "50300" so this prefix
    # matches both.
    ("50300", "MRR"),
    ("50400", "WHOLESALE_REVENUE"),

    # COGS (60xxx)
    ("60201", "FS_COGS"),
    ("60202", "FS_COGS"),
    ("60205", "FS_COGS"),
    ("60302", "PS_COGS"),

    # Operating expenses (70xxx)
    ("70601", "PAYROLL"),
    ("70511", "ADMIN_EXPENSES"),
    ("70205", "ADMIN_EXPENSES"),
    ("70201", "ADMIN_EXPENSES"),
]


# 3-digit category-family fallbacks. Consulted only when no specific
# 5-digit rule above matched. Catches sub-accounts like 50209 (Field
# Income family) or 50409 (Wholesale family) that aren't enumerated.
CATEGORY_FALLBACK_RULES: list[tuple[str, Category]] = [
    ("501", "PS_REVENUE"),
    ("502", "FS_REVENUE"),
    ("503", "MRR"),
    ("504", "WHOLESALE_REVENUE"),
]


def categorize(account_name: str) -> Category:
    """Map a QBO account name to a projection category.

    Returns UNCATEGORIZED if no prefix rule matches and no heuristic
    fires. Engine should surface uncategorized accounts as a warning.
    """
    if not account_name:
        return "UNCATEGORIZED"

    code_match = _ACCOUNT_CODE_RE.search(account_name)
    if code_match is not None:
        code = code_match.group(1)
        for prefix, category in CATEGORY_RULES:
            if code.startswith(prefix):
                return category
        for prefix, category in CATEGORY_FALLBACK_RULES:
            if code.startswith(prefix):
                return category
    else:
        for prefix, category in CATEGORY_RULES:
            if account_name.startswith(prefix):
                return category

    upper = account_name.upper()
    if "INCOME" in upper:
        return "OTHER_REVENUE"
    if "COST OF GOODS" in upper or "COGS" in upper:
        return "OTHER_COGS"
    if "EXPENSE" in upper:
        return "OTHER_OPEX"

    return "UNCATEGORIZED"


def is_revenue(category: Category) -> bool:
    return category in (
        "PS_REVENUE", "FS_REVENUE", "MRR",
        "WHOLESALE_REVENUE", "OTHER_REVENUE",
    )


def is_cost(category: Category) -> bool:
    return category in (
        "PS_COGS", "FS_COGS", "OTHER_COGS",
        "PAYROLL", "ADMIN_EXPENSES", "OTHER_OPEX",
    )
