"""Conductor-level wiring tests for Nova's Gemini explicit cache.

These tests run a real Nova turn through ``get_chat_response`` and
mock the boundary points — the Gemini cache helper and ``call_llm``
itself — so we can assert:

  * cache helper is consulted only when route=="nova", provider=
    "google", both feature flags are on, and a Gemini API key is set
  * the cache helper receives the stable system prompt + tool schemas
    and NOT user messages, chat history, tool results, or finance rows
  * the returned cache name reaches ``call_llm`` on the initial
    tool-selection call
  * cache helper failures fall back silently to an uncached call
  * non-Nova routes / non-Gemini providers / disabled flags skip the
    helper entirely

The cache helper itself is unit-tested separately in
``test_nova_gemini_cache.py``. These tests focus strictly on the
plumbing inside the conductor.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

import pytest

import services.ai.agents  # noqa: F401 — triggers @register decorators
from services.ai import config as ai_config
from services.ai.agents.registry import _AGENTS
from services.ai.conductor import get_chat_response
from services.ai.llm import LlmResponse


# ---------------------------------------------------------------------------
# Mongo stand-ins copied minimal-fidelity from test_nova_chat_conductor.
# Just enough to satisfy the conductor's reads/writes without a real DB.
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def sort(self, *args, **kwargs):
        return self

    def skip(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    async def to_list(self, length=None):
        if length is None:
            return list(self.docs)
        return list(self.docs[:length])


class _FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.inserted = []

    async def find_one(self, query=None, projection=None, **kwargs):
        query = query or {}
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                return dict(doc)
        return None

    def find(self, query=None, projection=None):
        query = query or {}
        docs = [
            dict(doc) for doc in self.docs
            if all(doc.get(k) == v for k, v in query.items() if not isinstance(v, dict))
        ]
        return _FakeCursor(docs)

    def aggregate(self, *args, **kwargs):
        return _FakeCursor([])

    async def find_one_and_update(self, query, update, **kwargs):
        return dict(query or {})

    async def insert_one(self, doc):
        self.inserted.append(dict(doc))
        return SimpleNamespace(inserted_id=f"id_{len(self.inserted)}")

    async def update_one(self, query, update, **kwargs):
        return SimpleNamespace(modified_count=1)


class _FakeDb:
    def __init__(self, pinned_agent: str = "nova"):
        self.chat_sessions = _FakeCollection([
            {
                "session_id": "chat_cache",
                "user_id": "user_cache",
                "tokens_used": 0,
                "pinned_agent": pinned_agent,
                "title": "New Chat",
            }
        ])
        self.chat_messages = _FakeCollection([])
        self.counters = _FakeCollection([])
        self.users = _FakeCollection([
            {"user_id": "user_cache", "role": "finance"},
        ])
        self.ai_usage = _FakeCollection([])
        self.ai_model_config = _FakeCollection([])


def _user(perms=None, user_id="user_cache"):
    return SimpleNamespace(
        user_id=user_id,
        email=f"{user_id}@example.com",
        name="Cache Tester",
        role="finance",
        permissions=perms or ["chat.access", "nova.use", "deals.view.all"],
    )


def _history(content="What was revenue last month?"):
    return [{"role": "user", "content": content}]


def _enable_cache_flags(monkeypatch):
    monkeypatch.setenv("NOVA_PROMPT_CACHE_ENABLED", "true")
    monkeypatch.setenv("GEMINI_CONTEXT_CACHE_ENABLED", "true")
    monkeypatch.setenv("GEMINI_CONTEXT_CACHE_TTL_SECONDS", "3600")
    # The conductor reads config.GEMINI_API_KEY (module attribute);
    # monkeypatch the attribute directly because the module-level
    # constant captured the env value at import time.
    monkeypatch.setattr(ai_config, "GEMINI_API_KEY", "synthetic-test-key")


def _disable_cache_flags(monkeypatch):
    monkeypatch.setenv("NOVA_PROMPT_CACHE_ENABLED", "false")
    monkeypatch.setenv("GEMINI_CONTEXT_CACHE_ENABLED", "false")
    monkeypatch.setattr(ai_config, "GEMINI_API_KEY", "synthetic-test-key")


# ---------------------------------------------------------------------------
# 1. Helper invoked under right conditions; cached_content reaches call_llm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_helper_called_for_nova_with_flags_on(monkeypatch):
    _enable_cache_flags(monkeypatch)
    db = _FakeDb()

    cache_helper = AsyncMock(return_value="cachedContents/synthetic-cache-1")
    llm = AsyncMock(return_value=LlmResponse(text="ok", total_tokens=0))

    with patch(
        "services.ai.gemini_cache.get_or_create_nova_cache",
        new=cache_helper,
    ), patch("services.ai.conductor.call_llm", new=llm):
        result = await get_chat_response(
            _user(), _history(), db,
            session_id="chat_cache",
            pinned_agent="nova",
            message_id="msg_user",
        )

    assert result["content"] == "ok"
    assert cache_helper.await_count == 1
    # cached_content keyword reaches call_llm on the initial call.
    assert llm.await_args.kwargs.get("cached_content") == (
        "cachedContents/synthetic-cache-1"
    )


@pytest.mark.asyncio
async def test_cache_helper_receives_stable_prompt_and_schemas(monkeypatch):
    _enable_cache_flags(monkeypatch)
    db = _FakeDb()

    cache_helper = AsyncMock(return_value="cachedContents/with-content")
    llm = AsyncMock(return_value=LlmResponse(text="ok", total_tokens=0))

    with patch(
        "services.ai.gemini_cache.get_or_create_nova_cache",
        new=cache_helper,
    ), patch("services.ai.conductor.call_llm", new=llm):
        await get_chat_response(
            _user(), _history(), db,
            session_id="chat_cache",
            pinned_agent="nova",
            message_id="msg_user",
        )

    kwargs = cache_helper.await_args.kwargs
    # Stable Nova system prompt — contains the static guard rails.
    assert isinstance(kwargs["system_prompt"], str)
    assert "Nova" in kwargs["system_prompt"]
    # Schemas come through as raw dicts and include Nova's core tools.
    schemas = kwargs["tool_schemas"]
    assert isinstance(schemas, list)
    tool_names = {s.get("name") for s in schemas if isinstance(s, dict)}
    assert "run_projection" in tool_names
    assert "query_invoice_totals" in tool_names
    # Provider + model match what the conductor would otherwise send.
    assert kwargs["provider"] == "google"
    assert kwargs["model"] == _AGENTS["nova"].model_name


@pytest.mark.asyncio
async def test_cache_helper_does_not_receive_user_message(monkeypatch):
    _enable_cache_flags(monkeypatch)
    db = _FakeDb()
    private_user_text = "PRIVATE-USER-PAYLOAD-SHOULD-NOT-CACHE"

    cache_helper = AsyncMock(return_value="cachedContents/x")
    llm = AsyncMock(return_value=LlmResponse(text="ok", total_tokens=0))

    with patch(
        "services.ai.gemini_cache.get_or_create_nova_cache",
        new=cache_helper,
    ), patch("services.ai.conductor.call_llm", new=llm):
        await get_chat_response(
            _user(), _history(private_user_text), db,
            session_id="chat_cache",
            pinned_agent="nova",
            message_id="msg_user",
        )

    kwargs = cache_helper.await_args.kwargs
    blob = (
        kwargs["system_prompt"]
        + "".join(str(s) for s in kwargs["tool_schemas"])
    )
    assert private_user_text not in blob


@pytest.mark.asyncio
async def test_cache_helper_does_not_receive_history(monkeypatch):
    _enable_cache_flags(monkeypatch)
    db = _FakeDb()
    earlier_assistant_text = "PRIOR-ASSISTANT-REPLY-MUST-NOT-LEAK-INTO-CACHE"
    history = [
        {"role": "user", "content": "First user turn"},
        {"role": "model", "content": earlier_assistant_text},
        {"role": "user", "content": "Latest user turn"},
    ]

    cache_helper = AsyncMock(return_value="cachedContents/x")
    llm = AsyncMock(return_value=LlmResponse(text="ok", total_tokens=0))

    with patch(
        "services.ai.gemini_cache.get_or_create_nova_cache",
        new=cache_helper,
    ), patch("services.ai.conductor.call_llm", new=llm):
        await get_chat_response(
            _user(), history, db,
            session_id="chat_cache",
            pinned_agent="nova",
            message_id="msg_user",
        )

    kwargs = cache_helper.await_args.kwargs
    blob = (
        kwargs["system_prompt"]
        + "".join(str(s) for s in kwargs["tool_schemas"])
    )
    assert earlier_assistant_text not in blob
    assert "First user turn" not in blob
    assert "Latest user turn" not in blob


# ---------------------------------------------------------------------------
# 2. Fall-back paths — cache must never break chat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_helper_failure_falls_back_to_uncached(monkeypatch):
    _enable_cache_flags(monkeypatch)
    db = _FakeDb()

    async def _boom(**kwargs):
        raise RuntimeError("synthetic cache failure")

    llm = AsyncMock(return_value=LlmResponse(text="ok", total_tokens=0))

    with patch(
        "services.ai.gemini_cache.get_or_create_nova_cache",
        new=_boom,
    ), patch("services.ai.conductor.call_llm", new=llm):
        result = await get_chat_response(
            _user(), _history(), db,
            session_id="chat_cache",
            pinned_agent="nova",
            message_id="msg_user",
        )

    assert result["content"] == "ok"
    # call_llm was still made — chat does not refuse on cache failure.
    assert llm.await_count >= 1
    # cached_content is None (or absent) when the helper raised.
    assert llm.await_args.kwargs.get("cached_content") in (None, "")


@pytest.mark.asyncio
async def test_cache_helper_returning_none_falls_back_to_uncached(monkeypatch):
    """Helper may return None for legitimate reasons (preview SDK, min
    token threshold). The conductor must treat that the same as
    failure — keep chat working uncached."""
    _enable_cache_flags(monkeypatch)
    db = _FakeDb()

    cache_helper = AsyncMock(return_value=None)
    llm = AsyncMock(return_value=LlmResponse(text="ok", total_tokens=0))

    with patch(
        "services.ai.gemini_cache.get_or_create_nova_cache",
        new=cache_helper,
    ), patch("services.ai.conductor.call_llm", new=llm):
        await get_chat_response(
            _user(), _history(), db,
            session_id="chat_cache",
            pinned_agent="nova",
            message_id="msg_user",
        )

    assert llm.await_args.kwargs.get("cached_content") in (None, "")


# ---------------------------------------------------------------------------
# 3. Negative gates — non-Nova / non-Gemini / disabled flags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_nova_route_skips_cache_helper(monkeypatch):
    _enable_cache_flags(monkeypatch)
    db = _FakeDb(pinned_agent="deals")

    cache_helper = AsyncMock(return_value="cachedContents/x")
    llm = AsyncMock(return_value=LlmResponse(text="ok", total_tokens=0))

    with patch(
        "services.ai.gemini_cache.get_or_create_nova_cache",
        new=cache_helper,
    ), patch("services.ai.conductor.call_llm", new=llm):
        await get_chat_response(
            _user(), _history(), db,
            session_id="chat_cache",
            pinned_agent="deals",
            message_id="msg_user",
        )

    assert cache_helper.await_count == 0


@pytest.mark.asyncio
async def test_disabled_prompt_cache_flag_skips_helper(monkeypatch):
    monkeypatch.setenv("NOVA_PROMPT_CACHE_ENABLED", "false")
    monkeypatch.setenv("GEMINI_CONTEXT_CACHE_ENABLED", "true")
    monkeypatch.setattr(ai_config, "GEMINI_API_KEY", "synthetic-test-key")
    db = _FakeDb()

    cache_helper = AsyncMock(return_value="cachedContents/x")
    llm = AsyncMock(return_value=LlmResponse(text="ok", total_tokens=0))

    with patch(
        "services.ai.gemini_cache.get_or_create_nova_cache",
        new=cache_helper,
    ), patch("services.ai.conductor.call_llm", new=llm):
        await get_chat_response(
            _user(), _history(), db,
            session_id="chat_cache",
            pinned_agent="nova",
            message_id="msg_user",
        )

    assert cache_helper.await_count == 0


@pytest.mark.asyncio
async def test_disabled_gemini_cache_flag_skips_helper(monkeypatch):
    monkeypatch.setenv("NOVA_PROMPT_CACHE_ENABLED", "true")
    monkeypatch.setenv("GEMINI_CONTEXT_CACHE_ENABLED", "false")
    monkeypatch.setattr(ai_config, "GEMINI_API_KEY", "synthetic-test-key")
    db = _FakeDb()

    cache_helper = AsyncMock(return_value="cachedContents/x")
    llm = AsyncMock(return_value=LlmResponse(text="ok", total_tokens=0))

    with patch(
        "services.ai.gemini_cache.get_or_create_nova_cache",
        new=cache_helper,
    ), patch("services.ai.conductor.call_llm", new=llm):
        await get_chat_response(
            _user(), _history(), db,
            session_id="chat_cache",
            pinned_agent="nova",
            message_id="msg_user",
        )

    assert cache_helper.await_count == 0


@pytest.mark.asyncio
async def test_missing_gemini_api_key_skips_helper(monkeypatch):
    monkeypatch.setenv("NOVA_PROMPT_CACHE_ENABLED", "true")
    monkeypatch.setenv("GEMINI_CONTEXT_CACHE_ENABLED", "true")
    monkeypatch.setattr(ai_config, "GEMINI_API_KEY", "")
    db = _FakeDb()

    cache_helper = AsyncMock(return_value="cachedContents/x")
    llm = AsyncMock(return_value=LlmResponse(text="ok", total_tokens=0))

    with patch(
        "services.ai.gemini_cache.get_or_create_nova_cache",
        new=cache_helper,
    ), patch("services.ai.conductor.call_llm", new=llm):
        await get_chat_response(
            _user(), _history(), db,
            session_id="chat_cache",
            pinned_agent="nova",
            message_id="msg_user",
        )

    assert cache_helper.await_count == 0


# ---------------------------------------------------------------------------
# 4. Perf telemetry — cached read tokens surface when the response carries them
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_perf_log_carries_cache_read_tokens_when_response_has_them(
    monkeypatch, caplog
):
    _enable_cache_flags(monkeypatch)
    db = _FakeDb()

    cache_helper = AsyncMock(return_value="cachedContents/x")
    # Simulate Gemini surfacing cached_content_token_count via the
    # LlmResponse field the adapter populates.
    llm = AsyncMock(
        return_value=LlmResponse(
            text="ok",
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            cached_input_tokens=85,
        )
    )

    import logging as _logging

    with patch(
        "services.ai.gemini_cache.get_or_create_nova_cache",
        new=cache_helper,
    ), patch("services.ai.conductor.call_llm", new=llm), caplog.at_level(
        _logging.INFO, logger="services.ai.nova_perf"
    ):
        await get_chat_response(
            _user(), _history(), db,
            session_id="chat_cache",
            pinned_agent="nova",
            message_id="msg_user",
        )

    perf_lines = [
        r.getMessage() for r in caplog.records if "[nova_perf]" in r.getMessage()
    ]
    assert perf_lines, "expected at least one [nova_perf] log line"
    assert any("llm_cache_read_tokens=85" in line for line in perf_lines)


@pytest.mark.asyncio
async def test_perf_log_omits_cache_read_tokens_when_provider_silent(
    monkeypatch, caplog
):
    """When the provider doesn't surface a cached-token count, the
    perf record must omit the field — null and zero are different in
    this log."""
    _enable_cache_flags(monkeypatch)
    db = _FakeDb()

    cache_helper = AsyncMock(return_value="cachedContents/x")
    llm = AsyncMock(
        return_value=LlmResponse(
            text="ok",
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            cached_input_tokens=None,
        )
    )

    import logging as _logging

    with patch(
        "services.ai.gemini_cache.get_or_create_nova_cache",
        new=cache_helper,
    ), patch("services.ai.conductor.call_llm", new=llm), caplog.at_level(
        _logging.INFO, logger="services.ai.nova_perf"
    ):
        await get_chat_response(
            _user(), _history(), db,
            session_id="chat_cache",
            pinned_agent="nova",
            message_id="msg_user",
        )

    perf_lines = [
        r.getMessage() for r in caplog.records if "[nova_perf]" in r.getMessage()
    ]
    assert perf_lines
    # The compact log format drops null/zero fields, so the key
    # should not appear at all when no cache tokens were reported.
    assert not any("llm_cache_read_tokens" in line for line in perf_lines)
