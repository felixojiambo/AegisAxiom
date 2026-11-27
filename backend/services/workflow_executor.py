"""
Workflow Executor — Executes the 7 action types for matched workflow rules.

Template variable interpolation supports {{record.field}} syntax in all action params.
"""

import re
import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from database import get_db
from db.migrated_collections import migrated_in
from services.email_service import _send as send_email_raw
from services.field_registry import get_module_config

logger = logging.getLogger(__name__)


# ==================== TEMPLATE INTERPOLATION ====================

def interpolate(value: Any, context: dict) -> Any:
    """Replace {{path.to.field}} patterns with actual values from context.

    Special token: {{uuid}} is replaced with a fresh 12-char hex UUID on each
    match. Lets seeded rules mint IDs whose prefix matches the owning route
    (e.g. procnotif_<hex>) instead of relying on the executor's target_module[:3]
    fallback.
    """
    if isinstance(value, str):
        def replacer(match):
            path = match.group(1)
            if path == "uuid":
                return uuid.uuid4().hex[:12]
            parts = path.split(".")
            obj = context
            for part in parts:
                if isinstance(obj, dict):
                    obj = obj.get(part)
                else:
                    return match.group(0)
                if obj is None:
                    return ""
            return str(obj) if obj is not None else ""
        return re.sub(r"\{\{(\w+(?:\.\w+)*)\}\}", replacer, value)
    elif isinstance(value, dict):
        return {k: interpolate(v, context) for k, v in value.items()}
    elif isinstance(value, list):
        return [interpolate(v, context) for v in value]
    return value


def _build_context(event: dict, rule: dict) -> dict:
    """Build the context object for template interpolation."""
    return {
        "record": event.get("record", {}),
        "old_record": event.get("old_record") or {},
        "user": {
            "id": event.get("user_id"),
            "email": event.get("user_email"),
        },
        "timestamp": event.get("timestamp"),
        "rule": {
            "name": rule.get("name"),
            "id": rule.get("rule_id"),
            "module": rule.get("module"),
        },
    }


# ==================== COLLECTION HELPERS ====================

# Migrated-collection guard. The write actions below (update_field /
# create_record / delete_record) address records as db[collection] on Mongo.
# field_registry maps several modules onto collections that now live in
# PostgreSQL (e.g. `roles`, `audit`, `admin` -> users, `tenants`), so an action
# targeting one would silently write a dead Mongo copy. Refuse instead; the
# executor records the action as failed with this message. The set comes from
# the single registry (db/migrated_collections.json), so every future phase is
# covered the moment it registers its collections. (Phase 2b census,
# dynamic-path trace; harness retro 2026-11-27.)


def _get_collection(module: str) -> str:
    config = get_module_config(module)
    collection = config.get("collection", module) if config else module
    phase = migrated_in(collection)
    if phase is not None:
        raise ValueError(
            f"Workflow actions cannot target module '{module}': its collection "
            f"'{collection}' is on PostgreSQL (Phase {phase}) and is not writable "
            f"through workflow record actions."
        )
    return collection


def _get_id_field(module: str) -> str:
    config = get_module_config(module)
    return config.get("id_field", "id") if config else "id"


# ==================== MAIN EXECUTOR ====================

async def execute_actions(rule: dict, event: dict, db, chain_depth: int) -> list:
    """Execute all instant + scheduled actions for a matched rule."""
    context = _build_context(event, rule)
    context["_chain_depth"] = chain_depth
    results = []

    # Instant actions
    for action in rule.get("instant_actions", []):
        result = await _execute_single(action, context, db, chain_depth, event)
        results.append(result)

    # Scheduled (delayed) actions
    for action in rule.get("scheduled_actions", []):
        delay = _calc_delay_seconds(action)
        if delay > 0:
            asyncio.get_running_loop().call_later(
                delay,
                lambda a=action, c=dict(context), d=db, cd=chain_depth, e=event: (
                    asyncio.create_task(_execute_single(a, c, d, cd, e))
                ),
            )
            results.append({"type": action.get("type"), "status": "scheduled", "delay_seconds": delay})
        else:
            result = await _execute_single(action, context, db, chain_depth, event)
            results.append(result)

    return results


def _calc_delay_seconds(action: dict) -> int:
    value = action.get("delay_value", 0)
    unit = action.get("delay_unit", "minutes")
    if not value:
        return 0
    multipliers = {"minutes": 60, "hours": 3600, "days": 86400}
    return int(value * multipliers.get(unit, 60))


