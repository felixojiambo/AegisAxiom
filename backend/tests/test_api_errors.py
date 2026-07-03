"""Tests for the /api/errors frontend error forwarder (v1.3 Wave 3.5).

The endpoint accepts JSON error reports from the frontend and pipes them
into the same GCP Error Reporting integration used for backend exceptions.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from routes import api_errors


def _make_user(user_id="user_grace"):
    return SimpleNamespace(
        user_id=user_id,
        name="Grace",
        email=f"{user_id}@simplimeta.io",
        role="project_manager",
        permissions=set(),
        _perm_closure=set(),
    )


@pytest.mark.asyncio
async def test_report_frontend_error_happy_path():
    """A valid payload forwards to report_exception with merged context."""
    captured = {}

    def fake_report(exc, context=None):
        captured["exc"] = exc
        captured["context"] = context or {}

    with patch("routes.api_errors.report_exception", side_effect=fake_report):
        payload = api_errors.FrontendError(
            message="TypeError: Cannot read property 'foo' of undefined",
            stack="at Component.render (App.js:42)",
            url="https://app.simplimeta.io/projects/p1",
            user_agent="Mozilla/5.0",
            context={"component": "ProjectBoard"},
            severity="error",
        )
        request = MagicMock()
        user = _make_user()

        result = await api_errors.report_frontend_error(
            payload=payload, request=request, current_user=user,
        )

    assert result == {"ok": True}
    assert isinstance(captured["exc"], RuntimeError)
    assert "frontend: TypeError" in str(captured["exc"])
    ctx = captured["context"]
    assert ctx["user_id"] == "user_grace"
    assert ctx["user_email"] == "user_grace@simplimeta.io"
    assert ctx["frontend_stack"] == "at Component.render (App.js:42)"
    assert ctx["frontend_url"] == "https://app.simplimeta.io/projects/p1"
    assert ctx["severity"] == "error"
    assert ctx["source"] == "frontend"
    # Caller-supplied context fields are merged in.
    assert ctx["component"] == "ProjectBoard"


@pytest.mark.asyncio
async def test_report_frontend_error_clamps_invalid_severity():
    """A severity outside the allow-list is coerced to 'error'."""
    captured = {}

    def fake_report(exc, context=None):
        captured["context"] = context or {}

    with patch("routes.api_errors.report_exception", side_effect=fake_report):
        payload = api_errors.FrontendError(
            message="boom",
            severity="extreme-doom",  # not in allow-list
        )
        request = MagicMock()
        user = _make_user()

        await api_errors.report_frontend_error(
            payload=payload, request=request, current_user=user,
        )

    assert captured["context"]["severity"] == "error"


@pytest.mark.asyncio
async def test_report_frontend_error_swallows_observability_failure():
    """If report_exception itself raises, the endpoint must still return 200.
    Frontend error reporting is a best-effort path."""
    def bombing_report(exc, context=None):
        raise RuntimeError("error reporter is down")

    with patch("routes.api_errors.report_exception", side_effect=bombing_report):
        payload = api_errors.FrontendError(message="boom")
        request = MagicMock()
        user = _make_user()

        # Must NOT raise.
        result = await api_errors.report_frontend_error(
            payload=payload, request=request, current_user=user,
        )
    assert result == {"ok": True}


def test_frontend_error_model_rejects_empty_message():
    """Empty message must fail Pydantic validation (422 at the HTTP layer)."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        api_errors.FrontendError(message="")


def test_frontend_error_model_truncation_caps():
    """The model enforces upper bounds via max_length so a runaway frontend
    can't dump multi-MB payloads."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        api_errors.FrontendError(message="x" * (4096 + 1))
