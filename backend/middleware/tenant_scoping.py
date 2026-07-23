"""Phase 1E.3b — Tenant scoping middleware.

The middleware that turns `tenant_app_grants` into actual route-level
enforcement. Runs on every `/api/*` request. Three jobs:

  1. **Resolve the caller's tenant.** Stash `tenant_id` on
     `request.state` so any downstream handler can read it via the
     `get_current_tenant_id` dependency below. For non-root users
     this is always their own `tenant_id`. For root users it's
     `ten_simplimeta_root` by default, or the value of the
     `X-Override-Tenant-Id` header if they're using the support
     escape hatch (root-only).

  2. **Enforce app-level access on scoped routes.** If the request
     path matches a route prefix declared by an entry in
     `app_registry`, that request is gated by an active
     `tenant_app_grants` row for the caller's tenant. No active
     grant → 403, handler never runs. This is the piece that
     replaces ad-hoc `has_perm` gating per route with a single,
     declarative source of truth.

  3. **Audit logging for the override.** Every time a root user
     uses `X-Override-Tenant-Id`, we log who/when/what/where. The
     header has legitimate use (debugging a partner's data with
     their permission) but it's also the single most powerful
     piece of routing we expose. Audit trail is non-negotiable.

## What this middleware does NOT do

  * **Touch route handlers.** Route handlers continue to enforce
    their existing `has_perm` checks. The middleware sits in front
    of them. Mechanical refactoring of `projects_*.py` /
    `financials.py` to USE the injected `tenant_id` is 1E.3c.

  * **Seed permissions.** Tenant_admins still need their app's
    permission keys granted on their role for the existing
    `has_perm` checks to pass. That's 1E.3d's auto-seeder.

  * **Handle WebSockets.** `BaseHTTPMiddleware` only fires on HTTP
    requests. The realtime events route (`projects_events`) is
    out of scope until WebSocket scoping is built — flagged as a
    known limitation.

## Observable behaviour change after 1E.3b

  * Non-root tenants without an active grant trying to hit
    `/api/projects/*` or `/api/finance/*` get a cleaner 403 from
    the middleware (with the app's name in the message) instead
    of the generic permission-denied from inside the handler.

  * Non-root tenants WITH a grant get past the middleware but
    still 403 inside the handler (`has_perm` check fails because
    permission keys aren't seeded yet). 1E.3d fixes that. After
    1E.3d, they get all the way through and 1E.3c's refactor
    makes the query filter by their tenant_id.

  * Root tenants (you) are unchanged. You see the same data on
    `/api/projects` you've always seen. The `X-Override-Tenant-Id`
    header is new but optional.

Refs: 1E.3 design discussion 19 May 2026; 1E.3a populated the
metadata this middleware reads.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request as FastAPIRequest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from database import get_db
from seeds.seed_tenants import SIMPLIMETA_ROOT_TENANT_ID
from services.security_logger import log_security_event


# Paths the middleware always passes through without any tenant-scoping
# logic. Auth endpoints obviously can't require a tenant to authenticate;
# they're how you GET a tenant. Static / docs are not API surface.
_PASSTHROUGH_PREFIXES = (
    "/api/auth/",  # login, logout, register, password reset
)


class TenantScopingMiddleware(BaseHTTPMiddleware):
    """Enforces app-grant-based access on requests to app-owned routes.

    Runs after CORS and security headers so we don't process requests
    that would be rejected for those reasons anyway. Runs before
    handlers so the `tenant_id` lookup happens once per request and
    can be consumed by any handler via the dependency below.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Cheap early-exit for non-API and auth paths. Most requests
        # to /uploads/, /docs, etc. fall out here.
        if not path.startswith("/api/"):
            return await call_next(request)
        for prefix in _PASSTHROUGH_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Phase 2a: user/session resolution and tenant lookup move to Postgres.
        # We open a short-lived PG session for the middleware's OWN reads only
        # and close it before ``call_next`` — the route handlers own their own
        # ``get_session`` for the downstream request. ``deny`` collects an
        # optional early 4xx; if it stays None the request proceeds.
        from db.session import get_sessionmaker
        from repositories.tenants import TenantRepository

        deny: Optional[JSONResponse] = None
        sm = get_sessionmaker()
        async with sm() as pg:
            # Resolve the user without raising. If the request has no valid
            # auth, we let it through — the route handler's own
            # `Depends(get_current_user)` returns the 401 cleanly. The
            # middleware deliberately does NOT raise auth errors.
            user = await _try_resolve_user(request, pg)
            if user is not None:
                # Set the request-scoped tenant context (default: the caller's
                # own tenant_id; overridden below for root + X-Override).
                request.state.tenant_id = user.get("tenant_id")
                request.state.user_id = user.get("user_id")
                request.state.is_root_override = False

                # Is this path owned by an ingested app? (app_registry_legacy_read)
                db = get_db()
                owning_app = await _find_owning_app(db, path)
                if owning_app is not None:
                    deny = await self._enforce_scoped_app(
                        request, path, user, owning_app, db, TenantRepository(pg)
                    )

        if deny is not None:
            return deny
        return await call_next(request)

    async def _enforce_scoped_app(
        self, request, path, user, owning_app, db, tenants_repo
    ) -> Optional[JSONResponse]:
        """Enforce app-grant access for a scoped-app request.

        Returns a ``JSONResponse`` to deny, or None to proceed. Mutates
        ``request.state`` for the root-override case. ``db`` is Mongo (the
        ``tenant_app_grants`` grant check remains a legacy read until Phase 8).
        """
        user_tenant_id = user.get("tenant_id")
        is_root = user_tenant_id == SIMPLIMETA_ROOT_TENANT_ID
        override_header = request.headers.get("X-Override-Tenant-Id")

        if is_root:
            if override_header:
                target = await tenants_repo.get_tenant(override_header)
                if not target:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "detail": (
                                f"X-Override-Tenant-Id target not found: "
                                f"{override_header}"
                            )
                        },
                    )
                # Audit the override use — the single most privileged routing
                # action we offer (a root admin impersonating a tenant context).
                await log_security_event(
                    event_type="tenant_override_used",
                    severity="info",
                    user_id=user.get("user_id"),
                    user_email=user.get("email"),
                    resource="tenant",
                    resource_id=override_header,
                    action="impersonate",
                    outcome="success",
                    details={
                        "path": path,
                        "method": request.method,
                        "app_id": owning_app.get("app_id"),
                        "app_slug": owning_app.get("slug"),
                        "target_tenant_name": target.name,
                    },
                )
                request.state.tenant_id = override_header
                request.state.is_root_override = True
            # Root users always pass — root owns the catalog.
            return None

        # ── Non-root user on a scoped path ──

        # The override header is root-only. A non-root user sending it is denied.
        if override_header:
            await log_security_event(
                event_type="tenant_override_denied",
                severity="warning",
                user_id=user.get("user_id"),
                user_email=user.get("email"),
                action="impersonate_attempt",
                outcome="denied",
                details={
                    "reason": "non_root_user",
                    "path": path,
                    "attempted_target": override_header,
                },
            )
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "X-Override-Tenant-Id is only available to root admins.",
                },
            )

        if not user_tenant_id:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "User account is not associated with any tenant.",
                },
            )

        # The actual grant check (tenant_app_grants_legacy_read; Mongo; Phase 8).
        # tenant_app_grants migrates with the app-registry/ingestion domain —
        # this Mongo read is a temporary shim, deleted then. One indexed lookup
        # on (tenant_id, app_id, status); uncached so revocation is immediate.
        grant = await db.tenant_app_grants.find_one(
            {
                "tenant_id": user_tenant_id,
                "app_id": owning_app["app_id"],
                "status": "active",
            },
            {"_id": 0, "grant_id": 1},
        )
        if not grant:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        f"Your tenant does not have access to "
                        f"{owning_app.get('name', 'this app')}. "
                        "Contact your SimpliMeta administrator if you "
                        "believe this is in error."
                    ),
                    "app_id": owning_app["app_id"],
                    "app_slug": owning_app.get("slug"),
                },
            )

        # Grant exists — proceed with request.state.tenant_id already set.
        return None


