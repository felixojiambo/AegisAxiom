"""Tests for the Phase 0 storage-agnostic Nova perf helper.

The helper lives at ``services/ai/nova_perf.py`` and is consumed by
the conductor at ``services/ai/conductor.py`` to emit one structured
``[nova_perf]`` log line per Nova turn. These tests pin:

  * timing primitives never return absurd values
  * payload sizing handles serializable AND non-serializable inputs
    without raising
  * row-count extraction follows the documented field-priority order
    (rows_returned > row_count > total_matched > len(rows) >
    len(returned) > len(preview_rows))
  * the emitted log line carries the ``[nova_perf]`` prefix
  * the emitted log line uses storage-agnostic span names only —
    never ``mongo_query_ms`` / ``sql_query_ms`` / ``collection_scan_ms``
    / ``aggregation_ms`` / ``collection_name`` / ``table_name``
  * the helper does NOT carry, accept, or emit raw user message text

All expected values are computed from synthetic fixtures in-test.
"""
from __future__ import annotations

import logging
import os
import time

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

import pytest

from services.ai.nova_perf import (
    NovaPerfRecord,
    elapsed_ms,
    estimate_payload_bytes,
    extract_rows_returned,
    log_nova_perf,
    now_perf,
)


# ---------------------------------------------------------------------------
# Forbidden field names — any of these in the helper or its emitted log
# means a storage-engine leaked into the perf vocabulary. Keep these
# list-driven so the grep is self-documenting in the assertion message.
# ---------------------------------------------------------------------------

_STORAGE_LEAK_NAMES = (
    "mongo_query_ms",
    "sql_query_ms",
    "collection_scan_ms",
    "aggregation_ms",
    "collection_name",
    "table_name",
)


# ---------------------------------------------------------------------------
# 1. elapsed_ms() returns a non-negative number
# ---------------------------------------------------------------------------


def test_elapsed_ms_returns_non_negative_number():
    start = now_perf()
    # Burn a deterministic but tiny amount of wall time so the assertion
    # isn't testing a no-op. The test only requires >= 0, not a specific
    # lower bound, so this stays portable across slow CI runners.
    time.sleep(0.001)
    elapsed = elapsed_ms(start)
    assert isinstance(elapsed, float)
    assert elapsed >= 0.0


# ---------------------------------------------------------------------------
# 2. estimate_payload_bytes returns a positive int for a small dict
# ---------------------------------------------------------------------------


def test_estimate_payload_bytes_returns_positive_int_for_small_dict():
    size = estimate_payload_bytes({"a": 1, "b": "two"})
    assert isinstance(size, int)
    assert size > 0


# ---------------------------------------------------------------------------
# 3. estimate_payload_bytes does not raise for non-JSON-serializable input
# ---------------------------------------------------------------------------


def test_estimate_payload_bytes_does_not_raise_for_non_serializable():
    class _CircularRef:
        pass

    a = _CircularRef()
    a.self = a  # noqa: SLF001 — building a deliberately bad input

    # Must return None (or a fallback int via ``default=str``), never
    # propagate an exception. The contract is "instrumentation can't
    # break chat" — and a circular reference is the classic
    # json.dumps blow-up.
    result = estimate_payload_bytes(a)
    assert result is None or isinstance(result, int)


# ---------------------------------------------------------------------------
# 4-9. extract_rows_returned field-priority + list fallbacks
# ---------------------------------------------------------------------------


def test_extract_rows_returned_reads_rows_returned():
    # When the tool already computed a count, prefer that over list len.
    assert extract_rows_returned({"rows_returned": 7, "rows": [1, 2]}) == 7


def test_extract_rows_returned_reads_row_count():
    assert extract_rows_returned({"row_count": 12}) == 12


def test_extract_rows_returned_reads_total_matched():
    # Nova tools sometimes report ``total_matched`` separately from the
    # preview slice they ship — the count is the audit-grade number.
    assert extract_rows_returned({"total_matched": 99, "preview_rows": [1, 2, 3]}) == 99


def test_extract_rows_returned_falls_back_to_len_rows():
    fixture = [{"k": i} for i in range(5)]
    assert extract_rows_returned({"rows": fixture}) == len(fixture)


