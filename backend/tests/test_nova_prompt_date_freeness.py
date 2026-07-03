"""Tests for Nova system prompt date freeness.

PR cleanup B4. The Nova system prompt is fed into Gemini explicit
context caching. The cache entry is reused for up to TTL_SECONDS so
the cached body must not carry today's date. A baked in date would
freeze for the cache lifetime and the model would resolve relative
date phrases such as today and this month against a stale anchor.

The fix is twofold. build_system_prompt no longer renders any
YYYY-MM-DD value. A per turn wrapper wrap_user_message_for_nova
prepends a Today is YYYY-MM-DD preamble to the user message which
the conductor injects outside the cache before every Nova LLM call.

These tests lock both halves of the contract so a future edit that
re anchors the date inside the system prompt fails loudly.

Synthetic only. Date fixtures use a clearly fake far future year.
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import date
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest")

import pytest


_YYYYMMDD_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _make_synth_user():
    return SimpleNamespace(
        user_id="u_synth_b4",
        email="u_synth_b4@example.invalid",
        name="Synthetic User",
        role="contractor",
        permissions=["nova.use"],
        tenant_id=None,
    )


def test_build_system_prompt_contains_no_full_date():
    """The rendered system prompt must not contain a YYYY-MM-DD
    substring. Any such value would survive in the Gemini cache for
    TTL_SECONDS and freeze relative date resolution."""
    from services.ai.agents.nova import NovaAgent

    agent = NovaAgent()
    user = _make_synth_user()
    prompt = asyncio.run(agent.build_system_prompt(user))
    assert isinstance(prompt, str)
    match = _YYYYMMDD_RE.search(prompt)
    assert match is None, (
        "system prompt unexpectedly contains date literal "
        + repr(match.group(0) if match else None)
        + ". The prompt must stay date free so it can be cached."
    )


def test_wrap_user_message_for_nova_injects_fixed_date():
    """The wrapper must render today's date as a preamble line. A
    fixed test date is passed in to keep this deterministic."""
    from services.ai.agents.nova import wrap_user_message_for_nova

    fixed_today = date(2099, 7, 4)
    wrapped = wrap_user_message_for_nova(
        "what is my cash flow next month",
        today=fixed_today,
    )
    assert "Today is 2099-07-04" in wrapped
    assert "what is my cash flow next month" in wrapped


def test_wrap_user_message_for_nova_preamble_is_first():
    """The preamble must precede the user message. The model reads
    instructions top down so the date anchor needs to land before
    the question."""
    from services.ai.agents.nova import wrap_user_message_for_nova

    fixed_today = date(2099, 7, 4)
    wrapped = wrap_user_message_for_nova("ping", today=fixed_today)
    preamble_pos = wrapped.index("Today is 2099-07-04")
    user_msg_pos = wrapped.index("ping")
    assert preamble_pos < user_msg_pos


def test_wrap_user_message_for_nova_handles_empty_user_message():
    """Empty or non string input still returns the preamble alone.
    Callers can invoke the wrapper without a None guard."""
    from services.ai.agents.nova import wrap_user_message_for_nova

    fixed_today = date(2099, 7, 4)
    assert wrap_user_message_for_nova("", today=fixed_today) == (
        "Today is 2099-07-04. Resolve every relative date phrase"
        " such as today and this month and next year against this"
        " date. Do not use any other year. When the user provides"
        " explicit YYYY-MM or YYYY-MM-DD values use them verbatim."
    )
    assert wrap_user_message_for_nova(None, today=fixed_today).startswith(
        "Today is 2099-07-04"
    )


def test_wrap_user_message_for_nova_default_today_is_now_utc():
    """When no fixed date is passed the wrapper anchors to the
    current UTC date. Spot check the shape and that the date is
    recent enough to be today."""
    from datetime import datetime, timezone

    from services.ai.agents.nova import wrap_user_message_for_nova

    before = datetime.now(timezone.utc).date()
    wrapped = wrap_user_message_for_nova("ping")
    after = datetime.now(timezone.utc).date()
    match = _YYYYMMDD_RE.search(wrapped)
    assert match is not None
    iso = match.group(0)
    assert iso in {before.isoformat(), after.isoformat()}, (
        "wrapper today should match wall clock UTC date"
    )


# ---------------------------------------------------------------------------
# G5. Logger warning on a non string or empty user_message.
# ---------------------------------------------------------------------------


def test_wrap_user_message_for_nova_logs_warning_on_none(caplog):
    """A None user_message must still return the preamble unchanged
    AND log a single warning naming the actual type so the upstream
    bug is visible instead of silently swallowed."""
    import logging
    from datetime import date

    from services.ai.agents.nova import (
        wrap_user_message_for_nova,
        logger as nova_logger,
    )

    fixed_today = date(2099, 7, 4)
    with caplog.at_level(logging.WARNING, logger=nova_logger.name):
        result = wrap_user_message_for_nova(None, today=fixed_today)
    assert result.startswith("Today is 2099-07-04")
    captured = " ".join(rec.getMessage() for rec in caplog.records)
    assert "wrap_user_message_for_nova received non string or empty" in captured
    assert "NoneType" in captured


def test_wrap_user_message_for_nova_logs_warning_on_empty_string(caplog):
    """An empty string user_message must also log a warning naming
    the actual type str. The preamble is returned unchanged."""
    import logging
    from datetime import date

    from services.ai.agents.nova import (
        wrap_user_message_for_nova,
        logger as nova_logger,
    )

    fixed_today = date(2099, 7, 4)
    with caplog.at_level(logging.WARNING, logger=nova_logger.name):
        result = wrap_user_message_for_nova("", today=fixed_today)
    assert result.startswith("Today is 2099-07-04")
    captured = " ".join(rec.getMessage() for rec in caplog.records)
    assert "wrap_user_message_for_nova received non string or empty" in captured
    assert "str" in captured


def test_wrap_user_message_for_nova_no_warning_on_valid_user_message(caplog):
    """A non empty string user_message must NOT log a warning. The
    silent path is reserved for the unexpected types."""
    import logging
    from datetime import date

    from services.ai.agents.nova import (
        wrap_user_message_for_nova,
        logger as nova_logger,
    )

    fixed_today = date(2099, 7, 4)
    with caplog.at_level(logging.WARNING, logger=nova_logger.name):
        wrap_user_message_for_nova("a real question", today=fixed_today)
    captured = " ".join(rec.getMessage() for rec in caplog.records)
    assert "wrap_user_message_for_nova" not in captured