# ─── Internal helpers ───────────────────────────────────────────────


async def _try_resolve_user(request: Request, pg) -> Optional[dict]:
    """Resolve the user from the request without raising on auth failure.

    Phase 2a: session + user reads come from Postgres (repositories, using the
    ``pg`` session threaded in by ``dispatch``). Returns the user as a **dict**
    (``User.model_dump()``) to preserve the caller's ``.get(...)`` contract, or
    None on any failure (no token, invalid/expired session, deactivated user).
    The middleware does not raise auth errors — those are owned by the route
    handlers' own ``Depends(get_current_user)``.
    """
    from repositories.auth import AuthRepository
    from repositories.users import UserRepository

    # Token: cookie first (browser sessions), then Bearer header (API callers).
    # Identical precedence to `get_current_user`.
    token = request.cookies.get("session_token")
    if not token:
        auth_header = request.headers.get("authorization") or request.headers.get(
            "Authorization"
        )
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        return None

    session = await AuthRepository(pg).get_session(token)
    if not session:
        return None

    expires_at = session.expires_at
    if expires_at is None:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return None

    user = await UserRepository(pg).get_user_by_id(session.user_id)
    if not user:
        return None
    if not user.is_active:
        return None

    return user.model_dump()


async def _find_owning_app(db, path: str) -> Optional[dict]:
    """Find the app (if any) whose route_prefixes claim this path.

    ``app_registry_legacy_read`` (Mongo; Phase 8): ``app_registry`` migrates with
    the ingestion domain — this read is a temporary legacy-read shim, deleted then.


    Longest-prefix-wins to handle nested-app cases cleanly — if app A
    declares `/api/foo` and app B declares `/api/foo/bar`, a request
    to `/api/foo/bar/baz` is owned by B, not A.

    We pull all active apps with non-empty `route_prefixes` and match
    in Python. The cardinality of `app_registry` is low (single-digit
    to low-double-digit apps in any realistic timeframe), and the
    individual rows are small (project to a handful of fields). At
    50 apps this is ~5KB transferred per request. Per the 1E.3
    design discussion we accept this overhead in exchange for
    revocation taking effect on the next request with no cache to
    invalidate.

    Returns the app document on match, None otherwise.
    """
    normalized = path.rstrip("/")

    apps = await db.app_registry.find(
        {
            "deleted_at": None,
            "status": "active",
            "route_prefixes": {"$exists": True, "$ne": []},
        },
        {"_id": 0, "app_id": 1, "name": 1, "slug": 1, "route_prefixes": 1},
    ).to_list(200)

    best_match: Optional[dict] = None
    best_prefix_length = -1

    for app in apps:
        for prefix in app.get("route_prefixes") or []:
            if not prefix or not prefix.startswith("/api/"):
                # Defensive: skip malformed prefixes. The backfill
                # script normalises these, but the PATCH endpoint
                # in app_ingestion.py also enforces /api/ — this is
                # belt-and-braces in case anyone edits the DB directly.
                continue
            normalized_prefix = prefix.rstrip("/")
            # A prefix matches if the request path equals it exactly
            # OR if the request path is a sub-path (starts with
            # prefix + "/"). This prevents `/api/projects` from
            # matching `/api/projects-something-else` — that bug
            # surfaces if you only do startswith without the "/" guard.
            if (
                normalized == normalized_prefix
                or normalized.startswith(normalized_prefix + "/")
            ):
                if len(normalized_prefix) > best_prefix_length:
                    best_match = app
                    best_prefix_length = len(normalized_prefix)

    return best_match


