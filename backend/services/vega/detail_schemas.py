"""Vega v3 — Stage 2 detail call schemas and prompt templates.

This module is owned by Wave 2 / Thread 2b. It defines the Gemini
response_schema dicts and ``.format()`` prompt templates for the two
per-entity detail calls that follow the Stage 1 skeleton.

Stage mapping:
    Stage 2a — per-workflow detail  → WORKFLOW_DETAIL_SCHEMA / WORKFLOW_DETAIL_PROMPT
    Stage 2b — per-app detail       → APP_DETAIL_SCHEMA / APP_DETAIL_PROMPT

Design notes:
  * Schemas are hand-built (not auto-derived from Pydantic) matching the
    v2 pattern from ``services/ai/vega_analysis.py``.  Hand-building lets us
    trim fields that belong to a different stage and keep Gemini's state
    budget well inside the limit.
  * Enum stripping: We deliberately *omit* ``enum`` constraints on the
    ``VegaFlag`` string fields and on ``bucket`` (IntegrationEntry) so that
    Gemini's constraint-state budget is not blown.  The description line for
    each of those fields lists the allowed values explicitly — the same
    pattern ``_strip_enums_for_gemini`` would produce, applied up front.
  * Flag tokens are pre-baked at module load time from the canonical
    ``VegaFlag`` enum so the prompts automatically track future additions to
    ``flags.py``.  Callers only need to ``.format()`` the entity-specific
    slots at call time.
  * Neither prompt re-emits skeleton fields, BPMN, or — in the workflow
    prompt — integrations (those live on the app side only).
"""

from __future__ import annotations

from services.vega.flags import VegaFlag

# ──────────────────────────────────────────────────────────────────────
# Shared sub-schemas (reused across both detail schemas)
# ──────────────────────────────────────────────────────────────────────

# The allowed VegaFlag values are described inline rather than via "enum"
# to avoid blowing Gemini's constraint-state budget on deeply-nested arrays.
_VEGA_FLAG_DESC = (
    "One of: MISSING, NOT_DISCUSSED, NOT_STATED, INFERRED_CONFIRM, "
    "NEEDS_TECHNICAL_FOLLOWUP, SOURCE_UNCLEAR. "
    "Omit the field when no flag applies."
)

_SOURCE_PROVENANCE_SCHEMA: dict = {
    "type": "object",
    "description": (
        "Where in the transcript this content came from. "
        "Set source_flag to SOURCE_UNCLEAR when you cannot pin a quote."
    ),
    "properties": {
        "transcript_filename": {
            "type": "string",
            "description": "Filename of the transcript this content was sourced from.",
        },
        "timestamp_seconds": {
            "type": "number",
            "description": "Approximate offset into the transcript audio in seconds. Omit if unknown.",
        },
        "speaker": {
            "type": "string",
            "description": "Speaker name attributed to the quote, if known.",
        },
        "quote": {
            "type": "string",
            "description": (
                "Verbatim quote (or closest paraphrase) from the transcript. "
                "Use the customer's own words."
            ),
        },
        "source_flag": {
            "type": "string",
            "description": _VEGA_FLAG_DESC,
        },
    },
    "required": [],
}

_DECISION_BRANCH_SCHEMA: dict = {
    "type": "object",
    "description": "One outgoing branch from a BPMN gateway.",
    "properties": {
        "condition": {
            "type": "string",
            "description": (
                "The condition this branch represents, e.g. 'amount > $10k' "
                "or 'customer is enterprise tier'."
            ),
        },
        "leads_to_step_id": {
            "type": "string",
            "description": (
                "Step ID this branch flows into. Omit when the branch leads "
                "to an end event."
            ),
        },
    },
    "required": ["condition"],
}

_DECISION_POINT_SCHEMA: dict = {
    "type": "object",
    "description": "A BPMN gateway within the workflow's process steps.",
    "properties": {
        "id": {
            "type": "string",
            "description": "Stable decision-point ID, e.g. 'd1', 'd2'.",
        },
        "name": {
            "type": "string",
            "description": "Human-readable name of the decision, e.g. 'amount threshold check'.",
        },
        "description": {
            "type": "string",
            "description": "Plain-language description of what this gateway decides.",
        },
        "after_step_id": {
            "type": "string",
            "description": "The step ID this gateway evaluates after. Omit for entry-level gateways.",
        },
        "type": {
            "type": "string",
            "description": "BPMN gateway type. One of: exclusive, parallel, inclusive.",
        },
        "branches": {
            "type": "array",
            "items": _DECISION_BRANCH_SCHEMA,
            "description": "Outgoing branches. At least two for exclusive/inclusive gateways.",
        },
        "inferred": {
            "type": "boolean",
            "description": "True when Vega inferred the gateway from context rather than explicit statement.",
        },
    },
    "required": ["id", "name", "type"],
}

