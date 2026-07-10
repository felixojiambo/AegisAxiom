import os
import asyncio
import logging
import resend

logger = logging.getLogger(__name__)

resend.api_key = os.environ.get("RESEND_API_KEY", "")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "onboarding@resend.dev")


def is_configured() -> bool:
    """True when a real Resend API key is configured in the runtime
    environment. Callers that need to surface a clear
    "not configured" result (instead of the legacy silent-success
    of ``_send``) should consult this before sending."""
    key = resend.api_key
    return bool(key) and not key.startswith("re_placeholder")


async def _send(to: str, subject: str, html: str) -> bool:
    if not is_configured():
        logger.warning(f"[Email skipped - no key] To: {to} | Subject: {subject}")
        return True
    try:
        params = {"from": SENDER_EMAIL, "to": [to], "subject": subject, "html": html}
        await asyncio.to_thread(resend.Emails.send, params)
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to}: {e}")
        return False


async def send_with_attachments(
    to,
    subject: str,
    html: str,
    *,
    attachments: list | None = None,
) -> dict:
    """Send an email with optional attachments. Distinct from
    ``_send`` because it returns a structured result so callers can
    distinguish "delivered" from "not configured" from "failed" —
    the legacy ``_send`` collapses all three into True/False.

    ``to`` may be a single address string or a list of strings.
    ``attachments`` follows Resend's shape:
    ``[{"filename": "x.pdf", "content": <base64-str>}]``. Never log
    the attachment ``content`` body — it can contain PII.

    Returns::
        {
          "status": "sent" | "not_configured" | "failed",
          "error":  Optional[str],   # sanitized; never includes
                                     #  attachment bytes or secrets
          "recipients": list[str],
        }
    """
    recipients = [to] if isinstance(to, str) else list(to or [])
    safe_subject = subject or "(no subject)"

    if not is_configured():
        logger.warning(
            "[Email skipped - no key] recipients=%s subject=%r",
            len(recipients), safe_subject,
        )
        return {"status": "not_configured", "error": None, "recipients": recipients}

    params: dict = {
        "from": SENDER_EMAIL,
        "to": recipients,
        "subject": safe_subject,
        "html": html,
    }
    if attachments:
        params["attachments"] = list(attachments)

    try:
        await asyncio.to_thread(resend.Emails.send, params)
        return {"status": "sent", "error": None, "recipients": recipients}
    except Exception as exc:  # noqa: BLE001
        # Sanitize the error before bubbling out — never include the
        # attachment bytes, the HTML body, or the SMTP/API secret.
        sanitized = f"{type(exc).__name__}: send failed"
        logger.error(
            "send_with_attachments failed (recipients=%d): %s",
            len(recipients), exc,
        )
        return {"status": "failed", "error": sanitized, "recipients": recipients}


async def send_invite_email(to: str, name: str, invite_link: str, role: str, invited_by: str):
    role_display = role.replace("_", " ").title()
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#0F172A;color:#F1F5F9;padding:40px;border-radius:12px;">
      <h1 style="color:#7C3AED;font-size:24px;">Welcome to AEGISAXIOM</h1>
      <p>Hi {name},</p>
      <p>You've been invited by <strong>{invited_by}</strong> to join as a <strong style="color:#7C3AED;">{role_display}</strong>.</p>
      <div style="text-align:center;margin:32px 0;">
        <a href="{invite_link}" style="background:#7C3AED;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:bold;">Accept Invitation</a>
      </div>
      <p style="color:#64748B;font-size:13px;">Expires in 48 hours. Powered by SimpliMeta.</p>
    </div>"""
    await _send(to, "You're invited to AEGISAXIOM — SimpliMeta", html)


async def send_partner_approval_email(to: str, name: str, invite_link: str):
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#0F172A;color:#F1F5F9;padding:40px;border-radius:12px;">
      <h1 style="color:#10B981;">Partner Application Approved!</h1>
      <p>Hi {name}, congratulations! Set up your partner account below.</p>
      <div style="text-align:center;margin:32px 0;">
        <a href="{invite_link}" style="background:#10B981;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:bold;">Set Up Account</a>
      </div>
    </div>"""
    await _send(to, "Partner Application Approved — SimpliMeta", html)


