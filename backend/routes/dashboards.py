"""Dashboard Widget System - User-configurable dashboards with draggable widgets."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from typing import Optional
import uuid
import logging

from database import get_db
from auth_helper import get_current_user, has_perm
from services.audit_service import log_action
from services.field_registry import register_fields, register_module_events, register_module_config
from models import DashboardCreate, DashboardUpdate, DashboardWidget

logger = logging.getLogger(__name__)
router = APIRouter()

register_module_config("dashboards", "dashboards", "dashboard_id", "Admin")
register_module_events("dashboards", "dashboard", ["created", "updated", "deleted"])
register_fields("dashboards", "dashboard", [
    {"name": "name", "type": "string", "label": "Dashboard Name", "description": "Display name of the dashboard"},
    {"name": "is_default", "type": "boolean", "label": "Is Default", "description": "Whether this is the user's primary dashboard"},
    {"name": "order", "type": "number", "label": "Order", "description": "Display order in the dashboard list"},
])


@router.get("/dashboards")
async def get_user_dashboards(current_user=Depends(get_current_user)):
    """Get all dashboards for current user."""
    if not has_perm(current_user, "dashboards.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    dashboards = await db.dashboards.find(
        {"user_id": current_user.user_id},
        {"_id": 0}
    ).sort("order", 1).to_list(100)
    
    return {"items": dashboards, "total": len(dashboards)}


@router.post("/dashboards")
async def create_dashboard(data: DashboardCreate, current_user=Depends(get_current_user)):
    """Create a new dashboard."""
    if not has_perm(current_user, "dashboards.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    
    # If this is set as default, unset other defaults
    if data.is_default:
        await db.dashboards.update_many(
            {"user_id": current_user.user_id},
            {"$set": {"is_default": False}}
        )
    
    # Get next order number
    existing = await db.dashboards.find({"user_id": current_user.user_id}, {"_id": 0, "order": 1}).to_list(100)
    max_order = max([d.get("order", 0) for d in existing], default=-1)
    
    dashboard_id = f"dash_{uuid.uuid4().hex[:12]}"
    doc = {
        "dashboard_id": dashboard_id,
        "user_id": current_user.user_id,
        "name": data.name,
        "is_default": data.is_default,
        "widgets": [],
        "order": max_order + 1,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    await db.dashboards.insert_one(doc)
    doc.pop("_id", None)
    
    await log_action("dashboard.create", current_user.user_id, current_user.email, "dashboard", dashboard_id, {"name": doc["name"]})
    return doc


@router.get("/dashboards/{dashboard_id}")
async def get_dashboard(dashboard_id: str, current_user=Depends(get_current_user)):
    """Get dashboard details."""
    if not has_perm(current_user, "dashboards.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    dashboard = await db.dashboards.find_one(
        {"dashboard_id": dashboard_id, "user_id": current_user.user_id},
        {"_id": 0}
    )
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    
    return dashboard


@router.put("/dashboards/{dashboard_id}")
async def update_dashboard(dashboard_id: str, data: DashboardUpdate, current_user=Depends(get_current_user)):
    """Update dashboard (including widget positions)."""
    if not has_perm(current_user, "dashboards.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    
    dashboard = await db.dashboards.find_one(
        {"dashboard_id": dashboard_id, "user_id": current_user.user_id},
        {"_id": 0}
    )
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    
    update_doc = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
    
    # If setting as default, unset others
    if data.is_default:
        await db.dashboards.update_many(
            {"user_id": current_user.user_id, "dashboard_id": {"$ne": dashboard_id}},
            {"$set": {"is_default": False}}
        )
    
    update_doc["updated_at"] = datetime.now(timezone.utc)
    
    await db.dashboards.update_one(
        {"dashboard_id": dashboard_id},
        {"$set": update_doc}
    )
    
    return await db.dashboards.find_one({"dashboard_id": dashboard_id}, {"_id": 0})


@router.delete("/dashboards/{dashboard_id}")
async def delete_dashboard(dashboard_id: str, current_user=Depends(get_current_user)):
    """Delete a dashboard."""
    if not has_perm(current_user, "dashboards.delete"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    
    result = await db.dashboards.delete_one(
        {"dashboard_id": dashboard_id, "user_id": current_user.user_id}
    )
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    
    await log_action("dashboard.delete", current_user.user_id, current_user.email, "dashboard", dashboard_id)
    return {"message": "Dashboard deleted"}


@router.post("/dashboards/{dashboard_id}/widgets")
async def add_widget(dashboard_id: str, widget: DashboardWidget, current_user=Depends(get_current_user)):
    """Add a widget to dashboard."""
    if not has_perm(current_user, "dashboards.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    
    dashboard = await db.dashboards.find_one(
        {"dashboard_id": dashboard_id, "user_id": current_user.user_id},
        {"_id": 0}
    )
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    
    widget_id = f"widget_{uuid.uuid4().hex[:12]}"
    widget_doc = {**widget.model_dump(), "widget_id": widget_id}
    
    await db.dashboards.update_one(
        {"dashboard_id": dashboard_id},
        {"$push": {"widgets": widget_doc}, "$set": {"updated_at": datetime.now(timezone.utc)}}
    )
    
    return {"message": "Widget added", "widget_id": widget_id}


@router.delete("/dashboards/{dashboard_id}/widgets/{widget_id}")
async def remove_widget(dashboard_id: str, widget_id: str, current_user=Depends(get_current_user)):
    """Remove a widget from dashboard."""
    if not has_perm(current_user, "dashboards.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    
    await db.dashboards.update_one(
        {"dashboard_id": dashboard_id, "user_id": current_user.user_id},
        {"$pull": {"widgets": {"widget_id": widget_id}}, "$set": {"updated_at": datetime.now(timezone.utc)}}
    )
    
    return {"message": "Widget removed"}


@router.get("/widgets/available")
async def get_available_widgets(current_user=Depends(get_current_user)):
    """Get list of available widget types."""
    if not has_perm(current_user, "dashboards.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {
        "widgets": [
            {
                "type": "project_health",
                "name": "Project Health Overview",
                "description": "Summary of all projects by health status",
                "default_size": {"w": 4, "h": 2},
            },
            {
                "type": "time_summary",
                "name": "Time Tracking Summary",
                "description": "Weekly time entry statistics",
                "default_size": {"w": 4, "h": 2},
            },
            {
                "type": "budget_chart",
                "name": "Budget vs Actual",
                "description": "Project budget tracking chart",
                "default_size": {"w": 6, "h": 3},
            },
            {
                "type": "capacity_view",
                "name": "Team Capacity",
                "description": "Engineer and PM capacity overview",
                "default_size": {"w": 6, "h": 4},
            },
            {
                "type": "milestone_progress",
                "name": "Milestone Progress",
                "description": "Track milestone completion",
                "default_size": {"w": 4, "h": 3},
            },
            {
                "type": "task_list",
                "name": "My Tasks",
                "description": "Tasks assigned to you",
                "default_size": {"w": 4, "h": 4},
            },
            {
                "type": "recent_activity",
                "name": "Recent Activity",
                "description": "Latest project updates",
                "default_size": {"w": 4, "h": 3},
            },
        ]
    }
