"""Vega v3 artifact contract — Pydantic models.

This module defines the canonical shape of the Vega v3 discovery artifact
that flows through the staged extraction pipeline (Wave 2 owns the
orchestrator; this file is types only).

Pipeline staging — which models each stage produces:

    Stage 1  (skeleton call)         -> WorkflowSkeleton, AppSkeleton, SessionFlags
    Stage 2a (per-workflow detail)   -> WorkflowDetail (merged into Workflow)
    Stage 2b (per-app detail)        -> AppDetail (merged into App)
    Stage 3  (BPMN + design docs)    -> bpmn_id / bpmn_xml / bpmn_status / design fields
    Stage 4  (post-processing)       -> SharedInfrastructureRow, OpenItem, extraction_status

The renderer (Wave 3) consumes ``DiscoveryAnalysisV3`` directly. The
prompt-deriver (Wave 2 Thread 2a) walks this module reflectively to
produce a Gemini ``response_schema`` — every leaf field carries a
``Field(..., description=...)`` for that purpose.

Naming conventions are carried forward from v2 (`workflow_id`,
`mmi_hypothesis`, `bpmn_id`, `bpmn_xml`, `bpmn_status`,
`bpmn_content_hash`) so v2 and v3 sessions can share BPMN storage and
helper utilities.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# ──────────────────────────────────────────────────────────────────────
# Flags
# ──────────────────────────────────────────────────────────────────────
# Canonical flag enum lives in services/vega/flags.py. Re-imported here so
# downstream code can pull the type from this module if convenient.

from services.vega.flags import VegaFlag


__all__ = [
    "VegaFlag",
    "SourceProvenance",
    "AcceptanceCriterion",
    "PersonaRow",
    "IntegrationEntry",
    "ScaleProfile",
    "NFRs",
    "DecisionBranch",
    "DecisionPoint",
    "ProcessStep",
    "MMIDimension",
    "MMIHypothesis",
    "WorkflowSkeleton",
    "WorkflowDetail",
    "Workflow",
    "AppSkeleton",
    "AppDetail",
    "App",
    "SessionFlags",
    "SharedInfrastructureRow",
    "OpenItem",
    "TranscriptRef",
    "PipelineMetadata",
    "DiscoveryAnalysisV3",
]


# ──────────────────────────────────────────────────────────────────────
# Building blocks
# ──────────────────────────────────────────────────────────────────────


class SourceProvenance(BaseModel):
    """Where in the transcript a piece of artifact content came from.

    Attached to every Workflow and App; also reused inside
    AppDetail.additional_source_quotes for secondary citations.
    """

    transcript_filename: Optional[str] = Field(
        default=None,
        description="Filename of the transcript this content was sourced from. "
        "None only when the LLM cannot pin a source — set source_flag to SOURCE_UNCLEAR.",
    )
    timestamp_seconds: Optional[float] = Field(
        default=None,
        description="Approximate offset into the transcript audio in seconds. "
        "May be None if the transcript lacks timestamps; that is not itself a flag condition.",
    )
    speaker: Optional[str] = Field(
        default=None,
        description="Speaker name attributed to the quote, if known.",
    )
    quote: Optional[str] = Field(
        default=None,
        description="The verbatim quote (or closest paraphrase) from the transcript that "
        "originated this content. Use the customer's words, not Vega's summary.",
    )
    source_flag: Optional[VegaFlag] = Field(
        default=None,
        description="Set to SOURCE_UNCLEAR when Vega generated this content but cannot "
        "trace it to a specific transcript moment. Otherwise None.",
    )


class AcceptanceCriterion(BaseModel):
    """II.1 — success definition for a single core flow inside a workflow."""

    flow_name: str = Field(
        ...,
        description="Name of the core flow this acceptance criterion applies to "
        "(e.g., 'invoice intake', 'PO matching').",
    )
    success_when: Optional[str] = Field(
        default=None,
        description="Plain-English success definition: 'This flow succeeds when [X].' "
        "None if Vega could not extract or infer one — set flag to MISSING.",
    )
    flag: Optional[VegaFlag] = Field(
        default=None,
        description="MISSING when no success criterion was found in the transcript. "
        "INFERRED_CONFIRM when Vega inferred it from goals/pain rather than direct statement.",
    )


class PersonaRow(BaseModel):
    """II.6 — one user persona for an app.

    Build-handoff fields per dev requirements: count, technical literacy,
    primary device, concurrent usage, and accessibility needs are all
    captured here so the build team can size load + meet WCAG without
    re-running discovery.
    """

    role: str = Field(
        ...,
        description="Persona role label (e.g., 'field technician', 'finance manager').",
    )
    count: Optional[int] = Field(
        default=None,
        description="Number of users in this persona. None if not stated in transcript.",
    )
    device: Optional[str] = Field(
        default=None,
        description="Primary device this persona uses (e.g., 'laptop', 'mobile', 'tablet'). "
        "Extract from transcript only — do not infer.",
    )
    technical_literacy: Optional[str] = Field(
        default=None,
        description="Inferred from transcript context and conversation tone "
        "(e.g., 'low', 'moderate', 'high'). Always carry INFERRED_CONFIRM in flags.",
    )
    concurrent_usage: Optional[str] = Field(
        default=None,
        description="Concurrent-usage pattern signal: 'all 50 log in at 8am for daily standup', "
        "'sporadic, ~5 at a time', 'sustained throughout business hours'. "
        "Drives load sizing for the build team. None if not stated.",
    )
    accessibility_needs: Optional[str] = Field(
        default=None,
        description="Accessibility requirements — WCAG level, screen reader support, "
        "keyboard-only operation, color-blind safe palette, etc. None if not stated; "
        "default to NOT_STATED + NEEDS_TECHNICAL_FOLLOWUP rather than inferring.",
    )
    flags: list[VegaFlag] = Field(
        default_factory=list,
        description="Per-leaf-field flags. Empty list if every leaf is cleanly extracted.",
    )


class IntegrationEntry(BaseModel):
    """II.4 — three-bucket integration tagging + build-handoff depth fields.

    Bucket 1: pre-research (well-known platforms Vega can describe from training)
    Bucket 2: transcript-extracted (customer mentioned actively using it)
    Bucket 3: needs technical follow-up (auth, API tier, rate limits, sandbox)

    The depth fields (disposition / auth_method / rate_limits / sandbox /
    interaction_pattern / read_write / access_status) are required for the
    build team to start work without re-running discovery. Each is Optional
    because rare deals will leave some unanswered — the renderer surfaces
    "NOT CAPTURED" so the dev team chases the gap rather than absorbing it.
    """

    system_name: str = Field(
        ...,
        description="Name of the integrated system (e.g., 'QuickBooks Online', 'Salesforce').",
    )
    bucket: Literal[1, 2, 3] = Field(
        ...,
        description="Bucket 1 = pre-research, 2 = transcript-extracted, "
        "3 = needs technical follow-up. Use Literal[1,2,3] (int) — Gemini schema export "
        "preserves Literal int unions but drops Annotated/Field constraint bounds.",
    )
    disposition: Optional[Literal["integrate", "replace", "unknown"]] = Field(
        default=None,
        description="Whether we INTEGRATE with this system or REPLACE it. Drives whether "
        "the build team writes adapters vs migration tooling. Default 'unknown' when not stated.",
    )
    auth_method: Optional[str] = Field(
        default=None,
        description="Authentication method if discoverable: 'OAuth2 client credentials', "
        "'API key in header', 'mTLS', 'SAML SSO', etc. None when not stated.",
    )
    rate_limits: Optional[str] = Field(
        default=None,
        description="API tier / rate limits if known: 'Salesforce Enterprise tier — "
        "100k API calls/day', 'free tier — 60 reqs/min'. None when not stated.",
    )
    sandbox_availability: Optional[str] = Field(
        default=None,
        description="Whether a sandbox or test environment is available, and how to "
        "obtain access. None when not discussed.",
    )
    interaction_pattern: Optional[Literal["webhook", "polling", "batch", "streaming", "manual_export", "unknown"]] = Field(
        default=None,
        description="How the integration moves data: webhook push, polling, batch ETL, "
        "real-time streaming, or manual file export. Drives architecture decisions.",
    )
    read_write: Optional[Literal["read", "write", "read_write", "unknown"]] = Field(
        default=None,
        description="Whether we read from this system, write to it, or both. "
        "Different from notes — this is structured for the build team's checklist.",
    )
    access_status: Optional[Literal["already_provisioned", "needs_setup", "unknown"]] = Field(
        default=None,
        description="Whether the customer has already set up our access (creds in hand, "
        "sandbox usable) vs the build team has to wait for provisioning. Critical for sequencing.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Free-text notes — usage volume, legacy vs active, anything else "
        "that doesn't fit the structured fields above.",
    )
    flags: list[VegaFlag] = Field(
        default_factory=list,
        description="Typically [NEEDS_TECHNICAL_FOLLOWUP] for Bucket 3 entries. "
        "Bucket 1 entries may carry [INFERRED_CONFIRM] when industry-derived rather "
        "than transcript-stated.",
    )


class ScaleProfile(BaseModel):
    """II.5 — proxy signals for data volume / scale per app.

    Vega never guesses GB/TB. It captures the four proxy signals Samson defined
    (volume, retention, growth, pain) and renders an order-of-magnitude inference.
    """

    transaction_volume: Optional[str] = Field(
        default=None,
        description="Any plain-language volume signal: '~5k orders/day', "
        "'a few thousand records per month', etc. None if not stated.",
    )
    retention_window: Optional[str] = Field(
        default=None,
        description="Data retention requirement if mentioned: '5-year retention', "
        "'audit trail forever'. None if not discussed.",
    )
    growth_trajectory: Optional[str] = Field(
        default=None,
        description="Growth commentary from the transcript: '2x YoY', 'flat', "
        "'rolling out to 12 more locations'. None if not discussed.",
    )
    speed_or_storage_pain: Optional[str] = Field(
        default=None,
        description="Reported pain with current speed or storage limits: "
        "'queries take 10 minutes', 'we hit Excel's row cap'. None if not mentioned.",
    )
    inference: Optional[str] = Field(
        default=None,
        description="Vega's order-of-magnitude inference, e.g., 'data lake likely required' "
        "or 'normal app database is sufficient'. None when no signals were extractable.",
    )
    flag: Optional[VegaFlag] = Field(
        default=None,
        description="MISSING when no scale signals were present in the transcript. "
        "INFERRED_CONFIRM on the inference field whenever it is non-None.",
    )


class NFRs(BaseModel):
    """II.8 — non-functional requirements per app.

    Each leaf field is either an extracted string, or None paired with a flag
    explaining why. Production-tier specs (SLA, multi-region) default to
    NEEDS_TECHNICAL_FOLLOWUP. RBAC and compliance may be inferred from industry
    context but always carry INFERRED_CONFIRM.
    """

    device_support: Optional[str] = Field(
        default=None,
        description="Device support requirements stated in the transcript "
        "(e.g., 'desktop only', 'iOS + web'). Default to None + NOT_STATED if absent — never assume.",
    )
    browser_support: Optional[str] = Field(
        default=None,
        description="Browser support requirements if explicitly stated. "
        "Default to None + NOT_STATED if absent.",
    )
    data_freshness: Optional[str] = Field(
        default=None,
        description="Data freshness expectation, often inferable from use-case context "
        "(e.g., 'real-time dashboard' vs 'weekly report'). When inferred, carry INFERRED_CONFIRM.",
    )
    rbac: Optional[str] = Field(
        default=None,
        description="Role-based access control requirements. Vega may infer from industry "
        "context (multi-entity hospitality almost certainly needs RBAC) — when inferred, "
        "carry INFERRED_CONFIRM. Never treat as locked.",
    )
    sla: Optional[str] = Field(
        default=None,
        description="Uptime / SLA requirements. Almost always None at discovery time — "
        "carry NEEDS_TECHNICAL_FOLLOWUP unless explicitly stated.",
    )
    compliance: Optional[str] = Field(
        default=None,
        description="Compliance regimes (HIPAA, SOC2, SOX-adjacent, etc.). May be inferred "
        "from industry — carry INFERRED_CONFIRM when inferred, NEEDS_TECHNICAL_FOLLOWUP for depth.",
    )
    multi_region: Optional[str] = Field(
        default=None,
        description="Multi-region / data-residency requirements. "
        "Carry NEEDS_TECHNICAL_FOLLOWUP unless explicitly stated.",
    )
    audit_logging: Optional[str] = Field(
        default=None,
        description="Audit logging requirements — who logs what, retention, immutability. "
        "Production-tier cost driver. Carry NEEDS_TECHNICAL_FOLLOWUP unless stated.",
    )
    performance_budgets: Optional[str] = Field(
        default=None,
        description="Concrete latency / throughput targets: 'p95 page load < 2s', "
        "'list view < 1s for 10k rows'. Drives caching, pagination, and DB design.",
    )
    flags: list[VegaFlag] = Field(
        default_factory=list,
        description="Per-leaf-field flag bag. The renderer pairs each None field with "
        "its flag at display time — see the contract doc for the matching rules.",
    )


class DecisionBranch(BaseModel):
    """One branch out of a decision point. Mirrors v2's decision_point.branches."""

    condition: str = Field(
        ...,
        description="The condition this branch represents (e.g., 'amount > $10k', "
        "'customer is enterprise tier').",
    )
    leads_to_step_id: Optional[str] = Field(
        default=None,
        description="The step ID this branch flows into. None when the BPMN gateway "
        "leads to an end event rather than a step.",
    )


