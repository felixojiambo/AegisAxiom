"""
Projects module — Standup CRUD + AI summarization.

Wave 5 / Thread C: rebuilds the standup workflow as a first-class entity
(replacing the legacy per-thread `standup` sub-object that lived on
pm_threads). Standups are recorded daily, optionally scoped to a wave (W&T
projects), and may be sourced from manual entry, a pasted transcript, or a
Fireflies markdown upload. The `/summarize` route invokes the existing LLM
infra (services/ai/llm.py) to extract structured bullets + a one-line
summary from raw_content — no new model class required.

Collection: pm_standups. ID prefix: standup_.
"""
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime, timezone
import json
import logging

from database import get_db
from models import gen_id
from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id
from services.ingestion.route_helpers import crud_envelope
from services.field_registry import register_fields, register_module_events, register_module_config
from services.ai.llm import call_llm, LlmTimeoutError, BudgetExceededError


logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Field registry
# ---------------------------------------------------------------------------
register_fields("projects", "standup", [
    {"name": "standup_id", "type": "string", "label": "Standup ID", "description": "Primary key (standup_<uuid>)"},
    {"name": "project_id", "type": "string", "label": "Project ID", "description": "FK to projects.project_id"},
    {"name": "wave_id", "type": "string", "label": "Wave ID", "description": "FK to pm_waves.wave_id (required for waves_threads projects)"},
    {"name": "date", "type": "date", "label": "Date", "description": "YYYY-MM-DD; one standup per project per day is the convention"},
    {"name": "source", "type": "string", "label": "Source",
     "options": [
         {"value": "manual", "label": "Manual"},
         {"value": "fireflies_md", "label": "Fireflies .md"},
         {"value": "transcript_paste", "label": "Pasted Transcript"},
     ],
     "description": "How the standup was captured"},
    {"name": "raw_content", "type": "string", "label": "Raw Content", "description": "Source markdown / pasted transcript / concatenated manual answers — immutable post-create"},
    {"name": "summary", "type": "string", "label": "Summary", "description": "Short prose summary (AI-generated or manually entered)"},
    {"name": "structured", "type": "list", "label": "Structured",
     "description": "Extracted bullets {shipped_yesterday[], working_today[], blockers[]}"},
    {"name": "created_at", "type": "datetime", "label": "Created At"},
    {"name": "updated_at", "type": "datetime", "label": "Updated At"},
    {"name": "created_by", "type": "string", "label": "Created By"},
])

register_module_config("projects", "pm_standups", "standup_id", "Custom")
register_module_events("projects", "standup", ["created", "updated", "deleted", "summarized"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
SOURCE_ENUM = {"manual", "fireflies_md", "transcript_paste"}


class StandupStructured(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shipped_yesterday: List[str] = Field(default_factory=list)
    working_today: List[str] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)


class StandupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1)
    wave_id: Optional[str] = None
    date: str = Field(min_length=8, max_length=10, description="YYYY-MM-DD")
    source: Literal["manual", "fireflies_md", "transcript_paste"]
    raw_content: str = Field(min_length=1)
    summary: Optional[str] = None
    structured: Optional[StandupStructured] = None


class StandupUpdate(BaseModel):
    """Only summary + structured are mutable post-create. raw_content is
    intentionally immutable so audit + AI re-runs always trace back to the
    original source-of-truth content."""
    model_config = ConfigDict(extra="forbid")
    summary: Optional[str] = None
    structured: Optional[StandupStructured] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_FROZEN_PROJECT_STATUSES = {"closed", "on_hold"}
_INDEXES_ENSURED = False


async def _ensure_indexes(db) -> None:
    """Create pm_standups indexes lazily on first request. Idempotent —
    create_index is a no-op if the index already exists."""
    global _INDEXES_ENSURED
    if _INDEXES_ENSURED:
        return
    try:
        await db["pm_standups"].create_index("standup_id", unique=True)
        await db["pm_standups"].create_index([("project_id", 1), ("date", -1)])
        await db["pm_standups"].create_index([("wave_id", 1), ("date", -1)])
        _INDEXES_ENSURED = True
    except Exception as e:
        # Non-fatal — index may already exist with different signature
        logger.warning(f"pm_standups index ensure failed (non-fatal): {e}")


async def _load_project(db, project_id: str, tenant_id: str) -> dict:
    project = await db["projects"].find_one(
        {"project_id": project_id, "tenant_id": tenant_id}
    )
    if project is None:
        raise HTTPException(status_code=422, detail=f"project_id {project_id} does not match any project")
    return project


def _refuse_if_project_frozen(project: dict) -> None:
    status = (project or {}).get("status")
    if status in _FROZEN_PROJECT_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Project is {status}. Cannot create standups. "
                "Reopen the project first."
            ),
        )


