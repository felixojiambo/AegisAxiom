"""Integration: bulk endpoint partial-failure with per-row forbidden code.

Spec: 3 tasks where 1 is from a project the caller does NOT own →
  - response.updated[] has 2 docs
  - response.failed[] has 1 entry with code='forbidden'
  - HTTP 200 (not 403) — partial auth should not abort the whole batch

Uses FastAPI TestClient against the real route with a mocked DB and auth dep,
mirroring the pattern already established in test_projects_task_bulk.py.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers (mirrored from test_projects_task_bulk.py conventions)
# ---------------------------------------------------------------------------

def _make_user(user_id="u_pm_A", permissions=None):
    perms = permissions or ["projects.task.change_status", "projects.project.view"]
    return SimpleNamespace(
        user_id=user_id, email="tester@aegisaxiom.io", role="pm",
        permissions=perms,
    )


def _make_task(task_id, project_id="proj_A", status="not_started"):
    return {
        "task_id": task_id,
        "project_id": project_id,
        "title": f"Task {task_id}",
        "status": status,
        "priority": "medium",
        "assignee_id": None,
        "methodology_fields": {},
        "sort_index": 0,
        "created_at": "2026-05-11T00:00:00+00:00",
        "updated_at": "2026-05-11T00:00:00+00:00",
    }


def _make_project(project_id, pm_id="u_pm_A"):
    return {"project_id": project_id, "name": f"Project {project_id}", "pm_id": pm_id,
            "project_type": "agile", "status": "active"}


@asynccontextmanager
async def _fake_envelope(*args, **kwargs):
    ctx = MagicMock()
    ctx.set_after = MagicMock()
    yield ctx


def _build_db(tasks, projects):
    tasks_by_id = {t["task_id"]: dict(t) for t in tasks}
    projects_by_id = {p["project_id"]: dict(p) for p in projects}

    tasks_coll = MagicMock()

    async def _find_one(query, projection=None):
        tid = query.get("task_id")
        doc = tasks_by_id.get(tid)
        if doc is None:
            return None
        return {k: v for k, v in doc.items() if k != "_id"}

    async def _update_one(query, update_ops):
        tid = query.get("task_id")
        if tid not in tasks_by_id:
            return MagicMock(modified_count=0)
        doc = tasks_by_id[tid]
        for k, v in (update_ops.get("$set") or {}).items():
            doc[k] = v
        return MagicMock(modified_count=1)

    async def _count_docs(query):
        ids = query.get("task_id", {})
        target_ids = ids.get("$in", []) if isinstance(ids, dict) else [ids]
        return sum(1 for t in target_ids if t in tasks_by_id)

    tasks_coll.find_one = AsyncMock(side_effect=_find_one)
    tasks_coll.update_one = AsyncMock(side_effect=_update_one)
    tasks_coll.count_documents = AsyncMock(side_effect=_count_docs)
    tasks_coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id="x"))

    projects_coll = MagicMock()

    async def _proj_find_one(query, projection=None):
        pid = query.get("project_id")
        doc = projects_by_id.get(pid)
        return {k: v for k, v in doc.items() if k != "_id"} if doc else None

    projects_coll.find_one = AsyncMock(side_effect=_proj_find_one)

    history_coll = MagicMock()
    history_coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id="hx"))

    def _getitem(name):
        return {
            "pm_tasks": tasks_coll,
            "projects": projects_coll,
            "pm_task_status_history": history_coll,
        }.get(name, MagicMock())

    db = MagicMock()
    db.__getitem__ = MagicMock(side_effect=_getitem)
    return db


def _build_app(user):
    from routes.projects_task import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/projects")
    app.dependency_overrides[get_current_user] = lambda: user
    return app


def _patches(db):
    return (
        patch("routes.projects_task.get_db", return_value=db),
        patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None),
        patch("routes.projects_task.crud_envelope", side_effect=_fake_envelope),
        patch(
            "routes.projects_task.validate_task_methodology_fields",
            side_effect=lambda project, mf, status: (mf or {}, status),
        ),
    )


# ---------------------------------------------------------------------------
# Core spec: 3 tasks, 1 forbidden
# ---------------------------------------------------------------------------

class TestBulkPartialForbidden:
    """TC-BF-01: 3 tasks, 1 in a project the caller doesn't own → 2 updated, 1 forbidden."""

    def _setup_3_tasks_1_forbidden(self):
        # Caller is PM on proj_A only.
        user = _make_user(user_id="u_pm_A", permissions=[
            "projects.task.change_status",
            "projects.project.view",
        ])
        tasks = [
            _make_task("t1", project_id="proj_A", status="not_started"),
            _make_task("t2", project_id="proj_A", status="not_started"),
            _make_task("t3", project_id="proj_B", status="not_started"),  # ← forbidden
        ]
        projects = [
            _make_project("proj_A", pm_id="u_pm_A"),
            _make_project("proj_B", pm_id="u_other"),  # caller is NOT pm here
        ]
        db = _build_db(tasks, projects)
        return user, db

    def test_bulk_status_2_updated_1_forbidden(self):
        user, db = self._setup_3_tasks_1_forbidden()
        app = _build_app(user)
        p = _patches(db)
        with p[0], p[1], p[2], p[3]:
            resp = TestClient(app).patch(
                "/api/projects/tasks/bulk-status",
                json={"task_ids": ["t1", "t2", "t3"], "status": "in_progress"},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Envelope shape intact
        assert set(body.keys()) == {"updated", "failed", "summary"}

        # 2 succeeded, 1 forbidden
        assert body["summary"]["updated_count"] == 2, \
            f"Expected 2 updated, got {body['summary']}"
        assert body["summary"]["failed_count"] == 1, \
            f"Expected 1 failed, got {body['summary']}"

        # failed[] entry for t3 must carry code='forbidden'
        failed_ids = [f["task_id"] for f in body["failed"]]
        assert "t3" in failed_ids, f"t3 must be in failed[], got {failed_ids}"
        t3_failure = next(f for f in body["failed"] if f["task_id"] == "t3")
        assert t3_failure["code"] == "forbidden", \
            f"Expected code='forbidden', got code='{t3_failure.get('code')}'"
        assert isinstance(t3_failure["error"], str), "failed[] entry must include error string"

        # updated[] holds full task docs for t1 and t2
        updated_ids = {d["task_id"] for d in body["updated"]}
        assert "t1" in updated_ids
        assert "t2" in updated_ids
        assert "t3" not in updated_ids

        # Each updated doc must be a full task shape, not just a count
        for doc in body["updated"]:
            assert "title" in doc, "updated[] docs must be full task objects"
            assert "status" in doc

    def test_bulk_status_all_authorized_returns_zero_failed(self):
        """Happy path: no forbidden rows → failed=[] and all 3 updated."""
        user = _make_user(permissions=[
            "projects.task.change_status",
            "projects.project.view",
        ])
        tasks = [
            _make_task("a1", project_id="proj_A"),
            _make_task("a2", project_id="proj_A"),
            _make_task("a3", project_id="proj_A"),
        ]
        projects = [_make_project("proj_A", pm_id="u_pm_A")]
        db = _build_db(tasks, projects)
        app = _build_app(user)
        p = _patches(db)
        with p[0], p[1], p[2], p[3]:
            resp = TestClient(app).patch(
                "/api/projects/tasks/bulk-status",
                json={"task_ids": ["a1", "a2", "a3"], "status": "in_progress"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["failed_count"] == 0
        assert body["summary"]["updated_count"] == 3
        assert body["failed"] == []

    def test_bulk_assign_3_tasks_1_forbidden(self):
        """Same partial-forbidden scenario exercised via bulk-assign endpoint."""
        user = _make_user(user_id="u_pm_A", permissions=[
            "projects.task.assign",
            "projects.project.view",
        ])
        tasks = [
            _make_task("b1", project_id="proj_A"),
            _make_task("b2", project_id="proj_A"),
            _make_task("b3", project_id="proj_B"),  # ← forbidden
        ]
        projects = [
            _make_project("proj_A", pm_id="u_pm_A"),
            _make_project("proj_B", pm_id="u_other"),
        ]
        # Add assignee user support
        users_coll = MagicMock()
        users_coll.find_one = AsyncMock(return_value={"user_id": "u_assignee"})

        db = _build_db(tasks, projects)

        # Patch users collection into db
        original_getitem = db.__getitem__.side_effect
        def _getitem_with_users(name):
            if name == "users":
                return users_coll
            return original_getitem(name)
        db.__getitem__ = MagicMock(side_effect=_getitem_with_users)

        app = _build_app(user)
        p = _patches(db)
        with p[0], p[1], p[2], p[3]:
            resp = TestClient(app).patch(
                "/api/projects/tasks/bulk-assign",
                json={"task_ids": ["b1", "b2", "b3"], "assignee_id": "u_assignee"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["updated_count"] == 2
        assert body["summary"]["failed_count"] == 1
        assert body["failed"][0]["code"] == "forbidden"
        assert body["failed"][0]["task_id"] == "b3"
