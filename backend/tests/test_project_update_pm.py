"""Regression — Project PATCH accepts pm_id reassignment.

Locks the contract behind the Settings tab's new Project Manager
picker:

  - PATCH /api/projects/projects/{id} with body {"pm_id": "<user>"}
    reassigns the PM when the target user exists.
  - PATCH with pm_id set to a non-existent user_id returns 422.
  - PATCH with pm_id=null clears the PM (the picker's "Unassigned"
    option).
  - The permission gate accepts either projects.project.edit
    (director / project_manager seed) OR PM-of-project + view (the
    Wave 4/A4 auto-grant).

The bug this regression catches is silent: the route already
accepted pm_id; the Settings tab simply did not send it. A future
schema rename or `extra="forbid"` flip would now surface here
instead of stranding the UI again.

Uses AsyncMock-shaped fakes — no Mongo required.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


def _director(perms=None):
    return SimpleNamespace(
        user_id="u_director_001",
        email="director@aegisaxiom.test",
        role="director",
        permissions=list(perms or ["projects.project.edit", "projects.project.view", "*"]),
        tenant_id=None,
        account_id=None,
    )


def _pm_of_project_user():
    """User who holds projects.project.view + is the project's PM —
    the Wave 4/A4 auto-grant should let them reassign the PM (or any
    other field) without the global edit perm."""
    return SimpleNamespace(
        user_id="u_pm_self",
        email="pm@aegisaxiom.test",
        role="project_manager",
        permissions=["projects.project.view"],
        tenant_id=None,
        account_id=None,
    )


def _viewer_user():
    return SimpleNamespace(
        user_id="u_viewer",
        email="viewer@aegisaxiom.test",
        role="viewer",
        permissions=["projects.project.view"],
        tenant_id=None,
        account_id=None,
    )


def _project(**overrides):
    base = {
        "project_id": "proj_001",
        "name": "Acme Migration",
        "project_type": "waterfall",
        "service_line": "ucaas",
        "status": "active",
        "health": "green",
        "pm_id": "u_pm_old",
        "assigned_user_ids": [],
        "methodology_config": {"phases": []},
        "account_id": "acct_001",
        "contract_id": None,
    }
    base.update(overrides)
    return base


class _FakeProjects:
    def __init__(self, doc):
        self._doc = dict(doc) if doc else None
        self.updates: list = []

    async def find_one(self, query, _projection=None):
        if not isinstance(self._doc, dict):
            return None
        if query.get("project_id") == self._doc.get("project_id"):
            return dict(self._doc)
        return None

    async def update_one(self, query, update):
        self.updates.append((dict(query), dict(update)))
        if isinstance(self._doc, dict) and "$set" in update:
            self._doc.update(update["$set"])
        return SimpleNamespace(modified_count=1)


class _FakeUsers:
    def __init__(self, by_id):
        self._by_id = dict(by_id or {})

    async def find_one(self, query, _projection=None):
        uid = query.get("user_id")
        return dict(self._by_id[uid]) if uid in self._by_id else None

    async def count_documents(self, query):
        ids = query.get("user_id", {}).get("$in", [])
        return sum(1 for i in ids if i in self._by_id)


def _setup(monkeypatch, *, project, users):
    projects = _FakeProjects(project)

    class _DB:
        pass
    db = _DB()
    db._collections = {
        "projects": projects,
        "users": _FakeUsers(users),
    }

    def _get_item(name):
        return db._collections[name]
    db.__class__.__getitem__ = staticmethod(_get_item)

    import routes.projects_project as routes_pp
    monkeypatch.setattr(routes_pp, "get_db", lambda: db)

    # crud_envelope wraps the update inside an async-context — stub a
    # minimal no-op so the route runs without the audit / event
    # plumbing that the unit test does not exercise.
    class _Ctx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def set_after(self, _doc):
            pass

    def _envelope_factory(**_kwargs):
        return _Ctx()
    monkeypatch.setattr(routes_pp, "crud_envelope", _envelope_factory)
    return db, projects, routes_pp


@pytest.mark.asyncio
async def test_director_can_reassign_pm(monkeypatch):
    """Director with projects.project.edit reassigns the PM."""
    from routes.projects_project import update_project, ProjectUpdate
    db, projects, _ = _setup(
        monkeypatch,
        project=_project(pm_id="u_pm_old"),
        users={"u_pm_new": {"user_id": "u_pm_new", "name": "New PM"}},
    )
    body = ProjectUpdate(pm_id="u_pm_new")
    result = await update_project(
        project_id="proj_001", payload=body,
        request=SimpleNamespace(), current_user=_director(),
    )
    assert result["pm_id"] == "u_pm_new"
    # Update payload includes pm_id, not any unrelated fields.
    set_doc = projects.updates[0][1]["$set"]
    assert set_doc["pm_id"] == "u_pm_new"


@pytest.mark.asyncio
async def test_reassign_to_unknown_user_returns_422(monkeypatch):
    from routes.projects_project import update_project, ProjectUpdate
    _setup(
        monkeypatch,
        project=_project(),
        users={"u_pm_existing": {"user_id": "u_pm_existing", "name": "Existing"}},
    )
    body = ProjectUpdate(pm_id="u_pm_missing")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await update_project(
            project_id="proj_001", payload=body,
            request=SimpleNamespace(), current_user=_director(),
        )
    assert ei.value.status_code == 422
    assert "pm_id" in str(ei.value.detail)


@pytest.mark.asyncio
async def test_pm_of_project_can_reassign_via_auto_grant(monkeypatch):
    """The Wave 4/A4 auto-grant: a user who is the project's PM and
    has projects.project.view can edit the project (including pm_id)
    without the global edit perm. Pins that the Settings tab's PM
    picker works for a PM reassigning themselves out."""
    from routes.projects_project import update_project, ProjectUpdate
    project_doc = _project(pm_id="u_pm_self")
    db, projects, _ = _setup(
        monkeypatch,
        project=project_doc,
        users={"u_pm_new": {"user_id": "u_pm_new", "name": "New PM"}},
    )
    body = ProjectUpdate(pm_id="u_pm_new")
    result = await update_project(
        project_id="proj_001", payload=body,
        request=SimpleNamespace(), current_user=_pm_of_project_user(),
    )
    assert result["pm_id"] == "u_pm_new"


@pytest.mark.asyncio
async def test_viewer_cannot_reassign_pm(monkeypatch):
    from routes.projects_project import update_project, ProjectUpdate
    _setup(
        monkeypatch,
        project=_project(pm_id="u_pm_old"),
        users={"u_pm_new": {"user_id": "u_pm_new", "name": "New PM"}},
    )
    body = ProjectUpdate(pm_id="u_pm_new")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await update_project(
            project_id="proj_001", payload=body,
            request=SimpleNamespace(), current_user=_viewer_user(),
        )
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_pm_id_null_clears_assignment(monkeypatch):
    """The Settings tab's 'Unassigned' option sends pm_id=null. The
    PATCH must persist a null pm_id without 422'ing on the FK check
    (route only validates when pm_id is truthy)."""
    from routes.projects_project import update_project, ProjectUpdate
    db, projects, _ = _setup(
        monkeypatch,
        project=_project(pm_id="u_pm_old"),
        users={},
    )
    body = ProjectUpdate(pm_id=None)
    # exclude_unset semantics: pm_id was explicitly set to None on the
    # body, so the route sees it and writes it through to the update.
    # If a future refactor switched to exclude_none, this test would
    # surface the regression.
    body_dict = body.model_dump(exclude_unset=True)
    assert "pm_id" in body_dict and body_dict["pm_id"] is None
    await update_project(
        project_id="proj_001", payload=body,
        request=SimpleNamespace(), current_user=_director(),
    )
    set_doc = projects.updates[0][1]["$set"]
    assert "pm_id" in set_doc
    assert set_doc["pm_id"] is None


# ---------------------------------------------------------------------------
# Frontend source-level lock for the Settings PM picker UX.
# ---------------------------------------------------------------------------


def test_settings_pm_picker_renders_synthetic_option_for_current_pm():
    """When the project carries a pm_id but the assignable-users
    candidate fetch is still in flight (or the assigned PM is no
    longer in the candidate list), the <select> would otherwise
    display blank because no <option value=pmId> exists. The fix is
    a synthetic option that surfaces the current assignment
    regardless of candidate-list state, so the user always sees who
    is currently assigned and a save without a change does not
    silently clear the PM.

    This is a source-level lock — yarn is not available in the
    sandbox to run the React test, but the contract is verifiable
    by inspecting the source."""
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "projects"
        / "ProjectDetailPage.js"
    )
    if not page.exists():  # pragma: no cover
        import pytest

        pytest.skip("frontend not mounted in this test image")
    src = page.read_text(encoding="utf-8")

    # The synthetic option must render conditionally — only when the
    # current pm_id is non-empty AND not already in the candidate
    # list. Inspect for the gate.
    assert "currentPmInList" in src, (
        "Settings PM picker must compute whether the current pm_id is "
        "already in the candidate list so it can avoid double-listing."
    )
    assert "settings-field-pm-current" in src, (
        "Settings PM picker must mark the synthetic 'current PM' "
        "option with data-testid=settings-field-pm-current so a "
        "browser smoke can verify the assignment displays during "
        "candidate-list loading."
    )

    # Clearing the PM must send null (not empty string) — already
    # locked by test_pm_id_null_clears_assignment above for the
    # backend; this test makes sure the frontend save handler keeps
    # the null contract.
    assert "patch.pm_id = pmId || null;" in src, (
        "ProjectDetailPage save handler must send pm_id: null when "
        "the user clears the picker; the backend skips FK validation "
        "for null but rejects empty string."
    )
