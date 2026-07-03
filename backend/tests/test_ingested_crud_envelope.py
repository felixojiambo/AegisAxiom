"""
Tests for the crud_envelope context manager (services/ingestion/route_helpers.py).

Covers:
  - allow path: body executes, event emitted, audit written
  - deny path: 403, body not executed, no event/audit
  - policy engine raises: 503 (fail-closed)
  - body raises: exception propagates, no event/audit
  - event bus failure post-success: logged, not propagated, audit still written
  - audit failure post-success: logged, not propagated
  - ctx.set_after, ctx.set_resource_id, ctx.skip_audit
  - request_id derivation and sharing across envelopes
  - two envelopes sharing request_id
"""
import pytest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(user_id="user_001", email="test@example.com"):
    u = SimpleNamespace()
    u.user_id = user_id
    u.email = email
    return u


def _make_request(request_id=None):
    """Return a minimal mock that simulates a FastAPI Request.state."""
    req = MagicMock()
    state = SimpleNamespace()
    if request_id:
        state.request_id = request_id
    req.state = state
    return req


async def _run_envelope(
    module="crm",
    entity="contact",
    action="created",
    resource_id="res_001",
    user=None,
    db=None,
    before=None,
    request=None,
    body_fn=None,
):
    """Helper: run the envelope with optional custom body."""
    from services.ingestion.route_helpers import crud_envelope

    if user is None:
        user = _make_user()
    if db is None:
        db = MagicMock()

    results = {}

    async with crud_envelope(
        module=module,
        entity=entity,
        action=action,
        resource_id=resource_id,
        current_user=user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        if body_fn:
            await body_fn(ctx)
        results["ctx"] = ctx

    return results


# ---------------------------------------------------------------------------
# Allow path
# ---------------------------------------------------------------------------

class TestCrudEnvelopeAllowPath:
    @pytest.mark.asyncio
    async def test_allow_body_executes(self):
        """When policy allows, the body runs."""
        body_ran = []

        async def body(ctx):
            body_ran.append(True)
            ctx.set_after({"id": "res_001", "status": "active"})

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})), \
             patch("services.ingestion.route_helpers.emit", AsyncMock()), \
             patch("services.ingestion.route_helpers.log_ingested_crud", AsyncMock()):
            await _run_envelope(body_fn=body)

        assert body_ran == [True]

    @pytest.mark.asyncio
    async def test_allow_event_emitted(self):
        """After body succeeds, emit() is called."""
        emit_calls = []

        async def mock_emit(event):
            emit_calls.append(event)

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})), \
             patch("services.ingestion.route_helpers.emit", mock_emit), \
             patch("services.ingestion.route_helpers.log_ingested_crud", AsyncMock()):
            await _run_envelope()

        assert len(emit_calls) == 1

    @pytest.mark.asyncio
    async def test_allow_audit_written(self):
        """After body succeeds, log_ingested_crud() is called."""
        audit_calls = []

        async def mock_audit(**kwargs):
            audit_calls.append(kwargs)

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})), \
             patch("services.ingestion.route_helpers.emit", AsyncMock()), \
             patch("services.ingestion.route_helpers.log_ingested_crud", mock_audit):
            await _run_envelope()

        assert len(audit_calls) == 1


# ---------------------------------------------------------------------------
# Deny path
# ---------------------------------------------------------------------------

class TestCrudEnvelopeDenyPath:
    @pytest.mark.asyncio
    async def test_deny_raises_403(self):
        """Policy deny → HTTPException(403)."""
        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "deny", "reason": "blocked by policy"})):
            with pytest.raises(HTTPException) as exc_info:
                await _run_envelope()
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_deny_reason_in_exception_detail(self):
        """Policy deny reason is passed to HTTPException detail."""
        reason = "Cannot update locked record"
        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "deny", "reason": reason})):
            with pytest.raises(HTTPException) as exc_info:
                await _run_envelope()
        assert reason in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_deny_body_not_executed(self):
        """On deny, the body must not run."""
        body_ran = []

        async def body(ctx):
            body_ran.append(True)

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "deny", "reason": "blocked"})):
            with pytest.raises(HTTPException):
                await _run_envelope(body_fn=body)

        assert body_ran == []

    @pytest.mark.asyncio
    async def test_deny_no_event_emitted(self):
        """On deny, emit() must not be called."""
        emit_calls = []

        async def mock_emit(event):
            emit_calls.append(event)

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "deny", "reason": "blocked"})), \
             patch("services.ingestion.route_helpers.emit", mock_emit):
            with pytest.raises(HTTPException):
                await _run_envelope()

        assert emit_calls == []

    @pytest.mark.asyncio
    async def test_deny_no_audit_written(self):
        """On deny, audit must not be written."""
        audit_calls = []

        async def mock_audit(**kwargs):
            audit_calls.append(kwargs)

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "deny", "reason": "blocked"})), \
             patch("services.ingestion.route_helpers.log_ingested_crud", mock_audit):
            with pytest.raises(HTTPException):
                await _run_envelope()

        assert audit_calls == []


