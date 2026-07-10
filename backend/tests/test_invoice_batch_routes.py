"""Wave 5 (contracts-time-mgmt) — invoice batch route tests.

Covers the four endpoints in ``routes/invoice_batches.py``:
  POST /invoice-batches/build
  GET  /invoice-batches  /  GET /invoice-batches/{batch_id}
  POST /invoice-batches/{batch_id}/approve
  POST /invoice-batches/{batch_id}/reject

Locks:
  - Permission gating (finance.portal / contracts.approve / *).
  - Build delegates to the service and surfaces the result.
  - List supports status / contract_id / period_start filters and
    is tenant-scoped.
  - Approve creates a finance_drafts AR row, sets
    linked_finance_draft_id, flips status to approved.
  - Re-approve is idempotent and returns already_approved=True.
  - Reject moves status to rejected; does not touch source entries.
  - Re-reject is idempotent.
  - Cross-tenant batch returns 404.
  - Approve / reject require notes for reject; reject 409s from
    bad source state.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


def _user(role="finance", perms=None, tenant_id=None):
    return SimpleNamespace(
        user_id="u_finance",
        email="finance@aegisaxiom.test",
        role=role,
        permissions=list(perms or ["finance.portal", "contracts.approve", "*"]),
        tenant_id=tenant_id,
        account_id=None,
    )


def _batch(**overrides):
    base = {
        "batch_id": "ibatch_001",
        "contract_id": "c_001",
        "account_id": "acct_001",
        "account_name": "Acme",
        "contract_name": "Acme Engagement",
        "period_start": "2026-05-18",
        "period_end": "2026-05-31",
        "status": "pending_finance_approval",
        "total_hours": 6.0,
        "total_amount": 1080.0,
        "line_items": [
            {"entry_id": "te_a", "user_id": "u_eng", "user_name": "Eng One",
             "date": "2026-05-18", "hours": 4.0, "sell_rate_snapshot": 180.0,
             "amount": 720.0, "description": "Eng One Sprint work (2026-05-18)"},
            {"entry_id": "te_b", "user_id": "u_pm", "user_name": "PM",
             "date": "2026-05-19", "hours": 2.0, "sell_rate_snapshot": 180.0,
             "amount": 360.0, "description": "PM Planning (2026-05-19)"},
        ],
        "entry_ids": ["te_a", "te_b"],
        "linked_finance_draft_id": None,
        "decided_by": None,
        "decided_at": None,
        "decision_notes": None,
        "tenant_id": None,
    }
    base.update(overrides)
    return base


class _Batches:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.updates: List = []

    def find(self, query, _projection=None):
        results = []
        for r in self._rows:
            ok = True
            for k, v in query.items():
                if k == "$or":
                    matches_or = False
                    for clause in v:
                        clause_ok = True
                        for ck, cv in clause.items():
                            if isinstance(cv, dict):
                                if "$exists" in cv:
                                    if cv["$exists"] is False and ck in r:
                                        clause_ok = False
                                        break
                                    if cv["$exists"] is True and ck not in r:
                                        clause_ok = False
                                        break
                            elif r.get(ck) != cv:
                                clause_ok = False
                                break
                        if clause_ok:
                            matches_or = True
                            break
                    if not matches_or:
                        ok = False
                        break
                elif r.get(k) != v:
                    ok = False
                    break
            if ok:
                results.append(dict(r))

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
        # Reuse the same filter logic as ``find`` so list-route tests
        # that assert ``total`` see the same matcher behavior.
        return len(self.find(query)._items)

    async def update_one(self, query, update):
        self.updates.append((dict(query), dict(update)))
        for r in self._rows:
            if all(r.get(k) == v for k, v in query.items()):
                if "$set" in update:
                    r.update(update["$set"])
        return SimpleNamespace(modified_count=1)

    async def insert_one(self, doc):
        self._rows.append(dict(doc))
        return SimpleNamespace(inserted_id="x")


class _FinanceDrafts:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.inserts: List[Dict[str, Any]] = []

    async def find_one(self, query, _projection=None):
        for r in self._rows:
            if all(r.get(k) == v for k, v in query.items()):
                return dict(r)
        return None

    async def insert_one(self, doc):
        self.inserts.append(dict(doc))
        self._rows.append(dict(doc))
        return SimpleNamespace(inserted_id="x")


def _setup(monkeypatch, *, batches=None, drafts=None, builder_stub=None):
    batches_coll = _Batches(batches)
    drafts_coll = _FinanceDrafts(drafts)

    class _DB:
        pass
    db = _DB()
    db.invoice_batches = batches_coll
    db.finance_drafts = drafts_coll

    import routes.invoice_batches as ib
    monkeypatch.setattr(ib, "get_db", lambda: db)
    monkeypatch.setattr(ib, "log_action", AsyncMock(return_value=None))
    monkeypatch.setattr(ib, "emit_event", AsyncMock(return_value=None))

    # The route helpers now wrap state-changing ops in crud_envelope,
    # which calls into policy_engine + event_bus + audit. None of those
    # systems are set up in this unit-test harness; substitute the
    # envelope with a no-op async context manager whose ctx exposes
    # the same set_after / set_resource_id surface.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_envelope(*_args, **_kwargs):
        class _Ctx:
            after = None
            resource_id = _kwargs.get("resource_id")
            def set_after(self, doc): self.after = doc
            def set_resource_id(self, rid): self.resource_id = rid
        yield _Ctx()

    monkeypatch.setattr(ib, "crud_envelope", _fake_envelope)
    if builder_stub is not None:
        monkeypatch.setattr(ib, "build_invoice_batches", builder_stub)
    return db, batches_coll, drafts_coll, ib


# ---------------------------------------------------------------------------
# POST /invoice-batches/build
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_route_requires_permission(monkeypatch):
    _, _, _, ib = _setup(monkeypatch)
    no_perm = _user(role="sales_rep", perms=["contracts.view.own"])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ib.build_batches_route(
            data={"period_start": "2026-05-18", "period_end": "2026-05-31"},
            current_user=no_perm,
        )
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_build_route_requires_period_args(monkeypatch):
    _, _, _, ib = _setup(monkeypatch)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ib.build_batches_route(data={}, current_user=_user())
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_build_route_delegates_to_service(monkeypatch):
    captured: Dict[str, Any] = {}

    async def _builder(db, *, period_start, period_end, contract_id_filter=None,
                       actor_user_id=None, dry_run=False):
        captured.update({
            "period_start": period_start, "period_end": period_end,
            "contract_id_filter": contract_id_filter,
            "actor_user_id": actor_user_id, "dry_run": dry_run,
        })
        return ([_batch()], [], [])

    _, _, _, ib = _setup(monkeypatch, builder_stub=_builder)
    result = await ib.build_batches_route(
        data={"period_start": "2026-05-18", "period_end": "2026-05-31",
              "contract_id": "c_001", "dry_run": True},
        current_user=_user(),
    )
    assert captured["period_start"] == "2026-05-18"
    assert captured["contract_id_filter"] == "c_001"
    assert captured["dry_run"] is True
    assert captured["actor_user_id"] == "u_finance"
    assert len(result["batches"]) == 1


# ---------------------------------------------------------------------------
# GET /invoice-batches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_filters_by_status(monkeypatch):
    rows = [
        _batch(batch_id="ib_pending", status="pending_finance_approval"),
        _batch(batch_id="ib_approved", status="approved"),
        _batch(batch_id="ib_rejected", status="rejected"),
    ]
    _, _, _, ib = _setup(monkeypatch, batches=rows)
    result = await ib.list_invoice_batches(
        status="pending_finance_approval", contract_id=None, period_start=None,
        current_user=_user(),
    )
    ids = {b["batch_id"] for b in result["items"]}
    assert ids == {"ib_pending"}


@pytest.mark.asyncio
async def test_list_invalid_status_returns_400(monkeypatch):
    _, _, _, ib = _setup(monkeypatch)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ib.list_invoice_batches(
            status="invented", contract_id=None, period_start=None, current_user=_user(),
        )
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_list_tenant_scope(monkeypatch):
    rows = [
        _batch(batch_id="ib_a", tenant_id="tenant_a"),
        _batch(batch_id="ib_b", tenant_id="tenant_b"),
    ]
    _, _, _, ib = _setup(monkeypatch, batches=rows)
    tenant_user = _user(tenant_id="tenant_a")
    result = await ib.list_invoice_batches(
        status=None, contract_id=None, period_start=None, current_user=tenant_user,
    )
    ids = {b["batch_id"] for b in result["items"]}
    assert ids == {"ib_a"}


# ---------------------------------------------------------------------------
# GET /invoice-batches/{batch_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cross_tenant_returns_404(monkeypatch):
    rows = [_batch(tenant_id="tenant_a")]
    _, _, _, ib = _setup(monkeypatch, batches=rows)
    cross = _user(tenant_id="tenant_b")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ib.get_invoice_batch(batch_id="ibatch_001", current_user=cross)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_get_unknown_batch_returns_404(monkeypatch):
    _, _, _, ib = _setup(monkeypatch, batches=[])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ib.get_invoice_batch(batch_id="ibatch_missing", current_user=_user())
    assert ei.value.status_code == 404


# ---------------------------------------------------------------------------
# POST /invoice-batches/{batch_id}/approve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_creates_finance_draft(monkeypatch):
    rows = [_batch()]
    db, batches, drafts, ib = _setup(monkeypatch, batches=rows)
    result = await ib.approve_invoice_batch(
        batch_id="ibatch_001", data={"notes": "ok to send"}, current_user=_user(),
    )
    # Finance draft was written.
    assert len(drafts.inserts) == 1
    draft = drafts.inserts[0]
    assert draft["kind"] == "ar_invoice"
    assert draft["source_origin"] == "invoice_batch"
    assert draft["source_batch_id"] == "ibatch_001"
    assert draft["source_contract_id"] == "c_001"
    assert draft["amount"] == 1080.0
    assert len(draft["line_items"]) == 2
    # Batch was flipped to approved with the link back.
    assert result["batch"]["status"] == "approved"
    assert result["batch"]["linked_finance_draft_id"] == draft["draft_id"]
    assert result["already_approved"] is False


@pytest.mark.asyncio
async def test_approve_is_idempotent_on_already_approved(monkeypatch):
    """Second approve call must NOT create a duplicate finance draft."""
    rows = [_batch(status="approved", linked_finance_draft_id="fcc_draft_abc")]
    db, batches, drafts, ib = _setup(
        monkeypatch, batches=rows,
        drafts=[{"draft_id": "fcc_draft_abc", "kind": "ar_invoice"}],
    )
    result = await ib.approve_invoice_batch(
        batch_id="ibatch_001", data={"notes": "again"}, current_user=_user(),
    )
    assert result["already_approved"] is True
    assert result["finance_draft"]["draft_id"] == "fcc_draft_abc"
    # No new draft inserted.
    assert drafts.inserts == []


@pytest.mark.asyncio
async def test_approve_from_rejected_returns_409(monkeypatch):
    rows = [_batch(status="rejected")]
    _, _, _, ib = _setup(monkeypatch, batches=rows)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ib.approve_invoice_batch(
            batch_id="ibatch_001", data={"notes": "force"}, current_user=_user(),
        )
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_approve_requires_permission(monkeypatch):
    rows = [_batch()]
    _, _, _, ib = _setup(monkeypatch, batches=rows)
    no_perm = _user(role="sales_ops", perms=["contracts.create", "contracts.edit"])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ib.approve_invoice_batch(
            batch_id="ibatch_001", data={}, current_user=no_perm,
        )
    assert ei.value.status_code == 403


# ---------------------------------------------------------------------------
# POST /invoice-batches/{batch_id}/reject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_flips_status(monkeypatch):
    rows = [_batch()]
    db, batches, drafts, ib = _setup(monkeypatch, batches=rows)
    result = await ib.reject_invoice_batch(
        batch_id="ibatch_001",
        data={"notes": "wrong period bounds; rebuild after fixing entries"},
        current_user=_user(),
    )
    assert result["batch"]["status"] == "rejected"
    assert result["batch"]["decision_notes"] == "wrong period bounds; rebuild after fixing entries"
    # No finance draft was created.
    assert drafts.inserts == []


@pytest.mark.asyncio
async def test_reject_requires_notes(monkeypatch):
    rows = [_batch()]
    _, _, _, ib = _setup(monkeypatch, batches=rows)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ib.reject_invoice_batch(
            batch_id="ibatch_001", data={"notes": "   "}, current_user=_user(),
        )
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_reject_from_approved_returns_409(monkeypatch):
    rows = [_batch(status="approved")]
    _, _, _, ib = _setup(monkeypatch, batches=rows)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ib.reject_invoice_batch(
            batch_id="ibatch_001", data={"notes": "too late"}, current_user=_user(),
        )
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_reject_is_idempotent_on_already_rejected(monkeypatch):
    rows = [_batch(status="rejected")]
    _, _, _, ib = _setup(monkeypatch, batches=rows)
    result = await ib.reject_invoice_batch(
        batch_id="ibatch_001", data={"notes": "still no"}, current_user=_user(),
    )
    assert result["already_rejected"] is True
    assert result["batch"]["status"] == "rejected"


# ---------------------------------------------------------------------------
# Reject does not touch source time entries — lock via direct assertion
# (the route only writes invoice_batches; never references time_entries).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_does_not_call_time_entries(monkeypatch):
    """Source time entries must remain at status=approved. The reject
    route's update path only touches invoice_batches; missing
    db.time_entries would surface if it were referenced."""
    rows = [_batch()]
    _, batches, _, ib = _setup(monkeypatch, batches=rows)
    # Don't attach time_entries to the fake db — any access raises
    # AttributeError immediately.
    await ib.reject_invoice_batch(
        batch_id="ibatch_001",
        data={"notes": "noise in the period; rebuild later"},
        current_user=_user(),
    )
    # The only collection updated was invoice_batches.
    assert len(batches.updates) == 1
    assert batches.updates[0][1]["$set"]["status"] == "rejected"
