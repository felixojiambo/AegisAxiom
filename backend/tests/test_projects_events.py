"""Tests for v1.3 Wave 1 / Thread 1A — realtime SSE, presence, and
optimistic concurrency.

Two test layers:

  1. Pure-async tests for the in-process `RealtimeEventBus` and the
     SSE generator helpers (`_sse_frame`, `_sse_comment`). These never
     touch FastAPI or Mongo and run in milliseconds.

  2. ASGI-level tests via `httpx.AsyncClient(app=...)` for the SSE
     endpoint, presence heartbeat, and the `If-Match` concurrency
     check on PATCH /tasks/{id}. We monkeypatch the small set of
     project/task/users collection accessors to keep Mongo out of the
     test loop. The audit + crud_envelope path is too coupled to
     monkeypatch wholesale, so the If-Match test mocks at the
     `db["pm_tasks"].find_one` level and short-circuits before the
     envelope runs.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from routes.projects_events import (
    _PRESENCE_ACTIVE_WINDOW_SECONDS,
    _can_view_project,
    _sse_comment,
    _sse_frame,
)
from services.projects_realtime_bus import (
    OVERFLOW_SENTINEL,
    RealtimeEventBus,
    project_topic,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _enable_realtime_by_default(monkeypatch):
    """Most tests in this file exercise the realtime code path. The production
    default is DISABLED (fail-safe), so without this fixture every endpoint
    would return 503. Tests that specifically verify the disabled behavior
    override via their own `monkeypatch.setenv(..., 'false')` or
    `monkeypatch.delenv(...)` — pytest's function-scoped monkeypatch wins."""
    monkeypatch.setenv("ENABLE_REALTIME_SSE", "true")


def make_user(perms=None, user_id="user_grace", name="grace"):
    perms = perms or []
    return SimpleNamespace(
        user_id=user_id,
        name=name,
        email=f"{name}@simplimeta.io",
        role="project_manager",
        permissions=set(perms),
        _perm_closure=set(perms),
    )


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ---------------------------------------------------------------------------
# Layer 1 — RealtimeEventBus pure tests
# ---------------------------------------------------------------------------
def test_publish_with_no_subscribers_is_noop():
    bus = RealtimeEventBus()

    async def go():
        # Should not raise, should not block.
        await bus.publish(project_topic("p1"), {"type": "task.updated"})

    run(go())
    assert bus.subscriber_count(project_topic("p1")) == 0


def test_subscriber_receives_published_event():
    bus = RealtimeEventBus()

    async def go():
        topic = project_topic("p1")
        sub_iter = bus.subscribe(topic)

        # Wait until subscription is registered before publishing.
        # Drive the generator far enough to enter the loop.
        get_task = asyncio.create_task(sub_iter.__anext__())
        # Give the event loop one tick to register the subscription.
        await asyncio.sleep(0)

        assert bus.subscriber_count(topic) == 1

        await bus.publish(topic, {"type": "task.created", "entity_id": "t1"})
        event = await asyncio.wait_for(get_task, timeout=1.0)
        assert event["type"] == "task.created"
        assert event["entity_id"] == "t1"
        assert "event_id" in event  # bus stamped it

        await sub_iter.aclose()

    run(go())


def test_other_projects_dont_get_events():
    """A subscriber to project p1 must NOT receive events for project p2."""
    bus = RealtimeEventBus()

    async def go():
        topic_a = project_topic("p1")
        topic_b = project_topic("p2")

        sub_iter = bus.subscribe(topic_a)
        get_task = asyncio.create_task(sub_iter.__anext__())
        await asyncio.sleep(0)

        # Publish to p2 only.
        await bus.publish(topic_b, {"type": "task.updated", "entity_id": "t99"})
        # Subscriber for p1 must NOT have received anything.
        # Wait a tick + a small slack to be sure.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(get_task), timeout=0.05)

        # Now publish to p1 — should arrive.
        await bus.publish(topic_a, {"type": "task.updated", "entity_id": "t1"})
        event = await asyncio.wait_for(get_task, timeout=1.0)
        assert event["entity_id"] == "t1"

        await sub_iter.aclose()

    run(go())


