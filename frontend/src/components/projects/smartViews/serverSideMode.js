/**
 * serverSideMode — detect when smart-view filters should be served from the API
 * instead of evaluated client-side.
 *
 * Threshold: projects with >500 tasks skip the client-side applyPills pass and
 * call GET /api/projects/tasks with the translated query params directly.
 *
 * Wave 1C API contract:
 *   assignee_id   — user id, '__me', or '__unassigned'
 *   status_in     — comma-separated status values
 *   priority_in   — comma-separated priority values
 *   is_blocked    — 'true'
 *   due_within_days — integer
 *   is_overdue    — 'true'
 *   is_at_risk    — 'true'
 */

export const SERVER_SIDE_THRESHOLD = 500;

/**
 * Returns true when the number of tasks exceeds the threshold AND at least one
 * pill filter is active.
 *
 * @param {number} taskCount        — total tasks loaded in the project
 * @param {number} activePillsCount — number of active pill ids
 * @returns {boolean}
 */
export function shouldUseServerSideFiltering(taskCount, activePillsCount) {
  return taskCount > SERVER_SIDE_THRESHOLD && activePillsCount > 0;
}

/**
 * Maps active pill ids to a URLSearchParams-compatible object using the
 * Wave 1C query param contract.
 *
 * @param {string[]} activePills  — array of pill ids from pillRegistry
 * @param {{ currentUserId: string }} ctx
 * @returns {Record<string, string>}  — key/value pairs ready for URLSearchParams
 */
export function buildServerQueryFromPills(activePills, ctx) {
  const params = {};

  for (const pillId of activePills) {
    switch (pillId) {
      case 'mine':
        // If we already have __unassigned set, mine wins (last write wins; real-world
        // usage shouldn't combine these).
        params.assignee_id = '__me';
        break;

      case 'unassigned':
        if (!params.assignee_id) {
          params.assignee_id = '__unassigned';
        }
        break;

      case 'overdue':
        params.is_overdue = 'true';
        break;

      case 'at_risk':
        params.is_at_risk = 'true';
        break;

      case 'blocked':
        params.is_blocked = 'true';
        break;

      case 'due_this_week':
        // "Due this week" maps to within 7 days; only set if not already more
        // restrictive via at_risk (3 days). Let both coexist — backend ANDs them.
        params.due_within_days = '7';
        break;

      case 'high_priority':
        params.priority_in = 'high,critical';
        break;

      // mine_pm is a myday-only pill with no direct task-level query param; skip.
      default:
        break;
    }
  }

  return params;
}
