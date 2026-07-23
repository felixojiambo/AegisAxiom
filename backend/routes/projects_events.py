"""Projects — Realtime events (SSE) + presence (v1.3 Wave 1 / Thread 1A).

Two endpoints power Theme A (realtime + multi-PM collaboration):

  * `GET  /api/projects/{project_id}/events`            — SSE stream
  * `POST /api/projects/{project_id}/presence/heartbeat` — presence ping

SSE design
----------
The SSE endpoint subscribes the connection to the project's topic on
`services.projects_realtime_bus.realtime_bus`. As mutation routes
(`projects_task.py`, future `projects_project.py` / `projects_sprint.py`
emissions) publish events, the SSE handler formats each as an
`event: project.update` frame and writes it to the response stream.

A `: heartbeat\n\n` comment is sent every 25 seconds while idle to keep
the connection alive on Cloud Run (request-stream timeout) and through
browser/CDN proxies that close idle HTTP/2 streams.

NOTE for ops (Sahil): SSE on Cloud Run requires `--timeout=3600` on the
backend service deploy (default is 5 minutes). The client implements
reconnect-on-drop so the 60-minute Cloud Run cap is acceptable. Do NOT
add infrastructure config files here — the requirement is documented in
.waves/projects-v1.3-plan.md §7.1 and applied on the deploy side.

Presence design
---------------
Lightweight: the frontend hits the heartbeat endpoint every 30 seconds.
Each call upserts the caller's row in `pm_presence` and returns the list
of users active in the last 60 seconds (excluding the caller). A TTL
index on `last_seen` (60s) cleans stale rows automatically — no
background worker needed.

`pm_*` collection prefix matches the convention established in
projects_task.py (every Projects-owned collection other than the PK
`projects` collection gets `pm_`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from auth_helper import get_current_user, has_perm
from database import get_db
from db.session import get_sessionmaker
from repositories.auth import AuthRepository
from repositories.users import UserRepository
from middleware.tenant_scoping import get_current_tenant_id
from services.projects_realtime_bus import (
    OVERFLOW_SENTINEL,
    project_topic,
    realtime_bus,
)
from services.security_logger import log_security_event
from services.sse_tickets import consume_ticket, issue_ticket


logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Send a `: heartbeat\n\n` SSE comment every 25 seconds while idle.
# Frontend's EventSource reconnect cadence is ~30s; the 25s heartbeat
# keeps the connection alive comfortably under any browser idle timeout
# and well inside Cloud Run's --timeout=3600 ceiling.
_HEARTBEAT_INTERVAL_SECONDS = 25.0

# Presence: a user is "active" if their last_seen is within this window.
_PRESENCE_ACTIVE_WINDOW_SECONDS = 60

# Allowed viewports for the presence ping. Kept narrow so the field is
# safely renderable as a small avatar badge (no free-form strings from
# the client).
_VALID_VIEWPORTS = {"board", "backlog", "list", "gantt", "calendar", "phases", "sprints", "threads", "overview", "settings", "standup"}


# ---------------------------------------------------------------------------
# Feature flag — realtime SSE delivery
# ---------------------------------------------------------------------------
# SSE cross-user push is the only Cloud-Run-flag-dependent feature in v1.3
# (needs `--timeout=3600` on the backend service). When AEGISAXIOM is deployed to
# infrastructure that doesn't allow long-lived HTTP responses — or when the
# architecture is mid-replatform — operators can disable just this slice by
# setting `ENABLE_REALTIME_SSE=false` in the backend env.
#
# What stays ON when this flag is off:
#   - Presence avatars (heartbeat endpoint uses normal request/response)
#   - ConflictDialog (409 on PATCH with If-Match — short request)
#   - Every other v1.3 feature
#
# What turns OFF when this flag is off:
#   - GET /api/projects/{id}/events             → 503
#   - POST /api/projects/{id}/events/ticket     → 503
#   - The frontend hook short-circuits to a no-op (no EventSource opened)
#
# Default: DISABLED. Fail-safe — if Sahil hasn't yet added the var to GCP
# Secret Manager / set the Cloud Run timeout flag, production gets the
# degraded mode (no reconnect storm) instead of the broken realtime mode.
# Operators explicitly opt-in by setting the var to "true" AND configuring
# the matching infrastructure (Cloud Run `--timeout=3600`).
#
# To enable:
#   1. Set `ENABLE_REALTIME_SSE=true` in the backend env
#   2. Set `REACT_APP_ENABLE_REALTIME_SSE=true` in the frontend build env
#   3. Configure `--timeout=3600` on the Cloud Run backend service (or run
#      on infra that allows long-lived HTTP/2 streams natively)
# ---------------------------------------------------------------------------
def _realtime_enabled() -> bool:
    """Read ENABLE_REALTIME_SSE env var. Defaults to DISABLED (fail-safe)."""
    raw = os.getenv("ENABLE_REALTIME_SSE", "false").strip().lower()
    return raw in ("true", "1", "yes", "on")


_REALTIME_DISABLED_DETAIL = (
    "Realtime SSE delivery is disabled on this deployment. "
    "Presence and conflict detection still work; cross-user updates require a page refresh."
)


async def _require_realtime_enabled() -> None:
    """Dependency: short-circuit with 503 when the SSE feature flag is off.

    Declared before `get_sse_user` on protected routes so a rejected
    connection does NOT pay the cost of session lookup, role hydration,
    or ticket consumption when the feature is disabled.
    """
    if not _realtime_enabled():
        raise HTTPException(status_code=503, detail=_REALTIME_DISABLED_DETAIL)


# ---------------------------------------------------------------------------
# SSE-specific auth dependency (v1.4 — ticket exchange)
# ---------------------------------------------------------------------------
# The browser EventSource API does NOT allow custom headers (no Authorization,
# no cookies on cross-origin requests with credentials gated by CORS preflight
# that EventSource itself cannot send). Real-world SSE auth therefore needs
# either a same-site cookie OR a query parameter on the connect URL.
#
# v1.3 used `?access_token=<session_token>` as the EventSource fallback. The
# downside: the long-lived session token landed in LB/proxy access logs and
# browser history, with re-use possible if the URL leaked.
#
# v1.4 replaces that with a one-time **ticket exchange**:
#
#   1. Client POSTs `/api/projects/{project_id}/events/ticket` with normal
#      cookie/Bearer auth → server returns a short-TTL, single-use ticket.
#   2. Client opens `EventSource(.../events?ticket=<ticket>)`.
#   3. Server consumes the ticket on first read and verifies the bound
#      project_id matches the path; a second consume of the same ticket
#      fails (single-use), and the ticket expires in 30s if unused.
#
# The session token never reaches a URL. Even if the URL leaks to a log,
# the ticket is already consumed and cannot be replayed.
# ---------------------------------------------------------------------------
async def _load_user_from_session_token(token: str):
    """Load + RBAC-resolve a User from a session_token. Shared by the
    cookie path and the Bearer path inside get_sse_user. Delegates the
    actual hydration to `_hydrate_user`, so the symbols it imports (User,
    get_cached_implication_graph, resolve_implications) are not needed
    here directly."""
    sm = get_sessionmaker()
    async with sm() as pg:
        session = await AuthRepository(pg).get_session(token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    expires_at = session.expires_at
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired")

    return await _hydrate_user(session.user_id)


async def _hydrate_user(user_id: str):
    """Load the User by id + resolve effective permissions. Used by both
    the session-token path and the ticket path."""
    from models import User
    from auth_helper import get_cached_implication_graph
    from services.permission_implications import resolve_implications

    db = get_db()
    sm = get_sessionmaker()
    async with sm() as pg:
        user_dto = await UserRepository(pg).get_user_by_id(user_id)
    if not user_dto:
        raise HTTPException(status_code=401, detail="User not found")
    user_doc = user_dto.model_dump()

    if not user_doc.get("is_active", True):
        raise HTTPException(status_code=403, detail="Account is deactivated")

    role_name = user_doc.get("role", "customer")
    role_doc = await db.roles.find_one({"name": role_name}, {"_id": 0, "permissions": 1})
    role_permissions = role_doc.get("permissions", []) if role_doc else []
    user_extras = user_doc.get("extra_permissions", [])
    permissions = list(set(role_permissions + user_extras))

    try:
        registry_graph = await get_cached_implication_graph()
    except Exception:
        registry_graph = {}
    perm_closure = resolve_implications(set(permissions), registry_graph=registry_graph)

    safe_doc = {k: v for k, v in user_doc.items() if k != "password_hash"}
    safe_doc["permissions"] = permissions
    safe_doc["extra_permissions"] = user_extras
    safe_doc["effective_permissions"] = sorted(perm_closure)
    user = User(**safe_doc)
    object.__setattr__(user, "_perm_closure", perm_closure)
    object.__setattr__(user, "is_ai_employee", False)
    object.__setattr__(user, "ai_employee_id", None)
    return user


async def get_sse_user(
    request: Request,
    ticket: Optional[str] = Query(None),
    session_token: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """Auth dependency for the SSE endpoint.

    Resolution order:
      1. session_token cookie  (preferred — same-site request from our SPA)
      2. Authorization: Bearer header  (rare — direct curl / fetch wrapper)
      3. ticket query parameter  (EventSource fallback — single-use,
         short-TTL, project-bound)

    Otherwise raises 401. The ticket binding to a specific project_id is
    re-checked against the path here so a ticket issued for project A
    cannot be used to subscribe to project B.
    """
    # 1 + 2: standard auth paths. These ride existing cookies/headers that
    # the browser sends to the same-origin POST that issued the ticket — so
    # in practice the cookie path wins when the SPA is the client. The
    # ticket fallback is only exercised by EventSource (which can't send
    # custom headers).
    standard_token = None
    if session_token:
        standard_token = session_token
    elif authorization and authorization.startswith("Bearer "):
        standard_token = authorization[7:]

    if standard_token:
        return await _load_user_from_session_token(standard_token)

    # 3: ticket fallback. Consume atomically; missing/expired/already-used
    # all return None → 401.
    if ticket:
        consumed = await consume_ticket(ticket)
        if not consumed:
            raise HTTPException(status_code=401, detail="Invalid or expired ticket")

        # Cross-project guard: the ticket was issued for a specific
        # project_id; refuse to honor it on a different project's stream.
        # Fail-closed: if the route doesn't expose `project_id` in path_params
        # for any reason (misregistration, future re-use of this dependency
        # on a route without a project param), we must NOT silently skip
        # the binding check — that would let a ticket for project A authorize
        # a connection on any route.
        path_project_id = request.path_params.get("project_id")
        if path_project_id is None or consumed["project_id"] != path_project_id:
            try:
                await log_security_event(
                    event_type="sse_ticket_project_mismatch", severity="warning",
                    user_id=consumed["user_id"], action="sse_auth", outcome="denied",
                    details={
                        "ticket_project_id": consumed["project_id"],
                        "path_project_id": path_project_id,
                    },
                )
            except Exception:  # noqa: BLE001 — never fail auth on logging
                logger.debug("sse_ticket_project_mismatch audit write failed", exc_info=True)
            raise HTTPException(status_code=403, detail="Ticket not valid for this project")

        # Audit the fallback path so anomalous usage is visible in the
        # security log. Stamped with the bound user_id + project_id per
        # v1.4 spec (was `sse_query_token_used` in v1.3).
        try:
            await log_security_event(
                event_type="sse_ticket_used", severity="info",
                user_id=consumed["user_id"],
                action="sse_auth", outcome="allowed",
                details={
                    "path": str(request.url.path),
                    "project_id": consumed["project_id"],
                },
            )
        except Exception:  # noqa: BLE001 — never fail auth on logging
            logger.debug("sse_ticket_used audit write failed", exc_info=True)

        return await _hydrate_user(consumed["user_id"])

    raise HTTPException(status_code=401, detail="Not authenticated")


# ---------------------------------------------------------------------------
# Helpers — RBAC + project lookup
# ---------------------------------------------------------------------------
async def _load_project_or_404(
    db: Any, project_id: str, tenant_id: str
) -> Dict[str, Any]:
    """Fetch a project by id or raise 404. Centralized so SSE + presence
    apply the same lookup pattern.

    Phase 1E.3c — tenant-scoped. A cross-tenant project_id returns 404,
    matching the read-isolation contract on other Projects endpoints.
    Critical for SSE: without this, a user could subscribe to events on
    a project in another tenant by guessing the project_id.
    """
    project = await db["projects"].find_one(
        {"project_id": project_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _can_view_project(user, project: Dict[str, Any]) -> bool:
    """Project-level view gate.

    Mirrors the read perm pattern used elsewhere in projects_*: a user
    can view a project if they hold `projects.task.view` OR
    `projects.project.view`. PM-of-this-project also passes (auto-grant
    of view to the PM, same pattern as projects_task.py's
    `_caller_can_edit_task` for editing).
    """
    if has_perm(user, "projects.task.view") or has_perm(user, "projects.project.view"):
        return True
    pm_id = project.get("pm_id")
    if pm_id and pm_id == getattr(user, "user_id", None):
        return True
    return False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


# ---------------------------------------------------------------------------
# SSE — GET /projects/{project_id}/events
# ---------------------------------------------------------------------------
def _sse_frame(event: dict) -> bytes:
    """Format an event dict as an SSE frame.

    SSE wire format:
        event: <type>
        id:    <event_id>
        data:  <json>
        \n
    """
    event_name = event.get("type") or "project.update"
    event_id = event.get("event_id") or ""
    data_json = json.dumps(event, default=str, separators=(",", ":"))
    # Each line ends with \n; the frame itself ends with a blank line (\n\n).
    frame = (
        f"event: {event_name}\n"
        f"id: {event_id}\n"
        f"data: {data_json}\n\n"
    )
    return frame.encode("utf-8")


def _sse_comment(text: str) -> bytes:
    """SSE comment frame — lines starting with `:` are ignored by clients
    but keep the connection alive. Used for heartbeats and resync hints."""
    return f": {text}\n\n".encode("utf-8")


async def _event_stream(
    project_id: str,
    last_event_id: Optional[str],
) -> AsyncIterator[bytes]:
    """Async generator that yields SSE-formatted bytes.

    Concurrency model:
      - Subscribe to the project's topic on the realtime bus.
      - Race a queue-get against a 25s heartbeat timer. When the timer
        fires first, emit a heartbeat comment and loop. When the queue
        delivers an event, emit it as a frame. When the queue delivers
        OVERFLOW_SENTINEL, emit a `: refetch` comment instructing the
        client to resync.

    last_event_id is currently accepted but not used for in-memory
    replay (events are ephemeral). Reconnecting clients should backfill
    via the `recent-changes` endpoint using the timestamp they last
    received. See module docstring "No persistence in v1.3."
    """
    if last_event_id:
        # Hint to the client that we acknowledged the resume header but
        # do not replay from in-memory; they should refetch.
        logger.debug(
            "SSE resume with Last-Event-ID=%s project=%s (no in-memory replay; client should refetch)",
            last_event_id, project_id,
        )

    topic = project_topic(project_id)

    # Initial "hello" comment so the client's EventSource immediately
    # sees data on the wire (helps some intermediaries open the stream
    # before the first real event).
    yield _sse_comment("ok")

    # `bus.subscribe(topic)` is itself an async generator. We can't trivially
    # race it against a timer, so we drive the underlying queue manually via
    # an inner subscription. We open it via the public surface to keep the
    # abstraction (so future Pub/Sub swap doesn't break this handler).
    sub_iter = realtime_bus.subscribe(topic)

    # Wave 3.5 fix-up: hold a SINGLE long-lived `next_task` across heartbeat
    # cycles. Previously this was recreated inside the loop on every iteration,
    # which (combined with `asyncio.shield`) leaked ~one dangling Task per
    # heartbeat — ~144 tasks per 60-minute SSE session per subscriber. Now the
    # task is only replaced after we have actually consumed its result.
    next_task: Optional[asyncio.Task] = None
    try:
        while True:
            if next_task is None:
                next_task = asyncio.create_task(sub_iter.__anext__())

            # Race the (single, persistent) next-event task against the
            # heartbeat timer. shield() ensures wait_for's cancellation on
            # timeout doesn't cancel the underlying task — we want to keep
            # it pending across heartbeat cycles.
            try:
                event = await asyncio.wait_for(
                    asyncio.shield(next_task), timeout=_HEARTBEAT_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                # Heartbeat tick. The same `next_task` stays alive; loop
                # back and wait_for it again on the next cycle.
                yield _sse_comment("heartbeat")
                continue
            except StopAsyncIteration:
                # Generator closed — bus tore down.
                next_task = None
                yield _sse_comment("closed")
                return

            # next_task completed — consume and clear so the top of the loop
            # creates a fresh one for the next event.
            next_task = None

            if event is OVERFLOW_SENTINEL or (
                isinstance(event, dict) and event.get("__overflow__")
            ):
                yield _sse_comment("refetch")
                continue

            yield _sse_frame(event)
    except asyncio.CancelledError:
        # Client disconnected — the StreamingResponse cancels this task.
        # Falling through to finally cleans up the bus subscription via
        # the generator's own finally.
        raise
    finally:
        # Cancel any in-flight next_task so it doesn't outlive this generator.
        # This is the second half of the leak fix: without explicit cancellation,
        # a shielded task that's still awaiting `queue.get()` survives until the
        # bus eventually publishes (or forever, if the topic goes quiet).
        if next_task is not None and not next_task.done():
            next_task.cancel()
            try:
                await next_task
            except (asyncio.CancelledError, StopAsyncIteration, Exception):  # noqa: BLE001
                pass
        # Aggressively close the inner subscription generator so its
        # `finally` (which unsubscribes from the bus) runs even if the
        # client dropped while we were holding a pending next_task.
        try:
            await sub_iter.aclose()
        except Exception:  # pragma: no cover — defensive
            logger.exception("SSE inner subscription close raised; ignoring")


@router.get(
    "/{project_id}/events",
    # NOTE: not wrapped with require_ai_permission — this is a human-only
    # streaming endpoint. AI flows don't connect to SSE; they use the
    # synchronous recent-changes endpoint instead.
)
async def stream_project_events(
    project_id: str,
    request: Request,
    _: None = Depends(_require_realtime_enabled),
    current_user=Depends(get_sse_user),
):
    """SSE stream of mutation events for a single project.

    RBAC: caller must hold `projects.task.view` OR `projects.project.view`
    (or be the PM of this project). Each event carries the full updated
    entity doc in `payload` so clients merge optimistically without
    follow-up GETs.

    Reconnect: clients can send `Last-Event-ID: <event_id>` on reconnect.
    v1.3 acknowledges the header but does not replay from in-memory.
    The client should backfill via `/api/projects/recent-changes` using
    the timestamp of the last event it processed.

    Heartbeat: a `: heartbeat\n\n` comment is sent every 25s while idle
    to defeat HTTP/2 idle-stream cleanup on Cloud Run and browser proxies.

    Cloud Run note: SSE requires `--timeout=3600` on the backend deploy
    (max 60min request streaming). Set on the deploy side (Sahil), not
    in app config.

    Feature flag: when `ENABLE_REALTIME_SSE=false`, the
    `_require_realtime_enabled` dependency short-circuits with 503 before
    auth resolution runs (no DB hits on rejected SSE attempts).
    """
    db = get_db()
    # Phase 1E.3c — SSE auth flows via session-token or single-use ticket,
    # NOT through normal middleware (EventSource cannot send the
    # X-Override-Tenant-Id header). So tenant resolution here uses the
    # SSE-resolved user's home tenant. Root admin operating under override
    # in the rest of the app will still get their HOME tenant's SSE stream
    # — they cannot impersonate a partner over a long-lived SSE connection.
    sse_tenant_id = getattr(current_user, "tenant_id", None)
    if not sse_tenant_id:
        # Defensive: a tenantless user can't reach project-scoped SSE.
        raise HTTPException(status_code=403, detail="No tenant context")
    project = await _load_project_or_404(db, project_id, sse_tenant_id)
    if not _can_view_project(current_user, project):
        raise HTTPException(status_code=403, detail="Permission denied")

    last_event_id = request.headers.get("Last-Event-ID")

    # Headers tuned for SSE through proxies and browsers.
    # `no-store`: the connect URL may carry a single-use `ticket` query
    # parameter (EventSource cannot send custom headers). Even though the
    # ticket is one-time, telling every cache between the client and us
    # not to retain the URL/response keeps proxies from holding stale
    # tickets and prevents the URL from re-running from cache.
    headers = {
        "Cache-Control": "no-store, no-cache, no-transform",
        # `X-Accel-Buffering: no` is the canonical hint to nginx (and many
        # other reverse proxies) not to buffer the response. Cloud Run's
        # front end has no such buffer, but middleware in front of us
        # (load balancers, dev proxies) might.
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }

    return StreamingResponse(
        _event_stream(project_id, last_event_id),
        media_type="text/event-stream",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Ticket exchange — POST /projects/{project_id}/events/ticket  (v1.4)
# ---------------------------------------------------------------------------
class EventsTicketResponse(BaseModel):
    ticket: str = Field(..., description="Single-use, project-bound SSE ticket")
    expires_in: int = Field(..., description="Seconds until the ticket expires")


_TICKET_TTL_SECONDS = 30


@router.post(
    "/{project_id}/events/ticket",
    status_code=201,
    response_model=EventsTicketResponse,
)
async def issue_events_ticket(
    project_id: str,
    _: None = Depends(_require_realtime_enabled),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Issue a short-TTL, single-use ticket for opening the SSE stream.

    Why this exists
    ---------------
    `EventSource` cannot send Authorization headers or cross-origin
    cookies, so the client needs *something* in the URL to authenticate
    the SSE connect. v1.3 used the long-lived session_token directly —
    that leaked it into LB/proxy access logs. This endpoint replaces
    that pattern with an exchange:

      1. Client POSTs here with normal cookie/Bearer auth.
      2. Server returns a 30-second, single-use opaque ticket bound to
         `(user_id, project_id)`.
      3. Client opens `EventSource(.../events?ticket=<ticket>)`.

    A leaked ticket URL exposes only a one-time, already-consumed token.

    RBAC: same view gate as the SSE stream itself — the caller must hold
    `projects.task.view` OR `projects.project.view`, or be the PM of this
    project. We re-check here so a 401 is surfaced synchronously on the
    POST instead of opaquely on the SSE connect.

    Reconnect contract: tickets are single-use. The frontend MUST request
    a fresh ticket on every connect AND every reconnect cycle.

    Feature flag: when `ENABLE_REALTIME_SSE=false`, the
    `_require_realtime_enabled` dependency short-circuits with 503 before
    any DB work.

    Phase 1E.3c — project-existence check is tenant-scoped via the
    normal middleware dep. Issuing a ticket for a cross-tenant project
    returns 404, blocking the SSE handshake before it starts.
    """
    db = get_db()
    project = await _load_project_or_404(db, project_id, tenant_id)
    if not _can_view_project(current_user, project):
        raise HTTPException(status_code=403, detail="Permission denied")

    ticket = await issue_ticket(
        current_user.user_id,
        project_id,
        ttl_seconds=_TICKET_TTL_SECONDS,
    )
    return EventsTicketResponse(ticket=ticket, expires_in=_TICKET_TTL_SECONDS)


