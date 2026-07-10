"""Source-level wiring contract for the Contract Approval Access
admin page (renamed from "Contract Approver Overrides" on
2026-05-24).

This file replaces the old test_contract_approver_overrides_ui_wiring
suite. It pins the new copy, the new endpoint shape, the new
single-table layout with pagination/search/filter, and the new
source-specific action behaviour (Managed by role / Revoke
override / Grant override / Role + Override hint).

Whitelist unchanged: the page still writes EXACTLY one permission
key — contracts.approve — to the user's extra_permissions array.
Every other entry is preserved verbatim.

yarn / node_modules unavailable in the build sandbox — source
inspection only.
"""

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Persistence model — the User / UserUpdateRequest shapes the page
# still depends on.
# ---------------------------------------------------------------------------


def test_user_update_request_still_accepts_extra_permissions():
    """The page sends `{ extra_permissions: [...] }` partial PUTs.
    If UserUpdateRequest stops accepting that field the override
    toggle silently breaks."""
    from models import UserUpdateRequest

    body = UserUpdateRequest(extra_permissions=["contracts.approve"])
    assert body.extra_permissions == ["contracts.approve"]
    dumped = body.model_dump(exclude_unset=True)
    assert "extra_permissions" in dumped

    empty = UserUpdateRequest()
    assert empty.extra_permissions is None


def test_user_model_still_persists_extra_permissions_array():
    """The User model must keep extra_permissions as a List[str]
    default_factory=list. The page's append/remove semantics
    assume an iterable."""
    from models import User

    user = User(
        user_id="u_test_1",
        email="approver@example.test",
        name="Approver Test",
        password_hash="hash",
        role="sales_ops",
    )
    assert user.extra_permissions == []
    assert isinstance(user.extra_permissions, list)


# ---------------------------------------------------------------------------
# Source helpers
# ---------------------------------------------------------------------------


def _page_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend" / "src" / "components" / "admin"
        / "ContractApprovalAccess.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def _app_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend" / "src" / "App.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def _layout_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend" / "src" / "components" / "Layout.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def _old_page_file_is_gone() -> bool:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend" / "src" / "components" / "admin"
        / "ContractApproverOverrides.js"
    )
    return not path.exists()


# ---------------------------------------------------------------------------
# Component identity + rename
# ---------------------------------------------------------------------------


def test_old_component_file_was_renamed():
    """The May 24 rename moved ContractApproverOverrides.js ->
    ContractApprovalAccess.js. Both can't exist simultaneously."""
    assert _old_page_file_is_gone(), (
        "ContractApproverOverrides.js must be removed/renamed — the "
        "page is now ContractApprovalAccess.js."
    )


def test_page_exports_renamed_component():
    src = _page_src()
    assert "export default function ContractApprovalAccess()" in src


def test_app_imports_renamed_component():
    src = _app_src()
    assert (
        "import ContractApprovalAccess from './components/admin/ContractApprovalAccess'"
        in src
    )
    assert (
        "import ContractApproverOverrides from './components/admin/ContractApproverOverrides'"
        not in src
    )
    # The route still mounts at the old URL for backward-compat.
    assert 'path="admin/contract-approvers"' in src
    assert "<ContractApprovalAccess />" in src


def test_sidebar_label_is_contract_approval_access():
    src = _layout_src()
    idx = src.find("path: '/admin/contract-approvers',")
    assert idx != -1
    block = src[idx:idx + 400]
    assert "label: 'Contract Approval Access'" in block
    assert "label: 'Contract Approvers'" not in block


# ---------------------------------------------------------------------------
# Page title + user-friendly copy
# ---------------------------------------------------------------------------


def test_page_title_is_contract_approval_access():
    src = _page_src()
    assert "Contract Approval Access" in src
    assert "Contract approver overrides" not in src
    assert "Contract Approver Overrides" not in src


def _page_src_user_visible() -> str:
    """Return the page source with developer-only commentary
    stripped: leading JSDoc block (/** ... */), all /* ... */
    block comments, and all // line comments. What remains is
    roughly what the user can see in the rendered JSX (plus
    JS identifiers / data-testid strings, which are intentionally
    not stripped so testid assertions elsewhere keep working).
    """
    import re
    src = _page_src()
    # Strip /* ... */ blocks (covers both the top JSDoc and any
    # mid-source block comments).
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    # Strip // line comments — but only when they start at column
    # boundaries with whitespace, to avoid mangling a string that
    # contains "//". Cheap heuristic: drop everything from "//" to
    # end-of-line when "//" is preceded by whitespace.
    src = re.sub(r"(^|[ \t])//.*$", "", src, flags=re.MULTILINE)
    return src


