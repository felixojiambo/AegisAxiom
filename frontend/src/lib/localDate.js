/**
 * Local calendar date helpers.
 *
 * The Time Management page (and every other surface that talks
 * about "which day did the user pick on the calendar") needs to
 * round-trip YYYY-MM-DD strings through `Date` objects WITHOUT
 * crossing the UTC boundary. The native ways to do that in JS
 * are quietly broken:
 *
 *   - `new Date('2026-05-25')` parses as UTC midnight (per the
 *     ECMAScript spec for date-only ISO strings). In a positive-
 *     offset timezone (e.g. Africa/Nairobi UTC+3) that produces
 *     a Date whose LOCAL day is 2026-05-25 03:00 — still May 25
 *     — but in a negative-offset timezone (e.g. America/New_York
 *     UTC-4) it produces 2026-05-24 20:00. The displayed local
 *     day silently shifts by one.
 *
 *   - `date.toISOString().slice(0, 10)` writes the UTC calendar
 *     day, not the local one. A Date created from local-midnight
 *     in Nairobi (UTC+3) round-trips through .toISOString() back
 *     to the PREVIOUS day in UTC, so the local Mon -> Sun shift
 *     the AEGISAXIOM UI was hitting on 2026-05-25.
 *
 * The four helpers below all use `new Date(y, m-1, d, 12, 0, 0)`
 * (local noon) for any YYYY-MM-DD <-> Date trip. Noon is far
 * enough from both midnights that no DST jump can cross the day
 * boundary, so the Date's local-day equals the intended calendar
 * day regardless of where the user is.
 *
 * Use these helpers wherever a "calendar day" is the right
 * abstraction (Time Management week navigation, the Quick Add
 * date dropdown, day-column headers). Do NOT use them for
 * "instants in time" — those legitimately need toISOString().
 */

/**
 * Parse a YYYY-MM-DD string into a Date at LOCAL noon on that
 * calendar day. Throws on malformed input so silent failures
 * don't quietly land midnight UTC of "Invalid Date" somewhere.
 */
export function fromLocalISO(value) {
  if (typeof value !== 'string') {
    throw new TypeError(`fromLocalISO: expected string, got ${typeof value}`);
  }
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!m) {
    throw new RangeError(`fromLocalISO: invalid YYYY-MM-DD string: ${value}`);
  }
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  // Local noon — safe from any DST midnight wobble.
  return new Date(y, mo - 1, d, 12, 0, 0, 0);
}

/**
 * Format a Date as YYYY-MM-DD using its LOCAL calendar day.
 * Never crosses UTC. Safe to call on any Date including one
 * constructed via fromLocalISO() above.
 */
export function toLocalISO(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    throw new TypeError('toLocalISO: expected a valid Date');
  }
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

/**
 * Snap a Date back to the Monday of its local calendar week
 * (Monday-first; Sunday belongs to the PREVIOUS week, not the
 * next one). Returns a fresh Date at local noon so further
 * setDate/getDate arithmetic stays inside the same day.
 */
export function startOfMondayWeek(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    throw new TypeError('startOfMondayWeek: expected a valid Date');
  }
  const d = new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate(),
    12, 0, 0, 0,
  );
  // getDay(): 0 = Sunday, 1 = Monday, ..., 6 = Saturday.
  // We want Sunday to fall back 6 days (so it lands on the
  // PREVIOUS Monday — Sunday is the END of the week, not the
  // start of a new one).
  const day = d.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  d.setDate(d.getDate() + diff);
  return d;
}

/**
 * Add an integer number of days to a Date, returning a fresh
 * Date at local noon. Negative offsets work the same way.
 * Stays inside local calendar arithmetic — never round-trips
 * through UTC.
 */
export function addDays(date, days) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    throw new TypeError('addDays: expected a valid Date');
  }
  if (!Number.isInteger(days)) {
    throw new TypeError('addDays: expected integer days');
  }
  const d = new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate(),
    12, 0, 0, 0,
  );
  d.setDate(d.getDate() + days);
  return d;
}
