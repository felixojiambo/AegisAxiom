"""
App Ingestion — API Routes

Endpoints for managing app ingestions: initiate, analyze, extract fields,
register, activate, and query status. These endpoints support the
/ingest-app Claude Code skill and the admin review UI.
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List

from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from services.ingestion import service
from services.ingestion import repository as repo
from services.ingestion import rbac_generator
from services.field_registry import (
    get_fields, get_all_modules, get_entities_for_module,
    get_module_events, get_module_config,
)
from services.permission_registry import register_module_permissions

router = APIRouter()


# Register this module's system permissions so they're seeded into the
# permission registry on startup (same pattern as roles.py::_core_permissions).
@register_module_permissions("app_ingestion")
def _app_ingestion_permissions():
    return [
        {
            "key": "apps.permissions.edit",
            "group": "Admin",
            "label": "Edit ingested app permissions",
        },
    ]


# ==================== REQUEST MODELS ====================

class InitiateIngestionRequest(BaseModel):
    repo_url: str
    name: str
    slug: str
    description: str = ""
    account_id: Optional[str] = None


class RecordDecisionsRequest(BaseModel):
    selected_modules: List[dict]  # [{name, entity_name, replaces_module, ui_placement, ...}]
    discarded_modules: List[dict]  # [{name, reason}]
    import_plan: dict = {}


class ExtractRequest(BaseModel):
    clone_dir: str
    selected_entities: Optional[List[str]] = None


# ==================== ROUTES ====================

@router.post("/ingestions")
async def initiate_ingestion(req: InitiateIngestionRequest, current_user=Depends(get_current_user)):
    """Start a new app ingestion."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    try:
        result = await service.initiate_ingestion(
            repo_url=req.repo_url,
            name=req.name,
            slug=req.slug,
            description=req.description,
            account_id=req.account_id,
            created_by=current_user.user_id,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/ingestions")
async def list_ingestions(status: Optional[str] = None, current_user=Depends(get_current_user)):
    """List all ingested apps."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    return await repo.list_app_registrations(status)


@router.get("/ingestions/{app_id}")
async def get_ingestion(app_id: str, current_user=Depends(get_current_user)):
    """Get detailed status for an ingestion."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    try:
        return await service.get_ingestion_status(app_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/ingestions/{app_id}/clone")
async def clone_repo(app_id: str, current_user=Depends(get_current_user)):
    """Clone the source repo for an ingestion."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    try:
        return await service.clone_repo(app_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingestions/{app_id}/analyze")
async def analyze_repo(app_id: str, clone_dir: str, current_user=Depends(get_current_user)):
    """Run analysis and classification on a cloned repo."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    try:
        return await service.analyze_and_classify(app_id, clone_dir)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/ingestions/{app_id}/decisions")
async def record_decisions(app_id: str, req: RecordDecisionsRequest, current_user=Depends(get_current_user)):
    """Record developer decisions from the interview phase."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    try:
        return await service.record_developer_decisions(
            app_id=app_id,
            selected_modules=req.selected_modules,
            discarded_modules=req.discarded_modules,
            import_plan=req.import_plan,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/ingestions/{app_id}/extract")
async def extract_fields(app_id: str, req: ExtractRequest, current_user=Depends(get_current_user)):
    """Extract field catalogues from the analyzed source."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    try:
        return await service.extract_field_catalogue(
            app_id=app_id,
            clone_dir=req.clone_dir,
            selected_entities=req.selected_entities,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/ingestions/{app_id}/register")
async def register_fields(app_id: str, current_user=Depends(get_current_user)):
    """Register extracted fields into AEGISAXIOM field registry and create app_modules."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    try:
        return await service.register_fields_from_catalogue(app_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class ApproveIngestionRequest(BaseModel):
    pass  # No body required; actor comes from auth session


class RejectIngestionRequest(BaseModel):
    reason: str


class RerunStepRequest(BaseModel):
    step_id: str


@router.post("/ingestions/{app_id}/approve")
async def approve_ingestion(app_id: str, current_user=Depends(get_current_user)):
    """Approve a pending-review ingestion, advancing it to 'configured' state."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    try:
        return await service.approve_ingestion(app_id, performed_by=current_user.user_id)
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=409, detail=msg)


@router.post("/ingestions/{app_id}/reject")
async def reject_ingestion(app_id: str, req: RejectIngestionRequest, current_user=Depends(get_current_user)):
    """Reject a pending-review ingestion, archiving it with a reason."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    if not req.reason or not req.reason.strip():
        raise HTTPException(status_code=400, detail="A rejection reason is required")
    try:
        return await service.reject_ingestion(app_id, reason=req.reason.strip(), performed_by=current_user.user_id)
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=409, detail=msg)


# ─── Phase 1E.3a — Registry metadata patch endpoint ────────────────
#
# Lets admins set route_prefixes + permission_keys on an existing
# app_registry row. These fields drive the route-scoping middleware
# (1E.3b) and the permission auto-seeder (1E.3d). For the two apps
# live today (Projects + Finance) the backfill script populates
# them automatically; this endpoint is for the rare case of editing
# an existing app's surface or initialising a future app whose
# analyzer hasn't been taught to auto-extract these yet.
#
# Deliberately a PATCH-shaped POST (rather than PATCH verb) for
# consistency with the rest of this module — the other state-change
# endpoints here use POST + /verb suffixes. Keep the convention.

class UpdateRegistryMetadataRequest(BaseModel):
    """Body for /ingestions/{app_id}/registry-metadata.

    Both fields are optional — omitting one leaves it untouched.
    Sending an empty list explicitly clears that field. The endpoint
    fully replaces the list it touches; it does not append.
    """
    route_prefixes: Optional[List[str]] = None
    permission_keys: Optional[List[str]] = None


@router.post("/ingestions/{app_id}/registry-metadata")
async def update_registry_metadata(
    app_id: str,
    req: UpdateRegistryMetadataRequest,
    current_user=Depends(get_current_user),
):
    """Set route_prefixes and/or permission_keys on an app_registry row.

    Returns the updated row. 404 if the app doesn't exist. Gated by
    `admin.manage` since these fields control multi-tenancy routing
    and permissioning — they're security-sensitive metadata.
    """
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")

    if req.route_prefixes is None and req.permission_keys is None:
        raise HTTPException(
            status_code=400,
            detail="At least one of route_prefixes or permission_keys must be provided.",
        )

    # Normalise prefix entries: trim whitespace, ensure they start
    # with /api/ (consistent with how the middleware will match them
    # in 1E.3b). Bare /projects becomes /api/projects automatically.
    update_doc: dict = {}
    if req.route_prefixes is not None:
        cleaned = []
        for p in req.route_prefixes:
            if not isinstance(p, str):
                continue
            p = p.strip()
            if not p:
                continue
            if not p.startswith("/"):
                p = "/" + p
            if not p.startswith("/api/"):
                p = "/api" + p
            # Strip trailing slash for canonical form (the matcher
            # in 1E.3b will do prefix comparison, so /api/projects
            # and /api/projects/ should not be treated as different).
            p = p.rstrip("/")
            cleaned.append(p)
        # Deduplicate while preserving order.
        seen = set()
        deduped = []
        for p in cleaned:
            if p not in seen:
                seen.add(p)
                deduped.append(p)
        update_doc["route_prefixes"] = deduped

    if req.permission_keys is not None:
        cleaned_keys = []
        for k in req.permission_keys:
            if not isinstance(k, str):
                continue
            k = k.strip()
            if not k:
                continue
            cleaned_keys.append(k)
        seen = set()
        deduped_keys = []
        for k in cleaned_keys:
            if k not in seen:
                seen.add(k)
                deduped_keys.append(k)
        update_doc["permission_keys"] = deduped_keys

    updated = await repo.update_app_registry_metadata(app_id, update_doc)
    if not updated:
        raise HTTPException(status_code=404, detail="App not found")
    return updated


@router.post("/ingestions/{app_id}/rerun-step")
async def rerun_step(app_id: str, req: RerunStepRequest, current_user=Depends(get_current_user)):
    """Mark a pipeline step as resolved (or re-run it when supported)."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    try:
        return await service.rerun_pipeline_step(
            app_id, step_id=req.step_id, performed_by=current_user.user_id
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.post("/ingestions/{app_id}/activate")
async def activate_app(app_id: str, current_user=Depends(get_current_user)):
    """Activate a configured app."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    try:
        return await service.activate_app(app_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/ingestions/{app_id}/modules")
async def get_modules(app_id: str, current_user=Depends(get_current_user)):
    """Get all registered modules for an app."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    return await repo.get_app_modules(app_id)


@router.get("/ingestions/{app_id}/fields")
async def get_field_catalogues(app_id: str, current_user=Depends(get_current_user)):
    """Get the extracted field catalogues for an app."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    return await repo.get_field_catalogues(app_id)


@router.get("/ingestions/{app_id}/logs")
async def get_logs(app_id: str, current_user=Depends(get_current_user)):
    """Get ingestion audit logs."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    return await repo.get_ingestion_logs(app_id)


# ==================== RBAC ====================

class GenerateRBACRequest(BaseModel):
    source_roles: List[str] = []  # roles from the source app (e.g., ["admin", "project_manager", "engineer"])


class ApplyRBACRequest(BaseModel):
    permissions: List[dict]  # the approved permission list
    role_decisions: List[dict]  # [{action, name, display_name, permissions, existing_role_name}]


class LinkAIEmployeeRequest(BaseModel):
    employee_id: str
    permissions: List[str]  # which permission keys to grant


class AddAppPermissionRequest(BaseModel):
    key: str
    group: str
    label: str


class ReassignRolePermsRequest(BaseModel):
    perm_keys: List[str]


@router.post("/ingestions/{app_id}/rbac/generate")
async def generate_rbac(app_id: str, req: GenerateRBACRequest, current_user=Depends(get_current_user)):
    """Generate RBAC permissions and role suggestions for an ingested app."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")

    app = await repo.get_app_registry(app_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"App {app_id} not found")

    catalogues = await repo.get_field_catalogues(app_id)
    if not catalogues:
        raise HTTPException(status_code=400, detail="No field catalogues found. Run extraction first.")

    app_slug = app.get("app_slug", app["slug"].replace("-", "_"))
    permissions = rbac_generator.generate_permissions(app_slug, app["name"], catalogues)
    suggestions = rbac_generator.suggest_roles(app_slug, app["name"], permissions, req.source_roles)

    return {
        "permissions": permissions,
        "role_suggestions": suggestions,
        "total_permissions": len(permissions),
    }


@router.post("/ingestions/{app_id}/rbac/apply")
async def apply_rbac(app_id: str, req: ApplyRBACRequest, current_user=Depends(get_current_user)):
    """Apply developer-approved RBAC decisions."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    try:
        return await rbac_generator.apply_rbac(
            app_id=app_id,
            permissions=req.permissions,
            role_decisions=req.role_decisions,
            performed_by=current_user.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==================== Post-ingest RBAC editing ====================
#
# Admin endpoints for editing an ingested app's RBAC after the initial
# apply_rbac() call. Each endpoint sources `performed_by` from the
# authenticated session (current_user.user_id) — never from the request
# body — so audit-log attribution can't be forged by clients. The
# rbac_generator service writes the audit_logs entries; routes only gate
# access and translate ValueErrors into HTTP 4xx responses.

@router.post(
    "/ingestions/{app_id}/permissions",
    status_code=201,
    dependencies=[Depends(require_ai_permission("apps.permissions.edit"))],
)
async def add_app_permission_endpoint(
    app_id: str,
    req: AddAppPermissionRequest,
    current_user=Depends(get_current_user),
):
    """Register a new manual_override permission against an ingested app."""
    if not has_perm(current_user, "apps.permissions.edit"):
        raise HTTPException(
            status_code=403,
            detail="apps.permissions.edit permission required",
        )
    try:
        return await rbac_generator.add_app_permission(
            app_id=app_id,
            perm={"key": req.key, "group": req.group, "label": req.label},
            performed_by=current_user.user_id,
        )
    except ValueError as e:
        msg = str(e)
        # "App {id} not found" → 404; everything else (missing fields,
        # validation) → 400.
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.delete(
    "/ingestions/{app_id}/permissions/{key}",
    dependencies=[Depends(require_ai_permission("apps.permissions.edit"))],
)
async def remove_app_permission_endpoint(
    app_id: str,
    key: str,
    current_user=Depends(get_current_user),
):
    """Deactivate an app permission and pull it from any roles that hold it."""
    if not has_perm(current_user, "apps.permissions.edit"):
        raise HTTPException(
            status_code=403,
            detail="apps.permissions.edit permission required",
        )
    try:
        return await rbac_generator.remove_app_permission(
            app_id=app_id,
            key=key,
            performed_by=current_user.user_id,
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.put(
    "/ingestions/{app_id}/roles/{role_name}/permissions",
    dependencies=[Depends(require_ai_permission("apps.permissions.edit"))],
)
async def reassign_app_role_perms_endpoint(
    app_id: str,
    role_name: str,
    req: ReassignRolePermsRequest,
    current_user=Depends(get_current_user),
):
    """Replace a role's permissions for THIS app with exactly perm_keys."""
    if not has_perm(current_user, "apps.permissions.edit"):
        raise HTTPException(
            status_code=403,
            detail="apps.permissions.edit permission required",
        )
    try:
        return await rbac_generator.reassign_app_role_perms(
            app_id=app_id,
            role_name=role_name,
            perm_keys=req.perm_keys,
            performed_by=current_user.user_id,
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


# ==================== Reconciliation ====================
#
# Resync the persisted RBAC permissions, policy targets, and mapping file
# with the *running* app's field_registry state. Use this after dev edits
# that touched register_fields()/register_module_events() in the imported
# routes. The /reconcile-app Claude skill drives these endpoints.

@router.post(
    "/ingestions/{app_id}/reconcile",
    dependencies=[Depends(require_ai_permission("apps.permissions.edit"))],
)
async def reconcile_endpoint(app_id: str, current_user=Depends(get_current_user)):
    """Run RBAC / policy / mapping reconciliation against runtime field_registry state."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    from services.ingestion import reconcile
    try:
        return await reconcile.reconcile_app(
            app_id, performed_by=current_user.user_id, dry_run=False
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/ingestions/{app_id}/reconcile/preview",
    dependencies=[Depends(require_ai_permission("apps.permissions.edit"))],
)
async def reconcile_preview_endpoint(app_id: str, current_user=Depends(get_current_user)):
    """Dry-run reconciliation — computes the diff without applying changes."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    from services.ingestion import reconcile
    try:
        return await reconcile.reconcile_app(
            app_id, performed_by=current_user.user_id, dry_run=True
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ==================== AI Employee Linking ====================

@router.post("/ingestions/{app_id}/link-ai")
async def link_ai_employee(app_id: str, req: LinkAIEmployeeRequest, current_user=Depends(get_current_user)):
    """Link an ingested app to an AI employee."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    try:
        return await rbac_generator.link_ai_employee(
            app_id=app_id,
            employee_id=req.employee_id,
            permissions=req.permissions,
            performed_by=current_user.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ==================== Mapping File ====================

@router.post("/ingestions/{app_id}/mapping")
async def generate_mapping_endpoint(app_id: str, current_user=Depends(get_current_user)):
    """Generate (or regenerate) the adaptation mapping file for an ingested app.
    Writes to .ingestion/mappings/{app_slug}.json and returns the mapping dict."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    try:
        return await service.generate_mapping_file(app_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/ingestions/{app_id}/mapping")
async def get_mapping_endpoint(app_id: str, current_user=Depends(get_current_user)):
    """Read the existing mapping file from disk for an ingested app.
    Returns 404 if the mapping has not been generated yet."""
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    app = await repo.get_app_registry(app_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"App {app_id} not found")
    import json
    mapping_path = service.MAPPINGS_DIR / f"{app['app_slug']}.json"
    if not mapping_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Mapping file not generated yet — POST to /mapping first.",
        )
    return json.loads(mapping_path.read_text())


# ==================== Workflow Integration Verification ====================

@router.get("/ingestions/{app_id}/integration-status")
async def get_integration_status(app_id: str, current_user=Depends(get_current_user)):
    """
    Verify what's registered for an ingested app across all AEGISAXIOM systems:
    field registry, events (workflow triggers), policy targets, and modules.
    """
    if not has_perm(current_user, "admin.manage"):
        raise HTTPException(status_code=403, detail="Admin permission required")

    app = await repo.get_app_registry(app_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"App {app_id} not found")

    app_slug = app.get("app_slug", app["slug"].replace("-", "_"))
    entities = get_entities_for_module(app_slug)
    config = get_module_config(app_slug)

    integration = {
        "app_id": app_id,
        "app_slug": app_slug,
        "status": app.get("status"),
        "field_registry": {
            "registered": len(entities) > 0,
            "entities": entities,
            "fields_per_entity": {},
        },
        "events": {
            "registered": False,
            "events_per_entity": {},
        },
        "module_config": config if config else None,
        "modules": await repo.get_app_modules(app_id),
    }

    for entity in entities:
        fields = get_fields(app_slug, entity)
        integration["field_registry"]["fields_per_entity"][entity] = len(fields)
        events = get_module_events(app_slug, entity)
        if events:
            integration["events"]["registered"] = True
            integration["events"]["events_per_entity"][entity] = events

    return integration