_PROCESS_STEP_SCHEMA: dict = {
    "type": "object",
    "description": "A BPMN-shaped step in the workflow.",
    "properties": {
        "id": {
            "type": "string",
            "description": "Stable step ID, e.g. 's1', 's2'.",
        },
        "name": {
            "type": "string",
            "description": "Short, action-oriented step name, e.g. 'extract line items'.",
        },
        "actor_id": {
            "type": "string",
            "description": "ID of the actor performing this step (human / ai / external_system).",
        },
        "task_type": {
            "type": "string",
            "description": "BPMN task type. One of: user, service, business_rule, manual.",
        },
        "description": {
            "type": "string",
            "description": (
                "Plain-language description of what happens in this step. "
                "Prefix with '[INFERRED]' when Vega inferred the step from missing context."
            ),
        },
        "inferred": {
            "type": "boolean",
            "description": "True when Vega inferred this step from context.",
        },
    },
    "required": ["id", "name"],
}

_PERSONA_ROW_SCHEMA: dict = {
    "type": "object",
    "description": "One user persona for an app.",
    "properties": {
        "role": {
            "type": "string",
            "description": "Persona role label, e.g. 'field technician', 'finance manager'.",
        },
        "count": {
            "type": "integer",
            "description": "Number of users in this persona. Omit if not stated in transcript.",
        },
        "device": {
            "type": "string",
            "description": (
                "Primary device this persona uses, e.g. 'laptop', 'mobile', 'tablet'. "
                "Extract from transcript only — do not infer."
            ),
        },
        "technical_literacy": {
            "type": "string",
            "description": (
                "Inferred from transcript context and conversation tone, "
                "e.g. 'low', 'moderate', 'high'. Always carry INFERRED_CONFIRM in flags."
            ),
        },
        "concurrent_usage": {
            "type": "string",
            "description": (
                "Concurrent-usage signal: 'all 50 log in at 8am for daily standup', "
                "'sporadic, ~5 at a time', 'sustained throughout business hours'. "
                "Drives load sizing for the build team. Omit if not stated."
            ),
        },
        "accessibility_needs": {
            "type": "string",
            "description": (
                "Accessibility requirements — WCAG level, screen reader support, "
                "keyboard-only operation, color-blind safe palette, etc. "
                "Default to NOT_STATED + NEEDS_TECHNICAL_FOLLOWUP rather than inferring."
            ),
        },
        "flags": {
            "type": "array",
            "items": {"type": "string", "description": _VEGA_FLAG_DESC},
            "description": "Per-leaf-field flags. Empty list if every leaf is cleanly extracted.",
        },
    },
    "required": ["role"],
}

_INTEGRATION_ENTRY_SCHEMA: dict = {
    "type": "object",
    "description": (
        "Three-bucket integration entry plus build-handoff depth fields. "
        "Bucket 1 = pre-research (well-known platform), "
        "2 = transcript-extracted (customer mentioned actively using it), "
        "3 = needs technical follow-up (auth/API tier/rate limits). "
        "The depth fields (disposition, auth_method, rate_limits, sandbox, "
        "interaction_pattern, read_write, access_status) let the build team start "
        "work without re-running discovery."
    ),
    "properties": {
        "system_name": {
            "type": "string",
            "description": "Name of the integrated system, e.g. 'QuickBooks Online', 'Salesforce'.",
        },
        "bucket": {
            "type": "integer",
            "description": "Bucket number: 1, 2, or 3 (integer, not string).",
        },
        "disposition": {
            "type": "string",
            "description": (
                "Whether we INTEGRATE with this system or REPLACE it. "
                "Values: integrate, replace, unknown. Default 'unknown' when not stated."
            ),
        },
        "auth_method": {
            "type": "string",
            "description": (
                "Authentication method if discoverable: 'OAuth2 client credentials', "
                "'API key in header', 'mTLS', 'SAML SSO'. Omit when not stated."
            ),
        },
        "rate_limits": {
            "type": "string",
            "description": (
                "API tier / rate limits if known: 'Salesforce Enterprise — 100k API calls/day', "
                "'free tier — 60 reqs/min'. Omit when not stated."
            ),
        },
        "sandbox_availability": {
            "type": "string",
            "description": (
                "Whether a sandbox or test environment exists, and how to obtain access. "
                "Omit when not discussed."
            ),
        },
        "interaction_pattern": {
            "type": "string",
            "description": (
                "How data moves: webhook, polling, batch, streaming, manual_export, unknown. "
                "Drives architecture decisions for the build team."
            ),
        },
        "read_write": {
            "type": "string",
            "description": (
                "read | write | read_write | unknown. Different from notes — structured "
                "for the build team's checklist."
            ),
        },
        "access_status": {
            "type": "string",
            "description": (
                "already_provisioned | needs_setup | unknown. Whether the customer already "
                "has our access provisioned (creds in hand, sandbox usable) or whether "
                "the build team has to wait. Critical for sequencing."
            ),
        },
        "notes": {
            "type": "string",
            "description": (
                "Free-text notes — usage volume, legacy vs active, anything else that "
                "doesn't fit the structured fields above."
            ),
        },
        "flags": {
            "type": "array",
            "items": {"type": "string", "description": _VEGA_FLAG_DESC},
            "description": (
                "Typically [NEEDS_TECHNICAL_FOLLOWUP] for Bucket 3 entries. "
                "Bucket 1 entries may carry [INFERRED_CONFIRM] when industry-derived."
            ),
        },
    },
    "required": ["system_name", "bucket"],
}

