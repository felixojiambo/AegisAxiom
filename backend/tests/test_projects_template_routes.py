"""Integration tests for the v1.3 template polish endpoints.

Covers the three new routes:
  * POST /projects/{project_id}/save-as-template
  * GET  /templates/{template_id}/preview
  * POST /templates/{template_id}/instantiate

Plus the v1.3 versioning behavior on the existing PATCH /templates/{id}.

Pattern: FastAPI TestClient against the live route module with get_db,
crud_envelope, and require_ai_permission patched. No live Mongo.
"""
from __future__ import annotations

import os
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "aegisaxiom_test")

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
DEFAULT_PERMS = [
    "projects.template.view",
    "projects.template.create",
    "projects.template.edit",
    "projects.template.delete",
    "projects.project.view",
    "projects.project.create",
    "projects.project.edit",
]


def _make_user(user_id="u_pm", permissions=None):
    perms = list(DEFAULT_PERMS) if permissions is None else permissions
    return SimpleNamespace(
        user_id=user_id,
        email="pm@simplimeta.io",
        role="pm",
        name="PM Tester",
        permissions=set(perms),
        _perm_closure=set(perms),
    )


@asynccontextmanager
async def _fake_envelope(*args, **kwargs):
    ctx = MagicMock()
    ctx.set_after = MagicMock()
    yield ctx


def _make_project(
    project_id="proj_w1",
    project_type="waterfall",
    start_date="2026-06-01",
    pm_id="u_pm",
):
    return {
        "project_id": project_id,
        "name": f"Project {project_id}",
        "project_type": project_type,
        "service_line": "ucaas",
        "status": "active",
        "health": "green",
        "pm_id": pm_id,
        "assigned_user_ids": [pm_id],
        "start_date": start_date,
        "methodology_config": {
            "phases": [
                {"name": "Plan", "start_date": "2026-06-01", "end_date": "2026-06-05"},
                {"name": "Build", "start_date": "2026-06-06", "end_date": "2026-06-20"},
            ],
        }
        if project_type == "waterfall"
        else {"columns": [{"id": "to_do", "name": "To Do"}, {"id": "done", "name": "Done"}]}
        if project_type == "agile"
        else {"wave_count_target": 1, "modules": ["general"]},
    }


def _make_task(task_id, project_id="proj_w1", **kwargs):
    base = {
        "task_id": task_id,
        "project_id": project_id,
        "title": f"Task {task_id}",
        "assignee_id": "u_pm",
        "status": "not_started",
        "priority": "medium",
        "start_date": "2026-06-01",
        "due_date": "2026-06-03",
        "dependencies": [],
        "methodology_fields": {"phase": "Plan"},
        "labels": [],
        "task_number": 1,
        "created_at": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-06-01T00:00:00+00:00",
        "created_by": "u_pm",
    }
    base.update(kwargs)
    return base


def _make_template(template_id="tmpl_abc", project_type="waterfall", version=1):
    """Build a minimal template doc with two tasks + offsets."""
    return {
        "template_id": template_id,
        "name": "PMO Standard",
        "description": "Standard PMO scaffold",
        "project_type": project_type,
        "service_line": "ucaas",
        "task_definitions": [
            {
                "template_task_id": "ttask_001",
                "title": "Kickoff",
                "status": "not_started",
                "priority": "high",
                "start_offset_days": 0,
                "due_offset_days": 2,
                "dependencies": [],
                "methodology_fields": {"phase": "Plan"},
                "labels": [],
            },
            {
                "template_task_id": "ttask_002",
                "title": "Build",
                "status": "not_started",
                "priority": "medium",
                "start_offset_days": 3,
                "due_offset_days": 14,
                "dependencies": ["ttask_001"],
                "methodology_fields": {"phase": "Build"},
                "labels": [],
            },
        ],
        "default_phases": [
            {"name": "Plan", "start_offset_days": 0, "end_offset_days": 4},
            {"name": "Build", "start_offset_days": 5, "end_offset_days": 19},
        ] if project_type == "waterfall" else None,
        "default_columns": [
            {"id": "to_do", "name": "To Do"}, {"id": "done", "name": "Done"}
        ] if project_type == "agile" else None,
        "default_sprints": [
            {"name": "S1", "start_offset_days": 0, "end_offset_days": 13, "goal": "go", "capacity_points": 30},
        ] if project_type == "agile" else None,
        "default_waves": None,
        "version": version,
        "is_system": False,
        "config": {"include_dates": False, "skip_weekends": False},
        "created_at": "2026-05-11T00:00:00+00:00",
        "updated_at": "2026-05-11T00:00:00+00:00",
        "created_by": "u_pm",
    }


