"""
AI Employee Management Module
Manage AI employees (Conversational AIs and N8N Automated Workflows).
Includes webhook endpoint for receiving execution data from N8N workflows.
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from datetime import datetime, timezone
import uuid
import logging
import hashlib
import secrets
import re

from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from db.session import get_session, get_sessionmaker
from repositories.users import UserRepository
from models import (
    AIEmployeeCreate, AIEmployeeUpdate, AIEmployeeStatusUpdate,
    AIEmployeeWebhookPayload
)
from auth_helper import get_current_user, has_perm
from seeds.seed_tenants import SIMPLIMETA_ROOT_TENANT_ID
from services.ai.agents.marketplace import (
    register_ai_employee,
    unregister_ai_employee,
    derive_employee_use_perms,
)
from services.ai_employee_defaults import (
    DEFAULT_APP_ACCESS_BY_PERSONA,
    apply_persona_defaults,
)
from services.security_logger import log_security_event
from services.field_registry import register_fields, register_module_events, register_module_config
from services.permission_registry import register_module_permissions


# Permissions owned by this module. Registered via the decorator so they land
# in the dynamic permission registry at import time alongside the core perms
# in routes/roles.py. Keep the list focused on AI-employee-specific perms;
# app- or role-scoped perms belong in their respective modules.
@register_module_permissions("ai_employees")
def _ai_employees_permissions():
    return [
        {
            "key": "ai.manage.access",
            "group": "AI Employees",
            "label": "View AI Employee Access Summary",
        },
    ]

logger = logging.getLogger(__name__)

router = APIRouter()

register_module_config("ai_employees", "ai_employees", "employee_id", "AI & Automation")
register_module_events("ai_employees", "ai_employee", ["created", "updated", "deleted", "status_changed"])
register_fields("ai_employees", "ai_employee", [
    {"name": "name", "type": "string", "label": "Employee Name", "description": "Human-readable name of the AI employee"},
    {"name": "type", "type": "string", "label": "Type", "description": "AI employee type (conversational, n8n)"},
    {"name": "role", "type": "string", "label": "Role", "description": "Business role or function of the AI employee"},
    {"name": "status", "type": "string", "label": "Status", "description": "Operational status (active, paused, offline, training)"},
    {"name": "mode", "type": "string", "label": "Mode", "description": "Execution mode (manual, automatic)"},
    {"name": "specialties", "type": "list", "label": "Specialties", "description": "List of specialized domains or functions"},
    {"name": "execution_count", "type": "number", "label": "Execution Count", "description": "Total number of executions or sessions"},
    {"name": "success_count", "type": "number", "label": "Success Count", "description": "Number of successful executions"},
    {"name": "error_count", "type": "number", "label": "Error Count", "description": "Number of failed executions"},
    {"name": "description", "type": "string", "label": "Description", "description": "Purpose and capabilities summary"},
])


def gen_ai_employee_id() -> str:
    return f"ai_{uuid.uuid4().hex[:12]}"


def gen_webhook_token() -> str:
    """Generate a unique webhook token for secure N8N -> AEGISAXIOM communication."""
    return secrets.token_urlsafe(32)


def _gen_ai_user_id() -> str:
    """Synthetic user_id for an AI employee's backing user record."""
    return f"ai_user_{uuid.uuid4().hex[:12]}"


async def _ensure_backing_user(
        db, employee_id: str, name: str, tenant_id: str = SIMPLIMETA_ROOT_TENANT_ID
) -> str:
    """Create (or return) the synthetic ``users`` record backing an AI employee.

    Backs the AI employee with a system user record so that future HTTP-based
    AI integrations can authenticate as the AI. Today, AI tool execution
    happens in-process via ``services/ai/agents/tools/`` and bypasses HTTP —
    so the auto-created user has no session attached. Session creation is
    left to whatever HTTP path is added later (webhook tokens, service
    accounts, etc.).

    The link still matters even without a session: the AI authz middleware
    (`middleware/ai_authz.py`) detects AI requests by looking up
    ``db.ai_employees.find_one({"user_id": <current user>.user_id})``.
    Without a user_id link, that lookup fails and the middleware silently
    no-ops once an HTTP path does land — defeating the enforcement path
    before it ever runs. Auto-creating the user here keeps the link in
    place from day one.

    The synthetic user has:
      * ``role="ai_employee"`` — distinguishable from human users in listings
      * ``is_system=True`` — marks it as machine-managed
      * ``is_active=True`` — required for `get_current_user` to load it
      * ``tenant_id=<arg>`` — Phase 1B.5: the backing user lives in the
        same tenant as the AI employee instance so the marketplace tenant
        gate (1B.3) treats them consistently. Defaults to root for
        backward compatibility with callers that pre-date this argument.
      * a deterministic email of the form ``{employee_id}@ai.aegisaxiom.internal``

    Idempotent: if a user with the matching email already exists (e.g. on a
    backfill path for a legacy employee), reuse it rather than creating a
    duplicate. Note: if a legacy backing user exists with a different
    `tenant_id` than the one passed in, we DO NOT silently change it —
    cross-tenant identity drift is a Phase 1B/2 cleanup concern, and the
    safer default here is to keep the existing record and let an explicit
    migration align them later.
    """
    synthetic_email = f"{employee_id}@ai.aegisaxiom.internal"
    sm = get_sessionmaker()
    async with sm() as pg:
        existing = await UserRepository(pg).get_user_by_email(synthetic_email)
    if existing and existing.user_id:
        return existing.user_id

    user_id = _gen_ai_user_id()
    now = datetime.now(timezone.utc)
    user_doc = {
        "user_id": user_id,
        "email": synthetic_email,
        "name": f"AI: {name}",
        "role": "ai_employee",
        "is_active": True,
        "is_system": True,
        "is_email_verified": True,
        "extra_permissions": [],
        # Hash a random secret so the user record is well-formed even
        # though no one ever logs in as it. Today AI tool execution is
        # in-process and bypasses HTTP entirely; if/when an HTTP path is
        # added later it will need its own session-creation mechanism
        # (webhook tokens, service accounts, etc.).
        "password_hash": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
        "ai_employee_id": employee_id,
        # Phase 1B.5 — backing user shares the tenant of its AI employee
        # so the marketplace tenant gate (1B.3) treats AI sessions as
        # belonging to the right tenant.
        "tenant_id": tenant_id,
        "created_at": now,
        "updated_at": now,
    }
    async with sm() as pg:
        await UserRepository(pg).create_user(user_doc)
        await pg.commit()
    logger.info(
        f"Created backing user {user_id} for AI employee {employee_id} "
        f"in tenant {tenant_id}"
    )
    return user_id


