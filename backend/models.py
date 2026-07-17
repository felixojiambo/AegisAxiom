from pydantic import AnyUrl, BaseModel, EmailStr, Field, HttpUrl, field_validator
from typing import Optional, List, Dict, Any, Union, Literal
from datetime import datetime, timezone
import uuid


def gen_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class UserRole:
    ADMIN = "admin"
    DIRECTOR = "director"
    FINANCE = "finance"
    SALES_OPS = "sales_ops"
    SALES_REP = "sales_rep"
    AGENT = "agent"
    CUSTOMER = "customer"
    CONTRACTOR = "contractor"
    # Phase 1B.1 — tenant_admin is a SimpliMeta-delegated administrator
    # scoped to a single tenant. Holds delegation rights for AI Employee
    # assignments within their tenant. Full permission set is populated in
    # commit 1B.5 (provisioning + catalog APIs); 1B.1 only registers the
    # role so users can be assigned to it.
    TENANT_ADMIN = "tenant_admin"
    ALL_ROLES = ["admin", "director", "finance", "sales_ops", "sales_rep", "agent", "customer", "contractor", "tenant_admin"]

# ---- User Models ----
class User(BaseModel):
    user_id: str
    email: str
    name: str
    role: str
    tenant_id: Optional[str] = None
    account_id: Optional[str] = None
    contractor_business_id: Optional[str] = None  # Link to contractor business (for contractors)
    contractor_business_name: Optional[str] = None  # Cached for display
    picture: Optional[str] = None
    is_active: bool = True
    is_email_verified: bool = False
    zoho_contact_id: Optional[str] = None
    permissions: List[str] = Field(default_factory=list)
    extra_permissions: List[str] = Field(default_factory=list)  # Per-user permission overrides beyond their role
    # Resolved transitive closure of `permissions` against the implication
    # graph (auth_helper.resolve_implications). Authoritative for frontend
    # UI gating — closure-aware so e.g. director's `opportunities.edit.all`
    # surfaces `opportunities.view.all` here. Backend authorization does NOT
    # trust this field on the wire; route guards always recompute via
    # `has_perm()` per request. Populated by get_current_user and
    # _build_user_with_permissions; empty for User instances built outside
    # those auth paths.
    effective_permissions: List[str] = Field(default_factory=list)
    ai_usage_viewable_users: List[str] = Field(default_factory=list)  # User IDs this user can view AI usage for
    # Projects-module assignability flags (Wave 4 / Thread A1).
    # Drive the GET /api/projects/assignable-users picker independently of
    # the broader users.view permission. Default false on PM (admin opt-in)
    # and default true on member (active non-contractors). See
    # backend/seeds/seed_assignable_flags.py for the one-time backfill.
    #
    # Wave 5 / Thread B: these flags are now DERIVED from `pm_capacity_tier`.
    # Backfill in seed_assignable_flags.py sets the tier from existing role
    # and re-derives the flags. Capacity admin tab (Contract 3) lets admins
    # flip tiers directly via PATCH /api/projects/users/{user_id}/capacity-tier.
    #
    #   tier == "main_pm"      -> pm=True,  member=True   (top-of-funnel PM pool)
    #   tier == "overflow_pm"  -> pm=True,  member=True   (capacity backstop)
    #   tier == "member_only"  -> pm=False, member=True   (assignable, not as PM)
    #   tier == "none"         -> pm=False, member=False  (excluded from pickers)
    pm_capacity_tier: Optional[Literal["main_pm", "overflow_pm", "member_only", "none"]] = None
    is_assignable_as_pm: bool = False
    is_assignable_as_member: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---- Role Models ----
class Role(BaseModel):
    role_id: str
    name: str
    display_name: str
    description: Optional[str] = None
    color: str = "#64748B"
    is_system: bool = False
    is_default: bool = False
    permissions: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))




# ---- TIME TRACKING MODELS ----

class TimeEntry(BaseModel):
    entry_id: str
    user_id: str
    user_name: Optional[str] = None
    project_id: str
    project_name: Optional[str] = None
    contract_id: Optional[str] = None  # Link to contract for billing
    account_id: Optional[str] = None
    resource_type_id: Optional[str] = None  # Which resource type contractor is working as
    resource_type_name: Optional[str] = None

    # PM Task Linking (NEW)
    pm_task_id: Optional[str] = None  # Link to PMTask
    pm_task_name: Optional[str] = None
    pm_subtask_id: Optional[str] = None  # Link to sub-task (parent_task_id != None)

    date: str  # YYYY-MM-DD
    hours: float  # 0.167 increments (10 minutes), max 4.0
    notes: str  # Required
    note_quality: Optional[str] = "good"  # good, fair, poor
    is_billable: bool = True
    status: str = "draft"  # draft, submitted, pm_approved, admin_approved, rejected

    # Two-Stage Approval Tracking
    # Stage 1: PM Approval (always required)
    pm_approved_by: Optional[str] = None
    pm_approved_at: Optional[datetime] = None
    pm_approval_notes: Optional[str] = None

    # Stage 2: Admin Approval (only for late entries)
    admin_approved_by: Optional[str] = None
    admin_approved_at: Optional[datetime] = None
    admin_approval_notes: Optional[str] = None
    requires_admin_approval: bool = False  # True if created after lock

    # Rejection tracking
    rejected_by: Optional[str] = None
    rejected_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None

    # Rates (locked from contract at time of entry)
    cost_rate: Optional[float] = None  # Contractor rate (for AP)
    sell_rate: Optional[float] = None  # Customer rate (for AR)
    margin_percent: Optional[float] = None

    # ---- Wave 0 (contracts-time-mgmt) — additive billing classification ----
    # Wave 0 only adds these fields; derivation logic that translates
    # is_billable + contract.contract_type into billing_type lives in
    # Wave 3. is_billable is intentionally retained so legacy rows still
    # load and existing readers keep working.
    billing_type: Optional[str] = None
    cost_rate_snapshot: Optional[float] = None
    sell_rate_snapshot: Optional[float] = None
    effective_billing_mode: Optional[str] = None

    # Week tracking (for batch processing)
    week_start: Optional[str] = None  # Monday date
    week_end: Optional[str] = None    # Sunday date

    # Late submission handling
    is_late: bool = False
    created_by: Optional[str] = None  # User who created entry (if admin creating for contractor)
    locked: bool = False
    submitted_at: Optional[datetime] = None

    # Test data flag
    is_test_data: bool = False

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TimeEntryCreate(BaseModel):
    project_id: str
    task_id: Optional[str] = None
    task_name: str
    date: str
    hours: float
    notes: str
    billable: bool = True


class TimeEntryUpdate(BaseModel):
    task_id: Optional[str] = None
    task_name: Optional[str] = None
    date: Optional[str] = None
    hours: Optional[float] = None
    notes: Optional[str] = None
    billable: Optional[bool] = None

class UserCreate(BaseModel):
    email: str
    name: str
    password: str
    role: str = "customer"
    account_id: Optional[str] = None

    @field_validator('email')
    @classmethod
    def email_must_contain_at(cls, v):
        if '@' not in v or '.' not in v.split('@')[-1]:
            raise ValueError('Invalid email address')
        return v.lower().strip()


class UserLogin(BaseModel):
    email: str
    password: str

    @field_validator('email')
    @classmethod
    def email_must_contain_at(cls, v):
        if '@' not in v:
            raise ValueError('Invalid email address')
        return v.lower().strip()


class UserInvite(BaseModel):
    email: EmailStr
    name: str
    role: str
    account_id: Optional[str] = None
    resource_type_assignments: Optional[list] = None
    # Phase 1D.8 — admins inviting a user INTO a specific tenant
    # (rather than the caller's own tenant) pass this. Validated in the
    # invite handler: only root admins can target other tenants; non-
    # root callers either omit it or pass their own tenant_id.
    target_tenant_id: Optional[str] = None




# ---- Resource Type Assignment Models ----
class ResourceTypeAssignment(BaseModel):
    """
    Resource type assigned to an individual user/contractor with their cost rate.
    Auto-approves if rate ≤ contractor business rate, otherwise needs Procurement approval.
    """
    resource_type_id: str
    resource_type_name: str
    cost_rate: float  # What this specific contractor gets paid
    contractor_business_rate: Optional[float] = None  # Locked rate from contractor business (for comparison)

    # Approval (auto-approved if ≤ business rate, pending if >)
    approval_status: str = "approved"  # approved, pending, rejected (default approved for legacy)
    approved_by: Optional[str] = None
    approved_by_name: Optional[str] = None
    approved_date: Optional[datetime] = None
    rejection_reason: Optional[str] = None

    is_active: bool = True
    assigned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    assigned_by: Optional[str] = None  # Admin who assigned it


class UserResourceTypeAssignment(BaseModel):
    """Add/update resource type assignment for a user."""
    resource_type_id: str
    cost_rate: float
    is_active: bool = True

class PasswordReset(BaseModel):
    token: str
    new_password: str


class ForgotPasswordRequest(BaseModel):
    email: str

    @field_validator('email')
    @classmethod
    def email_must_contain_at(cls, v):
        if '@' not in v:
            raise ValueError('Invalid email address')
        return v.lower().strip()


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


# ---- Organization Branding ----
class OrganizationBranding(BaseModel):
    """Brand configuration for SOW document generation.

    Each organization can customize how their SOW documents appear
    (provider name, legal text, logo, etc.).
    """
    org_id: str
    provider_name: str = "MetaData Engineering d/b/a SimpliMeta"
    legal_text: str = (
        "Unless otherwise agreed to in this document, the Terms and Conditions are "
        "governed by MetaData Engineering d/b/a SimpliMeta's Master Services Agreement "
        "available for review at https://simplimeta.io/legal"
    )
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OrganizationBrandingUpdate(BaseModel):
    """Update organization branding settings."""
    provider_name: Optional[str] = None
    legal_text: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None


# ---- Account Models ----
# Allowed values for Account.partnership_status (signed-partnership agreement state).
# Distinct from the older `partner_status` operational state (active/inactive/suspended)
# which some dashboards still read — we keep both rather than migrating historical data.
PARTNERSHIP_STATUSES = ["none", "pending", "active", "terminated"]


class Account(BaseModel):
    account_id: str
    name: str
    account_type: str = "company"
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    industry: Optional[str] = None
    assigned_sales_rep_id: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    zoho_account_id: Optional[str] = None
    qbo_customer_id: Optional[str] = None
    # Partner-specific fields
    is_partner: bool = False
    partner_tier: Optional[str] = None  # gold, silver, bronze
    partner_since: Optional[datetime] = None
    partner_commission_rate: Optional[float] = None  # Percentage
    partner_active_deals_count: int = 0
    partner_total_revenue: float = 0.0
    partner_status: str = "active"  # active, inactive, suspended (legacy operational status)
    # Signed-partnership tracking (director ask — managed from Partners tab).
    partnership_status: Optional[str] = None  # one of PARTNERSHIP_STATUSES
    partnership_signed_date: Optional[datetime] = None
    # NOTE: `partner_commission_rate` (above) is a pre-existing field on this model.
    # `commission_rate` is the canonical field going forward — the Partners tab in
    # User Management reads and writes this one, and the detail-view Partnership
    # block does too. The old field is still read by the legacy "partner-specific
    # info" card on Accounts.js, so both currently coexist on the same document.
    commission_rate: Optional[float] = None  # percentage, 0-100
    is_test_data: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AccountCreate(BaseModel):
    name: str
    account_type: str = "company"
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    industry: Optional[str] = None
    assigned_sales_rep_id: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    # Partner fields (optional on create)
    is_partner: bool = False
    partner_tier: Optional[str] = None
    partner_since: Optional[datetime] = None
    partner_commission_rate: Optional[float] = None
    partnership_status: Optional[str] = None
    partnership_signed_date: Optional[datetime] = None
    commission_rate: Optional[float] = None
    is_test_data: bool = False


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    account_type: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    industry: Optional[str] = None
    assigned_sales_rep_id: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    # Partner fields (optional on update)
    is_partner: Optional[bool] = None
    partner_tier: Optional[str] = None
    partner_since: Optional[datetime] = None
    partner_commission_rate: Optional[float] = None
    partner_status: Optional[str] = None
    partnership_status: Optional[str] = None
    partnership_signed_date: Optional[datetime] = None
    commission_rate: Optional[float] = None
    is_test_data: Optional[bool] = None


# ---- Contact Models ----
class Contact(BaseModel):
    contact_id: str
    account_id: str
    account_name: Optional[str] = None
    first_name: str
    last_name: str
    name: Optional[str] = None  # Auto-computed: first_name + last_name
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile_phone: Optional[str] = None
    title: Optional[str] = None
    department: Optional[str] = None
    linkedin_url: Optional[str] = None
    preferred_contact_method: Optional[str] = None  # email, phone, mobile
    is_primary: bool = False  # Primary contact for the account
    is_active: bool = True

    # Link to user account (if contact has login)
    linked_user_id: Optional[str] = None

    # Source tracking
    source: str = "local"  # local, zoho, import
    zoho_contact_id: Optional[str] = None

    # Metadata
    notes: Optional[str] = None
    is_test_data: bool = False
    assigned_sales_rep_id: Optional[str] = None
    assigned_sales_rep_name: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    probability: int = 0
    description: Optional[str] = None
    zoho_deal_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DealCreate(BaseModel):
    name: str
    account_id: str
    stage: str = "Prospecting"
    amount: float = 0.0
    close_date: Optional[str] = None
    assigned_sales_rep_id: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    probability: int = 0
    description: Optional[str] = None
    is_test_data: bool = False


class DealUpdate(BaseModel):
    name: Optional[str] = None
    stage: Optional[str] = None
    amount: Optional[float] = None
    close_date: Optional[str] = None
    assigned_sales_rep_id: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    probability: Optional[int] = None
    description: Optional[str] = None



# ---- Opportunity Models ----

class Opportunity(BaseModel):
    opportunity_id: str
    # Per-opportunity tracking number (director ask — Telarus deal-reg
    # verification). Format: OPP-YYYY-NNNNNN. Assigned by the create route
    # via services.opportunity_numbering; immutable after creation.
    tracking_number: Optional[str] = None
    name: str
    account_id: str
    account_name: Optional[str] = None
    contact_id: Optional[str] = None
    contact_name: Optional[str] = None
    agent_of_record_id: Optional[str] = None  # Partner ID from partners collection
    agent_of_record_name: Optional[str] = None
    description: Optional[str] = None
    desired_proposal_date: Optional[str] = None  # YYYY-MM-DD
    # Expected close date for the deal. Separate from
    # ``desired_proposal_date`` (which is the prospective proposal-send
    # date). Drives the Opportunity Management close-date alert.
    # Backwards compatible — legacy rows without close_date load fine.
    close_date: Optional[str] = None  # YYYY-MM-DD
    priority: str = "medium"  # high, medium, low
    assigned_sales_ops_id: Optional[str] = None
    assigned_sales_ops_name: Optional[str] = None
    amount: float = 0.0
    stage: str = "prospect_opp"  # See OPPORTUNITY_STAGES
    commission_payment_method: Optional[str] = None  # telarus, appdirect, direct, custom
    lines_of_business: List[str] = Field(
        default_factory=list)  # professional_services, field_services, referral_mrr, wholesale_mrr

    # Sales Ops Internal Stages (only used when stage="proposal_development")
    sales_ops_stage: Optional[str] = None  # discovery, designing, procurement, review, complete

    # Ownership
    opportunity_owner_id: str
    opportunity_owner_name: Optional[str] = None

    # Conversion tracking
    converted_to_deal_id: Optional[str] = None
    converted_at: Optional[datetime] = None

    # Attachments & recordings
    notes: List[Dict[str, Any]] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    call_recordings: List[Dict[str, Any]] = Field(default_factory=list)

    # Metadata
    is_test_data: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Soft-delete (matches pattern on policies / workflow_rules)
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[str] = None


