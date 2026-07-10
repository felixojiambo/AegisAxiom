from fastapi import APIRouter, HTTPException, Response, Depends, Request
from datetime import datetime, timezone, timedelta
import asyncio
import logging
import uuid
import httpx
import os
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

logger = logging.getLogger(__name__)

from database import get_db
from models import User, UserCreate, UserLogin, PasswordReset, ForgotPasswordRequest, ResetPasswordRequest, GoogleCodeRequest, GoogleInviteCodeRequest, AdminSetupRequest
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
from seeds.seed_tenants import SIMPLIMETA_ROOT_TENANT_ID
from rate_limiter import limiter

router = APIRouter()


async def _build_user_with_permissions(doc: dict) -> User:
    """Build a User object with permissions loaded from the role.
    This must be used by ALL auth endpoints that return user data.

    Also resolves and serializes the `effective_permissions` closure
    so login/register/profile-update responses carry the closure-aware
    permission list the frontend uses for UI gating. Backend route
    guards never trust this field — they recompute via has_perm() per
    request.
    """
    # Deferred imports to avoid circular dependency at module load:
    # auth_helper imports from this module's siblings via FastAPI dep
    # wiring, and routes/__init__.py is imported by auth_helper indirectly
    # through server.py — pulling these symbols at function-call time
    # keeps the import graph acyclic.
    from auth_helper import get_cached_implication_graph
    from services.permission_implications import resolve_implications

    db = get_db()
    user_dict = {k: v for k, v in doc.items() if k not in ["password_hash", "invite_token"]}
    role_name = doc.get("role")
    if role_name:
        role_doc = await db.roles.find_one({"name": role_name}, {"_id": 0})
        if role_doc:
            user_dict["permissions"] = role_doc.get("permissions", [])
        else:
            user_dict["permissions"] = user_dict.get("permissions") or []
    else:
        user_dict["permissions"] = user_dict.get("permissions") or []

    # Resolve effective_permissions for the wire payload. The closure
    # input must union role permissions with per-user extra_permissions,
    # matching auth_helper.get_current_user — otherwise /login and
    # /api/auth/me return divergent effective_permissions for users with
    # extras. Convention-only fallback if the registry graph fetch fails
    # so login can never be blocked by an implication-graph hiccup.
    try:
        registry_graph = await get_cached_implication_graph()
    except Exception:
        logger.exception(
            "Failed to load implication graph during _build_user_with_permissions; "
            "falling back to convention-only closure"
        )
        registry_graph = {}
    combined = set(user_dict.get("permissions") or []) | set(
        user_dict.get("extra_permissions") or []
    )
    closure = resolve_implications(combined, registry_graph=registry_graph)
    user_dict["effective_permissions"] = sorted(closure)

    return User(**user_dict)
SESSION_EXPIRY_DAYS = 7


