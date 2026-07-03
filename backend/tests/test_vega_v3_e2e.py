"""End-to-end tests for Vega v3 pipeline + export endpoints.

Covers:
  1. Full pipeline run — four stages mocked, DiscoveryAnalysisV3 shape validated.
  2. All six VegaFlag values round-trip through aggregate_open_items, blocking sort.
  3. shared_infrastructure detects 2+ apps sharing a system.
  4. Dispatcher path — model_dump(mode="json") payload has the expected top-level keys.
  5. Export endpoints — every Wave 3e endpoint returns 200 + non-empty body.
  6. IDOR guard — cross-user request returns 403.
  7. MMI confidence enrichment — WorkflowDetail.mmi_hypothesis validation + _merge_workflow prefer.

No live LLM calls. All async hooks are stubbed with AsyncMock / monkeypatch.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# auth_helper requires JWT_SECRET at module import time
os.environ.setdefault("JWT_SECRET", "test-secret")

# ---------------------------------------------------------------------------
# Imports (after sys.path + env set)
# ---------------------------------------------------------------------------
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
    NFRs,
    OpenItem,
    PersonaRow,
    PipelineMetadata,
    ScaleProfile,
    SessionFlags,
    SharedInfrastructureRow,
    SourceProvenance,
    TranscriptRef,
    Workflow,
    WorkflowDetail,
    WorkflowSkeleton,
)
from services.vega.postprocess import aggregate_open_items, detect_shared_infrastructure
from services.vega.v3_pipeline import _merge_workflow, _seed_mmi_from_skeleton, run_v3_pipeline


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str = "user_a", perms: list[str] | None = None) -> Any:
    """Minimal user shim. Only user_id + permissions needed."""
    user = MagicMock()
    user.user_id = user_id
    user.permissions = perms if perms is not None else ["vega.use"]
    # has_perm does a direct membership check when no closure is stashed
    user._permission_closure = None
    return user


def _make_db_with_transcript(text: str = "REP: hi\nCUSTOMER: We process about 500 invoices a month") -> Any:
    """Mock Mongo client: chat_uploads.find_one returns a doc with extracted_text."""
    db = MagicMock()
    db.chat_uploads.find_one = AsyncMock(return_value={
        "upload_id": "u_test",
        "user_id": "user_a",
        "filename": "call.txt",
        "extracted_text": text,
    })
    db.chat_uploads.update_one = AsyncMock()
    db.vega_events.insert_one = AsyncMock()
    db.bpmn_diagrams.insert_one = AsyncMock()
    return db


def _make_seed_mmi() -> MMIHypothesis:
    """Minimal valid MMIHypothesis for test fixtures."""
    dim = lambda v: MMIDimension(value=v, confidence="medium", reasoning="test")
    return MMIHypothesis(
        tss=dim(3), ie=dim(3), time=dim(3), cost=dim(3),
        quality=dim(3), satisfaction=dim(3),
    )


def _make_source() -> SourceProvenance:
    return SourceProvenance(quote="Customer said this in the call.")


def _make_workflow(
    wf_id: str = "wf_invoice",
    partial: bool = False,
    acceptance_criteria: list[AcceptanceCriterion] | None = None,
    persona_overrides: list[PersonaRow] | None = None,
) -> Workflow:
    return Workflow(
        workflow_id=wf_id,
        workflow_name=f"Workflow {wf_id}",
        source=_make_source(),
        mmi_hypothesis=_make_seed_mmi(),
        partial_extraction_failed=partial,
        partial_extraction_reason="timeout" if partial else None,
        acceptance_criteria=acceptance_criteria or [],
        persona_overrides=persona_overrides or [],
    )


def _make_app(
    app_id: str = "app_dispatch",
    partial: bool = False,
    integrations: list[IntegrationEntry] | None = None,
    personas: list[PersonaRow] | None = None,
    scale_flag: VegaFlag | None = None,
    nfr_flags: list[VegaFlag] | None = None,
    linked_workflow_ids: list[str] | None = None,
) -> App:
    scale = ScaleProfile(flag=scale_flag)
    nfrs = NFRs(flags=nfr_flags or [])
    return App(
        app_id=app_id,
        app_name=f"App {app_id}",
        source=_make_source(),
        partial_extraction_failed=partial,
        partial_extraction_reason="timeout" if partial else None,
        integrations=integrations or [],
        personas=personas or [],
        scale_profile=scale,
        nfrs=nfrs,
        linked_workflow_ids=linked_workflow_ids or [],
    )


def _make_v3_artifact(
    artifact_id: str | None = None,
    user_id: str = "user_a",
    workflows: list[Workflow] | None = None,
    apps: list[App] | None = None,
    session_flags: SessionFlags | None = None,
) -> dict:
    """Build a full ai_chat_artifacts document dict as persisted to Mongo."""
    art_id = artifact_id or f"art_{uuid.uuid4().hex[:12]}"
    v3 = DiscoveryAnalysisV3(
        client_company="Acme Corp",
        workflows=workflows or [],
        apps=apps or [],
        session_flags=session_flags or SessionFlags(),
        pipeline_metadata=PipelineMetadata(
            stage_1_duration_ms=50,
            stage_2_duration_ms=120,
            stage_3_duration_ms=80,
            stage_4_duration_ms=10,
            total_duration_ms=260,
        ),
        extraction_status="complete",
    )
    payload = v3.model_dump(mode="json")
    payload["session_id"] = "sess_fixture"
    payload["discovery_mode"] = "single_call"
    return {
        "artifact_id": art_id,
        "user_id": user_id,
        "artifact_type": "discovery_analysis_v3",
        "session_id": "sess_fixture",
        "payload": payload,
        "status": "delivered",
    }


# ---------------------------------------------------------------------------
# Test 1 — Full pipeline run produces a correct v3 artifact
# ---------------------------------------------------------------------------


class TestFullPipelineRun:
    """Mocks all four stages and validates the assembled DiscoveryAnalysisV3."""

    def _build_canned_stages(self):
        """Return consistent canned data: 2 workflows (1 partial), 3 apps (1 partial, 1 standalone)."""
        wf_good = _make_workflow("wf_good")
        wf_partial = _make_workflow("wf_partial", partial=True)

        app_good = _make_app("app_good", linked_workflow_ids=["wf_good"])
        app_standalone = _make_app("app_standalone")  # no linked workflows = standalone
        app_partial = _make_app("app_partial", partial=True)

        return [wf_good, wf_partial], [app_good, app_standalone, app_partial]

    def test_full_pipeline_returns_v3_artifact(self, monkeypatch):
        db = _make_db_with_transcript()
        user = _make_user()

        workflows, apps = self._build_canned_stages()

        async def _stub_stage_1(*a, **kw):
            return ("Acme Corp", [TranscriptRef(filename="call.txt")], SessionFlags(), [], [])

        async def _stub_stage_2(*a, **kw):
            return workflows, apps

        async def _stub_stage_3(*a, **kw):
            return None

        def _stub_stage_4(workflows, apps, session_flags):
            return [], []

        monkeypatch.setattr(pipeline_mod, "_stage_1_skeleton", _stub_stage_1)
        monkeypatch.setattr(pipeline_mod, "_stage_2_details", _stub_stage_2)
        monkeypatch.setattr(pipeline_mod, "_stage_3_bpmn_and_design_docs", _stub_stage_3)
        monkeypatch.setattr(pipeline_mod, "_stage_4_postprocess", _stub_stage_4)

        artifact = asyncio.run(run_v3_pipeline(
            db=db, user=user, upload_ids=["u_test"],
            chat_session_id="sess_e2e",
        ))

        assert isinstance(artifact, DiscoveryAnalysisV3)
        assert artifact.client_company == "Acme Corp"
        assert len(artifact.workflows) == 2
        assert len(artifact.apps) == 3

    def test_extraction_status_is_partial_when_any_entity_failed(self, monkeypatch):
        db = _make_db_with_transcript()
        user = _make_user()

        workflows, apps = self._build_canned_stages()

        async def _stub_stage_1(*a, **kw):
            return ("Acme Corp", [], SessionFlags(), [], [])

        async def _stub_stage_2(*a, **kw):
            return workflows, apps

        async def _stub_stage_3(*a, **kw):
            return None

        def _stub_stage_4(workflows, apps, session_flags):
            return [], []

        monkeypatch.setattr(pipeline_mod, "_stage_1_skeleton", _stub_stage_1)
        monkeypatch.setattr(pipeline_mod, "_stage_2_details", _stub_stage_2)
        monkeypatch.setattr(pipeline_mod, "_stage_3_bpmn_and_design_docs", _stub_stage_3)
        monkeypatch.setattr(pipeline_mod, "_stage_4_postprocess", _stub_stage_4)

        artifact = asyncio.run(run_v3_pipeline(
            db=db, user=user, upload_ids=["u_test"],
            chat_session_id="sess_e2e",
        ))

        # wf_partial and app_partial both have partial_extraction_failed=True
        assert artifact.extraction_status == "partial"

    def test_pipeline_metadata_all_four_stage_durations_present(self, monkeypatch):
        db = _make_db_with_transcript()
        user = _make_user()

        async def _stub_stage_1(*a, **kw):
            return ("Acme Corp", [], SessionFlags(), [], [])

        async def _stub_stage_2(*a, **kw):
            return [], []

        async def _stub_stage_3(*a, **kw):
            return None

        def _stub_stage_4(workflows, apps, session_flags):
            return [], []

        monkeypatch.setattr(pipeline_mod, "_stage_1_skeleton", _stub_stage_1)
        monkeypatch.setattr(pipeline_mod, "_stage_2_details", _stub_stage_2)
        monkeypatch.setattr(pipeline_mod, "_stage_3_bpmn_and_design_docs", _stub_stage_3)
        monkeypatch.setattr(pipeline_mod, "_stage_4_postprocess", _stub_stage_4)

        artifact = asyncio.run(run_v3_pipeline(
            db=db, user=user, upload_ids=["u_test"],
            chat_session_id="sess_e2e",
        ))

        md = artifact.pipeline_metadata
        assert md.stage_1_duration_ms is not None and md.stage_1_duration_ms >= 0
        assert md.stage_2_duration_ms is not None and md.stage_2_duration_ms >= 0
        assert md.stage_3_duration_ms is not None and md.stage_3_duration_ms >= 0
        assert md.stage_4_duration_ms is not None and md.stage_4_duration_ms >= 0
        assert md.total_duration_ms is not None
        assert md.total_duration_ms >= max(
            md.stage_1_duration_ms, md.stage_2_duration_ms,
            md.stage_3_duration_ms, md.stage_4_duration_ms,
        )

    def test_standalone_app_has_empty_linked_workflow_ids(self, monkeypatch):
        db = _make_db_with_transcript()
        user = _make_user()

        workflows, apps = self._build_canned_stages()

        async def _stub_stage_1(*a, **kw):
            return ("Acme Corp", [], SessionFlags(), [], [])

        async def _stub_stage_2(*a, **kw):
            return workflows, apps

        async def _stub_stage_3(*a, **kw):
            return None

        def _stub_stage_4(workflows, apps, session_flags):
            return [], []

        monkeypatch.setattr(pipeline_mod, "_stage_1_skeleton", _stub_stage_1)
        monkeypatch.setattr(pipeline_mod, "_stage_2_details", _stub_stage_2)
        monkeypatch.setattr(pipeline_mod, "_stage_3_bpmn_and_design_docs", _stub_stage_3)
        monkeypatch.setattr(pipeline_mod, "_stage_4_postprocess", _stub_stage_4)

        artifact = asyncio.run(run_v3_pipeline(
            db=db, user=user, upload_ids=["u_test"],
            chat_session_id="sess_e2e",
        ))

        standalone = [a for a in artifact.apps if a.app_id == "app_standalone"]
        assert len(standalone) == 1
        assert standalone[0].linked_workflow_ids == []


# ---------------------------------------------------------------------------
# Test 2 — All six VegaFlag values round-trip into open_items
# ---------------------------------------------------------------------------


class TestVegaFlagRoundTrip:
    """Every VegaFlag value must appear in the aggregated open_items."""

    def _build_all_flags_fixture(self):
        """Build session_flags + workflows + apps that each carry one distinct flag."""
        # Session flags cover: MISSING, NOT_DISCUSSED, NOT_STATED
        session_flags = SessionFlags(
            tech_contact=None,
            tech_contact_flag=VegaFlag.MISSING,
            engineering_capacity=None,
            engineering_capacity_flag=VegaFlag.NOT_DISCUSSED,
            it_infosec_gate=None,
            it_infosec_gate_flag=VegaFlag.NOT_STATED,
        )

        # Workflow acceptance criterion covers: INFERRED_CONFIRM
        ac = AcceptanceCriterion(
            flow_name="invoice intake",
            success_when=None,
            flag=VegaFlag.INFERRED_CONFIRM,
        )
        wf = _make_workflow("wf_flags", acceptance_criteria=[ac])

        # App integration covers: NEEDS_TECHNICAL_FOLLOWUP
        # App scale_profile covers: SOURCE_UNCLEAR
        integration = IntegrationEntry(
            system_name="NetSuite",
            bucket=3,
            flags=[VegaFlag.NEEDS_TECHNICAL_FOLLOWUP],
        )
        app = _make_app(
            "app_flags",
            integrations=[integration],
            scale_flag=VegaFlag.SOURCE_UNCLEAR,
        )

        return session_flags, [wf], [app]

    def test_all_six_flags_appear_in_open_items(self):
        session_flags, workflows, apps = self._build_all_flags_fixture()
        open_items = aggregate_open_items(workflows, apps, session_flags)

        present_flags = {item.flag for item in open_items}
        for flag in VegaFlag:
            assert flag in present_flags, f"VegaFlag.{flag.name} not represented in open_items"

    def test_blocking_flags_sort_before_non_blocking(self):
        session_flags, workflows, apps = self._build_all_flags_fixture()
        open_items = aggregate_open_items(workflows, apps, session_flags)

        # Find the index of the first non-blocking item
        from services.vega.flags import is_blocking_for_handoff
        first_non_blocking = next(
            (i for i, item in enumerate(open_items) if not is_blocking_for_handoff(item.flag)),
            None,
        )
        if first_non_blocking is not None:
            # All items before that index must be blocking
            for i in range(first_non_blocking):
                assert is_blocking_for_handoff(open_items[i].flag), (
                    f"Non-blocking item at index {i} appears before first non-blocking at {first_non_blocking}"
                )

    def test_open_items_have_required_fields(self):
        session_flags, workflows, apps = self._build_all_flags_fixture()
        open_items = aggregate_open_items(workflows, apps, session_flags)

        for item in open_items:
            assert isinstance(item.flag, VegaFlag)
            assert item.label and len(item.label) > 0
            assert item.location and len(item.location) > 0


# ---------------------------------------------------------------------------
# Test 3 — shared_infrastructure populates when 2+ apps share a system
# ---------------------------------------------------------------------------


class TestSharedInfrastructure:
    def test_shared_system_emits_one_row_with_both_app_ids(self):
        shared_system = "NetSuite"
        app_a = _make_app("app_a", integrations=[
            IntegrationEntry(system_name=shared_system, bucket=2, notes="reads data"),
        ])
        app_b = _make_app("app_b", integrations=[
            IntegrationEntry(system_name=shared_system, bucket=2, notes="writes updates"),
        ])

        rows = detect_shared_infrastructure(workflows=[], apps=[app_a, app_b])

        assert len(rows) == 1
        row = rows[0]
        assert row.system_name == shared_system
        assert "app_a" in row.used_by_app_ids
        assert "app_b" in row.used_by_app_ids
        assert len(row.used_by_app_ids) == 2

    def test_unshared_system_produces_no_row(self):
        app_a = _make_app("app_a", integrations=[
            IntegrationEntry(system_name="SystemA", bucket=2),
        ])
        app_b = _make_app("app_b", integrations=[
            IntegrationEntry(system_name="SystemB", bucket=2),
        ])

        rows = detect_shared_infrastructure(workflows=[], apps=[app_a, app_b])

        assert rows == []

    def test_partial_extraction_app_skipped(self):
        """Partial apps have no integration data — should be skipped, not crash."""
        app_good = _make_app("app_good", integrations=[
            IntegrationEntry(system_name="SharedSystem", bucket=2),
        ])
        app_partial = _make_app("app_partial", partial=True)

        rows = detect_shared_infrastructure(workflows=[], apps=[app_good, app_partial])

        # Only one good app has "SharedSystem" — no shared row expected
        assert rows == []

    def test_three_apps_all_sharing_same_system(self):
        apps = [
            _make_app(f"app_{i}", integrations=[
                IntegrationEntry(system_name="Salesforce", bucket=1),
            ])
            for i in range(3)
        ]

        rows = detect_shared_infrastructure(workflows=[], apps=apps)

        assert len(rows) == 1
        assert len(rows[0].used_by_app_ids) == 3


# ---------------------------------------------------------------------------
# Test 4 — Dispatcher swap persists artifact with correct model_dump shape
# ---------------------------------------------------------------------------


class TestDispatcherPayloadShape:
    """Verify model_dump(mode='json') output matches the frontend renderer's expected shape."""

    REQUIRED_KEYS = {
        "version",
        "client_company",
        "workflows",
        "apps",
        "session_flags",
        "extraction_status",
        "pipeline_metadata",
        "shared_infrastructure",
        "open_items",
    }

    def test_payload_has_required_top_level_keys(self, monkeypatch):
        db = _make_db_with_transcript()
        user = _make_user()

        async def _stub_stage_1(*a, **kw):
            return ("Acme Corp", [], SessionFlags(), [], [])

        async def _stub_stage_2(*a, **kw):
            return [], []

        async def _stub_stage_3(*a, **kw):
            return None

        def _stub_stage_4(workflows, apps, session_flags):
            return [], []

        monkeypatch.setattr(pipeline_mod, "_stage_1_skeleton", _stub_stage_1)
        monkeypatch.setattr(pipeline_mod, "_stage_2_details", _stub_stage_2)
        monkeypatch.setattr(pipeline_mod, "_stage_3_bpmn_and_design_docs", _stub_stage_3)
        monkeypatch.setattr(pipeline_mod, "_stage_4_postprocess", _stub_stage_4)

        artifact = asyncio.run(run_v3_pipeline(
            db=db, user=user, upload_ids=["u_test"],
            chat_session_id="sess_e2e",
        ))

        payload = artifact.model_dump(mode="json")
        missing = self.REQUIRED_KEYS - set(payload.keys())
        assert not missing, f"Payload missing required keys: {missing}"

    def test_version_sentinel_is_v3(self, monkeypatch):
        db = _make_db_with_transcript()
        user = _make_user()

        async def _stub_stage_1(*a, **kw):
            return ("Acme Corp", [], SessionFlags(), [], [])

        async def _stub_stage_2(*a, **kw):
            return [], []

        async def _stub_stage_3(*a, **kw):
            return None

        def _stub_stage_4(workflows, apps, session_flags):
            return [], []

        monkeypatch.setattr(pipeline_mod, "_stage_1_skeleton", _stub_stage_1)
        monkeypatch.setattr(pipeline_mod, "_stage_2_details", _stub_stage_2)
        monkeypatch.setattr(pipeline_mod, "_stage_3_bpmn_and_design_docs", _stub_stage_3)
        monkeypatch.setattr(pipeline_mod, "_stage_4_postprocess", _stub_stage_4)

        artifact = asyncio.run(run_v3_pipeline(
            db=db, user=user, upload_ids=["u_test"],
            chat_session_id="sess_e2e",
        ))
        payload = artifact.model_dump(mode="json")
        assert payload["version"] == "v3"

    def test_pipeline_metadata_is_dict_with_stage_keys(self, monkeypatch):
        db = _make_db_with_transcript()
        user = _make_user()

        async def _stub_stage_1(*a, **kw):
            return ("Acme Corp", [], SessionFlags(), [], [])

        async def _stub_stage_2(*a, **kw):
            return [], []

        async def _stub_stage_3(*a, **kw):
            return None

        def _stub_stage_4(workflows, apps, session_flags):
            return [], []

        monkeypatch.setattr(pipeline_mod, "_stage_1_skeleton", _stub_stage_1)
        monkeypatch.setattr(pipeline_mod, "_stage_2_details", _stub_stage_2)
        monkeypatch.setattr(pipeline_mod, "_stage_3_bpmn_and_design_docs", _stub_stage_3)
        monkeypatch.setattr(pipeline_mod, "_stage_4_postprocess", _stub_stage_4)

        artifact = asyncio.run(run_v3_pipeline(
            db=db, user=user, upload_ids=["u_test"],
            chat_session_id="sess_e2e",
        ))
        payload = artifact.model_dump(mode="json")
        pm = payload["pipeline_metadata"]
        assert isinstance(pm, dict)
        for key in ("stage_1_duration_ms", "stage_2_duration_ms", "stage_3_duration_ms", "stage_4_duration_ms", "total_duration_ms"):
            assert key in pm, f"pipeline_metadata missing key: {key}"


