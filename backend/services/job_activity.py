"""
Job Activity Tracker — logs all activity on a job (status changes, emails, edits, notes).
Also handles notification settings (which auto-notifications are enabled).
"""
import uuid
from datetime import datetime, timezone
from database import get_db

# ---- DEFAULT NOTIFICATION SETTINGS ----
DEFAULT_NOTIFICATION_SETTINGS = {
    "settings_id": "fs_notifications",
    "day_before_contractor_reminder": {"enabled": True, "label": "Day-Before Contractor Reminder", "description": "Email reminder to contractor the day before a scheduled job", "category": "reminders"},
    "day_before_customer_reminder": {"enabled": True, "label": "Day-Before Customer Reminder", "description": "Email reminder to customer the day before a scheduled service visit", "category": "reminders"},
    "on_confirmed_contractor": {"enabled": True, "label": "Job Confirmed → Contractor Email", "description": "Email contractor when job status moves to confirmed", "category": "status_change"},
    "on_confirmed_customer": {"enabled": True, "label": "Job Confirmed → Customer Email", "description": "Email customer when job status moves to confirmed", "category": "status_change"},
    "on_arrival_customer": {"enabled": True, "label": "Tech Arrived → Customer Email", "description": "Email customer when technician marks arrival (in_progress)", "category": "status_change"},
    "on_completed_customer": {"enabled": True, "label": "Job Complete → Customer Email", "description": "Email customer when job is completed", "category": "status_change"},
}


async def get_notification_settings() -> dict:
    db = get_db()
    doc = await db.fs_notification_settings.find_one({"settings_id": "fs_notifications"}, {"_id": 0})
    if not doc:
        return DEFAULT_NOTIFICATION_SETTINGS
    return doc


async def is_notification_enabled(key: str) -> bool:
    settings = await get_notification_settings()
    setting = settings.get(key)
    if isinstance(setting, dict):
        return setting.get("enabled", True)
    return True  # default enabled if not found


async def log_job_activity(
    job_id: str,
    activity_type: str,
    title: str,
    detail: str = "",
    user_id: str = None,
    user_name: str = None,
    metadata: dict = None
):
    """Log an activity entry for a job."""
    db = get_db()
    entry = {
        "activity_id": f"act_{uuid.uuid4().hex[:12]}",
        "job_id": job_id,
        "type": activity_type,  # status_change, email_sent, edit, note, check_in, check_out, photo_upload
        "title": title,
        "detail": detail,
        "user_id": user_id,
        "user_name": user_name,
        "metadata": metadata or {},
        "timestamp": datetime.now(timezone.utc)
    }
    await db.job_activity.insert_one(entry)
    return entry


async def get_job_activity(job_id: str, limit: int = 100) -> list:
    """Get activity log for a job, newest first."""
    db = get_db()
    docs = await db.job_activity.find(
        {"job_id": job_id}, {"_id": 0}
    ).sort("timestamp", -1).limit(limit).to_list(limit)
    return docs
