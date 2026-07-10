"""MATCH ingestion — Provider Template entity routes.

Adapted from source app ``backend/routers/provider_templates.py``
(Simplimeta/Appdirect-Order-POC1 @ feature/poc-to-prod).

A provider template captures everything the matching engine needs to know
about a single provider's report shape: column-to-AppDirect-field mappings,
default product/sales-rep/order-status values, business rules, and the
per-provider AppDirect FK overrides (advisor, product, provider, account
manager). Templates are the static configuration that drives n8n's parsing
and normalization stage.

Router prefix (mounted in server.py): ``/api/match/provider-templates``
(hyphenated URL slug — the entity name uses underscores in code, but the
HTTP path uses hyphens by convention).

The source app's ``/suggest-mappings`` route forwarded directly to an n8n
webhook URL stored on the connection doc. In AEGISAXIOM that wiring lives in
``services.match.n8n_client.N8nClient`` (Thread 1A); this router only
loads the settings singleton, instantiates the client, and calls
``suggest_mappings(...)``. The endpoint is NOT wrapped in ``crud_envelope``
because it doesn't mutate state — it just proxies a read/inference call.

The source app's ``/{template_id}/test`` accepted a CSV upload and returned
a preview of the projected mappings. The transform logic is ported inline
(``_apply_transform``) — it must stay in sync with the n8n JS implementation
in the source repo's "Parse CSVs" node so the AEGISAXIOM preview matches what n8n
will produce in production.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from models import gen_id
from services.field_registry import (
    register_fields,
    register_module_config,
    register_module_events,
)
from services.ingestion.route_helpers import crud_envelope
from services.match.models import (
    ProviderTemplate,
    ProviderTemplateCreate,
    ProviderTemplateUpdate,
    Settings,
    SuggestMappingsRequest,
)
from services.match.n8n_client import N8nClient

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Field / event / module registration (runs at module import)
# ---------------------------------------------------------------------------
# The 11 fields below mirror ``.ingestion/mappings/match.json::entities.
# provider_template.fields``. Source field ``id`` is registered as
# ``template_id`` (the AEGISAXIOM canonical ID field for this entity).
register_fields(
    "match",
    "provider_template",
    [
        {"name": "template_id", "type": "string", "label": "Template ID"},
        {"name": "name", "type": "string", "label": "Name"},
        {"name": "default_product", "type": "string", "label": "Default Product"},
        {
            "name": "sales_rep_priority",
            "type": "string",
            "label": "Sales Rep Priority",
        },
        {
            "name": "default_order_status",
            "type": "string",
            "label": "Default Order Status",
        },
        {
            "name": "column_mappings",
            "type": "list",
            "label": "Column Mappings",
        },
        {
            "name": "business_rules",
            "type": "list",
            "label": "Business Rules",
        },
        {
            "name": "appdirect_advisor_id",
            "type": "number",
            "label": "AppDirect Advisor ID",
        },
        {
            "name": "appdirect_product_id",
            "type": "number",
            "label": "AppDirect Product ID",
        },
        {
            "name": "appdirect_provider_id",
            "type": "number",
            "label": "AppDirect Provider ID",
        },
        {
            "name": "appdirect_account_manager_id",
            "type": "number",
            "label": "AppDirect Account Manager ID",
        },
    ],
)
register_module_config("match", "match_provider_templates", "template_id", "Custom")
register_module_events(
    "match", "provider_template", ["created", "updated", "deleted"]
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _scrub(doc: dict) -> dict:
    """Remove Mongo ``_id`` before returning a template doc to the API caller."""
    if doc is None:
        return doc
    doc.pop("_id", None)
    return doc


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _load_settings(db) -> Settings:
    """Load the singleton settings doc from ``match_settings`` and return it
    as a validated ``Settings`` Pydantic model.

    The settings singleton holds the n8n connection config (webhook URLs,
    shared auth token) and the AppDirect token used by AppDirectClient.
    ``suggest-mappings`` needs the n8n config to know where to send the
    inference request.

    Returns ``Settings()`` (Pydantic defaults) if no settings doc exists yet;
    N8nClient surfaces "not configured" errors at call time rather than
    crashing here. The Pydantic instance is required because N8nClient does
    attribute access (``settings.n8n.trigger_webhook_url``) in its
    constructor — a raw dict would AttributeError on the first call.
    """
    raw = await db["match_settings"].find_one({"_singleton": True}, {"_id": 0})
    if not raw:
        return Settings()
    raw.pop("_singleton", None)
    return Settings.model_validate(raw)


def _apply_dot_path(d: Dict[str, Any], path: str, value: Any) -> None:
    """Set ``value`` at the dotted ``path`` inside ``d``, creating intermediate
    dicts as needed.

    Ported verbatim from MATCH source's ``backend/utils.py::apply_dot_path``
    so the preview output's nesting matches what n8n produces for the
    ``output.*`` Contract H namespace.
    """
    parts = path.split(".")
    cur: Dict[str, Any] = d
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _apply_transform(value: Any, transform_name: Optional[str]) -> Any:
    """Apply a column transform to a raw CSV cell value.

    Ported from the source app's ``backend/routers/provider_templates.py::
    _apply_transform`` (which mirrors the JS ``applyTransform()`` helper in
    n8n's "Parse CSVs" node). The Test Template endpoint uses this so the
    preview rendered by AEGISAXIOM matches what n8n will produce in production.

    Supported transforms:
        * ``strip_usd_prefix`` — strip "USD"/"$"/",", parse as float.
        * ``normalize_prc`` — bucket the value into one of
          {Renewal, Upgrade, MACD, New} via case-insensitive matching.
          Order matters: "renew" is checked before "new" so "Renewal" isn't
          mis-classified as "New".
        * ``parse_date`` — try several common date formats, return ISO date.
        * ``none`` / missing — pass through.
    """
    if value is None or value == "":
        return None
    if not transform_name or transform_name == "none":
        return value
    if transform_name == "strip_usd_prefix":
        cleaned = str(value).replace("USD ", "").replace("usd ", "")
        cleaned = cleaned.replace(",", "").replace("$", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    if transform_name == "normalize_prc":
        v = str(value).strip()
        low = v.lower()
        if "renew" in low:
            return "Renewal"
        if "upgrade" in low:
            return "Upgrade"
        if "macd" in low or low == "mac":
            return "MACD"
        if low.startswith("new"):
            return "New"
        return v
    if transform_name == "parse_date":
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(str(value).strip(), fmt).date().isoformat()
            except ValueError:
                continue
        return str(value)
    # Output-only or unknown transform — pass through.
    return value


# ---------------------------------------------------------------------------
# Routes — read
# ---------------------------------------------------------------------------
@router.get(
    "",
    dependencies=[Depends(require_ai_permission("match.provider_template.view"))],
)
async def list_provider_templates(
    current_user=Depends(get_current_user),
):
    """List provider templates, newest first, capped at 500."""
    if not has_perm(current_user, "match.provider_template.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    docs = (
        await db["match_provider_templates"]
        .find({}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(500)
    )
    return docs


# ---------------------------------------------------------------------------
# Routes — n8n proxy + template test
# Registered BEFORE the ``/{template_id}`` parametric routes so the literal
# paths ``/suggest-mappings`` and ``/{...}/test`` match correctly. (FastAPI
# resolves in declaration order; without this ordering ``/suggest-mappings``
# would be captured by ``GET /{template_id}``.)
# ---------------------------------------------------------------------------
@router.post(
    "/suggest-mappings",
    dependencies=[Depends(require_ai_permission("match.provider_template.create"))],
)
async def suggest_mappings(
    payload: SuggestMappingsRequest,
    current_user=Depends(get_current_user),
):
    """Proxy a column-mapping inference request to n8n.

    Reads the singleton settings doc (for the n8n webhook URL + shared auth
    token), instantiates ``N8nClient``, and forwards the request. The client
    returns whatever n8n responded with — typically a list of suggested
    column-to-field mappings the user can accept or edit. Not a CRUD op, so
    no ``crud_envelope`` and no event/audit fan-out.
    """
    if not has_perm(current_user, "match.provider_template.create"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    settings = await _load_settings(db)
    client = N8nClient(settings)
    return await client.suggest_mappings(
        provider_report_url=payload.provider_report_url,
        snapshot_customers_url=payload.snapshot_customers_url,
        template_id=payload.template_id,
    )


@router.post(
    "/{template_id}/test",
    dependencies=[Depends(require_ai_permission("match.provider_template.view"))],
)
async def test_provider_template(
    template_id: str,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    """Run the template's mappings against an uploaded sample CSV.

    Returns a preview of the parsed output for the first 5 rows so users
    can sanity-check their mapping configuration before saving. The
    transform logic in ``_apply_transform`` must stay in sync with n8n's
    "Parse CSVs" node JS — preview parity is the entire point of this
    endpoint.

    Not a CRUD op — no envelope, no event.
    """
    if not has_perm(current_user, "match.provider_template.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    template = await db["match_provider_templates"].find_one(
        {"template_id": template_id}, {"_id": 0}
    )
    if not template:
        raise HTTPException(
            status_code=404, detail=f"Template '{template_id}' not found"
        )

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Some provider exports ship in latin-1; fall back rather than fail.
        text = content.decode("latin-1")

    rows = [
        r
        for r in csv.DictReader(io.StringIO(text))
        if any((v or "").strip() for v in r.values())
    ]

    if not rows:
        return {
            "status": "error",
            "message": "CSV file appears to be empty.",
            "preview": [],
            "warnings": [],
            "headers_detected": [],
            "total_rows": 0,
        }

    headers = list(rows[0].keys())

    # Field names follow the data-dictionary ColumnMapping contract:
    #   source_column  — column header in the provider CSV
    #   target_path    — dotted Contract H path inside output.* (e.g.
    #                    "output.customer.name")
    #   transforms[]   — ordered list of transform names applied left-to-right
    #
    # The legacy MATCH source used appdirect_field / provider_column /
    # transform (singular); AEGISAXIOM's Pydantic model is the source of truth and
    # those legacy names are NOT read here. Phase 4's n8n integration shim is
    # responsible for translating n8n's response keys back into this contract.
    field_to_column: Dict[str, str] = {
        m["target_path"]: m["source_column"]
        for m in (template.get("column_mappings") or [])
        if m.get("target_path") and m.get("source_column")
    }

    required_fields = [
        m["target_path"]
        for m in (template.get("column_mappings") or [])
        if m.get("required") and m.get("target_path")
    ]
    missing_columns = [
        field_to_column[f]
        for f in required_fields
        if f in field_to_column and field_to_column[f] not in headers
    ]
    warnings: List[str] = []
    if missing_columns:
        warnings.append(
            f"Required columns missing from CSV: {', '.join(missing_columns)}"
        )

    preview_rows: List[Dict[str, Any]] = []
    for i, row in enumerate(rows[:5]):
        extracted: Dict[str, Any] = {}
        for mapping in template.get("column_mappings") or []:
            target_path = mapping.get("target_path")
            col = mapping.get("source_column")
            transforms = mapping.get("transforms") or []
            if not target_path or not col:
                continue
            value: Any = row.get(col, "")
            # Apply transforms left-to-right; each step's output feeds the next.
            for tname in transforms:
                value = _apply_transform(value, tname)
            # Nest at the dotted path so the preview mirrors what n8n
            # produces for the output.* Contract H namespace.
            _apply_dot_path(extracted, target_path, value)
        preview_rows.append(
            {"row_index": i, "source": row, "extracted": extracted}
        )

    return {
        "status": "warnings" if warnings else "success",
        "message": f"Parsed {len(rows)} rows. Showing first {len(preview_rows)}.",
        "preview": preview_rows,
        "warnings": warnings,
        "headers_detected": headers,
        "total_rows": len(rows),
    }


# ---------------------------------------------------------------------------
# Routes — by-id read
# ---------------------------------------------------------------------------
@router.get(
    "/{template_id}",
    dependencies=[Depends(require_ai_permission("match.provider_template.view"))],
)
async def get_provider_template(
    template_id: str,
    current_user=Depends(get_current_user),
):
    """Fetch a single provider template by ID."""
    if not has_perm(current_user, "match.provider_template.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    doc = await db["match_provider_templates"].find_one(
        {"template_id": template_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(
            status_code=404, detail=f"Template '{template_id}' not found"
        )
    return doc


# ---------------------------------------------------------------------------
# Routes — write
# ---------------------------------------------------------------------------
@router.post(
    "",
    dependencies=[Depends(require_ai_permission("match.provider_template.create"))],
)
async def create_provider_template(
    payload: ProviderTemplateCreate,
    request: Request,
    current_user=Depends(get_current_user),
):
    """Create a new provider template.

    AEGISAXIOM generates the ``template_id`` (``m_tmpl_<uuid12>``) — callers do
    not supply it. The source app accepted a user-supplied slug; in AEGISAXIOM
    the canonical pattern is generated IDs (see ENGINE_ARCHITECTURE.md).
    """
    if not has_perm(current_user, "match.provider_template.create"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    template_id = gen_id("m_tmpl_")
    now = _utc_now()

    # Build the persisted doc via the Pydantic model so defaults apply
    # (column_mappings=[], business_rules=[], FK overrides=0, etc.).
    # ProviderTemplate.created_at / updated_at are typed `str` (ISO); the
    # doc re-stamp below upgrades to BSON datetime for Mongo sortability.
    template_obj = ProviderTemplate(
        template_id=template_id,
        **payload.model_dump(exclude_unset=True),
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
    )
    doc = template_obj.model_dump(mode="json")
    # Re-stamp tz-aware datetimes so Mongo stores them as BSON dates rather
    # than the ISO strings model_dump(mode="json") produces.
    doc["created_at"] = now
    doc["updated_at"] = now
    # ``created_by`` is not part of the ProviderTemplate Pydantic schema (the
    # data dict doesn't declare it), but we persist the creator on the Mongo
    # doc for audit-style "who made this?" queries. The CRUD envelope already
    # records actor on every create, so this is belt-and-suspenders.
    doc["created_by"] = current_user.user_id

    async with crud_envelope(
        module="match",
        entity="provider_template",
        action="created",
        resource_id=template_id,
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        await db["match_provider_templates"].insert_one(doc)
        ctx.set_after(_scrub(dict(doc)))

    return _scrub(doc)


@router.put(
    "/{template_id}",
    dependencies=[Depends(require_ai_permission("match.provider_template.edit"))],
)
async def update_provider_template(
    template_id: str,
    payload: ProviderTemplateUpdate,
    request: Request,
    current_user=Depends(get_current_user),
):
    """Partially update a provider template.

    Uses ``model_dump(exclude_unset=True)`` so unsupplied fields are not
    overwritten with their defaults. The full updated doc is written via
    ``replace_one`` so the audit ``after`` payload reflects the complete
    post-state, not just the delta.
    """
    if not has_perm(current_user, "match.provider_template.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    before = await db["match_provider_templates"].find_one(
        {"template_id": template_id}, {"_id": 0}
    )
    if not before:
        raise HTTPException(
            status_code=404, detail=f"Template '{template_id}' not found"
        )

    updates = payload.model_dump(exclude_unset=True)
    after = {**before, **updates, "updated_at": _utc_now()}

    async with crud_envelope(
        module="match",
        entity="provider_template",
        action="updated",
        resource_id=template_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["match_provider_templates"].replace_one(
            {"template_id": template_id}, after
        )
        ctx.set_after(_scrub(dict(after)))

    return _scrub(after)


@router.delete(
    "/{template_id}",
    dependencies=[Depends(require_ai_permission("match.provider_template.delete"))],
)
async def delete_provider_template(
    template_id: str,
    request: Request,
    current_user=Depends(get_current_user),
):
    """Hard-delete a provider template.

    The source app blocked delete when ``reports_processed > 0`` and asked
    the caller to set status to "paused" instead. That guard is intentionally
    NOT ported — AEGISAXIOM does not carry the ``reports_processed`` counter on
    the AEGISAXIOM-side template (it was a UI affordance for the standalone POC).
    If the operator wants soft-stop semantics, they should add a "disabled"
    field and gate the matching engine on it.
    """
    if not has_perm(current_user, "match.provider_template.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    before = await db["match_provider_templates"].find_one(
        {"template_id": template_id}, {"_id": 0}
    )
    if not before:
        raise HTTPException(
            status_code=404, detail=f"Template '{template_id}' not found"
        )

    async with crud_envelope(
        module="match",
        entity="provider_template",
        action="deleted",
        resource_id=template_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["match_provider_templates"].delete_one(
            {"template_id": template_id}
        )
        ctx.set_after({"template_id": template_id})

    return {"ok": True, "template_id": template_id}
