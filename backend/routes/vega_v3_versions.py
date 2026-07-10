"""Vega v3 artifact version history endpoints (Wave 5 / Thread 5c).

Lets the owner browse and fetch historical versions of a v3 artifact for
audit / rollback purposes. Two endpoints:

    GET /api/vega-artifacts/{artifact_id}/versions
        list metadata for every persisted version (desc by version number)

    GET /api/vega-artifacts/{artifact_id}/versions/{version}
        fetch the full payload at a specific version

Both are owner-only; audit users (who can read the latest via the export
surface) cannot drill into per-version payloads from this endpoint —
leadership review of historical states is a separate path.

Test-friendly imports follow the same pattern as vega_v3_regenerate.py:
``get_db`` is aliased as ``_get_db`` so monkeypatch lands cleanly.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from auth_helper import get_current_user, has_perm
from database import get_db as _get_db
from services.vega.regenerate import VEGA_ARTIFACT_VERSIONS_COLLECTION

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Owner-scoped artifact lookup (duplicated lightly from vega_v3_regenerate to
# avoid a cross-module import cycle once frontend wiring lands).
# ---------------------------------------------------------------------------


async def _load_v3_artifact_for_owner(db, artifact_id: str, user_id: str) -> dict:
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/vega-artifacts/{artifact_id}/versions")
async def list_v3_artifact_versions(
    artifact_id: str,
    current_user=Depends(get_current_user),
):
    """List versions of a v3 artifact for audit / rollback. Owner-only.

    Returns metadata only — no payloads — so the response is bounded
    even when the artifact has been regenerated many times. Sorted by
    version descending so the newest history entry comes first.
    """
    if not has_perm(current_user, "vega.use"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = _get_db()
    # IDOR + artifact_type guard before touching the versions collection.
    await _load_v3_artifact_for_owner(db, artifact_id, current_user.user_id)

    cursor = db[VEGA_ARTIFACT_VERSIONS_COLLECTION].find(
        {"artifact_id": artifact_id},
        {"_id": 0, "version": 1, "parent_version": 1, "regen_trigger": 1, "created_at": 1},
    ).sort("version", -1)
    rows = await cursor.to_list(length=None)

    versions = []
    for row in rows:
        created = row.get("created_at")
        # Mongo round-trips datetime objects; serialise to ISO for the
        # frontend without forcing it to know about BSON types.
        if hasattr(created, "isoformat"):
            created = created.isoformat()
        versions.append({
            "version": row.get("version"),
            "parent_version": row.get("parent_version"),
            "regen_trigger": row.get("regen_trigger") or {},
            "created_at": created,
        })

    return {"versions": versions}


@router.get("/vega-artifacts/{artifact_id}/versions/{version}")
async def get_v3_artifact_version(
    artifact_id: str,
    version: int,
    current_user=Depends(get_current_user),
):
    """Fetch a specific historical version. Owner-only.

    Returns the full versioned payload plus the regen_trigger and scope
    so audit consumers can reconstruct *why* this version was produced.
    """
    if not has_perm(current_user, "vega.use"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = _get_db()
    await _load_v3_artifact_for_owner(db, artifact_id, current_user.user_id)

    row = await db[VEGA_ARTIFACT_VERSIONS_COLLECTION].find_one(
        {"artifact_id": artifact_id, "version": version},
        {"_id": 0},
    )
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"Version {version} not found for artifact {artifact_id}",
        )

    created = row.get("created_at")
    if hasattr(created, "isoformat"):
        created = created.isoformat()

    return {
        "artifact_id": artifact_id,
        "version": row.get("version"),
        "parent_version": row.get("parent_version"),
        "regen_trigger": row.get("regen_trigger") or {},
        "regen_scope": row.get("regen_scope") or {},
        "payload": row.get("payload") or {},
        "created_at": created,
    }
