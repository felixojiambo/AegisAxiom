"""LLM-callable tools for conversational AI employees.

The conductor calls get_tool_definitions() to build the tool list for Gemini,
and execute_tool() to run a tool when Gemini returns a function_call.
"""

import json
import re
import uuid
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from services.vega_events import emit_vega_event
from services.audit_service import ai_actor_ctx
from services.observability import report_exception

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# v1.3 Theme B — Tier-2 hard confirmation guard
#
# The Tier-2 set: tools that create new entities, touch many records, or are
# destructive. The v1.2 ADR (Q2) left confirmation as a prompt-instruction
# contract — prompt injection or hallucinated auto-confirmation could let the
# LLM execute these without user say-so. v1.3 enforces it at dispatch.
#
# Tools NOT in this set (e.g. update_task_status, assign_task,
# add_task_comment, update_task_field) auto-execute because they are single-
# target and reversible.
# ---------------------------------------------------------------------------
TIER_2_TOOLS: frozenset[str] = frozenset({
    "create_project",
    "create_project_from_template",
    "create_task",
    "bulk_update_task_status",
    "bulk_assign_tasks",
    "delete_task",
})


# The full PMO write-tool set — used to scope the ai_actor_ctx stamping in
# execute_tool. Read-only PMO tools (summarize_recent_changes) don't need
# the stamp because they don't write through crud_envelope.
_PMO_WRITE_TOOLS: frozenset[str] = frozenset({
    "update_task_status",
    "update_task_field",
    "assign_task",
    "add_task_comment",
    "create_project",
    "create_project_from_template",
    "create_task",
    "bulk_update_task_status",
    "bulk_assign_tasks",
    "delete_task",
})


# Confirmation matcher. Permissive on purpose — the guard is about preventing
# *surprise* execution, not enforcing a separate-message handshake. Words
# match as standalone tokens (word-boundary regex) so "do it" doesn't fire on
# "do it later" intent words and "yes" doesn't fire on "yesterday".
#
# Same-turn confirmation works: "yes, delete that task" passes because "yes"
# appears in the user message that triggered the tool call.
#
# Wave 3.5 tightening: review surfaced false-positive patterns where the
# matcher fired on questions and negations:
#   * "is it ok if I change my mind?"       — matches "ok"
#   * "I wouldn't do it"                    — matches "do it"
#   * "no, don't proceed"                   — matches "proceed"
#
# Fix approach: instead of per-word negative lookbehinds (brittle), we
# tokenize the message into sentences (split on .!?) and only inspect the
# trailing 1-2 sentences. Confirmation is almost always at the END of the
# user's last message ("...sure, go ahead." / "Yes, do it."). Questions
# ("is it ok if...?") and negations earlier in the message are filtered
# out structurally rather than per-word.
#
# Additional guard: a sentence that ends with `?` is treated as a question
# and NEVER counted as confirmation, even if it contains confirm tokens.
_CONFIRM_PATTERNS = [
    r"\bconfirm(?:ed|s|ing)?\b",
    r"\byes\b",
    r"\byep\b",
    r"\byeah\b",
    # Bare `\bgo\b` removed (PR #168 review): too broad — fires on
    # "let's go through the backlog" / "go check the Gantt while I wait".
    # `\bgo ahead\b` below is the precise variant we want.
    r"\bapprov(?:e|ed|es|ing)\b",
    r"\bproceed\b",
    r"\bdo it\b",
    r"\bok\b",
    r"\bokay\b",
    r"\bsure(?:\s+thing)?\b",
    r"\bsounds (?:good|great)\b",
    r"\blooks good\b",
    r"\bgo ahead\b",
    r"\bplease do\b",
]
_CONFIRM_RE = re.compile("|".join(_CONFIRM_PATTERNS), flags=re.IGNORECASE)

# Negation tokens that appear in a confirming-looking sentence flip the result.
# Example: "no, don't proceed" tokenizes to a single sentence with both "no"
# and "proceed" — without negation handling we'd false-positive.
_NEGATION_RE = re.compile(
    r"\b(?:no|not|don'?t|do\s*not|won'?t|wouldn'?t|cannot|can'?t|never|stop|wait|hold\s+on)\b",
    flags=re.IGNORECASE,
)

# Number of trailing sentences from each user message to inspect. We check
# ONLY the last sentence — the user's final decision lives there. Inspecting
# earlier sentences re-introduces ambiguity ("Yes. But actually, do not
# proceed." — the trailing decision wins, the leading "Yes." is stale intent).
_TRAILING_SENTENCES = 1


def _trailing_sentences(text: str, n: int = _TRAILING_SENTENCES) -> list[str]:
    """Return up to `n` trailing sentences from `text`, in original order.

    Uses a simple terminator split (.!?) — good enough for confirmation
    matching, intentionally not a full NLP sentence tokenizer. Empty
    fragments (from trailing punctuation) are filtered out.
    """
    if not text:
        return []
    # Keep the terminator with each sentence so question-mark detection works
    # downstream. Split on whitespace following a terminator.
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return []
    return parts[-n:]


def _is_confirmation(text: str) -> bool:
    """True iff `text` looks like an affirmative confirmation.

    A confirming message:
      1. Has at least one trailing sentence that contains a confirm token,
      2. That sentence is NOT a question (does not end with `?`),
      3. That sentence does NOT contain a negation token (no/not/don't/
         wouldn't/etc.) — those flip the meaning even with a confirm token.
    """
    if not text:
        return False
    for sentence in _trailing_sentences(text):
        # Questions don't count — "is it ok if I change my mind?" is a query,
        # not a confirmation.
        if sentence.rstrip().endswith("?"):
            continue
        if not _CONFIRM_RE.search(sentence):
            continue
        # Negation in the same sentence flips the meaning: "I wouldn't do it",
        # "no, don't proceed", "not yet — wait".
        if _NEGATION_RE.search(sentence):
            continue
        return True
    return False


async def _has_recent_confirmation(db, session_id: str | None, turns: int = 2) -> bool:
    """Return True if any of the last ``turns`` user messages contain a
    confirmation token in their trailing sentence(s).

    Looks at the most recent user-role messages in the chat session, newest
    first. If no session_id is supplied OR the lookup fails, returns False
    (fail-closed — better to ask again than auto-execute Tier-2).
    """
    if not session_id:
        return False

    # Defuse the latent asymmetry the reviewer flagged: turns=0 would limit(1)
    # but to_list(0) returns []. Normalize to the same clamp on both sides
    # so any future caller can pass turns=0 without a silent-empty surprise.
    clamped = max(1, turns)

    try:
        cursor = db.chat_messages.find(
            {"session_id": session_id, "role": "user"},
            {"_id": 0, "content": 1, "created_at": 1},
        ).sort("created_at", -1).limit(clamped)
        msgs = await cursor.to_list(clamped)
    except Exception as e:  # noqa: BLE001
        logger.warning("Tier-2 confirmation lookup failed for session=%s: %s", session_id, e)
        return False
    for m in msgs:
        content = m.get("content") or ""
        if _is_confirmation(content):
            return True
    return False


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ── Tool Schemas (JSON Schema dicts for Gemini function-calling) ──

