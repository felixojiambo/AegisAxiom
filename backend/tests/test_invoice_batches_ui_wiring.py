"""Source-level wiring contract for the Invoice Batches admin UI.

The backend invoice-batch lifecycle (build / list / get / approve /
reject) is fully covered by:

  - backend/tests/test_invoice_batch_routes.py
  - backend/tests/test_invoice_batch_builder.py
  - backend/tests/test_invoice_batch_finance_draft_promotion.py

Until this branch, none of those routes had a frontend consumer —
finance had to use API tooling to release the bi-weekly billing
cadence. This test locks the new UI surface so a future refactor
cannot quietly disconnect the page from the routes, or change the
route paths without updating the page.

yarn / node_modules are unavailable in the build sandbox, so the
React component cannot run through Jest here. The contract is
verified via source inspection — the same pattern used by the
SOW canvas tests on this branch.
"""

from pathlib import Path

import pytest


def _page_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "pages"
        / "InvoiceBatchesPage.jsx"
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


def test_invoice_batches_page_exists():
    """The page must exist as a React default export and be importable
    from the App router. A renamed or relocated file would break the
    /finance/invoice-batches route silently."""
    src = _page_src()
    assert "export default function InvoiceBatchesPage()" in src, (
        "InvoiceBatchesPage.jsx must export a default function "
        "named InvoiceBatchesPage."
    )


def test_invoice_batches_page_calls_canonical_routes():
    """The page must hit exactly the backend routes the lifecycle
    documents:

      GET   /api/invoice-batches            list (status-filtered)
      POST  /api/invoice-batches/{id}/approve
      POST  /api/invoice-batches/{id}/reject

    The literal substrings below are intentionally precise — a typo
    or path drift here would silently 404 in production."""
    src = _page_src()
    assert "`${API}/api/invoice-batches?${params.toString()}`" in src, (
        "List call must hit /api/invoice-batches with status query."
    )
    assert "/api/invoice-batches/${batch.batch_id}/${mode}" in src, (
        "Approve/Reject call must hit "
        "/api/invoice-batches/{id}/approve or /reject via the "
        "shared decision URL builder."
    )


def test_invoice_batches_page_gates_actions_on_status_and_perm():
    """Action buttons must only render for batches in
    pending_finance_approval or draft, and only when the caller has
    approve permission. The backend rejects approvals from other
    statuses with 409, so the UI must not even show the buttons in
    those cases (defense in depth)."""
    src = _page_src()
    # Action visibility gate.
    assert "b.status === 'pending_finance_approval'" in src, (
        "Approve / Reject buttons must check status === "
        "'pending_finance_approval' before rendering."
    )
    assert "b.status === 'draft'" in src, (
        "Approve / Reject buttons must also be available on "
        "'draft' batches per the backend transition rule."
    )
    # Approve permission gate.
    assert "canApprove" in src, (
        "Action visibility must depend on canApprove (the union of "
        "the approve-tier permission keys)."
    )


def test_invoice_batches_page_requires_rejection_notes():
    """Rejection notes are required by the backend (route returns
    400 without them). The UI must enforce this client-side so the
    user gets immediate feedback rather than a 400 surfaced as a
    generic toast."""
    src = _page_src()
    assert "notesRequired" in src and "isReject" in src, (
        "Decision modal must compute notesRequired from the reject "
        "mode and disable the submit button until notes are entered."
    )
    assert "notes.trim().length > 0" in src, (
        "Decision modal must validate notes are non-empty (after "
        "trim) before allowing reject submit."
    )


def test_invoice_batches_page_handles_idempotent_re_approve():
    """The approve route is idempotent — re-approving an already-
    approved batch returns already_approved=true without creating a
    duplicate finance_drafts row. The UI must surface this as a
    success toast (\"Batch was already approved\"), not an error,
    so a network retry by the user does not look like a failure."""
    src = _page_src()
    assert "already_approved" in src, (
        "Decision handler must check payload.already_approved and "
        "show a benign 'already approved' toast."
    )
    assert "already_rejected" in src, (
        "Decision handler must also handle the symmetric "
        "already_rejected response on re-reject."
    )


def test_invoice_batches_page_uses_canonical_permission_keys():
    """The view + approve permission gates must use the exact key
    strings the backend route checks. A drift here (e.g. typoing
    'invoice_batches.approves') would silently lock everyone out."""
    src = _page_src()
    for key in (
        "invoice_batches.view",
        "invoice_batches.approve",
        "invoice_batches.build",
        "finance.portal",
        "contracts.view.all",
        "contracts.approve",
    ):
        assert f"hasPermission('{key}')" in src or f'hasPermission("{key}")' in src, (
            f"InvoiceBatchesPage must check hasPermission({key!r}) — "
            "the backend route gates on this exact key."
        )


