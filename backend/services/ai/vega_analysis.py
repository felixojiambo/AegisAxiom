"""Structured transcript analysis for Vega discovery calls.

Uses Gemini's response_schema for deterministic JSON output,
bypassing tool calling entirely.

Supports Call 1 (transcript analysis), Call 2 (AI Employee Matrix build),
and Call 3 (final scoring and gap resolution).
"""

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# 5 min — shared across all three Vega calls. Call 1 previously used 90s and
# Calls 2-3 used 180s, but all three were hitting timeouts on complex transcripts.
# A single generous ceiling is simpler than per-call constants; the retry wrapper
# handles transient failures, and the timeout is a last-resort safety net.
VEGA_LLM_TIMEOUT = 300

# Low temperature for Vega's structured-output calls. Gemini's default is 1.0,
# which produced highly variable outputs from the same call recording. 0.2
# keeps room for occasional rewording but pins the structural decisions
# (workflow segmentation, MMI dimensions, app extraction) much more tightly.
VEGA_LLM_TEMPERATURE = 0.2


async def run_with_retry(analysis_fn, *args, call_num=None, **kwargs):
    """Run an Vega analysis function, retrying once on None/exception."""
    for attempt in (1, 2):
        try:
            r = await analysis_fn(*args, **kwargs)
            if r is not None:
                return r
            if attempt == 1:
                logger.warning(
                    "Vega analysis returned None (call %s, attempt %s) — retrying",
                    call_num, attempt,
                )
        except Exception as exc:
            logger.warning(
                "Vega analysis exception (call %s, attempt %s): %s",
                call_num, attempt, exc,
            )
            if attempt == 2:
                raise
    logger.warning("Vega analysis returned None on both attempts (call %s) — giving up", call_num)
    return None


# ──────────────────────────────────────────────
#  Schema — Gemini structured output format
# ──────────────────────────────────────────────

TRANSCRIPT_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "client_name": {"type": "string", "description": "The client/company name discussed in the call"},
        "summary": {"type": "string", "description": "2-3 sentence summary of the call"},
        "data_state_assessment": {"type": "string", "enum": ["blind", "siloed", "visible"]},
        "data_state_justification": {"type": "string", "description": "Why this data state was assessed"},
        "identified_pain_points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "The pain point described in client's words"},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "frequency": {"type": "string", "enum": ["daily", "weekly", "monthly", "rare", "unknown"]},
                    "business_impact": {"type": "string", "description": "Financial or operational impact"},
                    "direct_quote": {"type": "string", "description": "Direct quote from the transcript supporting this pain point"},
                },
                "required": ["description", "severity", "frequency", "business_impact"],
            },
        },
        "industry_context": {
            "type": "string",
            "description": "The client's industry, market context, and business model as described in the call"
        },
        "current_tech_stack": {
            "type": "array",
            "items": {"type": "string"},
            "description": "ONLY systems, tools, databases, and platforms the client CURRENTLY uses today. Do NOT include tools that were proposed or suggested during the call."
        },
        "suggested_tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tools, systems, or platforms that were suggested, proposed, or discussed as potential solutions during the call — NOT currently in use by the client"
        },
        "ai_readiness_signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "signal": {"type": "string", "description": "The specific signal observed"},
                    "indicator": {"type": "string", "enum": ["positive", "negative", "neutral"]},
                    "evidence": {"type": "string", "description": "Direct quote or reference from transcript"},
                },
                "required": ["signal", "indicator", "evidence"],
            },
            "description": "Signals indicating the client's readiness (or resistance) to AI adoption"
        },
        "data_sources_inventory": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_name": {"type": "string"},
                    "type": {"type": "string", "enum": ["database", "spreadsheet", "saas", "manual", "paper", "other"]},
                    "accessibility": {"type": "string", "description": "How accessible is this data source (API, export, manual lookup, unknown)"},
                    "data_quality_notes": {"type": "string", "description": "Any notes on data quality, freshness, or reliability"},
                },
                "required": ["source_name", "type", "accessibility"],
            },
            "description": "Inventory of all data sources mentioned or implied"
        },
        "stakeholder_strategies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "stakeholder_name": {"type": "string"},
                    "role": {"type": "string"},
                    "buyer_type": {"type": "string", "description": "e.g., ROI & Bandwidth Buyer, Friction & Day-to-Day Buyer, Technical/Security Buyer"},
                    "pain_summary": {"type": "string", "description": "One-sentence summary of this person's core pain"},
                    "selling_strategy": {"type": "string", "description": "Strategic approach tailored to this stakeholder"},
                    "hook_question": {"type": "string", "description": "The specific opening question/hook to use with this stakeholder in Call 2"},
                    "tactical_advice": {"type": "string", "description": "Specific tactical advice for engaging this stakeholder"},
                },
                "required": ["stakeholder_name", "role", "pain_summary", "selling_strategy", "hook_question", "tactical_advice"],
            },
            "description": "Per-stakeholder selling strategies and tactical hooks for the next call"
        },
        "workflow_investigation_guides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "workflow_name": {"type": "string"},
                    "walkthrough_questions": {"type": "array", "items": {"type": "string"}, "description": "Step-by-step questions to extract the full workflow in Call 2"},
                    "decision_probes": {"type": "array", "items": {"type": "string"}, "description": "Questions to identify decision points and exception paths"},
                    "technical_checks": {"type": "array", "items": {"type": "string"}, "description": "Questions about APIs, credentials, access"},
                    "mmi_quantification": {"type": "array", "items": {"type": "string"}, "description": "Questions to quantify time/cost/error metrics for MMI scoring"},
                    "pro_tip": {"type": "string", "description": "A specific tactical tip for extracting this workflow"},
                },
                "required": ["workflow_name", "walkthrough_questions", "decision_probes", "mmi_quantification"],
            },
            "description": "Detailed investigation guides for each workflow to use in Call 2"
        },
        "call_2_approach_summary": {
            "type": "string",
            "description": "Brief strategic summary: 'With [Person]: Talk [Theme]' for each stakeholder, plus an overall pro tip"
        },
        "stakeholders_mentioned": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "concerns": {"type": "string"},
                },
                "required": ["name", "role", "concerns"],
            },
        },
        "missed_questions": {"type": "array", "items": {"type": "string"}},
        "potential_workflows_spotted": {"type": "array", "items": {"type": "string"}},
        "estimated_workflow_categories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "workflow_name": {"type": "string"},
                    "category": {"type": "string", "enum": ["bottleneck", "data_heavy", "repeated", "decision_intensive", "compliance"]},
                    "reasoning": {"type": "string", "description": "Why this workflow fits this category"},
                },
                "required": ["workflow_name", "category", "reasoning"],
            },
            "description": "Classify each spotted workflow into a category based on first-principles analysis"
        },
        "next_call_agenda": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "client_name",
        "summary",
        "data_state_assessment",
        "data_state_justification",
        "identified_pain_points",
        "industry_context",
        "current_tech_stack",
        "suggested_tools",
        "ai_readiness_signals",
        "data_sources_inventory",
        "stakeholder_strategies",
        "workflow_investigation_guides",
        "call_2_approach_summary",
        "stakeholders_mentioned",
        "missed_questions",
        "potential_workflows_spotted",
        "next_call_agenda",
    ],
}

# ──────────────────────────────────────────────
#  Prompt template
# ──────────────────────────────────────────────

ANALYSIS_PROMPT = """You are Vega, the AI Recruiter Agent. Analyze the following discovery call transcript.

CALL NUMBER: {call_number}
{prior_context}

[TRANSCRIPT START]
{transcript_text}
[TRANSCRIPT END]

Analyze the transcript and extract:
- A concise summary (2-3 sentences)
- Data maturity state: "blind" (no visibility), "siloed" (data exists but fragmented), or "visible" (accessible and usable)
- Justification for the data state assessment
- Pain points identified, each as a descriptive sentence using the client's own words where possible
- Stakeholders mentioned with name, role/title, and their concerns as a joined string
- Questions the rep missed or should have asked
- Potential AI workflow opportunities spotted, each as "Workflow Name: brief description"
- Next call agenda items as actionable discussion points, each prefixed with the relevant workflow name in brackets like "[Workflow Name] Question"

- Industry context: the client's industry, market position, and business model
- Current technology stack: ONLY systems the client CURRENTLY USES TODAY. If the client says "we use Square and Any Road," list those. Do NOT include tools that were suggested or proposed during the call (like AEGISAXIOM, frameworks being pitched, etc.) — those go in "suggested_tools."
- Suggested tools: Any systems, tools, or platforms that were proposed, discussed, or recommended as potential solutions during the call.
- Data sources inventory: for each data source, capture: name, type (database/spreadsheet/saas/manual/paper), accessibility (API/export/manual lookup/unknown), and data quality notes
- AI readiness signals: specific positive and negative indicators of the client's readiness for AI adoption, each with evidence from the transcript
- For each pain point: include severity (high/medium/low), frequency (daily/weekly/monthly/rare), business impact, and a DIRECT QUOTE from the transcript

Use exact numbers, timeframes, and system names from the transcript. Never generalize when specifics are available.

STAKEHOLDER SELLING STRATEGIES:
For EACH stakeholder identified, produce a detailed selling strategy:
- Classify their buyer type (ROI & Bandwidth Buyer, Friction & Day-to-Day Buyer, Technical/Security Buyer, Relationship Buyer, etc.)
- Summarize their specific pain in one sentence using their own words
- Write a concrete selling strategy tailored to this person's role and concerns
- Write a specific "hook question" — the opening line to use with them in Call 2. Make it personal and reference their specific situation.
- Write tactical advice: what to focus on, what to avoid, how to position the AI solution for this specific person
Example: "Keely is the ROI & Bandwidth Buyer. Strategy: Focus on scalability and reclaimable time. Hook: 'Keely, right now you're manually keying in data on Sundays. If we automate that, what strategic initiatives would you have time for instead?' Tactical: Keep bringing it back to her capacity to manage more locations without adding headcount."

WORKFLOW INVESTIGATION GUIDES:
For EACH workflow spotted, produce a detailed Call 2 investigation guide:
- Walkthrough questions: Step-by-step questions to extract the full "as-is" process (triggers, steps, decisions, outputs)
- Decision and exception probes: Questions to identify where humans make judgment calls, what happens when things go wrong
- Technical checks: Questions about API access, credentials, system admin rights, data formats
- MMI quantification: Specific questions to extract time/cost/error metrics (e.g., "How many minutes per report? How many reports total?")
- Pro tip: A specific tactical recommendation (e.g., "Ask Keely to share her screen and walk through the Sunday reporting spreadsheet")

CALL 2 APPROACH SUMMARY:
Write a brief strategic summary like: "With Keely: Talk ROI & Growth. With Meghan: Talk Time & Sanity. With Ben: Talk Invisible Admin. With Fred: Talk Architecture & Control." Include a pro tip about identifying the economic buyer vs technical buyer.

Be specific, not generic. Use the client's own words. If something wasn't discussed, note it as a gap.

WORKFLOW CATEGORY CLASSIFICATION:
For each potential workflow spotted, classify it into one of these categories using FIRST-PRINCIPLES THINKING — strip away the human handoffs and identify the raw inputs, transformations, and outputs:
- BOTTLENECK: Throughput-limited. A single point of dependency stalls the entire process. Look for: one person who must approve everything, a single system that all data flows through, a step where work queues up.
- DATA_HEAVY: High cognitive load, not necessarily high volume. Look for: humans cross-referencing multiple systems, manual data entry from one system to another, reconciliation tasks, report compilation from scattered sources.
- REPEATED: High-frequency, low-variance. The same steps execute over and over with minimal decision-making. Look for: daily/weekly routines, standard operating procedures, form processing, notification chains.
- DECISION_INTENSIVE: Many branching conditions. Look for: complex approval matrices, tiered pricing logic, exception handling trees, compliance checks with multiple criteria.
- COMPLIANCE: Regulatory or audit-driven. Look for: mandated reporting, audit trails, certification requirements, data retention rules.

A workflow may fit multiple categories. Choose the PRIMARY one — the category that most determines its automation approach.

QUESTION GENERATION RULES — BE EXHAUSTIVE:
- Generate ALL relevant questions the rep should ask next, not just the top 2-3 most obvious ones.
- Use the Power Questions knowledge base as a COMPLETE CHECKLIST. For every question category (Situational Awareness, Friction, Cost of Inaction, Data Landscape, Strategic Vision), check if the rep adequately covered it. If not, add follow-up questions.
- For EVERY workflow spotted, generate at least one question about triggers, data sources, and decision logic.
- Include questions about stakeholders who were mentioned but not directly interviewed.
- Include questions about systems that were named but not fully explored (data format, API access, current usage).
- Err on the side of MORE questions. The rep can prioritize; your job is completeness.
- Group next_call_agenda items by workflow/topic and include a bracketed prefix like "[Workflow Name] Question"."""

# ──────────────────────────────────────────────
#  Transcript loader (shared with conductor)
# ──────────────────────────────────────────────

_MAX_TRANSCRIPT_CHARS = 120_000


