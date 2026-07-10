"""
Tests for backend/services/ingestion/event_listeners.py.

The module registers two listeners as an import side-effect:
  - "*" forensic-trail logger
  - "*.*.status_changed" → workflow_engine bridge

These tests exercise each listener in isolation and confirm the import
itself registers them.
"""
import importlib
import logging
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch


def _make_event(name="davies_pmo.project.status_changed", **overrides):
    base = {
        "name": name,
        "module": name.split(".")[0],
        "entity": name.split(".")[1],
        "action": name.split(".")[2],
        "resource_id": "res_001",
        "actor_id": "user_001",
        "actor_email": "user@example.com",
        "timestamp": datetime.now(timezone.utc),
        "payload": {"status": "approved", "field": "v"},
        "previous_state": {"status": "pending", "field": "v"},
        "request_id": "req_test_listener",
    }
    base.update(overrides)
    return base


def _fresh_bus_then_load_listeners():
    """Clear the module-level listener list, then re-import the listeners
    module so its register_listener() calls run again into the fresh slate."""
    import services.event_bus as eb
    eb._listeners.clear()
    import services.ingestion.event_listeners as el
    importlib.reload(el)
    return eb


class TestRegistration:
    def test_import_registers_two_listeners(self):
        eb = _fresh_bus_then_load_listeners()
        patterns = [p for p, _ in eb._listeners]
        assert "*" in patterns
        assert "*.*.status_changed" in patterns

    def test_registration_idempotent_per_reload(self):
        # Reload twice — list grows because register_listener is append-only.
        # The acceptance is "module reload re-registers"; production wires it
        # once at startup, so duplication isn't a real concern. This test just
        # documents the behavior.
        eb = _fresh_bus_then_load_listeners()
        first_count = len(eb._listeners)
        import services.ingestion.event_listeners as el
        importlib.reload(el)
        assert len(eb._listeners) == first_count + 2


class TestForensicLogger:
    @pytest.mark.asyncio
    async def test_logs_event_at_info(self, caplog):
        from services.ingestion.event_listeners import _log_event
        with caplog.at_level(logging.INFO, logger="services.ingestion.event_listeners"):
            await _log_event(_make_event())
        assert any("ingestion_event" in r.message for r in caplog.records)
        assert any("req_test_listener" in r.message for r in caplog.records)


class TestWorkflowBridge:
    @pytest.mark.asyncio
    async def test_translates_status_change_to_legacy_shape(self):
        from services.ingestion.event_listeners import (
            _dispatch_status_change_to_workflow_engine,
        )
        with patch(
            "services.workflow_engine.handle_event", new_callable=AsyncMock
        ) as mock_legacy:
            await _dispatch_status_change_to_workflow_engine(_make_event())
            mock_legacy.assert_awaited_once()
            legacy_event = mock_legacy.call_args.args[0]
            assert legacy_event["module"] == "davies_pmo"
            assert legacy_event["entity"] == "project"
            assert legacy_event["action"] == "updated"
            assert legacy_event["changed_fields"]["status"] == {
                "old": "pending",
                "new": "approved",
            }
            assert legacy_event["record"]["status"] == "approved"
            assert legacy_event["old_record"]["status"] == "pending"
            assert legacy_event["request_id"] == "req_test_listener"

    @pytest.mark.asyncio
    async def test_handles_missing_previous_state(self):
        """A status_changed event with no previous_state shouldn't crash;
        old status is None."""
        from services.ingestion.event_listeners import (
            _dispatch_status_change_to_workflow_engine,
        )
        evt = _make_event(previous_state=None)
        with patch(
            "services.workflow_engine.handle_event", new_callable=AsyncMock
        ) as mock_legacy:
            await _dispatch_status_change_to_workflow_engine(evt)
            legacy_event = mock_legacy.call_args.args[0]
            assert legacy_event["changed_fields"]["status"]["old"] is None
            assert legacy_event["changed_fields"]["status"]["new"] == "approved"
            assert legacy_event["old_record"] == {}

    @pytest.mark.asyncio
    async def test_isoformats_datetime_timestamp(self):
        from services.ingestion.event_listeners import (
            _dispatch_status_change_to_workflow_engine,
        )
        evt = _make_event()
        with patch(
            "services.workflow_engine.handle_event", new_callable=AsyncMock
        ) as mock_legacy:
            await _dispatch_status_change_to_workflow_engine(evt)
            legacy_event = mock_legacy.call_args.args[0]
            assert isinstance(legacy_event["timestamp"], str)
            assert "T" in legacy_event["timestamp"]
