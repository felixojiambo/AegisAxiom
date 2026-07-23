"""
User Resource Type Management
Endpoints for admins to assign/remove resource types from users with cost rates.

Phase 2a: user resource-type assignments live in the Postgres
``user_resource_type_assignments`` child table (via the users repository). The
resource-type NAME resolution still reads Mongo (default_resource_types /
contracts) — those collections are unmigrated.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone

from database import get_db
from db.session import get_session
from repositories.users import UserRepository
from auth_helper import get_current_user, has_perm
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
async def get_user_resource_types(
    user_id: str,
    current_user=Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    """Get all resource types assigned to a user."""
    # Check permissions
    if not has_perm(current_user, "*") and current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    repo = UserRepository(pg)
    user = await repo.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    resource_assignments = await repo.list_user_resource_assignments(user_id)

    return {
        "user_id": user_id,
        "user_name": user.name,
        "assignments": resource_assignments,
        "total": len(resource_assignments)
    }


@router.post("/users/{user_id}/resource-types")
async def assign_resource_type_to_user(
    user_id: str,
    assignment: UserResourceTypeAssignment,
    current_user=Depends(get_current_user),
    db=Depends(get_db),
    pg: AsyncSession = Depends(get_session),
):
    """Assign a resource type to a user with their cost rate."""
    if not has_perm(current_user, "*") and not has_perm(current_user, "users.manage"):
        raise HTTPException(status_code=403, detail="Only admins can assign resource types")

    repo = UserRepository(pg)
    user = await repo.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get resource type name - first check default_resource_types collection
    # (The frontend sends name as the ID since default_resource_types uses name as key)
    resource_type_name = "Unknown"

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

    # Upsert the assignment (insert or replace) in the child table.
    existing = await repo.get_resource_assignment(user_id, assignment.resource_type_id)
    action = "updated" if existing is not None else "added"
    new_assignment = await repo.upsert_resource_assignment(
        user_id,
        assignment.resource_type_id,
        {
            "resource_type_name": resource_type_name,
            "cost_rate": assignment.cost_rate,
            "is_active": assignment.is_active,
            "assigned_at": datetime.now(timezone.utc),
            "assigned_by": current_user.user_id,
        },
    )

    return {
        "message": f"Resource type {action} successfully",
        "assignment": new_assignment
    }


@router.delete("/users/{user_id}/resource-types/{resource_type_id}")
async def remove_resource_type_from_user(
    user_id: str,
    resource_type_id: str,
    current_user=Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    """Remove a resource type assignment from a user."""
    if not has_perm(current_user, "*") and not has_perm(current_user, "users.manage"):
        raise HTTPException(status_code=403, detail="Only admins can remove resource types")

    repo = UserRepository(pg)
    user = await repo.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    removed = await repo.delete_resource_assignment(user_id, resource_type_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Resource type not assigned to this user")

    return {"message": "Resource type removed successfully"}


@router.get("/resource-types/{resource_type_id}/users")
async def get_users_with_resource_type(
    resource_type_id: str,
    current_user=Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    """Get all users who have a specific resource type assigned."""
    result = await UserRepository(pg).list_users_by_resource_type(resource_type_id)
    return {
        "resource_type_id": resource_type_id,
        "users": result,
        "total": len(result)
    }
