"""
AI runtime authorization middleware.

Enforces that AI employees can only invoke endpoints whose permission keys
appear in the union of their `app_access[*].permissions` lists (plus any
directly-granted system permissions on the underlying user record).

Human callers are short-circuited: the dependency is a no-op for them so
that wrapping a route in `Depends(require_ai_permission(...))` does not
break human flows. Humans continue to be authorized by the existing
`has_perm` / `require_perm` helpers in `auth_helper.py`.

Wave 1 / Thread 1A. Companion threads:
  1B – permission_registry (permission discovery)
  1C – rbac_generator     (writes app_access at ingest time)
  1D – ai_employee_defaults / ai_employees route (writes user_id link
       and seeds app_access on creation)
  1E – permission_implications (expansion of permission keys)

This thread does NOT apply the dependency to any existing route — that
work belongs to Wave 2 (Thread 2A).
"""
from __future__ import annotations

from typing import Optional, Set

from fastapi import Depends, HTTPException, Request

from auth_helper import get_current_user
from database import get_db
from services.security_logger import log_security_event


_CACHE_KEY = "ai_perms_cache"


async def get_flattened_ai_permissions(
    employee_id: str,
    request: Optional[Request] = None,
) -> Set[str]:
    """Return the union of permissions granted to an AI employee.

    Sources merged:
      * `app_access[*].permissions` — every permission across every linked
        app on the employee's record.
      * `extra_permissions` on the underlying user record (if present) —
        directly-granted system perms (e.g. an explicit grant outside
        any app bundle).

    Result is cached on `request.state.ai_perms_cache[employee_id]` so
    multiple permission checks within a single request are O(1) after
    the first lookup. The cache is per-request only; AI access changes
    take effect on the next inbound request.
    """
    if request is not None:
        cache = getattr(request.state, _CACHE_KEY, None)
        if cache is None:
            cache = {}
            request.state.ai_perms_cache = cache
        if employee_id in cache:
            return cache[employee_id]

    db = get_db()
    employee = await db.ai_employees.find_one(
        {"employee_id": employee_id},
        {"_id": 0, "app_access": 1, "user_id": 1},
    )

    flattened: Set[str] = set()
    if employee:
        for entry in employee.get("app_access") or []:
            perms = entry.get("permissions") or []
            flattened.update(p for p in perms if isinstance(p, str))

        # Pull any directly-granted system permissions from the AI's
        # underlying user record. AI employees authenticate as a user;
        # admins may grant ad-hoc perms there outside the app_access bundle.
        linked_user_id = employee.get("user_id")
        if linked_user_id:
            # extra_permissions live on the Postgres users row.
            from db.session import get_sessionmaker
            from repositories.users import UserRepository
            _sm = get_sessionmaker()
            async with _sm() as _pg:
                _u = await UserRepository(_pg).get_user_by_id(linked_user_id)
            if _u:
                for p in _u.extra_permissions or []:
                    if isinstance(p, str):
                        flattened.add(p)

    if request is not None:
        request.state.ai_perms_cache[employee_id] = flattened

    return flattened


def require_ai_permission(permission_key: str):
    """FastAPI dependency factory enforcing an AI permission check.

    Behavior:
      * Human callers (no `is_ai_employee` flag): no-op. Humans are
        gated by existing `has_perm` / `require_perm` checks elsewhere.
      * AI callers: the requested `permission_key` must be in their
        flattened permission set. On miss, an `authz_failed` security
        event is written to `audit_logs` (event_category="security"),
        then a 403 is raised. Wildcard `*` in the AI's set grants any
        permission (parity with human RBAC).

    Usage:
        from middleware.ai_authz import require_ai_permission

        @router.post(
            "/widgets",
            dependencies=[Depends(require_ai_permission("widgets.create"))],
        )
        async def create_widget(...):
            ...
    """

    async def _dependency(
        request: Request,
        current_user=Depends(get_current_user),
    ):
        if not getattr(current_user, "is_ai_employee", False):
            return current_user

        employee_id = getattr(current_user, "ai_employee_id", None)
        if not employee_id:
            # Defensive: the user is flagged AI but the link is missing.
            # Treat as denied and audit so it surfaces.
            await _audit_denied(
                employee_id="<unknown>",
                user_id=getattr(current_user, "user_id", None),
                user_email=getattr(current_user, "email", None),
                permission_key=permission_key,
                summary=[],
                reason="missing_ai_employee_id",
            )
            raise HTTPException(
                status_code=403,
                detail="AI employee lacks required permission",
            )

        granted = await get_flattened_ai_permissions(employee_id, request=request)

        if "*" in granted or permission_key in granted:
            return current_user

        # Build a compact summary for the audit detail. Avoid dumping the
        # full permission set — it can be large and is not needed forensically.
        db = get_db()
        employee = await db.ai_employees.find_one(
            {"employee_id": employee_id},
            {"_id": 0, "app_access": 1},
        )
        summary = [
            {
                "app_id": entry.get("app_id"),
                "app_slug": entry.get("app_slug"),
                "permission_count": len(entry.get("permissions") or []),
            }
            for entry in (employee or {}).get("app_access") or []
        ]

        await _audit_denied(
            employee_id=employee_id,
            user_id=getattr(current_user, "user_id", None),
            user_email=getattr(current_user, "email", None),
            permission_key=permission_key,
            summary=summary,
            reason="not_in_app_access",
        )
        raise HTTPException(
            status_code=403,
            detail="AI employee lacks required permission",
        )

    return _dependency


async def _audit_denied(
    *,
    employee_id: str,
    user_id: Optional[str],
    user_email: Optional[str],
    permission_key: str,
    summary: list,
    reason: str,
) -> None:
    """Write an authz_failed security event for an AI permission denial."""
    await log_security_event(
        event_type="authz_failed",
        severity="warning",
        user_id=user_id,
        user_email=user_email,
        resource="ai_employee",
        resource_id=employee_id,
        action="ai_permission_check",
        outcome="denied",
        details={
            "actor_id": employee_id,
            "attempted_permission": permission_key,
            "app_access_summary": summary,
            "reason": reason,
        },
    )
