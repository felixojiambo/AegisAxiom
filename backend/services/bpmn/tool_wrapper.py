"""LLM-mediated wrapper for the BPMN generation pipeline.

Called as a tool by Vega and Orion.  If the incoming data is already
structured (has all required keys), it goes straight to the deterministic
pipeline.  If partially structured, the LLM-mediation path will complete
the structure first (not yet implemented in v1).
"""

import logging
import uuid

from services.bpmn.pipeline import generate_bpmn_from_json

logger = logging.getLogger(__name__)

# Required top-level keys for structured workflow JSON
_REQUIRED_KEYS = {"workflow_id", "workflow_name", "actors", "trigger", "steps"}


def _looks_structured(data: dict) -> bool:
    """Heuristic: does the dict have all required keys for the pipeline?"""
    return _REQUIRED_KEYS.issubset(data.keys())


async def generate_workflow_diagram(workflow_data: dict, db) -> dict:
    """Generate a BPMN diagram from workflow data.

    If data is already structured, pass directly to pipeline.
    If partially structured, use LLM to complete the structure first
    (v2 — currently returns an error for unstructured input).

    Returns:
        {
            "success": bool,
            "bpmn_id": str | None,
            "bpmn_xml": str | None,
            "validation_warnings": list,
            "error": str | None,
        }
    """
    if not _looks_structured(workflow_data):
        missing = _REQUIRED_KEYS - set(workflow_data.keys())
        return {
            "success": False,
            "bpmn_id": None,
            "bpmn_xml": None,
            "validation_warnings": [],
            "error": (
                f"Workflow data is missing required keys: {', '.join(sorted(missing))}. "
                "LLM-mediated structuring is not yet implemented. "
                "Please provide fully structured workflow JSON."
            ),
        }

    # Run the deterministic pipeline
    result = await generate_bpmn_from_json(workflow_data)

    if not result["success"]:
        return {
            "success": False,
            "bpmn_id": None,
            "bpmn_xml": None,
            "validation_warnings": result.get("validation_warnings", []),
            "error": result.get("error"),
        }

    # Return the generated diagram data without persisting.
    # Persistence is handled by the caller (conversational_tools.py)
    # to avoid duplicate inserts.
    bpmn_id = f"bpmn_{uuid.uuid4().hex[:12]}"

    return {
        "success": True,
        "bpmn_id": bpmn_id,
        "bpmn_xml": result["bpmn_xml"],
        "semantic_xml": result.get("semantic_xml"),
        "validation_warnings": result.get("validation_warnings", []),
        "error": None,
    }
