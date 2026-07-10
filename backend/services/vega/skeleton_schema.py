"""Vega v3 — Stage 1 skeleton call: Gemini response_schema + prompt template.

This module is owned by Wave 2 / Thread 2a. It defines the *shape* of the
Stage 1 LLM call that produces ``WorkflowSkeleton`` + ``AppSkeleton`` +
``SessionFlags`` + top-level identity / transcript-ref fields.

It is intentionally hand-built (not auto-derived from Pydantic):
  * Gemini's structured-output validator expects a JSON-schema-ish dict.
  * The v2 pipeline already builds its schemas this way (see
    ``TRANSCRIPT_ANALYSIS_SCHEMA`` and friends in ``services/ai/vega_analysis.py``).
  * Hand-building lets us trim non-Stage-1 fields (no ``acceptance_criteria``,
    no ``personas``, no ``integrations`` — those land in Stage 2).

The shape mirrors the Pydantic contract in ``services/vega/models_v3.py``
exactly for the fields it covers. If the contract changes, update this
schema in lock-step.

Field-shape rules (from contract + Wave 1 SHOULD-ADD items):

* ``complexity`` is ``Literal["S", "M", "L", "XL"]`` — exposed as a JSON
  ``enum`` here. If Gemini's state budget complains, callers should run
  ``services.ai.vega_analysis._strip_enums_for_gemini`` over the schema
  before sending — the pattern carries forward from v2.

* ``mmi_dims`` is locked to the six known dimension keys (``tss``, ``ie``,
  ``time``, ``cost``, ``quality``, ``satisfaction``). Wave 1 SHOULD-ADD
  flagged the v2 ``dict[str, int]`` shape as too loose; we promote it to
  a fixed-shape object with each of the six keys named explicitly.

* ``VegaFlag`` enums are emitted inline (not referenced) so Gemini doesn't
  have to chase ``$ref``. Same enum-strip caveat applies.

* ``client_company`` (NOT ``client_name``) — v3 rename per contract.
"""

from __future__ import annotations

from services.vega.flags import VegaFlag

# ──────────────────────────────────────────────────────────────────────
# Reusable sub-schemas
# ──────────────────────────────────────────────────────────────────────

_VEGA_FLAG_VALUES: list[str] = [f.value for f in VegaFlag]
_COMPLEXITY_VALUES: list[str] = ["S", "M", "L", "XL"]
_MMI_DIM_KEYS: list[str] = ["tss", "ie", "time", "cost", "quality", "satisfaction"]


_SOURCE_PROVENANCE_SCHEMA: dict = {
    "type": "object",
    "description": (
        "Where in the transcript a piece of artifact content came from. "
        "Set source_flag to SOURCE_UNCLEAR if you cannot pin a quote."
    ),
    "properties": {
        "transcript_filename": {
            "type": "string",
            "description": "Filename of the transcript this content was sourced from.",
        },
        "timestamp_seconds": {
            "type": "number",
            "description": "Approximate offset into the transcript audio in seconds. May be omitted.",
        },
        "speaker": {
            "type": "string",
            "description": "Speaker name attributed to the quote, if known.",
        },
        "quote": {
            "type": "string",
            "description": (
                "The verbatim quote (or closest paraphrase) from the transcript that "
                "originated this content. Use the customer's words, not your summary."
            ),
        },
        "source_flag": {
            "type": "string",
            "enum": _VEGA_FLAG_VALUES,
            "description": (
                "Set to SOURCE_UNCLEAR when you generated this content but cannot "
                "trace it to a specific transcript moment. Otherwise omit."
            ),
        },
    },
    "required": [],
}


# Six MMI dimension integer values, locked to known keys.
# Stage 1 only emits the *integer* per dimension; Stage 2a fills the full
# MMIHypothesis (with confidence + reasoning) downstream.
_MMI_DIMS_SCHEMA: dict = {
    "type": "object",
    "description": (
        "Integer 1-5 score per MMI dimension. All six keys must be present. "
        "Stage 2a will deepen this into a full MMIHypothesis with confidence + reasoning."
    ),
    "properties": {
        key: {
            "type": "integer",
            "description": f"1-5 score on the {key} dimension.",
        }
        for key in _MMI_DIM_KEYS
    },
    "required": list(_MMI_DIM_KEYS),
}


