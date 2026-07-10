"""Source-level wiring contract for the Contract Billing Triggers UI panel.

Backend trigger lifecycle (covered by
backend/tests/test_contract_billing_triggers.py and
backend/tests/test_contract_trigger_evaluator.py):

    GET    /api/contracts/{id}/billing-triggers
    POST   /api/contracts/{id}/billing-triggers
    DELETE /api/contracts/{id}/billing-triggers/{trigger_id}   (soft-disable)
    POST   /api/contracts/{id}/billing-triggers/evaluate       (manual evaluator)

Perm gates (mirror routes/contract_triggers.py):
    list     : RBAC contract filter (404 not 403 on miss)
    create   : contracts.edit | contracts.edit.all
    disable  : contracts.edit | contracts.edit.all
    evaluate : contracts.approve | finance.portal | *

Body shape for POST (mirrors validate_trigger_payload):
    {
        trigger_type: "date" | "pct_consumed" | "remaining_threshold"
                    | "block_exhausted" | "biweekly_cycle",
        action     : "notify_finance" | "create_task" | "queue_invoice_batch",
        threshold  : <varies by trigger_type>,
        state?     : "armed" | "disabled"  (fired is evaluator-only)
        label?     : str,
    }

This file locks the new ContractBillingTriggersPanel so a future
refactor cannot silently drift the page-to-route bindings or invent
fields / kinds / actions that the backend would 400 on.

yarn / node_modules are unavailable in the build sandbox; the panel
cannot run through Jest. The contract is verified via source
inspection — same pattern as the prior Contracts UI wiring tests
on this branch (adjustments, approval panel, approval queue,
invoice batches).
"""

from pathlib import Path

import pytest


def _panel_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "contracts"
        / "sections"
        / "ContractBillingTriggersPanel.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def _modal_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "contracts"
        / "modals"
        / "ContractDetailsModal.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def test_panel_exports_default_component():
    src = _panel_src()
    assert "export default function ContractBillingTriggersPanel(" in src, (
        "ContractBillingTriggersPanel.js must export a default "
        "function component named ContractBillingTriggersPanel."
    )


def test_panel_calls_all_four_canonical_backend_routes():
    """Lock the four exact route paths. A typo silently 404s — the
    backend has no fallback for any of these."""
    src = _panel_src()

    # GET list
    assert (
        "api.get(`${API}/api/contracts/${contractId}/billing-triggers`)"
        in src
    ), "List must use api.get against /api/contracts/{id}/billing-triggers."

    # POST create
    assert "api.post(" in src and "/api/contracts/${contractId}/billing-triggers`" in src, (
        "Create must use api.post against the same /billing-triggers path."
    )

    # DELETE (soft-disable)
    assert (
        "api.delete(" in src
        and "/api/contracts/${contractId}/billing-triggers/${trigger.trigger_id}`"
        in src
    ), (
        "Disable must use api.delete against "
        "/api/contracts/{id}/billing-triggers/{trigger_id}."
    )

    # POST evaluate
    assert (
        "/api/contracts/${contractId}/billing-triggers/evaluate`"
        in src
    ), (
        "Manual evaluate must hit "
        "/api/contracts/{id}/billing-triggers/evaluate via POST."
    )


def test_panel_uses_canonical_permission_keys():
    """Permission keys in the UI must match the backend route gates
    exactly. A drift here either locks out the right users or
    surfaces buttons the backend will 403."""
    src = _panel_src()
    # create + disable union (both routes use the same gate).
    for needle in (
        "hasPermission('contracts.edit')",
        "hasPermission('contracts.edit.all')",
    ):
        assert (needle in src) or (needle.replace("'", '"') in src), (
            f"Create / disable gate must check {needle!r}."
        )
    # evaluate union.
    for needle in (
        "hasPermission('contracts.approve')",
        "hasPermission('finance.portal')",
    ):
        assert (needle in src) or (needle.replace("'", '"') in src), (
            f"Evaluate gate must check {needle!r}."
        )
    # Wildcard admin grant on at least one of the gates.
    assert "hasPermission('*')" in src or 'hasPermission("*")' in src, (
        "At least one gate must admit hasPermission('*') for "
        "admin-tier users."
    )


def test_panel_request_body_uses_canonical_fields():
    """POST body must use exactly the field names validate_trigger_payload
    expects. Any rename here silently 400s with 'trigger_type must
    be one of' or 'action must be one of'."""
    src = _panel_src()
    assert "trigger_type: form.trigger_type" in src, (
        "Body must send `trigger_type`."
    )
    assert "action: form.action" in src, "Body must send `action`."
    assert "state: form.state," in src, (
        "Body must send `state` so 'disabled' on create is supported."
    )
    # Numeric threshold coercion for the two numeric types.
    assert "Number(rawThreshold)" in src, (
        "Body must coerce numeric thresholds via Number() so "
        "_validate_payload's float(threshold) does not blow up "
        "on string-vs-number drift."
    )
    # Label optional + non-empty gate.
    assert "if (label) body.label = label;" in src, (
        "Body must include `label` ONLY when non-empty (trimmed)."
    )


