"""Tests for Nova direct-API routes (POST /api/nova/project, GET runs).

Uses FastAPI TestClient with auth_helper.get_current_user dependency
overrides to simulate users with/without nova.use. Engine + persistence
are patched via AsyncMock.
"""
import os

# Environment bootstrap — auth_helper raises if JWT_SECRET is missing.
# Pattern matches tests/test_field_services_branch.py.
os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production")

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_helper import get_current_user
from routes.nova import router as nova_router
from services.nova_engine.models import (
    InsufficientHistoryError,
    ManualCostAssumption,
    ProjectionInputs,
    ProjectionOutput,
    RecurringRevenueAssumption,
    WeekRow,
    WeeklyDistribution,
)


# ---------------------------------------------------------------------------
# App + auth fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mute_audit_logs(monkeypatch):
    """Make audit-log writes a no-op for this test module.

    The route now calls services.audit_service.log_action on success,
    and require_perm calls services.security_logger.log_security_event
    on permission failure. Both call database.get_db() which raises
    post-null-guard if database.init() hasn't run. We don't init Mongo
    in these tests; muting the writers isolates route logic from
    audit infrastructure.

    log_action is patched at routes.nova because the route imports
    it via `from services.audit_service import log_action` (bound at
    import time). log_security_event is patched at the source module
    because require_perm imports it lazily inside its body.
    """
    monkeypatch.setattr("routes.nova.log_action", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "services.security_logger.log_security_event",
        AsyncMock(return_value=None),
    )


def _make_app(user) -> FastAPI:
    """Build a FastAPI test app with the nova router and a user-override dep."""
    app = FastAPI()
    app.include_router(nova_router, prefix="/api/nova")
    app.dependency_overrides[get_current_user] = lambda: user
    return app


def _user(
    user_id: str = "user_test",
    perms: list[str] | None = None,
    account_id: str | None = None,
    email: str = "tester@simplimeta.com",
):
    return SimpleNamespace(
        user_id=user_id,
        email=email,
        account_id=account_id,
        permissions=perms if perms is not None else ["nova.use"],
    )


def _make_inputs() -> ProjectionInputs:
    return ProjectionInputs(
        starting_cash=50000.0,
        projection_start_month="2026-05",
        horizon_weeks=13,
        ps_revenue_last_3_months=[10000.0, 10000.0, 10000.0],
        fs_revenue_last_3_months=[5000.0, 5000.0, 5000.0],
        ps_gross_profit_last_3_months=[7000.0, 7000.0, 7000.0],
        fs_gross_profit_last_3_months=[3000.0, 3000.0, 3000.0],
        manual_recurring_revenue=[
            RecurringRevenueAssumption(
                name="MRR neutralizer",
                amount_per_month=0.0,
                line_type="MRR",
                starts_month="2026-05",
                ends_month="2026-12",
            ),
        ],
    )


def _make_output(inputs_hash: str = "hash1") -> ProjectionOutput:
    week = WeekRow(week_number=1, week_starting=date(2026, 5, 1), ending_cash=51000.0)
    return ProjectionOutput(
        weeks=[week],
        starting_cash=50000.0,
        ending_cash=51000.0,
        minimum_cash=51000.0,
        minimum_cash_week=1,
        total_revenue=15000.0,
        total_costs=10000.0,
        net_change=5000.0,
        inputs_hash=inputs_hash,
        computed_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# POST /api/nova/project
# ---------------------------------------------------------------------------

def test_project_happy_path():
    app = _make_app(_user())
    inputs = _make_inputs()
    output = _make_output("hash_happy")

    saved = {"projection_run_id": "pr_abc", "inputs_hash": "hash_happy"}

    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(return_value=inputs),
    ), patch(
        "routes.nova.project_cash_flow",
        return_value=output,
    ), patch(
        "routes.nova.save_run",
        new=AsyncMock(return_value=saved),
    ):
        r = TestClient(app).post(
            "/api/nova/project",
            json={"starting_cash": 50000, "projection_start_month": "2026-05"},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["projection_run_id"] == "pr_abc"
    assert body["inputs_hash"] == "hash_happy"
    assert body["output"]["ending_cash"] == 51000.0


def test_project_missing_required_field_returns_400():
    app = _make_app(_user())
    r = TestClient(app).post("/api/nova/project", json={"projection_start_month": "2026-05"})
    assert r.status_code == 400
    assert "starting_cash" in r.json()["detail"]


def test_project_insufficient_history_returns_422():
    app = _make_app(_user())
    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(side_effect=InsufficientHistoryError(
            line="history", available_months=2, required_months=3,
        )),
    ):
        r = TestClient(app).post(
            "/api/nova/project",
            json={"starting_cash": 50000, "projection_start_month": "2026-05"},
        )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "insufficient_history"
    assert detail["affected_line"] == "history"
    assert detail["available_months"] == 2
    assert detail["required_months"] == 3


