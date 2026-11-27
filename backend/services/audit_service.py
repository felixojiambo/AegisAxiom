import hashlib
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Literal, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_sessionmaker
from models import User
from repositories.audit import AuditRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The one audit_logs write path (Phase 2b)
# ---------------------------------------------------------------------------
async def write_audit_entry(entry: dict, *, session: Optional[AsyncSession] = None) -> bool:
    """Persist one audit row. True iff inserted (False = ``audit_id`` existed).

    Every audit writer (log_action, log_ingested_crud, security_logger,
    conversational-tools AI actions, ingestion stage events) funnels here.

    Without ``session`` the row is written in its OWN short transaction and
    committed immediately (plan §A6.2 "must commit independently"): an audit
    row — especially a security denial logged just before an HTTPException —
    must survive the caller's request rolling back. ``session`` is for tests
    and for callers that deliberately want the row inside their transaction.
    """
    if session is not None:
        return await AuditRepository(session).record(entry)
    async with get_sessionmaker()() as own:
        try:
            inserted = await AuditRepository(own).record(entry)
            await own.commit()
            return inserted
        except Exception:
            await own.rollback()
            raise


# ---------------------------------------------------------------------------
# AI actor context — v1.3 Theme B (single-row audit, retires dual-write)
#
# AI tool handlers set this contextvar at the entry of every dispatch so the
# downstream crud_envelope write (via log_ingested_crud) can stamp
# `actor_type: "pmo_ai"` + `tool_name` + `tier` directly onto the single
# activity row. Retires the separate `event_category="ai_action"` write that
# v1.2 used for the dual-write pattern.
#
# Default is an empty dict so a human-initiated write produces no actor_type
# stamp (downstream readers default to "user" when absent). Contextvars are
# task-local, so a concurrent human request and AI request can't see each
# other's stamps.
#
# Shape:
#   {"actor_type": "pmo_ai", "tool_name": str, "tier": int, "session_id": str|None}
# ---------------------------------------------------------------------------
ai_actor_ctx: ContextVar[dict] = ContextVar("ai_actor_ctx", default={})


async def log_action(
    action: str,
    actor_id: str,
    actor_email: str,
    resource_type: str,
    resource_id: str = None,
    details: dict = None,
):
    """Record a sensitive activity in the unified audit log.

    Tags entries with event_category="activity" so the admin UI can
    distinguish them from security events written by
    services.security_logger.log_security_event into the same collection.

    Audit failure must never break the user's action. The entire body
    runs inside a single try/except so a Mongo client misconfiguration,
    a missing database init in a test path, or an upstream Mongo
    transient error all degrade to a logged warning instead of
    propagating into the caller's response. The earlier shape only
    wrapped the insert call, which left the datastore handle unwrapped —
    a real production hazard the cutover surfaced through the proposal
    send path.
    """
    try:
        entry = {
            "audit_id": f"audit_{uuid.uuid4().hex[:16]}",
            "event_category": "activity",
            "action": action,
            "actor_id": actor_id,
            "actor_email": actor_email,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc),
        }
        await write_audit_entry(entry)
    except Exception as e:  # noqa: BLE001 — audit must never raise
        logger.error(f"Failed to write audit log: {e}")


def _compute_diff(before: dict, after: dict) -> dict:
    """Key-by-key diff. Skips fields where before == after.

    Treats missing keys on either side as a change (None -> value or value -> None).
    Returned shape: {"<field>": {"before": x, "after": y}, ...}
    """
    diff: dict = {}
    keys = set((before or {}).keys()) | set((after or {}).keys())
    for k in keys:
        b = (before or {}).get(k)
        a = (after or {}).get(k)
        if b != a:
            diff[k] = {"before": b, "after": a}
    return diff


