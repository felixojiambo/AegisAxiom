"""Compiler — turns a validated FinanceQueryPlan into a Nova tool call.

Hard rules:

  * The compiler does NOT execute the tool — it only assembles
    ``CompiledFinanceToolCall(tool_name, tool_args)``. The conductor's
    existing tool-loop (or the shadow-mode logger) does the actual
    dispatch.
  * The compiler does NOT invoke Mongo. Nova's deterministic tools
    own all Mongo queries.
  * The compiler does NOT compute totals. Same reason.
  * If no existing Nova tool can satisfy a plan, the compiler returns
    ``is_supported=False`` with an explanatory ``unsupported_reason``.
    Surfacing the gap is preferable to silently routing to a
    near-miss tool (e.g. mapping ``invoice_total`` to GL revenue).

The mapping below is the entire allowed surface. Adding a new intent
means adding a row to ``intent_to_tool_name`` AND a branch in
``compile_finance_plan``. The validator's
``_LOCATION_BLANK_OK_INTENTS`` and ``_DETAIL_CAPABLE_INTENTS`` sets
must stay in sync.
"""
from __future__ import annotations

from typing import Optional

from .finance_query_plan import (
    CompiledFinanceToolCall,
    FinanceCategory,
    FinanceFilterField,
    FinanceFilterOp,
    FinanceIntent,
    FinanceQueryPlan,
)


# ---------------------------------------------------------------------------
# Intent → Nova-tool mapping
# ---------------------------------------------------------------------------
#
# Tool names mirror entries in
# ``services/ai/agents/tools/nova_tools.NOVA_TOOL_SCHEMAS``. When a
# plan's intent has no row here it is by definition unsupported.

_INTENT_TO_TOOL: dict[FinanceIntent, str] = {
    FinanceIntent.REVENUE_TOTAL: "query_finance_data",
    FinanceIntent.REVENUE_BY_PERIOD: "query_finance_data",
    FinanceIntent.GL_TRANSACTION_DETAIL: "query_gl_transactions",
    FinanceIntent.FINANCE_DRIVER_EXPLANATION: "explain_finance_drivers",
    # CASHFLOW_PROJECTION is intentionally NOT mapped here.
    # ``_compile_cashflow_projection`` always returns
    # ``is_supported=False`` (the planner has no path to extract
    # starting_cash / projection_start_month from a user message),
    # so registering a tool name would be misleading — callers
    # would think a tool call is going to happen. Keep the mapping
    # absent until the compile branch can actually emit args, then
    # add the row alongside the new compile logic.
    # Issued-invoice intents map to query_invoice_totals (paid +
    # closed + open). NEVER to get_open_invoices: that source is
    # OPEN-only and would silently exclude paid/closed invoices,
    # understating the period.
    FinanceIntent.INVOICE_TOTAL: "query_invoice_totals",
    FinanceIntent.INVOICE_DETAIL: "query_invoice_totals",
    # Open-invoice intents map to get_open_invoices (the AR-aging
    # snapshot). NEVER to query_invoice_totals: that source includes
    # paid + closed invoices and would silently inflate an "open
    # invoices" answer with closed records.
    FinanceIntent.OPEN_INVOICE_ROWS: "get_open_invoices",
    FinanceIntent.OPEN_INVOICE_TOTAL: "get_open_invoices",
}


def intent_to_tool_name(intent: FinanceIntent) -> Optional[str]:
    """Return the Nova tool name for an intent, or None if unsupported.

    Exposed at module level so the validator can warn about missing
    tools without re-importing the mapping internals.
    """
    return _INTENT_TO_TOOL.get(intent)


# ---------------------------------------------------------------------------
# Per-intent compile helpers
# ---------------------------------------------------------------------------


