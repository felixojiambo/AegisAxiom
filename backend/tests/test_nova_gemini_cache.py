"""Tests for Nova's Gemini context-cache scaffolding.

The cache helper at ``services/ai/gemini_cache.py`` is opt-in — the
``GEMINI_CONTEXT_CACHE_ENABLED`` env flag gates Gemini-side cache
creation, and the helper itself only acts when the provider is
``"google"``. These tests pin:

  * hashing is stable, sensitive to prompt edits AND tool-schema
    edits, and order-insensitive across tool ordering
  * the helper no-ops cleanly for non-Gemini providers, missing API
    keys, and SDK failures (preview SDKs that lack the caches API)
  * the in-process cache map can be reset for test isolation
  * config helpers parse env values strictly: invalid bool / int /
    thinking-level values fall back without raising
  * ``LlmResponse`` carries cache-token fields and they default to
    ``None`` so the Phase 0 perf log can distinguish absent telemetry
    from a real zero

No real Gemini API is contacted — the SDK import path is exercised
inside ``get_or_create_nova_cache`` and the helper's failure mode
(``ImportError`` / ``AttributeError`` / generic ``Exception``) is the
contract we pin here.
"""
from __future__ import annotations

import asyncio
import os

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

import pytest

from services.ai import config as ai_config
from services.ai.gemini_cache import (
    compute_cache_key,
    get_or_create_nova_cache,
    reset_cache_map_for_tests,
)
from services.ai.llm import LlmResponse


# ---------------------------------------------------------------------------
# Synthetic fixtures — never use the real Nova prompt or schemas.
# ---------------------------------------------------------------------------


_SYNTH_PROMPT_A = "You are a synthetic Nova used only for tests. Follow rule X."
_SYNTH_PROMPT_B = "You are a synthetic Nova used only for tests. Follow rule X. Edited."


def _schemas_a() -> list[dict]:
    return [
        {
            "name": "synth_query_one",
            "description": "test tool one",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "synth_query_two",
            "description": "test tool two",
            "parameters": {"type": "object", "properties": {}},
        },
    ]


def _schemas_b_renamed() -> list[dict]:
    return [
        {
            "name": "synth_query_one",
            "description": "test tool one",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "synth_query_two_renamed",
            "description": "test tool two",
            "parameters": {"type": "object", "properties": {}},
        },
    ]


# ---------------------------------------------------------------------------
# 1. Hashing: stable, prompt-sensitive, tool-sensitive, order-insensitive
# ---------------------------------------------------------------------------


def test_cache_key_stable_for_same_inputs():
    key_one = compute_cache_key(
        provider="google",
        model="gemini-3-flash-preview",
        system_prompt=_SYNTH_PROMPT_A,
        tool_schemas=_schemas_a(),
    )
    key_two = compute_cache_key(
        provider="google",
        model="gemini-3-flash-preview",
        system_prompt=_SYNTH_PROMPT_A,
        tool_schemas=_schemas_a(),
    )
    assert key_one == key_two


def test_cache_key_changes_when_prompt_changes():
    key_a = compute_cache_key(
        provider="google",
        model="gemini-3-flash-preview",
        system_prompt=_SYNTH_PROMPT_A,
        tool_schemas=_schemas_a(),
    )
    key_b = compute_cache_key(
        provider="google",
        model="gemini-3-flash-preview",
        system_prompt=_SYNTH_PROMPT_B,
        tool_schemas=_schemas_a(),
    )
    assert key_a != key_b


def test_cache_key_changes_when_tool_schema_changes():
    key_a = compute_cache_key(
        provider="google",
        model="gemini-3-flash-preview",
        system_prompt=_SYNTH_PROMPT_A,
        tool_schemas=_schemas_a(),
    )
    key_b = compute_cache_key(
        provider="google",
        model="gemini-3-flash-preview",
        system_prompt=_SYNTH_PROMPT_A,
        tool_schemas=_schemas_b_renamed(),
    )
    assert key_a != key_b


def test_cache_key_order_insensitive_across_tool_ordering():
    schemas = _schemas_a()
    reordered = list(reversed(schemas))
    key_in_order = compute_cache_key(
        provider="google",
        model="gemini-3-flash-preview",
        system_prompt=_SYNTH_PROMPT_A,
        tool_schemas=schemas,
    )
    key_reordered = compute_cache_key(
        provider="google",
        model="gemini-3-flash-preview",
        system_prompt=_SYNTH_PROMPT_A,
        tool_schemas=reordered,
    )
    assert key_in_order == key_reordered


# ---------------------------------------------------------------------------
# 2. No-op paths — non-Gemini provider, missing key, SDK absent
# ---------------------------------------------------------------------------


