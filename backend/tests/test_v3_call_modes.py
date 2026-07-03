"""Unit tests for the Vega v3 per-call pipeline modes (Wave 6 / Thread 6a).

Covers ``run_v3_call1`` / ``run_v3_call2`` / ``run_v3_call3`` in
``services.vega.v3_call_modes``:

  * Call 1 produces a lighter artifact — no MMI score, no BPMN, no
    design docs, no EADD.
  * Call 2 produces full Stage 2 (MMI present) but skips Stage 3.
  * Call 2 with ``prior_call1_artifact_id`` loads the prior artifact
    and threads it into the prompt context (verified by capturing the
    ``prior_block`` passed to the per-entity extractors).
  * Call 3 produces a full v3-shaped artifact with all Stage 3 fields
    populated.
  * Cross-call merge sets ``prior_call_artifact_id`` correctly.
  * ``pipeline_metadata.call_mode`` round-trips through Pydantic + JSON
    serialization.

Mocking strategy mirrors ``test_v3_pipeline_smoke.py`` and
``test_v3_partial_rerun.py``:

  - ``_stage_1_skeleton`` is monkeypatched to skip the LLM call and
    return a fixed skeleton tuple.
  - ``_extract_workflow_detail_for_call_mode`` and
    ``_extract_app_detail_for_call_mode`` are monkeypatched on the
    ``v3_call_modes`` module so the test owns what each "extracted" detail
    looks like.
  - Stage 3 generators (``_stage_3_bpmn_and_design_docs``) are mocked
    to mutate the workflow/app objects in place when call_mode="call3".

Run with:
    docker compose exec backend pytest tests/test_v3_call_modes.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("JWT_SECRET", "test-secret")

from services.vega import v3_call_modes as call_modes_mod
from services.vega import v3_pipeline as pipeline_mod
from services.vega.flags import VegaFlag
from services.vega.models_v3 import (
    AcceptanceCriterion,
    App,
    AppDetail,
    AppSkeleton,
    DiscoveryAnalysisV3,
    IntegrationEntry,
    MMIDimension,
    MMIHypothesis,
    OpenItem,
    PersonaRow,
    PipelineMetadata,
    SessionFlags,
    SourceProvenance,
    TranscriptRef,
    Workflow,
    WorkflowDetail,
    WorkflowSkeleton,
)
from services.vega.v3_call_modes import (
    _summarise_artifact_for_prompt,
    run_v3_call1,
    run_v3_call2,
    run_v3_call3,
)


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


def _make_user(user_id: str = "u_test"):
    user = MagicMock()
    user.user_id = user_id
    return user


def _make_db(transcript_text: str = "REP: hello\nCUSTOMER: we use QuickBooks"):
    """Mock Mongo. Supports both the upload lookup and the prior-artifact lookup."""
    db = MagicMock()

    async def _uploads_find_one(query: dict, projection: dict | None = None):
        if "upload_id" in query:
            return {
                "upload_id": query["upload_id"],
                "filename": f"call_{query['upload_id']}.txt",
                "user_id": query["user_id"],
                "extracted_text": transcript_text,
            }
        if "filename" in query:
            return {
                "filename": query["filename"],
                "user_id": query["user_id"],
                "extracted_text": "PRIOR TRANSCRIPT: invoices come from QuickBooks",
            }
        return None

    db.chat_uploads.find_one = AsyncMock(side_effect=_uploads_find_one)
    db.chat_uploads.update_one = AsyncMock()
    db.vega_events.insert_one = AsyncMock()
    db.ai_chat_artifacts.find_one = AsyncMock(return_value=None)
    return db


def _wf_skeleton(wf_id: str = "wf1") -> WorkflowSkeleton:
    return WorkflowSkeleton(
        workflow_id=wf_id,
        workflow_name=f"Workflow {wf_id}",
        complexity="M",
        mmi_dims={"tss": 3, "ie": 3, "time": 3, "cost": 3, "quality": 3, "satisfaction": 3},
        source=SourceProvenance(quote=f"src for {wf_id}"),
        linked_app_ids=["app1"],
    )


def _app_skeleton(app_id: str = "app1") -> AppSkeleton:
    return AppSkeleton(
        app_id=app_id,
        app_name=f"App {app_id}",
        complexity="M",
        source=SourceProvenance(quote=f"src for {app_id}"),
        linked_workflow_ids=["wf1"],
    )


def _full_mmi(reasoning: str = "real") -> MMIHypothesis:
    dim = MMIDimension(value=4, confidence="high", reasoning=reasoning)
    return MMIHypothesis(
        tss=dim, ie=dim, time=dim, cost=dim, quality=dim, satisfaction=dim,
        needs_client_input=[], score=None,
    )


def _stub_stage_1(monkeypatch, *, wf_skeletons=None, app_skeletons=None):
    """Patch _stage_1_skeleton on both modules — call_modes imports it directly,
    pipeline_mod owns the symbol. Patching only on pipeline_mod isn't enough
    because v3_call_modes did `from ... import _stage_1_skeleton` (binds locally).
    """
    if wf_skeletons is None:
        wf_skeletons = [_wf_skeleton("wf1")]
    if app_skeletons is None:
        app_skeletons = [_app_skeleton("app1")]

    async def _stage_1(*args, **kwargs):
        return (
            "Acme Co",
            [TranscriptRef(filename="call.txt")],
            SessionFlags(),
            wf_skeletons,
            app_skeletons,
        )

    monkeypatch.setattr(call_modes_mod, "_stage_1_skeleton", _stage_1)
    monkeypatch.setattr(pipeline_mod, "_stage_1_skeleton", _stage_1)


def _stub_stage_3(monkeypatch, *, mark_generated: bool = True):
    """Patch _stage_3_bpmn_and_design_docs to mutate workflows/apps in place."""
    async def _stage_3(workflows, apps, db, user, session_id, **kwargs):
        if not mark_generated:
            return
        for wf in workflows:
            wf.bpmn_id = f"bpmn_{wf.workflow_id}"
            wf.bpmn_xml = "<bpmn:definitions/>"
            wf.bpmn_status = "generated"
            wf.bpmn_content_hash = "deadbeef"
            wf.design_doc = {"status": "success", "summary": "doc"}
            wf.design_doc_status = "generated"
        for app in apps:
            app.eadd_doc = {"status": "success", "summary": "eadd"}
            app.design_doc_status = "generated"

    monkeypatch.setattr(call_modes_mod, "_stage_3_bpmn_and_design_docs", _stage_3)


def _stub_extractors(
    monkeypatch,
    *,
    wf_detail_factory=None,
    app_detail_factory=None,
    captured_calls: list | None = None,
):
    """Patch the per-entity extractors on the call_modes module.

    ``captured_calls`` (when supplied) is appended to with one dict per
    extractor call so tests can assert which call_mode + prior_block the
    LLM-side prompt would have seen.
    """
    if captured_calls is None:
        captured_calls = []

    async def _wf_extract(skeleton, transcript_text, db, user, session_id,
                          call_mode, prior_block):
        captured_calls.append({
            "kind": "workflow",
            "workflow_id": skeleton.workflow_id,
            "call_mode": call_mode,
            "prior_block": prior_block,
            "transcript_text": transcript_text,
        })
        if wf_detail_factory is not None:
            return wf_detail_factory(skeleton, call_mode)
        # Default — mode-aware shape that mirrors what each prompt promises.
        if call_mode == "call1":
            return WorkflowDetail(
                acceptance_criteria=[
                    AcceptanceCriterion(flow_name="primary", success_when="invoice posted"),
                ],
                # Call 1: NO MMI, NO process steps, NO decision logic
                mmi_hypothesis=None,
                process_steps=[],
                decision_logic=[],
                persona_overrides=[],
            )
        # Call 2 + Call 3 — full WorkflowDetail.
        return WorkflowDetail(
            acceptance_criteria=[
                AcceptanceCriterion(flow_name="primary", success_when="invoice posted"),
            ],
            mmi_hypothesis=_full_mmi(reasoning=f"{call_mode} for {skeleton.workflow_id}"),
            process_steps=[],
            decision_logic=[],
            persona_overrides=[],
        )

    async def _app_extract(skeleton, transcript_text, db, user, session_id,
                           call_mode, prior_block):
        captured_calls.append({
            "kind": "app",
            "app_id": skeleton.app_id,
            "call_mode": call_mode,
            "prior_block": prior_block,
            "transcript_text": transcript_text,
        })
        if app_detail_factory is not None:
            return app_detail_factory(skeleton, call_mode)
        return AppDetail(
            personas=[PersonaRow(role="manager", flags=[VegaFlag.INFERRED_CONFIRM])],
            integrations=[
                IntegrationEntry(system_name="QuickBooks", bucket=2),
            ],
        )

    monkeypatch.setattr(
        call_modes_mod, "_extract_workflow_detail_for_call_mode", _wf_extract,
    )
    monkeypatch.setattr(
        call_modes_mod, "_extract_app_detail_for_call_mode", _app_extract,
    )
    return captured_calls


# ──────────────────────────────────────────────────────────────────────
# Pydantic round-trip — call_mode + prior_call_artifact_id
# ──────────────────────────────────────────────────────────────────────


class TestModelRoundTrip:
    def test_pipeline_metadata_serialises_call_mode(self):
        md = PipelineMetadata(
            stage_1_duration_ms=10,
            stage_2_duration_ms=20,
            stage_3_duration_ms=None,
            stage_4_duration_ms=5,
            total_duration_ms=35,
            call_mode="call2",
        )
        as_json = md.model_dump_json()
        loaded = json.loads(as_json)
        assert loaded["call_mode"] == "call2"
        rebuilt = PipelineMetadata.model_validate(loaded)
        assert rebuilt.call_mode == "call2"

    def test_pipeline_metadata_call_mode_defaults_none(self):
        md = PipelineMetadata()
        assert md.call_mode is None

    def test_artifact_round_trips_call_mode_and_prior_artifact_id(self):
        artifact = DiscoveryAnalysisV3(
            client_company="Acme",
            pipeline_metadata=PipelineMetadata(call_mode="call3"),
            prior_call_artifact_id="art_abc123",
        )
        loaded = json.loads(artifact.model_dump_json())
        assert loaded["pipeline_metadata"]["call_mode"] == "call3"
        assert loaded["prior_call_artifact_id"] == "art_abc123"
        # contradictions_resolved defaults to []
        assert loaded["contradictions_resolved"] == []

        rebuilt = DiscoveryAnalysisV3.model_validate(loaded)
        assert rebuilt.pipeline_metadata.call_mode == "call3"
        assert rebuilt.prior_call_artifact_id == "art_abc123"

    def test_invalid_call_mode_rejected(self):
        with pytest.raises(Exception):
            PipelineMetadata(call_mode="call7")  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────
# Call 1
# ──────────────────────────────────────────────────────────────────────


class TestCall1:
    def test_call1_produces_lighter_artifact(self, monkeypatch):
        """Call 1: call_mode=call1, no MMI hypothesis, no Stage 3 fields."""
        db = _make_db()
        user = _make_user()

        _stub_stage_1(monkeypatch)
        captured = _stub_extractors(monkeypatch)
        # Stage 3 should NOT run for Call 1 — patch it to fail loudly if it does.
        async def _stage_3_should_not_run(*args, **kwargs):
            raise AssertionError("Stage 3 must not run for Call 1")
        monkeypatch.setattr(
            call_modes_mod, "_stage_3_bpmn_and_design_docs", _stage_3_should_not_run,
        )

        artifact = asyncio.run(run_v3_call1(
            db=db, user=user, upload_ids=["u1"], chat_session_id="sess_test",
        ))

        assert isinstance(artifact, DiscoveryAnalysisV3)
        assert artifact.pipeline_metadata.call_mode == "call1"
        # No prior — Call 1 starts fresh.
        assert artifact.prior_call_artifact_id is None

        # Stage 3 was skipped, so stage_3_duration_ms should be None.
        assert artifact.pipeline_metadata.stage_3_duration_ms is None

        # Call 1 specifically: workflows must NOT carry an MMI score, BPMN,
        # design docs.
        assert len(artifact.workflows) == 1
        wf = artifact.workflows[0]
        # Skeleton seeds a low-confidence MMI; Call 1 keeps that seed
        # (mmi_hypothesis is required on Workflow, but score must be None).
        assert wf.mmi_hypothesis.score is None
        # Confidence on every dim should be "low" — the seed's marker.
        for dim_name in ("tss", "ie", "time", "cost", "quality", "satisfaction"):
            dim = getattr(wf.mmi_hypothesis, dim_name)
            assert dim.confidence == "low", (
                f"Call 1 should keep skeleton-seed MMI (low confidence) on {dim_name}, "
                f"got {dim.confidence}"
            )
        assert wf.bpmn_xml is None
        assert wf.bpmn_id is None
        assert wf.bpmn_status is None
        assert wf.design_doc is None
        assert wf.design_doc_status is None

        # Apps should also lack EADD docs.
        assert len(artifact.apps) == 1
        app = artifact.apps[0]
        assert app.eadd_doc is None
        assert app.design_doc_status is None

        # Acceptance criteria did get extracted (Call 1 contract).
        assert len(wf.acceptance_criteria) == 1
        assert wf.acceptance_criteria[0].flow_name == "primary"

        # Each captured call must report the right call_mode + empty prior_block.
        assert len(captured) == 2  # 1 workflow + 1 app
        for entry in captured:
            assert entry["call_mode"] == "call1"
            assert entry["prior_block"] == ""  # no prior artifact for Call 1


# ──────────────────────────────────────────────────────────────────────
# Call 2
# ──────────────────────────────────────────────────────────────────────


class TestCall2:
    def test_call2_no_prior_produces_full_stage_2_no_stage_3(self, monkeypatch):
        """Call 2: call_mode=call2, full MMI, no BPMN, no design docs."""
        db = _make_db()
        user = _make_user()

        _stub_stage_1(monkeypatch)
        captured = _stub_extractors(monkeypatch)

        # Stage 3 should NOT run for Call 2.
        async def _stage_3_should_not_run(*args, **kwargs):
            raise AssertionError("Stage 3 must not run for Call 2")
        monkeypatch.setattr(
            call_modes_mod, "_stage_3_bpmn_and_design_docs", _stage_3_should_not_run,
        )

        artifact = asyncio.run(run_v3_call2(
            db=db, user=user, upload_ids=["u1"], chat_session_id="sess_test",
        ))

        assert artifact.pipeline_metadata.call_mode == "call2"
        assert artifact.prior_call_artifact_id is None
        assert artifact.pipeline_metadata.stage_3_duration_ms is None

        wf = artifact.workflows[0]
        # Call 2 produces a real MMI hypothesis — high confidence, real reasoning.
        assert wf.mmi_hypothesis.tss.confidence == "high"
        assert "call2" in wf.mmi_hypothesis.tss.reasoning
        # Still no Stage 3 fields.
        assert wf.bpmn_xml is None
        assert wf.bpmn_id is None
        assert wf.design_doc is None

        app = artifact.apps[0]
        assert app.eadd_doc is None

        # All extractor calls saw call_mode="call2" + empty prior_block.
        for entry in captured:
            assert entry["call_mode"] == "call2"
            assert entry["prior_block"] == ""

    def test_call2_with_prior_call1_threads_artifact_into_prompts(self, monkeypatch):
        """Call 2 with prior_call1_artifact_id loads the artifact and threads it in."""
        db = _make_db()
        user = _make_user()

        # The "prior" artifact lives in ai_chat_artifacts. Make it a valid
        # Call 1 payload so model_validate succeeds.
        prior = DiscoveryAnalysisV3(
            client_company="Acme Co",
            workflows=[
                Workflow(
                    workflow_id="wf1",
                    workflow_name="Invoice intake",
                    complexity="M",
                    source=SourceProvenance(quote="prior"),
                    linked_app_ids=["app1"],
                    mmi_hypothesis=_full_mmi(reasoning="prior call 1 seed"),
                    acceptance_criteria=[
                        AcceptanceCriterion(
                            flow_name="primary",
                            success_when="invoice posted to NetSuite",
                        ),
                    ],
                ),
            ],
            apps=[
                App(
                    app_id="app1",
                    app_name="AP App",
                    complexity="M",
                    source=SourceProvenance(quote="prior"),
                    linked_workflow_ids=["wf1"],
                    integrations=[IntegrationEntry(system_name="NetSuite", bucket=2)],
                ),
            ],
            pipeline_metadata=PipelineMetadata(call_mode="call1"),
        )

        async def _artifact_find_one(query, projection=None):
            if query.get("artifact_id") == "art_call1_xyz":
                return {
                    "artifact_id": "art_call1_xyz",
                    "user_id": user.user_id,
                    "payload": prior.model_dump(mode="python"),
                }
            return None

        db.ai_chat_artifacts.find_one = AsyncMock(side_effect=_artifact_find_one)

        _stub_stage_1(monkeypatch)
        captured = _stub_extractors(monkeypatch)

        async def _stage_3_should_not_run(*args, **kwargs):
            raise AssertionError("Stage 3 must not run for Call 2")
        monkeypatch.setattr(
            call_modes_mod, "_stage_3_bpmn_and_design_docs", _stage_3_should_not_run,
        )

        artifact = asyncio.run(run_v3_call2(
            db=db, user=user, upload_ids=["u1"],
            prior_call1_artifact_id="art_call1_xyz",
            chat_session_id="sess_test",
        ))

        # Audit chain established.
        assert artifact.prior_call_artifact_id == "art_call1_xyz"
        assert artifact.pipeline_metadata.call_mode == "call2"

        # Each extractor call must have seen the prior block in the prompt.
        assert len(captured) == 2
        for entry in captured:
            assert entry["call_mode"] == "call2"
            assert entry["prior_block"], (
                "Call 2 with prior_call1_artifact_id should pass a non-empty prior_block"
            )
            assert "Call 1" in entry["prior_block"]
            # The prior summary should mention the prior workflow + app names.
            assert "Invoice intake" in entry["prior_block"]
            assert "NetSuite" in entry["prior_block"]

    def test_call2_with_missing_prior_artifact_falls_back_gracefully(self, monkeypatch):
        """Call 2 with an unfindable prior_call1_artifact_id should not crash."""
        db = _make_db()
        user = _make_user()

        # prior artifact lookup returns None.
        db.ai_chat_artifacts.find_one = AsyncMock(return_value=None)

        _stub_stage_1(monkeypatch)
        captured = _stub_extractors(monkeypatch)
        async def _stage_3_should_not_run(*args, **kwargs):
            raise AssertionError("Stage 3 must not run for Call 2")
        monkeypatch.setattr(
            call_modes_mod, "_stage_3_bpmn_and_design_docs", _stage_3_should_not_run,
        )

        artifact = asyncio.run(run_v3_call2(
            db=db, user=user, upload_ids=["u1"],
            prior_call1_artifact_id="art_does_not_exist",
            chat_session_id="sess_test",
        ))

        # When prior lookup fails we don't set prior_call_artifact_id.
        assert artifact.prior_call_artifact_id is None
        assert artifact.pipeline_metadata.call_mode == "call2"

        # Extractors should have been called with empty prior_block.
        for entry in captured:
            assert entry["prior_block"] == ""


# ──────────────────────────────────────────────────────────────────────
# Call 3
# ──────────────────────────────────────────────────────────────────────


class TestCall3:
    def test_call3_produces_full_v3_artifact(self, monkeypatch):
        """Call 3: call_mode=call3, full Stage 3 fields populated."""
        db = _make_db()
        user = _make_user()

        _stub_stage_1(monkeypatch)
        _stub_extractors(monkeypatch)
        _stub_stage_3(monkeypatch, mark_generated=True)

        artifact = asyncio.run(run_v3_call3(
            db=db, user=user, upload_ids=["u1"], chat_session_id="sess_test",
        ))

        assert artifact.pipeline_metadata.call_mode == "call3"
        assert artifact.pipeline_metadata.stage_3_duration_ms is not None
        assert artifact.pipeline_metadata.stage_3_duration_ms >= 0

        # Every Stage 3 field landed.
        wf = artifact.workflows[0]
        assert wf.bpmn_id == "bpmn_wf1"
        assert wf.bpmn_xml == "<bpmn:definitions/>"
        assert wf.bpmn_status == "generated"
        assert wf.design_doc == {"status": "success", "summary": "doc"}
        assert wf.design_doc_status == "generated"

        app = artifact.apps[0]
        assert app.eadd_doc == {"status": "success", "summary": "eadd"}
        assert app.design_doc_status == "generated"

        # MMI hypothesis is real (Call 3 runs full Stage 2).
        assert wf.mmi_hypothesis.tss.confidence == "high"
        assert "call3" in wf.mmi_hypothesis.tss.reasoning

    def test_call3_with_prior_call2_sets_audit_chain(self, monkeypatch):
        """Call 3 with prior_call2_artifact_id sets prior_call_artifact_id."""
        db = _make_db()
        user = _make_user()

        prior = DiscoveryAnalysisV3(
            client_company="Acme Co",
            workflows=[
                Workflow(
                    workflow_id="wf1",
                    workflow_name="Invoice intake",
                    complexity="M",
                    source=SourceProvenance(quote="prior"),
                    linked_app_ids=["app1"],
                    mmi_hypothesis=_full_mmi(reasoning="prior call 2 hypothesis"),
                ),
            ],
            apps=[
                App(
                    app_id="app1",
                    app_name="AP App",
                    complexity="M",
                    source=SourceProvenance(quote="prior"),
                    linked_workflow_ids=["wf1"],
                ),
            ],
            pipeline_metadata=PipelineMetadata(call_mode="call2"),
            prior_call_artifact_id="art_call1_abc",
        )

        async def _artifact_find_one(query, projection=None):
            if query.get("artifact_id") == "art_call2_def":
                return {
                    "artifact_id": "art_call2_def",
                    "user_id": user.user_id,
                    "payload": prior.model_dump(mode="python"),
                }
            return None

        db.ai_chat_artifacts.find_one = AsyncMock(side_effect=_artifact_find_one)

        _stub_stage_1(monkeypatch)
        captured = _stub_extractors(monkeypatch)
        _stub_stage_3(monkeypatch, mark_generated=True)

        artifact = asyncio.run(run_v3_call3(
            db=db, user=user, upload_ids=["u1"],
            prior_call2_artifact_id="art_call2_def",
            chat_session_id="sess_test",
        ))

        # Audit chain — Call 3 points at Call 2.
        assert artifact.prior_call_artifact_id == "art_call2_def"
        assert artifact.pipeline_metadata.call_mode == "call3"

        # Every extractor call must include the contradiction-resolution
        # block in the prior_block string.
        for entry in captured:
            assert entry["call_mode"] == "call3"
            assert entry["prior_block"]
            # Either the "Call 2" prior heading or the contradiction block
            # (or both) should appear — Call 3 sees both.
            assert "Call 2" in entry["prior_block"]
            assert "Resolve contradictions" in entry["prior_block"]


# ──────────────────────────────────────────────────────────────────────
# Cross-call merge helper
# ──────────────────────────────────────────────────────────────────────


class TestSummariseArtifact:
    def test_summary_compacts_workflows_and_apps(self):
        artifact = DiscoveryAnalysisV3(
            client_company="Acme",
            workflows=[
                Workflow(
                    workflow_id="wf1",
                    workflow_name="Invoice intake",
                    complexity="M",
                    source=SourceProvenance(quote="src"),
                    linked_app_ids=["app1"],
                    mmi_hypothesis=_full_mmi(),
                    acceptance_criteria=[
                        AcceptanceCriterion(
                            flow_name="primary",
                            success_when="invoice posted to NetSuite",
                        ),
                    ],
                ),
            ],
            apps=[
                App(
                    app_id="app1",
                    app_name="AP App",
                    complexity="M",
                    source=SourceProvenance(quote="src"),
                    linked_workflow_ids=["wf1"],
                    integrations=[
                        IntegrationEntry(system_name="NetSuite", bucket=2),
                        IntegrationEntry(system_name="QuickBooks", bucket=2),
                    ],
                ),
            ],
            open_items=[
                OpenItem(
                    flag=VegaFlag.MISSING,
                    label="Acceptance criterion missing for X",
                    location="workflow:wf1",
                ),
            ],
        )

        summary = _summarise_artifact_for_prompt(artifact)
        assert summary["client_company"] == "Acme"
        assert len(summary["workflows"]) == 1
        assert summary["workflows"][0]["workflow_id"] == "wf1"
        assert "invoice posted" in summary["workflows"][0]["acceptance_criteria_summary"]
        assert len(summary["apps"]) == 1
        assert "NetSuite" in summary["apps"][0]["integrations_summary"]
        assert "QuickBooks" in summary["apps"][0]["integrations_summary"]
        assert summary["open_items_summary"] == ["Acceptance criterion missing for X"]

    def test_summary_handles_empty_artifact(self):
        artifact = DiscoveryAnalysisV3(client_company="Empty Co")
        summary = _summarise_artifact_for_prompt(artifact)
        assert summary["workflows"] == []
        assert summary["apps"] == []
        assert summary["open_items_summary"] == []
        assert summary["session_flags_summary"] is None


class TestMergeHelper:
    def test_merge_helper_returns_none_when_artifact_id_empty(self):
        from services.vega.v3_call_modes import _merge_call_n_into_call_n_plus_1

        async def _run():
            return await _merge_call_n_into_call_n_plus_1(
                db=MagicMock(), user=_make_user(), prior_artifact_id="",
            )

        prior, notes = asyncio.run(_run())
        assert prior is None
        assert notes == []

    def test_merge_helper_returns_none_when_lookup_fails(self):
        from services.vega.v3_call_modes import _merge_call_n_into_call_n_plus_1

        db = MagicMock()
        db.ai_chat_artifacts.find_one = AsyncMock(return_value=None)

        async def _run():
            return await _merge_call_n_into_call_n_plus_1(
                db=db, user=_make_user(), prior_artifact_id="art_missing",
            )

        prior, notes = asyncio.run(_run())
        assert prior is None
        assert notes == []

    def test_merge_helper_validates_prior_payload(self):
        from services.vega.v3_call_modes import _merge_call_n_into_call_n_plus_1

        prior_payload = DiscoveryAnalysisV3(
            client_company="Acme",
            pipeline_metadata=PipelineMetadata(call_mode="call1"),
        ).model_dump(mode="python")

        db = MagicMock()
        db.ai_chat_artifacts.find_one = AsyncMock(return_value={
            "artifact_id": "art_xyz",
            "user_id": "u_test",
            "payload": prior_payload,
        })

        async def _run():
            return await _merge_call_n_into_call_n_plus_1(
                db=db, user=_make_user(), prior_artifact_id="art_xyz",
            )

        prior, notes = asyncio.run(_run())
        assert isinstance(prior, DiscoveryAnalysisV3)
        assert prior.client_company == "Acme"
        assert prior.pipeline_metadata.call_mode == "call1"
        assert notes == []

    def test_merge_helper_handles_invalid_payload(self):
        from services.vega.v3_call_modes import _merge_call_n_into_call_n_plus_1

        db = MagicMock()
        db.ai_chat_artifacts.find_one = AsyncMock(return_value={
            "artifact_id": "art_xyz",
            "user_id": "u_test",
            # Missing required client_company; model_validate will raise.
            "payload": {"version": "v3"},
        })

        async def _run():
            return await _merge_call_n_into_call_n_plus_1(
                db=db, user=_make_user(), prior_artifact_id="art_xyz",
            )

        prior, notes = asyncio.run(_run())
        # Invalid payload swallows -> None; we don't blow up the new pipeline.
        assert prior is None


# ──────────────────────────────────────────────────────────────────────
# Schema selection
# ──────────────────────────────────────────────────────────────────────


class TestSchemaSelection:
    def test_workflow_schema_selection(self):
        from services.vega.call_mode_prompts import (
            WORKFLOW_DETAIL_PROMPT_CALL1,
            WORKFLOW_DETAIL_SCHEMA_CALL1,
        )
        from services.vega.detail_schemas import (
            WORKFLOW_DETAIL_PROMPT,
            WORKFLOW_DETAIL_SCHEMA,
        )
        from services.vega.v3_call_modes import _select_workflow_schema_and_prompt

        s1, p1 = _select_workflow_schema_and_prompt("call1")
        assert s1 is WORKFLOW_DETAIL_SCHEMA_CALL1
        assert p1 is WORKFLOW_DETAIL_PROMPT_CALL1

        s2, p2 = _select_workflow_schema_and_prompt("call2")
        assert s2 is WORKFLOW_DETAIL_SCHEMA
        assert p2 is WORKFLOW_DETAIL_PROMPT

        s3, p3 = _select_workflow_schema_and_prompt("call3")
        assert s3 is WORKFLOW_DETAIL_SCHEMA
        assert p3 is WORKFLOW_DETAIL_PROMPT

    def test_app_schema_selection(self):
        from services.vega.call_mode_prompts import (
            APP_DETAIL_PROMPT_CALL1,
            APP_DETAIL_SCHEMA_CALL1,
        )
        from services.vega.detail_schemas import APP_DETAIL_SCHEMA
        from services.vega.v3_call_modes import _select_app_schema_and_prompt

        s1, p1 = _select_app_schema_and_prompt("call1")
        assert s1 is APP_DETAIL_SCHEMA_CALL1
        assert p1 is APP_DETAIL_PROMPT_CALL1

        s2, _ = _select_app_schema_and_prompt("call2")
        assert s2 is APP_DETAIL_SCHEMA

        s3, _ = _select_app_schema_and_prompt("call3")
        assert s3 is APP_DETAIL_SCHEMA

    def test_call1_workflow_schema_excludes_mmi_and_process_steps(self):
        """Locks the contract: Call 1's schema must not require MMI / process_steps."""
        from services.vega.call_mode_prompts import WORKFLOW_DETAIL_SCHEMA_CALL1

        props = WORKFLOW_DETAIL_SCHEMA_CALL1["properties"]
        # The trimmed schema should NOT include these — they're Call 2's job.
        assert "mmi_hypothesis" not in props
        assert "process_steps" not in props
        assert "decision_logic" not in props
        assert "persona_overrides" not in props
        # acceptance_criteria stays — it's Call 1's job.
        assert "acceptance_criteria" in props
        # And it remains required.
        assert WORKFLOW_DETAIL_SCHEMA_CALL1["required"] == ["acceptance_criteria"]

    def test_call1_app_schema_excludes_nfrs_and_quotes(self):
        """Locks the contract: Call 1's app schema must not require NFRs."""
        from services.vega.call_mode_prompts import APP_DETAIL_SCHEMA_CALL1

        props = APP_DETAIL_SCHEMA_CALL1["properties"]
        assert "nfrs" not in props
        assert "additional_source_quotes" not in props
        # Personas + integrations stay (Call 1 fills them).
        assert "personas" in props
        assert "integrations" in props
        assert "scale_profile" in props


# ──────────────────────────────────────────────────────────────────────
# Empty-transcript guard
# ──────────────────────────────────────────────────────────────────────


class TestEmptyTranscript:
    def test_call1_aborts_on_empty_transcript(self, monkeypatch):
        db = _make_db(transcript_text="")
        user = _make_user()

        with pytest.raises(ValueError, match="no transcript text"):
            asyncio.run(run_v3_call1(
                db=db, user=user, upload_ids=["u1"], chat_session_id="sess_test",
            ))

    def test_call2_aborts_on_empty_transcript(self):
        db = _make_db(transcript_text="")
        user = _make_user()

        with pytest.raises(ValueError, match="no transcript text"):
            asyncio.run(run_v3_call2(
                db=db, user=user, upload_ids=["u1"], chat_session_id="sess_test",
            ))

    def test_call3_aborts_on_empty_transcript(self):
        db = _make_db(transcript_text="")
        user = _make_user()

        with pytest.raises(ValueError, match="no transcript text"):
            asyncio.run(run_v3_call3(
                db=db, user=user, upload_ids=["u1"], chat_session_id="sess_test",
            ))