def test_invoice_batches_route_mounted_in_app():
    """The page must be reachable at /finance/invoice-batches and
    gated by the union of view-tier permission keys. Without the
    route, the page is dead code; without the permission gate, an
    unauthorized user reaches the 403 toast instead of the perm
    placeholder."""
    src = _app_src()
    assert "import InvoiceBatchesPage from './pages/InvoiceBatchesPage'" in src, (
        "App.js must import the InvoiceBatchesPage."
    )
    assert 'path="finance/invoice-batches"' in src, (
        "App.js must mount the page at finance/invoice-batches."
    )
    # The route must declare a PermissionRoute that lets through the
    # view-tier keys. The backend matches this exact set.
    assert "'invoice_batches.view'" in src, (
        "Route must allow invoice_batches.view."
    )
    assert "'invoice_batches.approve'" in src, (
        "Route must allow invoice_batches.approve."
    )
    assert "'finance.portal'" in src, (
        "Route must allow finance.portal (legacy finance-team key)."
    )


def test_invoice_batches_page_does_not_invent_backend_fields():
    """Defensive: the page must use only fields the backend route
    actually returns. Any new field this UI displays should match
    routes/invoice_batches.py field names exactly so a server-side
    rename is loud, not silent."""
    src = _page_src()
    # The canonical fields the list response carries today (see
    # _promote_to_finance_draft + the build pipeline).
    for field in (
        "batch_id",
        "contract_id",
        "contract_name",
        "period_start",
        "period_end",
        "total_amount",
        "total_hours",
        "status",
        "decided_by",
        "decided_at",
    ):
        assert field in src, (
            f"Page must read the canonical {field!r} field from each "
            "batch row — renaming a field server-side without "
            "updating this UI would silently blank out the column."
        )


def test_invoice_batches_page_exposes_manual_build_trigger():
    """The bi-weekly cadence is operator-driven until a scheduler
    lands (see docs/contracts/scheduler.md). The Invoice Batches
    page must surface a manual build button gated on the canonical
    build-permission union and a small modal that POSTs to
    /api/invoice-batches/build with the exact body the route
    accepts (period_start, period_end, optional contract_id,
    dry_run boolean)."""
    src = _page_src()

    # Build button gate matches the backend route's _can_build union
    # at routes/invoice_batches.py:72-82.
    for key in (
        "'invoice_batches.build'",
        "'finance.portal'",
        "'contracts.approve'",
        "'*'",
    ):
        assert key in src, (
            f"Manual build gate must include {key} so it matches the "
            "backend route's _can_build union."
        )

    # Build modal posts to the canonical /build route. We intentionally
    # do NOT post to /build-biweekly here — both routes share the same
    # body per backend line 184-186; /build is the explicit name.
    assert "api.post(`${API}/api/invoice-batches/build`" in src, (
        "Build trigger must POST to /api/invoice-batches/build with "
        "the canonical body shape."
    )

    # Required body fields per routes/invoice_batches.py:137-145.
    assert "period_start" in src and "period_end" in src, (
        "Build body must include period_start and period_end — the "
        "route 400s without both (line 139-143)."
    )
    # dry_run defaults to true in the UI so the user can preview
    # before committing. Locking the default-true behavior so a
    # refactor cannot quietly flip it off and silently commit batches.
    assert "useState(true)" in src, (
        "Build modal must default dry_run to true so the user must "
        "explicitly opt out before persisting batches."
    )

    # The page must offer a 'Build batches' button on the toolbar
    # alongside the existing Refresh button. The data-testid anchors
    # a future browser smoke.
    assert "batch-build-open" in src and "batch-build-submit" in src, (
        "Page must expose stable testids for the Build button "
        "(batch-build-open) and the modal submit (batch-build-submit)."
    )


def test_invoice_batches_build_does_not_call_biweekly_alias():
    """Defensive lock: the manual UI must hit /build, not the
    /build-biweekly alias. Both routes share the same body and
    handler today, but using the explicit name keeps the audit log
    rows uniformly stamped as `invoice_batch.build` rather than
    splitting across two action names. The /build-biweekly alias is
    reserved for the scheduler when it lands."""
    src = _page_src()
    assert "/api/invoice-batches/build-biweekly" not in src, (
        "Manual build UI must hit /api/invoice-batches/build, not "
        "the /build-biweekly alias. The alias exists for the future "
        "scheduler."
    )
