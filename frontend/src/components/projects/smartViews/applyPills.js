/**
 * applyPills — pure filter function.
 *
 * Returns the Set of task IDs that pass ALL active pills (AND semantics).
 * If activePillIds is empty (or has zero members), returns null to signal
 * "no filter — show all."
 *
 * Design doc: .waves/projects-v1.2-smart-views-design.md §5.2
 *
 * @param {Array<Object>} tasks        — full task array from the host view
 * @param {Set<string>|Array<string>}  activePillIds — pill ids that are ON
 * @param {Object}        ctx
 * @param {string}        ctx.currentUserId
 * @param {Date}          [ctx.now]    — injected for tests; defaults to new Date()
 * @param {Object}        [ctx.project] — { methodology, methodology_config }
 * @returns {Set<string>|null}          taskId set, or null when no filter active
 */

import { PILLS } from './pillRegistry';
import { PREDICATE_MAP } from './predicates';

export function applyPills(tasks, activePillIds, ctx) {
  // Normalise to array so callers can pass Set or Array
  const active = activePillIds instanceof Set
    ? [...activePillIds]
    : (Array.isArray(activePillIds) ? activePillIds : []);

  if (active.length === 0) return null;

  const now = ctx.now instanceof Date ? ctx.now : new Date();
  const resolvedCtx = { ...ctx, now };

  // Build a list of predicate functions for the active pill ids
  const predicates = active.map((pillId) => {
    const pill = PILLS[pillId];
    if (!pill) return null;
    const fn = PREDICATE_MAP[pill.predicate];
    if (!fn) return null;
    const arg = pill.predicateArg;
    return (task) => fn(task, resolvedCtx, arg);
  }).filter(Boolean);

  if (predicates.length === 0) return null;

  const result = new Set();
  for (const task of tasks) {
    const id = task.task_id || task.id;
    if (!id) continue;
    // AND semantics — every active predicate must pass
    if (predicates.every((pred) => pred(task))) {
      result.add(id);
    }
  }
  return result;
}
