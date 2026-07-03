"""Tier-2 hard confirmation guard — v1.3 Theme B / Thread 1B.

Closes ADR Q2 from v1.2: confirmation for Tier-2 AI tools was prompt-only,
honored by the LLM but not enforced at dispatch. Prompt injection or
hallucinated auto-confirmation could let the LLM execute destructive tools
without user say-so. v1.3 enforces it in `execute_tool`.

What the guard does:
  * Inspects the most recent N (=2) user-role messages in the chat session.
  * Matches against a permissive word list (yes, go, confirm, approve, ...).
  * If no match: refuses dispatch with `{_blocked: True, error:
    "tier_2_confirmation_required", intended_params: ...}`.
  * Tier-1 tools auto-execute regardless of confirmation state.

These tests verify each branch.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.ai.agents.tools.conversational_tools import (
    TIER_2_TOOLS,
    TOOL_REGISTRY,
    _has_recent_confirmation,
    execute_tool,
)


# ---------------------------------------------------------------------------
# Helper: swap a TOOL_REGISTRY handler for the duration of a test.
#
# `_register_tool` stores a direct function reference, so monkeypatching the
# module attr does not redirect dispatch. We patch through the registry.
# ---------------------------------------------------------------------------
class _SwapHandler:
    def __init__(self, name: str, fake):
        self._name = name
        self._fake = fake
        self._orig = None

    def __enter__(self):
        self._orig = TOOL_REGISTRY[self._name]["handler"]
        TOOL_REGISTRY[self._name]["handler"] = self._fake
        return self._fake

    def __exit__(self, *exc):
        TOOL_REGISTRY[self._name]["handler"] = self._orig
        return False


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

def _make_user(perms=None):
    perms = perms or {
        "projects.task.delete",
        "projects.task.create",
        "projects.task.change_status",
        "projects.task.edit",
        "projects.project.create",
        "projects.task.assign",
    }
    return SimpleNamespace(
        user_id="u_pm_grace",
        email="grace@x.io",
        role="project_manager",
        name="Grace Holloway",
        permissions=perms,
    )


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._sort_dir = -1

    def sort(self, key, direction=-1):
        self._sort_dir = direction
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, n):
        return list(self._docs[:n])


class _ChatMessagesColl:
    """Returns a list of user messages newest-first."""

    def __init__(self, messages):
        self._msgs = list(messages)

    def find(self, query=None, projection=None, **_kw):
        # Filter for role=='user' and session_id if specified
        sid = (query or {}).get("session_id")
        role = (query or {}).get("role")
        out = []
        for m in self._msgs:
            if sid and m.get("session_id") != sid:
                continue
            if role and m.get("role") != role:
                continue
            out.append(m)
        # Sort by created_at desc (newest first)
        out.sort(key=lambda m: m.get("created_at", 0), reverse=True)
        return _Cursor(out)


class _AuditColl:
    def __init__(self):
        self.inserted: list = []

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return SimpleNamespace(inserted_id="stub")


class _Db:
    def __init__(self, messages=None):
        self.chat_messages = _ChatMessagesColl(messages or [])
        self.audit_logs = _AuditColl()


def run(coro):
    return asyncio.run(coro)


def _msg(content: str, ts: int, session_id: str = "chat_x", role: str = "user") -> dict:
    return {
        "session_id": session_id,
        "role": role,
        "content": content,
        "created_at": ts,
    }


# ---------------------------------------------------------------------------
# _has_recent_confirmation — unit-level
# ---------------------------------------------------------------------------

class TestConfirmationMatcher:
    def test_no_session_id_fails_closed(self):
        db = _Db()
        assert run(_has_recent_confirmation(db, None)) is False

    def test_empty_history_no_confirmation(self):
        db = _Db()
        assert run(_has_recent_confirmation(db, "chat_x")) is False

    def test_yes_in_last_user_message(self):
        db = _Db([_msg("yes, do it", ts=10)])
        assert run(_has_recent_confirmation(db, "chat_x")) is True

    def test_confirm_in_last_user_message(self):
        db = _Db([_msg("confirm", ts=10)])
        assert run(_has_recent_confirmation(db, "chat_x")) is True

    def test_go_word_boundary_matches(self):
        db = _Db([_msg("go ahead and delete it", ts=10)])
        assert run(_has_recent_confirmation(db, "chat_x")) is True

    def test_go_does_not_match_substring(self):
        # "going" should NOT trigger — the word-boundary regex requires
        # standalone tokens.
        db = _Db([_msg("I'm going to think about this", ts=10)])
        assert run(_has_recent_confirmation(db, "chat_x")) is False

    def test_yes_does_not_match_yesterday(self):
        db = _Db([_msg("yesterday I was about to delete it", ts=10)])
        assert run(_has_recent_confirmation(db, "chat_x")) is False

    def test_same_turn_confirmation_works(self):
        """'yes, delete task 42' in the SAME turn as the command counts."""
        db = _Db([_msg("yes, delete task 42", ts=10)])
        assert run(_has_recent_confirmation(db, "chat_x")) is True

    def test_confirmation_in_second_most_recent_turn(self):
        """Confirmation can be in either of the last 2 user turns."""
        db = _Db([
            _msg("yes", ts=10),                       # turn N-1
            _msg("now also delete task 99", ts=20),   # turn N (most recent)
        ])
        assert run(_has_recent_confirmation(db, "chat_x", turns=2)) is True

    def test_confirmation_too_far_back_misses(self):
        """Confirmation from 3 turns ago does NOT count (turns=2)."""
        db = _Db([
            _msg("yes", ts=5),                        # turn N-2 — out of window
            _msg("what's the status?", ts=10),        # turn N-1
            _msg("delete it", ts=20),                 # turn N
        ])
        assert run(_has_recent_confirmation(db, "chat_x", turns=2)) is False

    def test_question_does_not_match(self):
        db = _Db([_msg("can you delete task 42?", ts=10)])
        assert run(_has_recent_confirmation(db, "chat_x")) is False

    # ---- Wave 3.5: false-positive tightening -----------------------------
    # The Wave 3 review flagged three patterns the original matcher fired on
    # incorrectly. These tests pin the new sentence-tokenized behavior.

    def test_question_with_ok_does_not_match(self):
        """'is it ok if I change my mind?' is a question, not a confirmation,
        even though it contains the token 'ok'."""
        db = _Db([_msg("is it ok if I change my mind?", ts=10)])
        assert run(_has_recent_confirmation(db, "chat_x")) is False

    def test_negation_with_do_it_does_not_match(self):
        """'I wouldn't do it' contains 'do it' but is negated by 'wouldn't'."""
        db = _Db([_msg("I wouldn't do it", ts=10)])
        assert run(_has_recent_confirmation(db, "chat_x")) is False

    def test_ok_then_go_ahead_matches(self):
        """'ok, go ahead' is a clear confirmation."""
        db = _Db([_msg("ok, go ahead", ts=10)])
        assert run(_has_recent_confirmation(db, "chat_x")) is True

    def test_yes_do_it_with_period_matches(self):
        """'Yes, do it.' is a clear confirmation — single trailing sentence."""
        db = _Db([_msg("Yes, do it.", ts=10)])
        assert run(_has_recent_confirmation(db, "chat_x")) is True

    def test_sure_thing_please_proceed_matches(self):
        """'Sure thing, please proceed.' is a clear confirmation."""
        db = _Db([_msg("Sure thing, please proceed.", ts=10)])
        assert run(_has_recent_confirmation(db, "chat_x")) is True

    def test_wait_no_dont_proceed_does_not_match(self):
        """'Wait, no, don't proceed yet' contains 'proceed' but is negated."""
        db = _Db([_msg("Wait, no, don't proceed yet", ts=10)])
        assert run(_has_recent_confirmation(db, "chat_x")) is False

    def test_question_earlier_in_message_doesnt_taint_confirmation(self):
        """A multi-sentence message where the question is earlier and the
        last sentence is a confirmation should still count as confirmed."""
        db = _Db([_msg("is that the right one? Yes, do it.", ts=10)])
        assert run(_has_recent_confirmation(db, "chat_x")) is True

    def test_confirmation_earlier_in_message_with_trailing_negation_fails(self):
        """'Yes, but actually, do not proceed' — the trailing decision wins."""
        db = _Db([_msg("Yes. But actually, do not proceed.", ts=10)])
        assert run(_has_recent_confirmation(db, "chat_x")) is False


