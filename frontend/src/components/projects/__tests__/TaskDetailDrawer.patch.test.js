/**
 * Unit tests for the TaskDetailDrawer patch() optimistic-update flow.
 *
 * Strategy: exercise the patch function logic as a plain async function
 * extracted from the component's inline definition. We mock:
 *   - api.patch  (from @/lib/api)
 *   - toast.error (from sonner)
 *
 * No DOM / RTL needed for these behavioural assertions.
 *
 * Run with: cd frontend && yarn test --watchAll=false TaskDetailDrawer.patch
 */

// ---------------------------------------------------------------------------
// Minimal Jest mocks (CRA / craco wires these automatically)
// ---------------------------------------------------------------------------

jest.mock('@/lib/api', () => ({
  api: {
    patch: jest.fn(),
  },
}));

jest.mock('sonner', () => ({
  toast: {
    error: jest.fn(),
    success: jest.fn(),
  },
}));

jest.mock('@/lib/apiConfig', () => 'http://localhost:8000');

const { api } = require('@/lib/api');
const { toast } = require('sonner');

// ---------------------------------------------------------------------------
// Helper: replicate the component's patch() function signature
// ---------------------------------------------------------------------------

function makePatch({ taskId, onTaskMutated }) {
  let saving = false;

  const setSaving = (v) => { saving = v; };

  /**
   * Mirrors TaskDetailDrawer patch() verbatim (lines 111-132 of the file).
   * Returns the server response body on success, null on failure.
   */
  async function patch(partial) {
    const API_URL = 'http://localhost:8000';
    setSaving(true);
    try {
      const res = await api.patch(`${API_URL}/api/projects/tasks/${taskId}`, partial);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail =
          typeof data?.detail === 'string'
            ? data.detail
            : data?.detail?.message ?? 'Could not update task.';
        toast.error(detail);
        return null;
      }
      onTaskMutated?.(data);
      return data;
    } catch {
      toast.error('Could not update task.');
      return null;
    } finally {
      setSaving(false);
    }
  }

  return { patch, isSaving: () => saving };
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

beforeEach(() => {
  jest.clearAllMocks();
});

// --- Happy path -----------------------------------------------------------

describe('patch() happy path', () => {
  it('calls api.patch with the correct URL and body', async () => {
    const responseDoc = { task_id: 'task_abc', status: 'in_progress', title: 'My task' };
    api.patch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(responseDoc),
    });

    const onTaskMutated = jest.fn();
    const { patch } = makePatch({ taskId: 'task_abc', onTaskMutated });
    await patch({ status: 'in_progress' });

    expect(api.patch).toHaveBeenCalledWith(
      'http://localhost:8000/api/projects/tasks/task_abc',
      { status: 'in_progress' },
    );
  });

  it('returns the server response body on success', async () => {
    const responseDoc = { task_id: 'task_abc', status: 'in_progress' };
    api.patch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(responseDoc),
    });

    const { patch } = makePatch({ taskId: 'task_abc', onTaskMutated: jest.fn() });
    const result = await patch({ status: 'in_progress' });

    expect(result).toEqual(responseDoc);
  });

  it('calls onTaskMutated with the response body on success', async () => {
    const responseDoc = { task_id: 'task_abc', status: 'in_progress' };
    api.patch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(responseDoc),
    });

    const onTaskMutated = jest.fn();
    const { patch } = makePatch({ taskId: 'task_abc', onTaskMutated });
    await patch({ status: 'in_progress' });

    expect(onTaskMutated).toHaveBeenCalledTimes(1);
    expect(onTaskMutated).toHaveBeenCalledWith(responseDoc);
  });

  it('does not call toast.error on success', async () => {
    api.patch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ task_id: 'task_abc' }),
    });

    const { patch } = makePatch({ taskId: 'task_abc', onTaskMutated: jest.fn() });
    await patch({ priority: 'high' });

    expect(toast.error).not.toHaveBeenCalled();
  });
});

// --- API failure (4xx / 5xx) ----------------------------------------------

describe('patch() API failure path (non-ok response)', () => {
  it('returns null on 500', async () => {
    api.patch.mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ detail: 'Internal server error' }),
    });

    const { patch } = makePatch({ taskId: 'task_abc', onTaskMutated: jest.fn() });
    const result = await patch({ status: 'complete' });

    expect(result).toBeNull();
  });

  it('calls toast.error with the backend detail string on 500', async () => {
    api.patch.mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ detail: 'Internal server error' }),
    });

    const { patch } = makePatch({ taskId: 'task_abc', onTaskMutated: jest.fn() });
    await patch({ status: 'complete' });

    expect(toast.error).toHaveBeenCalledWith('Internal server error');
  });

  it('calls toast.error with detail.message when detail is an object', async () => {
    api.patch.mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ detail: { message: 'Permission denied' } }),
    });

    const { patch } = makePatch({ taskId: 'task_abc', onTaskMutated: jest.fn() });
    await patch({ status: 'complete' });

    expect(toast.error).toHaveBeenCalledWith('Permission denied');
  });

  it('falls back to generic message when detail shape is unknown', async () => {
    api.patch.mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({}),
    });

    const { patch } = makePatch({ taskId: 'task_abc', onTaskMutated: jest.fn() });
    await patch({ status: 'complete' });

    expect(toast.error).toHaveBeenCalledWith('Could not update task.');
  });

  it('does NOT call onTaskMutated on failure', async () => {
    api.patch.mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ detail: 'Server error' }),
    });

    const onTaskMutated = jest.fn();
    const { patch } = makePatch({ taskId: 'task_abc', onTaskMutated });
    await patch({ status: 'complete' });

    expect(onTaskMutated).not.toHaveBeenCalled();
  });
});

