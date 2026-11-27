"""
RBAC / Policy / Audit reconciliation service for ingested apps.

After initial ingestion, the dev team makes code changes that may add new
fields, entities, or events. This service detects those additions by diffing
the runtime field_registry against the persisted RBAC permissions, policy
targets, and mapping file. Missing artifacts are auto-generated; removed
artifacts are flagged for human review (additive-only — never destructive).

Idempotent: running reconcile twice produces the same result.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from database import get_db
from db.session import get_sessionmaker
from repositories.permissions import PermissionsRepository
from repositories.policies import PolicyTargetRepository
from services import field_registry, permission_registry
from services.ingestion import rbac_generator
from services.ingestion import repository as repo
from services.ingestion import service
from services.ingestion.models import AppIngestionLog, PipelineStage
from services.permission_registry import SOURCE_RECONCILE_AUTO

logger = logging.getLogger(__name__)


# Fields whose presence implies extra permissions need to exist for an entity.
_STATUS_FIELD_NAMES = ("status", "state")
_ASSIGNEE_FIELD_NAMES = ("assignee_id", "assigned_to", "owner_id")


def _is_status_field(field_name: str) -> bool:
    return field_name in _STATUS_FIELD_NAMES


def _is_assignee_field(field_name: str) -> bool:
    return field_name in _ASSIGNEE_FIELD_NAMES


def _build_synthetic_catalogue(
    entity_name: str,
    runtime_fields: list[dict],
    runtime_events: list[str],
) -> dict:
    """Construct a catalogue-shaped dict suitable for rbac_generator.

    rbac_generator.generate_permissions consumes catalogues with
    entity_name, fields, and api_routes (used to detect operations).
    Reconciliation doesn't have api_routes in the runtime registry,
    so synthesize the full CRUD route set — every newly-detected entity
    needs the standard view/create/edit/delete permissions, plus the
    conditional change_status / assign perms when applicable.
    """
    return {
        "entity_name": entity_name,
        "fields": runtime_fields,
        "api_routes": [
            {"operation": "list"},
            {"operation": "get"},
            {"operation": "create"},
            {"operation": "update"},
            {"operation": "delete"},
        ],
    }


def _read_mapping_file(app_slug: str) -> Optional[dict]:
    """Read the mapping file from disk. Returns None when absent."""
    mapping_path = service.MAPPINGS_DIR / f"{app_slug}.json"
    if not mapping_path.exists():
        return None
    try:
        with mapping_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Failed to read mapping file {mapping_path}: {exc}")
        return None


async def _ensure_mapping(app_id: str, app_slug: str) -> dict:
    """Read the mapping file, generating it first if it doesn't exist."""
    mapping = _read_mapping_file(app_slug)
    if mapping is None:
        await service.generate_mapping_file(app_id)
        mapping = _read_mapping_file(app_slug) or {"entities": {}}
    return mapping


