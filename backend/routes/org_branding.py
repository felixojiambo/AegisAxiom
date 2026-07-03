"""Organization branding settings for SOW document generation.

Allows each organization to customize provider name, legal text,
and other branding elements used when generating SOW Google Docs.
"""

from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone

from database import get_db
from models import OrganizationBrandingUpdate
from auth_helper import get_current_user, has_perm

router = APIRouter()

# Default org_id for the singleton branding record
DEFAULT_ORG_ID = "default"

# Default branding values (used when no record exists yet)
_DEFAULTS = {
    "org_id": DEFAULT_ORG_ID,
    "provider_name": "MetaData Engineering d/b/a SimpliMeta",
    "legal_text": (
        "Unless otherwise agreed to in this document, the Terms and Conditions are "
        "governed by MetaData Engineering d/b/a SimpliMeta's Master Services Agreement "
        "available for review at https://simplimeta.io/legal"
    ),
    "logo_url": None,
    "primary_color": None,
}


@router.get("/branding")
async def get_branding(
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get the organization branding settings."""
    if not has_perm(current_user, "branding.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    doc = await db.org_branding.find_one(
        {"org_id": DEFAULT_ORG_ID}, {"_id": 0}
    )
    return doc or _DEFAULTS


@router.put("/branding")
async def update_branding(
    payload: OrganizationBrandingUpdate,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update organization branding settings."""
    if not has_perm(current_user, "branding.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    updates = {
        k: v for k, v in payload.model_dump().items() if v is not None
    }
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates["updated_at"] = datetime.now(timezone.utc)

    await db.org_branding.update_one(
        {"org_id": DEFAULT_ORG_ID},
        {"$set": updates, "$setOnInsert": {"org_id": DEFAULT_ORG_ID}},
        upsert=True,
    )

    doc = await db.org_branding.find_one(
        {"org_id": DEFAULT_ORG_ID}, {"_id": 0}
    )
    return doc
