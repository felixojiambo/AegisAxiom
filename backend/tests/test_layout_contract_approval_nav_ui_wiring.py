"""Source-level wiring: Contract Approval nav entries restored.

The Operations sidebar (Layout.js) lost the Contract Approvals and
Contract Approvers links in an earlier refactor — the underlying
routes and components were never deleted (App.js still mounts
``contracts/approval-queue`` and ``admin/contract-approvers``, the
``pages/ContractApprovalQueuePage.jsx`` and
``components/admin/ContractApproverOverrides.js`` files still
exist). This test pins the nav entries so a future refactor cannot
silently drop them again.

Frontend test image is unavailable in this sandbox, so the test
asserts on the source string — same pattern every other
``*_ui_wiring.py`` test uses.
"""
from pathlib import Path

import pytest


def _layout_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend" / "src" / "components" / "Layout.js"
    )
    if not path.exists():  # pragma: no cover — sandbox without frontend
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def _app_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend" / "src" / "App.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def test_contract_approvals_nav_entry_present_in_operations_group():
    """Operations sidebar must list 'Contract Approvals' with the
    canonical route App.js mounts at contracts/approval-queue."""
    src = _layout_src()
    assert "'/contracts/approval-queue'" in src
    assert "label: 'Contract Approvals'" in src


def test_contract_approvers_admin_nav_entry_present():
    """Operations sidebar (admin section) must list the
    tenant-wide contract-approval audit page pointing at the
    existing /admin/contract-approvers route. The label was
    renamed from 'Contract Approvers' to 'Contract Approval
    Access' on 2026-05-24 to clarify scope (tenant-wide, not
    per-contract); the URL was kept for backward compatibility."""
    src = _layout_src()
    assert "'/admin/contract-approvers'" in src
    assert "label: 'Contract Approval Access'" in src


def test_contract_approvals_nav_gated_by_approve_or_view_all():
    """The sidebar entry must mirror the App.js route permission
    set so a non-approver doesn't see a link that 403s when clicked."""
    src = _layout_src()
    # The route in App.js (line 405-417) gates on the union
    # [contracts.approve, contracts.view.all, contracts.view,
    # contracts.view.own]. The sidebar uses the stricter pair
    # so the link only appears for users who can take action;
    # users with read-only contracts.view can still hit the
    # page directly via URL.
    assert "permission: ['contracts.approve', 'contracts.view.all']" in src


def test_contract_approvers_nav_gated_by_user_perms():
    src = _layout_src()
    assert "permission: ['users.edit', 'users.view']" in src


def test_underlying_routes_still_mounted_in_app_js():
    """Regression guard: this test exists because the nav and the
    routes are maintained separately. If anyone removes the routes
    from App.js, the sidebar entries become dead links — this
    confirms the routes are still there."""
    app = _app_src()
    assert 'path="contracts/approval-queue"' in app
    assert 'path="admin/contract-approvers"' in app


def test_icon_imports_present():
    """The new sidebar entries reference FileCheck + Shield from
    lucide-react. Pin the imports."""
    src = _layout_src()
    # Both icons appear in the import block.
    import_section = src.split("} from 'lucide-react';", 1)[0]
    assert "FileCheck" in import_section
    assert "Shield" in import_section
