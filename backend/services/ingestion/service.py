"""
App Ingestion — Pipeline Service

Orchestrates the ingestion pipeline stages. Called by the API routes
and by the Claude Code /ingest-app skill.
"""
import asyncio
import json
import logging
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from database import get_db
from db.session import get_sessionmaker
from repositories.permissions import PermissionsRepository
from repositories.policies import PolicyTargetRepository
from services.ingestion.models import (
    AppRegistry, AppModule, IngestionArtifact, IngestionState,
    IngestionStatus, ModuleStatus, PipelineStage, ArtifactType,
    ClassificationTier, ModuleDescriptor,
    validate_slug, slug_to_app_slug,
)
from services.ingestion import repository as repo
from services.ingestion.analyzer import EmergentAnalyzer

logger = logging.getLogger(__name__)

STAGING_DIR = Path(__file__).resolve().parent.parent.parent / ".ingestion" / "staging"
MAPPINGS_DIR = Path(__file__).resolve().parent.parent.parent / ".ingestion" / "mappings"


def _pluralize(word: str) -> str:
    """Simple English pluralization. Mirrors EmergentAnalyzer._pluralize to keep
    service.py decoupled from the analyzer module."""
    if word.endswith("y") and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "sh", "ch", "x", "z")):
        return word + "es"
    return word + "s"


def _validate_clone_dir(clone_dir: str) -> Path:
    """Validate that clone_dir is within the staging directory (prevents path traversal)."""
    resolved = Path(clone_dir).resolve()
    if not resolved.is_relative_to(STAGING_DIR.resolve()):
        raise ValueError("clone_dir must be within the staging directory")
    return resolved


# ==================== STAGE 1: INITIATE ====================

async def initiate_ingestion(
    repo_url: str,
    name: str,
    slug: str,
    description: str = "",
    account_id: str = None,
    created_by: str = None,
) -> dict:
    """
    Start a new ingestion: validate slug, check for existing app,
    create registry record and initial state.
    Returns the app_registry document.
    """
    # Validate slug
    valid, error = validate_slug(slug)
    if not valid:
        raise ValueError(f"Invalid slug: {error}")

    # Check for existing app with same slug
    existing = await repo.get_app_by_slug(slug)
    if existing:
        raise ValueError(f"App with slug '{slug}' already exists (app_id: {existing['app_id']})")

    # Check for existing app with same repo URL (might be a re-ingestion)
    existing_repo = await repo.get_app_by_repo_url(repo_url)
    if existing_repo:
        raise ValueError(
            f"This repo has already been ingested as '{existing_repo['name']}' "
            f"(app_id: {existing_repo['app_id']}). Use /reingest-app instead."
        )

    app_slug = slug_to_app_slug(slug)

    # Create app registry record
    app = AppRegistry(
        name=name,
        slug=slug,
        app_slug=app_slug,
        description=description,
        source_repo_url=repo_url,
        account_id=account_id,
        status=IngestionStatus.PENDING,
        created_by=created_by,
    )
    await repo.create_app_registry(app)

    # Create initial pipeline state
    state = IngestionState(
        app_id=app.app_id,
        current_stage=PipelineStage.INVOKE,
    )
    await repo.create_or_update_state(state)

    # Log initiation
    await repo.log_stage(
        app_id=app.app_id,
        stage=PipelineStage.INVOKE,
        action="ingestion_initiated",
        details={"repo_url": repo_url, "slug": slug, "name": name},
        performed_by=created_by,
    )

    return app.model_dump()


# ==================== STAGE 2: CLONE ====================

async def clone_repo(app_id: str) -> dict:
    """
    Clone the GitHub repo to a local staging directory.
    Returns clone info including the local path.
    """
    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")

    repo_url = app["source_repo_url"]
    if not repo_url.startswith(("https://", "http://")):
        raise ValueError("repo_url must use https:// or http:// scheme")

    slug = app["slug"]
    clone_dir = STAGING_DIR / slug

    # Ensure staging directory exists
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    # Clean up any existing staging directory for this app
    if clone_dir.exists():
        shutil.rmtree(clone_dir)

    await repo.update_app_status(app_id, IngestionStatus.ANALYZING)
    start = time.time()

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", repo_url, str(clone_dir),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(f"git clone failed: {stderr.decode()}")

        # Get commit SHA
        sha_proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(clone_dir),
        )
        sha_stdout, _ = await sha_proc.communicate()
        sha_result_code = sha_proc.returncode
        commit_sha = sha_stdout.decode().strip() if sha_result_code == 0 else None

        # Update app with commit SHA
        if commit_sha:
            await repo.update_app_status(
                app_id, IngestionStatus.ANALYZING,
                source_commit_sha=commit_sha,
            )

        duration_ms = int((time.time() - start) * 1000)
        await repo.log_stage(
            app_id=app_id,
            stage=PipelineStage.CLONE,
            action="repo_cloned",
            details={"clone_dir": str(clone_dir), "commit_sha": commit_sha},
            duration_ms=duration_ms,
        )

        await repo.update_stage(app_id, PipelineStage.CLONE, {
            "status": "success", "clone_dir": str(clone_dir), "commit_sha": commit_sha,
        })

        return {"clone_dir": str(clone_dir), "commit_sha": commit_sha}

    except Exception as e:
        await repo.log_stage(
            app_id=app_id,
            stage=PipelineStage.CLONE,
            action="clone_failed",
            status="failed",
            details={"error": str(e)},
        )
        await repo.update_app_status(app_id, IngestionStatus.FAILED)
        raise


# ==================== STAGE 3-5: ANALYZE & CLASSIFY ====================

async def analyze_and_classify(app_id: str, clone_dir: str) -> dict:
    """
    Run the Emergent analyzer on the cloned repo.
    Extracts models, routes, enums, entities, and classifies components.
    Returns the analysis summary.
    """
    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")

    _validate_clone_dir(clone_dir)
    start = time.time()

    try:
        # Run analyzer
        analyzer = EmergentAnalyzer(clone_dir)
        summary = analyzer.analyze()

        # Store emergent metadata if found
        if summary.get("emergent_metadata"):
            meta = summary["emergent_metadata"]
            await repo.update_app_status(
                app_id, IngestionStatus.ANALYZING,
                emergent_job_id=meta.get("job_id"),
                emergent_image=meta.get("env_image_name"),
            )

        # Store classification result as artifact
        await repo.store_artifact(IngestionArtifact(
            app_id=app_id,
            artifact_type=ArtifactType.CLASSIFICATION_RESULT,
            data=summary,
        ))

        duration_ms = int((time.time() - start) * 1000)

        # Log analysis completion
        await repo.log_stage(
            app_id=app_id,
            stage=PipelineStage.ANALYZE,
            action="analysis_complete",
            details={
                "entities_found": len(summary["entities"]),
                "routes_found": summary["backend"]["total_routes"],
                "models_found": summary["backend"]["total_models"],
                "pages_found": summary["frontend"]["total_pages"],
                "infra_components": len(summary["classification"]["infrastructure"]),
                "app_components": len(summary["classification"]["app_logic"]),
                "review_components": len(summary["classification"]["needs_review"]),
            },
            duration_ms=duration_ms,
        )

        await repo.update_stage(app_id, PipelineStage.ANALYZE, {"status": "success"})
        await repo.update_stage(app_id, PipelineStage.CLASSIFY, {"status": "success"})
        await repo.update_app_status(app_id, IngestionStatus.AWAITING_REVIEW)

        return summary

    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        await repo.log_stage(
            app_id=app_id,
            stage=PipelineStage.ANALYZE,
            action="analysis_failed",
            status="failed",
            details={"error": str(e)},
            duration_ms=duration_ms,
        )
        await repo.update_stage(app_id, PipelineStage.ANALYZE, {"status": "failed", "error": str(e)})
        await repo.update_app_status(app_id, IngestionStatus.FAILED)
        raise


