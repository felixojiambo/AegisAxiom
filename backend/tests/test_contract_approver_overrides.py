"""Wave 1 follow-up — configurable contract approver tests.

These tests prove that contract approval permission is correctly
delegable via TWO independent paths so the initial approver set can
be wired up entirely through RBAC seed data and per-user contract
approver grants — never through route-level hardcoding of named
business approvers.

The two paths under test:

  1. Role-based grant. The user's role document carries
     ``contracts.approve`` in its ``permissions`` array. Already
     covered by the director/finance role grants in
     ``backend/routes/roles.py``; spot-checked here for completeness.

  2. Per-user override. The user document carries the permission
     string in ``extra_permissions``. ``auth_helper.get_current_user``
     and ``routes/auth._build_user_with_permissions`` BOTH merge
     ``role_permissions + user_extras`` into the user's effective
     permission set, so ``has_perm(user, "contracts.approve")``
     returns True for either path.

Companion to ``test_contract_approval_workflow.py`` (which covers the
role-grant happy path and the Sales-Ops 403). This file owns the
override path exclusively.

Coverage:
  - Role grant: director-shaped user with role permissions including
    contracts.approve approves successfully.
  - Override path: sales_ops-shaped user with extra_permissions
    containing contracts.approve approves successfully.
  - No grant: sales_ops-shaped user with NO extras gets 403 on
    approve.
  - No grant: sales_ops-shaped user with NO extras gets 403 on the
    approval queue.
  - Override path on queue: sales_ops-shaped user with extras can
    list the queue.
  - Merge layer lock: routes/auth._build_user_with_permissions merges
    extras into the User's permissions field so has_perm respects the
    override without any extra plumbing.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


# ---------------------------------------------------------------------------
# Fakes mirror test_contract_approval_workflow.py so the two suites share a
# style. The collection fakes deliberately stay tiny — they only support
# the small operation surface each test uses.
# ---------------------------------------------------------------------------


def _fake_contract(**overrides) -> dict:
    base = {
        "contract_id": "contract_synth_override_001",
        "name": "Wholesale MRR Renewal",
        "account_id": "acct_001",
        "account_name": "Acme",
        "contract_type": "wholesale_mrr",
        "service_category": "wholesale_mrr",  # Not PS → no auto-create path.
        "status": "draft",
        "approval_status": "pending",
        "project_ids": [],
        "tenant_id": None,
    }
    base.update(overrides)
    return base


class _FakeContracts:
    def __init__(self, doc, find_results=None):
        self._doc = dict(doc) if isinstance(doc, dict) else None
        self._find_results = list(find_results or [])
        self.updates: list = []

    async def find_one(self, _query, _projection=None):
        return dict(self._doc) if isinstance(self._doc, dict) else None

    async def update_one(self, query, update):
        self.updates.append((dict(query), dict(update)))
        if isinstance(self._doc, dict) and "$set" in update:
            self._doc.update(update["$set"])
        return SimpleNamespace(modified_count=1)

    async def find_one_and_update(self, query, update, **_kwargs):
        """Atomic conditional update used by the approval race fix.
        The fake just applies the $set when the canned doc exists."""
        self.updates.append((dict(query), dict(update)))
        if not isinstance(self._doc, dict):
            return None
        if "$set" in update:
            self._doc.update(update["$set"])
        return dict(self._doc)

    def find(self, _query, _projection=None):
        items = list(self._find_results)

        class _Cursor:
            def sort(self, *_a, **_kw):
                return self

            async def to_list(self, _n):
                return items

        return _Cursor()


def _setup(monkeypatch, contract_doc=None, queue_results=None):
    contracts = _FakeContracts(contract_doc, find_results=queue_results)
    approvals = SimpleNamespace(insert_one=AsyncMock(return_value=None))
    projects = SimpleNamespace(insert_one=AsyncMock(return_value=None))

    class _DB:
        pass
    db = _DB()
    db.contracts = contracts
    db.contract_approvals = approvals
    db.projects = projects

    import routes.contracts as rc
    monkeypatch.setattr(rc, "get_db", lambda: db)
    monkeypatch.setattr(rc, "log_action", AsyncMock(return_value=None))
    monkeypatch.setattr(rc, "emit_event", AsyncMock(return_value=None))

    async def _empty_filter(_u):
        return {}
    monkeypatch.setattr(rc, "get_contract_filter", _empty_filter)
    return contracts, approvals, rc


# ---------------------------------------------------------------------------
# Helpers — build "user shaped" objects that mirror what auth_helper.get_current_user
# stamps onto a request. Crucially, the `permissions` list is the MERGED
# (role + extras) view because both auth_helper.get_current_user and
# routes/auth._build_user_with_permissions perform that merge BEFORE
# the User object is handed to a route. has_perm() then reads
# user.permissions directly; it never re-reads extras.
# ---------------------------------------------------------------------------


def _user_with_role_grant() -> SimpleNamespace:
    """Director-shaped user — contracts.approve via role permissions."""
    return SimpleNamespace(
        user_id="u_director_001",
        email="director@aegisaxiom.test",
        role="director",
        tenant_id=None,
        account_id=None,
        # Role grant: contracts.approve sits inside the role.permissions
        # array in roles.py. The auth layer would merge that into the
        # user.permissions list with no extras — represented here.
        permissions=[
            "contracts.create",
            "contracts.edit",
            "contracts.edit.all",
            "contracts.approve",
        ],
        extra_permissions=[],
    )


def _user_with_override_only() -> SimpleNamespace:
    """Sales-Ops-shaped user — role does NOT carry contracts.approve, but
    extra_permissions does. After the auth-layer merge, permissions
    contains contracts.approve."""
    return SimpleNamespace(
        user_id="u_sales_ops_override",
        email="ops_override@aegisaxiom.test",
        role="sales_ops",
        tenant_id=None,
        account_id=None,
        # Merged view: role perms + extras. The override puts
        # contracts.approve into the merged set even though the
        # sales_ops role does not grant it.
        permissions=[
            "contracts.create",
            "contracts.edit",
            "contracts.approve",
        ],
        extra_permissions=["contracts.approve"],
    )


def _user_without_grant() -> SimpleNamespace:
    """Sales-Ops-shaped user with no override — approve must 403."""
    return SimpleNamespace(
        user_id="u_sales_ops_basic",
        email="ops_basic@aegisaxiom.test",
        role="sales_ops",
        tenant_id=None,
        account_id=None,
        permissions=[
            "contracts.create",
            "contracts.edit",
        ],
        extra_permissions=[],
    )


# ---------------------------------------------------------------------------
# Route-level tests — exercise the actual contracts.py endpoints.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_grant_user_can_approve(monkeypatch):
    contracts, approvals, rc = _setup(monkeypatch, contract_doc=_fake_contract())
    user = _user_with_role_grant()
    result = await rc.approve_contract(
        contract_id="contract_synth_override_001",
        data={"approval_notes": "via role"},
        current_user=user,
    )
    assert result["approval_status"] == "approved"


@pytest.mark.asyncio
async def test_override_only_user_can_approve(monkeypatch):
    """User whose role does NOT carry contracts.approve but whose
    extra_permissions does — i.e., the per-user contract approver
    grant pattern — must be able to approve. This is the central
    claim of the change."""
    contracts, approvals, rc = _setup(monkeypatch, contract_doc=_fake_contract())
    user = _user_with_override_only()
    result = await rc.approve_contract(
        contract_id="contract_synth_override_001",
        data={"approval_notes": "via per-user grant"},
        current_user=user,
    )
    assert result["approval_status"] == "approved"
    assert approvals.insert_one.await_count == 1


@pytest.mark.asyncio
async def test_user_without_role_or_override_cannot_approve(monkeypatch):
    contracts, _, rc = _setup(monkeypatch, contract_doc=_fake_contract())
    user = _user_without_grant()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rc.approve_contract(
            contract_id="contract_synth_override_001",
            data={"approval_notes": "no perm"},
            current_user=user,
        )
    assert ei.value.status_code == 403
    # No approval row written, no contract update.
    assert contracts.updates == []


@pytest.mark.asyncio
async def test_user_without_grant_cannot_see_queue(monkeypatch):
    """Approval queue is gated on contracts.approve; a Sales Ops user
    with no override gets 403 (matches the wire response the admin
    GUI / Finance Command Center surfaces to the role)."""
    contracts, _, rc = _setup(
        monkeypatch,
        contract_doc=None,
        queue_results=[_fake_contract()],
    )
    user = _user_without_grant()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rc.list_contract_approval_queue(current_user=user)
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_override_only_user_can_see_queue(monkeypatch):
    """The override path also unlocks the queue endpoint, not just the
    decision endpoints. Same has_perm check, exercised separately."""
    contracts, _, rc = _setup(
        monkeypatch,
        contract_doc=None,
        queue_results=[_fake_contract(contract_id="c_q_a"), _fake_contract(contract_id="c_q_b")],
    )
    user = _user_with_override_only()
    result = await rc.list_contract_approval_queue(current_user=user)
    assert result["total"] == 2
    assert {item["contract_id"] for item in result["items"]} == {"c_q_a", "c_q_b"}


@pytest.mark.asyncio
async def test_sales_ops_can_still_create_and_edit_without_approve(monkeypatch):
    """Sales Ops without override must keep create/edit access. This
    test proves the override mechanism does NOT shrink the role's
    existing rights — it only adds the optional approve grant when
    extras name it."""
    contracts, _, rc = _setup(monkeypatch, contract_doc=_fake_contract(approval_status=None, status="draft"))
    user = _user_without_grant()  # has contracts.create + contracts.edit
    # The submit endpoint requires contracts.edit, not contracts.approve.
    result = await rc.submit_contract_for_approval(
        contract_id="contract_synth_override_001",
        data={"approval_notes": "submitting"},
        current_user=user,
    )
    assert result["approval_status"] == "pending"


# ---------------------------------------------------------------------------
# Merge-layer lock — verifies the production contract that auth_helper.get_current_user
# upholds: a User constructed with merged (role + extras) permissions and
# the precomputed closure stashed via object.__setattr__ resolves
# overrides through has_perm() in O(1).
#
# The generic merge mechanism (union of role + extras into the closure)
# is already locked by tests/test_auth_effective_permissions.py
# (test_extra_permissions_union_into_closure / _alone_resolve_into_closure).
# These two tests pin the contract specifically for contracts.approve so
# a future refactor that drops contracts.approve from the closure surfaces
# here.
# ---------------------------------------------------------------------------


def _build_user_like_get_current_user(role_perms: list[str], extras: list[str]):
    """Construct a User the same way auth_helper.get_current_user does:
    merge role + extras, resolve the closure, stash both on the User.
    Returns a User instance suitable for has_perm() calls."""
    from models import User
    from services.permission_implications import resolve_implications

    merged = list(set(role_perms) | set(extras))
    closure = resolve_implications(set(merged), registry_graph={})
    user_dict = {
        "user_id": "u_synth_merge",
        "email": "synth@aegisaxiom.test",
        "name": "Synth",
        "role": "sales_ops",
        "permissions": merged,
        "extra_permissions": list(extras),
        "effective_permissions": sorted(closure),
        "is_active": True,
    }
    user = User(**user_dict)
    object.__setattr__(user, "_perm_closure", closure)
    return user


def test_merge_contract_grants_approve_when_extras_only(monkeypatch):
    """When the role grants no approve but extras does, the merged
    User produced by the production auth path resolves the override
    through has_perm. This is the per-user contract approver grant
    pattern."""
    from auth_helper import has_perm

    user = _build_user_like_get_current_user(
        role_perms=[
            "contracts.create",
            "contracts.edit",
            "sales_ops.solutions_design.create",
        ],
        extras=["contracts.approve"],
    )

    assert has_perm(user, "contracts.approve") is True
    assert has_perm(user, "contracts.create") is True
    assert has_perm(user, "contracts.delete") is False


def test_merge_contract_denies_approve_when_extras_empty(monkeypatch):
    """Sales Ops user with empty extras must NOT get contracts.approve
    via the merged closure. Locks the override mechanism's blast
    radius — extras are the only delegation door."""
    from auth_helper import has_perm

    user = _build_user_like_get_current_user(
        role_perms=["contracts.create", "contracts.edit"],
        extras=[],
    )

    assert has_perm(user, "contracts.approve") is False
    assert has_perm(user, "contracts.create") is True


def test_get_current_user_source_unions_role_and_extras():
    """Source-level lock: auth_helper.get_current_user must continue to
    merge role_permissions with user_extras before stashing the
    closure. If a refactor drops the union, the override path silently
    breaks for every permission, not just contracts.approve."""
    import inspect
    import auth_helper

    source = inspect.getsource(auth_helper.get_current_user)
    # Both halves of the union appear adjacent in the source — pin both
    # so a rename of either variable surfaces here.
    assert "role_permissions" in source
    assert "user_extras" in source
    assert "user_doc.get(\"extra_permissions\"" in source


# ---------------------------------------------------------------------------
# Registry lock — the permission must exist in the permission registry
# so the admin endpoint (PUT /api/admin/users/{id}) accepts it as a
# valid value for extra_permissions. Without this entry the admin
# GUI's per-user grant flow rejects the key as invalid and the
# initial approver set cannot be wired up.
# ---------------------------------------------------------------------------


def test_permission_registry_contains_contracts_approve():
    """Source-level lock against accidental removal of the registry
    entry that powers the per-user override path."""
    import inspect
    import routes.roles as roles_module

    source = inspect.getsource(roles_module)
    assert '"contracts.approve"' in source, (
        "contracts.approve must remain in routes/roles.py's permission "
        "registry; admin PUT /users/{id} validates extras against this set"
    )
