"""Locks the Gemini cache TTL tracking and the reactive retry path
plus the helper invariants that surround them.

  * TTL tracking. ``_cache_names`` stores each cache name together
    with a unix expiry timestamp. The read path treats an expired
    entry as a miss and removes it before returning. The local map
    must evict the entry ahead of the Gemini side by exactly the
    documented safety window.
  * Reactive retry. ``_call_gemini`` evicts a stale entry and retries
    once with ``cached_content`` cleared when the SDK rejects the
    cache name. An unrelated SDK error is not caught and is not
    retried. A generic INVALID_ARGUMENT response that does not
    mention the cache must propagate untouched so a healthy cache
    is not evicted on a non cache failure.
  * Malformed tool spec logging. ``_build_genai_tools`` logs a
    warning naming the index and the actual type before dropping
    any entry that is not a dict.
  * Cache key scoping. ``compute_cache_key`` depends on all four
    inputs so two callers with the same prompt and schemas but a
    different provider or model see distinct hashes.

Synthetic only. No real Gemini API is contacted. Far future
synthetic dates and fake labels keep the cases stable.
"""
from __future__ import annotations

import logging
import os
import time
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest")

import pytest


_SYNTH_PROMPT = "Synthetic Nova prompt for tests. Follow rule X."


def _synth_schemas() -> list[dict]:
    return [
        {
            "name": "synth_tool_one",
            "description": "synth tool one",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "synth_tool_two",
            "description": "synth tool two",
            "parameters": {"type": "object", "properties": {}},
        },
    ]


# ---------------------------------------------------------------------------
# TTL tracking and eviction helpers
# ---------------------------------------------------------------------------


def test_cache_entry_past_local_expiry_is_treated_as_miss():
    """An entry whose stored expiry is in the past must be removed
    by the read path and the lookup must return None."""
    from services.ai import gemini_cache as gc

    gc.reset_cache_map_for_tests()
    content_hash = gc._hash_content(_SYNTH_PROMPT, _synth_schemas())
    key = gc._CacheKey(
        provider="google",
        model="gemini-3-flash-preview",
        content_hash=content_hash,
    )
    gc._cache_names[key] = ("cachedContents/synth-expired", time.time() - 5)

    assert gc._get_active_name(key) is None
    assert key not in gc._cache_names


def test_cache_entry_inside_local_ttl_is_returned():
    """An entry whose stored expiry is in the future must be
    returned by the read path."""
    from services.ai import gemini_cache as gc

    gc.reset_cache_map_for_tests()
    content_hash = gc._hash_content(_SYNTH_PROMPT, _synth_schemas())
    key = gc._CacheKey(
        provider="google",
        model="gemini-3-flash-preview",
        content_hash=content_hash,
    )
    gc._cache_names[key] = ("cachedContents/synth-fresh", time.time() + 600)

    assert gc._get_active_name(key) == "cachedContents/synth-fresh"


@pytest.mark.asyncio
async def test_evict_by_name_removes_matching_entry():
    """evict_by_name must drop the entry whose stored name matches.
    Returns True when an entry was evicted."""
    from services.ai import gemini_cache as gc

    gc.reset_cache_map_for_tests()
    content_hash = gc._hash_content(_SYNTH_PROMPT, _synth_schemas())
    key = gc._CacheKey(
        provider="google",
        model="gemini-3-flash-preview",
        content_hash=content_hash,
    )
    gc._cache_names[key] = ("cachedContents/synth-evict-me", time.time() + 600)

    assert await gc.evict_by_name("cachedContents/synth-evict-me") is True
    assert key not in gc._cache_names


@pytest.mark.asyncio
async def test_evict_by_name_returns_false_when_no_match():
    """evict_by_name must return False without raising when the
    name does not appear in the map."""
    from services.ai import gemini_cache as gc

    gc.reset_cache_map_for_tests()
    assert await gc.evict_by_name("cachedContents/synth-missing") is False
    assert await gc.evict_by_name("") is False
    assert await gc.evict_by_name(None) is False


