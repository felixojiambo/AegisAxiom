"""
Tests for ingestion audit unification (Wave 2B + 2C).

Covers:
  - repository.log_stage writes to BOTH app_ingestion_log AND audit_logs
  - audit_logs entry has event_category="security", event_type="ingestion_stage"
  - Failed audit_logs write does NOT prevent app_ingestion_log write
  - service._register_policy_targets: idempotent (re-registering same tuple is no-op)
  - service._deactivate_policy_targets: marks all rows for an app is_active=False
  - Re-ingest sequence: deactivate → register → only current catalogue active
  - Stale targets remain is_active=False
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_update_result(modified_count=1):
    r = MagicMock()
    r.modified_count = modified_count
    return r


def _make_mock_db():
    """Build a minimal mock DB with tracked calls."""
    db = MagicMock()
    db.app_ingestion_log = MagicMock()
    db.app_ingestion_log.insert_one = AsyncMock()
    db.audit_logs = MagicMock()
    db.audit_logs.insert_one = AsyncMock()
    db.users = MagicMock()
    db.users.find_one = AsyncMock(return_value=None)
    db.policy_targets = MagicMock()
    db.policy_targets.update_one = AsyncMock(return_value=_make_update_result(1))
    db.policy_targets.update_many = AsyncMock(return_value=_make_update_result(3))
    return db


# ---------------------------------------------------------------------------
# log_stage dual-write
# ---------------------------------------------------------------------------

class TestLogStageDualWrite:
    @pytest.mark.asyncio
    async def test_log_stage_writes_to_app_ingestion_log(self):
        """log_stage must write a record to app_ingestion_log."""
        from services.ingestion.models import PipelineStage

        mock_db = _make_mock_db()

        with patch("services.ingestion.repository.get_db", return_value=mock_db):
            await __import__("services.ingestion.repository", fromlist=["log_stage"]).log_stage(
                app_id="app_001",
                stage=PipelineStage.ANALYZE,
                action="analysis_complete",
                status="success",
            )

        mock_db.app_ingestion_log.insert_one.assert_awaited()

    @pytest.mark.asyncio
    async def test_log_stage_writes_to_audit_logs(self):
        """log_stage must also write a record to audit_logs."""
        from services.ingestion.models import PipelineStage
        import services.ingestion.repository as repo

        mock_db = _make_mock_db()

        with patch("services.ingestion.repository.get_db", return_value=mock_db):
            await repo.log_stage(
                app_id="app_001",
                stage=PipelineStage.ANALYZE,
                action="analysis_complete",
                status="success",
            )

        mock_db.audit_logs.insert_one.assert_awaited()

    @pytest.mark.asyncio
    async def test_audit_log_entry_has_security_category(self):
        """The audit_logs entry must have event_category='security'."""
        from services.ingestion.models import PipelineStage
        import services.ingestion.repository as repo

        inserted_docs = []

        async def cap_insert(doc):
            inserted_docs.append(doc)

        mock_db = _make_mock_db()
        mock_db.audit_logs.insert_one = cap_insert

        with patch("services.ingestion.repository.get_db", return_value=mock_db):
            await repo.log_stage(
                app_id="app_001",
                stage=PipelineStage.CLONE,
                action="repo_cloned",
            )

        assert inserted_docs, "audit_logs.insert_one was never called"
        assert inserted_docs[0]["event_category"] == "security"

    @pytest.mark.asyncio
    async def test_audit_log_entry_has_ingestion_stage_type(self):
        """The audit_logs entry must have event_type='ingestion_stage'."""
        from services.ingestion.models import PipelineStage
        import services.ingestion.repository as repo

        inserted_docs = []

        async def cap_insert(doc):
            inserted_docs.append(doc)

        mock_db = _make_mock_db()
        mock_db.audit_logs.insert_one = cap_insert

        with patch("services.ingestion.repository.get_db", return_value=mock_db):
            await repo.log_stage(
                app_id="app_001",
                stage=PipelineStage.REGISTER_FIELDS,
                action="field_registration_complete",
            )

        assert inserted_docs[0]["event_type"] == "ingestion_stage"


# ---------------------------------------------------------------------------
# Graceful degradation: failed audit_logs write does not block pipeline log
# ---------------------------------------------------------------------------

class TestAuditLogGracefulDegradation:
    @pytest.mark.asyncio
    async def test_failed_audit_logs_write_does_not_prevent_pipeline_log(self):
        """If audit_logs insert raises, app_ingestion_log write must still succeed."""
        from services.ingestion.models import PipelineStage
        import services.ingestion.repository as repo

        pipeline_writes = []

        async def cap_pipeline(doc):
            pipeline_writes.append(doc)

        async def failing_audit(doc):
            raise RuntimeError("audit db unavailable")

        mock_db = _make_mock_db()
        mock_db.app_ingestion_log.insert_one = cap_pipeline
        mock_db.audit_logs.insert_one = failing_audit

        with patch("services.ingestion.repository.get_db", return_value=mock_db):
            # Should not raise despite audit_logs failure
            await repo.log_stage(
                app_id="app_001",
                stage=PipelineStage.ANALYZE,
                action="analysis_complete",
            )

        assert len(pipeline_writes) == 1, (
            "app_ingestion_log was not written even though it should survive audit failure"
        )

    @pytest.mark.asyncio
    async def test_failed_audit_logs_write_returns_log_id(self):
        """Even with audit failure, log_stage must return a valid log_id."""
        from services.ingestion.models import PipelineStage
        import services.ingestion.repository as repo

        async def failing_audit(doc):
            raise RuntimeError("audit db unavailable")

        mock_db = _make_mock_db()
        mock_db.audit_logs.insert_one = failing_audit

        with patch("services.ingestion.repository.get_db", return_value=mock_db):
            log_id = await repo.log_stage(
                app_id="app_001",
                stage=PipelineStage.INVOKE,
                action="ingestion_initiated",
            )

        assert log_id  # non-empty string


# ---------------------------------------------------------------------------
# _register_policy_targets — idempotency
# ---------------------------------------------------------------------------

class TestRegisterPolicyTargetsIdempotent:
    @pytest.mark.asyncio
    async def test_register_same_tuple_twice_is_idempotent(self):
        """Re-registering the same (app_id, module, entity, event) uses upsert."""
        from services.ingestion.service import _register_policy_targets

        upsert_calls = []

        async def cap_update_one(query, update, upsert=False):
            upsert_calls.append({"query": query, "upsert": upsert})
            return _make_update_result(0)

        mock_db = MagicMock()
        mock_db.policy_targets.update_one = cap_update_one

        await _register_policy_targets(
            mock_db, "app_slug", "myapp", "task", ["created"]
        )
        await _register_policy_targets(
            mock_db, "app_slug", "myapp", "task", ["created"]
        )

        # Both calls use upsert=True (idempotent)
        assert all(c["upsert"] for c in upsert_calls)

    @pytest.mark.asyncio
    async def test_register_activates_target(self):
        """Registered target must have is_active=True in the $set."""
        from services.ingestion.service import _register_policy_targets

        set_docs = []

        async def cap_update_one(query, update, upsert=False):
            set_docs.append(update.get("$set", {}))
            return _make_update_result(1)

        mock_db = MagicMock()
        mock_db.policy_targets.update_one = cap_update_one

        await _register_policy_targets(
            mock_db, "app_slug", "myapp", "task", ["created"]
        )

        assert set_docs[0]["is_active"] is True

    @pytest.mark.asyncio
    async def test_register_multiple_events_calls_upsert_per_event(self):
        """Each event in the list gets its own upsert call."""
        from services.ingestion.service import _register_policy_targets

        upsert_calls = []

        async def cap_update_one(query, update, upsert=False):
            upsert_calls.append(query["event"])
            return _make_update_result(1)

        mock_db = MagicMock()
        mock_db.policy_targets.update_one = cap_update_one

        events = ["created", "updated", "deleted", "status_changed"]
        await _register_policy_targets(
            mock_db, "app_slug", "myapp", "task", events
        )

        assert set(upsert_calls) == set(events)
        assert len(upsert_calls) == len(events)


# ---------------------------------------------------------------------------
# _deactivate_policy_targets
# ---------------------------------------------------------------------------

class TestDeactivatePolicyTargets:
    @pytest.mark.asyncio
    async def test_deactivate_marks_all_app_rows_inactive(self):
        """_deactivate_policy_targets must set is_active=False for the app."""
        from services.ingestion.service import _deactivate_policy_targets

        update_calls = []

        async def cap_update_many(query, update):
            update_calls.append({"query": query, "update": update})
            return _make_update_result(5)

        mock_db = MagicMock()
        mock_db.policy_targets.update_many = cap_update_many

        count = await _deactivate_policy_targets(mock_db, "app_slug_x")

        assert len(update_calls) == 1
        assert update_calls[0]["query"]["app_id"] == "app_slug_x"
        assert update_calls[0]["update"]["$set"]["is_active"] is False

    @pytest.mark.asyncio
    async def test_deactivate_returns_modified_count(self):
        """Return value must be the modified_count from update_many."""
        from services.ingestion.service import _deactivate_policy_targets

        async def cap_update_many(query, update):
            return _make_update_result(7)

        mock_db = MagicMock()
        mock_db.policy_targets.update_many = cap_update_many

        count = await _deactivate_policy_targets(mock_db, "some_app")
        assert count == 7

    @pytest.mark.asyncio
    async def test_deactivate_only_targets_active_rows(self):
        """Query must include is_active=True to avoid touching already-deactivated rows."""
        from services.ingestion.service import _deactivate_policy_targets

        queries = []

        async def cap_update_many(query, update):
            queries.append(query)
            return _make_update_result(0)

        mock_db = MagicMock()
        mock_db.policy_targets.update_many = cap_update_many

        await _deactivate_policy_targets(mock_db, "app_slug")

        assert queries[0].get("is_active") is True


# ---------------------------------------------------------------------------
# Re-ingest sequence
# ---------------------------------------------------------------------------

class TestReIngestSequence:
    @pytest.mark.asyncio
    async def test_reingest_deactivates_then_registers(self):
        """Re-ingest must deactivate existing targets before registering new ones."""
        from services.ingestion.service import _deactivate_policy_targets, _register_policy_targets

        call_order = []

        async def cap_deactivate(query, update):
            call_order.append("deactivate")
            return _make_update_result(3)

        async def cap_register(query, update, upsert=False):
            call_order.append("register")
            return _make_update_result(1)

        mock_db = MagicMock()
        mock_db.policy_targets.update_many = cap_deactivate
        mock_db.policy_targets.update_one = cap_register

        await _deactivate_policy_targets(mock_db, "app_slug")
        await _register_policy_targets(mock_db, "app_slug", "myapp", "task", ["created"])

        assert call_order.index("deactivate") < call_order.index("register")

    @pytest.mark.asyncio
    async def test_current_catalogue_targets_are_active_after_reingest(self):
        """After deactivate + register, the newly registered targets must be is_active=True."""
        from services.ingestion.service import _register_policy_targets

        set_calls = []

        async def cap_update_one(query, update, upsert=False):
            set_calls.append(update.get("$set", {}))
            return _make_update_result(1)

        mock_db = MagicMock()
        mock_db.policy_targets.update_one = cap_update_one

        await _register_policy_targets(
            mock_db, "app_slug", "myapp", "task", ["created", "updated"]
        )

        for doc in set_calls:
            assert doc["is_active"] is True

    @pytest.mark.asyncio
    async def test_stale_targets_remain_inactive(self):
        """Stale entries (not re-registered) should stay is_active=False after reingest.

        We test this by verifying that _deactivate does NOT flip them back and
        _register only touches the events in the current catalogue.
        """
        from services.ingestion.service import _deactivate_policy_targets, _register_policy_targets

        # Track which events were re-registered
        registered_events = []

        async def cap_update_many(query, update):
            return _make_update_result(4)

        async def cap_update_one(query, update, upsert=False):
            registered_events.append(query.get("event"))
            return _make_update_result(1)

        mock_db = MagicMock()
        mock_db.policy_targets.update_many = cap_update_many
        mock_db.policy_targets.update_one = cap_update_one

        # Simulate: old catalogue had "created", "updated", "deleted", "status_changed"
        # New catalogue only has "created", "updated" (status field removed)
        new_events = ["created", "updated"]

        await _deactivate_policy_targets(mock_db, "app_slug")
        await _register_policy_targets(
            mock_db, "app_slug", "myapp", "task", new_events
        )

        # Only new events are re-registered
        assert set(registered_events) == set(new_events)
        # "deleted" and "status_changed" were NOT re-registered — they remain deactivated
        assert "deleted" not in registered_events
        assert "status_changed" not in registered_events