class DecisionPoint(BaseModel):
    """A BPMN gateway in a workflow's process_steps. Carried forward from v2."""

    id: str = Field(..., description="Stable decision-point ID, e.g., 'd1', 'd2'.")
    name: str = Field(..., description="Human-readable name of the decision (e.g., 'amount threshold check').")
    description: Optional[str] = Field(
        default=None,
        description="Plain-language description of what this gateway is deciding.",
    )
    after_step_id: Optional[str] = Field(
        default=None,
        description="The step ID this gateway evaluates after. None for entry-level gateways.",
    )
    type: Literal["exclusive", "parallel", "inclusive"] = Field(
        ...,
        description="BPMN gateway type. Literal[str] survives Gemini schema export; an enum class would not.",
    )
    branches: list[DecisionBranch] = Field(
        default_factory=list,
        description="The outgoing branches of this gateway. At least two for exclusive/inclusive.",
    )
    inferred: bool = Field(
        default=False,
        description="True when Vega inferred the gateway from context rather than explicit statement. "
        "Renderer pairs with INFERRED_CONFIRM badge.",
    )


class ProcessStep(BaseModel):
    """A BPMN-shaped step inside Workflow.process_steps. Carried forward from v2."""

    id: str = Field(..., description="Stable step ID, e.g., 's1', 's2'.")
    name: str = Field(..., description="Short, action-oriented step name (e.g., 'extract line items').")
    actor_id: Optional[str] = Field(
        default=None,
        description="ID of the actor performing this step (human / ai / external_system).",
    )
    task_type: Optional[Literal["user", "service", "business_rule", "manual"]] = Field(
        default=None,
        description="BPMN task type. Literal[str] union — Gemini schema export handles this cleanly.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Plain-language description of what happens in this step. "
        "Prefix with '[INFERRED]' when Vega inferred the step from missing context.",
    )
    inferred: bool = Field(
        default=False,
        description="Mirror of the [INFERRED] prefix as a structured boolean for the renderer.",
    )


