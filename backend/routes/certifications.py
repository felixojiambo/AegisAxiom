"""Certification endpoints — view user certifications."""
from fastapi import APIRouter, Depends
from database import get_db
from auth_helper import get_current_user
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/users/me/certifications")
async def get_my_certifications(current_user=Depends(get_current_user)):
    """Get the current user's certifications."""
    db = get_db()
    certs = await db.certifications.find(
        {"user_id": current_user.user_id},
        {"_id": 0},
    ).sort("issued_at", -1).to_list(100)
    return certs
