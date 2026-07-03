"""Contract tests for the post-May-2026 RBAC decision: every authenticated
seeded role grants `nova.use` so any user can open Nova chat without
needing per-user `extra_permissions`.

These tests guard the role seed in ``backend/routes/roles.py``. If a future
edit drops `nova.use` from a default role, these fail loudly — the product
intent is "all authenticated users can use Nova", and the easiest way to
break that is to forget the permission on a role added later.

Scope:
  * Static check against ``DEFAULT_ROLES`` — no DB, no FastAPI app boot.
  * Existing role docs in production are reconciled by ``seed_default_roles``
    on startup ($addToSet for new defaults that aren't already in the
    closure). For environments that ship with ``SKIP_STARTUP_SEEDS=true``,
    a one-time DB command is required (see MVP_FINANCE_BUG_AUDIT.md §5).
"""
from __future__ import annotations

import pytest

from routes.roles import DEFAULT_ROLES


_NOVA_USE = "nova.use"
_WILDCARD = "*"


def _role_grants_nova(role: dict) -> bool:
    perms = role.get("permissions", [])
    return _WILDCARD in perms or _NOVA_USE in perms


# Roles intentionally EXCLUDED from Nova access per PR #164 review.
# Adding a name here is a deliberate product decision; the inverse
# test ``test_excluded_roles_do_not_grant_nova_use`` pins the
# expectation so a future role edit that re-adds nova.use shows up
# as a failed assertion rather than a silent capability flip.
_ROLES_WITHOUT_NOVA: frozenset[str] = frozenset({
    # contractor — latent nova.use grant removed in PR #164. If
    # contractor.chat.access lands later, the moment nova.use is
    # also re-added contractors get Nova — that should be a
    # deliberate, reviewed product call, not the side-effect of a
    # comment-only role edit.
    "contractor",
    # tenant_admin carries only chat.access today; 1B.5 expands the
    # delegation perm shape and that's where nova.use will be
    # re-considered.
    "tenant_admin",
})


def test_every_non_excluded_seeded_role_grants_nova_use():
    """All seeded roles EXCEPT those in ``_ROLES_WITHOUT_NOVA`` must
    grant nova.use (or hold the * wildcard).

    Iterates DEFAULT_ROLES rather than hardcoding role names so a role
    added later automatically falls under the same rule. Failure means
    a role was added without granting Nova — either grant nova.use
    or document the explicit exclusion in ``_ROLES_WITHOUT_NOVA``.
    """
    missing = [
        r["name"]
        for r in DEFAULT_ROLES
        if r["name"] not in _ROLES_WITHOUT_NOVA
        and not _role_grants_nova(r)
    ]
    assert not missing, (
        f"Default roles missing nova.use (and no wildcard either): {missing}. "
        "Per the May 2026 product call, every authenticated seeded role "
        "should grant Nova access unless explicitly excluded. Add "
        "'nova.use' to the role's permissions list in "
        "backend/routes/roles.py."
    )


def test_excluded_roles_do_not_grant_nova_use():
    """The inverse of the rule above: roles in
    ``_ROLES_WITHOUT_NOVA`` MUST NOT carry nova.use. This catches a
    silent regression where a future role edit re-adds the latent
    grant without product approval."""
    leaked = [
        r["name"]
        for r in DEFAULT_ROLES
        if r["name"] in _ROLES_WITHOUT_NOVA and _role_grants_nova(r)
    ]
    assert not leaked, (
        f"Roles {leaked!r} carry nova.use but are in the "
        "_ROLES_WITHOUT_NOVA set. Either remove nova.use from the "
        "role definition (preferred) OR remove the role from the set "
        "after a product-approved policy change."
    )


@pytest.mark.parametrize(
    "role_name",
    [
        "admin",       # via wildcard
        "director",
        "finance",
        "sales_rep",   # the regression case — Phil
        "sales_ops",
        "agent",
        "customer",
        "chat_only",
    ],
)
def test_named_default_role_grants_nova_use(role_name: str):
    """Belt-and-braces: explicit per-role coverage so a regression on
    sales_rep specifically (the Phil case) shows up as a single failed
    parametrize node rather than buried in the iterator above."""
    role = next((r for r in DEFAULT_ROLES if r["name"] == role_name), None)
    assert role is not None, f"Default role {role_name!r} not seeded — did the role get renamed?"
    assert _role_grants_nova(role), (
        f"Role {role_name!r} does not grant nova.use (or *). "
        "See backend/routes/roles.py:DEFAULT_ROLES."
    )


def test_admin_is_only_role_relying_on_wildcard():
    """Sanity: admin is the only system role that gates Nova access via *.

    Every other role lists nova.use explicitly. Locks the convention so
    a future change that quietly relies on wildcards on a non-admin role
    fails this test instead of silently expanding the wildcard surface.
    """
    wildcard_only = [
        r["name"]
        for r in DEFAULT_ROLES
        if _WILDCARD in r.get("permissions", [])
        and _NOVA_USE not in r.get("permissions", [])
    ]
    assert wildcard_only == ["admin"], (
        f"Expected only 'admin' to rely on '*' for Nova access, got: "
        f"{wildcard_only}. Other roles should list nova.use explicitly so "
        "permission audits can grep for it."
    )