async def log_ingested_crud(
    module: str,
    entity: str,
    action: Literal["created", "updated", "deleted", "status_changed"],
    resource_id: str,
    actor: User,
    before: Optional[dict],
    after: Optional[dict],
    request_id: str,
    db=None,
) -> str:
    """Write a single audit_logs row for an ingested-app CRUD operation.

    Idempotent: a duplicate (request_id, resource_id, action) tuple is a no-op.
    Phase 2b: ``audit_id`` is the table's primary key, so the deterministic id
    makes the insert ``ON CONFLICT DO NOTHING`` — race-free in one statement,
    replacing the Mongo find-before-insert.

    ``db`` is accepted for caller compatibility (services/ingestion/
    route_helpers.py still passes its Mongo handle) and ignored.

    Diff strategy:
      - created:                details.after  = full doc
      - updated/status_changed: details.diff   = key-by-key diff (empty diff
                                                  still writes — useful for
                                                  forensic "noop update" trail)
      - deleted:                details.before = full doc (preserved post-delete)

    Returns the audit_id (existing one if a duplicate was found).
    """
    # Deterministic audit_id so retries collapse to the same row (the PK
    # conflict in write_audit_entry makes a retry a no-op).
    audit_id = hashlib.sha256(
        f"{request_id}|{resource_id}|{action}".encode("utf-8")
    ).hexdigest()[:32]

    details: dict = {
        "module": module,
        "entity": entity,
        "action": action,
        "request_id": request_id,
    }
    if action == "created":
        details["after"] = after or {}
    elif action == "deleted":
        details["before"] = before or {}
    else:  # updated, status_changed
        details["diff"] = _compute_diff(before or {}, after or {})

    # v1.3 Theme B: if an AI tool set the actor context, stamp it onto the
    # single activity row so the recent-changes reader can attribute the
    # write to pmo_ai + tool_name. Default is "user" (absent → reader treats
    # as user). This replaces the v1.2 dual-write pattern.
    ai_ctx = ai_actor_ctx.get() or {}
    actor_type = ai_ctx.get("actor_type") or "user"
    tool_name = ai_ctx.get("tool_name")
    if actor_type != "user":
        details["actor_type"] = actor_type
    if tool_name:
        details["tool_name"] = tool_name
    if ai_ctx.get("tier") is not None:
        details["tier"] = ai_ctx["tier"]
    if ai_ctx.get("session_id"):
        details["session_id"] = ai_ctx["session_id"]

    entry = {
        "audit_id": audit_id,
        "event_category": "activity",
        "event_type": f"ingested_app.{action}",
        "action": action,
        "resource_type": f"{module}.{entity}",
        "resource_id": resource_id,
        "actor_id": actor.user_id,
        "actor_email": actor.email,
        "details": details,
        "timestamp": datetime.now(timezone.utc),
    }

    # Phase 1E.3c — tenant_id stamping on audit_logs.
    #
    # Multi-tenant isolation requires the recent-changes feed (and any
    # future per-tenant audit query) to filter audit rows by tenant. The
    # before/after docs we receive from crud_envelope always carry a
    # tenant_id field for tenant-bearing entities (stamped by all 1E.3c
    # phases — projects/tasks/waves/sprints/threads/blockers/standups/
    # comments/attachments/views/etc). Extract that here so audit_logs
    # rows describe whose data the action touched.
    #
    # Precedence: prefer after (always present for create/update); fall
    # back to before for delete. If neither has tenant_id (system entities
    # like roles, or pre-1E.3c historical writes), leave the field absent
    # — readers should treat missing tenant_id as "global / pre-multitenant"
    # and apply their own policy.
    tenant_id = (after or {}).get("tenant_id") or (before or {}).get("tenant_id")
    if tenant_id:
        entry["tenant_id"] = tenant_id

    # Top-level mirror for actor_type/tool_name so admin consumers that read
    # the row without traversing `details` (legacy / audit UI) still see
    # them. The recent-changes normalizer prefers `details` but falls back
    # to top-level for compatibility.
    if actor_type != "user":
        entry["actor_type"] = actor_type
    if tool_name:
        entry["tool_name"] = tool_name
    try:
        await write_audit_entry(entry)
    except Exception as e:
        logger.error(f"Failed to write ingested CRUD audit log: {e}")

    # The (resource_type, resource_id, timestamp DESC) index this used to ask
    # for now ships in migration 0005_platform_core_rbac.

    return audit_id
