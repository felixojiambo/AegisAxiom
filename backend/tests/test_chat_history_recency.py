"""Regression test for chat.py history-loading bug.

Before the fix: chat.py sorted chat_messages ascending and capped at 25/50,
which for any session over the cap dispatched only the OLDEST window —
the user's freshly-persisted turn was never in history. Vega's intent
classifier would read a stale message as "latest user turn", graduation
never fired, and long resumed sessions felt like Vega had forgotten.

This test simulates the slice directly against an in-memory list of
50 messages and verifies the new (sort: -1, limit: cap, reverse) path
returns the most recent 25 in chronological order, with the freshly-
persisted message at the tail.
"""
from datetime import datetime, timezone, timedelta


def _build_messages(n: int) -> list[dict]:
    """Build n chronologically-ordered messages, alternating roles, with timestamps."""
    base = datetime(2026, 5, 14, 8, 0, tzinfo=timezone.utc)
    return [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"msg-{i:02d}",
            "created_at": base + timedelta(seconds=i),
        }
        for i in range(n)
    ]


class _Cursor:
    """Minimal motor-style chainable cursor for the slice under test."""

    def __init__(self, msgs: list[dict]):
        self._msgs = msgs
        self._sort_key = None
        self._sort_dir = 1
        self._limit = None

    def sort(self, key, direction):
        self._sort_key = key
        self._sort_dir = direction
        return self

    def limit(self, n):
        self._limit = n
        return self

    async def to_list(self, n):
        ordered = sorted(
            self._msgs,
            key=lambda m: m[self._sort_key],
            reverse=(self._sort_dir == -1),
        )
        if self._limit:
            ordered = ordered[:self._limit]
        return ordered[:n]


class _FakeCollection:
    def __init__(self, msgs: list[dict]):
        self._msgs = msgs

    def find(self, *_args, **_kwargs):
        return _Cursor(self._msgs)


async def _load_history_under_test(coll, history_cap: int) -> list[dict]:
    """Mirror chat.py's fixed history-loading slice exactly."""
    history = await coll.find(
        {"session_id": "s"}, {"_id": 0, "role": 1, "content": 1}
    ).sort("created_at", -1).limit(history_cap).to_list(history_cap)
    history.reverse()
    return history


class TestChatHistoryRecency:
    """Lock down the post-fix history-loading semantics."""

    async def test_long_session_returns_most_recent_window(self):
        """50-msg Vega session — must return msgs 25..49 (newest 25), in chronological order."""
        msgs = _build_messages(50)
        coll = _FakeCollection(msgs)

        history = await _load_history_under_test(coll, 25)

        assert len(history) == 25, f"expected 25 messages, got {len(history)}"
        # Chronological order preserved (oldest first within the window).
        assert history[0]["content"] == "msg-25", f"first should be msg-25, got {history[0]!r}"
        assert history[-1]["content"] == "msg-49", f"last should be msg-49, got {history[-1]!r}"
        # The user's freshly-persisted latest turn is at the tail — this is the
        # property dispatch.handle's `reversed(history)` walk depends on to
        # extract `last_user_message`.
        assert any(m["role"] == "user" for m in history[-2:]), \
            "tail must include a user turn — intent classifier reads this as 'latest user message'"

    async def test_short_session_returns_everything(self):
        """10-msg session — cap doesn't truncate, full history returned in order."""
        msgs = _build_messages(10)
        coll = _FakeCollection(msgs)

        history = await _load_history_under_test(coll, 25)

        assert len(history) == 10
        assert history[0]["content"] == "msg-00"
        assert history[-1]["content"] == "msg-09"

    async def test_exact_cap_returns_full_window(self):
        """25-msg session — exact cap, all returned in order."""
        msgs = _build_messages(25)
        coll = _FakeCollection(msgs)

        history = await _load_history_under_test(coll, 25)

        assert len(history) == 25
        assert history[0]["content"] == "msg-00"
        assert history[-1]["content"] == "msg-24"

    async def test_empty_session(self):
        """0 msgs — empty history, no crash."""
        coll = _FakeCollection([])
        history = await _load_history_under_test(coll, 25)
        assert history == []


# pytest-asyncio glue
import pytest


@pytest.fixture(autouse=True)
def _enable_asyncio_for_tests():
    pass


pytestmark = pytest.mark.asyncio
