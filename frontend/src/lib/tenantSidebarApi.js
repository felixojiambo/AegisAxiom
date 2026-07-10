/**
 * Phase 1E.2 — Tenant sidebar config fetcher.
 *
 * Single-purpose helper that calls GET /api/tenant/sidebar-config
 * for the current user. Used by Layout.js when the authenticated
 * user is on a non-root tenant — root tenants render the existing
 * static NAV_GROUPS and never call this.
 */
import { api } from './api';
import API_URL from './apiConfig';

const API = API_URL;

/**
 * Fetches the dynamic sidebar payload for the calling user's tenant.
 *
 * @returns {Promise<{ apps: Array<{ app_id: string, name: string, slug: string, description: string }>, tenant_id: string, tenant_name: string, is_root: boolean }>}
 *   On any non-2xx response or network error, resolves to a safe
 *   empty payload so the caller can render the spine without
 *   special-casing failures. We deliberately don't throw — a
 *   degraded sidebar (spine only) is better than a thrown-error
 *   sidebar.
 */
export async function fetchTenantSidebarConfig() {
  const empty = {
    tenant_id: null,
    tenant_name: null,
    is_root: false,
    apps: [],
  };

  try {
    const res = await api.get(`${API}/api/tenant/sidebar-config`);
    if (!res.ok) {
      if (typeof console !== 'undefined') {
        console.warn(
          `[TenantSidebar] sidebar-config returned HTTP ${res.status}; rendering spine only`,
        );
      }
      return empty;
    }
    const data = await res.json();
    return {
      tenant_id: data?.tenant_id ?? null,
      tenant_name: data?.tenant_name ?? null,
      is_root: data?.is_root === true,
      apps: Array.isArray(data?.apps) ? data.apps : [],
    };
  } catch (err) {
    if (typeof console !== 'undefined') {
      console.warn(
        '[TenantSidebar] sidebar-config network error; rendering spine only',
        err,
      );
    }
    return empty;
  }
}