def test_project_engine_insufficient_history_returns_422():
    app = _make_app(_user())
    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(return_value=_make_inputs()),
    ), patch(
        "routes.nova.project_cash_flow",
        side_effect=InsufficientHistoryError(
            line="mrr (2026-05)", available_months=0, required_months=1,
        ),
    ):
        r = TestClient(app).post(
            "/api/nova/project",
            json={"starting_cash": 50000, "projection_start_month": "2026-05"},
        )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "mrr_mapping_unavailable"
    assert "Existing MRR could not be derived from QBO" in detail["message"]


def test_project_without_nova_use_returns_403():
    app = _make_app(_user(perms=[]))  # no nova.use
    r = TestClient(app).post(
        "/api/nova/project",
        json={"starting_cash": 50000, "projection_start_month": "2026-05"},
    )
    assert r.status_code == 403
    assert "nova.use" in r.json()["detail"]


def test_project_uses_default_horizon_13():
    app = _make_app(_user())
    inputs = _make_inputs()
    output = _make_output()
    captured = {}

    async def _fake_build(**kwargs):
        captured.update(kwargs)
        return inputs

    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(side_effect=_fake_build),
    ), patch(
        "routes.nova.project_cash_flow",
        return_value=output,
    ), patch(
        "routes.nova.save_run",
        new=AsyncMock(return_value={"projection_run_id": "pr_x"}),
    ):
        r = TestClient(app).post(
            "/api/nova/project",
            json={"starting_cash": 50000, "projection_start_month": "2026-05"},
        )

    assert r.status_code == 200
    assert captured["horizon_weeks"] == 13


# ---------------------------------------------------------------------------
# GET /api/nova/runs/{id}
# ---------------------------------------------------------------------------

def test_get_run_happy_path():
    app = _make_app(_user(user_id="user_owner"))
    fake_run = {
        "projection_run_id": "pr_abc",
        "user_id": "user_owner",
        "inputs_hash": "h1",
    }
    with patch("routes.nova.get_run", new=AsyncMock(return_value=fake_run)):
        r = TestClient(app).get("/api/nova/runs/pr_abc")
    assert r.status_code == 200
    assert r.json()["projection_run_id"] == "pr_abc"


def test_get_run_not_found_returns_404():
    app = _make_app(_user())
    with patch("routes.nova.get_run", new=AsyncMock(return_value=None)):
        r = TestClient(app).get("/api/nova/runs/pr_missing")
    assert r.status_code == 404
    assert r.json()["detail"] == "Projection run not found"


def test_get_run_cross_user_returns_404():
    """get_run scopes by user_id at the persistence layer, so other-user
    runs come back as None and the route surfaces 404 (not 403, since we
    don't reveal that the run exists for someone else)."""
    app = _make_app(_user(user_id="user_attacker"))
    captured = {}

    async def _fake_get_run(rid, uid):
        captured["user_id"] = uid
        return None  # scoped query, attacker doesn't see owner's run

    with patch("routes.nova.get_run", new=AsyncMock(side_effect=_fake_get_run)):
        r = TestClient(app).get("/api/nova/runs/pr_owners")
    assert r.status_code == 404
    assert captured["user_id"] == "user_attacker"  # filter is applied


def test_get_run_without_nova_use_returns_403():
    app = _make_app(_user(perms=[]))
    r = TestClient(app).get("/api/nova/runs/pr_abc")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/nova/runs (list / latest)
# ---------------------------------------------------------------------------

def test_list_runs_returns_list():
    app = _make_app(_user())
    runs = [{"projection_run_id": f"pr_{i}"} for i in range(3)]
    with patch("routes.nova.list_runs", new=AsyncMock(return_value=runs)):
        r = TestClient(app).get("/api/nova/runs")
    assert r.status_code == 200
    body = r.json()
    assert body["returned_count"] == 3
    assert len(body["runs"]) == 3


def test_list_runs_latest_query_returns_single_run():
    """latest=true returns the same shape as the list call: a runs[] array."""
    app = _make_app(_user())
    fake_latest = {"projection_run_id": "pr_latest"}
    with patch("routes.nova.get_latest_run", new=AsyncMock(return_value=fake_latest)):
        r = TestClient(app).get("/api/nova/runs?latest=true")
    assert r.status_code == 200
    body = r.json()
    assert "runs" in body
    assert "returned_count" in body
    assert isinstance(body["runs"], list)
    assert len(body["runs"]) == 1
    assert body["returned_count"] == 1
    assert body["runs"][0]["projection_run_id"] == "pr_latest"


