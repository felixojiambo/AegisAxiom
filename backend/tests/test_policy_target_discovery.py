"""
Tests for policy target discovery (services/policy_engine.py).

Covers:
  - evaluate_for_event allow path: no policies → decision="allow"
  - evaluate_for_event deny path: block policy → decision="deny" with reason
  - evaluate_for_event re-raises on _get_policies exception (fail-closed)
  - list_targets_for_admin_ui returns native targets from field_registry
  - list_targets_for_admin_ui returns ingested targets from policy_targets collection
  - list_targets_for_admin_ui merges without duplicates
  - list_targets_for_admin_ui tolerant of missing policy_targets collection
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(module="crm", entity="contact", action="created", **extra):
    e = {
        "name": f"{module}.{entity}.{action}",
        "module": module,
        "entity": entity,
        "action": action,
        "resource_id": "res_001",
        "actor_id": "user_001",
        "actor_email": "u@example.com",
        "payload": {},
        "previous_state": None,
        "request_id": "req_test_001",
    }
    e.update(extra)
    return e


def _make_block_policy(module="crm", entity="contact", event="created", reason="blocked by policy"):
    return {
        "module": module,
        "entity": entity,
        "trigger": {"event": event},
        "enabled": True,
        "deleted_at": None,
        "conditions": [],  # unconditional
        "condition_logic": "AND",
        "actions": [{"type": "block", "message": reason}],
        "priority": 10,
        "policy_id": "pol_001",
    }


def _make_async_list(docs):
    """Mimic a Motor async cursor's to_list."""
    class AsyncCursor:
        def sort(self, *a, **kw):
            return self

        async def to_list(self, length=None):
            return list(docs)

    return AsyncCursor()


# ---------------------------------------------------------------------------
# evaluate_for_event — allow path (no policies)
# ---------------------------------------------------------------------------

class TestEvaluateForEventAllowPath:
    @pytest.mark.asyncio
    async def test_no_policies_returns_allow(self):
        """When no policies are registered, decision is allow."""
        import services.policy_engine as pe
        pe.invalidate_policy_cache()

        mock_db = MagicMock()
        mock_db.policies.find.return_value = _make_async_list([])

        result = await pe.evaluate_for_event(_make_event(), mock_db)

        assert result["decision"] == "allow"
        assert result["reason"] is None

    @pytest.mark.asyncio
    async def test_missing_module_returns_allow(self):
        """Missing module key → fast-path allow."""
        import services.policy_engine as pe
        pe.invalidate_policy_cache()

        mock_db = MagicMock()
        mock_db.policies.find.return_value = _make_async_list([])

        event = _make_event()
        del event["module"]
        result = await pe.evaluate_for_event(event, mock_db)

        assert result["decision"] == "allow"

    @pytest.mark.asyncio
    async def test_policies_for_different_module_returns_allow(self):
        """Policies for a different module do not block the current event."""
        import services.policy_engine as pe
        pe.invalidate_policy_cache()

        other_policy = _make_block_policy(module="finance", entity="invoice", event="created")
        mock_db = MagicMock()
        mock_db.policies.find.return_value = _make_async_list([other_policy])

        result = await pe.evaluate_for_event(_make_event(module="crm"), mock_db)

        assert result["decision"] == "allow"


# ---------------------------------------------------------------------------
# evaluate_for_event — deny path
# ---------------------------------------------------------------------------

class TestEvaluateForEventDenyPath:
    @pytest.mark.asyncio
    async def test_block_policy_returns_deny(self):
        """A matching block policy yields decision='deny'."""
        import services.policy_engine as pe
        pe.invalidate_policy_cache()

        block = _make_block_policy(reason="record is locked")
        mock_db = MagicMock()
        mock_db.policies.find.return_value = _make_async_list([block])

        result = await pe.evaluate_for_event(_make_event(), mock_db)

        assert result["decision"] == "deny"

    @pytest.mark.asyncio
    async def test_deny_includes_reason(self):
        """Deny result includes the block message as reason."""
        import services.policy_engine as pe
        pe.invalidate_policy_cache()

        reason_text = "Cannot create while billing is suspended"
        block = _make_block_policy(reason=reason_text)
        mock_db = MagicMock()
        mock_db.policies.find.return_value = _make_async_list([block])

        result = await pe.evaluate_for_event(_make_event(), mock_db)

        assert result["reason"] == reason_text


