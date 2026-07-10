"""Projects ingestion — Dashboard summary route.

Adapted from Davies-PMO-app `backend/app/api/dashboard.py`. Differences:
  * Collections renamed: `tasks` -> `pm_tasks`, `project_status_history` ->
    `pm_project_status_history`, `task_status_history` -> `pm_task_status_history`.
    `projects` and `users` keep their names.
  * `$lookup` joins now use the string fields `project_id` / `task_id`
    (AEGISAXIOM-native) instead of `_id` ObjectIds. The Davies pipelines were
    matching on `_id`, so each lookup has been rewritten to match on the
    string FK on both sides.
  * Reads only — no `crud_envelope` wrapping.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id
from services.field_registry import register_module_events
from services.projects_methodology import (
    DONE_STATUSES,
    WATERFALL_STATUSES,
    WAVES_THREADS_STATUSES,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# Dashboard has no fields/collection of its own; only register events for
# discoverability (empty list = read-only surface).
register_module_events("projects", "dashboard", [])


# ---------------------------------------------------------------------------
# Card helpers
# ---------------------------------------------------------------------------
async def _project_health(db: Any, tenant_id: str) -> Dict[str, int]:
    out = {"green": 0, "yellow": 0, "red": 0}
    # Phase 1E.3c — aggregate only this tenant's projects.
    async for doc in db["projects"].aggregate(
        [
            {"$match": {"tenant_id": tenant_id}},
            {"$group": {"_id": "$health", "count": {"$sum": 1}}},
        ]
    ):
        key = doc.get("_id")
        if key in out:
            out[key] = doc.get("count", 0)
    return out


async def _active_projects_count(db: Any, now: datetime, tenant_id: str) -> Dict[str, int]:
    """Count of currently active projects + delta vs 7 days ago.

    Walks `pm_project_status_history` for entries in the last 7 days; for any
    project that flipped status in the window, its status 7 days ago is
    `from_status` of the earliest entry. Projects with no entries kept the
    same status. Projects created after the cutoff didn't exist 7 days ago.

    Phase 1E.3c — every collection touch scoped by tenant_id.
    """
    cutoff = now - timedelta(days=7)
    current = await db["projects"].count_documents(
        {"status": "active", "tenant_id": tenant_id}
    )

    earliest: Dict[str, str] = {}
    cursor = db["pm_project_status_history"].find(
        {"changed_at": {"$gte": cutoff}, "tenant_id": tenant_id},
        {"_id": 0, "project_id": 1, "from_status": 1, "changed_at": 1},
    ).sort("changed_at", 1)
    async for h in cursor:
        pid = h.get("project_id")
        if pid and pid not in earliest:
            earliest[pid] = h.get("from_status") or ""

    active_then = 0
    async for p in db["projects"].find(
        {"tenant_id": tenant_id},
        {"_id": 0, "project_id": 1, "status": 1, "created_at": 1},
    ):
        # `created_at` may be ISO string OR datetime; normalise for compare.
        # BSON datetimes come back naive from motor (no tz_aware client), and
        # ISO strings without a tz suffix parse to naive. `cutoff` is tz-aware
        # because `now = datetime.now(timezone.utc)`. A naive-vs-aware compare
        # raises TypeError, which previously 500'd the whole dashboard once
        # any imported Zoho project landed in the projects collection. Stamp
        # UTC on naive values before comparing — Mongo guarantees UTC for
        # BSON dates, and the historical ISO-string writers in this repo all
        # use UTC.
        # PyMongo strips tzinfo from BSON datetimes on read, so a value written
        # as `datetime.now(timezone.utc)` comes back naive UTC. Comparing a
        # naive datetime to `cutoff` (tz-aware) raises TypeError and 500s the
        # whole dashboard — attach UTC explicitly when the field is naive.
        created = p.get("created_at")
        created_dt: datetime | None = None
        if isinstance(created, datetime):
            created_dt = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
        elif isinstance(created, str):
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                created_dt = None
        if created_dt is not None:
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            if created_dt > cutoff:
                continue  # didn't exist 7 days ago
        pid = p.get("project_id")
        status_then = earliest.get(pid, p.get("status"))
        if status_then == "active":
            active_then += 1

    return {"count": current, "delta_vs_last_week": current - active_then}


async def _tasks_by_status(db: Any, tenant_id: str) -> List[Dict[str, Any]]:
    """Three entries (waterfall, agile, waves_threads). Agile buckets keyed
    by each task's stored status (== column name).

    Wave 4 / A5 hardening:
      * Out-of-enum statuses for waterfall/W&T are surfaced via a
        self-describing sentinel entry (`methodology="_unknown"`) in the
        response (they used to be silently dropped — see audit BLOCKER S1 /
        Bug A). Wave 4.5 / SF5 reshaped the sentinel from
        `{"_unknown_buckets": [...]}` to `{methodology, buckets, details}`
        so the entry matches the other rows.
      * Agile bucketing requires `methodology_fields.column_id` to exist
        on the task — orphan rows from legacy data without a column_id
        no longer pollute the dashboard.

    Phase 1E.3c — primary collection filtered by tenant_id. project_id is
    tenant-unique so the $lookup join can't cross tenants, but the
    pre-match keeps the pipeline tight.
    """
    buckets_wf = {s: 0 for s in sorted(WATERFALL_STATUSES)}
    buckets_wt = {s: 0 for s in sorted(WAVES_THREADS_STATUSES)}
    buckets_agile: Dict[str, int] = {}
    unknown: List[Dict[str, Any]] = []

    pipeline = [
        {"$match": {"tenant_id": tenant_id}},
        {
            "$lookup": {
                "from": "projects",
                "localField": "project_id",
                "foreignField": "project_id",
                "as": "_project",
            }
        },
        {"$unwind": "$_project"},
        {
            "$group": {
                "_id": {
                    "methodology": "$_project.project_type",
                    "status": "$status",
                    # For agile, capture whether column_id is present so we
                    # can filter orphans without losing the count signal.
                    "has_column_id": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$ne": ["$_project.project_type", "agile"]},
                                    {"$literal": True},
                                ]
                            },
                            True,
                            {
                                "$and": [
                                    {"$ne": [{"$ifNull": ["$methodology_fields.column_id", None]}, None]},
                                    {"$ne": ["$methodology_fields.column_id", ""]},
                                ]
                            },
                        ]
                    },
                },
                "count": {"$sum": 1},
            }
        },
    ]
    async for row in db["pm_tasks"].aggregate(pipeline):
        gid = row.get("_id") or {}
        methodology = gid.get("methodology")
        status_ = gid.get("status")
        has_column_id = gid.get("has_column_id", True)
        count = row.get("count", 0)
        if methodology == "waterfall":
            if status_ in buckets_wf:
                buckets_wf[status_] += count
            else:
                logger.info(
                    "tasks_by_status: dropping %d waterfall task(s) with unknown status %r",
                    count, status_,
                )
                unknown.append(
                    {"methodology": "waterfall", "status": status_, "count": count}
                )
        elif methodology == "agile":
            if not has_column_id:
                logger.info(
                    "tasks_by_status: dropping %d agile task(s) with missing column_id (status=%r)",
                    count, status_,
                )
                unknown.append(
                    {
                        "methodology": "agile",
                        "status": status_,
                        "count": count,
                        "reason": "missing_column_id",
                    }
                )
                continue
            buckets_agile[status_] = buckets_agile.get(status_, 0) + count
        elif methodology == "waves_threads":
            if status_ in buckets_wt:
                buckets_wt[status_] += count
            else:
                logger.info(
                    "tasks_by_status: dropping %d waves_threads task(s) with unknown status %r",
                    count, status_,
                )
                unknown.append(
                    {"methodology": "waves_threads", "status": status_, "count": count}
                )

    # Return shape preserves backward-compat: top-level list with one entry
    # per methodology. The sentinel item appended for unknown statuses is
    # self-describing — `methodology="_unknown"` — so frontends iterating
    # the list can filter on `entry.methodology === "_unknown"` rather than
    # sniffing for a non-standard key.
    result: List[Dict[str, Any]] = [
        {"methodology": "waterfall", "buckets": buckets_wf},
        {"methodology": "agile", "buckets": buckets_agile},
        {"methodology": "waves_threads", "buckets": buckets_wt},
    ]
    if unknown:
        # Surface unknown statuses with both a flat status->count map (so the
        # shape matches the other entries) and the original detail array (so
        # debugging can recover methodology/reason for each dropped row).
        unknown_buckets: Dict[str, int] = {}
        for item in unknown:
            status_key = item.get("status") or "(none)"
            unknown_buckets[status_key] = unknown_buckets.get(status_key, 0) + int(item.get("count", 0))
        result.append({
            "methodology": "_unknown",
            "buckets": unknown_buckets,
            "details": unknown,
        })
    return result


async def _workload_by_assignee(db: Any, tenant_id: str) -> List[Dict[str, Any]]:
    """Open tasks per assignee (excluding done). Sorted desc by count.

    Phase 1E.3c — restricted to this tenant's tasks and users.
    """
    pipeline = [
        {
            "$match": {
                "tenant_id": tenant_id,
                "assignee_id": {"$ne": None},
                "status": {"$nin": list(DONE_STATUSES)},
            }
        },
        {"$group": {"_id": "$assignee_id", "open_task_count": {"$sum": 1}}},
        {
            "$lookup": {
                "from": "users",
                "localField": "_id",
                "foreignField": "user_id",
                "as": "_user",
            }
        },
        {"$unwind": {"path": "$_user", "preserveNullAndEmptyArrays": False}},
        {
            "$project": {
                "_id": 0,
                "user_id": "$_id",
                "display_name": {
                    "$ifNull": ["$_user.display_name", "$_user.name"]
                },
                "open_task_count": 1,
            }
        },
        {"$sort": {"open_task_count": -1, "display_name": 1}},
    ]
    return [row async for row in db["pm_tasks"].aggregate(pipeline)]


async def _upcoming_deadlines(db: Any, now: datetime, tenant_id: str) -> List[Dict[str, Any]]:
    """Top 5 tasks due in the next 14 days, excluding done.

    Phase 1E.3c — only this tenant's tasks.
    """
    today_iso = now.date().isoformat()
    cutoff_iso = (now + timedelta(days=14)).date().isoformat()

    pipeline = [
        {
            "$match": {
                "tenant_id": tenant_id,
                "due_date": {"$gte": today_iso, "$lte": cutoff_iso, "$ne": None},
                "status": {"$nin": list(DONE_STATUSES)},
            }
        },
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
                "assignee_id": "$assignee_id",
            }
        },
    ]
    return [row async for row in db["pm_tasks"].aggregate(pipeline)]


async def _recent_activity(db: Any, tenant_id: str) -> List[Dict[str, Any]]:
    """Last 10 status changes across tasks, projects, waves, and threads —
    unioned into one timeline so a PM's "what's happened recently" card
    catches every kind of state flip.

    Wave 4 / A5 hardening: previously this only joined `pm_task_status_history`,
    so project closures, wave shipments, and thread status flips were
    invisible. Now we run four parallel aggregations (one per history
    collection), normalize each row to a common shape, then merge-sort and
    truncate to 10. `changed_by` is always included.

    Phase 1E.3c — each of the four feeds is gated by a `$match: {tenant_id}`
    stage on the primary collection. Status-history collections are
    stamped by all upstream PATCH paths (see phases 1E.3c-projects-task,
    -projects-project, -projects-hierarchy).
    """
    task_pipeline = [
        {"$match": {"tenant_id": tenant_id}},
        {"$sort": {"changed_at": -1}},
        {"$limit": 10},
        {
            "$lookup": {
                "from": "pm_tasks",
                "localField": "task_id",
                "foreignField": "task_id",
                "as": "_task",
            }
        },
        {"$unwind": {"path": "$_task", "preserveNullAndEmptyArrays": True}},
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
                "kind": {"$literal": "task"},
                "entity_id": "$task_id",
                "title": "$_task.title",
                "project_id": "$project_id",
                "project_name": "$_project.name",
                "from_status": "$from_status",
                "to_status": "$to_status",
                "changed_at": "$changed_at",
                "changed_by": "$changed_by",
            }
        },
    ]

    project_pipeline = [
        {"$match": {"tenant_id": tenant_id}},
        {"$sort": {"changed_at": -1}},
        {"$limit": 10},
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
                "kind": {"$literal": "project"},
                "entity_id": "$project_id",
                "title": "$_project.name",
                "project_id": "$project_id",
                "project_name": "$_project.name",
                "from_status": "$from_status",
                "to_status": "$to_status",
                "changed_at": "$changed_at",
                "changed_by": "$changed_by",
            }
        },
    ]

    wave_pipeline = [
        {"$match": {"tenant_id": tenant_id}},
        {"$sort": {"changed_at": -1}},
        {"$limit": 10},
        {
            "$lookup": {
                "from": "pm_waves",
                "localField": "wave_id",
                "foreignField": "wave_id",
                "as": "_wave",
            }
        },
        {"$unwind": {"path": "$_wave", "preserveNullAndEmptyArrays": True}},
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
                "kind": {"$literal": "wave"},
                "entity_id": "$wave_id",
                "title": {
                    "$concat": [
                        "Wave ",
                        {"$toString": {"$ifNull": ["$_wave.wave_number", 0]}},
                    ]
                },
                "project_id": "$project_id",
                "project_name": "$_project.name",
                "from_status": "$from_status",
                "to_status": "$to_status",
                "changed_at": "$changed_at",
                "changed_by": "$changed_by",
            }
        },
    ]

    thread_pipeline = [
        {"$match": {"tenant_id": tenant_id}},
        {"$sort": {"changed_at": -1}},
        {"$limit": 10},
        {
            "$lookup": {
                "from": "pm_threads",
                "localField": "thread_id",
                "foreignField": "thread_id",
                "as": "_thread",
            }
        },
        {"$unwind": {"path": "$_thread", "preserveNullAndEmptyArrays": True}},
        {
            "$lookup": {
                "from": "pm_waves",
                "localField": "wave_id",
                "foreignField": "wave_id",
                "as": "_wave",
            }
        },
        {"$unwind": {"path": "$_wave", "preserveNullAndEmptyArrays": True}},
        {
            "$lookup": {
                "from": "projects",
                "localField": "_wave.project_id",
                "foreignField": "project_id",
                "as": "_project",
            }
        },
        {"$unwind": {"path": "$_project", "preserveNullAndEmptyArrays": True}},
        {
            "$project": {
                "_id": 0,
                "kind": {"$literal": "thread"},
                "entity_id": "$thread_id",
                "title": "$_thread.module",
                "project_id": "$_wave.project_id",
                "project_name": "$_project.name",
                "from_status": "$from_status",
                "to_status": "$to_status",
                "changed_at": "$changed_at",
                "changed_by": "$changed_by",
            }
        },
    ]

    # Run the four feeds in parallel.
    results = await asyncio.gather(
        db["pm_task_status_history"].aggregate(task_pipeline).to_list(10),
        db["pm_project_status_history"].aggregate(project_pipeline).to_list(10),
        db["pm_wave_status_history"].aggregate(wave_pipeline).to_list(10),
        db["pm_thread_status_history"].aggregate(thread_pipeline).to_list(10),
        return_exceptions=True,
    )

    rows: List[Dict[str, Any]] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"recent_activity feed errored (non-fatal): {r}")
            continue
        rows.extend(r)

    # changed_at can be a datetime or an ISO string depending on the feed.
    def _sort_key(row: Dict[str, Any]) -> str:
        ca = row.get("changed_at")
        if isinstance(ca, datetime):
            return ca.isoformat()
        return ca or ""

    rows.sort(key=_sort_key, reverse=True)
    return rows[:10]


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
@router.get(
    "/dashboard/summary",
    dependencies=[Depends(require_ai_permission("projects.dashboard.view"))],
)
async def dashboard_summary(
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.dashboard.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    now = datetime.now(timezone.utc)

    # Wave 4 / A5: 6 aggregations were running serially. Wrap in
    # asyncio.gather so the dashboard cold-load fans out instead of
    # waiting on one before starting the next. Each feed is independent
    # so we can run them concurrently.
    #
    # `return_exceptions=True` so a single broken card does not blank the
    # whole dashboard — we log the offender and substitute a safe empty
    # value. Inner gather in `_recent_activity` already follows this
    # pattern; applying it here is consistent.
    #
    # Phase 1E.3c — every feed receives the resolved tenant_id so the
    # dashboard payload describes only this tenant's portfolio. Root
    # admin operating under override sees the target tenant's dashboard.
    feeds = (
        ("project_health", _project_health(db, tenant_id), {"green": 0, "yellow": 0, "red": 0}),
        ("active_projects", _active_projects_count(db, now, tenant_id), {"count": 0, "delta_vs_last_week": 0}),
        ("tasks_by_status", _tasks_by_status(db, tenant_id), []),
        ("workload_by_assignee", _workload_by_assignee(db, tenant_id), []),
        ("upcoming_deadlines", _upcoming_deadlines(db, now, tenant_id), []),
        ("recent_activity", _recent_activity(db, tenant_id), []),
    )
    results = await asyncio.gather(
        *(coro for _, coro, _ in feeds),
        return_exceptions=True,
    )
    payload: Dict[str, Any] = {}
    for (name, _coro, fallback), result in zip(feeds, results):
        if isinstance(result, Exception):
            logger.exception("dashboard_summary: feed %r failed; returning fallback", name, exc_info=result)
            payload[name] = fallback
        else:
            payload[name] = result
    return payload