def test_extract_rows_returned_falls_back_to_len_returned():
    fixture = [{"k": "a"}, {"k": "b"}, {"k": "c"}]
    assert extract_rows_returned({"returned": fixture}) == len(fixture)


def test_extract_rows_returned_falls_back_to_len_preview_rows():
    fixture = [{"k": i} for i in range(4)]
    assert extract_rows_returned({"preview_rows": fixture}) == len(fixture)


def test_extract_rows_returned_returns_none_for_non_dict():
    # Defensive: a tool returning a list directly (rare) must not blow up
    # the perf hook — None means "unknown row count", not zero.
    assert extract_rows_returned([1, 2, 3]) is None
    assert extract_rows_returned(None) is None


# ---------------------------------------------------------------------------
# 10-11. log_nova_perf emits the prefix and storage-neutral spans
# ---------------------------------------------------------------------------


def test_log_nova_perf_emits_prefix(caplog: pytest.LogCaptureFixture):
    record = NovaPerfRecord(
        route="nova",
        tool_name="query_finance_data",
        total_request_ms=2840.0,
        data_access_ms=310.0,
    )
    with caplog.at_level(logging.INFO, logger="services.ai.nova_perf"):
        log_nova_perf(record)

    messages = [r.getMessage() for r in caplog.records]
    assert any("[nova_perf]" in m for m in messages), messages


def test_log_nova_perf_includes_storage_neutral_field(
    caplog: pytest.LogCaptureFixture,
):
    record = NovaPerfRecord(
        route="nova",
        tool_name="query_gl_transactions",
        total_request_ms=1500.0,
        data_access_ms=420.5,
        tool_execution_ms=400.0,
    )
    with caplog.at_level(logging.INFO, logger="services.ai.nova_perf"):
        log_nova_perf(record)

    rendered = " ".join(r.getMessage() for r in caplog.records)
    assert "data_access_ms=420.5" in rendered
    assert "tool_execution_ms=400.0" in rendered


def test_log_nova_perf_emits_both_tool_selection_ms_and_answer_llm_ms(
    caplog: pytest.LogCaptureFixture,
):
    """A Nova turn with at least one tool call has two distinct LLM
    phases: the initial call_llm (tools passed, no tool results yet —
    the model is choosing which tool to invoke) and the follow-up
    call_llm (tool results passed back, model produces the answer).

    They are accumulated into different spans so the perf log can
    distinguish "LLM time spent picking a tool" from "LLM time spent
    writing the answer". Both must appear in the emitted line when
    both are populated."""
    record = NovaPerfRecord(
        route="nova",
        tool_name="query_finance_data",
        total_request_ms=2840.0,
        tool_selection_ms=420.0,
        tool_execution_ms=520.0,
        answer_llm_ms=1800.0,
    )
    with caplog.at_level(logging.INFO, logger="services.ai.nova_perf"):
        log_nova_perf(record)

    rendered = " ".join(r.getMessage() for r in caplog.records)
    assert "tool_selection_ms=420.0" in rendered
    assert "answer_llm_ms=1800.0" in rendered
    # And the two values must remain distinct keys in the line — a
    # regression that aliased one to the other would collapse them.
    assert rendered.count("tool_selection_ms=") == 1
    assert rendered.count("answer_llm_ms=") == 1


# ---------------------------------------------------------------------------
# 12. log_nova_perf does not include raw user message
# ---------------------------------------------------------------------------


def test_log_nova_perf_does_not_carry_user_message(
    caplog: pytest.LogCaptureFixture,
):
    record = NovaPerfRecord(
        route="nova",
        tool_name="query_finance_data",
        total_request_ms=100.0,
    )

    # The dataclass has no field that would let a caller pass a raw
    # user message in. Confirm that probe-style synthetic content
    # never appears in the emitted log even if someone tried to shove
    # it into one of the existing string fields (route/tool_name).
    probe = "REDACT_ME_SECRET_USER_TEXT_42"
    record.tool_name = probe  # caller would have to do this on purpose
    with caplog.at_level(logging.INFO, logger="services.ai.nova_perf"):
        log_nova_perf(record)

    rendered = " ".join(r.getMessage() for r in caplog.records)
    # ``tool_name=probe`` ends up in the line because the field is
    # tool_name. That's expected — the contract is that NO new field
    # named ``user_message`` (or similar) exists in the record.
    assert probe in rendered  # sanity: log actually fired
    # No record-level field carries user message content. Audit the
    # dataclass surface.
    forbidden_field_names = ("user_message", "message", "raw_message", "prompt")
    for name in forbidden_field_names:
        assert not hasattr(record, name), (
            f"NovaPerfRecord must not expose ``{name}`` — perf logs "
            f"must never carry raw user message text"
        )


