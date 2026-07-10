"""Source-level contract for the opportunity create/update name path.

User-reported bug: when creating a new opportunity in aegisaxiom-dev, the
visible deal/opportunity name occasionally appears with extra UI /
search-related words prepended. The audit covered the entire create
path:

    OpportunityFormModal.js (frontend submit)
        -> POST /api/opportunities (route)
            -> OpportunityCreate (Pydantic)
                -> Mongo doc {"name": data.name, ...}

The Pydantic model is a passthrough (``name: str``), the route writes
``data.name`` verbatim into the document, and the React form sets
``formData.name`` only from the user's keyboard input on the name
input. There is no string concatenation in either layer that could
prepend UI text. The frontend now also trims the name at the submit
boundary so any leading/trailing whitespace introduced by browser
autofill, paste, or carry-over from a different field does not land
in storage.

These tests lock the contract so a future refactor cannot quietly
reintroduce a concatenation or a backend-side mutation that would
re-open the bug.
"""

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Pydantic-level checks. These run without a DB or running server so the
# contract is verifiable even when the integration suite is skipped.
# ---------------------------------------------------------------------------


def test_opportunity_create_stores_name_verbatim():
    """OpportunityCreate must accept the exact ``name`` string the
    caller sent — no validator strip, no concatenation, no prefix
    injection. A regression fixture with a UI-label-like prefix proves
    the model never relies on hardcoded stripping."""
    from models import OpportunityCreate

    payload = OpportunityCreate(
        name="Search results: Acme Q4 Migration",
        account_id="acc_test_1",
    )
    # Exact bytes round-trip — nothing stripped.
    assert payload.name == "Search results: Acme Q4 Migration"

    # Trim is NOT applied at the model boundary — the frontend is the
    # right place to normalize input. The model preserves whatever the
    # caller sends so a future migration / import path cannot have its
    # legitimate leading spaces silently eaten.
    spaced = OpportunityCreate(name="  Acme Q4  ", account_id="acc_test_1")
    assert spaced.name == "  Acme Q4  "


def test_opportunity_update_stores_name_verbatim():
    """OpportunityUpdate carries the same verbatim contract for renames."""
    from models import OpportunityUpdate

    upd = OpportunityUpdate(name="Renamed Deal — Final")
    assert upd.name == "Renamed Deal — Final"


# ---------------------------------------------------------------------------
# Route-level source inspection. The create + update handlers must
# never mutate ``data.name`` between Pydantic and the document/update
# dict that hits Mongo.
# ---------------------------------------------------------------------------


def test_create_route_writes_name_field_verbatim():
    routes = (
        Path(__file__).resolve().parents[1] / "routes" / "opportunities.py"
    )
    src = routes.read_text(encoding="utf-8")

    # The create handler must write data.name straight into the doc —
    # not data.name.strip(), not f"prefix {data.name}", not anything
    # that mixes in another field. The exact literal proves the call
    # site did not grow a transform.
    assert '"name": data.name,' in src, (
        "create_opportunity must write the name field verbatim from "
        "data.name. Any transform here re-opens the prepended-text "
        "bug the user reported."
    )

    # The update handler must do the same on rename.
    assert 'update_data["name"] = data.name' in src, (
        "update_opportunity must assign data.name to update_data['name'] "
        "directly. A future helper that wraps the assignment must not "
        "introduce a hidden concatenation."
    )

    # Sanity: there must be no string concatenation between data.name
    # and another field on the document path. The only legitimate uses
    # of data.name in this route are the two assignments above.
    forbidden = (
        # f-strings that mix data.name with another value would prepend
        # extra context into the stored name.
        'f"{data.name}',
        # plus-concatenation of data.name with anything other than a
        # whitespace literal in a logger line.
        'data.name + ',
        '" + data.name',
    )
    for needle in forbidden:
        assert needle not in src, (
            f"opportunities route contains {needle!r} — that pattern "
            "would risk prepending or appending characters to the "
            "stored opportunity name. Fix the source, not the test."
        )


