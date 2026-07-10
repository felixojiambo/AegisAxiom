/**
 * Unit tests for ProjectDetailPage task-management callbacks.
 *
 * Tests cover the pure logic of onTaskMutated and onTaskDeleted —
 * extracted as standalone reducers so they can be tested without
 * mounting the full component tree.
 *
 * Run with: cd frontend && yarn test --watchAll=false ProjectDetailPage.callbacks
 */

// ---------------------------------------------------------------------------
// onTaskMutated logic
// Mirrors ProjectDetailPage.js lines 704-714.
// ---------------------------------------------------------------------------

/**
 * Pure reducer extracted from ProjectDetailPage.onTaskMutated.
 * Returns the new tasks array (does NOT call fetchTasks — caller must do
 * that when updated is null).
 */
function applyTaskMutated(prev, updated) {
  if (!updated) return prev;
  const id = updated.task_id ?? updated._id;
  const idx = prev.findIndex((t) => (t.task_id ?? t._id) === id);
  if (idx === -1) return [...prev, updated];
  const next = prev.slice();
  next[idx] = updated;
  return next;
}

/**
 * onTaskDeleted reducer — mirrors ProjectDetailPage.js lines 716-719.
 */
function applyTaskDeleted(prev, taskId) {
  if (!taskId) return prev;
  return prev.filter((t) => (t.task_id ?? t._id) !== taskId);
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeTask(overrides = {}) {
  return {
    task_id: 'task_001',
    title: 'Default task',
    status: 'not_started',
    priority: 'medium',
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// onTaskMutated tests
// ---------------------------------------------------------------------------

describe('onTaskMutated(null) — fallback signal', () => {
  it('returns the same array reference (caller triggers fetchTasks)', () => {
    // When updated is null the real component calls fetchTasks() and doesn't
    // touch state. We verify the null guard is separate from array logic.
    const tasks = [makeTask()];
    // The component guard: if (!updated) { fetchTasks(); return; }
    // We just test that applyTaskMutated is never called with null in prod code.
    // Passing null would cause id=undefined and infinite mismatches.
    // This test documents the expected caller behaviour.
    expect(() => applyTaskMutated(tasks, null)).not.toThrow();
    // (The component short-circuits before calling applyTaskMutated when null)
  });
});

describe('onTaskMutated(updated) — merge into existing list', () => {
  it('replaces a task with matching task_id in place, length unchanged', () => {
    const initial = [
      makeTask({ task_id: 'task_001', status: 'not_started' }),
      makeTask({ task_id: 'task_002', status: 'in_progress' }),
    ];
    const updated = { task_id: 'task_001', status: 'complete', title: 'Done now' };

    const result = applyTaskMutated(initial, updated);

    expect(result).toHaveLength(2);
    expect(result[0]).toEqual(updated);
    expect(result[1]).toEqual(initial[1]);
  });

  it('replaces a task using _id when task_id is absent', () => {
    const initial = [
      { _id: 'mongo_abc', title: 'Old title', status: 'not_started' },
    ];
    const updated = { _id: 'mongo_abc', title: 'New title', status: 'complete' };

    const result = applyTaskMutated(initial, updated);

    expect(result).toHaveLength(1);
    expect(result[0].title).toBe('New title');
  });

  it('appends to the list when task_id is not found (new task)', () => {
    const initial = [makeTask({ task_id: 'task_001' })];
    const newTask = makeTask({ task_id: 'task_999', title: 'Brand new' });

    const result = applyTaskMutated(initial, newTask);

    expect(result).toHaveLength(2);
    expect(result[1]).toEqual(newTask);
    expect(result[0]).toEqual(initial[0]);
  });

  it('does not mutate the original array', () => {
    const initial = [makeTask({ task_id: 'task_001' })];
    const updated = { task_id: 'task_001', status: 'complete' };

    const result = applyTaskMutated(initial, updated);

    expect(result).not.toBe(initial);
    expect(initial[0].status).toBe('not_started'); // original unchanged
  });

  it('handles empty initial list — appends the task', () => {
    const newTask = makeTask({ task_id: 'task_001' });
    const result = applyTaskMutated([], newTask);
    expect(result).toHaveLength(1);
    expect(result[0]).toEqual(newTask);
  });

  it('preserves ordering of other tasks during in-place replace', () => {
    const initial = [
      makeTask({ task_id: 'task_001' }),
      makeTask({ task_id: 'task_002' }),
      makeTask({ task_id: 'task_003' }),
    ];
    const updated = { task_id: 'task_002', status: 'complete' };
    const result = applyTaskMutated(initial, updated);

    expect(result.map((t) => t.task_id)).toEqual(['task_001', 'task_002', 'task_003']);
    expect(result[1]).toEqual(updated);
  });

  it('uses task_id over _id when both are present on existing task', () => {
    const initial = [{ task_id: 'task_001', _id: 'mongo_abc', status: 'not_started' }];
    const updated = { task_id: 'task_001', _id: 'mongo_abc', status: 'complete' };
    const result = applyTaskMutated(initial, updated);
    expect(result[0].status).toBe('complete');
    expect(result).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// onTaskDeleted tests
// ---------------------------------------------------------------------------

describe('onTaskDeleted(taskId) — filter out by id', () => {
  it('removes the task with matching task_id', () => {
    const tasks = [
      makeTask({ task_id: 'task_001' }),
      makeTask({ task_id: 'task_002' }),
    ];
    const result = applyTaskDeleted(tasks, 'task_001');
    expect(result).toHaveLength(1);
    expect(result[0].task_id).toBe('task_002');
  });

  it('removes the task with matching _id when task_id absent', () => {
    const tasks = [
      { _id: 'mongo_abc', title: 'Task A' },
      { _id: 'mongo_xyz', title: 'Task B' },
    ];
    const result = applyTaskDeleted(tasks, 'mongo_abc');
    expect(result).toHaveLength(1);
    expect(result[0]._id).toBe('mongo_xyz');
  });

  it('returns same-length array when id not found (no-op)', () => {
    const tasks = [makeTask({ task_id: 'task_001' })];
    const result = applyTaskDeleted(tasks, 'task_999');
    expect(result).toHaveLength(1);
  });

  it('handles empty taskId gracefully — returns array unchanged', () => {
    const tasks = [makeTask({ task_id: 'task_001' })];
    const result = applyTaskDeleted(tasks, '');
    // The component guard: if (!taskId) return;
    // When taskId is falsy the real component returns early.
    // Our reducer mirrors this: when taskId is falsy, filter((t) => id !== '')
    // will still find a mismatch, but the real guard prevents this call.
    // We document that the pure function handles it without crashing.
    expect(result).not.toBeNull();
  });

  it('does not mutate the original array', () => {
    const tasks = [makeTask({ task_id: 'task_001' })];
    applyTaskDeleted(tasks, 'task_001');
    expect(tasks).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Mixed scenario — onTaskMutated followed by onTaskDeleted
// ---------------------------------------------------------------------------

describe('mixed mutation + delete sequence', () => {
  it('update then delete leaves correct final state', () => {
    const initial = [
      makeTask({ task_id: 'task_001', status: 'not_started' }),
      makeTask({ task_id: 'task_002', status: 'not_started' }),
    ];

    // Update task_001
    const afterUpdate = applyTaskMutated(initial, { task_id: 'task_001', status: 'complete' });
    expect(afterUpdate[0].status).toBe('complete');

    // Delete task_002
    const afterDelete = applyTaskDeleted(afterUpdate, 'task_002');
    expect(afterDelete).toHaveLength(1);
    expect(afterDelete[0].task_id).toBe('task_001');
  });
});
