"""
Tests for apply_persona_defaults() in services/ai_employee_defaults.py (Wave 1D).

Tests are offline: DB and registry are mocked.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from services.ai_employee_defaults import (
    apply_persona_defaults,
    DEFAULT_APP_ACCESS_BY_PERSONA,
    _resolve_patterns,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_employee_doc(employee_id="ai_emp_001", app_access=None):
    return {
        "employee_id": employee_id,
        "app_access": app_access or [],
    }


def _active_keys_for_persona(persona: str) -> set[str]:
    """Return all literal pattern keys in a persona (no glob chars) so
    _resolve_patterns can find them as exact matches."""
    keys = set()
    persona_map = DEFAULT_APP_ACCESS_BY_PERSONA.get(persona, {})
    for patterns in persona_map.values():
        for p in patterns:
            if not any(ch in p for ch in "*?["):
                keys.add(p)
    return keys


# ---------------------------------------------------------------------------
# apply_persona_defaults: sales_assistant
# ---------------------------------------------------------------------------

class TestApplyPersonaDefaultsSalesAssistant:
    @pytest.mark.asyncio
    async def test_sales_assistant_populates_expected_app_access(self):
        employee_doc = _make_employee_doc()
        final_state = []

        async def mock_update_one(query, update, *args, **kwargs):
            final_state.append(update["$set"]["app_access"])

        mock_db = MagicMock()
        mock_db.ai_employees.find_one = AsyncMock(return_value=employee_doc)
        mock_db.ai_employees.update_one = mock_update_one

        # Provide active keys for all patterns in the persona
        active_keys = _active_keys_for_persona("sales_assistant")

        with patch("services.ai_employee_defaults.get_db", return_value=mock_db), \
             patch("services.ai_employee_defaults.get_all_permissions",
                   AsyncMock(return_value=[{"key": k} for k in active_keys])):
            await apply_persona_defaults("ai_emp_001", "sales_assistant")

        assert len(final_state) == 1
        access = final_state[0]
        slugs = {e["app_slug"] for e in access}
        # sales_assistant covers contacts, accounts, deals, opportunities
        for expected_slug in ("contacts", "accounts", "deals", "opportunities"):
            assert expected_slug in slugs, (
                f"Expected slug '{expected_slug}' in app_access, got: {slugs}"
            )

    @pytest.mark.asyncio
    async def test_sales_assistant_includes_contacts_perms(self):
        employee_doc = _make_employee_doc()
        final_state = []

        async def mock_update_one(query, update, *args, **kwargs):
            final_state.append(update["$set"]["app_access"])

        mock_db = MagicMock()
        mock_db.ai_employees.find_one = AsyncMock(return_value=employee_doc)
        mock_db.ai_employees.update_one = mock_update_one

        active_keys = _active_keys_for_persona("sales_assistant")

        with patch("services.ai_employee_defaults.get_db", return_value=mock_db), \
             patch("services.ai_employee_defaults.get_all_permissions",
                   AsyncMock(return_value=[{"key": k} for k in active_keys])):
            await apply_persona_defaults("ai_emp_001", "sales_assistant")

        contacts_entry = next(
            (e for e in final_state[0] if e["app_slug"] == "contacts"), None
        )
        assert contacts_entry is not None
        assert "contacts.view.all" in contacts_entry["permissions"]
        assert "contacts.create" in contacts_entry["permissions"]
        assert "contacts.edit.all" in contacts_entry["permissions"]


# ---------------------------------------------------------------------------
# Idempotency: applying same persona twice = no duplicates
# ---------------------------------------------------------------------------

class TestIdempotency:
    @pytest.mark.asyncio
    async def test_applying_same_persona_twice_no_duplicates(self):
        active_keys = _active_keys_for_persona("sales_assistant")
        registry_docs = [{"key": k} for k in active_keys]

        states = []

        async def capture_update(query, update, *args, **kwargs):
            states.append([e.copy() for e in update["$set"]["app_access"]])

        mock_db = MagicMock()
        mock_db.ai_employees.find_one = AsyncMock(return_value=_make_employee_doc())
        mock_db.ai_employees.update_one = capture_update

        with patch("services.ai_employee_defaults.get_db", return_value=mock_db), \
             patch("services.ai_employee_defaults.get_all_permissions",
                   AsyncMock(return_value=registry_docs)):
            await apply_persona_defaults("ai_emp_001", "sales_assistant")

        # Second call: employee already has app_access from first call
        first_app_access = states[0]
        mock_db.ai_employees.find_one = AsyncMock(
            return_value=_make_employee_doc(app_access=first_app_access)
        )

        with patch("services.ai_employee_defaults.get_db", return_value=mock_db), \
             patch("services.ai_employee_defaults.get_all_permissions",
                   AsyncMock(return_value=registry_docs)):
            await apply_persona_defaults("ai_emp_001", "sales_assistant")

        second_app_access = states[1]

        # Every perm list must have no duplicates
        for entry in second_app_access:
            perms = entry.get("permissions", [])
            assert len(perms) == len(set(perms)), (
                f"Duplicate permissions found for {entry['app_slug']}: {perms}"
            )

        # Lengths must match first call (no extras)
        first_by_slug = {e["app_slug"]: set(e["permissions"]) for e in first_app_access}
        second_by_slug = {e["app_slug"]: set(e["permissions"]) for e in second_app_access}
        assert first_by_slug == second_by_slug


# ---------------------------------------------------------------------------
# Merge: existing app_access is union'd, not overwritten
# ---------------------------------------------------------------------------

class TestMergeBehavior:
    @pytest.mark.asyncio
    async def test_existing_contacts_entry_is_merged_not_replaced(self):
        # Employee already has contacts.view.all; persona also grants view.all + create
        existing_app_access = [
            {
                "app_slug": "contacts",
                "permissions": ["contacts.view.all", "contacts.custom_extra"],
                "linked_at": "2025-01-01T00:00:00",
            }
        ]
        employee_doc = _make_employee_doc(app_access=existing_app_access)
        final_state = []

        async def mock_update_one(query, update, *args, **kwargs):
            final_state.append(update["$set"]["app_access"])

        mock_db = MagicMock()
        mock_db.ai_employees.find_one = AsyncMock(return_value=employee_doc)
        mock_db.ai_employees.update_one = mock_update_one

        active_keys = _active_keys_for_persona("sales_assistant")

        with patch("services.ai_employee_defaults.get_db", return_value=mock_db), \
             patch("services.ai_employee_defaults.get_all_permissions",
                   AsyncMock(return_value=[{"key": k} for k in active_keys])):
            await apply_persona_defaults("ai_emp_001", "sales_assistant")

        contacts_entry = next(
            (e for e in final_state[0] if e["app_slug"] == "contacts"), None
        )
        assert contacts_entry is not None
        # custom_extra must be preserved
        assert "contacts.custom_extra" in contacts_entry["permissions"]
        # Persona perms must also be added
        assert "contacts.view.all" in contacts_entry["permissions"]
        assert "contacts.create" in contacts_entry["permissions"]


# ---------------------------------------------------------------------------
# Glob resolution
# ---------------------------------------------------------------------------

class TestGlobResolution:
    def test_glob_pattern_resolves_to_matching_active_keys(self):
        active_keys = {
            "crm.contacts.view.all",
            "crm.contacts.view.own",
            "crm.contacts.edit.all",
            "crm.contacts.create",
            "finance.portal",
        }
        patterns = ["crm.contacts.*"]
        result = set(_resolve_patterns(patterns, active_keys))
        # All crm.contacts.* keys should match
        assert "crm.contacts.view.all" in result
        assert "crm.contacts.view.own" in result
        assert "crm.contacts.edit.all" in result
        assert "crm.contacts.create" in result
        # finance.portal must NOT match
        assert "finance.portal" not in result

    def test_literal_pattern_resolves_only_if_active(self):
        active_keys = {"contacts.view.all"}
        patterns = ["contacts.view.all", "contacts.create"]  # create is not active
        result = set(_resolve_patterns(patterns, active_keys))
        assert "contacts.view.all" in result
        assert "contacts.create" not in result


# ---------------------------------------------------------------------------
# Unknown persona: no-op
# ---------------------------------------------------------------------------

class TestUnknownPersona:
    @pytest.mark.asyncio
    async def test_unknown_persona_is_silent_noop(self):
        """apply_persona_defaults with an unknown persona must NOT update the employee."""
        mock_db = MagicMock()
        mock_db.ai_employees.update_one = AsyncMock()

        with patch("services.ai_employee_defaults.get_db", return_value=mock_db), \
             patch("services.ai_employee_defaults.get_all_permissions", AsyncMock(return_value=[])):
            # Should not raise
            await apply_persona_defaults("ai_emp_001", "totally_fake_persona_xyz")

        # No DB write should have occurred
        mock_db.ai_employees.update_one.assert_not_called()
