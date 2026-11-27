from fastapi import APIRouter, HTTPException, Response, Depends, Request
from datetime import datetime, timezone, timedelta
import asyncio
import logging
import uuid
import httpx
import os
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from db.session import get_session
from models import User, UserCreate, UserLogin, PasswordReset, ForgotPasswordRequest, ResetPasswordRequest, \
    GoogleCodeRequest, GoogleInviteCodeRequest, AdminSetupRequest
from services.audit_service import log_action
from services.security_logger import log_security_event
from auth_helper import (
    hash_password,
    verify_password,
    create_session_token,
    get_current_user,
    create_invite_token,
    verify_invite_token,
    get_frontend_url,
)
from repositories.users import UserRepository
from repositories.auth import AuthRepository
from seeds.seed_tenants import SIMPLIMETA_ROOT_TENANT_ID
from rate_limiter import limiter

router = APIRouter()


async def _build_user_with_permissions(user: User, pg: AsyncSession) -> User:
    """Return a copy of ``user`` with role permissions + closure filled in.

    Used by ALL auth endpoints that return user data so login / register /
    invite-accept / profile-update responses carry the same closure-aware
    permission list ``/api/auth/me`` returns. Backend route guards never trust
    ``effective_permissions`` from the wire — they recompute via has_perm().

    Phase 2b: role permissions come from the roles table via
    ``RolesRepository`` — the same source ``auth_helper.get_current_user``
    reads. The hardcoded role->permissions convention dict that
    used to live here (a second, drift-prone source) is deleted.
    """
    from auth_helper import get_cached_implication_graph
    from repositories.roles import RolesRepository
    from services.permission_implications import resolve_implications

    user_dict = user.model_dump()

    # Hydrate permissions from the user's role + explicit user permissions
    role_perms = await RolesRepository(pg).get_role_permissions(user.role or "customer")
    explicit_perms = user_dict.get("permissions") or []
    base_permissions = set(explicit_perms) | set(role_perms)

    user_dict["permissions"] = list(base_permissions)

    # Resolve effective_permissions for the wire payload. The closure input must
    # union role permissions with per-user extra_permissions, matching
    # auth_helper.get_current_user. Convention-only fallback if the registry
    # graph fetch fails so login can never be blocked by a graph hiccup.
    try:
        registry_graph = await get_cached_implication_graph()
    except Exception:
        logger.exception(
            "Failed to load implication graph during _build_user_with_permissions; "
            "falling back to convention-only closure"
        )
        registry_graph = {}

    combined = base_permissions | set(user_dict.get("extra_permissions") or [])

    if "*" in combined:
        closure = {"*"} | set(resolve_implications(combined, registry_graph=registry_graph))
    else:
        closure = resolve_implications(combined, registry_graph=registry_graph)

    user_dict["effective_permissions"] = sorted(closure)

    return User(**user_dict)


SESSION_EXPIRY_DAYS = 7


async def _create_session(auth_repo: AuthRepository, user_id: str, response: Response) -> str:
    """Create a Postgres-backed session row and set the session cookie."""
    token = create_session_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_EXPIRY_DAYS)
    await auth_repo.create_session(user_id, token, expires_at)
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=SESSION_EXPIRY_DAYS * 86400,
        path="/",
    )
    return token


