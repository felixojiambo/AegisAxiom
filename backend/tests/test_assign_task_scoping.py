"""assign_task name resolution scoped to project members — v1.3 Theme B / Thread 1B.

v1.2 carry-forward: ``assign_task`` looked up the requested user across the
entire workspace. A workspace can contain dozens of Marcuses; the LLM had no
way to know which one the user meant, and the resolver would happily pick
the first match even if that user had no relationship to the project.

v1.3 scopes the lookup to project members (``pm_id`` + ``assigned_user_ids``):

  * 0 matches → ``{error: "not_found", message: ..., candidates: [...]}``
    where candidates is the actual project member list so the LLM can
    suggest a real person.
  * 1 match  → ``{user_id: ...}``
  * >1 match → ``{error: "ambiguous", candidates: [...]}``

Bonus: even when the LLM passes a raw user_id, we verify the user is on
the project before assigning. Hallucinated IDs should not slip through.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.ai.agents.tools.conversational_tools import (
    _handle_assign_task,
    _project_member_ids,
    _resolve_user_by_name_on_project,
)


# ---------------------------------------------------------------------------
# In-memory DB stubs
# ---------------------------------------------------------------------------

def _make_user(user_id="u_pm_grace"):
    return SimpleNamespace(
        user_id=user_id, email="grace@x.io", name="Grace", role="project_manager",
        permissions={"projects.task.assign"},
    )


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_kw):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, n):
        return list(self._docs[:n])


class _Coll:
    """A collection where `.find(query)` filters by simple predicates.

    Supports the predicates used by the assign_task code path:
      * ``user_id``: ``{"$in": [...]}``
      * ``$or``: list of ``{name|email: {$regex, $options}}``
      * ``project_id``: exact match (for projects collection find_one)
      * ``task_id``: exact match (for pm_tasks find_one)
    """

    def __init__(self, docs):
        self._docs = list(docs)

    async def find_one(self, query=None, projection=None, **_kw):
        query = query or {}
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                return d
        return None

    def find(self, query=None, projection=None, **_kw):
        query = query or {}
        out = []
        for d in self._docs:
            if not self._matches(d, query):
                continue
            out.append(d)
        return _Cursor(out)

    @staticmethod
    def _matches(doc: dict, query: dict) -> bool:
        for k, v in query.items():
            if k == "$or":
                if not any(_Coll._matches(doc, sub) for sub in v):
                    return False
                continue
            field_val = doc.get(k)
            if isinstance(v, dict):
                if "$in" in v:
                    if field_val not in v["$in"]:
                        return False
                if "$regex" in v:
                    import re
                    pattern = v["$regex"]
                    flags = re.IGNORECASE if "i" in (v.get("$options") or "") else 0
                    if not isinstance(field_val, str):
                        return False
                    if not re.search(pattern, field_val, flags):
                        return False
            else:
                if field_val != v:
                    return False
        return True


class _Db:
    def __init__(self, users=None, projects=None, pm_tasks=None):
        self._collections = {
            "users": _Coll(users or []),
            "projects": _Coll(projects or []),
            "pm_tasks": _Coll(pm_tasks or []),
        }
        # AI audit collection — track writes so error paths can be verified.
        self.audit_logs = _AuditColl()

    def __getitem__(self, key):
        if key not in self._collections:
            self._collections[key] = _Coll([])
        return self._collections[key]

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        if key == "audit_logs":
            # Should already be set as attribute, but fallback if not.
            return self.audit_logs
        return self[key]


class _AuditColl:
    def __init__(self):
        self.inserted: list = []

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return SimpleNamespace(inserted_id="stub")


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# _project_member_ids
# ---------------------------------------------------------------------------

class TestProjectMemberIds:
    def test_includes_pm_id_and_assigned_user_ids(self):
        db = _Db(projects=[{
            "project_id": "p1",
            "pm_id": "u_pm",
            "assigned_user_ids": ["u_a", "u_b"],
        }])
        members = run(_project_member_ids(db, "p1"))
        assert members == {"u_pm", "u_a", "u_b"}

    def test_missing_project_returns_empty(self):
        db = _Db(projects=[])
        members = run(_project_member_ids(db, "p_missing"))
        assert members == set()

    def test_project_with_no_assigned_users_just_pm(self):
        db = _Db(projects=[{"project_id": "p1", "pm_id": "u_pm"}])
        members = run(_project_member_ids(db, "p1"))
        assert members == {"u_pm"}


# ---------------------------------------------------------------------------
# _resolve_user_by_name_on_project
# ---------------------------------------------------------------------------

class TestResolveNameOnProject:
    def test_unique_member_match(self):
        db = _Db(
            users=[
                {"user_id": "u_marcus", "name": "Marcus Bell", "email": "m@x.io"},
                # Workspace user — but NOT on the project, must be filtered out.
                {"user_id": "u_other_marcus", "name": "Marcus Stone", "email": "ms@x.io"},
            ],
            projects=[{
                "project_id": "p1", "pm_id": "u_pm",
                "assigned_user_ids": ["u_marcus"],
            }],
        )
        result = run(_resolve_user_by_name_on_project(db, "Marcus", "p1"))
        assert result == {"user_id": "u_marcus"}

    def test_not_a_project_member_returns_not_found_with_member_candidates(self):
        """The other Marcus exists in the workspace but isn't on the project.
        We return not_found with the actual member list so the LLM can suggest
        someone real.
        """
        db = _Db(
            users=[
                {"user_id": "u_other_marcus", "name": "Marcus Stone", "email": "ms@x.io"},
                {"user_id": "u_pm", "name": "Grace Holloway", "email": "g@x.io"},
                {"user_id": "u_alice", "name": "Alice", "email": "a@x.io"},
            ],
            projects=[{
                "project_id": "p1", "pm_id": "u_pm",
                "assigned_user_ids": ["u_alice"],
            }],
        )
        result = run(_resolve_user_by_name_on_project(db, "Marcus", "p1"))
        assert result["error"] == "not_found"
        assert "Marcus" in result["message"]
        # The candidate list is the actual project member set, not workspace-wide.
        candidate_ids = {c["user_id"] for c in result["candidates"]}
        assert candidate_ids == {"u_pm", "u_alice"}

    def test_ambiguous_on_project_returns_candidates(self):
        db = _Db(
            users=[
                {"user_id": "u_g1", "name": "Grace H", "email": "g1@x.io"},
                {"user_id": "u_g2", "name": "Grace L", "email": "g2@x.io"},
            ],
            projects=[{
                "project_id": "p1", "pm_id": "u_pm",
                "assigned_user_ids": ["u_g1", "u_g2"],
            }],
        )
        result = run(_resolve_user_by_name_on_project(db, "Grace", "p1"))
        assert result["error"] == "ambiguous"
        assert len(result["candidates"]) == 2


# ---------------------------------------------------------------------------
# _handle_assign_task — full flow
# ---------------------------------------------------------------------------

class TestAssignTaskScoping:
    @pytest.mark.asyncio
    async def test_user_not_on_project_returns_not_found(self):
        db = _Db(
            users=[
                {"user_id": "u_other", "name": "Marcus", "email": "marcus@x.io"},
                {"user_id": "u_alice", "name": "Alice", "email": "a@x.io"},
            ],
            projects=[{
                "project_id": "p1", "pm_id": "u_pm",
                "assigned_user_ids": ["u_alice"],
            }],
            pm_tasks=[{"task_id": "task_1", "project_id": "p1"}],
        )
        user = _make_user()

        with patch("routes.projects_task.update_task", new=AsyncMock()) as mock_update:
            result = await _handle_assign_task(
                {"task_id": "task_1", "user_id_or_name": "Marcus"},
                db, user,
            )

        assert result["error"] == "not_found"
        mock_update.assert_not_called()
        # candidates should be the project member list, so the LLM can suggest.
        candidate_ids = {c["user_id"] for c in result.get("candidates", [])}
        assert "u_alice" in candidate_ids

    @pytest.mark.asyncio
    async def test_ambiguous_match_among_project_members(self):
        db = _Db(
            users=[
                {"user_id": "u_g1", "name": "Grace H", "email": "g1@x.io"},
                {"user_id": "u_g2", "name": "Grace L", "email": "g2@x.io"},
            ],
            projects=[{
                "project_id": "p1", "pm_id": "u_pm",
                "assigned_user_ids": ["u_g1", "u_g2"],
            }],
            pm_tasks=[{"task_id": "task_1", "project_id": "p1"}],
        )
        user = _make_user()

        with patch("routes.projects_task.update_task", new=AsyncMock()) as mock_update:
            result = await _handle_assign_task(
                {"task_id": "task_1", "user_id_or_name": "Grace"},
                db, user,
            )

        assert result["error"] == "ambiguous"
        mock_update.assert_not_called()
        assert len(result["candidates"]) == 2

    @pytest.mark.asyncio
    async def test_exact_match_on_project_assigns_successfully(self):
        db = _Db(
            users=[
                {"user_id": "u_marcus", "name": "Marcus Bell", "email": "m@x.io"},
            ],
            projects=[{
                "project_id": "p1", "pm_id": "u_pm",
                "assigned_user_ids": ["u_marcus"],
            }],
            pm_tasks=[{"task_id": "task_1", "project_id": "p1"}],
        )
        user = _make_user()

        async def _fake_update_task(task_id, payload, request, current_user):
            return {
                "task_id": task_id, "assignee_id": payload.assignee_id,
                "project_id": "p1",
            }

        with patch(
            "routes.projects_task.update_task",
            new=AsyncMock(side_effect=_fake_update_task),
        ) as mock_update:
            result = await _handle_assign_task(
                {"task_id": "task_1", "user_id_or_name": "Marcus"},
                db, user,
            )

        assert result.get("ok") is True
        assert result["assignee_id"] == "u_marcus"
        mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_raw_user_id_off_project_is_rejected(self):
        """Even when the LLM passes a UUID, we verify membership."""
        db = _Db(
            users=[
                {"user_id": "u_outsider", "name": "Outsider", "email": "o@x.io"},
                {"user_id": "u_alice", "name": "Alice", "email": "a@x.io"},
            ],
            projects=[{
                "project_id": "p1", "pm_id": "u_pm",
                "assigned_user_ids": ["u_alice"],
            }],
            pm_tasks=[{"task_id": "task_1", "project_id": "p1"}],
        )
        user = _make_user()

        with patch("routes.projects_task.update_task", new=AsyncMock()) as mock_update:
            result = await _handle_assign_task(
                {"task_id": "task_1", "user_id_or_name": "u_outsider_1234567890abcdef"},
                db, user,
            )

        assert result["error"] == "not_found"
        assert "not a member" in result["message"].lower()
        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_task_returns_not_found(self):
        db = _Db(pm_tasks=[])
        user = _make_user()

        with patch("routes.projects_task.update_task", new=AsyncMock()) as mock_update:
            result = await _handle_assign_task(
                {"task_id": "task_missing", "user_id_or_name": "Marcus"},
                db, user,
            )

        assert result["error"] == "not_found"
        assert "task_missing" in result["message"]
        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_unassign_with_empty_string_still_works(self):
        """Passing empty/None for user_id_or_name unassigns — no resolution needed."""
        db = _Db(
            pm_tasks=[{"task_id": "task_1", "project_id": "p1"}],
            projects=[{"project_id": "p1", "pm_id": "u_pm", "assigned_user_ids": []}],
        )
        user = _make_user()

        async def _fake_update_task(task_id, payload, request, current_user):
            return {"task_id": task_id, "assignee_id": payload.assignee_id}

        with patch(
            "routes.projects_task.update_task",
            new=AsyncMock(side_effect=_fake_update_task),
        ) as mock_update:
            result = await _handle_assign_task(
                {"task_id": "task_1", "user_id_or_name": ""},
                db, user,
            )

        assert result.get("ok") is True
        assert result["assignee_id"] is None
        mock_update.assert_called_once()
