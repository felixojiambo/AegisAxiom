/**
 * Safe HTTP client that works in BOTH preview and production.
 *
 * Problem: In preview, emergent-main.js patches XMLHttpRequest.send() causing
 * "Illegal invocation" errors. In production, that script isn't loaded but
 * the iframe fallback may be blocked by CSP or other security policies.
 *
 * Solution: Try multiple approaches in order of reliability:
 * 1. Saved originals from index.html (fastest, works if saved before patching)
 * 2. Normal XMLHttpRequest (works in production where nothing is patched)
 * 3. iframe-based clean XHR (works in preview where XHR is patched)
 */

let _resolvedXHR = null;

function resolveCleanXHR() {
  if (_resolvedXHR) return _resolvedXHR;

  // Strategy 1: Use saved originals from index.html
  if (
    window.__OriginalXHR &&
    window.__xhrOpen &&
    window.__xhrSend &&
    window.__xhrSetRequestHeader
  ) {
    _resolvedXHR = {
      create: () => new window.__OriginalXHR(),
      open: window.__xhrOpen,
      send: window.__xhrSend,
      setRequestHeader: window.__xhrSetRequestHeader,
    };
    // Quick smoke test
    try {
      const test = _resolvedXHR.create();
      _resolvedXHR.open.call(test, 'GET', '/', true);
      // If open didn't throw, this is likely good
      return _resolvedXHR;
    } catch {
      _resolvedXHR = null;
    }
  }

  // Strategy 2: Try normal XHR (works in production where nothing is patched)
  try {
    const test = new XMLHttpRequest();
    const origOpen = XMLHttpRequest.prototype.open;
    const origSend = XMLHttpRequest.prototype.send;
    const origSetHeader = XMLHttpRequest.prototype.setRequestHeader;
    origOpen.call(test, 'GET', '/', true);
    // Try a dry-run send to check for "Illegal invocation"
    // We can't actually send without side effects, so just validate the binding
    if (typeof origSend === 'function') {
      _resolvedXHR = {
        create: () => new XMLHttpRequest(),
        open: origOpen,
        send: origSend,
        setRequestHeader: origSetHeader,
      };
      return _resolvedXHR;
    }
  } catch {
    // XHR is patched, try iframe
  }

  // Strategy 3: iframe-based clean XHR (production fallback blocked? try anyway)
  try {
    const iframe = document.createElement('iframe');
    iframe.style.display = 'none';
    // Don't set sandbox - it can restrict XHR in some environments
    document.body.appendChild(iframe);
    const w = iframe.contentWindow;
    if (w && w.XMLHttpRequest) {
      _resolvedXHR = {
        create: () => new w.XMLHttpRequest(),
        open: w.XMLHttpRequest.prototype.open,
        send: w.XMLHttpRequest.prototype.send,
        setRequestHeader: w.XMLHttpRequest.prototype.setRequestHeader,
      };
      return _resolvedXHR;
    }
  } catch {
    // iframe blocked
  }

  // Last resort: use whatever XMLHttpRequest we have
  _resolvedXHR = {
    create: () => new XMLHttpRequest(),
    open: XMLHttpRequest.prototype.open,
    send: XMLHttpRequest.prototype.send,
    setRequestHeader: XMLHttpRequest.prototype.setRequestHeader,
  };
  return _resolvedXHR;
}

/**
 * Extract a human-readable error message from a backend error response.
 * Handles both plain strings and Pydantic 422 validation error arrays.
 * Used internally by safeXhr.
 */
function extractDetail(data, status) {
  if (!data || !data.detail) return `Request failed (${status})`;
  if (typeof data.detail === 'string') return data.detail;
  if (data.detail?.message && typeof data.detail.message === 'string') return data.detail.message;
  if (Array.isArray(data.detail)) {
    return data.detail.map((e) => e.msg || JSON.stringify(e)).join('; ');
  }
  return `Request failed (${status})`;
}

