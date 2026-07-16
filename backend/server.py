from fastapi import FastAPI, APIRouter, Request, Response, Depends
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from rate_limiter import limiter
import os
import logging
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# ---- Security Configuration ---- (must be parsed BEFORE FastAPI app is created)
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]


def env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


# Live-DB safety flags.
#
# Use these when pointing local Docker at a live/dev Atlas database with a
# restricted user. They prevent startup from mutating roles, permissions,
# default resource types, policies, workflows, model pricing, and indexes.
SKIP_STARTUP_SEEDS = env_flag("SKIP_STARTUP_SEEDS")
SKIP_STARTUP_INDEXES = env_flag("SKIP_STARTUP_INDEXES")
DISABLE_BACKGROUND_JOBS = env_flag("DISABLE_BACKGROUND_JOBS")

# Initialize database BEFORE importing routes
import database

database.init(os.environ["MONGO_URL"], os.environ["DB_NAME"])

# Import routers after DB init
from routes.auth import router as auth_router
from routes.tenants import router as tenants_router
from routes.ai_employee_catalog import router as ai_employee_catalog_router
from routes.tenant_app_grants import router as tenant_app_grants_router
from routes.tenant_sidebar import router as tenant_sidebar_router
from routes.crm import router as crm_router
from routes.financials import router as financials_router
from routes.operations import router as operations_router
from routes.field_services import router as field_services_router
from routes.admin import router as admin_router
from routes.partners import router as partners_router
from routes.chat import router as chat_router
from routes.integrations import router as integrations_router
from routes.roles import router as roles_router
from routes.agent_dashboard import router as agent_dashboard_router
from routes.audit import router as audit_router
from routes.zoho_sync import router as zoho_sync_router
from routes.qbo_sync import router as qbo_sync_router
from routes.contracts import router as contracts_router
from routes.projects_project import router as projects_project_router
from routes.projects_task import router as projects_task_router
from routes.projects_task_comment import router as projects_task_comment_router
from routes.projects_task_attachment import router as projects_task_attachment_router
from routes.projects_sprint import router as projects_sprint_router
from routes.projects_wave import router as projects_wave_router
from routes.projects_thread import router as projects_thread_router
from routes.projects_blocker_ticket import router as projects_blocker_ticket_router
from routes.projects_standup import router as projects_standup_router
from routes.projects_template import router as projects_template_router
from routes.projects_dashboard import router as projects_dashboard_router
from routes.projects_my_work import router as projects_my_work_router
from routes.projects_assignable_users import router as projects_assignable_users_router
from routes.projects_capacity import router as projects_capacity_router
from routes.projects_import_export import router as projects_import_export_router
from routes.projects_recent_changes import router as projects_recent_changes_router
from routes.projects_events import router as projects_events_router
from routes.projects_saved_views import router as projects_saved_views_router
from routes.time_entries import router as time_entries_router
from routes.default_resource_types import router as default_resource_types_router
from routes.milestone_parser import router as milestone_parser_router
from routes.commissions import router as commissions_router
from routes.products import router as products_router
from routes.distributors import router as distributors_router
from routes.hardware_material_requests import router as hardware_material_requests_router
from routes.user_resource_types import router as user_resource_types_router
from routes.contractor_businesses import router as contractor_businesses_router
from routes.resources import router as resources_router
from routes.opportunities import router as opportunities_router
from routes.contacts import router as contacts_router
from routes.sales_ops_procurement import router as sales_ops_procurement_router
from routes.solutions_design import router as solutions_design_router
from routes.proposals import router as proposals_router
from routes.contract_templates import router as contract_templates_router
from routes.sow_milestone_templates import router as sow_milestone_templates_router
from routes.automation_rules import router as automation_rules_router
from routes.dashboards import router as dashboards_router
from routes.time_locking import router as time_locking_router
from routes.policies import router as policies_router
from routes.fireflies import router as fireflies_router
from routes.ai_employees import router as ai_employees_router
from routes.ai_usage import router as ai_usage_router
from routes.workflow_rules import router as workflow_rules_router
from routes.org_branding import router as org_branding_router
from routes.ai_chat_artifacts import router as artifacts_router
from routes.certifications import router as certifications_router
from routes.chat_uploads import router as chat_uploads_router
from routes.vega_sessions import router as vega_sessions_router
from routes.vega_v3_sessions_user import router as vega_v3_sessions_user_router
from routes.vega_v3_versions import router as vega_v3_versions_router
from routes.vega_v3_regenerate import router as vega_v3_regenerate_router
from routes.vega_v3_exports import router as vega_v3_exports_router
from routes.orion import router as orion_router
from routes.bpmn import router as bpmn_router
from routes.app_ingestion import router as app_ingestion_router
from routes.nova import router as nova_router
from routes.nova_exports import router as nova_exports_router
from routes.finance_meta import router as finance_meta_router
from routes.finance_kpis import router as finance_kpis_router
from routes.finance_statements import router as finance_statements_router
from routes.finance_working_capital import router as finance_working_capital_router
from routes.finance_qbo_payables import router as finance_qbo_payables_router
from routes.finance_revenue import router as finance_revenue_router
from routes.finance_lists import router as finance_lists_router
from routes.finance_gl import router as finance_gl_router
from routes.finance_operations_coa import router as finance_operations_coa_router
from routes.finance_coa_mapping import router as finance_coa_mapping_router
from routes.finance_draft import router as finance_draft_router
from routes.finance_draft_autogen import router as finance_draft_autogen_router
from routes.finance_dashboard import router as finance_dashboard_router
from routes.finance_widget import router as finance_widget_router
from routes.time_my_earnings import router as time_my_earnings_router
from routes.finance_commissions import router as finance_commissions_router
from routes.finance_referral_mrr import router as finance_referral_mrr_router
from routes.operations_contracts import router as operations_contracts_router
from routes.api_errors import router as api_errors_router
from routes.ai_dispatcher import router as ai_dispatcher_router
# MATCH ingestion (app_id app_a984d000d748). See .ingestion/mappings/match.json.
from routes.match_session import router as match_session_router
from routes.match_row import router as match_row_router
from routes.match_appdirect_call import router as match_appdirect_call_router
from routes.match_provider_template import router as match_provider_template_router
from routes.match_settings import router as match_settings_router
from routes.match_webhooks import router as match_webhooks_router
from routes.match_uploads import router as match_uploads_router
import services.field_registry_finance_qbo  # noqa: F401  (import-time registration)

