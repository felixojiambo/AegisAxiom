"""Pydantic v2 models for engine inputs and outputs.

All dataclasses are frozen — engine inputs and outputs are immutable.

Strictness baked in at the model layer:
  - History list fields enforce length == 3 via Pydantic Field constraints.
  - Month strings enforce YYYY-MM format via regex pattern.
  - Cost vs revenue line types are split into separate Literal types so
    invalid combinations are unrepresentable.

These constraints come from Friday's call with Asad: trailing 3 months
strict, manual assumptions explicit, types unambiguous.
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Reusable annotated types
# ---------------------------------------------------------------------------

# YYYY-MM month string. Rejects "May-2026", "2026/05", "April", etc.
MonthStr = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]

# YYYY-MM-DD date string. Rejects "May 1 2026", "2026/05/01", etc.
DateStr = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")]

# Trailing 3-month history list. Exactly 3 floats, oldest first.
History3 = Annotated[list[float], Field(min_length=3, max_length=3)]

# Cash receipt timing — separate from recognition timing.
#  - same_month: cash hits the same month the assumption recognizes.
#    Default; preserves engine behavior for callers that haven't opted
#    into the new field.
#  - next_month: cash hits the month AFTER the recognition month
#    (typical AR timing for invoiced revenue with NET-30 terms).
#  - specific_date: cash hits on cash_receipt_date (YYYY-MM-DD).
CashReceiptTimingStr = Literal["same_month", "next_month", "specific_date"]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InsufficientHistoryError(Exception):
    """Raised when an auto-derived projection line lacks enough trailing history.

    The engine requires exactly 3 complete months of history for any line
    derived from rolling averages (PS revenue, FS revenue, GP/COGS ratios).
    Same for prior-year same-month MRR if the comparator month is missing.

    Caller resolves by either:
      1. Supplying a manual assumption for the affected line, or
      2. Aborting the projection.

    The engine does NOT silently degrade to 1- or 2-month averages.
    """

    def __init__(
        self,
        line: str,
        available_months: int,
        required_months: int = 3,
        *,
        metadata: dict | None = None,
    ):
        self.line = line
        self.available_months = available_months
        self.required_months = required_months
        # Optional structured payload (e.g., the MRR baseline audit
        # block) the chat tool surfaces verbatim when the engine
        # gives up on auto-derivation. Stays None for non-MRR
        # callers; data_loader attaches the MRR baseline metadata on
        # the missing-both path so the user message can quote the
        # actual lookup window and row counts instead of a generic
        # "mapping unavailable" line.
        self.metadata = metadata
        super().__init__(
            f"Insufficient history for line '{line}': "
            f"{available_months} complete month(s) available, "
            f"{required_months} required. "
            f"Supply a manual assumption for '{line}' or abort the projection."
        )


# ---------------------------------------------------------------------------
# Line types — split by polarity
# ---------------------------------------------------------------------------

# Revenue lines: things that produce cash inflow.
RevenueLineType = Literal[
    "PS_REVENUE", "FS_REVENUE", "MRR", "WHOLESALE", "OTHER_REVENUE",
]

# Cost lines: things that produce cash outflow.
CostLineType = Literal[
    "PS_COGS", "FS_COGS",
    "PAYROLL", "RENT", "ADMIN_EXPENSES", "OTHER_COSTS",
]

# Combined, used where either side is acceptable (e.g., LINE_TIMING_DEFAULTS,
# weekly_timing_overrides).
LineType = RevenueLineType | CostLineType


# ---------------------------------------------------------------------------
# Weekly distribution
# ---------------------------------------------------------------------------

class WeeklyDistribution(BaseModel):
    """How a monthly amount spreads across 4 weeks.

    Weights do NOT need to sum to 1.0. The engine does not normalize.
    For typical use, weights should sum to 1.0.
    """
    model_config = ConfigDict(frozen=True)
    week1_pct: float = 0.25
    week2_pct: float = 0.25
    week3_pct: float = 0.25
    week4_pct: float = 0.25


# Per-line default timing. Editable per-projection via
# ProjectionInputs.weekly_timing_overrides. None of these are truth claims
# about how SimpliMeta actually pays/collects — they are starting points only.
#
# - PS/FS revenue, COGS, ADMIN, OTHER_*: even quarters.
# - MRR: week 1 (Asad's call: "MRR usually lands in the first week").
# - WHOLESALE: weeks 1-2 (Asad's call: "first or second week").
# - PAYROLL: biweekly (weeks 2 + 4).
# - RENT: week 3 — PLACEHOLDER, not a truth claim. The workbook does not
#   establish a canonical rent timing. Override per-projection if needed.
LINE_TIMING_DEFAULTS: dict[str, WeeklyDistribution] = {
    "PS_REVENUE":     WeeklyDistribution(week1_pct=0.25, week2_pct=0.25, week3_pct=0.25, week4_pct=0.25),
    "FS_REVENUE":     WeeklyDistribution(week1_pct=0.25, week2_pct=0.25, week3_pct=0.25, week4_pct=0.25),
    "MRR":            WeeklyDistribution(week1_pct=1.00, week2_pct=0.00, week3_pct=0.00, week4_pct=0.00),
    "WHOLESALE":      WeeklyDistribution(week1_pct=0.50, week2_pct=0.50, week3_pct=0.00, week4_pct=0.00),
    "OTHER_REVENUE":  WeeklyDistribution(week1_pct=0.25, week2_pct=0.25, week3_pct=0.25, week4_pct=0.25),
    "PS_COGS":        WeeklyDistribution(week1_pct=0.25, week2_pct=0.25, week3_pct=0.25, week4_pct=0.25),
    "FS_COGS":        WeeklyDistribution(week1_pct=0.25, week2_pct=0.25, week3_pct=0.25, week4_pct=0.25),
    "PAYROLL":        WeeklyDistribution(week1_pct=0.00, week2_pct=0.50, week3_pct=0.00, week4_pct=0.50),
    "RENT":           WeeklyDistribution(week1_pct=0.00, week2_pct=0.00, week3_pct=1.00, week4_pct=0.00),
    "ADMIN_EXPENSES": WeeklyDistribution(week1_pct=0.25, week2_pct=0.25, week3_pct=0.25, week4_pct=0.25),
    "OTHER_COSTS":    WeeklyDistribution(week1_pct=0.25, week2_pct=0.25, week3_pct=0.25, week4_pct=0.25),
}


# ---------------------------------------------------------------------------
# Existing legacy models (kept from scaffold; will be deprecated as the
# manual assumption models take over)
# ---------------------------------------------------------------------------

class FixedRecurring(BaseModel):
    """Fixed cost that does not scale with revenue."""
    model_config = ConfigDict(frozen=True)
    name: str
    amount_per_month: float
    week_of_month: int = Field(ge=1, le=4)
    category: str


class WholesaleInvoice(BaseModel):
    """Known recurring wholesale revenue. Not predicted — taken from a list."""
    model_config = ConfigDict(frozen=True)
    customer_name: str
    amount: float
    expected_month: MonthStr
    expected_week: int = Field(ge=1, le=4, default=1)


# ---------------------------------------------------------------------------
# Override model — post-distribution weekly adjustments
# ---------------------------------------------------------------------------
#
# Overrides operate on already-distributed weekly rows (WeekRow). They are
# the post-distribution adjustment layer: take a baseline projection, apply
# ad-hoc tweaks. Different from manual assumptions, which are persistent
# baseline inputs at the monthly level.
#
# Phase ordering inside apply_overrides:
#   1. new_cost / new_revenue   (add line to specific week_number)
#   2. shift_payment            (move existing line by N weeks)
#   3. remove_payment           (zero out original-anchored amount; wins
#                                over shift via delta semantics)
#
# "modify" was previously a 5th override type. It has been removed from v1
# overrides. Global sales/cost scaling lives on ProjectionInputs as
# sales_modifier / cost_modifier, applied upstream in engine.py before
# weekly distribution.

OverrideType = Literal[
    "new_cost", "new_revenue", "shift_payment", "remove_payment",
]


# Targets are limited to user-facing aggregate categories that exist as
# fields on WeekRow. The internal "overrides_costs" bucket is intentionally
# NOT a user target — overrides should target real categories like
# OTHER_COSTS, PAYROLL, etc.
#
# NOTE: OverrideTarget uses "ADMINISTRATIVE_EXPENSES" (full word) as the
# user-facing override target name, while CostLineType uses
# "ADMIN_EXPENSES" (engine-internal monthly-bucket key). Both resolve
# to WeekRow.administrative_expenses via TARGET_FIELD_MAP. This split
# is intentional: engine internals favor brevity, user-facing names
# favor clarity. Round-trip behavior is verified by
# test_admin_expenses_naming_round_trip in test_nova_engine.py.
OverrideTarget = Literal[
    # Revenue
    "PS_REVENUE", "FS_REVENUE", "MRR", "WHOLESALE", "OTHER_REVENUE",
    # Cost
    "PS_COGS", "FS_COGS", "PAYROLL", "ADMINISTRATIVE_EXPENSES", "OTHER_COSTS",
]


class Override(BaseModel):
    """Ad-hoc adjustment to a baseline weekly projection.

    Field requirements by type:
      new_cost / new_revenue:
        REQUIRED: week_number (1-based), amount
        FORBIDDEN: source_week, shift_weeks
      shift_payment:
        REQUIRED: source_week (1-based), shift_weeks (any int including
                  negative; positive shifts forward, negative backward)
        FORBIDDEN: week_number, amount
      remove_payment:
        REQUIRED: source_week (1-based)
        FORBIDDEN: week_number, amount, shift_weeks

    Validation is enforced at construction time via a model_validator.
    "Within horizon" validation (week_number / source_week <= len(weeks))
    is NOT enforced here because the model has no knowledge of horizon.
    apply_overrides does that check at application time.

    source_week semantics: anchored to the original input projection
    passed into apply_overrides, NOT to mutated intermediate state. This
    makes shifts and removes deterministic regardless of override order.
    """
    model_config = ConfigDict(frozen=True)

    type: OverrideType
    target: OverrideTarget

    # For new_cost / new_revenue
    week_number: int | None = Field(default=None, ge=1)
    amount: float | None = None

    # For shift_payment / remove_payment
    source_week: int | None = Field(default=None, ge=1)

    # Only for shift_payment
    shift_weeks: int | None = None  # any int; not bounded — apply_overrides
                                    # silently drops shifts that land outside
                                    # the horizon

    description: str = ""

    # Idempotency markers used by the chat tool's dispatch layer
    # when it re-applies dynamic projection inputs across chat
    # turns. ``generated_source`` distinguishes a tool-generated
    # entry (e.g. ``"dynamic_payroll_schedule"``) from a user-
    # created override (``None``); ``dedupe_key`` is a stable
    # fingerprint the dispatcher uses to collapse duplicate
    # generated events within a single batch AND to scrub prior
    # generated entries before re-applying. Both default to None
    # so existing user-facing callers (apply_override, the engine,
    # legacy persisted state) work unchanged.
    generated_source: str | None = None
    dedupe_key: str | None = None

    def model_post_init(self, __context):
        """Type-specific required/forbidden field validation.

        Pydantic v2 idiom: model_post_init runs after frozen model
        construction, so we can read fields and raise ValueError if the
        combination is invalid.
        """
        type_ = self.type

        if type_ in ("new_cost", "new_revenue"):
            if self.week_number is None:
                raise ValueError(
                    f"Override(type={type_!r}) requires 'week_number'"
                )
            if self.amount is None:
                raise ValueError(
                    f"Override(type={type_!r}) requires 'amount'"
                )
            if self.source_week is not None:
                raise ValueError(
                    f"Override(type={type_!r}) must not set 'source_week'"
                )
            if self.shift_weeks is not None:
                raise ValueError(
                    f"Override(type={type_!r}) must not set 'shift_weeks'"
                )
        elif type_ == "shift_payment":
            if self.source_week is None:
                raise ValueError(
                    "Override(type='shift_payment') requires 'source_week'"
                )
            if self.shift_weeks is None:
                raise ValueError(
                    "Override(type='shift_payment') requires 'shift_weeks'"
                )
            if self.week_number is not None:
                raise ValueError(
                    "Override(type='shift_payment') must not set 'week_number'"
                )
            if self.amount is not None:
                raise ValueError(
                    "Override(type='shift_payment') must not set 'amount'"
                )
        elif type_ == "remove_payment":
            if self.source_week is None:
                raise ValueError(
                    "Override(type='remove_payment') requires 'source_week'"
                )
            if self.week_number is not None:
                raise ValueError(
                    "Override(type='remove_payment') must not set 'week_number'"
                )
            if self.amount is not None:
                raise ValueError(
                    "Override(type='remove_payment') must not set 'amount'"
                )
            if self.shift_weeks is not None:
                raise ValueError(
                    "Override(type='remove_payment') must not set 'shift_weeks'"
                )


# ---------------------------------------------------------------------------
# Manual assumption models
# ---------------------------------------------------------------------------

class ManualCostAssumption(BaseModel):
    """Editable baseline cost the engine treats as deterministic input.

    Use for: payroll, FTE/contractor costs, planned hires, recurring
    operating expenses, grouped expense bucket totals, loan payments.

    Not for: ad-hoc one-shot scenarios (use Override).
    Not for: COGS lines computed from revenue × derived ratio (the engine
    does that automatically; manual COGS lines are an override path for
    edge cases like a new contractor commitment).

    line_type is restricted to CostLineType — assigning a revenue line
    type ("MRR", "PS_REVENUE") will fail at construction.

    starts_month and ends_month are inclusive YYYY-MM bounds. Both None
    means the assumption applies to every forecast month in the horizon.

    cash_receipt_timing controls when cash leaves the bank account
    relative to the recognition month. Defaults to ``same_month`` so
    callers that haven't opted into the new field see identical engine
    behavior. ``next_month`` shifts the cash impact one month later
    (typical for invoiced costs paid on NET-30 terms);
    ``specific_date`` requires ``cash_receipt_date``.
    """
    model_config = ConfigDict(frozen=True)
    name: str
    amount_per_month: float
    line_type: CostLineType
    starts_month: MonthStr | None = None
    ends_month: MonthStr | None = None
    weekly_dist: WeeklyDistribution | None = None
    cash_receipt_timing: CashReceiptTimingStr = "same_month"
    cash_receipt_date: DateStr | None = None
    # Idempotency markers for dynamic generated assumptions
    # (account-prefix averages). User-created assumptions leave
    # these None; the chat tool tags generated ones so reruns
    # don't multiply costs.
    generated_source: str | None = None
    dedupe_key: str | None = None

    @model_validator(mode="after")
    def _check_specific_date_pair(self) -> "ManualCostAssumption":
        if self.cash_receipt_timing == "specific_date" and self.cash_receipt_date is None:
            raise ValueError(
                "cash_receipt_timing='specific_date' requires cash_receipt_date "
                "to be a YYYY-MM-DD string."
            )
        if self.cash_receipt_timing != "specific_date" and self.cash_receipt_date is not None:
            raise ValueError(
                "cash_receipt_date may only be set when "
                "cash_receipt_timing='specific_date'."
            )
        return self


class RecurringRevenueAssumption(BaseModel):
    """Editable recurring revenue stream that bypasses trailing-history derivation.

    Use for:
      - New referral MRR (Asad's call: "manual input")
      - Wholesale recurring not yet in QBO
      - Any new recurring stream
      - Other recurring revenue not modeled by the rolling-3-month logic

    line_type is restricted to RevenueLineType — assigning a cost line
    type ("PAYROLL", "RENT") will fail at construction.

    Use this when prior-year same-month MRR data isn't available — supply
    the recurring assumption manually instead of raising
    InsufficientHistoryError.

    cash_receipt_timing controls when cash arrives in the bank account
    relative to the recognition month. Defaults to ``same_month`` so
    callers that haven't opted into the new field see identical engine
    behavior. ``next_month`` shifts the cash impact one month later
    (typical for AR-backed revenue billed on NET-30 terms);
    ``specific_date`` requires ``cash_receipt_date``.
    """
    model_config = ConfigDict(frozen=True)
    name: str
    amount_per_month: float
    line_type: RevenueLineType
    starts_month: MonthStr | None = None
    ends_month: MonthStr | None = None
    weekly_dist: WeeklyDistribution | None = None
    cash_receipt_timing: CashReceiptTimingStr = "same_month"
    cash_receipt_date: DateStr | None = None

    @model_validator(mode="after")
    def _check_specific_date_pair(self) -> "RecurringRevenueAssumption":
        if self.cash_receipt_timing == "specific_date" and self.cash_receipt_date is None:
            raise ValueError(
                "cash_receipt_timing='specific_date' requires cash_receipt_date "
                "to be a YYYY-MM-DD string."
            )
        if self.cash_receipt_timing != "specific_date" and self.cash_receipt_date is not None:
            raise ValueError(
                "cash_receipt_date may only be set when "
                "cash_receipt_timing='specific_date'."
            )
        return self


# ---------------------------------------------------------------------------
# Inputs and outputs
# ---------------------------------------------------------------------------

class ProjectionInputs(BaseModel):
    """Everything project_cash_flow() needs.

    Strictness baked in:
      - All four trailing-history fields enforce length == 3 at construction.
      - projection_start_month and all *_month fields enforce YYYY-MM format.
      - mrr_last_year_same_months keys enforce YYYY-MM format.

    Bad inputs fail here, not deeper in the engine.
    """
    model_config = ConfigDict(frozen=True)

    starting_cash: float
    projection_start_month: MonthStr
    horizon_weeks: int = Field(default=13, ge=1, le=52)  # 13 weeks ~= 90 days
    basis: Literal["accrual", "cash"] = "accrual"

    # Last 3 complete months, oldest first. STRICT length 3 at model level.
    ps_revenue_last_3_months: History3
    fs_revenue_last_3_months: History3
    ps_gross_profit_last_3_months: History3
    fs_gross_profit_last_3_months: History3

    # OTHER_REVENUE = revenue accounts that don't fall into PS/FS/MRR/
    # WHOLESALE — e.g., legacy QBO accounts whose only signal is the
    # "INCOME" substring heuristic ("Other Income:SimpliComply"), or
    # 5xxxx codes outside the enumerated families. Defaulted to zeros
    # so callers built before this field existed (and tests that
    # construct ProjectionInputs by hand) keep working — the engine
    # then projects 0.0 contribution for OTHER_REVENUE, matching prior
    # behavior. data_loader.build_projection_inputs populates it for
    # real from load_revenue_history.
    other_revenue_last_3_months: History3 = Field(
        default_factory=lambda: [0.0, 0.0, 0.0]
    )

    # MRR by forecast month from last-year-same-month
    # Note: dict keys are not pattern-validated by Pydantic out of the box.
    # data_loader is responsible for producing well-formed YYYY-MM keys.
    mrr_last_year_same_months: dict[str, float] = Field(default_factory=dict)

    # Audit block from data_loader.load_mrr_baseline_with_fallback:
    # basis, account filter, prior-year window + row counts, fallback
    # window + months used + run-rate, per-forecast-month method, and
    # any warnings. Surfaced verbatim by the run_projection tool so
    # Nova can quote which source fed each MRR forecast month.
    # Optional so callers that construct ProjectionInputs by hand
    # (tests, legacy paths) keep working without filling it in.
    mrr_baseline_metadata: dict | None = None

    # Audit block describing the SALES baseline used for the
    # projection: method (last 3 complete months), basis,
    # reference_month, latest_tx_date, months_used, revenue
    # categories, per-month totals, average_monthly_sales. Nova
    # quotes this verbatim when explaining "last 3 month avg
    # sales" so it doesn't have to recompute or paraphrase. Stays
    # None for hand-constructed inputs (tests, legacy paths) to
    # preserve backward compatibility.
    sales_baseline_metadata: dict | None = None

    # Known recurring revenue and fixed costs (legacy compat path)
    wholesale_invoices: list[WholesaleInvoice] = Field(default_factory=list)
    fixed_costs: list[FixedRecurring] = Field(default_factory=list)

    # Explicit editable manual assumptions (preferred over legacy fields above)
    manual_cost_assumptions: list[ManualCostAssumption] = Field(default_factory=list)
    manual_recurring_revenue: list[RecurringRevenueAssumption] = Field(default_factory=list)

    # Scenario knobs (workbook cells Q2 / T2)
    sales_modifier: float = 1.0
    cost_modifier: float = 1.0

    # Per-line weekly distribution overrides. Engine consumes
    # LINE_TIMING_DEFAULTS[line_type] unless an entry exists here, in
    # which case the override is used.
    weekly_timing_overrides: dict[str, WeeklyDistribution] = Field(default_factory=dict)

    overrides: list[Override] = Field(default_factory=list)
    account_groupings: list[dict] = Field(default_factory=list)


class WeekRow(BaseModel):
    """One week of projected cash flow."""
    model_config = ConfigDict(frozen=True)
    week_number: int
    week_starting: date

    ps_revenue: float = 0.0
    fs_revenue: float = 0.0
    mrr_revenue: float = 0.0
    wholesale_revenue: float = 0.0
    other_revenue: float = 0.0
    total_revenue: float = 0.0

    ps_cogs: float = 0.0
    fs_cogs: float = 0.0
    payroll: float = 0.0
    administrative_expenses: float = 0.0
    other_costs: float = 0.0
    overrides_costs: float = 0.0
    total_costs: float = 0.0

    net_cash_flow: float = 0.0
    ending_cash: float = 0.0


class FreshnessWarning(BaseModel):
    """Surfaced when the engine is operating on incomplete data."""
    model_config = ConfigDict(frozen=True)
    kind: Literal["incomplete_month", "stale_sync", "missing_basis"]
    message: str
    affected_month: MonthStr | None = None


class ProjectionOutput(BaseModel):
    """Full output of project_cash_flow."""
    model_config = ConfigDict(frozen=True)
    weeks: list[WeekRow]
    starting_cash: float
    ending_cash: float
    minimum_cash: float
    minimum_cash_week: int
    total_revenue: float
    total_costs: float
    net_change: float

    inputs_hash: str
    computed_at: datetime
    formulas_version: str = "0.1.0"
    warnings: list[FreshnessWarning] = Field(default_factory=list)
