"""Locks the SOW manual-line-item contract.

Field Services Sales Ops must be able to list required materials,
hardware, and services on a SOW even when the product catalog has no
matching row — Procurement prices/sources them later. The fix added
``source`` and ``supplier_name`` to ``SOWProductItem`` and relaxed
``product_id`` / ``product_sku`` to be optional so a manually entered
line can persist without a catalog product.

These tests validate the persisted contract at the model layer:

  * catalog-shaped items still validate (backwards compatibility)
  * manual items with a known price serialize ``source='manual'``
  * manual items without a price serialize ``source='needs_sourcing'``
  * each Field Services category (materials/hardware/services) round-trips
  * the parent ``SOWCreate`` payload carries manual items through
    ``model_dump`` exactly as the route writes them to Mongo

The model layer is the right place to lock this — the route's persistence
path is just ``SOWCreate.model_dump(exclude_unset=True)`` + Mongo upsert
(``backend/routes/solutions_design.py:445-461``), so any field that
survives ``model_dump`` survives save/load.

Synthetic identifiers only. No fixtures, no DB.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

from models import SOWCreate, SOWProductItem  # noqa: E402


def _make_catalog_item(category: str = "materials") -> dict:
    return {
        "item_id": "item_synth_cat_001",
        "product_id": "prod_synth_001",
        "product_name": "Cat6 Patch Cable",
        "product_sku": "CAT6-3FT",
        "item_category": category,
        "quantity": 5,
        "unit_price": 12.50,
        "total_price": 62.50,
        "notes": "",
        "source": "catalog",
    }


def _make_manual_priced_item(category: str = "materials") -> dict:
    return {
        "item_id": "item_synth_manual_001",
        "product_id": None,
        "product_name": "RG-6 quad-shield bulk spool",
        "product_sku": None,
        "item_category": category,
        "quantity": 2,
        "unit_price": 95.00,
        "total_price": 190.00,
        "notes": "Site requirement per scope",
        "source": "manual",
        "supplier_name": None,
        "unit": "spool",
    }


def _make_needs_sourcing_item(category: str = "hardware") -> dict:
    return {
        "item_id": "item_synth_ns_001",
        "product_id": None,
        "product_name": "Outdoor PoE switch (24-port)",
        "product_sku": None,
        "item_category": category,
        "quantity": 1,
        "unit_price": 0.0,
        "total_price": 0.0,
        "notes": "",
        "source": "needs_sourcing",
        "supplier_name": None,
        "unit": "each",
    }


# --- SOWProductItem model contract ---------------------------------------


def test_sow_product_item_accepts_catalog_shape():
    """Existing catalog entries must continue to validate unchanged."""
    item = SOWProductItem(**_make_catalog_item())
    dumped = item.model_dump()
    assert dumped["source"] == "catalog"
    assert dumped["product_id"] == "prod_synth_001"
    assert dumped["product_sku"] == "CAT6-3FT"


def test_sow_product_item_accepts_manual_priced_entry():
    """A manual entry with a price must validate and report source=manual."""
    item = SOWProductItem(**_make_manual_priced_item())
    dumped = item.model_dump()
    assert dumped["source"] == "manual"
    assert dumped["product_id"] is None
    assert dumped["product_sku"] is None
    assert dumped["unit_price"] == pytest.approx(95.00)
    assert dumped["unit"] == "spool"


def test_sow_product_item_accepts_needs_sourcing_entry():
    """A manual entry without a price must validate as needs_sourcing."""
    item = SOWProductItem(**_make_needs_sourcing_item())
    dumped = item.model_dump()
    assert dumped["source"] == "needs_sourcing"
    assert dumped["unit_price"] == 0.0


@pytest.mark.parametrize("category", ["materials", "hardware", "services"])
def test_sow_product_item_preserves_field_services_categories(category):
    """All three FS-only line-item categories must round-trip."""
    payload = _make_manual_priced_item(category=category)
    item = SOWProductItem(**payload)
    assert item.item_category == category
    assert item.model_dump()["item_category"] == category


def test_sow_product_item_supplier_name_optional():
    """supplier_name is optional; manual entries may carry it for Procurement."""
    payload = _make_manual_priced_item()
    payload["supplier_name"] = "Local Distributor A"  # not a real vendor name; synthetic
    item = SOWProductItem(**payload)
    assert item.supplier_name == "Local Distributor A"

    payload_no_supplier = _make_manual_priced_item()
    payload_no_supplier["supplier_name"] = None
    item_no = SOWProductItem(**payload_no_supplier)
    assert item_no.supplier_name is None


def test_sow_product_item_rejects_missing_name():
    """product_name remains required so Procurement always has something to source."""
    bad = _make_manual_priced_item()
    bad.pop("product_name")
    with pytest.raises(Exception):  # pydantic.ValidationError or its alias
        SOWProductItem(**bad)


# --- SOWCreate end-to-end persistence shape ------------------------------


def test_sow_create_carries_mixed_catalog_and_manual_items():
    """The SOW save payload must keep catalog and manual entries together
    so reload renders both kinds of rows."""
    payload = {
        "sow_product_items": [
            _make_catalog_item(category="hardware"),
            _make_manual_priced_item(category="materials"),
            _make_needs_sourcing_item(category="services"),
        ]
    }
    sow_create = SOWCreate(**payload)
    dumped = sow_create.model_dump(exclude_unset=True)
    items = dumped["sow_product_items"]
    assert len(items) == 3

    by_source = {item["source"]: item for item in items}
    assert set(by_source.keys()) == {"catalog", "manual", "needs_sourcing"}
    assert by_source["catalog"]["item_category"] == "hardware"
    assert by_source["manual"]["item_category"] == "materials"
    assert by_source["needs_sourcing"]["item_category"] == "services"
    # The manual row keeps its identity through dump even with null product_id/sku.
    assert by_source["manual"]["product_id"] is None
    assert by_source["needs_sourcing"]["product_sku"] is None


def test_sow_create_round_trip_preserves_manual_fields():
    """Round-trip via model_dump → model_validate must preserve the manual
    fields exactly. This is the same shape the route hands to Mongo and
    the same shape the GET endpoint returns."""
    original = SOWCreate(
        sow_product_items=[
            SOWProductItem(**_make_manual_priced_item(category="services")),
        ]
    )
    dumped = original.model_dump(exclude_unset=True)
    rebuilt = SOWCreate(**dumped)
    item = rebuilt.sow_product_items[0]
    assert item.source == "manual"
    assert item.item_category == "services"
    assert item.product_name == "RG-6 quad-shield bulk spool"
    assert item.unit_price == pytest.approx(95.00)
    assert item.total_price == pytest.approx(190.00)