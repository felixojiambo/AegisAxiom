"""Integration: PMO AI write tool → audit log → recent-changes pipeline.

Tests the full flow: execute a PMO write tool → assert audit_logs row written
with correct shape → assert recent-changes endpoint sees the change.

Two scenarios:
  1. update_task_status — Tier 1, uses _handle_update_task_status directly,
     then queries fetch_recent_changes to verify the row surfaces.
  2. assign_task — Tier 1, user-name resolved to UUID first, then route called,
     audit row written, recent-changes row returned with pmo_ai actor_type.

The tests patch only the downstream route handlers (projects_task.update_task,
projects_task.update_task) and the perm gate; everything else (audit write,
recent-changes normalisation) runs real code.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.ai.agents.tools.conversational_tools import (
    _handle_update_task_status,
    _handle_assign_task,
)
from routes.projects_recent_changes import fetch_recent_changes


# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------

def _make_user(user_id="u_pm_grace", name="Grace Holloway", email="grace@x.io"):
    return SimpleNamespace(
        user_id=user_id, name=name, email=email,
        role="project_manager", permissions=set(),
        _perm_closure=set(),
    )


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_kw):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, _n):
        return list(self._docs)

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class _Collection:
    """In-memory collection supporting find/find_one/insert_one."""

    def __init__(self, docs=None):
        self._store: list[dict] = list(docs or [])
        self.inserted: list[dict] = []

    async def insert_one(self, doc):
        self._store.append(doc)
        self.inserted.append(doc)
        return SimpleNamespace(inserted_id="stub_id")

    async def find_one(self, query=None, projection=None, **_kw):
        return self._store[0] if self._store else None

    def find(self, query=None, projection=None, **_kw):
        return _Cursor(self._store)

    async def count_documents(self, query):
        return len(self._store)


class _Db:
    def __init__(self, audit_docs=None, user_docs=None, project_docs=None, task_docs=None):
        self.audit_logs = _Collection(audit_docs)
        self.users = _Collection(user_docs)
        self.projects = _Collection(project_docs)
        self.pm_tasks = _Collection(task_docs)

    def __getitem__(self, key):
        return getattr(self, key, _Collection())

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        return _Collection()


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Test 1: update_task_status → audit row written → recent-changes sees it
# ---------------------------------------------------------------------------

class TestUpdateTaskStatusE2E:
    """Full write-tool → audit → recent-changes pipeline for status update."""

    def test_status_update_no_separate_handler_audit_row_on_success(self, monkeypatch):
        """v1.3 Theme B: single-row audit.

        On success the handler no longer writes a separate
        ``event_category="ai_action"`` row — the activity row produced by
        crud_envelope is stamped with ``actor_type=pmo_ai`` + ``tool_name``
        via the ``ai_actor_ctx`` contextvar (set in execute_tool). In this
        test crud_envelope itself is not exercised (update_task is patched),
        so no audit rows should be written at all.
        """
        user = _make_user()

        audit_docs: list[dict] = []

        class _AuditColl(_Collection):
            async def insert_one(self, doc):
                audit_docs.append(doc)
                self._store.append(doc)
                return SimpleNamespace(inserted_id="ai_audit")

        db = _Db()
        db.audit_logs = _AuditColl()

        async def _fake_update_task(task_id, payload, request, current_user):
            return {"task_id": task_id, "status": payload.status, "project_id": "proj_henderson"}

        with patch("routes.projects_task.update_task", new=AsyncMock(side_effect=_fake_update_task)):
            result = run(_handle_update_task_status(
                {"task_id": "task_42", "status": "blocked"},
                db, user, session_id="chat_99",
            ))

        assert result["ok"] is True
        assert result["tier"] == 1
        # Single-row contract: zero handler-written rows on success path.
        assert len(audit_docs) == 0

    def test_status_update_audit_row_on_route_failure(self):
        """A failed route still writes an error audit row (event_category=ai_action)."""
        from fastapi import HTTPException

        user = _make_user()
        db = _Db()

        with patch(
            "routes.projects_task.update_task",
            new=AsyncMock(side_effect=HTTPException(status_code=404, detail="Task not found")),
        ):
            result = run(_handle_update_task_status(
                {"task_id": "task_missing", "status": "complete"},
                db, user,
            ))

        assert result["error"] == "update_failed"
        assert len(db.audit_logs.inserted) == 1
        err_row = db.audit_logs.inserted[0]
        assert err_row["event_category"] == "ai_action"
        assert err_row["result_status"] == "error"
        assert "Task not found" in (err_row["error_detail"] or "")

    def test_recent_changes_surfaces_ai_action_audit_row(self, monkeypatch):
        """Audit rows written by a PMO AI tool appear in fetch_recent_changes output."""
        from routes import projects_recent_changes as mod
        monkeypatch.setattr(mod, "has_perm", lambda u, p: True)

        now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)

        # Simulate the audit_log doc shape that _audit_pmo_ai_action would write,
        # then wrapped in the normalized structure that _normalize_audit_row expects.
        ai_audit_row = {
            "audit_id": "audit_ai_test_001",
            "event_category": "ai_action",
            "event_type": "ingested_app.status_changed",
            "action": "status_changed",
            "resource_type": "projects.task",
            "resource_id": "task_42",
            "actor_id": "u_pm_grace",
            "actor_email": "grace@x.io",
            "details": {
                "module": "projects",
                "entity": "task",
                "action": "status_changed",
                "request_id": "req_ai_99",
                "actor_type": "pmo_ai",
                "tool_name": "update_task_status",
                "diff": {"status": {"before": "in_progress", "after": "blocked"}},
                "after": {"project_id": "proj_henderson"},
            },
            "timestamp": now - timedelta(minutes=30),
        }

        db = _Db(
            audit_docs=[ai_audit_row],
            user_docs=[{"user_id": "u_pm_grace", "name": "Grace Holloway", "email": "grace@x.io"}],
            project_docs=[{"project_id": "proj_henderson", "name": "Henderson Q3"}],
            task_docs=[{"task_id": "task_42", "task_number": 42, "title": "Stand up dashboards"}],
        )

        user = _make_user()
        rows = run(fetch_recent_changes(db, user, hours=24, limit=50, now=now))

        assert len(rows) == 1
        r = rows[0]
        assert r["actor_type"] == "pmo_ai"
        assert r["tool_name"] == "update_task_status"
        assert r["entity_id"] == "task_42"
        assert r["action"] == "status_changed"
        # Summary must identify it as a PMO AI action
        assert "@aegisaxiom-ai" in r["summary"]
        assert "@Grace Holloway" in r["summary"] or "grace" in r["summary"].lower()


# ---------------------------------------------------------------------------
# Test 2: assign_task — name resolves, route called, audit row captured
# ---------------------------------------------------------------------------

class TestAssignTaskE2E:
    """Full pipeline: assign_task with name resolution → audit row → shape check."""

    def test_assign_task_single_match_calls_route(self):
        """v1.3 Theme B: when name resolves to a single project member, route
        is called. The single-row audit comes from crud_envelope (not
        exercised here), so the handler itself writes zero rows on success.
        """
        user = _make_user()

        # Project has Marcus as a member; user lookup returns him.
        db = _Db(
            user_docs=[{"user_id": "u_marcus", "name": "Marcus Bell", "email": "marcus@x.io"}],
            project_docs=[{
                "project_id": "proj_henderson",
                "pm_id": "u_pm_grace",
                "assigned_user_ids": ["u_marcus"],
            }],
            task_docs=[{"task_id": "task_87", "project_id": "proj_henderson"}],
        )

        async def _fake_update_task(task_id, payload, request, current_user):
            return {
                "task_id": task_id,
                "assignee_id": payload.assignee_id,
                "project_id": "proj_henderson",
            }

        with patch("routes.projects_task.update_task", new=AsyncMock(side_effect=_fake_update_task)):
            result = run(_handle_assign_task(
                {"task_id": "task_87", "user_id_or_name": "Marcus"},
                db, user, session_id="chat_77",
            ))

        assert result.get("ok") is True
        assert result["tier"] == 1
        # v1.3: no handler-written audit row on success.
        assert len(db.audit_logs.inserted) == 0

    def test_assign_task_ambiguous_name_does_not_call_route(self):
        """Ambiguous name resolution short-circuits before calling update_task.

        Both Graces are project members so the scope doesn't narrow the
        ambiguity. The implementation writes an error audit row (for
        observability) but must NOT call the update_task route.
        """
        user = _make_user()

        db = _Db(
            user_docs=[
                {"user_id": "u_g1", "name": "Grace H", "email": "g1@x.io"},
                {"user_id": "u_g2", "name": "Grace L", "email": "g2@x.io"},
            ],
            project_docs=[{
                "project_id": "proj_x",
                "pm_id": "u_pm",
                "assigned_user_ids": ["u_g1", "u_g2"],
            }],
            task_docs=[{"task_id": "task_87", "project_id": "proj_x"}],
        )

        with patch("routes.projects_task.update_task", new=AsyncMock()) as mock_update:
            result = run(_handle_assign_task(
                {"task_id": "task_87", "user_id_or_name": "Grace"},
                db, user,
            ))

        assert result["error"] == "ambiguous"
        mock_update.assert_not_called()
        assert len(db.audit_logs.inserted) == 1
        row = db.audit_logs.inserted[0]
        assert row["result_status"] == "error"
        assert row["tool_name"] == "assign_task"
        assert row["error_detail"] == "ambiguous"

    def test_assign_task_not_found_does_not_call_route(self):
        """Unknown name returns not_found; update_task is NOT called."""
        user = _make_user()
        # Task exists but the project has no members matching "Nobody".
        db = _Db(
            user_docs=[],
            project_docs=[{
                "project_id": "proj_x",
                "pm_id": "u_pm",
                "assigned_user_ids": [],
            }],
            task_docs=[{"task_id": "task_87", "project_id": "proj_x"}],
        )

        with patch("routes.projects_task.update_task", new=AsyncMock()) as mock_update:
            result = run(_handle_assign_task(
                {"task_id": "task_87", "user_id_or_name": "Nobody"},
                db, user,
            ))

        assert result["error"] == "not_found"
        mock_update.assert_not_called()
        assert len(db.audit_logs.inserted) == 1
        assert db.audit_logs.inserted[0]["result_status"] == "error"
        assert db.audit_logs.inserted[0]["error_detail"] == "not_found"
