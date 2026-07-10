"""Pydantic models for MATCH — AppDirect order matching engine.

Mirrors the data dictionary at .ingestion/mappings/match.json. The six route
modules (match_session, match_row, match_provider_template, match_appdirect_call,
match_settings, match_webhooks) import these directly — field names are part
of the wave 1 interface contract and must not be renamed.

ID-bearing models do NOT default their ID field. Route handlers call
``models.gen_id(prefix)`` with the prefix from the data dictionary
(m_sess_, m_row_, m_tmpl_, m_adc_) and set it on create.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ==================== ROW STATUS / ACTION ENUMS ====================
# Source of truth for status/action values. Imported by the row route module
# for body validation and by the webhook route module for recommended_status
# validation.

ROW_STATUS_VALUES: List[str] = [
    "ready",
    "ready_to_draft",
    "needs_review",
    "approved",
    "edited",
    "rejected",
    "escalated",
    "create_new",
    "create_new_order",
    "error",
]

ROW_RECOMMENDED_STATUS_VALUES: List[str] = [
    "ready_to_draft",
    "needs_review",
    "error",
]

ROW_ACTION_VALUES: List[str] = [
    "approved",
    "edited",
    "rejected",
    "escalated",
    "create_new",
    "create_new_order",
]


# ==================== SESSION ====================

class SessionCreate(BaseModel):
    """Body for POST /api/match/sessions. Provider report URL is the result of
    a prior /api/match/uploads/provider-report call — the session record itself
    does not embed the file."""
    provider: str
    mode: str  # "upload" | "live_sql" | "live_api"
    template_id: Optional[str] = None
    provider_report_url: Optional[str] = None
    provider_report_filename: Optional[str] = None
    snapshot_file_url: Optional[str] = None
    snapshot_file_filename: Optional[str] = None
    snapshot_customers_url: Optional[str] = None
    snapshot_customers_filename: Optional[str] = None
    snapshot_orders_url: Optional[str] = None
    snapshot_orders_filename: Optional[str] = None
    sql_connection_string: Optional[str] = None
    label: Optional[str] = None


class Session(SessionCreate):
    """Persisted session document. Immutable once created (no PUT route)."""
    session_id: str
    created_by: str
    created_at: str


# ==================== ROW ====================

class Row(BaseModel):
    """Persisted row document. Never user-created — arrives via the n8n
    callback webhook (POST /api/match/webhooks/row-processed) and is mutated
    by reviewer actions (POST /api/match/rows/{row_id}/action) and the push
    flow (POST /api/match/rows/{row_id}/push-to-appdirect)."""
    row_id: str
    session_id: str
    row_index: int
    source_row: Dict[str, Any] = Field(default_factory=dict)
    output: Dict[str, Any] = Field(default_factory=dict)
    audit_metadata: Dict[str, Any] = Field(default_factory=dict)
    recommended_status: str
    status: str
    review_reason: Optional[str] = None
    escalation_email_draft: Optional[str] = None
    flags: List[str] = Field(default_factory=list)
    callback_url: Optional[str] = None
    pushed_to_appdirect: bool = False
    appdirect_order_id: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None


class RowAction(BaseModel):
    """Body for POST /api/match/rows/{row_id}/action. ``action`` must be in
    ROW_ACTION_VALUES — validated by the route handler."""
    action: str
    edited_output: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None


class BulkApproveRequest(BaseModel):
    """Body for POST /api/match/rows/bulk-approve. Auto-approves all rows in
    a session whose audit_metadata.confidence_score >= threshold."""
    session_id: str
    threshold: int = 95


class PushToAppDirectRequest(BaseModel):
    """Body for POST /api/match/rows/{row_id}/push-to-appdirect. When
    dry_run is True (or settings.appdirect.live_post_enabled is False), the
    payload is built and logged but no live POST is made."""
    dry_run: bool = False


# ==================== PROVIDER TEMPLATE ====================

class ColumnMapping(BaseModel):
    """One row of the template's column-mapping table. ``target_path`` is a
    dotted Contract H path (e.g. ``output.customer.name``); ``transforms`` is
    a list of named transforms applied left-to-right by n8n."""
    source_column: str
    target_path: str
    transforms: List[str] = Field(default_factory=list)
    required: bool = False


class BusinessRule(BaseModel):
    """One row of the template's business-rules table. Evaluated by n8n during
    matching; if ``condition_path`` ``condition_op`` ``condition_value`` is
    true, ``action_path`` is set to ``action_value``."""
    name: str
    condition_path: str
    condition_op: str
    condition_value: Any
    action_path: str
    action_value: Any


class ProviderTemplateCreate(BaseModel):
    """Body for POST /api/match/provider-templates."""
    name: str
    default_product: Optional[str] = None
    sales_rep_priority: Optional[str] = None
    default_order_status: Optional[str] = None
    column_mappings: List[ColumnMapping] = Field(default_factory=list)
    business_rules: List[BusinessRule] = Field(default_factory=list)
    appdirect_advisor_id: Optional[int] = None
    appdirect_product_id: Optional[int] = None
    appdirect_provider_id: Optional[int] = None
    appdirect_account_manager_id: Optional[int] = None


class ProviderTemplate(ProviderTemplateCreate):
    """Persisted provider template document."""
    template_id: str
    created_at: str
    updated_at: Optional[str] = None


class ProviderTemplateUpdate(BaseModel):
    """Body for PUT /api/match/provider-templates/{template_id}. All fields
    optional — only provided fields are merged onto the existing document."""
    name: Optional[str] = None
    default_product: Optional[str] = None
    sales_rep_priority: Optional[str] = None
    default_order_status: Optional[str] = None
    column_mappings: Optional[List[ColumnMapping]] = None
    business_rules: Optional[List[BusinessRule]] = None
    appdirect_advisor_id: Optional[int] = None
    appdirect_product_id: Optional[int] = None
    appdirect_provider_id: Optional[int] = None
    appdirect_account_manager_id: Optional[int] = None


class SuggestMappingsRequest(BaseModel):
    """Body for POST /api/match/provider-templates/suggest-mappings.
    Proxied through to n8n's /webhook/suggest-mappings endpoint."""
    provider_report_url: str
    snapshot_customers_url: str
    template_id: Optional[str] = None