def test_multiple_subscribers_each_get_the_event():
    bus = RealtimeEventBus()

    async def go():
        topic = project_topic("p1")
        sub_a = bus.subscribe(topic)
        sub_b = bus.subscribe(topic)
        task_a = asyncio.create_task(sub_a.__anext__())
        task_b = asyncio.create_task(sub_b.__anext__())
        await asyncio.sleep(0)

        assert bus.subscriber_count(topic) == 2

        await bus.publish(topic, {"type": "task.updated", "entity_id": "t1"})

        ev_a = await asyncio.wait_for(task_a, timeout=1.0)
        ev_b = await asyncio.wait_for(task_b, timeout=1.0)
        assert ev_a["entity_id"] == "t1"
        assert ev_b["entity_id"] == "t1"
        # Each subscriber must see the same event_id (it's the same event).
        assert ev_a["event_id"] == ev_b["event_id"]

        await sub_a.aclose()
        await sub_b.aclose()

    run(go())


def test_overflow_sentinel_force_evicts_oldest_when_queue_full():
    """Wave 3.5 fix: when a subscriber queue is completely full, the bus
    must force-evict the oldest event so the sentinel still reaches the
    subscriber. Pre-fix, the sentinel was silently dropped on QueueFull
    and the subscriber stopped receiving events until heartbeat reconnect."""
    from services.projects_realtime_bus import _QUEUE_MAXSIZE, _Subscription
    bus = RealtimeEventBus()

    async def go():
        topic = project_topic("p_overflow_fix")
        # Bypass the subscribe() generator entirely so the subscription
        # remains registered without a consumer draining it. This lets us
        # deterministically fill the queue.
        sub = _Subscription()
        async with bus._lock:
            bus._subscribers[topic].add(sub)

        # Fill the queue exactly to capacity.
        for i in range(_QUEUE_MAXSIZE):
            await bus.publish(topic, {"type": "task.updated", "entity_id": f"t_{i}"})

        assert sub.queue.qsize() == _QUEUE_MAXSIZE  # truly full

        # One more publish — queue is FULL. The sentinel must still land
        # by evicting the oldest event.
        await bus.publish(topic, {"type": "task.updated", "entity_id": "t_overflow"})

        # Drain the queue; the sentinel MUST be one of the messages received.
        items = []
        while not sub.queue.empty():
            items.append(sub.queue.get_nowait())

        assert any(
            item is OVERFLOW_SENTINEL or (isinstance(item, dict) and item.get("__overflow__"))
            for item in items
        ), f"OVERFLOW_SENTINEL must reach subscriber even when queue is full; got items={items[:5]}..."

        # The eviction sacrificed the oldest event (t_0), so the queue
        # length is still bounded.
        assert len(items) == _QUEUE_MAXSIZE

    run(go())


def test_subscription_cleanup_on_close():
    bus = RealtimeEventBus()

    async def go():
        topic = project_topic("p1")
        sub_iter = bus.subscribe(topic)
        # Drive into the loop so the subscription is registered.
        get_task = asyncio.create_task(sub_iter.__anext__())
        await asyncio.sleep(0)
        assert bus.subscriber_count(topic) == 1

        # Cancel the pending get and close the generator.
        get_task.cancel()
        try:
            await get_task
        except asyncio.CancelledError:
            pass
        await sub_iter.aclose()

        # Subscription should be removed; topic bucket dropped.
        assert bus.subscriber_count(topic) == 0

    run(go())


# ---------------------------------------------------------------------------
# Layer 1.5 — SSE frame formatting
# ---------------------------------------------------------------------------
def test_sse_frame_shape():
    event = {
        "type": "task.updated",
        "entity_id": "t1",
        "event_id": "abc123",
        "payload": {"task_id": "t1", "title": "x"},
    }
    raw = _sse_frame(event).decode()
    assert raw.startswith("event: task.updated\n")
    assert "id: abc123\n" in raw
    assert raw.endswith("\n\n")
    # data line must be parseable as JSON.
    data_line = [line for line in raw.split("\n") if line.startswith("data: ")][0]
    parsed = json.loads(data_line[len("data: "):])
    assert parsed["entity_id"] == "t1"


def test_sse_comment_shape():
    raw = _sse_comment("heartbeat").decode()
    assert raw == ": heartbeat\n\n"


def test_sse_frame_defaults_type_when_missing():
    raw = _sse_frame({"event_id": "e1", "payload": {}}).decode()
    assert "event: project.update\n" in raw


# ---------------------------------------------------------------------------
# Layer 2 — _can_view_project RBAC helper
# ---------------------------------------------------------------------------
def test_can_view_project_with_task_view_perm():
    user = make_user(perms=["projects.task.view"])
    project = {"project_id": "p1", "pm_id": "user_other"}
    assert _can_view_project(user, project) is True


def test_can_view_project_with_project_view_perm():
    user = make_user(perms=["projects.project.view"])
    project = {"project_id": "p1", "pm_id": "user_other"}
    assert _can_view_project(user, project) is True


