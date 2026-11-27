"""Projects — Recent Changes API (v1.2 Wave 1 / Thread 1B).

Backs the PMO AI's "what changed in the last 24 hours?" capability.

Audit source decision (read carefully — this is load-bearing):

    The existing infrastructure already writes a unified row to
    `db.audit_logs` on every project / task / sprint / wave / thread /
    blocker / comment mutation, courtesy of `services.audit_service.
    log_ingested_crud` called by `services.ingestion.route_helpers.
    crud_envelope`. Every PATCH / POST / DELETE in projects_task.py,
    projects_project.py, etc. is already wrapped in that envelope. So
    the audit collection is already populated with:

        {
            audit_id, timestamp, action ("created" / "updated" /
            "deleted" / "status_changed" / "assigned" / ...),
            resource_type ("projects.task", "projects.project", ...),
            resource_id, actor_id, actor_email, event_category,
            details: { module, entity, action, request_id, diff|after|before }
        }

    For PMO AI write tools the same envelope is reused — they stamp
    `details.actor_type = "pmo_ai"` and `details.tool_name = "<tool>"` on
    the activity row via the `ai_actor_ctx` contextvar set in
    `execute_tool` (v1.3 Theme B). One row per logical write; the v1.2
    dual-write pattern was retired. Failed AI calls still write a
    standalone `event_category="ai_action"` row with
    `result_status="error"` so the failure trail is preserved — those
    rows are surfaced here via the same `$or` clause.

Audit log shape agreed with Thread 1A:

    Inside `details` (no top-level schema change):
        actor_type: "user" | "pmo_ai"     (default "user" when absent)
        tool_name:  str | None             (only set for pmo_ai writes)

    Top-level row already has actor_id, actor_email, resource_type,
    resource_id, action, timestamp.

RBAC: results are scoped by `build_pmo_filters` — only audit rows for
`resource_type`s the user has projects.<entity>.view perms on are
returned, and within those, project-membership scoping is applied via
the existing `project_filter` / `task_filter` helpers (binary today —
the user has the perm or they don't, and the filter is `{}` or deny).

Performance: a single `audit_logs.find` against the existing
`(timestamp -1)` index, plus optional `(resource_type, resource_id,
timestamp)` index hit when filtering by project. The handler issues at
most 3 follow-up Mongo lookups for resolving actor names + project
names + task numbers — batched via `$in`, never per-row. Budget for a
24h window on a normal project is well under 500ms.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth_helper import get_current_user, has_perm
from database import get_db
from db.session import get_sessionmaker
from repositories.audit import AuditRepository
from repositories.users import UserRepository
from middleware.tenant_scoping import get_current_tenant_id


logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_HOURS = 168          # 1 week cap on time window
MAX_LIMIT = 500          # absolute upper bound on returned rows
DEFAULT_HOURS = 24
DEFAULT_LIMIT = 100

# resource_type values written by projects_* routes (via crud_envelope).
# The module prefix is "projects" so anything starting with that string is
# in-scope. We keep an explicit list rather than a `$regex` so the Mongo
# (resource_type, resource_id, timestamp) compound index is usable.
_PROJECT_RESOURCE_TYPES = [
    "projects.project",
    "projects.task",
    "projects.sprint",
    "projects.wave",
    "projects.thread",
    "projects.blocker_ticket",
    "projects.standup",
    "projects.task_comment",
]

# entity_type query param → resource_type prefix mapping
_ENTITY_TYPE_MAP = {
    "project": ["projects.project"],
    "task": ["projects.task"],
    "sprint": ["projects.sprint"],
    "wave": ["projects.wave"],
    "thread": ["projects.thread"],
    "blocker": ["projects.blocker_ticket"],
    "standup": ["projects.standup"],
    "comment": ["projects.task_comment"],
}

# Required permission set — user needs ONE of these to hit the endpoint at all.
# Returned rows are then further filtered by `build_pmo_filters` so a user with
# only `projects.task.view` won't see project / wave / standup changes.
_VIEW_PERMS = [
    "projects.project.view",
    "projects.task.view",
    "projects.sprint.view",
    "projects.wave.view",
    "projects.thread.view",
    "projects.blocker_ticket.view",
    "projects.standup.view",
]


def _has_any_view_perm(user) -> bool:
    return any(has_perm(user, p) for p in _VIEW_PERMS)


# ---------------------------------------------------------------------------
# Time-formatting helper (used by the summary line)
# ---------------------------------------------------------------------------
def _fmt_relative(at: datetime, now: datetime) -> str:
    """Render a delta as 'just now' / '<N>m ago' / '<N>h ago' / '<N>d ago'."""
    if at is None:
        return "recently"
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    delta = now - at
    secs = delta.total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


# ---------------------------------------------------------------------------
# Summary formatter — shared with PmoAgent.fetch_data so the LLM context and
# the API response use identical strings.
# ---------------------------------------------------------------------------
def format_change_summary(
    row: Dict[str, Any],
    *,
    actor_name: Optional[str],
    project_name: Optional[str],
    entity_label: Optional[str],
    now: Optional[datetime] = None,
) -> str:
    """Build a one-line natural-language summary for a change row.

    Format examples:
        "@grace changed status of task #42 from 'in_progress' to 'blocked' 2h ago"
        "@henry created project 'Henderson Q3' 45m ago"
        "@aegisaxiom-ai (on behalf of @grace) assigned task #87 to @marcus 12m ago"

    `row` is the normalized change dict produced by `_normalize_audit_row`.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    when = _fmt_relative(row.get("at"), now)
    actor_display = actor_name or "someone"
    actor_handle = f"@{actor_display}"

    # AI on-behalf-of prefix
    if row.get("actor_type") == "pmo_ai":
        actor_handle = f"@aegisaxiom-ai (on behalf of @{actor_display})"

    entity_type = row.get("entity_type") or "record"
    label = entity_label or row.get("entity_id") or ""
    if entity_type == "task" and label and not label.startswith("#"):
        label = f"#{label}"
    elif entity_type == "project" and label and not label.startswith("'"):
        label = f"'{label}'"

    action = row.get("action")
    field = row.get("field")
    old_v = row.get("old_value")
    new_v = row.get("new_value")

    project_suffix = ""
    if project_name and entity_type != "project":
        project_suffix = f" in '{project_name}'"

    if action == "status_changed" or (action == "updated" and field == "status"):
        return (
            f"{actor_handle} changed status of {entity_type} {label} "
            f"from '{old_v}' to '{new_v}'{project_suffix} {when}"
        )
    if action == "assigned" or (action == "updated" and field == "assignee_id"):
        new_label = new_v if new_v else "(unassigned)"
        return (
            f"{actor_handle} assigned {entity_type} {label} to "
            f"@{new_label}{project_suffix} {when}"
        )
    if action == "created":
        return f"{actor_handle} created {entity_type} {label}{project_suffix} {when}"
    if action == "deleted":
        return f"{actor_handle} deleted {entity_type} {label}{project_suffix} {when}"
    if action == "commented":
        return f"{actor_handle} commented on {entity_type} {label}{project_suffix} {when}"
    if field:
        return (
            f"{actor_handle} updated {field} on {entity_type} {label} "
            f"from '{old_v}' to '{new_v}'{project_suffix} {when}"
        )
    return f"{actor_handle} updated {entity_type} {label}{project_suffix} {when}"


