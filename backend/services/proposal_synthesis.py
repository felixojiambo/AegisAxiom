"""Numerical drift guard for AI-polished proposal output.

Protects against AI polish silently changing factual numbers that
came from the deterministic skeleton. The guard extracts numeric
facts (currency, hours, percentages, dates, quantities) from both
the skeleton and the polished output, normalizes them to a
comparable form, and rejects the polished output when any fact
was added, removed, or changed.

The module is local, testable, and deterministic. It does NOT call
any LLM or external service. The polish callable is supplied by
the caller; today no polish is wired into the proposal lifecycle,
so the guard infrastructure is dormant at runtime but unit-tested
directly. When a future ticket adds an AI polish step, the
regenerate route in routes/proposals.py is the only place the
polish callable plugs in.

See docs/sales-ops/PROPOSAL_LIFECYCLE_V2.md for the policy.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Data shapes
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class NumericFact:
    """A normalized numeric token extracted from proposal text.

    Two facts compare equal when their kind and value match. The
    display field is the original token from the source text and is
    used only in drift reports for human readability; it does not
    participate in equality.
    """

    kind: str
    value: Any
    display: str = field(compare=False)


@dataclass
class ComparisonResult:
    """Outcome of comparing skeleton facts to polished facts.

    equivalent is True only when every fact in skeleton has a
    matching fact in polished and vice versa, accounting for
    multiplicity (the same currency value appearing twice in the
    skeleton must appear twice in the polished output).
    """

    equivalent: bool
    added: list[NumericFact]
    removed: list[NumericFact]
    changed: list[tuple[NumericFact, NumericFact]]


@dataclass
class PolishResult:
    """Outcome of running apply_polish_with_drift_guard.

    When the polish callable is None, content is the input
    skeleton and the drift flags are False. When polish runs and
    produces drift-free output, content is the polish output and
    drift_detected is False. When drift is detected, content
    falls back to the skeleton, drift_detected is True, and
    drift_details carries the per-fact report.
    """

    content: str
    drift_detected: bool
    fallback_used: bool
    drift_details: Optional[dict] = None


# -----------------------------------------------------------------------------
# Extractors
# -----------------------------------------------------------------------------


# Currency patterns. Anchored on either a leading $ or a trailing
# currency-suggesting suffix. Hours and percentages are handled
# separately so they do not collide with this matcher. The
# extractor produces a single normalized float value per match so
# $1,234.56 and 1234.56 collapse to the same fact.
#
# Right boundary is `(?!\.?\d)` rather than `(?![\w.\d])` so that
# a sentence-ending period after the number does not prevent the
# match. The period followed by a digit (e.g. `1.50.20`) is still
# rejected because the dot-then-digit pattern fires the lookahead.
_CURRENCY_RX = re.compile(
    r"""
    (?<![\d.])                  # left boundary so 1.50 is not matched after $1.50
    (?:
        \$\s*([\d,]+(?:\.\d+)?)             # $1,234.56
        |
        ([\d,]+(?:\.\d+)?)\s*(?:USD|usd)    # 1,234.56 USD
    )
    (?!\.?\d)                   # right boundary rejects digit continuation
    """,
    re.VERBOSE,
)

# Hours pattern. Matches 12h, 12 h, 12 hour, 12 hours, 12.5h.
# Anchored so percentage tokens like 12% are not captured.
_HOURS_RX = re.compile(
    r"""
    (?<![\w.\d])
    (\d+(?:\.\d+)?)\s*(?:h\b|hour\b|hours\b)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Percentage pattern. Matches 12%, 12.5%.
_PERCENT_RX = re.compile(
    r"""
    (?<![\w.\d])
    (\d+(?:\.\d+)?)\s*%
    """,
    re.VERBOSE,
)

# ISO-style and slash-style dates. YYYY-MM-DD, MM/DD/YYYY,
# M/D/YYYY. The extractor normalizes to YYYY-MM-DD for
# comparison. Month-name dates (Jan 5, 2099) are intentionally
# excluded from V1 to avoid locale-pinning the extractor; the
# deterministic generator emits ISO format today.
_ISO_DATE_RX = re.compile(
    r"""
    (?<![\w])
    (\d{4})-(\d{1,2})-(\d{1,2})
    (?![\w])
    """,
    re.VERBOSE,
)
_SLASH_DATE_RX = re.compile(
    r"""
    (?<![\w])
    (\d{1,2})/(\d{1,2})/(\d{4})
    (?![\w])
    """,
    re.VERBOSE,
)

# Quantity pattern. Conservative: an integer followed by a
# whitespace-separated unit-suggesting word ("users", "seats",
# "endpoints", "items", "licenses"). Bare integers in narrative
# prose are not captured so a number like "team of 5" does not
# trip the guard. The unit list is the canonical short list a
# Sales Ops proposal typically quotes.
_QUANTITY_UNIT_WORDS = frozenset(
    {
        "users",
        "user",
        "seats",
        "seat",
        "endpoints",
        "endpoint",
        "licenses",
        "license",
        "devices",
        "device",
        "sites",
        "site",
        "locations",
        "location",
        "items",
        "item",
        "units",
        "unit",
    }
)
_QUANTITY_RX = re.compile(
    r"""
    (?<![\w.\d])
    (\d+)\s+([A-Za-z]+)
    """,
    re.VERBOSE,
)