# ==================== SETTINGS — SUB-BLOCKS ====================

class N8nSettings(BaseModel):
    """n8n webhook configuration. ``shared_auth_token`` is the only field
    that gets the encrypt-at-rest / mask-on-wire treatment in this block."""
    trigger_webhook_url: str = ""
    magic_mapping_webhook_url: str = ""
    reviewer_action_webhook_url: str = ""
    shared_auth_token: str = ""  # CIPHERTEXT at rest; MASKED_PASSWORD_SENTINEL on wire.
    callback_url: str = ""        # Auto-derived per deploy from FRONTEND_URL.


class SqlConnection(BaseModel):
    """Live SQL CRM connection. ``service_password`` is encrypted at rest."""
    server: str = ""
    database: str = ""
    port: int = 1433
    auth_mode: str = "service_account"  # "service_account" | "integrated"
    service_user: str = ""
    service_password: str = ""    # CIPHERTEXT at rest; MASKED_PASSWORD_SENTINEL on wire.
    read_tables: List[str] = Field(
        default_factory=lambda: ["customers", "orders", "order_attributes"]
    )


class ApiConnection(BaseModel):
    """Live HTTP CRM connection. ``api_key`` is encrypted at rest."""
    base_url: str = ""
    api_key: str = ""             # CIPHERTEXT at rest; MASKED_PASSWORD_SENTINEL on wire.
    customers_endpoint: str = ""


class CrmSettings(BaseModel):
    connection_mode: str = "snapshot"  # "snapshot" | "live_sql" | "live_api"
    sql: SqlConnection = Field(default_factory=SqlConnection)
    api: ApiConnection = Field(default_factory=ApiConnection)


class AppDirectEnumDefaults(BaseModel):
    """Fallback AppDirect enum FK IDs used when the cached lookup tables
    don't yield a match. Operator-configurable per environment."""
    contract_type_id_new: int = 0
    contract_term_id_12mo: int = 0
    contract_term_id_24mo: int = 0
    contract_term_id_36mo: int = 0
    sales_agent_id: int = 0


