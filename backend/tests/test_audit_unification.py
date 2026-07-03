"""
Audit log schema unification tests.

Pre-PR #5, services.security_logger.log_security_event wrote into a
separate security_audit_logs collection while services.audit_service
wrote into audit_logs. The admin Audit Log UI only read audit_logs, so
every authz_failed, auth_failed, and role_changed event was invisible.

Post-unification, both writers target audit_logs and are distinguishable
by event_category. These tests pin down:
- security_logger writes go to audit_logs with event_category='security'
- security_logger normalizes its kwargs to audit_logs field names
  (user_id -> actor_id, user_email -> actor_email, resource -> resource_type)
- audit_service.log_action writes carry event_category='activity'
- both writers stamp an audit_id

The endpoint-level filter behavior is covered by manual testing against
a live backend (see PR description test plan); these are the unit-level
contracts that catch regressions in the schema.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def fake_db(monkeypatch):
    """Patch get_db() everywhere both writers use it.

    Both modules import get_db from `database` at module load. Patching
    `database.get_db` covers both call sites so we capture every insert.
    """
    db = MagicMock()
    db.audit_logs = MagicMock()
    db.audit_logs.insert_one = AsyncMock()

    import database

    monkeypatch.setattr(database, "get_db", lambda: db)
    # Also patch the names re-exported into the writer modules in case
    # they captured the function by reference at import time.
    import services.audit_service as audit_service
    import services.security_logger as security_logger

    monkeypatch.setattr(audit_service, "get_db", lambda: db)
    monkeypatch.setattr(security_logger, "get_db", lambda: db)
    return db


# ---------------------------------------------------------------------------
# security_logger.log_security_event writes into audit_logs
# ---------------------------------------------------------------------------

class TestSecurityLoggerWritesToAuditLogs:
    @pytest.mark.asyncio
    async def test_writes_to_audit_logs_collection(self, fake_db):
        from services.security_logger import log_security_event

        await log_security_event(
            event_type="authz_failed",
            severity="warning",
            user_id="u_123",
            user_email="user@example.com",
            resource="role",
            resource_id="role_admin",
            action="update",
            outcome="denied",
            details={"required_permission": "roles.manage"},
        )

        fake_db.audit_logs.insert_one.assert_awaited_once()
        entry = fake_db.audit_logs.insert_one.call_args.args[0]
        assert entry["event_category"] == "security"
        assert entry["event_type"] == "authz_failed"
        assert entry["severity"] == "warning"
        assert entry["outcome"] == "denied"

    @pytest.mark.asyncio
    async def test_normalizes_field_names_to_audit_logs_schema(self, fake_db):
        from services.security_logger import log_security_event

        await log_security_event(
            event_type="auth_failed",
            user_id="u_alice",
            user_email="alice@example.com",
            resource="auth",
            resource_id="session_abc",
            action="login",
        )
        entry = fake_db.audit_logs.insert_one.call_args.args[0]
        # user_id -> actor_id
        assert entry["actor_id"] == "u_alice"
        assert "user_id" not in entry
        # user_email -> actor_email
        assert entry["actor_email"] == "alice@example.com"
        assert "user_email" not in entry
        # resource -> resource_type
        assert entry["resource_type"] == "auth"
        assert "resource" not in entry

    @pytest.mark.asyncio
    async def test_stamps_audit_id_and_timestamp(self, fake_db):
        from services.security_logger import log_security_event

        await log_security_event(event_type="role_changed", user_id="u_1")
        entry = fake_db.audit_logs.insert_one.call_args.args[0]
        assert entry["audit_id"].startswith("audit_")
        assert entry["timestamp"] is not None

    @pytest.mark.asyncio
    async def test_default_outcome_and_severity_present(self, fake_db):
        """Defaults must serialize into the entry, not be left out."""
        from services.security_logger import log_security_event

        await log_security_event(event_type="role_changed", user_id="u_1")
        entry = fake_db.audit_logs.insert_one.call_args.args[0]
        assert entry["severity"] == "info"
        assert entry["outcome"] == "success"
        assert entry["details"] == {}


# ---------------------------------------------------------------------------
# audit_service.log_action carries event_category='activity'
# ---------------------------------------------------------------------------

class TestAuditServiceTagsActivity:
    @pytest.mark.asyncio
    async def test_log_action_writes_event_category_activity(self, fake_db):
        from services.audit_service import log_action

        await log_action(
            action="role_create",
            actor_id="u_admin",
            actor_email="admin@example.com",
            resource_type="role",
            resource_id="role_new",
            details={"name": "Custom Role"},
        )
        entry = fake_db.audit_logs.insert_one.call_args.args[0]
        assert entry["event_category"] == "activity"
        # Original audit_logs schema fields preserved as-is
        assert entry["action"] == "role_create"
        assert entry["actor_id"] == "u_admin"
        assert entry["actor_email"] == "admin@example.com"
        assert entry["resource_type"] == "role"

    @pytest.mark.asyncio
    async def test_log_action_stamps_audit_id(self, fake_db):
        from services.audit_service import log_action

        await log_action(
            action="user_invite",
            actor_id="u_admin",
            actor_email="admin@example.com",
            resource_type="user",
        )
        entry = fake_db.audit_logs.insert_one.call_args.args[0]
        assert entry["audit_id"].startswith("audit_")