def _build_db(
    *,
    projects=None,
    tasks=None,
    templates=None,
    users=None,
    sprints=None,
    waves=None,
):
    """In-memory mongo stand-in.

    Stores docs in dicts. Insert/update/delete/find_one all mutate the
    backing store so a subsequent find_one returns the new state — the
    route handlers expect that fidelity.
    """
    p_store = {p["project_id"]: dict(p) for p in (projects or [])}
    t_store = {t["task_id"]: dict(t) for t in (tasks or [])}
    tmpl_store = {t["template_id"]: dict(t) for t in (templates or [])}
    u_store = {u: {"user_id": u} for u in (users or ["u_pm"])}
    s_store = {s["sprint_id"]: dict(s) for s in (sprints or [])}
    w_store = {w["wave_id"]: dict(w) for w in (waves or [])}
    counters = {}

    # ---- projects ----
    pcoll = MagicMock()

    async def _pfind_one(query, projection=None):
        return p_store.get(query.get("project_id"))

    async def _pinsert_one(doc):
        p_store[doc["project_id"]] = dict(doc)
        return MagicMock(inserted_id=doc["project_id"])

    pcoll.find_one = AsyncMock(side_effect=_pfind_one)
    pcoll.insert_one = AsyncMock(side_effect=_pinsert_one)
    pcoll.count_documents = AsyncMock(return_value=0)

    # ---- pm_tasks ----
    tcoll = MagicMock()

    async def _tfind(query=None, projection=None):
        # Used by save-as-template's `.find(...).to_list(None)`.
        results = [
            {k: v for k, v in t.items() if k != "_id"}
            for t in t_store.values()
            if (query is None or t.get("project_id") == (query or {}).get("project_id"))
        ]
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=results)
        return cursor

    def _tfind_sync(query=None, projection=None):
        results = [
            {k: v for k, v in t.items() if k != "_id"}
            for t in t_store.values()
            if (query is None or t.get("project_id") == (query or {}).get("project_id"))
        ]
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=results)
        return cursor

    tcoll.find = MagicMock(side_effect=_tfind_sync)

    async def _tinsert_one(doc):
        t_store[doc["task_id"]] = dict(doc)
        return MagicMock(inserted_id=doc["task_id"])

    async def _tinsert_many(docs):
        for d in docs:
            t_store[d["task_id"]] = dict(d)
        return MagicMock(inserted_ids=[d["task_id"] for d in docs])

    tcoll.insert_one = AsyncMock(side_effect=_tinsert_one)
    tcoll.insert_many = AsyncMock(side_effect=_tinsert_many)
    tcoll.count_documents = AsyncMock(return_value=0)

    # ---- pm_templates ----
    tplcoll = MagicMock()

    async def _tplfind_one(query, projection=None):
        return tmpl_store.get(query.get("template_id"))

    async def _tplinsert_one(doc):
        tmpl_store[doc["template_id"]] = dict(doc)
        return MagicMock(inserted_id=doc["template_id"])

    async def _tplupdate_one(query, ops):
        tmpl_id = query.get("template_id")
        if tmpl_id not in tmpl_store:
            return MagicMock(modified_count=0)
        for k, v in (ops.get("$set") or {}).items():
            tmpl_store[tmpl_id][k] = v
        return MagicMock(modified_count=1)

    tplcoll.find_one = AsyncMock(side_effect=_tplfind_one)
    tplcoll.insert_one = AsyncMock(side_effect=_tplinsert_one)
    tplcoll.update_one = AsyncMock(side_effect=_tplupdate_one)

    # ---- users ----
    ucoll = MagicMock()

    async def _ufind_one(query, projection=None):
        return u_store.get(query.get("user_id"))

    ucoll.find_one = AsyncMock(side_effect=_ufind_one)
    ucoll.count_documents = AsyncMock(return_value=0)

    # ---- pm_sprints / pm_waves ----
    scoll = MagicMock()

    def _sfind_sync(query=None, projection=None):
        results = [
            {k: v for k, v in s.items() if k != "_id"}
            for s in s_store.values()
            if (query is None or s.get("project_id") == (query or {}).get("project_id"))
        ]
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=results)
        return cursor

    scoll.find = MagicMock(side_effect=_sfind_sync)

    async def _sinsert_one(doc):
        s_store[doc["sprint_id"]] = dict(doc)
        return MagicMock(inserted_id=doc["sprint_id"])

    scoll.insert_one = AsyncMock(side_effect=_sinsert_one)

    wcoll = MagicMock()

    def _wfind_sync(query=None, projection=None):
        results = [
            {k: v for k, v in w.items() if k != "_id"}
            for w in w_store.values()
            if (query is None or w.get("project_id") == (query or {}).get("project_id"))
        ]
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=results)
        return cursor

    wcoll.find = MagicMock(side_effect=_wfind_sync)

    async def _winsert_many(docs):
        for d in docs:
            w_store[d["wave_id"]] = dict(d)
        return MagicMock(inserted_ids=[d["wave_id"] for d in docs])

    wcoll.insert_many = AsyncMock(side_effect=_winsert_many)

    # ---- counters (used by _next_task_number_for_project) ----
    counters_coll = MagicMock()

    async def _ctr_find_and_update(query, update, upsert=False, return_document=None):
        key = query["_id"]
        counters[key] = counters.get(key, 0) + (update.get("$inc", {}).get("seq", 0))
        return {"_id": key, "seq": counters[key]}

    counters_coll.find_one_and_update = AsyncMock(side_effect=_ctr_find_and_update)

    def _getitem(name):
        return {
            "projects": pcoll,
            "pm_tasks": tcoll,
            "pm_templates": tplcoll,
            "users": ucoll,
            "pm_sprints": scoll,
            "pm_waves": wcoll,
            "pm_counters": counters_coll,
        }.get(name, MagicMock())

    db = MagicMock()
    db.__getitem__ = MagicMock(side_effect=_getitem)
    return db, {
        "projects": p_store,
        "tasks": t_store,
        "templates": tmpl_store,
        "sprints": s_store,
        "waves": w_store,
    }