def _compile_revenue_total(plan: FinanceQueryPlan) -> dict:
    """query_finance_data args for a single-window revenue total.

    Maps the plan's ``basis`` / category / account_prefix filters into
    the tool's flat-arg shape. Defaults to ``group_by="month"`` so the
    response is bucketed (one entry for the user's window). The
    underlying Mongo $match still applies date_from/date_to.
    """
    args: dict = {
        "metric": "revenue",
        "group_by": "month",
        "date_from": plan.date_from,
        "date_to": plan.date_to,
        "basis": (plan.basis.value if plan.basis.value != "unspecified" else "accrual"),
    }
    # Category filter (closed enum → canonical category string)
    if plan.category is not None:
        args["category"] = plan.category.value
    # Optional account_prefix filter from the closed filter list.
    for f in plan.filters:
        if (
            f.field == FinanceFilterField.ACCOUNT_PREFIX
            and f.op == FinanceFilterOp.EQ
            and isinstance(f.value, str)
        ):
            args["account_prefix"] = f.value
    return args


def _compile_revenue_by_period(plan: FinanceQueryPlan) -> dict:
    """Same as revenue_total today; kept as a separate compile path so
    a future split (e.g. group_by=quarter for a different intent label)
    only changes one branch."""
    args = _compile_revenue_total(plan)
    return args


def _compile_gl_transaction_detail(plan: FinanceQueryPlan) -> dict:
    """query_gl_transactions args for the detail intent.

    Honors the location_blank filter, which is the ONLY intent with
    that capability today.
    """
    args: dict = {
        "date_from": plan.date_from,
        "date_to": plan.date_to,
        "basis": (plan.basis.value if plan.basis.value != "unspecified" else "accrual"),
    }
    has_blank = any(
        f.field == FinanceFilterField.LOCATION_BLANK
        and f.op == FinanceFilterOp.IS_BLANK
        and bool(f.value)
        for f in plan.filters
    )
    if has_blank:
        args["location_blank"] = True
    return args


# Filter fields the issued-invoice tool exposes as direct args.
# Categories and account_prefix are GL concepts and intentionally
# excluded — the validator already rejects invoice intents paired
# with a category, and account_prefix is meaningless for AR records.
_INVOICE_PASSTHROUGH_FIELDS: dict[FinanceFilterField, str] = {
    FinanceFilterField.CUSTOMER: "customer",
    FinanceFilterField.SEARCH: "search",
    FinanceFilterField.STATUS: "status",
    FinanceFilterField.CLASS_NAME: "class_name",
    FinanceFilterField.LOCATION: "location",
}

# Conservative default preview cap for invoice rows. Matches the
# tool's own default (100) and stays under its hard cap (500).
_INVOICE_DEFAULT_LIMIT = 100


def _compile_invoice_intent(plan: FinanceQueryPlan) -> dict:
    """query_invoice_totals args for INVOICE_TOTAL / INVOICE_DETAIL.

    The compile shape is the same for both intents; only
    ``include_rows`` differs:

      * INVOICE_TOTAL  → include_rows defaults to False unless the
        plan explicitly asked for rows via detail_level.
      * INVOICE_DETAIL → include_rows=True.

    Filter pass-through is restricted to the closed
    _INVOICE_PASSTHROUGH_FIELDS list. GL-only filters (CATEGORY,
    ACCOUNT_PREFIX, LOCATION_BLANK) are intentionally NOT forwarded
    — the validator separately rejects invoice plans that carry
    them.
    """
    args: dict = {
        "date_from": plan.date_from,
        "date_to": plan.date_to,
        "limit": _INVOICE_DEFAULT_LIMIT,
    }

    # include_rows resolution. Detail intent forces True. For
    # TOTAL intent, honour an explicit row-level detail_level
    # (rows / aggregate_with_rows) but default to False so a bare
    # "how much have we invoiced" doesn't waste budget on a row
    # preview the user didn't ask for.
    if plan.intent == FinanceIntent.INVOICE_DETAIL:
        args["include_rows"] = True
    elif plan.detail_level.value in ("rows", "aggregate_with_rows"):
        args["include_rows"] = True
    else:
        args["include_rows"] = False

    # Closed filter pass-through. Only EQ-shaped filters become tool
    # args; anything else is dropped because the issued-invoice tool
    # doesn't accept structured operator dicts.
    for f in plan.filters:
        target_arg = _INVOICE_PASSTHROUGH_FIELDS.get(f.field)
        if target_arg is None:
            continue
        if f.op != FinanceFilterOp.EQ:
            continue
        if not isinstance(f.value, str) or not f.value.strip():
            continue
        args[target_arg] = f.value

    return args