TOOL_SCHEMAS = {
    "render_artifact": {
        "name": "render_artifact",
        "description": (
            "Render an interactive artifact in the chat. Supports: reading, quiz_form (ARC), "
            "single_select, multi_upload, transcript_analysis, ai_employee_matrix, agent_handoff (Vega), "
            "sow_draft, sow_section_edit, sow_finalize_preview (Orion). "
            "For Vega/Orion types, pass the full payload object. For reading/quiz_form, pass module_number."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "artifact_type": {
                    "type": "string",
                    "description": "Type of artifact to render.",
                    "enum": [
                        "reading", "quiz_form",
                        "single_select", "multi_upload", "transcript_analysis",
                        "ai_employee_matrix", "agent_handoff",
                        "sow_draft", "sow_section_edit", "sow_finalize_preview",
                        "bpmn_viewer",
                    ],
                },
                "module_number": {
                    "type": "integer",
                    "description": "Module number (1-7) or 0 for final test. Only for reading/quiz_form.",
                },
                "payload": {
                    "type": "object",
                    "description": "The artifact payload data. Required for Vega/Orion artifact types.",
                },
            },
            "required": ["artifact_type"],
        },
    },
    "update_user_state": {
        "name": "update_user_state",
        "description": (
            "Update the user's training progress state. Use this when the user advances "
            "to a new module, completes a quiz, or any other state change."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "state_updates": {
                    "type": "object",
                    "description": (
                        "Key-value pairs to merge into the user's state. "
                        'Example: {"current_module": 2, "modules_completed": [1]}'
                    ),
                },
            },
            "required": ["state_updates"],
        },
    },
    "grade_quiz": {
        "name": "grade_quiz",
        "description": (
            "Grade a submitted quiz. Loads the artifact, evaluates user responses "
            "against correct answers, and returns scored results with per-question feedback."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "artifact_id": {
                    "type": "string",
                    "description": "The artifact_id of the submitted quiz to grade",
                },
            },
            "required": ["artifact_id"],
        },
    },
    "award_certification": {
        "name": "award_certification",
        "description": (
            "Award the AI Recruiter Certification to the user after they pass the final test."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "certification_type": {
                    "type": "string",
                    "description": "Type of certification to award",
                    "enum": ["ai_recruiter"],
                },
                "module_scores": {
                    "type": "object",
                    "description": 'Dict of module number to quiz score, e.g. {"1": 85, "2": 92}',
                },
                "final_test_score": {
                    "type": "number",
                    "description": "Score on the final certification test (0-100)",
                },
            },
            "required": ["certification_type", "final_test_score"],
        },
    },
    # ── Vega Tools ──
    "ingest_transcript": {
        "name": "ingest_transcript",
        "description": (
            "Load a previously uploaded transcript from chat_uploads by its upload_id. "
            "Returns the extracted text so you can read and analyze it on demand."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "upload_id": {"type": "string", "description": "The upload_id from a chat upload"},
            },
            "required": ["upload_id"],
        },
    },
    "save_vega_session": {
        "name": "save_vega_session",
        "description": (
            "Create or update an Vega discovery session. Merges the provided fields into "
            "the existing session document. Use this after analyzing a call to persist "
            "the analysis, matrix, and session state."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Vega session ID (vega_xxx). Omit to create a new session."},
                "updates": {"type": "object", "description": "Fields to merge into the session document."},
            },
            "required": ["updates"],
        },
    },
    "name_vega_session": {
        "name": "name_vega_session",
        "description": (
            "Set the session_name on the current Vega discovery session so the "
            "user's sidebar shows a meaningful label instead of "
            "'Untitled session'. Call this AS SOON AS you learn the customer "
            "name or topic — typically after the rep's first 1-2 messages "
            "describing the opportunity. Idempotent: calling again with a "
            "better name overrides the previous one. The name should be "
            "concise: '<Customer> — <topic>' (e.g., 'AppDirect — billing "
            "automation') or just the customer name if topic is unclear."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_name": {
                    "type": "string",
                    "description": "Concise human-readable name. Max 140 chars. Required.",
                },
            },
            "required": ["session_name"],
        },
    },
    "graduate_freeflow_to_call1": {
        "name": "graduate_freeflow_to_call1",
        "description": (
            "Wrap the current freeflow conversation into a Call 1 analysis. "
            "Synthesizes the chat history into a transcript-shaped string, runs "
            "run_transcript_analysis on it, and returns the analysis result. "
            "Use this ONLY when the user has explicitly accepted the graduation "
            "offer (e.g., 'yes wrap it up', 'go ahead', 'do it'). Takes no arguments."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "run_vega_analysis": {
        "name": "run_vega_analysis",
        "description": (
            "Dispatch one of Vega's analysis pipelines on the transcripts attached "
            "to this chat session. ALWAYS use this tool when the rep asks for a "
            "Call 1 / Call 2 / Call 3 / full analysis on uploaded transcripts. "
            "Do NOT attempt to generate the matrix inline — that path times out at "
            "60s. This tool runs the proper backend pipeline (300s budget) and "
            "returns a summary so you can post a final message with an "
            "`[[artifact:…]]` marker.\n\n"
            "Routing:\n"
            "  - call_number=1 → run_transcript_analysis(call_number=1) — Call 1 "
            "matrix scaffold from a single transcript\n"
            "  - call_number=2 → run_call2_analysis — deep-dive matrix + per-workflow BPMN + MMI hypotheses\n"
            "  - call_number=3 → run_call3_analysis — final matrix + MMI scoring + AI Design Documents + App Designs\n"
            "  - call_number='full' → run_single_call_pipeline — single-pass full deliverable from all attached transcripts\n\n"
            "If the rep didn't specify which call, ask them — don't guess from filename."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "call_number": {
                    "type": "string",
                    "description": "Which analysis to run. (One of: 1, 2, 3, full)",
                },
                "upload_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional explicit upload_ids. If omitted, all upload_ids in this chat session are used.",
                },
            },
            "required": ["call_number"],
        },
    },
    "compute_mmi_score": {
        "name": "compute_mmi_score",
        "description": (
            "Compute the MMI score for a workflow using the formula: "
            "(TSS / IE) * (Time + Cost + Quality + Satisfaction). "
            "Each input is an integer 1-5. Returns the computed score."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tss": {"type": "integer", "description": "Technical Skill Score (1-5)"},
                "ie": {"type": "integer", "description": "Integration Effort (1-5)"},
                "time": {"type": "integer", "description": "Time/Speed impact (1-5)"},
                "cost": {"type": "integer", "description": "Cost/Efficiency impact (1-5)"},
                "quality": {"type": "integer", "description": "Quality/Accuracy impact (1-5)"},
                "satisfaction": {"type": "integer", "description": "Satisfaction/Culture impact (1-5)"},
            },
            "required": ["tss", "ie", "time", "cost", "quality", "satisfaction"],
        },
    },
    # ── BPMN Tool ──
    "generate_bpmn_diagram": {
        "name": "generate_bpmn_diagram",
        "description": (
            "Generate a BPMN 2.0 process diagram from workflow data. Takes structured "
            "workflow info (actors, trigger, steps, decision points) and produces rendered "
            "BPMN XML. If the data is incomplete, returns an error indicating missing fields."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_data": {
                    "type": "object",
                    "description": (
                        "Structured workflow data with workflow_id, workflow_name, actors, "
                        "trigger, steps, decision_points, and outputs."
                    ),
                },
            },
            "required": ["workflow_data"],
        },
    },
    # ── Orion Tools ──
    "load_vega_session": {
        "name": "load_vega_session",
        "description": (
            "Load an Vega discovery session by opportunity_id. Returns the verified workflow "
            "inventory, MMI matrix, and any prior Orion state."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "opportunity_id": {"type": "string", "description": "The Vega session_id to load"},
            },
            "required": ["opportunity_id"],
        },
    },
    "generate_sow_section": {
        "name": "generate_sow_section",
        "description": (
            "Generate a single SOW section (executive_summary, smart, milestones, billing, "
            "assumptions_risks) using the Vega session data as input context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "section": {"type": "string", "description": "Section to generate", "enum": ["executive_summary", "smart", "milestones", "billing", "assumptions_risks"]},
                "vega_session_id": {"type": "string", "description": "The Vega session to use as input"},
                "additional_context": {"type": "string", "description": "Optional user feedback or regeneration instructions"},
            },
            "required": ["section", "vega_session_id"],
        },
    },
    "update_sow_draft": {
        "name": "update_sow_draft",
        "description": "Persist a SOW section to the opportunity_sows collection. Idempotent.",
        "parameters": {
            "type": "object",
            "properties": {
                "opportunity_id": {"type": "string", "description": "The opportunity/session ID"},
                "section": {"type": "string", "description": "Which section to update"},
                "content": {"type": "string", "description": "The section content to save"},
            },
            "required": ["opportunity_id", "section", "content"],
        },
    },
    "render_sow_preview": {
        "name": "render_sow_preview",
        "description": "Render the current SOW draft as a sow_draft artifact in the chat for user review.",
        "parameters": {
            "type": "object",
            "properties": {
                "opportunity_id": {"type": "string", "description": "The opportunity/session ID"},
            },
            "required": ["opportunity_id"],
        },
    },
    "finalize_sow_to_google_docs": {
        "name": "finalize_sow_to_google_docs",
        "description": "Create a formatted Google Doc from the finalized SOW. Returns the doc URL.",
        "parameters": {
            "type": "object",
            "properties": {
                "opportunity_id": {"type": "string", "description": "The opportunity/session ID"},
            },
            "required": ["opportunity_id"],
        },
    },
    # ── PMO Write Tools (Wave 1 / Thread 1A) ──
    # Tier-1 (auto-execute) and Tier-2 (confirm-before-execute) tools that let
    # the PMO assistant act on projects/tasks on the user's behalf. See ADR
    # `.waves/projects-v1.2-adr-ai-write-confirmation.md` — the confirmation
    # gate lives in the pmo.py system prompt, NOT here. Tools always execute
    # when invoked; the LLM is taught to pause-and-summarize before calling
    # Tier-2 tools.
    "update_task_status": {
        "name": "update_task_status",
        "description": (
            "[Tier 1 — auto-execute] Update a single task's status. Reversible "
            "via the task drawer in one click. Status enum varies by project "
            "methodology: waterfall=(not_started|in_progress|blocked|complete), "
            "waves_threads=(scoped|in_progress|blocked|shipped|carried_forward), "
            "agile=(dynamic column id from project.methodology_config.columns). "
            "Use update_task_field for non-status fields."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "task_xxx — primary key of the task"},
                "status": {"type": "string", "description": "New status. Must be valid for the project's methodology."},
                "reason": {"type": "string", "description": "Optional human-readable reason (logged, not stored on the task)."},
            },
            "required": ["task_id", "status"],
        },
    },
    "update_task_field": {
        "name": "update_task_field",
        "description": (
            "[Tier 1 — auto-execute] Update a single non-status field on a task. "
            "Allowed fields: priority, assignee_id, start_date, due_date, title, "
            "description. For assignment by name, use assign_task instead. For "
            "status changes use update_task_status."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "task_xxx"},
                "field": {
                    "type": "string",
                    "description": "Whitelisted field name",
                    "enum": ["priority", "assignee_id", "start_date", "due_date", "title", "description"],
                },
                "value": {"description": "New value. Type depends on field."},
            },
            "required": ["task_id", "field", "value"],
        },
    },
    "assign_task": {
        "name": "assign_task",
        "description": (
            "[Tier 1 — auto-execute] Assign a single task to a user. Accepts either "
            "a user_id (uuid-prefixed) or a human name. Resolves names against the "
            "users collection (RBAC-scoped to assignable members). If the name is "
            "ambiguous, returns {error: 'ambiguous', candidates: [...]} so you can "
            "ask the user which person they meant — do NOT pick one yourself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "user_id_or_name": {
                    "type": "string",
                    "description": "user_id (preferred) or display name. Pass null/empty to UNASSIGN.",
                },
            },
            "required": ["task_id"],
        },
    },
    "add_task_comment": {
        "name": "add_task_comment",
        "description": (
            "[Tier 1 — auto-execute] Add a Markdown-formatted comment to a task on "
            "the user's behalf. The user is attributed as the author. Body is "
            "stored verbatim and rendered client-side. 10K char max."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "body": {"type": "string", "description": "Markdown body. 1–10000 chars."},
            },
            "required": ["task_id", "body"],
        },
    },
    "create_project": {
        "name": "create_project",
        "description": (
            "[Tier 2 — confirm first] Create a brand-new project. You MUST summarize "
            "your plan (name, methodology, dates, PM) and pause for explicit user "
            "confirmation ('yes', 'go ahead', 'confirm') BEFORE calling this tool. "
            "methodology is one of: waterfall, agile, waves_threads. Downstream "
            "effects: RBAC scoping, notifications, sprint scaffolding."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Project name"},
                "methodology": {
                    "type": "string",
                    "enum": ["waterfall", "agile", "waves_threads"],
                    "description": "Project methodology",
                },
                "service_line": {
                    "type": "string",
                    "enum": ["ucaas", "networking", "cloud", "security", "ai_product", "other"],
                    "description": "Service line for the project. Defaults to 'other' if unknown.",
                },
                "description": {"type": "string"},
                "due_date": {"type": "string", "description": "ISO date (target_end_date)"},
                "pm_user_id": {"type": "string", "description": "user_id of the PM. Defaults to caller if omitted."},
            },
            "required": ["name", "methodology"],
        },
    },
    "create_project_from_template": {
        "name": "create_project_from_template",
        "description": (
            "[Tier 2 — confirm first] Instantiate a new project from a saved template. "
            "Pulls methodology defaults + seeds the task tree from the template. "
            "REQUIRES explicit user confirmation before calling — multiple tasks "
            "get created in one go and they are not easy to undo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "template_id": {"type": "string", "description": "tmpl_xxx"},
                "name": {"type": "string"},
                "due_date": {"type": "string", "description": "target_end_date ISO date"},
                "pm_user_id": {"type": "string", "description": "user_id of the PM. Defaults to caller if omitted."},
            },
            "required": ["template_id", "name"],
        },
    },
    "create_task": {
        "name": "create_task",
        "description": (
            "[Tier 2 — confirm first if creating multiple] Create a single task in a "
            "project. Single-task creates with clear context can run immediately, "
            "but if the user asked for 'kickoff tasks' or anything that implies "
            "multiple tasks, ALWAYS summarize the plan and pause for confirmation.\n\n"
            "**Methodology context is REQUIRED** and varies by project_type:\n"
            "  - waterfall → `methodology_fields.phase` (one of the project's phase names)\n"
            "  - agile → `methodology_fields.column_id` (one of the project's columns; optionally `sprint_id`)\n"
            "  - waves_threads → `methodology_fields.thread_id` (the handler will derive wave_id + module_owned from the thread)\n\n"
            "If you don't already know the correct value, DO NOT GUESS — call create_task once without that field. "
            "The handler will return `{error: 'needs_methodology_context', available_threads/phases/columns: [...]}` so you can ask the user which one they meant, then call again with the answer. Never invent thread IDs / phase names — always pick from the returned list."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "title": {"type": "string"},
                "status": {"type": "string", "description": "Optional; defaults to methodology's initial state"},
                "assignee": {"type": "string", "description": "user_id or name (will be resolved)"},
                "due_date": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                "methodology_fields": {
                    "type": "object",
                    "description": (
                        "Methodology context. Required field depends on project_type: "
                        "{phase} for waterfall, {column_id, sprint_id?} for agile, "
                        "{thread_id} for waves_threads (wave_id + module_owned auto-derived). "
                        "If you don't know, omit and the handler will return the available options."
                    ),
                },
            },
            "required": ["project_id", "title"],
        },
    },
    "bulk_update_task_status": {
        "name": "bulk_update_task_status",
        "description": (
            "[Tier 2 — confirm first] Update status on N tasks in one call. ALWAYS "
            "summarize which tasks will be touched and pause for explicit user "
            "confirmation BEFORE calling. The response includes a _warning field "
            "you MUST surface to the user (number affected + failed tasks)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_ids": {"type": "array", "items": {"type": "string"}},
                "status": {"type": "string"},
            },
            "required": ["task_ids", "status"],
        },
    },
    "bulk_assign_tasks": {
        "name": "bulk_assign_tasks",
        "description": (
            "[Tier 2 — confirm first] Reassign N tasks to one user (or unassign by "
            "passing null). ALWAYS summarize and pause for confirmation. Response "
            "includes a _warning field — surface it to the user."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_ids": {"type": "array", "items": {"type": "string"}},
                "user_id_or_name": {"type": "string", "description": "user_id or name; pass null/empty to unassign"},
            },
            "required": ["task_ids"],
        },
    },
    "delete_task": {
        "name": "delete_task",
        "description": (
            "[Tier 2 — DESTRUCTIVE — confirm first] Permanently delete a task. "
            "Cannot be undone from the UI. ALWAYS summarize what is being deleted "
            "and require explicit user confirmation ('yes', 'delete it', 'confirm') "
            "BEFORE calling. The response includes a _warning field."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
        "_destructive": True,
    },
    "summarize_recent_changes": {
        "name": "summarize_recent_changes",
        "description": (
            "[Tier 1 — read-only] Fetch project + task mutations from the last N "
            "hours (status changes, assignments, creations). RBAC-scoped to the "
            "caller. Returns structured records you should narrate as prose. "
            "Default: 24 hours, all projects the user can see."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Optional — scope to a single project."},
                "hours": {"type": "integer", "description": "Look-back window in hours (default 24, max 168)."},
            },
            "required": [],
        },
    },
}