app = FastAPI(
    title="AEGISAXIOM API",
    version="1.0.0",
    # FIX CRITICAL-4: Disable interactive API docs in production to prevent
    # attackers from using the Swagger UI to enumerate and test endpoints.
    docs_url=None if PRODUCTION_MODE else "/docs",
    redoc_url=None if PRODUCTION_MODE else "/redoc",
    openapi_url=None if PRODUCTION_MODE else "/openapi.json",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


# ---- v1.3 Theme B: GCP Error Reporting on uncaught exceptions ------------
#
# Catches anything that bubbles out of a route or middleware and reports it
# to GCP Error Reporting (no-op if GCP_PROJECT_ID is unset — see
# services/observability.py). The handler re-raises so FastAPI's default
# error response path still runs, preserving existing behavior for clients.
@app.exception_handler(Exception)
async def _unhandled_exception_to_error_reporting(request: Request, exc: Exception):
    from services.observability import report_exception
    from fastapi.exceptions import HTTPException as _HTTPExc, RequestValidationError

    # Do not page on expected/by-design HTTP errors (4xx) or validation errors.
    # We only want to surface real bugs (5xx, uncaught exceptions).
    if isinstance(exc, (_HTTPExc, RequestValidationError, RateLimitExceeded)):
        raise exc

    try:
        user = getattr(request.state, "user", None)
        report_exception(exc, context={
            "user_id": getattr(user, "user_id", None) if user else None,
            "user_email": getattr(user, "email", None) if user else None,
            "request_id": getattr(request.state, "request_id", None),
            "http_method": request.method,
            "http_path": str(request.url.path),
            "http_status": 500,
        })
    except Exception:  # noqa: BLE001
        # Observability must not break the request path.
        pass

    # Mirror FastAPI's default 500 shape so clients see the same response.
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )


api_router = APIRouter(prefix="/api")

api_router.include_router(auth_router, prefix="/auth", tags=["Auth"])
api_router.include_router(tenants_router, prefix="/tenants", tags=["Tenants"])
# Phase 1B.5 — AI Employee catalog + tenant assignments + provisioning.
# No prefix: this router defines fully-qualified paths
# (/ai-employee-definitions, /tenants/{tenant_id}/assignments, etc.)
# so it slots in alongside the per-tenant routes naturally.
api_router.include_router(ai_employee_catalog_router, tags=["AI Employee Catalog"])
# Phase 1E.1 — Tenant app grants (entitlements for ingested apps).
# Endpoints land under /tenant-app-grants/catalog and
# /tenants/{tenant_id}/app-grants — no shared URL prefix, the router
# defines paths explicitly to mirror the ai_employee_catalog convention.
api_router.include_router(tenant_app_grants_router, tags=["Tenant App Grants"])
# Phase 1E.2 — Tenant sidebar config (returns granted apps for the
# caller's tenant). Mounted with no prefix; the router defines
# /tenant/sidebar-config directly.
api_router.include_router(tenant_sidebar_router, tags=["Tenant Sidebar"])
api_router.include_router(crm_router, tags=["Accounts & Deals"])
api_router.include_router(financials_router, tags=["Financials"])
api_router.include_router(operations_router, tags=["Operations"])
api_router.include_router(field_services_router, tags=["Field Services"])
api_router.include_router(admin_router, prefix="/admin", tags=["Admin"])
api_router.include_router(partners_router, prefix="/partners", tags=["Partners"])
api_router.include_router(chat_router, prefix="/chat", tags=["Chat"])
api_router.include_router(integrations_router, prefix="/integrations", tags=["Integrations"])
api_router.include_router(roles_router, prefix="/roles", tags=["Roles"])
api_router.include_router(agent_dashboard_router, tags=["Agent Dashboard"])
api_router.include_router(audit_router, prefix="/audit", tags=["Audit Log"])
api_router.include_router(finance_meta_router, prefix="/finance", tags=["Finance FCC - Meta"])
api_router.include_router(finance_kpis_router, prefix="/finance", tags=["Finance FCC - KPIs"])
api_router.include_router(finance_statements_router, prefix="/finance", tags=["Finance FCC - Statements"])
api_router.include_router(finance_working_capital_router, prefix="/finance", tags=["Finance FCC - Working Capital"])
api_router.include_router(finance_qbo_payables_router, prefix="/finance", tags=["Finance FCC - QBO Payables"])
api_router.include_router(finance_revenue_router, prefix="/finance", tags=["Finance FCC - Revenue"])
api_router.include_router(finance_lists_router, prefix="/finance", tags=["Finance FCC - Lists"])
api_router.include_router(finance_gl_router, prefix="/finance", tags=["Finance FCC - GL Explorer"])
api_router.include_router(finance_operations_coa_router, prefix="/finance", tags=["Finance FCC - CoA"])
api_router.include_router(finance_coa_mapping_router, prefix="/finance", tags=["Finance FCC - CoA Mappings"])
api_router.include_router(finance_draft_router, prefix="/finance", tags=["Finance FCC - Drafts / Sync Queue"])
api_router.include_router(finance_draft_autogen_router, prefix="/finance", tags=["Finance FCC - Drafts / Auto-Generate"])
api_router.include_router(finance_dashboard_router, prefix="/finance", tags=["Finance FCC - Workspace Dashboards"])
api_router.include_router(finance_widget_router, prefix="/finance", tags=["Finance FCC - Workspace Widgets"])
api_router.include_router(time_my_earnings_router, prefix="/time", tags=["Time - My Earnings"])
api_router.include_router(finance_commissions_router, prefix="/finance", tags=["Finance FCC - Commissions"])
api_router.include_router(finance_referral_mrr_router, prefix="/finance", tags=["Finance FCC - Referral MRR"])
api_router.include_router(operations_contracts_router, prefix="/operations", tags=["Operations - Contracts"])
api_router.include_router(zoho_sync_router, tags=["Zoho Sync"])
api_router.include_router(qbo_sync_router, tags=["QBO Sync"])
api_router.include_router(contracts_router, tags=["Contracts"])
# Wave 4 (contracts-time-mgmt) — billing triggers + adjustments. Mounted
# without a prefix because their route paths already start with
# /contracts/{contract_id}/... matching the contracts router style.
from routes.contract_triggers import router as contract_triggers_router
from routes.contract_adjustments import router as contract_adjustments_router
api_router.include_router(contract_triggers_router, tags=["Contracts - Billing Triggers"])
api_router.include_router(contract_adjustments_router, tags=["Contracts - Adjustments"])
# Wave 5 (contracts-time-mgmt) — bi-weekly invoice batch approval queue.
from routes.invoice_batches import router as invoice_batches_router
api_router.include_router(invoice_batches_router, tags=["Contracts - Invoice Batches"])
# IMPORTANT: projects_my_work must be registered BEFORE projects_project and
# projects_task so its specific paths (/projects/mine, /tasks/assigned-to-me,
# /blocker-tickets/owned-by-me) are not shadowed by the {project_id} /
# {task_id} parameter matchers that come later.
api_router.include_router(projects_my_work_router, prefix="/projects", tags=["Projects - My Work"])
api_router.include_router(projects_project_router, prefix="/projects", tags=["Projects"])
api_router.include_router(projects_task_router, prefix="/projects", tags=["Projects - Tasks"])
api_router.include_router(projects_task_comment_router, prefix="/projects", tags=["Projects - Task Comments"])
api_router.include_router(projects_task_attachment_router, prefix="/projects", tags=["Projects - Task Attachments"])
api_router.include_router(projects_sprint_router, prefix="/projects", tags=["Projects - Sprints"])
api_router.include_router(projects_wave_router, prefix="/projects", tags=["Projects - Waves"])
api_router.include_router(projects_thread_router, prefix="/projects", tags=["Projects - Threads"])
api_router.include_router(projects_blocker_ticket_router, prefix="/projects", tags=["Projects - Blockers"])
api_router.include_router(projects_standup_router, prefix="/projects", tags=["Projects - Standups"])
api_router.include_router(projects_template_router, prefix="/projects", tags=["Projects - Templates"])
api_router.include_router(projects_dashboard_router, prefix="/projects", tags=["Projects - Dashboard"])
api_router.include_router(projects_assignable_users_router, prefix="/projects", tags=["Projects - Assignable Users"])
api_router.include_router(projects_capacity_router, prefix="/projects", tags=["Projects - Capacity"])
api_router.include_router(projects_import_export_router, prefix="/projects", tags=["Projects - Import/Export"])
api_router.include_router(projects_recent_changes_router, prefix="/projects", tags=["Projects - Recent Changes"])
# v1.3 Wave 1 / Thread 1A — Realtime SSE + presence. Registered after the
# {project_id}-anchored routers above so the `/projects/{project_id}/events`
# and `/projects/{project_id}/presence/heartbeat` paths resolve cleanly.
api_router.include_router(projects_events_router, prefix="/projects", tags=["Projects - Realtime Events"])
api_router.include_router(projects_saved_views_router, prefix="/projects", tags=["Projects - Saved Views"])
api_router.include_router(time_entries_router, tags=["Time Management"])
api_router.include_router(default_resource_types_router, prefix="/admin", tags=["Admin - Resource Types"])
api_router.include_router(milestone_parser_router, prefix="/contracts", tags=["Contracts"])
api_router.include_router(commissions_router, prefix="/commission-management", tags=["Commission Management"])
api_router.include_router(products_router, prefix="/admin", tags=["Products & Services"])
api_router.include_router(distributors_router, prefix="/admin", tags=["Distributors"])
api_router.include_router(user_resource_types_router, tags=["User Resource Types"])
api_router.include_router(contractor_businesses_router, prefix="/admin", tags=["Contractor Businesses"])
api_router.include_router(opportunities_router, tags=["Opportunity Management"])
api_router.include_router(contacts_router, tags=["Contacts"])
api_router.include_router(sales_ops_procurement_router, prefix="/sales-ops", tags=["Sales Ops Procurement"])
api_router.include_router(hardware_material_requests_router, prefix="/sales-ops", tags=["Hardware & Material Requests"])
api_router.include_router(solutions_design_router, prefix="/sales-ops", tags=["Solutions Design"])
api_router.include_router(proposals_router, prefix="/sales-ops", tags=["Sales Ops Proposals"])
api_router.include_router(contract_templates_router, prefix="/admin", tags=["Admin - Contract Templates"])
api_router.include_router(sow_milestone_templates_router, prefix="/admin", tags=["Admin - SOW Milestone Templates"])
api_router.include_router(automation_rules_router, prefix="/sales-ops", tags=["Sales Ops Automations"])
api_router.include_router(fireflies_router, prefix="/sales-ops", tags=["Fireflies Transcripts"])
api_router.include_router(dashboards_router, tags=["Dashboards"])
api_router.include_router(time_locking_router, prefix="/admin", tags=["Time Locking Automation"])
api_router.include_router(resources_router, tags=["Resources"])
api_router.include_router(policies_router, prefix="/admin", tags=["Policy Engine"])
api_router.include_router(ai_employees_router, tags=["AI Employees"])
api_router.include_router(ai_usage_router, prefix="/admin/ai-usage", tags=["AI Usage Analytics"])
api_router.include_router(workflow_rules_router)
api_router.include_router(org_branding_router, prefix="/admin", tags=["Organization Branding"])
api_router.include_router(artifacts_router, tags=["Chat Artifacts"])
api_router.include_router(certifications_router, tags=["Certifications"])
api_router.include_router(chat_uploads_router, tags=["Chat Uploads"])
api_router.include_router(vega_sessions_router, tags=["Vega Sessions"])
api_router.include_router(vega_v3_sessions_user_router, tags=["Vega v3 - My Sessions"])
api_router.include_router(vega_v3_versions_router, tags=["Vega v3 - Artifact Versions"])
api_router.include_router(vega_v3_regenerate_router, tags=["Vega v3 - Regenerate"])
api_router.include_router(vega_v3_exports_router, tags=["Vega v3 - Exports"])
api_router.include_router(orion_router, prefix="/orion", tags=["Orion SOW Builder"])
api_router.include_router(bpmn_router, prefix="/bpmn", tags=["BPMN Diagrams"])
api_router.include_router(app_ingestion_router, prefix="/app-ingestion", tags=["App Ingestion"])
api_router.include_router(nova_router, prefix="/nova", tags=["Nova"])
# v1.3 Wave 3.5 — frontend error forwarder (POST /api/errors). Mounts at the
# api_router (already prefixed with /api), so the final path is /api/errors.
api_router.include_router(api_errors_router, tags=["Observability - Frontend Errors"])
api_router.include_router(nova_exports_router, prefix="/nova", tags=["Nova - Exports"])
# v1.3 Wave 3.5 — frontend error forwarder (POST /api/errors). Mounts at the
# api_router (already prefixed with /api), so the final path is /api/errors.
api_router.include_router(api_errors_router, tags=["Observability - Frontend Errors"])
api_router.include_router(ai_dispatcher_router, prefix="/ai-dispatcher", tags=["AI Dispatcher"])

