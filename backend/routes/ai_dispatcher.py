"""
AI Dispatcher

AppDirect dispatch AI POC. Provides:
  * Contract A — human/AI-employee CRUD for dispatch teams, members, settings,
    and a paginated decisions audit list. Gated by `has_perm` (humans) and
    `require_ai_permission` (AI callers).
  * Contract B — machine-to-machine endpoints for the n8n classifier workflow.
    Authenticated by a shared static token in the `X-Dispatcher-Token` header
    (constant-time compared against the value persisted in DispatcherSettings).
    Human session auth is bypassed on these — they're invoked by the n8n
    workflow runner, not by a logged-in user.

The shape of stored documents is locked to Part C of the build spec
(`AppDirect_Dispatch_AI_Build.md`). The Department / Ticket Type enums and the
`New Order (DO NOT USE)` normalization rule are encoded here so the AEGISAXIOM side
is the source of truth and n8n can simply trust whatever it reads back.
"""
from __future__ import annotations

import hmac
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from database import get_db
from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from models import gen_id
from services.event_bus import emit_event
from services.field_registry import (
    register_fields,
    register_module_config,
    register_module_events,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module slug & collection names
# ---------------------------------------------------------------------------
MODULE_SLUG = "ai_dispatcher"
TEAMS_COLLECTION = "dispatcher_teams"
DECISIONS_COLLECTION = "dispatcher_decisions"
SETTINGS_COLLECTION = "dispatcher_settings"
SETTINGS_DOC_ID = "dispatcher_settings_singleton"

# ---------------------------------------------------------------------------
# Enums (verbatim from spec Part I.5 / Part C)
# ---------------------------------------------------------------------------
DEPARTMENTS = [
    "Microcorp Quotes",
    "Commissions",
    "Cable Support",
    "Elite Commissions",
    "Quotes",
    "Orders",
    "Cloud and SaaS",
    "Account Management Support",
    "Revenue Assurance",
    "Training",
    "Mobility",
    "Omni Center",
    "Invest orders",
    "ADCom Orders",
    "Specialized Teams",
]

# Stored values for ticket type. The "(DO NOT USE)" form must never be
# persisted; on write we normalize it to "New Order".
TICKET_TYPES = [
    "Billing Issue",
    "Other",
    "New Order",
    "Trouble / Outage",
    "MACD",
    "Elite Orders",
    "Order",
    "Appsmart ASO Order",
    "Reports",
    "Invest - New Order",
    "OC - New Order",
]
TICKET_TYPE_DO_NOT_USE = "New Order (DO NOT USE)"

ASSIGNMENT_MODES = ["round_robin", "skill_based"]
AVAILABILITY_VALUES = ["on_duty", "off_duty", "vacation", "big_project"]

# Decision lifecycle status values emitted by the n8n workflow.
# - auto_routed: AI confidence cleared the threshold and a real ticket was created
# - manual_review: AI deferred to a human (low confidence, no available owner, etc.)
# - pending: in-flight / not yet finalized (rare — most rows are terminal by the time they POST)
# - failed: unrecoverable error in the workflow before a verdict could be produced
DECISION_STATUS_VALUES = ["auto_routed", "manual_review", "pending", "failed"]

# Which model produced the final verdict. The n8n workflow records detail
# inline by suffixing a colon-separated discriminator (e.g.
# "sonnet:anthropic-claude-4-6-sonnet" or
# "haiku_fallback_after_sonnet_fail:SONNET_LLM_PARSE_FAIL"). The validator
# below matches any value whose category prefix (before the first ":") is one
# of these — that keeps the contract enforced while preserving the audit
# detail. `haiku_fallback` covers the "sonnet escalation failed and we fell
# back" cases (the workflow emits `haiku_fallback_after_sonnet_fail:<reason>`).
AI_FINAL_SOURCE_PREFIXES = ("haiku", "sonnet", "haiku_fallback")


def _normalize_ticket_type(value: Optional[str]) -> Optional[str]:
    """Collapse the deprecated 'New Order (DO NOT USE)' label to 'New Order'."""
    if value == TICKET_TYPE_DO_NOT_USE:
        return "New Order"
    return value


# ---------------------------------------------------------------------------
# Field-registry / module-config registrations (import-time side effect)
# ---------------------------------------------------------------------------
register_module_config(MODULE_SLUG, TEAMS_COLLECTION, "team_id", "AI Dispatcher")
register_module_events(MODULE_SLUG, "team", ["created", "updated", "deleted"])
register_module_events(MODULE_SLUG, "member", ["created", "updated", "deleted"])
register_module_events(MODULE_SLUG, "settings", ["updated", "token_rotated"])
register_module_events(MODULE_SLUG, "decision", ["created", "updated"])

register_fields(MODULE_SLUG, "team", [
    {"name": "team_name", "type": "string", "label": "Team Name",
     "description": "Display name of the dispatch team (e.g. 'Orders Pool')"},
    {"name": "department", "type": "string", "label": "Department",
     "options": [{"value": d, "label": d} for d in DEPARTMENTS],
     "description": "AppDirect department this team handles (one of the 15 canonical departments)"},
    {"name": "assignment_mode", "type": "string", "label": "Assignment Mode",
     "options": [{"value": v, "label": v} for v in ASSIGNMENT_MODES],
     "description": "How AI picks an owner: round_robin (rotating order) or skill_based (best-skill match)"},
    {"name": "rr_pointer", "type": "number", "label": "Round-Robin Pointer",
     "description": "Runtime pointer owned by n8n — index of next member to assign in round-robin"},
])

register_fields(MODULE_SLUG, "member", [
    {"name": "name", "type": "string", "label": "Member Name",
     "description": "Display name of the dispatch agent"},
    {"name": "order_index", "type": "number", "label": "Order Index",
     "description": "Zero-based position in round-robin rotation"},
    {"name": "skill_description", "type": "string", "label": "Skill Description",
     "description": "Free-text skill summary used for skill-based matching (e.g. 'Comcast escalations')"},
    {"name": "availability", "type": "string", "label": "Availability",
     "options": [{"value": v, "label": v} for v in AVAILABILITY_VALUES],
     "description": "Current availability state — only on_duty members receive assignments"},
])

register_fields(MODULE_SLUG, "decision", [
    {"name": "message_id", "type": "string", "label": "Email Message-ID",
     "description": "RFC 5322 Message-ID of the source email; idempotency key for re-classification"},
    {"name": "distro", "type": "string", "label": "Distro List",
     "description": "Inbound distribution list (quotes, orders, cable, oni, invest)"},
    {"name": "ai_department", "type": "string", "label": "AI Department",
     "options": [{"value": d, "label": d} for d in DEPARTMENTS],
     "description": "Department chosen by the AI classifier"},
    {"name": "ai_ticket_type", "type": "string", "label": "AI Ticket Type",
     "options": [{"value": t, "label": t} for t in TICKET_TYPES],
     "description": "Ticket Type chosen by the AI classifier (never stores 'DO NOT USE' form)"},
    {"name": "ai_confidence", "type": "number", "label": "AI Confidence",
     "description": "Classifier confidence score 0.0-1.0"},
    {"name": "owner_mode", "type": "string", "label": "Owner Mode",
     "description": "How the AI picked an owner: round_robin | skill_based | none"},
])

register_fields(MODULE_SLUG, "settings", [
    {"name": "n8n_token", "type": "string", "label": "n8n Shared Token",
     "description": "Static shared secret n8n presents in X-Dispatcher-Token for Contract B"},
    {"name": "linked_workflow_url", "type": "string", "label": "Linked Workflow URL",
     "description": "Free-text URL of the n8n workflow that consumes this dispatcher (display only)"},
])

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------
class TeamCreate(BaseModel):
    team_name: str = Field(..., min_length=1, max_length=120)
    department: str
    assignment_mode: str

    @field_validator("department")
    @classmethod
    def _dept_valid(cls, v: str) -> str:
        if v not in DEPARTMENTS:
            raise ValueError(f"department must be one of: {DEPARTMENTS}")
        return v

    @field_validator("assignment_mode")
    @classmethod
    def _mode_valid(cls, v: str) -> str:
        if v not in ASSIGNMENT_MODES:
            raise ValueError(f"assignment_mode must be one of: {ASSIGNMENT_MODES}")
        return v


class TeamUpdate(BaseModel):
    team_name: Optional[str] = Field(None, min_length=1, max_length=120)
    department: Optional[str] = None
    assignment_mode: Optional[str] = None
    rr_pointer: Optional[int] = Field(None, ge=0)

    @field_validator("department")
    @classmethod
    def _dept_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in DEPARTMENTS:
            raise ValueError(f"department must be one of: {DEPARTMENTS}")
        return v

    @field_validator("assignment_mode")
    @classmethod
    def _mode_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ASSIGNMENT_MODES:
            raise ValueError(f"assignment_mode must be one of: {ASSIGNMENT_MODES}")
        return v


class MemberCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    order_index: Optional[int] = Field(None, ge=0)
    skill_description: Optional[str] = Field(None, max_length=2000)
    availability: Optional[str] = "on_duty"

    @field_validator("availability")
    @classmethod
    def _avail_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in AVAILABILITY_VALUES:
            raise ValueError(f"availability must be one of: {AVAILABILITY_VALUES}")
        return v


class MemberUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    order_index: Optional[int] = Field(None, ge=0)
    skill_description: Optional[str] = Field(None, max_length=2000)
    availability: Optional[str] = None

    @field_validator("availability")
    @classmethod
    def _avail_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in AVAILABILITY_VALUES:
            raise ValueError(f"availability must be one of: {AVAILABILITY_VALUES}")
        return v


class SettingsUpdate(BaseModel):
    linked_workflow_url: Optional[str] = Field(None, max_length=2000)


class DecisionSplit(BaseModel):
    provider: Optional[str] = None
    request_type: Optional[str] = None
    ticket_type: Optional[str] = None
    department: Optional[str] = None
    confidence: Optional[float] = None


class DecisionLLMCall(BaseModel):
    model: str
    raw_response: str
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    called_at: Optional[datetime] = None


class DecisionCreate(BaseModel):
    """Inbound POST shape for /ai-dispatcher/decisions (n8n only).

    Mirrors the schema in spec Part II.6.3 (Decision) + II.6.5 (AuditEntry).
    `message_id` is the idempotency key; existing records on the same Message-ID
    are upserted in place so workflow re-runs don't accumulate duplicates.
    """
    dispatch_item_id: Optional[str] = None
    message_id: str = Field(..., min_length=1)
    received_at: Optional[datetime] = None
    distro: Optional[str] = None
    sender_domain: Optional[str] = None
    sender_address: Optional[str] = None
    subject: Optional[str] = None
    thread_state: Optional[str] = None
    advisor_match: Optional[str] = None
    ai_department: Optional[str] = None
    ai_ticket_type: Optional[str] = None
    ai_owner: Optional[str] = None
    ai_confidence: Optional[float] = None
    dept_source: Optional[str] = None
    type_source: Optional[str] = None
    owner_mode: Optional[str] = None
    is_multi_request: Optional[bool] = False
    splits: Optional[List[DecisionSplit]] = None
    reasoning: Optional[str] = None
    flags: Optional[List[str]] = None
    sla_due_at: Optional[datetime] = None
    llm_calls: Optional[List[DecisionLLMCall]] = None
    human_department: Optional[str] = None
    human_ticket_type: Optional[str] = None
    human_owner: Optional[str] = None
    decided_at: Optional[datetime] = None
    # Lifecycle + provenance fields written by the n8n workflow on every POST.
    # Without explicit declarations, Pydantic's `model_dump(exclude_unset=True)`
    # in the upsert path drops them silently — they need to be declared here so
    # they round-trip to Mongo.
    status: Optional[str] = None
    escalation_reason: Optional[str] = None
    ai_final_source: Optional[str] = None
    # Stage G missing-info draft text. Persisted for audit only — never sent to
    # the advisor. The workflow may produce this for low-confidence decisions
    # that benefit from a manual follow-up.
    missing_info_draft_text: Optional[str] = None
    # POC default — every classifier decision is shadow-mode until we cut over.
    shadow_mode: Optional[bool] = True

    @field_validator("status")
    @classmethod
    def _status_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in DECISION_STATUS_VALUES:
            raise ValueError(f"status must be one of: {DECISION_STATUS_VALUES}")
        return v

    @field_validator("ai_final_source")
    @classmethod
    def _ai_final_source_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # The workflow emits one of:
        #   - "haiku"
        #   - "sonnet" or "sonnet:<alias>"
        #   - "haiku_fallback" or "haiku_fallback_after_sonnet_fail:<reason>"
        # Accept any value that starts with one of these category prefixes.
        if not any(v == p or v.startswith(p + ":") or v.startswith(p + "_")
                   for p in AI_FINAL_SOURCE_PREFIXES):
            raise ValueError(
                f"ai_final_source must start with one of: {list(AI_FINAL_SOURCE_PREFIXES)}"
            )
        return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _strip_id(doc: Optional[dict]) -> Optional[dict]:
    if doc is None:
        return None
    doc.pop("_id", None)
    return doc


async def _load_settings_or_init(db) -> dict:
    """Return the singleton DispatcherSettings doc, creating it on first read."""
    doc = await db[SETTINGS_COLLECTION].find_one({"_id": SETTINGS_DOC_ID})
    if doc:
        return _strip_id(doc)

    fresh = {
        "_id": SETTINGS_DOC_ID,
        "n8n_token": str(uuid.uuid4()),
        "linked_workflow_url": "",
        "created_at": _now(),
        "updated_at": _now(),
    }
    # Upsert defensively in case another worker raced us. The doc has a
    # fixed _id, so a duplicate insert is a no-op and the existing token wins.
    await db[SETTINGS_COLLECTION].update_one(
        {"_id": SETTINGS_DOC_ID},
        {"$setOnInsert": fresh},
        upsert=True,
    )
    persisted = await db[SETTINGS_COLLECTION].find_one({"_id": SETTINGS_DOC_ID})
    return _strip_id(persisted) or _strip_id(fresh)


async def _verify_dispatcher_token(db, header_value: Optional[str]) -> None:
    """Raise 401 unless the X-Dispatcher-Token header matches stored token.

    Uses hmac.compare_digest so timing leaks can't reveal the token byte by
    byte. The settings doc is lazy-created on first read so a freshly-deployed
    AEGISAXIOM instance always has a token to compare against.
    """
    if not header_value:
        raise HTTPException(status_code=401, detail="Missing X-Dispatcher-Token")
    settings = await _load_settings_or_init(db)
    expected = settings.get("n8n_token") or ""
    if not expected or not hmac.compare_digest(header_value, expected):
        raise HTTPException(status_code=401, detail="Invalid dispatcher token")


def _decorate_team_counts(team: dict) -> dict:
    members = team.get("members") or []
    team["member_count"] = len(members)
    team["on_duty_count"] = sum(1 for m in members if m.get("availability") == "on_duty")
    return team


def _is_admin(current_user) -> bool:
    """True if the caller is an admin (wildcard) or has the system-admin role."""
    if has_perm(current_user, "*"):
        return True
    return getattr(current_user, "role", None) == "admin"


# ---------------------------------------------------------------------------
# Contract A — Teams CRUD
# ---------------------------------------------------------------------------
@router.get(
    "/teams",
    dependencies=[Depends(require_ai_permission("ai_dispatcher.teams.view"))],
)
async def list_teams(current_user=Depends(get_current_user)):
    if not has_perm(current_user, "ai_dispatcher.teams.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    teams = await db[TEAMS_COLLECTION].find({"deleted_at": None}, {"_id": 0}) \
        .sort("team_name", 1).to_list(1000)
    return [_decorate_team_counts(t) for t in teams]


@router.post(
    "/teams",
    dependencies=[Depends(require_ai_permission("ai_dispatcher.teams.create"))],
)
async def create_team(payload: TeamCreate, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "ai_dispatcher.teams.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    team_id = gen_id("dt_")
    doc = {
        "team_id": team_id,
        "team_name": payload.team_name,
        "department": payload.department,
        "assignment_mode": payload.assignment_mode,
        "rr_pointer": 0,
        "members": [],
        "created_at": _now(),
        "updated_at": _now(),
        "created_by": current_user.user_id,
        "deleted_at": None,
    }
    await db[TEAMS_COLLECTION].insert_one(doc)
    doc.pop("_id", None)

    await emit_event(
        MODULE_SLUG,
        "team.created",
        record=doc,
        user_id=current_user.user_id,
        user_email=getattr(current_user, "email", None),
    )
    return _decorate_team_counts(doc)


@router.get(
    "/teams/{team_id}",
    dependencies=[Depends(require_ai_permission("ai_dispatcher.teams.view"))],
)
async def get_team(team_id: str, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "ai_dispatcher.teams.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    team = await db[TEAMS_COLLECTION].find_one(
        {"team_id": team_id, "deleted_at": None}, {"_id": 0}
    )
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return _decorate_team_counts(team)


@router.patch(
    "/teams/{team_id}",
    dependencies=[Depends(require_ai_permission("ai_dispatcher.teams.update"))],
)
async def update_team(
    team_id: str,
    payload: TeamUpdate,
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "ai_dispatcher.teams.update"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    before = await db[TEAMS_COLLECTION].find_one(
        {"team_id": team_id, "deleted_at": None}, {"_id": 0}
    )
    if not before:
        raise HTTPException(status_code=404, detail="Team not found")

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return _decorate_team_counts(before)
    updates["updated_at"] = _now()

    await db[TEAMS_COLLECTION].update_one({"team_id": team_id}, {"$set": updates})
    after = await db[TEAMS_COLLECTION].find_one({"team_id": team_id}, {"_id": 0})

    await emit_event(
        MODULE_SLUG,
        "team.updated",
        record=after,
        old_record=before,
        user_id=current_user.user_id,
        user_email=getattr(current_user, "email", None),
    )
    return _decorate_team_counts(after)


@router.delete(
    "/teams/{team_id}",
    dependencies=[Depends(require_ai_permission("ai_dispatcher.teams.delete"))],
)
async def delete_team(team_id: str, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "ai_dispatcher.teams.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    existing = await db[TEAMS_COLLECTION].find_one(
        {"team_id": team_id, "deleted_at": None}, {"_id": 0}
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Team not found")

    await db[TEAMS_COLLECTION].update_one(
        {"team_id": team_id},
        {"$set": {"deleted_at": _now(), "updated_at": _now()}},
    )

    await emit_event(
        MODULE_SLUG,
        "team.deleted",
        record=existing,
        user_id=current_user.user_id,
        user_email=getattr(current_user, "email", None),
    )
    return {"message": "Team deleted"}


# ---------------------------------------------------------------------------
# Contract A — Members CRUD (members are an embedded array on the team doc)
# ---------------------------------------------------------------------------
@router.post(
    "/teams/{team_id}/members",
    dependencies=[Depends(require_ai_permission("ai_dispatcher.members.create"))],
)
async def add_member(
    team_id: str,
    payload: MemberCreate,
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "ai_dispatcher.members.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    team = await db[TEAMS_COLLECTION].find_one(
        {"team_id": team_id, "deleted_at": None}, {"_id": 0}
    )
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    members = team.get("members") or []
    order_index = payload.order_index if payload.order_index is not None else len(members)

    member = {
        "member_id": gen_id("dm_"),
        "name": payload.name,
        "order_index": order_index,
        "skill_description": payload.skill_description or "",
        "availability": payload.availability or "on_duty",
        "created_at": _now(),
        "updated_at": _now(),
    }

    await db[TEAMS_COLLECTION].update_one(
        {"team_id": team_id},
        {
            "$push": {"members": member},
            "$set": {"updated_at": _now()},
        },
    )

    await emit_event(
        MODULE_SLUG,
        "member.created",
        record={"team_id": team_id, **member},
        user_id=current_user.user_id,
        user_email=getattr(current_user, "email", None),
    )
    return member


@router.patch(
    "/teams/{team_id}/members/{member_id}",
    dependencies=[Depends(require_ai_permission("ai_dispatcher.members.update"))],
)
async def update_member(
    team_id: str,
    member_id: str,
    payload: MemberUpdate,
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "ai_dispatcher.members.update"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    team = await db[TEAMS_COLLECTION].find_one(
        {"team_id": team_id, "deleted_at": None}, {"_id": 0}
    )
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    existing = next(
        (m for m in (team.get("members") or []) if m.get("member_id") == member_id),
        None,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Member not found")

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return existing

    # Build $set with positional operator so we only rewrite changed keys.
    set_doc: Dict[str, Any] = {
        f"members.$[elem].{k}": v for k, v in updates.items()
    }
    set_doc["members.$[elem].updated_at"] = _now()
    set_doc["updated_at"] = _now()

    await db[TEAMS_COLLECTION].update_one(
        {"team_id": team_id},
        {"$set": set_doc},
        array_filters=[{"elem.member_id": member_id}],
    )

    team_after = await db[TEAMS_COLLECTION].find_one(
        {"team_id": team_id}, {"_id": 0}
    )
    after = next(
        (m for m in (team_after.get("members") or []) if m.get("member_id") == member_id),
        None,
    )

    await emit_event(
        MODULE_SLUG,
        "member.updated",
        record={"team_id": team_id, **(after or {})},
        old_record={"team_id": team_id, **existing},
        user_id=current_user.user_id,
        user_email=getattr(current_user, "email", None),
    )
    return after


@router.delete(
    "/teams/{team_id}/members/{member_id}",
    dependencies=[Depends(require_ai_permission("ai_dispatcher.members.delete"))],
)
async def delete_member(
    team_id: str,
    member_id: str,
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "ai_dispatcher.members.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    team = await db[TEAMS_COLLECTION].find_one(
        {"team_id": team_id, "deleted_at": None}, {"_id": 0}
    )
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    existing = next(
        (m for m in (team.get("members") or []) if m.get("member_id") == member_id),
        None,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Member not found")

    await db[TEAMS_COLLECTION].update_one(
        {"team_id": team_id},
        {
            "$pull": {"members": {"member_id": member_id}},
            "$set": {"updated_at": _now()},
        },
    )

    await emit_event(
        MODULE_SLUG,
        "member.deleted",
        record={"team_id": team_id, **existing},
        user_id=current_user.user_id,
        user_email=getattr(current_user, "email", None),
    )
    return {"message": "Member deleted"}


# ---------------------------------------------------------------------------
# Contract A — Settings
# ---------------------------------------------------------------------------
@router.get(
    "/settings",
    dependencies=[Depends(require_ai_permission("ai_dispatcher.settings.view"))],
)
async def get_settings(current_user=Depends(get_current_user)):
    if not has_perm(current_user, "ai_dispatcher.settings.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    settings = await _load_settings_or_init(db)
    return {
        "n8n_token": settings.get("n8n_token", ""),
        "linked_workflow_url": settings.get("linked_workflow_url", ""),
    }


@router.patch(
    "/settings",
    dependencies=[Depends(require_ai_permission("ai_dispatcher.settings.update"))],
)
async def update_settings(
    payload: SettingsUpdate,
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "ai_dispatcher.settings.update"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    # Ensure doc exists so the update lands on something concrete
    before = await _load_settings_or_init(db)

    updates = payload.model_dump(exclude_unset=True)
    if updates:
        updates["updated_at"] = _now()
        await db[SETTINGS_COLLECTION].update_one(
            {"_id": SETTINGS_DOC_ID},
            {"$set": updates},
        )

    after = await db[SETTINGS_COLLECTION].find_one({"_id": SETTINGS_DOC_ID})
    after = _strip_id(after) or {}

    await emit_event(
        MODULE_SLUG,
        "settings.updated",
        record=after,
        old_record=before,
        user_id=current_user.user_id,
        user_email=getattr(current_user, "email", None),
    )
    return {
        "n8n_token": after.get("n8n_token", ""),
        "linked_workflow_url": after.get("linked_workflow_url", ""),
    }


@router.post(
    "/settings/rotate-token",
    dependencies=[Depends(require_ai_permission("ai_dispatcher.settings.rotate_token"))],
)
async def rotate_token(current_user=Depends(get_current_user)):
    # Token rotation is an admin-only action. We require the granular
    # rotate-token permission AND a hard admin role check, because a leaked
    # token rotation can lock out the n8n workflow until someone reapplies
    # the new secret on the integration side.
    if not has_perm(current_user, "ai_dispatcher.settings.rotate_token"):
        raise HTTPException(status_code=403, detail="Permission denied")
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin role required to rotate the dispatcher token")

    db = get_db()
    before = await _load_settings_or_init(db)
    new_token = str(uuid.uuid4())

    await db[SETTINGS_COLLECTION].update_one(
        {"_id": SETTINGS_DOC_ID},
        {"$set": {"n8n_token": new_token, "updated_at": _now()}},
        upsert=True,
    )

    after = await db[SETTINGS_COLLECTION].find_one({"_id": SETTINGS_DOC_ID})
    after = _strip_id(after) or {}

    await emit_event(
        MODULE_SLUG,
        "settings.token_rotated",
        record={"linked_workflow_url": after.get("linked_workflow_url", "")},
        old_record={"linked_workflow_url": before.get("linked_workflow_url", "")},
        user_id=current_user.user_id,
        user_email=getattr(current_user, "email", None),
    )

    logger.info(
        "AI Dispatcher n8n_token rotated by user_id=%s email=%s",
        getattr(current_user, "user_id", None),
        getattr(current_user, "email", None),
    )
    return {"n8n_token": new_token}


# ---------------------------------------------------------------------------
# Contract A — Decisions (read)
# ---------------------------------------------------------------------------
@router.get(
    "/decisions",
    dependencies=[Depends(require_ai_permission("ai_dispatcher.decisions.view"))],
)
async def list_decisions(
    limit: int = 50,
    offset: int = 0,
    distro: Optional[str] = None,
    department: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "ai_dispatcher.decisions.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    # Bounded pagination — never trust the client to size the page.
    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))

    db = get_db()
    query: Dict[str, Any] = {}
    if distro:
        query["distro"] = distro
    if department:
        query["ai_department"] = department

    date_clause: Dict[str, Any] = {}
    if from_date:
        try:
            date_clause["$gte"] = datetime.fromisoformat(from_date.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="from_date must be ISO 8601")
    if to_date:
        try:
            date_clause["$lte"] = datetime.fromisoformat(to_date.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="to_date must be ISO 8601")
    if date_clause:
        query["decided_at"] = date_clause

    total = await db[DECISIONS_COLLECTION].count_documents(query)
    cursor = db[DECISIONS_COLLECTION].find(query, {"_id": 0}) \
        .sort("decided_at", -1).skip(offset).limit(limit)
    decisions = await cursor.to_list(limit)
    return {"decisions": decisions, "total": total}


# ---------------------------------------------------------------------------
# Contract B — n8n machine-to-machine endpoints
# ---------------------------------------------------------------------------
# DESIGN NOTE: Contract B intentionally does NOT use Depends(require_ai_permission)
# even though the spec lists ai_dispatcher.config.read / decisions.write perm
# keys for these routes. The reason: require_ai_permission chains through
# Depends(get_current_user), which raises 401 on a missing session cookie.
# n8n calls these endpoints with no user session — only the X-Dispatcher-Token
# header — so the dependency would short-circuit the request before our token
# check could run. The shared-token gate below is functionally equivalent: it
# proves the caller is the authorized n8n workflow runner, which is the same
# guarantee an AI-employee permission would provide. The perm keys still exist
# in the catalog so that admins can later grant them to a human-authenticated
# integration test runner without code changes.
@router.get("/config")
async def get_config(
    x_dispatcher_token: Optional[str] = Header(None, alias="X-Dispatcher-Token"),
):
    """Return the full team config in Part C.2 shape.

    Bypasses human auth (`get_current_user`) — n8n calls this with no user
    session. `rr_pointer` is included for completeness but the canonical
    runtime state is owned and persisted by the n8n workflow.
    """
    db = get_db()
    await _verify_dispatcher_token(db, x_dispatcher_token)

    teams_raw = await db[TEAMS_COLLECTION].find(
        {"deleted_at": None}, {"_id": 0}
    ).sort("team_name", 1).to_list(1000)

    # Project to the exact spec-Part-C shape — strip created_at/updated_at and
    # other internal columns; keep only the contract surface.
    teams: List[dict] = []
    for t in teams_raw:
        members = [
            {
                "member_id": m.get("member_id"),
                "name": m.get("name"),
                "order_index": m.get("order_index", 0),
                "skill_description": m.get("skill_description", ""),
                "availability": m.get("availability", "on_duty"),
            }
            for m in (t.get("members") or [])
        ]
        teams.append({
            "team_id": t.get("team_id"),
            "team_name": t.get("team_name"),
            "department": t.get("department"),
            "assignment_mode": t.get("assignment_mode"),
            "rr_pointer": t.get("rr_pointer", 0),
            "members": members,
        })

    return {"teams": teams}


@router.post("/decisions")
async def write_decision(
    payload: DecisionCreate,
    x_dispatcher_token: Optional[str] = Header(None, alias="X-Dispatcher-Token"),
):
    """Idempotent upsert of a routing decision.

    Bypasses human auth — this is the n8n workflow writing back the
    classifier's verdict. Idempotency is keyed on `message_id`; re-runs of the
    same email update fields in place rather than creating a new row. Scorecard
    fields (`human_*`, `*_match`) may be filled in by later writes — those
    follow the same upsert.
    """
    db = get_db()
    await _verify_dispatcher_token(db, x_dispatcher_token)

    now = _now()
    body = payload.model_dump(exclude_unset=True)

    # Normalize ticket type — never persist the DO NOT USE label.
    if "ai_ticket_type" in body:
        body["ai_ticket_type"] = _normalize_ticket_type(body["ai_ticket_type"])
    if "human_ticket_type" in body:
        body["human_ticket_type"] = _normalize_ticket_type(body["human_ticket_type"])

    # Compute scorecard match flags when both sides are present.
    def _maybe_match(human_key: str, ai_key: str) -> Optional[bool]:
        human_val = body.get(human_key)
        ai_val = body.get(ai_key)
        if human_val is None or ai_val is None:
            return None
        return human_val == ai_val

    body["dept_match"] = _maybe_match("human_department", "ai_department")
    body["type_match"] = _maybe_match("human_ticket_type", "ai_ticket_type")
    body["owner_match"] = _maybe_match("human_owner", "ai_owner")

    # Stamp synthetic ids / timestamps if the caller didn't.
    body.setdefault("dispatch_item_id", gen_id("di_"))
    body.setdefault("decided_at", now)

    message_id = body["message_id"]
    existing = await db[DECISIONS_COLLECTION].find_one(
        {"message_id": message_id}, {"_id": 0}
    )

    if existing:
        body["updated_at"] = now
        # Preserve original created_at and dispatch_item_id from the first write.
        body["dispatch_item_id"] = existing.get("dispatch_item_id", body["dispatch_item_id"])
        body["created_at"] = existing.get("created_at", now)
        await db[DECISIONS_COLLECTION].update_one(
            {"message_id": message_id},
            {"$set": body},
        )
        emit_kind = "updated"
    else:
        body["created_at"] = now
        body["updated_at"] = now
        await db[DECISIONS_COLLECTION].insert_one({**body})
        emit_kind = "created"

    after = await db[DECISIONS_COLLECTION].find_one(
        {"message_id": message_id}, {"_id": 0}
    )

    # Event-bus emit. No human caller — actor is the n8n service. Identify
    # the actor as "n8n" in user_id so downstream rules can filter by source.
    try:
        await emit_event(
            MODULE_SLUG,
            f"decision.{emit_kind}",
            record=after,
            old_record=existing,
            user_id="n8n",
            user_email=None,
        )
    except Exception:
        logger.exception("emit_event failed for ai_dispatcher.decision.%s message_id=%s",
                         emit_kind, message_id)

    return after
