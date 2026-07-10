"""Tests for the Generate-with-AI flow that wires Fireflies transcripts
into the SOW SMART proposal generator.

Locks the structured error contract every failure mode must satisfy:

  * missing FIREFLIES_API_KEY               -> 503 + fireflies_api_key_missing
  * Fireflies API failure                   -> 502 + fireflies_api_error
  * Empty transcript content                -> 422 + transcript_empty
  * Successful transcript fetch feeds AI    -> 200 + smart payload
  * /fireflies/transcripts list returns the same structured error shape

Synthetic fixtures only — no live Fireflies, no live AI, no Mongo.
The Fireflies service and the SOW Drafter LLM call are both
monkeypatched. Tests set ``FIREFLIES_API_KEY`` directly in
``os.environ`` and rely on the service reading the env per-call (the
previous module-constant read froze the value at import and made these
cases untestable).
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_OPP_ID = "opp_synth_fireflies_001"
_LOB = "professional_services"


def _make_user(*, can_create: bool = True):
    perms = ["sales_ops.solutions_design.create"] if can_create else []
    return SimpleNamespace(
        user_id="u_synth_ff_001",
        email="fireflies-tester@aegisaxiom.test",
        role="sales_ops",
        permissions=perms,
    )


def _build_app(user):
    from routes.fireflies import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/sales-ops")
    app.dependency_overrides[get_current_user] = lambda: user
    return app


@pytest.fixture(autouse=True)
def _clear_fireflies_env(monkeypatch):
    """Every test starts with no FIREFLIES_API_KEY in env so each test
    must opt-in to the key. The service is now expected to read the
    env at call time — if any test ever sees a stale value, it's a
    sign the module-import-time read regressed."""
    monkeypatch.delenv("FIREFLIES_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _clear_sow_drafter_env(monkeypatch):
    """Same contract for the SOW drafter API key. Tests that exercise
    a happy AI-call path opt in by setting both ANTHROPIC_API_KEY (or
    SOW_DRAFTER_API_KEY) and patching ``_call_sow_drafter`` so no real
    HTTP traffic is issued."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SOW_DRAFTER_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# Service-level: FirefliesError contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_transcripts_raises_key_missing_when_env_absent():
    """The service raises a typed FirefliesError with the
    fireflies_api_key_missing code when FIREFLIES_API_KEY is not in
    env. The previous behaviour was to raise a bare ValueError, which
    was harder to translate into a structured HTTP response."""
    from services.fireflies_service import (
        FIREFLIES_ERR_KEY_MISSING,
        FirefliesError,
        list_transcripts,
    )

    with pytest.raises(FirefliesError) as excinfo:
        await list_transcripts(limit=5)
    assert excinfo.value.code == FIREFLIES_ERR_KEY_MISSING
    # Never leak the key — the message describes the config gap only.
    assert "FIREFLIES_API_KEY" in excinfo.value.message


@pytest.mark.asyncio
async def test_fetch_transcript_content_raises_key_missing_when_env_absent():
    from services.fireflies_service import (
        FIREFLIES_ERR_KEY_MISSING,
        FirefliesError,
        fetch_transcript_content,
    )

    with pytest.raises(FirefliesError) as excinfo:
        await fetch_transcript_content(["t_synth_1"])
    assert excinfo.value.code == FIREFLIES_ERR_KEY_MISSING


@pytest.mark.asyncio
async def test_api_key_is_read_at_call_time(monkeypatch):
    """If the module read the key at import time, this test would
    fail because the key was empty at import. Setting it before the
    call must succeed without restarting the process."""
    from services import fireflies_service

    monkeypatch.setenv("FIREFLIES_API_KEY", "test-key-synth")
    # _get_api_key returns the freshly-set value.
    assert fireflies_service._get_api_key() == "test-key-synth"


# ---------------------------------------------------------------------------
# Route-level: GET /fireflies/transcripts structured errors
# ---------------------------------------------------------------------------


