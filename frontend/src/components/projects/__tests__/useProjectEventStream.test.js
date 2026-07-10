/**
 * Tests for useProjectEventStream hook.
 *
 * Strategy: mock EventSource globally + the `api` wrapper's POST, exercise
 * the hook via renderHook, then assert on the ticket-exchange flow,
 * connection lifecycle, self-event filtering, and lastEventId tracking.
 *
 * v1.4: the hook does a one-time ticket fetch before opening EventSource.
 * Tickets are single-use, so on every reconnect we expect a NEW ticket
 * fetch before the next EventSource open.
 */

import { renderHook, act } from '@testing-library/react-hooks';

jest.mock('@/lib/apiConfig', () => 'http://localhost:8000');

// Mock useAuth to return a stable current user.
jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: { user_id: 'me-123', name: 'Test User' } }),
}));

// Mock the `api` wrapper so we can pin what POST /events/ticket returns.
const apiPostMock = jest.fn();
jest.mock('@/lib/api', () => ({
  api: { post: (...args) => apiPostMock(...args) },
}));

// ---- EventSource mock ----

class MockEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    MockEventSource.instances.push(this);
    // Simulate open asynchronously (matches browser behaviour).
    setTimeout(() => this.onopen?.(), 0);
  }

  addEventListener(type, fn) {
    this.listeners[type] = fn;
  }

  close() {
    this.closed = true;
  }

  emit(data, lastEventId = '') {
    const event = { data, lastEventId };
    this.onmessage?.(event);
  }

  triggerError() {
    this.onerror?.();
  }
}
MockEventSource.instances = [];

function mockTicketResponse({ ok = true, status = 201, ticket = 'tkt-fresh' } = {}) {
  apiPostMock.mockImplementationOnce(() =>
    Promise.resolve({
      ok,
      status,
      json: () => Promise.resolve(ok ? { ticket, expires_in: 30 } : { detail: 'denied' }),
    }),
  );
}

beforeEach(() => {
  MockEventSource.instances = [];
  global.EventSource = MockEventSource;
  apiPostMock.mockReset();
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
  jest.clearAllMocks();
});

// ---- Import after mocks are set ----

const { useProjectEventStream } = require('../hooks/useProjectEventStream');

// ---- Tests ----

describe('useProjectEventStream — ticket exchange', () => {
  it('fetches a ticket BEFORE opening the EventSource', async () => {
    mockTicketResponse({ ticket: 'tkt-abc' });

    renderHook(() => useProjectEventStream('proj-1', {}));

    // Flush the awaited ticket POST.
    await act(async () => { await Promise.resolve(); });

    expect(apiPostMock).toHaveBeenCalledTimes(1);
    expect(apiPostMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/projects/proj-1/events/ticket',
    );

    // EventSource opens AFTER the ticket has resolved, with ?ticket=<value>.
    await act(async () => { jest.runAllTimers(); });
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toContain('/api/projects/proj-1/events');
    expect(MockEventSource.instances[0].url).toContain('ticket=tkt-abc');
    // Old v1.3 pattern must NOT be present.
    expect(MockEventSource.instances[0].url).not.toContain('access_token=');
  });

  it('connected becomes true after open fires', async () => {
    mockTicketResponse();
    const { result } = renderHook(() => useProjectEventStream('proj-1', {}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { jest.runAllTimers(); });
    expect(result.current.connected).toBe(true);
  });

  it('cleans up EventSource on unmount', async () => {
    mockTicketResponse();
    const { unmount } = renderHook(() => useProjectEventStream('proj-1', {}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { jest.runAllTimers(); });
    unmount();
    expect(MockEventSource.instances[0].closed).toBe(true);
  });

  it('surfaces an error and stops reconnecting when ticket fetch returns 401', async () => {
    apiPostMock.mockImplementationOnce(() =>
      Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({}) }),
    );

    const { result } = renderHook(() => useProjectEventStream('proj-1', {}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });

    expect(result.current.error).toBe('Authentication lost');
    expect(MockEventSource.instances).toHaveLength(0);

    // Advance the timer well past any backoff — we should NOT see a retry.
    await act(async () => { jest.advanceTimersByTime(60_000); });
    expect(apiPostMock).toHaveBeenCalledTimes(1);
    expect(MockEventSource.instances).toHaveLength(0);
  });

  it('surfaces a permission error and stops reconnecting when ticket fetch returns 403', async () => {
    apiPostMock.mockImplementationOnce(() =>
      Promise.resolve({ ok: false, status: 403, json: () => Promise.resolve({}) }),
    );

    const { result } = renderHook(() => useProjectEventStream('proj-1', {}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });

    expect(result.current.error).toBe('Permission denied');
    expect(MockEventSource.instances).toHaveLength(0);
  });
});