# ==================== STAGE 6: RECORD DEVELOPER DECISIONS ====================

async def record_developer_decisions(
    app_id: str,
    selected_modules: list[dict],
    discarded_modules: list[dict],
    import_plan: dict,
) -> dict:
    """
    Record the developer's decisions from the interview phase.
    selected_modules: [{name, entity_name, replaces_module, ui_placement, icon, label}]
    discarded_modules: [{name, reason}]
    import_plan: full plan dict
    """
    # Store developer responses as artifact
    await repo.store_artifact(IngestionArtifact(
        app_id=app_id,
        artifact_type=ArtifactType.DEVELOPER_RESPONSES,
        data={
            "selected_modules": selected_modules,
            "discarded_modules": discarded_modules,
        },
    ))

    # Store import plan
    await repo.store_artifact(IngestionArtifact(
        app_id=app_id,
        artifact_type=ArtifactType.IMPORT_PLAN,
        data=import_plan,
    ))

    # Update app with module lists
    module_descriptors = [
        ModuleDescriptor(**m).model_dump() for m in selected_modules
    ]
    await repo.update_app_modules_list(app_id, module_descriptors, discarded_modules)

    # Log
    await repo.log_stage(
        app_id=app_id,
        stage=PipelineStage.INTERVIEW,
        action="developer_decisions_recorded",
        details={
            "modules_selected": len(selected_modules),
            "modules_discarded": len(discarded_modules),
        },
    )

    await repo.update_stage(app_id, PipelineStage.INTERVIEW, {"status": "success"})
    await repo.update_stage(app_id, PipelineStage.PLAN, {"status": "success"})
    await repo.update_stage(app_id, PipelineStage.CONFIRM, {"status": "success"})

    return {"modules_selected": len(selected_modules), "modules_discarded": len(discarded_modules)}


# ==================== STAGE 13: EXTRACT FIELD CATALOGUE ====================

async def extract_field_catalogue(app_id: str, clone_dir: str, selected_entities: list[str] = None) -> list[dict]:
    """
    Extract field catalogues from the analyzed source code.
    Returns list of catalogue documents.
    """
    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")

    _validate_clone_dir(clone_dir)
    await repo.update_app_status(app_id, IngestionStatus.EXTRACTING)
    start = time.time()

    try:
        # Use the clone dir to extract field catalogues
        analyzer = EmergentAnalyzer(clone_dir)
        analyzer.analyze()  # Required to populate internal state for get_field_catalogues

        # Generate catalogues
        catalogues = analyzer.get_field_catalogues(app_id, selected_entities)

        # Store each catalogue
        stored = []
        for catalogue in catalogues:
            await repo.store_field_catalogue(catalogue)
            stored.append(catalogue.model_dump())

        duration_ms = int((time.time() - start) * 1000)
        await repo.log_stage(
            app_id=app_id,
            stage=PipelineStage.EXTRACT,
            action="field_catalogue_extracted",
            details={
                "catalogues_created": len(stored),
                "entities": [c["entity_name"] for c in stored],
                "total_fields": sum(len(c["fields"]) for c in stored),
            },
            duration_ms=duration_ms,
        )

        await repo.update_stage(app_id, PipelineStage.EXTRACT, {"status": "success"})
        return stored

    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        await repo.log_stage(
            app_id=app_id,
            stage=PipelineStage.EXTRACT,
            action="extraction_failed",
            status="failed",
            details={"error": str(e)},
            duration_ms=duration_ms,
        )
        await repo.update_stage(app_id, PipelineStage.EXTRACT, {"status": "failed", "error": str(e)})
        await repo.update_app_status(app_id, IngestionStatus.FAILED)
        raise


# ==================== STAGE 14-15: REGISTER FIELDS & POLICIES ====================

# policy_targets (Phase 2b) is a Postgres table keyed on
# (app_id, module, entity, event) with (app_id, is_active) and
# (module, entity, is_active) indexes — all created by Alembic
# 0005_platform_core_rbac, not lazily here. NOTE: `app_id` here is the app
# SLUG the caller passes (load-bearing; ingestion/reconcile.py reads it so).


async def _register_policy_targets(
    db, app_id: str, module: str, entity: str, events: list[str]
) -> None:
    """Upsert one policy_targets row per (app_id, module, entity, event).
    Idempotent. Marks active=True. Caller is responsible for calling
    _deactivate_policy_targets first on re-ingestion. ``db`` is accepted
    for caller compatibility and ignored (the table is Postgres)."""
    async with get_sessionmaker()() as pg:
        await PolicyTargetRepository(pg).upsert_targets(app_id, module, entity, events)
        await pg.commit()


async def _deactivate_policy_targets(db, app_id: str) -> int:
    """Mark every policy_target for this app as is_active=False before
    re-registering. Re-registering then flips them back to True for any
    (module, entity, event) still in the current catalogue. Stale ones
    remain is_active=False.
    Returns count of deactivated rows. ``db`` is accepted and ignored."""
    async with get_sessionmaker()() as pg:
        n = await PolicyTargetRepository(pg).deactivate_for_app(app_id)
        await pg.commit()
    return n


