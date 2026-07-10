"""Vega v3 regenerate-from-open-items endpoint (Wave 5 / Thread 5c).

When a customer answers an OpenItem (manually or by uploading more
transcript), we don't re-run the entire pipeline. Wave 5a's blast-radius
helpers tell us *which* workflows / apps need re-extracting; Wave 5b's
``run_v3_pipeline_partial`` runs only those. This endpoint glues the two
together, persists the result as a new artifact version, and updates the
chat artifact pointer so the UI shows the latest by default.

Endpoint contract:

    POST /api/vega-artifacts/{artifact_id}/regenerate
        body: {answers: [{open_item_index, value}], new_upload_ids?: [str]}
        returns: {artifact_id, new_version, regen_scope, extraction_status}

IDOR guard: regenerate is owner-only. Audit users can view via the export
endpoints but cannot mutate, so this endpoint does not honour the audit
override that some read endpoints carry.

Test-friendly imports: ``get_db`` is aliased as ``_get_db`` at module load
so test code can monkeypatch ``routes.vega_v3_regenerate._get_db`` to
inject a mock client. Wave 4a's review noted that ``from database import
get_db`` makes patching brittle; this pattern fixes it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth_helper import get_current_user, has_perm
from database import get_db as _get_db
from services.vega.models_v3 import DiscoveryAnalysisV3, OpenItem
from services.vega.regenerate import (
    VEGA_ARTIFACT_VERSIONS_COLLECTION,
    compute_combined_blast_radius,
    persist_artifact_version,
)
from services.vega_events import emit_vega_event

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Wave 5b binding — bound at module load so tests can monkeypatch.
# ---------------------------------------------------------------------------
#
# We bind ``run_v3_pipeline_partial`` as a module-level name so tests can
# replace it with ``monkeypatch.setattr(routes.vega_v3_regenerate,
# "run_v3_pipeline_partial", stub)``. Importing inside the handler would
# defeat that — the patched module attribute would never be read.
#
# 5b is in flight in parallel; if its symbol isn't available yet we bind
# a placeholder that raises a clear error instead of failing at import.
try:
    from services.vega.v3_pipeline import run_v3_pipeline_partial  # type: ignore
except ImportError:  # pragma: no cover — exercised during the wave overlap
    async def run_v3_pipeline_partial(*args, **kwargs):  # type: ignore[no-redef]
        raise HTTPException(
            status_code=503,
            detail="run_v3_pipeline_partial is not yet available — Wave 5b in progress",
        )


# Soft cap on regenerations per artifact. Past this, the user has to open
# a fresh discovery session — keeps storage and per-session LLM spend
# bounded. Bump in one place when policy changes.
MAX_REGENERATIONS = 10


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class AnsweredItem(BaseModel):
    """One answered OpenItem in the regenerate request body.

    ``open_item_index`` is zero-based against the prior artifact's
    ``open_items`` list. Stale indexes (e.g., the user submitted against
    a previous version) raise 400 from the endpoint.
    """

    open_item_index: int = Field(
        ...,
        description="Zero-based index into the prior artifact's open_items list.",
    )
    value: str = Field(
        ...,
        description="The customer's answer text. Stored verbatim into the entity's "
        "user_provided_answers map and replayed into the next LLM prompt.",
    )


class RegenerateRequest(BaseModel):
    answers: list[AnsweredItem] = Field(
        ...,
        min_length=1,
        description="One or more answered open items. Empty payload is rejected — "
        "there is nothing for the pipeline to re-run.",
    )
    new_upload_ids: Optional[list[str]] = Field(
        default=None,
        description="Optional newly-uploaded transcript IDs (chat_uploads.upload_id) to "
        "feed into the partial re-run. Each must be owned by the requesting user.",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _load_v3_artifact_for_owner(db, artifact_id: str, user_id: str) -> dict:
    """Owner-scoped artifact lookup. Mirrors ``vega_v3_exports._load_v3_artifact``.

    Differences from the export-side helper:
      * No audit-role bypass — regenerate is owner-only.
      * Returns the canonical ai_chat_artifacts row; the caller is
        responsible for resolving the latest version separately.
    """
    art = await db.ai_chat_artifacts.find_one({"artifact_id": artifact_id}, {"_id": 0})
    if not art:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if art.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="You do not own this artifact")
    if art.get("artifact_type") != "discovery_analysis_v3":
        raise HTTPException(
            status_code=400,
            detail=f"Artifact is not a v3 discovery analysis "
            f"(got {art.get('artifact_type')!r})",
        )
    return art


async def _verify_uploads_ownership(db, upload_ids: list[str], user_id: str) -> None:
    """Verify every upload_id exists in chat_uploads and is owned by user_id.

    Mirrors the IDOR-safe lookup pattern in
    ``services/ai/vega_analysis._load_transcripts``: every chat_uploads
    read must be filtered by user_id, never by upload_id alone. A
    cross-user upload_id (or any nonexistent ID) raises 403 — we don't
    distinguish "not yours" from "doesn't exist" so attackers cannot
    enumerate IDs through the error channel.
    """
    if not upload_ids:
        return
    for uid in upload_ids:
        doc = await db.chat_uploads.find_one(
            {"upload_id": uid, "user_id": user_id},
            {"_id": 0, "upload_id": 1},
        )
        if not doc:
            raise HTTPException(
                status_code=403,
                detail=f"Upload {uid!r} not found or not owned by current user",
            )


async def _resolve_parent_version(db, artifact_id: str) -> Optional[int]:
    """Find the highest existing version number for this artifact, or None.

    ``persist_artifact_version`` allocates the *next* version atomically
    via a counter; we just need the parent linkage for the audit trail.
    Returning None is correct on the very first regen — the prior payload
    is the unversioned canonical artifact.
    """
    cursor = db[VEGA_ARTIFACT_VERSIONS_COLLECTION].find(
        {"artifact_id": artifact_id},
        {"_id": 0, "version": 1},
    ).sort("version", -1).limit(1)
    docs = await cursor.to_list(length=1)
    if not docs:
        return None
    return int(docs[0]["version"])


async def _count_existing_versions(db, artifact_id: str) -> int:
    """Count rows in vega_artifact_versions for this artifact.

    Used for the soft cap. The canonical (pre-regen) artifact lives in
    ``ai_chat_artifacts`` and is *not* counted in this collection — so
    "more than 9 versions in vega_artifact_versions" means the user has
    already requested 10 regenerations on top of the original.
    """
    return await db[VEGA_ARTIFACT_VERSIONS_COLLECTION].count_documents(
        {"artifact_id": artifact_id}
    )


def _coerce_open_items(raw_items: list[dict]) -> list[OpenItem]:
    """Pydantic-validate the prior artifact's open_items list.

    Persisted artifacts store dicts; we need OpenItem instances to call
    into ``compute_blast_radius``. Pydantic raises ValidationError on bad
    shapes — the endpoint wraps that as HTTP 500 because it indicates
    artifact corruption, not a user input error.
    """
    return [OpenItem.model_validate(item) for item in raw_items]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/vega-artifacts/{artifact_id}/regenerate")
async def regenerate_v3_artifact(
    artifact_id: str,
    body: RegenerateRequest,
    current_user=Depends(get_current_user),
):
    """Regenerate an Vega v3 artifact in response to answered open items.

    Flow:
      1. Permission + IDOR + artifact_type checks.
      2. Soft cap (MAX_REGENERATIONS) on per-artifact regen count.
      3. Validate every open_item_index against the prior artifact.
      4. Verify ownership of any new_upload_ids.
      5. Compute combined blast radius (Wave 5a).
      6. Run Wave 5b's partial pipeline.
      7. Persist a new vega_artifact_versions row.
      8. Update the canonical ai_chat_artifacts payload to the new version.
      9. Emit telemetry events.
    """
    if not has_perm(current_user, "vega.use"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = _get_db()
    art = await _load_v3_artifact_for_owner(db, artifact_id, current_user.user_id)

    # Soft cap. Counter is on the regen-version collection only; the
    # original artifact does not occupy a slot, so > MAX_REGENERATIONS - 1
    # means "already used all 10 regens".
    existing_versions = await _count_existing_versions(db, artifact_id)
    if existing_versions > (MAX_REGENERATIONS - 1):
        raise HTTPException(
            status_code=400,
            detail="Regeneration cap reached — open a new session to continue.",
        )

    payload_dict = art.get("payload") or {}
    raw_open_items = payload_dict.get("open_items") or []

    # Validate indexes up front — clearer error than letting compute_combined
    # raise IndexError, and avoids partial work before we know inputs are good.
    n_open = len(raw_open_items)
    bad = [a.open_item_index for a in body.answers if a.open_item_index < 0 or a.open_item_index >= n_open]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Stale or out-of-range open_item_index values: {bad}. "
                f"The artifact currently has {n_open} open_items — refresh the page "
                "and re-submit."
            ),
        )

    # Verify upload ownership before doing any expensive work.
    new_upload_ids = body.new_upload_ids or []
    await _verify_uploads_ownership(db, new_upload_ids, current_user.user_id)

    # Reconstruct domain models from the persisted JSON.
    try:
        prior = DiscoveryAnalysisV3.model_validate(payload_dict)
    except Exception as exc:
        logger.exception("Failed to validate prior artifact payload for %s", artifact_id)
        raise HTTPException(
            status_code=500,
            detail=f"Stored artifact payload is invalid v3 shape: {str(exc)[:200]}",
        )

    try:
        coerced_open_items = _coerce_open_items(raw_open_items)
    except Exception as exc:
        logger.exception("Failed to validate open_items in artifact %s", artifact_id)
        raise HTTPException(
            status_code=500,
            detail=f"Stored open_items are invalid: {str(exc)[:200]}",
        )

    # Compute the union blast radius. Convert AnsweredItem -> tuple for
    # the helper (which takes (index, value) tuples — value is unused
    # there but kept in the signature so callers don't have to transform).
    answered_tuples = [(a.open_item_index, a.value) for a in body.answers]
    try:
        regen_scope = compute_combined_blast_radius(answered_tuples, prior)
    except IndexError as exc:
        # Defensive: we already validated indexes above, but if open_items
        # count drifts mid-flight (concurrent regen) we still surface 400.
        raise HTTPException(status_code=400, detail=f"Stale open_item_index: {exc}")
    except ValueError as exc:
        # Malformed location string — artifact corruption, not user input.
        logger.exception("compute_blast_radius rejected artifact %s", artifact_id)
        raise HTTPException(
            status_code=500,
            detail=f"OpenItem location format is invalid: {str(exc)[:200]}",
        )

    # Build the answered_items payload for Wave 5b. We pass the resolved
    # OpenItem.label / location alongside the answer so the partial
    # pipeline can stash entries into the right entity's
    # user_provided_answers map without re-walking the artifact.
    answered_items_for_pipeline: list[dict] = []
    for ans in body.answers:
        item = coerced_open_items[ans.open_item_index]
        answered_items_for_pipeline.append({
            "open_item_index": ans.open_item_index,
            "value": ans.value,
            "label": item.label,
            "location": item.location,
        })

    session_id = art.get("session_id") or ""

    await emit_vega_event(
        db,
        current_user.user_id,
        session_id,
        "v3_regenerate_started",
        {
            "artifact_id": artifact_id,
            "answered_count": len(body.answers),
            "new_upload_count": len(new_upload_ids),
            "regen_scope": {
                "workflow_ids": regen_scope.workflow_ids,
                "app_ids": regen_scope.app_ids,
                "rerun_shared_infra": regen_scope.rerun_shared_infra,
            },
        },
    )

    # Wave 5b's partial pipeline. Bound at module load (see top of file)
    # so tests can monkeypatch ``run_v3_pipeline_partial`` on the module
    # without import-time race conditions.
    new_artifact = await run_v3_pipeline_partial(
        db,
        current_user,
        prior,
        regen_scope,
        answered_items_for_pipeline,
        new_upload_ids or None,
    )

    if not isinstance(new_artifact, DiscoveryAnalysisV3):
        # Defensive: 5b is contracted to return DiscoveryAnalysisV3.
        raise HTTPException(
            status_code=500,
            detail="Partial pipeline returned an unexpected shape",
        )

    new_payload = new_artifact.model_dump(mode="json")
    # Preserve the session_id / discovery_mode hints the chat surface
    # relies on when re-rendering. The pipeline doesn't roundtrip these
    # because they aren't part of DiscoveryAnalysisV3.
    if session_id:
        new_payload["session_id"] = session_id
    if payload_dict.get("discovery_mode"):
        new_payload["discovery_mode"] = payload_dict["discovery_mode"]

    parent_version = await _resolve_parent_version(db, artifact_id)
    new_version = await persist_artifact_version(
        db=db,
        artifact_id=artifact_id,
        parent_version=parent_version,
        regen_trigger={
            "open_item_indices": [a.open_item_index for a in body.answers],
            "answers": [
                {"open_item_index": a.open_item_index, "value": a.value}
                for a in body.answers
            ],
            "new_upload_ids": new_upload_ids,
        },
        regen_scope=regen_scope,
        payload=new_payload,
        user_id=current_user.user_id,
    )

    # Update canonical ai_chat_artifacts so the chat surface picks up the
    # latest version on its next read. The versioned row in
    # vega_artifact_versions is the audit trail; this is the live pointer.
    await db.ai_chat_artifacts.update_one(
        {"artifact_id": artifact_id},
        {
            "$set": {
                "payload": new_payload,
                "updated_at": datetime.now(timezone.utc),
                "current_version": new_version,
            }
        },
    )

    await emit_vega_event(
        db,
        current_user.user_id,
        session_id,
        "v3_regenerate_complete",
        {
            "artifact_id": artifact_id,
            "new_version": new_version,
            "regen_scope": {
                "workflow_ids": regen_scope.workflow_ids,
                "app_ids": regen_scope.app_ids,
            },
        },
    )

    return {
        "artifact_id": artifact_id,
        "new_version": new_version,
        "regen_scope": {
            "workflow_ids": regen_scope.workflow_ids,
            "app_ids": regen_scope.app_ids,
            "rerun_shared_infra": regen_scope.rerun_shared_infra,
        },
        "extraction_status": new_artifact.extraction_status,
    }
