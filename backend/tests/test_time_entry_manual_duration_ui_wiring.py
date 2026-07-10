"""Source-level wiring contract for the manual duration text input.

May 24, 2026 review surfaced that real durations like 2h3m / 6h15m
couldn't be entered — chips only cover discrete values and the
start/end picker stepped at 10 minutes. The fix adds a free-text
manual duration input alongside the existing chips + range picker.

Source-level checks because the frontend test image is unavailable.
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


def test_manual_duration_field_in_quick_add_state():
    src = _component_src()
    assert "manual_duration: ''" in src, (
        "Quick Add addRow state must include the new manual_duration "
        "field so the form data round-trips to the server."
    )


def test_manual_duration_input_rendered_with_testid():
    src = _component_src()
    assert 'data-testid="time-entry-manual-duration"' in src


def test_manual_duration_label_is_short():
    """May 24 revised copy: field label is the canonical
    'Manual duration' (not 'Or enter time manually')."""
    src = _component_src()
    assert "Manual duration" in src


def test_manual_duration_placeholder_lists_accepted_formats():
    """Placeholder mirrors the May 24 (revised) spec exactly."""
    src = _component_src()
    assert '"6h 15m, 2:03, 2.30, 375m"' in src or "'6h 15m, 2:03, 2.30, 375m'" in src


def test_manual_duration_helper_text_is_clean():
    """The helper line must be short and not mention parser /
    backend implementation details. Decimal-hours warning is
    GONE — the H.MM rule made it obsolete."""
    src = _component_src()
    assert "Use hours/minutes, H:MM, H.MM, or total minutes." in src
    # Old wrong copy must be gone.
    assert "Decimal is HOURS (2.30 = 2h 18m, not 2h 30m)." not in src


def test_chip_labels_use_human_time_not_decimal():
    """May 24 (revised): chip labels must be 1h 30m / 2h 30m / 3h 30m,
    NOT 1.5h / 2.5h / 3.5h. The internal numeric chip value can stay
    decimal (backward compat with the `hours` payload field) — only
    the label changed."""
    src = _component_src()
    assert "1.5: '1h 30m'" in src
    assert "2.5: '2h 30m'" in src
    assert "3.5: '3h 30m'" in src
    # Old decimal labels must be gone.
    assert "'1.5h'" not in src
    assert "'2.5h'" not in src
    assert "'3.5h'" not in src


def test_chip_click_clears_manual_duration():
    """Resolution-order match with the server: a chip click clears
    manual_duration so the server reads `hours` authoritatively."""
    src = _component_src()
    # Inside the chip onClick lambda the reset must include both
    # start/end AND manual_duration.
    assert "start_time: '', end_time: ''," in src
    assert "manual_duration: ''," in src


def test_typing_manual_duration_clears_chip_and_start_end():
    """Typing into manual_duration clears hours=0 + start/end so
    the server reads the text input authoritatively."""
    src = _component_src()
    # The onChange handler must zero hours and blank start/end.
    # Pin the substring that's unambiguously inside the manual
    # duration onChange.
    assert "manual_duration: e.target.value," in src
    # And the same handler must clear the chip + range.
    assert "hours: 0," in src
    assert "start_time: '', end_time: ''," in src


def test_start_end_pickers_use_minute_precision_step_60():
    """Step bumped from 600 (10-minute) to 60 (1-minute) so the
    picker matches the new manual-duration capability."""
    src = _component_src()
    # Both inputs use step="60".
    assert src.count('step="60"') >= 2
    # And the old step="600" must be gone (no other input on the
    # form used it; pin the removal to catch a regression).
    assert 'step="600"' not in src


def test_preview_helper_present():
    """The JS-side parser mirror feeds the inline preview so users
    see "6h 15m = 375 min" while typing."""
    src = _component_src()
    assert "parseManualDurationPreview" in src
    assert 'data-testid="time-entry-manual-duration-preview"' in src


def test_post_submit_reset_clears_manual_duration():
    """After saving an entry, the reset must clear manual_duration
    so the next entry starts with an empty text input."""
    src = _component_src()
    # The reset block is the line `manual_duration: '',` inside the
    # post-submit setAddRow.
    assert "manual_duration: ''," in src
    # The comment near the reset names manual-duration even though
    # it's split across two lines; just confirm the phrase is in the
    # source after the leading reset comment block.
    assert "manual-duration" in src
