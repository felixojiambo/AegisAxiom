"""Wave 4 Thread 4h — BPMN richness regression coverage.

Covers:
- Named data objects render as <bpmn:dataObjectReference> with the literal name
- Tasks that consume data emit <bpmn:dataInputAssociation> referencing the
  data object reference
- Tasks that produce data emit <bpmn:dataOutputAssociation>
- Generic data names ("data1", "input") are filtered out
- A workflow with `call_activities` renders <bpmn:callActivity> with the
  correct calledElement attribute
- The reusable-AI-subroutine detector + promoter mutates the matrix in-place
  with `call_activities` entries on every workflow that shares the AI step
  except the canonical one
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("JWT_SECRET", "test-secret")

from services.bpmn.json_to_xml import json_to_semantic_bpmn
from services.ai.vega_analysis import (
    _detect_reusable_ai_subroutines,
    _promote_call_activities,
)


def _wf_with_data():
    return {
        "workflow_id": "wf_invoice",
        "workflow_name": "Invoice approval",
        "actors": [
            {"id": "a1", "name": "Account Manager", "type": "human"},
            {"id": "a2", "name": "AI Approver", "type": "ai"},
        ],
        "trigger": {"type": "manual", "description": "Invoice arrives", "actor_id": "a1"},
        "steps": [
            {"id": "s1", "name": "Receive invoice", "actor_id": "a1", "task_type": "user",
             "data_outputs": [{"name": "Raw Invoice", "destination_system": "queue"}]},
            {"id": "s2", "name": "Approve invoice", "actor_id": "a2", "task_type": "business_rule",
             "data_inputs": [{"name": "Raw Invoice", "source_system": "queue"}],
             "data_outputs": [{"name": "Approved Invoice", "destination_system": "NetSuite"}]},
            {"id": "s3", "name": "File invoice", "actor_id": "a1", "task_type": "user",
             "data_inputs": [{"name": "Approved Invoice", "source_system": "NetSuite"}]},
        ],
        "decision_points": [],
        "exception_paths": [],
        "outputs": [],
    }


def test_named_data_objects_render():
    xml = json_to_semantic_bpmn(_wf_with_data())
    assert '<bpmn:dataObject id="DataObj_raw_invoice" />' in xml
    assert '<bpmn:dataObject id="DataObj_approved_invoice" />' in xml
    assert 'name="Raw Invoice"' in xml
    assert 'name="Approved Invoice"' in xml


def test_data_input_output_associations_render():
    xml = json_to_semantic_bpmn(_wf_with_data())
    assert "<bpmn:dataOutputAssociation" in xml
    assert "<bpmn:dataInputAssociation" in xml
    # The producer step (s1) emits an output association referencing DataRef_raw_invoice
    assert "DataOutAssoc_s1_DataObj_raw_invoice" in xml
    # The consumer step (s2) emits an input association referencing DataRef_raw_invoice
    assert "DataInAssoc_s2_DataObj_raw_invoice" in xml


def test_generic_data_names_are_skipped():
    wf = _wf_with_data()
    wf["steps"][0]["data_outputs"] = [{"name": "data1", "destination_system": "queue"}]
    wf["steps"][1]["data_inputs"] = [{"name": "input", "source_system": "queue"}]
    # Approved Invoice should still show up; data1 / input must not
    xml = json_to_semantic_bpmn(wf)
    assert 'name="Approved Invoice"' in xml
    assert "DataObj_data1" not in xml
    assert "DataObj_input" not in xml


def test_call_activity_renders_when_present():
    wf = _wf_with_data()
    wf["call_activities"] = [
        {"id": "ca_classify", "name": "Classify document",
         "called_element_ref": "Process_subroutine_classify_document"},
    ]
    xml = json_to_semantic_bpmn(wf)
    assert '<bpmn:callActivity id="CallActivity_ca_classify"' in xml
    assert 'name="Classify document"' in xml
    assert 'calledElement="Process_subroutine_classify_document"' in xml


def test_reusable_ai_subroutine_detection_promotes_only_duplicates():
    """Same AI 'Classify document' step in 2 workflows → 1 promotion (the second)."""
    matrix = [
        {
            "workflow_id": "wf_invoices",
            "workflow_name": "Invoice triage",
            "actors": [{"id": "a1", "name": "Rep", "type": "human"},
                        {"id": "ai", "name": "AI", "type": "ai"}],
            "steps": [
                {"id": "s1", "name": "Classify document", "actor_id": "ai", "task_type": "business_rule"},
                {"id": "s2", "name": "Approve invoice", "actor_id": "a1", "task_type": "user"},
            ],
        },
        {
            "workflow_id": "wf_contracts",
            "workflow_name": "Contract triage",
            "actors": [{"id": "a1", "name": "Rep", "type": "human"},
                        {"id": "ai", "name": "AI", "type": "ai"}],
            "steps": [
                {"id": "s5", "name": "Classify document", "actor_id": "ai", "task_type": "business_rule"},
                {"id": "s6", "name": "Send to legal", "actor_id": "a1", "task_type": "user"},
            ],
        },
    ]
    detected = _detect_reusable_ai_subroutines(matrix)
    assert "classify document" in detected
    promoted = _promote_call_activities(matrix)
    assert promoted == 1, f"expected exactly 1 promotion, got {promoted}"
    # First workflow keeps the original step; second workflow gets a call_activities entry
    assert not matrix[0].get("call_activities")
    assert len(matrix[1]["call_activities"]) == 1
    assert matrix[1]["call_activities"][0]["called_element_ref"] == "Process_subroutine_classify_document"


def test_reusable_subroutine_promotion_idempotent():
    """Re-running the promoter on already-promoted matrix doesn't double-add."""
    matrix = [
        {"workflow_id": "wf_a", "actors": [{"id": "ai", "name": "AI", "type": "ai"}],
         "steps": [{"id": "sa", "name": "Score lead", "actor_id": "ai", "task_type": "business_rule"}]},
        {"workflow_id": "wf_b", "actors": [{"id": "ai", "name": "AI", "type": "ai"}],
         "steps": [{"id": "sb", "name": "Score lead", "actor_id": "ai", "task_type": "business_rule"}]},
    ]
    first = _promote_call_activities(matrix)
    second = _promote_call_activities(matrix)
    assert first == 1
    assert second == 0, "second pass should not re-promote"
    assert len(matrix[1]["call_activities"]) == 1


def test_coincidentally_named_steps_not_promoted_if_not_ai():
    """Don't promote 'Approve invoice' that's a userTask, even if it appears twice."""
    matrix = [
        {"workflow_id": "wf_a", "actors": [{"id": "h", "name": "Mgr", "type": "human"}],
         "steps": [{"id": "x1", "name": "Approve invoice", "actor_id": "h", "task_type": "user"}]},
        {"workflow_id": "wf_b", "actors": [{"id": "h", "name": "Mgr", "type": "human"}],
         "steps": [{"id": "x2", "name": "Approve invoice", "actor_id": "h", "task_type": "user"}]},
    ]
    assert _promote_call_activities(matrix) == 0
    assert not matrix[0].get("call_activities")
    assert not matrix[1].get("call_activities")
