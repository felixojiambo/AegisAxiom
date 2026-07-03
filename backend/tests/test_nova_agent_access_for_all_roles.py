"""Tests for NovaAgent.user_has_access against every seeded role.

Purpose: lock the role × Nova-access matrix at the agent-access layer.
The chat session route (``backend/routes/chat.py``) defers to
``agent.user_has_access(current_user)`` before creating a pinned
session, so the role seed and the agent gate must agree on which
roles can reach Nova.

PR #164 review decision: contractor does NOT get Nova. tenant_admin
does NOT (yet) get Nova — it carries only ``chat.access`` until the
1B.5 delegation perm shape lands. Every other seeded role keeps
``nova.use``.

This test does NOT spin up FastAPI or Mongo. It walks
``DEFAULT_ROLES``, builds a stand-in user object with each role's
permissions, and checks ``NovaAgent().user_has_access(user)`` in
isolation.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from routes.roles import DEFAULT_ROLES


# Roles that the product policy EXPECTS to be denied Nova access.
# Adding a role here is a deliberate policy decision; removing one
# requires either granting ``nova.use`` on that role OR relaxing
# NovaAgent.required_permissions.
_ROLES_WITHOUT_NOVA: frozenset[str] = frozenset({
    # PR #164 review: latent nova.use grant removed so a future
    # contractor chat.access flip does not silently surface Nova.
    "contractor",
    # tenant_admin carries only chat.access until 1B.5 expands the
    # tenant-scoped delegation perms.
    "tenant_admin",
})


def _user_for(role: dict) -> SimpleNamespace:
    """Build a minimal user stand-in matching what auth_helper produces.

    BaseAgent.user_has_access reads ``permissions``, ``user_id``, and
    ``role`` off the user — SimpleNamespace covers all three without
    pulling in the full Pydantic User model (which would require a DB).
    """
    return SimpleNamespace(
        user_id=f"u_test_{role['name']}",
        role=role["name"],
        permissions=list(role.get("permissions", [])),
    )


def _all_role_names():
    return [r["name"] for r in DEFAULT_ROLES]


@pytest.mark.parametrize("role_name", _all_role_names())
def test_default_role_nova_access_matches_policy(role_name: str):
    """For each seeded role, NovaAgent.user_has_access should agree
    with the product policy in ``_ROLES_WITHOUT_NOVA``.

    Failure modes:
      * A role in the "with Nova" set was denied → the role lost
        ``nova.use`` or NovaAgent.required_permissions drifted.
      * A role in ``_ROLES_WITHOUT_NOVA`` was admitted → a latent
        ``nova.use`` grant slipped back into the role definition.
    """
    # Import lazily so the static @register agents don't pull DB
    # imports before the test even runs.
    from services.ai.agents.nova import NovaAgent

    role = next(r for r in DEFAULT_ROLES if r["name"] == role_name)
    user = _user_for(role)

    agent = NovaAgent()
    has_access = agent.user_has_access(user)

    if role_name in _ROLES_WITHOUT_NOVA:
        assert not has_access, (
            f"Role {role_name!r} unexpectedly has Nova access. "
            f"Permissions: {user.permissions}. "
            "If product policy now allows Nova for this role, "
            "remove it from _ROLES_WITHOUT_NOVA and grant nova.use "
            "on the role definition."
        )
    else:
        assert has_access, (
            f"Role {role_name!r} cannot access NovaAgent. "
            f"Permissions: {user.permissions}. "
            "Either grant 'nova.use' on the role (preferred) or "
            "add the role to _ROLES_WITHOUT_NOVA if this is the "
            "intended product policy."
        )


def test_user_without_nova_use_is_denied():
    """Negative control: a role with chat.access but no nova.use must
    still be blocked. Guards against the test passing because NovaAgent
    silently dropped its required_permissions check."""
    from services.ai.agents.nova import NovaAgent

    user = SimpleNamespace(
        user_id="u_test_nopenny",
        role="custom_no_nova",
        permissions=["chat.access"],  # has chat but not nova.use
    )
    agent = NovaAgent()
    assert not agent.user_has_access(user), (
        "NovaAgent should refuse access to a user missing nova.use. "
        "If this fails, NovaAgent's gate is too permissive."
    )
