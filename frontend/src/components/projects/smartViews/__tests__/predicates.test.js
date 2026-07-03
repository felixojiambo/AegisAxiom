/**
 * Unit tests for smart-view predicates.
 *
 * Pure functions — no DOM, no mocks needed.
 * Run: cd frontend && yarn test --watchAll=false predicates.test
 */

import {
  dueWithinDays,
  isAtRisk,
  isBlocked,
  isDone as _isDone,
  isHighPriority,
  isMine,
  isOverdue,
  isUnassigned,
  DONE_STATUSES,
} from '../predicates';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const TODAY = '2026-05-11';
const TOMORROW = '2026-05-12';
const IN_2_DAYS = '2026-05-13';
const IN_3_DAYS = '2026-05-14'; // NOT within <3 (boundary: diffDays must be < 3)
const IN_7_DAYS = '2026-05-18';
const IN_8_DAYS = '2026-05-19';
const YESTERDAY = '2026-05-10';

function makeCtx(overrides = {}) {
  return {
    currentUserId: 'user_123',
    now: new Date(`${TODAY}T12:00:00Z`),
    project: {
      methodology: 'waterfall',
      methodology_config: { columns: [] },
    },
    ...overrides,
  };
}

function task(overrides = {}) {
  return {
    task_id: 'task_001',
    title: 'Test task',
    assignee_id: null,
    status: 'in_progress',
    priority: 'medium',
    due_date: null,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// isMine
// ---------------------------------------------------------------------------
describe('isMine', () => {
  const ctx = makeCtx();

  it('returns true when assignee_id matches currentUserId', () => {
    expect(isMine(task({ assignee_id: 'user_123' }), ctx)).toBe(true);
  });

  it('returns false when assignee_id differs', () => {
    expect(isMine(task({ assignee_id: 'user_999' }), ctx)).toBe(false);
  });

  it('returns false when assignee_id is null', () => {
    expect(isMine(task({ assignee_id: null }), ctx)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// dueWithinDays
// ---------------------------------------------------------------------------
describe('dueWithinDays', () => {
  const ctx = makeCtx();

  it('returns true for task due today (0 days out)', () => {
    expect(dueWithinDays(task({ due_date: TODAY }), ctx, 7)).toBe(true);
  });

  it('returns true for task due in 7 days', () => {
    expect(dueWithinDays(task({ due_date: IN_7_DAYS }), ctx, 7)).toBe(true);
  });

  it('returns false for task due in 8 days', () => {
    expect(dueWithinDays(task({ due_date: IN_8_DAYS }), ctx, 7)).toBe(false);
  });

  it('returns false when no due_date', () => {
    expect(dueWithinDays(task({ due_date: null }), ctx, 7)).toBe(false);
  });

  it('returns false when task is done', () => {
    expect(dueWithinDays(task({ due_date: TODAY, status: 'complete' }), ctx, 7)).toBe(false);
  });

  it('returns false when task is overdue (past today)', () => {
    expect(dueWithinDays(task({ due_date: YESTERDAY }), ctx, 7)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// isOverdue
// ---------------------------------------------------------------------------
describe('isOverdue', () => {
  const ctx = makeCtx();

  it('returns true for past due date and non-done status', () => {
    expect(isOverdue(task({ due_date: YESTERDAY }), ctx)).toBe(true);
  });

  it('returns false when due today (not yet overdue)', () => {
    expect(isOverdue(task({ due_date: TODAY }), ctx)).toBe(false);
  });

  it('returns false when due in the future', () => {
    expect(isOverdue(task({ due_date: IN_7_DAYS }), ctx)).toBe(false);
  });

  it('returns false when no due_date', () => {
    expect(isOverdue(task({ due_date: null }), ctx)).toBe(false);
  });

  it('returns false when status is done even if past due', () => {
    expect(isOverdue(task({ due_date: YESTERDAY, status: 'complete' }), ctx)).toBe(false);
    expect(isOverdue(task({ due_date: YESTERDAY, status: 'shipped' }), ctx)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// isBlocked
// ---------------------------------------------------------------------------
describe('isBlocked — waterfall/W&T', () => {
  const ctx = makeCtx({ project: { methodology: 'waterfall', methodology_config: { columns: [] } } });

  it('returns true when status === "blocked"', () => {
    expect(isBlocked(task({ status: 'blocked' }), ctx)).toBe(true);
  });

  it('is case-insensitive', () => {
    expect(isBlocked(task({ status: 'Blocked' }), ctx)).toBe(true);
  });

  it('returns false when status is something else', () => {
    expect(isBlocked(task({ status: 'in_progress' }), ctx)).toBe(false);
  });
});

describe('isBlocked — agile with named column', () => {
  const agileCtx = makeCtx({
    project: {
      methodology: 'agile',
      methodology_config: {
        columns: [
          { id: 'col_1', name: 'To Do' },
          { id: 'col_2', name: 'Blocked' },
          { id: 'col_3', name: 'Done' },
        ],
      },
    },
  });

  it('matches by column id', () => {
    expect(isBlocked(task({ status: 'col_2' }), agileCtx)).toBe(true);
  });

  it('matches by column name (case-insensitive)', () => {
    expect(isBlocked(task({ status: 'blocked' }), agileCtx)).toBe(true);
  });

  it('returns false for task in non-blocked column', () => {
    expect(isBlocked(task({ status: 'col_1' }), agileCtx)).toBe(false);
  });
});

describe('isBlocked — agile with no blocked column', () => {
  const agileCtx = makeCtx({
    project: {
      methodology: 'agile',
      methodology_config: {
        columns: [
          { id: 'col_1', name: 'Backlog' },
          { id: 'col_2', name: 'In Progress' },
        ],
      },
    },
  });

  it('returns false (TC-03)', () => {
    expect(isBlocked(task({ status: 'col_1' }), agileCtx)).toBe(false);
  });
});

describe('isBlocked — agile with is_blocked flag', () => {
  const agileCtx = makeCtx({
    project: {
      methodology: 'agile',
      methodology_config: {
        columns: [
          { id: 'col_flag', name: 'Impediment', is_blocked: true },
        ],
      },
    },
  });

  it('matches by is_blocked: true flag', () => {
    expect(isBlocked(task({ status: 'col_flag' }), agileCtx)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// isAtRisk (TC-05, TC-06)
// ---------------------------------------------------------------------------
describe('isAtRisk', () => {
  const ctx = makeCtx();

  it('TC-05: due in 2 days, not done → at risk', () => {
    expect(isAtRisk(task({ due_date: IN_2_DAYS }), ctx)).toBe(true);
  });

  it('TC-06: due in 5 days, not done → not at risk', () => {
    expect(isAtRisk(task({ due_date: IN_7_DAYS }), ctx)).toBe(false);
  });

  it('due today → at risk', () => {
    expect(isAtRisk(task({ due_date: TODAY }), ctx)).toBe(true);
  });

  it('due tomorrow → at risk', () => {
    expect(isAtRisk(task({ due_date: TOMORROW }), ctx)).toBe(true);
  });

  it('due in 3 days → NOT at risk (boundary: diffDays < 3 required)', () => {
    // IN_3_DAYS = 2026-05-14, today = 2026-05-11 → diffDays = 3 → NOT < 3
    expect(isAtRisk(task({ due_date: IN_3_DAYS }), ctx)).toBe(false);
  });

  it('overdue → not at risk (past due, not future-near)', () => {
    expect(isAtRisk(task({ due_date: YESTERDAY }), ctx)).toBe(false);
  });

  it('done task with imminent due date → not at risk', () => {
    expect(isAtRisk(task({ due_date: TODAY, status: 'complete' }), ctx)).toBe(false);
  });

  it('no due date → not at risk', () => {
    expect(isAtRisk(task({ due_date: null }), ctx)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// isUnassigned
// ---------------------------------------------------------------------------
describe('isUnassigned', () => {
  it('returns true when assignee_id is null', () => {
    expect(isUnassigned(task({ assignee_id: null }))).toBe(true);
  });

  it('returns true when assignee_id is undefined', () => {
    expect(isUnassigned(task({ assignee_id: undefined }))).toBe(true);
  });

  it('returns false when assignee_id is set', () => {
    expect(isUnassigned(task({ assignee_id: 'user_456' }))).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// isHighPriority
// ---------------------------------------------------------------------------
describe('isHighPriority', () => {
  it('returns true for "high"', () => {
    expect(isHighPriority(task({ priority: 'high' }))).toBe(true);
  });

  it('returns true for "critical"', () => {
    expect(isHighPriority(task({ priority: 'critical' }))).toBe(true);
  });

  it('is case-insensitive', () => {
    expect(isHighPriority(task({ priority: 'High' }))).toBe(true);
  });

  it('returns false for "medium"', () => {
    expect(isHighPriority(task({ priority: 'medium' }))).toBe(false);
  });

  it('returns false for null priority', () => {
    expect(isHighPriority(task({ priority: null }))).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// DONE_STATUSES coverage
// ---------------------------------------------------------------------------
describe('DONE_STATUSES', () => {
  it('contains expected terminal statuses', () => {
    expect(DONE_STATUSES.has('complete')).toBe(true);
    expect(DONE_STATUSES.has('shipped')).toBe(true);
    expect(DONE_STATUSES.has('done')).toBe(true);
    expect(DONE_STATUSES.has('closed')).toBe(true);
    expect(DONE_STATUSES.has('cancelled')).toBe(true);
  });

  it('does not contain active statuses', () => {
    expect(DONE_STATUSES.has('in_progress')).toBe(false);
    expect(DONE_STATUSES.has('blocked')).toBe(false);
    expect(DONE_STATUSES.has('not_started')).toBe(false);
  });
});
