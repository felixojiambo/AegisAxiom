"""Single-row audit (v1.3 Theme B / Thread 1B).

Verifies the retirement of the v1.2 dual-write pattern:

  * v1.2 wrote TWO audit rows on a successful AI write:
      - one `event_category="activity"` from crud_envelope (the route)
      - one `event_category="ai_action"` from `_audit_pmo_ai_action` (the AI tool)
    `projects_recent_changes` then deduped them and preferred the ai_action row.

  * v1.3 writes ONE row: crud_envelope's activity row is stamped with
    `actor_type=pmo_ai` + `tool_name` + `tier` via the `ai_actor_ctx`
    contextvar (set in `execute_tool`). The AI-tool-level audit helper is a
    no-op on the success path. Failed AI calls still write a standalone
    error row so the forensic trail is preserved.

These tests exercise the audit_service.log_ingested_crud path directly,
toggling the contextvar to simulate AI vs human callers. crud_envelope's
upstream policy and event-bus hooks are out of scope for this thread.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services.audit_service import ai_actor_ctx, log_ingested_crud


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

def _make_user(user_id="u_pm_grace", email="grace@x.io"):
    return SimpleNamespace(user_id=user_id, email=email, name="Grace")


class _AuditColl:
    def __init__(self):
        self.inserted: list[dict] = []

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return SimpleNamespace(inserted_id="stub_id")

    async def find_one(self, query=None, projection=None, **_kw):
        # Idempotency check: return None so we always write
        return None


class _Db:
    def __init__(self):
        self.audit_logs = _AuditColl()


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Single-row contract
# ---------------------------------------------------------------------------

class TestAiActorContextStamping:
    def test_ai_write_produces_one_row_with_pmo_ai_stamp(self):
        """When ai_actor_ctx is set, log_ingested_crud stamps the row."""
        db = _Db()

        token = ai_actor_ctx.set({
            "actor_type": "pmo_ai",
            "tool_name": "update_task_status",
            "tier": 1,
            "session_id": "chat_42",
        })
        try:
            run(log_ingested_crud(
                module="projects",
                entity="task",
                action="status_changed",
                resource_id="task_x",
                actor=_make_user(),
                before={"status": "in_progress"},
                after={"status": "blocked"},
                request_id="req_ai_1",
                db=db,
            ))
        finally:
            ai_actor_ctx.reset(token)

        # ── Exactly ONE row ──
        assert len(db.audit_logs.inserted) == 1
        row = db.audit_logs.inserted[0]
        # The row is the standard activity row (not a separate ai_action row).
        assert row["event_category"] == "activity"
        # Stamped with AI metadata.
        assert row["details"]["actor_type"] == "pmo_ai"
        assert row["details"]["tool_name"] == "update_task_status"
        assert row["details"]["tier"] == 1
        assert row["details"]["session_id"] == "chat_42"
        # Top-level mirror so legacy admin consumers still see it.
        assert row["actor_type"] == "pmo_ai"
        assert row["tool_name"] == "update_task_status"
        # The standard activity-row fields are still present.
        assert row["resource_type"] == "projects.task"
        assert row["resource_id"] == "task_x"
        assert row["action"] == "status_changed"
        assert row["actor_id"] == "u_pm_grace"

    def test_human_write_produces_one_row_without_actor_type(self):
        """When ai_actor_ctx is NOT set, the row is unstamped (=human)."""
        db = _Db()

        # Make sure the contextvar is at its default (empty dict).
        assert ai_actor_ctx.get() == {}

        run(log_ingested_crud(
            module="projects",
            entity="task",
            action="status_changed",
            resource_id="task_x",
            actor=_make_user(user_id="u_henry", email="h@x"),
            before={"status": "open"},
            after={"status": "complete"},
            request_id="req_human_1",
            db=db,
        ))

        assert len(db.audit_logs.inserted) == 1
        row = db.audit_logs.inserted[0]
        assert row["event_category"] == "activity"
        # No actor_type stamp at top-level OR in details for human writes.
        assert "actor_type" not in row
        assert "tool_name" not in row
        assert "actor_type" not in row["details"]
        assert "tool_name" not in row["details"]
        # Standard activity shape is intact.
        assert row["actor_id"] == "u_henry"
        assert row["resource_type"] == "projects.task"

    def test_contextvar_isolation_does_not_leak_across_writes(self):
        """Setting + resetting ai_actor_ctx isolates the stamp to one write."""
        db = _Db()

        # 1st write — AI
        token = ai_actor_ctx.set({
            "actor_type": "pmo_ai",
            "tool_name": "update_task_status",
            "tier": 1,
        })
        try:
            run(log_ingested_crud(
                module="projects", entity="task", action="status_changed",
                resource_id="t1", actor=_make_user(),
                before={"s": "a"}, after={"s": "b"},
                request_id="r1", db=db,
            ))
        finally:
            ai_actor_ctx.reset(token)

        # 2nd write — human (post-reset)
        run(log_ingested_crud(
            module="projects", entity="task", action="status_changed",
            resource_id="t2", actor=_make_user(),
            before={"s": "a"}, after={"s": "b"},
            request_id="r2", db=db,
        ))

        assert len(db.audit_logs.inserted) == 2
        ai_row, human_row = db.audit_logs.inserted
        assert ai_row["details"]["actor_type"] == "pmo_ai"
        assert ai_row["details"]["tool_name"] == "update_task_status"
        # 2nd row is clean — no AI stamp leaked.
        assert "actor_type" not in human_row["details"]
        assert "tool_name" not in human_row["details"]


# ---------------------------------------------------------------------------
# AI-tool-level audit helper now no-ops on success
# ---------------------------------------------------------------------------

class TestAuditHelperSuccessNoop:
    @pytest.mark.asyncio
    async def test_audit_pmo_ai_action_success_writes_nothing(self):
        """v1.3: success path is a no-op — crud_envelope writes the single row."""
        from services.ai.agents.tools.conversational_tools import _audit_pmo_ai_action

        class _Stub:
            inserted: list = []

            async def insert_one(self, doc):
                self.inserted.append(doc)

        stub = _Stub()
        db = SimpleNamespace(audit_logs=stub)

        await _audit_pmo_ai_action(
            db,
            tool_name="update_task_status", tier=1, user=_make_user(),
            session_id="chat_42", resource_type="task", resource_id="task_x",
            params={"status": "complete"}, result_status="success",
        )
        assert stub.inserted == [], (
            "v1.3 single-row contract: the helper must NOT write on success — "
            "the activity row from crud_envelope is the single source of truth."
        )

    @pytest.mark.asyncio
    async def test_audit_pmo_ai_action_error_still_writes_one_row(self):
        """Errors write a standalone ai_action row so the failure trail exists."""
        from services.ai.agents.tools.conversational_tools import _audit_pmo_ai_action

        class _Stub:
            def __init__(self):
                self.inserted: list = []

            async def insert_one(self, doc):
                self.inserted.append(doc)

        stub = _Stub()
        db = SimpleNamespace(audit_logs=stub)

        await _audit_pmo_ai_action(
            db,
            tool_name="update_task_status", tier=1, user=_make_user(),
            session_id="chat_42", resource_type="task", resource_id="task_x",
            params={"status": "complete"}, result_status="error",
            error_detail="Task not found",
        )
        assert len(stub.inserted) == 1
        row = stub.inserted[0]
        assert row["event_category"] == "ai_action"
        assert row["result_status"] == "error"
        assert row["error_detail"] == "Task not found"
        assert row["actor_type"] == "pmo_ai"
        assert row["tool_name"] == "update_task_status"
