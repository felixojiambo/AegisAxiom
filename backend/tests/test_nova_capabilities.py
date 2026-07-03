"""Tests for the Nova dynamic capability summary.

These tests pin two contracts:

1. The capability summary is derived from NOVA_TOOL_SCHEMAS — adding or
   removing a tool changes the rendered intro / structured summary.
2. Nova's intro and capability copy contains no hardcoded
   people / company / tenant names. The product team flagged the prior
   intro for naming specific employees and the SimpliMeta brand inside
   the static prompt; this test prevents regressing.
"""
from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production")

import pytest

import services.ai.agents  # noqa: F401  registers NovaAgent
from services.ai.agents.nova import _SYSTEM_PROMPT, NovaAgent
from services.ai.agents.nova_capabilities import (
    build_nova_capability_summary,
    render_nova_intro,
)
from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS


_FORBIDDEN_NAMES = ["Ryan", "Asad", "SimpliMeta"]


def _all_registered_tools() -> list[str]:
    return [schema["name"] for schema in NOVA_TOOL_SCHEMAS]


# ---------------------------------------------------------------------------
# 1. Capability summary is generated from NOVA_TOOL_SCHEMAS
# ---------------------------------------------------------------------------
def test_capability_summary_derives_from_registered_tools():
    summary = build_nova_capability_summary()
    assert summary["assistant_name"] == "Nova"
    assert "cash flow projection assistant" in summary["role"]
    assert "finance reporting analyst" in summary["role"]

    # Every tool listed under any enabled category must actually be in
    # NOVA_TOOL_SCHEMAS — the helper cannot invent capabilities backed by
    # tools that aren't registered.
    registered = set(_all_registered_tools())
    for entry in summary["capabilities"]:
        assert entry["enabled"] is True
        for tool in entry["tools"]:
            assert tool in registered, (
                f"Capability '{entry['category']}' lists tool '{tool}' that "
                f"is not in NOVA_TOOL_SCHEMAS"
            )


# ---------------------------------------------------------------------------
# 2 & 3. No hardcoded people or company names
# ---------------------------------------------------------------------------
def test_capability_summary_has_no_hardcoded_names():
    summary = build_nova_capability_summary()
    summary_text = repr(summary)
    for forbidden in _FORBIDDEN_NAMES:
        assert forbidden not in summary_text, (
            f"Capability summary leaks hardcoded name: {forbidden}"
        )


def test_intro_markdown_has_no_hardcoded_names():
    intro = render_nova_intro()
    for forbidden in _FORBIDDEN_NAMES:
        assert forbidden not in intro, f"Intro leaks hardcoded name: {forbidden}"


def test_system_prompt_has_no_hardcoded_names():
    for forbidden in _FORBIDDEN_NAMES:
        assert forbidden not in _SYSTEM_PROMPT, (
            f"_SYSTEM_PROMPT still contains hardcoded {forbidden}; "
            "Nova's static prompt must be tenant-neutral."
        )


# ---------------------------------------------------------------------------
# 4-7. Categories are gated on backing tools
# ---------------------------------------------------------------------------
def _categories(summary: dict) -> set[str]:
    return {entry["category"] for entry in summary["capabilities"]}


def test_cash_flow_projections_requires_projection_tools():
    summary = build_nova_capability_summary(available_tools=["run_projection"])
    assert "Cash Flow Projections" in _categories(summary)

    summary_no_projection = build_nova_capability_summary(available_tools=["query_finance_data"])
    assert "Cash Flow Projections" not in _categories(summary_no_projection)


def test_issued_invoice_capability_requires_query_invoice_totals():
    summary = build_nova_capability_summary(available_tools=["query_invoice_totals"])
    assert "Issued Invoices" in _categories(summary)

    summary_without = build_nova_capability_summary(available_tools=["get_open_invoices"])
    assert "Issued Invoices" not in _categories(summary_without)


def test_open_invoice_capability_requires_get_open_invoices():
    summary = build_nova_capability_summary(available_tools=["get_open_invoices"])
    assert "Open Invoices & Bills" in _categories(summary)

    summary_without = build_nova_capability_summary(available_tools=["query_invoice_totals"])
    assert "Open Invoices & Bills" not in _categories(summary_without)


def test_data_freshness_requires_get_finance_data_freshness():
    summary = build_nova_capability_summary(available_tools=["get_finance_data_freshness"])
    assert "Data Freshness" in _categories(summary)

    summary_without = build_nova_capability_summary(available_tools=["run_projection"])
    assert "Data Freshness" not in _categories(summary_without)


# ---------------------------------------------------------------------------
# 8. Removing a tool removes / disables the related claim
# ---------------------------------------------------------------------------
def test_removing_tool_disables_related_claim():
    full = _all_registered_tools()
    # Data Freshness is backed by both get_finance_data_freshness AND
    # get_finance_data_status — removing one keeps the category alive
    # (which is the whole point of the OR-of-backing-tools design),
    # so this test removes the entire backing set to confirm the
    # category drops out and is surfaced as a limitation.
    freshness_tools = {"get_finance_data_freshness", "get_finance_data_status"}
    assert freshness_tools <= set(full)

    pruned = [t for t in full if t not in freshness_tools]
    summary = build_nova_capability_summary(available_tools=pruned)

    assert "Data Freshness" not in _categories(summary)
    # And the limitation is surfaced so Nova tells the user the gap exists.
    limitations_text = " ".join(summary["limitations"])
    assert "Data Freshness" in limitations_text


