"""Unit tests for the Stage 2 per-workflow and per-app detail extractors.

These tests mock ``call_llm`` and ``_get_model`` so no live LLM connection
is needed.

Run with:
    docker compose exec backend pytest tests/test_v3_detail_extractors.py -q
"""

import asyncio
import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.vega.flags import VegaFlag
from services.vega.models_v3 import (
    AcceptanceCriterion,
    AppDetail,
    AppSkeleton,
    PersonaRow,
    SourceProvenance,
    WorkflowDetail,
    WorkflowSkeleton,
)
from services.vega.detail_schemas import (
    APP_DETAIL_SCHEMA,
    WORKFLOW_DETAIL_SCHEMA,
)
from services.vega.v3_pipeline import (
    _extract_app_detail,
    _extract_workflow_detail,
)


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


def _make_user(user_id: str = "u_test") -> MagicMock:
    user = MagicMock()
    user.user_id = user_id
    return user


def _make_mock_db() -> MagicMock:
    return MagicMock()


def _make_wf_skeleton(
    workflow_id: str = "wf_invoice_intake",
    workflow_name: str = "Invoice Intake",
) -> WorkflowSkeleton:
    return WorkflowSkeleton(
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        complexity="M",
        mmi_dims={"tss": 3, "ie": 2, "time": 4, "cost": 3, "quality": 4, "satisfaction": 3},
        source=SourceProvenance(quote="We process about 200 invoices a week manually."),
        linked_app_ids=["app_invoices"],
    )


def _make_app_skeleton(
    app_id: str = "app_invoices",
    app_name: str = "Invoice Manager",
) -> AppSkeleton:
    return AppSkeleton(
        app_id=app_id,
        app_name=app_name,
        complexity="M",
        source=SourceProvenance(quote="We need an app to manage our invoice workflow."),
        linked_workflow_ids=["wf_invoice_intake"],
    )


def _valid_workflow_detail_json() -> str:
    return json.dumps({
        "acceptance_criteria": [
            {
                "flow_name": "Invoice intake",
                "success_when": "All invoices are captured without manual re-entry.",
                "flag": None,
            }
        ],
        "decision_logic": [
            {
                "id": "d1",
                "name": "Amount threshold check",
                "type": "exclusive",
                "branches": [
                    {"condition": "amount > $10k", "leads_to_step_id": "s3"},
                    {"condition": "amount <= $10k", "leads_to_step_id": "s2"},
                ],
                "inferred": False,
            }
        ],
        "process_steps": [
            {"id": "s1", "name": "Receive invoice", "task_type": "user", "inferred": False},
            {"id": "s2", "name": "Auto-approve", "task_type": "service", "inferred": False},
            {"id": "s3", "name": "Manager review", "task_type": "user", "inferred": False},
        ],
        "persona_depth_app_id": "app_invoices",
        "persona_overrides": [],
    })


def _valid_app_detail_json() -> str:
    return json.dumps({
        "personas": [
            {
                "role": "accounts payable clerk",
                "count": 3,
                "device": "laptop",
                "technical_literacy": "moderate",
                "flags": ["INFERRED_CONFIRM"],
            }
        ],
        "integrations": [
            {"system_name": "QuickBooks Online", "bucket": 2, "notes": "Exports invoices to QBO", "flags": []},
            {"system_name": "Email", "bucket": 1, "notes": "Inbound invoices via email", "flags": ["INFERRED_CONFIRM"]},
            {"system_name": "Bank API", "bucket": 3, "notes": "Direct payment integration", "flags": ["NEEDS_TECHNICAL_FOLLOWUP"]},
        ],
        "scale_profile": {
            "transaction_volume": "~200 invoices per week",
            "inference": "normal app database is sufficient",
            "flag": "INFERRED_CONFIRM",
        },
        "nfrs": {
            "device_support": "desktop only",
            "sla": None,
            "compliance": None,
            "flags": ["NEEDS_TECHNICAL_FOLLOWUP", "NEEDS_TECHNICAL_FOLLOWUP"],
        },
        "additional_source_quotes": [
            {
                "quote": "We do about 200 invoices a week, sometimes more at month end.",
                "speaker": "CFO",
            }
        ],
    })


def _llm_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


# ──────────────────────────────────────────────────────────────────────
# Workflow detail — happy path
# ──────────────────────────────────────────────────────────────────────


