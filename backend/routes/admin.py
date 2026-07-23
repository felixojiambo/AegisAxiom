from fastapi import APIRouter, HTTPException, Depends, Query, Request, UploadFile, File
from datetime import datetime, timezone
import uuid
import os
import re
import logging

from pymongo.errors import DuplicateKeyError

from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from db.session import get_session
from repositories.users import UserRepository
from repositories.tenants import TenantRepository

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
from models import UserInvite, PartnerApplicationCreate, UserUpdateRequest, PartnerRejectRequest
from auth_helper import get_current_user, create_invite_token, hash_password, has_perm, get_frontend_url
from middleware.ai_authz import require_ai_permission
from seeds.seed_tenants import SIMPLIMETA_ROOT_TENANT_ID
from rate_limiter import limiter
from services.audit_service import log_action
from services.contract_approval_access import (
    filter_and_paginate,
    resolve_approval_access,
)
from services.field_registry import register_fields, register_module_events, register_module_config

router = APIRouter()

register_module_config("admin", "users", "user_id", "Admin")
register_module_events("admin", "user", ["created", "updated", "deleted", "invited"])
register_fields("admin", "user", [
    {"name": "name", "type": "string", "label": "Name", "description": "User's full name"},
    {"name": "email", "type": "string", "label": "Email", "description": "User login email address"},
    {"name": "role", "type": "string", "label": "Role",
     "description": "Assigned role (admin, project_manager, sales_ops, etc.)"},
    {"name": "is_active", "type": "boolean", "label": "Is Active", "description": "Whether the user account is active"},
    {"name": "is_contractor", "type": "boolean", "label": "Is Contractor",
     "description": "Whether user is an external contractor"},
    {"name": "is_test_data", "type": "boolean", "label": "Is Test Data",
     "description": "Flag indicating test/demo data"},
])


# ---- USER MANAGEMENT ----

