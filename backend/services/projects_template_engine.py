"""Projects module — Template engine (pure functions).

Owns the data transformations between a live project (project doc + tasks +
phases/sprints/waves) and a stored template document, plus the dry-run preview
shape that the frontend renders before instantiation.

This module is intentionally I/O-free: route handlers do all Mongo reads and
writes. The functions here take plain dicts and return plain dicts, which
keeps them trivially unit-testable and lets the route layer compose them
without round-tripping through fixtures.

Conceptual model
----------------
A *template* captures the *shape* of a project — methodology, task tree,
phase/sprint structure, agile columns — without carrying user references or
absolute dates. When a template is instantiated, dates are computed relative
to a caller-supplied start_date, and assignees come from instantiation-time
overrides (or are left unset).

Each template task definition has a stable synthetic `template_task_id` so
that override maps (skip lists, assignee mappings, title prefixes) and
provenance can address tasks unambiguously. Task dependency arrays use the
same synthetic ids; hydration rewrites them to the freshly minted task_ids.

Versioning
----------
Templates carry a `version: int` field. PATCH bumps it. When a project is
instantiated, the project doc records `source_template_id` plus
`source_template_version` so audit can answer "which version of which
template did this project come from."

Methodology coverage
--------------------
- waterfall: snapshots/hydrates phases as List[{name, start_offset_days?,
  end_offset_days?}]. Absolute dates in the source project are converted to
  offsets when `include_dates=False`; preserved when `include_dates=True`.
- agile: snapshots/hydrates sprint cadence as
  List[{name, start_offset_days, end_offset_days, goal?, capacity_points?}].
  Hydration creates one pm_sprints row per definition.
- waves_threads: snapshots/hydrates wave scaffold as List[{goal?,
  start_offset_days?, end_offset_days?, depends_on_indexes?}]. The
  `depends_on_indexes` array references other entries in the same list by
  index; hydration rewrites these into wave_ids.

Date semantics
--------------
- Templates store *only* `day_offset_from_start` (or pair of offsets) on
  every dated entity, unless the original snapshot was taken with
  include_dates=True — in which case raw ISO dates are stored alongside and
  used verbatim on hydration.
- Hydration: due_date_computed = start_date + day_offset_from_start. If
  `skip_weekends` is True on the template config, Saturdays and Sundays are
  rolled forward to the next Monday.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import uuid


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SUPPORTED_METHODOLOGIES = {"waterfall", "agile", "waves_threads"}

# Fields stripped from project + task snapshots regardless of include_dates.
# Templates must not carry user references, audit trails, or runtime state
# (a stale assignee_id baked into a template would re-assign work to a user
# who may not even be on the project).
PROJECT_STRIP_FIELDS = {
    "project_id",
    "pm_id",
    "assigned_user_ids",
    "status",
    "health",
    "actual_end_date",
    "contract_id",
    "account_id",
    "created_at",
    "updated_at",
    "created_by",
    "template_id",
    "source_template_id",
    "source_template_version",
    "_id",
}

TASK_STRIP_FIELDS = {
    "task_id",
    "project_id",
    "assignee_id",
    "task_number",
    "created_at",
    "updated_at",
    "created_by",
    "_id",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_iso_date(value: Any) -> Optional[date]:
    """Parse an ISO date / datetime string into a date. Returns None on
    None / empty / unparseable inputs — callers decide whether that's an
    error (we deliberately don't raise here so snapshot can be lenient on
    partially dated projects)."""
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            # Accept both "YYYY-MM-DD" and full ISO datetimes.
            if "T" in value:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _day_offset(target: Any, anchor: Optional[date]) -> Optional[int]:
    """Number of days from anchor to target. None if either side can't
    be parsed."""
    if anchor is None:
        return None
    t = _parse_iso_date(target)
    if t is None:
        return None
    return (t - anchor).days


def _roll_forward_weekend(d: date) -> date:
    """If date falls on Saturday or Sunday, roll to the next Monday."""
    if d.weekday() == 5:  # Saturday
        return d + timedelta(days=2)
    if d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d


def _compute_date_from_offset(
    start: date, offset: Optional[int], *, skip_weekends: bool = False
) -> Optional[str]:
    """Return ISO date string for start + offset. None if offset is None."""
    if offset is None:
        return None
    result = start + timedelta(days=int(offset))
    if skip_weekends:
        result = _roll_forward_weekend(result)
    return result.isoformat()


def _new_template_task_id() -> str:
    """Synthetic stable id used to address task definitions inside a template
    (for overrides + dependency rewiring on hydration). Distinct from the
    AEGISAXIOM `task_xxx` ids minted on instantiation."""
    return f"ttask_{uuid.uuid4().hex[:12]}"


def _stripped(doc: Dict[str, Any], strip: Iterable[str]) -> Dict[str, Any]:
    return {k: v for k, v in doc.items() if k not in strip}


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------
def snapshot_project(
    project_doc: Dict[str, Any],
    tasks: Sequence[Dict[str, Any]],
    phases: Optional[Sequence[Dict[str, Any]]] = None,
    sprints: Optional[Sequence[Dict[str, Any]]] = None,
    waves: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    include_dates: bool = False,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a template document from a live project + its child entities.

    Args:
        project_doc: the source project (must carry project_type,
            service_line, optionally methodology_config + start_date).
        tasks: pm_tasks rows belonging to the project.
        phases: ignored when project_type != "waterfall". If None for
            waterfall, falls back to project_doc.methodology_config.phases.
        sprints: ignored when project_type != "agile". Optional.
        waves: ignored when project_type != "waves_threads". Optional.
        include_dates: when False (default), drop absolute dates and convert
            them to day-offsets from project.start_date. When True, preserve
            the absolute dates AS WELL AS computing offsets so hydration can
            pick the right representation per task.
        name / description: the new template's name / description. Falls
            back to project name + " (Template)" if name is omitted.

    Returns:
        Template document ready to insert into pm_templates. Does NOT
        include template_id / created_at / created_by — the route handler
        stamps those.
    """
    project_type = project_doc.get("project_type")
    if project_type not in SUPPORTED_METHODOLOGIES:
        raise ValueError(
            f"Unsupported project_type for template snapshot: {project_type!r}"
        )

    anchor = _parse_iso_date(project_doc.get("start_date"))

    # --- Tasks ------------------------------------------------------------
    task_definitions: List[Dict[str, Any]] = []
    # Map original task_id → synthetic template_task_id for dependency rewriting.
    id_map: Dict[str, str] = {}
    for t in tasks:
        original_id = t.get("task_id")
        ttid = _new_template_task_id()
        if original_id:
            id_map[original_id] = ttid
    # Second pass: build definitions with rewritten dependencies + offsets.
    for t in tasks:
        clean = _stripped(t, TASK_STRIP_FIELDS)
        # Drop assignee in any guise (the strip already removed assignee_id,
        # but methodology_fields might smuggle one in — be defensive).
        mf = dict(clean.get("methodology_fields") or {})
        mf.pop("assignee_id", None)
        clean["methodology_fields"] = mf

        # Dependency rewrite.
        deps = list(clean.get("dependencies") or [])
        clean["dependencies"] = [id_map[d] for d in deps if d in id_map]

        # Date handling.
        start_off = _day_offset(t.get("start_date"), anchor)
        due_off = _day_offset(t.get("due_date"), anchor)
        if include_dates:
            # Preserve absolute dates AND offsets so hydration can pick.
            clean["start_date"] = t.get("start_date")
            clean["due_date"] = t.get("due_date")
        else:
            clean.pop("start_date", None)
            clean.pop("due_date", None)
        if start_off is not None:
            clean["start_offset_days"] = start_off
        if due_off is not None:
            clean["due_offset_days"] = due_off

        clean["template_task_id"] = id_map[t["task_id"]] if t.get("task_id") else _new_template_task_id()
        task_definitions.append(clean)

    # --- Methodology seeds -----------------------------------------------
    cfg = dict(project_doc.get("methodology_config") or {})
    default_phases: Optional[List[Dict[str, Any]]] = None
    default_columns: Optional[List[Dict[str, Any]]] = None
    default_sprints: Optional[List[Dict[str, Any]]] = None
    default_waves: Optional[List[Dict[str, Any]]] = None

    if project_type == "waterfall":
        # Prefer the live sprints/phases arg; fall back to methodology_config.phases.
        source_phases = list(phases) if phases is not None else list(cfg.get("phases") or [])
        default_phases = []
        for p in source_phases:
            if not isinstance(p, dict):
                continue
            ph: Dict[str, Any] = {"name": p.get("name")}
            s_off = _day_offset(p.get("start_date"), anchor)
            e_off = _day_offset(p.get("end_date"), anchor)
            if s_off is not None:
                ph["start_offset_days"] = s_off
            if e_off is not None:
                ph["end_offset_days"] = e_off
            if include_dates:
                if p.get("start_date"):
                    ph["start_date"] = p["start_date"]
                if p.get("end_date"):
                    ph["end_date"] = p["end_date"]
            default_phases.append(ph)
    elif project_type == "agile":
        # Columns come from methodology_config; sprints from the arg.
        cols = cfg.get("columns") or []
        default_columns = [
            {"id": c.get("id"), "name": c.get("name")}
            for c in cols
            if isinstance(c, dict) and c.get("id")
        ]
        if sprints:
            default_sprints = []
            for s in sprints:
                if not isinstance(s, dict):
                    continue
                sp: Dict[str, Any] = {"name": s.get("name")}
                s_off = _day_offset(s.get("start_date"), anchor)
                e_off = _day_offset(s.get("end_date"), anchor)
                if s_off is not None:
                    sp["start_offset_days"] = s_off
                if e_off is not None:
                    sp["end_offset_days"] = e_off
                if s.get("goal"):
                    sp["goal"] = s["goal"]
                if s.get("capacity_points") is not None:
                    sp["capacity_points"] = s["capacity_points"]
                default_sprints.append(sp)
    elif project_type == "waves_threads":
        # Wave scaffold — preserve goals + offsets + depends_on by index.
        if waves:
            wave_id_to_index = {
                w.get("wave_id"): i for i, w in enumerate(waves) if w.get("wave_id")
            }
            default_waves = []
            for w in waves:
                if not isinstance(w, dict):
                    continue
                wv: Dict[str, Any] = {}
                if w.get("goal"):
                    wv["goal"] = w["goal"]
                s_off = _day_offset(w.get("start_date"), anchor)
                e_off = _day_offset(w.get("end_date"), anchor)
                if s_off is not None:
                    wv["start_offset_days"] = s_off
                if e_off is not None:
                    wv["end_offset_days"] = e_off
                deps = [
                    wave_id_to_index[d]
                    for d in (w.get("depends_on_wave_ids") or [])
                    if d in wave_id_to_index
                ]
                if deps:
                    wv["depends_on_indexes"] = deps
                default_waves.append(wv)

    # --- Assemble the template doc ---------------------------------------
    template_name = name or f"{project_doc.get('name', 'Untitled')} (Template)"

    out: Dict[str, Any] = {
        "name": template_name,
        "description": description,
        "project_type": project_type,
        "service_line": project_doc.get("service_line"),
        "task_definitions": task_definitions,
        "default_phases": default_phases,
        "default_columns": default_columns,
        "default_sprints": default_sprints,
        "default_waves": default_waves,
        "is_system": False,
        "version": 1,
        "config": {
            "include_dates": bool(include_dates),
            # `skip_weekends` is opt-in on hydration; default off. Stored on
            # the template so a PM can set "this template always rolls to
            # Monday" once and re-instantiate without re-passing it.
            "skip_weekends": False,
        },
    }
    return out


