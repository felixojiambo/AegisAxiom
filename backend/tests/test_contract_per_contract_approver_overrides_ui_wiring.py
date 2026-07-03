"""Source-level wiring contract for ContractPerContractApproversPanel.

Locks the bytes-on-the-wire for the new per-contract approver
overrides UI so a future refactor cannot silently drift away from
the backend routes added in the same commit.

Distinct from the existing global approver-overrides UI (which
mutates user.extra_permissions). This panel manages the
contract_approver_overrides collection through the contract
detail surface.
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
        / "ContractPerContractApproversPanel.js"
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


def _app_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "App.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def test_panel_exports_default_component():
    src = _panel_src()
    assert "export default function ContractPerContractApproversPanel(" in src, (
        "ContractPerContractApproversPanel.js must export a default "
        "function named ContractPerContractApproversPanel."
    )


def test_panel_calls_canonical_per_contract_routes():
    """GET / POST / DELETE on /api/contracts/{id}/approver-overrides.
    The path is intentionally not the global
    /admin/contract-approvers — this is contract-scoped."""
    src = _panel_src()
    assert (
        "api.get("
        in src
        and "/api/contracts/${contractId}/approver-overrides"
        in src
    ), "Panel must GET /api/contracts/{id}/approver-overrides."
    assert (
        "api.post("
        in src
        and "/api/contracts/${contractId}/approver-overrides"
        in src
    ), "Panel must POST /api/contracts/{id}/approver-overrides."
    assert (
        "api.delete("
        in src
        and "/api/contracts/${contractId}/approver-overrides/${userId}"
        in src
    ), (
        "Panel must DELETE /api/contracts/{id}/approver-overrides/{user_id}."
    )


def test_grant_body_uses_canonical_fields():
    """The POST body must use exactly user_id (required) and note
    (optional). The backend route validates these field names."""
    src = _panel_src()
    assert "body = { user_id: trimmedId }" in src, (
        "Grant body must start as { user_id: trimmedId } and add "
        "note ONLY when non-empty."
    )
    assert "if (note) body.note = note;" in src, (
        "Grant body must include `note` ONLY when non-empty after "
        "trim — keeps the audit row clean."
    )


def test_panel_management_gate_matches_backend():
    """The backend manage gate is users.edit AND admin role (or *).
    The panel must mirror that union so a non-admin viewer doesn't
    see grant/revoke buttons that would all 403."""
    src = _panel_src()
    assert "hasPermission('users.edit')" in src or 'hasPermission("users.edit")' in src
    assert "isAdmin" in src, (
        "canManage gate must consult isAdmin from usePermissions, "
        "mirroring the backend role check at routes/admin.py:288."
    )
    # The combined gate must AND users.edit with isAdmin, not OR —
    # match the backend trust boundary.
    assert "hasPermission('users.edit') && isAdmin" in src or 'hasPermission("users.edit") && isAdmin' in src, (
        "canManage must AND users.edit with isAdmin to mirror the "
        "backend gate."
    )


def test_panel_distinguishes_from_global_override_page():
    """The notice block must clearly state the per-contract scope
    so users do not confuse this panel with the global page."""
    src = _panel_src()
    assert "this contract only" in src.lower(), (
        "Notice block must call out the per-contract scope."
    )
    assert "/admin/contract-approvers" in src, (
        "Notice block must reference the global page so users "
        "discover where to grant tenant-wide approval authority."
    )


def test_panel_does_not_mutate_global_permissions():
    """Negative lock — this panel must not call any admin/users
    routes or touch extra_permissions. Those mutations live on
    the global page only."""
    src = _panel_src()
    for forbidden in (
        "/api/admin/users",
        "extra_permissions",
    ):
        assert forbidden not in src, (
            f"Per-contract panel must not touch {forbidden!r} — "
            "that's the global page's job."
        )


def test_panel_has_loading_empty_and_error_states():
    src = _panel_src()
    for testid in (
        "contract-per-contract-approvers-loading",
        "contract-per-contract-approvers-empty",
        "contract-per-contract-approvers-error",
        "contract-per-contract-approvers-notice",
    ):
        assert testid in src, (
            f"Panel must render the {testid!r} anchor for browser "
            "smoke."
        )


def test_panel_mounted_in_contract_details_modal():
    src = _modal_src()
    assert (
        "import ContractPerContractApproversPanel from '../sections/ContractPerContractApproversPanel'"
        in src
    ), (
        "ContractDetailsModal must import the per-contract approvers "
        "panel."
    )
    assert "<ContractPerContractApproversPanel" in src, (
        "ContractDetailsModal must render the panel."
    )


def test_approval_queue_route_admits_override_only_users():
    """The frontend approval-queue route must let users without
    global contracts.approve through so they can see the queue
    filtered by their per-contract overrides. Backend is the
    canonical security boundary — the route gate just decides
    whether the page mounts at all."""
    src = _app_src()
    # Find the queue route's PermissionRoute block.
    idx = src.find('path="contracts/approval-queue"')
    assert idx != -1, "App.js must mount the approval-queue route."
    end = src.find("</Route>", idx)
    # </Route> may not appear; tolerate a longer window.
    window = src[idx:idx + 800]
    # The gate must still admit contracts.approve (existing
    # behavior).
    assert "'contracts.approve'" in window, (
        "approval-queue route must still admit contracts.approve."
    )
    # But the gate must ALSO admit broader contract-view perms so
    # override-only users can reach the page.
    assert (
        "'contracts.view.all'" in window
        or "'contracts.view'" in window
        or "'contracts.view.own'" in window
    ), (
        "approval-queue route must admit at least one broader "
        "contract-view permission so per-contract override users "
        "can reach the page. The backend filters the queue contents."
    )
