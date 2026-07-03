"""
User Resource Type Management
Endpoints for admins to assign/remove resource types from users with cost rates
"""
from fastapi import APIRouter, Depends, HTTPException
from database import get_db
from auth_helper import get_current_user, has_perm
from datetime import datetime, timezone
from models import UserResourceTypeAssignment
from services.field_registry import register_fields, register_module_events, register_module_config

router = APIRouter()

register_module_config("user_resources", "users", "user_id", "Admin")
register_module_events("user_resources", "user_resource_type", ["created", "updated", "deleted"])
register_fields("user_resources", "user_resource_type", [
    {"name": "resource_type_name", "type": "string", "label": "Resource Type", "description": "Name of the assigned resource type"},
    {"name": "cost_rate", "type": "number", "label": "Cost Rate", "description": "Hourly cost rate for this user in this resource type"},
])


@router.get("/users/{user_id}/resource-types")
async def get_user_resource_types(user_id: str, current_user=Depends(get_current_user)):
    """Get all resource types assigned to a user."""
    db = get_db()
    
    # Check permissions
    if not has_perm(current_user, "*") and current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    resource_assignments = user.get("resource_type_assignments", [])
    
    return {
        "user_id": user_id,
        "user_name": user.get("name"),
        "assignments": resource_assignments,
        "total": len(resource_assignments)
    }


@router.post("/users/{user_id}/resource-types")
async def assign_resource_type_to_user(
    user_id: str,
    assignment: UserResourceTypeAssignment,
    current_user=Depends(get_current_user)
):
    """Assign a resource type to a user with their cost rate."""
    if not has_perm(current_user, "*") and not has_perm(current_user, "users.manage"):
        raise HTTPException(status_code=403, detail="Only admins can assign resource types")
    
    db = get_db()
    
    # Get user
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get resource type name - first check default_resource_types collection
    # (The frontend sends name as the ID since default_resource_types uses name as key)
    resource_type_name = "Unknown"
    
    # First try to find in default_resource_types by name (since name is used as ID)
    default_rt = await db.default_resource_types.find_one(
        {"name": assignment.resource_type_id},
        {"_id": 0, "name": 1}
    )
    
    if default_rt:
        resource_type_name = default_rt.get("name", "Unknown")
    else:
        # Fallback: Look in contracts collection for the resource type by id
        sample_contract = await db.contracts.find_one(
            {"resource_types.id": assignment.resource_type_id},
            {"_id": 0, "resource_types": 1}
        )
        
        if sample_contract:
            for rt in sample_contract.get("resource_types", []):
                if rt.get("id") == assignment.resource_type_id:
                    resource_type_name = rt.get("name", "Unknown")
                    break
    
    # Get existing assignments
    assignments = user.get("resource_type_assignments", [])
    
    # Check if already assigned
    existing_index = None
    for i, a in enumerate(assignments):
        if a.get("resource_type_id") == assignment.resource_type_id:
            existing_index = i
            break
    
    new_assignment = {
        "resource_type_id": assignment.resource_type_id,
        "resource_type_name": resource_type_name,
        "cost_rate": assignment.cost_rate,
        "is_active": assignment.is_active,
        "assigned_at": datetime.now(timezone.utc),
        "assigned_by": current_user.user_id,
    }
    
    if existing_index is not None:
        # Update existing
        assignments[existing_index] = new_assignment
        action = "updated"
    else:
        # Add new
        assignments.append(new_assignment)
        action = "added"
    
    # Update user
    await db.users.update_one(
        {"user_id": user_id},
        {"$set": {"resource_type_assignments": assignments}}
    )
    
    return {
        "message": f"Resource type {action} successfully",
        "assignment": new_assignment
    }


@router.delete("/users/{user_id}/resource-types/{resource_type_id}")
async def remove_resource_type_from_user(
    user_id: str,
    resource_type_id: str,
    current_user=Depends(get_current_user)
):
    """Remove a resource type assignment from a user."""
    if not has_perm(current_user, "*") and not has_perm(current_user, "users.manage"):
        raise HTTPException(status_code=403, detail="Only admins can remove resource types")
    
    db = get_db()
    
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Filter out the resource type
    assignments = user.get("resource_type_assignments", [])
    new_assignments = [a for a in assignments if a.get("resource_type_id") != resource_type_id]
    
    if len(new_assignments) == len(assignments):
        raise HTTPException(status_code=404, detail="Resource type not assigned to this user")
    
    await db.users.update_one(
        {"user_id": user_id},
        {"$set": {"resource_type_assignments": new_assignments}}
    )
    
    return {"message": "Resource type removed successfully"}


@router.get("/resource-types/{resource_type_id}/users")
async def get_users_with_resource_type(
    resource_type_id: str,
    current_user=Depends(get_current_user)
):
    """Get all users who have a specific resource type assigned."""
    db = get_db()
    
    # Find all users with this resource type
    users = await db.users.find(
        {
            "resource_type_assignments.resource_type_id": resource_type_id,
            "is_active": True
        },
        {"_id": 0, "user_id": 1, "name": 1, "email": 1, "role": 1, "resource_type_assignments": 1}
    ).to_list(500)
    
    # Filter to only show the specific resource type assignment
    result = []
    for user in users:
        for assignment in user.get("resource_type_assignments", []):
            if assignment.get("resource_type_id") == resource_type_id and assignment.get("is_active", True):
                result.append({
                    "user_id": user["user_id"],
                    "name": user["name"],
                    "email": user.get("email"),
                    "role": user.get("role"),
                    "cost_rate": assignment.get("cost_rate"),
                    "assigned_at": assignment.get("assigned_at"),
                })
                break
    
    return {
        "resource_type_id": resource_type_id,
        "users": result,
        "total": len(result)
    }
