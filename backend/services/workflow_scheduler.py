"""
Workflow Scheduler — APScheduler integration for date_field and cron triggers.

Uses the existing AsyncIOScheduler from server.py (app.state.scheduler).
"""

import logging
from datetime import datetime, timezone, timedelta

from database import get_db

logger = logging.getLogger(__name__)


async def start_workflow_scheduler(scheduler) -> None:
    """Register workflow-related jobs on the existing APScheduler."""
    scheduler.add_job(
        scan_date_field_triggers,
        "interval",
        minutes=15,
        id="workflow_date_field_scanner",
        replace_existing=True,
    )
    logger.info("Workflow scheduler: date field scanner registered (every 15 min)")

    await sync_cron_jobs(scheduler)


async def scan_date_field_triggers() -> None:
    """Scan for date_field triggers that are due to fire."""
    db = get_db()

    rules = await db.workflow_rules.find({
        "enabled": True,
        "deleted_at": None,
        "trigger.type": "date_field",
    }).to_list(500)

    if not rules:
        return

    from services.workflow_executor import execute_actions
    from services.workflow_engine import _log_execution
    from services.policy_engine import _evaluate_conditions
    from services.field_registry import get_module_config

    now = datetime.now(timezone.utc)

    for rule in rules:
        trigger = rule.get("trigger", {})
        module = rule.get("module", "")
        date_field_name = trigger.get("date_field", "")
        offset_direction = trigger.get("offset_direction", "on")
        offset_value = trigger.get("offset_value", 0)
        offset_unit = trigger.get("offset_unit", "days")
        repeat = trigger.get("repeat", "once")

        if not date_field_name or not module:
            continue

        config = get_module_config(module)
        if not config:
            continue

        collection = config["collection"]
        id_field = config["id_field"]

        # Calculate offset in minutes
        multipliers = {"minutes": 1, "hours": 60, "days": 1440}
        offset_minutes = offset_value * multipliers.get(offset_unit, 1440)

        # Determine target date window
        if offset_direction == "before":
            target_date = now + timedelta(minutes=offset_minutes)
        elif offset_direction == "after":
            target_date = now - timedelta(minutes=offset_minutes)
        else:  # "on"
            target_date = now

        # 15-minute scan window
        window_start = target_date - timedelta(minutes=8)
        window_end = target_date + timedelta(minutes=8)

        try:
            records = await db[collection].find({
                date_field_name: {"$gte": window_start, "$lte": window_end}
            }).to_list(100)
        except Exception as e:
            logger.error(f"Date field scan failed for {collection}.{date_field_name}: {e}")
            continue

        for record in records:
            record_id = record.get(id_field, str(record.get("_id", "")))

            # Dedupe: check if already fired
            already_fired = await db.workflow_date_trigger_log.find_one({
                "rule_id": rule.get("rule_id"),
                "record_id": str(record_id),
                "trigger_window": target_date.strftime("%Y-%m-%d"),
            })
            if already_fired and repeat == "once":
                continue

            # Mark as fired
            await db.workflow_date_trigger_log.update_one(
                {
                    "rule_id": rule.get("rule_id"),
                    "record_id": str(record_id),
                    "trigger_window": target_date.strftime("%Y-%m-%d"),
                },
                {"$set": {"fired_at": now, "updated_at": now}},
                upsert=True,
            )

            # Build synthetic event
            record.pop("_id", None)
            event = {
                "module": module,
                "action": "date_field_trigger",
                "record": record,
                "old_record": None,
                "changed_fields": {},
                "user_id": "system",
                "user_email": "system@aegisaxiom.internal",
                "timestamp": now.isoformat(),
                "chain_depth": 0,
            }

            try:
                conditions_met = _evaluate_conditions(rule, record)
                if not conditions_met:
                    await _log_execution(db, rule, event, "skipped", chain_depth=0)
                    continue

                action_results = await execute_actions(rule, event, db, chain_depth=0)
                statuses = [r.get("status") for r in action_results]
                if all(s == "success" for s in statuses):
                    overall = "success"
                elif any(s == "success" for s in statuses):
                    overall = "partial"
                else:
                    overall = "failed"

                await _log_execution(
                    db, rule, event, overall, actions_executed=action_results, chain_depth=0,
                )
                await db.workflow_rules.update_one(
                    {"rule_id": rule.get("rule_id")},
                    {"$set": {"last_run_at": now}, "$inc": {"run_count": 1}},
                )
            except Exception as e:
                logger.error(f"Date field trigger failed for rule '{rule.get('name')}': {e}")
                await _log_execution(db, rule, event, "failed", error=str(e), chain_depth=0)


async def sync_cron_jobs(scheduler) -> None:
    """Sync cron workflow rules with APScheduler jobs."""
    db = get_db()

    # Remove existing workflow cron jobs
    for job in scheduler.get_jobs():
        if job.id.startswith("workflow_cron_"):
            scheduler.remove_job(job.id)

    cron_rules = await db.workflow_rules.find({
        "enabled": True,
        "deleted_at": None,
        "trigger.type": "scheduled",
    }).to_list(100)

    for rule in cron_rules:
        trigger = rule.get("trigger", {})
        cron_expr = trigger.get("cron_expression", "")
        tz = trigger.get("timezone", "UTC")

        if not cron_expr:
            continue

        job_id = f"workflow_cron_{rule.get('rule_id')}"
        parts = cron_expr.strip().split()

        if len(parts) == 5:
            try:
                scheduler.add_job(
                    _execute_cron_rule,
                    "cron",
                    args=[rule.get("rule_id")],
                    minute=parts[0],
                    hour=parts[1],
                    day=parts[2],
                    month=parts[3],
                    day_of_week=parts[4],
                    timezone=tz,
                    id=job_id,
                    replace_existing=True,
                )
                logger.info(f"Registered cron job for '{rule.get('name')}': {cron_expr}")
            except Exception as e:
                logger.error(f"Failed to register cron for '{rule.get('name')}': {e}")


async def _execute_cron_rule(rule_id: str) -> None:
    """Execute a scheduled (cron) workflow rule."""
    db = get_db()
    rule = await db.workflow_rules.find_one(
        {"rule_id": rule_id, "enabled": True, "deleted_at": None}, {"_id": 0}
    )
    if not rule:
        logger.warning(f"Cron rule {rule_id} not found or disabled, skipping")
        return

    now = datetime.now(timezone.utc)

    event = {
        "module": rule.get("module", ""),
        "action": "scheduled_trigger",
        "record": {},
        "old_record": None,
        "changed_fields": {},
        "user_id": "system",
        "user_email": "system@aegisaxiom.internal",
        "timestamp": now.isoformat(),
        "chain_depth": 0,
    }

    from services.workflow_executor import execute_actions
    from services.workflow_engine import _log_execution

    try:
        action_results = await execute_actions(rule, event, db, chain_depth=0)
        statuses = [r.get("status") for r in action_results]
        if all(s == "success" for s in statuses):
            overall = "success"
        elif any(s == "success" for s in statuses):
            overall = "partial"
        else:
            overall = "failed"

        await _log_execution(
            db, rule, event, overall, actions_executed=action_results, chain_depth=0,
        )
        await db.workflow_rules.update_one(
            {"rule_id": rule_id},
            {"$set": {"last_run_at": now}, "$inc": {"run_count": 1}},
        )
    except Exception as e:
        logger.error(f"Cron rule {rule_id} execution failed: {e}")
        await _log_execution(db, rule, event, "failed", error=str(e), chain_depth=0)
