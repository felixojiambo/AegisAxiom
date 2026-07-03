"""Unit tests for `services.projects_template_engine`.

The engine is intentionally pure (no Mongo, no FastAPI), so every test here
exercises the dict-in / dict-out shape directly. Route-level integration
sits in test_projects_template_routes.py.

Coverage:
    - snapshot strips assignees, ids, audit metadata
    - snapshot converts absolute dates to day offsets when include_dates=False
    - snapshot preserves dates when include_dates=True (alongside offsets)
    - snapshot dependency rewiring uses synthetic template_task_id
    - snapshot covers all three methodology types
    - hydrate computes dates correctly from offsets
    - hydrate honors include_dates absolute dates over offsets
    - hydrate weekend-skip rolls Sat/Sun forward to Monday
    - hydrate honors overrides (skip, title prefix, assignee mapping)
    - hydrate rewires dependency references back through template_task_id
    - preview caps long task lists + emits warning
    - preview computes estimated_duration_days from max offset
    - methodology seeding wires phases/sprints/waves on each project_type
"""
from __future__ import annotations

import pytest

from services.projects_template_engine import (
    PREVIEW_TASK_CAP,
    SUPPORTED_METHODOLOGIES,
    build_preview,
    hydrate_template,
    snapshot_project,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _waterfall_project():
    return {
        "project_id": "proj_w1",
        "name": "Waterfall Project",
        "project_type": "waterfall",
        "service_line": "ucaas",
        "start_date": "2026-06-01",
        "status": "active",
        "pm_id": "user_grace",
        "assigned_user_ids": ["user_grace", "user_henry"],
        "methodology_config": {
            "phases": [
                {"name": "Discovery", "start_date": "2026-06-01", "end_date": "2026-06-10"},
                {"name": "Build", "start_date": "2026-06-11", "end_date": "2026-06-25"},
            ]
        },
        "created_by": "user_grace",
    }


def _waterfall_tasks():
    return [
        {
            "task_id": "task_001",
            "project_id": "proj_w1",
            "title": "Kickoff",
            "assignee_id": "user_grace",
            "status": "in_progress",
            "priority": "high",
            "start_date": "2026-06-01",
            "due_date": "2026-06-03",
            "dependencies": [],
            "methodology_fields": {"phase": "Discovery", "assignee_id": "user_grace"},
            "labels": ["important"],
            "task_number": 1,
            "created_at": "2026-06-01T00:00:00+00:00",
            "updated_at": "2026-06-01T00:00:00+00:00",
            "created_by": "user_grace",
        },
        {
            "task_id": "task_002",
            "project_id": "proj_w1",
            "title": "Build feature",
            "assignee_id": "user_henry",
            "status": "not_started",
            "priority": "medium",
            "start_date": "2026-06-04",
            "due_date": "2026-06-15",
            "dependencies": ["task_001"],
            "methodology_fields": {"phase": "Build"},
            "labels": [],
            "task_number": 2,
            "created_at": "2026-06-04T00:00:00+00:00",
            "updated_at": "2026-06-04T00:00:00+00:00",
            "created_by": "user_grace",
        },
    ]


def _agile_project():
    return {
        "project_id": "proj_a1",
        "name": "Agile Project",
        "project_type": "agile",
        "service_line": "ai_product",
        "start_date": "2026-06-01",
        "methodology_config": {
            "columns": [
                {"id": "to_do", "name": "To Do"},
                {"id": "in_progress", "name": "In Progress"},
                {"id": "done", "name": "Done"},
            ]
        },
    }


def _agile_sprints():
    return [
        {
            "sprint_id": "sprint_001",
            "project_id": "proj_a1",
            "name": "Sprint 1",
            "start_date": "2026-06-01",
            "end_date": "2026-06-14",
            "goal": "Land MVP",
            "capacity_points": 30,
            "status": "active",
        },
        {
            "sprint_id": "sprint_002",
            "project_id": "proj_a1",
            "name": "Sprint 2",
            "start_date": "2026-06-15",
            "end_date": "2026-06-28",
            "goal": "Polish",
            "capacity_points": 30,
            "status": "planned",
        },
    ]


def _waves_project():
    return {
        "project_id": "proj_wt1",
        "name": "Waves Project",
        "project_type": "waves_threads",
        "service_line": "cloud",
        "start_date": "2026-06-01",
        "methodology_config": {"wave_count_target": 2, "modules": ["general"]},
    }


def _waves():
    return [
        {
            "wave_id": "wave_001",
            "project_id": "proj_wt1",
            "goal": "Foundation",
            "start_date": "2026-06-01",
            "end_date": "2026-06-10",
            "depends_on_wave_ids": [],
        },
        {
            "wave_id": "wave_002",
            "project_id": "proj_wt1",
            "goal": "Build",
            "start_date": "2026-06-11",
            "end_date": "2026-06-20",
            "depends_on_wave_ids": ["wave_001"],
        },
    ]


# ---------------------------------------------------------------------------
# snapshot_project
# ---------------------------------------------------------------------------
class TestSnapshotProject:
    def test_strips_assignees_and_ids_and_audit_fields(self):
        snap = snapshot_project(_waterfall_project(), _waterfall_tasks())
        # No leaked user references at the project level.
        assert "pm_id" not in snap
        assert "assigned_user_ids" not in snap
        assert "created_by" not in snap
        # And no per-task assignee_id either.
        for td in snap["task_definitions"]:
            assert "assignee_id" not in td
            assert "task_id" not in td
            assert "task_number" not in td
            assert "created_by" not in td
            # methodology_fields.assignee_id is also stripped.
            assert "assignee_id" not in (td.get("methodology_fields") or {})

    def test_strips_dates_by_default_and_records_offsets(self):
        snap = snapshot_project(_waterfall_project(), _waterfall_tasks())
        td_kickoff = next(t for t in snap["task_definitions"] if t["title"] == "Kickoff")
        # Absolute dates are gone…
        assert "start_date" not in td_kickoff or td_kickoff.get("start_date") is None
        assert "due_date" not in td_kickoff or td_kickoff.get("due_date") is None
        # …but offsets relative to 2026-06-01 are present.
        assert td_kickoff["start_offset_days"] == 0
        assert td_kickoff["due_offset_days"] == 2  # 06-03 - 06-01

    def test_preserves_dates_when_include_dates_true(self):
        snap = snapshot_project(_waterfall_project(), _waterfall_tasks(), include_dates=True)
        td_kickoff = next(t for t in snap["task_definitions"] if t["title"] == "Kickoff")
        # Both representations are kept so hydration can choose.
        assert td_kickoff["start_date"] == "2026-06-01"
        assert td_kickoff["due_date"] == "2026-06-03"
        assert td_kickoff["start_offset_days"] == 0
        assert td_kickoff["due_offset_days"] == 2

    def test_dependencies_rewired_to_synthetic_ids(self):
        snap = snapshot_project(_waterfall_project(), _waterfall_tasks())
        # Build the lookup from title → template_task_id.
        by_title = {t["title"]: t["template_task_id"] for t in snap["task_definitions"]}
        td_build = next(t for t in snap["task_definitions"] if t["title"] == "Build feature")
        # task_001 was renamed to a ttask_ id.
        assert td_build["dependencies"] == [by_title["Kickoff"]]
        # And those ids are not the original AEGISAXIOM ids.
        for ttid in by_title.values():
            assert ttid.startswith("ttask_")

    def test_waterfall_seeds_phases_with_offsets(self):
        snap = snapshot_project(_waterfall_project(), _waterfall_tasks())
        phases = snap["default_phases"]
        assert len(phases) == 2
        assert phases[0]["name"] == "Discovery"
        assert phases[0]["start_offset_days"] == 0
        assert phases[0]["end_offset_days"] == 9  # 06-10 - 06-01
        assert phases[1]["name"] == "Build"
        assert phases[1]["start_offset_days"] == 10

    def test_agile_seeds_columns_and_sprints(self):
        snap = snapshot_project(
            _agile_project(), tasks=[], sprints=_agile_sprints()
        )
        assert snap["default_columns"] == [
            {"id": "to_do", "name": "To Do"},
            {"id": "in_progress", "name": "In Progress"},
            {"id": "done", "name": "Done"},
        ]
        sprints = snap["default_sprints"]
        assert len(sprints) == 2
        assert sprints[0]["name"] == "Sprint 1"
        assert sprints[0]["start_offset_days"] == 0
        assert sprints[0]["end_offset_days"] == 13  # 06-14 - 06-01
        assert sprints[0]["goal"] == "Land MVP"
        assert sprints[0]["capacity_points"] == 30

    def test_waves_seeds_with_depends_on_indexes(self):
        snap = snapshot_project(_waves_project(), tasks=[], waves=_waves())
        waves = snap["default_waves"]
        assert len(waves) == 2
        assert waves[0]["goal"] == "Foundation"
        assert waves[0]["start_offset_days"] == 0
        assert waves[1]["goal"] == "Build"
        # wave_002 depends on wave_001, which is at index 0.
        assert waves[1]["depends_on_indexes"] == [0]

    def test_version_is_one_and_is_system_false(self):
        snap = snapshot_project(_waterfall_project(), _waterfall_tasks())
        assert snap["version"] == 1
        assert snap["is_system"] is False

    def test_unsupported_project_type_raises(self):
        bad = {**_waterfall_project(), "project_type": "unsupported"}
        with pytest.raises(ValueError):
            snapshot_project(bad, [])


# ---------------------------------------------------------------------------
# hydrate_template
# ---------------------------------------------------------------------------
class TestHydrateTemplate:
    def _waterfall_template(self):
        return snapshot_project(_waterfall_project(), _waterfall_tasks())

    def test_offsets_resolve_to_absolute_dates(self):
        tpl = self._waterfall_template()
        bp = hydrate_template(
            tpl, start_date="2026-09-01", pm_user_id="user_pm", name="New Project"
        )
        kickoff = next(t for t in bp["tasks"] if t["title"] == "Kickoff")
        assert kickoff["start_date"] == "2026-09-01"
        assert kickoff["due_date"] == "2026-09-03"
        build_t = next(t for t in bp["tasks"] if t["title"] == "Build feature")
        # Original 06-04 → offset 3, instantiated 09-01 + 3 = 09-04.
        assert build_t["start_date"] == "2026-09-04"
        # Original 06-15 → offset 14, instantiated 09-01 + 14 = 09-15.
        assert build_t["due_date"] == "2026-09-15"

    def test_include_dates_template_keeps_absolutes(self):
        tpl = snapshot_project(_waterfall_project(), _waterfall_tasks(), include_dates=True)
        bp = hydrate_template(
            tpl, start_date="2026-09-01", pm_user_id="user_pm", name="X"
        )
        kickoff = next(t for t in bp["tasks"] if t["title"] == "Kickoff")
        # Template stored absolute dates → hydrate preserves them as-is.
        assert kickoff["start_date"] == "2026-06-01"
        assert kickoff["due_date"] == "2026-06-03"

    def test_weekend_skip_rolls_forward(self):
        tpl = self._waterfall_template()
        tpl["config"]["skip_weekends"] = True
        # 2026-09-05 is a Saturday. start + 0 = Sat → Mon (2026-09-07).
        bp = hydrate_template(
            tpl, start_date="2026-09-05", pm_user_id="user_pm", name="W"
        )
        kickoff = next(t for t in bp["tasks"] if t["title"] == "Kickoff")
        assert kickoff["start_date"] == "2026-09-07"

    def test_skip_task_overrides_omits_task(self):
        tpl = self._waterfall_template()
        kickoff_id = next(
            t["template_task_id"] for t in tpl["task_definitions"] if t["title"] == "Kickoff"
        )
        bp = hydrate_template(
            tpl,
            start_date="2026-09-01",
            pm_user_id="user_pm",
            name="X",
            overrides={"skip_task_ids": [kickoff_id]},
        )
        titles = [t["title"] for t in bp["tasks"]]
        assert "Kickoff" not in titles
        assert "Build feature" in titles

    def test_title_prefix_override_prepended(self):
        tpl = self._waterfall_template()
        bp = hydrate_template(
            tpl,
            start_date="2026-09-01",
            pm_user_id="user_pm",
            name="X",
            overrides={"task_title_prefix": "[Q3] "},
        )
        for t in bp["tasks"]:
            assert t["title"].startswith("[Q3] ")

    def test_assignee_mapping_applied(self):
        tpl = self._waterfall_template()
        kickoff_id = next(
            t["template_task_id"] for t in tpl["task_definitions"] if t["title"] == "Kickoff"
        )
        bp = hydrate_template(
            tpl,
            start_date="2026-09-01",
            pm_user_id="user_pm",
            name="X",
            overrides={"assignee_mapping": {kickoff_id: "user_alice"}},
        )
        kickoff = next(t for t in bp["tasks"] if t["title"] == "Kickoff")
        assert kickoff["assignee_id"] == "user_alice"
        build_t = next(t for t in bp["tasks"] if t["title"] == "Build feature")
        # Unmapped tasks have no assignee.
        assert build_t["assignee_id"] is None

    def test_dependencies_pass_through_template_ids_for_route_rewire(self):
        tpl = self._waterfall_template()
        bp = hydrate_template(
            tpl, start_date="2026-09-01", pm_user_id="user_pm", name="X"
        )
        # The engine returns template_task_id refs; the route rewrites them.
        build_t = next(t for t in bp["tasks"] if t["title"] == "Build feature")
        kickoff_ttid = next(
            t["template_task_id"] for t in tpl["task_definitions"] if t["title"] == "Kickoff"
        )
        assert build_t["dependencies"] == [kickoff_ttid]

    def test_provenance_lands_on_project_blueprint(self):
        tpl = self._waterfall_template()
        tpl["template_id"] = "tmpl_abc"
        tpl["version"] = 4
        bp = hydrate_template(
            tpl, start_date="2026-09-01", pm_user_id="user_pm", name="X"
        )
        assert bp["project"]["source_template_id"] == "tmpl_abc"
        assert bp["project"]["source_template_version"] == 4

    def test_invalid_start_date_raises(self):
        tpl = self._waterfall_template()
        with pytest.raises(ValueError):
            hydrate_template(tpl, start_date="garbage", pm_user_id="u", name="N")

    def test_agile_seeds_sprints_and_columns(self):
        tpl = snapshot_project(_agile_project(), tasks=[], sprints=_agile_sprints())
        bp = hydrate_template(
            tpl, start_date="2026-09-01", pm_user_id="user_pm", name="A"
        )
        assert bp["project"]["methodology_config"]["columns"]
        assert len(bp["sprints"]) == 2
        # 2026-06-01 → 2026-06-14 was offset 0 → 13. So 2026-09-01 → 2026-09-14.
        assert bp["sprints"][0]["start_date"] == "2026-09-01"
        assert bp["sprints"][0]["end_date"] == "2026-09-14"

    def test_waves_seed_with_depends_on_indexes_resolved_by_route(self):
        tpl = snapshot_project(_waves_project(), tasks=[], waves=_waves())
        bp = hydrate_template(
            tpl, start_date="2026-09-01", pm_user_id="user_pm", name="W"
        )
        assert len(bp["waves"]) == 2
        # The engine surfaces depends_on_indexes for the route to rewire.
        assert bp["waves"][1]["_depends_on_indexes"] == [0]


# ---------------------------------------------------------------------------
# build_preview
# ---------------------------------------------------------------------------
class TestBuildPreview:
    def test_returns_summary_and_computed_dates(self):
        tpl = snapshot_project(_waterfall_project(), _waterfall_tasks())
        preview = build_preview(tpl, start_date="2026-09-01")
        assert preview["project_type"] == "waterfall"
        assert preview["service_line"] == "ucaas"
        assert preview["task_count_total"] == 2
        assert preview["task_count_shown"] == 2
        assert preview["truncated"] is False
        kickoff = next(t for t in preview["tasks"] if t["title"] == "Kickoff")
        assert kickoff["due_date_computed"] == "2026-09-03"

    def test_estimated_duration_uses_max_offset(self):
        tpl = snapshot_project(_waterfall_project(), _waterfall_tasks())
        preview = build_preview(tpl, start_date="2026-09-01")
        # Max due_offset across tasks (14) vs max end_offset across phases (24).
        # build_preview takes max of both.
        assert preview["estimated_duration_days"] == 24

    def test_truncates_large_template_and_emits_warning(self):
        tpl = snapshot_project(_waterfall_project(), _waterfall_tasks())
        # Inflate task_definitions past PREVIEW_TASK_CAP.
        big_tasks = []
        for i in range(PREVIEW_TASK_CAP + 5):
            big_tasks.append(
                {
                    "template_task_id": f"ttask_{i:04d}",
                    "title": f"T{i}",
                    "priority": "medium",
                    "due_offset_days": i,
                    "dependencies": [],
                }
            )
        tpl["task_definitions"] = big_tasks
        preview = build_preview(tpl, start_date="2026-09-01")
        assert preview["truncated"] is True
        assert preview["task_count_total"] == PREVIEW_TASK_CAP + 5
        assert preview["task_count_shown"] == PREVIEW_TASK_CAP
        assert any("preview shows first" in w for w in preview["warnings"])

    def test_phases_sprints_waves_appear_for_their_methodology(self):
        wf = build_preview(
            snapshot_project(_waterfall_project(), _waterfall_tasks()),
            start_date="2026-09-01",
        )
        assert wf["phases"]
        assert wf["sprints"] == []
        assert wf["waves"] == []

        ag = build_preview(
            snapshot_project(_agile_project(), tasks=[], sprints=_agile_sprints()),
            start_date="2026-09-01",
        )
        assert ag["sprints"]
        assert ag["phases"] == []
        assert ag["waves"] == []

        wt = build_preview(
            snapshot_project(_waves_project(), tasks=[], waves=_waves()),
            start_date="2026-09-01",
        )
        assert wt["waves"]
        assert wt["phases"] == []
        assert wt["sprints"] == []

    def test_invalid_start_date_raises(self):
        tpl = snapshot_project(_waterfall_project(), _waterfall_tasks())
        with pytest.raises(ValueError):
            build_preview(tpl, start_date="not-a-date")


# ---------------------------------------------------------------------------
# Sanity
# ---------------------------------------------------------------------------
def test_supported_methodologies_constant():
    assert SUPPORTED_METHODOLOGIES == {"waterfall", "agile", "waves_threads"}
