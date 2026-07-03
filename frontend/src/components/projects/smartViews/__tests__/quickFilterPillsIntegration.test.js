/**
 * Integration: QuickFilterPills + applyPills — "Blocked" pill filters BoardView tasks.
 *
 * Exercises the full smart-views data path:
 *   activePillIds state → applyPills() → visibleTaskIds Set → tasks rendered
 *
 * This test verifies the spec requirement: activate "Blocked" pill → only
 * blocked tasks visible; deactivate → all tasks visible.
 *
 * Strategy: test the pure data pipeline (applyPills + pillRegistry) with 5
 * tasks (1 blocked, 4 with other statuses) rather than rendering the full
 * BoardView component — keeps the test fast and free of routing/auth context.
 * The DOM-level result (which TaskCards render) is covered by manual QA matrix.
 *
 * Run: cd frontend && yarn test --watchAll=false quickFilterPillsIntegration
 */

import { applyPills } from '../applyPills';
import { PILLS, visiblePillIds, deriveRole } from '../pillRegistry';

// ---------------------------------------------------------------------------
// Test dataset: 5 tasks, 1 blocked
// ---------------------------------------------------------------------------
const TODAY = '2026-05-11';
const ctx = {
  currentUserId: 'user_me',
  now: new Date(`${TODAY}T12:00:00Z`),
  project: { methodology: 'waterfall', methodology_config: { columns: [] } },
};

function t(id, status = 'in_progress', extra = {}) {
  return {
    task_id: id,
    title: `Task ${id}`,
    status,
    priority: 'medium',
    assignee_id: null,
    due_date: null,
    ...extra,
  };
}

const FIVE_TASKS = [
  t('t1', 'not_started'),
  t('t2', 'in_progress'),
  t('t3', 'blocked'),         // ← the blocked task
  t('t4', 'complete'),
  t('t5', 'in_progress'),
];

// ---------------------------------------------------------------------------
// TC-QFP-01: No pills active → all tasks shown
// ---------------------------------------------------------------------------
describe('TC-QFP-01 — no active pills (show all)', () => {
  it('returns null when pills array is empty', () => {
    const result = applyPills(FIVE_TASKS, [], ctx);
    expect(result).toBeNull();
  });

  it('consumer interprets null as "show all" (no filter)', () => {
    // Confirm the contract: BoardView renders all tasks when applyPills returns null
    const visibleTaskIds = applyPills(FIVE_TASKS, [], ctx);
    const visible = visibleTaskIds === null
      ? FIVE_TASKS
      : FIVE_TASKS.filter((t) => visibleTaskIds.has(t.task_id));
    expect(visible).toHaveLength(5);
  });
});

// ---------------------------------------------------------------------------
// TC-QFP-02: Activate "Blocked" pill → only blocked task visible
// ---------------------------------------------------------------------------
describe('TC-QFP-02 — "blocked" pill active', () => {
  it('returns a Set with only the blocked task id', () => {
    const result = applyPills(FIVE_TASKS, ['blocked'], ctx);
    expect(result).toBeInstanceOf(Set);
    expect(result.size).toBe(1);
    expect(result.has('t3')).toBe(true);
  });

  it('the 4 non-blocked tasks are excluded', () => {
    const result = applyPills(FIVE_TASKS, ['blocked'], ctx);
    expect(result.has('t1')).toBe(false);
    expect(result.has('t2')).toBe(false);
    expect(result.has('t4')).toBe(false);
    expect(result.has('t5')).toBe(false);
  });

  it('consumer filters correctly: exactly 1 visible task', () => {
    const visibleTaskIds = applyPills(FIVE_TASKS, ['blocked'], ctx);
    const visible = FIVE_TASKS.filter((t) => (visibleTaskIds ?? new Set()).has(t.task_id));
    expect(visible).toHaveLength(1);
    expect(visible[0].task_id).toBe('t3');
    expect(visible[0].status).toBe('blocked');
  });
});

