"""Integration tests for GET /api/admin/contract-approval-access.

The route is a thin orchestrator: it pulls users + roles from
Mongo, hands them to the pure helper in
services.contract_approval_access, and returns the envelope. These
tests cover the route layer — permission gates, tenant scoping,
query-param plumbing — using fake Mongo collections and synthetic
users. The helper algorithm itself is covered exhaustively in
test_contract_approval_access_service.py.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production",
)

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest  # noqa: E402

import routes.admin as ra  # noqa: E402
from fastapi import HTTPException  # noqa: E402


# ---------------------------------------------------------------------------
# Fake Mongo collections
# ---------------------------------------------------------------------------


class _FakeUsersCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    async def to_list(self, _n):
        return list(self._rows)


class _FakeUsers:
    """Tiny in-memory `db.users` that honours a {tenant_id: X} filter."""

    def __init__(self, rows):
        self._rows = list(rows)

    def find(self, query, _projection=None):
        tenant_id = query.get("tenant_id") if isinstance(query, dict) else None
        if tenant_id is None:
            filtered = list(self._rows)
        else:
            filtered = [r for r in self._rows if r.get("tenant_id") == tenant_id]
        return _FakeUsersCursor(filtered)


class _FakeRoles:
    def __init__(self, rows):
        self._rows = list(rows)

    def find(self, _query, _projection=None):
        return _FakeUsersCursor(self._rows)


def _install_fake_db(monkeypatch, *, users, roles):
    class _DB:
        pass
    db = _DB()
    db.users = _FakeUsers(users)
    db.roles = _FakeRoles(roles)
    monkeypatch.setattr(ra, "get_db", lambda: db)


def _admin_user(tenant_id="tenant-a"):
    return SimpleNamespace(
        user_id="u-admin",
        email="admin@aegisaxiom.test",
        role="admin",
        tenant_id=tenant_id,
        permissions=["*"],
        extra_permissions=[],
    )


def _viewer_user(tenant_id="tenant-a"):
    """users.view but not admin role — should still be allowed to
    READ this endpoint (we mirror the existing /admin/users gate)."""
    return SimpleNamespace(
        user_id="u-viewer",
        email="viewer@aegisaxiom.test",
        role="finance",
        tenant_id=tenant_id,
        permissions=["users.view", "finance.access"],
        extra_permissions=[],
    )


def _unauth_user(tenant_id="tenant-a"):
    return SimpleNamespace(
        user_id="u-noview",
        email="noview@aegisaxiom.test",
        role="sales_rep",
        tenant_id=tenant_id,
        permissions=["opportunities.view"],
        extra_permissions=[],
    )


def _tenantless_user():
    return SimpleNamespace(
        user_id="u-no-tenant",
        email="orphan@aegisaxiom.test",
        role="admin",
        tenant_id=None,
        permissions=["*"],
        extra_permissions=[],
    )


# Synthetic users that span all four approval source buckets.
def _seed_users(tenant_id="tenant-a"):
    return [
        {"user_id": "u1", "name": "Alice Admin", "email": "a@x.com",
         "role": "admin", "tenant_id": tenant_id, "extra_permissions": []},
        {"user_id": "u2", "name": "Dan Director", "email": "d@x.com",
         "role": "director", "tenant_id": tenant_id, "extra_permissions": []},
        {"user_id": "u3", "name": "Sam Sales", "email": "s@x.com",
         "role": "sales_rep", "tenant_id": tenant_id,
         "extra_permissions": ["contracts.approve"]},
        {"user_id": "u4", "name": "Sara Sales", "email": "ss@x.com",
         "role": "sales_rep", "tenant_id": tenant_id, "extra_permissions": []},
        {"user_id": "u5", "name": "Frank Finance", "email": "f@x.com",
         "role": "finance", "tenant_id": tenant_id,
         "extra_permissions": ["contracts.approve"]},
    ]


def _seed_roles():
    return [
        {"name": "admin", "permissions": ["*"]},
        {"name": "director", "permissions": ["contracts.approve"]},
        {"name": "finance", "permissions": ["contracts.approve"]},
        {"name": "sales_rep", "permissions": ["deals.view"]},
    ]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_returns_pagination_envelope(monkeypatch):
    _install_fake_db(
        monkeypatch,
        users=_seed_users(),
        roles=_seed_roles(),
    )
    result = _run(ra.list_contract_approval_access(
        search="",
        approval_filter="all",
        page=1,
        page_size=50,
        current_user=_admin_user(),
    ))
    assert set(result) == {"items", "page", "page_size", "total"}
    assert result["total"] == 5
    assert result["page"] == 1
    assert result["page_size"] == 50


def test_resolves_approval_source_per_user(monkeypatch):
    _install_fake_db(
        monkeypatch,
        users=_seed_users(),
        roles=_seed_roles(),
    )
    result = _run(ra.list_contract_approval_access(
        search="",
        approval_filter="all",
        page=1,
        page_size=50,
        current_user=_admin_user(),
    ))
    by_id = {r["user_id"]: r for r in result["items"]}
    assert by_id["u1"]["approval_source"] == "role"        # admin -> *
    assert by_id["u2"]["approval_source"] == "role"        # director
    assert by_id["u3"]["approval_source"] == "override"    # sales+override
    assert by_id["u4"]["approval_source"] == "none"        # sales no override
    assert by_id["u5"]["approval_source"] == "role_and_override"


def test_search_filters_correctly(monkeypatch):
    _install_fake_db(
        monkeypatch,
        users=_seed_users(),
        roles=_seed_roles(),
    )
    result = _run(ra.list_contract_approval_access(
        search="frank",
        approval_filter="all",
        page=1,
        page_size=50,
        current_user=_admin_user(),
    ))
    assert {r["user_id"] for r in result["items"]} == {"u5"}


def test_approval_filter_role_only(monkeypatch):
    _install_fake_db(
        monkeypatch,
        users=_seed_users(),
        roles=_seed_roles(),
    )
    result = _run(ra.list_contract_approval_access(
        search="",
        approval_filter="role",
        page=1,
        page_size=50,
        current_user=_admin_user(),
    ))
    assert {r["user_id"] for r in result["items"]} == {"u1", "u2"}


def test_approval_filter_override_only(monkeypatch):
    _install_fake_db(
        monkeypatch,
        users=_seed_users(),
        roles=_seed_roles(),
    )
    result = _run(ra.list_contract_approval_access(
        search="",
        approval_filter="override",
        page=1,
        page_size=50,
        current_user=_admin_user(),
    ))
    assert {r["user_id"] for r in result["items"]} == {"u3"}


def test_pagination_page_2(monkeypatch):
    bulk = [
        {"user_id": f"u{i:03d}", "name": f"User {i:03d}",
         "email": f"u{i:03d}@x.com", "role": "sales_rep",
         "tenant_id": "tenant-a", "extra_permissions": []}
        for i in range(30)
    ]
    _install_fake_db(monkeypatch, users=bulk, roles=_seed_roles())
    page1 = _run(ra.list_contract_approval_access(
        search="", approval_filter="all", page=1, page_size=25,
        current_user=_admin_user(),
    ))
    page2 = _run(ra.list_contract_approval_access(
        search="", approval_filter="all", page=2, page_size=25,
        current_user=_admin_user(),
    ))
    assert page1["total"] == 30
    assert len(page1["items"]) == 25
    assert len(page2["items"]) == 5


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_tenant_isolation_filters_other_tenants_users(monkeypatch):
    """A user in tenant-a must never see users from tenant-b."""
    mixed = _seed_users(tenant_id="tenant-a") + [
        {"user_id": "uX1", "name": "Other Tenant Director",
         "email": "x1@x.com", "role": "director",
         "tenant_id": "tenant-b", "extra_permissions": []},
        {"user_id": "uX2", "name": "Other Tenant Override",
         "email": "x2@x.com", "role": "sales_rep",
         "tenant_id": "tenant-b",
         "extra_permissions": ["contracts.approve"]},
    ]
    _install_fake_db(monkeypatch, users=mixed, roles=_seed_roles())

    result = _run(ra.list_contract_approval_access(
        search="", approval_filter="all", page=1, page_size=50,
        current_user=_admin_user(tenant_id="tenant-a"),
    ))
    ids = {r["user_id"] for r in result["items"]}
    assert "uX1" not in ids
    assert "uX2" not in ids
    assert {"u1", "u2", "u3", "u4", "u5"}.issubset(ids)


# ---------------------------------------------------------------------------
# Permission gates
# ---------------------------------------------------------------------------


def test_user_without_users_view_is_403(monkeypatch):
    _install_fake_db(
        monkeypatch,
        users=_seed_users(),
        roles=_seed_roles(),
    )
    with pytest.raises(HTTPException) as ei:
        _run(ra.list_contract_approval_access(
            search="", approval_filter="all", page=1, page_size=50,
            current_user=_unauth_user(),
        ))
    assert ei.value.status_code == 403


def test_user_without_tenant_is_403(monkeypatch):
    _install_fake_db(
        monkeypatch,
        users=_seed_users(),
        roles=_seed_roles(),
    )
    with pytest.raises(HTTPException) as ei:
        _run(ra.list_contract_approval_access(
            search="", approval_filter="all", page=1, page_size=50,
            current_user=_tenantless_user(),
        ))
    assert ei.value.status_code == 403


def test_viewer_with_users_view_is_allowed_to_read(monkeypatch):
    """The read endpoint mirrors the /admin/users gate (users.view).
    Admin role is NOT required to READ — it's only required at PUT
    time when mutating extra_permissions. That keeps the audit
    surface available to finance/director auditors."""
    _install_fake_db(
        monkeypatch,
        users=_seed_users(),
        roles=_seed_roles(),
    )
    result = _run(ra.list_contract_approval_access(
        search="", approval_filter="all", page=1, page_size=50,
        current_user=_viewer_user(),
    ))
    assert result["total"] == 5
