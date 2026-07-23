"""LLM abstraction layer — dual-backend support with usage tracking and safeguards.

Selects the LLM backend based on available API keys:
  - EMERGENT_LLM_KEY set  → use emergentintegrations (Emergent preview environments)
  - GEMINI_API_KEY set    → use google-genai SDK directly (local dev + production)

Emergent takes precedence if both keys are present.
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from services.ai.config import (
    EMERGENT_LLM_KEY,
    GEMINI_API_KEY,
    gemini_thinking_level,
    nova_daily_token_budget,
    nova_max_output_tokens,
    nova_session_token_budget,
)

logger = logging.getLogger(__name__)

# Default model for agents that don't specify one
DEFAULT_PROVIDER = "google"
DEFAULT_MODEL = "gemini-3-flash-preview"
DEFAULT_TIMEOUT = 60  # seconds — transcript analysis needs more time

# Budget defaults (overridden by env vars)
SESSION_TOKEN_BUDGET = int(os.environ.get("AI_SESSION_TOKEN_BUDGET", "1000000"))
DAILY_USER_TOKEN_BUDGET = int(os.environ.get("AI_DAILY_TOKEN_BUDGET", "5000000"))
GLOBAL_DAILY_TOKEN_BUDGET = int(os.environ.get("AI_GLOBAL_DAILY_BUDGET", "50000000"))


# Route name used for Nova-specific budget overrides. Kept as a
# constant so the override mechanism is grep-friendly and a future
# rename of the route is a single-line change.
_NOVA_ROUTE: str = "nova"


def _resolve_session_budget(route: Optional[str]) -> int:
    """Return the session budget for the given route.

    Nova gets ``NOVA_SESSION_TOKEN_BUDGET`` when set; every other
    route keeps the module-level default. Returning the int here
    rather than swapping module-level constants per-call keeps the
    function thread-safe and avoids surprising other agents.
    """
    if route == _NOVA_ROUTE:
        override = nova_session_token_budget()
        if isinstance(override, int) and override > 0:
            return override
    return SESSION_TOKEN_BUDGET


def _resolve_daily_user_budget(route: Optional[str]) -> int:
    if route == _NOVA_ROUTE:
        override = nova_daily_token_budget()
        if isinstance(override, int) and override > 0:
            return override
    return DAILY_USER_TOKEN_BUDGET


# ──────────────────────────────────────────────
#  Custom Errors
# ──────────────────────────────────────────────

class BudgetExceededError(Exception):
    """Raised when a token budget limit is exceeded."""
    pass


class LlmTimeoutError(Exception):
    """Raised when an LLM call exceeds the timeout."""
    pass


@dataclass(eq=False)
class LLMProviderError(Exception):
    """Normalized provider-side failure (rate-limit, quota, auth, 5xx).

    eq=False — exception types are compared by identity, not by
    field equality. The default dataclass ``__eq__`` would treat two
    distinct error instances with the same provider/status/code as
    equal, which breaks ``except ... as exc`` chains and ``assert
    exc1 is not exc2`` patterns in tests.

    Raised by :func:`call_llm` when the underlying SDK reports an error
    that the conductor should translate into a friendly chat reply
    rather than a 500. Carries enough structured information for the
    conductor / chat route to decide what to surface to the user
    without leaking provider internals (API keys, raw JSON, stack
    traces, internal file paths).

    Attributes:
        provider: ``"google"`` / ``"emergent"`` etc.
        status_code: HTTP status from the provider, when known
            (``429`` for quota, ``401``/``403`` for auth).
        code: Provider-specific code string (e.g.
            ``"RESOURCE_EXHAUSTED"``).
        retry_after_seconds: Hint from the provider when a retry
            window is signalled. ``None`` when not provided.
        user_message: Pre-formatted user-facing text. Conductor
            quotes this verbatim — it deliberately omits any
            provider-specific identifiers.
    """

    provider: str = "unknown"
    status_code: Optional[int] = None
    code: Optional[str] = None
    retry_after_seconds: Optional[int] = None
    user_message: str = (
        "The AI provider is currently unavailable. Please retry shortly."
    )

    def __str__(self) -> str:  # noqa: D401 — short, intentional
        return (
            f"LLMProviderError(provider={self.provider!r}, "
            f"status_code={self.status_code!r}, code={self.code!r})"
        )


# Patterns that mark an exception as a quota / rate-limit error
# regardless of the provider SDK's class hierarchy. Matched against
# the stringified exception so we don't need a hard import on
# ``google.genai.errors`` — the SDK is optional in test environments.
_QUOTA_STATUS_CODES = (429,)
_QUOTA_CODE_TOKENS = ("RESOURCE_EXHAUSTED",)
_QUOTA_MESSAGE_TOKENS = (
    "quota exceeded",
    "rate limit",
    "rate-limit",
    "free_tier_requests",
    "free_tier_input_token_count",
)
_RETRY_AFTER_RE = re.compile(
    r"retry[\s_-]*(?:after|in)?\s*[:=]?\s*(\d+)",
    re.IGNORECASE,
)


def _extract_status_code(exc: BaseException) -> Optional[int]:
    """Best-effort status-code probe across SDK shapes.

    Tries ``code`` / ``status_code`` / ``response.status_code`` /
    ``args[0]`` so we cover the google-genai ``ClientError`` shape
    (``code=429``) plus generic httpx-style errors. Never raises.
    """
    for attr in ("code", "status_code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    response = getattr(exc, "response", None)
    if response is not None:
        v = getattr(response, "status_code", None)
        if isinstance(v, int):
            return v
    args = getattr(exc, "args", None) or ()
    if args and isinstance(args[0], int):
        return args[0]
    return None


def _extract_retry_after_seconds(exc: BaseException) -> Optional[int]:
    """Pull a retry hint from the exception or its response, if any."""
    # Preferred: structured retry_after on the exception.
    for attr in ("retry_after", "retry_after_seconds"):
        v = getattr(exc, attr, None)
        if isinstance(v, (int, float)) and v >= 0:
            return int(v)
    # Header on the underlying response.
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None) or {}
        try:
            ra = headers.get("Retry-After") or headers.get("retry-after")
        except Exception:  # noqa: BLE001 — defensive
            ra = None
        if ra:
            try:
                return int(ra)
            except (TypeError, ValueError):
                pass
    # Fall back to scraping the message text.
    text = str(exc) or ""
    m = _RETRY_AFTER_RE.search(text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _is_quota_error(exc: BaseException) -> bool:
    """Duck-typed test for "this is a provider quota/rate-limit error."

    Avoids a hard dependency on ``google.genai.errors`` so tests can
    pass synthetic exception objects without importing the SDK.
    """
    status = _extract_status_code(exc)
    if status in _QUOTA_STATUS_CODES:
        return True
    text = str(exc) or ""
    upper = text.upper()
    if any(token in upper for token in _QUOTA_CODE_TOKENS):
        return True
    lower = text.lower()
    if any(token in lower for token in _QUOTA_MESSAGE_TOKENS):
        return True
    # Class-name fallback for SDKs that subclass without exposing a
    # status code on the instance (e.g. older google-genai builds
    # raised plain ``ResourceExhausted``).
    cls_name = exc.__class__.__name__
    if cls_name.lower() in {"resourceexhausted", "ratelimiterror"}:
        return True
    return False


def _build_quota_user_message(retry_after_seconds: Optional[int]) -> str:
    msg = (
        "The AI provider is currently rate-limited, so Nova could "
        "not complete the answer. Please retry shortly or switch to "
        "an AI provider/key with available quota."
    )
    if retry_after_seconds and retry_after_seconds > 0:
        msg += f" Suggested retry: about {retry_after_seconds} seconds."
    return msg


def _classify_provider_exception(
    provider: str, exc: BaseException
) -> Optional[LLMProviderError]:
    """Translate a raw SDK exception into an :class:`LLMProviderError`.

    Returns ``None`` when the exception does not look like a
    structured provider failure — caller should let it propagate to
    the existing generic-exception handlers so unrelated bugs surface
    in logs at full fidelity.
    """
    if _is_quota_error(exc):
        retry_after = _extract_retry_after_seconds(exc)
        return LLMProviderError(
            provider=provider,
            status_code=_extract_status_code(exc) or 429,
            code="RESOURCE_EXHAUSTED",
            retry_after_seconds=retry_after,
            user_message=_build_quota_user_message(retry_after),
        )
    return None


# ──────────────────────────────────────────────
#  Response Dataclass
# ──────────────────────────────────────────────

@dataclass
class LlmResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    function_calls: list = None     # [{name, args, id}] when model wants to call tools
    raw_parts: list = None          # Preserved for round-trip tool conversations
    # Cache usage from the provider response when explicit context
    # caching is in play. ``None`` (not ``0``) when the provider did
    # not surface a field — Phase 0 perf telemetry omits null values
    # so the absence of cache usage is distinguishable from "0 cached
    # tokens were used" without inventing data.
    cached_input_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None


# ──────────────────────────────────────────────
#  Usage Tracking
# ──────────────────────────────────────────────

async def log_usage(
    db,
    user_id: str,
    session_id: str,
    agent: str,
    resp: LlmResponse,
    model: str,
    parent_agent: str = None,
    user_email: str = None,
    user_name: str = None,
):
    """Log an LLM call to the ai_usage collection.

    user_email and user_name are snapshotted at log time so /by-user can
    render the actor even after the user record is later deleted. Older
    rows lacking these fields fall back to a live users-collection lookup
    in the route handler.
    """
    try:
        doc = {
            "user_id": user_id,
            "session_id": session_id,
            "agent": agent,
            "prompt_tokens": resp.prompt_tokens,
            "completion_tokens": resp.completion_tokens,
            "total_tokens": resp.total_tokens,
            "model": model,
            "created_at": datetime.now(timezone.utc),
        }
        if parent_agent:
            doc["parent_agent"] = parent_agent
        if user_email:
            doc["user_email"] = user_email
        if user_name:
            doc["user_name"] = user_name
        await db.ai_usage.insert_one(doc)
    except Exception as e:
        logger.warning(f"Failed to log AI usage: {e}")


ADMIN_DAILY_TOKEN_BUDGET = int(os.environ.get("AI_ADMIN_DAILY_TOKEN_BUDGET", "5000000"))


async def check_budget(
    db,
    user_id: str,
    session_id: str = None,
    route: Optional[str] = None,
):
    """Check all budget limits. Raises BudgetExceededError if any are exceeded.

    ``route`` is the agent route name (``"nova"``, ``"deals"``, etc.).
    Nova gets per-route session / daily overrides via
    ``NOVA_SESSION_TOKEN_BUDGET`` / ``NOVA_DAILY_TOKEN_BUDGET`` when
    set; every other route uses the global ``AI_*`` budgets. Passing
    ``None`` keeps the legacy global behavior, so existing call sites
    that haven't been updated yet remain unaffected.
    """
    # Intentional TOCTOU. Three sequential reads against the session,
    # the user, and the usage aggregate are not atomic. Concurrent
    # requests from the same user can all pass the check before any
    # one of them increments the counter. The trade off is deliberate.
    # Wrapping the check in a lock would serialise every Nova request
    # from a single user and add latency that is worse than the
    # occasional one request overshoot. Do not add a lock here without
    # measuring the contention cost against a realistic Nova workload.
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)

    session_budget = _resolve_session_budget(route)
    daily_budget = _resolve_daily_user_budget(route)

    # Per-session budget
    if session_id and session_budget > 0:
        session_doc = await db.chat_sessions.find_one(
            {"session_id": session_id},
            {"_id": 0, "tokens_used": 1},
        )
        tokens_used = (session_doc or {}).get("tokens_used", 0)
        if tokens_used >= session_budget:
            raise BudgetExceededError(
                "You've reached the token limit for this chat session. Please start a new chat."
            )

    # Per-user daily budget (admins get a higher limit). The admin
    # boost is intentionally a single global value — admins exercising
    # Nova are still capped by ``ADMIN_DAILY_TOKEN_BUDGET``, not by
    # the Nova override, so an admin can't accidentally burn the org
    # quota by setting a very high Nova daily budget.
    if daily_budget > 0:
        from db.session import get_sessionmaker
        from repositories.users import UserRepository

        sm = get_sessionmaker()
        async with sm() as pg:
            budget_fields = await UserRepository(pg).get_user_budget_fields(user_id)
        user_role = budget_fields[0] if budget_fields else ""
        budget = ADMIN_DAILY_TOKEN_BUDGET if user_role == "admin" else daily_budget

        # If an admin reset this user's budget, only count usage after the reset
        budget_reset_at = budget_fields[1] if budget_fields else None
        if budget_reset_at and budget_reset_at.tzinfo is None:
            budget_reset_at = budget_reset_at.replace(tzinfo=timezone.utc)
        usage_since = max(day_ago, budget_reset_at) if budget_reset_at else day_ago

        pipeline = [
            {"$match": {"user_id": user_id, "created_at": {"$gte": usage_since}}},
            {"$group": {"_id": None, "total": {"$sum": "$total_tokens"}}},
        ]
        result = await db.ai_usage.aggregate(pipeline).to_list(1)
        user_daily = result[0]["total"] if result else 0
        if user_daily >= budget:
            raise BudgetExceededError(
                "You've reached your daily AI usage limit. Please contact an admin to reset your budget or try again later."
            )

    # Global daily budget
    if GLOBAL_DAILY_TOKEN_BUDGET > 0:
        pipeline = [
            {"$match": {"created_at": {"$gte": day_ago}}},
            {"$group": {"_id": None, "total": {"$sum": "$total_tokens"}}},
        ]
        result = await db.ai_usage.aggregate(pipeline).to_list(1)
        global_daily = result[0]["total"] if result else 0
        if global_daily >= GLOBAL_DAILY_TOKEN_BUDGET:
            raise BudgetExceededError(
                "The AI system has reached its daily usage limit. Please try again tomorrow."
            )


async def update_session_tokens(db, session_id: str, tokens: int):
    """Increment the token counter on a chat session."""
    if session_id and tokens > 0:
        await db.chat_sessions.update_one(
            {"session_id": session_id},
            {"$inc": {"tokens_used": tokens}},
        )


# ──────────────────────────────────────────────
#  Backend Selection
# ──────────────────────────────────────────────


# Module-level cache of ``genai.Client`` instances keyed by API key.
# Building the client constructs an httpx connection pool and resolves
# auth metadata — paying that cost on every Nova turn is wasteful.
# Keyed by api_key so a future multi-tenant scenario (or a test that
# rewires the key) does not pick up a stale auth context. The import
# of ``google.genai`` lives inside the helper so the cache map exists
# even in test environments where the SDK is patched out.
_GEMINI_CLIENTS: dict[str, Any] = {}


def _get_gemini_client(api_key: str) -> Any:
    """Return a cached ``genai.Client`` for the supplied API key.

    The first call per key constructs and caches the client; every
    subsequent call returns the same instance. Tests that need to
    pin call counts can clear the cache via
    ``_reset_gemini_clients_for_tests`` before each case.
    """
    client = _GEMINI_CLIENTS.get(api_key)
    if client is None:
        from google import genai

        client = genai.Client(api_key=api_key)
        _GEMINI_CLIENTS[api_key] = client
    return client


def _reset_gemini_clients_for_tests() -> None:
    """Clear the cached client map. Test helper only — production
    code paths should NEVER call this; it forces the next request
    to rebuild a fresh connection pool."""
    _GEMINI_CLIENTS.clear()


def _get_backend() -> str:
    if EMERGENT_LLM_KEY:
        return "emergent"
    if GEMINI_API_KEY:
        return "gemini"
    raise RuntimeError("No LLM API key configured. Set EMERGENT_LLM_KEY or GEMINI_API_KEY.")


# ──────────────────────────────────────────────
#  Backend Implementations
# ──────────────────────────────────────────────

async def _call_emergent(
    system_prompt: str,
    user_message: str,
    session_key: str,
    provider: str,
    model: str,
) -> LlmResponse:
    from emergentintegrations.llm.chat import LlmChat, UserMessage

    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=session_key,
        system_message=system_prompt,
    ).with_model(provider, model)

    text = await chat.send_message(UserMessage(text=user_message))
    return LlmResponse(text=text)


async def _call_gemini(
    system_prompt: str,
    user_message: str,
    model: str,
    tools: list = None,
    tool_responses: list = None,
    prior_parts: list = None,
    response_schema: dict = None,
    response_mime_type: str = None,
    temperature: float = None,
    thinking_level: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    cached_content: Optional[str] = None,
    history_for_contents: Optional[list] = None,
) -> LlmResponse:
    from google.genai import types as genai_types

    # Reuse the per-API-key genai.Client instance instead of building
    # a fresh one (and its connection pool) on every call. Keyed by
    # api_key so a future multi-tenant or per-test override does not
    # cross-contaminate. See ``_get_gemini_client`` for the cache
    # rationale.
    client = _get_gemini_client(GEMINI_API_KEY)

    # ── Contents assembly ──
    # Three shapes, in priority order:
    #
    #   1. Tool round-trip (``tool_responses`` + ``prior_parts``): the
    #      model already proposed function calls last turn; we're now
    #      handing back the tool responses. This shape predates
    #      caching and must keep its exact three-turn structure so
    #      Gemini's function-calling state machine stays valid.
    #
    #   2. Cached path WITH history (``cached_content`` +
    #      ``history_for_contents``): the cache holds the system
    #      prompt + tools, so the conversation history that would
    #      normally live inside ``system_instruction`` must be moved
    #      into ``contents`` as proper user/model turns instead. Only
    #      Nova's initial tool-selection call uses this shape today.
    #
    #   3. Default (single string): the request is a one-shot user
    #      turn — either the very first chat message, or a non-cached
    #      Nova call where history was already merged into the
    #      system prompt by ``call_llm``.
    contents: Any
    if tool_responses and prior_parts:
        contents = [
            genai_types.Content(role="user", parts=[genai_types.Part(text=user_message)]),
            genai_types.Content(role="model", parts=prior_parts),
            genai_types.Content(role="user", parts=[
                genai_types.Part(function_response=genai_types.FunctionResponse(
                    name=tr["name"], response=tr["response"]
                )) for tr in tool_responses
            ]),
        ]
    elif cached_content and history_for_contents:
        # Cap to the most recent 16 turns to match the legacy
        # system-prompt-history truncation. The cache pays for the
        # stable prefix; long histories should not undo that win by
        # ballooning the per-request payload.
        #
        # Gemini's content-validation refuses two consecutive turns
        # that share a role ("INVALID_ARGUMENT: Please ensure that
        # multiturn requests alternate between user and model"). When
        # a chat session captures two consecutive assistant messages
        # (a retried call, a streamed completion replayed twice, a
        # tool-response interleave), forwarding them verbatim breaks
        # the request. Coalesce same-role neighbours into one Content
        # whose ``parts`` are the text segments joined by a blank
        # line — same final tokens, but a single role boundary.
        normalized: list[tuple[str, str]] = []  # (role, text)
        for msg in history_for_contents[-16:]:
            role_raw = (msg.get("role") or "").lower()
            if role_raw not in ("user", "model", "assistant"):
                continue
            text = msg.get("content") or ""
            if not isinstance(text, str) or not text:
                continue
            normalized_role = "model" if role_raw in ("model", "assistant") else "user"
            if normalized and normalized[-1][0] == normalized_role:
                # Coalesce into the previous same-role entry.
                prev_role, prev_text = normalized[-1]
                normalized[-1] = (prev_role, f"{prev_text}\n\n{text}")
            else:
                normalized.append((normalized_role, text))

        turns: list = [
            genai_types.Content(
                role=role,
                parts=[genai_types.Part(text=text)],
            )
            for role, text in normalized
        ]

        # Append the current user turn. If the last coalesced entry
        # was already ``user`` (e.g. the only history was a user
        # message), merge into it so we don't emit two consecutive
        # user turns either.
        if turns and turns[-1].role == "user":
            existing_text = turns[-1].parts[0].text
            turns[-1] = genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=f"{existing_text}\n\n{user_message}")],
            )
        else:
            turns.append(
                genai_types.Content(
                    role="user", parts=[genai_types.Part(text=user_message)]
                )
            )
        contents = turns
    else:
        contents = user_message

    # Build config with optional tools / structured output.
    #
    # When ``cached_content`` is supplied, the cache already carries
    # system_instruction + tools, so we MUST omit them from the request
    # config — the SDK rejects sending both. The caller is responsible
    # for ensuring the cache was built from the same (or compatible)
    # system prompt and tool schemas; ``gemini_cache.compute_cache_key``
    # hashes both so any drift produces a different cache name.
    config_kwargs: dict = {}
    if cached_content:
        config_kwargs["cached_content"] = cached_content
    else:
        config_kwargs["system_instruction"] = system_prompt
        if tools:
            config_kwargs["tools"] = tools
    if response_schema:
        config_kwargs["response_mime_type"] = response_mime_type or "application/json"
        config_kwargs["response_schema"] = response_schema
    if temperature is not None:
        config_kwargs["temperature"] = temperature
    if isinstance(max_output_tokens, int) and max_output_tokens > 0:
        config_kwargs["max_output_tokens"] = max_output_tokens

    # ``thinking_level`` is a Gemini 3 family field. Older SDK builds
    # surface it as ``ThinkingConfig(thinking_level=...)``; older
    # models used the legacy ``thinking_budget`` integer. We never
    # send both. If the installed SDK does not expose ThinkingConfig
    # or the level enum, log a warning and omit the field — chat
    # continues without thinking.
    if thinking_level:
        try:
            thinking_cfg_cls = getattr(genai_types, "ThinkingConfig", None)
            if thinking_cfg_cls is not None:
                config_kwargs["thinking_config"] = thinking_cfg_cls(
                    thinking_level=thinking_level
                )
            else:
                logger.warning(
                    "Installed google-genai SDK does not expose "
                    "ThinkingConfig; omitting thinking_level=%s.",
                    thinking_level,
                )
        except Exception as exc:  # noqa: BLE001 — knob is optional
            logger.warning(
                "Failed to attach thinking_level=%s to Gemini config: %s. "
                "Continuing without thinking_config.",
                thinking_level, exc,
            )

    config = genai_types.GenerateContentConfig(**config_kwargs)

    # Reactive retry on a bad cached_content reference. When the SDK
    # rejects the cache name because it expired on the Gemini side or
    # was never visible to this worker the local map entry is stale.
    # Evict the entry and retry the call once with cached_content
    # cleared so the very next request rebuilds the cache through
    # ``get_or_create_nova_cache``. Match only on cache specific
    # error signatures so a generic INVALID_ARGUMENT response such as
    # a malformed role sequence or a schema validation failure does
    # not evict a healthy cache. Unrelated SDK failures keep their
    # full original signal and propagate to the caller untouched.
    _CACHED_CONTENT_ERROR_TOKENS = (
        "cached_content",
        "cachedcontent",
        "not_found",
    )
    try:
        response = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
    except Exception as exc:  # noqa: BLE001 — retry path is narrow
        if not cached_content:
            raise
        msg = str(exc).lower()
        if not any(token in msg for token in _CACHED_CONTENT_ERROR_TOKENS):
            raise
        try:
            from services.ai import gemini_cache as _gemini_cache
            await _gemini_cache.evict_by_name(cached_content)
        except Exception as evict_exc:  # noqa: BLE001
            logger.warning(
                "Gemini cache reactive eviction failed name=%s err=%s",
                cached_content,
                evict_exc,
            )
        logger.warning(
            "Gemini call rejected cached_content reference, retrying once "
            "without the cache name=%s err=%s",
            cached_content,
            exc,
        )

        # Rebuild config and contents as if cached_content had never
        # been supplied. system_instruction and tools move back into
        # the per request config because the cache no longer carries
        # them. When the original call shipped a multi turn history
        # for the cached path that history is folded into the system
        # prompt as a conversation_history block so the retry matches
        # the legacy uncached shape exactly.
        retry_system_prompt = system_prompt
        if history_for_contents:
            ctx = "\n".join(
                f"{(m.get('role') or '').upper()}: {m.get('content') or ''}"
                for m in history_for_contents[-16:]
                if isinstance(m, dict) and isinstance(m.get("content"), str)
            )
            if ctx:
                retry_system_prompt = (
                    retry_system_prompt
                    + "\n\n<conversation_history>\n"
                    + ctx
                    + "\n</conversation_history>"
                )
        retry_kwargs: dict = {"system_instruction": retry_system_prompt}
        if tools:
            retry_kwargs["tools"] = tools
        if response_schema:
            retry_kwargs["response_mime_type"] = (
                response_mime_type or "application/json"
            )
            retry_kwargs["response_schema"] = response_schema
        if temperature is not None:
            retry_kwargs["temperature"] = temperature
        if isinstance(max_output_tokens, int) and max_output_tokens > 0:
            retry_kwargs["max_output_tokens"] = max_output_tokens
        if thinking_level:
            try:
                thinking_cfg_cls = getattr(genai_types, "ThinkingConfig", None)
                if thinking_cfg_cls is not None:
                    retry_kwargs["thinking_config"] = thinking_cfg_cls(
                        thinking_level=thinking_level
                    )
            except Exception as cfg_exc:  # noqa: BLE001
                logger.warning(
                    "Failed to attach thinking_level=%s on retry err=%s. "
                    "Continuing without thinking_config.",
                    thinking_level,
                    cfg_exc,
                )
        retry_contents: Any
        if tool_responses and prior_parts:
            retry_contents = [
                genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=user_message)],
                ),
                genai_types.Content(role="model", parts=prior_parts),
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part(
                            function_response=genai_types.FunctionResponse(
                                name=tr["name"], response=tr["response"]
                            )
                        )
                        for tr in tool_responses
                    ],
                ),
            ]
        else:
            retry_contents = user_message
        retry_config = genai_types.GenerateContentConfig(**retry_kwargs)
        response = await client.aio.models.generate_content(
            model=model,
            contents=retry_contents,
            config=retry_config,
        )

    # Extract function calls and raw parts before accessing text
    fc_list = None
    raw = None
    text = ""

    if hasattr(response, "candidates") and response.candidates:
        candidate_content = response.candidates[0].content
        raw = candidate_content.parts if candidate_content else None

    try:
        fn_calls = response.function_calls
    except (ValueError, AttributeError):
        fn_calls = None

    if fn_calls:
        fc_list = [
            {"name": fc.name, "args": dict(fc.args) if fc.args else {}, "id": getattr(fc, "id", None)}
            for fc in fn_calls
        ]
        try:
            text = response.text or ""
        except (ValueError, AttributeError):
            text = ""
    else:
        try:
            text = response.text or ""
        except (ValueError, AttributeError):
            text = ""

    prompt_tokens = 0
    completion_tokens = 0
    cached_input_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        prompt_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
        completion_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0
        # The SDK surfaces cache usage as ``cached_content_token_count``
        # (read side). The write side is typically inferred from the
        # cache create call and is not echoed back on generate, so we
        # leave it as ``None`` rather than zero. Distinguishing
        # "provider didn't tell us" from "0 cached tokens" matters for
        # the Phase 0 perf log.
        cached = getattr(response.usage_metadata, "cached_content_token_count", None)
        if isinstance(cached, int):
            cached_input_tokens = cached

    return LlmResponse(
        text=text,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        function_calls=fc_list,
        raw_parts=raw,
        cached_input_tokens=cached_input_tokens,
        cache_write_tokens=cache_write_tokens,
    )


# ──────────────────────────────────────────────
#  Main Entry Point
# ──────────────────────────────────────────────

async def call_llm(
    system_prompt: str,
    user_message: str,
    session_key: str = "",
    provider: str = DEFAULT_PROVIDER,
    model: str = DEFAULT_MODEL,
    history: list = None,
    timeout: int = DEFAULT_TIMEOUT,
    tools: list = None,
    tool_responses: list = None,
    prior_parts: list = None,
    response_schema: dict = None,
    response_mime_type: str = None,
    temperature: float = None,
    route: Optional[str] = None,
    cached_content: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
) -> LlmResponse:
    """Call the LLM using whichever backend is available.

    Pass `temperature=0.2` (or similar) on structured-output calls where
    determinism matters more than creativity. Default (None) keeps the
    provider's default (Gemini = 1.0), which is correct for chat but
    causes wide variance on the same input for analysis pipelines.

    ``route`` identifies the agent route (``"nova"``, ``"deals"``, ...)
    so per-route knobs — Gemini thinking_level, Nova max output tokens,
    Nova token budgets — can apply without leaking to other agents.
    Non-Nova routes get the previous defaults.

    ``cached_content`` is a Gemini ``cachedContents/...`` name. When
    set, the Gemini path sends the request against the cache and omits
    system_instruction + tools from the per-request config. The caller
    is responsible for ensuring the cache was built from the SAME
    system prompt + tool schemas the route would otherwise pass.

    Raises:
        LlmTimeoutError: If the call exceeds the timeout.
    """
    # Conversation history handling depends on whether the request is
    # going to use a Gemini explicit cache.
    #
    #   * Uncached path (legacy / non-Gemini / non-Nova): history is
    #     stuffed into ``system_prompt`` as a ``<conversation_history>``
    #     block. The provider sees one big system instruction and the
    #     current ``user_message`` as the lone user turn. Keep this
    #     shape — non-Nova agents and Emergent depend on it.
    #
    #   * Cached path (Gemini + cached_content set): the cache HOLDS
    #     the system instruction, so anything we append here would be
    #     silently dropped. Pass the raw history through to the Gemini
    #     adapter instead so it can emit proper multi-turn ``contents``
    #     alongside ``cached_content``. The cache stays free of the
    #     volatile per-turn history — which is exactly what makes it
    #     cacheable in the first place.
    history_for_contents: Optional[list] = None
    if history:
        if cached_content:
            history_for_contents = list(history)
        else:
            ctx = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in history[-16:]])
            system_prompt += (
                "\n\n<conversation_history>\n"
                + ctx
                + "\n</conversation_history>"
            )

    backend = _get_backend()

    # Resolve Nova-only knobs lazily so non-Nova calls don't pay the
    # env-read cost. The thinking_level value is read every call because
    # operators expect to flip ``GEMINI_THINKING_LEVEL`` and have it
    # take effect on the next request without a restart.
    effective_thinking_level: Optional[str] = None
    effective_max_output_tokens: Optional[int] = max_output_tokens
    if route == _NOVA_ROUTE and backend == "gemini":
        effective_thinking_level = gemini_thinking_level()
        if effective_max_output_tokens is None:
            effective_max_output_tokens = nova_max_output_tokens()

    try:
        if backend == "emergent":
            logger.debug(f"LLM call via emergent: {provider}/{model}")
            coro = _call_emergent(system_prompt, user_message, session_key, provider, model)
        else:
            logger.debug(f"LLM call via gemini: {model}")
            coro = _call_gemini(
                system_prompt, user_message, model,
                tools=tools, tool_responses=tool_responses, prior_parts=prior_parts,
                response_schema=response_schema, response_mime_type=response_mime_type,
                temperature=temperature,
                thinking_level=effective_thinking_level,
                max_output_tokens=effective_max_output_tokens,
                cached_content=cached_content,
                history_for_contents=history_for_contents,
            )

        return await asyncio.wait_for(coro, timeout=timeout)

    except asyncio.TimeoutError:
        raise LlmTimeoutError(f"LLM call timed out after {timeout}s (model={model})")
    except LLMProviderError:
        # Already normalized — let the conductor's specific handler
        # take over. Re-raise without logging here so the conductor
        # owns the single audit line for the failure.
        raise
    except Exception as exc:  # noqa: BLE001 — we re-raise unrecognized
        # Detect provider quota / rate-limit signals and translate them
        # into the normalized error shape. The full provider exception
        # is logged at warning level for operators; the user-facing
        # text is built from a curated template that omits API keys,
        # provider JSON, and stack traces.
        normalized = _classify_provider_exception(backend, exc)
        if normalized is None:
            # Unrelated failure — let the existing generic-exception
            # handlers in the conductor / chat route surface it. Stack
            # traces stay in operator logs there, not here.
            raise
        logger.warning(
            "LLM provider error (provider=%s, status=%s, code=%s, "
            "retry_after=%s): %s",
            normalized.provider,
            normalized.status_code,
            normalized.code,
            normalized.retry_after_seconds,
            exc,
        )
        raise normalized from exc