# ---------------------------------------------------------------------------
# Hydration (template → project + tasks + phases/sprints/waves)
# ---------------------------------------------------------------------------
def _normalize_overrides(overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    overrides = overrides or {}
    return {
        "task_title_prefix": overrides.get("task_title_prefix") or "",
        "skip_task_ids": set(overrides.get("skip_task_ids") or []),
        "assignee_mapping": dict(overrides.get("assignee_mapping") or {}),
    }


def hydrate_template(
    template: Dict[str, Any],
    *,
    start_date: str,
    pm_user_id: str,
    name: str,
    description: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Hydrate a template into a project + tasks + methodology entities.

    This function is pure: it returns blueprints (dicts) for the route
    handler to insert into Mongo. The blueprints do NOT include the
    project_id / task_id / etc — those are minted by the route. Cross-entity
    references inside the blueprint are by *synthetic* template ids
    (template_task_id, index for waves), which the route resolves to AEGISAXIOM
    ids after insertion.

    Args:
        template: the pm_templates document.
        start_date: ISO date string anchoring all offset calculations.
        pm_user_id: stamped on the project as pm_id.
        name: name for the new project.
        description: optional description override.
        overrides: optional dict with keys:
          - task_title_prefix: prepended to every task title
          - skip_task_ids: iterable of template_task_id values to omit
          - assignee_mapping: {template_task_id: user_id} pairs

    Returns:
        {
          "project": {project blueprint, sans project_id},
          "tasks": [{task blueprint, sans task_id but with template_task_id}, ...],
          "phases": [...],   # waterfall only, possibly empty
          "sprints": [...],  # agile only, possibly empty
          "waves": [...],    # waves_threads only, possibly empty
        }
    """
    anchor = _parse_iso_date(start_date)
    if anchor is None:
        raise ValueError(f"start_date is not a valid ISO date: {start_date!r}")

    project_type = template.get("project_type")
    if project_type not in SUPPORTED_METHODOLOGIES:
        raise ValueError(
            f"Unsupported project_type on template: {project_type!r}"
        )

    norm = _normalize_overrides(overrides)
    skip_weekends = bool((template.get("config") or {}).get("skip_weekends"))

    # --- Project blueprint ------------------------------------------------
    project_bp: Dict[str, Any] = {
        "name": name,
        "description": description if description is not None else template.get("description"),
        "project_type": project_type,
        "service_line": template.get("service_line"),
        "status": "planning",
        "health": "green",
        "pm_id": pm_user_id,
        "assigned_user_ids": [],
        "start_date": anchor.isoformat(),
        "target_end_date": None,
        "actual_end_date": None,
        # Provenance — what the route should persist on the project doc.
        "source_template_id": template.get("template_id"),
        "source_template_version": int(template.get("version") or 1),
        # methodology_config is filled in below per type.
        "methodology_config": {},
    }

    # --- Methodology seeding ---------------------------------------------
    phases_bp: List[Dict[str, Any]] = []
    sprints_bp: List[Dict[str, Any]] = []
    waves_bp: List[Dict[str, Any]] = []

    if project_type == "waterfall":
        source_phases = list(template.get("default_phases") or [])
        for p in source_phases:
            if not isinstance(p, dict):
                continue
            entry: Dict[str, Any] = {"name": p.get("name")}
            # Prefer absolute dates if the template stored them; else use offsets.
            if p.get("start_date"):
                entry["start_date"] = p["start_date"]
            else:
                entry["start_date"] = _compute_date_from_offset(
                    anchor, p.get("start_offset_days"), skip_weekends=skip_weekends
                )
            if p.get("end_date"):
                entry["end_date"] = p["end_date"]
            else:
                entry["end_date"] = _compute_date_from_offset(
                    anchor, p.get("end_offset_days"), skip_weekends=skip_weekends
                )
            phases_bp.append(entry)
        project_bp["methodology_config"] = {"phases": phases_bp}
    elif project_type == "agile":
        cols = list(template.get("default_columns") or [])
        normalized_cols: List[Dict[str, Any]] = []
        for i, c in enumerate(cols):
            if not isinstance(c, dict):
                continue
            normalized_cols.append(
                {
                    "id": c.get("id") or (c.get("name") or f"col_{i}").lower().replace(" ", "_"),
                    "name": c.get("name") or c.get("id") or f"Column {i+1}",
                    "order": i,
                }
            )
        if not normalized_cols:
            normalized_cols = [
                {"id": "to_do", "name": "To Do", "order": 0},
                {"id": "in_progress", "name": "In Progress", "order": 1},
                {"id": "done", "name": "Done", "order": 2},
            ]
        project_bp["methodology_config"] = {"columns": normalized_cols}

        for s in (template.get("default_sprints") or []):
            if not isinstance(s, dict):
                continue
            sprints_bp.append(
                {
                    "name": s.get("name") or "Sprint",
                    "start_date": _compute_date_from_offset(
                        anchor, s.get("start_offset_days"), skip_weekends=skip_weekends
                    ),
                    "end_date": _compute_date_from_offset(
                        anchor, s.get("end_offset_days"), skip_weekends=skip_weekends
                    ),
                    "goal": s.get("goal"),
                    "capacity_points": s.get("capacity_points"),
                    "status": "planned",
                }
            )
    elif project_type == "waves_threads":
        # Modules + wave_count_target keep the validator happy; the actual
        # wave docs go into waves_bp for the route to insert.
        project_bp["methodology_config"] = {
            "wave_count_target": max(1, len(template.get("default_waves") or [])),
            "modules": ["general"],
        }
        for w in (template.get("default_waves") or []):
            if not isinstance(w, dict):
                continue
            waves_bp.append(
                {
                    "goal": w.get("goal"),
                    "start_date": _compute_date_from_offset(
                        anchor, w.get("start_offset_days"), skip_weekends=skip_weekends
                    ),
                    "end_date": _compute_date_from_offset(
                        anchor, w.get("end_offset_days"), skip_weekends=skip_weekends
                    ),
                    # depends_on indexes get rewritten by the route after
                    # wave inserts mint wave_ids.
                    "_depends_on_indexes": list(w.get("depends_on_indexes") or []),
                    "status": "planning",
                }
            )

    # --- Tasks ------------------------------------------------------------
    tasks_bp: List[Dict[str, Any]] = []
    template_task_ids_seen: List[str] = []
    for td in (template.get("task_definitions") or []):
        if not isinstance(td, dict):
            continue
        ttid = td.get("template_task_id") or _new_template_task_id()
        if ttid in norm["skip_task_ids"]:
            continue
        template_task_ids_seen.append(ttid)

        # Title prefix (override).
        title = td.get("title") or "Untitled"
        if norm["task_title_prefix"]:
            title = f"{norm['task_title_prefix']}{title}"

        # Date resolution: absolute > offset.
        start_iso: Optional[str]
        due_iso: Optional[str]
        if td.get("start_date"):
            start_iso = td["start_date"]
        else:
            start_iso = _compute_date_from_offset(
                anchor, td.get("start_offset_days"), skip_weekends=skip_weekends
            )
        if td.get("due_date"):
            due_iso = td["due_date"]
        else:
            due_iso = _compute_date_from_offset(
                anchor, td.get("due_offset_days"), skip_weekends=skip_weekends
            )

        tasks_bp.append(
            {
                "template_task_id": ttid,
                "title": title,
                "description": td.get("description"),
                "assignee_id": norm["assignee_mapping"].get(ttid),
                "status": td.get("status"),  # let methodology validator default
                "priority": td.get("priority", "medium"),
                "estimated_duration_hours": td.get("estimated_duration_hours"),
                "parent_task_id": None,  # populated post-insert from parent template id
                "_parent_template_task_id": td.get("parent_task_id"),
                "start_date": start_iso,
                "due_date": due_iso,
                # Dependencies still reference template_task_id values; the
                # route rewrites them once real task_ids exist.
                "dependencies": list(td.get("dependencies") or []),
                "methodology_fields": dict(td.get("methodology_fields") or {}),
                "labels": list(td.get("labels") or []),
                "sort_index": td.get("sort_index"),
            }
        )

    return {
        "project": project_bp,
        "tasks": tasks_bp,
        "phases": phases_bp,
        "sprints": sprints_bp,
        "waves": waves_bp,
    }


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------
PREVIEW_TASK_CAP = 50


def build_preview(
    template: Dict[str, Any],
    *,
    start_date: str,
    pm_user_id: Optional[str] = None,
    task_cap: int = PREVIEW_TASK_CAP,
) -> Dict[str, Any]:
    """Build a dry-run preview from a template + start_date.

    Returns the shape the frontend renders before the PM clicks "Create."
    No DB writes — purely computational.

    Caps the task list at `task_cap` rows; emits a warning describing the
    truncation if the template has more.
    """
    anchor = _parse_iso_date(start_date)
    if anchor is None:
        raise ValueError(f"start_date is not a valid ISO date: {start_date!r}")

    project_type = template.get("project_type")
    skip_weekends = bool((template.get("config") or {}).get("skip_weekends"))

    task_defs = list(template.get("task_definitions") or [])
    warnings: List[str] = []
    truncated = False
    if len(task_defs) > task_cap:
        warnings.append(
            f"Template has {len(task_defs)} tasks; preview shows first "
            f"{task_cap}. Full hydration will create all of them."
        )
        task_defs = task_defs[:task_cap]
        truncated = True

    preview_tasks: List[Dict[str, Any]] = []
    for td in task_defs:
        if not isinstance(td, dict):
            continue
        due_iso: Optional[str]
        if td.get("due_date"):
            due_iso = td["due_date"]
        else:
            due_iso = _compute_date_from_offset(
                anchor, td.get("due_offset_days"), skip_weekends=skip_weekends
            )
        preview_tasks.append(
            {
                "template_task_id": td.get("template_task_id"),
                "title": td.get("title") or "Untitled",
                "status": td.get("status"),
                "priority": td.get("priority", "medium"),
                "due_date_computed": due_iso,
                "phase": (td.get("methodology_fields") or {}).get("phase"),
                "depends_on": list(td.get("dependencies") or []),
            }
        )

    # Estimated duration: max(due_offset_days) across all task_definitions
    # (not just the truncated preview), falling back to max(end_offset_days)
    # of phases/sprints/waves.
    max_offset = 0
    for td in template.get("task_definitions") or []:
        if not isinstance(td, dict):
            continue
        for k in ("due_offset_days", "start_offset_days"):
            v = td.get(k)
            if isinstance(v, int) and v > max_offset:
                max_offset = v
    for coll_key in ("default_phases", "default_sprints", "default_waves"):
        for entry in template.get(coll_key) or []:
            if not isinstance(entry, dict):
                continue
            v = entry.get("end_offset_days")
            if isinstance(v, int) and v > max_offset:
                max_offset = v

    phases_preview: List[Dict[str, Any]] = []
    sprints_preview: List[Dict[str, Any]] = []
    waves_preview: List[Dict[str, Any]] = []
    if project_type == "waterfall":
        for p in template.get("default_phases") or []:
            if not isinstance(p, dict):
                continue
            phases_preview.append(
                {
                    "name": p.get("name"),
                    "start_date_computed": p.get("start_date")
                    or _compute_date_from_offset(
                        anchor, p.get("start_offset_days"), skip_weekends=skip_weekends
                    ),
                    "end_date_computed": p.get("end_date")
                    or _compute_date_from_offset(
                        anchor, p.get("end_offset_days"), skip_weekends=skip_weekends
                    ),
                }
            )
    elif project_type == "agile":
        for s in template.get("default_sprints") or []:
            if not isinstance(s, dict):
                continue
            sprints_preview.append(
                {
                    "name": s.get("name"),
                    "start_date_computed": _compute_date_from_offset(
                        anchor, s.get("start_offset_days"), skip_weekends=skip_weekends
                    ),
                    "end_date_computed": _compute_date_from_offset(
                        anchor, s.get("end_offset_days"), skip_weekends=skip_weekends
                    ),
                    "goal": s.get("goal"),
                    "capacity_points": s.get("capacity_points"),
                }
            )
    elif project_type == "waves_threads":
        for w in template.get("default_waves") or []:
            if not isinstance(w, dict):
                continue
            waves_preview.append(
                {
                    "goal": w.get("goal"),
                    "start_date_computed": _compute_date_from_offset(
                        anchor, w.get("start_offset_days"), skip_weekends=skip_weekends
                    ),
                    "end_date_computed": _compute_date_from_offset(
                        anchor, w.get("end_offset_days"), skip_weekends=skip_weekends
                    ),
                    "depends_on_indexes": list(w.get("depends_on_indexes") or []),
                }
            )

    return {
        "project_name_pattern": template.get("name") or "Untitled Template",
        "project_type": project_type,
        "service_line": template.get("service_line"),
        "estimated_duration_days": int(max_offset),
        "task_count_total": len(template.get("task_definitions") or []),
        "task_count_shown": len(preview_tasks),
        "truncated": truncated,
        "tasks": preview_tasks,
        "phases": phases_preview,
        "sprints": sprints_preview,
        "waves": waves_preview,
        "warnings": warnings,
        "version": int(template.get("version") or 1),
    }


__all__ = [
    "SUPPORTED_METHODOLOGIES",
    "PREVIEW_TASK_CAP",
    "snapshot_project",
    "hydrate_template",
    "build_preview",
]