class OpportunityCreate(BaseModel):
    name: str
    account_id: str
    contact_id: Optional[str] = None
    agent_of_record_id: Optional[str] = None
    description: Optional[str] = None
    desired_proposal_date: Optional[str] = None
    # Expected close date (YYYY-MM-DD). Separate from
    # ``desired_proposal_date``; never auto-derived from it.
    close_date: Optional[str] = None
    priority: str = "medium"
    assigned_sales_ops_id: Optional[str] = None
    amount: float = 0.0
    stage: str = "prospect_opp"
    commission_payment_method: Optional[str] = None
    lines_of_business: List[str] = Field(default_factory=list)
    is_test_data: bool = False


class OpportunityUpdate(BaseModel):
    name: Optional[str] = None
    account_id: Optional[str] = None
    contact_id: Optional[str] = None
    agent_of_record_id: Optional[str] = None
    description: Optional[str] = None
    desired_proposal_date: Optional[str] = None
    # Expected close date (YYYY-MM-DD). Editable independently of
    # ``desired_proposal_date``.
    close_date: Optional[str] = None
    priority: Optional[str] = None
    assigned_sales_ops_id: Optional[str] = None
    amount: Optional[float] = None
    stage: Optional[str] = None
    commission_payment_method: Optional[str] = None
    lines_of_business: Optional[List[str]] = None
    sales_ops_stage: Optional[str] = None  # Internal Sales Ops stage
    # Accepted on the request so we can log attempts to mutate it, but the
    # create-time value is immutable — the update route drops this field.
    tracking_number: Optional[str] = None


# Opportunity stages
OPPORTUNITY_STAGES = [
    "prospect_opp",
    "prospect_lead",
    "proposal_development",
    "scoping_complete",
    "proposal_sent",
    "negotiation_review",
    "verbal_agreement",
    "closed_won",
    "closed_lost"
]


# ---- OPPORTUNITY RESOURCE ASSIGNMENTS (Procurement) ----

class OpportunityResourceAssignment(BaseModel):
    """Links opportunity to assigned contractor/resource with pricing."""
    assignment_id: str
    opportunity_id: str
    opportunity_name: Optional[str] = None

    # Resource Information
    resource_type_id: str
    resource_type_name: str

    # ---- LEFT SIDE: CUSTOMER BILLING (What We Sell) ----
    # Applied from Contract Template (editable by Sales Ops)
    customer_contract_type: str = "boh"  # boh, boa, fixed_fee, monthly

    # Billing Schedule (Unified with Contract Management)
    customer_billing_schedule: List[dict] = Field(default_factory=list)  # Array of billing items

    # Payment Terms (when customer pays AFTER invoice)
    customer_payment_terms: str = "net_30"  # net_7, net_14, net_30, net_45, net_60

    # Customer Pricing (varies by contract type)
    customer_hours_purchased: Optional[float] = None  # For BOH
    customer_sell_rate: Optional[float] = None  # Per hour (BOH, BOA, Monthly)
    customer_fixed_amount: Optional[float] = None  # For fixed_fee
    customer_monthly_rate: Optional[float] = None  # For monthly retainer

    # BOA-Specific Billing Configuration
    billing_frequency: Optional[str] = None  # monthly, biweekly, manual (for BOA)
    boa_billing_day: Optional[int] = None  # 1-28 (day of month for monthly BOA billing)
    estimated_boa_cost: Optional[float] = None  # Estimated total BOA cost (Field Services)

    # Fixed Fee-Specific Configuration
    billing_method: Optional[str] = None  # percentage, date, milestone (for fixed_fee)
    milestones: List[dict] = Field(default_factory=list)  # Milestones for fixed_fee billing

    # Calculated customer revenue
    total_sell: float = 0.0

    # ---- RIGHT SIDE: CONTRACTOR COST (What We Buy) ----
    # Pulled from Contractor Management
    contractor_business_id: Optional[str] = None
    contractor_business_name: Optional[str] = None
    contractor_user_id: Optional[str] = None  # Optional now - can be missing
    contractor_user_name: Optional[str] = None

    # Contractor Payment Configuration
    contractor_pay_type: Optional[str] = None  # block_hours, bill_actuals, fixed_price, monthly
    contractor_payment_terms: Optional[str] = None  # When we pay contractor
    contractor_billing_terms: Optional[str] = None  # How often we pay

    # Contractor Pricing (varies by pay type)
    contractor_hours_estimated: Optional[float] = None  # Estimated hours contractor will use
    contractor_cost_rate: Optional[float] = None  # Per hour rate
    contractor_fixed_cost: Optional[float] = None  # For fixed_price
    contractor_monthly_cost: Optional[float] = None  # For monthly

    # Calculated contractor cost
    total_cost: float = 0.0

    # ---- MIDDLE: PROFIT METRICS ----
    profit_amount: Optional[float] = None  # total_sell - total_cost
    profit_margin: Optional[float] = None  # (profit_amount / total_sell) * 100

    # Internal Resource Assignment (alternative to contractor)
    internal_user_id: Optional[str] = None
    internal_user_name: Optional[str] = None

    # Legacy fields (deprecated but kept for backward compatibility)
    pricing_model: Optional[str] = None  # Use customer_contract_type instead
    customer_billing_term: Optional[str] = None  # Use customer_contract_type instead
    estimated_hours: Optional[float] = None  # Use customer_hours_purchased instead
    cost_rate: Optional[float] = None  # Use contractor_cost_rate instead
    sell_rate: Optional[float] = None  # Use customer_sell_rate instead
    fixed_cost: Optional[float] = None  # Use contractor_fixed_cost instead
    fixed_sell: Optional[float] = None  # Use customer_fixed_amount instead
    payment_terms: Optional[str] = None  # Use customer_payment_terms instead
    billing_terms: Optional[str] = None  # Use customer_billing_terms instead

    # Status & Blockers
    status: str = "active"  # active, blocked_missing_contractor, blocked_pending_approval, completed
    blocker_reason: Optional[str] = None  # Why is this blocked?
    procurement_notified: bool = False  # Has procurement been notified?
    procurement_notification_id: Optional[str] = None  # Link to notification
    notes: Optional[str] = None

    # Metadata
    assigned_by: Optional[str] = None
    assigned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OpportunityLineItem(BaseModel):
    """Materials, hardware, wholesale MRR products for an opportunity."""
    line_item_id: str
    opportunity_id: str

    # Item Details
    category: str  # material, hardware, wholesale_mrr
    item_name: str
    description: Optional[str] = None
    quantity: float = 1.0
    unit: str = "each"  # each, hour, month, year

    # Pricing
    cost_price: float = 0.0  # What we pay
    sell_price: float = 0.0  # What we charge
    total_cost: float = 0.0  # quantity * cost_price
    total_sell: float = 0.0  # quantity * sell_price

    # Wholesale MRR Specific
    is_recurring: bool = False  # For MRR items
    recurring_term_months: Optional[int] = None  # 12, 24, 36, 60

    # Supplier/Vendor
    supplier_name: Optional[str] = None
    supplier_sku: Optional[str] = None

    # Status
    status: str = "pending"  # pending, quoted, approved, ordered
    notes: Optional[str] = None

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = None


class ReferralAgreement(BaseModel):
    """Tracks referral MRR agreements with expiration monitoring."""
    agreement_id: str
    opportunity_id: str
    opportunity_name: Optional[str] = None
    account_id: str
    account_name: str

    # Agreement Details
    agreement_name: str
    partner_name: str  # The referral partner
    agreement_term_years: int  # 1, 2, 3, 5
    start_date: str  # YYYY-MM-DD
    expiration_date: str  # YYYY-MM-DD (calculated)

    # Financial
    monthly_recurring_revenue: float = 0.0
    commission_rate: Optional[float] = None  # Percentage we pay to referral partner

    # Documents
    agreement_document_url: Optional[str] = None
    agreement_document_filename: Optional[str] = None

    # Status & Tracking
    status: str = "active"  # active, expiring_soon, expired, renewed
    expiration_notification_sent: bool = False
    days_until_expiration: Optional[int] = None  # Calculated field

    # Renewal Tracking
    renewal_opportunity_id: Optional[str] = None
    renewed_to_agreement_id: Optional[str] = None

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = None
    uploaded_by: Optional[str] = None


# ---- DISTRIBUTORS ----

class DistributorBase(BaseModel):
    """Hardware / material distributor that products can be sourced from."""
    name: str
    contact_name: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None
    website: Optional[AnyUrl] = None
    notes: Optional[str] = None
    status: str = "active"  # active | inactive


class DistributorCreate(DistributorBase):
    pass


class DistributorUpdate(BaseModel):
    name: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None
    website: Optional[AnyUrl] = None
    notes: Optional[str] = None
    status: Optional[str] = None


class Distributor(DistributorBase):
    distributor_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---- HARDWARE / MATERIAL REQUESTS ----

HARDWARE_MATERIAL_REQUEST_STATUSES = ["open", "sourcing", "ordered", "received", "cancelled"]
HARDWARE_MATERIAL_REQUEST_PRIORITIES = ["low", "medium", "high"]
HARDWARE_MATERIAL_REQUEST_TERMINAL_STATUSES = {"received", "cancelled"}


class HardwareMaterialRequestBase(BaseModel):
    """First-class procurement request distinct from OpportunityLineItem.

    Line items are sell-side (SOW pricing); requests are buy-side
    (something procurement needs to source). See
    docs/procurement-handoff-analysis.md Q8 for why these are separate.
    """
    opportunity_id: str
    distributor_id: Optional[str] = None
    product_id: Optional[str] = None
    item_description: Optional[str] = None  # free-text when no catalog product
    quantity: int = 1
    needed_by_date: Optional[str] = None    # ISO date string, matches opportunities' pattern
    priority: str = "medium"
    notes: Optional[str] = None
    status: str = "open"
    assigned_to: Optional[str] = None       # user_id


class HardwareMaterialRequestCreate(HardwareMaterialRequestBase):
    pass


class HardwareMaterialRequestUpdate(BaseModel):
    distributor_id: Optional[str] = None
    product_id: Optional[str] = None
    item_description: Optional[str] = None
    quantity: Optional[int] = None
    needed_by_date: Optional[str] = None
    priority: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    assigned_to: Optional[str] = None


class HardwareMaterialRequest(HardwareMaterialRequestBase):
    request_id: str
    requested_by: str
    requested_by_name: Optional[str] = None
    # Denormalised for list/display:
    distributor_name: Optional[str] = None
    product_name: Optional[str] = None
    opportunity_name: Optional[str] = None
    assigned_to_name: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[str] = None


class GrievanceComment(BaseModel):
    """Comment on a grievance."""
    comment_id: str
    commenter_id: str
    commenter_name: str
    comment: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ContractorGrievance(BaseModel):
    """Record of customer or internal complaints about a contractor (ENHANCED)."""
    grievance_id: str
    contractor_business_id: str
    contractor_user_id: Optional[str] = None  # Specific contractor user (if applicable)

    # Grievance Details
    title: str  # Brief summary
    description: str  # Detailed description
    severity: str  # low, medium, high, critical
    status: str = "open"  # open, in_progress, resolved

    # Reporter Info
    reported_by_id: str
    reported_by_name: str
    reporter_type: str = "internal"  # customer, internal, partner

    # Comments & Resolution
    comments: List[GrievanceComment] = Field(default_factory=list)
    resolved_at: Optional[datetime] = None
    resolved_by_id: Optional[str] = None
    resolved_by_name: Optional[str] = None

    # Impact
    affects_rating: bool = False

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))



# ---- CONTRACTOR SURVEY/RATING MODELS ----

class SurveyDimensionRating(BaseModel):
    """Individual dimension rating in contractor survey."""
    dimension: str  # e.g., "overall_professionalism"
    rating: int  # 1-5 scale


class ContractorSurvey(BaseModel):
    """Survey sent to rate a contractor's performance."""
    survey_id: str
    contractor_business_id: str
    contractor_user_id: str  # Specific contractor being rated
    contractor_user_name: str

    # Project Context
    project_name: Optional[str] = None
    customer_name: Optional[str] = None
    opportunity_id: Optional[str] = None

    # Survey Access
    public_token: str  # Unique token for public access
    survey_url: str  # Full public URL with token

    # Recipient Info (who will fill the survey)
    sent_to_name: Optional[str] = None
    sent_to_email: Optional[str] = None
    sent_by_id: str
    sent_by_name: str

    # Status
    status: str = "sent"  # sent, completed
    sent_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    # Response Data (populated when completed)
    hire_likelihood: Optional[int] = None  # 1-10 scale
    dimension_ratings: List[SurveyDimensionRating] = Field(default_factory=list)  # 10 dimensions, each 1-5
    calculated_rating: Optional[float] = None  # Average of dimension_ratings (out of 5)

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ContractorSurveyCreate(BaseModel):
    """Create a new contractor survey."""
    contractor_business_id: str
    contractor_user_id: str
    project_name: Optional[str] = None
    customer_name: Optional[str] = None
    opportunity_id: Optional[str] = None
    sent_to_name: Optional[str] = None
    sent_to_email: Optional[str] = None


class ContractorSurveySubmit(BaseModel):
    """Public survey submission (no auth required)."""
    project_name: str
    customer_name: str
    hire_likelihood: int  # 1-10
    dimension_ratings: List[SurveyDimensionRating]  # Must have 10 dimensions


# Lines of business
LINES_OF_BUSINESS = [
    "professional_services",
    "field_services",
    "referral_mrr",
    "wholesale_mrr"
]

# Commission payment methods
COMMISSION_PAYMENT_METHODS = [
    "telarus",
    "appdirect",
    "direct"
]


# ---- Contract Models ----

class ResourceType(BaseModel):
    """Resource type with billing rate and hours tracking for BOH contracts."""
    id: str
    name: str  # e.g., "Senior Developer", "Junior Developer", "Designer"
    sell_rate: float  # Billing rate to client
    hours_purchased: float = 0.0  # Total hours purchased of this type (BOH)
    hours_used: float = 0.0  # Hours consumed of this type
    hours_remaining: float = 0.0  # Calculated: purchased - used
    description: Optional[str] = None


class AssignedResource(BaseModel):
    """Worker assigned to a contract with their cost rate and resource type."""
    user_id: str
    name: str
    resource_type_id: str  # Links to ResourceType.id
    cost_rate: float  # What we pay this worker
    role: Optional[str] = None  # e.g., "Lead Developer", "UI Designer"


class Contract(BaseModel):
    contract_id: str
    name: str
    account_id: str
    account_name: Optional[str] = None
    contract_type: str = "fixed_fee"
    total_hours_purchased: float = 0.0
    hours_consumed: float = 0.0
    fixed_amount: float = 0.0
    monthly_rate: float = 0.0
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: str = "active"
    assigned_sales_rep_id: Optional[str] = None
    zoho_project_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Financial Summaries (Phase 1)
    ar_summary: Optional[Dict[str, Any]] = None  # Accounts Receivable (what customer owes us)
    ap_summary: Optional[Dict[str, Any]] = None  # Accounts Payable (what we owe contractors)
    margin_summary: Optional[Dict[str, Any]] = None

    # ---- Wave 0 (contracts-time-mgmt) — additive schema foundation ----
    # All fields below are optional with safe defaults so legacy contract
    # documents (created before this wave) continue to deserialize. No
    # behavior is wired up to these fields in Wave 0; the approval queue,
    # BOH-to-BOA overflow, billing triggers, Zoho import and invoice
    # batch waves consume them in later commits.

    # Approval queue state (Wave 1 — Finance Command Center).
    approval_status: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    approval_notes: Optional[str] = None

    # BOH + BOA overflow tracking (Wave 2). A contract typed boh_with_boa
    # flips bill_and_actuals_active=True once total_hours_purchased is
    # exhausted; subsequent time bills against overage_* rates.
    bill_and_actuals_active: bool = False
    overage_hours: float = 0.0
    overage_bill_rate: Optional[float] = None
    overage_cost_rate: Optional[float] = None

    # Contract dates / value.
    signed_date: Optional[datetime] = None
    financial_commit: Optional[float] = None

    # Forecast snapshot pulled from the source SOW at approval time
    # (Wave 1). Shape lives in Wave 2 derivation; default empty dict is
    # safe for legacy reads.
    forecast: Dict[str, Any] = Field(default_factory=dict)

    # SOW source linkage. source_sow_id points at the SOW row this
    # contract was created from; original_sow_snapshot captures the
    # frozen scope at approval; spirit_summary is the human-readable
    # intent statement carried alongside.
    source_sow_id: Optional[str] = None
    original_sow_snapshot: Dict[str, Any] = Field(default_factory=dict)
    spirit_summary: Optional[str] = None

    # Tenancy / external import bridge. tenant_id is set on new rows by
    # the multi-tenancy filter; the external_* fields are populated by
    # the Zoho import wave so a contract can be matched back to its
    # source system without ambiguity.
    tenant_id: Optional[str] = None
    import_source: Optional[str] = None
    external_contract_id: Optional[str] = None
    external_source_synced_at: Optional[datetime] = None  # Profitability metrics


