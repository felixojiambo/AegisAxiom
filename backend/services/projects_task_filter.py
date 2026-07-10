"""Smart-view filter query builder for `GET /api/projects/tasks`.

Pure function that translates structured filter query params (the ones the
frontend's smart-view pills push to the server when a project exceeds the
500-task client-side cap) into a Mongo `$match`-style filter dict.

Design notes:
  * No DB calls. Inject `now` for deterministic tests.
  * All filters combine with AND. Caller is responsible for AND'ing the
    returned dict with any RBAC-scoping filter (project access, tenant, etc.)
    before passing to `find()`.
  * `__me` / `__unassigned` are sentinel values used by the frontend so it
    never has to know the caller's user_id; the server resolves them here.
  * `terminal_states` (below) is sourced from frontend
    `frontend/src/components/projects/smartViews/predicates.js` →
    `DONE_STATUSES`. That set is the source of truth because the smart-view
    pills (Overdue, At risk, Due this week) compare against it client-side
    today; mirroring it server-side keeps client + server filtering
    semantically identical. The narrower backend `services.projects_methodology.
    DONE_STATUSES` ({"complete","shipped"}) covers only waterfall + W&T and is
    intentionally NOT used here — agile column-name done states ("done",
    "completed", "closed") would leak through otherwise.

Index recommendations (do NOT create from this code path — document only):
  * `pm_tasks` benefits from a compound index on
      (project_id, assignee_id, due_date, status, priority).
    `project_id` is always present (RBAC scope), the rest are the high-cardinality
    smart-view filter columns. For text search the `$regex` path won't use an
    index — if search becomes hot, add a Mongo text index on (title, description)
    and switch `search` to `$text`.
  * `(project_id, updated_at)` and `(project_id, created_at)` support the
    `updated_after` / `created_after` filters used by polling clients.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


# Sentinel values the frontend passes for assignee_id.
ME_SENTINEL = "__me"
UNASSIGNED_SENTINEL = "__unassigned"

# Terminal / done statuses — mirrors frontend predicates.js DONE_STATUSES.
# See module docstring for why this is the union set, not the backend
# methodology DONE_STATUSES.
TERMINAL_STATES: frozenset[str] = frozenset(
    {
        "complete",
        "completed",
        "done",
        "shipped",
        "closed",
        "cancelled",
        "canceled",
    }
)


def _today_utc_midnight(now: Optional[datetime] = None) -> datetime:
    """Return today's date at 00:00:00 UTC. Mirrors frontend
    `todayMidnightUTC` in predicates.js so the date-only comparisons match
    on both sides of the wire."""
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return datetime(base.year, base.month, base.day, tzinfo=timezone.utc)


def _iso_date_only(dt: datetime) -> str:
    """`due_date` is stored as a `YYYY-MM-DD` string in `pm_tasks` (see the
    task DTOs in routes/projects_task.py). Compare against strings, not
    datetimes, so Mongo's lexical string comparison matches our intent."""
    return dt.strftime("%Y-%m-%d")


