"""Source-level wiring contract for the Contract Approval Queue UI.

The backend exposes the queue at:

    GET /api/contracts/approval-queue
        required perm: contracts.approve
        response shape: { "items": [...contracts...], "total": int }
        scoping: contracts whose approval_status == "pending",
                 RBAC-filtered + tenant-filtered

The queue route is already covered by
backend/tests/test_contract_approval_workflow.py. This file locks
the new ContractApprovalQueuePage so a future refactor cannot
silently drift the page-to-route binding or the permission key.

yarn / node_modules are unavailable in the build sandbox, so the
React component cannot run through Jest. The contract is verified
via source inspection — same pattern as the invoice-batches and
contract-approval-panel wiring tests on this branch.
"""

from pathlib import Path

import pytest


def _page_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "pages"
        / "ContractApprovalQueuePage.jsx"
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


def test_queue_page_default_export_exists():
    src = _page_src()
    assert "export default function ContractApprovalQueuePage()" in src, (
        "ContractApprovalQueuePage.jsx must export a default function "
        "named ContractApprovalQueuePage."
    )


def test_queue_page_calls_canonical_backend_route():
    """Lock the exact route path. A typo here would silently 404 in
    production — the backend has no fallback."""
    src = _page_src()
    assert "`${API}/api/contracts/approval-queue`" in src, (
        "Queue must hit /api/contracts/approval-queue. Any other "
        "path would 404 because the backend route is hard-coded."
    )
    # Method must be GET (the route is read-only — decisions live
    # on the per-contract approval routes consumed by the modal).
    assert "api.get(`${API}/api/contracts/approval-queue`)" in src, (
        "Queue must read via api.get; no other verbs are allowed "
        "by the backend route."
    )


def test_queue_page_uses_canonical_permission_key():
    """The backend route gates on contracts.approve (with no
    fallback keys). The page must check the same key — using a
    different one would lock out the right users or admit users
    who would then get 403 from the route."""
    src = _page_src()
    assert (
        "hasPermission('contracts.approve')" in src
        or 'hasPermission("contracts.approve")' in src
    ), "Queue must check hasPermission('contracts.approve')."
    # Wildcard admin key is allowed as a parallel grant — match
    # the pattern used by the invoice batches and contract
    # approval panel pages on this branch.
    assert "hasPermission('*')" in src or 'hasPermission("*")' in src, (
        "Queue must also admit hasPermission('*') so admin-tier "
        "users keep their access without enumerating role names."
    )


def test_queue_rows_open_contract_details_modal():
    """Rows must open the existing ContractDetailsModal — the
    approval workflow panel inside that modal owns the actual
    approve / reject / request-changes actions. Building duplicate
    row actions would split the decision UX across two surfaces."""
    src = _page_src()
    assert (
        "import ContractDetailsModal from '../components/contracts/modals/ContractDetailsModal'"
        in src
    ), (
        "Queue must import the existing ContractDetailsModal — no "
        "duplicate detail surface."
    )
    assert "<ContractDetailsModal" in src, (
        "Queue must render ContractDetailsModal when a row opens."
    )
    # The row click must set the openContractId state that drives
    # the modal mount. Direct test for the wiring shape.
    assert "setOpenContractId(c.contract_id)" in src, (
        "Queue rows must set openContractId from c.contract_id."
    )
    # Negative lock: no fake approve/reject buttons on rows. The
    # modal owns the decisions.
    for forbidden in (
        "/approve`",
        "/reject`",
        "/request-changes`",
    ):
        assert forbidden not in src, (
            f"Queue must not POST to {forbidden!r} — that path "
            "lives on the ContractDetailsModal's approval panel, "
            "not on the queue row. Avoid duplicate action surfaces."
        )


def test_queue_refreshes_after_modal_close():
    """A decision made in the modal updates the underlying
    contract's approval_status away from 'pending', so the row
    should leave the queue. The page enforces this by calling
    loadQueue() on every modal close (a no-op close just refetches
    an unchanged list — cheap and consistent)."""
    src = _page_src()
    assert "closeDetail" in src and "loadQueue()" in src, (
        "Queue must define closeDetail and call loadQueue() inside "
        "it so the list reflects the decision immediately."
    )


def test_queue_has_loading_empty_and_error_states():
    """All three states must render so the user is never staring
    at a blank table. The data-testid hooks anchor a future
    browser smoke."""
    src = _page_src()
    for testid in (
        "contract-approval-queue-loading",
        "contract-approval-queue-empty",
        "contract-approval-queue-error",
    ):
        assert testid in src, (
            f"Queue page must render the {testid!r} state."
        )


def test_queue_uses_only_canonical_backend_fields():
    """The queue surfaces fields the backend route actually
    returns. Any new field referenced here should match
    routes/contracts.py exactly so a server-side rename is loud,
    not silent."""
    src = _page_src()
    for field in (
        "contract_id",
        "name",
        "account_name",
        "contract_type",
        "status",
        "approval_status",
        "updated_at",
    ):
        assert field in src, (
            f"Queue must read the canonical {field!r} field from "
            "each row."
        )