class ContractCreate(BaseModel):
    name: str
    account_id: str
    contract_type: str = "fixed_fee"
    total_hours_purchased: float = 0.0
    fixed_amount: float = 0.0
    monthly_rate: float = 0.0
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    assigned_sales_rep_id: Optional[str] = None


class ContractUpdate(BaseModel):
    name: Optional[str] = None
    contract_type: Optional[str] = None
    total_hours_purchased: Optional[float] = None
    hours_consumed: Optional[float] = None
    fixed_amount: Optional[float] = None
    monthly_rate: Optional[float] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = None


# ---- Invoice Models ----
class Invoice(BaseModel):
    invoice_id: str
    invoice_number: str
    account_id: str
    account_name: Optional[str] = None
    deal_id: Optional[str] = None
    contract_id: Optional[str] = None
    amount: float = 0.0
    tax_amount: float = 0.0
    total_amount: float = 0.0
    status: str = "draft"  # draft, pending_approval, approved, rejected, sent_to_qb, paid
    due_date: Optional[str] = None
    paid_date: Optional[str] = None
    qbo_invoice_id: Optional[str] = None
    description: Optional[str] = None
    assigned_sales_rep_id: Optional[str] = None
    is_past_due: bool = False
    submitted_for_approval_at: Optional[datetime] = None
    submitted_by: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    approval_notes: Optional[str] = None
    is_test_data: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class InvoiceCreate(BaseModel):
    account_id: str
    deal_id: Optional[str] = None
    contract_id: Optional[str] = None
    amount: float = 0.0
    tax_amount: float = 0.0
    due_date: Optional[str] = None
    description: Optional[str] = None
    is_past_due: bool = False


class InvoiceUpdate(BaseModel):
    status: Optional[str] = None
    paid_date: Optional[str] = None
    qbo_invoice_id: Optional[str] = None
    due_date: Optional[str] = None
    approval_notes: Optional[str] = None


# ---- Commission Models ----
class Commission(BaseModel):
    commission_id: str
    deal_id: str
    deal_name: Optional[str] = None
    user_id: str
    user_name: Optional[str] = None
    commission_type: str = "agent"
    amount_owed: float = 0.0
    amount_paid: float = 0.0
    potential_amount: float = 0.0
    commission_rate: float = 0.0
    status: str = "pending"
    notes: Optional[str] = None
    paid_date: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CommissionCreate(BaseModel):
    deal_id: str
    user_id: str
    commission_type: str = "agent"
    amount_owed: float = 0.0
    potential_amount: float = 0.0
    commission_rate: float = 0.0
    notes: Optional[str] = None


class CommissionUpdate(BaseModel):
    amount_owed: Optional[float] = None
    amount_paid: Optional[float] = None
    potential_amount: Optional[float] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    paid_date: Optional[str] = None



# ---- Zoho Write-back Models ----
class ZohoDealUpdate(BaseModel):
    deal_name: Optional[str] = None
    stage: Optional[str] = None
    amount: Optional[float] = None
    close_date: Optional[str] = None
    force: bool = False  # bypass conflict check

class ZohoCommissionUpdate(BaseModel):
    sales_rep_commission: Optional[float] = None
    sales_rep_commission_status: Optional[str] = None
    agent_commission: Optional[float] = None
    agent_commission_status: Optional[str] = None
    dir_commission: Optional[float] = None
    dir_commission_status: Optional[str] = None
    payment_date: Optional[str] = None
    invoice_no: Optional[str] = None
    force: bool = False  # bypass conflict check



# ---- Field Job Models ----

# Canonical FieldJob.status values. Lives next to the model so any
# additions update one place. Mirrors the existing precedent in this
# file (PARTNERSHIP_STATUSES, HARDWARE_MATERIAL_REQUEST_STATUSES).
# Importers should derive their own structures from this list rather
# than hand-syncing copies elsewhere.
FIELD_JOB_STATUSES = [
    "scheduled", "confirmed", "in_progress", "completed", "cancelled",
]


class FieldJob(BaseModel):
    job_id: str
    name: str
    account_id: str
    account_name: Optional[str] = None
    contract_id: Optional[str] = None
    deal_id: Optional[str] = None
    deal_name: Optional[str] = None
    status: str = "scheduled"  # one of FIELD_JOB_STATUSES
    priority: str = "medium"  # low, medium, high, urgent

    # Job Category (Phase 3A)
    job_category: str = "execution"  # "survey" or "execution"
    linked_survey_job_id: Optional[str] = None  # For execution jobs linked to a survey

    # People
    assigned_tech_id: Optional[str] = None
    assigned_tech_name: Optional[str] = None
    assigned_pm_id: Optional[str] = None
    assigned_pm_name: Optional[str] = None
    assigned_sales_rep_id: Optional[str] = None

    # Customer contact for this job
    customer_contact_name: Optional[str] = None
    customer_contact_email: Optional[str] = None
    customer_contact_phone: Optional[str] = None

    # Scheduling
    scheduled_date: Optional[str] = None  # YYYY-MM-DD
    scheduled_start: Optional[str] = None  # HH:MM (24h)
    scheduled_end: Optional[str] = None  # HH:MM (24h)
    estimated_hours: float = 0.0
    actual_hours: float = 0.0

    # Financials
    labor_cost: float = 0.0
    material_cost: float = 0.0
    total_cost: float = 0.0
    billable_hours: float = 0.0

    # Scheduling override
    schedule_override: bool = False  # True if scheduling rules were overridden
    override_approved_by: Optional[str] = None
    override_reason: Optional[str] = None

    # Descriptions
    description: Optional[str] = None
    notes: Optional[str] = None
    job_date: Optional[str] = None  # Legacy compat

    # Check-in / Check-out
    check_in_time: Optional[datetime] = None
    check_out_time: Optional[datetime] = None
    check_in_notes: Optional[str] = None
    check_out_notes: Optional[str] = None

    # Photos
    photos: List[Dict[str, Any]] = Field(default_factory=list)

    # Job type + checklists
    job_type: Optional[str] = None  # References job_types.type_id
    job_type_name: Optional[str] = None
    checklist_checkin: List[Dict[str, Any]] = Field(default_factory=list)
    checklist_checkout: List[Dict[str, Any]] = Field(default_factory=list)

    # Survey & SOW (Phase 3A)
    survey_form_data: Optional[Dict[str, Any]] = None  # Stores survey responses
    sow_data: Optional[Dict[str, Any]] = None  # Generated Statement of Work
    sow_generated_at: Optional[datetime] = None
    sow_edited_at: Optional[datetime] = None
    sow_version: int = 1  # Track SOW revisions

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FieldJobCreate(BaseModel):
    name: str
    account_id: str
    contract_id: Optional[str] = None
    deal_id: Optional[str] = None
    priority: str = "medium"
    job_category: str = "execution"  # "survey" or "execution"
    linked_survey_job_id: Optional[str] = None  # Link to survey job
    job_type: Optional[str] = None
    assigned_tech_id: Optional[str] = None
    assigned_pm_id: Optional[str] = None
    customer_contact_name: Optional[str] = None
    customer_contact_email: Optional[str] = None
    customer_contact_phone: Optional[str] = None
    scheduled_date: Optional[str] = None
    scheduled_start: Optional[str] = None
    scheduled_end: Optional[str] = None
    estimated_hours: float = 0.0
    labor_cost: float = 0.0
    material_cost: float = 0.0
    billable_hours: float = 0.0
    description: Optional[str] = None
    notes: Optional[str] = None
    job_date: Optional[str] = None  # Legacy compat
    schedule_override: bool = False
    override_reason: Optional[str] = None


class FieldJobUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    job_type: Optional[str] = None
    assigned_tech_id: Optional[str] = None
    assigned_pm_id: Optional[str] = None
    contract_id: Optional[str] = None
    customer_contact_name: Optional[str] = None
    customer_contact_email: Optional[str] = None
    customer_contact_phone: Optional[str] = None
    scheduled_date: Optional[str] = None
    scheduled_start: Optional[str] = None
    scheduled_end: Optional[str] = None
    estimated_hours: Optional[float] = None
    labor_cost: Optional[float] = None
    material_cost: Optional[float] = None
    billable_hours: Optional[float] = None
    description: Optional[str] = None
    notes: Optional[str] = None
    job_date: Optional[str] = None
    schedule_override: Optional[bool] = None
    override_reason: Optional[str] = None


# ---- Account Operating Hours ----
class DayHours(BaseModel):
    open: str = "09:00"  # HH:MM 24h
    close: str = "17:00"
    closed: bool = False


class AccountOperatingHours(BaseModel):
    account_id: str
    monday: DayHours = Field(default_factory=DayHours)
    tuesday: DayHours = Field(default_factory=DayHours)
    wednesday: DayHours = Field(default_factory=DayHours)
    thursday: DayHours = Field(default_factory=DayHours)
    friday: DayHours = Field(default_factory=DayHours)
    saturday: DayHours = Field(default_factory=lambda: DayHours(closed=True))
    sunday: DayHours = Field(default_factory=lambda: DayHours(closed=True))
    timezone: str = "America/New_York"
    notes: Optional[str] = None


# ---- Scheduling Rules ----
class SchedulingRules(BaseModel):
    enforce_operating_hours: bool = True
    enforce_max_hours_per_day: bool = True
    allow_weekend_scheduling: bool = False
    require_override_approval: bool = True
    min_advance_notice_hours: int = 24  # Minimum hours before job start


# ---- Partner Application Models ----
class PartnerApplication(BaseModel):
    application_id: str
    company_name: str
    contact_name: str
    email: str
    phone: Optional[str] = None
    website: Optional[str] = None
    business_description: Optional[str] = None
    services_interested: Optional[List[str]] = None
    status: str = "pending"
    applied_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    notes: Optional[str] = None


class PartnerApplicationCreate(BaseModel):
    company_name: str
    contact_name: str
    email: EmailStr
    phone: Optional[str] = None
    website: Optional[str] = None
    business_description: Optional[str] = None
    services_interested: Optional[List[str]] = None


# ---- Chat Models ----
class ChatMessage(BaseModel):
    message_id: str
    session_id: str
    user_id: str
    role: str
    content: str
    thinking: Optional[List[dict]] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatSession(BaseModel):
    session_id: str
    user_id: str
    title: str = "New Chat"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatMessageCreate(BaseModel):
    content: str


# ---- Request Models (replacing raw `data: dict`) ----

class GoogleCodeRequest(BaseModel):
    code: str


class GoogleInviteCodeRequest(BaseModel):
    code: str
    invite_token: str

class AdminSetupRequest(BaseModel):
    email: str
    password: str
    name: str = "Admin"

    @field_validator('email')
    @classmethod
    def email_must_contain_at(cls, v):
        if '@' not in v or '.' not in v.split('@')[-1]:
            raise ValueError('Invalid email address')
        return v.lower().strip()

class OAuthCodeRequest(BaseModel):
    code: str

class RoleCreateRequest(BaseModel):
    name: str
    display_name: Optional[str] = None
    description: str = ""
    color: str = "#64748B"
    permissions: List[str] = Field(default_factory=list)

class RoleUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    permissions: Optional[List[str]] = None

