"""Wave 0 (contracts-time-mgmt) — TimeEntry model back-compat locks.

These tests pin the additive schema contract for the Wave 0 TimeEntry
extension:

  1. A legacy TimeEntry document (only the fields that existed before
     this wave) still deserializes without raising.
  2. is_billable still works exactly as before — Wave 0 does NOT touch
     legacy billing semantics; derivation of billing_type from
     is_billable + contract.contract_type lands in Wave 3.
  3. The four new fields default safely (None) and round-trip when set.
  4. No previously optional field was promoted to required.

No live database is touched. The ``os.environ.setdefault("JWT_SECRET",
…)`` preamble matches adjacent unit tests so the auth_helper module-load
guard does not block import.
"""

from __future__ import annotations

import os

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

from models import TimeEntry


# A minimal pre-Wave-0 time entry. Mirrors what a legacy row carries:
# the canonical required tuple plus the historic is_billable flag.
_LEGACY_TIME_ENTRY_DICT = {
    "entry_id": "te_legacy_001",
    "user_id": "user_legacy_001",
    "project_id": "proj_legacy_001",
    "date": "2025-09-01",
    "hours": 1.5,
    "notes": "Legacy entry — back-compat test",
    "is_billable": True,
}


def test_legacy_time_entry_dict_still_deserializes():
    """A pre-Wave-0 time entry loads without error."""
    t = TimeEntry(**_LEGACY_TIME_ENTRY_DICT)
    assert t.entry_id == "te_legacy_001"
    assert t.hours == 1.5
    assert t.is_billable is True
    assert t.status == "draft"


def test_is_billable_still_works_both_values():
    """Wave 0 must not have altered is_billable semantics. True/False
    both round-trip and the default remains True."""
    t_true = TimeEntry(**_LEGACY_TIME_ENTRY_DICT)
    assert t_true.is_billable is True
    t_false = TimeEntry(**{**_LEGACY_TIME_ENTRY_DICT, "is_billable": False})
    assert t_false.is_billable is False
    # Default — drop the explicit value.
    minimal = {k: v for k, v in _LEGACY_TIME_ENTRY_DICT.items() if k != "is_billable"}
    t_default = TimeEntry(**minimal)
    assert t_default.is_billable is True


def test_wave0_optional_fields_default_safely():
    """Every Wave 0 TimeEntry field defaults to None."""
    t = TimeEntry(**_LEGACY_TIME_ENTRY_DICT)
    assert t.billing_type is None
    assert t.cost_rate_snapshot is None
    assert t.sell_rate_snapshot is None
    assert t.effective_billing_mode is None


def test_wave0_optional_fields_round_trip_when_set():
    """The four new fields are settable and persist verbatim."""
    t = TimeEntry(
        **_LEGACY_TIME_ENTRY_DICT,
        billing_type="billable",
        cost_rate_snapshot=85.0,
        sell_rate_snapshot=175.0,
        effective_billing_mode="boh",
    )
    assert t.billing_type == "billable"
    assert t.cost_rate_snapshot == 85.0
    assert t.sell_rate_snapshot == 175.0
    assert t.effective_billing_mode == "boh"


def test_wave0_did_not_add_required_fields():
    """The Wave 0 commit must not promote any previously optional
    field to required. The original required set on TimeEntry is
    (entry_id, user_id, project_id, date, hours, notes) — Wave 0
    leaves it alone."""
    t = TimeEntry(
        entry_id="te_min",
        user_id="u_min",
        project_id="p_min",
        date="2025-09-01",
        hours=0.5,
        notes="min",
    )
    # Defaults kick in for everything else.
    assert t.is_billable is True
    assert t.status == "draft"
    assert t.billing_type is None
    assert t.cost_rate_snapshot is None
    assert t.sell_rate_snapshot is None
    assert t.effective_billing_mode is None


def test_billing_type_is_free_form_string():
    """Wave 0 does not add a validator that constrains billing_type.
    The model accepts any string; the derivation/validation layer
    lands in Wave 3."""
    for value in ("billable", "non_billable", "fixed_fee"):
        t = TimeEntry(**{**_LEGACY_TIME_ENTRY_DICT, "billing_type": value})
        assert t.billing_type == value