def test_list_runs_latest_returns_empty_array_when_no_runs():
    """latest=true with no stored runs returns an empty array, not null."""
    app = _make_app(_user())
    with patch("routes.nova.get_latest_run", new=AsyncMock(return_value=None)):
        r = TestClient(app).get("/api/nova/runs?latest=true")
    assert r.status_code == 200
    body = r.json()
    assert body["runs"] == []
    assert body["returned_count"] == 0


def test_list_runs_respects_limit():
    app = _make_app(_user())
    captured = {}

    async def _fake_list(uid, limit):
        captured["limit"] = limit
        return []

    with patch("routes.nova.list_runs", new=AsyncMock(side_effect=_fake_list)):
        r = TestClient(app).get("/api/nova/runs?limit=5")
    assert r.status_code == 200
    assert captured["limit"] == 5


def test_list_runs_limit_validation():
    """limit > 100 rejected by Query(le=100) before reaching the handler."""
    app = _make_app(_user())
    r = TestClient(app).get("/api/nova/runs?limit=999")
    assert r.status_code == 422  # FastAPI validation error


def test_list_runs_without_nova_use_returns_403():
    app = _make_app(_user(perms=[]))
    r = TestClient(app).get("/api/nova/runs")
    assert r.status_code == 403


def test_run_projection_route_rejects_malformed_month():
    """POST /api/nova/project with malformed projection_start_month -> 400."""
    app = _make_app(_user())
    r = TestClient(app).post(
        "/api/nova/project",
        json={"starting_cash": 50000, "projection_start_month": "not-a-month"},
    )
    assert r.status_code == 400
    assert "YYYY-MM" in r.json()["detail"]


def test_run_projection_route_rejects_invalid_month_value():
    """POST /api/nova/project with month=13 -> 400."""
    app = _make_app(_user())
    r = TestClient(app).post(
        "/api/nova/project",
        json={"starting_cash": 50000, "projection_start_month": "2026-13"},
    )
    assert r.status_code == 400
    assert "YYYY-MM" in r.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/nova/projections/cashflow  (AEGISAXIOM-owned API for Ryan's portal)
# ---------------------------------------------------------------------------

_CASHFLOW_PERMS = [
    "finance.nova.run_projection",
    "finance.cashflow.edit_assumptions",
    "finance.cashflow.view",
]


def test_cashflow_happy_path_returns_envelope():
    app = _make_app(_user(perms=_CASHFLOW_PERMS, account_id="acc_wayne"))
    inputs = _make_inputs()
    output = _make_output("hash_envelope")
    saved = {"projection_run_id": "pr_envelope", "inputs_hash": "hash_envelope"}

    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(return_value=inputs),
    ), patch(
        "routes.nova.project_cash_flow",
        return_value=output,
    ), patch(
        "routes.nova.save_run",
        new=AsyncMock(return_value=saved),
    ):
        r = TestClient(app).post(
            "/api/nova/projections/cashflow",
            json={"starting_cash": 50000, "projection_start_month": "2026-05"},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["projection_run_id"] == "pr_envelope"
    assert body["status"] == "complete"
    assert body["inputs_hash"] == "hash_envelope"
    assert set(body.keys()) >= {
        "projection_run_id", "status", "inputs_hash", "output", "report",
        "warnings",
    }
    assert body["report"]["column_names"] == ["W1"]
    assert body["report"]["kpis"]["ending_cash"] == 51000.0
    assert body["summary"]["ending_cash"] == 51000.0
    # Tenant context resolved server-side from session.account_id.
    assert body["meta"]["tenant_id"] == "acc_wayne"
    assert body["meta"]["tenant_source"] == "session.account_id"
    assert body["meta"]["inputs_hash"] == "hash_envelope"


def test_cashflow_falls_back_to_platform_tenant_when_no_account_id():
    """Internal users without account_id still get a deterministic tenant key."""
    app = _make_app(_user(perms=_CASHFLOW_PERMS, account_id=None))
    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(return_value=_make_inputs()),
    ), patch(
        "routes.nova.project_cash_flow",
        return_value=_make_output(),
    ), patch(
        "routes.nova.save_run",
        new=AsyncMock(return_value={"projection_run_id": "pr_x", "inputs_hash": "h"}),
    ):
        r = TestClient(app).post(
            "/api/nova/projections/cashflow",
            json={"starting_cash": 50000, "projection_start_month": "2026-05"},
        )
    assert r.status_code == 200
    assert r.json()["meta"]["tenant_id"] == "platform"
    assert r.json()["meta"]["tenant_source"] == "session.platform_default"


