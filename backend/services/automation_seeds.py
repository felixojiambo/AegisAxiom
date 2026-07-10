"""
DEPRECATED — Sales Operations Automation - Default Rules Seeding

This module is deprecated as of Branch 02 (Policy Engine).
Policy-type rules have been migrated to services/policy_seeds.py.
Workflow-type rules remain here until Branch 03 (Workflow Rules Engine).

Creates built-in automation rules that replace hardcoded "shadow logic"
from solutions_design.py with visible, toggleable rules.
"""

from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
import logging

logger = logging.getLogger(__name__)

# Built-in rule IDs (deterministic for idempotent seeding)
BUILTIN_RULE_IDS = {
    "missing_contractor_block": "builtin_missing_contractor_block",
    "expected_date_auto_progress": "builtin_expected_date_auto_progress",
    "negative_margin_alert": "builtin_negative_margin_alert"
}

async def seed_builtin_automation_rules(db: AsyncIOMotorClient):
    """
    Seed default built-in automation rules.
    These rules extract hardcoded backend logic into visible, toggleable rules.
    """
    
    rules_to_seed = [
        # ==================== RULE 1: Missing Contractor Auto-Block ====================
        {
            "rule_id": BUILTIN_RULE_IDS["missing_contractor_block"],
            "name": "🚫 Missing Contractor Auto-Block",
            "description": "Automatically blocks SOW when a resource assignment is missing a contractor",
            "functional_area": "solution_design",
            "trigger_category": "status",
            "trigger_type": "missing_contractor_detected",
            "conditions": [
                {
                    "field": "has_missing_contractors",
                    "operator": "equals",
                    "value": True,
                    "time_unit": None
                }
            ],
            "actions": [
                {
                    "action_type": "banner",
                    "severity": "critical",
                    "banner_message": "SOW blocked - one or more resource assignments are missing contractors",
                    "recipients": [],
                    "email_template": None,
                    "new_status": None,
                    "task_description": None
                },
                {
                    "action_type": "auto_status_change",
                    "severity": None,
                    "banner_message": None,
                    "recipients": [],
                    "email_template": None,
                    "new_status": "blocked",
                    "task_description": None
                }
            ],
            "lob_filter": None,
            "stage_filter": ["proposal_development"],
            "enabled": True,
            "priority": 10,
            "is_built_in": True,
            "created_by": "system",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        },
        
        # ==================== RULE 2: Expected Completion Date Auto-Progress ====================
        {
            "rule_id": BUILTIN_RULE_IDS["expected_date_auto_progress"],
            "name": "▶️ Expected Date Auto-Progress",
            "description": "Automatically moves SOW from 'Not Started' to 'In Progress' when Expected Completion Date is filled",
            "functional_area": "solution_design",
            "trigger_category": "status",
            "trigger_type": "expected_completion_date_filled",
            "conditions": [
                {
                    "field": "expected_completion_date_exists",
                    "operator": "equals",
                    "value": True,
                    "time_unit": None
                },
                {
                    "field": "sow_status",
                    "operator": "equals",
                    "value": "not_started",
                    "time_unit": None
                }
            ],
            "actions": [
                {
                    "action_type": "auto_status_change",
                    "severity": None,
                    "banner_message": None,
                    "recipients": [],
                    "email_template": None,
                    "new_status": "in_progress",
                    "task_description": None
                },
                {
                    "action_type": "banner",
                    "severity": "success",
                    "banner_message": "SOW automatically progressed to 'In Progress'",
                    "recipients": [],
                    "email_template": None,
                    "new_status": None,
                    "task_description": None
                }
            ],
            "lob_filter": None,
            "stage_filter": ["proposal_development"],
            "enabled": True,
            "priority": 9,
            "is_built_in": True,
            "created_by": "system",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        },
        
        # ==================== RULE 3: Negative Margin Critical Alert ====================
        {
            "rule_id": BUILTIN_RULE_IDS["negative_margin_alert"],
            "name": "⚠️ Negative Margin Critical Alert",
            "description": "Triggers critical alert when any resource assignment has negative profit margin",
            "functional_area": "procurement",
            "trigger_category": "margin",
            "trigger_type": "margin_below",
            "conditions": [
                {
                    "field": "margin_percentage",
                    "operator": "less_than",
                    "value": 0,
                    "time_unit": None
                }
            ],
            "actions": [
                {
                    "action_type": "banner",
                    "severity": "critical",
                    "banner_message": "Negative margin detected - review resource pricing immediately",
                    "recipients": [],
                    "email_template": None,
                    "new_status": None,
                    "task_description": None
                },
                {
                    "action_type": "email",
                    "severity": None,
                    "banner_message": None,
                    "recipients": ["procurement@simplimeta.io"],
                    "email_template": "negative_margin_alert",
                    "new_status": None,
                    "task_description": None
                }
            ],
            "lob_filter": None,
            "stage_filter": ["proposal_development"],
            "enabled": True,
            "priority": 10,
            "is_built_in": True,
            "created_by": "system",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        }
    ]
    
    # Upsert rules (insert if not exists, skip if already exists)
    for rule in rules_to_seed:
        existing = await db.automation_rules.find_one({"rule_id": rule["rule_id"]})
        if not existing:
            await db.automation_rules.insert_one(rule)
            logger.info(f"✅ Seeded built-in rule: {rule['name']} ({rule['rule_id']})")
        else:
            logger.info(f"⏭️ Built-in rule already exists: {rule['name']}")
    
    logger.info(f"✅ Built-in automation rules seeding complete ({len(rules_to_seed)} rules)")
    return {"seeded": len(rules_to_seed), "rules": [r["rule_id"] for r in rules_to_seed]}
