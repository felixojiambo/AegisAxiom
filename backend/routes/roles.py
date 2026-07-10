from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
import uuid

from database import get_db
from auth_helper import get_current_user, has_perm, require_perm
from models import RoleCreateRequest, RoleUpdateRequest, RoleCloneRequest
from services.audit_service import log_action
from services.security_logger import log_security_event
from services.field_registry import register_fields, register_module_events, register_module_config
from services.permission_registry import (
    get_all_permissions,
    get_valid_keys,
    register_module_permissions,
)

router = APIRouter()

register_module_config("roles", "roles", "role_id", "Admin")
register_module_events("roles", "role", ["created", "updated", "deleted"])
register_fields("roles", "role", [
    {"name": "name", "type": "string", "label": "Role Name", "description": "Machine-readable unique role name"},
    {"name": "display_name", "type": "string", "label": "Display Name", "description": "Human-friendly role name shown in the UI"},
    {"name": "description", "type": "string", "label": "Description", "description": "Role purpose and responsibilities"},
    {"name": "permissions", "type": "list", "label": "Permissions", "description": "List of permission keys granted to this role"},
    {"name": "is_system", "type": "boolean", "label": "Is System Role", "description": "System roles cannot be modified or deleted"},
    {"name": "is_default", "type": "boolean", "label": "Is Default", "description": "Whether role is included in default role setup"},
    {"name": "color", "type": "string", "label": "Color", "description": "Hex color code for UI display"},
])

# ---- Deprecated finance permission keys ----
# Keys that had route handlers before the Wave 3A FCC cutover but no longer do.
# seed_system_permissions sets is_active=false on these so they disappear from
# the picker without removing the rows (which would orphan any role still
# referencing them). Kept as a separate list because they're NOT re-emitted
# as live perms — never add them back to _core_permissions().
_DEPRECATED_FINANCE_KEYS = {
    "finance.ar.edit",
    "finance.ar.mark_collected",
    "finance.ar.write_off",
    "finance.ar.export",
    "finance.ar.send_reminders",
    "finance.ap.create",
    "finance.ap.edit",
    "finance.ap.delete",
    "finance.ap.mark_paid",
    "finance.ap.approve",
    "finance.ap.export",
    "finance.ap.upload_invoice",
    "finance.past_due.view",
    "finance.past_due.edit",
    "finance.past_due.export",
    # Edit verbs declared but never implemented (no PATCH handler exists):
    "finance.coa_mapping.edit",       # Wave 4C N6 — pattern is delete-then-create
    "finance.referral_mrr.edit",      # bare; granular *_template / *_mapping verbs cover writes
    "operations.contracts.edit",      # Wave 2G spinout was read-only
}