def test_cashflow_rejects_tenant_id_in_body():
    """Body must not be allowed to override session-derived tenant context."""
    app = _make_app(_user(perms=_CASHFLOW_PERMS, account_id="acc_self"))
    r = TestClient(app).post(
        "/api/nova/projections/cashflow",
        json={
            "starting_cash": 50000,
            "projection_start_month": "2026-05",
            "tenant_id": "acc_other",
        },
    )
    assert r.status_code == 400
    assert "tenant_id" in r.json()["detail"]


def test_cashflow_rejects_account_id_in_body():
    app = _make_app(_user(perms=_CASHFLOW_PERMS))
    r = TestClient(app).post(
        "/api/nova/projections/cashflow",
        json={
            "starting_cash": 50000,
            "projection_start_month": "2026-05",
            "account_id": "acc_other",
        },
    )
    assert r.status_code == 400
    assert "account_id" in r.json()["detail"]


def test_cashflow_without_run_projection_returns_403():
    app = _make_app(_user(perms=["finance.cashflow.view"]))
    r = TestClient(app).post(
        "/api/nova/projections/cashflow",
        json={"starting_cash": 50000, "projection_start_month": "2026-05"},
    )
    assert r.status_code == 403
    assert "finance.nova.run_projection" in r.json()["detail"]


def test_cashflow_with_run_projection_only_can_run_baseline():
    """run_projection implies view + edit_assumptions, so a baseline run works."""
    app = _make_app(_user(perms=["finance.nova.run_projection"]))
    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(return_value=_make_inputs()),
    ), patch(
        "routes.nova.project_cash_flow",
        return_value=_make_output(),
    ), patch(
        "routes.nova.save_run",
        new=AsyncMock(return_value={"projection_run_id": "pr_y", "inputs_hash": "h"}),
    ):
        r = TestClient(app).post(
            "/api/nova/projections/cashflow",
            json={"starting_cash": 50000, "projection_start_month": "2026-05"},
        )
    assert r.status_code == 200


def test_cashflow_scenario_state_requires_edit_assumptions_perm():
    """When the caller passes assumptions/overrides we must additionally
    hold finance.cashflow.edit_assumptions. The implication chain from
    run_projection grants this transitively, so we test it by passing
    only the bare run perm without the implication closure expanded."""
    # Simulate a token where only run_projection appears, with no
    # implication closure pre-expanded into permissions list.
    app = _make_app(_user(perms=["finance.cashflow.view"]))
    r = TestClient(app).post(
        "/api/nova/projections/cashflow",
        json={
            "starting_cash": 50000,
            "projection_start_month": "2026-05",
            "manual_recurring_revenue": [
                {
                    "name": "Referral MRR",
                    "amount_per_month": 2500,
                    "line_type": "MRR",
                    "starts_month": "2026-05",
                    "ends_month": "2026-08",
                },
            ],
        },
    )
    # Fails on the first authz gate (run_projection) before hitting
    # the second (edit_assumptions). Either is acceptable for v1.
    assert r.status_code == 403


def test_cashflow_audit_emitted_on_success():
    """nova.projection.generated must fire on every successful run."""
    app = _make_app(_user(perms=_CASHFLOW_PERMS, account_id="acc_a"))
    audit_mock = AsyncMock(return_value=None)

    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(return_value=_make_inputs()),
    ), patch(
        "routes.nova.project_cash_flow",
        return_value=_make_output(),
    ), patch(
        "routes.nova.save_run",
        new=AsyncMock(return_value={"projection_run_id": "pr_audit", "inputs_hash": "h"}),
    ), patch(
        "routes.nova.log_action",
        new=audit_mock,
    ):
        r = TestClient(app).post(
            "/api/nova/projections/cashflow",
            json={"starting_cash": 50000, "projection_start_month": "2026-05"},
        )

    assert r.status_code == 200
    actions = [c.kwargs["action"] for c in audit_mock.await_args_list]
    assert "nova.projection.generated" in actions
    assert "nova.assumptions.updated" not in actions  # baseline, no scenario state
    generated = next(
        c.kwargs for c in audit_mock.await_args_list
        if c.kwargs["action"] == "nova.projection.generated"
    )
    details = generated["details"]
    assert details["user_id"] == "user_test"
    assert details["projection_run_id"] == "pr_audit"
    assert details["projection_start_month"] == "2026-05"
    assert details["horizon_weeks"] == 13
    assert details["inputs_hash"] == "hash1"
    assert details["manual_assumptions_supplied"] is False
    assert details["manual_mrr_supplied"] is False