class MMIDimension(BaseModel):
    """One of the six MMI dimensions. Mirrors v2 _MMI_DIM_SCHEMA."""

    value: int = Field(
        ...,
        description="1-5 score on this dimension. v2 used integer with no Pydantic ge/le bound "
        "because Gemini schema export drops constraint bounds. Validation done downstream.",
    )
    confidence: Literal["high", "medium", "low"] = Field(
        ...,
        description="Confidence in the score. 'low' confidence dimensions are added to needs_client_input.",
    )
    reasoning: str = Field(
        ...,
        description="Short justification for the score, ideally referencing transcript evidence.",
    )


class MMIHypothesis(BaseModel):
    """Full MMI breakdown attached to a Workflow. Carried forward from v2 mmi_hypothesis."""

    tss: MMIDimension = Field(..., description="Task structure / standardization dimension (1-5).")
    ie: MMIDimension = Field(..., description="Information environment / data accessibility dimension (1-5).")
    time: MMIDimension = Field(..., description="Time-savings potential dimension (1-5).")
    cost: MMIDimension = Field(..., description="Cost-savings potential dimension (1-5).")
    quality: MMIDimension = Field(..., description="Quality / error-rate improvement dimension (1-5).")
    satisfaction: MMIDimension = Field(..., description="Stakeholder-satisfaction-lift dimension (1-5).")
    needs_client_input: list[str] = Field(
        default_factory=list,
        description="Dimensions whose confidence was 'low' — questions to resolve them in the next call.",
    )
    score: Optional[float] = Field(
        default=None,
        description="Computed MMI composite score. Stage 4 fills this from the scoring helper; "
        "the LLM does not return it.",
    )


