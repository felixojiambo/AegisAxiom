"""QBO payables / weekly finance plan endpoints.

Three endpoints. All read only. All gated by
finance.working_capital.view since the report mixes the same AR and
AP lake the working capital dashboard already reads. None of these
mutate QuickBooks; none of these page n8n or trigger a sync.

  GET /finance/qbo/payables-metrics
    Returns the structured payables timing payload built from the
    synced ap_bills collection.

  GET /finance/qbo/weekly-plan
    Returns the six section weekly finance plan with payables
    metrics, AR and AP by week, raw rows, and a sync audit.

  GET /finance/qbo/weekly-plan/export
    Same content rendered as a downloadable CSV. The filename is
    derived from report type and requested date, never from a
    customer or vendor name.

Structured error codes the assistant can branch on are documented
inline. They are the only strings on the wire callers should match
against. The free text fields can drift over time.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from services.field_registry import register_module_events
from services.finance.qbo_payables_report import (
    DEFAULT_WEEK_ENDING_DAY,
    audit_qbo_finance_data_health,
    build_weekly_finance_report,
    calculate_payables_metrics,
)
from services.finance.qbo_payables_report import (
    _normalize_ap_row,  # type: ignore[attr-defined]
    _normalize_ar_row,  # type: ignore[attr-defined]
    AP_COLLECTION,
    AR_COLLECTION,
    MAX_ROWS,
)
from services.qbo_sync_service import get_finance_data_freshness, stale_after_seconds

logger = logging.getLogger(__name__)

router = APIRouter()

register_module_events("finance", "qbo_payables", [])


# Permission key. Reuses the working capital view perm because the
# weekly plan is the same data surface, expanded with week buckets
# and timing metrics.
_PERM = "finance.working_capital.view"


# Structured error codes the assistant can match on. Free text in
# detail may drift but these enum values are part of the wire
# contract.
ERR_QBO_NOT_CONNECTED = "qbo_not_connected"
ERR_QBO_SYNC_STALE = "qbo_sync_stale"
ERR_NO_PAYABLES_DATA = "no_payables_data"
ERR_MISSING_REQUIRED_FIELDS = "missing_required_fields"
ERR_FINANCE_PERMISSION_DENIED = "finance_permission_denied"
ERR_REPORT_EXPORT_FAILED = "report_export_failed"


def _check_perm(user) -> None:
    user_dict = user if isinstance(user, dict) else user.model_dump()
    if not has_perm(user_dict, _PERM):
        raise HTTPException(
            status_code=403,
            detail={
                "code": ERR_FINANCE_PERMISSION_DENIED,
                "message": f"{_PERM} permission required",
            },
        )


def _parse_as_of(as_of: Optional[str]) -> date:
    if not as_of:
        return datetime.now(timezone.utc).date()
    try:
        return datetime.strptime(as_of, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={
                "code": ERR_MISSING_REQUIRED_FIELDS,
                "message": "as_of must be YYYY-MM-DD.",
            },
        )


def _stale_threshold_hours() -> int:
    """Convert the existing sync stale threshold from seconds to
    hours for the wire response. The seconds value lives in env via
    services.qbo_sync_service.stale_after_seconds so this route does
    not hardcode a second number."""
    return max(1, int(stale_after_seconds() // 3600))


async def _read_qbo_rows(db) -> tuple[list[dict], list[dict]]:
    ap_cursor = db[AP_COLLECTION].find({}, {"_id": 0}).limit(MAX_ROWS)
    ap_raw = await ap_cursor.to_list(length=MAX_ROWS)
    ar_cursor = db[AR_COLLECTION].find({}, {"_id": 0}).limit(MAX_ROWS)
    ar_raw = await ar_cursor.to_list(length=MAX_ROWS)
    return ap_raw, ar_raw


def _tenant_id_from(user) -> Optional[str]:
    user_dict = user if isinstance(user, dict) else user.model_dump()
    return user_dict.get("tenant_id")


# ---------------------------------------------------------------------------
# JSON endpoints.
# ---------------------------------------------------------------------------


@router.get(
    "/qbo/payables-metrics",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def get_payables_metrics(
    as_of: Optional[str] = Query(default=None, description="YYYY-MM-DD. Defaults to today UTC."),
    current_user=Depends(get_current_user),
    db=Depends(get_db),
) -> dict[str, Any]:
    """Return the structured payables timing payload from the QBO lake.

    Honest about the data gap. Paid bill payment cycle requires the
    QBO BillPayment entity to have been synced, which AEGISAXIOM does not do
    today; the metric falls back to LinkedTxn or LastUpdatedTime
    proxies and reports the gap in the warnings list rather than
    fabricating a number.
    """
    _check_perm(current_user)
    as_of_date = _parse_as_of(as_of)

    sync = await get_finance_data_freshness(db)
    ap_raw, _ = await _read_qbo_rows(db)
    ap_norm = [_normalize_ap_row(r) for r in ap_raw]
    payables = calculate_payables_metrics(ap_norm, as_of_date)

    warnings: list[str] = []
    if not ap_norm:
        warnings.append(
            "No AP rows are present in the synced QBO lake. Confirm the QBO sync has run."
        )

    return {
        "as_of": as_of_date.isoformat(),
        "tenant_id": _tenant_id_from(current_user),
        "sync": {
            "last_successful_sync_at": sync.get("last_successful_sync_at"),
            "is_stale": bool(sync.get("is_stale", False)),
            "stale_threshold_hours": _stale_threshold_hours(),
            "warnings": [sync["warning"]] if sync.get("warning") else [],
        },
        "payables": payables,
        "warnings": warnings,
    }


@router.get(
    "/qbo/weekly-plan",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def get_weekly_plan(
    as_of: Optional[str] = Query(default=None, description="YYYY-MM-DD."),
    weeks: int = Query(default=8, ge=1, le=26),
    week_ending_day: str = Query(
        default="friday",
        description="Week ending day name. Default Friday.",
    ),
    current_user=Depends(get_current_user),
    db=Depends(get_db),
) -> dict[str, Any]:
    """Return the six section weekly finance plan."""
    _check_perm(current_user)
    as_of_date = _parse_as_of(as_of)

    sync = await get_finance_data_freshness(db)
    report = await build_weekly_finance_report(
        db,
        tenant_id=_tenant_id_from(current_user),
        as_of_date=as_of_date,
        week_count=weeks,
        week_ending_day=week_ending_day or DEFAULT_WEEK_ENDING_DAY,
        sync_freshness=sync,
    )

    return {
        "as_of": report["as_of"],
        "tenant_id": report["tenant_id"],
        "week_ending_day": report["week_ending_day"],
        "week_count": report["week_count"],
        "sync": {
            "last_successful_sync_at": sync.get("last_successful_sync_at"),
            "is_stale": bool(sync.get("is_stale", False)),
            "stale_threshold_hours": _stale_threshold_hours(),
            "warnings": [sync["warning"]] if sync.get("warning") else [],
        },
        "summary": report["summary"],
        "ar_weekly": report["ar_weekly"],
        "ap_weekly": report["ap_weekly"],
        "raw_ar": report["raw_ar"],
        "raw_ap": report["raw_ap"],
        "sync_audit": report["sync_audit"],
        "warnings": report["warnings"],
    }


# ---------------------------------------------------------------------------
# CSV export.
# ---------------------------------------------------------------------------


def _csv_section(writer: csv.writer, title: str) -> None:
    writer.writerow([])
    writer.writerow([f"# {title}"])


def _format_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return str(value)
    return str(value)


def _write_csv_report(report: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")

    _csv_section(w, "Summary")
    summary = report["summary"]
    timing = summary.get("payables_timing", {})
    w.writerow(["as_of", summary.get("as_of")])
    w.writerow(["qbo_sync_at", summary.get("qbo_sync_at")])
    w.writerow(["is_stale", summary.get("is_stale")])
    w.writerow(["ap_total_bill_count", summary.get("ap_total_bill_count")])
    w.writerow(["ap_open_count", summary.get("ap_open_count")])
    w.writerow(["ap_paid_count", summary.get("ap_paid_count")])
    w.writerow(["ar_total_invoice_count", summary.get("ar_total_invoice_count")])
    w.writerow(["ar_open_count", summary.get("ar_open_count")])
    w.writerow(["open_payables_total", summary.get("open_payables_total")])
    w.writerow(["overdue_open_payables_total", summary.get("overdue_open_payables_total")])
    for key in (
        "average_paid_bill_payment_days",
        "average_open_bill_age_days",
        "average_days_until_due",
    ):
        metric = timing.get(key) or {}
        w.writerow(
            [
                key,
                metric.get("value"),
                f"count={metric.get('count')}",
                f"excluded={metric.get('excluded_count')}",
                f"formula={metric.get('formula')}",
            ]
        )

    _csv_section(w, "AR Collections by Week")
    w.writerow(
        [
            "week_ending",
            "week_index",
            "customer",
            "doc_number",
            "invoice_date",
            "due_date",
            "open_balance",
        ]
    )
    for bucket in report.get("ar_weekly", []) or []:
        for r in bucket.get("rows", []) or []:
            w.writerow(
                [
                    bucket.get("week_ending"),
                    bucket.get("week_index"),
                    r.get("customer"),
                    r.get("doc_number"),
                    r.get("invoice_date"),
                    r.get("due_date"),
                    r.get("balance"),
                ]
            )
        w.writerow(
            [
                bucket.get("week_ending"),
                bucket.get("week_index"),
                "TOTAL",
                "",
                "",
                "",
                bucket.get("weekly_total"),
            ]
        )

    _csv_section(w, "AP Vendor Payments by Week — Open Bills (due)")
    w.writerow(
        [
            "week_ending",
            "week_index",
            "vendor",
            "doc_number",
            "bill_date",
            "due_date",
            "payment_date",
            "open_balance",
            "paid_amount",
        ]
    )
    ap_weekly = report.get("ap_weekly", {}) or {}
    for bucket in ap_weekly.get("open", []) or []:
        for r in bucket.get("rows", []) or []:
            w.writerow(
                [
                    bucket.get("week_ending"),
                    bucket.get("week_index"),
                    r.get("vendor"),
                    r.get("doc_number"),
                    r.get("bill_date"),
                    r.get("due_date"),
                    r.get("payment_date"),
                    r.get("balance"),
                    r.get("paid_amount"),
                ]
            )
        w.writerow(
            [
                bucket.get("week_ending"),
                bucket.get("week_index"),
                "TOTAL",
                "",
                "",
                "",
                "",
                bucket.get("weekly_total"),
                "",
            ]
        )

    _csv_section(w, "AP Vendor Payments by Week — Paid Bills (payment_date)")
    w.writerow(
        [
            "week_ending",
            "week_index",
            "vendor",
            "doc_number",
            "bill_date",
            "due_date",
            "payment_date",
            "paid_amount",
        ]
    )
    for bucket in ap_weekly.get("paid", []) or []:
        for r in bucket.get("rows", []) or []:
            w.writerow(
                [
                    bucket.get("week_ending"),
                    bucket.get("week_index"),
                    r.get("vendor"),
                    r.get("doc_number"),
                    r.get("bill_date"),
                    r.get("due_date"),
                    r.get("payment_date"),
                    r.get("paid_amount"),
                ]
            )
        w.writerow(
            [
                bucket.get("week_ending"),
                bucket.get("week_index"),
                "TOTAL",
                "",
                "",
                "",
                "",
                bucket.get("weekly_total"),
            ]
        )

    _csv_section(w, "Raw AR Rows")
    w.writerow(
        [
            "qbo_invoice_id",
            "doc_number",
            "customer",
            "invoice_date",
            "due_date",
            "balance",
            "total_amount",
            "paid_amount",
            "is_open",
        ]
    )
    for r in report.get("raw_ar", []) or []:
        w.writerow(
            [
                r.get("qbo_invoice_id"),
                r.get("doc_number"),
                r.get("customer"),
                r.get("invoice_date"),
                r.get("due_date"),
                r.get("balance"),
                r.get("total_amount"),
                r.get("paid_amount"),
                r.get("is_open"),
            ]
        )

    _csv_section(w, "Raw AP Rows")
    w.writerow(
        [
            "qbo_bill_id",
            "doc_number",
            "vendor",
            "bill_date",
            "due_date",
            "payment_date",
            "balance",
            "total_amount",
            "paid_amount",
            "is_open",
            "is_paid",
        ]
    )
    for r in report.get("raw_ap", []) or []:
        w.writerow(
            [
                r.get("qbo_bill_id"),
                r.get("doc_number"),
                r.get("vendor"),
                r.get("bill_date"),
                r.get("due_date"),
                r.get("payment_date"),
                r.get("balance"),
                r.get("total_amount"),
                r.get("paid_amount"),
                r.get("is_open"),
                r.get("is_paid"),
            ]
        )

    _csv_section(w, "Sync Audit")
    audit = report.get("sync_audit", {}) or {}
    sync = audit.get("sync", {}) or {}
    w.writerow(["last_successful_sync_at", sync.get("last_successful_sync_at")])
    w.writerow(["is_stale", sync.get("is_stale")])
    if "stale_threshold_hours" in sync:
        w.writerow(["stale_threshold_hours", sync["stale_threshold_hours"]])
    w.writerow([])
    w.writerow(["source_name", "source_kind"])
    for src in audit.get("sources", []) or []:
        w.writerow([src.get("name"), src.get("kind")])
    w.writerow([])
    w.writerow(["ap_count_key", "value"])
    for k, v in (audit.get("ap_counts", {}) or {}).items():
        w.writerow([k, v])
    w.writerow([])
    w.writerow(["ar_count_key", "value"])
    for k, v in (audit.get("ar_counts", {}) or {}).items():
        w.writerow([k, v])
    w.writerow([])
    w.writerow(["excluded_reason", "count"])
    for k, v in (audit.get("excluded_rows_by_reason", {}) or {}).items():
        w.writerow([k, v])

    return buf.getvalue()


@router.get(
    "/qbo/weekly-plan/export",
    dependencies=[Depends(require_ai_permission(_PERM))],
)
async def export_weekly_plan(
    as_of: Optional[str] = Query(default=None, description="YYYY-MM-DD."),
    weeks: int = Query(default=8, ge=1, le=26),
    week_ending_day: str = Query(default="friday"),
    current_user=Depends(get_current_user),
    db=Depends(get_db),
) -> StreamingResponse:
    """Return the weekly plan as a downloadable CSV.

    The filename is derived from the report type and the requested
    date so the download never embeds a customer or vendor name.
    Excel opens the CSV natively. The repo also has an openpyxl
    XLSX path under services/nova_engine/finance_export_service.py
    but the weekly plan crosses multiple section tables that csv
    represents cleanly enough; a future XLSX upgrade can build on
    that path without changing the route shape.
    """
    _check_perm(current_user)
    as_of_date = _parse_as_of(as_of)

    sync = await get_finance_data_freshness(db)
    try:
        report = await build_weekly_finance_report(
            db,
            tenant_id=_tenant_id_from(current_user),
            as_of_date=as_of_date,
            week_count=weeks,
            week_ending_day=week_ending_day or DEFAULT_WEEK_ENDING_DAY,
            sync_freshness=sync,
        )
        body = _write_csv_report(report)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — surface as structured error
        logger.exception("weekly plan export failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail={
                "code": ERR_REPORT_EXPORT_FAILED,
                "message": "Failed to render the weekly finance plan export.",
            },
        )

    filename = f"qbo_weekly_finance_plan_{as_of_date.isoformat()}.csv"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Report-As-Of": as_of_date.isoformat(),
    }
    return StreamingResponse(
        iter([body]),
        media_type="text/csv",
        headers=headers,
    )
