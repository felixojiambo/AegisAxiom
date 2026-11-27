"""
App Ingestion — Repository Layer

All persistence goes to MongoDB fallback collections. When Postgres migration
is complete, swap the backend in this file — no other code changes needed.
"""
import logging
from datetime import datetime, timezone
from typing import Optional, List

from database import get_db
from services.ingestion.models import (
    AppRegistry, AppModule, AppIngestionLog, IngestionFieldCatalogue,
    IngestionArtifact, IngestionState, IngestionStatus, PipelineStage,
)

logger = logging.getLogger(__name__)


# ==================== APP REGISTRY ====================

async def create_app_registry(app: AppRegistry) -> str:
    """Insert a new app_registry document. Returns app_id."""
    db = get_db()
    doc = app.model_dump()
    doc["created_at"] = doc["created_at"].isoformat()
    doc["updated_at"] = doc["updated_at"].isoformat()
    if doc.get("deleted_at"):
        doc["deleted_at"] = doc["deleted_at"].isoformat()
    # Ensure modules_imported contains plain dicts (Pydantic models → dict)
    doc["modules_imported"] = [m if isinstance(m, dict) else m.model_dump() for m in doc.get("modules_imported", [])]
    await db.app_registry.insert_one(doc)
    logger.info(f"Created app_registry: {app.app_id} ({app.name})")
    return app.app_id


async def get_app_registry(app_id: str) -> Optional[dict]:
    """Get an app_registry document by app_id."""
    db = get_db()
    return await db.app_registry.find_one({"app_id": app_id, "deleted_at": None}, {"_id": 0})


async def get_app_by_slug(slug: str) -> Optional[dict]:
    """Get an app_registry document by slug."""
    db = get_db()
    return await db.app_registry.find_one({"slug": slug, "deleted_at": None}, {"_id": 0})


async def get_app_by_repo_url(repo_url: str) -> Optional[dict]:
    """Get an app_registry document by source repo URL."""
    db = get_db()
    return await db.app_registry.find_one(
        {"source_repo_url": repo_url, "deleted_at": None}, {"_id": 0}
    )


async def list_app_registrations(status: Optional[str] = None) -> List[dict]:
    """List all app registrations, optionally filtered by status."""
    db = get_db()
    query = {"deleted_at": None}
    if status:
        query["status"] = status
    return await db.app_registry.find(query, {"_id": 0}).sort("created_at", -1).to_list(100)


async def update_app_status(app_id: str, status: IngestionStatus, **extra_fields) -> bool:
    """Update an app's status and any extra fields."""
    db = get_db()
    update = {"$set": {"status": status.value, "updated_at": datetime.now(timezone.utc).isoformat()}}
    for k, v in extra_fields.items():
        update["$set"][k] = v
    result = await db.app_registry.update_one({"app_id": app_id}, update)
    return result.modified_count > 0


async def update_app_registry_metadata(
    app_id: str, update_doc: dict
) -> Optional[dict]:
    """Phase 1E.3a — Update route_prefixes and/or permission_keys.

    Used by the registry-metadata PATCH endpoint and the backfill
    script. Returns the updated row (sans Mongo `_id`) on success,
    or None if the app doesn't exist / is soft-deleted.

    `update_doc` is the pre-cleaned set of fields to assign — the
    caller is responsible for normalising prefix formats and
    deduplicating list entries. We just stamp updated_at and write.
    """
    if not update_doc:
        return None

    db = get_db()
    update_doc = {**update_doc, "updated_at": datetime.now(timezone.utc).isoformat()}
    result = await db.app_registry.update_one(
        {"app_id": app_id, "deleted_at": None},
        {"$set": update_doc},
    )
    if result.matched_count == 0:
        return None
    return await db.app_registry.find_one(
        {"app_id": app_id}, {"_id": 0}
    )


async def update_app_modules_list(app_id: str, modules_imported: list, modules_discarded: list):
    """Update the modules_imported and modules_discarded arrays."""
    db = get_db()
    await db.app_registry.update_one({"app_id": app_id}, {"$set": {
        "modules_imported": modules_imported,
        "modules_discarded": modules_discarded,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }})


# ==================== APP MODULES ====================

async def create_app_module(module: AppModule) -> str:
    """Insert a new app_modules document. Returns module_id."""
    db = get_db()
    doc = module.model_dump()
    doc["created_at"] = doc["created_at"].isoformat()
    doc["updated_at"] = doc["updated_at"].isoformat()
    await db.app_modules.insert_one(doc)
    logger.info(f"Created app_module: {module.module_id} ({module.name}) for app {module.app_id}")
    return module.module_id


