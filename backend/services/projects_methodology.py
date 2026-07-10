"""Methodology-specific validation for Projects (waterfall / agile / waves+threads).

Pure validation logic — no DB access. Project + task route handlers call into
this module to validate `methodology_config` (project-level) and
`methodology_fields` (task-level) before persisting.

Validation errors raise FastAPI `HTTPException(status_code=422)` so the HTTP
contract is consistent with the rest of the AEGISAXIOM API.

Shape rules for `methodology_config` (project-level):
  - waterfall       -> { phases: [{name: str, start_date?: ISO, end_date?: ISO}, ...] }
                       (>=1, unique names; bare strings accepted on input and
                       normalized to dicts {name})
  - agile           -> { columns: [{id, name, color?, is_done?}, ...] }
                       (>=1, unique ids; at most one is_done — last wins)
  - waves_threads   -> { wave_count_target: int>=1, modules: [str, ...] }
                       (waves themselves are a separate resource)

Shape rules for `methodology_fields` (task-level):
  - waterfall       -> { phase (must be in project.phases), milestone: bool=False,
                         milestone_name?: str (required when milestone=true),
                         milestone_target_date?: ISO (required when milestone=true),
                         milestone_actual_date?: ISO (set on completion),
                         milestone_at_risk: bool (computed on read; not stored) }
                       status in WATERFALL_STATUSES, default "not_started"
  - agile           -> { column_id (must exist in project.columns),
                         story_points?: number, sprint_id?: str }
                       status DERIVED from column.name on write
  - waves_threads   -> { wave_id: str, thread_id: str, module_owned: str,
                         bucket?: str, scope_drift?: bool=False }
                       status in WAVES_THREADS_STATUSES, default "scoped"

ID convention: AEGISAXIOM uses prefixed UUID strings (e.g. ``sprint_<hex>``,
``wave_<hex>``, ``thread_<hex>``) — see `id_strategy: Davies ObjectId ->
AEGISAXIOM gen_id UUID strings` in `.ingestion/mappings/projects.json`. We accept
any non-empty string for FK fields here; cross-collection existence is
enforced by the route handler with a DB lookup, not in this module.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Status enums per methodology.
# Mirrors `task.fields.status.enum_per_methodology` in the mapping file.
# Inlined here so this module has no cross-thread import dependency on the
# Wave 1A task model. If Wave 1A later exports these as constants we should
# re-import to keep them DRY.
# ---------------------------------------------------------------------------
WATERFALL_STATUSES: frozenset[str] = frozenset(
    {"not_started", "in_progress", "blocked", "complete"}
)
WAVES_THREADS_STATUSES: frozenset[str] = frozenset(
    {"scoped", "in_progress", "blocked", "shipped", "carried_forward"}
)
# "Done" buckets for dashboard / progress aggregations across methodologies.
# Agile has no fixed done set (column-name driven); these cover waterfall + W+T.
DONE_STATUSES: frozenset[str] = frozenset({"complete", "shipped"})
PROJECT_TYPES: frozenset[str] = frozenset({"waterfall", "agile", "waves_threads"})


def _unprocessable(detail: str) -> HTTPException:
    """Construct a 422 with a structured detail payload.

    Wrapping in a tagged dict (vs. a bare string) lets the frontend show
    field-level errors without parsing prose.
    """
    return HTTPException(
        status_code=422,
        detail={"error": "methodology_validation_failed", "message": detail},
    )


def _require_non_empty_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _unprocessable(f"{field} must be a non-empty string")
    return value.strip()


# ---------------------------------------------------------------------------
# Project-level: validate `methodology_config`
# ---------------------------------------------------------------------------
def validate_project_methodology_config(
    project_type: str, config: Any
) -> Dict[str, Any]:
    """Validate and normalize a project's `methodology_config`.

    Returns the normalized config dict. Raises 422 on any shape violation.
    """
    if project_type not in PROJECT_TYPES:
        raise _unprocessable(
            f"unknown project_type '{project_type}' "
            f"(must be one of {sorted(PROJECT_TYPES)})"
        )
    if not isinstance(config, dict):
        raise _unprocessable("methodology_config must be an object")

    if project_type == "waterfall":
        return _validate_waterfall_config(config)
    if project_type == "agile":
        return _validate_agile_config(config)
    # waves_threads
    return _validate_waves_threads_config(config)


def _validate_waterfall_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize phases to a list of dicts with `name` and optional date fields.

    Accepts two input shapes for backward compat:
      - bare strings (legacy):   ["Discovery", "Design"]   -> [{name: "Discovery"}, ...]
      - phase objects (canonical):
            [{name: "Discovery", start_date: "2026-01-01", end_date: "2026-01-15"}, ...]
    """
    phases = config.get("phases")
    if not isinstance(phases, list) or len(phases) < 1:
        raise _unprocessable(
            "waterfall methodology_config requires 'phases' (non-empty list)"
        )
    normalized_phases: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for idx, phase in enumerate(phases):
        if isinstance(phase, str):
            name = _require_non_empty_str(phase, f"phases[{idx}]")
            entry: Dict[str, Any] = {"name": name}
        elif isinstance(phase, dict):
            name = _require_non_empty_str(phase.get("name"), f"phases[{idx}].name")
            entry = {"name": name}
            for date_key in ("start_date", "end_date"):
                if phase.get(date_key) is not None:
                    val = phase[date_key]
                    if not isinstance(val, str) or not val.strip():
                        raise _unprocessable(
                            f"phases[{idx}].{date_key} must be an ISO date string"
                        )
                    entry[date_key] = val
        else:
            raise _unprocessable(
                f"phases[{idx}] must be a string or an object with name/start_date/end_date"
            )
        if name in seen:
            raise _unprocessable(f"duplicate phase name: {name!r}")
        seen.add(name)
        normalized_phases.append(entry)
    return {"phases": normalized_phases}


