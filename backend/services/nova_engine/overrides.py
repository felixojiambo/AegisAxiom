"""Apply ad-hoc overrides to a baseline weekly projection.

This module is the post-distribution override layer. Inputs:
  - weeks: list of WeekRow objects (frozen, baseline projection)
  - overrides: list of Override objects (frozen, validated at construction)

Output:
  - new list of WeekRow objects (also frozen) with overrides applied
    and all derived fields recomputed.

Phase ordering (deterministic):
  1. new_cost / new_revenue   add to week_number
  2. shift_payment            move original-week amount forward/backward
  3. remove_payment           subtract original-anchored amount

Key semantics:
  - source_week and week_number are 1-based.
  - source_week is anchored to the ORIGINAL input projection. Shifts and
    removes do not see new_cost/new_revenue rows created earlier in the
    same call.
  - Delta-semantics removes: remove_payment subtracts the original
    anchored amount, preserving independent contributions. If a shift
    moved the original amount, the remove subtracts it from the
    destination week (where the shift left it).
  - Conflicting shifts (same target + source_week, different shift_weeks)
    raise ValueError.
  - Duplicate removes (same target + source_week) are idempotent.
  - Shifts outside the horizon drop silently — that's the cleanest
    cashflow interpretation.
  - After all edits, per-row totals (total_revenue, total_costs,
    net_cash_flow) are recomputed, then ending_cash rolls forward from
    week 1 through the horizon.
"""
from __future__ import annotations
from collections import defaultdict
from typing import get_args

from .models import Override, OverrideTarget, WeekRow


# ---------------------------------------------------------------------------
# Target -> WeekRow field mapping
# ---------------------------------------------------------------------------
# Single source of truth for translating canonical OverrideTarget literals
# into WeekRow attribute names. If a WeekRow field is renamed, this is the
# only place that needs to change.
TARGET_FIELD_MAP: dict[str, str] = {
    # Revenue
    "PS_REVENUE":              "ps_revenue",
    "FS_REVENUE":              "fs_revenue",
    "MRR":                     "mrr_revenue",
    "WHOLESALE":               "wholesale_revenue",
    "OTHER_REVENUE":           "other_revenue",
    # Cost
    "PS_COGS":                 "ps_cogs",
    "FS_COGS":                 "fs_cogs",
    "PAYROLL":                 "payroll",
    "ADMINISTRATIVE_EXPENSES": "administrative_expenses",
    "OTHER_COSTS":             "other_costs",
}


REVENUE_FIELDS = (
    "ps_revenue", "fs_revenue", "mrr_revenue",
    "wholesale_revenue", "other_revenue",
)


COST_FIELDS = (
    "ps_cogs", "fs_cogs", "payroll",
    "administrative_expenses", "other_costs", "overrides_costs",
)


def _validate_target_field_map_complete() -> None:
    """Sanity: every OverrideTarget literal has an entry in TARGET_FIELD_MAP.

    Runs at module load. If a new target is added to the Literal but not
    to the map (or vice versa), this raises before any code runs.
    """
    target_literals = set(get_args(OverrideTarget))
    map_keys = set(TARGET_FIELD_MAP.keys())

    missing_in_map = target_literals - map_keys
    extra_in_map = map_keys - target_literals

    if missing_in_map:
        raise ValueError(
            f"OverrideTarget literals missing from TARGET_FIELD_MAP: "
            f"{sorted(missing_in_map)}"
        )
    if extra_in_map:
        raise ValueError(
            f"TARGET_FIELD_MAP has entries not in OverrideTarget: "
            f"{sorted(extra_in_map)}"
        )


# Run at module load. Fails fast if the map and enum drift apart.
_validate_target_field_map_complete()


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def _validate_no_conflicting_shifts(overrides: list[Override]) -> None:
    """Raise if two shift_payment overrides target the same (target,
    source_week) with different shift_weeks values.

    Idempotent shifts (same target+source_week+shift_weeks repeated) are
    permitted — they collapse to one shift.
    """
    seen_shifts: dict[tuple[str, int], int] = {}
    for ov in overrides:
        if ov.type != "shift_payment":
            continue
        key = (ov.target, ov.source_week)
        if key in seen_shifts:
            existing_shift = seen_shifts[key]
            if existing_shift != ov.shift_weeks:
                raise ValueError(
                    f"Conflicting shift_payment overrides for "
                    f"target={ov.target!r}, source_week={ov.source_week}: "
                    f"shift_weeks={existing_shift} vs {ov.shift_weeks}. "
                    f"Resolve in the chat extraction layer; "
                    f"engine refuses to guess."
                )
        else:
            # Type narrowing for mypy: shift_payment overrides require shift_weeks
            # (enforced by Override.model_post_init).
            assert ov.shift_weeks is not None
            seen_shifts[key] = ov.shift_weeks


