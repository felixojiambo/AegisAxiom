"""Projects ingestion — "My work" surface for the calling user.

Wave 6 / Thread A / Contract 1. Read-only endpoints scoped to the caller
(`current_user.user_id`) so a non-admin PM can answer the question "what's on
my plate?" without admin help.

Routes:
  * `GET /api/projects/projects/mine` — projects where caller is `pm_id` OR
    in `assigned_user_ids`.
  * `GET /api/projects/tasks/assigned-to-me` — tasks where `assignee_id ==
    caller`. Filters: `status`, `overdue`, `limit`.
  * `GET /api/projects/blocker-tickets/owned-by-me` — blockers where the
    caller owns the source thread (the thread that filed the blocker, by
    way of `pm_threads.owner_id == caller`) OR the target thread.
  * `GET /api/projects/dashboard/my-summary` — aggregate counts: open
    projects, overdue tasks, open blockers, upcoming deadlines (14-day).

RBAC: each endpoint requires the matching `projects.<entity>.view` perm
(plus `projects.dashboard.view` for the summary). No global view-all is
required — the queries are filtered to caller-owned data, so a PM with
only the standard projects.* perms can hit these and get only their own
work back.

These routes live in their own file (rather than folded into
`projects_dashboard.py`) so the "user-mine" RBAC scope is contained and
discoverable. `dashboard/my-summary` is the only one that overlaps with
the existing dashboard surface, but it stays here to keep the user-mine
contract together.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id
from models import gen_id
from services.field_registry import get_module_events, register_module_events
from services.projects_methodology import DONE_STATUSES

logger = logging.getLogger(__name__)
router = APIRouter()


# Read-only surface; register an empty events list for discoverability so
# the field-registry's module index lists `projects.my_work` alongside the
# real entities.
register_module_events("projects", "my_work", [])


# Wave 6 / Thread A / Contract 3: register the three notification-event
# keys onto their owning entities — `projects.task.overdue`,
# `projects.project.health_red`, `projects.blocker.stale`. These are
# emitted (in v1) by the /notifications/scan endpoint below; future cron
# jobs or background tasks may also fire them. We append rather than
# overwrite each entity's existing event list so the canonical
# created/updated/deleted events on task/project/blocker_ticket are
# preserved.
def _extend_events(module: str, entity: str, new_events: List[str]) -> None:
    """Append new event keys onto an entity's registered list, preserving
    whatever was registered earlier in import order."""
    existing = list(get_module_events(module, entity))
    merged = existing + [e for e in new_events if e not in existing]
    register_module_events(module, entity, merged)


_extend_events("projects", "task", ["overdue"])
_extend_events("projects", "project", ["health_red"])
# Note: the contract calls this `projects.blocker.stale`, but the existing
# entity name in AEGISAXIOM is `blocker_ticket` (see projects_blocker_ticket.py).
# We register under the canonical entity name so other code reading the
# event registry stays consistent. The notification scan still labels the
# event kind as "blocker.stale" in payloads for human-readability.
_extend_events("projects", "blocker_ticket", ["stale"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _scrub(doc: Optional[dict]) -> Optional[dict]:
    if doc is None:
        return None
    out = {**doc}
    out.pop("_id", None)
    return out


async def _owned_thread_ids(db: Any, user_id: str, tenant_id: str) -> List[str]:
    """Thread IDs where this user is the owner. Used to scope blockers.

    Phase 1E.3c — tenant-scoped. user_id is itself tenant-unique, so the
    filter is defensive; threads in another tenant won't be returned even
    if the user_id somehow matched (which it can't since user_id is also
    tenant-scoped).
    """
    cursor = db["pm_threads"].find(
        {"owner_id": user_id, "tenant_id": tenant_id},
        {"_id": 0, "thread_id": 1},
    )
    return [d["thread_id"] async for d in cursor if d.get("thread_id")]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/projects/mine",
    dependencies=[Depends(require_ai_permission("projects.project.view"))],
)
async def list_my_projects(
    skip: int = 0,
    limit: int = 100,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Projects where caller is `pm_id` OR appears in `assigned_user_ids`.

    Mirrors the shape of `GET /projects` (items / total / skip / limit) so
    the frontend can swap data sources without rewriting the rendering
    layer. Sorted by `created_at desc` to match the global list view.
    """
    if not has_perm(current_user, "projects.project.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    capped = min(limit, 500)
    user_id = current_user.user_id

    # Phase 1E.3c — restrict to current tenant. The user_id filter is
    # already tenant-effective (user_id is tenant-unique), but the explicit
    # tenant_id makes the index hit cleaner and protects against any future
    # change to user_id allocation.
    query: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "$or": [
            {"pm_id": user_id},
            {"assigned_user_ids": user_id},
        ],
    }
    total = await db["projects"].count_documents(query)
    items = (
        await db["projects"]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(capped)
        .to_list(capped)
    )
    return {"items": items, "total": total, "skip": skip, "limit": capped}


