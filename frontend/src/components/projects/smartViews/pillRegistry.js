/**
 * Pill registry — single source of truth for quick-filter pills.
 *
 * Pure data — no React, no fetch. Safe to import from tests.
 *
 * Design doc: .waves/projects-v1.2-smart-views-design.md §5.1
 */

export const PILLS = {
  mine: {
    id: 'mine',
    label: 'My tasks',
    predicate: 'isMine',
    roles: ['admin', 'pm', 'assignee', 'viewer'],
    surfaces: ['board', 'backlog', 'myday'],
    tooltip: 'Tasks assigned to you',
  },
  due_this_week: {
    id: 'due_this_week',
    label: 'Due this week',
    predicate: 'dueWithinDays',
    predicateArg: 7,
    roles: ['admin', 'pm', 'assignee', 'viewer'],
    surfaces: ['board', 'backlog', 'myday'],
    tooltip: 'Due within the next 7 days and not done',
  },
  overdue: {
    id: 'overdue',
    label: 'Overdue',
    predicate: 'isOverdue',
    roles: ['admin', 'pm', 'assignee', 'viewer'],
    surfaces: ['board', 'backlog', 'myday'],
    tooltip: 'Past due and not done',
  },
  blocked: {
    id: 'blocked',
    label: 'Blocked',
    predicate: 'isBlocked',
    roles: ['admin', 'pm', 'assignee'],
    surfaces: ['board', 'backlog', 'myday'],
    tooltip: 'Status is blocked (waterfall/W&T) or in a blocked column (agile)',
  },
  at_risk: {
    id: 'at_risk',
    label: 'At risk',
    predicate: 'isAtRisk',
    roles: ['admin', 'pm'],
    surfaces: ['board', 'backlog', 'myday'],
    tooltip: 'Due within 3 days and not yet done',
  },
  unassigned: {
    id: 'unassigned',
    label: 'Unassigned',
    predicate: 'isUnassigned',
    roles: ['admin', 'pm'],
    surfaces: ['board', 'backlog'],
    tooltip: 'No assignee set',
  },
  high_priority: {
    id: 'high_priority',
    label: 'High priority',
    predicate: 'isHighPriority',
    roles: ['admin', 'pm', 'assignee'],
    surfaces: ['board', 'backlog'],
    tooltip: 'Priority is high or critical',
  },
  mine_pm: {
    id: 'mine_pm',
    label: 'My PM projects',
    predicate: 'isPmProject',
    roles: ['admin', 'pm'],
    surfaces: ['myday'],
    tooltip: 'Projects where you are the PM',
  },
};

export const PILL_IDS = Object.keys(PILLS);

/**
 * Default active pills per role per surface.
 * Applied on first visit (no localStorage state yet).
 */
export const DEFAULT_ACTIVE = {
  admin:    { board: [],                         backlog: [],              myday: [] },
  pm:       { board: ['due_this_week', 'blocked'], backlog: ['due_this_week'], myday: [] },
  assignee: { board: ['mine', 'due_this_week'],  backlog: ['mine'],        myday: [] },
  viewer:   { board: ['mine'],                   backlog: ['mine'],        myday: [] },
};

/**
 * Derive the user's role key from their permission set.
 * Matches the design doc §4 detection logic.
 *
 * @param {(perm: string) => boolean} hasPermission
 * @returns {'admin' | 'pm' | 'assignee' | 'viewer'}
 */
export function deriveRole(hasPermission) {
  if (hasPermission('admin.manage')) return 'admin';
  if (hasPermission('projects.project.create')) return 'pm';
  if (hasPermission('projects.task.edit')) return 'assignee';
  return 'viewer';
}

/**
 * Pills visible for a given role on a given surface.
 *
 * @param {string} role — 'admin' | 'pm' | 'assignee' | 'viewer'
 * @param {string} surface — 'board' | 'backlog' | 'myday'
 * @returns {string[]} pill ids
 */
export function visiblePillIds(role, surface) {
  return Object.values(PILLS)
    .filter((p) => p.roles.includes(role) && p.surfaces.includes(surface))
    .map((p) => p.id);
}
