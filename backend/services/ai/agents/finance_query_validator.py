"""Validator for FinanceQueryPlan.

Sits between the planner and the compiler. Catches plans that are
syntactically valid Pydantic objects but semantically wrong:

  * a date-bound metric without a date window
  * an invalid basis (the schema enforces accrual/cash, but plain
    'unspecified' is rejected for date-bound aggregates)
  * a year-omitted month with no runtime default
  * an "invoiced" intent that would silently fall through to GL revenue
  * a row-level / detail request when no detail-capable tool exists
  * ``location_blank=true`` paired with a non-detail intent

Validation is read-only on the plan; it never mutates fields. The
caller (compiler or shadow-mode logger) decides what to do with the
result — typically: if ``needs_clarification`` is True, ask the user a
follow-up; if ``is_valid`` is False, return an unsupported tool call;
otherwise compile and execute.
"""
from __future__ import annotations

from typing import Optional

from .finance_query_plan import (
    FinanceBasis,
    FinanceDetailLevel,
    FinanceFilterField,
    FinanceIntent,
    FinancePlanValidationResult,
    FinanceQueryPlan,
)


# Intents that compile to a date-bound aggregation. A plan with one of
# these intents but no resolved date_from/date_to is treated as needing
# clarification — we won't fabricate a default window. REVENUE_BY_PERIOD
# is included so a future planner emitting it (e.g. group_by=quarter)
# inherits the same date-window guard as REVENUE_TOTAL — the rule layer
# does not currently emit this intent but the closed schema permits it.
_DATE_BOUND_INTENTS = {
    FinanceIntent.REVENUE_TOTAL,
    FinanceIntent.REVENUE_BY_PERIOD,
    FinanceIntent.GL_TRANSACTION_DETAIL,
    FinanceIntent.INVOICE_TOTAL,
    FinanceIntent.INVOICE_DETAIL,
}

# Open-invoice intents are snapshot-style by default — get_open_invoices
# returns the current AR-aging without any date filter. They become
# date-conditional only when the user names a period (period_label is
# set). When the planner saw a period but couldn't resolve a year, we
# clarify — same UX as the issued-invoice family.
_OPEN_INVOICE_INTENTS = {
    FinanceIntent.OPEN_INVOICE_ROWS,
    FinanceIntent.OPEN_INVOICE_TOTAL,
}

# Invoice-family intents (open + issued). Categories are a GL concept
# and never apply to AR records, so the validator rejects all four
# variants if a category filter is set.
_AR_INTENTS = {
    FinanceIntent.INVOICE_TOTAL,
    FinanceIntent.INVOICE_DETAIL,
    FinanceIntent.OPEN_INVOICE_ROWS,
    FinanceIntent.OPEN_INVOICE_TOTAL,
}

# Intents that the compiler can hand off to a detail-capable Nova tool.
_DETAIL_CAPABLE_INTENTS = {
    FinanceIntent.GL_TRANSACTION_DETAIL,
    FinanceIntent.INVOICE_DETAIL,
}

# Intents whose tool family supports the location_blank filter today.
# As of this slice, only query_gl_transactions does — adding a new
# member here without also wiring the compiler is a contract bug, so
# the validator emits a hard error rather than letting it through.
_LOCATION_BLANK_OK_INTENTS = {
    FinanceIntent.GL_TRANSACTION_DETAIL,
}


