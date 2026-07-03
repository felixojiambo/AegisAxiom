"""MATCH ingestion — Session entity routes.

Adapted from source app ``backend/routers/sessions.py``
(Simplimeta/Appdirect-Order-POC1 @ feature/poc-to-prod).

A session is a single import run — provider report + CRM snapshot — fed into
n8n for AI matching. Sessions are immutable post-create (no update endpoint);
the only mutations are create and delete. Rows attached to a session are
created out-of-band by the webhook handler in ``match_webhooks.py``.

Cascade-delete decision:
    When a session is deleted we also wipe its rows via ``delete_many``
    WITHOUT firing a per-row ``crud_envelope`` for each. Rationale: rows are
    derived artifacts owned by the session, not independently auditable
    objects in this context, and per-row envelopes on a multi-thousand-row
    session would generate a fan-out of audit + event records that drowns
    the meaningful "session deleted" signal. Future ops that need per-row
    audit on cascade should add it explicitly.

The source app's `/process`, `/simulate`, `/audit`, and `/{session_id}/rows`
endpoints are deliberately NOT ported here:
    * `/process` and `/simulate` involve n8n trigger logic that lives in the
      webhook router (Thread 1D).
    * `/audit` is replaced by the AEGISAXIOM unified audit log (written via
      ``crud_envelope``).
    * Rows-by-session lives on the row router as ``/api/match/rows/by-session/{id}``
      (Thread 1B owns that route — see ``match_row.py``).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from models import gen_id
from services.field_registry import (
    register_fields,
    register_module_config,
    register_module_events,
)
from services.permission_registry import register_module_permissions
from services.ingestion.route_helpers import crud_envelope
from services.match.models import (
    N8nSettings,
    ProviderTemplate,
    Session,
    SessionCreate,
    Settings,
)
from services.match.n8n_client import N8nClient

logger = logging.getLogger(__name__)
router = APIRouter()

# Disk location of CSV uploads — kept in sync with match_uploads.UPLOAD_DIR.
_UPLOAD_DIR = Path(__file__).parent.parent / "uploads" / "match"


# ---------------------------------------------------------------------------
# Permission registration — declared here so seed_system_permissions picks
# them up on every deploy, the same way native modules do. Without this,
# the perms would only exist on whatever DB the ingestion pipeline's
# apply_rbac() was last run against — leaving the role-creation picker
# blank for MATCH on every other environment.
#
# Keys + labels are the canonical ingestion output (14 perms generated
# from the route surface — keep this list in sync with the route handlers
# below + the equivalent permission gates in the other match_*.py files).
# ---------------------------------------------------------------------------
@register_module_permissions("match")
def _match_permissions():
    return [
        {"key": "match.access",                    "group": "MATCH", "label": "Access MATCH"},
        {"key": "match.appdirect_call.view",       "group": "MATCH", "label": "View AppDirect Call"},
        {"key": "match.provider_template.view",    "group": "MATCH", "label": "View Provider Template"},
        {"key": "match.provider_template.create",  "group": "MATCH", "label": "Create Provider Template"},
        {"key": "match.provider_template.edit",    "group": "MATCH", "label": "Edit Provider Template"},
        {"key": "match.provider_template.delete",  "group": "MATCH", "label": "Delete Provider Template"},
        {"key": "match.row.view",                  "group": "MATCH", "label": "View Row"},
        {"key": "match.row.edit",                  "group": "MATCH", "label": "Edit Row"},
        {"key": "match.row.change_status",         "group": "MATCH", "label": "Change Row Status"},
        {"key": "match.session.view",              "group": "MATCH", "label": "View Session"},
        {"key": "match.session.create",            "group": "MATCH", "label": "Create Session"},
        {"key": "match.session.delete",            "group": "MATCH", "label": "Delete Session"},
        {"key": "match.settings.view",             "group": "MATCH", "label": "View Settings"},
        {"key": "match.settings.edit",             "group": "MATCH", "label": "Edit Settings"},
    ]


# ---------------------------------------------------------------------------
# Field / event / module registration (runs at module import)
# ---------------------------------------------------------------------------
register_fields(
    "match",
    "session",
    [
        {"name": "session_id", "type": "string", "label": "Session ID"},
        {"name": "provider", "type": "string", "label": "Provider"},
        {
            "name": "mode",
            "type": "string",
            "label": "Mode",
            "options": [
                {"value": "upload", "label": "Upload"},
                {"value": "live_sql", "label": "Live SQL"},
                {"value": "live_api", "label": "Live API"},
            ],
        },
        {"name": "template_id", "type": "string", "label": "Provider Template"},
        {"name": "provider_report_url", "type": "string", "label": "Provider Report URL"},
        {"name": "provider_report_filename", "type": "string", "label": "Provider Report Filename"},
        {"name": "snapshot_file_url", "type": "string", "label": "Snapshot File URL"},
        {"name": "snapshot_file_filename", "type": "string", "label": "Snapshot File Filename"},
        {"name": "snapshot_customers_url", "type": "string", "label": "Snapshot Customers URL"},
        {"name": "snapshot_customers_filename", "type": "string", "label": "Snapshot Customers Filename"},
        {"name": "snapshot_orders_url", "type": "string", "label": "Snapshot Orders URL"},
        {"name": "snapshot_orders_filename", "type": "string", "label": "Snapshot Orders Filename"},
        {"name": "sql_connection_string", "type": "string", "label": "SQL Connection (masked)"},
        {"name": "label", "type": "string", "label": "Label"},
    ],
)
register_module_config("match", "match_sessions", "session_id", "Custom")
# Sessions are immutable post-create — no "updated" event in the lifecycle.
# "processed" fires when the operator triggers the n8n matching workflow.
register_module_events("match", "session", ["created", "deleted", "processed"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _scrub(doc: dict) -> dict:
    """Remove Mongo ``_id`` before returning a session doc to the API caller."""
    doc.pop("_id", None)
    return doc


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post(
    "",
    dependencies=[Depends(require_ai_permission("match.session.create"))],
)
async def create_session(
    payload: SessionCreate,
    request: Request,
    current_user=Depends(get_current_user),
):
    """Create a new MATCH session.

    Body shape comes from the source app's ``SessionCreate`` model and is
    re-exported from ``services.match.models`` by Thread 1A. The handler
    builds the persisted ``Session`` document, generates a AEGISAXIOM-prefixed
    ``session_id`` (``m_sess_<uuid12>``), and wraps the insert in a
    ``crud_envelope`` so the create event + audit log fire after the write.
    """
    if not has_perm(current_user, "match.session.create"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    session_id = gen_id("m_sess_")
    now = _utc_now()

    # Build the persisted doc. Start from the SessionCreate payload, then
    # stamp AEGISAXIOM-managed fields (session_id, created_by, created_at). The
    # Session model lives in services.match.models — we accept whatever
    # extra fields it carries (status, row_count, etc.) by constructing
    # via the model so defaults apply. `created_at` on the model is `str`
    # (ISO), so we pass the ISO form to the constructor; the re-stamp on
    # line below upgrades the persisted doc back to a BSON datetime for
    # Mongo sortability.
    session_obj = Session(
        session_id=session_id,
        provider=payload.provider,
        mode=payload.mode,
        template_id=payload.template_id,
        label=payload.label or f"{payload.provider} session",
        provider_report_url=payload.provider_report_url,
        provider_report_filename=payload.provider_report_filename,
        snapshot_file_url=payload.snapshot_file_url,
        snapshot_file_filename=payload.snapshot_file_filename,
        snapshot_customers_url=payload.snapshot_customers_url,
        snapshot_customers_filename=payload.snapshot_customers_filename,
        snapshot_orders_url=payload.snapshot_orders_url,
        snapshot_orders_filename=payload.snapshot_orders_filename,
        sql_connection_string=payload.sql_connection_string,
        created_by=current_user.user_id,
        created_at=now.isoformat(),
    )
    doc = session_obj.model_dump(mode="json")
    # Re-stamp the timezone-aware datetime (model_dump(mode="json") serializes
    # to ISO strings; we want Python datetimes in Mongo for sortability).
    # Sessions are immutable post-create — no updated_at field per the data
    # dictionary; the registered events are ["created", "deleted"] only.
    doc["created_at"] = now

    async with crud_envelope(
        module="match",
        entity="session",
        action="created",
        resource_id=session_id,
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        await db["match_sessions"].insert_one(doc)
        ctx.set_after(_scrub(dict(doc)))

    return _scrub(doc)


@router.get(
    "",
    dependencies=[Depends(require_ai_permission("match.session.view"))],
)
async def list_sessions(
    current_user=Depends(get_current_user),
):
    """List MATCH sessions, newest first, capped at 500."""
    if not has_perm(current_user, "match.session.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    docs = (
        await db["match_sessions"]
        .find({}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(500)
    )
    return docs


@router.get(
    "/{session_id}",
    dependencies=[Depends(require_ai_permission("match.session.view"))],
)
async def get_session(
    session_id: str,
    current_user=Depends(get_current_user),
):
    """Fetch a single session by ID."""
    if not has_perm(current_user, "match.session.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    doc = await db["match_sessions"].find_one({"session_id": session_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Session not found")
    return doc


@router.delete(
    "/{session_id}",
    dependencies=[Depends(require_ai_permission("match.session.delete"))],
)
async def delete_session(
    session_id: str,
    request: Request,
    current_user=Depends(get_current_user),
):
    """Hard-delete a session and cascade-delete its rows.

    See the file docstring for the rationale behind the single-envelope
    cascade (rows are deleted via ``delete_many`` without per-row envelopes).
    """
    if not has_perm(current_user, "match.session.delete"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    before = await db["match_sessions"].find_one(
        {"session_id": session_id}, {"_id": 0}
    )
    if not before:
        raise HTTPException(status_code=404, detail="Session not found")

    # Capture the cascade count into an outer local so the return value
    # doesn't reach across the `async with` boundary. `async with` does not
    # introduce a scope in Python, but the explicit hoist makes the intent
    # obvious to anyone reading the handler.
    cascaded_count = 0
    async with crud_envelope(
        module="match",
        entity="session",
        action="deleted",
        resource_id=session_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["match_sessions"].delete_one({"session_id": session_id})
        delete_result = await db["match_rows"].delete_many({"session_id": session_id})
        cascaded_count = delete_result.deleted_count
        # Surface the row-cascade count in the audit `after` payload so
        # auditors can see how much was wiped without a follow-up query.
        ctx.set_after(
            {
                "session_id": session_id,
                "cascaded_row_count": cascaded_count,
            }
        )

    return {
        "ok": True,
        "session_id": session_id,
        "cascaded_row_count": cascaded_count,
    }


# ---------------------------------------------------------------------------
# Process — outbound n8n trigger
# ---------------------------------------------------------------------------
def _read_upload_csv(filename: str | None) -> str:
    """Read an uploaded CSV from disk by stored filename. Returns "" when
    the filename is missing or unreadable — caller decides whether that's
    fatal (provider_report = fatal, snapshot_orders = optional)."""
    if not filename:
        return ""
    p = _UPLOAD_DIR / Path(filename).name
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_bytes().decode("utf-8", errors="replace")


def _derive_callback_url() -> str:
    """Mirror of match_settings._derive_callback_url so the process handler
    doesn't have to import from the settings route module."""
    base = (
        os.getenv("MATCH_PUBLIC_BASE_URL")
        or os.getenv("FRONTEND_URL")
        or "http://localhost:3001"
    )
    return f"{base.rstrip('/')}/api/match/webhooks/row-processed"


