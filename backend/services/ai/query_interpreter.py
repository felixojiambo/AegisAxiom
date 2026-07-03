"""AI-driven query interpretation and MongoDB filter building.

Two responsibilities:
1. Send user message to LLM to extract structured query filters + confirmation message
2. Convert those structured filters into MongoDB query dicts (with RBAC)
"""

import json
import logging
import re
from datetime import datetime, timezone

from services.ai.llm import call_llm
from services.ai.rbac import (
    deal_filter, zoho_deal_filter,
    commission_filter, zoho_commission_filter,
    opportunity_filter,
    combine_filters,
    project_filter, task_filter, sprint_filter,
    wave_filter, thread_filter, blocker_filter, standup_filter,
)

logger = logging.getLogger(__name__)

# Default query returned on interpretation failure
_EMPTY_QUERY = {
    "name_searches": [], "date_start": None, "date_end": None,
    "statuses": [], "stages": [], "amount_min": None, "amount_max": None,
    "sort_field": None, "sort_direction": "desc", "limit": 50,
    "aggregations": [],
    # time_window_hours is consumed by the PMO agent's recent-changes
    # aggregation. Null = no recency filter; integer = "things changed in
    # the last N hours" semantic. See registry.build_query_builder_prompt
    # for the LLM-facing teaching prompt.
    "time_window_hours": None,
    "confirmation_message": "Just to confirm, you would like me to search for **all recent records**?",
}

# Query builder uses the cheapest model
QUERY_BUILDER_MODEL = "gemini-3-flash-preview"


# ──────────────────────────────────────────────
#  AI QUERY INTERPRETATION (Pass 1)
# ──────────────────────────────────────────────

async def interpret_query_for_agent(message: str, route: str) -> dict:
    """Use LLM + agent's schema to interpret a natural-language message into query filters."""
    from services.ai.agents.registry import build_query_builder_prompt

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    year = datetime.now(timezone.utc).strftime("%Y")
    prompt = build_query_builder_prompt(route, today, year)

    if not prompt:
        logger.warning(f"No query builder prompt for route '{route}'; returning empty query")
        return dict(_EMPTY_QUERY)

    session_key = f"query_builder_{route}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"
    try:
        resp = await call_llm(prompt, message, session_key=session_key, model=QUERY_BUILDER_MODEL)
        cleaned = resp.text.strip()
        if "{" in cleaned:
            json_str = cleaned[cleaned.index("{"):cleaned.rindex("}") + 1]
            parsed = json.loads(json_str)
            logger.info(f"AI query interpretation: {json.dumps(parsed, default=str)}")
            return parsed
    except Exception as e:
        logger.warning(f"AI query interpretation failed: {e}")

    return dict(_EMPTY_QUERY)


# ──────────────────────────────────────────────
#  CONFIRMATION DETECTION
# ──────────────────────────────────────────────

_YES_PATTERNS = frozenset({
    "yes", "yeah", "yep", "yup", "correct", "that's right", "thats right",
    "right", "confirm", "confirmed", "go ahead", "proceed", "do it",
    "yes please", "that's correct", "thats correct", "sure", "absolutely",
    "exactly", "perfect", "looks good", "sounds good", "yes sir", "yes ma'am",
    "affirmative", "y",
})

_NO_PATTERNS = frozenset({
    "no", "nope", "not quite", "wrong", "incorrect", "change", "actually",
    "no that's wrong", "thats wrong", "cancel", "stop", "n", "nah",
    "not what i meant", "let me rephrase",
})


def detect_confirmation(message: str) -> str:
    """Classify a message as 'yes', 'no', or 'new_query'."""
    clean = message.strip().lower().rstrip(".,!?")
    if clean in _YES_PATTERNS:
        return "yes"
    if clean in _NO_PATTERNS:
        return "no"
    return "new_query"


# ──────────────────────────────────────────────
#  MONGODB FILTER BUILDERS
# ──────────────────────────────────────────────