# ──────────────────────────────────────────────────────────────────────
# Workflow shapes — skeleton, detail, merged
# ──────────────────────────────────────────────────────────────────────


class WorkflowSkeleton(BaseModel):
    """Stage 1 emits one of these per workflow.

    Intentionally minimal: just enough for the renderer's Analyzing state
    and for Stage 2a/2b dispatch fan-out. NO acceptance criteria, NO
    process steps, NO persona depth here — those land in WorkflowDetail.
    """

    workflow_id: str = Field(
        ...,
        description="Stable workflow identifier (e.g., 'wf_invoice_intake'). Carried through all stages — "
        "v2's same workflow_id convention; do not regenerate at detail-fill time.",
    )
    workflow_name: str = Field(..., description="Human-readable workflow name.")
    complexity: Optional[Literal["S", "M", "L", "XL"]] = Field(
        default=None,
        description="II.7 complexity scoring — same S/M/L/XL the workflow side already uses.",
    )
    mmi_dims: dict[str, int] = Field(
        default_factory=dict,
        description="Stage 1 emits just the integer dim values per MMI dimension "
        "(tss/ie/time/cost/quality/satisfaction). Full MMIHypothesis is built in Stage 2a "
        "from Stage 1's seed values + transcript depth.",
    )
    source: SourceProvenance = Field(
        ...,
        description="Where in the transcript this workflow was identified.",
    )
    linked_app_ids: list[str] = Field(
        default_factory=list,
        description="App IDs this workflow runs through. Must be a subset of the App IDs in "
        "the same skeleton-call response. Empty list = workflow currently runs purely manually.",
    )


class WorkflowDetail(BaseModel):
    """Stage 2a fills these fields on top of a WorkflowSkeleton.

    Persona depth is normally a pointer (lookup) into the linked App; we still
    allow inline overrides per workflow for the rare case where the personas
    differ within a single app.
    """

    acceptance_criteria: list[AcceptanceCriterion] = Field(
        default_factory=list,
        description="II.1 — one per core flow in the workflow.",
    )
    decision_logic: list[DecisionPoint] = Field(
        default_factory=list,
        description="Decision gateways referenced by process_steps.",
    )
    process_steps: list[ProcessStep] = Field(
        default_factory=list,
        description="BPMN-shaped step list for this workflow. Stage 3 (BPMN gen) reads this "
        "to produce bpmn_xml; the renderer can also display it directly without the diagram.",
    )
    persona_depth_app_id: Optional[str] = Field(
        default=None,
        description="App ID whose AppDetail.personas should be displayed under this workflow. "
        "Defaults to the first linked_app_id when None.",
    )
    persona_overrides: list[PersonaRow] = Field(
        default_factory=list,
        description="Workflow-specific personas that differ from the linked app's persona table. "
        "Empty list = inherit from linked app.",
    )
    mmi_hypothesis: Optional[MMIHypothesis] = Field(
        default=None,
        description="Six-dimension MMI scoring with transcript-grounded reasoning per dim. "
        "Stage 2a is where MMI gets real values — Stage 1's seed is just a placeholder. "
        "Optional only so that older transcripts that fail to surface MMI evidence still "
        "merge cleanly; the orchestrator falls back to the skeleton seed if absent.",
    )


