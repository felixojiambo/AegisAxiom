"""Tests for field-service job margin computation.

Covers:
  - single-job margin (revenue from time entries x sell_rate, cost from
    stored labor_cost + material_cost)
  - cost-source fallback to time_entries x cost_rate when stored cost is 0
  - low-margin / loss-first ranking and the min_margin_percent filter
  - missing cost data => partial_result + missing_fields=['direct_cost']
  - missing revenue data => partial_result + missing_fields=['revenue']
  - permission/scoping: dispatcher rejects bogus job_id and silently
    ignores LLM-supplied tenant_id / account_id keys

The dispatcher branch is exercised end-to-end so coverage tracks how
Nova actually calls the tool.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ai.agents.tools.nova_tools import (
    NOVA_TOOL_SCHEMAS,
    _ScenarioState,
    execute_tool,
)


# ---------------------------------------------------------------------------
# Mock app db: field_jobs.find, projects.find, time_entries.find
# ---------------------------------------------------------------------------


def _make_app_db(*, field_jobs=None, projects=None, time_entries=None):
    """Build a MagicMock app db with the three collections fs_job_margin
    reads. find() honors the join keys the helper uses (field_job_id,
    project_id, status) so test fixtures only have to seed the rows
    that matter.
    """
    field_jobs = field_jobs or []
    projects = projects or []
    time_entries = time_entries or []

    def _flat_cursor(rows):
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=list(rows))
        return cursor

    db = MagicMock()

    def _field_jobs_find(query, projection):
        rows = list(field_jobs)
        if isinstance(query, dict):
            if "job_id" in query:
                rows = [j for j in rows if j.get("job_id") == query["job_id"]]
            if "status" in query:
                rows = [j for j in rows if j.get("status") == query["status"]]
        return _flat_cursor(rows)

    def _field_jobs_find_one(query, projection):
        cursor_rows = _field_jobs_find(query, projection)
        rows = cursor_rows.to_list.return_value
        return AsyncMock(return_value=rows[0] if rows else None)()

    db.field_jobs.find = MagicMock(side_effect=_field_jobs_find)
    db.field_jobs.find_one = MagicMock(side_effect=_field_jobs_find_one)

    def _projects_find(query, projection):
        ids = (query.get("field_job_id") or {}).get("$in", [])
        rows = [p for p in projects if p.get("field_job_id") in ids]
        return _flat_cursor(rows)

    db.projects.find = MagicMock(side_effect=_projects_find)

    def _time_entries_find(query, projection):
        proj_ids = (query.get("project_id") or {}).get("$in", [])
        statuses = (query.get("status") or {}).get("$in", [])
        rows = [
            e for e in time_entries
            if e.get("project_id") in proj_ids
            and (not statuses or e.get("status") in statuses)
        ]
        return _flat_cursor(rows)

    db.time_entries.find = MagicMock(side_effect=_time_entries_find)
    return db


def _patch_app_db(db):
    return patch(
        "services.nova_engine.fs_job_margin.get_app_db",
        return_value=db,
    )


# ---------------------------------------------------------------------------
# Schema sanity
# ---------------------------------------------------------------------------


def test_fs_job_margin_schema_advertised():
    schema = next(
        t for t in NOVA_TOOL_SCHEMAS if t["name"] == "analyze_fs_job_margin"
    )
    props = schema["parameters"]["properties"]
    assert "job_id" in props
    assert "status" in props
    assert "sort" in props
    assert "min_margin_percent" in props
    assert set(props["sort"]["enum"]) == {
        "loss_first", "margin_asc", "margin_desc", "revenue_desc",
    }


# ---------------------------------------------------------------------------
# Single-job margin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_job_margin_uses_stored_cost_and_time_entry_revenue():
    """Stored labor_cost + material_cost = $400. Time entries: 8h @ $100
    sell_rate = $800 revenue. Margin = ($800-$400)/$800 = 50%."""
    db = _make_app_db(
        field_jobs=[
            {"job_id": "job_a", "name": "Wayne Cable Pull",
             "status": "completed", "account_id": "acc_w",
             "account_name": "Wayne",
             "labor_cost": 300.0, "material_cost": 100.0},
        ],
        projects=[
            {"project_id": "proj_a", "field_job_id": "job_a"},
        ],
        time_entries=[
            {"entry_id": "te1", "project_id": "proj_a",
             "status": "approved", "hours": 8.0,
             "cost_rate": 50.0, "sell_rate": 100.0},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_fs_job_margin",
            {"job_id": "job_a"},
            state, "user_x", None, None,
        )

    assert "error" not in result
    assert result["job_id"] == "job_a"
    assert result["revenue"] == 800.0
    assert result["direct_cost"] == 400.0
    assert result["gross_profit"] == 400.0
    assert result["margin_percent"] == 50.0
    assert result["cost_source"] == "field_job_record"
    assert result["revenue_source"] == "time_entries_sell_rate"
    assert result["partial_result"] is False
    assert result["missing_fields"] == []


@pytest.mark.asyncio
async def test_single_job_falls_back_to_time_entry_cost_when_stored_zero():
    """labor_cost=0 + material_cost=0 means the stored field is unset.
    Fall back to time_entries x cost_rate ($50 x 8h = $400)."""
    db = _make_app_db(
        field_jobs=[
            {"job_id": "job_b", "name": "LexCorp install",
             "status": "completed", "labor_cost": 0.0, "material_cost": 0.0},
        ],
        projects=[
            {"project_id": "proj_b", "field_job_id": "job_b"},
        ],
        time_entries=[
            {"entry_id": "te1", "project_id": "proj_b",
             "status": "approved", "hours": 8.0,
             "cost_rate": 50.0, "sell_rate": 100.0},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_fs_job_margin",
            {"job_id": "job_b"},
            state, "user_x", None, None,
        )

    assert result["cost_source"] == "time_entries_cost_rate"
    assert result["direct_cost"] == 400.0
    assert result["margin_percent"] == 50.0
    assert result["partial_result"] is False


@pytest.mark.asyncio
async def test_single_job_returns_job_not_found_for_missing_id():
    db = _make_app_db(field_jobs=[])
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_fs_job_margin",
            {"job_id": "job_missing"},
            state, "user_x", None, None,
        )
    assert result.get("error") == "job_not_found"


# ---------------------------------------------------------------------------
# Missing revenue data => partial_result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_job_missing_revenue_returns_partial_result():
    """Job has stored cost but no time entries (no revenue source).
    Result must flag partial_result with missing_fields=['revenue']."""
    db = _make_app_db(
        field_jobs=[
            {"job_id": "job_c", "name": "no-time job",
             "labor_cost": 200.0, "material_cost": 50.0},
        ],
        projects=[],   # no project link, no time entries
        time_entries=[],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_fs_job_margin",
            {"job_id": "job_c"},
            state, "user_x", None, None,
        )

    assert result["partial_result"] is True
    assert "revenue" in result["missing_fields"]
    assert result["revenue"] == 0.0
    assert result["margin_percent"] is None
    assert result["direct_cost"] == 250.0


@pytest.mark.asyncio
async def test_single_job_missing_sell_rate_flagged_in_partial_result():
    """Time entries exist but sell_rate is None - we won't invent a
    rate, so revenue stays 0 and the entries-with-missing-rate count
    surfaces in the response."""
    db = _make_app_db(
        field_jobs=[
            {"job_id": "job_d", "name": "no-rate job",
             "labor_cost": 100.0, "material_cost": 0.0},
        ],
        projects=[
            {"project_id": "proj_d", "field_job_id": "job_d"},
        ],
        time_entries=[
            {"entry_id": "te1", "project_id": "proj_d",
             "status": "approved", "hours": 4.0,
             "cost_rate": 25.0, "sell_rate": None},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_fs_job_margin",
            {"job_id": "job_d"},
            state, "user_x", None, None,
        )

    assert result["partial_result"] is True
    assert "revenue" in result["missing_fields"]
    assert result["entries_with_missing_sell_rate"] == 1


# ---------------------------------------------------------------------------
# Missing cost data => partial_result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_job_missing_cost_returns_partial_result():
    """No stored labor/material cost AND time entries lack cost_rate.
    Cost cannot be computed - partial_result + missing_fields=
    ['direct_cost']. Revenue still computed from sell_rate."""
    db = _make_app_db(
        field_jobs=[
            {"job_id": "job_e", "name": "rate-less job",
             "labor_cost": 0.0, "material_cost": 0.0},
        ],
        projects=[
            {"project_id": "proj_e", "field_job_id": "job_e"},
        ],
        time_entries=[
            {"entry_id": "te1", "project_id": "proj_e",
             "status": "approved", "hours": 4.0,
             "cost_rate": None, "sell_rate": 100.0},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_fs_job_margin",
            {"job_id": "job_e"},
            state, "user_x", None, None,
        )

    assert result["partial_result"] is True
    assert "direct_cost" in result["missing_fields"]
    assert result["direct_cost"] == 0.0
    assert result["revenue"] == 400.0
    assert result["entries_with_missing_cost_rate"] == 1


# ---------------------------------------------------------------------------
# Job list ranking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_loss_first_surfaces_negative_margin_jobs():
    """Three completed jobs: A=+50%, B=-25%, C=+10%. loss_first must
    rank B first."""
    db = _make_app_db(
        field_jobs=[
            {"job_id": "job_a", "name": "A", "status": "completed",
             "labor_cost": 400.0, "material_cost": 0.0},
            {"job_id": "job_b", "name": "B", "status": "completed",
             "labor_cost": 1250.0, "material_cost": 0.0},
            {"job_id": "job_c", "name": "C", "status": "completed",
             "labor_cost": 900.0, "material_cost": 0.0},
        ],
        projects=[
            {"project_id": "proj_a", "field_job_id": "job_a"},
            {"project_id": "proj_b", "field_job_id": "job_b"},
            {"project_id": "proj_c", "field_job_id": "job_c"},
        ],
        time_entries=[
            {"entry_id": "te1", "project_id": "proj_a",
             "status": "approved", "hours": 8.0,
             "cost_rate": 50.0, "sell_rate": 100.0},  # rev 800, margin 50
            {"entry_id": "te2", "project_id": "proj_b",
             "status": "approved", "hours": 10.0,
             "cost_rate": 50.0, "sell_rate": 100.0},  # rev 1000, cost 1250 -> -25%
            {"entry_id": "te3", "project_id": "proj_c",
             "status": "approved", "hours": 10.0,
             "cost_rate": 50.0, "sell_rate": 100.0},  # rev 1000, cost 900, margin 10
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_fs_job_margin",
            {"sort": "loss_first"},
            state, "user_x", None, None,
        )

    assert result["row_count"] == 3
    keys = [r["job_id"] for r in result["rows"]]
    assert keys[0] == "job_b"  # most negative margin
    assert keys[1] == "job_c"
    assert keys[2] == "job_a"


@pytest.mark.asyncio
async def test_loss_first_is_alias_for_margin_asc_on_mixed_signs():
    """PR #144 second-pass review item 1: loss_first and margin_asc
    were producing identical sort tuples and the bucketed alternative
    the reviewer suggested ((0 if m<0 else 1, m)) collapses to the same
    ordering because every negative margin is less than every
    non-negative margin. This test locks in the deliberate alias so a
    future refactor that tries to make them diverge has to update this
    assertion and explain why."""
    db = _make_app_db(
        field_jobs=[
            # Mixed signs to maximise the chance any two distinct sort
            # algorithms diverge: -5%, +50%, -40%, +10%.
            {"job_id": "j1", "name": "small_loss", "status": "completed",
             "labor_cost": 105.0, "material_cost": 0.0},
            {"job_id": "j2", "name": "big_win", "status": "completed",
             "labor_cost": 50.0, "material_cost": 0.0},
            {"job_id": "j3", "name": "big_loss", "status": "completed",
             "labor_cost": 140.0, "material_cost": 0.0},
            {"job_id": "j4", "name": "small_win", "status": "completed",
             "labor_cost": 90.0, "material_cost": 0.0},
        ],
        projects=[
            {"project_id": "p1", "field_job_id": "j1"},
            {"project_id": "p2", "field_job_id": "j2"},
            {"project_id": "p3", "field_job_id": "j3"},
            {"project_id": "p4", "field_job_id": "j4"},
        ],
        time_entries=[
            {"entry_id": f"te{i}", "project_id": pid,
             "status": "approved", "hours": 1.0,
             "cost_rate": 50.0, "sell_rate": 100.0}
            for i, pid in enumerate(["p1", "p2", "p3", "p4"], 1)
        ],
    )
    # Run both sorts back to back; assert the row order is byte-equivalent.
    state = _ScenarioState()
    with _patch_app_db(db):
        loss_first_result = await execute_tool(
            "analyze_fs_job_margin",
            {"sort": "loss_first"},
            state, "user_x", None, None,
        )
        margin_asc_result = await execute_tool(
            "analyze_fs_job_margin",
            {"sort": "margin_asc"},
            state, "user_x", None, None,
        )
    assert (
        [r["job_id"] for r in loss_first_result["rows"]]
        == [r["job_id"] for r in margin_asc_result["rows"]]
    )


@pytest.mark.asyncio
async def test_list_min_margin_percent_filters_low_margin_only():
    """min_margin_percent=20 returns only jobs with margin BELOW 20%
    (the 'low margin' filter). Jobs with no revenue are excluded -
    we cannot compare 'no margin' to a numeric threshold."""
    db = _make_app_db(
        field_jobs=[
            {"job_id": "job_a", "name": "A", "status": "completed",
             "labor_cost": 400.0, "material_cost": 0.0},  # 50% margin
            {"job_id": "job_b", "name": "B", "status": "completed",
             "labor_cost": 1250.0, "material_cost": 0.0},  # -25% margin
            {"job_id": "job_c", "name": "C", "status": "completed",
             "labor_cost": 900.0, "material_cost": 0.0},  # +10% margin
            {"job_id": "job_d", "name": "no-rev",
             "status": "completed",
             "labor_cost": 100.0, "material_cost": 0.0},  # no revenue
        ],
        projects=[
            {"project_id": "proj_a", "field_job_id": "job_a"},
            {"project_id": "proj_b", "field_job_id": "job_b"},
            {"project_id": "proj_c", "field_job_id": "job_c"},
        ],
        time_entries=[
            {"entry_id": "te1", "project_id": "proj_a",
             "status": "approved", "hours": 8.0,
             "cost_rate": 50.0, "sell_rate": 100.0},
            {"entry_id": "te2", "project_id": "proj_b",
             "status": "approved", "hours": 10.0,
             "cost_rate": 50.0, "sell_rate": 100.0},
            {"entry_id": "te3", "project_id": "proj_c",
             "status": "approved", "hours": 10.0,
             "cost_rate": 50.0, "sell_rate": 100.0},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_fs_job_margin",
            {"sort": "loss_first", "min_margin_percent": 20.0},
            state, "user_x", None, None,
        )

    keys = sorted(r["job_id"] for r in result["rows"])
    # job_b (-25%) and job_c (+10%) are both below 20%; job_a (50%)
    # is above; job_d has no margin so it's excluded.
    assert keys == ["job_b", "job_c"]


@pytest.mark.asyncio
async def test_list_status_filter_narrows_to_completed_only():
    """status filter must narrow the FieldJob query so only matching
    rows feed into the ranking."""
    db = _make_app_db(
        field_jobs=[
            {"job_id": "job_a", "name": "completed",
             "status": "completed", "labor_cost": 100.0, "material_cost": 0},
            {"job_id": "job_b", "name": "scheduled",
             "status": "scheduled", "labor_cost": 100.0, "material_cost": 0},
        ],
        projects=[
            {"project_id": "proj_a", "field_job_id": "job_a"},
            {"project_id": "proj_b", "field_job_id": "job_b"},
        ],
        time_entries=[
            {"entry_id": "te1", "project_id": "proj_a",
             "status": "approved", "hours": 1.0,
             "cost_rate": 50.0, "sell_rate": 100.0},
            {"entry_id": "te2", "project_id": "proj_b",
             "status": "approved", "hours": 1.0,
             "cost_rate": 50.0, "sell_rate": 100.0},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_fs_job_margin",
            {"status": "completed"},
            state, "user_x", None, None,
        )
    assert result["row_count"] == 1
    assert result["rows"][0]["job_id"] == "job_a"


@pytest.mark.asyncio
async def test_list_only_includes_approved_time_entries():
    """Draft / submitted time entries must NOT contribute to the
    margin - their rates can change before approval. Mirrors
    finance.py's AP/AR logic."""
    db = _make_app_db(
        field_jobs=[
            {"job_id": "job_a", "name": "A", "status": "completed",
             "labor_cost": 0.0, "material_cost": 0.0},
        ],
        projects=[
            {"project_id": "proj_a", "field_job_id": "job_a"},
        ],
        time_entries=[
            {"entry_id": "te_approved", "project_id": "proj_a",
             "status": "approved", "hours": 2.0,
             "cost_rate": 50.0, "sell_rate": 100.0},  # counted
            {"entry_id": "te_draft", "project_id": "proj_a",
             "status": "draft", "hours": 100.0,
             "cost_rate": 50.0, "sell_rate": 100.0},  # ignored
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_fs_job_margin",
            {"job_id": "job_a"},
            state, "user_x", None, None,
        )
    # Only the approved 2h x $100 = $200 should count.
    assert result["revenue"] == 200.0
    assert result["billable_hours"] == 2.0


# ---------------------------------------------------------------------------
# Validation / scoping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_job_id_returns_structured_error():
    db = _make_app_db()
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_fs_job_margin",
            {"job_id": ""},
            state, "user_x", None, None,
        )
    assert result.get("error") == "invalid_job_id"