# ---------------------------------------------------------------------------
# Test 5 — Export endpoints
# ---------------------------------------------------------------------------


def _mock_db_for_export(artifact_doc: dict) -> Any:
    """Build a mock db handle that returns the given artifact from find_one."""
    db = MagicMock()
    db.ai_chat_artifacts.find_one = AsyncMock(return_value=artifact_doc)
    db.vega_events.insert_one = AsyncMock()
    return db



def _make_user_with_perm(user_id: str = "user_a") -> Any:
    """User with vega.use permission, compatible with has_perm direct check."""
    user = MagicMock()
    user.user_id = user_id
    user.permissions = ["vega.use"]
    user._permission_closure = None
    return user


class TestExportEndpoints:
    """Drive export_v3_artifact directly (no HTTP layer, same as smoke_v3_wave3.py)."""

    @pytest.fixture(scope="class")
    def fixture_artifact(self):
        """Reusable v3 artifact with one workflow + design_doc, one app + eadd_doc."""
        wf = _make_workflow("wf_export_test")
        wf.design_doc = {
            "executive_summary": "This workflow automates invoice processing.",
            "current_state": {
                "process_description": "Manual review of invoices takes 2 hours per batch.",
                "pain_points": ["slow", "error-prone"],
                "systems_involved": ["QuickBooks", "Excel"],
            },
            "proposed_ai_solution": {
                "solution_description": "AI classifies and routes invoices automatically.",
                "ai_role": "classify and route",
                "human_role": "exception handling",
                "key_capabilities": ["OCR", "rule-based routing"],
            },
            "status": "generated",
        }
        wf.design_doc_status = "generated"

        app = _make_app("app_export_test")
        app.eadd_doc = {
            "overview": "Invoice processing app for finance team.",
            "app_purpose": "Digitize AP workflow.",
            "tech_stack_notes": "React + FastAPI + Mongo.",
            "data_model": ["Invoice", "Vendor", "Payment"],
            "integrations": [{"name": "QuickBooks", "purpose": "sync payments"}],
            "user_flows": ["upload invoice", "review classification"],
            "status": "generated",
        }
        app.design_doc_status = "generated"

        return _make_v3_artifact(
            artifact_id="art_export_fixture",
            user_id="user_a",
            workflows=[wf],
            apps=[app],
        )

    def _call_export(self, artifact_doc, user, target, fmt, entity_id=None):
        """Helper: patch get_db in the exports module, call handler, return response."""
        from routes.vega_v3_exports import export_v3_artifact
        from routes import vega_v3_exports as exports_mod
        db = _mock_db_for_export(artifact_doc)
        with patch.object(exports_mod, "get_db", return_value=db):
            return asyncio.run(export_v3_artifact(
                artifact_id=artifact_doc["artifact_id"],
                target=target,
                entity_id=entity_id,
                format=fmt,
                current_user=user,
            ))

    def test_analysis_markdown_returns_200_with_content(self, fixture_artifact):
        user = _make_user_with_perm("user_a")
        resp = self._call_export(fixture_artifact, user, "analysis", "markdown")
        assert hasattr(resp, "body"), "Expected a Response object"
        assert resp.media_type == "text/markdown"
        assert len(resp.body) > 100

    def test_analysis_pdf_returns_200(self, fixture_artifact):
        user = _make_user_with_perm("user_a")
        resp = self._call_export(fixture_artifact, user, "analysis", "pdf")
        assert hasattr(resp, "body")
        # Either PDF or markdown fallback — both are acceptable
        assert resp.media_type in ("application/pdf", "text/markdown")
        if resp.media_type == "text/markdown":
            assert resp.headers.get("X-Export-Fallback") == "reportlab-missing"
        assert len(resp.body) > 100

    def test_analysis_json_returns_payload_dict(self, fixture_artifact):
        user = _make_user_with_perm("user_a")
        result = self._call_export(fixture_artifact, user, "analysis", "json")
        assert isinstance(result, dict)
        assert "version" in result or "client_company" in result  # payload keys

    def test_design_doc_markdown_returns_200_with_content(self, fixture_artifact):
        user = _make_user_with_perm("user_a")
        resp = self._call_export(
            fixture_artifact, user, "design_doc", "markdown",
            entity_id="wf_export_test",
        )
        assert hasattr(resp, "body")
        assert resp.media_type == "text/markdown"
        assert len(resp.body) > 100

    def test_eadd_markdown_returns_200_with_content(self, fixture_artifact):
        user = _make_user_with_perm("user_a")
        resp = self._call_export(
            fixture_artifact, user, "eadd", "markdown",
            entity_id="app_export_test",
        )
        assert hasattr(resp, "body")
        assert resp.media_type == "text/markdown"
        assert len(resp.body) > 100

    def test_design_doc_without_entity_id_returns_400(self, fixture_artifact):
        from fastapi import HTTPException
        user = _make_user_with_perm("user_a")
        with pytest.raises(HTTPException) as exc_info:
            self._call_export(fixture_artifact, user, "design_doc", "markdown", entity_id=None)
        assert exc_info.value.status_code == 400

    def test_design_doc_with_missing_workflow_id_returns_404(self, fixture_artifact):
        from fastapi import HTTPException
        user = _make_user_with_perm("user_a")
        with pytest.raises(HTTPException) as exc_info:
            self._call_export(
                fixture_artifact, user, "design_doc", "markdown",
                entity_id="wf_does_not_exist",
            )
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Test 6 — IDOR guard
# ---------------------------------------------------------------------------


