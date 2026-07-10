"""Wave 0 (contracts-time-mgmt) — Contract model back-compat locks.

These tests pin the additive schema contract for the Wave 0 commit:

  1. A legacy Contract document (only the fields that existed before
     this wave) still deserializes without raising.
  2. Every new field is present, optional, and has the documented
     default value.
  3. The new contract_type option ``boh_with_boa`` is accepted by the
     Contract model (which carries contract_type as a free-form string)
     and is exposed in the CONTRACT_TYPES options list so the policy
     admin GUI and form schema endpoint can render it.
  4. The model never made any previously optional field required;
     instantiating with only the original required fields still works.

No live database is touched. No new test framework is added; the file
uses plain pytest and the existing ``os.environ.setdefault("JWT_SECRET",
…)`` preamble seen in adjacent unit tests (e.g.
test_opportunity_close_date.py) so auth_helper's module-load guard
does not block import.
"""

from __future__ import annotations

import os

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

from datetime import datetime, timezone

from models import Contract
from services.field_options import CONTRACT_TYPES


# Minimal field set a legacy Contract row would carry. Mirrors the
# fields that existed before Wave 0 plus the three required ones the
# model has always demanded (contract_id, name, account_id). Anything
# the model added in Wave 0 must NOT be required for this dict to
# instantiate.
_LEGACY_CONTRACT_DICT = {
    "contract_id": "contract_legacy_001",
    "name": "Legacy BOH Contract",
    "account_id": "acct_legacy_001",
    "contract_type": "boh",
    "total_hours_purchased": 40.0,
    "hours_consumed": 12.5,
    "fixed_amount": 0.0,
    "monthly_rate": 0.0,
    "status": "active",
}


def test_legacy_contract_dict_still_deserializes():
    """A pre-Wave-0 contract document loads without error."""
    c = Contract(**_LEGACY_CONTRACT_DICT)
    assert c.contract_id == "contract_legacy_001"
    assert c.contract_type == "boh"
    assert c.status == "active"
    assert c.hours_consumed == 12.5


def test_wave0_optional_fields_default_safely():
    """Every Wave 0 field defaults to a safe empty value."""
    c = Contract(**_LEGACY_CONTRACT_DICT)
    # Approval block.
    assert c.approval_status is None
    assert c.approved_by is None
    assert c.approved_at is None
    assert c.approval_notes is None
    # BOH + BOA overflow.
    assert c.bill_and_actuals_active is False
    assert c.overage_hours == 0.0
    assert c.overage_bill_rate is None
    assert c.overage_cost_rate is None
    # Contract dates / value.
    assert c.signed_date is None
    assert c.financial_commit is None
    # Forecast.
    assert c.forecast == {}
    # SOW snapshot.
    assert c.source_sow_id is None
    assert c.original_sow_snapshot == {}
    assert c.spirit_summary is None
    # Tenancy / import.
    assert c.tenant_id is None
    assert c.import_source is None
    assert c.external_contract_id is None
    assert c.external_source_synced_at is None


def test_wave0_optional_fields_round_trip_when_set():
    """When a caller does supply the new fields they round-trip
    verbatim. Confirms the fields are settable, not just defaulted."""
    now = datetime.now(timezone.utc)
    c = Contract(
        **_LEGACY_CONTRACT_DICT,
        approval_status="pending",
        approved_by="user_a",
        approved_at=now,
        approval_notes="Hold for legal review",
        bill_and_actuals_active=True,
        overage_hours=4.5,
        overage_bill_rate=210.0,
        overage_cost_rate=130.0,
        signed_date=now,
        financial_commit=125_000.0,
        forecast={"hours": 200, "revenue": 50_000},
        source_sow_id="sow_001",
        original_sow_snapshot={"frozen": True, "version": 3},
        spirit_summary="Migrate UCaaS tenant by end of quarter.",
        tenant_id="tenant_001",
        import_source="zoho",
        external_contract_id="zoho_contract_001",
        external_source_synced_at=now,
    )
    assert c.approval_status == "pending"
    assert c.bill_and_actuals_active is True
    assert c.overage_hours == 4.5
    assert c.forecast == {"hours": 200, "revenue": 50_000}
    assert c.original_sow_snapshot["version"] == 3
    assert c.external_contract_id == "zoho_contract_001"


def test_boh_with_boa_is_accepted_by_contract_type():
    """The new contract_type option must be accepted on the wire.
    The Contract model carries contract_type as a free-form string;
    the option list is what gates dropdowns and policy admin choices."""
    c = Contract(**{**_LEGACY_CONTRACT_DICT, "contract_type": "boh_with_boa"})
    assert c.contract_type == "boh_with_boa"


def test_boh_with_boa_listed_in_contract_types_options():
    """CONTRACT_TYPES is the canonical option list consumed by the
    policy admin GUI. The new boh_with_boa entry must be present so
    later waves can render it without further plumbing."""
    values = {opt["value"] for opt in CONTRACT_TYPES}
    assert "boh_with_boa" in values, (
        "CONTRACT_TYPES is missing boh_with_boa; check "
        "services/field_options.py"
    )
    # Legacy options must still be present (no removals in Wave 0).
    for legacy in ("boh", "boa", "fixed_fee", "monthly"):
        assert legacy in values, f"CONTRACT_TYPES dropped legacy option {legacy}"


def test_wave0_did_not_add_required_fields():
    """The Wave 0 commit must not have promoted any previously-optional
    field to required. The original required set is (contract_id, name,
    account_id) — verified by instantiating with that set only and
    expecting success."""
    c = Contract(
        contract_id="contract_min",
        name="Minimal",
        account_id="acct_min",
    )
    # Defaults kick in for everything else.
    assert c.contract_type == "fixed_fee"
    assert c.total_hours_purchased == 0.0
    assert c.status == "active"
    assert c.approval_status is None
    assert c.forecast == {}
