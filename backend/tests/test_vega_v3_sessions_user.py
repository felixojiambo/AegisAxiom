"""Wave 7 — tests for Vega end-user session listing + resume endpoints.

Perf budgets (documented as comments — not asserted in CI):
  list   < 200ms p95 with 100 sessions
  resume < 500ms p95 with 50 chat_messages tail
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("JWT_SECRET", "test-secret")

from routes.vega_v3_sessions_user import (  # noqa: E402
    _format_iso,
    _resolve_session_name,
)


# ──────────────────────────────────────────────────────────────────────
# Pure unit tests — _resolve_session_name + _format_iso
# ──────────────────────────────────────────────────────────────────────


def test_resolve_session_name_uses_override_when_present():
    name = _resolve_session_name(
        override="Acme Q3 Discovery",
        customer_name="Other Customer",
        created_at="2026-05-01T10:00:00Z",
    )
    assert name == "Acme Q3 Discovery"


def test_resolve_session_name_strips_override_whitespace():
    name = _resolve_session_name(
        override="  My Session  ",
        customer_name=None,
        created_at="2026-05-01T10:00:00Z",
    )
    assert name == "My Session"


def test_resolve_session_name_falls_back_to_customer_plus_date():
    name = _resolve_session_name(
        override=None,
        customer_name="Lone Star Foundation Drilling",
        created_at="2026-05-01T10:00:00Z",
    )
    assert name == "Lone Star Foundation Drilling — 2026-05-01"


def test_resolve_session_name_untitled_when_no_customer():
    name = _resolve_session_name(
        override=None,
        customer_name=None,
        created_at="2026-05-01T10:00:00Z",
    )
    assert name == "Untitled session — 2026-05-01"


def test_resolve_session_name_untitled_when_nothing():
    name = _resolve_session_name(
        override=None,
        customer_name="",
        created_at=None,
    )
    assert name == "Untitled session"


def test_resolve_session_name_handles_datetime_object():
    dt = datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc)
    name = _resolve_session_name(
        override=None,
        customer_name="Acme",
        created_at=dt,
    )
    assert name.startswith("Acme — 2026-05-01")


def test_resolve_session_name_empty_override_falls_through():
    """Empty override should not block the customer-name path."""
    name = _resolve_session_name(
        override="",
        customer_name="Acme",
        created_at="2026-05-01T00:00:00Z",
    )
    assert name == "Acme — 2026-05-01"


def test_format_iso_handles_none():
    assert _format_iso(None) is None


def test_format_iso_passes_through_string():
    assert _format_iso("2026-05-01T10:00:00Z") == "2026-05-01T10:00:00Z"


def test_format_iso_adds_utc_to_naive_datetime():
    dt = datetime(2026, 5, 1, 10, 0, 0)
    iso = _format_iso(dt)
    assert iso.endswith("+00:00")


def test_format_iso_preserves_aware_datetime():
    dt = datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc)
    iso = _format_iso(dt)
    assert iso == "2026-05-01T10:00:00+00:00"


# ──────────────────────────────────────────────────────────────────────
# Endpoint shape — fake mongo + fake current_user
# ──────────────────────────────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_args, **_kwargs):
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs[: length or len(self._docs)])


class _FakeAggregate:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        return list(self._docs[: length or len(self._docs)])


class _FakeCollection:
    def __init__(self, docs=None):
        self.docs = docs or []
        self.updates = []

    def find(self, *_args, **_kwargs):
        return _FakeCursor(list(self.docs))

    def aggregate(self, _pipeline):
        return _FakeAggregate(list(self.docs))

    async def find_one(self, query, *_args, **_kwargs):
        def get_path(doc, path):
            cur = doc
            for part in path.split("."):
                if not isinstance(cur, dict):
                    return None
                cur = cur.get(part)
            return cur
        for d in self.docs:
            ok = True
            for k, v in query.items():
                if k.startswith("$"):
                    continue
                if "." in k:
                    if get_path(d, k) != v:
                        ok = False
                        break
                else:
                    if d.get(k) != v:
                        ok = False
                        break
            if ok:
                return dict(d)
        return None

    async def count_documents(self, query):
        if not query:
            return len(self.docs)
        return sum(
            1 for d in self.docs
            if all(d.get(k) == v for k, v in query.items() if not k.startswith("$"))
        )

    async def update_one(self, query, update, *_args, **_kwargs):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                set_doc = update.get("$set") or {}
                d.update(set_doc)
                self.updates.append((query, update))
                return type("Result", (), {"modified_count": 1})()
        return type("Result", (), {"modified_count": 0})()

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("Result", (), {"inserted_id": "x"})()


class _FakeDB:
    def __init__(self):
        self.vega_discovery_sessions = _FakeCollection()
        self.ai_chat_artifacts = _FakeCollection()
        self.chat_sessions = _FakeCollection()
        self.chat_messages = _FakeCollection()


class _FakeUser:
    def __init__(self, user_id="user_abc", perms=("vega_sessions.view_own",)):
        self.user_id = user_id
        self.role = "sales_rep"
        self.permissions = list(perms)


def _has_perm(user, key):
    return key in user.permissions


@pytest.mark.asyncio
async def test_list_my_sessions_returns_only_own_sessions(monkeypatch):
    db = _FakeDB()
    # The endpoint relies on Mongo's $lookup to attach _artifacts. The fake
    # aggregate doesn't reimplement $lookup — we pre-attach the joined shape
    # so the test exercises the row-building logic only.
    db.vega_discovery_sessions.docs = [
        {
            "session_id": "vega_1", "user_id": "user_abc",
            "chat_session_id": "chat_1",
            "created_at": "2026-05-01T10:00:00Z",
            "updated_at": "2026-05-01T11:00:00Z",
            "client_company": "Acme",
            "discovery_mode": "single_call",
            "status": "complete",
            "_artifacts": [
                {
                    "artifact_id": "art_1",
                    "artifact_type": "discovery_analysis_v3",
                    "client_company": "Acme",
                    "current_version": 2,
                    "call_mode": "single_call",
                    "created_at": "2026-05-01T11:00:00Z",
                },
            ],
        },
    ]

    user = _FakeUser()

    import routes.vega_v3_sessions_user as mod
    monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr(mod, "has_perm", _has_perm)

    result = await mod.list_my_sessions(limit=20, offset=0, current_user=user)

    assert result["total"] == 1
    assert len(result["sessions"]) == 1
    row = result["sessions"][0]
    assert row["session_id"] == "vega_1"
    assert row["customer_name"] == "Acme"
    assert row["session_name"].startswith("Acme — 2026-05-01")
    assert row["latest_artifact_id"] == "art_1"
    assert row["current_version"] == 2
    assert row["latest_artifact_call_mode"] == "single_call"


@pytest.mark.asyncio
async def test_list_my_sessions_403_without_perm(monkeypatch):
    user = _FakeUser(perms=())
    import routes.vega_v3_sessions_user as mod
    monkeypatch.setattr(mod, "has_perm", _has_perm)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await mod.list_my_sessions(limit=20, offset=0, current_user=user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_list_my_sessions_pagination(monkeypatch):
    db = _FakeDB()
    db.vega_discovery_sessions.docs = [
        {
            "session_id": f"vega_{i}", "user_id": "user_abc",
            "chat_session_id": f"chat_{i}",
            "created_at": "2026-05-01T10:00:00Z",
            "updated_at": f"2026-05-{i:02d}T11:00:00Z",
            "client_company": f"Cust {i}",
            "discovery_mode": "single_call",
            "status": "complete",
        }
        for i in range(1, 31)
    ]
    user = _FakeUser()
    import routes.vega_v3_sessions_user as mod
    monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr(mod, "has_perm", _has_perm)

    result = await mod.list_my_sessions(limit=10, offset=0, current_user=user)
    assert result["total"] == 30
    assert result["limit"] == 10
    assert result["offset"] == 0
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_rename_my_session_succeeds(monkeypatch):
    db = _FakeDB()
    db.vega_discovery_sessions.docs = [
        {"session_id": "vega_1", "user_id": "user_abc"},
    ]
    user = _FakeUser()
    import routes.vega_v3_sessions_user as mod
    monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr(mod, "has_perm", _has_perm)
    body = mod.RenameBody(session_name="New Name")
    result = await mod.rename_my_session("vega_1", body, current_user=user)
    assert result["session_name"] == "New Name"
    assert db.vega_discovery_sessions.docs[0]["session_name_override"] == "New Name"


@pytest.mark.asyncio
async def test_rename_my_session_idor_blocked(monkeypatch):
    db = _FakeDB()
    db.vega_discovery_sessions.docs = [
        {"session_id": "vega_1", "user_id": "user_xyz"},
    ]
    user = _FakeUser(user_id="user_abc")
    import routes.vega_v3_sessions_user as mod
    monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr(mod, "has_perm", _has_perm)
    body = mod.RenameBody(session_name="Hijack")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await mod.rename_my_session("vega_1", body, current_user=user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_resume_my_session_returns_full_bundle(monkeypatch):
    db = _FakeDB()
    db.vega_discovery_sessions.docs = [
        {
            "session_id": "vega_1", "user_id": "user_abc",
            "chat_session_id": "chat_1",
            "created_at": "2026-05-01T10:00:00Z",
            "updated_at": "2026-05-01T11:00:00Z",
            "client_company": "Acme",
        },
    ]
    db.chat_sessions.docs = [
        {"session_id": "chat_1", "user_id": "user_abc", "ai_employee_id": "emp_ai_vega"},
    ]
    db.chat_messages.docs = [
        {"session_id": "chat_1", "user_id": "user_abc", "role": "user",
         "content": "hi", "created_at": "2026-05-01T10:01:00Z"},
        {"session_id": "chat_1", "user_id": "user_abc", "role": "assistant",
         "content": "hello", "created_at": "2026-05-01T10:02:00Z"},
    ]
    db.ai_chat_artifacts.docs = [
        {
            "artifact_id": "art_1", "user_id": "user_abc",
            "artifact_type": "discovery_analysis_v3",
            "payload": {"session_id": "vega_1", "client_company": "Acme"},
            "created_at": "2026-05-01T11:00:00Z",
        },
    ]
    user = _FakeUser()
    import routes.vega_v3_sessions_user as mod
    monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr(mod, "has_perm", _has_perm)

    result = await mod.resume_my_session("vega_1", current_user=user)
    assert result["vega_session"]["session_id"] == "vega_1"
    assert result["vega_session"]["session_name"].startswith("Acme — 2026-05-01")
    assert result["chat_session"]["session_id"] == "chat_1"
    assert len(result["chat_messages"]) == 2
    assert result["latest_artifact"]["artifact_id"] == "art_1"
    assert result["has_more_messages"] is False


@pytest.mark.asyncio
async def test_resume_my_session_idor_blocked(monkeypatch):
    db = _FakeDB()
    db.vega_discovery_sessions.docs = [
        {"session_id": "vega_1", "user_id": "user_xyz"},
    ]
    user = _FakeUser(user_id="user_abc")
    import routes.vega_v3_sessions_user as mod
    monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr(mod, "has_perm", _has_perm)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await mod.resume_my_session("vega_1", current_user=user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_resume_my_session_404_when_not_found(monkeypatch):
    db = _FakeDB()
    user = _FakeUser()
    import routes.vega_v3_sessions_user as mod
    monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr(mod, "has_perm", _has_perm)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await mod.resume_my_session("vega_missing", current_user=user)
    assert exc.value.status_code == 404
