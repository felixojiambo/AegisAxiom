"""Tests for the finance Query Planner / Validator / Compiler.

All synthetic prompts. Years used in fixtures (2030, 2031, 2032) are
deliberately distant from any production tenant's books — a sneaky
production-year hardcode in the planner would surface here as a
regression on a synthetic year.

No expected totals are asserted (the planner does no arithmetic). No
real account numbers, customer names, or tenant names appear anywhere.
"""
from __future__ import annotations

import os
from datetime import date
from unittest.mock import patch

import pytest

from services.ai.agents.finance_query_compiler import (
    compile_finance_plan,
    intent_to_tool_name,
)
from services.ai.agents.finance_query_plan import (
    CompiledFinanceToolCall,
    FinanceBasis,
    FinanceCategory,
    FinanceDetailLevel,
    FinanceFilter,
    FinanceFilterField,
    FinanceFilterOp,
    FinanceIntent,
    FinanceMetric,
    FinancePlanValidationResult,
    FinanceQueryPlan,
)
from services.ai.agents.finance_query_planner import plan_finance_query
from services.ai.agents.finance_query_shadow import (
    get_planner_mode,
    is_shadow_enabled,
    run_shadow_evaluation,
)
from services.ai.agents.finance_query_validator import validate_finance_plan


# A stable runtime "today" for tests that need the planner's
# year-default behavior. Synthetic — not aligned with any production
# year.
_RUNTIME_TODAY = date(2030, 7, 15)


# ---------------------------------------------------------------------------
# Phase 1 — schema invariants
# ---------------------------------------------------------------------------


class TestSchemaClosedness:
    """The closed-schema models reject unknown fields and refuse to
    serialize raw Mongo operators."""

    def test_finance_filter_rejects_unknown_field(self):
        with pytest.raises(Exception):
            FinanceFilter(
                # type: ignore[arg-type] — deliberately invalid
                field="raw_data.SomeRandomField",
                op=FinanceFilterOp.EQ,
                value="x",
            )

    def test_finance_filter_rejects_unknown_op(self):
        with pytest.raises(Exception):
            FinanceFilter(
                field=FinanceFilterField.CATEGORY,
                op="$gte",  # type: ignore[arg-type]
                value="X",
            )

    def test_finance_query_plan_rejects_extra_fields(self):
        # ConfigDict(extra="forbid") on the model — passing an
        # unknown field must raise.
        with pytest.raises(Exception):
            FinanceQueryPlan(
                intent=FinanceIntent.REVENUE_TOTAL,
                raw_user_message="hi",
                # type: ignore[call-arg] — deliberately invalid
                mongo_pipeline=[{"$match": {}}],
            )

    def test_plan_has_raw_mongo_detector(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.REVENUE_TOTAL,
            raw_user_message="x",
        )
        assert plan.has_raw_mongo() is False

    def test_has_raw_mongo_detects_nested_dollar_where(self):
        # A $where literal nested inside a FinanceFilter.value must be
        # detected by the recursive walker. The previous top-level
        # repr scan would catch it via repr() noise, but a value like
        # the dict below makes the leak survive a stringification
        # round-trip and stay visible to the walker.
        plan = FinanceQueryPlan(
            intent=FinanceIntent.REVENUE_TOTAL,
            raw_user_message="x",
            filters=[
                FinanceFilter(
                    field=FinanceFilterField.SEARCH,
                    op=FinanceFilterOp.EQ,
                    value={"hostile": {"$where": "function() { return true; }"}},
                ),
            ],
        )
        assert plan.has_raw_mongo() is True

    def test_has_raw_mongo_detects_nested_in_list(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.REVENUE_TOTAL,
            raw_user_message="x",
            filters=[
                FinanceFilter(
                    field=FinanceFilterField.SEARCH,
                    op=FinanceFilterOp.EQ,
                    value=["safe", "$regex"],
                ),
            ],
        )
        assert plan.has_raw_mongo() is True


# ---------------------------------------------------------------------------
# Phase 2 — planner rules
# ---------------------------------------------------------------------------


class TestPlannerInvoiceVsRevenue:
    """'Invoiced' must not silently become GL revenue."""

    def test_invoiced_prompt_yields_invoice_intent(self):
        plan = plan_finance_query(
            "how much have we invoiced in May",
            runtime_today=_RUNTIME_TODAY,
        )
        assert plan.intent in (
            FinanceIntent.INVOICE_TOTAL,
            FinanceIntent.INVOICE_DETAIL,
        )
        # The metric must be invoice_amount, NOT revenue.
        assert plan.metric == FinanceMetric.INVOICE_AMOUNT

    def test_revenue_prompt_yields_revenue_intent(self):
        plan = plan_finance_query(
            "how much revenue did we earn in May",
            runtime_today=_RUNTIME_TODAY,
        )
        assert plan.intent == FinanceIntent.REVENUE_TOTAL
        assert plan.metric == FinanceMetric.REVENUE


