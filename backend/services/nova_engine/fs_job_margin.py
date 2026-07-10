"""Field-service job margin computation for Nova.

Nova needs to answer "what was the margin on this FS job?", "show me
low-margin jobs", and "which jobs are losing money?". The data already
exists in AEGISAXIOM - it just was never aggregated into a per-job
profitability view.

Reliable signals (used here):

  Cost
    - FieldJob.labor_cost + FieldJob.material_cost  (canonical, stored
      on the job document at create/update time; see FieldJob model
      and routes/field_services.py:403/465).
    - Approved time_entries x cost_rate via the project link  -
      time_entries do NOT carry job_id directly. The route at
      field_services.py:1609 sets ``project.field_job_id = job_id``
      when a job's first check-out fires; we follow the same path
      backwards: field_job.job_id -> projects (field_job_id) ->
      time_entries.project_id. This is a fallback when the stored
      labor_cost is 0.

  Revenue
    - Approved time_entries x sell_rate via the same project link.
      Each time entry locks cost_rate and sell_rate from the contract
      at submission (see TimeEntry.cost_rate / sell_rate). Multiplying
      by entry.hours gives both AR billable and AP cost in one pass.

What we DELIBERATELY do not do:

  - Invent a labor rate. If a time entry has sell_rate = 0 / None we
    treat that entry as missing revenue, not as $0 revenue.
  - Pro-rate AR invoices across jobs. ar_invoices link to deal_id /
    contract_id, not job_id, and a deal can fan out to many jobs.
    Allocating an invoice across jobs would be a guess.
  - Allocate overhead. There are no existing overhead-allocation rules
    in AEGISAXIOM today. "Direct cost only" is the honest answer until those
    rules exist.

The result includes a ``partial_result`` flag plus a ``missing_fields``
list whenever a contributing source was unavailable, so Nova can be
explicit about what wasn't included instead of returning a misleading
single number.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Literal

from motor.motor_asyncio import AsyncIOMotorDatabase

from ._gl_helpers import _safe_float
from .db import get_app_db


logger = logging.getLogger(__name__)


_TIME_ENTRY_LIMIT = 5_000
_PROJECT_LIMIT = 1_000
_FIELD_JOB_LIMIT = 1_000

# Time entry statuses we count toward AR/AP. Mirrors finance.py's AP/AR
# routes which only sum approved entries - draft/submitted/rejected
# rows are too volatile to feed into a margin number Nova will quote.
_APPROVED_STATUSES = {"approved", "pm_approved", "admin_approved"}


SortOrder = Literal["loss_first", "margin_asc", "margin_desc", "revenue_desc"]


# ---------------------------------------------------------------------------
# Per-job aggregation
# ---------------------------------------------------------------------------


async def _project_ids_for_jobs(
    db: AsyncIOMotorDatabase, job_ids: list[str],
) -> dict[str, list[str]]:
    """Return ``{job_id: [project_id, ...]}`` for every project linked
    back to a field job via the ``field_job_id`` column. Empty mapping
    when no projects exist - the caller treats those as no-time-entry
    jobs and degrades to job-document totals."""
    if not job_ids:
        return {}
    cursor = db.projects.find(
        {"field_job_id": {"$in": job_ids}},
        {"_id": 0, "project_id": 1, "field_job_id": 1},
    )
    docs = await cursor.to_list(length=_PROJECT_LIMIT)
    by_job: dict[str, list[str]] = {}
    for d in docs:
        jid = d.get("field_job_id")
        pid = d.get("project_id")
        if jid and pid:
            by_job.setdefault(jid, []).append(pid)
    return by_job


async def _time_entries_for_projects(
    db: AsyncIOMotorDatabase, project_ids: list[str],
) -> list[dict]:
    """Approved time entries against the supplied project ids."""
    if not project_ids:
        return []
    cursor = db.time_entries.find(
        {
            "project_id": {"$in": project_ids},
            "status": {"$in": list(_APPROVED_STATUSES)},
        },
        {"_id": 0},
    )
    entries = await cursor.to_list(length=_TIME_ENTRY_LIMIT)
    if len(entries) >= _TIME_ENTRY_LIMIT:
        logger.warning(
            "fs_job_margin time_entries fetch hit cap of %d; result may be partial.",
            _TIME_ENTRY_LIMIT,
        )
    return entries


def _aggregate_time_entries(entries: Iterable[dict]) -> dict[str, Any]:
    """Sum revenue/cost from a collection of approved time entries.

    Returns four pieces:
        revenue, cost, billable_hours, entries_with_missing_rate

    A time entry that has no sell_rate is counted toward billable_hours
    (so it's visible) but contributes 0 revenue and is reported under
    ``entries_with_missing_rate`` so the caller can flag the partial
    result. Same treatment for missing cost_rate."""
    revenue = 0.0
    cost = 0.0
    hours = 0.0
    missing_sell_rate = 0
    missing_cost_rate = 0
    for entry in entries:
        h = _safe_float(entry.get("hours"))
        sell = entry.get("sell_rate")
        cost_rate = entry.get("cost_rate")
        hours += h
        if sell is None or _safe_float(sell) <= 0:
            missing_sell_rate += 1
        else:
            revenue += h * _safe_float(sell)
        if cost_rate is None or _safe_float(cost_rate) <= 0:
            missing_cost_rate += 1
        else:
            cost += h * _safe_float(cost_rate)
    return {
        "revenue": revenue,
        "cost": cost,
        "billable_hours": hours,
        "entries_with_missing_sell_rate": missing_sell_rate,
        "entries_with_missing_cost_rate": missing_cost_rate,
    }


def _compute_margin_row(
    job: dict,
    entries: list[dict],
) -> dict[str, Any]:
    """Build the margin row for a single field job.

    Cost preference: stored job.labor_cost + job.material_cost when
    non-zero, otherwise time-entry cost. Revenue source: time entries
    only - the FieldJob model does not carry a billable_amount field
    and we won't invent one.
    """
    job_labor = _safe_float(job.get("labor_cost"))
    job_material = _safe_float(job.get("material_cost"))
    stored_cost = job_labor + job_material

    agg = _aggregate_time_entries(entries)
    te_revenue = agg["revenue"]
    te_cost = agg["cost"]

    if stored_cost > 0:
        direct_cost = stored_cost
        cost_source = "field_job_record"
    elif te_cost > 0:
        direct_cost = te_cost
        cost_source = "time_entries_cost_rate"
    else:
        direct_cost = 0.0
        cost_source = "none"

    revenue = te_revenue
    revenue_source = "time_entries_sell_rate" if revenue > 0 else "none"

    missing_fields: list[str] = []
    if revenue_source == "none":
        missing_fields.append("revenue")
    if cost_source == "none":
        missing_fields.append("direct_cost")

    gross_profit = revenue - direct_cost
    margin_percent = (
        round((gross_profit / revenue) * 100.0, 2) if revenue > 0 else None
    )

    return {
        "job_id": job.get("job_id"),
        "name": job.get("name"),
        "status": job.get("status"),
        "account_id": job.get("account_id"),
        "account_name": job.get("account_name"),
        "deal_id": job.get("deal_id"),
        "deal_name": job.get("deal_name"),
        "scheduled_date": job.get("scheduled_date") or job.get("job_date"),
        "revenue": round(revenue, 2),
        "direct_cost": round(direct_cost, 2),
        "gross_profit": round(gross_profit, 2),
        "margin_percent": margin_percent,
        "revenue_source": revenue_source,
        "cost_source": cost_source,
        "billable_hours": round(agg["billable_hours"], 2),
        "time_entry_count": len(entries),
        "stored_labor_cost": round(job_labor, 2),
        "stored_material_cost": round(job_material, 2),
        "time_entry_cost": round(te_cost, 2),
        "missing_fields": missing_fields,
        "partial_result": bool(missing_fields)
        or agg["entries_with_missing_sell_rate"] > 0
        or agg["entries_with_missing_cost_rate"] > 0,
        "entries_with_missing_sell_rate": agg["entries_with_missing_sell_rate"],
        "entries_with_missing_cost_rate": agg["entries_with_missing_cost_rate"],
    }


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------


async def compute_fs_job_margin(
    *,
    job_id: str,
    db: AsyncIOMotorDatabase | None = None,
) -> dict[str, Any]:
    """Compute the margin for a single field-service job.

    Returns ``{"error": "job_not_found", ...}`` when no field_jobs row
    matches. Otherwise returns the margin row (including ``missing_fields``
    when revenue or cost data is incomplete).
    """
    if db is None:
        db = get_app_db()

    job = await db.field_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if job is None:
        return {
            "error": "job_not_found",
            "detail": f"No field_jobs document with job_id={job_id!r}.",
            "job_id": job_id,
        }

    by_job = await _project_ids_for_jobs(db, [job_id])
    project_ids = by_job.get(job_id, [])
    entries = await _time_entries_for_projects(db, project_ids)
    return _compute_margin_row(job, entries)


async def list_fs_job_margins(
    *,
    status: str | None = None,
    sort: SortOrder = "loss_first",
    limit: int = 10,
    min_margin_percent: float | None = None,
    db: AsyncIOMotorDatabase | None = None,
) -> dict[str, Any]:
    """Rank field-service jobs by margin.

    Filters:
      - status: optional FieldJob.status filter (e.g. 'completed').
      - min_margin_percent: when set, returns jobs whose margin_percent
        is BELOW this threshold (the 'low margin / losing money' view).
        Jobs with no revenue (margin_percent None) are excluded from
        this filter - we cannot compare 'no margin' to a numeric
        threshold without inventing a value.

    Sort modes:
      - loss_first / margin_asc: lowest margin first. These are aliases
        for the same ordering. The "loss-first" framing is mathematically
        equivalent to ascending sort because every negative margin is
        less than every non-negative margin, so an ascending sort already
        groups losers ahead of profitable jobs. PR #144 second-pass
        review surfaced the duplicate and the bucketed alternative the
        reviewer suggested ((0 if m<0 else 1, m)) collapses to the same
        ordering for the same reason. Kept as a friendlier name in the
        schema and prompt; locked in by a regression test that asserts
        both produce identical rows on a mixed-sign fixture.
      - margin_desc: numeric sort over margin_percent, descending.
      - revenue_desc: largest jobs first by revenue.

    Jobs whose margin_percent is None (no revenue, undefined ratio) are
    pushed to the end regardless of sort mode.
    """
    if db is None:
        db = get_app_db()

    job_filter: dict[str, Any] = {}
    if status is not None:
        job_filter["status"] = status

    cursor = db.field_jobs.find(job_filter, {"_id": 0})
    jobs = await cursor.to_list(length=_FIELD_JOB_LIMIT)
    if len(jobs) >= _FIELD_JOB_LIMIT:
        logger.warning(
            "fs_job_margin field_jobs fetch hit cap of %d; result may be partial.",
            _FIELD_JOB_LIMIT,
        )

    job_ids = [j.get("job_id") for j in jobs if j.get("job_id")]
    by_job_projects = await _project_ids_for_jobs(db, job_ids)
    all_project_ids = [pid for pids in by_job_projects.values() for pid in pids]
    entries = await _time_entries_for_projects(db, all_project_ids)
    entries_by_project: dict[str, list[dict]] = {}
    for e in entries:
        pid = e.get("project_id")
        if pid:
            entries_by_project.setdefault(pid, []).append(e)

    rows: list[dict[str, Any]] = []
    for job in jobs:
        jid = job.get("job_id")
        project_ids = by_job_projects.get(jid, [])
        job_entries: list[dict] = []
        for pid in project_ids:
            job_entries.extend(entries_by_project.get(pid, []))
        rows.append(_compute_margin_row(job, job_entries))

    if min_margin_percent is not None:
        rows = [
            r for r in rows
            if r.get("margin_percent") is not None
            and r["margin_percent"] < min_margin_percent
        ]

    def _sort_key(r: dict) -> tuple:
        m = r.get("margin_percent")
        # loss_first is a deliberate alias for margin_asc - see the
        # docstring above for why the bucketed alternative collapses.
        # Jobs with margin_percent=None always sort to the end via the
        # leading 0/1 component of the tuple.
        if sort in ("loss_first", "margin_asc"):
            return (1 if m is None else 0, m if m is not None else 0)
        if sort == "margin_desc":
            return (1 if m is None else 0, -(m if m is not None else 0))
        if sort == "revenue_desc":
            return (-_safe_float(r.get("revenue", 0)),)
        return (0,)

    rows.sort(key=_sort_key)
    truncated_total = len(rows)
    rows = rows[:limit]

    return {
        "rows": rows,
        "row_count": len(rows),
        "total_matched": truncated_total,
        "filters": {
            "status": status,
            "min_margin_percent": min_margin_percent,
        },
        "sort": sort,
    }


__all__ = [
    "compute_fs_job_margin",
    "list_fs_job_margins",
]
