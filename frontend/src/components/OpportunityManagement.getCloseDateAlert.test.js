/**
 * Locks the close-date alert source-of-truth contract.
 *
 * The helper must only read the actual close-date field on the
 * payload — close_date / expected_close_date / expected_close /
 * closeDate. created_at, updated_at, stage timestamps, and
 * tracking_number must never influence the result. A missing or
 * invalid close-date value resolves to 'normal' so the UI never shows
 * "Close date overdue" without an actual overdue close date.
 *
 * Synthetic only. ``today`` is injected into every call so the suite
 * stays deterministic across calendar drift.
 */

import { getCloseDateAlert } from './OpportunityManagement';

const TODAY = new Date(2099, 5, 15); // Synthetic anchor: 2099-06-15

function isoOffset(days) {
  const d = new Date(TODAY);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10); // YYYY-MM-DD
}

describe('getCloseDateAlert — source of truth', () => {
  test('missing close_date returns normal (no overdue badge)', () => {
    const opp = { stage: 'proposal_development' };
    expect(getCloseDateAlert(opp, TODAY)).toBe('normal');
  });

  test('invalid close_date returns normal', () => {
    const opp = { stage: 'proposal_development', close_date: 'not-a-date' };
    expect(getCloseDateAlert(opp, TODAY)).toBe('normal');
  });

  test('null close_date returns normal', () => {
    const opp = { stage: 'proposal_development', close_date: null };
    expect(getCloseDateAlert(opp, TODAY)).toBe('normal');
  });

  test('empty-string close_date returns normal', () => {
    const opp = { stage: 'proposal_development', close_date: '' };
    expect(getCloseDateAlert(opp, TODAY)).toBe('normal');
  });

  test('old created_at but no close_date returns normal', () => {
    // Regression lock for the prior fallback bug — created_at must
    // never influence the alert. An opportunity created a year ago
    // with no close_date should NOT render as overdue.
    const opp = {
      stage: 'proposal_development',
      created_at: '2098-01-01T00:00:00Z',
    };
    expect(getCloseDateAlert(opp, TODAY)).toBe('normal');
  });

  test('old updated_at but no close_date returns normal', () => {
    const opp = {
      stage: 'proposal_development',
      updated_at: '2098-01-01T00:00:00Z',
    };
    expect(getCloseDateAlert(opp, TODAY)).toBe('normal');
  });

  test('desired_proposal_date alone never triggers the alert', () => {
    // Locks the specific regression in question — the previous
    // implementation fell back to desired_proposal_date when close_date
    // was missing, causing every opportunity with a past desired
    // proposal date to render as overdue.
    const opp = {
      stage: 'proposal_development',
      desired_proposal_date: isoOffset(-30),
    };
    expect(getCloseDateAlert(opp, TODAY)).toBe('normal');
  });

  test('tracking_number is not inferred as a date', () => {
    const opp = {
      stage: 'proposal_development',
      tracking_number: 'OPP-2099-000001',
    };
    expect(getCloseDateAlert(opp, TODAY)).toBe('normal');
  });
});

describe('getCloseDateAlert — overdue / due_soon / normal', () => {
  test('close_date yesterday on an open stage returns overdue', () => {
    const opp = { stage: 'proposal_development', close_date: isoOffset(-1) };
    expect(getCloseDateAlert(opp, TODAY)).toBe('overdue');
  });

  test('close_date today on an open stage returns due_soon', () => {
    const opp = { stage: 'proposal_development', close_date: isoOffset(0) };
    expect(getCloseDateAlert(opp, TODAY)).toBe('due_soon');
  });

  test('close_date in 7 days on an open stage returns due_soon', () => {
    const opp = { stage: 'proposal_development', close_date: isoOffset(7) };
    expect(getCloseDateAlert(opp, TODAY)).toBe('due_soon');
  });

  test('close_date in exactly 14 days on an open stage returns due_soon', () => {
    const opp = { stage: 'proposal_development', close_date: isoOffset(14) };
    expect(getCloseDateAlert(opp, TODAY)).toBe('due_soon');
  });

  test('close_date in 15 days on an open stage returns normal', () => {
    const opp = { stage: 'proposal_development', close_date: isoOffset(15) };
    expect(getCloseDateAlert(opp, TODAY)).toBe('normal');
  });

  test('close_date in 30 days on an open stage returns normal', () => {
    const opp = { stage: 'proposal_development', close_date: isoOffset(30) };
    expect(getCloseDateAlert(opp, TODAY)).toBe('normal');
  });
});

describe('getCloseDateAlert — closed stages suppress the alert', () => {
  test('closed_won with overdue close_date returns normal', () => {
    const opp = { stage: 'closed_won', close_date: isoOffset(-30) };
    expect(getCloseDateAlert(opp, TODAY)).toBe('normal');
  });

  test('closed_lost with overdue close_date returns normal', () => {
    const opp = { stage: 'closed_lost', close_date: isoOffset(-30) };
    expect(getCloseDateAlert(opp, TODAY)).toBe('normal');
  });

  test('closed_won with future close_date returns normal', () => {
    const opp = { stage: 'closed_won', close_date: isoOffset(7) };
    expect(getCloseDateAlert(opp, TODAY)).toBe('normal');
  });
});

describe('getCloseDateAlert — accepted field name aliases', () => {
  // The accepted aliases per the spec: close_date, expected_close_date,
  // expected_close, closeDate. Read priority is the array order in the
  // helper; any one alone must be sufficient.

  test('expected_close_date alone triggers overdue', () => {
    const opp = {
      stage: 'proposal_development',
      expected_close_date: isoOffset(-1),
    };
    expect(getCloseDateAlert(opp, TODAY)).toBe('overdue');
  });

  test('expected_close alone triggers due_soon', () => {
    const opp = {
      stage: 'proposal_development',
      expected_close: isoOffset(7),
    };
    expect(getCloseDateAlert(opp, TODAY)).toBe('due_soon');
  });

  test('closeDate camelCase alone triggers overdue', () => {
    const opp = {
      stage: 'proposal_development',
      closeDate: isoOffset(-5),
    };
    expect(getCloseDateAlert(opp, TODAY)).toBe('overdue');
  });

  test('close_date wins over the alias fields when both present', () => {
    // First match wins so the canonical field is authoritative if the
    // payload happens to carry both shapes.
    const opp = {
      stage: 'proposal_development',
      close_date: isoOffset(20), // normal
      expected_close_date: isoOffset(-1), // would be overdue
    };
    expect(getCloseDateAlert(opp, TODAY)).toBe('normal');
  });
});

describe('getCloseDateAlert — defensive', () => {
  test('null opportunity returns normal', () => {
    expect(getCloseDateAlert(null, TODAY)).toBe('normal');
    expect(getCloseDateAlert(undefined, TODAY)).toBe('normal');
  });

  test('unknown stage returns normal even with overdue close_date', () => {
    const opp = { stage: 'made_up_stage', close_date: isoOffset(-30) };
    expect(getCloseDateAlert(opp, TODAY)).toBe('normal');
  });
});