class TestPlannerOpenInvoiceVsIssuedInvoice:
    """Open-invoice phrases must classify as the OPEN_INVOICE_*
    intent family, BEFORE the generic invoice family runs. Routing
    "show me open invoices" through INVOICE_TOTAL would compile to
    query_invoice_totals (which includes paid + closed records) and
    silently inflate the answer."""

    def test_show_me_open_invoices_classifies_as_open_invoice_rows(self):
        plan = plan_finance_query(
            "Show me open invoices in May 2031",
            runtime_today=_RUNTIME_TODAY,
        )
        assert plan.intent == FinanceIntent.OPEN_INVOICE_ROWS
        # Date filter from the prompt is preserved on the plan so the
        # compiler can forward it to get_open_invoices.
        assert plan.date_from == "2031-05-01"
        assert plan.date_to == "2031-05-31"

    def test_outstanding_invoices_classifies_as_open_invoice(self):
        plan = plan_finance_query(
            "What invoices are outstanding?",
            runtime_today=_RUNTIME_TODAY,
        )
        assert plan.intent in (
            FinanceIntent.OPEN_INVOICE_ROWS,
            FinanceIntent.OPEN_INVOICE_TOTAL,
        )

    def test_overdue_invoices_classifies_as_open_invoice_rows(self):
        plan = plan_finance_query(
            "Show overdue invoices",
            runtime_today=_RUNTIME_TODAY,
        )
        assert plan.intent == FinanceIntent.OPEN_INVOICE_ROWS

    def test_unpaid_invoices_classifies_as_open_invoice(self):
        plan = plan_finance_query(
            "List unpaid invoices",
            runtime_today=_RUNTIME_TODAY,
        )
        assert plan.intent in (
            FinanceIntent.OPEN_INVOICE_ROWS,
            FinanceIntent.OPEN_INVOICE_TOTAL,
        )

    def test_open_invoice_balance_classifies_as_open_invoice_total(self):
        plan = plan_finance_query(
            "How much open invoice balance do we have?",
            runtime_today=_RUNTIME_TODAY,
        )
        assert plan.intent == FinanceIntent.OPEN_INVOICE_TOTAL

    def test_owed_phrase_without_invoice_word_classifies_as_open_invoice(self):
        plan = plan_finance_query(
            "What is owed to us right now?",
            runtime_today=_RUNTIME_TODAY,
        )
        assert plan.intent in (
            FinanceIntent.OPEN_INVOICE_ROWS,
            FinanceIntent.OPEN_INVOICE_TOTAL,
        )

    def test_issued_invoiced_phrase_still_classifies_as_invoice_total(self):
        # Regression: "invoiced" without an open/outstanding qualifier
        # must continue to classify as the issued-invoice family.
        plan = plan_finance_query(
            "how much have we invoiced in May",
            runtime_today=_RUNTIME_TODAY,
        )
        assert plan.intent == FinanceIntent.INVOICE_TOTAL

    def test_show_me_invoices_issued_still_classifies_as_invoice_detail(self):
        plan = plan_finance_query(
            "show me invoices issued in May",
            runtime_today=_RUNTIME_TODAY,
        )
        # No open/outstanding/unpaid/overdue qualifier → issued family.
        assert plan.intent in (
            FinanceIntent.INVOICE_TOTAL,
            FinanceIntent.INVOICE_DETAIL,
        )
        assert plan.intent not in (
            FinanceIntent.OPEN_INVOICE_ROWS,
            FinanceIntent.OPEN_INVOICE_TOTAL,
        )


class TestPlannerCategoryRouting:
    """A specific revenue family in the prompt sets the category enum."""

    def test_field_service_revenue_sets_fs_revenue(self):
        plan = plan_finance_query(
            "what was field service revenue in March",
            runtime_today=_RUNTIME_TODAY,
        )
        assert plan.intent == FinanceIntent.REVENUE_TOTAL
        assert plan.category == FinanceCategory.FS_REVENUE

    def test_professional_services_revenue_sets_ps_revenue(self):
        plan = plan_finance_query(
            "professional services revenue this April",
            runtime_today=_RUNTIME_TODAY,
        )
        assert plan.intent == FinanceIntent.REVENUE_TOTAL
        assert plan.category == FinanceCategory.PS_REVENUE

    def test_no_category_when_unspecified(self):
        plan = plan_finance_query(
            "total revenue in March",
            runtime_today=_RUNTIME_TODAY,
        )
        assert plan.intent == FinanceIntent.REVENUE_TOTAL
        assert plan.category is None


class TestPlannerTransactionDetailWithPriorContext:
    """Drill-down phrasing requires either an explicit detail keyword
    or a prior-total signal in the conversation."""

    def test_transactions_behind_with_prior_total_compiles_to_detail(self):
        plan = plan_finance_query(
            "show me the transactions behind that total for April",
            runtime_today=_RUNTIME_TODAY,
            has_prior_total=True,
        )
        assert plan.intent == FinanceIntent.GL_TRANSACTION_DETAIL
        assert plan.detail_level == FinanceDetailLevel.AGGREGATE_WITH_ROWS

    def test_explicit_detail_phrase_compiles_without_prior_total(self):
        plan = plan_finance_query(
            "fetch all the transactions for May",
            runtime_today=_RUNTIME_TODAY,
            has_prior_total=False,
        )
        assert plan.intent == FinanceIntent.GL_TRANSACTION_DETAIL

    def test_backref_without_prior_total_requires_clarification(self):
        plan = plan_finance_query(
            "show me the transactions behind this total",
            runtime_today=_RUNTIME_TODAY,
            has_prior_total=False,
        )
        assert plan.intent == FinanceIntent.CLARIFICATION_REQUIRED
        assert plan.requires_confirmation is True
        assert plan.confirmation_prompt is not None


class TestPlannerBlankLocationFilter:
    """``location_blank`` is captured as a closed filter; the validator
    enforces detail-tool support downstream."""

    def test_blank_location_sets_filter_on_detail_request(self):
        plan = plan_finance_query(
            "show transactions where location is blank in May",
            runtime_today=_RUNTIME_TODAY,
        )
        # Detail intent — location_blank is allowed.
        assert plan.intent == FinanceIntent.GL_TRANSACTION_DETAIL
        assert any(
            f.field == FinanceFilterField.LOCATION_BLANK and f.value is True
            for f in plan.filters
        )

    def test_blank_location_on_revenue_intent_is_validation_error(self):
        # Build a revenue-aggregate plan with location_blank filter
        # (as if a future planner regression placed it there). The
        # validator must refuse.
        plan = FinanceQueryPlan(
            intent=FinanceIntent.REVENUE_TOTAL,
            metric=FinanceMetric.REVENUE,
            date_from="2030-05-01",
            date_to="2030-05-31",
            filters=[
                FinanceFilter(
                    field=FinanceFilterField.LOCATION_BLANK,
                    op=FinanceFilterOp.IS_BLANK,
                    value=True,
                )
            ],
            raw_user_message="x",
        )
        result = validate_finance_plan(plan)
        assert not result.is_valid
        assert any("location_blank" in e for e in result.errors)


