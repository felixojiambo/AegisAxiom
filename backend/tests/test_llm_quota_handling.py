"""Tests for graceful Gemini/LLM quota error handling.

Targets the failure mode reported in the operator log: a 429
RESOURCE_EXHAUSTED from Gemini was logged as "AI chat error" by
``services.ai.conductor`` and immediately followed by a Starlette
``RuntimeError: No response returned.`` The chat route handler had no
defensive try/except around ``get_chat_response``, and the
catch-all in ``get_chat_response`` returned a string while every
other branch returned a dict — both contributed to the route exiting
without a response under sustained quota exhaustion.

These tests pin the documented contracts:
  * Quota errors translate to a structured ``LLMProviderError``.
  * The user-facing message is curated — no API keys, no provider
    JSON, no stack traces.
  * Retry-after, when surfaced, lands in the user message.
  * Conductor's catch-all always returns the dict shape the chat
    route expects.
  * Non-quota errors continue to log and behave as today.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ai.llm import (
    LLMProviderError,
    _classify_provider_exception,
    _extract_retry_after_seconds,
    _extract_status_code,
    _is_quota_error,
)


# ---------------------------------------------------------------------------
# Fakes that mimic the SDK shapes we want to detect
# ---------------------------------------------------------------------------


class _FakeGeminiClientError(Exception):
    """Stand-in for ``google.genai.errors.ClientError`` — same surface
    we need for detection (status code on the instance)."""

    def __init__(self, code: int, message: str = ""):
        super().__init__(message or f"{code} error")
        self.code = code


class _FakeHttpxError(Exception):
    """Stand-in for an httpx-style error carrying a ``response`` with
    headers + status_code."""

    def __init__(self, status: int, headers: dict | None = None, text: str = ""):
        super().__init__(text or f"HTTP {status}")
        self.response = SimpleNamespace(
            status_code=status,
            headers=headers or {},
        )


# ---------------------------------------------------------------------------
# 1. quota detection
# ---------------------------------------------------------------------------


class TestQuotaDetection:
    def test_status_code_429_classified_as_quota(self):
        exc = _FakeGeminiClientError(429, "Quota exceeded for free_tier_requests")
        assert _is_quota_error(exc) is True

    def test_resource_exhausted_string_token_classified(self):
        exc = RuntimeError("status: RESOURCE_EXHAUSTED quota exceeded")
        assert _is_quota_error(exc) is True

    def test_quota_keyword_in_message_classified(self):
        exc = RuntimeError("Generic provider error: quota exceeded — try later")
        assert _is_quota_error(exc) is True

    def test_unrelated_error_not_classified(self):
        exc = RuntimeError("Internal server error 500")
        assert _is_quota_error(exc) is False

    def test_resourceexhausted_class_name_fallback(self):
        # SDKs that raise a distinct subclass without a status code on
        # the instance still get caught via the class-name probe.
        ResourceExhausted = type("ResourceExhausted", (Exception,), {})
        exc = ResourceExhausted("free_tier limit reached")
        assert _is_quota_error(exc) is True


# ---------------------------------------------------------------------------
# 2. classify_provider_exception → LLMProviderError shape
# ---------------------------------------------------------------------------


class TestClassifyProviderException:
    def test_quota_returns_normalized_error(self):
        exc = _FakeGeminiClientError(
            429,
            "RESOURCE_EXHAUSTED — free_tier_requests limit: 0",
        )
        out = _classify_provider_exception("google", exc)
        assert isinstance(out, LLMProviderError)
        assert out.provider == "google"
        assert out.status_code == 429
        assert out.code == "RESOURCE_EXHAUSTED"

    def test_unrelated_returns_none(self):
        exc = RuntimeError("Internal 500")
        assert _classify_provider_exception("google", exc) is None

    def test_user_message_has_no_provider_internals(self):
        exc = _FakeGeminiClientError(
            429,
            "Quota exceeded for model gemini-3.1-pro and project "
            "internal-project-12345 with API key sk-test-secretvalue",
        )
        out = _classify_provider_exception("google", exc)
        assert out is not None
        msg = out.user_message
        # No API key, no project ID, no model identifier in the
        # user-facing text.
        assert "sk-test-secretvalue" not in msg
        assert "internal-project-12345" not in msg
        assert "gemini-3.1-pro" not in msg
        # And no obvious traceback markers.
        assert "Traceback" not in msg
        assert "File \"" not in msg

    def test_retry_after_from_response_header_lands_in_message(self):
        exc = _FakeHttpxError(
            429,
            headers={"Retry-After": "42"},
            text="rate limit",
        )
        out = _classify_provider_exception("google", exc)
        assert out is not None
        assert out.retry_after_seconds == 42
        assert "42" in out.user_message
        assert "Suggested retry" in out.user_message

    def test_retry_after_scraped_from_message_when_no_header(self):
        exc = RuntimeError(
            "RESOURCE_EXHAUSTED — quota exceeded; retry after 17 seconds"
        )
        out = _classify_provider_exception("google", exc)
        assert out is not None
        # Falls back to the regex on the message body.
        assert out.retry_after_seconds == 17

    def test_no_retry_after_omits_suggested_retry(self):
        exc = _FakeGeminiClientError(429, "RESOURCE_EXHAUSTED quota exceeded")
        out = _classify_provider_exception("google", exc)
        assert out is not None
        assert out.retry_after_seconds is None
        assert "Suggested retry" not in out.user_message
        assert "rate-limited" in out.user_message.lower()


# ---------------------------------------------------------------------------
# 3. call_llm raises LLMProviderError on quota
# ---------------------------------------------------------------------------


class TestCallLlmQuotaPath:
    def test_call_llm_raises_llmprovidererror_on_quota(self, monkeypatch):
        # Force the Gemini backend by setting the env key before
        # call_llm reads it. Then patch the inner _call_gemini coro
        # to raise a synthetic quota error. The wrapper must catch
        # it and re-raise as LLMProviderError.
        from services.ai import llm as llm_mod

        async def _boom(*args, **kwargs):
            raise _FakeGeminiClientError(
                429,
                "RESOURCE_EXHAUSTED — quota exceeded",
            )

        monkeypatch.setattr(llm_mod, "_get_backend", lambda: "google")
        monkeypatch.setattr(llm_mod, "_call_gemini", _boom)
        with pytest.raises(LLMProviderError) as excinfo:
            asyncio.run(llm_mod.call_llm("sys", "hello"))
        err = excinfo.value
        assert err.status_code == 429
        assert err.code == "RESOURCE_EXHAUSTED"
        assert "rate-limited" in err.user_message.lower()

    def test_call_llm_propagates_unrelated_errors(self, monkeypatch):
        # An exception that does NOT look like a quota error stays
        # raw — surfaces in operator logs at full fidelity via the
        # downstream catch-all rather than masquerading as a quota
        # event.
        from services.ai import llm as llm_mod

        async def _boom(*args, **kwargs):
            raise RuntimeError("totally unrelated bug")

        monkeypatch.setattr(llm_mod, "_get_backend", lambda: "google")
        monkeypatch.setattr(llm_mod, "_call_gemini", _boom)
        with pytest.raises(RuntimeError) as excinfo:
            asyncio.run(llm_mod.call_llm("sys", "hello"))
        # Original exception preserved, not reclassified.
        assert "totally unrelated bug" in str(excinfo.value)
        assert not isinstance(excinfo.value, LLMProviderError)


# ---------------------------------------------------------------------------
# 4. conductor catch-all returns the dict shape the route expects
# ---------------------------------------------------------------------------


class TestConductorReturnShape:
    def test_unexpected_error_returns_dict_with_thinking(self):
        # The previous catch-all returned a bare string while every
        # other branch returned ``{"content": ..., "thinking": ...}``.
        # That asymmetry was a structural footgun for the chat route.
        # This test pins the new symmetric shape.
        import asyncio
        from services.ai import conductor

        async def _boom(*args, **kwargs):
            raise RuntimeError("synthesised inner failure")

        # Patch _route_message so we exercise get_chat_response's
        # outer try/except without standing up the full agent stack.
        with patch.object(conductor, "_route_message", _boom), \
             patch.object(conductor, "_check_pending", AsyncMock(return_value=None)), \
             patch.object(conductor, "check_budget", AsyncMock()):
            user = SimpleNamespace(
                user_id="u1",
                name="Test User",
                email="t@example.com",
            )
            db = MagicMock()
            result = asyncio.run(
                conductor.get_chat_response(
                    user,
                    history=[{"role": "user", "content": "hi"}],
                    db=db,
                    session_id="s1",
                )
            )
        assert isinstance(result, dict)
        assert "content" in result
        assert "thinking" in result
        assert isinstance(result["thinking"], list)


# ---------------------------------------------------------------------------
# 5. conductor surfaces curated user_message for quota
# ---------------------------------------------------------------------------


class TestConductorQuotaPath:
    def test_quota_yields_friendly_dict(self):
        from services.ai import conductor

        quota_err = LLMProviderError(
            provider="google",
            status_code=429,
            code="RESOURCE_EXHAUSTED",
            retry_after_seconds=30,
            user_message=(
                "The AI provider is currently rate-limited, so Nova "
                "could not complete the answer. Please retry shortly "
                "or switch to an AI provider/key with available quota. "
                "Suggested retry: about 30 seconds."
            ),
        )

        async def _boom(*args, **kwargs):
            raise quota_err

        with patch.object(conductor, "_route_message", _boom), \
             patch.object(conductor, "_check_pending", AsyncMock(return_value=None)), \
             patch.object(conductor, "check_budget", AsyncMock()):
            user = SimpleNamespace(
                user_id="u1",
                name="Test User",
                email="t@example.com",
            )
            db = MagicMock()
            result = asyncio.run(
                conductor.get_chat_response(
                    user,
                    history=[{"role": "user", "content": "hi"}],
                    db=db,
                    session_id="s1",
                )
            )
        assert isinstance(result, dict)
        assert "rate-limited" in result["content"]
        # Curated message — no Traceback, no provider JSON markers
        assert "Traceback" not in result["content"]
        # Trace mentions provider failure
        steps = [s.get("step") for s in result["thinking"]]
        assert "Provider" in steps


# ---------------------------------------------------------------------------
# LLMProviderError equality (PR #158 review backlog Fix 3)
# ---------------------------------------------------------------------------


def test_llm_provider_error_uses_identity_equality_not_field_equality():
    """The dataclass declares eq=False so two distinct exception
    instances with identical fields are NOT equal under ==. The
    default eq=True would generate a field-equality __eq__, which
    breaks ``except ... as exc`` chains and assertion patterns where
    callers rely on identity comparison."""
    err1 = LLMProviderError(
        provider="google",
        status_code=429,
        code="RESOURCE_EXHAUSTED",
        user_message="quota",
    )
    err2 = LLMProviderError(
        provider="google",
        status_code=429,
        code="RESOURCE_EXHAUSTED",
        user_message="quota",
    )
    assert err1 is not err2
    assert err1 != err2
    # Each instance still equals itself (identity equality holds).
    assert err1 == err1