# ---- All available permissions ----
# Seed source for the dynamic permission registry. Declared via the
# @register_module_permissions decorator so the registry picks it up at
# import time. The runtime list of valid permissions lives in MongoDB
# (see services/permission_registry.py) and is populated from this list
# at server startup. Ingested apps register their own permissions through
# the same registry without touching this file.
@register_module_permissions("core")
def _core_permissions():
    return [
        # Opportunities
        {"key": "opportunities.view.all",  "group": "Opportunities", "label": "View All Opportunities"},
        {"key": "opportunities.view.own",  "group": "Opportunities", "label": "View Own Opportunities"},
        {"key": "opportunities.create",    "group": "Opportunities", "label": "Create Opportunities"},
        {"key": "opportunities.edit.all",  "group": "Opportunities", "label": "Edit All Opportunities"},
        {"key": "opportunities.edit.own",  "group": "Opportunities", "label": "Edit Own Opportunities"},
        {"key": "opportunities.delete",    "group": "Opportunities", "label": "Delete Opportunities"},
        {"key": "opportunities.restore",   "group": "Opportunities", "label": "Restore Deleted Opportunities"},
        {"key": "opportunities.view.trash","group": "Opportunities", "label": "View Deleted Opportunities (Trash)"},
        {"key": "opportunities.export",    "group": "Opportunities", "label": "Export Opportunities"},

        # Contacts
        {"key": "contacts.view.all",  "group": "Contacts", "label": "View All Contacts"},
        {"key": "contacts.view.own",  "group": "Contacts", "label": "View Own Contacts"},
        {"key": "contacts.create",    "group": "Contacts", "label": "Create Contacts"},
        {"key": "contacts.edit.all",  "group": "Contacts", "label": "Edit All Contacts"},
        {"key": "contacts.edit.own",  "group": "Contacts", "label": "Edit Own Contacts"},
        {"key": "contacts.delete",    "group": "Contacts", "label": "Delete Contacts"},
        {"key": "contacts.export",    "group": "Contacts", "label": "Export Contacts"},

        # Accounts
        {"key": "accounts.view.all",  "group": "Accounts",      "label": "View All Accounts"},
        {"key": "accounts.view.own",  "group": "Accounts",      "label": "View Assigned Accounts"},
        {"key": "accounts.create",    "group": "Accounts",      "label": "Create Accounts"},
        {"key": "accounts.edit.all",  "group": "Accounts",      "label": "Edit All Accounts"},
        {"key": "accounts.edit.own",  "group": "Accounts",      "label": "Edit Assigned Accounts"},
        {"key": "accounts.delete",    "group": "Accounts",      "label": "Delete Accounts"},
        {"key": "accounts.import",    "group": "Accounts",      "label": "Import Accounts"},
        {"key": "accounts.export",    "group": "Accounts",      "label": "Export Account Data"},

        # Deals
        {"key": "deals.view.all",     "group": "Deals",         "label": "View All Deals"},
        {"key": "deals.view.own",     "group": "Deals",         "label": "View Assigned Deals"},
        {"key": "deals.create",       "group": "Deals",         "label": "Create Deals"},
        {"key": "deals.edit.all",     "group": "Deals",         "label": "Edit All Deals"},
        {"key": "deals.edit.own",     "group": "Deals",         "label": "Edit Assigned Deals"},
        {"key": "deals.delete",       "group": "Deals",         "label": "Delete Deals"},
        {"key": "deals.sync",         "group": "Deals",         "label": "Sync Deals from Zoho"},
        {"key": "deals.assign",       "group": "Deals",         "label": "Assign Deals to Users"},
        {"key": "deals.import",       "group": "Deals",         "label": "Import Deals from Zoho"},
        {"key": "deals.export",       "group": "Deals",         "label": "Export Deal Data"},
        {"key": "deals.stage.change", "group": "Deals",         "label": "Change Deal Stage"},
        {"key": "deals.close",        "group": "Deals",         "label": "Mark Deals as Won/Lost"},

        # Commissions
        {"key": "commissions.view.all",  "group": "Commissions", "label": "View All Commissions"},
        {"key": "commissions.view.own",  "group": "Commissions", "label": "View Own Commissions"},
        {"key": "commissions.create",    "group": "Commissions", "label": "Create Commissions"},
        {"key": "commissions.edit.all",  "group": "Commissions", "label": "Edit All Commissions"},
        {"key": "commissions.mark_paid", "group": "Commissions", "label": "Mark Commissions Paid"},
        {"key": "commissions.delete",         "group": "Commissions", "label": "Delete Commission Records"},
        {"key": "commissions.template.view",  "group": "Commissions", "label": "View Commission Templates"},
        {"key": "commissions.template.create","group": "Commissions", "label": "Create Commission Templates"},
        {"key": "commissions.template.edit",  "group": "Commissions", "label": "Edit Commission Templates"},
        {"key": "commissions.template.delete","group": "Commissions", "label": "Delete Commission Templates"},
        {"key": "commissions.plan.commit",    "group": "Commissions", "label": "Commit Commission Plans to Deals"},
        {"key": "commissions.status.change",  "group": "Commissions", "label": "Change Commission Status"},
        {"key": "commissions.export",         "group": "Commissions", "label": "Export Commission Data"},

        # Invoices
        {"key": "invoices.view.all",  "group": "Invoices",      "label": "View All Invoices"},
        {"key": "invoices.view.own",  "group": "Invoices",      "label": "View Own Invoices"},
        {"key": "invoices.create",    "group": "Invoices",      "label": "Create Invoices"},
        {"key": "invoices.edit",      "group": "Invoices",      "label": "Edit Invoices"},
        {"key": "invoices.edit.all",  "group": "Invoices",      "label": "Edit All Invoices"},
        {"key": "invoices.edit.own",  "group": "Invoices",      "label": "Edit Own Invoices"},
        {"key": "invoices.delete",    "group": "Invoices",      "label": "Delete Invoices"},
        {"key": "invoices.send",      "group": "Invoices",      "label": "Send Invoices to Clients"},
        {"key": "invoices.mark_paid", "group": "Invoices",      "label": "Mark Invoices as Paid"},
        {"key": "invoices.export",    "group": "Invoices",      "label": "Export Invoice Data"},
        {"key": "invoices.void",      "group": "Invoices",      "label": "Void Invoices"},

        # Contracts
        {"key": "contracts.view.all", "group": "Contracts",     "label": "View All Contracts"},
        {"key": "contracts.view.own", "group": "Contracts",     "label": "View Own Contracts"},
        {"key": "contracts.create",   "group": "Contracts",     "label": "Create Contracts"},
        {"key": "contracts.edit",     "group": "Contracts",     "label": "Edit Contracts"},
        {"key": "contracts.edit.all", "group": "Contracts",     "label": "Edit All Contracts"},
        {"key": "contracts.edit.own", "group": "Contracts",     "label": "Edit Own Contracts"},
        {"key": "contracts.delete",   "group": "Contracts",     "label": "Delete Contracts"},
        # Wave 1 (contracts-time-mgmt) — Finance Command Center approval queue.
        # Distinct from contracts.edit so the approval decision can be
        # delegated separately from contract authorship.
        #
        # Approver set is configurable via TWO independent paths so the
        # initial approver set can be wired up without any route-level
        # hardcoding of named business approvers:
        #
        #   1. Role grant. The permission is listed in the role's
        #      `permissions` array (admin via "*", director, finance —
        #      see the role blocks below).
        #
        #   2. Per-user override. The admin GUI / PUT /api/admin/users/{id}
        #      endpoint can write the key into a specific user's
        #      `extra_permissions` array (models.User.extra_permissions).
        #      auth_helper.get_current_user merges role permissions and
        #      extra_permissions before stashing the closure on the User
        #      object, so has_perm(user, "contracts.approve") respects
        #      both paths through a single check at the route layer.
        #
        # Sales Ops keeps create/edit but does not auto-receive approve;
        # explicit per-user grants are the supported way to authorize an
        # individual Sales Ops user.
        {"key": "contracts.approve",  "group": "Contracts",     "label": "Approve Contracts"},
        {"key": "contracts.update_cost_rates", "group": "Contracts", "label": "Update Cost Rates on Active Contracts"},
        {"key": "contracts.assign_to_deal",    "group": "Contracts", "label": "Assign Contracts to Deals"},
        {"key": "contracts.milestones.create", "group": "Contracts", "label": "Create Milestones"},
        {"key": "contracts.milestones.edit",   "group": "Contracts", "label": "Edit Milestones"},
        {"key": "contracts.milestones.delete", "group": "Contracts", "label": "Delete Milestones"},
        {"key": "contracts.products.add",      "group": "Contracts", "label": "Add Products to Contracts"},
        {"key": "contracts.products.remove",   "group": "Contracts", "label": "Remove Products from Contracts"},
        {"key": "contracts.export",            "group": "Contracts", "label": "Export Contract Data"},
        {"key": "contracts.import",            "group": "Contracts", "label": "Import Contracts"},

        # Contract Templates
        {"key": "contract_templates.view",   "group": "Contracts", "label": "View Contract Templates"},
        {"key": "contract_templates.create", "group": "Contracts", "label": "Create Contract Templates"},
        {"key": "contract_templates.edit",   "group": "Contracts", "label": "Edit Contract Templates"},
        {"key": "contract_templates.delete", "group": "Contracts", "label": "Delete Contract Templates"},

        # Projects
        {"key": "projects.view.all",  "group": "Projects",      "label": "View All Projects"},
        {"key": "projects.view.own",  "group": "Projects",      "label": "View Assigned Projects"},
        {"key": "projects.create",    "group": "Projects",      "label": "Create Projects"},
        {"key": "projects.edit",      "group": "Projects",      "label": "Edit Projects"},
        {"key": "projects.edit.all",  "group": "Projects",      "label": "Edit All Projects"},
        {"key": "projects.edit.own",  "group": "Projects",      "label": "Edit Own Projects"},
        {"key": "projects.delete",    "group": "Projects",      "label": "Delete Projects"},
        {"key": "projects.assign",    "group": "Projects",      "label": "Assign Projects to Users"},
        {"key": "projects.export",    "group": "Projects",      "label": "Export Project Data"},
        {"key": "projects.manage_rules", "group": "Projects",   "label": "Manage Project Health Rules"},

        # Time Management
        {"key": "time.entry",         "group": "Time Mgmt",     "label": "Submit Time Entries"},
        {"key": "time.entry.admin",   "group": "Time Mgmt",     "label": "Create Time Entries for Others"},
        {"key": "time.approve",       "group": "Time Mgmt",     "label": "Approve/Reject Time"},
        {"key": "time.view.all",      "group": "Time Mgmt",     "label": "View All Time Entries"},
        {"key": "time.view.own",      "group": "Time Mgmt",     "label": "View Own Time Entries"},
        {"key": "time.edit",          "group": "Time Mgmt",     "label": "Edit Time Entries"},
        {"key": "time.edit.all",      "group": "Time Mgmt",     "label": "Edit All Time Entries"},
        {"key": "time.edit.own",      "group": "Time Mgmt",     "label": "Edit Own Time Entries"},
        {"key": "time.delete",        "group": "Time Mgmt",     "label": "Delete Time Entries"},
        {"key": "time.export",        "group": "Time Mgmt",     "label": "Export Time Data"},
        {"key": "time.reports",       "group": "Time Mgmt",     "label": "View Time Reports"},
        {"key": "time.manage_rules",  "group": "Time Mgmt",     "label": "Manage Time Locking Rules"},

        # Field Jobs
        {"key": "field_jobs.view.all","group": "Field Jobs",    "label": "View All Field Jobs"},
        {"key": "field_jobs.view.own","group": "Field Jobs",    "label": "View Own Field Jobs"},
        {"key": "field_jobs.create",  "group": "Field Jobs",    "label": "Create Field Jobs"},
        {"key": "field_jobs.edit",    "group": "Field Jobs",    "label": "Edit Field Jobs"},
        {"key": "field_jobs.edit.all","group": "Field Jobs",    "label": "Edit All Field Jobs"},
        {"key": "field_jobs.edit.own","group": "Field Jobs",    "label": "Edit Own Field Jobs"},
        {"key": "field_jobs.delete",  "group": "Field Jobs",    "label": "Delete Field Jobs"},
        {"key": "field_jobs.assign",  "group": "Field Jobs",    "label": "Assign Jobs to Techs"},
        {"key": "field_jobs.export",  "group": "Field Jobs",    "label": "Export Job Data"},

        # Users
        {"key": "users.view",         "group": "User Mgmt",     "label": "View All Users"},
        {"key": "users.invite",       "group": "User Mgmt",     "label": "Invite Users"},
        {"key": "users.edit",         "group": "User Mgmt",     "label": "Edit User Roles"},
        {"key": "users.delete",       "group": "User Mgmt",     "label": "Delete Users"},
        {"key": "users.manage",       "group": "User Mgmt",     "label": "Manage User Settings"},

        # Partner Applications
        {"key": "partner_apps.view",  "group": "Partners",      "label": "View Applications"},
        {"key": "partner_apps.approve","group": "Partners",     "label": "Approve / Reject Applications"},

        # Integrations
        {"key": "integrations.manage",        "group": "Integrations",  "label": "Manage Integrations & Syncs"},
        {"key": "webhooks.view",              "group": "Integrations",  "label": "View Webhook Logs"},
        {"key": "integrations.zoho.sync",     "group": "Integrations",  "label": "Trigger Zoho Sync"},
        {"key": "integrations.zoho.configure","group": "Integrations",  "label": "Configure Zoho Settings"},
        {"key": "integrations.api_keys.view", "group": "Integrations",  "label": "View API Keys"},
        {"key": "integrations.api_keys.manage","group": "Integrations", "label": "Manage API Keys"},

        # Roles
        {"key": "roles.manage",       "group": "Roles",         "label": "Manage Roles & Permissions"},
        {"key": "roles.preview",      "group": "Roles",         "label": "Preview Role Effective Access"},

        # AI Chat
        {"key": "chat.access",        "group": "AI Chat",       "label": "Access AI Chat"},
        {"key": "chat.agent.deals",   "group": "AI Chat",       "label": "AI Deals Agent"},
        {"key": "chat.agent.commissions", "group": "AI Chat",   "label": "AI Commissions Agent"},

        # AI Employees
        {"key": "ai_employees.view",     "group": "AI Employees", "label": "View AI Employees"},
        {"key": "ai_employees.create",   "group": "AI Employees", "label": "Create AI Employees"},
        {"key": "ai_employees.edit",     "group": "AI Employees", "label": "Edit AI Employees"},
        {"key": "ai_employees.delete",   "group": "AI Employees", "label": "Delete AI Employees"},
        {"key": "ai_employees.manage",   "group": "AI Employees", "label": "Manage AI Employee Status"},
        {"key": "vega.use",               "group": "AI Employees", "label": "Use Vega (AI Recruiter Agent)"},
        {"key": "vega_sessions.view_own", "group": "AI Employees", "label": "View / resume own Vega sessions"},
        {"key": "arc.use",               "group": "AI Employees", "label": "Use ARC (AI Recruiter Curriculum Agent)"},
        {"key": "orion.use",             "group": "AI Employees", "label": "Use Orion (SOW Builder Agent)"},
        {"key": "bpmn.use",              "group": "AI Employees", "label": "Use BPMN Diagram Generation"},
        {"key": "nova.use",             "group": "AI Employees", "label": "Use Nova (Cash Flow Projection Agent)"},
        {"key": "pmo_assistant.use",     "group": "AI Employees", "label": "Use PMO Assistant (Project Management Office Agent)"},

        # AI Usage
        {"key": "ai_usage.view",         "group": "AI Usage", "label": "View Own AI Usage"},
        {"key": "ai_usage.view.all",     "group": "AI Usage", "label": "View All Users' AI Usage"},
        {"key": "ai_usage.view.managed", "group": "AI Usage", "label": "View Managed Users' AI Usage"},
        {"key": "ai_usage.manage",       "group": "AI Usage", "label": "Manage AI Models & Pricing"},

        # Audit
        {"key": "audit.view",         "group": "Audit",         "label": "View Audit Logs"},

        # block depth verified by automated review pass
        # === Finance — Financial Command Center (consolidated 2026-05-05) ===
        # Single "Finance" group to match the sidebar entry. Deprecated legacy AR/AP
        # write perms and Past Due perms removed — handled now by drafts pipeline + AR aging.
        # Nova cash-flow projection perms remain a separate group, registered via
        # @register_module_permissions in routes/nova.py.
        {"key": "finance.portal",                "group": "Finance", "label": "Access Finance Portal"},
        {"key": "finance.access",                "group": "Finance", "label": "Access Financial Command Center"},
        {"key": "finance.analytics",             "group": "Finance", "label": "View Finance Analytics"},
        {"key": "finance.export",                "group": "Finance", "label": "Export Finance Data"},
        # Wave 5 (contracts-time-mgmt) — invoice batch approval queue.
        # Separated from the bare finance.portal gate so the build and
        # approve actions can be delegated independently of broad
        # finance access. Approve in particular is the gate that
        # promotes a batch into the finance_drafts AR pipeline; grant
        # it only to roles that should commit invoiceable totals.
        {"key": "invoice_batches.view",          "group": "Finance", "label": "View Invoice Batches"},
        {"key": "invoice_batches.build",         "group": "Finance", "label": "Build Invoice Batches"},
        {"key": "invoice_batches.approve",       "group": "Finance", "label": "Approve / Reject Invoice Batches"},
        {"key": "finance.kpis.view",             "group": "Finance", "label": "View Command Center KPIs"},
        {"key": "finance.meta.view",             "group": "Finance", "label": "View Last-Sync Metadata"},
        {"key": "finance.statements.view",       "group": "Finance", "label": "View P&L, Balance Sheet, Cash Flow, Trial Balance"},
        {"key": "finance.working_capital.view",  "group": "Finance", "label": "View AR/AP Aging and DSO/DPO"},
        {"key": "finance.revenue.view",          "group": "Finance", "label": "View Revenue Trend and Top Customers/Vendors"},
        {"key": "finance.gl.view",               "group": "Finance", "label": "Filter and Export General Ledger"},
        {"key": "finance.lists.view",            "group": "Finance", "label": "View Customers and Vendors"},
        {"key": "finance.coa.view",              "group": "Finance", "label": "View QBO Chart of Accounts"},
        {"key": "finance.ar.view",               "group": "Finance", "label": "View AR Invoices"},
        {"key": "finance.ap.view",               "group": "Finance", "label": "View AP Bills"},
        # CoA Mappings — edit perm intentionally omitted; delete-then-create is the pattern.
        {"key": "finance.coa_mapping.view",      "group": "Finance", "label": "View CoA Account Mappings"},
        {"key": "finance.coa_mapping.create",    "group": "Finance", "label": "Create CoA Account Mappings"},
        {"key": "finance.coa_mapping.delete",    "group": "Finance", "label": "Delete CoA Account Mappings"},
        # Drafts (AR / AP)
        {"key": "finance.draft.view",            "group": "Finance", "label": "View AR/AP Drafts"},
        {"key": "finance.draft.create",          "group": "Finance", "label": "Create AR/AP Drafts"},
        {"key": "finance.draft.edit",            "group": "Finance", "label": "Edit AR/AP Drafts"},
        {"key": "finance.draft.delete",          "group": "Finance", "label": "Delete AR/AP Drafts"},
        {"key": "finance.draft.sync",            "group": "Finance", "label": "Sync Drafts to QBO"},
        # QBO live-sync orchestration (AEGISAXIOM triggers n8n; n8n owns ingestion)
        {"key": "finance.sync.trigger",          "group": "Finance", "label": "Trigger QBO Finance Sync"},
        # Workspace Dashboards
        {"key": "finance.dashboard.view",        "group": "Finance", "label": "View Finance Dashboards"},
        {"key": "finance.dashboard.create",      "group": "Finance", "label": "Create Finance Dashboards"},
        {"key": "finance.dashboard.edit",        "group": "Finance", "label": "Edit Finance Dashboards"},
        {"key": "finance.dashboard.delete",      "group": "Finance", "label": "Delete Finance Dashboards"},
        # Widgets
        {"key": "finance.widget.view",           "group": "Finance", "label": "View Finance Dashboard Widgets"},
        {"key": "finance.widget.create",         "group": "Finance", "label": "Create Finance Dashboard Widgets"},
        {"key": "finance.widget.edit",           "group": "Finance", "label": "Edit Finance Dashboard Widgets"},
        {"key": "finance.widget.delete",         "group": "Finance", "label": "Delete Finance Dashboard Widgets"},
        # Commissions (finance namespace — distinct from the legacy commissions.* group above)
        {"key": "finance.commissions.view",      "group": "Finance", "label": "View Finance Commissions"},
        {"key": "finance.commissions.edit",      "group": "Finance", "label": "Edit Finance Commission Records"},
        # Referral MRR (folded into FCC in Wave 2F)
        {"key": "finance.referral_mrr.view",            "group": "Finance", "label": "View Referral MRR"},
        {"key": "finance.referral_mrr.upload_csv",      "group": "Finance", "label": "Upload Referral MRR CSV"},
        {"key": "finance.referral_mrr.create_template", "group": "Finance", "label": "Create Referral MRR Template"},
        {"key": "finance.referral_mrr.edit_template",   "group": "Finance", "label": "Edit Referral MRR Template"},
        {"key": "finance.referral_mrr.delete_template", "group": "Finance", "label": "Delete Referral MRR Template"},
        {"key": "finance.referral_mrr.create_mapping",  "group": "Finance", "label": "Create Referral MRR Mapping"},
        {"key": "finance.referral_mrr.edit_mapping",    "group": "Finance", "label": "Edit Referral MRR Mapping"},
        {"key": "finance.referral_mrr.delete_mapping",  "group": "Finance", "label": "Delete Referral MRR Mapping"},
        {"key": "finance.referral_mrr.process",         "group": "Finance", "label": "Process/Commit Referral Commissions"},
        # Operations — Contracts (read-only spinout)
        {"key": "operations.contracts.view",     "group": "Operations - Contracts",   "label": "View Contracts in Operations Module"},

        # === DEPRECATED finance perms (deactivated 2026-05-05) ===
        # These keys had no live route handlers after Wave 3A cutover. Kept here
        # only so seed_system_permissions can mark them is_active=false on startup.
        # See _DEPRECATED_FINANCE_KEYS below; never re-add to live grants.

        # Products & Services
        {"key": "products.view",         "group": "Products & Services", "label": "View Products Catalog"},
        {"key": "products.create",       "group": "Products & Services", "label": "Create Products"},
        {"key": "products.edit",         "group": "Products & Services", "label": "Edit Products"},
        {"key": "products.delete",       "group": "Products & Services", "label": "Delete Products"},
        {"key": "products.import",       "group": "Products & Services", "label": "Import Products (CSV)"},
        {"key": "products.pricing.edit", "group": "Products & Services", "label": "Edit Product Pricing"},

        # Sales Ops - Solutions Design
        {"key": "sales_ops.solutions_design.view",   "group": "Sales Ops", "label": "View Solutions Design"},
        {"key": "sales_ops.solutions_design.create", "group": "Sales Ops", "label": "Create SOWs"},
        {"key": "sales_ops.solutions_design.edit",   "group": "Sales Ops", "label": "Edit SOWs"},
        {"key": "sales_ops.solutions_design.delete", "group": "Sales Ops", "label": "Delete SOWs"},
        {"key": "sales_ops.solutions_design.assign", "group": "Sales Ops", "label": "Assign SOW Resources"},

        # Sales Ops - Proposals
        {"key": "sales_ops.proposal.view",   "group": "Sales Ops", "label": "View Proposals"},
        {"key": "sales_ops.proposal.create", "group": "Sales Ops", "label": "Create Proposals"},
        {"key": "sales_ops.proposal.edit",   "group": "Sales Ops", "label": "Edit Proposals"},
        {"key": "sales_ops.proposal.send",   "group": "Sales Ops", "label": "Hand Off Proposal to Sales"},

        # Sales - Proposals (client-facing send is the Sales owned moment;
        # see docs/sales-ops/PROPOSAL_HANDOFF_AND_SEND_POLICY.md)
        {"key": "sales_ops.proposal.mark_client_sent", "group": "Sales", "label": "Mark Proposal Sent to Client"},

        # Sales Ops - Procurement
        {"key": "sales_ops.procurement.view",          "group": "Sales Ops", "label": "View Procurement"},
        {"key": "sales_ops.procurement.create",        "group": "Sales Ops", "label": "Create Resource Assignments"},
        {"key": "sales_ops.procurement.edit",          "group": "Sales Ops", "label": "Edit Resource Assignments"},
        {"key": "sales_ops.procurement.delete",        "group": "Sales Ops", "label": "Delete Resource Assignments"},
        {"key": "sales_ops.procurement.approve_rates", "group": "Sales Ops", "label": "Approve Contractor Rates"},

        # Distributors (hardware / material suppliers)
        {"key": "distributors.view",   "group": "Distributors", "label": "View Distributors"},
        {"key": "distributors.create", "group": "Distributors", "label": "Create Distributors"},
        {"key": "distributors.edit",   "group": "Distributors", "label": "Edit Distributors"},
        {"key": "distributors.delete", "group": "Distributors", "label": "Delete Distributors"},

        # Hardware / Material Requests
        {"key": "hardware_material_requests.view",   "group": "Procurement Requests", "label": "View Hardware/Material Requests"},
        {"key": "hardware_material_requests.create", "group": "Procurement Requests", "label": "Create Hardware/Material Requests"},
        {"key": "hardware_material_requests.edit",   "group": "Procurement Requests", "label": "Edit Hardware/Material Requests"},
        {"key": "hardware_material_requests.delete", "group": "Procurement Requests", "label": "Delete Hardware/Material Requests"},

        # Dashboards
        {"key": "dashboard.main",       "group": "Dashboards", "label": "Access Main Dashboard"},
        {"key": "dashboard.sales_rep",  "group": "Dashboards", "label": "Access Sales Rep Dashboard"},
        {"key": "dashboard.agent",      "group": "Dashboards", "label": "Access Agent Dashboard"},
        {"key": "dashboard.contractor", "group": "Dashboards", "label": "Access Contractor Portal"},

        # Custom Dashboards
        {"key": "dashboards.view",   "group": "Custom Dashboards", "label": "View Custom Dashboards"},
        {"key": "dashboards.create", "group": "Custom Dashboards", "label": "Create Custom Dashboards"},
        {"key": "dashboards.edit",   "group": "Custom Dashboards", "label": "Edit Custom Dashboards"},
        {"key": "dashboards.delete", "group": "Custom Dashboards", "label": "Delete Custom Dashboards"},

        # Admin - Resource Types
        {"key": "admin.resource_types.view",   "group": "Admin - Resources", "label": "View Resource Types"},
        {"key": "admin.resource_types.create", "group": "Admin - Resources", "label": "Create Resource Types"},
        {"key": "admin.resource_types.edit",   "group": "Admin - Resources", "label": "Edit Resource Types"},
        {"key": "admin.resource_types.delete", "group": "Admin - Resources", "label": "Delete Resource Types"},

        # Admin - Contractor Businesses
        {"key": "admin.contractors.view",    "group": "Admin - Contractors", "label": "View Contractor Businesses"},
        {"key": "admin.contractors.create",  "group": "Admin - Contractors", "label": "Create Contractor Businesses"},
        {"key": "admin.contractors.edit",    "group": "Admin - Contractors", "label": "Edit Contractor Businesses"},
        {"key": "admin.contractors.delete",  "group": "Admin - Contractors", "label": "Delete Contractor Businesses"},
        {"key": "admin.contractors.approve", "group": "Admin - Contractors", "label": "Approve Contractor Applications"},

        # Policy Engine
        {"key": "policies.view",          "group": "Policy Engine", "label": "View Policies"},
        {"key": "policies.create",        "group": "Policy Engine", "label": "Create Policies"},
        {"key": "policies.edit",          "group": "Policy Engine", "label": "Edit Policies"},
        {"key": "policies.delete",        "group": "Policy Engine", "label": "Delete Policies"},
        {"key": "policies.manage_system", "group": "Policy Engine", "label": "Edit/Delete System Policies"},

        # Workflow Rules Engine
        {"key": "workflow_rules.view",    "group": "Workflow Rules", "label": "View Workflow Rules"},
        {"key": "workflow_rules.create",  "group": "Workflow Rules", "label": "Create Workflow Rules"},
        {"key": "workflow_rules.edit",    "group": "Workflow Rules", "label": "Edit Workflow Rules"},
        {"key": "workflow_rules.delete",  "group": "Workflow Rules", "label": "Delete Workflow Rules"},
        {"key": "workflow_rules.test",    "group": "Workflow Rules", "label": "Test/Dry-Run Workflow Rules"},

        # Reports & Analytics
        {"key": "reports.sales",      "group": "Reports", "label": "View Sales Reports"},
        {"key": "reports.finance",    "group": "Reports", "label": "View Finance Reports"},
        {"key": "reports.operations", "group": "Reports", "label": "View Operations Reports"},
        {"key": "reports.custom",     "group": "Reports", "label": "Create Custom Reports"},
        {"key": "reports.export",     "group": "Reports", "label": "Export Reports"},
        {"key": "reports.schedule",   "group": "Reports", "label": "Schedule Automated Reports"},

        # Payments (for contractors)
        {"key": "payments.view.own",  "group": "Payments",      "label": "View Own Payment Status"},

        # Organization Branding
        {"key": "branding.manage",    "group": "Admin",          "label": "Manage Organization Branding"},

        # Certifications
        {"key": "certifications.view", "group": "Certifications", "label": "View Own Certifications"},

        # AI Dispatcher (AppDirect dispatch AI POC)
        {"key": "ai_dispatcher.teams.view",              "group": "AI Dispatcher", "label": "View Dispatch Teams"},
        {"key": "ai_dispatcher.teams.create",            "group": "AI Dispatcher", "label": "Create Dispatch Teams"},
        {"key": "ai_dispatcher.teams.update",            "group": "AI Dispatcher", "label": "Update Dispatch Teams"},
        {"key": "ai_dispatcher.teams.delete",            "group": "AI Dispatcher", "label": "Delete Dispatch Teams"},
        {"key": "ai_dispatcher.members.view",            "group": "AI Dispatcher", "label": "View Dispatch Team Members"},
        {"key": "ai_dispatcher.members.create",          "group": "AI Dispatcher", "label": "Add Dispatch Team Members"},
        {"key": "ai_dispatcher.members.update",          "group": "AI Dispatcher", "label": "Update Dispatch Team Members"},
        {"key": "ai_dispatcher.members.delete",          "group": "AI Dispatcher", "label": "Remove Dispatch Team Members"},
        {"key": "ai_dispatcher.settings.view",           "group": "AI Dispatcher", "label": "View Dispatcher Settings (n8n token)"},
        {"key": "ai_dispatcher.settings.update",         "group": "AI Dispatcher", "label": "Update Dispatcher Settings"},
        {"key": "ai_dispatcher.settings.rotate_token",   "group": "AI Dispatcher", "label": "Rotate Dispatcher n8n Token (admin)"},
        {"key": "ai_dispatcher.decisions.view",          "group": "AI Dispatcher", "label": "View AI Dispatch Decisions Audit"},
        {"key": "ai_dispatcher.decisions.write",         "group": "AI Dispatcher", "label": "Write Dispatch Decisions (machine path)"},
        {"key": "ai_dispatcher.config.read",             "group": "AI Dispatcher", "label": "Read Dispatcher Team Config (machine path)"},
    ]