class TestPlannerMissingYearRequiresConfirmation:
    """Named-month-without-year requires confirmation when no runtime
    default is provided."""

    def test_no_runtime_today_yields_clarification(self):
        plan = plan_finance_query(
            "how much revenue in May",
            runtime_today=None,  # no default available
        )
        assert plan.date_from is None
        assert plan.date_to is None
        result = validate_finance_plan(plan)
        assert result.needs_clarification is True
        assert "May" in (result.clarification_question or "")

    def test_runtime_today_resolves_year_with_disclosure(self):
        plan = plan_finance_query(
            "how much revenue in May",
            runtime_today=_RUNTIME_TODAY,
        )
        assert plan.date_from is not None
        assert plan.date_to is not None
        assert plan.year_was_inferred is True
        result = validate_finance_plan(plan)
        # Validation passes but warns the caller to disclose the
        # inferred year in the reply.
        assert result.is_valid
        assert any("inferred" in w for w in result.warnings)


class TestPlannerNoRawMongo:
    """Whatever the planner emits must contain no Mongo operators."""

    @pytest.mark.parametrize(
        "prompt",
        [
            "how much revenue in May",
            "field service revenue this April",
            "show me the transactions behind that total",
            "MRR in March 2031",
            "Q2 2032 revenue",
            "show me open invoices in May 2031",
            "how much open invoice balance do we have",
            "what invoices are outstanding",
        ],
    )
    def test_planner_output_has_no_raw_mongo(self, prompt: str):
        plan = plan_finance_query(prompt, runtime_today=_RUNTIME_TODAY, has_prior_total=True)
        assert plan.has_raw_mongo() is False, (
            f"plan dump contained a Mongo operator: {plan.model_dump_json()}"
        )


# ---------------------------------------------------------------------------
# Phase 3 — validator rules
# ---------------------------------------------------------------------------


class TestValidator:
    def test_unsupported_intent_is_invalid(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.UNSUPPORTED,
            raw_user_message="hello",
        )
        result = validate_finance_plan(plan)
        assert not result.is_valid

    def test_clarification_required_intent_propagates_question(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.CLARIFICATION_REQUIRED,
            raw_user_message="x",
            confirmation_prompt="Which year did you mean?",
        )
        result = validate_finance_plan(plan)
        assert result.needs_clarification is True
        assert result.clarification_question == "Which year did you mean?"

    def test_invoice_intent_with_category_is_error(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.INVOICE_TOTAL,
            metric=FinanceMetric.INVOICE_AMOUNT,
            category=FinanceCategory.PS_REVENUE,  # GL category — wrong dimension
            date_from="2030-01-01",
            date_to="2030-01-31",
            raw_user_message="x",
        )
        result = validate_finance_plan(plan)
        assert not result.is_valid
        assert any("GL category" in e for e in result.errors)

    def test_inverted_date_range_is_error(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.REVENUE_TOTAL,
            metric=FinanceMetric.REVENUE,
            date_from="2030-05-31",
            date_to="2030-05-01",
            raw_user_message="x",
        )
        result = validate_finance_plan(plan)
        assert not result.is_valid

    def test_open_invoice_intent_with_category_is_error(self):
        # AR records carry no GL category — pairing an open-invoice
        # plan with a category signals a planner regression.
        plan = FinanceQueryPlan(
            intent=FinanceIntent.OPEN_INVOICE_TOTAL,
            metric=FinanceMetric.INVOICE_AMOUNT,
            category=FinanceCategory.PS_REVENUE,
            raw_user_message="x",
        )
        result = validate_finance_plan(plan)
        assert not result.is_valid

    def test_open_invoice_named_period_without_year_clarifies(self):
        # Period named ("May") but year unresolvable → planner emits a
        # plan with period_label set and dates None. The validator
        # asks which year rather than silently dropping the period
        # filter.
        plan = FinanceQueryPlan(
            intent=FinanceIntent.OPEN_INVOICE_ROWS,
            metric=FinanceMetric.INVOICE_AMOUNT,
            period_label="May",
            raw_user_message="show me open invoices in May",
        )
        result = validate_finance_plan(plan)
        assert result.needs_clarification is True
        assert "May" in (result.clarification_question or "")

    def test_open_invoice_snapshot_without_period_is_valid(self):
        # No period mentioned (period_label is None) → snapshot is
        # valid even without dates. The compiler will return the
        # current AR-aging.
        plan = FinanceQueryPlan(
            intent=FinanceIntent.OPEN_INVOICE_ROWS,
            metric=FinanceMetric.INVOICE_AMOUNT,
            raw_user_message="what invoices are outstanding",
        )
        result = validate_finance_plan(plan)
        assert result.is_valid

    def test_detail_level_with_non_detail_intent_is_error(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.REVENUE_TOTAL,
            metric=FinanceMetric.REVENUE,
            detail_level=FinanceDetailLevel.ROWS,
            date_from="2030-05-01",
            date_to="2030-05-31",
            raw_user_message="x",
        )
        result = validate_finance_plan(plan)
        assert not result.is_valid


# ---------------------------------------------------------------------------
# Phase 4 — compiler mappings
# ---------------------------------------------------------------------------


