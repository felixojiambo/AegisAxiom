"""Orion SOW Builder — REST endpoints.

Provides endpoints to view, approve, edit, and finalize
Orion-generated SOW documents for opportunities.
All endpoints are RBAC-gated by the ``orion.use`` permission.
"""

import logging
import re
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from database import get_db
from auth_helper import get_current_user, has_perm

router = APIRouter()
logger = logging.getLogger(__name__)


# ── SOW Adapter Helpers ──
# The Google Docs service expects a flat SOW dict with top-level fields
# (executive_summary, smart_specific, milestones, etc.) but Orion stores
# content under sow["sections"][section_id]["content"] as markdown strings.
# These helpers parse the markdown back into the structured format.


def _extract_smart_field(smart_content: str, field_name: str) -> str:
    """Extract a SMART field from markdown content with ### headers."""
    if not smart_content:
        return ""
    pattern = rf'###\s*{re.escape(field_name)}\s*\n\n(.*?)(?=###|\Z)'
    match = re.search(pattern, smart_content, re.DOTALL)
    return match.group(1).strip() if match else ""


VALID_SECTION_IDS = frozenset({
    "executive_summary", "smart", "milestones",
    "assumptions_risks", "billing", "signature",
})


def _parse_milestones_to_structured(milestones_content: str) -> list:
    """Convert milestones markdown to the structured format Google Docs expects.

    Handles the new Detailed SOW format which has a Scope of Services section
    followed by an Implementation Plan section separated by ``---``.
    Only the Implementation Plan portion is parsed for milestones.

    Returns list of {milestone_name, bullets: [{bullet_text, deliverable, estimated_hours}]}
    """
    if not milestones_content:
        return []

    # Only parse the Implementation Plan section, skip Scope of Services
    impl_marker = "## Implementation Plan"
    impl_idx = milestones_content.find(impl_marker)
    if impl_idx >= 0:
        milestones_content = milestones_content[impl_idx + len(impl_marker):]
    elif "---" in milestones_content:
        milestones_content = milestones_content.split("---", 1)[1]

    milestones = []
    current_milestone = None
    for line in milestones_content.split('\n'):
        line = line.strip()
        if not line or line.startswith('## ') or line.startswith('**Target'):
            continue
        if line.startswith('### ') or (line.startswith('**') and line.endswith('**')):
            if current_milestone:
                milestones.append(current_milestone)
            name = line.lstrip('#').strip().strip('*').strip()
            current_milestone = {"milestone_name": name, "bullets": []}
        elif line.startswith('- ') and current_milestone:
            text = line[2:].strip()
            deliverable = ""
            estimated_hours = 0
            if '*Deliverable:' in text:
                parts = text.split('*Deliverable:', 1)
                text = parts[0].strip()
                deliverable = parts[1].strip().rstrip('*')
            # Extract hours from (Xh) suffix
            hours_match = re.search(r'\((\d+)h\)', text)
            if hours_match:
                estimated_hours = int(hours_match.group(1))
                text = re.sub(r'\s*\(\d+h\)\s*$', '', text).strip()
            # Also strip [Fill Hours] placeholder
            text = re.sub(r'\s*\[Fill Hours\]\s*$', '', text).strip()
            current_milestone["bullets"].append({
                "bullet_text": text,
                "deliverable": deliverable,
                "estimated_hours": estimated_hours,
            })
    if current_milestone:
        milestones.append(current_milestone)

    # If no structured milestones found, create one catch-all
    if not milestones and milestones_content.strip():
        milestones = [{"milestone_name": "Project Implementation", "bullets": [
            {"bullet_text": line.strip().lstrip('0123456789.-) '), "deliverable": "", "estimated_hours": 0}
            for line in milestones_content.split('\n') if line.strip() and not line.strip().startswith('**')
        ]}]
    return milestones


def _extract_billing_field(billing_content: str, field_name: str) -> str:
    """Extract a billing field from markdown content."""
    if not billing_content:
        return ""
    pattern = rf'###?\s*{re.escape(field_name)}\s*\n\n?(.*?)(?=###|\Z)'
    match = re.search(pattern, billing_content, re.DOTALL)
    return match.group(1).strip() if match else ""


