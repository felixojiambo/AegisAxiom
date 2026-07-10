"""Seed script for Vega (AI Recruiter Agent).

Creates the Vega AI employee record and ingests knowledge base content
(system prompt, power questions, gold standard template, BPMN reference,
curriculum modules, and MMI scoring worksheet) into ai_employee_knowledge.

Idempotent — uses upserts keyed on employee_id and knowledge_id.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

VEGA_KNOWLEDGE_DIR = Path(__file__).parent / "vega_knowledge"
CURRICULUM_DIR = Path(__file__).parent / "arc_curriculum"


async def seed_vega(db):
    """Seed Vega employee + knowledge base content. Idempotent."""

    # ── 1. Read system prompt ──
    system_prompt_path = VEGA_KNOWLEDGE_DIR / "system_prompt.md"
    system_prompt = (
        system_prompt_path.read_text(encoding="utf-8")
        if system_prompt_path.exists()
        else "You are Vega. The knowledge base is being loaded."
    )

    # ── 2. Upsert Vega employee doc ──
    vega_doc = {
        "employee_id": "ai_vega",
        "name": "Vega",
        "role": "AI Recruiter Agent",
        "description": (
            "Partners with certified sales reps during AI discovery calls — "
            "analyzes transcripts, builds AI Employee matrices, generates BPMN "
            "diagrams, runs MMI scoring, and hands off to Orion for SOW drafting."
        ),
        "avatar": "🔍",
        "type": "conversational",
        "status": "active",
        "mode": "manual",
        # Conversational-specific fields
        "system_prompt": system_prompt,
        "router_description": (
            "questions about analyzing sales discovery call transcripts, "
            "identifying AI workflow opportunities, building AI Employee "
            "matrices, generating BPMN process diagrams, running MMI scoring, "
            "or managing the 3-call discovery process"
        ),
        "model_provider": "google",
        "model_name": "gemini-3-flash-preview",
        "auto_greeting": True,
        "knowledge_base": {
            "collection": "ai_employee_knowledge",
            "filter": {"employee_id": "ai_vega"},
        },
        "tools": [
            "render_artifact",
            "update_user_state",
            "ingest_transcript",
            "save_vega_session",
            "compute_mmi_score",
            "generate_bpmn_diagram",
            "load_vega_session",
            "graduate_freeflow_to_call1",
            "run_vega_analysis",
        ],
        "state_schema": {
            "active_session_id": None,
        },
        # Standard AI employee fields
        "connection": {},
        "metrics_config": {
            "metric1_label": "Transcripts Analyzed",
            "metric2_label": "BPMN Diagrams Generated",
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
            "discovery",
            "transcript_analysis",
            "bpmn",
            "mmi_scoring",
            "ai_recruiter",
        ],
        "contractor": {"name": "", "agreement_id": ""},
        "webhook_token": "",
        "created_by": "system",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Split the upsert payload into two classes:
    #
    #   $setOnInsert (preserved on every restart): tools list, metrics
    #     counters, execution log, created_at/created_by. These represent
    #     *live state* on the doc — operator-managed tool whitelist, plus
    #     usage counters that accumulate across the lifetime of the doc.
    #     Re-asserting them on every deploy silently rolls back any in-
    #     place edit (manual tool list update, ongoing usage counters).
    #
    #   $set (refreshed on every restart): identity, prompts, model
    #     selection, knowledge_base config, specialties. These represent
    #     *latest-code-wins* configuration. We want them to track the
    #     deployed branch.
    #
    # Why this matters: dev's auto-deploy on each PR-to-dev merge runs
    # seed_vega against Orion at backend startup. With everything in $set,
    # every merge clobbered Vega's tools list back to whatever was on the
    # deploying branch — wiping any prior migration. Repro window seen on
    # 2026-05-14: PR #176 merged at 08:42:43 UTC, ai_vega doc recreated at
    # 08:48:06 UTC with the deprecated 9-tool list, breaking Vega for ~5min
    # until traffic happened to land on a fresh request.
    PRESERVE_ON_INSERT_KEYS = {
        "tools",
        "metrics",
        "execution_count",
        "success_count",
        "error_count",
        "last_execution_at",
        "execution_log",
        "created_at",
        "created_by",
    }
    set_payload = {k: v for k, v in vega_doc.items() if k not in PRESERVE_ON_INSERT_KEYS}
    set_on_insert_payload = {k: v for k, v in vega_doc.items() if k in PRESERVE_ON_INSERT_KEYS}

    await db.ai_employees.update_one(
        {"employee_id": "ai_vega"},
        {
            "$set": set_payload,
            "$setOnInsert": set_on_insert_payload,
        },
        upsert=True,
    )
    logger.info("Vega employee record upserted (tools + counters preserved on restart)")

    # ── 3. Ingest Vega-specific knowledge base files ──
    vega_kb_files = [
        ("kb_vega_system_prompt", "system_prompt.md", "Vega System Prompt", ["system_prompt"]),
        ("kb_vega_power_questions", "power_questions.md", "Power Questions Reference", ["power_questions", "call_1"]),
        ("kb_vega_gold_standard", "gold_standard_template.md", "Gold Standard Discovery Report Template", ["gold_standard", "template"]),
        ("kb_vega_bpmn_ref", "bpmn_element_reference.md", "BPMN Element Reference", ["bpmn", "reference"]),
    ]

    kb_count = 0
    for knowledge_id, filename, title, tags in vega_kb_files:
        filepath = VEGA_KNOWLEDGE_DIR / filename
        if not filepath.exists():
            logger.warning("Vega knowledge file not found: %s", filepath)
            continue

        content = filepath.read_text(encoding="utf-8")
        await db.ai_employee_knowledge.update_one(
            {"knowledge_id": knowledge_id},
            {
                "$set": {
                    "knowledge_id": knowledge_id,
                    "employee_id": "ai_vega",
                    "title": title,
                    "module_number": None,
                    "content": content,
                    "quiz_payload": None,
                    "content_type": "markdown",
                    "tags": tags,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                "$setOnInsert": {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            },
            upsert=True,
        )
        kb_count += 1

    # ── 4. Clean up any previously ingested curriculum modules ──
    # Vega doesn't need ARC's curriculum in its prompt — the system prompt and
    # Vega-specific knowledge files (power questions, gold standard, BPMN ref)
    # are sufficient. Curriculum modules were causing the LLM to behave like ARC.
    reading_count = 0
    deleted = await db.ai_employee_knowledge.delete_many({
        "employee_id": "ai_vega",
        "knowledge_id": {"$regex": "^kb_vega_m[0-9]$"},
    })
    if deleted.deleted_count:
        logger.info("Cleaned up %d stale curriculum entries for Vega", deleted.deleted_count)

    # ── 5. Ingest MMI scoring worksheet ──
    mmi_path = CURRICULUM_DIR / "mmi_scoring.xlsx"
    if mmi_path.exists():
        try:
            import openpyxl

            wb = openpyxl.load_workbook(mmi_path, data_only=True)
            ws = wb.active
            rows = []
            for row in ws.iter_rows(values_only=True):
                rows.append(
                    ",".join(str(cell) if cell is not None else "" for cell in row)
                )
            csv_content = "\n".join(rows)

            await db.ai_employee_knowledge.update_one(
                {"knowledge_id": "kb_vega_mmi_csv"},
                {
                    "$set": {
                        "knowledge_id": "kb_vega_mmi_csv",
                        "employee_id": "ai_vega",
                        "title": "MMI Scoring Worksheet",
                        "module_number": None,
                        "content": csv_content,
                        "quiz_payload": None,
                        "content_type": "csv",
                        "tags": ["global", "mmi_calculation", "scoring"],
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                    "$setOnInsert": {
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                },
                upsert=True,
            )
            logger.info("MMI scoring worksheet ingested for Vega")
        except ImportError:
            logger.warning("openpyxl not installed — skipping MMI xlsx ingestion")
        except Exception:
            logger.warning("MMI ingestion failed for Vega", exc_info=True)

    # ── 6. Register Vega in the conductor ──
    try:
        from services.ai.agents.marketplace import register_ai_employee

        vega_full = await db.ai_employees.find_one(
            {"employee_id": "ai_vega"}, {"_id": 0}
        )
        if vega_full:
            await register_ai_employee(db, vega_full)
    except Exception:
        logger.warning("Vega agent registration during seed", exc_info=True)

    # ── 7. Sync the matching `ai_employee_definitions` row ──
    # Phase 1E.3e — keeps the catalog blueprint's `default_*` fields in
    # lockstep with the live `ai_employees.ai_vega` row so future tenant
    # provisions get the latest prompt/model/tools. Existing tenant
    # instances are NOT retroactively updated — they're snapshots
    # authoritative for their own configuration. See
    # `_definition_sync.py` module docstring for the full rationale.
    try:
        from seeds._definition_sync import sync_definition_defaults_from_employee
        await sync_definition_defaults_from_employee(db, "ai_vega")
    except Exception:
        logger.warning(
            "Vega definition sync during seed (non-fatal)", exc_info=True,
        )

    logger.info(
        "Vega seed complete: %d KB files, %d curriculum readings",
        kb_count,
        reading_count,
    )