def test_explainer_copy_is_user_friendly():
    """User-facing copy must NOT leak backend jargon
    (extra_permissions, persistence shape, deferred_items.md,
    'per-user grant of contracts.approve'). Source is stripped
    of JS/JSDoc/inline comments first — developer commentary is
    intentionally exempt."""
    user_visible = _page_src_user_visible()
    forbidden_in_copy = (
        "extra_permissions array",
        "backend persistence shape",
        "deferred_items.md",
        "per-user grant of",
    )
    for token in forbidden_in_copy:
        assert token not in user_visible, (
            f"User-facing copy must not contain {token!r}."
        )
    # And the canonical new copy is present. Normalise whitespace
    # so the assertion isn't sensitive to JSX line-wraps.
    import re
    normalised = re.sub(r"\s+", " ", _page_src())
    assert "Manage who can approve contracts across this tenant." in normalised
    assert (
        "Per-contract approver assignment is not currently supported."
        in normalised
    )


def test_roles_link_present():
    src = _page_src()
    assert "Roles &amp; Permissions" in src or "Roles & Permissions" in src
    assert 'to="/admin/roles"' in src


# ---------------------------------------------------------------------------
# Endpoint contract
# ---------------------------------------------------------------------------


def test_page_calls_new_pagination_endpoint():
    src = _page_src()
    assert "/api/admin/contract-approval-access" in src
    assert "api.get(" in src
    assert "approval_filter" in src
    assert "page_size" in src


def test_page_writes_via_existing_admin_users_put():
    """Mutations stay on the existing PUT route — no /grant or
    /revoke endpoints were added."""
    src = _page_src()
    assert "`${API}/api/admin/users/${user.user_id}`" in src
    for forbidden in (
        "/api/admin/contract-approval-access/",
        "/grant-override",
        "/revoke-override",
    ):
        assert forbidden not in src


def test_whitelist_is_single_key_contracts_approve():
    src = _page_src()
    assert "const APPROVER_OVERRIDE_KEY = 'contracts.approve';" in src
    for forbidden in (
        "contracts.edit",
        "contracts.delete",
        "contracts.approve.all",
        "invoice_batches.approve",
        "finance.portal",
    ):
        assert f"'{forbidden}'" not in src
        assert f'"{forbidden}"' not in src


def test_unrelated_extras_preserved_on_toggle():
    src = _page_src()
    assert "current.includes(APPROVER_OVERRIDE_KEY)" in src
    assert "current.filter((p) => p !== APPROVER_OVERRIDE_KEY)" in src


def test_put_body_includes_only_extra_permissions():
    """The PUT body must contain ONLY extra_permissions — bundling
    `role` would trigger the admin route's role-change gate."""
    src = _page_src()
    import re
    put_call_pattern = re.compile(
        r"api\.put\(\s*`\$\{API\}/api/admin/users/\$\{user\.user_id\}`\s*,\s*\{([^}]+)\}\s*\)",
        re.DOTALL,
    )
    matches = put_call_pattern.findall(src)
    assert matches, "Could not locate api.put call."
    for body in matches:
        for forbidden in ("role:", "name:", "is_active:", "account_id:"):
            assert forbidden not in body


# ---------------------------------------------------------------------------
# Permission gate
# ---------------------------------------------------------------------------


def test_page_gates_on_users_view_and_isadmin():
    src = _page_src()
    assert "hasPermission('users.view')" in src
    assert "isAdmin()" in src
    assert "&& isAdmin()" in src, (
        "canSee must AND isAdmin with the users.view check, not OR."
    )


# ---------------------------------------------------------------------------
# Filter bar
# ---------------------------------------------------------------------------


def test_filter_bar_has_search_filter_pagesize():
    src = _page_src()
    for testid in (
        "contract-approval-access-search",
        "contract-approval-access-filter",
        "contract-approval-access-page-size",
    ):
        assert testid in src


def test_approval_filter_options_match_backend_contract():
    src = _page_src()
    for value in ("'all'", "'can_approve'", "'role'", "'override'", "'none'"):
        assert f"value: {value}" in src, (
            f"Approval filter <select> must include {value} option."
        )


def test_page_size_options_match_spec():
    src = _page_src()
    assert "PAGE_SIZE_OPTIONS = [25, 50, 100]" in src
    assert "DEFAULT_PAGE_SIZE = 50" in src


# ---------------------------------------------------------------------------
# Table — columns + source-specific actions
# ---------------------------------------------------------------------------


def test_table_columns_match_spec():
    src = _page_src()
    for column in ("User", "Email", "Role", "Approval source", "Action"):
        assert f">{column}<" in src, (
            f"Table must render {column!r} column header."
        )