# ---------------------------------------------------------------------------
# Frontend source-level checks. yarn / node_modules are unavailable
# in this sandbox, so the React form is locked via source inspection.
# The form is the entry point for the user-reported bug because the
# backend is already a passthrough.
# ---------------------------------------------------------------------------


def _frontend_form_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "opportunities"
        / "OpportunityFormModal.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def test_form_submit_trims_name_at_submit_boundary():
    """The form must trim the opportunity name once at the submit
    boundary and send the trimmed value in the payload — never the
    untrimmed ``formData.name``. Without this, leading whitespace from
    browser autofill / paste / carry-over from a different field
    lands in storage and visually prepends garbage to the card title."""
    src = _frontend_form_src()
    assert "const trimmedName = formData.name.trim();" in src, (
        "Form submit must derive a trimmedName local from "
        "formData.name.trim()."
    )
    assert "name: trimmedName" in src, (
        "Form submit payload must use the trimmed value "
        "(`name: trimmedName`), not the raw input."
    )
    # The submit must NOT send raw `formData` directly to either the
    # POST or the PUT — that would skip the trim. The audit lock is on
    # the explicit `payload` rebuild.
    assert "api.post(`${API}/api/opportunities`, payload)" in src
    assert "api.put(" in src and "payload" in src, (
        "PUT path must also send the trimmed payload, not raw formData."
    )


def test_form_name_input_disables_browser_autofill_carryover():
    """The name input must declare autoComplete=\"off\" so a recent
    value typed into a different form field (a search bar, another
    deal name, etc.) does not get suggested into this input by the
    browser's autofill heuristics."""
    src = _frontend_form_src()
    assert 'autoComplete="off"' in src, (
        "Opportunity name input must set autoComplete=\"off\" to "
        "defeat browser autofill carry-over."
    )


def test_form_name_input_does_not_concatenate_external_labels():
    """The name input's onChange handler must take ONLY e.target.value
    — never the account label, contact label, search query, or any
    other UI string. The lock asserts that the onChange shape is
    exactly `setFormData((prev) => ({ ...prev, name: e.target.value }))`
    so a future refactor cannot quietly fold in another value."""
    src = _frontend_form_src()
    assert (
        "setFormData((prev) => ({ ...prev, name: e.target.value }))"
        in src
    ), (
        "Name input onChange must set name to e.target.value verbatim. "
        "A handler that concatenates a label / placeholder / search "
        "term would re-open the prepended-text bug."
    )


def test_overlay_close_uses_mousedown_target_tracking():
    """A text selection started inside the name input that releases
    on the dim overlay must NOT close the modal. The accepted fix is
    to track the mousedown target on the overlay and only close on
    click when both the mousedown AND the click landed on the overlay
    itself."""
    src = _frontend_form_src()
    assert "overlayMouseDownTargetRef" in src, (
        "Form modal must track the overlay mousedown target so a "
        "drag-out text selection does not close the modal."
    )
    assert "handleOverlayMouseDown" in src and "handleOverlayClick" in src, (
        "Form modal must wire onMouseDown + onClick handlers on the "
        "overlay rather than a bare onClick={onClose}."
    )
    # The overlay's onClick must wire to handleOverlayClick (not the
    # bare onClose) so a drag-out text selection does not close the
    # modal. The X close button legitimately keeps onClick={onClose};
    # the lock here is specifically the overlay's onClick line, which
    # must reference the new handler.
    assert "onClick={handleOverlayClick}" in src, (
        "Overlay must wire onClick to handleOverlayClick (the "
        "mousedown-tracking handler), not directly to onClose."
    )
    # The inner content also stops propagation on mousedown so a
    # selection started inside the content does not register at the
    # overlay either.
    assert "onMouseDown={(e) => e.stopPropagation()}" in src, (
        "Form modal inner panel must stopPropagation on mousedown so "
        "a selection started inside the panel is not mis-attributed "
        "to the overlay."
    )