def _build_flat_sow(sow: dict) -> dict:
    """Convert a sections-based SOW document into the flat format expected
    by ``create_sow_google_doc_user`` / ``_build_doc_requests``.

    Reads each section's content (markdown strings) and parses them into
    the flat fields the Google Docs service expects.
    """
    sections = sow.get("sections", {})

    smart_content = sections.get("smart", {}).get("content", "")
    exec_summary = sections.get("executive_summary", {}).get("content", "")
    smart_specific = _extract_smart_field(smart_content, "Specific")
    smart_measurable = _extract_smart_field(smart_content, "Measurable")
    smart_achievable = _extract_smart_field(smart_content, "Achievable")
    smart_relevant = _extract_smart_field(smart_content, "Relevant")
    smart_time_bound = _extract_smart_field(smart_content, "Time-Bound")

    # Parse milestones from the "Implementation Plan" portion of the detailed SOW
    milestones_content = sections.get("milestones", {}).get("content", "")
    milestones = _parse_milestones_to_structured(milestones_content)

    billing_content = sections.get("billing", {}).get("content", "")

    return {
        "version": sow.get("version", 1),
        "executive_summary": exec_summary,
        "smart_specific": smart_specific,
        "smart_measurable": smart_measurable,
        "smart_achievable": smart_achievable,
        "smart_relevant": smart_relevant,
        "smart_time_bound": smart_time_bound,
        "milestones": milestones,
        "billing_account_email": "",
        "billing_account_phone": "",
        "payment_terms": _extract_billing_field(billing_content, "Payment Terms"),
        "billing_terms": _extract_billing_field(billing_content, "Billing Terms"),
        "special_billing_instructions": _extract_billing_field(
            billing_content, "Special Instructions"
        ),
        "boa_authorized_users": [],
    }


def _build_flat_opportunity(vega_session: dict | None, sow: dict | None = None) -> dict:
    """Build the flat opportunity dict expected by the Google Docs service.

    Parameters
    ----------
    vega_session : dict | None
        The Vega discovery session document.
    sow : dict | None
        The SOW document (sections-based).  Used as a fallback source for
        the client name when the Vega session lacks one — the executive
        summary or signature content often mentions the company.
    """
    if not vega_session and not sow:
        return {"account_name": "Client"}

    name = None

    # Try Vega session direct fields first
    if vega_session:
        name = vega_session.get("client_company") or vega_session.get("opportunity_name")

        # Fall back to extracting from Call 1 analysis summary
        if not name:
            calls = vega_session.get("calls", {})
            for call_key in ("1", "2", "3"):
                analysis = calls.get(call_key, {}).get("analysis", {})
                if isinstance(analysis, dict):
                    # Check for client_name field in analysis
                    name = analysis.get("client_name") or analysis.get("client_company")
                    if name:
                        break
                    # Try to extract from summary (e.g., "...of Pinecrest Property Management, ...")
                    summary = analysis.get("summary", "")
                    if " of " in summary and "," in summary:
                        # Extract company name between "of" and first comma
                        try:
                            after_of = summary.split(" of ", 1)[1]
                            name = after_of.split(",")[0].strip()
                            if len(name) > 3:
                                break
                            name = None
                        except (IndexError, ValueError):
                            pass

    # Fall back to SOW content for client name
    if not name and sow:
        sections = sow.get("sections", {})
        exec_content = sections.get("executive_summary", {}).get("content", "")
        # Try specific company name patterns
        for pattern in [
            r'(?:for|with)\s+([A-Z][A-Za-z0-9 &.,]+?)(?:\.|,|\n|$)',
            r'(?:for|with)\s+(a\s+[\w\s]+?(?:organization|company|firm|group|LLC|Inc|Corp))',
        ]:
            match = re.search(pattern, exec_content, re.IGNORECASE)
            if match:
                candidate = match.group(1).strip().rstrip('.,')
                if len(candidate) > 3:
                    name = candidate
                    break

    # Build opportunity name from session or fallback
    opp_name = None
    if vega_session:
        opp_name = (
            vega_session.get("opportunity_name")
            or vega_session.get("client_company")
            or name
        )

    return {
        "account_name": name or "Client",
        "opportunity_name": opp_name or name or "SOW Engagement",
    }


# ── Request Models ──


class SectionEditRequest(BaseModel):
    content: str


# ── Endpoints ──