async def _load_transcripts(db, upload_ids: list[str], user) -> str:
    """Load extracted text from chat_uploads for the given upload IDs.

    `user` is required — every transcript read is scoped to its owner.
    Defaulting to None historically allowed callers to silently bypass the
    user filter, which is an IDOR. Pass an explicit user object always.
    """
    if user is None or not getattr(user, "user_id", None):
        raise ValueError("_load_transcripts requires an authenticated user")
    if not upload_ids:
        return ""
    parts = []
    total_chars = 0
    for uid in upload_ids:
        query = {"upload_id": uid, "user_id": user.user_id}
        doc = await db.chat_uploads.find_one(query, {"_id": 0})
        if not doc or not doc.get("extracted_text"):
            continue
        text = doc["extracted_text"]
        if total_chars + len(text) > _MAX_TRANSCRIPT_CHARS:
            remaining = _MAX_TRANSCRIPT_CHARS - total_chars
            if remaining > 0:
                text = text[:remaining] + "\n[...TRUNCATED]"
            else:
                break
        parts.append(f"\n--- {doc.get('filename', uid)} ---\n{text}")
        total_chars += len(text)
    return "\n".join(parts)


async def _load_prior_context(db, user, session_id=None) -> str:
    """Load approved prior call analyses from a specific vega_discovery_session.

    Only loads context when an explicit session_id is provided to prevent
    prior session data from leaking into unrelated analyses.
    """
    if not session_id:
        return ""
    prior_sessions = await db.vega_discovery_sessions.find(
        {"session_id": session_id, "user_id": user.user_id,
         "status": {"$in": ["in_progress", "complete"]}},
        {"_id": 0, "calls": 1},
    ).sort("updated_at", -1).to_list(1)

    context_parts = []
    for ps in prior_sessions:
        for call_num in ("1", "2"):
            call_data = ps.get("calls", {}).get(call_num, {})
            if call_data.get("user_approved") and call_data.get("analysis"):
                context_parts.append(
                    f"\n[PRIOR CALL {call_num} ANALYSIS]\n{json.dumps(call_data['analysis'], default=str)}"
                )

    if context_parts:
        return "\nPRIOR APPROVED ANALYSES:" + "".join(context_parts)
    return ""


# ──────────────────────────────────────────────
#  Orchestration
# ──────────────────────────────────────────────

async def run_transcript_analysis(
    db, user, upload_ids: list[str], call_number: int = 1, session_id: str = None
) -> dict:
    """Run structured transcript analysis -- returns the analysis dict and a text summary.

    Returns {"analysis": <parsed JSON>, "text": <summary string>} on success,
    or None if the transcript is empty or the LLM call fails.
    """
    from services.ai.llm import call_llm

    _log_ctx = f"[Call {call_number} | session={session_id} | user={user.user_id}]"

    transcript_text = await _load_transcripts(db, upload_ids, user=user)
    if not transcript_text.strip():
        logger.warning("%s No transcript text found for upload_ids=%s", _log_ctx, upload_ids)
        return None

    logger.info("%s Transcript loaded (%d chars) from %d upload(s)", _log_ctx, len(transcript_text), len(upload_ids))

    prior_context = await _load_prior_context(db, user, session_id)

    prompt = ANALYSIS_PROMPT.format(
        call_number=call_number,
        prior_context=prior_context,
        transcript_text=transcript_text,
    )

    from services.ai.conductor import _get_model
    provider, model = await _get_model(db, "emp_ai_vega")

    t0 = time.monotonic()
    resp = await call_llm(
        system_prompt="You are Vega, the AI Recruiter Agent. Return structured JSON analysis.",
        user_message=prompt,
        provider=provider,
        model=model,
        response_schema=TRANSCRIPT_ANALYSIS_SCHEMA,
        timeout=VEGA_LLM_TIMEOUT,
        temperature=VEGA_LLM_TEMPERATURE,
    )
    llm_elapsed = time.monotonic() - t0

    if not resp or not resp.text:
        logger.warning("%s Empty LLM response after %.1fs", _log_ctx, llm_elapsed)
        return None

    logger.info("%s LLM responded in %.1fs (%d chars)", _log_ctx, llm_elapsed, len(resp.text))

    try:
        analysis = json.loads(resp.text)
    except json.JSONDecodeError:
        logger.error("%s Failed to parse JSON response: %s", _log_ctx, resp.text[:500])
        return {"error": "Analysis returned invalid format. Please try uploading the transcript again."}

    # After successful analysis, link uploads to the session
    for uid in upload_ids:
        await db.chat_uploads.update_one(
            {"upload_id": uid, "user_id": user.user_id},
            {"$set": {"linked_to": {"call_number": call_number}}},
        )

    summary_text = analysis.get("summary", "Analysis complete.")

    from services.vega_events import emit_vega_event
    await emit_vega_event(db, user.user_id, session_id, "transcript_analyzed", {"call_number": call_number, "upload_count": len(upload_ids)})

    return {"analysis": analysis, "text": summary_text}


# ──────────────────────────────────────────────
#  Call 2 — AI Employee Matrix schema + prompt
# ──────────────────────────────────────────────

_MMI_DIM_SCHEMA = {
    "type": "object",
    "properties": {
        "value": {"type": "integer"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reasoning": {"type": "string"},
    },
    "required": ["value", "confidence", "reasoning"],
}

AI_EMPLOYEE_MATRIX_SCHEMA = {
    "type": "object",
    "properties": {
        "matrix": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "workflow_name": {"type": "string"},
                    "description": {"type": "string"},
                    "source_apps": {"type": "array", "items": {"type": "string"}},
                    "data_state": {"type": "string", "enum": ["blind", "siloed", "visible"]},
                    "manual_hours_per_week": {"type": "number"},
                    "stakeholders": {"type": "array", "items": {"type": "string"}},
                    "mmi_hypothesis": {
                        "type": "object",
                        "properties": {
                            "tss": _MMI_DIM_SCHEMA,
                            "ie": _MMI_DIM_SCHEMA,
                            "time": _MMI_DIM_SCHEMA,
                            "cost": _MMI_DIM_SCHEMA,
                            "quality": _MMI_DIM_SCHEMA,
                            "satisfaction": _MMI_DIM_SCHEMA,
                            "needs_client_input": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["tss", "ie", "time", "cost", "quality", "satisfaction", "needs_client_input"],
                    },
                    "integration_points": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "system_name": {"type": "string"},
                                "integration_type": {"type": "string", "enum": ["api", "file_transfer", "manual_entry", "database", "webhook", "unknown"]},
                                "current_state": {"type": "string", "description": "How the integration currently works"},
                                "data_exchanged": {"type": "string", "description": "What data flows through this integration"},
                                "notes": {"type": "string"},
                            },
                            "required": ["system_name", "integration_type", "current_state"],
                        },
                        "description": "All system integration points for this workflow"
                    },
                    "sla_requirements": {
                        "type": "object",
                        "properties": {
                            "expected_duration": {"type": "string", "description": "How long the workflow currently takes end-to-end"},
                            "target_duration": {"type": "string", "description": "What the target duration should be"},
                            "current_bottleneck": {"type": "string", "description": "What step is the biggest time bottleneck"},
                        },
                    },
                    # BPMN-compatible fields for diagram generation
                    "actors": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                                "type": {"type": "string", "enum": ["human", "ai", "external_system"]},
                            },
                            "required": ["id", "name", "type"],
                        },
                    },
                    "trigger": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["scheduled", "event", "manual", "api_call"]},
                            "description": {"type": "string"},
                            "actor_id": {"type": "string"},
                        },
                        "required": ["type", "description", "actor_id"],
                    },
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                                "actor_id": {"type": "string"},
                                "task_type": {"type": "string", "enum": ["user", "service", "business_rule", "manual"]},
                                "description": {"type": "string"},
                                "data_inputs": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "source_system": {"type": "string"},
                                            "format": {"type": "string"},
                                        },
                                        "required": ["name", "source_system"],
                                    },
                                    "description": "Data inputs this step requires"
                                },
                                "data_outputs": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "destination_system": {"type": "string"},
                                            "format": {"type": "string"},
                                        },
                                        "required": ["name", "destination_system"],
                                    },
                                    "description": "Data outputs this step produces"
                                },
                                "error_handling": {
                                    "type": "string",
                                    "description": "What happens when this step fails or encounters an error"
                                },
                            },
                            "required": ["id", "name", "actor_id", "task_type"],
                        },
                    },
                    "decision_points": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "after_step_id": {"type": "string"},
                                "type": {"type": "string", "enum": ["exclusive", "parallel", "inclusive"]},
                                "name": {"type": "string"},
                                "branches": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "condition": {"type": "string"},
                                            "leads_to_step_id": {"type": "string"},
                                        },
                                        "required": ["condition", "leads_to_step_id"],
                                    },
                                },
                            },
                            "required": ["id", "after_step_id", "type", "name", "branches"],
                        },
                    },
                    "exception_paths": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "trigger": {"type": "string"},
                                "from_step_id": {"type": "string"},
                            },
                            "required": ["id", "trigger", "from_step_id"],
                        },
                    },
                    "outputs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                                "after_step_id": {"type": "string"},
                            },
                            "required": ["id", "name", "after_step_id"],
                        },
                    },
                    "estimated_workflow_category": {
                        "type": "string",
                        "enum": ["bottleneck", "data_heavy", "repeated", "decision_intensive", "compliance"],
                        "description": "Primary workflow category classification"
                    },
                    "aegisaxiom_module_dependencies": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "AEGISAXIOM platform modules this workflow depends on (e.g., 'deals', 'contracts', 'field_services', 'invoices')"
                    },
                    "transformation_logic": {
                        "type": "string",
                        "description": "What transformation the AI applies to input data to produce output (the core value-add)"
                    },
                    "evidence_pack": {
                        "type": "string",
                        "description": "150-300 words of grounding evidence the downstream AI Design Document writer will quote: 3-5 verbatim quotes from the transcript with attribution ('Sarah, Call 2: \"…\"'), the concrete numbers (hours/week, error rates, dollar amounts), the named stakeholders and their roles, and the specific systems mentioned. This is the only context the design-doc writer sees beyond the matrix entry — make it dense, specific, and free of paraphrase.",
                    },
                },
                "required": [
                    "workflow_id", "workflow_name", "description",
                    "source_apps", "data_state", "stakeholders", "mmi_hypothesis",
                    "actors", "trigger", "steps", "decision_points", "exception_paths", "outputs",
                ],
            },
        },
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "what_we_know": {"type": "string"},
                    "what_we_need_to_ask": {"type": "string"},
                },
                "required": ["topic", "what_we_know", "what_we_need_to_ask"],
            },
        },
        "next_call_agenda": {"type": "array", "items": {"type": "string"}},
        "call_analysis": {
            "type": "object",
            "description": "Analysis of the call itself — what went well, what needs improvement, what was missed",
            "properties": {
                "summary": {"type": "string", "description": "2-3 sentence summary of the call"},
                "what_went_well": {"type": "array", "items": {"type": "string"}, "description": "Things the rep did well"},
                "areas_for_improvement": {"type": "array", "items": {"type": "string"}, "description": "Areas where the rep could improve"},
                "missed_items": {"type": "array", "items": {"type": "string"}, "description": "Important topics or questions that were missed"},
            },
            "required": ["summary", "what_went_well", "areas_for_improvement", "missed_items"],
        },
        "stakeholder_strategies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "stakeholder_name": {"type": "string"},
                    "role": {"type": "string"},
                    "buyer_type": {"type": "string"},
                    "pain_summary": {"type": "string"},
                    "selling_strategy": {"type": "string"},
                    "hook_question": {"type": "string", "description": "Opening question for Call 3"},
                    "tactical_advice": {"type": "string"},
                },
                "required": ["stakeholder_name", "role", "pain_summary", "selling_strategy", "hook_question", "tactical_advice"],
            },
            "description": "Updated per-stakeholder selling strategies for Call 3"
        },
        "workflow_deep_dive_feedback": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "workflow_name": {"type": "string"},
                    "completeness_assessment": {"type": "string", "description": "How complete is scoping? What's missing?"},
                    "call_3_questions": {"type": "array", "items": {"type": "string"}},
                    "pro_tip": {"type": "string"},
                },
                "required": ["workflow_name", "completeness_assessment", "call_3_questions"],
            },
            "description": "Per-workflow completeness assessment and Call 3 questions"
        },
        "call_3_approach_summary": {
            "type": "string",
            "description": "Brief strategic summary for Call 3 approach"
        },
    },
    "required": ["matrix", "gaps", "next_call_agenda", "call_analysis", "stakeholder_strategies", "workflow_deep_dive_feedback", "call_3_approach_summary"],
}

