"""
Ingestion event listeners — registered at import time.

Two listeners ship by default:

1. Forensic-trail observer (`*`): logs every emitted event at INFO so the
   server log is a single source of truth for what crud_envelope did,
   independent of policy/audit infrastructure being healthy.

2. Automation bridge (`*.*.status_changed`): translates the ingestion Event
   shape into the legacy workflow_engine's record_action/field_change shape
   and dispatches. This is what fulfills the "automation rule listening to
   status_changed fires" acceptance criterion without requiring ingested
   apps to opt into a new automation engine.

Listener registration happens at module import time. server.py imports this
module once during startup so registration is idempotent and ordered.
"""

import logging

from services.event_bus import Event, register_listener

logger = logging.getLogger(__name__)


async def _log_event(event: Event) -> None:
    logger.info(
        "ingestion_event name=%s resource_id=%s actor=%s request_id=%s",
        event["name"],
        event["resource_id"],
        event["actor_email"],
        event["request_id"],
    )


async def _dispatch_status_change_to_workflow_engine(event: Event) -> None:
    """Bridge to the legacy workflow_engine for status_changed events.

    The legacy engine expects a different event shape (module/action/record/
    changed_fields). We synthesize that here so existing workflow_rules can
    trigger off ingested-entity status changes without engine changes.
    """
    from services.workflow_engine import handle_event as _legacy_handle

    payload = event.get("payload") or {}
    previous = event.get("previous_state") or {}

    legacy_event = {
        "module": event["module"],
        "entity": event["entity"],
        "action": "updated",
        "record": payload,
        "old_record": previous,
        "changed_fields": {
            "status": {
                "old": previous.get("status"),
                "new": payload.get("status"),
            }
        },
        "user_id": event.get("actor_id"),
        "user_email": event.get("actor_email"),
        "timestamp": event["timestamp"].isoformat() if hasattr(event["timestamp"], "isoformat") else event["timestamp"],
        "chain_depth": 0,
        "request_id": event.get("request_id"),
    }

    await _legacy_handle(legacy_event)


register_listener("*", _log_event)
register_listener("*.*.status_changed", _dispatch_status_change_to_workflow_engine)