async def _validate_wave(db, project_id: str, wave_id: str, tenant_id: str) -> None:
    wave = await db["pm_waves"].find_one(
        {"wave_id": wave_id, "tenant_id": tenant_id},
        {"_id": 0, "project_id": 1},
    )
    if not wave:
        raise HTTPException(status_code=422, detail=f"wave_id {wave_id} does not match any wave")
    if wave.get("project_id") != project_id:
        raise HTTPException(
            status_code=422,
            detail="wave_id does not belong to project_id",
        )


def _validate_date_str(value: str) -> str:
    """Confirm the supplied string parses as YYYY-MM-DD; return as-is."""
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    return value


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/standups",
    dependencies=[Depends(require_ai_permission("projects.standup.view"))],
)
async def list_standups(
    project_id: Optional[str] = Query(None),
    wave_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD inclusive"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD inclusive"),
    limit: int = Query(50, ge=1, le=500),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.standup.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    await _ensure_indexes(db)

    # Phase 1E.3c — every query starts tenant-scoped.
    query: dict = {"tenant_id": tenant_id}
    if project_id:
        query["project_id"] = project_id
    if wave_id:
        query["wave_id"] = wave_id
    if start_date or end_date:
        date_q: dict = {}
        if start_date:
            _validate_date_str(start_date)
            date_q["$gte"] = start_date
        if end_date:
            _validate_date_str(end_date)
            date_q["$lte"] = end_date
        query["date"] = date_q

    items = (
        await db["pm_standups"]
        .find(query, {"_id": 0})
        .sort([("date", -1), ("created_at", -1)])
        .limit(limit)
        .to_list(limit)
    )
    return {"items": items, "total": len(items), "limit": limit}