// ---------------------------------------------------------------------------
// TC-QFP-03: Deactivate pill → return to null (all tasks shown again)
// ---------------------------------------------------------------------------
describe('TC-QFP-03 — deactivate pill restores all tasks', () => {
  it('empty pills after deactivation returns null again', () => {
    // First activation
    const withPill = applyPills(FIVE_TASKS, ['blocked'], ctx);
    expect(withPill).toBeInstanceOf(Set);

    // Then deactivation (simulates user toggling the pill off)
    const withoutPill = applyPills(FIVE_TASKS, [], ctx);
    expect(withoutPill).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// TC-QFP-04: Multiple tasks with "blocked" status — all included
// ---------------------------------------------------------------------------
describe('TC-QFP-04 — multiple blocked tasks all included', () => {
  it('includes all blocked tasks when pill is active', () => {
    const manyBlocked = [
      t('b1', 'blocked'),
      t('b2', 'blocked'),
      t('ok', 'in_progress'),
    ];
    const result = applyPills(manyBlocked, ['blocked'], ctx);
    expect(result.size).toBe(2);
    expect(result.has('b1')).toBe(true);
    expect(result.has('b2')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// TC-QFP-05: No blocked tasks → empty Set (not null)
// ---------------------------------------------------------------------------
describe('TC-QFP-05 — blocked pill active but no blocked tasks', () => {
  it('returns empty Set (not null) when pill is active but nothing matches', () => {
    const noBlocked = [
      t('x1', 'not_started'),
      t('x2', 'in_progress'),
      t('x3', 'complete'),
    ];
    const result = applyPills(noBlocked, ['blocked'], ctx);
    expect(result).toBeInstanceOf(Set);
    expect(result.size).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// TC-QFP-06: AND semantics — "blocked" + "mine" narrows further
// ---------------------------------------------------------------------------
describe('TC-QFP-06 — blocked AND mine (AND semantics)', () => {
  it('only tasks that are both blocked AND assigned to me are included', () => {
    const mixed = [
      t('blocked_mine', 'blocked', { assignee_id: 'user_me' }),
      t('blocked_other', 'blocked', { assignee_id: 'user_other' }),
      t('unblocked_mine', 'in_progress', { assignee_id: 'user_me' }),
    ];
    const result = applyPills(mixed, ['blocked', 'mine'], ctx);
    expect(result.size).toBe(1);
    expect(result.has('blocked_mine')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// TC-QFP-07: pillRegistry integration — "blocked" pill defined with correct shape
// ---------------------------------------------------------------------------
describe('TC-QFP-07 — pillRegistry: "blocked" pill shape contract', () => {
  it('blocked pill is registered with correct properties', () => {
    const pill = PILLS['blocked'];
    expect(pill).toBeDefined();
    expect(pill.id).toBe('blocked');
    expect(pill.label).toBeTruthy();
    expect(pill.predicate).toBe('isBlocked');
    expect(pill.surfaces).toContain('board');
    expect(pill.surfaces).toContain('backlog');
  });

  it('admin role can see blocked pill on board surface', () => {
    const ids = visiblePillIds('admin', 'board');
    expect(ids).toContain('blocked');
  });

  it('pm role can see blocked pill on board surface', () => {
    const ids = visiblePillIds('pm', 'board');
    expect(ids).toContain('blocked');
  });

  it('viewer role CANNOT see blocked pill (admin/pm/assignee only)', () => {
    const ids = visiblePillIds('viewer', 'board');
    expect(ids).not.toContain('blocked');
  });
});

// ---------------------------------------------------------------------------
// TC-QFP-08: deriveRole maps permissions to correct role key
// ---------------------------------------------------------------------------
describe('TC-QFP-08 — deriveRole permission mapping', () => {
  it('admin.manage → admin', () => {
    expect(deriveRole((p) => p === 'admin.manage')).toBe('admin');
  });

  it('projects.project.create → pm', () => {
    expect(deriveRole((p) => p === 'projects.project.create')).toBe('pm');
  });

  it('projects.task.edit (no create) → assignee', () => {
    expect(deriveRole((p) => p === 'projects.task.edit')).toBe('assignee');
  });

  it('no matching perms → viewer', () => {
    expect(deriveRole(() => false)).toBe('viewer');
  });

  it('viewer cannot see blocked pill on board', () => {
    const role = deriveRole(() => false);
    const ids = visiblePillIds(role, 'board');
    expect(ids).not.toContain('blocked');
  });
});
