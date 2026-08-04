"""Projects ingestion — CSV import/export routes.

Wave 6 / Thread C — Contract 6.

Two endpoints, deliberately small:
    GET  /projects/{id}/export?format=csv  — stream all tasks for one project
                                              as a CSV
    POST /projects/{id}/import             — multipart upload of a CSV; each
                                              row validated through
                                              `validate_task_methodology_fields`;
                                              row-level error reporting

Supported import columns (case-sensitive headers, others ignored):
    title, description, status, priority, assignee_email, due_date,
    start_date, labels, methodology_field_<key>

`methodology_field_<key>` columns let CSVs carry methodology-specific bits:
    methodology_field_phase            (waterfall)
    methodology_field_milestone        (waterfall, "true"/"false")
    methodology_field_milestone_name   (waterfall)
    methodology_field_milestone_target_date  (waterfall)
    methodology_field_column_id        (agile)
    methodology_field_story_points     (agile, parsed as float)
    methodology_field_sprint_id        (agile)
    methodology_field_wave_id          (waves_threads)
    methodology_field_thread_id        (waves_threads)
    methodology_field_module_owned     (waves_threads)
    methodology_field_bucket           (waves_threads)
    methodology_field_scope_drift      (waves_threads, "true"/"false")

The endpoint performs full per-row validation through the existing
`validate_task_methodology_fields` helper so the FE preview matches what the
write path will accept.

No new permissions are introduced — gated on `projects.task.view` (export) and
`projects.task.create` (import) which already exist.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pymongo import ReturnDocument

from sqlalchemy.ext.asyncio import AsyncSession

from auth_helper import get_current_user, has_perm
from database import get_db
from db.session import get_session
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id
from models import gen_id
from repositories.users import UserRepository
from services.field_registry import register_module_events
from services.ingestion.route_helpers import crud_envelope
from services.projects_methodology import validate_task_methodology_fields

logger = logging.getLogger(__name__)
router = APIRouter()


# Import/export is a virtual entity — no collection of its own. Register
# events so the audit pipeline knows about the surface.
register_module_events("projects", "import_export", ["exported", "imported"])


# Maximum bytes accepted from a single CSV upload. Keeps malicious or
# accidental gigabyte payloads from blowing memory; legitimate task imports
# are small (typically <100 KB).
MAX_IMPORT_BYTES = 5 * 1024 * 1024  # 5 MB

# Maximum rows imported in a single request. Bulk-create is not the right tool
# for tens-of-thousands of tasks — that path should run as a background job.
MAX_IMPORT_ROWS = 1000

EXPORT_HEADER = [
    "task_id",
    "task_number",
    "title",
    "description",
    "status",
    "priority",
    "assignee_email",
    "start_date",
    "due_date",
    "labels",
    "methodology_field_phase",
    "methodology_field_milestone",
    "methodology_field_milestone_name",
    "methodology_field_milestone_target_date",
    "methodology_field_milestone_actual_date",
    "methodology_field_column_id",
    "methodology_field_story_points",
    "methodology_field_sprint_id",
    "methodology_field_wave_id",
    "methodology_field_thread_id",
    "methodology_field_module_owned",
    "methodology_field_bucket",
    "methodology_field_scope_drift",
    "created_at",
    "updated_at",
]

PRIORITIES = {"low", "medium", "high", "critical"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _counter_key(project_id: str) -> str:
    return f"project:{project_id}:task"


async def _next_task_number(db: Any, project_id: str, tenant_id: str) -> int:
    # Phase 1E.3c — counter doc carries tenant_id. The key is already
    # tenant-unique via project_id, but the filter is defensive.
    result = await db["pm_counters"].find_one_and_update(
        {"_id": _counter_key(project_id), "tenant_id": tenant_id},
        {"$inc": {"seq": 1}, "$setOnInsert": {"tenant_id": tenant_id}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(result["seq"])


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
@router.get(
    "/projects/{project_id}/export",
    dependencies=[Depends(require_ai_permission("projects.task.view"))],
)
async def export_project_tasks(
    project_id: str,
    format: str = Query("csv", regex="^(csv)$"),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
    pg: AsyncSession = Depends(get_session),
):
    if not has_perm(current_user, "projects.task.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    # Phase 1E.3c — block cross-tenant export. The 404 hides whether the
    # project exists in another tenant.
    project = await db["projects"].find_one(
        {"project_id": project_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Pull all tasks for the project. No pagination — exports are bounded by
    # how many tasks one project realistically has (low thousands at most).
    tasks = (
        await db["pm_tasks"]
        .find({"project_id": project_id, "tenant_id": tenant_id}, {"_id": 0})
        .sort("task_number", 1)
        .to_list(length=10_000)
    )

    # Build email lookup so the export is human-friendly. Only fetch the user
    # IDs actually referenced by the task set. Scope to same tenant: a
    # cross-tenant assignee shouldn't surface anyway, but the filter is
    # defensive.
    assignee_ids = sorted({t.get("assignee_id") for t in tasks if t.get("assignee_id")})
    email_by_user: Dict[str, str] = await UserRepository(pg).get_emails_by_ids(
        assignee_ids, tenant_id=tenant_id
    )

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(EXPORT_HEADER)

    for t in tasks:
        mf = t.get("methodology_fields") or {}
        row = [
            t.get("task_id", ""),
            str(t.get("task_number", "")) if t.get("task_number") is not None else "",
            t.get("title", "") or "",
            (t.get("description") or "").replace("\r\n", "\n").replace("\r", "\n"),
            t.get("status", "") or "",
            t.get("priority", "") or "",
            email_by_user.get(t.get("assignee_id") or "", ""),
            t.get("start_date") or "",
            t.get("due_date") or "",
            ",".join(t.get("labels") or []),
            mf.get("phase", "") or "",
            "true" if mf.get("milestone") is True else ("false" if mf.get("milestone") is False else ""),
            mf.get("milestone_name", "") or "",
            mf.get("milestone_target_date", "") or "",
            mf.get("milestone_actual_date", "") or "",
            mf.get("column_id", "") or "",
            "" if mf.get("story_points") is None else str(mf.get("story_points")),
            mf.get("sprint_id", "") or "",
            mf.get("wave_id", "") or "",
            mf.get("thread_id", "") or "",
            mf.get("module_owned", "") or "",
            mf.get("bucket", "") or "",
            "true" if mf.get("scope_drift") is True else ("false" if mf.get("scope_drift") is False else ""),
            t.get("created_at", "") or "",
            t.get("updated_at", "") or "",
        ]
        writer.writerow(row)

    csv_bytes = buf.getvalue().encode("utf-8-sig")  # BOM so Excel honors UTF-8
    safe_name = (project.get("name") or project_id).replace('"', "").replace(",", "")[:80]
    filename = f"{safe_name or 'project'}_tasks.csv"

    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Import — parsing helpers
# ---------------------------------------------------------------------------
def _parse_bool(raw: Optional[str]) -> Optional[bool]:
    """Accept the common shapes spreadsheets emit. None means "not provided"."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s == "":
        return None
    if s in {"true", "t", "yes", "y", "1"}:
        return True
    if s in {"false", "f", "no", "n", "0"}:
        return False
    raise ValueError(f"Invalid boolean: {raw!r}")