# Filter fields the open-invoice tool exposes as direct args. The
# tool's surface (see NOVA_TOOL_SCHEMAS / get_open_invoices) accepts:
# search, bucket, date_from, date_to, class_name, limit. There is no
# `customer` arg — customer-scoped queries reuse `search`. There is
# no `status` or `location` arg, so plans carrying STATUS or LOCATION
# filters are silently dropped (the validator can't reject them
# because LOCATION is legitimately allowed for the issued-invoice
# tool — the boundary is enforced here at compile time).
_OPEN_INVOICE_PASSTHROUGH_FIELDS: dict[FinanceFilterField, str] = {
    FinanceFilterField.CUSTOMER: "search",
    FinanceFilterField.SEARCH: "search",
    FinanceFilterField.CLASS_NAME: "class_name",
}

# Conservative limits for the row preview. The total_amount /
# total_open_balance fields are server-computed and don't depend on
# the limit, so OPEN_INVOICE_TOTAL can use the smaller default.
_OPEN_INVOICE_ROWS_LIMIT = 200
_OPEN_INVOICE_TOTAL_LIMIT = 50


def _compile_open_invoice_intent(plan: FinanceQueryPlan) -> dict:
    """get_open_invoices args for OPEN_INVOICE_ROWS / OPEN_INVOICE_TOTAL.

    Both intents target the same tool — the difference is preview
    breadth (rows intent gets a higher limit). The tool's response
    carries server-computed total_amount and total_open_balance so the
    LLM can quote totals verbatim without summing rows itself.

    None-valued date fields are omitted from the args because the tool
    treats absent date_from/date_to as "no period filter, return the
    current AR-aging snapshot." Forwarding ``None`` would serialize as
    a null in the schema and trigger validation noise.
    """
    args: dict = {}
    if plan.date_from is not None:
        args["date_from"] = plan.date_from
    if plan.date_to is not None:
        args["date_to"] = plan.date_to

    args["limit"] = (
        _OPEN_INVOICE_ROWS_LIMIT
        if plan.intent == FinanceIntent.OPEN_INVOICE_ROWS
        else _OPEN_INVOICE_TOTAL_LIMIT
    )

    # Closed filter pass-through. Only EQ-shaped filters become tool
    # args. STATUS / LOCATION / CATEGORY / ACCOUNT_PREFIX /
    # LOCATION_BLANK are intentionally omitted (the tool doesn't
    # accept them and the validator already disallows category /
    # location_blank for AR intents).
    for f in plan.filters:
        target_arg = _OPEN_INVOICE_PASSTHROUGH_FIELDS.get(f.field)
        if target_arg is None:
            continue
        if f.op != FinanceFilterOp.EQ:
            continue
        if not isinstance(f.value, str) or not f.value.strip():
            continue
        args[target_arg] = f.value

    return args


