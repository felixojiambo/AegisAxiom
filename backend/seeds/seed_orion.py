"""Seed script for Orion (SOW Builder Agent).

Creates the Orion AI employee record and ingests knowledge base content
(system prompt, SOW examples, legal boilerplate, best practices)
into ai_employee_knowledge.

Idempotent — uses upserts keyed on employee_id and knowledge_id.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(__file__).parent / "orion_knowledge"

# Knowledge base files to ingest
KNOWLEDGE_FILES = [
    {
        "filename": "system_prompt.md",
        "knowledge_id": "kb_orion_system_prompt",
        "title": "Orion System Prompt",
        "tags": ["system_prompt", "core"],
    },
    {
        "filename": "sow_examples.md",
        "knowledge_id": "kb_orion_sow_examples",
        "title": "SOW Examples",
        "tags": ["sow_examples", "reference"],
    },
    {
        "filename": "legal_boilerplate.md",
        "knowledge_id": "kb_orion_legal_boilerplate",
        "title": "Legal Boilerplate",
        "tags": ["legal", "boilerplate", "reference"],
    },
    {
        "filename": "best_practices.md",
        "knowledge_id": "kb_orion_best_practices",
        "title": "SOW Best Practices",
        "tags": ["best_practices", "reference"],
    },
]


async def seed_orion(db):
    """Seed Orion employee + knowledge base content. Idempotent."""

    # ── 1. Read system prompt ──
    system_prompt_path = KNOWLEDGE_DIR / "system_prompt.md"
    system_prompt = (
        system_prompt_path.read_text(encoding="utf-8")
        if system_prompt_path.exists()
        else "You are Orion. The knowledge base is being loaded."
    )

    # ── 2. Upsert Orion employee doc ──
    orion_doc = {
        "employee_id": "ai_orion",
        "name": "Orion",
        "role": "SOW Builder Agent",
        "description": (
            "Partners with sales reps to draft, refine, and finalize "
            "Statements of Work for AI implementation projects. Takes "
            "Vega discovery sessions as input, produces contract-ready SOW documents."
        ),
        "avatar": "📋",
        "type": "conversational",
        "status": "active",
        "mode": "manual",
        # Conversational-specific fields
        "system_prompt": system_prompt,
        "router_description": (
            "questions about generating Statements of Work, drafting proposals, "
            "building scoping documents, finalizing SOWs to Google Docs, "
            "or revising existing SOW drafts"
        ),
        "model_provider": "google",
        "model_name": "gemini-3-flash-preview",
        "auto_greeting": True,
        "knowledge_base": {
            "collection": "ai_employee_knowledge",
            "filter": {"employee_id": "ai_orion"},
        },
        "tools": [
            "render_artifact",
            "update_user_state",
            "load_vega_session",
            "generate_sow_section",
            "update_sow_draft",
            "render_sow_preview",
            "finalize_sow_to_google_docs",
            "generate_bpmn_diagram",
        ],
        "state_schema": {
            "active_opportunity_id": None,
        },
        # Standard AI employee fields
        "connection": {},
        "metrics_config": {
            "metric1_label": "SOWs Drafted",
            "metric2_label": "Sections Generated",
            "metric3_label": "SOWs Finalized",
            "source": "computed",
        },
        "metrics": {"metric1": 0, "metric2": 0, "metric3": 0},
        "execution_count": 0,
        "success_count": 0,
        "error_count": 0,
        "last_execution_at": None,
        "execution_log": [],
        "specialties": ["sow", "proposals", "scoping", "google_docs"],
        "contractor": {"name": "", "agreement_id": ""},
        "webhook_token": "",
        "created_by": "system",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    await db.ai_employees.update_one(
        {"employee_id": "ai_orion"},
        {"$set": orion_doc},
        upsert=True,
    )
    logger.info("Orion employee record upserted")

    # ── 3. Ingest knowledge base files ──
    kb_count = 0
    for entry in KNOWLEDGE_FILES:
        md_path = KNOWLEDGE_DIR / entry["filename"]
        if not md_path.exists():
            logger.warning("Orion KB file not found: %s", md_path)
            continue

        content = md_path.read_text(encoding="utf-8")
        await db.ai_employee_knowledge.update_one(
            {"knowledge_id": entry["knowledge_id"]},
            {
                "$set": {
                    "knowledge_id": entry["knowledge_id"],
                    "employee_id": "ai_orion",
                    "title": entry["title"],
                    "module_number": None,
                    "content": content,
                    "quiz_payload": None,
                    "content_type": "markdown",
                    "tags": entry["tags"],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                "$setOnInsert": {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            },
            upsert=True,
        )
        kb_count += 1

    # ── 4. Register Orion in the conductor ──
    try:
        from services.ai.agents.marketplace import register_ai_employee

        orion_full = await db.ai_employees.find_one(
            {"employee_id": "ai_orion"}, {"_id": 0}
        )
        if orion_full:
            await register_ai_employee(db, orion_full)
    except Exception:
        logger.warning("Orion agent registration during seed", exc_info=True)

    # ── 5. Sync the matching `ai_employee_definitions` row ──
    # Phase 1E.3e — keeps the catalog blueprint's `default_*` fields in
    # lockstep with the live `ai_employees.ai_orion` row so future tenant
    # provisions get the latest prompt/model/tools. Existing tenant
    # instances are NOT retroactively updated. See `_definition_sync.py`
    # for the full rationale.
    try:
        from seeds._definition_sync import sync_definition_defaults_from_employee
        await sync_definition_defaults_from_employee(db, "ai_orion")
    except Exception:
        logger.warning(
            "Orion definition sync during seed (non-fatal)", exc_info=True,
        )

    logger.info("Orion seed complete: %d knowledge base files ingested", kb_count)
