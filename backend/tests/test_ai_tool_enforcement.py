"""
AI Tool Dispatcher Permission Enforcement Tests

Verifies execute_tool gates calls against TOOL_REGISTRY before
dispatching. Targets the chokepoint at services/ai/agents/tools/
conversational_tools.py:execute_tool — every chat-AI tool call flows
through here.

Coverage:
- Tools mapped to the OPEN sentinel pass through unchanged
- Tools with a required permission deny callers who lack it (soft error,
  not exception)
- Denied calls write an authz_failed entry via log_security_event
  (which lands in the unified audit_logs collection with
  event_category="security")
- Tools with a required permission succeed for callers who have it
- Unknown tool names still return the existing "Unknown tool" error and
  do NOT trip the permission gate first
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services.ai.agents.tools.conversational_tools import (
    OPEN,
    TOOL_REGISTRY,
    execute_tool,
)


def _make_user(perms=None, role="user", user_id="u_test", email="t@example.com", name="Test User"):
    """Build a lightweight user stand-in compatible with has_perm()."""
    return SimpleNamespace(
        user_id=user_id,
        email=email,
        role=role,
        name=name,
        permissions=perms or [],
    )


@pytest.fixture
def fake_db():
    return AsyncMock()


# ---------------------------------------------------------------------------
# Mapping integrity
# ---------------------------------------------------------------------------

class TestToolRegistryMapping:
    def test_native_ai_agent_tools_require_their_use_perm(self):
        # Spot check: agent-specific tools must require the "*.use" perm.
        assert TOOL_REGISTRY["compute_mmi_score"]["perm"] == "vega.use"
        assert TOOL_REGISTRY["save_vega_session"]["perm"] == "vega.use"
        assert TOOL_REGISTRY["generate_bpmn_diagram"]["perm"] == "bpmn.use"
        assert TOOL_REGISTRY["generate_sow_section"]["perm"] == "orion.use"
        assert TOOL_REGISTRY["finalize_sow_to_google_docs"]["perm"] == "orion.use"

    def test_chat_plumbing_tools_are_open(self):
        # These must remain ungated — they're the bedrock of any chat turn.
        # OPEN is a sentinel object distinct from None so that "forgot to
        # register" never collapses into "intentionally open".
        assert TOOL_REGISTRY["render_artifact"]["perm"] is OPEN
        assert TOOL_REGISTRY["update_user_state"]["perm"] is OPEN

    def test_every_registered_tool_has_a_handler(self):
        # Sanity check that no entry was registered with a missing handler.
        for name, entry in TOOL_REGISTRY.items():
            assert callable(entry["handler"]), f"{name} has no callable handler"
            assert "perm" in entry, f"{name} missing 'perm' key"


# ---------------------------------------------------------------------------
# Permission gate behaviour
# ---------------------------------------------------------------------------

class TestExecuteToolPermissionGate:
    @pytest.mark.asyncio
    async def test_open_tool_dispatches_without_permission_check(self, fake_db):
        user = _make_user(perms=[])  # no perms at all
        # Patch the registry entry's handler directly so the dispatcher
        # actually exercises the new TOOL_REGISTRY lookup path.
        from services.ai.agents.tools import conversational_tools as ct

        new_handler = AsyncMock(return_value={"ok": True})
        original = ct.TOOL_REGISTRY["render_artifact"]["handler"]
        ct.TOOL_REGISTRY["render_artifact"]["handler"] = new_handler
        try:
            with patch(
                "services.security_logger.log_security_event",
                new=AsyncMock(),
            ) as mock_log:
                result = await execute_tool(
                    "render_artifact", {"some": "args"}, fake_db, user
                )
        finally:
            ct.TOOL_REGISTRY["render_artifact"]["handler"] = original

        assert result == {"ok": True}
        # OPEN tools must skip the audit gate entirely — no authz_failed
        # entries should be written for them.
        mock_log.assert_not_called()

    @pytest.mark.asyncio
    async def test_gated_tool_denies_user_without_perm(self, fake_db):
        user = _make_user(perms=[])  # lacks vega.use
        from services.ai.agents.tools import conversational_tools as ct

        mock_handler = AsyncMock(return_value={"should_not_run": True})
        original = ct.TOOL_REGISTRY["compute_mmi_score"]["handler"]
        ct.TOOL_REGISTRY["compute_mmi_score"]["handler"] = mock_handler
        try:
            with patch(
                "services.security_logger.log_security_event",
                new=AsyncMock(),
            ) as mock_log:
                result = await execute_tool(
                    "compute_mmi_score", {}, fake_db, user, session_id="s_1"
                )
        finally:
            ct.TOOL_REGISTRY["compute_mmi_score"]["handler"] = original

        assert result["error"] == "permission_denied"
        assert result["tool"] == "compute_mmi_score"
        assert result["required_permission"] == "vega.use"
        # Handler must not have been invoked.
        mock_handler.assert_not_called()
        # Authz failure must have been logged.
        mock_log.assert_awaited_once()
        kwargs = mock_log.call_args.kwargs
        assert kwargs["event_type"] == "authz_failed"
        assert kwargs["resource"] == "ai_tool"
        assert kwargs["resource_id"] == "compute_mmi_score"
        assert kwargs["outcome"] == "denied"
        assert kwargs["details"]["required_permission"] == "vega.use"
        assert kwargs["details"]["session_id"] == "s_1"

    @pytest.mark.asyncio
    async def test_gated_tool_allows_user_with_perm(self, fake_db):
        user = _make_user(perms=["vega.use"])
        from services.ai.agents.tools import conversational_tools as ct

        mock_handler = AsyncMock(return_value={"score": 7})
        original = ct.TOOL_REGISTRY["compute_mmi_score"]["handler"]
        ct.TOOL_REGISTRY["compute_mmi_score"]["handler"] = mock_handler
        try:
            with patch(
                "services.security_logger.log_security_event",
                new=AsyncMock(),
            ) as mock_log:
                result = await execute_tool(
                    "compute_mmi_score", {"transcript_id": "t1"}, fake_db, user
                )
        finally:
            ct.TOOL_REGISTRY["compute_mmi_score"]["handler"] = original

        assert result == {"score": 7}
        mock_handler.assert_awaited_once()
        # No authz failure when permission is present.
        mock_log.assert_not_called()

    @pytest.mark.asyncio
    async def test_admin_wildcard_passes_gated_tool(self, fake_db):
        # Admins carry the "*" wildcard via has_perm; the gate must respect it.
        user = _make_user(perms=["*"], role="admin")
        from services.ai.agents.tools import conversational_tools as ct

        mock_handler = AsyncMock(return_value={"section": "intro"})
        original = ct.TOOL_REGISTRY["generate_sow_section"]["handler"]
        ct.TOOL_REGISTRY["generate_sow_section"]["handler"] = mock_handler
        try:
            result = await execute_tool(
                "generate_sow_section", {"section": "intro"}, fake_db, user
            )
        finally:
            ct.TOOL_REGISTRY["generate_sow_section"]["handler"] = original

        assert result == {"section": "intro"}
        mock_handler.assert_awaited_once()


# ---------------------------------------------------------------------------
# Unknown tool short-circuit
# ---------------------------------------------------------------------------

class TestUnknownToolShortCircuit:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_unknown_error_without_perm_check(self, fake_db):
        user = _make_user(perms=[])
        with patch(
            "services.security_logger.log_security_event",
            new=AsyncMock(),
        ) as mock_log:
            result = await execute_tool("nonexistent_tool", {}, fake_db, user)

        assert "Unknown tool" in result["error"]
        # Permission gate should not fire for unknown tools — the error
        # is structural, not authz-related, so we don't pollute the audit
        # log with unknown-tool noise.
        mock_log.assert_not_called()


# ---------------------------------------------------------------------------
# Handler-level guards (defense in depth, separate from the dispatch gate)
# ---------------------------------------------------------------------------

class TestGradeQuizOwnershipScope:
    """Regression: grade_quiz must scope artifact lookup by user_id so chat
    user A cannot grade chat user B's quiz by guessing the artifact_id."""

    @pytest.mark.asyncio
    async def test_grade_quiz_filters_artifact_by_user_id(self):
        from services.ai.agents.tools.conversational_tools import _handle_grade_quiz

        user = _make_user(user_id="u_owner")
        captured = {}

        async def find_one(query, projection=None):
            captured["query"] = query
            return None

        fake_db = SimpleNamespace(ai_chat_artifacts=SimpleNamespace(find_one=find_one))
        result = await _handle_grade_quiz({"artifact_id": "art_123"}, fake_db, user)

        assert captured["query"] == {"artifact_id": "art_123", "user_id": "u_owner"}
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_grade_quiz_rejects_user_without_user_id(self):
        from services.ai.agents.tools.conversational_tools import _handle_grade_quiz

        user = SimpleNamespace(name="Anon", role="user")  # no user_id attr
        fake_db = SimpleNamespace()
        result = await _handle_grade_quiz({"artifact_id": "art_x"}, fake_db, user)

        assert "user_id" in result["error"].lower()


