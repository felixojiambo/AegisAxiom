"""Vega discovery session endpoints — list, detail, and call approval."""
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import Response
from datetime import datetime, timezone
from typing import Optional
import logging
import uuid

from database import get_db
from auth_helper import get_current_user, has_perm
from services.security_logger import log_security_event
from services.vega_events import emit_vega_event

logger = logging.getLogger(__name__)
router = APIRouter()


async def _resolve_vega_session(db, session_id: str, user_id: str):
    """Look up an Vega discovery session by `session_id`, with fallback for legacy
    artifacts whose `payload.session_id` was missing.

    The frontend computes a placeholder `vega_<chat_session_id>` when payload
    fields are missing (e.g. Call 2 matrix artifacts created before the
    payload.session_id field was added). Detect that prefix and resolve via the
    Vega session's `chat_session_id` link instead. Returns None if no session
    matches under either lookup.
    """
    session = await db.vega_discovery_sessions.find_one(
        {"session_id": session_id, "user_id": user_id}, {"_id": 0}
    )
    if not session and session_id.startswith("vega_chat_"):
        chat_sid = session_id[len("vega_"):]
        session = await db.vega_discovery_sessions.find_one(
            {"chat_session_id": chat_sid, "user_id": user_id},
            {"_id": 0},
            sort=[("updated_at", -1)],
        )
    return session