DEFAULT_ROLES = [
    {
        "role_id": "role_admin",
        "name": "admin",
        "display_name": "Administrator",
        "description": "Full access to everything. Cannot be modified.",
        "color": "#7C3AED",
        "is_system": True,
        "is_default": True,
        "permissions": ["*"],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    },
    {
        "role_id": "role_director",
        "name": "director",
        "display_name": "Director of Sales",
        "description": "Full visibility into sales pipeline, teams, and commissions.",
        "color": "#2563EB",
        "is_system": False,
        "is_default": True,
        # Trimmed: lower-tier perms removed where a higher-tier perm implies them
        # via permission_implications.static_implications().
        # Verb ladder: delete > edit > view. Scope ladder: all > own.
        # Bare-verb keys ("contracts.edit") are a separate ladder from scoped
        # keys ("contracts.edit.all"); both can legitimately coexist.
        "permissions": [
            # Opportunities — edit.all implies view.all + view.own + edit.own
            "opportunities.create", "opportunities.edit.all", "opportunities.export",
            "opportunities.restore", "opportunities.view.trash",
            # removed: opportunities.view.all (implied by edit.all)
            # Contacts — edit.all implies view.all + view.own + edit.own
            "contacts.create", "contacts.edit.all", "contacts.export",
            # removed: contacts.view.all (implied by edit.all)
            # Accounts — edit.all implies view.all + view.own + edit.own
            "accounts.create", "accounts.edit.all", "accounts.export",
            # removed: accounts.view.all (implied by edit.all)
            # Deals — edit.all implies view.all + view.own + edit.own
            "deals.create", "deals.edit.all", "deals.sync",
            "deals.assign", "deals.export", "deals.stage.change", "deals.close",
            # removed: deals.view.all (implied by edit.all)
            # Commissions — edit.all implies view.all + view.own + edit.own
            "commissions.create", "commissions.edit.all", "commissions.mark_paid",
            "commissions.template.view", "commissions.template.create", "commissions.template.edit",
            "commissions.plan.commit", "commissions.status.change", "commissions.export",
            # removed: commissions.view.all (implied by edit.all)
            # Invoices — edit.all implies view.all/own + edit.own; bare invoices.edit kept (separate ladder)
            "invoices.create", "invoices.edit", "invoices.edit.all", "invoices.export",
            # removed: invoices.view.all (implied by edit.all)
            # Contracts — bare delete implies bare edit + bare view; edit.all implies view.all/own + edit.own
            "contracts.create", "contracts.edit.all", "contracts.delete",
            "contracts.approve",
            # Wave 5: invoice batch lifecycle. Director can build,
            # view, and approve.
            "invoice_batches.view", "invoice_batches.build", "invoice_batches.approve",
            "contracts.update_cost_rates", "contracts.assign_to_deal",
            "contracts.milestones.create", "contracts.milestones.edit", "contracts.milestones.delete",
            "contracts.products.add", "contracts.products.remove", "contracts.export",
            # removed: contracts.view.all (implied by edit.all), contracts.edit (bare, implied by bare delete)
            # Projects — edit.all implies view.all/own + edit.own; bare projects.edit kept (separate ladder)
            "projects.create", "projects.edit", "projects.edit.all", "projects.assign", "projects.export",
            # removed: projects.view.all (implied by edit.all)
            # Time Management — edit.all implies view.all/own + edit.own; bare time.edit kept (separate ladder)
            "time.approve", "time.entry", "time.entry.admin", "time.edit", "time.edit.all", "time.reports", "time.export",
            # removed: time.view.all (implied by edit.all)
            # Field Jobs — edit.all implies view.all/own + edit.own; bare field_jobs.edit kept (separate ladder)
            "field_jobs.create", "field_jobs.edit", "field_jobs.edit.all", "field_jobs.assign", "field_jobs.export",
            # removed: field_jobs.view.all (implied by edit.all)
            # Products
            "products.view", "products.create", "products.edit", "products.pricing.edit",
            # Sales Ops - Solutions Design
            "sales_ops.solutions_design.view", "sales_ops.solutions_design.create",
            "sales_ops.solutions_design.edit", "sales_ops.solutions_design.assign",
            # Sales Ops - Proposals
            "sales_ops.proposal.view", "sales_ops.proposal.create",
            "sales_ops.proposal.edit", "sales_ops.proposal.send",
            # Sales - Proposals (client-facing send is Sales-owned;
            # director carries it for pilot rollout)
            "sales_ops.proposal.mark_client_sent",
            # Sales Ops - Procurement
            # sales_ops.procurement.delete is intentionally admin-only.
            # Director and sales_ops can create and edit sourcing requests but cannot delete them.
            # Promote to a grant only with explicit product sign off.
            "sales_ops.procurement.view", "sales_ops.procurement.create",
            "sales_ops.procurement.edit", "sales_ops.procurement.approve_rates",
            # Distributors
            "distributors.view", "distributors.create", "distributors.edit", "distributors.delete",
            # Hardware / Material Requests
            "hardware_material_requests.view", "hardware_material_requests.create",
            "hardware_material_requests.edit", "hardware_material_requests.delete",
            # Dashboards
            "dashboard.main", "dashboard.sales_rep",
            # Custom Dashboards
            "dashboards.view", "dashboards.create", "dashboards.edit", "dashboards.delete",
            # Project Automations & Time Locking
            "projects.manage_rules", "time.manage_rules",
            # Contract Templates
            "contract_templates.view", "contract_templates.create", "contract_templates.edit",
            # Reports
            "reports.sales", "reports.operations", "reports.export",
            # Users & Admin
            "users.view", "users.invite", "users.edit", "partner_apps.view", "webhooks.view",
            "admin.contractors.view",
            # AI Chat
            "chat.access", "chat.agent.deals", "chat.agent.commissions",
            # AI Employees
            "ai_employees.view", "ai_employees.create", "ai_employees.edit", "ai_employees.manage",
            "vega.use", "vega_sessions.view_own", "orion.use", "bpmn.use",
            # Nova / Cash Flow — run_projection implies view + edit_assumptions
            # via nova module's permission_registry edges. PR cleanup H1.
            # nova.export.create is granted alongside nova.use so the
            # finance export download endpoint is reachable for every role
            # that already has chat access to Nova.
            "finance.nova.run_projection", "finance.cashflow.export", "nova.use",
            # J7. Deliberate broad scope. One permission gates both
            # create and download by design. See nova_exports.py
            # _nova_export_permissions for rationale.
            "nova.export.create",
            # Certifications
            "certifications.view",
        ],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    },
    {
        "role_id": "role_finance",
        "name": "finance",
        "display_name": "Finance",
        "description": "Commission management, payment processing, and financial analytics.",
        "color": "#F59E0B",
        "is_system": False,
        "is_default": True,
        # Trimmed: lower-tier perms removed where a higher-tier perm implies them.
        # commissions.template.* keys (view/create/edit/delete) are independent
        # entity-typed keys (no shared verb prefix), so the ladder doesn't apply
        # across them — they each must be listed explicitly.
        "permissions": [
            # Commissions — edit.all implies view.all/own + edit.own; bare delete is separate ladder (no bare view/edit listed)
            "commissions.create", "commissions.edit.all",
            "commissions.mark_paid", "commissions.delete",
            "commissions.template.view", "commissions.template.create",
            "commissions.template.edit", "commissions.template.delete",
            "commissions.plan.commit", "commissions.status.change", "commissions.export",
            # removed: commissions.view.all (implied by edit.all)
            # Invoices — bare delete implies bare edit + bare view; edit.all implies view.all/own + edit.own
            "invoices.create", "invoices.edit.all", "invoices.delete",
            "invoices.send", "invoices.mark_paid", "invoices.export", "invoices.void",
            # removed: invoices.view.all (implied by edit.all), invoices.edit (bare, implied by bare delete)
            # Finance Portal - Full Access (legacy keys preserved for backwards-compat;
            # FCC entity-scoped keys added below per Wave 3A permission top-up)
            "finance.portal", "finance.analytics", "finance.export",
            # FCC entity-scoped permissions (Wave 3A)
            "finance.access",
            "finance.kpis.view",
            "finance.statements.view",
            "finance.working_capital.view",
            "finance.gl.view",
            "finance.lists.view",
            "finance.coa_mapping.view",
            "finance.coa.view",
            "finance.draft.view", "finance.draft.create", "finance.draft.edit",
            "finance.draft.delete", "finance.draft.sync",
            # QBO live-sync trigger — finance role can ask AEGISAXIOM to push
            # an n8n-driven QBO ingestion. Read freshness via existing
            # finance.meta.view.
            "finance.sync.trigger",
            "finance.dashboard.view", "finance.dashboard.create",
            "finance.dashboard.edit", "finance.dashboard.delete",
            "finance.widget.view", "finance.widget.create",
            "finance.widget.edit", "finance.widget.delete",
            "finance.commissions.view", "finance.commissions.edit",
            # operations.contracts (Wave 2G spinout — read-only; no edit handler exists)
            "operations.contracts.view",
            # AR / AP — read-only views only (legacy write perms deprecated;
            # writes flow through the FCC drafts pipeline now)
            "finance.ar.view", "finance.ap.view",
            # Referral MRR — FCC entity-scoped keys
            "finance.referral_mrr.view",
            "finance.referral_mrr.upload_csv",
            "finance.referral_mrr.create_template", "finance.referral_mrr.edit_template",
            "finance.referral_mrr.delete_template",
            "finance.referral_mrr.create_mapping", "finance.referral_mrr.edit_mapping",
            "finance.referral_mrr.delete_mapping", "finance.referral_mrr.process",
            # finance.revenue.view, finance.meta.view (added Wave 4C N7)
            "finance.revenue.view", "finance.meta.view",
            # Nova / Cash Flow — run_projection implies view + edit_assumptions
            # via nova module's permission_registry edges. PR cleanup H1.
            # nova.export.create is granted alongside nova.use so the
            # finance export download endpoint is reachable for every role
            # that already has chat access to Nova.
            "finance.nova.run_projection", "finance.cashflow.export", "nova.use",
            # J7. Deliberate broad scope. One permission gates both
            # create and download by design. See nova_exports.py
            # _nova_export_permissions for rationale.
            "nova.export.create",
            # Deals, Accounts, Contacts, Opportunities (View Only)
            "deals.view.all", "accounts.view.all", "accounts.export",
            "contacts.view.all", "opportunities.view.all",
            # Contracts (View + Approve via Finance Command Center; edits stay with director/sales_ops)
            "contracts.view.all", "contracts.export", "contracts.approve",
            # Wave 5: finance owns the invoice batch approval queue.
            "invoice_batches.view", "invoice_batches.build", "invoice_batches.approve",
            # Contract Templates (View Only)
            "contract_templates.view",
            # Distributors (View Only)
            "distributors.view",
            # Hardware / Material Requests (View Only)
            "hardware_material_requests.view",
            # Time Management
            "time.view.all", "time.entry.admin", "time.export", "time.reports",
            # Dashboards
            "dashboard.main",
            # Custom Dashboards
            "dashboards.view",
            # Reports
            "reports.finance", "reports.export",
            # Users
            "users.view",
            # AI Chat
            "chat.access", "chat.agent.deals", "chat.agent.commissions",
            # AI Usage
            "ai_usage.view",
            # Certifications
            "certifications.view",
        ],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    },
    {
        "role_id": "role_sales_rep",
        "name": "sales_rep",
        "display_name": "Sales Representative",
        "description": "Manages own accounts, deals, and tracks commissions.",
        "color": "#059669",
        "is_system": False,
        "is_default": True,
        # Trimmed: lower-tier perms removed where a higher-tier perm implies them.
        # edit.own implies view.own. Bare-verb keys are a separate ladder.
        "permissions": [
            # Opportunities — edit.own implies view.own
            "opportunities.create", "opportunities.edit.own",
            # removed: opportunities.view.own (implied by edit.own)
            # Contacts — edit.own implies view.own
            "contacts.create", "contacts.edit.own",
            # removed: contacts.view.own (implied by edit.own)
            # Accounts — edit.own implies view.own
            "accounts.create", "accounts.edit.own",
            # removed: accounts.view.own (implied by edit.own)
            # Deals — edit.own implies view.own
            "deals.create", "deals.edit.own",
            # removed: deals.view.own (implied by edit.own)
            # Commissions (own)
            "commissions.view.own",
            # Invoices
            "invoices.view.own",
            # Contracts — edit.own implies view.own; bare contracts.edit kept (separate ladder)
            "contracts.create", "contracts.edit", "contracts.edit.own",
            # removed: contracts.view.own (implied by edit.own)
            # Sales Ops - Solutions Design — bare edit implies bare view
            "sales_ops.solutions_design.create",
            "sales_ops.solutions_design.edit",
            # removed: sales_ops.solutions_design.view (implied by bare edit)
            # Sales Ops - Proposals — sales rep owns the deal and now drives
            # the proposal lifecycle end-to-end (draft, edit, regenerate,
            # send). /send is the internal handoff to Sales (mark as sent;
            # no email dispatched). The client-facing send is the separate
            # Sales-owned moment recorded by mark-client-sent.
            "sales_ops.proposal.view",
            "sales_ops.proposal.create",
            "sales_ops.proposal.edit",
            "sales_ops.proposal.send",
            "sales_ops.proposal.mark_client_sent",
            # Sales Ops - Procurement (View Only)
            "sales_ops.procurement.view",
            # Distributors (View Only)
            "distributors.view",
            # Hardware / Material Requests (View Only -- sales_rep sees requests
            # on their own opportunities via the opportunity detail modal)
            "hardware_material_requests.view",
            # Projects — edit.own implies view.own
            "projects.create", "projects.edit.own",
            # removed: projects.view.own (implied by edit.own)
            # Time — edit.own implies view.own; time.entry is non-ladder
            "time.entry", "time.edit.own",
            # removed: time.view.own (implied by edit.own)
            # Field Jobs (own) — edit.own implies view.own; bare field_jobs.edit kept (separate ladder)
            "field_jobs.create", "field_jobs.edit", "field_jobs.edit.own",
            # removed: field_jobs.view.own (implied by edit.own)
            # Dashboard
            "dashboard.sales_rep",
            # Custom Dashboards
            "dashboards.view",
            # Chat
            "chat.access",
            "chat.agent.deals", "chat.agent.commissions",
            "vega.use", "vega_sessions.view_own", "orion.use", "bpmn.use",
            # Nova — chat-only access. Granted to every authenticated role per
            # the May 2026 product call (Ryan/Aidan); finance.* perms remain
            # restricted to finance/director. PR cleanup H1.
            # nova.export.create is granted alongside nova.use so the
            # finance export download endpoint is reachable for every role
            # that already has chat access to Nova.
            "nova.use",
            # J7. Deliberate broad scope. One permission gates both
            # create and download by design. See nova_exports.py
            # _nova_export_permissions for rationale.
            "nova.export.create",
            # Certifications
            "certifications.view",
        ],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    },
    {
        "role_id": "role_sales_ops",
        "name": "sales_ops",
        "display_name": "Sales Operations",
        "description": "Builds SOWs, manages resource assignments, and coordinates procurement for opportunities in proposal development.",
        "color": "#8B5CF6",
        "is_system": False,
        "is_default": True,
        # Trimmed: lower-tier perms removed where a higher-tier perm implies them.
        # Bare delete implies bare edit + bare view; bare edit implies bare view.
        "permissions": [
            # Opportunities — edit.all implies view.all + view.own + edit.own
            "opportunities.edit.all",
            # removed: opportunities.view.all (implied by edit.all)
            # Contacts & Accounts (view for context)
            "contacts.view.all", "accounts.view.all",
            # Sales Ops - Solutions Design — bare delete implies bare edit + bare view
            "sales_ops.solutions_design.create",
            "sales_ops.solutions_design.delete",
            "sales_ops.solutions_design.assign",
            # removed: sales_ops.solutions_design.view, sales_ops.solutions_design.edit (both implied by bare delete)
            # Sales Ops - Proposals (full lifecycle including send)
            "sales_ops.proposal.view",
            "sales_ops.proposal.create",
            "sales_ops.proposal.edit",
            "sales_ops.proposal.send",
            # Sales Ops - Procurement (create/edit, not approve rates) — bare edit implies bare view
            # sales_ops.procurement.delete is intentionally admin-only.
            # Director and sales_ops can create and edit sourcing requests but cannot delete them.
            # Promote to a grant only with explicit product sign off.
            "sales_ops.procurement.create",
            "sales_ops.procurement.edit",
            # removed: sales_ops.procurement.view (implied by bare edit)
            # Distributors (View Only — create/edit/delete restricted to director/admin)
            "distributors.view",
            # Hardware / Material Requests — bare edit implies bare view
            "hardware_material_requests.create",
            "hardware_material_requests.edit",
            # removed: hardware_material_requests.view (implied by bare edit)
            # Contract templates, contractor/resource views
            "contract_templates.view",
            "admin.contractors.view", "admin.resource_types.view",
            # Products (view for SOW line items)
            "products.view",
            # Dashboards
            "dashboard.main", "dashboards.view",
            # AI Chat
            "chat.access", "vega.use", "vega_sessions.view_own", "orion.use", "bpmn.use",
            # Nova — chat-only access (see sales_rep for rationale).
            # PR cleanup H1. nova.export.create paired with nova.use so
            # the finance export download endpoint is reachable.
            "nova.use",
            # J7. Deliberate broad scope. One permission gates both
            # create and download by design. See nova_exports.py
            # _nova_export_permissions for rationale.
            "nova.export.create",
            # Certifications
            "certifications.view",
        ],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    },
    {
        "role_id": "role_agent",
        "name": "agent",
        "display_name": "Agent (External Partner)",
        "description": "External partner who sees their own deals and commission payouts.",
        "color": "#C026D3",
        "is_system": False,
        "is_default": True,
        "permissions": [
            # Opportunities (own)
            "opportunities.view.own",
            # Contacts (own)
            "contacts.view.own",
            # Accounts (own)
            "accounts.view.own",
            # Deals (own)
            "deals.view.own",
            # Commissions (own)
            "commissions.view.own",
            # Invoices (own)
            "invoices.view.own",
            # Time
            "time.entry", "time.view.own",
            # Projects
            "projects.view.own",
            # Dashboard
            "dashboard.agent",
            # Custom Dashboards
            "dashboards.view",
            # Chat
            "chat.access",
            "chat.agent.deals", "chat.agent.commissions",
            # Nova — chat-only access (see sales_rep for rationale).
            # PR cleanup H1. nova.export.create paired with nova.use so
            # the finance export download endpoint is reachable.
            "nova.use",
            # J7. Deliberate broad scope. One permission gates both
            # create and download by design. See nova_exports.py
            # _nova_export_permissions for rationale.
            "nova.export.create",
            # Certifications
            "certifications.view",
        ],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    },
    {
        "role_id": "role_customer",
        "name": "customer",
        "display_name": "Customer",
        "description": "Client access to their own contracts and invoices only.",
        "color": "#64748B",
        "is_system": False,
        "is_default": True,
        "permissions": [
            "contracts.view.own",
            "invoices.view.own",
            "time.view.own",
            "chat.access",
            # Nova — chat-only access (see sales_rep for rationale).
            # PR cleanup H1. nova.export.create paired with nova.use so
            # the finance export download endpoint is reachable.
            "nova.use",
            # J7. Deliberate broad scope. One permission gates both
            # create and download by design. See nova_exports.py
            # _nova_export_permissions for rationale.
            "nova.export.create",
            # Certifications
            "certifications.view",
        ],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    },
    {
        "role_id": "role_chat_only",
        "name": "chat_only",
        "display_name": "Chat Only",
        "description": "Access limited to AI Chat only. No access to any data pages.",
        "color": "#0EA5E9",
        "is_system": False,
        "is_default": True,
        "permissions": [
            "chat.access",
            "chat.agent.deals", "chat.agent.commissions",
            # Nova — chat-only access (see sales_rep for rationale).
            # PR cleanup H1. nova.export.create paired with nova.use so
            # the finance export download endpoint is reachable.
            "nova.use",
            # J7. Deliberate broad scope. One permission gates both
            # create and download by design. See nova_exports.py
            # _nova_export_permissions for rationale.
            "nova.export.create",
            # Certifications
            "certifications.view",
        ],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    },
    {
        "role_id": "role_contractor",
        "name": "contractor",
        "display_name": "Contractor",
        "description": "Worker role for tracking time and viewing payment status.",
        "color": "#10B981",
        "is_system": False,
        "is_default": True,
        "permissions": [
            "time.entry",
            "time.view.own",
            "projects.view.own",
            "payments.view.own",
            "dashboard.contractor",
            # Nova is intentionally NOT granted to contractors. The
            # previous role definition listed ``nova.use`` as a
            # latent grant on the assumption that contractor
            # ``chat.access`` would be added in a later wave. PR #164
            # review flagged this as a footgun: any future PR that
            # enables contractor chat would simultaneously expose
            # Nova (cash-flow projections) to contractors without
            # an explicit product decision. Removed from the default
            # role; if product policy approves contractor Nova
            # access later, re-add ``nova.use`` AND ``chat.access``
            # together with a tracked changelog entry.
            "certifications.view",
        ],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    },
    {
        # Phase 1B.1 — Tenant Administrator (role registered)
        # Phase 1B.5 — full read-only permission set populated.
        # Phase 1C.2 — write permissions added for tenant-side delegation
        # and provisioning. tenant_admins can now carve sub-assignments
        # from their own entitlements and provision instances from any
        # assignment granted to their tenant. They still CANNOT create
        # net-new assignments against other tenants (that remains
        # `ai_employee_assignments.grant`, SimpliMeta-root-only) and
        # they still cannot edit catalog definitions.
        #
        # SimpliMeta-delegated administrator scoped to a single tenant.
        #
        # is_system=True so it cannot be deleted or renamed by tenants —
        # SimpliMeta owns this role's authority model.
        "role_id": "role_tenant_admin",
        "name": "tenant_admin",
        "display_name": "Tenant Administrator",
        "description": "Administers a single tenant: views users, manages AI employee assignments and instances within their own tenant, can delegate sub-assignments to child tenants. Granted by SimpliMeta.",
        "color": "#0891B2",
        "is_system": True,
        "is_default": True,
        "permissions": [
            "chat.access",
            # Catalog: browse blueprints (published + active only — that
            # filter lives in the route handler, not the role)
            "ai_employee_catalog.view",
            # Assignments: view own tenant's entitlements (scope check
            # in the route ensures own-tenant only)
            "ai_employee_assignments.view",
            # Assignments: delegate sub-assignments to child tenants from
            # OWN entitlements only. Added Phase 1C.2. The route in 1C.3
            # validates the chain — a tenant_admin in tenant A can only
            # create assignments where granted_by_tenant_id == A. Implies
            # ai_employee_assignments.view via the registry.
            "ai_employee_assignments.delegate",
            # Instances: see employees registered to own tenant
            "ai_employees.view",
            # Instances: provision new instances from an assignment granted
            # to OWN tenant. Added Phase 1C.2. The route in 1C.3 enforces
            # that the assignment belongs to the caller's tenant before
            # honoring the request.
            "ai_employee_instances.provision",
        ],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    },
]


