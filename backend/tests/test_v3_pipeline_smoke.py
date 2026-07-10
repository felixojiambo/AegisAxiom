"""Smoke tests for the Vega v3 pipeline scaffold (Wave 2 / Thread 2a).

These tests verify the orchestrator wiring and the Stage 1 schema /
prompt without making any live LLM calls. They are intentionally light:

  * confirm SKELETON_SCHEMA round-trips through json.dumps cleanly,
  * confirm SKELETON_PROMPT formats with a transcript_text placeholder,
  * confirm the orchestrator raises NotImplementedError when the
    Thread 2b / 2c / 2d hooks haven't landed yet (so we know the
    scaffold actually wires through),
  * confirm pipeline_metadata stage timings populate when the four
    _stage_* methods are mocked.

Real end-to-end coverage lives in Wave 4 (test_vega_v3_e2e.py).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Required so auth_helper / services don't blow up at import time.
os.environ.setdefault("JWT_SECRET", "test-secret")

from services.vega import v3_pipeline as pipeline_mod
from services.vega.models_v3 import (
    App,
    AppSkeleton,
    DiscoveryAnalysisV3,
    SessionFlags,
    SourceProvenance,
    Workflow,
    WorkflowSkeleton,
)
from services.vega.skeleton_schema import (
    SKELETON_PROMPT,
    SKELETON_SCHEMA,
)
from services.vega.v3_pipeline import (
    V3_FANOUT_LIMIT,
    V3_LLM_TIMEOUT,
    run_v3_pipeline,
)


# ──────────────────────────────────────────────────────────────────────
# SKELETON_SCHEMA / SKELETON_PROMPT shape tests
# ──────────────────────────────────────────────────────────────────────


class TestSkeletonSchema:
    def test_schema_round_trips_through_json(self):
        # JSON-serialisable means Gemini's structured-output validator
        # can ingest it. If this fails, the schema has a non-JSON value
        # somewhere (a Pydantic class, a tuple, etc.).
        payload = json.dumps(SKELETON_SCHEMA)
        assert isinstance(payload, str)
        assert len(payload) > 100  # sanity: not an empty dict

    def test_schema_has_required_top_level_fields(self):
        assert SKELETON_SCHEMA["type"] == "object"
        props = SKELETON_SCHEMA["properties"]
        for required in ("client_company", "workflows", "apps", "session_flags", "source_transcripts"):
            assert required in props, f"missing top-level property: {required}"

    def test_workflow_skeleton_has_six_mmi_dims(self):
        wf_props = SKELETON_SCHEMA["properties"]["workflows"]["items"]["properties"]
        mmi_props = wf_props["mmi_dims"]["properties"]
        for dim in ("tss", "ie", "time", "cost", "quality", "satisfaction"):
            assert dim in mmi_props, f"missing MMI dim: {dim}"
        # Wave 1 SHOULD-ADD: the keys are locked, not loose.
        assert set(SKELETON_SCHEMA["properties"]["workflows"]["items"]
                   ["properties"]["mmi_dims"]["required"]) == {
            "tss", "ie", "time", "cost", "quality", "satisfaction",
        }

    def test_complexity_uses_literal_enum(self):
        wf_props = SKELETON_SCHEMA["properties"]["workflows"]["items"]["properties"]
        assert wf_props["complexity"]["enum"] == ["S", "M", "L", "XL"]
        app_props = SKELETON_SCHEMA["properties"]["apps"]["items"]["properties"]
        assert app_props["complexity"]["enum"] == ["S", "M", "L", "XL"]


class TestSkeletonPrompt:
    def test_prompt_formats_with_transcript_placeholder(self):
        rendered = SKELETON_PROMPT.format(transcript_text="REP: hello\nCUSTOMER: hi")
        assert "REP: hello" in rendered
        assert "{transcript_text}" not in rendered  # placeholder consumed

    def test_prompt_lists_all_six_flag_tokens(self):
        for token in (
            "[MISSING]",
            "[NOT_DISCUSSED]",
            "[NOT_STATED]",
            "[INFERRED_CONFIRM]",
            "[NEEDS_TECHNICAL_FOLLOWUP]",
            "[SOURCE_UNCLEAR]",
        ):
            assert token in SKELETON_PROMPT, f"missing flag token: {token}"

    def test_prompt_says_stage_1_only(self):
        # Defence against the LLM trying to fill Stage 2 detail fields.
        assert "Stage 1" in SKELETON_PROMPT
        assert "Do not produce that detail" in SKELETON_PROMPT


# ──────────────────────────────────────────────────────────────────────
# Orchestrator wiring tests
# ──────────────────────────────────────────────────────────────────────


def _make_user(user_id: str = "user_test"):
    """Minimal user shim — only needs .user_id for log + IDOR scoping."""
    user = MagicMock()
    user.user_id = user_id
    return user


def _make_db_with_transcript(text: str = "REP: hi\nCUSTOMER: we use QuickBooks"):
    """Mock Mongo: chat_uploads.find_one returns a doc with extracted_text."""
    db = MagicMock()
    db.chat_uploads.find_one = AsyncMock(return_value={
        "upload_id": "u1",
        "user_id": "user_test",
        "filename": "call.txt",
        "extracted_text": text,
    })
    db.chat_uploads.update_one = AsyncMock()
    db.vega_events.insert_one = AsyncMock()
    return db


def test_module_constants_locked():
    """Wave plan: per-call timeout 180s, fan-out cap 5."""
    assert V3_LLM_TIMEOUT == 180
    assert V3_FANOUT_LIMIT == 5


def test_run_v3_pipeline_records_stage_durations_when_stubs_mocked(monkeypatch):
    """When all four stages are mocked, pipeline_metadata is populated."""
    db = _make_db_with_transcript()
    user = _make_user()

    async def _stub_stage_1(*args, **kwargs):
        return ("Acme Co", [], SessionFlags(), [], [])

    async def _stub_stage_2(*args, **kwargs):
        return ([], [])

    async def _stub_stage_3(*args, **kwargs):
        return None

    def _stub_stage_4(*args, **kwargs):
        return ([], [])

    monkeypatch.setattr(pipeline_mod, "_stage_1_skeleton", _stub_stage_1)
    monkeypatch.setattr(pipeline_mod, "_stage_2_details", _stub_stage_2)
    monkeypatch.setattr(pipeline_mod, "_stage_3_bpmn_and_design_docs", _stub_stage_3)
    monkeypatch.setattr(pipeline_mod, "_stage_4_postprocess", _stub_stage_4)

    artifact = asyncio.run(run_v3_pipeline(
        db=db, user=user, upload_ids=["u1"],
        chat_session_id="sess_test",
    ))

    assert isinstance(artifact, DiscoveryAnalysisV3)
    assert artifact.client_company == "Acme Co"
    assert artifact.extraction_status == "complete"

    md = artifact.pipeline_metadata
    # Each duration should be a non-negative int — mocked stages run
    # near-instantly, so 0ms is acceptable on fast machines but must
    # not be None.
    assert md.stage_1_duration_ms is not None and md.stage_1_duration_ms >= 0
    assert md.stage_2_duration_ms is not None and md.stage_2_duration_ms >= 0
    assert md.stage_3_duration_ms is not None and md.stage_3_duration_ms >= 0
    assert md.stage_4_duration_ms is not None and md.stage_4_duration_ms >= 0
    assert md.total_duration_ms is not None and md.total_duration_ms >= 0
    # Total >= max of any single stage (clock monotonicity).
    assert md.total_duration_ms >= max(
        md.stage_1_duration_ms, md.stage_2_duration_ms,
        md.stage_3_duration_ms, md.stage_4_duration_ms,
    )


def test_run_v3_pipeline_aborts_when_no_transcript(monkeypatch):
    """Stage 1 cannot run with empty transcript text — orchestrator raises."""
    db = _make_db_with_transcript(text="")
    user = _make_user()

    with pytest.raises(ValueError, match="no transcript text"):
        asyncio.run(run_v3_pipeline(
            db=db, user=user, upload_ids=["u1"],
            chat_session_id="sess_test",
        ))


def test_run_v3_pipeline_overrides_client_company(monkeypatch):
    """Caller-supplied client_company wins over Stage 1's extraction."""
    db = _make_db_with_transcript()
    user = _make_user()

    async def _stub_stage_1(*args, **kwargs):
        return ("Skeleton-extracted Co", [], SessionFlags(), [], [])

    async def _stub_stage_2(*args, **kwargs):
        return ([], [])

    async def _stub_stage_3(*args, **kwargs):
        return None

    def _stub_stage_4(*args, **kwargs):
        return ([], [])

    monkeypatch.setattr(pipeline_mod, "_stage_1_skeleton", _stub_stage_1)
    monkeypatch.setattr(pipeline_mod, "_stage_2_details", _stub_stage_2)
    monkeypatch.setattr(pipeline_mod, "_stage_3_bpmn_and_design_docs", _stub_stage_3)
    monkeypatch.setattr(pipeline_mod, "_stage_4_postprocess", _stub_stage_4)

    artifact = asyncio.run(run_v3_pipeline(
        db=db, user=user, upload_ids=["u1"],
        chat_session_id="sess_test",
        client_company="Caller-supplied Co",
    ))
    assert artifact.client_company == "Caller-supplied Co"


