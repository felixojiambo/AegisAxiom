"""BPMN diagram endpoints — fetch, edit, and export generated diagrams."""

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import Response
from datetime import datetime, timezone
import io
import logging
import re
import zipfile

from database import get_db
from auth_helper import get_current_user, has_perm

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/export-all")
async def export_all_bpmn_diagrams(
    session_id: str = Query(..., description="Vega session ID"),
    current_user=Depends(get_current_user),
):
    """Download all BPMN diagrams for an Vega session as a ZIP file."""
    if not has_perm(current_user, "bpmn.use"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()

    # Get the Vega session to find workflow bpmn_ids
    session = await db.vega_discovery_sessions.find_one(
        {"session_id": session_id},
        {"_id": 0, "ai_employee_matrix": 1, "user_id": 1},
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("user_id") != current_user.user_id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="You do not own this session")

    matrix = session.get("ai_employee_matrix", [])
    bpmn_ids = [wf.get("bpmn_id") for wf in matrix if wf.get("bpmn_id")]

    if not bpmn_ids:
        raise HTTPException(status_code=404, detail="No BPMN diagrams found for this session")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for bpmn_id in bpmn_ids:
            diagram = await db.bpmn_diagrams.find_one(
                {"bpmn_id": bpmn_id},
                {"_id": 0, "bpmn_xml": 1, "edited_xml": 1, "workflow_name": 1},
            )
            if not diagram:
                continue
            xml = diagram.get("edited_xml") or diagram.get("bpmn_xml")
            if not xml:
                continue
            wf_name = diagram.get("workflow_name", "diagram").replace(" ", "_")
            filename = f"{wf_name}_{bpmn_id}.bpmn"
            zf.writestr(filename, xml)

    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="bpmn_diagrams_{session_id}.zip"'},
    )


@router.get("/{bpmn_id}")
async def get_bpmn_diagram(bpmn_id: str, current_user=Depends(get_current_user)):
    """Fetch full BPMN XML for rendering.

    Returns the edited_xml if the user has made edits, otherwise the
    generated_xml. Checks that the user owns the session that produced
    this diagram (via created_by) or is an admin.
    """
    if not has_perm(current_user, "bpmn.use"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    diagram = await db.bpmn_diagrams.find_one(
        {"bpmn_id": bpmn_id},
        {"_id": 0},
    )
    if not diagram:
        raise HTTPException(status_code=404, detail="BPMN diagram not found")

    # Ownership check: created_by must match or user is admin
    created_by = diagram.get("created_by")
    if created_by is not None and created_by != current_user.user_id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="You do not have access to this diagram")

    # Return edited version if it exists, otherwise generated
    xml = diagram.get("edited_xml") or diagram.get("bpmn_xml")

    return {
        "bpmn_id": diagram.get("bpmn_id"),
        "workflow_id": diagram.get("workflow_id"),
        "workflow_name": diagram.get("workflow_name"),
        "bpmn_xml": xml,
        "has_edits": diagram.get("edited_xml") is not None,
        "validation_warnings": diagram.get("validation_warnings", []),
        "created_at": diagram.get("created_at"),
        "updated_at": diagram.get("updated_at"),
    }


@router.patch("/{bpmn_id}")
async def update_bpmn_diagram(bpmn_id: str, body: dict, current_user=Depends(get_current_user)):
    """Save user edits to a BPMN diagram.

    Expects: {"edited_xml": "<bpmn:definitions ...>...</bpmn:definitions>"}
    """
    if not has_perm(current_user, "bpmn.use"):
        raise HTTPException(status_code=403, detail="Permission denied")

    edited_xml = body.get("edited_xml")
    if not edited_xml or not isinstance(edited_xml, str):
        raise HTTPException(status_code=400, detail="edited_xml is required and must be a string")

    if len(edited_xml.encode('utf-8')) > 2_000_000:
        raise HTTPException(status_code=400, detail="edited_xml exceeds 2MB size limit")

    # Basic sanity check: must look like BPMN XML
    if "<bpmn:definitions" not in edited_xml and "<definitions" not in edited_xml:
        raise HTTPException(status_code=400, detail="edited_xml does not appear to be valid BPMN XML")

    db = get_db()
    diagram = await db.bpmn_diagrams.find_one(
        {"bpmn_id": bpmn_id},
        {"_id": 0, "created_by": 1},
    )
    if not diagram:
        raise HTTPException(status_code=404, detail="BPMN diagram not found")

    # Ownership check
    created_by = diagram.get("created_by")
    if created_by is not None and created_by != current_user.user_id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="You do not have access to this diagram")

    result = await db.bpmn_diagrams.update_one(
        {"bpmn_id": bpmn_id},
        {
            "$set": {
                "edited_xml": edited_xml,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="BPMN diagram not found")

    return {"success": True, "bpmn_id": bpmn_id}


@router.get("/{bpmn_id}/export")
async def export_bpmn_diagram(
    bpmn_id: str,
    format: str = Query("xml", pattern="^(xml|pdf)$"),
    current_user=Depends(get_current_user),
):
    """Download BPMN diagram as a file.

    Supports format=xml (returns .bpmn file).
    format=pdf is stubbed for v1 and returns 501.
    """
    if not has_perm(current_user, "bpmn.use"):
        raise HTTPException(status_code=403, detail="Permission denied")

    if format == "pdf":
        raise HTTPException(
            status_code=501,
            detail="PDF export is not yet implemented. Use format=xml.",
        )

    db = get_db()
    diagram = await db.bpmn_diagrams.find_one(
        {"bpmn_id": bpmn_id},
        {"_id": 0},
    )
    if not diagram:
        raise HTTPException(status_code=404, detail="BPMN diagram not found")

    # Ownership check
    created_by = diagram.get("created_by")
    if created_by is not None and created_by != current_user.user_id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="You do not have access to this diagram")

    xml = diagram.get("edited_xml") or diagram.get("bpmn_xml")
    if not xml:
        raise HTTPException(status_code=404, detail="No BPMN XML available for this diagram")

    # Build a safe filename
    wf_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', diagram.get("workflow_name", "diagram"))
    filename = f"{wf_name}_{bpmn_id}.bpmn"

    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
