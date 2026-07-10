from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
import uuid

from database import get_db
from models import FieldJob, FieldJobCreate, FieldJobUpdate
from auth_helper import get_current_user, get_job_filter, has_perm

router = APIRouter()


# ---- FIELD SERVICE JOBS ----

@router.get("/jobs")
async def get_jobs(skip: int = 0, limit: int = 100, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "field_jobs.view.all") and not has_perm(current_user, "field_jobs.view.own"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    filt = await get_job_filter(current_user)
    docs = await db.field_jobs.find(filt, {"_id": 0}).sort("created_at", -1).skip(skip).limit(min(limit, 500)).to_list(min(limit, 500))
    total = await db.field_jobs.count_documents(filt)
    return {"items": docs, "total": total, "skip": skip, "limit": limit}


@router.post("/jobs")
async def create_job(data: FieldJobCreate, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "field_jobs.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    account = await db.accounts.find_one({"account_id": data.account_id}, {"_id": 0, "name": 1, "assigned_sales_rep_id": 1})
    tech_name = None
    if data.assigned_tech_id:
        tech = await db.users.find_one({"user_id": data.assigned_tech_id}, {"_id": 0, "name": 1})
        tech_name = tech["name"] if tech else None

    job_id = f"job_{uuid.uuid4().hex[:12]}"
    doc = {
        "job_id": job_id,
        **data.model_dump(),
        "account_name": account["name"] if account else None,
        "assigned_tech_name": tech_name,
        "assigned_sales_rep_id": account.get("assigned_sales_rep_id") if account else None,
        "total_cost": data.labor_cost + data.material_cost,
        "status": "scheduled",
        "created_at": datetime.now(timezone.utc),
    }
    await db.field_jobs.insert_one(doc)
    return FieldJob(**doc)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, current_user=Depends(get_current_user)):
    # SECURITY FIX C-8: Apply RBAC filter to prevent direct ID access bypass
    db = get_db()
    rbac_filter = await get_job_filter(current_user)
    query = {"job_id": job_id}
    
    # Combine RBAC filter with ID lookup
    if rbac_filter and rbac_filter != {}:
        query = {"$and": [{"job_id": job_id}, rbac_filter]}
    
    doc = await db.field_jobs.find_one(query, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Job not found")
    return doc


@router.put("/jobs/{job_id}")
async def update_job(job_id: str, data: FieldJobUpdate, current_user=Depends(get_current_user)):
    if not has_perm(current_user, "field_jobs.edit"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    if "labor_cost" in update_data or "material_cost" in update_data:
        job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
        labor = update_data.get("labor_cost", job.get("labor_cost", 0))
        material = update_data.get("material_cost", job.get("material_cost", 0))
        update_data["total_cost"] = labor + material
    await db.field_jobs.update_one({"job_id": job_id}, {"$set": update_data})
    return await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
