"""Finance FCC — Financial Statement routes.

Provides:
  GET /financials/pnl?period=current|prior
  GET /financials/balance-sheet
  GET /financials/cash-flow
  GET /financials/trial-balance

All routes are read-only against the QBO data lake. The private helpers
(_get_report, _line_items, _sum_section, _find_summary, _find_row, to_float,
safe_doc) are also exported so finance_kpis.py and finance_working_capital.py
can import them without circular dependencies.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id, require_root_tenant
from services.field_registry import register_module_events
from services.nova_engine.db import get_qbo_db

logger = logging.getLogger(__name__)
router = APIRouter()

register_module_events("finance", "statements", [])


# ---------------------------------------------------------------------------
# Pure transformation helpers (shared with finance_kpis, finance_working_capital)
# ---------------------------------------------------------------------------

def to_float(value: Any) -> float:
    """Safely coerce a value to float. Returns 0.0 on failure."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def safe_doc(doc: dict) -> dict:
    """Strip MongoDB _id field from a document."""
    d = dict(doc)
    d.pop("_id", None)
    return d


async def _get_report(name: str) -> Optional[dict]:
    """Fetch the most recently synced document from a QBO report collection."""
    qbo_db = get_qbo_db()
    docs = await qbo_db[name].find({}, {"_id": 0}).sort([("synced_at", -1)]).limit(1).to_list(1)
    return docs[0] if docs else None


def _line_items(report: Optional[dict]) -> List[dict]:
    if not report:
        return []
    return report.get("line_items", [])


def _sum_section(report: Optional[dict], section_match: str, total_col: str = "Total") -> float:
    """Sum numeric values from report rows where section/parent_account contains the match string."""
    total = 0.0
    for li in _line_items(report):
        if (
            section_match.lower() in str(li.get("section", "")).lower()
            or section_match.lower() in str(li.get("parent_account", "")).lower()
        ) and not li.get("is_summary", False):
            total += to_float(li.get(total_col))
    return total


def _find_summary(report: Optional[dict], starts_with: str, total_col: str = "Total") -> Optional[float]:
    """Find the first summary row whose account name starts with the given string."""
    for li in _line_items(report):
        name = str(li.get("Account") or li.get("account_name") or "")
        if li.get("is_summary") and name.lower().startswith(starts_with.lower()):
            return to_float(li.get(total_col))
    return None


def _find_row(report: Optional[dict], name_match: str, total_col: str = "Total") -> Optional[float]:
    """Find the first non-summary row whose account name exactly matches."""
    for li in _line_items(report):
        name = str(li.get("Account") or li.get("account_name") or "")
        if name.strip().lower() == name_match.strip().lower():
            return to_float(li.get(total_col))
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_EMPTY_STATEMENT: Dict[str, Any] = {
    "line_items": [],
    "summary": {},
    "warning": "no QBO sync data yet",
}


@router.get(
    "/financials/pnl",
    dependencies=[Depends(require_ai_permission("finance.statements.view"))],
)
async def get_pnl(
    period: str = Query("current", pattern="^(current|prior)$"),
    current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id),
):
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    if not has_perm(user_dict, "finance.statements.view"):
        raise HTTPException(status_code=403, detail="finance.statements.view permission required")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    coll = "reports_pnl" if period == "current" else "reports_pnl_prior_year"
    doc = await _get_report(coll)
    if not doc:
        return {**_EMPTY_STATEMENT, "period": period}
    return safe_doc(doc)


@router.get(
    "/financials/balance-sheet",
    dependencies=[Depends(require_ai_permission("finance.statements.view"))],
)
async def get_balance_sheet(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    if not has_perm(user_dict, "finance.statements.view"):
        raise HTTPException(status_code=403, detail="finance.statements.view permission required")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    doc = await _get_report("reports_balance_sheet")
    if not doc:
        return _EMPTY_STATEMENT
    return safe_doc(doc)


@router.get(
    "/financials/cash-flow",
    dependencies=[Depends(require_ai_permission("finance.statements.view"))],
)
async def get_cash_flow(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    if not has_perm(user_dict, "finance.statements.view"):
        raise HTTPException(status_code=403, detail="finance.statements.view permission required")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    doc = await _get_report("reports_cash_flow")
    if not doc:
        return _EMPTY_STATEMENT
    return safe_doc(doc)


@router.get(
    "/financials/trial-balance",
    dependencies=[Depends(require_ai_permission("finance.statements.view"))],
)
async def get_trial_balance(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    if not has_perm(user_dict, "finance.statements.view"):
        raise HTTPException(status_code=403, detail="finance.statements.view permission required")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    doc = await _get_report("reports_trial_balance")
    if not doc:
        return _EMPTY_STATEMENT
    return safe_doc(doc)
