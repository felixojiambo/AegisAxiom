"""Contract tests for the AI agent permission registry seed.

Locks two invariants in place:

1. Every static @register-decorated AI agent that gates on a `<route>.use`
   permission must have that key declared in
   ``backend/routes/roles.py:_core_permissions()``. Without the
   declaration, the GUI permission picker can't show the key and admins
   have no way to grant it to other roles.

2. Every canonical seeded AI marketplace employee (the ones in
   ``backend/seeds/seed_*.py``) must have its derived
   ``<employee_id-prefix>.use`` key declared. Marketplace agents
   register dynamically at runtime via
   ``MarketplaceAgent.__init__:64-72`` which derives the `.use` key
   from ``employee_id`` (``ai_arc -> arc.use`` etc.). The seed must
   include the matching key so non-admin roles can be granted access
   via the admin GUI.

The test iterates the registry and the canonical seeded employee_ids
rather than hardcoding agent names, so new agents added later
automatically test against this contract — they fail until the seed
is updated.

NOTE: ``chat.access`` is also asserted as it's the gate the chat
routes use before any agent routing happens. Without it admins can't
even reach the chat surface.
"""
from __future__ import annotations

import pytest


# Canonical seeded AI employee_ids. Mirrors backend/seeds/seed_*.py.
# The marketplace agent derives ``<short>.use`` from the prefix
# (``ai_X -> X.use``), so each entry here corresponds to a `.use` key
# the registry must declare.
#
# Custom user-created AI employees (e.g. ``ai_d8a9f87c0fb9``) generate
# `.use` keys at creation time and are out of scope here — they are
# tracked dynamically, not via this static seed contract.
#
# TODO: this list is manually maintained. If a new marketplace AI employee
# is seeded (backend/seeds/seed_*.py) without updating this list, the
# contract test will silently miss the new agent's <short>.use key. A
# better pattern is parsing seed_*.py modules directly to discover
# canonical employee_ids — deferred for now, acceptable short-term.
_SEEDED_AI_EMPLOYEE_IDS = [
    "ai_vega",
    "ai_arc",
    "ai_orion",
    "ai_pmo_assistant",
]


def _registered_permission_keys() -> set[str]:
    """Return every key declared in the core permission seed."""
    from routes.roles import _core_permissions

    return {entry["key"] for entry in _core_permissions()}


def test_chat_access_is_declared():
    """The chat route guard requires ``chat.access`` before any agent
    routing. The key must be in the registry so the GUI can grant it."""
    keys = _registered_permission_keys()
    assert "chat.access" in keys, (
        "chat.access missing from _core_permissions(); "
        "chat routes (routes/chat.py) gate on it before any agent dispatch"
    )


def test_static_agent_use_keys_are_declared():
    """For every static @register agent whose required_permissions list
    references a ``<route>.use`` key, that key must be in the registry.

    Iterates the agent registry rather than hardcoding names so new
    agents added later are checked automatically — they fail until
    their ``<route>.use`` key is added to _core_permissions().
    """
    # Import side-effect populates the static agent registry.
    import services.ai.agents  # noqa: F401
    from services.ai.agents.registry import all_agents

    registered = _registered_permission_keys()

    missing: list[tuple[str, str]] = []
    for agent in all_agents():
        for perm in agent.required_permissions:
            if perm.endswith(".use") and perm not in registered:
                missing.append((agent.route_name, perm))

    assert not missing, (
        f"Static agents reference .use keys not declared in "
        f"_core_permissions(): {missing}. Add each missing key to "
        f"backend/routes/roles.py in the AI Employees group."
    )


@pytest.mark.parametrize("employee_id", _SEEDED_AI_EMPLOYEE_IDS)
def test_seeded_ai_employee_use_key_is_declared(employee_id: str):
    """Each canonical seeded AI marketplace employee derives its
    ``<short>.use`` key via MarketplaceAgent.__init__ at runtime
    (see services/ai/agents/marketplace.py:64-72). The key must be in
    the permission registry so the GUI can grant it to non-admin
    roles. Admin already has ``["*"]`` wildcard so this test is
    primarily a contract for non-admin role assignability."""
    assert employee_id.startswith("ai_"), (
        f"Test fixture invariant: employee_id {employee_id!r} must start "
        "with 'ai_' so the .use prefix is well-defined"
    )
    short = employee_id[len("ai_"):]
    expected_key = f"{short}.use"

    keys = _registered_permission_keys()
    assert expected_key in keys, (
        f"Seeded AI employee {employee_id!r} derives required permission "
        f"{expected_key!r} via MarketplaceAgent, but that key is not "
        "declared in _core_permissions(). Add it to backend/routes/roles.py "
        "in the AI Employees group, label like "
        f"'Use <Display Name> (...)'."
    )