@pytest.mark.asyncio
async def test_invalid_sort_returns_structured_error():
    db = _make_app_db()
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_fs_job_margin",
            {"sort": "by_color"},
            state, "user_x", None, None,
        )
    assert result.get("error") == "invalid_sort"


def test_fs_job_statuses_derives_from_models_canonical_list():
    """The _FS_JOB_STATUSES validator set must equal the canonical
    FIELD_JOB_STATUSES list in models.py. Locks in the derivation:
    if anyone re-introduces a hand-synced copy, this test catches the
    drift on the next run."""
    from models import FIELD_JOB_STATUSES
    from services.ai.agents.tools.nova_tools import _FS_JOB_STATUSES

    assert _FS_JOB_STATUSES == set(FIELD_JOB_STATUSES)


@pytest.mark.asyncio
async def test_unknown_status_returns_structured_error_with_valid_list():
    """PR #144 second-pass review item 8: a typo / LLM-fabricated status
    value used to silently match zero rows. Now it returns a structured
    unknown_status error with the canonical FieldJob enum so the model
    can self-correct."""
    db = _make_app_db()
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_fs_job_margin",
            {"status": "kompleted"},  # typo
            state, "user_x", None, None,
        )
    assert result.get("error") == "unknown_status"
    assert "kompleted" in result.get("detail", "")
    valid = set(result.get("valid", []))
    assert valid == {
        "scheduled", "confirmed", "in_progress", "completed", "cancelled",
    }