class RoleCloneRequest(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None

class CommissionRejectRequest(BaseModel):
    reason: str = ""

class InvoiceApproveRequest(BaseModel):
    approval_notes: str = ""


class InvoiceRejectRequest(BaseModel):
    rejection_reason: str
class UserUpdateRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    account_id: Optional[str] = None
    is_active: Optional[bool] = None
    resource_type_assignments: Optional[List[dict]] = None
    extra_permissions: Optional[List[str]] = None  # Per-user permission overrides
    ai_usage_viewable_users: Optional[List[str]] = None  # User IDs this user can view AI usage for

class PartnerRejectRequest(BaseModel):
    reason: str = "Your application was not approved at this time."


# ---- OAuth Token Models ----
class OAuthToken(BaseModel):
    token_id: str
    provider: str
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[datetime] = None
    scope: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---- Webhook Log Models ----
class WebhookLog(BaseModel):
    log_id: str
    source: str
    event_type: str
    payload: Dict[str, Any] = {}
    processed: bool = False
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---- CONTRACTOR BUSINESS MODELS ----

# ---- CONTRACTOR BUSINESS MODELS ----

# Resource Type Configuration for Contractor Business
class ContractorBusinessResourceType(BaseModel):
    resource_type_id: str
    resource_type_name: str
    pricing_models_accepted: List[str] = Field(default_factory=list)  # ["block_hours", "bill_actuals", "fixed_price"]
    rate_cards: Dict[str, Dict[str, float]] = Field(
        default_factory=dict)  # {"block_hours": {"sell_rate": 180, "default_cost_rate": 150}}

    # Cost Rate & Approval (for Procurement gatekeeper)
    cost_rate: float = 0.0  # What we pay this contractor for this resource type (LOCKED after approval)
    default_cost_rate: float = 0.0  # Default rate from resource_types collection (for comparison)
    is_locked: bool = False  # True after Procurement approval - warns if contractor tries to change

    approval_status: str = "pending"  # pending, approved, rejected (ALWAYS pending when first added)
    approved_by: Optional[str] = None  # user_id of procurement approver
    approved_by_name: Optional[str] = None
    approved_date: Optional[datetime] = None
    rejection_reason: Optional[str] = None

    is_active: bool = True
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Special Offers/Promotions
class ContractorBusinessSpecial(BaseModel):
    special_id: str
    resource_type_id: Optional[str] = None  # Which resource type this applies to
    resource_type_name: Optional[str] = None
    discount_type: str = "percentage"  # percentage, fixed_amount
    discount_value: float = 0.0  # 10 = 10% off or $10 off
    description: str
    valid_from: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_until: Optional[datetime] = None
    notes: Optional[str] = None
    approved_by: Optional[str] = None
    approved_by_name: Optional[str] = None
    approved_at: Optional[datetime] = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Contractor Services (Wholesale MRR offerings)
class ContractorService(BaseModel):
    """
    Wholesale MRR service CREATED by a contractor business.
    Creates a new product in Products & Services database owned by this contractor.
    Procurement reviews, sets sell price, and approves before Solutions Design can use.
    """
    service_id: str  # Unique ID for this service
    product_id: str  # References product_id in products collection (created when service is created)

    # Service Definition (contractor fills these)
    service_name: str
    service_description: str = ""
    recurrency: str = "monthly"  # one-time, daily, weekly, monthly, annually
    cost_to_business: float = 0.0  # What we pay the contractor per recurrency

    # Pricing (Procurement sets these on approval)
    sell_price: Optional[float] = None  # What customer pays (set by Procurement during approval)
    margin: Optional[float] = None  # Calculated after Procurement sets sell price
    margin_percent: Optional[float] = None  # Calculated: (margin / sell_price) * 100

    # Approval (Procurement gatekeeper)
    approval_status: str = "pending"  # pending, approved, rejected
    approved_by: Optional[str] = None  # user_id of procurement approver
    approved_by_name: Optional[str] = None
    approved_date: Optional[datetime] = None
    rejection_reason: Optional[str] = None

    # Metadata
    notes: Optional[str] = None
    added_by: Optional[str] = None  # user_id who created this service
    added_by_name: Optional[str] = None
    added_date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True



class ContractorBusiness(BaseModel):
    business_id: str
    business_name: str
    contact_name: Optional[str] = None  # Primary contact person
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None

    # Address
    address_street: Optional[str] = None
    address_city: Optional[str] = None
    address_state: Optional[str] = None
    address_zip: Optional[str] = None
    address_country: Optional[str] = "USA"

    # Tax Information
    tax_id_ein: Optional[str] = None  # EIN/Tax ID
    w9_on_file: bool = False
    w9_file_url: Optional[str] = None  # Uploaded W9 document
    is_1099_eligible: bool = True

    # Billing Terms
    billing_frequency: str = "weekly"  # weekly, biweekly, monthly
    payment_terms: str = "net_30"  # net_14, net_30, net_45, net_60

    # Payment Method
    payment_method: Optional[str] = None  # ACH, Wire, Check, PayPal, Venmo, Other
    payment_details: Optional[Dict[str, Any]] = None  # Bank info, addresses, etc. (encrypted in prod)

    # Billing Contact (AP specific contact)
    billing_contact_name: Optional[str] = None
    billing_email: Optional[str] = None
    billing_phone: Optional[str] = None
    billing_address_street: Optional[str] = None
    billing_address_city: Optional[str] = None
    billing_address_state: Optional[str] = None
    billing_address_zip: Optional[str] = None

    # Bank Account Details (for ACH/Wire payments)
    bank_account_details: Optional[Dict[str, Any]] = None
    # Expected structure: {
    #   "bank_name": str,
    #   "account_holder_name": str,
    #   "routing_number": str,
    #   "account_number": str,
    #   "account_type": str  # checking, savings
    # }

    # Business Status
    status: str = "active"  # active, inactive
    notes: Optional[str] = None

    # ---- SALES OPS EXTENSIONS ----
    # Emergency Contact
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    emergency_contact_relationship: Optional[str] = None

    # Performance & Partnership
    rating: Optional[float] = None  # 1-5 stars
    partnership_tier: Optional[str] = None  # none, bronze, silver, gold, platinum

    # Marketing Funds
    marketing_funds_allocated: float = 0.0
    marketing_funds_used: float = 0.0

    # Legal Documents (array of document metadata)
    legal_documents: List[Dict[str, Any]] = Field(default_factory=list)
    # Example: [{"type": "NDA", "filename": "nda.pdf", "uploaded_at": "...", "url": "..."}]

    # Grievances (array of complaint records)
    grievances: List[Dict[str, Any]] = Field(default_factory=list)
    # Example: [{"date": "...", "reporter": "...", "description": "...", "resolved": false}]

    # Interview & Onboarding
    interview_notes: Optional[str] = None
    onboarding_date: Optional[datetime] = None
    interviewer_name: Optional[str] = None

    # Resource Types & Rates (what they can do)
    resource_type_capabilities: List[Dict[str, Any]] = Field(default_factory=list)
    # Example: [{"resource_type_id": "rt_...", "resource_type_name": "Field Tech", "hourly_rate": 75}]

    # NEW: Enhanced Resource Types with Pricing Models
    resource_types_offered: List[ContractorBusinessResourceType] = Field(default_factory=list)

    # NEW: Active Specials (pushed to Solutions Design)
    active_specials: List[ContractorBusinessSpecial] = Field(default_factory=list)

    # NEW: Services Offered (Wholesale MRR products - need Procurement approval)
    services_offered: List[ContractorService] = Field(default_factory=list)

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = None


class ContractorBusinessCreate(BaseModel):
    business_name: str
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    address_street: Optional[str] = None
    address_city: Optional[str] = None
    address_state: Optional[str] = None
    address_zip: Optional[str] = None
    address_country: Optional[str] = "USA"
    tax_id_ein: Optional[str] = None
    w9_on_file: bool = False
    is_1099_eligible: bool = True
    billing_frequency: str = "weekly"
    payment_terms: str = "net_30"
    payment_method: Optional[str] = None
    payment_details: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None


class ContractorBusinessUpdate(BaseModel):
    business_name: Optional[str] = None
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    address_street: Optional[str] = None
    address_city: Optional[str] = None
    address_state: Optional[str] = None
    address_zip: Optional[str] = None
    address_country: Optional[str] = None
    tax_id_ein: Optional[str] = None
    w9_on_file: Optional[bool] = None
    w9_file_url: Optional[str] = None
    is_1099_eligible: Optional[bool] = None
    billing_frequency: Optional[str] = None
    payment_terms: Optional[str] = None
    payment_method: Optional[str] = None
    payment_details: Optional[Dict[str, Any]] = None
    status: Optional[str] = None
    notes: Optional[str] = None

    # Billing Contact Info
    billing_contact_name: Optional[str] = None
    billing_email: Optional[str] = None
    billing_phone: Optional[str] = None
    billing_address_street: Optional[str] = None
    billing_address_city: Optional[str] = None
    billing_address_state: Optional[str] = None
    billing_address_zip: Optional[str] = None
    bank_account_details: Optional[Dict[str, Any]] = None

    # Sales Ops Extensions
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    emergency_contact_relationship: Optional[str] = None
    rating: Optional[float] = None
    partnership_tier: Optional[str] = None
    marketing_funds_allocated: Optional[float] = None
    marketing_funds_used: Optional[float] = None
    legal_documents: Optional[List[Dict[str, Any]]] = None
    grievances: Optional[List[Dict[str, Any]]] = None
    interview_notes: Optional[str] = None
    onboarding_date: Optional[datetime] = None
    interviewer_name: Optional[str] = None
    resource_type_capabilities: Optional[List[Dict[str, Any]]] = None


# ---- AP BILLS (ACCOUNTS PAYABLE) ----

class APBillLineItem(BaseModel):
    contractor_name: str
    project_name: Optional[str] = None
    contract_name: Optional[str] = None
    hours: float
    rate: float
    amount: float
    time_entry_ids: List[str] = Field(default_factory=list)


class APBill(BaseModel):
    bill_id: str
    bill_number: str  # Auto-generated: "AP-2026-001"

    # Vendor Information (contractor business or individual)
    contractor_business_id: Optional[str] = None  # If part of a business
    contractor_business_name: Optional[str] = None
    contractor_user_id: Optional[str] = None  # If solo contractor
    contractor_name: str

    # Bill Period
    period_start: str  # YYYY-MM-DD
    period_end: str  # YYYY-MM-DD
    billing_frequency: str  # weekly, biweekly, monthly

    # Line Items (time entries grouped)
    line_items: List[APBillLineItem] = Field(default_factory=list)
    time_entry_ids: List[str] = Field(default_factory=list)  # All included time entries

    # Amounts
    subtotal: float
    total: float

    # Status & Approval
    status: str = "draft"  # draft, pending_finance_review, approved, paid, cancelled

    # Payment Details
    due_date: Optional[str] = None  # Calculated from payment_terms
    payment_terms: Optional[str] = None  # net_14, net_30, etc.
    payment_method: Optional[str] = None  # From contractor business
    payment_date: Optional[str] = None
    payment_notes: Optional[str] = None

    # Audit Trail
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    paid_at: Optional[datetime] = None
    paid_by: Optional[str] = None

    # Notes
    notes: Optional[str] = None


class APBillCreate(BaseModel):
    contractor_business_id: Optional[str] = None
    contractor_user_id: Optional[str] = None
    period_start: str
    period_end: str
    line_items: List[APBillLineItem]
    notes: Optional[str] = None


class APBillUpdate(BaseModel):
    line_items: Optional[List[APBillLineItem]] = None
    notes: Optional[str] = None
    payment_notes: Optional[str] = None



# ---- SOLUTIONS DESIGN - SOW (STATEMENT OF WORK) MODELS ----

class SOWSubtask(BaseModel):
    """Subtask under a SOW bullet (task). Optional third level of the
    SOW Builder hierarchy (phase → task → subtask). The substrate doc
    `docs/sales-ops/SHARED_SOW_BUILDER_SUBSTRATE.md` describes this
    three-level shape; this model reconciles the live data model to
    that description so the active editor surface can offer per-task
    subtask reorder without inventing structure on the wire.

    The field set is intentionally narrow: stable id, free-text
    description, persisted display order. Hours and deliverable stay
    on the parent SOWBullet — subtasks are bullets-within-a-bullet,
    not their own billing unit. Existing SOW documents that have no
    subtasks remain valid; Pydantic fills the field with an empty
    list."""

    subtask_id: str
    description: str = ""
    order: int = 0  # Display order within parent SOWBullet


class SOWBullet(BaseModel):
    """Individual task/bullet under a milestone."""
    bullet_id: str
    bullet_text: str  # Description of the task
    estimated_hours: Optional[float] = None  # For resource planning
    deliverable: Optional[str] = None  # What the client receives
    order: int = 0  # Display order within milestone
    # Optional third level of the SOW hierarchy. Existing bullets that
    # have no subtasks deserialize unchanged because Pydantic defaults
    # this to an empty list; the editor surface only renders the
    # subtask UI when this list is non-empty (or the user adds the
    # first subtask via the Add subtask button).
    subtasks: List[SOWSubtask] = Field(default_factory=list)


class SOWMilestone(BaseModel):
    """Milestone grouping for SOW tasks."""
    milestone_id: str
    milestone_name: str
    order: int = 0  # Display order in SOW
    bullets: List[SOWBullet] = Field(default_factory=list)


class SOWAuthorizedUser(BaseModel):
    """Authorized user for BOA contracts (can request hours)."""
    user_id: str  # Can be uuid or reference
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    max_hours_allowed: Optional[float] = None  # Max hours this user can request


class SOWProductItem(BaseModel):
    """Product/Material/Hardware/Service item in SOW (LOB-specific).

    Field Services Sales Ops must be able to list required items even
    when the product catalog has no match — Procurement prices/sources
    them later. ``source`` carries that intent:
      * ``"catalog"`` — selected from the products catalog
      * ``"manual"`` — entered manually with a known price
      * ``"needs_sourcing"`` — entered manually, awaiting Procurement
    ``product_id``/``product_sku`` are optional so a manual line item
    can persist without a catalog product row.
    """

    item_id: str
    product_id: Optional[str] = None  # Reference to products collection (None for manual)
    product_name: str
    product_sku: Optional[str] = None
    item_category: str  # services, wholesale_mrr, hardware, materials (matches Product.item_category)
    quantity: int = 1
    unit_price: float = 0.0  # Per unit/month
    total_price: float = 0.0  # quantity * unit_price
    notes: Optional[str] = None
    source: Optional[str] = None  # catalog | manual | needs_sourcing
    supplier_name: Optional[str] = None
    unit: Optional[str] = None

    # Procurement execution lifecycle. All optional so existing SOW
    # documents deserialize unchanged. Mutated only by the single-item
    # routes in sales_ops_procurement.py — the SOW create-or-update
    # path preserves whatever values are already on persisted items so
    # Sales Ops re-saves do not clobber Procurement/PMO state.
    procurement_status: Optional[str] = None  # pending_pricing | priced | ordered | delivered
    supplier_source_url: Optional[HttpUrl] = None
    availability_note: Optional[str] = None
    procurement_notes: Optional[str] = None
    expected_delivery_date: Optional[datetime] = None
    order_reference: Optional[str] = None
    tracking_number: Optional[str] = None
    tracking_url: Optional[HttpUrl] = None
    ordered_at: Optional[datetime] = None
    ordered_by: Optional[str] = None
    delivered_at: Optional[datetime] = None
    delivered_by: Optional[str] = None
    delivery_notes: Optional[str] = None


class SOWProseSection(BaseModel):
    """A free-text section attached to a SOW.

    Used by Professional Services SOWs to carry the canonical
    seven-section proposal shape (Executive Summary, SMART
    Objective, Scope of Work, Estimated Time, Assumptions,
    Responsibilities, Out of Scope) plus the optional sections
    (Risk Assessment, Solution Overview, In-Scope Inventory,
    Conclusion). The model is permissive on kind so a deploy can
    add new section kinds without a migration; the canonical kinds
    and ordering live in
    backend/services/proposal_generation.py.

    Stored alongside the milestones list so the structured Scope
    of Work bullets and the prose sections can coexist on one SOW
    row.
    """

    kind: str
    content: Optional[str] = None
    order: Optional[int] = None
    removable: bool = True
    # Lightweight citations the section-regeneration service stamps so
    # the UI can show "synthesized from: executive_summary, milestones".
    # Strings only — kept compact and free of identifiers that could
    # leak across tenant boundaries on shared error rendering.
    source_citations: Optional[List[str]] = None


class SOWServiceLocation(BaseModel):
    """Field Services service / site location captured on the SOW.

    Required for a Field Services SOW to advance to ready_for_review.
    The shape is intentionally permissive so partner data with
    differently-named fields can map onto it; the validator only
    requires address line + city to be present.
    """

    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    notes: Optional[str] = None


class SOWContactRole(BaseModel):
    """A single contact attached to the SOW under a named role.

    Field Services SOWs must include at least the canonical required
    roles (primary, billing, service_location) before they can advance
    to ready_for_review. The model itself does not enforce; the
    transition route does. Roles are free strings so the deploy can
    add custom roles without a schema change.
    """

    role: str
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    contact_id: Optional[str] = None


class OpportunitySOW(BaseModel):
    """
    Statement of Work for ONE Line of Business within an opportunity.
    Each opportunity can have multiple SOWs (1 per LOB).
    Referral MRR LOBs do not get SOWs - they route to Procurement.
    """
    sow_id: str
    opportunity_id: str
    line_of_business: str  # field_services, professional_services, wholesale_mrr (NOT referral_mrr)

    # Assignment
    assigned_to: Optional[str] = None  # user_id of sales_ops member
    assigned_to_name: Optional[str] = None

    # Executive Summary
    executive_summary: Optional[str] = None

    # SMART Proposal
    smart_specific: Optional[str] = None
    smart_measurable: Optional[str] = None
    smart_achievable: Optional[str] = None
    smart_relevant: Optional[str] = None
    smart_time_bound: Optional[str] = None

    # Contract Configuration
    contract_type: Optional[str] = None  # boh, boa, fixed_fee, monthly, wholesale_mrr
    billing_terms: Optional[str] = None  # weekly, biweekly, monthly
    payment_terms: Optional[str] = None  # net_14, net_30, net_45, net_60
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None  # YYYY-MM-DD
    total_amount: Optional[float] = None  # For fixed_fee
    monthly_rate: Optional[float] = None  # For monthly contracts

    # Detailed SOW (Milestone-based)
    milestones: List[SOWMilestone] = Field(default_factory=list)

    # Billing Details
    billing_account_email: Optional[str] = None
    billing_account_phone: Optional[str] = None
    special_billing_instructions: Optional[str] = None

    # Field Services service / site location and contact roles.
    # The model carries both fields for every LOB so the shared
    # OpportunitySOW shape stays uniform; the readiness validator
    # in routes/solutions_design.py only requires them for
    # field_services SOWs before ready_for_review. See
    # docs/sales-ops/SHARED_SOW_BUILDER_SUBSTRATE.md.
    service_location: Optional[SOWServiceLocation] = None
    contact_roles: List[SOWContactRole] = Field(default_factory=list)

    # Professional Services prose sections. Carries the optional
    # canonical sections (Assumptions, Responsibilities, Out of
    # Scope, Risk Assessment, Solution Overview, In-Scope
    # Inventory, Conclusion) as free-text blocks. The shared
    # OpportunitySOW shape carries this field for every LOB but it
    # is rendered only for professional_services in
    # proposal_generation.py. Empty list is the safe default and
    # preserves existing FS / Wholesale MRR behavior.
    prose_sections: List[SOWProseSection] = Field(default_factory=list)

    # BOA-Specific: Authorized Users
    boa_authorized_users: List[SOWAuthorizedUser] = Field(default_factory=list)

    # Timeline
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None  # YYYY-MM-DD
    expected_completion_date: Optional[str] = None  # Required to move to "in_progress"

    # Resources (selected from available pool - links to OpportunityResourceAssignment)
    selected_resource_assignment_ids: List[str] = Field(default_factory=list)

    # Materials/Hardware (selected from catalog or created in Procurement)
    selected_line_item_ids: List[str] = Field(default_factory=list)

    # Status
    status: str = "not_started"  # not_started, in_progress, blocked, ready_for_review, complete, sent_to_client
    version: int = 1  # Track revisions

    # Google Doc
    google_doc_url: Optional[str] = None
    google_doc_id: Optional[str] = None

    # Blockers & Health
    has_critical_blockers: bool = False
    blockers: List[str] = Field(default_factory=list)  # List of blocker messages
    warning_count: int = 0  # Count of non-critical warnings

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = None
    last_edited_by: Optional[str] = None


class SOWCreate(BaseModel):
    """Create or update SOW (per LOB)."""
    assigned_to: Optional[str] = None
    executive_summary: Optional[str] = None
    smart_specific: Optional[str] = None
    smart_measurable: Optional[str] = None
    smart_achievable: Optional[str] = None
    smart_relevant: Optional[str] = None
    smart_time_bound: Optional[str] = None
    contract_type: Optional[str] = None
    billing_terms: Optional[str] = None
    payment_terms: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    expected_completion_date: Optional[str] = None
    total_amount: Optional[float] = None
    monthly_rate: Optional[float] = None
    milestones: List[SOWMilestone] = Field(default_factory=list)
    billing_account_email: Optional[str] = None
    billing_account_phone: Optional[str] = None
    special_billing_instructions: Optional[str] = None
    service_location: Optional[SOWServiceLocation] = None
    contact_roles: List[SOWContactRole] = Field(default_factory=list)
    prose_sections: List[SOWProseSection] = Field(default_factory=list)
    boa_authorized_users: List[SOWAuthorizedUser] = Field(default_factory=list)
    sow_product_items: List[SOWProductItem] = Field(default_factory=list)
    selected_resource_assignment_ids: List[str] = Field(default_factory=list)
    selected_line_item_ids: List[str] = Field(default_factory=list)
    status: Optional[str] = None


# ---- PROPOSAL MODELS ----

ProposalStatus = Literal[
    "draft",
    "ready_for_review",
    "sent",
    "client_sent",
    "accepted",
    "rejected",
]


class Proposal(BaseModel):
    """Deliverable proposal artifact built from one or more SOWs on an
    opportunity. The first MVP keeps the artifact as a Google Doc URL
    (matching the existing SOW finalize convention) and treats "send" as
    a mark-as-sent flip with optional recipient metadata — no in-product
    email send and no PDF generation yet. Future revisions of a proposal
    increment ``version``; ``status`` flows draft → ready_for_review →
    sent → accepted | rejected. Manual stage transitions only: the
    proposal lifecycle does not auto-drive the parent opportunity stage.
    """

    proposal_id: str
    opportunity_id: str
    sow_ids: List[str] = Field(default_factory=list)
    version: int = 1
    status: ProposalStatus = "draft"

    # Artifact. Google Doc URL preferred, with inline content as fallback
    # so the workflow does not block when Google Docs generation is not
    # configured. The doc id is captured separately so downstream
    # integrations can resolve the doc without re parsing the URL.
    # HttpUrl provides a defence in depth check on top of route level
    # validation so a malformed URL cannot land in storage and feed the
    # download path.
    google_doc_url: Optional[HttpUrl] = None
    google_doc_id: Optional[str] = None

    # Tags how the proposal is being viewed: ``google_doc`` when a doc
    # URL is attached, ``inline_content`` when the user is reading the
    # generated content directly. Set deterministically on write so the
    # frontend can branch render paths without reasoning about urls.
    artifact_type: Optional[Literal["google_doc", "inline_content"]] = None

    title: Optional[str] = None
    notes: Optional[str] = None

    # Generated content. ``generated_content`` is the long-form draft
    # the user can preview, edit, and ship as-is when no Google Doc is
    # available. ``generated_summary`` is a short paragraph for kanban /
    # email-preview rendering. Both are deterministic, no LLM in V1.
    generated_content: Optional[str] = None
    generated_summary: Optional[str] = None
    generated_at: Optional[datetime] = None
    generated_by: Optional[str] = None
    regenerated_at: Optional[datetime] = None
    regenerated_by: Optional[str] = None

    # Provenance for the generated content so a reviewer can tell when
    # the draft was last refreshed against SOW state and decide whether
    # to regenerate. ``generation_source_hash`` is a stable digest of the
    # SOW fields that fed the draft so a no-op regenerate can short-circuit.
    generation_source: Optional[str] = None  # e.g. "sow"
    generation_source_hash: Optional[str] = None

    # Internal handoff metadata. The /send route is the Sales Ops
    # handoff to Sales action. sent_to_email is the internal Sales
    # recipient captured for the audit trail. Nothing is dispatched.
    # The legacy field names sent_at, sent_by, sent_to_email are
    # preserved so existing callers do not break. The handoff
    # response shape exposes them under handoff_to_sales_at and
    # handoff_by as well for callers that want the policy-aligned
    # names.
    sent_at: Optional[datetime] = None
    sent_by: Optional[str] = None
    sent_to_email: Optional[str] = None

    # Client-facing send metadata. Stamped by the Sales owned
    # /mark-client-sent route. Records that Sales delivered the
    # proposal externally (PDF, signing link, personal email)
    # outside AEGISAXIOM. AEGISAXIOM does not perform the external send.
    client_sent_at: Optional[datetime] = None
    client_sent_by: Optional[str] = None
    client_delivery_method: Optional[str] = None
    client_delivery_note: Optional[str] = None
    external_proposal_url: Optional[HttpUrl] = None
    external_pdf_url: Optional[HttpUrl] = None

    client_response_at: Optional[datetime] = None
    client_response_note: Optional[str] = None

    # Standard metadata.
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = None
    last_edited_by: Optional[str] = None


class ProposalCreate(BaseModel):
    """POST /opportunities/{id}/proposal body.

    sow_ids is required so a draft proposal is always anchored to at
    least one SOW. The route validates that each SOW belongs to the
    opportunity AND carries a status that is finished enough to ship
    (``ready_for_review`` or ``complete``). google_doc_url is optional:
    Sales Ops typically creates the draft first and pastes the doc URL
    in via PATCH once the doc exists.
    """

    # sow_ids is required and must be non empty. The route layer also
    # checks each id against the opportunity and the SOW status gate so
    # this min_length constraint catches the obvious missing argument
    # case at the Pydantic boundary and surfaces it in the OpenAPI spec.
    sow_ids: List[str] = Field(..., min_length=1)
    title: Optional[str] = None
    notes: Optional[str] = None
    google_doc_url: Optional[HttpUrl] = None
    google_doc_id: Optional[str] = None


class ProposalUpdate(BaseModel):
    """PATCH /proposals/{id} body. Every field optional — only supplied
    keys are written. Status transitions are accepted here (e.g. flip
    draft → ready_for_review) but the dedicated /send route is the only
    way to set status=sent because it records sent_at/sent_by alongside.
    """

    sow_ids: Optional[List[str]] = None
    status: Optional[ProposalStatus] = None
    title: Optional[str] = None
    notes: Optional[str] = None
    google_doc_url: Optional[HttpUrl] = None
    google_doc_id: Optional[str] = None
    generated_content: Optional[str] = None
    generated_summary: Optional[str] = None
    client_response_note: Optional[str] = None


class ProposalSendRequest(BaseModel):
    """POST /proposals/{id}/send body.

    Internal handoff to Sales. ``sent_to_email`` is the internal
    Sales recipient captured for the audit trail. Nothing is
    dispatched by AEGISAXIOM. The corresponding client-facing send is a
    separate Sales-owned action at /proposals/{id}/mark-client-sent.
    """

    sent_to_email: Optional[EmailStr] = None
    notes: Optional[str] = None


class MarkClientSentRequest(BaseModel):
    """POST /proposals/{id}/mark-client-sent body.

    Sales records that the client-facing delivery already happened
    outside AEGISAXIOM. All fields optional. The route stamps client-sent
    metadata, writes an audit row, and advances the parent
    opportunity stage to proposal_sent when the current stage is
    proposal_development or scoping_complete. AEGISAXIOM does not perform
    the external send.

    client_delivery_method is a free text the Sales owner picks.
    Common values include external_pdf, external_signing_link,
    email_attachment. The backend does not normalize. The optional
    URL fields are tracking handles only; no external tool
    integration is implied.
    """

    client_delivery_method: Optional[str] = None
    client_delivery_note: Optional[str] = None
    external_proposal_url: Optional[HttpUrl] = Field(None, description="External signing platform URL, any hostname accepted")
    external_pdf_url: Optional[HttpUrl] = Field(None, description="External PDF URL, any hostname accepted")


# ---- CONTRACT TEMPLATE MODELS ----

class ContractTemplate(BaseModel):
    """
    Reusable contract configuration template (LEFT side - Customer Billing only).
    Admins create these, Sales Ops selects them when configuring resource types in SOW.
    Templates are global and can be used for any resource type.
    """
    template_id: str
    template_name: str  # e.g., "Standard BOH Net-30", "Fixed Fee Professional Services"
    description: Optional[str] = None

    # Contract Configuration (LEFT side - Customer Billing)
    contract_type: str  # boh, boa, fixed_fee, monthly

    # Hours & Rates (for BOH, BOA, Monthly)
    default_hours: Optional[float] = None  # Default hours for BOH
    default_sell_rate: Optional[float] = None  # Default sell rate per hour

    # Fixed Fee & Monthly
    default_fixed_amount: Optional[float] = None  # For fixed_fee
    default_monthly_rate: Optional[float] = None  # For monthly retainer

    # Billing Configuration
    billing_terms: str = "monthly"  # weekly, biweekly, monthly
    payment_terms: str = "net_30"  # net_7, net_14, net_30, net_45, net_60

    # BOH Specific
    convert_to_boa_at_exhaustion: bool = False  # Auto-convert to BOA when hours run out

    # Dates (optional defaults)
    default_contract_duration_days: Optional[int] = None  # e.g., 90 days, 180 days

    # Metadata
    created_by: str
    created_by_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True


class ContractTemplateCreate(BaseModel):
    """Create new contract template."""
    template_name: str
    description: Optional[str] = None
    contract_type: str
    default_hours: Optional[float] = None
    default_sell_rate: Optional[float] = None
    default_fixed_amount: Optional[float] = None
    default_monthly_rate: Optional[float] = None
    billing_terms: str = "monthly"
    payment_terms: str = "net_30"
    convert_to_boa_at_exhaustion: bool = False
    default_contract_duration_days: Optional[int] = None


class ContractTemplateUpdate(BaseModel):
    """Update existing contract template."""
    template_name: Optional[str] = None
    description: Optional[str] = None
    contract_type: Optional[str] = None
    default_hours: Optional[float] = None
    default_sell_rate: Optional[float] = None
    default_fixed_amount: Optional[float] = None
    default_monthly_rate: Optional[float] = None
    billing_terms: Optional[str] = None
    payment_terms: Optional[str] = None
    convert_to_boa_at_exhaustion: Optional[bool] = None
    default_contract_duration_days: Optional[int] = None
    is_active: Optional[bool] = None




# ==================== SALES OPERATIONS AUTOMATIONS ====================

class AutomationRuleCondition(BaseModel):
    """Single condition for an automation rule"""
    field: str  # e.g., "opportunity_value", "margin_percentage", "days_in_stage"
    operator: str  # e.g., "greater_than", "less_than", "equals", "between", "in", "not_in"
    value: Optional[Union[int, float, str, List[str]]] = None  # Threshold value or list for "in"/"not_in"
    value_max: Optional[Union[int, float]] = None  # For "between" operator
    time_unit: Optional[str] = None  # "hours", "days", "weeks", "months" (for time-based conditions)

class AutomationRuleAction(BaseModel):
    """Action to take when rule triggers"""
    action_type: str  # "email", "banner", "block_stage", "stage_transition", "update_field", "create_task"

    # Email action fields
    recipients: List[str] = Field(default_factory=list)  # Email addresses or roles
    email_template: Optional[str] = None

    # Banner action fields
    severity: Optional[str] = None  # "success", "warning", "critical"
    banner_message: Optional[str] = None

    # Stage blocking fields (NEW)
    blocked_stages: Optional[List[str]] = None  # Stages that cannot be transitioned TO
    block_reason: Optional[str] = None  # User-facing reason for block

    # Stage transition fields (NEW)
    target_stage: Optional[str] = None  # Auto-move to this stage
    transition_condition: Optional[str] = "auto"  # "auto", "manual_approval_required"

    # Field update fields (NEW)
    field_name: Optional[str] = None
    field_value: Optional[Any] = None

    # Task creation fields
    task_description: Optional[str] = None
    task_assignee: Optional[str] = None

class AutomationRule(BaseModel):
    """Sales Operations Automation Rule with Preconditions & Advanced Actions"""
    rule_id: str = Field(default_factory=lambda: f"rule_{uuid4().hex[:12]}")
    name: str  # e.g., "High-Value Opportunity Alert"
    description: Optional[str] = None

    # Functional area grouping
    functional_area: str = "solution_design"  # "solution_design", "procurement", "contractor_management"

    # Trigger configuration
    trigger_category: str  # "time", "margin", "size", "status", "activity", "procurement"
    trigger_type: str  # e.g., "days_in_stage", "margin_below", "value_above"

    # Preconditions (WHEN to evaluate this rule - NEW)
    # These must be TRUE before checking conditions
    # Example: [{"field": "has_resources", "operator": "greater_than", "value": 0}]
    preconditions: Optional[List[AutomationRuleCondition]] = Field(default_factory=list)

    # Conditions (WHAT to check - all must be true - AND logic)
    conditions: List[AutomationRuleCondition]

    # Actions to execute when triggered (supports multiple action types)
    actions: List[AutomationRuleAction]

    # Scope filters (optional - apply rule only to specific opps)
    lob_filter: Optional[List[str]] = None  # ["field_services", "professional_services"]
    stage_filter: Optional[List[str]] = None  # ["discovery", "proposal_development"]

    # Control
    enabled: bool = True
    priority: int = 1  # Higher number = higher priority (for alert display order)
    is_built_in: bool = False  # System rules cannot be deleted

    # Metadata
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class TriggeredAlert(BaseModel):
    """Active alert for an opportunity"""
    alert_id: str = Field(default_factory=lambda: f"alert_{uuid4().hex[:12]}")
    rule_id: str  # Reference to AutomationRule
    rule_name: str

    # Alert details
    opportunity_id: str
    opportunity_name: str
    severity: str  # "success", "warning", "critical"
    message: str

    # Trigger context
    triggered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trigger_values: dict = Field(default_factory=dict)  # e.g., {"days_in_stage": 15, "margin": 12.5}

    # Status
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    dismissed: bool = False

class AutomationRuleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    functional_area: str = "solution_design"
    trigger_category: str
    trigger_type: str
    preconditions: Optional[List[AutomationRuleCondition]] = Field(default_factory=list)  # NEW
    conditions: List[AutomationRuleCondition]
    actions: List[AutomationRuleAction]
    lob_filter: Optional[List[str]] = None
    stage_filter: Optional[List[str]] = None
    enabled: bool = True
    priority: int = 1

class AutomationRuleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    functional_area: Optional[str] = None
    preconditions: Optional[List[AutomationRuleCondition]] = None  # NEW
    conditions: Optional[List[AutomationRuleCondition]] = None
    actions: Optional[List[AutomationRuleAction]] = None
    lob_filter: Optional[List[str]] = None
    stage_filter: Optional[List[str]] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None


# ==================== POLICY ENGINE MODELS ====================

class PolicyCondition(BaseModel):
    field: str
    operator: str  # equals, not_equals, greater_than, less_than, between, contains, starts_with, before, after, is_true, is_false, in, not_in, is_empty, is_not_empty (between expects value as [low, high] array)
    value: Optional[Any] = None
    value_type: str = "string"  # string, number, time, date, boolean, list

class PolicyAction(BaseModel):
    type: str  # block, warn, require_field
    message: Optional[str] = None  # for block/warn
    field: Optional[str] = None  # for require_field

class PolicyTrigger(BaseModel):
    event: str  # on_submit, on_create, on_update, on_delete, on_status_change

class Policy(BaseModel):
    policy_id: str
    name: str
    description: Optional[str] = None
    module: str
    entity: str
    enabled: bool = True
    is_system: bool = False
    trigger: PolicyTrigger
    condition_logic: str = "AND"  # AND, OR, CUSTOM
    condition_pattern: Optional[str] = None  # e.g. "(1 AND 2) OR 3" when logic = CUSTOM
    conditions: List[PolicyCondition] = Field(default_factory=list)
    actions: List[PolicyAction] = Field(default_factory=list)
    priority: int = 1
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: Optional[datetime] = None

class PolicyCreate(BaseModel):
    name: str
    description: Optional[str] = None
    module: str
    entity: str
    enabled: bool = True
    trigger: PolicyTrigger
    condition_logic: str = "AND"
    condition_pattern: Optional[str] = None
    conditions: List[PolicyCondition] = Field(default_factory=list)
    actions: List[PolicyAction] = Field(default_factory=list)
    priority: int = 1

class PolicyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    module: Optional[str] = None
    entity: Optional[str] = None
    enabled: Optional[bool] = None
    trigger: Optional[PolicyTrigger] = None
    condition_logic: Optional[str] = None
    condition_pattern: Optional[str] = None
    conditions: Optional[List[PolicyCondition]] = None
    actions: Optional[List[PolicyAction]] = None
    priority: Optional[int] = None


# ==================== PROJECT MANAGEMENT MODELS ====================

class PMProject(BaseModel):
    """Project Management Project - Enhanced for IT professional services."""
    project_id: str
    name: str
    description: Optional[str] = None

    # Links
    contract_id: Optional[str] = None
    contract_name: Optional[str] = None
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    opportunity_id: Optional[str] = None  # Optional link to opportunity

    # Project Type & Budgeting
    project_type: str = "boh"  # boh, boa, fixed_fee, hybrid
    sale_price: float = 0.0  # Deal amount from contract/opportunity
    margin_goal_percent: float = 35.0  # Default 35% margin
    cost_budget: float = 0.0  # Calculated: sale_price * (1 - margin_goal_percent/100)

    # Hours Tracking
    purchased_hours: Optional[float] = None  # For BOH projects
    estimated_hours: float = 0.0  # Sum of all task estimated hours
    consumed_hours: float = 0.0  # Sum of approved time entries
    remaining_hours: Optional[float] = None  # purchased_hours - consumed_hours (BOH only)

    # Health Status (calculated)
    health_status: str = "green"  # green, yellow, red
    health_last_calculated: Optional[datetime] = None

    # People
    project_manager_id: Optional[str] = None
    project_manager_name: Optional[str] = None
    assigned_engineers: List[str] = Field(default_factory=list)  # user_ids

    # Project Permissions (project-level access control)
    # Format: {"user_id": ["tasks.create", "tasks.edit", "time.approve"]}
    project_permissions: Dict[str, List[str]] = Field(default_factory=dict)

    # Timeline
    start_date: Optional[str] = None  # YYYY-MM-DD
    target_completion_date: Optional[str] = None
    actual_completion_date: Optional[str] = None

    # Status
    status: str = "planning"  # planning, active, on_hold, completed, cancelled

    # Documents
    documents: List[Dict[str, Any]] = Field(default_factory=list)
    # Format: [{"doc_id": "...", "filename": "...", "url": "...", "uploaded_by": "...", "uploaded_at": "..."}]

    # Metadata
    is_test_data: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = None


class PMProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    contract_id: Optional[str] = None
    account_id: Optional[str] = None
    project_type: str = "boh"
    sale_price: float = 0.0
    margin_goal_percent: float = 35.0
    purchased_hours: Optional[float] = None
    project_manager_id: Optional[str] = None
    assigned_engineers: List[str] = Field(default_factory=list)
    start_date: Optional[str] = None
    target_completion_date: Optional[str] = None
    is_test_data: bool = False


class PMProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    project_type: Optional[str] = None
    sale_price: Optional[float] = None
    margin_goal_percent: Optional[float] = None
    purchased_hours: Optional[float] = None
    project_manager_id: Optional[str] = None
    assigned_engineers: Optional[List[str]] = None
    start_date: Optional[str] = None
    target_completion_date: Optional[str] = None
    actual_completion_date: Optional[str] = None
    status: Optional[str] = None


class PMMilestone(BaseModel):
    """Project milestone for grouping tasks."""
    milestone_id: str
    project_id: str
    name: str
    description: Optional[str] = None
    due_date: Optional[str] = None  # YYYY-MM-DD
    order: int = 0  # Display order
    status: str = "not_started"  # not_started, in_progress, completed
    estimated_hours: float = 0.0  # Sum of task hours
    actual_hours: float = 0.0  # Sum of task actuals
    completion_percentage: float = 0.0  # Calculated
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PMMilestoneCreate(BaseModel):
    name: str
    description: Optional[str] = None
    due_date: Optional[str] = None
    order: int = 0


class PMMilestoneUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[str] = None
    order: Optional[int] = None
    status: Optional[str] = None


class PMTask(BaseModel):
    """Project task with time tracking and sub-tasks."""
    task_id: str
    project_id: str
    milestone_id: Optional[str] = None
    parent_task_id: Optional[str] = None  # For sub-tasks

    # Task Details
    name: str
    description: Optional[str] = None
    estimated_hours: float = 0.0  # Required field per user requirements
    actual_hours: float = 0.0  # Sum of time entries

    # Assignment & Status
    assigned_to: Optional[str] = None  # user_id
    assigned_to_name: Optional[str] = None
    status: str = "not_started"  # not_started, in_progress, blocked, completed, cancelled
    priority: str = "medium"  # low, medium, high, critical

    # Dates
    due_date: Optional[str] = None  # YYYY-MM-DD
    start_date: Optional[str] = None
    completed_at: Optional[datetime] = None

    # Hierarchy
    order: int = 0  # Display order within milestone/parent
    has_subtasks: bool = False
    subtask_count: int = 0

    # Import metadata
    imported_from: Optional[str] = None  # "spreadsheet", "contract", "manual"
    import_row_number: Optional[int] = None  # For tracking source

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = None


class PMTaskCreate(BaseModel):
    name: str
    description: Optional[str] = None
    milestone_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    estimated_hours: float = 0.0
    actual_hours: float = 0.0
    assigned_to: Optional[str] = None
    status: str = "not_started"
    priority: str = "medium"
    due_date: Optional[str] = None
    order: int = 0


class PMTaskUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    milestone_id: Optional[str] = None
    estimated_hours: Optional[float] = None
    actual_hours: Optional[float] = None
    assigned_to: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[str] = None
    start_date: Optional[str] = None
    completed_at: Optional[datetime] = None
    order: Optional[int] = None


class PMTaskImport(BaseModel):
    """Import tasks from spreadsheet or Google Sheets."""
    source_type: str  # "google_sheets", "excel", "csv"
    source_url: Optional[str] = None  # For Google Sheets
    milestone_id: Optional[str] = None  # Assign all tasks to this milestone


class PMProjectPermission(BaseModel):
    """Project-level permission assignment."""
    user_id: str
    permissions: List[str]  # ["tasks.create", "tasks.edit", "time.approve"]



class DashboardWidget(BaseModel):
    """Widget configuration for dashboard."""
    widget_id: str
    widget_type: str
    title: str
    config: dict = Field(default_factory=dict)
    layout: dict = Field(default_factory=lambda: {"x": 0, "y": 0, "w": 4, "h": 3})

class Dashboard(BaseModel):
    """User's custom dashboard configuration."""
    dashboard_id: str
    user_id: str
    name: str  # "Executive View", "My Projects", etc.
    is_default: bool = False  # Default dashboard for this user
    widgets: List[DashboardWidget] = Field(default_factory=list)
    order: int = 0  # Tab order
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DashboardCreate(BaseModel):
    name: str
    is_default: bool = False


class DashboardUpdate(BaseModel):
    name: Optional[str] = None
    is_default: Optional[bool] = None
    widgets: Optional[List[DashboardWidget]] = None


# ==================== PROJECT HEALTH AUTOMATION RULES ====================

class ProjectHealthRule(BaseModel):
    """Configurable rules for project health status and alerts."""
    rule_id: str
    name: str
    description: Optional[str] = None
    rule_type: str  # "health_status", "task_alert", "capacity_alert", "grievance_alert"

    # Conditions
    condition_type: str  # "budget_consumed_percent", "overdue_tasks", "over_capacity", "open_grievance"
    threshold_value: Optional[float] = None  # For percentage-based rules
    threshold_days: Optional[int] = None  # For time-based rules (e.g., "3 days until over capacity")

    # Outcome
    severity: str  # "critical", "warning", "info"
    badge_text: Optional[str] = None  # Display on project card
    badge_color: Optional[str] = None  # "red", "yellow", "orange", "blue"

    # Applies to
    applies_to: str = "all_projects"  # "all_projects", "specific_types", "specific_projects"
    project_type_filter: List[str] = Field(default_factory=list)  # ["boh", "boa"] etc
    project_id_filter: List[str] = Field(default_factory=list)

    # Status
    enabled: bool = True
    order: int = 0  # Evaluation order

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = None


class ProjectHealthRuleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    rule_type: str
    condition_type: str
    threshold_value: Optional[float] = None
    threshold_days: Optional[int] = None
    severity: str
    badge_text: Optional[str] = None
    badge_color: Optional[str] = None
    applies_to: str = "all_projects"
    project_type_filter: List[str] = Field(default_factory=list)
    enabled: bool = True


class ProjectHealthRuleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    threshold_value: Optional[float] = None
    threshold_days: Optional[int] = None
    severity: Optional[str] = None
    badge_text: Optional[str] = None
    badge_color: Optional[str] = None
    enabled: Optional[bool] = None


# ==================== CAPACITY AUTOMATION RULES ====================

class CapacityHealthRule(BaseModel):
    """Capacity health rules for calendar day coloring."""
    rule_id: str
    name: str
    description: Optional[str] = None

    # Thresholds (hours per day)
    green_threshold: float = 6.0  # < 6 hours = green (under capacity)
    yellow_threshold: float = 8.0  # 6-8 hours = yellow (at capacity)
    # > 8 hours = red (over capacity)

    # Applies to
    applies_to_roles: List[str] = Field(
        default_factory=lambda: ["contractor", "admin"])  # Which roles this rule applies to

    # Status
    enabled: bool = True

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = None


class CapacityHealthRuleUpdate(BaseModel):
    green_threshold: Optional[float] = None
    yellow_threshold: Optional[float] = None
    enabled: Optional[bool] = None


class TimeLockingRule(BaseModel):
    """Configurable time entry locking rules."""
    rule_id: str
    name: str = "Weekly Timesheet Lock"
    description: Optional[str] = None

    # Lock Configuration
    lock_after_days: int = 8  # Days after week_start (8 = next Monday)
    lock_time_hour: int = 22  # Hour in UTC (22 = 5pm ET in EST, 6pm ET in EDT)
    lock_time_minute: int = 0

    # Exemptions
    exempt_roles: List[str] = Field(default_factory=lambda: ["admin"])  # Roles that can bypass lock
    exempt_permissions: List[str] = Field(default_factory=lambda: ["time.entry.admin"])

    # Status
    enabled: bool = True

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = None


class TimeLockingRuleUpdate(BaseModel):
    lock_after_days: Optional[int] = None
    lock_time_hour: Optional[int] = None
    lock_time_minute: Optional[int] = None
    exempt_roles: Optional[List[str]] = None
    exempt_permissions: Optional[List[str]] = None
    enabled: Optional[bool] = None


# ============================================
# PM CUSTOM DASHBOARDS
# ============================================

class PMDashboard(BaseModel):
    """Custom dashboard configuration."""
    dashboard_id: str
    name: str
    description: Optional[str] = None
    is_default: bool = False
    order: int = 0
    widgets: List[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = None


class PMDashboardCreate(BaseModel):
    name: str
    description: Optional[str] = None
    is_default: bool = False
    order: int = 0


class PMDashboardUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    widgets: Optional[List[dict]] = None
    order: Optional[int] = None





# ---- Contact Models ----

class ContactCreate(BaseModel):
    account_id: str
    first_name: str
    last_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile_phone: Optional[str] = None
    title: Optional[str] = None
    department: Optional[str] = None
    linkedin_url: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    is_primary: bool = False
    notes: Optional[str] = None
    is_test_data: bool = False
    assigned_sales_rep_id: Optional[str] = None
    assigned_sales_rep_name: Optional[str] = None



class ContactUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile_phone: Optional[str] = None
    title: Optional[str] = None
    department: Optional[str] = None
    linkedin_url: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    is_primary: Optional[bool] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None
    is_test_data: Optional[bool] = None
    assigned_sales_rep_id: Optional[str] = None
    assigned_sales_rep_name: Optional[str] = None


# ---- Deal Models ----




# ---- Deal Models ----

class Deal(BaseModel):
    deal_id: str
    name: str
    account_id: str
    account_name: Optional[str] = None
    stage: str = "Prospecting"
    amount: float = 0.0
    close_date: Optional[str] = None
    deal_owner_id: str
    deal_owner_name: Optional[str] = None
    assigned_sales_rep_id: Optional[str] = None
    assigned_sales_rep_name: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    probability: int = 0
    description: Optional[str] = None
    zoho_deal_id: Optional[str] = None
    is_test_data: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))