# ============================================================================
# MATCH — AppDirect order-matching engine (ingested 2026-05-19, app_a984d000d748).
# n8n workflow `AppDirect Provider Report Processor v3` (lXCCpxQIcjeWtrOo)
# is the AI matching layer; this app surfaces sessions/rows/templates/settings
# for human review and pushes approved rows to AppDirect. The webhook router
# is the inbound side of the n8n contract — auth via shared bearer in
# settings.n8n.shared_auth_token (NOT user JWT).
# ============================================================================
api_router.include_router(match_session_router, prefix="/match/sessions", tags=["MATCH - Sessions"])
api_router.include_router(match_row_router, prefix="/match/rows", tags=["MATCH - Rows"])
api_router.include_router(match_appdirect_call_router, prefix="/match/rows", tags=["MATCH - AppDirect Calls"])
api_router.include_router(match_provider_template_router, prefix="/match/provider-templates", tags=["MATCH - Provider Templates"])
api_router.include_router(match_settings_router, prefix="/match/settings", tags=["MATCH - Settings"])
api_router.include_router(match_webhooks_router, prefix="/match/webhooks", tags=["MATCH - Webhooks (inbound from n8n)"])
api_router.include_router(match_uploads_router, prefix="/match/uploads", tags=["MATCH - Uploads"])


@api_router.get("/health")
async def health():
    return {"status": "ok", "service": "AEGISAXIOM API"}


app.include_router(api_router)

# Dangerous endpoints that should be disabled in production
DANGEROUS_ENDPOINTS = ["/api/auth/setup-admin", "/api/admin/seed", "/api/admin/seed-all"]


# ---- Production Mode Security Middleware ----
class ProductionSecurityMiddleware(BaseHTTPMiddleware):
    """Blocks dangerous endpoints when PRODUCTION_MODE is enabled"""

    async def dispatch(self, request: Request, call_next):
        if PRODUCTION_MODE and request.url.path in DANGEROUS_ENDPOINTS:
            logger.warning(f"Blocked dangerous endpoint in production: {request.url.path}")
            return JSONResponse(
                {"detail": "This endpoint is disabled in production mode"},
                status_code=403
            )
        return await call_next(request)


