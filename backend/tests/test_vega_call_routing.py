"""Test Vega call-number resolution from chat history.

Regression coverage for the Pinecrest bug where Call 3 analyses were mis-routed
to Call 2 because the session-picker message ("Pinecrest (Call 2 approved, ...)")
was tagged [INTAKE SELECTION] and confused a substring matcher.
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routes.ai_chat_artifacts import _resolve_call_num_from_history


def _msgs(*contents):
    """Build a list of message dicts ordered most-recent-first."""
    return [{"content": c} for c in contents]


def test_call_3_intake_then_session_pick_resolves_to_3():
    history = _msgs(
        "[INTAKE SELECTION] Just finished Call 3",
        "[INTAKE SELECTION] Pinecrest Property Management (Call 2 approved, Apr 27)",
    )
    assert _resolve_call_num_from_history(history) == 3


def test_call_2_intake_then_session_pick_resolves_to_2():
    history = _msgs(
        "[INTAKE SELECTION] Just finished Call 2",
        "[INTAKE SELECTION] Acme (Call 1 approved, Apr 20)",
    )
    assert _resolve_call_num_from_history(history) == 2


def test_call_1_intake_resolves_to_1():
    history = _msgs("[INTAKE SELECTION] Just finished Call 1")
    assert _resolve_call_num_from_history(history) == 1


def test_direct_analysis_phrase_resolves_to_0():
    history = _msgs("[INTAKE SELECTION] documents to analyze")
    assert _resolve_call_num_from_history(history) == 0


def test_direct_analysis_token_resolves_to_0():
    history = _msgs("[INTAKE SELECTION] direct_analysis")
    assert _resolve_call_num_from_history(history) == 0


def test_post_call_3_token_resolves_to_3():
    history = _msgs("[INTAKE SELECTION] post_call_3")
    assert _resolve_call_num_from_history(history) == 3


def test_post_call_2_token_resolves_to_2():
    history = _msgs("[INTAKE SELECTION] post_call_2")
    assert _resolve_call_num_from_history(history) == 2


def test_no_intake_messages_defaults_to_1():
    assert _resolve_call_num_from_history([]) == 1


def test_only_session_pick_defaults_to_1():
    """Session-pick descriptions alone (no real intake-call selection) → default 1."""
    history = _msgs(
        "[INTAKE SELECTION] Acme (Call 3 approved, Apr 25)",
    )
    assert _resolve_call_num_from_history(history) == 1


def test_call_22_does_not_match_call_2():
    """Word boundary must prevent 'Call 22' from matching 'Call 2'."""
    history = _msgs("[INTAKE SELECTION] Just finished Call 22")
    assert _resolve_call_num_from_history(history) == 1


def test_call_11_does_not_match_call_1():
    """Word boundary must prevent 'Call 11' from matching 'Call 1'."""
    history = _msgs("[INTAKE SELECTION] Just finished Call 11")
    assert _resolve_call_num_from_history(history) == 1


def test_call_33_does_not_match_call_3():
    """Word boundary must prevent 'Call 33' from matching 'Call 3'."""
    history = _msgs("[INTAKE SELECTION] Just finished Call 33")
    assert _resolve_call_num_from_history(history) == 1


def test_direct_analysis_overrides_call_number():
    """If both a call-number message and direct_analysis are in history, direct_analysis wins."""
    history = _msgs(
        "[INTAKE SELECTION] direct_analysis",
        "[INTAKE SELECTION] Just finished Call 3",
    )
    assert _resolve_call_num_from_history(history) == 0


@pytest.mark.parametrize(
    "history,expected",
    [
        (_msgs("[INTAKE SELECTION] Just finished Call 3",
               "[INTAKE SELECTION] X (Call 2 approved, Apr 27)"), 3),
        (_msgs("[INTAKE SELECTION] Just finished Call 2",
               "[INTAKE SELECTION] X (Call 1 approved, Apr 20)"), 2),
        (_msgs("[INTAKE SELECTION] Just finished Call 1"), 1),
        (_msgs("[INTAKE SELECTION] documents to analyze"), 0),
        (_msgs("[INTAKE SELECTION] post_call_3"), 3),
        (_msgs("[INTAKE SELECTION] post_call_2"), 2),
        ([], 1),
        (_msgs("[INTAKE SELECTION] X (Call 3 approved, Apr 25)"), 1),
        (_msgs("[INTAKE SELECTION] Just finished Call 22"), 1),
    ],
)
def test_routing_table(history, expected):
    """Spec table from the bug report — every row must hold."""
    assert _resolve_call_num_from_history(history) == expected