class TestWorkflowDetailHappyPath:
    def test_returns_workflow_detail_model(self):
        skeleton = _make_wf_skeleton()
        mock_resp = _llm_response(_valid_workflow_detail_json())

        with patch("services.vega.v3_pipeline.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_workflow_detail(
                        skeleton,
                        transcript_text="We process about 200 invoices a week manually.",
                        db=_make_mock_db(),
                        user=_make_user(),
                        session_id="ses_001",
                    )
                )

        assert isinstance(result, WorkflowDetail), f"Expected WorkflowDetail, got {type(result)}"

    def test_acceptance_criteria_populated(self):
        skeleton = _make_wf_skeleton()
        mock_resp = _llm_response(_valid_workflow_detail_json())

        with patch("services.vega.v3_pipeline.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_workflow_detail(
                        skeleton, "transcript", _make_mock_db(), _make_user(), "ses_001"
                    )
                )

        assert len(result.acceptance_criteria) == 1
        assert result.acceptance_criteria[0].flow_name == "Invoice intake"

    def test_process_steps_populated(self):
        skeleton = _make_wf_skeleton()
        mock_resp = _llm_response(_valid_workflow_detail_json())

        with patch("services.vega.v3_pipeline.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_workflow_detail(
                        skeleton, "transcript", _make_mock_db(), _make_user(), "ses_001"
                    )
                )

        assert len(result.process_steps) == 3
        assert result.process_steps[0].id == "s1"

    def test_persona_depth_app_id_set(self):
        skeleton = _make_wf_skeleton()
        mock_resp = _llm_response(_valid_workflow_detail_json())

        with patch("services.vega.v3_pipeline.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_workflow_detail(
                        skeleton, "transcript", _make_mock_db(), _make_user(), "ses_001"
                    )
                )

        assert result.persona_depth_app_id == "app_invoices"
        assert result.persona_overrides == []


# ──────────────────────────────────────────────────────────────────────
# App detail — happy path
# ──────────────────────────────────────────────────────────────────────


class TestAppDetailHappyPath:
    def test_returns_app_detail_model(self):
        skeleton = _make_app_skeleton()
        mock_resp = _llm_response(_valid_app_detail_json())

        with patch("services.vega.v3_pipeline.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_app_detail(
                        skeleton,
                        transcript_text="We need an app to manage our invoice workflow.",
                        db=_make_mock_db(),
                        user=_make_user(),
                        session_id="ses_001",
                    )
                )

        assert isinstance(result, AppDetail), f"Expected AppDetail, got {type(result)}"

    def test_personas_populated(self):
        skeleton = _make_app_skeleton()
        mock_resp = _llm_response(_valid_app_detail_json())

        with patch("services.vega.v3_pipeline.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_app_detail(skeleton, "transcript", _make_mock_db(), _make_user(), "ses_001")
                )

        assert len(result.personas) == 1
        assert result.personas[0].role == "accounts payable clerk"
        assert result.personas[0].count == 3

    def test_integrations_populated(self):
        skeleton = _make_app_skeleton()
        mock_resp = _llm_response(_valid_app_detail_json())

        with patch("services.vega.v3_pipeline.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_app_detail(skeleton, "transcript", _make_mock_db(), _make_user(), "ses_001")
                )

        assert len(result.integrations) == 3
        names = [i.system_name for i in result.integrations]
        assert "QuickBooks Online" in names

    def test_scale_profile_populated(self):
        skeleton = _make_app_skeleton()
        mock_resp = _llm_response(_valid_app_detail_json())

        with patch("services.vega.v3_pipeline.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_app_detail(skeleton, "transcript", _make_mock_db(), _make_user(), "ses_001")
                )

        assert result.scale_profile.transaction_volume == "~200 invoices per week"
        assert result.scale_profile.flag == VegaFlag.INFERRED_CONFIRM


# ──────────────────────────────────────────────────────────────────────
# Validation failure — both extractor types
# ──────────────────────────────────────────────────────────────────────