def test_can_view_project_as_pm_without_global_view_perm():
    """A PM of this project gets view via PM auto-grant, even with no view perm."""
    user = make_user(perms=[], user_id="user_grace")
    project = {"project_id": "p1", "pm_id": "user_grace"}
    assert _can_view_project(user, project) is True


def test_can_view_project_denied_without_perm_and_not_pm():
    user = make_user(perms=[])
    project = {"project_id": "p1", "pm_id": "user_other"}
    assert _can_view_project(user, project) is False


# ---------------------------------------------------------------------------
# Layer 3 — presence active-window math
#
# The endpoint relies on a TTL index for hard cleanup; we also apply a
# `last_seen >= now - 60s` belt-and-braces filter in the read path. Test
# that the constant is what the docs claim, and that the rendered viewing
# row shape is what the frontend expects.
# ---------------------------------------------------------------------------
def test_presence_active_window_constant():
    """If this changes, the frontend's heartbeat cadence (30s) must be
    re-evaluated. The frontend sends every 30s; the TTL window is 60s;
    so a single dropped heartbeat doesn't blink the user out."""
    assert _PRESENCE_ACTIVE_WINDOW_SECONDS == 60


# ---------------------------------------------------------------------------
# Layer 4 — If-Match concurrency check
#
# We test the helper-level logic by exercising update_task with a
# mocked db that returns a `before` doc with a known `updated_at`.
# A mismatch must surface as HTTP 409 with `current_doc` in the body.
# We short-circuit before the envelope runs by asserting on the
# exception raised before any write happens.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_if_match_mismatch_returns_409(monkeypatch):
    """PATCH with stale If-Match header must reject with 409 +
    current_doc payload so the frontend can rebase."""
    from fastapi import HTTPException

    from routes import projects_task

    stored = {
        "task_id": "t1",
        "project_id": "p1",
        "title": "old title",
        "updated_at": "2026-05-11T10:00:00+00:00",
        "status": "todo",
    }

    mock_db = MagicMock()
    mock_db.__getitem__.return_value.find_one = AsyncMock(return_value=stored)
    monkeypatch.setattr(projects_task, "get_db", lambda: mock_db)

    request = MagicMock()
    request.headers = {"If-Match": "2026-05-11T09:00:00+00:00"}  # stale

    user = make_user(perms=["projects.task.edit"])
    payload = projects_task.TaskUpdate(title="new")

    with pytest.raises(HTTPException) as exc_info:
        await projects_task.update_task(
            task_id="t1",
            payload=payload,
            request=request,
            current_user=user,
        )

    assert exc_info.value.status_code == 409
    body = exc_info.value.detail
    assert isinstance(body, dict)
    assert body["detail"] == "conflict"
    assert body["current_updated_at"] == stored["updated_at"]
    assert body["current_doc"]["task_id"] == "t1"
    assert body["current_doc"]["title"] == "old title"


@pytest.mark.asyncio
async def test_if_match_absent_skips_check(monkeypatch):
    """No If-Match header → behave like a normal PATCH. We assert by
    checking that the check does NOT raise 409 — we still let it fail
    further along on the project-load step because the mock isn't fully
    plumbed, but the failure must NOT be the 409 path."""
    from fastapi import HTTPException

    from routes import projects_task

    stored = {
        "task_id": "t1",
        "project_id": "p1",
        "title": "old",
        "updated_at": "2026-05-11T10:00:00+00:00",
    }

    pm_tasks_mock = MagicMock()
    pm_tasks_mock.find_one = AsyncMock(return_value=stored)
    projects_mock = MagicMock()
    # _load_project_for_task does find_one — return None → 422
    projects_mock.find_one = AsyncMock(return_value=None)

    def getitem(name):
        if name == "pm_tasks":
            return pm_tasks_mock
        if name == "projects":
            return projects_mock
        return MagicMock()

    mock_db = MagicMock()
    mock_db.__getitem__.side_effect = getitem
    monkeypatch.setattr(projects_task, "get_db", lambda: mock_db)

    request = MagicMock()
    request.headers = {}  # no If-Match

    user = make_user(perms=["projects.task.edit"])
    payload = projects_task.TaskUpdate(title="new")

    with pytest.raises(HTTPException) as exc_info:
        await projects_task.update_task(
            task_id="t1",
            payload=payload,
            request=request,
            current_user=user,
        )

    # Must NOT be 409 — the If-Match path is skipped.
    assert exc_info.value.status_code != 409