# ---- SOW Milestone Template Models ----

class SOWMilestoneTemplateBullet(BaseModel):
    bullet_text: str
    estimated_hours: Optional[float] = None
    deliverable: Optional[str] = None



class SOWMilestoneTemplateMilestone(BaseModel):
    milestone_name: str
    bullets: List[SOWMilestoneTemplateBullet] = []



class SOWMilestoneTemplate(BaseModel):
    template_id: str
    template_name: str
    description: Optional[str] = None
    line_of_business: str  # 'professional_services' | 'field_services' | 'wholesale_mrr'
    milestones: List[SOWMilestoneTemplateMilestone] = []
    is_active: bool = True
    created_by: Optional[str] = None
    created_by_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None



class SOWMilestoneTemplateCreate(BaseModel):
    template_name: str
    description: Optional[str] = None
    line_of_business: str
    milestones: List[SOWMilestoneTemplateMilestone] = []



class SOWMilestoneTemplateUpdate(BaseModel):
    template_name: Optional[str] = None
    description: Optional[str] = None
    line_of_business: Optional[str] = None
    milestones: Optional[List[SOWMilestoneTemplateMilestone]] = None
    is_active: Optional[bool] = None


# ==================== SALES OPERATIONS AUTOMATIONS ====================




# ---- AI Employee Models ----