def _normalize_currency(raw: str) -> float:
    """Parse a currency token (with optional commas) into a float."""
    cleaned = raw.replace(",", "").strip()
    return float(cleaned)


def _normalize_hours(raw: str) -> float:
    return float(raw)


def _normalize_percent(raw: str) -> float:
    return float(raw)


def _normalize_iso(year: str, month: str, day: str) -> str:
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def extract_numeric_facts(text: str) -> list[NumericFact]:
    """Return every recognized numeric fact in text.

    The result preserves order of appearance. Comparable facts
    appearing multiple times appear multiple times in the result
    so the comparison can be multiset-correct: dropping a
    duplicate currency line is a drift, not a no-op.
    """
    if not isinstance(text, str) or not text:
        return []

    facts: list[NumericFact] = []

    for m in _CURRENCY_RX.finditer(text):
        raw = m.group(1) or m.group(2)
        try:
            value = _normalize_currency(raw)
        except (TypeError, ValueError):
            continue
        facts.append(NumericFact(kind="currency", value=value, display=m.group(0)))

    for m in _HOURS_RX.finditer(text):
        try:
            value = _normalize_hours(m.group(1))
        except (TypeError, ValueError):
            continue
        facts.append(NumericFact(kind="hours", value=value, display=m.group(0)))

    for m in _PERCENT_RX.finditer(text):
        try:
            value = _normalize_percent(m.group(1))
        except (TypeError, ValueError):
            continue
        facts.append(NumericFact(kind="percentage", value=value, display=m.group(0)))

    for m in _ISO_DATE_RX.finditer(text):
        try:
            value = _normalize_iso(m.group(1), m.group(2), m.group(3))
        except (TypeError, ValueError):
            continue
        facts.append(NumericFact(kind="date", value=value, display=m.group(0)))

    for m in _SLASH_DATE_RX.finditer(text):
        try:
            # MM/DD/YYYY canonical form. The deterministic generator
            # writes ISO dates today; the slash matcher exists so
            # polished output that re-formats a date does not slip
            # through as a non-fact.
            value = _normalize_iso(m.group(3), m.group(1), m.group(2))
        except (TypeError, ValueError):
            continue
        facts.append(NumericFact(kind="date", value=value, display=m.group(0)))

    for m in _QUANTITY_RX.finditer(text):
        unit = m.group(2).lower()
        if unit not in _QUANTITY_UNIT_WORDS:
            continue
        try:
            value = int(m.group(1))
        except (TypeError, ValueError):
            continue
        # Canonicalize singular and plural to the same comparable
        # unit so a polish that rewrites "5 users" as "5 user"
        # does not trigger drift. The display field keeps the
        # original wording.
        canonical_unit = unit.rstrip("s") if unit.endswith("s") and unit != "s" else unit
        facts.append(
            NumericFact(
                kind="quantity",
                value=(value, canonical_unit),
                display=m.group(0),
            )
        )

    return facts


# -----------------------------------------------------------------------------
# Comparison
# -----------------------------------------------------------------------------


def _multiset(facts: list[NumericFact]) -> dict:
    """Build a multiplicity map keyed by (kind, value)."""
    out: dict[tuple[str, Any], int] = {}
    for f in facts:
        key = (f.kind, f.value)
        out[key] = out.get(key, 0) + 1
    return out


def _collect_unmatched(
    source: list[NumericFact],
    other_counts: dict[tuple[str, Any], int],
) -> list[NumericFact]:
    """Return the facts from source that have no surviving match
    in other_counts. Walks source in order so each distinct token
    instance is preserved; the counts map is decremented for each
    matched fact so a duplicate token does not get matched twice
    against a single counterpart.

    The returned list contains the actual NumericFact instances
    from source, so a caller can pair them with counterparts from
    the other side without losing duplicate identity.
    """
    counts = dict(other_counts)
    unmatched: list[NumericFact] = []
    for f in source:
        key = (f.kind, f.value)
        if counts.get(key, 0) > 0:
            counts[key] -= 1
        else:
            unmatched.append(f)
    return unmatched


