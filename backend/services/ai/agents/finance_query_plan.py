"""Closed-schema models for the Finance Query Planner.

The planner converts a user's natural-language finance question into a
``FinanceQueryPlan``. The validator audits it. The compiler maps it to
existing Nova tool calls. None of these layers fabricate finance
totals or talk to Mongo directly — they describe what the user wants
in a structured shape and hand off to the deterministic Nova tools
that already own math + queries.

Why a separate finance schema (not the generic Query Builder)?

The generic builder at ``services/ai/agents/registry.py:build_query_builder_prompt``
emits a CRM-shaped JSON (``name_searches``, ``stages``, ``aggregations``)
that Nova's tools can't consume. Finance questions need different
fields entirely: ``metric``, ``basis``, ``category``, ``detail_level``,
``location_blank``. Reusing the CRM schema would force every finance
intent through a square-peg/round-hole translation. Two parallel
planners is cleaner than one schema that tries to be both.

Hard rules baked into this schema:

  - No raw Mongo operators (``$gte``, ``$or``, …) anywhere.
  - No collection names from the LLM. The compiler picks the tool;
    the tool picks the collection.
  - No freeform query language. Every value is a closed Enum or a
    typed scalar.
  - No total calculations. The plan describes intent only — the
    Nova tool computes the total downstream.

If a future LLM-driven planner replaces the rule-based first slice,
it MUST emit JSON matching this schema (constrained generation /
JSON-mode call). The validator + compiler stay unchanged.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Closed enums
# ---------------------------------------------------------------------------


class FinanceIntent(str, Enum):
    """What the user wants Nova to answer.

    Order is canonical — earlier entries are higher-priority when the
    rule-based planner sees overlapping signals. ``unsupported`` and
    ``clarification_required`` are terminal: the compiler does not run
    a Mongo query for either.
    """

    REVENUE_TOTAL = "revenue_total"
    REVENUE_BY_PERIOD = "revenue_by_period"
    GL_TRANSACTION_DETAIL = "gl_transaction_detail"
    # Driver / variance explanation — "why did revenue go up?", "what
    # drove this expense change?". Compiled to explain_finance_drivers,
    # which reads only general_ledger and computes
    # variance + monthly buckets + top accounts deterministically.
    FINANCE_DRIVER_EXPLANATION = "finance_driver_explanation"
    # Issued-invoice intents — all invoices billed in a period (paid +
    # closed + open). Compiled to query_invoice_totals.
    INVOICE_TOTAL = "invoice_total"
    INVOICE_DETAIL = "invoice_detail"
    # Open-invoice intents — outstanding / unpaid / overdue invoices
    # only (the AR-aging snapshot). Compiled to get_open_invoices.
    # MUST stay distinct from the issued-invoice intents above:
    # routing an "open invoices" question to query_invoice_totals would
    # silently include paid invoices, and routing an "invoiced in
    # <period>" question to get_open_invoices would silently exclude
    # them. The compiler enforces the boundary in both directions.
    OPEN_INVOICE_ROWS = "open_invoice_rows"
    OPEN_INVOICE_TOTAL = "open_invoice_total"
    CASHFLOW_PROJECTION = "cashflow_projection"
    CLARIFICATION_REQUIRED = "clarification_required"
    UNSUPPORTED = "unsupported"


class FinanceMetric(str, Enum):
    """User-facing metric names. Maps onto the Nova tool's metric
    enum where the underlying tool exposes one (e.g. query_finance_data
    uses 'revenue', 'expense', 'gross_profit'). The compiler does the
    final translation; the planner only picks the closed-vocabulary
    name."""

    REVENUE = "revenue"
    EXPENSE = "expense"
    GROSS_PROFIT = "gross_profit"
    GROSS_PROFIT_MARGIN = "gross_profit_margin"
    EXPENSE_RATIO = "expense_ratio"
    TRANSACTION_COUNT = "transaction_count"
    INVOICE_AMOUNT = "invoice_amount"
    PAYROLL = "payroll"
    ADMIN_EXPENSES = "admin_expenses"


class FinanceBasis(str, Enum):
    ACCRUAL = "accrual"
    CASH = "cash"
    UNSPECIFIED = "unspecified"


class FinanceCategory(str, Enum):
    """Mirrors services/nova_engine/account_categories.Category.

    Kept as a parallel enum (not imported from account_categories) so
    the planner schema stays standalone and surface-level changes to
    the engine's Category Literal don't ripple here without an
    explicit code change.
    """

    PS_REVENUE = "PS_REVENUE"
    FS_REVENUE = "FS_REVENUE"
    MRR = "MRR"
    WHOLESALE_REVENUE = "WHOLESALE_REVENUE"
    OTHER_REVENUE = "OTHER_REVENUE"
    PS_COGS = "PS_COGS"
    FS_COGS = "FS_COGS"
    OTHER_COGS = "OTHER_COGS"
    PAYROLL = "PAYROLL"
    ADMIN_EXPENSES = "ADMIN_EXPENSES"
    OTHER_OPEX = "OTHER_OPEX"


class FinanceDetailLevel(str, Enum):
    AGGREGATE = "aggregate"
    ROWS = "rows"
    AGGREGATE_WITH_ROWS = "aggregate_with_rows"


class FinanceFilterField(str, Enum):
    """Closed list of filterable fields. Anything not in this enum
    cannot be filtered on through the planner — guards against
    LLM-emitted ``raw_data.SomeRandomField`` values reaching the
    storage engine.

    LOCATION (free text) and LOCATION_BLANK (boolean) are distinct
    on purpose: only the GL transaction tool supports the blank
    flavour, while the issued-invoice tool can filter on a populated
    location string. STATUS and SEARCH cover the issued-invoice
    tool's substring-search and EmailStatus filters.
    """

    CATEGORY = "category"
    ACCOUNT_PREFIX = "account_prefix"
    CUSTOMER = "customer"
    CLASS_NAME = "class_name"
    LOCATION = "location"
    LOCATION_BLANK = "location_blank"
    STATUS = "status"
    SEARCH = "search"


class FinanceFilterOp(str, Enum):
    EQ = "eq"
    IN = "in"
    IS_BLANK = "is_blank"
    IS_NOT_BLANK = "is_not_blank"


# ---------------------------------------------------------------------------
# Plan / filter / validation / compiled-call models
# ---------------------------------------------------------------------------


class FinanceFilter(BaseModel):
    """One filter term. Field + op + value is the entire vocabulary —
    no nested operators, no composition (top-level ``filters: list``
    is implicitly conjoined by the compiler)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: FinanceFilterField
    op: FinanceFilterOp
    value: Any = None