def test_partial_extraction_propagates_to_status(monkeypatch):
    """When any workflow has partial_extraction_failed, status -> partial."""
    db = _make_db_with_transcript()
    user = _make_user()

    async def _stub_stage_1(*args, **kwargs):
        return ("Acme", [], SessionFlags(), [], [])

    async def _stub_stage_2(*args, **kwargs):
        return ([
            Workflow(
                workflow_id="wf_x", workflow_name="X",
                source=SourceProvenance(),
                mmi_hypothesis=pipeline_mod._seed_mmi_from_skeleton(
                    WorkflowSkeleton(workflow_id="wf_x", workflow_name="X",
                                     source=SourceProvenance()),
                ),
                partial_extraction_failed=True,
                partial_extraction_reason="stage 2a timeout",
            ),
        ], [])

    async def _stub_stage_3(*args, **kwargs):
        return None

    def _stub_stage_4(workflows, apps, session_flags):
        # Stage 4 returns post-processing results only. The orchestrator
        # computes extraction_status itself based on partial_extraction_failed
        # markers — see the assertion at the bottom of this test.
        return ([], [])

    monkeypatch.setattr(pipeline_mod, "_stage_1_skeleton", _stub_stage_1)
    monkeypatch.setattr(pipeline_mod, "_stage_2_details", _stub_stage_2)
    monkeypatch.setattr(pipeline_mod, "_stage_3_bpmn_and_design_docs", _stub_stage_3)
    monkeypatch.setattr(pipeline_mod, "_stage_4_postprocess", _stub_stage_4)

    artifact = asyncio.run(run_v3_pipeline(
        db=db, user=user, upload_ids=["u1"],
        chat_session_id="sess_test",
    ))
    # Orchestrator owns extraction_status now — Stage 4 only returns
    # post-processing rows. Any partial_extraction_failed=True on a
    # workflow or app must flip artifact.extraction_status to "partial".
    assert artifact.workflows[0].partial_extraction_failed is True
    assert artifact.extraction_status == "partial"