async def register_fields_from_catalogue(app_id: str) -> dict:
    """
    Read the stored field catalogues and register all fields into
    AEGISAXIOM's field_registry. Also creates app_modules records.
    """
    from services.field_registry import register_fields, register_module_events, register_module_config

    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")

    app_slug = app.get("app_slug", slug_to_app_slug(app["slug"]))
    slug = app["slug"]

    await repo.update_app_status(app_id, IngestionStatus.REGISTERING)
    start = time.time()

    # Deactivate any existing policy_targets for this app before re-registering.
    # Targets that still exist in the new catalogue will be flipped back to
    # is_active=True below; stale ones remain is_active=False.
    db = get_db()
    deactivated_targets = await _deactivate_policy_targets(db, app_slug)
    registered_targets = 0

    try:
        catalogues = await repo.get_field_catalogues(app_id)
        registered_modules = []

        for cat in catalogues:
            module_name = cat["module_name"]
            entity_name = cat["entity_name"]
            fields = cat["fields"]

            # Convert field dicts to the format register_fields expects
            field_defs = []
            for f in fields:
                field_def = {
                    "name": f["name"],
                    "type": f["type"],
                    "label": f["label"],
                    "description": f.get("description", ""),
                }
                if f.get("computed"):
                    field_def["computed"] = True
                if f.get("options"):
                    field_def["options"] = f["options"]
                field_defs.append(field_def)

            # Register with AEGISAXIOM field registry
            register_fields(app_slug, entity_name, field_defs)

            # Register module config
            collection_name = f"{app_slug}_{cat.get('entity_name', module_name)}"
            register_module_config(app_slug, collection_name, f"{entity_name}_id", "Custom")

            # Register events
            events = ["created", "updated", "deleted"]
            # Add status_changed if entity has a status field
            has_status = any(f["name"] == "status" for f in fields)
            if has_status:
                events.append("status_changed")
            register_module_events(app_slug, entity_name, events)

            # Register policy_targets so admins can author policies against
            # this entity's events. Uses app_slug as the stable app identifier
            # for downstream policy target consumers (admin UI, policy engine).
            await _register_policy_targets(
                db, app_slug, module_name, entity_name, events
            )
            registered_targets += len(events)

            # Create app_module record
            module = AppModule(
                app_id=app_id,
                name=module_name,
                entity_name=entity_name,
                permission_prefix=f"{app_slug}.{module_name}",
                route_prefix=f"/api/{slug}/{module_name}",
                status=ModuleStatus.ACTIVE,
                field_count=len(field_defs),
            )

            # Check if developer specified replacement or UI placement
            for imported in app.get("modules_imported", []):
                if isinstance(imported, dict) and imported.get("name") == module_name:
                    module.replaces_module = imported.get("replaces_module")
                    module.ui_placement = imported.get("ui_placement", "portal_tab")
                    break

            await repo.create_app_module(module)
            registered_modules.append({
                "module_id": module.module_id,
                "module_name": module_name,
                "entity_name": entity_name,
                "field_count": len(field_defs),
                "permission_prefix": module.permission_prefix,
                "route_prefix": module.route_prefix,
            })

        duration_ms = int((time.time() - start) * 1000)

        await repo.log_stage(
            app_id=app_id,
            stage=PipelineStage.REGISTER_FIELDS,
            action="field_registration_complete",
            details={
                "modules_registered": len(registered_modules),
                "total_fields": sum(m["field_count"] for m in registered_modules),
                "policy_targets_registered": registered_targets,
                "policy_targets_deactivated": deactivated_targets,
            },
            duration_ms=duration_ms,
        )

        await repo.log_stage(
            app_id=app_id,
            stage=PipelineStage.REGISTER_POLICIES,
            action="policy_targets_registered",
            details={
                "modules": [m["module_name"] for m in registered_modules],
                "policy_targets_registered": registered_targets,
                "policy_targets_deactivated": deactivated_targets,
            },
        )

        await repo.update_stage(app_id, PipelineStage.REGISTER_FIELDS, {"status": "success"})
        await repo.update_stage(app_id, PipelineStage.REGISTER_POLICIES, {"status": "success"})
        await repo.update_app_status(app_id, IngestionStatus.CONFIGURED)

        # Auto-generate the adaptation mapping file. This is best-effort —
        # registration is the source of truth, and a missing mapping file
        # should not fail the registration step.
        try:
            await generate_mapping_file(app_id)
        except Exception as exc:
            logger.warning(f"Mapping file generation failed for {app_id}: {exc}")

        return {
            "modules_registered": registered_modules,
            "total_fields": sum(m["field_count"] for m in registered_modules),
        }

    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        await repo.log_stage(
            app_id=app_id,
            stage=PipelineStage.REGISTER_FIELDS,
            action="registration_failed",
            status="failed",
            details={"error": str(e)},
            duration_ms=duration_ms,
        )
        await repo.update_stage(app_id, PipelineStage.REGISTER_FIELDS, {"status": "failed", "error": str(e)})
        await repo.update_app_status(app_id, IngestionStatus.FAILED)
        raise


# ==================== STAGE 15.5: GENERATE ADAPTATION MAPPING FILE ====================


async def generate_mapping_file(app_id: str) -> dict:
    """
    Build and persist the adaptation mapping file at
    .ingestion/mappings/<app_slug>.json.

    This file is the contract used by /reingest-app to detect deltas between
    the source app and the imported representation in AEGISAXIOM. It is idempotent:
    running it twice produces the same file (modulo `generated_at`).
    """
    data = await repo.get_mapping_data(app_id)
    if data is None:
        raise ValueError(f"App {app_id} not found")

    app = data["app"]
    catalogues = data["catalogues"]
    app_slug = app["app_slug"]

    entities_dict: dict = {}
    for cat in catalogues:
        entity_name = cat["entity_name"]
        fields = cat.get("fields") or []
        enums = cat.get("enums") or []

        # Build the fields dict: {name: {type, source_name}}
        fields_dict = {}
        for f in fields:
            source_name = f.get("source_field_name") or f["name"]
            fields_dict[f["name"]] = {
                "type": f["type"],
                "source_name": source_name,
            }

        # Identify the status field (status or state) and find its enum values.
        status_field_name = None
        for f in fields:
            if f["name"] in ("status", "state"):
                status_field_name = f["name"]
                break

        status_enum: list = []
        if status_field_name:
            for enum in enums:
                if enum.get("field_name") == status_field_name:
                    status_enum = enum.get("values") or []
                    break

        # Events emitted for this entity.
        events = [
            f"{entity_name}_created",
            f"{entity_name}_updated",
            f"{entity_name}_deleted",
        ]
        if status_field_name:
            events.append(f"{entity_name}_status_changed")

        entities_dict[entity_name] = {
            "collection": f"{app_slug}_{_pluralize(entity_name)}",
            "id_field": f"{entity_name}_id",
            "fields": fields_dict,
            "status_enum": status_enum,
            "events": events,
        }

    # The contract file is intentionally content-addressable — same source data
    # produces the same file bytes. /reingest-app diffs this file to detect what
    # changed since the last ingestion, so a moving timestamp here would always
    # register a delta even when nothing changed. The generation timestamp is
    # written to a sidecar instead.
    mapping = {
        "app_slug": app_slug,
        "source_commit_sha": app.get("source_commit_sha", "") or "",
        "ingestion_version": app.get("ingestion_version", 1),
        "entities": entities_dict,
    }

    # Persist to disk. Idempotent — overwrites existing file.
    MAPPINGS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MAPPINGS_DIR / f"{app_slug}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, default=str)

    # Sidecar metadata — generation timestamp and any other run-specific data
    # that shouldn't be part of the content-addressable contract.
    meta_path = MAPPINGS_DIR / f"{app_slug}.meta.json"
    meta = {
        "app_slug": app_slug,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit_sha": app.get("source_commit_sha", "") or "",
        "ingestion_version": app.get("ingestion_version", 1),
    }
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, default=str)

    await repo.log_stage(
        app_id=app_id,
        stage=PipelineStage.REGISTER_FIELDS,
        action="mapping_file_generated",
        details={
            "path": str(out_path),
            "entity_count": len(entities_dict),
            "app_slug": app_slug,
        },
    )

    return mapping