_SCALE_PROFILE_SCHEMA: dict = {
    "type": "object",
    "description": "Scale proxy signals for an app. Extract from transcript — never guess GB/TB figures.",
    "properties": {
        "transaction_volume": {
            "type": "string",
            "description": (
                "Plain-language volume signal: '~5k orders/day', 'a few thousand records "
                "per month'. Omit if not stated."
            ),
        },
        "retention_window": {
            "type": "string",
            "description": (
                "Data retention requirement if mentioned: '5-year retention', "
                "'audit trail forever'. Omit if not discussed."
            ),
        },
        "growth_trajectory": {
            "type": "string",
            "description": (
                "Growth commentary from the transcript: '2x YoY', 'flat', "
                "'rolling out to 12 more locations'. Omit if not discussed."
            ),
        },
        "speed_or_storage_pain": {
            "type": "string",
            "description": (
                "Reported pain with current speed or storage limits: "
                "'queries take 10 minutes', 'we hit Excel's row cap'. Omit if not mentioned."
            ),
        },
        "inference": {
            "type": "string",
            "description": (
                "Vega's order-of-magnitude inference, e.g. 'data lake likely required' "
                "or 'normal app database is sufficient'. Omit when no signals were extractable."
            ),
        },
        "flag": {
            "type": "string",
            "description": (
                "MISSING when no scale signals were present. "
                "INFERRED_CONFIRM when inference is non-None. " + _VEGA_FLAG_DESC
            ),
        },
    },
    "required": [],
}

_NFRS_SCHEMA: dict = {
    "type": "object",
    "description": "Non-functional requirements for one app. Seven dimensions — each is None + flag when not extractable.",
    "properties": {
        "device_support": {
            "type": "string",
            "description": (
                "Device support requirements stated in transcript, e.g. 'desktop only', 'iOS + web'. "
                "Omit if absent (pair with NOT_STATED in flags)."
            ),
        },
        "browser_support": {
            "type": "string",
            "description": (
                "Browser support requirements if explicitly stated. "
                "Omit if absent (pair with NOT_STATED in flags)."
            ),
        },
        "data_freshness": {
            "type": "string",
            "description": (
                "Data freshness expectation, e.g. 'real-time dashboard' vs 'weekly report'. "
                "When inferred, carry INFERRED_CONFIRM in flags."
            ),
        },
        "rbac": {
            "type": "string",
            "description": (
                "Role-based access control requirements. May be inferred from industry context "
                "(e.g. multi-entity almost certainly needs RBAC) — carry INFERRED_CONFIRM when inferred."
            ),
        },
        "sla": {
            "type": "string",
            "description": (
                "Uptime / SLA requirements. Almost always None at discovery time — "
                "carry NEEDS_TECHNICAL_FOLLOWUP unless explicitly stated."
            ),
        },
        "compliance": {
            "type": "string",
            "description": (
                "Compliance regimes (HIPAA, SOC2, SOX-adjacent, etc.). "
                "Carry INFERRED_CONFIRM when inferred, NEEDS_TECHNICAL_FOLLOWUP for depth."
            ),
        },
        "multi_region": {
            "type": "string",
            "description": (
                "Multi-region / data-residency requirements. "
                "Carry NEEDS_TECHNICAL_FOLLOWUP unless explicitly stated."
            ),
        },
        "audit_logging": {
            "type": "string",
            "description": (
                "Audit logging requirements — who logs what, retention, immutability. "
                "Production-tier cost driver. Carry NEEDS_TECHNICAL_FOLLOWUP unless stated."
            ),
        },
        "performance_budgets": {
            "type": "string",
            "description": (
                "Concrete latency / throughput targets: 'p95 page load < 2s', "
                "'list view < 1s for 10k rows'. Drives caching, pagination, and DB design."
            ),
        },
        "flags": {
            "type": "array",
            "items": {"type": "string", "description": _VEGA_FLAG_DESC},
            "description": (
                "Per-leaf-field flag bag. Include one flag entry per dimension that "
                "could not be extracted cleanly."
            ),
        },
    },
    "required": [],
}

_ACCEPTANCE_CRITERION_SCHEMA: dict = {
    "type": "object",
    "description": "II.1 — success definition for one core flow inside a workflow.",
    "properties": {
        "flow_name": {
            "type": "string",
            "description": (
                "Name of the core flow this criterion applies to, "
                "e.g. 'invoice intake', 'PO matching'."
            ),
        },
        "success_when": {
            "type": "string",
            "description": (
                "Plain-English success definition: 'This flow succeeds when [X].' "
                "Omit if Vega could not extract or infer one — set flag to MISSING."
            ),
        },
        "flag": {
            "type": "string",
            "description": (
                "MISSING when no success criterion was found in the transcript. "
                "INFERRED_CONFIRM when Vega inferred it from goals/pain rather than direct statement. "
                + _VEGA_FLAG_DESC
            ),
        },
    },
    "required": ["flow_name"],
}


# ──────────────────────────────────────────────────────────────────────
# Stage 2a — workflow detail response schema
# ──────────────────────────────────────────────────────────────────────

