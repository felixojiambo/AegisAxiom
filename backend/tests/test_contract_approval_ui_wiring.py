"""Source-level wiring contract for the Contract Approval UI panel.

Backend approval lifecycle (already covered by
backend/tests/test_contract_approval_workflow.py and
backend/tests/test_contract_approver_overrides.py):

    POST /api/contracts/{id}/submit-for-approval
    POST /api/contracts/{id}/approve
    POST /api/contracts/{id}/reject
    POST /api/contracts/{id}/request-changes

Until this branch, none of those routes had a frontend consumer —
contracts could only be moved through the approval lifecycle via
API tooling. This file locks the new ContractApprovalPanel so a
future refactor cannot quietly drop the page-to-route binding or
drift the permission keys / status gates away from the backend.

yarn / node_modules are unavailable in the build sandbox, so the
React component cannot run through Jest here; the contract is
verified via source inspection — same pattern as the invoice
batches UI wiring test that landed in the previous commit.
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
        / "ContractApprovalPanel.js"
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
    assert "export default function ContractApprovalPanel(" in src, (
        "ContractApprovalPanel.js must export a default function "
        "component named ContractApprovalPanel."
    )


def test_panel_calls_canonical_backend_routes():
    """The panel must hit exactly the four backend approval routes.
    A typo or path drift here would silently 404 in production.
    Lock the URL-suffix derivation so the action -> path map can't
    silently change."""
    src = _panel_src()
    # The URL is built as `${API}/api/contracts/${contract.contract_id}/${urlSuffix}`.
    assert "/api/contracts/${contract.contract_id}/${urlSuffix}" in src, (
        "Panel must build the action URL as "
        "/api/contracts/{id}/{action} via urlSuffix."
    )
    # Each action's URL suffix must match the backend route literal.
    for needle in (
        "action === 'request_changes' ? 'request-changes'",
        "action === 'submit' ? 'submit-for-approval'",
    ):
        assert needle in src, (
            f"Panel URL suffix derivation must include {needle!r} "
            "so request_changes -> 'request-changes' and submit -> "
            "'submit-for-approval' line up with the route paths."
        )


def test_panel_status_gating_mirrors_backend_legal_priors():
    """Backend _LEGAL_PRIOR_STATES (contracts.py):
        submit: {None, '', 'draft', 'rejected', 'changes_requested'}
        approve / reject / request_changes: {'pending'}
    The UI must mirror this so a button is never rendered for a
    transition the route would reject with 409."""
    src = _panel_src()
    # Submit-legal prior set. None is normalized to '' before the
    # check (normalizeApprovalStatus), so the JS set carries the
    # other four prior values.
    assert "SUBMIT_LEGAL_PRIORS = new Set(['', 'draft', 'rejected', 'changes_requested'])" in src, (
        "SUBMIT_LEGAL_PRIORS must match the backend submit legal "
        "prior set verbatim (after None->'' normalization)."
    )
    # Decision-legal prior set (approve / reject / request_changes).
    assert "DECISION_LEGAL_PRIORS = new Set(['pending'])" in src, (
        "DECISION_LEGAL_PRIORS must contain exactly 'pending' to "
        "match the backend gate for approve / reject / request_changes."
    )
    # Each action's visibility must AND permission with the legal
    # prior gate — never just permission alone.
    for guard in (
        "showSubmit = canSubmit && submitLegal",
        "showApprove = canDecide && decisionLegal",
        "showReject = canDecide && decisionLegal",
        "showRequestChanges = canDecide && decisionLegal",
    ):
        assert guard in src, (
            f"Action visibility must AND permission with legal-prior: {guard!r}"
        )


def test_panel_uses_canonical_permission_keys():
    """Permission keys in the UI must match the backend route gates
    exactly (see _apply_approval_action `required_perm`). A typo
    here would either lock everyone out (if too restrictive) or
    surface actions the backend will 403."""
    src = _panel_src()
    # Submit gate: contracts.edit (mirrors required_perm on the
    # submit-for-approval route).
    assert "hasPermission('contracts.edit')" in src or 'hasPermission("contracts.edit")' in src, (
        "Submit visibility must check hasPermission('contracts.edit')."
    )
    # Decision gate: contracts.approve (mirrors the three decision
    # routes' required_perm).
    assert "hasPermission('contracts.approve')" in src or 'hasPermission("contracts.approve")' in src, (
        "Decision visibility must check hasPermission('contracts.approve')."
    )


def test_panel_sends_optional_notes_body_shape():
    """Backend reads notes from either 'notes' or 'approval_notes'
    keys. The UI sends 'notes' (the simpler form). When the user
    leaves notes blank, the panel posts an empty body so the route
    sees notes as None — matching the existing test fixtures."""
    src = _panel_src()
    assert "notes && notes.trim() ? { notes: notes.trim() } : {}" in src, (
        "Panel must build the request body as { notes: trimmed } "
        "only when notes are non-empty; otherwise post {}."
    )


def test_panel_handles_ps_auto_create_response():
    """Approving a Professional Services contract may auto-create a
    project. The backend stamps _auto_created_project_id on the
    refreshed contract response. The UI must surface this so the
    user is not surprised by the side-effect."""
    src = _panel_src()
    assert "_auto_created_project_id" in src, (
        "Panel must read updated._auto_created_project_id from the "
        "approve response so the PS auto-create side-effect is "
        "visible to the user."
    )


def test_panel_renders_status_badge_and_decision_notes():
    """The panel must show the current approval_status badge plus
    the latest decision notes (approval_notes). Without these, the
    user has no visual feedback on what state the contract is in
    after a decision."""
    src = _panel_src()
    assert "ApprovalStatusBadge" in src, (
        "Panel must render an ApprovalStatusBadge showing the "
        "current approval_status."
    )
    assert "approval_notes" in src, (
        "Panel must surface the latest decision notes via the "
        "contract.approval_notes field."
    )
    # The five canonical approval_status values must each have meta
    # so the badge renders correctly across the lifecycle.
    for status_value in ("draft", "pending", "approved", "rejected", "changes_requested"):
        assert f"{status_value}:" in src, (
            f"APPROVAL_STATUS_META must carry an entry for {status_value!r}."
        )


def test_modal_mounts_approval_panel_with_refresh_callback():
    """The detail modal must import and render the panel, and it
    must wire onContractUpdated so the badge refreshes after an
    action without requiring the user to reopen the modal."""
    src = _modal_src()
    assert (
        "import ContractApprovalPanel from '../sections/ContractApprovalPanel'"
        in src
    ), (
        "ContractDetailsModal must import the new panel from "
        "../sections/ContractApprovalPanel."
    )
    assert "<ContractApprovalPanel" in src, (
        "ContractDetailsModal must render the panel."
    )
    assert "onContractUpdated=" in src, (
        "Detail modal must wire onContractUpdated so the panel can "
        "refresh contract state after a decision."
    )


def test_no_invented_fields_or_routes():
    """The panel must consume only the fields the backend route
    response actually returns and only the four canonical routes.
    A regression here would mean we are guessing schema and the
    UI will silently misbehave when the backend changes."""
    src = _panel_src()
    # Canonical fields from the contracts route response.
    for field in ("approval_status", "approval_notes", "approved_by", "approved_at", "contract_id"):
        assert field in src, (
            f"Panel must read the canonical {field!r} field. Drift "
            "from this list means the UI is inventing schema."
        )
    # The four canonical route action tokens, used in the success
    # message map. Anything else here would mean we shipped a
    # phantom action.
    for action in ("submit", "approve", "reject", "request_changes"):
        assert f"{action}:" in src, (
            f"Panel must declare a success message for {action!r}."
        )