# ---------------------------------------------------------------------------
# Within-horizon validation (week_number / source_week)
# ---------------------------------------------------------------------------

def _validate_within_horizon(
    overrides: list[Override], horizon: int
) -> None:
    """Raise if any week_number or source_week exceeds horizon.

    Negative or zero values are already rejected at the Pydantic layer
    via Field(ge=1). This catches the upper bound, which the model
    cannot know.
    """
    for ov in overrides:
        if ov.week_number is not None and ov.week_number > horizon:
            raise ValueError(
                f"Override(type={ov.type!r}, target={ov.target!r}) "
                f"has week_number={ov.week_number}, "
                f"but horizon is {horizon} weeks."
            )
        if ov.source_week is not None and ov.source_week > horizon:
            raise ValueError(
                f"Override(type={ov.type!r}, target={ov.target!r}) "
                f"has source_week={ov.source_week}, "
                f"but horizon is {horizon} weeks."
            )


# ---------------------------------------------------------------------------
# Recomputation helpers
# ---------------------------------------------------------------------------

def _recompute_totals(row: WeekRow) -> WeekRow:
    """Return a new WeekRow with total_revenue, total_costs, and
    net_cash_flow recomputed from the line fields. ending_cash is left
    as-is (rolled forward separately).
    """
    total_revenue = sum(getattr(row, f) for f in REVENUE_FIELDS)
    total_costs = sum(getattr(row, f) for f in COST_FIELDS)
    net_cash_flow = total_revenue - total_costs

    return row.model_copy(update={
        "total_revenue": total_revenue,
        "total_costs": total_costs,
        "net_cash_flow": net_cash_flow,
    })


def _roll_forward_ending_cash(
    weeks: list[WeekRow], starting_cash: float
) -> list[WeekRow]:
    """Set ending_cash for each week as a running sum from starting_cash.

    Returns a new list. ending_cash[i] = ending_cash[i-1] + net_cash_flow[i],
    with ending_cash[0] = starting_cash + net_cash_flow[0].
    """
    result: list[WeekRow] = []
    running = starting_cash
    for row in weeks:
        running = running + row.net_cash_flow
        result.append(row.model_copy(update={"ending_cash": running}))
    return result


# ---------------------------------------------------------------------------
# Phase application helpers
# ---------------------------------------------------------------------------

