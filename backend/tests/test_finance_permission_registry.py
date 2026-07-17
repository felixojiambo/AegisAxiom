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