class TestAwardCertificationPrerequisite:
    """Defense against prompt-injection: award_certification is OPEN, so the
    handler itself must verify a passing graded artifact exists before
    writing the certification document."""

    @pytest.mark.asyncio
    async def test_refuses_when_no_passing_artifact(self):
        from services.ai.agents.tools.conversational_tools import _handle_award_certification

        user = _make_user(user_id="u_cheat")

        async def find_one(query, projection=None):
            return None  # no passing artifact on file

        async def insert_one(doc):
            raise AssertionError("certification must not be inserted without a passing artifact")

        async def update_one(*args, **kwargs):
            raise AssertionError("user state must not be updated without a passing artifact")

        fake_db = SimpleNamespace(
            ai_chat_artifacts=SimpleNamespace(find_one=find_one),
            certifications=SimpleNamespace(insert_one=insert_one),
            ai_employee_user_state=SimpleNamespace(update_one=update_one),
        )
        result = await _handle_award_certification({}, fake_db, user, employee_id="emp_arc")
        assert result["error"] == "no_passing_artifact"

    @pytest.mark.asyncio
    async def test_awards_when_passing_artifact_exists(self):
        from services.ai.agents.tools.conversational_tools import _handle_award_certification

        user = _make_user(user_id="u_legit")
        captured = {"insert": None, "update": None}

        async def find_one(query, projection=None):
            assert query["user_id"] == "u_legit"
            assert query["status"] == "graded"
            assert query["ai_feedback.passed"] is True
            # Cross-cert scope: the lookup must require both the
            # final-test marker and the requesting employee_id, so a
            # pass on employee A cannot justify a cert from employee B.
            assert query["is_final_test"] is True
            assert query["employee_id"] == "emp_arc"
            return {"artifact_id": "art_pass", "ai_feedback": {"passed": True, "score": 92}}

        async def insert_one(doc):
            captured["insert"] = doc

        async def update_one(query, update):
            captured["update"] = (query, update)

        fake_db = SimpleNamespace(
            ai_chat_artifacts=SimpleNamespace(find_one=find_one),
            certifications=SimpleNamespace(insert_one=insert_one),
            ai_employee_user_state=SimpleNamespace(update_one=update_one),
        )
        result = await _handle_award_certification(
            {"certification_type": "ai_recruiter", "final_test_score": 92},
            fake_db,
            user,
            employee_id="emp_arc",
        )
        assert "certification_id" in result
        assert captured["insert"]["user_id"] == "u_legit"
        assert captured["insert"]["source_artifact_id"] == "art_pass"
        assert captured["update"][0] == {"user_id": "u_legit", "employee_id": "emp_arc"}

    @pytest.mark.asyncio
    async def test_refuses_when_only_non_final_quiz_passed(self):
        # A user who passed a mid-module quiz (is_final_test=False) but
        # never the final test must not be awarded the cert.
        from services.ai.agents.tools.conversational_tools import _handle_award_certification

        user = _make_user(user_id="u_partial")

        async def find_one(query, projection=None):
            # Only "passing" doc on file is a mid-module quiz; the query
            # requires is_final_test=True so this returns None.
            assert query.get("is_final_test") is True
            return None

        async def insert_one(doc):
            raise AssertionError("cert must not be issued without passing the final test")

        async def update_one(*args, **kwargs):
            raise AssertionError("state must not flip to certified without final-test pass")

        fake_db = SimpleNamespace(
            ai_chat_artifacts=SimpleNamespace(find_one=find_one),
            certifications=SimpleNamespace(insert_one=insert_one),
            ai_employee_user_state=SimpleNamespace(update_one=update_one),
        )
        result = await _handle_award_certification({}, fake_db, user, employee_id="emp_arc")
        assert result["error"] == "no_passing_artifact"