class TestValidationFailure:
    def test_workflow_detail_malformed_json_returns_partial(self):
        """LLM returns unparseable JSON — extractor must return partial-failed dict."""
        skeleton = _make_wf_skeleton()
        mock_resp = _llm_response("not valid json at all {{{")

        with patch("services.vega.v3_pipeline.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_workflow_detail(
                        skeleton, "transcript", _make_mock_db(), _make_user(), "ses_001"
                    )
                )

        assert isinstance(result, dict)
        assert result["partial_extraction_failed"] is True
        assert "reason" in result

    def test_app_detail_malformed_json_returns_partial(self):
        skeleton = _make_app_skeleton()
        mock_resp = _llm_response("not valid json {{")

        with patch("services.vega.v3_pipeline.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_app_detail(
                        skeleton, "transcript", _make_mock_db(), _make_user(), "ses_001"
                    )
                )

        assert isinstance(result, dict)
        assert result["partial_extraction_failed"] is True

    def test_workflow_pydantic_validation_failure_returns_partial(self):
        """LLM returns valid JSON that fails Pydantic validation — partial-failed dict."""
        skeleton = _make_wf_skeleton()
        # process_steps items require 'id' and 'name'; omitting 'id' trips Pydantic.
        bad_json = json.dumps({
            "acceptance_criteria": [],
            "process_steps": [{"name": "Missing id field"}],  # id is required
        })
        mock_resp = _llm_response(bad_json)

        with patch("services.vega.v3_pipeline.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_workflow_detail(
                        skeleton, "transcript", _make_mock_db(), _make_user(), "ses_001"
                    )
                )

        assert isinstance(result, dict)
        assert result["partial_extraction_failed"] is True
        assert len(result["reason"]) <= 200

    def test_app_pydantic_validation_failure_returns_partial(self):
        """LLM returns valid JSON that fails Pydantic validation."""
        skeleton = _make_app_skeleton()
        # IntegrationEntry.bucket is required — passing it as a string trips validation.
        bad_json = json.dumps({
            "personas": [],
            "integrations": [{"system_name": "QBO", "bucket": "two"}],  # bucket must be int
        })
        mock_resp = _llm_response(bad_json)

        with patch("services.vega.v3_pipeline.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_app_detail(
                        skeleton, "transcript", _make_mock_db(), _make_user(), "ses_001"
                    )
                )

        assert isinstance(result, dict)
        assert result["partial_extraction_failed"] is True


# ──────────────────────────────────────────────────────────────────────
# Timeout / exception — retry contract
# ──────────────────────────────────────────────────────────────────────


class TestTimeoutAndException:
    def test_workflow_call_llm_raises_retries_then_partial(self):
        """call_llm raises on every attempt — run_with_retry retries once, then
        the extractor must return partial-failed dict (never raise)."""
        skeleton = _make_wf_skeleton()

        call_count = 0

        async def _raising_llm(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise TimeoutError("LLM timed out")

        with patch("services.vega.v3_pipeline.call_llm", side_effect=_raising_llm):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_workflow_detail(
                        skeleton, "transcript", _make_mock_db(), _make_user(), "ses_001"
                    )
                )

        # run_with_retry calls twice (attempt 1 + attempt 2), then re-raises;
        # our wrapper catches that and returns partial-failed.
        assert call_count == 2, f"Expected 2 attempts, got {call_count}"
        assert isinstance(result, dict)
        assert result["partial_extraction_failed"] is True
        assert "reason" in result

    def test_app_call_llm_raises_retries_then_partial(self):
        skeleton = _make_app_skeleton()

        call_count = 0

        async def _raising_llm(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("connection error")

        with patch("services.vega.v3_pipeline.call_llm", side_effect=_raising_llm):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_app_detail(
                        skeleton, "transcript", _make_mock_db(), _make_user(), "ses_001"
                    )
                )

        assert call_count == 2
        assert isinstance(result, dict)
        assert result["partial_extraction_failed"] is True

    def test_extractor_never_raises(self):
        """The extractor contract: no exception escapes, only partial-failed dict."""
        skeleton = _make_wf_skeleton()

        async def _exploding_llm(*args, **kwargs):
            raise Exception("catastrophic failure")

        with patch("services.vega.v3_pipeline.call_llm", side_effect=_exploding_llm):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                # Should not raise
                result = asyncio.run(
                    _extract_workflow_detail(
                        skeleton, "transcript", _make_mock_db(), _make_user(), "ses_001"
                    )
                )

        assert isinstance(result, dict)
        assert result.get("partial_extraction_failed") is True


# ──────────────────────────────────────────────────────────────────────
# Flag round-trip — [MISSING] on acceptance criterion
# ──────────────────────────────────────────────────────────────────────