class TestCompiler:
    def test_revenue_total_maps_to_query_finance_data(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.REVENUE_TOTAL,
            metric=FinanceMetric.REVENUE,
            basis=FinanceBasis.ACCRUAL,
            date_from="2030-05-01",
            date_to="2030-05-31",
            raw_user_message="x",
        )
        out = compile_finance_plan(plan)
        assert out.is_supported is True
        assert out.tool_name == "query_finance_data"
        assert out.tool_args["metric"] == "revenue"
        assert out.tool_args["date_from"] == "2030-05-01"
        assert out.tool_args["date_to"] == "2030-05-31"
        assert out.tool_args["basis"] == "accrual"
        # No raw Mongo operators leaked into the args.
        for v in out.tool_args.values():
            assert not str(v).startswith("$")

    def test_revenue_total_with_category_includes_category_arg(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.REVENUE_TOTAL,
            metric=FinanceMetric.REVENUE,
            category=FinanceCategory.FS_REVENUE,
            date_from="2030-05-01",
            date_to="2030-05-31",
            raw_user_message="x",
        )
        out = compile_finance_plan(plan)
        assert out.tool_args["category"] == "FS_REVENUE"

    def test_gl_transaction_detail_maps_to_query_gl_transactions(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.GL_TRANSACTION_DETAIL,
            metric=FinanceMetric.REVENUE,
            detail_level=FinanceDetailLevel.AGGREGATE_WITH_ROWS,
            date_from="2030-05-01",
            date_to="2030-05-31",
            raw_user_message="x",
        )
        out = compile_finance_plan(plan)
        assert out.is_supported is True
        assert out.tool_name == "query_gl_transactions"

    def test_gl_transaction_detail_passes_location_blank(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.GL_TRANSACTION_DETAIL,
            metric=FinanceMetric.REVENUE,
            detail_level=FinanceDetailLevel.AGGREGATE_WITH_ROWS,
            date_from="2030-05-01",
            date_to="2030-05-31",
            filters=[
                FinanceFilter(
                    field=FinanceFilterField.LOCATION_BLANK,
                    op=FinanceFilterOp.IS_BLANK,
                    value=True,
                ),
            ],
            raw_user_message="x",
        )
        out = compile_finance_plan(plan)
        assert out.tool_args["location_blank"] is True

    def test_invoice_total_compiles_to_query_invoice_totals(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.INVOICE_TOTAL,
            metric=FinanceMetric.INVOICE_AMOUNT,
            date_from="2030-05-01",
            date_to="2030-05-31",
            raw_user_message="x",
        )
        out = compile_finance_plan(plan)
        assert out.is_supported is True
        assert out.tool_name == "query_invoice_totals"
        assert out.tool_args["date_from"] == "2030-05-01"
        assert out.tool_args["date_to"] == "2030-05-31"
        # Aggregate intent → include_rows defaults to False so a bare
        # "how much have we invoiced" doesn't waste the row preview.
        assert out.tool_args["include_rows"] is False
        # Critical contract: invoice intents must NEVER compile to
        # the open-invoice tool (that source is OPEN-only).
        assert out.tool_name != "get_open_invoices"

    def test_invoice_total_with_explicit_rows_detail_includes_rows(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.INVOICE_TOTAL,
            metric=FinanceMetric.INVOICE_AMOUNT,
            detail_level=FinanceDetailLevel.AGGREGATE_WITH_ROWS,
            date_from="2030-05-01",
            date_to="2030-05-31",
            raw_user_message="x",
        )
        out = compile_finance_plan(plan)
        assert out.tool_args["include_rows"] is True

    def test_invoice_detail_compiles_to_query_invoice_totals_with_rows(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.INVOICE_DETAIL,
            metric=FinanceMetric.INVOICE_AMOUNT,
            detail_level=FinanceDetailLevel.ROWS,
            date_from="2030-05-01",
            date_to="2030-05-31",
            raw_user_message="x",
        )
        out = compile_finance_plan(plan)
        assert out.is_supported is True
        assert out.tool_name == "query_invoice_totals"
        assert out.tool_args["include_rows"] is True
        # Same critical contract as above.
        assert out.tool_name != "get_open_invoices"

    def test_invoice_intent_passes_supported_filters(self):
        # All four supported pass-through fields plus search.
        plan = FinanceQueryPlan(
            intent=FinanceIntent.INVOICE_TOTAL,
            metric=FinanceMetric.INVOICE_AMOUNT,
            date_from="2030-05-01",
            date_to="2030-05-31",
            filters=[
                FinanceFilter(field=FinanceFilterField.CUSTOMER, op=FinanceFilterOp.EQ, value="Acme Corp"),
                FinanceFilter(field=FinanceFilterField.STATUS, op=FinanceFilterOp.EQ, value="EmailSent"),
                FinanceFilter(field=FinanceFilterField.CLASS_NAME, op=FinanceFilterOp.EQ, value="ClassA"),
                FinanceFilter(field=FinanceFilterField.LOCATION, op=FinanceFilterOp.EQ, value="HQ"),
                FinanceFilter(field=FinanceFilterField.SEARCH, op=FinanceFilterOp.EQ, value="INV-99"),
            ],
            raw_user_message="x",
        )
        out = compile_finance_plan(plan)
        assert out.is_supported is True
        # All supported filters reach the compiled tool args verbatim.
        assert out.tool_args["customer"] == "Acme Corp"
        assert out.tool_args["status"] == "EmailSent"
        assert out.tool_args["class_name"] == "ClassA"
        assert out.tool_args["location"] == "HQ"
        assert out.tool_args["search"] == "INV-99"

    def test_invoice_intent_drops_gl_only_filters(self):
        # ACCOUNT_PREFIX is a GL concept and must not leak into the
        # issued-invoice tool args.
        plan = FinanceQueryPlan(
            intent=FinanceIntent.INVOICE_TOTAL,
            metric=FinanceMetric.INVOICE_AMOUNT,
            date_from="2030-05-01",
            date_to="2030-05-31",
            filters=[
                FinanceFilter(
                    field=FinanceFilterField.ACCOUNT_PREFIX,
                    op=FinanceFilterOp.EQ,
                    value="50101",
                ),
            ],
            raw_user_message="x",
        )
        out = compile_finance_plan(plan)
        assert "account_prefix" not in out.tool_args

    def test_invoice_total_unsupported_when_query_invoice_totals_missing(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.INVOICE_TOTAL,
            metric=FinanceMetric.INVOICE_AMOUNT,
            date_from="2030-05-01",
            date_to="2030-05-31",
            raw_user_message="x",
        )
        # Deployment without query_invoice_totals registered.
        out = compile_finance_plan(plan, available_tools={"get_open_invoices", "query_finance_data"})
        assert out.is_supported is False
        assert out.tool_name is None
        # The unsupported message must explicitly call out the
        # don't-fall-back-to-open-invoices rule so a future maintainer
        # can't quietly swap the route.
        assert "get_open_invoices" in (out.unsupported_reason or "")
        assert "OPEN-only" in (out.unsupported_reason or "")

    def test_invoice_detail_unsupported_when_query_invoice_totals_missing(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.INVOICE_DETAIL,
            metric=FinanceMetric.INVOICE_AMOUNT,
            detail_level=FinanceDetailLevel.ROWS,
            date_from="2030-05-01",
            date_to="2030-05-31",
            raw_user_message="x",
        )
        out = compile_finance_plan(plan, available_tools={"get_open_invoices"})
        assert out.is_supported is False
        assert out.tool_name is None
        # NEVER routed to the open-invoice tool, even when only that
        # is available.
        assert out.tool_name != "get_open_invoices"

    def test_intent_to_tool_name_for_invoice_intents(self):
        # The mapping must surface query_invoice_totals; previously
        # invoice intents returned None.
        from services.ai.agents.finance_query_compiler import intent_to_tool_name as _itn
        assert _itn(FinanceIntent.INVOICE_TOTAL) == "query_invoice_totals"
        assert _itn(FinanceIntent.INVOICE_DETAIL) == "query_invoice_totals"

    def test_intent_to_tool_name_for_open_invoice_intents(self):
        # Open-invoice intents must map to get_open_invoices and NEVER
        # to query_invoice_totals (which includes paid + closed
        # records).
        from services.ai.agents.finance_query_compiler import intent_to_tool_name as _itn
        assert _itn(FinanceIntent.OPEN_INVOICE_ROWS) == "get_open_invoices"
        assert _itn(FinanceIntent.OPEN_INVOICE_TOTAL) == "get_open_invoices"

    def test_open_invoice_rows_compiles_to_get_open_invoices(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.OPEN_INVOICE_ROWS,
            metric=FinanceMetric.INVOICE_AMOUNT,
            date_from="2031-05-01",
            date_to="2031-05-31",
            raw_user_message="x",
        )
        out = compile_finance_plan(plan)
        assert out.is_supported is True
        assert out.tool_name == "get_open_invoices"
        # Critical contract: open-invoice intents must NEVER compile
        # to query_invoice_totals (paid + closed inflate the answer).
        assert out.tool_name != "query_invoice_totals"
        # Date filter from the plan is preserved on the compiled args.
        assert out.tool_args["date_from"] == "2031-05-01"
        assert out.tool_args["date_to"] == "2031-05-31"
        assert "limit" in out.tool_args

    def test_open_invoice_total_compiles_to_get_open_invoices(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.OPEN_INVOICE_TOTAL,
            metric=FinanceMetric.INVOICE_AMOUNT,
            raw_user_message="x",
        )
        out = compile_finance_plan(plan)
        assert out.is_supported is True
        assert out.tool_name == "get_open_invoices"
        # The tool returns server-computed total_amount /
        # total_open_balance, so the LLM never sums rows itself.
        assert out.tool_name != "query_invoice_totals"
        # No date in the plan → no date in the args (snapshot mode).
        assert "date_from" not in out.tool_args
        assert "date_to" not in out.tool_args

    def test_open_invoice_passes_search_and_class_filters(self):
        # Customer and search filters both fold into get_open_invoices'
        # `search` arg (the tool exposes no separate `customer` arg).
        # Class pass-through is preserved.
        plan = FinanceQueryPlan(
            intent=FinanceIntent.OPEN_INVOICE_ROWS,
            metric=FinanceMetric.INVOICE_AMOUNT,
            filters=[
                FinanceFilter(field=FinanceFilterField.SEARCH, op=FinanceFilterOp.EQ, value="INV-99"),
                FinanceFilter(field=FinanceFilterField.CLASS_NAME, op=FinanceFilterOp.EQ, value="ClassA"),
            ],
            raw_user_message="x",
        )
        out = compile_finance_plan(plan)
        assert out.tool_args["search"] == "INV-99"
        assert out.tool_args["class_name"] == "ClassA"

    def test_open_invoice_drops_unsupported_filters(self):
        # STATUS and LOCATION are not on get_open_invoices' surface —
        # they get dropped silently rather than passed through as
        # invalid kwargs.
        plan = FinanceQueryPlan(
            intent=FinanceIntent.OPEN_INVOICE_ROWS,
            metric=FinanceMetric.INVOICE_AMOUNT,
            filters=[
                FinanceFilter(field=FinanceFilterField.STATUS, op=FinanceFilterOp.EQ, value="EmailSent"),
                FinanceFilter(field=FinanceFilterField.LOCATION, op=FinanceFilterOp.EQ, value="HQ"),
            ],
            raw_user_message="x",
        )
        out = compile_finance_plan(plan)
        assert "status" not in out.tool_args
        assert "location" not in out.tool_args

    def test_open_invoice_unsupported_when_get_open_invoices_missing(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.OPEN_INVOICE_ROWS,
            metric=FinanceMetric.INVOICE_AMOUNT,
            raw_user_message="x",
        )
        # Deployment without get_open_invoices registered.
        out = compile_finance_plan(
            plan,
            available_tools={"query_invoice_totals", "query_finance_data"},
        )
        assert out.is_supported is False
        assert out.tool_name is None
        # Must NOT silently route to query_invoice_totals — that source
        # would inflate the open-invoice answer with paid + closed
        # records.
        assert out.tool_name != "query_invoice_totals"
        assert "query_invoice_totals" in (out.unsupported_reason or "")

    def test_open_invoice_plans_never_compile_to_query_invoice_totals(self):
        # Independent of tool availability, the closed mapping must
        # never produce query_invoice_totals for an open-invoice plan.
        for intent in (
            FinanceIntent.OPEN_INVOICE_ROWS,
            FinanceIntent.OPEN_INVOICE_TOTAL,
        ):
            plan = FinanceQueryPlan(
                intent=intent,
                metric=FinanceMetric.INVOICE_AMOUNT,
                raw_user_message="x",
            )
            out = compile_finance_plan(plan)
            assert out.tool_name != "query_invoice_totals"
            assert out.tool_name == "get_open_invoices"

    def test_issued_invoice_plans_never_compile_to_get_open_invoices(self):
        # Symmetric contract: issued-invoice plans must never compile
        # to the open-invoice tool, even with both tools registered.
        for intent in (
            FinanceIntent.INVOICE_TOTAL,
            FinanceIntent.INVOICE_DETAIL,
        ):
            plan = FinanceQueryPlan(
                intent=intent,
                metric=FinanceMetric.INVOICE_AMOUNT,
                detail_level=(
                    FinanceDetailLevel.ROWS
                    if intent == FinanceIntent.INVOICE_DETAIL
                    else FinanceDetailLevel.AGGREGATE
                ),
                date_from="2030-05-01",
                date_to="2030-05-31",
                raw_user_message="x",
            )
            out = compile_finance_plan(plan)
            assert out.tool_name == "query_invoice_totals"
            assert out.tool_name != "get_open_invoices"

    def test_unsupported_plan_does_not_emit_a_tool_call(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.UNSUPPORTED,
            raw_user_message="x",
        )
        out = compile_finance_plan(plan)
        assert out.is_supported is False
        assert out.tool_name is None
        assert out.tool_args == {}

    def test_clarification_plan_does_not_emit_a_tool_call(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.CLARIFICATION_REQUIRED,
            raw_user_message="x",
            confirmation_prompt="Which year?",
        )
        out = compile_finance_plan(plan)
        assert out.is_supported is False
        assert out.tool_name is None

    def test_unavailable_tool_marks_unsupported(self):
        plan = FinanceQueryPlan(
            intent=FinanceIntent.REVENUE_TOTAL,
            metric=FinanceMetric.REVENUE,
            date_from="2030-05-01",
            date_to="2030-05-31",
            raw_user_message="x",
        )
        out = compile_finance_plan(plan, available_tools=set())  # nothing available
        assert out.is_supported is False
        assert "not registered" in (out.unsupported_reason or "")

    def test_intent_to_tool_name_returns_none_for_terminal_intents(self):
        # UNSUPPORTED and CLARIFICATION_REQUIRED have no compile target
        # by definition. INVOICE_TOTAL is no longer in this set —
        # since query_invoice_totals shipped, invoice intents have a
        # real compile target and that contract is asserted in
        # test_intent_to_tool_name_for_invoice_intents above.
        assert intent_to_tool_name(FinanceIntent.UNSUPPORTED) is None
        assert intent_to_tool_name(FinanceIntent.CLARIFICATION_REQUIRED) is None


