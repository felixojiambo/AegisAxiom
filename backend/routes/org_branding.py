"""Organization branding settings for SOW document generation.

Allows each organization to customize provider name, legal text,
and other branding elements used when generating SOW Google Docs.

Phase 2a: backed by the Postgres ``org_branding`` table via the branding
repository.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_session
from models import OrganizationBrandingUpdate
from auth_helper import get_current_user, has_perm
from repositories.org_branding import BrandingRepository

router = APIRouter()

# Default org_id for the singleton branding record
DEFAULT_ORG_ID = "default"

# Default branding values (returned when no record exists yet). Shape preserved
# byte-for-byte from the Mongo era: no `updated_at` key on the defaults.
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
    pg: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user),
):
    """Get the organization branding settings."""
    if not has_perm(current_user, "branding.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    branding = await BrandingRepository(pg).get_branding(DEFAULT_ORG_ID)
    return branding or _DEFAULTS


@router.put("/branding")
async def update_branding(
    payload: OrganizationBrandingUpdate,
    pg: AsyncSession = Depends(get_session),
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

    return await BrandingRepository(pg).upsert_branding(DEFAULT_ORG_ID, updates)