def _build_app(user):
    from routes.projects_template import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/projects")
    app.dependency_overrides[get_current_user] = lambda: user
    return app


def _patches(db):
    return [
        patch("routes.projects_template.get_db", return_value=db),
        patch("routes.projects_project.get_db", return_value=db),
        patch("routes.projects_template.crud_envelope", side_effect=_fake_envelope),
        patch(
            "middleware.ai_authz.require_ai_permission",
            return_value=lambda: None,
        ),
        # Bypass the methodology validator — the engine builds valid configs
        # already; the validator's real implementation requires module side
        # effects we don't want to import here.
        patch(
            "routes.projects_template.validate_project_methodology_config",
            side_effect=lambda pt, cfg: cfg,
        ),
        patch(
            "routes.projects_project.validate_task_methodology_fields",
            side_effect=lambda project, mf, status: (mf or {}, status or "not_started"),
        ),
    ]


# ---------------------------------------------------------------------------
# Tests — save-as-template
# ---------------------------------------------------------------------------
class TestSaveAsTemplate:
    def test_happy_path_creates_template_and_returns_counts(self):
        user = _make_user()
        project = _make_project()
        tasks = [
            _make_task("task_001"),
            _make_task("task_002", dependencies=["task_001"]),
        ]
        db, store = _build_db(projects=[project], tasks=tasks, users=["u_pm"])
        app = _build_app(user)
        with patch.multiple(
            "routes.projects_template",
            get_db=MagicMock(return_value=db),
            crud_envelope=MagicMock(side_effect=_fake_envelope),
        ):
            resp = TestClient(app).post(
                f"/api/projects/projects/{project['project_id']}/save-as-template",
                json={"name": "My Template", "include_dates": False},
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["task_count"] == 2
        assert body["phase_count"] == 2
        assert body["sprint_count"] == 0
        assert "template_id" in body
        # And the doc landed in the store.
        assert len(store["templates"]) == 1
        stored = next(iter(store["templates"].values()))
        assert stored["version"] == 1
        # Assignees were stripped.
        for td in stored["task_definitions"]:
            assert "assignee_id" not in td

    def test_missing_project_returns_404(self):
        user = _make_user()
        db, _ = _build_db(users=["u_pm"])
        app = _build_app(user)
        with patch.multiple(
            "routes.projects_template",
            get_db=MagicMock(return_value=db),
            crud_envelope=MagicMock(side_effect=_fake_envelope),
        ):
            resp = TestClient(app).post(
                "/api/projects/projects/proj_missing/save-as-template",
                json={"name": "x"},
            )
        assert resp.status_code == 404

    def test_permission_denial_returns_403(self):
        # User has projects.project.view but not projects.template.create.
        user = _make_user(permissions=["projects.project.view"])
        project = _make_project()
        db, _ = _build_db(projects=[project], users=["u_pm"])
        app = _build_app(user)
        with patch.multiple(
            "routes.projects_template",
            get_db=MagicMock(return_value=db),
            crud_envelope=MagicMock(side_effect=_fake_envelope),
        ):
            resp = TestClient(app).post(
                f"/api/projects/projects/{project['project_id']}/save-as-template",
                json={"name": "x"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests — preview
# ---------------------------------------------------------------------------
class TestPreview:
    def test_happy_path_returns_computed_dates(self):
        user = _make_user()
        tpl = _make_template()
        db, _ = _build_db(templates=[tpl])
        app = _build_app(user)
        with patch.multiple(
            "routes.projects_template",
            get_db=MagicMock(return_value=db),
        ):
            resp = TestClient(app).get(
                f"/api/projects/templates/{tpl['template_id']}/preview",
                params={"start_date": "2026-09-01"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["project_type"] == "waterfall"
        assert body["task_count_total"] == 2
        assert body["truncated"] is False
        # Build task has due_offset_days=14 → 2026-09-15.
        build = next(t for t in body["tasks"] if t["title"] == "Build")
        assert build["due_date_computed"] == "2026-09-15"
        # estimated_duration_days is max offset = 19 (default_phases Build end).
        assert body["estimated_duration_days"] == 19

    def test_missing_template_returns_404(self):
        user = _make_user()
        db, _ = _build_db(templates=[])
        app = _build_app(user)
        with patch.multiple(
            "routes.projects_template",
            get_db=MagicMock(return_value=db),
        ):
            resp = TestClient(app).get(
                "/api/projects/templates/tmpl_missing/preview",
                params={"start_date": "2026-09-01"},
            )
        assert resp.status_code == 404

    def test_invalid_start_date_returns_422(self):
        user = _make_user()
        tpl = _make_template()
        db, _ = _build_db(templates=[tpl])
        app = _build_app(user)
        with patch.multiple(
            "routes.projects_template",
            get_db=MagicMock(return_value=db),
        ):
            resp = TestClient(app).get(
                f"/api/projects/templates/{tpl['template_id']}/preview",
                params={"start_date": "not-a-date"},
            )
        assert resp.status_code == 422

    def test_permission_denial_returns_403(self):
        user = _make_user(permissions=[])  # No template.view.
        tpl = _make_template()
        db, _ = _build_db(templates=[tpl])
        app = _build_app(user)
        with patch.multiple(
            "routes.projects_template",
            get_db=MagicMock(return_value=db),
        ):
            resp = TestClient(app).get(
                f"/api/projects/templates/{tpl['template_id']}/preview",
                params={"start_date": "2026-09-01"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests — instantiate
# ---------------------------------------------------------------------------
class TestInstantiate:
    def test_happy_path_creates_project_and_tasks_with_provenance(self):
        user = _make_user()
        tpl = _make_template(version=3)
        db, store = _build_db(templates=[tpl], users=["u_pm"])
        app = _build_app(user)

        with patch.multiple(
            "routes.projects_template",
            get_db=MagicMock(return_value=db),
            crud_envelope=MagicMock(side_effect=_fake_envelope),
        ), patch(
            "services.projects_methodology.validate_project_methodology_config",
            side_effect=lambda pt, cfg: cfg,
        ), patch(
            "routes.projects_project.get_db", return_value=db
        ), patch(
            "routes.projects_project.validate_task_methodology_fields",
            side_effect=lambda project, mf, status: (mf or {}, status or "not_started"),
        ):
            resp = TestClient(app).post(
                f"/api/projects/templates/{tpl['template_id']}/instantiate",
                json={"name": "Q3 launch", "start_date": "2026-09-01"},
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["task_count_created"] == 2
        assert body["source_template_id"] == tpl["template_id"]
        assert body["source_template_version"] == 3

        # Check the persisted project recorded provenance.
        assert len(store["projects"]) == 1
        proj = next(iter(store["projects"].values()))
        assert proj["source_template_id"] == tpl["template_id"]
        assert proj["source_template_version"] == 3
        assert proj["template_id"] == tpl["template_id"]

        # Check the persisted tasks have dependencies rewired to real ids.
        tasks = list(store["tasks"].values())
        assert len(tasks) == 2
        kickoff = next(t for t in tasks if t["title"] == "Kickoff")
        build = next(t for t in tasks if t["title"] == "Build")
        assert build["dependencies"] == [kickoff["task_id"]]
        # Dates computed correctly: due_offset_days=14 from 2026-09-01.
        assert build["due_date"] == "2026-09-15"

    def test_overrides_skip_task_omits_it(self):
        user = _make_user()
        tpl = _make_template()
        db, store = _build_db(templates=[tpl], users=["u_pm"])
        app = _build_app(user)
        with patch.multiple(
            "routes.projects_template",
            get_db=MagicMock(return_value=db),
            crud_envelope=MagicMock(side_effect=_fake_envelope),
        ), patch(
            "services.projects_methodology.validate_project_methodology_config",
            side_effect=lambda pt, cfg: cfg,
        ), patch(
            "routes.projects_project.get_db", return_value=db
        ), patch(
            "routes.projects_project.validate_task_methodology_fields",
            side_effect=lambda project, mf, status: (mf or {}, status or "not_started"),
        ):
            resp = TestClient(app).post(
                f"/api/projects/templates/{tpl['template_id']}/instantiate",
                json={
                    "name": "Q3 launch",
                    "start_date": "2026-09-01",
                    "overrides": {
                        "skip_task_ids": ["ttask_002"],
                        "task_title_prefix": "[Q3] ",
                    },
                },
            )
        assert resp.status_code == 201, resp.text
        assert resp.json()["task_count_created"] == 1
        tasks = list(store["tasks"].values())
        assert len(tasks) == 1
        assert tasks[0]["title"] == "[Q3] Kickoff"

    def test_missing_template_returns_404(self):
        user = _make_user()
        db, _ = _build_db(users=["u_pm"])
        app = _build_app(user)
        with patch.multiple(
            "routes.projects_template",
            get_db=MagicMock(return_value=db),
            crud_envelope=MagicMock(side_effect=_fake_envelope),
        ):
            resp = TestClient(app).post(
                "/api/projects/templates/tmpl_missing/instantiate",
                json={"name": "x", "start_date": "2026-09-01"},
            )
        assert resp.status_code == 404

    def test_invalid_pm_user_id_returns_422(self):
        user = _make_user()
        tpl = _make_template()
        db, _ = _build_db(templates=[tpl], users=["u_pm"])
        app = _build_app(user)
        with patch.multiple(
            "routes.projects_template",
            get_db=MagicMock(return_value=db),
            crud_envelope=MagicMock(side_effect=_fake_envelope),
        ):
            resp = TestClient(app).post(
                f"/api/projects/templates/{tpl['template_id']}/instantiate",
                json={"name": "x", "start_date": "2026-09-01", "pm_user_id": "u_ghost"},
            )
        assert resp.status_code == 422

    def test_permission_denial_returns_403(self):
        user = _make_user(permissions=["projects.template.view"])  # No project.create.
        tpl = _make_template()
        db, _ = _build_db(templates=[tpl], users=["u_pm"])
        app = _build_app(user)
        with patch.multiple(
            "routes.projects_template",
            get_db=MagicMock(return_value=db),
            crud_envelope=MagicMock(side_effect=_fake_envelope),
        ):
            resp = TestClient(app).post(
                f"/api/projects/templates/{tpl['template_id']}/instantiate",
                json={"name": "x", "start_date": "2026-09-01"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests — versioning on PATCH
# ---------------------------------------------------------------------------
class TestVersioning:
    def test_patch_increments_version(self):
        user = _make_user()
        tpl = _make_template(version=5)
        db, store = _build_db(templates=[tpl])
        app = _build_app(user)
        with patch.multiple(
            "routes.projects_template",
            get_db=MagicMock(return_value=db),
            crud_envelope=MagicMock(side_effect=_fake_envelope),
        ):
            resp = TestClient(app).patch(
                f"/api/projects/templates/{tpl['template_id']}",
                json={"name": "Renamed"},
            )
        assert resp.status_code == 200, resp.text
        assert store["templates"][tpl["template_id"]]["version"] == 6

    def test_patch_with_no_body_still_bumps_version(self):
        user = _make_user()
        tpl = _make_template(version=2)
        db, store = _build_db(templates=[tpl])
        app = _build_app(user)
        with patch.multiple(
            "routes.projects_template",
            get_db=MagicMock(return_value=db),
            crud_envelope=MagicMock(side_effect=_fake_envelope),
        ):
            resp = TestClient(app).patch(
                f"/api/projects/templates/{tpl['template_id']}",
                json={},
            )
        assert resp.status_code == 200
        assert store["templates"][tpl["template_id"]]["version"] == 3
