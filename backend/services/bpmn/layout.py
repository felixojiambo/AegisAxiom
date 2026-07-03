import asyncio
import os
import logging

logger = logging.getLogger(__name__)

_NODE_HELPER_DIR = os.path.join(os.path.dirname(__file__), "node_helpers")


class BpmnLayoutError(Exception):
    pass


async def apply_auto_layout(semantic_xml: str) -> str:
    """Run ELK.js layout on semantic BPMN XML via Node subprocess.
    Returns laid-out XML with BPMNDiagram element (horizontal left-to-right)."""
    layout_script = os.path.join(_NODE_HELPER_DIR, "layout.js")
    proc = await asyncio.create_subprocess_exec(
        "node", layout_script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(semantic_xml.encode()), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        raise BpmnLayoutError("ELK.js layout timed out after 30s")
    if proc.returncode != 0:
        error_msg = stderr.decode().strip() if stderr else "Unknown layout error"
        raise BpmnLayoutError(error_msg)
    return stdout.decode()