describe('useProjectEventStream — self-event filtering', () => {
  it('drops events from the current user (actor_user_id === me)', async () => {
    mockTicketResponse();
    const onEvent = jest.fn();
    renderHook(() => useProjectEventStream('proj-1', { onEvent }));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { jest.runAllTimers(); });

    const es = MockEventSource.instances[0];
    act(() => {
      es.emit(JSON.stringify({ type: 'task.updated', actor_user_id: 'me-123', payload: {} }), 'evt-1');
    });

    expect(onEvent).not.toHaveBeenCalled();
  });

  it('passes through events from other users', async () => {
    mockTicketResponse();
    const onEvent = jest.fn();
    renderHook(() => useProjectEventStream('proj-1', { onEvent }));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { jest.runAllTimers(); });

    const es = MockEventSource.instances[0];
    const payload = { type: 'task.updated', actor_user_id: 'other-456', payload: { task_id: 't1' } };
    act(() => {
      es.emit(JSON.stringify(payload), 'evt-2');
    });

    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent).toHaveBeenCalledWith(payload);
  });
});

describe('useProjectEventStream — lastEventId tracking', () => {
  it('updates lastEventId from e.lastEventId on incoming events', async () => {
    mockTicketResponse();
    const { result } = renderHook(() => useProjectEventStream('proj-1', {}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { jest.runAllTimers(); });

    const es = MockEventSource.instances[0];
    act(() => {
      es.emit(JSON.stringify({ type: 'task.updated', actor_user_id: 'other-789', payload: {} }), 'event-id-42');
    });

    expect(result.current.lastEventId).toBe('event-id-42');
  });

  it('does not update lastEventId when e.lastEventId is empty', async () => {
    mockTicketResponse();
    const { result } = renderHook(() => useProjectEventStream('proj-1', {}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { jest.runAllTimers(); });

    const es = MockEventSource.instances[0];
    act(() => {
      es.emit(JSON.stringify({ type: 'task.updated', actor_user_id: 'other-789', payload: {} }), '');
    });

    expect(result.current.lastEventId).toBeNull();
  });
});

describe('useProjectEventStream — reconnect on error', () => {
  it('fetches a fresh ticket and creates a new EventSource after reconnect', async () => {
    mockTicketResponse({ ticket: 'tkt-first' });
    renderHook(() => useProjectEventStream('proj-1', {}));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { jest.runAllTimers(); });

    expect(apiPostMock).toHaveBeenCalledTimes(1);
    expect(MockEventSource.instances[0].url).toContain('ticket=tkt-first');

    // Queue the SECOND ticket the reconnect will pick up.
    mockTicketResponse({ ticket: 'tkt-second' });

    const es = MockEventSource.instances[0];
    act(() => { es.triggerError(); });

    // Advance past the backoff (1s for first retry).
    await act(async () => { jest.advanceTimersByTime(1_500); });
    // Flush the awaited ticket fetch + the next open() tick.
    await act(async () => { await Promise.resolve(); });
    await act(async () => { jest.runAllTimers(); });

    expect(apiPostMock).toHaveBeenCalledTimes(2);
    expect(MockEventSource.instances.length).toBeGreaterThanOrEqual(2);
    expect(MockEventSource.instances[1].url).toContain('ticket=tkt-second');
  });

  it('calls onGap when reconnect happens after >5s gap', async () => {
    mockTicketResponse();
    const onGap = jest.fn();
    renderHook(() => useProjectEventStream('proj-1', { onGap }));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { jest.runAllTimers(); });

    const es = MockEventSource.instances[0];
    act(() => { es.triggerError(); });

    // Queue the reconnect's ticket fetch BEFORE advancing the timer.
    mockTicketResponse({ ticket: 'tkt-after-gap' });

    // Advance time past the GAP_THRESHOLD_MS (5s).
    await act(async () => { jest.advanceTimersByTime(6_000); });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { jest.runAllTimers(); });

    // The new connection opens, which should detect the gap.
    expect(onGap).toHaveBeenCalledTimes(1);
  });
});