# ==================== STAGE 22: ACTIVATE ====================

async def activate_app(app_id: str) -> dict:
    """Flip an app from configured to active."""
    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")

    if app["status"] != IngestionStatus.CONFIGURED.value:
        raise ValueError(f"App must be in 'configured' status to activate (current: {app['status']})")

    await repo.update_app_status(app_id, IngestionStatus.ACTIVE)
    await repo.log_stage(
        app_id=app_id,
        stage=PipelineStage.ACTIVATE,
        action="app_activated",
        details={"previous_status": app["status"]},
    )

    return {"app_id": app_id, "status": "active"}


# ==================== STATUS ====================

async def _compute_actual_counts(app_id: str, app_slug: str, catalogues: list) -> dict:
    """Compute live registration counts from field_registry, permissions DB, and catalogues.

    Returns:
        {
            "entities": int,       # entities live in field_registry for this app_slug
            "fields": int,         # total fields across all entities in field_registry
            "routes": int,         # total routes from catalogues (best available source)
            "permissions": int,    # active permissions stored in DB for this app
            "modules": int,        # count of registered app_modules documents
        }
    """
    from services.field_registry import get_entities_for_module, get_fields

    # Live entity + field counts from the in-process field_registry (populated on import).
    live_entities = get_entities_for_module(app_slug)
    live_field_count = sum(len(get_fields(app_slug, e)) for e in live_entities)

    # If the field_registry hasn't been hydrated yet (e.g., first load before routes import),
    # fall back to catalogue counts which are always available from MongoDB.
    if not live_entities and catalogues:
        live_entities_count = len(catalogues)
        live_field_count = sum(len(c.get("fields", [])) for c in catalogues)
    else:
        live_entities_count = len(live_entities)

    # Routes come from catalogues (most reliable; field_registry doesn't store routes).
    total_routes = sum(len(c.get("api_routes", [])) for c in catalogues)

    # Permission count: the Postgres permissions registry by app_id (ingested perms carry app_id).
    async with get_sessionmaker()() as pg:
        perm_count = await PermissionsRepository(pg).count_active_for_app(app_id)
    db = get_db()

    # Module count from app_modules collection.
    module_count = await db.app_modules.count_documents({"app_id": app_id})

    return {
        "entities": live_entities_count,
        "fields": live_field_count,
        "routes": total_routes,
        "permissions": perm_count,
        "modules": module_count,
    }


def _compute_available_actions(status: str) -> list:
    """Return the list of available action identifiers for a given ingestion status."""
    if status == IngestionStatus.AWAITING_REVIEW.value:
        return ["approve", "reject"]
    if status == IngestionStatus.CONFIGURED.value:
        return ["activate"]
    if status == IngestionStatus.FAILED.value:
        return ["rerun_step"]
    if status == IngestionStatus.ACTIVE.value:
        return []
    return []


async def get_ingestion_status(app_id: str) -> dict:
    """Get comprehensive status for an ingestion — demo-ready."""
    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")

    app_slug = app.get("app_slug") or slug_to_app_slug(app.get("slug", ""))
    app_status = app.get("status", "")

    state = await repo.get_ingestion_state(app_id)
    modules = await repo.get_app_modules(app_id)
    logs = await repo.get_ingestion_logs(app_id)
    catalogues = await repo.get_field_catalogues(app_id)

    # Build pipeline progress — which stages are done, current, pending
    all_stages = [s.value for s in PipelineStage]
    stage_results = state.get("stage_results", {}) if state else {}
    current = state.get("current_stage") if state else None

    pipeline = []
    for stage_name in all_stages:
        result = stage_results.get(stage_name)
        if result:
            stage_status = result.get("status", "success")
        elif stage_name == current:
            stage_status = "in_progress"
        elif current and all_stages.index(stage_name) < all_stages.index(current):
            stage_status = "success"
        else:
            stage_status = "pending"
        pipeline.append({"stage": stage_name, "status": stage_status})

    # Aggregate stats (catalogue-based — used for legacy summary field)
    total_fields = sum(len(c.get("fields", [])) for c in catalogues)
    total_entities = len(catalogues)
    total_routes = sum(len(c.get("api_routes", [])) for c in catalogues)

    # Live counts from registries (authoritative for the UI stat cards)
    actual_counts = await _compute_actual_counts(app_id, app_slug, catalogues)

    # Build registered_modules list enriched with live counts.
    # Source: app_modules documents from DB, enriched with field_registry live counts.
    from services.field_registry import get_fields, get_entities_for_module
    live_entities = get_entities_for_module(app_slug)

    registered_modules_enriched = []
    for mod in modules:
        entity_name = mod.get("entity_name", "")
        module_name = mod.get("name", "")

        # Field count: prefer live registry; fall back to stored field_count on the module doc.
        if entity_name in live_entities:
            live_field_count = len(get_fields(app_slug, entity_name))
        else:
            live_field_count = mod.get("field_count", 0)

        # Route count for this entity: count from catalogues.
        entity_route_count = 0
        for cat in catalogues:
            if cat.get("entity_name") == entity_name or cat.get("module_name") == module_name:
                entity_route_count = len(cat.get("api_routes", []))
                break

        # Permission count for this module's prefix: query permissions where key starts with prefix.
        perm_prefix = mod.get("permission_prefix", "")
        if perm_prefix:
            # Former anchored $regex -> LIKE prefix (trigram-indexed key).
            async with get_sessionmaker()() as pg:
                module_perm_count = await PermissionsRepository(pg).count_active_for_app(
                    app_id, key_prefix=perm_prefix
                )
        else:
            module_perm_count = 0

        registered_modules_enriched.append({
            "module_id": mod.get("module_id"),
            "name": module_name,
            "entity_name": entity_name,
            "entity_count": 1,
            "field_count": live_field_count,
            "route_count": entity_route_count,
            "permission_count": module_perm_count,
            "route_prefix": mod.get("route_prefix", ""),
            "permission_prefix": perm_prefix,
            "status": mod.get("status", "active"),
        })

    # Annotate logs: for entries that have been resolved, include resolution context.
    # A resolved entry has a `resolved_by` field pointing to a later log_id.
    annotated_logs = []
    for log in logs:
        annotated = dict(log)
        # resolved_by may have been written by the rerun-step endpoint
        # (already stored on the document); pass it through unchanged.
        annotated_logs.append(annotated)

    return {
        "app": {
            "app_id": app.get("app_id"),
            "name": app.get("name"),
            "slug": app.get("slug"),
            "app_slug": app_slug,
            "description": app.get("description"),
            "status": app_status,
            "source_repo_url": app.get("source_repo_url"),
            "source_commit_sha": app.get("source_commit_sha"),
            "ingestion_version": app.get("ingestion_version"),
            "created_at": app.get("created_at"),
        },
        "pipeline": pipeline,
        "current_stage": current,
        "summary": {
            "entities_extracted": total_entities,
            "fields_catalogued": total_fields,
            "routes_mapped": total_routes,
            "modules_registered": len(modules),
            "permissions_generated": len(app.get("generated_permissions", [])),
            "log_entries": len(logs),
        },
        # Live counts for UI stat cards (Contract 6)
        "actual_counts": actual_counts,
        # Enriched module list for the Registered Modules panel
        "registered_modules": registered_modules_enriched,
        # Actionable status mirrors app status; drives button visibility on frontend
        "actionable_status": app_status,
        # Which actions are valid right now
        "available_actions": _compute_available_actions(app_status),
        "modules": modules,
        "recent_logs": annotated_logs[-10:] if annotated_logs else [],
        "all_logs": annotated_logs,
    }


