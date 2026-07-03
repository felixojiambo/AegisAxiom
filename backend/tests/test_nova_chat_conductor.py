"""Nova chat/conductor integration tests.

These tests keep the real chat orchestration path in scope while patching
LLM and Nova engine boundaries. They verify Nova's static tool schemas are
visible to the conductor and Nova tool calls do not fall through to the
generic conversational tool registry.
"""
import os
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret")

import services.ai.agents  # noqa: F401
from services.ai.conductor import get_chat_response
from services.ai.agents.registry import _AGENTS
from services.ai.llm import LlmResponse
from services.nova_engine.models import (
    InsufficientHistoryError,
    ProjectionInputs,
    ProjectionOutput,
    WeekRow,
)


# Mirrors MRR_MAPPING_UNAVAILABLE_MESSAGE in services/ai/agents/tools/
# nova_tools.py. Imported at runtime so a single source of truth -
# the dispatcher's message - drives both the test and the production
# error envelope.
from services.ai.agents.tools.nova_tools import (
    MRR_MAPPING_UNAVAILABLE_MESSAGE as MRR_MESSAGE,
)


class _FakeCursor:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def sort(self, *args, **kwargs):
        return self

    def skip(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    async def to_list(self, length=None):
        if length is None:
            return list(self.docs)
        return list(self.docs[:length])


class _FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.inserted = []
        self.updated = []

    async def find_one(self, query=None, projection=None, **kwargs):
        query = query or {}
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                return dict(doc)
        return None

    def find(self, query=None, projection=None):
        query = query or {}
        docs = [
            dict(doc) for doc in self.docs
            if all(doc.get(k) == v for k, v in query.items() if not isinstance(v, dict))
        ]
        return _FakeCursor(docs)

    def aggregate(self, *args, **kwargs):
        return _FakeCursor([])

    async def find_one_and_update(self, query, update, **kwargs):
        query = query or {}
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                for key, value in (update.get("$inc") or {}).items():
                    doc[key] = doc.get(key, 0) + value
                return dict(doc)

        doc = dict(query)
        for key, value in (update.get("$inc") or {}).items():
            doc[key] = value
        self.docs.append(doc)
        return dict(doc)

    async def insert_one(self, doc):
        stored = dict(doc)
        self.docs.append(stored)
        self.inserted.append(stored)
        return SimpleNamespace(inserted_id=f"id_{len(self.inserted)}")

    async def update_one(self, query, update, **kwargs):
        self.updated.append((query, update))
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                for key, value in (update.get("$set") or {}).items():
                    doc[key] = value
                for key, value in (update.get("$inc") or {}).items():
                    doc[key] = doc.get(key, 0) + value
                for key in (update.get("$unset") or {}):
                    doc.pop(key, None)
                break
        return SimpleNamespace(modified_count=1)


class _FakeDb:
    def __init__(self):
        self.chat_sessions = _FakeCollection([
            {
                "session_id": "chat_nova",
                "user_id": "user_nova",
                "tokens_used": 0,
                "pinned_agent": "nova",
                "title": "New Chat",
            }
        ])
        self.chat_messages = _FakeCollection([])
        self.counters = _FakeCollection([])
        self.users = _FakeCollection([
            {"user_id": "user_nova", "role": "finance"},
            {"user_id": "user_no_nova", "role": "finance"},
        ])
        self.ai_usage = _FakeCollection([])
        self.ai_model_config = _FakeCollection([])


def _user(perms=None, user_id="user_nova"):
    return SimpleNamespace(
        user_id=user_id,
        email=f"{user_id}@example.com",
        name="Nova Tester",
        role="finance",
        permissions=perms or ["chat.access", "nova.use"],
    )


def _history(content="Run a cash-flow projection"):
    return [{"role": "user", "content": content}]


def _tool_names(tools):
    names = []
    for tool in tools or []:
        for declaration in getattr(tool, "function_declarations", []) or []:
            names.append(declaration.name)
    return names


def _make_inputs(**updates):
    data = {
        "starting_cash": 50000.0,
        "projection_start_month": "2026-05",
        "horizon_weeks": 13,
        "ps_revenue_last_3_months": [10000.0, 10000.0, 10000.0],
        "fs_revenue_last_3_months": [5000.0, 5000.0, 5000.0],
        "ps_gross_profit_last_3_months": [7000.0, 7000.0, 7000.0],
        "fs_gross_profit_last_3_months": [3000.0, 3000.0, 3000.0],
    }
    data.update(updates)
    return ProjectionInputs(**data)


def _make_output(inputs_hash="hash_chat"):
    return ProjectionOutput(
        weeks=[
            WeekRow(
                week_number=1,
                week_starting=date(2026, 5, 4),
                total_revenue=0.0,
                payroll=1875.0,
                total_costs=1875.0,
                net_cash_flow=-1875.0,
                ending_cash=48125.0,
            )
        ],
        starting_cash=50000.0,
        ending_cash=48125.0,
        minimum_cash=48125.0,
        minimum_cash_week=1,
        total_revenue=0.0,
        total_costs=1875.0,
        net_change=-1875.0,
        inputs_hash=inputs_hash,
        computed_at=datetime.now(timezone.utc),
    )


def test_nova_agent_exposes_conversational_tool_schemas():
    """The conductor sees Nova's full tool surface, with no duplicates and
    with the expanded finance tools (issued/open invoices, GL transactions,
    finance-data freshness) reachable. The full canonical list lives in
    test_nova_tools.py — this test only verifies the conductor wiring and
    pins the tools the conductor's tool-call routing depends on."""
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    agent = _AGENTS["nova"]

    assert agent.is_conversational is True
    assert agent.tool_schemas is NOVA_TOOL_SCHEMAS

    names = [schema["name"] for schema in agent.tool_schemas]
    assert len(names) == len(set(names)), "duplicate tool names in NOVA_TOOL_SCHEMAS"

    required_core = {
        "run_projection",
        "apply_override",
        "add_manual_assumption",
        "explain_week",
        "query_finance_data",
        "reset_scenario_state",
        "compare_runs",
    }
    required_expanded = {
        "query_invoice_totals",
        "get_open_invoices",
        "query_gl_transactions",
        "get_finance_data_freshness",
    }
    assert required_core <= set(names)
    assert required_expanded <= set(names)


@pytest.mark.asyncio
async def test_conductor_includes_nova_tool_schemas_when_pinned():
    db = _FakeDb()
    llm = AsyncMock(return_value=LlmResponse(text="Ready.", total_tokens=0))

    with patch("services.ai.conductor.call_llm", new=llm):
        result = await get_chat_response(
            _user(),
            _history(),
            db,
            session_id="chat_nova",
            pinned_agent="nova",
            message_id="msg_user",
        )

    assert result["content"] == "Ready."
    tools = llm.await_args.kwargs["tools"]
    assert "run_projection" in _tool_names(tools)
    assert "add_manual_assumption" in _tool_names(tools)


@pytest.mark.asyncio
async def test_nova_tool_calls_dispatch_to_nova_tool_executor():
    db = _FakeDb()
    llm = AsyncMock(side_effect=[
        LlmResponse(
            text="",
            total_tokens=0,
            raw_parts=[],
            function_calls=[
                {
                    "name": "run_projection",
                    "args": {
                        "starting_cash": 50000,
                        "projection_start_month": "2026-05",
                    },
                }
            ],
        ),
        LlmResponse(text="Projection complete.", total_tokens=0),
    ])
    nova_execute = AsyncMock(return_value={
        "projection_run_id": "pr_chat",
        "artifact_id": None,
        "summary": {"ending_cash": 50000},
        "weeks": [],
        "inputs_hash": "hash_chat",
    })

    with patch("services.ai.conductor.call_llm", new=llm), patch(
        "services.ai.agents.tools.nova_tools.execute_tool",
        new=nova_execute,
    ):
        result = await get_chat_response(
            _user(),
            _history(),
            db,
            session_id="chat_nova",
            pinned_agent="nova",
            message_id="msg_user",
        )

    assert result["content"] == "Projection complete."
    args, kwargs = nova_execute.await_args
    assert args[0] == "run_projection"
    assert kwargs["user_id"] == "user_nova"
    assert kwargs["session_id"] == "chat_nova"
    assert kwargs["message_id"] == "msg_user"


@pytest.mark.asyncio
async def test_nova_turn_with_tool_call_emits_both_tool_selection_and_answer_llm_spans(
    caplog,
):
    """A real conductor Nova turn with one tool call goes through TWO
    distinct LLM phases:

      1. ``call_llm`` with ``tools=...`` and no tool_responses — the
         model is selecting a tool. Its wall time belongs in
         ``tool_selection_ms``.
      2. ``call_llm`` with ``tool_responses=...`` — the model writes
         the answer using the tool result. Its wall time belongs in
         ``answer_llm_ms``.

    A regression that re-aliased one to the other (or dropped a
    span) would either collapse them or leave one missing from the
    emitted ``[nova_perf]`` line. This test pins both spans show up
    when a Nova tool call actually runs end-to-end through the
    conductor."""
    import logging as _logging

    db = _FakeDb()
    llm = AsyncMock(side_effect=[
        LlmResponse(
            text="",
            total_tokens=0,
            raw_parts=[],
            function_calls=[
                {
                    "name": "run_projection",
                    "args": {
                        "starting_cash": 50000,
                        "projection_start_month": "2026-05",
                    },
                }
            ],
        ),
        LlmResponse(text="Projection complete.", total_tokens=0),
    ])
    nova_execute = AsyncMock(return_value={
        "projection_run_id": "pr_chat",
        "artifact_id": None,
        "summary": {"ending_cash": 50000},
        "weeks": [],
        "inputs_hash": "hash_chat",
    })

    with caplog.at_level(_logging.INFO, logger="services.ai.nova_perf"), patch(
        "services.ai.conductor.call_llm", new=llm
    ), patch(
        "services.ai.agents.tools.nova_tools.execute_tool",
        new=nova_execute,
    ):
        result = await get_chat_response(
            _user(),
            _history(),
            db,
            session_id="chat_nova",
            pinned_agent="nova",
            message_id="msg_user",
        )

    # Chat path still returns the answer text unchanged — instrumentation
    # must not alter the response shape.
    assert result["content"] == "Projection complete."

    perf_lines = [
        r.getMessage() for r in caplog.records if "[nova_perf]" in r.getMessage()
    ]
    assert len(perf_lines) == 1, (
        f"expected exactly one [nova_perf] line per Nova turn, got: {perf_lines}"
    )
    line = perf_lines[0]
    assert "tool_selection_ms=" in line, line
    assert "answer_llm_ms=" in line, line
    # Sanity: tool execution and primary tool name still emit alongside
    # — a regression that lost the tool dispatch timing would silently
    # remove these fields.
    assert "tool_execution_ms=" in line, line
    assert "tool=run_projection" in line, line


@pytest.mark.asyncio
async def test_nova_chat_run_projection_accepts_manual_mrr_manual_costs_and_persists_scope():
    db = _FakeDb()
    captured_build_kwargs = {}
    captured_save_kwargs = {}

    async def fake_build_projection_inputs(**kwargs):
        captured_build_kwargs.update(kwargs)
        return _make_inputs(
            manual_cost_assumptions=kwargs.get("manual_cost_assumptions", []),
            manual_recurring_revenue=kwargs.get("manual_recurring_revenue", []),
            overrides=kwargs.get("overrides", []),
        )

    async def fake_save_run(**kwargs):
        captured_save_kwargs.update(kwargs)
        return {
            "projection_run_id": "pr_chat",
            "inputs": kwargs["inputs"].model_dump(mode="json"),
            "output": kwargs["output"].model_dump(mode="json"),
        }

    llm = AsyncMock(side_effect=[
        LlmResponse(
            text="",
            total_tokens=0,
            raw_parts=[],
            function_calls=[
                {
                    "name": "run_projection",
                    "args": {
                        "starting_cash": 50000,
                        "projection_start_month": "2026-05",
                        "horizon_weeks": 13,
                        "manual_recurring_revenue": [
                            {
                                "name": "Manual MRR neutralizer",
                                "amount_per_month": 0.0,
                                "line_type": "MRR",
                                "starts_month": "2026-05",
                            }
                        ],
                        "manual_cost_assumptions": [
                            {
                                "name": "Payroll",
                                "amount_per_month": 7500.0,
                                "line_type": "PAYROLL",
                                "starts_month": "2026-05",
                            }
                        ],
                        "sales_modifier": 1.0,
                        "cost_modifier": 1.0,
                    },
                }
            ],
        ),
        LlmResponse(text="Done.", total_tokens=0),
    ])

    with patch("services.ai.conductor.call_llm", new=llm), patch(
        "services.nova_engine.data_loader.build_projection_inputs_from_globals",
        new=AsyncMock(side_effect=fake_build_projection_inputs),
    ), patch(
        "services.nova_engine.engine.project_cash_flow",
        return_value=_make_output(),
    ) as project, patch(
        "services.nova_engine.persistence.save_run",
        new=AsyncMock(side_effect=fake_save_run),
    ), patch(
        "services.ai.agents.tools.nova_tools._persist_cashflow_chart_artifact",
        new=AsyncMock(return_value=None),
    ):
        result = await get_chat_response(
            _user(),
            _history(),
            db,
            session_id="chat_nova",
            pinned_agent="nova",
            message_id="msg_user",
        )

    assert result["content"] == "Done."
    assert captured_build_kwargs["manual_recurring_revenue"][0].line_type == "MRR"
    assert captured_build_kwargs["manual_recurring_revenue"][0].amount_per_month == 0.0
    assert captured_build_kwargs["manual_cost_assumptions"][0].line_type == "PAYROLL"
    projected_inputs = project.call_args.args[0]
    assert projected_inputs.sales_modifier == 1.0
    assert projected_inputs.cost_modifier == 1.0
    assert captured_save_kwargs["user_id"] == "user_nova"
    assert captured_save_kwargs["session_id"] == "chat_nova"
    assert captured_save_kwargs["message_id"] == "msg_user"


@pytest.mark.asyncio
async def test_nova_chat_missing_mrr_returns_clear_message_not_unknown_tool():
    db = _FakeDb()
    llm = AsyncMock(side_effect=[
        LlmResponse(
            text="",
            total_tokens=0,
            raw_parts=[],
            function_calls=[
                {
                    "name": "run_projection",
                    "args": {
                        "starting_cash": 50000,
                        "projection_start_month": "2026-05",
                    },
                }
            ],
        ),
        LlmResponse(text="", total_tokens=0),
        LlmResponse(text="", total_tokens=0),
    ])

    with patch("services.ai.conductor.call_llm", new=llm), patch(
        "services.nova_engine.data_loader.build_projection_inputs_from_globals",
        new=AsyncMock(side_effect=InsufficientHistoryError(
            line="MRR", available_months=0, required_months=1,
        )),
    ):
        result = await get_chat_response(
            _user(),
            _history(),
            db,
            session_id="chat_nova",
            pinned_agent="nova",
            message_id="msg_user",
        )

    assert result["content"] == MRR_MESSAGE


@pytest.mark.asyncio
async def test_user_without_nova_use_cannot_use_pinned_nova():
    db = _FakeDb()
    llm = AsyncMock(return_value=LlmResponse(text="should not be called"))

    with patch("services.ai.conductor.call_llm", new=llm):
        result = await get_chat_response(
            _user(perms=["chat.access"], user_id="user_no_nova"),
            _history(),
            db,
            session_id="chat_nova",
            pinned_agent="nova",
            message_id="msg_user",
        )

    assert "don't have access" in result
    llm.assert_not_awaited()


def test_chat_route_with_pinned_nova_executes_run_projection_and_passes_message_id():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.chat as chat_route

    db = _FakeDb()
    user = _user()
    llm = AsyncMock(side_effect=[
        LlmResponse(
            text="",
            total_tokens=0,
            raw_parts=[],
            function_calls=[
                {
                    "name": "run_projection",
                    "args": {
                        "starting_cash": 50000,
                        "projection_start_month": "2026-05",
                    },
                }
            ],
        ),
        LlmResponse(text="Projection complete.", total_tokens=0),
    ])
    nova_execute = AsyncMock(return_value={
        "projection_run_id": "pr_route",
        "artifact_id": None,
        "summary": {"ending_cash": 50000},
        "weeks": [],
        "inputs_hash": "hash_route",
    })

    app = FastAPI()
    app.include_router(chat_route.router, prefix="/api/chat")
    app.dependency_overrides[chat_route.get_current_user] = lambda: user

    with patch.object(chat_route, "get_db", return_value=db), patch(
        "services.ai.conductor.call_llm",
        new=llm,
    ), patch(
        "services.ai.agents.tools.nova_tools.execute_tool",
        new=nova_execute,
    ):
        response = TestClient(app).post(
            "/api/chat/sessions/chat_nova/messages",
            json={"content": "Run a 13-week Nova projection."},
        )

    assert response.status_code == 200
    assert response.json()["role"] == "assistant"
    assert response.json()["content"] == "Projection complete."
    args, kwargs = nova_execute.await_args
    assert args[0] == "run_projection"
    assert kwargs["session_id"] == "chat_nova"
    assert kwargs["message_id"].startswith("msg_")
    roles = [doc["role"] for doc in db.chat_messages.docs]
    assert roles == ["user", "assistant"]


def test_chat_route_creates_pinned_nova_session():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.chat as chat_route

    db = _FakeDb()
    app = FastAPI()
    app.include_router(chat_route.router, prefix="/api/chat")
    app.dependency_overrides[chat_route.get_current_user] = lambda: _user()

    with patch.object(chat_route, "get_db", return_value=db):
        response = TestClient(app).post(
            "/api/chat/sessions",
            json={"agent": "nova"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["pinned_agent"] == "nova"
    assert db.chat_sessions.inserted[-1]["pinned_agent"] == "nova"


@pytest.mark.asyncio
async def test_conductor_dispatches_compare_runs_to_nova_executor():
    """Nova-pinned chat surfaces compare_runs in tool defs and the
    conductor routes the call to nova_tools.execute_tool with user
    scoping (user_id forwarded, session_id forwarded)."""
    db = _FakeDb()
    llm = AsyncMock(side_effect=[
        LlmResponse(
            text="",
            total_tokens=0,
            raw_parts=[],
            function_calls=[
                {
                    "name": "compare_runs",
                    "args": {
                        "base_run_id": "pr_old",
                        "compare_run_id": "pr_new",
                    },
                }
            ],
        ),
        LlmResponse(text="Ending cash improved by $5,000.", total_tokens=0),
    ])
    nova_execute = AsyncMock(return_value={
        "base_run_id": "pr_old",
        "compare_run_id": "pr_new",
        "deltas": {
            "ending_cash": 5000.0, "minimum_cash": -1000.0,
            "total_revenue": 2000.0, "total_costs": 2000.0,
            "net_change": 5000.0, "starting_cash": 0.0,
        },
        "minimum_cash_week": {"base": 3, "compare": 2, "shifted": True},
        "summaries": {"base": {}, "compare": {}},
        "weekly_deltas": [],
        "summary_text": "Ending cash improved by $5,000.",
    })

    with patch("services.ai.conductor.call_llm", new=llm), patch(
        "services.ai.agents.tools.nova_tools.execute_tool",
        new=nova_execute,
    ):
        result = await get_chat_response(
            _user(),
            _history("Compare my last two cash-flow projections."),
            db,
            session_id="chat_nova",
            pinned_agent="nova",
            message_id="msg_user",
        )

    assert result["content"] == "Ending cash improved by $5,000."
    # compare_runs is in the tool defs the LLM sees.
    initial_tools = llm.await_args_list[0].kwargs["tools"]
    assert "compare_runs" in _tool_names(initial_tools)
    # Dispatcher forwarded the call to nova_tools.execute_tool with user
    # scoping intact.
    args, kwargs = nova_execute.await_args
    assert args[0] == "compare_runs"
    assert args[1] == {"base_run_id": "pr_old", "compare_run_id": "pr_new"}
    assert kwargs["user_id"] == "user_nova"
    assert kwargs["session_id"] == "chat_nova"


@pytest.mark.asyncio
async def test_conductor_renders_compare_runs_summary_text_when_llm_returns_no_prose():
    """If the LLM returns a tool call but no narration, the conductor
    falls back to compare_runs's summary_text rather than 'Nova could
    not complete'. Mirrors the run_projection fallback path."""
    db = _FakeDb()
    llm = AsyncMock(side_effect=[
        LlmResponse(
            text="",
            total_tokens=0,
            raw_parts=[],
            function_calls=[
                {"name": "compare_runs", "args": {}},
            ],
        ),
        LlmResponse(text="", total_tokens=0),  # LLM produced no prose
        LlmResponse(text="", total_tokens=0),  # retry-without-tools also empty
    ])
    summary = (
        "Comparing run `pr_a` (base) against run `pr_b` (compare):\n"
        "Ending cash improved by $+1,000.00."
    )
    nova_execute = AsyncMock(return_value={
        "base_run_id": "pr_a",
        "compare_run_id": "pr_b",
        "deltas": {"ending_cash": 1000.0},
        "minimum_cash_week": {"base": 1, "compare": 1, "shifted": False},
        "summaries": {"base": {}, "compare": {}},
        "weekly_deltas": [],
        "summary_text": summary,
    })

    with patch("services.ai.conductor.call_llm", new=llm), patch(
        "services.ai.agents.tools.nova_tools.execute_tool",
        new=nova_execute,
    ):
        result = await get_chat_response(
            _user(),
            _history("compare last two"),
            db,
            session_id="chat_nova",
            pinned_agent="nova",
            message_id="msg_user",
        )

    # Fallback rendered the tool's own summary_text instead of a generic
    # 'could not complete' message.
    assert summary in result["content"]


def test_chat_route_rejects_nova_pinned_session_without_nova_permission():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.chat as chat_route

    app = FastAPI()
    app.include_router(chat_route.router, prefix="/api/chat")
    app.dependency_overrides[chat_route.get_current_user] = lambda: _user(
        perms=["chat.access"],
        user_id="user_no_nova",
    )

    response = TestClient(app).post(
        "/api/chat/sessions",
        json={"agent": "nova"},
    )

    assert response.status_code == 403
    assert "Insufficient permissions" in response.json()["detail"]


# ===========================================================================
# PR #164 follow-up — shadow planner wrapped in asyncio.to_thread
# ===========================================================================


def test_shadow_planner_call_is_dispatched_via_to_thread():
    """``run_shadow_evaluation`` is dispatched via
    ``asyncio.to_thread`` so any future I/O inside the planner
    pipeline cannot block the event loop. Source-grep the
    conductor's invocation pattern to pin the contract."""
    import inspect

    import services.ai.conductor as conductor_mod

    source = inspect.getsource(conductor_mod)
    # The call site must use the to_thread bridge.
    assert "asyncio.to_thread(" in source
    assert "run_shadow_evaluation" in source
    # And the module-level asyncio import must be present.
    assert "import asyncio" in source
