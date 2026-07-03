"""
App Ingestion — Pydantic Models

Data models for the ingestion pipeline: app registry, modules, field catalogues,
pipeline state, and ingestion logs. All persistence goes to MongoDB (fallback mode
until Postgres migration is complete).
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from enum import Enum
import re

from models import gen_id


# ==================== ENUMS ====================

class IngestionStatus(str, Enum):
    PENDING = "pending"
    ANALYZING = "analyzing"
    AWAITING_REVIEW = "awaiting_review"
    ADAPTING = "adapting"
    EXTRACTING = "extracting"
    REGISTERING = "registering"
    CONFIGURED = "configured"
    ACTIVE = "active"
    FAILED = "failed"
    ARCHIVED = "archived"


class ModuleStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    PENDING_REVIEW = "pending_review"


class ArtifactType(str, Enum):
    ORIGINAL_SOURCE = "original_source"
    ADAPTED_SOURCE = "adapted_source"
    CLASSIFICATION_RESULT = "classification_result"
    DEVELOPER_RESPONSES = "developer_responses"
    IMPORT_PLAN = "import_plan"
    FIELD_CATALOGUE = "field_catalogue"


class ClassificationTier(str, Enum):
    INFRASTRUCTURE = "infrastructure"
    APP_LOGIC = "app_logic"
    NEEDS_REVIEW = "needs_review"


class PipelineStage(str, Enum):
    INVOKE = "invoke"
    CLONE = "clone"
    VALIDATE = "validate"
    ANALYZE = "analyze"
    CLASSIFY = "classify"
    INTERVIEW = "interview"
    PLAN = "plan"
    CONFIRM = "confirm"
    STRIP = "strip"
    DECOMPOSE = "decompose"
    ADAPT_BACKEND = "adapt_backend"
    ADAPT_FRONTEND = "adapt_frontend"
    EXTRACT = "extract"
    REGISTER_FIELDS = "register_fields"
    REGISTER_POLICIES = "register_policies"
    GENERATE_RBAC = "generate_rbac"
    REVIEW_RBAC = "review_rbac"
    LINK_AI = "link_ai"
    REGISTER_WORKFLOWS = "register_workflows"
    INTEGRATE_UI = "integrate_ui"
    VALIDATE_E2E = "validate_e2e"
    ACTIVATE = "activate"


class IngestionMode(str, Enum):
    """Ingestion mode — determines whether the app is internal (Emergent → AEGISAXIOM,
    same environment) or external (customer-facing, multi-tenant). Internal mode
    supports pushback to Emergent; external mode is dev-team-managed after
    initial ingestion."""
    INTERNAL = "internal"
    EXTERNAL = "external"


# ==================== SLUG HELPERS ====================

# Reserved namespaces that cannot be used as app slugs.
# Note: `projects` was released 2026-04-29 — the legacy core AEGISAXIOM `projects` module
# was wholesale-replaced by an ingested PMO module that took the slug.
# Note: `finance` was released 2026-05-05 — the legacy core AEGISAXIOM `finance` module
# is being wholesale-replaced by an ingested Financial Command Center that takes
# the slug. Same precedent as the projects PMO release.
RESERVED_NAMESPACES = {
    "opportunities", "contacts", "accounts", "deals", "commissions", "invoices",
    "contracts", "contract_templates", "time", "field_jobs", "users",
    "partner_apps", "integrations", "webhooks", "roles", "chat", "ai_employees",
    "ai_usage", "vega", "orion", "bpmn", "audit", "products", "dashboard",
    "dashboards", "admin", "policies", "workflow_rules", "reports", "payments",
    "branding", "sales_ops", "certifications", "resources",
}

SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")


def validate_slug(slug: str) -> tuple[bool, str]:
    """Validate an app slug. Returns (is_valid, error_message)."""
    if len(slug) < 3 or len(slug) > 100:
        return False, "Slug must be 3–100 characters"
    if not SLUG_PATTERN.match(slug):
        return False, "Slug must be lowercase, hyphens allowed, no leading/trailing hyphens"
    app_slug = slug.replace("-", "_")
    if app_slug in RESERVED_NAMESPACES:
        return False, f"'{slug}' conflicts with reserved AEGISAXIOM namespace '{app_slug}'"
    return True, ""


def slug_to_app_slug(slug: str) -> str:
    """Convert URL slug (hyphenated) to Python identifier (underscored)."""
    return slug.replace("-", "_")


# ==================== CORE MODELS ====================

class FieldDefinition(BaseModel):
    """A single field in the ingestion field catalogue."""
    name: str
    type: str  # string, number, boolean, date, time, list, object
    label: str
    description: str = ""
    computed: bool = False
    options: Optional[List[Dict[str, str]]] = None  # [{value, label}] for enums
    source_field_name: Optional[str] = None  # original name in Emergent app


class EnumDefinition(BaseModel):
    """An enum extracted from the source app."""
    name: str
    entity: str  # which entity this enum belongs to
    field_name: str  # which field uses this enum
    values: List[Dict[str, str]]  # [{value, label}]
    source_class_name: Optional[str] = None  # original Python class name


class EntityRelationship(BaseModel):
    """A relationship between two entities."""
    from_entity: str
    from_field: str
    to_entity: str
    to_field: str = "_id"
    cardinality: str = "many_to_one"  # many_to_one, one_to_many, many_to_many


class ApiRoute(BaseModel):
    """An API route extracted from the source app."""
    method: str  # GET, POST, PUT, DELETE
    path: str  # original path (e.g., /api/projects/{project_id})
    operation: str  # list, get, create, update, delete, custom
    entity: str
    requires_auth: bool = True
    adapted_path: Optional[str] = None  # AEGISAXIOM path after adaptation


class ClassifiedComponent(BaseModel):
    """A component classified during analysis."""
    name: str
    type: str  # route, model, page, component, context, hook, middleware, enum
    tier: ClassificationTier
    reason: str  # why it was classified this way
    source_file: Optional[str] = None
    source_lines: Optional[str] = None  # e.g., "120-180"


class ModuleDescriptor(BaseModel):
    """Describes an imported module."""
    name: str
    entity_name: str
    replaces_module: Optional[str] = None
    ui_placement: str = "portal_tab"  # portal_tab, sidebar, under_existing
    permission_group: str = "Custom"
    icon: Optional[str] = None
    label: Optional[str] = None


# ==================== DOCUMENT MODELS (MongoDB) ====================

class AppRegistry(BaseModel):
    """Master record for an ingested app. MongoDB fallback collection: app_registry."""
    app_id: str = Field(default_factory=lambda: gen_id("app_"))
    name: str
    slug: str  # hyphenated, URL-safe: davies-pmo
    app_slug: str = ""  # underscored, Python-safe: davies_pmo (derived from slug)
    description: str = ""
    source_repo_url: str
    source_commit_sha: Optional[str] = None
    emergent_job_id: Optional[str] = None
    emergent_image: Optional[str] = None
    account_id: Optional[str] = None  # which client owns this app
    ingestion_mode: IngestionMode = IngestionMode.INTERNAL  # internal (Emergent) vs external (multi-tenant)
    status: IngestionStatus = IngestionStatus.PENDING
    ingestion_version: int = 1
    modules_imported: List[ModuleDescriptor] = Field(default_factory=list)
    modules_discarded: List[Dict[str, str]] = Field(default_factory=list)  # [{name, reason}]
    ui_config: Dict[str, Any] = Field(default_factory=dict)

    # ─── Phase 1E.3a — Multi-tenancy entitlement metadata ────────────
    # These two fields make the grant action (tenant_app_grants) the
    # single source of truth for what a tenant can access. When a
    # tenant is granted this app, the middleware in 1E.3b consults
    # `route_prefixes` to authorise requests, and 1E.3d's permission
    # auto-seeder copies `permission_keys` onto the target tenant's
    # tenant_admin role.
    #
    # Empty defaults are intentional: existing AppRegistry rows in
    # the DB will read as [] without migration. The backfill script
    # (scripts/backfill_app_registry_metadata.py) populates them for
    # Projects + Finance — the two apps live today. New apps either
    # populate via the PATCH endpoint on app_ingestion.py, or wait
    # for 1F when the analyzer auto-extracts both fields from the
    # ingested code surface.
    route_prefixes: List[str] = Field(default_factory=list)
    permission_keys: List[str] = Field(default_factory=list)

    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: Optional[datetime] = None

    def model_post_init(self, __context):
        if not self.app_slug:
            self.app_slug = slug_to_app_slug(self.slug)


class AppModule(BaseModel):
    """A single imported module. MongoDB fallback collection: app_modules."""
    module_id: str = Field(default_factory=lambda: gen_id("mod_"))
    app_id: str
    account_id: Optional[str] = None  # tenant scoping — populated by future multi-tenancy work
    name: str  # e.g., "projects"
    entity_name: str  # e.g., "project"
    replaces_module: Optional[str] = None
    ui_placement: str = "portal_tab"
    permission_prefix: str = ""  # e.g., "davies_pmo.projects"
    route_prefix: str = ""  # e.g., "/api/davies-pmo/projects"
    status: ModuleStatus = ModuleStatus.ACTIVE
    field_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AppIngestionLog(BaseModel):
    """Audit trail entry. MongoDB fallback collection: app_ingestion_log."""
    log_id: str = Field(default_factory=lambda: gen_id("alog_"))
    app_id: str
    ingestion_version: int = 1
    action: str  # e.g., "clone_started", "classification_complete"
    stage: PipelineStage
    status: str = "success"  # success, failed, skipped, awaiting_review
    details: Dict[str, Any] = Field(default_factory=dict)
    duration_ms: Optional[int] = None
    performed_by: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IngestionFieldCatalogue(BaseModel):
    """Extracted field catalogue for one module/entity. MongoDB: ingestion_field_catalogue."""
    app_id: str
    module_name: str
    entity_name: str
    version: int = 1
    fields: List[FieldDefinition] = Field(default_factory=list)
    enums: List[EnumDefinition] = Field(default_factory=list)
    relationships: List[EntityRelationship] = Field(default_factory=list)
    api_routes: List[ApiRoute] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IngestionArtifact(BaseModel):
    """Raw artifact stored during ingestion. MongoDB: ingestion_artifacts."""
    app_id: str
    artifact_type: ArtifactType
    version: int = 1
    data: Any = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IngestionState(BaseModel):
    """Pipeline processing state. MongoDB: ingestion_state."""
    app_id: str
    ingestion_version: int = 1
    current_stage: PipelineStage = PipelineStage.INVOKE
    stage_results: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    developer_session: Dict[str, Any] = Field(default_factory=dict)  # Q&A log
    classification: Dict[str, List[Dict]] = Field(default_factory=dict)
    import_plan: Dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
