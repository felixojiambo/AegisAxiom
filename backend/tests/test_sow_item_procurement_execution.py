"""Tests for the direct SOW item procurement execution lifecycle.

Covers the new substrate added on top of the Felix manual-line-item
commit (93e63b29) and the Ticket 9 sourcing scaffold:

  * ``SOWProductItem`` carries the procurement lifecycle fields
    (procurement_status, supplier_source_url, expected_delivery_date,
    order_reference, tracking_*, ordered_*, delivered_*).
  * The SOW create-or-update path does not overwrite those fields when
    Sales Ops re-saves the SOW (``_preserve_procurement_lifecycle``).
  * Five new routes wire Procurement and PMO to the SOW item
    lifecycle:
      - GET  /procurement/sow-items/pricing-queue
      - PATCH /procurement/sows/{sow_id}/items/{item_id}/pricing
      - POST /procurement/sows/{sow_id}/items/{item_id}/ordered
      - POST /procurement/sows/{sow_id}/items/{item_id}/delivered
      - GET  /pmo/sows/{sow_id}/checklist
      - GET  /pmo/projects/{project_id}/checklist

Real DB and HTTP are mocked. Synthetic identifiers only — no real
supplier names, vendor names, tenant ids, or user emails appear.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

from fastapi import HTTPException  # noqa: E402

from models import (  # noqa: E402
    SOWCreate,
    SOWItemDeliveredMark,
    SOWItemOrderedMark,
    SOWItemPricingUpdate,
    SOWProductItem,
)


# ---------------------------------------------------------------------------
# Model contract
# ---------------------------------------------------------------------------


def test_sow_product_item_round_trips_procurement_lifecycle_fields():
    """All new optional fields persist through model_dump and reload
    without loss. Defaults stay None so existing Mongo documents that
    pre-date the migration deserialize unchanged."""
    item = SOWProductItem(
        item_id="i_synth_1",
        product_name="Synthetic patch panel",
        item_category="materials",
        quantity=4,
        source="needs_sourcing",
    )
    dumped = item.model_dump()
    for f in (
        "procurement_status",
        "supplier_source_url",
        "availability_note",
        "procurement_notes",
        "expected_delivery_date",
        "order_reference",
        "tracking_number",
        "tracking_url",
        "ordered_at",
        "ordered_by",
        "delivered_at",
        "delivered_by",
        "delivery_notes",
    ):
        assert f in dumped, f"{f} missing from SOWProductItem dump"
        assert dumped[f] is None
    reloaded = SOWProductItem(**dumped)
    assert reloaded.source == "needs_sourcing"
    assert reloaded.procurement_status is None


def test_pricing_update_body_accepts_supplier_source_url():
    body = SOWItemPricingUpdate(
        unit_price=10.0,
        supplier_name="Synthetic Supplier",
        supplier_source_url="https://example.test/p/x",
        availability_note="in stock",
    )
    payload = body.model_dump(exclude_unset=True)
    assert payload["unit_price"] == 10.0
    assert "supplier_source_url" in payload


def test_ordered_mark_body_accepts_tracking_fields():
    body = SOWItemOrderedMark(
        tracking_number="1Z999",
        tracking_url="https://t.example.test/1z",
        order_reference="PO-001",
    )
    payload = body.model_dump(exclude_unset=True)
    assert payload["tracking_number"] == "1Z999"
    assert payload["order_reference"] == "PO-001"


def test_delivered_mark_body_accepts_only_delivery_notes():
    body = SOWItemDeliveredMark(delivery_notes="left at dock")
    assert body.model_dump(exclude_unset=True) == {"delivery_notes": "left at dock"}


def test_sow_create_carries_lifecycle_fields_through_dump():
    """SOWCreate.model_dump must include nested lifecycle fields so the
    create path persists them through the Mongo write."""
    item = SOWProductItem(
        item_id="i_synth_2",
        product_name="Synth cable",
        item_category="materials",
        quantity=1,
        source="manual",
        unit_price=5.0,
        total_price=5.0,
        procurement_status="priced",
        supplier_name="Synthetic Supplier",
    )
    payload = SOWCreate(sow_product_items=[item])
    dumped = payload.model_dump()
    assert dumped["sow_product_items"][0]["procurement_status"] == "priced"
    assert dumped["sow_product_items"][0]["supplier_name"] == "Synthetic Supplier"


# ---------------------------------------------------------------------------
# Preserve helper
# ---------------------------------------------------------------------------


def test_preserve_helper_keeps_lifecycle_fields_on_resave():
    from routes.solutions_design import _preserve_procurement_lifecycle

    existing = [
        {
            "item_id": "i_a",
            "product_name": "Synth A",
            "procurement_status": "ordered",
            "ordered_at": "2026-05-01T00:00:00+00:00",
            "tracking_number": "TRACK-1",
        }
    ]
    incoming = [
        {
            "item_id": "i_a",
            "product_name": "Synth A — edited label",
            "quantity": 9,
            # Caller did NOT touch lifecycle; defaults from SOWCreate
            # would arrive as None.
            "procurement_status": None,
            "ordered_at": None,
            "tracking_number": None,
        }
    ]
    merged = _preserve_procurement_lifecycle(incoming, existing)
    assert merged[0]["product_name"] == "Synth A — edited label"
    assert merged[0]["quantity"] == 9
    assert merged[0]["procurement_status"] == "ordered"
    assert merged[0]["tracking_number"] == "TRACK-1"
    assert merged[0]["ordered_at"] == "2026-05-01T00:00:00+00:00"


def test_preserve_helper_initializes_pending_pricing_for_new_needs_sourcing():
    from routes.solutions_design import _preserve_procurement_lifecycle

    incoming = [
        {
            "item_id": "i_new",
            "product_name": "Synth new manual",
            "source": "needs_sourcing",
            "procurement_status": None,
        }
    ]
    merged = _preserve_procurement_lifecycle(incoming, [])
    assert merged[0]["procurement_status"] == "pending_pricing"


def test_preserve_helper_leaves_catalog_rows_without_lifecycle():
    """Catalog rows do not auto-acquire ``pending_pricing``. The new
    item enters the world with no procurement_status; the field stays
    absent (or None) until Procurement enters pricing through the
    single-item route."""
    from routes.solutions_design import _preserve_procurement_lifecycle

    incoming = [
        {
            "item_id": "i_catalog",
            "product_name": "Cat6 patch",
            "source": "catalog",
            "procurement_status": None,
        }
    ]
    merged = _preserve_procurement_lifecycle(incoming, [])
    assert merged[0].get("procurement_status") is None


def test_preserve_helper_strips_lifecycle_fields_from_new_items():
    """Sales Ops creating a new SOW must not be able to pre-stamp
    procurement_status, ordered_at, or any other lifecycle field on
    a new row. The merge helper strips them and lets the routes own
    the lifecycle."""
    from routes.solutions_design import (
        SOW_ITEM_PROCUREMENT_FIELDS,
        _preserve_procurement_lifecycle,
    )

    incoming = [
        {
            "item_id": "i_attack",
            "product_name": "Synth",
            "source": "manual",
            "unit_price": 5.0,
            "procurement_status": "delivered",
            "ordered_at": "2024-01-01T00:00:00+00:00",
            "delivered_at": "2024-01-02T00:00:00+00:00",
            "tracking_number": "spoof-1",
        }
    ]
    merged = _preserve_procurement_lifecycle(incoming, [])
    for f in SOW_ITEM_PROCUREMENT_FIELDS:
        assert f not in merged[0], (
            f"{f} survived the strip pass on a new item"
        )
    # The product fields are preserved.
    assert merged[0]["product_name"] == "Synth"
    assert merged[0]["unit_price"] == 5.0


def test_pricing_update_rejects_negative_unit_price():
    """Pydantic constraint must reject negative prices before the
    route sees them, so a malformed write cannot land in Mongo with a
    negative unit_price."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SOWItemPricingUpdate(unit_price=-1.0)


