"""Tool schemas + dispatch for the Nova chat agent.

Ten tools for v1:
- run_projection: run the engine with current state
- apply_override: add an Override (new_cost, new_revenue, shift_payment,
  remove_payment) to the running scenario state. Call run_projection
  afterwards to see updated results
- add_manual_assumption: add a ManualCostAssumption or
  RecurringRevenueAssumption to the running scenario state
- explain_week: return structured WeekRow breakdown for a specific
  week, used by the LLM to narrate without doing arithmetic
- query_finance_data: return server-side QBO general-ledger aggregations
  for historical actuals outside the projection scope; also serves
  snapshot-backed metrics (ar_total / ap_total / ar_aging / ap_aging)
- reset_scenario_state: clear scenario assumptions/overrides/modifiers
- compare_runs: diff two persisted projection runs by KPI + worst weeks
- analyze_customer_deal_finance: rank customers or deals by
  AR-invoiced revenue, contracted cost, or margin
- analyze_fs_job_margin: per-field-service-job revenue / direct_cost /
  gross_profit / margin_percent (single job or ranked list)
- detect_wholesale_revenue: deterministic rules-only classifier over
  GL prefix 50400 and AEGISAXIOM structured wholesale_mrr fields

Tool schemas follow Gemini-native function-declaration format (matches
services/ai/agents/tools/conversational_tools.py:TOOL_SCHEMAS).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional, get_args

# Soft scan cap for query_gl_transactions. With the (tx_date, basis)
# index covering the canonical $match shape, a typical month-window
# query reads a few thousand rows. 50k is a generous ceiling that
# lets year-window queries succeed without OOM risk. If hit, the
# tool response notes the truncation. Module-level so callers /
# tests can import it instead of duplicating the literal.
_SCAN_CAP = 50_000

from pydantic import ValidationError

from models import FIELD_JOB_STATUSES
from services.nova_engine._gl_helpers import _safe_float
from services.nova_engine.account_categories import Category
from services.nova_engine.formulas import (
    COGS_CATEGORIES,
    GROSS_PROFIT_FORMULA_SOURCE_GL,
    GROSS_PROFIT_FORMULA_VERSION,
    REVENUE_CATEGORIES,
    compute_reversal_metadata,
)
from services.nova_engine.models import (
    InsufficientHistoryError,
    ManualCostAssumption,
    Override,
    RecurringRevenueAssumption,
)


# Canonical list of categories the dispatcher will accept as a category
# filter, derived directly from account_categories.Category so the schema
# never drifts from the source of truth. categorize() returns one of these
# values for every GL row.
_CATEGORY_VALUES: tuple[str, ...] = tuple(get_args(Category))


logger = logging.getLogger(__name__)

MRR_MAPPING_UNAVAILABLE_MESSAGE = (
    "Existing MRR could not be derived from QBO. The MRR account mapping "
    "(50300 Contract Commission Income) is configured, but no rows posted "
    "to that account in EITHER the prior-year same-month query window OR "
    "the recent historical fallback window. The tenant either hasn't "
    "booked recurring revenue yet, the QBO sync is stale, or the account "
    "is named differently in this tenant's chart of accounts. Provide a "
    "manual MRR assumption for the forecast months to proceed, or check "
    "that 50300 has activity in the months Nova queried. See the "
    "`mrr_baseline` block on this response for the exact lookup windows "
    "and row counts."
)


# YYYY-MM with month in 01-12. Shared between the FastAPI route and the
# tool dispatcher so format errors surface identically.
_PROJECTION_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _is_valid_projection_month(value: Any) -> bool:
    """Validate projection_start_month format (YYYY-MM, valid month).

    Used at both the FastAPI route and the tool dispatcher to reject
    malformed input early with a clean error instead of letting
    malformed strings reach the engine.
    """
    return isinstance(value, str) and bool(_PROJECTION_MONTH_PATTERN.match(value))


_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_valid_date(value: Any) -> bool:
    """Validate YYYY-MM-DD format and reject invalid calendar dates.

    Used for date_from / date_to in query_finance_data to reject
    malformed input early with a clean error instead of returning
    silently empty MongoDB results for invalid calendar dates
    like Feb 31.
    """
    if not isinstance(value, str) or not _DATE_PATTERN.match(value):
        return False

    from datetime import datetime

    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# query_gl_transactions helpers
# ---------------------------------------------------------------------------
# Used by the GL transaction-detail tool. Kept module-level (not nested
# inside the dispatch branch) so the unit tests can exercise the
# blank-location and category-membership rules in isolation.

# Categories from account_categories.categorize() that count toward
# user-facing income totals. Sourced explicitly from the Category Literal
# rather than hard-coded strings, so a future rename in
# account_categories.py surfaces here as a Literal validation error
# instead of silently dropping rows.
_INCOME_CATEGORIES: frozenset[str] = frozenset({
    "PS_REVENUE",
    "FS_REVENUE",
    "MRR",
    "WHOLESALE_REVENUE",
    "OTHER_REVENUE",
})

# Location-like fields on a GL row that, if all blank, mark the row as
# blank-location. The AEGISAXIOM GL ingestion landed both id and name flavours
# for location and department, so all four must be checked — a row with
# location_id populated but location_name null still has a location and
# must NOT be returned by the blank-location filter.
# class_name is intentionally absent: Class and Location are distinct
# QBO concepts and conflating them would silently mis-bucket rows.
_LOCATION_LIKE_FIELDS: tuple[str, ...] = (
    "location",
    "location_id",
    "location_name",
    "department",
    "department_name",
    "department_ref_id",
    "department_ref_name",
)
# Nested QBO raw-data paths covered by the spec. Absent from the AEGISAXIOM
# flat schema today; checked defensively so a future raw-data ingestion
# pass-through is handled without code change.
_LOCATION_LIKE_NESTED_PATHS: tuple[tuple[str, ...], ...] = (
    ("raw_data", "DepartmentRef"),
    ("raw_data", "Line", "DepartmentRef"),
)


def _value_at_path(doc: dict, path: tuple[str, ...], _index: int = 0) -> Any:
    """Read a nested field by path. Returns None if any segment is missing
    or the intermediate value is not a dict / not a list at the right
    place. Used by the nested location-like checks; flat top-level fields
    use ``doc.get(name)`` directly.

    The ``_index`` parameter tracks position within ``path`` across
    recursive calls into list items so each recursion advances past the
    segment that triggered the list-walk. The previous implementation
    sliced ``path[path.index(segment):]`` which both relies on
    ``path.index`` (returning the first occurrence — wrong if a path
    contains a repeated segment) and DOES NOT advance past the segment,
    so list traversal would loop forever conceptually but in practice
    returned the unchanged input dict on every recursion and yielded
    None for every nested raw_data.Line.* lookup.
    """
    cur: Any = doc
    for i in range(_index, len(path)):
        segment = path[i]
        if isinstance(cur, dict):
            cur = cur.get(segment)
        elif isinstance(cur, list):
            # raw_data.Line is a list of line dicts. Apply the
            # remaining path to each line, advancing PAST the current
            # segment so list elements are descended into.
            results = [_value_at_path(item, path, i) for item in cur]
            return results if results else None
        else:
            return None
    return cur


def _is_blank_value(v: Any) -> bool:
    """True for values that mean 'no location/department on this row'.

    Covers None, empty string, whitespace-only string, and a DepartmentRef
    (or LocationRef) dict whose ``name``, ``id``, and ``value`` are all
    blank or missing. Lists are blank when every element is blank.
    """
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ""
    if isinstance(v, dict):
        # QBO ref shape: {"name": "...", "id": "...", "value": "..."}
        return all(
            _is_blank_value(v.get(k)) for k in ("name", "id", "value")
        )
    if isinstance(v, list):
        return all(_is_blank_value(item) for item in v)
    # Anything else (int, float, bool) is treated as populated - we'd
    # rather miss a real blank than silently bucket a populated value
    # into the blank pile.
    return False


def _is_blank_location(row: dict) -> bool:
    """All location-like fields on the row are blank.

    A row passes if every field listed in _LOCATION_LIKE_FIELDS and every
    nested path in _LOCATION_LIKE_NESTED_PATHS evaluates blank. Fields
    that don't exist on the row count as blank (absence = blank by spec).
    """
    for name in _LOCATION_LIKE_FIELDS:
        if name in row and not _is_blank_value(row.get(name)):
            return False
    for path in _LOCATION_LIKE_NESTED_PATHS:
        v = _value_at_path(row, path)
        if v is not None and not _is_blank_value(v):
            return False
    return True


# Per-tenant TTL cache for the location-field probe. The probe used to
# run on every query_gl_transactions invocation — one extra Mongo round
# trip per call, even though the answer changes only when the tenant's
# QBO sync schema changes. 5 minutes is short enough that a sync that
# starts populating location fields takes effect quickly, and long
# enough that bursty Nova usage doesn't hammer the index.
#
# Keyed by Motor db.name (one db per tenant in the AEGISAXIOM layout) so a
# single process serving multiple tenants caches each independently.
# id() falls back when a future adapter doesn't expose a name.
_LOCATION_FIELD_STATUS_CACHE: dict[str, tuple[str, float]] = {}
_LOCATION_FIELD_STATUS_TTL_SECONDS = 300.0


def _location_field_status_cache_key(qbo_db: Any) -> str:
    name = getattr(qbo_db, "name", None)
    if isinstance(name, str) and name:
        return name
    return f"id:{id(qbo_db)}"


def _reset_location_field_status_cache() -> None:
    """Test hook — clears the cache so suite runs don't bleed state."""
    _LOCATION_FIELD_STATUS_CACHE.clear()


async def _check_location_field_status(qbo_db: Any) -> str:
    """Return ``"available"`` iff ANY document in general_ledger carries
    at least one location-like field, else ``"unavailable"``.

    Result is cached per tenant for ``_LOCATION_FIELD_STATUS_TTL_SECONDS``
    seconds. The probe issues one ``find_one`` with an ``$or`` of
    ``$exists`` checks across every flat field in _LOCATION_LIKE_FIELDS
    plus the nested raw_data roots, which short-circuits the moment
    Mongo finds any matching document. Cheap; touches a single index
    entry per OR clause when indexes exist, falls back to a partial
    scan otherwise.
    """
    cache_key = _location_field_status_cache_key(qbo_db)
    now = time.monotonic()
    cached = _LOCATION_FIELD_STATUS_CACHE.get(cache_key)
    if cached is not None and cached[1] > now:
        return cached[0]

    or_clauses: list[dict[str, Any]] = [
        {name: {"$exists": True}} for name in _LOCATION_LIKE_FIELDS
    ]
    for path in _LOCATION_LIKE_NESTED_PATHS:
        # The raw_data root is enough — if the doc carries raw_data at
        # all, the canonical QBO ref dict shape lives under it.
        or_clauses.append({path[0]: {"$exists": True}})
    sample = await qbo_db.general_ledger.find_one({"$or": or_clauses})
    status = "available" if sample is not None else "unavailable"
    _LOCATION_FIELD_STATUS_CACHE[cache_key] = (
        status,
        now + _LOCATION_FIELD_STATUS_TTL_SECONDS,
    )
    return status


def _is_mrr_history_error(error: InsufficientHistoryError) -> bool:
    return "mrr" in (getattr(error, "line", "") or "").lower()


def _insufficient_history_tool_error(error: InsufficientHistoryError) -> dict[str, Any]:
    if _is_mrr_history_error(error):
        payload: dict[str, Any] = {
            "error": "mrr_mapping_unavailable",
            "message": MRR_MAPPING_UNAVAILABLE_MESSAGE,
            "detail": MRR_MAPPING_UNAVAILABLE_MESSAGE,
            "affected_line": error.line,
            "available_months": error.available_months,
            "required_months": error.required_months,
        }
        # When data_loader attached the MRR baseline audit block,
        # surface it so the chat tool can quote the actual lookup
        # window, row counts, and fallback outcome instead of the
        # generic "mapping unavailable" sentence.
        if getattr(error, "metadata", None):
            payload["mrr_baseline"] = error.metadata
        return payload

    return {
        "error": "insufficient_history",
        "detail": str(error),
        "affected_line": error.line,
        "available_months": error.available_months,
        "required_months": error.required_months,
    }


# Hard cap on per-call rows from query_finance_data's GL aggregation.
# Mirrors data_loader._GL_QUERY_LIMIT pattern: a logged warning fires
# at the cap so a deployment that needs more sees a clear signal.
_GL_AGGREGATION_LIMIT = 10_000


# ---------------------------------------------------------------------------
# query_finance_data metric registry
# ---------------------------------------------------------------------------
# Three buckets:
#
#   _GL_DERIVABLE_METRICS  Metrics computed from qbo_db.general_ledger via
#                          the existing aggregation pipeline. These are
#                          safe to expose because every row carries amount
#                          and account_name, and categorize() maps them to
#                          known buckets.
#
#   _UNAVAILABLE_METRICS   Metric names the LLM is allowed to ask for but
#                          AEGISAXIOM doesn't have a clean source for yet. The
#                          dispatcher returns ``unavailable_data`` with a
#                          dashboard hint so Nova can redirect the user
#                          rather than fabricate a number.
#
#   _METRIC_ALIASES        Plain-English variants the LLM keeps reaching for.
#                          Resolving aliases up front lets the schema enum
#                          accept "expenses"/"gross_margin" without the
#                          dispatcher carrying duplicate code paths.
#
# Anything not in any bucket falls through to ``unknown_metric`` so the
# LLM gets a structured error instead of silent zero rows.

_METRIC_ALIASES: dict[str, str] = {
    # The LLM tends to oscillate between singular/plural and the more
    # natural "gross margin" wording. Keep both reachable.
    "expenses": "expense",
    "gross_margin": "gross_profit_margin",
}

# Derived from models.FIELD_JOB_STATUSES rather than hand-synced.
# Used by analyze_fs_job_margin to validate the caller's status
# filter so a typo / LLM-fabricated value returns a structured error
# instead of silently matching zero rows.
_FS_JOB_STATUSES: frozenset[str] = frozenset(FIELD_JOB_STATUSES)

_GL_DERIVABLE_METRICS: set[str] = {
    "revenue",
    "expense",
    "gross_profit",
    "gross_profit_margin",
    "expense_ratio",
    "transaction_count",
    # Per-category roll-ups
    "payroll",
    "admin_expenses",
    # Current-calendar-month single-bucket roll-ups
    "current_month_revenue",
    "current_month_expenses",
    "current_month_net_change",
}

# Metrics served from the finance_snapshots collection (refreshed hourly
# by services/nova_engine/finance_snapshots.refresh_finance_snapshots).
# These ignore date_from/date_to/group_by because the snapshot is a
# single point-in-time aggregate, not a time series.
_SNAPSHOT_METRICS: set[str] = {
    "ar_total",
    "ap_total",
    "ar_aging",
    "ap_aging",
}

# Values are the message we return to the LLM. Keep them actionable - the
# point is to redirect Nova to the dashboard the user actually needs,
# not just say "no."
_UNAVAILABLE_METRICS: dict[str, dict[str, str]] = {
    "rent": {
        "detail": (
            "RENT is not currently mapped to any QBO account category in "
            "services/nova_engine/account_categories.py. Until a CATEGORY_RULES "
            "entry is added for the rent account prefix, this metric cannot be "
            "computed from the general ledger."
        ),
        "hint": (
            "If the user needs rent specifically, suggest looking at the "
            "P&L dashboard (/api/finance/profit-and-loss) or filter "
            "query_finance_data with account_prefix to the QBO rent account."
        ),
    },
}

# Combined set used for schema-enum + dispatcher-level validation.
_VALID_METRICS: set[str] = (
    _GL_DERIVABLE_METRICS
    | _SNAPSHOT_METRICS
    | set(_UNAVAILABLE_METRICS.keys())
    | set(_METRIC_ALIASES.keys())
)

# Metrics that ignore the caller's date_from/date_to/group_by because they
# are inherently scoped to the current calendar month.
_CURRENT_MONTH_METRICS: set[str] = {
    "current_month_revenue",
    "current_month_expenses",
    "current_month_net_change",
}


# ---------------------------------------------------------------------------
# Cashflow chart artifact
# ---------------------------------------------------------------------------
# When run_projection succeeds, persist a cashflow_chart artifact alongside
# the projection_run doc so the agent can embed [[artifact:<id>]] inline
# and the frontend's ChatArtifact dispatcher can render the curve. The
# chart complements (does NOT replace) the markdown table in the agent's
# reply.
#
# The artifact's Mongo doc shape mirrors conversational_tools.py's
# Vega/Orion convention (artifact_id, session_id, user_id, employee_id,
# artifact_type, payload, status, created_*).


def build_cashflow_chart_payload(run: dict) -> dict:
    """Build the cashflow_chart artifact payload from a saved run doc.

    Returns the payload only - the dict that goes into the artifact's
    `payload` Mongo field. Persistence (artifact_id, timestamps, etc.)
    is the caller's responsibility, mirroring the conversational_tools
    helper shape.
    """
    inputs = run.get("inputs", {}) or {}
    output = run.get("output", {}) or {}
    weeks_in = output.get("weeks", []) or []
    return {
        "projection_run_id": run.get("projection_run_id"),
        "starting_cash": inputs.get("starting_cash"),
        "horizon_weeks": inputs.get("horizon_weeks", 13),
        "weeks": [
            {
                "week_number": w.get("week_number"),
                "week_starting": w.get("week_starting"),
                "ending_cash": w.get("ending_cash"),
                "revenue": w.get("total_revenue", 0),
                "total_costs": w.get("total_costs", 0),
            }
            for w in weeks_in
        ],
    }


async def _persist_cashflow_chart_artifact(
    run: dict,
    user_id: str,
    session_id: str | None,
    employee_id: str = "ai_nova",
    db=None,
) -> str:
    """Insert the cashflow_chart artifact doc into ai_chat_artifacts.

    Returns the new artifact_id. Mirrors conversational_tools._handle_render_artifact's
    insert shape exactly so the frontend's ChatArtifact dispatcher can
    fetch and render this artifact alongside Vega/Orion/SOW artifacts
    without any pipeline divergence.
    """
    import uuid
    from datetime import datetime, timezone

    if db is None:
        from services.nova_engine.db import get_app_db
        db = get_app_db()

    artifact_id = f"art_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    artifact_doc = {
        "artifact_id": artifact_id,
        "session_id": session_id,
        "user_id": user_id,
        "employee_id": employee_id,
        "artifact_type": "cashflow_chart",
        "payload": build_cashflow_chart_payload(run),
        "status": "delivered",
        "created_by_agent": employee_id,
        "created_at": now,
        "updated_at": now,
    }
    await db.ai_chat_artifacts.insert_one(artifact_doc)
    logger.info(
        "Created cashflow_chart artifact %s for run %s",
        artifact_id, run.get("projection_run_id"),
    )
    return artifact_id