@pytest.mark.asyncio
async def test_dispatcher_ignores_caller_supplied_tenant_overrides():
    """The chat surface enforces user_id via nova.use at the conductor.
    Bogus tenant_id / account_id args from the LLM must NOT widen
    scope - the dispatcher silently ignores them."""
    db = _make_app_db(
        field_jobs=[
            {"job_id": "job_a", "name": "A", "status": "completed",
             "labor_cost": 100.0, "material_cost": 0.0},
        ],
        projects=[
            {"project_id": "proj_a", "field_job_id": "job_a"},
        ],
        time_entries=[
            {"entry_id": "te1", "project_id": "proj_a",
             "status": "approved", "hours": 2.0,
             "cost_rate": 25.0, "sell_rate": 100.0},
        ],
    )
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_fs_job_margin",
            {
                "job_id": "job_a",
                # Bogus scoping fields - must be ignored, not honored.
                "tenant_id": "other-tenant",
                "account_id": "acc_should_be_ignored",
            },
            state, "user_x", None, None,
        )
    assert "error" not in result
    assert result["job_id"] == "job_a"


@pytest.mark.asyncio
async def test_limit_is_clamped_at_50():
    """Out-of-bounds limits clamp to [1, 50] without raising."""
    db = _make_app_db(field_jobs=[])
    state = _ScenarioState()
    with _patch_app_db(db):
        result = await execute_tool(
            "analyze_fs_job_margin",
            {"limit": 9999},
            state, "user_x", None, None,
        )
    assert "error" not in result
    assert result["row_count"] == 0
