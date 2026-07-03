"""Finance Query Planner — produces a closed-schema plan from a user
question.

This first slice is **deterministic / rule-based** by design. The risk
of letting an LLM emit raw Mongo or invent finance arithmetic is high
enough that the safer starting point is regex-style intent detection.
Every keyword family below is documented with its mapping so future
maintainers can audit the rules without reading the LLM's prior runs.

A future commit may swap the rule layer for a JSON-mode LLM call — the
public API (``plan_finance_query``) stays stable and the validator +
compiler downstream don't care which side built the plan, only that it
matches ``FinanceQueryPlan``.
"""
from __future__ import annotations

import calendar
import logging
import re
from datetime import date
from typing import Iterable, Optional

from .finance_query_plan import (
    FinanceBasis,
    FinanceCategory,
    FinanceDetailLevel,
    FinanceFilter,
    FinanceFilterField,
    FinanceFilterOp,
    FinanceIntent,
    FinanceMetric,
    FinanceQueryPlan,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Keyword families (ordered: earlier hits win)
# ---------------------------------------------------------------------------

# Detail intent — "show me the rows", "transactions behind this total"
_DETAIL_PHRASES = (
    "transactions behind",
    "transactions that made up",
    "rows behind",
    "rows that made up",
    "show me the transactions",
    "show transactions",
    "show me the rows",
    "show the rows",
    "pull the transactions",
    "fetch the transactions",
    "fetch all the transactions",
    "list the transactions",
    "what transactions",
)

# Open-invoice intent — outstanding / unpaid / overdue invoices, the
# AR-aging snapshot source. MUST be tested BEFORE _INVOICE_PHRASES
# below, otherwise "open invoices" would match the generic invoice
# family and route to query_invoice_totals (which also returns paid +
# closed invoices and would silently inflate the answer).
#
# Two-part detection: an open-qualifier word (open / outstanding /
# unpaid / overdue) paired with an invoice-context word (invoice /
# invoices / receivable / receivables), OR one of the AR-style phrases
# below that imply open invoices without using the literal word
# "invoice".
_OPEN_INVOICE_QUALIFIERS = ("open", "outstanding", "unpaid", "overdue")
_OPEN_INVOICE_CONTEXT = ("invoice", "invoices", "receivable", "receivables")
_OPEN_INVOICE_OWED_PHRASES = (
    "what do they owe",
    "what is owed",
    "what's owed",
    "what we are owed",
    "what we're owed",
    "outstanding balance",
    "outstanding receivables",
    "open invoice balance",
    "open invoices balance",
    "open balance",
    "receivables outstanding",
    "how much is owed",
    "how much is outstanding",
)
# Hints that flip an open-invoice plan from rows-default to total.
_OPEN_INVOICE_TOTAL_HINTS = (
    "how much",
    "total open",
    "total outstanding",
    "total unpaid",
    "total overdue",
    "outstanding balance",
    "open invoice balance",
    "open invoices balance",
    "open balance",
    "receivables outstanding",
    "what do they owe",
    "what is owed",
    "what's owed",
    "what we are owed",
    "what we're owed",
    "how much is owed",
    "how much is outstanding",
)

# Invoice intent — "invoiced" / "invoices" / "bill" (AR billing volume).
# Matches the issued-invoice family: paid + closed + open. Open-invoice
# detection above runs FIRST so "open invoices" / "outstanding
# invoices" don't fall into this family and silently route to
# query_invoice_totals.
_INVOICE_PHRASES = (
    "invoiced",
    "invoice",
    "invoices",
    "ar invoice",
    "ar invoices",
    "billed",
    "billings",
)

# Driver / variance / "why" intent — explanations grounded in
# month-by-month buckets and top accounts. MUST be tested BEFORE the
# revenue / invoice families below: "why did revenue change" must NOT
# fall into REVENUE_TOTAL, which would just return a single number
# without the variance / driver context the user asked for.
_DRIVER_PHRASES = (
    "why did",
    "why is",
    "why has",
    "why was",
    "why are",
    "what drove",
    "what's driving",
    "what is driving",
    "what changed",
    "what's behind",
    "what is behind",
    "drivers of",
    "drivers behind",
    "explain expenses",
    "explain expense",
    "explain revenue",
    "explain the variance",
    "month by month",
    "month-by-month",
    "month over month",
    "month-over-month",
    "vs last month",
    "vs last quarter",
    "vs last year",
    "compared to last",
    "variance vs",
    "variance against",
)
_DRIVER_METRIC_PHRASES: tuple[tuple[str, FinanceMetric], ...] = (
    # Order matters: most specific multi-word phrases first.
    ("gross profit", FinanceMetric.GROSS_PROFIT),
    ("gross margin", FinanceMetric.GROSS_PROFIT_MARGIN),
    ("cogs", FinanceMetric.GROSS_PROFIT),  # cogs questions resolve to a GP-axis driver call
    ("cost of goods", FinanceMetric.GROSS_PROFIT),
    ("expense", FinanceMetric.EXPENSE),
    ("expenses", FinanceMetric.EXPENSE),
    ("spending", FinanceMetric.EXPENSE),
    ("revenue", FinanceMetric.REVENUE),
    ("income", FinanceMetric.REVENUE),
    ("sales", FinanceMetric.REVENUE),
    ("top line", FinanceMetric.REVENUE),
)

# Revenue / income intent
_REVENUE_PHRASES = (
    "revenue",
    "income",
    "sales",
    "top line",
)

# Cash flow projection
_PROJECTION_PHRASES = (
    "cash flow projection",
    "cashflow projection",
    "project cash",
    "projected cash",
    "13 week",
    "13-week",
    "run a projection",
    "run the projection",
)

# Category-routing keywords. Maps phrase fragments to the closed
# FinanceCategory enum. Order is canonical — the most specific
# multi-word phrases match first so "field service" doesn't get
# clobbered by a broad "service" rule.
_CATEGORY_PHRASES: tuple[tuple[str, FinanceCategory], ...] = (
    ("field service revenue", FinanceCategory.FS_REVENUE),
    ("field services revenue", FinanceCategory.FS_REVENUE),
    ("field service income", FinanceCategory.FS_REVENUE),
    ("fs revenue", FinanceCategory.FS_REVENUE),
    ("professional services revenue", FinanceCategory.PS_REVENUE),
    ("professional service revenue", FinanceCategory.PS_REVENUE),
    ("ps revenue", FinanceCategory.PS_REVENUE),
    ("wholesale revenue", FinanceCategory.WHOLESALE_REVENUE),
    ("wholesale", FinanceCategory.WHOLESALE_REVENUE),
    ("mrr", FinanceCategory.MRR),
    ("recurring revenue", FinanceCategory.MRR),
    ("payroll", FinanceCategory.PAYROLL),
    ("admin expenses", FinanceCategory.ADMIN_EXPENSES),
    ("administrative expenses", FinanceCategory.ADMIN_EXPENSES),
    ("ps cogs", FinanceCategory.PS_COGS),
    ("fs cogs", FinanceCategory.FS_COGS),
)

# Basis keywords
_BASIS_ACCRUAL_PHRASES = ("accrual basis", "on accrual", "accrual")
_BASIS_CASH_PHRASES = ("cash basis", "on cash basis")  # "cash" alone is too noisy

# Blank-location signal — keep narrow; "blank" alone can mean almost
# anything. The detail-tool gate in the validator further restricts
# this to gl_transaction_detail before it reaches the user.
_BLANK_LOCATION_PHRASES = (
    "blank location",
    "location is blank",
    "location blank",
    "no location",
    "missing location",
    "without a location",
    "without location",
)

# Prior-context signal — "behind this" / "of this total" only makes
# sense when the conversation already produced a number to drill into.
# The planner takes ``has_prior_total`` as an explicit caller-supplied
# flag so we don't try to scrape conversation context here.
_BACKREF_PHRASES = (
    "this total",
    "that total",
    "the total above",
    "behind this",
    "behind that",
    "of this total",
    "of that total",
)

# Month-name → 1..12 lookup. Includes 3-letter abbreviations.
_MONTHS: dict[str, int] = {}
for _i, _name in enumerate(calendar.month_name):
    if _name:
        _MONTHS[_name.lower()] = _i
for _i, _name in enumerate(calendar.month_abbr):
    if _name:
        _MONTHS[_name.lower()] = _i

# Quarter labels — "Q1" .. "Q4" + "first quarter" .. "fourth quarter"
_QUARTERS: dict[str, tuple[int, int]] = {  # label -> (start_month, end_month)
    "q1": (1, 3),
    "q2": (4, 6),
    "q3": (7, 9),
    "q4": (10, 12),
    "first quarter": (1, 3),
    "second quarter": (4, 6),
    "third quarter": (7, 9),
    "fourth quarter": (10, 12),
}

# Year regex — anchored to 4 digits and a sane range so we don't grab
# dollar amounts like "$2025" out of context.
_YEAR_RE = re.compile(r"\b(20\d{2})\b")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _ymd(d: date) -> str:
    return d.isoformat()


def _resolve_month_window(
    text_lower: str,
    runtime_today: Optional[date],
) -> tuple[Optional[str], Optional[str], Optional[str], bool]:
    """Find a single-month window in the text.

    Returns ``(date_from, date_to, period_label, year_was_inferred)``.
    ``date_from`` / ``date_to`` are None when neither a month nor a
    year could be resolved. ``year_was_inferred`` is True when the
    user wrote a month with no year and we filled in the year from
    ``runtime_today``; the validator surfaces this to the user.
    """
    # Month detection — first whole-word match wins.
    matched_month: Optional[int] = None
    matched_label: Optional[str] = None
    for name, mo in sorted(_MONTHS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(name)}\b", text_lower):
            matched_month = mo
            matched_label = name.capitalize()
            break
    if matched_month is None:
        return (None, None, None, False)

    year_match = _YEAR_RE.search(text_lower)
    year: Optional[int] = int(year_match.group(1)) if year_match else None
    inferred = False
    if year is None:
        if runtime_today is None:
            # No explicit year and no runtime default — leave dates
            # unresolved so the validator triggers a clarification.
            return (None, None, matched_label, False)
        # Default: same year as runtime_today, unless that puts us in
        # the future — in which case use the previous year so a
        # sensible historical window is queried by default.
        year = runtime_today.year
        if matched_month > runtime_today.month:
            year -= 1
        inferred = True

    df = date(year, matched_month, 1)
    dt = date(year, matched_month, _last_day_of_month(year, matched_month))
    return (_ymd(df), _ymd(dt), f"{matched_label} {year}", inferred)


def _resolve_quarter_window(
    text_lower: str,
    runtime_today: Optional[date],
) -> tuple[Optional[str], Optional[str], Optional[str], bool]:
    """Detect a quarter phrase. Same contract as _resolve_month_window."""
    for label, (start_mo, end_mo) in _QUARTERS.items():
        if label in text_lower:
            year_match = _YEAR_RE.search(text_lower)
            inferred = False
            if year_match:
                year = int(year_match.group(1))
            elif runtime_today is not None:
                year = runtime_today.year
                if start_mo > runtime_today.month:
                    year -= 1
                inferred = True
            else:
                return (None, None, label.upper(), False)
            df = date(year, start_mo, 1)
            dt = date(year, end_mo, _last_day_of_month(year, end_mo))
            return (_ymd(df), _ymd(dt), f"{label.upper()} {year}", inferred)
    return (None, None, None, False)


def _detect_category(text_lower: str) -> Optional[FinanceCategory]:
    for phrase, cat in _CATEGORY_PHRASES:
        if phrase in text_lower:
            return cat
    return None


def _detect_basis(text_lower: str) -> FinanceBasis:
    for p in _BASIS_CASH_PHRASES:
        if p in text_lower:
            return FinanceBasis.CASH
    for p in _BASIS_ACCRUAL_PHRASES:
        if p in text_lower:
            return FinanceBasis.ACCRUAL
    return FinanceBasis.UNSPECIFIED


def _detect_blank_location(text_lower: str) -> bool:
    return any(p in text_lower for p in _BLANK_LOCATION_PHRASES)


def _looks_like_backreference(text_lower: str) -> bool:
    return any(p in text_lower for p in _BACKREF_PHRASES)


def _matches_any(text_lower: str, phrases: Iterable[str]) -> bool:
    return any(p in text_lower for p in phrases)


def _detect_open_invoice_signal(text_lower: str) -> bool:
    """True iff the prompt is asking about open / outstanding / unpaid
    / overdue invoices.

    Whole-word matching for the qualifier prevents "openrate" or "ARP
    output" style false positives. Substring matching is fine for the
    invoice context words because they're already invoice-specific.
    """
    has_qualifier = any(
        re.search(rf"\b{re.escape(q)}\b", text_lower)
        for q in _OPEN_INVOICE_QUALIFIERS
    )
    has_invoice_ctx = any(c in text_lower for c in _OPEN_INVOICE_CONTEXT)
    if has_qualifier and has_invoice_ctx:
        return True
    return any(p in text_lower for p in _OPEN_INVOICE_OWED_PHRASES)


def _detect_driver_metric(text_lower: str) -> Optional[FinanceMetric]:
    """Return the metric the driver call should explain, derived from
    the user's words. None when no metric phrase is present — caller
    decides whether to ask for clarification."""
    for phrase, metric in _DRIVER_METRIC_PHRASES:
        if phrase in text_lower:
            return metric
    return None


def _is_driver_signal(text_lower: str) -> bool:
    return _matches_any(text_lower, _DRIVER_PHRASES)


def _is_open_invoice_total_phrasing(text_lower: str) -> bool:
    """Pick OPEN_INVOICE_TOTAL vs OPEN_INVOICE_ROWS once we know the
    prompt is in the open-invoice family. Defaults to ROWS unless one
    of the explicit total hints appears."""
    return any(h in text_lower for h in _OPEN_INVOICE_TOTAL_HINTS)


# ---------------------------------------------------------------------------
# Planner public API
# ---------------------------------------------------------------------------


def plan_finance_query(
    user_message: str,
    *,
    runtime_today: Optional[date] = None,
    has_prior_total: bool = False,
    available_tools: Optional[set[str]] = None,
) -> FinanceQueryPlan:
    """Convert a user message into a FinanceQueryPlan.

    Args:
      user_message: the raw user question.
      runtime_today: the date to use when the user names a month
        without a year. Pass ``None`` to force a clarification — the
        validator turns that into a follow-up question.
      has_prior_total: True iff a prior tool result in this conversation
        produced a total the user could be drilling into. Drives the
        ``transactions behind this total`` intent path.
      available_tools: optional set of Nova tool names available in
        this deployment. The planner records this in the plan's notes
        so the compiler can reason about availability without
        re-querying the registry.
    """
    text = user_message.strip()
    text_lower = text.lower()
    notes: list[str] = []

    # ---- Window resolution ----
    df, dt, label, inferred = _resolve_quarter_window(text_lower, runtime_today)
    if df is None:
        df, dt, label, inferred = _resolve_month_window(text_lower, runtime_today)

    basis = _detect_basis(text_lower)
    category = _detect_category(text_lower)
    blank_location = _detect_blank_location(text_lower)

    filters: list[FinanceFilter] = []
    if category is not None:
        filters.append(
            FinanceFilter(
                field=FinanceFilterField.CATEGORY,
                op=FinanceFilterOp.EQ,
                value=category.value,
            )
        )
    if blank_location:
        filters.append(
            FinanceFilter(
                field=FinanceFilterField.LOCATION_BLANK,
                op=FinanceFilterOp.IS_BLANK,
                value=True,
            )
        )

    if inferred and label:
        notes.append(
            f"Year was not specified; defaulted to {label} based on the "
            "current calendar year."
        )

    # ---- Intent detection (rule order: most specific first) ----

    # 1. Cash-flow projection
    if _matches_any(text_lower, _PROJECTION_PHRASES):
        return FinanceQueryPlan(
            intent=FinanceIntent.CASHFLOW_PROJECTION,
            metric=None,
            basis=basis,
            detail_level=FinanceDetailLevel.AGGREGATE,
            date_from=df,
            date_to=dt,
            period_label=label,
            year_was_inferred=inferred,
            filters=filters,
            raw_user_message=text,
            notes=notes,
        )

    # 2. Detail / drill-down intent — "transactions behind this total"
    #    requires either an explicit prior-total signal OR a clear
    #    "show me the rows" phrasing. The validator further checks that
    #    a detail-capable tool exists when the request is row-level.
    is_detail_phrase = _matches_any(text_lower, _DETAIL_PHRASES)
    is_backref = _looks_like_backreference(text_lower)
    if is_detail_phrase or (is_backref and has_prior_total):
        if is_backref and not has_prior_total:
            # User's phrasing references a "this total" the planner has
            # no record of. Don't fabricate a detail call - ask first.
            return FinanceQueryPlan(
                intent=FinanceIntent.CLARIFICATION_REQUIRED,
                basis=basis,
                date_from=df,
                date_to=dt,
                period_label=label,
                year_was_inferred=inferred,
                filters=filters,
                raw_user_message=text,
                requires_confirmation=True,
                confirmation_prompt=(
                    "You're asking for the rows behind a total, but I "
                    "don't have a prior total in this conversation to "
                    "drill into. Could you tell me which period or "
                    "metric you'd like the transactions for?"
                ),
                notes=notes,
            )
        return FinanceQueryPlan(
            intent=FinanceIntent.GL_TRANSACTION_DETAIL,
            metric=FinanceMetric.REVENUE,  # the detail tool aggregates income
            basis=basis,
            category=category,
            detail_level=FinanceDetailLevel.AGGREGATE_WITH_ROWS,
            date_from=df,
            date_to=dt,
            period_label=label,
            year_was_inferred=inferred,
            filters=filters,
            raw_user_message=text,
            notes=notes,
        )

    # 2.5. Driver / variance explanation — "why did revenue change?",
    #      "what drove the expense increase?", "month-by-month
    #      breakdown vs last quarter". Routes to explain_finance_drivers.
    #      MUST be tested BEFORE open-invoice / invoice / revenue
    #      families: "why did revenue go up vs last quarter" otherwise
    #      lands in REVENUE_TOTAL and returns a single number with no
    #      driver context.
    #
    #      The driver tool requires a date_from/date_to and a metric.
    #      If either is missing, ask for clarification rather than
    #      silently passing nulls — the helper would error out
    #      identically downstream and the user gets a worse experience.
    if _is_driver_signal(text_lower):
        driver_metric = _detect_driver_metric(text_lower)
        if df is None or dt is None:
            return FinanceQueryPlan(
                intent=FinanceIntent.CLARIFICATION_REQUIRED,
                metric=driver_metric,
                basis=basis,
                date_from=df,
                date_to=dt,
                period_label=label,
                year_was_inferred=inferred,
                filters=filters,
                raw_user_message=text,
                requires_confirmation=True,
                confirmation_prompt=(
                    "I can break down the drivers behind a metric, but I "
                    "need a date range. Which window should I compare — "
                    "last month, last quarter, this year, or a specific "
                    "YYYY-MM-DD to YYYY-MM-DD?"
                ),
                notes=notes
                + [
                    "Driver intent detected but no date window resolved; "
                    "asking for clarification before running the tool."
                ],
            )
        if driver_metric is None:
            return FinanceQueryPlan(
                intent=FinanceIntent.CLARIFICATION_REQUIRED,
                metric=None,
                basis=basis,
                date_from=df,
                date_to=dt,
                period_label=label,
                year_was_inferred=inferred,
                filters=filters,
                raw_user_message=text,
                requires_confirmation=True,
                confirmation_prompt=(
                    "Which metric should I explain — revenue, expenses, "
                    "COGS, or gross profit?"
                ),
                notes=notes
                + [
                    "Driver intent detected but no metric phrase matched; "
                    "asking for clarification before running the tool."
                ],
            )
        return FinanceQueryPlan(
            intent=FinanceIntent.FINANCE_DRIVER_EXPLANATION,
            metric=driver_metric,
            basis=basis,
            category=category,
            detail_level=FinanceDetailLevel.AGGREGATE,
            date_from=df,
            date_to=dt,
            period_label=label,
            year_was_inferred=inferred,
            filters=filters,
            raw_user_message=text,
            notes=notes,
        )

    # 3. Open-invoice intent — "show me open invoices", "outstanding
    #    invoices", "what do they owe". MUST be tested BEFORE the
    #    generic invoice family below: an "open invoices" question
    #    routed through INVOICE_TOTAL would compile to
    #    query_invoice_totals (the issued-invoices source, paid +
    #    closed + open) and silently inflate the answer with paid
    #    invoices the user is not asking about.
    if _detect_open_invoice_signal(text_lower):
        is_total = _is_open_invoice_total_phrasing(text_lower)
        intent = (
            FinanceIntent.OPEN_INVOICE_TOTAL
            if is_total
            else FinanceIntent.OPEN_INVOICE_ROWS
        )
        return FinanceQueryPlan(
            intent=intent,
            metric=FinanceMetric.INVOICE_AMOUNT,
            basis=basis,
            # No category on open-invoice plans — categories are GL
            # concepts and the validator rejects them on AR intents.
            category=None,
            # detail_level stays AGGREGATE; the intent itself encodes
            # rows-vs-total. The validator's _DETAIL_CAPABLE_INTENTS
            # set therefore doesn't need to grow.
            detail_level=FinanceDetailLevel.AGGREGATE,
            date_from=df,
            date_to=dt,
            period_label=label,
            year_was_inferred=inferred,
            filters=filters,
            raw_user_message=text,
            notes=notes,
        )

    # 4. Issued-invoice intent — "how much have we invoiced". MUST NOT
    #    fall through to revenue_total: per the demo notes, conflating
    #    GL revenue with AR-invoiced amount is a known footgun.
    if _matches_any(text_lower, _INVOICE_PHRASES):
        intent = (
            FinanceIntent.INVOICE_DETAIL
            if _matches_any(text_lower, ("show me", "list", "which invoices"))
            else FinanceIntent.INVOICE_TOTAL
        )
        return FinanceQueryPlan(
            intent=intent,
            metric=FinanceMetric.INVOICE_AMOUNT,
            basis=basis,
            category=category,
            detail_level=(
                FinanceDetailLevel.ROWS
                if intent == FinanceIntent.INVOICE_DETAIL
                else FinanceDetailLevel.AGGREGATE
            ),
            date_from=df,
            date_to=dt,
            period_label=label,
            year_was_inferred=inferred,
            filters=filters,
            raw_user_message=text,
            notes=notes,
        )

    # 5. Revenue / income intent
    if _matches_any(text_lower, _REVENUE_PHRASES):
        return FinanceQueryPlan(
            intent=FinanceIntent.REVENUE_TOTAL,
            metric=FinanceMetric.REVENUE,
            basis=basis,
            category=category,
            detail_level=FinanceDetailLevel.AGGREGATE,
            date_from=df,
            date_to=dt,
            period_label=label,
            year_was_inferred=inferred,
            filters=filters,
            raw_user_message=text,
            notes=notes,
        )

    # 6. No matching family — out of scope for the planner. Caller
    #    should fall back to the existing Nova tool-loop path.
    return FinanceQueryPlan(
        intent=FinanceIntent.UNSUPPORTED,
        basis=basis,
        date_from=df,
        date_to=dt,
        period_label=label,
        year_was_inferred=inferred,
        filters=filters,
        raw_user_message=text,
        notes=notes
        + [
            "Planner could not match a known finance intent. "
            "Existing Nova tool-loop should answer instead."
        ],
    )


__all__ = ["plan_finance_query"]
