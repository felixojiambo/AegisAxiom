"""
Workflow Rules — System Workflow Seeds

Seeds built-in workflow automation rules on startup.
Idempotent: uses rule_id as the key. On re-seed, updates the definition
but preserves the user's ``enabled`` setting.

These replace hardcoded automation logic that was previously scattered
across individual route handlers.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


SYSTEM_WORKFLOWS = [
    # ── Contractor Rate Auto-Approval ──────────────────────────────────
    # Previously hardcoded in routes/admin.py lines 158-173.
    # When a contractor resource-type assignment is created or updated,
    # auto-approve if the individual rate is ≤ the business rate.
    {
        "rule_id": "sys_contractor_rate_auto_approve",
        "name": "Auto-Approve Contractor Rate (≤ Business Rate)",
        "description": (
            "Automatically approves contractor resource-type assignments "
            "when the individual rate does not exceed the business rate."
        ),
        "module": "contractor_management",
        "trigger": {
            "type": "field_change",
            "watch_field": "cost_rate",
        },
        "condition_logic": "AND",
        "conditions": [
            {
                "field": "cost_rate_exceeds_business_rate",
                "operator": "is_false",
                "value": None,
                "value_type": "boolean",
            },
            {
                "field": "business_resource_approved",
                "operator": "is_true",
                "value": None,
                "value_type": "boolean",
            },
        ],
        "instant_actions": [
            {
                "type": "update_field",
                "module": "contractor_management",
                "field": "approval_status",
                "value": "approved",
            },
        ],
        "scheduled_actions": [],
        "priority": 10,
    },
    {
        "rule_id": "sys_contractor_rate_pending",
        "name": "Flag Contractor Rate for Approval (> Business Rate)",
        "description": (
            "Sets contractor resource-type assignment to pending approval "
            "when the individual rate exceeds the business rate."
        ),
        "module": "contractor_management",
        "trigger": {
            "type": "field_change",
            "watch_field": "cost_rate",
        },
        "condition_logic": "AND",
        "conditions": [
            {
                "field": "cost_rate_exceeds_business_rate",
                "operator": "is_true",
                "value": None,
                "value_type": "boolean",
            },
        ],
        "instant_actions": [
            {
                "type": "update_field",
                "module": "contractor_management",
                "field": "approval_status",
                "value": "pending",
            },
            {
                "type": "send_notification",
                "title": "Contractor Rate Exceeds Business Rate",
                "message": (
                    "{{record.contractor_name}} rate for {{record.resource_type_name}} "
                    "exceeds the business rate and requires approval."
                ),
                "target": "role",
                "target_value": "admin",
            },
        ],
        "scheduled_actions": [],
        "priority": 9,
    },

    # ── SOW Auto-Status Transitions ────────────────────────────────────
    # Previously hardcoded in routes/solutions_design.py lines 745-751.
    # Auto-sets SOW status to "blocked" when critical blockers exist,
    # or "in_progress" when resources are assigned and no blockers.
    {
        "rule_id": "sys_sow_auto_blocked",
        "name": "Auto-Block SOW on Critical Blockers",
        "description": (
            "Automatically sets SOW status to 'blocked' when critical "
            "blockers are detected (missing contractor, negative margin)."
        ),
        "module": "sales_ops",
        "trigger": {
            "type": "record_action",
            "action": "updated",
        },
        "condition_logic": "AND",
        "conditions": [
            {
                "field": "has_critical_blockers",
                "operator": "is_true",
                "value": None,
                "value_type": "boolean",
            },
        ],
        "instant_actions": [
            {
                "type": "update_field",
                "module": "sales_ops",
                "field": "status",
                "value": "blocked",
            },
        ],
        "scheduled_actions": [],
        "priority": 10,
    },
    {
        "rule_id": "sys_sow_auto_in_progress",
        "name": "Auto-Start SOW When Resources Assigned",
        "description": (
            "Automatically transitions SOW from 'not_started' to 'in_progress' "
            "when resources are assigned and no critical blockers exist."
        ),
        "module": "sales_ops",
        "trigger": {
            "type": "record_action",
            "action": "updated",
        },
        "condition_logic": "AND",
        "conditions": [
            {
                "field": "has_critical_blockers",
                "operator": "is_false",
                "value": None,
                "value_type": "boolean",
            },
            {
                "field": "resource_count",
                "operator": "greater_than",
                "value": 0,
                "value_type": "number",
            },
            {
                "field": "status",
                "operator": "equals",
                "value": "not_started",
                "value_type": "string",
            },
        ],
        "instant_actions": [
            {
                "type": "update_field",
                "module": "sales_ops",
                "field": "status",
                "value": "in_progress",
            },
        ],
        "scheduled_actions": [],
        "priority": 8,
    },

    # ── Project Health Thresholds ──────────────────────────────────────
    # Previously hardcoded in routes/project_health_automation.py lines 44-76.
    # Budget warning at 70%, critical at 90%, overdue tasks at 1 day.
    {
        "rule_id": "sys_project_health_budget_warning",
        "name": "Project Budget Warning (70%)",
        "description": (
            "Sends a warning notification when a project has consumed "
            "70% or more of its budget."
        ),
        "module": "pm_projects",
        "trigger": {
            "type": "field_change",
            "watch_field": "budget_consumed_pct",
        },
        "condition_logic": "AND",
        "conditions": [
            {
                "field": "budget_consumed_pct",
                "operator": "greater_than_or_equal",
                "value": 70.0,
                "value_type": "number",
            },
            {
                "field": "budget_consumed_pct",
                "operator": "less_than",
                "value": 90.0,
                "value_type": "number",
            },
        ],
        "instant_actions": [
            {
                "type": "send_notification",
                "title": "Budget Warning",
                "message": (
                    "Project {{record.name}} has consumed {{record.budget_consumed_pct}}% "
                    "of its budget."
                ),
                "target": "role",
                "target_value": "project_manager",
            },
        ],
        "scheduled_actions": [],
        "priority": 7,
    },
    {
        "rule_id": "sys_project_health_budget_critical",
        "name": "Project Budget Critical (90%)",
        "description": (
            "Sends a critical alert when a project has consumed "
            "90% or more of its budget."
        ),
        "module": "pm_projects",
        "trigger": {
            "type": "field_change",
            "watch_field": "budget_consumed_pct",
        },
        "condition_logic": "AND",
        "conditions": [
            {
                "field": "budget_consumed_pct",
                "operator": "greater_than_or_equal",
                "value": 90.0,
                "value_type": "number",
            },
        ],
        "instant_actions": [
            {
                "type": "send_notification",
                "title": "Budget Critical",
                "message": (
                    "Project {{record.name}} has consumed {{record.budget_consumed_pct}}% "
                    "of its budget — immediate review required."
                ),
                "target": "role",
                "target_value": "admin",
            },
        ],
        "scheduled_actions": [],
        "priority": 9,
    },
    # ── Procurement Handoff ────────────────────────────────────────────
    # MEB4-T40. When an opportunity stage transitions to scoping_complete,
    # create a procurement_notifications record so Maurice sees the handoff
    # in the existing Procurement tab Approvals view. `field_change` on
    # `stage` is used (instead of `record_action` on `stage_changed`) so
    # the rule also fires when stage is changed via PUT /opportunities/{id}
    # — which emits 'updated', not 'stage_changed'. See
    # docs/procurement-handoff-analysis.md Q4/Q5.
    {
        "rule_id": "sys_notify_procurement_on_scoping_complete",
        "name": "Notify Procurement on Scoping Complete",
        "description": (
            "When an opportunity's stage transitions to scoping_complete, "
            "create a procurement notification so procurement sees the handoff "
            "in the Procurement tab Approvals view."
        ),
        "module": "opportunities",
        "trigger": {
            "type": "field_change",
            "watch_field": "stage",
        },
        "condition_logic": "AND",
        "conditions": [
            {
                "field": "stage",
                "operator": "equals",
                "value": "scoping_complete",
                "value_type": "string",
            },
        ],
        "instant_actions": [
            {
                "type": "create_record",
                "module": "procurement_notifications",
                # Skip if a pending notification already exists for this
                # opportunity+type. Stage re-entry (scoping_complete → other →
                # scoping_complete) would otherwise spam the Approvals view.
                "dedup_on": ["opportunity_id", "notification_type", "status"],
                "fields": {
                    # Pre-set notification_id to match sales_ops_procurement.py's
                    # procnotif_<hex12> format. Without this, the executor's
                    # target_module[:3] fallback would mint pro_<hex12> IDs and
                    # collide with the route's format inside one collection.
                    "notification_id": "procnotif_{{uuid}}",
                    "notification_type": "scoping_complete",
                    "opportunity_id": "{{record.opportunity_id}}",
                    "resource_type_name": "{{record.name}}",
                    "message": (
                        "Opportunity {{record.name}} has reached scoping_complete. "
                        "Review procurement requirements and open any hardware or "
                        "material requests needed."
                    ),
                    "status": "pending",
                    "created_by": "system",
                    "created_by_name": "Workflow Engine",
                },
            },
        ],
        "scheduled_actions": [],
        "priority": 8,
    },
    {
        "rule_id": "sys_project_health_overdue_tasks",
        "name": "Project Overdue Tasks Alert",
        "description": (
            "Sends a critical alert when a project has tasks that are "
            "overdue by 1 or more days."
        ),
        "module": "pm_projects",
        "trigger": {
            "type": "field_change",
            "watch_field": "overdue_task_count",
        },
        "condition_logic": "AND",
        "conditions": [
            {
                "field": "overdue_task_count",
                "operator": "greater_than",
                "value": 0,
                "value_type": "number",
            },
        ],
        "instant_actions": [
            {
                "type": "send_notification",
                "title": "Overdue Tasks",
                "message": (
                    "Project {{record.name}} has {{record.overdue_task_count}} "
                    "overdue task(s) requiring attention."
                ),
                "target": "role",
                "target_value": "project_manager",
            },
        ],
        "scheduled_actions": [],
        "priority": 8,
    },
]


async def seed_system_workflows(db):
    """
    Seed all system workflow rules.  Idempotent — upserts by rule_id.
    Preserves the user's ``enabled`` setting on existing rules.
    """
    try:
        seeded = 0
        updated = 0

        for wf_def in SYSTEM_WORKFLOWS:
            rule_id = wf_def["rule_id"]
            existing = await db.workflow_rules.find_one({"rule_id": rule_id})

            if existing:
                user_enabled = existing.get("enabled", True)
                update_doc = {**wf_def}
                update_doc["enabled"] = user_enabled
                update_doc["is_system"] = True
                update_doc["updated_at"] = datetime.now(timezone.utc)
                update_doc["created_by"] = existing.get("created_by", "system")
                update_doc["created_at"] = existing.get("created_at", datetime.now(timezone.utc))
                update_doc["deleted_at"] = None

                await db.workflow_rules.update_one(
                    {"rule_id": rule_id},
                    {"$set": update_doc},
                )
                updated += 1
            else:
                now = datetime.now(timezone.utc)
                doc = {
                    **wf_def,
                    "is_system": True,
                    "enabled": True,
                    "created_by": "system",
                    "created_at": now,
                    "updated_at": now,
                    "deleted_at": None,
                    "last_run_at": None,
                    "run_count": 0,
                }
                await db.workflow_rules.insert_one(doc)
                seeded += 1

        logger.info(f"Workflow Engine: {seeded} new system workflows seeded, {updated} existing updated")
        return {"seeded": seeded, "updated": updated}
    except Exception:
        logger.exception("Workflow Engine: failed to seed system workflows")
        return {"seeded": 0, "updated": 0}
