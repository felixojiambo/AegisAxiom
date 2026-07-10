"""Main engine entry point.

project_cash_flow(inputs) -> ProjectionOutput

Pure function (modulo datetime.now() for the computed_at timestamp).
Same inputs always produce same output (and same inputs_hash).

SCOPE — important context for future readers:

This is the v1 trailing-3-month FORECAST COMPOSER. It is NOT yet the full
"current-month actuals + known AR/AP scheduling + next-two-months
estimates" scheduler that Asad described in the design call.

What this engine does:
  - Project monthly PS/FS revenue from trailing 3-month averages
  - Derive PS/FS COGS from rolling gross profit ratios
  - Compose MRR additively: prior-year same-month base + manual additions
  - Apply manual cost and recurring revenue assumptions
  - Distribute monthly figures to 4 weekly buckets via per-line timing
  - Run overrides (new_cost, new_revenue, shift_payment, remove_payment)
  - Produce a deterministic ProjectionOutput with content hash

What this engine does NOT yet do:
  - Read current-month actuals YTD and schedule remaining weeks
  - Honor known AR/AP timing for the next two months
  - Differentiate "month 1 = actuals + scheduled" vs "month 3+ = pure forecast"

Adding those behaviors requires two new ProjectionInputs fields
(current_month_actuals, known_ar_ap_commits) plus two new branches in
this engine. v1.1 work, not v1.

Composition algorithm (7 steps):
  1. Mean last 3 months → scalar PS/FS revenue base
  2. Cogs ratio from rolling 3-month GP → scalar PS/FS COGS multiplier
  3. Per forecast month: project monthly amounts per line type, then
     apply ManualCostAssumption / RecurringRevenueAssumption deltas
  4. Distribute each line type to 4 weeks via LINE_TIMING_DEFAULTS or
     weekly_timing_overrides
  5. Build initial WeekRow list with line totals only (no row totals)
  6. Run apply_overrides → recomputes totals and rolls ending_cash
  7. Derive summary fields and build ProjectionOutput
"""
from __future__ import annotations
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from .formulas import (
    cogs_ratio,
    distribute_weekly,
    mean_last_3_months,
)
from .models import (
    InsufficientHistoryError,
    LINE_TIMING_DEFAULTS,
    ManualCostAssumption,
    ProjectionInputs,
    ProjectionOutput,
    RecurringRevenueAssumption,
    WeekRow,
    WeeklyDistribution,
)
from .overrides import apply_overrides


# ---------------------------------------------------------------------------
# Active-assumption helper
# ---------------------------------------------------------------------------

def _is_active(
    assumption: ManualCostAssumption | RecurringRevenueAssumption,
    month: str,
) -> bool:
    """True if the assumption applies to this month for RECOGNITION purposes.

    starts_month and ends_month are inclusive YYYY-MM bounds. Either
    being None means open-ended on that side. Both None means "applies
    to every month".

    NOTE: The engine projects cash flow, so for cash-impact bucketing
    the caller should additionally translate the recognition month
    through ``_cash_month_for_recognition`` to honor
    ``cash_receipt_timing``. ``_is_active`` itself reflects only the
    recognition window.
    """
    if assumption.starts_month is not None and month < assumption.starts_month:
        return False
    if assumption.ends_month is not None and month > assumption.ends_month:
        return False
    return True


def _next_month(month: str) -> str:
    """YYYY-MM successor. Pure; no calendar I/O."""
    year_str, mo_str = month.split("-")
    year, mo = int(year_str), int(mo_str)
    mo += 1
    if mo > 12:
        mo = 1
        year += 1
    return f"{year}-{mo:02d}"


def _cash_month_for_recognition(
    assumption: ManualCostAssumption | RecurringRevenueAssumption,
    recognition_month: str,
) -> str:
    """Map the recognition month to the cash-impact month.

    same_month (default): recognition_month — preserves prior engine
        behavior for callers that haven't set cash_receipt_timing.
    next_month: recognition_month + 1, wrapping calendar years
        correctly.
    specific_date: NOT supported by the v1 engine — tool callers must
        not reach this path. Raised here as a defensive belt; the
        ``add_manual_assumption`` dispatcher rejects this combination
        with a user-facing error before the assumption is constructed.
    """
    timing = getattr(assumption, "cash_receipt_timing", "same_month")
    if timing == "same_month":
        return recognition_month
    if timing == "next_month":
        return _next_month(recognition_month)
    raise ValueError(
        "Engine does not yet implement cash_receipt_timing="
        f"{timing!r}. Only 'same_month' and 'next_month' are supported "
        "in the projection. Reject 'specific_date' at the tool layer."
    )