# ─── FastAPI dependency ─────────────────────────────────────────────


def get_current_tenant_id(request: FastAPIRequest) -> str:
    """Returns the effective tenant_id for the current request.

    Set by `TenantScopingMiddleware`. For most requests this is the
    caller's own tenant_id; for root admins using
    `X-Override-Tenant-Id` on a scoped route, this is the override
    target. Either way, the value is the one route handlers should
    use to filter their queries.

    Raises 500 if called outside an authenticated request — that
    means either the middleware wasn't mounted (wiring bug) or the
    route is being called by something that bypassed auth (also a
    bug). In neither case should the handler proceed.

    For routes that don't need tenant scoping (admin endpoints, etc.)
    just don't inject this dependency. The middleware still runs and
    still sets the value if the user is authenticated, but uninjected
    handlers ignore it.
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(
            status_code=500,
            detail=(
                "tenant_id not set on request.state — "
                "TenantScopingMiddleware is not running for this route. "
                "Check middleware ordering in server.py."
            ),
        )
    return tenant_id


def is_root_override(request: FastAPIRequest) -> bool:
    """True iff the current request is a root user using
    X-Override-Tenant-Id. Useful for audit-sensitive handlers that
    want to log differently when an admin is impersonating.
    """
    return bool(getattr(request.state, "is_root_override", False))


def require_root_tenant(tenant_id: str) -> None:
    """Phase 1E.3c — gate root-tenant-only routes.

    Some AEGISAXIOM apps are SimpliMeta-internal only and should never be
    exposed to partner tenants. The canonical example is the Finance
    module: it reads SimpliMeta's QBO sync and ledgers, which contain
    SimpliMeta's own books. Partners must never see this data, even
    with the override header.

    Usage pattern at the top of every handler in such an app:

        async def some_handler(
            ...,
            tenant_id: str = Depends(get_current_tenant_id),
        ):
            if not has_perm(current_user, "finance.foo.view"):
                raise HTTPException(status_code=403, detail="Permission denied")
            require_root_tenant(tenant_id)
            ...

    The gate raises 403 (not 404) because the data IS there — the
    caller just isn't allowed to see it. A 404 would suggest "no such
    feature", which is misleading when the feature exists but is
    SimpliMeta-internal.

    Note on override semantics: if a root admin uses
    X-Override-Tenant-Id to impersonate a partner tenant, the
    effective tenant_id will be the partner's, and this gate will
    fire — root cannot view Finance data while impersonating a
    partner. That's the correct behavior: impersonation should
    faithfully reflect the partner's view of the system. Root admins
    needing Finance access should drop the override header.
    """
    if tenant_id != SIMPLIMETA_ROOT_TENANT_ID:
        raise HTTPException(
            status_code=403,
            detail=(
                "This module is SimpliMeta-internal and not available "
                "for partner tenants."
            ),
        )
