"""
Projects module — Task Comments (Wave 6 / Thread D, Contract 7).

First-class threaded comments on tasks. Markdown-formatted; the body is
stored verbatim and rendered client-side via react-markdown + remark-gfm.

Permissions:
  * `projects.task.comment.view`   — read comments
  * `projects.task.comment.create` — add a new comment (own author only)
  * `projects.task.comment.edit`   — edit a comment (only own; admin/PM
                                     bypasses via has_perm wildcard)
  * `projects.task.comment.delete` — delete a comment (own OR admin/PM)

Collection: `pm_task_comments`. ID prefix: `comment_`.
Index: `(task_id, created_at desc)` for the listing path.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

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
# Field / event / module registration
# ---------------------------------------------------------------------------
register_fields(
    "projects",
    "task_comment",
    [
        {"name": "comment_id", "type": "string", "label": "Comment ID",
         "description": "Primary key (comment_<uuid>)"},
        {"name": "task_id", "type": "string", "label": "Task ID",
         "description": "FK to pm_tasks.task_id"},
        {"name": "body", "type": "string", "label": "Body",
         "description": "Markdown body, rendered client-side"},
        {"name": "author_id", "type": "string", "label": "Author ID",
         "description": "FK to users.user_id (immutable)"},
        {"name": "edited", "type": "boolean", "label": "Edited",
         "description": "True once the body has been changed at least once"},
        {"name": "created_at", "type": "datetime", "label": "Created At"},
        {"name": "updated_at", "type": "datetime", "label": "Updated At"},
    ],
)

register_module_config("projects", "pm_task_comments", "comment_id", "Custom")
register_module_events(
    "projects",
    "task_comment",
    ["created", "updated", "deleted"],
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
_BODY_MAX = 10_000  # 10K chars; markdown bodies above this are likely a copy/paste accident.


class CommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=_BODY_MAX)


class CommentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=_BODY_MAX)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_INDEXES_ENSURED = False


async def _ensure_indexes(db) -> None:
    """Create pm_task_comments indexes lazily on first request."""
    global _INDEXES_ENSURED
    if _INDEXES_ENSURED:
        return
    try:
        await db["pm_task_comments"].create_index("comment_id", unique=True)
        await db["pm_task_comments"].create_index([("task_id", 1), ("created_at", -1)])
        _INDEXES_ENSURED = True
    except Exception as e:
        logger.warning(f"pm_task_comments index ensure failed (non-fatal): {e}")


async def _ensure_task_exists(db, task_id: str, tenant_id: str) -> Dict[str, Any]:
    task = await db["pm_tasks"].find_one(
        {"task_id": task_id, "tenant_id": tenant_id},
        {"_id": 0, "task_id": 1, "project_id": 1},
    )
    if task is None:
        raise HTTPException(
            status_code=404, detail=f"Task {task_id} not found"
        )
    return task


def _is_author_or_privileged(comment: Dict[str, Any], user) -> bool:
    """Edit/delete gate: author always, PM/admin via wildcard perm."""
    if comment.get("author_id") == user.user_id:
        return True
    # admin (`*`) and project_manager (full projects.* set) hold delete; the
    # actual permission check still runs at the route level — this helper
    # is only for the additional ownership gate on edit/delete.
    return has_perm(user, "projects.task.comment.delete") and has_perm(
        user, "projects.task.delete"
    )


def _can_edit(comment: Dict[str, Any], user) -> bool:
    """Edit is author-only by design — even admins shouldn't rewrite a
    user's words. They CAN delete the comment via _is_author_or_privileged."""
    return comment.get("author_id") == user.user_id


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/tasks/{task_id}/comments",
    dependencies=[Depends(require_ai_permission("projects.task.comment.view"))],
)
async def list_task_comments(
    task_id: str,
    limit: int = Query(50, ge=1, le=500),
    before: Optional[str] = Query(
        None, description="comment_id — return rows older than this one (cursor)"
    ),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """List comments for a task, newest first.

    Cursor pagination via `before=<comment_id>`: returns comments with
    `created_at < cursor.created_at`. The cursor comment itself is excluded.
    """
    if not has_perm(current_user, "projects.task.comment.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    await _ensure_indexes(db)
    await _ensure_task_exists(db, task_id, tenant_id)

    # Phase 1E.3c — comments are tenant-scoped via their parent task.
    # The task-existence check above already failed cross-tenant requests
    # with 404; the filter below is defensive belt-and-braces so even a
    # bug in _ensure_task_exists wouldn't leak comments.
    query: Dict[str, Any] = {"task_id": task_id, "tenant_id": tenant_id}
    if before:
        cursor_doc = await db["pm_task_comments"].find_one(
            {"comment_id": before, "tenant_id": tenant_id},
            {"_id": 0, "created_at": 1},
        )
        if cursor_doc is not None:
            query["created_at"] = {"$lt": cursor_doc["created_at"]}

    items = (
        await db["pm_task_comments"]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
        .to_list(limit)
    )
    total = await db["pm_task_comments"].count_documents(
        {"task_id": task_id, "tenant_id": tenant_id}
    )
    return {"items": items, "total": total, "limit": limit}


@router.post(
    "/tasks/{task_id}/comments",
    status_code=201,
    dependencies=[Depends(require_ai_permission("projects.task.comment.create"))],
)
async def create_task_comment(
    task_id: str,
    payload: CommentCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.task.comment.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    await _ensure_indexes(db)
    await _ensure_task_exists(db, task_id, tenant_id)

    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="Comment body cannot be empty")

    now = _now_iso()
    doc: Dict[str, Any] = {
        "comment_id": gen_id("comment_"),
        # Phase 1E.3c — stamp owning tenant.
        "tenant_id": tenant_id,
        "task_id": task_id,
        "body": body,
        "author_id": current_user.user_id,
        "edited": False,
        "created_at": now,
        "updated_at": now,
    }

    async with crud_envelope(
        module="projects",
        entity="task_comment",
        action="created",
        resource_id=doc["comment_id"],
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        await db["pm_task_comments"].insert_one(doc)
        doc.pop("_id", None)
        ctx.set_after(doc)

    return doc


@router.patch(
    "/tasks/comments/{comment_id}",
    dependencies=[Depends(require_ai_permission("projects.task.comment.edit"))],
)
async def update_task_comment(
    comment_id: str,
    payload: CommentUpdate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.task.comment.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["pm_task_comments"].find_one(
        {"comment_id": comment_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Comment not found")

    if not _can_edit(before, current_user):
        # Even admins cannot rewrite another user's comment. They can delete it.
        raise HTTPException(
            status_code=403,
            detail="You can only edit your own comments.",
        )

    new_body = payload.body.strip()
    if not new_body:
        raise HTTPException(status_code=422, detail="Comment body cannot be empty")

    if new_body == (before.get("body") or ""):
        # No-op — return the existing doc (idempotent).
        return before

    update = {
        "body": new_body,
        "edited": True,
        "updated_at": _now_iso(),
    }

    async with crud_envelope(
        module="projects",
        entity="task_comment",
        action="updated",
        resource_id=comment_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["pm_task_comments"].update_one(
            {"comment_id": comment_id, "tenant_id": tenant_id}, {"$set": update}
        )
        updated = await db["pm_task_comments"].find_one(
            {"comment_id": comment_id, "tenant_id": tenant_id}, {"_id": 0}
        )
        ctx.set_after(updated)

    return updated


@router.delete(
    "/tasks/comments/{comment_id}",
    dependencies=[Depends(require_ai_permission("projects.task.comment.delete"))],
)
async def delete_task_comment(
    comment_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.task.comment.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["pm_task_comments"].find_one(
        {"comment_id": comment_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Comment not found")

    if not _is_author_or_privileged(before, current_user):
        raise HTTPException(
            status_code=403,
            detail="You can only delete your own comments (or as a project admin).",
        )

    async with crud_envelope(
        module="projects",
        entity="task_comment",
        action="deleted",
        resource_id=comment_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["pm_task_comments"].delete_one(
            {"comment_id": comment_id, "tenant_id": tenant_id}
        )
        ctx.set_after(None)

    return {"ok": True, "deleted_id": comment_id}
