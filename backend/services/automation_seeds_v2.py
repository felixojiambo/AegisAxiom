"""
DEPRECATED — Sales Operations Automation - Enhanced Seed Rules

This module is deprecated as of Branch 02 (Policy Engine).
Policy-type rules (block_stage) have been migrated to services/policy_seeds.py.
Workflow-type rules (alerts, auto-progress) remain here until Branch 03.

Creates built-in automation rules with flexible preconditions and action types:
- Preconditions: WHEN to evaluate the rule
- Conditions: WHAT to check
- Actions: WHAT to do (banner, block_stage, stage_transition, email, etc.)
"""

from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
import logging

logger = logging.getLogger(__name__)

# Built-in rule IDs (deterministic for idempotent seeding)
BUILTIN_RULE_IDS = {
    "negative_margin_alert": "builtin_negative_margin_alert",
    "missing_contractor_block": "builtin_missing_contractor_block",
    "block_complete_no_resources": "builtin_block_complete_no_resources",
    "expected_date_auto_progress": "builtin_expected_date_auto_progress"
}

async def seed_enhanced_automation_rules(db: AsyncIOMotorClient):
    """
    Seed enhanced automation rules with preconditions and advanced actions.
    """
    
    rules_to_seed = [
        # ==================== RULE 1: Negative Margin Alert ====================
        {
            "rule_id": BUILTIN_RULE_IDS["negative_margin_alert"],
            "name": "⚠️ Negative Margin Critical Alert",
            "description": "Alert when SOW margin is negative (losing money)",
            "functional_area": "solution_design",
            "trigger_category": "margin",
            "trigger_type": "margin_below",
            
            # PRECONDITIONS: Only check if resources are assigned and margin is calculated
            "preconditions": [
                {
                    "field": "has_resources",
                    "operator": "greater_than",
                    "value": 0,
                    "time_unit": None
                },
                {
                    "field": "margin_is_calculated",
                    "operator": "equals",
                    "value": True,
                    "time_unit": None
                }
            ],
            
            # CONDITIONS: Check if margin < 0
            "conditions": [
                {
                    "field": "margin_percentage",
                    "operator": "less_than",
                    "value": 0,
                    "time_unit": None
                }
            ],
            
            # ACTIONS: Show critical banner
            "actions": [
                {
                    "action_type": "banner",
                    "severity": "critical",
                    "banner_message": "Negative margin detected - review resource pricing immediately",
                    "recipients": [],
                    "email_template": None
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
        
        # ==================== RULE 2: Missing Contractor Block ====================
        {
            "rule_id": BUILTIN_RULE_IDS["missing_contractor_block"],
            "name": "🚫 Missing Contractor Auto-Block",
            "description": "Blocks SOW when resource assignments are missing contractors",
            "functional_area": "contractor_management",
            "trigger_category": "status",
            "trigger_type": "missing_contractor_detected",
            
            # PRECONDITIONS: Only check if resources are defined
            "preconditions": [
                {
                    "field": "has_resources",
                    "operator": "greater_than",
                    "value": 0,
                    "time_unit": None
                }
            ],
            
            # CONDITIONS: Check for blocked status
            "conditions": [
                {
                    "field": "sow_status",
                    "operator": "equals",
                    "value": "blocked",
                    "time_unit": None
                }
            ],
            
            # ACTIONS: Show banner + auto-block stage transitions
            "actions": [
                {
                    "action_type": "banner",
                    "severity": "critical",
                    "banner_message": "Missing contractor assignment - cannot proceed",
                    "recipients": []
                },
                {
                    "action_type": "block_stage",
                    "blocked_stages": ["ready_for_review", "complete"],
                    "block_reason": "Cannot complete SOW without assigning contractors to all resources"
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
        
        # ==================== RULE 3: Block Complete Without Resources ====================
        {
            "rule_id": BUILTIN_RULE_IDS["block_complete_no_resources"],
            "name": "🔒 Block Complete Without Resources",
            "description": "Prevents marking SOW as complete if no resources are assigned",
            "functional_area": "solution_design",
            "trigger_category": "status",
            "trigger_type": "completion_requirements",
            
            # PRECONDITIONS: Always check (no preconditions)
            "preconditions": [],
            
            # CONDITIONS: No resources assigned
            "conditions": [
                {
                    "field": "has_resources",
                    "operator": "equals",
                    "value": 0,
                    "time_unit": None
                }
            ],
            
            # ACTIONS: Block Complete stage
            "actions": [
                {
                    "action_type": "block_stage",
                    "blocked_stages": ["complete"],
                    "block_reason": "Cannot mark SOW as complete without assigning at least one resource"
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
        
        # ==================== RULE 4: Expected Date Auto-Progress ====================
        {
            "rule_id": BUILTIN_RULE_IDS["expected_date_auto_progress"],
            "name": "▶️ Expected Date Auto-Progress",
            "description": "Auto-moves SOW from 'Not Started' to 'In Progress' when Expected Completion Date is filled",
            "functional_area": "solution_design",
            "trigger_category": "status",
            "trigger_type": "expected_completion_date_filled",
            
            # PRECONDITIONS: Only if status is not_started
            "preconditions": [
                {
                    "field": "sow_status",
                    "operator": "equals",
                    "value": "not_started",
                    "time_unit": None
                }
            ],
            
            # CONDITIONS: Expected completion date is set
            "conditions": [
                {
                    "field": "expected_completion_date_set",
                    "operator": "equals",
                    "value": True,
                    "time_unit": None
                }
            ],
            
            # ACTIONS: Auto-move to in_progress
            "actions": [
                {
                    "action_type": "stage_transition",
                    "target_stage": "in_progress",
                    "transition_condition": "auto"
                },
                {
                    "action_type": "banner",
                    "severity": "success",
                    "banner_message": "SOW auto-moved to 'In Progress' (Expected Completion Date filled)"
                }
            ],
            
            "lob_filter": None,
            "stage_filter": ["proposal_development"],
            "enabled": True,
            "priority": 5,
            "is_built_in": True,
            "created_by": "system",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        }
    ]
    
    # Insert or update rules
    for rule in rules_to_seed:
        existing = await db.automation_rules.find_one({"rule_id": rule["rule_id"]})
        
        if existing:
            # Update existing built-in rule (preserve enabled status)
            update_data = {k: v for k, v in rule.items() if k not in ["rule_id", "created_by", "created_at"]}
            update_data["enabled"] = existing.get("enabled", True)  # Preserve user's enabled choice
            update_data["updated_at"] = datetime.now(timezone.utc)
            
            await db.automation_rules.update_one(
                {"rule_id": rule["rule_id"]},
                {"$set": update_data}
            )
            logger.info(f"✅ Updated built-in rule: {rule['name']}")
        else:
            # Insert new rule
            await db.automation_rules.insert_one(rule)
            logger.info(f"✅ Created built-in rule: {rule['name']}")
    
    logger.info(f"✅ Seeded {len(rules_to_seed)} built-in automation rules")
