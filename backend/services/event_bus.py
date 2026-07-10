"""
Event Bus — Two coexisting subsystems.

1. Workflow automation bus (existing): build_event/emit_event/get_event_bus.
   Fire-and-forget via asyncio.create_task. Backends: local | pubsub.
   Used by native route files (deals, contracts, ap_bills, etc.) and the
   workflow_engine consumer.

2. Ingestion event bus (new — Contract A): Event TypedDict + emit() +
   register_listener(). Sequential, in-process, error-isolated dispatch
   with glob pattern matching. Used by the ingestion CRUD envelope and
   its policy/audit/automation listeners.

The two are intentionally separate. The ingestion path needs predictable
ordering and per-listener error isolation (so a failing audit write doesn't
swallow the policy decision); the workflow bus is fire-and-forget by design.
Same module, different contracts.
"""

import os
import asyncio
import fnmatch
import json
import logging
import time
import traceback
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, List, Optional, TypedDict

logger = logging.getLogger(__name__)


# ==================== EVENT PAYLOAD ====================

def build_event(
    module: str,
    action: str,
    record: dict,
    old_record: Optional[dict] = None,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    chain_depth: int = 0,
) -> dict:
    """Build a standardized event payload."""
    changed_fields = {}
    if old_record and record:
        for key in set(list(record.keys()) + list(old_record.keys())):
            if key.startswith("_"):
                continue
            old_val = old_record.get(key)
            new_val = record.get(key)
            if old_val != new_val:
                changed_fields[key] = {"old": _serialize(old_val), "new": _serialize(new_val)}

    return {
        "module": module,
        "action": action,
        "record": _serialize(record),
        "old_record": _serialize(old_record) if old_record else None,
        "changed_fields": changed_fields,
        "user_id": user_id,
        "user_email": user_email,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "chain_depth": chain_depth,
    }


def _serialize(obj: Any) -> Any:
    """Make an object JSON-safe (handle ObjectId, datetime, etc.)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items() if k != "_id"}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__str__") and type(obj).__name__ == "ObjectId":
        return str(obj)
    return obj


# ==================== EVENT BUS ABC ====================

class EventBus(ABC):
    @abstractmethod
    async def publish(self, event: dict) -> None: ...

    @abstractmethod
    def subscribe(self, handler: Callable) -> None: ...


# ==================== LOCAL EVENT BUS ====================

class LocalEventBus(EventBus):
    """In-process event bus using asyncio.create_task for fire-and-forget."""

    def __init__(self):
        self._handlers: List[Callable] = []

    async def publish(self, event: dict) -> None:
        for handler in self._handlers:
            asyncio.create_task(self._safe_call(handler, event))

    def subscribe(self, handler: Callable) -> None:
        self._handlers.append(handler)
        logger.info(f"Event handler registered: {handler.__name__}")

    async def _safe_call(self, handler: Callable, event: dict) -> None:
        try:
            await handler(event)
        except Exception as e:
            logger.error(f"Event handler {handler.__name__} failed: {e}", exc_info=True)


# ==================== PUB/SUB EVENT BUS ====================

class PubSubEventBus(EventBus):
    """Google Cloud Pub/Sub — publishes to single 'aegisaxiom-events' topic.

    GCP setup: 1 topic + 1 push subscription. Module/action are message attributes.
    """

    def __init__(self):
        self._publisher = None
        self._topic_path = None
        self._handlers: List[Callable] = []

        try:
            from google.cloud import pubsub_v1
            project_id = os.environ.get("GCP_PROJECT_ID", "")
            topic_name = os.environ.get("PUBSUB_TOPIC", "aegisaxiom-events")
            if project_id:
                self._publisher = pubsub_v1.PublisherClient()
                self._topic_path = self._publisher.topic_path(project_id, topic_name)
                logger.info(f"Pub/Sub event bus initialized: {self._topic_path}")
            else:
                logger.warning("GCP_PROJECT_ID not set — Pub/Sub disabled, using local handlers only")
        except ImportError:
            logger.warning("google-cloud-pubsub not installed — using local handlers only")

    async def publish(self, event: dict) -> None:
        # Pub/Sub publish (non-blocking)
        if self._publisher and self._topic_path:
            asyncio.create_task(self._async_publish(event))

        # Also call local handlers (for in-process workflow engine)
        for handler in self._handlers:
            asyncio.create_task(self._safe_call(handler, event))

    def subscribe(self, handler: Callable) -> None:
        self._handlers.append(handler)
        logger.info(f"Event handler registered: {handler.__name__}")

    async def _async_publish(self, event: dict) -> None:
        try:
            data = json.dumps(event, default=str).encode("utf-8")
            future = self._publisher.publish(
                self._topic_path,
                data,
                module=event.get("module", ""),
                action=event.get("action", ""),
            )
            await asyncio.to_thread(future.result, timeout=5)
        except Exception as e:
            logger.error(f"Pub/Sub publish failed: {e}")

    async def _safe_call(self, handler: Callable, event: dict) -> None:
        try:
            await handler(event)
        except Exception as e:
            logger.error(f"Event handler {handler.__name__} failed: {e}", exc_info=True)


# ==================== SINGLETON ====================

_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get or create the singleton event bus instance."""
    global _bus
    if _bus is None:
        backend = os.environ.get("EVENT_BUS_BACKEND", "local")
        if backend == "pubsub":
            _bus = PubSubEventBus()
        else:
            _bus = LocalEventBus()
        logger.info(f"Event bus initialized: {backend}")
    return _bus


