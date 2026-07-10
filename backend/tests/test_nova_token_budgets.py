"""Tests for Nova-specific token-budget overrides.

The override contract is narrow on purpose:

  * ``NOVA_SESSION_TOKEN_BUDGET`` and ``NOVA_DAILY_TOKEN_BUDGET``
    affect ONLY the ``route="nova"`` budget check. Every other route
    (deals, commissions, marketplace agents, the legacy ``None``
    route) continues to use the global ``AI_*`` budgets.
  * Invalid env values (non-numeric, empty, negative) fall back to
    the global default silently — chat must never refuse a Nova
    turn because the operator typo'd a knob.
  * ``NOVA_MAX_OUTPUT_TOKENS`` is consulted only on the Nova path
    and only when no explicit per-call max is passed in.

All assertions use synthetic budgets and synthetic Mongo stand-ins —
no real LLM API is contacted.
"""
from __future__ import annotations

import asyncio
import importlib
import os

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

import pytest


# ---------------------------------------------------------------------------
# Fake Mongo: just enough to satisfy check_budget's reads.
# ---------------------------------------------------------------------------


class _FakeAggregateCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    async def to_list(self, _limit):
        return self._rows


class _FakeUsageCollection:
    def __init__(self, total_tokens: int):
        self._total = total_tokens

    def aggregate(self, _pipeline):
        return _FakeAggregateCursor([{"_id": None, "total": self._total}])


class _FakeUsersCollection:
    def __init__(self, role: str = "user"):
        self._role = role

    async def find_one(self, _query, _projection=None):
        return {"role": self._role}


class _FakeSessionsCollection:
    def __init__(self, tokens_used: int):
        self._tokens_used = tokens_used

    async def find_one(self, _query, _projection=None):
        return {"tokens_used": self._tokens_used}


class _FakeDb:
    """Minimal Mongo-shaped fake with attribute-style collection access."""

    def __init__(
        self,
        *,
        session_tokens: int = 0,
        usage_tokens: int = 0,
        user_role: str = "user",
    ):
        self.chat_sessions = _FakeSessionsCollection(session_tokens)
        self.ai_usage = _FakeUsageCollection(usage_tokens)
        self.users = _FakeUsersCollection(user_role)


def _reload_llm_module():
    """Re-import llm.py so module-level int(os.environ.get(...))
    constants pick up the monkeypatched env values. The check_budget
    helper reads SESSION_TOKEN_BUDGET / DAILY_USER_TOKEN_BUDGET as
    module attributes set at import time."""
    import services.ai.llm as llm_module
    return importlib.reload(llm_module)


# ---------------------------------------------------------------------------
# 1. Nova override applies on the Nova route
# ---------------------------------------------------------------------------


def test_nova_session_override_raises_when_nova_route(monkeypatch):
    """Nova route + NOVA_SESSION_TOKEN_BUDGET=1000; usage at 1500 →
    BudgetExceededError raised."""
    monkeypatch.setenv("AI_SESSION_TOKEN_BUDGET", "10000")
    monkeypatch.setenv("NOVA_SESSION_TOKEN_BUDGET", "1000")
    monkeypatch.setenv("AI_DAILY_TOKEN_BUDGET", "0")  # disable daily
    monkeypatch.setenv("AI_GLOBAL_DAILY_BUDGET", "0")  # disable global

    llm_module = _reload_llm_module()
    db = _FakeDb(session_tokens=1500)

    with pytest.raises(llm_module.BudgetExceededError):
        asyncio.run(
            llm_module.check_budget(db, "u_test", "sess_test", route="nova")
        )


def test_global_session_budget_holds_for_non_nova_route(monkeypatch):
    """Same env: AI_SESSION=10000, Nova=1000. With route="deals" the
    Nova override must NOT apply; usage at 1500 < AI_SESSION → no
    error."""
    monkeypatch.setenv("AI_SESSION_TOKEN_BUDGET", "10000")
    monkeypatch.setenv("NOVA_SESSION_TOKEN_BUDGET", "1000")
    monkeypatch.setenv("AI_DAILY_TOKEN_BUDGET", "0")
    monkeypatch.setenv("AI_GLOBAL_DAILY_BUDGET", "0")

    llm_module = _reload_llm_module()
    db = _FakeDb(session_tokens=1500)

    # No raise — Nova override is invisible to other routes.
    asyncio.run(
        llm_module.check_budget(db, "u_test", "sess_test", route="deals")
    )


def test_global_session_budget_holds_for_unspecified_route(monkeypatch):
    monkeypatch.setenv("AI_SESSION_TOKEN_BUDGET", "10000")
    monkeypatch.setenv("NOVA_SESSION_TOKEN_BUDGET", "1000")
    monkeypatch.setenv("AI_DAILY_TOKEN_BUDGET", "0")
    monkeypatch.setenv("AI_GLOBAL_DAILY_BUDGET", "0")

    llm_module = _reload_llm_module()
    db = _FakeDb(session_tokens=1500)
    # route=None preserves the legacy global-only behavior.
    asyncio.run(llm_module.check_budget(db, "u_test", "sess_test"))


