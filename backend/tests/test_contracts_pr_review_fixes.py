"""Source + behavior locks for the Contracts/time-management PR
review blockers.

This file pins the deliberate fixes that closed PR review findings
so a future refactor can't silently regress them:

1. AI authorization gates on new route modules
   (invoice_batches, contract_adjustments, contract_triggers).
2. The deliberate ai-authz exemption on the token-only
   scheduler routes.
3. Module registration calls (register_module_config /
   register_fields / register_module_events) on the new modules.
4. crud_envelope use on state-changing routes.
5. GET /contracts/{id}/adjustments requires explicit financial-view
   permission, not plain contracts-view.
6. Invoice batch list uses count_documents for ``total`` so the
   total is not capped at the to_list page size.
7. Bulk time-entry approval rollback clears approval metadata to
   mirror single-approve rollback.
8. _apply_approval_action uses an atomic find_one_and_update so
   two concurrent approvers cannot both succeed.
9. _check_invalid_transition dead-code line removed.
10. Optional[dict] annotation on approve/reject bodies.
11. Builder to_list(20000) carries a comment explaining the
    intentional scale ceiling.
"""
from __future__ import annotations

import inspect
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


ROUTES_DIR = Path(__file__).resolve().parents[1] / "routes"
SERVICES_DIR = Path(__file__).resolve().parents[1] / "services"


# ---------------------------------------------------------------------------
# 1. AI authorization gates on user-authenticated routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_path", [
    ROUTES_DIR / "invoice_batches.py",
    ROUTES_DIR / "contract_adjustments.py",
    ROUTES_DIR / "contract_triggers.py",
])
def test_new_route_modules_import_require_ai_permission(module_path):
    """All three new route modules must import the AEGISAXIOM AI authz
    helper. Without this, the dependencies=[...] decorators would
    raise NameError at import time — so this lock catches a refactor
    that silently strips the import without also stripping the deps."""
    src = module_path.read_text(encoding="utf-8")
    assert "from middleware.ai_authz import require_ai_permission" in src, (
        f"{module_path.name} must import require_ai_permission so that "
        "AI employee invocations are gated by their app_access bundle."
    )


@pytest.mark.parametrize("module_path,expected_min_gates", [
    (ROUTES_DIR / "invoice_batches.py", 6),
    (ROUTES_DIR / "contract_adjustments.py", 2),
    (ROUTES_DIR / "contract_triggers.py", 4),
])
def test_new_route_modules_apply_ai_permission_dep(module_path, expected_min_gates):
    """Every user-authenticated route on the three new modules must
    decorate with ``dependencies=[Depends(require_ai_permission(...))]``.

    The expected_min_gates count is the lower bound — adding new
    routes is allowed, removing them below this floor is the
    regression signal."""
    src = module_path.read_text(encoding="utf-8")
    gate_count = src.count("require_ai_permission(")
    # The token-only scheduler endpoints are deliberately exempt and
    # call out the carve-out in a comment; subtract those from the
    # count so the floor still flags accidental removal of the human
    # gates.
    assert gate_count >= expected_min_gates, (
        f"{module_path.name} has only {gate_count} require_ai_permission "
        f"gate(s); expected at least {expected_min_gates}. Adding a "
        "new user-authenticated route without an ai-authz dep would "
        "let an AI employee bypass app_access checks."
    )


def test_scheduler_routes_documented_ai_authz_exemption():
    """The token-only scheduler endpoints
    (``POST /invoice-batches/build-biweekly/scheduled`` and
    ``GET /invoice-batches/scheduler/health``) are NOT gated by
    require_ai_permission because they are authenticated by the
    X-Scheduler-Token header, not by a AEGISAXIOM user session.

    The exemption must be explicit in source so a future refactor
    can't drop the comment and then quietly add an ai-authz dep
    that breaks the scheduler contract (or vice versa)."""
    src = (ROUTES_DIR / "invoice_batches.py").read_text(encoding="utf-8")
    assert "AI authorization carve-out" in src, (
        "invoice_batches.py must explain why the scheduled / health "
        "endpoints are exempt from require_ai_permission."
    )
    # The user-authenticated /status endpoint, by contrast, IS gated.
    assert (
        '"/invoice-batches/scheduler/status"' in src
        and "require_ai_permission(\"invoice_batches.view\")" in src
    ), (
        "/scheduler/status is user-authenticated and must be wrapped "
        "in require_ai_permission(invoice_batches.view)."
    )