@router.get("/sows/{opportunity_id}")
async def get_orion_sow(
    opportunity_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get the current Orion-generated SOW for an opportunity."""
    if not has_perm(current_user, "orion.use"):
        raise HTTPException(status_code=403, detail="Permission denied: orion.use required")

    sow = await db.opportunity_sows.find_one(
        {
            "opportunity_id": opportunity_id,
            "created_by_agent": "ai_orion",
        },
        {"_id": 0},
    )
    if not sow:
        raise HTTPException(status_code=404, detail="No Orion SOW found for this opportunity")

    return sow


@router.post("/sows/{opportunity_id}/sections/{section_id}/approve")
async def approve_sow_section(
    opportunity_id: str,
    section_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Approve a section of the Orion-generated SOW."""
    if section_id not in VALID_SECTION_IDS:
        raise HTTPException(status_code=400, detail="Invalid section ID")
    if not has_perm(current_user, "orion.use"):
        raise HTTPException(status_code=403, detail="Permission denied: orion.use required")

    # Verify user owns the SOW (created_by must match current user)
    sow = await db.opportunity_sows.find_one(
        {
            "opportunity_id": opportunity_id,
            "created_by_agent": "ai_orion",
        },
        {"_id": 0, "created_by": 1},
    )
    if not sow:
        raise HTTPException(status_code=404, detail="No Orion SOW found for this opportunity")
    if sow.get("created_by") != current_user.user_id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="You do not have access to this SOW")

    result = await db.opportunity_sows.update_one(
        {
            "opportunity_id": opportunity_id,
            "created_by_agent": "ai_orion",
        },
        {
            "$set": {
                f"sections.{section_id}.approved": True,
                f"sections.{section_id}.status": "approved",
                f"sections.{section_id}.approved_by": current_user.user_id,
                f"sections.{section_id}.approved_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="No Orion SOW found for this opportunity")

    return {"status": "approved", "section": section_id}


@router.post("/sows/{opportunity_id}/sections/{section_id}/edit")
async def edit_sow_section(
    opportunity_id: str,
    section_id: str,
    body: SectionEditRequest,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Save user edits to a section of the Orion-generated SOW."""
    if section_id not in VALID_SECTION_IDS:
        raise HTTPException(status_code=400, detail="Invalid section ID")
    if not has_perm(current_user, "orion.use"):
        raise HTTPException(status_code=403, detail="Permission denied: orion.use required")

    # Verify user owns the SOW (created_by must match current user)
    sow = await db.opportunity_sows.find_one(
        {
            "opportunity_id": opportunity_id,
            "created_by_agent": "ai_orion",
        },
        {"_id": 0, "created_by": 1},
    )
    if not sow:
        raise HTTPException(status_code=404, detail="No Orion SOW found for this opportunity")
    if sow.get("created_by") != current_user.user_id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="You do not have access to this SOW")

    result = await db.opportunity_sows.update_one(
        {
            "opportunity_id": opportunity_id,
            "created_by_agent": "ai_orion",
        },
        {
            "$set": {
                f"sections.{section_id}.content": body.content,
                f"sections.{section_id}.edited_by": current_user.user_id,
                f"sections.{section_id}.edited_at": datetime.now(timezone.utc).isoformat(),
                f"sections.{section_id}.approved": False,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="No Orion SOW found for this opportunity")

    return {"status": "updated", "section": section_id}


@router.post("/sows/{opportunity_id}/generate-all")
async def generate_all_sections(
    opportunity_id: str,
    data: dict,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Generate all SOW sections at once for the given opportunity."""
    if not has_perm(current_user, "orion.use"):
        raise HTTPException(status_code=403, detail="Permission denied: orion.use required")

    vega_session_id = data.get("vega_session_id")
    if not vega_session_id:
        raise HTTPException(status_code=400, detail="vega_session_id required")

    from services.orion.tools import handle_generate_sow_section, handle_update_sow_draft

    section_order = ["executive_summary", "smart", "milestones", "assumptions_risks", "billing", "signature"]
    results = {}
    smart_cache = None

    for section in section_order:
        try:
            gen_args = {"section": section, "vega_session_id": vega_session_id}
            if section == "smart" and smart_cache:
                gen_args["prior_smart_result"] = smart_cache
            gen_result = await handle_generate_sow_section(
                gen_args,
                db, current_user, session_id=None, employee_id="ai_orion",
            )
            if section == "executive_summary" and gen_result.get("parsed_json"):
                smart_cache = gen_result["parsed_json"]
            if gen_result.get("error"):
                results[section] = {"status": "failed", "error": gen_result["error"]}
                continue

            content = gen_result.get("content", "")
            await handle_update_sow_draft(
                {"opportunity_id": opportunity_id, "section": section, "content": content},
                db, current_user, session_id=None, employee_id="ai_orion",
            )
            results[section] = {"status": "generated"}
        except Exception as e:
            results[section] = {"status": "failed", "error": str(e)}

    return {"opportunity_id": opportunity_id, "sections": results}


@router.post("/sows/{opportunity_id}/finalize")
async def finalize_sow(
    opportunity_id: str,
    force: bool = False,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Trigger Google Doc creation for a finalized SOW.

    Duplicate prevention. When the SOW already carries a non-empty
    google_doc_url, the route short-circuits with a no-op response
    carrying duplicate_prevented=True. Pass force=true on the query
    string to bypass. See
    docs/sales-ops/GOOGLE_DOC_ARTIFACT_PATH.md.
    """
    if not has_perm(current_user, "orion.use"):
        raise HTTPException(status_code=403, detail="Permission denied")

    sow = await db.opportunity_sows.find_one(
        {"opportunity_id": opportunity_id, "created_by_agent": "ai_orion"},
        {"_id": 0},
    )
    if not sow:
        raise HTTPException(status_code=404, detail="SOW not found")

    # Duplicate prevention. Short-circuit without any Drive call
    # when the SOW already has a Google Doc URL. Same envelope as
    # the fireflies path so the frontend handles both identically.
    existing_doc_url = (sow.get("google_doc_url") or "").strip()
    if existing_doc_url and not force:
        try:
            from services.audit_service import log_action as _audit_log_action
            await _audit_log_action(
                action="sales_ops.sow.google_doc_export_noop",
                actor_id=getattr(current_user, "user_id", None),
                actor_email=getattr(current_user, "email", "unknown"),
                resource_type="opportunity_sow",
                resource_id=sow.get("sow_id"),
                details={
                    "opportunity_id": opportunity_id,
                    "created_by_agent": "ai_orion",
                    "reason": "duplicate_prevented",
                },
            )
        except Exception as _audit_err:  # noqa: BLE001
            logger.warning(
                "Audit row for orion finalize no-op failed: %s", _audit_err
            )
        return {
            "status": sow.get("status") or "ready_for_review",
            "google_doc_url": existing_doc_url,
            "google_doc_id": sow.get("google_doc_id"),
            "duplicate_prevented": True,
            "no_op": True,
        }

    from services.google_docs_service import (
        GoogleDocsFolderNotConfigured,
        GoogleDocsFolderNotFound,
        GoogleDocsPermissionDenied,
        create_sow_google_doc_user,
        is_sow_folder_configured,
    )

    if not is_sow_folder_configured():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "google_docs_folder_not_configured",
                "message": (
                    "Google Docs export is not configured. Ask an admin to "
                    "set SIMPLIMETA_SOW_FOLDER_ID and verify Drive folder "
                    "access."
                ),
                "next_step": (
                    "Paste an existing Google Doc URL on the proposal as "
                    "the escape hatch."
                ),
            },
        )

    vega_session = await db.vega_discovery_sessions.find_one(
        {"session_id": opportunity_id}, {"_id": 0}
    )

    flat_sow = _build_flat_sow(sow)
    flat_opp = _build_flat_opportunity(vega_session, sow)

    sections = sow.get("sections", {})
    assumptions_risks = sections.get("assumptions_risks", {}).get("content", "")

    try:
        doc_result = await create_sow_google_doc_user(
            db,
            user_id=current_user.user_id,
            sow=flat_sow,
            opportunity=flat_opp,
            assumptions_risks=assumptions_risks,
        )
    except GoogleDocsFolderNotConfigured:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "google_docs_folder_not_configured",
                "message": (
                    "Google Docs export is not configured. Ask an admin to "
                    "set SIMPLIMETA_SOW_FOLDER_ID and verify Drive folder "
                    "access."
                ),
                "next_step": (
                    "Paste an existing Google Doc URL on the proposal as "
                    "the escape hatch."
                ),
            },
        )
    except GoogleDocsPermissionDenied as e:
        logger.error("Drive permission denied on orion finalize: %s", e)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "google_docs_permission_denied",
                "message": (
                    "AEGISAXIOM does not have access to the configured Drive "
                    "folder. Ask an admin to share it."
                ),
            },
        )
    except GoogleDocsFolderNotFound as e:
        logger.error("Drive folder not found on orion finalize: %s", e)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "google_docs_folder_not_found",
                "message": (
                    "Google Docs export failed. The configured Drive folder "
                    "was not found."
                ),
            },
        )
    except FileNotFoundError as e:
        # Missing service account file or missing Google OAuth tokens
        # for the user. Returns a structured error so the frontend
        # can offer the external Google Doc attach as an escape hatch
        # instead of a generic crash. Raw exception text can include
        # a file system path so it is logged server side only.
        logger.error(
            "Google Docs not connected for user %s on orion finalize: %s",
            current_user.user_id,
            e,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "code": "google_docs_not_configured",
                "message": (
                    "Google Docs export is not configured for this "
                    "deployment or this user has not connected Google. "
                    "Paste a Google Doc URL on the proposal as the "
                    "escape hatch."
                ),
            },
        )
    except Exception as e:
        # Catch all hides raw exception text from clients. Server
        # side log keeps the detail for debugging. Mirrors the
        # fireflies /sow/finalize envelope so frontend callers can
        # branch on a stable code field regardless of which finalize
        # route they hit. Audit row records the failure even when no
        # doc was written.
        logger.error("Orion finalize failed to create Google Doc: %s", e)
        try:
            from services.audit_service import log_action as _audit_log_action
            await _audit_log_action(
                action="sales_ops.sow.google_doc_export_failed",
                actor_id=getattr(current_user, "user_id", None),
                actor_email=getattr(current_user, "email", "unknown"),
                resource_type="opportunity_sow",
                resource_id=sow.get("sow_id"),
                details={
                    "opportunity_id": opportunity_id,
                    "created_by_agent": "ai_orion",
                },
            )
        except Exception as _audit_err:  # noqa: BLE001
            logger.warning(
                "Audit row for orion finalize failure failed: %s",
                _audit_err,
            )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "google_docs_export_failed",
                "message": (
                    "Google Docs export failed. Try again or paste a "
                    "Google Doc URL on the proposal as the escape "
                    "hatch."
                ),
            },
        )

    doc_url = doc_result.get("doc_url") or doc_result.get("url")
    doc_id = doc_result.get("doc_id") or doc_result.get("id")

    # Defensive guard. If the service returned an empty doc_url the
    # downstream update_one would skip silently and the response
    # would advertise success with no artifact. Treat that as the
    # same failure mode as a Drive API error so the response shape
    # never claims a doc that does not exist.
    if not doc_url:
        logger.error(
            "Orion finalize completed without a doc_url for %s",
            opportunity_id,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "google_docs_export_failed",
                "message": (
                    "Google Docs export failed. Try again or paste a "
                    "Google Doc URL on the proposal as the escape "
                    "hatch."
                ),
            },
        )

    # Shared substrate. Both finalize paths write the same SOW row in
    # db.opportunity_sows. The canonical field names are google_doc_url
    # and google_doc_id; the canonical post-finalize status value is
    # ready_for_review. The non-status string "finalized" is not part
    # of the SOW status enum and would not display in the frontend
    # SOWStatusBar. See docs/sales-ops/SHARED_SOW_BUILDER_SUBSTRATE.md.
    if doc_url:
        now = datetime.now(timezone.utc)
        await db.opportunity_sows.update_one(
            {"opportunity_id": opportunity_id, "created_by_agent": "ai_orion"},
            {"$set": {
                "google_doc_url": doc_url,
                "google_doc_id": doc_id,
                "status": "ready_for_review",
                "exported_at": now,
                "updated_at": now,
                "last_edited_by": current_user.user_id,
            }},
        )

    # Audit. A successful structured export from the orion path
    # writes the same audit action as the fireflies path so the
    # activity feed shows one canonical event per SOW export.
    try:
        from services.audit_service import log_action as _audit_log_action
        await _audit_log_action(
            action="sales_ops.sow.google_doc_exported",
            actor_id=getattr(current_user, "user_id", None),
            actor_email=getattr(current_user, "email", "unknown"),
            resource_type="opportunity_sow",
            resource_id=sow.get("sow_id"),
            details={
                "opportunity_id": opportunity_id,
                "created_by_agent": "ai_orion",
                "forced": bool(force),
                "has_google_doc_id": bool(doc_id),
            },
        )
    except Exception as _audit_err:  # noqa: BLE001
        logger.warning(
            "Audit row for orion finalize success failed: %s", _audit_err
        )

    return {
        "status": "ready_for_review",
        "google_doc_url": doc_url,
        "google_doc_id": doc_id,
        "duplicate_prevented": False,
        "no_op": False,
    }