// --- Network error path ---------------------------------------------------

describe('patch() network error path (fetch throws)', () => {
  it('returns null when fetch throws', async () => {
    api.patch.mockRejectedValue(new Error('Network failure'));

    const { patch } = makePatch({ taskId: 'task_abc', onTaskMutated: jest.fn() });
    const result = await patch({ status: 'complete' });

    expect(result).toBeNull();
  });

  it('calls toast.error with generic message on network error', async () => {
    api.patch.mockRejectedValue(new Error('Network failure'));

    const { patch } = makePatch({ taskId: 'task_abc', onTaskMutated: jest.fn() });
    await patch({ status: 'complete' });

    expect(toast.error).toHaveBeenCalledWith('Could not update task.');
  });

  it('does NOT call onTaskMutated when fetch throws', async () => {
    api.patch.mockRejectedValue(new Error('Network failure'));

    const onTaskMutated = jest.fn();
    const { patch } = makePatch({ taskId: 'task_abc', onTaskMutated });
    await patch({ status: 'complete' });

    expect(onTaskMutated).not.toHaveBeenCalled();
  });
});

// --- Optimistic revert pattern (per-field) --------------------------------

describe('optimistic revert on failure', () => {
  it('status reverts: field stays at prev value when patch returns null', async () => {
    api.patch.mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ detail: 'Server error' }),
    });

    // Simulate the per-field optimistic update pattern in the component:
    //   setStatus(next); ok = await patch({status: next}); if (!ok) setStatus(prev);
    let status = 'not_started';
    const setStatus = (v) => { status = v; };
    const prev = status;
    const next = 'in_progress';
    setStatus(next);                                          // optimistic set
    expect(status).toBe('in_progress');

    const { patch } = makePatch({ taskId: 'task_abc', onTaskMutated: jest.fn() });
    const ok = await patch({ status: next });
    if (!ok) setStatus(prev);                                 // revert

    expect(status).toBe('not_started');
  });

  it('priority reverts independently of status on its own failure', async () => {
    // status patch succeeds; priority patch fails
    api.patch
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ task_id: 'task_abc', status: 'in_progress' }),
      })
      .mockResolvedValueOnce({
        ok: false,
        json: () => Promise.resolve({ detail: 'Priority update failed' }),
      });

    let status = 'not_started';
    let priority = 'low';

    // Status edit
    const prevStatus = status;
    status = 'in_progress';
    const { patch } = makePatch({ taskId: 'task_abc', onTaskMutated: jest.fn() });
    const statusOk = await patch({ status });
    if (!statusOk) status = prevStatus;
    expect(status).toBe('in_progress'); // stayed updated

    // Priority edit (separate patch call, different field)
    const { patch: patch2 } = makePatch({ taskId: 'task_abc', onTaskMutated: jest.fn() });
    const prevPriority = priority;
    priority = 'critical';
    const priorityOk = await patch2({ priority });
    if (!priorityOk) priority = prevPriority;
    expect(priority).toBe('low');       // reverted
  });
});

// --- Saving/disabled state ------------------------------------------------

describe('saving disabled state', () => {
  it('saving is true during the API call and false after', async () => {
    let resolveApi;
    api.patch.mockReturnValue(
      new Promise((res) => { resolveApi = () => res({ ok: true, json: () => Promise.resolve({}) }); })
    );

    const { patch, isSaving } = makePatch({ taskId: 'task_abc', onTaskMutated: jest.fn() });
    const patchPromise = patch({ status: 'complete' });

    // Synchronously after patch() is called, saving should be true
    expect(isSaving()).toBe(true);

    resolveApi();
    await patchPromise;

    expect(isSaving()).toBe(false);
  });
});

// --- onTaskMutated not called when no callback ----------------------------

describe('optional onTaskMutated callback', () => {
  it('does not throw when onTaskMutated is undefined', async () => {
    api.patch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ task_id: 'task_abc' }),
    });

    const { patch } = makePatch({ taskId: 'task_abc', onTaskMutated: undefined });
    await expect(patch({ status: 'complete' })).resolves.not.toThrow();
  });
});
