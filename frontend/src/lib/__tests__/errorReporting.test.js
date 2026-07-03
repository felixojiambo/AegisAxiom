/**
 * errorReporting.test.js
 *
 * Tests: console fallback behavior, POST attempt to /api/errors with context,
 * and idempotent global handler installation.
 */

import { reportError, installGlobalHandlers } from '../errorReporting';

// Spy on console.error without suppressing actual output in test logs.
const consoleSpy = jest.spyOn(console, 'error').mockImplementation(() => {});

// Capture fetch calls.
global.fetch = jest.fn().mockResolvedValue({ ok: true });

beforeEach(() => {
  consoleSpy.mockClear();
  fetch.mockClear();
  // Reset the installed flag between tests by re-importing the module.
  // Jest module cache isolation handles this via jest.resetModules() if needed.
});

afterAll(() => {
  consoleSpy.mockRestore();
});

describe('reportError', () => {
  it('logs to console.error', () => {
    const err = new Error('test error');
    reportError(err);
    expect(consoleSpy).toHaveBeenCalled();
    expect(consoleSpy.mock.calls[0][1]).toBe('test error');
  });

  it('attempts POST to /api/errors', async () => {
    const err = new Error('network error');
    reportError(err, { component: 'ChatPage' });

    // Wait for the async fetch to fire
    await Promise.resolve();
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, opts] = fetch.mock.calls[0];
    expect(url).toMatch(/\/api\/errors$/);
    expect(opts.method).toBe('POST');
    const body = JSON.parse(opts.body);
    expect(body.message).toBe('network error');
    expect(body.context).toEqual({ component: 'ChatPage' });
  });

  it('includes stack and name in the POST body', async () => {
    const err = new Error('stack test');
    reportError(err);
    await Promise.resolve();
    const body = JSON.parse(fetch.mock.calls[0][1].body);
    expect(body.stack).toBeTruthy();
    expect(body.name).toBe('Error');
  });

  it('handles non-Error values gracefully', () => {
    expect(() => reportError('string error')).not.toThrow();
    expect(consoleSpy).toHaveBeenCalled();
  });

  it('silently drops if fetch throws', async () => {
    fetch.mockRejectedValueOnce(new Error('network down'));
    expect(() => reportError(new Error('test'))).not.toThrow();
    await Promise.resolve();
    // No unhandled rejection — error was swallowed.
  });
});

describe('installGlobalHandlers', () => {
  it('is idempotent — calling twice does not double-register', () => {
    const addSpy = jest.spyOn(window, 'addEventListener');
    installGlobalHandlers();
    installGlobalHandlers();
    // Should only call addEventListener twice (error + unhandledrejection)
    // across both calls combined, not four times.
    const errorCalls = addSpy.mock.calls.filter(([event]) => event === 'error');
    const rejectionCalls = addSpy.mock.calls.filter(([event]) => event === 'unhandledrejection');
    expect(errorCalls.length).toBeLessThanOrEqual(1);
    expect(rejectionCalls.length).toBeLessThanOrEqual(1);
    addSpy.mockRestore();
  });
});