/**
 * Extract a human-readable error detail from a failed fetch Response.
 *
 * Handles all backend error shapes:
 *   1. `{ detail: "string" }` — HTTPException string detail
 *   2. `{ detail: { message: "string" } }` — structured validator detail (422)
 *   3. `{ detail: [{ msg: "..." }] }` — Pydantic 422 field-level array
 *
 * @param {Response} res — the failed fetch Response.
 * @param {string} fallback — message to use if parsing fails.
 * @returns {Promise<string>}
 */
export async function extractTaskErrorMessage(res, fallback) {
  try {
    const data = await res.clone().json();
    if (typeof data?.detail === 'string') return data.detail;
    if (data?.detail?.message && typeof data.detail.message === 'string') return data.detail.message;
    if (Array.isArray(data?.detail)) {
      const msg = data.detail.map((e) => e?.msg).filter(Boolean).join('; ');
      if (msg) return msg;
    }
  } catch {
    // ignore — fall through to fallback
  }
  return fallback;
}

/**
 * Make a safe HTTP request that works in both preview and production.
 */
export function safeXhr(method, url, options = {}) {
  const { headers = {}, body = null } = options;
  return new Promise((resolve, reject) => {
    const clean = resolveCleanXHR();
    let xhr;
    try {
      xhr = clean.create();
      clean.open.call(xhr, method, url, true);
      for (const [k, v] of Object.entries(headers)) {
        clean.setRequestHeader.call(xhr, k, v);
      }
    } catch (e) {
      // If creation/setup fails, reset and try one more time with a fresh resolution
      _resolvedXHR = null;
      try {
        const fresh = resolveCleanXHR();
        xhr = fresh.create();
        fresh.open.call(xhr, method, url, true);
        for (const [k, v] of Object.entries(headers)) {
          fresh.setRequestHeader.call(xhr, k, v);
        }
      } catch (e2) {
        reject(new Error('Network error. Please try again.'));
        return;
      }
    }

    xhr.onload = () => {
      let data;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        data = {};
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve({ status: xhr.status, data });
      } else {
        const err = new Error(extractDetail(data, xhr.status));
        err.status = xhr.status;
        err.data = data;
        reject(err);
      }
    };
    xhr.onerror = () => {
      // If onerror fires, the strategy might be wrong. Reset for next call.
      _resolvedXHR = null;
      const err = new Error('Network error. Please try again.');
      err.status = 0;
      reject(err);
    };

    try {
      const clean2 = resolveCleanXHR();
      clean2.send.call(xhr, body);
    } catch {
      // send failed - likely "Illegal invocation" from patched XHR
      // Reset and try iframe approach
      _resolvedXHR = null;
      try {
        // Create completely new request via iframe
        const iframe = document.createElement('iframe');
        iframe.style.display = 'none';
        document.body.appendChild(iframe);
        const w = iframe.contentWindow;
        const xhr2 = new w.XMLHttpRequest();
        w.XMLHttpRequest.prototype.open.call(xhr2, method, url, true);
        for (const [k, v] of Object.entries(headers)) {
          w.XMLHttpRequest.prototype.setRequestHeader.call(xhr2, k, v);
        }
        xhr2.onload = () => {
          let data;
          try {
            data = JSON.parse(xhr2.responseText);
          } catch {
            data = {};
          }
          if (xhr2.status >= 200 && xhr2.status < 300) {
            resolve({ status: xhr2.status, data });
          } else {
            const err = new Error(extractDetail(data, xhr2.status));
            err.status = xhr2.status;
            err.data = data;
            reject(err);
          }
          try {
            document.body.removeChild(iframe);
          } catch {}
        };
        xhr2.onerror = () => {
          const err = new Error('Network error. Please try again.');
          err.status = 0;
          reject(err);
          try {
            document.body.removeChild(iframe);
          } catch {}
        };
        w.XMLHttpRequest.prototype.send.call(xhr2, body);
      } catch {
        reject(new Error('Network error. Please try again.'));
      }
    }
  });
}

export function safePost(url, bodyObj) {
  return safeXhr('POST', url, {
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(bodyObj),
  });
}

export function safeGet(url, token) {
  const headers = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return safeXhr('GET', url, { headers });
}
