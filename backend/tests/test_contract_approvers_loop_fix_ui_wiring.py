"""Source-level wiring contract for the Contract Approvers loop fix.

The original ContractApproverOverrides page sat on a permanent
"Loading…" + visible blink because:

  const canSee = (hasPermission(...) || hasPermission('*')) && isAdmin;

usePermissions() returns ``isAdmin`` as a FUNCTION REFERENCE, and the
hook is called every render, so the reference changes every render.
``canSee`` therefore changed every render, the useEffect deps
``[canSee, loadAll]`` fired the load on every render, and each load
called setLoading(true) + setUsers([]) which produced more renders.

This test pins the corrected behaviour:
  * ``isAdmin`` is called as a function, not used as a value.
  * A request-id guard exists so stale fetches don't clobber newer
    state.
  * The component does not clear ``users`` mid-refresh (no blink).
  * An error path exposes a Retry control.
  * Search is debounced before it drives the filter pass.

yarn / node_modules are unavailable in the sandbox, so the
assertions are on the component source string — same pattern every
other ``*_ui_wiring.py`` test uses.
"""
from pathlib import Path

import pytest


def _component_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend" / "src" / "components" / "admin"
        / "ContractApproverOverrides.js"
    )
    if not path.exists():  # pragma: no cover — sandbox without frontend
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def test_canSee_calls_isAdmin_as_function():
    src = _component_src()
    # The fix is the parentheses on isAdmin(). The bug was using the
    # bare function ref as a value, which made canSee unstable across
    # renders. Pin the call form.
    assert "isAdmin()" in src, (
        "ContractApproverOverrides must call isAdmin() — using the "
        "bare function reference in a boolean expression triggers "
        "the loop the original blink was caused by."
    )


def test_canSee_is_normalised_to_boolean():
    src = _component_src()
    # Either by Boolean() or by the explicit function call above —
    # the resulting expression must yield a stable boolean across
    # renders even if usePermissions changes object identity.
    assert "Boolean(" in src and "isAdmin()" in src


def test_load_uses_request_seq_guard():
    """A second loadAll() before the first finishes must not clobber
    the newer state with the older response."""
    src = _component_src()
    assert "requestSeqRef" in src
    assert "useRef" in src


def test_load_does_not_clear_users_during_refresh():
    """Keeping prior data visible during refresh is what kills the
    blink. The load function must not call setUsers([]) on the
    happy path."""
    src = _component_src()
    # The exact failure pattern from the prior implementation was
    # `setUsers([])` inside the load before the response landed.
    # No source line should clear the array on the happy path; the
    # only acceptable setUsers calls are the ones that commit the
    # newly fetched array.
    assert "setUsers([])" not in src, (
        "load() must not clear users on refresh — that triggers the "
        "blink the bug originally produced."
    )


def test_search_is_debounced():
    """A real input does not need to filter on every keystroke; the
    debounce keeps the filter pass off the hot path."""
    src = _component_src()
    assert "debouncedSearch" in src
    assert "SEARCH_DEBOUNCE_MS" in src or "setTimeout" in src


def test_error_surface_exposes_retry_button():
    src = _component_src()
    assert "contract-approver-overrides-retry" in src


def test_cold_load_spinner_only_first_time():
    """After the first successful load, subsequent refreshes must
    keep the table visible while the spinner sits in the header,
    not replace the table with a spinner."""
    src = _component_src()
    assert "hasLoadedOnce" in src
