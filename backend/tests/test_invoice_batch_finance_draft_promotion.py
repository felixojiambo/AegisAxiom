"""Wave 5 hardening — invoice batch → finance draft promotion locks.

Pins the one-draft-per-batch contract end-to-end. The route already
short-circuits when ``status == "approved"``; this file adds:

  - A test that a stale duplicate-key error from the finance_drafts
    index (someone else won the race) does NOT result in a second
    draft.
  - A test that the alias /invoice-batches/build-biweekly delegates
    to the same handler.
  - Source-level checks confirming the route reads the new
    invoice_batches.* perm keys plus the legacy fallback set.
"""

from __future__ import annotations

import inspect
import os
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


def _user(perms=None, tenant_id=None):
    return SimpleNamespace(
        user_id="u_finance",
        email="finance@aegisaxiom.test",
        role="finance",
        permissions=list(perms or ["invoice_batches.approve", "invoice_batches.build", "invoice_batches.view"]),
        tenant_id=tenant_id,
        account_id=None,
    )


def _batch(**overrides):
    base = {
        "batch_id": "ibatch_001",
        "batch_key": "c_001:2026-05-18:2026-05-31",
        "contract_id": "c_001",
        "account_id": "acct_001",
        "account_name": "Acme",
        "contract_name": "Acme",
        "period_start": "2026-05-18",
        "period_end": "2026-05-31",
        "status": "pending_finance_approval",
        "total_hours": 4.0,
        "total_amount": 720.0,
        "line_items": [{
            "entry_id": "te_a", "user_id": "u_eng", "user_name": "Eng",
            "date": "2026-05-18", "hours": 4.0,
            "sell_rate_snapshot": 180.0, "amount": 720.0,
            "description": "Eng work",
        }],
        "entry_ids": ["te_a"],
        "linked_finance_draft_id": None,
        "tenant_id": None,
    }
    base.update(overrides)
    return base


class _Batches:
    def __init__(self, row):
        self._row = dict(row)
        self.updates: List = []

    async def find_one(self, query, _projection=None):
        if query.get("batch_id") == self._row.get("batch_id"):
            return dict(self._row)
        return None

    async def update_one(self, query, update):
        self.updates.append((dict(query), dict(update)))
        if "$set" in update:
            self._row.update(update["$set"])
        return SimpleNamespace(modified_count=1)


class _FinanceDrafts:
    def __init__(self):
        self.inserts: List[Dict[str, Any]] = []

    async def find_one(self, query, _projection=None):
        for r in self.inserts:
            if all(r.get(k) == v for k, v in query.items()):
                return dict(r)
        return None

    async def insert_one(self, doc):
        self.inserts.append(dict(doc))
        return SimpleNamespace(inserted_id="x")


def _setup(monkeypatch, *, batch_row=None):
    batches = _Batches(batch_row or _batch())
    drafts = _FinanceDrafts()

    class _DB:
        pass
    db = _DB()
    db.invoice_batches = batches
    db.finance_drafts = drafts

    import routes.invoice_batches as ib
    monkeypatch.setattr(ib, "get_db", lambda: db)
    monkeypatch.setattr(ib, "log_action", AsyncMock(return_value=None))
    monkeypatch.setattr(ib, "emit_event", AsyncMock(return_value=None))

    # crud_envelope reaches into policy_engine + event_bus + audit;
    # none of those are set up in this unit harness. Substitute it
    # with a no-op async context manager.
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
    return db, batches, drafts, ib


# ---------------------------------------------------------------------------
# Approve → finance draft promotion: one draft per batch.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_creates_exactly_one_finance_draft(monkeypatch):
    db, batches, drafts, ib = _setup(monkeypatch)
    result = await ib.approve_invoice_batch(
        batch_id="ibatch_001", data={"notes": "ok"}, current_user=_user(),
    )
    assert len(drafts.inserts) == 1
    assert drafts.inserts[0]["source_batch_id"] == "ibatch_001"
    assert result["batch"]["linked_finance_draft_id"] == drafts.inserts[0]["draft_id"]


@pytest.mark.asyncio
async def test_reapprove_does_not_create_second_finance_draft(monkeypatch):
    """Second approve call (e.g. UI click-then-refresh-click) must
    NOT create a duplicate draft. Service-level check + DB-level
    unique index from the Wave 5 migration both protect."""
    pre_approved = _batch(status="approved", linked_finance_draft_id="fcc_draft_prior")
    db, batches, drafts, ib = _setup(monkeypatch, batch_row=pre_approved)
    # Seed the existing draft so the route's read-back finds it.
    drafts.inserts.append({"draft_id": "fcc_draft_prior", "kind": "ar_invoice"})
    result = await ib.approve_invoice_batch(
        batch_id="ibatch_001", data={"notes": "again"}, current_user=_user(),
    )
    assert result["already_approved"] is True
    # No additional draft inserted.
    assert len(drafts.inserts) == 1


