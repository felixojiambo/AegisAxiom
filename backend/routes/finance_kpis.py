"""Finance FCC — Dashboard KPI route.

Provides:
  GET /dashboard/kpis  — 16 KPIs derived from QBO pre-aggregated reports.

Pulls from: reports_balance_sheet, reports_pnl, reports_pnl_prior_year,
reports_ar_aging, reports_ap_aging.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from middleware.tenant_scoping import get_current_tenant_id, require_root_tenant
from services.field_registry import register_module_events

from .finance_statements import (
    _find_summary,
    _get_report,
    _line_items,
    _sum_section,
    to_float,
)

logger = logging.getLogger(__name__)
router = APIRouter()

register_module_events("finance", "kpis", [])


# ---------------------------------------------------------------------------
# GET /dashboard/kpis
# ---------------------------------------------------------------------------

@router.get(
    "/dashboard/kpis",
    dependencies=[Depends(require_ai_permission("finance.kpis.view"))],
)
async def dashboard_kpis(current_user=Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant_id)):
    """Return 16 top-level financial KPIs derived from QBO report snapshots."""
    user_dict = current_user if isinstance(current_user, dict) else current_user.model_dump()
    if not has_perm(user_dict, "finance.kpis.view"):
        raise HTTPException(status_code=403, detail="finance.kpis.view permission required")
    # Phase 1E.3c — Finance is SimpliMeta-internal only.
    require_root_tenant(tenant_id)

    bs = await _get_report("reports_balance_sheet")
    pnl = await _get_report("reports_pnl")
    pnl_prior = await _get_report("reports_pnl_prior_year")
    ar_aging = await _get_report("reports_ar_aging")
    ap_aging = await _get_report("reports_ap_aging")

    # -- Balance sheet values --
    cash = _sum_section(bs, "Bank Accounts")
    total_assets = _find_summary(bs, "Total Assets") or _find_summary(bs, "TOTAL ASSETS") or 0.0
    total_liabilities = (
        _find_summary(bs, "Total Liabilities") or _find_summary(bs, "TOTAL LIABILITIES") or 0.0
    )
    current_assets = _find_summary(bs, "Total Current Assets") or 0.0
    current_liabilities = _find_summary(bs, "Total Current Liabilities") or 0.0
    accounts_receivable = _sum_section(bs, "Accounts receivable")

    # -- P&L values --
    total_income = _find_summary(pnl, "Total Income") or _find_summary(pnl, "TOTAL INCOME") or 0.0
    total_cogs = (
        _find_summary(pnl, "Total Cost of Goods Sold") or _find_summary(pnl, "Total COGS") or 0.0
    )
    gross_profit = _find_summary(pnl, "Gross Profit") or (total_income - total_cogs)
    total_expenses = _find_summary(pnl, "Total Expenses") or 0.0
    net_income = _find_summary(pnl, "Net Income") or (gross_profit - total_expenses)

    prior_income = _find_summary(pnl_prior, "Total Income") or 0.0
    prior_net_income = _find_summary(pnl_prior, "Net Income") or 0.0

    # -- Margin percentages --
    gross_margin = (gross_profit / total_income * 100) if total_income else 0.0
    net_margin = (net_income / total_income * 100) if total_income else 0.0
    prior_net_margin = (prior_net_income / prior_income * 100) if prior_income else 0.0

    # -- Liquidity ratios --
    current_ratio = (current_assets / current_liabilities) if current_liabilities else None
    quick_ratio = ((cash + accounts_receivable) / current_liabilities) if current_liabilities else None

    # -- Cash runway --
    avg_monthly_burn = total_expenses / 12 if total_expenses else 0.0
    cash_runway_months = (cash / avg_monthly_burn) if avg_monthly_burn else None

    # -- AR / AP totals --
    ar_items = [li for li in _line_items(ar_aging) if not li.get("is_summary")]
    ap_items = [li for li in _line_items(ap_aging) if not li.get("is_summary")]

    ar_total = sum(to_float(li.get("Total")) for li in ar_items)
    ap_total = sum(to_float(li.get("Total")) for li in ap_items)
    ar_overdue = sum(
        to_float(li.get("1 - 30"))
        + to_float(li.get("31 - 60"))
        + to_float(li.get("61 - 90"))
        + to_float(li.get("91 and over"))
        for li in ar_items
    )

    return {
        "as_of": (bs or {}).get("end_period"),
        "currency": (bs or {}).get("currency", "USD"),
        "cash_balance": round(cash, 2),
        "total_assets": round(total_assets, 2),
        "total_liabilities": round(total_liabilities, 2),
        "current_ratio": round(current_ratio, 2) if current_ratio is not None else None,
        "quick_ratio": round(quick_ratio, 2) if quick_ratio is not None else None,
        "total_income": round(total_income, 2),
        "total_expenses": round(total_expenses, 2),
        "gross_profit": round(gross_profit, 2),
        "net_income": round(net_income, 2),
        "gross_margin_pct": round(gross_margin, 2),
        "net_margin_pct": round(net_margin, 2),
        "prior_total_income": round(prior_income, 2),
        "prior_net_income": round(prior_net_income, 2),
        "prior_net_margin_pct": round(prior_net_margin, 2),
        "income_yoy_pct": (
            round((total_income - prior_income) / prior_income * 100, 2)
            if prior_income
            else None
        ),
        "net_income_yoy_pct": (
            round((net_income - prior_net_income) / abs(prior_net_income) * 100, 2)
            if prior_net_income
            else None
        ),
        "cash_runway_months": (
            round(cash_runway_months, 1) if cash_runway_months is not None else None
        ),
        "ar_total": round(ar_total, 2),
        "ar_overdue": round(ar_overdue, 2),
        "ap_total": round(ap_total, 2),
    }
