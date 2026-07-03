"""Seed script for ARC (AI Recruiter Curriculum Teacher).

Creates the ARC AI employee record and ingests curriculum content
(module readings, quizzes, final test) into ai_employee_knowledge.

Idempotent — uses upserts keyed on employee_id and knowledge_id.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CURRICULUM_DIR = Path(__file__).parent / "arc_curriculum"


async def seed_arc(db):
    """Seed ARC employee + curriculum content. Idempotent."""

    # ── 1. Read system prompt ──
    system_prompt_path = CURRICULUM_DIR / "system_prompt.md"
    system_prompt = (
        system_prompt_path.read_text(encoding="utf-8")
        if system_prompt_path.exists()
        else "You are ARC. The curriculum is being loaded."
    )

    # ── 2. Upsert ARC employee doc ──
    arc_doc = {
        "employee_id": "ai_arc",
        "name": "ARC",
        "role": "AI Recruiter Curriculum Teacher",
        "description": (
            "Walks sales reps and technical advisors through the "
            "AI Recruiter Certification program — 7 modules plus a final test."
        ),
        "avatar": "🎓",
        "type": "conversational",
        "status": "active",
        "mode": "manual",
        # Conversational-specific fields
        "system_prompt": system_prompt,
        "router_description": (
            "questions about AI Recruiter certification, training, modules, "
            "quizzes, or starting/continuing the AI Recruiter program"
        ),
        "model_provider": "google",
        "model_name": "gemini-3-flash-preview",
        "knowledge_base": {
            "collection": "ai_employee_knowledge",
            "filter": {"employee_id": "ai_arc"},
        },
        "tools": [
            "render_artifact",
            "update_user_state",
            "grade_quiz",
            "award_certification",
        ],
        "state_schema": {
            "current_module": 1,
            "modules_completed": [],
            "module_quiz_scores": {},
            "final_test_score": None,
            "certified": False,
            "quiz_in_progress": None,
        },
        # Standard AI employee fields
        "connection": {},
        "metrics_config": {
            "metric1_label": "Modules Taught",
            "metric2_label": "Avg Quiz Score",
            "metric3_label": "Certifications Issued",
            "source": "computed",
        },
        "metrics": {"metric1": 0, "metric2": 0, "metric3": 0},
        "execution_count": 0,
        "success_count": 0,
        "error_count": 0,
        "last_execution_at": None,
        "execution_log": [],
        "specialties": ["training", "certification", "ai_recruiter"],
        "contractor": {"name": "", "agreement_id": ""},
        "webhook_token": "",
        "created_by": "system",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    await db.ai_employees.update_one(
        {"employee_id": "ai_arc"},
        {"$set": arc_doc},
        upsert=True,
    )
    logger.info("ARC employee record upserted")

    # ── 3. Ingest module readings ──
    reading_count = 0
    for n in range(1, 8):
        md_path = CURRICULUM_DIR / f"module_{n}.md"
        if not md_path.exists():
            logger.warning("Module %d reading not found: %s", n, md_path)
            continue

        content = md_path.read_text(encoding="utf-8")
        await db.ai_employee_knowledge.update_one(
            {"knowledge_id": f"kb_arc_m{n}"},
            {
                "$set": {
                    "knowledge_id": f"kb_arc_m{n}",
                    "employee_id": "ai_arc",
                    "title": f"Module {n} Reading",
                    "module_number": n,
                    "content": content,
                    "quiz_payload": None,
                    "content_type": "markdown",
                    "tags": [f"module_{n}", "reading"],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                "$setOnInsert": {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            },
            upsert=True,
        )
        reading_count += 1

    # ── 4. Ingest module quizzes ──
    quiz_count = 0
    for n in range(1, 8):
        # Try both naming patterns
        quiz_path = CURRICULUM_DIR / f"module_{n}_quiz.json"
        if not quiz_path.exists():
            quiz_path = CURRICULUM_DIR / f"module_{n}_quiz_1.json"
        if not quiz_path.exists():
            logger.warning("Module %d quiz not found", n)
            continue

        payload = json.loads(quiz_path.read_text(encoding="utf-8"))
        await db.ai_employee_knowledge.update_one(
            {"knowledge_id": f"kb_arc_m{n}_quiz"},
            {
                "$set": {
                    "knowledge_id": f"kb_arc_m{n}_quiz",
                    "employee_id": "ai_arc",
                    "title": f"Module {n} Quiz",
                    "module_number": n,
                    "content": "",
                    "quiz_payload": payload,
                    "content_type": "json",
                    "tags": ["quiz", f"module_{n}"],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                "$setOnInsert": {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            },
            upsert=True,
        )
        quiz_count += 1

    # ── 5. Ingest final test ──
    final_path = CURRICULUM_DIR / "final_test.json"
    if final_path.exists():
        payload = json.loads(final_path.read_text(encoding="utf-8"))
        await db.ai_employee_knowledge.update_one(
            {"knowledge_id": "kb_arc_final_test"},
            {
                "$set": {
                    "knowledge_id": "kb_arc_final_test",
                    "employee_id": "ai_arc",
                    "title": "AI Recruiter Final Certification Test",
                    "module_number": None,
                    "content": "",
                    "quiz_payload": payload,
                    "content_type": "json",
                    "tags": ["quiz", "final_test"],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                "$setOnInsert": {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            },
            upsert=True,
        )
        logger.info("Final test ingested")

    # ── 6. Ingest MMI scoring worksheet ──
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
                {"knowledge_id": "kb_arc_mmi_csv"},
                {
                    "$set": {
                        "knowledge_id": "kb_arc_mmi_csv",
                        "employee_id": "ai_arc",
                        "title": "MMI Scoring Worksheet",
                        "module_number": 6,
                        "content": csv_content,
                        "quiz_payload": None,
                        "content_type": "csv",
                        "tags": ["module_6", "mmi_calculation"],
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                    "$setOnInsert": {
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                },
                upsert=True,
            )
            logger.info("MMI scoring worksheet ingested")
        except ImportError:
            logger.warning("openpyxl not installed — skipping MMI xlsx ingestion")
        except Exception:
            logger.warning("MMI ingestion failed", exc_info=True)

    # ── 7. Register ARC in the conductor ──
    try:
        from services.ai.agents.marketplace import register_ai_employee

        arc_full = await db.ai_employees.find_one(
            {"employee_id": "ai_arc"}, {"_id": 0}
        )
        if arc_full:
            await register_ai_employee(db, arc_full)
    except Exception:
        logger.warning("ARC agent registration during seed", exc_info=True)

    # ── 8. Sync the matching `ai_employee_definitions` row ──
    # Phase 1E.3e — keeps the catalog blueprint's `default_*` fields in
    # lockstep with the live `ai_employees.ai_arc` row so future tenant
    # provisions get the latest prompt/model/tools. Existing tenant
    # instances are NOT retroactively updated. See `_definition_sync.py`
    # for the full rationale.
    try:
        from seeds._definition_sync import sync_definition_defaults_from_employee
        await sync_definition_defaults_from_employee(db, "ai_arc")
    except Exception:
        logger.warning(
            "ARC definition sync during seed (non-fatal)", exc_info=True,
        )

    logger.info(
        "ARC seed complete: %d readings, %d quizzes", reading_count, quiz_count
    )
