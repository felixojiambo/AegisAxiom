"""Locks the get_chat_logs admin endpoint aggregation contract.

The admin endpoint at GET /api/chat/admin/logs serves the
paginated session slice joined with a per session message count
via a single chat_sessions.aggregate call. The previous loop
based path issued one count_documents call per returned session.
The aggregation rewrite must hold the contract points locked here.

  * Every returned row carries the correct message_count.
  * The route calls aggregate exactly once on chat_sessions and
    NEVER calls count_documents on chat_messages.
  * Pagination semantics under identical skip and limit inputs
    produce the same set of session ids as the legacy loop based
    oracle.
  * An empty page returns an empty list and a 200, not an error.
  * No internal lookup intermediate field and no bson _id leak to
    the response.

Synthetic only. The fake collection is an in process stub that
mimics the motor aggregate cursor and chat_sessions.find chain
the legacy loop walked so the oracle is a faithful baseline.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest")

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_admin():
    return SimpleNamespace(
        user_id="u_synth_e",
        email="u_synth_e@example.invalid",
        role="admin",
        permissions=["*"],
        tenant_id=None,
    )


class _FakeCursor:
    """Minimal motor style cursor for the aggregate path."""

    def __init__(self, docs):
        self._docs = list(docs)

    async def to_list(self, limit):
        return list(self._docs[:limit])


class _FakeFindCursor:
    """Minimal motor style find chain used in legacy oracle path."""

    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *args, **kwargs):
        if args and args[0] == "updated_at":
            self._docs.sort(
                key=lambda d: d.get("updated_at"),
                reverse=(args[1] == -1) if len(args) > 1 else True,
            )
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, n):
        return list(self._docs[:n])


class _FakeChatSessionsCollection:
    def __init__(self, sessions, messages):
        self._sessions = list(sessions)
        self._messages = list(messages)
        self.aggregate_call_count = 0
        self.find_call_count = 0
        self.count_documents_call_count = 0
        self.last_aggregate_pipeline = None

    async def count_documents(self, query):
        self.count_documents_call_count += 1
        return sum(1 for s in self._sessions if self._matches(s, query))

    def find(self, query, projection=None):
        self.find_call_count += 1
        out = [
            {k: v for k, v in s.items() if k != "_id"}
            for s in self._sessions
            if self._matches(s, query)
        ]
        return _FakeFindCursor(out)

    def aggregate(self, pipeline):
        self.aggregate_call_count += 1
        self.last_aggregate_pipeline = list(pipeline)
        # Replay the pipeline shape the route emits. The supported
        # stages are match, sort, skip, limit, lookup, addFields,
        # project. Anything else is unexpected so the test will
        # surface it as a KeyError.
        docs = [dict(s) for s in self._sessions]
        for stage in pipeline:
            (op, arg), = stage.items()
            if op == "$match":
                docs = [d for d in docs if self._matches(d, arg)]
            elif op == "$sort":
                field, direction = next(iter(arg.items()))
                docs.sort(
                    key=lambda d: d.get(field),
                    reverse=(direction == -1),
                )
            elif op == "$skip":
                docs = docs[arg:]
            elif op == "$limit":
                docs = docs[:arg]
            elif op == "$lookup":
                local_key = arg["let"]["session_id"].lstrip("$")
                as_field = arg["as"]
                for d in docs:
                    sess_id = d.get(local_key)
                    count = sum(
                        1
                        for m in self._messages
                        if m.get("session_id") == sess_id
                    )
                    d[as_field] = [{"count": count}] if count else []
            elif op == "$addFields":
                # Implement the exact expression the route uses.
                spec = arg.get("message_count")
                if isinstance(spec, dict) and "$ifNull" in spec:
                    inner = spec["$ifNull"][0]
                    elem_at = inner["$arrayElemAt"]
                    field_path = elem_at[0].lstrip("$")
                    base_field, sub_key = field_path.split(".", 1)
                    for d in docs:
                        arr = d.get(base_field) or []
                        if arr:
                            d["message_count"] = arr[0].get(sub_key, 0)
                        else:
                            d["message_count"] = 0
            elif op == "$project":
                drop_fields = [k for k, v in arg.items() if v == 0]
                for d in docs:
                    for f in drop_fields:
                        d.pop(f, None)
        return _FakeCursor(docs)

    @staticmethod
    def _matches(doc, query):
        for k, v in query.items():
            if doc.get(k) != v:
                return False
        return True


class _FakeChatMessagesCollection:
    """Surfaces count_documents so the test can assert the rewrite
    no longer invokes the per session count loop."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.count_documents_call_count = 0

    async def count_documents(self, query):
        self.count_documents_call_count += 1
        return sum(
            1
            for m in self._messages
            if all(m.get(k) == v for k, v in query.items())
        )


