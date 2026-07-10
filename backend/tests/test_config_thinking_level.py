"""Locks GEMINI_THINKING_LEVEL parsing across the three states.

  * Env var unset returns None silently with no warning log.
  * Env var equal to the literal value none is treated as
    equivalent to unset. Returns None silently with no warning log.
    Case insensitive so NONE behaves the same.
  * Env var with an unrecognized value returns None and logs a
    single warning naming the offender so the operator can fix it.
  * The four documented valid values minimal, low, medium, high
    round trip unchanged.

none is intentionally NOT added to the valid set itself because
downstream consumers of the level should not see it as a valid
level value.
"""
from __future__ import annotations

import logging
import os

os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest")

import pytest


def test_thinking_level_unset_returns_none_silently(monkeypatch, caplog):
    from services.ai.config import _parse_thinking_level_env, logger as cfg_logger

    monkeypatch.delenv("GEMINI_THINKING_LEVEL", raising=False)
    with caplog.at_level(logging.WARNING, logger=cfg_logger.name):
        assert _parse_thinking_level_env() is None
    assert "Invalid GEMINI_THINKING_LEVEL" not in " ".join(
        rec.getMessage() for rec in caplog.records
    )


def test_thinking_level_none_returns_none_silently(monkeypatch, caplog):
    """Literal value none must be treated as unset with no warning."""
    from services.ai.config import _parse_thinking_level_env, logger as cfg_logger

    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "none")
    with caplog.at_level(logging.WARNING, logger=cfg_logger.name):
        assert _parse_thinking_level_env() is None
    assert "Invalid GEMINI_THINKING_LEVEL" not in " ".join(
        rec.getMessage() for rec in caplog.records
    )

    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "NONE")
    with caplog.at_level(logging.WARNING, logger=cfg_logger.name):
        assert _parse_thinking_level_env() is None


def test_thinking_level_garbage_returns_none_with_warning(monkeypatch, caplog):
    from services.ai.config import _parse_thinking_level_env, logger as cfg_logger

    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "garbage")
    with caplog.at_level(logging.WARNING, logger=cfg_logger.name):
        assert _parse_thinking_level_env() is None
    captured = " ".join(rec.getMessage() for rec in caplog.records)
    assert "Invalid GEMINI_THINKING_LEVEL" in captured


def test_thinking_level_valid_values_round_trip(monkeypatch):
    from services.ai.config import _parse_thinking_level_env

    for level in ("minimal", "low", "medium", "high"):
        monkeypatch.setenv("GEMINI_THINKING_LEVEL", level)
        assert _parse_thinking_level_env() == level