def _name_regex_conditions(names: list, fields: list) -> list:
    """Build $or conditions matching any name across multiple fields."""
    if not names:
        return []
    ors = []
    for n in names:
        for f in fields:
            ors.append({f: {"$regex": re.escape(n), "$options": "i"}})
    return [{"$or": ors}]


def _date_range_condition(date_start, date_end, field: str) -> list:
    if not date_start and not date_end:
        return []
    cond = {}
    if date_start:
        cond["$gte"] = date_start
    if date_end:
        cond["$lt"] = date_end
    return [{field: cond}]


def _amount_range_conditions(amt_min, amt_max, fields: list) -> list:
    if amt_min is None and amt_max is None:
        return []
    ors = []
    for fld in fields:
        cond = {}
        if amt_min is not None:
            cond["$gte"] = amt_min
        if amt_max is not None:
            cond["$lte"] = amt_max
        ors.append({fld: cond})
    return [{"$or": ors}]


def build_commission_filters(query: dict, user) -> tuple:
    """Return (local_filter, zoho_filter, limit) for commission queries."""
    names = query.get("name_searches", [])
    date_start = query.get("date_start")
    date_end = query.get("date_end")
    statuses = query.get("statuses", [])
    amt_min = query.get("amount_min")
    amt_max = query.get("amount_max")
    limit = query.get("limit", 200)

    # Local conditions
    local_conds = (
        _name_regex_conditions(names, ["deal_name", "user_name"])
        + _date_range_condition(date_start, date_end, "created_at")
        + _amount_range_conditions(amt_min, amt_max, ["amount_owed", "amount_paid"])
    )
    local_statuses = [s for s in statuses if s.lower() in ("pending", "approved", "paid", "rejected")]
    if local_statuses:
        local_conds.append({"status": {"$in": [s.lower() for s in local_statuses]}})

    # Zoho conditions
    zoho_conds = (
        _name_regex_conditions(names, [
            "deal_name", "customer_name", "sales_rep_name",
            "sales_rep_email", "agent_of_records_name", "invoice_no",
        ])
        + _date_range_condition(date_start, date_end, "payment_date")
        + _amount_range_conditions(amt_min, amt_max, ["sales_rep_commission", "agent_commission"])
    )
    zoho_statuses = [s for s in statuses if s in ("Paid", "Owed", "Potential")]
    if zoho_statuses:
        zoho_conds.append({"$or": [
            {"sales_rep_commission_status": {"$in": zoho_statuses}},
            {"agent_commission_status": {"$in": zoho_statuses}},
        ]})

    local_filter = combine_filters(commission_filter(user), *local_conds)
    zoho_filter = combine_filters(zoho_commission_filter(user), *zoho_conds)
    return local_filter, zoho_filter, limit


def build_deal_filters(query: dict, user) -> tuple:
    """Return (local_filter, zoho_filter, limit) for deal queries."""
    names = query.get("name_searches", [])
    date_start = query.get("date_start")
    date_end = query.get("date_end")
    stages = query.get("stages", [])
    amt_min = query.get("amount_min")
    amt_max = query.get("amount_max")
    limit = query.get("limit", 200)

    local_conds = (
        _name_regex_conditions(names, [
            "name", "account_name", "deal_owner_name",
            "assigned_sales_rep_name", "description",
        ])
        + _date_range_condition(date_start, date_end, "close_date")
        + _amount_range_conditions(amt_min, amt_max, ["amount"])
    )
    if stages:
        local_conds.append({"stage": {"$in": stages}})

    zoho_conds = (
        _name_regex_conditions(names, [
            "deal_name", "account_name", "deal_owner_name", "agent_of_record_name",
        ])
        + _date_range_condition(date_start, date_end, "close_date")
        + _amount_range_conditions(amt_min, amt_max, ["amount"])
    )
    if stages:
        zoho_conds.append({"stage": {"$in": stages}})

    local_filter = combine_filters(deal_filter(user), *local_conds)
    zoho_filter = combine_filters(zoho_deal_filter(user), *zoho_conds)
    return local_filter, zoho_filter, limit