@router.post("/register")
@limiter.limit("5/minute")
async def register(
        data: UserCreate,
        request: Request,
        response: Response,
        pg: AsyncSession = Depends(get_session),
):
    users = UserRepository(pg)

    # SECURITY FIX C-2: Only allow 'customer' role for self-registration
    ALLOWED_REGISTRATION_ROLES = ["customer"]
    if data.role not in ALLOWED_REGISTRATION_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role. Only {', '.join(ALLOWED_REGISTRATION_ROLES)} registration is allowed."
        )

    # SECURITY FIX M-3: Enforce password complexity requirements
    password = data.password
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long")
    if not any(c.isupper() for c in password):
        raise HTTPException(status_code=400, detail="Password must contain at least one uppercase letter")
    if not any(c.islower() for c in password):
        raise HTTPException(status_code=400, detail="Password must contain at least one lowercase letter")
    if not any(c.isdigit() for c in password):
        raise HTTPException(status_code=400, detail="Password must contain at least one number")

    existing = await users.get_user_by_email(data.email.lower())
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user_id = f"user_{uuid.uuid4().hex[:12]}"
    created = await users.create_user({
        "user_id": user_id,
        "email": data.email.lower(),
        "name": data.name,
        "role": data.role,
        # Phase 1A multi-tenancy: public self-registration lands in the
        # SimpliMeta root tenant.
        "tenant_id": SIMPLIMETA_ROOT_TENANT_ID,
        "account_id": None,
        "picture": None,
        "is_active": True,
        "is_email_verified": False,
        "password_hash": hash_password(data.password),
        "zoho_contact_id": None,
        "created_at": datetime.now(timezone.utc),
    })
    token = await _create_session(AuthRepository(pg), user_id, response)
    user = await _build_user_with_permissions(created, pg)
    return {"user": user, "token": token}


