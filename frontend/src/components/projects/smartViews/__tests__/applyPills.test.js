/**
 * Unit tests for applyPills.
 *
 * Pure function — no DOM, no mocks.
 * Run: cd frontend && yarn test --watchAll=false applyPills.test
 */

import { applyPills } from '../applyPills';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const TODAY = '2026-05-11';
const IN_2_DAYS = '2026-05-13';
const YESTERDAY = '2026-05-10';

function makeCtx(overrides = {}) {
  return {
    currentUserId: 'user_me',
    now: new Date(`${TODAY}T12:00:00Z`),
    project: {
      methodology: 'waterfall',
      methodology_config: { columns: [] },
    },
    ...overrides,
  };
}

function t(id, overrides = {}) {
  return {
    task_id: id,
    title: `Task ${id}`,
    assignee_id: null,
    status: 'in_progress',
    priority: 'medium',
    due_date: null,
    ...overrides,
  };
}

const ctx = makeCtx();

// ---------------------------------------------------------------------------
// No active pills → null (show all)
// ---------------------------------------------------------------------------
describe('applyPills — no active pills', () => {
  it('returns null for empty array', () => {
    const tasks = [t('a'), t('b')];
    expect(applyPills(tasks, [], ctx)).toBeNull();
  });

  it('returns null for empty Set', () => {
    expect(applyPills([t('a')], new Set(), ctx)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// TC-01: My tasks pill
// ---------------------------------------------------------------------------
describe('TC-01 — mine pill', () => {
  it('returns only tasks assigned to current user', () => {
    const tasks = [
      t('mine1', { assignee_id: 'user_me' }),
      t('mine2', { assignee_id: 'user_me' }),
      t('other', { assignee_id: 'user_other' }),
      t('unassigned', { assignee_id: null }),
    ];
    const result = applyPills(tasks, ['mine'], ctx);
    expect(result).toBeInstanceOf(Set);
    expect([...result].sort()).toEqual(['mine1', 'mine2'].sort());
  });
});

// ---------------------------------------------------------------------------
// TC-02: AND semantics — mine + overdue
// ---------------------------------------------------------------------------
describe('TC-02 — mine + overdue (AND)', () => {
  it('returns intersection of both predicates', () => {
    const tasks = [
      t('match', { assignee_id: 'user_me', due_date: YESTERDAY, status: 'in_progress' }),
      t('mine_not_overdue', { assignee_id: 'user_me', due_date: TODAY }),
      t('overdue_not_mine', { assignee_id: 'other', due_date: YESTERDAY, status: 'in_progress' }),
      t('neither', {}),
    ];
    const result = applyPills(tasks, ['mine', 'overdue'], ctx);
    expect([...result]).toEqual(['match']);
  });
});

// ---------------------------------------------------------------------------
// blocked pill
// ---------------------------------------------------------------------------
describe('blocked pill', () => {
  it('waterfall: returns tasks with status blocked', () => {
    const tasks = [
      t('blk', { status: 'blocked' }),
      t('ok', { status: 'in_progress' }),
    ];
    const result = applyPills(tasks, ['blocked'], ctx);
    expect([...result]).toEqual(['blk']);
  });
});

// ---------------------------------------------------------------------------
// at_risk pill
// ---------------------------------------------------------------------------
describe('at_risk pill', () => {
  it('returns tasks due within 3 days and not done', () => {
    const tasks = [
      t('risk1', { due_date: TODAY }),
      t('risk2', { due_date: IN_2_DAYS }),
      t('safe', { due_date: '2026-05-18' }),
      t('done', { due_date: TODAY, status: 'complete' }),
    ];
    const result = applyPills(tasks, ['at_risk'], ctx);
    expect([...result].sort()).toEqual(['risk1', 'risk2'].sort());
  });
});

// ---------------------------------------------------------------------------
// unassigned pill
// ---------------------------------------------------------------------------
describe('unassigned pill', () => {
  it('returns tasks with no assignee_id', () => {
    const tasks = [
      t('ua1', { assignee_id: null }),
      t('ua2', { assignee_id: undefined }),
      t('assigned', { assignee_id: 'user_x' }),
    ];
    const result = applyPills(tasks, ['unassigned'], ctx);
    expect([...result].sort()).toEqual(['ua1', 'ua2'].sort());
  });
});

// ---------------------------------------------------------------------------
// high_priority pill
// ---------------------------------------------------------------------------
describe('high_priority pill', () => {
  it('matches high and critical tasks', () => {
    const tasks = [
      t('hp1', { priority: 'high' }),
      t('hp2', { priority: 'critical' }),
      t('med', { priority: 'medium' }),
      t('low', { priority: 'low' }),
    ];
    const result = applyPills(tasks, ['high_priority'], ctx);
    expect([...result].sort()).toEqual(['hp1', 'hp2'].sort());
  });
});

// ---------------------------------------------------------------------------
// Empty result — all tasks filtered out
// ---------------------------------------------------------------------------
describe('empty result', () => {
  it('returns empty Set (not null) when pills are active but nothing matches', () => {
    const tasks = [
      t('a', { assignee_id: 'other' }),
      t('b', { assignee_id: 'other2' }),
    ];
    const result = applyPills(tasks, ['mine'], ctx);
    expect(result).toBeInstanceOf(Set);
    expect(result.size).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Unknown pill id — ignored gracefully
// ---------------------------------------------------------------------------
describe('unknown pill id', () => {
  it('ignores unknown pill ids', () => {
    const tasks = [t('a', { assignee_id: 'user_me' })];
    // Only 'mine' resolves; 'bogus_pill' is ignored
    const result = applyPills(tasks, ['mine', 'bogus_pill'], ctx);
    expect([...result]).toEqual(['a']);
  });
});

// ---------------------------------------------------------------------------
// Accepts both Set and Array for activePillIds
// ---------------------------------------------------------------------------
describe('input type flexibility', () => {
  it('accepts a Set', () => {
    const tasks = [t('a', { assignee_id: 'user_me' })];
    expect(applyPills(tasks, new Set(['mine']), ctx)).toBeInstanceOf(Set);
  });

  it('accepts an Array', () => {
    const tasks = [t('a', { assignee_id: 'user_me' })];
    expect(applyPills(tasks, ['mine'], ctx)).toBeInstanceOf(Set);
  });
});

// ---------------------------------------------------------------------------
// Tasks without task_id (fallback to .id)
// ---------------------------------------------------------------------------
describe('task id fallback', () => {
  it('uses .id when task_id is absent', () => {
    const tasks = [{ id: 'legacy_1', assignee_id: 'user_me', status: 'in_progress', priority: 'low' }];
    const result = applyPills(tasks, ['mine'], ctx);
    expect([...result]).toEqual(['legacy_1']);
  });
});