def test_cache_no_op_for_non_google_provider():
    reset_cache_map_for_tests()
    result = asyncio.run(
        get_or_create_nova_cache(
            api_key="not-a-real-key",
            provider="anthropic",
            model="claude-sonnet-4-5-20250929",
            system_prompt=_SYNTH_PROMPT_A,
            tool_schemas=_schemas_a(),
            ttl_seconds=3600,
        )
    )
    assert result is None


def test_cache_no_op_for_missing_api_key():
    reset_cache_map_for_tests()
    result = asyncio.run(
        get_or_create_nova_cache(
            api_key="",
            provider="google",
            model="gemini-3-flash-preview",
            system_prompt=_SYNTH_PROMPT_A,
            tool_schemas=_schemas_a(),
            ttl_seconds=3600,
        )
    )
    assert result is None


def test_cache_swallows_sdk_failures(monkeypatch):
    """If the installed Gemini SDK raises during cache create, the
    helper must return None rather than propagating — chat must keep
    working uncached."""
    reset_cache_map_for_tests()

    import services.ai.gemini_cache as cache_module

    def _boom_build_tools(_schemas):  # noqa: ARG001 — signature match
        raise RuntimeError("synthetic SDK breakage")

    monkeypatch.setattr(cache_module, "_build_genai_tools", _boom_build_tools)

    result = asyncio.run(
        get_or_create_nova_cache(
            api_key="synth-key",
            provider="google",
            model="gemini-3-flash-preview",
            system_prompt=_SYNTH_PROMPT_A,
            tool_schemas=_schemas_a(),
            ttl_seconds=3600,
        )
    )
    assert result is None


# ---------------------------------------------------------------------------
# 3. LlmResponse carries cache-token fields, defaults None
# ---------------------------------------------------------------------------


def test_llm_response_cache_token_defaults_none():
    resp = LlmResponse(text="ok")
    assert resp.cached_input_tokens is None
    assert resp.cache_write_tokens is None


def test_llm_response_can_carry_cache_tokens():
    resp = LlmResponse(
        text="ok",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cached_input_tokens=80,
    )
    # 80 of the 100 prompt tokens came from cache; the perf log should
    # be able to surface that without losing the prompt_tokens total.
    assert resp.cached_input_tokens == 80
    assert resp.prompt_tokens == 100


# ---------------------------------------------------------------------------
# 4. Config helpers — strict parsing with safe fallback
# ---------------------------------------------------------------------------


def test_thinking_level_accepts_valid_values(monkeypatch):
    for value in ("minimal", "low", "medium", "high"):
        monkeypatch.setenv("GEMINI_THINKING_LEVEL", value)
        assert ai_config.gemini_thinking_level() == value


def test_thinking_level_ignored_when_invalid(monkeypatch):
    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "ludicrous")
    assert ai_config.gemini_thinking_level() is None


def test_thinking_level_unset_returns_none(monkeypatch):
    monkeypatch.delenv("GEMINI_THINKING_LEVEL", raising=False)
    assert ai_config.gemini_thinking_level() is None


def test_thinking_level_case_insensitive(monkeypatch):
    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "LOW")
    assert ai_config.gemini_thinking_level() == "low"


def test_prompt_cache_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("NOVA_PROMPT_CACHE_ENABLED", raising=False)
    assert ai_config.nova_prompt_cache_enabled() is False


def test_prompt_cache_flag_truthy_values(monkeypatch):
    for value in ("true", "True", "1", "yes", "on"):
        monkeypatch.setenv("NOVA_PROMPT_CACHE_ENABLED", value)
        assert ai_config.nova_prompt_cache_enabled() is True


def test_prompt_cache_flag_falsey_values(monkeypatch):
    for value in ("false", "0", "no", "off", ""):
        monkeypatch.setenv("NOVA_PROMPT_CACHE_ENABLED", value)
        assert ai_config.nova_prompt_cache_enabled() is False


def test_prompt_cache_flag_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("NOVA_PROMPT_CACHE_ENABLED", "maybe")
    # Default is False, so an invalid value should also be False.
    assert ai_config.nova_prompt_cache_enabled() is False


def test_context_cache_ttl_positive(monkeypatch):
    monkeypatch.setenv("GEMINI_CONTEXT_CACHE_TTL_SECONDS", "1200")
    assert ai_config.gemini_context_cache_ttl_seconds() == 1200


def test_context_cache_ttl_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("GEMINI_CONTEXT_CACHE_TTL_SECONDS", "not-a-number")
    assert ai_config.gemini_context_cache_ttl_seconds() == 3600


def test_context_cache_ttl_non_positive_falls_back(monkeypatch):
    monkeypatch.setenv("GEMINI_CONTEXT_CACHE_TTL_SECONDS", "-5")
    assert ai_config.gemini_context_cache_ttl_seconds() == 3600