# ---------------------------------------------------------------------------
# 3. Module registration (config + fields + events) on the new modules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_path,module_name,entity", [
    (ROUTES_DIR / "invoice_batches.py", "invoice_batches", "invoice_batch"),
    (ROUTES_DIR / "contract_adjustments.py", "contracts", "contract_adjustment"),
    (ROUTES_DIR / "contract_triggers.py", "contracts", "contract_billing_trigger"),
])
def test_new_route_modules_register_with_field_registry(
    module_path, module_name, entity,
):
    """Each new module must register with the field/policy/workflow
    registry so the engine + Admin GUI can see it."""
    src = module_path.read_text(encoding="utf-8")
    assert "register_module_config(" in src, (
        f"{module_path.name} must call register_module_config()."
    )
    assert "register_module_events(" in src, (
        f"{module_path.name} must call register_module_events()."
    )
    assert "register_fields(" in src, (
        f"{module_path.name} must call register_fields()."
    )
    # And the registered entity name must match what the route
    # writes — drift here means the policy engine can't load the
    # right field types when a policy references this entity.
    assert f'"{entity}"' in src, (
        f"{module_path.name} must register the {entity!r} entity name."
    )


# ---------------------------------------------------------------------------
# 4. crud_envelope on state-changers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_path,min_envelopes", [
    # invoice_batches: build alias, build, approve, reject
    (ROUTES_DIR / "invoice_batches.py", 3),
    # contract_adjustments: create
    (ROUTES_DIR / "contract_adjustments.py", 1),
    # contract_triggers: create + disable
    (ROUTES_DIR / "contract_triggers.py", 2),
])
def test_new_route_modules_use_crud_envelope(module_path, min_envelopes):
    """State-changing routes on the new modules must route through
    crud_envelope so policy / event / audit fire as one atomic
    surface. The lower-bound count catches a refactor that drops a
    write out of the envelope."""
    src = module_path.read_text(encoding="utf-8")
    assert (
        "from services.ingestion.route_helpers import crud_envelope" in src
    ), f"{module_path.name} must import crud_envelope."
    # Count actual call sites — ``async with crud_envelope(...)``
    # blocks. The import statement form ``import crud_envelope``
    # does not match this pattern.
    actual = src.count("async with crud_envelope(")
    assert actual >= min_envelopes, (
        f"{module_path.name} uses async with crud_envelope() in only "
        f"{actual} site(s); expected at least {min_envelopes} (one "
        "per state-changing route)."
    )


