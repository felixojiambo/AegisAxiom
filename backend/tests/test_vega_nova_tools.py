"""Vega -> Nova tool bridge tests.

These tests keep the real conductor path in scope while patching the LLM
boundary. Vega is registered as the marketplace conversational route
``emp_ai_vega`` and may delegate deterministic cash-flow work only through
Nova's existing dispatcher.
"""
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret")

import services.ai.agents  # noqa: F401
from services.ai.agents.marketplace import ConversationalMarketplaceAgent
from services.ai.agents.registry import _AGENTS, register_instance, unregister
from services.ai.conductor import get_chat_response
from services.ai.llm import LlmResponse
from services.nova_engine.models import InsufficientHistoryError


# Mirrors MRR_MAPPING_UNAVAILABLE_MESSAGE in services/ai/agents/tools/
# nova_tools.py. Imported at runtime so a single source of truth -
# the dispatcher's message - drives both the test and the production
# error envelope. If the message text is tuned, the test follows
# without manual sync.
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
                "session_id": "chat_vega",
                "user_id": "user_vega",
                "tokens_used": 0,
                "pinned_agent": "emp_ai_vega",
                "title": "Vega Chat",
            },
            {
                "session_id": "chat_other",
                "user_id": "user_vega",
                "tokens_used": 0,
                "pinned_agent": "emp_ai_other",
                "title": "Other Chat",
            },
        ])
        self.chat_messages = _FakeCollection([])
        self.users = _FakeCollection([
            {"user_id": "user_vega", "role": "director"},
        ])
        self.vega_discovery_sessions = _FakeCollection([])
        self.ai_employee_user_state = _FakeCollection([])
        self.ai_employee_knowledge = _FakeCollection([])
        self.ai_usage = _FakeCollection([])
        self.ai_model_config = _FakeCollection([])
        self.projection_runs = _FakeCollection([])


def _employee_doc(employee_id="ai_vega", name="Vega"):
    return {
        "employee_id": employee_id,
        "name": name,
        "role": "AI Employee",
        "description": f"{name} conversational employee",
        "type": "conversational",
        "status": "active",
        "system_prompt": f"You are {name}.",
        "router_description": f"{name} conversations",
        "allowed_roles": ["director", "finance"],
        "allowed_user_ids": [],
        "tools": [],
        "state_schema": {},
    }


@pytest.fixture
def vega_agent():
    previous = _AGENTS.get("emp_ai_vega")
    agent = ConversationalMarketplaceAgent(_employee_doc())
    register_instance(agent)
    yield agent
    if previous is not None:
        _AGENTS["emp_ai_vega"] = previous
    else:
        unregister("emp_ai_vega")


@pytest.fixture
def other_agent():
    previous = _AGENTS.get("emp_ai_other")
    agent = ConversationalMarketplaceAgent(_employee_doc("ai_other", "Other"))
    register_instance(agent)
    yield agent
    if previous is not None:
        _AGENTS["emp_ai_other"] = previous
    else:
        unregister("emp_ai_other")


def _user(perms=None, user_id="user_vega"):
    return SimpleNamespace(
        user_id=user_id,
        email=f"{user_id}@example.com",
        name="Vega Tester",
        role="director",
        permissions=perms or ["chat.access", "vega.use", "nova.use"],
    )


def _history(content="Run a cash-flow projection"):
    return [{"role": "user", "content": content}]


def _tool_names(tools):
    names = []
    for tool in tools or []:
        for declaration in getattr(tool, "function_declarations", []) or []:
            names.append(declaration.name)
    return names


def _projection_call_args():
    return {
        "starting_cash": 50000,
        "projection_start_month": "2026-05",
        "horizon_weeks": 13,
        "manual_recurring_revenue": [
            {
                "name": "Manual MRR",
                "amount_per_month": 0,
                "line_type": "MRR",
                "starts_month": "2026-05",
            }
        ],
        "manual_cost_assumptions": [
            {
                "name": "Payroll",
                "amount_per_month": 7500,
                "line_type": "PAYROLL",
                "starts_month": "2026-05",
            }
        ],
    }


@pytest.mark.asyncio
async def test_vega_exposes_nova_tools_only_through_approved_bridge(vega_agent, other_agent):
    db = _FakeDb()
    llm = AsyncMock(return_value=LlmResponse(text="Ready.", total_tokens=0))

    with patch("services.ai.conductor.call_llm", new=llm):
        await get_chat_response(
            _user(),
            _history(),
            db,
            session_id="chat_vega",
            pinned_agent="emp_ai_vega",
            message_id="msg_vega",
        )

    assert "run_projection" in _tool_names(llm.await_args.kwargs["tools"])

    llm.reset_mock()
    llm.return_value = LlmResponse(text="Ready.", total_tokens=0)
    with patch("services.ai.conductor.call_llm", new=llm):
        await get_chat_response(
            _user(),
            _history(),
            db,
            session_id="chat_other",
            pinned_agent="emp_ai_other",
            message_id="msg_other",
        )

    assert "run_projection" not in _tool_names(llm.await_args.kwargs["tools"])