def _parse_methodology_fields(
    row: Dict[str, str], project_type: str
) -> Dict[str, Any]:
    """Build a methodology_fields dict from `methodology_field_*` columns.

    Coerces basic types (bool, number) but does NOT enforce per-methodology
    rules — that's `validate_task_methodology_fields`'s job.
    """
    mf: Dict[str, Any] = {}
    for col, raw in row.items():
        if not col or not col.startswith("methodology_field_"):
            continue
        key = col[len("methodology_field_") :]
        value = (raw or "").strip()
        if value == "":
            continue
        if key in {"milestone", "scope_drift"}:
            mf[key] = _parse_bool(value)
        elif key == "story_points":
            try:
                # Float so "3.5" works; ints round-trip fine.
                mf[key] = float(value)
            except ValueError:
                raise ValueError(f"methodology_field_story_points must be a number, got {value!r}")
        else:
            mf[key] = value
    return mf


def _parse_labels(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [s.strip() for s in str(raw).split(",") if s.strip()]


async def _resolve_assignee(
    users: UserRepository,
    email: Optional[str],
    cache: Dict[str, Optional[str]],
    tenant_id: str,
) -> Optional[str]:
    if not email:
        return None
    e = email.strip().lower()
    if not e:
        return None
    if e in cache:
        return cache[e]
    # Phase 1E.3c — tenant-scoped lookup. Cross-tenant emails won't resolve.
    user_id = await users.get_user_id_by_email_in_tenant(e, tenant_id)
    cache[e] = user_id
    return user_id


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
@router.post(
    "/projects/{project_id}/import",
    dependencies=[Depends(require_ai_permission("projects.task.create"))],
)
async def import_project_tasks(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
    pg: AsyncSession = Depends(get_session),
):
    if not has_perm(current_user, "projects.task.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    users = UserRepository(pg)

    # Phase 1E.3c — block cross-tenant import. A user impersonating another
    # tenant can't dump a CSV into the wrong tenant's project.
    project = await db["projects"].find_one(
        {"project_id": project_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.get("status") in {"closed", "on_hold"}:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Project is {project['status']}. Cannot import tasks. "
                "Reopen the project first."
            ),
        )

    # Read up to MAX_IMPORT_BYTES + 1 to detect oversize without buffering the
    # whole stream. We rely on FastAPI's UploadFile having a SpooledTemporaryFile
    # under the hood, so .read() is safe.
    payload = await file.read(MAX_IMPORT_BYTES + 1)
    if len(payload) > MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"CSV exceeds maximum size of {MAX_IMPORT_BYTES} bytes",
        )

    # Decode UTF-8 with BOM tolerance (Excel exports include one).
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = payload.decode("latin-1")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="CSV could not be decoded as UTF-8 or Latin-1")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV has no header row")
    if "title" not in {h.strip() for h in reader.fieldnames if h}:
        raise HTTPException(
            status_code=400,
            detail="CSV must include a 'title' column",
        )

    project_type = project.get("project_type")
    created: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    email_cache: Dict[str, Optional[str]] = {}

    rows: List[Tuple[int, Dict[str, str]]] = []
    for idx, raw_row in enumerate(reader, start=2):  # header is row 1
        rows.append((idx, raw_row))
        if len(rows) > MAX_IMPORT_ROWS:
            raise HTTPException(
                status_code=413,
                detail=f"CSV exceeds maximum row count of {MAX_IMPORT_ROWS}",
            )

    # Each row is wrapped in its own crud_envelope so a policy deny / failed
    # validation on row 17 doesn't roll back rows 1-16. This matches the
    # "report" semantics of bulk-create elsewhere in the module.
    for line_no, raw in rows:
        try:
            doc = await _build_task_doc_from_row(
                db=db,
                users=users,
                project=project,
                project_type=project_type,
                raw=raw,
                current_user=current_user,
                email_cache=email_cache,
                tenant_id=tenant_id,
            )
        except ValueError as exc:
            failed.append({"row": line_no, "error": str(exc)})
            continue
        except HTTPException as exc:
            # validate_task_methodology_fields raises 422 with structured detail
            detail = exc.detail
            if isinstance(detail, dict):
                msg = detail.get("message") or "Validation failed"
            else:
                msg = str(detail)
            failed.append({"row": line_no, "error": msg})
            continue

        task_id = doc["task_id"]
        try:
            async with crud_envelope(
                module="projects",
                entity="task",
                action="created",
                resource_id=task_id,
                current_user=current_user,
                db=db,
                request=request,
            ) as ctx:
                await db["pm_tasks"].insert_one(doc)
                await db["pm_task_status_history"].insert_one(
                    {
                        # Phase 1E.3c — append-only audit stamped with tenant.
                        "tenant_id": tenant_id,
                        "task_id": task_id,
                        "project_id": project_id,
                        "from_status": None,
                        "to_status": doc["status"],
                        "changed_at": _utc_now(),
                        "changed_by": current_user.user_id,
                    }
                )
                # Strip the mongo-internal _id (defensive — insert_one mutates).
                clean = {k: v for k, v in doc.items() if k != "_id"}
                ctx.set_after(clean)
            created.append({k: v for k, v in doc.items() if k != "_id"})
        except HTTPException as exc:
            failed.append({"row": line_no, "error": str(exc.detail)})
        except Exception as exc:  # noqa: BLE001 — we surface storage errors per-row
            logger.exception("CSV import row %s insert failed", line_no)
            failed.append({"row": line_no, "error": f"Storage error: {exc}"})

    return {
        "created_count": len(created),
        "failed": failed,
        "created": created,
    }


