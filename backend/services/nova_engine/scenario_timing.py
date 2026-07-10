"""Deterministic resolution of scenario assumption timing.

Nova's manual assumption tools accept timing in three shapes:
  1. Explicit ``starts_month`` (YYYY-MM) — preferred.
  2. A natural-language phrase ("starting next month", "starting this month").
  3. A ``cash_receipt_timing`` choice independent of recognition timing.

Without a deterministic resolver, the LLM was silently picking when the
recognition month and the cash receipt month should land. The audit case
was "Add $10k MRR starting next month" — depending on whether the model
chose "this month" or "next month," the projection cash totals diverged.

This module is a pure function. It does no I/O, never reads the clock
itself (the caller passes ``current_date``), and never mutates inputs.
The returned ``ScenarioTimingResult`` is the single shape every Nova
caller threads through to ``add_manual_assumption`` / ``run_projection``.

Out of scope here:
  * Translating the resolved timing into engine math — the engine reads
    ``starts_month`` and ``cash_receipt_timing`` off the assumption
    model and applies the cash shift in
    ``services/nova_engine/engine.py``.
  * Persisting the resolution — the conductor / planner stores the
    chosen values on the scenario state once the user (or the
    deterministic resolver) has settled them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


CashReceiptTiming = Literal["same_month", "next_month", "specific_date"]


@dataclass(frozen=True)
class ScenarioTimingResult:
    """Resolved timing for a single manual assumption.

    Fields:
      recognition_start_month: YYYY-MM the recognition / accrual entry
        first hits. ``None`` only when ambiguous (and then
        ``requires_confirmation`` is True).
      cash_receipt_timing: Documented cash-receipt rule. ``None`` only
        when ambiguous.
      cash_receipt_start_month: YYYY-MM the cash impact first hits. Set
        whenever cash_receipt_timing is "same_month" or "next_month";
        for "specific_date" callers, this mirrors the YYYY-MM portion
        of cash_receipt_date so downstream cash math has one source of
        truth.
      cash_receipt_date: Optional explicit YYYY-MM-DD. Set only when
        cash_receipt_timing == "specific_date".
      requires_confirmation: True when the helper cannot pick a
        recognition month or cash timing without the user's input.
        Callers must NOT execute the assumption when this is True.
      confirmation_question: A user-facing question to surface when
        requires_confirmation is True. Always paired with the True
        flag; never set when False.
      timing_summary: One-line human-readable summary the tool response
        echoes verbatim. Written so Nova can quote it without
        paraphrasing.
      warnings: Soft warnings the LLM should surface alongside the
        result (e.g. "next_month was inferred from a phrase; confirm
        with the user before running the projection if cash timing
        matters").
    """

    recognition_start_month: Optional[str]
    cash_receipt_timing: Optional[CashReceiptTiming]
    cash_receipt_start_month: Optional[str]
    cash_receipt_date: Optional[str]
    requires_confirmation: bool
    confirmation_question: Optional[str]
    timing_summary: str
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Phrase parsing
# ---------------------------------------------------------------------------


# Phrases the planner / LLM might pass through verbatim. Match
# case-insensitive against the user's words. Order matters: "starting
# the month after next" must match before "starting next month" so the
# longer phrase wins.
_PHRASE_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(starting|starts|begins|begin)\s+the\s+month\s+after\s+next\b", re.IGNORECASE),
     "month_after_next"),
    (re.compile(r"\b(starting|starts|begins|begin)\s+next\s+month\b", re.IGNORECASE),
     "next_month"),
    (re.compile(r"\b(starting|starts|begins|begin)\s+(this|the\s+current)\s+month\b", re.IGNORECASE),
     "this_month"),
    (re.compile(r"\bstarting\s+immediately\b", re.IGNORECASE), "this_month"),
]


_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_DATE_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


def _is_valid_month(value: object) -> bool:
    return isinstance(value, str) and bool(_MONTH_RE.match(value))


def _is_valid_date(value: object) -> bool:
    return isinstance(value, str) and bool(_DATE_RE.match(value))


def _next_month(month: str) -> str:
    m = _MONTH_RE.match(month)
    if m is None:
        raise ValueError(f"_next_month requires YYYY-MM, got {month!r}")
    year, mo = int(m.group(1)), int(m.group(2))
    mo += 1
    if mo > 12:
        mo = 1
        year += 1
    return f"{year}-{mo:02d}"


def _date_to_month(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _resolve_phrase_to_anchor_month(
    phrase: Optional[str],
    current_date: Optional[date],
    projection_start_month: Optional[str],
) -> tuple[Optional[str], Optional[str], list[str]]:
    """Return (resolved_month, matched_rule_name, warnings).

    The anchor is "the month the user appears to be talking about." If
    no phrase is supplied, anchor is the projection_start_month or the
    current_date's month (in that order). Warnings call out
    inferred-from-phrase resolutions so the planner can ask the user to
    confirm if cash timing matters.
    """
    warnings: list[str] = []

    if phrase:
        for pattern, rule in _PHRASE_RULES:
            if pattern.search(phrase):
                anchor_source = projection_start_month or (
                    _date_to_month(current_date) if current_date is not None else None
                )
                if anchor_source is None:
                    return None, rule, warnings
                if rule == "this_month":
                    return anchor_source, rule, warnings
                if rule == "next_month":
                    warnings.append(
                        "Recognition month was inferred from the phrase 'starting "
                        "next month'; confirm with the user if exact timing matters."
                    )
                    return _next_month(anchor_source), rule, warnings
                if rule == "month_after_next":
                    warnings.append(
                        "Recognition month was inferred from the phrase 'starting "
                        "the month after next'; confirm with the user if exact "
                        "timing matters."
                    )
                    return _next_month(_next_month(anchor_source)), rule, warnings
        # Phrase supplied but no rule matched — fall through to the
        # default-anchor branch and surface a warning so the planner
        # can ask for an explicit YYYY-MM.
        warnings.append(
            "Could not interpret the timing phrase; falling back to the projection "
            "start month. Ask the user for an explicit recognition month if needed."
        )

    # No phrase or unmatched phrase — use the strongest anchor available.
    if projection_start_month is not None:
        return projection_start_month, None, warnings
    if current_date is not None:
        return _date_to_month(current_date), None, warnings
    return None, None, warnings


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------


def resolve_scenario_timing(
    phrase: Optional[str] = None,
    current_date: Optional[date] = None,
    projection_start_month: Optional[str] = None,
    explicit_starts_month: Optional[str] = None,
    cash_receipt_timing: Optional[CashReceiptTiming] = None,
    cash_receipt_date: Optional[str] = None,
) -> ScenarioTimingResult:
    """Resolve a manual assumption's timing into deterministic fields.

    Resolution order for the recognition month:
      1. ``explicit_starts_month`` if it is a valid YYYY-MM — caller wins.
      2. The phrase resolver, anchored on
         ``projection_start_month`` or ``current_date``.
      3. Otherwise return ``requires_confirmation=True`` with a
         confirmation_question telling the LLM what to ask.

    Cash receipt timing:
      * If ``cash_receipt_timing`` is "specific_date", a valid
        ``cash_receipt_date`` (YYYY-MM-DD) is required; otherwise the
        result asks for confirmation.
      * If the caller supplies "same_month" / "next_month", that wins.
      * If neither is supplied, the resolver returns
        ``requires_confirmation=True`` with a confirmation_question
        rather than silently choosing — this is the audited bug.

    Returns a frozen ScenarioTimingResult; never raises for normal
    ambiguity (those land in ``requires_confirmation``). Raises
    ``ValueError`` only when the inputs are structurally malformed
    (e.g. a non-YYYY-MM string in ``explicit_starts_month`` or
    ``projection_start_month``).
    """
    if explicit_starts_month is not None and not _is_valid_month(explicit_starts_month):
        raise ValueError(
            f"explicit_starts_month must be YYYY-MM, got {explicit_starts_month!r}"
        )
    if projection_start_month is not None and not _is_valid_month(projection_start_month):
        raise ValueError(
            f"projection_start_month must be YYYY-MM, got {projection_start_month!r}"
        )
    if cash_receipt_date is not None and not _is_valid_date(cash_receipt_date):
        raise ValueError(
            f"cash_receipt_date must be YYYY-MM-DD, got {cash_receipt_date!r}"
        )
    if cash_receipt_timing is not None and cash_receipt_timing not in (
        "same_month",
        "next_month",
        "specific_date",
    ):
        raise ValueError(
            "cash_receipt_timing must be one of 'same_month', 'next_month', "
            f"'specific_date', got {cash_receipt_timing!r}"
        )

    warnings: list[str] = []

    # --- Recognition month ----------------------------------------------
    if explicit_starts_month is not None:
        recognition_start_month: Optional[str] = explicit_starts_month
    else:
        resolved, rule, phrase_warnings = _resolve_phrase_to_anchor_month(
            phrase, current_date, projection_start_month
        )
        warnings.extend(phrase_warnings)
        recognition_start_month = resolved
        if recognition_start_month is None:
            return ScenarioTimingResult(
                recognition_start_month=None,
                cash_receipt_timing=None,
                cash_receipt_start_month=None,
                cash_receipt_date=None,
                requires_confirmation=True,
                confirmation_question=(
                    "What month should this assumption start in (YYYY-MM)? I "
                    "couldn't infer it from the phrase or context."
                ),
                timing_summary=(
                    "Timing unresolved: no recognition month could be inferred "
                    "from the inputs."
                ),
                warnings=warnings,
            )

    # --- Cash receipt timing --------------------------------------------
    if cash_receipt_timing == "specific_date":
        if cash_receipt_date is None:
            return ScenarioTimingResult(
                recognition_start_month=recognition_start_month,
                cash_receipt_timing=None,
                cash_receipt_start_month=None,
                cash_receipt_date=None,
                requires_confirmation=True,
                confirmation_question=(
                    "cash_receipt_timing='specific_date' requires a "
                    "cash_receipt_date (YYYY-MM-DD). What date should I use?"
                ),
                timing_summary=(
                    "Recognition starts in "
                    f"{recognition_start_month}; cash receipt timing is "
                    "unresolved (specific_date requires a date)."
                ),
                warnings=warnings,
            )
        cash_month = cash_receipt_date[:7]
        timing_summary = (
            f"Recognition starts in {recognition_start_month}; cash "
            f"receipt is scheduled for {cash_receipt_date} (specific_date)."
        )
        return ScenarioTimingResult(
            recognition_start_month=recognition_start_month,
            cash_receipt_timing="specific_date",
            cash_receipt_start_month=cash_month,
            cash_receipt_date=cash_receipt_date,
            requires_confirmation=False,
            confirmation_question=None,
            timing_summary=timing_summary,
            warnings=warnings,
        )

    if cash_receipt_timing is None:
        return ScenarioTimingResult(
            recognition_start_month=recognition_start_month,
            cash_receipt_timing=None,
            cash_receipt_start_month=None,
            cash_receipt_date=None,
            requires_confirmation=True,
            confirmation_question=(
                "Should the cash for this assumption land in the same month it "
                "starts, one month later, or on a specific date? (same_month / "
                "next_month / specific_date)"
            ),
            timing_summary=(
                f"Recognition starts in {recognition_start_month}; cash "
                "receipt timing is unresolved — caller must specify "
                "same_month, next_month, or specific_date."
            ),
            warnings=warnings,
        )

    # same_month / next_month deterministic branch
    if cash_receipt_timing == "same_month":
        cash_start = recognition_start_month
    else:  # next_month
        cash_start = _next_month(recognition_start_month)

    timing_summary = (
        f"Recognition starts in {recognition_start_month}; cash "
        f"receipt timing is {cash_receipt_timing} (first cash month: "
        f"{cash_start})."
    )
    return ScenarioTimingResult(
        recognition_start_month=recognition_start_month,
        cash_receipt_timing=cash_receipt_timing,
        cash_receipt_start_month=cash_start,
        cash_receipt_date=None,
        requires_confirmation=False,
        confirmation_question=None,
        timing_summary=timing_summary,
        warnings=warnings,
    )
