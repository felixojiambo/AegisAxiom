"""Shared configuration for the AI service modules.

Supports three deployment contexts:
  1. Emergent platform  — uses EMERGENT_LLM_KEY with any provider
  2. Local dev          — uses GEMINI_API_KEY with Google Gemini
  3. GCP production     — uses GEMINI_API_KEY with Google Gemini

Set MODEL_PROVIDER and MODEL_NAME via env vars to switch providers.
"""

import logging
import os
from typing import Optional


logger = logging.getLogger(__name__)


# --- API Keys ---
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# --- Model selection (override via env vars) ---
# Defaults to Anthropic via Emergent; switch to "google" / "gemini-2.0-flash"
# for local dev or GCP deployments.
MODEL_PROVIDER = os.environ.get("MODEL_PROVIDER", "anthropic")
MODEL_NAME = os.environ.get("MODEL_NAME", "claude-sonnet-4-5-20250929")


# --- Which API key to use ---
# Emergent key takes precedence; fall back to Gemini key for local/GCP
def get_llm_key() -> str:
    """Return the best available LLM API key for the current environment."""
    if EMERGENT_LLM_KEY:
        return EMERGENT_LLM_KEY
    if GEMINI_API_KEY:
        return GEMINI_API_KEY
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Nova / Gemini quick-win env knobs
# ─────────────────────────────────────────────────────────────────────────────
# Parsing helpers below all share the same contract: invalid values log a
# warning once and fall back to the documented default. No env var here is
# required for the app to boot; everything is opt-in.

_VALID_THINKING_LEVELS: frozenset[str] = frozenset({"minimal", "low", "medium", "high"})


def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    cleaned = raw.strip().lower()
    if cleaned in {"1", "true", "yes", "on"}:
        return True
    if cleaned in {"0", "false", "no", "off"}:
        return False
    logger.warning(
        "Invalid bool for %s=%r; falling back to %s", name, raw, default
    )
    return default


def _parse_int_env(name: str, default: Optional[int]) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid int for %s=%r; falling back to %r", name, raw, default
        )
        return default


def _parse_thinking_level_env(name: str = "GEMINI_THINKING_LEVEL") -> Optional[str]:
    """Return the cleaned thinking level value or None when the env
    var is unset or explicitly disabled.

    The literal value ``none`` is treated as equivalent to the env var
    being unset so an operator who wants to disable thinking does not
    get a misleading warning. ``none`` is NOT added to the valid set
    itself because downstream consumers of the level should not see it
    as a valid level value.
    """
    raw = os.environ.get(name)
    if raw is None:
        return None
    cleaned = raw.strip().lower()
    if cleaned in ("", "none"):
        return None
    if cleaned in _VALID_THINKING_LEVELS:
        return cleaned
    logger.warning(
        "Invalid %s=%r expected one of %s. Omitting thinking_level.",
        name,
        raw,
        sorted(_VALID_THINKING_LEVELS),
    )
    return None


def nova_prompt_cache_enabled() -> bool:
    """Master switch for Nova prompt-caching behavior.

    Off by default. When false, the LLM request shape must be identical
    to the pre-cache code path — no cache markers, no extra metadata.
    """
    return _parse_bool_env("NOVA_PROMPT_CACHE_ENABLED", default=False)


def gemini_context_cache_enabled() -> bool:
    """Gemini-specific explicit context cache toggle.

    Independent of :func:`nova_prompt_cache_enabled` so an operator can
    opt into Nova caching globally while keeping the Gemini explicit
    cache off (e.g. while debugging cache invalidation). The Gemini
    cache only activates when both flags are true and the provider is
    google.
    """
    return _parse_bool_env("GEMINI_CONTEXT_CACHE_ENABLED", default=False)


def gemini_context_cache_ttl_seconds() -> int:
    """TTL for Gemini explicit caches in seconds.

    Default 3600 (1h). Invalid / non-positive values fall back to the
    default; the Gemini API rejects sub-minute TTLs anyway.
    """
    value = _parse_int_env("GEMINI_CONTEXT_CACHE_TTL_SECONDS", default=3600)
    if not value or value <= 0:
        return 3600
    return value


def gemini_thinking_level() -> Optional[str]:
    """Return the configured thinking level or None if unset/invalid."""
    return _parse_thinking_level_env()


def nova_session_token_budget() -> Optional[int]:
    """Nova-specific session budget; falls back to AI_SESSION_TOKEN_BUDGET."""
    return _parse_int_env("NOVA_SESSION_TOKEN_BUDGET", default=None)


def nova_daily_token_budget() -> Optional[int]:
    """Nova-specific per-user daily budget; falls back to AI_DAILY_TOKEN_BUDGET."""
    return _parse_int_env("NOVA_DAILY_TOKEN_BUDGET", default=None)


def nova_max_output_tokens() -> Optional[int]:
    """Optional per-call max output tokens override for Nova.

    Only consumed by the Gemini path today; other providers ignore the
    field silently. ``None`` means "use the provider/SDK default".
    """
    return _parse_int_env("NOVA_MAX_OUTPUT_TOKENS", default=None)


__all__ = [
    "EMERGENT_LLM_KEY",
    "GEMINI_API_KEY",
    "MODEL_NAME",
    "MODEL_PROVIDER",
    "gemini_context_cache_enabled",
    "gemini_context_cache_ttl_seconds",
    "gemini_thinking_level",
    "get_llm_key",
    "nova_daily_token_budget",
    "nova_max_output_tokens",
    "nova_prompt_cache_enabled",
    "nova_session_token_budget",
]