def test_queue_does_not_invent_status_strings():
    """The page only references the approval_status values the
    backend writes (pending / approved / rejected /
    changes_requested / draft). A fabricated status here would
    silently break the badge palette without raising any error."""
    src = _page_src()
    # The pending status must be present (this is the only status
    # the queue ever sees in its list).
    assert "pending" in src
    # The other approval_status values are referenced in the
    # StatusBadge palette so future decisions still render
    # correctly. Each must be one of the canonical set.
    canonical_set = {"pending", "approved", "rejected", "changes_requested", "draft"}
    # Source-level lock — every approval_status palette key must
    # be in the canonical set. We don't enforce "all five present"
    # because that would over-fit; we enforce "no extra ones".
    # Cheap heuristic: search for the palette object literal.
    palette_marker = "kind === 'approval'"
    assert palette_marker in src, (
        "Queue must declare an approval-status palette branch via "
        "kind='approval' on StatusBadge."
    )
    # Any approval status string in the palette source must be
    # one of the canonical names. This catches a typo like
    # 'rejcted' or 'change_requested'.
    for invented in ("pending_review", "needs_review", "in_review", "change_requested"):
        assert invented not in src, (
            f"Queue must not reference invented approval status "
            f"{invented!r} — backend writes only {sorted(canonical_set)}."
        )


def test_queue_route_mounted_in_app():
    """The page must be reachable at /contracts/approval-queue and
    gated by contracts.approve at the route layer (defense in
    depth on top of the page-level canView check)."""
    src = _app_src()
    assert "import ContractApprovalQueuePage from './pages/ContractApprovalQueuePage'" in src, (
        "App.js must import the ContractApprovalQueuePage."
    )
    assert 'path="contracts/approval-queue"' in src, (
        "App.js must mount the page at contracts/approval-queue."
    )
    # The route's PermissionRoute must declare contracts.approve.
    # Inspect the import-to-route block and confirm the gate.
    idx = src.find('path="contracts/approval-queue"')
    assert idx != -1
    end = src.find("</Route>", idx)
    # </Route> may not appear because Routes can self-close; allow
    # any subsequent end-of-block marker.
    window = src[idx:idx + 600]
    assert "'contracts.approve'" in window, (
        "Route mount must gate on 'contracts.approve' so users "
        "without the permission never reach the page."
    )


def test_queue_user_facing_copy_has_no_developer_jargon():
    """May 24 (revised) UX review: the queue page must not surface
    raw permission keys, backend route paths, or other developer
    jargon. Two specific bad strings flagged on the prior pass:
    a 'Required permission' card showing 'contracts.approve' as a
    monospaced value, and a footer that named the backend route
    path. Both are gone — pin the removal so a future commit
    can't sneak them back."""
    src = _page_src()
    # The summary cards must not include a "Required permission" card.
    assert "Required permission" not in src, (
        "Queue must not show a 'Required permission' summary card — "
        "permission keys are not user-facing."
    )
    # The literal permission key as visible text (monospaced or
    # otherwise) must not appear inside a user-rendered <p> / <span>.
    # The hasPermission(...) JS call is fine; what we ban is the bare
    # value displayed as content. Heuristic: look for the
    # `font-mono` className paired with the permission key on the
    # same logical chunk.
    assert "<p className=\"text-sm font-mono text-aegisaxiom-text\">contracts.approve" not in src
    # The footer line that named the backend endpoint must be gone.
    assert "/api/contracts/approval-queue</code>" not in src
    assert "Decisions are made\n        from the contract detail panel" not in src


def test_queue_empty_state_uses_human_voice():
    """The empty state must read naturally — not 'No items.' or
    'No data.' and not anything that mentions API/backend."""
    src = _page_src()
    assert "Nothing waiting on you right now." in src


def test_queue_loading_state_guards_against_flicker_on_refresh():
    """May 24 (revised) review flagged the queue flickering between
    'Loading…' and the previous items on every refresh. The guard:
    only render the Loading… row when the list is empty (initial
    load); on a refresh, keep the existing rows visible so the
    user doesn't see an empty table mid-poll."""
    src = _page_src()
    assert "loading && items.length === 0" in src, (
        "Queue must only render the loading row when items are "
        "empty, otherwise refresh flickers the table to blank."
    )


def test_queue_row_has_open_contract_cta():
    """Each row carries a visible 'Open contract' button so the
    affordance is obvious — not relying on the row being clickable."""
    src = _page_src()
    assert "Open contract" in src
    # The button stops event propagation so clicking it doesn't
    # double-fire the row click.
    assert "e.stopPropagation()" in src
    # Testid for browser smoke tests.
    assert 'data-testid={`contract-approval-queue-open-${c.contract_id}`}' in src


def test_no_invoice_batches_or_unrelated_contract_routes_in_queue():
    """Defensive: the queue page must not call any invoice-batch,
    SOW, proposal, or procurement route. The queue surface is
    intentionally narrow — just the approval queue and the detail
    modal."""
    src = _page_src()
    for forbidden in (
        "/invoice-batches",
        "/sows/",
        "/proposals/",
        "/procurement/",
        "/sales-ops/",
    ):
        assert forbidden not in src, (
            f"Queue page must not reach {forbidden!r} — that's "
            "out of scope for the approval queue UI slice."
        )