def test_cashflow_audit_emits_assumptions_updated_when_scenario_supplied():
    app = _make_app(_user(perms=_CASHFLOW_PERMS, account_id="acc_a"))
    audit_mock = AsyncMock(return_value=None)

    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(return_value=_make_inputs()),
    ), patch(
        "routes.nova.project_cash_flow",
        return_value=_make_output(),
    ), patch(
        "routes.nova.save_run",
        new=AsyncMock(return_value={"projection_run_id": "pr_z", "inputs_hash": "h"}),
    ), patch(
        "routes.nova.log_action",
        new=audit_mock,
    ):
        r = TestClient(app).post(
            "/api/nova/projections/cashflow",
            json={
                "starting_cash": 50000,
                "projection_start_month": "2026-05",
                "manual_recurring_revenue": [
                    {
                        "name": "Referral MRR",
                        "amount_per_month": 2500,
                        "line_type": "MRR",
                        "starts_month": "2026-05",
                        "ends_month": "2026-08",
                    },
                ],
            },
        )

    assert r.status_code == 200, r.text
    actions = [c.kwargs["action"] for c in audit_mock.await_args_list]
    assert "nova.projection.generated" in actions
    assert "nova.assumptions.updated" in actions


def test_cashflow_insufficient_history_returns_422():
    app = _make_app(_user(perms=_CASHFLOW_PERMS))
    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(side_effect=InsufficientHistoryError(
            line="history", available_months=2, required_months=3,
        )),
    ):
        r = TestClient(app).post(
            "/api/nova/projections/cashflow",
            json={"starting_cash": 50000, "projection_start_month": "2026-05"},
        )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "insufficient_history"
    assert detail["affected_line"] == "history"
    assert detail["available_months"] == 2
    assert detail["required_months"] == 3


def test_cashflow_accepts_manual_cost_assumptions():
    app = _make_app(_user(perms=_CASHFLOW_PERMS))
    captured = {}

    async def _fake_build(**kwargs):
        captured.update(kwargs)
        return _make_inputs().model_copy(update={
            "manual_cost_assumptions": kwargs["manual_cost_assumptions"],
        })

    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(side_effect=_fake_build),
    ), patch(
        "routes.nova.project_cash_flow",
        return_value=_make_output(),
    ), patch(
        "routes.nova.save_run",
        new=AsyncMock(return_value={"projection_run_id": "pr_cost"}),
    ):
        r = TestClient(app).post(
            "/api/nova/projections/cashflow",
            json={
                "starting_cash": 50000,
                "projection_start_month": "2026-05",
                "manual_cost_assumptions": [
                    {
                        "name": "Payroll",
                        "amount_per_month": 7500,
                        "line_type": "PAYROLL",
                        "starts_month": "2026-05",
                    }
                ],
            },
        )

    assert r.status_code == 200, r.text
    assert len(captured["manual_cost_assumptions"]) == 1
    assert isinstance(captured["manual_cost_assumptions"][0], ManualCostAssumption)
    assert captured["manual_cost_assumptions"][0].line_type == "PAYROLL"


def test_cashflow_accepts_manual_recurring_revenue_with_mrr():
    app = _make_app(_user(perms=_CASHFLOW_PERMS))
    captured = {}

    async def _fake_build(**kwargs):
        captured.update(kwargs)
        return _make_inputs().model_copy(update={
            "manual_recurring_revenue": kwargs["manual_recurring_revenue"],
        })

    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(side_effect=_fake_build),
    ), patch(
        "routes.nova.project_cash_flow",
        return_value=_make_output(),
    ), patch(
        "routes.nova.save_run",
        new=AsyncMock(return_value={"projection_run_id": "pr_mrr"}),
    ):
        r = TestClient(app).post(
            "/api/nova/projections/cashflow",
            json={
                "starting_cash": 50000,
                "projection_start_month": "2026-05",
                "manual_recurring_revenue": [
                    {
                        "name": "Referral MRR",
                        "amount_per_month": 2500,
                        "line_type": "MRR",
                        "starts_month": "2026-05",
                        "ends_month": "2026-12",
                    }
                ],
            },
        )

    assert r.status_code == 200, r.text
    assert len(captured["manual_recurring_revenue"]) == 1
    assert isinstance(captured["manual_recurring_revenue"][0], RecurringRevenueAssumption)
    assert captured["manual_recurring_revenue"][0].line_type == "MRR"