@router.get(
    "/tasks/assigned-to-me",
    dependencies=[Depends(require_ai_permission("projects.task.view"))],
)
async def list_my_tasks(
    status: Optional[str] = Query(None),
    overdue: Optional[bool] = Query(None),
    limit: int = Query(default=100, le=500),
    skip: int = 0,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Tasks where `assignee_id == caller`. Filters: status, overdue.

    `overdue=true` filters to tasks with `due_date < today` AND status NOT
    in `DONE_STATUSES`. `overdue=false` returns the inverse (not overdue).
    Sorted by due_date asc (nulls last) so the most pressing items show
    first; ties broken by created_at desc.
    """
    if not has_perm(current_user, "projects.task.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    user_id = current_user.user_id

    # Phase 1E.3c — restrict to current tenant. Same reasoning as above.
    query: Dict[str, Any] = {"assignee_id": user_id, "tenant_id": tenant_id}
    if status:
        query["status"] = status

    today = _today_iso()
    if overdue is True:
        query["due_date"] = {"$lt": today, "$ne": None}
        query["status"] = query.get("status") or {"$nin": list(DONE_STATUSES)}
        # If caller passed an explicit status that IS done, the result will
        # be empty — that's a deliberate choice, not a bug.
    elif overdue is False:
        # Not overdue: due_date is null OR due_date >= today OR status is done.
        not_overdue: List[Dict[str, Any]] = [
            {"due_date": None},
            {"due_date": {"$gte": today}},
            {"status": {"$in": list(DONE_STATUSES)}},
        ]
        # Move our user_id assignee filter into an $and so the $or doesn't
        # widen the scope back to all users.
        query = {
            "$and": [
                {"assignee_id": user_id, "tenant_id": tenant_id},
                {"$or": not_overdue},
            ]
        }
        if status:
            query["$and"].append({"status": status})

    total = await db["pm_tasks"].count_documents(query)
    cursor = (
        db["pm_tasks"]
        .find(query, {"_id": 0})
        .sort([("due_date", 1), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
    )
    items = [doc async for doc in cursor]
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get(
    "/blocker-tickets/owned-by-me",
    dependencies=[Depends(require_ai_permission("projects.blocker_ticket.view"))],
)
async def list_my_blockers(
    skip: int = 0,
    limit: int = Query(default=100, le=500),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Blockers where caller owns either the source thread or the target
    thread. Source-of-truth ownership is `pm_threads.owner_id == caller`;
    we resolve thread IDs first then match against the blocker's
    `thread_id` (source) and `target_thread_id`.

    Note: the contract docstring says "source task," but blockers are
    filed against threads, not tasks (waves_threads methodology). We
    interpret "source task/thread" as the source thread for this scope.
    """
    if not has_perm(current_user, "projects.blocker_ticket.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    user_id = current_user.user_id

    owned_thread_ids = await _owned_thread_ids(db, user_id, tenant_id)
    if not owned_thread_ids:
        return {"items": [], "total": 0, "skip": skip, "limit": limit}

    # Phase 1E.3c — tenant_id on the blocker filter is defensive (the
    # thread_ids we matched above are already tenant-scoped).
    query: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "$or": [
            {"thread_id": {"$in": owned_thread_ids}},
            {"target_thread_id": {"$in": owned_thread_ids}},
        ],
    }
    total = await db["pm_blocker_tickets"].count_documents(query)
    cursor = (
        db["pm_blocker_tickets"]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )
    items = [doc async for doc in cursor]
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get(
    "/dashboard/my-summary",
    dependencies=[Depends(require_ai_permission("projects.dashboard.view"))],
)
async def my_summary(
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Single-shot aggregation of caller-scoped counts for a "My Work" card.

    Returns:
        open_projects.count          — projects where caller is PM or
                                        assigned, status != closed
        overdue_tasks.count          — assignee_id == caller AND
                                        due_date < today AND status NOT in
                                        DONE_STATUSES
        open_blockers.count          — blockers on owned threads with
                                        status in {open, acknowledged, escalated}
        upcoming_deadlines.count     — caller-assigned tasks due in the
                                        next 14 days (excluding done)
        upcoming_deadlines.items     — top 5 of those, lightweight rows
                                        (task_id, title, due_date,
                                        project_id, project_name) so the
                                        card can show a peek without a
                                        second round-trip

    Caller needs `projects.dashboard.view`. The aggregations are fanned
    out via asyncio.gather so the cold-load is one round-trip's worth of
    latency, not four (mirrors the dashboard summary pattern).

    Phase 1E.3c — every filter and pipeline scoped to current tenant.
    """
    if not has_perm(current_user, "projects.dashboard.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    user_id = current_user.user_id
    now = datetime.now(timezone.utc)
    today_iso = now.date().isoformat()
    cutoff_iso = (now + timedelta(days=14)).date().isoformat()

    open_projects_q = {
        "tenant_id": tenant_id,
        "$and": [
            {"$or": [{"pm_id": user_id}, {"assigned_user_ids": user_id}]},
            {"status": {"$ne": "closed"}},
        ],
    }
    overdue_tasks_q = {
        "tenant_id": tenant_id,
        "assignee_id": user_id,
        "due_date": {"$lt": today_iso, "$ne": None},
        "status": {"$nin": list(DONE_STATUSES)},
    }
    upcoming_q = {
        "tenant_id": tenant_id,
        "assignee_id": user_id,
        "due_date": {"$gte": today_iso, "$lte": cutoff_iso, "$ne": None},
        "status": {"$nin": list(DONE_STATUSES)},
    }
    upcoming_pipeline = [
        {"$match": upcoming_q},
        {"$sort": {"due_date": 1}},
        {"$limit": 5},
        {
            "$lookup": {
                "from": "projects",
                "localField": "project_id",
                "foreignField": "project_id",
                "as": "_project",
            }
        },
        {"$unwind": {"path": "$_project", "preserveNullAndEmptyArrays": True}},
        {
            "$project": {
                "_id": 0,
                "task_id": "$task_id",
                "title": "$title",
                "due_date": "$due_date",
                "project_id": "$project_id",
                "project_name": "$_project.name",
            }
        },
    ]

    owned_thread_ids = await _owned_thread_ids(db, user_id, tenant_id)
    open_blocker_status = ["open", "acknowledged", "escalated"]

    async def _zero() -> int:
        return 0

    if owned_thread_ids:
        open_blockers_q: Dict[str, Any] = {
            "tenant_id": tenant_id,
            "$and": [
                {
                    "$or": [
                        {"thread_id": {"$in": owned_thread_ids}},
                        {"target_thread_id": {"$in": owned_thread_ids}},
                    ]
                },
                {"status": {"$in": open_blocker_status}},
            ],
        }
        open_blockers_coro = db["pm_blocker_tickets"].count_documents(open_blockers_q)
    else:
        open_blockers_coro = _zero()

    (
        open_projects_count,
        overdue_tasks_count,
        upcoming_deadlines_count,
        upcoming_deadlines_items,
        open_blockers_count,
    ) = await asyncio.gather(
        db["projects"].count_documents(open_projects_q),
        db["pm_tasks"].count_documents(overdue_tasks_q),
        db["pm_tasks"].count_documents(upcoming_q),
        db["pm_tasks"].aggregate(upcoming_pipeline).to_list(5),
        open_blockers_coro,
    )

    return {
        "user_id": user_id,
        "open_projects": {"count": open_projects_count},
        "overdue_tasks": {"count": overdue_tasks_count},
        "open_blockers": {"count": open_blockers_count},
        "upcoming_deadlines": {
            "count": upcoming_deadlines_count,
            "items": upcoming_deadlines_items,
        },
    }


# ---------------------------------------------------------------------------
# Notification surface (Wave 6 / Thread A / Contract 3)
#
# Three event categories are surfaced through the existing AEGISAXIOM notifications
# module (db.notifications, same shape used by services/workflow_executor.py):
#
#   * projects.task.overdue       — task with due_date < today and status
#                                    NOT in DONE_STATUSES; notifies the
#                                    assignee_id (one notification per task)
#   * projects.project.health_red — project with health="red"; notifies
#                                    pm_id and every assigned_user_id (one
#                                    notification per recipient per project)
#   * projects.blocker.stale      — blocker with status in {open,
#                                    acknowledged, escalated} that's
#                                    older than STALE_BLOCKER_DAYS; notifies
#                                    the source thread's owner and (when
#                                    set) the target thread's owner
#
# Deduplication: we stamp each notification with a deterministic
# `notification_key` of the form `<event>:<resource_id>:<recipient>:<date>`
# so a same-day re-run of /notifications/scan upserts (no double-fire). The
# date component lets the scan re-fire on a NEW day when the underlying
# condition is still true — that's deliberate: a still-overdue task on day
# 2 is still worth surfacing.
#
# v1 has no scheduler — admin (or a future cron) hits POST
# /notifications/scan daily. The endpoint is admin-gated to keep the
# feature off non-admin users until the cron is wired.
# ---------------------------------------------------------------------------
STALE_BLOCKER_DAYS = 7


def _notification_key(event: str, resource_id: str, recipient: str, day_iso: str) -> str:
    """Deterministic dedup key for the notifications collection."""
    return f"{event}:{resource_id}:{recipient}:{day_iso}"


async def _upsert_notification(
    db: Any,
    *,
    user_id: str,
    title: str,
    body: str,
    record_id: str,
    event: str,
    day_iso: str,
    tenant_id: str,
) -> bool:
    """Upsert a notification keyed by the (event, resource, recipient, day).
    Returns True if a new notification was inserted, False if it already
    existed for today (no-op).

    Phase 1E.3c — stamps tenant_id and scopes the dedup lookup. A given
    notification_key collision must also be in the same tenant to count
    as a dedup hit (notification_key already encodes recipient and resource,
    and both are tenant-unique, so the tenant filter is defensive).
    """
    key = _notification_key(event, record_id, user_id, day_iso)
    existing = await db.notifications.find_one(
        {"notification_key": key, "tenant_id": tenant_id}, {"_id": 1}
    )
    if existing:
        return False
    await db.notifications.insert_one(
        {
            "notification_id": gen_id("notif_"),
            # Phase 1E.3c — stamp owning tenant on every notification.
            "tenant_id": tenant_id,
            "notification_key": key,
            "user_id": user_id,
            "title": title,
            "body": body,
            "module": "projects",
            "event": event,
            "record_id": record_id,
            "read": False,
            "created_at": datetime.now(timezone.utc),
        }
    )
    return True


async def _scan_overdue_tasks(db: Any, today_iso: str, tenant_id: str) -> int:
    """Generate `projects.task.overdue` notifications for the assignee of
    each overdue, not-done task. Returns count of new notifications.

    Phase 1E.3c — scans only this tenant's tasks.
    """
    cursor = db["pm_tasks"].find(
        {
            "tenant_id": tenant_id,
            "due_date": {"$lt": today_iso, "$ne": None},
            "status": {"$nin": list(DONE_STATUSES)},
            "assignee_id": {"$ne": None},
        },
        {"_id": 0, "task_id": 1, "title": 1, "due_date": 1, "assignee_id": 1, "project_id": 1},
    )
    inserted = 0
    async for t in cursor:
        title = "Task overdue"
        body = (
            f"\"{t.get('title') or '(untitled)'}\" was due "
            f"{t.get('due_date')} and is not done."
        )
        if await _upsert_notification(
            db,
            user_id=t["assignee_id"],
            title=title,
            body=body,
            record_id=t["task_id"],
            event="projects.task.overdue",
            day_iso=today_iso,
            tenant_id=tenant_id,
        ):
            inserted += 1
    return inserted


async def _scan_red_health_projects(db: Any, today_iso: str, tenant_id: str) -> int:
    """Generate `projects.project.health_red` notifications for the PM and
    every assigned user of a red-health project.

    Phase 1E.3c — scans only this tenant's projects.
    """
    cursor = db["projects"].find(
        {"health": "red", "tenant_id": tenant_id},
        {
            "_id": 0,
            "project_id": 1,
            "name": 1,
            "pm_id": 1,
            "assigned_user_ids": 1,
        },
    )
    inserted = 0
    async for p in cursor:
        recipients: set = set()
        if p.get("pm_id"):
            recipients.add(p["pm_id"])
        for uid in p.get("assigned_user_ids") or []:
            if uid:
                recipients.add(uid)
        if not recipients:
            continue
        title = "Project health: red"
        body = f"\"{p.get('name') or '(unnamed)'}\" is in red health and needs attention."
        for uid in recipients:
            if await _upsert_notification(
                db,
                user_id=uid,
                title=title,
                body=body,
                record_id=p["project_id"],
                event="projects.project.health_red",
                day_iso=today_iso,
                tenant_id=tenant_id,
            ):
                inserted += 1
    return inserted


async def _scan_stale_blockers(db: Any, today_iso: str, tenant_id: str) -> int:
    """Generate `projects.blocker.stale` notifications for source/target
    thread owners on blockers older than STALE_BLOCKER_DAYS in an open
    state.

    Phase 1E.3c — scans only this tenant's blockers and threads.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=STALE_BLOCKER_DAYS)
    ).isoformat()
    cursor = db["pm_blocker_tickets"].find(
        {
            "tenant_id": tenant_id,
            "status": {"$in": ["open", "acknowledged", "escalated"]},
            "created_at": {"$lt": cutoff},
        },
        {
            "_id": 0,
            "blocker_ticket_id": 1,
            "thread_id": 1,
            "target_thread_id": 1,
            "description": 1,
            "status": 1,
            "created_at": 1,
        },
    )
    inserted = 0
    async for b in cursor:
        recipients: set = set()
        for tid in (b.get("thread_id"), b.get("target_thread_id")):
            if not tid:
                continue
            thread = await db["pm_threads"].find_one(
                {"thread_id": tid, "tenant_id": tenant_id},
                {"_id": 0, "owner_id": 1},
            )
            if thread and thread.get("owner_id"):
                recipients.add(thread["owner_id"])
        if not recipients:
            continue
        title = "Stale blocker"
        body = (
            f"Blocker open since {b.get('created_at')}: "
            f"\"{(b.get('description') or '')[:120]}\""
        )
        for uid in recipients:
            if await _upsert_notification(
                db,
                user_id=uid,
                title=title,
                body=body,
                record_id=b["blocker_ticket_id"],
                event="projects.blocker.stale",
                day_iso=today_iso,
                tenant_id=tenant_id,
            ):
                inserted += 1
    return inserted


@router.post(
    "/notifications/scan",
    dependencies=[Depends(require_ai_permission("admin.manage"))],
)
async def scan_notifications(
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Run the three scan queries (overdue tasks, red-health projects,
    stale blockers) and write notifications into `db.notifications`.

    Admin-gated. v1 has no scheduler — an admin (or a future cron) hits
    this endpoint daily. Idempotent within a single day via the
    `notification_key` dedup field; re-fires the next day when the
    condition is still true.

    Returns a per-category count of new notifications written so the
    caller (cron, admin UI, smoke test) has a quick health check.

    Phase 1E.3c — the scan operates on the caller's tenant only. A
    future cron-driven sweep across all tenants would iterate over
    `tenants` collection and invoke each scan with the matching
    `tenant_id`, replacing the single-tenant trigger here.
    """
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    db = get_db()
    today_iso = datetime.now(timezone.utc).date().isoformat()

    overdue_count = await _scan_overdue_tasks(db, today_iso, tenant_id)
    red_health_count = await _scan_red_health_projects(db, today_iso, tenant_id)
    stale_count = await _scan_stale_blockers(db, today_iso, tenant_id)

    return {
        "scanned_at": today_iso,
        "tenant_id": tenant_id,
        "notifications_created": {
            "projects.task.overdue": overdue_count,
            "projects.project.health_red": red_health_count,
            "projects.blocker.stale": stale_count,
        },
        "total": overdue_count + red_health_count + stale_count,
    }


@router.get(
    "/notifications/my-summary",
    dependencies=[Depends(require_ai_permission("projects.dashboard.view"))],
)
async def my_notification_summary(
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Per-category notification counts for the calling user's "My Work"
    inbox card. Returns counts only — the existing
    GET /notifications endpoint (in workflow_rules.py) is the canonical
    list view; this is a lightweight aggregation for the dashboard card.

    Counts unread notifications grouped by event key, plus an `unread`
    total. The card renders three category badges (overdue / red-health /
    stale) so a PM gets a single-glance answer to "anything new for me?"

    Phase 1E.3c — only notifications stamped with the current tenant's
    tenant_id count. Cross-tenant notifications can't leak even if a
    rogue notification_key collision happened.
    """
    if not has_perm(current_user, "projects.dashboard.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    user_id = current_user.user_id

    pipeline = [
        {
            "$match": {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "module": "projects",
                "read": False,
            }
        },
        {
            "$group": {
                "_id": "$event",
                "count": {"$sum": 1},
            }
        },
    ]
    by_event: Dict[str, int] = {}
    async for row in db.notifications.aggregate(pipeline):
        key = row.get("_id")
        if key:
            by_event[key] = row.get("count", 0)

    total_unread = sum(by_event.values())
    return {
        "user_id": user_id,
        "unread_total": total_unread,
        "by_event": {
            "projects.task.overdue": by_event.get("projects.task.overdue", 0),
            "projects.project.health_red": by_event.get(
                "projects.project.health_red", 0
            ),
            "projects.blocker.stale": by_event.get("projects.blocker.stale", 0),
        },
    }