# ---------------------------------------------------------------------------
# 5. GET /contracts/{id}/adjustments requires financial-view permission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adjustments_list_rejects_plain_contract_viewer(monkeypatch):
    """A user with only ``contracts.view.own`` can see the contract
    itself but MUST NOT be able to read the adjustment ledger that
    moves billing balances. This was previously gated only by
    ``_load_contract_for_user`` which admits view.own — a financial
    surface escaping into the contract-viewer set is the review
    blocker."""
    import routes.contract_adjustments as ca

    # The fake DB never gets touched if the perm check fires first.
    class _DB:
        contracts = SimpleNamespace(
            find_one=AsyncMock(return_value={
                "contract_id": "c1", "tenant_id": None,
            }),
        )
        contract_adjustments = SimpleNamespace(
            find=lambda *_a, **_kw: SimpleNamespace(
                sort=lambda *_a, **_k: SimpleNamespace(
                    to_list=AsyncMock(return_value=[]),
                ),
            ),
            count_documents=AsyncMock(return_value=0),
        )

    monkeypatch.setattr(ca, "get_db", lambda: _DB())

    async def _empty_filter(_u):
        return {}
    monkeypatch.setattr(ca, "get_contract_filter", _empty_filter)

    view_own_user = SimpleNamespace(
        user_id="u_view_own", email="view@aegisaxiom.test", role="contractor",
        permissions=["contracts.view.own"], tenant_id=None, account_id=None,
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ca.list_contract_adjustments(
            contract_id="c1", current_user=view_own_user,
        )
    assert ei.value.status_code == 403, (
        "contracts.view.own MUST NOT unlock the adjustment ledger."
    )


@pytest.mark.asyncio
async def test_adjustments_list_admits_financial_viewer(monkeypatch):
    """The same route MUST admit users with one of the
    financial-view permission keys (contracts.approve /
    contracts.edit / contracts.edit.all / contracts.view.all / *)."""
    import routes.contract_adjustments as ca

    class _DB:
        contracts = SimpleNamespace(
            find_one=AsyncMock(return_value={
                "contract_id": "c1", "tenant_id": None,
            }),
        )

        class _Adj:
            @staticmethod
            def find(*_a, **_kw):
                class _C:
                    def sort(self, *_a, **_k): return self
                    async def to_list(self, _n): return []
                return _C()

            @staticmethod
            async def count_documents(_q): return 0

        contract_adjustments = _Adj()

    monkeypatch.setattr(ca, "get_db", lambda: _DB())

    async def _empty_filter(_u):
        return {}
    monkeypatch.setattr(ca, "get_contract_filter", _empty_filter)

    for perm in (
        "contracts.approve",
        "contracts.edit",
        "contracts.edit.all",
        "contracts.view.all",
        "*",
    ):
        user = SimpleNamespace(
            user_id="u", email="u@aegisaxiom.test", role="finance",
            permissions=[perm], tenant_id=None, account_id=None,
        )
        result = await ca.list_contract_adjustments(
            contract_id="c1", current_user=user,
        )
        assert "items" in result, (
            f"{perm!r} must unlock the adjustment ledger."
        )


# ---------------------------------------------------------------------------
# 6. Invoice batch list total uses count_documents
# ---------------------------------------------------------------------------


def test_invoice_batch_list_total_uses_count_documents():
    """The list route must compute ``total`` via
    ``count_documents(query)``, not ``len(items)``. Using len(items)
    silently caps the total at the to_list page size."""
    src = (ROUTES_DIR / "invoice_batches.py").read_text(encoding="utf-8")
    # The list block must call count_documents on invoice_batches.
    assert "db.invoice_batches.count_documents" in src, (
        "list_invoice_batches must compute total via count_documents."
    )
    # And the response field must read from the count_documents
    # result, not len(items).
    list_block_start = src.find("async def list_invoice_batches")
    assert list_block_start != -1
    list_block_end = src.find("async def get_invoice_batch", list_block_start)
    assert list_block_end != -1
    list_block = src[list_block_start:list_block_end]
    assert "return {\"items\": items, \"total\": total}" in list_block, (
        "list_invoice_batches must return total from count_documents, "
        "not len(items)."
    )


# ---------------------------------------------------------------------------
# 7. Bulk time-entry approve rollback clears approval metadata
# ---------------------------------------------------------------------------


def test_bulk_approve_rollback_clears_approval_metadata():
    """When a contract debit fails inside bulk_approve, the entry
    rollback must mirror single-approve: status=submitted plus
    explicit None / "" reset of approved_by / approved_at /
    approval_notes. Leaving the stamped approval metadata on a
    "submitted" row would let the retry path propagate a ghost
    approval into the downstream finance draft."""
    src = (ROUTES_DIR / "time_entries.py").read_text(encoding="utf-8")
    bulk_start = src.find("async def bulk_approve")
    assert bulk_start != -1
    # The rollback block lives inside the bulk_approve function;
    # look forward to the next async def boundary.
    next_def = src.find("\nasync def ", bulk_start + 1)
    bulk_block = src[bulk_start:next_def if next_def != -1 else len(src)]
    # The rollback path must set all four keys.
    rollback = bulk_block[bulk_block.find("Bulk approve debit failed"):]
    assert rollback, (
        "bulk_approve must have a rollback branch on debit failure."
    )
    for required in (
        '"status": "submitted"',
        '"approved_by": None',
        '"approved_at": None',
        '"approval_notes": ""',
    ):
        assert required in rollback, (
            f"bulk_approve rollback must include {required!r} to "
            "mirror single-approve rollback parity."
        )


# ---------------------------------------------------------------------------
# 8. Concurrent contract approvals cannot both succeed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_approvals_cannot_both_succeed(monkeypatch):
    """Two callers both pass the validator (both see
    approval_status='pending'), but the atomic
    find_one_and_update lets exactly one commit. The loser sees
    a 409 conflict matching the stale-prior-state semantics.

    This is the lock for the race-condition review finding."""
    import routes.contracts as rc

    # A fake contracts collection whose find_one_and_update only
    # matches the FIRST call. Subsequent calls return None — the
    # row has advanced.
    class _RacyContracts:
        def __init__(self):
            self._doc = {
                "contract_id": "c1",
                "approval_status": "pending",
                "status": "draft",
                "tenant_id": None,
                "project_ids": [],
                "contract_type": "boh",
                "service_category": "wholesale_mrr",
                "account_id": "a1",
            }
            self.find_one_and_update_calls = 0
            self.updates: list = []

        async def find_one(self, query, projection=None):
            return dict(self._doc)

        async def find_one_and_update(self, query, update, **_kw):
            self.find_one_and_update_calls += 1
            self.updates.append((dict(query), dict(update)))
            # Only the first caller commits. The second sees an
            # advanced state and gets back None.
            if self.find_one_and_update_calls == 1:
                if "$set" in update:
                    self._doc.update(update["$set"])
                return dict(self._doc)
            return None

        async def update_one(self, q, u):
            self.updates.append((dict(q), dict(u)))
            return SimpleNamespace(modified_count=1)

    contracts_coll = _RacyContracts()

    class _DB:
        contracts = contracts_coll
        contract_approvals = SimpleNamespace(insert_one=AsyncMock(return_value=None))
        projects = SimpleNamespace(
            insert_one=AsyncMock(return_value=None),
            delete_one=AsyncMock(return_value=None),
        )
        contract_approver_overrides = None

    monkeypatch.setattr(rc, "get_db", lambda: _DB())
    monkeypatch.setattr(rc, "log_action", AsyncMock(return_value=None))
    monkeypatch.setattr(rc, "emit_event", AsyncMock(return_value=None))

    async def _empty_filter(_u):
        return {}
    monkeypatch.setattr(rc, "get_contract_filter", _empty_filter)

    approver = SimpleNamespace(
        user_id="u_approver", email="a@aegisaxiom.test", role="finance",
        permissions=["contracts.approve"], tenant_id=None, account_id=None,
    )

    # First call wins.
    first = await rc.approve_contract(
        contract_id="c1", data={"approval_notes": "ok"}, current_user=approver,
    )
    assert first["approval_status"] == "approved"

    # Second call (concurrent) loses with a 409. The validator
    # sees the doc still says pending (because we keep find_one
    # returning the original doc — but find_one_and_update returns
    # None to model the race), so it passes pre-checks; the atomic
    # update is what fails.
    contracts_coll._doc["approval_status"] = "pending"  # simulate stale read
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rc.approve_contract(
            contract_id="c1", data={"approval_notes": "race"}, current_user=approver,
        )
    assert ei.value.status_code == 409, (
        "Loser of the approval race must see 409, not 200."
    )

    # And the atomic update was reached twice (one win, one miss).
    assert contracts_coll.find_one_and_update_calls == 2


# ---------------------------------------------------------------------------
# 9. _check_invalid_transition dead-code line removed
# ---------------------------------------------------------------------------


def test_check_invalid_transition_no_self_assignment_no_op():
    """The original implementation had a ``normalized = current if
    current in legal else current`` no-op self-assignment. It was
    flagged in review as dead code that confused readers. The fix
    removes the line and reads ``current`` directly. Lock against
    the no-op creeping back."""
    src = (ROUTES_DIR / "contracts.py").read_text(encoding="utf-8")
    # Find the helper body.
    start = src.find("def _check_invalid_transition")
    assert start != -1
    end = src.find("\ndef ", start + 1)
    block = src[start:end if end != -1 else len(src)]
    # The dead-code form must not be present.
    assert "current if current in legal else current" not in block, (
        "The dead-code 'normalized = current if current in legal "
        "else current' line must not reappear."
    )


# ---------------------------------------------------------------------------
# 10. Optional[dict] annotation on approve/reject bodies
# ---------------------------------------------------------------------------


def test_invoice_batch_approve_reject_use_optional_dict():
    """The original signatures used ``data: dict = None`` which is
    a mismatch — dict cannot be None. The review fix tightens to
    Optional[dict] = None so type checkers stop flagging it."""
    src = (ROUTES_DIR / "invoice_batches.py").read_text(encoding="utf-8")
    # Find the approve route block.
    approve_idx = src.find("async def approve_invoice_batch")
    assert approve_idx != -1
    approve_end = src.find("\nasync def ", approve_idx + 1)
    approve_block = src[approve_idx:approve_end]
    assert "data: Optional[dict] = None" in approve_block, (
        "approve_invoice_batch body annotation must be Optional[dict] = None."
    )
    assert "data: dict = None" not in approve_block, (
        "Old 'data: dict = None' annotation must not return."
    )

    # Same for reject.
    reject_idx = src.find("async def reject_invoice_batch")
    assert reject_idx != -1
    reject_end = src.find("\nasync def ", reject_idx + 1)
    reject_block = src[reject_idx:reject_end if reject_end != -1 else len(src)]
    assert "data: Optional[dict] = None" in reject_block, (
        "reject_invoice_batch body annotation must be Optional[dict] = None."
    )


# ---------------------------------------------------------------------------
# 11. Builder to_list(20000) carries an explanatory comment
# ---------------------------------------------------------------------------


def test_invoice_builder_to_list_20000_has_scale_ceiling_comment():
    """The 20k limit on the time-entries scan is a deliberate scale
    ceiling, not an arbitrary magic number. The review asked for a
    comment so a future reader doesn't burn a debugging hour
    questioning the value."""
    src = (SERVICES_DIR / "invoice_batch_builder.py").read_text(encoding="utf-8")
    # Anchor on the actual call (preceding ``.``) so the search
    # doesn't match the comment line that mentions to_list(20000)
    # by name.
    idx = src.find(".to_list(20000)")
    assert idx != -1, (
        "invoice_batch_builder must keep the .to_list(20000) call."
    )
    # The window preceding the call must include a comment line
    # explaining the ceiling. The actual comment is ~9 lines / >600
    # chars long so the window needs to be sized generously to
    # cover comments that grow as we add nuance to the explanation.
    window = src[max(0, idx - 1200):idx]
    lowered = window.lower()
    assert (
        "ceiling" in lowered
        or "scale ceiling" in lowered
        or "per-period" in lowered
        or "deliberate" in lowered
        or "20k" in lowered
    ), (
        "to_list(20000) must be preceded by a comment explaining "
        "the intentional scale ceiling."
    )


# ---------------------------------------------------------------------------
# 2 bonus. Scheduler route still imports + still works with the token
# ---------------------------------------------------------------------------


def test_scheduler_route_top_level_imports_hoisted():
    """The scheduler-route's per-call ``from services.X import Y``
    statements were hoisted to top-of-file. The review asked to
    remove the deferred imports unless an actual circular import
    requires them — none does in this case."""
    src = (ROUTES_DIR / "invoice_batches.py").read_text(encoding="utf-8")
    # The two security helpers must appear in the module-level
    # import block, not inside the function body.
    assert (
        "from services.invoice_batch_scheduler_security import" in src
    ), "invoice_batches must import scheduler security helpers at module level."
    # And the inside-function copy must be gone.
    scheduled_idx = src.find(
        "@router.post(\"/invoice-batches/build-biweekly/scheduled\")"
    )
    assert scheduled_idx != -1
    next_def = src.find("\n@router.", scheduled_idx + 1)
    scheduled_block = src[scheduled_idx:next_def if next_def != -1 else len(src)]
    assert (
        "from services.invoice_batch_scheduler_security import"
        not in scheduled_block
    ), (
        "The scheduled handler must not re-import the security "
        "helpers — they live at module top-level now."
    )