# ---------------------------------------------------------------------------
# Audit-row normalization
# ---------------------------------------------------------------------------
# Fields we consider "interesting" enough to surface as individual updates.
# Other diff fields are folded into a generic "updated" summary.
_INTERESTING_FIELDS = {
    "status", "priority", "assignee_id", "health",
    "due_date", "target_end_date", "pm_id",
}


def _extract_field_change(details: Dict[str, Any]) -> Tuple[Optional[str], Any, Any]:
    """For an updated/status_changed row, pull out the most interesting
    (field, old, new) triple from `details.diff`.

    Strategy: if any of `_INTERESTING_FIELDS` is in the diff, prefer the
    first one (status > priority > assignee_id > health > dates > pm_id).
    Otherwise pick the first changed field. Returns (None, None, None)
    when there's no usable diff.
    """
    diff = (details or {}).get("diff") or {}
    if not isinstance(diff, dict) or not diff:
        return None, None, None

    # Priority-ordered scan.
    order = ["status", "priority", "assignee_id", "health",
             "due_date", "target_end_date", "pm_id"]
    for f in order:
        if f in diff:
            change = diff[f] or {}
            return f, change.get("before"), change.get("after")

    # Fall back to first key (deterministic via sorted).
    for f in sorted(diff.keys()):
        change = diff[f] or {}
        return f, change.get("before"), change.get("after")

    return None, None, None


