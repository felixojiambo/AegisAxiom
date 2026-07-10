"""Tests for the v1.2 normalized bulk task endpoints.

Covers `POST /api/projects/tasks/reorder`, `PATCH .../bulk-assign`,
`PATCH .../bulk-status`, and `PATCH .../bulk-sprint`. All endpoints share
the response envelope:

    {
      "updated": [<full task doc>, ...],
      "failed":  [{"task_id": "...", "error": "...", "code": "..."}, ...],
      "summary": {"updated_count": int, "failed_count": int}
    }

These are in-process tests — FastAPI TestClient against a mocked DB and
auth dep override. No live Mongo, no live RBAC closure required.

Coverage per endpoint:
  * Happy path — returns updated[] of full task docs, not a count
  * Partial failure — some rows succeed, some fail, response shape holds
  * Top-level permission denial — 403 with no rows touched
  * Per-row RBAC scoping — cross-project bulk where caller can edit some
    projects but not others demotes the unauthorized rows into failed[]
    with code="forbidden" instead of 403'ing the whole batch
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

DEFAULT_PERMS = [
    "projects.task.view",
    "projects.task.edit",
    "projects.task.assign",
    "projects.task.change_status",
    "projects.project.view",
]


def _make_user(user_id="u_001", permissions=None):
    perms = list(DEFAULT_PERMS) if permissions is None else permissions
    return SimpleNamespace(
        user_id=user_id,
        email="tester@aegisaxiom.io",
        role="pm",
        permissions=perms,
    )


def _make_task(task_id, project_id="proj_A", status="not_started",
               assignee_id=None, methodology_fields=None, sort_index=0):
    return {
        "task_id": task_id,
        "project_id": project_id,
        "title": f"Task {task_id}",
        "status": status,
        "priority": "medium",
        "assignee_id": assignee_id,
        "methodology_fields": methodology_fields or {},
        "sort_index": sort_index,
        "created_at": "2026-05-11T12:00:00+00:00",
        "updated_at": "2026-05-11T12:00:00+00:00",
    }


def _make_project(project_id="proj_A", project_type="agile", status="active",
                  pm_id="u_001"):
    return {
        "project_id": project_id,
        "name": f"Project {project_id}",
        "project_type": project_type,
        "status": status,
        "pm_id": pm_id,
    }


def _make_sprint(sprint_id="sprint_X", project_id="proj_A"):
    return {
        "sprint_id": sprint_id,
        "project_id": project_id,
        "name": f"Sprint {sprint_id}",
    }


@asynccontextmanager
async def _fake_envelope(*args, **kwargs):
    """Stand-in for crud_envelope — yields a ctx with set_after."""
    ctx = MagicMock()
    ctx.set_after = MagicMock()
    yield ctx


def _build_db(tasks=None, projects=None, sprints=None, users=None):
    """Build a mock db with in-memory collections.

    Each collection supports find_one / update_one / count_documents /
    insert_one with enough fidelity for the bulk endpoints. Updates to
    pm_tasks mutate the in-memory store so a subsequent find_one returns
    the updated doc — this is what lets the endpoint's after-doc fetch
    return the post-update shape.
    """
    tasks_by_id = {t["task_id"]: dict(t) for t in (tasks or [])}
    projects_by_id = {p["project_id"]: dict(p) for p in (projects or [])}
    sprints_by_id = {s["sprint_id"]: dict(s) for s in (sprints or [])}
    users_by_id = {u: {"user_id": u} for u in (users or [])}

    # ---- pm_tasks ----
    tasks_coll = MagicMock()

    async def _tasks_find_one(query, projection=None):
        tid = query.get("task_id")
        if tid is None:
            return None
        doc = tasks_by_id.get(tid)
        if doc is None:
            return None
        return {k: v for k, v in doc.items() if k != "_id"}

    async def _tasks_update_one(query, update_ops):
        tid = query.get("task_id")
        if tid not in tasks_by_id:
            return MagicMock(modified_count=0)
        doc = tasks_by_id[tid]
        for k, v in (update_ops.get("$set") or {}).items():
            if "." in k:
                root, leaf = k.split(".", 1)
                doc.setdefault(root, {})[leaf] = v
            else:
                doc[k] = v
        for k in (update_ops.get("$unset") or {}):
            if "." in k:
                root, leaf = k.split(".", 1)
                if root in doc and leaf in doc[root]:
                    del doc[root][leaf]
            else:
                doc.pop(k, None)
        return MagicMock(modified_count=1)

    async def _tasks_count_documents(query):
        ids = query.get("task_id", {})
        target_ids = ids.get("$in", []) if isinstance(ids, dict) else [ids]
        wanted_project = query.get("project_id")
        wanted_column = query.get("methodology_fields.column_id")
        count = 0
        for tid in target_ids:
            doc = tasks_by_id.get(tid)
            if doc is None:
                continue
            if wanted_project and doc.get("project_id") != wanted_project:
                continue
            if wanted_column and (doc.get("methodology_fields") or {}).get("column_id") != wanted_column:
                continue
            count += 1
        return count

    tasks_coll.find_one = AsyncMock(side_effect=_tasks_find_one)
    tasks_coll.update_one = AsyncMock(side_effect=_tasks_update_one)
    tasks_coll.count_documents = AsyncMock(side_effect=_tasks_count_documents)
    tasks_coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id="x"))

    # ---- projects ----
    projects_coll = MagicMock()

    async def _projects_find_one(query, projection=None):
        pid = query.get("project_id")
        doc = projects_by_id.get(pid)
        if doc is None:
            return None
        return {k: v for k, v in doc.items() if k != "_id"}

    projects_coll.find_one = AsyncMock(side_effect=_projects_find_one)

    # ---- pm_sprints ----
    sprints_coll = MagicMock()

    async def _sprints_find_one(query, projection=None):
        sid = query.get("sprint_id")
        doc = sprints_by_id.get(sid)
        if doc is None:
            return None
        return {k: v for k, v in doc.items() if k != "_id"}

    sprints_coll.find_one = AsyncMock(side_effect=_sprints_find_one)

    # ---- users ----
    users_coll = MagicMock()

    async def _users_find_one(query, projection=None):
        uid = query.get("user_id")
        if uid in users_by_id:
            return users_by_id[uid]
        return None

    users_coll.find_one = AsyncMock(side_effect=_users_find_one)

    # ---- pm_task_status_history (write-only) ----
    history_coll = MagicMock()
    history_coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id="hx"))

    def _getitem(name):
        return {
            "pm_tasks": tasks_coll,
            "projects": projects_coll,
            "pm_sprints": sprints_coll,
            "users": users_coll,
            "pm_task_status_history": history_coll,
        }.get(name, MagicMock())

    db = MagicMock()
    db.__getitem__ = MagicMock(side_effect=_getitem)
    return db, tasks_by_id


def _build_app(user):
    from routes.projects_task import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/projects")
    app.dependency_overrides[get_current_user] = lambda: user
    return app


def _patch_route_deps(db):
    """Common patch set: db getter, AI permission gate (no-op), and
    crud_envelope (no-op async context manager)."""
    return (
        patch("routes.projects_task.get_db", return_value=db),
        patch(
            "middleware.ai_authz.require_ai_permission",
            return_value=lambda: None,
        ),
        patch("routes.projects_task.crud_envelope", side_effect=_fake_envelope),
        patch(
            "routes.projects_task.validate_task_methodology_fields",
            side_effect=lambda project, mf, status: (mf or {}, status),
        ),
    )


# ---------------------------------------------------------------------------
# /tasks/reorder
# ---------------------------------------------------------------------------
class TestReorder:
    def _setup(self, user=None):
        user = user or _make_user()
        tasks = [
            _make_task(
                "t1",
                methodology_fields={"column_id": "col_todo"},
                sort_index=0,
            ),
            _make_task(
                "t2",
                methodology_fields={"column_id": "col_todo"},
                sort_index=1,
            ),
            _make_task(
                "t3",
                methodology_fields={"column_id": "col_todo"},
                sort_index=2,
            ),
        ]
        projects = [_make_project("proj_A")]
        db, store = _build_db(tasks=tasks, projects=projects)
        return user, db, store

    def test_reorder_returns_full_task_docs(self):
        user, db, _ = self._setup()
        app = _build_app(user)
        patches = _patch_route_deps(db)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = TestClient(app).post(
                "/api/projects/tasks/reorder",
                json={
                    "project_id": "proj_A",
                    "column_id": "col_todo",
                    "ordered_task_ids": ["t3", "t1", "t2"],
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Envelope shape.
        assert set(body.keys()) == {"updated", "failed", "summary"}
        assert body["summary"] == {"updated_count": 3, "failed_count": 0}
        # updated[] holds full task docs (not just ids/counts).
        assert len(body["updated"]) == 3
        for doc in body["updated"]:
            assert "task_id" in doc
            assert "title" in doc
            assert "status" in doc
            assert "methodology_fields" in doc
        # sort_index reflects the new order.
        by_id = {d["task_id"]: d for d in body["updated"]}
        assert by_id["t3"]["sort_index"] == 0
        assert by_id["t1"]["sort_index"] == 1
        assert by_id["t2"]["sort_index"] == 2

    def test_reorder_permission_denied(self):
        user = _make_user(permissions=[])
        _, db, _ = self._setup(user=user)
        app = _build_app(user)
        patches = _patch_route_deps(db)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = TestClient(app).post(
                "/api/projects/tasks/reorder",
                json={
                    "project_id": "proj_A",
                    "column_id": "col_todo",
                    "ordered_task_ids": ["t1"],
                },
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# /tasks/bulk-assign
# ---------------------------------------------------------------------------
class TestBulkAssign:
    def test_assign_returns_updated_docs_not_count(self):
        user = _make_user()
        tasks = [
            _make_task("t1", project_id="proj_A", assignee_id=None),
            _make_task("t2", project_id="proj_A", assignee_id=None),
        ]
        projects = [_make_project("proj_A")]
        db, _ = _build_db(tasks=tasks, projects=projects, users=["u_assignee"])
        app = _build_app(user)

        patches = _patch_route_deps(db)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = TestClient(app).patch(
                "/api/projects/tasks/bulk-assign",
                json={"task_ids": ["t1", "t2"], "assignee_id": "u_assignee"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body.keys()) == {"updated", "failed", "summary"}
        assert body["summary"] == {"updated_count": 2, "failed_count": 0}
        assert len(body["updated"]) == 2
        for doc in body["updated"]:
            assert doc["assignee_id"] == "u_assignee"
            assert "title" in doc  # full doc, not just id

    def test_partial_failure_unknown_task_id(self):
        user = _make_user()
        tasks = [_make_task("t1", project_id="proj_A", assignee_id=None)]
        projects = [_make_project("proj_A")]
        db, _ = _build_db(tasks=tasks, projects=projects, users=["u_assignee"])
        app = _build_app(user)

        patches = _patch_route_deps(db)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = TestClient(app).patch(
                "/api/projects/tasks/bulk-assign",
                json={
                    "task_ids": ["t1", "t_missing"],
                    "assignee_id": "u_assignee",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["updated_count"] == 1
        assert body["summary"]["failed_count"] == 1
        assert body["failed"][0]["task_id"] == "t_missing"
        assert body["failed"][0]["code"] == "not_found"
        assert isinstance(body["failed"][0]["error"], str)

    def test_top_level_permission_denied(self):
        user = _make_user(permissions=[])
        tasks = [_make_task("t1", project_id="proj_A")]
        projects = [_make_project("proj_A")]
        db, _ = _build_db(tasks=tasks, projects=projects, users=["u_assignee"])
        app = _build_app(user)

        patches = _patch_route_deps(db)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = TestClient(app).patch(
                "/api/projects/tasks/bulk-assign",
                json={"task_ids": ["t1"], "assignee_id": "u_assignee"},
            )
        assert resp.status_code == 403

    def test_per_row_rbac_partial_authorization(self):
        # User has assign perm globally, but is PM on proj_A only and NOT
        # PM on proj_B. They lack global projects.task.edit. So bulk-assign
        # on a mix of proj_A and proj_B tasks should update proj_A's task
        # and demote proj_B's task into failed[] with code="forbidden" —
        # NOT 403 the whole batch.
        user = _make_user(
            user_id="u_pm_A",
            permissions=["projects.task.assign", "projects.project.view"],
        )
        tasks = [
            _make_task("t_A", project_id="proj_A"),
            _make_task("t_B", project_id="proj_B"),
        ]
        projects = [
            _make_project("proj_A", pm_id="u_pm_A"),
            _make_project("proj_B", pm_id="u_other"),
        ]
        db, _ = _build_db(tasks=tasks, projects=projects, users=["u_assignee"])
        app = _build_app(user)

        patches = _patch_route_deps(db)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = TestClient(app).patch(
                "/api/projects/tasks/bulk-assign",
                json={
                    "task_ids": ["t_A", "t_B"],
                    "assignee_id": "u_assignee",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["updated_count"] == 1
        assert body["summary"]["failed_count"] == 1
        assert body["updated"][0]["task_id"] == "t_A"
        assert body["failed"][0]["task_id"] == "t_B"
        assert body["failed"][0]["code"] == "forbidden"


# ---------------------------------------------------------------------------
# /tasks/bulk-status
# ---------------------------------------------------------------------------
class TestBulkStatus:
    def test_status_returns_updated_docs(self):
        user = _make_user()
        tasks = [
            _make_task("t1", project_id="proj_A", status="not_started"),
            _make_task("t2", project_id="proj_A", status="not_started"),
        ]
        projects = [_make_project("proj_A")]
        db, _ = _build_db(tasks=tasks, projects=projects)
        app = _build_app(user)

        patches = _patch_route_deps(db)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = TestClient(app).patch(
                "/api/projects/tasks/bulk-status",
                json={"task_ids": ["t1", "t2"], "status": "in_progress"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body.keys()) == {"updated", "failed", "summary"}
        assert body["summary"] == {"updated_count": 2, "failed_count": 0}
        for doc in body["updated"]:
            assert doc["status"] == "in_progress"
            assert "title" in doc

    def test_partial_failure_unknown_id(self):
        user = _make_user()
        tasks = [_make_task("t1", project_id="proj_A", status="not_started")]
        projects = [_make_project("proj_A")]
        db, _ = _build_db(tasks=tasks, projects=projects)
        app = _build_app(user)

        patches = _patch_route_deps(db)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = TestClient(app).patch(
                "/api/projects/tasks/bulk-status",
                json={
                    "task_ids": ["t1", "t_ghost"],
                    "status": "in_progress",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"] == {"updated_count": 1, "failed_count": 1}
        assert body["failed"][0]["code"] == "not_found"

    def test_top_level_permission_denied(self):
        user = _make_user(permissions=["projects.task.view"])
        tasks = [_make_task("t1", project_id="proj_A", status="not_started")]
        projects = [_make_project("proj_A")]
        db, _ = _build_db(tasks=tasks, projects=projects)
        app = _build_app(user)

        patches = _patch_route_deps(db)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = TestClient(app).patch(
                "/api/projects/tasks/bulk-status",
                json={"task_ids": ["t1"], "status": "in_progress"},
            )
        assert resp.status_code == 403

    def test_per_row_rbac_partial_authorization(self):
        user = _make_user(
            user_id="u_pm_A",
            permissions=["projects.task.change_status", "projects.project.view"],
        )
        tasks = [
            _make_task("t_A", project_id="proj_A", status="not_started"),
            _make_task("t_B", project_id="proj_B", status="not_started"),
        ]
        projects = [
            _make_project("proj_A", pm_id="u_pm_A"),
            _make_project("proj_B", pm_id="u_other"),
        ]
        db, _ = _build_db(tasks=tasks, projects=projects)
        app = _build_app(user)

        patches = _patch_route_deps(db)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = TestClient(app).patch(
                "/api/projects/tasks/bulk-status",
                json={
                    "task_ids": ["t_A", "t_B"],
                    "status": "in_progress",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"] == {"updated_count": 1, "failed_count": 1}
        assert body["updated"][0]["task_id"] == "t_A"
        assert body["failed"][0]["task_id"] == "t_B"
        assert body["failed"][0]["code"] == "forbidden"


# ---------------------------------------------------------------------------
# /tasks/bulk-sprint
# ---------------------------------------------------------------------------
class TestBulkSprint:
    def test_assign_to_sprint_returns_updated_docs(self):
        user = _make_user()
        tasks = [
            _make_task("t1", project_id="proj_A", methodology_fields={}),
            _make_task("t2", project_id="proj_A", methodology_fields={}),
        ]
        projects = [_make_project("proj_A", project_type="agile")]
        sprints = [_make_sprint("sprint_X", project_id="proj_A")]
        db, _ = _build_db(tasks=tasks, projects=projects, sprints=sprints)
        app = _build_app(user)

        patches = _patch_route_deps(db)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = TestClient(app).patch(
                "/api/projects/tasks/bulk-sprint",
                json={"task_ids": ["t1", "t2"], "sprint_id": "sprint_X"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body.keys()) == {"updated", "failed", "summary"}
        assert body["summary"] == {"updated_count": 2, "failed_count": 0}
        for doc in body["updated"]:
            assert (doc["methodology_fields"] or {}).get("sprint_id") == "sprint_X"

    def test_move_to_backlog_clears_sprint(self):
        user = _make_user()
        tasks = [
            _make_task(
                "t1",
                project_id="proj_A",
                methodology_fields={"sprint_id": "sprint_X"},
            ),
        ]
        projects = [_make_project("proj_A", project_type="agile")]
        db, _ = _build_db(tasks=tasks, projects=projects)
        app = _build_app(user)

        patches = _patch_route_deps(db)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = TestClient(app).patch(
                "/api/projects/tasks/bulk-sprint",
                json={"task_ids": ["t1"], "sprint_id": None},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["summary"] == {"updated_count": 1, "failed_count": 0}
        # sprint_id sub-field should be gone (move-to-backlog uses $unset).
        assert "sprint_id" not in (body["updated"][0].get("methodology_fields") or {})

    def test_unknown_sprint_id_422_top_level(self):
        user = _make_user()
        tasks = [_make_task("t1", project_id="proj_A")]
        projects = [_make_project("proj_A", project_type="agile")]
        db, _ = _build_db(tasks=tasks, projects=projects)
        app = _build_app(user)

        patches = _patch_route_deps(db)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = TestClient(app).patch(
                "/api/projects/tasks/bulk-sprint",
                json={"task_ids": ["t1"], "sprint_id": "sprint_ghost"},
            )
        # Top-level reject — the target sprint doesn't exist at all, so
        # the entire bulk would fail every row identically. Better to
        # fail loudly with 422 once than echo the same error N times.
        assert resp.status_code == 422

    def test_cross_project_sprint_demoted_per_row(self):
        # sprint_X belongs to proj_A. Task t_B is in proj_B. The cross-
        # project assignment should fail just t_B (per-row, code=
        # "invalid_sprint"), not the whole batch.
        user = _make_user()
        tasks = [
            _make_task("t_A", project_id="proj_A"),
            _make_task("t_B", project_id="proj_B"),
        ]
        projects = [
            _make_project("proj_A", pm_id="u_001"),
            _make_project("proj_B", pm_id="u_001"),
        ]
        sprints = [_make_sprint("sprint_X", project_id="proj_A")]
        db, _ = _build_db(tasks=tasks, projects=projects, sprints=sprints)
        app = _build_app(user)

        patches = _patch_route_deps(db)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = TestClient(app).patch(
                "/api/projects/tasks/bulk-sprint",
                json={"task_ids": ["t_A", "t_B"], "sprint_id": "sprint_X"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"] == {"updated_count": 1, "failed_count": 1}
        assert body["updated"][0]["task_id"] == "t_A"
        assert body["failed"][0]["task_id"] == "t_B"
        assert body["failed"][0]["code"] == "invalid_sprint"

    def test_top_level_permission_denied(self):
        user = _make_user(permissions=["projects.task.view"])
        tasks = [_make_task("t1", project_id="proj_A")]
        projects = [_make_project("proj_A")]
        sprints = [_make_sprint("sprint_X", project_id="proj_A")]
        db, _ = _build_db(tasks=tasks, projects=projects, sprints=sprints)
        app = _build_app(user)

        patches = _patch_route_deps(db)
        with patches[0], patches[1], patches[2], patches[3]:
            resp = TestClient(app).patch(
                "/api/projects/tasks/bulk-sprint",
                json={"task_ids": ["t1"], "sprint_id": "sprint_X"},
            )
        assert resp.status_code == 403