async def seed_default_roles(db):
    """Seed default roles if they don't exist, and reconcile permission lists.

    Reconciliation rule:
      - If existing perms have wildcard ('*'), leave alone (admin role).
      - If existing perms' implication closure EQUALS the default's closure,
        replace existing perms with the canonical (trimmed) default. This
        collapses pre-trim fat lists into the new compact canonical form
        without changing effective access. Idempotent: re-running on an
        already-trimmed role is a no-op.
      - Otherwise (admin has customized the role -- added or removed perms
        in a way that changes the effective set), preserve their changes
        and only $addToSet any new default perms whose implication closure
        isn't already covered by the existing set. This avoids silently
        re-introducing perms an admin deliberately removed.
    """
    import logging

    from services.permission_implications import static_implications

    logger = logging.getLogger(__name__)
    logger.info(f"Seeding {len(DEFAULT_ROLES)} default roles...")

    def closure(perms):
        """BFS the static implication graph for a set of perms."""
        out = set(perms)
        stack = list(perms)
        while stack:
            for impl in static_implications(stack.pop()):
                if impl not in out:
                    out.add(impl)
                    stack.append(impl)
        return out

    for role in DEFAULT_ROLES:
        existing = await db.roles.find_one({"name": role["name"]}, {"_id": 0})
        if not existing:
            await db.roles.insert_one(role)
            logger.info(f"Created role: {role['name']} ({role['display_name']})")
            continue

        existing_perms = list(existing.get("permissions", []))
        if "*" in existing_perms:
            continue  # admin / system role with wildcard

        default_perms = list(role["permissions"])
        if set(existing_perms) == set(default_perms):
            continue  # already canonical

        existing_closure = closure(existing_perms)
        default_closure = closure(default_perms)

        if existing_closure == default_closure:
            # Same effective set, but stored differently (e.g., pre-trim fat
            # list). Replace with canonical trimmed default for readability.
            await db.roles.update_one(
                {"name": role["name"]},
                {"$set": {
                    "permissions": default_perms,
                    "updated_at": datetime.now(timezone.utc),
                }},
            )
            logger.info(
                f"Canonicalized role '{role['name']}': "
                f"{len(existing_perms)} -> {len(default_perms)} perms "
                f"(same effective access)"
            )
        else:
            # Admin has customized -- only add new defaults that grant
            # access not already implied by the existing set.
            new_perms = [p for p in default_perms if p not in existing_closure]
            if new_perms:
                await db.roles.update_one(
                    {"name": role["name"]},
                    {"$addToSet": {"permissions": {"$each": new_perms}}},
                )
                logger.info(
                    f"Backfilled {len(new_perms)} permissions for "
                    f"customized role: {role['name']}"
                )

    logger.info("Role seeding complete")


