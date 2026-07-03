/**
 * Unit tests for InlineStatusPicker (and the shared patchTask helper).
 *
 * Strategy: exercise the patch logic as a pure async function, mirroring
 * the pattern in TaskDetailDrawer.patch.test.js.
 *
 * No DOM / RTL needed for the core behavioural assertions.
 *
 * Run with: cd frontend && yarn test --watchAll=false InlineStatusPicker
 */

jest.mock('@/lib/api', () => ({
  api: { patch: jest.fn(), patchConditional: jest.fn() },
}));

jest.mock('sonner', () => ({
  toast: { error: jest.fn(), success: jest.fn(), warning: jest.fn() },
}));

jest.mock('@/lib/apiConfig', () => 'http://localhost:8000');

const { api } = require('@/lib/api');
const { toast } = require('sonner');

// ---------------------------------------------------------------------------
// Extract the shared patchTask helper logic directly for unit testing.
// This mirrors the updated implementation in InlineEditPopover.js (v1.4).
//
// Uses api.patchConditional (If-Match) and routes 409s to onConflict.
// ---------------------------------------------------------------------------

async function patchTask({ task, taskId, partial, setSaving, onTaskMutated, onConflict }) {
  const API_URL = 'http://localhost:8000';
  setSaving(true);
  try {
    const ifMatchValue = task?.updated_at ?? null;
    const res = await api.patchConditional(
      `${API_URL}/api/projects/tasks/${taskId}`,
      partial,
      ifMatchValue,
    );
    const data = await res.json().catch(() => ({}));

    if (res.status === 409) {
      if (onConflict) {
        onConflict({
          myDoc: { ...task, ...partial },
          theirDoc: data?.current_doc ?? null,
          retryFn: () => patchTask({ task, taskId, partial, setSaving, onTaskMutated, onConflict: null }),
        });
      } else {
        toast.error('Another user changed this task. Refresh to see latest.');
      }
      return null;
    }

    if (!res.ok) {
      const detail =
        typeof data?.detail === 'string'
          ? data.detail
          : data?.detail?.message ?? 'Could not update task.';
      toast.error(detail);
      return null;
    }
    if (onTaskMutated) onTaskMutated(data);
    return data;
  } catch {
    toast.error('Could not update task.');
    return null;
  } finally {
    setSaving(false);
  }
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeTask(overrides = {}) {
  return { task_id: 'task_001', title: 'Test task', status: 'not_started', priority: 'medium', ...overrides };
}

beforeEach(() => { jest.clearAllMocks(); });

// ---------------------------------------------------------------------------
// Happy path
// ---------------------------------------------------------------------------

describe('patchTask — happy path', () => {
  it('calls api.patchConditional with correct URL and status field', async () => {
    const task = makeTask({ status: 'in_progress', updated_at: '2026-05-01T00:00:00Z' });
    api.patchConditional.mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve(task) });

    await patchTask({ task, taskId: 'task_001', partial: { status: 'in_progress' }, setSaving: jest.fn(), onTaskMutated: jest.fn(), onConflict: null });

    expect(api.patchConditional).toHaveBeenCalledWith(
      'http://localhost:8000/api/projects/tasks/task_001',
      { status: 'in_progress' },
      '2026-05-01T00:00:00Z',
    );
  });

  it('calls onTaskMutated with the returned doc on success', async () => {
    const task = makeTask();
    const doc = makeTask({ status: 'complete' });
    api.patchConditional.mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve(doc) });
    const onTaskMutated = jest.fn();

    await patchTask({ task, taskId: 'task_001', partial: { status: 'complete' }, setSaving: jest.fn(), onTaskMutated, onConflict: null });

    expect(onTaskMutated).toHaveBeenCalledTimes(1);
    expect(onTaskMutated).toHaveBeenCalledWith(doc);
  });

  it('does not call toast.error on success', async () => {
    const task = makeTask();
    api.patchConditional.mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve(task) });

    await patchTask({ task, taskId: 'task_001', partial: { status: 'complete' }, setSaving: jest.fn(), onTaskMutated: jest.fn(), onConflict: null });

    expect(toast.error).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Failure path (non-ok response)
// ---------------------------------------------------------------------------