class AIEmployeeCreate(BaseModel):
    """Schema for creating/importing a new AI employee."""
    name: str
    role: str
    type: str  # "conversational" or "n8n"
    description: Optional[str] = None
    avatar: Optional[str] = "🤖"
    initial_status: Optional[str] = "active"  # active, paused, training

    # Conversational AI fields
    embedded_url: Optional[str] = None
    embed_code: Optional[str] = None

    # N8N Workflow fields
    n8n_instance_url: Optional[str] = None
    n8n_api_key: Optional[str] = None
    n8n_workflow_id: Optional[str] = None

    # Metrics
    metric1_label: Optional[str] = None
    metric2_label: Optional[str] = None
    metric3_label: Optional[str] = None
    metrics_source: Optional[str] = "manual"  # "manual" or "n8n"

    # Specialties
    specialties: Optional[List[str]] = []

    # Contractor
    contractor_name: Optional[str] = None
    agreement_id: Optional[str] = None

    # Access control (optional — defaults to admin-only if not provided)
    allowed_roles: Optional[List[str]] = None
    allowed_user_ids: Optional[List[str]] = None

    # Native conversational AI fields (all optional, only meaningful when type=="conversational")
    system_prompt: Optional[str] = None
    router_description: Optional[str] = None
    model_provider: Optional[str] = "google"
    model_name: Optional[str] = "gemini-3-flash-preview"
    knowledge_base_filter: Optional[dict] = None
    tools: Optional[List[str]] = None
    state_schema: Optional[dict] = None

    # Phase 1B.2 — linkage to catalog definition + tenant entitlement.
    #
    # These three fields connect an instance row back to:
    #   * definition_id   → ai_employee_definitions.definition_id (blueprint)
    #   * tenant_id       → tenants.tenant_id (owning tenant)
    #   * assignment_id   → tenant_app_assignments.assignment_id (entitlement)
    #
    # All Optional[str]. Two transitional states they cover:
    #   1. Pre-1B.4 migration: every existing instance row predates these
    #      fields and has none of them. The 1B.4 backfill stamps them.
    #   2. Direct-import flow (pre-1B.5): callers that still POST to
    #      /ai-employees without going through the catalog can omit them;
    #      the row gets persisted unlinked and is backfilled the same way.
    #
    # Once 1B.5 wires the catalog-driven provisioning API, all three are
    # always set at create time. After 1B.4 + 1B.5, no row should have
    # any of these as None — but we keep them Optional in the model so
    # the rollout sequence can be staged safely.
    definition_id: Optional[str] = None
    tenant_id: Optional[str] = None
    assignment_id: Optional[str] = None

    @field_validator('type')
    @classmethod
    def type_must_be_valid(cls, v):
        if v not in ['conversational', 'n8n']:
            raise ValueError('Type must be "conversational" or "n8n"')
        return v

    @field_validator('initial_status')
    @classmethod
    def status_must_be_valid(cls, v):
        if v and v not in ['active', 'paused', 'training', 'offline']:
            raise ValueError('Status must be active, paused, training, or offline')
        return v



