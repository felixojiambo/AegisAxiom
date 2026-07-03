"""Unit tests for the AI-employee read-side visibility gates.

Covers the two consumer-side helpers that decide whether a non-admin user
sees an AI employee in the listing or single-GET endpoints:

  - ``services.ai.agents.marketplace.derive_employee_use_perms`` — pure
    employee-doc → derived ``*.use`` permission key(s).
  - ``routes.ai_employees._user_can_access_employee`` — three-gate access
    check (allowed_user_ids, allowed_roles, derived *.use).

These tests exist to lock down the regression that caused granting
``vega.use`` to a role to have no effect: the listing filter used to check
only allowed_user_ids+allowed_roles, ignoring the user's hydrated perms.
Per the consumer-enumeration rule, the test suite walks every gate path
explicitly so a future refactor of one gate can't silently break another.
"""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret")

from services.ai.agents.marketplace import derive_employee_use_perms
from routes.ai_employees import _user_can_access_employee


# ---------------------------------------------------------------------------
# derive_employee_use_perms — pure helper
# ---------------------------------------------------------------------------


class TestDeriveEmployeeUsePerms:
    def test_convention_from_employee_id(self):
        assert derive_employee_use_perms({"employee_id": "ai_vega"}) == ["vega.use"]
        assert derive_employee_use_perms({"employee_id": "ai_orion"}) == ["orion.use"]
        assert derive_employee_use_perms({"employee_id": "ai_pmo_assistant"}) == ["pmo_assistant.use"]

    def test_uuid_style_employee_id_derives_literally(self):
        """Nova's employee_id is UUID-based; convention still derives but yields
        a key that won't match the admin-UI's static ``nova.use``. This test
        documents that bug class — the fix is to set explicit
        ``required_permissions`` on the doc."""
        assert derive_employee_use_perms({"employee_id": "ai_d8a9f87c0fb9"}) == ["d8a9f87c0fb9.use"]

    def test_explicit_required_permissions_overrides_convention(self):
        emp = {"employee_id": "ai_d8a9f87c0fb9", "required_permissions": ["nova.use"]}
        assert derive_employee_use_perms(emp) == ["nova.use"]

    def test_empty_required_permissions_falls_back_to_convention(self):
        emp = {"employee_id": "ai_vega", "required_permissions": []}
        assert derive_employee_use_perms(emp) == ["vega.use"]

    def test_no_ai_prefix_returns_empty(self):
        assert derive_employee_use_perms({"employee_id": "not_prefixed"}) == []
        assert derive_employee_use_perms({"employee_id": ""}) == []
        assert derive_employee_use_perms({}) == []

    def test_ai_prefix_with_no_short_returns_empty(self):
        assert derive_employee_use_perms({"employee_id": "ai_"}) == []

    def test_explicit_required_permissions_filters_garbage(self):
        emp = {"employee_id": "ai_x", "required_permissions": [None, "foo.use", 42, ""]}
        assert derive_employee_use_perms(emp) == ["foo.use"]


# ---------------------------------------------------------------------------
# _user_can_access_employee — three-gate helper
# ---------------------------------------------------------------------------


def _user(user_id="user_test", role="sales_rep", permissions=None):
    """Build a User-like stub good enough for the visibility helper.

    has_perm reads .permissions plus a _perm_closure stash (if present). For
    these tests we just put everything in .permissions; the closure check is
    covered by has_perm's own tests.
    """
    perms = list(permissions or [])
    u = SimpleNamespace(
        user_id=user_id,
        role=role,
        permissions=perms,
    )
    # Mirror auth_helper.get_current_user's closure stash; without it the sync
    # has_perm path degrades to direct membership — same answer for these
    # test fixtures, but matches production shape.
    object.__setattr__(u, "_perm_closure", set(perms))
    return u


class TestUserCanAccessEmployee:
    def test_gate_allowed_user_ids_permits(self):
        emp = {"employee_id": "ai_vega", "allowed_user_ids": ["user_phil"], "allowed_roles": []}
        assert _user_can_access_employee(emp, _user(user_id="user_phil"))

    def test_gate_allowed_roles_permits(self):
        emp = {"employee_id": "ai_vega", "allowed_user_ids": [], "allowed_roles": ["technology_advisor"]}
        assert _user_can_access_employee(emp, _user(role="technology_advisor"))

    def test_gate_derived_use_perm_permits(self):
        """The bug we just fixed: a user holding ``vega.use`` via role.permissions
        should see Vega even when not listed in allowed_user_ids or allowed_roles."""
        emp = {"employee_id": "ai_vega", "allowed_user_ids": [], "allowed_roles": []}
        assert _user_can_access_employee(emp, _user(permissions=["vega.use"]))

    def test_gate_explicit_required_permission_permits(self):
        emp = {
            "employee_id": "ai_d8a9f87c0fb9",
            "required_permissions": ["nova.use"],
            "allowed_user_ids": [],
            "allowed_roles": [],
        }
        assert _user_can_access_employee(emp, _user(permissions=["nova.use"]))

    def test_no_gate_denies(self):
        emp = {"employee_id": "ai_vega", "allowed_user_ids": [], "allowed_roles": []}
        assert not _user_can_access_employee(emp, _user(permissions=["unrelated.perm"]))

    def test_wrong_role_denied_even_with_other_role_grant(self):
        emp = {"employee_id": "ai_vega", "allowed_user_ids": [], "allowed_roles": ["technology_advisor"]}
        assert not _user_can_access_employee(emp, _user(role="sales_rep"))

    def test_wrong_user_id_denied(self):
        emp = {"employee_id": "ai_vega", "allowed_user_ids": ["user_phil"], "allowed_roles": []}
        assert not _user_can_access_employee(emp, _user(user_id="user_eric"))

    def test_broad_ai_employees_view_does_not_grant_per_employee_access(self):
        """Listing is intentionally narrower than BaseAgent.user_has_access:
        holding ``ai_employees.view`` (the generic feature gate) must NOT
        surface every AI employee in the user's sidebar. Per-employee access
        comes from the three explicit gates."""
        emp = {"employee_id": "ai_vega", "allowed_user_ids": [], "allowed_roles": []}
        assert not _user_can_access_employee(emp, _user(permissions=["ai_employees.view"]))

    def test_uuid_employee_id_with_mismatched_static_grant_still_denies(self):
        """Nova's broken state pre-migration: admin grants ``nova.use`` but
        Nova's doc derives to ``d8a9f87c0fb9.use``. Until ``required_permissions``
        is set, the user does not gain access via the derived path. This test
        documents the gap so the migration fix doesn't quietly regress it."""
        emp = {
            "employee_id": "ai_d8a9f87c0fb9",
            "allowed_user_ids": [],
            "allowed_roles": [],
        }
        assert not _user_can_access_employee(emp, _user(permissions=["nova.use"]))

    def test_multiple_gates_compose(self):
        """A user in allowed_user_ids AND with derived perm passes — defense in depth."""
        emp = {"employee_id": "ai_vega", "allowed_user_ids": ["user_phil"], "allowed_roles": []}
        assert _user_can_access_employee(emp, _user(user_id="user_phil", permissions=["vega.use"]))
