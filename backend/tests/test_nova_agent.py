"""Tests for NovaAgent registration with the agent framework.

Verifies the @register decorator put NovaAgent into _AGENTS keyed
by route_name, that BaseAgent contracts are honored, and that the
tool_schemas property surfaces all four Nova tools.
"""
import os

# Environment bootstrap — auth_helper raises if JWT_SECRET is missing.
# services.ai.__init__ now imports the conductor at package load, which
# transitively pulls auth_helper. Pattern matches test_nova_route.py.
os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production")

from types import SimpleNamespace

import pytest

# Importing the agents package triggers the @register decorator.
import services.ai.agents  # noqa: F401
from services.ai.agents.base import BaseAgent
from services.ai.agents.nova import NovaAgent
from services.ai.agents.registry import _AGENTS
from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS


def test_nova_registered_in_registry():
    assert "nova" in _AGENTS
    assert isinstance(_AGENTS["nova"], NovaAgent)


def test_nova_subclasses_base_agent():
    assert issubclass(NovaAgent, BaseAgent)


def test_nova_route_name_and_description():
    agent = _AGENTS["nova"]
    assert agent.route_name == "nova"
    assert "cash flow" in agent.description.lower() or "projection" in agent.description.lower()


def test_nova_required_permissions():
    agent = _AGENTS["nova"]
    assert agent.required_permissions == ["nova.use"]


def test_nova_model_config():
    agent = _AGENTS["nova"]
    assert agent.model_provider == "google"
    assert agent.model_name == "gemini-3-flash-preview"


def test_nova_collections_empty_action_driven():
    """Nova is action-driven, not query-driven — collections is empty."""
    agent = _AGENTS["nova"]
    assert agent.collections == []


def test_nova_tool_schemas_property_exposes_seven_tools():
    # Legacy test name kept so blame stays readable; the assertion now
    # only pins the contract this test is responsible for — the property
    # surfaces the canonical NOVA_TOOL_SCHEMAS list, with no duplicates
    # and with the core projection tools plus the expanded finance tools
    # (issued/open invoices, GL transactions, finance-data freshness)
    # reachable. The full canonical set lives in test_nova_tools.py.
    agent = _AGENTS["nova"]
    schemas = agent.tool_schemas
    assert schemas is NOVA_TOOL_SCHEMAS

    names = [s["name"] for s in schemas]
    assert len(names) == len(set(names)), "duplicate tool names in NOVA_TOOL_SCHEMAS"

    required_core = {
        "run_projection",
        "apply_override",
        "add_manual_assumption",
        "explain_week",
        "query_finance_data",
        "reset_scenario_state",
        "compare_runs",
    }
    required_expanded = {
        "query_invoice_totals",
        "get_open_invoices",
        "query_gl_transactions",
        "get_finance_data_freshness",
    }
    assert required_core <= set(names)
    assert required_expanded <= set(names)


@pytest.mark.asyncio
async def test_nova_build_system_prompt_returns_string():
    agent = _AGENTS["nova"]
    user = SimpleNamespace(user_id="user_1", permissions=["nova.use"])
    prompt = await agent.build_system_prompt(user, data="", summary="")
    assert isinstance(prompt, str)
    assert "Nova" in prompt
    # Asserts the no-LLM-math guardrail survives in the prompt. Wording
    # changed from "do NOT do arithmetic" to "don't do arithmetic" in the
    # round-3 tone reframe; both phrasings count.
    assert "don't do arithmetic" in prompt or "do NOT do arithmetic" in prompt


@pytest.mark.asyncio
async def test_nova_fetch_data_returns_empty_action_driven():
    """Nova is action-driven; fetch_data is a no-op stub returning the
    empty {docs, summary, total_count} shape BaseAgent callers expect."""
    agent = _AGENTS["nova"]
    user = SimpleNamespace(user_id="user_1", permissions=["nova.use"])
    result = await agent.fetch_data(db=None, user=user, query={})
    assert isinstance(result, dict)
    assert result == {"docs": [], "summary": "", "total_count": 0}


def test_nova_user_has_access_with_perm():
    agent = _AGENTS["nova"]
    user = SimpleNamespace(user_id="user_1", permissions=["nova.use"])
    assert agent.user_has_access(user) is True


def test_nova_user_has_access_with_wildcard():
    agent = _AGENTS["nova"]
    user = SimpleNamespace(user_id="user_admin", permissions=["*"])
    assert agent.user_has_access(user) is True


def test_nova_user_has_access_denied_without_perm():
    agent = _AGENTS["nova"]
    user = SimpleNamespace(user_id="user_other", permissions=["chat.access"])
    assert agent.user_has_access(user) is False