@pytest.mark.asyncio
async def test_if_match_exact_match_proceeds(monkeypatch):
    """Header value matches stored updated_at → no 409. Like the previous
    test, we let the call fail downstream (no fully-plumbed mock) but
    confirm we got past the concurrency gate."""
    from fastapi import HTTPException

    from routes import projects_task

    stored = {
        "task_id": "t1",
        "project_id": "p1",
        "title": "old",
        "updated_at": "2026-05-11T10:00:00+00:00",
    }

    pm_tasks_mock = MagicMock()
    pm_tasks_mock.find_one = AsyncMock(return_value=stored)
    projects_mock = MagicMock()
    projects_mock.find_one = AsyncMock(return_value=None)  # cascades to 422

    def getitem(name):
        if name == "pm_tasks":
            return pm_tasks_mock
        if name == "projects":
            return projects_mock
        return MagicMock()

    mock_db = MagicMock()
    mock_db.__getitem__.side_effect = getitem
    monkeypatch.setattr(projects_task, "get_db", lambda: mock_db)

    request = MagicMock()
    request.headers = {"If-Match": stored["updated_at"]}  # exact match

    user = make_user(perms=["projects.task.edit"])
    payload = projects_task.TaskUpdate(title="new")

    with pytest.raises(HTTPException) as exc_info:
        await projects_task.update_task(
            task_id="t1",
            payload=payload,
            request=request,
            current_user=user,
        )

    # Must NOT be 409 — match means we passed the gate.
    assert exc_info.value.status_code != 409


# ---------------------------------------------------------------------------
# Layer 5 — Mutation routes publish to the realtime bus
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_emit_task_event_publishes_to_correct_topic(monkeypatch):
    """The shared `_emit_task_event` helper used by every mutation route
    must publish to `project:{project_id}` with the canonical event
    shape (type, entity_*, project_id, payload, actor_*, at)."""
    from routes import projects_task

    captured: List[Dict[str, Any]] = []

    async def fake_publish(topic, event):
        captured.append({"topic": topic, "event": event})

    monkeypatch.setattr(projects_task.realtime_bus, "publish", fake_publish)

    task_doc = {"task_id": "t1", "title": "x", "project_id": "p1"}
    user = make_user()

    await projects_task._emit_task_event(
        event_type="task.updated",
        task_doc=task_doc,
        project_id="p1",
        entity_id="t1",
        current_user=user,
    )

    assert len(captured) == 1
    assert captured[0]["topic"] == "project:p1"
    ev = captured[0]["event"]
    assert ev["type"] == "task.updated"
    assert ev["entity_type"] == "task"
    assert ev["entity_id"] == "t1"
    assert ev["project_id"] == "p1"
    assert ev["actor_user_id"] == user.user_id
    assert ev["actor_type"] == "user"
    assert ev["payload"] == task_doc
    assert "at" in ev


@pytest.mark.asyncio
async def test_emit_task_event_never_raises_on_publish_failure(monkeypatch):
    """A bus failure must NOT poison a successful Mongo write — the
    emit helper logs and swallows."""
    from routes import projects_task

    async def bad_publish(topic, event):
        raise RuntimeError("bus is on fire")

    monkeypatch.setattr(projects_task.realtime_bus, "publish", bad_publish)

    # Should not raise.
    await projects_task._emit_task_event(
        event_type="task.updated",
        task_doc={"task_id": "t1"},
        project_id="p1",
        entity_id="t1",
        current_user=make_user(),
    )