# ---------------------------------------------------------------------------
# evaluate_for_event — re-raises on _get_policies exception (fail-closed)
# ---------------------------------------------------------------------------

class TestEvaluateForEventFailClosed:
    @pytest.mark.asyncio
    async def test_get_policies_exception_propagates(self):
        """If _get_policies raises, evaluate_for_event must re-raise (not allow)."""
        import services.policy_engine as pe
        pe.invalidate_policy_cache()

        mock_db = MagicMock()
        mock_db.policies.find.side_effect = ConnectionError("mongo down")

        with pytest.raises(Exception):
            await pe.evaluate_for_event(_make_event(), mock_db)

    @pytest.mark.asyncio
    async def test_get_policies_exception_does_not_silently_allow(self):
        """Ensure we do NOT get a {'decision': 'allow'} when DB is down."""
        import services.policy_engine as pe
        pe.invalidate_policy_cache()

        mock_db = MagicMock()
        mock_db.policies.find.side_effect = RuntimeError("timeout")

        raised = False
        try:
            result = await pe.evaluate_for_event(_make_event(), mock_db)
            # If we somehow got here, it must NOT be allow
            assert result.get("decision") != "allow", (
                "evaluate_for_event silently allowed when _get_policies raised — "
                "this is a security regression"
            )
        except Exception:
            raised = True

        assert raised, "evaluate_for_event should re-raise on _get_policies failure"


# ---------------------------------------------------------------------------
# list_targets_for_admin_ui — native targets
# ---------------------------------------------------------------------------

class TestListTargetsNative:
    @pytest.mark.asyncio
    async def test_returns_native_targets_from_field_registry(self):
        """Native modules from field_registry appear in the output."""
        import services.policy_engine as pe

        native_entries = [
            {"module": "crm", "entity": "contact", "events": ["created", "updated"], "fields": []},
        ]

        mock_db = MagicMock()
        mock_db.list_collection_names = AsyncMock(return_value=[])

        with patch("services.field_registry.list_modules_and_entities",
                   return_value=native_entries):
            targets = await pe.list_targets_for_admin_ui(mock_db)

        assert any(
            t["module"] == "crm" and t["entity"] == "contact" and t["source"] == "native"
            for t in targets
        )

    @pytest.mark.asyncio
    async def test_native_target_has_required_keys(self):
        """Each native target dict has module, entity, events, source, label."""
        import services.policy_engine as pe

        native_entries = [
            {"module": "time_tracking", "entity": "time_entry",
             "events": ["on_create"], "fields": []},
        ]

        mock_db = MagicMock()
        mock_db.list_collection_names = AsyncMock(return_value=[])

        with patch("services.field_registry.list_modules_and_entities",
                   return_value=native_entries):
            targets = await pe.list_targets_for_admin_ui(mock_db)

        native = [t for t in targets if t["source"] == "native"]
        assert native
        for t in native:
            assert "module" in t
            assert "entity" in t
            assert "events" in t
            assert "source" in t
            assert "label" in t


# ---------------------------------------------------------------------------
# list_targets_for_admin_ui — ingested targets
# ---------------------------------------------------------------------------

class TestListTargetsIngested:
    @pytest.mark.asyncio
    async def test_returns_ingested_targets_from_policy_targets_collection(self):
        """Ingested targets from policy_targets appear in the output."""
        import services.policy_engine as pe

        ingested_docs = [
            {"module": "davies_pmo", "entity": "project", "event": "created"},
            {"module": "davies_pmo", "entity": "project", "event": "updated"},
        ]

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=ingested_docs)

        mock_db = MagicMock()
        mock_db.list_collection_names = AsyncMock(return_value=["policy_targets"])
        mock_db.policy_targets.find.return_value = mock_cursor

        with patch("services.field_registry.list_modules_and_entities", return_value=[]):
            targets = await pe.list_targets_for_admin_ui(mock_db)

        ingested = [t for t in targets if t["source"] == "ingestion"]
        assert len(ingested) == 1  # collapsed to one row
        assert ingested[0]["module"] == "davies_pmo"
        assert ingested[0]["entity"] == "project"
        assert set(ingested[0]["events"]) == {"created", "updated"}

    @pytest.mark.asyncio
    async def test_ingested_target_has_source_ingestion(self):
        """Ingested targets carry source='ingestion'."""
        import services.policy_engine as pe

        ingested_docs = [
            {"module": "myapp", "entity": "task", "event": "created"},
        ]

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=ingested_docs)

        mock_db = MagicMock()
        mock_db.list_collection_names = AsyncMock(return_value=["policy_targets"])
        mock_db.policy_targets.find.return_value = mock_cursor

        with patch("services.field_registry.list_modules_and_entities", return_value=[]):
            targets = await pe.list_targets_for_admin_ui(mock_db)

        assert all(t["source"] == "ingestion" for t in targets if t["entity"] == "task")