_MMI_DIM_DETAIL_SCHEMA: dict = {
    "type": "object",
    "description": (
        "One MMI dimension scored on a 1–5 scale with a confidence rating and "
        "transcript-grounded reasoning. Use 1 for very low fit/value and 5 for very high. "
        "The reasoning MUST cite specifics from the transcript (volume, speakers, pain "
        "points) — not generic copy."
    ),
    "properties": {
        "value": {
            "type": "integer",
            "description": "1–5 score for this dimension. 1=very low, 5=very high.",
        },
        "confidence": {
            "type": "string",
            "description": (
                "One of 'high', 'medium', 'low'. Use 'high' when the transcript directly "
                "states facts behind the score; 'medium' when the score is inferred from "
                "context; 'low' when the score is a best guess and a follow-up question "
                "is needed. Low-confidence dims auto-populate needs_client_input."
            ),
        },
        "reasoning": {
            "type": "string",
            "description": (
                "1–2 sentences citing the specific transcript evidence behind the score. "
                "Example: 'Customer mentioned 4-6 hours/week per manager preparing meeting notes manually — IE is high.'"
            ),
        },
    },
    "required": ["value", "confidence", "reasoning"],
}

_MMI_HYPOTHESIS_SCHEMA: dict = {
    "type": "object",
    "description": (
        "Six-dimension MMI scoring for this workflow. Every dim must be filled with "
        "a 1–5 value plus transcript-grounded reasoning. Do NOT leave dims at the "
        "skeleton seed default — Stage 2a is where MMI gets its real scoring."
    ),
    "properties": {
        "tss": {
            **_MMI_DIM_DETAIL_SCHEMA,
            "description": "Total System Suitability — how well an AI employee fits this workflow at all (1=poor fit, 5=ideal AI candidate).",
        },
        "ie": {
            **_MMI_DIM_DETAIL_SCHEMA,
            "description": "Internal Efficiency — labor hours/week saved (1=trivial savings, 5=double-digit hours/week).",
        },
        "time": {
            **_MMI_DIM_DETAIL_SCHEMA,
            "description": "Time-to-completion improvement (1=already fast, 5=major SLA win).",
        },
        "cost": {
            **_MMI_DIM_DETAIL_SCHEMA,
            "description": "Cost reduction beyond labor — error rate, rework, contractors (1=no measurable cost win, 5=clear hard-dollar savings).",
        },
        "quality": {
            **_MMI_DIM_DETAIL_SCHEMA,
            "description": "Output quality / consistency improvement (1=quality already high, 5=variance kills the workflow today).",
        },
        "satisfaction": {
            **_MMI_DIM_DETAIL_SCHEMA,
            "description": "Stakeholder + employee satisfaction lift (1=neutral, 5=this is the most-hated workflow in their day).",
        },
        "needs_client_input": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Open questions that need a customer answer before the score can be locked. "
                "One item per question. Empty list when the transcript fully grounds the scoring."
            ),
        },
    },
    "required": ["tss", "ie", "time", "cost", "quality", "satisfaction"],
}


WORKFLOW_DETAIL_SCHEMA: dict = {
    "type": "object",
    "description": (
        "Vega v3 Stage 2a per-workflow detail. Fill acceptance criteria, "
        "decision logic, process steps, persona pointers, AND a real MMI scoring "
        "for ONE workflow. Do NOT re-emit skeleton fields (workflow_id, workflow_name, "
        "complexity, source, linked_app_ids). Do NOT add integrations, scale_profile, "
        "or NFRs — those live on apps only."
    ),
    "properties": {
        "acceptance_criteria": {
            "type": "array",
            "items": _ACCEPTANCE_CRITERION_SCHEMA,
            "description": (
                "II.1 — one entry per core flow inside this workflow. "
                "A workflow with N named flows produces N entries. "
                "Flag individual criteria MISSING when no success definition was stated."
            ),
        },
        "decision_logic": {
            "type": "array",
            "items": _DECISION_POINT_SCHEMA,
            "description": (
                "BPMN gateways for this workflow. Reference these from process_steps "
                "via after_step_id / leads_to_step_id. Empty list if no explicit "
                "branching was described in the transcript."
            ),
        },
        "process_steps": {
            "type": "array",
            "items": _PROCESS_STEP_SCHEMA,
            "description": (
                "BPMN-shaped step list for this workflow. Stage 3 reads this to "
                "produce bpmn_xml. Aim for 3–12 steps per workflow; don't collapse "
                "the whole workflow into one step."
            ),
        },
        "persona_depth_app_id": {
            "type": "string",
            "description": (
                "App ID whose AppDetail.personas should be displayed under this workflow. "
                "Set to the primary linked app ID when the workflow's users are covered by "
                "that app's persona table. Omit only when persona_overrides is non-empty."
            ),
        },
        "persona_overrides": {
            "type": "array",
            "items": _PERSONA_ROW_SCHEMA,
            "description": (
                "Workflow-specific personas that differ from the linked app's persona table. "
                "Use only when this workflow's user base is genuinely distinct from the app's "
                "general users. Empty list = inherit from the app pointed at by persona_depth_app_id."
            ),
        },
        "mmi_hypothesis": {
            **_MMI_HYPOTHESIS_SCHEMA,
            "description": (
                "Six-dimension MMI scoring for this workflow with transcript-grounded "
                "reasoning per dimension. Stage 2a is the only place MMI gets real values "
                "— Stage 1's skeleton seed is just a placeholder."
            ),
        },
    },
    "required": ["acceptance_criteria", "process_steps", "mmi_hypothesis"],
}