@router.get("/vega-sessions")
async def list_vega_sessions(current_user=Depends(get_current_user)):
    """List all Vega discovery sessions for the current user."""
    if not has_perm(current_user, "vega_sessions.view_own"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    cursor = db.vega_discovery_sessions.find(
        {"user_id": current_user.user_id},
        {"_id": 0},
    ).sort("updated_at", -1)

    sessions = await cursor.to_list(length=200)
    return {"sessions": sessions, "total": len(sessions)}


@router.get("/vega-sessions/{session_id}")
async def get_vega_session(session_id: str, current_user=Depends(get_current_user)):
    """Get a single Vega discovery session by ID."""
    if not has_perm(current_user, "vega_sessions.view_own"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    session = await _resolve_vega_session(db, session_id, current_user.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.get("user_id") != current_user.user_id:
        raise HTTPException(status_code=403, detail="You do not own this session")

    return session


@router.post("/vega-sessions/{session_id}/calls/{call_number}/approve")
async def approve_call_analysis(
    session_id: str,
    call_number: int,
    data: dict,
    current_user=Depends(get_current_user),
):
    """Approve (and optionally edit) a call analysis for a given call number.

    Accepts an optional `edits` dict in the request body. Sets
    `calls[call_number].user_approved = True` and advances
    `current_call_number`.
    """
    if not has_perm(current_user, "vega.use"):
        raise HTTPException(status_code=403, detail="Permission denied")

    if call_number not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="call_number must be 1, 2, or 3")

    db = get_db()
    # Upsert: create the Vega session if it doesn't exist yet (the LLM may not
    # have called save_vega_session before the user approves the analysis).
    # Scoped to current user — without this, an attacker could supply another
    # user's session_id, hit the not-found branch, and either squat the ID
    # (DoS) or pollute analytics.
    session = await db.vega_discovery_sessions.find_one(
        {"session_id": session_id, "user_id": current_user.user_id},
        {"_id": 0},
    )
    # Defense-in-depth: if any session_id collision exists across users,
    # surface a 409 instead of overwriting it during the auto-create path.
    if not session:
        collision = await db.vega_discovery_sessions.find_one(
            {"session_id": session_id}, {"_id": 0, "user_id": 1}
        )
        if collision and collision.get("user_id") != current_user.user_id:
            raise HTTPException(status_code=409, detail="Session id already in use")

    now = datetime.now(timezone.utc).isoformat()
    call_key = str(call_number)
    edits = data.get("edits")

    if not session:
        # Auto-create the session from the approval data
        new_session = {
            "session_id": session_id,
            "user_id": current_user.user_id,
            "status": "in_progress",
            "current_call_number": call_number + 1 if call_number < 3 else 3,
            "calls": {
                call_key: {
                    "analysis": edits or data,
                    "user_approved": True,
                    "approved_at": now,
                    "approved_by": current_user.user_id,
                }
            },
            "created_at": now,
            "updated_at": now,
        }
        if call_number == 2:
            new_session["ai_employee_matrix"] = (edits or data).get("matrix", [])
            new_session["gap_questions"] = (edits or data).get("gaps", [])
        elif call_number == 3:
            new_session["ai_employee_matrix"] = (edits or data).get("matrix", [])
            mmi_scores = []
            for wf in (edits or data).get("matrix", []):
                hyp = wf.get("mmi_hypothesis", {})
                if hyp.get("score"):
                    mmi_scores.append({"workflow_id": wf.get("workflow_id"), "score": hyp["score"]})
            new_session["mmi_scores"] = mmi_scores
            new_session["ai_design_documents"] = (edits or data).get("ai_design_documents", [])
            new_session["status"] = "complete"
        await db.vega_discovery_sessions.insert_one(new_session)
        logger.info(f"Auto-created Vega session {session_id} on call {call_number} approval")
    else:
        if session.get("user_id") != current_user.user_id:
            raise HTTPException(status_code=403, detail="You do not own this session")

        # Always store the analysis data in the calls entry
        analysis_data = edits or data
        update_fields = {
            f"calls.{call_key}.user_approved": True,
            f"calls.{call_key}.approved_at": now,
            f"calls.{call_key}.approved_by": current_user.user_id,
            f"calls.{call_key}.analysis": analysis_data,
            "current_call_number": call_number + 1 if call_number < 3 else 3,
            "updated_at": now,
        }

        # Extract and store client name from analysis (Call 1 usually has it)
        if isinstance(analysis_data, dict):
            client = analysis_data.get("client_name") or analysis_data.get("client_company")
            if not client and isinstance(analysis_data.get("analysis"), dict):
                client = analysis_data["analysis"].get("client_name")
            if client:
                update_fields["client_company"] = client
                update_fields["opportunity_name"] = client

        if edits:
            update_fields[f"calls.{call_key}.user_edits"] = edits

        if call_number == 2:
            update_fields["ai_employee_matrix"] = (edits or data).get("matrix", [])
            update_fields["gap_questions"] = (edits or data).get("gaps", [])
        elif call_number == 3:
            update_fields["ai_employee_matrix"] = (edits or data).get("matrix", [])
            mmi_scores = []
            for wf in (edits or data).get("matrix", []):
                hyp = wf.get("mmi_hypothesis", {})
                if hyp.get("score"):
                    mmi_scores.append({"workflow_id": wf.get("workflow_id"), "score": hyp["score"]})
            update_fields["mmi_scores"] = mmi_scores
            update_fields["ai_design_documents"] = (edits or data).get("ai_design_documents", [])

        if call_number == 3:
            update_fields["status"] = "complete"

        await db.vega_discovery_sessions.update_one(
            {"session_id": session_id},
            {"$set": update_fields},
        )

    next_call = call_number + 1 if call_number < 3 else 3

    await log_security_event(
        event_type="session_approved", severity="info",
        user_id=current_user.user_id, user_email=current_user.email,
        resource="vega_session", resource_id=session_id,
        action="approve_call", outcome="success",
        details={"session_id": session_id, "call_number": call_number},
    )

    # Post a follow-up message in the chat
    chat_session_id = data.get("chat_session_id")
    if chat_session_id:
        now_dt = datetime.now(timezone.utc)
        if call_number < 3:
            follow_up = {
                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                "session_id": chat_session_id,
                "user_id": current_user.user_id,
                "role": "assistant",
                "content": (
                    f"Call {call_number} analysis approved and saved. "
                    f"When you're ready to proceed with Call {next_call}, "
                    f"start a new Vega session and select **Just finished Call {next_call}** "
                    f"to upload the transcript."
                ),
                "created_at": now_dt,
            }
            await db.chat_messages.insert_one(follow_up)
        elif call_number == 3:
            follow_up = {
                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                "session_id": chat_session_id,
                "user_id": current_user.user_id,
                "role": "assistant",
                "content": (
                    "All 3 discovery calls have been analyzed and approved. "
                    "The AI Employee Matrix with finalized MMI scores and BPMN process diagrams "
                    "is ready for technical review. Use the **Export** button on the matrix "
                    "to download the full discovery package."
                ),
                "created_at": now_dt,
            }
            await db.chat_messages.insert_one(follow_up)

    return {
        "message": f"Call {call_number} analysis approved",
        "session_id": session_id,
        "call_number": call_number,
        "current_call_number": next_call,
    }


# ---------------------------------------------------------------------------
# Named session management endpoints
# ---------------------------------------------------------------------------


def _sanitize_tags(raw) -> list:
    """Coerce tags to a list of plain strings — blocks operator-injection from {"$gt": ""} etc."""
    if not isinstance(raw, list):
        return []
    return [str(t).strip() for t in raw if isinstance(t, str) and t.strip()]


def _sanitize_text(raw) -> str:
    """Coerce metadata text fields to a string — blocks dict-shaped injection."""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        return ""
    return raw.strip()


@router.post("/vega-sessions")
async def create_vega_session(data: dict, current_user=Depends(get_current_user)):
    """Create a new named Vega session."""
    if not has_perm(current_user, "vega.use"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    session_id = f"vega_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    session_name = _sanitize_text(data.get("session_name"))
    session = {
        "session_id": session_id,
        "user_id": current_user.user_id,
        "session_name": session_name,
        "client_company": _sanitize_text(data.get("client_company")),
        "opportunity_name": _sanitize_text(data.get("opportunity_name")) or session_name,
        "status": "in_progress",
        "current_call_number": 0,
        "calls": {},
        "ai_employee_matrix": [],
        "gap_questions": [],
        "mmi_scores": [],
        "tags": _sanitize_tags(data.get("tags")),
        "created_at": now,
        "updated_at": now,
    }
    await db.vega_discovery_sessions.insert_one(session)
    session.pop("_id", None)
    return session


@router.patch("/vega-sessions/{session_id}")
async def update_vega_session(session_id: str, data: dict, current_user=Depends(get_current_user)):
    """Update Vega session metadata (name, company, tags)."""
    if not has_perm(current_user, "vega.use"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    session = await db.vega_discovery_sessions.find_one(
        {"session_id": session_id, "user_id": current_user.user_id},
        {"_id": 0, "session_id": 1},
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    sanitizers = {
        "session_name": _sanitize_text,
        "client_company": _sanitize_text,
        "opportunity_name": _sanitize_text,
        "tags": _sanitize_tags,
    }
    updates = {k: sanitizers[k](data[k]) for k in sanitizers if k in data}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.vega_discovery_sessions.update_one(
        {"session_id": session_id, "user_id": current_user.user_id},
        {"$set": updates},
    )
    return {"session_id": session_id, "updated": list(updates.keys())}


@router.delete("/vega-sessions/{session_id}")
async def archive_vega_session(session_id: str, current_user=Depends(get_current_user)):
    """Soft-delete (archive) an Vega session."""
    if not has_perm(current_user, "vega.use"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    result = await db.vega_discovery_sessions.update_one(
        {"session_id": session_id, "user_id": current_user.user_id},
        {"$set": {"status": "archived", "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "status": "archived"}


@router.post("/vega-sessions/{session_id}/resume")
async def resume_vega_session(session_id: str, data: Optional[dict] = None, current_user=Depends(get_current_user)):
    """Resume an Vega session — returns or creates a linked chat session."""
    if not has_perm(current_user, "vega.use"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    session = await db.vega_discovery_sessions.find_one(
        {"session_id": session_id, "user_id": current_user.user_id},
        {"_id": 0},
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    chat_session_id = session.get("chat_session_id")
    if chat_session_id:
        chat_session = await db.chat_sessions.find_one(
            {"session_id": chat_session_id, "user_id": current_user.user_id},
            {"_id": 0},
        )
        if chat_session:
            return {"chat_session_id": chat_session_id, "vega_session_id": session_id, "resumed": True}

    new_chat_id = f"chat_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    chat_doc = {
        "session_id": new_chat_id,
        "user_id": current_user.user_id,
        "pinned_agent": "emp_ai_vega",
        "created_at": now,
        "updated_at": now,
        "tokens_used": 0,
    }

    # Atomic claim: only the first concurrent resume succeeds in writing the link.
    # Filter requires both user ownership AND that chat_session_id is still unset/empty,
    # so a double-submit can't orphan a chat session or hijack an existing one.
    claim = await db.vega_discovery_sessions.find_one_and_update(
        {
            "session_id": session_id,
            "user_id": current_user.user_id,
            "$or": [{"chat_session_id": {"$exists": False}}, {"chat_session_id": None}, {"chat_session_id": ""}],
        },
        {"$set": {"chat_session_id": new_chat_id, "updated_at": now.isoformat()}},
        projection={"_id": 0, "chat_session_id": 1},
    )

    if claim is None:
        # A concurrent caller won the race; re-read and return their link.
        existing = await db.vega_discovery_sessions.find_one(
            {"session_id": session_id, "user_id": current_user.user_id},
            {"_id": 0, "chat_session_id": 1},
        )
        existing_chat_id = (existing or {}).get("chat_session_id")
        if existing_chat_id:
            return {"chat_session_id": existing_chat_id, "vega_session_id": session_id, "resumed": True}
        raise HTTPException(status_code=409, detail="Could not resume session")

    await db.chat_sessions.insert_one(chat_doc)
    return {"chat_session_id": new_chat_id, "vega_session_id": session_id, "resumed": False}


@router.post("/vega-sessions/{session_id}/summarize")
async def summarize_vega_session(session_id: str, current_user=Depends(get_current_user)):
    """Generate a compressed context summary for efficient session resume."""
    if not has_perm(current_user, "vega.use"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    session = await db.vega_discovery_sessions.find_one(
        {"session_id": session_id, "user_id": current_user.user_id},
        {"_id": 0},
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Build context from all calls
    calls = session.get("calls", {})
    context_parts = []
    for call_num in sorted(calls.keys()):
        call_data = calls[call_num]
        analysis = call_data.get("analysis", {})
        if analysis:
            summary = analysis.get("summary", "")
            if summary:
                context_parts.append(f"Call {call_num}: {summary}")

    matrix = session.get("ai_employee_matrix", [])
    if matrix:
        wf_names = [wf.get("workflow_name", "Unknown") for wf in matrix]
        context_parts.append(f"Workflows identified: {', '.join(wf_names)}")

    gaps = session.get("gap_questions", [])
    if gaps:
        gap_topics = [g.get("topic", "") for g in gaps if g.get("topic")]
        if gap_topics:
            context_parts.append(f"Open gaps: {', '.join(gap_topics)}")

    if not context_parts:
        return {"session_id": session_id, "context_summary": "No analysis data to summarize."}

    # Use LLM to generate a concise summary
    try:
        from services.ai.llm import call_llm
        from services.ai.conductor import _get_model

        provider, model = await _get_model(db, "emp_ai_vega")
        raw_context = "\n".join(context_parts)

        resp = await call_llm(
            system_prompt=(
                "You are a summarizer. Compress the following Vega discovery session data into a "
                "concise 3-5 sentence summary that preserves: client name, key workflows identified, "
                "current status, and any critical gaps. This summary will be used to resume the "
                "session efficiently."
            ),
            user_message=raw_context,
            provider=provider,
            model=model,
            timeout=30,
        )
        context_summary = resp.text.strip() if resp and resp.text else "\n".join(context_parts)
    except Exception as e:
        logger.warning("Context summarization failed, using raw summary: %s", e)
        context_summary = "\n".join(context_parts)

    await db.vega_discovery_sessions.update_one(
        {"session_id": session_id, "user_id": current_user.user_id},
        {"$set": {"context_summary": context_summary, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )

    return {"session_id": session_id, "context_summary": context_summary}


# ---------------------------------------------------------------------------
# Export endpoints
# ---------------------------------------------------------------------------


@router.get("/vega-sessions/{session_id}/export/mmi")
async def export_mmi_matrix(
    session_id: str,
    format: str = Query("json", pattern="^(json|csv|markdown)$"),
    current_user=Depends(get_current_user),
):
    """Export the AI Employee Matrix with MMI scores."""
    if not has_perm(current_user, "vega_sessions.view_own"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    session = await _resolve_vega_session(db, session_id, current_user.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("user_id") != current_user.user_id:
        raise HTTPException(status_code=403, detail="You do not own this session")

    await emit_vega_event(db, current_user.user_id, session_id, "export_triggered", {"format": "mmi_csv"})

    matrix = session.get("ai_employee_matrix", [])
    if not matrix:
        raise HTTPException(status_code=404, detail="No matrix data found")

    if format == "json":
        return {"session_id": session_id, "matrix": matrix}

    elif format == "csv":
        import io, csv
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["workflow_id", "workflow_name", "description", "data_state",
                         "manual_hours_per_week", "tss", "ie", "time", "cost",
                         "quality", "satisfaction", "mmi_score"])
        for wf in matrix:
            mmi = wf.get("mmi_hypothesis", {})
            writer.writerow([
                wf.get("workflow_id", ""),
                wf.get("workflow_name", ""),
                wf.get("description", ""),
                wf.get("data_state", ""),
                wf.get("manual_hours_per_week", ""),
                mmi.get("tss", ""),
                mmi.get("ie", ""),
                mmi.get("time", ""),
                mmi.get("cost", ""),
                mmi.get("quality", ""),
                mmi.get("satisfaction", ""),
                mmi.get("score", ""),
            ])
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="mmi_matrix_{session_id}.csv"'},
        )

    elif format == "markdown":
        lines = [f"# AI Employee Matrix — {session.get('client_company', session_id)}\n"]
        for wf in matrix:
            mmi = wf.get("mmi_hypothesis", {})
            lines.append(f"## {wf.get('workflow_name', 'Unknown')} ({wf.get('data_state', '?')})")
            if wf.get("description"):
                lines.append(f"\n{wf['description']}\n")
            if wf.get("source_apps"):
                lines.append(f"**Source Apps:** {', '.join(wf['source_apps'])}")
            if wf.get("manual_hours_per_week"):
                lines.append(f"**Manual Hours/Week:** {wf['manual_hours_per_week']}")
            lines.append(
                f"**MMI:** TSS={mmi.get('tss','?')} IE={mmi.get('ie','?')} "
                f"Time={mmi.get('time','?')} Cost={mmi.get('cost','?')} "
                f"Quality={mmi.get('quality','?')} Satisfaction={mmi.get('satisfaction','?')} "
                f"| **Score={mmi.get('score','pending')}**\n"
            )
        return Response(
            content="\n".join(lines),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="mmi_matrix_{session_id}.md"'},
        )


@router.get("/vega-sessions/{session_id}/export/design-docs")
async def export_design_documents(
    session_id: str,
    workflow_id: str = Query(None),
    format: str = Query("markdown", pattern="^(json|markdown)$"),
    current_user=Depends(get_current_user),
):
    """Export AI Design Documents."""
    if not has_perm(current_user, "vega_sessions.view_own"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    session = await _resolve_vega_session(db, session_id, current_user.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("user_id") != current_user.user_id:
        raise HTTPException(status_code=403, detail="You do not own this session")

    await emit_vega_event(db, current_user.user_id, session_id, "export_triggered", {"format": "design_docs"})

    docs = session.get("ai_design_documents", [])
    if workflow_id:
        docs = [d for d in docs if d.get("workflow_id") == workflow_id]
    if not docs:
        raise HTTPException(status_code=404, detail="No design documents found")

    if format == "json":
        return {"session_id": session_id, "design_documents": docs}

    # Markdown format
    lines = [f"# AI Design Documents — {session.get('client_company', session_id)}\n"]
    for doc in docs:
        lines.append(f"---\n\n## {doc.get('workflow_name', 'Unknown')}\n")
        if doc.get("executive_summary"):
            lines.append(f"### Executive Summary\n{doc['executive_summary']}\n")

        cs = doc.get("current_state", {})
        if cs:
            lines.append("### Current State")
            if cs.get("process_description"):
                lines.append(f"\n{cs['process_description']}\n")
            if cs.get("manual_effort"):
                lines.append(f"**Manual Effort:** {cs['manual_effort']}")
            if cs.get("pain_points"):
                lines.append("\n**Pain Points:**")
                for p in cs["pain_points"]:
                    lines.append(f"- {p}")
            if cs.get("systems_involved"):
                lines.append(f"\n**Systems:** {', '.join(cs['systems_involved'])}\n")

        ps = doc.get("proposed_ai_solution", {})
        if ps:
            lines.append("### Proposed AI Solution")
            if ps.get("solution_description"):
                lines.append(f"\n{ps['solution_description']}\n")
            if ps.get("ai_role"):
                lines.append(f"**AI Role:** {ps['ai_role']}")
            if ps.get("human_role"):
                lines.append(f"**Human Role:** {ps['human_role']}")
            if ps.get("automation_percentage"):
                lines.append(f"**Automation:** {ps['automation_percentage']}")
            if ps.get("key_capabilities"):
                lines.append("\n**Key Capabilities:**")
                for c in ps["key_capabilities"]:
                    lines.append(f"- {c}")
            lines.append("")

        if doc.get("bpmn_process_narrative"):
            lines.append(f"### Process Narrative\n{doc['bpmn_process_narrative']}\n")

        irs = doc.get("integration_requirements", [])
        if irs:
            lines.append("### Integration Requirements\n")
            lines.append("| System | Type | Data | Complexity |")
            lines.append("|--------|------|------|------------|")
            pipe = "|"
            escaped_pipe = "\\|"
            for ir in irs:
                sn = str(ir.get('system_name', '')).replace(pipe, escaped_pipe)
                it = str(ir.get('integration_type', '')).replace(pipe, escaped_pipe)
                de = str(ir.get('data_exchanged', '')).replace(pipe, escaped_pipe)
                cx = str(ir.get('complexity', '')).replace(pipe, escaped_pipe)
                lines.append(f"| {sn} | {it} | {de} | {cx} |")
            lines.append("")

        mj = doc.get("mmi_justification", {})
        if mj:
            lines.append("### MMI Justification")
            for dim_key, dim_label in [("tss_rationale","TSS"),("ie_rationale","IE"),("time_rationale","Time"),("cost_rationale","Cost"),("quality_rationale","Quality"),("satisfaction_rationale","Satisfaction")]:
                if mj.get(dim_key):
                    lines.append(f"- **{dim_label}:** {mj[dim_key]}")
            lines.append("")

        ic = doc.get("implementation_considerations", {})
        if ic:
            lines.append("### Implementation Considerations")
            if ic.get("estimated_complexity"):
                lines.append(f"\n**Complexity:** {ic['estimated_complexity']}")
            for label, key in [("Risks","risks"),("Dependencies","dependencies"),("Assumptions","assumptions"),("Open Questions","open_questions")]:
                items = ic.get(key, [])
                if items:
                    lines.append(f"\n**{label}:**")
                    for item in items:
                        lines.append(f"- {item}")
            lines.append("")

        hts = doc.get("human_touchpoints", [])
        if hts:
            lines.append("### Human Touchpoints")
            for ht in hts:
                freq = f" ({ht['frequency']})" if ht.get("frequency") else ""
                lines.append(f"- **{ht.get('touchpoint','')}**: {ht.get('reason','')}{freq}")
            lines.append("")

    return Response(
        content="\n".join(lines),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="design_docs_{session_id}.md"'},
    )


@router.get("/vega-sessions/{session_id}/export/app-designs")
async def export_app_designs(
    session_id: str,
    format: str = Query("md", pattern="^(md|json)$"),
    current_user=Depends(get_current_user),
):
    """Export App Designs (internal: EADDs) as paste-ready markdown or raw JSON."""
    if not has_perm(current_user, "vega_sessions.view_own"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    session = await _resolve_vega_session(db, session_id, current_user.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("user_id") != current_user.user_id:
        raise HTTPException(status_code=403, detail="You do not own this session")

    eadd_docs = session.get("emergent_app_design_documents") or []

    await emit_vega_event(
        db, current_user.user_id, session_id, "export_triggered",
        {"export_type": "app_designs", "format": format, "count": len(eadd_docs)},
    )

    if format == "json":
        import json as _json
        return Response(
            content=_json.dumps({"app_designs": eadd_docs}, default=str, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="app_designs_{session_id}.json"'},
        )

    # Markdown — one heading per app, starter prompt in a fenced code block so
    # the rep can copy-paste cleanly into Emergent without losing formatting.
    lines: list[str] = []
    lines.append(f"# App Designs — {session.get('client_company') or session_id}")
    lines.append("")
    if not eadd_docs:
        lines.append("_No App Designs were surfaced in this session._")
    for doc in eadd_docs:
        name = doc.get("app_name") or "(unnamed app)"
        lines.append(f"## {name}")
        if doc.get("status") == "failed":
            lines.append(f"_Generation failed: {doc.get('failure_reason') or 'unknown reason'}_")
            lines.append("")
            continue
        if doc.get("app_pitch_summary"):
            lines.append(f"**Pitch:** {doc['app_pitch_summary']}")
        if doc.get("primary_persona_hint"):
            lines.append(f"**Primary persona:** {doc['primary_persona_hint']}")
        if doc.get("linked_workflow_ids"):
            lines.append(f"**Linked workflows:** {', '.join(doc['linked_workflow_ids'])}")
        lines.append("")
        lines.append("### Starter prompt")
        lines.append("```")
        lines.append((doc.get("starter_prompt") or "").strip())
        lines.append("```")
        lines.append("")
        if doc.get("core_flows"):
            lines.append("### Core flows")
            for f in doc["core_flows"]:
                lines.append(f"- {f}")
            lines.append("")
        if doc.get("data_model"):
            lines.append("### Data model")
            for entity in doc["data_model"]:
                ename = entity.get("entity") or "(entity)"
                fields = ", ".join(entity.get("key_fields") or [])
                lines.append(f"- **{ename}** — {fields}")
            lines.append("")
        if doc.get("integrations"):
            lines.append("### Integrations")
            for ig in doc["integrations"]:
                lines.append(f"- {ig.get('system')} — {ig.get('purpose')}")
            lines.append("")
        if doc.get("out_of_scope"):
            lines.append("### Out of scope")
            for o in doc["out_of_scope"]:
                lines.append(f"- {o}")
            lines.append("")
        if doc.get("visual_style"):
            lines.append(f"**Visual style:** {doc['visual_style']}")
            lines.append("")
        lines.append("---")
        lines.append("")

    return Response(
        content="\n".join(lines),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="app_designs_{session_id}.md"'},
    )


@router.get("/vega-sessions/{session_id}/export/package")
async def export_full_package(
    session_id: str,
    current_user=Depends(get_current_user),
):
    """Export complete discovery package as a ZIP file."""
    if not has_perm(current_user, "vega_sessions.view_own"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    session = await _resolve_vega_session(db, session_id, current_user.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("user_id") != current_user.user_id:
        raise HTTPException(status_code=403, detail="You do not own this session")

    await emit_vega_event(db, current_user.user_id, session_id, "export_triggered", {"format": "zip"})

    import io, csv, json, zipfile

    matrix = session.get("ai_employee_matrix", [])
    design_docs = session.get("ai_design_documents", [])

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Matrix CSV
        csv_buf = io.StringIO()
        writer = csv.writer(csv_buf)
        writer.writerow(["workflow_id", "workflow_name", "description", "data_state",
                         "manual_hours_per_week", "tss", "ie", "time", "cost",
                         "quality", "satisfaction", "mmi_score"])
        for wf in matrix:
            mmi = wf.get("mmi_hypothesis", {})
            writer.writerow([
                wf.get("workflow_id",""), wf.get("workflow_name",""),
                wf.get("description",""), wf.get("data_state",""),
                wf.get("manual_hours_per_week",""),
                mmi.get("tss",""), mmi.get("ie",""), mmi.get("time",""),
                mmi.get("cost",""), mmi.get("quality",""),
                mmi.get("satisfaction",""), mmi.get("score",""),
            ])
        zf.writestr("ai_employee_matrix.csv", csv_buf.getvalue())

        # 2. Matrix JSON
        zf.writestr("ai_employee_matrix.json", json.dumps(matrix, default=str, indent=2))

        # 3. Design docs as full markdown (matching AIDesignDocumentViewer)
        for doc in design_docs:
            wf_name = (doc.get("workflow_name") or "unknown").replace(" ", "_")
            wf_id = doc.get("workflow_id", "")
            filename = f"design_docs/{wf_name}_{wf_id}.md"
            lines = [f"# {doc.get('workflow_name', 'Unknown')}\n"]

            # Executive Summary
            if doc.get("executive_summary"):
                lines.append(f"## Executive Summary\n{doc['executive_summary']}\n")

            # Current State
            cs = doc.get("current_state", {})
            if cs:
                lines.append("## Current State")
                if cs.get("process_description"):
                    lines.append(f"\n{cs['process_description']}\n")
                if cs.get("pain_points"):
                    lines.append("**Pain Points:**")
                    for pp in cs["pain_points"]:
                        lines.append(f"- {pp}")
                    lines.append("")
                if cs.get("systems_involved"):
                    lines.append(f"**Systems:** {', '.join(cs['systems_involved'])}")
                if cs.get("manual_effort"):
                    lines.append(f"**Manual Effort:** {cs['manual_effort']}")
                if cs.get("error_rate_notes"):
                    lines.append(f"**Error Rate:** {cs['error_rate_notes']}")
                lines.append("")

            # Proposed AI Solution
            ps = doc.get("proposed_ai_solution", {})
            if ps:
                lines.append("## Proposed AI Solution")
                if ps.get("solution_description"):
                    lines.append(f"\n{ps['solution_description']}\n")
                if ps.get("ai_role"):
                    lines.append(f"**AI Role:** {ps['ai_role']}")
                if ps.get("human_role"):
                    lines.append(f"**Human Role:** {ps['human_role']}")
                if ps.get("automation_percentage"):
                    lines.append(f"**Automation:** {ps['automation_percentage']}")
                if ps.get("key_capabilities"):
                    lines.append("**Key Capabilities:**")
                    for cap in ps["key_capabilities"]:
                        lines.append(f"- {cap}")
                lines.append("")

            # Process Narrative
            if doc.get("bpmn_process_narrative"):
                lines.append(f"## Process Narrative\n{doc['bpmn_process_narrative']}\n")

            # Integration Requirements
            ir = doc.get("integration_requirements", [])
            if ir:
                lines.append("## Integration Requirements\n")
                lines.append("| System | Type | Data Exchanged | Complexity |")
                lines.append("|--------|------|----------------|------------|")
                pipe = "|"
                escaped_pipe = "\\|"
                for req in ir:
                    sn = str(req.get('system_name', '')).replace(pipe, escaped_pipe)
                    it = str(req.get('integration_type', '')).replace(pipe, escaped_pipe)
                    de = str(req.get('data_exchanged', '')).replace(pipe, escaped_pipe)
                    cx = str(req.get('complexity', '')).replace(pipe, escaped_pipe)
                    lines.append(f"| {sn} | {it} | {de} | {cx} |")
                lines.append("")

            # Data Flow
            df = doc.get("data_flow", {})
            if df:
                lines.append("## Data Flow")
                if df.get("inputs"):
                    lines.append(f"\n**Inputs:** {df['inputs']}")
                if df.get("processing"):
                    lines.append(f"**Processing:** {df['processing']}")
                if df.get("outputs"):
                    lines.append(f"**Outputs:** {df['outputs']}")
                if df.get("data_sensitivity"):
                    lines.append(f"**Data Sensitivity:** {df['data_sensitivity']}")
                lines.append("")

            # MMI Justification
            mmi = doc.get("mmi_justification", {})
            if mmi:
                lines.append("## MMI Score Justification")
                for dim in ["tss", "ie", "time", "cost", "quality", "satisfaction"]:
                    key = f"{dim}_rationale"
                    if mmi.get(key):
                        lines.append(f"- **{dim.upper()}:** {mmi[key]}")
                lines.append("")

            # Implementation Considerations
            ic = doc.get("implementation_considerations", {})
            if ic:
                lines.append("## Implementation Considerations")
                if ic.get("estimated_complexity"):
                    lines.append(f"\n**Complexity:** {ic['estimated_complexity']}")
                if ic.get("estimated_timeline"):
                    lines.append(f"**Timeline:** {ic['estimated_timeline']}")
                if ic.get("risks"):
                    lines.append("**Risks:**")
                    for r in ic["risks"]:
                        lines.append(f"- {r}")
                if ic.get("dependencies"):
                    lines.append("**Dependencies:**")
                    for d in ic["dependencies"]:
                        lines.append(f"- {d}")
                if ic.get("assumptions"):
                    lines.append("**Assumptions:**")
                    for a in ic["assumptions"]:
                        lines.append(f"- {a}")
                if ic.get("open_questions"):
                    lines.append("**Open Questions:**")
                    for q in ic["open_questions"]:
                        lines.append(f"- {q}")
                lines.append("")

            # Human Touchpoints
            ht = doc.get("human_touchpoints", [])
            if ht:
                lines.append("## Human Touchpoints")
                for tp in ht:
                    touch = tp.get("touchpoint", "")
                    reason = tp.get("reason", "")
                    freq = tp.get("frequency", "")
                    lines.append(f"- **{touch}** — {reason}{f' ({freq})' if freq else ''}")
                lines.append("")

            zf.writestr(filename, "\n".join(lines))

        # 4. BPMN diagrams
        bpmn_ids = [wf.get("bpmn_id") for wf in matrix if wf.get("bpmn_id")]
        for bpmn_id in bpmn_ids:
            diagram = await db.bpmn_diagrams.find_one(
                {"bpmn_id": bpmn_id}, {"_id": 0, "bpmn_xml": 1, "edited_xml": 1, "workflow_name": 1}
            )
            if diagram:
                xml = diagram.get("edited_xml") or diagram.get("bpmn_xml")
                if xml:
                    wf_name = (diagram.get("workflow_name") or "diagram").replace(" ", "_")
                    zf.writestr(f"bpmn/{wf_name}_{bpmn_id}.bpmn", xml)

    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="discovery_package_{session_id}.zip"'},
    )


# ---------------------------------------------------------------------------
# Preliminary BPMN generation
# ---------------------------------------------------------------------------


@router.post("/vega-sessions/{session_id}/generate-preliminary-bpmns")
async def generate_preliminary_bpmns(
    session_id: str,
    current_user=Depends(get_current_user),
):
    """Generate preliminary BPMN diagrams from Call 1 workflow descriptions."""
    if not has_perm(current_user, "vega.use"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    session = await _resolve_vega_session(db, session_id, current_user.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("user_id") != current_user.user_id:
        raise HTTPException(status_code=403, detail="You do not own this session")

    # Get workflows from Call 1 analysis
    call_1 = session.get("calls", {}).get("1", {})
    analysis = call_1.get("analysis", {})
    workflows = analysis.get("potential_workflows_spotted", [])

    if not workflows:
        raise HTTPException(status_code=400, detail="No workflows found in Call 1 analysis")

    from services.ai.llm import call_llm
    from services.ai.conductor import _get_model
    from services.bpmn.pipeline import generate_bpmn_from_json
    import json

    provider, model = await _get_model(db, "emp_ai_vega")
    results = []

    for wf_desc in workflows:
        # Parse "Workflow Name: description" format
        parts = wf_desc.split(":", 1)
        wf_name = parts[0].strip()
        wf_description = parts[1].strip() if len(parts) > 1 else wf_desc
        wf_id = "wf_" + wf_name.lower().replace(" ", "_")[:30]

        # Ask LLM to infer basic BPMN structure
        infer_prompt = f"""Based on this workflow description from a discovery call, infer the PROPOSED AI Employee workflow — showing how the AI will handle this process, not the current manual process.

Workflow: {wf_name}
Description: {wf_description}

Generate a JSON object with:
- workflow_id: "{wf_id}"
- workflow_name: "{wf_name}"
- actors: list of {{id, name, type}} where type is human/ai/external_system. ALWAYS include an AI actor (type=ai, name="AI Employee") for the automated steps.
- trigger: {{type (scheduled/event/manual/api_call), description, actor_id}}
- steps: ordered list of {{id, name, actor_id, task_type (user/service/business_rule/manual), description}}. Assign AI-automated steps to the AI actor. Include human review/approval steps where appropriate.
- decision_points: {{id, after_step_id, type (exclusive/parallel), name, branches: [{{condition, leads_to_step_id}}]}}
- exception_paths: [] (empty for preliminary)
- outputs: {{id, name, after_step_id}}

Use "a1", "a2" for actor IDs, "s1", "s2" for step IDs, "d1" for decision IDs, "o1" for output IDs.
Be reasonable — infer 4-6 steps based on the description. Show where AI handles the heavy lifting and where humans review/approve."""

        try:
            resp = await call_llm(
                system_prompt="Return valid JSON only. No explanation.",
                user_message=infer_prompt,
                provider=provider, model=model,
                timeout=60,
            )
            if resp and resp.text:
                wf_json = json.loads(resp.text)
                bpmn_result = await generate_bpmn_from_json(wf_json)
                if bpmn_result.get("success") and bpmn_result.get("bpmn_xml"):
                    bpmn_id = f"bpmn_{uuid.uuid4().hex[:12]}"
                    await db.bpmn_diagrams.insert_one({
                        "bpmn_id": bpmn_id,
                        "workflow_id": wf_id,
                        "workflow_name": wf_name,
                        "bpmn_xml": bpmn_result["bpmn_xml"],
                        "validation_warnings": bpmn_result.get("validation_warnings", []),
                        "created_by": current_user.user_id,
                        "created_by_agent": "ai_vega",
                        "preliminary": True,
                        "created_at": datetime.now(timezone.utc),
                    })
                    results.append({"workflow_name": wf_name, "bpmn_id": bpmn_id, "status": "generated"})
                else:
                    results.append({"workflow_name": wf_name, "status": "failed", "error": bpmn_result.get("error")})
        except Exception as e:
            results.append({"workflow_name": wf_name, "status": "failed", "error": str(e)})

    return {"session_id": session_id, "results": results, "generated": sum(1 for r in results if r["status"] == "generated")}


# ---------------------------------------------------------------------------
# Google Doc export
# ---------------------------------------------------------------------------


@router.post("/vega-sessions/{session_id}/export/google-doc")
async def export_to_google_doc(
    session_id: str,
    current_user=Depends(get_current_user),
):
    """Export Vega analysis as a formatted Google Doc."""
    if not has_perm(current_user, "vega_sessions.view_own"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    session = await _resolve_vega_session(db, session_id, current_user.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("user_id") != current_user.user_id:
        raise HTTPException(status_code=403, detail="You do not own this session")

    await emit_vega_event(db, current_user.user_id, session_id, "export_triggered", {"format": "google_doc"})

    calls = session.get("calls", {})
    matrix = session.get("ai_employee_matrix", [])
    design_docs = session.get("ai_design_documents", [])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    client = session.get("client_company") or session.get("opportunity_name")
    if not client:
        for cn in ("1", "2", "3"):
            a = (calls.get(cn) or {}).get("analysis") or {}
            if isinstance(a, dict) and a.get("client_name"):
                client = a["client_name"]
                break
    if not client:
        client = "Unknown"

    # Build content with heading markers.
    # segments: list of (text, heading_level) where 0=normal, 1=H1, 2=H2, 3=H3
    segments: list[tuple[str, int]] = []

    def _add(text: str = "", level: int = 0):
        segments.append((text, level))

    def _bullet(text: str, indent: int = 1):
        if text is None:
            return
        s = str(text).strip()
        if s:
            _add(("  " * indent) + "\u2022 " + s)

    def _kv(label: str, value):
        if value in (None, "", [], {}):
            return
        if isinstance(value, list):
            value = ", ".join(str(x) for x in value if x not in (None, ""))
            if not value:
                return
        _add(f"{label}: {value}")

    def _list_section(label: str, items, formatter=None):
        """Render a labeled bullet list. Skip if items is empty."""
        items = [i for i in (items or []) if i not in (None, "", {}, [])]
        if not items:
            return
        _add(label, 3)
        for i in items:
            _bullet(formatter(i) if formatter else i)

    _add(f"Vega Discovery Report \u2014 {client}", 1)
    _add(f"Generated: {today}")
    _add()

    # \u2500\u2500\u2500 Call analyses \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    for call_num in ("1", "2", "3"):
        call_data = calls.get(call_num) or {}
        analysis = call_data.get("analysis") or {}
        if not isinstance(analysis, dict) or not analysis:
            continue

        _add(f"Call {call_num} Analysis", 2)

        if analysis.get("summary"):
            _add(f"Summary: {analysis['summary']}")
        if analysis.get("client_name"):
            _kv("Client", analysis["client_name"])
        if analysis.get("data_state_assessment"):
            ds = analysis["data_state_assessment"]
            justification = analysis.get("data_state_justification", "")
            _add(f"Data State: {ds}" + (f" \u2014 {justification}" if justification else ""))
        if analysis.get("industry_context"):
            _add("Industry Context", 3)
            _add(analysis["industry_context"])

        # Pain points (Call 1 / Call 2)
        if analysis.get("identified_pain_points"):
            _add("Identified Pain Points", 3)
            for pp in analysis["identified_pain_points"]:
                desc = pp.get("description", "")
                sev = pp.get("severity", "")
                freq = pp.get("frequency", "")
                impact = pp.get("business_impact", "")
                quote = pp.get("direct_quote", "")
                tag_parts = [p for p in (sev, freq) if p]
                tag = f" [{' / '.join(tag_parts)}]" if tag_parts else ""
                _bullet(f"{desc}{tag}")
                if impact:
                    _bullet(f"Business impact: {impact}", indent=2)
                if quote:
                    _bullet(f"Quote: \u201c{quote}\u201d", indent=2)

        _list_section("Current Tech Stack", analysis.get("current_tech_stack"))
        _list_section("Suggested Tools", analysis.get("suggested_tools"))

        if analysis.get("ai_readiness_signals"):
            _add("AI Readiness Signals", 3)
            for sig in analysis["ai_readiness_signals"]:
                indicator = sig.get("indicator", "")
                signal = sig.get("signal", "")
                evidence = sig.get("evidence", "")
                marker = {"positive": "\u2795", "negative": "\u2796", "neutral": "\u25cb"}.get(indicator, "\u2022")
                _add(f"  {marker} [{indicator}] {signal}")
                if evidence:
                    _bullet(f"Evidence: {evidence}", indent=2)

        if analysis.get("data_sources_inventory"):
            _add("Data Sources Inventory", 3)
            for src in analysis["data_sources_inventory"]:
                name = src.get("source_name", "")
                stype = src.get("type", "")
                access = src.get("accessibility", "")
                quality = src.get("data_quality_notes", "")
                head = name + (f" ({stype})" if stype else "")
                _bullet(head)
                if access:
                    _bullet(f"Accessibility: {access}", indent=2)
                if quality:
                    _bullet(f"Quality notes: {quality}", indent=2)

        if analysis.get("stakeholders_mentioned"):
            _add("Stakeholders Mentioned", 3)
            for s in analysis["stakeholders_mentioned"]:
                name = s.get("name", "")
                role = s.get("role", "")
                head = f"{name} \u2014 {role}" if name and role else (name or role)
                _bullet(head)
                if s.get("concerns"):
                    _bullet(f"Concerns: {s['concerns']}", indent=2)

        if analysis.get("stakeholder_strategies"):
            _add("Stakeholder Strategies", 3)
            for s in analysis["stakeholder_strategies"]:
                name = s.get("stakeholder_name", "")
                role = s.get("role", "")
                head = f"{name} \u2014 {role}" if name and role else (name or role or "Stakeholder")
                _add(f"  {head}")
                if s.get("buyer_type"):
                    _bullet(f"Buyer type: {s['buyer_type']}", indent=2)
                if s.get("pain_summary"):
                    _bullet(f"Pain: {s['pain_summary']}", indent=2)
                if s.get("selling_strategy"):
                    _bullet(f"Strategy: {s['selling_strategy']}", indent=2)
                if s.get("hook_question"):
                    _bullet(f"Hook: {s['hook_question']}", indent=2)
                if s.get("tactical_advice"):
                    _bullet(f"Tactical: {s['tactical_advice']}", indent=2)

        if analysis.get("workflow_investigation_guides"):
            _add("Workflow Investigation Guides", 3)
            for g in analysis["workflow_investigation_guides"]:
                _add(f"  {g.get('workflow_name', 'Workflow')}")
                for q in g.get("walkthrough_questions") or []:
                    _bullet(f"Walkthrough: {q}", indent=2)
                for q in g.get("decision_probes") or []:
                    _bullet(f"Decision probe: {q}", indent=2)
                for q in g.get("technical_checks") or []:
                    _bullet(f"Technical check: {q}", indent=2)
                for q in g.get("mmi_quantification") or []:
                    _bullet(f"MMI question: {q}", indent=2)
                if g.get("pro_tip"):
                    _bullet(f"Pro tip: {g['pro_tip']}", indent=2)

        # Call-self assessment (Call 2)
        ca = analysis.get("call_analysis") or {}
        if ca:
            _add("Call Analysis", 3)
            if ca.get("summary"):
                _add(ca["summary"])
            if ca.get("what_went_well"):
                _add("What Went Well:")
                for x in ca["what_went_well"]:
                    _bullet(x)
            if ca.get("areas_for_improvement"):
                _add("Areas for Improvement:")
                for x in ca["areas_for_improvement"]:
                    _bullet(x)
            if ca.get("missed_items"):
                _add("Missed Items:")
                for x in ca["missed_items"]:
                    _bullet(x)

        # Gaps (Call 2 / Call 3)
        if analysis.get("gaps"):
            _add("Gaps to Resolve", 3)
            for gap in analysis["gaps"]:
                topic = gap.get("topic", "")
                _bullet(topic)
                if gap.get("what_we_know"):
                    _bullet(f"Known: {gap['what_we_know']}", indent=2)
                if gap.get("what_we_need_to_ask"):
                    _bullet(f"Need to ask: {gap['what_we_need_to_ask']}", indent=2)

        # Workflow deep-dive feedback (Call 2)
        if analysis.get("workflow_deep_dive_feedback"):
            _add("Workflow Deep-Dive Feedback", 3)
            for wf in analysis["workflow_deep_dive_feedback"]:
                _add(f"  {wf.get('workflow_name', 'Workflow')}")
                if wf.get("completeness_assessment"):
                    _bullet(f"Completeness: {wf['completeness_assessment']}", indent=2)
                for q in wf.get("call_3_questions") or []:
                    _bullet(f"Call 3 question: {q}", indent=2)
                if wf.get("pro_tip"):
                    _bullet(f"Pro tip: {wf['pro_tip']}", indent=2)

        # Contradictions resolved (Call 3)
        if analysis.get("contradictions_resolved"):
            _add("Contradictions Resolved", 3)
            for c in analysis["contradictions_resolved"]:
                _bullet(c.get("topic", ""))
                if c.get("prior_understanding"):
                    _bullet(f"Was: {c['prior_understanding']}", indent=2)
                if c.get("resolved_to"):
                    _bullet(f"Now: {c['resolved_to']}", indent=2)

        _list_section("Missed Questions", analysis.get("missed_questions"))
        _list_section("Potential Workflows Spotted", analysis.get("potential_workflows_spotted"))
        _list_section("Next Call Agenda", analysis.get("next_call_agenda"))

        if analysis.get("call_2_approach_summary"):
            _add("Call 2 Approach Summary", 3)
            _add(analysis["call_2_approach_summary"])
        if analysis.get("call_3_approach_summary"):
            _add("Call 3 Approach Summary", 3)
            _add(analysis["call_3_approach_summary"])

        _add()

    # \u2500\u2500\u2500 AI Employee Matrix \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    if matrix:
        _add("AI Employee Matrix", 2)
        for wf in matrix:
            _add(wf.get("workflow_name", "Unknown"), 3)
            if wf.get("description"):
                _add(wf["description"])

            _kv("Category", wf.get("estimated_workflow_category"))
            _kv("Data State", wf.get("data_state"))
            _kv("Manual Hours/Week", wf.get("manual_hours_per_week"))
            _kv("Source Systems", wf.get("source_apps"))
            _kv("Stakeholders", wf.get("stakeholders"))
            _kv("AEGISAXIOM Dependencies", wf.get("aegisaxiom_module_dependencies"))
            _kv("Transformation", wf.get("transformation_logic"))

            mmi = wf.get("mmi_hypothesis") or {}
            if mmi:
                score = mmi.get("score", "pending")
                _kv("MMI Score", score)
                detail = mmi.get("_detail") or {}
                if detail:
                    _add("MMI Dimension Detail:")
                    for dim in ("tss", "ie", "time", "cost", "quality", "satisfaction"):
                        d = detail.get(dim) or {}
                        if not d:
                            continue
                        val = d.get("value")
                        conf = d.get("confidence", "")
                        reasoning = d.get("reasoning", "")
                        head = f"{dim.upper()} = {val}"
                        if conf:
                            head += f" ({conf} confidence)"
                        _bullet(head)
                        if reasoning:
                            _bullet(reasoning, indent=2)
                if mmi.get("needs_client_input"):
                    _add("Needs Client Input:")
                    for q in mmi["needs_client_input"]:
                        _bullet(q)

            sla = wf.get("sla_requirements") or {}
            if sla:
                _add("SLA:")
                _kv("  Expected duration", sla.get("expected_duration"))
                _kv("  Target duration", sla.get("target_duration"))
                _kv("  Current bottleneck", sla.get("current_bottleneck"))

            ips = wf.get("integration_points") or []
            if ips:
                _add("Integration Points:")
                for ip in ips:
                    sysname = ip.get("system_name", "")
                    itype = ip.get("integration_type", "")
                    head = f"{sysname} ({itype})" if sysname and itype else (sysname or itype or "")
                    _bullet(head)
                    if ip.get("current_state"):
                        _bullet(f"Current state: {ip['current_state']}", indent=2)
                    if ip.get("data_exchanged"):
                        _bullet(f"Data exchanged: {ip['data_exchanged']}", indent=2)
                    if ip.get("notes"):
                        _bullet(f"Notes: {ip['notes']}", indent=2)

            # BPMN structure summary
            actors = wf.get("actors") or []
            steps = wf.get("steps") or []
            decisions = wf.get("decision_points") or []
            exceptions = wf.get("exception_paths") or []
            outputs = wf.get("outputs") or []
            if actors or steps or decisions:
                _add("Process Structure:")
                if actors:
                    actor_str = ", ".join(
                        f"{a.get('name', '?')} ({a.get('type', '?')})" for a in actors
                    )
                    _bullet(f"Actors: {actor_str}")
                trig = wf.get("trigger") or {}
                if trig:
                    _bullet(f"Trigger ({trig.get('type', '?')}): {trig.get('description', '')}")
                if steps:
                    _bullet(f"Steps ({len(steps)}):")
                    for st in steps:
                        sid = st.get("id", "")
                        sname = st.get("name", "")
                        sactor = st.get("actor_id", "")
                        stype = st.get("task_type", "")
                        head = f"{sid}: {sname}"
                        tag_parts = [p for p in (stype, sactor) if p]
                        if tag_parts:
                            head += f" [{' / '.join(tag_parts)}]"
                        _bullet(head, indent=2)
                if decisions:
                    _bullet(f"Decision points: {len(decisions)}")
                if exceptions:
                    _bullet(f"Exception paths: {len(exceptions)}")
                if outputs:
                    out_str = ", ".join(o.get("name", "?") for o in outputs)
                    _bullet(f"Outputs: {out_str}")

            _add()

    # \u2500\u2500\u2500 Design Documents \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    for doc in design_docs:
        _add(f"Design Document: {doc.get('workflow_name', 'Unknown')}", 2)

        if doc.get("executive_summary"):
            _add("Executive Summary", 3)
            _add(doc["executive_summary"])

        cs = doc.get("current_state") or {}
        if cs:
            _add("Current State", 3)
            if cs.get("process_description"):
                _add(cs["process_description"])
            if cs.get("pain_points"):
                _add("Pain Points:")
                for pp in cs["pain_points"]:
                    _bullet(pp)
            _kv("Systems", cs.get("systems_involved"))
            _kv("Manual Effort", cs.get("manual_effort"))
            _kv("Error Rate Notes", cs.get("error_rate_notes"))

        ps = doc.get("proposed_ai_solution") or {}
        if ps:
            _add("Proposed AI Solution", 3)
            if ps.get("solution_description"):
                _add(ps["solution_description"])
            _kv("AI Role", ps.get("ai_role"))
            _kv("Human Role", ps.get("human_role"))
            _kv("Automation %", ps.get("automation_percentage"))
            if ps.get("key_capabilities"):
                _add("Key Capabilities:")
                for cap in ps["key_capabilities"]:
                    _bullet(cap)

        if doc.get("bpmn_process_narrative"):
            _add("Process Narrative", 3)
            _add(doc["bpmn_process_narrative"])

        # Enriched flat fields (when present from older flow)
        _kv("Workflow Category", doc.get("estimated_workflow_category"))
        _kv("AEGISAXIOM Dependencies", doc.get("aegisaxiom_module_dependencies"))
        trigger = doc.get("trigger_specification") or {}
        if trigger:
            parts = []
            if trigger.get("type"):
                parts.append(f"Type: {trigger['type']}")
            if trigger.get("frequency"):
                parts.append(f"Frequency: {trigger['frequency']}")
            if trigger.get("data_source"):
                parts.append(f"Source: {trigger['data_source']}")
            if parts:
                _add(f"Trigger: {' | '.join(parts)}")

        ireq = doc.get("integration_requirements") or []
        if ireq:
            _add("Integration Requirements", 3)
            for ir in ireq:
                sysname = ir.get("system_name", "")
                itype = ir.get("integration_type", "")
                complexity = ir.get("complexity", "")
                head = sysname
                tag_parts = [p for p in (itype, complexity and f"{complexity} complexity") if p]
                if tag_parts:
                    head += f" [{' / '.join(tag_parts)}]"
                _bullet(head)
                if ir.get("data_exchanged"):
                    _bullet(f"Data: {ir['data_exchanged']}", indent=2)
                if ir.get("notes"):
                    _bullet(f"Notes: {ir['notes']}", indent=2)

        df = doc.get("data_flow") or {}
        if df:
            _add("Data Flow", 3)
            inputs = df.get("inputs") or []
            if inputs:
                _add("Inputs:")
                for i in inputs:
                    name = i.get("name", "")
                    src = i.get("source", "")
                    fmt = i.get("format", "")
                    parts = [p for p in (src, fmt) if p]
                    tag = f" ({', '.join(parts)})" if parts else ""
                    _bullet(f"{name}{tag}")
            if df.get("processing"):
                _add(f"Processing: {df['processing']}")
            outputs = df.get("outputs") or []
            if outputs:
                _add("Outputs:")
                for o in outputs:
                    name = o.get("name", "")
                    dest = o.get("destination", "")
                    fmt = o.get("format", "")
                    parts = [p for p in (dest, fmt) if p]
                    tag = f" ({', '.join(parts)})" if parts else ""
                    _bullet(f"{name}{tag}")
            _kv("Data Sensitivity", df.get("data_sensitivity"))

        mmi = doc.get("mmi_justification") or {}
        if mmi:
            _add("MMI Score Justification", 3)
            if mmi.get("overall_score") is not None:
                _kv("Overall Score", mmi.get("overall_score"))
            for dim in ("tss", "ie", "time", "cost", "quality", "satisfaction"):
                rationale = mmi.get(f"{dim}_rationale")
                if rationale:
                    _add(f"{dim.upper()}: {rationale}")

        ic = doc.get("implementation_considerations") or {}
        if ic:
            _add("Implementation Considerations", 3)
            _kv("Complexity", ic.get("estimated_complexity"))
            if ic.get("risks"):
                _add("Risks:")
                for r in ic["risks"]:
                    _bullet(r)
            if ic.get("dependencies"):
                _add("Dependencies:")
                for d in ic["dependencies"]:
                    _bullet(d)
            if ic.get("assumptions"):
                _add("Assumptions:")
                for a in ic["assumptions"]:
                    _bullet(a)
            if ic.get("open_questions"):
                _add("Open Questions:")
                for q in ic["open_questions"]:
                    _bullet(q)

        ht = doc.get("human_touchpoints") or []
        if ht:
            _add("Human Touchpoints", 3)
            for tp in ht:
                touch = tp.get("touchpoint", "")
                reason = tp.get("reason", "")
                freq = tp.get("frequency", "")
                head = touch + (f" \u2014 {reason}" if reason else "")
                _bullet(head)
                if freq:
                    _bullet(f"Frequency: {freq}", indent=2)

        _add()

    # Build the full text and track heading ranges for formatting.
    # Google Docs API uses UTF-16 code units for indexing.
    def _utf16_len(s):
        return sum(2 if ord(c) > 0xFFFF else 1 for c in s)

    full_text = ""
    heading_ranges = []  # list of (start_index, end_index, heading_level)
    current_index = 1  # Google Docs body index starts at 1

    for text, level in segments:
        line = text + "\n"
        line_len = _utf16_len(line)
        if level > 0:
            heading_ranges.append((current_index, current_index + line_len - 1, level))
        current_index += line_len
        full_text += line

    # Google Docs API
    try:
        from services.google_docs_service import (
            _get_credentials,
            _get_user_credentials,
            DRIVE_FOLDER_ID,
        )
        import asyncio
        from googleapiclient.discovery import build as google_build
    except ImportError:
        raise HTTPException(status_code=501, detail="Google Docs integration is not configured.")

    try:
        creds = await _get_user_credentials(db, current_user.user_id)
    except FileNotFoundError:
        try:
            creds = _get_credentials()
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Google Docs is not connected. Please sign out and sign back in "
                    f"with Google to authorize. ({str(e)[:160]})"
                ),
            )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Google credentials error: {str(e)[:200]}")

    HEADING_MAP = {1: "HEADING_1", 2: "HEADING_2", 3: "HEADING_3"}

    try:
        docs_service = google_build("docs", "v1", credentials=creds)
        drive_service = google_build("drive", "v3", credentials=creds)

        doc_title = f"{client} \u2014 Vega Discovery Report \u2014 {today}"
        created_doc = await asyncio.to_thread(
            docs_service.documents().create(body={"title": doc_title}).execute
        )
        doc_id = created_doc["documentId"]
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"

        # Build batch update: insert text first, then apply heading styles
        requests = [{"insertText": {"location": {"index": 1}, "text": full_text}}]

        for start, end, level in heading_ranges:
            requests.append({
                "updateParagraphStyle": {
                    "range": {"startIndex": start, "endIndex": end},
                    "paragraphStyle": {"namedStyleType": HEADING_MAP[level]},
                    "fields": "namedStyleType",
                }
            })

        await asyncio.to_thread(
            docs_service.documents()
            .batchUpdate(documentId=doc_id, body={"requests": requests})
            .execute
        )

        # Move to shared Drive folder if configured
        if DRIVE_FOLDER_ID:
            try:
                await asyncio.to_thread(
                    drive_service.files()
                    .update(fileId=doc_id, addParents=DRIVE_FOLDER_ID, fields="id,parents")
                    .execute
                )
            except Exception as e:
                logger.warning(f"Failed to move doc to Drive folder: {e}")

        # Share with user if email available
        if hasattr(current_user, "email") and current_user.email:
            try:
                await asyncio.to_thread(
                    drive_service.permissions()
                    .create(
                        fileId=doc_id,
                        body={"type": "user", "role": "writer", "emailAddress": current_user.email},
                        sendNotificationEmail=False,
                    )
                    .execute
                )
            except Exception as e:
                logger.warning(f"Failed to share doc with {current_user.email}: {e}")

        return {"doc_url": doc_url, "doc_id": doc_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create Google Doc: {str(e)[:200]}")


# ---------------------------------------------------------------------------
# KPI Events query
# ---------------------------------------------------------------------------


@router.get("/vega-sessions/{session_id}/events")
async def get_session_events(session_id: str, current_user=Depends(get_current_user)):
    """Get KPI events for an Vega session."""
    if not has_perm(current_user, "vega_sessions.view_own"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    session = await db.vega_discovery_sessions.find_one(
        {"session_id": session_id, "user_id": current_user.user_id},
        {"_id": 0, "session_id": 1},
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    events = await db.vega_events.find(
        {"session_id": session_id},
        {"_id": 0},
    ).sort("created_at", 1).to_list(500)

    return {"events": events, "total": len(events)}
