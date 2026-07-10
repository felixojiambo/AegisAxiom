/**
 * savedViewsApi — thin wrappers over the /api/projects/saved-views endpoints.
 *
 * All calls return parsed JSON on success; throw on network or HTTP error.
 * Design doc: .waves/projects-v1.2-smart-views-design.md §2.4
 */

import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';

const BASE = `${API_URL}/api/projects/saved-views`;

/**
 * List saved views for the current user.
 *
 * @param {Object} [params]
 * @param {string} [params.view_type]  — 'board'|'backlog'|'list'|'myday'
 * @param {string} [params.scope]      — 'global'|'project'
 * @param {string} [params.project_id]
 * @returns {Promise<{items: Array, total: number}>}
 */
export async function listSavedViews(params = {}) {
  const qs = new URLSearchParams();
  if (params.view_type)  qs.set('view_type', params.view_type);
  if (params.scope)      qs.set('scope', params.scope);
  if (params.project_id) qs.set('project_id', params.project_id);
  const url = qs.toString() ? `${BASE}?${qs}` : BASE;
  const res = await api.get(url);
  if (!res.ok) throw new Error(`Failed to fetch saved views: ${res.status}`);
  return res.json();
}

/**
 * Create a saved view.
 *
 * @param {Object} body
 * @param {string} body.name        — 1-80 chars
 * @param {string} body.scope       — 'global'|'project'
 * @param {string|null} [body.project_id]
 * @param {string} body.view_type   — 'board'|'backlog'|'list'|'myday'
 * @param {string[]} body.pills     — pill ids
 * @param {boolean} [body.is_default]
 * @returns {Promise<Object>}       saved view doc
 */
export async function createSavedView(body) {
  const res = await api.post(BASE, body);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to create saved view: ${res.status}`);
  }
  return res.json();
}

/**
 * Partial-update a saved view.
 *
 * @param {string} savedViewId
 * @param {Object} patch  — may contain name, pills, is_default
 * @returns {Promise<Object>}
 */
export async function updateSavedView(savedViewId, patch) {
  const res = await api.patch(`${BASE}/${savedViewId}`, patch);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to update saved view: ${res.status}`);
  }
  return res.json();
}

/**
 * Delete a saved view.
 *
 * @param {string} savedViewId
 * @returns {Promise<void>}
 */
export async function deleteSavedView(savedViewId) {
  const res = await api.delete(`${BASE}/${savedViewId}`);
  if (!res.ok && res.status !== 204) {
    throw new Error(`Failed to delete saved view: ${res.status}`);
  }
}