def build_opportunity_filters(query: dict, user) -> tuple:
    """Return (opp_filter, limit) for opportunity queries."""
    names = query.get("name_searches", [])
    date_start = query.get("date_start")
    date_end = query.get("date_end")
    stages = query.get("stages", [])
    amt_min = query.get("amount_min")
    amt_max = query.get("amount_max")
    limit = query.get("limit", 200)

    conds = (
        _name_regex_conditions(names, [
            "name", "account_name", "opportunity_owner_name",
            "agent_of_record_name", "contact_name", "description",
        ])
        + _date_range_condition(date_start, date_end, "created_at")
        + _amount_range_conditions(amt_min, amt_max, ["amount"])
    )
    if stages:
        conds.append({"stage": {"$in": stages}})

    opp_filter = combine_filters(opportunity_filter(user), *conds)
    return opp_filter, limit


# ──────────────────────────────────────────────
#  PMO FILTER BUILDER
#
#  Returns a dict mapping each pm_* collection name to its combined RBAC +
#  natural-language filter. Mirrors `build_deal_filters` shape (regex-i name
#  searches, $gte/$lt date ranges, $in for enums) and adds PM-specific
#  fields: project_type (methodology), priority, statuses-per-entity.
# ──────────────────────────────────────────────

# Project status / health enums (lowercase per mapping file)
_PROJECT_STATUSES = {"planning", "active", "on_hold", "closed"}
_PROJECT_TYPES = {"waterfall", "agile", "waves_threads"}
_HEALTHS = {"green", "yellow", "red"}

# Per-entity status enums (mapped from .ingestion/mappings/projects.json)
_TASK_STATUSES_WATERFALL = {"not_started", "in_progress", "blocked", "complete"}
_TASK_STATUSES_WT = {"scoped", "in_progress", "blocked", "shipped", "carried_forward"}
_SPRINT_STATUSES = {"planned", "active", "closed"}
_WAVE_STATUSES = {"planning", "executing", "postmortem", "closed"}
_THREAD_STATUSES = {"scoped", "in_progress", "shipped", "blocked"}
_BLOCKER_STATUSES = {"open", "acknowledged", "resolved", "escalated"}
_PRIORITIES = {"low", "medium", "high", "critical"}


def _filter_statuses(statuses: list, allowed: set) -> list:
    return [s.lower() for s in statuses if isinstance(s, str) and s.lower() in allowed]


