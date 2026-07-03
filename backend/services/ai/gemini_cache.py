"""Gemini explicit cache helper for the Nova stable prompt context.

Nova sends the same large system prompt and tool schemas on every
turn. The Gemini explicit cache API lets us upload that static block
once and reference it by name on later calls so the per request input
token charge for the cached prefix is dropped.

The module is intentionally narrow.

  * Nova only. The cache covers the Nova system prompt and tool
    schemas. No other agent uses it.
  * Never caches user data. The cached body is bounded to the
    arguments passed in. Callers must pass only the stable system
    instruction and tool schemas. There is no field for user
    messages, chat history, tool results, or finance rows.
  * Fail safe. Any SDK or network error during cache creation logs
    a warning and returns ``None``. Callers must treat ``None`` as
    no cache available so the request goes out the normal way.
  * In process cache map only. Cache names live in a module level
    dict keyed by a hash of the cached content. In a multi worker
    deployment each worker pays the cache create cost once on first
    use. That is fine because caches are reused for ``TTL_SECONDS``
    and cache create is about one round trip.

The cache create call is the most likely failure mode. Preview SDKs,
minimum token thresholds, and transient API errors all surface here.
The helper always swallows those failures so chat keeps working
without caching.

Date freeness contract. The Nova system prompt fed into this cache
MUST NOT carry the current date or any other wall clock value. The
cache entry is reused for up to TTL_SECONDS so any date baked into
the cached body would freeze for the cache lifetime and the model
would resolve relative phrases such as today and this month against
the stale anchor. Today is injected per turn on the user message
side via services.ai.agents.nova.wrap_user_message_for_nova which
runs outside the cache. Keep build_system_prompt date free or the
cache silently corrupts date arithmetic.

TTL tracking and retry contract. ``_cache_names`` stores each cache
name together with a unix expiry timestamp. The read path treats an
expired entry as a miss and removes it before returning. The expiry
is the cache create wall clock plus the requested TTL minus a
``SAFETY_MARGIN`` of 60 seconds so the local map evicts ahead of the
Gemini side. The reactive layer at the call site can also call
``evict_by_name`` to drop a stale entry when the SDK rejects it.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional


logger = logging.getLogger(__name__)


_CACHE_NAMESPACE = "nova"


# Local map evicts SAFETY_MARGIN seconds before the Gemini side TTL
# expires so a request that crossed the boundary in flight does not
# end up sending a stale cache name.
SAFETY_MARGIN: int = 60


@dataclass(frozen=True)
class _CacheKey:
    provider: str
    model: str
    content_hash: str


# Module level cache map guarded by an asyncio.Lock. The lock is only
# held while reading or writing ``_cache_names``. It is never held
# across the ``client.caches.create`` network call so a slow create
# cannot block other Nova turns on the same worker.
_cache_lock = asyncio.Lock()
_cache_names: dict[_CacheKey, tuple[str, float]] = {}


def _hash_content(system_prompt: str, tool_schemas: list[dict]) -> str:
    """Stable hash of the cacheable payload.

    Hashing both the prompt text and the tool schemas means a prompt
    edit or a tool schema rename produces a new cache so a stale
    cache cannot reintroduce removed guardrails or stale tool args.
    Sorted JSON keys keep the hash stable across dict iteration order.
    """
    h = hashlib.sha256()
    h.update(system_prompt.encode("utf-8"))
    try:
        ordered = sorted(
            tool_schemas,
            key=lambda s: s.get("name", "") if isinstance(s, dict) else "",
        )
        h.update(json.dumps(ordered, sort_keys=True, default=str).encode("utf-8"))
    except Exception:  # noqa: BLE001 — hashing must never raise
        h.update(repr(tool_schemas).encode("utf-8"))
    return h.hexdigest()


def compute_cache_key(
    provider: str, model: str, system_prompt: str, tool_schemas: list[dict]
) -> str:
    """Return a string cache key that incorporates all four scoping
    inputs. The internal ``_CacheKey`` dataclass already scopes the
    runtime map by provider, model, and content hash so the public
    function must produce a hash with the same dependency set. A
    caller using the public function for deduplication will then see
    distinct hashes for distinct providers or models even when the
    system prompt and tool schemas are identical.
    """
    payload = {
        "provider": provider,
        "model": model,
        "content_hash": _hash_content(system_prompt, tool_schemas),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _build_genai_tools(tool_schemas: list[dict]) -> list[Any]:
    """Wrap raw tool schema dicts as ``genai`` ``Tool`` objects.

    Mirrors the construction the conductor does today when handing
    tools to ``call_llm``. The SDK refuses a plain list of dicts for
    cache create so we convert here. Wrapped in try except so an SDK
    shape we do not recognize falls back to no cache instead of an
    import error at module load time.
    """
    from google.genai import types as genai_types

    fns = []
    for index, spec in enumerate(tool_schemas):
        if not isinstance(spec, dict):
            logger.warning(
                "dropping malformed tool spec at index %s, expected dict got %s",
                index,
                type(spec).__name__,
            )
            continue
        fns.append(
            genai_types.FunctionDeclaration(
                name=spec.get("name"),
                description=spec.get("description") or "",
                parameters=spec.get("parameters") or {},
            )
        )
    return [genai_types.Tool(function_declarations=fns)] if fns else []


def _get_active_name(key: _CacheKey) -> Optional[str]:
    """Return the cache name for ``key`` when the entry is still
    inside its local TTL window. An expired entry is removed and the
    function returns ``None`` so the caller treats the lookup as a
    miss and goes through the create path.
    """
    entry = _cache_names.get(key)
    if entry is None:
        return None
    name, expires_at = entry
    if time.time() >= expires_at:
        _cache_names.pop(key, None)
        logger.info(
            "Gemini cache entry expired locally and was evicted hash=%s model=%s",
            key.content_hash[:12],
            key.model,
        )
        return None
    return name


async def evict_by_name(cache_name: str) -> bool:
    """Remove a cache map entry by its returned cache name. Holds
    ``_cache_lock`` for the iteration plus the pop so a concurrent
    write cannot raise dictionary changed size during iteration when
    the module is ever called from a worker thread. Used by the call
    site when the SDK reports the cache name is unknown or invalid
    so a stale local entry cannot be reused on the next call. Never
    raises so the reactive eviction layer can call it without an
    extra try except.
    """
    if not isinstance(cache_name, str) or not cache_name:
        return False
    async with _cache_lock:
        found_key = None
        for key, entry in _cache_names.items():
            name, _expires = entry
            if name == cache_name:
                found_key = key
                break
        if found_key is None:
            return False
        _cache_names.pop(found_key, None)
    logger.info(
        "Gemini cache entry evicted by name hash=%s model=%s",
        found_key.content_hash[:12],
        found_key.model,
    )
    return True


async def get_or_create_nova_cache(
    *,
    api_key: str,
    provider: str,
    model: str,
    system_prompt: str,
    tool_schemas: list[dict],
    ttl_seconds: int,
) -> Optional[str]:
    """Return a Gemini ``cachedContents`` name for the Nova stable
    context.

    Args.
      api_key. Gemini API key already resolved by the caller.
      provider. ``"google"`` or similar. Non google providers no op.
      model. Model id. Part of the cache key so swapping models does
        not pick up the wrong cache.
      system_prompt. The Nova stable system instruction. Must NOT
        include user messages, chat history, tool results, or finance
        data.
      tool_schemas. The Nova stable tool schemas. Must NOT include
        per turn tool args.
      ttl_seconds. Cache lifetime in seconds.

    Returns.
      The cached content name on success, or ``None`` if caching is
      unavailable for any reason such as wrong provider, missing API
      key, SDK lacking the caches API, the SDK rejecting the create
      call, or a network or auth failure. Callers must treat ``None``
      as send the request without cached_content.
    """
    if not api_key:
        return None
    if provider != "google":
        logger.debug(
            "Gemini explicit cache requested but provider=%s is not google, "
            "falling back to uncached request.",
            provider,
        )
        return None

    content_hash = _hash_content(system_prompt, tool_schemas)
    key = _CacheKey(provider=provider, model=model, content_hash=content_hash)

    # Fast path lock free dict read. ``_get_active_name`` returns None
    # when the entry has crossed its local expiry so a stale name does
    # not get returned even when the entry is still physically present.
    existing = _get_active_name(key)
    if existing:
        return existing

    # Slow path cache create. We do NOT hold the lock across the
    # network call. Two concurrent cold start Nova turns may both
    # create a cache and the second write wins. The orphaned cache
    # expires on its own TTL. Spending one extra create call per
    # cold start is cheaper than serialising all Nova turns behind
    # a network round trip.
    try:
        from google import genai
        from google.genai import types as genai_types

        client = genai.Client(api_key=api_key)
        tools = _build_genai_tools(tool_schemas)

        create_config = genai_types.CreateCachedContentConfig(
            system_instruction=system_prompt,
            tools=tools or None,
            ttl=f"{int(ttl_seconds)}s",
            display_name=f"{_CACHE_NAMESPACE}:{content_hash[:12]}",
        )
        cache_obj = await asyncio.to_thread(
            client.caches.create, model=model, config=create_config
        )
        cache_name = getattr(cache_obj, "name", None)
        if not isinstance(cache_name, str) or not cache_name:
            logger.warning(
                "Gemini cache create returned no name. Falling back to "
                "uncached request model=%s.",
                model,
            )
            return None
    except Exception as exc:  # noqa: BLE001 — caching is best effort
        logger.warning(
            "Gemini cache create failed model=%s err=%s. Falling back "
            "to uncached request.",
            model,
            exc,
        )
        return None

    expires_at = time.time() + int(ttl_seconds) - SAFETY_MARGIN

    # Lock only for the dict write. A second coroutine that raced us
    # may have populated the map already so prefer its value when it
    # is still inside its local TTL window. All callers converge on a
    # single cache name within the worker.
    async with _cache_lock:
        existing = _get_active_name(key)
        if existing:
            return existing
        _cache_names[key] = (cache_name, expires_at)
    logger.info(
        "Created Gemini Nova cache name=%s hash=%s model=%s ttl=%ss",
        cache_name,
        content_hash[:12],
        model,
        ttl_seconds,
    )
    return cache_name


def reset_cache_map_for_tests() -> None:
    """Clear the in process cache map. Tests use this to keep cases
    isolated. Synchronous because tests run between events so the
    asyncio.Lock is intentionally bypassed and cleanup never has to
    enter an event loop just to clear a dict.
    """
    _cache_names.clear()


__all__ = [
    "SAFETY_MARGIN",
    "compute_cache_key",
    "evict_by_name",
    "get_or_create_nova_cache",
    "reset_cache_map_for_tests",
]