# ---------------------------------------------------------------------------
# 2. Daily Nova override behaves the same way
# ---------------------------------------------------------------------------


def test_nova_daily_override_raises_when_nova_route(monkeypatch):
    monkeypatch.setenv("AI_SESSION_TOKEN_BUDGET", "0")
    monkeypatch.setenv("AI_DAILY_TOKEN_BUDGET", "10000")
    monkeypatch.setenv("NOVA_DAILY_TOKEN_BUDGET", "1000")
    monkeypatch.setenv("AI_GLOBAL_DAILY_BUDGET", "0")
    monkeypatch.setenv("AI_ADMIN_DAILY_TOKEN_BUDGET", "10000")

    llm_module = _reload_llm_module()
    db = _FakeDb(usage_tokens=1500, user_role="user")

    with pytest.raises(llm_module.BudgetExceededError):
        asyncio.run(
            llm_module.check_budget(db, "u_test", "sess_test", route="nova")
        )


def test_nova_daily_override_invisible_to_other_routes(monkeypatch):
    monkeypatch.setenv("AI_SESSION_TOKEN_BUDGET", "0")
    monkeypatch.setenv("AI_DAILY_TOKEN_BUDGET", "10000")
    monkeypatch.setenv("NOVA_DAILY_TOKEN_BUDGET", "1000")
    monkeypatch.setenv("AI_GLOBAL_DAILY_BUDGET", "0")
    monkeypatch.setenv("AI_ADMIN_DAILY_TOKEN_BUDGET", "10000")

    llm_module = _reload_llm_module()
    db = _FakeDb(usage_tokens=1500, user_role="user")
    # No raise — non-Nova route keeps the global daily budget.
    asyncio.run(
        llm_module.check_budget(db, "u_test", "sess_test", route="commissions")
    )


# ---------------------------------------------------------------------------
# 3. Invalid env values fall back to the global default
# ---------------------------------------------------------------------------


def test_invalid_nova_session_env_falls_back(monkeypatch):
    monkeypatch.setenv("AI_SESSION_TOKEN_BUDGET", "10000")
    monkeypatch.setenv("NOVA_SESSION_TOKEN_BUDGET", "not-a-number")
    monkeypatch.setenv("AI_DAILY_TOKEN_BUDGET", "0")
    monkeypatch.setenv("AI_GLOBAL_DAILY_BUDGET", "0")

    llm_module = _reload_llm_module()
    db = _FakeDb(session_tokens=1500)

    # Nova route + invalid override → falls back to AI_SESSION=10000,
    # so usage of 1500 is well under budget and the check passes.
    asyncio.run(
        llm_module.check_budget(db, "u_test", "sess_test", route="nova")
    )


def test_negative_nova_session_env_falls_back(monkeypatch):
    """A negative override is treated as 'not set' to avoid silently
    disabling the budget for Nova."""
    monkeypatch.setenv("AI_SESSION_TOKEN_BUDGET", "10000")
    monkeypatch.setenv("NOVA_SESSION_TOKEN_BUDGET", "-1")
    monkeypatch.setenv("AI_DAILY_TOKEN_BUDGET", "0")
    monkeypatch.setenv("AI_GLOBAL_DAILY_BUDGET", "0")

    llm_module = _reload_llm_module()
    db = _FakeDb(session_tokens=15000)

    # Falls back to AI_SESSION=10000; usage 15000 > 10000 → raises.
    with pytest.raises(llm_module.BudgetExceededError):
        asyncio.run(
            llm_module.check_budget(db, "u_test", "sess_test", route="nova")
        )


# ---------------------------------------------------------------------------
# 4. NOVA_MAX_OUTPUT_TOKENS — config parsing
# ---------------------------------------------------------------------------


def test_nova_max_output_tokens_parses_int(monkeypatch):
    from services.ai import config as ai_config

    monkeypatch.setenv("NOVA_MAX_OUTPUT_TOKENS", "4096")
    assert ai_config.nova_max_output_tokens() == 4096


def test_nova_max_output_tokens_invalid_returns_none(monkeypatch):
    from services.ai import config as ai_config

    monkeypatch.setenv("NOVA_MAX_OUTPUT_TOKENS", "lots")
    assert ai_config.nova_max_output_tokens() is None


def test_nova_max_output_tokens_unset_returns_none(monkeypatch):
    from services.ai import config as ai_config

    monkeypatch.delenv("NOVA_MAX_OUTPUT_TOKENS", raising=False)
    assert ai_config.nova_max_output_tokens() is None
