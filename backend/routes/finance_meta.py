"""Finance FCC — Meta routes.

Provides:
  GET /meta/last-sync  — latest synced_at timestamp across QBO collections.

Skipped:
  GET /health  — AEGISAXIOM already has a global health endpoint.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException

from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id, require_root_tenant
from services.field_registry import register_module_events
from services.nova_engine.db import get_qbo_db

logger = logging.getLogger(__name__)
router = APIRouter()

register_module_events("finance", "meta", [])


# ---------------------------------------------------------------------------
# GET /meta/last-sync
# ---------------------------------------------------------------------------

@router.get(
    "/meta/last-sync",
    dependencies=[Depends(require_ai_permission("finance.meta.view"))],
)
async def last_sync(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    """Return the latest synced_at timestamp seen across the QBO source collections."""
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    if not has_perm(user_dict, "finance.meta.view"):
        raise HTTPException(status_code=403, detail="finance.meta.view permission required")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    qbo_db = get_qbo_db()

    probes = [
        ("general_ledger", "synced_at"),
        ("transactions", "synced_at"),
        ("ap_bills", "raw_data.MetaData.LastUpdatedTime"),
        ("ar_invoices", "raw_data.MetaData.LastUpdatedTime"),
    ]

    latest = None
    for coll, field in probes:
        try:
            cur = qbo_db[coll].find({field: {"$exists": True}}).sort([(field, -1)]).limit(1)
            doc = await cur.to_list(length=1)
            if doc:
                v = doc[0]
                for k in field.split("."):
                    v = v.get(k) if isinstance(v, dict) else None
                if v:
                    if latest is None or str(v) > str(latest):
                        latest = v
        except Exception as exc:
            logger.warning("last-sync probe failed on %s.%s: %s", coll, field, exc)

    return {
        "last_synced_at": str(latest) if latest else None,
        # Note 9: ORG_NAME is env-var configurable for multi-tenant Phase-2.
        # Defaults to "AegisAxiom AI" for single-tenant Phase-1.
        "organization": os.environ.get("ORG_NAME", "AegisAxiom AI"),
    }