# ---------------------------------------------------------------------------
# list_targets_for_admin_ui — no duplicates
# ---------------------------------------------------------------------------

class TestListTargetsNoDuplicates:
    @pytest.mark.asyncio
    async def test_no_duplicate_targets_when_native_and_ingested_overlap(self):
        """If a module/entity appears in both native and ingested, it should
        not be deduplicated at the merge level — they are distinct source rows.
        But within each source, no internal duplicates."""
        import services.policy_engine as pe

        native_entries = [
            {"module": "crm", "entity": "contact", "events": ["created"], "fields": []},
        ]
        ingested_docs = [
            {"module": "crm", "entity": "contact", "event": "updated"},
        ]

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=ingested_docs)

        mock_db = MagicMock()
        mock_db.list_collection_names = AsyncMock(return_value=["policy_targets"])
        mock_db.policy_targets.find.return_value = mock_cursor

        with patch("services.field_registry.list_modules_and_entities",
                   return_value=native_entries):
            targets = await pe.list_targets_for_admin_ui(mock_db)

        # Within each source, no duplicates
        native = [t for t in targets if t["source"] == "native"]
        ingested = [t for t in targets if t["source"] == "ingestion"]

        native_keys = [(t["module"], t["entity"]) for t in native]
        ingested_keys = [(t["module"], t["entity"]) for t in ingested]

        assert len(native_keys) == len(set(native_keys)), "Duplicate native targets"
        assert len(ingested_keys) == len(set(ingested_keys)), "Duplicate ingested targets"

    @pytest.mark.asyncio
    async def test_ingested_events_deduplicated_within_entity(self):
        """Multiple docs for same (module, entity, event) collapse to one event entry."""
        import services.policy_engine as pe

        ingested_docs = [
            {"module": "myapp", "entity": "task", "event": "created"},
            {"module": "myapp", "entity": "task", "event": "created"},  # duplicate
            {"module": "myapp", "entity": "task", "event": "updated"},
        ]

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=ingested_docs)

        mock_db = MagicMock()
        mock_db.list_collection_names = AsyncMock(return_value=["policy_targets"])
        mock_db.policy_targets.find.return_value = mock_cursor

        with patch("services.field_registry.list_modules_and_entities", return_value=[]):
            targets = await pe.list_targets_for_admin_ui(mock_db)

        task_targets = [t for t in targets if t["entity"] == "task"]
        assert len(task_targets) == 1
        assert task_targets[0]["events"].count("created") == 1  # deduplicated


# ---------------------------------------------------------------------------
# list_targets_for_admin_ui — missing policy_targets collection
# ---------------------------------------------------------------------------

class TestListTargetsMissingCollection:
    @pytest.mark.asyncio
    async def test_missing_collection_returns_native_only(self):
        """If policy_targets collection doesn't exist, return native targets only."""
        import services.policy_engine as pe

        native_entries = [
            {"module": "crm", "entity": "contact", "events": ["created"], "fields": []},
        ]

        mock_db = MagicMock()
        mock_db.list_collection_names = AsyncMock(return_value=[])  # no policy_targets

        with patch("services.field_registry.list_modules_and_entities",
                   return_value=native_entries):
            targets = await pe.list_targets_for_admin_ui(mock_db)

        assert any(t["source"] == "native" for t in targets)
        assert not any(t["source"] == "ingestion" for t in targets)

    @pytest.mark.asyncio
    async def test_ingestion_lookup_failure_does_not_raise(self):
        """If the ingested lookup raises (e.g. collection error), return natives anyway."""
        import services.policy_engine as pe

        native_entries = [
            {"module": "crm", "entity": "contact", "events": ["created"], "fields": []},
        ]

        mock_db = MagicMock()
        mock_db.list_collection_names = AsyncMock(side_effect=RuntimeError("db error"))

        with patch("services.field_registry.list_modules_and_entities",
                   return_value=native_entries):
            targets = await pe.list_targets_for_admin_ui(mock_db)

        # Should not raise and should return native targets
        assert any(t["source"] == "native" for t in targets)
