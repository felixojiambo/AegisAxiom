"""Vega v3 — per-call-mode prompt + schema variants (Wave 6 / Thread 6a).

This module owns the *trimmed* prompt + schema variants used when Vega's
conversational dispatcher invokes a per-call analysis (run_v3_call1 /
run_v3_call2 / run_v3_call3 in services.vega.v3_call_modes).

Why a separate file?
  - The full v3 Stage 2 prompts in ``detail_schemas.py`` weigh ~750 lines
    already. Folding three more variants in there would obscure which
    prompt is "the canonical full v3 prompt" vs which is "the lighter
    Call 1 trim".
  - Each call mode is a distinct contract with the LLM. The Wave 6 plan
    explicitly carves Call 1 / Call 2 / Call 3 lighter shapes; keeping
    them isolated makes future tweaks (e.g., trim Call 1 further) a
    one-file edit.

Mode summary:
    Call 1  - WORKFLOW_DETAIL_SCHEMA_CALL1, WORKFLOW_DETAIL_PROMPT_CALL1
              APP_DETAIL_SCHEMA_CALL1,      APP_DETAIL_PROMPT_CALL1
              No MMI hypothesis, no process_steps, no decision_logic,
              no persona_overrides, no NFRs, no additional_source_quotes.
    Call 2  - reuses canonical WORKFLOW_DETAIL_SCHEMA / WORKFLOW_DETAIL_PROMPT
              and APP_DETAIL_SCHEMA / APP_DETAIL_PROMPT verbatim, plus an
              optional prior-call merge block via build_prior_call_block().
    Call 3  - reuses canonical schemas; adds CALL3_CONTRADICTION_BLOCK to
              instruct the LLM to resolve contradictions vs the prior Call 2
              artifact and emit them as a structured list. The orchestrator
              extracts that list into DiscoveryAnalysisV3.contradictions_resolved.

The Call 1 schemas are deliberately separate dicts (not just "trimmed
prompts over the canonical schema") so that Gemini's structured-output
guard never asks the LLM to emit an MMI score when we don't want one —
the LLM cannot fill a field that isn't in the response schema.
"""

from __future__ import annotations

from typing import Optional

from services.vega.detail_schemas import (
    _ACCEPTANCE_CRITERION_SCHEMA,
    _VEGA_FLAG_DESC,
    _FLAG_TOKEN_LINES,
    _INTEGRATION_ENTRY_SCHEMA,
    _PERSONA_ROW_SCHEMA,
    _SCALE_PROFILE_SCHEMA,
    _SOURCE_PROVENANCE_SCHEMA,
)


# ──────────────────────────────────────────────────────────────────────
# Call 1 — workflow detail (trimmed)
# ──────────────────────────────────────────────────────────────────────

WORKFLOW_DETAIL_SCHEMA_CALL1: dict = {
    "type": "object",
    "description": (
        "Vega v3 Call 1 per-workflow detail. THIS IS THE LIGHTER VARIANT — "
        "first-call discovery only. Fill acceptance criteria + a primary persona "
        "pointer for ONE workflow. Do NOT fill MMI hypothesis, process_steps, "
        "decision_logic, or persona_overrides — those are deferred to Call 2. "
        "Do NOT re-emit skeleton fields (workflow_id, workflow_name, complexity, "
        "source, linked_app_ids)."
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
        "persona_depth_app_id": {
            "type": "string",
            "description": (
                "App ID whose AppDetail.personas should be displayed under this workflow. "
                "Set to the primary linked app ID when the workflow's users are covered by "
                "that app's persona table."
            ),
        },
    },
    "required": ["acceptance_criteria"],
}