def _validate_agile_config(config: Dict[str, Any]) -> Dict[str, Any]:
    columns = config.get("columns")
    if not isinstance(columns, list) or len(columns) < 1:
        raise _unprocessable(
            "agile methodology_config requires 'columns' (non-empty list)"
        )
    validated: list[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    done_count = 0
    for idx, col in enumerate(columns):
        if not isinstance(col, dict):
            raise _unprocessable(f"columns[{idx}] must be an object")
        cid = _require_non_empty_str(col.get("id"), f"columns[{idx}].id")
        cname = _require_non_empty_str(col.get("name"), f"columns[{idx}].name")
        if cid in seen_ids:
            raise _unprocessable(f"duplicate column id: {cid!r}")
        seen_ids.add(cid)
        entry: Dict[str, Any] = {"id": cid, "name": cname}

        color = col.get("color")
        if color is not None:
            if not isinstance(color, str) or not color.startswith("#"):
                raise _unprocessable(
                    f"columns[{idx}].color must be a hex string like '#RRGGBB'"
                )
            entry["color"] = color

        order = col.get("order")
        if order is not None:
            if isinstance(order, bool) or not isinstance(order, int):
                raise _unprocessable(f"columns[{idx}].order must be an integer")
            entry["order"] = order

        if col.get("is_done"):
            entry["is_done"] = True
            done_count += 1

        validated.append(entry)

    # Enforce at most one is_done. If caller set multiple, the last one wins
    # and earlier ones are cleared. (Matches Davies behavior — UX-friendly
    # for board configs that toggled multiple "done" columns historically.)
    if done_count > 1:
        found_last = False
        for entry in reversed(validated):
            if entry.get("is_done"):
                if found_last:
                    entry.pop("is_done", None)
                else:
                    found_last = True

    return {"columns": validated}


def _validate_waves_threads_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Davies kept methodology_config empty for waves+threads (waves are
    their own resource). The AEGISAXIOM mapping file requires
    `wave_count_target: int` and `modules: [str]` here so the dashboard
    + UI can render the planned shape before any waves are created.
    """
    wave_count_target = config.get("wave_count_target")
    if (
        isinstance(wave_count_target, bool)
        or not isinstance(wave_count_target, int)
        or wave_count_target < 1
    ):
        raise _unprocessable(
            "waves_threads methodology_config requires "
            "'wave_count_target' (positive integer)"
        )

    modules = config.get("modules")
    if not isinstance(modules, list) or len(modules) < 1:
        raise _unprocessable(
            "waves_threads methodology_config requires 'modules' (non-empty list)"
        )
    normalized_modules: list[str] = []
    seen: set[str] = set()
    for idx, mod in enumerate(modules):
        name = _require_non_empty_str(mod, f"modules[{idx}]")
        if name in seen:
            raise _unprocessable(f"duplicate module name: {name!r}")
        seen.add(name)
        normalized_modules.append(name)

    return {
        "wave_count_target": wave_count_target,
        "modules": normalized_modules,
    }


# ---------------------------------------------------------------------------
# Task-level: validate `methodology_fields`
# ---------------------------------------------------------------------------
def validate_task_methodology_fields(
    project: Dict[str, Any],
    methodology_fields: Any,
    status: Optional[str] = None,
    status_explicit: bool = True,
) -> Tuple[Dict[str, Any], str]:
    """Validate and normalize a task's `methodology_fields` against its
    parent project's `methodology_config`.

    Args:
        project: the project document — must contain ``project_type`` and
            ``methodology_config``. The route handler is responsible for
            loading this; this module never touches the DB.
        methodology_fields: the candidate methodology_fields payload.
        status: the candidate status string (or None to use the methodology
            default). For agile tasks the status is DERIVED from the column
            and any incoming status is ignored.

    Returns:
        A 2-tuple ``(normalized_fields, final_status)``.

    Raises:
        HTTPException(422): on any shape or referential violation.
    """
    if not isinstance(methodology_fields, dict):
        raise _unprocessable("methodology_fields must be an object")

    ptype = project.get("project_type")
    if ptype not in PROJECT_TYPES:
        raise _unprocessable(
            f"project.project_type '{ptype}' is not a recognized methodology"
        )
    pconfig = project.get("methodology_config") or {}

    if ptype == "waterfall":
        return _validate_waterfall_task(methodology_fields, pconfig, status)
    if ptype == "agile":
        # Only feed status to the agile validator when it was an explicit part
        # of the patch. Drag-drop sends `{methodology_fields: {column_id: X}}`
        # with no status field; the route handler defaults `status` to the
        # task's existing value, but treating that as a "requested status"
        # would re-translate it back and undo the column move.
        return _validate_agile_task(
            methodology_fields, pconfig, status if status_explicit else None
        )
    # waves_threads
    return _validate_waves_threads_task(methodology_fields, pconfig, status)


def _validate_waterfall_task(
    fields: Dict[str, Any],
    pconfig: Dict[str, Any],
    status: Optional[str],
) -> Tuple[Dict[str, Any], str]:
    # Wave 6 / Thread C: milestone_* fields added to support the MilestonesView
    # (Contract 5). When milestone=true, milestone_name + milestone_target_date
    # are REQUIRED. milestone_actual_date is set on completion. milestone_at_risk
    # is COMPUTED and is NOT stored — callers (route handlers / dashboards)
    # derive it from target_date + status at read time.
    allowed = {
        "phase",
        "milestone",
        "milestone_name",
        "milestone_target_date",
        "milestone_actual_date",
    }
    extra = set(fields.keys()) - allowed
    if extra:
        raise _unprocessable(
            f"unexpected methodology_fields for waterfall task: {sorted(extra)}"
        )
    phase = _require_non_empty_str(fields.get("phase"), "methodology_fields.phase")
    project_phases = pconfig.get("phases", [])
    # `project_phases` is normalized as List[Dict] (canonical) but legacy
    # writes may have List[str]. Accept either shape on read.
    phase_names = [
        p.get("name") if isinstance(p, dict) else p for p in project_phases
    ]
    if phase not in phase_names:
        raise _unprocessable(
            f"phase {phase!r} not in project phases {phase_names}"
        )
    milestone = fields.get("milestone", False)
    if not isinstance(milestone, bool):
        raise _unprocessable("methodology_fields.milestone must be boolean")

    normalized: Dict[str, Any] = {"phase": phase, "milestone": milestone}

    # Milestone metadata fields. Required when milestone=true; tolerated as
    # optional / ignored when milestone=false (any value present is dropped to
    # avoid orphan metadata on regular tasks).
    if milestone:
        milestone_name = _require_non_empty_str(
            fields.get("milestone_name"),
            "methodology_fields.milestone_name",
        )
        milestone_target_date = _require_non_empty_str(
            fields.get("milestone_target_date"),
            "methodology_fields.milestone_target_date",
        )
        normalized["milestone_name"] = milestone_name
        normalized["milestone_target_date"] = milestone_target_date

        actual = fields.get("milestone_actual_date")
        if actual is not None:
            if not isinstance(actual, str) or not actual.strip():
                raise _unprocessable(
                    "methodology_fields.milestone_actual_date must be an ISO date string"
                )
            normalized["milestone_actual_date"] = actual.strip()
    else:
        # If callers send milestone_* metadata on a non-milestone task, ignore
        # it silently rather than 422 — allows the same form to round-trip a
        # toggle without forcing the FE to clear stale fields.
        pass

    final_status = status or "not_started"
    if final_status not in WATERFALL_STATUSES:
        raise _unprocessable(
            f"invalid status for waterfall task: {final_status!r} "
            f"(must be one of {sorted(WATERFALL_STATUSES)})"
        )
    return (normalized, final_status)


def compute_milestone_at_risk(
    methodology_fields: Optional[Dict[str, Any]],
    status: Optional[str],
    today_iso: Optional[str] = None,
) -> bool:
    """Return True iff a waterfall milestone task is at-risk.

    A milestone is at-risk when:
      - methodology_fields.milestone is True, AND
      - milestone_target_date is in the past, AND
      - status is not "complete"

    Pure helper — callers (dashboards, list views) compute on read so the
    flag is always fresh without needing a stored bool.
    """
    if not methodology_fields or not methodology_fields.get("milestone"):
        return False
    target = methodology_fields.get("milestone_target_date")
    if not isinstance(target, str) or not target.strip():
        return False
    if status == "complete":
        return False
    from datetime import date

    try:
        target_d = date.fromisoformat(target[:10])
    except ValueError:
        return False
    if today_iso:
        try:
            today_d = date.fromisoformat(today_iso[:10])
        except ValueError:
            today_d = date.today()
    else:
        today_d = date.today()
    return target_d < today_d


def _agile_column_for_status(status: str, columns: list) -> Optional[Dict[str, Any]]:
    """Resolve a requested status string to an agile column.

    Accepts:
      - exact display-name match ("In Progress")
      - case-insensitive display-name match ("in progress")
      - slug form ("in_progress") that maps to a column whose slugified name matches
      - direct column id ("col_in_progress")

    Returns the matching column dict or None.
    """
    if not status:
        return None
    s_lower = status.strip().lower()
    s_slug = s_lower.replace(" ", "_")
    for c in columns:
        cname = (c.get("name") or "").strip()
        cid = (c.get("id") or "").strip()
        if cname == status:
            return c
        if cname.lower() == s_lower:
            return c
        if cname.lower().replace(" ", "_") == s_slug:
            return c
        if cid == status or cid.lower() == s_lower:
            return c
    return None


def _validate_agile_task(
    fields: Dict[str, Any],
    pconfig: Dict[str, Any],
    status: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    allowed = {"column_id", "story_points", "sprint_id"}
    extra = set(fields.keys()) - allowed
    if extra:
        raise _unprocessable(
            f"unexpected methodology_fields for agile task: {sorted(extra)}"
        )
    columns = pconfig.get("columns", [])

    # Agile tasks track their column in `methodology_fields.column_id` and the
    # status is DERIVED from the matching column's name. Callers patching just
    # `{status: "Done"}` (without touching methodology_fields) historically had
    # their status silently overwritten because column_id stayed put. Translate
    # an incoming status to the matching column id so PATCH `{status: ...}` on
    # an agile task moves the card to that column. We only override column_id
    # when the requested status doesn't already match the column's derived
    # name — that way explicit column_id patches (drag-drop, board reorder)
    # win when status was sent alongside but matches.
    column_id = fields.get("column_id")
    if status:
        current_col = next((c for c in columns if c.get("id") == column_id), None) if column_id else None
        current_name = (current_col or {}).get("name")
        if current_name != status:
            translated = _agile_column_for_status(status, columns)
            if translated is not None:
                column_id = translated["id"]
            else:
                # Don't silently swallow an unknown status — 422 with the valid
                # column-name list so the FE can show a clear error instead of
                # a silent no-op (Round 3 BUG-03).
                col_names = [c.get("name") for c in columns if c.get("name")]
                raise _unprocessable(
                    f"invalid status {status!r} for agile project — "
                    f"must match a column (valid: {col_names})"
                )

    column_id = _require_non_empty_str(
        column_id, "methodology_fields.column_id"
    )
    matching = next((c for c in columns if c.get("id") == column_id), None)
    if matching is None:
        raise _unprocessable(
            f"column_id {column_id!r} not in project columns"
        )

    story_points = fields.get("story_points")
    if story_points is not None:
        if isinstance(story_points, bool) or not isinstance(
            story_points, (int, float)
        ):
            raise _unprocessable(
                "methodology_fields.story_points must be a number"
            )

    sprint_id = fields.get("sprint_id")
    sprint_id_str: Optional[str] = None
    if sprint_id is not None:
        # AEGISAXIOM sprint IDs are prefixed UUID strings (sprint_<hex>); existence
        # is checked by the route handler against pm_sprints.
        sprint_id_str = _require_non_empty_str(
            sprint_id, "methodology_fields.sprint_id"
        )

    normalized: Dict[str, Any] = {"column_id": column_id}
    if story_points is not None:
        normalized["story_points"] = story_points
    if sprint_id_str is not None:
        normalized["sprint_id"] = sprint_id_str

    # Status is DERIVED from the column's canonical name. The agile bucket
    # set is the project's own column names — no shared enum.
    final_status = matching["name"]
    return (normalized, final_status)


def _validate_waves_threads_task(
    fields: Dict[str, Any],
    pconfig: Dict[str, Any],
    status: Optional[str],
) -> Tuple[Dict[str, Any], str]:
    allowed = {"wave_id", "thread_id", "module_owned", "bucket", "scope_drift"}
    extra = set(fields.keys()) - allowed
    if extra:
        raise _unprocessable(
            f"unexpected methodology_fields for waves_threads task: "
            f"{sorted(extra)}"
        )
    wave_id = _require_non_empty_str(
        fields.get("wave_id"), "methodology_fields.wave_id"
    )
    thread_id = _require_non_empty_str(
        fields.get("thread_id"), "methodology_fields.thread_id"
    )

    module_owned = _require_non_empty_str(
        fields.get("module_owned"), "methodology_fields.module_owned"
    )
    # If the project declared a list of modules, enforce membership. The
    # mapping file requires `modules` on waves_threads projects so this
    # is normally non-empty, but we tolerate empty-list configs (e.g.
    # legacy data) by skipping the check.
    project_modules = pconfig.get("modules") or []
    if project_modules and module_owned not in project_modules:
        raise _unprocessable(
            f"module_owned {module_owned!r} not in project modules "
            f"{project_modules}"
        )

    bucket = fields.get("bucket")
    if bucket is not None and (
        not isinstance(bucket, str) or not bucket.strip()
    ):
        raise _unprocessable("methodology_fields.bucket must be a non-empty string")

    scope_drift = fields.get("scope_drift", False)
    if not isinstance(scope_drift, bool):
        raise _unprocessable("methodology_fields.scope_drift must be boolean")

    final_status = status or "scoped"
    if final_status not in WAVES_THREADS_STATUSES:
        raise _unprocessable(
            f"invalid status for waves_threads task: {final_status!r} "
            f"(must be one of {sorted(WAVES_THREADS_STATUSES)})"
        )

    normalized: Dict[str, Any] = {
        "wave_id": wave_id,
        "thread_id": thread_id,
        "module_owned": module_owned,
        "scope_drift": scope_drift,
    }
    if bucket is not None:
        normalized["bucket"] = bucket.strip()
    return (normalized, final_status)