# ---- PERSONA METADATA ----

# Friendly labels + short descriptions for the personas defined in
# `services.ai_employee_defaults.DEFAULT_APP_ACCESS_BY_PERSONA`. Anything not
# listed here falls back to a title-cased version of the key. Kept inline
# rather than added to the defaults module because these strings are
# UI-facing and shouldn't influence permission resolution.
_PERSONA_DESCRIPTIONS: dict[str, str] = {
    "sales_assistant": "CRM read/write across contacts, accounts, deals, and opportunities.",
    "ops_assistant": "Project and field-job visibility, primarily read-only.",
    "finance_assistant": "Invoices and commissions access for finance workflows.",
    "support_assistant": "Read-only access to customer-facing records for support triage.",
    "general_assistant": "Minimal baseline access; expand manually per use case.",
}


@router.get("/ai-employees/personas")
async def list_ai_employee_personas(current_user=Depends(get_current_user)):
    """List the personas available when creating an AI employee.

    Returns the keys defined in `DEFAULT_APP_ACCESS_BY_PERSONA` paired with
    UI-friendly labels and descriptions. Read-only metadata; any
    authenticated user can hit it (the dropdown is on an admin-only form,
    so gating beyond auth would be redundant).
    """
    personas = []
    for key in sorted(DEFAULT_APP_ACCESS_BY_PERSONA.keys()):
        personas.append({
            "key": key,
            "label": key.replace("_", " ").title(),
            "description": _PERSONA_DESCRIPTIONS.get(key, ""),
        })
    return personas


# ---- CRUD OPERATIONS ----


def _user_can_access_employee(employee: dict, current_user) -> bool:
    """Per-employee access check used by the read-side AI employee endpoints.

    Three independent grant paths, any one passes:
      1. ``allowed_user_ids`` — per-user exception.
      2. ``allowed_roles`` — deprecated role grant (kept for backward compat).
      3. Employee-specific ``*.use`` permission(s) the user holds in their
         hydrated permissions/closure (e.g., ``vega.use`` on ``ai_vega``).

    Mirrors ``BaseAgent.user_has_access`` Gates 3-5 minus the broad
    ``ai_employees.view`` escape hatch — listing is intentionally
    narrower than chat so granting ``foo.use`` to a role surfaces only
    that specific employee in the user's sidebar.
    """
    if current_user.user_id in (employee.get("allowed_user_ids") or []):
        return True
    if current_user.role in (employee.get("allowed_roles") or []):
        return True
    for perm in derive_employee_use_perms(employee):
        if has_perm(current_user, perm):
            return True
    return False


def _employee_visible_to_tenant(employee: dict, current_user) -> bool:
    """Phase 1B.3 — Tenant visibility check for AI Employee read endpoints.

    Mirrors the marketplace agent's runtime tenant gate so the AI
    Employees page (which hits this REST surface) matches what the AI
    Chat router considers reachable.

    Fallback semantics match `services/ai/agents/marketplace.py`:
    rows without an explicit `tenant_id` are treated as belonging to
    SIMPLIMETA_ROOT_TENANT_ID. After 1B.4's migration backfills every
    row, the fallback never fires in practice but stays as a safety net.

    Note this is intentionally NOT a permission override — wildcard
    admins in the root tenant still cannot see other tenants' employees
    through this endpoint. Cross-tenant admin views are a separate
    surface (catalog admin UI, 1B.5+).
    """
    employee_tenant = employee.get("tenant_id") or SIMPLIMETA_ROOT_TENANT_ID
    return getattr(current_user, "tenant_id", None) == employee_tenant


