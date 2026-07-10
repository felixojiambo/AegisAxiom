"""
Projects module — Task Attachments (Wave 6 / Thread D, Contract 8).

File uploads attached to tasks. v1 stores blobs in GridFS (motor's
AsyncIOMotorGridFSBucket); a future wave can swap to S3/GCS by replacing
the storage helpers without changing the API surface.

Constraints:
  * 25 MB hard limit per upload
  * MIME whitelist: image/*, application/pdf, text/*, application/zip
  * Permissions:
      - `projects.task.attachment.view`   — list + download
      - `projects.task.attachment.create` — upload
      - `projects.task.attachment.delete` — remove

Collection: `pm_task_attachments` (metadata) + GridFS bucket `pm_task_files`.
ID prefix: `attach_`. Index: `(task_id, uploaded_at desc)`.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id
from models import gen_id
from services.field_registry import (
    register_fields,
    register_module_config,
    register_module_events,
)
from services.ingestion.route_helpers import crud_envelope


logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_BYTES = 25 * 1024 * 1024  # 25 MB
GRIDFS_BUCKET = "pm_task_files"

# Mime whitelist — exact prefixes, matched via `.startswith` so `image/png`,
# `image/jpeg`, etc. all flow through `image/`.
ALLOWED_MIME_PREFIXES = (
    "image/",
    "text/",
)
ALLOWED_MIME_EXACT = {
    "application/pdf",
    "application/zip",
    # Common variants browsers send for zip
    "application/x-zip-compressed",
}

# Filename sanitization — strip path separators, control chars, NULs.
_UNSAFE_FILENAME_CHARS = re.compile(r"[\x00-\x1f/\\]")


# ---------------------------------------------------------------------------
# Field / event / module registration
# ---------------------------------------------------------------------------
register_fields(
    "projects",
    "task_attachment",
    [
        {"name": "attachment_id", "type": "string", "label": "Attachment ID",
         "description": "Primary key (attach_<uuid>)"},
        {"name": "task_id", "type": "string", "label": "Task ID",
         "description": "FK to pm_tasks.task_id"},
        {"name": "filename", "type": "string", "label": "Filename"},
        {"name": "content_type", "type": "string", "label": "Content Type",
         "description": "MIME type"},
        {"name": "size_bytes", "type": "number", "label": "Size (bytes)"},
        {"name": "storage_url", "type": "string", "label": "Storage URL",
         "description": "gridfs://<bucket>/<gridfs_id> for v1; future S3/GCS uri"},
        {"name": "uploaded_by", "type": "string", "label": "Uploaded By",
         "description": "FK to users.user_id"},
        {"name": "uploaded_at", "type": "datetime", "label": "Uploaded At"},
    ],
)

register_module_config(
    "projects", "pm_task_attachments", "attachment_id", "Custom"
)
register_module_events(
    "projects",
    "task_attachment",
    ["created", "deleted"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_INDEXES_ENSURED = False


async def _ensure_indexes(db) -> None:
    """Create pm_task_attachments indexes lazily on first request."""
    global _INDEXES_ENSURED
    if _INDEXES_ENSURED:
        return
    try:
        await db["pm_task_attachments"].create_index(
            "attachment_id", unique=True
        )
        await db["pm_task_attachments"].create_index(
            [("task_id", 1), ("uploaded_at", -1)]
        )
        _INDEXES_ENSURED = True
    except Exception as e:
        logger.warning(
            f"pm_task_attachments index ensure failed (non-fatal): {e}"
        )


async def _ensure_task_exists(db, task_id: str, tenant_id: str) -> Dict[str, Any]:
    task = await db["pm_tasks"].find_one(
        {"task_id": task_id, "tenant_id": tenant_id},
        {"_id": 0, "task_id": 1, "project_id": 1},
    )
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return task


def _validate_mime(content_type: Optional[str]) -> str:
    """Reject uploads outside the whitelist. Empty/None ct gets coerced to
    application/octet-stream and rejected, since we want explicit consent
    on every type that lands in GridFS."""
    ct = (content_type or "").lower().strip()
    if not ct:
        raise HTTPException(
            status_code=415,
            detail="Missing content-type. Allowed: image/*, application/pdf, text/*, application/zip.",
        )
    if any(ct.startswith(p) for p in ALLOWED_MIME_PREFIXES):
        return ct
    if ct in ALLOWED_MIME_EXACT:
        return ct
    raise HTTPException(
        status_code=415,
        detail=(
            f"Unsupported content-type {ct!r}. "
            "Allowed: image/*, application/pdf, text/*, application/zip."
        ),
    )


def _sanitize_filename(name: Optional[str]) -> str:
    """Strip path separators and control chars; cap length at 255."""
    raw = (name or "attachment").strip()
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", raw)
    if not cleaned:
        cleaned = "attachment"
    if len(cleaned) > 255:
        cleaned = cleaned[:255]
    return cleaned


def _get_gridfs(db) -> AsyncIOMotorGridFSBucket:
    """Wrap motor's GridFS bucket. New bucket per request — bucket objects
    are cheap and the underlying connection pool is shared by motor."""
    return AsyncIOMotorGridFSBucket(db, bucket_name=GRIDFS_BUCKET)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/tasks/{task_id}/attachments",
    dependencies=[Depends(require_ai_permission("projects.task.attachment.view"))],
)
async def list_task_attachments(
    task_id: str,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.task.attachment.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    await _ensure_indexes(db)
    await _ensure_task_exists(db, task_id, tenant_id)

    # Phase 1E.3c — attachments inherit tenant scope from their parent
    # task. Filter applied defensively.
    items = (
        await db["pm_task_attachments"]
        .find({"task_id": task_id, "tenant_id": tenant_id}, {"_id": 0})
        .sort("uploaded_at", -1)
        .to_list(500)
    )
    return {"items": items, "total": len(items)}


@router.post(
    "/tasks/{task_id}/attachments",
    status_code=201,
    dependencies=[Depends(require_ai_permission("projects.task.attachment.create"))],
)
async def upload_task_attachment(
    task_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.task.attachment.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    await _ensure_indexes(db)
    await _ensure_task_exists(db, task_id, tenant_id)

    content_type = _validate_mime(file.content_type)
    filename = _sanitize_filename(file.filename)

    # Read fully into memory — 25 MB cap means this is bounded. Streaming
    # straight to GridFS would save peak RAM but complicates the size guard.
    blob = await file.read()
    size = len(blob)
    if size == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")
    if size > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File too large ({size / 1024 / 1024:.1f} MB). "
                f"Max {MAX_BYTES // (1024 * 1024)} MB."
            ),
        )

    # Persist blob in GridFS first; metadata insert is the commit point.
    # Phase 1E.3c — stamp tenant_id into GridFS metadata so the blob itself
    # carries provenance independent of the metadata doc. Future audits can
    # cross-check pm_task_attachments tenant_id against fs.files metadata.
    bucket = _get_gridfs(db)
    grid_id = await bucket.upload_from_stream(
        filename,
        blob,
        metadata={
            "tenant_id": tenant_id,
            "task_id": task_id,
            "uploaded_by": current_user.user_id,
            "content_type": content_type,
        },
    )

    attachment_id = gen_id("attach_")
    storage_url = f"gridfs://{GRIDFS_BUCKET}/{grid_id}"
    now = _now_iso()
    doc: Dict[str, Any] = {
        "attachment_id": attachment_id,
        # Phase 1E.3c — stamp owning tenant on metadata doc.
        "tenant_id": tenant_id,
        "task_id": task_id,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size,
        "storage_url": storage_url,
        "gridfs_id": str(grid_id),  # convenience pointer back to GridFS
        "uploaded_by": current_user.user_id,
        "uploaded_at": now,
    }

    try:
        async with crud_envelope(
            module="projects",
            entity="task_attachment",
            action="created",
            resource_id=attachment_id,
            current_user=current_user,
            db=db,
            request=request,
        ) as ctx:
            await db["pm_task_attachments"].insert_one(doc)
            doc.pop("_id", None)
            ctx.set_after(doc)
    except Exception:
        # Roll back the GridFS upload if metadata insert fails so we don't
        # leak orphan blobs.
        try:
            await bucket.delete(grid_id)
        except Exception as cleanup_err:
            logger.warning(
                "GridFS rollback after metadata insert failed: %s", cleanup_err
            )
        raise

    return doc


@router.get(
    "/tasks/attachments/{attachment_id}/download",
    dependencies=[Depends(require_ai_permission("projects.task.attachment.view"))],
)
async def download_task_attachment(
    attachment_id: str,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.task.attachment.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    # Phase 1E.3c — refusing to serve cross-tenant attachments is the
    # SINGLE MOST important check in this file. Without tenant_id in this
    # filter, a tenant-admin who guesses an attachment_id from another
    # tenant could download the blob.
    doc = await db["pm_task_attachments"].find_one(
        {"attachment_id": attachment_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    grid_id_str = doc.get("gridfs_id")
    if not grid_id_str:
        raise HTTPException(
            status_code=500, detail="Attachment is missing its GridFS pointer."
        )

    try:
        grid_id = ObjectId(grid_id_str)
    except Exception:
        raise HTTPException(status_code=500, detail="Invalid GridFS pointer.")

    bucket = _get_gridfs(db)

    try:
        stream = await bucket.open_download_stream(grid_id)
    except Exception as e:
        logger.warning("GridFS open_download_stream failed: %s", e)
        raise HTTPException(status_code=404, detail="Stored file not found.")

    async def _iter():
        try:
            while True:
                chunk = await stream.readchunk()
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                await stream.close()
            except Exception:
                pass

    safe_name = _sanitize_filename(doc.get("filename"))
    return StreamingResponse(
        _iter(),
        media_type=doc.get("content_type") or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Content-Length": str(doc.get("size_bytes") or 0),
        },
    )


@router.delete(
    "/tasks/attachments/{attachment_id}",
    dependencies=[Depends(require_ai_permission("projects.task.attachment.delete"))],
)
async def delete_task_attachment(
    attachment_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.task.attachment.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["pm_task_attachments"].find_one(
        {"attachment_id": attachment_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    grid_id_str = before.get("gridfs_id")

    async with crud_envelope(
        module="projects",
        entity="task_attachment",
        action="deleted",
        resource_id=attachment_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["pm_task_attachments"].delete_one(
            {"attachment_id": attachment_id, "tenant_id": tenant_id}
        )
        # Best-effort GridFS cleanup — metadata is the source of truth.
        if grid_id_str:
            try:
                bucket = _get_gridfs(db)
                await bucket.delete(ObjectId(grid_id_str))
            except Exception as e:
                logger.warning(
                    "GridFS delete during attachment removal failed (non-fatal): %s",
                    e,
                )
        ctx.set_after(None)

    return {"ok": True, "deleted_id": attachment_id}