def _compile_finance_driver_explanation(plan: FinanceQueryPlan) -> dict:
    """explain_finance_drivers args from a driver-explanation plan.

    The driver tool requires both date_from and date_to (the validator
    is also responsible for catching the missing-window case). The
    metric maps from the planner's FinanceMetric to the driver tool's
    short metric labels: REVENUE -> 'revenue', EXPENSE -> 'expenses',
    GROSS_PROFIT / GROSS_PROFIT_MARGIN -> 'gross_profit'.

    compare_to defaults to 'previous_period' when the planner doesn't
    have a clearer signal — matches the helper's default. The caller
    can override by emitting a richer plan in a future slice.
    """
    if plan.metric is None:
        # Validator should have flagged this; defensive default to
        # 'revenue' so the tool returns a structured error rather than
        # an enum mismatch.
        metric = "revenue"
    elif plan.metric.value in ("gross_profit", "gross_profit_margin"):
        metric = "gross_profit"
    elif plan.metric.value == "expense":
        metric = "expenses"
    elif plan.metric.value == "revenue":
        metric = "revenue"
    else:
        # Closed-vocabulary fallback — the driver tool will refuse
        # unsupported labels with a structured error.
        metric = plan.metric.value

    args: dict = {
        "metric": metric,
        "date_from": plan.date_from,
        "date_to": plan.date_to,
        "basis": (
            plan.basis.value if plan.basis.value != "unspecified" else "accrual"
        ),
        "compare_to": "previous_period",
    }
    return args


def _compile_cashflow_projection(plan: FinanceQueryPlan) -> CompiledFinanceToolCall:
    """Cashflow projection requires assumptions the planner has no way
    to invent (starting_cash, projection_start_month). If a future
    slice surfaces those via conversation context, expand here. For
    now mark unsupported with a clear handoff to the existing tool.
    """
    return CompiledFinanceToolCall(
        plan=plan,
        tool_name=None,
        tool_args={},
        is_supported=False,
        unsupported_reason=(
            "cashflow_projection requires explicit starting_cash and "
            "projection_start_month inputs the planner does not yet "
            "extract. The existing Nova tool-loop handles these "
            "directly."
        ),
        helpful_alternative=(
            "Ask Nova to run a 13-week projection with a starting "
            "cash figure (e.g. 'Run a projection starting June 2030 "
            "with $50,000 cash')."
        ),
    )


# ---------------------------------------------------------------------------
# Public compile API
# ---------------------------------------------------------------------------