async def _create_session(db, user_id: str, response: Response) -> str:
    token = create_session_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_EXPIRY_DAYS)
    await db.user_sessions.insert_one(
        {
            "session_token": token,
            "user_id": user_id,
            "expires_at": expires_at,
            "created_at": datetime.now(timezone.utc),
        }
    )
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
async def register(data: UserCreate, request: Request, response: Response):
    db = get_db()
    
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
    
    existing = await db.users.find_one({"email": data.email.lower()}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user_id = f"user_{uuid.uuid4().hex[:12]}"
    doc = {
        "user_id": user_id,
        "email": data.email.lower(),
        "name": data.name,
        "role": data.role,
        # Phase 1A multi-tenancy: public self-registration lands in the
        # SimpliMeta root tenant. Phase 1B introduces invite-based tenant
        # attribution so partner/account tenants can self-register users.
        "tenant_id": SIMPLIMETA_ROOT_TENANT_ID,
        "account_id": None,
        "picture": None,
        "is_active": True,
        "is_email_verified": False,
        "password_hash": hash_password(data.password),
        "zoho_contact_id": None,
        "created_at": datetime.now(timezone.utc),
    }
    await db.users.insert_one(doc)
    token = await _create_session(db, user_id, response)
    user = await _build_user_with_permissions(doc)
    return {"user": user, "token": token}


@router.post("/login")
@limiter.limit("10/minute")
async def login(data: UserLogin, request: Request, response: Response):
    db = get_db()
    doc = await db.users.find_one({"email": data.email.lower()}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not doc.get("is_active", True):
        await log_security_event(
            event_type="auth_failed", severity="warning",
            user_id=doc.get("user_id"), user_email=doc.get("email"),
            action="login", outcome="denied",
            details={"reason": "account_deactivated", "email": data.email},
        )
        raise HTTPException(status_code=403, detail="Account is deactivated")
    ph = doc.get("password_hash")
    if not ph:
        raise HTTPException(status_code=401, detail="This account uses Google Sign-In")
    if not verify_password(data.password, ph):
        await log_security_event(
            event_type="auth_failed", severity="warning",
            user_id=doc.get("user_id"), user_email=doc.get("email"),
            action="login", outcome="denied",
            details={"reason": "invalid_password", "email": data.email},
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = await _create_session(db, doc["user_id"], response)
    await log_action("login", doc["user_id"], doc["email"], "auth")
    
    user = await _build_user_with_permissions(doc)
    return {"user": user, "token": token}


@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/logout")
async def logout(response: Response, current_user: User = Depends(get_current_user)):
    # SECURITY FIX C-10: Improved session security
    # Delete all sessions for this user (prevents token reuse)
    db = get_db()
    await db.user_sessions.delete_many({"user_id": current_user.user_id})
    
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


async def _store_google_tokens(db, user_id: str, auth_data: dict):
    """Store or update user's Google OAuth tokens for Docs/Drive access."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=auth_data.get("expires_in", 3600))

    set_fields = {
        "provider": "google",
        "user_id": user_id,
        "access_token": auth_data["access_token"],
        "expires_at": expires_at,
        "scope": auth_data.get("scope", ""),
        "updated_at": now,
    }
    # Only overwrite refresh_token if a new one was provided
    # (Google only sends refresh_token on first consent or re-consent)
    if auth_data.get("refresh_token"):
        set_fields["refresh_token"] = auth_data["refresh_token"]

    await db.oauth_tokens.update_one(
        {"provider": "google", "user_id": user_id},
        {
            "$set": set_fields,
            "$setOnInsert": {"token_id": f"google_{user_id}", "created_at": now},
        },
        upsert=True,
    )


@router.post("/google/session")
@limiter.limit("20/minute")
async def google_session(data: GoogleCodeRequest, request: Request, response: Response):
    try:
        auth_data = await _exchange_google_code(data.code)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Google auth failed")

    email = auth_data["email"]
    name = auth_data["name"]
    picture = auth_data["picture"]

    db = get_db()
    doc = await db.users.find_one({"email": email}, {"_id": 0})
    if not doc:
        # SECURITY: Do not auto-create accounts. Only pre-invited users can sign in.
        raise HTTPException(
            status_code=403,
            detail="No account found for this email. Please contact your administrator to receive an invitation."
        )

    if not doc.get("is_active", True):
        raise HTTPException(status_code=403, detail="Account is deactivated")

    await db.users.update_one({"email": email}, {"$set": {"picture": picture, "name": name}})
    doc["picture"] = picture
    doc["name"] = name

    # Store Google OAuth tokens for Docs/Drive access
    if auth_data.get("access_token"):
        await _store_google_tokens(db, doc["user_id"], auth_data)

    token = await _create_session(db, doc["user_id"], response)
    await log_action("login_google", doc["user_id"], doc["email"], "auth")
    user = await _build_user_with_permissions(doc)
    return {"user": user, "token": token}


@router.post("/accept-invite-google")
@limiter.limit("10/minute")
async def accept_invite_google(data: GoogleInviteCodeRequest, request: Request, response: Response):
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

    # Verify the invited user exists
    db = get_db()
    doc = await db.users.find_one({"user_id": payload["sub"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="User not found")

    # SECURITY: Google email must match the invited email
    if google_email != doc["email"].lower():
        await log_security_event(
            event_type="auth_failed", severity="critical",
            user_id=doc.get("user_id"), user_email=doc.get("email"),
            action="accept_invite_google", outcome="denied",
            details={
                "reason": "oauth_email_mismatch",
                "invited_email": doc["email"],
                "google_email": google_email,
            },
        )
        raise HTTPException(
            status_code=403,
            detail="The Google account used does not match the invitation email. Please sign in with the correct Google account."
        )

    # Activate the account via SSO (no password needed)
    await db.users.update_one(
        {"user_id": payload["sub"]},
        {
            "$set": {
                "is_active": True,
                "is_email_verified": True,
                "name": google_name or doc.get("name", ""),
                "picture": google_picture,
            },
            "$unset": {"invite_token": ""}
        },
    )

    # Store Google OAuth tokens for Docs/Drive access
    if auth_data.get("access_token"):
        await _store_google_tokens(db, payload["sub"], auth_data)

    # Wipe any pre-existing sessions
    await db.user_sessions.delete_many({"user_id": payload["sub"]})
    token = await _create_session(db, payload["sub"], response)
    await log_action("invite_accepted_google", payload["sub"], doc["email"], "auth")

    updated_doc = await db.users.find_one({"user_id": payload["sub"]}, {"_id": 0})
    user = await _build_user_with_permissions(updated_doc)
    return {"user": user, "token": token}


@router.post("/accept-invite")
@limiter.limit("5/minute")
async def accept_invite(data: PasswordReset, request: Request, response: Response):
    payload = verify_invite_token(data.token)
    db = get_db()
    doc = await db.users.find_one({"user_id": payload["sub"]}, {"_id": 0})
    if not doc:
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
    await db.users.update_one(
        {"user_id": payload["sub"]},
        {
            "$set": {
                "password_hash": hash_password(data.new_password),
                "is_active": True,
                "is_email_verified": True
            },
            "$unset": {"invite_token": ""}  # Remove the invite token from database
        },
    )
    # FIX CRITICAL-3: Wipe any pre-existing sessions when accepting an invite
    await db.user_sessions.delete_many({"user_id": payload["sub"]})
    token = await _create_session(db, payload["sub"], response)
    updated_doc = await db.users.find_one({"user_id": payload["sub"]}, {"_id": 0})
    user = await _build_user_with_permissions(updated_doc)
    return {"user": user, "token": token}


@router.post("/validate-invite")
@limiter.limit("5/minute")
async def validate_invite(data: PasswordReset, request: Request):
    payload = verify_invite_token(data.token)
    db = get_db()
    doc = await db.users.find_one({"user_id": payload["sub"]}, {"_id": 0, "email": 1, "name": 1, "role": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Invite not found")
    email = doc["email"]
    masked = email[0] + "***@" + email.split("@")[-1] if "@" in email else "***"
    return {"valid": True, "email": masked, "name": doc["name"], "role": doc.get("role")}


@router.post("/forgot-password")
@limiter.limit("5/minute")
async def forgot_password(data: ForgotPasswordRequest, request: Request):
    db = get_db()
    doc = await db.users.find_one({"email": data.email.lower()}, {"_id": 0})
    # Always return 200 to avoid user enumeration
    if not doc or not doc.get("password_hash"):
        return {"message": "If that email is registered, a reset link has been sent."}

    reset_token = str(uuid.uuid4().hex)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    await db.password_reset_tokens.update_one(
        {"email": data.email.lower()},
        {"$set": {"token": reset_token, "expires_at": expires_at, "used": False, "created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )

    frontend_url = get_frontend_url(request)
    reset_link = f"{frontend_url}/reset-password?token={reset_token}"
    try:
        from services.email_service import send_password_reset_email
        await send_password_reset_email(data.email.lower(), doc.get("name", "User"), reset_link)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Password reset email failed (non-blocking): {e}")

    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password")
@limiter.limit("10/minute")
async def reset_password(data: ResetPasswordRequest, request: Request, response: Response):
    db = get_db()
    record = await db.password_reset_tokens.find_one({"token": data.token}, {"_id": 0})
    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link.")
    if record.get("used"):
        raise HTTPException(status_code=400, detail="This reset link has already been used.")
    expires_at = record.get("expires_at")
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
    
    # SECURITY FIX H-7: Mark token as used immediately after validation, before password update
    # This prevents reuse if the password update fails or there's a race condition
    await db.password_reset_tokens.update_one({"token": data.token}, {"$set": {"used": True, "used_at": datetime.now(timezone.utc)}})

    email = record["email"]
    doc = await db.users.find_one({"email": email}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="User not found.")

    # Now update password (token already marked as used)
    await db.users.update_one({"email": email}, {"$set": {"password_hash": hash_password(data.new_password)}})

    # FIX CRITICAL-3: Invalidate ALL existing sessions after password reset.
    # If an attacker had hijacked a session, they lose access the moment the victim resets.
    await db.user_sessions.delete_many({"user_id": doc["user_id"]})

    token = await _create_session(db, doc["user_id"], response)
    await log_action("password_reset", doc["user_id"], email, "auth")
    await log_security_event(
        event_type="password_reset", severity="warning",
        user_id=doc["user_id"], user_email=email,
        resource="user", resource_id=doc["user_id"],
        action="password_reset", outcome="success",
    )
    user = await _build_user_with_permissions(doc)
    return {"user": user, "token": token, "message": "Password reset successful."}


@router.post("/setup-admin")
@limiter.limit("5/hour")
async def setup_admin(data: AdminSetupRequest, request: Request):
    """Create first admin - only works when no admin exists"""
    db = get_db()
    existing = await db.users.find_one({"role": "admin"}, {"_id": 0})
    if existing:
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
    await db.users.insert_one(
        {
            "user_id": user_id,
            "email": data.email.lower(),
            "name": data.name,
            "role": "admin",
            # Phase 1A multi-tenancy: first-admin bootstrap creates the
            # admin in the SimpliMeta root tenant by definition.
            "tenant_id": SIMPLIMETA_ROOT_TENANT_ID,
            "account_id": None,
            "picture": None,
            "is_active": True,
            "is_email_verified": True,
            "password_hash": hash_password(password),
            "zoho_contact_id": None,
            "created_at": datetime.now(timezone.utc),
        }
    )
    return {"message": "Admin created successfully", "user_id": user_id}


@router.post("/request-password-change")
@limiter.limit("3/hour")
async def request_password_change(request: Request, current_user: User = Depends(get_current_user)):
    """
    Authenticated user requests a password set/change email.
    Works for both SSO users (who never set a password) and password users.
    """
    db = get_db()

    reset_token = str(uuid.uuid4().hex)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    await db.password_reset_tokens.update_one(
        {"email": current_user.email.lower()},
        {"$set": {"token": reset_token, "expires_at": expires_at, "used": False, "created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )

    frontend_url = get_frontend_url(request)
    reset_link = f"{frontend_url}/reset-password?token={reset_token}"
    try:
        from services.email_service import send_password_change_email
        await send_password_change_email(current_user.email.lower(), current_user.name, reset_link)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Password change email failed (non-blocking): {e}")

    await log_action("password_change_requested", current_user.user_id, current_user.email, "auth")
    return {"message": "A password change link has been sent to your email."}