# ---------------------------------------------------------------------------
# execute_tool — Tier-2 hard guard at dispatch
# ---------------------------------------------------------------------------

class TestTier2HardGuard:
    @pytest.mark.asyncio
    async def test_tier_2_delete_blocked_without_confirmation(self):
        """delete_task is Tier-2; without confirmation it must be blocked."""
        db = _Db([_msg("delete task 42", ts=10)])  # no confirm token
        user = _make_user()

        # Swap the registered handler so we can prove it was NOT called.
        mock_handler = AsyncMock()
        with _SwapHandler("delete_task", mock_handler):
            result = await execute_tool(
                "delete_task",
                {"task_id": "task_42"},
                db, user, session_id="chat_x",
            )

        assert result.get("_blocked") is True
        assert result["error"] == "tier_2_confirmation_required"
        assert result["tool"] == "delete_task"
        assert result["intended_params"] == {"task_id": "task_42"}
        mock_handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_tier_2_delete_proceeds_with_yes_confirmation(self):
        """When the last user turn has 'yes', delete_task dispatches."""
        db = _Db([_msg("yes, delete task 42", ts=10)])
        user = _make_user()

        mock_handler = AsyncMock(return_value={"ok": True, "deleted_id": "task_42", "tier": 2})
        with _SwapHandler("delete_task", mock_handler):
            result = await execute_tool(
                "delete_task",
                {"task_id": "task_42"},
                db, user, session_id="chat_x",
            )

        assert result.get("_blocked") is not True
        assert result.get("ok") is True
        mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_tier_2_create_project_blocked_without_confirmation(self):
        db = _Db([_msg("create a project for Henderson", ts=10)])
        user = _make_user()

        mock_handler = AsyncMock()
        with _SwapHandler("create_project", mock_handler):
            result = await execute_tool(
                "create_project",
                {"name": "Henderson Q3", "methodology": "waterfall"},
                db, user, session_id="chat_x",
            )

        assert result["error"] == "tier_2_confirmation_required"
        mock_handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_tier_2_bulk_blocked_without_confirmation(self):
        db = _Db([_msg("move all overdue tasks to in_progress", ts=10)])
        user = _make_user()

        mock_handler = AsyncMock()
        with _SwapHandler("bulk_update_task_status", mock_handler):
            result = await execute_tool(
                "bulk_update_task_status",
                {"task_ids": ["t1", "t2"], "status": "in_progress"},
                db, user, session_id="chat_x",
            )

        assert result["error"] == "tier_2_confirmation_required"

    @pytest.mark.asyncio
    async def test_tier_1_assign_task_passes_without_confirmation(self):
        """assign_task is Tier-1 — no confirmation required, auto-executes."""
        db = _Db([_msg("assign task 42 to Marcus", ts=10)])
        user = _make_user()

        mock_handler = AsyncMock(return_value={"ok": True, "tier": 1})
        with _SwapHandler("assign_task", mock_handler):
            result = await execute_tool(
                "assign_task",
                {"task_id": "task_42", "user_id_or_name": "Marcus"},
                db, user, session_id="chat_x",
            )

        assert result.get("_blocked") is not True
        mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_tier_1_update_status_passes_without_confirmation(self):
        """update_task_status is Tier-1 — bypasses the guard."""
        db = _Db([])  # no chat history at all
        user = _make_user()

        mock_handler = AsyncMock(return_value={"ok": True, "tier": 1})
        with _SwapHandler("update_task_status", mock_handler):
            result = await execute_tool(
                "update_task_status",
                {"task_id": "task_42", "status": "complete"},
                db, user, session_id="chat_x",
            )

        assert result.get("_blocked") is not True
        mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_tier_2_blocked_when_session_id_is_none(self):
        """No session_id → can't verify confirmation → fail closed."""
        db = _Db([_msg("yes", ts=10)])
        user = _make_user()

        mock_handler = AsyncMock()
        with _SwapHandler("delete_task", mock_handler):
            result = await execute_tool(
                "delete_task",
                {"task_id": "task_42"},
                db, user, session_id=None,
            )

        assert result["error"] == "tier_2_confirmation_required"
        mock_handler.assert_not_called()


# ---------------------------------------------------------------------------
# Sanity: TIER_2_TOOLS set matches the prompt + handler tiers
# ---------------------------------------------------------------------------

class TestTier2SetSanity:
    def test_destructive_tools_in_set(self):
        assert "delete_task" in TIER_2_TOOLS
        assert "create_project" in TIER_2_TOOLS
        assert "create_project_from_template" in TIER_2_TOOLS
        assert "create_task" in TIER_2_TOOLS
        assert "bulk_update_task_status" in TIER_2_TOOLS
        assert "bulk_assign_tasks" in TIER_2_TOOLS

    def test_reversible_tools_not_in_set(self):
        # Tier-1 tools must NOT be in the Tier-2 set.
        assert "update_task_status" not in TIER_2_TOOLS
        assert "update_task_field" not in TIER_2_TOOLS
        assert "assign_task" not in TIER_2_TOOLS
        assert "add_task_comment" not in TIER_2_TOOLS
        assert "summarize_recent_changes" not in TIER_2_TOOLS