# ---------------------------------------------------------------------------
# Fake DB
# ---------------------------------------------------------------------------


class _FakeAsyncCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._limit_n = None

    def limit(self, n):
        self._limit_n = n
        return self

    def __aiter__(self):
        self._iter = iter(
            self._docs[: self._limit_n] if self._limit_n is not None else self._docs
        )
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as e:
            raise StopAsyncIteration from e

    def to_list(self, limit=None):
        async def _coro():
            if limit is not None:
                return list(self._docs)[:limit]
            return list(self._docs)

        return _coro()


class _FakeCollection:
    def __init__(self, docs=None):
        self._docs: list[dict] = [dict(d) for d in (docs or [])]
        self.updates: list[tuple[dict, dict]] = []

    @staticmethod
    def _matches(doc, query):
        for k, v in query.items():
            if doc.get(k) != v:
                return False
        return True

    async def find_one(self, query, _projection=None):
        for d in self._docs:
            if self._matches(d, query):
                return dict(d)
        return None

    def find(self, query, _projection=None):
        matched = [dict(d) for d in self._docs if self._matches(d, query)]
        return _FakeAsyncCursor(matched)

    async def update_one(self, query, update):
        for d in self._docs:
            if self._matches(d, query):
                payload = update.get("$set") or {}
                # Apply dotted updates onto nested array fields the way
                # Mongo does so the next read reflects the change.
                for key, value in payload.items():
                    if "." in key:
                        head, _, rest = key.partition(".")
                        if head == "sow_product_items":
                            idx_str, _, leaf = rest.partition(".")
                            idx = int(idx_str)
                            items = d.setdefault("sow_product_items", [])
                            while len(items) <= idx:
                                items.append({})
                            items[idx][leaf] = value
                        else:
                            d[key] = value
                    else:
                        d[key] = value
                self.updates.append((query, payload))
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


