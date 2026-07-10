"""Tests for the Wave 6b conversational call_num swap in ai_chat_artifacts.

Covers the dispatcher in ``routes/ai_chat_artifacts.py``'s ``submit_artifact``
multi_upload branch:

  * Conversational call_num=1 routes to ``run_v3_call1`` and persists as
    ``discovery_analysis_v3_call1`` with ``discovery_mode_origin`` recorded.
  * Conversational call_num=2 looks up the most recent
    ``discovery_analysis_v3_call1`` artifact for the linked Vega session and
    passes its id to ``run_v3_call2`` as ``prior_call1_artifact_id``.
  * Conversational call_num=3 looks up the most recent
    ``discovery_analysis_v3_call2`` artifact and passes it to ``run_v3_call3``.
  * Conversational call_num=2 with NO prior Call 1 artifact still runs (just
    without prior context) — that's a valid "fresh Call 2" mode.
  * Chip-driven legacy paths (``discovery_mode_origin="chip_legacy"`` or
    absent entirely) still route to the v2 ``run_transcript_analysis`` /
    ``run_call2_analysis`` / ``run_call3_analysis`` and persist as
    ``transcript_analysis`` / ``ai_employee_matrix``.

Strategy: monkeypatch the v3_call_modes + vega_analysis entry points so we
can assert routing decisions without running the LLM. The dispatcher is
exercised by calling ``submit_artifact`` directly with a stubbed
``get_db`` / ``current_user``.
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("JWT_SECRET", "test-secret")

from routes import ai_chat_artifacts as route_mod
from services.vega.models_v3 import (
    DiscoveryAnalysisV3,
    PipelineMetadata,
    SessionFlags,
)


# ──────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ──────────────────────────────────────────────────────────────────────


def _make_user(user_id: str = "user_test"):
    user = MagicMock()
    user.user_id = user_id
    user.permissions = ["chat.access"]
    user._permission_closure = None
    user._perm_closure = None
    user.name = "Test User"
    return user


def _make_v3_artifact(call_mode: str) -> DiscoveryAnalysisV3:
    return DiscoveryAnalysisV3(
        client_company="Acme",
        workflows=[],
        apps=[],
        session_flags=SessionFlags(),
        open_items=[],
        extraction_status="complete",
        pipeline_metadata=PipelineMetadata(call_mode=call_mode),
    )


class _DBState:
    """Capture inserts + the cursor returned by chat_messages.find."""

    def __init__(
        self,
        *,
        artifact_payload: dict,
        vega_session: dict | None = None,
        prior_v3_call_artifact_id: str | None = None,
        prior_call_n: int | None = None,
        chat_messages: list[dict] | None = None,
        chat_session_doc: dict | None = None,
    ):
        self.artifact_payload = artifact_payload
        self.vega_session = vega_session
        self.prior_v3_call_artifact_id = prior_v3_call_artifact_id
        self.prior_call_n = prior_call_n
        self.chat_messages = chat_messages or []
        self.chat_session_doc = chat_session_doc or {}

        self.inserted_artifacts: list[dict] = []
        self.inserted_messages: list[dict] = []


def _make_db(state: _DBState):
    db = MagicMock()

    artifact_doc = {
        "artifact_id": "art_test",
        "session_id": "sess_test",
        "user_id": "user_test",
        "employee_id": "ai_vega",
        "artifact_type": "multi_upload",
        "payload": state.artifact_payload,
        "status": "delivered",
    }

    async def _ai_chat_artifacts_find_one(query, projection=None, sort=None, **kw):
        # find_one calls without sort kwarg — top-level lookup of THIS artifact.
        if sort is None and query.get("artifact_id") == "art_test":
            return artifact_doc
        # Wave 6b prior-artifact lookup uses sort=[("created_at", -1)] +
        # artifact_type=discovery_analysis_v3_call{N}.
        atype = query.get("artifact_type")
        if atype and state.prior_call_n is not None and atype == f"discovery_analysis_v3_call{state.prior_call_n}":
            if state.prior_v3_call_artifact_id:
                return {"artifact_id": state.prior_v3_call_artifact_id}
            return None
        if atype and atype.startswith("discovery_analysis_v3_call"):
            return None  # no prior found for any call N
        return None

    db.ai_chat_artifacts.find_one = AsyncMock(side_effect=_ai_chat_artifacts_find_one)

    async def _ai_chat_artifacts_find_one_and_update(query, update, **kw):
        # Multi_upload claim — succeeds.
        return artifact_doc

    db.ai_chat_artifacts.find_one_and_update = AsyncMock(
        side_effect=_ai_chat_artifacts_find_one_and_update,
    )

    async def _ai_chat_artifacts_insert_one(doc):
        state.inserted_artifacts.append(doc)
        return MagicMock()

    db.ai_chat_artifacts.insert_one = AsyncMock(
        side_effect=_ai_chat_artifacts_insert_one,
    )

    # chat_uploads count_documents — the cross-session contamination guard.
    db.chat_uploads.count_documents = AsyncMock(return_value=1)

    async def _chat_messages_insert_one(doc):
        state.inserted_messages.append(doc)
        return MagicMock()

    db.chat_messages.insert_one = AsyncMock(side_effect=_chat_messages_insert_one)

    # chat_messages.find(...).sort(...).to_list(N) chain — used to determine
    # call_num from history.
    history = state.chat_messages or []

    class _Cursor:
        def __init__(self, docs):
            self._docs = docs

        def sort(self, *_args, **_kw):
            return self

        async def to_list(self, _n):
            return self._docs

    db.chat_messages.find = MagicMock(return_value=_Cursor(history))

    # chat_sessions.find_one — used to detect single_call mode.
    async def _chat_sessions_find_one(query, projection=None):
        return state.chat_session_doc

    db.chat_sessions.find_one = AsyncMock(side_effect=_chat_sessions_find_one)
    db.chat_sessions.update_one = AsyncMock()

    # vega_discovery_sessions — one matching session.
    async def _vega_find_one(query, projection=None, sort=None):
        return state.vega_session

    db.vega_discovery_sessions.find_one = AsyncMock(side_effect=_vega_find_one)
    db.vega_discovery_sessions.update_one = AsyncMock()

    class _VegaSessCursor:
        def __init__(self, docs):
            self._docs = docs

        def sort(self, *_args, **_kw):
            return self

        async def to_list(self, _n):
            return self._docs

    db.vega_discovery_sessions.find = MagicMock(return_value=_VegaSessCursor([]))

    return db


def _patch_get_db_and_perm(monkeypatch, db):
    monkeypatch.setattr(route_mod, "get_db", lambda: db)
    monkeypatch.setattr(route_mod, "has_perm", lambda u, p: True)


def _intake_msg(call_n: int) -> dict:
    return {"content": f"[INTAKE SELECTION] post_call_{call_n}"}


# ──────────────────────────────────────────────────────────────────────
# Conversational paths
# ──────────────────────────────────────────────────────────────────────


class TestConversationalCall1:
    def test_call1_conversational_routes_to_v3_and_persists_v3_artifact(self, monkeypatch):
        state = _DBState(
            artifact_payload={
                "title": "Document Upload",
                "discovery_mode_origin": "conversational",
                "linked_vega_session_id": "vega_sess_xyz",
            },
            vega_session={"session_id": "vega_sess_xyz", "client_company": "Acme"},
            chat_messages=[_intake_msg(1)],
        )
        db = _make_db(state)
        _patch_get_db_and_perm(monkeypatch, db)

        captured = {}

        async def _fake_run_v3_call1(**kwargs):
            captured.update(kwargs)
            return _make_v3_artifact("call1")

        # Patch on the module the route imports it from.
        from services.vega import v3_call_modes as call_modes_mod
        monkeypatch.setattr(call_modes_mod, "run_v3_call1", _fake_run_v3_call1)

        result = asyncio.run(route_mod.submit_artifact(
            artifact_id="art_test",
            data={"upload_ids": ["u_1"], "session_id": "sess_test"},
            current_user=_make_user(),
        ))

        assert result["status"] == "submitted"

        # The v3 path was chosen — run_v3_call1 was invoked.
        assert captured["upload_ids"] == ["u_1"]
        assert captured["chat_session_id"] == "sess_test"

        # The v3 artifact was persisted as discovery_analysis_v3_call1.
        v3_inserts = [
            d for d in state.inserted_artifacts
            if d.get("artifact_type") == "discovery_analysis_v3_call1"
        ]
        assert len(v3_inserts) == 1
        persisted = v3_inserts[0]
        assert persisted["payload"]["discovery_mode_origin"] == "conversational"
        assert persisted["payload"]["call_number"] == 1
        # Legacy artifact types must NOT have been persisted.
        legacy_inserts = [
            d for d in state.inserted_artifacts
            if d.get("artifact_type") in ("transcript_analysis", "ai_employee_matrix")
        ]
        assert legacy_inserts == []

        # The success chat message was posted with the Call 1 phrasing.
        assistant_msgs = [
            m for m in state.inserted_messages if m.get("role") == "assistant"
        ]
        assert len(assistant_msgs) == 1
        assert "Call 1 discovery complete" in assistant_msgs[0]["content"]
        assert "[[artifact:" in assistant_msgs[0]["content"]


class TestConversationalCall2:
    def test_call2_conversational_threads_prior_call1_artifact_id(self, monkeypatch):
        state = _DBState(
            artifact_payload={
                "title": "Document Upload",
                "discovery_mode_origin": "conversational",
                "linked_vega_session_id": "vega_sess_xyz",
            },
            vega_session={"session_id": "vega_sess_xyz", "client_company": "Acme"},
            prior_v3_call_artifact_id="art_call1_prior",
            prior_call_n=1,
            chat_messages=[_intake_msg(2)],
        )
        db = _make_db(state)
        _patch_get_db_and_perm(monkeypatch, db)

        captured = {}

        async def _fake_run_v3_call2(**kwargs):
            captured.update(kwargs)
            return _make_v3_artifact("call2")

        from services.vega import v3_call_modes as call_modes_mod
        monkeypatch.setattr(call_modes_mod, "run_v3_call2", _fake_run_v3_call2)

        result = asyncio.run(route_mod.submit_artifact(
            artifact_id="art_test",
            data={"upload_ids": ["u_1"], "session_id": "sess_test"},
            current_user=_make_user(),
        ))

        assert result["status"] == "submitted"
        # The dispatcher resolved + threaded the prior Call 1 artifact id.
        assert captured["prior_call1_artifact_id"] == "art_call1_prior"

        # Persisted as discovery_analysis_v3_call2 with audit chain.
        v3_inserts = [
            d for d in state.inserted_artifacts
            if d.get("artifact_type") == "discovery_analysis_v3_call2"
        ]
        assert len(v3_inserts) == 1
        persisted = v3_inserts[0]
        assert persisted["payload"]["discovery_mode_origin"] == "conversational"
        assert persisted["payload"]["call_number"] == 2
        assert persisted["payload"]["prior_call_artifact_id"] == "art_call1_prior"

    def test_call2_conversational_with_no_prior_call1_still_runs(self, monkeypatch):
        """No prior Call 1 artifact exists for the session — Call 2 still runs.

        This is a valid "fresh Call 2 without a Call 1" mode. The prior id is
        passed as None and the v3 artifact is persisted without a
        prior_call_artifact_id.
        """
        state = _DBState(
            artifact_payload={
                "title": "Document Upload",
                "discovery_mode_origin": "conversational",
                "linked_vega_session_id": "vega_sess_xyz",
            },
            vega_session={"session_id": "vega_sess_xyz", "client_company": "Acme"},
            prior_v3_call_artifact_id=None,  # nothing found
            prior_call_n=1,
            chat_messages=[_intake_msg(2)],
        )
        db = _make_db(state)
        _patch_get_db_and_perm(monkeypatch, db)

        captured = {}

        async def _fake_run_v3_call2(**kwargs):
            captured.update(kwargs)
            return _make_v3_artifact("call2")

        from services.vega import v3_call_modes as call_modes_mod
        monkeypatch.setattr(call_modes_mod, "run_v3_call2", _fake_run_v3_call2)

        result = asyncio.run(route_mod.submit_artifact(
            artifact_id="art_test",
            data={"upload_ids": ["u_1"], "session_id": "sess_test"},
            current_user=_make_user(),
        ))

        assert result["status"] == "submitted"
        # No prior artifact found — call ran with prior_call1_artifact_id=None.
        assert captured.get("prior_call1_artifact_id") is None

        # The persisted artifact's prior_call_artifact_id is null (the v3
        # model field exists but no prior was threaded). The dispatcher does
        # NOT inject a top-level prior_call_artifact_id override — that's
        # only set when a prior id was actually resolved.
        v3_inserts = [
            d for d in state.inserted_artifacts
            if d.get("artifact_type") == "discovery_analysis_v3_call2"
        ]
        assert len(v3_inserts) == 1
        # The model_dump field (None when no prior threaded).
        assert v3_inserts[0]["payload"].get("prior_call_artifact_id") is None


class TestConversationalCall3:
    def test_call3_conversational_threads_prior_call2_artifact_id(self, monkeypatch):
        state = _DBState(
            artifact_payload={
                "title": "Document Upload",
                "discovery_mode_origin": "conversational",
                "linked_vega_session_id": "vega_sess_xyz",
            },
            vega_session={"session_id": "vega_sess_xyz", "client_company": "Acme"},
            prior_v3_call_artifact_id="art_call2_prior",
            prior_call_n=2,
            chat_messages=[_intake_msg(3)],
        )
        db = _make_db(state)
        _patch_get_db_and_perm(monkeypatch, db)

        captured = {}

        async def _fake_run_v3_call3(**kwargs):
            captured.update(kwargs)
            return _make_v3_artifact("call3")

        from services.vega import v3_call_modes as call_modes_mod
        monkeypatch.setattr(call_modes_mod, "run_v3_call3", _fake_run_v3_call3)

        result = asyncio.run(route_mod.submit_artifact(
            artifact_id="art_test",
            data={"upload_ids": ["u_1"], "session_id": "sess_test"},
            current_user=_make_user(),
        ))

        assert result["status"] == "submitted"
        assert captured["prior_call2_artifact_id"] == "art_call2_prior"

        v3_inserts = [
            d for d in state.inserted_artifacts
            if d.get("artifact_type") == "discovery_analysis_v3_call3"
        ]
        assert len(v3_inserts) == 1
        persisted = v3_inserts[0]
        assert persisted["payload"]["discovery_mode_origin"] == "conversational"
        assert persisted["payload"]["call_number"] == 3
        assert persisted["payload"]["prior_call_artifact_id"] == "art_call2_prior"


# ──────────────────────────────────────────────────────────────────────
# Chip-driven legacy paths — must NOT be touched by Wave 6b
# ──────────────────────────────────────────────────────────────────────


class TestChipDrivenLegacy:
    def test_call1_chip_legacy_uses_v2_transcript_analysis(self, monkeypatch):
        state = _DBState(
            # No discovery_mode_origin key → defaults to "chip_legacy".
            artifact_payload={
                "title": "Document Upload",
                "linked_vega_session_id": "vega_sess_xyz",
            },
            vega_session={"session_id": "vega_sess_xyz", "client_company": "Acme"},
            chat_messages=[_intake_msg(1)],
        )
        db = _make_db(state)
        _patch_get_db_and_perm(monkeypatch, db)

        v3_called = {"hit": False}

        async def _v3_should_not_be_called(**kwargs):
            v3_called["hit"] = True
            return _make_v3_artifact("call1")

        from services.vega import v3_call_modes as call_modes_mod
        monkeypatch.setattr(call_modes_mod, "run_v3_call1", _v3_should_not_be_called)

        # Patch the v2 entry point to return a synthetic Call 1 result.
        captured = {}

        async def _fake_run_transcript_analysis(db_, user_, upload_ids, **kwargs):
            captured["call_number"] = kwargs.get("call_number")
            return {"analysis": {"summary": "v2 stub"}}

        from services.ai import vega_analysis as vega_mod
        monkeypatch.setattr(vega_mod, "run_transcript_analysis", _fake_run_transcript_analysis)

        # run_with_retry is used as a thin wrapper — patch to call through.
        async def _fake_run_with_retry(fn, *args, **kwargs):
            return await fn(*args, **{k: v for k, v in kwargs.items() if k != "call_num"})

        monkeypatch.setattr(vega_mod, "run_with_retry", _fake_run_with_retry)

        result = asyncio.run(route_mod.submit_artifact(
            artifact_id="art_test",
            data={"upload_ids": ["u_1"], "session_id": "sess_test"},
            current_user=_make_user(),
        ))

        assert result["status"] == "submitted"
        assert v3_called["hit"] is False, "v3 path must not run for chip_legacy"
        assert captured.get("call_number") == 1

        # Persisted as the LEGACY transcript_analysis artifact_type.
        legacy_inserts = [
            d for d in state.inserted_artifacts
            if d.get("artifact_type") == "transcript_analysis"
        ]
        assert len(legacy_inserts) == 1
        # No discovery_analysis_v3_call1 written.
        v3_inserts = [
            d for d in state.inserted_artifacts
            if d.get("artifact_type", "").startswith("discovery_analysis_v3")
        ]
        assert v3_inserts == []

    def test_call2_chip_legacy_uses_v2_call2_analysis(self, monkeypatch):
        state = _DBState(
            artifact_payload={
                "title": "Document Upload",
                "discovery_mode_origin": "chip_legacy",
                "linked_vega_session_id": "vega_sess_xyz",
            },
            vega_session={"session_id": "vega_sess_xyz", "client_company": "Acme"},
            chat_messages=[_intake_msg(2)],
        )
        db = _make_db(state)
        _patch_get_db_and_perm(monkeypatch, db)

        v3_called = {"hit": False}

        async def _v3_should_not_be_called(**kwargs):
            v3_called["hit"] = True
            return _make_v3_artifact("call2")

        from services.vega import v3_call_modes as call_modes_mod
        monkeypatch.setattr(call_modes_mod, "run_v3_call2", _v3_should_not_be_called)

        async def _fake_run_call2_analysis(db_, user_, upload_ids, **kwargs):
            return {"matrix_data": {"matrix": [{"workflow_id": "wf1"}]}}

        from services.ai import vega_analysis as vega_mod
        monkeypatch.setattr(vega_mod, "run_call2_analysis", _fake_run_call2_analysis)

        async def _fake_run_with_retry(fn, *args, **kwargs):
            return await fn(*args, **{k: v for k, v in kwargs.items() if k != "call_num"})

        monkeypatch.setattr(vega_mod, "run_with_retry", _fake_run_with_retry)

        result = asyncio.run(route_mod.submit_artifact(
            artifact_id="art_test",
            data={"upload_ids": ["u_1"], "session_id": "sess_test"},
            current_user=_make_user(),
        ))

        assert result["status"] == "submitted"
        assert v3_called["hit"] is False

        legacy_inserts = [
            d for d in state.inserted_artifacts
            if d.get("artifact_type") == "ai_employee_matrix"
        ]
        assert len(legacy_inserts) == 1
        assert legacy_inserts[0]["payload"]["call_number"] == 2

    def test_call3_chip_legacy_uses_v2_call3_analysis(self, monkeypatch):
        state = _DBState(
            artifact_payload={
                "title": "Document Upload",
                "discovery_mode_origin": "chip_legacy",
                "linked_vega_session_id": "vega_sess_xyz",
            },
            vega_session={"session_id": "vega_sess_xyz", "client_company": "Acme"},
            chat_messages=[_intake_msg(3)],
        )
        db = _make_db(state)
        _patch_get_db_and_perm(monkeypatch, db)

        v3_called = {"hit": False}

        async def _v3_should_not_be_called(**kwargs):
            v3_called["hit"] = True
            return _make_v3_artifact("call3")

        from services.vega import v3_call_modes as call_modes_mod
        monkeypatch.setattr(call_modes_mod, "run_v3_call3", _v3_should_not_be_called)

        async def _fake_run_call3_analysis(db_, user_, upload_ids, **kwargs):
            return {"matrix_data": {"matrix": [{"workflow_id": "wf1"}]}}

        from services.ai import vega_analysis as vega_mod
        monkeypatch.setattr(vega_mod, "run_call3_analysis", _fake_run_call3_analysis)

        async def _fake_run_with_retry(fn, *args, **kwargs):
            return await fn(*args, **{k: v for k, v in kwargs.items() if k != "call_num"})

        monkeypatch.setattr(vega_mod, "run_with_retry", _fake_run_with_retry)

        result = asyncio.run(route_mod.submit_artifact(
            artifact_id="art_test",
            data={"upload_ids": ["u_1"], "session_id": "sess_test"},
            current_user=_make_user(),
        ))

        assert result["status"] == "submitted"
        assert v3_called["hit"] is False

        legacy_inserts = [
            d for d in state.inserted_artifacts
            if d.get("artifact_type") == "ai_employee_matrix"
        ]
        assert len(legacy_inserts) == 1
        assert legacy_inserts[0]["payload"]["call_number"] == 3