def test_stage_3_surfaces_not_implemented_error():
    """If a Stage 3 hook is unimplemented, the pipeline must surface it.

    Earlier versions used asyncio.gather(return_exceptions=True) without
    inspecting results, which silently swallowed NotImplementedError and
    masked broken wiring. This test locks the contract that
    NotImplementedError raised by any Stage 3 closure propagates out of
    _stage_3_bpmn_and_design_docs.
    """
    async def _broken_bpmn(*args, **kwargs):
        raise NotImplementedError("BPMN hook not wired yet")

    async def _ok_design(*args, **kwargs):
        return None

    async def _ok_eadd(*args, **kwargs):
        return None

    async def _run():
        with patch.object(pipeline_mod, "_generate_bpmn_for_workflow", _broken_bpmn), \
             patch.object(pipeline_mod, "_generate_design_doc_for_workflow", _ok_design), \
             patch.object(pipeline_mod, "_generate_eadd_for_app", _ok_eadd):
            wf = Workflow(
                workflow_id="wf_x", workflow_name="X",
                source=SourceProvenance(),
                mmi_hypothesis=pipeline_mod._seed_mmi_from_skeleton(
                    WorkflowSkeleton(workflow_id="wf_x", workflow_name="X",
                                     source=SourceProvenance()),
                ),
            )
            await pipeline_mod._stage_3_bpmn_and_design_docs(
                workflows=[wf], apps=[],
                db=MagicMock(), user=_make_user(),
                session_id="sess_test",
            )

    with pytest.raises(NotImplementedError, match="BPMN hook not wired yet"):
        asyncio.run(_run())