def test_removing_all_projection_tools_removes_category_and_lists_limitation():
    full = _all_registered_tools()
    projection_tools = {
        "run_projection",
        "add_manual_assumption",
        "apply_override",
        "explain_week",
        "reset_scenario_state",
        "compare_runs",
    }
    pruned = [t for t in full if t not in projection_tools]
    summary = build_nova_capability_summary(available_tools=pruned)

    assert "Cash Flow Projections" not in _categories(summary)
    assert any("Cash Flow Projections" in line for line in summary["limitations"])


# ---------------------------------------------------------------------------
# 9. Greeting / "what can you do?" route uses the dynamic helper
# ---------------------------------------------------------------------------
def test_nova_agent_exposes_dynamic_capability_summary():
    agent = NovaAgent()
    summary = agent.capability_summary()
    assert summary["assistant_name"] == "Nova"
    # Must contain at least one enabled category from the live registry.
    assert summary["capabilities"], "agent.capability_summary() returned no enabled categories"


def test_nova_agent_intro_message_is_neutral_and_dynamic():
    agent = NovaAgent()
    intro = agent.intro_message()
    assert "Nova" in intro
    assert "cash flow projection assistant" in intro
    assert "finance reporting analyst" in intro
    for forbidden in _FORBIDDEN_NAMES:
        assert forbidden not in intro

    # Intro must derive its bullets from registered categories — the live
    # registry includes projections, so that bullet must show up.
    assert "Cash Flow Projections" in intro


@pytest.mark.asyncio
async def test_nova_build_system_prompt_includes_capability_summary():
    """The compiled system prompt the conductor sees must include the
    deterministic capability block, so the LLM has the registered-tool
    list when asked 'what can you do?'."""
    from types import SimpleNamespace

    agent = NovaAgent()
    user = SimpleNamespace(user_id="user_1", permissions=["nova.use"])
    prompt = await agent.build_system_prompt(user, data="", summary="")

    assert "Capability summary" in prompt
    assert "Cash Flow Projections" in prompt
    for forbidden in _FORBIDDEN_NAMES:
        assert forbidden not in prompt


# ---------------------------------------------------------------------------
# 10 & 11. Sourcing rule + examples-are-routing rule live in the prompt
# ---------------------------------------------------------------------------
def test_prompt_says_finance_numbers_must_come_from_tools():
    text = _SYSTEM_PROMPT.lower()
    # The mandatory-sourcing rule mentions the no-hardcoded-values
    # constraint explicitly.
    assert "must never answer" in text
    assert "hardcoded" in text or "demo values" in text
    assert "remembered outputs" in text or "memory" in text or "previous" in text
    assert "approved tool" in text


def test_prompt_says_examples_are_routing_only():
    text = _SYSTEM_PROMPT.lower()
    assert "routing examples only" in text
    assert "do not reuse example values" in text


# ---------------------------------------------------------------------------
# 12. No new code path contains hardcoded demo people / company strings
# ---------------------------------------------------------------------------
def test_capabilities_module_source_has_no_hardcoded_names():
    import services.ai.agents.nova_capabilities as mod
    import inspect

    source = inspect.getsource(mod)
    for forbidden in _FORBIDDEN_NAMES:
        assert forbidden not in source, (
            f"nova_capabilities.py leaks hardcoded {forbidden}"
        )


def test_nova_module_source_has_no_hardcoded_names():
    import services.ai.agents.nova as mod
    import inspect

    source = inspect.getsource(mod)
    for forbidden in _FORBIDDEN_NAMES:
        assert forbidden not in source, (
            f"nova.py leaks hardcoded {forbidden}"
        )


# ---------------------------------------------------------------------------
# Driver Explanations capability gating
# ---------------------------------------------------------------------------


def test_capability_summary_includes_driver_explanations_when_tool_registered():
    summary = build_nova_capability_summary(
        available_tools=["explain_finance_drivers"],
    )
    assert "Driver Explanations" in _categories(summary)


def test_capability_summary_omits_driver_explanations_when_tool_absent():
    summary = build_nova_capability_summary(
        available_tools=["run_projection"],  # no explain_finance_drivers
    )
    assert "Driver Explanations" not in _categories(summary)
    # And it must show up in limitations so Nova can disclose the gap.
    limitations_text = " ".join(summary["limitations"])
    assert "Driver Explanations" in limitations_text


def test_prompt_says_driver_explanations_must_be_source_data_backed():
    from services.ai.agents.nova import _SYSTEM_PROMPT

    assert "## Driver explanations" in _SYSTEM_PROMPT
    assert "explain_finance_drivers" in _SYSTEM_PROMPT
    lowered = _SYSTEM_PROMPT.lower()
    # Mandatory rules from the spec.
    assert "do not give generic commentary" in lowered
    # Canonical field names (post comparison-metadata normalization).
    # The deprecated alias `summary.current_total` is no longer
    # required in the prompt — the canonical name now wins.
    assert "summary.current_period_total" in lowered
    assert "summary.variance_amount" in lowered
    assert "top_accounts" in lowered
