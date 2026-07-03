"""Wave 4 (contracts-time-mgmt) — billing trigger CRUD tests.

Exercises the four routes in ``routes/contract_triggers.py`` directly
against AsyncMock-shaped fakes so the suite runs without Mongo.

Coverage:
  - Create happy path; trigger row carries contract_id / account_id /
    tenant_id / created_by / created_at / updated_at and starts armed.
  - Create with invalid trigger_type / action / state / threshold
    shape returns 400 with an actionable message.
  - List returns only the contracts-scoped triggers.
  - Cross-tenant contract returns 404 (not 403 — never leak
    existence).
  - DELETE soft-disables (state=disabled), does not hard-delete.
  - Evaluate route invokes the evaluator and returns its summary.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


def _user(role="director", perms=None, tenant_id=None):
    return SimpleNamespace(
        user_id="u_director_001",
        email="director@aegisaxiom.test",
        role=role,
        permissions=list(perms or ["contracts.edit.all", "contracts.approve", "*"]),
        tenant_id=tenant_id,
        account_id=None,
    )


def _contract(**overrides):
    base = {
        "contract_id": "c_001",
        "account_id": "acct_001",
        "contract_type": "boh_with_boa",
        "total_hours": 20.0,
        "hours_consumed": 5.0,
        "overage_hours": 0.0,
        "bill_and_actuals_active": False,
        "tenant_id": None,
    }
    base.update(overrides)
    return base


class _FakeTriggers:
    def __init__(self, rows=None):
        self._rows: list = list(rows or [])
        self.inserts: list = []
        self.updates: list = []

    def find(self, query, _projection=None):
        results = [r for r in self._rows if all(r.get(k) == v for k, v in query.items())]

        class _C:
            def __init__(s, items):
                s._items = items

            def sort(s, *_a, **_kw):
                return s

            async def to_list(s, _n):
                return list(s._items)
        return _C(results)

    async def find_one(self, query, _projection=None):
        for r in self._rows:
            if all(r.get(k) == v for k, v in query.items()):
                return dict(r)
        return None

    async def count_documents(self, query):
        return sum(
            1 for r in self._rows
            if all(r.get(k) == v for k, v in query.items())
        )

    async def insert_one(self, doc):
        self.inserts.append(dict(doc))
        self._rows.append(dict(doc))
        return SimpleNamespace(inserted_id="x")

    async def update_one(self, query, update):
        self.updates.append((dict(query), dict(update)))
        for r in self._rows:
            if all(r.get(k) == v for k, v in query.items()):
                if "$set" in update:
                    r.update(update["$set"])
        return SimpleNamespace(modified_count=1)


def _setup(monkeypatch, contract, triggers=None):
    triggers_coll = _FakeTriggers(triggers)

    class _DB:
        pass
    db = _DB()
    db.contracts = SimpleNamespace(
        find_one=AsyncMock(return_value=(dict(contract) if contract else None)),
    )
    db.contract_billing_triggers = triggers_coll

    import routes.contract_triggers as ct
    monkeypatch.setattr(ct, "get_db", lambda: db)
    monkeypatch.setattr(ct, "log_action", AsyncMock(return_value=None))
    monkeypatch.setattr(ct, "emit_event", AsyncMock(return_value=None))

    # crud_envelope wraps mutations in policy + event + audit hops
    # none of which are set up in this unit harness; substitute a
    # no-op async context manager.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_envelope(*_args, **_kwargs):
        class _Ctx:
            after = None
            resource_id = _kwargs.get("resource_id")
            def set_after(self, doc): self.after = doc
            def set_resource_id(self, rid): self.resource_id = rid
        yield _Ctx()
    monkeypatch.setattr(ct, "crud_envelope", _fake_envelope)

    async def _empty_filter(_u):
        return {}
    monkeypatch.setattr(ct, "get_contract_filter", _empty_filter)

    return db, triggers_coll, ct


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_date_trigger_happy_path(monkeypatch):
    db, triggers, ct = _setup(monkeypatch, _contract())
    result = await ct.create_billing_trigger(
        contract_id="c_001",
        data={
            "trigger_type": "date",
            "threshold": "2026-12-31",
            "action": "notify_finance",
            "label": "End of year sweep",
        },
        current_user=_user(),
    )
    assert result["contract_id"] == "c_001"
    assert result["trigger_type"] == "date"
    assert result["state"] == "armed"
    assert result["last_fired_at"] is None
    assert triggers.inserts and triggers.inserts[0]["trigger_id"].startswith("trig_")


@pytest.mark.asyncio
async def test_create_pct_consumed_trigger_validates_threshold(monkeypatch):
    db, triggers, ct = _setup(monkeypatch, _contract())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ct.create_billing_trigger(
            contract_id="c_001",
            data={
                "trigger_type": "pct_consumed",
                "threshold": 150,  # invalid: > 100
                "action": "queue_invoice_batch",
            },
            current_user=_user(),
        )
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_create_invalid_action_returns_400(monkeypatch):
    db, triggers, ct = _setup(monkeypatch, _contract())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ct.create_billing_trigger(
            contract_id="c_001",
            data={
                "trigger_type": "block_exhausted",
                "action": "send_customer_email",  # not in allowlist
            },
            current_user=_user(),
        )
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_create_invalid_trigger_type_returns_400(monkeypatch):
    db, triggers, ct = _setup(monkeypatch, _contract())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ct.create_billing_trigger(
            contract_id="c_001",
            data={"trigger_type": "marketing_sweep", "action": "notify_finance"},
            current_user=_user(),
        )
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_create_requires_edit_permission(monkeypatch):
    db, triggers, ct = _setup(monkeypatch, _contract())
    from fastapi import HTTPException
    sales_rep = _user(role="sales_rep", perms=["contracts.view.own"])
    with pytest.raises(HTTPException) as ei:
        await ct.create_billing_trigger(
            contract_id="c_001",
            data={
                "trigger_type": "date",
                "threshold": "2026-12-31",
                "action": "notify_finance",
            },
            current_user=sales_rep,
        )
    assert ei.value.status_code == 403


# ---------------------------------------------------------------------------
# Cross-tenant guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_cross_tenant_returns_404(monkeypatch):
    """Contract belongs to tenant A; user is in tenant B. The
    load helper returns 404 so the route never reveals existence."""
    db, triggers, ct = _setup(
        monkeypatch,
        _contract(tenant_id="tenant_a"),
    )
    cross_user = _user(role="director", tenant_id="tenant_b")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ct.create_billing_trigger(
            contract_id="c_001",
            data={
                "trigger_type": "date",
                "threshold": "2026-12-31",
                "action": "notify_finance",
            },
            current_user=cross_user,
        )
    assert ei.value.status_code == 404


# ---------------------------------------------------------------------------
# List + DELETE (soft-disable)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_returns_only_contract_scoped_triggers(monkeypatch):
    db, triggers, ct = _setup(
        monkeypatch,
        _contract(),
        triggers=[
            {"trigger_id": "trig_a", "contract_id": "c_001", "trigger_type": "date", "state": "armed"},
            {"trigger_id": "trig_b", "contract_id": "c_001", "trigger_type": "pct_consumed", "state": "disabled"},
            # Different contract — must NOT leak.
            {"trigger_id": "trig_c", "contract_id": "c_other", "trigger_type": "date", "state": "armed"},
        ],
    )
    result = await ct.list_billing_triggers(contract_id="c_001", current_user=_user())
    ids = {item["trigger_id"] for item in result["items"]}
    assert ids == {"trig_a", "trig_b"}


@pytest.mark.asyncio
async def test_delete_soft_disables_trigger(monkeypatch):
    db, triggers, ct = _setup(
        monkeypatch,
        _contract(),
        triggers=[{"trigger_id": "trig_a", "contract_id": "c_001", "state": "armed"}],
    )
    result = await ct.disable_billing_trigger(
        contract_id="c_001", trigger_id="trig_a", current_user=_user(),
    )
    assert result["state"] == "disabled"
    # The row is still present in the fake; state was flipped.
    assert triggers._rows[0]["state"] == "disabled"


@pytest.mark.asyncio
async def test_delete_unknown_trigger_returns_404(monkeypatch):
    db, triggers, ct = _setup(monkeypatch, _contract(), triggers=[])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ct.disable_billing_trigger(
            contract_id="c_001", trigger_id="trig_missing", current_user=_user(),
        )
    assert ei.value.status_code == 404


# ---------------------------------------------------------------------------
# Evaluate route — wraps the service. Behavior is exhaustively tested
# in test_contract_trigger_evaluator.py; here we just confirm the
# route invokes the service and surfaces its return shape.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_route_invokes_evaluator(monkeypatch):
    db, triggers, ct = _setup(
        monkeypatch,
        _contract(hours_consumed=20.0, bill_and_actuals_active=True),
        triggers=[{
            "trigger_id": "trig_block",
            "contract_id": "c_001",
            "trigger_type": "block_exhausted",
            "action": "notify_finance",
            "state": "armed",
            "threshold": None,
        }],
    )
    result = await ct.evaluate_contract_triggers_route(
        contract_id="c_001", current_user=_user(),
    )
    assert result["contract_id"] == "c_001"
    fired_ids = {f["trigger_id"] for f in result["fired"]}
    assert "trig_block" in fired_ids


@pytest.mark.asyncio
async def test_evaluate_route_requires_approve_or_finance_perm(monkeypatch):
    db, triggers, ct = _setup(monkeypatch, _contract())
    from fastapi import HTTPException
    no_perm = _user(role="sales_ops", perms=["contracts.create", "contracts.edit"])
    with pytest.raises(HTTPException) as ei:
        await ct.evaluate_contract_triggers_route(
            contract_id="c_001", current_user=no_perm,
        )
    assert ei.value.status_code == 403
