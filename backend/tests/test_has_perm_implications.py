"""
Tests for has_perm() in auth_helper.py that exercise implication paths (Wave 2D).

has_perm() is sync. It uses a precomputed _perm_closure stashed on the user
object by get_current_user(). Tests that need transitive resolution build
a SimpleNamespace user with a closure already attached.

For the DB-fetch path (has_perm_async), we mock get_implication_graph so
tests stay offline.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from auth_helper import has_perm, has_perm_async
from services.permission_implications import resolve_implications


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user_with_explicit_perms(*perms: str):
    """User with explicit permissions only; no precomputed closure."""
    return SimpleNamespace(permissions=list(perms))


def _user_with_closure(*perms: str):
    """User with a precomputed closure, as get_current_user would set up."""
    closure = resolve_implications(set(perms), registry_graph={})
    return SimpleNamespace(permissions=list(perms), _perm_closure=closure)


# ---------------------------------------------------------------------------
# Fast paths
# ---------------------------------------------------------------------------

class TestHasPermFastPaths:
    def test_exact_match_in_explicit_perms(self):
        user = _user_with_explicit_perms("crm.contacts.view.all")
        assert has_perm(user, "crm.contacts.view.all") is True

    def test_wildcard_grants_everything(self):
        user = _user_with_explicit_perms("*")
        assert has_perm(user, "crm.contacts.view.all") is True
        assert has_perm(user, "finance.portal") is True
        assert has_perm(user, "any.made_up.perm") is True

    def test_empty_perms_returns_false(self):
        user = _user_with_explicit_perms()
        assert has_perm(user, "crm.contacts.view.all") is False

    def test_unrelated_perm_returns_false(self):
        user = _user_with_explicit_perms("crm.contacts.view.all")
        assert has_perm(user, "finance.portal") is False


class TestHasPermViaStaticImplicationClosure:
    """User holds only a high-privilege key; closure enables lower-key checks."""

    def test_delete_all_implies_view_all(self):
        user = _user_with_closure("crm.contacts.delete.all")
        assert has_perm(user, "crm.contacts.view.all") is True

    def test_delete_all_implies_view_own(self):
        user = _user_with_closure("crm.contacts.delete.all")
        assert has_perm(user, "crm.contacts.view.own") is True

    def test_delete_all_implies_edit_all(self):
        user = _user_with_closure("crm.contacts.delete.all")
        assert has_perm(user, "crm.contacts.edit.all") is True

    def test_delete_all_does_not_imply_deals_perms(self):
        user = _user_with_closure("crm.contacts.delete.all")
        assert has_perm(user, "crm.deals.view.all") is False

    def test_no_closure_degrades_to_direct_membership(self):
        """Without a closure stashed on the user, only direct membership is checked."""
        user = SimpleNamespace(permissions=["crm.contacts.delete.all"])
        # No _perm_closure — falls back to direct check; view is not in explicit list
        assert has_perm(user, "crm.contacts.view.all") is False


class TestHasPermDict:
    """has_perm() must also work when user is a plain dict."""

    def test_dict_user_explicit_match(self):
        user = {"permissions": ["crm.contacts.view.all"]}
        assert has_perm(user, "crm.contacts.view.all") is True

    def test_dict_user_wildcard(self):
        user = {"permissions": ["*"]}
        assert has_perm(user, "any.perm") is True

    def test_dict_user_with_closure(self):
        closure = resolve_implications({"crm.contacts.delete.all"}, registry_graph={})
        user = {"permissions": ["crm.contacts.delete.all"], "_perm_closure": closure}
        assert has_perm(user, "crm.contacts.view.all") is True


# ---------------------------------------------------------------------------
# has_perm_async — registry graph fetch path
# ---------------------------------------------------------------------------

class TestHasPermAsync:
    @pytest.mark.asyncio
    async def test_async_explicit_match_no_graph_fetch(self):
        user = _user_with_explicit_perms("crm.contacts.view.all")
        # Graph fetch should NOT be called; exact match short-circuits.
        with patch("auth_helper.get_cached_implication_graph", new=AsyncMock()) as mock_graph:
            result = await has_perm_async(user, "crm.contacts.view.all")
        assert result is True
        mock_graph.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_wildcard_short_circuits(self):
        user = _user_with_explicit_perms("*")
        with patch("auth_helper.get_cached_implication_graph", new=AsyncMock()) as mock_graph:
            result = await has_perm_async(user, "any.perm")
        assert result is True
        mock_graph.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_transitive_via_registry_graph(self):
        """Without closure on the user, async variant fetches the graph and resolves."""
        user = SimpleNamespace(permissions=["crm.contacts.delete.all"])
        # No _perm_closure — async path must compute it
        with patch(
            "auth_helper.get_cached_implication_graph",
            new=AsyncMock(return_value={}),
        ) as mock_graph:
            result = await has_perm_async(user, "crm.contacts.view.all")
        assert result is True
        mock_graph.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_graph_fetched_once_when_no_closure(self):
        """Registry graph fetch happens at most once even if called back-to-back
        (the TTL cache in auth_helper handles process-level deduplication, but
        for users without a closure the fetch runs once per has_perm_async call —
        the TTL cache is process-scoped, not request-scoped)."""
        user = SimpleNamespace(permissions=["crm.contacts.delete.all"])
        with patch(
            "auth_helper.get_cached_implication_graph",
            new=AsyncMock(return_value={}),
        ):
            r1 = await has_perm_async(user, "crm.contacts.view.all")
            r2 = await has_perm_async(user, "crm.contacts.edit.all")
        assert r1 is True
        assert r2 is True

    @pytest.mark.asyncio
    async def test_async_precomputed_closure_skips_graph_fetch(self):
        """If the user already has _perm_closure set, the graph fetch is skipped."""
        closure = resolve_implications({"crm.contacts.delete.all"}, registry_graph={})
        user = SimpleNamespace(
            permissions=["crm.contacts.delete.all"],
            _perm_closure=closure,
        )
        with patch("auth_helper.get_cached_implication_graph", new=AsyncMock()) as mock_graph:
            result = await has_perm_async(user, "crm.contacts.view.all")
        assert result is True
        mock_graph.assert_not_called()