class TestIDORGuard:
    def _call_export_as_user(self, artifact_doc, requesting_user_id):
        from routes.vega_v3_exports import export_v3_artifact
        from routes import vega_v3_exports as exports_mod
        db = _mock_db_for_export(artifact_doc)
        with patch.object(exports_mod, "get_db", return_value=db):
            return asyncio.run(export_v3_artifact(
                artifact_id=artifact_doc["artifact_id"],
                target="analysis",
                entity_id=None,
                format="markdown",
                current_user=_make_user_with_perm(requesting_user_id),
            ))

    def test_owner_can_access_artifact(self):
        art = _make_v3_artifact(artifact_id="art_idor_test", user_id="user_owner")
        # Should not raise
        resp = self._call_export_as_user(art, "user_owner")
        assert hasattr(resp, "body")

    def test_different_user_gets_403(self):
        from fastapi import HTTPException
        art = _make_v3_artifact(artifact_id="art_idor_test", user_id="user_owner")
        with pytest.raises(HTTPException) as exc_info:
            self._call_export_as_user(art, "user_intruder")
        assert exc_info.value.status_code == 403
        assert "do not own" in exc_info.value.detail.lower()

    def test_403_detail_is_specific(self):
        from fastapi import HTTPException
        art = _make_v3_artifact(artifact_id="art_idor_test2", user_id="user_alice")
        with pytest.raises(HTTPException) as exc_info:
            self._call_export_as_user(art, "user_bob")
        # Verify the detail string matches the source code
        assert exc_info.value.detail == "You do not own this artifact"