class AIEmployeeUpdate(BaseModel):
    """Schema for updating an AI employee."""
    name: Optional[str] = None
    role: Optional[str] = None
    description: Optional[str] = None
    avatar: Optional[str] = None
    status: Optional[str] = None
    embedded_url: Optional[str] = None
    embed_code: Optional[str] = None
    n8n_instance_url: Optional[str] = None
    n8n_api_key: Optional[str] = None
    n8n_workflow_id: Optional[str] = None
    metric1_label: Optional[str] = None
    metric2_label: Optional[str] = None
    metric3_label: Optional[str] = None
    specialties: Optional[List[str]] = None
    contractor_name: Optional[str] = None
    agreement_id: Optional[str] = None
    # Conversational AI fields
    system_prompt: Optional[str] = None
    router_description: Optional[str] = None
    model_provider: Optional[str] = None
    model_name: Optional[str] = None
    knowledge_base_filter: Optional[dict] = None
    tools: Optional[List[str]] = None
    state_schema: Optional[dict] = None



class AIEmployeeStatusUpdate(BaseModel):
    """Schema for updating AI employee status."""
    status: str

    @field_validator('status')
    @classmethod
    def status_must_be_valid(cls, v):
        if v not in ['active', 'paused', 'training', 'offline']:
            raise ValueError('Status must be active, paused, training, or offline')
        return v



class AIEmployeeWebhookPayload(BaseModel):
    """Expected payload from N8N webhook (flexible, all fields optional)."""
    workflow_id: Optional[str] = None
    status: Optional[str] = "success"
    label: Optional[str] = None
    labelId: Optional[str] = None
    subject: Optional[str] = None
    execution_time_ms: Optional[int] = 0
    timestamp: Optional[str] = None
    webhook_token: Optional[str] = None




# ---- Chat Artifact Models ----

class ChatArtifactSubmit(BaseModel):
    """Schema for submitting quiz responses."""
    session_id: str
    responses: dict




# ---- Certification Models ----

class CertificationOut(BaseModel):
    """Response model for certification records."""
    certification_id: str
    user_id: str
    user_name: str
    certification_type: str
    issued_at: str
    issued_by: Optional[str] = None
    module_scores: Optional[dict] = None
    final_test_score: Optional[float] = None




# ---- Vega Session Models ----

class CreateSessionBody(BaseModel):
    """Optional body for creating a chat session with agent pinning."""
    agent: Optional[str] = None



# ---- MMI Models ----

class MMIDimension(BaseModel):
    value: Optional[int] = None  # 1-5
    confidence: Optional[str] = None  # high | medium | low
    reasoning: Optional[str] = None




class MMIHypothesis(BaseModel):
    tss: Optional[MMIDimension] = None
    ie: Optional[MMIDimension] = None
    time: Optional[MMIDimension] = None
    cost: Optional[MMIDimension] = None
    quality: Optional[MMIDimension] = None
    satisfaction: Optional[MMIDimension] = None
    score: Optional[float] = None
    needs_client_input: List[str] = Field(default_factory=list)


class WorkflowActor(BaseModel):
    id: str
    name: str
    type: str  # human | ai | external_system | output




class WorkflowStep(BaseModel):
    id: str
    name: str
    actor_id: str
    task_type: str = "user"  # user | service | business_rule | manual
    description: Optional[str] = None




class WorkflowDecisionPoint(BaseModel):
    id: str
    after_step_id: str
    type: str = "exclusive"  # exclusive | parallel | inclusive
    name: Optional[str] = None
    branches: List[Dict[str, str]] = Field(default_factory=list)




class WorkflowExceptionPath(BaseModel):
    id: str
    trigger: str
    from_step_id: str
    leads_to_step_id: Optional[str] = None




class WorkflowTrigger(BaseModel):
    """Trigger configuration for a workflow rule."""
    type: str  # record_action, field_change, date_field, scheduled
    action: Optional[str] = None  # created, updated, deleted, created_or_updated
    watch_field: Optional[str] = None
    date_field: Optional[str] = None
    offset_direction: Optional[str] = None  # before, after, on
    offset_value: Optional[int] = None
    offset_unit: Optional[str] = None  # days, hours, minutes
    repeat: Optional[str] = None  # once, daily, monthly, yearly
    cron_expression: Optional[str] = None
    timezone: Optional[str] = None




class WorkflowMatrixEntry(BaseModel):
    workflow_id: str
    workflow_name: str
    description: Optional[str] = None
    source_apps: List[str] = Field(default_factory=list)
    data_state: Optional[str] = None  # blind | siloed | visible
    data_state_justification: Optional[str] = None
    manual_hours_per_week: Optional[float] = None
    stakeholders: List[str] = Field(default_factory=list)
    actors: List[WorkflowActor] = Field(default_factory=list)
    trigger: Optional[WorkflowTrigger] = None
    steps: List[WorkflowStep] = Field(default_factory=list)
    decision_points: List[WorkflowDecisionPoint] = Field(default_factory=list)
    exception_paths: List[WorkflowExceptionPath] = Field(default_factory=list)
    mmi_hypothesis: Optional[MMIHypothesis] = None
    bpmn_xml: Optional[str] = None
    bpmn_id: Optional[str] = None
    bpmn_status: str = "not_generated"  # not_generated | generating | generated | failed
    bpmn_generated_at: Optional[datetime] = None
    estimated_workflow_category: Optional[str] = None  # bottleneck | data_heavy | repeated | decision_intensive | compliance
    aegisaxiom_module_dependencies: List[str] = Field(default_factory=list)
    transformation_logic: Optional[str] = None






# ==================== WORKFLOW RULES ENGINE MODELS ====================



class VegaSessionCall(BaseModel):
    call_number: int
    upload_ids: List[str] = Field(default_factory=list)
    ingested_at: Optional[datetime] = None
    analysis: Optional[Dict[str, Any]] = None
    user_approved: bool = False
    user_edits: Optional[Dict[str, Any]] = None



class VegaSessionOrionHandoff(BaseModel):
    handed_off_at: Optional[datetime] = None
    orion_session_state: Optional[Dict[str, Any]] = None
    handoff_count: int = 0



class EmergentAppDesign(BaseModel):
    """Internal: Emergent App Design Document (EADD).

    User-facing strings ALWAYS render this as 'App Design' / 'App Designs'.
    Internal naming preserves the Emergent reference because the starter_prompt
    is shaped specifically for paste-into-Emergent.
    """
    app_id: str
    app_name: str
    app_pitch_summary: Optional[str] = None
    primary_persona_hint: Optional[str] = None
    linked_workflow_ids: List[str] = Field(default_factory=list)
    starter_prompt: Optional[str] = None  # Paste-into-Emergent prompt
    core_flows: List[str] = Field(default_factory=list)
    data_model: List[Dict[str, Any]] = Field(default_factory=list)
    integrations: List[Dict[str, Any]] = Field(default_factory=list)
    out_of_scope: List[str] = Field(default_factory=list)
    visual_style: Optional[str] = None
    status: str = "success"  # success | failed
    failure_reason: Optional[str] = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VegaSession(BaseModel):
    session_id: str
    user_id: str
    chat_session_id: str
    opportunity_name: Optional[str] = None
    client_company: Optional[str] = None
    current_call_number: int = 0
    intake_complete: bool = False
    calls: List[VegaSessionCall] = Field(default_factory=list)
    ai_employee_matrix: List[WorkflowMatrixEntry] = Field(default_factory=list)
    gap_questions: List[Dict[str, Any]] = Field(default_factory=list)
    mmi_scores: List[Dict[str, Any]] = Field(default_factory=list)
    emergent_app_design_documents: List[EmergentAppDesign] = Field(default_factory=list)
    final_report_artifact_id: Optional[str] = None
    orion_handoff: VegaSessionOrionHandoff = Field(default_factory=VegaSessionOrionHandoff)
    status: str = "in_progress"  # in_progress | ready_for_sow | handed_off_to_orion | sow_in_revision | complete
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_name: Optional[str] = None
    context_summary: Optional[str] = None  # LLM-generated compressed summary for multi-call resume
    tags: List[str] = Field(default_factory=list)
    discovery_mode: Optional[str] = "three_call"  # three_call | single_call | freeflow



class VegaEvent(BaseModel):
    """KPI tracking event for Vega analytics."""
    event_id: str
    session_id: str
    user_id: str
    event_type: str  # transcript_analyzed | matrix_built | session_finalized | bpmn_generated | mmi_scored | design_doc_created | followup_agenda_built | export_triggered
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))



# ---- Workflow Models ----

class WorkflowAction(BaseModel):
    """A single workflow action (instant or scheduled)."""
    type: str  # update_field, create_record, send_notification, send_email, trigger_webhook, trigger_workflow, delete_record
    delay_value: Optional[int] = None
    delay_unit: Optional[str] = None  # minutes, hours, days
    module: Optional[str] = None
    field: Optional[str] = None
    value: Optional[Any] = None
    fields: Optional[Dict[str, Any]] = None
    target: Optional[str] = None  # role, user, record_field
    target_value: Optional[str] = None
    title: Optional[str] = None
    message: Optional[str] = None
    to: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    url: Optional[str] = None
    method: Optional[str] = "POST"
    headers: Optional[Dict[str, str]] = None
    webhook_body: Optional[Any] = None
    workflow_id: Optional[str] = None
    filter: Optional[Dict[str, Any]] = None



class WorkflowRule(BaseModel):
    """Complete workflow automation rule."""
    rule_id: str
    name: str
    description: Optional[str] = None
    module: str
    enabled: bool = True
    trigger: WorkflowTrigger
    condition_logic: str = "AND"
    condition_pattern: Optional[str] = None
    conditions: List[PolicyCondition] = Field(default_factory=list)
    instant_actions: List[WorkflowAction] = Field(default_factory=list)
    scheduled_actions: List[WorkflowAction] = Field(default_factory=list)
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    run_count: int = 0



class WorkflowRuleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    module: str
    enabled: bool = True
    trigger: WorkflowTrigger
    condition_logic: str = "AND"
    condition_pattern: Optional[str] = None
    conditions: List[PolicyCondition] = Field(default_factory=list)
    instant_actions: List[WorkflowAction] = Field(default_factory=list)
    scheduled_actions: List[WorkflowAction] = Field(default_factory=list)



class WorkflowRuleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    module: Optional[str] = None
    enabled: Optional[bool] = None
    trigger: Optional[WorkflowTrigger] = None
    condition_logic: Optional[str] = None
    condition_pattern: Optional[str] = None
    conditions: Optional[List[PolicyCondition]] = None
    instant_actions: Optional[List[WorkflowAction]] = None
    scheduled_actions: Optional[List[WorkflowAction]] = None



class WorkflowExecution(BaseModel):
    """Log entry for a workflow rule execution."""
    execution_id: str
    rule_id: str
    rule_name: str
    module: str
    trigger_type: str
    record_id: Optional[str] = None
    status: str  # success, failed, skipped, partial, dry_run
    actions_executed: List[Dict] = Field(default_factory=list)
    event_summary: Optional[Dict] = None
    chain_depth: int = 0
    dry_run: bool = False
    triggered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    execution_time_ms: int = 0
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))



# ── Chat Uploads (Vega transcript ingestion) ──

class ChatUploadLinkedTo(BaseModel):
    vega_session_id: Optional[str] = None
    call_number: Optional[int] = None


class ChatUpload(BaseModel):
    upload_id: str
    user_id: str
    session_id: str
    filename: str
    file_size_bytes: int
    content_type: str
    extracted_text: str
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    linked_to: Optional[ChatUploadLinkedTo] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =====================================================================
# TENANT MODELS — multi-tenancy foundation (Phase 1A)
# =====================================================================

class Tenant(BaseModel):
    """A tenant in the AEGISAXIOM hierarchy.

    Hierarchy:
      * `root` — the SimpliMeta organisation. Exactly one. parent_tenant_id is None.
      * `partner` — a reseller / channel partner. parent_tenant_id points to root.
      * `account` — a customer tenant. parent_tenant_id is None (direct customer
        of SimpliMeta) or points to a partner.
    """
    tenant_id: str
    name: str
    slug: str
    tenant_type: Literal["root", "partner", "account"]
    parent_tenant_id: Optional[str] = None
    status: Literal["active", "suspended", "archived"] = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = None


class TenantCreate(BaseModel):
    name: str
    slug: str
    tenant_type: Literal["partner", "account"]  # root not creatable via API
    parent_tenant_id: Optional[str] = None


class TenantUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    tenant_type: Optional[Literal["partner", "account"]] = None
    parent_tenant_id: Optional[str] = None
    status: Optional[Literal["active", "suspended", "archived"]] = None


# =====================================================================
# AI EMPLOYEE DEFINITIONS + TENANT APP ASSIGNMENTS — Phase 1B.1
# =====================================================================
#
# Three-collection model for AI Employees (locked at Ryan/Samson
# multi-tenancy meeting, 8 May 2026):
#
#   ai_employee_definitions   →  blueprint catalog (tenant-agnostic)
#   tenant_app_assignments    →  entitlements (which tenant gets which
#                                 blueprint, granted by whom)
#   ai_employees              →  deployed instances (tenant-scoped runtime;
#                                 refactored in commit 1B.2 to link back to
#                                 definition_id + assignment_id)
#
# Locked decisions reflected here:
#   1. Definition holds defaults (system_prompt, model_provider, etc.);
#      the instance can override per-tenant. Fields here are named
#      `default_*` to make the override seam explicit.
#   2. Native employees provision as a "tenant has X enabled" flag — no
#      additional setup beyond the assignment + instance row. The
#      `provisioning_mode` field on the definition drives this.
#
# 1B.1 scope is plumbing only: models + role + indexes. No routes touch
# these collections yet (that lands in 1B.5).