def _resource_type_to_entity_type(resource_type: str) -> str:
    """`projects.task` → `task`, `projects.task_comment` → `comment`, etc."""
    if resource_type == "projects.task_comment":
        return "comment"
    if resource_type.startswith("projects."):
        return resource_type.split(".", 1)[1].replace("_ticket", "")
    return resource_type


def _normalize_audit_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Audit row → public change-row shape.

    Public shape (returned to the API caller):
        {
            type: "change",
            entity_id, entity_type, project_id,
            actor_user_id, actor_type, action, field,
            old_value, new_value, at,
            summary (filled in later, after lookups)
        }
    """
    details = row.get("details") or {}
    action = row.get("action") or "updated"
    resource_type = row.get("resource_type") or ""
    entity_type = _resource_type_to_entity_type(resource_type)

    field: Optional[str] = None
    old_v: Any = None
    new_v: Any = None
    if action in {"updated", "status_changed", "assigned"}:
        field, old_v, new_v = _extract_field_change(details)
        # status_changed / assigned can carry their canonical fields even
        # when the diff was sparse — synthesize them from before/after if
        # the helper came back empty.
        if action == "status_changed" and field is None:
            field = "status"
        elif action == "assigned" and field is None:
            field = "assignee_id"

    # project_id resolution. For task / sprint / wave / thread changes,
    # the audit `after` (or `before` on delete) doc carries `project_id`.
    project_id: Optional[str] = None
    after = details.get("after") or {}
    before = details.get("before") or {}
    if entity_type == "project":
        project_id = row.get("resource_id")
    else:
        project_id = (
            (after.get("project_id") if isinstance(after, dict) else None)
            or (before.get("project_id") if isinstance(before, dict) else None)
        )

    # actor_type / tool_name: prefer details (Thread 1A contract / crud
    # path), then fall back to top-level (legacy pmo_ai audit rows).
    actor_type = details.get("actor_type") or row.get("actor_type") or "user"
    tool_name = details.get("tool_name") or row.get("tool_name")

    # actor identity: canonical `actor_id` (crud_envelope) wins, then
    # `actor_user_id` (AI helper writes both, older payloads may only have
    # the latter).
    actor_user_id = row.get("actor_id") or row.get("actor_user_id")

    at = row.get("timestamp")

    return {
        "type": "change",
        "entity_id": row.get("resource_id"),
        "entity_type": entity_type,
        "project_id": project_id,
        "actor_user_id": actor_user_id,
        "actor_type": actor_type,
        "tool_name": tool_name,
        "action": action,
        "field": field,
        "old_value": old_v,
        "new_value": new_v,
        "at": at,
        "summary": "",  # filled in after batch lookups
        # internal — used for dedup; stripped before return.
        "_event_category": row.get("event_category") or "",
    }


_SHADOW_FIELD_PAIRS = {
    # When both an "updated" row and one of these specific actions exist for
    # the same (entity, actor, field) within the window, drop the "updated".
    "status": "status_changed",
    "assignee_id": "assigned",
}
_SHADOW_WINDOW_SECONDS = 5


def _collapse_shadow_updates(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop noisy `action="updated"` rows that are shadowed by a more
    specific `status_changed` / `assigned` row written within seconds for
    the same entity + field + actor. This dedupes the audit-feed surface
    without losing data from the underlying audit_logs collection.
    """
    if not rows:
        return rows
    # Build an index of specific actions keyed by (entity_id, actor, field).
    # Use a set of bucketed timestamps so we tolerate the few-millisecond
    # gap between the two crud_envelope writes.
    def _ts_bucket(ts: Any) -> Optional[int]:
        if ts is None:
            return None
        try:
            return int(ts.timestamp() // _SHADOW_WINDOW_SECONDS)
        except Exception:
            return None

    specific_keys = set()
    for r in rows:
        action = r.get("action")
        field = r.get("field")
        shadowed_by = _SHADOW_FIELD_PAIRS.get(field or "")
        if action == shadowed_by:
            bucket = _ts_bucket(r.get("at"))
            if bucket is not None:
                specific_keys.add((r.get("entity_id"), r.get("actor_user_id"), field, bucket))

    out: List[Dict[str, Any]] = []
    for r in rows:
        if r.get("action") == "updated":
            field = r.get("field")
            if field in _SHADOW_FIELD_PAIRS:
                bucket = _ts_bucket(r.get("at"))
                if bucket is not None and (r.get("entity_id"), r.get("actor_user_id"), field, bucket) in specific_keys:
                    continue
        out.append(r)
    return out


def _strip_internal_dedup_marker(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strip the internal `_event_category` field from every row.

    v1.3 Theme B retired the dual-write pattern (see header). The activity
    row written by `crud_envelope` is now the single source of truth — it
    carries `actor_type` + `tool_name` directly when an AI tool is the
    caller (stamped via the `ai_actor_ctx` contextvar in audit_service).
    Failed AI calls still write an explicit `event_category="ai_action"`
    row (see `_audit_pmo_ai_action` in conversational_tools.py), so we
    keep the `$or` clause that includes both categories — but there are
    no longer two rows per successful write to collapse.

    This helper preserves the historic shape (`_event_category` stripped
    before returning rows to callers) without the v1.2 dedup logic.
    """
    if not rows:
        return rows
    for r in rows:
        r.pop("_event_category", None)
    return rows


# ---------------------------------------------------------------------------
# Core fetch — shared with PmoAgent.fetch_data
# ---------------------------------------------------------------------------
async def fetch_recent_changes(
    db,
    user,
    *,
    tenant_id: Optional[str] = None,
    hours: int = DEFAULT_HOURS,
    project_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Return RBAC-filtered list of recent change rows, newest first.

    Used by:
      - GET /api/projects/recent-changes  (the public API)
      - PmoAgent.fetch_data               (LLM context)

    Caller is responsible for permission gating. This function trusts that
    the user has at least one `projects.*.view` perm; it then filters
    individual rows by the per-entity perm so a user with only
    `projects.task.view` won't see project status changes etc.

    Phase 1E.3c — tenant_id-scoped. audit_service.log_ingested_crud now
    stamps tenant_id at the top level of every audit row (1E.3c update),
    so we filter on it directly. Historical rows written pre-1E.3c lack
    the top-level field — we fall back to `details.after.tenant_id` /
    `details.before.tenant_id` which were carried by the entity docs once
    1E.3c phases shipped to dev. Rows that lack tenant_id in BOTH places
    are excluded from tenant-scoped queries (pre-multitenant history).

    Callers should pass `tenant_id` explicitly — for route handlers, use
    the `get_current_tenant_id` FastAPI dep so override context is
    honored. When `tenant_id` is None, falls back to `user.tenant_id`,
    which is correct for AI-agent callers that operate in the user's
    home tenant rather than any override. If the user has no tenant_id
    (legacy / system actor), the function returns an empty list rather
    than leaking cross-tenant audit data.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    hours = max(1, min(int(hours or DEFAULT_HOURS), MAX_HOURS))
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))

    # Resolve effective tenant scope. Route handlers should always pass
    # tenant_id explicitly; agent callers fall back to user.tenant_id.
    effective_tenant_id = tenant_id or getattr(user, "tenant_id", None)
    if not effective_tenant_id:
        # No tenant context available — refuse rather than leak across
        # tenants. AI agents running as a system user without a tenant
        # would land here; they need to be updated to pass tenant_id.
        return []

    # Per-entity perm gating. Only include resource_types the user can see.
    allowed_resource_types: List[str] = []
    for rt in _PROJECT_RESOURCE_TYPES:
        et = _resource_type_to_entity_type(rt)
        # task_comment piggybacks on task perms — comments don't have their
        # own view perm in the recent-changes context.
        if et == "comment":
            if has_perm(user, "projects.task.view"):
                allowed_resource_types.append(rt)
            continue
        perm_key = (
            "projects.blocker_ticket.view"
            if rt == "projects.blocker_ticket"
            else f"projects.{et}.view"
        )
        if has_perm(user, perm_key):
            allowed_resource_types.append(rt)

    if not allowed_resource_types:
        return []

    # Optional entity_type filter (further narrows the resource_type set).
    if entity_type:
        wanted = _ENTITY_TYPE_MAP.get(entity_type.lower())
        if not wanted:
            return []
        allowed_resource_types = [r for r in allowed_resource_types if r in wanted]
        if not allowed_resource_types:
            return []

    since = now - timedelta(hours=hours)

    # Phase 2b: audit_logs is Postgres (AuditRepository.list_recent_activity).
    # Same predicate the Mongo filter expressed:
    #   * resource_type IN the allowed set (served by
    #     (resource_type, timestamp DESC));
    #   * since <= timestamp <= now;
    #   * tenant: top-level tenant_id (1E.3c+) OR details.after.tenant_id OR
    #     details.before.tenant_id — rows matching none are pre-multitenant
    #     history and stay excluded from tenant-scoped views;
    #   * human writes (event_category "activity" or NULL) AND AI writes
    #     ("ai_action");
    #   * optional actor: actor_id OR actor_user_id (the AI helper writes both);
    #   * optional project: the project row itself, or
    #     details.after/before.project_id.
    # Over-fetch slightly because some rows may be noise (no usable diff field)
    # and we want to honor `limit` post-filter.
    async with get_sessionmaker()() as audit_pg:
        records = await AuditRepository(audit_pg).list_recent_activity(
            resource_types=allowed_resource_types,
            since=since,
            until=now,
            tenant_id=effective_tenant_id,
            actor_user_id=actor_user_id or None,
            project_id=project_id or None,
            limit=limit * 2,
        )
    audit_rows = [r.to_api_dict() for r in records]

    # Normalize → public shape.
    rows = [_normalize_audit_row(r) for r in audit_rows]
    rows = [r for r in rows if r["entity_id"]]

    # v1.3 Theme B: dual-write retired. The activity row from crud_envelope
    # now carries actor_type + tool_name directly when an AI tool is the
    # caller (stamped via ai_actor_ctx in audit_service). Successful AI
    # writes produce exactly one row. Failed AI calls still write an
    # event_category="ai_action" error row — surfaced via the $or in the
    # Mongo filter above. No collapse step required.
    rows = _strip_internal_dedup_marker(rows)

    # v1.3 Round 5 — drop "updated" rows that shadow a "status_changed" /
    # "assigned" row on the same entity within a short window. Single-task
    # PATCH + bulk-status both emit two envelopes per change (action=updated
    # + action=status_changed) — both legitimate audit rows, but they look
    # like duplicates in the recent-changes feed because they describe the
    # same field change. Keep the more specific row.
    rows = _collapse_shadow_updates(rows)
    rows = rows[:limit]

    # Batch-resolve names for the summary line. We never do per-row lookups.
    actor_ids = {r["actor_user_id"] for r in rows if r.get("actor_user_id")}
    # assignee renderings ("to @marcus") may reference user_ids too
    actor_ids.update(
        r["new_value"] for r in rows
        if r.get("field") == "assignee_id" and isinstance(r.get("new_value"), str)
    )

    project_ids = {r["project_id"] for r in rows if r.get("project_id")}
    task_ids = [
        r["entity_id"] for r in rows
        if r.get("entity_type") == "task" and r.get("entity_id")
    ]

    user_names: Dict[str, str] = {}
    if actor_ids:
        # Phase 1E.3c — tenant-scoped user name lookup. A cross-tenant
        # actor whose audit row somehow leaked through would render with
        # the raw user_id rather than a name.
        sm = get_sessionmaker()
        async with sm() as pg:
            matched_users = await UserRepository(pg).list_users(
                user_ids=list(actor_ids),
                tenant_id=effective_tenant_id,
            )
        for u in matched_users:
            user_names[u.user_id] = u.name or u.email or u.user_id

    project_names: Dict[str, str] = {}
    if project_ids:
        async for p in db.projects.find(
            {"project_id": {"$in": list(project_ids)}, "tenant_id": effective_tenant_id},
            {"_id": 0, "project_id": 1, "name": 1},
        ):
            project_names[p["project_id"]] = p.get("name") or p["project_id"]

    task_labels: Dict[str, str] = {}
    if task_ids:
        async for t in db.pm_tasks.find(
            {"task_id": {"$in": task_ids}, "tenant_id": effective_tenant_id},
            {"_id": 0, "task_id": 1, "task_number": 1, "title": 1},
        ):
            num = t.get("task_number")
            task_labels[t["task_id"]] = str(num) if num is not None else (t.get("title") or t["task_id"])

    # Render summary lines.
    for r in rows:
        actor_name = user_names.get(r.get("actor_user_id") or "")
        project_name = project_names.get(r.get("project_id") or "")
        entity_label: Optional[str] = None
        if r.get("entity_type") == "task":
            entity_label = task_labels.get(r.get("entity_id") or "") or r.get("entity_id")
        elif r.get("entity_type") == "project":
            entity_label = project_names.get(r.get("entity_id") or "") or r.get("entity_id")
        else:
            entity_label = r.get("entity_id")

        # Resolve assignee name on the new_value side for assigned actions.
        if r.get("field") == "assignee_id" and isinstance(r.get("new_value"), str):
            r["new_value"] = user_names.get(r["new_value"], r["new_value"])
        if r.get("field") == "assignee_id" and isinstance(r.get("old_value"), str):
            r["old_value"] = user_names.get(r["old_value"], r["old_value"])

        r["summary"] = format_change_summary(
            r,
            actor_name=actor_name,
            project_name=project_name,
            entity_label=entity_label,
            now=now,
        )

    return rows


def aggregate_change_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """Compact tallies for the LLM context — "5 status changes today, ..."."""
    counts = {
        "total": len(rows),
        "status_changes": 0,
        "tasks_created": 0,
        "projects_created": 0,
        "assignments": 0,
        "comments": 0,
        "deletions": 0,
        "by_pmo_ai": 0,
    }
    for r in rows:
        action = r.get("action")
        field = r.get("field")
        et = r.get("entity_type")
        if action == "status_changed" or (action == "updated" and field == "status"):
            counts["status_changes"] += 1
        if action == "assigned" or (action == "updated" and field == "assignee_id"):
            counts["assignments"] += 1
        if action == "created" and et == "task":
            counts["tasks_created"] += 1
        if action == "created" and et == "project":
            counts["projects_created"] += 1
        # `et` is the normalized entity_type from _normalize_audit_row,
        # which collapses "task_comment" → "comment". Accept both for
        # callers that hand-construct rows.
        if action == "commented" or et in {"comment", "task_comment"}:
            counts["comments"] += 1
        if action == "deleted":
            counts["deletions"] += 1
        if r.get("actor_type") == "pmo_ai":
            counts["by_pmo_ai"] += 1
    return counts


# ---------------------------------------------------------------------------
# Public route
# ---------------------------------------------------------------------------
@router.get("/recent-changes")
async def get_recent_changes(
    request: Request,
    hours: int = Query(DEFAULT_HOURS, ge=1, le=MAX_HOURS),
    project_id: Optional[str] = Query(None),
    actor_user_id: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(
        None, description="Filter to one entity type: project, task, sprint, wave, thread, blocker, standup, comment",
    ),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """List recent project/task/etc changes for the PMO AI + dashboards.

    Returns rows newest-first. Each row carries a `summary` field — a
    one-line natural-language description the LLM can use directly.

    RBAC: caller must hold at least one `projects.*.view` permission. Each
    returned row is further filtered to the per-entity perm so a user with
    only `projects.task.view` won't see project / sprint / wave changes.

    Phase 1E.3c — feed restricted to the caller's tenant context. Root
    admin under override sees the target tenant's recent changes.
    """
    if not _has_any_view_perm(current_user):
        raise HTTPException(
            status_code=403, detail="Missing projects.*.view permission"
        )

    db = get_db()

    rows = await fetch_recent_changes(
        db,
        current_user,
        tenant_id=tenant_id,
        hours=hours,
        project_id=project_id,
        actor_user_id=actor_user_id,
        entity_type=entity_type,
        limit=limit,
    )

    return {
        "items": rows,
        "total": len(rows),
        "hours": hours,
        "limit": limit,
        "counts": aggregate_change_counts(rows),
    }
