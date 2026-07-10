"""Dynamic capability summary for Nova.

Nova's intro and "what can you do?" answers must reflect the tools that
are actually registered in NOVA_TOOL_SCHEMAS, not a hand-written
paragraph that drifts as tools come and go. This module owns the
deterministic mapping from registered tool name -> capability category
and renders both a structured summary and a user-facing markdown intro.

No tenant, customer, or user names appear here. Nova stays neutral in
its self-description; tenant context is injected by the caller only when
explicitly resolved from the authenticated session.
"""
from __future__ import annotations

from typing import Iterable

from .tools.nova_tools import NOVA_TOOL_SCHEMAS


_ASSISTANT_NAME = "Nova"
_ROLE = "cash flow projection assistant and finance reporting analyst"


# Ordered list of capability categories. Each entry maps a category name
# to the tools that back it. A category is "enabled" if any of its tools
# are registered. Order matters because it drives the bullet order in the
# rendered intro.
_CATEGORY_DEFINITIONS: list[dict] = [
    {
        "category": "Cash Flow Projections",
        "tools": [
            "run_projection",
            "add_manual_assumption",
            "apply_override",
            "explain_week",
            "reset_scenario_state",
            "compare_runs",
        ],
        "description": (
            "Run baseline projections, model assumptions and overrides, "
            "compare scenario runs, and explain projection weeks. "
            "Scenario assumptions resolve recognition and cash-receipt "
            "timing deterministically (same_month / next_month) and ask "
            "for confirmation rather than guessing when the user's "
            "phrase is ambiguous."
        ),
    },
    {
        "category": "Financial Reports",
        "tools": [
            "get_pnl_summary",
            "get_balance_sheet_summary",
            "get_cash_flow_summary",
            "get_trial_balance_summary",
            "query_finance_data",
        ],
        "description": (
            "P&L, balance sheet, cash flow statement, trial balance, and "
            "ad-hoc GL aggregations from the configured finance source."
        ),
    },
    {
        "category": "Issued Invoices",
        "tools": ["query_invoice_totals"],
        "description": (
            "Total invoice amount and counts for a date range, including "
            "paid, closed, and open invoices."
        ),
    },
    {
        "category": "Open Invoices & Bills",
        "tools": [
            "get_open_invoices",
            "get_open_bills",
            "get_ar_aging",
            "get_ap_aging",
        ],
        "description": (
            "Open AR/AP detail, aging buckets, and per-row open-invoice "
            "and open-bill listings."
        ),
    },
    {
        "category": "Driver Explanations",
        "tools": ["explain_finance_drivers"],
        "description": (
            "Deterministic driver / variance breakdowns for revenue, "
            "expenses, COGS, and gross profit — month-by-month buckets, "
            "top contributing accounts, and a comparison-period variance "
            "computed from general_ledger so 'why did this change?' "
            "questions are grounded in source data."
        ),
    },
    {
        "category": "Trends & Metrics",
        "tools": [
            "get_revenue_trend",
            "get_dso_dpo_trend",
            "get_payables_timing",
            "get_top_customers_by_income",
            "get_top_vendors_by_spend",
        ],
        "description": (
            "Revenue trend, DSO/DPO trend, average payables time and DPO "
            "via get_payables_timing, and ranked customer or vendor "
            "summaries from the finance source."
        ),
    },
    {
        "category": "GL Transaction Detail",
        "tools": ["query_gl_transactions", "get_coa_lookup"],
        "description": (
            "Per-row GL transaction queries for an explicit date range "
            "and chart-of-accounts lookups."
        ),
    },
    {
        "category": "Profitability Analysis",
        "tools": ["analyze_customer_deal_finance", "analyze_fs_job_margin"],
        "description": (
            "Customer / deal profitability and field-service job margin "
            "when source data supports it."
        ),
    },
    {
        "category": "Data Freshness",
        "tools": ["get_finance_data_freshness", "get_finance_data_status"],
        "description": (
            "Report the last successful finance sync timestamp, warn if "
            "the data appears stale, and inspect actual finance "
            "collections to confirm whether data is present and usable "
            "for queries."
        ),
    },
    {
        "category": "Location Coverage",
        "tools": ["get_gl_location_coverage"],
        "description": (
            "Report how many GL rows in scope carry a populated "
            "location or department field, scoped to the same "
            "metric / category as the answer (revenue / expenses / "
            "cogs / gross_profit) so location-scoped totals can be "
            "quoted with the unattributed remainder disclosed."
        ),
    },
]


# Limitations Nova must surface to the user even when a related tool
# exists, so they understand what Nova is NOT allowed to do.
_BASE_LIMITATIONS: list[str] = [
    "Nova does not trigger a QuickBooks sync. Ask an admin to start one if data is stale.",
    "Nova does not modify accounting records, post journal entries, or change source data.",
    "Nova does not perform free-form arithmetic on tool output; numbers come from deterministic tools.",
]


_BASE_GUARDRAILS: list[str] = [
    "Nova quotes deterministic tool output; Nova does not invent totals.",
    "Nova does not trigger QBO sync directly.",
    "Nova warns if finance data is stale.",
]