def get_tool_definitions(tool_names: list):
    """Return Gemini Tool objects for the requested tool names.

    Returns a list suitable for passing to GenerateContentConfig(tools=...).
    Returns None if no valid tools requested.
    """
    if not tool_names:
        return None

    from google.genai import types as genai_types

    declarations = []
    for name in tool_names:
        schema = TOOL_SCHEMAS.get(name)
        if schema:
            declarations.append(genai_types.FunctionDeclaration(
                name=schema["name"],
                description=schema["description"],
                parameters=schema["parameters"],
            ))

    if not declarations:
        return None

    return [genai_types.Tool(function_declarations=declarations)]


# ── Tool Executor ──


# Sentinel used in TOOL_REGISTRY to mark a tool as deliberately ungated.
# Distinct from None so that "forgot to declare" (= not registered) is
# never confused with "intentionally open" — the dispatcher fails closed
# on unregistered names rather than silently letting them through.
OPEN = object()


# Single source of truth for AI tool dispatch. Each entry binds a handler
# to its required user permission. Use OPEN for chat plumbing tools that
# should be available to any user already in chat. New tools — including
# tools registered by ingested apps — MUST add an entry here; the
# dispatcher refuses unknown tool names.
TOOL_REGISTRY: dict = {}


def _register_tool(name: str, handler, required_perm):
    """Internal: bind a tool name to its handler and required permission.

    Kept as a small helper so the registry block below reads as a flat
    list of declarations instead of dict-literal noise. The decorator
    pattern in permission_registry.py is for permission *catalogs* — this
    is a per-handler dispatch table, so a plain function call is the
    right level of ceremony.
    """
    TOOL_REGISTRY[name] = {"handler": handler, "perm": required_perm}