class Workflow(BaseModel):
    """The merged final shape: skeleton + detail + Stage 3 BPMN/design fields.

    This is what lives inside ``DiscoveryAnalysisV3.workflows`` after the full
    pipeline completes. Stage 1 produces a WorkflowSkeleton; Stage 2a merges
    a WorkflowDetail; Stage 3 attaches BPMN; if any of 2a/3 fails, the
    extraction-failed flags are set and downstream data stays None.
    """

    # Skeleton fields (Stage 1) ─────────────────────────────────────
    workflow_id: str = Field(..., description="Stable workflow identifier — see WorkflowSkeleton.")
    workflow_name: str = Field(..., description="Human-readable workflow name.")
    complexity: Optional[Literal["S", "M", "L", "XL"]] = Field(
        default=None, description="II.7 complexity score."
    )
    source: SourceProvenance = Field(..., description="Provenance of the workflow itself.")
    linked_app_ids: list[str] = Field(
        default_factory=list,
        description="Apps this workflow runs through.",
    )

    # Detail fields (Stage 2a) ──────────────────────────────────────
    acceptance_criteria: list[AcceptanceCriterion] = Field(
        default_factory=list, description="II.1 acceptance criteria per core flow."
    )
    decision_logic: list[DecisionPoint] = Field(
        default_factory=list, description="BPMN gateways for this workflow."
    )
    process_steps: list[ProcessStep] = Field(
        default_factory=list, description="BPMN-shaped step list."
    )
    persona_depth_app_id: Optional[str] = Field(
        default=None,
        description="App ID whose persona table is displayed under this workflow.",
    )
    persona_overrides: list[PersonaRow] = Field(
        default_factory=list, description="Workflow-specific persona overrides."
    )

    # MMI (Stage 2a fills full breakdown; Stage 4 fills score) ─────
    mmi_hypothesis: MMIHypothesis = Field(
        ...,
        description="Full MMI breakdown — six dimensions with reasoning + needs_client_input. "
        "Carries forward v2's mmi_hypothesis name and shape.",
    )

    # Stage 3 — BPMN fields, names carried verbatim from v2 ─────────
    bpmn_id: Optional[str] = Field(
        default=None,
        description="Foreign key into the bpmn_diagrams collection. None when bpmn_status is "
        "'pending' or 'failed'.",
    )
    bpmn_xml: Optional[str] = Field(
        default=None,
        description="Inline BPMN 2.0 XML for this workflow. II.10 requires the substance to be "
        "embedded in the artifact, not just referenced by ID — Stage 3 sets both bpmn_id and bpmn_xml.",
    )
    bpmn_status: Optional[Literal["pending", "generated", "failed"]] = Field(
        default=None,
        description="Lifecycle marker carried forward from v2. 'failed' means the diagram "
        "could not be generated; the renderer shows a placeholder.",
    )
    bpmn_content_hash: Optional[str] = Field(
        default=None,
        description="Hash of the BPMN XML for cache invalidation — name carried from v2.",
    )

    # Stage 3 — AI Design Document (inline blob) ─────────────────────
    design_doc: Optional[dict] = Field(
        default=None,
        description="Inline AI Design Document dict for this workflow, generated by Stage 3. "
        "Self-contained in the artifact per Samson's requirement. None when generation failed "
        "or was skipped (partial_extraction_failed). Structure mirrors v2 _generate_single_design_doc output.",
    )
    design_doc_status: Optional[Literal["pending", "generated", "failed"]] = Field(
        default=None,
        description="Lifecycle marker for the workflow design doc, mirroring App.design_doc_status. "
        "'failed' means Stage 3 could not produce the doc; the renderer shows a placeholder. "
        "None when no design doc applies (e.g., upstream Stage 2 partial failure).",
    )

    # Failure handling (Stage 4 marks this) ─────────────────────────
    partial_extraction_failed: bool = Field(
        default=False,
        description="True when Stage 2a or Stage 3 failed for this workflow. "
        "Skeleton fields remain populated; detail fields may be empty.",
    )
    partial_extraction_reason: Optional[str] = Field(
        default=None,
        description="Human-readable reason set when partial_extraction_failed is True "
        "(e.g., 'stage 2a timed out after 180s + 1 retry').",
    )

    # Wave 5 — regenerate-from-open-items accumulator ────────────────
    user_provided_answers: dict[str, str] = Field(
        default_factory=dict,
        description="Answers manually supplied by the user during regeneration. Keys are "
        "OpenItem.label slugs (or the answered field name); values are the user's text. "
        "These persist across regenerations and are passed back into the LLM prompt as "
        "additional context so subsequent re-extractions don't re-flag the same gap. "
        "Strict accumulator — never overwritten by a later LLM rerun. "
        "Example: {'invoice_volume': '~5000/month from QuickBooks'}.",
    )


# ──────────────────────────────────────────────────────────────────────
# App shapes — skeleton, detail, merged
# ──────────────────────────────────────────────────────────────────────


class AppSkeleton(BaseModel):
    """Stage 1 emits one of these per app discovered in the transcript."""

    app_id: str = Field(
        ...,
        description="Stable app identifier (e.g., 'app_field_dispatch'). Carries through all stages.",
    )
    app_name: str = Field(..., description="Human-readable app name.")
    complexity: Optional[Literal["S", "M", "L", "XL"]] = Field(
        default=None,
        description="II.7 — mirror the workflow-side complexity score onto apps.",
    )
    build_intent: Literal["build_for_customer", "existing_tool", "unclear"] = Field(
        default="build_for_customer",
        description=(
            "Distinguishes apps the customer wants US to build "
            "(``build_for_customer``) from existing tools they already use "
            "and just mentioned in passing (``existing_tool`` — e.g., "
            "Google Sheets, Salesforce, Slack). Existing tools belong in "
            "``integrations``, not as standalone apps. Only "
            "``build_for_customer`` apps get an EADD generated in Stage 3."
        ),
    )
    source: SourceProvenance = Field(
        ...,
        description="II.9 — the transcript moment / pain point that originated this app. "
        "Set source_flag=SOURCE_UNCLEAR if Vega can't pin one.",
    )
    linked_workflow_ids: list[str] = Field(
        default_factory=list,
        description="Workflow IDs this app supports. Empty list means the app is standalone — "
        "the renderer puts it under the 'Standalone Apps' tab. Stage 4 fills back-links if a "
        "workflow says 'this happens via App X' but X arrived without the workflow listed.",
    )