async def send_password_reset_email(to: str, name: str, reset_link: str):
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#0F172A;color:#F1F5F9;padding:40px;border-radius:12px;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
        <div style="width:32px;height:32px;background:linear-gradient(135deg,#7C3AED,#C026D3);border-radius:8px;display:flex;align-items:center;justify-content:center;">
          <span style="color:white;font-weight:bold;font-size:14px;">B</span>
        </div>
        <span style="color:white;font-weight:bold;font-size:18px;">AEGISAXIOM</span>
      </div>
      <p style="color:#64748B;font-size:12px;margin-bottom:32px;">by SimpliMeta</p>
      <h1 style="color:#7C3AED;font-size:22px;margin-bottom:8px;">Reset Your Password</h1>
      <p>Hi {name},</p>
      <p style="color:#94A3B8;">We received a request to reset your password. Click the button below to set a new password. This link expires in <strong style="color:#F1F5F9;">1 hour</strong>.</p>
      <div style="text-align:center;margin:32px 0;">
        <a href="{reset_link}" style="background:#7C3AED;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:15px;">Reset Password</a>
      </div>
      <p style="color:#64748B;font-size:12px;">If you didn't request a password reset, you can safely ignore this email. Your password will not change.</p>
      <hr style="border:none;border-top:1px solid #1E293B;margin:24px 0;" />
      <p style="color:#475569;font-size:11px;">Powered by SimpliMeta · AegisAxiom</p>
    </div>"""
    await _send(to, "Reset your AEGISAXIOM password — SimpliMeta", html)


async def send_partner_rejection_email(to: str, name: str, reason: str):
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#0F172A;color:#F1F5F9;padding:40px;border-radius:12px;">
      <h1 style="color:#EF4444;">Application Status Update</h1>
      <p>Hi {name}, thank you for applying. We're unable to approve your application at this time.</p>
      <p style="color:#94A3B8;">Reason: {reason}</p>
      <p style="color:#64748B;font-size:13px;">Questions? Contact partners@simplimeta.io</p>
    </div>"""
    await _send(to, "Your Partner Application Status — SimpliMeta", html)


async def send_password_change_email(to: str, name: str, reset_link: str):
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#0F172A;color:#F1F5F9;padding:40px;border-radius:12px;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
        <div style="width:32px;height:32px;background:linear-gradient(135deg,#7C3AED,#C026D3);border-radius:8px;display:flex;align-items:center;justify-content:center;">
          <span style="color:white;font-weight:bold;font-size:14px;">B</span>
        </div>
        <span style="color:white;font-weight:bold;font-size:18px;">AEGISAXIOM</span>
      </div>
      <p style="color:#64748B;font-size:12px;margin-bottom:32px;">by SimpliMeta</p>
      <h1 style="color:#7C3AED;font-size:22px;margin-bottom:8px;">Set or Change Your Password</h1>
      <p>Hi {name},</p>
      <p style="color:#94A3B8;">You requested to set or change your password. Click the button below to proceed. This link expires in <strong style="color:#F1F5F9;">1 hour</strong>.</p>
      <div style="text-align:center;margin:32px 0;">
        <a href="{reset_link}" style="background:#7C3AED;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:15px;">Set Password</a>
      </div>
      <p style="color:#64748B;font-size:12px;">If you didn't request this, you can safely ignore this email.</p>
      <hr style="border:none;border-top:1px solid #1E293B;margin:24px 0;" />
      <p style="color:#475569;font-size:11px;">Powered by SimpliMeta · AegisAxiom</p>
    </div>"""
    await _send(to, "Set or change your AEGISAXIOM password — SimpliMeta", html)
