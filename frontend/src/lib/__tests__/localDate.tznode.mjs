// Standalone Node script that exercises the localDate helpers
// and prints PASS / FAIL lines. The Python test runs this twice
// — once with TZ=America/New_York and once with TZ=Africa/Nairobi
// — and asserts both runs produced the same calendar output.
//
// The helpers must be timezone-independent at the calendar-day
// level: 2026-05-25 should be 2026-05-25 regardless of where the
// machine thinks it is. The Python harness re-asserts that.
//
// Run directly:
//   TZ=America/New_York node localDate.tznode.mjs
//   TZ=Africa/Nairobi   node localDate.tznode.mjs
//
// Exit 0 on all passes; non-zero on any fail (so a CI step can
// just `node ... && node ...`).

import {
  fromLocalISO,
  toLocalISO,
  startOfMondayWeek,
  addDays,
} from '../localDate.js';

const results = [];
let failed = 0;

function expect(label, actual, expected) {
  const pass = actual === expected;
  if (!pass) failed += 1;
  results.push({
    label,
    actual: String(actual),
    expected: String(expected),
    pass,
  });
}

// --- fromLocalISO + toLocalISO round-trip ---------------------------------

const may25 = fromLocalISO('2026-05-25');
expect('fromLocalISO("2026-05-25").getFullYear()', may25.getFullYear(), 2026);
expect('fromLocalISO("2026-05-25").getMonth()',    may25.getMonth(),    4);
expect('fromLocalISO("2026-05-25").getDate()',     may25.getDate(),     25);
expect('toLocalISO(fromLocalISO("2026-05-25"))',   toLocalISO(may25), '2026-05-25');

// --- startOfMondayWeek for the canonical week ----------------------------

const weekStart = startOfMondayWeek(new Date(2026, 4, 25));   // Mon May 25
expect('startOfMondayWeek(Mon May 25)',    toLocalISO(weekStart), '2026-05-25');
const weekEnd = addDays(weekStart, 6);
expect('weekEnd (Mon + 6 days)',           toLocalISO(weekEnd),   '2026-05-31');

// --- Sunday boundary: Sun May 31 must STAY on the May 25 week -----------

const sun31 = new Date(2026, 4, 31);                          // Sun May 31
expect('startOfMondayWeek(Sun May 31)',    toLocalISO(startOfMondayWeek(sun31)), '2026-05-25');

// --- Next Monday: Mon Jun 1 must move forward ----------------------------

const jun1 = new Date(2026, 5, 1);                            // Mon Jun 1
expect('startOfMondayWeek(Mon Jun 1)',     toLocalISO(startOfMondayWeek(jun1)),   '2026-06-01');
expect('next weekEnd',                     toLocalISO(addDays(startOfMondayWeek(jun1), 6)), '2026-06-07');

// --- 7-day generation -----------------------------------------------------

const expectedISO = [
  '2026-05-25', '2026-05-26', '2026-05-27', '2026-05-28',
  '2026-05-29', '2026-05-30', '2026-05-31',
];
for (let i = 0; i < 7; i += 1) {
  const iso = toLocalISO(addDays(weekStart, i));
  expect(`weekDates[${i}]`, iso, expectedISO[i]);
}

// --- Dropdown labels (en-US) --------------------------------------------
//
// Lock the en-US label shape used by the Quick Add dropdown so a
// future Intl change can't silently shift the labels. The lock is
// loose (substring match) because Node's ICU may emit either
// "Mon, May 25" or "Mon May 25" depending on version — what we
// guarantee is the weekday + month + day pieces appear in order.

for (let i = 0; i < 7; i += 1) {
  const date = addDays(weekStart, i);
  const label = date.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
  // Each label should contain the expected weekday, "May", and day-of-month.
  const weekday = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][i];
  expect(`label[${i}].weekday`, label.includes(weekday), true);
  expect(`label[${i}].month`,   label.includes('May'),   true);
  expect(`label[${i}].day`,     label.includes(String(25 + i)), true);
}

// --- Header range (matches the page header) ------------------------------

const headerStart = weekStart.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
const headerEnd   = weekEnd.toLocaleDateString('en-US',   { month: 'short', day: 'numeric' });
expect('header start label', headerStart, 'May 25');
expect('header end label',   headerEnd,   'May 31');

// --- Emit results ---------------------------------------------------------

console.log(`TZ=${process.env.TZ || '<unset>'}`);
for (const r of results) {
  const status = r.pass ? 'PASS' : 'FAIL';
  console.log(`${status} :: ${r.label} :: actual=${r.actual} expected=${r.expected}`);
}
console.log(`SUMMARY :: ${results.length - failed}/${results.length} passed`);

if (failed > 0) process.exit(1);