def test_panel_threshold_validation_mirrors_backend_per_type():
    """validate_trigger_payload imposes different rules per trigger
    type. The UI must mirror them so the user gets feedback before
    the network round-trip."""
    src = _panel_src()
    # Date: requires ISO date parse.
    assert "Date.parse(raw)" in src, (
        "Date / biweekly_cycle threshold must be validated via "
        "Date.parse() before submit."
    )
    # pct_consumed: 0..100.
    assert "0 || n > 100" in src or "n > 100" in src, (
        "pct_consumed must validate 0..100."
    )
    # remaining_threshold: non-negative number.
    assert "Remaining hours must be" in src, (
        "remaining_threshold must show a non-negative validation "
        "message."
    )
    # block_exhausted: threshold ignored — there must be no
    # threshold input rendered for this type.
    assert "thresholdLabel: null" in src, (
        "block_exhausted must have thresholdLabel: null so no "
        "threshold input renders for this type."
    )


def test_panel_uses_canonical_trigger_types_and_actions():
    """The panel's TRIGGER_TYPES and TRIGGER_ACTIONS must mirror the
    backend frozensets exactly. Extras would 400; missing values
    would silently hide a legitimate option."""
    src = _panel_src()
    for ttype in (
        "date",
        "pct_consumed",
        "remaining_threshold",
        "block_exhausted",
        "biweekly_cycle",
    ):
        assert f"value: '{ttype}'" in src, (
            f"Panel must offer canonical trigger_type {ttype!r}."
        )
    for action in (
        "notify_finance",
        "create_task",
        "queue_invoice_batch",
    ):
        assert f"value: '{action}'" in src, (
            f"Panel must offer canonical action {action!r}."
        )

    # Negative locks — no invented kinds / actions.
    for invented in (
        "weekly_cycle",
        "monthly_cycle",
        "hours_exhausted",
        "overage_threshold",
    ):
        assert f"value: '{invented}'" not in src, (
            f"Panel must not invent trigger_type {invented!r} — "
            "backend would 400 on this value."
        )
    for invented in (
        "email_customer",
        "send_invoice",
        "auto_renew",
    ):
        assert f"value: '{invented}'" not in src, (
            f"Panel must not invent action {invented!r} — backend "
            "would 400 on this value."
        )


def test_panel_state_select_does_not_offer_fired():
    """validate_trigger_payload rejects state='fired' on create —
    only the evaluator sets fired. The UI must not offer it on
    the form (the StateBadge still RENDERS fired for read-only
    display)."""
    src = _panel_src()
    # The state select offers only Armed / Disabled.
    assert "<option value=\"armed\">Armed</option>" in src
    assert "<option value=\"disabled\">Disabled</option>" in src
    # And no `<option value="fired">` anywhere.
    assert '<option value="fired">' not in src, (
        "State select must NOT offer 'fired' — backend rejects it "
        "on create."
    )


def test_panel_disable_route_is_soft_not_hard_delete():
    """The backend DELETE route soft-disables (state -> 'disabled'),
    not a hard delete. The UI must label the action accordingly so
    users understand the audit row is preserved."""
    src = _panel_src()
    # The action label must say "Disable" / "disabled", not "Delete".
    assert "Disable trigger" in src, (
        "Disable button title must say 'Disable trigger' so users "
        "know the row is preserved for the audit chain."
    )
    assert "Trigger disabled" in src, (
        "Success toast on disable must say 'Trigger disabled', "
        "not 'Trigger deleted'."
    )


def test_panel_has_loading_empty_and_error_states():
    src = _panel_src()
    for testid in (
        "contract-billing-triggers-loading",
        "contract-billing-triggers-empty",
        "contract-billing-triggers-error",
    ):
        assert testid in src, (
            f"Panel must render the {testid!r} state."
        )


def test_panel_refreshes_list_and_contract_on_mutating_actions():
    """Create, disable, and evaluate all mutate either the trigger
    list or the contract (queue_invoice_batch creates a batch; date
    triggers flip state to fired). The UI must refresh accordingly."""
    src = _panel_src()
    # create + disable + evaluate all call loadTriggers().
    assert src.count("loadTriggers();") >= 3, (
        "create + disable + evaluate must all call loadTriggers() "
        "to refresh the list."
    )
    # evaluate also propagates to the parent contract.
    assert "onContractUpdated?.();" in src, (
        "evaluate (and any other mutator that may have created a "
        "batch / updated last_action_result) must propagate to the "
        "parent contract via onContractUpdated."
    )


def test_panel_mounted_in_contract_details_modal():
    """The detail modal must import and render the panel; otherwise
    the routes are dead code."""
    src = _modal_src()
    assert (
        "import ContractBillingTriggersPanel from '../sections/ContractBillingTriggersPanel'"
        in src
    ), (
        "ContractDetailsModal must import the new panel from "
        "../sections/ContractBillingTriggersPanel."
    )
    assert "<ContractBillingTriggersPanel" in src, (
        "ContractDetailsModal must render the panel."
    )
    # The modal must wire onContractUpdated so an evaluate that
    # changed contract state propagates to other panels.
    assert "onContractUpdated={() => loadContract()}" in src, (
        "Detail modal must wire ContractBillingTriggersPanel's "
        "onContractUpdated to its loadContract()."
    )


def test_panel_does_not_invent_status_gating():
    """Backend does not restrict trigger CRUD by contract.status or
    approval_status. The UI must not pretend it does."""
    src = _panel_src()
    for invented_gate in (
        "contract.status === 'active'",
        "contract.status === 'completed'",
        "contract.approval_status === 'pending'",
        "contract.approval_status === 'approved'",
    ):
        assert invented_gate not in src, (
            f"Panel must not gate trigger CRUD on {invented_gate!r} — "
            "backend has no such restriction."
        )
