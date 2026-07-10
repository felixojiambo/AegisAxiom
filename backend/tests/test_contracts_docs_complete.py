"""Source-level lock on the Contracts runbook + smoke docs.

Three docs landed in this commit:

  docs/contracts-time-mgmt/scheduler.md       — manual trigger + future
                                                scheduler shape
  docs/contracts-time-mgmt/smoke_checklist.md — end-to-end smoke for
                                                every Contracts UI slice
  docs/contracts-time-mgmt/deferred_items.md  — approver overrides +
                                                Zoho status

These docs are operationally load-bearing — without them the
manual cadence is undocumented and operators have no reproducible
smoke for the new UI surfaces. This test locks the files exist and
that the canonical anchor sections are present so a future cleanup
can't quietly delete them.
"""

from pathlib import Path

import pytest


DOCS_ROOT = Path(__file__).resolve().parents[1].parent / "docs" / "contracts-time-mgmt"


def _doc(name: str) -> str:
    path = DOCS_ROOT / name
    if not path.exists():
        pytest.fail(f"Expected doc not found: docs/contracts-time-mgmt/{name}")
    return path.read_text(encoding="utf-8")


def test_scheduler_doc_documents_manual_trigger_and_scheduled_path():
    """The scheduler doc must document BOTH execution paths now:
    (a) the manual UI trigger,
    (b) the scheduler-callable POST /api/invoice-batches/build-biweekly/scheduled
        endpoint shipped alongside this commit, including the
        env var, header, fail-closed 503, and idempotency guarantee
    so an operator can stand up a Cloud Scheduler / GitHub Actions
    cron / n8n cron workflow without reading the route source."""
    src = _doc("scheduler.md")
    # Manual path covered.
    assert "POST /api/invoice-batches/build" in src, (
        "scheduler.md must document the canonical /build route."
    )
    assert "Finance → Invoice Batches" in src, (
        "scheduler.md must reference the sidebar nav entry so the "
        "manual cadence is discoverable."
    )
    assert "dry_run" in src, (
        "scheduler.md must document the dry_run option since the "
        "UI defaults to it being on."
    )
    # Scheduled path covered.
    assert "/build-biweekly/scheduled" in src, (
        "scheduler.md must document the scheduler-callable "
        "/build-biweekly/scheduled endpoint."
    )
    assert "INVOICE_BATCH_SCHEDULER_TOKEN" in src, (
        "scheduler.md must name the env var the scheduler endpoint "
        "reads its shared token from."
    )
    assert "X-Scheduler-Token" in src, (
        "scheduler.md must document the X-Scheduler-Token header "
        "the scheduler sends."
    )
    assert "scheduler_not_configured" in src or "503" in src, (
        "scheduler.md must document the fail-closed 503 response "
        "when the token env var is unset."
    )
    # External scheduler infra options are sketched.
    assert "Cloud Scheduler" in src or "Cloud Run" in src, (
        "scheduler.md must sketch at least one concrete external "
        "scheduler shape (Cloud Scheduler / GitHub Actions / n8n)."
    )
    # Idempotency guarantee called out — backend builder dedupes
    # on (contract_id, period_start) so re-running a period is safe.
    assert "idempotent" in src.lower(), (
        "scheduler.md must call out the idempotency guarantee for "
        "re-runs of the same period."
    )


def test_smoke_checklist_covers_every_contracts_ui_slice():
    """The smoke checklist must walk through every Contracts UI
    slice on this branch so a smoke run isn't missing surfaces.
    Drift between this list and what's actually shipped is a
    process problem that surfaces here loudly."""
    src = _doc("smoke_checklist.md")
    # Each major slice must have an anchor section in the doc.
    expected_anchors = (
        "approval panel",
        "approval queue",
        "Invoice batch approval",
        "Manual invoice-batch build",
        "Contract adjustments",
        "Contract billing triggers",
        "Project linking",
        "Indexes + migrations",
    )
    for anchor in expected_anchors:
        assert anchor.lower() in src.lower(), (
            f"smoke_checklist.md must cover the {anchor!r} smoke "
            "step — drift here means the checklist is missing a "
            "surface that exists in the UI."
        )
    # The doc must reference both migration files so an operator
    # running smoke against a fresh DB knows which migrations to apply.
    assert "2026_contracts_time_mgmt_schema.py" in src
    assert "2026_invoice_batches_wave5_indexes.py" in src


def test_deferred_items_documents_approver_overrides_and_zoho_status():
    """The deferred-items doc must capture the lifecycle of every
    item the workstream has touched — both the items still
    deferred (Zoho contract import) and the items previously
    deferred but now resolved (approver overrides, both layers;
    scheduler observability). A future workstream needs the
    audit history, not just a snapshot of what's still pending."""
    src = _doc("deferred_items.md")
    # Approver overrides — both layers shipped.
    assert "Approver overrides" in src, (
        "deferred_items.md must have an Approver overrides section."
    )
    assert "RESOLVED" in src, (
        "deferred_items.md must record that approver overrides are "
        "now resolved (both global tenant-wide via extras and "
        "per-contract via contract_approver_overrides)."
    )
    assert "extra_permissions" in src or "extras" in src, (
        "deferred_items.md must explain that the global override "
        "layer (Layer A) lives in user.extra_permissions / extras."
    )
    assert "contract_approver_overrides" in src, (
        "deferred_items.md must name the per-contract override "
        "collection so a future audit can trace the persistence "
        "shape back to the migration."
    )
    # Scheduler observability — shipped alongside this commit.
    assert "Scheduler observability" in src, (
        "deferred_items.md must record that scheduler observability "
        "was previously deferred but is now shipped on this branch."
    )
    assert "invoice_batch_scheduler_runs" in src, (
        "deferred_items.md must name the telemetry collection so a "
        "future workstream can find the persistence shape."
    )
    # Zoho import — still deferred.
    assert "Zoho" in src, (
        "deferred_items.md must have a Zoho import section."
    )
    assert "zoho_contract_import_mapping.md" in src, (
        "deferred_items.md must reference the existing mapping doc."
    )
    assert "external_contract_id" in src, (
        "deferred_items.md must call out the sparse-unique field "
        "the import will dedupe against."
    )
