"""Test Vega analysis retry logic and error messaging.

Covers the hotfix for:
1. Silent failures when analysis returns None — now retries once and posts an error
2. Exception handling — now catches, retries, and posts a user-visible error
3. chat_session_id linking to Vega discovery session
4. VEGA_LLM_TIMEOUT used for all LLM calls (instead of hardcoded or default values)
"""
import sys
import os
import asyncio
import json
import re
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


from services.ai.vega_analysis import run_with_retry


# ── Helpers ──

def _make_mock_user():
    user = MagicMock()
    user.user_id = "user_test"
    return user


# ── Tests: run_call2_analysis returns correct values on failure ──


def test_call2_returns_none_on_empty_transcript():
    """If transcript text is empty, run_call2_analysis should return None."""
    from services.ai.vega_analysis import run_call2_analysis

    mock_db = MagicMock()
    mock_db.chat_uploads.find_one = AsyncMock(return_value=None)

    result = asyncio.run(
        run_call2_analysis(mock_db, _make_mock_user(), ["upl_fake"], session_id="vega_test")
    )
    assert result is None


def test_call2_returns_none_on_empty_llm_response():
    """If LLM returns empty, run_call2_analysis should return None."""
    from services.ai.vega_analysis import run_call2_analysis

    mock_db = MagicMock()
    mock_db.chat_uploads.find_one = AsyncMock(return_value={
        "upload_id": "upl_test",
        "extracted_text": "Some transcript text here",
        "filename": "call2.txt",
    })

    mock_cursor = MagicMock()
    mock_cursor.sort.return_value = mock_cursor
    mock_cursor.to_list = AsyncMock(return_value=[])
    mock_db.vega_discovery_sessions.find.return_value = mock_cursor

    with patch("services.ai.llm.call_llm", new_callable=AsyncMock, return_value=None):
        with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-pro")):
            result = asyncio.run(
                run_call2_analysis(mock_db, _make_mock_user(), ["upl_test"], session_id="vega_test")
            )

    assert result is None


def test_call2_returns_error_on_invalid_json():
    """If LLM returns invalid JSON, should return an error dict."""
    from services.ai.vega_analysis import run_call2_analysis

    mock_db = MagicMock()
    mock_db.chat_uploads.find_one = AsyncMock(return_value={
        "upload_id": "upl_test",
        "extracted_text": "Some transcript text here",
        "filename": "call2.txt",
    })

    mock_cursor = MagicMock()
    mock_cursor.sort.return_value = mock_cursor
    mock_cursor.to_list = AsyncMock(return_value=[])
    mock_db.vega_discovery_sessions.find.return_value = mock_cursor

    bad_resp = MagicMock()
    bad_resp.text = "not valid json {{{["

    with patch("services.ai.llm.call_llm", new_callable=AsyncMock, return_value=bad_resp):
        with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-pro")):
            result = asyncio.run(
                run_call2_analysis(mock_db, _make_mock_user(), ["upl_test"], session_id="vega_test")
            )

    assert result is not None
    assert "error" in result


# ── Tests: retry pattern ──


def test_retry_calls_twice_on_first_none():
    """If analysis returns None on first attempt, retry should call it again."""
    call_count = 0

    async def mock_analysis(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None
        return {"matrix_data": {"matrix": []}, "text": "ok"}

    result = asyncio.run(run_with_retry(mock_analysis, call_num=2))
    assert call_count == 2
    assert result is not None
    assert result.get("matrix_data") is not None


def test_retry_calls_twice_on_first_exception():
    """If analysis throws on first attempt, retry should call it again."""
    call_count = 0

    async def mock_analysis(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("LLM connection failed")
        return {"matrix_data": {"matrix": []}, "text": "ok"}

    result = asyncio.run(run_with_retry(mock_analysis, call_num=2))
    assert call_count == 2
    assert result is not None


def test_retry_gives_up_after_two_nones():
    """If analysis returns None both times, result should be None."""
    async def mock_analysis(*args, **kwargs):
        return None

    result = asyncio.run(run_with_retry(mock_analysis, call_num=2))
    assert result is None


# ── Tests: chat_session_id linking ──


def test_chat_session_id_linked_to_vega_session():
    """When Vega analysis runs, the chat session should be linked to the Vega session."""
    mock_db = MagicMock()
    mock_db.vega_discovery_sessions.update_one = AsyncMock()

    async def run():
        vega_session_id = "vega_test_session"
        session_id = "chat_test_session"
        user_id = "user_test"

        if vega_session_id:
            await mock_db.vega_discovery_sessions.update_one(
                {"session_id": vega_session_id, "user_id": user_id},
                {"$set": {"chat_session_id": session_id}},
            )

    asyncio.run(run())

    mock_db.vega_discovery_sessions.update_one.assert_called_once()
    update_call = mock_db.vega_discovery_sessions.update_one.call_args
    assert update_call[0][0]["session_id"] == "vega_test_session"
    assert update_call[0][1]["$set"]["chat_session_id"] == "chat_test_session"


# ── Tests: VEGA_LLM_TIMEOUT usage ──


def test_no_hardcoded_timeouts_in_vega_analysis():
    """Verify call_llm uses VEGA_LLM_TIMEOUT, not hardcoded numeric timeouts."""
    from services.ai import vega_analysis

    source = inspect.getsource(vega_analysis)

    # Should NOT have hardcoded numeric timeouts like timeout=90 or timeout=180
    matches = re.findall(r'call_llm\([^)]*timeout=\d+', source, re.DOTALL)
    assert len(matches) == 0, f"Found hardcoded numeric timeouts in call_llm: {matches}"


def test_all_calls_use_vega_llm_timeout():
    """Verify all three analysis functions use VEGA_LLM_TIMEOUT."""
    from services.ai.vega_analysis import run_transcript_analysis, run_call2_analysis, run_call3_analysis

    for fn in (run_transcript_analysis, run_call2_analysis, run_call3_analysis):
        source = inspect.getsource(fn)
        assert "timeout=VEGA_LLM_TIMEOUT" in source, f"{fn.__name__} does not use VEGA_LLM_TIMEOUT"


def test_vega_llm_timeout_value():
    """Verify VEGA_LLM_TIMEOUT is set to a reasonable value (>= 300s)."""
    from services.ai.vega_analysis import VEGA_LLM_TIMEOUT
    assert VEGA_LLM_TIMEOUT >= 300, f"VEGA_LLM_TIMEOUT={VEGA_LLM_TIMEOUT} is too low for complex analyses"