async def execute_tool(
    name: str,
    args: dict,
    db,
    user,
    session_id: str = None,
    employee_id: str = None,
) -> dict:
    """Execute a tool by name and return the result dict for FunctionResponse.

    Enforces the calling user's permission against TOOL_REGISTRY before
    dispatching. Failures are returned as soft error dicts (not raised)
    so the AI receives the denial through the function response and can
    explain it to the user instead of crashing the chat turn.

    v1.3 Theme B additions:
    - Hard guard on Tier-2 tools — refuses dispatch unless the last 2
      user turns contain an explicit confirmation token.
    - Sets the ai_actor_ctx contextvar so the route-level crud_envelope
      writes a single audit row stamped with actor_type=pmo_ai and
      tool_name (retires the dual-write pattern).
    """
    entry = TOOL_REGISTRY.get(name)
    if not entry:
        return {"error": f"Unknown tool: {name}"}

    # Permission gate. Lazy imports avoid pulling auth_helper into module
    # load order for the AI subsystem.
    from auth_helper import has_perm
    from services.security_logger import log_security_event

    required_perm = entry["perm"]
    if required_perm is not OPEN and not has_perm(user, required_perm):
        await log_security_event(
            event_type="authz_failed",
            severity="warning",
            user_id=getattr(user, "user_id", None),
            user_email=getattr(user, "email", None),
            resource="ai_tool",
            resource_id=name,
            action="execute_tool",
            outcome="denied",
            details={
                "required_permission": required_perm,
                "employee_id": employee_id,
                "session_id": session_id,
            },
        )
        return {
            "error": "permission_denied",
            "tool": name,
            "required_permission": required_perm,
            "detail": (
                f"You do not have permission to use this tool. "
                f"Required: {required_perm}."
            ),
        }

    # ── Tier-2 hard confirmation guard ───────────────────────────────────
    # Closes the ADR Q2 open issue: prompt injection or hallucination must
    # not be sufficient to execute destructive/creation/bulk tools. The LLM
    # receives the block as a function response and is expected to surface
    # the confirmation request to the user before retrying.
    if name in TIER_2_TOOLS:
        confirmed = await _has_recent_confirmation(db, session_id, turns=2)
        if not confirmed:
            logger.info(
                "Tier-2 hard guard refused dispatch: tool=%s session=%s user=%s",
                name, session_id, getattr(user, "user_id", None),
            )
            # Audit the block so it appears in the security log + helps tune
            # the confirmation matcher if false-positives surface in prod.
            try:
                await log_security_event(
                    event_type="tier_2_confirmation_required",
                    severity="info",
                    user_id=getattr(user, "user_id", None),
                    user_email=getattr(user, "email", None),
                    resource="ai_tool",
                    resource_id=name,
                    action="execute_tool",
                    outcome="blocked",
                    details={
                        "tool_name": name,
                        "session_id": session_id,
                        "employee_id": employee_id,
                    },
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("tier_2 block security_logger write failed: %s", e)
            return {
                "_blocked": True,
                "error": "tier_2_confirmation_required",
                "tool": name,
                "intended_params": args or {},
                "detail": (
                    "This action requires explicit user confirmation. "
                    "Summarize the plan to the user and wait for them to "
                    "reply with 'yes', 'confirm', 'go ahead', or similar "
                    "before retrying."
                ),
            }

    # ── Set AI actor context so crud_envelope stamps the activity row ───
    # The contextvar is scoped to this asyncio task; concurrent requests
    # can't see each other's stamps. We reset on the way out so a later
    # human-initiated write in the same task (unlikely but possible) sees
    # the default empty context.
    is_pmo_write = name in _PMO_WRITE_TOOLS
    tier = 2 if name in TIER_2_TOOLS else 1
    token = None
    if is_pmo_write:
        token = ai_actor_ctx.set({
            "actor_type": "pmo_ai",
            "tool_name": name,
            "tier": tier,
            "session_id": session_id,
        })

    try:
        return await entry["handler"](
            args, db, user, session_id=session_id, employee_id=employee_id
        )
    except Exception as exc:  # noqa: BLE001
        # Report uncaught handler exceptions to GCP Error Reporting with the
        # tool_name / tier / session for forensic context. The handler
        # itself is expected to catch + return an error dict for known
        # failure modes (HTTPException, validation, etc.) — this path
        # catches programmer errors / unexpected raises.
        report_exception(exc, context={
            "tool_name": name,
            "tier": tier,
            "user_id": getattr(user, "user_id", None),
            "user_email": getattr(user, "email", None),
            "session_id": session_id,
            "employee_id": employee_id,
        })
        raise
    finally:
        if token is not None:
            ai_actor_ctx.reset(token)


# ── Tool Handlers ──


async def _handle_render_artifact(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Create an artifact (reading or quiz) from the knowledge base."""
    artifact_type = args.get("artifact_type", "quiz_form")
    module_number = args.get("module_number", 1)

    # ── Vega/Orion/Nova artifact types — payload is provided directly by the LLM ──
    VEGA_ORION_TYPES = {
        "single_select", "multi_upload", "transcript_analysis",
        "ai_employee_matrix", "agent_handoff", "bpmn_viewer",
        "sow_draft", "sow_section_edit", "sow_finalize_preview",
        "cashflow_chart",
    }
    if artifact_type in VEGA_ORION_TYPES:
        payload = args.get("payload", {})

        # Wave 6b — multi_upload artifacts produced via this conversational
        # tool path are by definition conversationally generated (the
        # chip-driven intake flow takes a separate code path in
        # routes/ai_chat_artifacts.py). Tagging the payload here lets the
        # submit_artifact dispatcher route the eventual analysis through
        # run_v3_call1/2/3 instead of the legacy v2 pipelines.
        if artifact_type == "multi_upload" and isinstance(payload, dict):
            payload.setdefault("discovery_mode_origin", "conversational")

        # Guard: ai_employee_matrix MUST come from a real analysis pipeline,
        # not a free-form LLM call. The pipelines write the matrix directly
        # to chat_messages with the [[artifact:…]] marker; if the LLM tries
        # to render one inline with no real workflow data, refuse so we
        # don't ship an empty "0 workflows identified" artifact to the rep.
        if artifact_type == "ai_employee_matrix":
            wf_count = len((payload or {}).get("matrix") or [])
            if wf_count == 0:
                logger.warning(
                    "Refused to render empty ai_employee_matrix artifact "
                    "(employee=%s session=%s). Tell the rep what's missing instead.",
                    employee_id, session_id,
                )
                return {
                    "error": "ai_employee_matrix requires a non-empty matrix from an analysis pipeline. "
                             "Do NOT render this artifact inline. Either run run_single_call_pipeline / "
                             "run_call2_analysis / run_call3_analysis first, or answer the user in plain prose."
                }

        artifact_id = _gen_id("art")
        now = datetime.now(timezone.utc).isoformat()
        artifact = {
            "artifact_id": artifact_id,
            "session_id": session_id,
            "user_id": user.user_id,
            "employee_id": employee_id,
            "artifact_type": artifact_type,
            "payload": payload,
            "status": "delivered",
            "created_by_agent": employee_id,
            "created_at": now,
            "updated_at": now,
        }
        await db.ai_chat_artifacts.insert_one(artifact)
        logger.info(f"Created {artifact_type} artifact {artifact_id}")
        return {"artifact_id": artifact_id, "artifact_type": artifact_type}

    if artifact_type == "reading":
        # Fetch the module reading from knowledge base
        reading_doc = await db.ai_employee_knowledge.find_one(
            {"employee_id": employee_id, "module_number": module_number, "tags": "reading"},
            {"_id": 0},
        )
        if not reading_doc or not reading_doc.get("content"):
            return {"error": f"No reading found for module {module_number}"}

        artifact_id = _gen_id("art")
        now = datetime.now(timezone.utc).isoformat()
        artifact = {
            "artifact_id": artifact_id,
            "session_id": session_id,
            "user_id": user.user_id,
            "employee_id": employee_id,
            "artifact_type": "reading",
            "payload": {
                "title": reading_doc.get("title", f"Module {module_number}"),
                "module_number": module_number,
                "content": reading_doc["content"],
            },
            "status": "delivered",
            "created_at": now,
            "updated_at": now,
        }
        await db.ai_chat_artifacts.insert_one(artifact)
        logger.info(f"Created reading artifact {artifact_id} (module={module_number})")
        return {"artifact_id": artifact_id, "artifact_type": "reading", "module_number": module_number}

    # Quiz path
    if module_number == 0:
        quiz_doc = await db.ai_employee_knowledge.find_one(
            {"employee_id": employee_id, "tags": "final_test", "quiz_payload": {"$ne": None}},
            {"_id": 0},
        )
    else:
        quiz_doc = await db.ai_employee_knowledge.find_one(
            {"employee_id": employee_id, "module_number": module_number, "quiz_payload": {"$ne": None}},
            {"_id": 0},
        )

    if not quiz_doc or not quiz_doc.get("quiz_payload"):
        return {"error": f"No quiz found for module {module_number}"}

    artifact_id = _gen_id("art")
    now = datetime.now(timezone.utc).isoformat()

    artifact = {
        "artifact_id": artifact_id,
        "session_id": session_id,
        "user_id": user.user_id,
        "employee_id": employee_id,
        "artifact_type": artifact_type,
        "module_number": module_number,
        # Marker so award_certification can scope its prerequisite lookup
        # to the actual final test rather than any graded passing artifact.
        "is_final_test": artifact_type == "quiz_form" and module_number == 0,
        "payload": quiz_doc["quiz_payload"],
        "status": "pending",
        "user_response": None,
        "ai_feedback": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.ai_chat_artifacts.insert_one(artifact)

    # Set quiz_in_progress on user state
    if artifact_type == "quiz_form":
        await db.ai_employee_user_state.update_one(
            {"user_id": user.user_id, "employee_id": employee_id},
            {"$set": {"state.quiz_in_progress": artifact_id, "updated_at": now}},
            upsert=True,
        )

    logger.info("Created artifact %s (type=%s, module=%s)", artifact_id, artifact_type, module_number)
    return {"artifact_id": artifact_id, "artifact_type": artifact_type, "module_number": module_number}


async def _handle_update_user_state(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Upsert the user's training state with the given updates."""
    state_updates = args.get("state_updates", {})
    if not state_updates:
        return {"error": "No state_updates provided"}

    now = datetime.now(timezone.utc).isoformat()
    set_fields = {f"state.{k}": v for k, v in state_updates.items()}
    set_fields["updated_at"] = now

    await db.ai_employee_user_state.update_one(
        {"user_id": user.user_id, "employee_id": employee_id},
        {"$set": set_fields, "$setOnInsert": {"created_at": now, "state_id": _gen_id("state")}},
        upsert=True,
    )

    logger.info("Updated user state for %s/%s: %s", user.user_id, employee_id, list(state_updates.keys()))
    return {
        "updated": True,
        "fields": list(state_updates.keys()),
        "reminder": "State saved. Now give the user your text response — review quiz results if applicable, or introduce the next step.",
    }


async def _handle_grade_quiz(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Grade a submitted quiz artifact."""
    artifact_id = args.get("artifact_id")
    if not artifact_id:
        return {"error": "No artifact_id provided"}

    user_id = getattr(user, "user_id", None)
    if not user_id:
        return {"error": "Authenticated user has no user_id; cannot scope artifact"}

    # Ownership scope: a quiz can only be graded by the user who submitted
    # it. Using {"artifact_id": ...} alone would let any chat user grade
    # another user's quiz by guessing or reusing an ID.
    artifact = await db.ai_chat_artifacts.find_one(
        {"artifact_id": artifact_id, "user_id": user_id},
        {"_id": 0},
    )
    if not artifact:
        return {"error": f"Artifact {artifact_id} not found"}
    if artifact.get("status") != "submitted":
        return {"error": f"Artifact status is '{artifact.get('status')}', expected 'submitted'"}

    payload = artifact.get("payload", {})
    questions = payload.get("questions", [])
    user_responses = artifact.get("user_response") or {}
    passing_score = payload.get("passing_score", 75)

    total_points = 0
    earned_points = 0
    feedback = []

    for q in questions:
        qid = q.get("id")
        q_type = q.get("type")
        points = q.get("points", 10)
        total_points += points
        user_answer = user_responses.get(qid)
        correct = q.get("correct_answer")
        explanation = q.get("explanation", "")

        if q_type == "free_text":
            rubric = q.get("rubric", "")
            ft_result = await _grade_free_text(user_answer or "", rubric, points, db)
            earned = ft_result["score"]
            is_correct = earned >= points * 0.75
            explanation = ft_result.get("justification", "") or explanation
        elif q_type == "multi_select":
            correct_set = set(correct) if isinstance(correct, list) else {correct}
            user_set = set(user_answer) if isinstance(user_answer, list) else set()
            is_correct = correct_set == user_set
            earned = points if is_correct else 0
        elif q_type == "scenario_match":
            is_correct = isinstance(correct, dict) and isinstance(user_answer, dict) and correct == user_answer
            earned = points if is_correct else 0
        else:
            # multiple_choice, true_false — direct string comparison
            is_correct = str(user_answer).lower() == str(correct).lower()
            earned = points if is_correct else 0

        earned_points += earned
        feedback.append({
            "question_id": qid,
            "earned": earned,
            "max": points,
            "correct": is_correct,
            "explanation": explanation,
        })

    score_pct = round((earned_points / total_points) * 100) if total_points > 0 else 0
    passed = score_pct >= passing_score

    now = datetime.now(timezone.utc).isoformat()
    await db.ai_chat_artifacts.update_one(
        {"artifact_id": artifact_id},
        {"$set": {
            "status": "graded",
            "ai_feedback": {
                "score": score_pct,
                "passed": passed,
                "feedback": feedback,
                "earned": earned_points,
                "total": total_points,
            },
            "updated_at": now,
        }},
    )

    logger.info("Graded quiz %s: %d%% (passed=%s)", artifact_id, score_pct, passed)
    return {
        "score": score_pct,
        "passed": passed,
        "earned_points": earned_points,
        "total_points": total_points,
        "feedback": feedback,
    }


async def _grade_free_text(answer: str, rubric: str, max_points: int, db) -> dict:
    """Grade a free-text answer using an LLM sub-call. Returns {score, justification}."""
    if not answer.strip():
        return {"score": 0, "justification": "No answer provided."}

    try:
        from services.ai.llm import call_llm

        grading_prompt = (
            f"You are grading a student's free-text answer. "
            f"Score it from 0 to {max_points}.\n\n"
            f"RUBRIC:\n{rubric}\n\n"
            f'Return ONLY a JSON object: {{"score": <number>, "justification": "<2-3 sentence explanation of why this score was given, referencing specific parts of the answer>"}}'
        )

        resp = await call_llm(
            grading_prompt,
            f"Student's answer: {answer}",
            session_key="grading",
            timeout=20,
        )

        raw = (resp.text or "").strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        if not raw:
            # LLM returned empty — give a middle score and note it
            mid = max_points // 2
            return {"score": mid, "justification": "Unable to auto-grade this response. A partial score was assigned — your instructor can review."}

        result = json.loads(raw)
        score = min(max(int(result.get("score", 0)), 0), max_points)
        justification = result.get("justification", "")
        return {"score": score, "justification": justification}
    except Exception as e:
        logger.warning("Free-text grading failed: %s", e)
        # Give partial credit rather than 0 on grading errors
        mid = max_points // 2
        return {"score": mid, "justification": "Auto-grading encountered an issue. A partial score was assigned — your instructor can review."}


async def _handle_award_certification(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Award a certification to the user.

    The dispatcher leaves this tool OPEN so the AI can call it freely once
    the chat user has earned the cert. To resist prompt-injection attacks
    that trick the AI into invoking it without a real pass, this handler
    re-checks the authoritative source — at least one graded artifact for
    this user with ai_feedback.passed=True must exist. Without it, no
    certification document is written.
    """
    certification_type = args.get("certification_type", "ai_recruiter")
    module_scores = args.get("module_scores", {})
    final_test_score = args.get("final_test_score", 0)

    user_id = getattr(user, "user_id", None)
    if not user_id:
        return {"error": "Authenticated user has no user_id; cannot award certification"}

    # Authoritative check: the AI cannot self-attest a pass — there must be
    # a graded final-test artifact owned by this user, scoped to the
    # employee that emits the requested cert. Scoping by employee_id keeps
    # cert types from cross-pollinating: passing the AI Recruiter final
    # test cannot justify a different cert from a different employee. The
    # is_final_test marker excludes mid-module quizzes that may have also
    # been graded passing.
    #
    # NOTE: this check + the certifications.insert_one below are not
    # atomic. An admin could in theory invalidate the artifact between
    # them. The window is small enough we accept the gap rather than wrap
    # the pair in a Mongo transaction (which would require a replica set).
    artifact_query = {
        "user_id": user_id,
        "status": "graded",
        "ai_feedback.passed": True,
        "is_final_test": True,
    }
    if employee_id:
        artifact_query["employee_id"] = employee_id
    passing_artifact = await db.ai_chat_artifacts.find_one(
        artifact_query,
        {"_id": 0, "artifact_id": 1, "ai_feedback": 1},
    )
    if not passing_artifact:
        logger.warning(
            "award_certification refused for user=%s — no graded passing artifact found",
            user_id,
        )
        return {
            "error": "no_passing_artifact",
            "detail": (
                "Cannot award certification: no graded quiz with passed=True is "
                "on file for this user. Grade the final test first."
            ),
        }

    cert_id = _gen_id("cert")
    now = datetime.now(timezone.utc).isoformat()

    cert = {
        "certification_id": cert_id,
        "user_id": user_id,
        "user_name": user.name,
        "certification_type": certification_type,
        "issued_at": now,
        "issued_by": employee_id or "ai_arc",
        "module_scores": module_scores,
        "final_test_score": final_test_score,
        "source_artifact_id": passing_artifact.get("artifact_id"),
        "expires_at": None,
    }
    await db.certifications.insert_one(cert)

    # Update user state to certified
    await db.ai_employee_user_state.update_one(
        {"user_id": user_id, "employee_id": employee_id},
        {"$set": {
            "state.certified": True,
            "state.final_test_score": final_test_score,
            "updated_at": now,
        }},
    )

    logger.info("Awarded %s certification to %s (cert_id=%s)", certification_type, user.name, cert_id)
    return {"certification_id": cert_id, "certification_type": certification_type, "issued_at": now}


# ── Vega Tool Handlers ──


async def _handle_ingest_transcript(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Load a transcript from chat_uploads by upload_id."""
    upload_id = args.get("upload_id")
    if not upload_id:
        return {"error": "No upload_id provided"}

    doc = await db.chat_uploads.find_one(
        {"upload_id": upload_id, "user_id": user.user_id}, {"_id": 0}
    )
    if not doc:
        return {"error": f"Upload {upload_id} not found"}

    # The full transcript is already injected into the system prompt via
    # _load_transcript_context. Return a short confirmation so the LLM
    # proceeds to analyze and call render_artifact.
    text = doc.get("extracted_text", "")
    return {
        "upload_id": upload_id,
        "filename": doc.get("filename"),
        "char_count": len(text),
        "status": "LOADED — the full transcript text is already in your system prompt above. "
                  "Do NOT request it again. Proceed to analyze it and call render_artifact "
                  "with artifact_type='transcript_analysis' to create the editable analysis form.",
    }


async def _handle_graduate_freeflow_to_call1(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Synthesize chat history into a transcript and run Call 1 analysis."""
    if not session_id:
        return {"error": "graduate_freeflow_to_call1 requires a chat session_id"}

    # Pull the conversation so far. Filter to user + assistant turns; skip system,
    # tool calls, and INTAKE markers — the goal is to reconstruct the substance
    # of what the rep described, not the plumbing.
    history = await db.chat_messages.find(
        {"session_id": session_id, "user_id": user.user_id,
         "role": {"$in": ["user", "assistant"]}},
        {"_id": 0, "role": 1, "content": 1, "created_at": 1},
    ).sort("created_at", 1).to_list(500)

    if not history:
        return {"error": "No chat history yet — nothing to graduate."}

    transcript_lines = []
    for m in history:
        content = (m.get("content") or "").strip()
        if not content or content.startswith("[INTAKE SELECTION]") or content.startswith("[[artifact:"):
            continue
        speaker = "Rep" if m.get("role") == "user" else "Vega"
        transcript_lines.append(f"{speaker}: {content}")

    if not transcript_lines:
        return {"error": "No substantive conversation to graduate yet."}

    synthesized = "\n\n".join(transcript_lines)

    # Persist as a chat_uploads document so run_transcript_analysis can pick it up
    # via its existing upload_ids path. Owner-scoped same as any real upload.
    upload_id = f"upl_freeflow_{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)
    await db.chat_uploads.insert_one({
        "upload_id": upload_id,
        "user_id": user.user_id,
        "session_id": session_id,
        "filename": "freeflow_conversation.txt",
        "extracted_text": synthesized,
        "source": "freeflow_graduation",
        "created_at": now,
    })

    from services.ai.vega_analysis import run_transcript_analysis

    result = await run_transcript_analysis(
        db, user, [upload_id], call_number=1, session_id=None,
    )

    if result is None:
        return {"error": "Graduation analysis returned no result — try again."}
    if result.get("error"):
        return result

    payload = result.get("analysis")
    if not payload:
        return {"error": "Graduation analysis returned no payload."}

    await emit_vega_event(
        db, user.user_id, session_id, "freeflow_graduation_accepted",
        {"upload_id": upload_id, "transcript_chars": len(synthesized)},
    )

    return {
        "status": "graduated",
        "analysis": payload,
        "summary": result.get("text", "Call 1 analysis complete."),
        "synthesized_upload_id": upload_id,
    }


async def _handle_run_vega_analysis(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Dispatch one of Vega's analysis pipelines. Used when the rep asks for a
    Call 1 / Call 2 / Call 3 / full analysis on already-attached transcripts.

    Returns a dict the LLM can use to compose a final response. The
    conductor does NOT auto-render an artifact from this tool's return —
    the LLM is expected to insert an `[[artifact:<id>]]` marker referencing
    the artifact_id in its reply text.
    """
    raw_call = str(args.get("call_number", "")).strip().lower()
    valid = {"1": 1, "2": 2, "3": 3, "full": "full", "single_call": "full"}
    if raw_call not in valid:
        return {"error": f"call_number must be one of: 1, 2, 3, full (got '{raw_call}')"}
    call_target = valid[raw_call]

    if not session_id:
        return {"error": "run_vega_analysis requires a chat session_id"}

    explicit_ids = args.get("upload_ids") or []
    if explicit_ids:
        upload_ids = list(dict.fromkeys(explicit_ids))  # dedupe, preserve order
    else:
        # Use the same session-scope helper the conductor uses, so the LLM
        # doesn't have to enumerate upload_ids itself.
        from services.ai.conductor import _gather_session_upload_ids
        upload_ids = await _gather_session_upload_ids(db, session_id, [])

    if not upload_ids:
        return {
            "error": "No transcripts attached to this chat session. Ask the rep "
                     "to drop the transcript with the paperclip button first."
        }

    # Verify each upload exists and is owned by this user before kicking off
    # the heavy pipeline (cheap fail-fast vs. burning Gemini tokens).
    found = await db.chat_uploads.count_documents(
        {"upload_id": {"$in": upload_ids}, "user_id": user.user_id}
    )
    if found != len(upload_ids):
        return {"error": f"One or more upload_id(s) do not belong to this user ({found}/{len(upload_ids)} matched)."}

    # Find or fail-soft on a linked vega_discovery_session for this chat.
    vega_session = await db.vega_discovery_sessions.find_one(
        {"user_id": user.user_id, "chat_session_id": session_id,
         "status": {"$in": ["in_progress", "complete"]}},
        {"_id": 0, "session_id": 1, "client_company": 1},
        sort=[("updated_at", -1)],
    )
    vega_session_id = (vega_session or {}).get("session_id")
    client_company = (vega_session or {}).get("client_company") or None

    from services.ai.vega_analysis import (
        run_transcript_analysis, run_call2_analysis, run_call3_analysis,
        run_single_call_pipeline, run_with_retry,
    )

    pipeline_label = {
        1: "Call 1", 2: "Call 2", 3: "Call 3", "full": "Full",
    }[call_target]
    logger.info(
        "[run_vega_analysis] dispatching %s pipeline (uploads=%d, vega_session=%s, chat=%s)",
        pipeline_label, len(upload_ids), vega_session_id, session_id,
    )

    try:
        if call_target == 1:
            result = await run_with_retry(
                run_transcript_analysis, db, user, upload_ids,
                call_number=1, session_id=vega_session_id, call_num=1,
            )
        elif call_target == 2:
            result = await run_with_retry(
                run_call2_analysis, db, user, upload_ids,
                call_number=2, session_id=vega_session_id, call_num=2,
            )
        elif call_target == 3:
            result = await run_with_retry(
                run_call3_analysis, db, user, upload_ids,
                call_number=3, session_id=vega_session_id, call_num=3,
            )
        else:  # "full"
            result = await run_with_retry(
                run_single_call_pipeline, db, user, upload_ids,
                session_id=vega_session_id, chat_session_id=session_id,
                client_company=client_company, call_num="single_call",
            )
    except Exception as e:
        logger.exception("[run_vega_analysis] pipeline raised: %s", e)
        return {"error": f"{pipeline_label} analysis raised: {str(e)[:200]}"}

    if not result:
        return {"error": f"{pipeline_label} analysis returned no result."}
    if isinstance(result, dict) and result.get("error"):
        return {"error": f"{pipeline_label} analysis failed: {result['error']}"}

    # Persist the artifact for the matrix-style results so the rep gets a
    # proper deliverable card in chat. Call 1 returns {"analysis": …}; the
    # others return {"matrix_data": …}.
    art_id = _gen_id("art")
    art_now = datetime.now(timezone.utc)
    if call_target == 1 and result.get("analysis"):
        artifact_type = "transcript_analysis"
        payload = {
            "call_number": 1,
            "analysis": result["analysis"],
            "session_id": vega_session_id,
        }
    elif result.get("matrix_data"):
        if call_target == "full":
            artifact_type = "single_call_summary"
            new_vega = await db.vega_discovery_sessions.find_one(
                {"user_id": user.user_id, "chat_session_id": session_id, "status": "complete"},
                {"_id": 0, "session_id": 1}, sort=[("updated_at", -1)],
            )
            resolved_sid = (new_vega or {}).get("session_id") or vega_session_id
            payload = {
                **result["matrix_data"],
                "session_id": resolved_sid,
                "client_name": client_company,
                "discovery_mode": "single_call",
            }
        else:
            artifact_type = "ai_employee_matrix"
            payload = {
                **result["matrix_data"],
                "call_number": call_target,
                "session_id": vega_session_id,
                "client_name": client_company,
            }
    else:
        return {"error": f"{pipeline_label} analysis returned an unexpected shape."}

    await db.ai_chat_artifacts.insert_one({
        "artifact_id": art_id, "session_id": session_id,
        "user_id": user.user_id, "employee_id": employee_id or "ai_vega",
        "artifact_type": artifact_type,
        "payload": payload,
        "status": "delivered",
        "created_by_agent": employee_id or "ai_vega",
        "created_at": art_now, "updated_at": art_now,
    })

    return {
        "status": "complete",
        "artifact_id": art_id,
        "artifact_type": artifact_type,
        "summary": result.get("text", f"{pipeline_label} analysis complete."),
        "instruction": (
            f"Reply with one short sentence acknowledging the {pipeline_label} "
            f"analysis is ready, then include the marker `[[artifact:{art_id}]]` "
            f"so the artifact renders inline."
        ),
    }


async def _handle_save_vega_session(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Create or update an Vega discovery session."""
    vega_session_id = args.get("session_id")
    updates = args.get("updates", {})
    if not updates:
        return {"error": "No updates provided"}

    now = datetime.now(timezone.utc).isoformat()
    updates["updated_at"] = now

    if not vega_session_id:
        vega_session_id = _gen_id("vega")
        updates["session_id"] = vega_session_id
        updates["user_id"] = user.user_id
        updates["chat_session_id"] = session_id
        updates["status"] = "in_progress"
        updates["created_at"] = now
        await db.vega_discovery_sessions.insert_one(updates)
        logger.info("Created Vega session %s", vega_session_id)
    else:
        await db.vega_discovery_sessions.update_one(
            {"session_id": vega_session_id, "user_id": user.user_id},
            {"$set": updates},
        )
        logger.info("Updated Vega session %s", vega_session_id)

    return {"session_id": vega_session_id, "current_call_number": updates.get("current_call_number", 0)}


async def _handle_name_vega_session(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Set ``session_name_override`` on the Vega discovery session linked to
    this chat. Used by Vega to auto-name sessions in the sidebar as soon as
    the customer / topic is known. No-op (with warning) when no Vega session
    is linked yet — caller should not retry.
    """
    new_name = (args.get("session_name") or "").strip()
    if not new_name:
        return {"error": "session_name is required and must be non-empty"}
    if len(new_name) > 140:
        new_name = new_name[:140]

    vega_session = await db.vega_discovery_sessions.find_one(
        {"user_id": user.user_id, "chat_session_id": session_id},
        {"_id": 0, "session_id": 1, "session_name_override": 1},
        sort=[("updated_at", -1)],
    )
    if not vega_session:
        logger.warning(
            "name_vega_session: no vega_discovery_sessions row for chat=%s user=%s — skipping",
            session_id, user.user_id,
        )
        return {"error": "No Vega session linked to this chat yet — try after first analysis."}

    await db.vega_discovery_sessions.update_one(
        {"session_id": vega_session["session_id"], "user_id": user.user_id},
        {"$set": {
            "session_name_override": new_name,
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    logger.info(
        "name_vega_session: %s -> %r",
        vega_session["session_id"], new_name,
    )
    return {"session_id": vega_session["session_id"], "session_name": new_name}


async def _handle_compute_mmi_score(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Pure computation: (TSS / IE) * (Time + Cost + Quality + Satisfaction)."""
    tss = max(1, min(5, int(args.get("tss", 1))))
    ie = max(1, min(5, int(args.get("ie", 1))))
    time_val = max(1, min(5, int(args.get("time", 1))))
    cost = max(1, min(5, int(args.get("cost", 1))))
    quality = max(1, min(5, int(args.get("quality", 1))))
    satisfaction = max(1, min(5, int(args.get("satisfaction", 1))))

    score = (tss / ie) * (time_val + cost + quality + satisfaction)
    score = round(score, 2)

    # session_id here is the chat session id; vega_events.session_id should be
    # the Vega discovery session id so KPI joins line up. Look it up cheaply.
    vega_session_id = None
    if session_id:
        vega_doc = await db.vega_discovery_sessions.find_one(
            {"chat_session_id": session_id, "user_id": user.user_id},
            {"_id": 0, "session_id": 1},
            sort=[("updated_at", -1)],
        )
        vega_session_id = (vega_doc or {}).get("session_id")
    await emit_vega_event(db, user.user_id, vega_session_id, "mmi_scored", {"score": score})

    return {
        "score": score,
        "breakdown": {
            "tss": tss, "ie": ie, "time": time_val,
            "cost": cost, "quality": quality, "satisfaction": satisfaction,
            "formula": f"({tss}/{ie}) * ({time_val}+{cost}+{quality}+{satisfaction}) = {score}",
        },
    }


# ── BPMN Tool Handler ──


async def _handle_generate_bpmn_diagram(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Generate a BPMN diagram from workflow data."""
    from services.bpmn.tool_wrapper import generate_workflow_diagram

    workflow_data = args.get("workflow_data", {})
    if not workflow_data:
        return {"error": "No workflow_data provided"}

    result = await generate_workflow_diagram(workflow_data, db)

    if not result.get("success"):
        return {"error": result.get("error", "Unknown error generating BPMN")}

    # Persist the diagram
    bpmn_doc = {
        "bpmn_id": _gen_id("bpmn"),
        "workflow_id": workflow_data.get("workflow_id"),
        "workflow_name": workflow_data.get("workflow_name"),
        "bpmn_xml": result["bpmn_xml"],
        "validation_warnings": result.get("validation_warnings", []),
        "created_by": user.user_id,
        "created_by_agent": employee_id,
        "session_id": session_id,
        "version": 1,
        "edited_xml": None,
        "edited_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.bpmn_diagrams.insert_one(bpmn_doc)

    logger.info("Generated BPMN diagram %s for workflow %s", bpmn_doc["bpmn_id"], workflow_data.get("workflow_id"))

    await emit_vega_event(db, user.user_id, session_id, "bpmn_generated", {"workflow_id": workflow_data.get("workflow_id"), "bpmn_id": bpmn_doc["bpmn_id"]})

    return {
        "bpmn_id": bpmn_doc["bpmn_id"],
        "workflow_id": workflow_data.get("workflow_id"),
        "validation_warnings": result.get("validation_warnings", []),
        "xml_preview": result["bpmn_xml"][:500] + "..." if len(result["bpmn_xml"]) > 500 else result["bpmn_xml"],
    }


# ── Orion Tool Handlers (thin wrappers to services.orion.tools) ──


async def _handle_load_vega_session(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Load an Vega session for Orion consumption."""
    from services.orion.tools import handle_load_vega_session
    return await handle_load_vega_session(args, db, user, session_id=session_id, employee_id=employee_id)


async def _handle_generate_sow_section(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Generate a SOW section."""
    from services.orion.tools import handle_generate_sow_section
    return await handle_generate_sow_section(args, db, user, session_id=session_id, employee_id=employee_id)


async def _handle_update_sow_draft(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Update a SOW draft section."""
    from services.orion.tools import handle_update_sow_draft
    return await handle_update_sow_draft(args, db, user, session_id=session_id, employee_id=employee_id)


async def _handle_render_sow_preview(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Render a SOW preview artifact."""
    from services.orion.tools import handle_render_sow_preview
    return await handle_render_sow_preview(args, db, user, session_id=session_id, employee_id=employee_id)


async def _handle_finalize_sow_to_google_docs(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Finalize SOW to Google Docs."""
    from services.orion.tools import handle_finalize_sow_to_google_docs
    return await handle_finalize_sow_to_google_docs(args, db, user, session_id=session_id, employee_id=employee_id)


# ── PMO Write Tool Handlers (Wave 1 / Thread 1A) ──
#
# Tools that mutate projects/tasks on the user's behalf. Each handler:
#   1. Calls the existing route function (lazy import — avoids cycles).
#   2. Writes a `pmo_ai` audit-log row regardless of success/failure so the
#      Updated-by-PMO-Assistant badge has data to render and admins can spot
#      misfires. Audit shape coordinated with Thread 1B (recent-changes API).
#
# Tier-1 vs Tier-2 distinction is in the system prompt (pmo.py) — the tools
# themselves always execute when invoked. See ADR
# `.waves/projects-v1.2-adr-ai-write-confirmation.md`.


# Permission keys used by the PMO write tools. Centralized so the test suite
# can assert TOOL_REGISTRY ↔ codebase parity without re-listing strings.
_PMO_PERM_PROJECT_CREATE = "projects.project.create"
_PMO_PERM_TASK_CREATE = "projects.task.create"
_PMO_PERM_TASK_EDIT = "projects.task.edit"
_PMO_PERM_TASK_DELETE = "projects.task.delete"
_PMO_PERM_TASK_ASSIGN = "projects.task.assign"
_PMO_PERM_TASK_STATUS = "projects.task.change_status"
_PMO_PERM_TASK_COMMENT = "projects.task.comment.create"
_PMO_PERM_TASK_VIEW = "projects.task.view"


_UPDATE_TASK_FIELD_WHITELIST = {
    "priority", "assignee_id", "start_date", "due_date", "title", "description",
}


@asynccontextmanager
async def _pg_users():
    """Yield a `UserRepository` on a short-lived Postgres session.

    Phase 2a completion: these AI tools are plain coroutines, not FastAPI
    handlers, so they have no `Depends(get_session)` to borrow. Same
    open-a-session-locally shape the other migrated service callers use
    (`services/fs_notifications.py`, `services/google_docs_service.py`).
    Read-only — nothing here commits.
    """
    from db.session import get_sessionmaker
    from repositories.users import UserRepository

    sm = get_sessionmaker()
    async with sm() as pg:
        yield UserRepository(pg)


def _is_uuid_like(s: str) -> bool:
    """Loose check: AEGISAXIOM user_ids are uuid hex, occasionally prefixed.
    Used to decide whether `user_id_or_name` needs name resolution."""
    if not s or not isinstance(s, str):
        return False
    bare = s.split("_")[-1] if "_" in s else s
    return len(bare) >= 16 and all(c in "0123456789abcdef-" for c in bare.lower())


async def _resolve_user_by_name(db, name: str) -> dict:
    """Resolve a display name to a user record. Returns {user_id} on unique
    match, {error: 'ambiguous', candidates: [...]} on multiple matches, or
    {error: 'not_found'} on zero matches. Case-insensitive contains-match on
    `name` and `email`; scoped to assignable users when the flag exists."""
    if not name or not name.strip():
        return {"error": "not_found"}

    name = name.strip()

    # Anchored prefix first (cheap) then fall back to contains-substring so
    # "@grace" finds "Grace Holloway" without the AI having to know the
    # full name. The LIKE-wildcard escaping that replaces the old `re.escape`
    # lives in the repository — the name still comes from a promptable LLM.
    async with _pg_users() as users:
        candidates = await users.search_user_cards(name, prefix_only=True, limit=10)
        if not candidates:
            candidates = await users.search_user_cards(
                name, prefix_only=False, limit=10
            )

    if not candidates:
        return {"error": "not_found", "detail": f"No user matched '{name}'"}
    if len(candidates) == 1:
        return {"user_id": candidates[0]["user_id"]}
    return {
        "error": "ambiguous",
        "detail": f"'{name}' matched {len(candidates)} users — ask which one",
        "candidates": candidates,
    }


async def _project_member_ids(db, project_id: str) -> set[str]:
    """Return the set of user_ids permitted on a project.

    Project membership = `pm_id` + everyone in `assigned_user_ids`. Mirrors
    how `routes/projects_my_work.py` and `projects_project.py` define
    membership. The project itself is the source of truth; no separate
    pm_project_members collection exists today.

    Returns an empty set on missing project or missing fields — callers
    should treat that as "not a project member".
    """
    if not project_id:
        return set()
    project = await db["projects"].find_one(
        {"project_id": project_id},
        {"_id": 0, "pm_id": 1, "assigned_user_ids": 1},
    )
    if not project:
        return set()
    members: set[str] = set()
    pm_id = project.get("pm_id")
    if pm_id:
        members.add(pm_id)
    assigned = project.get("assigned_user_ids") or []
    if isinstance(assigned, list):
        members.update(uid for uid in assigned if uid)
    return members


async def _resolve_user_by_name_on_project(
    db, name: str, project_id: str,
) -> dict:
    """Resolve a display name → user_id, scoped to a project's members.

    v1.3 Theme B fix #4. Without scoping, `assign_task` would happily match
    any user in the workspace named "Grace" — including someone with no
    relationship to the project. With scoping:

      * 0 candidates → ``{error: "not_found", message: ...}`` with a
        candidate-suggestion list of actual project members so the LLM can
        prompt the user "did you mean one of: ..."
      * 1 candidate → ``{user_id: ...}``
      * >1 candidates → ``{error: "ambiguous", candidates: [...]}``

    The lookup is a single Postgres query: find users whose user_id is in the
    project's member set AND whose name/email matches.
    """
    if not name or not name.strip():
        return {"error": "not_found", "message": "No name provided"}

    member_ids = await _project_member_ids(db, project_id)
    if not member_ids:
        return {
            "error": "not_found",
            "message": f"No members found on project {project_id}",
            "candidates": [],
        }

    name = name.strip()
    ids = list(member_ids)

    async with _pg_users() as users:
        # Prefix first, contains as fallback — both scoped to project members.
        candidates = await users.search_user_cards(
            name, prefix_only=True, user_ids=ids, limit=10
        )
        if not candidates:
            candidates = await users.search_user_cards(
                name, prefix_only=False, user_ids=ids, limit=10
            )
        member_docs = (
            await users.list_user_cards_by_ids(ids, limit=20)
            if not candidates
            else []
        )

    if not candidates:
        # 0 matches on project — surface the actual member list so the LLM
        # can suggest a real person instead of guessing.
        return {
            "error": "not_found",
            "message": f"No user '{name}' on this project",
            "candidates": member_docs,
        }

    if len(candidates) == 1:
        return {"user_id": candidates[0]["user_id"]}

    return {
        "error": "ambiguous",
        "message": (
            f"'{name}' matched {len(candidates)} project members — "
            "ask the user which one"
        ),
        "candidates": candidates,
    }


async def _audit_pmo_ai_action(
    db,
    *,
    tool_name: str,
    tier: int,
    user,
    session_id: str = None,
    resource_type: str = None,
    resource_id: str = None,
    params: dict = None,
    result_status: str = "success",
    error_detail: str = None,
    action: str = None,
) -> None:
    """Write a single audit_logs row for an AI-initiated write tool call.

    v1.3 Theme B (this commit) retires the dual-write pattern. The activity
    row written by `crud_envelope` is now stamped with `actor_type=pmo_ai`
    + `tool_name` directly (via the `ai_actor_ctx` contextvar set in
    `execute_tool`). So on the success path this helper is a no-op — the
    single source of truth is the activity row.

    On the error path the route raised before `crud_envelope` could write,
    so we still need a forensic row. We write one `event_category="ai_action"`
    row in that case, with `result_status="error"`, so admins can see which
    AI calls failed and why. This is a strict subset of v1.2's behavior
    (no more duplicate-on-success); the recent-changes reader still picks
    error rows up via its existing `event_category` $or clause.
    """
    if result_status == "success":
        # Activity row already written by crud_envelope with our stamp.
        return

    try:
        user_id = getattr(user, "user_id", None)
        # Normalize short resource_type ("task") → "projects.task" so the
        # recent-changes reader's resource_type whitelist matches.
        full_resource_type = resource_type
        if resource_type and "." not in resource_type:
            full_resource_type = f"projects.{resource_type}"

        details: dict = {
            "module": "projects",
            "entity": resource_type or "",
            "action": action or tool_name,
            "tool_name": tool_name,
            "actor_type": "pmo_ai",
            "tier": tier,
            "params": params or {},
            "session_id": session_id,
        }
        if error_detail:
            details["error_detail"] = error_detail

        entry = {
            "audit_id": f"audit_{uuid.uuid4().hex[:16]}",
            "event_category": "ai_action",
            "actor_id": user_id,
            "actor_user_id": user_id,
            "actor_type": "pmo_ai",
            "actor_email": getattr(user, "email", None),
            "tool_name": tool_name,
            "tier": tier,
            "params": params or {},
            "resource_type": full_resource_type,
            "resource_id": resource_id,
            "action": action or tool_name,
            "result_status": result_status,
            "error_detail": error_detail,
            "session_id": session_id,
            "details": details,
            "timestamp": datetime.now(timezone.utc),
        }
        from services.audit_service import write_audit_entry  # Phase 2b: audit_logs is Postgres
        await write_audit_entry(entry)
    except Exception as e:
        # Audit write is best-effort. The failure already happened — losing
        # the audit row should not surface to the user as a tool error.
        logger.warning("pmo_ai error audit write failed for %s: %s", tool_name, e)


async def _handle_update_task_status(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    task_id = (args.get("task_id") or "").strip()
    status = (args.get("status") or "").strip()
    if not task_id or not status:
        return {"error": "task_id and status are required"}

    # Lazy import — avoids cycle (routes import from services; services don't
    # normally import routes). The update_task handler does the heavy lifting
    # (methodology validation, status-history audit, crud_envelope events).
    from routes.projects_task import update_task, TaskUpdate

    payload = TaskUpdate(status=status)
    try:
        result = await update_task(
            task_id=task_id,
            payload=payload,
            request=None,
            current_user=user,
        )
        await _audit_pmo_ai_action(
            db, tool_name="update_task_status", tier=1, user=user,
            session_id=session_id, resource_type="task", resource_id=task_id,
            params={"status": status, "reason": args.get("reason")},
            result_status="success",
        )
        return {"ok": True, "task": result, "tier": 1}
    except Exception as e:
        detail = getattr(e, "detail", str(e))
        await _audit_pmo_ai_action(
            db, tool_name="update_task_status", tier=1, user=user,
            session_id=session_id, resource_type="task", resource_id=task_id,
            params={"status": status, "reason": args.get("reason")},
            result_status="error", error_detail=str(detail),
        )
        return {"error": "update_failed", "detail": str(detail)}


async def _handle_update_task_field(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    task_id = (args.get("task_id") or "").strip()
    field = (args.get("field") or "").strip()
    value = args.get("value")
    if not task_id or not field:
        return {"error": "task_id and field are required"}
    if field not in _UPDATE_TASK_FIELD_WHITELIST:
        return {
            "error": "field_not_allowed",
            "detail": f"Field {field!r} is not in the AI write whitelist: "
                      f"{sorted(_UPDATE_TASK_FIELD_WHITELIST)}",
        }

    from routes.projects_task import update_task, TaskUpdate

    payload = TaskUpdate(**{field: value})
    try:
        result = await update_task(
            task_id=task_id, payload=payload, request=None, current_user=user,
        )
        await _audit_pmo_ai_action(
            db, tool_name="update_task_field", tier=1, user=user,
            session_id=session_id, resource_type="task", resource_id=task_id,
            params={"field": field, "value": value}, result_status="success",
        )
        return {"ok": True, "task": result, "tier": 1}
    except Exception as e:
        detail = getattr(e, "detail", str(e))
        await _audit_pmo_ai_action(
            db, tool_name="update_task_field", tier=1, user=user,
            session_id=session_id, resource_type="task", resource_id=task_id,
            params={"field": field, "value": value},
            result_status="error", error_detail=str(detail),
        )
        return {"error": "update_failed", "detail": str(detail)}


async def _handle_assign_task(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    task_id = (args.get("task_id") or "").strip()
    raw = args.get("user_id_or_name")
    if not task_id:
        return {"error": "task_id is required"}

    # v1.3 Theme B fix #4 — scope name resolution to the task's project
    # members. Without this, "@grace" could match a Grace anywhere in the
    # workspace, even if they have no role on the project. Look up the
    # task's project_id once so we can pass it to the resolver.
    task_doc = await db["pm_tasks"].find_one(
        {"task_id": task_id},
        {"_id": 0, "project_id": 1},
    )
    if not task_doc:
        return {
            "error": "not_found",
            "message": f"Task {task_id} not found",
        }
    project_id = task_doc.get("project_id")

    # Resolve target user_id.
    target_user_id: str | None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        target_user_id = None  # unassign
    elif _is_uuid_like(raw):
        # Even with a raw user_id, verify the user is actually on the
        # project. AI hallucinated IDs shouldn't slip past membership.
        member_ids = await _project_member_ids(db, project_id) if project_id else set()
        if project_id and raw not in member_ids:
            async with _pg_users() as users:
                member_docs = await users.list_user_cards_by_ids(
                    list(member_ids), limit=20
                )
            await _audit_pmo_ai_action(
                db, tool_name="assign_task", tier=1, user=user,
                session_id=session_id, resource_type="task",
                resource_id=task_id, params={"user_id_or_name": raw},
                result_status="error", error_detail="not_a_project_member",
            )
            return {
                "error": "not_found",
                "message": f"User {raw} is not a member of this project",
                "candidates": member_docs,
            }
        target_user_id = raw
    else:
        resolved = await _resolve_user_by_name_on_project(db, raw, project_id)
        if "error" in resolved:
            # Bubble up the ambiguous/not_found verbatim so the LLM can ask
            # the user a follow-up question instead of guessing.
            await _audit_pmo_ai_action(
                db, tool_name="assign_task", tier=1, user=user,
                session_id=session_id, resource_type="task",
                resource_id=task_id, params={"user_id_or_name": raw},
                result_status="error", error_detail=resolved.get("error"),
            )
            return resolved
        target_user_id = resolved["user_id"]

    from routes.projects_task import update_task, TaskUpdate

    payload = TaskUpdate(assignee_id=target_user_id)
    try:
        result = await update_task(
            task_id=task_id, payload=payload, request=None, current_user=user,
        )
        await _audit_pmo_ai_action(
            db, tool_name="assign_task", tier=1, user=user,
            session_id=session_id, resource_type="task", resource_id=task_id,
            params={"user_id_or_name": raw, "resolved_user_id": target_user_id},
            result_status="success",
        )
        return {"ok": True, "task": result, "assignee_id": target_user_id, "tier": 1}
    except Exception as e:
        detail = getattr(e, "detail", str(e))
        await _audit_pmo_ai_action(
            db, tool_name="assign_task", tier=1, user=user,
            session_id=session_id, resource_type="task", resource_id=task_id,
            params={"user_id_or_name": raw, "resolved_user_id": target_user_id},
            result_status="error", error_detail=str(detail),
        )
        return {"error": "assign_failed", "detail": str(detail)}


async def _handle_add_task_comment(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    task_id = (args.get("task_id") or "").strip()
    body = (args.get("body") or "").strip()
    if not task_id or not body:
        return {"error": "task_id and body are required"}

    from routes.projects_task_comment import create_task_comment, CommentCreate

    try:
        result = await create_task_comment(
            task_id=task_id,
            payload=CommentCreate(body=body),
            request=None,
            current_user=user,
        )
        await _audit_pmo_ai_action(
            db, tool_name="add_task_comment", tier=1, user=user,
            session_id=session_id, resource_type="task_comment",
            resource_id=result.get("comment_id"),
            # Don't dump full comment body into audit params (could be long).
            params={"task_id": task_id, "body_chars": len(body)},
            result_status="success",
        )
        return {"ok": True, "comment": result, "tier": 1}
    except Exception as e:
        detail = getattr(e, "detail", str(e))
        await _audit_pmo_ai_action(
            db, tool_name="add_task_comment", tier=1, user=user,
            session_id=session_id, resource_type="task_comment",
            resource_id=None,
            params={"task_id": task_id, "body_chars": len(body)},
            result_status="error", error_detail=str(detail),
        )
        return {"error": "comment_failed", "detail": str(detail)}


async def _handle_create_project(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    name = (args.get("name") or "").strip()
    methodology = (args.get("methodology") or "").strip()
    if not name or methodology not in {"waterfall", "agile", "waves_threads"}:
        return {"error": "name and a valid methodology are required"}

    from routes.projects_project import create_project, ProjectCreate

    payload = ProjectCreate(
        name=name,
        project_type=methodology,
        service_line=(args.get("service_line") or "other"),
        description=args.get("description"),
        target_end_date=args.get("due_date"),
        pm_id=args.get("pm_user_id"),
    )
    try:
        result = await create_project(payload=payload, request=None, current_user=user)
        await _audit_pmo_ai_action(
            db, tool_name="create_project", tier=2, user=user,
            session_id=session_id, resource_type="project",
            resource_id=result.get("project_id"),
            params={"name": name, "methodology": methodology, "due_date": args.get("due_date")},
            result_status="success",
        )
        return {
            "ok": True,
            "project": result,
            "tier": 2,
            "_warning": "Project created. RBAC scoping + notifications applied.",
        }
    except Exception as e:
        detail = getattr(e, "detail", str(e))
        await _audit_pmo_ai_action(
            db, tool_name="create_project", tier=2, user=user,
            session_id=session_id, resource_type="project", resource_id=None,
            params={"name": name, "methodology": methodology, "due_date": args.get("due_date")},
            result_status="error", error_detail=str(detail),
        )
        return {"error": "create_failed", "detail": str(detail)}


async def _handle_create_project_from_template(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    template_id = (args.get("template_id") or "").strip()
    name = (args.get("name") or "").strip()
    if not template_id or not name:
        return {"error": "template_id and name are required"}

    # Need to know the template's project_type + service_line so we can fill
    # the required wire fields on the route's payload. The route validates
    # they match the template anyway.
    template = await db["pm_templates"].find_one(
        {"template_id": template_id},
        {"_id": 0, "project_type": 1, "service_line": 1},
    )
    if not template:
        return {"error": "template_not_found", "detail": f"No template {template_id}"}

    from routes.projects_project import (
        create_project_from_template, ProjectFromTemplateCreate,
    )

    payload = ProjectFromTemplateCreate(
        name=name,
        project_type=template["project_type"],
        service_line=template.get("service_line") or "other",
        template_id=template_id,
        target_end_date=args.get("due_date"),
        pm_id=args.get("pm_user_id"),
    )
    try:
        result = await create_project_from_template(
            payload=payload, request=None, current_user=user,
        )
        await _audit_pmo_ai_action(
            db, tool_name="create_project_from_template", tier=2, user=user,
            session_id=session_id, resource_type="project",
            resource_id=(result or {}).get("project_id"),
            params={"template_id": template_id, "name": name},
            result_status="success",
        )
        return {
            "ok": True,
            "project": result,
            "tier": 2,
            "_warning": (
                "Project instantiated from template. Task tree seeded — review "
                "the generated tasks with the user."
            ),
        }
    except Exception as e:
        detail = getattr(e, "detail", str(e))
        await _audit_pmo_ai_action(
            db, tool_name="create_project_from_template", tier=2, user=user,
            session_id=session_id, resource_type="project", resource_id=None,
            params={"template_id": template_id, "name": name},
            result_status="error", error_detail=str(detail),
        )
        return {"error": "create_failed", "detail": str(detail)}


async def _handle_create_task(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    project_id = (args.get("project_id") or "").strip()
    title = (args.get("title") or "").strip()
    if not project_id or not title:
        return {"error": "project_id and title are required"}

    # Pre-flight: load the project so we can enforce methodology context BEFORE
    # we try to create. Without this the AI happily calls create_task on a
    # waves_threads project with no thread_id, the backend 422s with a generic
    # validation error, and the user sees "couldn't create" with no way to fix.
    # Returning a structured `needs_methodology_context` lets the AI ask the
    # user "which thread?" / "which phase?" / "which column?" with the actual
    # options enumerated.
    project = await db["projects"].find_one({"project_id": project_id}, {"_id": 0})
    if project is None:
        return {"error": "project_not_found", "detail": f"project_id {project_id!r} does not exist"}
    project_type = project.get("project_type")
    mf_in = args.get("methodology_fields") or {}

    if project_type == "waves_threads" and not mf_in.get("thread_id"):
        # Enumerate available threads (per-wave grouping) so the AI can ask.
        waves = await db["pm_waves"].find(
            {"project_id": project_id},
            {"_id": 0, "wave_id": 1, "wave_number": 1, "goal": 1, "status": 1},
        ).sort("wave_number", 1).to_list(50)
        threads = await db["pm_threads"].find(
            {"project_id": project_id},
            {"_id": 0, "thread_id": 1, "wave_id": 1, "module": 1, "status": 1},
        ).to_list(200)
        return {
            "error": "needs_methodology_context",
            "needs": ["thread_id"],
            "reason": "waves_threads projects require thread context — ask the user which thread the task belongs to",
            "project_type": project_type,
            "available_waves": waves,
            "available_threads": threads,
        }
    if project_type == "waterfall" and not mf_in.get("phase"):
        phases = project.get("methodology_config", {}).get("phases", []) or []
        phase_names = [p.get("name") if isinstance(p, dict) else p for p in phases]
        return {
            "error": "needs_methodology_context",
            "needs": ["phase"],
            "reason": "waterfall projects require a phase — ask the user which phase the task belongs to",
            "project_type": project_type,
            "available_phases": phase_names,
        }
    if project_type == "agile" and not mf_in.get("column_id"):
        cols = project.get("methodology_config", {}).get("columns", []) or []
        # Active sprints (so AI can ask "which sprint?" if relevant)
        sprints = await db["pm_sprints"].find(
            {"project_id": project_id, "status": {"$in": ["active", "planned"]}},
            {"_id": 0, "sprint_id": 1, "name": 1, "status": 1, "start_date": 1, "end_date": 1},
        ).sort("start_date", 1).to_list(20)
        return {
            "error": "needs_methodology_context",
            "needs": ["column_id"],
            "reason": "agile projects require a column_id — ask the user which column (and optionally which sprint) the task belongs to",
            "project_type": project_type,
            "available_columns": [{"id": c.get("id"), "name": c.get("name")} for c in cols],
            "available_sprints": sprints,
        }

    # Resolve assignee by name if provided as a non-uuid.
    assignee_id: str | None = None
    raw_assignee = args.get("assignee")
    if raw_assignee:
        if _is_uuid_like(raw_assignee):
            assignee_id = raw_assignee
        else:
            resolved = await _resolve_user_by_name(db, raw_assignee)
            if "error" in resolved:
                await _audit_pmo_ai_action(
                    db, tool_name="create_task", tier=2, user=user,
                    session_id=session_id, resource_type="task", resource_id=None,
                    params={"project_id": project_id, "title": title, "assignee": raw_assignee},
                    result_status="error", error_detail=resolved.get("error"),
                )
                return resolved
            assignee_id = resolved["user_id"]

    # For waves_threads: if thread_id was supplied but wave_id wasn't, derive it
    # so the validator doesn't 422 on missing wave_id.
    if project_type == "waves_threads":
        thread_id = mf_in.get("thread_id")
        if thread_id and not mf_in.get("wave_id"):
            t = await db["pm_threads"].find_one(
                {"thread_id": thread_id}, {"_id": 0, "wave_id": 1, "module": 1}
            )
            if t:
                mf_in["wave_id"] = t.get("wave_id")
                if not mf_in.get("module_owned") and t.get("module"):
                    mf_in["module_owned"] = t["module"]
        args["methodology_fields"] = mf_in

    from routes.projects_task import create_task, TaskCreate

    payload_kwargs: dict = {
        "project_id": project_id,
        "title": title,
        "assignee_id": assignee_id,
        "priority": args.get("priority") or "medium",
        "methodology_fields": args.get("methodology_fields") or {},
    }
    if args.get("status"):
        payload_kwargs["status"] = args["status"]
    if args.get("due_date"):
        payload_kwargs["due_date"] = args["due_date"]

    try:
        payload = TaskCreate(**payload_kwargs)
        result = await create_task(payload=payload, request=None, current_user=user)
        await _audit_pmo_ai_action(
            db, tool_name="create_task", tier=2, user=user,
            session_id=session_id, resource_type="task",
            resource_id=(result or {}).get("task_id"),
            params={"project_id": project_id, "title": title, "assignee_id": assignee_id},
            result_status="success",
        )
        return {"ok": True, "task": result, "tier": 2}
    except Exception as e:
        detail = getattr(e, "detail", str(e))
        await _audit_pmo_ai_action(
            db, tool_name="create_task", tier=2, user=user,
            session_id=session_id, resource_type="task", resource_id=None,
            params={"project_id": project_id, "title": title, "assignee_id": assignee_id},
            result_status="error", error_detail=str(detail),
        )
        return {"error": "create_failed", "detail": str(detail)}


async def _handle_bulk_update_task_status(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    task_ids = args.get("task_ids") or []
    status = (args.get("status") or "").strip()
    if not task_ids or not status:
        return {"error": "task_ids and status are required"}

    from routes.projects_task import bulk_update_status, BulkTaskStatus

    body = BulkTaskStatus(task_ids=list(task_ids), status=status)
    try:
        result = await bulk_update_status(body=body, request=None, current_user=user)
        await _audit_pmo_ai_action(
            db, tool_name="bulk_update_task_status", tier=2, user=user,
            session_id=session_id, resource_type="task", resource_id=None,
            params={"task_count": len(task_ids), "status": status},
            result_status="success",
        )
        updated = result.get("updated_count", 0)
        failed = result.get("failed") or []
        return {
            "ok": True,
            "result": result,
            "tier": 2,
            "_warning": (
                f"Bulk status update applied. {updated} task(s) updated"
                + (f", {len(failed)} failed — surface the failures to the user." if failed else ".")
            ),
        }
    except Exception as e:
        detail = getattr(e, "detail", str(e))
        await _audit_pmo_ai_action(
            db, tool_name="bulk_update_task_status", tier=2, user=user,
            session_id=session_id, resource_type="task", resource_id=None,
            params={"task_count": len(task_ids), "status": status},
            result_status="error", error_detail=str(detail),
        )
        return {"error": "bulk_failed", "detail": str(detail)}


async def _handle_bulk_assign_tasks(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    task_ids = args.get("task_ids") or []
    raw = args.get("user_id_or_name")
    if not task_ids:
        return {"error": "task_ids is required"}

    assignee_id: str | None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        assignee_id = None
    elif _is_uuid_like(raw):
        assignee_id = raw
    else:
        resolved = await _resolve_user_by_name(db, raw)
        if "error" in resolved:
            await _audit_pmo_ai_action(
                db, tool_name="bulk_assign_tasks", tier=2, user=user,
                session_id=session_id, resource_type="task", resource_id=None,
                params={"task_count": len(task_ids), "user_id_or_name": raw},
                result_status="error", error_detail=resolved.get("error"),
            )
            return resolved
        assignee_id = resolved["user_id"]

    from routes.projects_task import bulk_assign_tasks, BulkTaskAssign

    body = BulkTaskAssign(task_ids=list(task_ids), assignee_id=assignee_id)
    try:
        result = await bulk_assign_tasks(body=body, request=None, current_user=user)
        await _audit_pmo_ai_action(
            db, tool_name="bulk_assign_tasks", tier=2, user=user,
            session_id=session_id, resource_type="task", resource_id=None,
            params={"task_count": len(task_ids), "assignee_id": assignee_id},
            result_status="success",
        )
        updated = result.get("updated_count", 0)
        failed = result.get("failed") or []
        return {
            "ok": True,
            "result": result,
            "tier": 2,
            "_warning": (
                f"Bulk assign applied. {updated} task(s) updated"
                + (f", {len(failed)} failed — surface failures to the user." if failed else ".")
            ),
        }
    except Exception as e:
        detail = getattr(e, "detail", str(e))
        await _audit_pmo_ai_action(
            db, tool_name="bulk_assign_tasks", tier=2, user=user,
            session_id=session_id, resource_type="task", resource_id=None,
            params={"task_count": len(task_ids), "assignee_id": assignee_id},
            result_status="error", error_detail=str(detail),
        )
        return {"error": "bulk_failed", "detail": str(detail)}


async def _handle_delete_task(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    task_id = (args.get("task_id") or "").strip()
    if not task_id:
        return {"error": "task_id is required"}

    from routes.projects_task import delete_task as route_delete_task

    try:
        result = await route_delete_task(
            task_id=task_id, request=None, current_user=user,
        )
        await _audit_pmo_ai_action(
            db, tool_name="delete_task", tier=2, user=user,
            session_id=session_id, resource_type="task", resource_id=task_id,
            params={"task_id": task_id}, result_status="success",
        )
        return {
            "ok": True,
            "deleted_id": task_id,
            "result": result,
            "tier": 2,
            "_warning": (
                "Task permanently deleted. This action cannot be undone. Inform "
                "the user the deletion is final."
            ),
        }
    except Exception as e:
        detail = getattr(e, "detail", str(e))
        await _audit_pmo_ai_action(
            db, tool_name="delete_task", tier=2, user=user,
            session_id=session_id, resource_type="task", resource_id=task_id,
            params={"task_id": task_id},
            result_status="error", error_detail=str(detail),
        )
        return {"error": "delete_failed", "detail": str(detail)}


async def _handle_summarize_recent_changes(
    args: dict, db, user, session_id: str = None, employee_id: str = None
) -> dict:
    """Fetch recent project/task mutations via the canonical recent-changes handler.

    Delegates to `routes.projects_recent_changes.fetch_recent_changes` —
    the single source of truth for recent-changes logic (Wave 1 / Thread 1B).
    This keeps both the API endpoint and the AI tool in sync without duplicating
    the RBAC scoping, audit-log normalization, or batch name-resolution logic.
    """
    from routes.projects_recent_changes import fetch_recent_changes, aggregate_change_counts

    hours = args.get("hours") or 24
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        hours = 24
    hours = max(1, min(168, hours))  # cap at 1 week

    project_id = (args.get("project_id") or "").strip() or None

    rows = await fetch_recent_changes(
        db,
        user,
        hours=hours,
        project_id=project_id,
        limit=150,
    )
    counts = aggregate_change_counts(rows)

    return {
        "ok": True,
        "hours": hours,
        "project_id_filter": project_id,
        "changes": rows,
        "counts": counts,
        "summary_hint": (
            f"Found {counts['total']} change(s) in the last {hours}h "
            f"({counts['status_changes']} status change(s), "
            f"{counts['tasks_created']} task(s) created, "
            f"{counts['assignments']} assignment(s)). "
            "Each item has a `summary` field — use those verbatim as prose, "
            "grouped by project then type."
        ),
    }


# ── Tool Registration ──
#
# Bind every dispatchable tool to its handler and required permission.
# Registry is consulted by execute_tool above. New tools — including
# those bundled by ingested apps — must add a _register_tool(...) line
# here or they will be unreachable. Use OPEN to declare a tool
# intentionally available to any user already in chat.

# Generic chat plumbing — open to anyone in chat
_register_tool("render_artifact", _handle_render_artifact, OPEN)
_register_tool("update_user_state", _handle_update_user_state, OPEN)
_register_tool("grade_quiz", _handle_grade_quiz, OPEN)
# award_certification is OPEN by design: the AI is responsible for
# checking the user passed the final test before invoking it. The
# handler itself enforces user-scoped writes (see ARC training flow).
_register_tool("award_certification", _handle_award_certification, OPEN)

# Vega agent tools
_register_tool("ingest_transcript", _handle_ingest_transcript, "vega.use")
_register_tool("save_vega_session", _handle_save_vega_session, "vega.use")
_register_tool("name_vega_session", _handle_name_vega_session, "vega.use")
_register_tool("compute_mmi_score", _handle_compute_mmi_score, "vega.use")
_register_tool("load_vega_session", _handle_load_vega_session, "vega.use")
_register_tool("graduate_freeflow_to_call1", _handle_graduate_freeflow_to_call1, "vega.use")
_register_tool("run_vega_analysis", _handle_run_vega_analysis, "vega.use")

# BPMN agent tool
_register_tool("generate_bpmn_diagram", _handle_generate_bpmn_diagram, "bpmn.use")

# Orion (SOW builder) agent tools
_register_tool("generate_sow_section", _handle_generate_sow_section, "orion.use")
_register_tool("update_sow_draft", _handle_update_sow_draft, "orion.use")
_register_tool("render_sow_preview", _handle_render_sow_preview, "orion.use")
_register_tool("finalize_sow_to_google_docs", _handle_finalize_sow_to_google_docs, "orion.use")

# PMO agent tools — write tools that mutate projects/tasks on the user's
# behalf. Permission keys mirror the human-callable route perms in
# seeds/seed_projects_rbac.py so the AI cannot exceed the user's UI rights.
# Tier-1 vs Tier-2 confirmation is enforced in pmo.py's system prompt; tools
# themselves always execute when called. See ADR
# `.waves/projects-v1.2-adr-ai-write-confirmation.md`.
_register_tool("update_task_status", _handle_update_task_status, _PMO_PERM_TASK_STATUS)
_register_tool("update_task_field", _handle_update_task_field, _PMO_PERM_TASK_EDIT)
_register_tool("assign_task", _handle_assign_task, _PMO_PERM_TASK_ASSIGN)
_register_tool("add_task_comment", _handle_add_task_comment, _PMO_PERM_TASK_COMMENT)
_register_tool("create_project", _handle_create_project, _PMO_PERM_PROJECT_CREATE)
_register_tool("create_project_from_template", _handle_create_project_from_template, _PMO_PERM_PROJECT_CREATE)
_register_tool("create_task", _handle_create_task, _PMO_PERM_TASK_CREATE)
_register_tool("bulk_update_task_status", _handle_bulk_update_task_status, _PMO_PERM_TASK_STATUS)
_register_tool("bulk_assign_tasks", _handle_bulk_assign_tasks, _PMO_PERM_TASK_ASSIGN)
_register_tool("delete_task", _handle_delete_task, _PMO_PERM_TASK_DELETE)
_register_tool("summarize_recent_changes", _handle_summarize_recent_changes, _PMO_PERM_TASK_VIEW)