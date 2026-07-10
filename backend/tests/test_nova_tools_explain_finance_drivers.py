"""Locks the explain_finance_drivers tool input clamp.

The tool reads an LLM supplied ``limit`` from ``tool_args``. Without
a clamp a value such as 99999 would materialise an unbounded
``top_accounts`` list. The clamp clamps the value into the closed
interval one through fifty before forwarding to the analyzer.

Synthetic only. Fixtures use far future dates and fake account
labels so the case is stable across calendar drift.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest")

import pytest


@pytest.mark.asyncio
async def test_explain_finance_drivers_clamps_limit_to_fifty():
    """An LLM supplied limit of 99999 must be clamped to 50 before
    reaching the analyzer. The clamped value flows into
    _analyze_drivers as the keyword arg."""
    from services.ai.agents.tools import nova_tools
    from services.ai.agents.tools.nova_tools import _ScenarioState

    captured = {}

    async def _fake_analyze(qbo_db, **kwargs):
        captured.update(kwargs)
        return {
            "metric": kwargs.get("metric"),
            "top_accounts": [
                {"account_name": "synth_acct_" + str(i), "amount": float(i)}
                for i in range(kwargs.get("limit", 0))
            ],
        }

    with patch(
        "services.nova_engine.finance_driver_analysis.analyze_finance_drivers",
        _fake_analyze,
    ), patch(
        "services.nova_engine.db.get_qbo_db",
        return_value=SimpleNamespace(),
    ):
        result = await nova_tools.execute_tool(
            "explain_finance_drivers",
            {
                "metric": "revenue",
                "date_from": "2099-01-01",
                "date_to": "2099-01-31",
                "basis": "accrual",
                "limit": 99999,
            },
            _ScenarioState(),
            "u_synth_d2",
            "sess_synth_d2",
            None,
        )

    assert captured.get("limit") == 50
    assert len(result.get("top_accounts", [])) == 50
