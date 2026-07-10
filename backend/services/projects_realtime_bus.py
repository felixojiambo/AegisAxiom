"""In-process async pub/sub for project-scoped SSE realtime fan-out.

v1.3 / Wave 1 / Thread 1A — backs the SSE stream
(`GET /api/projects/{project_id}/events`) so multiple PMs viewing the
same project see each other's mutations within ~1 second.

Why a separate module from services/event_bus.py
-------------------------------------------------
`services/event_bus.py` already hosts two coexisting subsystems:

  1. `LocalEventBus` / `PubSubEventBus` — the workflow automation bus
     used by native route files. Fire-and-forget via
     `asyncio.create_task`. One global topic. Not topic-keyed.

  2. The ingestion `emit()` / `register_listener()` machinery —
     sequential dispatch with glob pattern matching, used by the
     ingestion CRUD envelope for policy / audit / automation listeners.

Neither one is the right shape for SSE fan-out: we need per-project
topics, per-subscriber bounded queues with backpressure, and the
ability to drop a slow subscriber without affecting others. So this
module is a third, purpose-built bus. The three coexist intentionally
— same pattern as `services/event_bus.py` already documents for its
two flavors.

Design notes
------------
- **Topology:** one topic per project (`project:{project_id}`). Subscribers
  receive every event published to that topic via an `asyncio.Queue`.
  Publishers fan out by appending to each subscriber's queue.
- **Backpressure:** each queue has a bounded size. A slow consumer
  doesn't slow publishers — instead, the queue overflows and the consumer
  receives an `OVERFLOW_SENTINEL` telling it to refetch from scratch.
  The publisher never awaits a slow consumer.
- **No persistence in v1.3.** Events are ephemeral. Reconnecting clients
  bring `Last-Event-ID` — the SSE handler can backfill from the existing
  audit log (`projects_recent_changes.fetch_recent_changes`) starting at
  the timestamp the client knows about. So the bus itself is pure
  in-memory transport.
- **Multi-instance future:** for >1 Cloud Run instance, swap `publish`
  to also write to GCP Pub/Sub (topic-per-project), and have each
  FastAPI worker pull-subscribe and re-fan into its local `_subscribers`.
  The `RealtimeEventBus` public surface (`publish`, `subscribe`,
  `unsubscribe`) is the upgrade boundary. For v1.3 we are explicitly
  single-instance + client reconnect-on-drop (Cloud Run `--timeout=3600`),
  so the in-process bus is sufficient.

Event shape (canonical — what publishers MUST emit)
---------------------------------------------------
    {
        "type":            str,  # "task.updated" / ".created" / ".deleted" / ...
        "entity_type":     str,  # "task" | "project" | "sprint" | ...
        "entity_id":       str,
        "project_id":      str,
        "actor_user_id":   str,
        "actor_type":      str,  # "user" | "pmo_ai" | "automation"
        "at":              str,  # ISO-8601 UTC
        "payload":         dict, # full updated entity doc (what the API returned)
        "event_id":        str,  # assigned by the bus on publish — DO NOT set yourself
    }

The SSE handler emits `event_id` as the SSE frame id so the client can
echo it back as `Last-Event-ID` on reconnect.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from typing import Any, AsyncIterator, Dict, Set

logger = logging.getLogger(__name__)


# Per-subscriber queue depth. Tuned for a normal PMO session: a burst of
# ~20 bulk-status updates lands in the queue, the SSE consumer drains in
# one event-loop tick. If a tab is backgrounded and the queue fills, we
# send the overflow sentinel and the client refetches the project list.
_QUEUE_MAXSIZE = 256

# Sentinel value the SSE handler can detect to translate into a
# `: refetch\n\n` SSE comment — instructs the client to drop optimistic
# state and re-fetch the full task list. We use an opaque `object()`
# instance so identity comparison (`event is OVERFLOW_SENTINEL`) is the
# only check needed — and the sentinel cannot be accidentally mutated,
# deep-copied, or confused with a regular event dict.
OVERFLOW_SENTINEL: Any = object()


class _Subscription:
    """Per-connection state: bounded queue + stable id for explicit unsubscribe."""

    __slots__ = ("id", "queue")

    def __init__(self) -> None:
        self.id = uuid.uuid4().hex
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)


class RealtimeEventBus:
    """Single-process async pub/sub keyed by topic string."""

    def __init__(self) -> None:
        # topic -> set of subscriptions.
        self._subscribers: Dict[str, Set[_Subscription]] = defaultdict(set)
        # Guards mutation of `_subscribers` from concurrent
        # subscribe/unsubscribe/publish. Publish only holds the lock long
        # enough to snapshot the subscriber set, so a slow consumer can't
        # block other subscribers from receiving the same event.
        self._lock = asyncio.Lock()

    async def publish(self, topic: str, event: dict) -> None:
        """Fan an event out to every subscriber of `topic`.

        Stamps `event_id` (uuid4 hex) on the event so the SSE handler can
        use it as the SSE frame id. Callers should not set event_id.

        Non-blocking on slow consumers: if a subscriber's queue is full,
        we drop the event for *that subscriber only* and enqueue the
        overflow sentinel so its SSE handler can tell the client to
        resync. Other subscribers still receive the event normally.
        """
        if "event_id" not in event:
            event["event_id"] = uuid.uuid4().hex

        # Snapshot under the lock, then release before doing per-queue
        # puts. `put_nowait` doesn't suspend, so we never block here.
        async with self._lock:
            subs = list(self._subscribers.get(topic, ()))

        if not subs:
            return

        for sub in subs:
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "realtime_bus overflow topic=%s subscription=%s; signaling resync",
                    topic,
                    sub.id,
                )
                # QA medium finding: if the queue is *completely* full, a naive
                # put_nowait of the sentinel will also fail and the subscriber
                # will silently stop receiving events until they disconnect and
                # reconnect. Force-evict the oldest queued item to guarantee the
                # sentinel reaches the subscriber. The subscriber is already in
                # a "resync from scratch" state at this point, so dropping the
                # oldest event is safe — it'll be refetched.
                try:
                    sub.queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover — race window
                    # Drained between QueueFull and now; nothing to evict.
                    pass
                try:
                    sub.queue.put_nowait(OVERFLOW_SENTINEL)
                except asyncio.QueueFull:  # pragma: no cover — should be unreachable
                    # We just freed a slot; if this still raises, something
                    # else is concurrently filling the queue at exactly the
                    # wrong moment. Log and move on — the consumer will
                    # eventually disconnect and reconnect.
                    logger.error(
                        "realtime_bus could not enqueue OVERFLOW_SENTINEL after eviction "
                        "topic=%s subscription=%s",
                        topic,
                        sub.id,
                    )

    async def subscribe(self, topic: str) -> AsyncIterator[Any]:
        """Subscribe to `topic` and yield events as they arrive.

        Usage:
            async for event in bus.subscribe(topic):
                if event is OVERFLOW_SENTINEL:
                    ...  # tell client to resync
                ...
                if should_stop:
                    break  # generator's finally cleans up

        The async generator's `finally` removes the subscription, so
        breaking out of the loop is the canonical cleanup path. The
        explicit `unsubscribe()` method below exists for parity with the
        future Pub/Sub upgrade.
        """
        sub = _Subscription()
        async with self._lock:
            self._subscribers[topic].add(sub)
        try:
            while True:
                event = await sub.queue.get()
                yield event
        finally:
            async with self._lock:
                bucket = self._subscribers.get(topic)
                if bucket is not None:
                    bucket.discard(sub)
                    if not bucket:
                        # Empty bucket — drop the topic to keep memory tidy.
                        self._subscribers.pop(topic, None)

    async def unsubscribe(self, topic: str, subscription_id: str) -> None:
        """Explicit unsubscribe by id. Reserved for the Pub/Sub upgrade
        path. In v1.3 the async-generator `finally` in subscribe() is
        the canonical cleanup; this is rarely called."""
        async with self._lock:
            bucket = self._subscribers.get(topic)
            if bucket is None:
                return
            for sub in list(bucket):
                if sub.id == subscription_id:
                    bucket.discard(sub)
                    break
            if bucket is not None and not bucket:
                self._subscribers.pop(topic, None)

    def subscriber_count(self, topic: str) -> int:
        """Diagnostics — how many SSE clients are listening on this topic.
        Used by tests and future health checks."""
        return len(self._subscribers.get(topic, ()))


# Singleton instance imported by all mutation routes + the SSE handler.
realtime_bus = RealtimeEventBus()


def project_topic(project_id: str) -> str:
    """Canonical topic name for a project. Centralized so every emission
    site (projects_task.py, future projects_project.py / projects_sprint.py
    additions) uses the same string."""
    return f"project:{project_id}"