# ---------------------------------------------------------------------------
# PR #164 review cleanup — migrated from test_pr164_review_cleanup.py
# ---------------------------------------------------------------------------
# These tests lock the asyncio.Lock + no-lock-during-network-call
# fix flagged in the PR #164 review, plus the follow-up review item
# that asked for the genai.Client to be cached across calls instead
# of constructed inline on every Nova LLM call.


def test_gemini_cache_module_does_not_import_threading_lock():
    """The PR #164 review flagged the threading.Lock + held-across-
    network-call pattern. The fix replaces it with asyncio.Lock.
    Source-grep on the module's runtime imports + lock construction
    guards against a regression. A historical reference in a comment
    is allowed."""
    import inspect

    import services.ai.gemini_cache as gc

    source = inspect.getsource(gc)
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "import threading" not in stripped, (
            f"services/ai/gemini_cache.py still imports threading: {line!r}"
        )
        assert "threading.Lock" not in stripped, (
            f"services/ai/gemini_cache.py still constructs a threading.Lock: {line!r}"
        )
    assert "_cache_lock = asyncio.Lock()" in source


def test_gemini_cache_concurrent_calls_do_not_deadlock():
    """Two coroutines hitting get_or_create_nova_cache at once must
    not deadlock. The SDK is stubbed out so no network call happens;
    the test focuses on lock acquisition/release ordering."""
    from unittest.mock import MagicMock, patch

    from services.ai import gemini_cache as gc_mod

    gc_mod.reset_cache_map_for_tests()

    fake_cache_obj = MagicMock()
    fake_cache_obj.name = "cachedContents/synthetic-abc123"

    class _FakeCachesAPI:
        def __init__(self):
            self.call_count = 0

        def create(self, model, config):
            self.call_count += 1
            return fake_cache_obj

    class _FakeClient:
        def __init__(self, **_):
            self.caches = _FakeCachesAPI()

    with patch("google.genai.Client", new=_FakeClient):
        async def _run():
            return await asyncio.gather(
                gc_mod.get_or_create_nova_cache(
                    api_key="synthetic-key",
                    provider="google",
                    model="synthetic-model",
                    system_prompt="synthetic-prompt",
                    tool_schemas=[{"name": "synth_tool"}],
                    ttl_seconds=60,
                ),
                gc_mod.get_or_create_nova_cache(
                    api_key="synthetic-key",
                    provider="google",
                    model="synthetic-model",
                    system_prompt="synthetic-prompt",
                    tool_schemas=[{"name": "synth_tool"}],
                    ttl_seconds=60,
                ),
            )

        results = asyncio.run(asyncio.wait_for(_run(), timeout=2.0))
        assert all(r == "cachedContents/synthetic-abc123" for r in results)


def test_gemini_cache_create_failure_returns_none_and_does_not_poison_map():
    """When the SDK raises during caches.create, the helper must
    return None and leave the cache map empty so a subsequent call
    can retry."""
    from unittest.mock import patch

    from services.ai import gemini_cache as gc_mod

    gc_mod.reset_cache_map_for_tests()

    class _FailingCaches:
        def create(self, model, config):
            raise RuntimeError("synthetic SDK error")

    class _FakeClient:
        def __init__(self, **_):
            self.caches = _FailingCaches()

    with patch("google.genai.Client", new=_FakeClient):
        result = asyncio.run(gc_mod.get_or_create_nova_cache(
            api_key="synthetic-key",
            provider="google",
            model="synthetic-model",
            system_prompt="synthetic-prompt",
            tool_schemas=[{"name": "synth_tool"}],
            ttl_seconds=60,
        ))
    assert result is None
    assert gc_mod._cache_names == {}


# ---------------------------------------------------------------------------
# PR #164 follow-up — Gemini client cache + history coalescing
# ---------------------------------------------------------------------------


def test_get_gemini_client_caches_one_instance_per_api_key():
    """Two calls with the same api_key must return the SAME client
    instance. Different keys still get separate clients."""
    from unittest.mock import patch

    from services.ai import llm as llm_mod

    llm_mod._reset_gemini_clients_for_tests()
    constructed: list = []

    class _FakeClient:
        def __init__(self, *, api_key):
            constructed.append(api_key)
            self.api_key = api_key

    with patch("google.genai.Client", new=_FakeClient):
        a1 = llm_mod._get_gemini_client("synthetic-key-A")
        a2 = llm_mod._get_gemini_client("synthetic-key-A")
        b1 = llm_mod._get_gemini_client("synthetic-key-B")

    assert a1 is a2
    assert a1 is not b1
    assert constructed == ["synthetic-key-A", "synthetic-key-B"]