class FinanceQueryPlan(BaseModel):
    """Structured intent. The planner emits this; the validator audits
    it; the compiler turns it into a Nova tool call."""

    model_config = ConfigDict(extra="forbid")

    intent: FinanceIntent
    metric: Optional[FinanceMetric] = None
    basis: FinanceBasis = FinanceBasis.UNSPECIFIED
    category: Optional[FinanceCategory] = None
    detail_level: FinanceDetailLevel = FinanceDetailLevel.AGGREGATE

    # Date window (YYYY-MM-DD strings; the planner is responsible for
    # resolving relative phrases to concrete dates BEFORE building the
    # plan — same contract Nova's tools enforce). Either-or-both may
    # be None when the planner could not resolve a year.
    date_from: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_to: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    # Resolved month label, e.g. "May" — kept alongside date_from/to so
    # the validator can flag the missing-year case even after
    # synthesizing concrete dates from a runtime default.
    period_label: Optional[str] = None
    year_was_inferred: bool = False

    filters: list[FinanceFilter] = Field(default_factory=list)

    # Provenance & UX hooks
    raw_user_message: str
    requires_confirmation: bool = False
    confirmation_prompt: Optional[str] = None
    notes: list[str] = Field(default_factory=list)

    def has_raw_mongo(self) -> bool:
        """Defensive sanity check used by tests. Any value that looks
        like a Mongo operator anywhere in the plan is a bug — the
        schema's enums shouldn't allow this, but tests assert it.

        Walks every nested string in the dumped plan rather than
        relying on ``repr`` of top-level values. Catches a
        ``$where``-style operator hidden inside a deeply nested
        ``FinanceFilter.value`` that the previous top-level repr scan
        could miss when the value happened to be a non-string type
        whose repr did not surface the operator literal.
        """
        suspect_strings = ("$gte", "$lte", "$in", "$or", "$and", "$regex", "$exists", "$where")
        return _walk_for_mongo_ops(self.model_dump(mode="json"), suspect_strings)


class FinancePlanValidationResult(BaseModel):
    """What the validator returns. ``is_valid=False`` means the
    compiler must NOT run; ``needs_clarification=True`` means the LLM
    should ask the user a follow-up question instead of executing."""

    model_config = ConfigDict(extra="forbid")

    is_valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: Optional[str] = None


class CompiledFinanceToolCall(BaseModel):
    """Output of the compiler: which Nova tool to call, with which
    args. ``is_supported=False`` means no existing tool can satisfy the
    plan — the LLM should surface ``unsupported_reason`` to the user
    rather than fall back to a different metric.
    """

    model_config = ConfigDict(extra="forbid")

    plan: FinanceQueryPlan
    tool_name: Optional[str] = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    is_supported: bool = True
    unsupported_reason: Optional[str] = None
    helpful_alternative: Optional[str] = None


def _walk_for_mongo_ops(node: Any, ops: tuple[str, ...]) -> bool:
    """Recursively scan a dumped plan for raw Mongo operator strings.

    Visits every string anywhere in the structure (dict values, list
    elements, nested dicts inside list elements, dict keys). Any
    occurrence of any operator literal returns True immediately.
    """
    if isinstance(node, str):
        return any(op in node for op in ops)
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str) and any(op in k for op in ops):
                return True
            if _walk_for_mongo_ops(v, ops):
                return True
        return False
    if isinstance(node, (list, tuple)):
        for item in node:
            if _walk_for_mongo_ops(item, ops):
                return True
        return False
    # ints, floats, bools, None — never carry operator literals.
    return False


__all__ = [
    "FinanceIntent",
    "FinanceMetric",
    "FinanceBasis",
    "FinanceCategory",
    "FinanceDetailLevel",
    "FinanceFilterField",
    "FinanceFilterOp",
    "FinanceFilter",
    "FinanceQueryPlan",
    "FinancePlanValidationResult",
    "CompiledFinanceToolCall",
]
