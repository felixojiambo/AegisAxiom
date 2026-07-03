"""
Canonical flag taxonomy for Vega structured output.

Every leaf field in the v3 schema that may not be extractable from a transcript
carries one of these six tokens.  They are the single source of truth consumed by:
  - Vega prompts (prompt_token)
  - Pydantic schema (VegaFlag enum values)
  - Frontend pill renderer (serialised as string, mirrored in vegaFlags.js)
  - Open Items aggregator (is_blocking_for_handoff)
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class VegaFlag(str, Enum):
    """Six canonical flag tokens emitted by Vega in structured output fields."""

    MISSING = "MISSING"
    NOT_DISCUSSED = "NOT_DISCUSSED"
    NOT_STATED = "NOT_STATED"
    INFERRED_CONFIRM = "INFERRED_CONFIRM"
    NEEDS_TECHNICAL_FOLLOWUP = "NEEDS_TECHNICAL_FOLLOWUP"
    SOURCE_UNCLEAR = "SOURCE_UNCLEAR"


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

_DISPLAY_LABELS: dict[VegaFlag, str] = {
    VegaFlag.MISSING: "MISSING",
    VegaFlag.NOT_DISCUSSED: "NOT DISCUSSED",
    VegaFlag.NOT_STATED: "NOT STATED",
    VegaFlag.INFERRED_CONFIRM: "INFERRED — CONFIRM",
    VegaFlag.NEEDS_TECHNICAL_FOLLOWUP: "NEEDS TECHNICAL FOLLOW-UP",
    VegaFlag.SOURCE_UNCLEAR: "SOURCE UNCLEAR",
}

_LONG_EXPLANATIONS: dict[VegaFlag, str] = {
    VegaFlag.MISSING: (
        "Field not found in transcript. Customer follow-up required."
    ),
    VegaFlag.NOT_DISCUSSED: (
        "Topic was not raised during the call. Confirm whether it applies before handoff."
    ),
    VegaFlag.NOT_STATED: (
        "Topic was not explicitly stated in the transcript. Do not assume a default value."
    ),
    VegaFlag.INFERRED_CONFIRM: (
        "Vega inferred this value from context (e.g. industry, role, or tone). "
        "Must be confirmed — do not treat as locked."
    ),
    VegaFlag.NEEDS_TECHNICAL_FOLLOWUP: (
        "Defer to a technical follow-up call. Not expected to surface in a business discovery call."
    ),
    VegaFlag.SOURCE_UNCLEAR: (
        "Vega could not identify a clear transcript source for this item. "
        "Verify with the customer before handoff."
    ),
}

_PROMPT_TOKENS: dict[VegaFlag, str] = {
    VegaFlag.MISSING: "[MISSING]",
    VegaFlag.NOT_DISCUSSED: "[NOT DISCUSSED]",
    VegaFlag.NOT_STATED: "[NOT STATED]",
    VegaFlag.INFERRED_CONFIRM: "[INFERRED — CONFIRM]",
    VegaFlag.NEEDS_TECHNICAL_FOLLOWUP: "[NEEDS TECHNICAL FOLLOW-UP]",
    VegaFlag.SOURCE_UNCLEAR: "[SOURCE UNCLEAR]",
}

# Flags that block dev handoff until resolved.
_BLOCKING_FLAGS: frozenset[VegaFlag] = frozenset(
    {VegaFlag.MISSING, VegaFlag.NEEDS_TECHNICAL_FOLLOWUP}
)

# ---------------------------------------------------------------------------
# Variant mapping for from_prompt_token()
#
# Keys are normalised inner text (stripped of brackets, collapsed whitespace,
# uppercased).  Values are the canonical VegaFlag.
# Longer / more specific entries are checked before shorter ones so that
# "[INFERRED — CONFIRM IN TECH FOLLOW-UP]" binds to INFERRED_CONFIRM, not
# NEEDS_TECHNICAL_FOLLOWUP.
# ---------------------------------------------------------------------------

# Anchored prefixes — the first word of the normalised token disambiguates
# between INFERRED_CONFIRM and NEEDS_TECHNICAL_FOLLOWUP. A token like
# "INFERRED — CONFIRM IN TECH FOLLOW-UP" must resolve to INFERRED_CONFIRM
# even though it contains the substring "TECHNICAL FOLLOW-UP".
_PREFIX_MAP: list[tuple[str, VegaFlag]] = [
    ("INFERRED", VegaFlag.INFERRED_CONFIRM),
    ("MISSING", VegaFlag.MISSING),
    ("NOT DISCUSSED", VegaFlag.NOT_DISCUSSED),
    ("NOT STATED", VegaFlag.NOT_STATED),
    ("SOURCE UNCLEAR", VegaFlag.SOURCE_UNCLEAR),
    ("ASK IN TECHNICAL FOLLOW-UP", VegaFlag.NEEDS_TECHNICAL_FOLLOWUP),
    ("NEEDS TECHNICAL FOLLOW-UP", VegaFlag.NEEDS_TECHNICAL_FOLLOWUP),
    ("NEEDS TECHNICAL FOLLOWUP", VegaFlag.NEEDS_TECHNICAL_FOLLOWUP),
]

# Substring fallbacks — only consulted if no anchored prefix matched.
# Ordered most-specific to least so e.g. a "TECHNICAL FOLLOW-UP" mid-token
# resolves to NEEDS_TECHNICAL_FOLLOWUP without being shadowed by INFERRED.
_VARIANT_MAP: list[tuple[str, VegaFlag]] = [
    ("ASK IN TECHNICAL FOLLOW-UP", VegaFlag.NEEDS_TECHNICAL_FOLLOWUP),
    ("NEEDS TECHNICAL FOLLOW-UP", VegaFlag.NEEDS_TECHNICAL_FOLLOWUP),
    ("NEEDS TECHNICAL FOLLOWUP", VegaFlag.NEEDS_TECHNICAL_FOLLOWUP),
    ("TECHNICAL FOLLOW-UP", VegaFlag.NEEDS_TECHNICAL_FOLLOWUP),
    ("MISSING", VegaFlag.MISSING),
    ("NOT DISCUSSED", VegaFlag.NOT_DISCUSSED),
    ("NOT STATED", VegaFlag.NOT_STATED),
    ("SOURCE UNCLEAR", VegaFlag.SOURCE_UNCLEAR),
    ("INFERRED", VegaFlag.INFERRED_CONFIRM),
]


def _normalise_token_text(raw: str) -> str:
    """Strip brackets, collapse whitespace, uppercase."""
    inner = re.sub(r"^\[|\]$", "", raw.strip())
    return re.sub(r"\s+", " ", inner).upper().strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def display_label(flag: VegaFlag) -> str:
    """Short text shown in the flag pill, e.g. 'NEEDS TECHNICAL FOLLOW-UP'."""
    return _DISPLAY_LABELS[flag]


def long_explanation(flag: VegaFlag) -> str:
    """Full explanatory sentence used in tooltips and PDF exports."""
    return _LONG_EXPLANATIONS[flag]


def prompt_token(flag: VegaFlag) -> str:
    """Bracketed token that Vega prompts instruct the LLM to emit, e.g. '[MISSING]'."""
    return _PROMPT_TOKENS[flag]


def is_blocking_for_handoff(flag: VegaFlag) -> bool:
    """
    True when the flag indicates dev cannot proceed without resolution.

    Blocking: MISSING, NEEDS_TECHNICAL_FOLLOWUP
    Non-blocking (warning): INFERRED_CONFIRM, NOT_DISCUSSED, NOT_STATED, SOURCE_UNCLEAR
    """
    return flag in _BLOCKING_FLAGS


def from_prompt_token(token: str) -> Optional[VegaFlag]:
    """
    Parse a bracketed token string — including requirements-doc variants — into
    the canonical VegaFlag.  Returns None if no match is found.

    Handled variants (case-insensitive, whitespace-normalised):
      [MISSING — confirm with customer]       -> MISSING
      [MISSING — success criteria not found]  -> MISSING
      [NOT DISCUSSED — confirm whether ...]   -> NOT_DISCUSSED
      [NOT STATED]                            -> NOT_STATED
      [INFERRED — CONFIRM IN TECH FOLLOW-UP]  -> INFERRED_CONFIRM
      [INFERRED]                              -> INFERRED_CONFIRM
      [NEEDS TECHNICAL FOLLOW-UP]             -> NEEDS_TECHNICAL_FOLLOWUP
      [ASK IN TECHNICAL FOLLOW-UP]            -> NEEDS_TECHNICAL_FOLLOWUP
      [SOURCE UNCLEAR — verify with ...]      -> SOURCE_UNCLEAR
    """
    normalised = _normalise_token_text(token)

    # Anchored prefix match wins — disambiguates "INFERRED — CONFIRM IN
    # TECH FOLLOW-UP" (must resolve to INFERRED_CONFIRM, not the technical
    # follow-up substring it happens to contain).
    for prefix, flag in _PREFIX_MAP:
        if normalised.startswith(prefix):
            return flag

    # Fall back to substring scan for tokens that don't have a clean
    # leading word (rare; mostly defensive against odd LLM emissions).
    for substring, flag in _VARIANT_MAP:
        if substring in normalised:
            return flag

    return None