def test_safety_margin_is_sixty_seconds():
    """The local map must evict ahead of Gemini by exactly the
    documented safety window."""
    from services.ai import gemini_cache as gc

    assert gc.SAFETY_MARGIN == 60


# ---------------------------------------------------------------------------
# Reactive retry path inside _call_gemini
# ---------------------------------------------------------------------------


class _FakeGenerateContent:
    """Records every call so the test can assert exactly one retry
    happened. The first call raises the supplied exception. The
    second call returns the supplied response."""

    def __init__(self, first_exc, second_response):
        self.first_exc = first_exc
        self.second_response = second_response
        self.calls = []

    async def __call__(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if len(self.calls) == 1:
            raise self.first_exc
        return self.second_response


def _fake_response_with_text(text: str):
    usage = SimpleNamespace(
        prompt_token_count=1,
        candidates_token_count=1,
        cached_content_token_count=None,
    )
    candidate_content = SimpleNamespace(parts=[SimpleNamespace(text=text)])
    candidate = SimpleNamespace(content=candidate_content)
    resp = SimpleNamespace(
        candidates=[candidate],
        function_calls=None,
        text=text,
        usage_metadata=usage,
    )
    return resp


@pytest.mark.asyncio
async def test_call_gemini_retries_once_on_cached_content_not_found():
    """When the SDK raises an error whose message looks like a bad
    cached_content reference the helper must evict the cache name
    and retry exactly once with cached_content cleared."""
    from services.ai import gemini_cache as gc
    from services.ai import llm as llm_mod

    gc.reset_cache_map_for_tests()
    content_hash = gc._hash_content(_SYNTH_PROMPT, _synth_schemas())
    key = gc._CacheKey(
        provider="google",
        model="synth-model",
        content_hash=content_hash,
    )
    gc._cache_names[key] = ("cachedContents/synth-stale", time.time() + 600)

    fake = _FakeGenerateContent(
        first_exc=Exception(
            "NOT_FOUND CachedContent cachedContents/synth-stale was not found"
        ),
        second_response=_fake_response_with_text("ok"),
    )

    fake_client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(
        generate_content=fake
    )))
    with patch.object(llm_mod, "_get_gemini_client", return_value=fake_client):
        result = await llm_mod._call_gemini(
            system_prompt=_SYNTH_PROMPT,
            user_message="synth user message",
            model="synth-model",
            cached_content="cachedContents/synth-stale",
        )

    assert result.text == "ok"
    assert len(fake.calls) == 2
    first_cfg = fake.calls[0]["config"]
    second_cfg = fake.calls[1]["config"]
    assert getattr(first_cfg, "cached_content", None) == "cachedContents/synth-stale"
    assert getattr(second_cfg, "cached_content", None) in (None, "")
    assert key not in gc._cache_names


@pytest.mark.asyncio
async def test_call_gemini_does_not_retry_on_unrelated_error():
    """An unrelated SDK error must not trigger the retry path. The
    original exception must propagate untouched."""
    from services.ai import gemini_cache as gc
    from services.ai import llm as llm_mod

    gc.reset_cache_map_for_tests()
    content_hash = gc._hash_content(_SYNTH_PROMPT, _synth_schemas())
    key = gc._CacheKey(
        provider="google",
        model="synth-model",
        content_hash=content_hash,
    )
    gc._cache_names[key] = ("cachedContents/synth-keep", time.time() + 600)

    unrelated = RuntimeError("some unrelated SDK failure that we do not match")
    fake = _FakeGenerateContent(
        first_exc=unrelated,
        second_response=_fake_response_with_text("unused"),
    )
    fake_client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(
        generate_content=fake
    )))
    with patch.object(llm_mod, "_get_gemini_client", return_value=fake_client):
        with pytest.raises(RuntimeError):
            await llm_mod._call_gemini(
                system_prompt=_SYNTH_PROMPT,
                user_message="synth user message",
                model="synth-model",
                cached_content="cachedContents/synth-keep",
            )
    assert len(fake.calls) == 1
    assert key in gc._cache_names


