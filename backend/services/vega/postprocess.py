"""Vega v3 Stage 4 post-processing — pure Python, no LLM calls.

Implements the two deterministic helpers that Thread 2c owns:

    _detect_shared_infrastructure(workflows, apps) -> list[SharedInfrastructureRow]
    _aggregate_open_items(workflows, apps, session_flags)  -> list[OpenItem]

Both are imported by the Stage 4 stub in v3_pipeline.py and called from
_stage_4_postprocess().

Design notes
------------
- No asyncio, no DB, no call_llm.  These run synchronously inside
  _stage_4_postprocess which is itself synchronous.
- Apps with partial_extraction_failed=True have no detail fields (integrations /
  personas / nfrs / scale_profile are at default-factory empties).  Both
  functions skip those entities without crashing.
- Sorting in _aggregate_open_items is deterministic: blocking flags come first,
  then sorted by location string, then by label.  This keeps the Review-tab
  ordering stable across pipeline runs on the same artifact.
"""

from __future__ import annotations

from typing import Optional

from services.vega.flags import VegaFlag, display_label, is_blocking_for_handoff, long_explanation
from services.vega.models_v3 import (
    App,
    OpenItem,
    SessionFlags,
    SharedInfrastructureRow,
    Workflow,
)

__all__ = [
    "detect_shared_infrastructure",
    "aggregate_open_items",
]


# ---------------------------------------------------------------------------
# Read/write hint detection
# ---------------------------------------------------------------------------

_READ_HINTS = frozenset({"read", "reads", "reading", "query", "queries", "fetch", "pull"})
_WRITE_HINTS = frozenset({"write", "writes", "writing", "push", "update", "updates", "insert", "upsert", "send"})


def _infer_read_write(notes_list: list[str]) -> str:
    """Derive a read/write classification from a list of free-text note strings.

    Scans each note for keyword hints.  Returns one of the four Literal values
    accepted by SharedInfrastructureRow.read_write:
      'read'       — only read signals found
      'write'      — only write signals found
      'read_write' — both found
      'unknown'    — no parseable signals
    """
    has_read = False
    has_write = False
    for note in notes_list:
        if not note:
            continue
        lower = note.lower()
        for word in lower.split():
            # Strip common punctuation before comparing
            clean = word.strip(".,;:()")
            if clean in _READ_HINTS:
                has_read = True
            if clean in _WRITE_HINTS:
                has_write = True
    if has_read and has_write:
        return "read_write"
    if has_read:
        return "read"
    if has_write:
        return "write"
    return "unknown"


# ---------------------------------------------------------------------------
# Function 1: _detect_shared_infrastructure
# ---------------------------------------------------------------------------


def detect_shared_infrastructure(
    workflows: list[Workflow],
    apps: list[App],
) -> list[SharedInfrastructureRow]:
    """II.2 — scan App.integrations and surface shared systems.

    Any integration system_name that appears in 2+ apps is emitted as a
    SharedInfrastructureRow.  Apps with partial_extraction_failed=True are
    silently skipped (they have no integration data to walk).

    Args:
        workflows: Merged Workflow list from Stage 2/3.  Not directly used
            here — accepted to match the v3_pipeline.py stub signature, and
            may be needed if back-link logic moves here in a later wave.
        apps: Merged App list from Stage 2/3.

    Returns:
        List of SharedInfrastructureRow, one per system appearing in 2+ apps.
        Empty list when fewer than 2 apps share any integration.
        Sorted by system_name for stable output.
    """
    # Group integration data by normalised system_name. Each bucket holds
    # the canonical display name, the app IDs touching it, and the structured
    # read_write / access_status / auth_method values from each entry — when
    # the new IntegrationEntry depth fields are populated we use those
    # directly and only fall back to notes-keyword inference when the
    # structured value is missing.
    buckets: dict[str, dict] = {}

    for app in apps:
        if app.partial_extraction_failed:
            continue
        for entry in app.integrations:
            key = entry.system_name.strip().lower()
            if not key:
                continue
            if key not in buckets:
                buckets[key] = {
                    "canonical": entry.system_name.strip(),
                    "app_ids": set(),
                    "notes": [],
                    "structured_rw": set(),
                    "auth_methods": set(),
                    "access_statuses": set(),
                }
            bucket = buckets[key]
            bucket["app_ids"].add(app.app_id)
            if entry.notes:
                note = entry.notes.strip()
                if note and note not in bucket["notes"]:
                    bucket["notes"].append(note)
            rw = getattr(entry, "read_write", None)
            if rw and rw != "unknown":
                bucket["structured_rw"].add(rw)
            am = getattr(entry, "auth_method", None)
            if am:
                bucket["auth_methods"].add(am.strip())
            acc = getattr(entry, "access_status", None)
            if acc and acc != "unknown":
                bucket["access_statuses"].add(acc)

    def _merge_rw(structured: set[str], notes_list: list[str]) -> str:
        if "read_write" in structured or ("read" in structured and "write" in structured):
            return "read_write"
        if "read" in structured and "write" not in structured:
            return "read"
        if "write" in structured and "read" not in structured:
            return "write"
        # Fall back to keyword inference when no structured signal present
        return _infer_read_write(notes_list)

    def _merge_access(statuses: set[str]) -> Optional[str]:
        if not statuses:
            return None
        if len(statuses) == 1:
            return next(iter(statuses))
        return "mixed"

    def _merge_auth(methods: set[str]) -> Optional[str]:
        if not methods:
            return None
        if len(methods) == 1:
            return next(iter(methods))
        return " / ".join(sorted(methods))

    rows: list[SharedInfrastructureRow] = []
    for bucket in buckets.values():
        if len(bucket["app_ids"]) < 2:
            continue
        notes_list = bucket["notes"]
        combined_notes = " | ".join(notes_list) if notes_list else ""
        rows.append(
            SharedInfrastructureRow(
                system_name=bucket["canonical"],
                used_by_app_ids=sorted(bucket["app_ids"]),
                read_write=_merge_rw(bucket["structured_rw"], notes_list),
                auth_method=_merge_auth(bucket["auth_methods"]),
                access_status=_merge_access(bucket["access_statuses"]),
                notes=combined_notes if combined_notes else None,
            )
        )

    rows.sort(key=lambda r: r.system_name.lower())
    return rows


