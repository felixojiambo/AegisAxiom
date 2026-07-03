"""Source-level wiring contract for the May 25, 2026
Project -> Contract linkage UI work.

The investigation found that the backend pipeline already
supports the rollup; the gap was that the project creation
wizard had no `contract_id` field, and Time Management never
surfaced the linked contract (or its absence) to the user.

This file locks the two UI fixes:

  1. CreateProjectWizard.js declares a Linked Contract <select>,
     loads contracts on wizard open, includes contract_id in the
     create payload for both the blank and template flows, and
     warns the user when no contract is linked.

  2. TimeManagement.js surfaces the selected project's linked
     contract in the Quick Add row. When the project has no
     contract, the verbatim spec warning renders.

yarn / node_modules unavailable in the build sandbox — source
inspection only.
"""
from pathlib import Path

import pytest


def _frontend_root() -> Path:
    return Path(__file__).resolve().parents[1].parent / "frontend" / "src"


def _wizard_src() -> str:
    path = _frontend_root() / "components" / "projects" / "CreateProjectWizard.js"
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def _tm_src() -> str:
    path = _frontend_root() / "components" / "TimeManagement.js"
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def _project_detail_src() -> str:
    path = (
        _frontend_root() / "components" / "projects" / "ProjectDetailPage.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# CreateProjectWizard — Linked Contract field
# ---------------------------------------------------------------------------


def test_wizard_loads_contracts_on_open():
    """Wizard fetches contracts when it opens so the <select>
    is ready by the time the user reaches Step 3."""
    src = _wizard_src()
    assert "/api/contracts?status=active" in src
    # Soft-fail behaviour — a 4xx leaves contracts as an empty
    # list rather than blocking project creation entirely.
    assert "setContracts([])" in src


def test_wizard_renders_linked_contract_select():
    src = _wizard_src()
    assert 'data-testid="wizard-field-contract"' in src
    # The <select> renders contracts from state.
    assert "contracts" in src and "contract_id" in src


def test_wizard_warns_when_no_contract_linked():
    """Verbatim spec copy must render when the user has not picked
    a contract. NOT a blocking validation — internal R&D projects
    legitimately run without a contract."""
    src = _wizard_src()
    assert 'data-testid="wizard-contract-warning"' in src
    assert (
        "This project is not linked to a contract. Approved billable"
        in src
    )


def test_wizard_submits_contract_id_in_both_paths():
    """Blank-flow body AND template-flow body both include
    contract_id when the user picked one. The spread is gated on
    `contractId` truthiness so an empty selection sends NOTHING
    (instead of an empty string the backend would still store)."""
    src = _wizard_src()
    # Two `...(contractId ? { contract_id: contractId } : {})` spreads.
    assert src.count("contractId ? { contract_id: contractId }") >= 2, (
        "Wizard must include contract_id in BOTH the template "
        "POST body AND the blank-flow POST body."
    )


def test_wizard_includes_useful_label_in_each_option():
    """Per spec: 'Show contract name, account, type,
    purchased/used/remaining hours.' The option label is the
    user's only context — pin the fields it surfaces."""
    src = _wizard_src()
    # The option-label assembly composes c.name + account_name +
    # contract_type + hours summary. Locking the call shapes.
    assert "c.account_name" in src
    assert "c.contract_type" in src
    assert "c.total_hours_purchased" in src
    # Hours-left math reads either live hours_consumed or the
    # Zoho-imported baseline.
    assert "c.imported_consumed_hours" in src


def test_wizard_filters_to_active_contracts_only():
    """A closed/cancelled contract shouldn't be a valid home for
    a brand-new project. The fetch URL pins this filter."""
    src = _wizard_src()
    assert "status=active" in src


# ---------------------------------------------------------------------------
# TimeManagement — linked contract surface + warning
# ---------------------------------------------------------------------------


def test_time_management_surfaces_linked_contract_for_selected_project():
    src = _tm_src()
    assert 'data-testid="time-entry-linked-contract"' in src
    # Contract is resolved from `projects.find(p => p.project_id
    # === addRow.project_id)` plus `contracts.find(c =>
    # c.contract_id === selectedProject.contract_id)`. Pin both.
    assert "p.project_id === addRow.project_id" in src
    assert "c.contract_id === selectedProject.contract_id" in src


def test_time_management_warns_when_project_has_no_contract():
    """Verbatim spec copy. The warning renders only when a project
    is selected AND its contract_id is empty — not when no
    project is picked yet."""
    import re
    src = _tm_src()
    normalised = re.sub(r"\s+", " ", src)
    assert 'data-testid="time-entry-no-contract-warning"' in src
    assert (
        "This project is not linked to a contract. Approved billable "
        "time will not consume contract hours." in normalised
    )


def test_time_management_warning_does_not_block_submission():
    """The warning is informational — billable time on an
    unlinked project still saves (it just won't roll up to any
    contract). The submission gate at handleQuickAdd must not
    additionally check contract_id."""
    src = _tm_src()
    # The submission guard checks project_id, date, notes — it
    # does NOT include contract_id.
    idx = src.find("if (!addRow.project_id || !addRow.date || !addRow.notes.trim())")
    assert idx != -1, "handleQuickAdd guard signature must be unchanged."
    # Locally the line ends with `)`; no `addRow.contract_id` added.
    end = src.find(")", idx)
    assert "contract_id" not in src[idx:end]


def test_time_management_does_not_match_contract_by_name():
    """Defensive lock against the spec's 'Do not match contracts
    and projects by name' rule. The linked-contract block must
    look up by contract_id only."""
    src = _tm_src()
    # The match expression is `c.contract_id === selectedProject.contract_id`.
    # An accidental refactor to `c.name === selectedProject.name`
    # would silently link by name — pin against it.
    assert "c.name === selectedProject.name" not in src
    assert "p.name === " not in src or "contract" not in src.split("p.name === ")[1][:80]


# ---------------------------------------------------------------------------
# ProjectDetailPage SettingsTab — Linked Contract edit field
# ---------------------------------------------------------------------------
#
# The May 25 wizard work added the contract field to project CREATION
# only. The follow-up adds it to the SettingsTab edit surface so an
# existing project (the user's TEST01 case) can be linked to a contract
# without re-creating it. Backend ProjectUpdate already accepts
# contract_id — the SettingsTab UI was the remaining gap.


def test_settings_tab_renders_linked_contract_select():
    src = _project_detail_src()
    assert 'data-testid="settings-field-contract"' in src
    # Field label visible to users.
    assert ">Linked contract<" in src


def test_settings_tab_loads_contracts_on_mount():
    src = _project_detail_src()
    assert "/api/contracts?status=active" in src
    # Soft-fail behaviour so a user without contracts.view can still
    # open the settings tab and edit OTHER fields. setContracts is
    # populated only on a 2xx; the .catch leaves the empty default.
    assert "setContracts(items)" in src


def test_settings_tab_initialises_from_project_contract_id():
    """The form state must initialise FROM project.contract_id so
    the picker shows the current link when the user opens the tab.
    Empty-string default keeps the picker controllable when the
    project has no link yet."""
    src = _project_detail_src()
    assert "useState(project.contract_id ?? '')" in src


def test_settings_tab_renders_synthetic_option_for_stale_current_contract():
    """Same defensive UX as the PM picker: if the current
    contract_id isn't in the active-contracts list (closed /
    cancelled / inactive), inject a synthetic option so the
    field doesn't read blank. Lock the testid for browser smoke."""
    src = _project_detail_src()
    assert 'data-testid="settings-field-contract-current"' in src
    assert "(current)" in src


def test_settings_tab_marks_contract_dirty_when_changed():
    """Dirty detection must include the contract field so the
    Save button enables when the user changes the link. Mirror
    of pmDirty."""
    src = _project_detail_src()
    assert "const contractDirty = (contractId || '') !== (project.contract_id ?? '');" in src
    # isDirty composite includes contractDirty.
    assert "contractDirty ||" in src or "|| contractDirty" in src


def test_settings_tab_save_sends_null_when_contract_cleared():
    """Mirror of the pm_id null-vs-empty-string treatment. Sending
    an empty string would leave the field untouched on the
    backend partial PATCH; sending null explicitly clears the
    link in Mongo."""
    src = _project_detail_src()
    assert "patch.contract_id = contractId || null;" in src
    # And the branch is guarded by contractDirty so a no-op save
    # doesn't gratuitously include the field.
    idx = src.find("patch.contract_id = contractId || null;")
    window = src[max(0, idx - 200):idx]
    assert "if (contractDirty)" in window


def test_settings_tab_warns_when_no_contract_linked():
    """Same verbatim copy as the wizard so operators see one
    consistent message wherever the link is missing."""
    src = _project_detail_src()
    assert 'data-testid="settings-field-contract-warning"' in src
    import re
    normalised = re.sub(r"\s+", " ", src)
    assert (
        "This project is not linked to a contract. Approved billable "
        "time will not consume contract hours." in normalised
    )


def test_settings_tab_does_not_match_contract_by_name():
    """Defensive lock: the picker must select by contract_id only.
    No name-based comparisons anywhere in the contract block."""
    src = _project_detail_src()
    # currentContractInList uses contract_id equality, NOT name.
    assert "c.contract_id === currentContractId" in src
    # And no accidental name-based predicate sneaks in.
    assert "c.name === " not in src


def test_settings_tab_does_not_introduce_a_blocking_validation_for_missing_contract():
    """Saving a project without a linked contract must still work.
    The Save button's disabled predicate must not gate on the
    contract field."""
    src = _project_detail_src()
    # The disabled predicate today reads:
    #   disabled={saving || !name.trim() || !isDirty || !wavesThreadsValid}
    # No contract guard is allowed.
    import re
    # Anchor on the disabled attribute of the Save button.
    m = re.search(
        r"disabled=\{saving \|\| !name\.trim\(\) \|\| !isDirty \|\| !wavesThreadsValid\}",
        src,
    )
    assert m, (
        "SettingsTab Save button's disabled predicate must remain "
        "`saving || !name.trim() || !isDirty || !wavesThreadsValid` "
        "— blocking on a missing contract would prevent legitimate "
        "internal/R&D projects from being saved."
    )