CALL2_ANALYSIS_PROMPT = """You are Vega, the AI Recruiter Agent. Analyze the Call 2 deep-dive transcript and build the AI Employee Matrix.

EXHAUSTIVE MATRIX RULE (CRITICAL):
Identify EVERY workflow discussed across the transcripts. The matrix is exhaustive — prioritization happens later via MMI scoring. Do NOT cap at 3, 5, or any number. If 12 workflows came up, return all 12. If 4 came up, return 4. Quality of identification beats arbitrary trimming. The downstream system handles ranking; your job is full coverage.

CRITICAL WORKFLOW PRESERVATION RULE:
You MUST include ALL workflows identified in the prior Call 1 analysis in your matrix output. The Call 2 transcript should elaborate on workflows from Call 1. If a workflow from Call 1 was discussed in Call 2, include it with full detail. If a workflow from Call 1 was NOT discussed in Call 2, STILL include it in the matrix with what you know from Call 1, and add it to the gaps list noting "Not discussed in Call 2 — needs Call 3 attention." NEVER drop a workflow just because it wasn't the main focus of this call.

CAMUNDA BPMN 2.0 TERMINOLOGY — USE THESE EXACT TERMS:
When describing workflow elements, use the Camunda standard BPMN 2.0 element types from the BPMN Element Reference in your knowledge base:
- Human tasks → bpmn:userTask
- System/API calls → bpmn:serviceTask
- AI decisions/rules → bpmn:businessRuleTask
- Physical actions → bpmn:manualTask
- Scheduled triggers → bpmn:timerStartEvent
- Event triggers → bpmn:messageStartEvent
- Manual triggers → bpmn:startEvent
- Exclusive decisions (XOR) → bpmn:exclusiveGateway
- Parallel splits (AND) → bpmn:parallelGateway
- Errors/timeouts on tasks → bpmn:boundaryEvent
- Process end → bpmn:endEvent

FIRST-PRINCIPLES WORKFLOW ANALYSIS:
For each workflow, mentally strip away the human handoffs and identify:
1. RAW INPUTS: What data enters this workflow? From where? In what format?
2. TRANSFORMATIONS: What happens to that data? What decisions are made? What rules apply?
3. OUTPUTS: What is produced? Where does it go? Who consumes it?
4. COMPRESSION OPPORTUNITIES: Which human steps are pure handoffs that AI can eliminate? Which are judgment calls that AI can augment but not replace?

WORKFLOW CATEGORY CLASSIFICATION:
For each workflow in the matrix, set estimated_workflow_category to one of: bottleneck, data_heavy, repeated, decision_intensive, compliance. Also set aegisaxiom_module_dependencies to list which AEGISAXIOM modules this workflow touches (deals, contracts, field_services, invoices, commissions, etc.). Set transformation_logic to describe what the AI does with input data to produce output.

CALL NUMBER: 2
{prior_context}

[TRANSCRIPT START]
{transcript_text}
[TRANSCRIPT END]

Build the AI Employee Matrix. For each workflow discussed:
- Assign a stable workflow_id (e.g., "wf_maintenance_triage")
- Capture: name, description, source_apps, data_state (blind/siloed/visible), manual_hours_per_week, stakeholders
- For MMI hypothesis: estimate each of the 6 dimensions (tss, ie, time, cost, quality, satisfaction) on 1-5 scale with confidence (high/medium/low) and reasoning. Add dimensions with "low" confidence to needs_client_input.

STAKEHOLDER SELLING STRATEGIES FOR CALL 3 (CRITICAL — DO NOT SKIP):
For EACH stakeholder identified across Call 1 and Call 2, produce a detailed, actionable selling strategy:
- Classify buyer type (e.g., ROI & Bandwidth Buyer, Friction & Day-to-Day Buyer, Technical/Security Buyer, Executive Sponsor)
- Summarize their current pain in one sentence (updated with Call 2 insights, using their own words where possible)
- Write a specific Call 3 hook question for confirming their workflow or MMI score
- Write tactical advice for the final review conversation — what to say, what to avoid, how to frame the conversation
- Include a "pro tip" for each stakeholder — a specific tactical maneuver based on their personality/role
Example: "Sarah is the Friction & Operations Buyer. Pain: 12-15 hrs/week of triage work. Hook: 'Sarah, we've mapped your triage process into 7 steps. Walk me through whether this matches your morning routine.' Tactical: Have her validate the BPMN step by step. Pro Tip: She responds to visual proof — pull up the BPMN diagram on screen and walk through it live."

WORKFLOW DEEP-DIVE FEEDBACK (CRITICAL — DO NOT SKIP):
For EACH workflow in the matrix:
- Assess completeness: Are all 5 BPMN categories (trigger, dependency, logic, exceptions, outcomes) covered?
- Generate at least 3-5 specific Call 3 questions per workflow for any gaps
- Include a pro tip for getting the remaining information
- Rate readiness: ready_for_design / needs_minor_clarification / needs_major_investigation

CALL 3 APPROACH SUMMARY:
Write a detailed strategic summary: "With [Person]: Confirm [X]. With [Person]: Resolve [Y]." Call 3 should be confirmation-focused, not discovery-focused. Include specific talking points for each stakeholder.

Also identify:
- Gaps: topics that still need clarification, with what we know and what we need to ask
- Next call agenda: items for Call 3 to resolve gaps and finalize scoring

BPMN DIAGRAM GENERATION — PROPOSED AI EMPLOYEE WORKFLOW:
The BPMN diagram should document the PROPOSED workflow with the AI Employee in place, NOT the current manual process. Show where the AI handles steps (type=ai) vs where humans remain involved (type=human). Include the AI as an actor with type "ai" and name like "AI Employee" or the specific workflow name.

For BPMN diagram generation, provide per workflow:
- actors: list of {{id, name, type}} where type is human/ai/external_system. ALWAYS include an AI actor (type=ai) for the automated steps. Use short IDs like "a1", "a2".
- trigger: {{type (scheduled/event/manual/api_call), description, actor_id}} referencing a valid actor ID.
- steps: ordered list of {{id, name, actor_id, task_type (user/service/business_rule/manual), description}}. Use "s1", "s2" etc. Assign AI-automated steps to the AI actor. Mark human review/approval steps with a human actor.
- decision_points: {{id, after_step_id, type (exclusive/parallel/inclusive), name, branches: [{{condition, leads_to_step_id}}]}}. Use "d1", "d2" etc.
- exception_paths: {{id, trigger description, from_step_id}}. Use "e1", "e2" etc.
- outputs: {{id, name, after_step_id}}. Use "o1", "o2" etc.
Every step's actor_id and trigger's actor_id must reference a valid actor ID. Every decision branch's leads_to_step_id must reference a valid step ID.

For enhanced workflow detail, also capture per workflow:
- integration_points: every system this workflow touches, with integration type (api/file_transfer/manual_entry/database/webhook), current state, and data exchanged
- sla_requirements: current duration, target duration, and the biggest bottleneck step

For each step in a workflow:
- data_inputs: what data does this step need, from which system, in what format
- data_outputs: what data does this step produce, where does it go, in what format
- error_handling: what happens when this step fails

Aim for a MINIMUM of 5 steps per workflow. If the transcript describes fewer, infer the intermediate steps that must logically exist (e.g., data lookup, validation, notification) but flag inferred steps with "[INFERRED]" in the description.

For each decision point branch, describe the CONSEQUENCE of that branch, not just the condition.

GAP AND QUESTION GENERATION — BE EXHAUSTIVE:
- For EVERY workflow in the matrix, check ALL 5 Call 2 categories (Trigger Mechanisms, Dependency Mapping, Logic Rules, Exception Handling, Outcome Definition). If ANY category is incomplete, it is a gap.
- Generate questions for every missing BPMN element — if a step lacks data inputs, ask about them. If a decision point lacks specific thresholds, ask.
- Include questions about data formats, error rates, SLAs, and edge cases that were not discussed.
- Include questions about integration authentication methods, API versioning, and data freshness for each source system.
- A gap list of 2-3 items for a multi-workflow client is almost certainly incomplete. Be thorough.
- For each low-confidence MMI dimension, generate a specific question to resolve it.

Provide a call_analysis section:
- summary: 2-3 sentence overview of the call
- what_went_well: specific things the rep did effectively (good questions asked, rapport building, etc.)
- areas_for_improvement: where the rep could have probed deeper or handled differently
- missed_items: important topics or questions that should have been covered but weren't

Be specific. Use the client's own words. Flag contradictions with Call 1."""


# ──────────────────────────────────────────────
#  AI Design Document schema (per-workflow)
# ──────────────────────────────────────────────

AI_DESIGN_DOCUMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "workflow_id": {"type": "string"},
        "workflow_name": {"type": "string"},
        "executive_summary": {"type": "string", "description": "2-3 paragraph overview of this AI employee's purpose and value"},
        "current_state": {
            "type": "object",
            "properties": {
                "process_description": {"type": "string", "description": "How this workflow currently operates"},
                "pain_points": {"type": "array", "items": {"type": "string"}},
                "systems_involved": {"type": "array", "items": {"type": "string"}},
                "manual_effort": {"type": "string", "description": "Current manual hours and effort description"},
                "error_rate_notes": {"type": "string", "description": "Known error rates or quality issues"},
            },
            "required": ["process_description", "pain_points", "systems_involved", "manual_effort"],
        },
        "proposed_ai_solution": {
            "type": "object",
            "properties": {
                "solution_description": {"type": "string", "description": "What the AI employee will do"},
                "ai_role": {"type": "string", "description": "The AI's specific responsibilities"},
                "human_role": {"type": "string", "description": "What humans continue to do"},
                "automation_percentage": {"type": "string", "description": "Estimated percentage of workflow that can be automated"},
                "key_capabilities": {"type": "array", "items": {"type": "string"}, "description": "Core capabilities the AI needs"},
            },
            "required": ["solution_description", "ai_role", "human_role", "key_capabilities"],
        },
        "bpmn_process_narrative": {"type": "string", "description": "Plain-English walkthrough of the BPMN process flow from start to end"},
        "integration_requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "system_name": {"type": "string"},
                    "integration_type": {"type": "string", "enum": ["api", "file_transfer", "manual_entry", "database", "webhook", "unknown"]},
                    "data_exchanged": {"type": "string"},
                    "complexity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "notes": {"type": "string"},
                },
                "required": ["system_name", "integration_type", "data_exchanged", "complexity"],
            },
        },
        "data_flow": {
            "type": "object",
            "properties": {
                "inputs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "source": {"type": "string"},
                            "format": {"type": "string"},
                        },
                        "required": ["name", "source"],
                    },
                },
                "processing": {"type": "string", "description": "How the AI processes the data"},
                "outputs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "destination": {"type": "string"},
                            "format": {"type": "string"},
                        },
                        "required": ["name", "destination"],
                    },
                },
                "data_sensitivity": {"type": "string", "description": "PII, financial, health, or other sensitive data considerations"},
            },
            "required": ["inputs", "processing", "outputs"],
        },
        "mmi_justification": {
            "type": "object",
            "properties": {
                "overall_score": {"type": "number"},
                "tss_rationale": {"type": "string"},
                "ie_rationale": {"type": "string"},
                "time_rationale": {"type": "string"},
                "cost_rationale": {"type": "string"},
                "quality_rationale": {"type": "string"},
                "satisfaction_rationale": {"type": "string"},
            },
            "required": ["tss_rationale", "ie_rationale", "time_rationale", "cost_rationale", "quality_rationale", "satisfaction_rationale"],
        },
        "implementation_considerations": {
            "type": "object",
            "properties": {
                "risks": {"type": "array", "items": {"type": "string"}},
                "dependencies": {"type": "array", "items": {"type": "string"}},
                "assumptions": {"type": "array", "items": {"type": "string"}},
                "open_questions": {"type": "array", "items": {"type": "string"}},
                "estimated_complexity": {"type": "string", "enum": ["low", "medium", "high"]},
            },
            "required": ["risks", "dependencies", "assumptions", "estimated_complexity"],
        },
        "human_touchpoints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "touchpoint": {"type": "string", "description": "Where humans must remain involved"},
                    "reason": {"type": "string", "description": "Why automation is not appropriate here"},
                    "frequency": {"type": "string", "description": "How often this touchpoint occurs"},
                },
                "required": ["touchpoint", "reason"],
            },
        },
        "estimated_workflow_category": {
            "type": "string",
            "enum": ["bottleneck", "data_heavy", "repeated", "decision_intensive", "compliance"],
            "description": "Primary workflow category classification"
        },
        "aegisaxiom_module_dependencies": {
            "type": "array",
            "items": {"type": "string"},
            "description": "AEGISAXIOM platform modules this workflow depends on"
        },
        "trigger_specification": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["scheduled", "event_driven", "manual", "api_triggered", "threshold_based"]},
                "frequency": {"type": "string", "description": "How often this trigger fires (e.g., 'daily', '50x/week', 'on-demand')"},
                "data_source": {"type": "string", "description": "What system or event originates this trigger"},
            },
            "description": "Detailed trigger specification for this automation"
        },
    },
    "required": [
        "workflow_id", "workflow_name", "executive_summary",
        "current_state", "proposed_ai_solution", "bpmn_process_narrative",
        "integration_requirements", "data_flow", "mmi_justification",
        "implementation_considerations", "human_touchpoints",
    ],
}

# ──────────────────────────────────────────────
#  Call 3 — Final scoring schema + prompt
# ──────────────────────────────────────────────