def _coerce_template_for_pydantic(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Mongo stores ``created_at`` / ``updated_at`` as BSON datetimes; the
    ProviderTemplate model expects ISO strings. Same defect family as the
    Settings load — coerce defensively before model_validate."""
    out = dict(doc)
    out.pop("_id", None)
    for k in ("created_at", "updated_at"):
        v = out.get(k)
        if isinstance(v, datetime):
            out[k] = v.isoformat()
    return out


@router.post(
    "/{session_id}/process",
    dependencies=[Depends(require_ai_permission("match.session.create"))],
)
async def process_session(
    session_id: str,
    request: Request,
    current_user=Depends(get_current_user),
):
    """Trigger the n8n matching workflow for a session.

    Reads the uploaded CSVs off disk, loads the configured template, decrypts
    the n8n shared bearer, and POSTs the full payload to the n8n trigger
    webhook. n8n then calls back per row via /api/match/webhooks/row-processed.

    Sets the session status to ``processing`` BEFORE the n8n POST so the UI
    reflects the in-flight state even if the call hangs.
    """
    if not has_perm(current_user, "match.session.create"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    sess = await db["match_sessions"].find_one(
        {"session_id": session_id}, {"_id": 0}
    )
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    # --- Load + validate settings (n8n trigger url + shared auth token) ---
    settings_doc = await db["match_settings"].find_one({"_singleton": True}) or {}
    n8n_block = settings_doc.get("n8n") or {}
    trigger_url = n8n_block.get("trigger_webhook_url") or ""
    if not trigger_url:
        raise HTTPException(
            status_code=400,
            detail="n8n trigger webhook URL is not configured. Save the n8n "
            "section on the Settings tab first.",
        )
    encrypted_token = n8n_block.get("shared_auth_token") or ""
    if not encrypted_token:
        raise HTTPException(
            status_code=400,
            detail="n8n shared auth token is not configured. Save the n8n "
            "section on the Settings tab first.",
        )
    # N8nClient decrypts the token internally — pass ciphertext through.

    # --- Load + validate template ---
    template_id = sess.get("template_id") or ""
    if not template_id:
        raise HTTPException(
            status_code=400, detail="Session has no template_id."
        )
    template_doc = await db["match_provider_templates"].find_one(
        {"template_id": template_id}
    )
    if not template_doc:
        raise HTTPException(
            status_code=400,
            detail=f"Provider template {template_id!r} not found.",
        )
    if not template_doc.get("column_mappings"):
        raise HTTPException(
            status_code=400,
            detail=f"Provider template {template_id!r} has no column "
            "mappings. Configure mappings in Provider Templates before "
            "processing.",
        )
    try:
        template = ProviderTemplate.model_validate(
            _coerce_template_for_pydantic(template_doc)
        )
    except Exception as exc:
        logger.exception("Provider template validation failed")
        raise HTTPException(
            status_code=500,
            detail=f"Provider template {template_id!r} failed validation: {exc}",
        )

    # --- Resolve uploaded CSVs ---
    provider_report_csv = _read_upload_csv(sess.get("provider_report_filename"))
    if not provider_report_csv:
        raise HTTPException(
            status_code=400,
            detail="Provider report file is missing or unreadable. Re-upload "
            "the CSV in a fresh session.",
        )
    snapshot_customers_csv = _read_upload_csv(
        sess.get("snapshot_customers_filename") or sess.get("snapshot_file_filename")
    )
    if not snapshot_customers_csv:
        raise HTTPException(
            status_code=400,
            detail="Snapshot customers file is missing or unreadable. Re-upload "
            "the customers CSV in a fresh session.",
        )
    snapshot_orders_csv = _read_upload_csv(sess.get("snapshot_orders_filename"))

    # --- Build callback URL ---
    callback_url = (
        n8n_block.get("callback_url") or _derive_callback_url()
    )

    # --- Mark session as processing BEFORE the outbound POST ---
    now = _utc_now()
    await db["match_sessions"].update_one(
        {"session_id": session_id},
        {
            "$set": {
                "status": "processing",
                "triggered_at": now,
            }
        },
    )

    # --- Call n8n ---
    # N8nClient wants the full Settings (it reads .n8n off it) and decrypts
    # the shared_auth_token internally — pass the ciphertext through.
    stub_settings = Settings(
        n8n=N8nSettings(
            trigger_webhook_url=trigger_url,
            magic_mapping_webhook_url=n8n_block.get("magic_mapping_webhook_url", ""),
            reviewer_action_webhook_url=n8n_block.get("reviewer_action_webhook_url", ""),
            shared_auth_token=encrypted_token,
            callback_url=callback_url,
        )
    )
    client = N8nClient(stub_settings)
    try:
        result = await client.process_provider_report(
            session_id=session_id,
            provider_report_csv=provider_report_csv,
            snapshot_customers_csv=snapshot_customers_csv,
            template=template,
            callback_url=callback_url,
            provider=(sess.get("provider") or "Nextiva").lower(),
            snapshot_orders=snapshot_orders_csv,
            template_id=template_id,
        )
    except Exception as exc:
        logger.exception("n8n process_provider_report call raised")
        await db["match_sessions"].update_one(
            {"session_id": session_id},
            {"$set": {"status": "trigger_failed"}},
        )
        raise HTTPException(
            status_code=502,
            detail=f"n8n trigger failed: {exc}",
        )

    async with crud_envelope(
        module="match",
        entity="session",
        action="processed",
        resource_id=session_id,
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        ctx.set_after(
            {
                "session_id": session_id,
                "trigger_url": trigger_url,
                "callback_url": callback_url,
                "provider_report_chars": len(provider_report_csv),
                "snapshot_customers_chars": len(snapshot_customers_csv),
                "snapshot_orders_chars": len(snapshot_orders_csv),
                "template_id": template_id,
                "column_mappings_count": len(template_doc.get("column_mappings") or []),
            }
        )

    return {
        "ok": True,
        "session_id": session_id,
        "triggered_at": now.isoformat(),
        "callback_url": callback_url,
        "n8n_response": result,
        "provider_report_chars": len(provider_report_csv),
        "snapshot_customers_chars": len(snapshot_customers_csv),
        "snapshot_orders_chars": len(snapshot_orders_csv),
        "template_id": template_id,
    }