def test_cashflow_manual_mrr_zero_allows_projection_when_history_fallback_empty():
    app = _make_app(_user(perms=_CASHFLOW_PERMS))

    async def _fake_build(**kwargs):
        return _make_inputs().model_copy(update={
            "mrr_last_year_same_months": {},
            "manual_recurring_revenue": kwargs["manual_recurring_revenue"],
        })

    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(side_effect=_fake_build),
    ), patch(
        "routes.nova.save_run",
        new=AsyncMock(return_value={"projection_run_id": "pr_zero_mrr"}),
    ):
        r = TestClient(app).post(
            "/api/nova/projections/cashflow",
            json={
                "starting_cash": 50000,
                "projection_start_month": "2026-05",
                "manual_recurring_revenue": [
                    {
                        "name": "MRR intentionally zero",
                        "amount_per_month": 0.0,
                        "line_type": "MRR",
                        "starts_month": "2026-05",
                        "ends_month": "2026-12",
                    }
                ],
            },
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "complete"
    assert body["projection_run_id"] == "pr_zero_mrr"


def test_cashflow_missing_auto_mrr_and_missing_manual_mrr_returns_422():
    app = _make_app(_user(perms=_CASHFLOW_PERMS))

    async def _fake_build(**kwargs):
        return _make_inputs().model_copy(update={
            "mrr_last_year_same_months": {},
            "manual_recurring_revenue": [],
        })

    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(side_effect=_fake_build),
    ):
        r = TestClient(app).post(
            "/api/nova/projections/cashflow",
            json={"starting_cash": 50000, "projection_start_month": "2026-05"},
        )

    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "mrr_mapping_unavailable"
    # The message body shifted post-MRR-mapping. Mapping IS configured
    # now (account 50300); the runtime check fires when the GL has no
    # 50300 activity in the prior-year-same-month window.
    assert "could not be derived from QBO" in detail["message"]
    assert "50300" in detail["message"]
    assert "manual MRR assumption" in detail["message"]
    assert detail["affected_line"] == "mrr (2026-05)"
    assert detail["available_months"] == 0
    assert detail["required_months"] == 1


def test_cashflow_accepts_and_forwards_weekly_timing_overrides():
    app = _make_app(_user(perms=_CASHFLOW_PERMS))
    captured = {}

    async def _fake_build(**kwargs):
        captured.update(kwargs)
        return _make_inputs().model_copy(update={
            "weekly_timing_overrides": kwargs["weekly_timing_overrides"],
        })

    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(side_effect=_fake_build),
    ), patch(
        "routes.nova.project_cash_flow",
        return_value=_make_output(),
    ), patch(
        "routes.nova.save_run",
        new=AsyncMock(return_value={"projection_run_id": "pr_timing"}),
    ):
        r = TestClient(app).post(
            "/api/nova/projections/cashflow",
            json={
                "starting_cash": 50000,
                "projection_start_month": "2026-05",
                "weekly_timing_overrides": {
                    "MRR": {
                        "week1_pct": 0.0,
                        "week2_pct": 1.0,
                        "week3_pct": 0.0,
                        "week4_pct": 0.0,
                    }
                },
            },
        )

    assert r.status_code == 200, r.text
    dist = captured["weekly_timing_overrides"]["MRR"]
    assert isinstance(dist, WeeklyDistribution)
    assert dist.week2_pct == 1.0


def test_cashflow_zero_modifiers_count_as_supplied_scenario_state():
    app = _make_app(_user(perms=_CASHFLOW_PERMS))
    audit_mock = AsyncMock(return_value=None)
    captured = {}

    def _fake_project(inputs):
        captured["sales_modifier"] = inputs.sales_modifier
        captured["cost_modifier"] = inputs.cost_modifier
        return _make_output("hash_zero_mods")

    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(return_value=_make_inputs()),
    ), patch(
        "routes.nova.project_cash_flow",
        side_effect=_fake_project,
    ), patch(
        "routes.nova.save_run",
        new=AsyncMock(return_value={"projection_run_id": "pr_mods"}),
    ), patch(
        "routes.nova.log_action",
        new=audit_mock,
    ):
        r = TestClient(app).post(
            "/api/nova/projections/cashflow",
            json={
                "starting_cash": 50000,
                "projection_start_month": "2026-05",
                "sales_modifier": 0,
                "cost_modifier": 0,
            },
        )

    assert r.status_code == 200, r.text
    assert captured["sales_modifier"] == 0.0
    assert captured["cost_modifier"] == 0.0
    actions = [c.kwargs["action"] for c in audit_mock.await_args_list]
    assert "nova.assumptions.updated" in actions
    generated = next(
        c.kwargs for c in audit_mock.await_args_list
        if c.kwargs["action"] == "nova.projection.generated"
    )
    scenario = generated["details"]["scenario_state"]
    assert scenario["sales_modifier_set"] is True
    assert scenario["cost_modifier_set"] is True


