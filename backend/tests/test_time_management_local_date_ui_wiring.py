"""Time Management calendar date — bug fix locked at two layers.

May 25, 2026 bug:
  On Mon May 25, the Time Management page showed
    Header:  "Week of May 25 — May 30"
    Columns: Mon 24, Tue 25, ..., Sun 30
  instead of
    Header:  "Week of May 25 — May 31"
    Columns: Mon 25, Tue 26, ..., Sun 31

  Root cause: the calendar-day code in
  frontend/src/components/TimeManagement.js used
    new Date(weekStart + 'T00:00:00')   // parses as local midnight
    .toISOString().split('T')[0]        // converts BACK through UTC
  to round-trip a YYYY-MM-DD calendar day. In positive-offset
  timezones (Africa/Nairobi UTC+3 is the canonical example) the
  toISOString step lands on the PREVIOUS UTC calendar day, so
  weekDates[0] silently became 2026-05-24.

Fix:
  All calendar arithmetic now goes through the helpers in
  frontend/src/lib/localDate.js — fromLocalISO, toLocalISO,
  startOfMondayWeek, addDays — which use local-noon
  constructors and never round-trip through UTC.

This test runs in two halves:

  (1) Real Node execution of the helper under TZ=America/New_York
      AND TZ=Africa/Nairobi. The spec explicitly requires both
      TZs to produce the same calendar week. Skipped cleanly if
      Node isn't on PATH.

  (2) Source-level wiring locks: TimeManagement.js must import
      the helpers and must not contain the buggy patterns
      (`new Date('YYYY-MM-DD')`, `new Date(... + 'T00:00:00')`,
      `toISOString().split`, `toISOString().slice`) outside of
      explanatory comments.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Source helpers
# ---------------------------------------------------------------------------


def _frontend_root() -> Path:
    return Path(__file__).resolve().parents[1].parent / "frontend" / "src"


def _time_management_src() -> str:
    path = _frontend_root() / "components" / "TimeManagement.js"
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def _local_date_src() -> str:
    path = _frontend_root() / "lib" / "localDate.js"
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    return path.read_text(encoding="utf-8")


def _strip_block_and_line_comments(src: str) -> str:
    """Return source with /* ... */ and // ... comments removed.
    Lets the negative-lock assertions ignore the comments we
    intentionally left behind explaining what was removed."""
    import re
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    src = re.sub(r"(^|[ \t])//.*$", "", src, flags=re.MULTILINE)
    return src


# ---------------------------------------------------------------------------
# (1) Cross-TZ Node execution
# ---------------------------------------------------------------------------


def _node_path() -> str | None:
    return shutil.which("node")


def _run_node_under_tz(tz: str) -> subprocess.CompletedProcess:
    """Run the helper test script with the given TZ env var.

    Returns the completed process; the caller asserts return code
    and inspects stdout for PASS/FAIL lines."""
    node = _node_path()
    assert node is not None, "node not available (caller should skip)"
    script = (
        _frontend_root() / "lib" / "__tests__" / "localDate.tznode.mjs"
    )
    env = dict(os.environ)
    env["TZ"] = tz
    return subprocess.run(  # noqa: S603
        [node, str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


@pytest.mark.parametrize("tz", ["America/New_York", "Africa/Nairobi"])
def test_local_date_helpers_pass_under_tz(tz):
    """The whole point of the helpers: produce the same calendar
    output regardless of the host timezone. Atlanta (UTC-4) and
    Nairobi (UTC+3) both must hit the canonical 2026-05-25
    answers."""
    if _node_path() is None:
        pytest.skip("node not installed; skipping cross-TZ helper run")
    result = _run_node_under_tz(tz)
    assert result.returncode == 0, (
        f"Node helper test failed under TZ={tz}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # SUMMARY line must report all passes.
    summary_lines = [
        line for line in result.stdout.splitlines()
        if line.startswith("SUMMARY ::")
    ]
    assert summary_lines, "Node script must emit a SUMMARY line."
    summary = summary_lines[-1]
    # Format: "SUMMARY :: 39/39 passed"
    import re
    m = re.search(r"SUMMARY :: (\d+)/(\d+) passed", summary)
    assert m, f"Unparseable SUMMARY line: {summary!r}"
    passed = int(m.group(1))
    total = int(m.group(2))
    assert passed == total > 0, (
        f"TZ={tz}: {passed}/{total} passed. Failures:\n"
        + "\n".join(
            line for line in result.stdout.splitlines()
            if line.startswith("FAIL ::")
        )
    )


def test_node_helper_produces_identical_output_in_both_timezones():
    """Defensive: not only do both runs pass on their own — they
    must produce IDENTICAL output. Catches a future helper bug
    where one TZ silently picks up a tz-dependent code path."""
    if _node_path() is None:
        pytest.skip("node not installed; skipping cross-TZ comparison")
    ny = _run_node_under_tz("America/New_York")
    nairobi = _run_node_under_tz("Africa/Nairobi")
    assert ny.returncode == 0 and nairobi.returncode == 0
    # Drop the first line which prints the TZ; everything else must match.
    ny_lines = ny.stdout.splitlines()[1:]
    nairobi_lines = nairobi.stdout.splitlines()[1:]
    assert ny_lines == nairobi_lines, (
        "Cross-TZ output diverged. Helper is not tz-independent.\n"
        f"NY:      {ny_lines[:3]}\nNairobi: {nairobi_lines[:3]}"
    )


# ---------------------------------------------------------------------------
# (2) Helper module surface
# ---------------------------------------------------------------------------


def test_local_date_module_exports_required_helpers():
    """Pin the four helper names so a future rename can't silently
    break the consumers (TimeManagement.js + any future page that
    handles calendar days)."""
    src = _local_date_src()
    for name in ("fromLocalISO", "toLocalISO", "startOfMondayWeek", "addDays"):
        assert f"export function {name}" in src, (
            f"localDate.js must export {name}()."
        )


def test_local_date_helpers_use_local_noon_constructor():
    """The whole reason these helpers work is `new Date(y, m-1,
    d, 12, 0, 0)` (local noon — DST-safe). Locking this so a
    refactor doesn't drop the noon argument and reintroduce
    midnight wobble."""
    code = _strip_block_and_line_comments(_local_date_src())
    # Loose match — the `12, 0, 0, 0` argument tail is what makes
    # the constructor local-noon-safe. Multi-line constructors
    # with a trailing comma before `)` complicate a strict regex,
    # so just count the literal noon-args tail appearances in
    # non-comment code. Three constructors each include one.
    occurrences = code.count("12, 0, 0, 0")
    assert occurrences >= 3, (
        f"localDate.js must construct Dates at local noon "
        f"(... 12, 0, 0, 0) inside fromLocalISO / startOfMondayWeek "
        f"/ addDays. Found {occurrences} '12, 0, 0, 0' tails in "
        f"non-comment code; expected >= 3."
    )


def test_local_date_helpers_do_not_use_toisostring():
    """Sanity guard — the helpers themselves must never round-trip
    through toISOString or they'd reintroduce the original bug.
    Documented commentary explaining the bug DOES mention
    toISOString; we strip comments before checking."""
    code = _strip_block_and_line_comments(_local_date_src())
    assert "toISOString" not in code, (
        "localDate.js (non-comment code) must not use toISOString — "
        "the whole point of the module is to never cross the UTC "
        "boundary."
    )


# ---------------------------------------------------------------------------
# (3) TimeManagement.js wiring — the page consumes the helpers
# ---------------------------------------------------------------------------


def test_time_management_imports_local_date_helpers():
    src = _time_management_src()
    # Tolerate prettier reordering — just check each name appears
    # in some local-date import line.
    assert "from '@/lib/localDate'" in src
    for name in ("fromLocalISO", "toLocalISO", "startOfMondayWeek", "addDays"):
        assert name in src, (
            f"TimeManagement.js must import {name} from @/lib/localDate."
        )


def test_time_management_no_longer_uses_buggy_local_date_patterns():
    """Negative locks against the exact patterns the May 25 bug
    used. Comments are exempt — we intentionally left commentary
    referencing the old shapes."""
    code = _strip_block_and_line_comments(_time_management_src())
    forbidden = [
        # `new Date(weekStart + 'T00:00:00')` and friends.
        "+ 'T00:00:00')",
        '+ "T00:00:00")',
        # `toISOString().split('T')[0]` UTC bleed.
        "toISOString().split('T')",
        'toISOString().split("T")',
        # `toISOString().slice(0, 10)` is the same bug in shorter form.
        "toISOString().slice(0, 10)",
    ]
    for pattern in forbidden:
        assert pattern not in code, (
            f"TimeManagement.js must not contain {pattern!r} for "
            "calendar-day arithmetic — use the localDate helpers "
            "instead. The pattern silently shifts the calendar by "
            "one day in positive-offset timezones."
        )


def test_time_management_week_start_function_uses_helpers():
    """The getWeekStart() helper inside the component must be
    backed by startOfMondayWeek + toLocalISO. Pinning the
    delegation so a future refactor doesn't quietly substitute
    a manual implementation that reintroduces the bug."""
    src = _time_management_src()
    assert "startOfMondayWeek" in src
    assert "toLocalISO(startOfMondayWeek(" in src


def test_time_management_week_dates_uses_addDays():
    """getWeekDates() must walk the week via addDays from the
    Monday — NOT via setDate on a Date constructed from
    'YYYY-MM-DDT00:00:00'."""
    src = _time_management_src()
    assert "addDays(start, i)" in src or "addDays(start, i + 1)" in src or "addDays(start, " in src
    assert "fromLocalISO(weekStartIso)" in src or "fromLocalISO(weekStart" in src


def test_time_management_navigate_week_uses_helpers():
    """Prev/Next week must use addDays + fromLocalISO + toLocalISO
    so jumping a week never crosses UTC."""
    src = _time_management_src()
    # The fixed shape:
    #   setWeekStart(toLocalISO(addDays(fromLocalISO(weekStart), dir * 7)));
    assert "addDays(fromLocalISO(weekStart)" in src
    assert "dir * 7" in src


def test_time_management_go_to_current_week_anchors_on_real_today():
    """Jump-to-current-week must call getWeekStart(new Date()) so
    it always lands on the actual local current week — not on a
    test-data fixture week, not on the first loaded entry."""
    src = _time_management_src()
    assert "goToCurrentWeek = () => setWeekStart(getWeekStart(new Date()))" in src


def test_time_management_day_columns_use_helpers_not_T00_parse():
    """The day-column JSX previously did
    `new Date(d + 'T00:00:00').getDate()` per cell. Replaced with
    `fromLocalISO(d).getDate()`. Same for the today-highlight
    comparison."""
    src = _time_management_src()
    assert "fromLocalISO(d).getDate()" in src
    assert "d === toLocalISO(new Date())" in src


def test_time_management_quick_add_dropdown_uses_week_dates_iso():
    """The Quick Add date <select> must use the week's ISO strings
    AS the option values. Submitting them sends the same ISO to
    POST /api/time-entries — no transformation needed."""
    src = _time_management_src()
    # The dropdown maps weekDates and uses the ISO date as the value.
    assert "weekDates.map" in src
    # The post body is `addRow` — which carries `date` straight
    # from the dropdown value via setAddRow.
    assert "api.post(`${API}/api/time-entries`, addRow)" in src


def test_time_management_test_data_only_does_not_swap_week():
    """testDataOnly is a fetch-filter; it must NOT mutate
    weekStart. Pinning by source inspection — the
    setTestDataOnly handler should never call setWeekStart in
    the same statement."""
    src = _time_management_src()
    # Find every setTestDataOnly call and verify none of them
    # are followed by setWeekStart on the same line / chain.
    import re
    matches = re.findall(r"setTestDataOnly\([^)]*\)", src)
    assert matches, "Expected setTestDataOnly to exist"
    for m in matches:
        idx = src.find(m)
        # Look at the next 200 chars — if setWeekStart appears
        # inside a chained .then() / immediate-following statement
        # it would corrupt the active week.
        window = src[idx:idx + 200]
        # An adjacent setWeekStart inside a SAME setState batch
        # would be the bug. Allow setWeekStart on the next setState
        # only if separated by a clear newline + statement-level
        # context (we accept the file as a whole — this is a soft
        # guard).
        # Strict assertion: no setWeekStart in the same expression.
        # Approximate by checking the first 80 chars after the
        # setTestDataOnly call.
        immediate = src[idx + len(m):idx + len(m) + 80]
        assert "setWeekStart" not in immediate, (
            "setTestDataOnly handler must not also setWeekStart in "
            "the same expression — Test Data Only should only "
            "filter listed entries, not move the active week."
        )