# ──────────────────────────────────────────────────────────────────────
# Stage 2b — app detail response schema
# ──────────────────────────────────────────────────────────────────────

APP_DETAIL_SCHEMA: dict = {
    "type": "object",
    "description": (
        "Vega v3 Stage 2b per-app detail. Fill personas, integrations, scale profile, "
        "NFRs, and additional source quotes for ONE app. "
        "Do NOT re-emit skeleton fields (app_id, app_name, complexity, source, "
        "linked_workflow_ids). Do NOT add acceptance criteria, decision logic, or "
        "process steps — those live on workflows only."
    ),
    "properties": {
        "personas": {
            "type": "array",
            "items": _PERSONA_ROW_SCHEMA,
            "description": (
                "II.6 — one entry per distinct user persona for this app. "
                "Workflows linked to this app inherit this list unless they override "
                "via persona_overrides."
            ),
        },
        "integrations": {
            "type": "array",
            "items": _INTEGRATION_ENTRY_SCHEMA,
            "description": (
                "II.4 — three-bucket integration list for this app. "
                "Include every system the app reads from or writes to: "
                "Bucket 1 for well-known platforms (pre-research), "
                "Bucket 2 for transcript-confirmed active use, "
                "Bucket 3 for anything requiring a technical follow-up call."
            ),
        },
        "scale_profile": {
            **_SCALE_PROFILE_SCHEMA,
            "description": (
                "II.5 — scale proxy signals + order-of-magnitude inference for this app. "
                "Extract from transcript only — never guess raw GB/TB figures."
            ),
        },
        "nfrs": {
            **_NFRS_SCHEMA,
            "description": (
                "II.8 — seven non-functional requirement dimensions for this app. "
                "Use flags liberally (MISSING, NOT_DISCUSSED, NEEDS_TECHNICAL_FOLLOWUP) "
                "rather than inventing values not supported by the transcript."
            ),
        },
        "novel_ui_patterns": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "UI patterns this app needs that are NOT off-the-shelf component-library "
                "fare. Examples: 'split-screen confidence-review with side-by-side AI vs source', "
                "'Gantt-style scheduling board with drag-resolve conflicts'. "
                "Empty list when standard CRUD UI suffices. Drives sizing for the build team."
            ),
        },
        "custom_logic": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Business-logic items that need bespoke code beyond CRUD: "
                "'compute commission splits across 4-tier referral chain', "
                "'apply tax-jurisdiction rules per ZIP', 'route exceptions by confidence band + SLA tier'. "
                "Each entry is one named rule. Empty list when standard CRUD logic suffices."
            ),
        },
        "pain_point_refs": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Verbatim or near-verbatim transcript quotes capturing pain points this app "
                "specifically addresses. Used by the build team when scope is questioned. "
                "Different from app.source.quote (one summary quote) — list every distinct pain "
                "directly tied to this app. Empty list when no specific pain points were stated."
            ),
        },
        "additional_source_quotes": {
            "type": "array",
            "items": _SOURCE_PROVENANCE_SCHEMA,
            "description": (
                "Extra transcript citations that enriched this app's understanding "
                "beyond the primary AppSkeleton.source quote. Include any significant "
                "direct quotes that support integrations, scale, or NFR decisions."
            ),
        },
    },
    "required": ["personas", "integrations"],
}


# ──────────────────────────────────────────────────────────────────────
# Prompt flag-token block — pre-baked from canonical VegaFlag enum
# ──────────────────────────────────────────────────────────────────────

_FLAG_TOKEN_LINES = "\n".join(
    f"  - [{flag.value}]"
    for flag in VegaFlag
)


# ──────────────────────────────────────────────────────────────────────
# Prior-answers block helper (Wave 5b — partial regeneration)
# ──────────────────────────────────────────────────────────────────────
#
# When the pipeline re-extracts an entity in response to a user-answered
# OpenItem, we pass the entity's existing user_provided_answers AND the
# items just answered through this helper so the LLM treats them as
# authoritative facts and stops re-flagging the same gap.
#
# Stage 2 first-pass callers (full pipeline) leave the slot empty —
# format_prior_answers_block() with an empty dict returns "" so the
# prompt template formats unchanged.

def format_prior_answers_block(prior_answers: dict[str, str] | None) -> str:
    """Render the ``{prior_answers_block}`` slot for Stage 2 prompts.

    Args:
        prior_answers: Mapping of OpenItem.label (or field name slug) to the
            user's verbatim answer. Empty / None ⇒ no block emitted.

    Returns:
        A markdown block to be substituted into the prompt, or "" when
        no prior answers exist (preserves Stage 2 first-pass behavior).
    """
    if not prior_answers:
        return ""

    lines = ["## Prior Answers (do not re-flag these gaps)", ""]
    lines.append(
        "The customer has already answered these open items in a prior session:"
    )
    for label, value in prior_answers.items():
        # Defensive: keep one-line entries readable; truncate stray newlines.
        clean_value = (value or "").strip().replace("\n", " ")
        lines.append(f'- "{label}" → "{clean_value}"')
    lines.append("")
    lines.append(
        "Treat these as authoritative facts. Don't re-flag them as MISSING or "
        "NOT_DISCUSSED. Incorporate them into your output so subsequent "
        "extractions are consistent with the user's answers."
    )
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# Stage 2a — workflow detail prompt
# ──────────────────────────────────────────────────────────────────────

