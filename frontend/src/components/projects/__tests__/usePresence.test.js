/**
 * Tests for usePresence hook.
 *
 * Strategy: mock api.post, exercise the hook via renderHook with fake timers.
 * Verify: initial heartbeat on mount, 30s cadence, visibility-gated pause/resume.
 */

import { renderHook, act } from '@testing-library/react-hooks';

jest.mock('@/lib/apiConfig', () => 'http://localhost:8000');

jest.mock('@/lib/api', () => ({
  api: {
    post: jest.fn(),
  },
}));

const { api } = require('@/lib/api');

function mockOkResponse(viewing = []) {
  api.post.mockResolvedValue({
    ok: true,
    json: () => Promise.resolve({ viewing }),
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
  // Default: tab is visible.
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => 'visible',
  });
});

afterEach(() => {
  jest.useRealTimers();
});

const { usePresence } = require('../hooks/usePresence');

const HEARTBEAT_INTERVAL_MS = 30_000;

describe('usePresence — initial heartbeat', () => {
  it('fires a heartbeat immediately on mount', async () => {
    mockOkResponse([]);
    renderHook(() => usePresence('proj-1', 'board'));
    await act(async () => { jest.runAllTimers(); });
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith(
      'http://localhost:8000/api/projects/proj-1/presence/heartbeat',
      { viewport: 'board' },
    );
  });

  it('does not fire when projectId is null', async () => {
    mockOkResponse([]);
    renderHook(() => usePresence(null, 'board'));
    await act(async () => { jest.runAllTimers(); });
    expect(api.post).not.toHaveBeenCalled();
  });

  it('updates viewing from the heartbeat response', async () => {
    const viewing = [{ user_id: 'u1', name: 'Alice', viewport: 'board' }];
    mockOkResponse(viewing);
    const { result } = renderHook(() => usePresence('proj-1', 'board'));
    await act(async () => { jest.runAllTimers(); });
    expect(result.current.viewing).toEqual(viewing);
  });
});

describe('usePresence — interval cadence', () => {
  it('fires again after 30s', async () => {
    mockOkResponse([]);
    renderHook(() => usePresence('proj-1', 'backlog'));
    await act(async () => { jest.runAllTimers(); });
    expect(api.post).toHaveBeenCalledTimes(1);

    await act(async () => { jest.advanceTimersByTime(HEARTBEAT_INTERVAL_MS); });
    await act(async () => {}); // flush micro-tasks
    expect(api.post).toHaveBeenCalledTimes(2);
  });

  it('stops firing on unmount', async () => {
    mockOkResponse([]);
    const { unmount } = renderHook(() => usePresence('proj-1', 'board'));
    await act(async () => { jest.runAllTimers(); });
    unmount();
    await act(async () => { jest.advanceTimersByTime(HEARTBEAT_INTERVAL_MS * 3); });
    // Only the initial heartbeat should have fired.
    expect(api.post).toHaveBeenCalledTimes(1);
  });
});

describe('usePresence — visibility gating', () => {
  it('skips the heartbeat when the tab is hidden', async () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    mockOkResponse([]);
    renderHook(() => usePresence('proj-1', 'board'));
    await act(async () => { jest.runAllTimers(); });
    expect(api.post).not.toHaveBeenCalled();
  });

  it('fires a heartbeat immediately when the tab becomes visible again', async () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    mockOkResponse([]);
    renderHook(() => usePresence('proj-1', 'board'));
    await act(async () => { jest.runAllTimers(); });
    expect(api.post).not.toHaveBeenCalled();

    // Simulate visibility change back to visible.
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await act(async () => {}); // flush micro-tasks
    expect(api.post).toHaveBeenCalledTimes(1);
  });
});
