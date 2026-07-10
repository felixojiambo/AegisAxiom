"""Resources API - unified endpoint for all assignable resources (internal + contractors)."""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from database import get_db
from auth_helper import get_current_user, has_perm

router = APIRouter()


@router.get("/resources")
async def get_all_resources(
    role: Optional[str] = None,
    include_inactive: bool = False,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Get all assignable resources.
    - For PM dropdown: ?role=project_manager
    - For task assignment: Use /resources/contractors instead
    """
    if not has_perm(current_user, "users.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    query = {}
    if not include_inactive:
        query["is_active"] = True
    
    if role:
        query["role"] = role
    
    # Get users matching criteria
    all_users = await db.users.find(query, {"_id": 0}).sort("name", 1).to_list(1000)
    
    # Format for dropdown
    resources = []
    for user in all_users:
        resource = {
            "user_id": user["user_id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
            "is_contractor": user.get("contractor_business_id") is not None,
            "contractor_business_id": user.get("contractor_business_id"),
            "contractor_business_name": user.get("contractor_business_name"),
        }
        resources.append(resource)
    
    return {"items": resources, "total": len(resources)}


@router.get("/resources/contractors")
async def get_contractor_resources(
    business_id: Optional[str] = None,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get only contractor resources, optionally filtered by business."""
    if not has_perm(current_user, "users.view"):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    query = {"is_active": True}
    
    if business_id:
        query["contractor_business_id"] = business_id
    else:
        # Get all contractors (those with contractor_business_id)
        query["contractor_business_id"] = {"$ne": None, "$exists": True}
    
    contractors = await db.users.find(query, {"_id": 0}).sort("name", 1).to_list(1000)
    
    return {"items": contractors, "total": len(contractors)}
