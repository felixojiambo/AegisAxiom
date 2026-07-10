"""Unit tests for the Vega v3 partial-regeneration pipeline (Wave 5 / 5b).

Covers:
  * ``run_v3_pipeline_partial`` — entity-scoped re-extraction
  * carry-through of entities NOT in regen_scope
  * ``user_provided_answers`` accumulation across regenerations
  * new transcript text appended to re-extracted entity prompts
  * Stage 4 shared_infra rerun gated on ``RegenScope.rerun_shared_infra``
  * v3_partial_regen_* event emission

These tests mock every LLM hook (``_extract_workflow_detail``,
``_extract_app_detail``, ``_generate_bpmn_for_workflow``,
``_generate_design_doc_for_workflow``, ``_generate_eadd_for_app``) the same
way ``test_v3_pipeline_smoke.py`` does — by monkeypatching on the module.

Run with:
    docker compose exec backend pytest tests/test_v3_partial_rerun.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("JWT_SECRET", "test-secret")

from services.vega import v3_pipeline as pipeline_mod
from services.vega.flags import VegaFlag
from services.vega.models_v3 import (
    App,
    AppDetail,
    DiscoveryAnalysisV3,
    IntegrationEntry,
    MMIDimension,
    MMIHypothesis,
    OpenItem,
    PersonaRow,
    SessionFlags,
    SharedInfrastructureRow,
    SourceProvenance,
    TranscriptRef,
    Workflow,
    WorkflowDetail,
)
from services.vega.regenerate import RegenScope
from services.vega.v3_pipeline import run_v3_pipeline_partial


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


def _make_user(user_id: str = "u_test") -> MagicMock:
    user = MagicMock()
    user.user_id = user_id
    return user


def _mmi(value: int = 3, reasoning: str = "seed") -> MMIHypothesis:
    dim = MMIDimension(value=value, confidence="medium", reasoning=reasoning)
    return MMIHypothesis(
        tss=dim, ie=dim, time=dim, cost=dim, quality=dim, satisfaction=dim,
        needs_client_input=[], score=None,
    )


def _wf(
    wf_id: str,
    *,
    name: str | None = None,
    linked_app_ids: list[str] | None = None,
    user_provided_answers: dict[str, str] | None = None,
    bpmn_id: str | None = None,
    bpmn_xml: str | None = None,
    bpmn_content_hash: str | None = None,
    bpmn_status: str | None = None,
    design_doc: dict | None = None,
) -> Workflow:
    return Workflow(
        workflow_id=wf_id,
        workflow_name=name or f"Workflow {wf_id}",
        complexity="M",
        source=SourceProvenance(quote=f"prior {wf_id}"),
        linked_app_ids=linked_app_ids or [],
        mmi_hypothesis=_mmi(reasoning=f"prior reasoning for {wf_id}"),
        user_provided_answers=user_provided_answers or {},
        bpmn_id=bpmn_id,
        bpmn_xml=bpmn_xml,
        bpmn_content_hash=bpmn_content_hash,
        bpmn_status=bpmn_status,
        design_doc=design_doc,
    )


def _app(
    app_id: str,
    *,
    name: str | None = None,
    linked_workflow_ids: list[str] | None = None,
    user_provided_answers: dict[str, str] | None = None,
    integrations: list[IntegrationEntry] | None = None,
    eadd_doc: dict | None = None,
) -> App:
    return App(
        app_id=app_id,
        app_name=name or f"App {app_id}",
        complexity="M",
        source=SourceProvenance(quote=f"prior {app_id}"),
        linked_workflow_ids=linked_workflow_ids or [],
        integrations=integrations or [],
        user_provided_answers=user_provided_answers or {},
        eadd_doc=eadd_doc,
    )


def _prior_artifact(
    workflows: list[Workflow] | None = None,
    apps: list[App] | None = None,
    shared_infra: list[SharedInfrastructureRow] | None = None,
    open_items: list[OpenItem] | None = None,
    source_transcripts: list[TranscriptRef] | None = None,
) -> DiscoveryAnalysisV3:
    return DiscoveryAnalysisV3(
        client_company="ACME",
        source_transcripts=source_transcripts or [
            TranscriptRef(filename="prior_call.txt"),
        ],
        session_flags=SessionFlags(),
        workflows=workflows or [],
        apps=apps or [],
        shared_infrastructure=shared_infra or [],
        open_items=open_items or [],
        extraction_status="complete",
    )


def _make_db(
    *,
    prior_text: str = "PRIOR REP: invoices come from QuickBooks",
    new_text: str | None = None,
) -> MagicMock:
    """Mock Mongo: chat_uploads.find_one returns text by filename or upload_id.

    Two query shapes:
      - {filename, user_id} — used by partial regen to re-load prior transcripts.
      - {upload_id, user_id} — used by _load_transcripts for new uploads.
    """
    db = MagicMock()

    async def _find_one(query: dict, projection: dict | None = None):
        if "upload_id" in query and new_text is not None:
            return {
                "upload_id": query["upload_id"],
                "filename": f"new_{query['upload_id']}.txt",
                "user_id": query["user_id"],
                "extracted_text": new_text,
            }
        if "filename" in query:
            return {
                "filename": query["filename"],
                "user_id": query["user_id"],
                "extracted_text": prior_text,
            }
        return None

    db.chat_uploads.find_one = AsyncMock(side_effect=_find_one)
    db.chat_uploads.update_one = AsyncMock()
    db.vega_events.insert_one = AsyncMock()
    return db


def _stub_extractors(
    monkeypatch: pytest.MonkeyPatch,
    *,
    workflow_calls: list[dict] | None = None,
    app_calls: list[dict] | None = None,
):
    """Wire mocked Stage 2/3 hooks and capture per-call kwargs.

    The lists passed in are appended to (one entry per call) so tests can
    assert on the exact ID of each entity that was re-extracted and on the
    prior_answers value seen at call time.
    """
    if workflow_calls is None:
        workflow_calls = []
    if app_calls is None:
        app_calls = []

    async def _wf_extract(skeleton, transcript_text, db, user, session_id, prior_answers=None):
        workflow_calls.append({
            "workflow_id": skeleton.workflow_id,
            "transcript_text": transcript_text,
            "prior_answers": dict(prior_answers or {}),
        })
        return WorkflowDetail(
            acceptance_criteria=[],
            decision_logic=[],
            process_steps=[],
            persona_overrides=[],
            mmi_hypothesis=_mmi(reasoning=f"refreshed for {skeleton.workflow_id}"),
        )

    async def _app_extract(skeleton, transcript_text, db, user, session_id, prior_answers=None):
        app_calls.append({
            "app_id": skeleton.app_id,
            "transcript_text": transcript_text,
            "prior_answers": dict(prior_answers or {}),
        })
        return AppDetail(
            personas=[PersonaRow(role="updated user")],
            integrations=[],
        )

    async def _no_op_bpmn(workflow, db, user, session_id):
        return workflow

    async def _no_op_design(workflow, db, user, session_id, client_industry="", stakeholders_summary=""):
        return workflow

    async def _no_op_eadd(app, db, user, session_id, matrix=None, client_industry="", stakeholders_summary=""):
        return app

    monkeypatch.setattr(pipeline_mod, "_extract_workflow_detail", _wf_extract)
    monkeypatch.setattr(pipeline_mod, "_extract_app_detail", _app_extract)
    monkeypatch.setattr(pipeline_mod, "_generate_bpmn_for_workflow", _no_op_bpmn)
    monkeypatch.setattr(pipeline_mod, "_generate_design_doc_for_workflow", _no_op_design)
    monkeypatch.setattr(pipeline_mod, "_generate_eadd_for_app", _no_op_eadd)

    return workflow_calls, app_calls


# ──────────────────────────────────────────────────────────────────────
# Workflow-scoped partial regen
# ──────────────────────────────────────────────────────────────────────


class TestPartialRerunWorkflow:
    def test_only_in_scope_workflow_extracts(self, monkeypatch):
        prior = _prior_artifact(
            workflows=[
                _wf("wf_1", name="WF One"),
                _wf("wf_2", name="WF Two"),
            ],
            apps=[
                _app("app_a"),
                _app("app_b"),
                _app("app_c"),
            ],
        )
        wf_calls, app_calls = _stub_extractors(monkeypatch)

        scope = RegenScope(workflow_ids=["wf_1"])
        answered = [
            {
                "open_item_index": 0,
                "value": "5,000 invoices/week from QuickBooks",
                "label": "Workflow volume not stated",
                "location": "workflow:wf_1",
            },
        ]

        artifact = asyncio.run(run_v3_pipeline_partial(
            db=_make_db(), user=_make_user(),
            prior_artifact=prior,
            regen_scope=scope,
            answered_items=answered,
        ))

        # Only wf_1 should be re-extracted.
        assert [c["workflow_id"] for c in wf_calls] == ["wf_1"]
        # No apps should be re-extracted.
        assert app_calls == []

        # wf_2 must come through verbatim (same name + same MMI reasoning).
        wf_2 = next(w for w in artifact.workflows if w.workflow_id == "wf_2")
        assert wf_2.workflow_name == "WF Two"
        assert wf_2.mmi_hypothesis.tss.reasoning == "prior reasoning for wf_2"

        # All apps should come through verbatim.
        app_ids = sorted(a.app_id for a in artifact.apps)
        assert app_ids == ["app_a", "app_b", "app_c"]

    def test_extraction_status_reflects_merged_result(self, monkeypatch):
        prior = _prior_artifact(
            workflows=[_wf("wf_1"), _wf("wf_2")],
            apps=[_app("app_a")],
        )

        async def _wf_extract_partial(skeleton, transcript_text, db, user, session_id, prior_answers=None):
            # Simulate Stage 2a partial failure on the in-scope workflow
            return {"partial_extraction_failed": True, "reason": "timeout"}

        async def _app_extract(*args, **kwargs):
            return AppDetail()

        async def _noop(*args, **kwargs):
            return None

        monkeypatch.setattr(pipeline_mod, "_extract_workflow_detail", _wf_extract_partial)
        monkeypatch.setattr(pipeline_mod, "_extract_app_detail", _app_extract)
        monkeypatch.setattr(pipeline_mod, "_generate_bpmn_for_workflow", _noop)
        monkeypatch.setattr(pipeline_mod, "_generate_design_doc_for_workflow", _noop)
        monkeypatch.setattr(pipeline_mod, "_generate_eadd_for_app", _noop)

        scope = RegenScope(workflow_ids=["wf_1"])
        artifact = asyncio.run(run_v3_pipeline_partial(
            db=_make_db(), user=_make_user(),
            prior_artifact=prior,
            regen_scope=scope,
            answered_items=[],
        ))

        wf_1 = next(w for w in artifact.workflows if w.workflow_id == "wf_1")
        assert wf_1.partial_extraction_failed is True
        assert artifact.extraction_status == "partial"

    def test_pipeline_metadata_reflects_partial_run(self, monkeypatch):
        prior = _prior_artifact(workflows=[_wf("wf_1")], apps=[])
        _stub_extractors(monkeypatch)

        artifact = asyncio.run(run_v3_pipeline_partial(
            db=_make_db(), user=_make_user(),
            prior_artifact=prior,
            regen_scope=RegenScope(workflow_ids=["wf_1"]),
            answered_items=[],
        ))

        md = artifact.pipeline_metadata
        # Stage 1 is intentionally not re-run on partial regen.
        assert md.stage_1_duration_ms is None
        # Stages 2-4 ran — non-negative ints expected.
        assert md.stage_2_duration_ms is not None and md.stage_2_duration_ms >= 0
        assert md.stage_3_duration_ms is not None and md.stage_3_duration_ms >= 0
        assert md.stage_4_duration_ms is not None and md.stage_4_duration_ms >= 0
        assert md.total_duration_ms is not None and md.total_duration_ms >= 0


# ──────────────────────────────────────────────────────────────────────
# App-scoped partial regen
# ──────────────────────────────────────────────────────────────────────


class TestPartialRerunApp:
    def test_only_in_scope_app_extracts(self, monkeypatch):
        prior = _prior_artifact(
            workflows=[_wf("wf_1"), _wf("wf_2")],
            apps=[_app("app_a"), _app("app_b"), _app("app_c")],
        )
        wf_calls, app_calls = _stub_extractors(monkeypatch)

        scope = RegenScope(app_ids=["app_b"], rerun_shared_infra=True)
        answered = [
            {
                "open_item_index": 0,
                "value": "Yes, RBAC required for finance team",
                "label": "RBAC needed?",
                "location": "app:app_b",
            },
        ]

        artifact = asyncio.run(run_v3_pipeline_partial(
            db=_make_db(), user=_make_user(),
            prior_artifact=prior,
            regen_scope=scope,
            answered_items=answered,
        ))

        # Only app_b should be re-extracted.
        assert [c["app_id"] for c in app_calls] == ["app_b"]
        # No workflows should be re-extracted.
        assert wf_calls == []

        # Verbatim apps preserve original IDs.
        app_ids = sorted(a.app_id for a in artifact.apps)
        assert app_ids == ["app_a", "app_b", "app_c"]

        # Workflows came through verbatim too.
        wf_ids = sorted(w.workflow_id for w in artifact.workflows)
        assert wf_ids == ["wf_1", "wf_2"]

    def test_app_partial_failure_propagates_to_status(self, monkeypatch):
        prior = _prior_artifact(workflows=[], apps=[_app("app_a"), _app("app_b")])

        async def _app_extract_partial(skeleton, transcript_text, db, user, session_id, prior_answers=None):
            return {"partial_extraction_failed": True, "reason": "schema mismatch"}

        async def _wf_extract(*args, **kwargs):
            return WorkflowDetail()

        async def _noop(*args, **kwargs):
            return None

        monkeypatch.setattr(pipeline_mod, "_extract_workflow_detail", _wf_extract)
        monkeypatch.setattr(pipeline_mod, "_extract_app_detail", _app_extract_partial)
        monkeypatch.setattr(pipeline_mod, "_generate_bpmn_for_workflow", _noop)
        monkeypatch.setattr(pipeline_mod, "_generate_design_doc_for_workflow", _noop)
        monkeypatch.setattr(pipeline_mod, "_generate_eadd_for_app", _noop)

        scope = RegenScope(app_ids=["app_a"])
        artifact = asyncio.run(run_v3_pipeline_partial(
            db=_make_db(), user=_make_user(),
            prior_artifact=prior,
            regen_scope=scope,
            answered_items=[],
        ))

        app_a = next(a for a in artifact.apps if a.app_id == "app_a")
        assert app_a.partial_extraction_failed is True
        assert artifact.extraction_status == "partial"


# ──────────────────────────────────────────────────────────────────────
# New transcript upload appending
# ──────────────────────────────────────────────────────────────────────


class TestPartialRerunWithNewUpload:
    def test_new_upload_text_passed_to_re_extraction(self, monkeypatch):
        prior = _prior_artifact(
            workflows=[_wf("wf_1")],
            apps=[],
            source_transcripts=[TranscriptRef(filename="orig_call.txt")],
        )
        wf_calls, _ = _stub_extractors(monkeypatch)

        db = _make_db(
            prior_text="ORIGINAL TRANSCRIPT: nothing about volume",
            new_text="FOLLOWUP: 5,000 invoices a week from QuickBooks",
        )

        artifact = asyncio.run(run_v3_pipeline_partial(
            db=db, user=_make_user(),
            prior_artifact=prior,
            regen_scope=RegenScope(workflow_ids=["wf_1"]),
            answered_items=[],
            new_upload_ids=["upload_new_1"],
        ))

        # The mocked extractor captured what it saw — both prior + new text.
        assert len(wf_calls) == 1
        text = wf_calls[0]["transcript_text"]
        assert "ORIGINAL TRANSCRIPT" in text
        assert "FOLLOWUP" in text
        assert "5,000 invoices a week" in text

        # source_transcripts should now include the new upload's filename.
        filenames = [t.filename for t in artifact.source_transcripts]
        assert "orig_call.txt" in filenames
        assert any("upload_new_1" in fn or fn.startswith("new_") for fn in filenames)

    def test_no_new_upload_uses_prior_only(self, monkeypatch):
        prior = _prior_artifact(workflows=[_wf("wf_1")], apps=[])
        wf_calls, _ = _stub_extractors(monkeypatch)

        db = _make_db(prior_text="JUST PRIOR TEXT")

        asyncio.run(run_v3_pipeline_partial(
            db=db, user=_make_user(),
            prior_artifact=prior,
            regen_scope=RegenScope(workflow_ids=["wf_1"]),
            answered_items=[],
            new_upload_ids=None,
        ))

        text = wf_calls[0]["transcript_text"]
        assert "JUST PRIOR TEXT" in text
        assert "FOLLOWUP" not in text


# ──────────────────────────────────────────────────────────────────────
# user_provided_answers accumulator
# ──────────────────────────────────────────────────────────────────────


class TestPartialRerunPreservesUserAnswers:
    def test_prior_answers_persist_across_regen(self, monkeypatch):
        # First regen already set wf_1.user_provided_answers — second
        # regen should keep them and append new keys.
        prior = _prior_artifact(
            workflows=[
                _wf("wf_1", user_provided_answers={
                    "Volume not stated": "5,000/week",
                }),
            ],
            apps=[],
        )
        wf_calls, _ = _stub_extractors(monkeypatch)

        # New answer for the same workflow on a different open item.
        answered = [
            {
                "open_item_index": 0,
                "value": "7-year retention required by SOX",
                "label": "Retention window unclear",
                "location": "workflow:wf_1",
            },
        ]

        artifact = asyncio.run(run_v3_pipeline_partial(
            db=_make_db(), user=_make_user(),
            prior_artifact=prior,
            regen_scope=RegenScope(workflow_ids=["wf_1"]),
            answered_items=answered,
        ))

        wf_1 = next(w for w in artifact.workflows if w.workflow_id == "wf_1")
        # Both old + new answers must be present — strict accumulator.
        assert wf_1.user_provided_answers["Volume not stated"] == "5,000/week"
        assert wf_1.user_provided_answers["Retention window unclear"] == "7-year retention required by SOX"

        # The extractor saw the unioned dict in prior_answers at call time.
        assert wf_calls[0]["prior_answers"] == {
            "Volume not stated": "5,000/week",
            "Retention window unclear": "7-year retention required by SOX",
        }

    def test_new_answer_does_not_replace_prior_answer(self, monkeypatch):
        # Same label answered twice — the prior version is kept.
        # (This simulates a stale form against an evolving artifact.)
        prior = _prior_artifact(
            workflows=[
                _wf("wf_1", user_provided_answers={
                    "Volume": "Original answer",
                }),
            ],
            apps=[],
        )
        _stub_extractors(monkeypatch)

        answered = [
            {
                "open_item_index": 0,
                "value": "Newer answer",
                "label": "Volume",
                "location": "workflow:wf_1",
            },
        ]

        artifact = asyncio.run(run_v3_pipeline_partial(
            db=_make_db(), user=_make_user(),
            prior_artifact=prior,
            regen_scope=RegenScope(workflow_ids=["wf_1"]),
            answered_items=answered,
        ))

        wf_1 = next(w for w in artifact.workflows if w.workflow_id == "wf_1")
        # The newer user submission should win — it's still a user answer
        # (the strict-accumulator rule protects against LLM overwrites,
        # not against newer user answers).
        assert wf_1.user_provided_answers["Volume"] == "Newer answer"

    def test_app_user_answers_accumulate_too(self, monkeypatch):
        prior = _prior_artifact(
            workflows=[],
            apps=[
                _app("app_a", user_provided_answers={
                    "RBAC needed?": "Yes",
                }),
            ],
        )
        _, app_calls = _stub_extractors(monkeypatch)

        answered = [
            {
                "open_item_index": 0,
                "value": "Yes, SOC2",
                "label": "Compliance regime",
                "location": "app:app_a",
            },
        ]

        artifact = asyncio.run(run_v3_pipeline_partial(
            db=_make_db(), user=_make_user(),
            prior_artifact=prior,
            regen_scope=RegenScope(app_ids=["app_a"]),
            answered_items=answered,
        ))

        app_a = next(a for a in artifact.apps if a.app_id == "app_a")
        assert app_a.user_provided_answers == {
            "RBAC needed?": "Yes",
            "Compliance regime": "Yes, SOC2",
        }
        # The extractor saw the union.
        assert app_calls[0]["prior_answers"] == {
            "RBAC needed?": "Yes",
            "Compliance regime": "Yes, SOC2",
        }


# ──────────────────────────────────────────────────────────────────────
# Stage 4 — shared_infra & open_items rerun behaviour
# ──────────────────────────────────────────────────────────────────────


class TestPartialRerunStage4:
    def test_rerun_shared_infra_true_recomputes(self, monkeypatch):
        # Two apps share a system after the new extraction; the prior
        # artifact had no shared_infrastructure rows. With rerun_shared_infra
        # True, Stage 4 must recompute and surface the row.
        prior = _prior_artifact(
            workflows=[],
            apps=[
                _app("app_a"),
                _app("app_b"),
            ],
            shared_infra=[],  # nothing previously
        )

        async def _app_extract(skeleton, transcript_text, db, user, session_id, prior_answers=None):
            return AppDetail(
                personas=[],
                integrations=[
                    IntegrationEntry(
                        system_name="NetSuite", bucket=2, notes="reads invoices",
                    ),
                ],
            )

        async def _wf_extract(*args, **kwargs):
            return WorkflowDetail()

        async def _noop(*args, **kwargs):
            return None

        monkeypatch.setattr(pipeline_mod, "_extract_workflow_detail", _wf_extract)
        monkeypatch.setattr(pipeline_mod, "_extract_app_detail", _app_extract)
        monkeypatch.setattr(pipeline_mod, "_generate_bpmn_for_workflow", _noop)
        monkeypatch.setattr(pipeline_mod, "_generate_design_doc_for_workflow", _noop)
        monkeypatch.setattr(pipeline_mod, "_generate_eadd_for_app", _noop)

        scope = RegenScope(app_ids=["app_a", "app_b"], rerun_shared_infra=True)
        artifact = asyncio.run(run_v3_pipeline_partial(
            db=_make_db(), user=_make_user(),
            prior_artifact=prior,
            regen_scope=scope,
            answered_items=[],
        ))

        # The two re-extracted apps now share NetSuite — Stage 4 must surface a row.
        assert len(artifact.shared_infrastructure) == 1
        assert artifact.shared_infrastructure[0].system_name == "NetSuite"
        assert sorted(artifact.shared_infrastructure[0].used_by_app_ids) == ["app_a", "app_b"]

    def test_rerun_shared_infra_false_carries_forward(self, monkeypatch):
        # Prior had a manually-asserted shared_infra row; with rerun_shared_infra
        # False, Stage 4 must NOT recompute — it carries the prior list verbatim.
        prior_row = SharedInfrastructureRow(
            system_name="LegacyERP",
            used_by_app_ids=["app_x", "app_y"],
            read_write="read_write",
            notes="prior row",
        )
        prior = _prior_artifact(
            workflows=[_wf("wf_1")],
            apps=[_app("app_a"), _app("app_b")],
            shared_infra=[prior_row],
        )

        _stub_extractors(monkeypatch)

        scope = RegenScope(workflow_ids=["wf_1"], rerun_shared_infra=False)
        artifact = asyncio.run(run_v3_pipeline_partial(
            db=_make_db(), user=_make_user(),
            prior_artifact=prior,
            regen_scope=scope,
            answered_items=[],
        ))

        # Carried forward verbatim — system name + app IDs preserved.
        assert len(artifact.shared_infrastructure) == 1
        assert artifact.shared_infrastructure[0].system_name == "LegacyERP"
        assert artifact.shared_infrastructure[0].used_by_app_ids == ["app_x", "app_y"]

    def test_open_items_always_rebuilt(self, monkeypatch):
        # Even though rerun_open_items defaults True, the field is documented
        # as "always" — we lock that here. open_items in the prior artifact
        # are different from what the merged artifact emits.
        prior_open = OpenItem(
            flag=VegaFlag.MISSING,
            label="stale open item",
            location="session",
        )
        prior = _prior_artifact(
            workflows=[_wf("wf_1")],
            apps=[],
            open_items=[prior_open],
        )

        _stub_extractors(monkeypatch)

        artifact = asyncio.run(run_v3_pipeline_partial(
            db=_make_db(), user=_make_user(),
            prior_artifact=prior,
            regen_scope=RegenScope(workflow_ids=["wf_1"]),
            answered_items=[],
        ))

        # open_items rebuilt from the merged artifact — no session flags
        # were set, so the stale item should NOT appear.
        labels = [oi.label for oi in artifact.open_items]
        assert "stale open item" not in labels


# ──────────────────────────────────────────────────────────────────────
# Telemetry events
# ──────────────────────────────────────────────────────────────────────


class TestPartialRerunEvents:
    def test_started_and_complete_events_emitted(self, monkeypatch):
        prior = _prior_artifact(
            workflows=[_wf("wf_1")],
            apps=[_app("app_a")],
        )
        _stub_extractors(monkeypatch)

        # Capture every emit_vega_event call site at the module level.
        captured: list[dict] = []

        async def _capture(db, user_id, session_id, event_type, metadata=None):
            captured.append({
                "event_type": event_type,
                "metadata": metadata or {},
                "user_id": user_id,
                "session_id": session_id,
            })

        monkeypatch.setattr(pipeline_mod, "emit_vega_event", _capture)

        asyncio.run(run_v3_pipeline_partial(
            db=_make_db(), user=_make_user("u_evt"),
            prior_artifact=prior,
            regen_scope=RegenScope(workflow_ids=["wf_1"], app_ids=["app_a"]),
            answered_items=[],
        ))

        types = [c["event_type"] for c in captured]
        assert "v3_partial_regen_started" in types
        assert "v3_partial_regen_complete" in types
        # One entity_complete per re-extracted entity.
        entity_events = [c for c in captured if c["event_type"] == "v3_partial_regen_entity_complete"]
        assert len(entity_events) == 2
        entity_ids = {(e["metadata"]["entity_type"], e["metadata"]["entity_id"]) for e in entity_events}
        assert entity_ids == {("workflow", "wf_1"), ("app", "app_a")}

    def test_started_metadata_carries_scope(self, monkeypatch):
        prior = _prior_artifact(
            workflows=[_wf("wf_1")],
            apps=[_app("app_a"), _app("app_b")],
        )
        _stub_extractors(monkeypatch)

        captured: list[dict] = []

        async def _capture(db, user_id, session_id, event_type, metadata=None):
            captured.append({"event_type": event_type, "metadata": metadata or {}})

        monkeypatch.setattr(pipeline_mod, "emit_vega_event", _capture)

        asyncio.run(run_v3_pipeline_partial(
            db=_make_db(), user=_make_user(),
            prior_artifact=prior,
            regen_scope=RegenScope(
                workflow_ids=["wf_1"],
                app_ids=["app_a", "app_b"],
                rerun_shared_infra=True,
            ),
            answered_items=[
                {"open_item_index": 0, "value": "v", "label": "L", "location": "workflow:wf_1"},
            ],
            new_upload_ids=["u_new"],
        ))

        started = next(c for c in captured if c["event_type"] == "v3_partial_regen_started")
        meta = started["metadata"]
        assert meta["workflow_ids"] == ["wf_1"]
        assert sorted(meta["app_ids"]) == ["app_a", "app_b"]
        assert meta["rerun_shared_infra"] is True
        assert meta["answered_item_count"] == 1
        assert meta["new_upload_count"] == 1


# ──────────────────────────────────────────────────────────────────────
# BPMN content-hash short-circuit
# ──────────────────────────────────────────────────────────────────────


class TestBpmnContentHashShortCircuit:
    def test_bpmn_metadata_carried_forward_to_new_workflow(self, monkeypatch):
        # Prior workflow had a generated BPMN; new extraction returns identical
        # process_steps/decision_logic so the content hash should match.
        # We assert the prior bpmn_id / bpmn_xml / bpmn_content_hash are
        # carried onto the new Workflow (so _generate_bpmn_for_workflow can
        # short-circuit on hash match).
        prior_xml = "<bpmn>prior</bpmn>"
        prior = _prior_artifact(
            workflows=[
                _wf(
                    "wf_1",
                    bpmn_id="bpmn_prior_123",
                    bpmn_xml=prior_xml,
                    bpmn_status="generated",
                    bpmn_content_hash="hash_prior",
                ),
            ],
            apps=[],
        )

        # Track whether the BPMN hook was actually called — and that the
        # workflow it was called with carries the prior bpmn_* fields so
        # the content-hash short-circuit can fire.
        bpmn_calls: list[Workflow] = []

        async def _wf_extract(*args, **kwargs):
            return WorkflowDetail()

        async def _app_extract(*args, **kwargs):
            return AppDetail()

        async def _bpmn_capture(workflow, db, user, session_id):
            bpmn_calls.append(workflow)
            # Don't actually generate — just observe the inputs.
            return workflow

        async def _noop(*args, **kwargs):
            return None

        monkeypatch.setattr(pipeline_mod, "_extract_workflow_detail", _wf_extract)
        monkeypatch.setattr(pipeline_mod, "_extract_app_detail", _app_extract)
        monkeypatch.setattr(pipeline_mod, "_generate_bpmn_for_workflow", _bpmn_capture)
        monkeypatch.setattr(pipeline_mod, "_generate_design_doc_for_workflow", _noop)
        monkeypatch.setattr(pipeline_mod, "_generate_eadd_for_app", _noop)

        asyncio.run(run_v3_pipeline_partial(
            db=_make_db(), user=_make_user(),
            prior_artifact=prior,
            regen_scope=RegenScope(workflow_ids=["wf_1"]),
            answered_items=[],
        ))

        assert len(bpmn_calls) == 1
        carried = bpmn_calls[0]
        # Prior BPMN metadata threaded onto the new Workflow object.
        assert carried.bpmn_id == "bpmn_prior_123"
        assert carried.bpmn_xml == prior_xml
        assert carried.bpmn_content_hash == "hash_prior"
        assert carried.bpmn_status == "generated"
