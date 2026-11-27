"""
Policy Engine — System Policy Seeds

Seeds 16 system policies on startup. These replace hardcoded business rules
that were previously scattered across route handlers.

Idempotent: uses policy_id as the key. On re-seed, updates the definition
but preserves the user's `enabled` setting.
"""
import logging

logger = logging.getLogger(__name__)

SYSTEM_POLICIES = [
    # ==================== TIME TRACKING ====================
    {
        "policy_id": "sys_time_locking",
        "name": "Time Entry Week Locking",
        "description": "Prevents time entry creation/edits on locked weeks. Weeks lock 8 days after the week starts at 10 PM UTC. Admin-permissioned users are exempt.",
        "module": "time_tracking",
        "entity": "time_entry",
        "trigger": {"event": "on_create"},
        "condition_logic": "AND",
        "conditions": [
            {"field": "is_locked", "operator": "is_true", "value": None, "value_type": "boolean"},
            {"field": "user_has_admin_perm", "operator": "is_false", "value": None, "value_type": "boolean"},
        ],
        "actions": [
            {"type": "block", "message": "This week's time entries are locked. Contact an admin if you need to make changes.", "field": None},
        ],
        "priority": 10,
    },
    {
        "policy_id": "sys_time_locking_update",
        "name": "Time Entry Week Locking (Updates)",
        "description": "Prevents editing time entries on locked weeks. Same rules as creation locking.",
        "module": "time_tracking",
        "entity": "time_entry",
        "trigger": {"event": "on_update"},
        "condition_logic": "AND",
        "conditions": [
            {"field": "is_locked", "operator": "is_true", "value": None, "value_type": "boolean"},
            {"field": "user_has_admin_perm", "operator": "is_false", "value": None, "value_type": "boolean"},
        ],
        "actions": [
            {"type": "block", "message": "This week's time entries are locked. Contact an admin if you need to make changes.", "field": None},
        ],
        "priority": 10,
    },
    {
        "policy_id": "sys_time_max_hours",
        "name": "Max 4 Hours Per Time Entry",
        "description": "Limits each time entry to a maximum of 4 hours to limit dispute exposure.",
        "module": "time_tracking",
        "entity": "time_entry",
        "trigger": {"event": "on_submit"},
        "condition_logic": "AND",
        "conditions": [
            {"field": "hours", "operator": "greater_than", "value": 4.0, "value_type": "number"},
        ],
        "actions": [
            {"type": "block", "message": "Maximum 4 hours per time entry to limit dispute exposure.", "field": None},
        ],
        "priority": 9,
    },
    {
        "policy_id": "sys_time_increments",
        "name": "10-Minute Increments",
        "description": "Time entries must be in 10-minute increments (0.17, 0.33, 0.50, etc.).",
        "module": "time_tracking",
        "entity": "time_entry",
        "trigger": {"event": "on_submit"},
        "condition_logic": "AND",
        "conditions": [
            {"field": "hours_minutes_remainder", "operator": "greater_than", "value": 0.5, "value_type": "number"},
        ],
        "actions": [
            {"type": "block", "message": "Hours must be in 10-minute increments (0.17, 0.33, 0.50, 0.67, 0.83, 1.00, etc.).", "field": None},
        ],
        "priority": 9,
    },
    {
        "policy_id": "sys_time_note_quality",
        "name": "Note Quality Minimum",
        "description": "Time entry notes must contain at least 3 words describing the work performed.",
        "module": "time_tracking",
        "entity": "time_entry",
        "trigger": {"event": "on_submit"},
        "condition_logic": "AND",
        "conditions": [
            {"field": "note_word_count", "operator": "less_than", "value": 3, "value_type": "number"},
        ],
        "actions": [
            {"type": "block", "message": "Please provide more detail in your notes (at least 3 words describing what you worked on).", "field": None},
        ],
        "priority": 8,
    },
    {
        "policy_id": "sys_time_status_edit",
        "name": "Edit Only Draft/Rejected Entries",
        "description": "Time entries can only be edited when in draft or rejected status.",
        "module": "time_tracking",
        "entity": "time_entry",
        "trigger": {"event": "on_update"},
        "condition_logic": "AND",
        "conditions": [
            {"field": "status", "operator": "not_in", "value": ["draft", "rejected"], "value_type": "list"},
        ],
        "actions": [
            {"type": "block", "message": "Only draft or rejected time entries can be edited.", "field": None},
        ],
        "priority": 10,
    },

    # ==================== SALES OPS — SOW ====================
    {
        "policy_id": "sys_sow_valid_statuses",
        "name": "SOW Valid Statuses",
        "description": "SOW status must be one of the valid workflow states.",
        "module": "sales_ops",
        "entity": "sow",
        "trigger": {"event": "on_status_change"},
        "condition_logic": "AND",
        "conditions": [
            {"field": "target_status", "operator": "not_in", "value": ["not_started", "in_progress", "blocked", "ready_for_review", "complete"], "value_type": "list"},
        ],
        "actions": [
            {"type": "block", "message": "Invalid SOW status. Must be one of: not_started, in_progress, blocked, ready_for_review, complete.", "field": None},
        ],
        "priority": 10,
    },
    {
        "policy_id": "sys_sow_missing_contractor",
        "name": "SOW Missing Contractor Block",
        "description": "Prevents moving SOW to ready_for_review or complete when resource assignments are missing contractors.",
        "module": "sales_ops",
        "entity": "sow",
        "trigger": {"event": "on_status_change"},
        "condition_logic": "AND",
        "conditions": [
            {"field": "target_status", "operator": "in", "value": ["ready_for_review", "complete"], "value_type": "list"},
            {"field": "has_missing_contractors", "operator": "is_true", "value": None, "value_type": "boolean"},
        ],
        "actions": [
            {"type": "block", "message": "Cannot proceed — one or more resource assignments are missing a contractor. Assign contractors to all resources before moving forward.", "field": None},
        ],
        "priority": 9,
    },
    {
        "policy_id": "sys_sow_no_resources_complete",
        "name": "SOW Block Complete Without Resources",
        "description": "Prevents marking a SOW as complete when no resources have been assigned.",
        "module": "sales_ops",
        "entity": "sow",
        "trigger": {"event": "on_status_change"},
        "condition_logic": "AND",
        "conditions": [
            {"field": "target_status", "operator": "equals", "value": "complete", "value_type": "string"},
            {"field": "resource_count", "operator": "equals", "value": 0, "value_type": "number"},
        ],
        "actions": [
            {"type": "block", "message": "Cannot mark SOW as complete without assigning at least one resource.", "field": None},
        ],
        "priority": 9,
    },

    # ==================== FIELD SERVICES ====================
    {
        "policy_id": "sys_fs_operating_hours",
        "name": "Customer Operating Hours Validation",
        "description": "Blocks scheduling field jobs outside of the customer's operating hours or on closed days.",
        "module": "field_services",
        "entity": "field_job",
        "trigger": {"event": "on_create"},
        "condition_logic": "OR",
        "conditions": [
            {"field": "customer_closed_today", "operator": "is_true", "value": None, "value_type": "boolean"},
            {"field": "scheduled_start_before_open", "operator": "is_true", "value": None, "value_type": "boolean"},
            {"field": "scheduled_end_after_close", "operator": "is_true", "value": None, "value_type": "boolean"},
        ],
        "actions": [
            {"type": "block", "message": "Schedule violates customer operating hours. The customer is either closed on this day or the job falls outside their business hours.", "field": None},
        ],
        "priority": 8,
    },
    {
        "policy_id": "sys_fs_capacity",
        "name": "Scheduling Capacity Check",
        "description": "Blocks scheduling when estimated hours exceed the available window.",
        "module": "field_services",
        "entity": "field_job",
        "trigger": {"event": "on_create"},
        "condition_logic": "AND",
        "conditions": [
            {"field": "exceeds_available_hours", "operator": "is_true", "value": None, "value_type": "boolean"},
        ],
        "actions": [
            {"type": "block", "message": "Estimated hours exceed the available scheduling window for this day.", "field": None},
        ],
        "priority": 7,
    },

    # ==================== CONTRACTOR MANAGEMENT ====================
    {
        "policy_id": "sys_contractor_biz_deletion",
        "name": "Contractor Business Deletion Guard",
        "description": "Prevents deleting a contractor business that has active contractors assigned.",
        "module": "contractor_management",
        "entity": "contractor_business",
        "trigger": {"event": "on_delete"},
        "condition_logic": "AND",
        "conditions": [
            {"field": "active_contractor_count", "operator": "greater_than", "value": 0, "value_type": "number"},
        ],
        "actions": [
            {"type": "block", "message": "Cannot delete a contractor business with active contractors. Reassign or deactivate contractors first.", "field": None},
        ],
        "priority": 10,
    },
    {
        "policy_id": "sys_contractor_resource_pending",
        "name": "Resource Type Requires Approval",
        "description": "Warns that new contractor resource type assignments require Procurement approval.",
        "module": "contractor_management",
        "entity": "contractor_resource_type",
        "trigger": {"event": "on_create"},
        "condition_logic": "AND",
        "conditions": [],
        "actions": [
            {"type": "warn", "message": "New resource types require Procurement approval before activation.", "field": None},
        ],
        "priority": 5,
    },

    # ==================== CONTRACTS ====================
    {
        "policy_id": "sys_contract_cost_rate",
        "name": "Cost Rate Update Restriction",
        "description": "Blocks cost rate changes on contracts without the contracts.update_cost_rates permission.",
        "module": "contracts",
        "entity": "contract",
        "trigger": {"event": "on_update"},
        "condition_logic": "AND",
        "conditions": [
            {"field": "cost_rate_changed", "operator": "is_true", "value": None, "value_type": "boolean"},
            {"field": "user_has_cost_rate_perm", "operator": "is_false", "value": None, "value_type": "boolean"},
        ],
        "actions": [
            {"type": "block", "message": "You do not have permission to update cost rates on contracts.", "field": None},
        ],
        "priority": 10,
    },

    # ==================== SALES OPS — SERVICE APPROVAL ====================
    {
        "policy_id": "sys_service_sell_price",
        "name": "Service Approval Requires Sell Price",
        "description": "Blocks approval of contractor services without setting a sell price.",
        "module": "sales_ops",
        "entity": "service_approval",
        "trigger": {"event": "on_update"},
        "condition_logic": "OR",
        "conditions": [
            {"field": "sell_price", "operator": "is_empty", "value": None, "value_type": "number"},
            {"field": "sell_price", "operator": "less_than", "value": 0.01, "value_type": "number"},
        ],
        "actions": [
            {"type": "block", "message": "A sell price must be set before approving a contractor service.", "field": None},
        ],
        "priority": 9,
    },

    # ==================== SALES OPS — MARGIN POLICIES ====================
    {
        "policy_id": "sys_sow_negative_margin",
        "name": "Negative Margin Block",
        "description": "Blocks SOW updates when margin is negative and resources are assigned.",
        "module": "sales_ops",
        "entity": "sow",
        "trigger": {"event": "on_update"},
        "condition_logic": "AND",
        "conditions": [
            {"field": "margin_percentage", "operator": "less_than", "value": 0, "value_type": "number"},
            {"field": "has_resources", "operator": "is_true", "value": None, "value_type": "boolean"},
        ],
        "actions": [
            {"type": "block", "message": "Negative margin detected. Review resource pricing before proceeding.", "field": None},
        ],
        "priority": 8,
    },
    {
        "policy_id": "sys_sow_low_margin",
        "name": "Low Margin Warning",
        "description": "Warns when SOW margin is below 20% but still positive.",
        "module": "sales_ops",
        "entity": "sow",
        "trigger": {"event": "on_update"},
        "condition_logic": "AND",
        "conditions": [
            {"field": "margin_percentage", "operator": "less_than", "value": 20, "value_type": "number"},
            {"field": "margin_percentage", "operator": "greater_than", "value": -0.01, "value_type": "number"},
            {"field": "has_resources", "operator": "is_true", "value": None, "value_type": "boolean"},
        ],
        "actions": [
            {"type": "warn", "message": "Low margin detected (below 20%). Consider reviewing pricing.", "field": None},
        ],
        "priority": 7,
    },
]


