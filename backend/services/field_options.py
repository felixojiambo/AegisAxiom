"""
Policy Engine — Centralized Field Options

Single source of truth for all picklist/dropdown values used across the app.
Both the field registry (for the policy admin GUI) and any future form schema
endpoints can import from here.

To add a new option to an existing field: add it here. It automatically appears
in the policy admin condition builder.

To add options for a new field: add a new constant here and reference it in
the module's register_fields() call.
"""
from models import UserRole


def _opts(values: list) -> list:
    """Convert a list of (value, label) tuples or plain strings into options format."""
    result = []
    for v in values:
        if isinstance(v, tuple):
            result.append({"value": v[0], "label": v[1]})
        else:
            result.append({"value": v, "label": v.replace("_", " ").title()})
    return result


# ==================== USER / AUTH ====================

USER_ROLES = _opts([
    (r, r.replace("_", " ").title()) for r in UserRole.ALL_ROLES
])

# ==================== TIME TRACKING ====================

TIME_ENTRY_STATUSES = _opts([
    ("draft", "Draft"),
    ("submitted", "Submitted"),
    ("pm_approved", "PM Approved"),
    ("admin_approved", "Admin Approved"),
    ("rejected", "Rejected"),
])

NOTE_QUALITY = _opts(["good", "fair", "poor"])

# ==================== SALES OPS ====================

SOW_STATUSES = _opts([
    ("not_started", "Not Started"),
    ("in_progress", "In Progress"),
    ("blocked", "Blocked"),
    ("ready_for_review", "Ready for Review"),
    ("complete", "Complete"),
    ("sent_to_client", "Sent to Client"),
])