# ---------------------------------------------------------------------------
# Presence — POST /projects/{project_id}/presence/heartbeat
# ---------------------------------------------------------------------------
class PresenceHeartbeatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    viewport: Optional[str] = Field(default=None, description="Which view the user is on (board / backlog / list / gantt / ...)")


_PRESENCE_TTL_ENSURED = False


async def _ensure_presence_indexes(db: Any) -> None:
    """Idempotent: ensure the TTL index on `pm_presence.last_seen` exists.

    Mongo TTL semantics: rows are deleted once `last_seen` is older than
    `expireAfterSeconds`. We use 60s so a user who closed their tab
    drops out of the presence list automatically.

    `(project_id, user_id)` is the natural unique key; we also index it
    so the upsert in heartbeat() is O(log n) and the read filter is
    selective.
    """
    global _PRESENCE_TTL_ENSURED
    if _PRESENCE_TTL_ENSURED:
        return
    try:
        # TTL index — last_seen older than 60s → auto-delete.
        await db["pm_presence"].create_index(
            "last_seen",
            name="pm_presence_last_seen_ttl",
            expireAfterSeconds=_PRESENCE_ACTIVE_WINDOW_SECONDS,
            background=True,
        )
        # Lookup index — supports the upsert filter + the active-list read.
        await db["pm_presence"].create_index(
            [("project_id", 1), ("user_id", 1)],
            name="pm_presence_project_user",
            unique=True,
            background=True,
        )
        _PRESENCE_TTL_ENSURED = True
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(f"pm_presence index ensure failed (non-fatal): {e}")