class TestFlagRoundTrip:
    def test_acceptance_criterion_missing_flag_parses(self):
        """When the LLM emits flag='MISSING' on an acceptance criterion, the
        parsed WorkflowDetail carries AcceptanceCriterion.flag == VegaFlag.MISSING."""
        skeleton = _make_wf_skeleton()

        detail_json = json.dumps({
            "acceptance_criteria": [
                {
                    "flow_name": "Exception handling",
                    "success_when": None,
                    "flag": "MISSING",
                }
            ],
            "process_steps": [
                {"id": "s1", "name": "Receive invoice"},
            ],
        })
        mock_resp = _llm_response(detail_json)

        with patch("services.vega.v3_pipeline.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_workflow_detail(
                        skeleton, "transcript", _make_mock_db(), _make_user(), "ses_001"
                    )
                )

        assert isinstance(result, WorkflowDetail)
        crit = result.acceptance_criteria[0]
        assert crit.flag == VegaFlag.MISSING, f"Expected VegaFlag.MISSING, got {crit.flag!r}"

    def test_inferred_confirm_flag_parses(self):
        """INFERRED_CONFIRM on an acceptance criterion parses correctly."""
        skeleton = _make_wf_skeleton()

        detail_json = json.dumps({
            "acceptance_criteria": [
                {
                    "flow_name": "Approval routing",
                    "success_when": "Invoice routes to manager within 1 business day.",
                    "flag": "INFERRED_CONFIRM",
                }
            ],
            "process_steps": [{"id": "s1", "name": "Route invoice"}],
        })
        mock_resp = _llm_response(detail_json)

        with patch("services.vega.v3_pipeline.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_workflow_detail(
                        skeleton, "transcript", _make_mock_db(), _make_user(), "ses_001"
                    )
                )

        assert result.acceptance_criteria[0].flag == VegaFlag.INFERRED_CONFIRM

    def test_integration_bucket3_flag_parses(self):
        """NEEDS_TECHNICAL_FOLLOWUP in integration flags parses to correct VegaFlag."""
        skeleton = _make_app_skeleton()

        app_json = json.dumps({
            "personas": [{"role": "clerk", "flags": []}],
            "integrations": [
                {
                    "system_name": "Bank API",
                    "bucket": 3,
                    "flags": ["NEEDS_TECHNICAL_FOLLOWUP"],
                }
            ],
        })
        mock_resp = _llm_response(app_json)

        with patch("services.vega.v3_pipeline.call_llm", new_callable=AsyncMock, return_value=mock_resp):
            with patch("services.ai.conductor._get_model", new_callable=AsyncMock, return_value=("google", "gemini-flash")):
                result = asyncio.run(
                    _extract_app_detail(
                        skeleton, "transcript", _make_mock_db(), _make_user(), "ses_001"
                    )
                )

        assert isinstance(result, AppDetail)
        bank = result.integrations[0]
        assert VegaFlag.NEEDS_TECHNICAL_FOLLOWUP in bank.flags


# ──────────────────────────────────────────────────────────────────────
# Schema validity — sanity check JSON serialisability
# ──────────────────────────────────────────────────────────────────────


class TestSchemaValidity:
    def test_workflow_detail_schema_is_json_serialisable(self):
        """WORKFLOW_DETAIL_SCHEMA must round-trip through json.dumps without error."""
        dumped = json.dumps(WORKFLOW_DETAIL_SCHEMA)
        assert isinstance(dumped, str)
        assert len(dumped) > 0

    def test_app_detail_schema_is_json_serialisable(self):
        dumped = json.dumps(APP_DETAIL_SCHEMA)
        assert isinstance(dumped, str)
        assert len(dumped) > 0

    def test_workflow_detail_schema_has_required_fields(self):
        props = WORKFLOW_DETAIL_SCHEMA.get("properties", {})
        assert "acceptance_criteria" in props
        assert "decision_logic" in props
        assert "process_steps" in props
        assert "persona_depth_app_id" in props
        assert "persona_overrides" in props

    def test_app_detail_schema_has_required_fields(self):
        props = APP_DETAIL_SCHEMA.get("properties", {})
        assert "personas" in props
        assert "integrations" in props
        assert "scale_profile" in props
        assert "nfrs" in props
        assert "additional_source_quotes" in props

    def test_schemas_have_no_nested_enum_keys(self):
        """Confirm enum-stripping discipline: no 'enum' keys in either schema.

        The schemas deliberately omit ``enum`` constraints on flag and bucket
        fields to avoid Gemini's state-budget ceiling.  Flag values are
        described inline instead.
        """
        def _find_enum_keys(node, path=""):
            if isinstance(node, dict):
                found = []
                if "enum" in node:
                    found.append(path)
                for k, v in node.items():
                    found.extend(_find_enum_keys(v, path=f"{path}.{k}"))
                return found
            if isinstance(node, list):
                found = []
                for i, item in enumerate(node):
                    found.extend(_find_enum_keys(item, path=f"{path}[{i}]"))
                return found
            return []

        wf_enums = _find_enum_keys(WORKFLOW_DETAIL_SCHEMA)
        app_enums = _find_enum_keys(APP_DETAIL_SCHEMA)
        assert wf_enums == [], f"WORKFLOW_DETAIL_SCHEMA contains 'enum' keys at: {wf_enums}"
        assert app_enums == [], f"APP_DETAIL_SCHEMA contains 'enum' keys at: {app_enums}"