class AppDirectSettings(BaseModel):
    """AppDirect Provider Sales API connection. ``bearer_token`` is encrypted
    at rest. ``live_post_enabled`` is the safety switch — when False, all
    AppDirectClient calls are forced to dry_run mode."""
    base_url: str = ""
    bearer_token: str = ""        # CIPHERTEXT at rest; MASKED_PASSWORD_SENTINEL on wire.
    multipass_enabled: bool = False
    live_post_enabled: bool = False
    defaults: AppDirectEnumDefaults = Field(default_factory=AppDirectEnumDefaults)
    last_test: Optional[Dict[str, Any]] = None
    cached_lookups: Optional[Dict[str, Any]] = None
    lookups_fetched_at: Optional[str] = None


class ScheduleSettings(BaseModel):
    """When and how rows get auto-approved / queued for review."""
    mode: str = "immediate"       # "immediate" | "daily_batch" | "manual_approval"
    daily_batch_time: str = "09:00"
    timezone: str = "America/New_York"
    auto_approve_threshold: int = 95
    reviewer_threshold: int = 70
    escalation_email: str = ""


class ForwardRule(BaseModel):
    """One rule in the email-forwarding ingestion mode. ``match_pattern`` is
    matched against the inbound email; on hit, a session is auto-created
    with the named provider + template_id."""
    match_pattern: str
    provider: str
    template_id: str
    action: str


class IngestionSettings(BaseModel):
    mode: str = "manual_upload"   # "email_forwarding" | "manual_upload" | "api_push"
    forward_rules: List[ForwardRule] = Field(default_factory=list)


class Settings(BaseModel):
    """Singleton match_settings document. Stored at {_singleton: True}.
    Deep-merge semantics on PUT — operators update one sub-block at a time
    without nulling siblings."""
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    crm: CrmSettings = Field(default_factory=CrmSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    appdirect: AppDirectSettings = Field(default_factory=AppDirectSettings)
    n8n: N8nSettings = Field(default_factory=N8nSettings)
    updated_at: Optional[str] = None


class SettingsUpdate(BaseModel):
    """Body for PUT /api/match/settings. Every sub-block is optional;
    omitted sub-blocks are preserved as-is by the deep-merge in the route."""
    ingestion: Optional[IngestionSettings] = None
    crm: Optional[CrmSettings] = None
    schedule: Optional[ScheduleSettings] = None
    appdirect: Optional[AppDirectSettings] = None
    n8n: Optional[N8nSettings] = None


class TestConnectionResponse(BaseModel):
    """Response shape shared by POST /api/match/settings/test-sql,
    test-appdirect, and test-n8n."""
    status: str   # "success" | "error"
    message: str
    latency_ms: Optional[int] = None
    tables_verified: List[str] = Field(default_factory=list)


# ==================== APPDIRECT CALL LOG ====================

class AppDirectCall(BaseModel):
    """Read-only log row written by AppDirectClient on every API attempt.
    ``request_headers_redacted`` MUST already have the Authorization header
    replaced with MASKED_PASSWORD_SENTINEL before this model is constructed —
    no plaintext bearer ever lands in storage."""
    call_id: str
    row_id: str
    session_id: str
    endpoint: str
    request_body: Dict[str, Any]
    request_headers_redacted: Dict[str, str]
    response_status: Optional[int] = None
    response_body: Optional[Any] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    mode: str  # "dry_run" | "live"
    actor: str
    created_at: str


# ==================== WEBHOOK PAYLOADS ====================

class N8nRowProcessedPayload(BaseModel):
    """Body posted by n8n to POST /api/match/webhooks/row-processed. Forward
    compatibility is important — the n8n contract may grow new keys ahead of
    AEGISAXIOM picking them up, so we allow extras."""
    model_config = ConfigDict(extra="allow")

    session_id: str
    row_index: int
    source_row: Dict[str, Any] = Field(default_factory=dict)
    output: Dict[str, Any] = Field(default_factory=dict)
    audit_metadata: Dict[str, Any] = Field(default_factory=dict)
    recommended_status: str
    review_reason: Optional[str] = None
    escalation_email_draft: Optional[str] = None
    callback_url: Optional[str] = None