@router.post(
    "/{project_id}/presence/heartbeat",
)
async def presence_heartbeat(
    project_id: str,
    body: PresenceHeartbeatBody,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Heartbeat + active-users read in one call.

    Effect: upserts `{user_id, project_id, last_seen: now, viewport}` in
    `pm_presence`.

    Response: list of other users active in the last 60s.

    RBAC: caller must hold `projects.task.view` (or be the PM of this
    project). The endpoint deliberately uses the same view gate as the
    SSE stream so a viewer of the board can announce their presence
    without needing edit rights.

    Frontend cadence: call every 30 seconds while the tab is foregrounded.
    The TTL index ensures stale rows auto-clean within 60s of the last
    heartbeat — no background worker needed.

    Phase 1E.3c — presence rows stamped + filtered by tenant_id. The
    project-existence check is also tenant-scoped, so cross-tenant
    project_ids 404 here.
    """
    db = get_db()
    project = await _load_project_or_404(db, project_id, tenant_id)
    if not _can_view_project(current_user, project):
        raise HTTPException(status_code=403, detail="Permission denied")

    await _ensure_presence_indexes(db)

    viewport = body.viewport
    if viewport is not None and viewport not in _VALID_VIEWPORTS:
        raise HTTPException(
            status_code=422,
            detail=f"viewport must be one of {sorted(_VALID_VIEWPORTS)} (got {viewport!r})",
        )

    now = _utc_now()

    # Upsert: stamp last_seen + viewport for (project_id, user_id).
    # Phase 1E.3c — stamps tenant_id so presence rows can be tenant-filtered.
    await db["pm_presence"].update_one(
        {
            "project_id": project_id,
            "user_id": current_user.user_id,
            "tenant_id": tenant_id,
        },
        {
            "$set": {
                "project_id": project_id,
                "user_id": current_user.user_id,
                "tenant_id": tenant_id,
                "last_seen": now,
                "viewport": viewport,
            },
        },
        upsert=True,
    )

    # Read active list — exclude the caller; use the TTL window as a
    # belt-and-braces filter in case the TTL deleter hasn't run yet.
    # Phase 1E.3c — tenant-scoped so presence on a cross-tenant project
    # (already blocked by the 404 above) can't leak who's viewing it.
    cutoff = now - timedelta(seconds=_PRESENCE_ACTIVE_WINDOW_SECONDS)
    cursor = db["pm_presence"].find(
        {
            "project_id": project_id,
            "tenant_id": tenant_id,
            "user_id": {"$ne": current_user.user_id},
            "last_seen": {"$gte": cutoff},
        },
        {"_id": 0, "user_id": 1, "last_seen": 1, "viewport": 1},
    )
    rows = await cursor.to_list(length=200)  # 200 = generous; UI shows ~5 avatars

    # Hydrate user names/avatars in one batch (never per-row). Postgres: the
    # canonical avatar field is `picture` (exposed here as `avatar_url` to keep
    # the presence response shape unchanged).
    user_ids = [r["user_id"] for r in rows]
    user_lookup: Dict[str, Dict[str, Any]] = {}
    if user_ids:
        from db.session import get_sessionmaker
        from repositories.users import UserRepository
        _sm = get_sessionmaker()
        async with _sm() as _pg:
            _users = await UserRepository(_pg).get_users_by_ids(user_ids)
        for u in _users:
            if u.tenant_id == tenant_id:
                user_lookup[u.user_id] = {
                    "user_id": u.user_id,
                    "name": u.name,
                    "email": u.email,
                    "avatar_url": u.picture,
                }

    viewing: List[Dict[str, Any]] = []
    for r in rows:
        u = user_lookup.get(r["user_id"]) or {}
        last_seen = r.get("last_seen")
        if isinstance(last_seen, datetime):
            last_seen_iso = last_seen.isoformat()
        else:
            last_seen_iso = str(last_seen) if last_seen else None
        viewing.append({
            "user_id": r["user_id"],
            "name": u.get("name") or u.get("email") or r["user_id"],
            "avatar": u.get("avatar_url"),
            "viewport": r.get("viewport"),
            "last_seen": last_seen_iso,
        })

    return {
        "viewing": viewing,
        "now": now.isoformat(),
        "ttl_seconds": _PRESENCE_ACTIVE_WINDOW_SECONDS,
    }