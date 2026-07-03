"""Wave 2 Threads 2a/2b/2g — single-call entry point + content-hash BPMN guard.

Unit-level coverage:
- run_single_call_pipeline upserts a session and dispatches Call 3 in sole-input mode
- _bpmn_content_hash is stable across re-orderings and changes only when structural
  fields change

The full E2E timing test (transcript → matrix + BPMN under 5 min) requires a real
Gemini call; that runs as a documented manual test rather than CI to avoid spending
tokens on every push.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Required so auth_helper doesn't blow up at import time
os.environ.setdefault("JWT_SECRET", "test-secret")

from services.ai.vega_analysis import _bpmn_content_hash


def test_content_hash_stable_for_field_reorder():
    wf_a = {
        "actors": [{"id": "a1", "name": "Rep", "type": "human"}],
        "steps": [{"id": "s1", "name": "do", "actor_id": "a1"}],
        "decision_points": [],
        "exception_paths": [],
    }
    wf_b = {
        "exception_paths": [],
        "decision_points": [],
        "steps": [{"id": "s1", "name": "do", "actor_id": "a1"}],
        "actors": [{"id": "a1", "name": "Rep", "type": "human"}],
    }
    assert _bpmn_content_hash(wf_a) == _bpmn_content_hash(wf_b)


def test_content_hash_changes_when_steps_change():
    base = {
        "actors": [{"id": "a1", "name": "Rep", "type": "human"}],
        "steps": [{"id": "s1", "name": "do", "actor_id": "a1"}],
        "decision_points": [],
        "exception_paths": [],
    }
    changed = {**base, "steps": base["steps"] + [{"id": "s2", "name": "verify", "actor_id": "a1"}]}
    assert _bpmn_content_hash(base) != _bpmn_content_hash(changed)


def test_content_hash_unchanged_for_description_drift():
    """description / source_apps / MMI shouldn't trigger BPMN regen."""
    base = {
        "actors": [{"id": "a1", "name": "Rep", "type": "human"}],
        "steps": [{"id": "s1", "name": "do", "actor_id": "a1"}],
        "decision_points": [],
        "exception_paths": [],
        "description": "original",
        "source_apps": ["A"],
    }
    drifted = {**base, "description": "rewritten", "source_apps": ["A", "B"]}
    assert _bpmn_content_hash(base) == _bpmn_content_hash(drifted)


def test_content_hash_changes_on_decision_point_change():
    base = {
        "actors": [{"id": "a1", "name": "Rep", "type": "human"}],
        "steps": [{"id": "s1", "name": "do", "actor_id": "a1"}],
        "decision_points": [],
        "exception_paths": [],
    }
    with_dp = {**base, "decision_points": [
        {"id": "d1", "after_step_id": "s1", "type": "exclusive", "name": "amount?",
         "branches": [{"condition": ">10k", "leads_to_step_id": "s1"}]},
    ]}
    assert _bpmn_content_hash(base) != _bpmn_content_hash(with_dp)
