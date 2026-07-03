"""Regression test for the projects dashboard against imported / null-heavy rows.

Locks the fix for the TypeError(can't compare offset-naive and offset-
aware datetimes) crash that hit /api/projects/dashboard/summary once
imported Zoho project rows landed. The imported rows stamp
``created_at`` as ``datetime.now(timezone.utc)`` (aware), but motor
returns BSON datetimes back as naive Python ``datetime`` objects, so
the comparison against the aware ``cutoff`` in ``_active_projects_count``
raised on every dashboard load.

The fix normalises ``created_dt`` to timezone-aware UTC before the
compare. This test pins that behavior with an in-memory async fake
so the dashboard contract is locked even when the real DB is not
present in CI.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production",
)

import pytest


_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# pylint: disable=wrong-import-position
from routes.projects_dashboard import _active_projects_count  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal async-cursor / async-collection / async-db fakes. The dashboard
# helpers only use ``.find(...).sort(...)``, ``.count_documents(...)``, and
# async iteration; the fakes cover exactly that surface.
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]):
        self._docs = docs

    def sort(self, _key, _direction=1):
        return self

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as e:
            raise StopAsyncIteration from e


class _FakeProjectsCollection:
    def __init__(self, docs: list[dict[str, Any]]):
        self.docs = docs

    def find(self, query=None, projection=None):
        if not query:
            return _FakeCursor(self.docs)
        # Only ``count_documents({"status": "active"})`` is used by the
        # helper; the find with non-empty query never fires here.
        return _FakeCursor(
            [d for d in self.docs if all(d.get(k) == v for k, v in query.items())]
        )

    async def count_documents(self, query):
        return sum(1 for d in self.docs if all(d.get(k) == v for k, v in query.items()))


class _FakeStatusHistoryCollection:
    def __init__(self, docs: list[dict[str, Any]] | None = None):
        self.docs = docs or []

    def find(self, query=None, projection=None):
        cutoff = (query or {}).get("changed_at", {}).get("$gte")
        rows = [
            d for d in self.docs
            if cutoff is None or d.get("changed_at") and d["changed_at"] >= cutoff
        ]
        return _FakeCursor(rows)


class _FakeDB:
    def __init__(
        self,
        projects: list[dict[str, Any]],
        history: list[dict[str, Any]] | None = None,
    ):
        self._projects = _FakeProjectsCollection(projects)
        self._history = _FakeStatusHistoryCollection(history)

    def __getitem__(self, name: str):
        if name == "projects":
            return self._projects
        if name == "pm_project_status_history":
            return self._history
        raise KeyError(name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_active_count_handles_naive_datetime_from_bson():
    """Motor returns BSON dates as NAIVE datetimes. The helper must
    not raise when comparing them against the aware cutoff."""
    now = datetime.now(timezone.utc)
    docs = [
        # Naive datetime — simulates a BSON value returned by motor.
        {"project_id": "p_naive", "status": "active",
         "created_at": datetime.utcnow() - timedelta(days=30)},
        # Aware datetime — simulates a row written with timezone.utc.
        {"project_id": "p_aware", "status": "active",
         "created_at": now - timedelta(days=20)},
        # ISO string with no tz — historical writer style.
        {"project_id": "p_iso", "status": "active",
         "created_at": "2024-01-01 00:00:00"},
    ]
    db = _FakeDB(docs)
    result = asyncio.run(_active_projects_count(db, now))
    assert result["count"] == 3
    # No prior status history -> status_then == status -> all active.
    assert result["delta_vs_last_week"] == 0


def test_active_count_handles_imported_zoho_rows_with_null_fields():
    """Imported Zoho projects land with status='planning', null
    pm_id / assigned_user_ids / dates. The dashboard must not crash
    and must classify them correctly (status='planning' != 'active'
    so they don't count toward the active total)."""
    now = datetime.now(timezone.utc)
    imported = [
        {
            "project_id": f"proj_imp_{i}",
            "status": "planning",
            "project_type": "waterfall",
            "service_line": "other",
            "pm_id": None,
            "assigned_user_ids": [],
            "account_id": None,
            "start_date": None,
            "target_end_date": None,
            "actual_end_date": None,
            # Imported rows write created_at as datetime.now(timezone.utc)
            # then motor returns it naive — mirror that.
            "created_at": (datetime.utcnow() - timedelta(days=2)),
            "import_source": "zoho",
        }
        for i in range(50)
    ]
    # Plus one truly active project.
    imported.append({
        "project_id": "proj_real_active",
        "status": "active",
        "created_at": datetime.utcnow() - timedelta(days=14),
    })
    db = _FakeDB(imported)
    result = asyncio.run(_active_projects_count(db, now))
    # Only the genuine active project counts; imported planning rows
    # never flip into the active total.
    assert result["count"] == 1
    assert result["delta_vs_last_week"] == 0


def test_active_count_excludes_projects_created_after_cutoff():
    """The 'how many were active 7 days ago' baseline must drop rows
    that didn't yet exist back then. With imported rows now using
    timezone.utc-aware datetimes through motor (naive on read), the
    cutoff math must still work."""
    now = datetime.now(timezone.utc)
    docs = [
        # Created today — didn't exist 7 days ago.
        {"project_id": "p_new", "status": "active",
         "created_at": datetime.utcnow()},
        # Created 30 days ago.
        {"project_id": "p_old_active", "status": "active",
         "created_at": datetime.utcnow() - timedelta(days=30)},
        # Created 30 days ago, currently planning.
        {"project_id": "p_old_planning", "status": "planning",
         "created_at": datetime.utcnow() - timedelta(days=30)},
    ]
    db = _FakeDB(docs)
    result = asyncio.run(_active_projects_count(db, now))
    assert result["count"] == 2  # both currently-active rows
    # p_new didn't exist 7 days ago. p_old_active was active then. So
    # active_then == 1 -> delta = 2 - 1 = 1.
    assert result["delta_vs_last_week"] == 1


def test_active_count_handles_missing_created_at():
    """A row with no created_at field at all must not crash the
    dashboard (the helper assumes such rows existed at the cutoff)."""
    now = datetime.now(timezone.utc)
    docs = [{"project_id": "p_no_created", "status": "active"}]
    db = _FakeDB(docs)
    result = asyncio.run(_active_projects_count(db, now))
    assert result["count"] == 1
    # No created_at -> the row is treated as existing at cutoff;
    # status_then defaults to current status -> active.
    assert result["delta_vs_last_week"] == 0


def test_active_count_with_no_projects_returns_zero():
    """Empty projects collection — no crash, all-zero result."""
    db = _FakeDB([])
    result = asyncio.run(_active_projects_count(db, datetime.now(timezone.utc)))
    assert result == {"count": 0, "delta_vs_last_week": 0}