def test_reset_gemini_clients_for_tests_clears_cache():
    """The test-only reset helper drops every cached client so a
    subsequent ``_get_gemini_client`` rebuilds. Without this, test
    cases that pin construction-count would leak state."""
    from unittest.mock import patch

    from services.ai import llm as llm_mod

    class _FakeClient:
        def __init__(self, *, api_key):
            self.api_key = api_key

    with patch("google.genai.Client", new=_FakeClient):
        llm_mod._reset_gemini_clients_for_tests()
        first = llm_mod._get_gemini_client("synthetic-key-A")
        llm_mod._reset_gemini_clients_for_tests()
        second = llm_mod._get_gemini_client("synthetic-key-A")
    assert first is not second


# ── Cached-path history coalescing ────────────────────────────────
# Gemini refuses consecutive same-role turns with INVALID_ARGUMENT.
# The follow-up review asked the cached-path turn assembly in
# _call_gemini to coalesce neighbours so the request never
# violates that constraint.


def _materialise_coalesced_turns(history, user_message):
    normalized = []
    for msg in history[-16:]:
        role_raw = (msg.get("role") or "").lower()
        if role_raw not in ("user", "model", "assistant"):
            continue
        text = msg.get("content") or ""
        if not isinstance(text, str) or not text:
            continue
        normalized_role = "model" if role_raw in ("model", "assistant") else "user"
        if normalized and normalized[-1][0] == normalized_role:
            prev_role, prev_text = normalized[-1]
            normalized[-1] = (prev_role, f"{prev_text}\n\n{text}")
        else:
            normalized.append((normalized_role, text))

    if normalized and normalized[-1][0] == "user":
        prev_role, prev_text = normalized[-1]
        normalized[-1] = (prev_role, f"{prev_text}\n\n{user_message}")
    else:
        normalized.append(("user", user_message))
    return normalized


def test_history_coalesces_consecutive_user_messages():
    turns = _materialise_coalesced_turns(
        history=[
            {"role": "user", "content": "synthetic A"},
            {"role": "user", "content": "synthetic B"},
        ],
        user_message="synthetic C",
    )
    assert turns == [
        ("user", "synthetic A\n\nsynthetic B\n\nsynthetic C"),
    ]


def test_history_coalesces_consecutive_assistant_messages():
    turns = _materialise_coalesced_turns(
        history=[
            {"role": "user", "content": "synthetic q"},
            {"role": "assistant", "content": "synthetic r1"},
            {"role": "model", "content": "synthetic r2"},
        ],
        user_message="synthetic q2",
    )
    assert turns == [
        ("user", "synthetic q"),
        ("model", "synthetic r1\n\nsynthetic r2"),
        ("user", "synthetic q2"),
    ]


def test_history_alternating_roles_remain_alternating():
    turns = _materialise_coalesced_turns(
        history=[
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "m1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "m2"},
        ],
        user_message="u3",
    )
    assert turns == [
        ("user", "u1"),
        ("model", "m1"),
        ("user", "u2"),
        ("model", "m2"),
        ("user", "u3"),
    ]


def test_history_unknown_roles_and_empty_content_are_skipped():
    turns = _materialise_coalesced_turns(
        history=[
            {"role": "system", "content": "should drop"},
            {"role": "user", "content": ""},
            {"role": "user", "content": None},
            {"role": "tool", "content": "ignored"},
            {"role": "user", "content": "kept"},
        ],
        user_message="now",
    )
    assert turns == [
        ("user", "kept\n\nnow"),
    ]


def test_llm_call_gemini_uses_cached_client():
    """Source-grep: ``_call_gemini`` must build the client via
    ``_get_gemini_client`` (not inline ``genai.Client(...)``). Pins
    the follow-up fix in place against an accidental revert."""
    import inspect

    from services.ai import llm as llm_mod

    source = inspect.getsource(llm_mod._call_gemini)
    assert "_get_gemini_client(" in source, (
        "_call_gemini must build the client via _get_gemini_client"
    )
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "genai.Client(" not in stripped, (
            f"_call_gemini still constructs genai.Client inline: {line!r}"
        )


def test_llm_call_gemini_coalesces_cached_history():
    """Source-grep: the cached-path block contains the same-role
    coalescing routine."""
    import inspect

    from services.ai import llm as llm_mod

    source = inspect.getsource(llm_mod._call_gemini)
    assert "normalized[-1] = (prev_role" in source
    assert "if normalized and normalized[-1][0] == normalized_role" in source


# Re-import Path for the env-doc test below.
from pathlib import Path  # noqa: E402


def test_env_example_documents_gemini_preview_fallback():
    """The .env.example must document the operator-triggered
    fallback recommendation when the preview model is unavailable."""
    env_example = (
        Path(__file__).parent.parent / ".env.example"
    ).read_text()
    assert "PREVIEW MODEL FALLBACK" in env_example
    assert "gemini-2.5-flash" in env_example
    assert "gemini-3-flash-preview" in env_example