WORKFLOW_DETAIL_PROMPT_CALL1: str = """\
You are Vega, the AI Recruiter Agent. You are running **Call 1 (lighter v3 \
detail)** of the discovery pipeline — the per-workflow first-call pass for \
workflow **{workflow_id}** ({workflow_name}).

This is the FIRST customer call — surface-level discovery only. The goal \
is to capture the bones of what this customer's day looks like. We will \
deepen process steps, decision logic, and MMI scoring on the next call.

## What you MUST fill

1. ``acceptance_criteria`` — one entry per core flow inside this workflow.
   A "core flow" is a distinct path through the workflow (e.g., "invoice \
   intake", "exception handling", "approval routing"). Each entry needs:
   - ``flow_name``: the name of the flow
   - ``success_when``: plain English — "This flow succeeds when [X]."
   Flag MISSING when no success definition was stated in the transcript.
   Flag INFERRED_CONFIRM when you inferred success from the customer's \
   goals or pain rather than a direct statement.

2. ``persona_depth_app_id`` — set this to the app ID from the skeleton's \
   linked_app_ids that covers the users of this workflow. When this workflow \
   has no linked app (linked_app_ids is empty), omit this field.

## What you MUST NOT do (Call 1 is lighter — these come in Call 2)

- Do NOT emit ``mmi_hypothesis`` — MMI scoring is Call 2's job.
- Do NOT emit ``process_steps`` — process steps are Call 2's job.
- Do NOT emit ``decision_logic`` — decision gateways are Call 2's job.
- Do NOT emit ``persona_overrides`` — let Stage 2b's app personas \
  speak for the workflow's users at this stage.
- Do NOT re-emit workflow_id, workflow_name, complexity, source, or \
  linked_app_ids — those are already in the artifact from Stage 1.
- Do NOT emit BPMN XML — Stage 3 does not run on Call 1.
- Do NOT regenerate or change any IDs that Stage 1 already assigned.

## Workflow skeleton for context

```json
{skeleton_json}
```

## Flag tokens

Emit exactly one of these six values in any flag field you populate:
__FLAG_TOKENS__

Flag rules for Call 1 fields:
  - ``AcceptanceCriterion.flag``: MISSING when no success definition found, \
    INFERRED_CONFIRM when you inferred it from goals/pain.
  - ``SourceProvenance.source_flag``: SOURCE_UNCLEAR when you cannot pin \
    content to a transcript moment.

Return strictly the structured JSON described by the response schema. No \
free-text preamble, no markdown.

{prior_answers_block}

<transcript>
{transcript_text}
</transcript>
"""

WORKFLOW_DETAIL_PROMPT_CALL1 = WORKFLOW_DETAIL_PROMPT_CALL1.replace(
    "__FLAG_TOKENS__", _FLAG_TOKEN_LINES
)


# ──────────────────────────────────────────────────────────────────────
# Call 1 — app detail (trimmed)
# ──────────────────────────────────────────────────────────────────────