# ---------------------------------------------------------------------------
# Policy engine raises — fail-closed (503)
# ---------------------------------------------------------------------------

class TestCrudEnvelopeFailClosed:
    @pytest.mark.asyncio
    async def test_policy_engine_raises_returns_503(self):
        """Unexpected policy engine exception → HTTPException(503)."""
        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(side_effect=RuntimeError("DB timeout"))):
            with pytest.raises(HTTPException) as exc_info:
                await _run_envelope()
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_policy_engine_raises_body_not_executed(self):
        """On 503, body must not run."""
        body_ran = []

        async def body(ctx):
            body_ran.append(True)

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(side_effect=ConnectionError("mongo down"))):
            with pytest.raises(HTTPException):
                await _run_envelope(body_fn=body)

        assert body_ran == []


# ---------------------------------------------------------------------------
# Body raises after policy allow
# ---------------------------------------------------------------------------

class TestCrudEnvelopeBodyRaises:
    @pytest.mark.asyncio
    async def test_body_exception_propagates(self):
        """If body raises, the exception escapes the envelope."""
        async def body(ctx):
            raise ValueError("body error")

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})):
            with pytest.raises(ValueError, match="body error"):
                await _run_envelope(body_fn=body)

    @pytest.mark.asyncio
    async def test_body_exception_no_event_emitted(self):
        """If body raises, emit() must not be called."""
        emit_calls = []

        async def mock_emit(event):
            emit_calls.append(event)

        async def body(ctx):
            raise RuntimeError("body failed")

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})), \
             patch("services.ingestion.route_helpers.emit", mock_emit):
            with pytest.raises(RuntimeError):
                await _run_envelope(body_fn=body)

        assert emit_calls == []

    @pytest.mark.asyncio
    async def test_body_exception_no_audit_written(self):
        """If body raises, audit must not be written."""
        audit_calls = []

        async def mock_audit(**kwargs):
            audit_calls.append(kwargs)

        async def body(ctx):
            raise RuntimeError("body failed")

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})), \
             patch("services.ingestion.route_helpers.log_ingested_crud", mock_audit):
            with pytest.raises(RuntimeError):
                await _run_envelope(body_fn=body)

        assert audit_calls == []


# ---------------------------------------------------------------------------
# Post-success bus/audit failures are swallowed
# ---------------------------------------------------------------------------

class TestPostSuccessFailureHandling:
    @pytest.mark.asyncio
    async def test_emit_failure_does_not_propagate(self):
        """If emit() raises after successful body, the envelope must not re-raise."""
        async def failing_emit(event):
            raise RuntimeError("bus down")

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})), \
             patch("services.ingestion.route_helpers.emit", failing_emit), \
             patch("services.ingestion.route_helpers.log_ingested_crud", AsyncMock()):
            # Should not raise
            await _run_envelope()

    @pytest.mark.asyncio
    async def test_emit_failure_audit_still_written(self):
        """Even when emit() fails, audit must still be written."""
        audit_calls = []

        async def failing_emit(event):
            raise RuntimeError("bus down")

        async def mock_audit(**kwargs):
            audit_calls.append(kwargs)

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})), \
             patch("services.ingestion.route_helpers.emit", failing_emit), \
             patch("services.ingestion.route_helpers.log_ingested_crud", mock_audit):
            await _run_envelope()

        assert len(audit_calls) == 1

    @pytest.mark.asyncio
    async def test_audit_failure_does_not_propagate(self):
        """If audit write fails after successful body, must not raise."""
        async def failing_audit(**kwargs):
            raise RuntimeError("audit db down")

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})), \
             patch("services.ingestion.route_helpers.emit", AsyncMock()), \
             patch("services.ingestion.route_helpers.log_ingested_crud", failing_audit):
            await _run_envelope()  # must not raise

    @pytest.mark.asyncio
    async def test_audit_failure_ctx_return_value_unchanged(self):
        """Audit failure doesn't corrupt the ctx returned by the envelope."""
        async def failing_audit(**kwargs):
            raise RuntimeError("audit db down")

        async def body(ctx):
            ctx.set_after({"id": "res_001"})

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})), \
             patch("services.ingestion.route_helpers.emit", AsyncMock()), \
             patch("services.ingestion.route_helpers.log_ingested_crud", failing_audit):
            result = await _run_envelope(body_fn=body)

        assert result["ctx"].after == {"id": "res_001"}


# ---------------------------------------------------------------------------
# ctx methods
# ---------------------------------------------------------------------------