def _strip_enums_for_gemini(node):
    """Recursively deep-copy a JSON Schema, removing `enum` constraints and
    inlining the enum values into the field's description.

    Gemini's structured-output validator counts every enum value toward a
    "constraint state" budget; deeply-nested enums (like 6 MMI dims × N
    workflows × 6 nested enums) blow past it and produce
    `400 INVALID_ARGUMENT — too many states for serving`. Strings still get
    sensible values via the description hint."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "enum":
                continue
            out[k] = _strip_enums_for_gemini(v)
        # Surface allowed values in the description so the LLM still picks from them
        if "enum" in node:
            allowed = ", ".join(str(x) for x in node["enum"])
            existing = out.get("description", "")
            out["description"] = (existing + " " if existing else "") + f"(One of: {allowed})"
        return out
    if isinstance(node, list):
        return [_strip_enums_for_gemini(x) for x in node]
    return node


CALL3_FINAL_SCHEMA = {
    "type": "object",
    "properties": {
        "matrix": _strip_enums_for_gemini(AI_EMPLOYEE_MATRIX_SCHEMA["properties"]["matrix"]),
        "gaps": AI_EMPLOYEE_MATRIX_SCHEMA["properties"]["gaps"],
        "contradictions_resolved": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "prior_understanding": {"type": "string"},
                    "resolved_to": {"type": "string"},
                },
                "required": ["topic", "prior_understanding", "resolved_to"],
            },
        },
        # `ai_design_documents` is intentionally NOT in this schema.
        # We fan out one LLM call per workflow against
        # AI_DESIGN_DOCUMENT_SCHEMA in run_call3_analysis's parallel
        # post-processing block — keeps each call inside Gemini's
        # structured-output state budget and grounds every doc in the
        # per-workflow `evidence_pack` returned in the matrix below.
        "emergent_apps": {
            "type": "array",
            "description": "Standalone app/dashboard/tool ideas the client mentioned that are NOT 1:1 with an AI workflow. Empty array is fine if no apps were discussed.",
            "items": {
                "type": "object",
                "properties": {
                    "app_id": {"type": "string", "description": "Stable id like 'app_invoice_dashboard'"},
                    "app_name": {"type": "string", "description": "Short product-style name (e.g., 'Invoice Triage Dashboard')"},
                    "app_pitch_summary": {"type": "string", "description": "1-2 sentence pitch — what it does and who it's for"},
                    "primary_persona_hint": {"type": "string", "description": "Who would use this most — role/title"},
                    "linked_workflow_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: workflow_ids this app is backed by, if any",
                    },
                },
                "required": ["app_id", "app_name", "app_pitch_summary"],
            },
        },
        "client_industry": {"type": "string", "description": "Client's industry sector (used as context for App Design generation)"},
        "stakeholders_summary": {"type": "string", "description": "Brief summary of key stakeholders identified across the calls"},
    },
    "required": ["matrix", "gaps", "contradictions_resolved"],
}

CALL3_FINAL_PROMPT = """You are Vega, the AI Recruiter Agent. Analyze the Call 3 transcript and produce the FINAL discovery deliverables.

CALL NUMBER: 3
{prior_context}

EXISTING AI EMPLOYEE MATRIX:
{existing_matrix}

[TRANSCRIPT START]
{transcript_text}
[TRANSCRIPT END]

This is the FINAL discovery call. You have TWO jobs:

EXHAUSTIVE MATRIX RULE (CRITICAL):
Carry forward EVERY workflow from the existing matrix and add any new ones surfaced in Call 3. The matrix is exhaustive — prioritization happens later via MMI scoring. Do NOT cap the workflow count at 3, 5, or any number. If 12 workflows are in scope, return all 12. Never drop a workflow because it scored lower than another.

═══ PART 1: FINALIZE THE AI EMPLOYEE MATRIX ═══

1. Update each workflow in the matrix with any new information from Call 3.
2. Resolve all gaps from Call 2 — for each gap, record what the prior understanding was and what it resolved to.
3. Finalize all MMI dimension scores (1-5) with high-confidence values where possible.
4. If any dimensions still cannot be scored with confidence, note them in needs_client_input.
5. Keep workflow_ids stable from Call 2 — only add new workflows if truly new processes were discovered.

═══ PART 3: IDENTIFY STANDALONE APP IDEAS ═══

The client may have mentioned tools, dashboards, or apps they want to prototype that are NOT 1:1 with an AI workflow. Surface those in the `emergent_apps` array. Examples:
  - "We need a triage dashboard our ops team can use to see incoming tickets"
  - "I want a portal where contractors can submit photos"
  - "A simple tool that lets the partner manager see commission status"

For each app surfaced, return: a stable app_id, short product-style app_name, 1-2 sentence app_pitch_summary, primary_persona_hint (the role most likely to use it), and optionally linked_workflow_ids if the app is backed by AI workflows in the matrix.

**Empty array is fine** if no standalone apps were discussed. Do NOT invent apps to fill the array. Only surface what was actually mentioned. An app may reference workflow_ids it's backed by, but does not have to.

Also fill `client_industry` (e.g., "property management", "healthcare staffing") and `stakeholders_summary` (one paragraph naming the key stakeholders and their roles). These feed downstream App Design generation.

═══ PART 2: PRODUCE EVIDENCE PACKS FOR EACH WORKFLOW ═══

For EACH workflow in the matrix, populate the `evidence_pack` field. This is the ONLY transcript context that the downstream AI Design Document writer (a separate per-workflow LLM call) will see — make it dense and specific.

Each evidence_pack is 150-300 words and MUST include:
  1. **3-5 verbatim quotes** from the transcript with speaker attribution.
     Format: `Sarah (Ops Lead): "we spend probably 12 hours a week just triaging tickets"`.
     Pull quotes that anchor the workflow's pain, current process, or desired outcome.
  2. **Concrete numbers**: hours/week of manual effort, error rates, dollar amounts, ticket volumes, SLA durations, transaction counts. Cite who said them.
  3. **Named stakeholders + roles** as they appear in the transcript ("Mark, the warehouse manager"; "Diane in AP").
  4. **Specific systems / vendor names** the workflow touches (NetSuite, Salesforce, ServiceNow — not "ERP" or "ticketing system"). Note the integration shape if mentioned (API, CSV export, manual rekey).
  5. **Edge cases or exceptions** the client called out ("but if the invoice is over $10K it has to go to the CFO").

NO paraphrase, NO summary, NO restating the matrix. Pure source material the design-doc writer will quote directly. If a quote isn't available for a field, write `(no transcript evidence)` rather than inventing one.

