"""
Tests for resolve_implications() in services/permission_implications.py (Wave 2D).

Pure-function tests — no DB, no async.
"""
import pytest

from services.permission_implications import resolve_implications, static_implications


class TestResolveImplicationsStaticOnly:
    """Resolver with no registry graph exercises pure static-convention closure."""

    def test_delete_all_resolves_static_closure(self):
        result = resolve_implications({"crm.contacts.delete.all"}, registry_graph={})
        expected = {
            "crm.contacts.delete.all",
            "crm.contacts.delete.own",
            "crm.contacts.edit.all",
            "crm.contacts.edit.own",
            "crm.contacts.view.all",
            "crm.contacts.view.own",
        }
        assert result == expected

    def test_empty_input_returns_empty(self):
        result = resolve_implications(set(), registry_graph={})
        assert result == set()

    def test_none_registry_graph_uses_static_only(self):
        result = resolve_implications({"crm.contacts.edit.all"}, registry_graph=None)
        assert "crm.contacts.view.all" in result
        assert "crm.contacts.view.own" in result
        assert "crm.contacts.edit.own" in result

    def test_view_resolves_to_itself_only(self):
        result = resolve_implications({"crm.contacts.view.all"}, registry_graph={})
        assert result == {"crm.contacts.view.all", "crm.contacts.view.own"}

    def test_non_ladder_verb_stays_isolated(self):
        result = resolve_implications({"crm.contacts.create"}, registry_graph={})
        # create is non-ladder; closure should just be the key itself
        assert result == {"crm.contacts.create"}


class TestResolveImplicationsWithRegistryEdges:
    """Resolver combines registry-declared edges with static convention."""

    def test_registry_edge_followed(self):
        graph = {"finance.portal": ["commissions.view.all"]}
        result = resolve_implications({"finance.portal"}, registry_graph=graph)
        # finance.portal -> commissions.view.all (registry)
        # commissions.view.all -> commissions.view.own (static convention)
        assert "finance.portal" in result
        assert "commissions.view.all" in result
        assert "commissions.view.own" in result

    def test_three_hop_registry_chain_resolves(self):
        graph = {
            "a.access": ["b.edit.all"],
            "b.edit.all": ["c.view.all"],
        }
        result = resolve_implications({"a.access"}, registry_graph=graph)
        assert "a.access" in result
        assert "b.edit.all" in result
        assert "b.edit.own" in result    # static from b.edit.all
        assert "b.view.all" in result    # static from b.edit.all
        assert "b.view.own" in result    # static from b.edit.all
        assert "c.view.all" in result    # registry hop
        assert "c.view.own" in result    # static from c.view.all

    def test_cycle_in_registry_does_not_wedge(self):
        """Cycle detection via visited set — must not loop forever."""
        graph = {
            "a.view": ["b.view"],
            "b.view": ["a.view"],   # cycle
        }
        result = resolve_implications({"a.view"}, registry_graph=graph)
        assert "a.view" in result
        assert "b.view" in result


class TestResolveImplicationsIdempotency:
    """Closure of closure equals closure (idempotent)."""

    def test_idempotent_on_static_closure(self):
        first = resolve_implications({"crm.contacts.delete.all"}, registry_graph={})
        second = resolve_implications(first, registry_graph={})
        assert first == second

    def test_idempotent_with_registry_edges(self):
        graph = {"finance.portal": ["commissions.view.all"]}
        first = resolve_implications({"finance.portal"}, registry_graph=graph)
        second = resolve_implications(first, registry_graph=graph)
        assert first == second
