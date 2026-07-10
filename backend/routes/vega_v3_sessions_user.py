"""Vega end-user session listing + resume endpoints (Wave 7).

Distinct from ``routes/vega_sessions.py`` (the legacy v2 list/get) and
``routes/vega_sessions_audit.py`` (the future PR-SESSIONS audit module):
this module is the END-USER resume surface, accessed from the sidebar
inside the Vega chat window. Returns each user's OWN sessions only;
listing requires ``vega_sessions.view_own`` permission (default-allow:
seeded to every role that has ``vega.use``).

Endpoints:
  GET  /api/vega/my-sessions
  POST /api/vega/my-sessions/{session_id}/rename
  GET  /api/vega/my-sessions/{session_id}/resume

Performance budgets:
  list   < 200ms p95 with 100 sessions/user (single Mongo aggregate).
  resume < 500ms p95 (single aggregate + capped chat_messages tail).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from auth_helper import get_current_user, has_perm
from database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


CHAT_MESSAGES_TAIL_LIMIT = 50


def _format_iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _resolve_session_name(
    *,
    override: Optional[str],
    customer_name: Optional[str],
    created_at,
) -> str:
    if override and override.strip():
        return override.strip()
    date_part = ""
    iso = _format_iso(created_at) or ""
    if iso:
        date_part = iso[:10]  # YYYY-MM-DD
    if customer_name and customer_name.strip():
        if date_part:
            return f"{customer_name.strip()} — {date_part}"
        return customer_name.strip()
    if date_part:
        return f"Untitled session — {date_part}"
    return "Untitled session"


# ──────────────────────────────────────────────────────────────────────
# GET /api/vega/my-sessions
# ──────────────────────────────────────────────────────────────────────


@router.get("/vega/my-sessions")
async def list_my_sessions(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user=Depends(get_current_user),
):
    """List the current user's Vega sessions, newest first.

    Returns aggregated rows joining each session with its latest artifact
    (so the sidebar can show customer name + version + artifact_count).
    Uses a single Mongo aggregate for the budget.
    """
    if not has_perm(current_user, "vega_sessions.view_own"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    pipeline = [
        {"$match": {"user_id": current_user.user_id}},
        {"$sort": {"updated_at": -1}},
        {"$skip": offset},
        {"$limit": limit},
        {
            "$lookup": {
                "from": "ai_chat_artifacts",
                "let": {"sid": "$session_id"},
                "pipeline": [
                    # STRICT match: artifact.payload.session_id must equal
                    # the vega_discovery_sessions.session_id. Dropping the
                    # chat_session_id fallback because v2 reused a single
                    # pinned chat across many discovery sessions, so that
                    # fallback joined artifacts from unrelated sessions.
                    {"$match": {
                        "$expr": {
                            "$and": [
                                {"$eq": ["$payload.session_id", "$$sid"]},
                                {"$eq": ["$user_id", current_user.user_id]},
                            ],
                        },
                    }},
                    {"$sort": {"created_at": -1}},
                    {"$project": {
                        "_id": 0,
                        "artifact_id": 1,
                        "artifact_type": 1,
                        "created_at": 1,
                        "client_company": "$payload.client_company",
                        "client_name": "$payload.client_name",
                        "current_version": "$payload.current_version",
                        "call_mode": "$payload.pipeline_metadata.call_mode",
                    }},
                ],
                "as": "_artifacts",
            }
        },
    ]
    cursor = db.vega_discovery_sessions.aggregate(pipeline)
    sessions = await cursor.to_list(length=limit)

    # Total count for pagination — one cheap count_documents.
    total = await db.vega_discovery_sessions.count_documents(
        {"user_id": current_user.user_id}
    )

    rows = []
    for s in sessions:
        artifacts = s.get("_artifacts") or []
        latest = artifacts[0] if artifacts else None
        customer_name = (
            (latest or {}).get("client_company")
            or (latest or {}).get("client_name")
            or s.get("client_company")
        )
        rows.append({
            "session_id": s.get("session_id"),
            "chat_session_id": s.get("chat_session_id"),
            "session_name": _resolve_session_name(
                override=s.get("session_name_override"),
                customer_name=customer_name,
                created_at=s.get("created_at"),
            ),
            "session_name_override": s.get("session_name_override"),
            "customer_name": customer_name,
            "discovery_mode": s.get("discovery_mode"),
            "status": s.get("status"),
            "created_at": _format_iso(s.get("created_at")),
            "updated_at": _format_iso(s.get("updated_at")),
            "latest_artifact_id": (latest or {}).get("artifact_id"),
            "latest_artifact_type": (latest or {}).get("artifact_type"),
            "latest_artifact_call_mode": (latest or {}).get("call_mode"),
            "current_version": (latest or {}).get("current_version") or 1,
            "artifact_count": len(artifacts),
        })

    return {
        "sessions": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }


# ──────────────────────────────────────────────────────────────────────
# POST /api/vega/my-sessions/{session_id}/rename
# ──────────────────────────────────────────────────────────────────────


class RenameBody(BaseModel):
    session_name: str = Field(min_length=1, max_length=140)


@router.post("/vega/my-sessions/{session_id}/rename")
async def rename_my_session(
    session_id: str,
    body: RenameBody,
    current_user=Depends(get_current_user),
):
    """Set the explicit ``session_name_override`` on a session the user owns."""
    if not has_perm(current_user, "vega_sessions.view_own"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    session = await db.vega_discovery_sessions.find_one(
        {"session_id": session_id}, {"_id": 0, "user_id": 1}
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("user_id") != current_user.user_id:
        raise HTTPException(status_code=403, detail="You do not own this session")

    new_name = body.session_name.strip()
    await db.vega_discovery_sessions.update_one(
        {"session_id": session_id, "user_id": current_user.user_id},
        {"$set": {
            "session_name_override": new_name,
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    return {"session_id": session_id, "session_name": new_name}


# ──────────────────────────────────────────────────────────────────────
# GET /api/vega/my-sessions/{session_id}/resume
# ──────────────────────────────────────────────────────────────────────


@router.get("/vega/my-sessions/{session_id}/resume")
async def resume_my_session(
    session_id: str,
    current_user=Depends(get_current_user),
):
    """Hydrate everything the chat surface needs to re-open a session.

    Returns ``vega_session``, the linked ``chat_session`` doc, the most
    recent ``CHAT_MESSAGES_TAIL_LIMIT`` chat messages, and the latest
    artifact payload (full payload, not summary). Single-call aggregate
    for the perf budget; lazy-load older messages is out of scope for
    v1 but ``has_more`` flag is returned so the frontend can wire it
    later.
    """
    if not has_perm(current_user, "vega_sessions.view_own"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    vega_session = await db.vega_discovery_sessions.find_one(
        {"session_id": session_id}, {"_id": 0}
    )
    if not vega_session:
        raise HTTPException(status_code=404, detail="Session not found")
    if vega_session.get("user_id") != current_user.user_id:
        raise HTTPException(status_code=403, detail="You do not own this session")

    chat_session_id = vega_session.get("chat_session_id")
    chat_session = None
    chat_messages: list = []
    has_more_messages = False
    # Scope chat_messages to this Vega session's time window. Critical fix —
    # legacy v2 Vega used ONE pinned chat_session_id for many discovery
    # sessions, so filtering on chat_session_id alone returned the same
    # 50 messages for every old session ("17 copies of the current chat"
    # bug). Time-window scoping isolates the messages that belong to the
    # specific Vega session by their `created_at` falling between the
    # session's start and last-updated timestamps. New sessions
    # (post-Wave-7) get their own chat_session_id at creation time so
    # both filters agree.
    session_start = vega_session.get("created_at")
    session_end = vega_session.get("updated_at") or session_start
    if chat_session_id:
        chat_session = await db.chat_sessions.find_one(
            {"session_id": chat_session_id}, {"_id": 0}
        )
    # Lazy-create a real chat_session for sessions whose chat_session_id
    # points at nothing (legacy/recovery rows where the value is a synthetic
    # smoke ID). Without this, the resume returns chat_session=null and the
    # frontend falls back to whatever pinned chat is active — making every
    # old session appear to render the latest active chat.
    if not chat_session:
        new_chat_id = f"chat_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        chat_session = {
            "session_id": new_chat_id,
            "user_id": current_user.user_id,
            "pinned_agent": "emp_ai_vega",
            "title": vega_session.get("session_name_override") or "Vega session",
            "created_at": now,
            "updated_at": now,
        }
        await db.chat_sessions.insert_one(dict(chat_session))
        await db.vega_discovery_sessions.update_one(
            {"session_id": session_id, "user_id": current_user.user_id},
            {"$set": {"chat_session_id": new_chat_id, "updated_at": now}},
        )
        chat_session_id = new_chat_id
        chat_session.pop("_id", None)
        msg_query = {
            "session_id": chat_session_id,
            "user_id": current_user.user_id,
        }
        if session_start:
            # 60s grace either side to absorb clock skew between the chat
            # session record and the vega_discovery_sessions record.
            from datetime import timedelta
            start_with_grace = session_start
            end_with_grace = session_end
            if isinstance(start_with_grace, datetime):
                start_with_grace = start_with_grace - timedelta(seconds=60)
            if isinstance(end_with_grace, datetime):
                end_with_grace = end_with_grace + timedelta(seconds=60)
            msg_query["created_at"] = {
                "$gte": start_with_grace,
                "$lte": end_with_grace,
            }
        msg_total = await db.chat_messages.count_documents(msg_query)
        cursor = db.chat_messages.find(msg_query, {"_id": 0}).sort(
            "created_at", -1
        ).limit(CHAT_MESSAGES_TAIL_LIMIT)
        chat_messages = list(reversed(await cursor.to_list(length=CHAT_MESSAGES_TAIL_LIMIT)))
        has_more_messages = msg_total > len(chat_messages)
        for m in chat_messages:
            m["created_at"] = _format_iso(m.get("created_at"))

    # Latest artifact for this session — STRICTLY by payload.session_id.
    # Dropping the chat_session_id fallback because the same v2 chat is
    # shared across many vega_discovery_sessions, so that fallback returned
    # someone else's (or current) artifacts.
    artifact = await db.ai_chat_artifacts.find_one(
        {
            "user_id": current_user.user_id,
            "payload.session_id": session_id,
        },
        {"_id": 0},
        sort=[("created_at", -1)],
    )

    customer_name = None
    if artifact:
        payload = artifact.get("payload") or {}
        customer_name = payload.get("client_company") or payload.get("client_name")

    # If we have an artifact but no real chat history (legacy / recovery
    # session), synthesize a single rendered-artifact message so the user
    # sees the saved analysis when they resume. Without this the chat pane
    # is blank and the resume looks broken.
    if artifact and not chat_messages:
        art_id = artifact.get("artifact_id")
        synth_at = artifact.get("created_at") or datetime.now(timezone.utc)
        chat_messages = [{
            "message_id": f"msg_resumed_{art_id}",
            "session_id": chat_session_id,
            "user_id": current_user.user_id,
            "role": "assistant",
            "content": (
                f"Resumed session — here's the saved analysis.\n\n[[artifact:{art_id}]]"
            ),
            "created_at": _format_iso(synth_at),
        }]

    return {
        "vega_session": {
            **vega_session,
            "session_name": _resolve_session_name(
                override=vega_session.get("session_name_override"),
                customer_name=customer_name or vega_session.get("client_company"),
                created_at=vega_session.get("created_at"),
            ),
            "created_at": _format_iso(vega_session.get("created_at")),
            "updated_at": _format_iso(vega_session.get("updated_at")),
        },
        "chat_session": chat_session,
        "chat_messages": chat_messages,
        "has_more_messages": has_more_messages,
        "latest_artifact": artifact,
    }