# ---- Secure CORS Middleware ----
# SECURITY FIX: Use explicit allowed origins instead of reflecting all origins
# This prevents session hijacking attacks from malicious sites
CORS_ALLOW_HEADERS = "Content-Type, Authorization, X-Requested-With, Accept, Origin, If-Match, Last-Event-ID"
CORS_ALLOW_METHODS = "GET, POST, PUT, DELETE, OPTIONS, PATCH"


class SecureCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin", "")

        # Only allow explicitly whitelisted origins
        is_allowed = origin in ALLOWED_ORIGINS

        # Handle preflight
        if request.method == "OPTIONS":
            response = Response(status_code=204)
            if is_allowed:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
                response.headers["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
                response.headers["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
                response.headers["Access-Control-Max-Age"] = "86400"
                response.headers["Vary"] = "Origin"
            return response

        response = await call_next(request)
        if is_allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = "Origin"
        return response


# ---- Security Response Headers Middleware ----
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security headers to all responses to mitigate XSS, clickjacking, MIME sniffing."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self' https:"
        if PRODUCTION_MODE:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        return response


# Apply middleware in order (outermost runs first)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(ProductionSecurityMiddleware)
app.add_middleware(SecureCORSMiddleware)

# Phase 1E.3b — Tenant scoping middleware.
#
# Added AFTER the three security middlewares so CORS preflight
# (OPTIONS) isn't blocked, and so production-mode and security-
# header behaviour wraps the whole stack. Starlette runs the LAST-
# added middleware FIRST on inbound — so request order is:
#   1. TenantScoping  (resolves tenant, gates app-owned routes)
#   2. SecureCORS
#   3. ProductionSecurity
#   4. SecurityHeaders (response-side only)
#
# The middleware itself is no-op for non-/api/ paths and for /api/auth/*
# so static files, login, and refresh flows are unaffected. See
# middleware/tenant_scoping.py for the full enforcement logic.
from middleware.tenant_scoping import TenantScopingMiddleware
app.add_middleware(TenantScopingMiddleware)

# Mount static files for uploads (must be done before startup)
upload_dir = "/app/backend/uploads"
os.makedirs(upload_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=upload_dir), name="uploads")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@app.on_event("startup")
async def on_startup():
    db = database.get_db()

    # ------------------------------------------------------------
    # Observability — GCP Error Reporting (v1.3 Theme B)
    #
    # Eager-init so the first failing request doesn't pay the
    # credentials/handshake cost. No-op when GCP_PROJECT_ID isn't set.
    # ------------------------------------------------------------
    try:
        from services.observability import _init_client as _init_error_reporting
        _init_error_reporting()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Error Reporting init at startup failed (non-fatal): {e}")

    # ------------------------------------------------------------
    # Startup bootstrap / seed writes
    # ------------------------------------------------------------
    if SKIP_STARTUP_SEEDS:
        logger.info("Skipping startup seed/bootstrap writes because SKIP_STARTUP_SEEDS=true")
    else:
        # Seed the SimpliMeta root tenant + backfill existing users.
        # Must run before role/permission seeds because they may scope
        # to tenants in later phases.
        from seeds.seed_tenants import seed_tenants
        await seed_tenants(db)

        from routes.roles import seed_default_roles
        await seed_default_roles(db)

        # Phase 1B.6 — the 1B.4 catalog migration startup hook is removed.
        # It was a rollout-window safety net; with 1B.5's provisioning API
        # now the canonical (and only) path that creates linked instance
        # rows, the hook is dead code on every boot.
        #
        # The seed function itself stays at seeds/seed_ai_employee_catalog.py
        # because it's idempotent and useful for ad-hoc reconciliation
        # (e.g., backfilling linkage if an emergency raw-Mongo insert ever
        # bypasses the API). Callers must invoke it manually now.

        # Create permissions registry indexes BEFORE seeding so concurrent
        # startups cannot insert duplicate documents on the same key.
        try:
            await db.permissions.create_index("key", unique=True)
            await db.permissions.create_index("source")
            await db.permissions.create_index("app_id", sparse=True)
            await db.permissions.create_index("is_active")
        except Exception as e:
            logger.warning(f"Permissions registry index setup hit a non-fatal error: {e}")

        from services.permission_registry import (
            seed_system_permissions,
            deactivate_keys,
            strip_keys_from_roles,
        )
        await seed_system_permissions(db)

        # Retire deprecated permission keys (idempotent)
        try:
            from routes.roles import _DEPRECATED_FINANCE_KEYS
            await deactivate_keys(_DEPRECATED_FINANCE_KEYS, db)
            await strip_keys_from_roles(_DEPRECATED_FINANCE_KEYS, db)
        except Exception as e:
            logger.warning(f"Deprecated key cleanup warning (non-fatal): {e}")

        try:
            from seeds.seed_projects_rbac import seed_projects_rbac
            await seed_projects_rbac(db)
        except Exception as e:
            logger.warning(f"Projects RBAC seed warning (non-fatal): {e}")

        # Built-in ingested apps that pre-date the live /ingest-app pipeline
        # (projects-pmo, finance-cc) need app_registry rows so they show up
        # in the App Ingestion admin module. Idempotent upsert.
        try:
            from seeds.seed_builtin_ingestions import seed_builtin_ingestions
            await seed_builtin_ingestions(db)
        except Exception as e:
            logger.warning(f"Built-in ingestions seed warning (non-fatal): {e}")

        from routes.default_resource_types import seed_default_resource_types
        await seed_default_resource_types(db)

        from services.policy_seeds import seed_system_policies
        await seed_system_policies(db)

        from services.workflow_seeds import seed_system_workflows
        await seed_system_workflows(db)

        # AI Dispatcher (AppDirect dispatch AI POC) — idempotent.
        # Creates dispatcher_decisions.message_id unique index plus 4 POC
        # teams (Orders, Quotes, Cable, Specialized). Re-runs are no-ops if
        # the rows already exist. See services/ai_dispatcher_seed.py.
        try:
            from services.ai_dispatcher_seed import bootstrap_ai_dispatcher
            await bootstrap_ai_dispatcher(db)
        except Exception as e:
            logger.warning(f"AI Dispatcher bootstrap warning (non-fatal): {e}")

        # Auto-seed admin account if none exists.
        admin_email = os.getenv("ADMIN_EMAIL")
        admin_password = os.getenv("ADMIN_PASSWORD")
        # PR cleanup A7. Reject the .env.example placeholder so a
        # copy paste deploy that forgot to fill ADMIN_PASSWORD in
        # cannot create an admin user with a guessable password.
        if admin_email == "REPLACE_ME_BEFORE_DEPLOY" or admin_password == "REPLACE_ME_BEFORE_DEPLOY":
            logger.error(
                "ADMIN_EMAIL or ADMIN_PASSWORD is still the .env.example placeholder. "
                "Set a real value before boot."
            )
            raise ValueError(
                "ADMIN_EMAIL or ADMIN_PASSWORD is still the placeholder REPLACE_ME_BEFORE_DEPLOY"
            )
        if admin_email and admin_password:
            existing_admin = await db.users.find_one({"role": "admin"}, {"_id": 1})
            if not existing_admin:
                import uuid
                from auth_helper import hash_password
                from datetime import datetime, timezone
                from seeds.seed_tenants import SIMPLIMETA_ROOT_TENANT_ID

                user_id = f"user_{uuid.uuid4().hex[:12]}"
                await db.users.insert_one({
                    "user_id": user_id,
                    "email": admin_email.lower(),
                    "name": os.getenv("ADMIN_NAME", "Admin"),
                    "role": "admin",
                    "tenant_id": SIMPLIMETA_ROOT_TENANT_ID,
                    "account_id": None,
                    "picture": None,
                    "is_active": True,
                    "is_email_verified": True,
                    "password_hash": hash_password(admin_password),
                    "zoho_contact_id": None,
                    "is_test_data": False,
                    "created_at": datetime.now(timezone.utc),
                })
                logger.info(f"Auto-initialized admin account: {admin_email}")
            else:
                logger.info("Admin account already exists, skipping initialization")
        else:
            logger.warning("ADMIN_EMAIL and ADMIN_PASSWORD not set - admin auto-initialization skipped")

    # Permission DAG validation is a fail-fast safety check, not a seed
    # step. It must run on every boot - including the SKIP_STARTUP_SEEDS=true
    # path used when developing against a live Atlas database - so that a
    # corrupt 'implies' graph (cycles, dangling refs) is detected at
    # startup instead of producing silently-wrong RBAC decisions later.
    try:
        from services.permission_registry import validate_dag
        await validate_dag(db)
    except Exception as e:
        logger.error(
            "Permission DAG validation failed at startup: %s",
            e,
        )
        raise

    # Register ingestion event-bus listeners. Import side-effect only -
    # the module's import wires handlers onto the event bus.
    #
    # Deliberately NOT wrapped in try/except. Ingestion is not optional
    # in any deploy mode, so a broken import is a hard error: better to
    # crash startup loudly than to silently drop every ingestion event
    # for the process lifetime while the server appears healthy.
    import services.ingestion.event_listeners  # noqa: F401

    # ------------------------------------------------------------
    # MongoDB index creation
    # ------------------------------------------------------------
    if SKIP_STARTUP_INDEXES:
        logger.info("Skipping MongoDB startup index creation because SKIP_STARTUP_INDEXES=true")
    else:
        try:
            await db.users.create_index("email", unique=True)
            await db.users.create_index("user_id", unique=True)
            await db.users.create_index("role")
            await db.user_sessions.create_index("session_token", unique=True)
            await db.user_sessions.create_index("user_id")
            await db.user_sessions.create_index("expires_at", expireAfterSeconds=0)
            await db.accounts.create_index("account_id", unique=True)
            await db.accounts.create_index("zoho_account_id", sparse=True)
            await db.deals.create_index("deal_id", unique=True)
            await db.deals.create_index("account_id")
            await db.deals.create_index("deal_owner_id")
            await db.deals.create_index("assigned_sales_rep_id", sparse=True)
            await db.deals.create_index("zoho_deal_id", sparse=True)
            await db.commissions.create_index("commission_id", unique=True)
            await db.commissions.create_index("deal_id")
            await db.commissions.create_index("user_id")
            await db.products.create_index("product_id", unique=True)
            await db.products.create_index("product_type")
            await db.invoices.create_index("invoice_id", unique=True)
            await db.invoices.create_index("account_id")
            await db.invoices.create_index("invoice_number", sparse=True)
            await db.contracts.create_index("contract_id", unique=True)
            await db.contracts.create_index("account_id")
            await db.field_jobs.create_index("job_id", unique=True)
            await db.field_jobs.create_index("account_id")
            await db.chat_sessions.create_index("session_id", unique=True)
            await db.chat_sessions.create_index("user_id")
            await db.chat_messages.create_index("session_id")
            await db.roles.create_index("name", unique=True)
            await db.password_reset_tokens.create_index("token")
            await db.password_reset_tokens.create_index("email")
            # Tenants — Phase 1A multi-tenancy
            await db.tenants.create_index("tenant_id", unique=True)
            await db.tenants.create_index("slug", unique=True)
            await db.tenants.create_index("parent_tenant_id", sparse=True)
            await db.tenants.create_index("tenant_type")
            await db.tenants.create_index("status")
            # Users gain tenant scoping
            await db.users.create_index("tenant_id", sparse=True)
            await db.users.create_index([("tenant_id", 1), ("email", 1)])

            # AI Employee catalog + entitlements — Phase 1B.1
            #
            # ai_employee_definitions: tenant-agnostic catalog. Querying by
            # type and is_published is the common catalog-list path; querying
            # by is_active when surfacing SimpliMeta admin views.
            await db.ai_employee_definitions.create_index("definition_id", unique=True)
            await db.ai_employee_definitions.create_index("type")
            await db.ai_employee_definitions.create_index([("is_published", 1), ("is_active", 1)])
            # tenant_app_assignments: a tenant can hold at most one assignment
            # per definition (revocations are status transitions, not deletes,
            # so this enforces idempotent grant semantics). Lookups by tenant
            # (catalog filtering) and by definition (impact analysis on
            # blueprint changes) are both first-class.
            await db.tenant_app_assignments.create_index("assignment_id", unique=True)
            await db.tenant_app_assignments.create_index(
                [("tenant_id", 1), ("definition_id", 1)], unique=True
            )
            await db.tenant_app_assignments.create_index("tenant_id")
            await db.tenant_app_assignments.create_index("definition_id")
            await db.tenant_app_assignments.create_index("status")

            # Phase 1E.1 — Tenant app grants (entitlements for ingested apps).
            # Same uniqueness convention as tenant_app_assignments: one grant
            # per (tenant_id, app_id) pair, revocations are status transitions
            # on the permanent row. Lookups by tenant (sidebar render in 1E.2)
            # and by app (impact analysis when an app is archived) are both
            # first-class.
            await db.tenant_app_grants.create_index("grant_id", unique=True)
            await db.tenant_app_grants.create_index(
                [("tenant_id", 1), ("app_id", 1)], unique=True
            )
            await db.tenant_app_grants.create_index("tenant_id")
            await db.tenant_app_grants.create_index("app_id")
            await db.tenant_app_grants.create_index("status")

            # AI Employee instance indexes — Phase 1B.2
            #
            # The ai_employees collection has been queried by employee_id since
            # day one but never had an index on it. 1B.2 corrects that with a
            # unique constraint (we have verified no duplicates across
            # localhost/dev/prod). If this index creation ever fails on
            # startup, that indicates a duplicate slipped in and needs
            # investigation before the migration in 1B.4.
            #
            # The three linkage indexes are sparse: pre-1B.4 rows do not
            # carry these fields (see routes/ai_employees.py — the keys are
            # conditionally persisted). Once the 1B.4 backfill stamps every
            # row with at least tenant_id, the sparseness becomes moot but
            # costs nothing to retain.
            #
            # (tenant_id, status) supports the most common upcoming query
            # shape: "list active AI Employee instances for tenant X".
            await db.ai_employees.create_index("employee_id", unique=True)
            await db.ai_employees.create_index("tenant_id", sparse=True)
            await db.ai_employees.create_index(
                [("tenant_id", 1), ("status", 1)], sparse=True
            )
            await db.ai_employees.create_index("definition_id", sparse=True)
            await db.ai_employees.create_index("assignment_id", sparse=True)

            try:
                index_info = await db.oauth_tokens.index_information()
                if "provider_1" in index_info and index_info["provider_1"].get("unique"):
                    await db.oauth_tokens.drop_index("provider_1")
            except Exception as e:
                logger.warning(f"Failed to drop stale provider_1 index: {e}")

            await db.oauth_tokens.create_index([("provider", 1), ("user_id", 1)], unique=True, sparse=True)
            await db.oauth_tokens.create_index("provider")
            await db.oauth_states.create_index("state")
            await db.oauth_states.create_index("created_at", expireAfterSeconds=600)
            await db.audit_logs.create_index([("timestamp", -1)])
            await db.audit_logs.create_index("action")
            await db.audit_logs.create_index("actor_id")
            await db.audit_logs.create_index("event_category")
            await db.audit_logs.create_index("event_type")
            await db.audit_logs.create_index("severity")
            await db.audit_logs.create_index("outcome")
            await db.audit_logs.create_index([("event_category", 1), ("timestamp", -1)])
            await db.audit_logs.create_index([("event_type", 1), ("timestamp", -1)])
            await db.audit_logs.create_index([("actor_id", 1), ("timestamp", -1)])
            await db.audit_logs.create_index([("actor_email", 1), ("timestamp", -1)])
            await db.audit_logs.create_index([("resource_type", 1), ("resource_id", 1), ("timestamp", -1)])

            # qbo_sync_requests — AEGISAXIOM-side orchestration record for
            # the n8n-driven QBO ingestion. Indexes mirror the access
            # patterns in routes/qbo_sync.py:
            #   * sync_request_id  — exact lookup (callback + status)
            #   * created_at desc  — "latest" probe
            #   * status           — running/queued filter
            #   * requested_by_user_id — admin per-user view
            #   * finished_at desc — freshness signal
            await db.qbo_sync_requests.create_index("sync_request_id", unique=True)
            await db.qbo_sync_requests.create_index([("created_at", -1)])
            await db.qbo_sync_requests.create_index("status")
            await db.qbo_sync_requests.create_index("requested_by_user_id")
            await db.qbo_sync_requests.create_index([("finished_at", -1)])
            # Partial unique index for the atomic active-sync guard.
            # acquire_sync_slot inserts a record carrying
            # is_active_lock="global"; this index ensures at most one
            # such record can exist at a time, so two concurrent
            # trigger calls cannot both start a sync. release_sync_slot
            # $unsets the field on terminal status transitions so the
            # next sync can claim the slot. See qbo_sync_service.py.
            await db.qbo_sync_requests.create_index(
                "is_active_lock",
                unique=True,
                partialFilterExpression={"is_active_lock": "global"},
                name="qbo_sync_active_lock_partial_unique",
            )

            # nova_export_artifacts — chat-native finance export
            # metadata. Indexes were previously created on every
            # POST/persist call from routes/nova_exports.py and from
            # the create_finance_export tool dispatcher; moving them
            # here keeps the export hot path free of the per-request
            # ensure_indexes round-trip.
            from services.nova_engine.nova_export_storage import (
                ensure_indexes as _ensure_nova_export_indexes,
            )
            await _ensure_nova_export_indexes(db)

            await db.projects.create_index("project_id", unique=True)
            await db.projects.create_index("contract_id")
            await db.projects.create_index("account_id")
            await db.projects.create_index("pm_id")
            await db.projects.create_index("project_type")
            await db.projects.create_index("status")
            await db.projects.create_index("template_id")

            await db.time_entries.create_index("entry_id", unique=True)
            await db.time_entries.create_index("project_id")
            await db.time_entries.create_index("contract_id")
            await db.time_entries.create_index("user_id")
            await db.time_entries.create_index("week_start")
            await db.time_entries.create_index("status")
            await db.time_entries.create_index("account_id")
            await db.time_entries.create_index([("user_id", 1), ("week_start", 1)])

            await db.pm_tasks.create_index("task_id", unique=True)
            await db.pm_tasks.create_index("project_id")
            await db.pm_tasks.create_index("parent_task_id")
            await db.pm_tasks.create_index("assignee_id")
            await db.pm_sprints.create_index("sprint_id", unique=True)
            await db.pm_sprints.create_index("project_id")
            await db.pm_waves.create_index("wave_id", unique=True)
            await db.pm_waves.create_index("project_id")
            await db.pm_threads.create_index("thread_id", unique=True)
            await db.pm_threads.create_index("wave_id")
            await db.pm_threads.create_index("owner_id")
            await db.pm_blocker_tickets.create_index("blocker_ticket_id", unique=True)
            await db.pm_blocker_tickets.create_index("thread_id")
            await db.pm_templates.create_index("template_id", unique=True)
            await db.pm_templates.create_index("project_type")

            await db.dashboards.create_index("dashboard_id", unique=True)
            await db.dashboards.create_index([("user_id", 1), ("order", 1)])
            await db.capacity_health_rules.create_index("rule_id", unique=True)

            await db.policies.create_index("policy_id", unique=True)
            await db.policies.create_index([("module", 1), ("entity", 1), ("enabled", 1)])
            await db.policies.create_index("deleted_at")

            await db.ai_usage.create_index([("created_at", -1)])
            await db.ai_usage.create_index("user_id")
            await db.ai_usage.create_index("agent")
            await db.ai_usage.create_index("model")
            await db.ai_model_config.create_index("key", unique=True)
            await db.ai_model_pricing.create_index("model", unique=True)

            await db.opportunities.create_index("tracking_number", unique=True, sparse=True)

            await db.ai_employee_knowledge.create_index([("employee_id", 1), ("module_number", 1)])
            await db.ai_employee_knowledge.create_index([("employee_id", 1), ("tags", 1)])
            await db.ai_employee_knowledge.create_index("knowledge_id", unique=True)
            await db.ai_employee_user_state.create_index([("user_id", 1), ("employee_id", 1)], unique=True)
            await db.ai_chat_artifacts.create_index("artifact_id", unique=True)
            await db.ai_chat_artifacts.create_index([("session_id", 1), ("status", 1)])
            await db.ai_chat_artifacts.create_index([("user_id", 1), ("created_at", -1)])
            await db.certifications.create_index([("user_id", 1), ("certification_type", 1)])
            await db.certifications.create_index("certification_id", unique=True)

            await db.chat_uploads.create_index([("user_id", 1), ("session_id", 1)])
            await db.chat_uploads.create_index("upload_id", unique=True)
            await db.chat_uploads.create_index("created_at", expireAfterSeconds=30 * 24 * 3600)

            await db.vega_discovery_sessions.create_index([("user_id", 1), ("status", 1)])
            await db.vega_discovery_sessions.create_index("session_id", unique=True)
            await db.vega_discovery_sessions.create_index("opportunity_name")
            await db.vega_discovery_sessions.create_index("chat_session_id")
            await db.vega_discovery_sessions.create_index([("user_id", 1), ("discovery_mode", 1)])

            await db.vega_events.create_index("event_id", unique=True)
            await db.vega_events.create_index([("session_id", 1), ("created_at", 1)])
            await db.vega_events.create_index([("user_id", 1), ("created_at", -1)])
            await db.vega_events.create_index([("event_type", 1), ("created_at", -1)])
            await db.vega_events.create_index("created_at", expireAfterSeconds=90 * 24 * 3600)

            # Vega v3 — versioned artifact storage (Wave 5 / Thread 5a).
            # Each row is one immutable version of a discovery_analysis_v3
            # artifact. Counters back persist_artifact_version's atomic
            # find_one_and_update allocation. 90-day TTL keeps storage
            # bounded — only the latest version is read by default.
            await db.vega_artifact_versions.create_index(
                [("artifact_id", 1), ("version", 1)], unique=True,
            )
            await db.vega_artifact_versions.create_index(
                [("artifact_id", 1), ("created_at", -1)],
            )
            await db.vega_artifact_versions.create_index(
                "created_at", expireAfterSeconds=90 * 24 * 3600,
            )
            await db.vega_artifact_version_counters.create_index(
                "artifact_id", unique=True,
            )

            await db.bpmn_diagrams.create_index("bpmn_id", unique=True)
            await db.bpmn_diagrams.create_index("workflow_id")
            await db.bpmn_diagrams.create_index([("created_by", 1), ("created_at", -1)])

            await db.app_registry.create_index("app_id", unique=True)
            await db.app_registry.create_index("slug", unique=True)
            await db.app_registry.create_index("status")
            await db.app_registry.create_index("source_repo_url")
            await db.app_modules.create_index("module_id", unique=True)
            await db.app_modules.create_index([("app_id", 1), ("name", 1)], unique=True)
            await db.app_modules.create_index("app_id")
            await db.app_modules.create_index("permission_prefix")
            await db.app_ingestion_log.create_index("app_id")
            await db.app_ingestion_log.create_index([("app_id", 1), ("created_at", -1)])
            await db.ingestion_artifacts.create_index([("app_id", 1), ("artifact_type", 1), ("version", 1)], unique=True)
            await db.ingestion_field_catalogue.create_index([("app_id", 1), ("module_name", 1), ("version", 1)], unique=True)
            await db.ingestion_state.create_index([("app_id", 1), ("ingestion_version", 1)], unique=True)

            # Nova query optimization — covers tx_date+basis range scans used
            # by load_revenue_history, load_gross_profit_history,
            # query_finance_data, detect_wholesale_history_from_gl, and
            # get_latest_tx_date. Without this, every Nova GL aggregate is a
            # full collection scan (~35k rows). Ref: nova_engine performance
            # audit 2026-05-06.
            #
            # Names + key specs MUST match the indexes already created on the
            # live DB during the audit, otherwise create_index throws
            # IndexOptionsConflict on startup. create_index is a no-op when
            # the spec matches.
            #
            # `db` resolves to database.get_db() (the app DB). General-ledger
            # collections are co-located on the app DB in production
            # (services/nova_engine/db.py:get_qbo_db() defaults to
            # database.get_db() unless QBO_MONGO_URL is set). If QBO
            # collections ever move to a separate cluster (QBO_MONGO_URL set),
            # switch this block to use qbo_db = get_qbo_db().
            await db.general_ledger.create_index(
                [("tx_date", 1), ("basis", 1)],
                name="nova_tx_date_basis",
                background=True,
            )
            # Nova MRR query optimization — covers load_mrr_history which
            # filters by account_id prefix + tx_date range.
            await db.general_ledger.create_index(
                [("account_id", 1), ("tx_date", 1), ("basis", 1)],
                name="nova_account_tx_basis",
                background=True,
            )

            await db.projection_runs.create_index("projection_run_id", unique=True)
            await db.projection_runs.create_index("user_id")
            await db.projection_runs.create_index([("user_id", 1), ("created_at", -1)])
            await db.projection_runs.create_index("session_id", sparse=True)
            await db.projection_runs.create_index("inputs_hash")

            # Optimization for customer_deal_finance._aggregate_revenue and
            # finance_snapshots aging — both filter by kind+sync_status.
            # Ref: nova_engine performance audit 2026-05-06.
            await db.finance_drafts.create_index(
                [("kind", 1), ("sync_status", 1)],
                name="nova_kind_sync_status",
                background=True,
            )

            await db.finance_snapshots.create_index("snapshot_id", unique=True)
            await db.finance_snapshots.create_index([("tenant_id", 1), ("created_at", -1)])
            # Unique on (tenant_id, snapshot_date) enforces the
            # daily-upsert cap: hourly refreshes within the same UTC
            # day overwrite the same document instead of accumulating
            # ~24 redundant rows per tenant per day.
            await db.finance_snapshots.create_index(
                [("tenant_id", 1), ("snapshot_date", -1)],
                unique=True,
            )

            await db.policy_targets.create_index(
                [("app_id", 1), ("module", 1), ("entity", 1), ("event", 1)],
                unique=True,
            )
            await db.policy_targets.create_index([("app_id", 1), ("is_active", 1)])
            await db.policy_targets.create_index([("module", 1), ("entity", 1), ("is_active", 1)])

            logger.info("MongoDB indexes ensured")
        except Exception as e:
            logger.warning(f"Index creation warning (non-fatal): {e}")

    # Marketplace sync reads AI employee documents and registers them in memory.
    # It is not a seed. Keep it enabled so emp_ai_vega and other marketplace
    # agents are available when the DB user can read ai_employees.
    try:
        from services.ai.agents.marketplace import sync_marketplace_agents
        await sync_marketplace_agents(db)
    except Exception as e:
        logger.warning(f"Marketplace sync warning (non-fatal): {e}")

    # ------------------------------------------------------------
    # Optional AI employee seed
    # ------------------------------------------------------------
    if os.environ.get("SEED_AI_EMPLOYEES") == "true" and not SKIP_STARTUP_SEEDS:
        try:
            from seeds.seed_arc import seed_arc
            await seed_arc(db)
            logger.info("ARC curriculum seeded")
        except Exception as e:
            logger.warning(f"ARC seed warning (non-fatal): {e}")
        try:
            from seeds.seed_vega import seed_vega
            await seed_vega(db)
            logger.info("Vega knowledge base seeded")
        except Exception as e:
            logger.warning(f"Vega seed warning (non-fatal): {e}")
        try:
            from seeds.seed_orion import seed_orion
            await seed_orion(db)
            logger.info("Orion knowledge base seeded")
        except Exception as e:
            logger.warning(f"Orion seed warning (non-fatal): {e}")
        try:
            from seeds.seed_pmo import seed_pmo
            await seed_pmo(db)
            logger.info("PMO Assistant seeded")
        except Exception as e:
            logger.warning(f"PMO Assistant seed warning (non-fatal): {e}")
    elif os.environ.get("SEED_AI_EMPLOYEES") == "true":
        logger.info("Skipping AI employee seeds because SKIP_STARTUP_SEEDS=true")

    # ------------------------------------------------------------
    # Optional MATCH dev seed — pre-fills the MATCH settings singleton
    # and a canonical Nextiva (dev) provider template so operators do
    # not have to paste tokens / webhook URLs on every fresh stack.
    # Separate flag from SEED_AI_EMPLOYEES because MATCH is data-only,
    # not an AI employee. NEVER enable in production — defaults include
    # a local-only AppDirect bearer.
    # ------------------------------------------------------------
    if os.getenv("MATCH_DEV_SEED", "").lower() == "true":
        try:
            from seeds.seed_match_dev import seed_match_dev
            await seed_match_dev(db)
            logger.info("MATCH dev seed: complete")
        except Exception as e:
            logger.warning(f"MATCH dev seed failed (non-blocking): {e}")

    # ------------------------------------------------------------
    # AI schema discovery / pricing seed
    # ------------------------------------------------------------
    if SKIP_STARTUP_SEEDS:
        logger.info("Skipping AI schema discovery and pricing seed because SKIP_STARTUP_SEEDS=true")
    else:
        try:
            from services.ai.schema import discover as discover_schema
            await discover_schema(db)
        except Exception as e:
            logger.warning(f"AI schema discovery warning (non-fatal): {e}")

        try:
            default_pricing = [
                {"model": "gemini-2.0-flash", "input_per_million": 0.10, "output_per_million": 0.40},
                {"model": "gemini-2.5-flash", "input_per_million": 0.30, "output_per_million": 2.50},
                {"model": "gemini-2.5-flash-lite", "input_per_million": 0.10, "output_per_million": 0.40},
                {"model": "gemini-2.5-pro", "input_per_million": 1.25, "output_per_million": 10.00},
                {"model": "gemini-3-flash-preview", "input_per_million": 0.50, "output_per_million": 3.00},
                {"model": "gemini-3.1-pro-preview", "input_per_million": 2.00, "output_per_million": 12.00},
                {"model": "gemini-3.1-flash-lite-preview", "input_per_million": 0.25, "output_per_million": 1.50},
            ]
            for p in default_pricing:
                await db.ai_model_pricing.update_one(
                    {"model": p["model"]},
                    {"$setOnInsert": p},
                    upsert=True,
                )
            logger.info("AI model pricing defaults seeded")
        except Exception as e:
            logger.warning(f"AI pricing seed warning (non-fatal): {e}")

    # ------------------------------------------------------------
    # Background jobs / schedulers
    # ------------------------------------------------------------
    if DISABLE_BACKGROUND_JOBS:
        logger.info("Skipping background jobs because DISABLE_BACKGROUND_JOBS=true")
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from services.zoho_service import ZohoService

    _sync_lock = False

    async def _hourly_zoho_sync():
        nonlocal _sync_lock
        if _sync_lock:
            logger.warning("[Hourly Sync] Skipped - previous sync still running")
            return
        _sync_lock = True
        try:
            db = database.get_db()
            svc = ZohoService()
            acc_new, acc_updated = await svc.sync_accounts_from_zoho(db)
            deal_synced, deal_updated, deal_accts = await svc.sync_deals_from_zoho(db)
            comm_count = await svc.sync_commission_data(db)
            enriched_count = await svc.sync_deals_enriched(db)
            logger.info(
                f"[Hourly Sync] Accounts: {acc_new} new / {acc_updated} updated | "
                f"Deals: {deal_synced} new / {deal_updated} updated | "
                f"Commissions: {comm_count} | Enriched Deals: {enriched_count}"
            )
        except Exception as e:
            logger.warning(f"[Hourly Sync] Skipped (Zoho not connected or error): {e}")
        finally:
            _sync_lock = False

    scheduler = AsyncIOScheduler()
    scheduler.add_job(_hourly_zoho_sync, "interval", minutes=15, id="zoho_15min_sync")

    from services.fs_notifications import run_day_before_reminders
    scheduler.add_job(run_day_before_reminders, "cron", hour=8, minute=0, id="fs_day_before_reminders")

    # Nova finance snapshots: hourly so AR/AP totals Nova quotes never lag
    # by more than ~60 minutes behind finance_drafts (formerly ar_invoices / ap_bills
    # before the Wave 2D migration), plus a startup priming run so the first
    # chat turn after a deploy doesn't miss data.
    from datetime import datetime as _now_dt, timezone as _now_tz
    from services.nova_engine.finance_snapshots import refresh_finance_snapshots
    scheduler.add_job(
        refresh_finance_snapshots,
        "interval",
        hours=1,
        id="nova_finance_snapshot",
        next_run_time=_now_dt.now(_now_tz.utc),
    )

    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("APScheduler started - Zoho sync every 15 minutes, FS reminders daily at 8 AM")

    # ---- Workflow Rules Engine ----
    try:
        from services.event_bus import get_event_bus
        from services.workflow_engine import handle_event
        from services.workflow_scheduler import start_workflow_scheduler
        from routes.workflow_rules import set_scheduler_ref

        event_bus = get_event_bus()
        event_bus.subscribe(handle_event)

        set_scheduler_ref(scheduler)
        await start_workflow_scheduler(scheduler)

        if SKIP_STARTUP_INDEXES:
            logger.info("Skipping workflow-rule index creation because SKIP_STARTUP_INDEXES=true")
        else:
            db = database.get_db()
            await db.workflow_rules.create_index([("module", 1), ("enabled", 1), ("deleted_at", 1)])
            await db.workflow_rules.create_index([("trigger.type", 1)])
            await db.workflow_rule_executions.create_index([("rule_id", 1), ("triggered_at", -1)])
            await db.workflow_rule_executions.create_index([("status", 1)])
            await db.notifications.create_index([("user_id", 1), ("read", 1), ("created_at", -1)])
            await db.hardware_material_requests.create_index([("opportunity_id", 1), ("status", 1)])
            await db.hardware_material_requests.create_index([("distributor_id", 1), ("status", 1)])
            await db.hardware_material_requests.create_index([("status", 1), ("needed_by_date", 1)])
            await db.hardware_material_requests.create_index([("request_id", 1)], unique=True)
            await db.hardware_material_requests.create_index([("deleted_at", 1), ("status", 1)])
            await db.hardware_material_requests.create_index([("deleted_at", 1), ("opportunity_id", 1)])
            await db.hardware_material_requests.create_index([("deleted_at", 1), ("distributor_id", 1)])
            await db.distributors.create_index([("name", 1)], unique=True)
            await db.distributors.create_index([("deleted_at", 1), ("status", 1)])
        logger.info("Workflow rules engine initialized")
    except Exception as e:
        logger.warning(f"Workflow rules engine startup failed (non-fatal): {e}")


@app.on_event("shutdown")
async def shutdown_db_client():
    if hasattr(app.state, "scheduler"):
        app.state.scheduler.shutdown(wait=False)
    client = database.get_client()
    if client:
        client.close()
    # Phase 0 (Postgres re-platform): dispose the async SQLAlchemy engine if one
    # was ever created. Imported locally and lazy — this is a no-op when Postgres
    # was never touched (the entire Mongo-only Phase 0 run), so it cannot connect
    # or fail here. See db/session.py and postgres-migration-plan.md §A1.
    from db.session import dispose_engine
    await dispose_engine()