@router.post("/login")
@limiter.limit("10/minute")
async def login(
        data: UserLogin,
        request: Request,
        response: Response,
        pg: AsyncSession = Depends(get_session),
):
    users = UserRepository(pg)
    record = await users.get_auth_record_by_email(data.email.lower())
    if record is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    user_dto, password_hash = record
    if not user_dto.is_active:
        await log_security_event(
            event_type="auth_failed", severity="warning",
            user_id=user_dto.user_id, user_email=user_dto.email,
            action="login", outcome="denied",
            details={"reason": "account_deactivated", "email": data.email},
        )
        raise HTTPException(status_code=403, detail="Account is deactivated")
    if not password_hash:
        raise HTTPException(status_code=401, detail="This account uses Google Sign-In")
    if not verify_password(data.password, password_hash):
        await log_security_event(
            event_type="auth_failed", severity="warning",
            user_id=user_dto.user_id, user_email=user_dto.email,
            action="login", outcome="denied",
            details={"reason": "invalid_password", "email": data.email},
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = await _create_session(AuthRepository(pg), user_dto.user_id, response)
    await log_action("login", user_dto.user_id, user_dto.email, "auth")

    user = await _build_user_with_permissions(user_dto, pg)
    return {"user": user, "token": token}


@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/logout")
async def logout(
        response: Response,
        current_user: User = Depends(get_current_user),
        pg: AsyncSession = Depends(get_session),
):
    # SECURITY FIX C-10: Delete all sessions for this user (prevents token reuse)
    await AuthRepository(pg).delete_sessions_for_user(current_user.user_id)

    # Securely delete the cookie with all security attributes
    response.delete_cookie(
        "session_token",
        path="/",
        secure=True,
        httponly=True,
        samesite="lax"
    )

    return {"message": "Logged out"}


async def _exchange_google_code(code: str) -> dict:
    """Exchange a Google OAuth authorization code for user info and tokens."""
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI")

    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(status_code=500, detail="Google OAuth is not configured on this server")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=10.0,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Google auth failed: token exchange error")
        token_data = resp.json()

    id_token_str = token_data.get("id_token")
    if not id_token_str:
        raise HTTPException(status_code=401, detail="Google auth failed: no id_token returned")

    try:
        id_info = await asyncio.to_thread(
            google_id_token.verify_oauth2_token,
            id_token_str, google_requests.Request(), client_id
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Google auth failed: invalid token")

    if not id_info.get("email_verified"):
        raise HTTPException(status_code=401, detail="Google auth failed: email not verified")

    return {
        "email": id_info.get("email", "").lower(),
        "name": id_info.get("name", ""),
        "picture": id_info.get("picture", ""),
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "expires_in": token_data.get("expires_in", 3600),
        "scope": token_data.get("scope", ""),
    }


async def _store_google_tokens(auth_repo: AuthRepository, user_id: str, auth_data: dict):
    """Store or update the user's Google OAuth tokens for Docs/Drive access."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=auth_data.get("expires_in", 3600))
    # upsert_oauth_token overwrites refresh_token only when a new one is given
    # (Google sends it only on first consent) — pass None to preserve existing.
    await auth_repo.upsert_oauth_token(
        token_id=f"google_{user_id}",
        provider="google",
        user_id=user_id,
        access_token=auth_data["access_token"],
        refresh_token=auth_data.get("refresh_token"),
        expires_at=expires_at,
        scope=auth_data.get("scope", ""),
    )


@router.post("/google/session")
@limiter.limit("20/minute")
async def google_session(
        data: GoogleCodeRequest,
        request: Request,
        response: Response,
        pg: AsyncSession = Depends(get_session),
):
    try:
        auth_data = await _exchange_google_code(data.code)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Google auth failed")

    email = auth_data["email"]
    name = auth_data["name"]
    picture = auth_data["picture"]

    users = UserRepository(pg)
    user_dto = await users.get_user_by_email(email)

    if not user_dto:
        # Auto-create account if it doesn't exist
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        user_dto = await users.create_user({
            "user_id": user_id,
            "email": email,
            "name": name or email.split("@")[0],
            "role": "customer",
            "tenant_id": SIMPLIMETA_ROOT_TENANT_ID,
            "account_id": None,
            "picture": picture,
            "is_active": True,
            "is_email_verified": True,
            "password_hash": None,
            "zoho_contact_id": None,
            "created_at": datetime.now(timezone.utc),
        })
    else:
        if not user_dto.is_active:
            raise HTTPException(status_code=403, detail="Account is deactivated")
        user_dto = await users.update_user(user_dto.user_id, {"picture": picture, "name": name})

    # Store Google OAuth tokens for Docs/Drive access
    if auth_data.get("access_token"):
        await _store_google_tokens(AuthRepository(pg), user_dto.user_id, auth_data)

    token = await _create_session(AuthRepository(pg), user_dto.user_id, response)
    await log_action("login_google", user_dto.user_id, user_dto.email, "auth")
    user = await _build_user_with_permissions(user_dto, pg)
    return {"user": user, "token": token}


@router.post("/accept-invite-google")
@limiter.limit("10/minute")
async def accept_invite_google(
        data: GoogleInviteCodeRequest,
        request: Request,
        response: Response,
        pg: AsyncSession = Depends(get_session),
):
    """Accept an invitation using Google SSO instead of setting a password."""
    payload = verify_invite_token(data.invite_token)

    # Get Google user info
    try:
        auth_data = await _exchange_google_code(data.code)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Google auth failed")

    google_email = auth_data["email"]
    google_name = auth_data["name"]
    google_picture = auth_data["picture"]

    users = UserRepository(pg)
    auth_repo = AuthRepository(pg)

    # Verify the invited user exists
    user_dto = await users.get_user_by_id(payload["sub"])
    if not user_dto:
        raise HTTPException(status_code=404, detail="User not found")

    # SECURITY: Google email must match the invited email
    if google_email != user_dto.email.lower():
        await log_security_event(
            event_type="auth_failed", severity="critical",
            user_id=user_dto.user_id, user_email=user_dto.email,
            action="accept_invite_google", outcome="denied",
            details={
                "reason": "oauth_email_mismatch",
                "invited_email": user_dto.email,
                "google_email": google_email,
            },
        )
        raise HTTPException(
            status_code=403,
            detail="The Google account used does not match the invitation email. Please sign in with the correct Google account."
        )

    # Activate the account via SSO (no password needed); clear the invite token.
    updated = await users.update_user(
        payload["sub"],
        {
            "is_active": True,
            "is_email_verified": True,
            "name": google_name or user_dto.name or "",
            "picture": google_picture,
            "invite_token": None,
        },
    )

    # Store Google OAuth tokens for Docs/Drive access
    if auth_data.get("access_token"):
        await _store_google_tokens(auth_repo, payload["sub"], auth_data)

    # Wipe any pre-existing sessions
    await auth_repo.delete_sessions_for_user(payload["sub"])
    token = await _create_session(auth_repo, payload["sub"], response)
    await log_action("invite_accepted_google", payload["sub"], updated.email, "auth")

    user = await _build_user_with_permissions(updated, pg)
    return {"user": user, "token": token}


@router.post("/accept-invite")
@limiter.limit("5/minute")
async def accept_invite(
        data: PasswordReset,
        request: Request,
        response: Response,
        pg: AsyncSession = Depends(get_session),
):
    payload = verify_invite_token(data.token)
    users = UserRepository(pg)
    auth_repo = AuthRepository(pg)
    user_dto = await users.get_user_by_id(payload["sub"])
    if not user_dto:
        raise HTTPException(status_code=404, detail="User not found")

    # SECURITY FIX M-3: Enforce password complexity requirements
    password = data.new_password
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long")
    if not any(c.isupper() for c in password):
        raise HTTPException(status_code=400, detail="Password must contain at least one uppercase letter")
    if not any(c.islower() for c in password):
        raise HTTPException(status_code=400, detail="Password must contain at least one lowercase letter")
    if not any(c.isdigit() for c in password):
        raise HTTPException(status_code=400, detail="Password must contain at least one number")

    # SECURITY FIX H-8: Remove invite token after successful acceptance
    updated = await users.update_user(
        payload["sub"],
        {
            "password_hash": hash_password(data.new_password),
            "is_active": True,
            "is_email_verified": True,
            "invite_token": None,
        },
    )
    # FIX CRITICAL-3: Wipe any pre-existing sessions when accepting an invite
    await auth_repo.delete_sessions_for_user(payload["sub"])
    token = await _create_session(auth_repo, payload["sub"], response)
    user = await _build_user_with_permissions(updated, pg)
    return {"user": user, "token": token}


@router.post("/validate-invite")
@limiter.limit("5/minute")
async def validate_invite(
        data: PasswordReset,
        request: Request,
        pg: AsyncSession = Depends(get_session),
):
    payload = verify_invite_token(data.token)
    user_dto = await UserRepository(pg).get_user_by_id(payload["sub"])
    if not user_dto:
        raise HTTPException(status_code=404, detail="Invite not found")
    email = user_dto.email
    masked = email[0] + "***@" + email.split("@")[-1] if "@" in email else "***"
    return {"valid": True, "email": masked, "name": user_dto.name, "role": user_dto.role}


@router.post("/forgot-password")
@limiter.limit("5/minute")
async def forgot_password(
        data: ForgotPasswordRequest,
        request: Request,
        pg: AsyncSession = Depends(get_session),
):
    users = UserRepository(pg)
    email = data.email.lower()
    user_dto = await users.get_user_by_email(email)
    password_hash = await users.get_password_hash_by_email(email)
    # Always return 200 to avoid user enumeration
    if not user_dto or not password_hash:
        return {"message": "If that email is registered, a reset link has been sent."}

    reset_token = str(uuid.uuid4().hex)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    await AuthRepository(pg).upsert_reset_token(email, reset_token, expires_at)

    frontend_url = get_frontend_url(request)
    reset_link = f"{frontend_url}/reset-password?token={reset_token}"
    try:
        from services.email_service import send_password_reset_email
        await send_password_reset_email(email, user_dto.name or "User", reset_link)
    except Exception as e:
        logging.getLogger(__name__).warning(f"Password reset email failed (non-blocking): {e}")

    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password")
@limiter.limit("10/minute")
async def reset_password(
        data: ResetPasswordRequest,
        request: Request,
        response: Response,
        pg: AsyncSession = Depends(get_session),
):
    users = UserRepository(pg)
    auth_repo = AuthRepository(pg)

    record = await auth_repo.get_reset_token(data.token)
    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link.")
    if record.used:
        raise HTTPException(status_code=400, detail="This reset link has already been used.")
    expires_at = record.expires_at
    if expires_at:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(status_code=400, detail="This reset link has expired. Please request a new one.")

    # SECURITY FIX M-3: Enforce password complexity requirements
    password = data.new_password
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long")
    if not any(c.isupper() for c in password):
        raise HTTPException(status_code=400, detail="Password must contain at least one uppercase letter")
    if not any(c.islower() for c in password):
        raise HTTPException(status_code=400, detail="Password must contain at least one lowercase letter")
    if not any(c.isdigit() for c in password):
        raise HTTPException(status_code=400, detail="Password must contain at least one number")

    # SECURITY FIX H-7: single-use CAS — mark used iff currently unused. A lost
    # race (concurrent consumer already used it) returns None → treated as used.
    consumed = await auth_repo.consume_reset_token(data.token)
    if consumed is None:
        raise HTTPException(status_code=400, detail="This reset link has already been used.")

    email = consumed.email
    user_dto = await users.get_user_by_email(email)
    if not user_dto:
        raise HTTPException(status_code=404, detail="User not found.")

    # Now update password (token already marked as used)
    await users.set_password_by_email(email, hash_password(data.new_password))

    # FIX CRITICAL-3: Invalidate ALL existing sessions after password reset.
    await auth_repo.delete_sessions_for_user(user_dto.user_id)

    token = await _create_session(auth_repo, user_dto.user_id, response)
    await log_action("password_reset", user_dto.user_id, email, "auth")
    await log_security_event(
        event_type="password_reset", severity="warning",
        user_id=user_dto.user_id, user_email=email,
        resource="user", resource_id=user_dto.user_id,
        action="password_reset", outcome="success",
    )
    user = await _build_user_with_permissions(user_dto, pg)
    return {"user": user, "token": token, "message": "Password reset successful."}


@router.post("/setup-admin")
@limiter.limit("5/hour")
async def setup_admin(
        data: AdminSetupRequest,
        request: Request,
        pg: AsyncSession = Depends(get_session),
):
    """Create first admin - only works when no admin exists"""
    users = UserRepository(pg)
    if await users.admin_exists():
        raise HTTPException(status_code=400, detail="Admin already exists")

    password = data.password
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long")
    if not any(c.isupper() for c in password):
        raise HTTPException(status_code=400, detail="Password must contain at least one uppercase letter")
    if not any(c.islower() for c in password):
        raise HTTPException(status_code=400, detail="Password must contain at least one lowercase letter")
    if not any(c.isdigit() for c in password):
        raise HTTPException(status_code=400, detail="Password must contain at least one number")

    user_id = f"user_{uuid.uuid4().hex[:12]}"
    await users.create_user({
        "user_id": user_id,
        "email": data.email.lower(),
        "name": data.name,
        "role": "admin",
        # Phase 1A multi-tenancy: first-admin bootstrap creates the admin in
        # the SimpliMeta root tenant by definition.
        "tenant_id": SIMPLIMETA_ROOT_TENANT_ID,
        "account_id": None,
        "picture": None,
        "is_active": True,
        "is_email_verified": True,
        "password_hash": hash_password(password),
        "zoho_contact_id": None,
        "created_at": datetime.now(timezone.utc),
    })
    return {"message": "Admin created successfully", "user_id": user_id}


@router.post("/request-password-change")
@limiter.limit("3/hour")
async def request_password_change(
        request: Request,
        current_user: User = Depends(get_current_user),
        pg: AsyncSession = Depends(get_session),
):
    """
    Authenticated user requests a password set/change email.
    Works for both SSO users (who never set a password) and password users.
    """
    reset_token = str(uuid.uuid4().hex)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    await AuthRepository(pg).upsert_reset_token(current_user.email.lower(), reset_token, expires_at)

    frontend_url = get_frontend_url(request)
    reset_link = f"{frontend_url}/reset-password?token={reset_token}"
    try:
        from services.email_service import send_password_change_email
        await send_password_change_email(current_user.email.lower(), current_user.name, reset_link)
    except Exception as e:
        logging.getLogger(__name__).warning(f"Password change email failed (non-blocking): {e}")

    await log_action("password_change_requested", current_user.user_id, current_user.email, "auth")
    return {"message": "A password change link has been sent to your email."}