def _distribution_for_line(
    line_type: str,
    inputs: ProjectionInputs,
    assumption_dist: WeeklyDistribution | None = None,
) -> WeeklyDistribution:
    """Resolve timing for a projected line.

    Per-assumption timing wins when supplied. Otherwise the projection-level
    line override wins, falling back to the engine's line defaults.
    """
    return (
        assumption_dist
        or inputs.weekly_timing_overrides.get(line_type)
        or LINE_TIMING_DEFAULTS[line_type]
    )


# ---------------------------------------------------------------------------
# MRR resolution (additive rule, strict on missing-both)
# ---------------------------------------------------------------------------

def _resolve_mrr(month: str, inputs: ProjectionInputs) -> float:
    """Compute MRR for a forecast month using the additive rule.

    base = mrr_last_year_same_months.get(month)  # may be None
    manual = sum of active RecurringRevenueAssumption(line_type='MRR')
             amounts for this month

    Strict: if there is no historical base AND no active manual MRR
    assumption was supplied, raise. A zero-amount manual MRR assumption
    counts as an explicit acknowledgment ("MRR is intentionally zero
    this month"); presence beats amount for the strict check.
    """
    base = _resolve_mrr_base(month, inputs)
    manual_total = sum(
        a.amount_per_month for a in _active_manual_mrr_assumptions(month, inputs)
    )
    return base + manual_total


def _active_manual_mrr_assumptions(
    month: str,
    inputs: ProjectionInputs,
) -> list[RecurringRevenueAssumption]:
    return [
        a
        for a in inputs.manual_recurring_revenue
        if a.line_type == "MRR" and _is_active(a, month)
    ]


def _resolve_mrr_base(month: str, inputs: ProjectionInputs) -> float:
    """Return historical MRR base, enforcing manual MRR when base is absent."""
    base = inputs.mrr_last_year_same_months.get(month)
    active_manual_mrr = _active_manual_mrr_assumptions(month, inputs)

    if base is None and not active_manual_mrr:
        # Attach the MRR baseline audit block so the chat tool can
        # quote the actual lookup window and row counts (basis,
        # account filter, prior-year results, fallback search
        # outcome) instead of a generic "mapping unavailable" line.
        # ``metadata`` stays None when the inputs were constructed
        # without the loader (older test paths), preserving existing
        # error shape for those callers.
        raise InsufficientHistoryError(
            line=f"mrr ({month})",
            available_months=0,
            required_months=1,
            metadata=inputs.mrr_baseline_metadata,
        )

    return base or 0.0


# ---------------------------------------------------------------------------
# Forecast month list helper (mirrors data_loader._next_n_months for
# engine independence — engine should not depend on data_loader)
# ---------------------------------------------------------------------------

def _next_n_months(start_month: str, n: int) -> list[str]:
    """Given 'YYYY-MM' and n, return n consecutive months starting AT start."""
    year_str, mo_str = start_month.split("-")
    year = int(year_str)
    mo = int(mo_str)
    result: list[str] = []
    for _ in range(n):
        result.append(f"{year}-{mo:02d}")
        mo += 1
        if mo > 12:
            mo = 1
            year += 1
    return result


# ---------------------------------------------------------------------------
# Week starting date helper
# ---------------------------------------------------------------------------

def _projection_start_date(projection_start_month: str) -> date:
    """First day of projection_start_month."""
    year_str, mo_str = projection_start_month.split("-")
    return date(int(year_str), int(mo_str), 1)


def _week_starting_for(projection_start_month: str, week_index_zero_based: int) -> date:
    """Approximate week_starting for week index w.

    Fixed 4-week period model: each forecast month is exactly 4 weeks
    starting from the first day of projection_start_month. This is a
    cleaner invariant than calendar-week math and matches how the rest
    of the engine treats time.
    """
    start = _projection_start_date(projection_start_month)
    return start + timedelta(weeks=week_index_zero_based)


# ---------------------------------------------------------------------------
# Inputs hash (canonical JSON + SHA-256)
# ---------------------------------------------------------------------------