class AppDetail(BaseModel):
    """Stage 2b fills these on top of the skeleton."""

    personas: list[PersonaRow] = Field(
        default_factory=list,
        description="II.6 — persona table for this app. Workflows linked to this app point at "
        "this list via persona_depth_app_id unless they override.",
    )
    integrations: list[IntegrationEntry] = Field(
        default_factory=list,
        description="II.4 — three-bucket integration list for this app.",
    )
    scale_profile: ScaleProfile = Field(
        default_factory=ScaleProfile,
        description="II.5 — scale proxy signals + inference for this app.",
    )
    nfrs: NFRs = Field(
        default_factory=NFRs,
        description="II.8 — non-functional requirements for this app.",
    )
    additional_source_quotes: list[SourceProvenance] = Field(
        default_factory=list,
        description="Extra transcript citations that enriched this app's understanding "
        "beyond the primary AppSkeleton.source.",
    )


class App(BaseModel):
    """Final merged App shape: skeleton + detail + optional design-doc fields."""

    # Skeleton (Stage 1) ────────────────────────────────────────────
    app_id: str = Field(..., description="Stable app identifier.")
    app_name: str = Field(..., description="Human-readable app name.")
    complexity: Optional[Literal["S", "M", "L", "XL"]] = Field(
        default=None, description="II.7 complexity score mirrored from workflow side."
    )
    build_intent: Literal["build_for_customer", "existing_tool", "unclear"] = Field(
        default="build_for_customer",
        description=(
            "Whether the customer wants this app built or already uses it. "
            "Stage 3 only generates EADDs for build_for_customer apps; the "
            "renderer hides the EADD panel otherwise."
        ),
    )
    source: SourceProvenance = Field(..., description="II.9 provenance for the app itself.")
    linked_workflow_ids: list[str] = Field(
        default_factory=list,
        description="Workflows this app supports. Empty = standalone app.",
    )

    # Detail (Stage 2b) ─────────────────────────────────────────────
    personas: list[PersonaRow] = Field(default_factory=list, description="II.6 persona table.")
    integrations: list[IntegrationEntry] = Field(
        default_factory=list, description="II.4 three-bucket integration list."
    )
    scale_profile: ScaleProfile = Field(
        default_factory=ScaleProfile, description="II.5 scale proxy signals."
    )
    nfrs: NFRs = Field(default_factory=NFRs, description="II.8 non-functional requirements.")
    additional_source_quotes: list[SourceProvenance] = Field(
        default_factory=list, description="Extra transcript citations."
    )
    novel_ui_patterns: list[str] = Field(
        default_factory=list,
        description="UI patterns this app needs that are NOT off-the-shelf component-library "
        "fare. Examples: 'split-screen confidence-review with side-by-side AI vs source', "
        "'Gantt-style scheduling board with drag-resolve conflicts', 'real-time collab cursor "
        "on shared whiteboard'. Empty list = standard CRUD UI suffices. Drives sizing.",
    )
    custom_logic: list[str] = Field(
        default_factory=list,
        description="Business-logic items that need bespoke code beyond CRUD: "
        "'compute commission splits across 4-tier referral chain', 'apply tax-jurisdiction "
        "rules per ZIP', 'route exceptions by confidence band + SLA tier'. "
        "Each entry is one named rule. Empty list = standard CRUD logic suffices.",
    )
    pain_point_refs: list[str] = Field(
        default_factory=list,
        description="Direct pain-point quotes or paraphrases this app addresses, pulled from "
        "the transcript verbatim where possible. Different from app.source.quote (one-liner) "
        "and from linked workflows' current_state.pain_points (workflow-scoped). The build "
        "team uses this list when scope is questioned: 'we built this because the customer "
        "said X'. Empty list when no specific pain points were tied to this app.",
    )

    # Stage 3 design-doc fields (optional — only on apps tied to a generated design) ─
    design_doc_id: Optional[str] = Field(
        default=None,
        description="ID of the AI Design Document attached to this app (when the app is "
        "linked to a workflow that has a generated design). None for standalone apps.",
    )
    design_doc_status: Optional[Literal["pending", "generated", "failed"]] = Field(
        default=None,
        description="Lifecycle marker for the design doc. None when no design doc applies.",
    )
    eadd_doc: Optional[dict] = Field(
        default=None,
        description="Inline Emergent App Design Document dict generated by Stage 3. "
        "Self-contained in the artifact per Samson's requirement. None when generation "
        "failed or was skipped. Structure mirrors v2 _generate_single_eadd output.",
    )

    # Failure handling ──────────────────────────────────────────────
    partial_extraction_failed: bool = Field(
        default=False,
        description="True when Stage 2b failed for this app. Skeleton fields remain populated.",
    )
    partial_extraction_reason: Optional[str] = Field(
        default=None,
        description="Human-readable reason for partial_extraction_failed.",
    )

    # Wave 5 — regenerate-from-open-items accumulator ────────────────
    user_provided_answers: dict[str, str] = Field(
        default_factory=dict,
        description="Answers manually supplied by the user during regeneration. Keys are "
        "OpenItem.label slugs (or the answered field name); values are the user's text. "
        "These persist across regenerations and are passed back into the LLM prompt as "
        "additional context so subsequent re-extractions don't re-flag the same gap. "
        "Strict accumulator — never overwritten by a later LLM rerun. "
        "Example: {'rbac_roles': 'manager and field-tech only — no end-customer access'}.",
    )