async def emit_event(
    module: str,
    action: str,
    record: dict,
    old_record: Optional[dict] = None,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    chain_depth: int = 0,
) -> None:
    """Public API — call from any route handler to emit an event.

    Fire-and-forget. Errors are logged, never raised to the caller.
    """
    try:
        event = build_event(module, action, record, old_record, user_id, user_email, chain_depth)
        bus = get_event_bus()
        await bus.publish(event)
    except Exception as e:
        logger.error(f"emit_event failed for {module}.{action}: {e}", exc_info=True)


# ============================================================================
# INGESTION EVENT BUS (Contract A)
# ----------------------------------------------------------------------------
# Sequential, in-process dispatch with per-listener error isolation and glob
# pattern matching. Used by the ingestion CRUD envelope (services/ingestion/
# route_helpers.py) to fan out created/updated/deleted/status_changed events
# to policy, audit, and automation listeners.
#
# Distinct from the workflow bus above. Do not merge them — they have
# different ordering and error-handling guarantees on purpose.
# ============================================================================


EventName = str  # "{module}.{entity}.{action}" — e.g., "davies_pmo.project.created"


class Event(TypedDict):
    """Canonical event payload emitted by ingestion CRUD operations.

    The shape is normative — listeners depend on every key being present.
    `previous_state` is None for `created`; populated for updates/deletes/
    status_changed.
    """
    name: EventName
    module: str
    entity: str
    action: str  # "created" | "updated" | "deleted" | "status_changed"
    resource_id: str
    actor_id: str
    actor_email: str
    timestamp: datetime
    payload: dict
    previous_state: Optional[dict]
    request_id: str


# Module-level listener registry. Kept as a list of (pattern, handler) tuples
# in registration order. A list (not a dict) because patterns are globs, not
# keys — multiple registrations for the same pattern are valid.
#
# Intended to be populated at import time from a listeners module wired into
# server.py startup. Runtime mutation works but isn't the supported pattern;
# listeners that race startup will silently miss early events.
_listeners: List[tuple[str, Callable[[Event], Awaitable[None]]]] = []

# 50ms total per emit. Observability only — we log a warning, never abort.
_EMIT_LATENCY_BUDGET_MS = 50.0


def register_listener(
    event_pattern: str,
    handler: Callable[[Event], Awaitable[None]],
) -> None:
    """Register a listener for events matching `event_pattern`.

    Patterns use fnmatch glob syntax against `event.name`:
      - exact:        "davies_pmo.project.created"
      - module-glob:  "davies_pmo.*"
      - entity-glob:  "davies_pmo.project.*"
      - action-glob:  "*.*.status_changed"
      - wildcard:     "*"

    Listeners run sequentially in registration order. Call this at module
    import time from a listeners module wired into server startup.
    """
    _listeners.append((event_pattern, handler))
    logger.info(
        "Ingestion listener registered: pattern=%s handler=%s",
        event_pattern,
        getattr(handler, "__name__", repr(handler)),
    )


async def emit(event: Event) -> None:
    """Dispatch `event` to every matching registered listener, sequentially.

    Guarantees:
      - Listeners are called in registration order.
      - A failing listener does NOT abort the dispatch; the error is logged
        with the event's request_id for correlation, and the next listener
        runs as if nothing happened.
      - This function NEVER raises. The caller (a CRUD route) cannot have
        its request fail because of a listener bug.

    Latency budget is 50ms total — exceeding it logs a warning. We do not
    enforce a hard timeout; that would risk dropping audit/policy work
    silently, which is worse than a slow request.
    """
    matched = [
        (pattern, handler)
        for pattern, handler in _listeners
        if fnmatch.fnmatch(event["name"], pattern)
    ]

    logger.info(
        "event_bus.emit name=%s module=%s entity=%s action=%s request_id=%s listeners=%d",
        event["name"],
        event["module"],
        event["entity"],
        event["action"],
        event["request_id"],
        len(matched),
    )

    emit_start = time.perf_counter()

    for pattern, handler in matched:
        handler_name = getattr(handler, "__name__", repr(handler))
        listener_start = time.perf_counter()
        try:
            await handler(event)
        except Exception:
            # Error isolation: log full traceback, keep dispatching. Never
            # let a listener bug surface to the emitter's caller.
            logger.error(
                "event_bus listener failed: pattern=%s handler=%s event=%s "
                "request_id=%s\n%s",
                pattern,
                handler_name,
                event["name"],
                event["request_id"],
                traceback.format_exc(),
            )
        finally:
            duration_ms = (time.perf_counter() - listener_start) * 1000
            logger.debug(
                "event_bus listener done: handler=%s pattern=%s duration_ms=%.2f request_id=%s",
                handler_name,
                pattern,
                duration_ms,
                event["request_id"],
            )

    total_ms = (time.perf_counter() - emit_start) * 1000
    if total_ms > _EMIT_LATENCY_BUDGET_MS:
        logger.warning(
            "event_bus.emit exceeded latency budget: name=%s request_id=%s "
            "duration_ms=%.2f budget_ms=%.1f listeners=%d",
            event["name"],
            event["request_id"],
            total_ms,
            _EMIT_LATENCY_BUDGET_MS,
            len(matched),
        )