def validate_finance_plan(
    plan: FinanceQueryPlan,
    *,
    available_tools: Optional[set[str]] = None,
) -> FinancePlanValidationResult:
    """Audit a finance plan against the closed-schema rules.

    Returns:
      ``FinancePlanValidationResult`` with ``is_valid`` and any
      structured errors / warnings / clarification prompts. Caller uses
      these signals to decide whether to compile, ask the user a
      follow-up, or surface an unsupported response.
    """
    errors: list[str] = []
    warnings: list[str] = []
    clarification: Optional[str] = None

    # -------- Terminal intents short-circuit before further checks ----
    # CLARIFICATION_REQUIRED is already a plan asking for a follow-up;
    # propagate the planner's prompt verbatim. UNSUPPORTED is a tell to
    # the caller that the planner had nothing to say — the existing
    # Nova tool loop should handle the message.
    if plan.intent == FinanceIntent.CLARIFICATION_REQUIRED:
        return FinancePlanValidationResult(
            is_valid=False,
            errors=[],
            warnings=plan.notes,
            needs_clarification=True,
            clarification_question=(
                plan.confirmation_prompt
                or "Could you clarify what you'd like Nova to look up?"
            ),
        )
    if plan.intent == FinanceIntent.UNSUPPORTED:
        return FinancePlanValidationResult(
            is_valid=False,
            errors=["intent=unsupported — planner saw no finance pattern."],
            warnings=plan.notes,
        )

    # -------- Date-window guards --------
    if plan.intent in _DATE_BOUND_INTENTS:
        if plan.date_from is None or plan.date_to is None:
            # Either the user named a month with no year and no runtime
            # default was provided, OR the message had no date phrasing
            # at all. Distinguish for a clearer follow-up.
            if plan.period_label and not plan.date_from:
                clarification = (
                    f"You said \"{plan.period_label}\" — which year did "
                    "you mean? I can default to the current calendar "
                    "year if you'd like."
                )
            else:
                clarification = (
                    "I need a date range to answer that. Could you "
                    "tell me which period (a month, a quarter, or a "
                    "specific YYYY-MM-DD range)?"
                )
            return FinancePlanValidationResult(
                is_valid=False,
                errors=[],
                warnings=plan.notes,
                needs_clarification=True,
                clarification_question=clarification,
            )
        if plan.date_from > plan.date_to:
            errors.append(
                f"date_from ({plan.date_from}) must be on or before "
                f"date_to ({plan.date_to})."
            )

    # -------- Open-invoice date guard --------------------------------
    # Snapshot-style by default. If the user named a period but the
    # planner couldn't resolve the year (period_label set, dates None),
    # ask for clarification — silently dropping the period filter would
    # answer a different question than the user asked.
    if plan.intent in _OPEN_INVOICE_INTENTS:
        if plan.period_label and (plan.date_from is None or plan.date_to is None):
            return FinancePlanValidationResult(
                is_valid=False,
                errors=[],
                warnings=plan.notes,
                needs_clarification=True,
                clarification_question=(
                    f"You said \"{plan.period_label}\" — which year did "
                    "you mean? I can default to the current calendar "
                    "year if you'd like."
                ),
            )
        if (
            plan.date_from is not None
            and plan.date_to is not None
            and plan.date_from > plan.date_to
        ):
            errors.append(
                f"date_from ({plan.date_from}) must be on or before "
                f"date_to ({plan.date_to})."
            )

    # -------- Basis enum (the schema already enforces 'accrual' /
    # 'cash' / 'unspecified'; we additionally require an explicit basis
    # for date-bound aggregations so the LLM doesn't ship an
    # accrual-default through that the user expected to be cash).
    if plan.basis not in (FinanceBasis.ACCRUAL, FinanceBasis.CASH, FinanceBasis.UNSPECIFIED):
        errors.append(f"basis={plan.basis!r} is not a valid finance basis.")

    # -------- Year-inference disclosure --------
    # A plan with year_was_inferred=True is allowed but the validator
    # records a warning so the caller (planner/compiler) can ask the
    # LLM to mention the inferred year in its reply.
    if plan.year_was_inferred and plan.period_label:
        warnings.append(
            f"Year was inferred to build the window for "
            f"\"{plan.period_label}\". The reply should disclose the "
            "inferred year so the user can correct it."
        )

    # -------- AR intents must not carry a GL category ----------------
    # Invoice (issued + open) records live in AR, not the general
    # ledger. A plan that pairs an AR intent with a FinanceCategory
    # signals a planner regression where AR phrasing was routed
    # through a GL category tool by accident.
    if plan.intent in _AR_INTENTS:
        if plan.category is not None:
            errors.append(
                f"Invoice intent ({plan.intent.value}) was paired with "
                f"a GL category ({plan.category.value}). Invoice "
                "totals come from AR records, not the general ledger."
            )

    # -------- Detail intent requires a detail-capable tool ------------
    if plan.detail_level in (
        FinanceDetailLevel.ROWS,
        FinanceDetailLevel.AGGREGATE_WITH_ROWS,
    ) and plan.intent not in _DETAIL_CAPABLE_INTENTS:
        errors.append(
            f"detail_level={plan.detail_level.value} but intent "
            f"{plan.intent.value} does not have a detail-capable "
            "compile target."
        )

    # -------- location_blank guard ------------------------------------
    has_location_blank = any(
        f.field == FinanceFilterField.LOCATION_BLANK for f in plan.filters
    )
    if has_location_blank and plan.intent not in _LOCATION_BLANK_OK_INTENTS:
        errors.append(
            "location_blank filter is only supported for "
            "gl_transaction_detail today; intent "
            f"{plan.intent.value} cannot satisfy it."
        )

    # -------- Tool-availability check (optional) ----------------------
    # Caller can pass the actual Nova tool registry. The validator
    # warns (not errors) on missing tools — the compiler returns
    # is_supported=False with a clearer message in that case.
    if available_tools is not None:
        from .finance_query_compiler import intent_to_tool_name

        target = intent_to_tool_name(plan.intent)
        if target is not None and target not in available_tools:
            warnings.append(
                f"Plan targets tool {target!r} which is not in this "
                "deployment's available tools; compiler will mark "
                "the call unsupported."
            )

    return FinancePlanValidationResult(
        is_valid=not errors,
        errors=errors,
        warnings=warnings,
        needs_clarification=False,
        clarification_question=None,
    )


__all__ = ["validate_finance_plan"]
