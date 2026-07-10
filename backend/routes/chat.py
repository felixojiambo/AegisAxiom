from fastapi import APIRouter, HTTPException, Depends, Request, Body
from datetime import datetime, timezone
import logging
import uuid
import re
from pymongo import ReturnDocument

logger = logging.getLogger(__name__)

from database import get_db
from models import ChatSession, ChatMessage, ChatMessageCreate, CreateSessionBody
from auth_helper import get_current_user, has_perm
from rate_limiter import limiter
from services.field_registry import register_fields, register_module_events, register_module_config

router = APIRouter()

register_module_config("chat", "chat_sessions", "session_id", "AI & Automation")
register_module_events("chat", "chat_session", ["created", "deleted"])
register_fields("chat", "chat_session", [
    {"name": "title", "type": "string", "label": "Title", "description": "Chat session title or summary"},
    {"name": "model_name", "type": "string", "label": "Model", "description": "AI model used for this session"},
    {"name": "total_tokens", "type": "number", "label": "Total Tokens", "description": "Cumulative token usage across all messages"},
    {"name": "message_count", "type": "number", "label": "Message Count", "description": "Number of messages in the session"},
])


@router.get("/sessions")
async def get_sessions(current_user=Depends(get_current_user)):
    if not has_perm(current_user, "chat.access"):
        raise HTTPException(status_code=403, detail="Insufficient permissions to access chat")
    
    db = get_db()
    sessions = await db.chat_sessions.find(
        {"user_id": current_user.user_id}, {"_id": 0}
    ).sort("updated_at", -1).to_list(50)
    return sessions


