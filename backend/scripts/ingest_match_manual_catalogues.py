"""
One-shot helper: inserts hand-built field catalogues for the MATCH ingestion
that the analyzer cannot derive from source (Row has no Pydantic class, Settings
needs the folded Connection sub-block, AppDirectCall is a private collection
written by _write_call_log).

Run inside the aegisaxiom-ingestion-backend container. Idempotent (upserts by
app_id + module + version).
"""
import asyncio
import os
import sys

sys.path.insert(0, "/app/backend")

import database
database.init(os.environ["MONGO_URL"], os.environ["DB_NAME"])

from services.ingestion import repository as repo
from services.ingestion.models import IngestionFieldCatalogue, FieldDefinition, ApiRoute

APP_ID = "app_a984d000d748"


def F(name: str, type_: str, *, label: str | None = None, description: str | None = None,
      options=None, computed: bool = False, source_field_name: str | None = None) -> FieldDefinition:
    return FieldDefinition(
        name=name,
        type=type_,
        label=label or name.replace("_", " ").title(),
        description=description or f"The {name.replace('_', ' ')} of the entity",
        computed=computed,
        options=options,
        source_field_name=source_field_name or name,
    )


# Row — derived from rows.py + webhooks.py write paths. Schemaless in source
# (no Pydantic class) so we re-state the persisted shape explicitly.
ROW_FIELDS = [
    F("id", "string", description="Row identifier (uuid)"),
    F("session_id", "string", description="FK to session.id"),
    F("row_index", "number", description="0-based row position within the session's provider report"),
    F("source_row", "json", description="Raw provider report row as parsed from the CSV"),
    F("output", "json", description="Contract H-shaped matched output (top_level/order_details/order_attributes/milestones/service_attributes)"),
    F("audit_metadata", "json", description="Matching audit: confidence_score, matched_candidate_id, candidates_considered, flags, rules_applied, llm_disambiguation"),
    F("recommended_status", "string", description="n8n's recommendation", options=[
        {"value": "ready_to_draft", "label": "Ready To Draft"},
        {"value": "needs_review", "label": "Needs Review"},
        {"value": "error", "label": "Error"},
    ]),
    F("status", "string", description="Current human-review status", options=[
        {"value": "ready", "label": "Ready (initial)"},
        {"value": "ready_to_draft", "label": "Ready To Draft"},
        {"value": "needs_review", "label": "Needs Review"},
        {"value": "approved", "label": "Approved"},
        {"value": "edited", "label": "Edited"},
        {"value": "rejected", "label": "Rejected"},
        {"value": "escalated", "label": "Escalated"},
        {"value": "create_new", "label": "Create New Customer"},
        {"value": "create_new_order", "label": "Create New Order"},
        {"value": "error", "label": "Error"},
    ]),
    F("review_reason", "string", description="Human-readable explanation for needs_review/error states"),
    F("escalation_email_draft", "string", description="Email draft prepared when address discrepancy is flagged"),
    F("flags", "list", description="Match flags from n8n: new_customer, name_variation, address_discrepancy, llm_unavailable, etc."),
    F("callback_url", "string", description="Callback URL n8n used for this row (echoed back on write)"),
    F("pushed_to_appdirect", "boolean", description="Whether the approved row has been pushed to AppDirect"),
    F("appdirect_order_id", "string", description="AppDirect's order id once successfully pushed"),
    F("created_at", "date", description="When this row was first written by n8n"),
    F("updated_at", "date", description="Last mutation timestamp"),
]

# Settings — singleton with the folded n8n connection sub-block. Each nested
# block is registered as a `json` field so the policy engine treats them as
# opaque per-block; individual key edits surface as JSON-path policy targets.
SETTINGS_FIELDS = [
    F("mode", "string", description="Ingestion entry-point mode", options=[
        {"value": "email_forwarding", "label": "Email Forwarding"},
        {"value": "manual_upload", "label": "Manual Upload"},
        {"value": "api_push", "label": "API Push"},
    ]),
    F("forward_rules", "list", description="Email-forwarding rules: {match_pattern, provider, template_id, action}"),
    F("crm", "json", description="CRM data-source config: connection_mode (snapshot/live_sql/live_api), sql.{server,database,port,auth_mode,service_user,service_password[encrypted],read_tables}, api.{base_url,api_key[encrypted],customers_endpoint}"),
    F("schedule", "json", description="Run scheduling: mode (immediate/daily_batch/manual_approval), daily_batch_time, timezone, auto_approve_threshold, reviewer_threshold, escalation_email"),
    F("appdirect", "json", description="AppDirect connection: base_url, bearer_token [encrypted], multipass_enabled, live_post_enabled, defaults (enum ID overrides), last_test"),
    F("n8n", "json", description="n8n webhook config (folded from ConnectionConfig): trigger_webhook_url, magic_mapping_webhook_url, reviewer_action_webhook_url, ping_webhook_url, shared_auth_token [encrypted, used in BOTH directions], callback_url [auto-derived per deploy]"),
    F("updated_at", "date", description="Last settings mutation"),
]