async def _build_task_doc_from_row(
    *,
    db: Any,
    users: UserRepository,
    project: Dict[str, Any],
    project_type: str,
    raw: Dict[str, str],
    current_user: Any,
    email_cache: Dict[str, Optional[str]],
    tenant_id: str,
) -> Dict[str, Any]:
    """Build a fully-validated task doc from one CSV row.

    Raises ValueError for shape problems we own (priority enum, missing
    title, etc.). Lets HTTPException(422) from validate_task_methodology_fields
    bubble up — the caller catches both shapes.
    """
    title = (raw.get("title") or "").strip()
    if not title:
        raise ValueError("Missing 'title'")

    description = (raw.get("description") or "").strip() or None
    priority = (raw.get("priority") or "medium").strip().lower() or "medium"
    if priority not in PRIORITIES:
        raise ValueError(
            f"priority must be one of {sorted(PRIORITIES)} (got {priority!r})"
        )

    requested_status = (raw.get("status") or "").strip() or None
    start_date = (raw.get("start_date") or "").strip() or None
    due_date = (raw.get("due_date") or "").strip() or None
    if start_date and due_date and start_date > due_date:
        raise ValueError("start_date must be on or before due_date")

    labels = _parse_labels(raw.get("labels"))
    assignee_email = (raw.get("assignee_email") or "").strip()
    assignee_id = (
        await _resolve_assignee(users, assignee_email, email_cache, tenant_id)
        if assignee_email
        else None
    )
    if assignee_email and assignee_id is None:
        raise ValueError(f"assignee_email {assignee_email!r} does not match any user")

    methodology_fields = _parse_methodology_fields(raw, project_type)

    # Delegate methodology shape + status defaulting to the canonical helper.
    normalized_fields, final_status = validate_task_methodology_fields(
        project, methodology_fields, requested_status
    )

    task_number = await _next_task_number(db, project["project_id"], tenant_id)
    now_iso = _utc_now_iso()
    task_id = gen_id("task_")
    return {
        "task_id": task_id,
        # Phase 1E.3c — stamp owning tenant on every imported task.
        "tenant_id": tenant_id,
        "project_id": project["project_id"],
        "title": title,
        "description": description,
        "assignee_id": assignee_id,
        "status": final_status,
        "priority": priority,
        "estimated_duration_hours": None,
        "parent_task_id": None,
        "start_date": start_date,
        "due_date": due_date,
        "dependencies": [],
        "methodology_fields": normalized_fields,
        "labels": labels,
        "task_number": task_number,
        "sort_index": None,
        "created_at": now_iso,
        "updated_at": now_iso,
        "created_by": current_user.user_id,
    }
