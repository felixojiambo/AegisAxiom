import asyncio
import json
import os
import logging

logger = logging.getLogger(__name__)

_NODE_HELPER_DIR = os.path.join(os.path.dirname(__file__), "node_helpers")


async def validate_bpmn(bpmn_xml: str) -> dict:
    """Validate BPMN XML by importing into bpmn-js Viewer.
    Returns {"success": bool, "warnings": list, "error": str|None}."""
    validate_script = os.path.join(_NODE_HELPER_DIR, "validate.js")
    proc = await asyncio.create_subprocess_exec(
        "node", validate_script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(bpmn_xml.encode()), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        return {"success": False, "warnings": [], "error": "Validation timed out after 30s"}
    if proc.returncode != 0:
        return {"success": False, "warnings": [], "error": stderr.decode().strip()}
    try:
        return json.loads(stdout.decode())
    except json.JSONDecodeError:
        return {"success": False, "warnings": [], "error": "Invalid validation response"}