def build_pmo_filters(query: dict, user) -> dict:
    """Return {collection_name: filter_dict} across the 7 PMO collections.

    Combines the user's natural-language filters (extracted by the LLM
    query builder) with the RBAC scope returned by rbac.<entity>_filter.
    Mirrors build_deal_filters / build_opportunity_filters semantics:
        - name_searches → $regex i across name-ish fields
        - date_start/date_end → $gte/$lt against created_at OR target_end_date
        - statuses → $in against the matching enum (per-entity)
        - project_types → $in against project_type (methodology)
        - priorities → $in against priority (tasks only)

    The "scope to me" behavior for tasks is auto-enabled when the user
    asks about "my tasks" — we detect this via a `scope_to_user` flag the
    query builder may set, and via the presence of the user's name in
    name_searches matching an obvious self-reference. Otherwise the base
    perm gate alone is enforced.
    """
    names = query.get("name_searches", [])
    date_start = query.get("date_start")
    date_end = query.get("date_end")
    statuses = query.get("statuses", [])
    project_types = [p.lower() for p in query.get("project_types", []) if isinstance(p, str)]
    priorities = _filter_statuses(query.get("priorities", []), _PRIORITIES)
    scope_to_user = bool(query.get("scope_to_user"))

    # Date range: PM data uses two date semantics — record creation and
    # target completion. We OR them so a "due in May" question hits
    # target_end_date and a "created last month" question hits created_at.
    date_created = _date_range_condition(date_start, date_end, "created_at")
    date_target = _date_range_condition(date_start, date_end, "target_end_date")
    date_due = _date_range_condition(date_start, date_end, "due_date")

    # ---- pm_projects ----
    project_conds = (
        _name_regex_conditions(names, ["name", "description"])
        + (
            [{"$or": [date_created[0], date_target[0]]}]
            if date_created and date_target else date_created or date_target
        )
    )
    proj_statuses = _filter_statuses(statuses, _PROJECT_STATUSES)
    if proj_statuses:
        project_conds.append({"status": {"$in": proj_statuses}})
    if project_types:
        valid_types = [t for t in project_types if t in _PROJECT_TYPES]
        if valid_types:
            project_conds.append({"project_type": {"$in": valid_types}})
    proj_healths = [h for h in (s.lower() for s in statuses if isinstance(s, str)) if h in _HEALTHS]
    if proj_healths:
        project_conds.append({"health": {"$in": proj_healths}})

    # ---- pm_tasks ----
    task_conds = (
        _name_regex_conditions(names, ["title", "description"])
        + (
            [{"$or": [date_created[0], date_due[0]]}]
            if date_created and date_due else date_created or date_due
        )
    )
    task_statuses = _filter_statuses(statuses, _TASK_STATUSES_WATERFALL | _TASK_STATUSES_WT)
    if task_statuses:
        task_conds.append({"status": {"$in": task_statuses}})
    if priorities:
        task_conds.append({"priority": {"$in": priorities}})
    if project_types:
        # task project_type is denormalized into methodology_fields on some
        # records; safer route is via project lookup, but a direct match on
        # methodology_fields.project_type catches the common case.
        valid_types = [t for t in project_types if t in _PROJECT_TYPES]
        if valid_types:
            task_conds.append({"methodology_fields.project_type": {"$in": valid_types}})

    # Scope-to-me defensive layer — assignee match. Project-membership
    # scope is left out because it would require a synchronous lookup of
    # the user's projects, which build_pmo_filters can't do (sync). The
    # base task_filter() perm gate still controls baseline visibility.
    if scope_to_user and getattr(user, "user_id", None):
        task_conds.append({"assignee_id": user.user_id})

    # ---- pm_sprints ----
    sprint_conds = (
        _name_regex_conditions(names, ["name", "goal"])
        + _date_range_condition(date_start, date_end, "start_date")
    )
    sp_statuses = _filter_statuses(statuses, _SPRINT_STATUSES)
    if sp_statuses:
        sprint_conds.append({"status": {"$in": sp_statuses}})

    # ---- pm_waves ----
    wave_conds = (
        _name_regex_conditions(names, ["goal"])
        + _date_range_condition(date_start, date_end, "start_date")
    )
    wv_statuses = _filter_statuses(statuses, _WAVE_STATUSES)
    if wv_statuses:
        wave_conds.append({"status": {"$in": wv_statuses}})

    # ---- pm_threads ----
    thread_conds = _name_regex_conditions(names, ["module"])
    th_statuses = _filter_statuses(statuses, _THREAD_STATUSES)
    if th_statuses:
        thread_conds.append({"status": {"$in": th_statuses}})

    # ---- pm_blocker_tickets ----
    blocker_conds = (
        _name_regex_conditions(names, ["description", "target_module"])
        + date_created
    )
    bl_statuses = _filter_statuses(statuses, _BLOCKER_STATUSES)
    if bl_statuses:
        blocker_conds.append({"status": {"$in": bl_statuses}})

    # ---- pm_standups ----
    standup_conds = (
        _name_regex_conditions(names, ["summary", "raw_content"])
        + _date_range_condition(date_start, date_end, "date")
    )

    # NOTE: project entity's collection is `projects` (not `pm_projects`)
    # per the canonical mapping file `.ingestion/mappings/projects.json`.
    # All other entities are pm_* prefixed.
    return {
        "projects": combine_filters(project_filter(user), *project_conds),
        "pm_tasks": combine_filters(task_filter(user), *task_conds),
        "pm_sprints": combine_filters(sprint_filter(user), *sprint_conds),
        "pm_waves": combine_filters(wave_filter(user), *wave_conds),
        "pm_threads": combine_filters(thread_filter(user), *thread_conds),
        "pm_blocker_tickets": combine_filters(blocker_filter(user), *blocker_conds),
        "pm_standups": combine_filters(standup_filter(user), *standup_conds),
    }
