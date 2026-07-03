"""Regression lock. Nova build_system_prompt must not embed a
calendar date.

The output flows into Gemini explicit context caching and the
cache entry survives for up to TTL_SECONDS. Any baked in date
would freeze for that lifetime and the model would resolve
relative date phrases such as today and this month against a
stale anchor. The per turn current date lives in
wrap_user_message_for_nova which the conductor injects outside
the cache. This lock fails fast if a future edit re anchors the
date inside the cached system prompt body.

A previous test in tests/test_nova_prompt_date_freeness.py
covers the same contract for Commit B. This file is the
regression lock counterpart for Commit C. It is intentionally
narrow and matches the YYYY-MM-DD pattern only so a refactor
that splits the prompt into multiple parts is still caught.

Synthetic only.
"""
from __future__ import annotations

import asyncio
import os
import re
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest")

import pytest


_YYYYMMDD_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _make_synth_user():
    return SimpleNamespace(
        user_id="u_synth_c_dateless",
        email="u_synth_c_dateless@example.invalid",
        name="Synthetic User",
        role="contractor",
        permissions=["nova.use"],
        tenant_id=None,
    )


def test_build_system_prompt_emits_no_iso_date_substring():
    """The rendered prompt must not contain any YYYY-MM-DD value."""
    from services.ai.agents.nova import NovaAgent

    agent = NovaAgent()
    user = _make_synth_user()
    prompt = asyncio.run(agent.build_system_prompt(user))
    match = _YYYYMMDD_RE.search(prompt)
    assert match is None, (
        "Nova system prompt regression. A date literal "
        + repr(match.group(0) if match else None)
        + " has been reintroduced. The prompt feeds the Gemini cache"
        + " so any baked in date freezes for the cache TTL. Move the"
        + " current date into wrap_user_message_for_nova instead."
    )


def test_build_system_prompt_emits_no_iso_date_with_data_or_summary_args():
    """Same invariant when data and summary args are passed. The
    Nova path passes empty strings for these but the regression
    lock covers a future call site that supplies them."""
    from services.ai.agents.nova import NovaAgent

    agent = NovaAgent()
    user = _make_synth_user()
    prompt = asyncio.run(
        agent.build_system_prompt(
            user,
            data="synthetic data block",
            summary="synthetic summary block",
        )
    )
    assert _YYYYMMDD_RE.search(prompt) is None