@router.post("/sessions")
async def create_session(data: CreateSessionBody = Body(default=None), current_user=Depends(get_current_user)):
    if not has_perm(current_user, "chat.access"):
        raise HTTPException(status_code=403, detail="Insufficient permissions to create chat sessions")

    if data and data.agent:
        from services.ai.agents.registry import get_agent
        agent = get_agent(data.agent)
        if agent and not agent.user_has_access(current_user):
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions to use {data.agent}",
            )
    
    db = get_db()
    session_id = f"chat_{uuid.uuid4().hex[:12]}"

    # Atomic counter for human-readable chat number
    counter = await db.counters.find_one_and_update(
        {"_id": "chat_number"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    chat_number = f"CHAT-{counter['seq']:05d}"

    doc = {
        "session_id": session_id,
        "chat_number": chat_number,
        "user_id": current_user.user_id,
        "user_email": current_user.email,
        "user_name": current_user.name,
        "title": "New Chat",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    if data and data.agent:
        doc["pinned_agent"] = data.agent
    await db.chat_sessions.insert_one(doc)
    doc.pop("_id", None)

    # Auto-greet: for agents with static intakes (Vega, Orion), create the
    # greeting message and intake artifact directly — no LLM call needed.
    if data and data.agent:
        try:
            from services.ai.agents.registry import get_agent
            agent = get_agent(data.agent)
            employee_doc = getattr(agent, '_employee', {}) if agent else {}
            employee_id = getattr(agent, '_employee_id', None)

            if employee_id == "ai_vega":
                art_id = f"art_{uuid.uuid4().hex[:12]}"
                now_iso = datetime.now(timezone.utc)

                # Wave 7 fix — seed an vega_discovery_sessions row at chat-
                # creation time so the sidebar can list this session
                # immediately, before any transcript is uploaded. The row
                # gets enriched (client_company, calls, ai_employee_matrix)
                # as the analysis runs. session_name_override is left
                # unset; the sidebar's _resolve_session_name fallback
                # ("Untitled session — YYYY-MM-DD") covers the empty case
                # until Vega auto-names it once it knows the customer.
                vega_session_id = f"vega_{uuid.uuid4().hex[:12]}"
                await db.vega_discovery_sessions.insert_one({
                    "session_id": vega_session_id,
                    "user_id": current_user.user_id,
                    "chat_session_id": session_id,
                    "client_company": None,
                    "current_call_number": 0,
                    "calls": {},
                    "ai_employee_matrix": [],
                    "gap_questions": [],
                    "mmi_scores": [],
                    "discovery_mode": "freeflow",
                    "status": "in_progress",
                    "created_at": now_iso,
                    "updated_at": now_iso,
                })

                await db.ai_chat_artifacts.insert_one({
                    "artifact_id": art_id,
                    "session_id": session_id,
                    "user_id": current_user.user_id,
                    "employee_id": "ai_vega",
                    "artifact_type": "chips",
                    "payload": {
                        "name": "vega_entry_chips",
                        "chips": [
                            {
                                "id": "single_call",
                                "label": "Full analysis from one upload",
                                "description": "Drop in one transcript and I'll generate the matrix, BPMNs, and app designs in one pass.",
                                "primary": True,
                            },
                            {
                                "id": "freeflow",
                                "label": "Talk through an opportunity",
                                "description": "Walk me through the deal — I'll listen, ask questions, and offer to wrap it up whenever you're ready.",
                            },
                            {
                                "id": "more_options",
                                "label": "More options",
                                "description": "Pick a specific call (1, 2, 3), resume a session, or upload supporting docs.",
                            },
                        ],
                    },
                    "status": "delivered",
                    "created_at": now_iso,
                    "updated_at": now_iso,
                })
                greeting = "Hi, I'm Vega. How would you like to start?"

                await db.chat_messages.insert_one({
                    "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                    "session_id": session_id,
                    "user_id": current_user.user_id,
                    "role": "assistant",
                    "content": f"{greeting}\n\n[[artifact:{art_id}]]",
                    "created_at": now_iso,
                })

            elif employee_doc.get("auto_greeting") and agent:
                # Other auto-greeting agents: use LLM
                from services.ai.conductor import _run_agent_turn
                greeting_text, _, _ = await _run_agent_turn(
                    agent, current_user, "Hello — this is a new session, introduce yourself.",
                    history=[], db=db, session_id=session_id, invocation_depth=0,
                )
                if greeting_text and greeting_text.strip():
                    await db.chat_messages.insert_one({
                        "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                        "session_id": session_id,
                        "user_id": current_user.user_id,
                        "role": "assistant",
                        "content": greeting_text,
                        "created_at": datetime.now(timezone.utc),
                    })
        except Exception as e:
            logger.warning(f"Auto-greeting failed for {data.agent}: {e}")

    return doc


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, current_user=Depends(get_current_user)):
    # FIX CRITICAL-2: Use chat.access
    if not has_perm(current_user, "chat.access"):
        raise HTTPException(status_code=403, detail="Insufficient permissions to view chat messages")
    
    db = get_db()
    session = await db.chat_sessions.find_one(
        {"session_id": session_id, "user_id": current_user.user_id}, {"_id": 0}
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = await db.chat_messages.find(
        {"session_id": session_id}, {"_id": 0}
    ).sort("created_at", 1).to_list(200)
    return messages


@router.post("/sessions/{session_id}/messages")
@limiter.limit("20/minute")
async def send_message(session_id: str, data: ChatMessageCreate, request: Request, current_user=Depends(get_current_user)):
    # FIX CRITICAL-2: Use chat.access
    if not has_perm(current_user, "chat.access"):
        raise HTTPException(status_code=403, detail="Insufficient permissions to send chat messages")
    
    db = get_db()
    session = await db.chat_sessions.find_one(
        {"session_id": session_id, "user_id": current_user.user_id}, {"_id": 0}
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Save user message
    user_msg = {
        "message_id": f"msg_{uuid.uuid4().hex[:12]}",
        "session_id": session_id,
        "user_id": current_user.user_id,
        "role": "user",
        "content": data.content,
        "created_at": datetime.now(timezone.utc),
    }
    await db.chat_messages.insert_one(user_msg)

    # Get history for context. Wave 7 fix — Vega chats accumulate fat
    # artifact-laden content (multi_upload payloads, [[artifact:…]]
    # markers, transcript chunks). 50 turns of that blew context budget
    # and caused (a) slow Gemini responses, (b) typing lag because the
    # full history hit the wire on every keystroke-ack roundtrip, and
    # (c) eventual hallucination once context approached the model
    # ceiling. Vega gets a tighter 25-turn window with per-message
    # truncation; other agents keep 50.
    pinned_agent = session.get("pinned_agent")
    is_vega = pinned_agent == "emp_ai_vega"
    history_cap = 25 if is_vega else 50
    # Load the MOST RECENT `history_cap` messages, not the oldest. The
    # previous version sorted ascending and capped, which for any session
    # with more than `history_cap` messages quietly dispatched only the
    # OLDEST window — the user's freshly-persisted turn never made it
    # into history, so the intent classifier and downstream LLM always
    # operated on stale context (e.g. a 40-message Vega session would be
    # dispatched against turns 1-25, including the welcome message,
    # while turns 26-40 plus the latest user message were dropped). For
    # Vega this caused "resumed sessions act like they forgot everything"
    # and graduation never firing on long sessions because the intent
    # classifier kept reading an old user message as the latest turn.
    history = await db.chat_messages.find(
        {"session_id": session_id}, {"_id": 0, "role": 1, "content": 1}
    ).sort("created_at", -1).limit(history_cap).to_list(history_cap)
    history.reverse()
    if is_vega:
        # Truncate the body of older messages — keep the most recent 6
        # full-fidelity, summarize the rest with a hard 1500-char cap.
        if len(history) > 6:
            for m in history[:-6]:
                content = m.get("content") or ""
                if len(content) > 1500:
                    m["content"] = content[:1500] + " …[truncated for context budget]"

    # Get AI response. Wrapped in a defence-in-depth try/except so
    # the route ALWAYS returns a response, even if a future bug or
    # an exception class that escapes get_chat_response's own handlers
    # propagates up (e.g. a BaseException-subclass like CancelledError
    # that the catch-all there cannot catch). Without this belt the
    # chat handler exits without returning and Starlette raises
    # "RuntimeError: No response returned." — which is what the
    # operator log was showing under sustained Gemini quota
    # exhaustion.
    from services.ai import get_chat_response
    try:
        result = await get_chat_response(
            current_user,
            history,
            db,
            session_id=session_id,
            pinned_agent=pinned_agent,
            message_id=user_msg["message_id"],
        )
    except Exception as exc:  # noqa: BLE001 — last-resort guard
        logger.error(
            "Unhandled error in get_chat_response — chat route "
            "fell back to safe response: %s",
            exc,
            exc_info=True,
        )
        result = {
            "content": (
                "I'm having trouble connecting right now. Please try "
                "again in a moment."
            ),
            "thinking": [
                {"step": "Error", "detail": "Unexpected error"},
            ],
        }

    # Handle dict return (with thinking) or plain string (confirmations/errors)
    if isinstance(result, dict):
        ai_response = result.get("content", "")
        thinking = result.get("thinking", [])
    else:
        ai_response = result
        thinking = []

    # Don't save empty assistant messages (can happen when tool loop
    # completes but produces no text and no artifacts)
    if not (ai_response or "").strip():
        # Return a minimal empty response — frontend will ignore it
        return ChatMessage(
            message_id=f"msg_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            user_id=current_user.user_id,
            role="assistant",
            content="",
            created_at=datetime.now(timezone.utc),
        )

    # Save AI message
    ai_msg = {
        "message_id": f"msg_{uuid.uuid4().hex[:12]}",
        "session_id": session_id,
        "user_id": current_user.user_id,
        "role": "assistant",
        "content": ai_response,
        "thinking": thinking if thinking else None,
        "created_at": datetime.now(timezone.utc),
    }
    await db.chat_messages.insert_one(ai_msg)

    # Update session title and timestamp
    updates = {"updated_at": datetime.now(timezone.utc)}
    if session.get("title") == "New Chat" and len(history) <= 2:
        title = data.content[:50] + "..." if len(data.content) > 50 else data.content
        updates["title"] = title
    await db.chat_sessions.update_one({"session_id": session_id}, {"$set": updates})

    return ChatMessage(**ai_msg)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "chat.access"):
        raise HTTPException(status_code=403, detail="Insufficient permissions to delete chat sessions")
    
    db = get_db()
    await db.chat_sessions.delete_one({"session_id": session_id, "user_id": current_user.user_id})
    await db.chat_messages.delete_many({"session_id": session_id})
    await db.chat_uploads.delete_many({"session_id": session_id})
    return {"message": "Session deleted"}


# ──────────────────────────────────────────────
#  ADMIN: Chat Logs for Troubleshooting
# ──────────────────────────────────────────────

from fastapi import Query
from typing import Optional

@router.get("/admin/logs")
async def get_chat_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    user_id: Optional[str] = None,
    chat_number: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    """Admin-only endpoint to browse all user chat sessions for troubleshooting."""
    if not has_perm(current_user, "audit.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    query = {}

    if user_id:
        query["user_id"] = user_id
    if chat_number:
        query["chat_number"] = {"$regex": re.escape(chat_number), "$options": "i"}
    if date_from or date_to:
        # Wrap both parses so a malformed date_from or date_to query
        # param returns a controlled 400 instead of the prior raw 500
        # that the FastAPI ValueError handler would emit.
        date_filter = {}
        try:
            if date_from:
                date_filter["$gte"] = datetime.fromisoformat(date_from)
            if date_to:
                date_filter["$lte"] = datetime.fromisoformat(date_to + "T23:59:59")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid date format")
        query["created_at"] = date_filter

    total = await db.chat_sessions.count_documents(query)

    # Single aggregation that returns the paginated session slice
    # joined with a per session message count. Replaces the prior
    # for loop that issued one count_documents call per session and
    # produced up to 100 round trips per page load. The lookup uses
    # a let plus pipeline shape so the join key chat_messages.session_id
    # matches chat_sessions.session_id and the inner pipeline emits
    # only the count rather than the full message bodies.
    pipeline = [
        {"$match": query},
        {"$sort": {"updated_at": -1}},
        {"$skip": skip},
        {"$limit": limit},
        {
            "$lookup": {
                "from": "chat_messages",
                "let": {"session_id": "$session_id"},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$session_id", "$$session_id"]}}},
                    {"$count": "count"},
                ],
                "as": "message_count_doc",
            }
        },
        {
            "$addFields": {
                "message_count": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$message_count_doc.count", 0]},
                        0,
                    ]
                }
            }
        },
        {"$project": {"_id": 0, "message_count_doc": 0}},
    ]
    sessions = await db.chat_sessions.aggregate(pipeline).to_list(limit)

    return {"items": sessions, "total": total}


@router.get("/admin/logs/{session_id}/messages")
async def get_chat_log_messages(session_id: str, current_user=Depends(get_current_user)):
    """Admin-only: Read the full message history of any chat session."""
    if not has_perm(current_user, "audit.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    session = await db.chat_sessions.find_one({"session_id": session_id}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = await db.chat_messages.find(
        {"session_id": session_id}, {"_id": 0}
    ).sort("created_at", 1).to_list(500)

    return {"session": session, "messages": messages}