# ---------------------------------------------------------------------------
# Phase 5 — shadow mode
# ---------------------------------------------------------------------------


class TestShadowMode:
    """Env-var resolution for the shadow-mode helper.

    Canonical: NOVA_FINANCE_QUERY_PLANNER_MODE
    Legacy alias: NOVA_FINANCE_QUERY_PLANNER (read only when
    canonical is unset)
    """

    _CANON = "NOVA_FINANCE_QUERY_PLANNER_MODE"
    _LEGACY = "NOVA_FINANCE_QUERY_PLANNER"

    def _clear_env(self) -> None:
        os.environ.pop(self._CANON, None)
        os.environ.pop(self._LEGACY, None)

    def test_default_off_when_neither_var_set(self):
        with patch.dict(os.environ, {}, clear=False):
            self._clear_env()
            assert get_planner_mode() == "off"
            assert is_shadow_enabled() is False
            assert run_shadow_evaluation("how much revenue in May") is None

    def test_canonical_off_runs_nothing(self):
        with patch.dict(os.environ, {self._CANON: "off"}, clear=False):
            self._clear_env()
            os.environ[self._CANON] = "off"
            assert is_shadow_enabled() is False
            assert run_shadow_evaluation("how much revenue in May") is None

    def test_canonical_shadow_returns_payload(self):
        with patch.dict(os.environ, {self._CANON: "shadow"}, clear=False):
            self._clear_env()
            os.environ[self._CANON] = "shadow"
            payload = run_shadow_evaluation(
                "how much revenue in May",
                runtime_today=_RUNTIME_TODAY,
            )
            assert payload is not None
            assert payload["intent"] == "revenue_total"
            assert payload["compiled"]["tool_name"] == "query_finance_data"
            # PII guard: tool_args must NOT appear in the shadow log
            # payload — filter values can carry finance PII (customer
            # names, search strings) and the audit signal is the
            # tool name, not the args.
            assert "tool_args" not in payload["compiled"]
            # filter_shapes carries field/op only, never values.
            for shape in payload.get("filter_shapes", []):
                assert set(shape.keys()) == {"field", "op"}

    def test_shadow_payload_filter_shapes_omit_values(self):
        # A plan that produces filters (category from "field service
        # revenue") must surface the field/op of those filters but
        # never the value.
        with patch.dict(os.environ, {self._CANON: "shadow"}, clear=False):
            self._clear_env()
            os.environ[self._CANON] = "shadow"
            payload = run_shadow_evaluation(
                "field service revenue in May",
                runtime_today=_RUNTIME_TODAY,
            )
            assert payload is not None
            shapes = payload.get("filter_shapes")
            assert shapes is not None and len(shapes) >= 1
            for shape in shapes:
                assert "value" not in shape
                assert "field" in shape and "op" in shape

    def test_legacy_alias_still_works_when_canonical_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            self._clear_env()
            os.environ[self._LEGACY] = "shadow"
            try:
                assert is_shadow_enabled() is True
                payload = run_shadow_evaluation(
                    "how much revenue in May",
                    runtime_today=_RUNTIME_TODAY,
                )
                assert payload is not None
            finally:
                self._clear_env()

    def test_canonical_takes_precedence_over_legacy(self):
        # Canonical=off, legacy=shadow → off wins (canonical is the
        # source of truth when both are set).
        with patch.dict(os.environ, {}, clear=False):
            self._clear_env()
            os.environ[self._CANON] = "off"
            os.environ[self._LEGACY] = "shadow"
            try:
                assert get_planner_mode() == "off"
                assert is_shadow_enabled() is False
                assert run_shadow_evaluation("how much revenue in May") is None
            finally:
                self._clear_env()

    def test_canonical_shadow_with_legacy_off_still_runs_shadow(self):
        # Canonical=shadow, legacy=off → shadow wins.
        with patch.dict(os.environ, {}, clear=False):
            self._clear_env()
            os.environ[self._CANON] = "shadow"
            os.environ[self._LEGACY] = "off"
            try:
                assert is_shadow_enabled() is True
            finally:
                self._clear_env()

    def test_unknown_canonical_value_falls_back_to_off(self):
        with patch.dict(os.environ, {self._CANON: "garbage"}, clear=False):
            self._clear_env()
            os.environ[self._CANON] = "garbage"
            assert get_planner_mode() == "off"
            assert is_shadow_enabled() is False
            # User-facing execution must not be affected by an unknown
            # value — shadow eval returns None.
            assert run_shadow_evaluation("how much revenue in May") is None

    def test_unknown_legacy_value_falls_back_to_off(self):
        with patch.dict(os.environ, {}, clear=False):
            self._clear_env()
            os.environ[self._LEGACY] = "totally-bogus"
            try:
                assert get_planner_mode() == "off"
                assert is_shadow_enabled() is False
            finally:
                self._clear_env()

    def test_live_value_is_treated_as_shadow_with_warning(self):
        # 'live' is reserved; today the helper logs a warning and
        # behaves like shadow so live-mode misconfiguration is loud.
        with patch.dict(os.environ, {self._CANON: "live"}, clear=False):
            self._clear_env()
            os.environ[self._CANON] = "live"
            assert get_planner_mode() == "shadow"

    def test_shadow_swallows_planner_errors(self):
        with patch.dict(os.environ, {self._CANON: "shadow"}, clear=False):
            self._clear_env()
            os.environ[self._CANON] = "shadow"
            with patch(
                "services.ai.agents.finance_query_shadow.plan_finance_query",
                side_effect=RuntimeError("boom"),
            ):
                payload = run_shadow_evaluation(
                    "how much revenue in May",
                    runtime_today=_RUNTIME_TODAY,
                )
                assert payload is None

    def test_shadow_error_path_logs_hashed_message_not_raw(self, caplog):
        # The user message can carry finance PII. On the error path,
        # the log line must contain only a hashed token — never a
        # snippet of the message body.
        sensitive = "How much have we invoiced for Acme Industries Inc."
        with patch.dict(os.environ, {self._CANON: "shadow"}, clear=False):
            self._clear_env()
            os.environ[self._CANON] = "shadow"
            with patch(
                "services.ai.agents.finance_query_shadow.plan_finance_query",
                side_effect=RuntimeError("boom"),
            ):
                with caplog.at_level("WARNING", logger="services.ai.agents.finance_query_shadow"):
                    run_shadow_evaluation(sensitive, runtime_today=_RUNTIME_TODAY)
        log_text = caplog.text
        assert "Acme" not in log_text
        assert "Industries" not in log_text
        assert "sha256:" in log_text
        assert "message_token=" in log_text