APP_DETAIL_SCHEMA_CALL1: dict = {
    "type": "object",
    "description": (
        "Vega v3 Call 1 per-app detail. THIS IS THE LIGHTER VARIANT — "
        "first-call discovery only. Fill personas, integrations, and scale "
        "profile for ONE app. Do NOT fill NFRs or additional_source_quotes — "
        "those are deferred to Call 2. "
        "Do NOT re-emit skeleton fields (app_id, app_name, complexity, source, "
        "linked_workflow_ids). Do NOT add acceptance criteria, decision logic, "
        "or process steps — those live on workflows only."
    ),
    "properties": {
        "personas": {
            "type": "array",
            "items": _PERSONA_ROW_SCHEMA,
            "description": (
                "II.6 — one entry per distinct user persona for this app. "
                "Workflows linked to this app inherit this list."
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
    },
    "required": ["personas", "integrations"],
}


APP_DETAIL_PROMPT_CALL1: str = """\
You are Vega, the AI Recruiter Agent. You are running **Call 1 (lighter v3 \
detail)** of the discovery pipeline — the per-app first-call pass for app \
**{app_id}** ({app_name}).

This is the FIRST customer call — surface-level discovery only. NFRs and \
deeper source quote work are deferred to Call 2.

## What you MUST fill

1. ``personas`` — one entry per distinct user type for this app.
   Include role, count (if stated), device (if stated), and technical_literacy \
   (inferred — always carry INFERRED_CONFIRM in flags). If no personas were \
   discussed, return a single entry with role="unknown" and \
   flags=["MISSING"].

2. ``integrations`` — three-bucket classification for every system this app \
   reads from or writes to:
   - Bucket 1: pre-research (well-known platform you know from training data)
   - Bucket 2: transcript-confirmed (customer mentioned actively using it)
   - Bucket 3: needs technical follow-up (auth method, API tier, rate limits)
   Bucket 3 entries should carry NEEDS_TECHNICAL_FOLLOWUP in flags. \
   Bucket 1 entries inferred from industry context carry INFERRED_CONFIRM.

3. ``scale_profile`` — extract any proxy signals: transaction volume, \
   retention window, growth trajectory, or speed/storage pain statements. \
   Set inference to your order-of-magnitude conclusion if you have enough \
   signals (e.g. "normal app database is sufficient" vs "data lake likely \
   required"). Flag MISSING on the top-level flag field when no scale signals \
   were present.

## What you MUST NOT do (Call 1 is lighter — these come in Call 2)

- Do NOT emit ``nfrs`` — non-functional requirements are Call 2's job.
- Do NOT emit ``additional_source_quotes`` — secondary citations are \
  Call 2's job.
- Do NOT re-emit app_id, app_name, complexity, source, or \
  linked_workflow_ids — those are already in the artifact from Stage 1.
- Do NOT add acceptance criteria, decision logic, or process steps — those \
  live on workflows only.
- Do NOT emit BPMN XML — Stage 3 does not run on Call 1.
- Do NOT regenerate or change any IDs that Stage 1 already assigned.

## App skeleton for context

```json
{skeleton_json}
```

## Flag tokens

Emit exactly one of these six values in any flag field you populate:
__FLAG_TOKENS__

Flag rules for Call 1 fields:
  - ``PersonaRow.flags``: INFERRED_CONFIRM when technical_literacy is inferred; \
    MISSING when the persona table was entirely absent from the transcript.
  - ``IntegrationEntry.flags``: NEEDS_TECHNICAL_FOLLOWUP for Bucket 3; \
    INFERRED_CONFIRM for Bucket 1 entries inferred from industry.
  - ``ScaleProfile.flag``: MISSING when no scale signals were present; \
    INFERRED_CONFIRM when inference field is populated.
  - ``SourceProvenance.source_flag``: SOURCE_UNCLEAR when you cannot pin \
    content to a transcript moment.

Return strictly the structured JSON described by the response schema. No \
free-text preamble, no markdown.

{prior_answers_block}

<transcript>
{transcript_text}
</transcript>
"""

APP_DETAIL_PROMPT_CALL1 = APP_DETAIL_PROMPT_CALL1.replace(
    "__FLAG_TOKENS__", _FLAG_TOKEN_LINES
)


# ──────────────────────────────────────────────────────────────────────
# Cross-call merge — prior-artifact prompt block
# ──────────────────────────────────────────────────────────────────────


def build_prior_call_block(
    prior_artifact_summary: Optional[dict],
    prior_call_label: str,
) -> str:
    """Render the prior-call merge block for the Call 2 / Call 3 prompts.

    The orchestrator passes a *summary* of the prior artifact (not the full
    payload — would blow Gemini's context budget). Summary shape:

        {
          "client_company": str,
          "workflows": [{"workflow_id": str, "workflow_name": str, "complexity": str,
                         "acceptance_criteria_summary": str | None}],
          "apps": [{"app_id": str, "app_name": str, "complexity": str,
                    "integrations_summary": str | None}],
          "session_flags_summary": str | None,
          "open_items_summary": list[str]   # short labels only
        }

    Returns "" when ``prior_artifact_summary`` is None — preserves
    no-prior-call behaviour where the prompt formats unchanged.

    The block instructs the LLM to:
      - Carry forward the skeleton (workflows, apps, IDs) from the prior call.
      - Append new content found in the new transcript without overwriting
        existing entries.
      - Flag genuinely contradictory content with INFERRED_CONFIRM and
        record the contradiction (Call 3 only — Call 2 just appends).

    Args:
        prior_artifact_summary: Summary dict produced by
            ``v3_call_modes._summarise_artifact_for_prompt``.
        prior_call_label: Display label like "Call 1" or "Call 2" — purely
            for prompt readability so the LLM knows which call this came from.

    Returns:
        Markdown block ready for ``.format()`` substitution into the prompt,
        or "" when no prior summary exists.
    """
    if not prior_artifact_summary:
        return ""

    lines = [
        "",
        f"## Prior {prior_call_label} Analysis (carry forward, do not contradict)",
        "",
        f"You previously analyzed this customer in {prior_call_label}. The skeleton — "
        "workflows, apps, IDs, and primary source quotes — was already established "
        "and MUST be preserved. Your job in this pass is to deepen what's there, NOT "
        "to re-invent the structure.",
        "",
        "**Rules:**",
        "  - Use the same workflow_ids and app_ids the prior call produced. Do NOT "
        "re-id existing entities.",
        "  - New pain points / workflows / apps surfaced by the NEW transcript content "
        "should be ADDED — never replace prior entries.",
        "  - When the new transcript appears to contradict the prior understanding, "
        "do NOT silently overwrite. Flag the field with INFERRED_CONFIRM and continue.",
        "",
        "**Prior analysis summary:**",
        "",
        f"  - Client: `{prior_artifact_summary.get('client_company', '(unknown)')}`",
    ]

    workflows = prior_artifact_summary.get("workflows") or []
    if workflows:
        lines.append("  - Workflows already in scope:")
        for wf in workflows:
            wf_id = wf.get("workflow_id", "?")
            wf_name = wf.get("workflow_name", "?")
            ac = wf.get("acceptance_criteria_summary") or "(no acceptance criteria yet)"
            lines.append(f"    - `{wf_id}` ({wf_name}) — {ac}")

    apps = prior_artifact_summary.get("apps") or []
    if apps:
        lines.append("  - Apps already in scope:")
        for app in apps:
            app_id = app.get("app_id", "?")
            app_name = app.get("app_name", "?")
            integ = app.get("integrations_summary") or "(no integrations yet)"
            lines.append(f"    - `{app_id}` ({app_name}) — {integ}")

    sf = prior_artifact_summary.get("session_flags_summary")
    if sf:
        lines.append(f"  - Session flags: {sf}")

    open_items = prior_artifact_summary.get("open_items_summary") or []
    if open_items:
        lines.append("  - Open items the prior call flagged for follow-up:")
        for label in open_items[:10]:  # cap to keep prompt size reasonable
            lines.append(f"    - {label}")

    lines.append("")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# Call 3 — contradiction resolution prompt block
# ──────────────────────────────────────────────────────────────────────

CALL3_CONTRADICTION_BLOCK: str = """\

## Call 3: Resolve contradictions vs the prior Call 2 analysis

This is the FINAL call. The prior Call 2 hypothesis (MMI scoring, process \
steps, integrations) is your baseline. The new transcript may reveal that \
some of it was wrong. When you revise a prior conclusion, you MUST be \
explicit about what changed — silent overwrites poison the audit trail.

For every revision you make:
  1. Use the prior workflow_id / app_id — never re-invent IDs.
  2. Update the field with the corrected value.
  3. Mention the topic + prior understanding + resolved value in the \
     reasoning of any MMI dim you revise (Call 3 produces fresh MMI \
     scoring; you may carry forward Call 2's score when nothing has changed).
  4. Do not silently delete prior content — revise, don't erase. If a \
     workflow no longer applies, mark its acceptance_criteria with \
     INFERRED_CONFIRM and a note explaining why.

The orchestrator will collect contradictions resolved across the artifact \
into a structured list at ``DiscoveryAnalysisV3.contradictions_resolved`` \
based on what you write in your reasoning fields. Be precise — auditors \
read this.
"""


__all__ = [
    "WORKFLOW_DETAIL_SCHEMA_CALL1",
    "WORKFLOW_DETAIL_PROMPT_CALL1",
    "APP_DETAIL_SCHEMA_CALL1",
    "APP_DETAIL_PROMPT_CALL1",
    "build_prior_call_block",
    "CALL3_CONTRADICTION_BLOCK",
]
