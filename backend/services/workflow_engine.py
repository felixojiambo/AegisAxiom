"""
Workflow Engine — Asynchronous rule evaluator and dispatcher.

Receives events from the EventBus, matches them against enabled workflow rules,
evaluates conditions (reusing Policy Engine logic), and dispatches actions to
the WorkflowExecutor.
"""

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from database import get_db
from services.policy_engine import _evaluate_conditions, _evaluate_condition

logger = logging.getLogger(__name__)

MAX_CHAIN_DEPTH = 5

# In-memory cache (60s TTL, same pattern as policy engine)
_rule_cache = {"data": None, "timestamp": 0}
CACHE_TTL = 60


def invalidate_workflow_cache():
    """Call after any workflow rule CRUD operation."""
    _rule_cache["data"] = None
    _rule_cache["timestamp"] = 0


async def _get_enabled_rules(db) -> list:
    """Load enabled, non-deleted workflow rules with caching."""
    now = time.time()
    if _rule_cache["data"] is not None and (now - _rule_cache["timestamp"]) < CACHE_TTL:
        return _rule_cache["data"]

    rules = await db.workflow_rules.find(
        {"enabled": True, "deleted_at": None},
        {"_id": 0}
    ).to_list(1000)

    _rule_cache["data"] = rules
    _rule_cache["timestamp"] = now
    return rules


def _matches_trigger(rule: dict, event: dict) -> bool:
    """Check if a rule's trigger matches the incoming event."""
    trigger = rule.get("trigger", {})
    trigger_type = trigger.get("type")
    event_action = event.get("action", "")

    if trigger_type == "record_action":
        rule_action = trigger.get("action", "")
        if rule_action == "created_or_updated":
            return event_action in ("created", "updated")
        return event_action == rule_action

    elif trigger_type == "field_change":
        watch_field = trigger.get("watch_field", "")
        changed_fields = event.get("changed_fields", {})
        if watch_field not in changed_fields:
            return False
        return True

    elif trigger_type in ("date_field", "scheduled"):
        # Handled by workflow_scheduler, not the event bus
        return False

    return False


async def handle_event(event: dict, chain_depth: int = 0) -> None:
    """Main entry point — evaluate all matching rules against an event."""
    module = event.get("module", "")

    if chain_depth >= MAX_CHAIN_DEPTH:
        logger.warning(
            f"Workflow chain depth {chain_depth} reached max ({MAX_CHAIN_DEPTH}). Stopping."
        )
        return

    db = get_db()
    rules = await _get_enabled_rules(db)
    matching_rules = [r for r in rules if r.get("module") == module]

    if not matching_rules:
        return

    # Lazy import to avoid circular dependency
    from services.workflow_executor import execute_actions

    for rule in matching_rules:
        rule_name = rule.get("name", "?")
        rule_id = rule.get("rule_id", "?")

        if not _matches_trigger(rule, event):
            continue

        # Evaluate conditions against the record
        record = event.get("record", {})
        try:
            conditions_met = _evaluate_conditions(rule, record)
        except Exception as e:
            logger.error(f"Condition evaluation failed for rule '{rule_name}': {e}")
            await _log_execution(db, rule, event, "failed", error=str(e), chain_depth=chain_depth)
            continue

        if not conditions_met:
            await _log_execution(db, rule, event, "skipped", chain_depth=chain_depth)
            continue

        # Execute actions
        execution_id = f"wexec_{uuid.uuid4().hex[:12]}"
        started_at = datetime.now(timezone.utc)

        try:
            action_results = await execute_actions(rule, event, db, chain_depth)

            statuses = [r.get("status") for r in action_results]
            if all(s == "success" for s in statuses):
                overall_status = "success"
            elif all(s == "failed" for s in statuses):
                overall_status = "failed"
            elif any(s == "failed" for s in statuses):
                overall_status = "partial"
            else:
                overall_status = "success"

            await _log_execution(
                db, rule, event, overall_status,
                actions_executed=action_results,
                chain_depth=chain_depth,
                execution_id=execution_id,
                started_at=started_at,
            )

            # Update rule metadata
            await db.workflow_rules.update_one(
                {"rule_id": rule_id},
                {"$set": {"last_run_at": datetime.now(timezone.utc)}, "$inc": {"run_count": 1}},
            )

        except Exception as e:
            logger.error(f"Workflow rule '{rule_name}' execution failed: {e}", exc_info=True)
            await _log_execution(
                db, rule, event, "failed",
                error=str(e), chain_depth=chain_depth,
                execution_id=execution_id, started_at=started_at,
            )


async def handle_event_dry_run(event: dict) -> dict:
    """Evaluate rules without executing actions. Returns what would happen."""
    module = event.get("module", "")
    db = get_db()
    rules = await _get_enabled_rules(db)
    matching_rules = [r for r in rules if r.get("module") == module]

    results = []
    for rule in matching_rules:
        if not _matches_trigger(rule, event):
            continue

        record = event.get("record", {})
        conditions_met = _evaluate_conditions(rule, record)

        conditions_detail = []
        for cond in rule.get("conditions", []):
            passed = _evaluate_condition(cond, record)
            conditions_detail.append({
                "field": cond.get("field"),
                "operator": cond.get("operator"),
                "expected": cond.get("value"),
                "actual": record.get(cond.get("field")),
                "passed": passed,
            })

        actions_would_run = []
        if conditions_met:
            actions_would_run = rule.get("instant_actions", []) + rule.get("scheduled_actions", [])

        results.append({
            "rule_id": rule.get("rule_id"),
            "rule_name": rule.get("name"),
            "trigger_matched": True,
            "conditions_met": conditions_met,
            "conditions_detail": conditions_detail,
            "actions_would_execute": actions_would_run,
        })

    return {"dry_run": True, "module": module, "rules_evaluated": len(results), "results": results}


async def _log_execution(
    db,
    rule: dict,
    event: dict,
    status: str,
    actions_executed: Optional[list] = None,
    error: Optional[str] = None,
    chain_depth: int = 0,
    execution_id: Optional[str] = None,
    started_at: Optional[datetime] = None,
) -> None:
    """Log a workflow rule execution."""
    now = datetime.now(timezone.utc)
    doc = {
        "execution_id": execution_id or f"wexec_{uuid.uuid4().hex[:12]}",
        "rule_id": rule.get("rule_id"),
        "rule_name": rule.get("name"),
        "module": rule.get("module"),
        "trigger_type": rule.get("trigger", {}).get("type"),
        "record_id": _extract_record_id(rule.get("module", ""), event.get("record", {})),
        "status": status,
        "actions_executed": actions_executed or [],
        "event_summary": {
            "action": event.get("action"),
            "changed_fields": list(event.get("changed_fields", {}).keys()),
            "user_email": event.get("user_email"),
        },
        "chain_depth": chain_depth,
        "dry_run": False,
        "triggered_at": started_at or now,
        "completed_at": now,
        "execution_time_ms": int((now - (started_at or now)).total_seconds() * 1000),
        "error": error,
        "created_at": now,
    }
    try:
        await db.workflow_rule_executions.insert_one(doc)
    except Exception as e:
        logger.error(f"Failed to log workflow execution: {e}")


def _extract_record_id(module: str, record: dict) -> Optional[str]:
    """Extract the record ID from the event record based on module."""
    from services.field_registry import get_module_config
    config = get_module_config(module)
    if config:
        return record.get(config.get("id_field", "id"))
    # Fallback: look for common ID fields
    for key in record:
        if key.endswith("_id") and key != "user_id" and key != "account_id":
            return record[key]
    return None
