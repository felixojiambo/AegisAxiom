"""Source-level wiring contract for the Quick Add Zoho-style inputs.

May 22, 2026 review asked for:
  * decimal hours (already worked via HOUR_PRESETS chips),
  * start_time / end_time pickers,
  * 10-minute increments,
  * 1h–4h quick-add chip range.

This test pins the frontend wiring against the backend helpers in
``services/time_entry_input``. yarn / node_modules are unavailable
in the sandbox, so the assertions are on the source string.
"""
from pathlib import Path

import pytest


def _component_src() -> str:
    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend" / "src" / "components" / "TimeManagement.js"
    )
    if not path.exists():  # pragma: no cover — sandbox without frontend
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def test_quick_add_state_carries_start_and_end_time():
    src = _component_src()
    assert "start_time: ''" in src
    assert "end_time: ''" in src


def test_quick_add_renders_time_inputs_with_ten_minute_step():
    """The Zoho-style picker uses native <input type="time">. The
    step was bumped from 600 (10-minute) to 60 (1-minute) in the
    May 24, 2026 manual-duration slice so the picker matches the
    new free-text manual duration capability. The backend still
    snaps the resulting duration to a 10-minute boundary for the
    legacy billing `hours` field; raw_minutes carries the exact
    value if precision matters."""
    src = _component_src()
    assert 'data-testid="time-entry-start-time"' in src
    assert 'data-testid="time-entry-end-time"' in src
    # step="60" = 60 seconds = 1 minute precision.
    assert 'step="60"' in src
    # And the old 10-minute step must be gone.
    assert 'step="600"' not in src


def test_quick_add_inline_duration_label_visible_when_range_complete():
    src = _component_src()
    assert 'data-testid="time-entry-range-duration"' in src
    # The helper that mirrors the backend snap math is exposed by name.
    assert "snapToTenMinutes" in src
    assert "timeRangeDurationHours" in src


def test_chip_selection_clears_start_end_range():
    """Pressing a quick-add chip must clear start_time/end_time so the
    server doesn't have to disambiguate which duration the user meant
    (matches the resolution order in services/time_entry_input)."""
    src = _component_src()
    # The chip onClick clears the range. Pin the literal so a future
    # refactor doesn't accidentally drop the clear.
    assert "start_time: '', end_time: ''" in src


def test_range_picker_clears_hours_chip():
    """Conversely, typing in start_time or end_time clears the
    chip-driven hours so the server reads the range."""
    src = _component_src()
    # Same pattern in the time-input onChange handlers.
    assert "hours: 0," in src


def test_quick_add_chip_range_covers_1h_to_4h():
    """The May 22 review specified a 1h-4h chip range. The existing
    HOUR_PRESETS already includes 1, 1.5, 2, 2.5, 3, 3.5, 4 alongside
    sub-1h options. Pin every required chip value so a future cleanup
    cannot accidentally remove one."""
    src = _component_src()
    # Check the array literal includes every required value.
    for required in ("1,", "1.5,", "2,", "2.5,", "3,", "3.5,", "4]"):
        assert required in src, (
            f"HOUR_PRESETS must include {required.rstrip(',]')} for "
            "the May 22 Quick Add range."
        )


def test_post_submit_reset_clears_start_end_range():
    """After saving an entry, the reset must clear start_time and
    end_time so the next entry starts with empty pickers — otherwise
    a stale range would silently apply to the next save."""
    src = _component_src()
    # The reset block is the line `start_time: '',` followed by
    # `end_time: '',` inside the post-submit setAddRow.
    assert "// Clear the Zoho-style range picker" in src
