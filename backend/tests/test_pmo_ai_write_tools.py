"""PMO AI Write Tools — unit tests for Wave 1 / Thread 1A.

Covers:
- TOOL_REGISTRY mapping: every PMO write tool has the correct permission key
- TOOL_SCHEMAS: every registered tool has a matching schema with required keys
- Permission denial: execute_tool routes denials through the gate (relies on
  the existing dispatcher; we only spot-check that the new perms are wired in)
- Tool-level validation errors (missing task_id, bad field name, etc.)
- Name resolution: _resolve_user_by_name returns single / ambiguous / not_found
- Audit logging: each tool writes a pmo_ai audit_logs row on success and error
- Tier semantics: Tier 1 returns tier=1, Tier 2 returns tier=2 + _warning

These are pure unit tests — no Mongo, no HTTP. Route handlers are patched out
so the focus is the tool surface itself.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services.ai.agents.tools.conversational_tools import (
    OPEN,
    TOOL_REGISTRY,
    TOOL_SCHEMAS,
    _is_uuid_like,
    _resolve_user_by_name,
    _handle_update_task_status,
    _handle_update_task_field,
    _handle_assign_task,
    _handle_add_task_comment,
    _handle_create_project,
    _handle_create_task,
    _handle_bulk_update_task_status,
    _handle_delete_task,
    _handle_summarize_recent_changes,
    execute_tool,
)


PMO_WRITE_TOOL_NAMES = [
    "update_task_status",
    "update_task_field",
    "assign_task",
    "add_task_comment",
    "create_project",
    "create_project_from_template",
    "create_task",
    "bulk_update_task_status",
    "bulk_assign_tasks",
    "delete_task",
    "summarize_recent_changes",
]


def _make_user(perms=None, role="project_manager", user_id="u_pm", email="pm@simplimeta.io", name="Test PM"):
    return SimpleNamespace(
        user_id=user_id, email=email, role=role, name=name,
        permissions=perms or [],
    )


class _CollectionStub:
    """Mongo-like collection stub for unit tests. Supports the .find + .insert_one
    + .find_one shape we use in handlers. Tracks all writes so tests can assert."""

    def __init__(self, find_results=None, find_one_result=None):
        self._find_results = list(find_results or [])
        self._find_one_result = find_one_result
        self.inserted: list = []

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return SimpleNamespace(inserted_id="id_stub")

    async def find_one(self, query=None, projection=None, **kwargs):
        return self._find_one_result

    def find(self, query=None, projection=None, **kwargs):
        results = list(self._find_results)

        class _Cursor:
            def sort(self_inner, *a, **k):
                return self_inner

            def limit(self_inner, n):
                self_inner._cap = n
                return self_inner

            async def to_list(self_inner, n):
                cap = getattr(self_inner, "_cap", n) or n
                return results[:cap]

        return _Cursor()


class _DbStub:
    """Dict-style accessor mirroring how handlers use db["collection"]."""

    def __init__(self, mapping=None):
        self._mapping: dict = mapping or {}
        self.audit_logs = _CollectionStub()

    def __getitem__(self, key):
        if key not in self._mapping:
            self._mapping[key] = _CollectionStub()
        return self._mapping[key]

    def __getattr__(self, key):
        # Allow attribute access too — audit_logs already covered, others fall
        # through to the dict path.
        if key.startswith("_"):
            raise AttributeError(key)
        return self[key]


# ---------------------------------------------------------------------------
# Registry / schema integrity
# ---------------------------------------------------------------------------

class TestPmoToolRegistryMapping:
    def test_all_pmo_write_tools_registered(self):
        missing = [t for t in PMO_WRITE_TOOL_NAMES if t not in TOOL_REGISTRY]
        assert not missing, f"Missing registry entries: {missing}"

    def test_all_pmo_write_tools_have_schemas(self):
        missing = [t for t in PMO_WRITE_TOOL_NAMES if t not in TOOL_SCHEMAS]
        assert not missing, f"Missing tool schemas: {missing}"

    def test_pmo_tool_perms_match_codebase(self):
        # Spot check each tool maps to the correct permission key from
        # seeds/seed_projects_rbac.py. If any of these change, the seed
        # file and the tool registration must be updated together.
        assert TOOL_REGISTRY["update_task_status"]["perm"] == "projects.task.change_status"
        assert TOOL_REGISTRY["update_task_field"]["perm"] == "projects.task.edit"
        assert TOOL_REGISTRY["assign_task"]["perm"] == "projects.task.assign"
        assert TOOL_REGISTRY["add_task_comment"]["perm"] == "projects.task.comment.create"
        assert TOOL_REGISTRY["create_project"]["perm"] == "projects.project.create"
        assert TOOL_REGISTRY["create_project_from_template"]["perm"] == "projects.project.create"
        assert TOOL_REGISTRY["create_task"]["perm"] == "projects.task.create"
        assert TOOL_REGISTRY["bulk_update_task_status"]["perm"] == "projects.task.change_status"
        assert TOOL_REGISTRY["bulk_assign_tasks"]["perm"] == "projects.task.assign"
        assert TOOL_REGISTRY["delete_task"]["perm"] == "projects.task.delete"
        assert TOOL_REGISTRY["summarize_recent_changes"]["perm"] == "projects.task.view"

    def test_no_pmo_write_tool_is_open(self):
        # PMO writes MUST be gated — none of them should slip past the
        # permission check via the OPEN sentinel.
        for name in PMO_WRITE_TOOL_NAMES:
            assert TOOL_REGISTRY[name]["perm"] is not OPEN, (
                f"{name} must NOT be OPEN — every PMO write must be permission-gated"
            )

    def test_delete_task_marked_destructive(self):
        # Schema flag the LLM can use to prioritize confirmation.
        assert TOOL_SCHEMAS["delete_task"].get("_destructive") is True


# ---------------------------------------------------------------------------
# Permission gate (relies on the existing execute_tool dispatcher)
# ---------------------------------------------------------------------------

class TestPmoToolPermissionGate:
    @pytest.mark.asyncio
    async def test_update_task_status_denied_without_perm(self):
        user = _make_user(perms=[])  # no projects.task.change_status
        db = _DbStub()
        with patch("services.security_logger.log_security_event", new=AsyncMock()):
            result = await execute_tool(
                "update_task_status",
                {"task_id": "task_x", "status": "in_progress"},
                db, user,
            )
        assert result["error"] == "permission_denied"
        assert result["required_permission"] == "projects.task.change_status"

    @pytest.mark.asyncio
    async def test_create_project_denied_without_perm(self):
        user = _make_user(perms=["projects.task.view"])  # has view, lacks create
        db = _DbStub()
        with patch("services.security_logger.log_security_event", new=AsyncMock()):
            result = await execute_tool(
                "create_project",
                {"name": "Test", "methodology": "waterfall"},
                db, user,
            )
        assert result["error"] == "permission_denied"
        assert result["required_permission"] == "projects.project.create"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

class TestPmoToolValidation:
    @pytest.mark.asyncio
    async def test_update_task_status_requires_both_args(self):
        db = _DbStub()
        user = _make_user()
        result = await _handle_update_task_status({"task_id": "task_1"}, db, user)
        assert "required" in result["error"].lower()
        result = await _handle_update_task_status({"status": "in_progress"}, db, user)
        assert "required" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_update_task_field_rejects_non_whitelisted_field(self):
        db = _DbStub()
        user = _make_user()
        result = await _handle_update_task_field(
            {"task_id": "task_1", "field": "created_by", "value": "u_evil"},
            db, user,
        )
        assert result["error"] == "field_not_allowed"
        # Whitelist enumerated in the detail so the LLM can correct itself.
        assert "title" in result["detail"]

    @pytest.mark.asyncio
    async def test_create_project_rejects_invalid_methodology(self):
        db = _DbStub()
        user = _make_user()
        result = await _handle_create_project(
            {"name": "X", "methodology": "kanban"}, db, user,
        )
        assert "valid methodology" in result["error"]

    @pytest.mark.asyncio
    async def test_create_task_requires_project_and_title(self):
        db = _DbStub()
        user = _make_user()
        result = await _handle_create_task({"project_id": "proj_1"}, db, user)
        assert "required" in result["error"]

    @pytest.mark.asyncio
    async def test_delete_task_requires_task_id(self):
        db = _DbStub()
        user = _make_user()
        result = await _handle_delete_task({}, db, user)
        assert "task_id" in result["error"]


# ---------------------------------------------------------------------------
# Name resolution
# ---------------------------------------------------------------------------

class TestNameResolution:
    def test_is_uuid_like(self):
        assert _is_uuid_like("u_abc123def4567890")
        assert _is_uuid_like("abc123def4567890abcdef")
        # Display names should NOT match.
        assert not _is_uuid_like("Grace Holloway")
        assert not _is_uuid_like("")
        assert not _is_uuid_like(None)

    @pytest.mark.asyncio
    async def test_resolve_unique_match(self):
        users_coll = _CollectionStub(find_results=[
            {"user_id": "u_grace", "name": "Grace Holloway", "email": "grace@x.io"},
        ])
        db = _DbStub({"users": users_coll})
        result = await _resolve_user_by_name(db, "Grace")
        assert result == {"user_id": "u_grace"}

    @pytest.mark.asyncio
    async def test_resolve_ambiguous_returns_candidates(self):
        users_coll = _CollectionStub(find_results=[
            {"user_id": "u_g1", "name": "Grace Holloway", "email": "grace.h@x.io"},
            {"user_id": "u_g2", "name": "Grace Linden", "email": "grace.l@x.io"},
        ])
        db = _DbStub({"users": users_coll})
        result = await _resolve_user_by_name(db, "Grace")
        assert result["error"] == "ambiguous"
        assert len(result["candidates"]) == 2
        # Detail must hint at how many matched so the LLM can phrase the ask.
        assert "matched 2" in result["detail"]

    @pytest.mark.asyncio
    async def test_resolve_not_found(self):
        users_coll = _CollectionStub(find_results=[])
        db = _DbStub({"users": users_coll})
        result = await _resolve_user_by_name(db, "Nobody")
        assert result["error"] == "not_found"

    @pytest.mark.asyncio
    async def test_assign_task_returns_ambiguous_without_calling_route(self):
        """v1.3 Theme B: name resolution is now scoped to project members.
        The two ambiguous Graces must both be on the project for the
        ambiguous result to fire; otherwise scoping would narrow to one."""
        users_coll = _CollectionStub(find_results=[
            {"user_id": "u_g1", "name": "Grace H", "email": "g1@x.io"},
            {"user_id": "u_g2", "name": "Grace L", "email": "g2@x.io"},
        ])
        pm_tasks_coll = _CollectionStub(
            find_one_result={"task_id": "task_1", "project_id": "proj_1"},
        )
        projects_coll = _CollectionStub(
            find_one_result={
                "project_id": "proj_1",
                "pm_id": "u_pm",
                "assigned_user_ids": ["u_g1", "u_g2"],
            },
        )
        db = _DbStub({
            "users": users_coll,
            "pm_tasks": pm_tasks_coll,
            "projects": projects_coll,
        })
        user = _make_user()
        # Patch the route to detect it was NOT called — name resolution
        # must short-circuit on ambiguity.
        with patch("routes.projects_task.update_task", new=AsyncMock()) as mock_update:
            result = await _handle_assign_task(
                {"task_id": "task_1", "user_id_or_name": "Grace"},
                db, user,
            )
        assert result["error"] == "ambiguous"
        mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

class TestAuditLogging:
    @pytest.mark.asyncio
    async def test_successful_update_does_not_write_separate_audit_row(self):
        """v1.3 Theme B: single-row audit. On success, the activity row is
        written by crud_envelope (stamped with actor_type=pmo_ai via the
        ai_actor_ctx contextvar set in execute_tool). The handler itself
        no longer writes a separate `event_category="ai_action"` row.
        """
        db = _DbStub()
        user = _make_user(user_id="u_pm_grace")

        with patch("routes.projects_task.update_task", new=AsyncMock(return_value={"task_id": "task_x", "status": "in_progress"})):
            result = await _handle_update_task_status(
                {"task_id": "task_x", "status": "in_progress"},
                db, user, session_id="chat_42",
            )

        assert result["ok"] is True
        assert result["tier"] == 1
        # No separate row from the handler — the activity row from
        # crud_envelope (not exercised here because update_task is patched)
        # is the single source of truth.
        assert len(db.audit_logs.inserted) == 0

    @pytest.mark.asyncio
    async def test_failed_route_still_writes_error_audit_row(self):
        """Errors still write a standalone ai_action row so the failure
        trail is preserved — crud_envelope never ran because the route
        raised before reaching it.
        """
        from fastapi import HTTPException
        db = _DbStub()
        user = _make_user()

        with patch(
            "routes.projects_task.update_task",
            new=AsyncMock(side_effect=HTTPException(status_code=404, detail="Task not found")),
        ):
            result = await _handle_update_task_status(
                {"task_id": "task_missing", "status": "complete"},
                db, user,
            )

        assert result["error"] == "update_failed"
        assert len(db.audit_logs.inserted) == 1
        row = db.audit_logs.inserted[0]
        assert row["event_category"] == "ai_action"
        assert row["result_status"] == "error"
        assert "Task not found" in row["error_detail"]


# ---------------------------------------------------------------------------
# Tier semantics + warning surfaces
# ---------------------------------------------------------------------------

class TestTierSemantics:
    @pytest.mark.asyncio
    async def test_tier_1_status_update_returns_tier_1(self):
        db = _DbStub()
        with patch("routes.projects_task.update_task", new=AsyncMock(return_value={"task_id": "t"})):
            result = await _handle_update_task_status(
                {"task_id": "t", "status": "complete"}, db, _make_user(),
            )
        assert result["tier"] == 1
        assert "_warning" not in result  # Tier 1 is reversible — no warning needed

    @pytest.mark.asyncio
    async def test_tier_2_create_project_returns_warning(self):
        db = _DbStub()
        with patch(
            "routes.projects_project.create_project",
            new=AsyncMock(return_value={"project_id": "proj_new"}),
        ):
            result = await _handle_create_project(
                {"name": "Henderson Q3", "methodology": "waterfall"},
                db, _make_user(),
            )
        assert result["tier"] == 2
        assert "_warning" in result
        # The warning must be non-trivial — the LLM is supposed to relay it.
        assert len(result["_warning"]) > 10

    @pytest.mark.asyncio
    async def test_tier_2_delete_task_returns_destructive_warning(self):
        db = _DbStub()
        with patch(
            "routes.projects_task.delete_task",
            new=AsyncMock(return_value={"ok": True, "deleted_id": "task_x"}),
        ):
            result = await _handle_delete_task({"task_id": "task_x"}, db, _make_user())
        assert result["tier"] == 2
        assert "_warning" in result
        # The warning must mention irreversibility — that's the whole point.
        assert "cannot be undone" in result["_warning"].lower() or "permanent" in result["_warning"].lower()

    @pytest.mark.asyncio
    async def test_bulk_update_warning_includes_counts(self):
        db = _DbStub()
        with patch(
            "routes.projects_task.bulk_update_status",
            new=AsyncMock(return_value={"updated_count": 7, "failed": [{"task_id": "t9", "error": "x"}]}),
        ):
            result = await _handle_bulk_update_task_status(
                {"task_ids": ["t1", "t2"], "status": "in_progress"},
                db, _make_user(),
            )
        assert result["tier"] == 2
        # The warning must surface both updated + failed counts so the LLM
        # can relay them to the user.
        assert "7" in result["_warning"]
        assert "fail" in result["_warning"].lower()


# ---------------------------------------------------------------------------
# summarize_recent_changes — read-only
# Handler now delegates to routes.projects_recent_changes.fetch_recent_changes
# so we patch that function rather than the old collection stubs.
# ---------------------------------------------------------------------------

_FAKE_CHANGE_ROW = {
    "type": "change",
    "entity_id": "task_a",
    "entity_type": "task",
    "project_id": "proj_1",
    "actor_user_id": "u_pm",
    "actor_type": "user",
    "tool_name": None,
    "action": "status_changed",
    "field": "status",
    "old_value": "in_progress",
    "new_value": "complete",
    "at": None,
    "summary": "@Test PM changed status of task #task_a from 'in_progress' to 'complete' just now",
}


class TestSummarizeRecentChanges:
    @pytest.mark.asyncio
    async def test_returns_structured_records(self):
        db = _DbStub()
        with patch(
            "routes.projects_recent_changes.fetch_recent_changes",
            new=AsyncMock(return_value=[_FAKE_CHANGE_ROW]),
        ):
            result = await _handle_summarize_recent_changes(
                {"hours": 12}, db, _make_user(),
            )
        assert result["ok"] is True
        assert result["hours"] == 12
        assert len(result["changes"]) == 1
        assert "summary_hint" in result
        assert result["counts"]["total"] == 1

    @pytest.mark.asyncio
    async def test_clamps_hours_to_safe_range(self):
        db = _DbStub()
        with patch(
            "routes.projects_recent_changes.fetch_recent_changes",
            new=AsyncMock(return_value=[]),
        ):
            # 9999 hours -> 168 (1 week cap)
            result = await _handle_summarize_recent_changes(
                {"hours": 9999}, db, _make_user(),
            )
            assert result["hours"] == 168
            # 0 / negative -> 1 (lower bound)
            result = await _handle_summarize_recent_changes(
                {"hours": -5}, db, _make_user(),
            )
            assert result["hours"] == 1


# ---------------------------------------------------------------------------
# Branding constant — single source of truth contract
# ---------------------------------------------------------------------------

class TestAssistantBranding:
    def test_branding_dict_has_required_keys(self):
        from services.ai.agents.pmo import ASSISTANT_BRANDING
        # The branding dict is the contract for renaming — these three keys
        # are what the system prompt + future badge code consume.
        assert "name" in ASSISTANT_BRANDING
        assert "role_title" in ASSISTANT_BRANDING
        assert "tagline" in ASSISTANT_BRANDING
        for k in ("name", "role_title", "tagline"):
            assert isinstance(ASSISTANT_BRANDING[k], str)
            assert ASSISTANT_BRANDING[k].strip(), f"{k} must be non-empty"