# ==================== REVIEW ACTIONS ====================

async def approve_ingestion(app_id: str, performed_by: str) -> dict:
    """Transition an ingestion from awaiting_review → approved (configured).

    Returns the updated status dict or raises ValueError if the transition
    is not valid for the current state.
    """
    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")

    current_status = app.get("status")
    if current_status != IngestionStatus.AWAITING_REVIEW.value:
        raise ValueError(
            f"App must be in 'awaiting_review' status to approve "
            f"(current: {current_status})"
        )

    success = await repo.transition_app_status(
        app_id,
        from_status=IngestionStatus.AWAITING_REVIEW.value,
        to_status=IngestionStatus.CONFIGURED.value,
        approved_by=performed_by,
        approved_at=datetime.now(timezone.utc).isoformat(),
    )
    if not success:
        raise ValueError("Status transition failed — app may have been updated concurrently")

    log_id = await repo.log_stage(
        app_id=app_id,
        stage=PipelineStage.REVIEW_RBAC,
        action="ingestion_approved",
        status="success",
        details={"previous_status": current_status},
        performed_by=performed_by,
    )

    return {
        "app_id": app_id,
        "status": IngestionStatus.CONFIGURED.value,
        "log_id": log_id,
    }


async def reject_ingestion(app_id: str, reason: str, performed_by: str) -> dict:
    """Transition an ingestion from awaiting_review → archived (rejected).

    Stores the rejection reason in the activity log.
    """
    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")

    current_status = app.get("status")
    if current_status != IngestionStatus.AWAITING_REVIEW.value:
        raise ValueError(
            f"App must be in 'awaiting_review' status to reject "
            f"(current: {current_status})"
        )

    success = await repo.transition_app_status(
        app_id,
        from_status=IngestionStatus.AWAITING_REVIEW.value,
        to_status=IngestionStatus.ARCHIVED.value,
        rejected_by=performed_by,
        rejected_at=datetime.now(timezone.utc).isoformat(),
        rejection_reason=reason,
    )
    if not success:
        raise ValueError("Status transition failed — app may have been updated concurrently")

    log_id = await repo.log_stage(
        app_id=app_id,
        stage=PipelineStage.REVIEW_RBAC,
        action="ingestion_rejected",
        status="failed",
        details={"reason": reason, "previous_status": current_status},
        performed_by=performed_by,
    )

    return {
        "app_id": app_id,
        "status": IngestionStatus.ARCHIVED.value,
        "log_id": log_id,
    }


async def rerun_pipeline_step(app_id: str, step_id: str, performed_by: str) -> dict:
    """Re-run (or mark as resolved) a specific pipeline step.

    For most steps this records a step_resolved event and marks prior
    failed entries for that step as resolved. For steps that have an
    actual re-execution path, this will be extended in a future wave.
    Currently used to retroactively contextualize failed entries like
    clone_failed where the workaround succeeded outside the pipeline.
    """
    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")

    # Validate step_id is a known pipeline stage.
    valid_stages = {s.value for s in PipelineStage}
    if step_id not in valid_stages:
        raise ValueError(
            f"Unknown step_id '{step_id}'. Must be one of: {sorted(valid_stages)}"
        )

    # Write a new "step_resolved" log entry.
    new_log_id = await repo.log_stage(
        app_id=app_id,
        stage=PipelineStage(step_id),
        action="step_resolved",
        status="success",
        details={
            "step_id": step_id,
            "note": "Step manually marked as resolved by admin",
        },
        performed_by=performed_by,
    )

    # Mark all prior failed entries for this step as resolved.
    failed_entries = await repo.find_failed_log_entries(app_id, step_id)
    resolved_count = 0
    for entry in failed_entries:
        ok = await repo.mark_log_resolved(entry["log_id"], resolved_by=new_log_id)
        if ok:
            resolved_count += 1

    # Update the stage_results so the pipeline progress bar reflects resolution.
    await repo.update_stage(app_id, PipelineStage(step_id), {"status": "success", "resolved_by": new_log_id})

    return {
        "app_id": app_id,
        "step_id": step_id,
        "new_log_id": new_log_id,
        "resolved_count": resolved_count,
    }


# ==================== CONTRACT VALIDATION ====================

# Repo root used for resolving generated file paths and auto-discovery globs.
# service.py lives at backend/services/ingestion/service.py — four parents up
# is the repo root (one more level than MAPPINGS_DIR, which lives in backend/).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Regex patterns reused across files. Keep this set small and conservative —
# false positives are worse than false negatives for a validation gate.
_RE_BRACKET_FIELD = re.compile(r"""(\w+)\[(?:"|')([\w_]+)(?:"|')\]""")
_RE_ATTR_FIELD = re.compile(r"\b(\w+)\.([\w_]+)\b")
_RE_DB_BRACKET_COLLECTION = re.compile(r"""\bdb\[(?:"|')([\w_]+)(?:"|')\]""")
_RE_DB_ATTR_COLLECTION = re.compile(r"\bdb\.([\w_]+)\b")
_RE_STATUS_LITERAL = re.compile(
    r"""\bstatus\s*(?:===|==|:|=)\s*(?:"|')([\w_-]+)(?:"|')"""
)
_RE_DOT_ID = re.compile(r"\b(\w+)\.id\b")

# Attribute-access whitelist: short Python builtins / common method names that
# show up as `<name>.<attr>` but aren't field references. Keeps the heuristic
# from flagging things like `obj.get(...)`, `result.append(...)`, etc.
_ATTR_BLACKLIST = {
    "get", "set", "pop", "append", "extend", "insert", "remove", "keys",
    "values", "items", "update", "copy", "clear", "model_dump", "dict",
    "json", "lower", "upper", "strip", "split", "replace", "format",
    "find_one", "find", "insert_one", "insert_many", "update_one",
    "update_many", "delete_one", "delete_many",
}


def _is_test_file(path: Path) -> bool:
    """Heuristic: is this a test file we should skip?"""
    name = path.name
    parts = {p.lower() for p in path.parts}
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.js")
        or name.endswith(".test.jsx")
        or name.endswith(".test.tsx")
        or name.endswith(".spec.js")
        or name.endswith(".spec.jsx")
        or name.endswith(".spec.tsx")
        or "tests" in parts
        or "__tests__" in parts
    )


