"""Chat artifact endpoints — fetch and submit quiz/form artifacts."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
import json
import re
import uuid
import logging
import copy

from database import get_db
from auth_helper import get_current_user, has_perm

logger = logging.getLogger(__name__)
router = APIRouter()


# Skip session-pick messages tagged INTAKE SELECTION because their parenthetical
# (e.g. "Pinecrest (Call 2 approved, Apr 27)") confuses the call-number matcher.
_SESSION_PICK_RE = re.compile(r"\(Call \d+ approved")
_CALL_3_RE = re.compile(r"\bCall 3\b")
_CALL_2_RE = re.compile(r"\bCall 2\b")
_CALL_1_RE = re.compile(r"\bCall 1\b")


def _resolve_call_num_from_history(messages: list) -> int:
    """Return the Vega call number (0/1/2/3) inferred from chat history.

    `messages` is a list of {"content": str, ...} dicts ordered most-recent-first
    (as returned by Mongo .sort("created_at", -1)). Returns:
      0 — direct analysis mode
      1 — Call 1 (default if nothing matches)
      2 — Call 2
      3 — Call 3
    """
    relevant = [
        (m.get("content") or "")
        for m in messages
        if not _SESSION_PICK_RE.search(m.get("content") or "")
    ]

    call_num = 1
    for content in relevant:
        if "post_call_3" in content or _CALL_3_RE.search(content):
            call_num = 3
            break
        if "post_call_2" in content or _CALL_2_RE.search(content):
            call_num = 2
            break
        if "post_call_1" in content or _CALL_1_RE.search(content):
            call_num = 1
            break

    for content in relevant:
        if "direct_analysis" in content or "documents to analyze" in content.lower():
            return 0

    return call_num


def _strip_answers(payload: dict) -> dict:
    """Strip correct_answer, explanation, and rubric from quiz questions.

    CRITICAL SECURITY: This MUST be called when returning pending quiz artifacts.
    Without it, users can see correct answers via browser dev tools.
    """
    if not payload:
        return payload

    stripped = copy.deepcopy(payload)
    for question in stripped.get("questions", []):
        question.pop("correct_answer", None)
        question.pop("explanation", None)
        question.pop("rubric", None)
    return stripped


@router.get("/ai-chat-artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, current_user=Depends(get_current_user)):
    """Fetch a chat artifact by ID.

    SECURITY: When status is 'pending', correct_answer/explanation/rubric
    are stripped from the response to prevent cheating.
    """
    if not has_perm(current_user, "chat.access"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    artifact = await db.ai_chat_artifacts.find_one(
        {"artifact_id": artifact_id, "user_id": current_user.user_id},
        {"_id": 0},
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    # CRITICAL: Strip answers when quiz is still pending
    if artifact.get("status") == "pending" and artifact.get("payload"):
        artifact["payload"] = _strip_answers(artifact["payload"])

    return artifact


@router.post("/ai-chat-artifacts/{artifact_id}/submit")
async def submit_artifact(artifact_id: str, data: dict, current_user=Depends(get_current_user)):
    """Submit user responses, grade immediately, and inject results into chat."""
    if not has_perm(current_user, "chat.access"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    artifact = await db.ai_chat_artifacts.find_one(
        {"artifact_id": artifact_id, "user_id": current_user.user_id},
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    artifact_type = artifact.get("artifact_type")
    if not artifact_type:
        raise HTTPException(status_code=400, detail="Artifact has no type")
    session_id = data.get("session_id") or artifact.get("session_id")
    now = datetime.now(timezone.utc)

    # ── Vega: single_select / chips artifact submit ──
    if artifact_type in ("single_select", "chips"):
        selected_id = data.get("selected_id")
        if not selected_id:
            raise HTTPException(status_code=400, detail="No selected_id provided")
        claimed = await db.ai_chat_artifacts.find_one_and_update(
            {"artifact_id": artifact_id, "status": {"$ne": "submitted"}},
            {"$set": {
                "payload.selected_id": selected_id,
                "status": "submitted",
                "updated_at": now.isoformat(),
            }},
        )
        if not claimed:
            raise HTTPException(status_code=400, detail="Artifact already submitted")
        # Inject as system message so Vega's next turn sees the selection
        option_label = selected_id
        choices = (
            artifact.get("payload", {}).get("options")
            or artifact.get("payload", {}).get("chips")
            or []
        )
        for opt in choices:
            # Handle both {id, label} objects and plain strings
            if isinstance(opt, dict) and opt.get("id") == selected_id:
                option_label = opt.get("label", selected_id)
                break
            elif isinstance(opt, str) and opt == selected_id:
                option_label = opt
                break

        if session_id:
            system_msg = {
                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                "session_id": session_id,
                "user_id": current_user.user_id,
                "role": "user",
                "content": f"[INTAKE SELECTION] {option_label}",
                "created_at": now,
            }
            await db.chat_messages.insert_one(system_msg)

        # For Vega intake selections, create the next artifact directly (no LLM call).
        employee_id = artifact.get("employee_id")
        ai_response_msg = None
        payload_name = artifact.get("payload", {}).get("name", "")

        # Legacy Orion handoff — no longer used
        if employee_id == "ai_vega" and payload_name == "orion_handoff_offer":
            return {"status": "submitted", "artifact_id": artifact_id, "selected_id": selected_id}

        # Vega v2 entry chips — single_call / freeflow / more_options
        if employee_id == "ai_vega" and payload_name == "vega_entry_chips":
            chip_now = datetime.now(timezone.utc)

            if selected_id == "freeflow":
                # No artifact rendered — just a warm greeting and a discovery_mode tag
                # on chat_sessions so the conductor knows to stay conversational.
                await db.chat_sessions.update_one(
                    {"session_id": session_id, "user_id": current_user.user_id},
                    {"$set": {"discovery_mode": "freeflow", "updated_at": chip_now}},
                )
                ai_response_msg = {
                    "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                    "session_id": session_id,
                    "user_id": current_user.user_id,
                    "role": "assistant",
                    "content": (
                        "Sure — tell me about the opportunity. What's the company doing, "
                        "what's the pain you've heard so far, and where do you think AI "
                        "could fit? I'll listen and help you think through it.\n\n"
                        "Anytime you want, drop a transcript (or a few — Call 1, Call 2, "
                        "Call 3, supporting docs) using the **+** button below. From there "
                        "I can answer questions, help plan the next call, or run the "
                        "right analysis (Call 1, Call 2, Call 3, or a full single-pass)."
                    ),
                    "created_at": chip_now,
                }
                await db.chat_messages.insert_one(ai_response_msg)
                return {
                    "status": "submitted",
                    "artifact_id": artifact_id,
                    "selected_id": selected_id,
                    "ai_response": {
                        "message_id": ai_response_msg["message_id"],
                        "role": "assistant",
                        "content": ai_response_msg["content"],
                        "created_at": chip_now.isoformat(),
                    },
                }

            if selected_id == "single_call":
                # Forward to the existing direct_analysis upload-slot flow
                selected_id = "direct_analysis"
                await db.chat_sessions.update_one(
                    {"session_id": session_id, "user_id": current_user.user_id},
                    {"$set": {"discovery_mode": "single_call", "updated_at": chip_now}},
                )
                # fall through to VEGA_UPLOAD_SLOTS handling below

            elif selected_id == "more_options":
                # Render the legacy 6-option intake selector
                legacy_art_id = f"art_{uuid.uuid4().hex[:12]}"
                await db.ai_chat_artifacts.insert_one({
                    "artifact_id": legacy_art_id,
                    "session_id": session_id,
                    "user_id": current_user.user_id,
                    "employee_id": "ai_vega",
                    "artifact_type": "single_select",
                    "payload": {
                        "name": "discovery_stage",
                        "question": "Where are you in the discovery process?",
                        "options": [
                            {"id": "pre_call_1", "label": "About to do Call 1 (need help drafting an agenda)"},
                            {"id": "post_call_1", "label": "Just finished Call 1"},
                            {"id": "post_call_2", "label": "Just finished Call 2"},
                            {"id": "post_call_3", "label": "Just finished Call 3"},
                            {"id": "direct_analysis", "label": "I have documents or transcripts to analyze directly"},
                            {"id": "resume_session", "label": "I have prior session data to resume"},
                        ],
                    },
                    "status": "delivered",
                    "created_at": chip_now,
                    "updated_at": chip_now,
                })
                ai_response_msg = {
                    "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                    "session_id": session_id,
                    "user_id": current_user.user_id,
                    "role": "assistant",
                    "content": f"Pick the option that fits:\n\n[[artifact:{legacy_art_id}]]",
                    "created_at": chip_now,
                }
                await db.chat_messages.insert_one(ai_response_msg)
                return {
                    "status": "submitted",
                    "artifact_id": artifact_id,
                    "selected_id": selected_id,
                    "ai_response": {
                        "message_id": ai_response_msg["message_id"],
                        "role": "assistant",
                        "content": ai_response_msg["content"],
                        "created_at": chip_now.isoformat(),
                    },
                }

        # Handle opportunity picker response — user selected which session to continue
        if employee_id == "ai_vega" and payload_name == "pick_opportunity":
            # Determine which call the user originally selected from chat history
            hist = await db.chat_messages.find(
                {"session_id": session_id, "content": {"$regex": "^\\[INTAKE SELECTION\\]"}},
                {"_id": 0, "content": 1},
            ).sort("created_at", -1).to_list(5)
            call_type = "post_call_3" if _resolve_call_num_from_history(hist) == 3 else "post_call_2"

            if selected_id == "manual_upload":
                # User wants to upload docs manually — show full upload slots
                # No linked session — user will provide all docs themselves
                linked_vega_session_id = None
                full_slots = {
                    "post_call_2": [
                        {"slot_id": "call_1_doc", "label": "Call 1 Vega Analysis", "required": True, "accepted_extensions": [".txt", ".md", ".docx", ".pdf"]},
                        {"slot_id": "call_2_transcript", "label": "Call 2 Transcript", "required": True, "accepted_extensions": [".txt", ".md", ".docx", ".pdf"]},
                    ],
                    "post_call_3": [
                        {"slot_id": "call_1_doc", "label": "Call 1 Vega Analysis", "required": True, "accepted_extensions": [".txt", ".md", ".docx", ".pdf"]},
                        {"slot_id": "call_2_doc", "label": "Call 2 Vega Analysis", "required": True, "accepted_extensions": [".txt", ".md", ".docx", ".pdf"]},
                        {"slot_id": "call_3_transcript", "label": "Call 3 Transcript", "required": True, "accepted_extensions": [".txt", ".md", ".docx", ".pdf"]},
                    ],
                }
                slots = full_slots.get(call_type, full_slots["post_call_2"])
                text = "Upload all required documents."
            else:
                # User picked a specific session — only need the new transcript
                linked_vega_session_id = selected_id
                call_num = "2" if call_type == "post_call_2" else "3"
                slots = [{"slot_id": f"call_{call_num}_transcript", "label": f"Call {call_num} Transcript",
                          "required": True, "accepted_extensions": [".txt", ".md", ".docx", ".pdf"]}]
                text = f"Using your selected session. Just upload the Call {call_num} transcript."

            art_id = f"art_{uuid.uuid4().hex[:12]}"
            art_now = datetime.now(timezone.utc)
            await db.ai_chat_artifacts.insert_one({
                "artifact_id": art_id, "session_id": session_id,
                "user_id": current_user.user_id, "employee_id": employee_id,
                "artifact_type": "multi_upload",
                "payload": {"title": "Document Upload", "slots": slots,
                            "linked_vega_session_id": linked_vega_session_id},
                "status": "delivered", "created_at": art_now, "updated_at": art_now,
            })
            ai_response_msg = {
                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                "session_id": session_id, "user_id": current_user.user_id,
                "role": "assistant", "content": f"{text}\n\n[[artifact:{art_id}]]",
                "created_at": art_now,
            }
            await db.chat_messages.insert_one(ai_response_msg)
            result = {"status": "submitted", "artifact_id": artifact_id, "selected_id": selected_id}
            result["ai_response"] = {
                "message_id": ai_response_msg["message_id"], "role": "assistant",
                "content": ai_response_msg["content"], "created_at": art_now.isoformat(),
            }
            return result

        # Map Vega intake options to static upload slot configurations
        VEGA_UPLOAD_SLOTS = {
            "direct_analysis": {
                "text": "Upload any transcripts, documents, or supporting materials. I'll analyze them and generate a comprehensive discovery report with BPMNs and design recommendations.",
                "title": "Direct Document Analysis",
                "slots": [
                    {"slot_id": "documents", "label": "Documents to Analyze", "required": True,
                     "accepted_extensions": [".txt", ".md", ".docx", ".pdf", ".csv", ".xlsx"]},
                    {"slot_id": "supporting_docs", "label": "Additional Supporting Documents (optional)",
                     "required": False,
                     "accepted_extensions": [".txt", ".md", ".docx", ".pdf", ".csv", ".xlsx", ".bpmn", ".xml"]},
                ],
            },
            "post_call_1": {
                "text": "Thank you! Please upload the Call 1 transcript.",
                "title": "Document Upload",
                "slots": [
                    {"slot_id": "call_1_transcript", "label": "Call 1 Transcript", "required": True,
                     "accepted_extensions": [".txt", ".md", ".docx", ".pdf"]},
                    {"slot_id": "supporting_docs", "label": "Supporting Documents (optional)",
                     "required": False,
                     "accepted_extensions": [".txt", ".md", ".docx", ".pdf", ".csv", ".xlsx", ".bpmn", ".xml"]},
                ],
            },
            "post_call_2": {
                "text": "Please upload the Call 1 analysis doc and Call 2 transcript.",
                "title": "Document Upload",
                "slots": [
                    {"slot_id": "call_1_doc", "label": "Call 1 Vega Analysis", "required": True,
                     "accepted_extensions": [".txt", ".md", ".docx", ".pdf"]},
                    {"slot_id": "call_2_transcript", "label": "Call 2 Transcript", "required": True,
                     "accepted_extensions": [".txt", ".md", ".docx", ".pdf"]},
                    {"slot_id": "supporting_docs", "label": "Supporting Documents (optional)",
                     "required": False,
                     "accepted_extensions": [".txt", ".md", ".docx", ".pdf", ".csv", ".xlsx", ".bpmn", ".xml"]},
                ],
            },
            "post_call_3": {
                "text": "Please upload all three documents.",
                "title": "Document Upload",
                "slots": [
                    {"slot_id": "call_1_doc", "label": "Call 1 Vega Analysis", "required": True,
                     "accepted_extensions": [".txt", ".md", ".docx", ".pdf"]},
                    {"slot_id": "call_2_doc", "label": "Call 2 Vega Analysis", "required": True,
                     "accepted_extensions": [".txt", ".md", ".docx", ".pdf"]},
                    {"slot_id": "call_3_transcript", "label": "Call 3 Transcript", "required": True,
                     "accepted_extensions": [".txt", ".md", ".docx", ".pdf"]},
                    {"slot_id": "bpmn_edits", "label": "Edited BPMN Diagrams from Call 2 (optional)",
                     "required": False,
                     "accepted_extensions": [".bpmn", ".xml"]},
                    {"slot_id": "supporting_docs", "label": "Supporting Documents (optional)",
                     "required": False,
                     "accepted_extensions": [".txt", ".md", ".docx", ".pdf", ".csv", ".xlsx", ".bpmn", ".xml"]},
                ],
            },
        }

        if employee_id == "ai_vega" and selected_id in VEGA_UPLOAD_SLOTS:
            # Check for existing approved sessions — reduce upload slots if prior data exists
            config = VEGA_UPLOAD_SLOTS[selected_id].copy()
            config["slots"] = [dict(s) for s in config["slots"]]  # deep copy slots

            # Find prior sessions with approved calls (last 2 weeks only)
            from datetime import timedelta
            two_weeks_ago = (datetime.now(timezone.utc) - timedelta(weeks=2)).isoformat()
            prior_sessions = await db.vega_discovery_sessions.find(
                {"user_id": current_user.user_id, "status": {"$in": ["in_progress", "complete"]},
                 "updated_at": {"$gte": two_weeks_ago}},
                {"_id": 0, "session_id": 1, "calls": 1, "created_at": 1},
            ).sort("updated_at", -1).to_list(20)

            # Filter to sessions that have the needed prior calls approved
            sessions_with_call_1 = [s for s in prior_sessions if s.get("calls", {}).get("1", {}).get("user_approved")]
            sessions_with_call_2 = [s for s in prior_sessions if s.get("calls", {}).get("2", {}).get("user_approved")]

            # Always let user choose which prior session to use (or upload manually)
            # For Call 3, need sessions with Call 2 approved; for Call 2, need Call 1 approved
            relevant_sessions = sessions_with_call_2 if selected_id == "post_call_3" else sessions_with_call_1
            if selected_id in ("post_call_2", "post_call_3") and len(relevant_sessions) >= 1:
                # Build descriptive labels with client name and date
                pick_art_id = f"art_{uuid.uuid4().hex[:12]}"
                pick_now = datetime.now(timezone.utc)
                options = []
                for s in relevant_sessions[:10]:
                    calls = s.get("calls", {})
                    # Find highest approved call and extract client name
                    approved_calls = sorted(
                        [cn for cn, cd in calls.items() if cd.get("user_approved")],
                        key=int,
                    )
                    highest = approved_calls[-1] if approved_calls else "1"
                    # Try to get client name from analysis
                    analysis = calls.get("1", {}).get("analysis", {})
                    client = (analysis.get("client_name", "") or "").strip()
                    if not client:
                        summary = (analysis.get("summary", "") or "")
                        if "with " in summary:
                            client = summary.split("with ", 1)[1].split(".")[0].strip()
                        else:
                            client = summary[:40] or s["session_id"]
                    # Get approval date for the highest call
                    approved_at = calls.get(highest, {}).get("approved_at", "")
                    date_str = ""
                    if approved_at:
                        try:
                            d = datetime.fromisoformat(str(approved_at).replace("Z", "+00:00"))
                            date_str = d.strftime("%b %d")
                        except (ValueError, TypeError):
                            pass
                    label = f"{client} (Call {highest} approved{', ' + date_str if date_str else ''})"
                    options.append({"id": s["session_id"], "label": label})
                # Add manual upload option
                options.append({"id": "manual_upload", "label": "Upload previous call docs manually"})
                await db.ai_chat_artifacts.insert_one({
                    "artifact_id": pick_art_id,
                    "session_id": session_id,
                    "user_id": current_user.user_id,
                    "employee_id": employee_id,
                    "artifact_type": "single_select",
                    "payload": {
                        "name": "pick_opportunity",
                        "question": "Which opportunity are you continuing?",
                        "options": options,
                    },
                    "status": "delivered",
                    "created_at": pick_now,
                    "updated_at": pick_now,
                })
                ai_response_msg = {
                    "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                    "session_id": session_id,
                    "user_id": current_user.user_id,
                    "role": "assistant",
                    "content": f"I found multiple active discovery sessions. Which one are you continuing?\n\n[[artifact:{pick_art_id}]]",
                    "created_at": pick_now,
                }
                await db.chat_messages.insert_one(ai_response_msg)
                result = {"status": "submitted", "artifact_id": artifact_id, "selected_id": selected_id}
                result["ai_response"] = {
                    "message_id": ai_response_msg["message_id"],
                    "role": "assistant",
                    "content": ai_response_msg["content"],
                    "created_at": pick_now.isoformat(),
                }
                return result

            # Single or zero prior sessions — auto-select and reduce upload slots
            # Track which Vega session is linked so downstream analysis is scoped
            auto_linked_vega_session_id = None
            if selected_id == "post_call_2" and sessions_with_call_1:
                auto_linked_vega_session_id = sessions_with_call_1[0]["session_id"]
                config["slots"] = [s for s in config["slots"] if s["slot_id"] != "call_1_doc"]
                config["text"] = "I found your approved Call 1 analysis. Just upload the Call 2 transcript."
            elif selected_id == "post_call_3":
                if sessions_with_call_1 and sessions_with_call_2:
                    auto_linked_vega_session_id = sessions_with_call_2[0]["session_id"]
                    config["slots"] = [s for s in config["slots"] if s["slot_id"] == "call_3_transcript"]
                    config["text"] = "I found your approved Call 1 and Call 2 analyses. Just upload the Call 3 transcript."
                elif sessions_with_call_1:
                    auto_linked_vega_session_id = sessions_with_call_1[0]["session_id"]
                    config["slots"] = [s for s in config["slots"] if s["slot_id"] != "call_1_doc"]
                    config["text"] = "I found your approved Call 1 analysis. Upload the Call 2 analysis and Call 3 transcript."
                # Note: if only sessions_with_call_2 exists (no Call 1), no slots are
                # reduced and auto_linked_vega_session_id stays None — the user must
                # upload all docs manually since Call 1 analysis is a prerequisite.

            # No linked session for post_call_1/direct_analysis (fresh start)
            art_id = f"art_{uuid.uuid4().hex[:12]}"
            art_now = datetime.now(timezone.utc)
            await db.ai_chat_artifacts.insert_one({
                "artifact_id": art_id,
                "session_id": session_id,
                "user_id": current_user.user_id,
                "employee_id": employee_id,
                "artifact_type": "multi_upload",
                "payload": {"title": config["title"], "slots": config["slots"],
                            "linked_vega_session_id": auto_linked_vega_session_id},
                "status": "delivered",
                "created_at": art_now,
                "updated_at": art_now,
            })
            ai_response_msg = {
                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                "session_id": session_id,
                "user_id": current_user.user_id,
                "role": "assistant",
                "content": f"{config['text']}\n\n[[artifact:{art_id}]]",
                "created_at": art_now,
            }
            await db.chat_messages.insert_one(ai_response_msg)

        elif session_id and employee_id:
            # Dynamic response — use LLM (e.g., "About to do Call 1", "Resume session")
            try:
                from services.ai.agents.registry import get_agent
                from services.ai.conductor import _run_agent_turn

                agent = get_agent(f"emp_{employee_id}")
                if agent:
                    history = await db.chat_messages.find(
                        {"session_id": session_id}, {"_id": 0, "role": 1, "content": 1}
                    ).sort("created_at", 1).to_list(50)

                    response_text, _, _ = await _run_agent_turn(
                        agent, current_user,
                        history[-1]["content"] if history else f"[INTAKE SELECTION] {option_label}",
                        history=history[:-1][-16:],
                        db=db, session_id=session_id, invocation_depth=0,
                    )
                    if response_text and response_text.strip():
                        ai_response_msg = {
                            "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                            "session_id": session_id,
                            "user_id": current_user.user_id,
                            "role": "assistant",
                            "content": response_text,
                            "created_at": datetime.now(timezone.utc),
                        }
                        await db.chat_messages.insert_one(ai_response_msg)
            except Exception as e:
                logger.warning(f"Auto-response after intake selection failed: {e}")

        result = {"status": "submitted", "artifact_id": artifact_id, "selected_id": selected_id}
        if ai_response_msg:
            result["ai_response"] = {
                "message_id": ai_response_msg["message_id"],
                "role": "assistant",
                "content": ai_response_msg["content"],
                "created_at": ai_response_msg["created_at"].isoformat() if hasattr(ai_response_msg["created_at"], 'isoformat') else ai_response_msg["created_at"],
            }
        return result

    # ── Vega: multi_upload artifact submit ──
    if artifact_type == "multi_upload":
        upload_ids = data.get("upload_ids", [])
        if not upload_ids:
            raise HTTPException(status_code=400, detail="No upload_ids provided")
        # Cross-session contamination guard: every upload must belong to this
        # artifact's session AND the current user. Without this, a user could
        # submit upload_ids from another of their own sessions into this one,
        # corrupting the analysis with the wrong transcripts.
        if session_id:
            valid_count = await db.chat_uploads.count_documents({
                "upload_id": {"$in": upload_ids},
                "session_id": session_id,
                "user_id": current_user.user_id,
            })
            if valid_count != len(upload_ids):
                raise HTTPException(
                    status_code=400,
                    detail=f"One or more upload_ids do not belong to this session ({valid_count}/{len(upload_ids)} matched).",
                )
        claimed = await db.ai_chat_artifacts.find_one_and_update(
            {"artifact_id": artifact_id, "status": {"$ne": "submitted"}},
            {"$set": {
                "payload.submitted": True,
                "status": "submitted",
                "updated_at": now.isoformat(),
            }},
        )
        if not claimed:
            raise HTTPException(status_code=400, detail="Artifact already submitted")
        # Inject as system message with upload references + prior call data if available
        if session_id:
            markers = " ".join(f"[upload_id={uid}]" for uid in upload_ids)
            prior_context = ""

            # For Vega: only inject prior call analyses from the explicitly linked session.
            # Legacy artifacts (created before this fix) won't have the key at all —
            # fall back to the old global lookup so in-flight workflows aren't broken.
            emp_id = artifact.get("employee_id")
            vega_payload = artifact.get("payload", {})
            if emp_id == "ai_vega":
                has_link_key = "linked_vega_session_id" in vega_payload
                linked_vega_sid = vega_payload.get("linked_vega_session_id")

                if linked_vega_sid:
                    # Explicit link — scope to that session only
                    prior_session_query = {"session_id": linked_vega_sid, "user_id": current_user.user_id}
                elif not has_link_key:
                    # Legacy artifact — fall back to global lookup for backwards compat
                    prior_session_query = {"user_id": current_user.user_id, "status": {"$in": ["in_progress", "complete"]}}
                else:
                    # Key present but None — fresh start (post_call_1/direct_analysis), no prior context
                    prior_session_query = None

                if prior_session_query:
                    prior_sessions = await db.vega_discovery_sessions.find(
                        prior_session_query, {"_id": 0, "calls": 1},
                    ).sort("updated_at", -1).to_list(1)
                    for ps in prior_sessions:
                        for call_num in ("1", "2"):
                            call_data = ps.get("calls", {}).get(call_num, {})
                            if call_data.get("user_approved") and call_data.get("analysis"):
                                prior_context += f"\n\n[PRIOR CALL {call_num} ANALYSIS]\n{json.dumps(call_data['analysis'], default=str)}"

            system_msg = {
                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                "session_id": session_id,
                "user_id": current_user.user_id,
                "role": "user",
                "content": f"[UPLOADS SUBMITTED] {markers}{prior_context}",
                "created_at": now,
            }
            await db.chat_messages.insert_one(system_msg)

        # Auto-trigger AI analysis of the uploaded transcripts
        ai_response_msg = None
        employee_id = artifact.get("employee_id")

        # Direct structured analysis for Vega — no tool calling needed
        if session_id and employee_id == "ai_vega":
            try:
                # Vega v2: if the chat session is tagged single_call (set by the chip
                # selection), short-circuit to direct-analysis routing so the
                # single_call pipeline runs regardless of intake-message wording.
                _chat_doc_for_mode = await db.chat_sessions.find_one(
                    {"session_id": session_id, "user_id": current_user.user_id},
                    {"_id": 0, "discovery_mode": 1},
                )
                _v2_single_call = (_chat_doc_for_mode or {}).get("discovery_mode") == "single_call"

                if _v2_single_call:
                    call_num = 0
                else:
                    # Determine call number from chat history (look for intake selection)
                    history = await db.chat_messages.find(
                        {"session_id": session_id, "content": {"$regex": "^\\[INTAKE SELECTION\\]"}},
                        {"_id": 0, "content": 1, "created_at": 1},
                    ).sort("created_at", -1).to_list(5)
                    call_num = _resolve_call_num_from_history(history)

                # Use the linked Vega session from the artifact if available.
                # Legacy artifacts (no linked_vega_session_id key) fall back to global
                # lookup so in-flight workflows aren't broken mid-deploy.
                vega_payload = artifact.get("payload", {})
                has_link_key = "linked_vega_session_id" in vega_payload
                linked_vega_sid = vega_payload.get("linked_vega_session_id")

                if linked_vega_sid:
                    # Explicit link from pick_opportunity or auto-select
                    vega_session = await db.vega_discovery_sessions.find_one(
                        {"session_id": linked_vega_sid, "user_id": current_user.user_id},
                        {"_id": 0, "session_id": 1, "client_company": 1, "opportunity_name": 1, "calls.1.analysis.client_name": 1},
                    )
                elif not has_link_key:
                    # Legacy artifact — preserve old behavior
                    vega_session = await db.vega_discovery_sessions.find_one(
                        {"user_id": current_user.user_id, "status": "in_progress"},
                        {"_id": 0, "session_id": 1, "client_company": 1, "opportunity_name": 1, "calls.1.analysis.client_name": 1},
                        sort=[("updated_at", -1)],
                    )
                else:
                    # Key present but None — fresh start, scope to this chat session only
                    vega_session = await db.vega_discovery_sessions.find_one(
                        {"user_id": current_user.user_id, "chat_session_id": session_id, "status": "in_progress"},
                        {"_id": 0, "session_id": 1, "client_company": 1, "opportunity_name": 1, "calls.1.analysis.client_name": 1},
                        sort=[("updated_at", -1)],
                    )
                vega_session_id = vega_session["session_id"] if vega_session else None
                _vega_client_name = (
                    (vega_session or {}).get("client_company")
                    or (vega_session or {}).get("calls", {}).get("1", {}).get("analysis", {}).get("client_name")
                    or (vega_session or {}).get("opportunity_name")
                    or ""
                )

                # Wave 6b — conversational vs chip-driven entry detection.
                # The chip-driven legacy path (multi_upload generated by an
                # intake-selection chip) keeps the v2 pipelines verbatim. When
                # Vega's tool-calling flow conversationally generates a
                # multi_upload artifact mid-thread, it sets
                # `payload.discovery_mode_origin = "conversational"`; we route
                # those to the new v3 per-call modes (run_v3_call1/2/3) and
                # persist as discovery_analysis_v3_callN.
                _discovery_mode_origin = (artifact.get("payload") or {}).get(
                    "discovery_mode_origin", "chip_legacy"
                )
                _is_conversational = _discovery_mode_origin == "conversational"

                # ── Helper: post an error message so the user is never left with silence ──
                async def _post_error(msg_text: str):
                    await db.chat_messages.insert_one({
                        "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                        "session_id": session_id,
                        "user_id": current_user.user_id,
                        "role": "assistant",
                        "content": msg_text,
                        "created_at": datetime.now(timezone.utc),
                    })

                # ── Wave 6b helpers: find prior call N artifact for the
                # session (used by conversational call2/call3 routing). Looks
                # up the most recent discovery_analysis_v3_call{N} artifact
                # scoped to this Vega discovery session. Returns None when no
                # prior artifact exists — the v3 call mode then runs without
                # prior context (a valid "fresh Call 2 without a Call 1"
                # mode). ──
                async def _find_prior_v3_call_artifact(prior_call_n: int):
                    if not vega_session_id:
                        return None
                    prior_doc = await db.ai_chat_artifacts.find_one(
                        {
                            "user_id": current_user.user_id,
                            "artifact_type": f"discovery_analysis_v3_call{prior_call_n}",
                            "payload.session_id": vega_session_id,
                        },
                        {"_id": 0, "artifact_id": 1},
                        sort=[("created_at", -1)],
                    )
                    return (prior_doc or {}).get("artifact_id")

                # ── Wave 6b helper: persist a v3 call-mode artifact +
                # success chat message. Centralized so each call_num branch
                # below stays readable. ──
                async def _persist_v3_call_artifact(
                    v3_artifact, call_n: int, prior_artifact_id=None,
                ):
                    art_id = f"art_{uuid.uuid4().hex[:12]}"
                    art_now = datetime.now(timezone.utc)
                    payload = v3_artifact.model_dump(mode="json")
                    payload["session_id"] = vega_session_id
                    payload["call_number"] = call_n
                    payload["discovery_mode_origin"] = "conversational"
                    if prior_artifact_id:
                        payload["prior_call_artifact_id"] = prior_artifact_id
                    await db.ai_chat_artifacts.insert_one({
                        "artifact_id": art_id,
                        "session_id": session_id,
                        "user_id": current_user.user_id,
                        "employee_id": employee_id,
                        "artifact_type": f"discovery_analysis_v3_call{call_n}",
                        "payload": payload,
                        "status": "delivered",
                        "created_at": art_now,
                        "updated_at": art_now,
                    })

                    # Wave 7 fix — auto-name the Vega session if it doesn't
                    # have one yet. Belt-and-suspenders for the
                    # name_vega_session conversational tool: if Vega forgot
                    # to call it (or hasn't yet), the pipeline completion
                    # always gives us a real client_company to fall back
                    # on. Only sets when the override is currently empty.
                    if vega_session_id and v3_artifact.client_company:
                        await db.vega_discovery_sessions.update_one(
                            {"session_id": vega_session_id,
                             "user_id": current_user.user_id,
                             "$or": [
                                 {"session_name_override": {"$exists": False}},
                                 {"session_name_override": None},
                                 {"session_name_override": ""},
                             ]},
                            {"$set": {
                                "session_name_override": v3_artifact.client_company.strip(),
                                "client_company": v3_artifact.client_company.strip(),
                                "updated_at": art_now,
                            }},
                        )

                    wf_count = len(v3_artifact.workflows)
                    app_count = len(v3_artifact.apps)
                    partial_note = (
                        " (some workflows or apps were partial — see Open Items)"
                        if v3_artifact.extraction_status == "partial"
                        else ""
                    )
                    if call_n == 1:
                        response_text = (
                            f"Call 1 discovery complete — surfaced {wf_count} workflow"
                            f"{'s' if wf_count != 1 else ''} and {app_count} app"
                            f"{'s' if app_count != 1 else ''}{partial_note}. "
                            f"Review the analysis and we can go deeper on Call 2."
                            f"\n\n[[artifact:{art_id}]]"
                        )
                    elif call_n == 2:
                        response_text = (
                            f"Call 2 depth pass complete — added MMI scoring and process "
                            f"detail across {wf_count} workflow"
                            f"{'s' if wf_count != 1 else ''}{partial_note}. "
                            f"Ready for Call 3 to finalize."
                            f"\n\n[[artifact:{art_id}]]"
                        )
                    else:  # call_n == 3
                        response_text = (
                            f"Call 3 final pass complete — design docs and BPMN diagrams "
                            f"generated for {wf_count} workflow"
                            f"{'s' if wf_count != 1 else ''}{partial_note}. "
                            f"Review and approve."
                            f"\n\n[[artifact:{art_id}]]"
                        )
                    msg = {
                        "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                        "session_id": session_id,
                        "user_id": current_user.user_id,
                        "role": "assistant",
                        "content": response_text,
                        "created_at": art_now,
                    }
                    await db.chat_messages.insert_one(msg)
                    return msg

                # ── Link the chat session to the Vega discovery session ──
                if vega_session_id:
                    await db.vega_discovery_sessions.update_one(
                        {"session_id": vega_session_id, "user_id": current_user.user_id},
                        {"$set": {"chat_session_id": session_id}},
                    )

                if call_num == 0:
                    # Direct analysis mode. Two flavors:
                    #   - v2 single_call (chip selection set discovery_mode=single_call
                    #     on the chat_sessions doc): run the FULL deliverable in one
                    #     pass — matrix + MMI + BPMN + AI Design Documents.
                    #   - legacy direct_analysis: just run Call 1 analysis.
                    chat_doc = await db.chat_sessions.find_one(
                        {"session_id": session_id, "user_id": current_user.user_id},
                        {"_id": 0, "discovery_mode": 1},
                    )
                    is_single_call = (chat_doc or {}).get("discovery_mode") == "single_call"

                    if is_single_call:
                        from services.vega.v3_pipeline import run_v3_pipeline
                        try:
                            v3_artifact = await run_v3_pipeline(
                                db=db, user=current_user, upload_ids=upload_ids,
                                chat_session_id=session_id,
                                client_company=_vega_client_name or None,
                            )
                        except Exception as exc:
                            logger.exception("v3 pipeline failed: %s", exc)
                            await _post_error(
                                f"I ran into an issue generating the full analysis: {exc}"
                            )
                        else:
                            art_id = f"art_{uuid.uuid4().hex[:12]}"
                            art_now = datetime.now(timezone.utc)
                            payload = v3_artifact.model_dump(mode="json")
                            payload["session_id"] = vega_session_id
                            payload["discovery_mode"] = "single_call"
                            await db.ai_chat_artifacts.insert_one({
                                "artifact_id": art_id, "session_id": session_id,
                                "user_id": current_user.user_id, "employee_id": employee_id,
                                "artifact_type": "discovery_analysis_v3",
                                "payload": payload,
                                "status": "delivered", "created_at": art_now, "updated_at": art_now,
                            })
                            # Wave 7 fix — auto-name single_call sessions
                            # the same way we do for per-call artifacts.
                            if vega_session_id and v3_artifact.client_company:
                                await db.vega_discovery_sessions.update_one(
                                    {"session_id": vega_session_id,
                                     "user_id": current_user.user_id,
                                     "$or": [
                                         {"session_name_override": {"$exists": False}},
                                         {"session_name_override": None},
                                         {"session_name_override": ""},
                                     ]},
                                    {"$set": {
                                        "session_name_override": v3_artifact.client_company.strip(),
                                        "client_company": v3_artifact.client_company.strip(),
                                        "updated_at": art_now,
                                    }},
                                )
                            wf_count = len(v3_artifact.workflows)
                            app_count = len(v3_artifact.apps)
                            partial_note = (
                                " (some workflows or apps were partial — see Open Items)"
                                if v3_artifact.extraction_status == "partial"
                                else ""
                            )
                            response_text = (
                                f"Full analysis complete — extracted {wf_count} workflow"
                                f"{'s' if wf_count != 1 else ''} and {app_count} app"
                                f"{'s' if app_count != 1 else ''}{partial_note}.\n\n[[artifact:{art_id}]]"
                            )
                            ai_response_msg = {
                                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                                "session_id": session_id, "user_id": current_user.user_id,
                                "role": "assistant", "content": response_text, "created_at": art_now,
                            }
                            await db.chat_messages.insert_one(ai_response_msg)
                    else:
                        # Legacy direct_analysis — Call 1 only
                        from services.ai.vega_analysis import run_transcript_analysis, run_with_retry
                        result = await run_with_retry(run_transcript_analysis, db, current_user, upload_ids, call_number=1, session_id=vega_session_id, call_num=1)
                        if result and not result.get("error") and result.get("analysis"):
                            art_id = f"art_{uuid.uuid4().hex[:12]}"
                            art_now = datetime.now(timezone.utc)
                            await db.ai_chat_artifacts.insert_one({
                                "artifact_id": art_id, "session_id": session_id,
                                "user_id": current_user.user_id, "employee_id": employee_id,
                                "artifact_type": "transcript_analysis",
                                "payload": {"call_number": 1, "analysis": result["analysis"], "mode": "direct_analysis", "session_id": vega_session_id},
                                "status": "delivered", "created_at": art_now, "updated_at": art_now,
                            })
                            response_text = f"I've analyzed your documents and generated a comprehensive discovery report. Review and edit before approving.\n\n[[artifact:{art_id}]]"
                            ai_response_msg = {
                                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                                "session_id": session_id, "user_id": current_user.user_id,
                                "role": "assistant", "content": response_text, "created_at": art_now,
                            }
                            await db.chat_messages.insert_one(ai_response_msg)
                        elif result and result.get("error"):
                            await _post_error(f"I encountered an issue analyzing the documents: {result['error']}")
                        else:
                            await _post_error("I wasn't able to analyze the documents. Please try uploading them again.")

                elif call_num == 1:
                    if _is_conversational:
                        # Wave 6b — conversational mode routes Call 1 to the
                        # new v3 per-call pipeline. Persists as
                        # `discovery_analysis_v3_call1`. Chip-driven legacy
                        # path keeps the v2 transcript_analysis flow below.
                        from services.vega.v3_call_modes import run_v3_call1
                        try:
                            v3_artifact = await run_v3_call1(
                                db=db, user=current_user, upload_ids=upload_ids,
                                chat_session_id=session_id,
                                client_company=_vega_client_name or None,
                            )
                        except Exception as exc:
                            logger.exception("v3 call1 pipeline failed: %s", exc)
                            await _post_error(
                                f"I ran into an issue running the Call 1 analysis: {exc}"
                            )
                        else:
                            ai_response_msg = await _persist_v3_call_artifact(
                                v3_artifact, call_n=1, prior_artifact_id=None,
                            )
                    else:
                        from services.ai.vega_analysis import run_transcript_analysis, run_with_retry
                        result = await run_with_retry(run_transcript_analysis, db, current_user, upload_ids, call_number=1, session_id=vega_session_id, call_num=call_num)
                        if result and not result.get("error") and result.get("analysis"):
                            art_id = f"art_{uuid.uuid4().hex[:12]}"
                            art_now = datetime.now(timezone.utc)
                            await db.ai_chat_artifacts.insert_one({
                                "artifact_id": art_id, "session_id": session_id,
                                "user_id": current_user.user_id, "employee_id": employee_id,
                                "artifact_type": "transcript_analysis",
                                "payload": {"call_number": 1, "analysis": result["analysis"], "session_id": vega_session_id},
                                "status": "delivered", "created_at": art_now, "updated_at": art_now,
                            })
                            response_text = f"I've analyzed the Call 1 transcript. Please review and edit before approving.\n\n[[artifact:{art_id}]]"
                            ai_response_msg = {
                                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                                "session_id": session_id, "user_id": current_user.user_id,
                                "role": "assistant", "content": response_text, "created_at": art_now,
                            }
                            await db.chat_messages.insert_one(ai_response_msg)
                        elif result and result.get("error"):
                            await _post_error(f"I encountered an issue analyzing the transcript: {result['error']}")
                        else:
                            await _post_error("I wasn't able to analyze the Call 1 transcript. Please try uploading it again.")

                elif call_num == 2:
                    if _is_conversational:
                        # Wave 6b — conversational mode routes Call 2 to
                        # `run_v3_call2`, threading the prior Call 1 artifact
                        # (looked up by Vega session) for cross-call context.
                        # If no prior Call 1 artifact exists for this session,
                        # we still run — that's a valid "fresh Call 2 without
                        # a Call 1" mode (no prior context, just the new
                        # transcript).
                        from services.vega.v3_call_modes import run_v3_call2
                        prior_call1_artifact_id = await _find_prior_v3_call_artifact(1)
                        try:
                            v3_artifact = await run_v3_call2(
                                db=db, user=current_user, upload_ids=upload_ids,
                                prior_call1_artifact_id=prior_call1_artifact_id,
                                chat_session_id=session_id,
                                client_company=_vega_client_name or None,
                            )
                        except Exception as exc:
                            logger.exception("v3 call2 pipeline failed: %s", exc)
                            await _post_error(
                                f"I ran into an issue running the Call 2 analysis: {exc}"
                            )
                        else:
                            ai_response_msg = await _persist_v3_call_artifact(
                                v3_artifact, call_n=2,
                                prior_artifact_id=prior_call1_artifact_id,
                            )
                    else:
                        from services.ai.vega_analysis import run_call2_analysis, run_with_retry
                        result = await run_with_retry(run_call2_analysis, db, current_user, upload_ids, call_number=2, session_id=vega_session_id, call_num=call_num)
                        if result and result.get("error"):
                            await _post_error(f"I encountered an issue analyzing the transcript: {result['error']}")
                        elif result and result.get("matrix_data"):
                            art_id = f"art_{uuid.uuid4().hex[:12]}"
                            art_now = datetime.now(timezone.utc)
                            matrix_payload = {**result["matrix_data"], "call_number": 2, "session_id": vega_session_id, "client_name": _vega_client_name}
                            await db.ai_chat_artifacts.insert_one({
                                "artifact_id": art_id, "session_id": session_id,
                                "user_id": current_user.user_id, "employee_id": employee_id,
                                "artifact_type": "ai_employee_matrix",
                                "payload": matrix_payload,
                                "status": "delivered", "created_at": art_now, "updated_at": art_now,
                            })
                            response_text = f"I've built the AI Employee Matrix from Call 2. Review the workflows, MMI estimates, and BPMN diagrams below.\n\n[[artifact:{art_id}]]"
                            ai_response_msg = {
                                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                                "session_id": session_id, "user_id": current_user.user_id,
                                "role": "assistant", "content": response_text, "created_at": art_now,
                            }
                            await db.chat_messages.insert_one(ai_response_msg)
                        else:
                            await _post_error("I wasn't able to build the AI Employee Matrix from Call 2. Please try uploading the transcript again.")

                elif call_num == 3:
                    if _is_conversational:
                        # Wave 6b — conversational mode routes Call 3 to
                        # `run_v3_call3`, threading the prior Call 2 artifact
                        # for contradiction resolution. Same fall-through as
                        # Call 2 when no prior Call 2 artifact exists.
                        from services.vega.v3_call_modes import run_v3_call3
                        prior_call2_artifact_id = await _find_prior_v3_call_artifact(2)
                        try:
                            v3_artifact = await run_v3_call3(
                                db=db, user=current_user, upload_ids=upload_ids,
                                prior_call2_artifact_id=prior_call2_artifact_id,
                                chat_session_id=session_id,
                                client_company=_vega_client_name or None,
                            )
                        except Exception as exc:
                            logger.exception("v3 call3 pipeline failed: %s", exc)
                            await _post_error(
                                f"I ran into an issue running the Call 3 analysis: {exc}"
                            )
                        else:
                            ai_response_msg = await _persist_v3_call_artifact(
                                v3_artifact, call_n=3,
                                prior_artifact_id=prior_call2_artifact_id,
                            )
                    else:
                        from services.ai.vega_analysis import run_call3_analysis, run_with_retry
                        result = await run_with_retry(run_call3_analysis, db, current_user, upload_ids, call_number=3, session_id=vega_session_id, call_num=call_num)
                        if result and result.get("error"):
                            await _post_error(f"I encountered an issue analyzing the transcript: {result['error']}")
                        elif result and result.get("matrix_data"):
                            art_id = f"art_{uuid.uuid4().hex[:12]}"
                            art_now = datetime.now(timezone.utc)
                            matrix_payload = {**result["matrix_data"], "call_number": 3, "session_id": vega_session_id, "client_name": _vega_client_name}
                            await db.ai_chat_artifacts.insert_one({
                                "artifact_id": art_id, "session_id": session_id,
                                "user_id": current_user.user_id, "employee_id": employee_id,
                                "artifact_type": "ai_employee_matrix",
                                "payload": matrix_payload,
                                "status": "delivered", "created_at": art_now, "updated_at": art_now,
                            })
                            response_text = f"I've finalized the AI Employee Matrix with computed MMI scores. Review and approve to finalize.\n\n[[artifact:{art_id}]]"
                            ai_response_msg = {
                                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                                "session_id": session_id, "user_id": current_user.user_id,
                                "role": "assistant", "content": response_text, "created_at": art_now,
                            }
                            await db.chat_messages.insert_one(ai_response_msg)
                        else:
                            await _post_error("I wasn't able to finalize the AI Employee Matrix from Call 3. Please try uploading the transcript again.")
            except Exception as e:
                logger.exception("Vega analysis failed (call %s): %s", call_num, e)
                try:
                    if call_num == 0 and _v2_single_call:
                        friendly = "I ran into an unexpected error generating the full analysis."
                    elif call_num == 0:
                        friendly = "I ran into an unexpected error analyzing the documents."
                    else:
                        friendly = f"I ran into an unexpected error analyzing the Call {call_num} transcript."
                    await _post_error(
                        f"{friendly} Please try uploading the transcript again, and if the problem persists, let your admin know."
                    )
                except Exception:
                    logger.exception("Failed to post error message to chat after Vega analysis failure")

        elif session_id and employee_id:
            # Non-Vega agents — use _run_agent_turn as before
            try:
                from services.ai.agents.registry import get_agent
                from services.ai.conductor import _run_agent_turn

                agent = get_agent(f"emp_{employee_id}")
                if agent:
                    history = await db.chat_messages.find(
                        {"session_id": session_id}, {"_id": 0, "role": 1, "content": 1}
                    ).sort("created_at", 1).to_list(50)

                    response_text, _, _ = await _run_agent_turn(
                        agent, current_user,
                        history[-1]["content"] if history else "[UPLOADS SUBMITTED]",
                        history=history[:-1][-16:],
                        db=db, session_id=session_id, invocation_depth=0,
                        upload_ids=upload_ids,
                    )
                    if response_text and response_text.strip():
                        ai_response_msg = {
                            "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                            "session_id": session_id,
                            "user_id": current_user.user_id,
                            "role": "assistant",
                            "content": response_text,
                            "created_at": datetime.now(timezone.utc),
                        }
                        await db.chat_messages.insert_one(ai_response_msg)
            except Exception as e:
                logger.warning(f"Auto-response after upload submit failed: {e}")

        result = {"status": "submitted", "artifact_id": artifact_id, "upload_ids": upload_ids}
        if ai_response_msg:
            result["ai_response"] = {
                "message_id": ai_response_msg["message_id"],
                "role": "assistant",
                "content": ai_response_msg["content"],
                "created_at": ai_response_msg["created_at"].isoformat() if hasattr(ai_response_msg["created_at"], 'isoformat') else ai_response_msg["created_at"],
            }
        return result

    # ── Quiz submission (existing logic) ──
    if artifact.get("status") != "pending":
        raise HTTPException(status_code=400, detail="Artifact already submitted")

    # Handle both formats: {responses: {qid: answer}} or {answers: [{question_id, answer}]}
    responses = data.get("responses", {})
    if not responses and "answers" in data:
        responses = {a["question_id"]: a["answer"] for a in data["answers"] if "question_id" in a}

    # Store user responses
    await db.ai_chat_artifacts.update_one(
        {"artifact_id": artifact_id},
        {"$set": {
            "status": "submitted",
            "user_response": responses,
            "updated_at": now.isoformat(),
        }},
    )

    # Grade immediately — objective questions are instant, free_text uses LLM sub-call
    grade_result = {}
    try:
        from services.ai.agents.tools.conversational_tools import _handle_grade_quiz

        class _SimpleUser:
            def __init__(self, uid, name):
                self.user_id = uid
                self.name = name

        grade_result = await _handle_grade_quiz(
            {"artifact_id": artifact_id}, db,
            _SimpleUser(current_user.user_id, current_user.name),
            session_id=session_id,
            employee_id=artifact.get("employee_id"),
        )
    except Exception as e:
        logger.error(f"Inline grading failed for {artifact_id}: {e}", exc_info=True)
        grade_result = {"error": str(e)}

    # Clear quiz_in_progress from user state
    employee_id = artifact.get("employee_id")
    if employee_id:
        await db.ai_employee_user_state.update_one(
            {"user_id": current_user.user_id, "employee_id": employee_id},
            {"$set": {"state.quiz_in_progress": None, "updated_at": now.isoformat()}},
        )

    # Inject grading summary into chat so ARC can provide feedback on next message
    if session_id:
        score = grade_result.get("score", "?")
        passed = grade_result.get("passed", False)
        feedback_json = json.dumps(grade_result.get("feedback", []))
        module_number = artifact.get("payload", {}).get("module_number", "?")
        module_title = artifact.get("payload", {}).get("title", f"Module {module_number}")
        pass_fail = "PASSED" if passed else "FAILED — user must retake this module before advancing"
        system_msg = {
            "message_id": f"msg_{uuid.uuid4().hex[:12]}",
            "session_id": session_id,
            "user_id": current_user.user_id,
            "role": "user",
            "content": (
                f"[QUIZ GRADED] {module_title} (Module {module_number}): "
                f"scored {score}% ({pass_fail}). "
                f"Feedback: {feedback_json}"
            ),
            "created_at": now,
        }
        await db.chat_messages.insert_one(system_msg)

    logger.info(f"Quiz {artifact_id} graded: {grade_result.get('score', '?')}% by {current_user.user_id}")

    # Auto-generate AI feedback response so the frontend doesn't need to fake a user message
    ai_feedback_msg = None
    if session_id and employee_id:
        try:
            from services.ai.agents.registry import get_agent
            from services.ai.conductor import _run_agent_turn

            agent = get_agent(f"emp_{employee_id}")
            if agent:
                # Fetch full history so the agent has context
                history = await db.chat_messages.find(
                    {"session_id": session_id}, {"_id": 0, "role": 1, "content": 1}
                ).sort("created_at", 1).to_list(50)

                feedback_text, _, _ = await _run_agent_turn(
                    agent, current_user,
                    history[-1]["content"] if history else "Quiz submitted",
                    history=history[:-1][-16:],
                    db=db, session_id=session_id, invocation_depth=0,
                )
                if feedback_text and feedback_text.strip():
                    ai_feedback_msg = {
                        "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                        "session_id": session_id,
                        "user_id": current_user.user_id,
                        "role": "assistant",
                        "content": feedback_text,
                        "created_at": datetime.now(timezone.utc),
                    }
                    await db.chat_messages.insert_one(ai_feedback_msg)
        except Exception as e:
            logger.warning(f"Auto-feedback failed for {artifact_id}: {e}")

    result = {"status": "graded", "artifact_id": artifact_id, "grade": grade_result}
    if ai_feedback_msg:
        result["ai_feedback_message"] = {
            "message_id": ai_feedback_msg["message_id"],
            "role": "assistant",
            "content": ai_feedback_msg["content"],
            "created_at": ai_feedback_msg["created_at"].isoformat() if hasattr(ai_feedback_msg["created_at"], 'isoformat') else ai_feedback_msg["created_at"],
        }
    return result
