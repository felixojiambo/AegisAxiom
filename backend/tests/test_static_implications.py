"""
Tests for static_implications() in services/permission_implications.py (Wave 1E).

Pure-function tests — no DB, no async, no mocking needed.
"""
import pytest

from services.permission_implications import static_implications


class TestStaticImplicationsClosure:
    """Verify the closed set returned for each representative input."""

    def test_delete_all_returns_5_keys(self):
        result = static_implications("crm.contacts.delete.all")
        expected = {
            "crm.contacts.delete.own",
            "crm.contacts.edit.all",
            "crm.contacts.edit.own",
            "crm.contacts.view.all",
            "crm.contacts.view.own",
        }
        assert set(result) == expected, f"Got {result}"

    def test_delete_own_returns_2_keys(self):
        result = static_implications("crm.contacts.delete.own")
        expected = {
            "crm.contacts.edit.own",
            "crm.contacts.view.own",
        }
        assert set(result) == expected, f"Got {result}"

    def test_edit_all_returns_3_keys(self):
        result = static_implications("crm.contacts.edit.all")
        expected = {
            "crm.contacts.edit.own",
            "crm.contacts.view.all",
            "crm.contacts.view.own",
        }
        assert set(result) == expected, f"Got {result}"

    def test_edit_no_scope_returns_1_key(self):
        result = static_implications("crm.contacts.edit")
        assert result == ["crm.contacts.view"], f"Got {result}"

    def test_view_returns_empty(self):
        result = static_implications("crm.contacts.view")
        assert result == []

    def test_view_all_returns_view_own_only(self):
        result = static_implications("crm.contacts.view.all")
        assert set(result) == {"crm.contacts.view.own"}, f"Got {result}"

    def test_create_returns_empty(self):
        result = static_implications("crm.contacts.create")
        assert result == []

    def test_change_status_returns_empty(self):
        result = static_implications("crm.contacts.change_status")
        assert result == []

    def test_assign_returns_empty(self):
        result = static_implications("crm.contacts.assign")
        assert result == []

    def test_access_returns_empty(self):
        result = static_implications("crm.access")
        assert result == []

    def test_non_verb_tail_returns_empty(self):
        """'portal' is not a ladder verb — should produce no implications."""
        result = static_implications("finance.portal")
        assert result == []

    def test_wildcard_returns_empty(self):
        result = static_implications("*")
        assert result == []

    def test_empty_string_returns_empty(self):
        result = static_implications("")
        assert result == []

    def test_none_returns_empty(self):
        result = static_implications(None)
        assert result == []


class TestCrossPrefixIsolation:
    """Implications must never cross module boundaries."""

    def test_delete_all_does_not_imply_deals_perms(self):
        result = static_implications("crm.contacts.delete.all")
        for key in result:
            assert not key.startswith("crm.deals."), (
                f"Cross-prefix leakage: {key} found in implications of crm.contacts.delete.all"
            )

    def test_delete_all_does_not_imply_finance_perms(self):
        result = static_implications("crm.contacts.delete.all")
        for key in result:
            assert not key.startswith("finance."), (
                f"Cross-module leakage: {key} in crm.contacts.delete.all implications"
            )
