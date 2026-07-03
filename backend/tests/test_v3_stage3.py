"""Unit tests for v3 Stage 3 — BPMN + design-doc + EADD hooks.

Thread 2d implementation tests.  All LLM calls and database writes are
mocked; no live Mongo or Gemini connection is required.

Run with:
    docker compose exec backend pytest tests/test_v3_stage3.py -q
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.vega.models_v3 import (
    App,
    MMIDimension,
    MMIHypothesis,
    SourceProvenance,
    Workflow,
)
from services.vega.v3_pipeline import (
    _generate_bpmn_for_workflow,
    _generate_design_doc_for_workflow,
    _generate_eadd_for_app,
    _workflow_to_v2_dict,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SOURCE = SourceProvenance(transcript_filename="test.txt")

_MMI = MMIHypothesis(
    tss=MMIDimension(value=3, confidence="medium", reasoning="ok"),
    ie=MMIDimension(value=3, confidence="medium", reasoning="ok"),
    time=MMIDimension(value=3, confidence="medium", reasoning="ok"),
    cost=MMIDimension(value=3, confidence="medium", reasoning="ok"),
    quality=MMIDimension(value=3, confidence="medium", reasoning="ok"),
    satisfaction=MMIDimension(value=3, confidence="medium", reasoning="ok"),
)


def _make_workflow(**overrides) -> Workflow:
    kwargs = dict(
        workflow_id="wf_test_001",
        workflow_name="Test Workflow",
        source=_SOURCE,
        mmi_hypothesis=_MMI,
    )
    kwargs.update(overrides)
    return Workflow(**kwargs)


def _make_app(**overrides) -> App:
    kwargs = dict(
        app_id="app_test_001",
        app_name="Test App",
        source=_SOURCE,
    )
    kwargs.update(overrides)
    return App(**kwargs)


def _make_user():
    u = MagicMock()
    u.user_id = "user_test"
    return u


def _make_db():
    db = MagicMock()
    db.bpmn_diagrams = MagicMock()
    db.bpmn_diagrams.insert_one = AsyncMock()
    return db


_SMALL_XML = "<definitions><!-- small bpmn --></definitions>"
# Exactly 101_000 bytes — over the 100KB cap.
_LARGE_XML = "<definitions>" + "x" * 101_000 + "</definitions>"

_BPMN_SUCCESS = {
    "success": True,
    "bpmn_id": "bpmn_abc123",
    "bpmn_xml": _SMALL_XML,
    "validation_warnings": [],
    "error": None,
}

_BPMN_LARGE = {
    "success": True,
    "bpmn_id": "bpmn_big999",
    "bpmn_xml": _LARGE_XML,
    "validation_warnings": [],
    "error": None,
}

_DESIGN_DOC_SUCCESS = {
    "workflow_id": "wf_test_001",
    "workflow_name": "Test Workflow",
    "status": "success",
    "generated_at": "2026-05-01T00:00:00+00:00",
    "executive_summary": "This workflow does things.",
}

_EADD_SUCCESS = {
    "app_id": "app_test_001",
    "app_name": "Test App",
    "status": "success",
    "generated_at": "2026-05-01T00:00:00+00:00",
    "starter_prompt": "Build me a CRUD app",
}


# ---------------------------------------------------------------------------
# Helper: _workflow_to_v2_dict
# ---------------------------------------------------------------------------


class TestWorkflowToV2Dict:
    def test_required_keys_present(self):
        wf = _make_workflow()
        result = _workflow_to_v2_dict(wf)
        assert "workflow_id" in result
        assert "workflow_name" in result
        assert "actors" in result
        assert "trigger" in result
        assert "steps" in result
        assert "decision_points" in result
        assert "exception_paths" in result

    def test_workflow_id_and_name_pass_through(self):
        wf = _make_workflow()
        result = _workflow_to_v2_dict(wf)
        assert result["workflow_id"] == "wf_test_001"
        assert result["workflow_name"] == "Test Workflow"

    def test_empty_process_steps_gives_empty_steps(self):
        wf = _make_workflow()
        result = _workflow_to_v2_dict(wf)
        assert result["steps"] == []
        assert result["actors"] == []

    def test_exception_paths_always_empty_list(self):
        wf = _make_workflow()
        result = _workflow_to_v2_dict(wf)
        assert result["exception_paths"] == []


# ---------------------------------------------------------------------------
# Hook 1: _generate_bpmn_for_workflow
# ---------------------------------------------------------------------------


class TestGenerateBpmnForWorkflow:
    def test_happy_path_sets_all_fields(self):
        wf = _make_workflow()
        db = _make_db()
        user = _make_user()

        with patch(
            "services.vega.v3_pipeline.generate_workflow_diagram",
            new_callable=AsyncMock,
            return_value=_BPMN_SUCCESS,
        ):
            result = asyncio.run(_generate_bpmn_for_workflow(wf, db, user, "sess_001"))

        assert result.bpmn_id == "bpmn_abc123"
        assert result.bpmn_xml == _SMALL_XML
        assert result.bpmn_status == "generated"
        assert result.bpmn_content_hash is not None
        # DB write happened
        db.bpmn_diagrams.insert_one.assert_awaited_once()

    def test_100kb_guardrail_drops_inline_xml(self, caplog):
        wf = _make_workflow()
        db = _make_db()
        user = _make_user()

        with patch(
            "services.vega.v3_pipeline.generate_workflow_diagram",
            new_callable=AsyncMock,
            return_value=_BPMN_LARGE,
        ), caplog.at_level(logging.WARNING, logger="services.vega.v3_pipeline"):
            result = asyncio.run(_generate_bpmn_for_workflow(wf, db, user, "sess_001"))

        # bpmn_id retained; inline XML dropped
        assert result.bpmn_id == "bpmn_big999"
        assert result.bpmn_xml is None
        assert result.bpmn_status == "generated"
        # Warning was logged
        assert any("100KB cap" in r.message for r in caplog.records)

    def test_failure_isolation_generator_raises(self):
        wf = _make_workflow()
        db = _make_db()
        user = _make_user()

        with patch(
            "services.vega.v3_pipeline.generate_workflow_diagram",
            new_callable=AsyncMock,
            side_effect=RuntimeError("renderer exploded"),
        ):
            result = asyncio.run(_generate_bpmn_for_workflow(wf, db, user, "sess_001"))

        # Must not propagate — function returns the workflow
        assert result.bpmn_status == "failed"
        assert result.bpmn_id is None
        assert result.bpmn_xml is None

    def test_failure_isolation_generator_returns_error(self):
        wf = _make_workflow()
        db = _make_db()
        user = _make_user()

        with patch(
            "services.vega.v3_pipeline.generate_workflow_diagram",
            new_callable=AsyncMock,
            return_value={
                "success": False,
                "error": "missing actors",
                "bpmn_id": None,
                "bpmn_xml": None,
            },
        ):
            result = asyncio.run(_generate_bpmn_for_workflow(wf, db, user, "sess_001"))

        assert result.bpmn_status == "failed"

    def test_content_hash_skip_on_matching_hash(self):
        """If the content hash already matches, regen is skipped."""
        from services.ai.vega_analysis import _bpmn_content_hash

        wf = _make_workflow()
        # Pre-compute what the hash would be
        precomputed_hash = _bpmn_content_hash(_workflow_to_v2_dict(wf))
        wf.bpmn_id = "bpmn_existing"
        wf.bpmn_content_hash = precomputed_hash

        db = _make_db()
        user = _make_user()

        with patch(
            "services.vega.v3_pipeline.generate_workflow_diagram",
            new_callable=AsyncMock,
        ) as mock_gen:
            result = asyncio.run(_generate_bpmn_for_workflow(wf, db, user, "sess_001"))

        # Generator should not have been called
        mock_gen.assert_not_awaited()
        assert result.bpmn_id == "bpmn_existing"


# ---------------------------------------------------------------------------
# Hook 2: _generate_design_doc_for_workflow
# ---------------------------------------------------------------------------


class TestGenerateDesignDocForWorkflow:
    def test_happy_path_sets_design_doc(self):
        wf = _make_workflow()
        db = _make_db()
        user = _make_user()

        with patch(
            "services.vega.v3_pipeline._generate_single_design_doc",
            new_callable=AsyncMock,
            return_value=_DESIGN_DOC_SUCCESS,
        ):
            result = asyncio.run(
                _generate_design_doc_for_workflow(wf, db, user, "sess_001")
            )

        assert result.design_doc is not None
        assert result.design_doc["status"] == "success"
        assert result.design_doc["workflow_id"] == "wf_test_001"

    def test_failure_isolation_generator_raises(self):
        wf = _make_workflow()
        db = _make_db()
        user = _make_user()

        with patch(
            "services.vega.v3_pipeline._generate_single_design_doc",
            new_callable=AsyncMock,
            side_effect=ValueError("LLM quota exceeded"),
        ):
            result = asyncio.run(
                _generate_design_doc_for_workflow(wf, db, user, "sess_001")
            )

        assert result.design_doc is None

    def test_failed_status_leaves_design_doc_none(self):
        wf = _make_workflow()
        db = _make_db()
        user = _make_user()

        with patch(
            "services.vega.v3_pipeline._generate_single_design_doc",
            new_callable=AsyncMock,
            return_value={
                "status": "failed",
                "failure_reason": "empty LLM response",
                "workflow_id": "wf_test_001",
                "workflow_name": "Test Workflow",
            },
        ):
            result = asyncio.run(
                _generate_design_doc_for_workflow(wf, db, user, "sess_001")
            )

        assert result.design_doc is None

    def test_client_industry_forwarded(self):
        wf = _make_workflow()
        db = _make_db()
        user = _make_user()

        with patch(
            "services.vega.v3_pipeline._generate_single_design_doc",
            new_callable=AsyncMock,
            return_value=_DESIGN_DOC_SUCCESS,
        ) as mock_gen:
            asyncio.run(
                _generate_design_doc_for_workflow(
                    wf, db, user, "sess_001",
                    client_industry="healthcare",
                    stakeholders_summary="Dr Smith, nurse lead",
                )
            )

        call_args = mock_gen.call_args
        assert call_args.args[1] == "healthcare"
        assert call_args.args[2] == "Dr Smith, nurse lead"


# ---------------------------------------------------------------------------
# Hook 3: _generate_eadd_for_app
# ---------------------------------------------------------------------------


class TestGenerateEaddForApp:
    def test_happy_path_sets_eadd_doc(self):
        app = _make_app()
        db = _make_db()
        user = _make_user()

        with patch(
            "services.vega.v3_pipeline._generate_single_eadd",
            new_callable=AsyncMock,
            return_value=_EADD_SUCCESS,
        ):
            result = asyncio.run(_generate_eadd_for_app(app, db, user, "sess_001"))

        assert result.eadd_doc is not None
        assert result.eadd_doc["status"] == "success"
        assert result.eadd_doc["app_id"] == "app_test_001"

    def test_failure_isolation_generator_raises(self):
        app = _make_app()
        db = _make_db()
        user = _make_user()

        with patch(
            "services.vega.v3_pipeline._generate_single_eadd",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network timeout"),
        ):
            result = asyncio.run(_generate_eadd_for_app(app, db, user, "sess_001"))

        assert result.eadd_doc is None

    def test_failed_status_leaves_eadd_doc_none(self):
        app = _make_app()
        db = _make_db()
        user = _make_user()

        with patch(
            "services.vega.v3_pipeline._generate_single_eadd",
            new_callable=AsyncMock,
            return_value={
                "status": "failed",
                "failure_reason": "invalid JSON",
                "app_id": "app_test_001",
                "app_name": "Test App",
            },
        ):
            result = asyncio.run(_generate_eadd_for_app(app, db, user, "sess_001"))

        assert result.eadd_doc is None

    def test_matrix_defaults_to_empty_list(self):
        app = _make_app()
        db = _make_db()
        user = _make_user()

        with patch(
            "services.vega.v3_pipeline._generate_single_eadd",
            new_callable=AsyncMock,
            return_value=_EADD_SUCCESS,
        ) as mock_gen:
            asyncio.run(_generate_eadd_for_app(app, db, user, "sess_001"))

        # matrix arg (positional index 1) should be []
        call_args = mock_gen.call_args
        assert call_args.args[1] == []

    def test_client_industry_forwarded(self):
        app = _make_app()
        db = _make_db()
        user = _make_user()

        with patch(
            "services.vega.v3_pipeline._generate_single_eadd",
            new_callable=AsyncMock,
            return_value=_EADD_SUCCESS,
        ) as mock_gen:
            asyncio.run(
                _generate_eadd_for_app(
                    app, db, user, "sess_001",
                    client_industry="fintech",
                    stakeholders_summary="CFO, controller",
                )
            )

        call_args = mock_gen.call_args
        assert call_args.args[2] == "fintech"
        assert call_args.args[3] == "CFO, controller"
