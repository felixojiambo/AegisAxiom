"""Persistent storage for Nova chat-native finance exports.

The endpoint-only export at routes/nova_exports.py streams CSV/XLSX
bytes back from a single POST request. That is fine for an API
caller, but in chat the user expects ChatGPT/Claude-style behavior:
Nova generates the file and the chat UI renders a download card.
This module is the persistence layer that makes that experience
possible.

Storage:
  * File bytes go into GridFS bucket ``nova_export_files``. GridFS
    is the codebase's existing pattern for binary content
    (routes/projects_task_attachment.py uses bucket
    ``pm_task_files``); we mirror it.
  * Two metadata documents are written for each export, in the same
    insert so the download path can find the file by either id:
      - ``ai_chat_artifacts`` doc with ``artifact_type=
        "finance_export"`` so the existing frontend dispatcher in
        ChatArtifact.jsx can render the download card just by adding
        a switch case for the new type.
      - ``nova_export_artifacts`` doc keyed by ``export_id`` for
        the GET /api/nova/exports/{export_id}/download route's
        permission check + GridFS lookup.

Indexes:
  * ``nova_export_artifacts.export_id`` unique
  * ``nova_export_artifacts.requested_by_user_id`` for owner-only
    download checks
  * ``nova_export_artifacts.expires_at`` TTL — Mongo deletes the
    metadata doc when the export ages out. The accompanying
    ``ai_chat_artifacts`` row is left alone (it's a chat-history
    artifact and chat history outlives downloads).

Size limit:
  * Hard cap of ``_MAX_EXPORT_BYTES`` (8 MB by default). Beyond that
    the helper returns a structured error and the caller (Nova chat
    tool) tells the user to narrow filters. The size cap protects
    Mongo: GridFS handles much larger files, but a chat-native
    download UI shouldn't be the path for multi-MB CSVs.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorGridFSBucket


logger = logging.getLogger(__name__)


# Mongo collection names.
EXPORT_ARTIFACTS_COLLECTION = "nova_export_artifacts"
CHAT_ARTIFACTS_COLLECTION = "ai_chat_artifacts"
GRIDFS_BUCKET = "nova_export_files"

# Hard ceiling on a single export's serialized size. CSV/XLSX
# downloads larger than this should narrow the date window or
# split the export across multiple calls.
_MAX_EXPORT_BYTES: int = 8 * 1024 * 1024  # 8 MB

# Default TTL for an export download. Long enough that a chat user
# can return after a meeting and still grab the file; short enough
# that we're not hoarding generated content indefinitely.
_DEFAULT_EXPORT_TTL = timedelta(days=7)


# Fields the helper refuses to persist alongside the export — defensive
# even when the upstream tool runner already strips them.
_DROP_METADATA_FIELDS: frozenset[str] = frozenset(
    {
        "qbo_oauth_token",
        "qbo_oauth_refresh_token",
        "callback_secret",
        "n8n_execution_id",
        "raw_data",
        "raw_response",
        "raw_payload",
        "stack_trace",
        "traceback",
    }
)


class ExportTooLargeError(Exception):
    """Raised when serialized export bytes exceed _MAX_EXPORT_BYTES."""

    def __init__(self, *, size_bytes: int, max_bytes: int = _MAX_EXPORT_BYTES) -> None:
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes
        super().__init__(
            f"Export bytes ({size_bytes}) exceed the chat-native download "
            f"size cap ({max_bytes}). Narrow the date window or filters."
        )


def _new_artifact_id() -> str:
    """``art_<12hex>`` — matches the existing ai_chat_artifacts id
    convention used by _persist_cashflow_chart_artifact."""
    return f"art_{uuid.uuid4().hex[:12]}"


def _strip_metadata_secrets(metadata: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in metadata.items() if k not in _DROP_METADATA_FIELDS}


def _get_gridfs(db: Any) -> AsyncIOMotorGridFSBucket:
    """Return a GridFS bucket bound to the supplied db."""
    return AsyncIOMotorGridFSBucket(db, bucket_name=GRIDFS_BUCKET)


async def ensure_indexes(db: Any) -> None:
    """Create indexes for the export-artifacts collection.

    Idempotent. Safe to call on every request — Mongo no-ops when an
    index already exists. The caller in the route decides whether to
    invoke this on the hot path or wire it into application startup.
    """
    coll = db[EXPORT_ARTIFACTS_COLLECTION]
    await coll.create_index("export_id", unique=True)
    await coll.create_index("requested_by_user_id")
    # TTL index: Mongo deletes docs after expires_at. expireAfterSeconds=0
    # means "delete when expires_at <= now" — Mongo runs the reaper
    # roughly every minute.
    await coll.create_index("expires_at", expireAfterSeconds=0)


async def save_export_artifact(
    db: Any,
    *,
    export_id: str,
    filename: str,
    content_type: str,
    body: bytes,
    row_count: int,
    columns: list[str],
    filters: dict[str, Any],
    source_tool_name: str,
    source_tool_args: dict[str, Any],
    source_metadata: dict[str, Any],
    warnings: list[str],
    requested_by_user_id: str,
    session_id: Optional[str],
    message_id: Optional[str],
    source: str = "nova_chat",
    ttl: Optional[timedelta] = None,
    matched_rows: Optional[int] = None,
    exported_rows: Optional[int] = None,
    export_cap: Optional[int] = None,
    export_truncated: bool = False,
) -> dict[str, Any]:
    """Persist an export's bytes (GridFS) + metadata (two collections).

    Returns a dict the caller can splice into the Nova tool response —
    contains export_id, artifact_id, filename, format, content_type,
    row_count, columns, filters, generated_at, expires_at,
    download_url, warnings.

    Raises:
      ExportTooLargeError: when ``len(body) > _MAX_EXPORT_BYTES``. The
        Nova tool catches this and returns a structured error so the
        LLM can ask the user to narrow filters.
    """
    if not isinstance(body, (bytes, bytearray)):
        raise TypeError("body must be bytes")
    size_bytes = len(body)
    if size_bytes > _MAX_EXPORT_BYTES:
        raise ExportTooLargeError(size_bytes=size_bytes)

    artifact_id = _new_artifact_id()
    now = datetime.now(timezone.utc)
    expires_at = now + (ttl or _DEFAULT_EXPORT_TTL)
    fmt = (filename.rsplit(".", 1)[-1] or "csv").lower()
    download_url = f"/api/nova/exports/{export_id}/download"

    # 1. Persist file bytes in GridFS first. If anything below fails,
    #    a stray GridFS file will be reaped by the TTL on the metadata
    #    doc OR by a future janitor; either way the metadata insert is
    #    the commit point.
    bucket = _get_gridfs(db)
    grid_id = await bucket.upload_from_stream(
        filename,
        bytes(body),
        metadata={
            "export_id": export_id,
            "artifact_id": artifact_id,
            "content_type": content_type,
            "requested_by_user_id": requested_by_user_id,
            "source_tool_name": source_tool_name,
        },
    )

    cleaned_metadata = _strip_metadata_secrets(dict(source_metadata or {}))

    # 2. nova_export_artifacts: keyed by export_id, drives the
    #    permission check on GET /download.
    export_doc: dict[str, Any] = {
        "export_id": export_id,
        "artifact_id": artifact_id,
        "requested_by_user_id": requested_by_user_id,
        "session_id": session_id,
        "message_id": message_id,
        "source": source,
        "filename": filename,
        "format": fmt,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "row_count": row_count,
        "columns": list(columns),
        "filters": dict(filters or {}),
        "source_tool_name": source_tool_name,
        "source_tool_args": dict(source_tool_args or {}),
        "source_metadata": cleaned_metadata,
        "warnings": list(warnings or []),
        "gridfs_id": str(grid_id),
        "gridfs_bucket": GRIDFS_BUCKET,
        "download_url": download_url,
        "created_at": now,
        "expires_at": expires_at,
    }
    await db[EXPORT_ARTIFACTS_COLLECTION].insert_one(export_doc)

    # 3. ai_chat_artifacts: drives the chat dispatcher. The frontend
    #    fetches /api/ai-chat-artifacts/{artifact_id} and switches on
    #    artifact_type to pick the rendering component.
    chat_artifact_doc: dict[str, Any] = {
        "artifact_id": artifact_id,
        "session_id": session_id,
        "user_id": requested_by_user_id,
        "employee_id": "ai_nova",
        "artifact_type": "finance_export",
        "payload": {
            "export_id": export_id,
            "filename": filename,
            "format": fmt,
            "content_type": content_type,
            "row_count": row_count,
            "columns": list(columns),
            "source_tool_name": source_tool_name,
            "generated_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "download_url": download_url,
            "warnings": list(warnings or []),
        },
        "status": "delivered",
        "created_by_agent": "ai_nova",
        "created_at": now,
        "updated_at": now,
    }
    await db[CHAT_ARTIFACTS_COLLECTION].insert_one(chat_artifact_doc)

    return {
        "export_id": export_id,
        "artifact_id": artifact_id,
        "filename": filename,
        "format": fmt,
        "content_type": content_type,
        "row_count": row_count,
        "columns": list(columns),
        "filters": dict(filters or {}),
        "source_tool_name": source_tool_name,
        "generated_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "download_url": download_url,
        "warnings": list(warnings or []),
        # Export-vs-preview metadata. ``exported_rows`` is the number
        # of data rows in the downloaded file; ``matched_rows`` is the
        # full match the tool reported (None when the tool did not
        # surface a separate count). When ``export_truncated`` is
        # true, ``matched_rows`` is strictly greater than
        # ``exported_rows`` and the cap was hit.
        "matched_rows": matched_rows,
        "exported_rows": exported_rows if exported_rows is not None else row_count,
        "export_cap": export_cap,
        "export_truncated": bool(export_truncated),
    }


async def get_export_artifact(
    db: Any, export_id: str,
) -> Optional[dict[str, Any]]:
    """Look up the metadata doc by export_id. Returns None when absent
    (TTL-reaped or never existed). Strips _id."""
    doc = await db[EXPORT_ARTIFACTS_COLLECTION].find_one(
        {"export_id": export_id}, {"_id": 0}
    )
    return doc


async def open_export_download_stream(
    db: Any, export_doc: dict[str, Any],
) -> bytes:
    """Read the GridFS blob for a metadata doc and return the bytes.

    Returns the bytes verbatim. Caller wraps in StreamingResponse.
    Returns empty bytes if the GridFS file has been reaped — caller
    should treat that as a 410.
    """
    from bson import ObjectId  # local import to keep this module's
                                # import surface narrow

    grid_id_raw = export_doc.get("gridfs_id")
    if not grid_id_raw:
        return b""
    try:
        grid_id = ObjectId(grid_id_raw)
    except Exception:  # noqa: BLE001
        return b""
    bucket = _get_gridfs(db)
    try:
        stream = await bucket.open_download_stream(grid_id)
    except Exception as exc:  # noqa: BLE001 — gridfs.NoFile / etc.
        logger.warning(
            "GridFS open_download_stream failed for %r: %s", grid_id_raw, exc
        )
        return b""
    try:
        return await stream.read()
    finally:
        # motor's stream supports an explicit close.
        try:
            await stream.close()
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "CHAT_ARTIFACTS_COLLECTION",
    "EXPORT_ARTIFACTS_COLLECTION",
    "ExportTooLargeError",
    "GRIDFS_BUCKET",
    "ensure_indexes",
    "get_export_artifact",
    "open_export_download_stream",
    "save_export_artifact",
]