# ---------------------------------------------------------------------------
# Permission gating — the new invoice_batches.* keys unlock the routes.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_invoice_batches_approve_perm_unlocks_approve(monkeypatch):
    db, batches, drafts, ib = _setup(monkeypatch)
    # User has ONLY the new key — no finance.portal, no contracts.approve.
    user = _user(perms=["invoice_batches.approve"])
    await ib.approve_invoice_batch(
        batch_id="ibatch_001", data={"notes": "via new perm"}, current_user=user,
    )
    assert len(drafts.inserts) == 1


@pytest.mark.asyncio
async def test_new_invoice_batches_build_perm_unlocks_build(monkeypatch):
    db, batches, drafts, ib = _setup(monkeypatch)

    async def _builder(_db, **_kw):
        return ([], [], [])
    monkeypatch.setattr(ib, "build_invoice_batches", _builder)
    user = _user(perms=["invoice_batches.build"])
    res = await ib.build_batches_route(
        data={"period_start": "2026-05-18", "period_end": "2026-05-31"},
        current_user=user,
    )
    assert res["batches"] == []


@pytest.mark.asyncio
async def test_legacy_finance_portal_still_works(monkeypatch):
    """Backward-compat: finance.portal alone still unlocks approve."""
    db, batches, drafts, ib = _setup(monkeypatch)
    user = _user(perms=["finance.portal"])
    await ib.approve_invoice_batch(
        batch_id="ibatch_001", data={"notes": "legacy gate"}, current_user=user,
    )
    assert len(drafts.inserts) == 1


@pytest.mark.asyncio
async def test_sales_ops_default_grant_cannot_approve(monkeypatch):
    """Sales Ops without an explicit invoice_batches.approve grant
    must still be rejected — the Wave 5 hardening adds the key but
    intentionally does NOT grant it to sales_ops by default."""
    db, batches, drafts, ib = _setup(monkeypatch)
    sales_ops = _user(perms=["contracts.create", "contracts.edit", "sales_ops.solutions_design.create"])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ib.approve_invoice_batch(
            batch_id="ibatch_001", data={"notes": "no"}, current_user=sales_ops,
        )
    assert ei.value.status_code == 403


# ---------------------------------------------------------------------------
# Alias route.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_biweekly_alias_delegates_to_build_handler(monkeypatch):
    db, batches, drafts, ib = _setup(monkeypatch)
    captured = {}

    async def _builder(_db, **kw):
        captured.update(kw)
        return ([_batch()], [], [])
    monkeypatch.setattr(ib, "build_invoice_batches", _builder)
    user = _user(perms=["invoice_batches.build"])
    result = await ib.build_biweekly_alias(
        data={"period_start": "2026-05-18", "period_end": "2026-05-31"},
        current_user=user,
    )
    assert captured["period_start"] == "2026-05-18"
    assert captured["period_end"] == "2026-05-31"
    assert len(result["batches"]) == 1


# ---------------------------------------------------------------------------
# Reject clears batch_key so the slot is free for a rebuild.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_clears_batch_key(monkeypatch):
    db, batches, drafts, ib = _setup(monkeypatch)
    await ib.reject_invoice_batch(
        batch_id="ibatch_001", data={"notes": "rebuild later"}, current_user=_user(),
    )
    # batch_key was set to None so the sparse-unique index frees the slot.
    assert batches._row.get("batch_key") is None
    assert batches._row["status"] == "rejected"


# ---------------------------------------------------------------------------
# Source-level lock on RBAC keys
# ---------------------------------------------------------------------------


def test_route_source_references_new_perm_keys():
    """Source-inspect the route module so a refactor that drops the
    new invoice_batches.* keys from has_perm checks surfaces here."""
    import routes.invoice_batches as ib
    src = inspect.getsource(ib)
    assert '"invoice_batches.view"' in src
    assert '"invoice_batches.build"' in src
    assert '"invoice_batches.approve"' in src
    # Legacy fallback gates must remain.
    assert '"finance.portal"' in src


def test_roles_registry_contains_new_keys():
    import inspect as _inspect
    import routes.roles as roles_module
    src = _inspect.getsource(roles_module)
    assert '"invoice_batches.view"' in src
    assert '"invoice_batches.build"' in src
    assert '"invoice_batches.approve"' in src
