"""
Contact Management Module
Manage contacts associated with accounts.
Contacts are people/stakeholders at customer accounts who may or may not have system login.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
import uuid
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from db.session import get_session
from repositories.users import UserRepository
from models import Contact, ContactCreate, ContactUpdate
from auth_helper import get_current_user, has_perm
from seeds.seed_tenants import SIMPLIMETA_ROOT_TENANT_ID
from services.event_bus import emit_event
from services.field_registry import register_fields, register_module_events, register_module_config

logger = logging.getLogger(__name__)

register_module_config("contacts", "contacts", "contact_id", "CRM & Sales")
register_module_events("contacts", "contact", ["created", "updated", "deleted"])
register_fields("contacts", "contact", [
    {"name": "first_name", "type": "string", "label": "First Name", "description": "Contact's first name"},
    {"name": "last_name", "type": "string", "label": "Last Name", "description": "Contact's last name"},
    {"name": "email", "type": "string", "label": "Email", "description": "Contact email address"},
    {"name": "phone", "type": "string", "label": "Phone", "description": "Office phone number"},
    {"name": "mobile_phone", "type": "string", "label": "Mobile Phone", "description": "Mobile/cell phone number"},
    {"name": "title", "type": "string", "label": "Title", "description": "Job title or position"},
    {"name": "department", "type": "string", "label": "Department", "description": "Department within the account's organization"},
    {"name": "account_name", "type": "string", "label": "Account Name", "description": "Company this contact belongs to"},
    {"name": "is_primary", "type": "boolean", "label": "Is Primary", "description": "Whether this is the primary contact for the account"},
    {"name": "is_active", "type": "boolean", "label": "Is Active", "description": "Whether this contact is currently active"},
    {"name": "preferred_contact_method", "type": "string", "label": "Preferred Contact Method", "description": "Preferred communication channel"},
    {"name": "source", "type": "string", "label": "Source", "description": "Data source (local, zoho, import)"},
    {"name": "is_test_data", "type": "boolean", "label": "Is Test Data", "description": "Flag indicating test/demo data"},
])

router = APIRouter()


async def can_edit_contact(current_user, contact: dict) -> bool:
    """Check if user can edit a contact (must own the parent account for edit.own)."""
    if has_perm(current_user, "contacts.edit.all"):
        return True
    if has_perm(current_user, "contacts.edit.own"):
        db = get_db()
        account = await db.accounts.find_one(
            {"account_id": contact.get("account_id")},
            {"_id": 0, "assigned_sales_rep_id": 1, "assigned_agent_id": 1}
        )
        if account and (
            account.get("assigned_sales_rep_id") == current_user.user_id
            or account.get("assigned_agent_id") == current_user.user_id
        ):
            return True
    return False


# ---- CRUD OPERATIONS ----

@router.get("/contacts")
async def get_contacts(
    skip: int = 0,
    limit: int = 1000,
    account_id: str = None,
    is_active: bool = None,
    test_only: bool = False,
    search: str = None,
    current_user=Depends(get_current_user),
    pg: AsyncSession = Depends(get_session)
):
    """Get all contacts with optional filters."""
    db = get_db()
    
    # Build base filter
    base_filter = {}
    
    # RBAC: Users with view.own can only see contacts for accounts they own
    if not has_perm(current_user, "contacts.view.all"):
        if has_perm(current_user, "contacts.view.own"):
            # Get accounts owned by user
            user_accounts = await db.accounts.find(
                {"assigned_sales_rep_id": current_user.user_id},
                {"_id": 0, "account_id": 1}
            ).to_list(5000)
            account_ids = [acc["account_id"] for acc in user_accounts]
            base_filter["account_id"] = {"$in": account_ids}
        else:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # Apply filters
    if account_id:
        base_filter["account_id"] = account_id
    if is_active is not None:
        base_filter["is_active"] = is_active
    # Test data filter: default shows real data only, toggle shows test data only
    base_filter["is_test_data"] = True if test_only else {"$ne": True}

    # Search by name, email, title
    if search:
        safe_search = re.escape(search)
        base_filter["$or"] = [
            {"first_name": {"$regex": safe_search, "$options": "i"}},
            {"last_name": {"$regex": safe_search, "$options": "i"}},
            {"name": {"$regex": safe_search, "$options": "i"}},
            {"email": {"$regex": safe_search, "$options": "i"}},
            {"title": {"$regex": safe_search, "$options": "i"}}
        ]
    
    capped = min(limit, 5000)
    docs = await db.contacts.find(base_filter, {"_id": 0}).sort("last_name", 1).skip(skip).limit(capped).to_list(capped)
    total = await db.contacts.count_documents(base_filter)

    # Enrich contacts with sales rep names if missing
    rep_ids = set()
    for doc in docs:
        if doc.get("assigned_sales_rep_id") and not doc.get("assigned_sales_rep_name"):
            rep_ids.add(doc["assigned_sales_rep_id"])
    if rep_ids:
        users = await UserRepository(pg).get_users_by_ids(list(rep_ids))
        name_map = {u.user_id: u.name for u in users}
        for doc in docs:
            if doc.get("assigned_sales_rep_id") and not doc.get("assigned_sales_rep_name"):
                doc["assigned_sales_rep_name"] = name_map.get(doc["assigned_sales_rep_id"])

    return {"items": docs, "total": total, "skip": skip, "limit": limit}


@router.post("/contacts")
async def create_contact(data: ContactCreate, current_user=Depends(get_current_user),
    pg: AsyncSession = Depends(get_session)):
    """Create a new contact."""
    if not has_perm(current_user, "contacts.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    
    # Validate account exists
    account = await db.accounts.find_one({"account_id": data.account_id}, {"_id": 0})
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    # Check if email already exists for this account (if email provided)
    if data.email:
        existing = await db.contacts.find_one({
            "account_id": data.account_id,
            "email": data.email
        }, {"_id": 0})
        if existing:
            raise HTTPException(
                status_code=400,
                detail="A contact with this email already exists for this account"
            )
    
    contact_id = f"con_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    
    # Compute full name
    full_name = f"{data.first_name} {data.last_name}".strip()
    
    doc = {
        "contact_id": contact_id,
        "account_id": data.account_id,
        "account_name": account.get("name"),
        "first_name": data.first_name,
        "last_name": data.last_name,
        "name": full_name,
        "email": data.email,
        "phone": data.phone,
        "mobile_phone": data.mobile_phone,
        "title": data.title,
        "department": data.department,
        "linkedin_url": data.linkedin_url,
        "preferred_contact_method": data.preferred_contact_method,
        "is_primary": data.is_primary,
        "is_active": True,
        "linked_user_id": None,
        "source": "local",
        "zoho_contact_id": None,
        "notes": data.notes,
        "is_test_data": data.is_test_data,
        "assigned_sales_rep_id": data.assigned_sales_rep_id,
        "assigned_sales_rep_name": None,
        "created_at": now,
        "updated_at": now
    }

    # Auto-fill sales rep from account if none specified
    if not doc.get("assigned_sales_rep_id") and account.get("assigned_sales_rep_id"):
        doc["assigned_sales_rep_id"] = account["assigned_sales_rep_id"]

    # Always resolve sales rep name server-side from ID
    if doc.get("assigned_sales_rep_id"):
        rep_user = await UserRepository(pg).get_user_by_id(doc["assigned_sales_rep_id"])
        if rep_user:
            doc["assigned_sales_rep_name"] = rep_user.name
    
    # If this is set as primary, unset any existing primary contact for this account
    if data.is_primary:
        await db.contacts.update_many(
            {"account_id": data.account_id, "is_primary": True},
            {"$set": {"is_primary": False, "updated_at": now}}
        )
    
    await db.contacts.insert_one(doc)
    doc.pop("_id", None)
    logger.info(f"Contact created: {contact_id} ({full_name}) for account {data.account_id}")
    await emit_event("contacts", "created", record=doc, user_id=current_user.user_id, user_email=current_user.email)

    return Contact(**doc)


@router.get("/contacts/sales-reps")
async def get_sales_reps_for_contacts(current_user=Depends(get_current_user),
    pg: AsyncSession = Depends(get_session)):
    """Get users eligible to be assigned as sales rep on contacts."""
    if not has_perm(current_user, "contacts.create") and not has_perm(current_user, "contacts.edit.own") and not has_perm(current_user, "contacts.edit.all"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    user_objs = await UserRepository(pg).list_users(
        is_active=True, role_in=["admin", "director", "sales_rep"], limit=200
    )
    return [
        {"user_id": u.user_id, "name": u.name, "role": u.role}
        for u in user_objs
    ]


@router.get("/contacts/{contact_id}")
async def get_contact(contact_id: str, current_user=Depends(get_current_user)):
    """Get a single contact by ID."""
    db = get_db()
    
    contact = await db.contacts.find_one({"contact_id": contact_id}, {"_id": 0})
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    
    # RBAC check
    if not has_perm(current_user, "contacts.view.all"):
        if has_perm(current_user, "contacts.view.own"):
            # Check if user owns the account
            account = await db.accounts.find_one(
                {"account_id": contact["account_id"]},
                {"_id": 0, "assigned_sales_rep_id": 1}
            )
            if not account or account.get("assigned_sales_rep_id") != current_user.user_id:
                raise HTTPException(status_code=403, detail="You can only view contacts for your own accounts")
        else:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    return contact


@router.put("/contacts/{contact_id}")
async def update_contact(
    contact_id: str,
    data: ContactUpdate,
    current_user=Depends(get_current_user),
    pg: AsyncSession = Depends(get_session)
):
    """Update a contact."""
    db = get_db()
    
    contact = await db.contacts.find_one({"contact_id": contact_id}, {"_id": 0})
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    
    # Check edit permissions
    if not await can_edit_contact(current_user, contact):
        raise HTTPException(status_code=403, detail="Insufficient permissions to edit this contact")
    
    # Build update document
    update_data = {}
    
    if data.first_name is not None:
        update_data["first_name"] = data.first_name
    if data.last_name is not None:
        update_data["last_name"] = data.last_name
    
    # Recompute name if either first or last name changed
    if data.first_name is not None or data.last_name is not None:
        first = data.first_name if data.first_name is not None else contact.get("first_name", "")
        last = data.last_name if data.last_name is not None else contact.get("last_name", "")
        update_data["name"] = f"{first} {last}".strip()
    
    if data.email is not None:
        # Check for duplicate email in same account
        if data.email:
            existing = await db.contacts.find_one({
                "account_id": contact["account_id"],
                "email": data.email,
                "contact_id": {"$ne": contact_id}
            }, {"_id": 0})
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail="A contact with this email already exists for this account"
                )
        update_data["email"] = data.email
    
    if data.phone is not None:
        update_data["phone"] = data.phone
    if data.mobile_phone is not None:
        update_data["mobile_phone"] = data.mobile_phone
    if data.title is not None:
        update_data["title"] = data.title
    if data.department is not None:
        update_data["department"] = data.department
    if data.linkedin_url is not None:
        update_data["linkedin_url"] = data.linkedin_url
    if data.preferred_contact_method is not None:
        update_data["preferred_contact_method"] = data.preferred_contact_method
    if data.is_active is not None:
        update_data["is_active"] = data.is_active
    if data.notes is not None:
        update_data["notes"] = data.notes
    if data.is_test_data is not None:
        update_data["is_test_data"] = data.is_test_data
    if data.assigned_sales_rep_id is not None:
        update_data["assigned_sales_rep_id"] = data.assigned_sales_rep_id
        if not data.assigned_sales_rep_id:
            # Clearing the sales rep
            update_data["assigned_sales_rep_name"] = None
        else:
            # Always resolve name server-side from ID
            rep_user = await UserRepository(pg).get_user_by_id(data.assigned_sales_rep_id)
            update_data["assigned_sales_rep_name"] = rep_user.name if rep_user else None

    # Handle primary contact flag
    if data.is_primary is not None:
        if data.is_primary:
            # Unset any existing primary contact for this account
            await db.contacts.update_many(
                {"account_id": contact["account_id"], "is_primary": True},
                {"$set": {"is_primary": False, "updated_at": datetime.now(timezone.utc)}}
            )
        update_data["is_primary"] = data.is_primary
    
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    await db.contacts.update_one(
        {"contact_id": contact_id},
        {"$set": update_data}
    )
    
    logger.info(f"Contact updated: {contact_id} by {current_user.email}")

    updated_contact = await db.contacts.find_one({"contact_id": contact_id}, {"_id": 0})
    await emit_event("contacts", "updated", record=updated_contact, old_record=contact, user_id=current_user.user_id, user_email=current_user.email)
    return updated_contact


@router.delete("/contacts/{contact_id}")
async def delete_contact(contact_id: str, current_user=Depends(get_current_user)):
    """Delete a contact."""
    db = get_db()
    
    contact = await db.contacts.find_one({"contact_id": contact_id}, {"_id": 0})
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    
    # Check permissions
    if not await can_edit_contact(current_user, contact):
        raise HTTPException(status_code=403, detail="Insufficient permissions to delete this contact")
    
    # Check if contact is linked to any opportunities
    opp_count = await db.opportunities.count_documents({"contact_id": contact_id})
    if opp_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete contact. It is linked to {opp_count} opportunity(ies). Please update or delete those first."
        )
    
    await db.contacts.delete_one({"contact_id": contact_id})
    
    logger.info(f"Contact deleted: {contact_id} by {current_user.email}")
    await emit_event("contacts", "deleted", record=contact, user_id=current_user.user_id, user_email=current_user.email)

    return {"message": "Contact deleted successfully", "contact_id": contact_id}


# ---- USER PROMOTION ----

@router.post("/contacts/{contact_id}/create-user")
async def create_user_from_contact(
    contact_id: str,
    password: str,
    role: str = "customer",
    current_user=Depends(get_current_user),
    pg: AsyncSession = Depends(get_session)
):
    """
    Promote a contact to a user account (give them login capability).
    This creates a user record and links it to the contact.
    """
    if not has_perm(current_user, "users.manage"):
        raise HTTPException(status_code=403, detail="Only admins can create user accounts")

    db = get_db()
    
    contact = await db.contacts.find_one({"contact_id": contact_id}, {"_id": 0})
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    
    # Check if contact already has a user
    if contact.get("linked_user_id"):
        raise HTTPException(status_code=400, detail="Contact already has a user account")
    
    # Check if email is provided
    if not contact.get("email"):
        raise HTTPException(status_code=400, detail="Contact must have an email to create a user account")
    
    # Check if user with this email already exists
    existing_user = await UserRepository(pg).get_user_by_email(contact["email"])
    if existing_user:
        raise HTTPException(status_code=400, detail="A user with this email already exists")
    
    # Create user
    import bcrypt
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    user_doc = {
        "user_id": user_id,
        "email": contact["email"],
        "name": contact["name"],
        "role": role,
        # Phase 1A multi-tenancy: stamp tenant_id from the admin converting
        # the contact. Phase 1C will tenant-scope contacts and this should
        # use the contact's tenant_id directly.
        "tenant_id": (
                getattr(current_user, "tenant_id", None) or SIMPLIMETA_ROOT_TENANT_ID
        ),
        "account_id": contact["account_id"],
        "contractor_business_id": None,
        "picture": None,
        "is_active": True,
        "is_email_verified": False,
        "zoho_contact_id": contact.get("zoho_contact_id"),
        "permissions": [],
        "password_hash": hashed_password,
        "created_at": now
    }

    await UserRepository(pg).create_user(user_doc)

    # Link user to contact
    await db.contacts.update_one(
        {"contact_id": contact_id},
        {"$set": {"linked_user_id": user_id, "updated_at": now}}
    )
    
    logger.info(f"User created from contact: {contact_id} -> {user_id} by {current_user.email}")
    
    return {
        "message": "User account created successfully",
        "user_id": user_id,
        "contact_id": contact_id,
        "email": contact["email"]
    }


# ---- STATISTICS ----

@router.get("/contacts/stats/by-account")
async def get_contacts_by_account(current_user=Depends(get_current_user)):
    """Get count of contacts grouped by account."""
    if not has_perm(current_user, "contacts.view.all"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db = get_db()
    
    # Aggregate contacts by account
    pipeline = [
        {"$group": {
            "_id": "$account_id",
            "account_name": {"$first": "$account_name"},
            "count": {"$sum": 1}
        }},
        {"$sort": {"count": -1}},
        {"$limit": 50}
    ]
    
    results = await db.contacts.aggregate(pipeline).to_list(50)
    
    return {
        "accounts": [
            {
                "account_id": r["_id"],
                "account_name": r["account_name"],
                "contact_count": r["count"]
            }
            for r in results
        ]
    }
