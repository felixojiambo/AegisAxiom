/**
 * Pure predicate functions for smart-view pills.
 *
 * Each takes (task, ctx) and returns bool.
 * No React, no fetch, no side effects — fully testable in isolation.
 *
 * ctx shape:
 *   {
 *     currentUserId: string,
 *     now: Date,                          // injected for tests; defaults to new Date()
 *     project: {
 *       methodology: 'agile'|'waterfall'|'waves_threads',
 *       methodology_config: { columns: Array<{id, name, is_blocked}> }
 *     }
 *   }
 *
 * Design doc: .waves/projects-v1.2-smart-views-design.md §1, §5.2
 */

// Terminal / done statuses — mirrors backend DONE_STATUSES
// (services/projects_methodology.py). Kept in sync manually.
export const DONE_STATUSES = new Set([
  'complete',
  'completed',
  'done',
  'shipped',
  'closed',
  'cancelled',
  'canceled',
]);

/** Parse an ISO date string ("YYYY-MM-DD") to midnight UTC. */
function parseDate(iso) {
  if (!iso) return null;
  const d = new Date(`${iso}T00:00:00Z`);
  return isNaN(d.getTime()) ? null : d;
}

/** Today at midnight UTC (for consistent day-boundary comparisons). */
function todayMidnightUTC(now) {
  const n = now instanceof Date ? now : new Date();
  return new Date(Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate()));
}

/** Whether task status is in the done set. */
function isDone(task) {
  return DONE_STATUSES.has((task.status || '').toLowerCase());
}

// ---------------------------------------------------------------------------
// Individual predicates
// ---------------------------------------------------------------------------

/**
 * My tasks — assignee_id matches current user.
 */
export function isMine(task, ctx) {
  return task.assignee_id === ctx.currentUserId;
}

/**
 * Due within N calendar days from today (inclusive) and not done.
 * N=7 covers "due this week."
 */
export function dueWithinDays(task, ctx, days = 7) {
  if (isDone(task)) return false;
  const due = parseDate(task.due_date);
  if (!due) return false;
  const today = todayMidnightUTC(ctx.now);
  const ceiling = new Date(today.getTime() + days * 86400_000);
  return due >= today && due <= ceiling;
}

/**
 * Overdue — due_date is in the past and not done.
 */
export function isOverdue(task, ctx) {
  if (isDone(task)) return false;
  const due = parseDate(task.due_date);
  if (!due) return false;
  const today = todayMidnightUTC(ctx.now);
  return due < today;
}

/**
 * Blocked — methodology-aware.
 *   Waterfall / W&T: status === 'blocked' (case-insensitive)
 *   Agile: task in a column whose name (lowercased) === 'blocked'
 *          OR column has is_blocked: true flag
 *
 * Falls back to status check when methodology or columns are unknown.
 */
export function isBlocked(task, ctx) {
  const methodology = ctx.project?.methodology;

  if (methodology === 'agile') {
    const columns = ctx.project?.methodology_config?.columns;
    if (Array.isArray(columns) && columns.length > 0) {
      const blockedCol = columns.find(
        (c) => c.is_blocked === true || (c.name || '').toLowerCase() === 'blocked',
      );
      if (blockedCol) {
        // In agile the task's status field stores the column id or column name
        return (
          task.status === blockedCol.id ||
          (task.status || '').toLowerCase() === (blockedCol.name || '').toLowerCase()
        );
      }
      // No blocked column defined — 0 matches
      return false;
    }
  }

  // Waterfall, W&T, or unknown methodology — match by status value
  return (task.status || '').toLowerCase() === 'blocked';
}

/**
 * At risk — due within 3 days AND not done.
 * Deliberately no completion_percent (field does not exist on pm_tasks).
 * Tooltip: "Due within 3 days and not yet done."
 */
export function isAtRisk(task, ctx) {
  if (isDone(task)) return false;
  const due = parseDate(task.due_date);
  if (!due) return false;
  const today = todayMidnightUTC(ctx.now);
  const diffDays = (due.getTime() - today.getTime()) / 86400_000;
  return diffDays >= 0 && diffDays < 3;
}

/**
 * Unassigned — no assignee_id set.
 */
export function isUnassigned(task) {
  return !task.assignee_id;
}

/**
 * High priority — priority is 'high' or 'critical' (case-insensitive).
 */
export function isHighPriority(task) {
  const p = (task.priority || '').toLowerCase();
  return p === 'high' || p === 'critical';
}

/**
 * PM project — project.pm_id matches current user.
 * Used by mine_pm pill in MyDay (project-level predicate, not task).
 */
export function isPmProject(project, ctx) {
  return project.pm_id === ctx.currentUserId;
}

// ---------------------------------------------------------------------------
// Predicate dispatch map — keyed by pill predicate name from pillRegistry
// ---------------------------------------------------------------------------
export const PREDICATE_MAP = {
  isMine:          (task, ctx) => isMine(task, ctx),
  dueWithinDays:   (task, ctx, arg) => dueWithinDays(task, ctx, arg),
  isOverdue:       (task, ctx) => isOverdue(task, ctx),
  isBlocked:       (task, ctx) => isBlocked(task, ctx),
  isAtRisk:        (task, ctx) => isAtRisk(task, ctx),
  isUnassigned:    (task) => isUnassigned(task),
  isHighPriority:  (task) => isHighPriority(task),
  isPmProject:     (project, ctx) => isPmProject(project, ctx),
};