def _user(perms, user_id="u_synth", email="u@aegisaxiom.test"):
    return SimpleNamespace(
        user_id=user_id, email=email, role="sales_ops", permissions=list(perms)
    )


def _fs_sow(items=None, sow_id="sow_synth", status="not_started"):
    return {
        "sow_id": sow_id,
        "opportunity_id": "opp_synth",
        "line_of_business": "field_services",
        "status": status,
        "sow_product_items": list(items or []),
    }


def _ps_sow(items=None, sow_id="sow_synth_ps"):
    return {
        "sow_id": sow_id,
        "opportunity_id": "opp_synth_ps",
        "line_of_business": "professional_services",
        "status": "not_started",
        "sow_product_items": list(items or []),
    }


def _build_db(sows, projects=None):
    return SimpleNamespace(
        opportunity_sows=_FakeCollection(sows),
        pm_projects=_FakeCollection(projects or []),
    )


# ---------------------------------------------------------------------------
# Pricing queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pricing_queue_lists_needs_sourcing_and_pending_pricing_items():
    from routes.sales_ops_procurement import list_pricing_queue

    sow = _fs_sow(
        items=[
            {
                "item_id": "i_a",
                "product_name": "Needs sourcing item",
                "item_category": "materials",
                "quantity": 1,
                "source": "needs_sourcing",
                "procurement_status": "pending_pricing",
            },
            {
                "item_id": "i_b",
                "product_name": "Catalog priced",
                "item_category": "hardware",
                "quantity": 1,
                "source": "catalog",
                "procurement_status": None,
                "unit_price": 50.0,
            },
            {
                "item_id": "i_c",
                "product_name": "Already priced",
                "item_category": "materials",
                "source": "manual",
                "procurement_status": "priced",
            },
            {
                "item_id": "i_d",
                "product_name": "Delivered",
                "item_category": "materials",
                "source": "manual",
                "procurement_status": "delivered",
            },
        ]
    )
    db = _build_db([sow])
    user = _user(["sales_ops.procurement.view"])

    result = await list_pricing_queue(opportunity_id=None, db=db, current_user=user)
    item_ids = sorted(r["item_id"] for r in result["items"])
    assert "i_a" in item_ids
    assert "i_b" in item_ids
    assert "i_c" not in item_ids
    assert "i_d" not in item_ids
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_pricing_queue_excludes_professional_services_sows():
    from routes.sales_ops_procurement import list_pricing_queue

    db = _build_db(
        [
            _fs_sow(
                items=[
                    {
                        "item_id": "i_fs",
                        "product_name": "FS item",
                        "item_category": "materials",
                        "source": "needs_sourcing",
                    }
                ]
            ),
            _ps_sow(
                items=[
                    {
                        "item_id": "i_ps",
                        "product_name": "PS item",
                        "item_category": "services",
                        "source": "needs_sourcing",
                    }
                ]
            ),
        ]
    )
    user = _user(["sales_ops.procurement.view"])
    result = await list_pricing_queue(opportunity_id=None, db=db, current_user=user)
    ids = [r["item_id"] for r in result["items"]]
    assert ids == ["i_fs"]