APPROVAL_STATUSES = _opts([
    ("pending", "Pending"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
])

PROPOSAL_STATUSES = _opts([
    ("draft", "Draft"),
    ("ready_for_review", "Ready for Review"),
    ("sent", "Handed off to Sales"),
    ("client_sent", "Sent to client"),
    ("accepted", "Accepted"),
    ("rejected", "Rejected"),
])

APPROVAL_ACTIONS = _opts([
    ("approve", "Approve"),
    ("reject", "Reject"),
])

# ==================== FIELD SERVICES ====================

FIELD_JOB_STATUSES = _opts([
    ("scheduled", "Scheduled"),
    ("confirmed", "Confirmed"),
    ("in_progress", "In Progress"),
    ("completed", "Completed"),
    ("cancelled", "Cancelled"),
])

FIELD_JOB_PRIORITIES = _opts(["low", "medium", "high", "urgent"])

FIELD_JOB_CATEGORIES = _opts([
    ("survey", "Survey"),
    ("execution", "Execution"),
])

# ==================== CONTRACTS ====================

CONTRACT_STATUSES = _opts([
    ("draft", "Draft"),
    ("active", "Active"),
    ("completed", "Completed"),
    ("cancelled", "Cancelled"),
])

CONTRACT_TYPES = _opts([
    ("boh", "Block of Hours"),
    ("boa", "Burn on Actual"),
    ("fixed_fee", "Fixed Fee"),
    ("monthly", "Monthly"),
    ("boh_with_boa", "Block of Hours + Bill on Actuals"),
])

# Approval lifecycle for Contracts. Distinct from APPROVAL_STATUSES above
# (which is a generic 3-state used by SOWs) so that the contract module
# can add changes_requested without affecting the SOW set.
CONTRACT_APPROVAL_STATUSES = _opts([
    ("pending", "Pending"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
    ("changes_requested", "Changes Requested"),
])

# Per-time-entry billing classification. Used by Wave 3 derivation; in
# Wave 0 only the option list is registered so the policy admin UI and
# the form schema endpoint can see the values.
BILLING_TYPES = _opts([
    ("billable", "Billable"),
    ("non_billable", "Non-billable"),
    ("fixed_fee", "Fixed Fee"),
])

# Invoice batch lifecycle for the bi-weekly Finance Command Center
# approval queue. State machine: draft -> pending_finance_approval ->
# approved -> sent_to_qbo, with rejected as a terminal alt-state.
BATCH_STATUSES = _opts([
    ("draft", "Draft"),
    ("pending_finance_approval", "Pending Finance Approval"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
    ("sent_to_qbo", "Sent to QBO"),
])

PAYMENT_TERMS = _opts(["net_7", "net_14", "net_30", "net_45", "net_60"])

BILLING_FREQUENCIES = _opts(["weekly", "biweekly", "monthly"])

# Wave 4 (contracts-time-mgmt) — billing trigger configuration.
# Triggers fire side-effect actions when a contract crosses a
# milestone (date, percent consumed, hours remaining, block exhausted,
# or biweekly cycle marker). Wave 4 wires the evaluator and CRUD;
# action handlers degrade gracefully when their dependencies are not
# yet configured (see services/contract_trigger_evaluator.py).
CONTRACT_BILLING_TRIGGER_TYPES = _opts([
    ("date", "Date"),
    ("pct_consumed", "Percent Consumed"),
    ("remaining_threshold", "Remaining Hours Threshold"),
    ("block_exhausted", "Block Exhausted"),
    ("biweekly_cycle", "Biweekly Cycle Marker"),
])

CONTRACT_BILLING_TRIGGER_STATES = _opts([
    ("armed", "Armed"),
    ("fired", "Fired"),
    ("disabled", "Disabled"),
])

CONTRACT_BILLING_TRIGGER_ACTIONS = _opts([
    ("notify_finance", "Notify Finance"),
    ("create_task", "Create Internal Task"),
    ("queue_invoice_batch", "Queue Invoice Batch"),
])

# Wave 4 (contracts-time-mgmt) — contract adjustment ledger.
# Captures hours / billing credits and debits with a reason code so
# the FCC audit can answer "why did this contract's balance change
# outside of an approved time entry?".
CONTRACT_ADJUSTMENT_KINDS = _opts([
    ("hours_credit", "Hours Credit"),
    ("hours_debit", "Hours Debit"),
    ("billing_credit", "Billing Credit"),
    ("billing_debit", "Billing Debit"),
])

CONTRACT_ADJUSTMENT_REASON_CODES = _opts([
    ("late_timesheet", "Late Timesheet Correction"),
    ("scope_change", "Scope Change"),
    ("customer_credit", "Customer Credit"),
    ("correction", "Correction"),
    ("goodwill", "Goodwill"),
    ("manual_reconcile", "Manual Reconcile"),
    ("other", "Other"),
])

# ==================== CONTRACTOR MANAGEMENT ====================

CONTRACTOR_BUSINESS_STATUSES = _opts([
    ("active", "Active"),
    ("inactive", "Inactive"),
])

PARTNERSHIP_TIERS = _opts(["none", "bronze", "silver", "gold", "platinum"])

# ==================== FINANCE ====================

INVOICE_STATUSES = _opts([
    ("draft", "Draft"),
    ("pending_approval", "Pending Approval"),
    ("approved", "Approved"),
    ("sent", "Sent"),
    ("paid", "Paid"),
    ("overdue", "Overdue"),
    ("cancelled", "Cancelled"),
])

AP_BILL_STATUSES = _opts([
    ("draft", "Draft"),
    ("pending_finance_review", "Pending Finance Review"),
    ("approved", "Approved"),
    ("paid", "Paid"),
    ("cancelled", "Cancelled"),
])

COMMISSION_STATUSES = _opts(["pending", "approved", "paid", "rejected"])

# ==================== PROJECT MANAGEMENT ====================

PROJECT_STATUSES = _opts(["planning", "active", "on_hold", "completed", "cancelled"])

PROJECT_HEALTH = _opts(["green", "yellow", "red"])

TASK_STATUSES = _opts(["not_started", "in_progress", "completed"])

TASK_PRIORITIES = _opts(["critical", "high", "medium", "low"])
