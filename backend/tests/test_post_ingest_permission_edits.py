"""
Tests for post-ingest permission editing functions (Wave 1C + 2B).

Covers:
  - add_app_permission (services/ingestion/rbac_generator.py)
  - remove_app_permission
  - reassign_app_role_perms
  - deactivate_permissions preserves manual_override source (permission_registry.py)

All DB interactions are mocked — no live Mongo required.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, ANY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_update_result(modified_count=1):
    r = MagicMock()
    r.modified_count = modified_count
    return r


def _make_app_doc(app_id="app_test_001", app_slug="testapp"):
    return {"app_id": app_id, "app_slug": app_slug, "name": "Test App"}


def _make_perm_dict(key="testapp.entity.view", group="TestApp", label="View Entity"):
    return {"key": key, "group": group, "label": label}


# ---------------------------------------------------------------------------
# add_app_permission
# ---------------------------------------------------------------------------

class TestAddAppPermission:
    @pytest.mark.asyncio
    async def test_adds_permission_with_manual_override_source(self):
        from services.ingestion.rbac_generator import add_app_permission

        audit_writes = []
        registered = []

        app_doc = _make_app_doc()
        perm = _make_perm_dict()

        async def mock_register(perms, source, app_id):
            registered.append({"perms": perms, "source": source, "app_id": app_id})
            return len(perms)

        async def mock_log(**kwargs):
            audit_writes.append(kwargs)

        with patch("services.ingestion.rbac_generator.repo.get_app_registry", AsyncMock(return_value=app_doc)), \
             patch("services.ingestion.rbac_generator.register_permissions", mock_register), \
             patch("services.ingestion.rbac_generator.log_security_event", mock_log):
            result = await add_app_permission("app_test_001", perm, performed_by="user_admin")

        assert result["key"] == perm["key"]
        assert len(registered) == 1
        assert registered[0]["source"] == "manual_override"
        assert registered[0]["app_id"] == "app_test_001"

    @pytest.mark.asyncio
    async def test_add_permission_writes_audit_log(self):
        from services.ingestion.rbac_generator import add_app_permission

        audit_writes = []
        app_doc = _make_app_doc()
        perm = _make_perm_dict()

        async def mock_log(**kwargs):
            audit_writes.append(kwargs)

        with patch("services.ingestion.rbac_generator.repo.get_app_registry", AsyncMock(return_value=app_doc)), \
             patch("services.ingestion.rbac_generator.register_permissions", AsyncMock(return_value=1)), \
             patch("services.ingestion.rbac_generator.log_security_event", mock_log):
            await add_app_permission("app_test_001", perm, performed_by="user_admin")

        assert len(audit_writes) == 1
        assert audit_writes[0]["event_type"] == "app_permission_edited"
        assert audit_writes[0]["user_id"] == "user_admin"

    @pytest.mark.asyncio
    async def test_add_permission_missing_app_raises_value_error(self):
        from services.ingestion.rbac_generator import add_app_permission

        with patch("services.ingestion.rbac_generator.repo.get_app_registry", AsyncMock(return_value=None)):
            with pytest.raises(ValueError, match="not found"):
                await add_app_permission("bad_app_id", _make_perm_dict(), performed_by="u")

    @pytest.mark.asyncio
    async def test_add_permission_missing_key_raises_value_error(self):
        from services.ingestion.rbac_generator import add_app_permission

        app_doc = _make_app_doc()

        with patch("services.ingestion.rbac_generator.repo.get_app_registry", AsyncMock(return_value=app_doc)):
            with pytest.raises(ValueError, match="key"):
                await add_app_permission("app_test_001", {"group": "G", "label": "L"}, performed_by="u")


# ---------------------------------------------------------------------------
# remove_app_permission
# ---------------------------------------------------------------------------

class TestRemoveAppPermission:
    @pytest.mark.asyncio
    async def test_remove_marks_perm_inactive(self):
        from services.ingestion.rbac_generator import remove_app_permission

        app_doc = _make_app_doc()
        update_results = []

        mock_db = MagicMock()
        mock_db.permissions.update_one = AsyncMock(return_value=_make_update_result(1))
        mock_db.roles.find = MagicMock(return_value=_make_async_list([]))
        mock_db.roles.update_many = AsyncMock()

        async def mock_log(**kwargs):
            pass

        with patch("services.ingestion.rbac_generator.repo.get_app_registry", AsyncMock(return_value=app_doc)), \
             patch("services.ingestion.rbac_generator.get_db", return_value=mock_db), \
             patch("services.ingestion.rbac_generator.log_security_event", mock_log):
            result = await remove_app_permission("app_test_001", "testapp.entity.view", "user_admin")

        assert result["deactivated"] is True
        mock_db.permissions.update_one.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_remove_cascades_to_roles(self):
        from services.ingestion.rbac_generator import remove_app_permission

        app_doc = _make_app_doc()
        role_docs = [{"name": "sales_rep"}, {"name": "account_manager"}]

        mock_db = MagicMock()
        mock_db.permissions.update_one = AsyncMock(return_value=_make_update_result(1))
        mock_db.roles.find = MagicMock(return_value=_make_async_list(role_docs))
        mock_db.roles.update_many = AsyncMock()

        with patch("services.ingestion.rbac_generator.repo.get_app_registry", AsyncMock(return_value=app_doc)), \
             patch("services.ingestion.rbac_generator.get_db", return_value=mock_db), \
             patch("services.ingestion.rbac_generator.log_security_event", AsyncMock()):
            result = await remove_app_permission("app_test_001", "testapp.entity.view", "user_admin")

        assert set(result["removed_from_roles"]) == {"sales_rep", "account_manager"}
        mock_db.roles.update_many.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_remove_writes_audit_log_with_performed_by(self):
        from services.ingestion.rbac_generator import remove_app_permission

        app_doc = _make_app_doc()
        audit_writes = []

        mock_db = MagicMock()
        mock_db.permissions.update_one = AsyncMock(return_value=_make_update_result(1))
        mock_db.roles.find = MagicMock(return_value=_make_async_list([]))
        mock_db.roles.update_many = AsyncMock()

        async def mock_log(**kwargs):
            audit_writes.append(kwargs)

        with patch("services.ingestion.rbac_generator.repo.get_app_registry", AsyncMock(return_value=app_doc)), \
             patch("services.ingestion.rbac_generator.get_db", return_value=mock_db), \
             patch("services.ingestion.rbac_generator.log_security_event", mock_log):
            await remove_app_permission("app_test_001", "testapp.entity.view", "user_admin_xyz")

        assert len(audit_writes) == 1
        assert audit_writes[0]["event_type"] == "app_permission_edited"
        assert audit_writes[0]["user_id"] == "user_admin_xyz"

    @pytest.mark.asyncio
    async def test_remove_missing_app_raises_value_error(self):
        from services.ingestion.rbac_generator import remove_app_permission

        with patch("services.ingestion.rbac_generator.repo.get_app_registry", AsyncMock(return_value=None)):
            with pytest.raises(ValueError, match="not found"):
                await remove_app_permission("bad_app", "testapp.x.view", "u")


# ---------------------------------------------------------------------------
# reassign_app_role_perms
# ---------------------------------------------------------------------------

class TestReassignAppRolePerms:
    @pytest.mark.asyncio
    async def test_reassign_replaces_app_perms_preserves_others(self):
        from services.ingestion.rbac_generator import reassign_app_role_perms

        app_doc = _make_app_doc(app_id="app_crm", app_slug="crm")
        role_doc = {
            "name": "sales_rep",
            "permissions": [
                "crm.contacts.view",   # CRM perm — will be replaced
                "finance.portal",      # DIFFERENT app perm — must be preserved
            ],
        }

        # App owns only crm.contacts.view and crm.contacts.create
        app_keys = {"crm.contacts.view", "crm.contacts.create"}

        updated_perms = []

        async def mock_update_one(query, update, *args, **kwargs):
            updated_perms.extend(update["$set"]["permissions"])
            return _make_update_result(1)

        mock_db = MagicMock()
        mock_db.roles.find_one = AsyncMock(return_value=role_doc)
        mock_db.roles.update_one = mock_update_one

        with patch("services.ingestion.rbac_generator.repo.get_app_registry", AsyncMock(return_value=app_doc)), \
             patch("services.ingestion.rbac_generator.get_db", return_value=mock_db), \
             patch("services.ingestion.rbac_generator._app_permission_keys", AsyncMock(return_value=app_keys)), \
             patch("services.ingestion.rbac_generator.log_security_event", AsyncMock()):
            result = await reassign_app_role_perms(
                app_id="app_crm",
                role_name="sales_rep",
                perm_keys=["crm.contacts.create"],
                performed_by="admin_user",
            )

        # finance.portal must be preserved; crm.contacts.view replaced by crm.contacts.create
        assert "finance.portal" in updated_perms
        assert "crm.contacts.create" in updated_perms
        assert "crm.contacts.view" not in updated_perms

    @pytest.mark.asyncio
    async def test_reassign_empty_perm_keys_clears_app_perms(self):
        from services.ingestion.rbac_generator import reassign_app_role_perms

        app_doc = _make_app_doc(app_id="app_crm")
        role_doc = {
            "name": "sales_rep",
            "permissions": ["crm.contacts.view", "finance.portal"],
        }
        app_keys = {"crm.contacts.view", "crm.contacts.create"}
        updated_perms = []

        async def mock_update_one(query, update, *args, **kwargs):
            updated_perms.extend(update["$set"]["permissions"])
            return _make_update_result(1)

        mock_db = MagicMock()
        mock_db.roles.find_one = AsyncMock(return_value=role_doc)
        mock_db.roles.update_one = mock_update_one

        with patch("services.ingestion.rbac_generator.repo.get_app_registry", AsyncMock(return_value=app_doc)), \
             patch("services.ingestion.rbac_generator.get_db", return_value=mock_db), \
             patch("services.ingestion.rbac_generator._app_permission_keys", AsyncMock(return_value=app_keys)), \
             patch("services.ingestion.rbac_generator.log_security_event", AsyncMock()):
            result = await reassign_app_role_perms(
                app_id="app_crm",
                role_name="sales_rep",
                perm_keys=[],
                performed_by="admin_user",
            )

        # All CRM perms removed; finance.portal preserved
        assert "finance.portal" in updated_perms
        assert "crm.contacts.view" not in updated_perms
        assert "crm.contacts.create" not in updated_perms

    @pytest.mark.asyncio
    async def test_reassign_invalid_key_raises_value_error(self):
        from services.ingestion.rbac_generator import reassign_app_role_perms

        app_doc = _make_app_doc(app_id="app_crm")
        role_doc = {"name": "sales_rep", "permissions": []}
        app_keys = {"crm.contacts.view"}

        mock_db = MagicMock()
        mock_db.roles.find_one = AsyncMock(return_value=role_doc)

        with patch("services.ingestion.rbac_generator.repo.get_app_registry", AsyncMock(return_value=app_doc)), \
             patch("services.ingestion.rbac_generator.get_db", return_value=mock_db), \
             patch("services.ingestion.rbac_generator._app_permission_keys", AsyncMock(return_value=app_keys)):
            with pytest.raises(ValueError, match="not registered"):
                await reassign_app_role_perms(
                    app_id="app_crm",
                    role_name="sales_rep",
                    perm_keys=["crm.contacts.THIS_DOES_NOT_EXIST"],
                    performed_by="admin_user",
                )

    @pytest.mark.asyncio
    async def test_reassign_missing_role_raises_value_error(self):
        from services.ingestion.rbac_generator import reassign_app_role_perms

        app_doc = _make_app_doc()
        mock_db = MagicMock()
        mock_db.roles.find_one = AsyncMock(return_value=None)

        with patch("services.ingestion.rbac_generator.repo.get_app_registry", AsyncMock(return_value=app_doc)), \
             patch("services.ingestion.rbac_generator.get_db", return_value=mock_db), \
             patch("services.ingestion.rbac_generator._app_permission_keys", AsyncMock(return_value=set())):
            with pytest.raises(ValueError, match="not found"):
                await reassign_app_role_perms(
                    app_id="app_test_001",
                    role_name="nonexistent_role",
                    perm_keys=[],
                    performed_by="admin_user",
                )

    @pytest.mark.asyncio
    async def test_reassign_missing_app_raises_value_error(self):
        from services.ingestion.rbac_generator import reassign_app_role_perms

        with patch("services.ingestion.rbac_generator.repo.get_app_registry", AsyncMock(return_value=None)):
            with pytest.raises(ValueError, match="not found"):
                await reassign_app_role_perms(
                    app_id="bad_app",
                    role_name="sales_rep",
                    perm_keys=[],
                    performed_by="admin_user",
                )

    @pytest.mark.asyncio
    async def test_reassign_writes_audit_log(self):
        from services.ingestion.rbac_generator import reassign_app_role_perms

        app_doc = _make_app_doc()
        role_doc = {"name": "sales_rep", "permissions": []}
        app_keys = {"testapp.entity.view"}
        audit_writes = []

        async def mock_log(**kwargs):
            audit_writes.append(kwargs)

        mock_db = MagicMock()
        mock_db.roles.find_one = AsyncMock(return_value=role_doc)
        mock_db.roles.update_one = AsyncMock(return_value=_make_update_result(1))

        with patch("services.ingestion.rbac_generator.repo.get_app_registry", AsyncMock(return_value=app_doc)), \
             patch("services.ingestion.rbac_generator.get_db", return_value=mock_db), \
             patch("services.ingestion.rbac_generator._app_permission_keys", AsyncMock(return_value=app_keys)), \
             patch("services.ingestion.rbac_generator.log_security_event", mock_log):
            await reassign_app_role_perms(
                app_id="app_test_001",
                role_name="sales_rep",
                perm_keys=["testapp.entity.view"],
                performed_by="admin_xyz",
            )

        assert len(audit_writes) == 1
        assert audit_writes[0]["event_type"] == "app_permission_edited"
        assert audit_writes[0]["user_id"] == "admin_xyz"


# ---------------------------------------------------------------------------
# Re-ingest preservation: manual_override perms survive deactivate_permissions
# ---------------------------------------------------------------------------

class TestManualOverridePreservation:
    @pytest.mark.asyncio
    async def test_deactivate_skips_manual_override_source(self):
        """deactivate_permissions must NOT flip manual_override perms inactive."""
        from services.permission_registry import deactivate_permissions

        update_many_calls = []

        async def mock_update_many(query, update):
            update_many_calls.append(query)
            r = MagicMock()
            r.modified_count = 0
            return r

        mock_db = MagicMock()
        mock_db.permissions.update_many = mock_update_many

        with patch("services.permission_registry.get_db", return_value=mock_db):
            await deactivate_permissions("app_test_001", keys_to_keep=set())

        assert len(update_many_calls) == 1
        query = update_many_calls[0]
        # Must have source=ingestion constraint to exclude manual_override rows
        assert "source" in str(query) or "$and" in query, (
            "deactivate_permissions must filter by source to protect manual_override perms"
        )
        # The $and clause should restrict to ingestion source (not manual_override)
        and_clause = query.get("$and", [])
        sources_in_query = [c.get("source") for c in and_clause if "source" in c]
        assert "manual_override" not in sources_in_query or any(
            "$ne" in str(c) for c in and_clause
        ), "Query should exclude manual_override from deactivation"


# ---------------------------------------------------------------------------
# Helpers for async cursor mocking
# ---------------------------------------------------------------------------

def _make_async_list(docs):
    """Return an object whose .to_list() is an async method returning docs."""
    class AsyncList:
        async def to_list(self, length=None):
            return list(docs)

    return AsyncList()