# ---------------------------------------------------------------------------
# End-to-end planner→validator→compiler smoke tests
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """Walk the whole pipeline for the Asad-style demo prompts using
    synthetic dates only."""

    def test_revenue_total_pipeline(self):
        plan = plan_finance_query(
            "how much revenue did we earn in March",
            runtime_today=_RUNTIME_TODAY,
        )
        result = validate_finance_plan(plan)
        compiled = compile_finance_plan(plan)
        assert plan.intent == FinanceIntent.REVENUE_TOTAL
        assert result.is_valid
        assert compiled.is_supported is True
        assert compiled.tool_name == "query_finance_data"
        assert compiled.tool_args["metric"] == "revenue"

    def test_gl_transaction_detail_pipeline(self):
        plan = plan_finance_query(
            "fetch all the transactions for April where location is blank",
            runtime_today=_RUNTIME_TODAY,
        )
        result = validate_finance_plan(plan)
        compiled = compile_finance_plan(plan)
        assert plan.intent == FinanceIntent.GL_TRANSACTION_DETAIL
        assert result.is_valid
        assert compiled.tool_name == "query_gl_transactions"
        assert compiled.tool_args["location_blank"] is True

    def test_invoiced_pipeline_routes_to_query_invoice_totals(self):
        plan = plan_finance_query(
            "how much have we invoiced in February",
            runtime_today=_RUNTIME_TODAY,
        )
        compiled = compile_finance_plan(plan)
        # Plan classifies as invoice_* (the issued-invoice intent)
        # and compiles to query_invoice_totals — never to
        # get_open_invoices, which is OPEN-only.
        assert plan.intent in (
            FinanceIntent.INVOICE_TOTAL,
            FinanceIntent.INVOICE_DETAIL,
        )
        assert compiled.is_supported is True
        assert compiled.tool_name == "query_invoice_totals"
        assert compiled.tool_name != "get_open_invoices"

    def test_invoiced_pipeline_unsupported_when_invoice_tool_missing(self):
        plan = plan_finance_query(
            "how much have we invoiced in February",
            runtime_today=_RUNTIME_TODAY,
        )
        # Deployment without query_invoice_totals must NOT silently
        # route to get_open_invoices.
        compiled = compile_finance_plan(
            plan,
            available_tools={"get_open_invoices", "query_finance_data"},
        )
        assert compiled.is_supported is False
        assert compiled.tool_name is None
        assert compiled.tool_name != "get_open_invoices"

    def test_no_raw_mongo_in_compiled_output(self):
        plan = plan_finance_query(
            "field service revenue in March 2031",
            runtime_today=_RUNTIME_TODAY,
        )
        compiled = compile_finance_plan(plan)
        text = repr(compiled.tool_args)
        for op in ("$gte", "$lte", "$or", "$in", "$regex", "$exists"):
            assert op not in text

    def test_open_invoice_pipeline_routes_to_get_open_invoices(self):
        plan = plan_finance_query(
            "Show me open invoices in May 2031",
            runtime_today=_RUNTIME_TODAY,
        )
        result = validate_finance_plan(plan)
        compiled = compile_finance_plan(plan)
        assert plan.intent == FinanceIntent.OPEN_INVOICE_ROWS
        assert result.is_valid
        assert compiled.is_supported is True
        assert compiled.tool_name == "get_open_invoices"
        # NEVER compiles to query_invoice_totals — that source would
        # silently include paid + closed invoices.
        assert compiled.tool_name != "query_invoice_totals"
        # Date filter from the prompt rides through to the args.
        assert compiled.tool_args["date_from"] == "2031-05-01"
        assert compiled.tool_args["date_to"] == "2031-05-31"

    def test_open_invoice_pipeline_unsupported_when_open_tool_missing(self):
        plan = plan_finance_query(
            "Show me open invoices in May 2031",
            runtime_today=_RUNTIME_TODAY,
        )
        # Deployment without get_open_invoices: must NOT silently fall
        # back to query_invoice_totals.
        compiled = compile_finance_plan(
            plan,
            available_tools={"query_invoice_totals", "query_finance_data"},
        )
        assert compiled.is_supported is False
        assert compiled.tool_name is None
        assert compiled.tool_name != "query_invoice_totals"

    def test_open_invoice_snapshot_pipeline_without_dates(self):
        plan = plan_finance_query(
            "What invoices are outstanding?",
            runtime_today=_RUNTIME_TODAY,
        )
        result = validate_finance_plan(plan)
        compiled = compile_finance_plan(plan)
        # Snapshot intent — no period mentioned → no clarification
        # needed, no date filter on the compiled args.
        assert plan.intent in (
            FinanceIntent.OPEN_INVOICE_ROWS,
            FinanceIntent.OPEN_INVOICE_TOTAL,
        )
        assert result.is_valid
        assert compiled.tool_name == "get_open_invoices"
        assert "date_from" not in compiled.tool_args
        assert "date_to" not in compiled.tool_args


