"""Source-level lock on the sidebar nav entries surfacing the new
Contracts pages.

Two routes were shipped on this branch without a nav surface:

  /contracts/approval-queue   commit 77503122
  /finance/invoice-batches    commit 8f8c3107

Both were reachable by URL but invisible in the sidebar, so the
operational user had to know the path. This test locks the entries
that surface them in Layout.js so a future refactor that
reorganizes NAV_GROUPS doesn't silently drop them.

yarn / node_modules are unavailable in the build sandbox; the
contract is verified via source inspection — same pattern as the
other Contracts UI wiring tests on this branch.
"""

from pathlib import Path

import pytest


def _layout_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "Layout.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def test_approval_queue_nav_entry_exists_with_canonical_permission():
    """The Contract Approval Queue page must have a nav entry gated
    on contracts.approve — the same key the backend route gate uses
    (routes/contracts.py:746). Without this lock the entry can be
    silently dropped during a nav refactor and approvers lose
    discoverability."""
    src = _layout_src()
    # The entry path must be the canonical one mounted in App.js.
    assert "path: '/contracts/approval-queue'," in src, (
        "Layout NAV_GROUPS must include a /contracts/approval-queue "
        "entry — the approval queue page is otherwise reachable only "
        "by URL."
    )
    # Locate the entry block and confirm its label + permission.
    idx = src.find("path: '/contracts/approval-queue',")
    assert idx != -1
    block = src[idx:idx + 400]
    assert "label: 'Contract Approvals'" in block, (
        "Approval-queue nav entry must use the label "
        "'Contract Approvals'."
    )
    assert "permission: ['contracts.approve']" in block, (
        "Approval-queue nav entry must gate on the canonical "
        "contracts.approve permission key — the backend route uses "
        "the same key."
    )


def test_invoice_batches_nav_entry_exists_with_canonical_permissions():
    """The Invoice Batches page must have a nav entry gated by the
    same view-tier permission union the backend route checks
    (routes/invoice_batches.py:94-102). Permissions narrower than
    the backend would hide the entry from users who can access the
    page; broader would surface the entry to users who get 403."""
    src = _layout_src()
    assert "path: '/finance/invoice-batches'," in src, (
        "Layout NAV_GROUPS must include a /finance/invoice-batches "
        "entry — the invoice-batch approval page is otherwise "
        "reachable only by URL."
    )
    idx = src.find("path: '/finance/invoice-batches',")
    assert idx != -1
    block = src[idx:idx + 500]
    assert "label: 'Invoice Batches'" in block
    # Each canonical view-tier key must be in the permission union.
    for key in (
        "'invoice_batches.view'",
        "'invoice_batches.approve'",
        "'invoice_batches.build'",
        "'finance.portal'",
        "'contracts.view.all'",
    ):
        assert key in block, (
            f"Invoice-batches nav entry must include {key} in its "
            "permission union — the backend route gates on the same "
            "set."
        )


def test_approval_queue_nav_entry_lives_under_operations_group():
    """Approval queue belongs to Operations alongside /contracts and
    /operations/contracts. Surfacing it under Finance or Sales would
    be discoverability-confusing."""
    src = _layout_src()
    operations_idx = src.find("id: 'operations',")
    finance_idx = src.find("id: 'finance',")
    queue_idx = src.find("path: '/contracts/approval-queue',")
    assert operations_idx != -1 and finance_idx != -1 and queue_idx != -1
    # The approval-queue entry must be between the Operations group
    # marker and the Finance group marker.
    assert operations_idx < queue_idx < finance_idx, (
        "Approval-queue nav entry must live inside the Operations "
        "NAV_GROUPS section, not Finance or any group below."
    )


def test_invoice_batches_nav_entry_lives_under_finance_group():
    """Invoice batches belong to Finance alongside the Financial
    Command Center entry. Operators look for billing surfaces under
    Finance, not Operations."""
    src = _layout_src()
    finance_idx = src.find("id: 'finance',")
    invoice_idx = src.find("path: '/finance/invoice-batches',")
    assert finance_idx != -1 and invoice_idx != -1
    # The entry must come AFTER the finance group marker; the next
    # group marker after it should be 'ai' (per NAV_GROUPS order).
    assert finance_idx < invoice_idx, (
        "Invoice-batches nav entry must live inside the Finance "
        "NAV_GROUPS section."
    )
    next_group_idx = src.find("id: 'ai',")
    if next_group_idx != -1:
        assert invoice_idx < next_group_idx, (
            "Invoice-batches nav entry must come before the AI "
            "group marker so it stays inside the Finance group."
        )