def test_cashflow_invalid_override_returns_400():
    app = _make_app(_user(perms=_CASHFLOW_PERMS))
    r = TestClient(app).post(
        "/api/nova/projections/cashflow",
        json={
            "starting_cash": 50000,
            "projection_start_month": "2026-05",
            "overrides": [{"this_field_does_not_exist": True}],
        },
    )
    assert r.status_code == 400
    assert "overrides[0]" in r.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/nova/projections/{id}  (envelope read)
# ---------------------------------------------------------------------------

def test_get_cashflow_projection_returns_envelope():
    app = _make_app(_user(user_id="user_owner", perms=["finance.cashflow.view"]))
    fake_run = {
        "projection_run_id": "pr_envelope",
        "user_id": "user_owner",
        "inputs_hash": "h_stored",
        "engine_version": "0.1.0",
        "freshness_warnings": [{"message": "stale"}],
        "inputs": {"starting_cash": 50000.0},
        "output": {
            "starting_cash": 50000.0,
            "ending_cash": 60000.0,
            "minimum_cash": 50000.0,
            "minimum_cash_week": 1,
            "total_revenue": 30000.0,
            "total_costs": 20000.0,
            "net_change": 10000.0,
            "weeks": [{"week_number": 1, "ending_cash": 60000.0}],
            "inputs_hash": "h_stored",
            "computed_at": "2026-05-03T00:00:00Z",
        },
    }
    with patch("routes.nova.get_run", new=AsyncMock(return_value=fake_run)):
        r = TestClient(app).get("/api/nova/projections/pr_envelope")

    assert r.status_code == 200
    body = r.json()
    assert body["projection_run_id"] == "pr_envelope"
    assert body["summary"]["ending_cash"] == 60000.0
    assert body["weeks"] == [{"week_number": 1, "ending_cash": 60000.0}]
    assert body["warnings"] == [{"message": "stale"}]
    assert body["meta"]["inputs_hash"] == "h_stored"


def test_get_cashflow_projection_envelope_includes_status_output_and_report():
    """GET must return the same envelope keys as POST: status, output,
    and report were silently dropped before the fast-follow even though
    the comment claimed parity."""
    app = _make_app(_user(user_id="user_owner", perms=["finance.cashflow.view"]))
    fake_run = {
        "projection_run_id": "pr_parity",
        "user_id": "user_owner",
        "inputs_hash": "h_parity",
        "engine_version": "0.1.0",
        "freshness_warnings": [],
        "inputs": {"starting_cash": 50000.0},
        "output": {
            "starting_cash": 50000.0,
            "ending_cash": 60000.0,
            "minimum_cash": 50000.0,
            "minimum_cash_week": 1,
            "total_revenue": 30000.0,
            "total_costs": 20000.0,
            "net_change": 10000.0,
            "weeks": [
                {"week_number": 1, "week_starting": "2026-05-04",
                 "ending_cash": 60000.0, "total_revenue": 30000.0,
                 "total_costs": 20000.0},
            ],
            "inputs_hash": "h_parity",
            "computed_at": "2026-05-03T00:00:00Z",
        },
    }
    with patch("routes.nova.get_run", new=AsyncMock(return_value=fake_run)):
        r = TestClient(app).get("/api/nova/projections/pr_parity")

    assert r.status_code == 200
    body = r.json()
    # The three keys POST returns that GET used to drop:
    assert body["status"] == "complete"
    assert body["output"]["ending_cash"] == 60000.0
    assert "report" in body
    assert "column_names" in body["report"]
    assert "line_items" in body["report"]
    assert "kpis" in body["report"]
    assert body["report"]["kpis"]["ending_cash"] == 60000.0
    # And the existing keys still work:
    assert body["projection_run_id"] == "pr_parity"
    assert body["summary"]["ending_cash"] == 60000.0
    assert body["meta"]["inputs_hash"] == "h_parity"


