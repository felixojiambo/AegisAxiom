"""Tests for the v1.3 server-side smart-view filter builder + endpoint.

Two test suites:

  1. `TestBuildFilterQuery` — pure unit tests against
     `services.projects_task_filter.build_filter_query`. No DB, no FastAPI;
     just dict assertions. Fast and deterministic with an injected `now`.

  2. `TestListTasksFilterEndpoint` — small integration set against the
     `GET /api/projects/tasks` route with a mocked db. Verifies the new
     query params flow into the Mongo filter, that `X-Total-Count` is
     emitted, and that pre-v1.3 callers (no new params) still get
     identical behavior.

Thread 1C of Projects v1.3 Wave 1. Theme C — 500-task filter cap fix.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.projects_task_filter import (
    ME_SENTINEL,
    TERMINAL_STATES,
    UNASSIGNED_SENTINEL,
    build_filter_query,
    parse_comma_list,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PROJECT_ID = "proj_A"
USER_ID = "u_001"
# Pinned "today" used across the unit tests so date math is deterministic.
NOW = datetime(2026, 5, 11, 14, 30, 0, tzinfo=timezone.utc)


def _today() -> str:
    return "2026-05-11"


def _plus(days: int) -> str:
    from datetime import timedelta
    return (datetime(2026, 5, 11, tzinfo=timezone.utc)
            + timedelta(days=days)).strftime("%Y-%m-%d")


# ===========================================================================
# Unit tests — build_filter_query
# ===========================================================================
class TestBuildFilterQuery:
    """Pure-function tests. No Mongo, no FastAPI."""

    def test_no_filters_returns_just_project_scope(self):
        q = build_filter_query(PROJECT_ID, USER_ID, now=NOW)
        assert q == {"project_id": PROJECT_ID}

    def test_assignee_id_literal(self):
        q = build_filter_query(PROJECT_ID, USER_ID, assignee_id="u_other", now=NOW)
        assert q["assignee_id"] == "u_other"

    def test_assignee_id_me_sentinel(self):
        q = build_filter_query(PROJECT_ID, USER_ID, assignee_id=ME_SENTINEL, now=NOW)
        assert q["assignee_id"] == USER_ID

    def test_assignee_id_unassigned_sentinel(self):
        q = build_filter_query(
            PROJECT_ID, USER_ID, assignee_id=UNASSIGNED_SENTINEL, now=NOW
        )
        # Match Mongo-stored null/missing.
        assert q["assignee_id"] is None

    def test_status_in_single(self):
        q = build_filter_query(
            PROJECT_ID, USER_ID, status_in=["in_progress"], now=NOW
        )
        assert q["status"] == {"$in": ["in_progress"]}

    def test_status_in_multi(self):
        q = build_filter_query(
            PROJECT_ID, USER_ID, status_in=["in_progress", "blocked"], now=NOW
        )
        assert q["status"] == {"$in": ["in_progress", "blocked"]}

    def test_priority_in(self):
        q = build_filter_query(
            PROJECT_ID, USER_ID, priority_in=["high", "critical"], now=NOW
        )
        assert q["priority"] == {"$in": ["high", "critical"]}

    def test_is_blocked_true(self):
        q = build_filter_query(PROJECT_ID, USER_ID, is_blocked=True, now=NOW)
        assert q["status"] == "blocked"

    def test_is_blocked_false(self):
        q = build_filter_query(PROJECT_ID, USER_ID, is_blocked=False, now=NOW)
        assert q["status"] == {"$ne": "blocked"}

    def test_due_within_days_excludes_terminals(self):
        q = build_filter_query(
            PROJECT_ID, USER_ID, due_within_days=7, now=NOW
        )
        # Two constraints share `status`: terminals-exclusion. And due_date set.
        # Both fold into the top-level dict because only one constraint exists
        # for each field.
        assert q["due_date"] == {"$gte": _today(), "$lte": _plus(7)}
        assert q["status"] == {"$nin": sorted(TERMINAL_STATES)}

    def test_due_within_days_zero_is_today_only(self):
        q = build_filter_query(
            PROJECT_ID, USER_ID, due_within_days=0, now=NOW
        )
        assert q["due_date"] == {"$gte": _today(), "$lte": _today()}

    def test_is_overdue_uses_lt_today_and_excludes_terminals(self):
        q = build_filter_query(PROJECT_ID, USER_ID, is_overdue=True, now=NOW)
        assert q["due_date"] == {"$lt": _today()}
        assert q["status"] == {"$nin": sorted(TERMINAL_STATES)}

    def test_is_at_risk_window_is_zero_to_three_days(self):
        q = build_filter_query(PROJECT_ID, USER_ID, is_at_risk=True, now=NOW)
        # [today, today+3) — $gte today, $lt today+3
        assert q["due_date"] == {"$gte": _today(), "$lt": _plus(3)}
        assert q["status"] == {"$nin": sorted(TERMINAL_STATES)}

    def test_terminal_states_value(self):
        # Locks the set we mirror from frontend predicates.js DONE_STATUSES.
        assert TERMINAL_STATES == frozenset(
            {
                "complete",
                "completed",
                "done",
                "shipped",
                "closed",
                "cancelled",
                "canceled",
            }
        )

    def test_created_after_uses_iso_string(self):
        ts = datetime(2026, 4, 1, tzinfo=timezone.utc)
        q = build_filter_query(PROJECT_ID, USER_ID, created_after=ts, now=NOW)
        assert q["created_at"] == {"$gt": "2026-04-01T00:00:00+00:00"}

    def test_updated_after_uses_iso_string(self):
        ts = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        q = build_filter_query(PROJECT_ID, USER_ID, updated_after=ts, now=NOW)
        assert q["updated_at"] == {"$gt": "2026-04-01T12:00:00+00:00"}

    def test_search_targets_title_and_description_case_insensitive(self):
        q = build_filter_query(PROJECT_ID, USER_ID, search="hello", now=NOW)
        assert q["$or"] == [
            {"title": {"$regex": "hello", "$options": "i"}},
            {"description": {"$regex": "hello", "$options": "i"}},
        ]

    def test_search_escapes_regex_metachars(self):
        # `.` and `*` must be literal; otherwise a malicious search="(.*)"
        # could DOS or pull too many rows.
        q = build_filter_query(PROJECT_ID, USER_ID, search="a.b*c", now=NOW)
        title_clause = q["$or"][0]["title"]
        assert title_clause["$regex"] == "a\\.b\\*c"

    def test_multiple_filters_combine_with_and(self):
        # No conflicting fields here — all should land at top level.
        q = build_filter_query(
            PROJECT_ID,
            USER_ID,
            assignee_id=ME_SENTINEL,
            priority_in=["high"],
            is_overdue=True,
            now=NOW,
        )
        assert q["project_id"] == PROJECT_ID
        assert q["assignee_id"] == USER_ID
        assert q["priority"] == {"$in": ["high"]}
        assert q["due_date"] == {"$lt": _today()}
        assert q["status"] == {"$nin": sorted(TERMINAL_STATES)}

    def test_conflicting_status_constraints_fold_into_and(self):
        # is_blocked=True (status == "blocked") + status_in (["in_progress"])
        # both target `status` — must not silently clobber.
        q = build_filter_query(
            PROJECT_ID,
            USER_ID,
            is_blocked=True,
            status_in=["in_progress"],
            now=NOW,
        )
        # Either: a top-level `status` is absent and both clauses are in
        # `$and`, OR they're merged some other way. The contract is "both
        # constraints are applied" — assert that.
        and_clauses = q.get("$and", [])
        statuses_constrained = [c for c in and_clauses if "status" in c]
        if "status" in q:
            # If folded to top-level, only one survived — bug.
            assert False, f"status was clobbered: {q}"
        # Both constraints present in $and.
        assert {"status": "blocked"} in statuses_constrained
        assert {"status": {"$in": ["in_progress"]}} in statuses_constrained

    def test_overdue_and_due_within_days_fold_into_and(self):
        # Both target `due_date`. Should not clobber.
        q = build_filter_query(
            PROJECT_ID,
            USER_ID,
            is_overdue=True,
            due_within_days=7,
            now=NOW,
        )
        and_clauses = q.get("$and", [])
        assert any("due_date" in c and c["due_date"] == {"$lt": _today()}
                   for c in and_clauses)
        assert any("due_date" in c and c["due_date"] == {
            "$gte": _today(), "$lte": _plus(7)
        } for c in and_clauses)

    def test_now_defaults_to_utc_now_when_omitted(self):
        # Smoke test — no crash, project_id round-trips, no NOW injected.
        q = build_filter_query(PROJECT_ID, USER_ID, is_overdue=True)
        assert q["project_id"] == PROJECT_ID
        assert "$lt" in q["due_date"]

    def test_naive_datetime_treated_as_utc(self):
        naive = datetime(2026, 4, 1, 12, 0, 0)
        q = build_filter_query(
            PROJECT_ID, USER_ID, created_after=naive, now=NOW
        )
        # Output must carry tz offset; naive input gets coerced to UTC.
        assert q["created_at"]["$gt"].endswith("+00:00")


# ---------------------------------------------------------------------------
# parse_comma_list
# ---------------------------------------------------------------------------
class TestParseCommaList:
    def test_none_returns_none(self):
        assert parse_comma_list(None) is None

    def test_empty_string_returns_none(self):
        assert parse_comma_list("") is None

    def test_whitespace_only_returns_none(self):
        assert parse_comma_list(" , , ") is None

    def test_single_value(self):
        assert parse_comma_list("high") == ["high"]

    def test_multi_with_spaces(self):
        assert parse_comma_list("high, critical , low") == [
            "high",
            "critical",
            "low",
        ]


# ===========================================================================
# Integration tests — GET /api/projects/tasks with smart-view filters
# ===========================================================================

DEFAULT_PERMS = ["projects.task.view"]


def _make_user(user_id=USER_ID, permissions=None):
    return SimpleNamespace(
        user_id=user_id,
        email="tester@aegisaxiom.io",
        role="pm",
        permissions=list(DEFAULT_PERMS) if permissions is None else permissions,
    )


def _make_task(task_id, **overrides):
    base = {
        "task_id": task_id,
        "project_id": PROJECT_ID,
        "title": f"Task {task_id}",
        "description": "",
        "status": "not_started",
        "priority": "medium",
        "assignee_id": None,
        "due_date": None,
        "methodology_fields": {},
        "labels": [],
        "created_at": "2026-05-01T12:00:00+00:00",
        "updated_at": "2026-05-01T12:00:00+00:00",
    }
    base.update(overrides)
    return base


def _in_memory_tasks_collection(tasks):
    """Mock pm_tasks collection with enough fidelity for find/count
    against the smart-view filter shapes our builder emits."""
    store = [dict(t) for t in tasks]

    def matches(doc, query):
        if not isinstance(query, dict):
            return False
        for k, v in query.items():
            if k == "$and":
                if not all(matches(doc, sub) for sub in v):
                    return False
                continue
            if k == "$or":
                if not any(matches(doc, sub) for sub in v):
                    return False
                continue
            if isinstance(v, dict) and any(op.startswith("$") for op in v):
                actual = doc.get(k)
                for op, expected in v.items():
                    if op == "$in":
                        if actual not in expected:
                            return False
                    elif op == "$nin":
                        if actual in expected:
                            return False
                    elif op == "$ne":
                        if actual == expected:
                            return False
                    elif op == "$gt":
                        if actual is None or not actual > expected:
                            return False
                    elif op == "$gte":
                        if actual is None or not actual >= expected:
                            return False
                    elif op == "$lt":
                        if actual is None or not actual < expected:
                            return False
                    elif op == "$lte":
                        if actual is None or not actual <= expected:
                            return False
                    elif op == "$regex":
                        import re as _re
                        flags = _re.IGNORECASE if "i" in v.get(
                            "$options", ""
                        ) else 0
                        if actual is None:
                            return False
                        if not _re.search(expected, actual, flags):
                            return False
                    elif op == "$options":
                        pass
                    else:
                        return False
            else:
                if doc.get(k) != v:
                    return False
        return True

    coll = MagicMock()

    async def _count(query):
        return sum(1 for d in store if matches(d, query))

    coll.count_documents = AsyncMock(side_effect=_count)

    class _Cursor:
        def __init__(self, results):
            self._results = results

        def sort(self, *_a, **_k):
            return self

        def skip(self, n):
            self._results = self._results[n:]
            return self

        def limit(self, n):
            self._results = self._results[:n]
            return self

        async def to_list(self, _n):
            return self._results

    def _find(query, projection=None):
        results = [
            {k: v for k, v in d.items() if k != "_id"}
            for d in store
            if matches(d, query)
        ]
        return _Cursor(results)

    coll.find = MagicMock(side_effect=_find)
    return coll


def _build_app_with_db(user, tasks):
    from auth_helper import get_current_user
    from routes.projects_task import router

    tasks_coll = _in_memory_tasks_collection(tasks)
    db = MagicMock()
    db.__getitem__ = MagicMock(
        side_effect=lambda name: tasks_coll if name == "pm_tasks" else MagicMock()
    )

    app = FastAPI()
    app.include_router(router, prefix="/api/projects")
    app.dependency_overrides[get_current_user] = lambda: user
    return app, db


class TestListTasksFilterEndpoint:
    """A few targeted endpoint tests to cover wiring + headers."""

    def test_backwards_compatible_no_new_params(self):
        # Pre-v1.3 caller: project_id only. Behavior must be identical.
        user = _make_user()
        tasks = [
            _make_task("t1", status="in_progress"),
            _make_task("t2", status="complete"),
        ]
        app, db = _build_app_with_db(user, tasks)
        with patch("routes.projects_task.get_db", return_value=db):
            resp = TestClient(app).get(
                "/api/projects/tasks", params={"project_id": PROJECT_ID}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

    def test_smart_view_filter_is_overdue_excludes_terminals(self):
        user = _make_user()
        tasks = [
            # Overdue + active -> included
            _make_task("t1", due_date="2026-05-01", status="in_progress"),
            # Overdue but complete -> excluded
            _make_task("t2", due_date="2026-05-01", status="complete"),
            # Future due -> excluded
            _make_task("t3", due_date="2026-12-01", status="in_progress"),
            # No due date -> excluded
            _make_task("t4", due_date=None, status="in_progress"),
        ]
        app, db = _build_app_with_db(user, tasks)
        # Freeze "today" inside the builder to 2026-05-11.
        with patch("routes.projects_task.get_db", return_value=db), \
             patch(
                "services.projects_task_filter._today_utc_midnight",
                return_value=datetime(2026, 5, 11, tzinfo=timezone.utc),
            ):
            resp = TestClient(app).get(
                "/api/projects/tasks",
                params={"project_id": PROJECT_ID, "is_overdue": "true"},
            )
        assert resp.status_code == 200
        body = resp.json()
        item_ids = {t["task_id"] for t in body["items"]}
        assert item_ids == {"t1"}
        # X-Total-Count must reflect the filtered total.
        assert resp.headers.get("X-Total-Count") == "1"

    def test_smart_view_filter_me_sentinel_resolves_to_caller(self):
        user = _make_user(user_id=USER_ID)
        tasks = [
            _make_task("t1", assignee_id=USER_ID),
            _make_task("t2", assignee_id="u_other"),
            _make_task("t3", assignee_id=None),
        ]
        app, db = _build_app_with_db(user, tasks)
        with patch("routes.projects_task.get_db", return_value=db):
            resp = TestClient(app).get(
                "/api/projects/tasks",
                params={
                    "project_id": PROJECT_ID,
                    "assignee_id": ME_SENTINEL,
                    # Force smart-view path: pair with another filter to make
                    # sure ME sentinel gets resolved by build_filter_query
                    # too, not just the legacy branch.
                    "priority_in": "medium",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        item_ids = {t["task_id"] for t in body["items"]}
        assert item_ids == {"t1"}

    def test_smart_view_filter_unassigned_sentinel(self):
        user = _make_user()
        tasks = [
            _make_task("t1", assignee_id=USER_ID),
            _make_task("t2", assignee_id=None),
            _make_task("t3", assignee_id=None),
        ]
        app, db = _build_app_with_db(user, tasks)
        with patch("routes.projects_task.get_db", return_value=db):
            resp = TestClient(app).get(
                "/api/projects/tasks",
                params={
                    "project_id": PROJECT_ID,
                    "assignee_id": UNASSIGNED_SENTINEL,
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        item_ids = {t["task_id"] for t in body["items"]}
        assert item_ids == {"t2", "t3"}
        assert resp.headers.get("X-Total-Count") == "2"

    def test_invalid_priority_in_value_returns_422(self):
        user = _make_user()
        app, db = _build_app_with_db(user, [])
        with patch("routes.projects_task.get_db", return_value=db):
            resp = TestClient(app).get(
                "/api/projects/tasks",
                params={
                    "project_id": PROJECT_ID,
                    "priority_in": "high,bogus",
                },
            )
        # _validate_enum raises 422.
        assert resp.status_code == 422
