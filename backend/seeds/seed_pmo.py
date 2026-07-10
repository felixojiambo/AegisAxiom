"""Seed script for PMO Assistant (Projects ingestion default AI employee).

Creates the PMO Assistant AI employee record bound to the `pmo_assistant`
persona defined in `services.ai_employee_defaults`. The persona resolves
to the projects.* permission glob set at apply-time against the live
permission registry.

Idempotent — uses upsert keyed on employee_id. Safe to run on every
boot when SEED_AI_EMPLOYEES=true.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


EMPLOYEE_ID = "ai_pmo_assistant"
NAME = "PMO Assistant"
PERSONA = "pmo_assistant"
DESCRIPTION = (
    "Plans, tracks, and coordinates projects across waterfall, agile, "
    "and waves+threads methodologies."
)


async def seed_pmo(db) -> None:
    """Seed the default PMO Assistant AI employee. Idempotent.

    Pattern mirrors `seed_vega.py`: upsert keyed on `employee_id`, then
    apply the persona's default permission set via
    `apply_persona_defaults`. Re-applying the persona is a merge, not an
    overwrite, so manual `app_access` edits survive subsequent boots.
    """
    existing = await db.ai_employees.find_one(
        {"employee_id": EMPLOYEE_ID}, {"_id": 0, "employee_id": 1, "allowed_roles": 1}
    )
    if existing:
        # Idempotent path — employee already exists. Re-apply persona
        # defaults in case the permission registry has grown new keys
        # the persona's globs would now match.
        # Wave 5: Backfill allowed_roles for previously-seeded records that
        # missed the admin + project_manager grant. Use $addToSet so an
        # admin who has manually expanded the list keeps their additions.
        try:
            await db.ai_employees.update_one(
                {"employee_id": EMPLOYEE_ID},
                {"$addToSet": {"allowed_roles": {"$each": ["admin", "project_manager"]}}},
            )
        except Exception as e:
            logger.warning(
                "PMO Assistant allowed_roles backfill failed (non-fatal): %s", e
            )
        try:
            from services.ai_employee_defaults import apply_persona_defaults
            await apply_persona_defaults(EMPLOYEE_ID, PERSONA)
        except Exception as e:
            logger.warning(
                "PMO Assistant already seeded; re-apply of persona defaults "
                "failed (non-fatal): %s",
                e,
            )
        return

    now = datetime.now(timezone.utc).isoformat()
    pmo_doc = {
        "employee_id": EMPLOYEE_ID,
        "name": NAME,
        "role": "Project Management Office Assistant",
        "description": DESCRIPTION,
        "avatar": "🗂️",
        "type": "conversational",
        "status": "active",
        "mode": "manual",
        "persona": PERSONA,
        "system_prompt": (
            "You are the PMO Assistant. You help project managers and "
            "contributors plan projects, define tasks, run sprints, "
            "coordinate waves+threads work, and track health across "
            "waterfall, agile, and waves+threads methodologies."
        ),
        "router_description": (
            "questions about creating or updating projects, defining tasks "
            "and sprints, planning waves and threads, tracking project "
            "health, drafting postmortems, or running standups"
        ),
        "model_provider": "google",
        "model_name": "gemini-3-flash-preview",
        "auto_greeting": False,
        "knowledge_base": {
            "collection": "ai_employee_knowledge",
            "filter": {"employee_id": EMPLOYEE_ID},
        },
        "tools": [],
        "state_schema": {},
        "connection": {},
        "metrics_config": {
            "metric1_label": "Projects Created",
            "metric2_label": "Tasks Tracked",
            "metric3_label": "Sessions Completed",
            "source": "computed",
        },
        "metrics": {"metric1": 0, "metric2": 0, "metric3": 0},
        "execution_count": 0,
        "success_count": 0,
        "error_count": 0,
        "last_execution_at": None,
        "execution_log": [],
        "specialties": [
            "project_management",
            "waterfall",
            "agile",
            "waves_threads",
            "postmortems",
        ],
        "contractor": {"name": "", "agreement_id": ""},
        "webhook_token": "",
        "app_access": [],
        # Wave 5: PMO Assistant must be reachable by admins AND project managers
        # so non-admin PMs can chat with it. Marketplace agent registration
        # falls back to ["admin"] when this field is absent — explicit list here.
        "allowed_roles": ["admin", "project_manager"],
        "allowed_user_ids": [],
        "is_active": True,
        "callable_by_chat_orchestrator": True,
        "created_by": "system",
        "created_at": now,
        "updated_at": now,
    }

    await db.ai_employees.update_one(
        {"employee_id": EMPLOYEE_ID},
        {"$set": pmo_doc},
        upsert=True,
    )
    logger.info("PMO Assistant employee record upserted")

    # Apply persona-driven permission defaults. Resolves projects.* globs
    # against the live permission registry, so this MUST run after
    # seed_system_permissions and after the projects RBAC seed.
    try:
        from services.ai_employee_defaults import apply_persona_defaults
        await apply_persona_defaults(EMPLOYEE_ID, PERSONA)
    except Exception as e:
        logger.warning(
            "PMO Assistant persona defaults application failed (non-fatal): %s",
            e,
        )
