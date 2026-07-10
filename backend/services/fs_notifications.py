"""
Field Services Notification System
- Day-before reminders to contractors and customers
- Job confirmation emails on status changes
- Arrival notification relay
- Scheduled via APScheduler
"""
import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta

import resend

logger = logging.getLogger(__name__)

resend.api_key = os.environ.get("RESEND_API_KEY", "")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "noreply@simplimeta.io")
APP_NAME = "AEGISAXIOM Field Services"


async def _send(to: str, subject: str, html: str) -> bool:
    if not resend.api_key or resend.api_key.startswith("re_placeholder"):
        logger.warning(f"[FS Email skipped - no key] To: {to} | Subject: {subject}")
        return True
    try:
        params = {"from": SENDER_EMAIL, "to": [to], "subject": subject, "html": html}
        await asyncio.to_thread(resend.Emails.send, params)
        logger.info(f"[FS Email sent] To: {to} | Subject: {subject}")
        return True
    except Exception as e:
        logger.error(f"[FS Email failed] To: {to} | Error: {e}")
        return False


def _base_template(content: str) -> str:
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#0F172A;color:#F1F5F9;padding:40px;border-radius:12px;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:24px;">
        <div style="width:32px;height:32px;background:linear-gradient(135deg,#7C3AED,#C026D3);border-radius:8px;display:flex;align-items:center;justify-content:center;">
          <span style="color:white;font-weight:bold;font-size:14px;">B</span>
        </div>
        <span style="color:white;font-weight:bold;font-size:18px;">AEGISAXIOM</span>
        <span style="color:#64748B;font-size:12px;margin-left:4px;">Field Services</span>
      </div>
      {content}
      <hr style="border:none;border-top:1px solid #1E293B;margin:24px 0;" />
      <p style="color:#475569;font-size:11px;">Powered by SimpliMeta · AegisAxiom</p>
    </div>"""


# ---- CONTRACTOR NOTIFICATIONS ----

async def send_contractor_day_before_reminder(
    to: str, contractor_name: str, job_name: str,
    account_name: str, scheduled_date: str, scheduled_start: str,
    scheduled_end: str, description: str = "", notes: str = ""
):
    """Day-before reminder to contractor about upcoming job."""
    content = f"""
      <h2 style="color:#F59E0B;font-size:20px;margin-bottom:8px;">Reminder: Job Tomorrow</h2>
      <p>Hi {contractor_name},</p>
      <p style="color:#94A3B8;">This is a reminder about your scheduled job tomorrow.</p>
      <div style="background:#1E293B;border-radius:8px;padding:20px;margin:20px 0;">
        <p style="color:#7C3AED;font-weight:bold;font-size:16px;margin:0 0 12px 0;">{job_name}</p>
        <table style="width:100%;color:#CBD5E1;font-size:14px;">
          <tr><td style="padding:4px 0;color:#64748B;">Customer</td><td style="padding:4px 0;font-weight:bold;">{account_name}</td></tr>
          <tr><td style="padding:4px 0;color:#64748B;">Date</td><td style="padding:4px 0;">{scheduled_date}</td></tr>
          <tr><td style="padding:4px 0;color:#64748B;">Time</td><td style="padding:4px 0;">{scheduled_start}{' – ' + scheduled_end if scheduled_end else ''}</td></tr>
        </table>
        {'<p style="color:#94A3B8;font-size:13px;margin:12px 0 0 0;">' + description + '</p>' if description else ''}
        {'<p style="color:#64748B;font-size:12px;margin:8px 0 0 0;">Notes: ' + notes + '</p>' if notes else ''}
      </div>
      <p style="color:#94A3B8;font-size:13px;">Please confirm your availability. If you have questions, contact your project manager.</p>
    """
    await _send(to, f"Reminder: {job_name} — Tomorrow", _base_template(content))


async def send_contractor_job_confirmed(
    to: str, contractor_name: str, job_name: str,
    account_name: str, scheduled_date: str, scheduled_start: str, scheduled_end: str,
    pm_name: str = "", pm_email: str = ""
):
    """Notify contractor that their job has been confirmed."""
    content = f"""
      <h2 style="color:#06B6D4;font-size:20px;margin-bottom:8px;">Job Confirmed</h2>
      <p>Hi {contractor_name},</p>
      <p style="color:#94A3B8;">Your job has been confirmed. Details below:</p>
      <div style="background:#1E293B;border-radius:8px;padding:20px;margin:20px 0;">
        <p style="color:#7C3AED;font-weight:bold;font-size:16px;margin:0 0 12px 0;">{job_name}</p>
        <table style="width:100%;color:#CBD5E1;font-size:14px;">
          <tr><td style="padding:4px 0;color:#64748B;">Customer</td><td style="padding:4px 0;font-weight:bold;">{account_name}</td></tr>
          <tr><td style="padding:4px 0;color:#64748B;">Date</td><td style="padding:4px 0;">{scheduled_date}</td></tr>
          <tr><td style="padding:4px 0;color:#64748B;">Time</td><td style="padding:4px 0;">{scheduled_start}{' – ' + scheduled_end if scheduled_end else ''}</td></tr>
          {'<tr><td style="padding:4px 0;color:#64748B;">Project Manager</td><td style="padding:4px 0;">' + pm_name + '</td></tr>' if pm_name else ''}
        </table>
      </div>
      {'<p style="color:#64748B;font-size:12px;">PM Contact: ' + pm_email + '</p>' if pm_email else ''}
    """
    await _send(to, f"Confirmed: {job_name} on {scheduled_date}", _base_template(content))


# ---- CUSTOMER NOTIFICATIONS ----

async def send_customer_day_before_reminder(
    to: str, contact_name: str, job_name: str,
    account_name: str, scheduled_date: str, scheduled_start: str,
    scheduled_end: str, tech_name: str = ""
):
    """Day-before reminder to customer about scheduled service."""
    content = f"""
      <h2 style="color:#7C3AED;font-size:20px;margin-bottom:8px;">Service Visit Tomorrow</h2>
      <p>Hi {contact_name},</p>
      <p style="color:#94A3B8;">We're writing to confirm your scheduled service visit tomorrow.</p>
      <div style="background:#1E293B;border-radius:8px;padding:20px;margin:20px 0;">
        <p style="color:#7C3AED;font-weight:bold;font-size:16px;margin:0 0 12px 0;">{job_name}</p>
        <table style="width:100%;color:#CBD5E1;font-size:14px;">
          <tr><td style="padding:4px 0;color:#64748B;">Date</td><td style="padding:4px 0;">{scheduled_date}</td></tr>
          <tr><td style="padding:4px 0;color:#64748B;">Arrival Window</td><td style="padding:4px 0;">{scheduled_start}{' – ' + scheduled_end if scheduled_end else ''}</td></tr>
          {'<tr><td style="padding:4px 0;color:#64748B;">Technician</td><td style="padding:4px 0;">' + tech_name + '</td></tr>' if tech_name else ''}
        </table>
      </div>
      <p style="color:#94A3B8;font-size:13px;">Our technician will arrive during the scheduled window. If you need to reschedule, please contact us as soon as possible.</p>
    """
    await _send(to, f"Service Reminder: {job_name} — Tomorrow", _base_template(content))


async def send_customer_job_confirmed(
    to: str, contact_name: str, job_name: str,
    account_name: str, scheduled_date: str, scheduled_start: str, scheduled_end: str
):
    """Notify customer that their service job is confirmed."""
    content = f"""
      <h2 style="color:#10B981;font-size:20px;margin-bottom:8px;">Service Confirmed</h2>
      <p>Hi {contact_name},</p>
      <p style="color:#94A3B8;">Your service appointment has been confirmed.</p>
      <div style="background:#1E293B;border-radius:8px;padding:20px;margin:20px 0;">
        <p style="color:#7C3AED;font-weight:bold;font-size:16px;margin:0 0 12px 0;">{job_name}</p>
        <table style="width:100%;color:#CBD5E1;font-size:14px;">
          <tr><td style="padding:4px 0;color:#64748B;">Date</td><td style="padding:4px 0;">{scheduled_date}</td></tr>
          <tr><td style="padding:4px 0;color:#64748B;">Time</td><td style="padding:4px 0;">{scheduled_start}{' – ' + scheduled_end if scheduled_end else ''}</td></tr>
        </table>
      </div>
      <p style="color:#94A3B8;font-size:13px;">We look forward to serving you. You will receive another reminder the day before your appointment.</p>
    """
    await _send(to, f"Service Confirmed: {job_name} on {scheduled_date}", _base_template(content))


# ---- ARRIVAL NOTIFICATIONS ----

async def send_customer_arrival_notification(
    to: str, contact_name: str, job_name: str, tech_name: str
):
    """Notify customer that technician has arrived / is on the way."""
    content = f"""
      <h2 style="color:#10B981;font-size:20px;margin-bottom:8px;">Your Technician Has Arrived</h2>
      <p>Hi {contact_name},</p>
      <p style="color:#94A3B8;"><strong style="color:#F1F5F9;">{tech_name}</strong> has arrived on-site for:</p>
      <div style="background:#1E293B;border-radius:8px;padding:16px;margin:16px 0;">
        <p style="color:#7C3AED;font-weight:bold;font-size:15px;margin:0;">{job_name}</p>
      </div>
      <p style="color:#94A3B8;font-size:13px;">Work will begin shortly. You'll be notified when the job is complete.</p>
    """
    await _send(to, f"Technician Arrived: {job_name}", _base_template(content))


async def send_customer_job_completed(
    to: str, contact_name: str, job_name: str, account_name: str
):
    """Notify customer that the job is complete."""
    content = f"""
      <h2 style="color:#10B981;font-size:20px;margin-bottom:8px;">Service Complete</h2>
      <p>Hi {contact_name},</p>
      <p style="color:#94A3B8;">We're happy to let you know that the following service has been completed:</p>
      <div style="background:#1E293B;border-radius:8px;padding:16px;margin:16px 0;">
        <p style="color:#7C3AED;font-weight:bold;font-size:15px;margin:0;">{job_name}</p>
        <p style="color:#94A3B8;font-size:13px;margin:4px 0 0 0;">{account_name}</p>
      </div>
      <p style="color:#94A3B8;font-size:13px;">Thank you for choosing SimpliMeta. If you have any questions about the work performed, please don't hesitate to reach out.</p>
    """
    await _send(to, f"Service Complete: {job_name}", _base_template(content))


# ---- SCHEDULED JOB: DAY-BEFORE REMINDERS ----

async def run_day_before_reminders():
    """Run by APScheduler daily — sends reminders for tomorrow's jobs."""
    from database import get_db
    db = get_db()

    tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    jobs = await db.field_jobs.find(
        {"scheduled_date": tomorrow, "status": {"$in": ["scheduled", "confirmed"]}},
        {"_id": 0}
    ).to_list(200)

    logger.info(f"[FS Reminders] Found {len(jobs)} jobs scheduled for {tomorrow}")

    sent_count = 0
    for job in jobs:
        # Contractor reminder
        if job.get("assigned_tech_id"):
            tech = await db.users.find_one({"user_id": job["assigned_tech_id"]}, {"_id": 0, "email": 1, "name": 1})
            if tech and tech.get("email"):
                await send_contractor_day_before_reminder(
                    to=tech["email"],
                    contractor_name=tech.get("name", ""),
                    job_name=job.get("name", ""),
                    account_name=job.get("account_name", ""),
                    scheduled_date=job.get("scheduled_date", ""),
                    scheduled_start=job.get("scheduled_start", ""),
                    scheduled_end=job.get("scheduled_end", ""),
                    description=job.get("description", ""),
                    notes=job.get("notes", "")
                )
                sent_count += 1

        # Customer reminder
        if job.get("customer_contact_email"):
            await send_customer_day_before_reminder(
                to=job["customer_contact_email"],
                contact_name=job.get("customer_contact_name", "Customer"),
                job_name=job.get("name", ""),
                account_name=job.get("account_name", ""),
                scheduled_date=job.get("scheduled_date", ""),
                scheduled_start=job.get("scheduled_start", ""),
                scheduled_end=job.get("scheduled_end", ""),
                tech_name=job.get("assigned_tech_name", "")
            )
            sent_count += 1

    logger.info(f"[FS Reminders] Sent {sent_count} reminder emails for {tomorrow}")

    # Log notification history
    if sent_count > 0:
        await db.notification_log.insert_one({
            "type": "day_before_reminders",
            "date": tomorrow,
            "jobs_count": len(jobs),
            "emails_sent": sent_count,
            "timestamp": datetime.now(timezone.utc)
        })

    return sent_count