# ---------------------------------------------------------------------------
# 13. No storage-engine-specific span names in helper or emitted log
# ---------------------------------------------------------------------------


def test_no_storage_engine_specific_field_names_on_record():
    record = NovaPerfRecord()
    for name in _STORAGE_LEAK_NAMES:
        assert not hasattr(record, name), (
            f"NovaPerfRecord must not expose storage-specific field "
            f"``{name}`` — Phase 0 vocabulary is storage-agnostic"
        )


def test_no_storage_engine_specific_names_in_emitted_log(
    caplog: pytest.LogCaptureFixture,
):
    record = NovaPerfRecord(
        route="nova",
        tool_name="query_finance_data",
        total_request_ms=2840.0,
        route_ms=120.0,
        planner_ms=40.0,
        tool_execution_ms=520.0,
        data_access_ms=310.0,
        source_query_ms=290.0,
        answer_llm_ms=1800.0,
        rows_returned=12,
        payload_bytes=8400,
        llm_input_tokens=8420,
        llm_output_tokens=290,
        llm_cache_read_tokens=0,
        llm_cache_write_tokens=0,
    )
    with caplog.at_level(logging.INFO, logger="services.ai.nova_perf"):
        log_nova_perf(record)

    rendered = " ".join(r.getMessage() for r in caplog.records)
    for name in _STORAGE_LEAK_NAMES:
        assert name not in rendered, (
            f"Emitted [nova_perf] line leaked storage-specific span "
            f"``{name}``: {rendered}"
        )


# ---------------------------------------------------------------------------
# 14-15. Lightweight integration check — instrumentation does not change
# the chat response shape, and does not break a tool error response.
#
# We avoid spinning the conductor under FastAPI here (the full chat
# pipeline pulls in Mongo, the LLM provider stack, and a real session
# row). Instead we pin the contract by importing the conductor module
# and confirming the perf hook helpers it depends on are importable —
# i.e., a typo in the helper module would surface as an ImportError
# in the conductor at chat time. The richer integration test is the
# existing ``test_nova_chat_conductor.py`` suite, which exercises
# the conductor end-to-end and would fail if the instrumentation
# changed the chat shape or broke a tool error response.
# ---------------------------------------------------------------------------


def test_conductor_imports_nova_perf_helpers_used_in_instrumentation():
    """If the conductor's local imports of nova_perf break, chat
    breaks. Catch typos here so the failure mode is a fast unit-test
    failure, not a runtime AttributeError mid-turn."""
    from services.ai import nova_perf as pp

    assert callable(pp.now_perf)
    assert callable(pp.elapsed_ms)
    assert callable(pp.estimate_payload_bytes)
    assert callable(pp.extract_rows_returned)
    assert callable(pp.log_nova_perf)
    # The conductor instantiates this dataclass on every Nova turn —
    # confirm the constructor accepts the route kw it passes.
    rec = pp.NovaPerfRecord(route="nova")
    assert rec.route == "nova"


def test_log_nova_perf_does_not_raise_on_malformed_record(
    caplog: pytest.LogCaptureFixture,
):
    """Tool error responses sometimes leave the record with mixed
    types (e.g. a ``warnings`` list with non-string entries). The
    helper must still emit, not raise — phase-0 instrumentation
    failure must never bubble out of the conductor."""

    record = NovaPerfRecord(route="nova", tool_name="query_finance_data")
    # Stuff in atypical types deliberately. The contract is "logging
    # must never raise". If a future refactor makes the helper strict,
    # this test catches it.
    record.warnings = ["string warning", 42, None]  # type: ignore[list-item]
    record.rows_returned = None
    record.payload_bytes = None

    with caplog.at_level(logging.INFO, logger="services.ai.nova_perf"):
        log_nova_perf(record)

    # As long as we got here without raising, the contract holds.
    # A best-effort assertion: something was emitted under the
    # nova_perf logger.
    assert any("[nova_perf]" in r.getMessage() for r in caplog.records)
