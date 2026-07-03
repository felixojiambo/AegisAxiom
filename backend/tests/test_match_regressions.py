"""Regression tests for the three highest-risk MATCH ingestion defects from
the Wave 2 code review.

These tests intentionally have a narrow scope — each pins exactly one of the
failure modes that Wave 1.5 had to repair, so the same bug cannot silently
recur on the next ingestion. They are unit-level and offline (no Mongo,
no n8n). For full integration coverage of the MATCH routes see the broader
end-to-end suites — these run in milliseconds and gate the contract.

Run via:
    cd backend
    python -m pytest tests/test_match_regressions.py -v
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Tests live in backend/tests/ — add backend/ to sys.path so the imports
# below resolve the same way they do under the running server.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.match.models import (  # noqa: E402
    BulkApproveRequest,
    ColumnMapping,
    Settings,
)


# ---------------------------------------------------------------------------
# 1. BulkApproveRequest.threshold defaults to 95, NOT 0.
# ---------------------------------------------------------------------------
# Regression target: Wave 1's bulk_approve handler coerced
#     threshold = float(getattr(body, "threshold", 0.0) or 0.0)
# which fell back to 0.0 ("approve everything") instead of trusting the
# Pydantic default of 95. The handler now does `float(body.threshold)`
# unconditionally; this test guards the model default and is the safety net
# for that handler-side trust.

def test_threshold_defaults_to_95_not_zero():
    """Constructing BulkApproveRequest with only session_id must populate
    threshold from the model default (95), not 0."""
    req = BulkApproveRequest(session_id="m_sess_test123")
    assert req.threshold == 95, (
        f"BulkApproveRequest.threshold should default to 95, got {req.threshold}. "
        "If you changed the default, also update the bulk_approve handler "
        "in backend/routes/match_row.py."
    )
    # Belt-and-suspenders: the type must remain int so handlers can pass it
    # to float() without surprise truncation.
    assert isinstance(req.threshold, int)


# ---------------------------------------------------------------------------
# 2. _load_settings helpers return a Pydantic Settings instance, not a dict.
# ---------------------------------------------------------------------------
# Regression target: Wave 1 had two `_load_settings`-style helpers that
# returned raw Mongo dicts. AppDirectClient / N8nClient constructors do
# attribute access in the form `settings.appdirect.bearer_token` and would
# AttributeError on the first call. Wave 1.5 normalized BOTH helpers to
# return `Settings.model_validate(...)`. This test pins that contract.

@pytest.mark.asyncio
async def test_load_settings_returns_pydantic_object():
    """Both _load_settings helpers (match_settings.py and
    match_provider_template.py) must return a Pydantic Settings model so the
    n8n/AppDirect clients can do attribute access in their constructors."""
    # Import here so the test still runs even if a sibling module's import
    # fails — surfaces a clearer error than a collection-time crash.
    from routes import match_provider_template, match_settings

    # Mock a Mongo client. find_one returns a singleton-ish doc that the
    # Settings model can validate (extra keys like _singleton are tolerated
    # because the helpers strip them, but we include realistic shape too).
    fake_doc = {
        "_singleton": True,
        "n8n": {
            "trigger_webhook_url": "https://n8n.example.com/hook/trigger",
            "magic_mapping_webhook_url": "",
            "reviewer_action_webhook_url": "",
            "ping_webhook_url": "",
            "shared_auth_token": "",
            "callback_url": "",
        },
        "appdirect": {
            "base_url": "https://api.appdirect.example.com",
            "bearer_token": "",
            "live_post_enabled": False,
        },
    }

    fake_collection = MagicMock()
    fake_collection.find_one = AsyncMock(return_value=fake_doc)

    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection

    # --- match_settings._load_singleton ---
    settings_a = await match_settings._load_singleton(fake_db)
    assert isinstance(settings_a, Settings), (
        "match_settings._load_singleton must return a Settings instance, got "
        f"{type(settings_a).__name__}. AppDirectClient / N8nClient access "
        "attributes (settings.appdirect.*, settings.n8n.*) — a raw dict would "
        "AttributeError on the first call."
    )
    # Attribute access proves the model_validate happened end-to-end.
    assert settings_a.n8n.trigger_webhook_url == "https://n8n.example.com/hook/trigger"
    assert settings_a.appdirect.base_url == "https://api.appdirect.example.com"

    # --- match_provider_template._load_settings ---
    settings_b = await match_provider_template._load_settings(fake_db)
    assert isinstance(settings_b, Settings), (
        "match_provider_template._load_settings must return a Settings "
        f"instance, got {type(settings_b).__name__}."
    )
    assert settings_b.n8n.trigger_webhook_url == "https://n8n.example.com/hook/trigger"
    assert settings_b.appdirect.base_url == "https://api.appdirect.example.com"

    # Defaults branch — no doc in Mongo returns a Settings() with defaults,
    # NOT an empty dict.
    fake_collection.find_one = AsyncMock(return_value=None)
    defaults_a = await match_settings._load_singleton(fake_db)
    defaults_b = await match_provider_template._load_settings(fake_db)
    assert isinstance(defaults_a, Settings)
    assert isinstance(defaults_b, Settings)
    # Defaults are empty strings for connection blocks — never None.
    assert defaults_a.n8n.trigger_webhook_url == ""
    assert defaults_b.appdirect.bearer_token == ""


# ---------------------------------------------------------------------------
# 3. _apply_transform preview reads data-dict field names (not source app).
# ---------------------------------------------------------------------------
# Regression target: Wave 1 ported the source app's preview loop verbatim,
# which read `appdirect_field` / `provider_column` / `transform` (singular).
# AEGISAXIOM's ColumnMapping uses the data-dict contract: `source_column` /
# `target_path` / `transforms[]` (a list applied left-to-right). The Wave 1
# loop silently returned an empty `extracted` dict for every row.
#
# This test feeds a single-row CSV through the same iteration the
# test_provider_template handler runs, and asserts the nested output lands
# at the dotted target_path with the transform applied.

def test_apply_transform_uses_data_dict_names():
    """The provider-template test-preview loop must read the data-dict
    ColumnMapping fields (source_column / target_path / transforms[]) and
    nest values at the dotted target_path."""
    from routes.match_provider_template import _apply_dot_path, _apply_transform

    # Build a ColumnMapping the way the route handler reads it: dict-shaped
    # (it comes from a Mongo doc, not the Pydantic model directly).
    mapping = ColumnMapping(
        source_column="Account Name",
        target_path="output.customer.name",
        transforms=["strip_whitespace"],  # unknown transform → pass-through
    ).model_dump()

    source_row = {"Account Name": "  Acme Corp  ", "Other": "ignored"}

    # Reproduce the iteration the handler does (post-fix):
    extracted: dict = {}
    target_path = mapping.get("target_path")
    col = mapping.get("source_column")
    transforms = mapping.get("transforms") or []

    assert target_path == "output.customer.name", (
        "Test setup drift — ColumnMapping field rename would invalidate this "
        "regression test."
    )
    assert col == "Account Name"
    assert transforms == ["strip_whitespace"]

    value = source_row.get(col, "")
    for tname in transforms:
        value = _apply_transform(value, tname)
    _apply_dot_path(extracted, target_path, value)

    # The data-dict contract: target_path is a dotted Contract H path, so the
    # output dict must be NESTED, not flat.
    assert "output" in extracted, (
        f"Expected nested target_path output, got flat keys: {list(extracted)}"
    )
    assert extracted["output"]["customer"]["name"] == "  Acme Corp  ", (
        "Unknown transform names should pass the value through unchanged. "
        f"Got: {extracted['output']['customer']['name']!r}"
    )

    # And a real transform — strip_usd_prefix — round-trips a currency string
    # to a float, proving the transform chain still works end-to-end.
    mapping_currency = ColumnMapping(
        source_column="Price",
        target_path="output.line_item.unit_price",
        transforms=["strip_usd_prefix"],
    ).model_dump()
    extracted2: dict = {}
    value2 = "USD 1,234.50"
    for tname in mapping_currency["transforms"]:
        value2 = _apply_transform(value2, tname)
    _apply_dot_path(extracted2, mapping_currency["target_path"], value2)
    assert extracted2["output"]["line_item"]["unit_price"] == 1234.50, (
        "strip_usd_prefix should parse 'USD 1,234.50' to the float 1234.50; "
        f"got {extracted2['output']['line_item']['unit_price']!r}"
    )