@pytest.mark.asyncio
async def test_vega_prompt_executes_nova_run_projection(vega_agent):
    db = _FakeDb()
    llm = AsyncMock(side_effect=[
        LlmResponse(
            text="",
            total_tokens=0,
            raw_parts=[],
            function_calls=[{"name": "run_projection", "args": _projection_call_args()}],
        ),
        LlmResponse(text="", total_tokens=0),
        LlmResponse(text="", total_tokens=0),
    ])
    nova_execute = AsyncMock(return_value={
        "projection_run_id": "pr_vega",
        "artifact_id": None,
        "summary": {
            "starting_cash": 50000,
            "ending_cash": 42500,
            "minimum_cash": 42500,
            "minimum_cash_week": 13,
            "total_revenue": 0,
            "total_costs": 7500,
            "net_change": -7500,
        },
        "weeks": [
            {
                "week_number": 1,
                "week_starting": "2026-05-04",
                "total_revenue": 0,
                "total_costs": 1875,
                "ending_cash": 48125,
            }
        ],
        "inputs_hash": "hash_vega",
    })

    with patch("services.ai.conductor.call_llm", new=llm), patch(
        "services.ai.agents.tools.nova_tools.execute_tool",
        new=nova_execute,
    ):
        result = await get_chat_response(
            _user(),
            _history(),
            db,
            session_id="chat_vega",
            pinned_agent="emp_ai_vega",
            message_id="msg_vega",
        )

    content = result["content"]
    assert "Projection complete" in content
    assert "pr_vega" in content
    assert "Unknown tool" not in content
    args, kwargs = nova_execute.await_args
    assert args[0] == "run_projection"
    assert args[1]["manual_recurring_revenue"][0]["line_type"] == "MRR"
    assert args[1]["manual_cost_assumptions"][0]["line_type"] == "PAYROLL"
    assert kwargs["user_id"] == "user_vega"
    assert kwargs["session_id"] == "chat_vega"
    assert kwargs["message_id"] == "msg_vega"


@pytest.mark.asyncio
async def test_vega_cannot_execute_nova_tools_without_nova_use(vega_agent):
    db = _FakeDb()
    llm = AsyncMock(side_effect=[
        LlmResponse(
            text="",
            total_tokens=0,
            raw_parts=[],
            function_calls=[{"name": "run_projection", "args": _projection_call_args()}],
        ),
        LlmResponse(text="", total_tokens=0),
        LlmResponse(text="", total_tokens=0),
    ])
    nova_execute = AsyncMock(return_value={"projection_run_id": "should_not_run"})

    with patch("services.ai.conductor.call_llm", new=llm), patch(
        "services.ai.agents.tools.nova_tools.execute_tool",
        new=nova_execute,
    ):
        result = await get_chat_response(
            _user(perms=["chat.access", "vega.use"]),
            _history(),
            db,
            session_id="chat_vega",
            pinned_agent="emp_ai_vega",
            message_id="msg_vega",
        )

    assert "permission to use Nova tools" in result["content"]
    assert "run_projection" not in _tool_names(llm.await_args_list[0].kwargs["tools"])
    nova_execute.assert_not_awaited()
    assert db.projection_runs.inserted == []


@pytest.mark.asyncio
async def test_vega_missing_mrr_path_returns_clear_message(vega_agent):
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
                        "horizon_weeks": 13,
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
            line="MRR",
            available_months=0,
            required_months=1,
        )),
    ):
        result = await get_chat_response(
            _user(),
            _history("Run a projection without manual MRR."),
            db,
            session_id="chat_vega",
            pinned_agent="emp_ai_vega",
            message_id="msg_vega",
        )

    assert result["content"] == MRR_MESSAGE
    assert "Unknown tool" not in result["content"]


@pytest.mark.asyncio
async def test_vega_bridge_exposes_compare_runs_when_user_has_nova_use(vega_agent):
    """The Vega bridge re-exports every Nova tool, including new ones,
    when the caller holds nova.use. compare_runs landing here without
    code changes confirms the bridge isn't allow-listing tools by name."""
    db = _FakeDb()
    llm = AsyncMock(return_value=LlmResponse(text="Ready.", total_tokens=0))

    with patch("services.ai.conductor.call_llm", new=llm):
        await get_chat_response(
            _user(),  # has nova.use
            _history("Compare the last two cash-flow projections."),
            db,
            session_id="chat_vega",
            pinned_agent="emp_ai_vega",
            message_id="msg_vega",
        )

    tool_names = _tool_names(llm.await_args.kwargs["tools"])
    assert "compare_runs" in tool_names
    assert "run_projection" in tool_names


@pytest.mark.asyncio
async def test_vega_bridge_hides_compare_runs_without_nova_use(vega_agent):
    """When the user lacks nova.use the bridge should hide every Nova
    tool, not just run_projection."""
    db = _FakeDb()
    llm = AsyncMock(return_value=LlmResponse(text="Ready.", total_tokens=0))

    with patch("services.ai.conductor.call_llm", new=llm):
        await get_chat_response(
            _user(perms=["chat.access", "vega.use"]),
            _history(),
            db,
            session_id="chat_vega",
            pinned_agent="emp_ai_vega",
            message_id="msg_vega",
        )

    tool_names = _tool_names(llm.await_args.kwargs["tools"])
    assert "compare_runs" not in tool_names
    assert "run_projection" not in tool_names