@pytest.mark.asyncio
async def test_call_gemini_does_not_retry_on_generic_invalid_argument():
    """An INVALID_ARGUMENT response that does not mention the cache
    must NOT trigger the retry path. A healthy cache must remain in
    the map. The original exception type and message must reach the
    caller so the root cause is not masked by a confusing second
    error from the retry path."""
    from services.ai import gemini_cache as gc
    from services.ai import llm as llm_mod

    gc.reset_cache_map_for_tests()
    content_hash = gc._hash_content(_SYNTH_PROMPT, _synth_schemas())
    key = gc._CacheKey(
        provider="google",
        model="synth-model",
        content_hash=content_hash,
    )
    gc._cache_names[key] = ("cachedContents/synth-healthy", time.time() + 600)

    eviction_calls: list[str] = []
    original_evict = gc.evict_by_name

    async def _spy_evict(name):
        eviction_calls.append(name)
        return await original_evict(name)

    class _InvalidArgument(Exception):
        pass

    unrelated = _InvalidArgument(
        "INVALID_ARGUMENT, role sequence is malformed"
    )
    fake = _FakeGenerateContent(
        first_exc=unrelated,
        second_response=_fake_response_with_text("unused"),
    )
    fake_client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(
        generate_content=fake
    )))
    with patch.object(llm_mod, "_get_gemini_client", return_value=fake_client), \
         patch.object(gc, "evict_by_name", _spy_evict):
        with pytest.raises(_InvalidArgument) as exc_info:
            await llm_mod._call_gemini(
                system_prompt=_SYNTH_PROMPT,
                user_message="synth user message",
                model="synth-model",
                cached_content="cachedContents/synth-healthy",
            )
    assert "role sequence is malformed" in str(exc_info.value)
    assert len(fake.calls) == 1
    assert eviction_calls == []
    assert key in gc._cache_names


@pytest.mark.asyncio
async def test_call_gemini_retries_on_cache_specific_message_with_cached_content_set():
    """A cache specific error message with cached_content set must
    still trip the retry path, evict the entry exactly once, and
    succeed on the second generate call."""
    from services.ai import gemini_cache as gc
    from services.ai import llm as llm_mod

    gc.reset_cache_map_for_tests()
    content_hash = gc._hash_content(_SYNTH_PROMPT, _synth_schemas())
    key = gc._CacheKey(
        provider="google",
        model="synth-model",
        content_hash=content_hash,
    )
    gc._cache_names[key] = ("cachedContents/synth123", time.time() + 600)

    eviction_calls: list[str] = []
    original_evict = gc.evict_by_name

    async def _spy_evict(name):
        eviction_calls.append(name)
        return await original_evict(name)

    fake = _FakeGenerateContent(
        first_exc=Exception(
            "cached_content not_found for name cachedContents/synth123"
        ),
        second_response=_fake_response_with_text("retry-ok"),
    )
    fake_client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(
        generate_content=fake
    )))
    with patch.object(llm_mod, "_get_gemini_client", return_value=fake_client), \
         patch.object(gc, "evict_by_name", _spy_evict):
        result = await llm_mod._call_gemini(
            system_prompt=_SYNTH_PROMPT,
            user_message="synth user message",
            model="synth-model",
            cached_content="cachedContents/synth123",
        )
    assert result.text == "retry-ok"
    assert len(fake.calls) == 2
    assert eviction_calls == ["cachedContents/synth123"]
    assert key not in gc._cache_names


# ---------------------------------------------------------------------------
# Malformed tool spec logging in _build_genai_tools
# ---------------------------------------------------------------------------


def test_build_genai_tools_warns_on_malformed_spec(caplog):
    """When a tool spec is not a dict the helper must log a warning
    naming the index and the actual type before continuing."""
    from services.ai import gemini_cache as gc

    bad_specs = [
        {
            "name": "synth_real",
            "description": "synth tool",
            "parameters": {"type": "object", "properties": {}},
        },
        "this_is_not_a_dict",
        42,
    ]
    with caplog.at_level(logging.WARNING, logger=gc.logger.name):
        try:
            gc._build_genai_tools(bad_specs)
        except ImportError:
            pytest.skip("google.genai SDK not available in this environment")

    captured = " ".join(rec.getMessage() for rec in caplog.records)
    assert "dropping malformed tool spec" in captured
    assert "str" in captured or "int" in captured