class TestCrudContext:
    @pytest.mark.asyncio
    async def test_ctx_set_after_is_reflected_in_event(self):
        """ctx.set_after() populates event payload."""
        emitted = []

        async def cap_emit(event):
            emitted.append(event)

        async def body(ctx):
            ctx.set_after({"status": "active", "name": "Test"})

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})), \
             patch("services.ingestion.route_helpers.emit", cap_emit), \
             patch("services.ingestion.route_helpers.log_ingested_crud", AsyncMock()):
            await _run_envelope(body_fn=body)

        assert emitted[0]["payload"]["status"] == "active"

    @pytest.mark.asyncio
    async def test_ctx_set_resource_id_overrides_initial(self):
        """ctx.set_resource_id() updates resource_id used in event and audit."""
        emitted = []
        audited = []

        async def cap_emit(event):
            emitted.append(event)

        async def cap_audit(**kwargs):
            audited.append(kwargs)

        async def body(ctx):
            ctx.set_resource_id("new_res_999")

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})), \
             patch("services.ingestion.route_helpers.emit", cap_emit), \
             patch("services.ingestion.route_helpers.log_ingested_crud", cap_audit):
            await _run_envelope(resource_id="old_res_001", body_fn=body)

        assert emitted[0]["resource_id"] == "new_res_999"
        assert audited[0]["resource_id"] == "new_res_999"

    @pytest.mark.asyncio
    async def test_ctx_skip_audit_prevents_audit_write(self):
        """ctx.skip_audit() suppresses audit, but event is still emitted."""
        audit_calls = []
        emit_calls = []

        async def cap_audit(**kwargs):
            audit_calls.append(kwargs)

        async def cap_emit(event):
            emit_calls.append(event)

        async def body(ctx):
            ctx.skip_audit()

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})), \
             patch("services.ingestion.route_helpers.emit", cap_emit), \
             patch("services.ingestion.route_helpers.log_ingested_crud", cap_audit):
            await _run_envelope(body_fn=body)

        assert audit_calls == []
        assert len(emit_calls) == 1  # event still fires


# ---------------------------------------------------------------------------
# request_id derivation and sharing
# ---------------------------------------------------------------------------

class TestRequestIdDerivation:
    @pytest.mark.asyncio
    async def test_request_id_from_request_state(self):
        """If request.state.request_id is set, it's used for the event."""
        req = _make_request(request_id="req_preset_abc")
        emitted = []

        async def cap_emit(event):
            emitted.append(event)

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})), \
             patch("services.ingestion.route_helpers.emit", cap_emit), \
             patch("services.ingestion.route_helpers.log_ingested_crud", AsyncMock()):
            await _run_envelope(request=req)

        assert emitted[0]["request_id"] == "req_preset_abc"

    @pytest.mark.asyncio
    async def test_request_id_generated_when_absent(self):
        """If no request is passed, a request_id is generated and used."""
        emitted = []

        async def cap_emit(event):
            emitted.append(event)

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})), \
             patch("services.ingestion.route_helpers.emit", cap_emit), \
             patch("services.ingestion.route_helpers.log_ingested_crud", AsyncMock()):
            await _run_envelope(request=None)

        assert emitted[0]["request_id"]  # non-empty

    @pytest.mark.asyncio
    async def test_two_envelopes_share_request_id(self):
        """Two envelopes on the same request share the same request_id."""
        from services.ingestion.route_helpers import crud_envelope

        req = _make_request()  # no pre-set request_id; first envelope generates one
        emitted = []

        async def cap_emit(event):
            emitted.append(event)

        user = _make_user()
        db = MagicMock()

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})), \
             patch("services.ingestion.route_helpers.emit", cap_emit), \
             patch("services.ingestion.route_helpers.log_ingested_crud", AsyncMock()):
            async with crud_envelope(
                module="crm", entity="contact", action="updated",
                resource_id="res_001", current_user=user, db=db, request=req
            ) as ctx1:
                pass

            async with crud_envelope(
                module="crm", entity="contact", action="status_changed",
                resource_id="res_001", current_user=user, db=db, request=req
            ) as ctx2:
                pass

        assert len(emitted) == 2
        assert emitted[0]["request_id"] == emitted[1]["request_id"]

    @pytest.mark.asyncio
    async def test_two_envelopes_both_emit_and_audit(self):
        """Two envelopes (update + status_changed) each emit and each audit."""
        from services.ingestion.route_helpers import crud_envelope

        req = _make_request(request_id="req_shared_xyz")
        emitted = []
        audited = []

        async def cap_emit(event):
            emitted.append(event)

        async def cap_audit(**kwargs):
            audited.append(kwargs)

        user = _make_user()
        db = MagicMock()

        with patch("services.ingestion.route_helpers.evaluate_for_event",
                   AsyncMock(return_value={"decision": "allow"})), \
             patch("services.ingestion.route_helpers.emit", cap_emit), \
             patch("services.ingestion.route_helpers.log_ingested_crud", cap_audit):
            async with crud_envelope(
                module="crm", entity="contact", action="updated",
                resource_id="res_001", current_user=user, db=db, request=req
            ) as ctx:
                pass

            async with crud_envelope(
                module="crm", entity="contact", action="status_changed",
                resource_id="res_001", current_user=user, db=db, request=req
            ) as ctx:
                pass

        assert len(emitted) == 2
        assert len(audited) == 2
        assert emitted[0]["request_id"] == "req_shared_xyz"
        assert emitted[1]["request_id"] == "req_shared_xyz"
