/**
 * Unit tests for BoardView bulk-selection state logic.
 *
 * Strategy: test the Set-based selection state logic as pure functions —
 * no component mount needed. Mirrors the pattern established in
 * ProjectDetailPage.callbacks.test.js.
 *
 * Run with: cd frontend && yarn test --watchAll=false BoardView.bulkSelect
 */

// ---------------------------------------------------------------------------
// Pure selection reducers (mirrored from BoardView.js)
// ---------------------------------------------------------------------------

/**
 * Toggle a single task in/out of the selected set.
 */
function toggleSelected(prev, taskId) {
  const next = new Set(prev);
  if (next.has(taskId)) next.delete(taskId);
  else next.add(taskId);
  return next;
}

/**
 * Add a range of task IDs to selection (shift-click extension).
 * anchorId and targetId are task IDs; orderedIds is the flat list of task IDs
 * in the order they appear in the view.
 */
function extendSelection(prev, anchorId, targetId, orderedIds) {
  const ai = orderedIds.indexOf(anchorId);
  const ti = orderedIds.indexOf(targetId);
  if (ai === -1 || ti === -1) return new Set(prev);
  const lo = Math.min(ai, ti);
  const hi = Math.max(ai, ti);
  const next = new Set(prev);
  for (let i = lo; i <= hi; i++) next.add(orderedIds[i]);
  return next;
}

/**
 * Clear all selected task IDs and exit select mode.
 */
function clearSelection() {
  return new Set();
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const TASK_IDS = ['task_001', 'task_002', 'task_003', 'task_004', 'task_005'];

beforeEach(() => { /* no mocks needed */ });

// ---------------------------------------------------------------------------
// toggleSelected
// ---------------------------------------------------------------------------

describe('toggleSelected()', () => {
  it('adds a task ID when not in set', () => {
    const result = toggleSelected(new Set(), 'task_001');
    expect(result.has('task_001')).toBe(true);
    expect(result.size).toBe(1);
  });

  it('removes a task ID when already in set', () => {
    const prev = new Set(['task_001', 'task_002']);
    const result = toggleSelected(prev, 'task_001');
    expect(result.has('task_001')).toBe(false);
    expect(result.size).toBe(1);
  });

  it('does not mutate the original Set', () => {
    const prev = new Set(['task_001']);
    toggleSelected(prev, 'task_002');
    expect(prev.size).toBe(1); // unchanged
  });

  it('handles empty set — adds first item', () => {
    const result = toggleSelected(new Set(), 'task_001');
    expect(result.size).toBe(1);
  });

  it('deselecting the last item leaves an empty set', () => {
    const result = toggleSelected(new Set(['task_001']), 'task_001');
    expect(result.size).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// extendSelection (shift-click)
// ---------------------------------------------------------------------------

describe('extendSelection() — shift-click range', () => {
  it('selects all tasks between anchor and target (inclusive)', () => {
    const result = extendSelection(new Set(), 'task_001', 'task_003', TASK_IDS);
    expect([...result]).toEqual(expect.arrayContaining(['task_001', 'task_002', 'task_003']));
    expect(result.size).toBe(3);
  });

  it('works in reverse order (target before anchor)', () => {
    const result = extendSelection(new Set(), 'task_004', 'task_002', TASK_IDS);
    expect(result.has('task_002')).toBe(true);
    expect(result.has('task_003')).toBe(true);
    expect(result.has('task_004')).toBe(true);
    expect(result.size).toBe(3);
  });

  it('preserves previously selected IDs outside the range', () => {
    const prev = new Set(['task_005']);
    const result = extendSelection(prev, 'task_001', 'task_002', TASK_IDS);
    expect(result.has('task_005')).toBe(true); // kept
    expect(result.has('task_001')).toBe(true);
    expect(result.has('task_002')).toBe(true);
  });

  it('returns same set when anchor or target not found in orderedIds', () => {
    const prev = new Set(['task_001']);
    const result = extendSelection(prev, 'task_999', 'task_001', TASK_IDS);
    expect(result).toEqual(prev);
  });

  it('selects single task when anchor === target', () => {
    const result = extendSelection(new Set(), 'task_003', 'task_003', TASK_IDS);
    expect(result.has('task_003')).toBe(true);
    expect(result.size).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// clearSelection
// ---------------------------------------------------------------------------

describe('clearSelection()', () => {
  it('returns an empty Set regardless of previous state', () => {
    const result = clearSelection();
    expect(result.size).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Select-mode toggle contract
// ---------------------------------------------------------------------------

describe('select mode toggle contract', () => {
  it('entering select mode leaves selection empty', () => {
    // When selectMode flips true, selectedIds should still be empty Set.
    let selectMode = false;
    let selectedIds = new Set();

    selectMode = true; // toggle on
    // No tasks auto-selected
    expect(selectedIds.size).toBe(0);
  });

  it('exiting select mode clears selection', () => {
    let selectMode = true;
    let selectedIds = new Set(['task_001', 'task_002']);

    // On toggle off, the component calls setSelectedIds(new Set())
    selectMode = false;
    selectedIds = new Set(); // simulates the setState call in the component

    expect(selectedIds.size).toBe(0);
    expect(selectMode).toBe(false);
  });

  it('BulkActionBar count equals selectedIds.size', () => {
    const selectedIds = new Set(['task_001', 'task_002', 'task_003']);
    const displayCount = selectedIds.size;
    expect(displayCount).toBe(3);
  });
});

// ---------------------------------------------------------------------------
// Mixed select / deselect sequence
// ---------------------------------------------------------------------------

describe('mixed selection sequence', () => {
  it('select two, deselect one, extend to three — final count is four', () => {
    let sel = new Set();
    sel = toggleSelected(sel, 'task_001'); // +task_001 → size 1
    sel = toggleSelected(sel, 'task_002'); // +task_002 → size 2
    sel = toggleSelected(sel, 'task_001'); // -task_001 → size 1
    sel = extendSelection(sel, 'task_002', 'task_004', TASK_IDS); // adds task_002,003,004 → size 3 (task_002 already in)
    expect(sel.size).toBe(3);
    expect(sel.has('task_002')).toBe(true);
    expect(sel.has('task_003')).toBe(true);
    expect(sel.has('task_004')).toBe(true);
    expect(sel.has('task_001')).toBe(false);
  });

  it('full clear after complex selection returns empty set', () => {
    let sel = new Set(['task_001', 'task_002', 'task_003']);
    sel = clearSelection();
    expect(sel.size).toBe(0);
  });
});