# ---------------------------------------------------------------------------
# Test 7 — MMI confidence enrichment
# ---------------------------------------------------------------------------


class TestMMIConfidenceEnrichment:
    def test_workflow_detail_with_high_confidence_mmi_validates(self):
        """WorkflowDetail.mmi_hypothesis with value=4, confidence='high' passes Pydantic."""
        high_dim = MMIDimension(
            value=4,
            confidence="high",
            reasoning="Customer mentioned 4-6 hours/week",
        )
        mmi = MMIHypothesis(
            tss=high_dim,
            ie=high_dim,
            time=high_dim,
            cost=high_dim,
            quality=high_dim,
            satisfaction=high_dim,
        )
        detail = WorkflowDetail(mmi_hypothesis=mmi)
        assert detail.mmi_hypothesis is not None
        assert detail.mmi_hypothesis.time.value == 4
        assert detail.mmi_hypothesis.time.confidence == "high"
        assert "4-6 hours/week" in detail.mmi_hypothesis.time.reasoning

    def test_merge_workflow_prefers_detail_mmi_over_skeleton_seed(self):
        """_merge_workflow must use WorkflowDetail.mmi_hypothesis when present."""
        sk = WorkflowSkeleton(
            workflow_id="wf_mmi",
            workflow_name="MMI Test Workflow",
            source=SourceProvenance(),
            mmi_dims={"tss": 2, "ie": 2, "time": 2, "cost": 2, "quality": 2, "satisfaction": 2},
        )

        high_dim = MMIDimension(
            value=4,
            confidence="high",
            reasoning="Customer mentioned 4-6 hours/week",
        )
        detail_mmi = MMIHypothesis(
            tss=high_dim,
            ie=high_dim,
            time=high_dim,
            cost=high_dim,
            quality=high_dim,
            satisfaction=high_dim,
        )
        detail = WorkflowDetail(mmi_hypothesis=detail_mmi)

        merged = _merge_workflow(sk, detail, log_ctx="[test]")

        # Detail's MMI (value=4, high confidence) should win over skeleton seed (value=2, low)
        assert merged.mmi_hypothesis.time.value == 4
        assert merged.mmi_hypothesis.time.confidence == "high"
        assert "4-6 hours/week" in merged.mmi_hypothesis.time.reasoning

    def test_merge_workflow_falls_back_to_skeleton_mmi_when_detail_has_none(self):
        """When WorkflowDetail.mmi_hypothesis is None, skeleton seed is kept."""
        sk = WorkflowSkeleton(
            workflow_id="wf_seed",
            workflow_name="Seed Test",
            source=SourceProvenance(),
            mmi_dims={"tss": 3, "ie": 3, "time": 3, "cost": 3, "quality": 3, "satisfaction": 3},
        )
        detail = WorkflowDetail(mmi_hypothesis=None)

        merged = _merge_workflow(sk, detail, log_ctx="[test]")

        # Skeleton seed uses mmi_dims values
        assert merged.mmi_hypothesis.time.value == 3
        assert merged.mmi_hypothesis.time.confidence == "low"

    def test_merge_workflow_partial_failure_uses_skeleton_seed(self):
        """When detail signals failure, skeleton seed MMI must be present."""
        sk = WorkflowSkeleton(
            workflow_id="wf_fail",
            workflow_name="Fail Workflow",
            source=SourceProvenance(),
            mmi_dims={"tss": 1, "ie": 1, "time": 1, "cost": 1, "quality": 1, "satisfaction": 1},
        )
        failure_dict = {"partial_extraction_failed": True, "reason": "timeout"}

        merged = _merge_workflow(sk, failure_dict, log_ctx="[test]")

        assert merged.partial_extraction_failed is True
        assert merged.mmi_hypothesis is not None  # Seed was applied
        assert merged.mmi_hypothesis.tss.value == 1

    def test_mmi_dimension_value_is_preserved_in_model_dump(self):
        """Pydantic model_dump(mode='json') correctly serialises MMIDimension.value as int."""
        dim = MMIDimension(value=4, confidence="high", reasoning="Customer mentioned 4-6 hours/week")
        dumped = dim.model_dump(mode="json")
        assert dumped["value"] == 4
        assert dumped["confidence"] == "high"
        assert dumped["reasoning"] == "Customer mentioned 4-6 hours/week"
