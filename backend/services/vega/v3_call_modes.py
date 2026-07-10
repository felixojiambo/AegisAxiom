"""Vega v3 — per-call pipeline modes (Wave 6 / Thread 6a).

Three async wrappers over the existing ``run_v3_pipeline`` plumbing. Each
produces a lighter (or fuller) ``DiscoveryAnalysisV3`` artifact for the
conversational dispatcher's per-call decision flow:

    run_v3_call1  - first call discovery (skeleton + acceptance criteria
                    + light app detail). No MMI hypothesis, no process
                    steps, no decision logic, no Stage 3.
    run_v3_call2  - depth pass. Full Stage 1 + Stage 2 (MMI + process
                    steps + decision logic + NFRs). NO Stage 3. Optional
                    cross-call merge with a prior Call 1 artifact.
    run_v3_call3  - finalization. Full pipeline + contradiction
                    resolution against the prior Call 2 artifact.

The chip-driven legacy single-call mode (``run_v3_pipeline`` invoked
from ``ai_chat_artifacts.py``'s ``is_single_call`` branch) STAYS legacy
and is not affected by anything here. Wave 6 adds new entry points; it
never overhauls existing ones.

Cross-call merge mechanic
-------------------------

When ``prior_call1_artifact_id`` (or ``prior_call2_artifact_id``) is
supplied, the orchestrator:

  1. Looks up the prior artifact in ``ai_chat_artifacts``.
  2. Builds a SUMMARY (not full payload) of the prior artifact and
     threads it into Stage 1 + Stage 2 prompts via
     ``build_prior_call_block`` from call_mode_prompts.py.
  3. Sets ``DiscoveryAnalysisV3.prior_call_artifact_id`` to establish
     the audit chain.
  4. (Call 3 only) Adds the contradiction-resolution instruction block
     so the LLM emits structured reasoning for any revisions.

Programmatic merging is intentionally minimal — the LLM is better at
semantic merging than this code is. The helper just orchestrates the
prior-artifact lookup + prompt threading.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Literal, Optional

from services.ai.vega_analysis import (
    VEGA_LLM_TEMPERATURE,
    _load_transcripts,
    run_with_retry,
)
from services.ai.llm import call_llm
from services.vega.call_mode_prompts import (
    APP_DETAIL_PROMPT_CALL1,
    APP_DETAIL_SCHEMA_CALL1,
    CALL3_CONTRADICTION_BLOCK,
    WORKFLOW_DETAIL_PROMPT_CALL1,
    WORKFLOW_DETAIL_SCHEMA_CALL1,
    build_prior_call_block,
)
from services.vega.detail_schemas import (
    APP_DETAIL_PROMPT,
    APP_DETAIL_SCHEMA,
    WORKFLOW_DETAIL_PROMPT,
    WORKFLOW_DETAIL_SCHEMA,
)
from services.vega.models_v3 import (
    AppDetail,
    AppSkeleton,
    DiscoveryAnalysisV3,
    PipelineMetadata,
    WorkflowDetail,
    WorkflowSkeleton,
)
from services.vega.v3_pipeline import (
    V3_FANOUT_LIMIT,
    V3_LLM_TIMEOUT,
    _merge_app,
    _merge_workflow,
    _stage_1_skeleton,
    _stage_3_bpmn_and_design_docs,
    _stage_4_postprocess,
)
from services.vega_events import emit_vega_event

logger = logging.getLogger(__name__)


CallMode = Literal["call1", "call2", "call3"]


# ──────────────────────────────────────────────────────────────────────
# Public entry points — Call 1 / Call 2 / Call 3
# ──────────────────────────────────────────────────────────────────────


async def run_v3_call1(
    db,
    user,
    upload_ids: list[str],
    chat_session_id: Optional[str] = None,
    client_company: Optional[str] = None,
) -> DiscoveryAnalysisV3:
    """Call 1 / first-call discovery analysis.

    Lighter than the full v3 pipeline:
      - Full Stage 1 (skeleton: workflows, apps, session_flags).
      - Stage 2a runs with the trimmed Call 1 prompt — produces
        ``acceptance_criteria`` + ``persona_depth_app_id`` ONLY.
        NO MMI hypothesis, NO process_steps, NO decision_logic,
        NO persona_overrides.
      - Stage 2b runs with the trimmed Call 1 prompt — produces
        ``personas`` + ``integrations`` + ``scale_profile`` ONLY.
        NO NFRs, NO additional_source_quotes.
      - NO Stage 3 (no BPMN, no design docs, no EADDs).
      - Stage 4 post-processing runs as normal — open_items aggregation
        still surfaces the gaps a Call 1 transcript leaves behind.
      - ``pipeline_metadata.call_mode = "call1"``.

    Returns the assembled ``DiscoveryAnalysisV3``. Sets
    ``prior_call_artifact_id = None`` (Call 1 has no parent).
    """
    return await _run_call_mode(
        db=db,
        user=user,
        upload_ids=upload_ids,
        chat_session_id=chat_session_id,
        client_company=client_company,
        call_mode="call1",
        prior_artifact_id=None,
    )


async def run_v3_call2(
    db,
    user,
    upload_ids: list[str],
    prior_call1_artifact_id: Optional[str] = None,
    chat_session_id: Optional[str] = None,
    client_company: Optional[str] = None,
) -> DiscoveryAnalysisV3:
    """Call 2 / depth pass.

    Adds full Stage 2 + MMI but NO Stage 3:
      - If ``prior_call1_artifact_id`` is supplied, loads that artifact
        and threads its summary into the new pipeline run via
        ``build_prior_call_block``. The skeleton from Call 1 stays unless
        the new transcript contradicts; new pain points / workflows
        append; contradictions are flagged INFERRED_CONFIRM by the LLM.
      - Full Stage 1 (re-extracted from the merged transcript context)
        + canonical Stage 2a (MMI hypothesis + process_steps +
        decision_logic) + canonical Stage 2b (NFRs + additional source
        quotes).
      - NO Stage 3 — Call 2 still doesn't generate BPMN or design docs.
      - Stage 4 post-processing.
      - ``pipeline_metadata.call_mode = "call2"``.
      - ``prior_call_artifact_id`` set to ``prior_call1_artifact_id``
        when supplied (audit chain).
    """
    return await _run_call_mode(
        db=db,
        user=user,
        upload_ids=upload_ids,
        chat_session_id=chat_session_id,
        client_company=client_company,
        call_mode="call2",
        prior_artifact_id=prior_call1_artifact_id,
    )


async def run_v3_call3(
    db,
    user,
    upload_ids: list[str],
    prior_call2_artifact_id: Optional[str] = None,
    chat_session_id: Optional[str] = None,
    client_company: Optional[str] = None,
) -> DiscoveryAnalysisV3:
    """Call 3 / finalization.

    Full pipeline + contradiction resolution:
      - If ``prior_call2_artifact_id`` is supplied, loads + merges as
        Call 2 does. Adds the ``CALL3_CONTRADICTION_BLOCK`` to Stage 2
        prompts so the LLM is explicit about revisions vs the prior
        hypothesis.
      - Full Stage 1 + 2a + 2b + 3 (BPMN + design docs + EADDs).
      - Stage 4 post-processing.
      - ``pipeline_metadata.call_mode = "call3"``.
      - ``prior_call_artifact_id`` set to ``prior_call2_artifact_id``
        when supplied.

    The LLM may revise Call 2's MMI hypothesis. Revisions should be
    explicit in the dim ``reasoning`` fields per the prompt block;
    Stage 4 leaves ``contradictions_resolved`` empty for now (the
    audit-trail extractor is a deliberate follow-up — the structured
    reasoning is in the artifact even without it).
    """
    return await _run_call_mode(
        db=db,
        user=user,
        upload_ids=upload_ids,
        chat_session_id=chat_session_id,
        client_company=client_company,
        call_mode="call3",
        prior_artifact_id=prior_call2_artifact_id,
    )


# ──────────────────────────────────────────────────────────────────────
# Cross-call merge helper
# ──────────────────────────────────────────────────────────────────────


async def _merge_call_n_into_call_n_plus_1(
    db,
    user,
    prior_artifact_id: str,
) -> tuple[Optional[DiscoveryAnalysisV3], list[str]]:
    """Load the prior call's artifact + extract notes for the merge prompt.

    Returns ``(prior_artifact, contradiction_notes)``:
      - ``prior_artifact`` is the parsed ``DiscoveryAnalysisV3`` from
        ``ai_chat_artifacts`` (None when lookup fails — the caller falls
        back to a Call N pipeline run with no prior context).
      - ``contradiction_notes`` is a list of human-readable strings the
        caller may attach to telemetry — currently empty (the LLM does
        the actual contradiction detection in-prompt). Returned for
        future audit-trail extraction without changing this function's
        signature.

    The caller threads ``prior_artifact`` into the prompt via
    ``_summarise_artifact_for_prompt`` + ``build_prior_call_block``; we
    don't merge programmatically here because the LLM is better at
    semantic merging than we are.
    """
    contradiction_notes: list[str] = []

    if not prior_artifact_id:
        return None, contradiction_notes

    try:
        doc = await db.ai_chat_artifacts.find_one(
            {"artifact_id": prior_artifact_id, "user_id": user.user_id},
            {"_id": 0},
        )
    except Exception as exc:
        logger.warning(
            "[v3 call_modes] prior artifact lookup failed for %s: %s",
            prior_artifact_id, exc,
        )
        return None, contradiction_notes

    if not doc:
        logger.warning(
            "[v3 call_modes] prior artifact %s not found for user %s",
            prior_artifact_id, user.user_id,
        )
        return None, contradiction_notes

    payload = doc.get("payload") or {}
    if not payload:
        logger.warning(
            "[v3 call_modes] prior artifact %s has empty payload",
            prior_artifact_id,
        )
        return None, contradiction_notes

    try:
        prior_artifact = DiscoveryAnalysisV3.model_validate(payload)
    except Exception as exc:
        logger.warning(
            "[v3 call_modes] prior artifact %s payload failed v3 validation: %s — "
            "treating as no prior context",
            prior_artifact_id, exc,
        )
        return None, contradiction_notes

    return prior_artifact, contradiction_notes


def _summarise_artifact_for_prompt(
    artifact: DiscoveryAnalysisV3,
    max_open_items: int = 10,
) -> dict:
    """Build a compact summary of a prior artifact for prompt injection.

    Full artifact payloads are too large for a Stage 2 prompt — they'd
    blow Gemini's context budget. This summary keeps the bones (IDs,
    names, the highest-value extracted text) under a few KB.

    Output shape matches ``build_prior_call_block``'s expected input.
    """
    workflows: list[dict] = []
    for wf in artifact.workflows or []:
        ac_summary: Optional[str] = None
        if wf.acceptance_criteria:
            # First criterion's flow_name + success_when, truncated.
            first = wf.acceptance_criteria[0]
            success = (first.success_when or "").strip()
            if success:
                ac_summary = f"{first.flow_name}: {success[:120]}"
            else:
                ac_summary = f"{first.flow_name} (acceptance criterion missing)"
        workflows.append({
            "workflow_id": wf.workflow_id,
            "workflow_name": wf.workflow_name,
            "complexity": wf.complexity,
            "acceptance_criteria_summary": ac_summary,
        })

    apps: list[dict] = []
    for app in artifact.apps or []:
        integ_summary: Optional[str] = None
        if app.integrations:
            # Comma-joined list of system_names, truncated.
            names = [i.system_name for i in app.integrations[:5] if i.system_name]
            if names:
                integ_summary = "integrations: " + ", ".join(names)
        apps.append({
            "app_id": app.app_id,
            "app_name": app.app_name,
            "complexity": app.complexity,
            "integrations_summary": integ_summary,
        })

    sf = artifact.session_flags
    sf_parts: list[str] = []
    if sf and sf.tech_contact:
        sf_parts.append(f"tech_contact={sf.tech_contact}")
    if sf and sf.engineering_capacity is not None:
        sf_parts.append(f"engineering_capacity={sf.engineering_capacity}")
    if sf and sf.it_infosec_gate is not None:
        sf_parts.append(f"it_infosec_gate={sf.it_infosec_gate}")
    sf_summary = ", ".join(sf_parts) if sf_parts else None

    open_items_summary = [
        item.label for item in (artifact.open_items or [])[:max_open_items]
        if item.label
    ]

    return {
        "client_company": artifact.client_company,
        "workflows": workflows,
        "apps": apps,
        "session_flags_summary": sf_summary,
        "open_items_summary": open_items_summary,
    }


# ──────────────────────────────────────────────────────────────────────
# Internal orchestrator — parametrized by call_mode
# ──────────────────────────────────────────────────────────────────────


async def _run_call_mode(
    db,
    user,
    upload_ids: list[str],
    chat_session_id: Optional[str],
    client_company: Optional[str],
    call_mode: CallMode,
    prior_artifact_id: Optional[str],
) -> DiscoveryAnalysisV3:
    """The shared orchestrator for run_v3_call1/2/3.

    Reuses ``_stage_1_skeleton``, ``_stage_3_bpmn_and_design_docs``, and
    ``_stage_4_postprocess`` from ``v3_pipeline``. Stage 2 is a thin
    re-implementation that swaps in the Call-mode-specific prompt +
    schema (Call 1 trimmed, Call 2/3 canonical with optional prior-call
    block + Call-3 contradiction block).

    Stage 3 is skipped entirely for Call 1 + Call 2; Call 3 runs the
    full v3 Stage 3.

    Failure semantics match ``run_v3_pipeline`` — Stage 1 failure raises;
    Stage 2/3 per-entity failures mark ``partial_extraction_failed`` and
    the orchestrator continues.
    """
    session_id = chat_session_id or ""
    log_ctx = (
        f"[V3 {call_mode} | session={session_id} | user={user.user_id}]"
    )
    logger.info(
        "%s starting (uploads=%d, prior_artifact_id=%s)",
        log_ctx, len(upload_ids), prior_artifact_id,
    )

    pipeline_t0 = time.monotonic()

    # ── Load transcript text. ────────────────────────────────────────
    transcript_text = await _load_transcripts(db, upload_ids, user=user)
    if not transcript_text.strip():
        logger.warning("%s no transcript text — aborting", log_ctx)
        raise ValueError(
            f"v3 {call_mode} pipeline: no transcript text found for the supplied upload_ids"
        )

    # ── Resolve prior artifact (Call 2 / Call 3 only). ───────────────
    prior_artifact: Optional[DiscoveryAnalysisV3] = None
    prior_summary: Optional[dict] = None
    if prior_artifact_id and call_mode in ("call2", "call3"):
        prior_artifact, _notes = await _merge_call_n_into_call_n_plus_1(
            db, user, prior_artifact_id,
        )
        if prior_artifact:
            prior_summary = _summarise_artifact_for_prompt(prior_artifact)
            logger.info(
                "%s threaded prior artifact %s into prompts (workflows=%d apps=%d)",
                log_ctx, prior_artifact_id,
                len(prior_artifact.workflows), len(prior_artifact.apps),
            )

    # Optional: prepend prior transcripts to the new transcript text
    # so Stage 1 sees the full conversation history. This is best-effort
    # — if the prior artifact has no source filenames or the lookups
    # fail, we proceed with new-transcript-only context plus the
    # prompt-summary block.
    if prior_artifact and prior_artifact.source_transcripts:
        prior_text = await _load_prior_transcript_text(
            db, user, prior_artifact,
        )
        if prior_text:
            transcript_text = (
                f"--- PRIOR TRANSCRIPT(S) ---\n{prior_text}\n\n"
                f"--- NEW TRANSCRIPT(S) ---\n{transcript_text}"
            )

    # ── Stage 1 — skeleton. ──────────────────────────────────────────
    stage_1_t0 = time.monotonic()
    skeleton = await _stage_1_skeleton(
        transcript_text=transcript_text,
        db=db,
        user=user,
        session_id=session_id,
    )
    stage_1_ms = int((time.monotonic() - stage_1_t0) * 1000)
    s1_company, source_transcripts, session_flags, wf_skeletons, app_skeletons = skeleton
    final_company = (client_company or s1_company or "").strip() or s1_company
    logger.info(
        "%s Stage 1 complete in %dms (workflows=%d apps=%d)",
        log_ctx, stage_1_ms, len(wf_skeletons), len(app_skeletons),
    )
    await emit_vega_event(
        db, user.user_id, session_id, "v3_stage_complete",
        {"stage": 1, "duration_ms": stage_1_ms,
         "workflow_count": len(wf_skeletons),
         "app_count": len(app_skeletons),
         "call_mode": call_mode},
    )

    # ── Stage 2 — call-mode-aware fan-out. ───────────────────────────
    stage_2_t0 = time.monotonic()
    workflows, apps = await _stage_2_for_call_mode(
        wf_skeletons=wf_skeletons,
        app_skeletons=app_skeletons,
        transcript_text=transcript_text,
        db=db,
        user=user,
        session_id=session_id,
        call_mode=call_mode,
        prior_summary=prior_summary,
    )
    stage_2_ms = int((time.monotonic() - stage_2_t0) * 1000)
    partial_in_stage_2 = any(
        getattr(w, "partial_extraction_failed", False) for w in workflows
    ) or any(
        getattr(a, "partial_extraction_failed", False) for a in apps
    )
    logger.info(
        "%s Stage 2 complete in %dms (partial=%s)",
        log_ctx, stage_2_ms, partial_in_stage_2,
    )
    await emit_vega_event(
        db, user.user_id, session_id, "v3_stage_complete",
        {"stage": 2, "duration_ms": stage_2_ms,
         "partial": partial_in_stage_2,
         "call_mode": call_mode},
    )

    # ── Stage 3 — Call 3 only. ───────────────────────────────────────
    stage_3_ms: Optional[int] = None
    if call_mode == "call3":
        stage_3_t0 = time.monotonic()
        await _stage_3_bpmn_and_design_docs(
            workflows=workflows,
            apps=apps,
            db=db,
            user=user,
            session_id=session_id,
            client_industry="",
            stakeholders_summary="",
        )
        stage_3_ms = int((time.monotonic() - stage_3_t0) * 1000)
        logger.info("%s Stage 3 complete in %dms", log_ctx, stage_3_ms)
        await emit_vega_event(
            db, user.user_id, session_id, "v3_stage_complete",
            {"stage": 3, "duration_ms": stage_3_ms, "call_mode": call_mode},
        )
    else:
        logger.info("%s Stage 3 skipped (call_mode=%s)", log_ctx, call_mode)

    # ── Stage 4 — post-processing. ───────────────────────────────────
    stage_4_t0 = time.monotonic()
    shared_infra, open_items = _stage_4_postprocess(
        workflows=workflows,
        apps=apps,
        session_flags=session_flags,
    )
    stage_4_ms = int((time.monotonic() - stage_4_t0) * 1000)

    any_partial = any(
        getattr(w, "partial_extraction_failed", False) for w in workflows
    ) or any(
        getattr(a, "partial_extraction_failed", False) for a in apps
    )
    extraction_status: Literal["complete", "partial", "failed"] = (
        "partial" if any_partial else "complete"
    )

    total_ms = int((time.monotonic() - pipeline_t0) * 1000)
    logger.info(
        "%s done (status=%s, total=%dms)",
        log_ctx, extraction_status, total_ms,
    )
    await emit_vega_event(
        db, user.user_id, session_id, "v3_stage_complete",
        {"stage": 4, "duration_ms": stage_4_ms,
         "extraction_status": extraction_status,
         "call_mode": call_mode},
    )

    # ── Assemble final artifact. ─────────────────────────────────────
    artifact = DiscoveryAnalysisV3(
        client_company=final_company,
        source_transcripts=source_transcripts,
        session_flags=session_flags,
        workflows=workflows,
        apps=apps,
        shared_infrastructure=shared_infra,
        open_items=open_items,
        extraction_status=extraction_status,
        pipeline_metadata=PipelineMetadata(
            stage_1_duration_ms=stage_1_ms,
            stage_2_duration_ms=stage_2_ms,
            stage_3_duration_ms=stage_3_ms,  # None for Call 1 / Call 2
            stage_4_duration_ms=stage_4_ms,
            total_duration_ms=total_ms,
            call_mode=call_mode,
        ),
        prior_call_artifact_id=prior_artifact_id if prior_artifact else None,
    )
    logger.info(
        "%s artifact assembled (workflows=%d apps=%d open_items=%d)",
        log_ctx, len(artifact.workflows), len(artifact.apps),
        len(artifact.open_items),
    )
    return artifact


# ──────────────────────────────────────────────────────────────────────
# Stage 2 — call-mode-aware fan-out
# ──────────────────────────────────────────────────────────────────────


async def _stage_2_for_call_mode(
    wf_skeletons: list[WorkflowSkeleton],
    app_skeletons: list[AppSkeleton],
    transcript_text: str,
    db,
    user,
    session_id: str,
    call_mode: CallMode,
    prior_summary: Optional[dict],
):
    """Fan out per-workflow + per-app detail with the right prompt + schema.

    Call 1: trimmed prompt/schema. Call 2: canonical + optional prior-call
    block. Call 3: canonical + prior-call block + contradiction block.
    """
    log_ctx = (
        f"[V3 Stage 2 {call_mode} | session={session_id} | user={user.user_id}]"
    )
    sem = asyncio.Semaphore(V3_FANOUT_LIMIT)

    prior_label = "Call 1" if call_mode == "call2" else "Call 2"
    prior_block = build_prior_call_block(prior_summary, prior_call_label=prior_label)

    # Call 3: append the contradiction-resolution instruction block so
    # the LLM is explicit about revisions vs the prior Call 2 hypothesis.
    # Done here (not inside the per-entity extractor) so the assembled
    # block is visible to telemetry / tests as a single value.
    if call_mode == "call3":
        prior_block = (prior_block or "") + CALL3_CONTRADICTION_BLOCK

    async def _wf_task(wf_sk: WorkflowSkeleton):
        async with sem:
            try:
                return await _extract_workflow_detail_for_call_mode(
                    wf_sk, transcript_text, db, user, session_id,
                    call_mode=call_mode,
                    prior_block=prior_block,
                )
            except NotImplementedError:
                raise
            except Exception as exc:
                logger.warning(
                    "%s workflow %s detail extraction raised: %s",
                    log_ctx, wf_sk.workflow_id, exc,
                )
                return {"partial_extraction_failed": True, "reason": str(exc)}

    async def _app_task(app_sk: AppSkeleton):
        async with sem:
            try:
                return await _extract_app_detail_for_call_mode(
                    app_sk, transcript_text, db, user, session_id,
                    call_mode=call_mode,
                    prior_block=prior_block,
                )
            except NotImplementedError:
                raise
            except Exception as exc:
                logger.warning(
                    "%s app %s detail extraction raised: %s",
                    log_ctx, app_sk.app_id, exc,
                )
                return {"partial_extraction_failed": True, "reason": str(exc)}

    wf_tasks = [_wf_task(wf) for wf in wf_skeletons]
    app_tasks = [_app_task(app) for app in app_skeletons]

    wf_results = await asyncio.gather(*wf_tasks, return_exceptions=True)
    app_results = await asyncio.gather(*app_tasks, return_exceptions=True)

    workflows = [
        _merge_workflow(sk, detail, log_ctx=log_ctx)
        for sk, detail in zip(wf_skeletons, wf_results)
    ]
    apps = [
        _merge_app(sk, detail, log_ctx=log_ctx)
        for sk, detail in zip(app_skeletons, app_results)
    ]
    return workflows, apps


async def _extract_workflow_detail_for_call_mode(
    skeleton: WorkflowSkeleton,
    transcript_text: str,
    db,
    user,
    session_id: str,
    call_mode: CallMode,
    prior_block: str,
):
    """Per-workflow Stage 2a extractor parametrized by call_mode.

    Returns the same contract as ``v3_pipeline._extract_workflow_detail``:
      - ``WorkflowDetail`` on success.
      - ``{"partial_extraction_failed": True, "reason": str}`` on retry-then-fail.
    Never raises.
    """
    log_ctx = (
        f"[V3 Stage 2a {call_mode} | workflow={skeleton.workflow_id}"
        f" | session={session_id} | user={user.user_id}]"
    )

    schema, prompt_template = _select_workflow_schema_and_prompt(call_mode)

    # The prompt templates have ``{prior_answers_block}`` for Wave 5b;
    # we re-purpose it as the "extra context block" slot for call modes
    # so the trimmed Call 1 prompt and the canonical Call 2/3 prompts
    # share the same .format() signature. Caller (``_stage_2_for_call_mode``)
    # already appended the Call-3 contradiction block when applicable.
    prompt = prompt_template.format(
        workflow_id=skeleton.workflow_id,
        workflow_name=skeleton.workflow_name,
        skeleton_json=json.dumps(skeleton.model_dump(), default=str, indent=2),
        transcript_text=transcript_text,
        prior_answers_block=prior_block or "",
    )

    from services.ai.conductor import _get_model
    provider, model = await _get_model(db, "emp_ai_vega")

    async def _call():
        t0 = time.monotonic()
        resp = await call_llm(
            system_prompt=(
                "You are Vega, the AI Recruiter Agent. Return strict structured JSON."
            ),
            user_message=prompt,
            provider=provider,
            model=model,
            response_schema=schema,
            timeout=V3_LLM_TIMEOUT,
            temperature=VEGA_LLM_TEMPERATURE,
        )
        elapsed = time.monotonic() - t0
        if not resp or not resp.text:
            logger.warning("%s empty LLM response after %.1fs", log_ctx, elapsed)
            return None
        logger.info(
            "%s workflow detail LLM responded in %.1fs (%d chars)",
            log_ctx, elapsed, len(resp.text),
        )
        try:
            return json.loads(resp.text)
        except json.JSONDecodeError:
            logger.error(
                "%s workflow detail JSON parse failed: %s",
                log_ctx, resp.text[:500],
            )
            return None

    try:
        raw = await run_with_retry(_call, call_num=f"v3.{call_mode}.wf.{skeleton.workflow_id}")
        if not raw:
            return {
                "partial_extraction_failed": True,
                "reason": f"{call_mode} workflow detail call returned no usable output after retry",
            }
        # Call 1: only acceptance_criteria + persona_depth_app_id are
        # promised. We still validate via WorkflowDetail (every field is
        # optional) so the merge layer doesn't see a divergent shape.
        detail = WorkflowDetail.model_validate(raw)
        logger.info("%s workflow detail validated OK", log_ctx)
        return detail
    except Exception as exc:
        reason = str(exc)[:200]
        logger.warning("%s workflow detail failed: %s", log_ctx, reason)
        return {"partial_extraction_failed": True, "reason": reason}


async def _extract_app_detail_for_call_mode(
    skeleton: AppSkeleton,
    transcript_text: str,
    db,
    user,
    session_id: str,
    call_mode: CallMode,
    prior_block: str,
):
    """Per-app Stage 2b extractor parametrized by call_mode.

    Same contract as ``v3_pipeline._extract_app_detail``.
    """
    log_ctx = (
        f"[V3 Stage 2b {call_mode} | app={skeleton.app_id}"
        f" | session={session_id} | user={user.user_id}]"
    )

    schema, prompt_template = _select_app_schema_and_prompt(call_mode)

    # Caller (``_stage_2_for_call_mode``) already appended the Call-3
    # contradiction block to ``prior_block`` when applicable.
    prompt = prompt_template.format(
        app_id=skeleton.app_id,
        app_name=skeleton.app_name,
        skeleton_json=json.dumps(skeleton.model_dump(), default=str, indent=2),
        transcript_text=transcript_text,
        prior_answers_block=prior_block or "",
    )

    from services.ai.conductor import _get_model
    provider, model = await _get_model(db, "emp_ai_vega")

    async def _call():
        t0 = time.monotonic()
        resp = await call_llm(
            system_prompt=(
                "You are Vega, the AI Recruiter Agent. Return strict structured JSON."
            ),
            user_message=prompt,
            provider=provider,
            model=model,
            response_schema=schema,
            timeout=V3_LLM_TIMEOUT,
            temperature=VEGA_LLM_TEMPERATURE,
        )
        elapsed = time.monotonic() - t0
        if not resp or not resp.text:
            logger.warning("%s empty LLM response after %.1fs", log_ctx, elapsed)
            return None
        logger.info(
            "%s app detail LLM responded in %.1fs (%d chars)",
            log_ctx, elapsed, len(resp.text),
        )
        try:
            return json.loads(resp.text)
        except json.JSONDecodeError:
            logger.error(
                "%s app detail JSON parse failed: %s",
                log_ctx, resp.text[:500],
            )
            return None

    try:
        raw = await run_with_retry(_call, call_num=f"v3.{call_mode}.app.{skeleton.app_id}")
        if not raw:
            return {
                "partial_extraction_failed": True,
                "reason": f"{call_mode} app detail call returned no usable output after retry",
            }
        detail = AppDetail.model_validate(raw)
        logger.info("%s app detail validated OK", log_ctx)
        return detail
    except Exception as exc:
        reason = str(exc)[:200]
        logger.warning("%s app detail failed: %s", log_ctx, reason)
        return {"partial_extraction_failed": True, "reason": reason}


def _select_workflow_schema_and_prompt(call_mode: CallMode) -> tuple[dict, str]:
    """Pick the right (schema, prompt) pair for the workflow detail call."""
    if call_mode == "call1":
        return WORKFLOW_DETAIL_SCHEMA_CALL1, WORKFLOW_DETAIL_PROMPT_CALL1
    # Call 2 + Call 3 both use the canonical schema. The Call-3
    # contradiction block is added by the caller as part of
    # ``prior_answers_block`` substitution.
    return WORKFLOW_DETAIL_SCHEMA, WORKFLOW_DETAIL_PROMPT


def _select_app_schema_and_prompt(call_mode: CallMode) -> tuple[dict, str]:
    """Pick the right (schema, prompt) pair for the app detail call."""
    if call_mode == "call1":
        return APP_DETAIL_SCHEMA_CALL1, APP_DETAIL_PROMPT_CALL1
    return APP_DETAIL_SCHEMA, APP_DETAIL_PROMPT


# ──────────────────────────────────────────────────────────────────────
# Prior-transcript loader (Call 2 / Call 3 cross-call merge)
# ──────────────────────────────────────────────────────────────────────


async def _load_prior_transcript_text(
    db,
    user,
    prior_artifact: DiscoveryAnalysisV3,
) -> str:
    """Best-effort load of the prior call's transcript text by filename.

    The artifact stores ``source_transcripts`` as filename refs only —
    not upload IDs. We look up the matching ``chat_uploads`` documents
    by ``(filename, user_id)``. Filename collisions across user uploads
    take the first match; this is the same trade-off the partial-rerun
    flow makes in ``_assemble_transcript_for_partial``.

    Returns "" when no prior transcripts can be re-loaded — the caller
    falls back to new-transcript-only context plus the prompt summary.
    """
    chunks: list[str] = []
    for ref in prior_artifact.source_transcripts or []:
        if not ref.filename:
            continue
        try:
            doc = await db.chat_uploads.find_one(
                {"filename": ref.filename, "user_id": user.user_id},
                {"_id": 0},
            )
        except Exception as exc:
            logger.warning(
                "[v3 call_modes] prior transcript lookup failed for %s: %s",
                ref.filename, exc,
            )
            continue
        if doc and doc.get("extracted_text"):
            chunks.append(f"--- {ref.filename} (prior call) ---\n{doc['extracted_text']}")
    return "\n\n".join(chunks).strip()


__all__ = [
    "run_v3_call1",
    "run_v3_call2",
    "run_v3_call3",
    "_merge_call_n_into_call_n_plus_1",
    "_summarise_artifact_for_prompt",
    "_run_call_mode",
    "_stage_2_for_call_mode",
    "_extract_workflow_detail_for_call_mode",
    "_extract_app_detail_for_call_mode",
    "_select_workflow_schema_and_prompt",
    "_select_app_schema_and_prompt",
    "_load_prior_transcript_text",
]