@router.get("/users")
async def get_users(current_user=Depends(get_current_user), pg: AsyncSession = Depends(get_session)):
    if not has_perm(current_user, "users.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    # Phase 1A: tenant-scope the user list. Users only see other users in
    # the same tenant. SimpliMeta root admins also only see SimpliMeta
    # users by default — cross-tenant visibility for root admins is a
    # Phase 1B feature (explicit "view as tenant X" mode).
    tenant_id = getattr(current_user, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=403, detail="User is not assigned to a tenant")
    users = await UserRepository(pg).list_users(tenant_id=tenant_id, limit=500)
    return users


@router.get(
    "/contract-approval-access",
    dependencies=[Depends(require_ai_permission("admin.users.view"))],
)
async def list_contract_approval_access(
    search: str = Query("", description="Filter by name, email, or role"),
    approval_filter: str = Query(
        "all",
        description="all | can_approve | role | override | none",
    ),
    page: int = Query(1, ge=1, description="1-indexed page number"),
    page_size: int = Query(50, description="25 | 50 | 100"),
    current_user=Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    """Paginated read of who can approve contracts in this tenant
    and why (role grant, direct override, both, or neither).

    Drives the admin Contract Approval Access page. Mutations
    (grant / revoke a direct override) continue to flow through
    PUT /api/admin/users/{user_id} with extra_permissions — this
    endpoint is read-only.

    Tenant-scoped. Gated on users.view (matches the existing user
    list); admin role is still required at PUT time to actually
    mutate extra_permissions.
    """
    if not has_perm(current_user, "users.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    tenant_id = getattr(current_user, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=403, detail="User is not assigned to a tenant")

    db = get_db()
    user_dtos = await UserRepository(pg).list_users(tenant_id=tenant_id, limit=5000)
    # resolve_approval_access is a pure helper operating on plain dicts
    # (it does `isinstance(user, dict)` guards), so serialize the DTOs.
    users = [u.model_dump() for u in user_dtos]
    # Roles are tenant-shared (system + custom). The roles collection
    # has no tenant_id field — `routes/roles.list_roles` returns the
    # full set. Mirror that here so role permission lookup works
    # for every user regardless of role.
    roles = await db.roles.find({}, {"_id": 0}).to_list(200)

    rows = resolve_approval_access(users, roles)
    return filter_and_paginate(
        rows,
        search=search,
        approval_filter=approval_filter,
        page=page,
        page_size=page_size,
    )


@router.post("/users")
@limiter.limit("10/hour")
async def create_user_direct(data: dict, request: Request, current_user=Depends(get_current_user),
                             pg: AsyncSession = Depends(get_session)):
    """
    Create a user directly (used by Contractor Management to create contractor
    user accounts linked to a contractor business). Sends an invite email so
    the user sets their own password.
    """
    if not has_perm(current_user, "users.invite"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    email = (data.get("email") or "").lower().strip()
    name = (data.get("name") or "").strip()
    role = data.get("role") or "contractor"

    if not email or not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Valid email is required")
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    db = get_db()

    # Validate role against roles collection (includes custom roles)
    valid_roles = {r["name"] for r in await db.roles.find({}, {"_id": 0, "name": 1}).to_list(100)}
    if role not in valid_roles:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role. Must be one of: {', '.join(sorted(valid_roles))}"
        )

    # Validate contractor_business_id against DB and canonicalize name from record
    contractor_business_id = data.get("contractor_business_id")
    contractor_business_name = None
    if contractor_business_id:
        biz = await db.contractor_businesses.find_one(
            {"business_id": contractor_business_id},
            {"_id": 0, "business_name": 1}
        )
        if not biz:
            raise HTTPException(status_code=400, detail="Contractor business not found")
        contractor_business_name = biz.get("business_name")

    if await UserRepository(pg).get_user_by_email(email):
        raise HTTPException(status_code=400, detail="Email already exists")

    user_id = f"user_{uuid.uuid4().hex[:12]}"
    invite_token = create_invite_token(email, user_id)

    # Build resource_type_assignments (contractors) — reject negative/non-numeric cost rates
    resource_type_assignments = []
    for rta in (data.get("resource_type_assignments") or []):
        if rta.get("resource_type_name") and rta.get("cost_rate") is not None:
            try:
                cost_rate = float(rta["cost_rate"])
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="cost_rate must be a number")
            if cost_rate < 0:
                raise HTTPException(status_code=400, detail="cost_rate cannot be negative")
            resource_type_assignments.append({
                "resource_type_id": rta.get("resource_type_id", rta["resource_type_name"]),
                "resource_type_name": rta["resource_type_name"],
                "cost_rate": cost_rate,
                "is_active": True,
                "assigned_at": datetime.now(timezone.utc),
                "assigned_by": current_user.user_id,
            })

    user_doc = {
        "user_id": user_id,
        "email": email,
        "name": name,
        "role": role,
        "is_contractor": role == "contractor",
        "phone": data.get("phone"),
        "account_id": data.get("account_id"),
        "contractor_business_id": contractor_business_id,
        "contractor_business_name": contractor_business_name,
        "picture": None,
        "is_active": False,
        "is_email_verified": False,
        "password_hash": None,
        "zoho_contact_id": None,
        "invite_token": invite_token,
        "is_test_data": bool(data.get("is_test_data", False)),
        "created_at": datetime.now(timezone.utc),
    }
    # Phase 1A multi-tenancy: stamp tenant_id from the inviter's tenant.
    user_doc["tenant_id"] = (
            getattr(current_user, "tenant_id", None) or SIMPLIMETA_ROOT_TENANT_ID
    )

    # resource_type_assignments become child rows — pass via the repo's
    # `assignments` kwarg rather than embedding them in the user doc.
    try:
        await UserRepository(pg).create_user(
            user_doc, assignments=resource_type_assignments or None
        )
    except DuplicateKeyError:
        raise HTTPException(status_code=400, detail="Email already exists")

    # Send invite email (non-blocking). Invite link is NOT returned — avoid token
    # exposure in API logs since the caller does not consume it.
    frontend_url = get_frontend_url(request)
    invite_link = f"{frontend_url}/accept-invite?token={invite_token}"
    try:
        from services.email_service import send_invite_email
        await send_invite_email(email, name, invite_link, role, current_user.name)
    except Exception as e:
        logger.warning(f"Email failed (non-blocking): {e}")

    await log_action("user_create", current_user.user_id, current_user.email, "user", user_id,
                     {"email": email, "role": role, "contractor_business_id": contractor_business_id})

    return {
        "message": f"User created and invite sent to {email}",
        "user_id": user_id,
    }


@router.post("/invite")
@limiter.limit("10/hour")
async def invite_user(data: UserInvite, request: Request, current_user=Depends(get_current_user),
                      pg: AsyncSession = Depends(get_session)):
    if not has_perm(current_user, "users.invite"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # SECURITY FIX M-2: Validate role strings against allowed values (now includes custom roles)
    db = get_db()
    valid_roles = {r["name"] for r in await db.roles.find({}, {"_id": 0, "name": 1}).to_list(100)}
    if data.role not in valid_roles:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role. Must be one of: {', '.join(sorted(valid_roles))}"
        )

    if await UserRepository(pg).get_user_by_email(data.email.lower()):
        raise HTTPException(status_code=400, detail="Email already exists")

    user_id = f"user_{uuid.uuid4().hex[:12]}"
    invite_token = create_invite_token(data.email, user_id)
    # Build resource_type_assignments for contractor role
    resource_type_assignments = []
    if data.role == "contractor" and data.resource_type_assignments:
        for rta in data.resource_type_assignments:
            if rta.get("resource_type_name") and rta.get("cost_rate"):
                resource_type_assignments.append({
                    "resource_type_id": rta.get("resource_type_id", rta["resource_type_name"]),
                    "resource_type_name": rta["resource_type_name"],
                    "cost_rate": float(rta["cost_rate"]),
                    "is_active": True,
                    "assigned_at": datetime.now(timezone.utc),
                    "assigned_by": current_user.user_id,
                })

    user_doc = {
        "user_id": user_id,
        "email": data.email.lower(),
        "name": data.name,
        "role": data.role,
        "account_id": data.account_id,
        "picture": None,
        "is_active": False,
        "is_email_verified": False,
        "password_hash": None,
        "zoho_contact_id": None,
        "invite_token": invite_token,
        "created_at": datetime.now(timezone.utc),
    }
    # Phase 1A multi-tenancy: stamp tenant_id from the inviter's tenant.
    # Phase 1D.8: if the caller explicitly targets a different tenant
    # via `target_tenant_id`, honor it — but only for root admins, and
    # only after validating the target exists and is not archived.
    # Non-root callers either omit target_tenant_id or pass their own
    # tenant_id; mismatched values are a 403 to prevent privilege
    # escalation.
    caller_tenant_id = getattr(current_user, "tenant_id", None) or SIMPLIMETA_ROOT_TENANT_ID
    is_root_caller = caller_tenant_id == SIMPLIMETA_ROOT_TENANT_ID

    if data.target_tenant_id:
        if data.target_tenant_id != caller_tenant_id and not is_root_caller:
            raise HTTPException(
                status_code=403,
                detail="You can only invite users into your own tenant.",
            )
        target = await TenantRepository(pg).get_tenant(data.target_tenant_id)
        if not target:
            raise HTTPException(status_code=404, detail="Target tenant not found.")
        if target.status == "archived":
            raise HTTPException(
                status_code=400,
                detail="Cannot invite users into an archived tenant.",
            )
        user_doc["tenant_id"] = data.target_tenant_id
    else:
        user_doc["tenant_id"] = caller_tenant_id

    await UserRepository(pg).create_user(
        user_doc, assignments=resource_type_assignments or None
    )
    frontend_url = get_frontend_url(request)
    invite_link = f"{frontend_url}/accept-invite?token={invite_token}"
    try:
        from services.email_service import send_invite_email
        await send_invite_email(data.email, data.name, invite_link, data.role, current_user.name)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Email failed (non-blocking): {e}")
    await log_action("user_invite", current_user.user_id, current_user.email, "user", user_id,
                     {"email": data.email, "role": data.role})
    return {"message": f"Invite sent to {data.email}", "user_id": user_id, "invite_link": invite_link}


@router.put("/users/{user_id}")
async def update_user(user_id: str, data: UserUpdateRequest, current_user=Depends(get_current_user),
                      pg: AsyncSession = Depends(get_session)):
    if not has_perm(current_user, "users.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Block self-escalation: users cannot change their own role or permissions
    if user_id == current_user.user_id and (data.role is not None or data.extra_permissions is not None):
        raise HTTPException(status_code=403, detail="Cannot modify your own role or permissions")

    # Only administrators can change user roles (prevents lateral/vertical escalation)
    if data.role is not None and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only administrators can change user roles")

    # Only administrators can modify per-user permission overrides
    if data.extra_permissions is not None and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only administrators can modify user permissions")

    db = get_db()

    # SECURITY FIX M-2: Validate role if being updated (now includes custom roles)
    if data.role is not None:
        valid_roles = {r["name"] for r in await db.roles.find({}, {"_id": 0, "name": 1}).to_list(100)}
        if data.role not in valid_roles:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid role. Must be one of: {', '.join(sorted(valid_roles))}"
            )
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}

    # Validate extra_permissions against the permission registry
    if "extra_permissions" in update_data:
        from services.permission_registry import get_valid_keys
        valid_keys = await get_valid_keys()
        invalid = [p for p in update_data["extra_permissions"] if p not in valid_keys]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid permission keys: {', '.join(invalid)}")

    # PROCUREMENT APPROVAL LOGIC: For contractors with resource_type_assignments
    if "resource_type_assignments" in update_data:
        user = await UserRepository(pg).get_user_by_id(user_id)

        if user and user.role == "contractor" and user.contractor_business_id:
            # Get contractor business to compare rates
            business = await db.contractor_businesses.find_one(
                {"business_id": user.contractor_business_id},
                {"_id": 0, "resource_types_offered": 1}
            )

            if business:
                # Process each resource type assignment
                processed_assignments = []

                for assignment in update_data["resource_type_assignments"]:
                    resource_type_id = assignment.get("resource_type_id")
                    individual_rate = float(assignment.get("cost_rate", 0))

                    # Find the business's approved rate for this resource type
                    business_resource = next(
                        (rt for rt in business.get("resource_types_offered", [])
                         if rt.get("resource_type_id") == resource_type_id),
                        None
                    )

                    # Add approval logic
                    if business_resource and business_resource.get("approval_status") == "approved":
                        business_rate = float(business_resource.get("cost_rate", 0))

                        # Compare individual rate to business locked rate
                        if individual_rate <= business_rate:
                            # Auto-approve
                            assignment["approval_status"] = "approved"
                            assignment["approved_by"] = "system"
                            assignment["approved_by_name"] = "Auto-approved"
                            assignment["approved_date"] = datetime.now(timezone.utc)
                            assignment["contractor_business_rate"] = business_rate
                        else:
                            # Needs Procurement approval
                            assignment["approval_status"] = "pending"
                            assignment["approved_by"] = None
                            assignment["approved_by_name"] = None
                            assignment["approved_date"] = None
                            assignment["contractor_business_rate"] = business_rate
                            assignment["rejection_reason"] = None
                    else:
                        # Business resource not approved yet - default to pending
                        assignment["approval_status"] = "pending"
                        assignment["approved_by"] = None
                        assignment["contractor_business_rate"] = None

                    # Ensure required fields
                    if "assigned_at" not in assignment:
                        assignment["assigned_at"] = datetime.now(timezone.utc)
                    if "assigned_by" not in assignment:
                        assignment["assigned_by"] = current_user.user_id

                    processed_assignments.append(assignment)

                # Replace assignments with processed ones
                update_data["resource_type_assignments"] = processed_assignments

    updated = await UserRepository(pg).update_user(user_id, update_data)
    await log_action("user_update", current_user.user_id, current_user.email, "user", user_id, {"changes": update_data})
    return updated


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, current_user=Depends(get_current_user),
                      pg: AsyncSession = Depends(get_session)):
    if not has_perm(current_user, "users.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if user_id == current_user.user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    db = get_db()
    users = UserRepository(pg)
    target = await users.get_user_by_id(user_id)
    await users.delete_user(user_id)
    # user_sessions rows cascade-delete with the user (FK ON DELETE CASCADE,
    # Phase 2a) — no separate session purge needed.
    await log_action("user_delete", current_user.user_id, current_user.email, "user", user_id,
                     {"deleted_email": target.email if target else "unknown"})
    return {"message": "User deleted"}


@router.post("/users/{user_id}/reset-ai-budget")
async def reset_ai_budget(user_id: str, current_user=Depends(get_current_user),
                          pg: AsyncSession = Depends(get_session)):
    """Reset a user's daily AI token budget without deleting usage history."""
    if not has_perm(current_user, "ai_usage.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    users = UserRepository(pg)
    target = await users.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)
    await users.update_user(user_id, {"budget_reset_at": now})
    await log_action("ai_budget_reset", current_user.user_id, current_user.email, "user", user_id, {
        "target_name": target.name or "",
        "reset_at": now.isoformat(),
    })
    return {"message": f"AI budget reset for {target.name or user_id}", "reset_at": now.isoformat()}


@router.post("/resend-invite/{user_id}")
async def resend_invite(user_id: str, request: Request, current_user=Depends(get_current_user),
                        pg: AsyncSession = Depends(get_session)):
    if not has_perm(current_user, "users.invite"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    users = UserRepository(pg)
    user = await users.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_active:
        raise HTTPException(status_code=400, detail="User has already accepted their invite")

    new_token = create_invite_token(user.email, user_id)
    await users.update_user(user_id, {"invite_token": new_token})
    frontend_url = get_frontend_url(request)
    invite_link = f"{frontend_url}/accept-invite?token={new_token}"
    try:
        from services.email_service import send_invite_email
        await send_invite_email(user.email, user.name, invite_link, user.role, current_user.name)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Email failed: {e}")
    return {"message": f"Invite resent to {user.email}", "invite_link": invite_link}


# ---- PARTNER APPLICATIONS ----

@router.get("/partner-applications")
async def get_partner_apps(current_user=Depends(get_current_user)):
    if not has_perm(current_user, "partner_apps.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    apps = await db.partner_applications.find({}, {"_id": 0}).sort("applied_at", -1).to_list(200)
    return apps


@router.put("/partner-applications/{app_id}/approve")
async def approve_partner(app_id: str, request: Request, current_user=Depends(get_current_user),
                          pg: AsyncSession = Depends(get_session)):
    if not has_perm(current_user, "partner_apps.approve"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    app = await db.partner_applications.find_one({"application_id": app_id}, {"_id": 0})
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    user_id = f"user_{uuid.uuid4().hex[:12]}"
    invite_token = create_invite_token(app["email"], user_id)
    account_id = f"acc_{uuid.uuid4().hex[:12]}"

    await UserRepository(pg).create_user(
        {
            "user_id": user_id,
            "email": app["email"].lower(),
            "name": app["contact_name"],
            "role": "agent",
            # Phase 1A multi-tenancy: stamp tenant_id from the approving admin.
            "tenant_id": (
                    getattr(current_user, "tenant_id", None) or SIMPLIMETA_ROOT_TENANT_ID
            ),
            "account_id": account_id,
            "picture": None,
            "is_active": False,
            "is_email_verified": False,
            "password_hash": None,
            "invite_token": invite_token,
            "zoho_contact_id": None,
            "created_at": datetime.now(timezone.utc),
        }
    )
    await UserRepository(pg).create_user(
        {
            "user_id": user_id,
            "email": app["email"].lower(),
            "name": app["contact_name"],
            "role": "agent",
            "account_id": account_id,
            "picture": None,
            "is_active": False,
            "is_email_verified": False,
            "password_hash": None,
            "invite_token": invite_token,
            "zoho_contact_id": None,
            "created_at": datetime.now(timezone.utc),
        }
    )
    await db.partner_applications.update_one(
        {"application_id": app_id},
        {"$set": {"status": "approved", "reviewed_at": datetime.now(timezone.utc),
                  "reviewed_by": current_user.user_id}},
    )
    frontend_url = get_frontend_url(request)
    invite_link = f"{frontend_url}/accept-invite?token={invite_token}"
    try:
        from services.email_service import send_partner_approval_email
        await send_partner_approval_email(app["email"], app["contact_name"], invite_link)
    except Exception:
        pass
    return {"message": "Partner approved", "invite_link": invite_link}


@router.put("/partner-applications/{app_id}/reject")
async def reject_partner(app_id: str, data: PartnerRejectRequest, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "partner_apps.approve"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    app = await db.partner_applications.find_one({"application_id": app_id}, {"_id": 0})
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    await db.partner_applications.update_one(
        {"application_id": app_id},
        {"$set": {"status": "rejected", "reviewed_at": datetime.now(timezone.utc), "reviewed_by": current_user.user_id,
                  "notes": data.reason}},
    )
    try:
        from services.email_service import send_partner_rejection_email
        await send_partner_rejection_email(app["email"], app["contact_name"], data.reason)
    except Exception:
        pass
    return {"message": "Application rejected"}


# ---- DASHBOARD STATS ----

@router.get("/stats")
async def get_stats(current_user=Depends(get_current_user), pg: AsyncSession = Depends(get_session)):
    if not has_perm(current_user, "users.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    total_accounts = await db.accounts.count_documents({})
    total_deals = await db.deals.count_documents({})
    total_users = await UserRepository(pg).count_users()
    pending_apps = await db.partner_applications.count_documents({"status": "pending"})

    comms = await db.commissions.find({},
                                      {"_id": 0, "amount_owed": 1, "amount_paid": 1, "potential_amount": 1}).to_list(
        1000)
    invs = await db.invoices.find({}, {"_id": 0, "total_amount": 1, "status": 1}).to_list(1000)

    stage_agg = await db.deals.aggregate([{"$group": {"_id": "$stage", "count": {"$sum": 1}}}]).to_list(20)
    recent_deals = await db.deals.find({}, {"_id": 0, "deal_id": 1, "name": 1, "stage": 1, "amount": 1,
                                            "account_name": 1}).sort("created_at", -1).to_list(5)

    return {
        "total_accounts": total_accounts,
        "total_deals": total_deals,
        "total_users": total_users,
        "pending_partner_applications": pending_apps,
        "total_commissions_owed": sum(c.get("amount_owed", 0) for c in comms),
        "total_commissions_paid": sum(c.get("amount_paid", 0) for c in comms),
        "total_invoiced": sum(i.get("total_amount", 0) for i in invs),
        "total_paid_invoices": sum(i.get("total_amount", 0) for i in invs if i.get("status") == "paid"),
        "deals_by_stage": {s["_id"]: s["count"] for s in stage_agg},
        "recent_deals": recent_deals,
    }


# ---- SEED DATA ----

@router.post("/seed")
async def seed_data(current_user=Depends(get_current_user)):
    if not has_perm(current_user, "users.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    if await db.accounts.count_documents({}) > 2:
        return {"message": "Data already seeded"}

    accounts = [
        {"account_id": "acc_demo001", "name": "TechCorp Solutions", "account_type": "company",
         "email": "contact@techcorp.com", "industry": "Technology", "assigned_sales_rep_id": current_user.user_id,
         "zoho_account_id": None, "qbo_customer_id": None, "created_at": datetime.now(timezone.utc)},
        {"account_id": "acc_demo002", "name": "Green Energy Partners", "account_type": "partner_agency",
         "email": "info@greenenergy.com", "industry": "Energy", "assigned_agent_id": None, "zoho_account_id": None,
         "qbo_customer_id": None, "created_at": datetime.now(timezone.utc)},
        {"account_id": "acc_demo003", "name": "Metro Healthcare", "account_type": "company",
         "email": "it@metrohealthcare.com", "industry": "Healthcare", "assigned_sales_rep_id": current_user.user_id,
         "zoho_account_id": None, "qbo_customer_id": None, "created_at": datetime.now(timezone.utc)},
    ]
    await db.accounts.insert_many(accounts)

    deals = [
        {"deal_id": "deal_demo001", "name": "TechCorp - Network Infra", "account_id": "acc_demo001",
         "account_name": "TechCorp Solutions", "stage": "Proposal", "amount": 45000,
         "deal_owner_id": current_user.user_id, "deal_owner_name": current_user.name, "probability": 60,
         "assigned_sales_rep_id": current_user.user_id, "assigned_agent_id": None, "zoho_deal_id": None,
         "created_at": datetime.now(timezone.utc)},
        {"deal_id": "deal_demo002", "name": "Green Energy - Monitoring", "account_id": "acc_demo002",
         "account_name": "Green Energy Partners", "stage": "Closed Won", "amount": 28000,
         "deal_owner_id": current_user.user_id, "deal_owner_name": current_user.name, "probability": 100,
         "assigned_sales_rep_id": None, "assigned_agent_id": None, "zoho_deal_id": None,
         "created_at": datetime.now(timezone.utc)},
        {"deal_id": "deal_demo003", "name": "Metro - Cloud Migration", "account_id": "acc_demo003",
         "account_name": "Metro Healthcare", "stage": "Negotiation", "amount": 75000,
         "deal_owner_id": current_user.user_id, "deal_owner_name": current_user.name, "probability": 80,
         "assigned_sales_rep_id": current_user.user_id, "assigned_agent_id": None, "zoho_deal_id": None,
         "created_at": datetime.now(timezone.utc)},
        {"deal_id": "deal_demo004", "name": "TechCorp - Cybersecurity Audit", "account_id": "acc_demo001",
         "account_name": "TechCorp Solutions", "stage": "Qualification", "amount": 15000,
         "deal_owner_id": current_user.user_id, "deal_owner_name": current_user.name, "probability": 40,
         "assigned_sales_rep_id": None, "assigned_agent_id": None, "zoho_deal_id": None,
         "created_at": datetime.now(timezone.utc)},
    ]
    await db.deals.insert_many(deals)

    await db.commissions.insert_many([
        {"commission_id": "comm_demo001", "deal_id": "deal_demo002", "deal_name": "Green Energy - Monitoring",
         "user_id": current_user.user_id, "user_name": current_user.name, "commission_type": "sales_rep",
         "amount_owed": 2800, "amount_paid": 2800, "potential_amount": 2800, "commission_rate": 10, "status": "paid",
         "paid_date": "2025-01-15", "created_at": datetime.now(timezone.utc)},
        {"commission_id": "comm_demo002", "deal_id": "deal_demo003", "deal_name": "Metro - Cloud Migration",
         "user_id": current_user.user_id, "user_name": current_user.name, "commission_type": "sales_rep",
         "amount_owed": 7500, "amount_paid": 0, "potential_amount": 7500, "commission_rate": 10, "status": "pending",
         "paid_date": None, "created_at": datetime.now(timezone.utc)},
    ])

    await db.invoices.insert_many([
        {"invoice_id": "inv_demo001", "invoice_number": "INV-0001", "account_id": "acc_demo002",
         "account_name": "Green Energy Partners", "deal_id": "deal_demo002", "contract_id": None, "amount": 28000,
         "tax_amount": 2240, "total_amount": 30240, "status": "paid", "due_date": "2025-01-30",
         "paid_date": "2025-01-28", "qbo_invoice_id": None, "assigned_sales_rep_id": None,
         "description": "Network monitoring system", "created_at": datetime.now(timezone.utc)},
        {"invoice_id": "inv_demo002", "invoice_number": "INV-0002", "account_id": "acc_demo003",
         "account_name": "Metro Healthcare", "deal_id": "deal_demo003", "contract_id": None, "amount": 25000,
         "tax_amount": 2000, "total_amount": 27000, "status": "sent", "due_date": "2025-02-28", "paid_date": None,
         "qbo_invoice_id": None, "assigned_sales_rep_id": current_user.user_id,
         "description": "Cloud migration - Phase 1", "created_at": datetime.now(timezone.utc)},
    ])

    return {"message": "Sample data created successfully"}


@router.post("/seed-all")
async def seed_all_data(current_user=Depends(get_current_user)):
    """Run the comprehensive seed script that creates fully-linked test data across ALL modules."""
    if not has_perm(current_user, "*"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    from seeds.seed_all import seed_all as run_seed
    await run_seed()
    return {"message": "Comprehensive test data seeded successfully."}


@router.delete("/seed-all")
async def cleanup_seed_data(current_user=Depends(get_current_user)):
    """Delete all test data (is_test_data: True) across every collection."""
    if not has_perm(current_user, "*"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    from seeds.seed_all import clean, PMPROJ_IDS  # noqa: F811
    db = get_db()
    deleted = await clean(db)
    total = sum(deleted.values())
    return {"message": f"Cleaned {total} test documents", "details": deleted}


# ---- FILE UPLOAD ----

@router.post("/upload")
async def upload_file(
        file: UploadFile = File(...),
        current_user=Depends(get_current_user),
        db=Depends(get_db)
):
    """Upload a file (W9, documents, etc.) and return file URL."""
    if not has_perm(current_user, "users.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")

    # Validate file extension
    ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".png", ".jpg", ".jpeg", ".gif", ".txt"}
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

    file_ext = os.path.splitext(file.filename or "")[1].lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400,
                            detail=f"File type '{file_ext}' not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    # Create uploads directory if it doesn't exist
    upload_dir = "/app/backend/uploads"
    os.makedirs(upload_dir, exist_ok=True)

    # Generate unique filename
    unique_filename = f"{uuid.uuid4().hex}{file_ext}"
    file_path = os.path.join(upload_dir, unique_filename)

    # Save file with size check
    try:
        contents = await file.read()
        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400,
                                detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)} MB")
        with open(file_path, "wb") as f:
            f.write(contents)

        # Return URL that can be accessed via backend static files
        # Note: In production, this should be served via CDN/S3
        file_url = f"/uploads/{unique_filename}"

        return {
            "message": "File uploaded successfully",
            "file_url": file_url,
            "filename": file.filename
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")