def _iso_full(dt: datetime) -> str:
    """`created_at` / `updated_at` are stored as ISO 8601 strings with
    timezone offset (see _utc_now_iso in routes/projects_task.py)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Match the `+00:00` shape used by _utc_now_iso, not `Z`.
    return dt.isoformat()


def build_filter_query(
    project_id: str,
    user_id: str,
    *,
    assignee_id: Optional[str] = None,
    status_in: Optional[List[str]] = None,
    priority_in: Optional[List[str]] = None,
    is_blocked: Optional[bool] = None,
    due_within_days: Optional[int] = None,
    is_overdue: Optional[bool] = None,
    is_at_risk: Optional[bool] = None,
    created_after: Optional[datetime] = None,
    updated_after: Optional[datetime] = None,
    search: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Translate smart-view filter params into a Mongo filter dict.

    Pure function — no DB, no IO. Caller AND's this with any RBAC filter.

    Sentinels:
      * `assignee_id == "__me"` -> resolves to `user_id`
      * `assignee_id == "__unassigned"` -> matches `assignee_id == None`

    `terminal_states` exclusion applies to `due_within_days`, `is_overdue`,
    and `is_at_risk` — matches client-side predicate behavior.
    """
    query: Dict[str, Any] = {"project_id": project_id}
    # `status` may be constrained by multiple inputs (status_in, is_blocked,
    # is_overdue/is_at_risk/due_within_days excluding terminals). Collect
    # constraints and merge into a single `$and` block at the end so a later
    # constraint doesn't silently overwrite an earlier one.
    status_constraints: List[Dict[str, Any]] = []
    due_constraints: List[Dict[str, Any]] = []

    # ---- assignee_id ----
    if assignee_id is not None:
        if assignee_id == ME_SENTINEL:
            query["assignee_id"] = user_id
        elif assignee_id == UNASSIGNED_SENTINEL:
            query["assignee_id"] = None
        else:
            query["assignee_id"] = assignee_id

    # ---- status_in ----
    if status_in:
        status_constraints.append({"status": {"$in": list(status_in)}})

    # ---- priority_in ----
    if priority_in:
        query["priority"] = {"$in": list(priority_in)}

    # ---- is_blocked ----
    # Methodology-aware blocked-status detection is hard to do without the
    # project doc in hand. We match the documented endpoint contract:
    # status (case-sensitive lowercase) == "blocked". Agile boards that use
    # a column-id-as-status will not match here — those callers should pass
    # `status_in=<blocked_column_id>` instead. Documented in v1.3 plan §C.
    if is_blocked is True:
        status_constraints.append({"status": "blocked"})
    elif is_blocked is False:
        status_constraints.append({"status": {"$ne": "blocked"}})

    # ---- terminal-status exclusion (applies to due-based filters) ----
    exclude_terminals = (
        due_within_days is not None or is_overdue is True or is_at_risk is True
    )

    today = _today_utc_midnight(now)

    # ---- due_within_days ----
    if due_within_days is not None:
        ceiling = today + timedelta(days=int(due_within_days))
        due_constraints.append(
            {
                "due_date": {
                    "$gte": _iso_date_only(today),
                    "$lte": _iso_date_only(ceiling),
                }
            }
        )

    # ---- is_overdue ----
    if is_overdue is True:
        due_constraints.append({"due_date": {"$lt": _iso_date_only(today)}})

    # ---- is_at_risk ----
    # Frontend predicate: due_date - today in [0, 3) days. Mirror exactly.
    if is_at_risk is True:
        ceiling = today + timedelta(days=3)
        due_constraints.append(
            {
                "due_date": {
                    "$gte": _iso_date_only(today),
                    "$lt": _iso_date_only(ceiling),
                }
            }
        )

    if exclude_terminals:
        status_constraints.append(
            {"status": {"$nin": sorted(TERMINAL_STATES)}}
        )

    # ---- created_after / updated_after ----
    if created_after is not None:
        query["created_at"] = {"$gt": _iso_full(created_after)}
    if updated_after is not None:
        query["updated_at"] = {"$gt": _iso_full(updated_after)}

    # ---- search ----
    # Case-insensitive regex across title + description. Escape user input so
    # `.` / `*` etc. are literal — preserves intent + avoids regex DOS via
    # pathological patterns from the API surface.
    if search:
        import re as _re

        pattern = _re.escape(search)
        query["$or"] = [
            {"title": {"$regex": pattern, "$options": "i"}},
            {"description": {"$regex": pattern, "$options": "i"}},
        ]

    # ---- merge collected constraints ----
    # If only one status constraint exists, fold it into `query["status"]`
    # for a cleaner explain plan. Otherwise use `$and` to avoid clobbering.
    if len(status_constraints) == 1:
        query.update(status_constraints[0])
    elif len(status_constraints) > 1:
        query.setdefault("$and", []).extend(status_constraints)

    if len(due_constraints) == 1:
        query.update(due_constraints[0])
    elif len(due_constraints) > 1:
        query.setdefault("$and", []).extend(due_constraints)

    return query


def parse_comma_list(raw: Optional[str]) -> Optional[List[str]]:
    """Parse `?status_in=a,b,c` into ['a','b','c']. Returns None for None or
    empty so callers can distinguish "not provided" from "empty list."""
    if raw is None:
        return None
    items = [s.strip() for s in raw.split(",") if s.strip()]
    return items or None