def _registered_tool_names(schemas: Iterable[dict] | None = None) -> set[str]:
    schemas = schemas if schemas is not None else NOVA_TOOL_SCHEMAS
    return {schema["name"] for schema in schemas if isinstance(schema, dict) and "name" in schema}


def _detect_export_endpoint_available() -> bool:
    """True iff the Nova exports route module is importable.

    The exports surface is an HTTP endpoint, not a Nova chat tool, so
    it doesn't appear in NOVA_TOOL_SCHEMAS. Capability gating still
    needs a signal — importability of the route module is the
    closest proxy for "the deployment ships exports". The check is
    cheap (the import is cached after the first call) and fails
    silently to False when the module is missing.
    """
    try:
        import importlib

        importlib.import_module("routes.nova_exports")
        return True
    except Exception:  # noqa: BLE001 — any import failure means "not available"
        return False


def build_nova_capability_summary(
    available_tools: list[str] | None = None,
    include_details: bool = True,
    export_available: bool | None = None,
) -> dict:
    """Return a structured capability summary derived from registered tools.

    Args:
        available_tools: Optional override of the tool name set. When
            None, the live NOVA_TOOL_SCHEMAS registry is used. Tests
            and runtime feature gates pass an explicit list.
        include_details: When False, omit per-category tool lists and
            description strings (kept for compact UI surfaces).
        export_available: Optional override for the "exports endpoint
            is registered" signal. When None, the helper auto-detects
            via routes.nova_exports importability. Tests pass True /
            False explicitly so the gating contract can be pinned
            without depending on the route module being importable in
            the test environment.
    """
    if available_tools is None:
        registered = _registered_tool_names()
    else:
        registered = set(available_tools)

    if export_available is None:
        export_available = _detect_export_endpoint_available()

    capabilities: list[dict] = []
    missing_categories: list[str] = []

    for definition in _CATEGORY_DEFINITIONS:
        backing_tools = [t for t in definition["tools"] if t in registered]
        enabled = bool(backing_tools)
        if not enabled:
            missing_categories.append(definition["category"])
            continue
        entry: dict = {
            "category": definition["category"],
            "enabled": True,
        }
        if include_details:
            entry["tools"] = backing_tools
            entry["description"] = definition["description"]
        capabilities.append(entry)

    # Exports — gated on BOTH the create_finance_export chat tool AND
    # the route module being importable. Three states:
    #   * chat tool registered + route present → "Chat-native export available."
    #   * route present, chat tool missing     → "Export endpoint available, chat-native export pending."
    #   * neither                               → category dropped + listed in limitations.
    chat_export_tool_registered = "create_finance_export" in registered
    if chat_export_tool_registered or export_available:
        export_entry: dict = {
            "category": "Exports",
            "enabled": True,
        }
        if include_details:
            chat_native = chat_export_tool_registered and export_available
            if chat_native:
                export_tools = ["create_finance_export"]
                description = (
                    "Create downloadable CSV / XLSX exports directly "
                    "from chat for supported finance tool results. "
                    "Nova re-runs the source tool server-side, "
                    "persists the file, and renders a download card "
                    "in the chat message — no manual API call needed."
                )
            elif chat_export_tool_registered:
                export_tools = ["create_finance_export"]
                description = (
                    "Chat-native export tool registered but the "
                    "download endpoint is missing in this deployment; "
                    "downloads may not work until the route is "
                    "available."
                )
            else:
                export_tools = []
                description = (
                    "Export endpoint available, chat-native export "
                    "pending. CSV / XLSX exports are reachable via "
                    "POST /api/nova/exports for API callers; the "
                    "chat tool that creates downloads inline is not "
                    "registered yet."
                )
            export_entry["tools"] = export_tools
            export_entry["description"] = description
        capabilities.append(export_entry)
    else:
        missing_categories.append("Exports")

    limitations = list(_BASE_LIMITATIONS)
    for category in missing_categories:
        limitations.append(
            f"{category} is not available in this environment because the backing tools are not registered."
        )

    return {
        "assistant_name": _ASSISTANT_NAME,
        "role": _ROLE,
        "capabilities": capabilities,
        "limitations": limitations,
        "guardrails": list(_BASE_GUARDRAILS),
    }


def render_nova_intro(
    available_tools: list[str] | None = None,
) -> str:
    """Render the user-facing intro markdown from the capability summary.

    Output is intentionally neutral: no tenant name, no user name, no
    company name. Callers may inject tenant context separately if it has
    been resolved from an authenticated session.
    """
    summary = build_nova_capability_summary(available_tools=available_tools, include_details=True)

    lines: list[str] = []
    lines.append(f"I'm {summary['assistant_name']}, your {summary['role']}.")
    lines.append("")

    if summary["capabilities"]:
        lines.append("I can help with the finance capabilities currently enabled in this environment:")
        lines.append("")
        for entry in summary["capabilities"]:
            lines.append(f"- **{entry['category']}** — {entry['description']}")
        lines.append("")
    else:
        lines.append(
            "No finance tools are registered in this environment, so I cannot answer "
            "finance questions until tools are configured."
        )
        lines.append("")

    lines.append(
        "I quote numbers from approved tools and source data. I do not invent totals, "
        "and I will warn you if the finance data appears stale."
    )
    lines.append("")
    lines.append("What finance question would you like to review today?")

    return "\n".join(lines).rstrip() + "\n"