# AppDirectCall — read-only log written by _write_call_log in appdirect.py.
# Surfaced in the Board drawer's Push History sub-section.
APPDIRECT_CALL_FIELDS = [
    F("id", "string", description="Log id (uuid)"),
    F("row_id", "string", description="FK to row.id this call was made for"),
    F("session_id", "string", description="FK to session.id"),
    F("endpoint", "string", description="AppDirect endpoint URL the call targeted"),
    F("request_body", "json", description="Full outbound request body sent to AppDirect"),
    F("request_headers_redacted", "json", description="Outbound headers with Authorization replaced by a sentinel"),
    F("response_status", "number", description="HTTP status from AppDirect (null on dry_run / network error)"),
    F("response_body", "json", description="AppDirect response body (null on dry_run / network error)"),
    F("latency_ms", "number", description="Round-trip latency in ms (null on dry_run)"),
    F("error", "string", description="Error message when the call did not produce a normal response"),
    F("mode", "string", description="Call mode", options=[
        {"value": "dry_run", "label": "Dry Run"},
        {"value": "live", "label": "Live"},
    ]),
    F("actor", "string", description="User or system actor that triggered the push"),
    F("created_at", "date", description="When the call attempt was recorded"),
]


def R(method: str, path: str, operation: str, entity: str) -> ApiRoute:
    return ApiRoute(method=method, path=path, operation=operation, entity=entity)


# api_routes synthesis — drives which CRUD perms /rbac/generate emits.
# We use the *semantic* operation that matches each MATCH endpoint even when
# the source uses POST for state mutation (row_action) — the generator only
# inspects the `operation` field, so labeling it "update" is the right way
# to surface a `.edit` permission for that mutation.

ROW_ROUTES = [
    R("GET",    "/api/rows/{row_id}",                  "get",    "row"),
    R("GET",    "/api/sessions/{session_id}/rows",     "list",   "row"),  # source has list-by-session
    R("POST",   "/api/rows/{row_id}/action",           "update", "row"),  # row_action mutates state -> generator emits .edit + .change_status
    R("POST",   "/api/rows/bulk-approve",              "update", "row"),
    R("POST",   "/api/rows/{row_id}/push-to-appdirect","update", "row"),
    R("GET",    "/api/rows/{row_id}/export",           "get",    "row"),
]

SETTINGS_ROUTES = [
    R("GET",    "/api/settings",                       "get",    "settings"),
    R("PUT",    "/api/settings",                       "update", "settings"),  # deep-merge PUT
    R("POST",   "/api/settings/test-sql",              "custom", "settings"),
    R("POST",   "/api/settings/test-appdirect",        "custom", "settings"),
    R("GET",    "/api/settings/appdirect-lookups",     "get",    "settings"),
]

APPDIRECT_CALL_ROUTES = [
    R("GET",    "/api/rows/{row_id}/appdirect-calls",  "list",   "appdirect_call"),
]


async def main():
    catalogues = [
        IngestionFieldCatalogue(
            app_id=APP_ID, module_name="row", entity_name="row", version=1,
            fields=ROW_FIELDS, api_routes=ROW_ROUTES,
        ),
        IngestionFieldCatalogue(
            app_id=APP_ID, module_name="settings", entity_name="settings", version=1,
            fields=SETTINGS_FIELDS, api_routes=SETTINGS_ROUTES,
        ),
        IngestionFieldCatalogue(
            app_id=APP_ID, module_name="appdirect_call", entity_name="appdirect_call", version=1,
            fields=APPDIRECT_CALL_FIELDS, api_routes=APPDIRECT_CALL_ROUTES,
        ),
    ]
    for cat in catalogues:
        await repo.store_field_catalogue(cat)
        print(f"  stored: {cat.entity_name} ({len(cat.fields)} fields, {len(cat.api_routes)} routes)")


if __name__ == "__main__":
    asyncio.run(main())