@pytest.mark.asyncio
async def test_pricing_queue_requires_procurement_view_perm():
    from routes.sales_ops_procurement import list_pricing_queue

    db = _build_db([_fs_sow()])
    user = _user([])  # no perms
    with pytest.raises(HTTPException) as exc:
        await list_pricing_queue(opportunity_id=None, db=db, current_user=user)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Pricing update
# ---------------------------------------------------------------------------


def _patch_audit(monkeypatch):
    captured: list[dict] = []

    async def _fake(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(
        "routes.sales_ops_procurement.audit_log_action", _fake
    )
    return captured


@pytest.mark.asyncio
async def test_pricing_update_flips_needs_sourcing_to_manual_and_priced(monkeypatch):
    from routes.sales_ops_procurement import update_sow_item_pricing

    captured = _patch_audit(monkeypatch)
    sow = _fs_sow(
        items=[
            {
                "item_id": "i_a",
                "product_name": "Synth",
                "item_category": "materials",
                "quantity": 4,
                "source": "needs_sourcing",
                "procurement_status": "pending_pricing",
                "unit_price": 0.0,
            }
        ]
    )
    db = _build_db([sow])
    user = _user(["sales_ops.procurement.edit"])

    body = SOWItemPricingUpdate(
        unit_price=12.5,
        supplier_name="Synthetic Supplier",
        supplier_source_url="https://example.test/p/123",
    )
    fresh = await update_sow_item_pricing(
        sow_id="sow_synth",
        item_id="i_a",
        body=body,
        db=db,
        current_user=user,
    )
    assert fresh["source"] == "manual"
    assert fresh["procurement_status"] == "priced"
    assert fresh["unit_price"] == 12.5
    assert fresh["total_price"] == 50.0  # 4 * 12.5
    assert fresh["supplier_name"] == "Synthetic Supplier"
    assert captured and captured[0]["action"] == "sales_ops.sow_item.priced"


@pytest.mark.asyncio
async def test_pricing_update_rejects_when_item_already_ordered(monkeypatch):
    from routes.sales_ops_procurement import update_sow_item_pricing

    _patch_audit(monkeypatch)
    sow = _fs_sow(
        items=[
            {
                "item_id": "i_a",
                "product_name": "Synth",
                "item_category": "materials",
                "quantity": 1,
                "source": "manual",
                "procurement_status": "ordered",
                "unit_price": 10.0,
            }
        ]
    )
    db = _build_db([sow])
    user = _user(["sales_ops.procurement.edit"])
    body = SOWItemPricingUpdate(unit_price=999.0)

    with pytest.raises(HTTPException) as exc:
        await update_sow_item_pricing(
            sow_id="sow_synth", item_id="i_a", body=body, db=db, current_user=user
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "item_lifecycle_locked"


@pytest.mark.asyncio
async def test_pricing_update_rejects_professional_services_sow(monkeypatch):
    from routes.sales_ops_procurement import update_sow_item_pricing

    _patch_audit(monkeypatch)
    db = _build_db(
        [
            _ps_sow(
                items=[
                    {
                        "item_id": "i_ps",
                        "product_name": "PS item",
                        "item_category": "services",
                        "source": "needs_sourcing",
                    }
                ],
                sow_id="sow_ps",
            )
        ]
    )
    user = _user(["sales_ops.procurement.edit"])
    body = SOWItemPricingUpdate(unit_price=1.0)
    with pytest.raises(HTTPException) as exc:
        await update_sow_item_pricing(
            sow_id="sow_ps", item_id="i_ps", body=body, db=db, current_user=user
        )
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "sow_not_field_services"


@pytest.mark.asyncio
async def test_pricing_update_requires_procurement_edit():
    from routes.sales_ops_procurement import update_sow_item_pricing

    db = _build_db([_fs_sow(items=[{"item_id": "i_a", "product_name": "x", "item_category": "materials"}])])
    user = _user(["sales_ops.procurement.view"])
    body = SOWItemPricingUpdate(unit_price=1.0)
    with pytest.raises(HTTPException) as exc:
        await update_sow_item_pricing(
            sow_id="sow_synth", item_id="i_a", body=body, db=db, current_user=user
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_pricing_update_rejects_empty_body(monkeypatch):
    from routes.sales_ops_procurement import update_sow_item_pricing

    _patch_audit(monkeypatch)
    sow = _fs_sow(
        items=[
            {
                "item_id": "i_a",
                "product_name": "x",
                "item_category": "materials",
                "source": "needs_sourcing",
            }
        ]
    )
    db = _build_db([sow])
    user = _user(["sales_ops.procurement.edit"])
    body = SOWItemPricingUpdate()
    with pytest.raises(HTTPException) as exc:
        await update_sow_item_pricing(
            sow_id="sow_synth", item_id="i_a", body=body, db=db, current_user=user
        )
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "empty_pricing_update"


# ---------------------------------------------------------------------------
# Mark ordered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_ordered_requires_priced_status(monkeypatch):
    from routes.sales_ops_procurement import mark_sow_item_ordered

    _patch_audit(monkeypatch)
    sow = _fs_sow(
        items=[
            {
                "item_id": "i_a",
                "product_name": "x",
                "item_category": "materials",
                "source": "needs_sourcing",
                "procurement_status": "pending_pricing",
            }
        ]
    )
    db = _build_db([sow])
    user = _user(["sales_ops.procurement.edit"])
    body = SOWItemOrderedMark()
    with pytest.raises(HTTPException) as exc:
        await mark_sow_item_ordered(
            sow_id="sow_synth", item_id="i_a", body=body, db=db, current_user=user
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "item_not_priced"


@pytest.mark.asyncio
async def test_mark_ordered_records_tracking_and_audits(monkeypatch):
    from routes.sales_ops_procurement import mark_sow_item_ordered

    captured = _patch_audit(monkeypatch)
    sow = _fs_sow(
        items=[
            {
                "item_id": "i_a",
                "product_name": "x",
                "item_category": "materials",
                "source": "manual",
                "procurement_status": "priced",
                "unit_price": 10.0,
            }
        ]
    )
    db = _build_db([sow])
    user = _user(["sales_ops.procurement.edit"])
    body = SOWItemOrderedMark(
        tracking_number="1Z-synth",
        order_reference="PO-001",
        tracking_url="https://t.example.test/synth",
    )
    fresh = await mark_sow_item_ordered(
        sow_id="sow_synth", item_id="i_a", body=body, db=db, current_user=user
    )
    assert fresh["procurement_status"] == "ordered"
    assert fresh["tracking_number"] == "1Z-synth"
    assert fresh["order_reference"] == "PO-001"
    assert fresh["ordered_at"] is not None
    assert fresh["ordered_by"] == "u_synth"
    assert captured and captured[0]["action"] == "sales_ops.sow_item.ordered"
    assert captured[0]["details"]["has_tracking_number"] is True


# ---------------------------------------------------------------------------
# Mark delivered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_delivered_requires_ordered_status(monkeypatch):
    from routes.sales_ops_procurement import mark_sow_item_delivered

    _patch_audit(monkeypatch)
    sow = _fs_sow(
        items=[
            {
                "item_id": "i_a",
                "product_name": "x",
                "item_category": "materials",
                "procurement_status": "priced",
            }
        ]
    )
    db = _build_db([sow])
    user = _user(["sales_ops.procurement.edit"])
    body = SOWItemDeliveredMark()
    with pytest.raises(HTTPException) as exc:
        await mark_sow_item_delivered(
            sow_id="sow_synth", item_id="i_a", body=body, db=db, current_user=user
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "item_not_ordered"


@pytest.mark.asyncio
async def test_mark_delivered_pmo_capability_is_sufficient(monkeypatch):
    """PMO holding projects.edit.all can mark delivered without
    sales_ops.procurement.edit."""
    from routes.sales_ops_procurement import mark_sow_item_delivered

    captured = _patch_audit(monkeypatch)
    sow = _fs_sow(
        items=[
            {
                "item_id": "i_a",
                "product_name": "x",
                "item_category": "materials",
                "procurement_status": "ordered",
            }
        ]
    )
    db = _build_db([sow])
    pmo = _user(["projects.edit.all"])
    body = SOWItemDeliveredMark(delivery_notes="left at site")
    fresh = await mark_sow_item_delivered(
        sow_id="sow_synth", item_id="i_a", body=body, db=db, current_user=pmo
    )
    assert fresh["procurement_status"] == "delivered"
    assert fresh["delivery_notes"] == "left at site"
    assert captured[0]["details"]["marked_via"] == "pmo"


@pytest.mark.asyncio
async def test_mark_delivered_is_idempotent_when_already_delivered(monkeypatch):
    from routes.sales_ops_procurement import mark_sow_item_delivered

    _patch_audit(monkeypatch)
    sow = _fs_sow(
        items=[
            {
                "item_id": "i_a",
                "product_name": "x",
                "item_category": "materials",
                "procurement_status": "delivered",
            }
        ]
    )
    db = _build_db([sow])
    user = _user(["sales_ops.procurement.edit"])
    fresh = await mark_sow_item_delivered(
        sow_id="sow_synth",
        item_id="i_a",
        body=SOWItemDeliveredMark(),
        db=db,
        current_user=user,
    )
    assert fresh["procurement_status"] == "delivered"


@pytest.mark.asyncio
async def test_mark_delivered_rejects_unauthorized_caller():
    from routes.sales_ops_procurement import mark_sow_item_delivered

    db = _build_db(
        [
            _fs_sow(
                items=[
                    {
                        "item_id": "i_a",
                        "product_name": "x",
                        "item_category": "materials",
                        "procurement_status": "ordered",
                    }
                ]
            )
        ]
    )
    sales_user = _user(["sales.opportunities.view"])
    with pytest.raises(HTTPException) as exc:
        await mark_sow_item_delivered(
            sow_id="sow_synth",
            item_id="i_a",
            body=SOWItemDeliveredMark(),
            db=db,
            current_user=sales_user,
        )
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# PMO checklist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pmo_sow_checklist_groups_items_by_category_and_counts_delivered():
    from routes.sales_ops_procurement import get_pmo_sow_checklist

    sow = _fs_sow(
        items=[
            {
                "item_id": "i_m",
                "product_name": "Material A",
                "item_category": "materials",
                "procurement_status": "delivered",
            },
            {
                "item_id": "i_h",
                "product_name": "Hardware A",
                "item_category": "hardware",
                "procurement_status": "ordered",
            },
            {
                "item_id": "i_s",
                "product_name": "Service A",
                "item_category": "services",
                "procurement_status": None,
            },
        ]
    )
    db = _build_db([sow])
    pmo = _user(["projects.view"])
    out = await get_pmo_sow_checklist(sow_id="sow_synth", db=db, current_user=pmo)
    assert len(out["items_by_category"]["materials"]) == 1
    assert len(out["items_by_category"]["hardware"]) == 1
    assert len(out["items_by_category"]["services"]) == 1
    assert out["totals"]["total_items"] == 3
    assert out["totals"]["delivered_items"] == 1
    assert out["totals"]["remaining_items"] == 2


@pytest.mark.asyncio
async def test_pmo_project_checklist_resolves_opportunity_to_fs_sows():
    from routes.sales_ops_procurement import get_pmo_project_checklist

    project = {"project_id": "proj_synth", "opportunity_id": "opp_synth"}
    sow = _fs_sow(
        items=[
            {
                "item_id": "i_a",
                "product_name": "x",
                "item_category": "materials",
                "procurement_status": "delivered",
            }
        ]
    )
    db = _build_db([sow], projects=[project])
    pmo = _user(["projects.view"])
    out = await get_pmo_project_checklist(
        project_id="proj_synth", db=db, current_user=pmo
    )
    assert out["opportunity_id"] == "opp_synth"
    assert len(out["checklists"]) == 1
    assert out["checklists"][0]["totals"]["delivered_items"] == 1


@pytest.mark.asyncio
async def test_pmo_project_checklist_returns_empty_when_no_opportunity_link():
    from routes.sales_ops_procurement import get_pmo_project_checklist

    project = {"project_id": "proj_orphan"}  # no opportunity_id
    db = _build_db([], projects=[project])
    pmo = _user(["projects.view"])
    out = await get_pmo_project_checklist(
        project_id="proj_orphan", db=db, current_user=pmo
    )
    assert out["opportunity_id"] is None
    assert out["checklists"] == []


@pytest.mark.asyncio
async def test_pmo_sow_checklist_rejects_non_field_services_sow():
    from routes.sales_ops_procurement import get_pmo_sow_checklist

    db = _build_db([_ps_sow()])
    pmo = _user(["projects.view"])
    with pytest.raises(HTTPException) as exc:
        await get_pmo_sow_checklist(sow_id="sow_synth_ps", db=db, current_user=pmo)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Source-level checks — confirm no theme / hardcoded vendor regressions
# ---------------------------------------------------------------------------


def test_no_hardcoded_vendor_names_in_new_route_code():
    """The execution path must remain vendor-neutral. The Procurement
    bot scaffold doc enumerates the forbidden surface (no real vendor
    names in source). Lock that here for the new route block."""
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "routes" / "sales_ops_procurement.py"
    if not path.exists():  # pragma: no cover — not mounted in some test images
        pytest.skip("backend routes not mounted in this image")
    src = path.read_text(encoding="utf-8")
    forbidden = (
        "Amazon",
        "amazon.com",
        "CDW",
        "Insight Enterprises",
        "Best Buy",
        "Newegg",
    )
    for name in forbidden:
        assert name not in src, f"Hardcoded vendor name leaked into route source: {name}"


def test_new_routes_register_at_expected_paths():
    """Smoke-import the router and confirm the five new paths exist."""
    from routes.sales_ops_procurement import router

    paths = {r.path for r in router.routes}
    assert "/procurement/sow-items/pricing-queue" in paths
    assert "/procurement/sows/{sow_id}/items/{item_id}/pricing" in paths
    assert "/procurement/sows/{sow_id}/items/{item_id}/ordered" in paths
    assert "/procurement/sows/{sow_id}/items/{item_id}/delivered" in paths
    assert "/pmo/sows/{sow_id}/checklist" in paths
    assert "/pmo/projects/{project_id}/checklist" in paths