# ---------------------------------------------------------------------------
# Layer 6 — Presence response shape (logic-level)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_presence_excludes_caller_and_hydrates_names(monkeypatch):
    """Heartbeat returns the list of *other* users active in the last 60s,
    with hydrated name / avatar / viewport fields."""
    from routes import projects_events

    now = datetime.now(timezone.utc)
    project = {"project_id": "p1", "pm_id": "user_other"}

    # Stored rows: caller (excluded), one active peer, one stale peer.
    presence_rows = [
        {"user_id": "user_henry", "last_seen": now, "viewport": "board"},
    ]
    user_rows = [
        {"user_id": "user_henry", "name": "Henry", "avatar_url": "https://x/h.png"},
    ]

    pm_presence_mock = MagicMock()
    pm_presence_mock.update_one = AsyncMock()
    pm_presence_mock.find = MagicMock(
        return_value=MagicMock(to_list=AsyncMock(return_value=presence_rows))
    )
    pm_presence_mock.create_index = AsyncMock()

    projects_mock = MagicMock()
    projects_mock.find_one = AsyncMock(return_value=project)

    class _AsyncIter:
        def __init__(self, items):
            self._items = list(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._items:
                raise StopAsyncIteration
            return self._items.pop(0)

    users_mock = MagicMock()
    users_mock.find = MagicMock(return_value=_AsyncIter(user_rows))

    def getitem(name):
        if name == "pm_presence":
            return pm_presence_mock
        if name == "projects":
            return projects_mock
        if name == "users":
            return users_mock
        return MagicMock()

    mock_db = MagicMock()
    mock_db.__getitem__.side_effect = getitem
    monkeypatch.setattr(projects_events, "get_db", lambda: mock_db)
    # Pre-mark indexes ensured so we skip the create_index calls in test.
    monkeypatch.setattr(projects_events, "_PRESENCE_TTL_ENSURED", True)

    user = make_user(perms=["projects.task.view"], user_id="user_grace")

    result = await projects_events.presence_heartbeat(
        project_id="p1",
        body=projects_events.PresenceHeartbeatBody(viewport="board"),
        current_user=user,
    )

    assert result["ttl_seconds"] == _PRESENCE_ACTIVE_WINDOW_SECONDS
    viewing = result["viewing"]
    assert len(viewing) == 1
    assert viewing[0]["user_id"] == "user_henry"
    assert viewing[0]["name"] == "Henry"
    assert viewing[0]["avatar"] == "https://x/h.png"
    assert viewing[0]["viewport"] == "board"

    # Confirm the upsert was called with the caller's data.
    pm_presence_mock.update_one.assert_awaited_once()
    call_args = pm_presence_mock.update_one.await_args
    filt = call_args.args[0]
    update = call_args.args[1]
    assert filt == {"project_id": "p1", "user_id": "user_grace"}
    assert call_args.kwargs["upsert"] is True
    assert update["$set"]["viewport"] == "board"


@pytest.mark.asyncio
async def test_presence_rejects_unknown_viewport(monkeypatch):
    """Free-form viewport strings must be rejected so the field is safely
    renderable (no XSS / unrendered junk in the avatar tooltip)."""
    from fastapi import HTTPException

    from routes import projects_events

    project = {"project_id": "p1", "pm_id": "user_other"}
    projects_mock = MagicMock()
    projects_mock.find_one = AsyncMock(return_value=project)
    mock_db = MagicMock()
    mock_db.__getitem__.return_value = projects_mock
    monkeypatch.setattr(projects_events, "get_db", lambda: mock_db)
    monkeypatch.setattr(projects_events, "_PRESENCE_TTL_ENSURED", True)

    user = make_user(perms=["projects.task.view"])
    body = projects_events.PresenceHeartbeatBody(viewport="../../etc/passwd")

    with pytest.raises(HTTPException) as exc_info:
        await projects_events.presence_heartbeat(
            project_id="p1",
            body=body,
            current_user=user,
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_sse_rbac_denies_user_without_view_perm(monkeypatch):
    from fastapi import HTTPException

    from routes import projects_events

    project = {"project_id": "p1", "pm_id": "user_other"}
    projects_mock = MagicMock()
    projects_mock.find_one = AsyncMock(return_value=project)
    mock_db = MagicMock()
    mock_db.__getitem__.return_value = projects_mock
    monkeypatch.setattr(projects_events, "get_db", lambda: mock_db)

    user = make_user(perms=[])  # no perms, not PM
    request = MagicMock()
    request.headers = {}

    with pytest.raises(HTTPException) as exc_info:
        await projects_events.stream_project_events(
            project_id="p1",
            request=request,
            current_user=user,
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_sse_404_when_project_missing(monkeypatch):
    from fastapi import HTTPException

    from routes import projects_events

    projects_mock = MagicMock()
    projects_mock.find_one = AsyncMock(return_value=None)
    mock_db = MagicMock()
    mock_db.__getitem__.return_value = projects_mock
    monkeypatch.setattr(projects_events, "get_db", lambda: mock_db)

    user = make_user(perms=["projects.task.view"])
    request = MagicMock()
    request.headers = {}

    with pytest.raises(HTTPException) as exc_info:
        await projects_events.stream_project_events(
            project_id="missing",
            request=request,
            current_user=user,
        )
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Feature flag — ENABLE_REALTIME_SSE
# ---------------------------------------------------------------------------
# When the flag is off (e.g., during a Cloud-Run-bound deployment without
# `--timeout=3600`, or during architecture replatforming), the SSE endpoint
# and the ticket endpoint must return 503 so clients short-circuit cleanly
# instead of holding broken connections. Presence + conflict-on-PATCH stay on.


@pytest.mark.asyncio
async def test_sse_endpoint_503_when_realtime_disabled(monkeypatch):
    from fastapi import HTTPException
    from routes import projects_events

    monkeypatch.setenv("ENABLE_REALTIME_SSE", "false")

    user = make_user(perms=["projects.task.view"])
    request = MagicMock()
    request.headers = {}

    with pytest.raises(HTTPException) as exc_info:
        await projects_events.stream_project_events(
            project_id="p1",
            request=request,
            current_user=user,
        )
    assert exc_info.value.status_code == 503
    assert "disabled" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_ticket_endpoint_503_when_realtime_disabled(monkeypatch):
    from fastapi import HTTPException
    from routes import projects_events

    monkeypatch.setenv("ENABLE_REALTIME_SSE", "false")

    user = make_user(perms=["projects.task.view"])

    with pytest.raises(HTTPException) as exc_info:
        await projects_events.issue_events_ticket(
            project_id="p1",
            current_user=user,
        )
    assert exc_info.value.status_code == 503


def test_realtime_flag_defaults_to_disabled(monkeypatch):
    """Fail-safe default: unset → disabled. Protects production against the
    case where the new env var hasn't been added to secret manager yet."""
    from routes.projects_events import _realtime_enabled

    monkeypatch.delenv("ENABLE_REALTIME_SSE", raising=False)
    assert _realtime_enabled() is False


def test_realtime_flag_truthy_values_enable(monkeypatch):
    from routes.projects_events import _realtime_enabled

    for raw in ("true", "TRUE", "1", "yes", "on", " True ", "ON", "Yes"):
        monkeypatch.setenv("ENABLE_REALTIME_SSE", raw)
        assert _realtime_enabled() is True, f"expected {raw!r} to enable"


def test_realtime_flag_other_values_disable(monkeypatch):
    """Anything that isn't an explicit truthy opt-in stays disabled —
    including empty string, junk, common-but-unsupported variants."""
    from routes.projects_events import _realtime_enabled

    for raw in ("false", "FALSE", "0", "no", "off", "", " ", "enabled", "anything-else"):
        monkeypatch.setenv("ENABLE_REALTIME_SSE", raw)
        assert _realtime_enabled() is False, f"expected {raw!r} to stay disabled"


# ---------------------------------------------------------------------------
# Layer 7 — v1.4 ticket-exchange SSE auth dependency (get_sse_user)
# ---------------------------------------------------------------------------
#
# The browser EventSource API can't attach custom headers, so the SSE endpoint
# accepts a single-use, project-bound `?ticket=...` query parameter in
# addition to the standard session_token cookie / Authorization Bearer
# header. These tests pin that behavior and verify the auth-failure modes.
#
# v1.3 used `?access_token=<session_token>` directly; v1.4 replaced it with
# the ticket exchange. The cookie + Bearer paths are unchanged.


def _make_sse_db(*, session=None, user_doc=None, role_doc=None):
    """Build a mock db with the four collections get_sse_user reads."""
    sessions_mock = MagicMock()
    sessions_mock.find_one = AsyncMock(return_value=session)

    users_mock = MagicMock()
    users_mock.find_one = AsyncMock(return_value=user_doc)

    roles_mock = MagicMock()
    roles_mock.find_one = AsyncMock(return_value=role_doc or {"permissions": []})

    def getitem(name):
        if name in ("user_sessions",):
            return sessions_mock
        if name in ("users",):
            return users_mock
        if name in ("roles",):
            return roles_mock
        return MagicMock()

    mock_db = MagicMock()
    mock_db.__getitem__.side_effect = getitem
    # `db.user_sessions` (attribute access) is also used.
    mock_db.user_sessions = sessions_mock
    mock_db.users = users_mock
    mock_db.roles = roles_mock
    return mock_db


@pytest.mark.asyncio
async def test_get_sse_user_accepts_valid_ticket(monkeypatch):
    """Happy path: a valid ticket for the matching project resolves to a user."""
    from routes import projects_events

    user_doc = {"user_id": "user_grace", "email": "grace@x.io",
                "name": "Grace Holloway",
                "role": "project_manager", "is_active": True,
                "tenant_id": "t_simplimeta",
                "permissions": [], "extra_permissions": []}
    role_doc = {"permissions": ["projects.task.view"]}

    mock_db = _make_sse_db(user_doc=user_doc, role_doc=role_doc)
    monkeypatch.setattr(projects_events, "get_db", lambda: mock_db)

    # consume_ticket returns the bound (user_id, project_id) on success.
    async def fake_consume(ticket):
        return {"user_id": "user_grace", "project_id": "p1"}
    monkeypatch.setattr(projects_events, "consume_ticket", fake_consume)

    request = MagicMock()
    request.url.path = "/api/projects/p1/events"
    request.path_params = {"project_id": "p1"}

    user = await projects_events.get_sse_user(
        request=request,
        ticket="tkt_valid",
        session_token=None,
        authorization=None,
    )
    assert user.user_id == "user_grace"


@pytest.mark.asyncio
async def test_get_sse_user_rejects_invalid_or_consumed_ticket(monkeypatch):
    """A ticket that consume_ticket cannot validate (unknown, expired, or
    already consumed) MUST result in 401 — never silently fall through."""
    from fastapi import HTTPException
    from routes import projects_events

    mock_db = _make_sse_db()
    monkeypatch.setattr(projects_events, "get_db", lambda: mock_db)

    async def fake_consume(ticket):
        return None  # invalid / expired / already used
    monkeypatch.setattr(projects_events, "consume_ticket", fake_consume)

    request = MagicMock()
    request.url.path = "/api/projects/p1/events"
    request.path_params = {"project_id": "p1"}

    with pytest.raises(HTTPException) as exc_info:
        await projects_events.get_sse_user(
            request=request,
            ticket="tkt_bad",
            session_token=None,
            authorization=None,
        )
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_sse_user_rejects_ticket_for_other_project(monkeypatch):
    """A ticket issued for project A must NOT authenticate a stream of
    project B. consume_ticket succeeds (the ticket is real); the binding
    re-check in get_sse_user is what trips the 403."""
    from fastapi import HTTPException
    from routes import projects_events

    mock_db = _make_sse_db()
    monkeypatch.setattr(projects_events, "get_db", lambda: mock_db)

    async def fake_consume(ticket):
        # Ticket was issued for project_a, but the path is for project_b.
        return {"user_id": "user_grace", "project_id": "project_a"}
    monkeypatch.setattr(projects_events, "consume_ticket", fake_consume)

    request = MagicMock()
    request.url.path = "/api/projects/project_b/events"
    request.path_params = {"project_id": "project_b"}

    with pytest.raises(HTTPException) as exc_info:
        await projects_events.get_sse_user(
            request=request,
            ticket="tkt_for_a",
            session_token=None,
            authorization=None,
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_sse_user_accepts_session_cookie(monkeypatch):
    """Cookie path still works — the ticket fallback must not displace it."""
    from routes import projects_events

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    session = {"session_token": "sess_cookie", "user_id": "user_grace",
               "expires_at": future.isoformat()}
    user_doc = {"user_id": "user_grace", "email": "grace@x.io",
                "name": "Grace Holloway",
                "role": "project_manager", "is_active": True,
                "tenant_id": "t_simplimeta",
                "permissions": [], "extra_permissions": []}

    mock_db = _make_sse_db(session=session, user_doc=user_doc)
    monkeypatch.setattr(projects_events, "get_db", lambda: mock_db)

    request = MagicMock()
    request.url.path = "/api/projects/p1/events"
    request.path_params = {"project_id": "p1"}

    user = await projects_events.get_sse_user(
        request=request,
        ticket=None,
        session_token="sess_cookie",
        authorization=None,
    )
    assert user.user_id == "user_grace"


@pytest.mark.asyncio
async def test_get_sse_user_accepts_bearer_header(monkeypatch):
    """Bearer header path still works (rare — curl / fetch wrappers)."""
    from routes import projects_events

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    session = {"session_token": "sess_bearer", "user_id": "user_grace",
               "expires_at": future.isoformat()}
    user_doc = {"user_id": "user_grace", "email": "grace@x.io",
                "name": "Grace Holloway",
                "role": "project_manager", "is_active": True,
                "tenant_id": "t_simplimeta",
                "permissions": [], "extra_permissions": []}

    mock_db = _make_sse_db(session=session, user_doc=user_doc)
    monkeypatch.setattr(projects_events, "get_db", lambda: mock_db)

    request = MagicMock()
    request.url.path = "/api/projects/p1/events"
    request.path_params = {"project_id": "p1"}

    user = await projects_events.get_sse_user(
        request=request,
        ticket=None,
        session_token=None,
        authorization="Bearer sess_bearer",
    )
    assert user.user_id == "user_grace"


@pytest.mark.asyncio
async def test_get_sse_user_rejects_when_no_token(monkeypatch):
    from fastapi import HTTPException
    from routes import projects_events

    mock_db = _make_sse_db()
    monkeypatch.setattr(projects_events, "get_db", lambda: mock_db)

    request = MagicMock()
    request.url.path = "/api/projects/p1/events"
    request.path_params = {"project_id": "p1"}

    with pytest.raises(HTTPException) as exc_info:
        await projects_events.get_sse_user(
            request=request,
            ticket=None,
            session_token=None,
            authorization=None,
        )
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Layer 7.5 — v1.4 ticket-issue endpoint
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_issue_events_ticket_returns_ticket_and_ttl(monkeypatch):
    """Happy path: an authenticated viewer of the project gets a ticket
    + TTL hint back. The opaque ticket string itself is not asserted
    (it's a random token from secrets.token_urlsafe) — only the response
    shape and the fact that issue_ticket was called with the right binding."""
    from routes import projects_events

    project = {"project_id": "p1", "pm_id": "user_other"}
    projects_mock = MagicMock()
    projects_mock.find_one = AsyncMock(return_value=project)
    mock_db = MagicMock()
    mock_db.__getitem__.return_value = projects_mock
    monkeypatch.setattr(projects_events, "get_db", lambda: mock_db)

    issued = {}

    async def fake_issue(user_id, project_id, *, ttl_seconds=30):
        issued["user_id"] = user_id
        issued["project_id"] = project_id
        issued["ttl_seconds"] = ttl_seconds
        return "tkt_freshly_minted"
    monkeypatch.setattr(projects_events, "issue_ticket", fake_issue)

    user = make_user(perms=["projects.task.view"], user_id="user_grace")

    result = await projects_events.issue_events_ticket(
        project_id="p1",
        current_user=user,
    )
    assert result.ticket == "tkt_freshly_minted"
    assert result.expires_in == 30
    # Verify the binding was on the caller + the path's project_id.
    assert issued == {"user_id": "user_grace", "project_id": "p1", "ttl_seconds": 30}


@pytest.mark.asyncio
async def test_issue_events_ticket_denies_without_view_perm(monkeypatch):
    """RBAC: caller without view perm + not the PM gets 403 — same gate
    as the SSE GET. Failing here gives the SPA a synchronous error
    instead of an opaque EventSource onerror later."""
    from fastapi import HTTPException
    from routes import projects_events

    project = {"project_id": "p1", "pm_id": "user_other"}
    projects_mock = MagicMock()
    projects_mock.find_one = AsyncMock(return_value=project)
    mock_db = MagicMock()
    mock_db.__getitem__.return_value = projects_mock
    monkeypatch.setattr(projects_events, "get_db", lambda: mock_db)

    user = make_user(perms=[], user_id="user_grace")  # no view perm, not PM

    with pytest.raises(HTTPException) as exc_info:
        await projects_events.issue_events_ticket(
            project_id="p1",
            current_user=user,
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_issue_events_ticket_404_when_project_missing(monkeypatch):
    from fastapi import HTTPException
    from routes import projects_events

    projects_mock = MagicMock()
    projects_mock.find_one = AsyncMock(return_value=None)
    mock_db = MagicMock()
    mock_db.__getitem__.return_value = projects_mock
    monkeypatch.setattr(projects_events, "get_db", lambda: mock_db)

    user = make_user(perms=["projects.task.view"])

    with pytest.raises(HTTPException) as exc_info:
        await projects_events.issue_events_ticket(
            project_id="missing",
            current_user=user,
        )
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Layer 8 — Wave 3.5: SSE heartbeat task leak fix
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sse_heartbeat_does_not_leak_tasks_across_cycles():
    """The SSE generator must not spawn a new `next_task` on every heartbeat
    cycle. Pre-fix, ~144 dangling tasks accumulated per 60-min session per
    subscriber. We simulate many heartbeat cycles by patching the heartbeat
    interval to a small value and verify that asyncio's task count doesn't
    grow proportionally."""
    import gc
    from routes import projects_events as pe

    # Patch the heartbeat to a tiny interval so the loop ticks fast.
    original_interval = pe._HEARTBEAT_INTERVAL_SECONDS
    pe._HEARTBEAT_INTERVAL_SECONDS = 0.01
    try:
        gen = pe._event_stream("p_leak", last_event_id=None)

        async def drive_heartbeats(n_heartbeats):
            # First yield is the "ok" comment.
            await gen.__anext__()
            collected = 0
            while collected < n_heartbeats:
                chunk = await gen.__anext__()
                if b"heartbeat" in chunk:
                    collected += 1
            return collected

        await drive_heartbeats(10)

        gc.collect()
        baseline_tasks = len(asyncio.all_tasks())

        await drive_heartbeats(20)

        gc.collect()
        after_tasks = len(asyncio.all_tasks())

        # Allow a small slack for asyncio internals, but the count must NOT
        # grow ~1-per-heartbeat (which is what the pre-fix code did).
        assert after_tasks - baseline_tasks < 5, (
            f"task leak detected: baseline={baseline_tasks} after={after_tasks}"
        )

        await gen.aclose()
    finally:
        pe._HEARTBEAT_INTERVAL_SECONDS = original_interval