class TestListTranscriptsRoute:
    def _hit(self):
        user = _make_user()
        app = _build_app(user)
        client = TestClient(app)
        return client.get("/api/sales-ops/fireflies/transcripts")

    def test_missing_key_returns_503_with_structured_detail(self):
        # FIREFLIES_API_KEY cleared by fixture.
        resp = self._hit()
        assert resp.status_code == 503
        body = resp.json()
        assert body["detail"]["error_code"] == "fireflies_api_key_missing"
        assert "FIREFLIES_API_KEY" in body["detail"]["message"]

    def test_fireflies_api_error_returns_502_with_structured_detail(self, monkeypatch):
        monkeypatch.setenv("FIREFLIES_API_KEY", "test-key-synth")
        from services.fireflies_service import (
            FIREFLIES_ERR_API,
            FirefliesError,
        )

        async def _fail(*_a, **_kw):
            raise FirefliesError(FIREFLIES_ERR_API, "Fireflies is sad")

        with patch("routes.fireflies.list_transcripts", new=_fail):
            user = _make_user()
            app = _build_app(user)
            client = TestClient(app)
            resp = client.get("/api/sales-ops/fireflies/transcripts")
        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["error_code"] == "fireflies_api_error"
        assert body["detail"]["message"] == "Fireflies is sad"

    def test_success_returns_items_list(self, monkeypatch):
        monkeypatch.setenv("FIREFLIES_API_KEY", "test-key-synth")
        synthetic_items = [
            {
                "id": "t_synth_1",
                "title": "Synthetic Meeting 1",
                "date": 0,
                "duration": 1800,
                "participants": [],
                "summary": "",
            }
        ]

        async def _ok(*_a, **_kw):
            return synthetic_items

        with patch("routes.fireflies.list_transcripts", new=_ok):
            user = _make_user()
            app = _build_app(user)
            client = TestClient(app)
            resp = client.get("/api/sales-ops/fireflies/transcripts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["items"][0]["id"] == "t_synth_1"

    def test_permission_denied(self):
        user = _make_user(can_create=False)
        app = _build_app(user)
        client = TestClient(app)
        resp = client.get("/api/sales-ops/fireflies/transcripts")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Route-level: POST /fireflies/generate-smart — every failure mode
# ---------------------------------------------------------------------------


def _generate_payload(**overrides):
    return {
        "transcript_ids": overrides.get("transcript_ids", ["t_synth_1"]),
        "opportunity_id": overrides.get("opportunity_id", _OPP_ID),
        "lob": overrides.get("lob", _LOB),
    }


class TestGenerateSmartRoute:
    def _post(self, payload):
        user = _make_user()
        app = _build_app(user)
        client = TestClient(app)
        return client.post(
            "/api/sales-ops/fireflies/generate-smart",
            json=payload,
        )

    def test_missing_key_returns_503_with_structured_detail(self):
        # FIREFLIES_API_KEY cleared by fixture. The service will raise
        # FirefliesError on the first call inside the route.
        resp = self._post(_generate_payload())
        assert resp.status_code == 503
        body = resp.json()
        assert body["detail"]["error_code"] == "fireflies_api_key_missing"

    def test_fireflies_api_failure_returns_502(self, monkeypatch):
        monkeypatch.setenv("FIREFLIES_API_KEY", "test-key-synth")
        from services.fireflies_service import (
            FIREFLIES_ERR_API,
            FirefliesError,
        )

        async def _fail(*_a, **_kw):
            raise FirefliesError(FIREFLIES_ERR_API, "upstream down")

        with patch("routes.fireflies.fetch_transcript_content", new=_fail):
            resp = self._post(_generate_payload())
        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["error_code"] == "fireflies_api_error"
        assert "upstream down" in body["detail"]["message"]

    def test_transcript_not_found_returns_404(self, monkeypatch):
        monkeypatch.setenv("FIREFLIES_API_KEY", "test-key-synth")
        from services.fireflies_service import (
            FIREFLIES_ERR_TRANSCRIPT_NOT_FOUND,
            FirefliesError,
        )

        async def _missing(*_a, **_kw):
            raise FirefliesError(
                FIREFLIES_ERR_TRANSCRIPT_NOT_FOUND,
                "no rows",
            )

        with patch("routes.fireflies.fetch_transcript_content", new=_missing):
            resp = self._post(_generate_payload())
        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["error_code"] == "transcript_not_found"

    def test_empty_transcript_returns_422_transcript_empty(self, monkeypatch):
        """When Fireflies returns transcript objects whose sentences
        are all blank, the route must bail with transcript_empty
        instead of paying for an AI call on whitespace."""
        monkeypatch.setenv("FIREFLIES_API_KEY", "test-key-synth")

        async def _empty(*_a, **_kw):
            # Realistic empty-meeting shape from Fireflies.
            return [
                {"id": "t_synth_1", "title": "Empty Meeting", "content": ""},
            ]

        with patch("routes.fireflies.fetch_transcript_content", new=_empty):
            resp = self._post(_generate_payload())
        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"]["error_code"] == "transcript_empty"

    def test_success_feeds_transcripts_into_ai_and_returns_smart(self, monkeypatch):
        """Happy path: transcripts fetched, combined, passed to the
        SOW Drafter, and the response carries every SMART field."""
        monkeypatch.setenv("FIREFLIES_API_KEY", "test-key-synth")

        captured = {}

        async def _content(transcript_ids):
            return [
                {
                    "id": tid,
                    "title": f"Meeting {tid}",
                    "content": "Alice: scope is X.\nBob: budget is Y.",
                }
                for tid in transcript_ids
            ]

        async def _ai(system_prompt, user_message, parse_json=True):
            captured["system_prompt"] = system_prompt
            captured["user_message"] = user_message
            return {
                "executive_summary": "Synthetic exec summary.",
                "smart_specific": "Synthetic specific.",
                "smart_measurable": "Synthetic measurable.",
                "smart_achievable": "Synthetic achievable.",
                "smart_relevant": "Synthetic relevant.",
                "smart_time_bound": "Synthetic time-bound.",
            }

        with patch("routes.fireflies.fetch_transcript_content", new=_content), \
             patch("routes.fireflies._call_sow_drafter", new=_ai):
            resp = self._post(_generate_payload(
                transcript_ids=["t_synth_1", "t_synth_2"]
            ))

        assert resp.status_code == 200
        body = resp.json()
        smart = body["smart"]
        assert smart["executive_summary"] == "Synthetic exec summary."
        assert smart["smart_specific"] == "Synthetic specific."
        assert smart["smart_measurable"] == "Synthetic measurable."
        assert smart["smart_achievable"] == "Synthetic achievable."
        assert smart["smart_relevant"] == "Synthetic relevant."
        assert smart["smart_time_bound"] == "Synthetic time-bound."
        assert body["transcripts_used"] == 2
        # The combined transcript content must reach the AI's user
        # message — proving the wiring is intact end-to-end.
        assert "Alice: scope is X" in captured["user_message"]
        assert "Bob: budget is Y" in captured["user_message"]

    def test_permission_denied(self):
        user = _make_user(can_create=False)
        app = _build_app(user)
        client = TestClient(app)
        resp = client.post(
            "/api/sales-ops/fireflies/generate-smart",
            json=_generate_payload(),
        )
        assert resp.status_code == 403

    def test_empty_transcript_ids_rejected_by_validation(self):
        """The Pydantic model enforces min_length=1 on transcript_ids,
        so the empty list never reaches the service."""
        resp = self._post(_generate_payload(transcript_ids=[]))
        assert resp.status_code == 422  # FastAPI validation error


# ---------------------------------------------------------------------------
# SOW Drafter LLM key — separate failure surface from Fireflies
# ---------------------------------------------------------------------------


class TestSowDrafterKeyValidation:
    """Pin the SOW drafter (LLM proxy) error contract.

    The previous bug: ANTHROPIC_API_KEY was captured at module import
    time. When empty, the Authorization header went out as
    ``Bearer `` (trailing space, no token) and httpx rejected the
    request with ``illegal header value b'Bearer'`` — surfaced to the
    user as an opaque 502 string.

    The fix reads the key at call time and short-circuits with a
    structured 503 BEFORE any HTTP call. These tests pin the contract.
    """

    def _post(self, payload):
        user = _make_user()
        app = _build_app(user)
        client = TestClient(app)
        return client.post(
            "/api/sales-ops/fireflies/generate-smart",
            json=payload,
        )

    def test_missing_anthropic_key_returns_503_with_structured_detail(self, monkeypatch):
        """The SOW drafter helper must validate the key BEFORE the
        HTTP call. With only FIREFLIES_API_KEY set, transcript loading
        succeeds and we land inside _call_sow_drafter; the empty key
        must short-circuit there with sow_drafter_api_key_missing."""
        monkeypatch.setenv("FIREFLIES_API_KEY", "test-key-synth")
        # ANTHROPIC_API_KEY and SOW_DRAFTER_API_KEY cleared by autouse fixture.

        async def _content(transcript_ids):
            return [
                {"id": tid, "title": "M", "content": "Speaker: hi"}
                for tid in transcript_ids
            ]

        with patch("routes.fireflies.fetch_transcript_content", new=_content):
            resp = self._post(_generate_payload())
        assert resp.status_code == 503
        body = resp.json()
        assert body["detail"]["error_code"] == "sow_drafter_api_key_missing"
        # Message names the env var so admins know what to set.
        assert "ANTHROPIC_API_KEY" in body["detail"]["message"]

    def test_missing_key_does_not_call_httpx(self, monkeypatch):
        """The validate-before-call rule means an empty key MUST NOT
        produce any outbound HTTP request. If the regression ever
        comes back, this is the test that catches it — the patched
        AsyncClient would have its constructor called."""
        monkeypatch.setenv("FIREFLIES_API_KEY", "test-key-synth")

        async def _content(transcript_ids):
            return [
                {"id": tid, "title": "M", "content": "Speaker: hi"}
                for tid in transcript_ids
            ]

        # Track whether anyone tried to construct an AsyncClient.
        called = {"hit": False}

        class _ExplosiveClient:
            def __init__(self, *a, **kw):
                called["hit"] = True
                raise AssertionError(
                    "httpx.AsyncClient was constructed even though "
                    "the SOW drafter API key was missing"
                )

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

        with patch("routes.fireflies.fetch_transcript_content", new=_content), \
             patch("httpx.AsyncClient", new=_ExplosiveClient):
            resp = self._post(_generate_payload())
        assert resp.status_code == 503
        assert called["hit"] is False

    def test_sow_drafter_http_failure_returns_502(self, monkeypatch):
        """Any httpx-level failure during the SOW drafter call must
        map to sow_drafter_api_error (not back to the previous
        free-form 'AI service call failed' string)."""
        monkeypatch.setenv("FIREFLIES_API_KEY", "test-key-synth")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-synth")

        async def _content(transcript_ids):
            return [
                {"id": tid, "title": "M", "content": "Speaker: hi"}
                for tid in transcript_ids
            ]

        class _FailingClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def post(self, *a, **kw):
                # Simulate any httpx-level failure (DNS, timeout, 5xx,
                # malformed response). The route should map ALL of these
                # to the same structured 502.
                raise RuntimeError("synthetic upstream failure")

        with patch("routes.fireflies.fetch_transcript_content", new=_content), \
             patch("httpx.AsyncClient", new=_FailingClient):
            resp = self._post(_generate_payload())
        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["error_code"] == "sow_drafter_api_error"
        # Message is deliberately generic — it must NOT leak the
        # exception text (which could include header echoes).
        assert "synthetic" not in body["detail"]["message"]
        assert "Bearer" not in body["detail"]["message"]

    def test_sow_drafter_key_env_is_read_at_call_time(self, monkeypatch):
        """The key resolver must read os.environ live. If the key was
        captured at import, setting it inside a test would have no
        effect and the regression test below would behave identically
        to the missing-key test above."""
        from routes import fireflies as fireflies_route

        # First: empty -> empty.
        assert fireflies_route._get_sow_drafter_api_key() == ""

        # Set ANTHROPIC_API_KEY and the resolver returns it.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-synth-1")
        assert fireflies_route._get_sow_drafter_api_key() == "anthropic-synth-1"

        # SOW_DRAFTER_API_KEY override wins over ANTHROPIC_API_KEY so a
        # deployment can split keys without monkey-patching code.
        monkeypatch.setenv("SOW_DRAFTER_API_KEY", "sow-synth-2")
        assert fireflies_route._get_sow_drafter_api_key() == "sow-synth-2"

    def test_successful_generation_still_works_with_anthropic_key(self, monkeypatch):
        """The fix must not regress the happy path. With both keys
        configured and _call_sow_drafter patched, the route returns
        every SMART field exactly as before."""
        monkeypatch.setenv("FIREFLIES_API_KEY", "test-key-synth")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-synth")

        async def _content(transcript_ids):
            return [
                {"id": tid, "title": "M", "content": "Speaker: hi"}
                for tid in transcript_ids
            ]

        async def _ai(system_prompt, user_message, parse_json=True):
            return {
                "executive_summary": "ok",
                "smart_specific": "ok",
                "smart_measurable": "ok",
                "smart_achievable": "ok",
                "smart_relevant": "ok",
                "smart_time_bound": "ok",
            }

        with patch("routes.fireflies.fetch_transcript_content", new=_content), \
             patch("routes.fireflies._call_sow_drafter", new=_ai):
            resp = self._post(_generate_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["smart"]["smart_specific"] == "ok"


# ---------------------------------------------------------------------------
# Cross-error isolation
# ---------------------------------------------------------------------------


def test_fireflies_missing_key_does_not_emit_sow_drafter_code(monkeypatch):
    """Regression guard: when ONLY the Fireflies key is missing, the
    route must report ``fireflies_api_key_missing``, not the SOW
    drafter code. A code crossed-wires here would mislead admins to
    set the wrong env var."""
    # Both envs are cleared by the autouse fixtures; double-confirm so
    # the assertion below has unambiguous meaning.
    monkeypatch.delenv("FIREFLIES_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SOW_DRAFTER_API_KEY", raising=False)

    user = _make_user()
    app = _build_app(user)
    client = TestClient(app)
    resp = client.post(
        "/api/sales-ops/fireflies/generate-smart",
        json=_generate_payload(),
    )
    assert resp.status_code == 503
    body = resp.json()
    # Fireflies fails FIRST in the route (transcripts are fetched
    # before the AI call), so the code must be fireflies, not SOW
    # drafter.
    assert body["detail"]["error_code"] == "fireflies_api_key_missing"
    assert body["detail"]["error_code"] != "sow_drafter_api_key_missing"