# ---------------------------------------------------------------------------
# Driver / variance explanation routing
# ---------------------------------------------------------------------------


class TestFinanceDriverExplanationPipeline:
    def test_why_did_revenue_change_routes_to_explain_finance_drivers(self):
        plan = plan_finance_query(
            "Why did revenue go down in May 2031?",
            runtime_today=_RUNTIME_TODAY,
        )
        compiled = compile_finance_plan(plan)

        assert plan.intent == FinanceIntent.FINANCE_DRIVER_EXPLANATION
        assert compiled.is_supported is True
        assert compiled.tool_name == "explain_finance_drivers"
        # Date window from the prompt rides through to the tool args.
        assert compiled.tool_args["date_from"] == "2031-05-01"
        assert compiled.tool_args["date_to"] == "2031-05-31"
        assert compiled.tool_args["metric"] == "revenue"
        # Default comparison is previous_period — caller can override
        # via a richer plan in a future slice.
        assert compiled.tool_args["compare_to"] == "previous_period"

    def test_explain_expenses_routes_to_explain_finance_drivers(self):
        plan = plan_finance_query(
            "Explain expenses for Q2 2031",
            runtime_today=_RUNTIME_TODAY,
        )
        compiled = compile_finance_plan(plan)

        assert plan.intent == FinanceIntent.FINANCE_DRIVER_EXPLANATION
        assert compiled.tool_name == "explain_finance_drivers"
        assert compiled.tool_args["metric"] == "expenses"

    def test_what_drove_routes_to_explain_finance_drivers(self):
        plan = plan_finance_query(
            "What drove the increase in revenue in June 2031?",
            runtime_today=_RUNTIME_TODAY,
        )
        compiled = compile_finance_plan(plan)

        assert plan.intent == FinanceIntent.FINANCE_DRIVER_EXPLANATION
        assert compiled.tool_args["metric"] == "revenue"

    def test_gross_profit_driver_question_routes_to_gross_profit_metric(self):
        plan = plan_finance_query(
            "Why did gross profit move in May 2031?",
            runtime_today=_RUNTIME_TODAY,
        )
        compiled = compile_finance_plan(plan)

        assert plan.intent == FinanceIntent.FINANCE_DRIVER_EXPLANATION
        # Compiler maps GROSS_PROFIT planner metric to the driver tool's
        # "gross_profit" metric label.
        assert compiled.tool_args["metric"] == "gross_profit"

    def test_driver_intent_without_window_asks_for_clarification(self):
        plan = plan_finance_query(
            "Why did revenue go down?",  # no period
            runtime_today=_RUNTIME_TODAY,
        )

        assert plan.intent == FinanceIntent.CLARIFICATION_REQUIRED
        assert plan.requires_confirmation is True
        assert plan.confirmation_prompt
        prompt_lower = plan.confirmation_prompt.lower()
        assert "date range" in prompt_lower or "window" in prompt_lower

    def test_driver_intent_without_metric_asks_for_clarification(self):
        plan = plan_finance_query(
            "What drove the change in May 2031?",  # no metric phrase
            runtime_today=_RUNTIME_TODAY,
        )

        assert plan.intent == FinanceIntent.CLARIFICATION_REQUIRED
        assert plan.requires_confirmation is True
        assert "metric" in (plan.confirmation_prompt or "").lower()

    def test_driver_intent_runs_before_revenue_total(self):
        """Audit case: 'why did revenue go up' must NOT route to
        REVENUE_TOTAL (which returns a single number with no driver
        context). The driver branch runs first."""
        plan = plan_finance_query(
            "Why did revenue go up in April 2031?",
            runtime_today=_RUNTIME_TODAY,
        )
        assert plan.intent == FinanceIntent.FINANCE_DRIVER_EXPLANATION
        assert plan.intent != FinanceIntent.REVENUE_TOTAL

    def test_driver_intent_does_not_route_when_no_why_phrase(self):
        """A plain 'revenue in April 2031' must still go to
        REVENUE_TOTAL — driver detection requires a why/driver/variance
        phrase."""
        plan = plan_finance_query(
            "Revenue in April 2031",
            runtime_today=_RUNTIME_TODAY,
        )
        assert plan.intent == FinanceIntent.REVENUE_TOTAL

    def test_driver_intent_unsupported_when_tool_missing(self):
        plan = plan_finance_query(
            "Why did revenue go down in April 2031?",
            runtime_today=_RUNTIME_TODAY,
        )
        compiled = compile_finance_plan(
            plan,
            available_tools={"query_finance_data"},  # explain_finance_drivers absent
        )
        assert compiled.is_supported is False
        assert compiled.tool_name is None