def compile_finance_plan(
    plan: FinanceQueryPlan,
    *,
    available_tools: Optional[set[str]] = None,
) -> CompiledFinanceToolCall:
    """Compile a plan into a tool call (or an unsupported result).

    Args:
      plan: a FinanceQueryPlan that has already passed the validator.
        Compile is callable on an unvalidated plan but the caller is
        responsible for clarification handling.
      available_tools: optional Nova tool registry. When provided, the
        compiler downgrades to ``is_supported=False`` for any tool the
        deployment does not expose, even if the intent is otherwise
        compilable.
    """
    tool_name = intent_to_tool_name(plan.intent)

    # --- Terminal / unsupported intents ----------------------------------
    if plan.intent == FinanceIntent.CLARIFICATION_REQUIRED:
        return CompiledFinanceToolCall(
            plan=plan,
            tool_name=None,
            tool_args={},
            is_supported=False,
            unsupported_reason=(
                "Plan requires user clarification — no tool can be "
                "compiled until the user answers the follow-up."
            ),
        )
    if plan.intent == FinanceIntent.UNSUPPORTED:
        return CompiledFinanceToolCall(
            plan=plan,
            tool_name=None,
            tool_args={},
            is_supported=False,
            unsupported_reason=(
                "Planner did not match a known finance intent. "
                "Existing Nova tool-loop should answer instead."
            ),
        )
    # --- Tool-availability check -----------------------------------------
    if available_tools is not None and tool_name is not None and tool_name not in available_tools:
        # Tailor the unsupported message for invoice intents so the
        # caller knows the correct route (the issued-invoice tool)
        # is missing — and explicitly that get_open_invoices is NOT
        # a fallback. This is the contract the architecture spec
        # requires.
        if plan.intent in (FinanceIntent.INVOICE_TOTAL, FinanceIntent.INVOICE_DETAIL):
            return CompiledFinanceToolCall(
                plan=plan,
                tool_name=None,
                tool_args={},
                is_supported=False,
                unsupported_reason=(
                    "query_invoice_totals is not registered in this "
                    "deployment. Issued-invoice totals must NOT fall "
                    "back to get_open_invoices, which is OPEN-only "
                    "and would understate the period."
                ),
                helpful_alternative=(
                    "Register query_invoice_totals (see "
                    "NOVA_TOOL_SCHEMAS) so the planner can route "
                    "all-invoices-issued questions correctly."
                ),
            )
        if plan.intent in (
            FinanceIntent.OPEN_INVOICE_ROWS,
            FinanceIntent.OPEN_INVOICE_TOTAL,
        ):
            return CompiledFinanceToolCall(
                plan=plan,
                tool_name=None,
                tool_args={},
                is_supported=False,
                unsupported_reason=(
                    "get_open_invoices is not registered in this "
                    "deployment. Open-invoice questions must NOT "
                    "fall back to query_invoice_totals, which "
                    "includes paid + closed invoices and would "
                    "inflate the answer with non-open records."
                ),
                helpful_alternative=(
                    "Register get_open_invoices (see "
                    "NOVA_TOOL_SCHEMAS) so the planner can route "
                    "open-invoice questions correctly."
                ),
            )
        return CompiledFinanceToolCall(
            plan=plan,
            tool_name=None,
            tool_args={},
            is_supported=False,
            unsupported_reason=(
                f"Tool {tool_name!r} is not registered in this "
                "deployment."
            ),
        )

    # --- Intent dispatch -------------------------------------------------
    if plan.intent == FinanceIntent.REVENUE_TOTAL:
        return CompiledFinanceToolCall(
            plan=plan,
            tool_name=tool_name,
            tool_args=_compile_revenue_total(plan),
        )
    if plan.intent == FinanceIntent.REVENUE_BY_PERIOD:
        return CompiledFinanceToolCall(
            plan=plan,
            tool_name=tool_name,
            tool_args=_compile_revenue_by_period(plan),
        )
    if plan.intent == FinanceIntent.GL_TRANSACTION_DETAIL:
        return CompiledFinanceToolCall(
            plan=plan,
            tool_name=tool_name,
            tool_args=_compile_gl_transaction_detail(plan),
        )
    if plan.intent == FinanceIntent.FINANCE_DRIVER_EXPLANATION:
        return CompiledFinanceToolCall(
            plan=plan,
            tool_name=tool_name,
            tool_args=_compile_finance_driver_explanation(plan),
        )
    if plan.intent in (FinanceIntent.INVOICE_TOTAL, FinanceIntent.INVOICE_DETAIL):
        return CompiledFinanceToolCall(
            plan=plan,
            tool_name=tool_name,
            tool_args=_compile_invoice_intent(plan),
        )
    if plan.intent in (
        FinanceIntent.OPEN_INVOICE_ROWS,
        FinanceIntent.OPEN_INVOICE_TOTAL,
    ):
        return CompiledFinanceToolCall(
            plan=plan,
            tool_name=tool_name,
            tool_args=_compile_open_invoice_intent(plan),
        )
    if plan.intent == FinanceIntent.CASHFLOW_PROJECTION:
        return _compile_cashflow_projection(plan)

    # Defensive — if a new intent enum value lands without a compile
    # branch, fail closed rather than emitting an empty tool call.
    return CompiledFinanceToolCall(
        plan=plan,
        tool_name=None,
        tool_args={},
        is_supported=False,
        unsupported_reason=(
            f"intent {plan.intent.value!r} has no compile branch. "
            "Add a row to _INTENT_TO_TOOL and a branch in "
            "compile_finance_plan."
        ),
    )


__all__ = ["compile_finance_plan", "intent_to_tool_name"]
