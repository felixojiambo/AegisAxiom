"""Resources API - unified endpoint for all assignable resources (internal + contractors)."""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from db.session import get_session
from repositories.users import UserRepository
from auth_helper import get_current_user, has_perm

router = APIRouter()


@router.get("/resources")
async def get_all_resources(
    role: Optional[str] = None,
    include_inactive: bool = False,
    pg: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user)
):
    """
    Get all assignable resources.
    - For PM dropdown: ?role=project_manager
    - For task assignment: Use /resources/contractors instead
    """
    if not has_perm(current_user, "users.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    # Get users matching criteria
    all_users = await UserRepository(pg).list_users(
        role=role or None,
        is_active=True if not include_inactive else None,
        limit=1000,
    )

    # Format for dropdown
    resources = []
    for user in all_users:
        resource = {
            "user_id": user.user_id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "is_contractor": user.contractor_business_id is not None,
            "contractor_business_id": user.contractor_business_id,
            "contractor_business_name": user.contractor_business_name,
        }
        resources.append(resource)

    return {"items": resources, "total": len(resources)}


@router.get("/resources/contractors")
async def get_contractor_resources(
    business_id: Optional[str] = None,
    pg: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user)
):
    """Get only contractor resources, optionally filtered by business."""
    if not has_perm(current_user, "users.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    repo = UserRepository(pg)
    if business_id:
        contractors = await repo.list_users(
            contractor_business_id=business_id, is_active=True, limit=1000
        )
    else:
        # All contractors (those linked to a contractor business).
        contractors = await repo.list_users(
            contractor_business_id_not_null=True, is_active=True, limit=1000
        )

    return {"items": contractors, "total": len(contractors)}