@router.get("/ai-employees")
async def list_ai_employees(
        status: str = None,
        type: str = None,
        search: str = None,
        current_user=Depends(get_current_user)
):
    """List all AI employees with optional filters."""
    if not has_perm(current_user, "ai_employees.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    query = {}

    if status:
        query["status"] = status
    if type:
        query["type"] = type
    if search:
        safe_search = re.escape(search)
        query["$or"] = [
            {"name": {"$regex": safe_search, "$options": "i"}},
            {"role": {"$regex": safe_search, "$options": "i"}},
        ]

    employees = await db.ai_employees.find(query, {"_id": 0}).sort("created_at", -1).to_list(1000)

    # Phase 1B.3 — Tenant scoping. Hide rows belonging to other tenants
    # from this read surface. See _employee_visible_to_tenant for the
    # fallback semantics; mirrors the agent registry's runtime gate.
    employees = [emp for emp in employees if _employee_visible_to_tenant(emp, current_user)]

    # Non-admin users only see employees they have explicit access to.
    # Three grant paths: allowed_user_ids, allowed_roles, or the employee-
    # specific *.use permission via the user's hydrated permissions/closure.
    if not has_perm(current_user, "ai_employees.manage"):
        employees = [
            emp for emp in employees
            if _user_can_access_employee(emp, current_user)
        ]

    for emp in employees:
        conn = emp.get("connection")
        if conn and "n8n_api_key" in conn:
            conn["n8n_api_key"] = conn["n8n_api_key"][:4] + "****" if conn["n8n_api_key"] else ""
    return employees


@router.get("/ai-employees/{employee_id}")
async def get_ai_employee(employee_id: str, current_user=Depends(get_current_user)):
    """Get a single AI employee by ID."""
    if not has_perm(current_user, "ai_employees.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    employee = await db.ai_employees.find_one({"employee_id": employee_id}, {"_id": 0})
    if not employee:
        raise HTTPException(status_code=404, detail="AI employee not found")

    # Phase 1B.3 — Tenant scoping. Return 404 (not 403) for cross-tenant
    # access so we don't leak the existence of the employee. Matches the
    # behavior of the access-control 404 check below.
    if not _employee_visible_to_tenant(employee, current_user):
        raise HTTPException(status_code=404, detail="AI employee not found")

    if not has_perm(current_user, "ai_employees.manage"):
        if not _user_can_access_employee(employee, current_user):
            raise HTTPException(status_code=404, detail="AI employee not found")

    conn = employee.get("connection")
    if conn and "n8n_api_key" in conn:
        conn["n8n_api_key"] = conn["n8n_api_key"][:4] + "****" if conn["n8n_api_key"] else ""
    return employee


@router.post("/ai-employees")
async def create_ai_employee(
        data: AIEmployeeCreate,
        apply_defaults: bool = False,
        persona: str | None = None,
        current_user=Depends(get_current_user),
):
    """Create a new AI employee (import).

    Query params:
      - `apply_defaults` (bool): when True, seed `app_access` from the named
        persona's default permission set after insert. Requires `persona`.
      - `persona` (str): one of `DEFAULT_APP_ACCESS_BY_PERSONA` keys
        (`sales_assistant`, `ops_assistant`, `finance_assistant`,
        `support_assistant`, `general_assistant`). Unknown personas are
        rejected with 400 to avoid silent no-ops.
    """
    if not has_perm(current_user, "ai_employees.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    if apply_defaults:
        if not persona:
            raise HTTPException(
                status_code=400,
                detail="persona is required when apply_defaults=true",
            )
        if persona not in DEFAULT_APP_ACCESS_BY_PERSONA:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown persona '{persona}'. Valid: "
                    f"{sorted(DEFAULT_APP_ACCESS_BY_PERSONA.keys())}"
                ),
            )
    db = get_db()

    employee_id = gen_ai_employee_id()
    webhook_token = gen_webhook_token()
    now = datetime.now(timezone.utc).isoformat()

    # Phase 1B.5 + Phase 1C.1 — Tenant stamping with defense-in-depth gate.
    #
    # In the new world every instance is owned by exactly one tenant. The
    # body MAY carry a `tenant_id` (used by the root-admin provisioning
    # flow to create instances on behalf of another tenant), but we
    # cannot honor it unconditionally: a tenant_admin in tenant A would
    # otherwise be able to stamp tenant B on their POST and inject rows
    # into B's namespace. So:
    #
    #   * Root admins (caller is in SIMPLIMETA_ROOT_TENANT_ID) may target
    #     any tenant by passing `tenant_id` in the body.
    #   * Everyone else has their own tenant_id forced onto the row,
    #     regardless of what they sent. We log a warning when the body
    #     value disagrees so attempted cross-tenant writes are visible
    #     in the audit log.
    caller_tenant_id = getattr(current_user, "tenant_id", None) or SIMPLIMETA_ROOT_TENANT_ID
    is_root_caller = caller_tenant_id == SIMPLIMETA_ROOT_TENANT_ID

    if is_root_caller:
        instance_tenant_id = data.tenant_id or caller_tenant_id
    else:
        if data.tenant_id and data.tenant_id != caller_tenant_id:
            logger.warning(
                "Cross-tenant create blocked: user %s in tenant %s attempted "
                "to create AI employee in tenant %s. Forcing caller tenant.",
                getattr(current_user, "user_id", "?"),
                caller_tenant_id,
                data.tenant_id,
            )
        instance_tenant_id = caller_tenant_id

    # Carry-forward fix from Wave 1: the AI authz middleware finds an AI
    # request by looking up `ai_employees.find_one({user_id: <user>.user_id})`.
    # Without this backing user record + user_id link the middleware never
    # fires for AI traffic, silently no-opping every permission check. We
    # auto-create a system user so the link is always present at create time.
    backing_user_id = await _ensure_backing_user(
        db, employee_id, data.name, tenant_id=instance_tenant_id
    )

    employee = {
        "employee_id": employee_id,
        "user_id": backing_user_id,
        "name": data.name,
        "role": data.role,
        "description": data.description or "",
        "avatar": data.avatar or "🤖",
        "type": data.type,  # "conversational" or "n8n"
        "status": data.initial_status or "active",
        "mode": "manual" if data.type == "conversational" else "automatic",

        # Connection details
        "connection": {},

        # Metrics configuration
        "metrics_config": {
            "metric1_label": data.metric1_label or ("Sessions" if data.type == "conversational" else "Executions"),
            "metric2_label": data.metric2_label or ("Avg Response Time" if data.type == "conversational" else "Success Rate"),
            "metric3_label": data.metric3_label or ("Satisfaction" if data.type == "conversational" else "Tasks Completed"),
            "source": data.metrics_source or "manual",
        },

        # Live metrics (updated by webhooks or manually)
        "metrics": {
            "metric1": 0,
            "metric2": 0,
            "metric3": 0,
        },

        # Execution history (populated by webhook)
        "execution_count": 0,
        "success_count": 0,
        "error_count": 0,
        "last_execution_at": None,
        "execution_log": [],  # Last 100 executions

        # Specialties
        "specialties": data.specialties or [],

        # Contractor info
        "contractor": {
            "name": data.contractor_name or "",
            "agreement_id": data.agreement_id or "",
        },

        # Webhook (for N8N -> AEGISAXIOM communication)
        "webhook_token": webhook_token,

        # Access control
        "allowed_roles": data.allowed_roles if hasattr(data, 'allowed_roles') and data.allowed_roles else ["admin"],
        "allowed_user_ids": data.allowed_user_ids if hasattr(data, 'allowed_user_ids') and data.allowed_user_ids else [],

        # Audit
        "created_by": current_user.user_id,
        "created_at": now,
        "updated_at": now,
    }

    # Set connection details based on type
    if data.type == "conversational":
        employee["connection"] = {
            "embedded_url": data.embedded_url or "",
            "embed_code": data.embed_code or "",
        }
    elif data.type == "n8n":
        employee["connection"] = {
            "n8n_instance_url": data.n8n_instance_url or "",
            "n8n_api_key": data.n8n_api_key or "",
            "workflow_id": data.n8n_workflow_id or "",
        }

    # Conversational AI fields (when present)
    if data.system_prompt is not None:
        employee["system_prompt"] = data.system_prompt
    if data.router_description is not None:
        employee["router_description"] = data.router_description
    if data.tools is not None:
        employee["tools"] = data.tools
    if data.state_schema is not None:
        employee["state_schema"] = data.state_schema
    if data.knowledge_base_filter is not None:
        employee["knowledge_base"] = {
            "collection": "ai_employee_knowledge",
            "filter": data.knowledge_base_filter,
        }

    # Phase 1B.2 — Linkage to catalog definition + tenant entitlement.
    #
    # Phase 1B.5 — `tenant_id` is now ALWAYS stamped on new instance
    # rows (auto-derived from the caller earlier in this handler if
    # not explicitly provided). `definition_id` and `assignment_id`
    # remain optional — the legacy import flow doesn't have a catalog
    # entitlement to link to, so those fields are absent from the
    # document. The catalog-driven provisioning path at
    # POST /tenants/{tenant_id}/assignments/{assignment_id}/instances
    # sets all three.
    if data.definition_id is not None:
        employee["definition_id"] = data.definition_id
    employee["tenant_id"] = instance_tenant_id
    if data.assignment_id is not None:
        employee["assignment_id"] = data.assignment_id

    await db.ai_employees.insert_one(employee)

    # Register as a conductor agent so the AI chat can route to it
    await register_ai_employee(db, employee)

    # Apply persona-based default app_access after insert. Done post-insert
    # so the helper can read the employee doc and merge cleanly via $set.
    if apply_defaults and persona:
        await apply_persona_defaults(employee_id, persona)
        # Re-read so the response reflects the seeded app_access
        refreshed = await db.ai_employees.find_one(
            {"employee_id": employee_id}, {"_id": 0}
        )
        if refreshed:
            employee = refreshed

    # Return without _id and with webhook URL
    employee.pop("_id", None)
    employee["webhook_url"] = f"/api/ai-employees/webhook/{employee_id}"

    logger.info(f"AI employee created: {data.name} ({data.type}) by {current_user.user_id}")
    await log_security_event(
        event_type="ai_employee_created", severity="info",
        user_id=current_user.user_id, user_email=current_user.email,
        resource="ai_employee", resource_id=employee_id,
        action="create", outcome="success",
        details={"name": data.name, "type": data.type},
    )
    return employee


@router.put("/ai-employees/{employee_id}")
async def update_ai_employee(
        employee_id: str,
        data: AIEmployeeUpdate,
        current_user=Depends(get_current_user)
):
    """Update an existing AI employee."""
    if not has_perm(current_user, "ai_employees.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    existing = await db.ai_employees.find_one({"employee_id": employee_id})
    if not existing:
        raise HTTPException(status_code=404, detail="AI employee not found")

    # Phase 1C.1 — Defense in depth. The 1B.3 read surface filters foreign
    # tenants out of GET; this write surface must do the same or a
    # caller who guesses an employee_id can still mutate it. 404 (not
    # 403) so the response is indistinguishable from a truly missing id.
    if not _employee_visible_to_tenant(existing, current_user):
        raise HTTPException(status_code=404, detail="AI employee not found")

    update_data = {k: v for k, v in data.dict(exclude_unset=True).items() if v is not None}
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Migration path: if this is a legacy employee created before the
    # user_id auto-link landed and we're touching app_access (the field
    # the AI authz middleware actually consumes), backfill the backing
    # user record now so enforcement starts working on the next request.
    if "app_access" in update_data and not existing.get("user_id"):
        backing_user_id = await _ensure_backing_user(
            db, employee_id, existing.get("name", "AI Employee")
        )
        update_data["user_id"] = backing_user_id

    await db.ai_employees.update_one(
        {"employee_id": employee_id},
        {"$set": update_data}
    )

    updated = await db.ai_employees.find_one({"employee_id": employee_id}, {"_id": 0})
    logger.info(f"AI employee updated: {employee_id} by {current_user.user_id}")
    return updated


@router.patch("/ai-employees/{employee_id}/status")
async def update_ai_employee_status(
        employee_id: str,
        data: AIEmployeeStatusUpdate,
        current_user=Depends(get_current_user)
):
    """Toggle AI employee status (active/paused/offline/training)."""
    if not has_perm(current_user, "ai_employees.manage"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    existing = await db.ai_employees.find_one({"employee_id": employee_id})
    if not existing:
        raise HTTPException(status_code=404, detail="AI employee not found")

    # Phase 1C.1 — Defense in depth. See update_ai_employee for rationale.
    if not _employee_visible_to_tenant(existing, current_user):
        raise HTTPException(status_code=404, detail="AI employee not found")

    await db.ai_employees.update_one(
        {"employee_id": employee_id},
        {"$set": {
            "status": data.status,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }}
    )

    logger.info(f"AI employee {employee_id} status changed to {data.status} by {current_user.user_id}")

    # Update conductor registry based on new status
    if data.status in ("active", "training"):
        emp = await db.ai_employees.find_one({"employee_id": employee_id}, {"_id": 0})
        if emp:
            await register_ai_employee(db, emp)
    else:
        unregister_ai_employee(employee_id)

    return {"message": f"Status updated to {data.status}", "employee_id": employee_id}


@router.delete("/ai-employees/{employee_id}")
async def delete_ai_employee(employee_id: str, current_user=Depends(get_current_user)):
    """Delete an AI employee."""
    if not has_perm(current_user, "ai_employees.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    existing = await db.ai_employees.find_one({"employee_id": employee_id})
    if not existing:
        raise HTTPException(status_code=404, detail="AI employee not found")

    # Phase 1C.1 — Defense in depth. See update_ai_employee for rationale.
    if not _employee_visible_to_tenant(existing, current_user):
        raise HTTPException(status_code=404, detail="AI employee not found")

    # Remove from conductor's agent registry
    unregister_ai_employee(employee_id)

    await db.ai_employees.delete_one({"employee_id": employee_id})
    logger.info(f"AI employee deleted: {employee_id} by {current_user.user_id}")
    await log_security_event(
        event_type="ai_employee_deleted", severity="warning",
        user_id=current_user.user_id, user_email=current_user.email,
        resource="ai_employee", resource_id=employee_id,
        action="delete", outcome="success",
        details={"name": existing.get("name", "")},
    )
    return {"message": "AI employee deleted", "employee_id": employee_id}


# ---- WEBHOOK ENDPOINT ----
# This endpoint receives execution data from N8N workflows.
# No auth required (uses webhook token for verification).
#
# Phase 1C.1 — Intentionally NOT tenant-gated. The webhook_token is the
# auth boundary: it's per-employee, regenerated on rotate, and only
# distributed to the N8N workflow that owns it. A caller possessing both
# a valid employee_id AND the matching webhook_token is by definition
# the legitimate caller for that employee, regardless of which tenant
# the caller's session (if any) belongs to. Layering a session-tenant
# check on top would break the bearer-token model — N8N has no AEGISAXIOM
# session — without adding any real isolation.

@router.post("/ai-employees/webhook/{employee_id}")
async def receive_webhook(employee_id: str, request: Request):
    """
    Receive execution data from N8N workflows.

    N8N sends a POST to this endpoint after each workflow execution.
    The data is used to update the AI employee's live metrics.

    Expected payload:
    {
        "workflow_id": "xEP3XPYs5WCZQo8K",
        "status": "success",
        "label": "Notification",
        "subject": "Email subject",
        "execution_time_ms": 1500,
        "timestamp": "2026-03-27T14:30:00Z",
        "webhook_token": "the_secret_token"
    }
    """
    db = get_db()

    # Find the AI employee
    employee = await db.ai_employees.find_one({"employee_id": employee_id})
    if not employee:
        raise HTTPException(status_code=404, detail="AI employee not found")

    # Parse the incoming payload (flexible — accept whatever N8N sends)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Verify webhook token (required)
    provided_token = body.get("webhook_token")
    if not provided_token or provided_token != employee.get("webhook_token"):
        raise HTTPException(status_code=403, detail="Invalid or missing webhook token")

    # Extract execution data
    status = body.get("status", "success")
    label = body.get("label") or body.get("labelId") or body.get("label_applied", "")
    subject = body.get("subject", "")
    execution_time_ms = body.get("execution_time_ms", 0)
    workflow_id = body.get("workflow_id", "")
    timestamp = body.get("timestamp", datetime.now(timezone.utc).isoformat())

    # Build execution log entry
    log_entry = {
        "timestamp": timestamp,
        "status": status,
        "label": label,
        "subject": subject,
        "execution_time_ms": execution_time_ms,
        "workflow_id": workflow_id,
        "raw_data": {k: v for k, v in body.items() if k != "webhook_token"},
    }

    # Update counters
    is_success = status.lower() in ["success", "ok", "completed", "true"]
    execution_count = employee.get("execution_count", 0) + 1
    success_count = employee.get("success_count", 0) + (1 if is_success else 0)
    error_count = employee.get("error_count", 0) + (0 if is_success else 1)

    # Calculate live metrics
    success_rate = round((success_count / execution_count) * 100) if execution_count > 0 else 0

    # Get existing execution log, keep last 100
    existing_log = employee.get("execution_log", [])
    existing_log.insert(0, log_entry)
    existing_log = existing_log[:100]

    # Calculate avg execution time from log
    times = [e.get("execution_time_ms", 0) for e in existing_log if e.get("execution_time_ms", 0) > 0]
    avg_execution_time = round(sum(times) / len(times)) if times else 0

    # Update the AI employee
    update = {
        "execution_count": execution_count,
        "success_count": success_count,
        "error_count": error_count,
        "last_execution_at": timestamp,
        "execution_log": existing_log,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        # Update the 3 display metrics
        "metrics.metric1": execution_count,  # Total executions/classifications
        "metrics.metric2": success_rate,      # Success rate %
        "metrics.metric3": avg_execution_time, # Avg execution time
    }

    await db.ai_employees.update_one(
        {"employee_id": employee_id},
        {"$set": update}
    )

    logger.info(f"Webhook received for AI employee {employee_id}: status={status}, label={label}")

    return {
        "received": True,
        "employee_id": employee_id,
        "execution_count": execution_count,
        "success_rate": success_rate,
    }


# ---- WEBHOOK INFO ----

@router.get("/ai-employees/{employee_id}/webhook-info")
async def get_webhook_info(employee_id: str, current_user=Depends(get_current_user)):
    """Get the webhook URL and token for an AI employee (so user can give it to N8N)."""
    if not has_perm(current_user, "ai_employees.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    employee = await db.ai_employees.find_one({"employee_id": employee_id}, {"_id": 0})
    if not employee:
        raise HTTPException(status_code=404, detail="AI employee not found")

    # Phase 1C.1 — Defense in depth. The webhook token is a credential;
    # never leak it across tenant boundaries.
    if not _employee_visible_to_tenant(employee, current_user):
        raise HTTPException(status_code=404, detail="AI employee not found")

    return {
        "employee_id": employee_id,
        "webhook_url": f"/api/ai-employees/webhook/{employee_id}",
        "webhook_token": employee.get("webhook_token", ""),
        "sample_payload": {
            "workflow_id": employee.get("connection", {}).get("workflow_id", "your_workflow_id"),
            "status": "success",
            "label": "example_label",
            "subject": "Example subject",
            "execution_time_ms": 1500,
            "timestamp": "2026-03-27T14:30:00Z",
            "webhook_token": employee.get("webhook_token", "")
        }
    }


# ---- EXECUTION LOG ----

@router.get("/ai-employees/{employee_id}/executions")
async def get_execution_log(
        employee_id: str,
        limit: int = 50,
        current_user=Depends(get_current_user)
):
    """Get recent execution log for an AI employee."""
    if not has_perm(current_user, "ai_employees.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    employee = await db.ai_employees.find_one({"employee_id": employee_id}, {"_id": 0})
    if not employee:
        raise HTTPException(status_code=404, detail="AI employee not found")

    # Phase 1C.1 — Defense in depth. Execution logs may contain PII from
    # the tenant's workflows (subjects, labels, raw N8N payloads). Never
    # surface them across tenant boundaries.
    if not _employee_visible_to_tenant(employee, current_user):
        raise HTTPException(status_code=404, detail="AI employee not found")

    log = employee.get("execution_log", [])[:limit]

    return {
        "employee_id": employee_id,
        "total_executions": employee.get("execution_count", 0),
        "success_count": employee.get("success_count", 0),
        "error_count": employee.get("error_count", 0),
        "executions": log,
    }


@router.delete("/ai-employees/{employee_id}/user-state")
async def reset_user_state(employee_id: str, current_user=Depends(get_current_user)):
    """Reset the current user's state for a conversational AI employee.

    Clears quiz_in_progress, progress, and all training state so the user
    can start fresh.
    """
    if not has_perm(current_user, "ai_employees.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()

    # Phase 1C.1 — Defense in depth. Even though this endpoint only
    # touches the calling user's OWN state rows, we still verify the
    # target employee belongs to the caller's tenant. Without this
    # check, a tenant_admin probing endpoint shape could distinguish
    # "no state for me" from "employee doesn't exist" against another
    # tenant's employees. Fetch projection is minimal — we only need
    # tenant_id for the visibility check.
    employee = await db.ai_employees.find_one(
        {"employee_id": employee_id},
        {"_id": 0, "employee_id": 1, "tenant_id": 1},
    )
    if not employee:
        raise HTTPException(status_code=404, detail="AI employee not found")
    if not _employee_visible_to_tenant(employee, current_user):
        raise HTTPException(status_code=404, detail="AI employee not found")

    result = await db.ai_employee_user_state.delete_one(
        {"user_id": current_user.user_id, "employee_id": employee_id}
    )
    # Also clean up any pending artifacts
    await db.ai_chat_artifacts.update_many(
        {"user_id": current_user.user_id, "employee_id": employee_id, "status": "pending"},
        {"$set": {"status": "cancelled"}}
    )
    logger.info(f"User state reset for {current_user.user_id}/{employee_id}")
    return {"reset": True, "deleted": result.deleted_count > 0}


# ---- ACCESS CONTROL ----

def _derived_use_perms(employee_id: str, employee_doc: dict) -> list[str]:
    """Permissions that grant access to an AI employee via RBAC.

    Explicit ``employee_doc.required_permissions`` wins. Otherwise we derive
    one from the employee_id by stripping the ``ai_`` prefix and appending
    ``.use`` — so ``ai_vega`` resolves to ``vega.use``, ``ai_arc`` to
    ``arc.use``, etc.  Without this fallback the only way to grant a role
    access to an employee was to add it explicitly to ``allowed_roles``,
    even if the role already had ``vega.use`` in the global RBAC array.
    """
    explicit = employee_doc.get("required_permissions") or []
    if isinstance(explicit, list) and explicit:
        return [p for p in explicit if isinstance(p, str)]
    if employee_id.startswith("ai_"):
        short = employee_id[3:]
        if short:
            return [f"{short}.use"]
    return []


@router.get("/ai-employees/{employee_id}/access")
async def get_ai_employee_access(employee_id: str, current_user=Depends(get_current_user)):
    """Get access configuration for an AI employee.

    Returns explicit ``allowed_roles`` AND ``roles_with_permission`` — the set
    of roles whose RBAC permission list already implies access via one of
    the employee's required ``*.use`` permissions. The UI checks both lists
    so a role granted ``vega.use`` shows as checked without needing to be
    re-added in the AI Employee access panel.
    """
    if not has_perm(current_user, "ai_employees.manage"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    employee = await db.ai_employees.find_one(
        {"employee_id": employee_id},
        {"_id": 0, "employee_id": 1, "name": 1, "allowed_roles": 1,
         "allowed_user_ids": 1, "required_permissions": 1, "tenant_id": 1}
    )
    if not employee:
        raise HTTPException(status_code=404, detail="AI employee not found")

    # Phase 1C.1 — Defense in depth. Access configuration reveals role
    # and user names that may be tenant-specific; gate accordingly.
    if not _employee_visible_to_tenant(employee, current_user):
        raise HTTPException(status_code=404, detail="AI employee not found")

    required_perms = _derived_use_perms(employee_id, employee)

    roles_with_permission: list[str] = []
    if required_perms:
        cursor = db.roles.find(
            {"permissions": {"$in": required_perms}},
            {"_id": 0, "name": 1},
        )
        roles_with_permission = sorted({r["name"] async for r in cursor if r.get("name")})

    return {
        "employee_id": employee_id,
        "name": employee.get("name"),
        "allowed_roles": employee.get("allowed_roles", []),
        "allowed_user_ids": employee.get("allowed_user_ids", []),
        "required_permissions": required_perms,
        "roles_with_permission": roles_with_permission,
    }


@router.put("/ai-employees/{employee_id}/access")
async def update_ai_employee_access(employee_id: str, data: dict, current_user=Depends(get_current_user), pg: AsyncSession = Depends(get_session)):
    """Update access configuration for an AI employee."""
    if not has_perm(current_user, "ai_employees.manage"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db = get_db()
    employee = await db.ai_employees.find_one({"employee_id": employee_id})
    if not employee:
        raise HTTPException(status_code=404, detail="AI employee not found")

    # Phase 1C.1 — Defense in depth. Write surface — must mirror the GET
    # access tenant gate or a caller who guessed an employee_id could
    # rewrite another tenant's allowed_roles / allowed_user_ids.
    if not _employee_visible_to_tenant(employee, current_user):
        raise HTTPException(status_code=404, detail="AI employee not found")

    update = {}
    if "allowed_roles" in data:
        # Validate roles exist
        valid_roles = {r["name"] for r in await db.roles.find({}, {"_id": 0, "name": 1}).to_list(100)}
        invalid = [r for r in data["allowed_roles"] if r not in valid_roles]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid roles: {', '.join(invalid)}")
        update["allowed_roles"] = data["allowed_roles"]

    if "allowed_user_ids" in data:
        # Validate user IDs exist
        users_repo = UserRepository(pg)
        for uid in data["allowed_user_ids"]:
            user = await users_repo.get_user_by_id(uid)
            if not user:
                raise HTTPException(status_code=400, detail=f"User not found: {uid}")
        update["allowed_user_ids"] = data["allowed_user_ids"]

    if update:
        update["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db.ai_employees.update_one(
            {"employee_id": employee_id},
            {"$set": update}
        )
        await log_security_event(
            event_type="ai_access_changed", severity="info",
            user_id=current_user.user_id, user_email=current_user.email,
            resource="ai_employee", resource_id=employee_id,
            action="update_access", outcome="success",
            details={
                "allowed_roles": update.get("allowed_roles"),
                "allowed_user_ids": update.get("allowed_user_ids"),
            },
        )

    return {"message": "Access updated", "employee_id": employee_id}


# ---- ACCESS SUMMARY (admin visibility) ----

@router.get("/ai-employees/{employee_id}/access-summary")
async def get_ai_employee_access_summary(
        employee_id: str,
        current_user=Depends(get_current_user),
):
    """Per-app breakdown of an AI employee's effective permissions.

    Distinct from ``get_flattened_ai_permissions`` (in middleware/ai_authz.py),
    which returns a single flat set for runtime authz. This endpoint preserves
    the per-app grouping so the admin UI can show which app contributes which
    permissions.

    Returns the raw `app_access` shape (no implication expansion). Wave 3 UI
    layers implication-derived perms on top via a separate fetch once
    Thread 2D's resolver is wired in.
    """
    if not (
            has_perm(current_user, "ai.manage.access")
            or has_perm(current_user, "ai_employees.manage")
            or current_user.role == "admin"
    ):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    employee = await db.ai_employees.find_one(
        {"employee_id": employee_id},
        {"_id": 0, "employee_id": 1, "name": 1, "app_access": 1, "tenant_id": 1},
    )
    if not employee:
        raise HTTPException(status_code=404, detail="AI employee not found")

    # Phase 1C.1 — Defense in depth. The per-app permission grid is
    # tenant-specific configuration; never expose it cross-tenant.
    if not _employee_visible_to_tenant(employee, current_user):
        raise HTTPException(status_code=404, detail="AI employee not found")

    # Group by app_id so that an employee with multiple `app_access` entries
    # for the same app (legitimate after a re-link) shows up once with the
    # union of permissions. Preserve the first-seen ordering so the response
    # stays stable across calls.
    by_app_index: dict[str, dict] = {}
    by_app_order: list[str] = []
    seen_perms: set[str] = set()
    for entry in employee.get("app_access") or []:
        app_id = entry.get("app_id")
        perms = [p for p in (entry.get("permissions") or []) if isinstance(p, str)]
        # total_permissions counts unique perm keys across apps. An AI granted
        # the same perm by two different bundles should only count once.
        seen_perms.update(perms)
        if app_id not in by_app_index:
            by_app_index[app_id] = {
                "app_id": app_id,
                "app_slug": entry.get("app_slug"),
                "app_name": entry.get("app_name"),
                "permissions": list(perms),
            }
            by_app_order.append(app_id)
        else:
            existing = by_app_index[app_id]
            existing_perms = set(existing["permissions"])
            existing_perms.update(perms)
            existing["permissions"] = sorted(existing_perms)
            # Fill in slug/name from later entries if the first one was missing them.
            if not existing.get("app_slug") and entry.get("app_slug"):
                existing["app_slug"] = entry.get("app_slug")
            if not existing.get("app_name") and entry.get("app_name"):
                existing["app_name"] = entry.get("app_name")
    by_app = [by_app_index[app_id] for app_id in by_app_order]
    total = len(seen_perms)

    return {
        "employee_id": employee.get("employee_id"),
        "name": employee.get("name"),
        "total_permissions": total,
        "by_app": by_app,
    }


# ---- APP -> AI EMPLOYEES (reverse lookup) ----

@router.get("/apps/{app_id}/ai-employees")
async def list_ai_employees_for_app(
        app_id: str,
        current_user=Depends(get_current_user),
):
    """List every AI employee that has an `app_access` entry for the given app.

    Used by the admin UI when viewing an installed app: "which AIs are
    currently granted access here?". Each result projects only the permissions
    relevant to this specific app, not the employee's full app_access set.
    """
    if not (
            has_perm(current_user, "ai.manage.access")
            or has_perm(current_user, "ai_employees.manage")
            or current_user.role == "admin"
    ):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    cursor = db.ai_employees.find(
        {"app_access.app_id": app_id},
        {"_id": 0, "employee_id": 1, "name": 1, "app_access": 1, "tenant_id": 1},
    )
    employees = await cursor.to_list(length=1000)

    # Phase 1C.1 — Defense in depth. Mirror the main list endpoint's
    # tenant scoping (routes/ai_employees.py:245). Without this filter,
    # any user with `ai.manage.access` or `ai_employees.manage` could
    # enumerate AI employees from every tenant that has linked an
    # ai_employee to the requested app_id.
    employees = [emp for emp in employees if _employee_visible_to_tenant(emp, current_user)]

    results = []
    for emp in employees:
        # Filter app_access down to entries matching this app_id. There can
        # legitimately be multiple entries (e.g. re-link with a different
        # permission set after re-ingest), so union their permission lists.
        relevant_perms: set[str] = set()
        for entry in emp.get("app_access") or []:
            if entry.get("app_id") != app_id:
                continue
            for p in entry.get("permissions") or []:
                if isinstance(p, str):
                    relevant_perms.add(p)
        results.append({
            "employee_id": emp.get("employee_id"),
            "name": emp.get("name"),
            "permissions_for_this_app": sorted(relevant_perms),
        })

    return results