# ---------------------------------------------------------------------------
# compute_cache_key four input scoping
# ---------------------------------------------------------------------------


def test_compute_cache_key_changes_with_provider():
    from services.ai.gemini_cache import compute_cache_key

    schemas = _synth_schemas()
    a = compute_cache_key("google", "synth-model", _SYNTH_PROMPT, schemas)
    b = compute_cache_key("anthropic", "synth-model", _SYNTH_PROMPT, schemas)
    assert a != b


def test_compute_cache_key_changes_with_model():
    from services.ai.gemini_cache import compute_cache_key

    schemas = _synth_schemas()
    a = compute_cache_key("google", "synth-model-one", _SYNTH_PROMPT, schemas)
    b = compute_cache_key("google", "synth-model-two", _SYNTH_PROMPT, schemas)
    assert a != b


def test_compute_cache_key_stable_for_same_inputs():
    from services.ai.gemini_cache import compute_cache_key

    schemas = _synth_schemas()
    a = compute_cache_key("google", "synth-model", _SYNTH_PROMPT, schemas)
    b = compute_cache_key("google", "synth-model", _SYNTH_PROMPT, schemas)
    assert a == b


def test_compute_cache_key_changes_with_prompt_or_schemas():
    """Sanity check that the other two scoping inputs still
    contribute. Locks the full four argument scoping contract."""
    from services.ai.gemini_cache import compute_cache_key

    base = compute_cache_key(
        "google", "synth-model", _SYNTH_PROMPT, _synth_schemas()
    )
    other_prompt = compute_cache_key(
        "google", "synth-model", _SYNTH_PROMPT + " edited", _synth_schemas()
    )
    other_schemas = compute_cache_key(
        "google",
        "synth-model",
        _SYNTH_PROMPT,
        [{"name": "different", "description": "x", "parameters": {}}],
    )
    assert base != other_prompt
    assert base != other_schemas


# ---------------------------------------------------------------------------
# G4. evict_by_name acquires the cache lock so a concurrent write
# does not raise during iteration.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evict_by_name_concurrent_with_writer_runs_to_completion():
    """Two coroutines run side by side. One repeatedly calls
    evict_by_name. The other mutates _cache_names through the
    write path by inserting fresh entries. Both must run to
    completion without raising RuntimeError on dictionary changed
    size during iteration. The async lock acquisition inside
    evict_by_name is what guarantees the safety property."""
    import asyncio
    from services.ai import gemini_cache as gc

    gc.reset_cache_map_for_tests()

    # Seed enough entries that the iteration inside evict_by_name
    # has a meaningful chance to overlap with concurrent writes.
    for i in range(20):
        content_hash = gc._hash_content(
            _SYNTH_PROMPT + " seed " + str(i), _synth_schemas()
        )
        key = gc._CacheKey(
            provider="google",
            model="synth-model",
            content_hash=content_hash,
        )
        gc._cache_names[key] = (
            "cachedContents/synth-seed-" + str(i),
            time.time() + 600,
        )

    target_name = "cachedContents/synth-seed-7"

    async def _evicter():
        # Multiple eviction attempts so the iteration crosses many
        # async checkpoints under load.
        results = []
        for _ in range(25):
            results.append(await gc.evict_by_name(target_name))
            await asyncio.sleep(0)
        return results

    async def _writer():
        for i in range(25):
            content_hash = gc._hash_content(
                _SYNTH_PROMPT + " late " + str(i), _synth_schemas()
            )
            key = gc._CacheKey(
                provider="google",
                model="synth-model",
                content_hash=content_hash,
            )
            async with gc._cache_lock:
                gc._cache_names[key] = (
                    "cachedContents/synth-late-" + str(i),
                    time.time() + 600,
                )
            await asyncio.sleep(0)

    evict_results, _ = await asyncio.gather(_evicter(), _writer())

    # Exactly one of the eviction attempts succeeded. The remaining
    # attempts saw the entry already removed and returned False.
    assert sum(1 for r in evict_results if r is True) == 1
    assert all(r in (True, False) for r in evict_results)
