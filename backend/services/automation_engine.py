"""
DEPRECATED — Sales Operations Automation Engine

This module is deprecated as of Branch 02 (Policy Engine).
- Policy-type rules (block, warn) have been migrated to the Policy Engine (services/policy_engine.py)
- Workflow-type rules (negative_margin_alert banner/email, expected_date_auto_progress)
  are slated for migration to Branch 03 (Workflow Rules Engine)
- DO NOT add new rules here. Use the Policy Engine for gatekeeper rules.
- DO NOT delete this file yet — it contains workflow rules still in use.
"""

from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
from motor.motor_asyncio import AsyncIOMotorClient
import logging

logger = logging.getLogger(__name__)

class AutomationEngine:
    """Evaluates automation rules and triggers alerts/actions"""
    
    def __init__(self, db: AsyncIOMotorClient):
        self.db = db
    
    async def evaluate_opportunity(self, opportunity_id: str) -> List[Dict]:
        """
        Evaluate all enabled automation rules for an opportunity.
        Evaluates both opportunity-level and SOW-level rules.
        Returns list of triggered alerts with SOW context.
        """
        # Fetch opportunity data
        opportunity = await self.db.opportunities.find_one({"opportunity_id": opportunity_id}, {"_id": 0})
        if not opportunity:
            logger.warning(f"Opportunity {opportunity_id} not found")
            return []
        
        # Fetch all SOWs for this opportunity
        sows = await self.db.opportunity_sows.find(
            {"opportunity_id": opportunity_id},
            {"_id": 0}
        ).to_list(100)
        
        # Fetch enabled automation rules
        rules = await self.db.automation_rules.find({"enabled": True}, {"_id": 0}).to_list(1000)
        
        triggered_alerts = []
        
        for rule in rules:
            # Check if rule applies to this opportunity (LOB/stage filters)
            if not self._rule_applies(rule, opportunity):
                continue
            
            # Determine if rule checks SOW-level metrics
            sow_level_fields = ["margin_percentage", "total_days_in_sales_ops", "sow_status", "has_resources"]
            is_sow_level_rule = any(
                cond["field"] in sow_level_fields 
                for cond in (rule.get("conditions", []) + rule.get("preconditions", []))
            )
            
            if is_sow_level_rule:
                # Evaluate per SOW
                for sow in sows:
                    # Check preconditions first (WHEN to evaluate)
                    if not self._evaluate_preconditions(rule, sow, opportunity):
                        continue  # Skip this SOW if preconditions not met
                    
                    # Check trigger conditions (WHAT to check)
                    if self._evaluate_sow_conditions(rule, sow, opportunity):
                        # Check for existing alert for this SOW
                        existing_alert = await self.db.triggered_alerts.find_one({
                            "opportunity_id": opportunity_id,
                            "rule_id": rule["rule_id"],
                            "sow_id": sow.get("sow_id"),
                            "dismissed": False
                        })
                        
                        if not existing_alert:
                            # Execute actions (may include banner, block, email, etc.)
                            alert = await self._execute_rule_actions(rule, opportunity, sow)
                            if alert:
                                triggered_alerts.append(alert)
            else:
                # Evaluate at opportunity level
                # Check preconditions
                if not self._evaluate_preconditions(rule, None, opportunity):
                    continue
                
                if self._evaluate_conditions(rule, opportunity):
                    existing_alert = await self.db.triggered_alerts.find_one({
                        "opportunity_id": opportunity_id,
                        "rule_id": rule["rule_id"],
                        "sow_id": None,  # Opportunity-level alert
                        "dismissed": False
                    })
                    
                    if not existing_alert:
                        alert = await self._execute_rule_actions(rule, opportunity, None)
                        if alert:
                            triggered_alerts.append(alert)
        
        return triggered_alerts
    
    def _evaluate_preconditions(self, rule: Dict, sow: Dict = None, opportunity: Dict = None) -> bool:
        """
        Evaluate rule preconditions (WHEN to check).
        Preconditions determine if the rule should be evaluated at all.
        Returns True if all preconditions are met OR if no preconditions exist.
        """
        preconditions = rule.get("preconditions", [])
        if not preconditions:
            return True  # No preconditions = always evaluate
        
        # All preconditions must be TRUE (AND logic)
        for precond in preconditions:
            field = precond["field"]
            operator = precond["operator"]
            expected_value = precond.get("value")
            
            # Special fields for preconditions
            if field == "has_resources":
                # Check if SOW has resource assignments
                if not sow:
                    return False
                resource_count = len(sow.get("selected_resource_assignment_ids", []))
                field_value = resource_count
            
            elif field == "sow_status":
                # Check SOW status
                if not sow:
                    return False
                field_value = sow.get("status", "not_started")
            
            elif field == "margin_is_calculated":
                # Check if margin has been calculated (not None, not 0)
                if not sow:
                    return False
                margin = sow.get("margin_percentage")
                field_value = margin is not None and margin != 0
                expected_value = True  # Convert to boolean check
            
            elif field in ["stage", "opportunity_stage"]:
                field_value = opportunity.get("stage") if opportunity else None
            
            elif field == "expected_completion_date_set":
                if not sow:
                    return False
                field_value = bool(sow.get("expected_completion_date"))
                expected_value = True
            
            else:
                # Generic field lookup
                field_value = sow.get(field) if sow else opportunity.get(field) if opportunity else None
            
            # Evaluate operator
            if operator == "greater_than":
                if not (field_value and field_value > expected_value):
                    return False
            elif operator == "less_than":
                if not (field_value and field_value < expected_value):
                    return False
            elif operator == "equals":
                if field_value != expected_value:
                    return False
            elif operator == "in":
                # expected_value should be a list
                if not isinstance(expected_value, list):
                    expected_value = [expected_value]
                if field_value not in expected_value:
                    return False
            elif operator == "not_in":
                if not isinstance(expected_value, list):
                    expected_value = [expected_value]
                if field_value in expected_value:
                    return False
        
        return True  # All preconditions met
    
    def _rule_applies(self, rule: Dict, opportunity: Dict) -> bool:
        """Check if rule's filters match the opportunity"""
        # LOB filter
        if rule.get("lob_filter"):
            opp_lobs = [sow.get("lob") for sow in opportunity.get("sows", [])]
            if not any(lob in rule["lob_filter"] for lob in opp_lobs):
                return False
        
        # Stage filter
        if rule.get("stage_filter"):
            if opportunity.get("stage") not in rule["stage_filter"]:
                return False
        
        return True
    
    def _evaluate_conditions(self, rule: Dict, opportunity: Dict) -> bool:
        """Evaluate all conditions (AND logic)"""
        for condition in rule.get("conditions", []):
            if not self._evaluate_single_condition(condition, opportunity):
                return False
        return True
    
    def _evaluate_single_condition(self, condition: Dict, opportunity: Dict) -> bool:
        """Evaluate a single condition"""
        field = condition["field"]
        operator = condition["operator"]
        threshold = condition.get("value")
        time_unit = condition.get("time_unit", "days")
        
        # Extract field value from opportunity
        field_value = self._get_field_value(field, opportunity)
        
        if field_value is None:
            return False
        
        # Convert threshold to days if time_unit is specified for time-based fields
        if time_unit and field in ["days_in_stage", "total_days_in_sales_ops", "days_until_expected_completion", "days_since_last_activity"]:
            threshold = self._convert_to_days(threshold, time_unit)
            if condition.get("value_max"):
                condition["value_max"] = self._convert_to_days(condition["value_max"], time_unit)
        
        # Evaluate operator
        if operator == "greater_than":
            return field_value > threshold
        elif operator == "less_than":
            return field_value < threshold
        elif operator == "equals":
            return field_value == threshold
        elif operator == "between":
            return threshold <= field_value <= condition.get("value_max", float('inf'))
        elif operator == "not_equals":
            return field_value != threshold
        
        return False

    def _evaluate_sow_conditions(self, rule: Dict, sow: Dict, opportunity: Dict) -> bool:
        """Evaluate rule conditions against a specific SOW (for SOW-level metrics)"""
        conditions = rule.get("conditions", [])
        
        for condition in conditions:
            field = condition["field"]
            
            # Only evaluate SOW-specific fields
            if field in ["margin_percentage", "total_days_in_sales_ops", "sow_status"]:
                if not self._evaluate_single_sow_condition(condition, sow, opportunity):
                    return False
            else:
                # Skip opportunity-level conditions when evaluating SOWs
                continue
        
        return True
    
    def _evaluate_single_sow_condition(self, condition: Dict, sow: Dict, opportunity: Dict) -> bool:
        """Evaluate a single condition against SOW data"""
        field = condition["field"]
        operator = condition["operator"]
        threshold = condition.get("value")
        time_unit = condition.get("time_unit", "days")
        
        # Get field value from SOW
        if field == "margin_percentage":
            field_value = sow.get("margin_percentage", 0)
        
        elif field == "has_resources":
            # Count of resource assignments
            field_value = len(sow.get("selected_resource_assignment_ids", []))
        
        elif field == "expected_completion_date_set":
            # Boolean check if date is set
            field_value = bool(sow.get("expected_completion_date"))
        
        elif field == "sow_status":
            field_value = sow.get("status", "not_started")
        
        elif field == "total_days_in_sales_ops":
            created_at = sow.get("created_at") or opportunity.get("created_at")
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            elif created_at and not created_at.tzinfo:
                created_at = created_at.replace(tzinfo=timezone.utc)
            
            if created_at:
                field_value = (datetime.now(timezone.utc) - created_at).days
            else:
                field_value = 0
        else:
            return False
        
        # Convert threshold if time-based
        if time_unit and field in ["total_days_in_sales_ops"]:
            threshold = self._convert_to_days(threshold, time_unit)
        
        # Safety check: if field_value is None, return False (don't trigger)
        if field_value is None:
            return False
        
        # Evaluate operator
        if operator == "greater_than":
            return field_value > threshold
        elif operator == "less_than":
            return field_value < threshold
        elif operator == "equals":
            return field_value == threshold
        elif operator == "between":
            return threshold <= field_value <= condition.get("value_max", float('inf'))
        elif operator == "not_equals":
            return field_value != threshold
        
        return False
    
    def _convert_to_days(self, value: float, unit: str) -> float:
        """Convert time value to days based on unit"""
        conversions = {
            "hours": value / 24,
            "days": value,
            "weeks": value * 7,
            "months": value * 30  # Approximate
        }
        return conversions.get(unit, value)
    
    def _get_field_value(self, field: str, opportunity: Dict) -> Any:
        """Extract field value from opportunity data"""
        
        # Direct fields
        if field == "opportunity_value":
            return opportunity.get("opportunity_value", 0)
        
        # Shadow logic fields (NEW)
        elif field == "has_missing_contractors":
            # Check if any resource assignments are missing contractors
            sows = opportunity.get("sows", [])
            for sow in sows:
                assignment_ids = sow.get("selected_resource_assignment_ids", [])
                if assignment_ids:
                    # This would need to fetch assignments - for now return False
                    # In production, pass assignments as part of opportunity enrichment
                    pass
            return False  # Placeholder - needs enrichment
        
        elif field == "expected_completion_date_exists":
            sows = opportunity.get("sows", [])
            return any(sow.get("expected_completion_date") for sow in sows)
        
        elif field == "sow_status":
            sows = opportunity.get("sows", [])
            if sows:
                return sows[0].get("status", "not_started")
            return "not_started"
        
        # Time-based calculations
        elif field == "days_in_stage":
            stage_date = opportunity.get("stage_changed_at")
            if stage_date:
                if isinstance(stage_date, str):
                    stage_date = datetime.fromisoformat(stage_date.replace("Z", "+00:00"))
                return (datetime.now(timezone.utc) - stage_date).days
            return 0
        
        elif field == "total_days_in_sales_ops":
            created_at = opportunity.get("created_at")
            if created_at:
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                elif not created_at.tzinfo:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                return (datetime.now(timezone.utc) - created_at).days
            return 0
        
        elif field == "days_until_expected_completion":
            sows = opportunity.get("sows", [])
            if sows:
                # Check earliest expected completion date
                dates = [sow.get("expected_completion_date") for sow in sows if sow.get("expected_completion_date")]
                if dates:
                    earliest = min(dates)
                    if isinstance(earliest, str):
                        earliest = datetime.fromisoformat(earliest)
                    return (earliest - datetime.now(timezone.utc)).days
            return 999  # No date set
        
        elif field == "days_since_last_activity":
            updated_at = opportunity.get("updated_at")
            if updated_at:
                if isinstance(updated_at, str):
                    updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                return (datetime.now(timezone.utc) - updated_at).days
            return 0
        
        # Margin calculations
        elif field == "margin_percentage":
            return self._calculate_opportunity_margin(opportunity)
        
        # Resource counts
        elif field == "resource_count":
            sows = opportunity.get("sows", [])
            total_resources = 0
            for sow in sows:
                total_resources += len(sow.get("selected_resource_assignment_ids", []))
            return total_resources
        
        # SOW status checks
        elif field == "has_blockers":
            sows = opportunity.get("sows", [])
            for sow in sows:
                if sow.get("has_blockers", False):
                    return True
            return False
        
        # Attachment checks
        elif field == "new_attachments_24h":
            attachments = opportunity.get("attachments", [])
            recent = []
            now = datetime.now(timezone.utc)
            for att in attachments:
                uploaded = att.get("uploaded_at")
                if uploaded:
                    if isinstance(uploaded, str):
                        uploaded = datetime.fromisoformat(uploaded.replace("Z", "+00:00"))
                    if (now - uploaded).total_seconds() < 86400:  # 24 hours
                        recent.append(att)
            return len(recent)
        
        # User assignment load
        elif field == "assignee_opportunity_count":
            assigned_to = opportunity.get("assigned_to")
            if assigned_to:
                # Count total opportunities assigned to this user in active stages
                count = self.db.opportunities.count_documents({
                    "assigned_to": assigned_to,
                    "stage": {"$in": ["discovery", "proposal_development", "negotiation"]}
                })
                return count
            return 0
        
        return None
    
    def _calculate_opportunity_margin(self, opportunity: Dict) -> float:
        """Calculate weighted average margin across all SOW resources"""
        sows = opportunity.get("sows", [])
        total_revenue = 0
        total_cost = 0
        
        for sow in sows:
            # Get resource assignments for this SOW
            resources = sow.get("resource_assignments", [])  # Assuming denormalized or fetched
            for resource in resources:
                total_revenue += resource.get("total_sell", 0)
                total_cost += resource.get("total_cost", 0)
        
        if total_revenue == 0:
            return 0
        
        return ((total_revenue - total_cost) / total_revenue) * 100
    
    async def _execute_rule_actions(self, rule: Dict, opportunity: Dict, sow: Dict = None) -> Dict:
        """
        Execute all actions defined in the rule.
        Supports: banner, block_stage, stage_transition, email, update_field, create_task
        
        Returns:
            Alert document (if banner action exists), None otherwise
        """
        alert_to_create = None
        
        for action in rule.get("actions", []):
            action_type = action["action_type"]
            
            if action_type == "banner":
                # Prepare alert message
                message = action.get("banner_message") or self._build_alert_message(rule, opportunity, sow)
                severity = action.get("severity", "warning")
                
                # Create alert document with SOW context
                alert_to_create = {
                    "alert_id": f"alert_{datetime.now().timestamp()}".replace(".", "_"),
                    "rule_id": rule["rule_id"],
                    "rule_name": rule["name"],
                    "opportunity_id": opportunity["opportunity_id"],
                    "opportunity_name": opportunity["name"],
                    "sow_id": sow.get("sow_id") if sow else None,
                    "line_of_business": sow.get("line_of_business") if sow else None,
                    "severity": severity,
                    "message": message,
                    "triggered_at": datetime.now(timezone.utc),
                    "trigger_values": self._extract_trigger_values(rule, opportunity, sow),
                    "acknowledged": False,
                    "dismissed": False
                }
            
            elif action_type == "block_stage":
                # Stage blocking is enforced at transition time via check_stage_blocks()
                # This action type doesn't execute here, but is checked when user tries to change status
                pass
            
            elif action_type == "stage_transition":
                # Auto-move SOW to target stage
                target_stage = action.get("target_stage")
                if sow and target_stage:
                    await self.db.opportunity_sows.update_one(
                        {"sow_id": sow["sow_id"]},
                        {"$set": {
                            "status": target_stage,
                            "updated_at": datetime.now(timezone.utc)
                        }}
                    )
                    logger.info(f"Auto-transitioned SOW {sow['sow_id']} to {target_stage}")
            
            elif action_type in ["email", "send_email"]:
                # Email notification
                await self._send_email_notification(action, opportunity, sow)
            
            elif action_type == "update_field":
                # Update a field value
                field_name = action.get("field_name")
                field_value = action.get("field_value")
                if sow and field_name:
                    await self.db.opportunity_sows.update_one(
                        {"sow_id": sow["sow_id"]},
                        {"$set": {field_name: field_value, "updated_at": datetime.now(timezone.utc)}}
                    )
                    logger.info(f"Auto-updated {field_name} to {field_value} for SOW {sow['sow_id']}")
            
            elif action_type == "create_task":
                # Create follow-up task
                logger.info(f"Task creation triggered for rule {rule['rule_id']}")
                # TODO: Implement task creation logic
        
        # Store alert if banner action exists
        if alert_to_create:
            await self.db.triggered_alerts.insert_one(alert_to_create)
            return alert_to_create
        
        return None
    
    def _build_alert_message(self, rule: Dict, opportunity: Dict, sow: Dict = None) -> str:
        """Build human-readable alert message with SOW context"""
        trigger_type = rule.get("trigger_type", "")
        
        # Add SOW context prefix if applicable
        sow_prefix = ""
        if sow:
            lob_labels = {
                "field_services": "Field Services",
                "professional_services": "Professional Services",
                "wholesale_mrr": "Wholesale MRR",
                "referral_mrr": "Referral MRR"
            }
            lob_name = lob_labels.get(sow.get("line_of_business"), sow.get("line_of_business", ""))
            sow_prefix = f"[{lob_name}] "
        
        if trigger_type == "days_in_stage":
            days = self._get_field_value("days_in_stage", opportunity)
            return f"{sow_prefix}Opportunity in '{opportunity.get('stage')}' stage for {days} days"
        
        elif trigger_type == "margin_below":
            if sow:
                margin = sow.get("margin_percentage", 0)
                return f"{sow_prefix}Margin is {margin:.1f}% (below threshold)"
            else:
                margin = self._get_field_value("margin_percentage", opportunity)
                return f"Margin is {margin:.1f}% (below threshold)"
        
        elif trigger_type == "margin_above":
            if sow:
                margin = sow.get("margin_percentage", 0)
                return f"{sow_prefix}Excellent margin: {margin:.1f}%"
            else:
                margin = self._get_field_value("margin_percentage", opportunity)
                return f"Excellent margin: {margin:.1f}%"
        
        elif trigger_type == "value_above":
            value = opportunity.get("opportunity_value", 0)
            return f"High-value opportunity: ${value:,.0f}"
        
        elif trigger_type == "blockers_detected":
            return f"{sow_prefix}SOW has blockers (missing contractors or negative margins)"
        
        elif trigger_type == "new_attachments":
            count = self._get_field_value("new_attachments_24h", opportunity)
            return f"{count} new attachment(s) in last 24 hours"
        
        elif trigger_type == "expected_completion_approaching":
            days = self._get_field_value("days_until_expected_completion", opportunity)
            return f"Expected completion in {days} days"
        
        return f"{sow_prefix}{rule.get('name', 'Alert triggered')}"
    
    def _extract_trigger_values(self, rule: Dict, opportunity: Dict, sow: Dict = None) -> Dict:
        """Extract relevant field values for alert context"""
        values = {}
        for condition in rule.get("conditions", []):
            field = condition["field"]
            
            # Extract from SOW if applicable
            if sow and field in ["margin_percentage", "total_days_in_sales_ops", "sow_status"]:
                if field == "margin_percentage":
                    values[field] = sow.get("margin_percentage", 0)
                elif field == "sow_status":
                    values[field] = sow.get("status", "not_started")
                elif field == "total_days_in_sales_ops":
                    created_at = sow.get("created_at") or opportunity.get("created_at")
                    if isinstance(created_at, str):
                        created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    elif created_at and not created_at.tzinfo:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    
                    if created_at:
                        values[field] = (datetime.now(timezone.utc) - created_at).days
                    else:
                        values[field] = 0
            else:
                values[field] = self._get_field_value(field, opportunity)
        return values
    
    async def _send_email_notification(self, action: Dict, opportunity: Dict, sow: Dict = None):
        """Send email notification (MOCKED for now)"""
        recipients = action.get("recipients", [])
        sow_context = f" - {sow.get('line_of_business')}" if sow else ""
        logger.info(f"📧 MOCKED EMAIL: To {recipients}, Subject: Alert for {opportunity['name']}{sow_context}")
        # TODO: Integrate with Resend/SendGrid
    
    async def _auto_change_status(self, action: Dict, opportunity: Dict):
        """Automatically change opportunity/SOW status"""
        new_status = action.get("new_status")
        if new_status:
            logger.info(f"🤖 AUTO-ACTION: Changing opportunity {opportunity['opportunity_id']} to status {new_status}")
            # TODO: Update opportunity status in database
    
    async def check_stage_blocks(self, opportunity_id: str, sow_id: str, target_stage: str) -> Dict:
        """
        Check if any automation rules block transitioning to target_stage.
        Called before allowing SOW status updates.
        
        Args:
            opportunity_id: Opportunity ID
            sow_id: SOW ID
            target_stage: The stage user is trying to move to
        
        Returns:
            {
                "blocked": bool,
                "reasons": [{"rule_name": "...", "reason": "..."}]
            }
        """
        # Fetch opportunity and SOW
        opportunity = await self.db.opportunities.find_one({"opportunity_id": opportunity_id}, {"_id": 0})
        sow = await self.db.opportunity_sows.find_one({"sow_id": sow_id}, {"_id": 0})
        
        if not opportunity or not sow:
            return {"blocked": False, "reasons": []}
        
        # Fetch enabled rules with block_stage actions
        rules = await self.db.automation_rules.find({"enabled": True}, {"_id": 0}).to_list(1000)
        
        blocking_reasons = []
        
        for rule in rules:
            # Check if rule applies to this opportunity
            if not self._rule_applies(rule, opportunity):
                continue
            
            # Check if rule has block_stage actions
            has_block_action = any(
                action.get("action_type") == "block_stage" and 
                target_stage in action.get("blocked_stages", [])
                for action in rule.get("actions", [])
            )
            
            if not has_block_action:
                continue
            
            # Check preconditions
            if not self._evaluate_preconditions(rule, sow, opportunity):
                continue
            
            # Check if conditions are met (this means blocking is active)
            if self._evaluate_sow_conditions(rule, sow, opportunity):
                # Find the block action to get the reason
                for action in rule.get("actions", []):
                    if action.get("action_type") == "block_stage" and target_stage in action.get("blocked_stages", []):
                        blocking_reasons.append({
                            "rule_id": rule["rule_id"],
                            "rule_name": rule["name"],
                            "reason": action.get("block_reason") or f"Blocked by rule: {rule['name']}"
                        })
                        break
        
        return {
            "blocked": len(blocking_reasons) > 0,
            "reasons": blocking_reasons
        }