_TRANSCRIPT_REF_SCHEMA: dict = {
    "type": "object",
    "description": "Reference to one source transcript that fed this analysis.",
    "properties": {
        "filename": {
            "type": "string",
            "description": "Original filename of the transcript.",
        },
        "duration_minutes": {
            "type": "number",
            "description": "Duration of the underlying call in minutes. Omit if unknown.",
        },
        "attendees": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Names of people on the call when extractable.",
        },
        "captured_at": {
            "type": "string",
            "description": (
                "ISO 8601 timestamp of when the call was held. String, not datetime — "
                "the orchestrator parses if needed."
            ),
        },
    },
    "required": ["filename"],
}


_WORKFLOW_SKELETON_SCHEMA: dict = {
    "type": "object",
    "description": (
        "Stage 1 skeleton view of one workflow. NO acceptance criteria, NO process "
        "steps, NO persona depth — those are Stage 2a's job."
    ),
    "properties": {
        "workflow_id": {
            "type": "string",
            "description": (
                "Stable workflow identifier (e.g., 'wf_invoice_intake'). Use snake_case, "
                "prefix with 'wf_'. Reused verbatim through every later stage — do not "
                "regenerate."
            ),
        },
        "workflow_name": {
            "type": "string",
            "description": "Human-readable workflow name.",
        },
        "complexity": {
            "type": "string",
            "enum": _COMPLEXITY_VALUES,
            "description": "II.7 complexity score: S/M/L/XL.",
        },
        "mmi_dims": _MMI_DIMS_SCHEMA,
        "source": _SOURCE_PROVENANCE_SCHEMA,
        "linked_app_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "App IDs this workflow runs through. Must be a subset of the app_id "
                "values in this same response. Empty list = workflow currently runs "
                "purely manually."
            ),
        },
    },
    "required": ["workflow_id", "workflow_name", "source"],
}


_APP_SKELETON_SCHEMA: dict = {
    "type": "object",
    "description": (
        "Stage 1 skeleton view of one app. NO personas, NO integrations 3-bucket, "
        "NO scale profile, NO NFRs — those are Stage 2b's job."
    ),
    "properties": {
        "app_id": {
            "type": "string",
            "description": (
                "Stable app identifier (e.g., 'app_field_dispatch'). Use snake_case, "
                "prefix with 'app_'. Reused verbatim through every later stage."
            ),
        },
        "app_name": {
            "type": "string",
            "description": "Human-readable app name.",
        },
        "complexity": {
            "type": "string",
            "enum": _COMPLEXITY_VALUES,
            "description": "II.7 complexity score mirrored onto apps: S/M/L/XL.",
        },
        "build_intent": {
            "type": "string",
            "enum": ["build_for_customer", "existing_tool", "unclear"],
            "description": (
                "Required. ``build_for_customer`` = customer wants US to build this app "
                "(gets an EADD in Stage 3). ``existing_tool`` = customer already uses this "
                "(Google Sheets, Salesforce, Slack, Excel, QuickBooks, Outlook, etc.) and "
                "we are NOT building it — these should normally not appear in apps[] at all "
                "and should instead be referenced via integrations on workflows or other "
                "apps. ``unclear`` only if you genuinely cannot tell from the transcript "
                "(rare — pair with source_flag=SOURCE_UNCLEAR)."
            ),
        },
        "source": _SOURCE_PROVENANCE_SCHEMA,
        "linked_workflow_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Workflow IDs this app supports. Must be a subset of the workflow_id "
                "values in this same response. Empty list = standalone app."
            ),
        },
    },
    "required": ["app_id", "app_name", "build_intent", "source"],
}