def compare_numeric_facts(
    skeleton: list[NumericFact],
    polished: list[NumericFact],
) -> ComparisonResult:
    """Compare two fact lists as multisets keyed by (kind, value).

    A fact is considered equivalent across the two lists when the
    kind and the normalized value match. Multiplicity matters: a
    polished output that drops a duplicate currency line is a
    drift, not a no-op. The display field is ignored for equality
    so $1,000 and 1000.00 collapse to the same fact.

    The result categorizes drift as added (present in polished but
    not in skeleton), removed (present in skeleton but not in
    polished), and changed (a kind has different values; surfaced
    as a hint to the reader that a value was rewritten).
    """
    sk_counts = _multiset(skeleton)
    po_counts = _multiset(polished)

    # Walk each side and collect the facts that have no surviving
    # counterpart on the other side. Walking the original lists
    # preserves distinct NumericFact instances for duplicate tokens
    # so the pairing logic below can pair each token individually
    # rather than collapsing every duplicate onto a single
    # representative reference.
    unmatched_added = _collect_unmatched(polished, sk_counts)
    unmatched_removed = _collect_unmatched(skeleton, po_counts)

    # Heuristic for "changed": pair an added fact with a removed
    # fact of the same kind whose value differs. Lets the drift
    # report surface "the $1,000 turned into $1,500" as a single
    # changed line instead of a (removed, added) pair. Pure
    # additions and pure removals that have no matching kind on the
    # other side stay in their respective lists. Pairing is index
    # based so identical duplicate tokens are each paired
    # individually.
    by_kind_added: dict[str, list[NumericFact]] = {}
    for f in unmatched_added:
        by_kind_added.setdefault(f.kind, []).append(f)
    by_kind_removed: dict[str, list[NumericFact]] = {}
    for f in unmatched_removed:
        by_kind_removed.setdefault(f.kind, []).append(f)

    changed: list[tuple[NumericFact, NumericFact]] = []
    paired_added_ids: set[int] = set()
    paired_removed_ids: set[int] = set()
    for kind, removed_list in list(by_kind_removed.items()):
        added_list = by_kind_added.get(kind, [])
        while removed_list and added_list:
            r = removed_list.pop(0)
            a = added_list.pop(0)
            changed.append((r, a))
            paired_removed_ids.add(id(r))
            paired_added_ids.add(id(a))

    # Identity-based filter so duplicate tokens that share a
    # (kind, value) are not collapsed by NumericFact.__eq__ which
    # ignores display. Each NumericFact instance survives or is
    # removed on its own.
    remaining_added = [
        f for f in unmatched_added if id(f) not in paired_added_ids
    ]
    remaining_removed = [
        f for f in unmatched_removed if id(f) not in paired_removed_ids
    ]

    equivalent = not (remaining_added or remaining_removed or changed)
    return ComparisonResult(
        equivalent=equivalent,
        added=remaining_added,
        removed=remaining_removed,
        changed=changed,
    )


def _details_payload(result: ComparisonResult) -> dict:
    """Serialize a comparison result into a JSON-safe dict for
    audit and snapshot metadata. Uses the display field of each
    fact so the human reader can see what the original token was
    without re-running the extractor."""

    def fact_to_dict(f: NumericFact) -> dict:
        return {"kind": f.kind, "value": f.value, "display": f.display}

    return {
        "added": [fact_to_dict(f) for f in result.added],
        "removed": [fact_to_dict(f) for f in result.removed],
        "changed": [
            {"from": fact_to_dict(r), "to": fact_to_dict(a)}
            for r, a in result.changed
        ],
    }


# -----------------------------------------------------------------------------
# Public guard
# -----------------------------------------------------------------------------


def apply_polish_with_drift_guard(
    skeleton: str,
    polish: Optional[Callable[[str], str]],
) -> PolishResult:
    """Run the polish callable, run the drift guard, and return
    a PolishResult.

    When polish is None, the result is the input skeleton with
    no drift flags set. Today the regenerate route passes None;
    the guard infrastructure stands ready for a future ticket.

    When polish raises, the exception is caught, logged at warning
    level inside this function, and treated as a fallback. The
    exception detail never reaches the PolishResult so the route
    does not leak internal stack traces into a response body or an
    audit row. Callers can rely on this guard without remembering
    to install their own log statement around every call site.

    When polish succeeds but the polished output introduces
    numeric drift, the skeleton is returned and drift_detected /
    fallback_used are True. drift_details lists the offending
    facts as a JSON-safe dict suitable for the audit row.
    """
    if polish is None:
        return PolishResult(
            content=skeleton,
            drift_detected=False,
            fallback_used=False,
            drift_details=None,
        )

    try:
        polished_text = polish(skeleton)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "proposal polish callable raised; falling back to skeleton: %s",
            e,
        )
        return PolishResult(
            content=skeleton,
            drift_detected=False,
            fallback_used=True,
            drift_details=None,
        )

    if not isinstance(polished_text, str) or not polished_text:
        return PolishResult(
            content=skeleton,
            drift_detected=False,
            fallback_used=True,
            drift_details=None,
        )

    skeleton_facts = extract_numeric_facts(skeleton)
    polished_facts = extract_numeric_facts(polished_text)
    result = compare_numeric_facts(skeleton_facts, polished_facts)

    if result.equivalent:
        return PolishResult(
            content=polished_text,
            drift_detected=False,
            fallback_used=False,
            drift_details=None,
        )

    return PolishResult(
        content=skeleton,
        drift_detected=True,
        fallback_used=True,
        drift_details=_details_payload(result),
    )