# ==================== AUTO-ACTIONS (Built-in) ====================

async def check_expected_completion_auto_progress(db: AsyncIOMotorClient, opportunity_id: str, sow_id: str):
    """
    Built-in automation: When Expected Completion Date is filled, move SOW to 'In Progress'
    """
    sow = await db.opportunities.find_one(
        {"opportunity_id": opportunity_id, "sows.sow_id": sow_id},
        {"_id": 0, "sows.$": 1}
    )
    
    if sow and sow.get("sows"):
        sow_data = sow["sows"][0]
        
        # Check if expected_completion_date is filled and status is 'not_started'
        if sow_data.get("expected_completion_date") and sow_data.get("status") == "not_started":
            # Auto-advance to 'in_progress'
            await db.opportunities.update_one(
                {"opportunity_id": opportunity_id, "sows.sow_id": sow_id},
                {"$set": {"sows.$.status": "in_progress"}}
            )
            logger.info(f"🤖 AUTO-PROGRESS: SOW {sow_id} moved to 'In Progress' (Expected Completion Date filled)")
            return True
    
    return False

async def check_all_fields_complete_automation(db: AsyncIOMotorClient, opportunity_id: str):
    """
    Built-in automation: When all required fields complete, trigger notification
    """
    opportunity = await db.opportunities.find_one({"opportunity_id": opportunity_id}, {"_id": 0})
    if not opportunity:
        return False
    
    # Define required fields
    required_fields = [
        "name", "account_name", "opportunity_value", "desired_proposal_date",
        "smart_specific", "smart_measurable", "smart_achievable", "smart_relevant", "smart_time_bound"
    ]
    
    # Check if all required fields are filled
    all_complete = all(opportunity.get(field) for field in required_fields)
    
    # Check if at least one SOW exists with resources
    sows = opportunity.get("sows", [])
    has_resources = any(len(sow.get("selected_resource_assignment_ids", [])) > 0 for sow in sows)
    
    if all_complete and has_resources:
        # Create success alert
        alert_doc = {
            "alert_id": f"alert_{datetime.now().timestamp()}".replace(".", "_"),
            "rule_id": "builtin_all_fields_complete",
            "rule_name": "All Fields Complete",
            "opportunity_id": opportunity_id,
            "opportunity_name": opportunity["name"],
            "severity": "success",
            "message": "Opportunity ready for review - all required fields complete",
            "triggered_at": datetime.now(timezone.utc),
            "trigger_values": {"fields_complete": True, "has_resources": True},
            "acknowledged": False,
            "dismissed": False
        }
        
        # Check if this alert already exists
        existing = await db.triggered_alerts.find_one({
            "opportunity_id": opportunity_id,
            "rule_id": "builtin_all_fields_complete",
            "dismissed": False
        })
        
        if not existing:
            await db.triggered_alerts.insert_one(alert_doc)
            logger.info(f"✅ ALL FIELDS COMPLETE: Created success alert for {opportunity_id}")
            return True
    
    return False