class AIEmployeeDefinition(BaseModel):
    """Tenant-agnostic AI Employee blueprint in the global catalog.

    All tenants browse the same set of published definitions. Deploying
    one to a tenant requires a `TenantAppAssignment` (entitlement record)
    plus an `ai_employees` row (the instance). Defaults declared here can
    be overridden per-tenant on the instance.
    """
    definition_id: str  # def_<hex12>
    name: str
    role: str
    type: Literal["conversational", "n8n"]
    description: Optional[str] = None
    default_avatar: str = "🤖"

    # Conversational defaults (overridable on instance for per-tenant
    # tuning, e.g. tenant-specific system prompt with their branding)
    default_system_prompt: Optional[str] = None
    default_router_description: Optional[str] = None
    default_model_provider: Optional[str] = "google"
    default_model_name: Optional[str] = "gemini-2.5-flash"
    default_tools: List[str] = Field(default_factory=list)
    default_knowledge_base_filter: Optional[dict] = None
    default_state_schema: Optional[dict] = None

    # N8N defaults — the template workflow that gets cloned per-tenant.
    # Per-tenant N8N workflow_id and api_key live on the instance, not here.
    default_n8n_template_url: Optional[str] = None

    # Catalog metadata
    default_capabilities: List[str] = Field(default_factory=list)
    # Metric labels keyed by slot ("metric1", "metric2", "metric3") so the
    # dashboard card can render them without a definition lookup per tile.
    default_metric_labels: Dict[str, str] = Field(default_factory=dict)
    default_metrics_source: Literal["manual", "n8n"] = "manual"

    # Provisioning mode (locked decision 2)
    #   native     — instance row + assignment row is all that's needed;
    #                runtime registration happens via marketplace.py
    #   n8n_clone  — also clone the N8N workflow per-tenant on provision
    #   external   — assignment grants access to an externally-hosted
    #                conversational employee (embedded_url on instance)
    provisioning_mode: Literal["native", "n8n_clone", "external"] = "native"

    # Catalog visibility / lifecycle
    is_active: bool = True       # archived definitions stay queryable but hide from catalog
    is_published: bool = True    # unpublished = SimpliMeta drafts not yet shown to other tenants

    # Authorship (always SimpliMeta root in 1B; partner-authored definitions
    # are a 1C+ concern but the field exists so we don't need a migration)
    created_by_user_id: Optional[str] = None
    created_by_tenant_id: Optional[str] = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AIEmployeeDefinitionCreate(BaseModel):
    """Schema for creating a new AI Employee definition in the catalog.

    Only SimpliMeta root admins can create definitions in 1B
    (enforced at the route layer in 1B.5).
    """
    name: str
    role: str
    type: Literal["conversational", "n8n"]
    description: Optional[str] = None
    default_avatar: Optional[str] = "🤖"
    default_system_prompt: Optional[str] = None
    default_router_description: Optional[str] = None
    default_model_provider: Optional[str] = "google"
    default_model_name: Optional[str] = "gemini-2.5-flash"
    default_tools: Optional[List[str]] = None
    default_knowledge_base_filter: Optional[dict] = None
    default_state_schema: Optional[dict] = None
    default_n8n_template_url: Optional[str] = None
    default_capabilities: Optional[List[str]] = None
    default_metric_labels: Optional[Dict[str, str]] = None
    default_metrics_source: Optional[Literal["manual", "n8n"]] = "manual"
    provisioning_mode: Optional[Literal["native", "n8n_clone", "external"]] = "native"
    is_published: Optional[bool] = True


class AIEmployeeDefinitionUpdate(BaseModel):
    """Schema for updating an AI Employee definition.

    Fields are all optional — PATCH semantics. Changing a default does NOT
    retroactively change instances that have already been deployed; the
    override mechanism on instances preserves the original behaviour by
    design. Updating `is_published=False` hides the definition from the
    catalog but does not affect existing instances.
    """
    name: Optional[str] = None
    role: Optional[str] = None
    description: Optional[str] = None
    default_avatar: Optional[str] = None
    default_system_prompt: Optional[str] = None
    default_router_description: Optional[str] = None
    default_model_provider: Optional[str] = None
    default_model_name: Optional[str] = None
    default_tools: Optional[List[str]] = None
    default_knowledge_base_filter: Optional[dict] = None
    default_state_schema: Optional[dict] = None
    default_n8n_template_url: Optional[str] = None
    default_capabilities: Optional[List[str]] = None
    default_metric_labels: Optional[Dict[str, str]] = None
    default_metrics_source: Optional[Literal["manual", "n8n"]] = None
    provisioning_mode: Optional[Literal["native", "n8n_clone", "external"]] = None
    is_active: Optional[bool] = None
    is_published: Optional[bool] = None


class TenantAppAssignment(BaseModel):
    """Entitlement record granting a tenant the right to deploy a given
    AI Employee definition.

    Granted by a SimpliMeta admin (or, post-delegation in 1B.5, by a
    `tenant_admin` of an upstream tenant). One assignment per
    (tenant_id, definition_id) pair — uniqueness enforced at the index
    layer in server.py.

    The actual deployed instance (`ai_employees` row) carries the
    `assignment_id` so revoking an assignment can cascade-disable
    instances without dropping their data.
    """
    assignment_id: str  # asn_<hex12>
    definition_id: str  # → ai_employee_definitions.definition_id
    tenant_id: str      # → tenants.tenant_id (the tenant being granted)

    # Grant chain — supports the delegation model from Q4 of the
    # 8 May locked decisions doc. `granted_by_tenant_id` is the tenant
    # the grant *originated from* (always SimpliMeta root in 1B; a
    # partner tenant once 1C delegation lands).
    granted_by_user_id: str
    granted_by_tenant_id: str

    status: Literal["active", "suspended", "revoked"] = "active"

    granted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    revoked_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    # Phase 1C.4 — Revoke audit trail. Mirrors `granted_by_*`. Populated
    # when status transitions to "revoked"; remain None on assignments
    # that have never been revoked, and remain stamped (not cleared) if
    # an assignment is later re-activated — the row reflects last-known
    # revoker, with the timestamp authoritative for "is this currently
    # revoked" via `revoked_at != None AND status == 'revoked'`.
    revoked_by_user_id: Optional[str] = None
    revoked_by_tenant_id: Optional[str] = None

    # Optional per-tenant grant constraints. `max_instances` lets a
    # SimpliMeta admin sell e.g. "Acme can deploy up to 3 instances of
    # the Tier-1 Support employee" — enforced at provision time in 1B.5.
    max_instances: Optional[int] = None
    notes: Optional[str] = None

    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TenantAppAssignmentCreate(BaseModel):
    """Schema for granting a definition to a tenant.

    `granted_by_*` fields are derived from the authenticated user in the
    route handler (1B.5) — clients only supply the definition + target
    tenant + optional constraints.
    """
    definition_id: str
    tenant_id: str
    max_instances: Optional[int] = None
    expires_at: Optional[datetime] = None
    notes: Optional[str] = None


class TenantAppAssignmentUpdate(BaseModel):
    """Schema for modifying an existing assignment.

    Revocation is a status transition (`status="revoked"`) rather than a
    DELETE so the audit trail and any deployed instance back-references
    stay intact.
    """
    status: Optional[Literal["active", "suspended", "revoked"]] = None
    max_instances: Optional[int] = None
    expires_at: Optional[datetime] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Procurement sourcing + quote scaffold (Ticket 9, Mode B manual-review).
# See docs/sales-ops/PROCUREMENT_BOT_SCAFFOLD.md and
# docs/sales-ops/PROCUREMENT_VENDOR_DISCOVERY_PACKAGE.md.
# ---------------------------------------------------------------------------


# The full status machine. Documented in PROCUREMENT_BOT_SCAFFOLD.md.
# draft and cancelled are intentionally not used by the current routes
# but are reserved so the enum can grow without a migration.
ProcurementSourcingStatus = Literal[
    "draft",
    "submitted",
    "sourcing_in_progress",
    "review_required",
    "quote_selected",
    "ordered",
    "delivered",
    "failed",
    "cancelled",
]


class ProcurementSourcingRequest(BaseModel):
    """A AEGISAXIOM-side sourcing request submitted on behalf of a SOW.

    AEGISAXIOM is the client of a future procurement bot service. The bot
    fetches quotes from vendor portals and posts the results back via
    the signed webhook. When the bot is not configured the request
    lands in review_required so Procurement can fill quote options
    manually through the existing notification surface. Tenant
    scoping follows the existing implicit-tenant convention through
    opportunity_id ownership; no tenant_id field is introduced here
    until the codebase-wide tenancy migration ships.
    """

    sourcing_request_id: str
    opportunity_id: str
    sow_id: Optional[str] = None
    line_item_ids: List[str] = Field(default_factory=list)
    submitted_by: Optional[str] = None
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: ProcurementSourcingStatus = "review_required"
    status_reason: Optional[str] = None
    bot_request_id: Optional[str] = None
    bot_submitted_at: Optional[datetime] = None
    bot_response_received_at: Optional[datetime] = None
    manual_review_required: bool = True
    manual_review_reason: Optional[str] = None
    selected_quote_id: Optional[str] = None
    ordered_at: Optional[datetime] = None
    ordered_by: Optional[str] = None
    delivered_at: Optional[datetime] = None
    delivered_by: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProcurementSourcingRequestCreate(BaseModel):
    """Request body for POST /procurement/sourcing-requests."""

    opportunity_id: str
    sow_id: Optional[str] = None
    line_item_ids: List[str] = Field(default_factory=list)
    manual_review_reason: Optional[str] = None


class ProcurementQuoteOption(BaseModel):
    """One vendor quote returned for a sourcing request.

    Quotes arrive either via the procurement bot webhook or via a
    manual-entry path on the Procurement surface. Vendor names are
    data-driven; no real vendor names are hardcoded in AEGISAXIOM code
    anywhere. The raw bot payload is intentionally not persisted in
    full; only the structured fields below land in storage so
    Mongo's text-search indexes and audit consumers do not see raw
    Authorization headers or secret tokens.
    """

    quote_id: str
    sourcing_request_id: str
    material_line_id: Optional[str] = None
    vendor_name: str
    vendor_rank: Optional[int] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    availability: Optional[str] = None
    delivery_eta: Optional[str] = None
    shipping_cost: Optional[float] = None
    tax_estimate: Optional[float] = None
    total_cost: Optional[float] = None
    confidence: Optional[float] = None
    source_url: Optional[HttpUrl] = None
    source_captured_at: Optional[datetime] = None
    manual_review_reason: Optional[str] = None
    selected: bool = False
    selected_at: Optional[datetime] = None
    selected_by: Optional[str] = None
    ordered_at: Optional[datetime] = None
    ordered_by: Optional[str] = None
    delivered_at: Optional[datetime] = None
    delivered_by: Optional[str] = None
    external_quote_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProcurementWebhookQuotePayload(BaseModel):
    """Shape of one quote entry inside the procurement bot webhook
    payload. The bot posts a list of these for one sourcing request.
    External_quote_id is the bot's stable id used for idempotent
    upserts.
    """

    external_quote_id: str
    material_line_id: Optional[str] = None
    vendor_name: str
    vendor_rank: Optional[int] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    availability: Optional[str] = None
    delivery_eta: Optional[str] = None
    shipping_cost: Optional[float] = None
    tax_estimate: Optional[float] = None
    total_cost: Optional[float] = None
    confidence: Optional[float] = None
    source_url: Optional[HttpUrl] = None
    source_captured_at: Optional[datetime] = None
    manual_review_reason: Optional[str] = None


class ProcurementWebhookBody(BaseModel):
    """Full webhook body shape.

    sourcing_request_id is the canonical AEGISAXIOM-side identifier. If
    the bot also returns its bot_request_id AEGISAXIOM reconciles against
    the stored value but never trusts the bot for tenant scoping;
    the sourcing_request_id lookup determines which opportunity (and
    therefore which implicit tenant) the quotes attach to.
    """

    sourcing_request_id: str
    bot_request_id: Optional[str] = None
    quotes: List[ProcurementWebhookQuotePayload] = Field(default_factory=list)
    manual_review_reason: Optional[str] = None


# ---- SOW PRODUCT ITEM EXECUTION REQUEST BODIES ----
#
# Used by the single-item routes in sales_ops_procurement.py for
# Procurement pricing entry and PMO/Procurement order/delivery
# marks. Every field is optional so callers can PATCH a partial
# update; the route enforces what minimum data each transition
# requires.


class SOWItemPricingUpdate(BaseModel):
    """Procurement enters pricing/source/availability for one SOW
    item after reviewing supplier portals manually. The route flips
    ``procurement_status`` to ``priced`` and converts a
    ``needs_sourcing`` source to ``manual`` once a price is set."""

    unit_price: Optional[float] = Field(default=None, ge=0)
    supplier_name: Optional[str] = None
    supplier_source_url: Optional[HttpUrl] = None
    availability_note: Optional[str] = None
    procurement_notes: Optional[str] = None


class SOWItemOrderedMark(BaseModel):
    """Body for marking a SOW item ordered. ``expected_delivery_date``
    is ISO datetime so the existing frontend date pickers (which emit
    ISO strings) can post directly. Tracking fields are optional
    because not every vendor returns one at purchase time."""

    expected_delivery_date: Optional[datetime] = None
    order_reference: Optional[str] = None
    tracking_number: Optional[str] = None
    tracking_url: Optional[HttpUrl] = None
    procurement_notes: Optional[str] = None


class SOWItemDeliveredMark(BaseModel):
    """Body for marking a SOW item delivered. PMO callers post this
    with delivery notes only; the route stamps the actor and
    timestamp server-side."""

    delivery_notes: Optional[str] = None


class SOWSectionRegenerateRequest(BaseModel):
    """Body for the POST regenerate route on a single PS SOW prose
    section. ``mode`` defaults to ``preview`` so a casual click does
    not overwrite the existing section content; the user must
    explicitly opt into ``replace`` to apply. ``user_instruction`` is
    a tone hint only — the regeneration service uses keyword matching
    on the string, never injects it directly into the generated
    output (which would risk smuggled content reaching a client
    deliverable). The length cap is small because the regeneration
    only reads boolean tone flags from it; no caller has a reason to
    send more than a short sentence."""

    user_instruction: Optional[str] = Field(default=None, max_length=2000)
    mode: Literal["preview", "replace"] = "preview"
    notes: Optional[str] = None

# =====================================================================
# TENANT APP GRANTS — Phase 1E.1
# =====================================================================
#
# Parallel to TenantAppAssignment, but grants an ingested app from
# `app_registry` (services/ingestion/models.py::AppRegistry) instead
# of an AI Employee definition. Apps are not instance-counted, so this
# model has no `max_instances` field and the associated routes have no
# instance/provisioning concept.
#
# 1E.1 scope: plumbing only — model + routes + indexes + admin "Apps"
# tab on the Tenant Detail page. The dynamic tenant sidebar that
# actually surfaces granted apps lands in 1E.2; per-app tenant_id
# scoping at the route layer lands in 1E.3 just-in-time per app
# actually granted.


class TenantAppGrant(BaseModel):
    """Entitlement record granting a tenant access to an ingested app.

    Granted by a SimpliMeta admin in 1E.1. Tenant-side delegation
    (analog of AI Employee assignment delegation) is deferred to 1F.
    One grant per (tenant_id, app_id) pair — uniqueness enforced at
    the index layer in server.py.
    """
    grant_id: str        # apg_<hex12>
    app_id: str          # → app_registry.app_id
    tenant_id: str       # → tenants.tenant_id (the tenant being granted)

    # Grant chain — for symmetry with TenantAppAssignment. Always
    # SimpliMeta root in 1E.1; carried forward when delegation lands.
    granted_by_user_id: str
    granted_by_tenant_id: str

    status: Literal["active", "suspended", "revoked"] = "active"

    granted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    revoked_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    # Revoke audit trail — populated when status transitions to
    # "revoked". Preserved (not cleared) on reactivation so the row
    # documents the most recent revoke event.
    revoked_by_user_id: Optional[str] = None
    revoked_by_tenant_id: Optional[str] = None

    notes: Optional[str] = None

    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TenantAppGrantCreate(BaseModel):
    """Schema for granting an app to a tenant.

    `granted_by_*` fields are derived from the authenticated user in
    the route handler — clients only supply the target tenant + app
    + optional constraints.
    """
    app_id: str
    tenant_id: str
    expires_at: Optional[datetime] = None
    notes: Optional[str] = None


class TenantAppGrantUpdate(BaseModel):
    """Schema for modifying an existing grant.

    Revocation is a status transition (`status="revoked"`) rather than
    a DELETE so the audit trail stays intact. Reactivation is the
    inverse status transition back to "active".
    """
    status: Optional[Literal["active", "suspended", "revoked"]] = None
    expires_at: Optional[datetime] = None
    notes: Optional[str] = None