CAMUNDA NAMING RULES (apply to every workflow's BPMN data — actors / trigger / steps / decision_points / exception_paths / outputs):
- **Tasks**: object + verb infinitive. "Review invoice" YES; "Process invoice" NO; "Invoice processing" NO.
- **Events**: object + past-tense state. "Invoice paid" YES; "Pay invoice" NO.
- **Gateways**: phrased as a question. "Amount > $10K?" YES; "Approval check" NO.
- **Lanes**: role/system in title case. "Sales Ops", "NetSuite", "Account Manager". Never "Worker 1".
- **Data inputs/outputs**: noun-case naming the artifact. "Approved Invoice", "Customer Email". Never "data1", "input".
- Replace generic verbs (Handle, Process, Manage, Do) with specific ones (Validate, Approve, Reconcile, Classify, Notify).

For each workflow also classify its `estimated_workflow_category` and list `aegisaxiom_module_dependencies`. Describe the `transformation_logic` — what the AI specifically does with input data to produce output.

FOLLOW-UP QUESTIONS USING BPMN VOCABULARY (when identifying gaps):
- "What is the bpmn:timerStartEvent frequency for the invoicing workflow?"
- "Is the approval step a bpmn:userTask or can it be a bpmn:businessRuleTask with thresholds?"
- "What happens at the bpmn:exclusiveGateway when the amount exceeds $10K — who reviews?"

Be specific. Use the client's own words. Never hallucinate — if something wasn't discussed, flag it as a gap rather than filling it in."""


# ──────────────────────────────────────────────
#  EADD — Emergent App Design Document
# ──────────────────────────────────────────────
# Internal name: emergent_app_design / EADD. User-facing strings ALWAYS render
# this as "App Design" / "App Designs" — see Decision 8 in
# .waves/vega-v2/README.md.

EADD_SCHEMA = {
    "type": "object",
    "properties": {
        "starter_prompt": {
            "type": "string",
            "description": "The complete starter prompt to paste into Emergent. Self-contained, includes pitch + persona + flows + data + integrations + visual style + out-of-scope.",
        },
        "core_flows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short flow name, e.g. 'Submit a triage ticket'."},
                    "actor": {"type": "string", "description": "Persona / role that performs this flow."},
                    "trigger": {"type": "string", "description": "What kicks the flow off — 'user clicks Open in queue', 'webhook from AI workflow with confidence<0.95', 'cron at 02:00 UTC'. Vague triggers are unacceptable."},
                    "preconditions": {"type": "array", "items": {"type": "string"}, "description": "What must be true for the flow to start (e.g., 'order is in pending status', 'user has reviewer role')."},
                    "description": {"type": "string", "description": "1-2 sentence description of what the user does in this flow."},
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Numbered user-action / system-response steps. Each step is one observable action — 'user clicks Approve', 'system writes to AppDirect via integration', 'system creates AuditEntry'. Aim for 4-10 steps.",
                    },
                    "success_when": {
                        "type": "string",
                        "description": "REQUIRED acceptance criterion — 'this flow succeeds when X'. Concrete + testable: 'order.status transitions to approved AND an AuditEntry row exists referencing the user_id AND the AppDirect outbound integration returned 200'.",
                    },
                    "error_states": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "condition": {"type": "string", "description": "What goes wrong — 'AppDirect returns 5xx', 'user navigates away mid-edit', 'attached file > 25MB'."},
                                "behavior": {"type": "string", "description": "How the app handles it — 'show retry banner with exponential backoff', 'auto-save draft and prompt on resume'."},
                            },
                            "required": ["condition", "behavior"],
                        },
                        "description": "REQUIRED — at minimum cover: integration failure, validation failure, and concurrent edit / lost work. List every realistic failure mode the build team must handle.",
                    },
                    "side_effects": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Other entities written, integrations called, notifications sent, audit logs created as a result of this flow. List every observable side effect.",
                    },
                },
                "required": ["name", "actor", "trigger", "description", "steps", "success_when", "error_states"],
            },
            "description": "3-7 core user flows. Each MUST carry actor, trigger, preconditions, success_when, error_states, and side_effects — so a dev can write the controller, the integration calls, and the test suite without follow-up.",
        },
        "data_model": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "Entity name in PascalCase, e.g. 'Order', 'Exception'."},
                    "description": {"type": "string", "description": "1-sentence purpose of this entity."},
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string", "description": "Concrete type: 'uuid', 'string', 'int', 'decimal(10,2)', 'jsonb', 'datetime', 'enum', 'fk:Order.id', etc. NEVER leave a field type-less."},
                                "nullable": {"type": "boolean"},
                                "enum_values": {"type": "array", "items": {"type": "string"}, "description": "Required when type='enum'. List every allowed value."},
                                "description": {"type": "string"},
                            },
                            "required": ["name", "type", "nullable", "description"],
                        },
                        "description": "Every field with a concrete type. Include id, foreign keys (typed as fk:OtherEntity.id), enum value lists, timestamps (created_at/updated_at) where appropriate, and any audit columns implied by the design.",
                    },
                    "state_machine": {
                        "type": "object",
                        "properties": {
                            "states": {"type": "array", "items": {"type": "string"}, "description": "Every status / lifecycle state value the entity can hold."},
                            "transitions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "from": {"type": "string"},
                                        "to": {"type": "string"},
                                        "trigger": {"type": "string", "description": "What event or user action causes this transition."},
                                    },
                                    "required": ["from", "to", "trigger"],
                                },
                            },
                        },
                        "description": "Required for the primary entity (the one with a status field). Optional for entities with no lifecycle.",
                    },
                    "notes": {"type": "string"},
                },
                "required": ["entity", "description", "fields"],
            },
            "description": "Core entities the app manages. Be exhaustive: include EVERY entity the user will read, write, or reference — orders, line items, exceptions, audit log entries, attachments, users, etc. A dev should be able to write the migrations from this list without follow-up.",
        },
        "integrations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target_name": {"type": "string", "description": "Use the EXACT name from the solution landscape so cross-references resolve."},
                    "direction": {"type": "string", "description": "in / out / bidirectional"},
                    "trigger": {"type": "string", "description": "Concrete trigger: 'webhook POST when AI workflow output confidence < 0.95', 'nightly cron at 02:00 UTC', 'user clicks Approve button'. Vague triggers are unacceptable."},
                    "payload": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string"},
                                "type": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["field", "type"],
                        },
                        "description": "Concrete payload schema — list every field the receiving side will need, with types. NOT a freeform description.",
                    },
                    "auth_note": {"type": "string", "description": "Auth handoff details if relevant (shared service account, token forwarding, etc.)."},
                },
                "required": ["target_name", "direction", "trigger", "payload"],
            },
            "description": "Cross-system handoffs. Each entry is a contract the build team can write code against without follow-up.",
        },
        "business_rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "rule": {"type": "string", "description": "Concrete decision rule — 'IF confidence < 0.85 THEN route to manual review queue with priority=high'. Use IF/THEN form. Include numeric thresholds the customer stated; flag inferred thresholds with [INFERRED]."},
                    "rationale": {"type": "string"},
                },
                "required": ["name", "rule"],
            },
            "description": "Concrete business rules the app must enforce. Confidence-band routing, escalation thresholds, retry policies, cutoffs — anything that's IF/THEN logic. Empty list ONLY when the app is pure CRUD with no decision logic.",
        },
        "file_handling": {
            "type": "object",
            "properties": {
                "accepts": {"type": "array", "items": {"type": "string"}, "description": "File types the app must ingest, e.g. 'PDF', 'Excel (.xlsx)', 'CSV', 'image (PNG/JPG)'. Empty array if no file handling."},
                "extraction_strategy": {"type": "string", "description": "How the app extracts data from those files: 'OCR for scanned PDFs, text extract for native PDFs, openpyxl for xlsx', 'preview-only, no extraction'. Required when accepts is non-empty."},
                "max_size_mb": {"type": "integer", "description": "Reasonable per-file size cap. Default 25 if not stated."},
            },
            "description": "Required when ANY core flow involves file uploads, attachments, or document review. Empty object otherwise.",
        },
        "api_surface": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "HTTP method: GET / POST / PUT / PATCH / DELETE."},
                    "path": {"type": "string", "description": "REST path, e.g. '/api/orders/:id/approve'."},
                    "purpose": {"type": "string"},
                    "request_shape": {"type": "string", "description": "Concrete request body shape — '{order_id: uuid, corrections: {field: string, value: string}[]}', or 'query params: status, page, page_size'."},
                    "response_shape": {"type": "string", "description": "Concrete response body shape — '{order: Order, audit_entry_id: uuid}', '204 No Content'."},
                    "auth_required": {"type": "string", "description": "Roles that may call this — 'reviewer', 'reviewer | admin', 'public'."},
                },
                "required": ["method", "path", "purpose", "request_shape", "response_shape", "auth_required"],
            },
            "description": "Every endpoint the dev team needs to implement. Cover CRUD on every entity in data_model PLUS the action endpoints implied by core_flows (approve, reject, escalate, etc.). A dev should be able to scaffold the routes file from this list.",
        },
        "auth_model": {
            "type": "object",
            "properties": {
                "session_model": {"type": "string", "description": "How the app authenticates its OWN users — 'JWT in httpOnly cookie, 8h expiry, refresh-token rotation', 'SAML SSO via Okta', 'AEGISAXIOM platform SSO'. Concrete enough to implement."},
                "roles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "permissions": {"type": "array", "items": {"type": "string"}, "description": "What this role can do — 'view all orders', 'approve own queue', 'manage user roles'."},
                        },
                        "required": ["name", "permissions"],
                    },
                    "description": "Every role + the permissions it carries. RBAC enforcement strategy implied by these.",
                },
                "ownership_model": {"type": "string", "description": "How rows are scoped — 'orders are visible to anyone with reviewer role; assignment is via order.assigned_user_id which only that reviewer or admin can change'. Required when the persona table mentions 'visibility' or 'access'."},
            },
            "description": "App-side auth + RBAC. Different from per-integration auth_method — this is how the customer's USERS log into THIS app and what they can do. Required for any app with more than one persona role.",
        },
        "observability": {
            "type": "object",
            "properties": {
                "events_to_log": {"type": "array", "items": {"type": "string"}, "description": "Domain events the dev team should emit — 'order.approved', 'exception.escalated', 'integration.appdirect.write_failed'. Drives audit log and metrics."},
                "metrics_to_track": {"type": "array", "items": {"type": "string"}, "description": "Operational metrics — 'p95 page load on review screen', 'queue depth by confidence band', 'integration error rate'."},
                "alerts": {"type": "array", "items": {"type": "string"}, "description": "What pages on-call — 'integration error rate > 1% over 5min', 'queue depth > 500 for > 1h'."},
            },
            "description": "How the app reports its own health. Required for production-tier apps.",
        },
        "test_strategy": {
            "type": "object",
            "properties": {
                "unit_focus": {"type": "string", "description": "What logic deserves heavy unit coverage — typically business_rules and state_machine transitions."},
                "integration_focus": {"type": "string", "description": "What integration tests are non-negotiable — typically each external integration's success + failure paths."},
                "e2e_critical_paths": {"type": "array", "items": {"type": "string"}, "description": "Which core_flows are smoke-test critical (1-3 max)."},
            },
            "description": "How the dev team should think about testing. Brief — pointers, not a full QA plan.",
        },
        "out_of_scope": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Explicitly NOT in scope — e.g., 'no mobile native, web only', 'no SSO in v1'.",
        },
        "visual_style": {
            "type": "string",
            "description": "Short visual direction — e.g., 'clean enterprise SaaS, neutral palette, sidebar nav'.",
        },
    },
    "required": [
        "starter_prompt",
        "core_flows",
        "data_model",
        "integrations",
        "business_rules",
        "file_handling",
        "api_surface",
        "auth_model",
        "observability",
        "test_strategy",
        "out_of_scope",
        "visual_style",
    ],
}


EADD_PROMPT = """You are writing a **developer build specification** for a custom app. The audience is a senior software engineer who was NOT on the discovery calls. Your output's job is to let them build and ship the app without ever joining a call — replacing all the back-and-forth Slack/Teams threads that would otherwise be needed.

This is NOT a sales document. This is NOT an "Emergent app builder" prompt as the primary deliverable. The primary deliverable is the structured fields below — concrete data model with types, API surface, business rules, integration contracts, error semantics. Treat the optional starter_prompt as a small sales-ideation appendix at the end (one paragraph for kicking off a quick POC), not the main artifact.

CLIENT CONTEXT:
  - Industry: {client_industry}
  - Stakeholders summary: {stakeholders_summary}

THE APP TO DESIGN:
  - Name: {app_name}
  - Pitch: {app_pitch_summary}
  - Primary persona hint: {primary_persona_hint}
  - Linked AI workflows (if any): {linked_workflows_summary}

FULL SOLUTION LANDSCAPE — the OTHER apps and AI workflows in this engagement that this app must work alongside:
{solution_landscape}

YOUR JOB:
1. Produce `starter_prompt` — a SHORT (≤200 words) paragraph for sales / pre-sales to feed an AI app builder when ideating a POC. Mention pitch + primary persona + 2-3 core flows + cross-system handoffs. **This is appendix-only and is NOT the build spec.** The dev does not work from this — they work from the structured fields below. If the structured fields are vague, the doc fails regardless of how polished this prompt is.

2. Produce structured fields (`core_flows`, `data_model`, `integrations`, `business_rules`, `file_handling`, `out_of_scope`, `visual_style`) so a developer can build the app from THIS document without re-running discovery. Each field has a contract — match it.

   **Calibration rule:** if a senior engineer reading your output would have to ask a follow-up question to start coding, the field is not done. Concrete > comprehensive.

   For `core_flows`: 3-7 named user flows. Each flow MUST have:
     - `actor` — persona role
     - `trigger` — concrete kick-off event ("webhook POST when AI workflow output confidence < 0.95", NOT "when there's an exception")
     - `preconditions` — list ("order.status == pending", "user has reviewer role")
     - `steps` — 4-10 observable actions, each one user OR system not both
     - `success_when` — testable assertion ("order.status transitions to approved AND AuditEntry created AND outbound integration returned 200", NOT "ticket is created")
     - `error_states` — at minimum cover integration failure, validation failure, and concurrent edit / lost work
     - `side_effects` — every other entity written, integration called, notification sent

   For `data_model`: be exhaustive. List EVERY entity the user reads, writes, or references. For each entity:
     - `fields` — every field with a CONCRETE TYPE ('uuid', 'string', 'int', 'decimal(10,2)', 'jsonb', 'datetime', 'enum', 'fk:Order.id'). Never leave a field type-less. Always include id, FK columns, timestamps, and audit columns where appropriate.
     - When type='enum', list every allowed value in `enum_values`.
     - For the primary entity (the one with a status / lifecycle field), include a `state_machine` listing every state and every transition with its trigger.
     A dev should be able to write the migration from this list without follow-up.

   For `integrations`: every other entity from the solution landscape that this app exchanges data with. Name them by the EXACT name used in the landscape. Each integration specifies:
     - target_name, direction (in/out/bidirectional)
     - `trigger` — concrete event
     - `payload` — schema list, every field with type. NOT a freeform sentence. The receiving side should be able to write the parser from this list.

   For `business_rules`: list every IF/THEN decision the app must enforce. Confidence-band routing, escalation thresholds, retry policies, cutoffs. Use IF/THEN form. Include numeric thresholds the customer stated; flag inferred thresholds with [INFERRED]. Empty list ONLY if the app is pure CRUD with zero decision logic — for an app with confidence scoring, exception routing, or any "automatic vs manual" branching, business_rules MUST be non-empty.

   For `file_handling`: required when ANY core flow involves uploads, attachments, or document review. List accepted file types, extraction strategy (OCR for scanned PDFs, openpyxl for xlsx, etc.), and a per-file size cap.

   For `api_surface`: every endpoint the dev needs. Cover CRUD on every entity in data_model PLUS the action endpoints implied by core_flows (approve, reject, escalate, etc.). Each endpoint specifies method, path, request shape, response shape, and which roles may call it.

   For `auth_model`: how this app's USERS log in (different from per-integration auth_method, which is how the app reaches OTHER systems). Required when the persona table has more than one role. Include session_model + every role's permissions + the row ownership model (which user can see / modify which rows).

   For `observability`: domain events to log, operational metrics to track, alerts that page on-call. Required for any production-tier app.

   For `test_strategy`: brief pointers — what unit tests are critical (usually business_rules + state_machine), what integration tests are non-negotiable (each external integration's success + failure path), which 1-3 core flows are smoke-test critical.

GROUNDING RULES:
- Stay grounded in the linked workflows and the client's industry. If a workflow says invoices flow through NetSuite, your data_model must mention NetSuite, not generic ERP.
- If the linked workflows summary is empty or thin, infer reasonable defaults from the pitch + persona, but mark inferred entities/integrations clearly in the notes.
- Never invent solution-landscape entries that don't exist in the supplied list. Reference them by their exact names.
- Never include client-specific PII, internal IDs, or anything from the transcript that wasn't part of the surfaced app concept.
- Default to web-only unless the pitch clearly implies mobile.

THE THIN-DOC TEST:
Before you submit, re-read your output as if you're the senior dev who has to build this app on Monday morning, with NO calls allowed and the customer unreachable for two weeks. If any of these are true, the doc is too thin and you must go deeper:
  - Any field in data_model lacks a concrete type
  - Any enum field is missing its value list
  - The primary entity has a status field but no state_machine
  - Any integration's payload is a sentence instead of a schema
  - Any core_flow lacks actor / trigger / error_states / side_effects
  - business_rules is empty for an app that has confidence scoring or exception routing
  - An app touches files but file_handling is empty
  - api_surface doesn't cover the verbs implied by every core_flow (approve, reject, escalate, ...)
  - auth_model is empty when there's more than one persona role
  - observability has no events for state_machine transitions or integration failures
  - test_strategy doesn't name the critical-path flow

The starter_prompt section is OPTIONAL polish for sales POC ideation. It is NOT a substitute for any of the structured fields above. If the structured fields are vague, the doc fails regardless of how clean the starter_prompt reads.
"""


def _summarize_linked_workflows(matrix: list, linked_ids: list[str]) -> str:
    """Build a short, grounded summary of the workflows backing an app."""
    if not linked_ids or not matrix:
        return "(no linked workflows — app stands alone)"
    by_id = {wf.get("workflow_id"): wf for wf in matrix if wf.get("workflow_id")}
    parts = []
    for wid in linked_ids:
        wf = by_id.get(wid)
        if not wf:
            continue
        name = wf.get("workflow_name") or wid
        desc = (wf.get("description") or "")[:240]
        sources = ", ".join(wf.get("source_apps") or []) or "unspecified systems"
        parts.append(f"- {name}: {desc} (touches {sources})")
    return "\n".join(parts) if parts else "(linked_workflow_ids did not match any matrix entries)"


async def _generate_single_eadd(app: dict, matrix: list, client_industry: str,
                                 stakeholders_summary: str, db, user, session_id: str,
                                 solution_landscape: str = "") -> dict:
    """One LLM call → one Emergent App Design Document.

    Returns a dict shaped like the EmergentAppDesign model. Always returns —
    on failure, returns a `status='failed'` doc with `failure_reason` so the
    matrix-level orchestration can render a failed-state badge instead of
    aborting the whole batch.

    ``solution_landscape`` is a human-readable summary of the OTHER apps and
    workflows in the engagement. Threaded into the prompt so the EADD names
    its cross-system handoffs explicitly.
    """
    from services.ai.llm import call_llm
    from services.ai.conductor import _get_model
    from services.vega_events import emit_vega_event

    app_id = app.get("app_id") or f"app_{uuid.uuid4().hex[:8]}"
    app_name = app.get("app_name") or "(unnamed app)"

    base_doc = {
        "app_id": app_id,
        "app_name": app_name,
        "app_pitch_summary": app.get("app_pitch_summary"),
        "primary_persona_hint": app.get("primary_persona_hint"),
        "linked_workflow_ids": app.get("linked_workflow_ids", []) or [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        prompt = EADD_PROMPT.format(
            client_industry=client_industry or "(unspecified)",
            stakeholders_summary=stakeholders_summary or "(unspecified)",
            app_name=app_name,
            app_pitch_summary=base_doc["app_pitch_summary"] or "(unspecified)",
            primary_persona_hint=base_doc["primary_persona_hint"] or "(unspecified)",
            linked_workflows_summary=_summarize_linked_workflows(matrix, base_doc["linked_workflow_ids"]),
            solution_landscape=solution_landscape or "(no other workflows or apps in this engagement — this app stands alone)",
        )

        provider, model = await _get_model(db, "emp_ai_vega")
        t0 = time.monotonic()
        resp = await call_llm(
            system_prompt="You are a pragmatic product designer who writes paste-ready specs for AI app builders. Return structured JSON.",
            user_message=prompt,
            provider=provider,
            model=model,
            response_schema=EADD_SCHEMA,
            timeout=VEGA_LLM_TIMEOUT,
            temperature=VEGA_LLM_TEMPERATURE,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if not resp or not resp.text:
            await emit_vega_event(db, user.user_id, session_id, "eadd_generated",
                                 {"app_id": app_id, "latency_ms": elapsed_ms, "success": False})
            return {**base_doc, "status": "failed", "failure_reason": "empty LLM response"}

        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError as e:
            await emit_vega_event(db, user.user_id, session_id, "eadd_generated",
                                 {"app_id": app_id, "latency_ms": elapsed_ms, "success": False})
            return {**base_doc, "status": "failed", "failure_reason": f"invalid JSON: {e}"}

        await emit_vega_event(db, user.user_id, session_id, "eadd_generated",
                             {"app_id": app_id, "latency_ms": elapsed_ms, "success": True})

        return {
            **base_doc,
            "starter_prompt": payload.get("starter_prompt"),
            "core_flows": payload.get("core_flows", []),
            "data_model": payload.get("data_model", []),
            "integrations": payload.get("integrations", []),
            "out_of_scope": payload.get("out_of_scope", []),
            "visual_style": payload.get("visual_style"),
            "status": "success",
        }
    except Exception as e:
        logger.exception("EADD generation failed for app %s", app_id)
        return {**base_doc, "status": "failed", "failure_reason": str(e)[:200]}


# ──────────────────────────────────────────────
#  Per-workflow AI Design Document — fanned-out call
# ──────────────────────────────────────────────
# One LLM call per workflow against AI_DESIGN_DOCUMENT_SCHEMA. Keeps the
# structured-output state count well inside Gemini's budget (the all-in-one
# call3 schema with N×design_doc nested arrays previously blew past it).
# Inputs per call are tiny (~6KB): matrix entry + per-workflow evidence_pack
# + client context. Quotes & numbers come from evidence_pack so we don't
# re-ship the 60KB transcript per workflow.

DESIGN_DOC_PROMPT = """You are Vega, the AI Recruiter Agent. Produce ONE comprehensive AI Design Document for a single workflow. This document goes to the technical team for pricing and implementation planning — it must let an engineer who was NOT on the discovery calls understand what to build AND understand how this AI employee fits into the broader solution alongside the other workflows and apps.

CLIENT CONTEXT:
  - Industry: {client_industry}
  - Stakeholders: {stakeholders_summary}

WORKFLOW MATRIX ENTRY (the structured discovery output for this workflow):
{matrix_entry_json}

EVIDENCE PACK (verbatim quotes, numbers, and stakeholders pulled from the call transcript — quote these directly when writing rationale and current-state):
{evidence_pack}

FULL SOLUTION LANDSCAPE — the OTHER AI workflows and apps in this engagement that this AI employee must hand data off to or receive data from:
{solution_landscape}

YOUR JOB — produce a per-workflow AI Design Document that includes:

EXECUTIVE SUMMARY: 2-3 paragraphs explaining what this AI employee does, why it matters, and the expected business impact. Reference the concrete numbers from the evidence pack.

CURRENT STATE: How the workflow operates today — the process, pain points, systems involved, manual effort (with hours), and known error rates. Quote the client directly from the evidence pack ("Sarah said: '…'").

PROPOSED AI SOLUTION: What the AI employee will do, what the human's role becomes, estimated automation percentage, and the key capabilities required. Be specific — "extract line items via OCR and match against PO numbers in NetSuite" NOT "automate invoice processing."

BPMN PROCESS NARRATIVE: A plain-English walkthrough of the BPMN steps in this workflow, in order. Describe each step, who does it, what data flows in and out, and what decisions are made. This lets a reader understand the process without seeing the diagram.

INTEGRATION REQUIREMENTS: For EACH system this workflow touches (drawn from the matrix entry's integration_points and evidence_pack), specify: system name, integration type (api/file_transfer/manual_entry/database/webhook/unknown), what data is exchanged, complexity (low/medium/high), and notes. Be specific about APIs, file formats, and authentication where the evidence supports it.

CROSS-SOLUTION HANDOFFS (REQUIRED — this is what makes the design coherent across all the workflows + apps in the engagement): Walk through every entry in the FULL SOLUTION LANDSCAPE above and state explicitly:
  • Which other workflows feed THIS workflow (what triggers it / what data arrives) — name them by the same name used in the landscape.
  • Which other workflows or apps THIS workflow hands off to (what fires when, what payload) — name them by the same name used in the landscape.
  • Where this workflow shares data with a customer-facing app from the landscape (what fields, read or write, what consistency guarantees the engineer must enforce).
Use the entries' exact names from the landscape so the reader can cross-reference. If the landscape is empty, state "This is the only AI employee / app surfaced in discovery; no cross-system handoffs required" — do NOT invent additional workflows or apps.

DATA FLOW: What data comes in (name, source, format), how the AI processes it, what goes out (name, destination, format), and any data sensitivity concerns (PII, financial, health). When data flows in or out from another entity in the solution landscape, name that entity explicitly.

MMI JUSTIFICATION: For each of the 6 MMI dimensions, provide rationale citing the evidence pack. Example: "TSS=4: invoice matching follows clear rules (PO lookup, threshold checks). Sarah confirmed the rules are documented in their AP playbook."

IMPLEMENTATION CONSIDERATIONS: Concrete risks (not generic), dependencies on other systems/teams, assumptions, open questions, estimated complexity (low/medium/high).

HUMAN TOUCHPOINTS: Where humans must remain in the loop, why (regulatory, judgment, exception handling), and how often.

GROUNDING RULES:
- Use the matrix entry's actors, steps, decision_points, integration_points, transformation_logic verbatim where they fit.
- Quote the evidence_pack — that's why it's there. If something wasn't in the transcript, flag it as an open question instead of inventing it.
- Set workflow_id and workflow_name from the matrix entry. Do not invent IDs.
"""


async def _generate_single_design_doc(wf: dict, client_industry: str,
                                       stakeholders_summary: str, db, user,
                                       session_id: str,
                                       solution_landscape: str = "") -> dict | None:
    """One LLM call → one AI Design Document for a single workflow.

    Returns the parsed design doc dict on success, or a sentinel
    `{status: 'failed', failure_reason, workflow_id, workflow_name}` on
    failure. Never raises — the caller's asyncio.gather uses
    return_exceptions=True but we still want a structured failure record so
    the rep sees which workflows didn't get a design doc and why.

    ``solution_landscape`` is a human-readable summary of the OTHER apps and
    workflows in the engagement so the design doc can write its
    cross-system handoffs section against real names instead of hand-waving.
    """
    from services.ai.llm import call_llm
    from services.ai.conductor import _get_model

    workflow_id = wf.get("workflow_id") or f"wf_{uuid.uuid4().hex[:8]}"
    workflow_name = wf.get("workflow_name") or "(unnamed workflow)"

    base = {
        "workflow_id": workflow_id,
        "workflow_name": workflow_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Trim the matrix entry to the fields that actually inform the design doc;
    # drop the BPMN-edit fields (bpmn_id, bpmn_status, bpmn_content_hash) and
    # the evidence_pack (passed separately so the prompt frames it clearly).
    _drop = {"bpmn_id", "bpmn_status", "bpmn_content_hash", "evidence_pack"}
    matrix_entry = {k: v for k, v in wf.items() if k not in _drop and v is not None}
    matrix_entry_json = json.dumps(matrix_entry, default=str, indent=2)

    evidence_pack = wf.get("evidence_pack") or "(no evidence pack returned by Call 3 — rely on the matrix entry alone)"

    try:
        prompt = DESIGN_DOC_PROMPT.format(
            client_industry=client_industry or "(unspecified)",
            stakeholders_summary=stakeholders_summary or "(unspecified)",
            matrix_entry_json=matrix_entry_json,
            evidence_pack=evidence_pack,
            solution_landscape=solution_landscape or "(no other workflows or apps in this engagement — this AI employee stands alone)",
        )

        provider, model = await _get_model(db, "emp_ai_vega")
        t0 = time.monotonic()
        resp = await call_llm(
            system_prompt="You are Vega, the AI Recruiter Agent. Return one structured AI Design Document for the supplied workflow.",
            user_message=prompt,
            provider=provider,
            model=model,
            response_schema=AI_DESIGN_DOCUMENT_SCHEMA,
            timeout=VEGA_LLM_TIMEOUT,
            temperature=VEGA_LLM_TEMPERATURE,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if not resp or not resp.text:
            logger.warning("Design doc empty response for workflow %s (%dms)", workflow_id, elapsed_ms)
            return {**base, "status": "failed", "failure_reason": "empty LLM response"}

        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError as e:
            logger.warning("Design doc invalid JSON for workflow %s: %s", workflow_id, e)
            return {**base, "status": "failed", "failure_reason": f"invalid JSON: {e}"}

        # Force workflow_id/workflow_name from the matrix entry so the LLM
        # can't drift them and break downstream lookups.
        payload["workflow_id"] = workflow_id
        payload["workflow_name"] = workflow_name
        payload["generated_at"] = base["generated_at"]
        payload["status"] = "success"
        logger.info("Design doc generated for workflow %s in %dms", workflow_id, elapsed_ms)
        return payload
    except Exception as e:
        logger.exception("Design doc generation failed for workflow %s", workflow_id)
        return {**base, "status": "failed", "failure_reason": str(e)[:200]}


# ──────────────────────────────────────────────
#  Helpers — flatten MMI for frontend display
# ──────────────────────────────────────────────

_MMI_DIMENSIONS = ("tss", "ie", "time", "cost", "quality", "satisfaction")


def _flatten_mmi_for_frontend(mmi_hypothesis: dict) -> dict:
    """Convert nested {value, confidence, reasoning} objects into flat integer
    values that the AIEmployeeMatrixTable component expects, while preserving
    the full detail under a separate ``_detail`` key.

    The frontend reads ``mmi_hypothesis.tss`` as a plain number and
    ``mmi_hypothesis.score`` as the computed MMI score.
    """
    flat: dict = {}
    detail: dict = {}

    for dim in _MMI_DIMENSIONS:
        dim_data = mmi_hypothesis.get(dim, {})
        if isinstance(dim_data, dict):
            flat[dim] = dim_data.get("value")
            detail[dim] = dim_data
        else:
            # Already a plain value (shouldn't happen, but be defensive)
            flat[dim] = dim_data

    flat["needs_client_input"] = mmi_hypothesis.get("needs_client_input", [])
    flat["_detail"] = detail
    return flat


async def _load_existing_matrix(db, user, session_id=None) -> list:
    """Load the ai_employee_matrix from a specific Vega session.

    Only loads when an explicit session_id is provided to prevent
    a prior session's matrix from leaking into unrelated analyses.
    """
    if not session_id:
        return []
    session = await db.vega_discovery_sessions.find_one(
        {"session_id": session_id, "user_id": user.user_id,
         "status": {"$in": ["in_progress", "complete"]}},
        {"_id": 0, "ai_employee_matrix": 1},
        sort=[("updated_at", -1)],
    )
    if session:
        return session.get("ai_employee_matrix", [])
    return []


# ──────────────────────────────────────────────
#  Call 2 — Processing function
# ──────────────────────────────────────────────

async def run_call2_analysis(
    db, user, upload_ids: list[str], call_number: int = 2, session_id: str = None
) -> dict | None:
    """Analyze a Call 2 transcript and build the AI Employee Matrix.

    Steps:
    1. Load transcripts from chat_uploads.
    2. Load prior Call 1 analysis from vega_discovery_sessions.
    3. Call LLM with CALL2_ANALYSIS_PROMPT + AI_EMPLOYEE_MATRIX_SCHEMA.
    4. Parse JSON response.
    5. Generate BPMN diagram for each workflow.
    6. Attach bpmn_id and bpmn_status to each workflow entry.
    7. Save the matrix to the Vega session.
    8. Return {"matrix_data": parsed, "text": summary}.
    """
    from services.ai.llm import call_llm
    from services.ai.conductor import _get_model
    from services.ai.agents.tools.conversational_tools import _handle_generate_bpmn_diagram

    _log_ctx = f"[Call 2 | session={session_id} | user={user.user_id}]"

    transcript_text = await _load_transcripts(db, upload_ids, user=user)
    if not transcript_text.strip():
        logger.warning("%s No transcript text found for upload_ids=%s", _log_ctx, upload_ids)
        return None

    logger.info("%s Transcript loaded (%d chars) from %d upload(s)", _log_ctx, len(transcript_text), len(upload_ids))

    prior_context = await _load_prior_context(db, user, session_id)

    prompt = CALL2_ANALYSIS_PROMPT.format(
        prior_context=prior_context,
        transcript_text=transcript_text,
    )

    provider, model = await _get_model(db, "emp_ai_vega")

    t0 = time.monotonic()
    resp = await call_llm(
        system_prompt="You are Vega, the AI Recruiter Agent. Return structured JSON for the AI Employee Matrix. You MUST include ALL workflows from the prior Call 1 analysis.",
        user_message=prompt,
        provider=provider,
        model=model,
        response_schema=AI_EMPLOYEE_MATRIX_SCHEMA,
        timeout=VEGA_LLM_TIMEOUT,
        temperature=VEGA_LLM_TEMPERATURE,
    )
    llm_elapsed = time.monotonic() - t0

    if not resp or not resp.text:
        logger.warning("%s Empty LLM response after %.1fs", _log_ctx, llm_elapsed)
        return None

    logger.info("%s LLM responded in %.1fs (%d chars)", _log_ctx, llm_elapsed, len(resp.text))

    try:
        parsed = json.loads(resp.text)
    except json.JSONDecodeError:
        logger.error("%s Failed to parse JSON response: %s", _log_ctx, resp.text[:500])
        return {"error": "Analysis returned invalid format. Please try uploading the transcript again."}

    matrix = parsed.get("matrix", [])
    logger.info("%s Matrix contains %d workflow(s) — generating BPMN diagrams", _log_ctx, len(matrix))

    # Generate BPMN diagrams for each workflow
    bpmn_t0 = time.monotonic()
    for wf in matrix:
        try:
            bpmn_result = await _handle_generate_bpmn_diagram(
                {"workflow_data": wf}, db, user, session_id=session_id, employee_id="ai_vega"
            )
            if bpmn_result.get("bpmn_id"):
                wf["bpmn_id"] = bpmn_result["bpmn_id"]
                wf["bpmn_status"] = "generated"
            else:
                wf["bpmn_status"] = "failed"
                logger.warning(
                    "%s BPMN generation failed for workflow %s: %s",
                    _log_ctx, wf.get("workflow_id"), bpmn_result.get("error"),
                )
        except Exception:
            wf["bpmn_status"] = "failed"
            logger.exception("%s BPMN generation error for workflow %s", _log_ctx, wf.get("workflow_id"))
    bpmn_elapsed = time.monotonic() - bpmn_t0
    logger.info("%s BPMN generation complete in %.1fs", _log_ctx, bpmn_elapsed)

    # Flatten MMI for frontend display
    for wf in matrix:
        if "mmi_hypothesis" in wf:
            wf["mmi_hypothesis"] = _flatten_mmi_for_frontend(wf["mmi_hypothesis"])

    # Save matrix to the Vega session — require session_id to prevent
    # accidentally updating an unrelated session. Fail fast if missing.
    now = datetime.now(timezone.utc).isoformat()
    wf_count = len(matrix)
    gap_count = len(parsed.get("gaps", []))
    summary_text = (
        f"AI Employee Matrix built with {wf_count} workflow(s). "
        f"{gap_count} gap(s) identified for Call 3."
    )

    if not session_id:
        logger.error("Call 2 analysis has no session_id — cannot save, returning results without persisting")
        return {"matrix_data": parsed, "text": summary_text}

    # No status filter — a re-run after Call 3 marked the session complete
    # should still update (e.g. user re-runs Call 2 to refresh the matrix).
    # The upsert=False guard plus user_id scoping keeps writes tight.
    save_result = await db.vega_discovery_sessions.update_one(
        {"session_id": session_id, "user_id": user.user_id},
        {
            "$set": {
                "ai_employee_matrix": matrix,
                "gap_questions": parsed.get("gaps", []),
                f"calls.2.analysis": parsed,
                "updated_at": now,
            }
        },
        upsert=False,
    )
    if save_result.matched_count == 0:
        logger.warning("%s Call 2 results NOT persisted — no session matched id=%s user=%s",
                       _log_ctx, session_id, user.user_id)

    for uid in upload_ids:
        await db.chat_uploads.update_one(
            {"upload_id": uid, "user_id": user.user_id},
            {"$set": {"linked_to": {"call_number": call_number}}},
        )

    from services.vega_events import emit_vega_event
    await emit_vega_event(db, user.user_id, session_id, "matrix_built", {"workflow_count": len(matrix), "gap_count": len(parsed.get("gaps", []))})

    return {"matrix_data": parsed, "text": summary_text}


# ──────────────────────────────────────────────
#  Helper — load uploaded BPMN edits
# ──────────────────────────────────────────────

_BPMN_EXTENSIONS = (".bpmn", ".bpmn.xml", ".xml")


def _detect_reusable_ai_subroutines(matrix: list) -> dict:
    """Find AI steps that appear identically across 2+ workflows.

    A step counts as "reusable" only if (name, task_type=='business_rule',
    actor type=='ai') matches across multiple workflows. Narrow heuristic on
    purpose — coincidentally-named steps shouldn't get promoted.

    Returns: { (step_name, task_type) → list of {workflow_id, workflow_name,
    step_id} where it appears }. Caller decides what to do with the result.
    """
    if not matrix or len(matrix) < 2:
        return {}

    occurrences: dict = {}
    for wf in matrix:
        ai_actor_ids = {a.get("id") for a in (wf.get("actors") or [])
                         if (a.get("type") or "").lower() == "ai"}
        if not ai_actor_ids:
            continue
        for step in (wf.get("steps") or []):
            if (step.get("task_type") or "").lower() != "business_rule":
                continue
            if step.get("actor_id") not in ai_actor_ids:
                continue
            name = (step.get("name") or "").strip()
            if not name:
                continue
            key = name.lower()
            occurrences.setdefault(key, []).append({
                "workflow_id": wf.get("workflow_id"),
                "workflow_name": wf.get("workflow_name"),
                "step_id": step.get("id"),
                "step_name": name,
            })

    # Only keep names that appear in 2+ DIFFERENT workflows
    reusable: dict = {}
    for key, occs in occurrences.items():
        unique_wfs = {o["workflow_id"] for o in occs if o["workflow_id"]}
        if len(unique_wfs) >= 2:
            reusable[key] = occs
    return reusable


def _promote_call_activities(matrix: list) -> int:
    """Mutate matrix in-place to add call_activities entries for reusable
    AI subroutines. Returns the number of subroutines promoted.

    Strategy: when an AI business_rule step appears in 2+ workflows under the
    same name, leave the FIRST occurrence as-is (canonical implementation) and
    annotate every other occurrence's workflow with a call_activities entry
    that references the canonical one.
    """
    reusable = _detect_reusable_ai_subroutines(matrix)
    if not reusable:
        return 0

    promoted = 0
    for key, occs in reusable.items():
        # Pick a stable canonical called_element_ref derived from the first
        # workflow's id + step id so it's deterministic across re-runs.
        canonical = occs[0]
        called_ref = f"Process_subroutine_{_slug_for_subroutine(canonical['step_name'])}"
        for occ in occs[1:]:  # skip the canonical
            wf = next((w for w in matrix if w.get("workflow_id") == occ["workflow_id"]), None)
            if not wf:
                continue
            wf.setdefault("call_activities", [])
            ca_id = f"ca_{occ['step_id']}"
            # Don't double-add if a previous re-run already promoted it
            if any(c.get("id") == ca_id for c in wf["call_activities"]):
                continue
            wf["call_activities"].append({
                "id": ca_id,
                "name": occ["step_name"],
                "called_element_ref": called_ref,
            })
            promoted += 1
            logger.info(
                "Promoted reusable AI step %r in workflow %s to call activity %s",
                occ["step_name"], occ["workflow_id"], called_ref,
            )
    return promoted


def _slug_for_subroutine(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").strip()).strip("_").lower()
    return s or "subroutine"


def _bpmn_content_hash(workflow: dict) -> str:
    """Hash the workflow fields that BPMN renderer actually consumes.

    Returns a stable 16-char SHA-256 prefix over (steps + decision_points +
    actors + exception_paths). Description/source_apps/MMI changes
    intentionally don't trigger regen — they don't affect the rendered
    diagram.
    """
    import hashlib

    payload = {
        "actors": workflow.get("actors") or [],
        "steps": workflow.get("steps") or [],
        "decision_points": workflow.get("decision_points") or [],
        "exception_paths": workflow.get("exception_paths") or [],
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


async def _any_bpmn_in_uploads(db, upload_ids: list[str], user) -> bool:
    """Cheap filename-only peek: return True if any upload has a BPMN-shaped name."""
    if not upload_ids:
        return False
    cursor = db.chat_uploads.find(
        {"upload_id": {"$in": upload_ids}, "user_id": user.user_id},
        {"_id": 0, "filename": 1},
    )
    async for doc in cursor:
        fname = (doc.get("filename") or "").lower()
        if fname.endswith(_BPMN_EXTENSIONS):
            return True
    return False


async def _load_uploaded_bpmn_edits(db, upload_ids: list[str], user=None) -> dict:
    """Load any uploaded BPMN XML files, keyed by upload_id for matching."""
    edits = {}
    if not upload_ids:
        return edits
    for uid in upload_ids:
        query = {"upload_id": uid}
        if user:
            query["user_id"] = user.user_id
        doc = await db.chat_uploads.find_one(query, {"_id": 0})
        if not doc:
            continue
        if doc.get("document_type") == "bpmn_edit" and doc.get("extracted_text"):
            # Use filename to try to match to a workflow
            edits[uid] = {
                "filename": doc.get("filename", ""),
                "xml": doc["extracted_text"],
            }
    return edits


# ──────────────────────────────────────────────
#  Call 3 — Processing function
# ──────────────────────────────────────────────

async def run_call3_analysis(
    db, user, upload_ids: list[str], call_number: int = 3, session_id: str = None,
    is_sole_input: bool = False,
) -> dict | None:
    """Analyze a Call 3 transcript, finalize MMI scores, and mark session ready.

    When ``is_sole_input=True`` the transcript is treated as the entire discovery
    record (no prior Call 1/2 to lean on) — used by the single-call pipeline.

    Steps:
    1. Load transcripts.
    2. Load prior Call 1 + Call 2 analyses AND the existing ai_employee_matrix.
    3. Call LLM with CALL3_FINAL_PROMPT + CALL3_FINAL_SCHEMA.
    4. Parse JSON response.
    5. Compute MMI score for each workflow via _handle_compute_mmi_score.
    6. Attach computed score to each workflow's mmi_hypothesis.
    7. Regenerate BPMN for workflows that changed.
    8. Save updated matrix + mmi_scores to session, set status to complete.
    9. Return the data.
    """
    from services.ai.llm import call_llm
    from services.ai.conductor import _get_model
    from services.ai.agents.tools.conversational_tools import (
        _handle_generate_bpmn_diagram,
        _handle_compute_mmi_score,
    )

    _log_ctx = f"[Call 3 | session={session_id} | user={user.user_id}]"

    transcript_text = await _load_transcripts(db, upload_ids, user=user)
    if not transcript_text.strip():
        logger.warning("%s No transcript text found for upload_ids=%s", _log_ctx, upload_ids)
        return None

    logger.info("%s Transcript loaded (%d chars) from %d upload(s)", _log_ctx, len(transcript_text), len(upload_ids))

    if is_sole_input:
        prior_context = ""
        existing_matrix: list = []
    else:
        prior_context = await _load_prior_context(db, user, session_id)
        existing_matrix = await _load_existing_matrix(db, user, session_id)

    if is_sole_input or not prior_context:
        sole_input_note = (
            "\n\nSOLE-INPUT MODE: Treat this transcript as the **only** discovery input "
            "rather than a wrap-up of prior calls. Identify ALL workflows discussed and "
            "produce the full deliverable (matrix + MMI + BPMN + AI Design Documents) in "
            "a single pass. Do not reference prior Call 1/Call 2 analyses — there are none."
        )
    else:
        sole_input_note = ""

    prompt = CALL3_FINAL_PROMPT.format(
        prior_context=prior_context + sole_input_note,
        existing_matrix=json.dumps(existing_matrix, default=str) if existing_matrix else "No prior matrix found.",
        transcript_text=transcript_text,
    )

    provider, model = await _get_model(db, "emp_ai_vega")

    t0 = time.monotonic()
    resp = await call_llm(
        system_prompt="You are Vega, the AI Recruiter Agent. Return structured JSON to finalize the AI Employee Matrix.",
        user_message=prompt,
        provider=provider,
        model=model,
        response_schema=CALL3_FINAL_SCHEMA,
        timeout=VEGA_LLM_TIMEOUT,
        temperature=VEGA_LLM_TEMPERATURE,
    )
    llm_elapsed = time.monotonic() - t0

    if not resp or not resp.text:
        logger.warning("%s Empty LLM response after %.1fs", _log_ctx, llm_elapsed)
        return None

    logger.info("%s LLM responded in %.1fs (%d chars)", _log_ctx, llm_elapsed, len(resp.text))

    try:
        parsed = json.loads(resp.text)
    except json.JSONDecodeError:
        logger.error("%s Failed to parse JSON response: %s", _log_ctx, resp.text[:500])
        return {"error": "Analysis returned invalid format. Please try uploading the transcript again."}

    matrix = parsed.get("matrix", [])

    # Detect reusable AI subroutines (same business_rule AI step in 2+ workflows)
    # and promote them to bpmn:callActivity in the matrix entries that aren't
    # the canonical owner. Done BEFORE BPMN regen so the renderer picks them up.
    promoted = _promote_call_activities(matrix)
    if promoted:
        logger.info("%s Promoted %d reusable AI subroutine(s) to call activities", _log_ctx, promoted)

    # Build a lookup of prior workflow IDs that had BPMN diagrams
    prior_bpmn_lookup = {}
    for prior_wf in existing_matrix:
        wf_id = prior_wf.get("workflow_id")
        if wf_id and prior_wf.get("bpmn_id"):
            prior_bpmn_lookup[wf_id] = prior_wf

    # Load any user-uploaded BPMN edits — but skip the per-upload byte scan
    # entirely when none of the uploads even have a BPMN-shaped filename.
    # Common case (transcript-only) saves 50-200ms per Call 3.
    if await _any_bpmn_in_uploads(db, upload_ids, user):
        uploaded_bpmn = await _load_uploaded_bpmn_edits(db, upload_ids, user=user)
    else:
        uploaded_bpmn = {}

    # ── Concurrent post-processing ──
    # Three batches run in parallel via a single asyncio.gather:
    #   - per-workflow MMI compute + BPMN regen (perf #1)
    #   - per-app EADD generation (perf #4 — overlaps with the above)
    # return_exceptions=True isolates per-task failures so a single bad
    # workflow or app doesn't abort the batch.
    import asyncio
    from services.vega_events import emit_vega_event  # used by _process_workflow

    apps = parsed.get("emergent_apps", []) or []
    client_industry = parsed.get("client_industry") or ""
    stakeholders_summary = parsed.get("stakeholders_summary") or ""

    # Drop linked_workflow_ids that don't match a real workflow_id; log a
    # warning so the LLM's hallucinated cross-link doesn't propagate.
    valid_wf_ids = {wf.get("workflow_id") for wf in matrix if wf.get("workflow_id")}
    for app in apps:
        before = list(app.get("linked_workflow_ids") or [])
        kept = [wid for wid in before if wid in valid_wf_ids]
        if kept != before:
            dropped = [w for w in before if w not in valid_wf_ids]
            logger.warning("App %s referenced unknown workflow_ids: %s", app.get("app_id"), dropped)
        app["linked_workflow_ids"] = kept

    sem_wf = asyncio.Semaphore(5)
    sem_app = asyncio.Semaphore(5)
    sem_design = asyncio.Semaphore(5)

    async def _process_workflow(wf):
        async with sem_wf:
            mmi_raw = wf.get("mmi_hypothesis", {})

            # Extract dimension values for the score computation
            score_args = {}
            for dim in _MMI_DIMENSIONS:
                dim_data = mmi_raw.get(dim, {})
                if isinstance(dim_data, dict):
                    score_args[dim] = dim_data.get("value", 1)
                else:
                    score_args[dim] = dim_data if dim_data else 1

            score_result = {}
            try:
                score_result = await _handle_compute_mmi_score(
                    score_args, db, user, session_id=session_id, employee_id="ai_vega"
                )
                computed_score = score_result.get("score")
            except Exception:
                logger.exception("MMI score computation failed for workflow %s", wf.get("workflow_id"))
                computed_score = None

            wf["mmi_hypothesis"] = _flatten_mmi_for_frontend(mmi_raw)
            wf["mmi_hypothesis"]["score"] = computed_score

            mmi_entry = {
                "workflow_id": wf.get("workflow_id"),
                "workflow_name": wf.get("workflow_name"),
                "score": computed_score,
                "breakdown": score_result.get("breakdown") if computed_score else None,
            }

            # User-uploaded BPMN edit?
            matched_upload = None
            for uid, bpmn_data in uploaded_bpmn.items():
                fname = bpmn_data["filename"].lower()
                wf_name = wf.get("workflow_name", "").lower().replace(" ", "_")
                wf_id = wf.get("workflow_id", "").lower()
                if (wf_name and wf_name in fname) or (wf_id and wf_id in fname):
                    matched_upload = bpmn_data
                    break

            if matched_upload:
                prior_wf = prior_bpmn_lookup.get(wf.get("workflow_id"))
                prior_bpmn_id = prior_wf.get("bpmn_id") if prior_wf else None
                if prior_bpmn_id:
                    await db.bpmn_diagrams.update_one(
                        {"bpmn_id": prior_bpmn_id},
                        {"$set": {"edited_xml": matched_upload["xml"],
                                  "updated_at": datetime.now(timezone.utc)}},
                    )
                    wf["bpmn_id"] = prior_bpmn_id
                    wf["bpmn_status"] = "generated"
                else:
                    bpmn_id = f"bpmn_{uuid.uuid4().hex[:12]}"
                    await db.bpmn_diagrams.insert_one({
                        "bpmn_id": bpmn_id,
                        "workflow_id": wf.get("workflow_id"),
                        "workflow_name": wf.get("workflow_name"),
                        "bpmn_xml": matched_upload["xml"],
                        "edited_xml": matched_upload["xml"],
                        "created_by": user.user_id,
                        "created_by_agent": "ai_vega",
                        "created_at": datetime.now(timezone.utc),
                    })
                    wf["bpmn_id"] = bpmn_id
                    wf["bpmn_status"] = "generated"
            else:
                prior_wf = prior_bpmn_lookup.get(wf.get("workflow_id"))
                new_hash = _bpmn_content_hash(wf)
                prior_hash = (prior_wf or {}).get("bpmn_content_hash")
                needs_regen = prior_wf is None or new_hash != prior_hash

                if needs_regen:
                    try:
                        bpmn_result = await _handle_generate_bpmn_diagram(
                            {"workflow_data": wf}, db, user, session_id=session_id, employee_id="ai_vega"
                        )
                        if bpmn_result.get("bpmn_id"):
                            wf["bpmn_id"] = bpmn_result["bpmn_id"]
                            wf["bpmn_status"] = "generated"
                            wf["bpmn_content_hash"] = new_hash
                        else:
                            wf["bpmn_status"] = "failed"
                            logger.warning(
                                "BPMN regen failed for workflow %s: %s",
                                wf.get("workflow_id"), bpmn_result.get("error"),
                            )
                    except Exception:
                        wf["bpmn_status"] = "failed"
                        logger.exception("BPMN regen error for workflow %s", wf.get("workflow_id"))
                else:
                    wf["bpmn_id"] = prior_wf.get("bpmn_id")
                    wf["bpmn_status"] = prior_wf.get("bpmn_status", "generated")
                    wf["bpmn_content_hash"] = new_hash
                    await emit_vega_event(
                        db, user.user_id, session_id, "bpmn_regen_skipped",
                        {"workflow_id": wf.get("workflow_id"), "reason": "content_hash_match"},
                    )

            return mmi_entry

    async def _process_app(app):
        async with sem_app:
            return await _generate_single_eadd(
                app, matrix, client_industry, stakeholders_summary,
                db, user, session_id,
            )

    async def _process_design_doc(wf):
        async with sem_design:
            return await _generate_single_design_doc(
                wf, client_industry, stakeholders_summary,
                db, user, session_id,
            )

    logger.info(
        "%s Concurrent post-processing: %d workflow(s), %d app(s), %d design doc(s)",
        _log_ctx, len(matrix), len(apps), len(matrix),
    )
    call3_post_t0 = time.monotonic()

    workflow_tasks = [_process_workflow(wf) for wf in matrix]
    app_tasks = [_process_app(app) for app in apps]
    design_doc_tasks = [_process_design_doc(wf) for wf in matrix]

    results = await asyncio.gather(
        *workflow_tasks, *app_tasks, *design_doc_tasks, return_exceptions=True,
    )

    n_wf, n_app, n_dd = len(workflow_tasks), len(app_tasks), len(design_doc_tasks)
    wf_end = n_wf
    app_end = wf_end + n_app

    mmi_scores: list = []
    eadd_docs: list = []
    design_docs: list = []
    for idx, res in enumerate(results):
        if idx < wf_end:
            if isinstance(res, Exception):
                wf = matrix[idx]
                logger.exception("Workflow processing failed for %s: %s",
                                 wf.get("workflow_id"), res)
                continue
            if res:
                mmi_scores.append(res)
        elif idx < app_end:
            if isinstance(res, Exception):
                logger.exception("App EADD generation raised: %s", res)
                continue
            if res:
                eadd_docs.append(res)
        else:
            if isinstance(res, Exception):
                logger.exception("Design doc generation raised: %s", res)
                continue
            if res:
                design_docs.append(res)

    call3_post_elapsed = time.monotonic() - call3_post_t0
    design_doc_success = sum(1 for d in design_docs if d.get("status") == "success")
    logger.info(
        "%s Post-processing complete in %.1fs (mmi=%d, eadd=%d, design_docs=%d/%d ok)",
        _log_ctx, call3_post_elapsed, len(mmi_scores), len(eadd_docs),
        design_doc_success, n_dd,
    )

    parsed["ai_design_documents"] = design_docs
    parsed["emergent_app_design_documents"] = eadd_docs

    # Save to session and mark complete — require session_id to prevent
    # accidentally updating an unrelated session. Fail fast if missing.
    now = datetime.now(timezone.utc).isoformat()
    wf_count = len(matrix)
    scored_count = sum(1 for s in mmi_scores if s["score"] is not None)
    resolved_count = len(parsed.get("contradictions_resolved", []))
    design_doc_count = len(design_docs)
    eadd_count = len(eadd_docs)
    eadd_success = sum(1 for d in eadd_docs if d.get("status") == "success")
    summary_text = (
        f"AI Employee Matrix finalized with {wf_count} workflow(s), "
        f"{scored_count} MMI score(s) computed, "
        f"{resolved_count} contradiction(s) resolved, "
        f"{design_doc_success}/{design_doc_count} AI Design Document(s) generated"
        + (f", {eadd_success}/{eadd_count} App Design(s) generated" if eadd_count else "")
        + ". Session complete and ready for technical review."
    )

    if not session_id:
        logger.error("Call 3 analysis has no session_id — cannot save, returning results without persisting")
        return {"matrix_data": parsed, "text": summary_text}

    # No status filter — a re-run on a complete session should still persist.
    save_result = await db.vega_discovery_sessions.update_one(
        {"session_id": session_id, "user_id": user.user_id},
        {
            "$set": {
                "ai_employee_matrix": matrix,
                "mmi_scores": mmi_scores,
                "ai_design_documents": design_docs,
                "emergent_app_design_documents": eadd_docs,
                "gap_questions": parsed.get("gaps", []),
                f"calls.3.analysis": parsed,
                "status": "complete",
                "updated_at": now,
            }
        },
        upsert=False,
    )
    if save_result.matched_count == 0:
        logger.warning("%s Call 3 results NOT persisted — no session matched id=%s user=%s",
                       _log_ctx, session_id, user.user_id)

    for uid in upload_ids:
        await db.chat_uploads.update_one(
            {"upload_id": uid, "user_id": user.user_id},
            {"$set": {"linked_to": {"call_number": call_number}}},
        )

    from services.vega_events import emit_vega_event
    await emit_vega_event(db, user.user_id, session_id, "session_finalized", {"workflow_count": len(matrix), "design_doc_count": len(design_docs), "scored_count": scored_count})

    return {"matrix_data": parsed, "text": summary_text}


# ──────────────────────────────────────────────
#  Single-call pipeline — collapses the 3-call flow into one pass
# ──────────────────────────────────────────────


async def run_single_call_pipeline(
    db, user, upload_ids: list[str], session_id: str = None,
    chat_session_id: str = None, client_company: str = None,
) -> dict | None:
    """Run the full discovery analysis from a single transcript.

    Creates an vega_discovery_session if none exists, then dispatches Call 3
    in sole-input mode (no prior context, no existing matrix). The Call 3
    pipeline already does the full deliverable — matrix + MMI + BPMN + AI
    Design Documents — so all single-call needs is the right entry point
    and a freshly upserted session to write into.

    Returns the same shape as run_call3_analysis: {matrix_data, text}.
    """
    from services.vega_events import emit_vega_event

    if not upload_ids:
        return {"error": "single-call analysis requires at least one upload_id"}

    if not session_id:
        session_id = f"vega_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        await db.vega_discovery_sessions.insert_one({
            "session_id": session_id,
            "user_id": user.user_id,
            "chat_session_id": chat_session_id,
            "client_company": client_company,
            "current_call_number": 3,
            "calls": {},
            "ai_employee_matrix": [],
            "gap_questions": [],
            "mmi_scores": [],
            "discovery_mode": "single_call",
            "status": "in_progress",
            "created_at": now,
            "updated_at": now,
        })
        logger.info("[single_call | %s] Created Vega session %s", user.user_id, session_id)
    else:
        # Tag the existing session as single-call so downstream telemetry agrees
        await db.vega_discovery_sessions.update_one(
            {"session_id": session_id, "user_id": user.user_id},
            {"$set": {"discovery_mode": "single_call"}},
        )

    await emit_vega_event(
        db, user.user_id, session_id, "discovery_mode_selected",
        {"mode": "single_call", "upload_count": len(upload_ids)},
    )

    return await run_call3_analysis(
        db, user, upload_ids,
        call_number=3,
        session_id=session_id,
        is_sole_input=True,
    )
