/**
 * errorReporting.js — Frontend error reporting wrapper.
 *
 * Strategy: Option A (backend relay).
 * Frontend POSTs to /api/errors; the backend enriches with user identity and
 * forwards to GCP Error Reporting. This keeps API keys server-side and lets
 * the backend add session/tenant context.
 *
 * NOTE: The /api/errors backend endpoint is a v1.3 follow-up and does NOT
 * exist yet. Until it does, errors are logged to the console and the POST
 * attempt fails silently. No user-facing impact.
 */

import API_URL from '@/lib/apiConfig';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildPayload(error, context = {}) {
  const err = error instanceof Error ? error : new Error(String(error));
  // Backend /api/errors enforces min_length=1 on `message`. Empty strings
  // come back as 422. Cross-origin "Script error." events, Promise.reject()
  // with no value, and non-Error throws can all produce an empty message
  // — fall back to the error name (or a sentinel) so the report still posts.
  const message = err.message || err.name || 'Unknown error';
  return {
    message,
    stack: err.stack ?? null,
    name: err.name,
    url: window.location.href,
    userAgent: navigator.userAgent,
    timestamp: new Date().toISOString(),
    context,
  };
}

async function tryPost(payload) {
  try {
    // /api/errors requires a logged-in user; mirror the auth approach used
    // by lib/api.js (Bearer token from localStorage). credentials:'include'
    // is added so cookie-based sessions (HttpOnly session_token) also work.
    const headers = { 'Content-Type': 'application/json' };
    try {
      const token = localStorage.getItem('aegisaxiom_session_token');
      if (token) headers['Authorization'] = `Bearer ${token}`;
    } catch { /* localStorage may be unavailable in strict modes */ }
    await fetch(`${API_URL}/api/errors`, {
      method: 'POST',
      credentials: 'include',
      headers,
      body: JSON.stringify(payload),
      // keepalive lets the request outlive page unload events
      keepalive: true,
    });
  } catch {
    // Silently drop — endpoint may not exist yet (v1.3 follow-up)
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Report an error to GCP Error Reporting (via backend relay).
 *
 * @param {Error|unknown} error   — The error to report
 * @param {object}        context — Additional key/value context (e.g. { component, action })
 */
export function reportError(error, context = {}) {
  const payload = buildPayload(error, context);

  // Always log locally so devtools still show the error in dev.
  // eslint-disable-next-line no-console
  console.error('[ErrorReporting]', payload.message, error);

  tryPost(payload);
}

// ---------------------------------------------------------------------------
// Global handlers — call installGlobalHandlers() once from index.js
// ---------------------------------------------------------------------------

let installed = false;

/**
 * Wire window-level error + unhandledrejection handlers.
 * Safe to call multiple times (idempotent).
 */
export function installGlobalHandlers() {
  if (installed) return;
  installed = true;

  window.addEventListener('error', (event) => {
    // Skip chunk-load errors — those are handled by the chunk-reload guard.
    const msg = event?.message ?? '';
    if (/Loading (chunk|CSS chunk) \d+ failed/i.test(msg)) return;

    reportError(event.error ?? new Error(msg), {
      source: 'window.onerror',
      filename: event.filename,
      lineno: event.lineno,
      colno: event.colno,
    });
  });

  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    const msg = reason?.message ?? String(reason ?? '');
    if (/Loading (chunk|CSS chunk) \d+ failed/i.test(msg)) return;

    reportError(reason instanceof Error ? reason : new Error(msg), {
      source: 'unhandledrejection',
    });
  });
}