@router.get(
    "/standups/{standup_id}",
    dependencies=[Depends(require_ai_permission("projects.standup.view"))],
)
async def get_standup(
    standup_id: str,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.standup.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    doc = await db["pm_standups"].find_one(
        {"standup_id": standup_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Standup not found")
    return doc


@router.post(
    "/standups",
    status_code=201,
    dependencies=[Depends(require_ai_permission("projects.standup.create"))],
)
async def create_standup(
    payload: StandupCreate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.standup.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    await _ensure_indexes(db)

    project = await _load_project(db, payload.project_id, tenant_id)
    _refuse_if_project_frozen(project)

    # W&T projects MUST tie standups to a wave so the standup feed groups
    # cleanly under the wave timeline. Other methodology types treat
    # wave_id as informational and ignore it.
    if project.get("project_type") == "waves_threads":
        if not payload.wave_id:
            raise HTTPException(
                status_code=422,
                detail="wave_id is required for waves_threads projects",
            )
        await _validate_wave(db, payload.project_id, payload.wave_id, tenant_id)
    elif payload.wave_id:
        # Non-W&T project shouldn't carry a wave_id.
        raise HTTPException(
            status_code=422,
            detail="wave_id is only valid on waves_threads projects",
        )

    _validate_date_str(payload.date)

    if payload.source not in SOURCE_ENUM:
        raise HTTPException(status_code=422, detail=f"Invalid source; must be one of {sorted(SOURCE_ENUM)}")

    now = _now_iso()
    structured = payload.structured.model_dump() if payload.structured else None
    doc = {
        "standup_id": gen_id("standup_"),
        # Phase 1E.3c — stamp owning tenant.
        "tenant_id": tenant_id,
        "project_id": payload.project_id,
        "wave_id": payload.wave_id,
        "date": payload.date,
        "source": payload.source,
        "raw_content": payload.raw_content,
        "summary": payload.summary,
        "structured": structured,
        "created_at": now,
        "updated_at": now,
        "created_by": current_user.user_id,
    }

    async with crud_envelope(
        module="projects", entity="standup", action="created",
        resource_id=doc["standup_id"], current_user=current_user, db=db, request=request,
    ) as ctx:
        await db["pm_standups"].insert_one(doc)
        doc.pop("_id", None)
        ctx.set_after(doc)

    return doc


@router.patch(
    "/standups/{standup_id}",
    dependencies=[Depends(require_ai_permission("projects.standup.edit"))],
)
async def update_standup(
    standup_id: str,
    payload: StandupUpdate,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.standup.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["pm_standups"].find_one(
        {"standup_id": standup_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Standup not found")

    dumped = payload.model_dump(exclude_unset=True)

    # Phase 1E.3c — defensive: drop tenant_id from update payload so a
    # client can never reparent a standup to another tenant via PATCH.
    dumped.pop("tenant_id", None)

    if not dumped:
        # Nothing to do — preserve idempotency by returning the existing doc.
        return before

    if "structured" in dumped and dumped["structured"] is not None:
        # model_dump on a nested BaseModel already gives a dict via Pydantic;
        # but exclude_unset returns the raw dict shape from the patch input.
        # Normalize to ensure the canonical key set lands on disk.
        s = dumped["structured"]
        dumped["structured"] = {
            "shipped_yesterday": list(s.get("shipped_yesterday", []) or []),
            "working_today": list(s.get("working_today", []) or []),
            "blockers": list(s.get("blockers", []) or []),
        }

    dumped["updated_at"] = _now_iso()

    async with crud_envelope(
        module="projects", entity="standup", action="updated",
        resource_id=standup_id, current_user=current_user, db=db,
        before=before, request=request,
    ) as ctx:
        await db["pm_standups"].update_one(
            {"standup_id": standup_id, "tenant_id": tenant_id}, {"$set": dumped}
        )
        updated = await db["pm_standups"].find_one(
            {"standup_id": standup_id, "tenant_id": tenant_id}, {"_id": 0}
        )
        ctx.set_after(updated)

    return updated


@router.delete(
    "/standups/{standup_id}",
    dependencies=[Depends(require_ai_permission("projects.standup.delete"))],
)
async def delete_standup(
    standup_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    if not has_perm(current_user, "projects.standup.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["pm_standups"].find_one(
        {"standup_id": standup_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Standup not found")

    async with crud_envelope(
        module="projects", entity="standup", action="deleted",
        resource_id=standup_id, current_user=current_user, db=db,
        before=before, request=request,
    ) as ctx:
        await db["pm_standups"].delete_one(
            {"standup_id": standup_id, "tenant_id": tenant_id}
        )
        ctx.set_after(None)

    return {"ok": True, "deleted_id": standup_id}


# ---------------------------------------------------------------------------
# AI summarization
# ---------------------------------------------------------------------------
_SUMMARIZE_SYSTEM = (
    "You are a project-management assistant extracting structured bullets "
    "from a daily standup transcript or notes. Return JSON ONLY (no prose, "
    "no markdown fences) with this exact shape:\n"
    '{"summary": "<one-sentence recap>", '
    '"structured": {"shipped_yesterday": [...], "working_today": [...], '
    '"blockers": [...]}}\n'
    "Each bullet should be short (≤ 140 chars), declarative, and de-duped. "
    "If a category has no items, return an empty array. Do NOT invent items "
    "not present in the source."
)


_SUMMARIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "structured": {
            "type": "object",
            "properties": {
                "shipped_yesterday": {"type": "array", "items": {"type": "string"}},
                "working_today": {"type": "array", "items": {"type": "string"}},
                "blockers": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["shipped_yesterday", "working_today", "blockers"],
        },
    },
    "required": ["summary", "structured"],
}


def _coerce_structured(value: Any) -> Dict[str, List[str]]:
    """Normalize whatever the model returned into the canonical shape so
    downstream consumers (StandupViewerModal, list cards) never blow up on
    missing keys or non-array values."""
    if not isinstance(value, dict):
        return {"shipped_yesterday": [], "working_today": [], "blockers": []}

    def _as_str_list(v):
        if not isinstance(v, list):
            return []
        return [str(x).strip() for x in v if isinstance(x, (str, int, float)) and str(x).strip()]

    return {
        "shipped_yesterday": _as_str_list(value.get("shipped_yesterday")),
        "working_today": _as_str_list(value.get("working_today")),
        "blockers": _as_str_list(value.get("blockers")),
    }


@router.post(
    "/standups/{standup_id}/summarize",
    dependencies=[Depends(require_ai_permission("projects.standup.create"))],
)
async def summarize_standup(
    standup_id: str,
    request: Request,
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Run the LLM over the standup's raw_content and persist the extracted
    summary + structured bullets. Same permission as create — anyone who
    can record a standup can ask the AI to summarize it."""
    if not has_perm(current_user, "projects.standup.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    before = await db["pm_standups"].find_one(
        {"standup_id": standup_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Standup not found")

    raw = (before.get("raw_content") or "").strip()
    if not raw:
        raise HTTPException(status_code=422, detail="Standup has no raw_content to summarize")

    try:
        resp = await call_llm(
            system_prompt=_SUMMARIZE_SYSTEM,
            user_message=f"Standup transcript:\n\n{raw}",
            session_key=f"standup-{standup_id}",
            response_schema=_SUMMARIZE_SCHEMA,
            response_mime_type="application/json",
            timeout=45,
        )
    except LlmTimeoutError:
        raise HTTPException(status_code=504, detail="AI summarization timed out. Try again.")
    except BudgetExceededError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except RuntimeError as e:
        # No LLM configured in the running env — surface clearly so the UI
        # can show a sensible message instead of a generic 500.
        raise HTTPException(status_code=503, detail=f"AI is not available: {e}")
    except Exception as e:
        logger.exception("Standup summarize failed")
        raise HTTPException(status_code=502, detail=f"AI summarization failed: {e}")

    text = (resp.text or "").strip()
    parsed: Dict[str, Any]
    try:
        parsed = json.loads(text) if text else {}
    except json.JSONDecodeError:
        # Be forgiving: some backends wrap in ```json fences despite the
        # response_schema hint. Strip and retry once.
        cleaned = text.strip("` \n")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=502,
                detail="AI returned non-JSON output; unable to parse summary.",
            )

    summary = str(parsed.get("summary") or "").strip() or None
    structured = _coerce_structured(parsed.get("structured"))

    update_fields = {
        "summary": summary,
        "structured": structured,
        "updated_at": _now_iso(),
    }

    async with crud_envelope(
        module="projects", entity="standup", action="summarized",
        resource_id=standup_id, current_user=current_user, db=db,
        before=before, request=request,
    ) as ctx:
        await db["pm_standups"].update_one(
            {"standup_id": standup_id, "tenant_id": tenant_id}, {"$set": update_fields}
        )
        updated = await db["pm_standups"].find_one(
            {"standup_id": standup_id, "tenant_id": tenant_id}, {"_id": 0}
        )
        ctx.set_after(updated)

    return updated