WORKFLOW_DETAIL_PROMPT: str = """\
You are Vega, the AI Recruiter Agent. You are running **Stage 2a of 4** of the \
v3 discovery pipeline — the per-workflow detail pass for workflow \
**{workflow_id}** ({workflow_name}).

Stage 1 already extracted the skeleton for this workflow. Your job now is to \
deepen it: produce acceptance criteria, decision logic, process steps, a \
persona pointer, and a real MMI hypothesis. You must return exactly the fields \
in the Stage 2a response schema — no more, no fewer.

## The judgment bar

Your output feeds two readers: an engineer who has to build the POC from it, \
and a sales rep who has to defend the scope to the customer. **A reader \
should be able to actually start building from your output without asking \
follow-up questions for any field you populate.** If your acceptance criterion \
is "this works when invoices are processed," that is not enough — say what \
"processed" means, what the success state is for the user, and what failure \
looks like. If your MMI reasoning is "this saves time," that is not enough \
— cite the specific quote, hours/week, or pain point from the transcript.

## What you MUST fill

1. ``acceptance_criteria`` — one entry per core flow inside this workflow.
   A "core flow" is a distinct path through the workflow (e.g., "invoice \
   intake", "exception handling", "approval routing"). Each entry needs:
   - ``flow_name``: the name of the flow
   - ``success_when``: plain English — "This flow succeeds when [X]."
   Flag MISSING when no success definition was stated in the transcript.
   Flag INFERRED_CONFIRM when you inferred success from the customer's \
   goals or pain rather than a direct statement.

2. ``decision_logic`` — BPMN exclusive/parallel/inclusive gateways. Each \
   gateway needs a stable ID (d1, d2...), a name, a type (exclusive / \
   parallel / inclusive), and branches. Set inferred=true when the gateway \
   was not explicitly described but is implied by context.

3. ``process_steps`` — 3–12 BPMN-shaped steps. Each needs a stable ID \
   (s1, s2...), a name, and optionally actor_id (human / ai / \
   external_system), task_type (user / service / business_rule / manual), \
   description, and inferred. Prefix the description with "[INFERRED]" and \
   set inferred=true when you added the step from context, not explicit \
   transcript evidence.

4. ``persona_depth_app_id`` — set this to the app ID from the skeleton's \
   linked_app_ids that covers the users of this workflow. When this workflow \
   has no linked app (linked_app_ids is empty), omit this field and populate \
   ``persona_overrides`` instead.

5. ``persona_overrides`` — only populate when the users of THIS workflow are \
   genuinely distinct from the linked app's general users. If the app's \
   persona table already covers this workflow's users, leave this empty and \
   use persona_depth_app_id instead.

6. ``mmi_hypothesis`` — REAL MMI scoring for all six dimensions. This is the \
   only stage where MMI dims get scored — Stage 1's skeleton seed is just a \
   placeholder. Each of the six dims (``tss``, ``ie``, ``time``, ``cost``, \
   ``quality``, ``satisfaction``) needs:
   - ``value``: integer 1–5 (1=very low, 5=very high)
   - ``confidence``: one of "high" / "medium" / "low". "high" only when the \
     transcript directly states the fact behind the score; "medium" when you \
     inferred from context; "low" when you're guessing and a follow-up \
     question is needed. Default to "medium" if unsure — never "high" unless \
     the transcript truly grounds it.
   - ``reasoning``: 1–2 sentences citing **specific transcript evidence** \
     (volumes, hours, speakers, pain quotes). Generic copy like "this would \
     save time" is unacceptable — anchor every score in something the \
     customer actually said.
   Also fill ``needs_client_input`` (array of strings) with any open \
   questions you couldn't answer from the transcript. Every "low" confidence \
   dim should produce at least one entry in needs_client_input. Empty list \
   only when the scoring is fully grounded.

## Workflow skeleton for context

```json
{skeleton_json}
```

## What you MUST NOT do

- Do NOT re-emit workflow_id, workflow_name, complexity, source, or \
  linked_app_ids — those are already in the artifact from Stage 1.
- Do NOT add integrations, scale_profile, or NFRs — those live on apps only.
- Do NOT regenerate or change any IDs that Stage 1 already assigned.
- Do NOT emit BPMN XML — Stage 3 generates that from process_steps.

## Flag tokens

Emit exactly one of these six values in any flag field you populate:
__FLAG_TOKENS__

Flag rules for Stage 2a fields:
  - ``AcceptanceCriterion.flag``: MISSING when no success definition found, \
    INFERRED_CONFIRM when you inferred it from goals/pain.
  - ``ProcessStep.description``: prefix with "[INFERRED]" and set \
    inferred=true when the step is inferred.
  - ``DecisionPoint.inferred``: true when the gateway was implied, not stated.
  - ``SourceProvenance.source_flag``: SOURCE_UNCLEAR when you cannot pin \
    content to a transcript moment.
  - ``PersonaRow.flags``: INFERRED_CONFIRM when technical_literacy is inferred.

Return strictly the structured JSON described by the response schema. No \
free-text preamble, no markdown.

{prior_answers_block}

<transcript>
{transcript_text}
</transcript>
"""