def _line_is_skippable(line: str, in_block_comment: bool) -> bool:
    """Skip pure comment lines and obvious docstring-only lines."""
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("#"):
        return True
    if stripped.startswith("//"):
        return True
    if stripped.startswith("*") and not stripped.startswith("*/"):
        # Inside a JS block comment continuation
        return True
    return in_block_comment


def _infer_entity_from_path(path: Path, valid_entities: set) -> Optional[str]:
    """Best-effort: pick the entity name most likely associated with this file
    by scanning path parts for a match. Returns None if no confident match."""
    parts_lc = [p.lower() for p in path.parts]
    name_lc = path.stem.lower()
    for entity in valid_entities:
        e_lc = entity.lower()
        if e_lc in name_lc.split("_") or e_lc in name_lc.split("-"):
            return entity
        if any(e_lc == part or e_lc in part.split("_") or e_lc in part.split("-")
               for part in parts_lc):
            return entity
    return None


async def validate_against_mapping(
    app_id: str,
    generated_files: Optional[list] = None,
) -> dict:
    """Validate generated code references against the adaptation mapping file.

    For each generated file, scan for field name references, ID field patterns,
    enum value literals, and collection names. Flag references that don't match
    the mapping.

    Args:
        app_id: The ingestion registry ID.
        generated_files: Optional list of file paths to check. If None,
            auto-discovers backend/routes/{app_slug}_*.py and
            frontend/src/components/{slug}/ files.

    Returns:
        Validation report:
            {
                "valid": bool,
                "checked_files": int,
                "issues": [
                    {"file": str, "line": int, "severity": "error"|"warning", "issue": str},
                    ...
                ],
                "summary": {"errors": int, "warnings": int}
            }
        Returns {"valid": False, "issues": [...], "summary": {...}, "checked_files": 0}
        with a single "error" issue when the mapping file doesn't exist (does NOT raise).
    """
    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")

    app_slug = app.get("app_slug") or slug_to_app_slug(app["slug"])
    mapping_path = MAPPINGS_DIR / f"{app_slug}.json"

    if not mapping_path.exists():
        return {
            "valid": False,
            "checked_files": 0,
            "issues": [{
                "file": str(mapping_path),
                "line": 0,
                "severity": "error",
                "issue": (
                    f"Mapping file not found at {mapping_path}. "
                    f"Run generate_mapping_file({app_id}) first."
                ),
            }],
            "summary": {"errors": 1, "warnings": 0},
        }

    try:
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "valid": False,
            "checked_files": 0,
            "issues": [{
                "file": str(mapping_path),
                "line": 0,
                "severity": "error",
                "issue": f"Mapping file is unreadable: {exc}",
            }],
            "summary": {"errors": 1, "warnings": 0},
        }

    entities = mapping.get("entities") or {}
    valid_entities = set(entities.keys())
    valid_fields: dict = {
        e: set((entities[e].get("fields") or {}).keys()) for e in entities
    }
    valid_id_fields = {f"{e}_id" for e in entities}
    valid_enums: dict = {
        e: set(entities[e].get("status_enum") or []) for e in entities
    }
    valid_collections = {
        e: entities[e].get("collection") for e in entities
        if entities[e].get("collection")
    }
    valid_collection_names = set(valid_collections.values())

    # Resolve files to check.
    if generated_files is None:
        files_to_check: list = []
        backend_routes_dir = _REPO_ROOT / "backend" / "routes"
        if backend_routes_dir.exists():
            files_to_check.extend(backend_routes_dir.glob(f"{app_slug}_*.py"))
        # Try both app_slug and slug for the frontend directory — different
        # generators have used both conventions.
        for d in (
            _REPO_ROOT / "frontend" / "src" / "components" / app_slug,
            _REPO_ROOT / "frontend" / "src" / "components" / app.get("slug", ""),
        ):
            if d.exists() and d.is_dir():
                for ext in ("*.js", "*.jsx", "*.tsx"):
                    files_to_check.extend(d.rglob(ext))
                break
    else:
        files_to_check = [Path(f) for f in generated_files]

    issues: list = []
    checked = 0

    for fpath in files_to_check:
        # Resolve absolute and relative paths for reporting.
        fpath_abs = fpath if fpath.is_absolute() else (_REPO_ROOT / fpath)
        if not fpath_abs.exists() or not fpath_abs.is_file():
            continue
        if _is_test_file(fpath_abs):
            continue

        try:
            text = fpath_abs.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        try:
            rel_path = str(fpath_abs.relative_to(_REPO_ROOT))
        except ValueError:
            rel_path = str(fpath_abs)

        checked += 1
        is_python = fpath_abs.suffix == ".py"
        entity_for_file = _infer_entity_from_path(fpath_abs, valid_entities)

        # Quick bail-out: if the file body contains `def test_`, treat as tests.
        if "def test_" in text:
            continue

        in_block_comment = False
        for lineno, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line

            # Track JS-style /* ... */ block comments at line granularity.
            if "/*" in line and "*/" not in line:
                in_block_comment = True
            if "*/" in line and "/*" not in line:
                in_block_comment = False
                continue

            if _line_is_skippable(line, in_block_comment):
                continue

            # 1) Bracket-style field refs:  customer["customer_name"]
            for match in _RE_BRACKET_FIELD.finditer(line):
                var, field = match.group(1), match.group(2)
                if var in valid_entities:
                    if field not in valid_fields.get(var, set()):
                        issues.append({
                            "file": rel_path,
                            "line": lineno,
                            "severity": "error",
                            "issue": (
                                f"Unknown field '{field}' on entity '{var}'. "
                                f"Mapping defines: "
                                f"{sorted(valid_fields.get(var, set()))}"
                            ),
                        })

            # 2) Attribute-style field refs (Python only, conservative):
            #    project.title  →  flag if 'title' not in valid_fields['project']
            if is_python:
                for match in _RE_ATTR_FIELD.finditer(line):
                    var, attr = match.group(1), match.group(2)
                    if var not in valid_entities:
                        continue
                    if attr in _ATTR_BLACKLIST:
                        continue
                    if attr.startswith("_"):
                        continue
                    if attr in valid_fields.get(var, set()):
                        continue
                    if attr in valid_id_fields:
                        continue
                    # Only flag if the LHS is clearly the entity name as a
                    # variable, not a module/class. Heuristic: lowercase var.
                    if var != var.lower():
                        continue
                    issues.append({
                        "file": rel_path,
                        "line": lineno,
                        "severity": "error",
                        "issue": (
                            f"Unknown field '{attr}' on entity '{var}'. "
                            f"Mapping defines: "
                            f"{sorted(valid_fields.get(var, set()))}"
                        ),
                    })

            # 3) Frontend `.id` references — likely should be `<entity>_id`.
            #    Warning only: could be a legitimate Mongo `_id` or React key.
            if not is_python and entity_for_file:
                for match in _RE_DOT_ID.finditer(line):
                    var = match.group(1)
                    # Skip common noise: event.id, e.id, this.id (DOM), document.id
                    if var in {"e", "event", "this", "document", "window", "ref"}:
                        continue
                    issues.append({
                        "file": rel_path,
                        "line": lineno,
                        "severity": "warning",
                        "issue": (
                            f"`.id` reference on '{var}' — mapping uses "
                            f"'{entity_for_file}_id' as the canonical id field."
                        ),
                    })

            # 4) Status enum literals.
            if entity_for_file and valid_enums.get(entity_for_file):
                for match in _RE_STATUS_LITERAL.finditer(line):
                    literal = match.group(1)
                    if literal not in valid_enums[entity_for_file]:
                        issues.append({
                            "file": rel_path,
                            "line": lineno,
                            "severity": "error",
                            "issue": (
                                f"Status literal '{literal}' not in mapping enum "
                                f"for '{entity_for_file}': "
                                f"{sorted(valid_enums[entity_for_file])}"
                            ),
                        })

            # 5) Collection names (Python / backend only).
            if is_python:
                for match in _RE_DB_BRACKET_COLLECTION.finditer(line):
                    coll = match.group(1)
                    # Only flag collections that look like ours but aren't.
                    if coll.startswith(f"{app_slug}_") and coll not in valid_collection_names:
                        issues.append({
                            "file": rel_path,
                            "line": lineno,
                            "severity": "error",
                            "issue": (
                                f"Collection '{coll}' not in mapping. "
                                f"Expected one of: {sorted(valid_collection_names)}"
                            ),
                        })
                for match in _RE_DB_ATTR_COLLECTION.finditer(line):
                    coll = match.group(1)
                    if coll.startswith(f"{app_slug}_") and coll not in valid_collection_names:
                        issues.append({
                            "file": rel_path,
                            "line": lineno,
                            "severity": "error",
                            "issue": (
                                f"Collection '{coll}' not in mapping. "
                                f"Expected one of: {sorted(valid_collection_names)}"
                            ),
                        })

    errors = sum(1 for i in issues if i["severity"] == "error")
    warnings = sum(1 for i in issues if i["severity"] == "warning")

    return {
        "valid": errors == 0,
        "checked_files": checked,
        "issues": issues,
        "summary": {"errors": errors, "warnings": warnings},
    }