async def _execute_single(
    action: dict, context: dict, db, chain_depth: int, event: dict,
) -> dict:
    """Execute a single action and return its result."""
    action_type = action.get("type", "")
    started = time.time()

    try:
        handlers = {
            "update_field": _action_update_field,
            "create_record": _action_create_record,
            "send_notification": _action_send_notification,
            "send_email": _action_send_email,
            "trigger_webhook": _action_trigger_webhook,
            "trigger_workflow": _action_trigger_workflow,
            "delete_record": _action_delete_record,
        }

        handler = handlers.get(action_type)
        if not handler:
            return {
                "type": action_type, "status": "failed",
                "error": f"Unknown action type: {action_type}", "duration_ms": 0,
            }

        if action_type in ("trigger_workflow",):
            await handler(action, context, event, chain_depth)
        elif action_type in ("send_email", "trigger_webhook"):
            await handler(action, context)
        else:
            await handler(action, context, db)

        duration = int((time.time() - started) * 1000)
        return {"type": action_type, "status": "success", "duration_ms": duration}

    except Exception as e:
        duration = int((time.time() - started) * 1000)
        logger.error(f"Action {action_type} failed: {e}", exc_info=True)
        return {"type": action_type, "status": "failed", "error": str(e), "duration_ms": duration}


# ==================== ACTION HANDLERS ====================

async def _action_update_field(action: dict, context: dict, db) -> None:
    """Update a field on a record."""
    target_module = action.get("module", "")
    if not target_module:
        raise ValueError("update_field requires 'module'")

    collection = _get_collection(target_module)
    id_field = _get_id_field(target_module)
    record = context.get("record", {})
    record_id = record.get(id_field)

    if not record_id:
        raise ValueError(f"Cannot update: record has no '{id_field}'")

    field = interpolate(action.get("field", ""), context)
    value = interpolate(action.get("value"), context)

    await db[collection].update_one(
        {id_field: record_id},
        {"$set": {field: value, "updated_at": datetime.now(timezone.utc)}},
    )
    logger.info(f"Workflow: updated {collection}.{record_id}.{field}")


async def _action_create_record(action: dict, context: dict, db) -> None:
    """Create a new record in the target module.

    Optional `dedup_on`: list of field names. If present, and a record already
    exists whose values for all those fields match the interpolated new_record,
    the insert is skipped. Keeps seeded rules idempotent on re-entry events.
    """
    target_module = action.get("module", "")
    if not target_module:
        raise ValueError("create_record requires 'module'")

    collection = _get_collection(target_module)
    id_field = _get_id_field(target_module)

    fields_template = action.get("fields", {})
    new_record = interpolate(fields_template, context)

    dedup_on = action.get("dedup_on") or []
    if dedup_on:
        dedup_filter = {f: new_record.get(f) for f in dedup_on}
        existing = await db[collection].find_one(dedup_filter, {"_id": 1})
        if existing:
            logger.info(
                f"Workflow: skipped create on {collection} — dedup match on {dedup_on}"
            )
            return

    if id_field not in new_record:
        prefix = target_module[:3]
        new_record[id_field] = f"{prefix}_{uuid.uuid4().hex[:12]}"

    new_record["created_at"] = datetime.now(timezone.utc)
    new_record["updated_at"] = datetime.now(timezone.utc)
    new_record["_created_by_workflow"] = True

    await db[collection].insert_one(new_record)
    new_record.pop("_id", None)
    logger.info(f"Workflow: created {collection}/{new_record.get(id_field)}")

    # Emit event for chaining
    from services.event_bus import emit_event
    await emit_event(
        module=target_module,
        action="created",
        record=new_record,
        user_id=context.get("user", {}).get("id"),
        user_email=context.get("user", {}).get("email"),
        chain_depth=context.get("_chain_depth", 0) + 1,
    )


async def _action_send_notification(action: dict, context: dict, db) -> None:
    """Send an in-app notification (stored in notifications collection)."""
    message = interpolate(action.get("message", ""), context)
    title = interpolate(action.get("title", "Workflow Notification"), context)
    target_type = action.get("target", "")
    target_value = interpolate(action.get("target_value", ""), context)

    user_ids = []

    if target_type == "role":
        from db.session import get_sessionmaker
        from repositories.users import UserRepository

        sm = get_sessionmaker()
        async with sm() as pg:
            users = await UserRepository(pg).list_users(
                role=target_value, is_active=True, limit=100
            )
        user_ids = [u.user_id for u in users]

    elif target_type == "user":
        user_ids = [target_value]

    elif target_type == "record_field":
        record = context.get("record", {})
        field_val = record.get(target_value)
        if field_val:
            user_ids = [field_val]

    record = context.get("record", {})
    record_id = None
    for key in record:
        if key.endswith("_id") and key not in ("user_id", "account_id"):
            record_id = record[key]
            break

    for uid in user_ids:
        notification = {
            "notification_id": f"notif_{uuid.uuid4().hex[:12]}",
            "user_id": uid,
            "title": title,
            "body": message,
            "module": context.get("rule", {}).get("module", ""),
            "record_id": record_id,
            "rule_id": context.get("rule", {}).get("id"),
            "read": False,
            "created_at": datetime.now(timezone.utc),
        }
        await db.notifications.insert_one(notification)

    logger.info(f"Workflow: sent notification to {len(user_ids)} users")


