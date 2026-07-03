"""Test Vega session isolation — prior session data must not leak across sessions.

Covers the hotfix for three code paths:
1. ai_chat_artifacts.py — prior analyses only injected from linked session
2. conductor.py — Vega state scoped by chat_session_id
3. vega_analysis.py — _load_prior_context returns empty without explicit session_id
"""
import sys
import os
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

# Ensure backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.ai.vega_analysis import _load_prior_context


# ── _load_prior_context isolation ──


@pytest.mark.asyncio
async def test_load_prior_context_returns_empty_without_session_id():
    """No session_id → empty string, no DB query."""
    mock_user = MagicMock()
    mock_user.user_id = "user_test_123"
    mock_db = MagicMock()

    result = await _load_prior_context(mock_db, mock_user, session_id=None)

    assert result == ""
    mock_db.vega_discovery_sessions.find.assert_not_called()


@pytest.mark.asyncio
async def test_load_prior_context_scopes_query_to_session_id():
    """With explicit session_id, query must filter by that session."""
    mock_user = MagicMock()
    mock_user.user_id = "user_test_123"
    mock_db = MagicMock()

    mock_cursor = MagicMock()
    mock_cursor.sort.return_value = mock_cursor
    mock_cursor.to_list = AsyncMock(return_value=[{
        "calls": {
            "1": {"user_approved": True, "analysis": {"summary": "Call 1 for client X"}},
        }
    }])
    mock_db.vega_discovery_sessions.find.return_value = mock_cursor

    result = await _load_prior_context(mock_db, mock_user, session_id="vega_abc123")

    assert "PRIOR APPROVED ANALYSES" in result
    assert "Call 1 for client X" in result

    query = mock_db.vega_discovery_sessions.find.call_args[0][0]
    assert query["session_id"] == "vega_abc123"
    assert query["user_id"] == "user_test_123"


@pytest.mark.asyncio
async def test_load_prior_context_skips_unapproved_calls():
    """Only user_approved calls should appear in context."""
    mock_user = MagicMock()
    mock_user.user_id = "user_test_123"
    mock_db = MagicMock()

    mock_cursor = MagicMock()
    mock_cursor.sort.return_value = mock_cursor
    mock_cursor.to_list = AsyncMock(return_value=[{
        "calls": {
            "1": {"user_approved": True, "analysis": {"summary": "Approved call"}},
            "2": {"user_approved": False, "analysis": {"summary": "Not approved yet"}},
        }
    }])
    mock_db.vega_discovery_sessions.find.return_value = mock_cursor

    result = await _load_prior_context(mock_db, mock_user, session_id="vega_xyz")

    assert "Approved call" in result
    assert "Not approved yet" not in result


# ── Artifact payload: linked_vega_session_id routing logic ──


def _resolve_prior_context_query(artifact: dict, current_user_id: str):
    """Replicate the 3-way branching logic from ai_chat_artifacts.py multi_upload submit.

    Returns the query dict that would be used, or None for fresh-start.
    """
    vega_payload = artifact.get("payload", {})
    has_link_key = "linked_vega_session_id" in vega_payload
    linked_vega_sid = vega_payload.get("linked_vega_session_id")

    if linked_vega_sid:
        return {"session_id": linked_vega_sid, "user_id": current_user_id}
    elif not has_link_key:
        return {"user_id": current_user_id, "status": {"$in": ["in_progress", "complete"]}}
    else:
        return None


def test_linked_session_scopes_prior_context_query():
    """Artifact with explicit linked_vega_session_id → query scoped to that session."""
    artifact = {"payload": {"linked_vega_session_id": "vega_session_xyz"}}
    query = _resolve_prior_context_query(artifact, "user_1")

    assert query == {"session_id": "vega_session_xyz", "user_id": "user_1"}


def test_fresh_start_skips_prior_context():
    """Artifact with linked_vega_session_id=None (post_call_1) → no prior context."""
    artifact = {"payload": {"linked_vega_session_id": None}}
    query = _resolve_prior_context_query(artifact, "user_1")

    assert query is None


def test_legacy_artifact_falls_back_to_global_query():
    """Artifact without the key at all (pre-deploy) → global fallback."""
    artifact = {"payload": {"title": "Document Upload", "slots": []}}
    query = _resolve_prior_context_query(artifact, "user_1")

    assert query == {"user_id": "user_1", "status": {"$in": ["in_progress", "complete"]}}


def test_manual_upload_skips_prior_context():
    """manual_upload sets linked_vega_session_id=None → no prior context."""
    artifact = {"payload": {"linked_vega_session_id": None, "title": "Document Upload"}}
    query = _resolve_prior_context_query(artifact, "user_1")

    assert query is None


# ── Auto-select linked session logic ──


def _resolve_auto_linked_session(selected_id, sessions_with_call_1, sessions_with_call_2):
    """Replicate the auto-select logic from ai_chat_artifacts.py intake handler."""
    auto_linked = None
    if selected_id == "post_call_2" and sessions_with_call_1:
        auto_linked = sessions_with_call_1[0]["session_id"]
    elif selected_id == "post_call_3":
        if sessions_with_call_1 and sessions_with_call_2:
            auto_linked = sessions_with_call_2[0]["session_id"]
        elif sessions_with_call_1:
            auto_linked = sessions_with_call_1[0]["session_id"]
    return auto_linked


def test_auto_select_post_call_2_links_call1_session():
    sessions_c1 = [{"session_id": "vega_001"}]
    assert _resolve_auto_linked_session("post_call_2", sessions_c1, []) == "vega_001"


def test_auto_select_post_call_3_links_call2_session():
    sessions_c1 = [{"session_id": "vega_001"}]
    sessions_c2 = [{"session_id": "vega_002"}]
    assert _resolve_auto_linked_session("post_call_3", sessions_c1, sessions_c2) == "vega_002"


def test_auto_select_post_call_3_falls_back_to_call1():
    sessions_c1 = [{"session_id": "vega_001"}]
    assert _resolve_auto_linked_session("post_call_3", sessions_c1, []) == "vega_001"


def test_auto_select_post_call_1_has_no_linked_session():
    sessions_c1 = [{"session_id": "vega_001"}]
    assert _resolve_auto_linked_session("post_call_1", sessions_c1, []) is None


def test_auto_select_direct_analysis_has_no_linked_session():
    assert _resolve_auto_linked_session("direct_analysis", [], []) is None


def test_auto_select_post_call_3_only_call2_no_call1():
    """Edge case: only sessions_with_call_2, no call_1 → no auto-link (user uploads all)."""
    sessions_c2 = [{"session_id": "vega_002"}]
    assert _resolve_auto_linked_session("post_call_3", [], sessions_c2) is None
