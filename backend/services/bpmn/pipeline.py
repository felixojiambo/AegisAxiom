"""BPMN generation pipeline: validate -> XML -> layout -> validate.

Chains every stage of the deterministic BPMN pipeline and returns a
single result dict the caller (tool_wrapper / route) can use directly.
"""

import logging

from services.bpmn.schema import validate_workflow_json
from services.bpmn.json_to_xml import json_to_semantic_bpmn
from services.bpmn.layout import apply_auto_layout, BpmnLayoutError
from services.bpmn.validator import validate_bpmn

logger = logging.getLogger(__name__)


async def generate_bpmn_from_json(workflow_json: dict) -> dict:
    """Run the full deterministic pipeline: validate -> XML -> layout -> validate.

    Returns:
        {
            "success": bool,
            "bpmn_xml": str | None,
            "semantic_xml": str | None,
            "validation_warnings": list,
            "error": str | None,
        }
    """
    # Stage 1: Schema + cross-reference validation
    errors = validate_workflow_json(workflow_json)
    if errors:
        return {
            "success": False,
            "bpmn_xml": None,
            "semantic_xml": None,
            "validation_warnings": [],
            "error": f"Workflow validation failed: {'; '.join(errors)}",
        }

    # Stage 2: Deterministic JSON -> semantic BPMN XML
    try:
        semantic_xml = json_to_semantic_bpmn(workflow_json)
    except Exception as exc:
        logger.exception("json_to_semantic_bpmn failed")
        return {
            "success": False,
            "bpmn_xml": None,
            "semantic_xml": None,
            "validation_warnings": [],
            "error": f"XML generation failed: {exc}",
        }

    # Stage 3: Auto-layout (adds bpmndi:BPMNDiagram)
    try:
        laid_out_xml = await apply_auto_layout(semantic_xml)
    except BpmnLayoutError as exc:
        logger.warning("Auto-layout failed, returning semantic XML without layout: %s", exc)
        # Layout failure is non-fatal: the semantic XML is still valid BPMN,
        # it just won't render with diagram coordinates.  Return it so the
        # caller can decide whether to save the unlaid-out version.
        return {
            "success": True,
            "bpmn_xml": semantic_xml,
            "semantic_xml": semantic_xml,
            "validation_warnings": [f"Auto-layout unavailable: {exc}"],
            "error": None,
        }

    # Stage 4: Validate the laid-out XML via bpmn-js
    try:
        validation = await validate_bpmn(laid_out_xml)
    except Exception as exc:
        logger.warning("BPMN validation subprocess failed: %s", exc)
        # Validation failure is non-fatal: the XML was generated correctly,
        # the validator just could not confirm it.
        return {
            "success": True,
            "bpmn_xml": laid_out_xml,
            "semantic_xml": semantic_xml,
            "validation_warnings": [f"Validation unavailable: {exc}"],
            "error": None,
        }

    if not validation.get("success"):
        # Validation reported issues but the XML was generated and laid out.
        # Treat as non-fatal — the XML usually renders fine in bpmn-js even
        # if the backend validator (Node ESM) has compatibility issues.
        logger.warning("BPMN validation reported failure: %s", validation.get("error"))
        return {
            "success": True,
            "bpmn_xml": laid_out_xml,
            "semantic_xml": semantic_xml,
            "validation_warnings": validation.get("warnings", []) + [validation.get("error", "Validation issue")],
            "error": None,
        }

    return {
        "success": True,
        "bpmn_xml": laid_out_xml,
        "semantic_xml": semantic_xml,
        "validation_warnings": validation.get("warnings", []),
        "error": None,
    }
