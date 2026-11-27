"""
Security audit logging for RBAC events.

Writes into the unified audit_logs table (Phase 2b: PostgreSQL, the same store
used by services.audit_service.log_action) so security events show up
side-by-side with activity events in the admin Audit Log UI. The
event_category field distinguishes the two streams: "security" for
permission denials, auth failures, role changes, and AI access shifts;
"activity" for general resource CRUD events emitted by audit_service.

Public signature unchanged from the pre-unification version, so all
existing call sites work without modification.

Does NOT log sensitive content (transcripts, passwords, tokens, PII).
"""
import logging
import uuid
from datetime import datetime, timezone

from services.audit_service import write_audit_entry

logger = logging.getLogger(__name__)


async def log_security_event(
    event_type: str,
    severity: str = "info",
    user_id: str = None,
    user_email: str = None,
    resource: str = None,
    resource_id: str = None,
    action: str = None,
    outcome: str = "success",
    details: dict = None,
):
    """Record a security event in the unified audit log.

    Args:
        event_type: auth_failed, authz_failed, role_changed, user_created,
                    user_deleted, user_deactivated, ai_employee_created,
                    ai_employee_deleted, ai_access_changed, session_approved,
                    data_exported, view_as_used, password_reset, invite_sent
        severity: info, warning, critical
        user_id: Actor's user ID (who performed the action)
        user_email: Actor's email
        resource: Resource type affected (user, role, ai_employee, etc.)
        resource_id: Specific resource ID
        action: What was done (create, update, delete, login_failed, etc.)
        outcome: success, denied, error
        details: Additional context (NO sensitive data -- no passwords, tokens, PII)

    Field naming is normalized to match audit_logs schema:
        user_id    -> actor_id
        user_email -> actor_email
        resource   -> resource_type
    The original argument names are preserved on this function so call
    sites don't have to change.
    """
    entry = {
        "audit_id": f"audit_{uuid.uuid4().hex[:16]}",
        "event_category": "security",
        "event_type": event_type,
        "severity": severity,
        "actor_id": user_id,
        "actor_email": user_email,
        "resource_type": resource,
        "resource_id": resource_id,
        "action": action,
        "outcome": outcome,
        "details": details or {},
        "timestamp": datetime.now(timezone.utc),
    }
    try:
        await write_audit_entry(entry)
    except Exception as e:
        logger.error(f"Failed to write security audit log: {e}")