# ==================== CONTRACT VALIDATION ====================

# Repo root used for resolving generated file paths and auto-discovery globs.
# service.py lives at backend/services/ingestion/service.py — four parents up
# is the repo root (one more level than MAPPINGS_DIR, which lives in backend/).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Regex patterns reused across files. Keep this set small and conservative —
# false positives are worse than false negatives for a validation gate.
_RE_BRACKET_FIELD = re.compile(r"""(\w+)\[(?:"|')([\w_]+)(?:"|')\]""")
_RE_ATTR_FIELD = re.compile(r"\b(\w+)\.([\w_]+)\b")
_RE_DB_BRACKET_COLLECTION = re.compile(r"""\bdb\[(?:"|')([\w_]+)(?:"|')\]""")
_RE_DB_ATTR_COLLECTION = re.compile(r"\bdb\.([\w_]+)\b")
_RE_STATUS_LITERAL = re.compile(
    r"""\bstatus\s*(?:===|==|:|=)\s*(?:"|')([\w_-]+)(?:"|')"""
)
_RE_DOT_ID = re.compile(r"\b(\w+)\.id\b")

# Attribute-access whitelist: short Python builtins / common method names that
# show up as `<name>.<attr>` but aren't field references. Keeps the heuristic
# from flagging things like `obj.get(...)`, `result.append(...)`, etc.
_ATTR_BLACKLIST = {
    "get", "set", "pop", "append", "extend", "insert", "remove", "keys",
    "values", "items", "update", "copy", "clear", "model_dump", "dict",
    "json", "lower", "upper", "strip", "split", "replace", "format",
    "find_one", "find", "insert_one", "insert_many", "update_one",
    "update_many", "delete_one", "delete_many",
}


def _is_test_file(path: Path) -> bool:
    """Heuristic: is this a test file we should skip?"""
    name = path.name
    parts = {p.lower() for p in path.parts}
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.js")
        or name.endswith(".test.jsx")
        or name.endswith(".test.tsx")
        or name.endswith(".spec.js")
        or name.endswith(".spec.jsx")
        or name.endswith(".spec.tsx")
        or "tests" in parts
        or "__tests__" in parts
    )


def _line_is_skippable(line: str, in_block_comment: bool) -> bool:
    """Skip pure comment lines and obvious docstring-only lines."""
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("#"):
        return True
    if stripped.startswith("//"):
        return True
    if stripped.startswith("*") and not stripped.startswith("*/"):
        # Inside a JS block comment continuation
        return True
    return in_block_comment


def _infer_entity_from_path(path: Path, valid_entities: set) -> Optional[str]:
    """Best-effort: pick the entity name most likely associated with this file
    by scanning path parts for a match. Returns None if no confident match."""
    parts_lc = [p.lower() for p in path.parts]
    name_lc = path.stem.lower()
    for entity in valid_entities:
        e_lc = entity.lower()
        if e_lc in name_lc.split("_") or e_lc in name_lc.split("-"):
            return entity
        if any(e_lc == part or e_lc in part.split("_") or e_lc in part.split("-")
               for part in parts_lc):
            return entity
    return None