_SESSION_FLAGS_SCHEMA: dict = {
    "type": "object",
    "description": "II.3 — top-of-session technical flags. Each value+flag pair documents what was extracted and why anything is missing.",
    "properties": {
        "tech_contact": {
            "type": "string",
            "description": (
                "Name of the customer-side technical contact for integrations. "
                "Omit if no name was mentioned and pair with tech_contact_flag=MISSING."
            ),
        },
        "tech_contact_flag": {
            "type": "string",
            "enum": _VEGA_FLAG_VALUES,
            "description": "MISSING when no technical contact name was identifiable.",
        },
        "engineering_capacity": {
            "type": "boolean",
            "description": (
                "True/False for whether the customer has internal engineering capacity. "
                "Omit when not deducible and pair with engineering_capacity_flag=MISSING."
            ),
        },
        "engineering_capacity_flag": {
            "type": "string",
            "enum": _VEGA_FLAG_VALUES,
            "description": "MISSING when transcript does not state engineering capacity either way.",
        },
        "it_infosec_gate": {
            "type": "boolean",
            "description": (
                "True/False for whether an IT/infosec approval gate exists. "
                "Omit when not discussed and pair with it_infosec_gate_flag=NOT_DISCUSSED."
            ),
        },
        "it_infosec_gate_flag": {
            "type": "string",
            "enum": _VEGA_FLAG_VALUES,
            "description": "NOT_DISCUSSED when transcript contains no mention of IT approval, security review, or access controls.",
        },
    },
    "required": [],
}


# ──────────────────────────────────────────────────────────────────────
# Top-level Stage 1 schema
# ──────────────────────────────────────────────────────────────────────

SKELETON_SCHEMA: dict = {
    "type": "object",
    "description": (
        "Vega v3 Stage 1 skeleton response. Identifies workflows + apps + "
        "session-level flags from the full transcript. NO deep detail — "
        "Stage 2 fans out per-entity calls to fill acceptance criteria, "
        "personas, integrations, NFRs, etc."
    ),
    "properties": {
        "client_company": {
            "type": "string",
            "description": (
                "Client/company name discussed in the call. Required. "
                "(Note: v3 rename — was 'client_name' in v2.)"
            ),
        },
        "source_transcripts": {
            "type": "array",
            "items": _TRANSCRIPT_REF_SCHEMA,
            "description": (
                "One entry per transcript that fed this analysis. May be empty if "
                "metadata is unrecoverable from the transcript text alone — orchestrator "
                "will backfill from the upload records."
            ),
        },
        "session_flags": _SESSION_FLAGS_SCHEMA,
        "workflows": {
            "type": "array",
            "items": _WORKFLOW_SKELETON_SCHEMA,
            "description": "All distinct workflows you identified in the transcript.",
        },
        "apps": {
            "type": "array",
            "items": _APP_SKELETON_SCHEMA,
            "description": (
                "All distinct apps you identified. Apps with linked_workflow_ids == [] "
                "are standalone — the renderer puts them in a 'Standalone Apps' tab. "
                "Stage 4 back-fills linked_workflow_ids from any workflow that names this app."
            ),
        },
    },
    "required": ["client_company", "workflows", "apps"],
}


# ──────────────────────────────────────────────────────────────────────
# Stage 1 prompt
# ──────────────────────────────────────────────────────────────────────
#
# Inline constant follows the v2 convention (see ``ANALYSIS_PROMPT``,
# ``CALL2_ANALYSIS_PROMPT`` in ``services/ai/vega_analysis.py``). The
# transcript is injected via ``{transcript_text}``.

# Build the bracketed flag list reflectively from the canonical enum so
# the prompt automatically tracks any future additions to flags.py.
# Pre-baked into SKELETON_PROMPT below so callers only have to .format()
# transcript_text at call time.
_FLAG_TOKEN_LINES = "\n".join(
    f"  - [{flag.value}]"
    for flag in VegaFlag
)