# ──────────────────────────────────────────────────────────────────────
# Session-level shapes
# ──────────────────────────────────────────────────────────────────────


class SessionFlags(BaseModel):
    """II.3 — top-of-session technical flags. Stage 1 fills all three.

    Each field is a value + a flag pair; the value is what the LLM extracted
    (None when not present), the flag explains the absence/state for the renderer.
    """

    tech_contact: Optional[str] = Field(
        default=None,
        description="Name of the customer-side technical contact for integrations. "
        "None if no name was mentioned — pair with tech_contact_flag=MISSING.",
    )
    tech_contact_flag: Optional[VegaFlag] = Field(
        default=None,
        description="MISSING when no technical contact name was identifiable in the transcript.",
    )
    engineering_capacity: Optional[bool] = Field(
        default=None,
        description="True/False/None for whether the customer has internal engineering capacity. "
        "None when not deducible from transcript — pair with engineering_capacity_flag=MISSING.",
    )
    engineering_capacity_flag: Optional[VegaFlag] = Field(
        default=None,
        description="MISSING when transcript does not state engineering capacity either way.",
    )
    it_infosec_gate: Optional[bool] = Field(
        default=None,
        description="True/False/None for whether an IT/infosec approval gate exists. "
        "None when not discussed — pair with it_infosec_gate_flag=NOT_DISCUSSED.",
    )
    it_infosec_gate_flag: Optional[VegaFlag] = Field(
        default=None,
        description="NOT_DISCUSSED when transcript contains no mention of IT approval, "
        "security review, or access controls.",
    )


class SharedInfrastructureRow(BaseModel):
    """II.2 — one row in the cross-app shared infrastructure table.

    Stage 4 post-processing scans App.integrations across all apps and emits
    a row whenever the same system_name appears in 2+ apps. The renderer only
    shows this section when the artifact contains 2+ apps total.
    """

    system_name: str = Field(
        ...,
        description="The integration / data source that appears in multiple apps "
        "(e.g., 'NetSuite', 'shared customer DB').",
    )
    used_by_app_ids: list[str] = Field(
        default_factory=list,
        description="App IDs that reference this system in their integrations list.",
    )
    read_write: Optional[Literal["read", "write", "read_write", "unknown"]] = Field(
        default=None,
        description="Whether the apps read, write, or both. 'unknown' when notes don't disambiguate. "
        "Prefer the structured IntegrationEntry.read_write when present; fall back to notes-keyword "
        "inference otherwise.",
    )
    auth_method: Optional[str] = Field(
        default=None,
        description="Authentication method aggregated across the apps (when consistent). "
        "Surfaced so the build team coordinates one auth setup instead of duplicating it per app.",
    )
    access_status: Optional[Literal["already_provisioned", "needs_setup", "mixed", "unknown"]] = Field(
        default=None,
        description="Provisioning status across the apps using this system. 'mixed' when one app "
        "already has access but another doesn't — flags a sequencing risk for the build team.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Free-text on shared vs duplicated data, sync direction, etc.",
    )


class OpenItem(BaseModel):
    """An open question / follow-up flagged for human resolution.

    Stage 4 walks every flagged field across the artifact and emits one
    OpenItem per flag, with location pointers so the renderer's Review tab
    can deep-link back to the source.

    NOTE on III.1: every NEEDS_TECHNICAL_FOLLOWUP-tagged item will eventually
    grow an `assigned_owner: str | None` field once Ryan picks A/B/C from the
    requirements doc. For now, the field is intentionally absent — adding it
    later is non-breaking. See contract doc for details.
    """

    flag: VegaFlag = Field(..., description="The flag classification for this item.")
    label: str = Field(
        ...,
        description="Short label for display (e.g., 'Acceptance criterion missing for invoice intake flow').",
    )
    location: str = Field(
        ...,
        description="Pointer string with stable format: 'session', 'workflow:<workflow_id>', "
        "'app:<app_id>', or 'shared_infra'. The renderer parses the prefix to deep-link.",
    )
    detail: Optional[str] = Field(
        default=None,
        description="Extra context — what's missing, what needs to be confirmed, or the "
        "specific question the customer should answer.",
    )