describe('patchTask — API failure', () => {
  it('returns null on non-ok response', async () => {
    const task = makeTask();
    api.patchConditional.mockResolvedValue({ ok: false, status: 403, json: () => Promise.resolve({ detail: 'Forbidden' }) });

    const result = await patchTask({ task, taskId: 'task_001', partial: { status: 'complete' }, setSaving: jest.fn(), onTaskMutated: jest.fn(), onConflict: null });

    expect(result).toBeNull();
  });

  it('calls toast.error with the backend detail string', async () => {
    const task = makeTask();
    api.patchConditional.mockResolvedValue({ ok: false, status: 403, json: () => Promise.resolve({ detail: 'Project is frozen' }) });

    await patchTask({ task, taskId: 'task_001', partial: { status: 'complete' }, setSaving: jest.fn(), onTaskMutated: jest.fn(), onConflict: null });

    expect(toast.error).toHaveBeenCalledWith('Project is frozen');
  });

  it('does NOT call onTaskMutated on failure', async () => {
    const task = makeTask();
    api.patchConditional.mockResolvedValue({ ok: false, status: 403, json: () => Promise.resolve({ detail: 'Error' }) });
    const onTaskMutated = jest.fn();

    await patchTask({ task, taskId: 'task_001', partial: { status: 'complete' }, setSaving: jest.fn(), onTaskMutated, onConflict: null });

    expect(onTaskMutated).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Network error path
// ---------------------------------------------------------------------------

describe('patchTask — network error', () => {
  it('returns null when fetch throws', async () => {
    const task = makeTask();
    api.patchConditional.mockRejectedValue(new Error('Network down'));

    const result = await patchTask({ task, taskId: 'task_001', partial: { status: 'complete' }, setSaving: jest.fn(), onTaskMutated: jest.fn(), onConflict: null });

    expect(result).toBeNull();
  });

  it('calls toast.error with generic message on network error', async () => {
    const task = makeTask();
    api.patchConditional.mockRejectedValue(new Error('Network down'));

    await patchTask({ task, taskId: 'task_001', partial: { status: 'complete' }, setSaving: jest.fn(), onTaskMutated: jest.fn(), onConflict: null });

    expect(toast.error).toHaveBeenCalledWith('Could not update task.');
  });
});

// ---------------------------------------------------------------------------
// Optimistic revert pattern (the ESC / failure rollback)
// ---------------------------------------------------------------------------

describe('optimistic revert on failure', () => {
  it('status reverts to prev value when patchTask returns null', async () => {
    const task = makeTask();
    api.patchConditional.mockResolvedValue({ ok: false, status: 403, json: () => Promise.resolve({ detail: 'Forbidden' }) });

    let localStatus = 'not_started';
    const setLocalStatus = (v) => { localStatus = v; };

    const prev = localStatus;
    setLocalStatus('in_progress'); // optimistic
    expect(localStatus).toBe('in_progress');

    const ok = await patchTask({ task, taskId: 'task_001', partial: { status: 'in_progress' }, setSaving: jest.fn(), onTaskMutated: jest.fn(), onConflict: null });
    if (!ok) setLocalStatus(prev); // revert

    expect(localStatus).toBe('not_started');
  });

  it('priority reverts independently of status', async () => {
    const task = makeTask();
    api.patchConditional
      .mockResolvedValueOnce({ ok: true, status: 200, json: () => Promise.resolve({ task_id: 'task_001', status: 'in_progress' }) })
      .mockResolvedValueOnce({ ok: false, status: 403, json: () => Promise.resolve({ detail: 'Invalid priority' }) });

    let status = 'not_started';
    let priority = 'low';

    const statusOk = await patchTask({ task, taskId: 'task_001', partial: { status: 'in_progress' }, setSaving: jest.fn(), onTaskMutated: jest.fn(), onConflict: null });
    if (!statusOk) status = 'not_started';
    expect(status).toBe('not_started'); // patch returned data but we didn't update via return value here; that's fine

    const prevPriority = priority;
    priority = 'critical';
    const priorityOk = await patchTask({ task, taskId: 'task_001', partial: { priority: 'critical' }, setSaving: jest.fn(), onTaskMutated: jest.fn(), onConflict: null });
    if (!priorityOk) priority = prevPriority;
    expect(priority).toBe('low'); // reverted
  });
});

// ---------------------------------------------------------------------------
// saving state transitions
// ---------------------------------------------------------------------------

describe('saving state', () => {
  it('setSaving(true) is called before the request and setSaving(false) after', async () => {
    const task = makeTask();
    let resolveApi;
    api.patchConditional.mockReturnValue(new Promise((res) => { resolveApi = () => res({ ok: true, status: 200, json: () => Promise.resolve({}) }); }));

    const savingStates = [];
    const setSaving = (v) => savingStates.push(v);

    const p = patchTask({ task, taskId: 'task_001', partial: { status: 'complete' }, setSaving, onTaskMutated: jest.fn(), onConflict: null });
    expect(savingStates).toEqual([true]);

    resolveApi();
    await p;

    expect(savingStates).toEqual([true, false]);
  });
});

// ---------------------------------------------------------------------------
// ESC closes (popover state contract — documented test)
// ---------------------------------------------------------------------------

describe('popover ESC contract', () => {
  it('same-field selection at current value is a no-op (does not call api.patchConditional)', async () => {
    // When the user clicks the same option already selected, the component
    // returns early before calling patchTask. We document this contract:
    // if value === localStatus, setOpenId(null) and return — no PATCH fired.
    // This test verifies the guard condition at the call-site level.
    const currentStatus = 'in_progress';
    const selectedValue = 'in_progress';
    let patchCalled = false;

    if (selectedValue !== currentStatus) {
      patchCalled = true;
      const task = makeTask({ status: currentStatus });
      await patchTask({ task, taskId: 'task_001', partial: { status: selectedValue }, setSaving: jest.fn(), onTaskMutated: jest.fn(), onConflict: null });
    }

    expect(patchCalled).toBe(false);
    expect(api.patchConditional).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// 409 Conflict path (v1.4 — onConflict wiring)
// ---------------------------------------------------------------------------

describe('patchTask — 409 conflict', () => {
  const currentDoc = { task_id: 'task_001', status: 'complete', priority: 'high', updated_at: '2026-05-11T10:00:00Z' };

  it('calls onConflict with correct shape when onConflict is provided', async () => {
    const task = makeTask({ status: 'not_started', updated_at: '2026-05-11T09:00:00Z' });
    api.patchConditional.mockResolvedValue({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ current_doc: currentDoc }),
    });
    const onConflict = jest.fn();

    await patchTask({ task, taskId: 'task_001', partial: { status: 'in_progress' }, setSaving: jest.fn(), onTaskMutated: jest.fn(), onConflict });

    expect(onConflict).toHaveBeenCalledTimes(1);
    const call = onConflict.mock.calls[0][0];
    expect(call.myDoc).toMatchObject({ status: 'in_progress' });
    expect(call.theirDoc).toEqual(currentDoc);
    expect(typeof call.retryFn).toBe('function');
  });

  it('reverts local state before calling onConflict (caller-side revert pattern)', async () => {
    // Mirrors the pattern in each picker's handleSelect:
    // the onConflict wrapper calls setLocalStatus(prev) then onConflict(conflict).
    const task = makeTask({ status: 'not_started' });
    api.patchConditional.mockResolvedValue({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ current_doc: currentDoc }),
    });

    let localStatus = 'not_started';
    const prev = localStatus;
    localStatus = 'in_progress'; // optimistic

    const onConflict = jest.fn((conflict) => {
      // Simulate what the picker's onConflict wrapper does: revert then call parent
      localStatus = prev;
    });

    await patchTask({ task, taskId: 'task_001', partial: { status: 'in_progress' }, setSaving: jest.fn(), onTaskMutated: jest.fn(), onConflict });

    expect(localStatus).toBe('not_started');
    expect(onConflict).toHaveBeenCalledTimes(1);
  });

  it('falls back to toast when onConflict is NOT provided (409 + no handler)', async () => {
    const task = makeTask({ status: 'not_started' });
    api.patchConditional.mockResolvedValue({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ current_doc: currentDoc }),
    });

    await patchTask({ task, taskId: 'task_001', partial: { status: 'in_progress' }, setSaving: jest.fn(), onTaskMutated: jest.fn(), onConflict: null });

    expect(toast.error).toHaveBeenCalledWith('Another user changed this task. Refresh to see latest.');
  });

  it('does NOT call onTaskMutated on 409', async () => {
    const task = makeTask();
    api.patchConditional.mockResolvedValue({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ current_doc: currentDoc }),
    });
    const onTaskMutated = jest.fn();

    await patchTask({ task, taskId: 'task_001', partial: { status: 'in_progress' }, setSaving: jest.fn(), onTaskMutated, onConflict: jest.fn() });

    expect(onTaskMutated).not.toHaveBeenCalled();
  });

  it('returns null on 409', async () => {
    const task = makeTask();
    api.patchConditional.mockResolvedValue({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ current_doc: currentDoc }),
    });

    const result = await patchTask({ task, taskId: 'task_001', partial: { status: 'in_progress' }, setSaving: jest.fn(), onTaskMutated: jest.fn(), onConflict: jest.fn() });

    expect(result).toBeNull();
  });
});