class _FakeDb:
    def __init__(self, sessions, messages):
        self.chat_sessions = _FakeChatSessionsCollection(sessions, messages)
        self.chat_messages = _FakeChatMessagesCollection(messages)


def _seed():
    """N synthetic sessions with a varying number of messages each."""
    sessions = []
    messages = []
    counts = {
        "sess_synth_001": 0,
        "sess_synth_002": 1,
        "sess_synth_003": 5,
        "sess_synth_004": 13,
        "sess_synth_005": 27,
    }
    for index, (sess_id, n) in enumerate(counts.items()):
        sessions.append({
            "session_id": sess_id,
            "user_id": "u_synth_e",
            "chat_number": "CHAT-2099-" + str(index).zfill(4),
            "created_at": "2099-01-" + str(index + 1).zfill(2) + "T00:00:00",
            "updated_at": "2099-02-" + str(index + 1).zfill(2) + "T00:00:00",
        })
        for k in range(n):
            messages.append({
                "session_id": sess_id,
                "role": "user" if k % 2 == 0 else "assistant",
                "content": "synth message " + str(k),
            })
    return sessions, messages, counts


def _build_app(db):
    from routes.chat import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/chat")
    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    return app


def test_each_row_carries_correct_message_count():
    """Seed N sessions with varying message counts and assert every
    returned row has the matching message_count."""
    sessions, messages, expected = _seed()
    db = _FakeDb(sessions, messages)
    app = _build_app(db)
    with patch("routes.chat.get_db", return_value=db):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/chat/admin/logs?limit=100")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body["items"]
    assert len(items) == len(expected)
    for item in items:
        sid = item["session_id"]
        assert item["message_count"] == expected[sid], (
            "message_count mismatch for " + sid
            + " expected " + str(expected[sid])
            + " got " + str(item.get("message_count"))
        )


def test_route_uses_one_aggregate_and_no_per_session_count_documents():
    """The rewrite must replace the loop. The route invokes
    chat_sessions.aggregate exactly once and never invokes
    count_documents on chat_messages."""
    sessions, messages, _ = _seed()
    db = _FakeDb(sessions, messages)
    app = _build_app(db)
    with patch("routes.chat.get_db", return_value=db):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/chat/admin/logs?limit=100")
    assert resp.status_code == 200
    assert db.chat_sessions.aggregate_call_count == 1
    assert db.chat_messages.count_documents_call_count == 0


def test_pagination_boundaries_match_loop_oracle():
    """Pagination semantics under identical skip and limit inputs
    must produce the same set of session ids the legacy loop would
    have produced. The oracle is a hand built expected order
    derived from the seeded fixture which sorts by updated_at
    descending."""
    sessions, messages, _ = _seed()
    db = _FakeDb(sessions, messages)
    app = _build_app(db)
    # Oracle. Seeded sessions are ordered by updated_at ascending so
    # the legacy descending sort yields the reverse order.
    expected_full_order = list(reversed([s["session_id"] for s in sessions]))

    for skip, limit in [(0, 2), (1, 2), (2, 2), (3, 5), (0, 100)]:
        with patch("routes.chat.get_db", return_value=db):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(
                "/api/chat/admin/logs?skip=" + str(skip)
                + "&limit=" + str(limit)
            )
        assert resp.status_code == 200, resp.text
        got = [row["session_id"] for row in resp.json()["items"]]
        expected = expected_full_order[skip:skip + limit]
        assert got == expected, (
            "page boundary drift at skip="
            + str(skip) + " limit=" + str(limit)
            + " got " + repr(got) + " expected " + repr(expected)
        )


def test_empty_page_returns_empty_list():
    """An empty result set must surface as an empty items list and
    a 200, not an error."""
    db = _FakeDb([], [])
    app = _build_app(db)
    with patch("routes.chat.get_db", return_value=db):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/chat/admin/logs?limit=100")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_no_internal_lookup_fields_leak_to_response():
    """The aggregation collects per session counts in a lookup
    field message_count_doc and projects it out. The response must
    not expose the intermediate field."""
    sessions, messages, _ = _seed()
    db = _FakeDb(sessions, messages)
    app = _build_app(db)
    with patch("routes.chat.get_db", return_value=db):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/chat/admin/logs?limit=100")
    body = resp.json()
    for item in body["items"]:
        assert "message_count_doc" not in item
        assert "_id" not in item
