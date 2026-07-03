"""Wave 5 Thread 5c — sanity tests for the migration script's classifier.

The full script runs against MongoDB; here we just validate the pure-logic
_classify() function returns the right (updates, classification) tuples for
each combination the migration plan calls out.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("JWT_SECRET", "test-secret")

from migrate_vega_v1_to_v2 import _classify


def test_complete_session_gets_three_call_backfill():
    s = {"status": "complete", "calls": {"3": {"analysis": {"matrix": []}}}}
    updates, label = _classify(s)
    assert updates == {"discovery_mode": "three_call"}
    assert "three_call" in label


def test_in_progress_with_call3_analysis_promoted_to_single_call():
    s = {"status": "in_progress", "calls": {"3": {"analysis": {"matrix": [{"workflow_id": "wf1"}]}}}}
    updates, label = _classify(s)
    assert updates == {"discovery_mode": "single_call", "status": "complete"}
    assert "single_call" in label


def test_in_progress_calls_1_2_only_stays_three_call():
    s = {"status": "in_progress", "calls": {"1": {"analysis": {"summary": "hi"}},
                                              "2": {"analysis": {"matrix": []}}}}
    updates, label = _classify(s)
    assert updates == {"discovery_mode": "three_call"}
    assert "in_progress" in label


def test_in_progress_with_no_calls_stays_three_call():
    s = {"status": "in_progress", "calls": {}}
    updates, label = _classify(s)
    assert updates == {"discovery_mode": "three_call"}


def test_calls_dict_with_int_keys_also_recognized():
    """Mongo sometimes returns numeric keys; classifier should handle either."""
    s = {"status": "in_progress", "calls": {3: {"analysis": {"matrix": []}}}}
    updates, _ = _classify(s)
    assert updates == {"discovery_mode": "single_call", "status": "complete"}
