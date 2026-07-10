/**
 * XHR-based API helpers using CLEAN XMLHttpRequest from iframe.
 *
 * Why: emergent-main.js patches both fetch() AND XMLHttpRequest.send(),
 * causing "Illegal invocation" errors. We use an isolated iframe context
 * to get an unpatched XMLHttpRequest.
 */

import { safeXhr } from './safeXhr';

const TOKEN_KEY = 'aegisaxiom_session_token';

function apiRequest(method, url, body, extraHeaders) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers['Authorization'] = `Bearer ${token}`;

  if (extraHeaders) Object.assign(headers, extraHeaders);

  return safeXhr(method, url, {
    headers,
    body: body !== undefined ? JSON.stringify(body) : null,
  })
    .then(({ status, data }) => ({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(data),
      text: () => Promise.resolve(JSON.stringify(data)),
    }))
    .catch((err) => {
      const status = typeof err?.status === 'number' ? err.status : 0;
      const data = err?.data ?? {
        detail: err?.message || 'Network error. Please check your connection.',
      };
      return {
        ok: false,
        status,
        json: () => Promise.resolve(data),
        text: () => Promise.resolve(err?.message || 'Network error'),
      };
    });
}

export function apiFormRequest(method, url, formData) {
  // FormData uploads use a direct XMLHttpRequest (not safeXhr) because
  // FormData objects cannot be sent across iframe boundaries, which is
  // how safeXhr works around patched XHR in some environments.
  const token = localStorage.getItem(TOKEN_KEY);

  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url, true);
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    // Do NOT set Content-Type — browser adds multipart boundary automatically

    xhr.onload = () => {
      let data;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        data = {};
      }
      resolve({
        ok: xhr.status >= 200 && xhr.status < 300,
        status: xhr.status,
        json: () => Promise.resolve(data),
        text: () => Promise.resolve(JSON.stringify(data)),
      });
    };
    xhr.onerror = () => {
      resolve({
        ok: false,
        status: 0,
        json: () => Promise.resolve({ detail: 'Network error. Please check your connection.' }),
        text: () => Promise.resolve('Network error'),
      });
    };
    xhr.send(formData);
  });
}

/**
 * Download a file from an API endpoint. Uses a raw XHR with responseType=blob
 * because safeXhr only returns parsed JSON (no .blob() support).
 * Returns a Blob on success, null on failure.
 */
export function apiDownload(url) {
  const token = localStorage.getItem(TOKEN_KEY);
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open('GET', url, true);
    xhr.responseType = 'blob';
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response);
      } else {
        resolve(null);
      }
    };
    xhr.onerror = () => resolve(null);
    xhr.send();
  });
}

/**
 * Rich variant of apiDownload — returns headers (for Content-Disposition
 * filename parsing) and the status code. Used by per-document export
 * buttons that need to either save with the server-suggested filename
 * or read a JSON error body if the request failed.
 */
export function apiDownloadRich(url) {
  const token = localStorage.getItem(TOKEN_KEY);
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open('GET', url, true);
    xhr.responseType = 'blob';
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.onload = () => {
      const ok = xhr.status >= 200 && xhr.status < 300;
      resolve({
        ok,
        status: xhr.status,
        blob: ok ? xhr.response : null,
        errorBlob: ok ? null : xhr.response,
        getHeader: (name) => xhr.getResponseHeader(name),
      });
    };
    xhr.onerror = () => resolve({ ok: false, status: 0, blob: null, errorBlob: null, getHeader: () => null });
    xhr.send();
  });
}

export const api = {
  get: (url) => apiRequest('GET', url),
  post: (url, body) => apiRequest('POST', url, body ?? {}),
  put: (url, body) => apiRequest('PUT', url, body ?? {}),
  delete: (url) => apiRequest('DELETE', url),
  patch: (url, body) => apiRequest('PATCH', url, body ?? {}),
  /**
   * PATCH with an optional If-Match header for optimistic concurrency.
   * Pass the task's current `updated_at` value as `ifMatch`.
   * On 409 the caller receives the raw response (status 409, body has
   * `{detail, current_updated_at, current_doc}`) and must handle it.
   */
  patchConditional: (url, body, ifMatch) =>
    apiRequest('PATCH', url, body ?? {}, ifMatch ? { 'If-Match': ifMatch } : undefined),
  postForm: (url, formData) => apiFormRequest('POST', url, formData),
  download: (url) => apiDownload(url),
};

/** Extract array from paginated response {items, total} or return raw array */
export const unwrap = (data) => (Array.isArray(data) ? data : data?.items || []);