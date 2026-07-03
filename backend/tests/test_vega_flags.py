"""
Unit tests for backend/services/vega/flags.py

Run with: docker compose exec backend pytest tests/test_vega_flags.py -q
"""

import pytest

from services.vega.flags import (
    VegaFlag,
    display_label,
    from_prompt_token,
    is_blocking_for_handoff,
    long_explanation,
    prompt_token,
)

ALL_FLAGS = list(VegaFlag)


# ---------------------------------------------------------------------------
# Enum basics
# ---------------------------------------------------------------------------


class TestEnumRoundTrip:
    def test_value_equals_string(self):
        # VegaFlag is a str subclass — the enum member IS the string value.
        # str(member) in Python 3.11 returns "VegaFlag.MEMBER"; use .value instead.
        assert VegaFlag.MISSING.value == "MISSING"
        assert VegaFlag.NEEDS_TECHNICAL_FOLLOWUP.value == "NEEDS_TECHNICAL_FOLLOWUP"
        # Equality comparison works directly because VegaFlag subclasses str.
        assert VegaFlag.MISSING == "MISSING"
        assert VegaFlag.NOT_DISCUSSED == "NOT_DISCUSSED"

    def test_construct_from_value(self):
        assert VegaFlag("MISSING") is VegaFlag.MISSING
        assert VegaFlag("NOT_DISCUSSED") is VegaFlag.NOT_DISCUSSED
        assert VegaFlag("NOT_STATED") is VegaFlag.NOT_STATED
        assert VegaFlag("INFERRED_CONFIRM") is VegaFlag.INFERRED_CONFIRM
        assert VegaFlag("NEEDS_TECHNICAL_FOLLOWUP") is VegaFlag.NEEDS_TECHNICAL_FOLLOWUP
        assert VegaFlag("SOURCE_UNCLEAR") is VegaFlag.SOURCE_UNCLEAR

    def test_six_members(self):
        assert len(ALL_FLAGS) == 6


# ---------------------------------------------------------------------------
# prompt_token / from_prompt_token inverses
# ---------------------------------------------------------------------------


class TestPromptTokenRoundTrip:
    @pytest.mark.parametrize("flag", ALL_FLAGS)
    def test_canonical_round_trip(self, flag):
        token = prompt_token(flag)
        assert token.startswith("[") and token.endswith("]")
        recovered = from_prompt_token(token)
        assert recovered is flag, f"{token!r} did not round-trip to {flag}"

    def test_unknown_token_returns_none(self):
        assert from_prompt_token("[SOMETHING RANDOM]") is None
        assert from_prompt_token("no brackets at all") is None
        assert from_prompt_token("") is None


# ---------------------------------------------------------------------------
# Requirements-doc variant handling
# ---------------------------------------------------------------------------


class TestVariantMapping:
    def test_inferred_confirm_in_tech_follow_up(self):
        result = from_prompt_token("[INFERRED — CONFIRM IN TECH FOLLOW-UP]")
        assert result is VegaFlag.INFERRED_CONFIRM

    def test_inferred_confirm_plain(self):
        result = from_prompt_token("[INFERRED — CONFIRM]")
        assert result is VegaFlag.INFERRED_CONFIRM

    def test_ask_in_technical_follow_up(self):
        result = from_prompt_token("[ASK IN TECHNICAL FOLLOW-UP]")
        assert result is VegaFlag.NEEDS_TECHNICAL_FOLLOWUP

    def test_missing_with_trailing_note(self):
        result = from_prompt_token(
            "[MISSING — confirm with customer]"
        )
        assert result is VegaFlag.MISSING

    def test_missing_success_criteria_variant(self):
        result = from_prompt_token(
            "[MISSING — success criteria not found in transcript. Flag for customer follow-up]"
        )
        assert result is VegaFlag.MISSING

    def test_not_discussed_with_trailing_note(self):
        result = from_prompt_token(
            "[NOT DISCUSSED — confirm whether IT/infosec gate applies]"
        )
        assert result is VegaFlag.NOT_DISCUSSED

    def test_source_unclear_with_trailing_note(self):
        result = from_prompt_token("[SOURCE UNCLEAR — verify with customer]")
        assert result is VegaFlag.SOURCE_UNCLEAR

    def test_technical_follow_up_short_form(self):
        result = from_prompt_token("[NEEDS TECHNICAL FOLLOW-UP]")
        assert result is VegaFlag.NEEDS_TECHNICAL_FOLLOWUP

    def test_case_insensitive(self):
        result = from_prompt_token("[missing]")
        assert result is VegaFlag.MISSING


# ---------------------------------------------------------------------------
# is_blocking_for_handoff
# ---------------------------------------------------------------------------


class TestBlockingFlags:
    @pytest.mark.parametrize(
        "flag, expected",
        [
            (VegaFlag.MISSING, True),
            (VegaFlag.NEEDS_TECHNICAL_FOLLOWUP, True),
            (VegaFlag.NOT_DISCUSSED, False),
            (VegaFlag.NOT_STATED, False),
            (VegaFlag.INFERRED_CONFIRM, False),
            (VegaFlag.SOURCE_UNCLEAR, False),
        ],
    )
    def test_classification(self, flag, expected):
        assert is_blocking_for_handoff(flag) is expected

    def test_exactly_two_blocking_flags(self):
        blocking = [f for f in ALL_FLAGS if is_blocking_for_handoff(f)]
        assert len(blocking) == 2


# ---------------------------------------------------------------------------
# display_label
# ---------------------------------------------------------------------------


class TestDisplayLabel:
    @pytest.mark.parametrize("flag", ALL_FLAGS)
    def test_non_empty_string(self, flag):
        label = display_label(flag)
        assert isinstance(label, str) and len(label) > 0

    def test_specific_labels(self):
        assert display_label(VegaFlag.MISSING) == "MISSING"
        assert display_label(VegaFlag.NEEDS_TECHNICAL_FOLLOWUP) == "NEEDS TECHNICAL FOLLOW-UP"
        assert display_label(VegaFlag.INFERRED_CONFIRM) == "INFERRED — CONFIRM"
        assert display_label(VegaFlag.NOT_DISCUSSED) == "NOT DISCUSSED"
        assert display_label(VegaFlag.NOT_STATED) == "NOT STATED"
        assert display_label(VegaFlag.SOURCE_UNCLEAR) == "SOURCE UNCLEAR"


# ---------------------------------------------------------------------------
# long_explanation
# ---------------------------------------------------------------------------


class TestLongExplanation:
    @pytest.mark.parametrize("flag", ALL_FLAGS)
    def test_non_empty_string(self, flag):
        explanation = long_explanation(flag)
        assert isinstance(explanation, str) and len(explanation) > 0

    def test_blocking_flags_mention_follow_up(self):
        assert "follow" in long_explanation(VegaFlag.MISSING).lower()
        assert "follow" in long_explanation(VegaFlag.NEEDS_TECHNICAL_FOLLOWUP).lower()