def _add_amount_to_week(
    weeks: list[WeekRow], week_index: int, field_name: str, delta: float
) -> list[WeekRow]:
    """Return new list with `delta` added to weeks[week_index].field_name.

    week_index is 0-based here (we've already converted from 1-based
    week_number at the call site).
    """
    if week_index < 0 or week_index >= len(weeks):
        return weeks  # silent no-op — shifts outside horizon drop
    target = weeks[week_index]
    new_value = getattr(target, field_name) + delta
    new_row = target.model_copy(update={field_name: new_value})
    return weeks[:week_index] + [new_row] + weeks[week_index + 1:]


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def apply_overrides(
    weeks: list[WeekRow],
    overrides: list[Override],
    starting_cash: float = 0.0,
) -> list[WeekRow]:
    """Apply override list to a baseline weekly projection.

    Args:
        weeks: original weekly projection (frozen WeekRows). Not mutated.
        overrides: list of overrides to apply, in any order. The function
            sorts them into deterministic phases internally.
        starting_cash: starting cash for ending_cash roll-forward. Defaults
            to 0; engine.py will pass the real value.

    Returns:
        New list of WeekRow objects with overrides applied and all
        derived fields (total_revenue, total_costs, net_cash_flow,
        ending_cash) recomputed.

    Raises:
        ValueError: on conflicting shifts, on out-of-horizon week refs.

    Pure function. No I/O. Deterministic.
    """
    # --- Pre-validation -----------------------------------------------------
    horizon = len(weeks)
    _validate_within_horizon(overrides, horizon)
    _validate_no_conflicting_shifts(overrides)

    # --- Snapshot the original input ---------------------------------------
    # Used as the read-only reference for shift/remove anchoring. Even
    # though weeks is frozen, we keep an explicit alias for clarity.
    original = list(weeks)

    # --- Initialize working copy ------------------------------------------
    # Frozen WeekRows are immutable, so "working" is a list of WeekRow
    # objects we replace as we go (not mutate in place).
    working: list[WeekRow] = list(weeks)

    # --- Phase 1: new_cost / new_revenue ----------------------------------
    for ov in overrides:
        if ov.type not in ("new_cost", "new_revenue"):
            continue
        field_name = TARGET_FIELD_MAP[ov.target]
        # Type narrowing for mypy: new_cost/new_revenue overrides require
        # week_number and amount (enforced by Override.model_post_init).
        assert ov.week_number is not None
        assert ov.amount is not None
        # week_number is 1-based; convert to 0-based index
        idx = ov.week_number - 1
        working = _add_amount_to_week(working, idx, field_name, ov.amount)

    # --- Phase 2: shift_payment -------------------------------------------
    # Track shifts that have been applied so phase 3 knows where the
    # original amount landed (for delta-semantics remove).
    # Deduplicate idempotent shifts (same target+source_week+shift_weeks).
    shift_destinations: dict[tuple[str, int], int] = {}  # (target, source_week_1based) -> destination_week_1based
    seen_shifts: set[tuple[str, int, int]] = set()

    for ov in overrides:
        if ov.type != "shift_payment":
            continue
        signature = (ov.target, ov.source_week, ov.shift_weeks)
        if signature in seen_shifts:
            continue
        seen_shifts.add(signature)

        field_name = TARGET_FIELD_MAP[ov.target]
        src_idx = ov.source_week - 1  # 0-based
        dst_week_1based = ov.source_week + ov.shift_weeks
        dst_idx = dst_week_1based - 1

        # Read the ORIGINAL anchored amount (not the working state).
        # This is the key invariant: shifts move only what was in the
        # original baseline, never new_cost/new_revenue rows added
        # earlier in this call.
        original_amount = getattr(original[src_idx], field_name)

        if original_amount == 0:
            # Nothing to move. Still track destination as the source for
            # downstream remove resolution (no-op in practice).
            shift_destinations[(ov.target, ov.source_week)] = ov.source_week
            continue

        # Subtract from source (in working state). Note: the ORIGINAL
        # amount is what gets removed, so anything contributed by phase 1
        # at this same week stays.
        working = _add_amount_to_week(working, src_idx, field_name, -original_amount)

        # Add to destination (silently drops if out of horizon)
        if 0 <= dst_idx < horizon:
            working = _add_amount_to_week(working, dst_idx, field_name, original_amount)
            shift_destinations[(ov.target, ov.source_week)] = dst_week_1based
        else:
            # Shift left or right past horizon: original amount is gone,
            # nothing added. The contribution drops out of the forecast.
            shift_destinations[(ov.target, ov.source_week)] = -1  # sentinel: dropped

    # --- Phase 3: remove_payment ------------------------------------------
    # Idempotent: same (target, source_week) processed once.
    seen_removes: set[tuple[str, int]] = set()

    for ov in overrides:
        if ov.type != "remove_payment":
            continue
        key = (ov.target, ov.source_week)
        if key in seen_removes:
            continue
        seen_removes.add(key)

        field_name = TARGET_FIELD_MAP[ov.target]
        src_idx = ov.source_week - 1

        # Find where (if anywhere) a prior shift moved this contribution.
        if key in shift_destinations:
            dst_week_1based = shift_destinations[key]
            if dst_week_1based == -1:
                # Shifted out of horizon already. Nothing to subtract.
                continue
            dst_idx = dst_week_1based - 1
        else:
            # No shift was applied; remove from the source week directly.
            dst_idx = src_idx

        # Subtract the ORIGINAL anchored amount (delta semantics:
        # preserves any independent contributions from phase 1).
        original_amount = getattr(original[src_idx], field_name)
        if original_amount == 0:
            continue  # nothing to remove

        working = _add_amount_to_week(working, dst_idx, field_name, -original_amount)

    # --- Recompute per-row totals -----------------------------------------
    working = [_recompute_totals(row) for row in working]

    # --- Roll forward ending_cash ----------------------------------------
    working = _roll_forward_ending_cash(working, starting_cash)

    return working