def _compute_inputs_hash(inputs: ProjectionInputs) -> str:
    """Deterministic content hash of ProjectionInputs.

    Canonicalization: sort_keys=True (no insertion-order dependence),
    separators stripped of whitespace. Same logical input always
    produces same hash, across machines and Python versions.
    """
    payload = json.dumps(
        inputs.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

# Line types the engine projects. Maps to LINE_TIMING_DEFAULTS keys and
# determines what gets distributed to weeks.
_LINE_TYPES_REVENUE = ("PS_REVENUE", "FS_REVENUE", "MRR", "WHOLESALE", "OTHER_REVENUE")
_LINE_TYPES_COST = ("PS_COGS", "FS_COGS", "PAYROLL", "RENT", "ADMIN_EXPENSES", "OTHER_COSTS")
_ALL_LINE_TYPES = _LINE_TYPES_REVENUE + _LINE_TYPES_COST


# Map LINE_TIMING_DEFAULTS / monthly-bucket keys to WeekRow attribute names.
# Most are 1:1 lowercased, but ADMIN_EXPENSES → administrative_expenses,
# MRR → mrr_revenue, WHOLESALE → wholesale_revenue, OTHER_REVENUE → other_revenue.
# WeekRow has no rent field; RENT reports under administrative expenses in v1.
_LINE_TYPE_TO_WEEKROW_FIELD = {
    "PS_REVENUE":     "ps_revenue",
    "FS_REVENUE":     "fs_revenue",
    "MRR":            "mrr_revenue",
    "WHOLESALE":      "wholesale_revenue",
    "OTHER_REVENUE":  "other_revenue",
    "PS_COGS":        "ps_cogs",
    "FS_COGS":        "fs_cogs",
    "PAYROLL":        "payroll",
    "RENT":           "administrative_expenses",
    "ADMIN_EXPENSES": "administrative_expenses",
    "OTHER_COSTS":    "other_costs",
}


def project_cash_flow(inputs: ProjectionInputs) -> ProjectionOutput:
    """Run the v1 cash flow projection.

    Deterministic except for ProjectionOutput.computed_at (timestamp).
    Same inputs → same inputs_hash → same weeks.

    Raises:
      InsufficientHistoryError: if MRR can't be resolved for any
        forecast month (no prior-year data and no active manual MRR).
      ValueError: from formula functions if history lengths are wrong
        (Pydantic should catch these earlier, but defensive).
    """
    # --- Step 1: monthly base figures ---------------------------------
    mean_ps_rev = mean_last_3_months(inputs.ps_revenue_last_3_months)
    mean_fs_rev = mean_last_3_months(inputs.fs_revenue_last_3_months)
    # OTHER_REVENUE has no associated COGS bucket in the categorization
    # rules, so it projects as pure revenue (no derived cost line).
    mean_other_rev = mean_last_3_months(inputs.other_revenue_last_3_months)

    ps_cogs_ratio = cogs_ratio(
        inputs.ps_revenue_last_3_months,
        inputs.ps_gross_profit_last_3_months,
    )
    fs_cogs_ratio = cogs_ratio(
        inputs.fs_revenue_last_3_months,
        inputs.fs_gross_profit_last_3_months,
    )

    # --- Step 2: forecast month list ----------------------------------
    n_forecast_months = (inputs.horizon_weeks + 3) // 4  # ceiling division
    forecast_months = _next_n_months(inputs.projection_start_month, n_forecast_months)

    # --- Step 3: per-month line totals --------------------------------
    monthly: dict[str, dict[str, float]] = {}
    manual_monthly_entries: dict[str, list[tuple[str, float, WeeklyDistribution]]] = {}

    for month in forecast_months:
        ps_revenue = mean_ps_rev * inputs.sales_modifier
        fs_revenue = mean_fs_rev * inputs.sales_modifier
        other_revenue = mean_other_rev * inputs.sales_modifier

        bucket = {
            "PS_REVENUE":     ps_revenue,
            "FS_REVENUE":     fs_revenue,
            "MRR":            _resolve_mrr_base(month, inputs),
            "WHOLESALE":      0.0,
            "OTHER_REVENUE":  other_revenue,
            "PS_COGS":        ps_revenue * ps_cogs_ratio * inputs.cost_modifier,
            "FS_COGS":        fs_revenue * fs_cogs_ratio * inputs.cost_modifier,
            "PAYROLL":        0.0,
            "RENT":           0.0,
            "ADMIN_EXPENSES": 0.0,
            "OTHER_COSTS":    0.0,
        }

        # Manual entries are bucketed by their CASH month (which equals
        # the recognition month when cash_receipt_timing='same_month',
        # the default). For 'next_month' callers the cash impact is
        # bucketed one month later — see _cash_month_for_recognition.
        # We walk every assumption against the recognition month here,
        # then place its entry in the cash bucket. Cash months that
        # fall past the last forecast month silently drop — step 4
        # iterates over forecast_months, so out-of-range buckets are
        # never read.

        # Apply manual cost assumptions. They are distributed separately so
        # per-assumption weekly_dist has real effect.
        for a in inputs.manual_cost_assumptions:
            if _is_active(a, month):
                cash_month = _cash_month_for_recognition(a, month)
                manual_monthly_entries.setdefault(cash_month, []).append((
                    a.line_type,
                    a.amount_per_month * inputs.cost_modifier,
                    _distribution_for_line(a.line_type, inputs, a.weekly_dist),
                ))

        # Apply manual recurring revenue assumptions.
        # Manual MRR is intentionally included here; _resolve_mrr_base only
        # handles strictness and any historical base.
        for a in inputs.manual_recurring_revenue:
            if _is_active(a, month):
                amount = (
                    a.amount_per_month
                    if a.line_type == "MRR"
                    else a.amount_per_month * inputs.sales_modifier
                )
                cash_month = _cash_month_for_recognition(a, month)
                manual_monthly_entries.setdefault(cash_month, []).append((
                    a.line_type,
                    amount,
                    _distribution_for_line(a.line_type, inputs, a.weekly_dist),
                ))

        monthly[month] = bucket

    # --- Step 4: distribute monthly to weeks --------------------------
    accumulator: list[dict[str, float]] = [
        {lt: 0.0 for lt in _ALL_LINE_TYPES}
        for _ in range(inputs.horizon_weeks)
    ]

    for month_idx, month in enumerate(forecast_months):
        for line_type in _ALL_LINE_TYPES:
            dist = _distribution_for_line(line_type, inputs)
            weekly_amounts = distribute_weekly(monthly[month][line_type], dist)

            for week_in_month in range(4):
                absolute_idx = month_idx * 4 + week_in_month
                if absolute_idx >= inputs.horizon_weeks:
                    break
                accumulator[absolute_idx][line_type] += weekly_amounts[week_in_month]

        for line_type, amount, dist in manual_monthly_entries.get(month, []):
            weekly_amounts = distribute_weekly(amount, dist)
            for week_in_month in range(4):
                absolute_idx = month_idx * 4 + week_in_month
                if absolute_idx >= inputs.horizon_weeks:
                    break
                accumulator[absolute_idx][line_type] += weekly_amounts[week_in_month]

    # --- Step 5: initial WeekRow list ---------------------------------
    initial_weeks: list[WeekRow] = []
    for w in range(inputs.horizon_weeks):
        bucket = accumulator[w]
        # Aggregate any line types that share a WeekRow field (e.g., RENT
        # reports under administrative_expenses until WeekRow.rent exists).
        weekrow_fields: dict[str, float] = {
            "ps_revenue": 0.0,
            "fs_revenue": 0.0,
            "mrr_revenue": 0.0,
            "wholesale_revenue": 0.0,
            "other_revenue": 0.0,
            "ps_cogs": 0.0,
            "fs_cogs": 0.0,
            "payroll": 0.0,
            "administrative_expenses": 0.0,
            "other_costs": 0.0,
        }
        for line_type, amount in bucket.items():
            field = _LINE_TYPE_TO_WEEKROW_FIELD[line_type]
            if field in weekrow_fields:
                weekrow_fields[field] += amount

        row = WeekRow(
            week_number=w + 1,
            week_starting=_week_starting_for(inputs.projection_start_month, w),
            **weekrow_fields,
            # totals + ending_cash will be recomputed by apply_overrides
            total_revenue=0.0,
            total_costs=0.0,
            net_cash_flow=0.0,
            ending_cash=0.0,
        )
        initial_weeks.append(row)

    # --- Step 6: apply overrides --------------------------------------
    final_weeks = apply_overrides(
        weeks=initial_weeks,
        overrides=inputs.overrides,
        starting_cash=inputs.starting_cash,
    )

    # --- Step 7: derive summaries + build output ----------------------
    if final_weeks:
        ending_cash = final_weeks[-1].ending_cash
        min_idx = min(range(len(final_weeks)), key=lambda i: final_weeks[i].ending_cash)
        minimum_cash = final_weeks[min_idx].ending_cash
        minimum_cash_week = final_weeks[min_idx].week_number
    else:
        # Defensive: Pydantic enforces horizon_weeks >= 1, so this branch
        # is unreachable today. Kept so future relaxation of that bound
        # doesn't introduce an IndexError.
        ending_cash = inputs.starting_cash
        minimum_cash = inputs.starting_cash
        minimum_cash_week = 0

    total_revenue = sum(w.total_revenue for w in final_weeks)
    total_costs = sum(w.total_costs for w in final_weeks)
    net_change = total_revenue - total_costs

    return ProjectionOutput(
        weeks=final_weeks,
        starting_cash=inputs.starting_cash,
        ending_cash=ending_cash,
        minimum_cash=minimum_cash,
        minimum_cash_week=minimum_cash_week,
        total_revenue=total_revenue,
        total_costs=total_costs,
        net_change=net_change,
        inputs_hash=_compute_inputs_hash(inputs),
        computed_at=datetime.now(timezone.utc),
        warnings=[],  # Caller passes freshness warnings via future channel.
                      # See engine docstring re: scope.
    )