WORKFLOW_DETAIL_PROMPT = WORKFLOW_DETAIL_PROMPT.replace("__FLAG_TOKENS__", _FLAG_TOKEN_LINES)


# ──────────────────────────────────────────────────────────────────────
# Stage 2b — app detail prompt
# ──────────────────────────────────────────────────────────────────────

APP_DETAIL_PROMPT: str = """\
You are Vega, the AI Recruiter Agent. You are running **Stage 2b of 4** of the \
v3 discovery pipeline — the per-app detail pass for app \
**{app_id}** ({app_name}).

Stage 1 already extracted the skeleton for this app. Your job now is to deepen \
it: produce the persona table, integration list, scale profile, NFRs, and \
additional source quotes. Return exactly the fields in the Stage 2b response \
schema — no more, no fewer.

## What you MUST fill

1. ``personas`` — one entry per distinct user type for this app.
   Include role, count (if stated), device (if stated), technical_literacy \
   (inferred — always carry INFERRED_CONFIRM in flags), \
   ``concurrent_usage`` (load-sizing signal — 'all 50 log in at 8am', \
   'sporadic ~5 at a time'), and ``accessibility_needs`` (WCAG, screen \
   reader, keyboard-only, color-blind palette). If accessibility wasn't \
   discussed, omit and add NEEDS_TECHNICAL_FOLLOWUP to flags rather than \
   inventing a value. If no personas were discussed, return a single entry \
   with role="unknown" and flags=["MISSING"].

2. ``integrations`` — three-bucket classification for every system this app \
   reads from or writes to, PLUS the build-handoff depth fields:
   - Bucket 1: pre-research (well-known platform you know from training data)
   - Bucket 2: transcript-confirmed (customer mentioned actively using it)
   - Bucket 3: needs technical follow-up (auth method, API tier, rate limits)
   For EACH integration, fill the structured fields below. **PREFER an inferred \
   value with INFERRED_CONFIRM in flags over 'unknown'** — for well-known \
   systems you know from training data, infer the dominant production auth \
   pattern. Only use 'unknown' when the system is truly unrecognizable.

   AUTH METHOD INFERENCE CATALOG (use these defaults; mark INFERRED_CONFIRM):
   - Salesforce → 'OAuth2 (JWT bearer flow for server-to-server, or web-server \
     flow for user context)'
   - HubSpot → 'OAuth2 (public app) or Private App API key'
   - QuickBooks Online → 'OAuth2 with Intuit-managed app, refresh token \
     rotation required'
   - QuickBooks Desktop → 'QBXML over Web Connector with username/password'
   - NetSuite → 'Token-Based Authentication (TBA) or OAuth2.0 with consumer \
     key/secret'
   - Stripe → 'API key (publishable + secret), webhook-signed with signing \
     secret'
   - Google Workspace / Sheets / Drive / Gmail → 'OAuth2 with Google service \
     account (domain-wide delegation) or user-context refresh token'
   - Microsoft 365 / Outlook / SharePoint / Graph → 'OAuth2 via MSAL, \
     application permissions or delegated permissions'
   - Slack → 'OAuth2 user/bot tokens via Slack app'
   - Zoom (any product line) → 'OAuth2 server-to-server app or JWT (deprecated)'
   - Zendesk → 'OAuth2 or API token + email'
   - Jira / Confluence (Atlassian) → 'OAuth2 (3LO) or API token + email (basic)'
   - GitHub → 'OAuth2 app, GitHub App with installation token, or PAT'
   - AWS APIs → 'IAM SigV4 with access-key/secret-key or IAM role assumption'
   - Generic SQL DB (MySQL / Postgres / MS SQL) → 'connection string with \
     service-account credentials, network-restricted or VPN-tunneled'
   - Email/IMAP → 'OAuth2 (Google/Microsoft) or app-specific password for \
     legacy IMAP'
   - SFTP → 'SSH key pair, customer-managed user'
   - Generic REST API with no clear vendor → 'API key in Authorization header' \
     (mark NEEDS_TECHNICAL_FOLLOWUP)
   - Industry vertical platforms (AppDirect, ConnectWise, Autotask, etc.) → \
     'OAuth2 with vendor-managed app, partner program API key, or both — \
     verify in the vendor developer portal' (mark NEEDS_TECHNICAL_FOLLOWUP \
     since the specific tier matters)

   For OTHER fields:
   - ``disposition``: integrate / replace / unknown — replace when the customer \
     wants OUR new system to take this over, integrate otherwise
   - ``rate_limits``: only when explicitly stated or when the well-known \
     vendor has a famous tier limit; otherwise omit + flag NEEDS_TECHNICAL_FOLLOWUP
   - ``sandbox_availability``: 'Sandbox available, customer-provisioned' / \
     'No sandbox, dev against prod with feature-flag' / NEEDS_TECHNICAL_FOLLOWUP
   - ``interaction_pattern``: webhook / polling / batch / streaming / \
     manual_export. Infer from the workflow: real-time triggers ⇒ webhook; \
     monthly reports ⇒ batch; status checks ⇒ polling.
   - ``read_write``: explicit when the workflow makes it clear (e.g., \
     'fetch existing orders' = read; 'create new order' = write).
   - ``access_status``: already_provisioned / needs_setup / unknown — \
     already_provisioned only when the customer said they already have a \
     working integration today.

   Bucket 3 entries carry NEEDS_TECHNICAL_FOLLOWUP. Bucket 1 entries inferred \
   from industry context carry INFERRED_CONFIRM.

3. ``scale_profile`` — extract any proxy signals: transaction volume, \
   retention window, growth trajectory, or speed/storage pain statements. \
   Set inference to your order-of-magnitude conclusion if you have enough \
   signals (e.g. "normal app database is sufficient" vs "data lake likely \
   required"). Flag MISSING on the top-level flag field when no scale signals \
   were present.

4. ``nfrs`` — fill the nine dimensions for this app. Use flags liberally:
   - device_support, browser_support: NOT_STATED if not mentioned
   - sla, multi_region, audit_logging: NEEDS_TECHNICAL_FOLLOWUP unless \
     explicitly stated
   - compliance, rbac: may be INFERRED_CONFIRM from industry context
   - data_freshness: may be INFERRED_CONFIRM from use-case context
   - performance_budgets: NOT_STATED unless the customer gave concrete \
     latency or throughput numbers ("p95 page load < 2s", "list view < 1s \
     for 10k rows"). Do not invent budgets.
   Do not invent values — flag and omit rather than guess.

5. ``novel_ui_patterns`` — list every UI pattern this app needs that is NOT \
   a stock CRUD form / table / detail page. Be ACTIVE about identifying them; \
   empty list is wrong for any L or XL complexity app. Examples that count: \
   'split-screen review comparing source document and AI-extracted fields \
   with field-level confidence badges', 'inline editable table with optimistic \
   updates and undo', 'Gantt-style scheduling board with drag-resolve', \
   'real-time collaborative cursor', 'kanban with WIP limits per column', \
   'document annotation overlay'. If the app has confidence scoring → there \
   is a novel UI pattern (badge / sort / filter by confidence). If the app \
   has dual-pane review → there is a novel UI pattern. List them.

6. ``custom_logic`` — list every business-logic rule beyond stock CRUD. Be \
   ACTIVE; empty list is wrong for any app with scoring, routing, or \
   state-machine behavior. Examples: 'route to manual review queue when \
   confidence < 0.85', 'auto-approve when all fields confidence > 0.95', \
   'escalate to senior reviewer when SLA timer < 1 hour', 'compute \
   commission splits across 4-tier referral chain'. Each entry one rule.

7. ``pain_point_refs`` — verbatim or near-verbatim transcript quotes that \
   capture pain points this app specifically addresses. Quote LITERALLY from \
   the transcript — short snippets are fine ("If my confidence is less than \
   95, pop it up..."), but they must be the customer's actual words, not \
   paraphrase. Different from ``additional_source_quotes`` (which support \
   extraction decisions); these are the customer-stated problems the app \
   solves. The build team uses this list when scope is questioned. \
   **Empty list is almost certainly wrong** — every app surfaced from a \
   discovery transcript has at least one pain it addresses; quote it.

8. ``additional_source_quotes`` — any significant direct quotes that \
   supported your integrations, scale, or NFR decisions beyond the primary \
   source quote already in the skeleton.

## App skeleton for context

```json
{skeleton_json}
```

## What you MUST NOT do

- Do NOT re-emit app_id, app_name, complexity, source, or \
  linked_workflow_ids — those are already in the artifact from Stage 1.
- Do NOT add acceptance criteria, decision logic, or process steps — those \
  live on workflows only.
- Do NOT regenerate or change any IDs that Stage 1 already assigned.
- Do NOT emit BPMN XML — Stage 3 generates that.

## Flag tokens

Emit exactly one of these six values in any flag field you populate:
__FLAG_TOKENS__

Flag rules for Stage 2b fields:
  - ``PersonaRow.flags``: INFERRED_CONFIRM when technical_literacy is inferred; \
    MISSING when the persona table was entirely absent from the transcript.
  - ``IntegrationEntry.flags``: NEEDS_TECHNICAL_FOLLOWUP for Bucket 3; \
    INFERRED_CONFIRM for Bucket 1 entries inferred from industry.
  - ``ScaleProfile.flag``: MISSING when no scale signals were present; \
    INFERRED_CONFIRM when inference field is populated.
  - ``NFRs.flags``: list the flag for each dimension that could not be \
    cleanly extracted (one flag per such dimension).
  - ``SourceProvenance.source_flag``: SOURCE_UNCLEAR when you cannot pin \
    content to a transcript moment.

Return strictly the structured JSON described by the response schema. No \
free-text preamble, no markdown.

{prior_answers_block}

<transcript>
{transcript_text}
</transcript>
"""

APP_DETAIL_PROMPT = APP_DETAIL_PROMPT.replace("__FLAG_TOKENS__", _FLAG_TOKEN_LINES)


__all__ = [
    "WORKFLOW_DETAIL_SCHEMA",
    "WORKFLOW_DETAIL_PROMPT",
    "APP_DETAIL_SCHEMA",
    "APP_DETAIL_PROMPT",
    "format_prior_answers_block",
]
