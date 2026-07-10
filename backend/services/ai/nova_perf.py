"""Storage-agnostic performance instrumentation for Nova chat turns.

Phase 0 of the Nova performance roadmap: measure first, optimize
second. This module exposes a small, dependency-free helper surface
that callers in the AI conductor use to record per-turn timings,
token usage, and payload sizing, then emit a single structured log
line per turn.

Design rules:

  * Span names are **storage-agnostic**. We say ``data_access_ms`` and
    ``source_query_ms``, never ``mongo_query_ms`` / ``sql_query_ms``
    / ``collection_scan_ms``. The names must survive a future storage
    swap without renaming.
  * Helpers must **never raise**. If payload sizing or row extraction
    fails, return ``None`` — instrumentation must not break chat.
  * No raw user message, customer/vendor names, or finance rows in
    perf logs. The dataclass intentionally has no field for any of
    these; if a future caller is tempted to stuff one in, they need
    to add the field first, which surfaces the policy violation in
    review.
  * One structured ``[nova_perf]`` line per Nova turn is enough.
    The logging prefix is fixed so downstream log pipelines can
    grep / parse it without ambiguity.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger(__name__)


_PERF_LOG_PREFIX = "[nova_perf]"


@dataclass
class NovaPerfRecord:
    """Per-Nova-turn timing/usage record.

    Every field is optional. Callers populate what they can measure;
    unmeasured spans stay ``None`` and are omitted from the emitted
    log line. ``log_nova_perf`` is responsible for serialization —
    callers should not format the line themselves.

    Field names are the storage-agnostic span vocabulary fixed in the
    Phase 0 roadmap. Do not add storage-engine-specific names
    (``mongo_query_ms``, ``sql_query_ms``, ``collection_scan_ms``,
    ``aggregation_ms``, ``collection_name``, ``table_name``).
    """

    route: str | None = None
    tool_name: str | None = None

    total_request_ms: float | None = None
    route_ms: float | None = None
    planner_ms: float | None = None
    tool_selection_ms: float | None = None
    tool_execution_ms: float | None = None
    data_access_ms: float | None = None
    source_query_ms: float | None = None
    answer_llm_ms: float | None = None
    serialization_ms: float | None = None
    frontend_render_ms: float | None = None

    rows_returned: int | None = None
    payload_bytes: int | None = None

    llm_input_tokens: int | None = None
    llm_output_tokens: int | None = None
    llm_cache_read_tokens: int | None = None
    llm_cache_write_tokens: int | None = None

    warnings: list[str] = field(default_factory=list)


def now_perf() -> float:
    """Monotonic high-resolution timer reference.

    Use the return value as the ``start`` argument to ``elapsed_ms``.
    Wraps ``time.perf_counter`` so callers do not import ``time``
    directly and the unit-of-measure is consistent across the
    instrumentation surface.
    """
    return time.perf_counter()


def elapsed_ms(start: float) -> float:
    """Return milliseconds elapsed since ``start``, rounded to 2 dp.

    Never raises. A ``start`` from a different process or a wall-clock
    drift will produce a meaningless number, but no exception — the
    caller's perf accounting will look wrong but chat will keep
    serving traffic.
    """
    try:
        return round((time.perf_counter() - float(start)) * 1000.0, 2)
    except Exception:  # noqa: BLE001 — instrumentation must never raise
        return 0.0


def estimate_payload_bytes(value: Any) -> int | None:
    """Best-effort UTF-8 byte size of a Python value when serialized as JSON.

    Used to size tool result payloads passed to the answer LLM, so we
    can correlate slow LLM rounds with large context. Returns ``None``
    when the value isn't JSON-serializable (e.g. a Mongo ObjectId
    instance, an open file handle, a circular reference) — callers
    should treat ``None`` as "unknown size" rather than zero.
    """
    if value is None:
        return None
    try:
        return len(
            json.dumps(value, default=str, separators=(",", ":")).encode("utf-8")
        )
    except Exception:  # noqa: BLE001 — sizing must never raise
        return None


def extract_rows_returned(value: Any) -> int | None:
    """Best-effort row count from a Nova tool result dict.

    Nova tools surface row counts under several conventional keys:

      * ``rows_returned`` / ``row_count`` / ``total_matched`` —
        already-numeric counts the tool computed itself. Preferred,
        since they may reflect a count larger than the preview list
        the tool actually serialized.
      * ``rows`` / ``returned`` / ``preview_rows`` — list of row
        dicts. Fall back to ``len(...)`` when no numeric field is
        present.

    Returns ``None`` when the value isn't a dict or none of the
    known fields are usable. Storage-agnostic: never inspects
    Mongo/SQL plumbing.
    """
    if not isinstance(value, dict):
        return None

    for key in ("rows_returned", "row_count", "total_matched"):
        n = value.get(key)
        if isinstance(n, int) and not isinstance(n, bool):
            return n

    for key in ("rows", "returned", "preview_rows"):
        rows = value.get(key)
        if isinstance(rows, list):
            return len(rows)

    return None


def log_nova_perf(record: NovaPerfRecord) -> None:
    """Emit a single structured ``[nova_perf]`` log line for a turn.

    Fields with ``None`` or empty-list values are omitted so the line
    stays readable. The output is space-separated ``key=value`` pairs,
    grep-friendly for ad-hoc analysis and parser-friendly for
    structured log pipelines.

    Never raises — a logging or formatting failure must not bubble
    out of the perf hook.
    """
    try:
        fields_in_order = {
            "route": record.route,
            "tool": record.tool_name,
            "total_request_ms": record.total_request_ms,
            "route_ms": record.route_ms,
            "planner_ms": record.planner_ms,
            "tool_selection_ms": record.tool_selection_ms,
            "tool_execution_ms": record.tool_execution_ms,
            "data_access_ms": record.data_access_ms,
            "source_query_ms": record.source_query_ms,
            "answer_llm_ms": record.answer_llm_ms,
            "serialization_ms": record.serialization_ms,
            "frontend_render_ms": record.frontend_render_ms,
            "rows_returned": record.rows_returned,
            "payload_bytes": record.payload_bytes,
            "llm_input_tokens": record.llm_input_tokens,
            "llm_output_tokens": record.llm_output_tokens,
            "llm_cache_read_tokens": record.llm_cache_read_tokens,
            "llm_cache_write_tokens": record.llm_cache_write_tokens,
            "warnings": record.warnings or None,
        }
        compact = " ".join(
            f"{k}={v}"
            for k, v in fields_in_order.items()
            if v is not None and v != []
        )
        logger.info("%s %s", _PERF_LOG_PREFIX, compact)
    except Exception:  # noqa: BLE001 — logging must never raise
        pass


__all__ = [
    "NovaPerfRecord",
    "elapsed_ms",
    "estimate_payload_bytes",
    "extract_rows_returned",
    "log_nova_perf",
    "now_perf",
]