async def _action_send_email(action: dict, context: dict) -> None:
    """Send an email via Resend."""
    to = interpolate(action.get("to", ""), context)
    subject = interpolate(action.get("subject", ""), context)
    body = interpolate(action.get("body", ""), context)

    if not to:
        raise ValueError("send_email requires 'to'")

    if not body.strip().startswith("<"):
        body = f"""<div style="font-family:Arial,sans-serif;padding:20px;">
            <p>{body}</p>
            <hr style="margin-top:24px;border:1px solid #e2e8f0;">
            <p style="color:#94a3b8;font-size:12px;">Automated by AEGISAXIOM Workflow Engine</p>
        </div>"""

    await send_email_raw(to, subject, body)
    logger.info(f"Workflow: sent email to {to}")


async def _action_trigger_webhook(action: dict, context: dict) -> None:
    """Trigger an external webhook."""
    url = interpolate(action.get("url", ""), context)
    method = action.get("method", "POST").upper()
    headers = interpolate(action.get("headers", {}), context)
    body = interpolate(action.get("body", {}), context)

    if not url:
        raise ValueError("trigger_webhook requires 'url'")

    async with httpx.AsyncClient(timeout=10.0) as client:
        if isinstance(body, (dict, list)):
            resp = await client.request(method, url, json=body, headers=headers)
        elif isinstance(body, str) and body:
            resp = await client.request(method, url, content=body.encode(), headers=headers)
        else:
            resp = await client.request(method, url, headers=headers)
        if resp.status_code >= 400:
            raise ValueError(f"Webhook returned {resp.status_code}: {resp.text[:200]}")

    logger.info(f"Workflow: webhook {url} → {resp.status_code}")


async def _action_trigger_workflow(action: dict, context: dict, event: dict, chain_depth: int) -> None:
    """Execute another workflow rule directly (increments chain depth)."""
    target_rule_id = action.get("workflow_id", "")
    if not target_rule_id:
        raise ValueError("trigger_workflow requires 'workflow_id'")

    db = get_db()
    target_rule = await db.workflow_rules.find_one(
        {"rule_id": target_rule_id, "enabled": True, "deleted_at": None}, {"_id": 0}
    )
    if not target_rule:
        raise ValueError(f"Target workflow rule {target_rule_id} not found or disabled")

    # Execute target rule's actions directly at incremented chain depth
    from services.workflow_engine import _log_execution, MAX_CHAIN_DEPTH
    from services.policy_engine import _evaluate_conditions

    new_depth = chain_depth + 1
    if new_depth >= MAX_CHAIN_DEPTH:
        await _log_execution(
            db, target_rule, event, "skipped",
            error=f"Max chain depth ({new_depth}) exceeded", chain_depth=new_depth,
        )
        return

    record = event.get("record", {})
    conditions_met = _evaluate_conditions(target_rule, record)

    if not conditions_met:
        await _log_execution(db, target_rule, event, "skipped", chain_depth=new_depth)
        return

    action_results = await execute_actions(target_rule, event, db, new_depth)
    statuses = [r.get("status") for r in action_results]
    if all(s == "success" for s in statuses):
        overall = "success"
    elif all(s == "failed" for s in statuses):
        overall = "failed"
    else:
        overall = "partial"
    await _log_execution(
        db, target_rule, event, overall,
        actions_executed=action_results, chain_depth=new_depth,
    )

    await db.workflow_rules.update_one(
        {"rule_id": target_rule_id},
        {"$set": {"last_run_at": datetime.now(timezone.utc)}, "$inc": {"run_count": 1}},
    )
    logger.info(f"Workflow: triggered workflow {target_rule_id} (depth {new_depth})")


async def _action_delete_record(action: dict, context: dict, db) -> None:
    """Delete records matching a filter in the target module."""
    target_module = action.get("module", "")
    if not target_module:
        raise ValueError("delete_record requires 'module'")

    collection = _get_collection(target_module)
    filter_template = action.get("filter", {})
    filter_query = interpolate(filter_template, context)

    if not filter_query:
        raise ValueError("delete_record requires a non-empty 'filter'")

    result = await db[collection].delete_many(filter_query)
    logger.info(f"Workflow: deleted {result.deleted_count} records from {collection}")