class TranscriptRef(BaseModel):
    """Reference to one source transcript inside the artifact."""

    filename: str = Field(..., description="Original filename of the transcript.")
    duration_minutes: Optional[float] = Field(
        default=None,
        description="Duration of the underlying call in minutes. None when unknown.",
    )
    attendees: list[str] = Field(
        default_factory=list,
        description="Names of people on the call, when extractable from transcript metadata.",
    )
    captured_at: Optional[str] = Field(
        default=None,
        description="ISO 8601 timestamp of when the call was held. String (not datetime) so "
        "Gemini schema export stays clean — orchestrator parses if needed.",
    )


class PipelineMetadata(BaseModel):
    """Telemetry the renderer doesn't display but exports / audit hooks consume."""

    stage_1_duration_ms: Optional[int] = Field(
        default=None, description="Wall time for the skeleton call in ms."
    )
    stage_2_duration_ms: Optional[int] = Field(
        default=None,
        description="Wall time for Stage 2a + 2b combined (max of the parallel fan-out, not sum).",
    )
    stage_3_duration_ms: Optional[int] = Field(
        default=None, description="Wall time for BPMN + design-doc generation in ms."
    )
    stage_4_duration_ms: Optional[int] = Field(
        default=None,
        description="Wall time for Stage 4 post-processing (shared infra detection + open-items roll-up) in ms.",
    )
    total_duration_ms: Optional[int] = Field(
        default=None,
        description="End-to-end wall time from session start to artifact-ready, in ms.",
    )
    call_mode: Optional[Literal["call1", "call2", "call3", "single_call"]] = Field(
        default=None,
        description="Which entry mode produced this artifact. None for legacy v2 artifacts; "
        "'single_call' for chip-driven full-pipeline runs (current default); 'call1'/'call2'/'call3' "
        "for conversational-mode per-call analyses produced by run_v3_call1/2/3 in v3_call_modes.py. "
        "Renderer (Wave 6c) gates BPMN/design-doc/Standalone-Apps tabs on this field.",
    )


# ──────────────────────────────────────────────────────────────────────
# Top-level artifact
# ──────────────────────────────────────────────────────────────────────


class DiscoveryAnalysisV3(BaseModel):
    """The v3 Vega discovery artifact — the payload the renderer consumes.

    Pipeline summary:
      - Stage 1 fills: client_company, source_transcripts, session_flags,
        and the *skeleton* portions of workflows[] and apps[].
      - Stage 2a fills the *detail* portions of workflows[] (per-workflow
        fan-out, parallel).
      - Stage 2b fills the *detail* portions of apps[] (per-app fan-out, parallel).
      - Stage 3 attaches bpmn_id/bpmn_xml/bpmn_status/bpmn_content_hash to
        workflows and design-doc IDs to apps.
      - Stage 4 (post-processing) populates shared_infrastructure[],
        open_items[], extraction_status, and pipeline_metadata.
    """

    version: Literal["v3"] = Field(
        default="v3",
        description="Schema version sentinel. Renderer & exports branch on this.",
    )
    client_company: str = Field(
        ...,
        description="Client/company name. v2 used 'client_name'; v3 renames to "
        "'client_company' to disambiguate from contact-name fields elsewhere in AEGISAXIOM.",
    )
    source_transcripts: list[TranscriptRef] = Field(
        default_factory=list,
        description="One entry per transcript that fed this artifact.",
    )
    session_flags: SessionFlags = Field(
        default_factory=SessionFlags,
        description="II.3 top-of-session technical flags.",
    )
    workflows: list[Workflow] = Field(
        default_factory=list,
        description="All workflows for this discovery, fully merged (skeleton + detail + BPMN).",
    )
    apps: list[App] = Field(
        default_factory=list,
        description="All apps for this discovery. Apps with linked_workflow_ids == [] are "
        "standalone; the renderer surfaces them under the 'Standalone Apps' tab (II.10).",
    )
    shared_infrastructure: list[SharedInfrastructureRow] = Field(
        default_factory=list,
        description="II.2 — only populated when 2+ apps share an integration. Stage 4 fills this.",
    )
    open_items: list[OpenItem] = Field(
        default_factory=list,
        description="Aggregated flagged items for the renderer's Review tab. Stage 4 fills this.",
    )
    extraction_status: Literal["complete", "partial", "failed"] = Field(
        default="complete",
        description="'partial' when any per-workflow/per-app call failed and was marked "
        "partial_extraction_failed. 'failed' when Stage 1 itself returned no usable skeleton.",
    )
    pipeline_metadata: PipelineMetadata = Field(
        default_factory=PipelineMetadata,
        description="Stage timings — telemetry only, never shown in the renderer UI.",
    )
    prior_call_artifact_id: Optional[str] = Field(
        default=None,
        description="The artifact_id of the prior call's analysis when this is a Call 2 / Call 3 "
        "follow-up produced by the conversational dispatcher. Establishes the audit chain "
        "(Call 1 -> Call 2 -> Call 3). None for Call 1, single_call, or legacy artifacts. "
        "Wave 6 owns this field; Wave 6c renderer surfaces a link back to the prior analysis "
        "when present.",
    )
    contradictions_resolved: list[dict] = Field(
        default_factory=list,
        description="Call-3-only audit trail. Each entry is "
        "{'topic': str, 'prior_understanding': str, 'resolved_to': str} describing a "
        "concept the LLM revised vs the prior Call 2 artifact. Empty list for Call 1 / "
        "Call 2 / single_call. Mirrors v2's contradictions_resolved on the AI Employee Matrix.",
    )
