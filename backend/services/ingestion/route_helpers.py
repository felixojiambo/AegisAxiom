"""
Generated-route helper for ingested apps.

Provides a single async context manager — `crud_envelope` — that wraps a CRUD
operation in an ingested-app route with:

    1. Pre-policy check (block on deny → HTTP 403)
    2. Yield to the route body
    3. Emit event to the in-process event bus
    4. Write a unified audit-log entry

This keeps generated route templates flat. The envelope is a coordinator only —
all real logic lives in `event_bus`, `policy_engine`, and `audit_service`.

Status-change semantics: ONE action per envelope call. If a route detects a
status field change during an update, it should open TWO envelopes — one with
action="updated" (full update payload) and one with action="status_changed"
(carrying the status delta). The envelope itself is action-agnostic.

Policy warnings: `evaluate_for_event` returns `{decision, reason, mutations}`
and discards the underlying policy engine's `warnings` list. Routes that need
to surface soft policy warnings to the API response must call
`policy_engine.evaluate_policies` directly and merge `_warnings` themselves.
The envelope only blocks on `deny`; it does not propagate `warnings`.

Usage example (this is the shape Wave 2 will paste into route templates):

    @router.patch("/projects/{project_id}")
    async def update_project(
        project_id: str,
        payload: ProjectUpdate,
        request: Request,
        current_user=Depends(get_current_user),
        db=Depends(get_db),
    ):
        if not has_perm(current_user, "davies_pmo.projects.update"):
            raise HTTPException(403, "Permission denied")

        before = await db.davies_pmo_projects.find_one({"project_id": project_id})
        if not before:
            raise HTTPException(404, "Not found")

        async with crud_envelope(
            module="davies_pmo",
            entity="projects",
            action="updated",
            resource_id=project_id,
            current_user=current_user,
            db=db,
            before=before,
            request=request,
        ) as ctx:
            updated = {**before, **payload.dict(exclude_unset=True)}
            await db.davies_pmo_projects.replace_one({"project_id": project_id}, updated)
            ctx.set_after(updated)

        return updated
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Literal, Optional

from fastapi import HTTPException, Request

from models import gen_id
from services.audit_service import log_ingested_crud
from services.event_bus import Event, emit
from services.policy_engine import evaluate_for_event

logger = logging.getLogger(__name__)

CrudAction = Literal[
    "created", "updated", "deleted", "status_changed", "assigned",
    "tier_changed", "summarized", "synced", "reordered",
]


class _CrudContext:
    """Mutator surface yielded into the `async with` body.

    Public methods:
        set_after(doc)        — stash the post-state document; required for
                                created/updated/status_changed so the event
                                payload and audit `after` are populated.
        set_resource_id(rid)  — overwrite the resource_id when the route
                                generates the ID inside the body (e.g. on
                                create, after the DB insert returns it).
        skip_audit()          — opt out of the audit-log write (rare; used
                                for bulk imports that emit a single summary
                                entry separately).
    """

    __slots__ = ("resource_id", "after", "_skip_audit")

    def __init__(self, resource_id: str) -> None:
        self.resource_id: str = resource_id
        self.after: Optional[dict] = None
        self._skip_audit: bool = False

    def set_after(self, doc: dict) -> None:
        self.after = doc

    def set_resource_id(self, rid: str) -> None:
        self.resource_id = rid

    def skip_audit(self) -> None:
        self._skip_audit = True


def _resolve_request_id(request: Optional[Request]) -> str:
    """Reuse `request.state.request_id` if present so multiple envelopes inside
    a single HTTP request correlate. Generate + stash one if not."""
    if request is None:
        return gen_id("req_")
    existing = getattr(request.state, "request_id", None)
    if existing:
        return existing
    new_id = gen_id("req_")
    try:
        request.state.request_id = new_id
    except Exception:  # request.state should always be writable, but be safe
        pass
    return new_id


def _build_event(
    *,
    module: str,
    entity: str,
    action: CrudAction,
    resource_id: str,
    actor_id: str,
    actor_email: str,
    payload: dict,
    previous_state: Optional[dict],
    request_id: str,
) -> Event:
    return Event(
        name=f"{module}.{entity}.{action}",
        module=module,
        entity=entity,
        action=action,
        resource_id=resource_id,
        actor_id=actor_id,
        actor_email=actor_email,
        timestamp=datetime.now(timezone.utc),
        payload=payload,
        previous_state=previous_state,
        request_id=request_id,
    )


@asynccontextmanager
async def crud_envelope(
    module: str,
    entity: str,
    action: CrudAction,
    resource_id: str,
    current_user: Any,
    db: Any,
    before: Optional[dict] = None,
    request: Optional[Request] = None,
) -> AsyncIterator[_CrudContext]:
    """Coordinate policy → body → event → audit for an ingested-app CRUD op.

    See module docstring for usage. On policy `deny`, raises HTTPException(403)
    BEFORE yielding (the body never runs). If the body raises, the exception
    propagates and NO event is emitted, NO audit entry is written — the
    operation didn't complete, so observability stays silent.

    Post-success failures in `emit()` or `log_ingested_crud()` are logged and
    swallowed; the CRUD result is correctness, the audit/event hop is
    best-effort observability.
    """
    request_id = _resolve_request_id(request)
    actor_id = getattr(current_user, "user_id", "") or ""
    actor_email = getattr(current_user, "email", "") or ""

    # 1. Tentative event for pre-policy evaluation. Payload is `before` for
    #    update/delete (state being acted on) or empty for create.
    tentative_payload: dict = before if before is not None else {}
    tentative_event = _build_event(
        module=module,
        entity=entity,
        action=action,
        resource_id=resource_id,
        actor_id=actor_id,
        actor_email=actor_email,
        payload=tentative_payload,
        previous_state=before,
        request_id=request_id,
    )

    # 2. Pre-policy check — deny short-circuits with 403 and never runs body.
    # Fail-closed: an unexpected policy-engine exception raises 503 rather
    # than silently allowing. Compliance/security policies must not be
    # bypassed by transient infrastructure errors.
    try:
        decision = await evaluate_for_event(tentative_event, db)
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "policy_engine.evaluate_for_event raised; failing closed for "
            "event %s request_id=%s",
            tentative_event["name"],
            request_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Policy check unavailable — try again",
        )

    if isinstance(decision, dict) and decision.get("decision") == "deny":
        reason = decision.get("reason") or "Policy denied this operation"
        raise HTTPException(status_code=403, detail=reason)

    ctx = _CrudContext(resource_id=resource_id)

    # 3. Run the route body. If it raises, propagate without emit/audit.
    yield ctx

    # 4. Body succeeded — build the final event with the after-state and
    #    fan out to event bus + audit log. Both are best-effort.
    after_payload: dict = ctx.after if ctx.after is not None else {}
    final_event = _build_event(
        module=module,
        entity=entity,
        action=action,
        resource_id=ctx.resource_id,
        actor_id=actor_id,
        actor_email=actor_email,
        payload=after_payload,
        previous_state=before,
        request_id=request_id,
    )

    try:
        await emit(final_event)
    except Exception:
        logger.exception(
            "event_bus.emit failed for %s request_id=%s",
            final_event["name"],
            request_id,
        )

    if ctx._skip_audit:
        return

    try:
        await log_ingested_crud(
            db=db,
            module=module,
            entity=entity,
            action=action,
            resource_id=ctx.resource_id,
            actor=current_user,
            before=before,
            after=ctx.after,
            request_id=request_id,
        )
    except Exception:
        logger.exception(
            "audit_service.log_ingested_crud failed for %s/%s/%s "
            "resource_id=%s request_id=%s",
            module,
            entity,
            action,
            ctx.resource_id,
            request_id,
        )