def test_source_badges_render_all_four_states():
    src = _page_src()
    for testid in (
        "contract-approval-access-source-role",
        "contract-approval-access-source-override",
        "contract-approval-access-source-role-and-override",
        "contract-approval-access-source-none",
    ):
        assert testid in src
    # The unique badge label — anchors the four-state distinction in
    # user-visible text. The other three labels ("Role", "Override",
    # "None") are common enough words that the testid checks above
    # are the truer source of truth for "we rendered the badge".
    assert "Role + Override" in src


def test_role_only_users_show_managed_by_role_and_no_revoke():
    src = _page_src()
    assert "Managed by role" in src
    assert "contract-approval-access-managed-" in src
    # Action branching uses these three predicates.
    assert "showRevoke = has_override_permission" in src
    assert "showGrant = source === 'none'" in src
    assert "managedByRole = source === 'role'" in src


def test_role_plus_override_revoke_has_helper_text():
    src = _page_src()
    assert (
        "This will remove only the direct override. The user may still"
        in src
    )


def test_no_copy_implies_per_contract_scope():
    """Negative copy lock — the page must not imply approvals can
    be scoped to a single contract."""
    src = _page_src()
    forbidden_phrases = (
        "per-contract",
        "per contract",
        "for this contract",
        "specific contract",
    )
    lowered = src.lower()
    for phrase in forbidden_phrases:
        if phrase in lowered:
            assert "not currently supported" in lowered, (
                f"Found {phrase!r} without an accompanying denial — "
                "the page must not imply per-contract scope."
            )


# ---------------------------------------------------------------------------
# Pagination + empty states
# ---------------------------------------------------------------------------


def test_pagination_controls_present():
    src = _page_src()
    for testid in (
        "contract-approval-access-pagination",
        "contract-approval-access-range",
        "contract-approval-access-prev",
        "contract-approval-access-next",
    ):
        assert testid in src
    assert "Showing {showingStart}–{showingEnd} of {total}" in src


def test_empty_states_distinct_per_context():
    src = _page_src()
    expected_empties = (
        "No users match your search.",
        "No users have direct contract approval overrides.",
        "No users currently have contract approval access.",
    )
    for line in expected_empties:
        assert line in src, f"Empty-state line {line!r} missing."


def test_loading_and_error_and_denied_states():
    src = _page_src()
    for testid in (
        "contract-approval-access-loading",
        "contract-approval-access-error",
        "contract-approval-access-denied",
    ):
        assert testid in src


# ---------------------------------------------------------------------------
# URL-backed state + refresh
# ---------------------------------------------------------------------------


def test_search_filter_page_state_in_url():
    src = _page_src()
    assert "useSearchParams" in src
    for key in ("search", "approval_filter", "page", "page_size"):
        assert f"searchParams.get('{key}')" in src, (
            f"Query key {key!r} must be read from useSearchParams "
            "so refresh preserves the view."
        )


def test_refresh_button_does_not_reset_filters():
    """The Refresh button's onClick must be loadAccess, not a
    helper that clears filters first. loadAccess reads URL-backed
    state via the closure on the current render, so it preserves
    whatever search/filter/page the user is on."""
    src = _page_src()
    # Find the refresh button block — props can be ordered in any
    # JSX style, so widen the window enough to span the whole tag.
    refresh_idx = src.find("contract-approval-access-refresh")
    assert refresh_idx != -1
    # Look back to the opening `<button` of this element.
    button_open = src.rfind("<button", 0, refresh_idx)
    assert button_open != -1
    button_block = src[button_open:refresh_idx + 200]
    assert "onClick={loadAccess}" in button_block, (
        "Refresh must call loadAccess directly so URL-backed "
        "search/filter/page state is preserved."
    )


# ---------------------------------------------------------------------------
# Backend route mount + nav entry
# ---------------------------------------------------------------------------


def test_route_mounted_in_app_with_canonical_permission_gate():
    src = _app_src()
    idx = src.find('path="admin/contract-approvers"')
    assert idx != -1
    window = src[idx:idx + 400]
    assert "'users.edit'" in window or '"users.edit"' in window


def test_nav_entry_uses_renamed_label():
    src = _layout_src()
    assert "path: '/admin/contract-approvers'," in src
    idx = src.find("path: '/admin/contract-approvers',")
    block = src[idx:idx + 400]
    assert "label: 'Contract Approval Access'" in block
    assert "permission: ['users.edit', 'users.view']" in block
    operations_idx = src.find("id: 'operations',")
    finance_idx = src.find("id: 'finance',")
    assert operations_idx < idx < finance_idx, (
        "Nav entry must live in the Operations group."
    )
