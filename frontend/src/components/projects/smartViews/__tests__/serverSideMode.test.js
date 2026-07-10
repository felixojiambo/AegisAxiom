/**
 * Unit tests for serverSideMode.
 *
 * Pure functions — no DOM, no mocks needed.
 * Run: cd frontend && yarn test --watchAll=false serverSideMode.test
 */

import {
  shouldUseServerSideFiltering,
  buildServerQueryFromPills,
  SERVER_SIDE_THRESHOLD,
} from '../serverSideMode';

const CTX = { currentUserId: 'user_me' };

// ---------------------------------------------------------------------------
// shouldUseServerSideFiltering
// ---------------------------------------------------------------------------
describe('shouldUseServerSideFiltering', () => {
  it('returns false when task count is at or below threshold', () => {
    expect(shouldUseServerSideFiltering(SERVER_SIDE_THRESHOLD, 1)).toBe(false);
    expect(shouldUseServerSideFiltering(500, 3)).toBe(false);
  });

  it('returns false when no pills are active (even above threshold)', () => {
    expect(shouldUseServerSideFiltering(600, 0)).toBe(false);
  });

  it('returns true when task count exceeds threshold AND pills are active', () => {
    expect(shouldUseServerSideFiltering(501, 1)).toBe(true);
    expect(shouldUseServerSideFiltering(1000, 3)).toBe(true);
  });

  it('uses SERVER_SIDE_THRESHOLD constant (500)', () => {
    expect(SERVER_SIDE_THRESHOLD).toBe(500);
  });
});

// ---------------------------------------------------------------------------
// buildServerQueryFromPills
// ---------------------------------------------------------------------------
describe('buildServerQueryFromPills', () => {
  it('maps mine → assignee_id=__me', () => {
    expect(buildServerQueryFromPills(['mine'], CTX)).toEqual({ assignee_id: '__me' });
  });

  it('maps unassigned → assignee_id=__unassigned', () => {
    expect(buildServerQueryFromPills(['unassigned'], CTX)).toEqual({ assignee_id: '__unassigned' });
  });

  it('mine takes precedence over unassigned when both active (last write wins)', () => {
    // mine appears last so overwrites __unassigned
    const result = buildServerQueryFromPills(['unassigned', 'mine'], CTX);
    expect(result.assignee_id).toBe('__me');
  });

  it('maps overdue → is_overdue=true', () => {
    expect(buildServerQueryFromPills(['overdue'], CTX)).toEqual({ is_overdue: 'true' });
  });

  it('maps at_risk → is_at_risk=true', () => {
    expect(buildServerQueryFromPills(['at_risk'], CTX)).toEqual({ is_at_risk: 'true' });
  });

  it('maps blocked → is_blocked=true', () => {
    expect(buildServerQueryFromPills(['blocked'], CTX)).toEqual({ is_blocked: 'true' });
  });

  it('maps due_this_week → due_within_days=7', () => {
    expect(buildServerQueryFromPills(['due_this_week'], CTX)).toEqual({ due_within_days: '7' });
  });

  it('maps high_priority → priority_in=high,critical', () => {
    expect(buildServerQueryFromPills(['high_priority'], CTX)).toEqual({ priority_in: 'high,critical' });
  });

  it('combines multiple pills into a single params object', () => {
    const result = buildServerQueryFromPills(['mine', 'overdue', 'high_priority'], CTX);
    expect(result).toEqual({
      assignee_id: '__me',
      is_overdue: 'true',
      priority_in: 'high,critical',
    });
  });

  it('ignores unknown pill ids gracefully', () => {
    const result = buildServerQueryFromPills(['bogus_pill', 'mine'], CTX);
    expect(result).toEqual({ assignee_id: '__me' });
  });

  it('returns empty object for empty array', () => {
    expect(buildServerQueryFromPills([], CTX)).toEqual({});
  });

  it('ignores mine_pm (no server-side equivalent)', () => {
    expect(buildServerQueryFromPills(['mine_pm'], CTX)).toEqual({});
  });
});