# ---------------------------------------------------------------------------
# Function 2: _aggregate_open_items
# ---------------------------------------------------------------------------


def _make_item(
    flag: VegaFlag,
    label: str,
    location: str,
    detail: Optional[str] = None,
) -> OpenItem:
    """Convenience constructor for OpenItem."""
    return OpenItem(
        flag=flag,
        label=label,
        location=location,
        detail=detail or long_explanation(flag),
    )


def _open_items_from_session(session_flags: SessionFlags) -> list[OpenItem]:
    """Walk the three SessionFlags flag fields and emit OpenItems."""
    items: list[OpenItem] = []

    if session_flags.tech_contact_flag is not None:
        items.append(_make_item(
            flag=session_flags.tech_contact_flag,
            label="Tech contact missing",
            location="session",
            detail=(
                "No technical contact name was identified in the transcript. "
                "Confirm before handoff."
            ),
        ))

    if session_flags.engineering_capacity_flag is not None:
        items.append(_make_item(
            flag=session_flags.engineering_capacity_flag,
            label="Engineering capacity not confirmed",
            location="session",
            detail=(
                "Whether the customer has internal engineering capacity was not "
                "addressed in the transcript."
            ),
        ))

    if session_flags.it_infosec_gate_flag is not None:
        items.append(_make_item(
            flag=session_flags.it_infosec_gate_flag,
            label="IT/InfoSec gate not discussed",
            location="session",
            detail=(
                "No mention of IT approval or security review gate was found. "
                "Confirm whether one applies before handoff."
            ),
        ))

    return items


def _open_items_from_workflow(workflow: Workflow) -> list[OpenItem]:
    """Walk a single Workflow and emit OpenItems for every flagged field."""
    items: list[OpenItem] = []
    loc = f"workflow:{workflow.workflow_id}"
    wf_name = workflow.workflow_name or "this workflow"

    # Partial extraction failure
    if workflow.partial_extraction_failed:
        items.append(_make_item(
            flag=VegaFlag.MISSING,
            label=f"Workflow '{wf_name}' — detail extraction failed",
            location=loc,
            detail=(
                workflow.partial_extraction_reason
                or "Stage 2a extraction failed — skeleton data only."
            ),
        ))

    # Acceptance criteria flags
    for ac in workflow.acceptance_criteria:
        if ac.flag is not None:
            flow_label = ac.flow_name or "unnamed flow"
            items.append(_make_item(
                flag=ac.flag,
                label=f"{wf_name} · Acceptance criterion '{flow_label}'",
                location=loc,
                detail=(
                    f"Confirm the success condition for the '{flow_label}' flow "
                    f"in workflow '{wf_name}'. {long_explanation(ac.flag)}"
                ),
            ))

    # Decision logic — only emit an OpenItem for a decision point if the
    # decision point itself is flagged.  The contract says: "only if the
    # decision point is itself flagged, not just because inferred=True".
    # Decision points don't have a dedicated flag field in the model, so we
    # check: if inferred=True AND there are no branches (i.e. the point looks
    # incomplete), that's a signal worth surfacing.  But per the spec, the
    # trigger is a flag on the item — and since DecisionPoint has no flag field
    # in the v3 schema, we skip this for now.  The contract explicitly says
    # "only if the decision point is itself flagged" — no flag field means
    # nothing to walk here.
    # (Judgment call: see module docstring in v3_pipeline.py — 2c's contract
    # was: "only if the decision point is itself flagged, not just because
    # inferred=True".)

    # Persona override flags
    for persona in workflow.persona_overrides:
        for flag in persona.flags:
            role_label = persona.role or "unnamed persona"
            items.append(_make_item(
                flag=flag,
                label=f"{wf_name} · Persona override '{role_label}'",
                location=loc,
                detail=(
                    f"Confirm the persona override for role '{role_label}' "
                    f"in workflow '{wf_name}'. {long_explanation(flag)}"
                ),
            ))

    return items


