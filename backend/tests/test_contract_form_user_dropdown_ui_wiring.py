"""Source-level wiring contract for the contractor user dropdown.

The user dropdown in the Contract create/edit form is the contractor
picker inside the Resources section of ``ContractFormFields.js``. It
filters tenant users to those whose ``resource_type_assignments[]``
match the contract's selected resource type.

The May 22, 2026 review reported the dropdown "not working." Root
cause: strict ``===`` comparison on ``resource_type_name`` failed
when the contract's name string and the user's assignment name
differed only in case or trailing whitespace. The PART 8 fix:

  * compare case-insensitively + trim whitespace,
  * surface an explicit empty-state message that points at User
    Management when no users carry the assignment.

Source-level checks because the frontend test image is unavailable.
"""
from pathlib import Path

import pytest


def _component_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend" / "src" / "components" / "contracts"
        / "ContractFormFields.js"
    )
    if not path.exists():  # pragma: no cover — sandbox without frontend
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def test_resource_type_match_is_case_insensitive_and_trimmed():
    """The comparison must use trim().toLowerCase() so 'L1 Engineer'
    matches 'l1 engineer ' from the user's assignments. The previous
    strict === was the bug."""
    src = _component_src()
    # New form of the comparison: build a single normalised want
    # string and compare both sides through it.
    assert ".trim().toLowerCase()" in src, (
        "The contractor dropdown must compare resource_type_name "
        "case-insensitively + whitespace-trimmed."
    )


def test_user_dropdown_renders_explicit_empty_state():
    """When zero tenant users carry the selected resource_type
    assignment, the form must render a visible note pointing at
    Admin → User Management. The old behaviour silently rendered an
    empty <select> that looked broken."""
    src = _component_src()
    assert "contract-resource-picker-empty-" in src
    # Operator-facing copy explicitly mentions User Management.
    assert "User Management" in src


def test_user_dropdown_uses_admin_users_endpoint():
    """The Contracts page fetches the candidate users from
    /api/admin/users (tenant-scoped). Pin that wiring in the parent
    Contracts.js."""
    parent = (
        Path(__file__).resolve().parents[1].parent
        / "frontend" / "src" / "components" / "Contracts.js"
    )
    if not parent.exists():  # pragma: no cover
        pytest.skip("frontend not mounted")
    parent_src = parent.read_text(encoding="utf-8")
    assert "/api/admin/users" in parent_src
    # The response is a raw array (the route returns the user list
    # directly, not wrapped in an envelope). Tolerance for either
    # shape is preserved so a future envelope swap is non-breaking.
    assert "Array.isArray(udata) ? udata : udata.items || udata.users || []" in parent_src


def test_form_persists_selected_user_into_resource_user_id_field():
    """The picker writes to the contract's resources[].user_id, which
    is what the backend create/update handler reads."""
    src = _component_src()
    # The updateResource helper handles the user_id field path; pin
    # the literal so a future refactor cannot quietly rename it.
    assert "updateResource(i, 'user_id'" in src
    # And the option value is the user's user_id (AEGISAXIOM id, not email
    # or external id).
    assert "<option key={u.user_id} value={u.user_id}>" in src


def test_form_imports_no_external_user_id_fields():
    """Imported users may carry external_id / zoho_user_id; the
    picker must use the AEGISAXIOM user_id, never an external id."""
    src = _component_src()
    assert "external_user_id" not in src
    assert "zoho_user_id" not in src