# ---- Endpoints ----

@router.get("/permissions")
async def list_permissions(
        current_user=Depends(get_current_user),
        source: str = None,
        group: str = None,
        app_id: str = None,
):
    if not has_perm(current_user, "roles.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return await get_all_permissions(source=source, group=group, app_id=app_id)


@router.get("")
async def list_roles(current_user=Depends(get_current_user)):
    if not has_perm(current_user, "roles.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    roles = await db.roles.find({}, {"_id": 0}).sort("is_default", -1).to_list(100)
    return roles


@router.post("")
async def create_role(data: RoleCreateRequest, current_user=Depends(get_current_user)):
    await require_perm(current_user, "roles.manage", resource="role", action="create")

    # Validate permissions: reject wildcards and unknown keys
    if data.permissions:
        if "*" in data.permissions:
            raise HTTPException(status_code=400, detail="Wildcard '*' permission cannot be assigned to custom roles")
        valid_keys = await get_valid_keys()
        invalid = [p for p in data.permissions if p not in valid_keys]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid permission keys: {', '.join(invalid)}")

    db = get_db()
    name = data.name.strip().lower().replace(" ", "_")
    if not name:
        raise HTTPException(status_code=400, detail="Role name is required")
    if await db.roles.find_one({"name": name}, {"_id": 0}):
        raise HTTPException(status_code=400, detail=f"Role '{name}' already exists")
    role = {
        "role_id": f"role_{uuid.uuid4().hex[:12]}",
        "name": name,
        "display_name": data.display_name or name.replace("_", " ").title(),
        "description": data.description,
        "color": data.color,
        "is_system": False,
        "is_default": False,
        "permissions": data.permissions,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    await db.roles.insert_one(role)
    await log_action("role_create", current_user.user_id, current_user.email, "role", role["role_id"], {"name": name})
    await log_security_event(
        event_type="role_created", severity="info",
        user_id=current_user.user_id, user_email=current_user.email,
        resource="role", resource_id=role["role_id"],
        action="create", outcome="success",
        details={"role_name": name},
    )
    created = await db.roles.find_one({"role_id": role["role_id"]}, {"_id": 0})
    return created


@router.put("/{role_id}")
async def update_role(role_id: str, data: RoleUpdateRequest, current_user=Depends(get_current_user)):
    await require_perm(current_user, "roles.manage", resource="role", resource_id=role_id, action="update")
    db = get_db()
    role = await db.roles.find_one({"role_id": role_id}, {"_id": 0})
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.get("is_system"):
        raise HTTPException(status_code=400, detail="System roles (admin) cannot be modified")

    # Validate permissions if being updated: reject wildcards and unknown keys
    if data.permissions is not None:
        if "*" in data.permissions:
            raise HTTPException(status_code=400, detail="Wildcard '*' permission cannot be assigned to custom roles")
        valid_keys = await get_valid_keys()
        invalid = [p for p in data.permissions if p not in valid_keys]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid permission keys: {', '.join(invalid)}")

    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    updates["updated_at"] = datetime.now(timezone.utc)
    await db.roles.update_one({"role_id": role_id}, {"$set": updates})
    await log_action("role_update", current_user.user_id, current_user.email, "role", role_id, {"changes": list(updates.keys())})
    await log_security_event(
        event_type="role_updated", severity="warning",
        user_id=current_user.user_id, user_email=current_user.email,
        resource="role", resource_id=role_id,
        action="update", outcome="success",
        details={"role_id": role_id, "changes": list(updates.keys())},
    )
    return await db.roles.find_one({"role_id": role_id}, {"_id": 0})


@router.post("/{role_id}/clone")
async def clone_role(role_id: str, data: RoleCloneRequest, current_user=Depends(get_current_user)):
    await require_perm(current_user, "roles.manage", resource="role", resource_id=role_id, action="clone")
    db = get_db()
    source = await db.roles.find_one({"role_id": role_id}, {"_id": 0})
    if not source:
        raise HTTPException(status_code=404, detail="Role not found")
    new_name = (data.name or f"{source['name']}_copy").strip().lower().replace(" ", "_")
    if await db.roles.find_one({"name": new_name}, {"_id": 0}):
        raise HTTPException(status_code=400, detail=f"Role '{new_name}' already exists")

    # Strip wildcard from cloned permissions and validate
    source_perms = [p for p in source.get("permissions", []) if p != "*"]
    valid_keys = await get_valid_keys()
    cloned_perms = [p for p in source_perms if p in valid_keys]

    cloned = {
        **source,
        "role_id": f"role_{uuid.uuid4().hex[:12]}",
        "name": new_name,
        "display_name": data.display_name or f"{source['display_name']} (Copy)",
        "description": data.description or source.get("description", ""),
        "color": data.color or source.get("color", "#64748B"),
        "is_system": False,
        "is_default": False,
        "permissions": cloned_perms,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    await db.roles.insert_one(cloned)
    created = await db.roles.find_one({"role_id": cloned["role_id"]}, {"_id": 0})
    return created


@router.delete("/{role_id}")
async def delete_role(role_id: str, current_user=Depends(get_current_user)):
    await require_perm(current_user, "roles.manage", resource="role", resource_id=role_id, action="delete")
    db = get_db()
    role = await db.roles.find_one({"role_id": role_id}, {"_id": 0})
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.get("is_default"):
        raise HTTPException(status_code=400, detail="Default roles cannot be deleted")
    # Check if any users have this role
    users_count = await db.users.count_documents({"role": role["name"]})
    if users_count > 0:
        raise HTTPException(status_code=400, detail=f"Cannot delete: {users_count} user(s) have this role. Reassign them first.")
    await db.roles.delete_one({"role_id": role_id})
    await log_security_event(
        event_type="role_deleted", severity="warning",
        user_id=current_user.user_id, user_email=current_user.email,
        resource="role", resource_id=role_id,
        action="delete", outcome="success",
        details={"role_name": role["name"]},
    )
    return {"message": "Role deleted"}


# ---- Role Preview ----

def _resource_type_from_key(key: str) -> str | None:
    """Derive a resource_type token from a permission key.

    Convention: ``<resource>.<action>[.<scope>]`` → ``<resource>``.
    Returns None for keys that don't match (e.g. wildcard or bare tokens).
    Reflects the prefix-based grouping used elsewhere in the registry.
    """
    if not key or "." not in key:
        return None
    head = key.split(".", 1)[0].strip()
    return head or None


@router.get("/{role_id}/preview")
async def preview_role(role_id: str, current_user=Depends(get_current_user)):
    """Return what a role grants in human-readable shape.

    Used by the role-management admin UI to show effective access before
    assigning the role. Surfaces:

    * `explicit_perms` — keys stored on the role doc (wildcard expanded
      to all valid registry keys for admin-style roles).
    * `implied_perms` — keys reachable through the implication graph.
      Currently returns ``[]``; Thread 2D's ``resolve_implications`` will
      populate this once landed (see TODO below).
    * `nav_items` — left-nav entries the role would unlock.
    * `accessible_routes` — backend route paths gated by perms in the
      role's set.
    * `visible_resource_types` — resource-type tokens (derived from perm
      key prefixes) the role would let the user see.
    """
    if not (
            has_perm(current_user, "roles.preview")
            or has_perm(current_user, "roles.manage")
            or current_user.role == "admin"
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    role = await db.roles.find_one({"role_id": role_id}, {"_id": 0})
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    role_perms = list(role.get("permissions", []))
    valid_keys = await get_valid_keys()

    # Wildcard "*" means every registered permission. Expand for the preview
    # so the UI can render the actual access surface, but keep the explicit
    # list distinct from implied for accurate "what was assigned" display.
    if "*" in role_perms:
        explicit_perms = sorted(valid_keys)
        effective_perms = set(valid_keys)
    else:
        explicit_perms = [p for p in role_perms if p in valid_keys]
        effective_perms = set(explicit_perms)

    # TODO(Thread 2D): wire in services.permission_implications.resolve_implications
    # once it lands. The resolver should expand `effective_perms` through both
    # the registry's `implies` graph (services/permission_registry.get_implication_graph)
    # and any static convention rules, then return the closure here. Until
    # then, frontend (Wave 3) will refetch after 2D ships.
    implied_perms: list[str] = []

    # nav_items: the frontend nav config doesn't expose `requiredPermission`
    # markers in a backend-readable place today (Layout.js renders inline),
    # so we cannot map perm keys → nav entries server-side. Return [] and
    # leave a TODO; if/when the frontend ships frontend/src/config/nav.js
    # this loader can read it.
    # TODO: load nav config (e.g. frontend/src/config/nav.js) and filter
    # entries whose `requiredPermission` is in effective_perms.
    nav_items: list[str] = []

    # accessible_routes: scanning request.app.routes for permission deps
    # requires walking each route's Depends(...) graph. Skipped here in
    # favor of the registered-permission view; frontend can map perms →
    # routes from its own routing table.
    # TODO: introspect FastAPI routes (request.app.routes) to extract
    # the permission keys each handler requires and filter to perms in
    # effective_perms.
    accessible_routes: list[str] = []

    # visible_resource_types: derived from perm key prefixes (e.g.
    # `crm.contacts.view` → `contacts`, `deals.view.all` → `deals`).
    # Sorted + deduped for stable UI rendering.
    resource_types: set[str] = set()
    for key in effective_perms:
        rt = _resource_type_from_key(key)
        if rt:
            resource_types.add(rt)

    return {
        "role_id": role["role_id"],
        "role_name": role.get("display_name") or role.get("name"),
        "explicit_perms": explicit_perms,
        "implied_perms": implied_perms,
        "nav_items": nav_items,
        "accessible_routes": accessible_routes,
        "visible_resource_types": sorted(resource_types),
    }