NOVA_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "run_projection",
        "description": (
            "Run the cash flow projection engine. Use this when the user "
            "asks for a baseline projection or after applying overrides/"
            "assumptions to see updated results. Returns weekly rows with "
            "ending cash, revenue, costs, and a summary."
            "\n\n"
            "Successful responses may include a top-level `mrr_baseline` "
            "block — the deterministic audit metadata from the data "
            "loader. When present, use it to explain how baseline MRR "
            "was derived: quote `basis`, `account_filter` "
            "(code_prefix=50300 by default, override via "
            "`mrr_account_prefix`), the prior-year same-month "
            "`lookup_window` and `row_count`, the overall "
            "`fallback_method` "
            "(prior_year_same_month / historical_run_rate / mixed / "
            "no_history / partial_no_history), the `fallback.months_used` "
            "list, and `fallback.run_rate_monthly_amount`. Quote "
            "`fallback_method_label` verbatim when the fallback was "
            "used — it labels the run-rate as 'historical contract "
            "commission run-rate', NOT guaranteed fixed MRR. Do NOT "
            "say MRR is unavailable when this block is present and "
            "carries a usable baseline; the engine has already filled "
            "in the missing months from recent 50300 activity."
            "\n\n"
            "Dynamic projection inputs (all OPTIONAL; pass only what "
            "the user explicitly named): `account_average_prefixes` "
            "(list of GL code prefixes — no default list), "
            "`mrr_account_prefix` (override the 50300 default), "
            "`use_mrr_fallback` (default true), `ar_receipts_timing` "
            "(`{method: 'due_date_plus_days', days_after_due_date: N}`), "
            "`ap_payments_timing` (`{method: 'due_date'}`), and "
            "`payroll_schedule` (`{amount, start_date, frequency}`). "
            "The response carries matching top-level metadata blocks "
            "(`account_average_baselines`, `ar_receipts_schedule`, "
            "`ap_payments_schedule`, `payroll_schedule`) when those "
            "inputs are supplied. Quote them verbatim and surface "
            "their `warnings` lists. AR receipts and AP payments are "
            "ADDITIVE to the baseline (existing receivables/bills are "
            "distinct cash flows from projected new revenue/costs); "
            "account averages are also ADDITIVE manual cost "
            "assumptions."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "starting_cash": {
                    "type": "NUMBER",
                    "description": "Cash on hand at projection start (USD).",
                },
                "projection_start_month": {
                    "type": "STRING",
                    "description": (
                        "Month to start projecting from, format YYYY-MM. "
                        "MUST be the user's intended forecast start month. "
                        "When the user provides a payroll start_date, the "
                        "year/month here MUST match the payroll start's "
                        "year/month — otherwise run_projection returns a "
                        "projection_date_mismatch error. Do NOT pick a "
                        "year from training data; use the date anchor "
                        "supplied at the top of the system prompt and "
                        "the explicit dates the user named in their turn."
                    ),
                },
                "horizon_weeks": {
                    "type": "INTEGER",
                    "description": "Number of weeks to project. Default 13.",
                },
                "manual_cost_assumptions": {
                    "type": "ARRAY",
                    "description": "Optional monthly manual cost assumptions to include in this run.",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "name": {"type": "STRING"},
                            "amount_per_month": {"type": "NUMBER"},
                            "line_type": {
                                "type": "STRING",
                                "enum": ["PS_COGS", "FS_COGS", "PAYROLL", "RENT", "ADMIN_EXPENSES", "OTHER_COSTS"],
                            },
                            "starts_month": {"type": "STRING", "description": "YYYY-MM or null."},
                            "ends_month": {"type": "STRING", "description": "YYYY-MM or null."},
                            "weekly_dist": {
                                "type": "OBJECT",
                                "description": "Optional week1_pct/week2_pct/week3_pct/week4_pct distribution.",
                            },
                        },
                        "required": ["name", "amount_per_month", "line_type"],
                    },
                },
                "manual_recurring_revenue": {
                    "type": "ARRAY",
                    "description": "Optional monthly manual recurring revenue assumptions. Use line_type MRR for manual MRR; amount_per_month 0 is accepted as intentional.",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "name": {"type": "STRING"},
                            "amount_per_month": {"type": "NUMBER"},
                            "line_type": {
                                "type": "STRING",
                                "enum": ["PS_REVENUE", "FS_REVENUE", "MRR", "WHOLESALE", "OTHER_REVENUE"],
                            },
                            "starts_month": {"type": "STRING", "description": "YYYY-MM or null."},
                            "ends_month": {"type": "STRING", "description": "YYYY-MM or null."},
                            "weekly_dist": {
                                "type": "OBJECT",
                                "description": "Optional week1_pct/week2_pct/week3_pct/week4_pct distribution.",
                            },
                        },
                        "required": ["name", "amount_per_month", "line_type"],
                    },
                },
                "overrides": {
                    "type": "ARRAY",
                    "description": "Optional one-off scenario overrides to apply before running.",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "type": {
                                "type": "STRING",
                                "enum": ["new_cost", "new_revenue", "shift_payment", "remove_payment"],
                            },
                            "target": {"type": "STRING"},
                            "week_number": {"type": "INTEGER"},
                            "amount": {"type": "NUMBER"},
                            "source_week": {"type": "INTEGER"},
                            "shift_weeks": {"type": "INTEGER"},
                        },
                        "required": ["type", "target"],
                    },
                },
                "sales_modifier": {
                    "type": "NUMBER",
                    "description": "Revenue multiplier. Default 1.0.",
                },
                "cost_modifier": {
                    "type": "NUMBER",
                    "description": "Cost multiplier. Default 1.0.",
                },
                "account_average_prefixes": {
                    "type": "ARRAY",
                    "description": (
                        "Optional list of GL account-code prefixes the "
                        "user named for cost averaging (e.g. "
                        "['90000', '90100']). The engine computes a "
                        "last-3-complete-month average per prefix and "
                        "applies each as an ADDITIVE manual cost "
                        "assumption. There is NO default list — pass "
                        "only what the user provided."
                    ),
                    "items": {"type": "STRING"},
                },
                "mrr_account_prefix": {
                    "type": "STRING",
                    "description": (
                        "Optional override for the MRR account-code "
                        "prefix. Defaults to the product's existing "
                        "'50300' mapping when omitted."
                    ),
                },
                "use_mrr_fallback": {
                    "type": "BOOLEAN",
                    "description": (
                        "When true (default), MRR falls back to the "
                        "3-month historical run-rate if prior-year "
                        "same-month has no activity. Set to false to "
                        "require an explicit manual MRR assumption."
                    ),
                },
                "ar_receipts_timing": {
                    "type": "OBJECT",
                    "description": (
                        "Optional AR collection timing rule. "
                        "Schedules open-invoice receipts as dated cash "
                        "events ADDITIVE to the revenue baseline."
                    ),
                    "properties": {
                        "method": {
                            "type": "STRING",
                            "enum": ["due_date_plus_days"],
                        },
                        "days_after_due_date": {"type": "INTEGER"},
                    },
                    "required": ["method"],
                },
                "ap_payments_timing": {
                    "type": "OBJECT",
                    "description": (
                        "Optional AP payment timing rule. Schedules "
                        "open-bill payments as dated cash events "
                        "ADDITIVE to the cost baseline."
                    ),
                    "properties": {
                        "method": {
                            "type": "STRING",
                            "enum": ["due_date"],
                        },
                    },
                    "required": ["method"],
                },
                "payroll_schedule": {
                    "type": "OBJECT",
                    "description": (
                        "Optional explicit payroll cadence. Generates "
                        "dated cash outflows on each pay date. "
                        "'amount', 'start_date', and 'frequency' all "
                        "come from the user."
                    ),
                    "properties": {
                        "amount": {"type": "NUMBER"},
                        "start_date": {
                            "type": "STRING",
                            "description": "YYYY-MM-DD.",
                        },
                        "frequency": {
                            "type": "STRING",
                            "enum": ["biweekly", "weekly", "monthly"],
                        },
                    },
                    "required": ["amount", "start_date", "frequency"],
                },
            },
            "required": ["starting_cash", "projection_start_month"],
        },
    },
    {
        "name": "apply_override",
        "description": (
            "Add a one-off override to the current scenario. Call "
            "run_projection afterwards to see the updated result. Use for "
            "what-if questions like 'what if we delay the AP payment 2 "
            "weeks' (shift_payment) or 'what if we add a $5K expense in "
            "week 3' (new_cost). The override targets a specific week."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "type": {
                    "type": "STRING",
                    "enum": ["new_cost", "new_revenue", "shift_payment", "remove_payment"],
                },
                "target": {
                    "type": "STRING",
                    "description": "What category the override affects (e.g., PAYROLL, ADMINISTRATIVE_EXPENSES, PS_REVENUE).",
                },
                "week_number": {
                    "type": "INTEGER",
                    "description": "1-based week number (required for new_cost/new_revenue).",
                },
                "amount": {
                    "type": "NUMBER",
                    "description": "Dollar amount (required for new_cost/new_revenue).",
                },
                "source_week": {
                    "type": "INTEGER",
                    "description": "1-based week the override anchors to (required for shift_payment/remove_payment).",
                },
                "shift_weeks": {
                    "type": "INTEGER",
                    "description": "Number of weeks to shift forward (required for shift_payment).",
                },
            },
            "required": ["type", "target"],
        },
    },
    {
        "name": "add_manual_assumption",
        "description": (
            "Add a recurring monthly cost or revenue assumption to the "
            "scenario. Use for 'we plan to hire someone at $12K/mo "
            "starting June' (manual cost) or 'we expect $8K/mo new "
            "MRR from Q3' (manual recurring revenue). Affects every "
            "month in the active range. "
            "\n\n"
            "Timing fields: starts_month (YYYY-MM, required) sets the "
            "RECOGNITION month. cash_receipt_timing controls when cash "
            "actually moves: 'same_month' (default, identical to prior "
            "behavior) lands cash in the recognition month; "
            "'next_month' shifts the cash impact one month later — "
            "use for invoiced revenue/costs on NET-30-style terms. "
            "If timing is ambiguous (e.g. user said 'starting next "
            "month' without confirming cash receipt timing), the tool "
            "returns requires_confirmation=true with a "
            "confirmation_question; do NOT guess. The response always "
            "echoes timing_summary so the assistant can quote the "
            "exact recognition / cash months that were applied."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "kind": {
                    "type": "STRING",
                    "enum": ["cost", "revenue"],
                    "description": "'cost' for ManualCostAssumption, 'revenue' for RecurringRevenueAssumption.",
                },
                "name": {
                    "type": "STRING",
                    "description": "Human-readable label (e.g., 'senior eng hire', 'new MRR pipeline').",
                },
                "amount_per_month": {
                    "type": "NUMBER",
                },
                "line_type": {
                    "type": "STRING",
                    "description": (
                        "For cost: PS_COGS, FS_COGS, PAYROLL, RENT, ADMIN_EXPENSES, "
                        "OTHER_COSTS. For revenue: PS_REVENUE, FS_REVENUE, MRR, "
                        "WHOLESALE, OTHER_REVENUE."
                    ),
                },
                "starts_month": {
                    "type": "STRING",
                    "description": (
                        "Recognition start month, format YYYY-MM. May be omitted "
                        "if a timing_phrase is supplied; the tool then derives "
                        "the month deterministically from current_date / "
                        "projection_start_month, or returns "
                        "requires_confirmation if the inputs are ambiguous."
                    ),
                },
                "ends_month": {
                    "type": "STRING",
                    "description": "Format YYYY-MM, or null if open-ended.",
                },
                "weekly_dist": {
                    "type": "OBJECT",
                    "description": "Optional week1_pct/week2_pct/week3_pct/week4_pct distribution.",
                },
                "cash_receipt_timing": {
                    "type": "STRING",
                    "enum": ["same_month", "next_month"],
                    "description": (
                        "When the cash impact lands relative to the "
                        "recognition month. Defaults to 'same_month' "
                        "(no behavior change for callers that omit it). "
                        "Use 'next_month' for AR-backed revenue or "
                        "vendor invoices on NET-30 terms."
                    ),
                },
                "timing_phrase": {
                    "type": "STRING",
                    "description": (
                        "Optional natural-language timing hint (e.g. "
                        "'starting next month', 'starting this month'). "
                        "The tool resolves it deterministically using "
                        "the planner-supplied current_date / "
                        "projection_start_month. If a starts_month is "
                        "ALSO supplied, the explicit starts_month wins "
                        "and the phrase is ignored."
                    ),
                },
                "current_date": {
                    "type": "STRING",
                    "description": (
                        "Optional anchor date (YYYY-MM-DD) the planner "
                        "passes when resolving relative phrases. "
                        "Required when starts_month is omitted AND a "
                        "phrase is supplied AND no projection_start_month "
                        "is set on the scenario."
                    ),
                },
            },
            "required": ["kind", "name", "amount_per_month", "line_type"],
        },
    },
    {
        "name": "explain_week",
        "description": (
            "Return a structured breakdown of a specific week from a "
            "stored projection run. Use this when the user asks 'why "
            "did week N drop' or 'what's in week N's payroll'. "
            "Returns the WeekRow's components plus active overrides "
            "and assumptions. The LLM should narrate the breakdown - "
            "do NOT compute new numbers."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "projection_run_id": {
                    "type": "STRING",
                    "description": "ID of a previously stored run.",
                },
                "week_number": {
                    "type": "INTEGER",
                    "description": "1-based week number to explain.",
                },
            },
            "required": ["projection_run_id", "week_number"],
        },
    },
    {
        "name": "query_finance_data",
        "description": (
            "Run an aggregation query against the QBO general ledger for "
            "finance metrics outside the projection scope. Use this for "
            "margin trends, expense breakdowns, revenue comparisons, ratio "
            "analysis, or any finance question that asks about historical "
            "actuals (not forecasts). Mongo computes the aggregation "
            "server-side; you narrate the returned rows without doing "
            "arithmetic on them. "
            "If the user's question doesn't fit the metric enum, say so "
            "explicitly and offer the closest available metric - do NOT "
            "improvise a calculation. "
            "\n\n"
            "Company-level gross_profit / gross_profit_margin: "
            "Total Revenue (PS + FS + MRR + WHOLESALE + OTHER revenue) "
            "minus Total COGS (PS + FS + OTHER COGS). The response "
            "includes formula_source, formula_version, "
            "included_revenue_categories, and included_cogs_categories "
            "so you can describe coverage if the user asks. This "
            "matches the validated QBO P&L baseline; per-job and "
            "per-deal margin tools (analyze_fs_job_margin / "
            "analyze_customer_deal_finance) are NOT substitutes for "
            "company gross profit and must not be presented as such."
            "\n\n"
            "FORMATTING: render rows as a markdown table when the "
            "result has multiple buckets, or as a single bolded total "
            "when group_by would produce one. Render filter / "
            "category metadata (basis, included_revenue_categories, "
            "included_cogs_categories, formula_notes) as plain-text "
            "bullets or comma-separated lists — NOT as fenced code "
            "blocks."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "metric": {
                    "type": "STRING",
                    "enum": sorted(_VALID_METRICS),
                    "description": (
                        "Which aggregated metric to compute. Available groups: "
                        "(1) GL roll-ups: revenue, expense (alias: expenses), "
                        "gross_profit, gross_profit_margin (alias: gross_margin), "
                        "expense_ratio, transaction_count, payroll, "
                        "admin_expenses. "
                        "(2) Current-month rollups (ignore group_by, always one "
                        "bucket for the current calendar month): "
                        "current_month_revenue, current_month_expenses, "
                        "current_month_net_change. "
                        "(3) Snapshot-served point-in-time totals (ignore "
                        "date_from/date_to/group_by, served from the latest "
                        "finance_snapshots row, returns unavailable_data when "
                        "no snapshot exists yet): "
                        "ar_total, ap_total, ar_aging, ap_aging. "
                        "(4) Pending data sources (return unavailable_data "
                        "with a dashboard hint so Nova can redirect the user): "
                        "rent. "
                        "Anything outside this list returns unknown_metric - do "
                        "NOT improvise a calculation."
                    ),
                },
                "group_by": {
                    "type": "STRING",
                    "enum": ["month", "quarter", "year", "account_category", "account_name"],
                    "description": "Bucket dimension for the aggregation.",
                },
                "category": {
                    "type": "STRING",
                    "enum": list(_CATEGORY_VALUES),
                    "description": (
                        "Optional category filter. Must be one of the canonical "
                        "categories from services/nova_engine/account_categories.py: "
                        + ", ".join(_CATEGORY_VALUES)
                        + ". Each value is what categorize() returns for a "
                          "matching QBO account-name prefix; passing anything "
                          "else returns an invalid_category error."
                    ),
                },
                "account_prefix": {
                    "type": "STRING",
                    "description": "Optional QBO account number prefix (e.g., '5022').",
                },
                "basis": {
                    "type": "STRING",
                    "enum": ["accrual", "cash"],
                    "description": "Accounting basis. Default 'accrual' (matches the projection engine's default). Use 'cash' for cash-basis reporting.",
                },
                "date_from": {
                    "type": "STRING",
                    "description": "Optional start date YYYY-MM-DD. Default: 12 months ago.",
                },
                "date_to": {
                    "type": "STRING",
                    "description": "Optional end date YYYY-MM-DD. Default: today.",
                },
                "limit": {
                    "type": "INTEGER",
                    "description": "Max rows to return. Default 50, max 200.",
                },
                "location": {
                    "type": "STRING",
                    "description": (
                        "Optional location / department filter. Matches "
                        "case-insensitive substring against the row's "
                        "location, location_name, department, "
                        "department_name, and department_ref_name fields, "
                        "and exact match against location_id / "
                        "department_ref_id. Rows with NO location-like "
                        "field populated will not match — call "
                        "get_gl_location_coverage first to confirm "
                        "coverage before quoting location-attributed "
                        "totals."
                    ),
                },
                "location_blank": {
                    "type": "BOOLEAN",
                    "description": (
                        "When true, restricts the result to rows where "
                        "every location-like field is missing, null, "
                        "empty, or whitespace. Use to surface activity "
                        "that cannot be attributed to any location. "
                        "Mutually exclusive with the location filter."
                    ),
                },
            },
            "required": ["metric", "group_by"],
        },
    },
    {
        "name": "reset_scenario_state",
        "description": (
            "Clear all in-memory scenario state for the current chat session: "
            "manual cost assumptions, manual recurring revenue, overrides, "
            "sales/cost modifiers, and the cached starting cash / start month "
            "from the previous run. Use this when the user says 'start over', "
            "'reset', 'clear my assumptions', or wants to begin a new "
            "scenario from scratch. Persisted projection_runs are NOT touched - "
            "this only resets what would feed into the next run_projection "
            "call. Returns a status acknowledging the reset."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "compare_runs",
        "description": (
            "Compare two persisted cash-flow projection runs and return KPI "
            "deltas plus the largest weekly ending-cash differences. Use when "
            "the user asks 'compare my last two projections', 'what changed "
            "between run X and run Y', or any what-changed/diff question across "
            "saved runs. Pass base_run_id + compare_run_id to compare a specific "
            "pair, or omit both to compare the latest two runs for the current "
            "user. Reads only - no projection math. Returns deltas for "
            "starting/ending/minimum cash, total revenue, total costs, net "
            "change, plus the top weekly swings and a one-paragraph summary "
            "the LLM can quote verbatim."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "base_run_id": {
                    "type": "STRING",
                    "description": (
                        "Optional. The baseline run. If both base_run_id and "
                        "compare_run_id are omitted, defaults to the second-most-"
                        "recent run for the current user (within latest_count)."
                    ),
                },
                "compare_run_id": {
                    "type": "STRING",
                    "description": (
                        "Optional. The comparison run. If both base_run_id and "
                        "compare_run_id are omitted, defaults to the most recent "
                        "run for the current user."
                    ),
                },
                "latest_count": {
                    "type": "INTEGER",
                    "description": (
                        "Optional, default 2. When auto-selecting runs, the "
                        "lookup window: the newest of the last latest_count runs "
                        "is compared against the oldest of that window. Setting "
                        "this to 3+ lets the user compare against runs further "
                        "back than the immediately previous one."
                    ),
                },
                "include_weekly_deltas": {
                    "type": "BOOLEAN",
                    "description": (
                        "Optional, default true. When true the response "
                        "includes the top weekly ending-cash differences so the "
                        "LLM can call out which week swung most."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "analyze_customer_deal_finance",
        "description": (
            "Rank customers (accounts) or deals by AR-invoiced revenue, "
            "contracted cost, or margin. Use when the user asks 'which "
            "customers drive the most revenue', 'which deals are biggest', "
            "or 'which deals have the largest cost exposure'. Revenue is "
            "sourced from ar_invoices (excludes cancelled). Cost is sourced "
            "from approved time_entries x resolved cost_rate joined through "
            "contracts (mirrors /api/finance/accounts-payable). Returns a "
            "ranked list, not a time series. Cost on contracts that touch "
            "multiple deals is split evenly across those deals - the "
            "response notes when this allocation was applied. Returns "
            "unavailable_mapping when the source collection has no rows for "
            "the requested period."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "dimension": {
                    "type": "STRING",
                    "enum": ["customer", "deal"],
                    "description": (
                        "Which dimension to roll up. 'customer' groups by "
                        "AEGISAXIOM account_id; 'deal' groups by deal_id."
                    ),
                },
                "metric": {
                    "type": "STRING",
                    "enum": ["revenue", "cost", "margin"],
                    "description": (
                        "What to rank by. 'revenue' = sum of AR invoice "
                        "totals. 'cost' = approved time-entry hours x "
                        "resolved cost_rate. 'margin' = revenue - cost."
                    ),
                },
                "date_from": {
                    "type": "STRING",
                    "description": (
                        "Optional start date YYYY-MM-DD. AR invoices use "
                        "period_start; time entries use entry date."
                    ),
                },
                "date_to": {
                    "type": "STRING",
                    "description": "Optional end date YYYY-MM-DD.",
                },
                "limit": {
                    "type": "INTEGER",
                    "description": (
                        "Max attributable rows to return. Default 10, max 50. "
                        "An unattributed bucket (rows missing the dimension "
                        "key) is appended after the limit when it is non-zero."
                    ),
                },
            },
            "required": ["dimension", "metric"],
        },
    },
    {
        "name": "analyze_fs_job_margin",
        "description": (
            "Compute gross margin for a field-service job, or rank "
            "field-service jobs by margin. Use when the user asks 'what "
            "was the margin on this FS job?', 'show me low-margin jobs', "
            "or 'which FS jobs are losing money?'. Pass job_id to get a "
            "single-job breakdown; omit it to get a ranked list. Cost "
            "preference: stored FieldJob.labor_cost + material_cost when "
            "non-zero, otherwise approved time_entries x cost_rate via "
            "the project link (project.field_job_id). Revenue is "
            "approved time_entries x sell_rate via the same link - we "
            "do NOT pro-rate AR invoices across jobs because the deal "
            "level mapping is too lossy. When data is incomplete the "
            "response carries partial_result=true and lists the missing "
            "sources."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "job_id": {
                    "type": "STRING",
                    "description": (
                        "Optional. When provided, return the single-job "
                        "margin breakdown. Omit to rank multiple jobs."
                    ),
                },
                "status": {
                    "type": "STRING",
                    "description": (
                        "Optional FieldJob.status filter for the list "
                        "view (scheduled, confirmed, in_progress, "
                        "completed, cancelled)."
                    ),
                },
                "sort": {
                    "type": "STRING",
                    "enum": ["loss_first", "margin_asc", "margin_desc", "revenue_desc"],
                    "description": (
                        "List sort order. 'loss_first' is an alias for "
                        "'margin_asc' (kept as a friendlier name) - both "
                        "sort margin_percent ascending, which surfaces "
                        "money-losing jobs first because every negative "
                        "margin is less than every non-negative margin. "
                        "Jobs with no revenue (margin_percent undefined) "
                        "are pushed to the end. 'margin_desc' is "
                        "ascending reversed; 'revenue_desc' ranks by "
                        "revenue magnitude."
                    ),
                },
                "min_margin_percent": {
                    "type": "NUMBER",
                    "description": (
                        "Optional 'low margin' filter. Returns only "
                        "jobs whose margin_percent is BELOW this value. "
                        "Jobs with no revenue are excluded - we cannot "
                        "compare 'no margin' to a numeric threshold."
                    ),
                },
                "limit": {
                    "type": "INTEGER",
                    "description": (
                        "Max rows for the list view. Default 10, max 50."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "detect_wholesale_revenue",
        "description": (
            "Auto-detect wholesale revenue from AEGISAXIOM structured signals. "
            "Use when the user asks 'do we have wholesale revenue?', 'how "
            "much wholesale was there last quarter?', or 'is the projection "
            "picking up our wholesale book?'. Detection is deterministic - "
            "rules-only, no LLM classification. Reliable signals: GL "
            "accounts under prefix 50400 (mapped to WHOLESALE_REVENUE in "
            "account_categories.py), Contract.contract_type='wholesale_mrr', "
            "Opportunity/SOW lines_of_business='wholesale_mrr', and "
            "Product.item_category='wholesale_mrr'. When the GL signal "
            "is consistent across the history window the projection input "
            "auto-fills a recurring WHOLESALE assumption; otherwise the "
            "tool returns the detected monthly amounts but does NOT seed "
            "the projection (caller must add manually if they trust the "
            "single data point)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "history_months": {
                    "type": "ARRAY",
                    "description": (
                        "Optional list of YYYY-MM months to inspect. When "
                        "omitted, defaults to the last 3 complete months "
                        "before today (matches the projection-history "
                        "window data_loader.last_n_complete_months uses)."
                    ),
                    "items": {"type": "STRING"},
                },
                "basis": {
                    "type": "STRING",
                    "enum": ["accrual", "cash"],
                    "description": (
                        "Accounting basis. Default 'accrual' - matches the "
                        "engine."
                    ),
                },
            },
            "required": [],
        },
    },
    # -----------------------------------------------------------------------
    # FCC analytical tools (Wave 2J — Financial Command Center ingestion)
    # Each tool queries QBO report collections via get_qbo_db(). Thread 2B
    # route handlers expose the same collections; if their _compute_* helpers
    # are not yet merged, we query the QBO collections directly — duplicating
    # the transformation is acceptable for v1.
    # -----------------------------------------------------------------------
    {
        "name": "get_pnl_summary",
        "description": (
            "Return the Profit & Loss summary from QBO for the current or "
            "prior period. Use when the user asks 'show me the P&L', "
            "'what is our net income?', 'how profitable were we last "
            "quarter?', or any question about income vs. expenses from "
            "the formal QBO report (not the GL roll-up). Returns the "
            "structured QBO report doc including line items and summary "
            "totals. Narrate the key figures — do NOT recompute them."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "period": {
                    "type": "STRING",
                    "enum": ["current", "prior"],
                    "description": (
                        "'current' returns the most recent QBO P&L report "
                        "(reports_pnl collection). 'prior' returns the "
                        "prior-year comparison report (reports_pnl_prior_year). "
                        "Default: 'current'."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_balance_sheet_summary",
        "description": (
            "Return the Balance Sheet summary from QBO. Use when the user "
            "asks 'what is our balance sheet?', 'how much equity do we "
            "have?', 'what are our total assets?', or any question about "
            "assets, liabilities, or equity from the formal QBO report. "
            "Returns the latest cached QBO balance sheet doc. Narrate "
            "the key figures — do NOT recompute them."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_cash_flow_summary",
        "description": (
            "Return the Cash Flow Statement from QBO. Use when the user "
            "asks 'show me the cash flow statement', 'how much cash did "
            "we generate from operations?', or any question about "
            "operating/investing/financing cash flows from the formal "
            "QBO report (distinct from the Nova projection engine which "
            "forecasts future cash). Returns the latest cached QBO cash "
            "flow doc. Narrate the key figures — do NOT recompute them."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_trial_balance_summary",
        "description": (
            "Return the Trial Balance from QBO. Use when the user asks "
            "'show me the trial balance', 'what are the debit/credit "
            "totals by account?', or any question requiring the full "
            "account-level debit/credit summary. Returns the latest "
            "cached QBO trial balance doc. Narrate the key figures — "
            "do NOT recompute them."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_ar_aging",
        "description": (
            "Return AR aging buckets per customer from QBO. Use when the "
            "user asks 'how old is our receivables?', 'which customers "
            "are past due?', 'show me the AR aging report', or any "
            "question about receivables broken out by how overdue they "
            "are. Returns the QBO AR aging report with per-customer "
            "bucket breakdown. Narrate overdue concentrations — do NOT "
            "recompute the bucket math."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_ap_aging",
        "description": (
            "Return AP aging buckets per vendor from QBO. Use when the "
            "user asks 'what bills are past due?', 'which vendors are "
            "we behind on?', 'show me the AP aging report', or any "
            "question about payables broken out by how overdue they are. "
            "Returns the QBO AP aging report with per-vendor bucket "
            "breakdown. Narrate overdue concentrations — do NOT "
            "recompute the bucket math."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_dso_dpo_trend",
        "description": (
            "Return Days Sales Outstanding (DSO) and Days Payable "
            "Outstanding (DPO) trend data over recent months. Use when "
            "the user asks 'how fast are we collecting receivables?', "
            "'what is our DSO?', 'are we paying vendors faster or "
            "slower?', or any working-capital efficiency question. "
            "Returns month-by-month DSO and DPO figures. Narrate the "
            "trend direction — do NOT recompute the ratios."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "months": {
                    "type": "INTEGER",
                    "description": (
                        "Number of trailing months to include. Default 12, "
                        "max 24."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_payables_timing",
        "description": (
            "Return a single number that answers how long the business "
            "is taking to pay vendors. Use this any time the user asks "
            "for average payables time, average payable time, average "
            "time to pay bills, days payable outstanding, DPO, AP "
            "timing, payables aging summary, or how long are we taking "
            "to pay vendors. The tool picks the best available metric "
            "in priority order and returns the result with a method "
            "label so you can narrate which view it used. Priority is "
            "paid bill cycle first, then DPO proxy, then open AP "
            "timing as a fallback. The tool never returns a refusal. "
            "When only open AP data is available it still returns "
            "weighted average open AP age and weighted days past due "
            "so the user gets an actionable number. Do NOT recompute "
            "any of the returned figures."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "as_of_date": {
                    "type": "STRING",
                    "description": (
                        "Reference date for the open AP timing view in "
                        "YYYY-MM-DD format. Defaults to today when "
                        "omitted."
                    ),
                },
                "basis": {
                    "type": "STRING",
                    "description": (
                        "Accounting basis label. Defaults to accrual. "
                        "The label is surfaced in the response and does "
                        "not change the math."
                    ),
                },
                "period_days": {
                    "type": "INTEGER",
                    "description": (
                        "Trailing window in days used by the DPO proxy "
                        "to derive daily purchases. Defaults to 90."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_qbo_payables_metrics",
        "description": (
            "Return the structured QBO payables timing payload built "
            "from the synced ap_bills lake. Use this any time the "
            "user asks for average payables time, days payable "
            "outstanding, DPO, AP timing, paid bill payment cycle, "
            "or current open bill age. Backs the "
            "/api/finance/qbo/payables-metrics route and answers from "
            "real synced data rather than a refusal. The response "
            "carries the three timing metrics with formula, count, "
            "excluded_count, and warnings on each, plus "
            "open_payables_total, overdue_open_payables_total, and a "
            "sync block that reports last_successful_sync_at and "
            "is_stale. Quote the numbers verbatim. Explain that "
            "average payables time can mean either the paid bill "
            "payment cycle or the current open bill age, and surface "
            "both metrics when available. If the response carries a "
            "warning that paid bill payment dates are not synced, "
            "say so plainly and fall through to the open bill age."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "as_of_date": {
                    "type": "STRING",
                    "description": (
                        "Reference date for the metrics in YYYY-MM-DD "
                        "format. Defaults to today UTC when omitted."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_qbo_weekly_finance_report",
        "description": (
            "Return the six section weekly finance plan from the QBO "
            "lake. Sections are summary, ar_weekly, ap_weekly "
            "(open and paid), raw_ar, raw_ap, and sync_audit. Use "
            "this any time the user asks for the weekly finance "
            "plan, the AP AR plan, expected collections by week, "
            "vendor payments by week, or weekly cash plan. Backs "
            "the /api/finance/qbo/weekly-plan route. Quote the "
            "summary totals verbatim. Do NOT recompute weekly "
            "totals; the tool already grouped rows by the configured "
            "week ending day. If the user wants the spreadsheet, "
            "follow up with export_qbo_weekly_finance_report."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "as_of_date": {
                    "type": "STRING",
                    "description": (
                        "Anchor date in YYYY-MM-DD format. Defaults "
                        "to today UTC when omitted."
                    ),
                },
                "weeks": {
                    "type": "INTEGER",
                    "description": (
                        "Number of weeks ahead to include in the "
                        "plan horizon. Defaults to 8. Clamped to 26."
                    ),
                },
                "week_ending_day": {
                    "type": "STRING",
                    "description": (
                        "Day name that ends each week bucket. "
                        "Default Friday. The default is a default, "
                        "not a hardcoded business rule."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "export_qbo_weekly_finance_report",
        "description": (
            "Return the download URL for the weekly finance plan as "
            "a CSV. Use this when the user asks to export, download, "
            "share, or send the weekly plan as a spreadsheet. The "
            "tool itself does NOT render the CSV; it returns the "
            "/api/finance/qbo/weekly-plan/export URL with the "
            "requested query parameters so the chat surface or the "
            "browser can fetch the file. Quote the download_url "
            "verbatim. Do not invent a URL."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "as_of_date": {
                    "type": "STRING",
                    "description": "YYYY-MM-DD. Defaults to today UTC.",
                },
                "weeks": {
                    "type": "INTEGER",
                    "description": "Number of weeks in the horizon. Defaults to 8.",
                },
                "week_ending_day": {
                    "type": "STRING",
                    "description": "Week ending day name. Default Friday.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_revenue_trend",
        "description": (
            "Return month-by-month revenue trend from QBO. Use when the "
            "user asks 'how has revenue trended?', 'show me monthly "
            "revenue over the last year', 'is revenue growing?', or any "
            "time-series revenue question backed by QBO actuals (not the "
            "Nova projection). Returns monthly revenue figures. Narrate "
            "the trend — do NOT recompute the amounts."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "months": {
                    "type": "INTEGER",
                    "description": (
                        "Number of trailing months to include. Default 12, "
                        "max 24."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_top_customers_by_income",
        "description": (
            "Return top customers ranked by income from QBO. Use when "
            "the user asks 'who are our top customers?', 'which customers "
            "generate the most revenue per QBO?', or any customer revenue "
            "ranking question backed by QBO income data (as opposed to "
            "analyze_customer_deal_finance which uses AEGISAXIOM AR drafts). "
            "Returns a ranked list with income totals. Narrate the "
            "ranking — do NOT recompute the amounts."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "limit": {
                    "type": "INTEGER",
                    "description": "Max rows to return. Default 15, max 50.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_top_vendors_by_spend",
        "description": (
            "Return top vendors ranked by spend from QBO. Use when the "
            "user asks 'who do we spend the most with?', 'which vendors "
            "are our biggest costs?', or any vendor spend ranking question "
            "backed by QBO expense data. Returns a ranked list with spend "
            "totals. Narrate the ranking — do NOT recompute the amounts."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "limit": {
                    "type": "INTEGER",
                    "description": "Max rows to return. Default 15, max 50.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_coa_lookup",
        "description": (
            "Look up the Chart of Accounts from QBO, optionally filtered "
            "by kind. Use when the user asks 'show me the chart of "
            "accounts', 'which income accounts do we have?', 'what "
            "expense accounts are set up in QBO?', or any question "
            "about account structure. Returns account name, type, and "
            "classification. Narrate the accounts — do NOT invent "
            "account IDs."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "kind": {
                    "type": "STRING",
                    "enum": ["income", "expense"],
                    "description": (
                        "Optional filter by account classification. "
                        "'income' returns income/revenue accounts; "
                        "'expense' returns expense accounts. Omit to "
                        "return all account types."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_open_invoices",
        "description": (
            "Return OPEN AR invoices from the open-invoice / AR-aging "
            "snapshot source via the invoice data-access layer. "
            "Date / bucket / customer-search filtering is pushed into "
            "the query layer; the response carries server-computed "
            "total_amount (open-invoice amount) and total_open_balance "
            "(open balance) so the LLM never sums rows itself. "
            "\n\n"
            "Scope: OPEN invoices ONLY. Paid and closed invoices are "
            "NOT in this source — do NOT use this tool to answer "
            "'how much have we invoiced in <period>' or any "
            "all-invoices-issued total. Route those to "
            "query_invoice_totals instead. "
            "\n\n"
            "Use when the user asks 'show me open invoices', "
            "'which invoices are outstanding', 'overdue invoices', "
            "'what's outstanding for [customer]'. Filter by date_from / "
            "date_to (YYYY-MM-DD against invoice date), bucket "
            "('Current', '1 - 30', '31 - 60', '61 - 90', '91 and over'), "
            "customer search string, or class."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "search": {
                    "type": "STRING",
                    "description": "Match against customer name or invoice number (case-insensitive substring).",
                },
                "bucket": {
                    "type": "STRING",
                    "description": "Aging bucket. One of 'Current', '1 - 30', '31 - 60', '61 - 90', '91 and over'.",
                },
                "date_from": {
                    "type": "STRING",
                    "description": "Inclusive start date (YYYY-MM-DD). Compares against invoice date.",
                },
                "date_to": {
                    "type": "STRING",
                    "description": "Inclusive end date (YYYY-MM-DD). Compares against invoice date.",
                },
                "class_name": {
                    "type": "STRING",
                    "description": "Filter to invoices with this QBO class.",
                },
                "limit": {
                    "type": "INTEGER",
                    "description": "Max rows to return. Default 50, max 200.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "query_invoice_totals",
        "description": (
            "Return total ISSUED invoices in a period from the "
            "issued-invoices source via the invoice data-access "
            "layer. Use this for 'how much have we invoiced in "
            "<period>', 'total invoices issued', 'invoice amount for "
            "<period>', or any aggregate issued-invoice question. "
            "\n\n"
            "Scope: ALL issued invoices in the date window — paid AND "
            "closed AND open. The source carries paid/closed invoices, "
            "so the total reflects everything billed in the period, "
            "not just outstanding balances. Date / customer / search / "
            "location / status filters are pushed into the query layer. "
            "invoice_count and total_invoice_amount are computed by the "
            "data-access helper — quote total_invoice_amount verbatim, "
            "do NOT sum rows yourself. "
            "\n\n"
            "DO NOT use get_open_invoices to answer all-invoices-issued "
            "questions: that source is OPEN-only and would understate "
            "the period."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {
                    "type": "STRING",
                    "description": (
                        "Inclusive start date (YYYY-MM-DD) against "
                        "invoice transaction date. Required. The user's "
                        "natural-language phrase (a named month, "
                        "'last quarter', etc.) must be parsed by you "
                        "into a concrete date before the call."
                    ),
                },
                "date_to": {
                    "type": "STRING",
                    "description": (
                        "Inclusive end date (YYYY-MM-DD). Required."
                    ),
                },
                "search": {
                    "type": "STRING",
                    "description": (
                        "Substring match on customer name OR invoice "
                        "doc number, case-insensitive."
                    ),
                },
                "customer": {
                    "type": "STRING",
                    "description": "Customer name substring filter.",
                },
                "class_name": {
                    "type": "STRING",
                    "description": "QBO class label filter.",
                },
                "location": {
                    "type": "STRING",
                    "description": "QBO location / department label filter.",
                },
                "status": {
                    "type": "STRING",
                    "description": (
                        "Optional issued-invoice status filter. The "
                        "source has no first-class status enum; this "
                        "matches against EmailStatus where present."
                    ),
                },
                "include_rows": {
                    "type": "BOOLEAN",
                    "description": (
                        "Include preview rows in the response. Default "
                        "false — set true ONLY when the user asks to "
                        "see individual invoices (e.g. 'show me invoices "
                        "issued in May'). Aggregate phrasings ('how much "
                        "have we invoiced') do not need rows."
                    ),
                },
                "limit": {
                    "type": "INTEGER",
                    "description": (
                        "Max preview rows. Default 100, hard cap 500. "
                        "invoice_count and total_invoice_amount "
                        "reflect ALL matching invoices regardless of "
                        "limit."
                    ),
                },
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "get_open_bills",
        "description": (
            "Return AP bills from QBO with vendor, doc number, txn/due "
            "dates, amount, open balance, class/location when available. "
            "Use when the user asks 'show me open bills', 'what bills "
            "are due this week', 'how much do we owe [vendor]', or any "
            "per-bill question. Filter by date_from / date_to (YYYY-MM-DD "
            "against txn date), open_only=true to hide settled bills, "
            "vendor search, or class. Narrate the rows — do NOT "
            "recompute totals from them."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "search": {
                    "type": "STRING",
                    "description": "Match against vendor name or bill doc number (case-insensitive substring).",
                },
                "date_from": {
                    "type": "STRING",
                    "description": "Inclusive start date (YYYY-MM-DD). Compares against bill txn date.",
                },
                "date_to": {
                    "type": "STRING",
                    "description": "Inclusive end date (YYYY-MM-DD). Compares against bill txn date.",
                },
                "class_name": {
                    "type": "STRING",
                    "description": "Filter to bills with this QBO class.",
                },
                "open_only": {
                    "type": "BOOLEAN",
                    "description": "If true, only return bills with a positive open balance.",
                },
                "limit": {
                    "type": "INTEGER",
                    "description": "Max rows to return. Default 50, max 200.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "query_gl_transactions",
        "description": (
            "Return total income AND the underlying GL transaction rows for "
            "an explicit date range. Use this when the user asks to fetch "
            "the transactions behind a total, the rows behind a period's "
            "income, or any per-row income inquiry. Results are categorized "
            "in Python via account_categories.categorize() and only rows in "
            "PS_REVENUE / FS_REVENUE / MRR / WHOLESALE_REVENUE / "
            "OTHER_REVENUE are included. "
            "\n\n"
            "Set location_blank=true to restrict to rows whose location and "
            "department fields are missing, null, empty, or whitespace. If "
            "the GL schema for this tenant does not carry location-like "
            "fields, location_field_status comes back 'unavailable' with a "
            "notes string explaining the limit; pass that note through to "
            "the user verbatim. "
            "\n\n"
            "RENDERING (mandatory): in your reply, render a bolded total "
            "income line above the rows, then a markdown table with columns "
            "tx_date | account_name | category | txn_type | doc_num | name "
            "| memo | location | normalized_amount. If rows_returned <= 100 "
            "render every preview row; if > 100, render the first 100 with "
            "the sentence 'Table preview is capped at 100 rows for "
            "readability.'. If preview_truncated is true "
            "(row_count > rows_returned), render the sentence "
            "'Showing first <rows_returned> of <row_count> rows.' above "
            "the table. Quote normalized_amount and total_income as "
            "positive values; never quote amount_raw to the user unless "
            "they explicitly ask for raw GL signs. "
            "\n\n"
            "EXPORT: this tool's row set is exportable via the Nova "
            "exports endpoint (POST /api/nova/exports with "
            "tool_name='query_gl_transactions' and the same tool_args). "
            "If the user asks for a CSV / Excel of these rows, point "
            "them at that endpoint — do not paste the rows back as a "
            "code block and do not invent a download URL. The endpoint "
            "requires the nova.export.create permission."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {
                    "type": "STRING",
                    "description": (
                        "Inclusive lower bound on tx_date, YYYY-MM-DD. "
                        "The user's natural-language phrase (a named month, "
                        "'last month', etc.) must be parsed by you into a "
                        "concrete date before the call — the tool itself "
                        "does not interpret natural language."
                    ),
                },
                "date_to": {
                    "type": "STRING",
                    "description": (
                        "Inclusive upper bound on tx_date, YYYY-MM-DD."
                    ),
                },
                "basis": {
                    "type": "STRING",
                    "enum": ["accrual", "cash"],
                    "description": (
                        "Accounting basis. Default 'accrual' (matches the "
                        "projection engine)."
                    ),
                },
                "location_blank": {
                    "type": "BOOLEAN",
                    "description": (
                        "When true, restrict to rows whose location and "
                        "department fields are missing, null, empty, or "
                        "whitespace. Default false."
                    ),
                },
                "limit": {
                    "type": "INTEGER",
                    "description": (
                        "Max preview rows to return. Default 100, hard cap "
                        "500 (clamped silently with a notes warning when a "
                        "higher value is passed). total_income reflects "
                        "ALL matching rows regardless of limit."
                    ),
                },
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "get_finance_data_freshness",
        "description": (
            "Diagnostic-only probe of the AEGISAXIOM-triggered QBO sync log "
            "(qbo_sync_requests). Read-only — does NOT call QuickBooks, "
            "does NOT call n8n, does NOT trigger a sync. Nova is not "
            "allowed to start a sync; ask the user to trigger one via "
            "the Finance Sync admin endpoint when applicable. "
            "\n\n"
            "Returns {last_successful_sync_at, sync_lag_seconds, "
            "is_stale, warning}. The warning string is admin/diagnostic "
            "wording — do NOT quote it verbatim in normal user-facing "
            "finance answers. For normal finance answers, call "
            "get_finance_data_status and use its user_facing_data_note "
            "instead. Only mention this tool's output (sync log status, "
            "missing sync records, stale thresholds) when the user "
            "explicitly asks about sync status, freshness diagnostics, "
            "or admin sync history. "
            "\n\n"
            "IMPORTANT: A null last_successful_sync_at means no "
            "AEGISAXIOM-triggered sync log entry exists yet — freshness is "
            "UNKNOWN, not 'data missing'. If finance collections "
            "contain rows, do NOT tell the user the database is empty "
            "or unavailable solely because the sync log has no "
            "successful record. Use get_finance_data_status to check "
            "whether finance collections actually have rows."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_finance_data_status",
        "description": (
            "Read-only snapshot combining AEGISAXIOM sync freshness with "
            "actual finance-collection presence. Does NOT call "
            "QuickBooks, does NOT call n8n, does NOT trigger a sync. "
            "\n\n"
            "Use this when the user asks: 'can you access the finance "
            "database?', 'what data do you have?', 'what is the latest "
            "GL transaction date?', 'what basis values are available?', "
            "'is the data usable for testing?', 'is finance data stale?'. "
            "\n\n"
            "Returns user_facing_data_note (the wording Nova should "
            "use in normal finance answers — e.g. \"Based on the "
            "latest available finance data in AEGISAXIOM as of <ts>.\"), "
            "admin_sync_note (diagnostic line for admin dashboards), "
            "sync_status (same shape as get_finance_data_freshness — "
            "diagnostic only), data_status {finance_data_present, "
            "general_ledger_usable, general_ledger {row_count, "
            "latest_tx_date, latest_tx_date_basis, "
            "available_basis_values, latest_synced_at, by_basis: "
            "[{basis, row_count, latest_tx_date, latest_synced_at}, "
            "...]}, collections {<name>: {row_count, "
            "latest_synced_at}}}, and testing_status "
            "{usable_for_finance_testing, reason, warnings}. "
            "\n\n"
            "WORDING RULES: For normal finance answers, prefer "
            "user_facing_data_note. Only mention missing "
            "AEGISAXIOM-triggered sync records, sync_status.warning, "
            "admin_sync_note, or 'stale' wording if the user "
            "explicitly asks about sync/freshness diagnostics or "
            "admin sync history. If finance collections contain rows, "
            "do not tell the user the database is empty or "
            "unavailable solely because the sync log has no "
            "successful record. "
            "\n\n"
            "IMPORTANT: general_ledger.latest_tx_date is the ALL-BASIS "
            "max — accrual and cash rows can extend to different "
            "dates. Always report all-basis and per-basis GL dates "
            "separately. Do NOT collapse accrual and cash into one "
            "ambiguous date. If the all-basis latest date comes from "
            "cash-basis rows, say so. For accrual-reporting / P&L "
            "questions, quote the accrual entry from by_basis. Read "
            "by_basis to get the per-basis row_count, latest_tx_date, "
            "and latest_synced_at. "
            "\n\n"
            "IMPORTANT: No successful AEGISAXIOM-triggered sync record means "
            "freshness is unknown; it does NOT mean finance "
            "collections are empty. Nova must inspect "
            "data_status.finance_data_present and "
            "data_status.general_ledger_usable before claiming the "
            "database has no data."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_gl_location_coverage",
        "description": (
            "Read-only report on how many GL rows in scope carry a "
            "location or department value, scoped to the same metric "
            "or category as the answer being given. Reads only "
            "general_ledger; does NOT call QuickBooks, does NOT call "
            "n8n, does NOT trigger a sync. "
            "\n\n"
            "MANDATORY metric usage. For 'revenue by location' pass "
            "metric='revenue'. For 'expenses by location' pass "
            "metric='expenses'. For 'COGS by location' pass "
            "metric='cogs'. For category-specific questions ('FS "
            "revenue by location'), pass category. Do NOT use "
            "all-GL coverage to answer metric/category-specific "
            "location questions — that was the audited bug where "
            "Nova reported revenue location coverage at 100% from "
            "all-GL data while the actual revenue rows had 0% "
            "coverage. "
            "\n\n"
            "Returns row_count, with_location_count, "
            "blank_location_count, coverage_pct (0.0 - 1.0), "
            "location_like_fields, field_presence, by_basis, scope "
            "(applied_metric / applied_category / "
            "applied_categories / scope_description), and warning. "
            "All counts are computed AFTER metric/category filtering. "
            "\n\n"
            "IMPORTANT: This tool DOES NOT INVENT location values. "
            "Location-scoped totals are only as reliable as the share "
            "of GL rows that carry populated location/department "
            "fields. If row_count > 0 but with_location_count == 0, "
            "say location-level reporting is not reliable for that "
            "scope. If coverage_pct < 1.0, disclose row_count, "
            "with_location_count, blank_location_count, and "
            "coverage_pct before quoting any location-attributed "
            "total."
            "\n\n"
            "FORMATTING: present scope.applied_categories and "
            "field_presence as comma-separated text or a compact "
            "bullet list — NOT as fenced code blocks. Quote "
            "scope.scope_description and the warning string verbatim "
            "in plain prose."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {
                    "type": "STRING",
                    "description": "Optional start date YYYY-MM-DD; restricts the scope.",
                },
                "date_to": {
                    "type": "STRING",
                    "description": "Optional end date YYYY-MM-DD; restricts the scope.",
                },
                "basis": {
                    "type": "STRING",
                    "enum": ["accrual", "cash"],
                    "description": (
                        "Optional basis filter. Omit to count both bases; the "
                        "by_basis breakdown is always returned regardless."
                    ),
                },
                "metric": {
                    "type": "STRING",
                    "enum": ["revenue", "expenses", "cogs", "gross_profit"],
                    "description": (
                        "Scope coverage to a metric's canonical category set. "
                        "revenue = PS_REVENUE, FS_REVENUE, MRR, "
                        "WHOLESALE_REVENUE, OTHER_REVENUE. expenses = PS_COGS, "
                        "FS_COGS, OTHER_COGS, PAYROLL, ADMIN_EXPENSES, "
                        "OTHER_OPEX. cogs = PS_COGS, FS_COGS, OTHER_COGS. "
                        "gross_profit = revenue + cogs (mixed source scope; "
                        "the response carries a scope_description note)."
                    ),
                },
                "category": {
                    "type": "STRING",
                    "enum": list(_CATEGORY_VALUES),
                    "description": (
                        "Optional single-category scope. When paired with "
                        "metric, the category must be a member of the "
                        "metric's canonical set or the tool returns a "
                        "structured invalid_scope error rather than "
                        "silently dropping the filter."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "explain_finance_drivers",
        "description": (
            "Deterministic driver / variance explanation for a single "
            "finance metric over a date range. Reads only "
            "general_ledger; does NOT call QuickBooks, does NOT call "
            "n8n, does NOT trigger a sync. Nova narrates the returned "
            "rows but does NOT compute variance or rank accounts on its "
            "own. "
            "\n\n"
            "Use when the user asks: 'why did revenue go up / down?', "
            "'explain expenses', 'what drove this variance?', 'top "
            "accounts behind revenue / expense', 'month-by-month "
            "breakdown', 'what changed vs last month / quarter / year?'. "
            "\n\n"
            "Returns: scope {metric, basis, current_period_start, "
            "current_period_end, comparison_period_start, "
            "comparison_period_end, comparison_mode, comparison_label, "
            "comparison_n_days, source {collection, scope}, "
            "account_filter_label, included_revenue_categories, "
            "excluded_categories}; summary {current_period_total, "
            "comparison_period_total, variance_amount, "
            "variance_percent, variance_percent_display}; formula "
            "{variance_amount, variance_percent}; monthly buckets per "
            "YYYY-MM; top_accounts ranked by absolute contribution; "
            "top_customers / top_vendors (currently empty + warning); "
            "drivers; warnings. Backward-compat aliases under summary: "
            "current_total / comparison_total / variance_pct mirror "
            "the canonical names; older callers keep working. "
            "\n\n"
            "WORDING RULES (mandatory). Quote scope.comparison_mode "
            "and scope.comparison_label verbatim. Do NOT relabel "
            "'prior_period_same_length' as 'previous_month' — those "
            "are different windows; the helper has already chosen the "
            "more specific 'previous_month' label automatically when "
            "the comparison range exactly equals the prior calendar "
            "month. For 'prior_period_same_length' say 'prior "
            "same-length period' or 'prior N-day period' (use "
            "scope.comparison_n_days), NOT a calendar-month name. "
            "Quote scope.included_revenue_categories as a "
            "comma-separated list, not a code block. Quote "
            "summary.variance_amount and summary.variance_percent "
            "verbatim from the helper — never recompute them. When "
            "summary.variance_percent is null, quote "
            "summary.variance_percent_display ('N/A'). "
            "\n\n"
            "Supported metrics: revenue, expenses, cogs, gross_profit. "
            "Asking for cash, ar, or ap returns a routing hint pointing "
            "to the snapshot tools (get_finance_data_status / "
            "get_ar_aging / get_ap_aging) and does NOT invent numbers. "
            "\n\n"
            "Per-customer and per-vendor ranking are NOT derived from "
            "the GL row schema — call get_top_customers_by_income / "
            "get_top_vendors_by_spend for those, and quote both "
            "responses if the user wants both axes."
            "\n\n"
            "FORMATTING: render scope.included_revenue_categories as "
            "comma-separated text (e.g. 'PS_REVENUE, FS_REVENUE, MRR, "
            "WHOLESALE_REVENUE, and OTHER_REVENUE'), NOT as a fenced "
            "code block or one-bullet-per-category list. Quote "
            "formula.variance_amount and formula.variance_percent as "
            "plain-text equation lines, NOT as fenced code blocks. "
            "Render query parameters (basis, current period, "
            "comparison period, comparison mode) as a compact bullet "
            "list with bolded labels."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "metric": {
                    "type": "STRING",
                    "enum": ["revenue", "expenses", "cogs", "gross_profit", "cash", "ar", "ap"],
                    "description": (
                        "Which metric to explain. revenue / expenses / "
                        "cogs / gross_profit are computed from GL. "
                        "cash / ar / ap return a routing hint instead "
                        "of inventing values."
                    ),
                },
                "date_from": {
                    "type": "STRING",
                    "description": "Required start date YYYY-MM-DD.",
                },
                "date_to": {
                    "type": "STRING",
                    "description": "Required end date YYYY-MM-DD.",
                },
                "basis": {
                    "type": "STRING",
                    "enum": ["accrual", "cash"],
                    "description": "Accounting basis. Default 'accrual'.",
                },
                "compare_to": {
                    "type": "STRING",
                    "enum": ["previous_period", "same_period_prior_year"],
                    "description": (
                        "Comparison window. Default 'previous_period'. "
                        "Pass null in tool_args to omit comparison "
                        "entirely (variance fields will be null)."
                    ),
                },
                "limit": {
                    "type": "INTEGER",
                    "description": "Max top_accounts entries. Default 10.",
                },
            },
            "required": ["metric", "date_from", "date_to"],
        },
    },
    {
        "name": "create_finance_export",
        "description": (
            "Create a downloadable CSV / XLSX / PDF of an approved "
            "finance tool result. Re-runs the tool server-side, "
            "persists the file in GridFS, and returns metadata + a "
            "download_url + an artifact_id the chat UI renders as a "
            "download card. "
            "\n\n"
            "Use when the user asks for an export, CSV, XLSX, Excel, "
            "spreadsheet, PDF, report, or download of a supported "
            "finance result. Pass the SAME tool_name + tool_args you "
            "used (or would use) to compute the on-screen answer — "
            "the server re-runs the tool, never trusts client-supplied "
            "rows, and never builds the file from your chat markdown. "
            "\n\n"
            "Format / tool support matrix (the server enforces this): "
            "row-level tools — query_gl_transactions, "
            "query_invoice_totals, get_open_invoices, get_open_bills, "
            "query_finance_data — export as CSV or XLSX. Report-style "
            "tools — explain_finance_drivers — export as PDF. PDF is "
            "reserved for narrative summary documents; row-level GL / "
            "invoice / open-bill data exports as CSV or XLSX so the "
            "user can reconcile in a spreadsheet. Mixing them returns "
            "a structured 'unsupported format' error. "
            "\n\n"
            "On success the response carries status='export_created', "
            "export_id, artifact_id, filename, format, content_type, "
            "row_count, columns, download_url, generated_at, "
            "expires_at, warnings. For PDF exports the response also "
            "carries report_sections (the sections the renderer "
            "drew). Quote the filename / row_count / format in your "
            "reply and include the artifact_id verbatim as "
            "``[[artifact:<artifact_id>]]`` so the chat UI can render "
            "the download card. Do NOT invent a download_url. Do NOT "
            "instruct normal chat users to call the API endpoint "
            "manually unless they explicitly ask for API details. If "
            "status='export_too_large', tell the user to narrow the "
            "date window or filters. If error='unsupported_export_"
            "format', tell the user which formats are supported for "
            "that tool (CSV/XLSX for row data, PDF for driver "
            "reports)."
            "\n\n"
            "Driver explanation PDF rules: when the user says "
            "'export that' / 'I need a PDF' after an "
            "explain_finance_drivers answer AND the previous report "
            "scope is clear (you can name the metric, current period, "
            "basis, and comparison mode you just used), call this "
            "tool with tool_name='explain_finance_drivers', "
            "format='pdf', and tool_args mirroring the same metric / "
            "date_from / date_to / basis / compare_to. If the scope "
            "is ambiguous (e.g. the user said 'export that' but the "
            "last driver answer is several turns back or covered "
            "multiple metrics), ASK a one-line clarification instead "
            "of guessing. Never claim a PDF was generated unless the "
            "tool returned status='export_created'."
            "\n\n"
            "FORMATTING: render the export confirmation as a short "
            "plain-text sentence — quote filename and row_count "
            "inline, then put the [[artifact:<id>]] marker on its "
            "own line. Do NOT dump the full JSON response in a "
            "fenced code block (the chat UI already renders the "
            "download card from the artifact marker)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "tool_name": {
                    "type": "STRING",
                    "enum": [
                        "query_gl_transactions",
                        "query_invoice_totals",
                        "get_open_invoices",
                        "get_open_bills",
                        "query_finance_data",
                        "explain_finance_drivers",
                    ],
                    "description": (
                        "Allow-listed Nova tool whose latest result "
                        "the user wants to export. Row-level tools "
                        "export as CSV/XLSX; explain_finance_drivers "
                        "exports as PDF."
                    ),
                },
                "tool_args": {
                    "type": "OBJECT",
                    "description": (
                        "Args to pass through to the tool re-run. Use the "
                        "same args that produced the answer being "
                        "exported."
                    ),
                },
                "format": {
                    "type": "STRING",
                    "enum": ["csv", "xlsx", "pdf"],
                    "description": (
                        "Output format. CSV/XLSX for row-level data, "
                        "PDF for driver-explanation reports. The "
                        "server rejects mismatched combinations."
                    ),
                },
                "filename_hint": {
                    "type": "STRING",
                    "description": (
                        "Optional human-readable filename hint. The "
                        "server sanitizes and clamps it; "
                        "filesystem-unsafe characters are replaced."
                    ),
                },
            },
            "required": ["tool_name"],
        },
    },
]


# Scenario state held in conversation memory between tool calls within a
# single chat turn. The agent threads this through the conductor's tool loop.
class _ScenarioState:
    def __init__(self) -> None:
        self.starting_cash: float | None = None
        self.projection_start_month: str | None = None
        self.horizon_weeks: int = 13
        self.overrides: list[Override] = []
        self.manual_cost_assumptions: list[ManualCostAssumption] = []
        self.manual_recurring_revenue: list[RecurringRevenueAssumption] = []
        self.sales_modifier: float | None = None
        self.cost_modifier: float | None = None
        # Tracks the run id from the most recent run_projection call in this
        # chat turn, so the next run can persist parent_run_id for v1.1's
        # compare_runs traversal.
        self.last_projection_run_id: str | None = None
        # Runtime MRR baseline knobs. None means "let the loader's
        # defaults win" — the loader keeps 50300 as the product
        # default and fallback on, but a caller can override either
        # for a single projection turn without mutating the global
        # default.
        self.mrr_account_prefix: str | None = None
        self.use_mrr_fallback: bool | None = None
        # Calendar anchor for the "current month is not yet
        # complete" guard. The dispatcher sets this at the boundary
        # via ``date.today()`` so the helpers receive a deterministic
        # current_date instead of calling date.today() deep inside
        # the data layer. Tests override it with a synthetic date.
        self.current_date: "date | None" = None

    def to_inputs_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "starting_cash": self.starting_cash,
            "projection_start_month": self.projection_start_month,
            "horizon_weeks": self.horizon_weeks,
            "overrides": list(self.overrides),
            "manual_cost_assumptions": list(self.manual_cost_assumptions),
            "manual_recurring_revenue": list(self.manual_recurring_revenue),
        }
        if self.mrr_account_prefix is not None:
            kwargs["mrr_account_prefix"] = self.mrr_account_prefix
        if self.use_mrr_fallback is not None:
            kwargs["use_mrr_fallback"] = self.use_mrr_fallback
        if self.current_date is not None:
            kwargs["current_date"] = self.current_date
        return kwargs

    # Serialization for cross-turn persistence on the chat session document.
    # Conductor reads/writes session.nova_state so manual assumptions and
    # overrides survive between user turns; without it, "add a payroll cost"
    # in turn 1 would be wiped before "now run the projection" in turn 2.
    def to_dict(self) -> dict[str, Any]:
        return {
            "starting_cash": self.starting_cash,
            "projection_start_month": self.projection_start_month,
            "horizon_weeks": self.horizon_weeks,
            "overrides": [o.model_dump(mode="json") for o in self.overrides],
            "manual_cost_assumptions": [
                a.model_dump(mode="json") for a in self.manual_cost_assumptions
            ],
            "manual_recurring_revenue": [
                a.model_dump(mode="json") for a in self.manual_recurring_revenue
            ],
            "sales_modifier": self.sales_modifier,
            "cost_modifier": self.cost_modifier,
            "last_projection_run_id": self.last_projection_run_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "_ScenarioState":
        """Rehydrate from the serialized form. Tolerates partial/malformed
        rows by skipping them - a corrupt persisted assumption must not
        break the next chat turn. The caller can choose to start fresh by
        passing None or an empty dict.
        """
        inst = cls()
        if not data:
            return inst
        inst.starting_cash = data.get("starting_cash")
        inst.projection_start_month = data.get("projection_start_month")
        inst.horizon_weeks = data.get("horizon_weeks", 13) or 13
        inst.sales_modifier = data.get("sales_modifier")
        inst.cost_modifier = data.get("cost_modifier")
        inst.last_projection_run_id = data.get("last_projection_run_id")

        for raw in data.get("overrides") or []:
            try:
                inst.overrides.append(Override(**raw))
            except (ValidationError, TypeError, ValueError) as e:
                logger.warning(
                    "Dropped malformed persisted override %r: %s", raw, e
                )
        for raw in data.get("manual_cost_assumptions") or []:
            try:
                inst.manual_cost_assumptions.append(ManualCostAssumption(**raw))
            except (ValidationError, TypeError, ValueError) as e:
                logger.warning(
                    "Dropped malformed persisted manual cost assumption %r: %s",
                    raw, e,
                )
        for raw in data.get("manual_recurring_revenue") or []:
            try:
                inst.manual_recurring_revenue.append(
                    RecurringRevenueAssumption(**raw)
                )
            except (ValidationError, TypeError, ValueError) as e:
                logger.warning(
                    "Dropped malformed persisted recurring revenue %r: %s",
                    raw, e,
                )
        return inst

    def reset(self) -> None:
        """In-place wipe used by the reset_scenario_state tool. Mirrors
        __init__'s defaults so a reset state is indistinguishable from a
        fresh one.
        """
        self.starting_cash = None
        self.projection_start_month = None
        self.horizon_weeks = 13
        self.overrides = []
        self.manual_cost_assumptions = []
        self.manual_recurring_revenue = []
        self.sales_modifier = None
        self.cost_modifier = None
        self.last_projection_run_id = None


def _invalid_or_missing_field_error(error: Exception) -> dict[str, str]:
    """Return a stable tool error for bad LLM-supplied scalar fields."""
    return {
        "error": "invalid_or_missing_field",
        "detail": f"Invalid or missing field: {error}",
    }


def _coerce_override(item: dict[str, Any]) -> Override:
    return Override(
        type=item["type"],
        target=item["target"],
        week_number=item.get("week_number"),
        amount=item.get("amount"),
        source_week=item.get("source_week"),
        shift_weeks=item.get("shift_weeks"),
    )


def _coerce_manual_cost_assumption(item: dict[str, Any]) -> ManualCostAssumption:
    return ManualCostAssumption(
        name=item["name"],
        amount_per_month=float(item["amount_per_month"]),
        line_type=item["line_type"],
        starts_month=item.get("starts_month"),
        ends_month=item.get("ends_month"),
        weekly_dist=item.get("weekly_dist"),
    )


def _coerce_manual_revenue_assumption(item: dict[str, Any]) -> RecurringRevenueAssumption:
    return RecurringRevenueAssumption(
        name=item["name"],
        amount_per_month=float(item["amount_per_month"]),
        line_type=item["line_type"],
        starts_month=item.get("starts_month"),
        ends_month=item.get("ends_month"),
        weekly_dist=item.get("weekly_dist"),
    )


# ---------------------------------------------------------------------------
# compare_runs helpers
# ---------------------------------------------------------------------------
# Compare two persisted projection runs by their stored output. We never
# re-run the engine here - reading is a separate operation from projecting,
# and the dispatcher keeps it that way so a comparison is cheap and
# deterministic regardless of when the runs were generated.

# Cap the weekly-delta list so a 90-week run can't blow up the tool response
# size. Five rows captures the user-meaningful swings; more is noise.
_COMPARE_TOP_WEEKLY_DELTAS = 5


def _compare_kpi_delta(base_out: dict, compare_out: dict, key: str) -> float:
    """Subtract base[key] from compare[key] using a 0.0 fallback so a
    missing field never raises - older run docs may lack a KPI."""
    def _f(d: dict) -> float:
        v = d.get(key)
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0
    return _f(compare_out) - _f(base_out)


def _compare_summary_block(run: dict, output_doc: dict) -> dict[str, Any]:
    """Per-run summary keys returned alongside deltas so the LLM can
    quote either run's KPIs without a second tool call."""
    inputs_doc = run.get("inputs") or {}
    return {
        "projection_run_id": run.get("projection_run_id"),
        "projection_start_month": inputs_doc.get("projection_start_month"),
        "horizon_weeks": inputs_doc.get("horizon_weeks"),
        "starting_cash": output_doc.get("starting_cash"),
        "ending_cash": output_doc.get("ending_cash"),
        "minimum_cash": output_doc.get("minimum_cash"),
        "minimum_cash_week": output_doc.get("minimum_cash_week"),
        "total_revenue": output_doc.get("total_revenue"),
        "total_costs": output_doc.get("total_costs"),
        "net_change": output_doc.get("net_change"),
    }


def _compare_top_weekly_deltas(
    base_out: dict, compare_out: dict, top_k: int = _COMPARE_TOP_WEEKLY_DELTAS,
) -> list[dict[str, Any]]:
    """Return the top-K weeks by absolute ending-cash delta.

    Indexed by week_number so two horizons that share a prefix line up
    correctly. A week present in only one run still gets a row - its
    missing side is treated as 0 for delta computation, which mirrors
    how the engine emits that week's contribution.
    """
    base_by_wn = {
        w.get("week_number"): w
        for w in (base_out.get("weeks") or [])
        if w.get("week_number") is not None
    }
    compare_by_wn = {
        w.get("week_number"): w
        for w in (compare_out.get("weeks") or [])
        if w.get("week_number") is not None
    }

    rows: list[dict[str, Any]] = []
    for wn in sorted(set(base_by_wn) | set(compare_by_wn)):
        b = base_by_wn.get(wn) or {}
        c = compare_by_wn.get(wn) or {}
        try:
            be = float(b.get("ending_cash") or 0.0)
        except (TypeError, ValueError):
            be = 0.0
        try:
            ce = float(c.get("ending_cash") or 0.0)
        except (TypeError, ValueError):
            ce = 0.0
        rows.append({
            "week_number": wn,
            "week_starting": (
                c.get("week_starting") or b.get("week_starting")
            ),
            "base_ending_cash": be,
            "compare_ending_cash": ce,
            "delta": ce - be,
        })

    rows.sort(key=lambda r: abs(r["delta"]), reverse=True)
    return rows[:top_k]


def _format_compare_runs_summary_text(payload: dict[str, Any]) -> str:
    """Render the compare_runs payload as a readable plain-English block.

    Two surfaces consume this:
      1. The LLM, when the model produces a tool call but no narration -
         the conductor's fallback path can show this verbatim instead of
         "Nova could not complete that request."
      2. The model itself, when narrating the comparison; it can quote
         the wording rather than recomputing percentages and getting
         them wrong.
    """
    deltas = payload.get("deltas") or {}
    input_changes = payload.get("input_changes") or {}
    summaries = payload.get("summaries") or {}
    base_summary = summaries.get("base") or {}
    compare_summary = summaries.get("compare") or {}
    weekly_deltas = payload.get("weekly_deltas") or []
    min_week = payload.get("minimum_cash_week") or {}

    base_id = payload.get("base_run_id") or "(base run)"
    compare_id = payload.get("compare_run_id") or "(compare run)"

    ending_delta = deltas.get("ending_cash", 0.0) or 0.0
    # The verb already states the direction; render the magnitude only
    # so the phrase reads "improved by $5,000.00" instead of the
    # "improved by $+5,000.00" sign-noise the +,.2f format produced.
    if ending_delta > 0:
        direction_phrase = f"improved by ${abs(ending_delta):,.2f}"
    elif ending_delta < 0:
        direction_phrase = f"worsened by ${abs(ending_delta):,.2f}"
    else:
        direction_phrase = "held flat"

    base_ending = base_summary.get("ending_cash")
    compare_ending = compare_summary.get("ending_cash")

    lines = [
        f"Comparing run `{base_id}` (base) against run `{compare_id}` (compare):",
        "",
        "| Metric | Base | Compare | Δ |",
        "|---|---:|---:|---:|",
        (
            f"| Ending cash | "
            f"{(base_ending or 0):,.2f} | "
            f"{(compare_ending or 0):,.2f} | "
            f"{ending_delta:+,.2f} |"
        ),
        (
            f"| Minimum cash | "
            f"{(base_summary.get('minimum_cash') or 0):,.2f} | "
            f"{(compare_summary.get('minimum_cash') or 0):,.2f} | "
            f"{(deltas.get('minimum_cash') or 0):+,.2f} |"
        ),
        (
            f"| Total revenue | "
            f"{(base_summary.get('total_revenue') or 0):,.2f} | "
            f"{(compare_summary.get('total_revenue') or 0):,.2f} | "
            f"{(deltas.get('total_revenue') or 0):+,.2f} |"
        ),
        (
            f"| Total costs | "
            f"{(base_summary.get('total_costs') or 0):,.2f} | "
            f"{(compare_summary.get('total_costs') or 0):,.2f} | "
            f"{(deltas.get('total_costs') or 0):+,.2f} |"
        ),
        (
            f"| Net change | "
            f"{(base_summary.get('net_change') or 0):,.2f} | "
            f"{(compare_summary.get('net_change') or 0):,.2f} | "
            f"{(deltas.get('net_change') or 0):+,.2f} |"
        ),
        "",
        f"Ending cash {direction_phrase}.",
    ]

    if min_week.get("shifted"):
        lines.append(
            f"Minimum-cash week shifted from week {min_week.get('base')} "
            f"to week {min_week.get('compare')}."
        )

    # Render input changes (e.g. starting_cash) in their own section
    # when non-zero. starting_cash is a user assumption, not a
    # cash-flow delta, so quoting it next to ending_cash in the main
    # table would conflate user-side adjustments with computed
    # projection movement.
    starting_cash_change = _safe_float(input_changes.get("starting_cash"))
    if starting_cash_change:
        base_starting = base_summary.get("starting_cash")
        compare_starting = compare_summary.get("starting_cash")
        lines.append("")
        lines.append(
            f"Inputs that changed: starting_cash from "
            f"${(base_starting or 0):,.2f} to "
            f"${(compare_starting or 0):,.2f} "
            f"(Δ ${starting_cash_change:+,.2f})."
        )

    if weekly_deltas:
        lines.append("")
        lines.append("Top weekly ending-cash differences:")
        lines.append("")
        lines.append("| Week | Week Starting | Base | Compare | Δ |")
        lines.append("|---:|---|---:|---:|---:|")
        for row in weekly_deltas:
            lines.append(
                f"| {row.get('week_number', '')} | "
                f"{row.get('week_starting', '')} | "
                f"{(row.get('base_ending_cash') or 0):,.2f} | "
                f"{(row.get('compare_ending_cash') or 0):,.2f} | "
                f"{(row.get('delta') or 0):+,.2f} |"
            )

    return "\n".join(lines)


def _build_compare_runs_payload(
    *,
    base_run: dict,
    compare_run: dict,
    include_weekly_deltas: bool = True,
) -> dict[str, Any]:
    """Assemble the compare_runs tool response from two persisted run docs.

    Pure function - the caller is responsible for fetching the runs and
    enforcing user_id scoping. Splitting this out keeps the dispatcher's
    auth/lookup separate from the math, and lets the conductor's
    no-text fallback render the same payload via summary_text.
    """
    base_out = base_run.get("output") or {}
    compare_out = compare_run.get("output") or {}

    # starting_cash is a user-supplied input, not a computed output.
    # Its delta means "the user changed their assumption," not
    # "cash flow moved." It belongs in input_changes, not deltas;
    # rendering it next to ending_cash in the KPI table would conflate
    # assumption changes with projection movement.
    deltas = {
        "ending_cash": _compare_kpi_delta(base_out, compare_out, "ending_cash"),
        "minimum_cash": _compare_kpi_delta(base_out, compare_out, "minimum_cash"),
        "total_revenue": _compare_kpi_delta(base_out, compare_out, "total_revenue"),
        "total_costs": _compare_kpi_delta(base_out, compare_out, "total_costs"),
        "net_change": _compare_kpi_delta(base_out, compare_out, "net_change"),
    }
    input_changes = {
        "starting_cash": _compare_kpi_delta(base_out, compare_out, "starting_cash"),
    }
    base_min_week = base_out.get("minimum_cash_week")
    compare_min_week = compare_out.get("minimum_cash_week")

    weekly_deltas: list[dict[str, Any]] = []
    if include_weekly_deltas:
        weekly_deltas = _compare_top_weekly_deltas(base_out, compare_out)

    payload: dict[str, Any] = {
        "base_run_id": base_run.get("projection_run_id"),
        "compare_run_id": compare_run.get("projection_run_id"),
        "deltas": deltas,
        "input_changes": input_changes,
        "minimum_cash_week": {
            "base": base_min_week,
            "compare": compare_min_week,
            "shifted": (
                base_min_week is not None
                and compare_min_week is not None
                and base_min_week != compare_min_week
            ),
        },
        "summaries": {
            "base": _compare_summary_block(base_run, base_out),
            "compare": _compare_summary_block(compare_run, compare_out),
        },
        "weekly_deltas": weekly_deltas,
    }
    payload["summary_text"] = _format_compare_runs_summary_text(payload)
    return payload


# ---------------------------------------------------------------------------
# Dynamic projection input resolver
# ---------------------------------------------------------------------------
#
# Splices user-supplied account-average prefixes, AR/AP timing
# rules, and a payroll schedule into the ScenarioState before the
# engine runs. Each block is independent — passing no input keeps
# the existing baseline behaviour. The helper does NOT touch the
# engine math; it converts caller intent into the existing
# ManualCostAssumption / Override primitives the engine already
# consumes.

# Default for the optional ``mrr_account_prefix`` arg. The
# product's existing MRR mapping today is 50300 — leaving this
# constant ONLY here (not hardcoded into every test or branch)
# means a future tenant overriding the prefix at runtime works
# without code changes. The value is still surfaced in the audit
# metadata so reviewers see which prefix the engine actually used.
_PRODUCT_DEFAULT_MRR_PREFIX = "50300"


# Per-page size for the AR / AP scheduler fetchers. Matches the
# OpenInvoiceQuery / get_open_bills hard caps; the fetchers loop
# across pages until either the total is exhausted or the schedule
# cap is reached.
_AR_FETCH_PAGE_SIZE = 200
_AP_FETCH_PAGE_SIZE = 200

# Hard upper bound on rows the scheduler will collect before declaring
# the result truncated. 5,000 covers SimpliMeta-sized tenants (~600
# open invoices typical, ~200 open bills) with a comfortable safety
# margin while keeping the worst-case fetch latency bounded. Bigger
# tenants will see ``truncated=true`` plus a warning naming exactly
# how many rows were fetched vs available; they can narrow the
# horizon or pass an explicit AR filter to reduce the row set.
_AR_SCHEDULE_FETCH_CAP = 5_000
_AP_SCHEDULE_FETCH_CAP = 5_000


async def _fetch_open_invoice_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pull the open-invoice rows the AR helper schedules off of.
    Uses the same data-access path as the read-only get_open_invoices
    tool (``query_open_invoice_rows``) so AR scheduling sees exactly
    what Nova would see if it asked for the AR list directly.

    Pages through the result set in ``_AR_FETCH_PAGE_SIZE``-row
    pages (the repository's per-call hard cap) until either every
    matched row is collected or ``_AR_SCHEDULE_FETCH_CAP`` is hit.
    Returns ``(rows, fetch_meta)`` where ``fetch_meta`` carries the
    audit fields the chat tool surfaces verbatim in the
    ``ar_receipts_schedule`` block: ``fetched_row_count``,
    ``total_available_rows``, ``schedule_cap``, ``truncated``,
    ``source``, ``page_size``.
    """
    from services.nova_engine.invoice_repository import (
        OpenInvoiceQuery,
        query_open_invoice_rows,
    )

    rows: list[dict[str, Any]] = []
    total_available: int | None = None
    offset = 0
    while offset < _AR_SCHEDULE_FETCH_CAP:
        page_limit = min(
            _AR_FETCH_PAGE_SIZE,
            _AR_SCHEDULE_FETCH_CAP - offset,
        )
        result = await query_open_invoice_rows(
            OpenInvoiceQuery(limit=page_limit, offset=offset),
        )
        # total_matched is stable across pages — capture from first
        # page and use as the truncation oracle.
        if total_available is None:
            total_available = result.total_matched
        for row in result.rows:
            rows.append({
                "due_date": row.due_date,
                "open_balance": row.open_balance,
                "customer": row.customer,
                "doc_num": row.invoice_number,
            })
        page_returned = len(result.rows)
        # Loop terminates when the page came back short (we've
        # consumed the full result set) OR we've collected every
        # known row (defensive against an adapter that under-reports
        # total_matched).
        if page_returned < page_limit:
            break
        if total_available is not None and len(rows) >= total_available:
            break
        offset += page_returned

    truncated = bool(
        total_available is not None and len(rows) < total_available
    )
    fetch_meta: dict[str, Any] = {
        "source": "open_invoice_snapshot",
        "fetched_row_count": len(rows),
        "total_available_rows": total_available,
        "schedule_cap": _AR_SCHEDULE_FETCH_CAP,
        "page_size": _AR_FETCH_PAGE_SIZE,
        "truncated": truncated,
    }
    return rows, fetch_meta


async def _fetch_open_bill_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pull open-bill rows the AP helper schedules off of. Mirrors
    the path the read-only get_open_bills tool uses (qbo_db.ap_bills
    with ``open_only=True``).

    Pages through the collection in ``_AP_FETCH_PAGE_SIZE``-row
    pages and reports a fetch_meta block alongside the rows so the
    chat surface can quote fetched vs available counts. Mongo's
    count_documents() seeds ``total_available_rows`` when the
    underlying source supports it; pages stop once the cap is hit
    or the cursor is exhausted.
    """
    from services.nova_engine.db import get_qbo_db

    qbo_db = get_qbo_db()
    filt: dict[str, Any] = {"balance": {"$gt": 0}}

    # count_documents is cheap on indexed scalar predicates; surface
    # the total so the chat tool can quote "X of Y open bills".
    try:
        total_available: int | None = await qbo_db.ap_bills.count_documents(filt)
    except Exception:  # noqa: BLE001 — adapter without count is acceptable
        total_available = None

    rows: list[dict[str, Any]] = []
    offset = 0
    while offset < _AP_SCHEDULE_FETCH_CAP:
        page_limit = min(
            _AP_FETCH_PAGE_SIZE,
            _AP_SCHEDULE_FETCH_CAP - offset,
        )
        cursor = (
            qbo_db.ap_bills.find(filt, {"_id": 0})
            .skip(offset)
            .limit(page_limit)
        )
        docs = await cursor.to_list(length=page_limit)
        if not docs:
            break
        for d in docs:
            raw = d.get("raw_data") or {}
            rows.append({
                "due_date": d.get("due_date") or raw.get("DueDate"),
                "open_balance": d.get("balance"),
                "vendor": (raw.get("VendorRef") or {}).get("name")
                           if isinstance(raw.get("VendorRef"), dict) else None,
                "doc_num": raw.get("DocNumber"),
            })
        if len(docs) < page_limit:
            break
        if total_available is not None and len(rows) >= total_available:
            break
        offset += len(docs)

    truncated = bool(
        total_available is not None and len(rows) < total_available
    )
    fetch_meta: dict[str, Any] = {
        "source": "ap_bills",
        "fetched_row_count": len(rows),
        "total_available_rows": total_available,
        "schedule_cap": _AP_SCHEDULE_FETCH_CAP,
        "page_size": _AP_FETCH_PAGE_SIZE,
        "truncated": truncated,
    }
    return rows, fetch_meta


async def _resolve_dynamic_projection_inputs(
    *,
    state: _ScenarioState,
    account_average_prefixes: list[str] | None,
    ar_receipts_timing: dict[str, Any] | None,
    ap_payments_timing: dict[str, Any] | None,
    payroll_schedule: dict[str, Any] | None,
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    """Resolve the four dynamic-input blocks and mutate ``state``
    in place with the resulting ManualCostAssumptions / Overrides.

    Returns ``(account_average_baselines, ar_receipts_schedule,
    ap_payments_schedule, payroll_schedule)`` — each is None when
    the caller did not pass the corresponding input, or a metadata
    dict the chat tool surfaces verbatim otherwise.

    The four blocks never share state; resolving one does not affect
    the others. Account averages become additive ``ManualCostAssumption``
    entries. AR / AP / payroll schedules become ``Override`` entries on
    specific weeks. All of these are spliced onto ``state`` so
    ``build_projection_inputs_from_globals`` sees them when it runs.
    """
    from services.nova_engine.data_loader import (
        load_account_prefix_averages,
    )
    from services.nova_engine.db import get_qbo_db
    from services.nova_engine.scheduled_events import (
        compute_ap_schedule,
        compute_ar_schedule,
        compute_payroll_schedule,
    )

    horizon_weeks = state.horizon_weeks
    projection_start_month = state.projection_start_month
    assert projection_start_month is not None  # set by caller before this

    # ── Account-prefix averages → additive ManualCostAssumption ──
    account_average_baselines: dict[str, Any] | None = None
    if account_average_prefixes is not None:
        qbo_db = get_qbo_db()
        # The reference_date anchors the helper's lookback walk on
        # the month BEFORE projection_start_month — same convention
        # the engine uses for last-3-month averages elsewhere.
        # Reference_date is encoded as YYYY-MM-01 of the projection
        # start month; the helper strips the day.
        account_average_baselines = await load_account_prefix_averages(
            qbo_db,
            basis="accrual",
            account_prefixes=account_average_prefixes,
            reference_date=f"{projection_start_month}-01",
            months=3,
            current_date=state.current_date,
        )
        # Tag the block so the chat tool can tell at a glance that
        # these averages are ADDITIVE manual cost assumptions, not
        # replacements of the engine's baseline cost projection.
        account_average_baselines["application_mode"] = "additive_manual_cost_assumption"
        account_average_baselines["generated_source"] = "dynamic_account_average_cost"
        # Drop any prior generated account-average assumptions from
        # state BEFORE adding the new ones — keeps reruns idempotent
        # and preserves user-created manual_cost_assumptions
        # (generated_source is None on those).
        state.manual_cost_assumptions = [
            mca
            for mca in state.manual_cost_assumptions
            if getattr(mca, "generated_source", None) != "dynamic_account_average_cost"
        ]
        # Per-batch dedupe so two prefixes that happen to produce
        # the same (prefix, months_used, amount) collapse silently
        # and the chat surface can quote the count.
        months_used_joined = ",".join(
            account_average_baselines.get("months_used") or []
        )
        seen_dedupe_keys: set[str] = set()
        generated_count = 0
        deduped_count = 0
        for entry in account_average_baselines.get("accounts", []):
            avg = entry.get("average_monthly_amount") or 0.0
            if avg <= 0:
                continue
            prefix = entry.get("account_prefix")
            dedupe_key = (
                f"dynamic_account_average_cost:"
                f"{prefix}:{months_used_joined}:"
                f"{float(avg)}:OTHER_COSTS"
            )
            if dedupe_key in seen_dedupe_keys:
                deduped_count += 1
                continue
            seen_dedupe_keys.add(dedupe_key)
            # We label every prefix average under OTHER_COSTS to
            # avoid colliding with the engine's revenue-driven
            # COGS / payroll projections. Users who want a tighter
            # categorisation can pass their own
            # manual_cost_assumptions.
            state.manual_cost_assumptions.append(ManualCostAssumption(
                name=f"account_avg_{prefix}",
                amount_per_month=float(avg),
                line_type="OTHER_COSTS",
                starts_month=projection_start_month,
                ends_month=None,
                generated_source="dynamic_account_average_cost",
                dedupe_key=dedupe_key,
            ))
            generated_count += 1
        account_average_baselines["generated_assumption_count"] = generated_count
        account_average_baselines["deduped_duplicate_count"] = deduped_count

    # ── AR receipts schedule → Override(new_revenue) on each event ──
    ar_receipts_schedule: dict[str, Any] | None = None
    if ar_receipts_timing is not None:
        method = ar_receipts_timing.get("method")
        if method != "due_date_plus_days":
            ar_receipts_schedule = {
                "method": method,
                "days_after_due_date": None,
                "row_count": 0,
                "scheduled_receipts": [],
                "warnings": [
                    f"ar_receipts_timing.method {method!r} is not "
                    "supported. Supported: ['due_date_plus_days']."
                ],
            }
        else:
            days = ar_receipts_timing.get("days_after_due_date", 0)
            try:
                days = int(days)
            except (TypeError, ValueError):
                days = -1  # forces the helper's structured warning
            ar_rows, ar_fetch_meta = await _fetch_open_invoice_rows()
            overrides, ar_receipts_schedule = compute_ar_schedule(
                open_invoice_rows=ar_rows,
                days_after_due_date=days,
                projection_start_month=projection_start_month,
                horizon_weeks=horizon_weeks,
                fetch_meta=ar_fetch_meta,
            )
            # Strip prior dynamic_ar_receipt_schedule overrides
            # before appending the new batch. User-created
            # overrides (generated_source=None) survive.
            state.overrides = [
                o for o in state.overrides
                if getattr(o, "generated_source", None) != "dynamic_ar_receipt_schedule"
            ]
            state.overrides.extend(overrides)

    # ── AP payments schedule → Override(new_cost) on due date ──
    ap_payments_schedule: dict[str, Any] | None = None
    if ap_payments_timing is not None:
        method = ap_payments_timing.get("method")
        if method != "due_date":
            ap_payments_schedule = {
                "method": method,
                "row_count": 0,
                "scheduled_payments": [],
                "warnings": [
                    f"ap_payments_timing.method {method!r} is not "
                    "supported. Supported: ['due_date']."
                ],
            }
        else:
            ap_rows, ap_fetch_meta = await _fetch_open_bill_rows()
            overrides, ap_payments_schedule = compute_ap_schedule(
                open_bill_rows=ap_rows,
                projection_start_month=projection_start_month,
                horizon_weeks=horizon_weeks,
                fetch_meta=ap_fetch_meta,
            )
            state.overrides = [
                o for o in state.overrides
                if getattr(o, "generated_source", None) != "dynamic_ap_payment_schedule"
            ]
            state.overrides.extend(overrides)

    # ── Payroll schedule → Override(new_cost, PAYROLL) on each pay date ──
    payroll_schedule_meta: dict[str, Any] | None = None
    if payroll_schedule is not None:
        overrides, payroll_schedule_meta = compute_payroll_schedule(
            amount=payroll_schedule.get("amount", 0),
            start_date=payroll_schedule.get("start_date") or "",
            frequency=payroll_schedule.get("frequency") or "",
            projection_start_month=projection_start_month,
            horizon_weeks=horizon_weeks,
        )
        # Strip prior dynamic_payroll_schedule overrides so reruns
        # of the same projection don't multiply payroll into $40k /
        # $60k weekly totals. User-created PAYROLL overrides
        # (generated_source=None) are preserved.
        state.overrides = [
            o for o in state.overrides
            if getattr(o, "generated_source", None) != "dynamic_payroll_schedule"
        ]
        state.overrides.extend(overrides)

    return (
        account_average_baselines,
        ar_receipts_schedule,
        ap_payments_schedule,
        payroll_schedule_meta,
    )


async def execute_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    state: _ScenarioState,
    user_id: str,
    session_id: str | None,
    message_id: str | None,
) -> dict[str, Any]:
    """Dispatch a tool call. Mutates state for run/override/assumption tools.

    Returns a dict that becomes the tool_response payload sent back to the LLM.
    """
    from services.nova_engine.data_loader import build_projection_inputs_from_globals
    from services.nova_engine.engine import project_cash_flow
    from services.nova_engine.persistence import get_run, list_runs, save_run

    if tool_name == "run_projection":
        try:
            starting_cash = float(tool_args["starting_cash"])
            # Capture whether the caller explicitly named a horizon
            # so the response can disclose the 13-week default rather
            # than silently picking it. The audit field
            # ``horizon_was_defaulted`` flows into the response below.
            horizon_was_defaulted = "horizon_weeks" not in tool_args
            horizon_weeks = int(tool_args.get("horizon_weeks", 13))
            projection_start_month = tool_args["projection_start_month"]
        except (KeyError, ValueError, TypeError) as e:
            return {
                "error": "invalid_projection_input",
                "detail": f"Invalid or missing field: {e}",
            }

        if not _is_valid_projection_month(projection_start_month):
            return {
                "error": "invalid_projection_start_month",
                "detail": "projection_start_month must be YYYY-MM.",
            }

        # Coerce every caller-supplied value into locals BEFORE
        # touching state. If the scalars are written first and the
        # lists/modifiers are extended inside a try block, a malformed
        # manual_cost_assumptions entry partway through half-mutates
        # state and the next tool call runs against a scenario the
        # user thought they reverted. Collecting locals first makes
        # the dispatcher atomic: any coercion failure leaves state
        # unchanged.
        new_cost_assumptions: list[ManualCostAssumption] = []
        new_recurring_revenue: list[RecurringRevenueAssumption] = []
        new_overrides: list[Override] = []
        new_sales_modifier: float | None = None
        new_cost_modifier: float | None = None
        sales_modifier_set = False
        cost_modifier_set = False
        try:
            for item in (tool_args.get("manual_cost_assumptions") or []):
                new_cost_assumptions.append(_coerce_manual_cost_assumption(item))
            for item in (tool_args.get("manual_recurring_revenue") or []):
                new_recurring_revenue.append(_coerce_manual_revenue_assumption(item))
            for item in (tool_args.get("overrides") or []):
                new_overrides.append(_coerce_override(item))
            if "sales_modifier" in tool_args:
                new_sales_modifier = float(tool_args["sales_modifier"])
                sales_modifier_set = True
            if "cost_modifier" in tool_args:
                new_cost_modifier = float(tool_args["cost_modifier"])
                cost_modifier_set = True
        except (KeyError, ValidationError, ValueError, TypeError) as e:
            return {"error": "invalid_projection_input", "detail": str(e)}

        # ── Coerce the new generic projection inputs ──
        # Each block is permissive at the schema level (the LLM may
        # pass partial dicts); validation happens here so a malformed
        # entry returns a structured error rather than crashing the
        # engine. None means "the caller did not ask for this
        # feature" — keep the existing baseline behaviour.
        account_average_prefixes_input = tool_args.get("account_average_prefixes")
        mrr_account_prefix_input = tool_args.get("mrr_account_prefix")
        use_mrr_fallback_input = tool_args.get("use_mrr_fallback")
        ar_receipts_timing_input = tool_args.get("ar_receipts_timing")
        ap_payments_timing_input = tool_args.get("ap_payments_timing")
        payroll_schedule_input = tool_args.get("payroll_schedule")
        if account_average_prefixes_input is not None and not isinstance(
            account_average_prefixes_input, list
        ):
            return {
                "error": "invalid_projection_input",
                "detail": "account_average_prefixes must be a list of strings.",
            }
        for arg_name, val in (
            ("ar_receipts_timing", ar_receipts_timing_input),
            ("ap_payments_timing", ap_payments_timing_input),
            ("payroll_schedule", payroll_schedule_input),
        ):
            if val is not None and not isinstance(val, dict):
                return {
                    "error": "invalid_projection_input",
                    "detail": f"{arg_name} must be an object when supplied.",
                }

        # ── Date-anchor guard ──
        # The LLM has historically misread "today" as a training-data
        # year (e.g. projecting a 2026 request as 2024). When the
        # user supplies a payroll_schedule.start_date, its YYYY-MM
        # MUST match projection_start_month — otherwise the
        # projection runs on the wrong year. Reject explicitly rather
        # than silently producing a wrong-year forecast.
        #
        # Escape hatch: ``allow_historical_projection=true`` opts out
        # of the guard for the rare case a user really wants a
        # back-dated what-if. Today only the chat tool layer surfaces
        # this; if Nova passes it without the user asking, the
        # prompt's "do not silently coerce dates" rule catches the
        # mistake at review time.
        allow_historical = bool(tool_args.get("allow_historical_projection"))
        if (
            isinstance(payroll_schedule_input, dict)
            and not allow_historical
        ):
            payroll_start = payroll_schedule_input.get("start_date")
            if isinstance(payroll_start, str) and len(payroll_start) >= 7:
                payroll_ym = payroll_start[:7]
                if payroll_ym != projection_start_month:
                    return {
                        "error": "projection_date_mismatch",
                        "detail": (
                            f"projection_start_month {projection_start_month} "
                            f"conflicts with payroll_schedule.start_date "
                            f"{payroll_start}. Use "
                            f"{payroll_ym} as projection_start_month or "
                            "confirm a historical projection by passing "
                            "allow_historical_projection=true."
                        ),
                        "projection_start_month": projection_start_month,
                        "payroll_start_date": payroll_start,
                        "payroll_start_month": payroll_ym,
                    }

        # All coercions succeeded - safe to mutate state atomically.
        state.starting_cash = starting_cash
        state.projection_start_month = projection_start_month
        state.horizon_weeks = horizon_weeks
        state.manual_cost_assumptions.extend(new_cost_assumptions)
        state.manual_recurring_revenue.extend(new_recurring_revenue)
        state.overrides.extend(new_overrides)
        if sales_modifier_set:
            state.sales_modifier = new_sales_modifier
        if cost_modifier_set:
            state.cost_modifier = new_cost_modifier
        if isinstance(mrr_account_prefix_input, str) and mrr_account_prefix_input.strip():
            state.mrr_account_prefix = mrr_account_prefix_input.strip()
        if isinstance(use_mrr_fallback_input, bool):
            state.use_mrr_fallback = use_mrr_fallback_input
        # Capture today's calendar date ONCE at the dispatcher
        # boundary so the data layer never calls date.today() deep
        # inside business logic. The helpers then enforce
        # "month_end < current_date" deterministically; tests inject
        # a synthetic current_date via state.current_date.
        if state.current_date is None:
            from datetime import date as _today_date

            state.current_date = _today_date.today()

        # ── Resolve dynamic baseline / schedule metadata ──
        # All four helpers are pure: they take rows/config and return
        # (additions, metadata). The additions get spliced onto the
        # ScenarioState as manual cost assumptions / overrides before
        # the engine runs, so the projection sees them in week 1 same
        # as any caller-supplied scenario state.
        account_average_baselines: dict[str, Any] | None = None
        ar_receipts_schedule: dict[str, Any] | None = None
        ap_payments_schedule: dict[str, Any] | None = None
        payroll_schedule_meta: dict[str, Any] | None = None

        try:
            (
                account_average_baselines,
                ar_receipts_schedule,
                ap_payments_schedule,
                payroll_schedule_meta,
            ) = await _resolve_dynamic_projection_inputs(
                state=state,
                account_average_prefixes=account_average_prefixes_input,
                ar_receipts_timing=ar_receipts_timing_input,
                ap_payments_timing=ap_payments_timing_input,
                payroll_schedule=payroll_schedule_input,
            )
        except (ValueError, TypeError) as e:
            return {
                "error": "invalid_projection_input",
                "detail": f"Failed to resolve dynamic inputs: {e}",
            }

        try:
            inputs = await build_projection_inputs_from_globals(
                **state.to_inputs_kwargs()
            )
            modifier_updates = {}
            if state.sales_modifier is not None:
                modifier_updates["sales_modifier"] = state.sales_modifier
            if state.cost_modifier is not None:
                modifier_updates["cost_modifier"] = state.cost_modifier
            if modifier_updates:
                inputs = inputs.model_copy(update=modifier_updates)
            output = project_cash_flow(inputs)
        except InsufficientHistoryError as e:
            err_payload = _insufficient_history_tool_error(e)
            # Even on failure, surface the dynamic-input metadata so
            # the user can see which schedule blocks resolved before
            # the engine ran. Helps "why did MRR fail?" follow-ups.
            for key, val in (
                ("account_average_baselines", account_average_baselines),
                ("ar_receipts_schedule", ar_receipts_schedule),
                ("ap_payments_schedule", ap_payments_schedule),
                ("payroll_schedule", payroll_schedule_meta),
            ):
                if val is not None:
                    err_payload[key] = val
            return err_payload

        run = await save_run(
            user_id=user_id,
            inputs=inputs,
            output=output,
            session_id=session_id,
            message_id=message_id,
            parent_run_id=state.last_projection_run_id,
        )
        state.last_projection_run_id = run["projection_run_id"]

        # Persist a cashflow_chart artifact so the agent can embed
        # [[artifact:<id>]] inline and the frontend renders the curve.
        # Failure here must NOT block the projection response - log and
        # continue without an artifact_id rather than dropping the run
        # the user just asked for.
        artifact_id: str | None = None
        try:
            artifact_id = await _persist_cashflow_chart_artifact(
                run=run,
                user_id=user_id,
                session_id=session_id,
            )
        except Exception as e:
            logger.warning("Failed to persist cashflow_chart artifact: %s", e)

        response: dict[str, Any] = {
            "projection_run_id": run["projection_run_id"],
            "artifact_id": artifact_id,
            # Echo the resolved projection_start_month so Nova can
            # quote it verbatim instead of paraphrasing. Mismatch
            # between this and the user's intent is the bug the
            # date-anchor guard above catches.
            "projection_start_month": projection_start_month,
            "starting_cash": output.starting_cash,
            # Horizon disclosure. ``horizon_was_defaulted`` lets Nova
            # spell out that the 13-week default was used because the
            # user did not pass an explicit horizon. The note is the
            # exact sentence Nova is expected to quote when the
            # default was used; the chat layer copies it verbatim.
            "horizon_weeks": horizon_weeks,
            "horizon_was_defaulted": horizon_was_defaulted,
            "horizon_note": (
                "Projection horizon was not specified, so I used "
                "the default 13-week horizon."
                if horizon_was_defaulted
                else None
            ),
            "summary": {
                "starting_cash": output.starting_cash,
                "ending_cash": output.ending_cash,
                "minimum_cash": output.minimum_cash,
                "minimum_cash_week": output.minimum_cash_week,
                "total_revenue": output.total_revenue,
                "total_costs": output.total_costs,
                "net_change": output.net_change,
            },
            "weeks": [w.model_dump(mode="json") for w in output.weeks],
            "inputs_hash": output.inputs_hash,
        }
        # MRR baseline audit block from data_loader: basis, account
        # filter, prior-year vs fallback method per forecast month,
        # row counts, run-rate months used and average. Nova is
        # expected to quote this verbatim when explaining how MRR
        # was derived — the response carries the deterministic
        # numbers so Nova does not paraphrase the lookup outcome.
        if inputs.mrr_baseline_metadata is not None:
            response["mrr_baseline"] = inputs.mrr_baseline_metadata
        # Sales baseline audit block: method, basis, reference_month,
        # latest_tx_date, months_used, monthly_totals, and the
        # deterministic average_monthly_sales. Nova quotes this
        # verbatim when explaining the revenue baseline so it never
        # paraphrases or recomputes the average.
        if getattr(inputs, "sales_baseline_metadata", None) is not None:
            response["sales_baseline"] = inputs.sales_baseline_metadata
        # Dynamic projection metadata blocks. Each is None when the
        # caller did not opt in; present otherwise so Nova can quote
        # the exact prefixes / dates / row counts the engine used.
        if account_average_baselines is not None:
            response["account_average_baselines"] = account_average_baselines
        if ar_receipts_schedule is not None:
            response["ar_receipts_schedule"] = ar_receipts_schedule
        if ap_payments_schedule is not None:
            response["ap_payments_schedule"] = ap_payments_schedule
        if payroll_schedule_meta is not None:
            response["payroll_schedule"] = payroll_schedule_meta

        # ── Weekly table display metadata ──
        # Nova historically rendered only the first 4 weeks of a
        # 13-week projection. The display block tells the chat
        # surface to show all weeks (when small) or explicitly
        # disclose a preview (when large). The numbers come from
        # the engine output directly — no LLM math.
        weeks_returned = len(output.weeks)
        if weeks_returned <= 13:
            weekly_table_meta = {
                "weeks_returned": weeks_returned,
                "display_rule": "show_all",
                "previewed": False,
                "preview_note": None,
            }
        else:
            weekly_table_meta = {
                "weeks_returned": weeks_returned,
                "display_rule": "preview_with_disclosure",
                "previewed": True,
                "preview_note": (
                    f"Showing first 13 of {weeks_returned} weeks. "
                    "Export/download contains the full weekly table."
                ),
            }
        response["weekly_table"] = weekly_table_meta

        # ── Negative ending-cash explanation ──
        # Deterministic: if the engine's final week ends below zero,
        # surface a structured note carrying the four summary scalars
        # Nova is expected to quote verbatim. When ending cash is
        # non-negative, surface the block with the flag set to false
        # so Nova knows to skip the negative-cash phrasing.
        cash_position_note: dict[str, Any] = {
            "ending_cash_is_negative": output.ending_cash < 0,
            "starting_cash": output.starting_cash,
            "total_projected_revenue": output.total_revenue,
            "total_projected_costs": output.total_costs,
            "ending_cash": output.ending_cash,
            "message": (
                "Ending cash is negative because projected costs "
                "exceed starting cash plus projected revenue over "
                "the horizon."
                if output.ending_cash < 0
                else None
            ),
        }
        response["cash_position_note"] = cash_position_note

        return response

    elif tool_name == "apply_override":
        try:
            ov = Override(
                type=tool_args["type"],
                target=tool_args["target"],
                week_number=tool_args.get("week_number"),
                amount=tool_args.get("amount"),
                source_week=tool_args.get("source_week"),
                shift_weeks=tool_args.get("shift_weeks"),
            )
        except (KeyError, ValidationError, ValueError, TypeError) as e:
            return {"error": "invalid_override", "detail": str(e)}
        state.overrides.append(ov)
        return {"status": "override_added", "override_count": len(state.overrides)}

    elif tool_name == "add_manual_assumption":
        kind = tool_args.get("kind")
        if kind not in ("cost", "revenue"):
            return {
                "error": "invalid_kind",
                "detail": f"kind must be 'cost' or 'revenue', got {kind!r}",
                "valid": ["cost", "revenue"],
            }

        # Resolve recognition + cash timing deterministically before
        # constructing the assumption. The helper either returns
        # everything we need or returns requires_confirmation=True with
        # a confirmation_question — in which case the LLM must ask the
        # user rather than guess.
        from datetime import date as _date
        from services.nova_engine.scenario_timing import resolve_scenario_timing

        timing_phrase = tool_args.get("timing_phrase")
        explicit_starts_month = tool_args.get("starts_month")
        # Documented default per the v1 spec: when the caller does not
        # specify cash_receipt_timing, fall back to 'same_month' (the
        # behavior the engine had before this field existed) and let
        # the tool's timing_summary state it explicitly. The LLM is
        # instructed to pass cash_receipt_timing whenever the user's
        # phrasing implies it; this default keeps non-LLM callers
        # (older tests, scripts) on identical engine math.
        cash_receipt_timing_arg = tool_args.get("cash_receipt_timing") or "same_month"

        current_date_arg = tool_args.get("current_date")
        current_date_value: _date | None = None
        if isinstance(current_date_arg, str) and current_date_arg:
            try:
                current_date_value = _date.fromisoformat(current_date_arg)
            except ValueError:
                return {
                    "error": "invalid_current_date",
                    "detail": f"current_date must be YYYY-MM-DD, got {current_date_arg!r}",
                }

        try:
            timing = resolve_scenario_timing(
                phrase=timing_phrase if isinstance(timing_phrase, str) else None,
                current_date=current_date_value,
                projection_start_month=state.projection_start_month,
                explicit_starts_month=explicit_starts_month,
                cash_receipt_timing=cash_receipt_timing_arg,
                cash_receipt_date=None,
            )
        except ValueError as exc:
            return {"error": "invalid_timing", "detail": str(exc)}

        if timing.requires_confirmation:
            return {
                "error": "timing_requires_confirmation",
                "requires_confirmation": True,
                "confirmation_question": timing.confirmation_question,
                "timing_summary": timing.timing_summary,
                "warnings": list(timing.warnings),
            }

        # Engine does not yet support specific_date — refuse it cleanly
        # at the tool layer (the model would also reject without
        # cash_receipt_date, but be explicit so the LLM gets a clear
        # message).
        if timing.cash_receipt_timing == "specific_date":
            return {
                "error": "cash_receipt_specific_date_not_supported",
                "detail": (
                    "cash_receipt_timing='specific_date' is recognized by the "
                    "scenario timing helper but the projection engine does "
                    "not yet apply specific-date cash schedules. Use "
                    "'same_month' or 'next_month' for now."
                ),
                "timing_summary": timing.timing_summary,
                "warnings": list(timing.warnings),
            }

        common_kwargs: dict[str, Any] = {
            "name": tool_args.get("name"),
            "line_type": tool_args.get("line_type"),
            "starts_month": timing.recognition_start_month,
            "ends_month": tool_args.get("ends_month"),
            "weekly_dist": tool_args.get("weekly_dist"),
            "cash_receipt_timing": timing.cash_receipt_timing,
        }

        try:
            amount_per_month = float(tool_args["amount_per_month"])
        except (KeyError, ValueError, TypeError) as e:
            return {"error": "invalid_assumption", "detail": str(e)}

        try:
            if kind == "cost":
                state.manual_cost_assumptions.append(ManualCostAssumption(
                    amount_per_month=amount_per_month,
                    **common_kwargs,
                ))
            else:
                state.manual_recurring_revenue.append(RecurringRevenueAssumption(
                    amount_per_month=amount_per_month,
                    **common_kwargs,
                ))
        except (KeyError, ValidationError, ValueError, TypeError) as e:
            return {"error": "invalid_assumption", "detail": str(e)}

        return {
            "status": "assumption_added",
            "kind": kind,
            "timing_summary": timing.timing_summary,
            "recognition_start_month": timing.recognition_start_month,
            "cash_receipt_timing": timing.cash_receipt_timing,
            "cash_receipt_start_month": timing.cash_receipt_start_month,
            "warnings": list(timing.warnings),
        }

    elif tool_name == "explain_week":
        run = await get_run(tool_args["projection_run_id"], user_id)
        if run is None:
            return {"error": "run_not_found"}

        try:
            week_num = int(tool_args["week_number"])
        except (KeyError, ValueError, TypeError) as e:
            return {"error": "invalid_week_number", "detail": str(e)}

        # Older or partially-failed run docs may be missing 'output' or
        # 'inputs' entirely. Reach for them defensively and return a clean
        # tool-level error instead of letting a KeyError bubble up to a 500.
        output_doc = run.get("output") or {}
        inputs_doc = run.get("inputs") or {}
        if not output_doc:
            return {
                "error": "run_missing_output",
                "detail": (
                    "This projection run has no output payload to explain. "
                    "Re-run the projection and try again."
                ),
                "projection_run_id": run.get("projection_run_id"),
            }

        weeks = output_doc.get("weeks") or []
        if week_num < 1 or week_num > len(weeks):
            return {"error": "week_out_of_range", "valid_range": [1, len(weeks)]}

        week = weeks[week_num - 1]
        prev_ending_cash = (
            weeks[week_num - 2].get("ending_cash")
            if week_num > 1
            else inputs_doc.get("starting_cash", 0)
        )
        ending_cash = week.get("ending_cash")
        delta = (
            ending_cash - prev_ending_cash
            if ending_cash is not None and prev_ending_cash is not None
            else None
        )

        # Filter assumptions to those active in the requested week's
        # YYYY-MM. starts_month/ends_month are YYYY-MM strings, so
        # lexicographic comparison is correct for that format.
        week_starting = week.get("week_starting", "")
        week_month = week_starting[:7] if isinstance(week_starting, str) and len(week_starting) >= 7 else None

        def _assumption_active_in_month(assumption: dict, target_month: str | None) -> bool:
            if target_month is None:
                return True  # permissive fallback if month can't be derived
            starts = assumption.get("starts_month")
            ends = assumption.get("ends_month")
            if starts and target_month < starts:
                return False
            if ends and target_month > ends:
                return False
            return True

        all_assumptions = (
            (inputs_doc.get("manual_cost_assumptions") or [])
            + (inputs_doc.get("manual_recurring_revenue") or [])
        )
        active_assumptions = [
            a for a in all_assumptions
            if _assumption_active_in_month(a, week_month)
        ]

        return {
            "week_number": week_num,
            "week_starting": week.get("week_starting"),
            "ending_cash": ending_cash,
            "previous_ending_cash": prev_ending_cash,
            "delta": delta,
            "line_breakdown": {
                k: v for k, v in week.items()
                if isinstance(v, (int, float)) and k not in ("week_number", "ending_cash")
            },
            "active_overrides": [
                o for o in (inputs_doc.get("overrides") or [])
                if (
                    o.get("week_number") == week_num
                    or o.get("source_week") == week_num
                )
            ],
            "active_assumptions": active_assumptions,
        }

    elif tool_name == "reset_scenario_state":
        state.reset()
        return {"status": "scenario_reset"}

    elif tool_name == "compare_runs":
        # Read-only diff between two persisted projection runs. Both lookups
        # are scoped through persistence.get_run / list_runs which already
        # filter by user_id, so cross-user reads are impossible from here.
        base_id = tool_args.get("base_run_id")
        compare_id = tool_args.get("compare_run_id")
        include_weekly = tool_args.get("include_weekly_deltas", True)
        if isinstance(include_weekly, str):
            include_weekly = include_weekly.strip().lower() not in ("0", "false", "no")
        else:
            include_weekly = bool(include_weekly)

        try:
            latest_count = int(tool_args.get("latest_count", 2))
        except (TypeError, ValueError):
            latest_count = 2
        if latest_count < 2:
            latest_count = 2

        # Refuse a half-specified pair: silently auto-resolving the missing
        # side would mean "compare the latest two" produces a different
        # answer depending on whether the user typed one ID or zero.
        if (base_id and not compare_id) or (compare_id and not base_id):
            return {
                "error": "incomplete_run_pair",
                "detail": (
                    "Provide both base_run_id and compare_run_id to compare a "
                    "specific pair, or omit both to compare the latest two runs."
                ),
            }

        if base_id and compare_id:
            base_run = await get_run(base_id, user_id)
            compare_run = await get_run(compare_id, user_id)
            # Distinguish which side is missing so the agent can ask for
            # the right correction. The error code stays uniform from the
            # caller's perspective: a non-existent or non-owned id both
            # surface as <slot>_run_not_found - we never reveal whether
            # another user owns that id.
            if base_run is None:
                return {
                    "error": "base_run_not_found",
                    "projection_run_id": base_id,
                }
            if compare_run is None:
                return {
                    "error": "compare_run_not_found",
                    "projection_run_id": compare_id,
                }
        else:
            recent = await list_runs(user_id, limit=latest_count)
            if len(recent) < 2:
                return {
                    "error": "insufficient_runs",
                    "detail": (
                        "I need at least two saved projection runs to compare. "
                        "Run another projection first, then ask me to compare."
                    ),
                    "available_runs": len(recent),
                }
            # list_runs returns newest-first. With latest_count=2 (default)
            # this becomes "newest vs second-newest" - the user's most natural
            # 'what changed between my last two runs' framing. If they bumped
            # latest_count to compare further back, runs[-1] is the oldest in
            # the window so the diff is "now vs N runs ago".
            compare_run = recent[0]
            base_run = recent[-1]

        return _build_compare_runs_payload(
            base_run=base_run,
            compare_run=compare_run,
            include_weekly_deltas=include_weekly,
        )

    elif tool_name == "query_finance_data":
        # Aggregations against qbo_db.general_ledger. Read-only.
        #
        # Honors AEGISAXIOM GL schema (only `amount` field) and sign convention
        # (revenue stored as negative `amount`; flip in Python after
        # categorize()). Mirrors data_loader.py:load_revenue_history.
        # See nova PR thread re: rejecting the credit/debit assumption
        # for an honest amount-only pipeline.
        from collections import defaultdict
        from datetime import date, timedelta

        from services.nova_engine.account_categories import categorize
        from services.nova_engine.db import get_qbo_db

        metric = tool_args["metric"]
        group_by = tool_args["group_by"]
        category_filter = tool_args.get("category")
        account_prefix = tool_args.get("account_prefix")
        date_from = tool_args.get("date_from")
        date_to = tool_args.get("date_to")
        try:
            limit = min(int(tool_args.get("limit", 50)), 200)
        except (ValueError, TypeError) as e:
            return {"error": "invalid_limit", "detail": str(e)}

        valid_group_by = {
            "month", "quarter", "year", "account_category", "account_name",
        }
        # Reject anything outside the registry up front. Existing tests pass
        # 'DSO' here and expect 'unknown_metric'; the metric registry now
        # backs that decision rather than a hard-coded local set.
        if metric not in _VALID_METRICS:
            return {
                "error": "unknown_metric",
                "detail": (
                    f"metric={metric!r} is not supported. Use one of the "
                    "metrics in the schema enum, or ask the user for a "
                    "different question."
                ),
                "valid": sorted(_VALID_METRICS),
            }
        if group_by not in valid_group_by:
            return {"error": "unknown_group_by", "valid": sorted(valid_group_by)}

        # Resolve plain-English aliases to canonical metric names so the
        # downstream branches stay deduplicated.
        metric = _METRIC_ALIASES.get(metric, metric)

        # Metrics that exist in the enum but AEGISAXIOM has no clean source for
        # yet return a structured ``unavailable_data`` error pointing the
        # LLM at the dashboard. Critical not to fall through to the GL
        # pipeline here - that would silently match zero rows and the
        # LLM would narrate "no rent expenses" over a real ledger.
        if metric in _UNAVAILABLE_METRICS:
            payload = _UNAVAILABLE_METRICS[metric]
            return {
                "error": "unavailable_data",
                "metric": metric,
                "detail": payload["detail"],
                "hint": payload["hint"],
            }

        # Snapshot-served metrics short-circuit before the GL pipeline.
        # The hourly job (services/nova_engine/finance_snapshots.refresh_finance_snapshots)
        # writes one row per tenant per refresh; we read the latest. When
        # no snapshot has run yet (fresh deploy, scheduler disabled) the
        # caller gets a clear unavailable_data so Nova doesn't fabricate a
        # zero. Tenant scope is "platform" today - see nova.py
        # _resolve_tenant_context for the eventual multi-tenant seam.
        if metric in _SNAPSHOT_METRICS:
            from services.nova_engine.finance_snapshots import get_latest_snapshot

            snapshot = await get_latest_snapshot(tenant_id="platform")
            if snapshot is None:
                return {
                    "error": "unavailable_data",
                    "metric": metric,
                    "detail": (
                        "No finance snapshot has been generated yet. The "
                        "AR/AP refresh job runs hourly and on server "
                        "startup; if you just deployed, give it a moment."
                    ),
                    "hint": (
                        "Direct the user to the AR dashboard at "
                        "/api/finance/accounts-receivable or the AP "
                        "dashboard at /api/finance/accounts-payable for "
                        "the live numbers in the meantime."
                    ),
                }

            metadata = snapshot.get("source_metadata", {}) or {}
            data_available_key = (
                "ar_data_available" if metric.startswith("ar")
                else "ap_data_available"
            )
            if not metadata.get(data_available_key, True):
                source_label = "ar_invoices" if metric.startswith("ar") else "ap_bills"
                dashboard = (
                    "/api/finance/accounts-receivable"
                    if metric.startswith("ar")
                    else "/api/finance/accounts-payable"
                )
                return {
                    "error": "unavailable_data",
                    "metric": metric,
                    "detail": (
                        f"The {source_label} collection is empty so the "
                        "snapshot has nothing to roll up. This typically "
                        "means QBO hasn't synced or invoices/bills have "
                        "not been created yet."
                    ),
                    "hint": (
                        f"Direct the user to the {dashboard} dashboard "
                        "to confirm the source data exists, or wait for "
                        "the next QBO/AR sync."
                    ),
                    "snapshot_date": snapshot.get("snapshot_date"),
                }

            if metric == "ar_total":
                value: Any = snapshot.get("ar_total", 0.0)
            elif metric == "ap_total":
                value = snapshot.get("ap_total", 0.0)
            elif metric == "ar_aging":
                value = snapshot.get("ar_aging", {})
            else:  # ap_aging
                value = snapshot.get("ap_aging", {})

            return {
                "metric": metric,
                "group_by": "snapshot",
                "value": value,
                "snapshot_date": snapshot.get("snapshot_date"),
                "tenant_id": snapshot.get("tenant_id"),
                "source": snapshot.get("source"),
                "source_metadata": metadata,
            }
        if category_filter is not None and category_filter not in _CATEGORY_VALUES:
            # Pre-PR fix: an LLM-supplied category outside the canonical
            # list used to silently match no rows via categorize() and
            # return an empty result. Reject loudly so the LLM can correct
            # itself instead of narrating no-data over a real ledger.
            return {
                "error": "invalid_category",
                "detail": (
                    f"category={category_filter!r} is not a known account "
                    "category. Use one of the canonical categories from "
                    "account_categories.py."
                ),
                "valid": list(_CATEGORY_VALUES),
            }

        if date_from is not None and not _is_valid_date(date_from):
            return {
                "error": "invalid_date_from",
                "detail": f"date_from must be YYYY-MM-DD, got {date_from!r}",
            }
        if date_to is not None and not _is_valid_date(date_to):
            return {
                "error": "invalid_date_to",
                "detail": f"date_to must be YYYY-MM-DD, got {date_to!r}",
            }

        basis = tool_args.get("basis", "accrual")
        if basis not in ("accrual", "cash"):
            return {
                "error": "invalid_basis",
                "detail": f"basis must be 'accrual' or 'cash', got {basis!r}",
            }

        today = date.today()
        if date_to is None:
            date_to = today.isoformat()
        if date_from is None:
            date_from = (today - timedelta(days=365)).isoformat()

        # Current-month metrics override the caller's date window and force
        # group_by=month so the result is a single scalar for the current
        # calendar month. Honoring an arbitrary date range here would make
        # the metric name lie ("current_month_revenue" answering for last
        # year). Tracked in the response so the LLM can quote the actual
        # window we used.
        forced_filters: dict[str, Any] = {}
        if metric in _CURRENT_MONTH_METRICS:
            month_start = today.replace(day=1).isoformat()
            forced_filters = {
                "date_from": month_start,
                "date_to": today.isoformat(),
                "group_by": "month",
            }
            date_from = forced_filters["date_from"]
            date_to = forced_filters["date_to"]
            group_by = forced_filters["group_by"]

        qbo_db = get_qbo_db()

        if group_by == "month":
            bucket_expr: Any = {"$substr": ["$tx_date", 0, 7]}
        elif group_by == "quarter":
            bucket_expr = {
                "$concat": [
                    {"$substr": ["$tx_date", 0, 4]},
                    "-Q",
                    {"$toString": {
                        "$ceil": {"$divide": [
                            {"$toInt": {"$substr": ["$tx_date", 5, 2]}}, 3,
                        ]}
                    }},
                ]
            }
        elif group_by == "year":
            bucket_expr = {"$substr": ["$tx_date", 0, 4]}
        else:  # account_category / account_name
            bucket_expr = "$account_name"

        match_stage: dict[str, Any] = {
            "tx_date": {"$gte": date_from, "$lte": date_to},
            # P0: every other GL query in this codebase filters by basis
            # (data_loader.py:_build_history_pipeline). Without it, GL rows
            # that exist on both cash and accrual bases double-count.
            "basis": basis,
        }
        if account_prefix:
            # re.escape prevents regex injection from LLM-generated input.
            match_stage["account_name"] = {"$regex": f"^{re.escape(account_prefix)}"}

        # ---- Optional location / location_blank filters --------------
        # location_blank=true narrows the scope to rows whose every
        # location-like field is missing/null/empty. location="..." does
        # the inverse: substring match against the human-readable
        # location/department fields, exact match against the id fields.
        # Mutually exclusive — both set returns a structured error so the
        # LLM sees the contradiction instead of one filter silently
        # winning.
        location_arg = tool_args.get("location")
        location_blank_arg = bool(tool_args.get("location_blank"))
        location_filter_applied: dict[str, Any] | None = None
        if location_arg is not None and location_blank_arg:
            return {
                "error": "conflicting_location_filters",
                "detail": (
                    "location and location_blank are mutually exclusive. "
                    "Pass one or the other."
                ),
            }
        if location_blank_arg:
            # Row is "blank-location" iff every flat field is null/missing
            # OR equals "" — Mongo cannot easily detect whitespace-only
            # strings server-side, so the response carries a coverage
            # warning that says so.
            and_clauses = [
                {"$or": [{f: {"$exists": False}}, {f: None}, {f: ""}]}
                for f in _LOCATION_LIKE_FIELDS
            ]
            match_stage["$and"] = match_stage.get("$and", []) + and_clauses
            location_filter_applied = {"location_blank": True}
        elif isinstance(location_arg, str) and location_arg.strip():
            needle = location_arg.strip()
            escaped = re.escape(needle)
            substring_fields = (
                "location",
                "location_name",
                "department",
                "department_name",
                "department_ref_name",
            )
            id_fields = ("location_id", "department_ref_id")
            or_clauses: list[dict[str, Any]] = []
            for f in substring_fields:
                or_clauses.append({f: {"$regex": escaped, "$options": "i"}})
            for f in id_fields:
                or_clauses.append({f: needle})
            match_stage["$or"] = or_clauses
            location_filter_applied = {"location": needle}

        pipeline = [
            {"$match": match_stage},
            {"$project": {
                "_id": 0,
                "bucket": bucket_expr,
                "account_name": "$account_name",
                "amount": {"$ifNull": ["$amount", 0]},
            }},
            {"$group": {
                "_id": {"bucket": "$bucket", "account_name": "$account_name"},
                "amount": {"$sum": "$amount"},
                "count": {"$sum": 1},
            }},
        ]

        raw_rows = await qbo_db.general_ledger.aggregate(pipeline).to_list(
            length=_GL_AGGREGATION_LIMIT
        )
        if len(raw_rows) >= _GL_AGGREGATION_LIMIT:
            logger.warning(
                "query_finance_data aggregation hit row cap of %d. Result may be "
                "truncated. If this is expected for the date range / group_by "
                "combination, raise _GL_AGGREGATION_LIMIT in nova_tools.py.",
                _GL_AGGREGATION_LIMIT,
            )

        REVENUE_CATS = {
            "PS_REVENUE", "FS_REVENUE", "MRR",
            "WHOLESALE_REVENUE", "OTHER_REVENUE",
        }
        COGS_CATS = {"PS_COGS", "FS_COGS", "OTHER_COGS"}
        EXPENSE_CATS = COGS_CATS | {"PAYROLL", "ADMIN_EXPENSES", "OTHER_OPEX"}

        # Per-bucket accumulator: revenue (sign-flipped, positive),
        # PS / FS / OTHER COGS, total expense, payroll, admin_expenses,
        # count. Splitting payroll / admin_expenses out of the generic
        # 'expense' bucket lets the per-category metrics return values
        # without an extra pass over raw_rows. OTHER_COGS is tracked
        # separately so the canonical company-level GP / GM formula
        # (formulas.compute_gross_profit) sees it — see audit doc:
        # excluding OTHER_COGS from gross profit while including
        # OTHER_REVENUE-eligible revenue overstated the margin.
        per_bucket: dict[str, dict[str, float]] = defaultdict(
            lambda: {
                "revenue": 0.0,
                "ps_cogs": 0.0, "fs_cogs": 0.0, "other_cogs": 0.0,
                "expense": 0.0, "payroll": 0.0, "admin_expenses": 0.0,
                "count": 0,
            }
        )

        for row in raw_rows:
            bucket_raw = row["_id"]["bucket"]
            acct = row["_id"]["account_name"] or ""
            cat = categorize(acct) if acct else "UNCATEGORIZED"
            amt = float(row.get("amount", 0))
            cnt = int(row.get("count", 0))

            if category_filter and cat != category_filter:
                continue

            if group_by == "account_category":
                key = cat
            elif group_by == "account_name":
                key = acct
            else:  # month / quarter / year - bucket already pre-computed
                key = bucket_raw

            if cat in REVENUE_CATS:
                # GL `amount` is natural-balance signed (positive in the
                # normal direction of the account: credit on revenue,
                # debit on expense, negative for reversals). On revenue
                # accounts the natural direction IS positive, so summing
                # `amount` gives a positive total — no sign flip required.
                # See data_loader.load_revenue_history for the canonical
                # treatment.
                per_bucket[key]["revenue"] += amt
            if cat == "PS_COGS":
                per_bucket[key]["ps_cogs"] += amt
            if cat == "FS_COGS":
                per_bucket[key]["fs_cogs"] += amt
            if cat == "OTHER_COGS":
                per_bucket[key]["other_cogs"] += amt
            if cat in EXPENSE_CATS:
                per_bucket[key]["expense"] += amt
            if cat == "PAYROLL":
                per_bucket[key]["payroll"] += amt
            if cat == "ADMIN_EXPENSES":
                per_bucket[key]["admin_expenses"] += amt

            per_bucket[key]["count"] += cnt

        rows_out: list[dict[str, Any]] = []
        for key, agg in per_bucket.items():
            rev = agg["revenue"]
            if metric == "revenue" or metric == "current_month_revenue":
                value = rev
            elif metric == "expense" or metric == "current_month_expenses":
                value = agg["expense"]
            elif metric == "current_month_net_change":
                # Net cash impact of GL activity for the current month.
                # Note this is GL-only; AR/AP timing differences are out
                # of scope (those are tracked separately and surface via
                # ar_total / ap_total which are unavailable_data today).
                value = rev - agg["expense"]
            elif metric == "gross_profit":
                # Company-level gross profit must subtract Total COGS
                # across every canonical COGS category (PS + FS +
                # OTHER), matching the validated QBO P&L baseline in
                # routes/finance_kpis.py. Excluding OTHER_COGS while
                # including OTHER_REVENUE in `rev` overstated GP — see
                # services/nova_engine/formulas.py for the canonical
                # category enumeration this code mirrors.
                value = rev - agg["ps_cogs"] - agg["fs_cogs"] - agg["other_cogs"]
            elif metric == "gross_profit_margin":
                total_cogs = agg["ps_cogs"] + agg["fs_cogs"] + agg["other_cogs"]
                value = (
                    (rev - total_cogs) / rev
                    if rev > 0 else 0.0
                )
            elif metric == "expense_ratio":
                value = agg["expense"] / rev if rev > 0 else 0.0
            elif metric == "payroll":
                value = agg["payroll"]
            elif metric == "admin_expenses":
                value = agg["admin_expenses"]
            else:  # transaction_count
                value = float(agg["count"])
            rows_out.append({
                "bucket": key,
                "amount": value,
                "transaction_count": agg["count"],
            })

        rows_out.sort(key=lambda r: r["bucket"])
        rows_out = rows_out[:limit]

        response = {
            "metric": metric,
            "group_by": group_by,
            "filters": {
                "basis": basis,
                "category": category_filter,
                "account_prefix": account_prefix,
                "date_from": date_from,
                "date_to": date_to,
            },
            "rows": rows_out,
            "row_count": len(rows_out),
        }
        if metric in ("gross_profit", "gross_profit_margin"):
            # Disclose category coverage so the LLM can describe the
            # formula honestly. These come from the canonical
            # enumeration in services/nova_engine/formulas.py and
            # match the validated QBO P&L baseline in
            # routes/finance_kpis.py.
            response["formula_source"] = GROSS_PROFIT_FORMULA_SOURCE_GL
            response["formula_version"] = GROSS_PROFIT_FORMULA_VERSION
            response["included_revenue_categories"] = sorted(REVENUE_CATEGORIES)
            response["included_cogs_categories"] = sorted(COGS_CATEGORIES)
            response["formula_notes"] = (
                "Company-level gross profit = Total Revenue (PS + FS + "
                "MRR + WHOLESALE + OTHER revenue) - Total COGS (PS + FS "
                "+ OTHER COGS). Mirrors the validated QBO P&L baseline."
            )
        if forced_filters:
            # Surface that we ignored caller-supplied date_from/date_to/group_by
            # so the LLM doesn't quote a window that wasn't actually used.
            response["forced_filters"] = forced_filters
        if location_filter_applied is not None:
            # Disclose that the result is location-scoped so the LLM
            # can decide whether to call get_gl_location_coverage for
            # the same scope before quoting numbers as if they were
            # company-wide.
            response["location_filter"] = location_filter_applied
            response["location_coverage_warning"] = (
                "Result is filtered by location. Rows whose "
                "location/department fields are missing or blank are "
                "excluded — call get_gl_location_coverage with the "
                "same date_from / date_to / basis to see how many GL "
                "rows in scope cannot be attributed to any location."
            )
        return response

    elif tool_name == "detect_wholesale_revenue":
        # Deterministic wholesale-revenue detection from AEGISAXIOM structured
        # signals (GL 50400 prefix, contract_type, line_of_business,
        # item_category). No LLM classification - if rules don't fire,
        # we report UNKNOWN rather than guessing.
        from datetime import date as _date
        from services.nova_engine.data_freshness import last_n_complete_months
        from services.nova_engine.db import get_qbo_db
        from services.nova_engine.wholesale_detection import (
            MIN_MONTHS_FOR_AUTO_FILL,
            compute_auto_wholesale_assumption,
            detect_wholesale_history_from_gl,
        )

        basis = tool_args.get("basis", "accrual")
        if basis not in ("accrual", "cash"):
            return {
                "error": "invalid_basis",
                "detail": f"basis must be 'accrual' or 'cash', got {basis!r}",
            }

        history_months = tool_args.get("history_months")
        if history_months is not None:
            if not isinstance(history_months, list) or not all(
                _is_valid_projection_month(m) for m in history_months
            ):
                return {
                    "error": "invalid_history_months",
                    "detail": (
                        "history_months must be a list of YYYY-MM strings."
                    ),
                }
        else:
            today = _date.today()
            reference_month = f"{today.year:04d}-{today.month:02d}"
            try:
                from services.nova_engine.data_loader import (
                    get_latest_tx_date,
                )
                latest = await get_latest_tx_date(get_qbo_db())
                history_months = last_n_complete_months(
                    reference_month=reference_month,
                    latest_tx_date=latest,
                    n=3,
                )
            except ValueError:
                # general_ledger empty - no history to inspect.
                return {
                    "error": "unavailable_data",
                    "detail": (
                        "general_ledger is empty; no wholesale history can "
                        "be derived. Confirm QBO sync has run."
                    ),
                    "hint": (
                        "Direct the user to the QBO sync status panel or "
                        "wait for the next scheduled sync."
                    ),
                }

        qbo_db = get_qbo_db()
        monthly = await detect_wholesale_history_from_gl(
            qbo_db, list(history_months), basis,
        )
        months_with_signal = sum(1 for v in monthly.values() if v > 0)
        total = sum(monthly.values())
        avg = total / len(history_months) if history_months else 0.0
        confident = months_with_signal >= MIN_MONTHS_FOR_AUTO_FILL and avg > 0

        if total <= 0:
            classification = "UNKNOWN"
        elif confident:
            classification = "WHOLESALE_DETECTED"
        else:
            # Sparse signal - one month of activity is not enough to
            # auto-fill, but we still surface it so Nova can offer the
            # caller a manual-add prompt.
            classification = "AMBIGUOUS"

        auto_assumption = compute_auto_wholesale_assumption(
            monthly, list(history_months),
        )

        response = {
            "classification": classification,
            "history_months": list(history_months),
            "monthly_wholesale_revenue": monthly,
            "total_wholesale_revenue": round(total, 2),
            "average_monthly_wholesale": round(avg, 2),
            "months_with_signal": months_with_signal,
            "min_months_for_auto_fill": MIN_MONTHS_FOR_AUTO_FILL,
            "basis": basis,
            "signals_used": ["gl_account_50400"],
            "auto_assumption": (
                auto_assumption.model_dump(mode="json")
                if auto_assumption is not None else None
            ),
        }
        if classification == "AMBIGUOUS":
            response["hint"] = (
                f"Wholesale activity appeared in {months_with_signal} of "
                f"{len(history_months)} history months - not consistent "
                "enough to seed a recurring projection assumption. Add "
                "manual_recurring_revenue with line_type='WHOLESALE' if "
                "you want it included."
            )
        elif classification == "UNKNOWN":
            response["hint"] = (
                "No GL rows under the 50400 wholesale prefix were found "
                "in the history window. If wholesale revenue is expected, "
                "confirm the QBO chart of accounts uses the 50400 prefix "
                "or supply manual wholesale assumptions."
            )
        return response

    elif tool_name == "analyze_fs_job_margin":
        # Field-service job margin. Single-job mode when job_id is
        # supplied, ranked-list mode otherwise. Cost/revenue join keys
        # are documented in services/nova_engine/fs_job_margin.py.
        from services.nova_engine.fs_job_margin import (
            compute_fs_job_margin,
            list_fs_job_margins,
        )

        job_id = tool_args.get("job_id")
        if job_id is not None:
            if not isinstance(job_id, str) or not job_id.strip():
                return {
                    "error": "invalid_job_id",
                    "detail": "job_id must be a non-empty string.",
                }
            return await compute_fs_job_margin(job_id=job_id)

        sort = tool_args.get("sort", "loss_first")
        if sort not in ("loss_first", "margin_asc", "margin_desc", "revenue_desc"):
            return {
                "error": "invalid_sort",
                "detail": (
                    f"sort must be one of loss_first / margin_asc / "
                    f"margin_desc / revenue_desc, got {sort!r}"
                ),
            }

        try:
            limit = int(tool_args.get("limit", 10))
        except (TypeError, ValueError) as e:
            return {"error": "invalid_limit", "detail": str(e)}
        limit = max(1, min(limit, 50))

        min_margin_percent = tool_args.get("min_margin_percent")
        if min_margin_percent is not None:
            try:
                min_margin_percent = float(min_margin_percent)
            except (TypeError, ValueError):
                return {
                    "error": "invalid_min_margin_percent",
                    "detail": "min_margin_percent must be a number.",
                }

        status = tool_args.get("status")
        if status is not None and not isinstance(status, str):
            return {
                "error": "invalid_status",
                "detail": "status must be a string.",
            }
        # Validate against the FieldJob status enum so a typo or
        # LLM-fabricated value returns a structured error instead of
        # silently matching zero rows.
        if status is not None and status not in _FS_JOB_STATUSES:
            return {
                "error": "unknown_status",
                "detail": (
                    f"status={status!r} is not a known FieldJob status."
                ),
                "valid": sorted(_FS_JOB_STATUSES),
            }

        return await list_fs_job_margins(
            status=status,
            sort=sort,
            limit=limit,
            min_margin_percent=min_margin_percent,
        )

    elif tool_name == "analyze_customer_deal_finance":
        # Customer / deal AR-revenue and AP-cost ranking. Reads
        # ar_invoices, time_entries, and contracts from the AEGISAXIOM app DB
        # via reliable join keys (account_id, deal_id, contract_id) - no
        # fuzzy name matching. Wrapped in a try so a malformed contract
        # or time entry can't 500 the chat turn.
        from services.nova_engine.customer_deal_finance import (
            aggregate_customer_deal_finance,
        )

        dimension = tool_args.get("dimension")
        if dimension not in ("customer", "deal"):
            return {
                "error": "invalid_dimension",
                "detail": (
                    f"dimension must be 'customer' or 'deal', got {dimension!r}"
                ),
                "valid": ["customer", "deal"],
            }

        metric_arg = tool_args.get("metric")
        if metric_arg not in ("revenue", "cost", "margin"):
            return {
                "error": "invalid_metric",
                "detail": (
                    f"metric must be 'revenue', 'cost', or 'margin', got "
                    f"{metric_arg!r}"
                ),
                "valid": ["revenue", "cost", "margin"],
            }

        date_from = tool_args.get("date_from")
        if date_from is not None and not _is_valid_date(date_from):
            return {
                "error": "invalid_date_from",
                "detail": f"date_from must be YYYY-MM-DD, got {date_from!r}",
            }
        date_to = tool_args.get("date_to")
        if date_to is not None and not _is_valid_date(date_to):
            return {
                "error": "invalid_date_to",
                "detail": f"date_to must be YYYY-MM-DD, got {date_to!r}",
            }

        try:
            limit = int(tool_args.get("limit", 10))
        except (TypeError, ValueError) as e:
            return {"error": "invalid_limit", "detail": str(e)}
        limit = max(1, min(limit, 50))

        return await aggregate_customer_deal_finance(
            dimension=dimension,
            metric=metric_arg,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    # -----------------------------------------------------------------------
    # FCC analytical tools — query QBO report collections directly.
    # Thread 2B may expose _compute_* helpers once merged; for v1 we read
    # the same QBO collections directly to avoid a cross-thread import
    # dependency. Both paths transform identical source docs so results
    # are equivalent.
    # -----------------------------------------------------------------------

    elif tool_name == "get_pnl_summary":
        from services.nova_engine.db import get_qbo_db

        period = tool_args.get("period", "current")
        if period not in ("current", "prior"):
            return {
                "error": "invalid_period",
                "detail": f"period must be 'current' or 'prior', got {period!r}",
                "valid": ["current", "prior"],
            }
        qbo_db = get_qbo_db()
        collection = "reports_pnl" if period == "current" else "reports_pnl_prior_year"
        docs = await qbo_db[collection].find(
            {}, {"_id": 0}
        ).sort("period", -1).limit(1).to_list(1)
        if not docs:
            return {
                "error": "unavailable_data",
                "detail": (
                    f"No QBO P&L report found in {collection}. "
                    "Confirm QBO sync has run and the report is enabled."
                ),
                "hint": "Direct the user to the Finance portal P&L tab.",
            }
        return {"period": period, "report": docs[0]}

    elif tool_name == "get_balance_sheet_summary":
        from services.nova_engine.db import get_qbo_db

        qbo_db = get_qbo_db()
        docs = await qbo_db.reports_balance_sheet.find(
            {}, {"_id": 0}
        ).sort("_id", -1).limit(1).to_list(1)
        if not docs:
            return {
                "error": "unavailable_data",
                "detail": (
                    "No QBO Balance Sheet report found in reports_balance_sheet. "
                    "Confirm QBO sync has run and the report is enabled."
                ),
                "hint": "Direct the user to the Finance portal Balance Sheet tab.",
            }
        return {"report": docs[0]}

    elif tool_name == "get_cash_flow_summary":
        from services.nova_engine.db import get_qbo_db

        qbo_db = get_qbo_db()
        docs = await qbo_db.reports_cash_flow.find(
            {}, {"_id": 0}
        ).sort("_id", -1).limit(1).to_list(1)
        if not docs:
            return {
                "error": "unavailable_data",
                "detail": (
                    "No QBO Cash Flow report found in reports_cash_flow. "
                    "Confirm QBO sync has run and the report is enabled."
                ),
                "hint": "Direct the user to the Finance portal Cash Flow tab.",
            }
        return {"report": docs[0]}

    elif tool_name == "get_trial_balance_summary":
        from services.nova_engine.db import get_qbo_db

        qbo_db = get_qbo_db()
        docs = await qbo_db.reports_trial_balance.find(
            {}, {"_id": 0}
        ).sort("_id", -1).limit(1).to_list(1)
        if not docs:
            return {
                "error": "unavailable_data",
                "detail": (
                    "No QBO Trial Balance report found in reports_trial_balance. "
                    "Confirm QBO sync has run and the report is enabled."
                ),
                "hint": "Direct the user to the Finance portal Trial Balance tab.",
            }
        return {"report": docs[0]}

    elif tool_name == "get_ar_aging":
        from services.nova_engine.db import get_qbo_db

        qbo_db = get_qbo_db()
        # Prefer the detail report (per-customer bucket breakdown) when
        # available; fall back to the summary aging report.
        docs = await qbo_db.reports_ar_aging_detail.find(
            {}, {"_id": 0}
        ).sort("_id", -1).limit(1).to_list(1)
        if not docs:
            docs = await qbo_db.reports_ar_aging.find(
                {}, {"_id": 0}
            ).sort("_id", -1).limit(1).to_list(1)
        if not docs:
            return {
                "error": "unavailable_data",
                "detail": (
                    "No QBO AR Aging report found. "
                    "Confirm QBO sync has run and the report is enabled."
                ),
                "hint": (
                    "Direct the user to the Finance portal AR Aging tab "
                    "or confirm /api/finance/working-capital/ar-aging has data."
                ),
            }
        return {"report": docs[0]}

    elif tool_name == "get_ap_aging":
        from services.nova_engine.db import get_qbo_db

        qbo_db = get_qbo_db()
        docs = await qbo_db.reports_ap_aging_detail.find(
            {}, {"_id": 0}
        ).sort("_id", -1).limit(1).to_list(1)
        if not docs:
            docs = await qbo_db.reports_ap_aging.find(
                {}, {"_id": 0}
            ).sort("_id", -1).limit(1).to_list(1)
        if not docs:
            return {
                "error": "unavailable_data",
                "detail": (
                    "No QBO AP Aging report found. "
                    "Confirm QBO sync has run and the report is enabled."
                ),
                "hint": (
                    "Direct the user to the Finance portal AP Aging tab "
                    "or confirm /api/finance/working-capital/ap-aging has data."
                ),
            }
        return {"report": docs[0]}

    elif tool_name == "get_dso_dpo_trend":
        from datetime import date as _date
        from services.nova_engine.db import get_qbo_db

        try:
            months = min(int(tool_args.get("months", 12)), 24)
        except (TypeError, ValueError):
            months = 12
        if months < 1:
            months = 1

        qbo_db = get_qbo_db()
        # DSO/DPO are derived from AR and AP aging detail collections.
        # Pull the most recent N months of both aging details and compute
        # DSO = (avg AR balance / monthly revenue) * 30,
        # DPO = (avg AP balance / monthly spend) * 30.
        # For v1, return the raw aging snapshots ordered by period so the
        # LLM can narrate the trend without doing the division.
        ar_docs = await qbo_db.reports_ar_aging_detail.find(
            {}, {"_id": 0}
        ).sort("_id", -1).limit(months).to_list(months)
        ap_docs = await qbo_db.reports_ap_aging_detail.find(
            {}, {"_id": 0}
        ).sort("_id", -1).limit(months).to_list(months)

        if not ar_docs and not ap_docs:
            return {
                "error": "unavailable_data",
                "detail": (
                    "No QBO AR/AP aging detail found for DSO/DPO calculation. "
                    "Confirm QBO sync has run."
                ),
                "hint": "Direct the user to /api/finance/working-capital/dso-dpo.",
            }
        return {
            "months_requested": months,
            "ar_aging_snapshots": ar_docs,
            "ap_aging_snapshots": ap_docs,
            "note": (
                "DSO ≈ (AR balance / monthly revenue) × 30. "
                "DPO ≈ (AP balance / monthly spend) × 30. "
                "Narrate the trend direction from the snapshots above — "
                "do NOT compute the exact ratio from the raw figures."
            ),
        }

    elif tool_name == "get_payables_timing":
        # Answer the average payables time family of questions with a
        # single number rather than a refusal. The handler fans data
        # gathering across open AP and the recent expense window and
        # delegates the math to compute_payables_timing. The helper
        # picks the best available metric. The earlier closest tool
        # dumped raw aging snapshots and asked the LLM to do the
        # arithmetic, which violated the tools compute Nova explains
        # rule and produced the refusal text the spec calls out.
        from datetime import date as _date, datetime as _datetime, timezone as _timezone

        from services.nova_engine.db import get_qbo_db
        from services.nova_engine.payables_timing import (
            METRIC_DPO_PROXY,
            METRIC_OPEN_AP_TIMING,
            METRIC_PAID_BILL_CYCLE,
            compute_payables_timing,
        )

        as_of_arg = (tool_args.get("as_of_date") or "").strip()
        if as_of_arg and not _is_valid_date(as_of_arg):
            return {
                "error": "invalid_date_format",
                "detail": f"as_of_date must be YYYY-MM-DD, got {as_of_arg!r}",
            }
        as_of_value = as_of_arg or _datetime.now(_timezone.utc).date().isoformat()

        try:
            period_days = int(tool_args.get("period_days", 90))
        except (TypeError, ValueError):
            period_days = 90
        if period_days < 1:
            period_days = 90

        basis = (tool_args.get("basis") or "accrual").strip() or "accrual"

        qbo_db = get_qbo_db()

        # Pull every open AP bill row. The helper handles missing dates
        # and non positive balances. The cap keeps a runaway dataset
        # from blowing up the request.
        open_cursor = qbo_db.ap_bills.find(
            {"balance": {"$gt": 0}}, {"_id": 0},
        ).limit(2000)
        raw_open_rows = await open_cursor.to_list(length=2000)
        open_ap_rows: list[dict[str, Any]] = []
        for d in raw_open_rows:
            raw = d.get("raw_data") or {}
            vendor_ref = raw.get("VendorRef") or {}
            vendor = vendor_ref.get("name") if isinstance(vendor_ref, dict) else None
            open_ap_rows.append(
                {
                    "balance": d.get("balance") or raw.get("Balance"),
                    "txn_date": d.get("txn_date") or raw.get("TxnDate"),
                    "due_date": d.get("due_date") or raw.get("DueDate"),
                    "vendor": vendor or vendor_ref.get("value"),
                }
            )

        # DPO proxy inputs are best effort. Two AP aging snapshots give
        # beginning and ending AP. The GL expense window gives the
        # purchases stand in. Any missing input simply skips the proxy
        # branch and the helper falls through to the open AP fallback.
        beginning_ap: float | None = None
        ending_ap: float | None = None
        period_purchases: float | None = None
        purchases_source: str | None = None
        try:
            aging_docs = await qbo_db.reports_ap_aging.find(
                {}, {"_id": 0},
            ).sort("_id", -1).limit(2).to_list(2)
            if aging_docs:
                ending_doc = aging_docs[0]
                ending_ap = float(
                    ending_doc.get("grand_total")
                    or ending_doc.get("total")
                    or 0.0
                )
            if len(aging_docs) > 1:
                beginning_doc = aging_docs[1]
                beginning_ap = float(
                    beginning_doc.get("grand_total")
                    or beginning_doc.get("total")
                    or 0.0
                )
        except Exception:  # noqa: BLE001 — proxy branch is best effort
            beginning_ap = None
            ending_ap = None

        try:
            as_of_date_obj = _date.fromisoformat(as_of_value)
            window_start = _date.fromordinal(as_of_date_obj.toordinal() - period_days)
            expense_pipeline = [
                {
                    "$match": {
                        "basis": basis,
                        "tx_date": {
                            "$gte": window_start.isoformat(),
                            "$lt": as_of_date_obj.isoformat(),
                        },
                    }
                },
                {"$group": {"_id": None, "expense_sum": {"$sum": "$amount"}}},
            ]
            agg_rows = await qbo_db.general_ledger.aggregate(
                expense_pipeline
            ).to_list(length=2)
            if agg_rows:
                period_purchases = float(agg_rows[0].get("expense_sum") or 0.0)
                purchases_source = "general_ledger_period_expense"
        except Exception:  # noqa: BLE001 — proxy branch is best effort
            period_purchases = None
            purchases_source = None

        result = compute_payables_timing(
            as_of_date=as_of_value,
            basis=basis,
            open_ap_rows=open_ap_rows,
            paid_bill_rows=None,
            beginning_ap=beginning_ap,
            ending_ap=ending_ap,
            period_purchases=period_purchases,
            purchases_source=purchases_source,
            period_days=period_days,
        )

        metric = result.get("metric")
        if metric == METRIC_PAID_BILL_CYCLE:
            answer = (
                f"Average days to pay is {result.get('average_days_to_pay')} days "
                f"and average days late is {result.get('average_days_late')} days "
                f"as of {result.get('as_of_date')} across {result.get('row_count')} "
                "paid bill rows."
            )
            limitations: list[str] = []
        elif metric == METRIC_DPO_PROXY:
            answer = (
                f"DPO proxy is {result.get('dpo')} days as of "
                f"{result.get('as_of_date')} based on average AP and period "
                "purchases."
            )
            limitations = [
                "Labelled proxy because true credit purchases were unavailable "
                "and the helper used period expense or COGS as the purchases "
                "stand in.",
            ]
        else:
            answer = (
                f"Using open Accounts Payable as of {result.get('as_of_date')}, "
                f"the weighted average open AP age is "
                f"{result.get('weighted_average_open_ap_age_days')} days. "
                f"The weighted average days past due is "
                f"{result.get('weighted_average_days_past_due')} days across "
                f"{result.get('open_bill_count')} open bills totalling "
                f"{result.get('open_ap_total')}."
            )
            limitations = [
                "Paid bill payment dates were not available so this is an "
                "open AP timing view rather than a paid bill payment cycle "
                "average.",
            ]

        if metric == METRIC_OPEN_AP_TIMING and result.get("open_bill_count", 0) == 0:
            confidence = "low"
        elif metric == METRIC_OPEN_AP_TIMING:
            confidence = "medium"
        elif metric == METRIC_DPO_PROXY:
            confidence = "medium"
        else:
            confidence = "high"

        return {
            "answer": answer,
            "metric": metric,
            "confidence": confidence,
            "limitations": limitations,
            "details": result,
        }

    elif tool_name == "get_qbo_payables_metrics":
        # Real QBO data path. Reads the same ap_bills lake the working
        # capital dashboard reads, runs the deterministic helper, and
        # returns the structured payload the assistant quotes from.
        # No refusals. If the lake is empty or the paid bill payment
        # dates are not synced the response says so plainly in the
        # warnings list.
        from datetime import date as _d, datetime as _dt, timezone as _tz

        from services.nova_engine.db import get_qbo_db
        from services.finance.qbo_payables_report import (
            _normalize_ap_row as _norm_ap,
            calculate_payables_metrics,
            AP_COLLECTION,
            MAX_ROWS,
        )
        from services.qbo_sync_service import (
            get_finance_data_freshness,
            stale_after_seconds,
        )

        as_of_arg = (tool_args.get("as_of_date") or "").strip()
        if as_of_arg and not _is_valid_date(as_of_arg):
            return {
                "error": "invalid_date_format",
                "detail": f"as_of_date must be YYYY-MM-DD, got {as_of_arg!r}",
            }
        as_of_iso = as_of_arg or _dt.now(_tz.utc).date().isoformat()

        qbo_db = get_qbo_db()
        sync = await get_finance_data_freshness(qbo_db)
        ap_cursor = qbo_db[AP_COLLECTION].find({}, {"_id": 0}).limit(MAX_ROWS)
        ap_raw = await ap_cursor.to_list(length=MAX_ROWS)
        ap_norm = [_norm_ap(r) for r in ap_raw]
        payables = calculate_payables_metrics(ap_norm, as_of_iso)
        threshold_hours = max(1, int(stale_after_seconds() // 3600))

        warnings: list[str] = []
        if not ap_norm:
            warnings.append(
                "No AP rows are present in the synced QBO lake. "
                "Confirm the QBO sync has run."
            )

        return {
            "as_of": as_of_iso,
            "sync": {
                "last_successful_sync_at": sync.get("last_successful_sync_at"),
                "is_stale": bool(sync.get("is_stale", False)),
                "stale_threshold_hours": threshold_hours,
                "warnings": [sync["warning"]] if sync.get("warning") else [],
            },
            "payables": payables,
            "warnings": warnings,
            "answer": (
                "Average payables time can mean either the paid bill "
                "payment cycle (payment_date minus bill_date) or the "
                "current open bill age (as_of minus bill_date). Both "
                "are returned. Quote payables.average_paid_bill_payment_days "
                "and payables.average_open_bill_age_days verbatim. When "
                "the paid cycle is null the warnings list explains why."
            ),
        }

    elif tool_name == "get_qbo_weekly_finance_report":
        from datetime import datetime as _dt2, timezone as _tz2

        from services.nova_engine.db import get_qbo_db
        from services.finance.qbo_payables_report import (
            DEFAULT_WEEK_ENDING_DAY,
            build_weekly_finance_report,
        )
        from services.qbo_sync_service import (
            get_finance_data_freshness,
            stale_after_seconds,
        )

        as_of_arg = (tool_args.get("as_of_date") or "").strip()
        if as_of_arg and not _is_valid_date(as_of_arg):
            return {
                "error": "invalid_date_format",
                "detail": f"as_of_date must be YYYY-MM-DD, got {as_of_arg!r}",
            }
        as_of_iso = as_of_arg or _dt2.now(_tz2.utc).date().isoformat()

        try:
            weeks = max(1, min(int(tool_args.get("weeks", 8)), 26))
        except (TypeError, ValueError):
            weeks = 8

        week_ending_day = tool_args.get("week_ending_day") or DEFAULT_WEEK_ENDING_DAY

        qbo_db = get_qbo_db()
        sync = await get_finance_data_freshness(qbo_db)
        report = await build_weekly_finance_report(
            qbo_db,
            tenant_id=None,
            as_of_date=as_of_iso,
            week_count=weeks,
            week_ending_day=week_ending_day,
            sync_freshness=sync,
        )

        # Trim raw rows so the chat response stays bounded. The full
        # rows live on the JSON endpoint and the CSV export. The
        # summary plus the bucket totals are the parts Nova narrates.
        raw_ar_count = len(report.get("raw_ar") or [])
        raw_ap_count = len(report.get("raw_ap") or [])

        return {
            "as_of": report["as_of"],
            "week_ending_day": report["week_ending_day"],
            "week_count": report["week_count"],
            "sync": {
                "last_successful_sync_at": sync.get("last_successful_sync_at"),
                "is_stale": bool(sync.get("is_stale", False)),
                "stale_threshold_hours": max(1, int(stale_after_seconds() // 3600)),
                "warnings": [sync["warning"]] if sync.get("warning") else [],
            },
            "summary": report["summary"],
            "ar_weekly": report["ar_weekly"],
            "ap_weekly": report["ap_weekly"],
            "sync_audit": report["sync_audit"],
            "warnings": report["warnings"],
            "row_counts": {
                "raw_ar_rows": raw_ar_count,
                "raw_ap_rows": raw_ap_count,
            },
            "answer": (
                "Quote the summary block verbatim. Walk the user "
                "through ar_weekly and ap_weekly bucket totals if "
                "they asked for the cash plan. If they want the "
                "spreadsheet, follow up with "
                "export_qbo_weekly_finance_report."
            ),
        }

    elif tool_name == "export_qbo_weekly_finance_report":
        from datetime import datetime as _dt3, timezone as _tz3

        from services.finance.qbo_payables_report import DEFAULT_WEEK_ENDING_DAY

        as_of_arg = (tool_args.get("as_of_date") or "").strip()
        if as_of_arg and not _is_valid_date(as_of_arg):
            return {
                "error": "invalid_date_format",
                "detail": f"as_of_date must be YYYY-MM-DD, got {as_of_arg!r}",
            }
        as_of_iso = as_of_arg or _dt3.now(_tz3.utc).date().isoformat()
        try:
            weeks = max(1, min(int(tool_args.get("weeks", 8)), 26))
        except (TypeError, ValueError):
            weeks = 8
        week_ending_day = tool_args.get("week_ending_day") or "friday"
        if isinstance(week_ending_day, int):
            names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
            week_ending_day = names[week_ending_day] if 0 <= week_ending_day <= 6 else "friday"

        # The chat surface fetches the URL itself; the tool returns
        # the route + query string so Nova can hand the user a
        # clickable link without inventing a path.
        url = (
            "/api/finance/qbo/weekly-plan/export"
            f"?as_of={as_of_iso}&weeks={weeks}&week_ending_day={week_ending_day}"
        )
        return {
            "download_url": url,
            "as_of": as_of_iso,
            "weeks": weeks,
            "week_ending_day": week_ending_day,
            "media_type": "text/csv",
            "filename": f"qbo_weekly_finance_plan_{as_of_iso}.csv",
            "answer": (
                "Render the download_url as a link. Mention that the "
                "CSV opens in Excel or any spreadsheet tool. The "
                "filename and the as_of date are stable across runs "
                "for the same anchor date."
            ),
        }

    elif tool_name == "get_revenue_trend":
        from services.nova_engine.db import get_qbo_db

        try:
            months = min(int(tool_args.get("months", 12)), 24)
        except (TypeError, ValueError):
            months = 12
        if months < 1:
            months = 1

        qbo_db = get_qbo_db()
        # Pull monthly P&L reports to extract revenue line items.
        docs = await qbo_db.reports_pnl.find(
            {}, {"_id": 0}
        ).sort("period", -1).limit(months).to_list(months)
        if not docs:
            return {
                "error": "unavailable_data",
                "detail": (
                    "No QBO P&L reports found for revenue trend. "
                    "Confirm QBO sync has run."
                ),
                "hint": "Direct the user to /api/finance/revenue/trend.",
            }
        # Return newest-to-oldest; LLM reverses for narration if needed.
        return {
            "months_requested": months,
            "monthly_pnl_reports": docs,
            "note": (
                "Extract Income/Revenue totals from each report's summary "
                "to narrate the trend. Do NOT recompute the amounts."
            ),
        }

    elif tool_name == "get_top_customers_by_income":
        from services.nova_engine.db import get_qbo_db

        try:
            limit = min(int(tool_args.get("limit", 15)), 50)
        except (TypeError, ValueError):
            limit = 15
        if limit < 1:
            limit = 1

        qbo_db = get_qbo_db()
        docs = await qbo_db.reports_customer_income.find(
            {}, {"_id": 0}
        ).sort("_id", -1).limit(1).to_list(1)
        if not docs:
            return {
                "error": "unavailable_data",
                "detail": (
                    "No QBO Customer Income report found in reports_customer_income. "
                    "Confirm QBO sync has run and the report is enabled."
                ),
                "hint": "Direct the user to /api/finance/revenue/by-customer.",
            }
        report = docs[0]
        # Respect the caller's limit on line items if the report carries
        # row-level data. The report shape is QBO-native — narrate as-is.
        return {"limit": limit, "report": report}

    elif tool_name == "get_top_vendors_by_spend":
        from services.nova_engine.db import get_qbo_db

        try:
            limit = min(int(tool_args.get("limit", 15)), 50)
        except (TypeError, ValueError):
            limit = 15
        if limit < 1:
            limit = 1

        qbo_db = get_qbo_db()
        docs = await qbo_db.reports_vendor_expenses.find(
            {}, {"_id": 0}
        ).sort("_id", -1).limit(1).to_list(1)
        if not docs:
            return {
                "error": "unavailable_data",
                "detail": (
                    "No QBO Vendor Expenses report found in reports_vendor_expenses. "
                    "Confirm QBO sync has run and the report is enabled."
                ),
                "hint": "Direct the user to /api/finance/revenue/by-vendor.",
            }
        return {"limit": limit, "report": docs[0]}

    elif tool_name == "get_coa_lookup":
        from services.nova_engine.db import get_qbo_db

        kind = tool_args.get("kind")
        if kind is not None and kind not in ("income", "expense"):
            return {
                "error": "invalid_kind",
                "detail": f"kind must be 'income', 'expense', or omitted, got {kind!r}",
                "valid": ["income", "expense"],
            }

        qbo_db = get_qbo_db()
        query: dict[str, Any] = {}
        if kind == "income":
            # QBO classification for income/revenue accounts.
            query["classification"] = {"$in": ["Revenue", "Income"]}
        elif kind == "expense":
            query["classification"] = {"$in": ["Expense", "Cost of Goods Sold"]}

        coa_docs = await qbo_db.chart_of_accounts.find(
            query,
            {
                "_id": 0,
                "qbo_account_id": 1,
                "name": 1,
                "fully_qualified_name": 1,
                "account_type": 1,
                "classification": 1,
                "active": 1,
            },
        ).sort("name", 1).to_list(500)
        if not coa_docs:
            return {
                "error": "unavailable_data",
                "detail": (
                    "No QBO Chart of Accounts found"
                    + (f" matching kind={kind!r}" if kind else "")
                    + ". Confirm QBO sync has run."
                ),
                "hint": "Direct the user to /api/finance/operations/coa.",
            }
        return {
            "kind_filter": kind,
            "account_count": len(coa_docs),
            "accounts": coa_docs,
        }

    elif tool_name == "get_open_invoices":
        # Delegated to the invoice data-access helper. Date / bucket /
        # search filtering is pushed into the underlying query layer
        # there; the tool branch only validates args and formats the
        # response. Class-name filtering requires a side-join the
        # adapter post-filters in the helper (transparent via the
        # ``used_post_query_filtering`` flag on the result).
        from services.nova_engine.invoice_repository import (
            OpenInvoiceQuery,
            query_open_invoice_rows,
        )

        # ---- Validate args -------------------------------------------------
        search = (tool_args.get("search") or "").strip() or None
        bucket = tool_args.get("bucket") or None
        date_from = tool_args.get("date_from") or None
        date_to = tool_args.get("date_to") or None
        class_filter = tool_args.get("class_name") or None
        try:
            limit = min(int(tool_args.get("limit", 50)), 200)
        except (TypeError, ValueError):
            limit = 50
        if limit < 1:
            limit = 50

        for label, val in (("date_from", date_from), ("date_to", date_to)):
            if val is not None and not _is_valid_date(val):
                return {
                    "error": "invalid_date_format",
                    "detail": f"{label} must be YYYY-MM-DD, got {val!r}",
                }
        if date_from and date_to and date_from > date_to:
            return {
                "error": "invalid_date_range",
                "detail": (
                    f"date_from ({date_from}) must be on or before "
                    f"date_to ({date_to})."
                ),
            }

        # ---- Build the logical filter object ------------------------------
        filt = OpenInvoiceQuery(
            date_from=date_from,
            date_to=date_to,
            search=search,
            bucket=bucket,
            class_name=class_filter,
            limit=limit,
        )

        # ---- Call the data-access helper ----------------------------------
        result = await query_open_invoice_rows(filt)

        # ---- Empty source detection ---------------------------------------
        # The helper returns a default OpenInvoiceQueryResult when the
        # snapshot source is empty; surface a clear unavailable_data
        # error rather than a silently-zeroed reply.
        if (
            result.total_matched == 0
            and not result.rows
            and result.snapshot_synced_at is None
        ):
            return {
                "error": "unavailable_data",
                "detail": (
                    "No open-invoice snapshot is currently available. "
                    "Confirm the AR-aging snapshot source has been "
                    "synced."
                ),
                "hint": "Direct the user to /api/finance/operations/invoices.",
                "source": {
                    "logical_source": "open_invoice_snapshot",
                    "scope": "open_invoices",
                },
            }

        return {
            "total_matched": result.total_matched,
            "total_amount": result.total_amount,
            "total_open_balance": result.total_open_balance,
            "rows_returned": result.rows_returned,
            "preview_truncated": result.preview_truncated,
            "filters": {
                "search": search,
                "bucket": bucket,
                "date_from": date_from,
                "date_to": date_to,
                "class_name": class_filter,
                "open_only": True,
            },
            # New canonical field name.
            "rows": [r.model_dump() for r in result.rows],
            # Preserve the legacy ``returned`` key for any downstream
            # consumer; same content as ``rows``.
            "returned": [r.model_dump() for r in result.rows],
            "source": {
                "logical_source": "open_invoice_snapshot",
                "scope": "open_invoices",
                "synced_at": result.snapshot_synced_at,
                "used_post_query_filtering": result.used_post_query_filtering,
            },
            "note": (
                "Rows are open invoices from the latest AR-aging "
                "snapshot — paid and closed invoices are NOT included. "
                "total_amount is the open-invoice amount and "
                "total_open_balance is the open balance. Quote them "
                "verbatim — do NOT sum or average values yourself. "
                "For all-invoices-issued totals, use "
                "query_invoice_totals instead."
            ),
        }

    elif tool_name == "query_invoice_totals":
        # Aggregate issued-invoice questions ("how much have we
        # invoiced in <period>"). Backed by the issued-invoices source
        # via the invoice data-access helper. The source includes
        # paid AND closed invoices (AEGISAXIOM schema today). All totals
        # come from server-side or helper-computed aggregations — the
        # LLM never sums rows itself.
        from services.nova_engine.invoice_repository import (
            IssuedInvoiceQuery,
            query_issued_invoice_totals,
        )

        # ---- Validate args -------------------------------------------------
        date_from = tool_args.get("date_from")
        date_to = tool_args.get("date_to")
        if not _is_valid_date(date_from):
            return {
                "error": "invalid_date_format",
                "detail": (
                    f"date_from must be YYYY-MM-DD, got {date_from!r}. "
                    "Parse the user's natural-language date into a "
                    "concrete YYYY-MM-DD value before calling this "
                    "tool."
                ),
            }
        if not _is_valid_date(date_to):
            return {
                "error": "invalid_date_format",
                "detail": f"date_to must be YYYY-MM-DD, got {date_to!r}",
            }
        if date_from > date_to:
            return {
                "error": "invalid_date_range",
                "detail": (
                    f"date_from ({date_from}) must be on or before "
                    f"date_to ({date_to})."
                ),
            }
        try:
            requested_limit = int(tool_args.get("limit", 100))
        except (TypeError, ValueError):
            return {
                "error": "invalid_limit",
                "detail": f"limit must be an integer, got {tool_args.get('limit')!r}",
            }
        if requested_limit < 1:
            return {"error": "invalid_limit", "detail": "limit must be >= 1"}
        clamp_warning: Optional[str] = None
        if requested_limit > 500:
            clamp_warning = (
                f"limit={requested_limit} exceeded the hard cap of "
                "500; preview clamped to 500 rows. "
                "total_invoice_amount still reflects all matching "
                "issued invoices."
            )
            effective_limit = 500
        else:
            effective_limit = requested_limit

        # Default to False so direct LLM tool calls (shadow mode, ad
        # hoc test calls, anything not routed through the planner
        # compiler) don't pull a row preview on every aggregate.
        # Mirrors the compiler's INVOICE_TOTAL default in
        # services/ai/agents/finance_query_compiler.py — keep the two
        # in sync. When the LLM asks for "show me invoices issued in
        # <period>" the planner classifies it as INVOICE_DETAIL and
        # the compiler explicitly sets include_rows=True; aggregate
        # phrasings ("how much have we invoiced") never need rows.
        include_rows_raw = tool_args.get("include_rows", False)
        include_rows = bool(include_rows_raw)

        filt = IssuedInvoiceQuery(
            date_from=date_from,
            date_to=date_to,
            search=(tool_args.get("search") or None),
            customer=(tool_args.get("customer") or None),
            class_name=(tool_args.get("class_name") or None),
            location=(tool_args.get("location") or None),
            status=(tool_args.get("status") or None),
            include_rows=include_rows,
            limit=effective_limit,
        )

        result = await query_issued_invoice_totals(filt)

        warnings: list[str] = []
        if clamp_warning:
            warnings.append(clamp_warning)
        if result.includes_paid_and_closed == "unknown":
            warnings.append(
                "The issued-invoices source could not self-describe "
                "whether paid/closed invoices are included; the total "
                "may understate the period."
            )
        if result.used_post_query_filtering:
            warnings.append(
                "A class-name filter was requested; the data-access "
                "helper post-filtered the preview window and "
                "recomputed totals from the surviving subset. Totals "
                "reflect rows in the preview window when class "
                "filtering is active."
            )

        return {
            "date_from": date_from,
            "date_to": date_to,
            "invoice_count": result.invoice_count,
            "total_invoice_amount": result.total_invoice_amount,
            "rows_returned": result.rows_returned,
            "preview_truncated": result.preview_truncated,
            "rows": [r.model_dump() for r in result.rows],
            "filters": {
                "date_from": date_from,
                "date_to": date_to,
                "search": tool_args.get("search") or None,
                "customer": tool_args.get("customer") or None,
                "status": tool_args.get("status") or None,
                "class_name": tool_args.get("class_name") or None,
                "location": tool_args.get("location") or None,
            },
            "source": {
                "logical_source": "issued_invoices",
                "scope": "all_issued_invoices",
                "includes_paid_and_closed": result.includes_paid_and_closed,
                "synced_at": result.snapshot_synced_at,
            },
            "warnings": warnings,
            "note": (
                "total_invoice_amount is the sum of issued invoice "
                "amounts in the date window across the issued-invoices "
                "source. Quote it verbatim — do NOT sum rows yourself. "
                "Use get_open_invoices for OPEN-invoice / aging "
                "questions."
            ),
        }

    elif tool_name == "get_open_bills":
        from services.nova_engine.db import get_qbo_db

        search = (tool_args.get("search") or "").strip().lower()
        date_from = tool_args.get("date_from") or None
        date_to = tool_args.get("date_to") or None
        class_filter = tool_args.get("class_name") or None
        open_only = bool(tool_args.get("open_only", False))
        try:
            limit = min(int(tool_args.get("limit", 50)), 200)
        except (TypeError, ValueError):
            limit = 50
        if limit < 1:
            limit = 50

        for label, val in (("date_from", date_from), ("date_to", date_to)):
            if val is not None and not _is_valid_date(val):
                return {
                    "error": "invalid_date_format",
                    "detail": f"{label} must be YYYY-MM-DD, got {val!r}",
                }

        qbo_db = get_qbo_db()
        q: dict[str, Any] = {}
        if open_only:
            q["balance"] = {"$gt": 0}
        if class_filter:
            q["raw_data.Line.AccountBasedExpenseLineDetail.ClassRef.name"] = class_filter
        if date_from or date_to:
            range_q: dict[str, Any] = {}
            if date_from:
                range_q["$gte"] = date_from
            if date_to:
                range_q["$lte"] = date_to
            q["raw_data.TxnDate"] = range_q

        cursor = qbo_db.ap_bills.find(q, {"_id": 0}).sort([("due_date", -1)]).limit(limit)
        docs = await cursor.to_list(length=limit)
        total = await qbo_db.ap_bills.count_documents(q)

        rows = []
        for d in docs:
            raw = d.get("raw_data") or {}
            vendor = (raw.get("VendorRef") or {}).get("name") if isinstance(raw.get("VendorRef"), dict) else None
            doc_num = raw.get("DocNumber")
            if search:
                if search not in str(vendor or "").lower() and search not in str(doc_num or "").lower():
                    continue
            bill_class = None
            for line in raw.get("Line") or []:
                cls = ((line or {}).get("AccountBasedExpenseLineDetail") or {}).get("ClassRef") or {}
                if cls.get("name"):
                    bill_class = cls["name"]
                    break
            rows.append(
                {
                    "doc_num": doc_num,
                    "vendor": vendor,
                    "txn_date": raw.get("TxnDate"),
                    "due_date": d.get("due_date") or raw.get("DueDate"),
                    "amount": raw.get("TotalAmt") or raw.get("Balance"),
                    "balance": d.get("balance") or raw.get("Balance"),
                    "class_name": bill_class,
                    "location": (raw.get("DepartmentRef") or {}).get("name"),
                    "memo": raw.get("PrivateNote"),
                }
            )
        return {
            "filters": {
                "search": search or None,
                "date_from": date_from,
                "date_to": date_to,
                "class_name": class_filter,
                "open_only": open_only,
            },
            "total_matched": total,
            "returned": rows,
            "note": (
                "Quote the figures verbatim — do NOT sum or average values "
                "yourself. If the user asks for an aggregate, call "
                "query_finance_data or get_ap_aging instead."
            ),
        }

    elif tool_name == "query_gl_transactions":
        # Per-row GL transaction detail with optional blank-location
        # filtering. Read-only against qbo_db.general_ledger. The tool
        # accepts only YYYY-MM-DD date strings - the LLM is responsible
        # for parsing natural-language date phrases before the call.
        # See nova.py system prompt for the rendering contract.
        from services.nova_engine.account_categories import categorize
        from services.nova_engine.db import get_qbo_db

        t_total_start = time.monotonic()

        # ---- Validation ----
        date_from = tool_args.get("date_from")
        date_to = tool_args.get("date_to")
        if not _is_valid_date(date_from):
            return {
                "error": "invalid_date_format",
                "detail": (
                    f"date_from must be YYYY-MM-DD, got {date_from!r}. "
                    "Parse the user's natural-language date into a concrete "
                    "calendar date before calling this tool."
                ),
            }
        if not _is_valid_date(date_to):
            return {
                "error": "invalid_date_format",
                "detail": (
                    f"date_to must be YYYY-MM-DD, got {date_to!r}."
                ),
            }
        if date_from > date_to:
            return {
                "error": "invalid_date_range",
                "detail": (
                    f"date_from ({date_from}) must be on or before "
                    f"date_to ({date_to})."
                ),
            }

        basis = tool_args.get("basis", "accrual")
        if basis not in ("accrual", "cash"):
            return {
                "error": "invalid_basis",
                "detail": f"basis must be 'accrual' or 'cash', got {basis!r}",
            }

        location_blank = bool(tool_args.get("location_blank", False))

        # Limit clamp: < 1 is a validation error (user requested zero or
        # negative rows), > 500 silently clamps with a notes warning.
        try:
            requested_limit = int(tool_args.get("limit", 100))
        except (TypeError, ValueError):
            return {
                "error": "invalid_limit",
                "detail": f"limit must be an integer, got {tool_args.get('limit')!r}",
            }
        if requested_limit < 1:
            return {
                "error": "invalid_limit",
                "detail": "limit must be >= 1",
            }
        clamp_warning: str | None = None
        if requested_limit > 500:
            clamp_warning = (
                f"limit={requested_limit} exceeded the hard cap of 500; "
                "preview was clamped to 500 rows. total_income still "
                "reflects all matching rows in the date range."
            )
            effective_limit = 500
        else:
            effective_limit = requested_limit

        # ---- Field-availability probe ----
        qbo_db = get_qbo_db()
        location_field_status = await _check_location_field_status(qbo_db)

        # ---- Short-circuit: location_blank requested but field unavailable ----
        # The tool runs the same query path in both cases; the short-circuit
        # is purely about not materializing a misleading result. When the
        # schema doesn't carry location data, "filter to blank-location"
        # cannot be expressed honestly - we surface that to the LLM via
        # the notes string so it gets passed through to the user.
        if location_blank and location_field_status == "unavailable":
            t_total_ms = (time.monotonic() - t_total_start) * 1000.0
            logger.info(
                "[nova_timing] tool=query_gl_transactions "
                "date_from=%s date_to=%s basis=%s rows=0 "
                "query_ms=0.0 total_ms=%.1f",
                date_from, date_to, basis, t_total_ms,
            )
            return {
                "date_from": date_from,
                "date_to": date_to,
                "basis": basis,
                "location_blank": location_blank,
                "row_count": 0,
                "rows_returned": 0,
                "preview_truncated": False,
                "total_income": 0.0,
                "rows": [],
                "location_field_status": "unavailable",
                "notes": (
                    "The general_ledger collection for this tenant does "
                    "not carry QBO Location or Department fields, so "
                    "'blank location' versus 'tagged location' cannot be "
                    "distinguished until the QBO sync includes those "
                    "fields. No transactions were returned for the "
                    "blank-location filter."
                ),
            }

        # ---- Mongo query ----
        # Mongo-side filters: tx_date range + basis. When location_blank
        # is requested AND the schema carries location fields, push a
        # null/missing/empty filter into the $match so we don't materialize
        # populated-location rows just to discard them in Python.
        match: dict[str, Any] = {
            "tx_date": {"$gte": date_from, "$lte": date_to},
            "basis": basis,
        }
        if location_blank and location_field_status == "available":
            # In Mongo, ``{name: None}`` already matches both "field
            # is null" AND "field does not exist", so the previous
            # ``{name: {"$exists": False}}`` clause was redundant.
            # The regex ``^\s*$`` matches the empty string and any
            # whitespace-only string, so a separate ``{name: ""}``
            # clause is also unnecessary. Result: two clauses per
            # field instead of three, same logical coverage.
            blank_clauses: list[dict[str, Any]] = []
            for name in _LOCATION_LIKE_FIELDS:
                blank_clauses.append({"$or": [
                    {name: None},
                    {name: {"$regex": r"^\s*$"}},
                ]})
            match["$and"] = blank_clauses

        # Project only the fields the response shape needs. Avoids
        # pulling oversized raw_data blobs on the common path. The
        # nested location-blank check (_is_blank_location) walks
        # _LOCATION_LIKE_NESTED_PATHS, all of which root at
        # ``raw_data``; those paths only matter when the caller asked
        # for blank-location filtering AND the schema actually carries
        # location data. In every other case the per-row raw_data
        # blob is pulled out of Mongo and discarded — wasted bandwidth
        # and memory.
        projection = {
            "_id": 0,
            "tx_date": 1,
            "basis": 1,
            "account_name": 1,
            "txn_type": 1,
            "doc_num": 1,
            "name": 1,
            "memo": 1,
            "amount": 1,
            "debit": 1,
            "credit": 1,
            "location": 1,
            "location_id": 1,
            "location_name": 1,
            "department": 1,
            "department_name": 1,
            "department_ref_id": 1,
            "department_ref_name": 1,
        }
        if location_blank and location_field_status == "available":
            projection["raw_data"] = 1

        # _SCAN_CAP lives at module scope so callers / tests share
        # one constant and the dispatcher doesn't re-define it on
        # every call.

        t_query_start = time.monotonic()
        cursor = qbo_db.general_ledger.find(match, projection).sort(
            [("tx_date", 1), ("doc_num", 1)]
        )
        # Read one extra row beyond the cap so we can distinguish
        # "exactly _SCAN_CAP rows matched" (NOT truncated) from
        # "more rows than _SCAN_CAP exist" (truncated). The previous
        # `len(raw_rows) >= _SCAN_CAP` check returned True at exactly
        # _SCAN_CAP and surfaced a false truncation warning.
        raw_rows = await cursor.to_list(length=_SCAN_CAP + 1)
        t_query_ms = (time.monotonic() - t_query_start) * 1000.0
        scan_truncated = len(raw_rows) > _SCAN_CAP
        if scan_truncated:
            raw_rows = raw_rows[:_SCAN_CAP]

        # ---- Python categorize + filter + sum ----
        # Reversal handling. Negative rows posted to income accounts
        # are kept in the preview with their signed amount preserved
        # AND counted in the signed total. QBO posts reversals as
        # Expense, Bill, Journal Entry, Credit Memo, refund, or as
        # direct negative rows on income accounts. Every one of those
        # txn_types must reach revenue if the account categorises as
        # revenue. txn_type is metadata only and never excludes a row.
        # K4. The accumulation moved into compute_reversal_metadata in
        # services/nova_engine/formulas.py so all three finance
        # surfaces share one signed total math path. Category filter,
        # location filter, and txn type collection stay here because
        # they are call site specific.
        income_rows: list[dict[str, Any]] = []
        income_amounts: list[float] = []
        txn_types_seen: set[str] = set()
        for r in raw_rows:
            account_name = r.get("account_name") or ""
            cat = categorize(account_name)
            if cat not in _INCOME_CATEGORIES:
                continue
            # When location_blank is requested AND the schema is
            # available, the Mongo $match already filtered most non-blank
            # rows. The Python check below catches whitespace-only values
            # the Mongo regex would have missed.
            if location_blank and not _is_blank_location(r):
                continue

            amount_raw = _safe_float(r.get("amount"))
            income_amounts.append(amount_raw)
            tt_value = r.get("txn_type")
            if tt_value is not None:
                txn_types_seen.add(str(tt_value))

            # Resolve a single user-facing location string. Prefer the
            # most specific human-readable value; fall back through the
            # department-like names, then the id flavours so a row that
            # only carries an id is still attributable. None when every
            # location-like field is blank.
            location_display = (
                r.get("location_name")
                or r.get("location")
                or r.get("department_name")
                or r.get("department_ref_name")
                or r.get("department")
                or r.get("location_id")
                or r.get("department_ref_id")
            )
            if isinstance(location_display, str) and location_display.strip() == "":
                location_display = None

            income_rows.append({
                "tx_date": r.get("tx_date"),
                "basis": r.get("basis"),
                # account_id and class_name are not present in the AEGISAXIOM
                # general_ledger schema today, so they were always
                # serialized as None and added noise to every row.
                # Dropped from the response shape — the LLM now sees
                # only fields with potential values.
                "account_name": account_name or None,
                "category": cat,
                "txn_type": r.get("txn_type"),
                "doc_num": r.get("doc_num"),
                "name": r.get("name"),
                "memo": r.get("memo"),
                "location": location_display,
                "department": (
                    r.get("department_ref_name")
                    or r.get("department_name")
                    or r.get("department")
                    or r.get("department_ref_id")
                ),
                # Signed amount canonical for reconciliation against
                # the QBO Transaction Report. amount_raw is preserved
                # for backwards compatibility with existing callers
                # and equals amount. normalized_amount is the positive
                # magnitude kept for callers that already render the
                # preview shape, but reconciliation must use amount.
                "amount": amount_raw,
                "amount_raw": amount_raw,
                "debit": _safe_float(r.get("debit")),
                "credit": _safe_float(r.get("credit")),
                "normalized_amount": abs(amount_raw),
                # A row is reversal like when its signed amount is
                # negative and the account maps to revenue. The flag
                # is account driven, not memo driven, so a legitimate
                # invoice or deposit with a negative amount is still
                # labelled correctly.
                "is_reversal_like": amount_raw < 0,
            })

        # K4. Aggregate the collected amounts through the shared
        # compute_reversal_metadata helper so signed_total,
        # positive_total, negative_total, positive_row_count, and
        # negative_row_count are computed the same way across all
        # finance surfaces.
        reversal_meta = compute_reversal_metadata(income_amounts)
        signed_total = reversal_meta["signed_total"]
        negative_row_count = reversal_meta["negative_row_count"]

        row_count = len(income_rows)
        rows_returned = min(row_count, effective_limit)
        preview_rows = income_rows[:rows_returned]
        preview_truncated = row_count > rows_returned

        # Canonical net total preserves sign so reconciliation against
        # the QBO Transaction Report lines up. The legacy total_income
        # field is the absolute magnitude and is kept so existing
        # callers do not break.
        total_income = abs(signed_total)

        notes_parts: list[str] = []
        if clamp_warning:
            notes_parts.append(clamp_warning)
        if preview_truncated:
            notes_parts.append(
                f"Showing first {rows_returned} of {row_count} rows. "
                "total_income is computed across all "
                f"{row_count} matching rows."
            )
        if scan_truncated:
            notes_parts.append(
                f"Mongo scan was capped at {_SCAN_CAP} rows; result "
                "may be partial. Narrow the date range to be sure."
            )
        if negative_row_count > 0:
            notes_parts.append(
                "Negative rows posted to income accounts are included "
                "as signed reductions to revenue. signed_total is the "
                "net of positive_total plus negative_total and matches "
                "the QBO Transaction Report convention."
            )
        if signed_total < 0:
            notes_parts.append(
                "Raw GL sum across the matching rows was negative. "
                "Refund or reversal entries exceeded sales for this "
                "window. total_income carries the absolute magnitude "
                "for legacy callers. signed_total carries the actual "
                "net so the negative direction is preserved."
            )
        if location_blank and location_field_status == "unavailable":
            # Reachable only via the early short-circuit above; kept for
            # documentation symmetry.
            notes_parts.append(
                "Location/Department fields are not present in this "
                "tenant's general_ledger schema."
            )
        notes = " ".join(notes_parts) if notes_parts else None

        t_total_ms = (time.monotonic() - t_total_start) * 1000.0
        logger.info(
            "[nova_timing] tool=query_gl_transactions "
            "date_from=%s date_to=%s basis=%s rows=%d "
            "query_ms=%.1f total_ms=%.1f",
            date_from, date_to, basis, row_count, t_query_ms, t_total_ms,
        )

        return {
            "date_from": date_from,
            "date_to": date_to,
            "basis": basis,
            "location_blank": location_blank,
            "row_count": row_count,
            "rows_returned": rows_returned,
            "preview_truncated": preview_truncated,
            "total_income": total_income,
            # Reversal metadata. signed_total is the canonical
            # reconciliation number against the QBO Transaction
            # Report. positive_total and negative_total split signed
            # contributions so the user can see how much of the net
            # came from reversals. negative_row_count counts the
            # negative rows posted to revenue accounts. The earlier
            # reversal_like_row_count alias was dropped because it
            # always equalled negative_row_count.
            "signed_total": signed_total,
            "positive_total": reversal_meta["positive_total"],
            "negative_total": reversal_meta["negative_total"],
            "positive_row_count": reversal_meta["positive_row_count"],
            "negative_row_count": negative_row_count,
            "transaction_types": sorted(txn_types_seen),
            "reversal_note": (
                "Negative rows posted to income accounts are included "
                "as signed reductions to revenue."
            ),
            "rows": preview_rows,
            "location_field_status": location_field_status,
            "notes": notes,
        }

    elif tool_name == "get_finance_data_freshness":
        # Read-only freshness probe — answers "how stale is the
        # finance data?" without calling QuickBooks or n8n. Nova
        # uses this to warn the user before quoting a stale total.
        # Nova is NOT allowed to trigger a sync; that path goes
        # through a human / admin via /api/qbo/sync/trigger.
        from database import get_db
        from services.qbo_sync_service import get_finance_data_freshness as _gfdf

        try:
            db = get_db()
            return await _gfdf(db)
        except Exception as exc:  # noqa: BLE001 — never break Nova
            logger.warning("get_finance_data_freshness failed: %s", exc)
            return {
                "last_successful_sync_at": None,
                "sync_lag_seconds": None,
                "is_stale": True,
                "warning": (
                    "Finance data freshness could not be verified. "
                    "Treat any quoted totals as potentially stale."
                ),
            }

    elif tool_name == "get_finance_data_status":
        # Read-only snapshot combining freshness with actual
        # collection presence. Nova uses this to answer "can you
        # access the database?" / "what data do you have?" without
        # confusing a missing qbo_sync_requests record for an empty
        # finance data lake.
        from database import get_db
        from services.qbo_sync_service import get_finance_data_status as _gfds

        try:
            db = get_db()
            return await _gfds(db)
        except Exception as exc:  # noqa: BLE001 — never break Nova
            logger.warning("get_finance_data_status failed: %s", exc)
            return {
                "sync_status": {
                    "last_successful_sync_at": None,
                    "sync_lag_seconds": None,
                    "is_stale": True,
                    "warning": (
                        "Finance data status could not be verified."
                    ),
                },
                "user_facing_data_note": "Finance data is not available for this question yet.",
                "admin_sync_note": "Finance data status probe failed.",
                "data_status": {
                    "finance_data_present": False,
                    "general_ledger_usable": False,
                    "general_ledger": {
                        "collection": "general_ledger",
                        "exists": False,
                        "row_count": 0,
                        "latest_tx_date": None,
                        "latest_tx_date_basis": "all",
                        "available_basis_values": [],
                        "latest_synced_at": None,
                        "by_basis": [],
                    },
                    "collections": {},
                },
                "testing_status": {
                    "usable_for_finance_testing": False,
                    "reason": "Finance data status probe failed.",
                    "warnings": ["status_probe_failed"],
                },
            }

    elif tool_name == "get_gl_location_coverage":
        # Read-only coverage probe over the same general_ledger
        # location-like fields the location_blank filter on
        # query_gl_transactions uses. Nova calls this when the user
        # asks about location data quality, before quoting any
        # location-attributed total.
        from services.nova_engine.db import get_qbo_db
        from services.nova_engine.gl_location_coverage import (
            get_gl_location_coverage as _gl_loc_coverage,
        )

        date_from = tool_args.get("date_from")
        date_to = tool_args.get("date_to")
        if date_from is not None and not _is_valid_date(date_from):
            return {
                "error": "invalid_date_from",
                "detail": f"date_from must be YYYY-MM-DD, got {date_from!r}",
            }
        if date_to is not None and not _is_valid_date(date_to):
            return {
                "error": "invalid_date_to",
                "detail": f"date_to must be YYYY-MM-DD, got {date_to!r}",
            }
        basis = tool_args.get("basis")
        if basis is not None and basis not in ("accrual", "cash"):
            return {
                "error": "invalid_basis",
                "detail": f"basis must be 'accrual' or 'cash', got {basis!r}",
            }
        category = tool_args.get("category")
        if category is not None and category not in _CATEGORY_VALUES:
            return {
                "error": "invalid_category",
                "detail": f"category must be one of {sorted(_CATEGORY_VALUES)!r}, got {category!r}",
            }
        metric = tool_args.get("metric")
        if metric is not None and metric not in (
            "revenue", "expenses", "cogs", "gross_profit",
        ):
            return {
                "error": "invalid_metric",
                "detail": (
                    "metric must be one of 'revenue', 'expenses', 'cogs', "
                    f"'gross_profit', got {metric!r}"
                ),
            }

        try:
            qbo_db = get_qbo_db()
            return await _gl_loc_coverage(
                qbo_db,
                date_from=date_from,
                date_to=date_to,
                basis=basis,
                metric=metric,
                category=category,
            )
        except Exception as exc:  # noqa: BLE001 — never break Nova
            logger.warning("get_gl_location_coverage failed: %s", exc)
            return {
                "filters": {
                    "date_from": date_from,
                    "date_to": date_to,
                    "basis": basis,
                    "metric": metric,
                    "category": category,
                },
                "scope": {
                    "applied_metric": metric,
                    "applied_category": category,
                    "applied_categories": [],
                    "category_filter_applied": metric is not None or category is not None,
                    "scope_description": "scope unknown — probe failed",
                },
                "row_count": 0,
                "with_location_count": 0,
                "blank_location_count": 0,
                "coverage_pct": 0.0,
                "location_like_fields": list(_LOCATION_LIKE_FIELDS),
                "field_presence": {f: 0 for f in _LOCATION_LIKE_FIELDS},
                "by_basis": [],
                "warning": (
                    "GL location coverage probe failed; coverage cannot be "
                    "verified for this scope."
                ),
            }

    elif tool_name == "explain_finance_drivers":
        # Deterministic driver / variance breakdown. The tool reads
        # only general_ledger; variance, monthly buckets, and top
        # accounts are computed by analyze_finance_drivers — the LLM
        # narrates the returned shape but does not run arithmetic.
        from services.nova_engine.db import get_qbo_db
        from services.nova_engine.finance_driver_analysis import (
            analyze_finance_drivers as _analyze_drivers,
        )

        metric = tool_args.get("metric")
        if not isinstance(metric, str) or not metric:
            return {
                "error": "missing_metric",
                "detail": "metric is required.",
            }
        date_from = tool_args.get("date_from")
        date_to = tool_args.get("date_to")
        if not _is_valid_date(date_from):
            return {
                "error": "invalid_date_from",
                "detail": f"date_from must be YYYY-MM-DD, got {date_from!r}",
            }
        if not _is_valid_date(date_to):
            return {
                "error": "invalid_date_to",
                "detail": f"date_to must be YYYY-MM-DD, got {date_to!r}",
            }
        basis = tool_args.get("basis") or "accrual"
        if basis not in ("accrual", "cash"):
            return {
                "error": "invalid_basis",
                "detail": f"basis must be 'accrual' or 'cash', got {basis!r}",
            }
        # The schema declares compare_to as a string enum, but tool
        # callers can pass null to omit the comparison entirely.
        compare_to = tool_args.get("compare_to", "previous_period")
        try:
            limit = int(tool_args.get("limit", 10))
        except (TypeError, ValueError):
            limit = 10
        # Clamp so an LLM supplied limit cannot materialise an
        # unbounded top_accounts list. Mirrors the clamp pattern used
        # elsewhere in this file.
        limit = max(1, min(limit, 50))

        try:
            qbo_db = get_qbo_db()
            return await _analyze_drivers(
                qbo_db,
                metric=metric,
                date_from=date_from,
                date_to=date_to,
                basis=basis,
                compare_to=compare_to,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001 — never break Nova
            logger.warning("explain_finance_drivers failed: %s", exc)
            return {
                "metric": metric,
                "error": "driver_analysis_failed",
                "detail": (
                    "Driver analysis could not be computed for this "
                    "scope; check the date range and try a narrower "
                    "window."
                ),
            }

    elif tool_name == "create_finance_export":
        # Chat-native export: re-run the approved tool, build a
        # CSV/XLSX (row tools) or PDF (report tools), persist the file
        # in GridFS, and return metadata the chat UI renders as a
        # download card. The user never has to call the API endpoint
        # manually for the normal path.
        from database import get_db
        from services.nova_engine.finance_export_service import (
            EXPORTABLE_REPORTS,
            EXPORTABLE_TOOLS,
            SUPPORTED_FORMATS_BY_TOOL,
            ExportRequest,
            build_export,
        )
        from services.nova_engine.nova_export_storage import (
            ExportTooLargeError,
            save_export_artifact,
        )

        export_tool_name = tool_args.get("tool_name")
        allowed_export_tools = sorted(
            set(EXPORTABLE_TOOLS) | set(EXPORTABLE_REPORTS)
        )
        if export_tool_name not in allowed_export_tools:
            return {
                "error": "unsupported_tool_for_export",
                "detail": (
                    f"tool_name {export_tool_name!r} is not in the "
                    f"export allow-list. Supported row-table tools: "
                    f"{sorted(EXPORTABLE_TOOLS)}. Supported report "
                    f"tools: {sorted(EXPORTABLE_REPORTS)}."
                ),
                "supported_tools": allowed_export_tools,
            }
        export_tool_args = tool_args.get("tool_args") or {}
        if not isinstance(export_tool_args, dict):
            return {
                "error": "invalid_tool_args",
                "detail": "tool_args must be an object when supplied.",
            }
        # Format default depends on tool shape: report-style tools
        # default to PDF, row-table tools keep the CSV default they
        # had before report exports existed.
        default_format = "pdf" if export_tool_name in EXPORTABLE_REPORTS else "csv"
        export_format = (tool_args.get("format") or default_format).lower()
        supported_for_tool = SUPPORTED_FORMATS_BY_TOOL.get(
            export_tool_name, ()
        )
        if export_format not in supported_for_tool:
            return {
                "error": "unsupported_export_format",
                "detail": (
                    f"format {export_format!r} is not supported for "
                    f"{export_tool_name!r}. Supported: "
                    f"{list(supported_for_tool)}."
                ),
                "tool_name": export_tool_name,
                "format": export_format,
                "supported_formats": list(supported_for_tool),
            }

        try:
            request = ExportRequest(
                tool_name=export_tool_name,
                tool_args=dict(export_tool_args),
                format=export_format,
                requested_by_user_id=user_id,
                source="nova_chat",
            )
            export = await build_export(request, tool_runner=execute_tool)
        except ValueError as exc:
            return {"error": "export_build_failed", "detail": str(exc)}

        try:
            db = get_db()
            # Indexes are registered once at app startup
            # (server.py ensure_indexes block); this hot path no
            # longer pays the per-request ensure_indexes round-trip.
            persisted = await save_export_artifact(
                db,
                export_id=export.export_id,
                filename=export.filename,
                content_type=export.content_type,
                body=export.body,
                row_count=export.row_count,
                columns=export.columns,
                filters=export.filters,
                source_tool_name=export.source_tool_name,
                source_tool_args=dict(export_tool_args),
                source_metadata=export.source_metadata,
                warnings=export.warnings,
                requested_by_user_id=user_id,
                session_id=session_id,
                message_id=message_id,
                matched_rows=export.matched_rows,
                exported_rows=export.exported_rows,
                export_cap=export.export_cap,
                export_truncated=export.export_truncated,
            )
        except ExportTooLargeError as exc:
            return {
                "error": "export_too_large",
                "detail": str(exc),
                "size_bytes": exc.size_bytes,
                "max_bytes": exc.max_bytes,
            }
        except Exception as exc:  # noqa: BLE001 — never break Nova
            logger.warning("create_finance_export persistence failed: %s", exc)
            return {
                "error": "export_persistence_failed",
                "detail": (
                    "Export was generated but could not be persisted. "
                    "Try again, or use the POST /api/nova/exports "
                    "endpoint to download directly."
                ),
            }

        return {
            "status": "export_created",
            **persisted,
        }

    return {"error": "unknown_tool", "tool_name": tool_name}