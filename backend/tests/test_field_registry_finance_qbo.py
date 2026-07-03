"""
Unit tests for field_registry_finance_qbo — verifies that all QBO virtual
entities are registered correctly in the AEGISAXIOM field registry at import time.

No live DB or server required; the registry is an in-process dict.
"""

import sys
import os
import importlib
import pytest

# Ensure the backend services path is on sys.path so imports resolve.
_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ---------------------------------------------------------------------------
# We need a clean registry state per test run so we reset and re-import.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def import_registry_module():
    """Import the QBO registry module once for all tests in this file.

    The module calls register_all_qbo_virtual_entities() at import time, so a
    single import is sufficient to populate the registry. We reload here to
    guarantee a deterministic state even if a previous test suite partially
    populated the registry.
    """
    import services.field_registry as fr
    import services.field_registry_finance_qbo as qbo_mod

    # Reload both to get a clean slate.
    importlib.reload(fr)
    importlib.reload(qbo_mod)
    return fr


# ---------------------------------------------------------------------------
# Expected entity names (all registered under module "finance")
# ---------------------------------------------------------------------------

EXPECTED_ENTITIES = [
    "qbo_chart_of_accounts",
    "qbo_general_ledger",
    "qbo_ar_invoice",
    "qbo_ap_bill",
    "qbo_account",
    "qbo_contact",
    "qbo_report_pnl",
    "qbo_report_pnl_prior",
    "qbo_report_balance_sheet",
    "qbo_report_cash_flow",
    "qbo_report_trial_balance",
    "qbo_report_ar_aging",
    "qbo_report_ap_aging",
    "qbo_report_ar_aging_detail",
    "qbo_report_ap_aging_detail",
    "qbo_report_customer_balance",
    "qbo_report_vendor_balance",
    "qbo_report_customer_income",
    "qbo_report_vendor_expenses",
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestQBOVirtualEntityRegistration:

    def _get_fr(self):
        import services.field_registry as fr
        return fr

    def test_finance_module_has_all_qbo_entities(self):
        """get_entities_for_module('finance') must contain all 19 QBO virtual entities."""
        fr = self._get_fr()
        registered = fr.get_entities_for_module("finance")
        missing = [e for e in EXPECTED_ENTITIES if e not in registered]
        assert not missing, f"Missing QBO virtual entities from 'finance' module: {missing}"

    def test_at_least_19_qbo_entities_registered(self):
        """At least 19 qbo_* entities must be present under the finance module."""
        fr = self._get_fr()
        registered = fr.get_entities_for_module("finance")
        qbo_entities = [e for e in registered if e.startswith("qbo_")]
        assert len(qbo_entities) >= 19, (
            f"Expected >= 19 qbo_* entities, found {len(qbo_entities)}: {qbo_entities}"
        )

    def test_qbo_general_ledger_has_required_fields(self):
        """qbo_general_ledger must declare tx_date, amount, and account_name fields."""
        fr = self._get_fr()
        fields = fr.get_fields("finance", "qbo_general_ledger")
        field_names = {f["name"] for f in fields}
        for required in ("tx_date", "amount", "account_name"):
            assert required in field_names, (
                f"qbo_general_ledger is missing field '{required}'. Found: {field_names}"
            )

    def test_qbo_general_ledger_has_computed_join_fields(self):
        """qbo_general_ledger must have deal_id, class_name, location marked as computed."""
        fr = self._get_fr()
        fields = fr.get_fields("finance", "qbo_general_ledger")
        computed = {f["name"]: f for f in fields if f.get("computed")}
        for join_field in ("deal_id", "class_name", "location"):
            assert join_field in computed, (
                f"Expected computed field '{join_field}' in qbo_general_ledger. Computed fields found: {list(computed)}"
            )

    def test_qbo_general_ledger_field_types(self):
        """Key qbo_general_ledger fields must have correct types."""
        fr = self._get_fr()
        fields = {f["name"]: f for f in fr.get_fields("finance", "qbo_general_ledger")}
        assert fields["tx_date"]["type"] == "date", "tx_date must be type 'date'"
        assert fields["amount"]["type"] == "number", "amount must be type 'number'"
        assert fields["debit"]["type"] == "number", "debit must be type 'number'"
        assert fields["credit"]["type"] == "number", "credit must be type 'number'"

    def test_every_qbo_entity_has_synced_event(self):
        """Every QBO virtual entity must register the 'synced' event."""
        fr = self._get_fr()
        for entity in EXPECTED_ENTITIES:
            events = fr.get_module_events("finance", entity)
            assert "synced" in events, (
                f"Entity '{entity}' is missing the 'synced' event. Registered events: {events}"
            )

    def test_every_qbo_entity_has_at_least_one_field(self):
        """Every QBO virtual entity must have at least one field registered."""
        fr = self._get_fr()
        for entity in EXPECTED_ENTITIES:
            fields = fr.get_fields("finance", entity)
            assert len(fields) >= 1, (
                f"Entity '{entity}' has no registered fields."
            )

    def test_all_fields_have_qbo_lake_marker(self):
        """Every field description in a QBO virtual entity must start with '[QBO LAKE]'."""
        fr = self._get_fr()
        violations = []
        for entity in EXPECTED_ENTITIES:
            for field in fr.get_fields("finance", entity):
                desc = field.get("description", "")
                if not desc.startswith("[QBO LAKE]"):
                    violations.append(f"{entity}.{field['name']}: {desc!r}")
        assert not violations, (
            f"Fields missing '[QBO LAKE]' marker in description:\n" + "\n".join(violations)
        )

    def test_qbo_ar_invoice_has_key_fields(self):
        """qbo_ar_invoice must expose the canonical invoice fields."""
        fr = self._get_fr()
        fields = {f["name"] for f in fr.get_fields("finance", "qbo_ar_invoice")}
        for expected in ("qbo_invoice_id", "balance", "total_amount", "due_date", "customer_ref"):
            assert expected in fields, f"qbo_ar_invoice missing field '{expected}'"

    def test_qbo_ap_bill_has_key_fields(self):
        """qbo_ap_bill must expose the canonical bill fields."""
        fr = self._get_fr()
        fields = {f["name"] for f in fr.get_fields("finance", "qbo_ap_bill")}
        for expected in ("qbo_bill_id", "balance", "total_amount", "due_date", "vendor_ref"):
            assert expected in fields, f"qbo_ap_bill missing field '{expected}'"

    def test_module_configs_registered_for_qbo_virtual_entities(self):
        """Each QBO virtual entity should have a module_config entry (keyed by entity name)."""
        fr = self._get_fr()
        all_configs = fr.get_all_module_configs()
        # Virtual entities register their module config under their own entity name.
        expected_config_keys = [
            "qbo_chart_of_accounts",
            "qbo_general_ledger",
            "qbo_ar_invoice",
            "qbo_ap_bill",
            "qbo_account",
            "qbo_contact",
            "qbo_report_pnl",
            "qbo_report_pnl_prior",
            "qbo_report_balance_sheet",
            "qbo_report_cash_flow",
            "qbo_report_trial_balance",
            "qbo_report_ar_aging",
            "qbo_report_ap_aging",
            "qbo_report_ar_aging_detail",
            "qbo_report_ap_aging_detail",
            "qbo_report_customer_balance",
            "qbo_report_vendor_balance",
            "qbo_report_customer_income",
            "qbo_report_vendor_expenses",
        ]
        missing_configs = [k for k in expected_config_keys if k not in all_configs]
        assert not missing_configs, f"Missing module configs: {missing_configs}"

    def test_module_configs_use_qbo_lake_category(self):
        """All QBO virtual entity module configs must use 'Finance (QBO Lake)' as category."""
        fr = self._get_fr()
        all_configs = fr.get_all_module_configs()
        bad = [
            k for k in all_configs
            if k.startswith("qbo_") and all_configs[k].get("category") != "Finance (QBO Lake)"
        ]
        assert not bad, f"QBO module configs with wrong category: {bad}"
