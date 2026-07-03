"""Wave 2 Thread 2e — BPMN renderer must skip lanes for actors that own no steps.

Regression coverage for the empty-swim-lane bug Aidan flagged: the LLM
sometimes declares an actor (e.g., a stakeholder mentioned but never assigned
a step) and the renderer used to emit `<bpmn:lane>` for them anyway, which
shows up in bpmn.io as an empty stripe.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.bpmn.json_to_xml import json_to_semantic_bpmn


def _wf(actors, steps, trigger_actor=None, decision_points=None):
    return {
        "workflow_id": "wf_test",
        "workflow_name": "Test Workflow",
        "description": "fixture",
        "actors": actors,
        "trigger": {"type": "manual", "description": "kickoff", "actor_id": trigger_actor or actors[0]["id"]},
        "steps": steps,
        "decision_points": decision_points or [],
        "exception_paths": [],
        "outputs": [],
    }


def test_empty_lane_actor_is_skipped():
    """Actor declared but never owns a step → no lane emitted."""
    workflow = _wf(
        actors=[
            {"id": "a1", "name": "Sales Rep", "type": "human"},
            {"id": "a2", "name": "Bystander", "type": "human"},  # no steps
        ],
        steps=[
            {"id": "s1", "name": "Open ticket", "actor_id": "a1", "task_type": "user"},
            {"id": "s2", "name": "Close ticket", "actor_id": "a1", "task_type": "user"},
        ],
    )

    xml = json_to_semantic_bpmn(workflow)

    assert 'name="Sales Rep"' in xml, "active actor lane should render"
    assert 'name="Bystander"' not in xml, "actor with zero steps must not render as a lane"
    assert 'id="Lane_a2"' not in xml, "no Lane_a2 element should be emitted"


def test_all_actors_with_steps_get_lanes():
    """When every declared actor owns at least one step, every lane should render."""
    workflow = _wf(
        actors=[
            {"id": "a1", "name": "Sales Rep", "type": "human"},
            {"id": "a2", "name": "AI Assistant", "type": "ai"},
        ],
        steps=[
            {"id": "s1", "name": "Submit", "actor_id": "a1", "task_type": "user"},
            {"id": "s2", "name": "Process", "actor_id": "a2", "task_type": "business_rule"},
        ],
    )

    xml = json_to_semantic_bpmn(workflow)
    assert 'name="Sales Rep"' in xml
    assert 'name="AI Assistant"' in xml
    assert 'id="Lane_a1"' in xml
    assert 'id="Lane_a2"' in xml
