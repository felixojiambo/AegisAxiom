"""Seed app_registry rows for built-in ingested apps.

Some apps were ingested into AEGISAXIOM before the app-ingestion admin module
became the source of truth for governance display. Their code lives in
the AEGISAXIOM codebase but no `app_registry` document exists, so they don't
surface in the admin UI. This seed makes them visible.

Idempotent — keyed on slug (uniquely indexed on app_registry).

Add new entries to BUILTIN_APPS when an app is wholesale-merged into
AEGISAXIOM without going through the live /ingest-app pipeline.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


BUILTIN_APPS = [
    {
        "app_id": "app_projects_pmo",
        "name": "Projects PMO",
        "slug": "projects",
        "app_slug": "projects",
        "description": (
            "Project management module — projects, tasks, sprints, waves, "
            "threads, blocker tickets, standups. Wholesale replacement of the "
            "legacy AEGISAXIOM pm_projects namespace (released 2026-04-29)."
        ),
        "source_repo_url": "https://github.com/aharrelson/Davies-PMO",
        "ingestion_mode": "internal",
        "status": "active",
        "ingestion_version": 1,
        "modules_imported": [
            {"name": "project", "entity_name": "project"},
            {"name": "task", "entity_name": "task"},
            {"name": "sprint", "entity_name": "sprint"},
            {"name": "wave", "entity_name": "wave"},
            {"name": "thread", "entity_name": "thread"},
            {"name": "blocker_ticket", "entity_name": "blocker_ticket"},
            {"name": "standup", "entity_name": "standup"},
        ],
    },
    {
        "app_id": "app_finance_cc",
        "name": "Financial Command Center",
        "slug": "finance",
        "app_slug": "finance",
        "description": (
            "Single-tenant financial analytics + operations command center "
            "reading the QBO data lake. Wholesale replacement of the legacy "
            "AEGISAXIOM finance namespace (released 2026-05-05). Includes 4 writable "
            "entities (drafts, dashboards, widgets, CoA mappings), ~30 "
            "analytical endpoints, and 19 QBO virtual entities."
        ),
        "source_repo_url": "https://github.com/aharrelson/Ryan-Finance-App",
        "ingestion_mode": "internal",
        "status": "active",
        "ingestion_version": 1,
        "modules_imported": [
            {"name": "coa_mapping", "entity_name": "coa_mapping"},
            {"name": "draft", "entity_name": "draft"},
            {"name": "dashboard", "entity_name": "dashboard"},
            {"name": "widget", "entity_name": "widget"},
        ],
    },
]


async def seed_builtin_ingestions(db) -> int:
    """Upsert app_registry rows for built-in ingested apps. Idempotent.

    Returns the number of rows touched (inserted or updated).
    """
    now = datetime.now(timezone.utc).isoformat()
    touched = 0
    for app in BUILTIN_APPS:
        slug = app["slug"]
        # Match on slug only; app_id is generated for inserts but preserved on
        # updates so any URL or audit log already pointing at the real app_id
        # keeps working.
        set_on_insert = {
            **app,
            "modules_discarded": [],
            "ui_config": {},
            "created_at": now,
            "deleted_at": None,
        }
        # Don't overwrite app_id on existing rows — only set it when inserting.
        # Same for created_at. Status / description / modules_imported get
        # refreshed on every startup so doc reflects latest code state.
        update = {
            "$set": {
                "name": app["name"],
                "app_slug": app["app_slug"],
                "description": app["description"],
                "source_repo_url": app["source_repo_url"],
                "ingestion_mode": app["ingestion_mode"],
                "status": app["status"],
                "ingestion_version": app["ingestion_version"],
                "modules_imported": app["modules_imported"],
                "updated_at": now,
            },
            "$setOnInsert": {
                "app_id": app["app_id"],
                "modules_discarded": [],
                "ui_config": {},
                "created_at": now,
                "deleted_at": None,
            },
        }
        result = await db.app_registry.update_one(
            {"slug": slug, "deleted_at": None},
            update,
            upsert=True,
        )
        if result.upserted_id is not None:
            touched += 1
            logger.info(f"Seeded built-in ingestion: {slug} (inserted)")
        elif result.modified_count > 0:
            touched += 1
            logger.debug(f"Refreshed built-in ingestion: {slug}")

    logger.info(f"Built-in ingestions: {touched}/{len(BUILTIN_APPS)} touched")
    return touched