async def get_app_modules(app_id: str) -> List[dict]:
    """Get all modules for an app."""
    db = get_db()
    return await db.app_modules.find({"app_id": app_id}, {"_id": 0}).to_list(100)


async def update_module_field_count(module_id: str, field_count: int):
    """Update a module's field_count."""
    db = get_db()
    await db.app_modules.update_one(
        {"module_id": module_id},
        {"$set": {"field_count": field_count, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )


# ==================== INGESTION LOG ====================

async def write_log(log: AppIngestionLog) -> str:
    """Insert an ingestion log entry. Returns log_id."""
    db = get_db()
    doc = log.model_dump()
    doc["stage"] = doc["stage"].value if hasattr(doc["stage"], "value") else doc["stage"]
    doc["created_at"] = doc["created_at"].isoformat()
    await db.app_ingestion_log.insert_one(doc)
    return log.log_id


async def log_stage(
    app_id: str,
    stage: PipelineStage,
    action: str,
    status: str = "success",
    details: dict = None,
    duration_ms: int = None,
    performed_by: str = None,
    ingestion_version: int = 1,
) -> str:
    """Convenience: create and write a log entry in one call.

    Dual-writes a summary entry to the unified audit_logs collection so
    ingestion pipeline activity surfaces in the admin Audit Log UI alongside
    other security events. The detailed app_ingestion_log row remains the
    source of truth; the audit_logs row is observability.
    """
    log = AppIngestionLog(
        app_id=app_id,
        ingestion_version=ingestion_version,
        action=action,
        stage=stage,
        status=status,
        details=details or {},
        duration_ms=duration_ms,
        performed_by=performed_by,
    )
    log_id = await write_log(log)

    # Mirror to unified audit_logs (best-effort; pipeline log is source of truth).
    # Write directly here rather than via audit_service.log_action so we can set
    # event_category="security" + event_type="ingestion_stage" — log_action
    # hardcodes event_category="activity" and audit_service.py is frozen this wave.
    try:
        import uuid as _uuid

        from services.audit_service import write_audit_entry  # Phase 2b: audit_logs is Postgres
        stage_value = stage.value if hasattr(stage, "value") else stage
        actor_id = performed_by or "system"
        actor_email = "system@aegisaxiom.internal"
        if performed_by:
            try:
                from db.session import get_sessionmaker
                from repositories.users import UserRepository

                sm = get_sessionmaker()
                async with sm() as pg:
                    user = await UserRepository(pg).get_user_by_id(performed_by)
                if user and user.email:
                    actor_email = user.email
            except Exception as lookup_err:
                logger.warning(
                    f"audit_logs actor_email lookup failed for {performed_by}: {lookup_err}"
                )

        summary = f"{stage_value}/{action} ({status})"
        if duration_ms is not None:
            summary += f" in {duration_ms}ms"

        await write_audit_entry({
            "audit_id": f"audit_{_uuid.uuid4().hex[:16]}",
            "event_category": "security",
            "event_type": "ingestion_stage",
            "action": f"ingestion_{action}",
            "actor_id": actor_id,
            "actor_email": actor_email,
            "resource_type": "app_ingestion",
            "resource_id": app_id,
            "details": {
                "stage": stage_value,
                "action": action,
                "status": status,
                "duration_ms": duration_ms,
                "ingestion_version": ingestion_version,
                "summary": summary,
                "raw_details_ref": log_id,
            },
            "timestamp": datetime.now(timezone.utc),
        })
    except Exception as audit_err:
        logger.warning(f"audit_logs mirror failed for ingestion stage {app_id}/{stage}: {audit_err}")

    return log_id


async def get_ingestion_logs(app_id: str, ingestion_version: int = None) -> List[dict]:
    """Get all log entries for an app, optionally filtered by version."""
    db = get_db()
    query = {"app_id": app_id}
    if ingestion_version is not None:
        query["ingestion_version"] = ingestion_version
    return await db.app_ingestion_log.find(query, {"_id": 0}).sort("created_at", 1).to_list(500)


# ==================== INGESTION ARTIFACTS ====================

async def store_artifact(artifact: IngestionArtifact):
    """Store an ingestion artifact. Upserts by (app_id, artifact_type, version)."""
    db = get_db()
    doc = artifact.model_dump()
    doc["artifact_type"] = doc["artifact_type"].value if hasattr(doc["artifact_type"], "value") else doc["artifact_type"]
    doc["created_at"] = doc["created_at"].isoformat()
    await db.ingestion_artifacts.update_one(
        {"app_id": artifact.app_id, "artifact_type": doc["artifact_type"], "version": artifact.version},
        {"$set": doc},
        upsert=True,
    )


async def get_artifact(app_id: str, artifact_type: str, version: int = 1) -> Optional[dict]:
    """Get a specific artifact."""
    db = get_db()
    return await db.ingestion_artifacts.find_one(
        {"app_id": app_id, "artifact_type": artifact_type, "version": version},
        {"_id": 0}
    )


# ==================== FIELD CATALOGUE ====================

async def store_field_catalogue(catalogue: IngestionFieldCatalogue):
    """Store a field catalogue document. Upserts by (app_id, module_name, version)."""
    db = get_db()
    doc = catalogue.model_dump()
    doc["created_at"] = doc["created_at"].isoformat()
    await db.ingestion_field_catalogue.update_one(
        {"app_id": catalogue.app_id, "module_name": catalogue.module_name, "version": catalogue.version},
        {"$set": doc},
        upsert=True,
    )


async def get_field_catalogues(app_id: str, version: int = None) -> List[dict]:
    """Get all field catalogue documents for an app."""
    db = get_db()
    query = {"app_id": app_id}
    if version is not None:
        query["version"] = version
    return await db.ingestion_field_catalogue.find(query, {"_id": 0}).to_list(100)


async def get_mapping_data(app_id: str) -> Optional[dict]:
    """Consolidate all data needed to build the adaptation mapping file.
    Returns None if the app doesn't exist."""
    app = await get_app_registry(app_id)
    if not app:
        return None
    catalogues = await get_field_catalogues(app_id)
    modules = await get_app_modules(app_id)
    return {"app": app, "catalogues": catalogues, "modules": modules}


# ==================== INGESTION STATE ====================

async def create_or_update_state(state: IngestionState):
    """Create or update the pipeline state document."""
    db = get_db()
    doc = state.model_dump()
    doc["current_stage"] = doc["current_stage"].value if hasattr(doc["current_stage"], "value") else doc["current_stage"]
    doc["started_at"] = doc["started_at"].isoformat()
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.ingestion_state.update_one(
        {"app_id": state.app_id, "ingestion_version": state.ingestion_version},
        {"$set": doc},
        upsert=True,
    )


async def update_stage(app_id: str, stage: PipelineStage, result: dict = None, ingestion_version: int = 1):
    """Update the current stage and record its result."""
    db = get_db()
    update = {
        "$set": {
            "current_stage": stage.value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    }
    if result is not None:
        update["$set"][f"stage_results.{stage.value}"] = result
    await db.ingestion_state.update_one(
        {"app_id": app_id, "ingestion_version": ingestion_version},
        update,
    )


async def get_ingestion_state(app_id: str, ingestion_version: int = None) -> Optional[dict]:
    """Get the current pipeline state for an app."""
    db = get_db()
    query = {"app_id": app_id}
    if ingestion_version is not None:
        query["ingestion_version"] = ingestion_version
    return await db.ingestion_state.find_one(query, {"_id": 0})


# ==================== REVIEW ACTIONS ====================

async def transition_app_status(app_id: str, from_status: str, to_status: str, **extra_fields) -> bool:
    """Atomically transition an app's status only when it is currently `from_status`.
    Returns True if the update succeeded (row was in the expected state), False otherwise."""
    db = get_db()
    update = {
        "$set": {
            "status": to_status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **extra_fields,
        }
    }
    result = await db.app_registry.update_one(
        {"app_id": app_id, "status": from_status},
        update,
    )
    return result.modified_count > 0


async def mark_log_resolved(log_id: str, resolved_by: str) -> bool:
    """Set `resolved_by` on an existing log entry to indicate it was superseded.

    The log entry is NOT deleted — it remains visible in the activity feed but
    the frontend renders it as struck-through with a "(resolved)" tag when
    `resolved_by` is present.
    """
    db = get_db()
    result = await db.app_ingestion_log.update_one(
        {"log_id": log_id},
        {"$set": {"resolved_by": resolved_by}},
    )
    return result.modified_count > 0


async def find_failed_log_entries(app_id: str, step_id: str) -> List[dict]:
    """Find all failed log entries for a given pipeline step."""
    db = get_db()
    return await db.app_ingestion_log.find(
        {"app_id": app_id, "stage": step_id, "status": "failed"},
        {"_id": 0, "log_id": 1},
    ).to_list(50)


# ==================== COLLECTION COLLISION CHECK ====================

async def check_collection_collisions(app_slug: str, entity_names: List[str]) -> List[str]:
    """Check if any proposed collection names already exist in MongoDB."""
    db = get_db()
    existing = await db.list_collection_names()
    collisions = []
    for entity in entity_names:
        collection_name = f"{app_slug}_{entity}"
        if collection_name in existing:
            collisions.append(collection_name)
    return collisions
