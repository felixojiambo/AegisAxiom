"""Vega v3 pipeline orchestrator.

This is the public entry point for the v3 staged-extraction pipeline. It
calls the Stage 1 skeleton schema, fans out per-entity Stage 2 calls,
fires Stage 3 BPMN + design-doc generation, then runs deterministic
Stage 4 post-processing.

v2 code paths (run_call3_analysis, run_single_call_pipeline,
run_transcript_analysis) are NOT touched. v2 and v3 coexist until the
Wave 4 cutover.

Stage hooks (other Wave 2 threads implement):

    Thread 2b — Per-workflow + per-app detail extractors:
        _extract_workflow_detail(skeleton, transcript_text, db, user, session_id)
            -> WorkflowDetail | dict
            Returns WorkflowDetail on success, or
            {"partial_extraction_failed": True, "reason": str}
            on retry-then-fail.
        _extract_app_detail(skeleton, transcript_text, db, user, session_id)
            -> AppDetail | dict
            Same contract.
        Each call: 180s timeout, retried once via run_with_retry.

    Thread 2c — Post-processing (pure Python, no LLM):
        _detect_shared_infrastructure(workflows, apps)
            -> list[SharedInfrastructureRow]
        _bucket_standalone_apps(workflows, apps)
            -> list[App]   # apps with empty linked_workflow_ids
            Note: this may collapse to a one-line filter; if so, no
            separate function is needed and 2c can drop it.
        _aggregate_open_items(workflows, apps, session_flags)
            -> list[OpenItem]
            Walks every flagged field and produces structured OpenItem entries.

    Thread 2d — BPMN + design docs:
        _generate_bpmn_for_workflow(workflow, db, user, session_id) -> Workflow
            Adds bpmn_id, bpmn_xml, bpmn_status, bpmn_content_hash. 180s timeout.
        _generate_design_doc_for_workflow(workflow, db, user, session_id) -> Workflow
        _generate_eadd_for_app(app, db, user, session_id) -> App
        Reuse v2 generators where possible.

The four ``_stage_N`` methods below own the orchestration; the helper
underscored functions above are the implementation seams. Keep the
contracts here in sync with what 2b/2c/2d ship.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Literal, Optional

from datetime import datetime, timezone

from services.ai.vega_analysis import (
    VEGA_LLM_TEMPERATURE,
    _bpmn_content_hash,
    _generate_single_design_doc,
    _generate_single_eadd,
    _load_transcripts,
    run_with_retry,
)
from services.bpmn.tool_wrapper import generate_workflow_diagram
from services.ai.llm import call_llm
from services.vega.flags import VegaFlag
from services.vega.postprocess import (
    aggregate_open_items as _aggregate_open_items_impl,
    detect_shared_infrastructure as _detect_shared_infrastructure_impl,
)
from services.vega.models_v3 import (
    App,
    AppDetail,
    AppSkeleton,
    DiscoveryAnalysisV3,
    MMIDimension,
    MMIHypothesis,
    OpenItem,
    PipelineMetadata,
    SessionFlags,
    SharedInfrastructureRow,
    SourceProvenance,
    TranscriptRef,
    Workflow,
    WorkflowDetail,
    WorkflowSkeleton,
)
from services.vega.detail_schemas import (
    APP_DETAIL_PROMPT,
    APP_DETAIL_SCHEMA,
    WORKFLOW_DETAIL_PROMPT,
    WORKFLOW_DETAIL_SCHEMA,
    format_prior_answers_block,
)
from services.vega.regenerate import RegenScope
from services.vega.skeleton_schema import SKELETON_PROMPT, SKELETON_SCHEMA
from services.vega_events import emit_vega_event

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Module-level constants
# ──────────────────────────────────────────────────────────────────────

# Per-call timeout for v3 LLM calls. The v3 staging splits the workload
# so no single call needs the v2 300s ceiling; 180s is the locked budget
# from the wave plan and applies uniformly to Stages 1, 2a, 2b, 3.
V3_LLM_TIMEOUT = 180

# Fan-out concurrency cap, mirroring v2's run_call3_analysis pattern.
# Five concurrent LLM calls keeps wall-clock low without triggering
# Gemini's per-project rate limits.
V3_FANOUT_LIMIT = 5


# Type alias for the Stage 1 raw output the orchestrator passes around
# internally. We keep it as a thin tuple rather than a Pydantic model
# because the workflows / apps lists here are the *skeleton* shapes that
# Stage 2 will deepen — the merged ``Workflow`` / ``App`` shapes only
# exist after Stage 2 completes.
SkeletonResult = tuple[
    str,                       # client_company
    list[TranscriptRef],       # source_transcripts
    SessionFlags,              # session_flags
    list[WorkflowSkeleton],    # workflows
    list[AppSkeleton],         # apps
]


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────


async def run_v3_pipeline(
    db,
    user,
    upload_ids: list[str],
    chat_session_id: Optional[str] = None,
    client_company: Optional[str] = None,
) -> DiscoveryAnalysisV3:
    """Run the v3 staged extraction pipeline end-to-end.

    Args:
        db: Mongo client (passed verbatim to LLM helpers + event emitter).
        user: Authenticated user object — required for transcript IDOR scoping.
        upload_ids: chat_uploads identifiers to source transcript text from.
        chat_session_id: Session ID for telemetry / event emission.
        client_company: Optional override — if the caller already knows
            the company, the orchestrator will pass it through to the
            output even when Stage 1 also extracts it.

    Returns:
        ``DiscoveryAnalysisV3`` populated end-to-end. ``extraction_status``
        is ``"complete"`` when no entity has ``partial_extraction_failed``;
        ``"partial"`` when at least one does.

    Raises:
        Stage 1 failures propagate (no skeleton means nothing to render —
        the artifact never lands). Stage 2 / 3 per-entity failures are
        caught and marked on the entity; the pipeline continues.
        Stage 4 is deterministic — bugs there raise.
    """
    session_id = chat_session_id or ""
    log_ctx = f"[V3 pipeline | session={session_id} | user={user.user_id}]"
    logger.info("%s starting (uploads=%d)", log_ctx, len(upload_ids))

    pipeline_t0 = time.monotonic()

    # ── Load transcript text once and pass to every stage. ───────────
    transcript_text = await _load_transcripts(db, upload_ids, user=user)
    if not transcript_text.strip():
        logger.warning("%s no transcript text — aborting", log_ctx)
        raise ValueError(
            "v3 pipeline: no transcript text found for the supplied upload_ids"
        )
    logger.info(
        "%s transcript loaded (%d chars from %d upload(s))",
        log_ctx, len(transcript_text), len(upload_ids),
    )

    # ── Stage 1 — skeleton call. ─────────────────────────────────────
    stage_1_t0 = time.monotonic()
    skeleton = await _stage_1_skeleton(
        transcript_text=transcript_text,
        db=db,
        user=user,
        session_id=session_id,
    )
    stage_1_ms = int((time.monotonic() - stage_1_t0) * 1000)
    logger.info(
        "%s Stage 1 complete in %dms (workflows=%d apps=%d)",
        log_ctx, stage_1_ms, len(skeleton[3]), len(skeleton[4]),
    )
    await emit_vega_event(
        db, user.user_id, session_id, "v3_stage_complete",
        {"stage": 1, "duration_ms": stage_1_ms,
         "workflow_count": len(skeleton[3]), "app_count": len(skeleton[4])},
    )

    s1_company, source_transcripts, session_flags, wf_skeletons, app_skeletons = skeleton
    final_company = (client_company or s1_company or "").strip() or s1_company

    # ── Stage 2 — per-workflow + per-app detail (parallel fan-out). ──
    stage_2_t0 = time.monotonic()
    workflows, apps = await _stage_2_details(
        skeleton=skeleton,
        transcript_text=transcript_text,
        db=db,
        user=user,
        session_id=session_id,
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
        {"stage": 2, "duration_ms": stage_2_ms, "partial": partial_in_stage_2},
    )

    # ── Stage 3 — BPMN + design docs (parallel fan-out). ─────────────
    # client_industry and stakeholders_summary are not yet extracted in
    # v3 Stage 1 (they come from v2's Call 3 LLM). Pass empty strings —
    # the v2 generators treat "(unspecified)" gracefully.
    # TODO(wave3): when v3 Stage 1 or 2 extracts industry/stakeholder
    # context, thread those values through here.
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
        {"stage": 3, "duration_ms": stage_3_ms},
    )

    # ── Stage 4 — pure-Python post-processing. ───────────────────────
    stage_4_t0 = time.monotonic()
    shared_infra, open_items = _stage_4_postprocess(
        workflows=workflows,
        apps=apps,
        session_flags=session_flags,
    )
    stage_4_ms = int((time.monotonic() - stage_4_t0) * 1000)

    # extraction_status is the orchestrator's call: post-processing is
    # deterministic and doesn't know whether earlier stages partial-failed.
    # "failed" is reserved for Stage 1 producing nothing — that path raises
    # before reaching here, so we only choose between "complete" and "partial".
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
        "%s Stage 4 complete (status=%s, stage_4=%dms, total=%dms)",
        log_ctx, extraction_status, stage_4_ms, total_ms,
    )
    await emit_vega_event(
        db, user.user_id, session_id, "v3_stage_complete",
        {"stage": 4, "duration_ms": stage_4_ms,
         "extraction_status": extraction_status},
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
            stage_3_duration_ms=stage_3_ms,
            stage_4_duration_ms=stage_4_ms,
            total_duration_ms=total_ms,
        ),
    )
    logger.info(
        "%s artifact assembled (workflows=%d apps=%d open_items=%d)",
        log_ctx, len(artifact.workflows), len(artifact.apps), len(artifact.open_items),
    )
    return artifact


# ──────────────────────────────────────────────────────────────────────
# Partial regeneration entry point (Wave 5b)
# ──────────────────────────────────────────────────────────────────────


async def run_v3_pipeline_partial(
    db,
    user,
    prior_artifact: DiscoveryAnalysisV3,
    regen_scope: RegenScope,
    answered_items: list[dict],
    new_upload_ids: Optional[list[str]] = None,
) -> DiscoveryAnalysisV3:
    """Partial rerun: skip Stage 1, re-extract only the entities in regen_scope.

    Wave 5b's core mechanic. The full pipeline costs ~5–8 LLM calls per app
    plus skeleton + design docs; a partial rerun costs only the entities the
    user just answered for. The artifact ID stays the same — Wave 5c writes a
    new ``vega_artifact_versions`` row from the returned object.

    Args:
        db: Mongo client.
        user: Authenticated user — required for transcript IDOR scoping.
        prior_artifact: The current ``DiscoveryAnalysisV3`` whose entities
            are partially being re-extracted. Read-only here — never mutated.
        regen_scope: ``RegenScope`` produced by ``compute_combined_blast_radius``.
            Lists the workflow_ids / app_ids to re-extract; ``rerun_shared_infra``
            forces Stage 4 shared-infra recomputation; ``rerun_open_items`` is
            always honoured.
        answered_items: List of dicts shaped
            ``{"open_item_index": int, "value": str, "label": str, "location": str}``.
            ``label`` becomes the key in the entity's ``user_provided_answers``
            dict so the next regeneration's prompt sees the answer.
        new_upload_ids: Optional list of upload_ids whose extracted_text should
            be appended to the prior transcript text passed to the re-extraction
            prompts. Cumulative across regenerations.

    Returns:
        A fresh ``DiscoveryAnalysisV3``. Entities not in ``regen_scope`` are
        copied verbatim from ``prior_artifact``; entities in scope have their
        Stage 2 detail (and Stage 3 design-doc / EADD) re-extracted. Stage 4
        post-processing always re-runs over the merged result.

    Raises:
        ValueError: when ``new_upload_ids`` references uploads with no
            extracted_text (caught by ``_load_transcripts`` returning empty
            and the partial flow then continuing with old transcript text only,
            matching the full pipeline's behaviour).
    """
    session_id = ""  # Partial regen reuses the calling session indirectly via
    # the wave 5c endpoint — we don't have the v3 session_id on the artifact
    # itself. Telemetry events therefore carry only user_id + artifact context.
    log_ctx = f"[V3 partial | user={user.user_id}]"

    pipeline_t0 = time.monotonic()

    workflow_ids_to_regen = set(regen_scope.workflow_ids or [])
    app_ids_to_regen = set(regen_scope.app_ids or [])

    logger.info(
        "%s starting partial regen (workflows=%d apps=%d shared_infra=%s "
        "answered_items=%d new_uploads=%d)",
        log_ctx,
        len(workflow_ids_to_regen),
        len(app_ids_to_regen),
        regen_scope.rerun_shared_infra,
        len(answered_items or []),
        len(new_upload_ids or []),
    )
    await emit_vega_event(
        db, user.user_id, session_id, "v3_partial_regen_started",
        {
            "workflow_ids": sorted(workflow_ids_to_regen),
            "app_ids": sorted(app_ids_to_regen),
            "rerun_shared_infra": regen_scope.rerun_shared_infra,
            "answered_item_count": len(answered_items or []),
            "new_upload_count": len(new_upload_ids or []),
        },
    )

    # ── Build the transcript text for re-extracted entities. ─────────
    # Priority: any new uploads first (so the new transcript is loaded
    # under the user's IDOR guard), then append the prior artifact's
    # transcript text on as a stable contextual base.
    transcript_text = await _assemble_transcript_for_partial(
        db=db,
        user=user,
        prior_artifact=prior_artifact,
        new_upload_ids=new_upload_ids,
    )

    # ── Group answered items by entity location for prompt injection. ──
    # Each entity that's about to be re-extracted gets a slice of the
    # answered_items whose ``location`` matches that entity. The key in
    # ``user_provided_answers`` is the OpenItem.label so subsequent
    # regenerations see it without further conversion.
    answers_by_location = _group_answers_by_location(answered_items or [])

    # ── Build new transcript ref list — append the new uploads. ─────
    source_transcripts = list(prior_artifact.source_transcripts or [])
    if new_upload_ids:
        appended = await _build_transcript_refs_for_uploads(db, user, new_upload_ids)
        source_transcripts.extend(appended)

    # ── Stage 2 partial — re-extract only entities in scope. ─────────
    stage_2_t0 = time.monotonic()
    new_workflows, new_apps = await _stage_2_partial(
        prior_artifact=prior_artifact,
        workflow_ids_to_regen=workflow_ids_to_regen,
        app_ids_to_regen=app_ids_to_regen,
        answers_by_location=answers_by_location,
        transcript_text=transcript_text,
        db=db,
        user=user,
        session_id=session_id,
    )
    stage_2_ms = int((time.monotonic() - stage_2_t0) * 1000)
    logger.info("%s partial Stage 2 complete in %dms", log_ctx, stage_2_ms)

    # Per-entity progress events for the front-end progress bar.
    for wf_id in sorted(workflow_ids_to_regen):
        await emit_vega_event(
            db, user.user_id, session_id, "v3_partial_regen_entity_complete",
            {"entity_type": "workflow", "entity_id": wf_id},
        )
    for app_id in sorted(app_ids_to_regen):
        await emit_vega_event(
            db, user.user_id, session_id, "v3_partial_regen_entity_complete",
            {"entity_type": "app", "entity_id": app_id},
        )

    # ── Stage 3 — only run for entities we just re-extracted. ────────
    # BPMN regeneration is short-circuited by ``_bpmn_content_hash``: if
    # process_steps + decision_logic match the prior workflow's hash, the
    # previously-generated bpmn_id / bpmn_xml are carried forward.
    stage_3_t0 = time.monotonic()
    await _stage_3_partial(
        new_workflows=new_workflows,
        new_apps=new_apps,
        prior_artifact=prior_artifact,
        workflow_ids_to_regen=workflow_ids_to_regen,
        app_ids_to_regen=app_ids_to_regen,
        db=db,
        user=user,
        session_id=session_id,
    )
    stage_3_ms = int((time.monotonic() - stage_3_t0) * 1000)
    logger.info("%s partial Stage 3 complete in %dms", log_ctx, stage_3_ms)

    # ── Stage 4 — open_items always; shared_infra only if requested. ─
    stage_4_t0 = time.monotonic()
    if regen_scope.rerun_shared_infra:
        shared_infra = _detect_shared_infrastructure(new_workflows, new_apps)
    else:
        # Carry forward — none of the apps' integrations changed.
        shared_infra = list(prior_artifact.shared_infrastructure or [])
    open_items = _aggregate_open_items(
        new_workflows, new_apps, prior_artifact.session_flags
    )
    stage_4_ms = int((time.monotonic() - stage_4_t0) * 1000)

    any_partial = any(
        getattr(w, "partial_extraction_failed", False) for w in new_workflows
    ) or any(
        getattr(a, "partial_extraction_failed", False) for a in new_apps
    )
    extraction_status: Literal["complete", "partial", "failed"] = (
        "partial" if any_partial else "complete"
    )

    total_ms = int((time.monotonic() - pipeline_t0) * 1000)

    artifact = DiscoveryAnalysisV3(
        client_company=prior_artifact.client_company,
        source_transcripts=source_transcripts,
        session_flags=prior_artifact.session_flags,
        workflows=new_workflows,
        apps=new_apps,
        shared_infrastructure=shared_infra,
        open_items=open_items,
        extraction_status=extraction_status,
        pipeline_metadata=PipelineMetadata(
            # Stage 1 is intentionally not re-run — leave the field None
            # so consumers can distinguish a partial run from a full run.
            stage_1_duration_ms=None,
            stage_2_duration_ms=stage_2_ms,
            stage_3_duration_ms=stage_3_ms,
            stage_4_duration_ms=stage_4_ms,
            total_duration_ms=total_ms,
        ),
    )
    logger.info(
        "%s partial regen complete (status=%s, total=%dms, workflows=%d, apps=%d)",
        log_ctx, extraction_status, total_ms,
        len(artifact.workflows), len(artifact.apps),
    )
    await emit_vega_event(
        db, user.user_id, session_id, "v3_partial_regen_complete",
        {
            "extraction_status": extraction_status,
            "duration_ms": total_ms,
            "workflow_count": len(artifact.workflows),
            "app_count": len(artifact.apps),
            "regen_workflow_count": len(workflow_ids_to_regen),
            "regen_app_count": len(app_ids_to_regen),
        },
    )
    return artifact


# ──────────────────────────────────────────────────────────────────────
# Partial regen helpers
# ──────────────────────────────────────────────────────────────────────


async def _assemble_transcript_for_partial(
    db,
    user,
    prior_artifact: DiscoveryAnalysisV3,
    new_upload_ids: Optional[list[str]],
) -> str:
    """Build the transcript text passed to re-extraction prompts.

    The prior artifact doesn't store its raw transcript text inline (Stage 1
    is skipped on regen), so we read the prior transcripts via their
    upload_ids if available. Falls back to using just the new transcript
    text when prior transcripts cannot be re-loaded — the user's ``answered_items``
    still ground the regen via ``prior_answers_block``.

    Args:
        db: Mongo client.
        user: Authenticated user — required for IDOR-scoped reads.
        prior_artifact: Source of prior ``source_transcripts`` filenames.
        new_upload_ids: Newly-uploaded transcripts to append.

    Returns:
        Concatenated transcript text. May be empty when neither prior
        nor new transcripts have extracted_text — the caller handles
        the empty case via the ``prior_answers_block`` slot.
    """
    chunks: list[str] = []

    # Re-load prior uploads when we can find their IDs. The artifact's
    # ``source_transcripts`` carries filename, not upload_id, so we look
    # up uploads by filename + user_id. This is best-effort: if the
    # filename collides across user uploads we accept the first match.
    prior_filenames = [
        ref.filename
        for ref in (prior_artifact.source_transcripts or [])
        if ref.filename
    ]
    for fname in prior_filenames:
        try:
            doc = await db.chat_uploads.find_one(
                {"filename": fname, "user_id": user.user_id},
                {"_id": 0},
            )
        except Exception as exc:
            logger.warning("partial regen: prior transcript lookup failed for %s: %s", fname, exc)
            continue
        if doc and doc.get("extracted_text"):
            chunks.append(f"\n--- {fname} (prior) ---\n{doc['extracted_text']}")

    # New uploads — IDOR-scoped via _load_transcripts.
    if new_upload_ids:
        new_text = await _load_transcripts(db, new_upload_ids, user=user)
        if new_text.strip():
            chunks.append(f"\n--- NEW TRANSCRIPT(S) ---\n{new_text}")

    return "\n".join(chunks).strip()


async def _build_transcript_refs_for_uploads(
    db,
    user,
    upload_ids: list[str],
) -> list[TranscriptRef]:
    """Look up newly-uploaded transcripts and emit TranscriptRef entries.

    Returns one TranscriptRef per upload_id with extracted_text. Skips uploads
    without text (mirrors ``_load_transcripts`` skip behaviour).
    """
    refs: list[TranscriptRef] = []
    for uid in upload_ids:
        try:
            doc = await db.chat_uploads.find_one(
                {"upload_id": uid, "user_id": user.user_id},
                {"_id": 0},
            )
        except Exception as exc:
            logger.warning("partial regen: new upload lookup failed for %s: %s", uid, exc)
            continue
        if not doc or not doc.get("extracted_text"):
            continue
        refs.append(TranscriptRef(
            filename=doc.get("filename") or uid,
            duration_minutes=None,
            attendees=[],
            captured_at=None,
        ))
    return refs


def _group_answers_by_location(answered_items: list[dict]) -> dict[str, dict[str, str]]:
    """Group answered items by their ``location`` string.

    Returns a mapping ``{location: {label: value}}`` where ``location`` is the
    OpenItem location pointer (``"workflow:<id>"``, ``"app:<id>"``, etc.) and
    the inner dict is the slice of ``user_provided_answers`` to merge into
    the entity's existing accumulator.

    Items without a ``label`` or ``location`` are skipped — the value can't
    be threaded into an entity prompt without the entity pointer.
    """
    grouped: dict[str, dict[str, str]] = {}
    for item in answered_items:
        if not isinstance(item, dict):
            continue
        location = (item.get("location") or "").strip()
        label = (item.get("label") or "").strip()
        value = item.get("value")
        if not location or not label or value is None:
            continue
        grouped.setdefault(location, {})[label] = str(value)
    return grouped


async def _stage_2_partial(
    prior_artifact: DiscoveryAnalysisV3,
    workflow_ids_to_regen: set[str],
    app_ids_to_regen: set[str],
    answers_by_location: dict[str, dict[str, str]],
    transcript_text: str,
    db,
    user,
    session_id: str,
) -> tuple[list[Workflow], list[App]]:
    """Re-extract entities in scope and copy the rest verbatim.

    Returns (new_workflows, new_apps) preserving the order of the prior
    artifact. Entities in scope are run through ``_extract_workflow_detail`` /
    ``_extract_app_detail`` with the user's accumulated answers passed in.
    The ``_merge_workflow`` / ``_merge_app`` helpers carry forward
    ``user_provided_answers`` from the prior entity (strict accumulator —
    the LLM cannot overwrite a user-supplied answer).
    """
    log_ctx = f"[V3 partial Stage 2 | user={user.user_id}]"
    sem = asyncio.Semaphore(V3_FANOUT_LIMIT)

    async def _one_workflow(prior_wf: Workflow) -> Workflow:
        if prior_wf.workflow_id not in workflow_ids_to_regen:
            # Verbatim copy — Pydantic's model_copy gives us a fresh
            # instance so the new artifact owns its own object graph.
            return prior_wf.model_copy(deep=True)

        # Build prompt-time answer block: union of prior_wf.user_provided_answers
        # + this regen's freshly-answered items targeting this workflow.
        loc_key = f"workflow:{prior_wf.workflow_id}"
        merged_answers: dict[str, str] = dict(prior_wf.user_provided_answers or {})
        merged_answers.update(answers_by_location.get(loc_key, {}))

        # Reconstruct a WorkflowSkeleton from the prior Workflow's skeleton fields.
        skeleton = WorkflowSkeleton(
            workflow_id=prior_wf.workflow_id,
            workflow_name=prior_wf.workflow_name,
            complexity=prior_wf.complexity,
            mmi_dims={},  # not used by the prompt; full MMI rebuilt in Stage 2a.
            source=prior_wf.source,
            linked_app_ids=list(prior_wf.linked_app_ids or []),
        )

        async with sem:
            try:
                detail = await _extract_workflow_detail(
                    skeleton,
                    transcript_text,
                    db,
                    user,
                    session_id,
                    prior_answers=merged_answers,
                )
            except NotImplementedError:
                raise
            except Exception as exc:
                logger.warning(
                    "%s workflow %s partial extraction raised: %s",
                    log_ctx, prior_wf.workflow_id, exc,
                )
                detail = {"partial_extraction_failed": True, "reason": str(exc)}

        # Build a "prior" Workflow whose user_provided_answers reflect the
        # union — _merge_workflow then carries those into the new Workflow.
        prior_for_merge = prior_wf.model_copy(
            update={"user_provided_answers": merged_answers}
        )
        return _merge_workflow(skeleton, detail, log_ctx=log_ctx, prior=prior_for_merge)

    async def _one_app(prior_app: App) -> App:
        if prior_app.app_id not in app_ids_to_regen:
            return prior_app.model_copy(deep=True)

        loc_key = f"app:{prior_app.app_id}"
        merged_answers: dict[str, str] = dict(prior_app.user_provided_answers or {})
        merged_answers.update(answers_by_location.get(loc_key, {}))

        skeleton = AppSkeleton(
            app_id=prior_app.app_id,
            app_name=prior_app.app_name,
            complexity=prior_app.complexity,
            source=prior_app.source,
            linked_workflow_ids=list(prior_app.linked_workflow_ids or []),
        )

        async with sem:
            try:
                detail = await _extract_app_detail(
                    skeleton,
                    transcript_text,
                    db,
                    user,
                    session_id,
                    prior_answers=merged_answers,
                )
            except NotImplementedError:
                raise
            except Exception as exc:
                logger.warning(
                    "%s app %s partial extraction raised: %s",
                    log_ctx, prior_app.app_id, exc,
                )
                detail = {"partial_extraction_failed": True, "reason": str(exc)}

        prior_for_merge = prior_app.model_copy(
            update={"user_provided_answers": merged_answers}
        )
        return _merge_app(skeleton, detail, log_ctx=log_ctx, prior=prior_for_merge)

    workflow_tasks = [_one_workflow(wf) for wf in (prior_artifact.workflows or [])]
    app_tasks = [_one_app(app) for app in (prior_artifact.apps or [])]

    new_workflows = await asyncio.gather(*workflow_tasks)
    new_apps = await asyncio.gather(*app_tasks)
    return list(new_workflows), list(new_apps)


async def _stage_3_partial(
    new_workflows: list[Workflow],
    new_apps: list[App],
    prior_artifact: DiscoveryAnalysisV3,
    workflow_ids_to_regen: set[str],
    app_ids_to_regen: set[str],
    db,
    user,
    session_id: str,
) -> None:
    """Re-run BPMN + design-doc / EADD generation only for re-extracted entities.

    BPMN: short-circuited by ``_bpmn_content_hash``. If the workflow's
    process_steps + decision_logic hash matches what was already on the
    prior artifact, Stage 3 reuses the cached bpmn_id / bpmn_xml.

    Design doc / EADD: always re-run for entities in scope (their detail
    fields changed, so the design doc must reflect that).

    Mutates ``new_workflows`` / ``new_apps`` in place — same contract as
    the full pipeline's Stage 3.
    """
    log_ctx = f"[V3 partial Stage 3 | user={user.user_id}]"
    sem = asyncio.Semaphore(V3_FANOUT_LIMIT)

    # Index priors by ID so we can carry forward bpmn metadata for the
    # content-hash short-circuit when the LLM emitted the same steps.
    prior_workflows_by_id: dict[str, Workflow] = {
        wf.workflow_id: wf for wf in (prior_artifact.workflows or [])
    }

    # Carry forward BPMN metadata onto the new Workflow object so the
    # _generate_bpmn_for_workflow content-hash check can short-circuit.
    for wf in new_workflows:
        if wf.workflow_id in workflow_ids_to_regen:
            prior = prior_workflows_by_id.get(wf.workflow_id)
            if prior:
                wf.bpmn_id = prior.bpmn_id
                wf.bpmn_xml = prior.bpmn_xml
                wf.bpmn_status = prior.bpmn_status
                wf.bpmn_content_hash = prior.bpmn_content_hash

    async def _wf_bpmn(wf: Workflow) -> None:
        if wf.workflow_id not in workflow_ids_to_regen:
            return
        if wf.partial_extraction_failed:
            return
        async with sem:
            try:
                await _generate_bpmn_for_workflow(wf, db, user, session_id)
            except NotImplementedError:
                raise
            except Exception as exc:
                logger.warning(
                    "%s BPMN regen failed for %s: %s",
                    log_ctx, wf.workflow_id, exc,
                )
                wf.bpmn_status = "failed"

    async def _wf_design(wf: Workflow) -> None:
        if wf.workflow_id not in workflow_ids_to_regen:
            return
        if wf.partial_extraction_failed:
            return
        async with sem:
            try:
                landscape = _build_solution_landscape(
                    new_workflows, new_apps, exclude_workflow_id=wf.workflow_id,
                )
                await _generate_design_doc_for_workflow(
                    wf, db, user, session_id,
                    client_industry="",
                    stakeholders_summary="",
                    solution_landscape=landscape,
                )
            except NotImplementedError:
                raise
            except Exception as exc:
                logger.warning(
                    "%s design-doc regen failed for %s: %s",
                    log_ctx, wf.workflow_id, exc,
                )
                wf.design_doc_status = "failed"

    async def _app_eadd(app: App) -> None:
        if app.app_id not in app_ids_to_regen:
            return
        if app.partial_extraction_failed:
            return
        if getattr(app, "build_intent", "build_for_customer") != "build_for_customer":
            return
        async with sem:
            try:
                landscape = _build_solution_landscape(
                    new_workflows, new_apps, exclude_app_id=app.app_id,
                )
                await _generate_eadd_for_app(
                    app, db, user, session_id,
                    client_industry="",
                    stakeholders_summary="",
                    solution_landscape=landscape,
                )
            except NotImplementedError:
                raise
            except Exception as exc:
                logger.warning(
                    "%s EADD regen failed for %s: %s",
                    log_ctx, app.app_id, exc,
                )
                app.design_doc_status = "failed"

    tasks = (
        [_wf_bpmn(wf) for wf in new_workflows]
        + [_wf_design(wf) for wf in new_workflows]
        + [_app_eadd(app) for app in new_apps]
    )
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, NotImplementedError):
            raise r


# ──────────────────────────────────────────────────────────────────────
# Stage 1 — skeleton call
# ──────────────────────────────────────────────────────────────────────


async def _stage_1_skeleton(
    transcript_text: str,
    db,
    user,
    session_id: str,
) -> SkeletonResult:
    """Run the Stage 1 skeleton call.

    One LLM call, 180s timeout, retried once via ``run_with_retry``.
    Produces ``WorkflowSkeleton``s + ``AppSkeleton``s + ``SessionFlags``
    + identity fields. Stage 2 fan-out depends on this returning at
    least an empty workflow / app list.

    Returns the skeleton tuple. Raises if the call fails on both
    attempts — a missing skeleton means there is nothing for Stages 2-4
    to do, so the artifact never lands.
    """
    log_ctx = f"[V3 Stage 1 | session={session_id} | user={user.user_id}]"

    prompt = SKELETON_PROMPT.format(transcript_text=transcript_text)

    # Provider/model selection mirrors v2 — pull from the same emp_ai_vega
    # entity so tooling and budget tracking line up. Imported lazily so
    # this module stays importable in test environments that don't have
    # a live Mongo handle.
    from services.ai.conductor import _get_model
    provider, model = await _get_model(db, "emp_ai_vega")

    async def _call() -> Optional[dict]:
        t0 = time.monotonic()
        resp = await call_llm(
            system_prompt=(
                "You are Vega, the AI Recruiter Agent. Return strict structured JSON."
            ),
            user_message=prompt,
            provider=provider,
            model=model,
            response_schema=SKELETON_SCHEMA,
            timeout=V3_LLM_TIMEOUT,
            temperature=VEGA_LLM_TEMPERATURE,
        )
        elapsed = time.monotonic() - t0
        if not resp or not resp.text:
            logger.warning("%s empty LLM response after %.1fs", log_ctx, elapsed)
            return None
        logger.info(
            "%s skeleton LLM responded in %.1fs (%d chars)",
            log_ctx, elapsed, len(resp.text),
        )
        try:
            return json.loads(resp.text)
        except json.JSONDecodeError:
            logger.error("%s skeleton JSON parse failed: %s", log_ctx, resp.text[:500])
            return None

    raw = await run_with_retry(_call, call_num="v3.skeleton")
    if not raw:
        # Stage 1 is non-recoverable. The artifact never lands.
        raise RuntimeError("v3 Stage 1 skeleton call returned no usable output")

    return _parse_skeleton(raw, log_ctx=log_ctx)


def _parse_skeleton(raw: dict, log_ctx: str) -> SkeletonResult:
    """Coerce the raw Stage 1 JSON into typed Pydantic objects.

    Defensive: per the contract, several fields are optional. We
    tolerate missing / null entries without crashing the pipeline —
    Stage 4 surfaces specific gaps as OpenItems.
    """
    client_company = (raw.get("client_company") or "").strip()
    if not client_company:
        # Don't crash — Stage 4 will surface this as an open item.
        # The orchestrator will let an explicit ``client_company``
        # override (passed by the caller) replace this empty string.
        logger.warning("%s skeleton returned empty client_company", log_ctx)

    transcripts: list[TranscriptRef] = []
    for t in raw.get("source_transcripts") or []:
        if not isinstance(t, dict) or not t.get("filename"):
            continue
        try:
            transcripts.append(TranscriptRef(**t))
        except Exception as exc:
            logger.warning("%s dropping malformed source_transcripts entry: %s", log_ctx, exc)

    raw_flags = raw.get("session_flags") or {}
    try:
        # Coerce flag strings to VegaFlag enum members where present.
        for k in ("tech_contact_flag", "engineering_capacity_flag", "it_infosec_gate_flag"):
            v = raw_flags.get(k)
            if isinstance(v, str):
                try:
                    raw_flags[k] = VegaFlag(v)
                except ValueError:
                    logger.warning(
                        "%s session_flags.%s contained invalid VegaFlag token %r — coercing to None",
                        log_ctx, k, v,
                    )
                    raw_flags[k] = None
        session_flags = SessionFlags(**raw_flags)
    except Exception as exc:
        logger.warning("%s session_flags coercion failed (%s) — using empty default", log_ctx, exc)
        session_flags = SessionFlags()

    wf_skeletons: list[WorkflowSkeleton] = []
    for wf in raw.get("workflows") or []:
        if not isinstance(wf, dict):
            continue
        try:
            wf = _coerce_source(wf)
            wf_skeletons.append(WorkflowSkeleton(**wf))
        except Exception as exc:
            logger.warning("%s dropping malformed workflow skeleton: %s", log_ctx, exc)

    app_skeletons: list[AppSkeleton] = []
    for app in raw.get("apps") or []:
        if not isinstance(app, dict):
            continue
        try:
            app = _coerce_source(app)
            app_skeletons.append(AppSkeleton(**app))
        except Exception as exc:
            logger.warning("%s dropping malformed app skeleton: %s", log_ctx, exc)

    return (client_company, transcripts, session_flags, wf_skeletons, app_skeletons)


def _coerce_source(d: dict) -> dict:
    """Coerce a SourceProvenance dict's source_flag string to VegaFlag.

    Returns a shallow copy to avoid mutating the original.
    """
    out = dict(d)
    src = out.get("source")
    if isinstance(src, dict):
        src = dict(src)
        flag = src.get("source_flag")
        if isinstance(flag, str):
            try:
                src["source_flag"] = VegaFlag(flag)
            except ValueError:
                src["source_flag"] = None
        out["source"] = src
    elif src is None:
        # Pydantic SourceProvenance has every field optional, so a fresh
        # default is safe and lets the skeleton model validate.
        out["source"] = SourceProvenance().model_dump()
    return out


# ──────────────────────────────────────────────────────────────────────
# Stage 2 — per-workflow + per-app detail extraction
# ──────────────────────────────────────────────────────────────────────


async def _stage_2_details(
    skeleton: SkeletonResult,
    transcript_text: str,
    db,
    user,
    session_id: str,
) -> tuple[list[Workflow], list[App]]:
    """Fan out per-workflow + per-app detail calls in parallel.

    Calls Thread 2b's ``_extract_workflow_detail`` and
    ``_extract_app_detail`` hooks under a Semaphore(5). Per-call
    failures are caught and used to mark ``partial_extraction_failed``
    on the affected entity; the orchestrator never aborts the whole
    pipeline because of a single Stage 2 timeout.

    Returns merged ``Workflow`` / ``App`` lists ready for Stage 3.
    """
    log_ctx = f"[V3 Stage 2 | session={session_id} | user={user.user_id}]"
    _, _, _, wf_skeletons, app_skeletons = skeleton

    sem = asyncio.Semaphore(V3_FANOUT_LIMIT)

    async def _wf_task(wf_sk: WorkflowSkeleton):
        async with sem:
            try:
                return await _extract_workflow_detail(
                    wf_sk, transcript_text, db, user, session_id,
                )
            except NotImplementedError:
                # Re-raise so the test suite can detect the unimplemented
                # state. In production with 2b landed, this branch is dead.
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
                return await _extract_app_detail(
                    app_sk, transcript_text, db, user, session_id,
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

    # gather() with return_exceptions=True so a single failure can't sink
    # the whole fan-out. Each task already wraps its own try/except, but
    # the safety net guards against asyncio internals.
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


def _merge_workflow(
    sk: WorkflowSkeleton,
    detail: Any,
    log_ctx: str,
    prior: Optional[Workflow] = None,
) -> Workflow:
    """Merge a WorkflowSkeleton + WorkflowDetail (or failure dict) into Workflow.

    On any failure mode we still emit a Workflow with the skeleton fields
    populated so the renderer can show the entity even when the deep
    extraction failed. Stage 4 sees the failure flag and surfaces an
    OpenItem.

    ``prior`` is the previously-merged Workflow from an earlier pipeline
    run — used by Wave 5b's partial regeneration mode to carry forward
    user-supplied answers (``user_provided_answers``) into the new merge.
    Strict accumulator: keys present in ``prior.user_provided_answers``
    are preserved; the LLM cannot overwrite a user answer.
    """
    carried_answers: dict[str, str] = (
        dict(prior.user_provided_answers) if prior and prior.user_provided_answers else {}
    )

    base_kwargs = dict(
        workflow_id=sk.workflow_id,
        workflow_name=sk.workflow_name,
        complexity=sk.complexity,
        source=sk.source,
        linked_app_ids=list(sk.linked_app_ids),
        mmi_hypothesis=_seed_mmi_from_skeleton(sk),
        user_provided_answers=carried_answers,
    )

    if isinstance(detail, Exception):
        logger.warning(
            "%s workflow %s gather raised: %s — keeping skeleton only",
            log_ctx, sk.workflow_id, detail,
        )
        return Workflow(
            **base_kwargs,
            partial_extraction_failed=True,
            partial_extraction_reason=f"gather error: {detail!r}",
        )

    if isinstance(detail, dict) and detail.get("partial_extraction_failed"):
        return Workflow(
            **base_kwargs,
            partial_extraction_failed=True,
            partial_extraction_reason=detail.get("reason"),
        )

    # Normal success path — Thread 2b returns a WorkflowDetail.
    if hasattr(detail, "model_dump"):
        d = detail.model_dump()
    elif isinstance(detail, dict):
        d = detail
    else:
        # Defensive: Thread 2b contract says WorkflowDetail | dict.
        # Anything else is a programmer error in the hook.
        return Workflow(
            **base_kwargs,
            partial_extraction_failed=True,
            partial_extraction_reason=f"unexpected detail type {type(detail).__name__}",
        )

    # If 2b returned a full MMIHypothesis on the detail, prefer it.
    if d.get("mmi_hypothesis"):
        base_kwargs["mmi_hypothesis"] = d["mmi_hypothesis"]
    return Workflow(
        **base_kwargs,
        acceptance_criteria=d.get("acceptance_criteria") or [],
        decision_logic=d.get("decision_logic") or [],
        process_steps=d.get("process_steps") or [],
        persona_depth_app_id=d.get("persona_depth_app_id"),
        persona_overrides=d.get("persona_overrides") or [],
    )


def _merge_app(
    sk: AppSkeleton,
    detail: Any,
    log_ctx: str,
    prior: Optional[App] = None,
) -> App:
    """Merge an AppSkeleton + AppDetail (or failure dict) into App.

    ``prior`` is the previously-merged App from an earlier pipeline run —
    used by Wave 5b's partial regeneration mode to carry forward user-
    supplied answers (``user_provided_answers``) into the new merge.
    Strict accumulator: the LLM cannot overwrite a user answer.
    """
    carried_answers: dict[str, str] = (
        dict(prior.user_provided_answers) if prior and prior.user_provided_answers else {}
    )

    base_kwargs = dict(
        app_id=sk.app_id,
        app_name=sk.app_name,
        complexity=sk.complexity,
        source=sk.source,
        linked_workflow_ids=list(sk.linked_workflow_ids),
        user_provided_answers=carried_answers,
    )

    if isinstance(detail, Exception):
        logger.warning(
            "%s app %s gather raised: %s — keeping skeleton only",
            log_ctx, sk.app_id, detail,
        )
        return App(
            **base_kwargs,
            partial_extraction_failed=True,
            partial_extraction_reason=f"gather error: {detail!r}",
        )

    if isinstance(detail, dict) and detail.get("partial_extraction_failed"):
        return App(
            **base_kwargs,
            partial_extraction_failed=True,
            partial_extraction_reason=detail.get("reason"),
        )

    if hasattr(detail, "model_dump"):
        d = detail.model_dump()
    elif isinstance(detail, dict):
        d = detail
    else:
        return App(
            **base_kwargs,
            partial_extraction_failed=True,
            partial_extraction_reason=f"unexpected detail type {type(detail).__name__}",
        )

    return App(
        **base_kwargs,
        personas=d.get("personas") or [],
        integrations=d.get("integrations") or [],
        scale_profile=d.get("scale_profile") or {},
        nfrs=d.get("nfrs") or {},
        additional_source_quotes=d.get("additional_source_quotes") or [],
    )


def _seed_mmi_from_skeleton(sk: WorkflowSkeleton) -> MMIHypothesis:
    """Build a low-confidence MMIHypothesis from the skeleton's mmi_dims.

    Used as the merged Workflow's seed when Stage 2a fails or is still
    pending. Stage 2a normally replaces this with a full reasoning-rich
    breakdown.
    """
    dims = sk.mmi_dims or {}

    def _dim(key: str) -> MMIDimension:
        value = dims.get(key)
        if not isinstance(value, int):
            value = 1
        return MMIDimension(
            value=value,
            confidence="low",
            reasoning="Skeleton-stage seed — Stage 2a did not deepen this dimension.",
        )

    return MMIHypothesis(
        tss=_dim("tss"),
        ie=_dim("ie"),
        time=_dim("time"),
        cost=_dim("cost"),
        quality=_dim("quality"),
        satisfaction=_dim("satisfaction"),
        needs_client_input=[],
        score=None,
    )


# ──────────────────────────────────────────────────────────────────────
# Stage 3 — BPMN + design docs (parallel fan-out)
# ──────────────────────────────────────────────────────────────────────


def _build_solution_landscape(
    workflows: list[Workflow],
    apps: list[App],
    *,
    exclude_workflow_id: Optional[str] = None,
    exclude_app_id: Optional[str] = None,
) -> str:
    """Build a compact human-readable summary of every workflow + app in
    the artifact, suitable for threading into the design-doc and EADD
    prompts. Exclude the entity currently being designed so the prompt's
    "OTHER workflows and apps" framing stays accurate.

    Existing-tool apps (build_intent != "build_for_customer") are
    omitted: they're integrations, not part of the SimpliMeta-built
    solution surface, and listing them would invite the LLM to design
    handoffs we're not building.
    """
    lines: list[str] = []
    for wf in workflows or []:
        if exclude_workflow_id and wf.workflow_id == exclude_workflow_id:
            continue
        bits = [f"AI Workflow: {wf.workflow_name} (id={wf.workflow_id})"]
        if wf.complexity:
            bits.append(f"complexity={wf.complexity}")
        # First acceptance criterion gives a quick "what does it do" hint.
        ac = (wf.acceptance_criteria or [None])[0]
        if ac and getattr(ac, "flow_name", None):
            bits.append(f"primary flow: {ac.flow_name}")
        lines.append("- " + " | ".join(bits))

    for app in apps or []:
        if exclude_app_id and app.app_id == exclude_app_id:
            continue
        if getattr(app, "build_intent", "build_for_customer") != "build_for_customer":
            continue
        bits = [f"App: {app.app_name} (id={app.app_id})"]
        if app.complexity:
            bits.append(f"complexity={app.complexity}")
        if app.linked_workflow_ids:
            bits.append(f"supports workflows: {', '.join(app.linked_workflow_ids)}")
        lines.append("- " + " | ".join(bits))

    return "\n".join(lines) if lines else ""


async def _stage_3_bpmn_and_design_docs(
    workflows: list[Workflow],
    apps: list[App],
    db,
    user,
    session_id: str,
    client_industry: str = "",
    stakeholders_summary: str = "",
) -> None:
    """Fan out BPMN + design-doc generation across workflows + apps.

    Mutates the workflow / app objects in place (adds bpmn_id, bpmn_xml,
    design_doc, eadd_doc, etc.). Per-entity failures are caught and the
    entity is left in a safe degraded state rather than aborting.

    ``client_industry`` and ``stakeholders_summary`` are forwarded to the
    design-doc and EADD generators. In v3 they default to empty strings
    until Stage 1/2 extracts those values (see caller TODO).

    Each design-doc + EADD call also receives a ``solution_landscape``
    summary of the OTHER workflows + apps in this artifact so the LLM
    can write explicit cross-system handoffs (which workflows feed which
    apps, what data is exchanged, etc.).

    Reuses Thread 2d's hooks: ``_generate_bpmn_for_workflow``,
    ``_generate_design_doc_for_workflow``, ``_generate_eadd_for_app``.
    """
    log_ctx = f"[V3 Stage 3 | session={session_id} | user={user.user_id}]"
    sem = asyncio.Semaphore(V3_FANOUT_LIMIT)

    async def _wf_bpmn(wf: Workflow) -> None:
        if wf.partial_extraction_failed:
            # No detail to diagram — skip. Leave bpmn_status None so the
            # renderer's banner explains why.
            return
        async with sem:
            try:
                await _generate_bpmn_for_workflow(wf, db, user, session_id)
            except NotImplementedError:
                raise
            except Exception as exc:
                logger.warning(
                    "%s BPMN generation failed for %s: %s",
                    log_ctx, wf.workflow_id, exc,
                )
                wf.bpmn_status = "failed"

    async def _wf_design(wf: Workflow) -> None:
        if wf.partial_extraction_failed:
            return
        async with sem:
            try:
                landscape = _build_solution_landscape(
                    workflows, apps, exclude_workflow_id=wf.workflow_id,
                )
                await _generate_design_doc_for_workflow(
                    wf, db, user, session_id,
                    client_industry=client_industry,
                    stakeholders_summary=stakeholders_summary,
                    solution_landscape=landscape,
                )
            except NotImplementedError:
                raise
            except Exception as exc:
                logger.warning(
                    "%s design-doc generation failed for %s: %s",
                    log_ctx, wf.workflow_id, exc,
                )
                wf.design_doc_status = "failed"

    async def _app_eadd(app: App) -> None:
        if app.partial_extraction_failed:
            return
        # Wave 7 fix — never burn a Stage 3 EADD on an app the customer
        # didn't ask us to build. ``existing_tool`` apps (Google Sheets,
        # Salesforce, Slack, etc.) shouldn't normally make it to apps[]
        # at all, but defense-in-depth: even if Stage 1 lets one slip
        # through, Stage 3 refuses to write a design doc for it.
        if getattr(app, "build_intent", "build_for_customer") != "build_for_customer":
            logger.info(
                "%s skipping EADD for %s (build_intent=%s — not an app we are building)",
                log_ctx, app.app_id, getattr(app, "build_intent", None),
            )
            return
        async with sem:
            try:
                landscape = _build_solution_landscape(
                    workflows, apps, exclude_app_id=app.app_id,
                )
                await _generate_eadd_for_app(
                    app, db, user, session_id,
                    client_industry=client_industry,
                    stakeholders_summary=stakeholders_summary,
                    solution_landscape=landscape,
                )
            except NotImplementedError:
                raise
            except Exception as exc:
                logger.warning(
                    "%s EADD generation failed for %s: %s",
                    log_ctx, app.app_id, exc,
                )
                app.design_doc_status = "failed"

    tasks = (
        [_wf_bpmn(wf) for wf in workflows]
        + [_wf_design(wf) for wf in workflows]
        + [_app_eadd(app) for app in apps]
    )
    # return_exceptions=True so per-entity failures don't tear down the
    # other tasks in flight. We then walk the results and re-raise any
    # NotImplementedError so the wiring remains visible — Stage 3 hooks
    # in development raise NotImplementedError, and silently swallowing
    # that would mask "this stage isn't actually running" during smoke
    # tests. All other Exceptions are already handled inside the closures
    # (they mark the entity and return), so they shouldn't surface here.
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, NotImplementedError):
            raise r


# ──────────────────────────────────────────────────────────────────────
# Stage 4 — pure-Python post-processing
# ──────────────────────────────────────────────────────────────────────


def _stage_4_postprocess(
    workflows: list[Workflow],
    apps: list[App],
    session_flags: SessionFlags,
) -> tuple[list[SharedInfrastructureRow], list[OpenItem]]:
    """Run deterministic post-processing.

    Hooks into Thread 2c's pure functions. No LLM calls. Bugs here are
    programmer error and propagate. extraction_status is derived by the
    orchestrator after this returns — Stage 4 doesn't own that decision.
    """
    shared_infra = _detect_shared_infrastructure(workflows, apps)
    open_items = _aggregate_open_items(workflows, apps, session_flags)
    return shared_infra, open_items


# ──────────────────────────────────────────────────────────────────────
# Stage hook stubs — owned by other Wave 2 threads
# ──────────────────────────────────────────────────────────────────────


# Thread 2b implements the two Stage 2 detail extractors below. Until
# those land, calling the orchestrator surfaces NotImplementedError so
# the test suite can detect the wiring.

async def _extract_workflow_detail(
    skeleton: WorkflowSkeleton,
    transcript_text: str,
    db,
    user,
    session_id: str,
    prior_answers: Optional[dict[str, str]] = None,
):
    """Per-workflow Stage 2a detail extractor.

    One LLM call per workflow, 180s timeout, retried once via
    ``run_with_retry``.  Fills ``WorkflowDetail`` fields (acceptance
    criteria, decision logic, process steps, persona pointer) on top of
    the skeleton that Stage 1 produced.

    ``prior_answers`` is the partial-regeneration accumulator (Wave 5b).
    When non-empty, formatted answers are injected into the prompt so
    the LLM treats them as authoritative and stops re-flagging the same
    gap. First-pass callers pass None and behaviour is unchanged.

    Returns:
      - ``WorkflowDetail`` on success.
      - ``{"partial_extraction_failed": True, "reason": str}`` on
        retry-then-fail.  NEVER raises — the orchestrator depends on
        this contract.
    """
    log_ctx = (
        f"[V3 Stage 2a | workflow={skeleton.workflow_id}"
        f" | session={session_id} | user={user.user_id}]"
    )

    prompt = WORKFLOW_DETAIL_PROMPT.format(
        workflow_id=skeleton.workflow_id,
        workflow_name=skeleton.workflow_name,
        skeleton_json=json.dumps(skeleton.model_dump(), default=str, indent=2),
        transcript_text=transcript_text,
        prior_answers_block=format_prior_answers_block(prior_answers),
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
            response_schema=WORKFLOW_DETAIL_SCHEMA,
            timeout=V3_LLM_TIMEOUT,
            temperature=VEGA_LLM_TEMPERATURE,
        )
        elapsed = time.monotonic() - t0
        if not resp or not resp.text:
            logger.warning(
                "%s empty LLM response after %.1fs", log_ctx, elapsed
            )
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
        raw = await run_with_retry(_call, call_num=f"v3.wf_detail.{skeleton.workflow_id}")
        if not raw:
            return {
                "partial_extraction_failed": True,
                "reason": "workflow detail call returned no usable output after retry",
            }
        detail = WorkflowDetail.model_validate(raw)
        logger.info("%s workflow detail validated OK", log_ctx)
        return detail
    except Exception as exc:
        reason = str(exc)[:200]
        logger.warning("%s workflow detail failed: %s", log_ctx, reason)
        return {"partial_extraction_failed": True, "reason": reason}


async def _extract_app_detail(
    skeleton: AppSkeleton,
    transcript_text: str,
    db,
    user,
    session_id: str,
    prior_answers: Optional[dict[str, str]] = None,
):
    """Per-app Stage 2b detail extractor.

    One LLM call per app, 180s timeout, retried once via
    ``run_with_retry``.  Fills ``AppDetail`` fields (personas,
    integrations, scale profile, NFRs, additional source quotes) on top
    of the skeleton that Stage 1 produced.

    ``prior_answers`` is the partial-regeneration accumulator (Wave 5b).
    When non-empty, formatted answers are injected into the prompt so
    the LLM treats them as authoritative and stops re-flagging the same
    gap. First-pass callers pass None and behaviour is unchanged.

    Returns:
      - ``AppDetail`` on success.
      - ``{"partial_extraction_failed": True, "reason": str}`` on
        retry-then-fail.  NEVER raises — the orchestrator depends on
        this contract.
    """
    log_ctx = (
        f"[V3 Stage 2b | app={skeleton.app_id}"
        f" | session={session_id} | user={user.user_id}]"
    )

    prompt = APP_DETAIL_PROMPT.format(
        app_id=skeleton.app_id,
        app_name=skeleton.app_name,
        skeleton_json=json.dumps(skeleton.model_dump(), default=str, indent=2),
        transcript_text=transcript_text,
        prior_answers_block=format_prior_answers_block(prior_answers),
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
            response_schema=APP_DETAIL_SCHEMA,
            timeout=V3_LLM_TIMEOUT,
            temperature=VEGA_LLM_TEMPERATURE,
        )
        elapsed = time.monotonic() - t0
        if not resp or not resp.text:
            logger.warning(
                "%s empty LLM response after %.1fs", log_ctx, elapsed
            )
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
        raw = await run_with_retry(_call, call_num=f"v3.app_detail.{skeleton.app_id}")
        if not raw:
            return {
                "partial_extraction_failed": True,
                "reason": "app detail call returned no usable output after retry",
            }
        detail = AppDetail.model_validate(raw)
        logger.info("%s app detail validated OK", log_ctx)
        return detail
    except Exception as exc:
        reason = str(exc)[:200]
        logger.warning("%s app detail failed: %s", log_ctx, reason)
        return {"partial_extraction_failed": True, "reason": reason}


# Thread 2c implements the three Stage 4 helpers below.

def _detect_shared_infrastructure(
    workflows: list[Workflow],
    apps: list[App],
) -> list[SharedInfrastructureRow]:
    """II.2 — Stage 4 hook. Implemented in services.vega.postprocess.

    Scans App.integrations across all apps and emits a row for any
    system_name appearing in 2+ apps. Returns [] when fewer than 2 apps
    share any integration.
    """
    return _detect_shared_infrastructure_impl(workflows, apps)


def _aggregate_open_items(
    workflows: list[Workflow],
    apps: list[App],
    session_flags: SessionFlags,
) -> list[OpenItem]:
    """Stage 4 hook — implemented in services.vega.postprocess.

    Walks every flagged field in workflows + apps + session_flags and
    emits one OpenItem per flagged value with a structured location
    pointer ('session', 'workflow:<id>', 'app:<id>').
    """
    return _aggregate_open_items_impl(workflows, apps, session_flags)


# Thread 2d implements the three Stage 3 generators below.

async def _generate_bpmn_for_workflow(
    workflow: Workflow,
    db,
    user,
    session_id: str,
) -> Workflow:
    """Stage 3 BPMN hook — Thread 2d.

    Translates the v3 Workflow model into the v2 dict shape that
    ``generate_workflow_diagram`` expects, calls the renderer, persists
    the diagram to ``bpmn_diagrams``, and embeds the XML on the workflow
    model so the artifact is self-contained (Samson's II.10 requirement).

    100KB guardrail: if the XML exceeds 100_000 bytes the inline copy is
    dropped (bpmn_xml = None) but bpmn_id is kept so the renderer can
    fall back to fetching by ID for large diagrams.

    Failure path: any exception sets bpmn_status="failed", logs via
    logger.exception, and returns the workflow unchanged — never raises.
    """
    wf_id = workflow.workflow_id
    log_ctx = (
        f"[V3 Stage 3 | bpmn | workflow={wf_id} | session={session_id} | user={user.user_id}]"
    )
    try:
        workflow_data = _workflow_to_v2_dict(workflow)
        new_hash = _bpmn_content_hash(workflow_data)

        # Skip regen if a previous run already produced a matching hash.
        # On first run bpmn_content_hash is None, so this guard fires only
        # during re-runs where Stage 3 is re-entered (e.g., retry logic).
        if workflow.bpmn_content_hash and workflow.bpmn_content_hash == new_hash:
            logger.info(
                "%s content_hash match — reusing existing bpmn_id=%s",
                log_ctx, workflow.bpmn_id,
            )
            return workflow

        result = await generate_workflow_diagram(workflow_data, db)

        if not result.get("success"):
            error_msg = result.get("error", "unknown error")
            logger.warning("%s generate_workflow_diagram failed: %s", log_ctx, error_msg)
            workflow.bpmn_status = "failed"
            return workflow

        bpmn_id = result["bpmn_id"]
        bpmn_xml: str = result["bpmn_xml"]

        # Persist to bpmn_diagrams collection (mirrors v2's conversational_tools pattern).
        bpmn_doc = {
            "bpmn_id": bpmn_id,
            "workflow_id": wf_id,
            "workflow_name": workflow.workflow_name,
            "bpmn_xml": bpmn_xml,
            "validation_warnings": result.get("validation_warnings", []),
            "created_by": user.user_id,
            "created_by_agent": "ai_vega_v3",
            "session_id": session_id,
            "version": 1,
            "edited_xml": None,
            "edited_at": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.bpmn_diagrams.insert_one(bpmn_doc)

        # 100KB guardrail — embed XML inline only when it fits.
        xml_bytes = len(bpmn_xml.encode("utf-8"))
        if xml_bytes > 100_000:
            logger.warning(
                "[V3 Stage 3 | workflow=%s] bpmn_xml is %d bytes — over 100KB cap; "
                "dropping inline copy, bpmn_id retained for fetch-by-id fallback",
                wf_id, xml_bytes,
            )
            workflow.bpmn_xml = None
        else:
            workflow.bpmn_xml = bpmn_xml

        workflow.bpmn_id = bpmn_id
        workflow.bpmn_status = "generated"
        workflow.bpmn_content_hash = new_hash
        logger.info("%s BPMN generated (bpmn_id=%s, xml_bytes=%d)", log_ctx, bpmn_id, xml_bytes)

    except Exception:
        logger.exception("%s BPMN generation raised — marking failed", log_ctx)
        workflow.bpmn_status = "failed"

    return workflow


def _workflow_to_v2_dict(workflow: Workflow) -> dict:
    """Translate a v3 Workflow model into the v2 dict shape the BPMN pipeline expects.

    The v2 BPMN generator (``generate_workflow_diagram``) requires these top-level
    keys: ``workflow_id``, ``workflow_name``, ``actors``, ``trigger``, ``steps``,
    ``decision_points``, ``exception_paths``.

    v3→v2 field mapping decisions:
    - ``process_steps`` → ``steps``: v3 ProcessStep carries the same
      sub-fields (id, name, actor_id, task_type, description) the v2 step
      schema expects, so a direct model_dump() works.
    - ``decision_logic`` → ``decision_points``: DecisionPoint mirrors v2's
      shape exactly (id, after_step_id, type, name, branches).
    - ``actors``: v3 has no actor list on Workflow (actors live on ProcessStep
      via actor_id). We synthesise a minimal actor roster by collecting unique
      actor_id values from process_steps.
      TODO(2a-contract): v3 WorkflowDetail should add an ``actors`` list so
      the BPMN renderer can attach role names and types (human/ai/external_system)
      rather than deriving them synthetically here.
    - ``trigger``: v3 has no explicit trigger field.
      TODO(2a-contract): add a ``trigger`` field (type + description + actor_id)
      to WorkflowDetail so Stage 3 can pass it verbatim to the renderer.
      For now we emit a synthetic "manual" trigger so the renderer doesn't reject
      the payload for a missing required key.
    - ``exception_paths``: v3 has no exception paths.
      TODO(2a-contract): add ``exception_paths`` to WorkflowDetail.
      Passed as [] for now — the BPMN renderer handles an empty list cleanly.
    - v3-only fields (acceptance_criteria, persona_overrides, mmi_hypothesis,
      source, etc.) are intentionally omitted — the BPMN renderer doesn't know
      about them and _bpmn_content_hash only hashes actors/steps/decision_points/
      exception_paths, so they don't affect regen-deduplication.
    """
    steps = [s.model_dump() for s in (workflow.process_steps or [])]
    decision_points = [d.model_dump() for d in (workflow.decision_logic or [])]

    # Derive a minimal actor roster from step actor_ids.
    seen_actor_ids: set[str] = set()
    actors: list[dict] = []
    for step in steps:
        actor_id = step.get("actor_id")
        if actor_id and actor_id not in seen_actor_ids:
            seen_actor_ids.add(actor_id)
            # TODO(2a-contract): actor type/name should come from WorkflowDetail.actors.
            # For now we default to "human" so the BPMN renderer assigns a swimlane.
            actors.append({"id": actor_id, "name": actor_id, "type": "human"})

    # Synthetic trigger — required by the v2 BPMN schema.
    # TODO(2a-contract): source a real trigger from WorkflowDetail once the field exists.
    trigger = {
        "type": "manual",
        "description": f"Trigger for {workflow.workflow_name}",
        "actor_id": actors[0]["id"] if actors else "unassigned",
    }

    return {
        "workflow_id": workflow.workflow_id,
        "workflow_name": workflow.workflow_name,
        "actors": actors,
        "trigger": trigger,
        "steps": steps,
        "decision_points": decision_points,
        "exception_paths": [],  # TODO(2a-contract): add exception_paths to WorkflowDetail
    }


async def _generate_design_doc_for_workflow(
    workflow: Workflow,
    db,
    user,
    session_id: str,
    client_industry: str = "",
    stakeholders_summary: str = "",
    solution_landscape: str = "",
) -> Workflow:
    """Stage 3 design-doc hook — Thread 2d.

    Calls v2's ``_generate_single_design_doc`` using the v3 Workflow
    serialised as a dict, then stores the result inline on
    ``workflow.design_doc``.

    ``client_industry`` and ``stakeholders_summary`` are passed through
    from the orchestrator call site (see ``_stage_3_bpmn_and_design_docs``).
    They default to empty strings — the v2 generator treats those as
    "(unspecified)" and continues gracefully.

    Failure path: any exception logs via logger.exception, leaves
    ``workflow.design_doc = None``, and returns the workflow — never raises.
    """
    wf_id = workflow.workflow_id
    log_ctx = (
        f"[V3 Stage 3 | design_doc | workflow={wf_id} | session={session_id} | user={user.user_id}]"
    )
    try:
        wf_dict = workflow.model_dump(mode="python")
        doc = await _generate_single_design_doc(
            wf_dict,
            client_industry,
            stakeholders_summary,
            db,
            user,
            session_id,
            solution_landscape=solution_landscape,
        )
        if doc and doc.get("status") != "failed":
            workflow.design_doc = doc
            workflow.design_doc_status = "generated"
            logger.info("%s design doc generated (status=%s)", log_ctx, doc.get("status"))
        else:
            reason = (doc or {}).get("failure_reason", "no doc returned")
            logger.warning("%s design doc failed — %s", log_ctx, reason)
            workflow.design_doc = None
            workflow.design_doc_status = "failed"
    except Exception:
        logger.exception("%s design doc generation raised — leaving design_doc=None", log_ctx)
        workflow.design_doc = None
        workflow.design_doc_status = "failed"

    return workflow


async def _generate_eadd_for_app(
    app: App,
    db,
    user,
    session_id: str,
    matrix: list | None = None,
    client_industry: str = "",
    stakeholders_summary: str = "",
    solution_landscape: str = "",
) -> App:
    """Stage 3 EADD hook — Thread 2d.

    Calls v2's ``_generate_single_eadd`` using the v3 App serialised as
    a dict, then stores the result inline on ``app.eadd_doc``.

    ``matrix`` is the v2 AI Employee Matrix list. In the v3 pipeline we
    pass an empty list — ``_generate_single_eadd`` uses it to summarise
    linked workflows for the EADD prompt, and an empty list causes it to
    emit "(no linked workflows)" rather than crashing.
    TODO(orchestrator): when the v3 pipeline has a materialized matrix,
    pass it here for richer EADD prompts.

    ``client_industry`` and ``stakeholders_summary`` default to empty
    strings — v2 generator treats those as "(unspecified)" and continues.

    Failure path: any exception logs via logger.exception, leaves
    ``app.eadd_doc = None``, and returns the app — never raises.
    """
    app_id = app.app_id
    log_ctx = (
        f"[V3 Stage 3 | eadd | app={app_id} | session={session_id} | user={user.user_id}]"
    )
    try:
        app_dict = app.model_dump(mode="python")
        doc = await _generate_single_eadd(
            app_dict,
            matrix or [],
            client_industry,
            stakeholders_summary,
            db,
            user,
            session_id,
            solution_landscape=solution_landscape,
        )
        if doc and doc.get("status") != "failed":
            app.eadd_doc = doc
            app.design_doc_status = "generated"
            logger.info("%s EADD generated (status=%s)", log_ctx, doc.get("status"))
        else:
            reason = (doc or {}).get("failure_reason", "no doc returned")
            logger.warning("%s EADD failed — %s", log_ctx, reason)
            app.eadd_doc = None
            app.design_doc_status = "failed"
    except Exception:
        logger.exception("%s EADD generation raised — leaving eadd_doc=None", log_ctx)
        app.eadd_doc = None
        app.design_doc_status = "failed"

    return app


__all__ = [
    "V3_LLM_TIMEOUT",
    "V3_FANOUT_LIMIT",
    "run_v3_pipeline",
    "run_v3_pipeline_partial",
    # Stage 2b/2c/2d hook seams — exported so the implementers can patch
    # them via ``services.vega.v3_pipeline._extract_workflow_detail = ...``
    # without monkey-patching the module twice.
    "_extract_workflow_detail",
    "_extract_app_detail",
    "_detect_shared_infrastructure",
    "_aggregate_open_items",
    "_generate_bpmn_for_workflow",
    "_generate_design_doc_for_workflow",
    "_generate_eadd_for_app",
]
