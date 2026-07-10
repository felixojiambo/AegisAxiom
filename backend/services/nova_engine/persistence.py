"""Persistence for Nova projection runs.

Writes to projection_runs collection in the AEGISAXIOM app DB (aegisaxiom_crm).
Each run is keyed by a string projection_run_id (pr_<12hex>) and
content-hashed via inputs_hash for cache lookup.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from .db import get_app_db
from .models import ProjectionInputs, ProjectionOutput


_COLLECTION = "projection_runs"
_ENGINE_VERSION = "0.1.0"


def _new_run_id() -> str:
    return f"pr_{uuid.uuid4().hex[:12]}"


async def save_run(
    user_id: str,
    inputs: ProjectionInputs,
    output: ProjectionOutput,
    session_id: str | None = None,
    message_id: str | None = None,
    parent_run_id: str | None = None,
    db: AsyncIOMotorDatabase | None = None,
) -> dict[str, Any]:
    """Persist a projection run. Returns the stored document (without _id)."""
    if db is None:
        db = get_app_db()

    warnings_list: list[Any] = []
    raw_warnings = getattr(output, "warnings", None) or []
    for w in raw_warnings:
        if hasattr(w, "model_dump"):
            warnings_list.append(w.model_dump(mode="json"))
        else:
            warnings_list.append(w)

    doc = {
        "projection_run_id": _new_run_id(),
        "user_id": user_id,
        "session_id": session_id,
        "message_id": message_id,
        "inputs": inputs.model_dump(mode="json"),
        "inputs_hash": output.inputs_hash,
        "output": output.model_dump(mode="json"),
        "engine_version": _ENGINE_VERSION,
        "parent_run_id": parent_run_id,
        "freshness_warnings": warnings_list,
        "created_at": datetime.now(timezone.utc),
    }

    await db[_COLLECTION].insert_one(doc)
    doc.pop("_id", None)
    return doc


async def get_run(
    projection_run_id: str,
    user_id: str,
    db: AsyncIOMotorDatabase | None = None,
) -> dict[str, Any] | None:
    """Fetch a run by id, scoped to the requesting user."""
    if db is None:
        db = get_app_db()
    return await db[_COLLECTION].find_one(
        {"projection_run_id": projection_run_id, "user_id": user_id},
        {"_id": 0},
    )


async def get_latest_run(
    user_id: str,
    session_id: str | None = None,
    db: AsyncIOMotorDatabase | None = None,
) -> dict[str, Any] | None:
    """Fetch the user's most recent run, optionally scoped to a session."""
    if db is None:
        db = get_app_db()
    query: dict[str, Any] = {"user_id": user_id}
    if session_id is not None:
        query["session_id"] = session_id
    cursor = db[_COLLECTION].find(query, {"_id": 0}).sort("created_at", -1).limit(1)
    docs = await cursor.to_list(length=1)
    return docs[0] if docs else None


async def list_runs(
    user_id: str,
    limit: int = 20,
    db: AsyncIOMotorDatabase | None = None,
) -> list[dict[str, Any]]:
    """Fetch the user's recent runs, newest first."""
    if db is None:
        db = get_app_db()
    cursor = (
        db[_COLLECTION]
        .find({"user_id": user_id}, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


# Pre-wired infrastructure for v1.1 cache-lookup. Not yet called by any
# v1 path. The projection_runs.inputs_hash index supports this function —
# when v1.1 wires up cache lookup before re-running expensive projections,
# this is the entry point. The deterministic-hashing implementation in
# the engine guarantees same inputs -> same hash, so a hit means we can
# return the stored run instead of re-projecting.
async def find_by_inputs_hash(
    user_id: str,
    inputs_hash: str,
    db: AsyncIOMotorDatabase | None = None,
) -> dict[str, Any] | None:
    """Cache lookup: find an existing run for this user with matching inputs_hash."""
    if db is None:
        db = get_app_db()
    return await db[_COLLECTION].find_one(
        {"user_id": user_id, "inputs_hash": inputs_hash},
        {"_id": 0},
    )