async def validate_against_mapping(
    app_id: str,
    generated_files: Optional[list] = None,
) -> dict:
    """Validate generated code references against the adaptation mapping file.

    For each generated file, scan for field name references, ID field patterns,
    enum value literals, and collection names. Flag references that don't match
    the mapping.

    Args:
        app_id: The ingestion registry ID.
        generated_files: Optional list of file paths to check. If None,
            auto-discovers backend/routes/{app_slug}_*.py and
            frontend/src/components/{slug}/ files.

    Returns:
        Validation report:
            {
                "valid": bool,
                "checked_files": int,
                "issues": [
                    {"file": str, "line": int, "severity": "error"|"warning", "issue": str},
                    ...
                ],
                "summary": {"errors": int, "warnings": int}
            }
        Returns {"valid": False, "issues": [...], "summary": {...}, "checked_files": 0}
        with a single "error" issue when the mapping file doesn't exist (does NOT raise).
    """
    app = await repo.get_app_registry(app_id)
    if not app:
        raise ValueError(f"App {app_id} not found")

    app_slug = app.get("app_slug") or slug_to_app_slug(app["slug"])
    mapping_path = MAPPINGS_DIR / f"{app_slug}.json"

    if not mapping_path.exists():
        return {
            "valid": False,
            "checked_files": 0,
            "issues": [{
                "file": str(mapping_path),
                "line": 0,
                "severity": "error",
                "issue": (
                    f"Mapping file not found at {mapping_path}. "
                    f"Run generate_mapping_file({app_id}) first."
                ),
            }],
            "summary": {"errors": 1, "warnings": 0},
        }

    try:
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "valid": False,
            "checked_files": 0,
            "issues": [{
                "file": str(mapping_path),
                "line": 0,
                "severity": "error",
                "issue": f"Mapping file is unreadable: {exc}",
            }],
            "summary": {"errors": 1, "warnings": 0},
        }

    entities = mapping.get("entities") or {}
    valid_entities = set(entities.keys())
    valid_fields: dict = {
        e: set((entities[e].get("fields") or {}).keys()) for e in entities
    }
    valid_id_fields = {f"{e}_id" for e in entities}
    valid_enums: dict = {
        e: set(entities[e].get("status_enum") or []) for e in entities
    }
    valid_collections = {
        e: entities[e].get("collection") for e in entities
        if entities[e].get("collection")
    }
    valid_collection_names = set(valid_collections.values())

    # Resolve files to check.
    if generated_files is None:
        files_to_check: list = []
        backend_routes_dir = _REPO_ROOT / "backend" / "routes"
        if backend_routes_dir.exists():
            files_to_check.extend(backend_routes_dir.glob(f"{app_slug}_*.py"))
        # Try both app_slug and slug for the frontend directory — different
        # generators have used both conventions.
        for d in (
            _REPO_ROOT / "frontend" / "src" / "components" / app_slug,
            _REPO_ROOT / "frontend" / "src" / "components" / app.get("slug", ""),
        ):
            if d.exists() and d.is_dir():
                for ext in ("*.js", "*.jsx", "*.tsx"):
                    files_to_check.extend(d.rglob(ext))
                break
    else:
        files_to_check = [Path(f) for f in generated_files]

    issues: list = []
    checked = 0

    for fpath in files_to_check:
        # Resolve absolute and relative paths for reporting.
        fpath_abs = fpath if fpath.is_absolute() else (_REPO_ROOT / fpath)
        if not fpath_abs.exists() or not fpath_abs.is_file():
            continue
        if _is_test_file(fpath_abs):
            continue

        try:
            text = fpath_abs.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        try:
            rel_path = str(fpath_abs.relative_to(_REPO_ROOT))
        except ValueError:
            rel_path = str(fpath_abs)

        is_python = fpath_abs.suffix == ".py"
        entity_for_file = _infer_entity_from_path(fpath_abs, valid_entities)

        # Quick bail-out: if the file body contains `def test_`, treat as tests.
        # Increment `checked` only after this guard so files we skip don't
        # inflate the validated-file count in the report.
        if "def test_" in text:
            continue

        checked += 1

        in_block_comment = False
        for lineno, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line

            # Track JS-style /* ... */ block comments at line granularity.
            if "/*" in line and "*/" not in line:
                in_block_comment = True
            if "*/" in line and "/*" not in line:
                in_block_comment = False
                continue

            if _line_is_skippable(line, in_block_comment):
                continue

            # 1) Bracket-style field refs:  customer["customer_name"]
            for match in _RE_BRACKET_FIELD.finditer(line):
                var, field = match.group(1), match.group(2)
                if var in valid_entities:
                    if field not in valid_fields.get(var, set()):
                        issues.append({
                            "file": rel_path,
                            "line": lineno,
                            "severity": "error",
                            "issue": (
                                f"Unknown field '{field}' on entity '{var}'. "
                                f"Mapping defines: "
                                f"{sorted(valid_fields.get(var, set()))}"
                            ),
                        })

            # 2) Attribute-style field refs (Python only, conservative):
            #    project.title  →  flag if 'title' not in valid_fields['project']
            if is_python:
                for match in _RE_ATTR_FIELD.finditer(line):
                    var, attr = match.group(1), match.group(2)
                    if var not in valid_entities:
                        continue
                    if attr in _ATTR_BLACKLIST:
                        continue
                    if attr.startswith("_"):
                        continue
                    if attr in valid_fields.get(var, set()):
                        continue
                    if attr in valid_id_fields:
                        continue
                    # Only flag if the LHS is clearly the entity name as a
                    # variable, not a module/class. Heuristic: lowercase var.
                    if var != var.lower():
                        continue
                    issues.append({
                        "file": rel_path,
                        "line": lineno,
                        "severity": "error",
                        "issue": (
                            f"Unknown field '{attr}' on entity '{var}'. "
                            f"Mapping defines: "
                            f"{sorted(valid_fields.get(var, set()))}"
                        ),
                    })

            # 3) Frontend `.id` references — likely should be `<entity>_id`.
            #    Warning only: could be a legitimate Mongo `_id` or React key.
            if not is_python and entity_for_file:
                for match in _RE_DOT_ID.finditer(line):
                    var = match.group(1)
                    # Skip common noise: event.id, e.id, this.id (DOM), document.id
                    if var in {"e", "event", "this", "document", "window", "ref"}:
                        continue
                    issues.append({
                        "file": rel_path,
                        "line": lineno,
                        "severity": "warning",
                        "issue": (
                            f"`.id` reference on '{var}' — mapping uses "
                            f"'{entity_for_file}_id' as the canonical id field."
                        ),
                    })

            # 4) Status enum literals.
            if entity_for_file and valid_enums.get(entity_for_file):
                for match in _RE_STATUS_LITERAL.finditer(line):
                    literal = match.group(1)
                    if literal not in valid_enums[entity_for_file]:
                        issues.append({
                            "file": rel_path,
                            "line": lineno,
                            "severity": "error",
                            "issue": (
                                f"Status literal '{literal}' not in mapping enum "
                                f"for '{entity_for_file}': "
                                f"{sorted(valid_enums[entity_for_file])}"
                            ),
                        })

            # 5) Collection names (Python / backend only).
            if is_python:
                for match in _RE_DB_BRACKET_COLLECTION.finditer(line):
                    coll = match.group(1)
                    # Only flag collections that look like ours but aren't.
                    if coll.startswith(f"{app_slug}_") and coll not in valid_collection_names:
                        issues.append({
                            "file": rel_path,
                            "line": lineno,
                            "severity": "error",
                            "issue": (
                                f"Collection '{coll}' not in mapping. "
                                f"Expected one of: {sorted(valid_collection_names)}"
                            ),
                        })
                for match in _RE_DB_ATTR_COLLECTION.finditer(line):
                    coll = match.group(1)
                    if coll.startswith(f"{app_slug}_") and coll not in valid_collection_names:
                        issues.append({
                            "file": rel_path,
                            "line": lineno,
                            "severity": "error",
                            "issue": (
                                f"Collection '{coll}' not in mapping. "
                                f"Expected one of: {sorted(valid_collection_names)}"
                            ),
                        })

    errors = sum(1 for i in issues if i["severity"] == "error")
    warnings = sum(1 for i in issues if i["severity"] == "warning")

    return {
        "valid": errors == 0,
        "checked_files": checked,
        "issues": issues,
        "summary": {"errors": errors, "warnings": warnings},
    }