SKELETON_PROMPT: str = """\
You are Vega, the AI Recruiter Agent. You are running **Stage 1 of 4** of the v3 \
discovery pipeline.

Your job in Stage 1 is **structural identification only**: pull out the workflows \
and apps the customer talks about, the high-level MMI dimension scores, and the \
session-level technical flags. Stage 2 will fan out per-workflow and per-app calls \
to fill acceptance criteria, personas, integrations, NFRs, scale profile, and \
process steps. **Do not produce that detail in this call** — keep your response \
focused and short so the structured-output validator stays inside Gemini's state \
budget.

What you MUST extract:
  1. ``client_company`` — the customer/company name.
  2. ``source_transcripts[]`` — one entry per uploaded transcript with filename, \
     and where extractable: duration, attendees, captured_at. If you cannot tell, \
     leave the optional fields off; the orchestrator will backfill from upload \
     metadata.
  3. ``session_flags`` — the three II.3 technical flags (tech_contact, \
     engineering_capacity, it_infosec_gate) each as a value + flag pair.
  4. ``workflows[]`` — every distinct business workflow the customer described, \
     with: workflow_id, workflow_name, complexity (S/M/L/XL), mmi_dims (six \
     integer 1-5 scores), source quote, linked_app_ids.
  5. ``apps[]`` — apps the customer wants US to BUILD (with: app_id, \
     app_name, complexity, build_intent, source quote, linked_workflow_ids). \
     **Critical:** ``apps[]`` is for apps the customer wants built, NOT \
     existing tools they already use. Tools like Google Sheets, Excel, \
     Salesforce, Slack, Outlook, QuickBooks, HubSpot, ServiceTitan, Asana, \
     Monday, Notion, SharePoint, OneDrive, Dropbox, Zapier, Make, etc. are \
     ``existing_tool`` and should be referenced via Stage 2b's \
     ``integrations`` list on whichever workflow or app uses them — not \
     listed as apps. If you are unsure whether the customer is asking us \
     to build something or just naming a tool they use, set \
     ``build_intent="unclear"`` and pair with a NEEDS_TECHNICAL_FOLLOWUP \
     flag on the source — Stage 4 will surface it as an Open Item rather \
     than wasting a Stage 3 EADD on a tool we're not building.

What you MUST NOT do in this call:
  * Do NOT emit acceptance criteria, decision logic, process steps, persona tables, \
    integration 3-bucket entries, scale profile, NFRs, or full MMI reasoning. \
    Those land in Stage 2.
  * Do NOT invent IDs that are not consistent across workflows[] and apps[]. If \
    workflow ``wf_x`` lists ``app_y`` in linked_app_ids, ``app_y`` must appear in \
    apps[] (the back-fill is one-way: missing app_id = real bug).
  * Do NOT regenerate IDs you've already used in earlier outputs. Use stable \
    snake_case names (``wf_invoice_intake``, ``app_field_dispatch``).

Flag tokens you will emit (each leaf field that can be flagged uses one of these \
six values, exactly):
__FLAG_TOKENS__

Flag rules for Stage 1 fields:
  * ``SessionFlags.tech_contact_flag``: MISSING when no technical contact name \
    was identifiable in the transcript.
  * ``SessionFlags.engineering_capacity_flag``: MISSING when transcript does not \
    state engineering capacity either way.
  * ``SessionFlags.it_infosec_gate_flag``: NOT_DISCUSSED when transcript contains \
    no mention of IT approval, security review, or access controls.
  * ``SourceProvenance.source_flag``: SOURCE_UNCLEAR when you cannot pin the \
    content to a transcript moment.

MMI dimensions (all six required, integer 1-5):
  * ``tss`` — Task structure / standardization
  * ``ie`` — Information environment / data accessibility
  * ``time`` — Time-savings potential
  * ``cost`` — Cost-savings potential
  * ``quality`` — Quality / error-rate improvement
  * ``satisfaction`` — Stakeholder-satisfaction lift

Stage 1 confidence: emit your best read on each MMI integer. Stage 2a will \
revisit each workflow with full transcript context and produce reasoning + \
confidence per dimension; you don't need to second-guess yourself here.

Source provenance: every workflow and every app must carry a ``source`` quote \
from the transcript. If you genuinely cannot find one, set ``source_flag`` to \
SOURCE_UNCLEAR — Stage 4 will surface that as an OpenItem.

Return strictly the structured JSON described by the response schema. No \
free-text preamble, no markdown.

<transcript>
{transcript_text}
</transcript>
"""


# Bake the static flag-token list into the prompt at module-load time so
# downstream callers only need to .format(transcript_text=...).
SKELETON_PROMPT = SKELETON_PROMPT.replace("__FLAG_TOKENS__", _FLAG_TOKEN_LINES)


__all__ = [
    "SKELETON_SCHEMA",
    "SKELETON_PROMPT",
]