async def seed_system_policies(db=None, *, session=None):
    """
    Seed all system policies. Idempotent — upserts by policy_id.
    Preserves the user's `enabled` setting on existing policies.

    Phase 2b: one ``INSERT … ON CONFLICT (policy_id) DO UPDATE`` per policy
    (PolicyRepository.upsert_system_policy): a conflict rewrites the
    definition, forces is_system / un-deletes, and keeps the operator's
    ``enabled`` plus the original ``created_by`` / ``created_at``. ``db`` (the
    old Mongo handle) is accepted and ignored; without ``session`` the seed
    runs in its own transaction and commits.
    """
    from db.session import get_sessionmaker
    from repositories.policies import PolicyRepository

    seeded = 0
    updated = 0

    async def _run(s) -> None:
        nonlocal seeded, updated
        repo = PolicyRepository(s)
        for policy_def in SYSTEM_POLICIES:
            if await repo.upsert_system_policy(policy_def):
                seeded += 1
            else:
                updated += 1

    if session is not None:
        await _run(session)
    else:
        async with get_sessionmaker()() as own:
            try:
                await _run(own)
                await own.commit()
            except Exception:
                await own.rollback()
                raise

    logger.info(f"Policy Engine: {seeded} new system policies seeded, {updated} existing updated")
    return {"seeded": seeded, "updated": updated}