async def reconcile_app(
    app_id: str,
    performed_by: str,
    dry_run: bool = False,
) -> dict:
    """Reconcile RBAC, policy targets, and mapping file for an ingested app.

    Compares runtime field_registry state against persisted state. Adds
    missing permissions, policy targets, and refreshes the mapping. Flags
    removed fields for human review without deleting anything.

    Args:
        app_id: The ingestion registry ID.
        performed_by: User ID for audit trail.
        dry_run: If True, compute the diff but don't apply changes.

    Returns:
        Reconciliation report (see contract in wave plan).
    """
    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")

    app_slug = app["app_slug"]
    app_display_name = app.get("name", app_slug)

    # ---- Snapshot runtime state -------------------------------------------------
    runtime_entities = field_registry.get_entities_for_module(app_slug)
    runtime_state: dict = {}
    for entity in runtime_entities:
        fields = field_registry.get_fields(app_slug, entity)
        events = field_registry.get_module_events(app_slug, entity)
        runtime_state[entity] = {
            "fields": fields,
            "field_set": {f["name"] for f in fields if isinstance(f, dict) and f.get("name")},
            "events": list(events),
            "status_field": next(
                (f["name"] for f in fields if isinstance(f, dict) and _is_status_field(f.get("name", ""))),
                None,
            ),
            "assignee_field": next(
                (f["name"] for f in fields if isinstance(f, dict) and _is_assignee_field(f.get("name", ""))),
                None,
            ),
        }

    # ---- Snapshot persisted state ----------------------------------------------
    catalogues = await repo.get_field_catalogues(app_id)
    catalogue_state: dict = {}
    for cat in catalogues:
        entity_name = cat.get("entity_name")
        if not entity_name:
            continue
        fields = cat.get("fields") or []
        catalogue_state[entity_name] = {
            "fields": fields,
            "field_set": {f["name"] for f in fields if isinstance(f, dict) and f.get("name")},
            "enums": cat.get("enums") or [],
            # module_name is what policy_targets was registered under at
            # ingestion time. Fall back to entity_name if absent (matches the
            # analyzer's convention).
            "module_name": cat.get("module_name") or entity_name,
        }

    mapping = await _ensure_mapping(app_id, app_slug)
    mapping_entities = mapping.get("entities", {}) if isinstance(mapping, dict) else {}

    db = get_db()
    async with get_sessionmaker()() as pg:
        # Pull both active and manual_override permissions. is_active=False
        # alone would let manual_override re-add itself via the diff; we want
        # to count any persisted key (regardless of active state) as "already
        # persisted" so the registry is the source of truth for what exists.
        persisted_perm_keys = await PermissionsRepository(pg).list_keys_for_app(
            app_id, only_active=False
        )

        # policy_targets are stored with module = the catalogue's
        # `module_name` (which equals `entity_name` per the analyzer's
        # convention — see service.py). Reading `module` here lets us match
        # what's already registered without assuming app_slug.
        # NOTE: the policy_targets table's `app_id` column actually stores the
        # app_slug, not the app's `app_id`. This is a pre-existing convention
        # from the ingestion pipeline (see service.py where targets are
        # written) and is load-bearing for the reader. If the convention is
        # ever corrected, the writer in service.py and this reader must change
        # together.
        persisted_targets = {
            (t.module, t.entity, t.event)
            for t in await PolicyTargetRepository(pg).list_active_targets(app_slug=app_slug)
        }

    # ---- Diff --------------------------------------------------------------------
    changes: dict = {
        "permissions_added": [],
        "policy_targets_added": [],
        "fields_added": [],
        "fields_removed_flagged": [],
        "enums_changed": [],
        "mapping_updated": False,
    }

    new_entity_catalogues: list[dict] = []
    # Existing entities that gained a status or assignee field — they need the
    # conditional permissions (`change_status`, `assign`) regenerated. Same
    # synthetic-catalogue shape as new entities; keyed separately so we can
    # report them under the right entity in the diff.
    upgraded_entity_catalogues: list[dict] = []
    # (module, entity, events). module follows the pipeline convention from
    # service.py:472,510 — module_name from the catalogue, which the analyzer
    # sets equal to entity_name. For new entities (no catalogue), use entity
    # name directly so policy_targets stored by reconcile match what the
    # pipeline writes for fresh ingestions.
    new_target_specs: list[tuple[str, str, list[str]]] = []

    runtime_entity_set = set(runtime_state.keys())
    catalogue_entity_set = set(catalogue_state.keys())

    # New entities — fully missing from the persisted catalogue.
    for entity in sorted(runtime_entity_set - catalogue_entity_set):
        rt = runtime_state[entity]
        synthetic = _build_synthetic_catalogue(entity, rt["fields"], rt["events"])
        new_entity_catalogues.append(synthetic)
        # Mark every runtime field as "added" for the diff report.
        for field in rt["fields"]:
            if not isinstance(field, dict) or not field.get("name"):
                continue
            changes["fields_added"].append({
                "entity": entity,
                "field": field["name"],
                "type": field.get("type", "string"),
            })
        events = rt["events"] or _default_events_for(rt)
        if events:
            new_target_specs.append((entity, entity, events))

    # Existing entities — diff field-by-field and event-by-event.
    for entity in sorted(runtime_entity_set & catalogue_entity_set):
        rt = runtime_state[entity]
        cat = catalogue_state[entity]

        # New fields (in runtime, not in catalogue).
        gained_status_or_assignee = False
        for field in rt["fields"]:
            if not isinstance(field, dict):
                continue
            name = field.get("name")
            if not name or name in cat["field_set"]:
                continue
            changes["fields_added"].append({
                "entity": entity,
                "field": name,
                "type": field.get("type", "string"),
            })
            if _is_status_field(name) or _is_assignee_field(name):
                gained_status_or_assignee = True

        # If an entity gained a status/assignee field, regenerate its full
        # permission set so the conditional `.change_status` / `.assign` perms
        # land. The generator is idempotent — already-persisted keys are
        # filtered out below by `persisted_perm_keys`.
        if gained_status_or_assignee:
            upgraded_entity_catalogues.append(
                _build_synthetic_catalogue(entity, rt["fields"], rt["events"])
            )

        # Removed fields (in catalogue, not in runtime). FLAG ONLY.
        for field_name in sorted(cat["field_set"] - rt["field_set"]):
            changes["fields_removed_flagged"].append({
                "entity": entity,
                "field": field_name,
                "reason": "Field is registered in catalogue but not in runtime field_registry. "
                          "Existing roles may still reference related permissions; review before removing.",
            })

        # Status enum drift.
        runtime_status = rt["status_field"]
        if runtime_status:
            mapping_entity = mapping_entities.get(entity, {}) if isinstance(mapping_entities, dict) else {}
            old_enum = list(mapping_entity.get("status_enum", []) or [])
            new_enum = _runtime_status_enum(rt["fields"], runtime_status)
            if new_enum and sorted(new_enum) != sorted(old_enum):
                changes["enums_changed"].append({
                    "entity": entity,
                    "field": runtime_status,
                    "old": old_enum,
                    "new": new_enum,
                })

        # New events not in policy_targets. Use the catalogue's module_name
        # so the (module, entity, event) tuple matches what was persisted.
        module_name = cat["module_name"]
        missing_events = [
            ev for ev in rt["events"]
            if (module_name, entity, ev) not in persisted_targets
        ]
        if missing_events:
            new_target_specs.append((module_name, entity, missing_events))

    # Generate permission proposals (so dry_run reports show them) before applying.
    # New entities need their full CRUD perm set; existing entities that gained
    # a status/assignee field need their conditional perms (`.change_status`,
    # `.assign`). The generator dedupes by key and the apply path filters out
    # already-persisted keys, so feeding both lists in is safe.
    proposed_permissions: list[dict] = []
    catalogues_for_perm_gen = new_entity_catalogues + upgraded_entity_catalogues
    if catalogues_for_perm_gen:
        proposed_permissions = rbac_generator.generate_permissions(
            app_slug, app_display_name, catalogues_for_perm_gen
        )
        # Only the entries that aren't already in the persisted set count as additions.
        # Every reconcile-generated permission is sourced from a synthetic catalogue
        # (_build_synthetic_catalogue hard-codes the full CRUD set since reconcile has
        # no api_routes input from the runtime registry). Flag these so an operator
        # can review whether the over-provisioning matches the entity's real surface.
        for perm in proposed_permissions:
            if perm.get("key") and perm["key"] not in persisted_perm_keys:
                changes["permissions_added"].append({
                    "key": perm["key"],
                    "label": perm.get("label", ""),
                    "source": SOURCE_RECONCILE_AUTO,
                    "synthesized_crud": True,
                })

    for module, entity, events in new_target_specs:
        for event in events:
            changes["policy_targets_added"].append({
                "module": module,
                "entity": entity,
                "event": event,
            })

    # If there's anything to refresh in the mapping file, mark it.
    mapping_will_update = bool(
        new_entity_catalogues
        or upgraded_entity_catalogues
        or changes["fields_added"]
        or changes["enums_changed"]
    )
    changes["mapping_updated"] = mapping_will_update and not dry_run

    summary = {
        "added": (
            len(changes["permissions_added"])
            + len(changes["policy_targets_added"])
            + len(changes["fields_added"])
        ),
        "flagged_for_review": (
            len(changes["fields_removed_flagged"]) + len(changes["enums_changed"])
        ),
        "no_change": 1 if (
            not changes["permissions_added"]
            and not changes["policy_targets_added"]
            and not changes["fields_added"]
            and not changes["fields_removed_flagged"]
            and not changes["enums_changed"]
        ) else 0,
    }

    report = {
        "app_id": app_id,
        "app_slug": app_slug,
        "reconciled_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "changes": changes,
        "summary": summary,
    }

    if dry_run:
        return report

    # ---- Apply -----------------------------------------------------------------
    # Permissions: filter to ones not already persisted, register with reconcile_auto source.
    perms_to_register = [
        p for p in proposed_permissions
        if p.get("key") and p["key"] not in persisted_perm_keys
    ]
    if perms_to_register:
        await permission_registry.register_permissions(
            perms_to_register,
            source=SOURCE_RECONCILE_AUTO,
            app_id=app_id,
        )

    # Policy targets: idempotent upsert per (app_id, module, entity, event).
    for module, entity, events in new_target_specs:
        if not events:
            continue
        await service._register_policy_targets(db, app_slug, module, entity, events)

    # Mapping refresh (best-effort — same pattern register_fields uses).
    if mapping_will_update:
        try:
            await service.generate_mapping_file(app_id)
        except Exception as exc:
            logger.warning(f"Mapping file refresh failed for {app_id}: {exc}")
            changes["mapping_updated"] = False

    # Audit trail.
    await repo.write_log(AppIngestionLog(
        app_id=app_id,
        ingestion_version=app.get("ingestion_version", 1),
        action="reconcile_applied",
        stage=PipelineStage.REGISTER_FIELDS,
        status="success",
        details={
            "summary": summary,
            "permissions_added": len(changes["permissions_added"]),
            "policy_targets_added": len(changes["policy_targets_added"]),
            "fields_added": len(changes["fields_added"]),
            "fields_removed_flagged": len(changes["fields_removed_flagged"]),
            "enums_changed": len(changes["enums_changed"]),
            "mapping_updated": changes["mapping_updated"],
        },
        performed_by=performed_by,
    ))
    await repo.log_stage(
        app_id=app_id,
        stage=PipelineStage.REGISTER_FIELDS,
        action="reconcile_applied",
        details={
            "summary": summary,
            "permissions_added": [p["key"] for p in changes["permissions_added"]],
            "policy_targets_added": len(changes["policy_targets_added"]),
            "fields_removed_flagged": [
                f"{f['entity']}.{f['field']}" for f in changes["fields_removed_flagged"]
            ],
        },
        performed_by=performed_by,
    )

    return report


def _default_events_for(runtime_entry: dict) -> list[str]:
    """Default lifecycle events for a newly-detected entity.

    If the runtime registry has no events declared for an entity (the dev
    forgot to call register_module_events), assume the standard CRUD set —
    plus status_changed when the entity has a status field.
    """
    events = ["created", "updated", "deleted"]
    if runtime_entry.get("status_field"):
        events.append("status_changed")
    return events


def _runtime_status_enum(fields: list, status_field_name: str) -> list:
    """Pull the enum option values for the runtime status field, if declared."""
    for field in fields:
        if not isinstance(field, dict) or field.get("name") != status_field_name:
            continue
        options = field.get("options") or []
        values: list = []
        for opt in options:
            if isinstance(opt, dict):
                v = opt.get("value")
                if v is not None:
                    values.append(v)
            elif isinstance(opt, str):
                values.append(opt)
        return values
    return []
