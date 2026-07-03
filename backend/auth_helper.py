from fastapi import HTTPException, Cookie, Header
from typing import Optional
from datetime import datetime, timezone, timedelta
from passlib.context import CryptContext
from jose import jwt, JWTError
import os
import time
import uuid
import logging

from database import get_db
from services.security_logger import log_security_event
from services.permission_implications import resolve_implications
from services.permission_registry import get_implication_graph

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# SECURITY FIX H-3 plus PR cleanup A7. JWT_SECRET is required. A
# blank value or the .env.example placeholder REPLACE_ME_BEFORE_DEPLOY
# is rejected so a copy paste deploy that forgot to fill the file in
# fails fast at import time instead of silently signing tokens with
# a guessable shared string.
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET or JWT_SECRET == "REPLACE_ME_BEFORE_DEPLOY":
    raise ValueError(
        "JWT_SECRET environment variable is required for security. "
        "Generate a strong secret with openssl rand -hex 32"
    )

INVITE_EXPIRY_HOURS = 48


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_session_token() -> str:
    return f"sess_{uuid.uuid4().hex}"


def create_invite_token(email: str, user_id: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "type": "invite",
        "exp": datetime.now(timezone.utc) + timedelta(hours=INVITE_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def verify_invite_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        if payload.get("type") != "invite":
            raise ValueError("Invalid token type")
        return payload
    except JWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired invite token")


# ---- Permission helper ----

# Process-level cache for the implication graph. Used as the fallback
# closure source when a User instance was constructed outside of
# get_current_user() and therefore has no precomputed closure stashed
# on it. The TTL is intentionally short — the registry rarely changes,
# but operators editing it should not have to wait minutes for the
# change to take effect across long-lived workers.
_IMPLICATION_GRAPH_TTL_SECONDS = 60.0
_implication_graph_cache: dict = {
    "graph": None,        # last fetched dict[str, list[str]]
    "fetched_at": 0.0,    # monotonic timestamp of last fetch
}


async def get_cached_implication_graph() -> dict:
    """Return the registry implication graph, refetching at most every 60s.

    Public entry point for callers that need the resolved registry-declared
    implication graph — both the in-module `has_perm` async fallbacks and
    cross-module callers (e.g. `routes/auth._build_user_with_permissions`).
    The graph is small (one entry per active permission) and rarely changes,
    so this trades a tiny window of staleness for a vastly cheaper hot-path
    check.
    """
    now = time.monotonic()
    cached_at = _implication_graph_cache["fetched_at"]
    cached_graph = _implication_graph_cache["graph"]
    if cached_graph is not None and (now - cached_at) < _IMPLICATION_GRAPH_TTL_SECONDS:
        return cached_graph
    try:
        graph = await get_implication_graph()
    except Exception:
        # Never fail an authz check because the graph fetch errored.
        # Fall back to whatever we last had, or an empty graph.
        logger.exception("Failed to refresh permission implication graph; using stale cache")
        return cached_graph if cached_graph is not None else {}
    _implication_graph_cache["graph"] = graph
    _implication_graph_cache["fetched_at"] = now
    return graph


def _user_perms_and_closure(user):
    """Return (perms_iterable, closure_or_None) for a user.

    Closure is None when the user was built outside get_current_user
    (e.g., in tests or internal services that hand-construct a user
    record). Callers must handle the None case explicitly — typically
    by falling back to a direct membership check.
    """
    if isinstance(user, dict):
        perms = user.get("permissions", []) or []
        closure = user.get("_perm_closure")
    else:
        perms = getattr(user, "permissions", []) or []
        closure = getattr(user, "_perm_closure", None)
    return perms, closure


def has_perm(user, perm: str) -> bool:
    """Check if a user holds (or transitively implies) a permission.

    Order of checks, fastest first:
      1. ``*`` wildcard in user's explicit permissions — short-circuits True.
      2. Exact match against user's explicit permissions.
      3. Membership in the precomputed implication closure stashed on
         the user by ``get_current_user`` (covers static convention +
         registry-declared edges).

    Sync by design: 466+ call sites across the codebase rely on the
    sync signature, and changing it would require an audit of every
    caller. The expensive work (closure resolution) happens once per
    request inside ``get_current_user`` (which is async) and is
    stashed on the User object so this hot-path stays O(1).

    For users constructed outside ``get_current_user`` (no closure
    stashed), this falls back to the original direct-membership check
    so legacy code paths remain correct — but they will not see
    transitive implications. Async code that needs the full closure
    for such users should call ``has_perm_async`` instead.
    """
    perms, closure = _user_perms_and_closure(user)
    # Fast paths.
    if "*" in perms:
        return True
    if perm in perms:
        return True
    # Closure path (precomputed at auth time).
    if closure is not None:
        return perm in closure
    # No closure available — degrade to direct membership.
    # Callers that need transitive resolution in this case should use
    # has_perm_async, which can fetch the registry graph and compute on
    # demand.
    return False


async def has_perm_async(user, perm: str) -> bool:
    """Async variant of ``has_perm`` that resolves the closure on demand.

    Used by ``tests/test_has_perm_implications.py`` to exercise the
    registry-graph fetch path. Kept for use by future synthetic-user
    callers (e.g., background jobs) that lack the request-scoped
    closure stashed by ``get_current_user``. For request-scoped users,
    prefer the sync ``has_perm`` — the closure is already precomputed
    and stashed on the User instance.
    """
    perms, closure = _user_perms_and_closure(user)
    if "*" in perms:
        return True
    if perm in perms:
        return True
    if closure is not None:
        return perm in closure
    graph = await get_cached_implication_graph()
    resolved = resolve_implications(set(perms), registry_graph=graph)
    return perm in resolved


async def require_perm(user, perm: str, *, resource: str = None, resource_id: str = None, action: str = None):
    """Raise 403 and emit authz_failed audit event if user lacks the permission."""
    if has_perm(user, perm):
        return
    from services.security_logger import log_security_event
    user_id = user.get("user_id") if isinstance(user, dict) else getattr(user, "user_id", None)
    user_email = user.get("email") if isinstance(user, dict) else getattr(user, "email", None)
    await log_security_event(
        event_type="authz_failed",
        severity="warning",
        user_id=user_id,
        user_email=user_email,
        resource=resource,
        resource_id=resource_id,
        action=action,
        outcome="denied",
        details={"required_permission": perm},
    )
    raise HTTPException(status_code=403, detail=f"{perm} permission required")


async def get_current_user(
    session_token: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    from models import User

    db = get_db()
    token = None
    if session_token:
        token = session_token
    elif authorization and authorization.startswith("Bearer "):
        token = authorization[7:]

    if not token:
        await log_security_event(
            event_type="auth_failed", severity="warning",
            action="token_check", outcome="denied",
            details={"reason": "no_token"},
        )
        raise HTTPException(status_code=401, detail="Not authenticated")

    session = await db.user_sessions.find_one({"session_token": token}, {"_id": 0})
    if not session:
        await log_security_event(
            event_type="auth_failed", severity="warning",
            action="session_check", outcome="denied",
            details={"reason": "invalid_session"},
        )
        raise HTTPException(status_code=401, detail="Invalid session")

    expires_at = session["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        await log_security_event(
            event_type="auth_failed", severity="info",
            user_id=session.get("user_id"),
            action="session_check", outcome="denied",
            details={"reason": "session_expired"},
        )
        raise HTTPException(status_code=401, detail="Session expired")

    user_doc = await db.users.find_one({"user_id": session["user_id"]}, {"_id": 0})
    if not user_doc:
        await log_security_event(
            event_type="auth_failed", severity="warning",
            user_id=session["user_id"],
            action="user_lookup", outcome="denied",
            details={"reason": "user_not_found"},
        )
        raise HTTPException(status_code=401, detail="User not found")

    if not user_doc.get("is_active", True):
        await log_security_event(
            event_type="auth_failed", severity="warning",
            user_id=user_doc.get("user_id"),
            user_email=user_doc.get("email"),
            action="activation_check", outcome="denied",
            details={"reason": "account_deactivated", "user_id": user_doc.get("user_id")},
        )
        raise HTTPException(status_code=403, detail="Account is deactivated")

    # Load permissions from roles collection + per-user overrides
    role_name = user_doc.get("role", "customer")
    role_doc = await db.roles.find_one({"name": role_name}, {"_id": 0, "permissions": 1})
    role_permissions = role_doc.get("permissions", []) if role_doc else []
    user_extras = user_doc.get("extra_permissions", [])
    permissions = list(set(role_permissions + user_extras))

    # ---- Permission closure precomputation ----
    # Resolve the transitive closure of the user's permissions once per
    # request (here, inside the already-async auth path) so the sync
    # has_perm() helper can stay O(1) on its hot path. Combines static
    # convention edges (delete > edit > view, all > own) with registry-
    # declared `implies` edges.
    #
    # The closure is exposed two ways, both populated here:
    #   - `effective_permissions` — a serializable Pydantic field, passed
    #     to the User constructor so the frontend can read closure-aware
    #     permissions from /api/auth/me without re-implementing the
    #     implication graph in JS. This matches the construction pattern
    #     used by routes/auth._build_user_with_permissions — both auth
    #     paths build the field via the constructor dict, never via
    #     post-hoc attribute mutation.
    #   - `_perm_closure` — a runtime-only stash via object.__setattr__,
    #     consumed by has_perm()'s synchronous fast path. Intentionally
    #     not a Pydantic field so it never leaks into JSON.
    #
    # Backend authorization never trusts effective_permissions from the
    # wire — route guards always go through has_perm() which uses the
    # runtime _perm_closure (or recomputes via has_perm_async).
    #
    # If the registry graph fetch fails, we still build a closure
    # computed from convention only — never block a request because
    # the implication graph is momentarily unavailable.
    try:
        registry_graph = await get_cached_implication_graph()
    except Exception:
        logger.exception("Failed to load implication graph during auth; falling back to convention-only closure")
        registry_graph = {}
    perm_closure = resolve_implications(set(permissions), registry_graph=registry_graph)

    safe_doc = {k: v for k, v in user_doc.items() if k != "password_hash"}
    safe_doc["permissions"] = permissions
    safe_doc["extra_permissions"] = user_extras
    # Sorted for stable JSON output across requests (helps client-side
    # caching / diffing).
    safe_doc["effective_permissions"] = sorted(perm_closure)
    user = User(**safe_doc)
    object.__setattr__(user, "_perm_closure", perm_closure)

    # ---- AI employee detection ----
    # If this user record is the underlying identity for an AI employee,
    # tag the User instance so middleware/ai_authz can route the request
    # through the AI permission check. Humans get is_ai_employee=False
    # and the dependency is a no-op for them.
    #
    # We use object.__setattr__ because the User pydantic model declares
    # a fixed schema and v2 BaseModel.__setattr__ rejects unknown attrs.
    # These are runtime-only flags consumed by middleware — they
    # intentionally do not become part of the serialized model.
    ai_doc = await db.ai_employees.find_one(
        {"user_id": user_doc["user_id"]},
        {"_id": 0, "employee_id": 1},
    )
    if ai_doc:
        object.__setattr__(user, "is_ai_employee", True)
        object.__setattr__(user, "ai_employee_id", ai_doc.get("employee_id"))
    else:
        object.__setattr__(user, "is_ai_employee", False)
        object.__setattr__(user, "ai_employee_id", None)

    return user


def require_roles(*roles):
    from fastapi import Depends

    async def checker(current_user=Depends(get_current_user)):
        if current_user.role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Required: {', '.join(roles)}",
            )
        return current_user

    return checker


# ---- RBAC Filter Functions (permission-driven) ----

async def get_deal_filter(user) -> dict:
    # Admins see everything
    if has_perm(user, "deals.view.all"):
        return {}
    
    # Contractors: filter by contracts they're assigned to
    if user.role == "contractor":
        # Get all contract IDs where contractor is in resources
        db = get_db()
        contracts = await db.contracts.find(
            {"resources.user_id": user.user_id},
            {"_id": 0, "account_id": 1}
        ).to_list(1000)
        account_ids = list(set([c["account_id"] for c in contracts if c.get("account_id")]))
        if not account_ids:
            return {"_id": {"$exists": False}}
        return {"account_id": {"$in": account_ids}}
    
    # Sales reps, agents, customers: existing logic
    elif has_perm(user, "deals.view.own"):
        conditions = [{"deal_owner_id": user.user_id}, {"assigned_sales_rep_id": user.user_id}, {"assigned_agent_id": user.user_id}]
        if user.account_id:
            conditions.append({"account_id": user.account_id})
        return {"$or": conditions}
    
    return {"_id": {"$exists": False}}


async def get_account_filter(user) -> dict:
    # Admins see everything
    if has_perm(user, "accounts.view.all"):
        return {}
    
    # Contractors: filter by contracts they're assigned to
    if user.role == "contractor":
        # Get all account IDs from contracts where contractor is in resources
        db = get_db()
        contracts = await db.contracts.find(
            {"resources.user_id": user.user_id},
            {"_id": 0, "account_id": 1}
        ).to_list(1000)
        account_ids = list(set([c["account_id"] for c in contracts if c.get("account_id")]))
        if not account_ids:
            return {"_id": {"$exists": False}}
        return {"account_id": {"$in": account_ids}}

    elif has_perm(user, "accounts.view.own"):
        conditions = [
            {"assigned_sales_rep_id": user.user_id},
            {"assigned_agent_id": user.user_id},
            {"created_by": user.user_id},
        ]
        if user.account_id:
            conditions.append({"account_id": user.account_id})
        return {"$or": conditions}
    
    return {"_id": {"$exists": False}}


def get_commission_filter(user) -> dict:
    if has_perm(user, "commissions.view.all"):
        return {}
    elif has_perm(user, "commissions.view.own"):
        return {"user_id": user.user_id}
    return {"_id": {"$exists": False}}


async def get_invoice_filter(user) -> dict:
    # Admins see everything
    if has_perm(user, "invoices.view.all"):
        return {}
    
    # Contractors: filter by contracts they're assigned to
    if user.role == "contractor":
        # Get all contract IDs where contractor is in resources
        db = get_db()
        contracts = await db.contracts.find(
            {"resources.user_id": user.user_id},
            {"_id": 0, "contract_id": 1}
        ).to_list(1000)
        contract_ids = [c["contract_id"] for c in contracts]
        if not contract_ids:
            return {"_id": {"$exists": False}}
        return {"contract_id": {"$in": contract_ids}}
    
    # Sales reps, agents: existing logic
    elif has_perm(user, "invoices.view.own"):
        conditions = [{"assigned_sales_rep_id": user.user_id}]
        if user.account_id:
            conditions.append({"account_id": user.account_id})
        return {"$or": conditions}
    
    return {"_id": {"$exists": False}}


async def get_contract_filter(user) -> dict:
    # Admins and directors with .view.all see everything (no contract filter)
    if has_perm(user, "contracts.view.all"):
        return {}
    
    # Contractors: filter by resource assignment in contracts
    if user.role == "contractor":
        return {"resources.user_id": user.user_id}
    
    # Sales reps, agents: filter by assignment fields
    elif has_perm(user, "contracts.view.own"):
        conditions = [{"assigned_sales_rep_id": user.user_id}]
        if user.account_id:
            conditions.append({"account_id": user.account_id})
        return {"$or": conditions}

    
    return {"_id": {"$exists": False}}


async def get_job_filter(user) -> dict:
    # Admins see everything
    if has_perm(user, "field_jobs.view.all"):
        return {}
    
    # Contractors: filter by contracts they're assigned to
    if user.role == "contractor":
        # Get all account IDs from contracts where contractor is in resources
        db = get_db()
        contracts = await db.contracts.find(
            {"resources.user_id": user.user_id},
            {"_id": 0, "account_id": 1}
        ).to_list(1000)
        account_ids = list(set([c["account_id"] for c in contracts if c.get("account_id")]))
        if not account_ids:
            return {"_id": {"$exists": False}}
        return {"account_id": {"$in": account_ids}}
    
    # Sales reps, agents, techs: existing logic
    elif has_perm(user, "field_jobs.view.own"):
        conditions = [{"assigned_sales_rep_id": user.user_id}, {"assigned_tech_id": user.user_id}]
        if user.account_id:
            conditions.append({"account_id": user.account_id})
        return {"$or": conditions}
    
    return {"_id": {"$exists": False}}


async def get_project_filter(user) -> dict:
    """Filter projects by contract assignment"""
    # Admins see everything
    if has_perm(user, "projects.view.all"):
        return {}
    
    # Contractors: filter by contracts they're assigned to
    if user.role == "contractor":
        # Get all contract IDs where contractor is in resources
        db = get_db()
        contracts = await db.contracts.find(
            {"resources.user_id": user.user_id},
            {"_id": 0, "contract_id": 1}
        ).to_list(1000)
        contract_ids = [c["contract_id"] for c in contracts]
        if not contract_ids:
            return {"_id": {"$exists": False}}
        return {"contract_id": {"$in": contract_ids}}
    
    # Sales reps, agents: existing logic
    elif has_perm(user, "projects.view.own"):
        conditions = []
        if user.account_id:
            conditions.append({"account_id": user.account_id})
        # Also check if they're assigned to the contract
        db = get_db()
        contracts = await db.contracts.find(
            {"assigned_sales_rep_id": user.user_id},
            {"_id": 0, "contract_id": 1}
        ).to_list(1000)
        contract_ids = [c["contract_id"] for c in contracts]
        if contract_ids:
            conditions.append({"contract_id": {"$in": contract_ids}})
        if not conditions:
            return {"_id": {"$exists": False}}
        return {"$or": conditions}
    
    return {"_id": {"$exists": False}}


def get_frontend_url(request) -> str:
    """
    Return the frontend base URL for generating links (invites, resets, etc).
    Requires FRONTEND_URL env var to be set. No header fallbacks — those are
    attacker-controlled and could redirect reset/invite links to a malicious site.
    """
    frontend_url = os.environ.get("FRONTEND_URL", "").strip().rstrip("/")
    if frontend_url and frontend_url.startswith("http"):
        return frontend_url

    raise HTTPException(
        status_code=500,
        detail="Server configuration error: FRONTEND_URL is not set"
    )


def verify_ownership(resource: dict, user, resource_type: str = "resource") -> bool:
    """
    SECURITY FIX C-9: Verify that a user actually owns or is assigned to a resource
    before allowing edit.own operations.
    
    Args:
        resource: The resource document from database
        user: The current user
        resource_type: Type of resource (for better error messages)
    
    Returns:
        True if user owns the resource, False otherwise
    """
    if not resource:
        return False
    
    # Ownership requires a direct user-level assignment. account_id match is NOT
    # sufficient: in multi-user accounts (e.g., multiple sales reps at the same
    # company) it would let anyone in the account modify another user's records.
    ownership_fields = [
        "assigned_sales_rep_id",
        "assigned_agent_id",
        "deal_owner_id",
        "user_id",
        "created_by",
    ]

    for field in ownership_fields:
        if resource.get(field) == user.user_id:
            return True

    return False


# =====================================================================
# TENANT CONTEXT — Phase 1A multi-tenancy
# =====================================================================

class TenantContext:
    """Resolved tenant context for the current request.

    Constructed by `get_tenant_context` and injected into routes that opt into
    tenant scoping. Phase 1A: the context is derived from the authenticated
    user's `tenant_id`. Phase 1B may add request-level tenant override (e.g.
    a SimpliMeta admin browsing into a partner tenant).
    """

    __slots__ = ("tenant_id", "tenant_type", "parent_tenant_id", "is_root")

    def __init__(self, tenant_id: str, tenant_type: str,
                 parent_tenant_id: Optional[str] = None):
        self.tenant_id = tenant_id
        self.tenant_type = tenant_type
        self.parent_tenant_id = parent_tenant_id
        self.is_root = tenant_type == "root"

    def __repr__(self) -> str:
        return (
            f"TenantContext(tenant_id={self.tenant_id!r}, "
            f"tenant_type={self.tenant_type!r})"
        )


async def get_tenant_context(current_user=None) -> TenantContext:
    """FastAPI dependency: resolve the active tenant for the request.

    Usage:
        from auth_helper import get_current_user, get_tenant_context

        @router.get("/things")
        async def list_things(
            current_user=Depends(get_current_user),
            tenant=Depends(get_tenant_context),
        ):
            things = await db.things.find({"tenant_id": tenant.tenant_id}).to_list(100)
            ...

    Raises 401 if there is no authenticated user, 403 if the user is not
    assigned to a tenant, 404 if the tenant record cannot be found.
    """
    # Late-binding to avoid circular imports at module load.
    from fastapi import Depends as _Depends

    # When called directly (not via Depends), `current_user` will be None and
    # we fall through to resolving from get_current_user. When called via
    # Depends(get_tenant_context) without a `current_user` parameter the
    # caller should add `Depends(get_current_user)` explicitly. To keep this
    # ergonomic, we accept either path.
    if current_user is None:
        current_user = await get_current_user()

    tenant_id = getattr(current_user, "tenant_id", None)
    if not tenant_id:
        await log_security_event(
            event_type="tenant_context_failed", severity="warning",
            user_id=getattr(current_user, "user_id", None),
            user_email=getattr(current_user, "email", None),
            action="tenant_context_check", outcome="denied",
            details={"reason": "user_has_no_tenant_id"},
        )
        raise HTTPException(status_code=403, detail="User is not assigned to a tenant")

    db = get_db()
    tenant_doc = await db.tenants.find_one(
        {"tenant_id": tenant_id},
        {"_id": 0, "tenant_id": 1, "tenant_type": 1, "parent_tenant_id": 1, "status": 1},
    )
    if not tenant_doc:
        await log_security_event(
            event_type="tenant_context_failed", severity="warning",
            user_id=getattr(current_user, "user_id", None),
            user_email=getattr(current_user, "email", None),
            action="tenant_context_check", outcome="denied",
            details={"reason": "tenant_not_found", "tenant_id": tenant_id},
        )
        raise HTTPException(status_code=404, detail="Tenant not found")

    if tenant_doc.get("status") != "active":
        await log_security_event(
            event_type="tenant_context_failed", severity="warning",
            user_id=getattr(current_user, "user_id", None),
            user_email=getattr(current_user, "email", None),
            action="tenant_context_check", outcome="denied",
            details={
                "reason": "tenant_not_active",
                "tenant_id": tenant_id,
                "status": tenant_doc.get("status"),
            },
        )
        raise HTTPException(status_code=403, detail="Tenant is not active")

    return TenantContext(
        tenant_id=tenant_doc["tenant_id"],
        tenant_type=tenant_doc["tenant_type"],
        parent_tenant_id=tenant_doc.get("parent_tenant_id"),
    )
