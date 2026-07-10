"""Tests for the finance permission registry safety contract.

Locks the structural invariants the PR #164 review surfaced:

  * ``_core_permissions()`` returns every expected ``finance.*``
    key (and ``nova.use``), with no duplicate keys.
  * The finance / operations / deprecated permission block sits at
    the same indentation as every other group in the returned list.
  * No zero-byte test files are tracked under ``backend/tests``.

These are repo-hygiene invariants, not per-feature behaviour — they
live in a dedicated file so a future test reorganisation does not
sweep them into a domain-specific suite by accident.
"""
from __future__ import annotations

import os
from pathlib import Path


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


def test_core_permissions_returns_expected_finance_keys():
    """``_core_permissions()`` must contain a representative finance
    permission set. The fix in PR #164 normalised the block's
    indentation; this assertion pins the runtime contract so a
    future structural edit cannot silently drop the finance keys."""
    from routes.roles import _core_permissions

    perms = _core_permissions()
    keys = {p["key"] for p in perms}

    for expected in (
        "finance.portal",
        "finance.access",          # Access Financial Command Center
        "finance.analytics",
        "finance.export",
        "finance.kpis.view",
        "finance.statements.view",
        "finance.gl.view",
        "finance.sync.trigger",
    ):
        assert expected in keys, (
            f"_core_permissions() missing {expected!r}. Either the "
            "finance block fell out of the return list or the "
            "permission was renamed/removed."
        )


def test_core_permissions_includes_nova_use():
    """``nova.use`` is registered under the AI Employees group;
    the review wanted a representative assertion alongside the
    finance keys."""
    from routes.roles import _core_permissions

    keys = {p["key"] for p in _core_permissions()}
    assert "nova.use" in keys


def test_core_permissions_has_no_duplicate_keys():
    """A duplicate key would silently overwrite the earlier entry's
    group/label when the registry seeds. Catch it here."""
    from routes.roles import _core_permissions

    perms = _core_permissions()
    keys = [p["key"] for p in perms]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    assert not duplicates, (
        f"_core_permissions() contains duplicate keys: {duplicates}. "
        "Duplicates silently overwrite the earlier entry's group/label "
        "at seed time."
    )


def test_finance_block_is_indented_consistently_with_other_groups():
    """Source-line check: every permission line inside
    ``_core_permissions`` starts at the same column as the
    function's first list item. Pre-fix the finance block was at
    4-space indentation while the rest of the list was at 8-space —
    that disparity cost reviewers cycles confirming the block was
    actually returned."""
    import inspect

    import routes.roles as roles_mod

    source = inspect.getsource(roles_mod._core_permissions)
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith('{"key":'):
            indent = len(line) - len(stripped)
            assert indent == 8, (
                f"Permission entry not indented at 8 spaces: "
                f"{line!r} (indent={indent})"
            )


def test_no_zero_byte_test_files_tracked():
    """No ``backend/tests/test_*.py`` file may be zero bytes — that
    creates a false coverage signal. Empty placeholders should be
    deleted, not committed."""
    tests_dir = Path(__file__).parent
    empty = [
        p.name
        for p in tests_dir.glob("test_*.py")
        if p.is_file() and p.stat().st_size == 0
    ]
    assert not empty, (
        f"Zero-byte tracked test files found: {empty}. Either fill "
        "them with meaningful coverage or delete them."
    )


def test_no_pr_number_test_file_names_tracked():
    """Test files must not encode ephemeral PR metadata in their
    filenames. PR-number test files should be migrated into
    domain-specific suites."""
    tests_dir = Path(__file__).parent
    pr_numbered = [
        p.name for p in tests_dir.glob("test_*pr[0-9]*.py")
    ]
    assert not pr_numbered, (
        f"Tracked test files with PR-number names: {pr_numbered}. "
        "Move their contents into domain-specific test files and "
        "delete the PR-number file."
    )


# ---------------------------------------------------------------------------
# H1. nova.export.create reachable by every role that has nova.use.
# ---------------------------------------------------------------------------


import pytest


def _roles_with_nova_use():
    """Return every seeded role that carries nova.use. The H1 fix
    pairs nova.export.create with nova.use across the role
    catalog so this discovery driver list is the right shape for a
    parametrized assertion."""
    from routes.roles import DEFAULT_ROLES

    return [
        r["name"]
        for r in DEFAULT_ROLES
        if "nova.use" in r.get("permissions", [])
    ]


@pytest.mark.parametrize("role_name", _roles_with_nova_use())
def test_role_with_nova_use_also_has_nova_export_create(role_name):
    """Every role that carries nova.use must also carry
    nova.export.create after permission resolution. The codebase
    uses explicit per role grants for feature permissions so the
    membership check below mirrors the resolver behaviour the
    route handler would see. Without this pair the download
    endpoint at nova_exports.py would return 403 for every non
    wildcard user with chat access to Nova."""
    from routes.roles import DEFAULT_ROLES

    role = next(r for r in DEFAULT_ROLES if r["name"] == role_name)
    perms = role.get("permissions", [])
    assert "nova.export.create" in perms, (
        "role " + role_name + " has nova.use but is missing "
        "nova.export.create. Add the explicit grant alongside "
        "nova.use so the download endpoint is reachable."
    )


def test_nova_export_create_registered_in_module_permissions():
    """The H1 fix relies on nova.export.create being a registered
    permission. A grant that names an unregistered key would not
    surface in the role audit output even though has_perm would
    accept it. Lock the registry membership so a future rename of
    the key cannot drift away from the role grants."""
    from routes.nova_exports import _nova_export_permissions

    keys = {p["key"] for p in _nova_export_permissions()}
    assert "nova.export.create" in keys


def test_download_endpoint_reachable_for_lowest_privileged_nova_use_role():
    """A user holding the lowest privileged role that carries
    nova.use must reach the download route without a 403. The
    lowest privileged role today is chat_only which has the smallest
    permissions list. The endpoint returns 404 for a fabricated
    export_id when the permission check passes and 403 when it does
    not. Asserting NOT 403 is the precise gate the H1 fix unlocks."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth_helper import get_current_user
    from routes.nova_exports import router as nova_exports_router
    from routes.roles import DEFAULT_ROLES

    chat_only_role = next(
        r for r in DEFAULT_ROLES if r["name"] == "chat_only"
    )
    user = SimpleNamespace(
        user_id="u_synth_h1",
        email="u_synth_h1@example.invalid",
        role="chat_only",
        permissions=list(chat_only_role.get("permissions", [])),
        tenant_id=None,
    )
    app = FastAPI()
    app.include_router(nova_exports_router, prefix="/api/nova")
    app.dependency_overrides[get_current_user] = lambda: user

    db = MagicMock()
    db.nova_exports = MagicMock()
    db.nova_exports.find_one = AsyncMock(return_value=None)
    with patch("database.get_db", return_value=db), \
         patch(
             "services.nova_engine.nova_export_storage.get_export_artifact",
             new=AsyncMock(return_value=None),
         ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/api/nova/exports/synth_export_does_not_exist/download"
        )
    assert resp.status_code != 403, (
        "chat_only user with nova.use plus nova.export.create must "
        "pass the permission gate. Got 403 with body " + resp.text
    )
    # 404 confirms the permission check passed and the route
    # progressed to the lookup which returned None.
    assert resp.status_code == 404, (
        "expected 404 for a missing export_id once the permission "
        "gate passes got " + str(resp.status_code) + " " + resp.text
    )
