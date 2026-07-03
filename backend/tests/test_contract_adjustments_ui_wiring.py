"""Source-level wiring contract for the Contract Adjustments UI panel.

Backend adjustments lifecycle (covered by
backend/tests/test_contract_adjustments.py — 20 tests):

    GET  /api/contracts/{id}/adjustments
    POST /api/contracts/{id}/adjustments
        body: { kind, amount > 0, reason_code,
                reason_note?, linked_time_entry_id? }
        kind ∈ {hours_credit, hours_debit, billing_credit, billing_debit}
        reason_code ∈ {late_timesheet, scope_change, customer_credit,
                       correction, goodwill, manual_reconcile, other}
        perms: contracts.approve | contracts.edit.all | *

Until this commit, neither route had a frontend consumer — finance
had to apply credit/debit corrections via API tooling. This file
locks the new ContractAdjustmentsPanel so a future refactor cannot
silently drift the page-to-route binding or invent fields the
backend's _validate_payload would 400 on.

yarn / node_modules are unavailable in the build sandbox, so the
React panel cannot run through Jest here; the contract is verified
via source inspection — same pattern as the prior Contracts UI
wiring tests on this branch (approval panel, approval queue,
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
        / "ContractAdjustmentsPanel.js"
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
    assert "export default function ContractAdjustmentsPanel(" in src, (
        "ContractAdjustmentsPanel.js must export a default function "
        "component named ContractAdjustmentsPanel."
    )


def test_panel_calls_canonical_backend_routes():
    """Lock the exact route paths. A typo here silently 404s — the
    backend has no fallback."""
    src = _panel_src()
    assert "`${API}/api/contracts/${contractId}/adjustments`" in src, (
        "Panel must use the canonical "
        "/api/contracts/{id}/adjustments path for both GET and POST."
    )
    # The list route uses GET and the create route uses POST; the
    # backend rejects any other verb on these paths.
    assert "api.get(`${API}/api/contracts/${contractId}/adjustments`)" in src, (
        "List must use api.get — no other verb is allowed."
    )
    assert "api.post(" in src and "/adjustments`" in src, (
        "Create must use api.post against the same path."
    )


def test_panel_uses_canonical_create_permission_keys():
    """The create route gates on
        contracts.approve | contracts.edit.all | *
    The panel must check exactly this union — narrower hides the
    button for users who can act; broader surfaces a button that
    the backend will 403."""
    src = _panel_src()
    for needle in (
        "hasPermission('contracts.approve')",
        "hasPermission('contracts.edit.all')",
        "hasPermission('*')",
    ):
        assert (needle in src) or (needle.replace("'", '"') in src), (
            f"Panel create gate must check {needle!r}."
        )


def test_panel_request_body_matches_backend_fields():
    """The POST body must use the exact field names _validate_payload
    expects. A renamed field here silently 400s with 'kind must be
    one of ...' or 'reason_code must be one of ...' or
    'amount must be a number'."""
    src = _panel_src()
    # The required field assignments must be present in the body
    # construction.
    assert "kind: form.kind," in src, "Body must send `kind`."
    assert "amount: amountNumber," in src, (
        "Body must send `amount` as a number (after Number()), not "
        "the raw form string."
    )
    assert "reason_code: form.reason_code," in src, (
        "Body must send `reason_code`."
    )
    # Optional fields are sent only when populated — match the
    # condition syntax verbatim so the lock is exact.
    assert "if (note) body.reason_note = note;" in src, (
        "Body must include reason_note ONLY when it is non-empty "
        "after trim, so a blank textarea does not write an empty "
        "string into the audit row."
    )
    assert "if (linked) body.linked_time_entry_id = linked;" in src, (
        "Body must include linked_time_entry_id ONLY when populated "
        "(the backend 404s on a non-existent entry id)."
    )


def test_panel_enforces_amount_greater_than_zero_client_side():
    """Backend _validate_payload rejects amount <= 0 with 400. The
    UI must mirror this so the user gets immediate feedback rather
    than a network round-trip + generic toast."""
    src = _panel_src()
    assert "amountNumber > 0" in src, (
        "Panel must validate amount > 0 before enabling submit."
    )
    assert "formValid" in src, (
        "Panel must derive a formValid flag (kindValid && "
        "reasonValid && amountValid) and disable submit when false."
    )
    # The submit button must be disabled when invalid OR submitting.
    assert "disabled={!formValid || submitting}" in src, (
        "Submit button must disable on invalid form or in-flight "
        "request."
    )


def test_panel_uses_canonical_kinds_and_reason_codes():
    """The panel's kind + reason-code lists must mirror backend
    VALID_ADJUSTMENT_KINDS and VALID_REASON_CODES verbatim. Any
    extra value invented client-side would 400; any missing value
    would silently hide a legitimate option."""
    src = _panel_src()
    canonical_kinds = (
        "hours_credit",
        "hours_debit",
        "billing_credit",
        "billing_debit",
    )
    for k in canonical_kinds:
        assert f"value: '{k}'" in src, (
            f"Panel must offer the canonical kind {k!r} in its "
            "ADJUSTMENT_KINDS list."
        )
    canonical_reasons = (
        "late_timesheet",
        "scope_change",
        "customer_credit",
        "correction",
        "goodwill",
        "manual_reconcile",
        "other",
    )
    for r in canonical_reasons:
        assert f"value: '{r}'" in src, (
            f"Panel must offer the canonical reason_code {r!r} in "
            "its REASON_CODES list."
        )

    # Negative locks — no invented kinds / reasons.
    for invented_kind in ("refund", "void", "writeoff", "write_off", "credit_memo"):
        assert f"value: '{invented_kind}'" not in src, (
            f"Panel must not offer invented kind {invented_kind!r} — "
            "backend would 400 on this value."
        )
    for invented_reason in ("dispute", "chargeback", "promotion", "trial"):
        assert f"value: '{invented_reason}'" not in src, (
            f"Panel must not offer invented reason_code "
            f"{invented_reason!r} — backend would 400 on this value."
        )


def test_panel_has_loading_empty_and_error_states():
    """The panel must render distinct states so the user never
    stares at a frozen UI on slow networks or permission gaps.
    Testid hooks anchor a future browser smoke."""
    src = _panel_src()
    for testid in (
        "contract-adjustments-loading",
        "contract-adjustments-empty",
        "contract-adjustments-error",
    ):
        assert testid in src, (
            f"Panel must render the {testid!r} state."
        )


def test_panel_refreshes_list_and_contract_after_create():
    """A successful create writes a new audit row AND mutates the
    contract (hours_consumed / financial_commit / overage_hours).
    The panel must (a) reload the adjustments list and (b) tell
    the parent modal to reload the contract so other panels show
    the new numbers."""
    src = _panel_src()
    assert "loadAdjustments();" in src, (
        "Panel submit must call loadAdjustments() to refresh the "
        "list after a successful create."
    )
    assert "onContractUpdated?.();" in src, (
        "Panel submit must invoke onContractUpdated so the parent "
        "modal can re-load the contract after the backend mutates "
        "hours_consumed / financial_commit etc."
    )


def test_panel_does_not_invent_edit_or_delete_routes():
    """The backend deliberately exposes ONLY GET + POST on
    /adjustments — every row is an immutable audit entry with
    before/after snapshots. The UI must not pretend edit / delete
    exist."""
    src = _panel_src()
    for forbidden_verb in ("api.patch(", "api.put(", "api.delete("):
        assert forbidden_verb not in src, (
            f"Panel must not call {forbidden_verb!r} on adjustments "
            "— the backend exposes only GET and POST. Adjustments "
            "are immutable audit rows."
        )


def test_panel_mounted_in_contract_details_modal():
    """The detail modal must import and render the panel; otherwise
    the route is dead code."""
    src = _modal_src()
    assert (
        "import ContractAdjustmentsPanel from '../sections/ContractAdjustmentsPanel'"
        in src
    ), (
        "ContractDetailsModal must import the new panel from "
        "../sections/ContractAdjustmentsPanel."
    )
    assert "<ContractAdjustmentsPanel" in src, (
        "ContractDetailsModal must render the panel."
    )
    # The modal must wire onContractUpdated so the contract reloads
    # after a successful adjustment.
    assert "onContractUpdated={() => loadContract()}" in src, (
        "Detail modal must wire ContractAdjustmentsPanel's "
        "onContractUpdated to its loadContract() so refreshed "
        "balances propagate to the rest of the modal."
    )


def test_panel_does_not_pretend_status_gating_we_do_not_have():
    """The backend does NOT restrict adjustments by contract.status
    — it gates purely on the create-permission union. The UI must
    not invent a status gate (e.g. block adjustments on a 'completed'
    contract) because that would diverge from backend behavior."""
    src = _panel_src()
    # Negative lock — no contract.status filter on the form.
    for invented_gate in (
        "contract.status === 'active'",
        "contract.status === 'completed'",
        "contract.status === 'cancelled'",
        "contract.approval_status === 'pending'",
    ):
        assert invented_gate not in src, (
            f"Panel must not gate the create form on "
            f"{invented_gate!r} — backend has no such restriction."
        )