# Display names for NFR fields so open items name the actual aspect.
_NFR_FIELD_LABELS = {
    "device_support": "Device support",
    "browser_support": "Browser support",
    "data_freshness": "Data freshness",
    "rbac": "RBAC / access control",
    "sla": "SLA / uptime",
    "compliance": "Compliance",
    "multi_region": "Multi-region / data residency",
}


def _open_items_from_app(app: App) -> list[OpenItem]:
    """Walk a single App and emit OpenItems for every flagged field."""
    items: list[OpenItem] = []
    loc = f"app:{app.app_id}"
    app_name = app.app_name or "this app"

    # Partial extraction failure
    if app.partial_extraction_failed:
        items.append(_make_item(
            flag=VegaFlag.MISSING,
            label=f"App '{app_name}' — detail extraction failed",
            location=loc,
            detail=(
                app.partial_extraction_reason
                or "Stage 2b extraction failed — skeleton data only."
            ),
        ))

    # Persona flags
    for persona in app.personas:
        for flag in persona.flags:
            role_label = persona.role or "unnamed persona"
            items.append(_make_item(
                flag=flag,
                label=f"{app_name} · Persona '{role_label}'",
                location=loc,
                detail=(
                    f"Confirm the '{role_label}' persona on '{app_name}'. "
                    f"{long_explanation(flag)}"
                ),
            ))

    # Integration flags
    for integration in app.integrations:
        for flag in integration.flags:
            sys_label = integration.system_name or "unnamed system"
            items.append(_make_item(
                flag=flag,
                label=f"{app_name} · Integration with '{sys_label}'",
                location=loc,
                detail=(
                    f"Confirm how '{app_name}' integrates with '{sys_label}'. "
                    f"{long_explanation(flag)}"
                ),
            ))

    # NFR flags — NFRs carries a bag `flags: list[VegaFlag]`. The flag bag
    # doesn't carry per-field attribution, so we list every NFR field that
    # is currently None so the user knows exactly which aspects to clarify.
    if app.nfrs.flags:
        missing_fields = [
            label for attr, label in _NFR_FIELD_LABELS.items()
            if getattr(app.nfrs, attr, None) in (None, "")
        ]
        missing_str = ", ".join(missing_fields) if missing_fields else (
            "non-functional requirements"
        )
        for flag in app.nfrs.flags:
            items.append(_make_item(
                flag=flag,
                label=f"{app_name} · NFR follow-up needed",
                location=loc,
                detail=(
                    f"For '{app_name}', confirm: {missing_str}. "
                    f"{long_explanation(flag)}"
                ),
            ))

    # Scale profile flag — single Optional[VegaFlag]
    if app.scale_profile.flag is not None:
        items.append(_make_item(
            flag=app.scale_profile.flag,
            label=f"{app_name} · Scale profile",
            location=loc,
            detail=(
                f"Confirm expected scale (users / volume / peak) for '{app_name}'. "
                f"{long_explanation(app.scale_profile.flag)}"
            ),
        ))

    return items


def aggregate_open_items(
    workflows: list[Workflow],
    apps: list[App],
    session_flags: SessionFlags,
) -> list[OpenItem]:
    """Stage 4 — walk every flagged field and emit one OpenItem per flag.

    Covers:
      - session_flags.tech_contact_flag / engineering_capacity_flag / it_infosec_gate_flag
      - Workflow.acceptance_criteria[*].flag
      - Workflow.persona_overrides[*].flags[*]
      - Workflow.partial_extraction_failed
      - App.personas[*].flags[*]
      - App.integrations[*].flags[*]
      - App.nfrs.flags[*]
      - App.scale_profile.flag
      - App.partial_extraction_failed

    Returns items sorted by:
      1. is_blocking_for_handoff(flag) DESC  — blocking items first
      2. location ASC
      3. label ASC
    """
    items: list[OpenItem] = []

    items.extend(_open_items_from_session(session_flags))

    for workflow in workflows:
        items.extend(_open_items_from_workflow(workflow))

    for app in apps:
        items.extend(_open_items_from_app(app))

    items.sort(key=lambda item: (
        not is_blocking_for_handoff(item.flag),  # False sorts before True, so blocking first
        item.location,
        item.label,
    ))

    return items