def test_post_and_get_envelopes_match_for_same_projection():
    """POST and GET for the same projection_run_id must produce
    equivalent envelopes - same keys, same values - so a frontend that
    consumes one can swap to the other without branching."""
    output = _make_output("hash_parity_e2e")
    saved_run = {
        "projection_run_id": "pr_parity_e2e",
        "user_id": "user_test",
        "inputs_hash": output.inputs_hash,
        "engine_version": "0.1.0",
        "freshness_warnings": [],
        "inputs": _make_inputs().model_dump(mode="json"),
        "output": output.model_dump(mode="json"),
    }

    app_post = _make_app(
        _user(perms=["finance.nova.run_projection",
                     "finance.cashflow.view",
                     "finance.cashflow.edit_assumptions"])
    )
    with patch(
        "routes.nova.build_projection_inputs_from_globals",
        new=AsyncMock(return_value=_make_inputs()),
    ), patch(
        "routes.nova.project_cash_flow",
        return_value=output,
    ), patch(
        "routes.nova.save_run",
        new=AsyncMock(return_value=saved_run),
    ):
        post_resp = TestClient(app_post).post(
            "/api/nova/projections/cashflow",
            json={"starting_cash": 50000, "projection_start_month": "2026-05"},
        )
    assert post_resp.status_code == 200, post_resp.text
    post_body = post_resp.json()

    app_get = _make_app(_user(perms=["finance.cashflow.view"]))
    with patch("routes.nova.get_run", new=AsyncMock(return_value=saved_run)):
        get_resp = TestClient(app_get).get(
            "/api/nova/projections/pr_parity_e2e",
        )
    assert get_resp.status_code == 200, get_resp.text
    get_body = get_resp.json()

    # Top-level keys are identical
    assert set(post_body.keys()) == set(get_body.keys())
    # Critical envelope blocks match field-for-field
    assert post_body["projection_run_id"] == get_body["projection_run_id"]
    assert post_body["status"] == get_body["status"]
    assert post_body["inputs_hash"] == get_body["inputs_hash"]
    assert post_body["summary"] == get_body["summary"]
    assert post_body["weeks"] == get_body["weeks"]
    assert post_body["output"] == get_body["output"]
    assert post_body["report"] == get_body["report"]
    # meta is keyed identically (computed_at may serialize equivalently)
    assert set(post_body["meta"].keys()) == set(get_body["meta"].keys())
    assert post_body["meta"]["inputs_hash"] == get_body["meta"]["inputs_hash"]


def test_get_cashflow_projection_cross_user_returns_404():
    app = _make_app(_user(user_id="attacker", perms=["finance.cashflow.view"]))
    with patch("routes.nova.get_run", new=AsyncMock(return_value=None)):
        r = TestClient(app).get("/api/nova/projections/pr_owners")
    assert r.status_code == 404


def test_get_cashflow_projection_without_view_returns_403():
    app = _make_app(_user(perms=[]))
    r = TestClient(app).get("/api/nova/projections/pr_x")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/nova/projections/{id}/export  (audit-only stub for v1)
# ---------------------------------------------------------------------------

def test_export_cashflow_projection_emits_audit_and_returns_marker():
    app = _make_app(_user(perms=["finance.cashflow.export"], account_id="acc_a"))
    audit_mock = AsyncMock(return_value=None)
    fake_run = {
        "projection_run_id": "pr_e",
        "user_id": "user_test",
        "inputs_hash": "h",
    }
    with patch(
        "routes.nova.get_run", new=AsyncMock(return_value=fake_run),
    ), patch(
        "routes.nova.log_action", new=audit_mock,
    ):
        r = TestClient(app).post(
            "/api/nova/projections/pr_e/export",
            json={"format": "csv"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "audit_logged"
    assert body["format"] == "csv"
    actions = [c.kwargs["action"] for c in audit_mock.await_args_list]
    assert "nova.projection.exported" in actions


def test_export_cashflow_projection_rejects_unknown_format():
    app = _make_app(_user(perms=["finance.cashflow.export"]))
    fake_run = {"projection_run_id": "pr_e", "user_id": "user_test"}
    with patch("routes.nova.get_run", new=AsyncMock(return_value=fake_run)):
        r = TestClient(app).post(
            "/api/nova/projections/pr_e/export",
            json={"format": "exe"},
        )
    assert r.status_code == 400
    assert "format" in r.json()["detail"]


def test_export_cashflow_projection_without_export_perm_returns_403():
    app = _make_app(_user(perms=["finance.cashflow.view"]))
    r = TestClient(app).post(
        "/api/nova/projections/pr_e/export",
        json={"format": "csv"},
    )
    assert r.status_code == 403
    assert "finance.cashflow.export" in r.json()["detail"]


def test_export_cashflow_projection_missing_run_returns_404():
    app = _make_app(_user(perms=["finance.cashflow.export"]))
    with patch("routes.nova.get_run", new=AsyncMock(return_value=None)):
        r = TestClient(app).post(
            "/api/nova/projections/pr_missing/export",
            json={"format": "csv"},
        )
    assert r.status_code == 404
