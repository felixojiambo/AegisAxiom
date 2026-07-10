/**
 * Endpoint wrappers for the Phase 1A / 1B / 1C tenant admin API surface.
 *
 * Centralised so we have one place to evolve when batch / count-include
 * endpoints land. All callers should import from here, not hit fetch
 * directly. Each wrapper returns the standard `{ ok, status, json() }`
 * shape from lib/api.js.
 *
 * Endpoint reference: see PHASE_1D_DESIGN.md §1.
 */

import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';

const API = API_URL;

// Build a query string from an object. Skip null/undefined/empty values
// so callers can pass partial filter objects cleanly.
function qs(params) {
  if (!params) return '';
  const pairs = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === '') continue;
    pairs.push(`${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
  }
  return pairs.length ? `?${pairs.join('&')}` : '';
}

// ─── Tenants (Phase 1A) ────────────────────────────────────────────────

/**
 * List tenants. Filters: tenant_type, status, parent_tenant_id.
 * Returns { tenants: [...], count }.
 */
export const listTenants = (filters) =>
  api.get(`${API}/api/tenants${qs(filters)}`);

/** Returns { tenant, ancestry: [self → root] } for the current user. */
export const getMyTenant = () => api.get(`${API}/api/tenants/me`);

export const getTenant = (tenantId) =>
  api.get(`${API}/api/tenants/${tenantId}`);

/** Body: { name, slug, tenant_type, parent_tenant_id }. */
export const createTenant = (data) =>
  api.post(`${API}/api/tenants`, data);

/** Body: subset of { name, slug, tenant_type, parent_tenant_id, status }. */
export const updateTenant = (tenantId, patch) =>
  api.patch(`${API}/api/tenants/${tenantId}`, patch);

/** Soft archive. 409 if active children exist. */
export const archiveTenant = (tenantId) =>
  api.delete(`${API}/api/tenants/${tenantId}`);

// ─── AI Employee Definitions (Phase 1B.5) ──────────────────────────────

export const listDefinitions = (type) =>
  api.get(`${API}/api/ai-employee-definitions${qs({ type })}`);

export const getDefinition = (definitionId) =>
  api.get(`${API}/api/ai-employee-definitions/${definitionId}`);

// ─── Assignments (Phase 1B.5 / 1C.3 / 1C.4) ────────────────────────────

export const listAssignments = (tenantId, filters) =>
  api.get(`${API}/api/tenants/${tenantId}/assignments${qs(filters)}`);

/** Body: { definition_id, tenant_id, max_instances?, expires_at?, notes? }. */
export const grantAssignment = (tenantId, data) =>
  api.post(`${API}/api/tenants/${tenantId}/assignments`, data);

/**
 * Body: subset of { status, max_instances, expires_at, notes }.
 * Revoke (status='revoked') may return 409 with body
 *   { detail: { message, blocking_sub_assignments: [...] } }
 * — the 1C.4 chain check. Surface this in RevokeConflictModal.
 */
export const updateAssignment = (tenantId, assignmentId, patch) =>
  api.patch(
    `${API}/api/tenants/${tenantId}/assignments/${assignmentId}`,
    patch,
  );

// ─── Instances (Phase 1B.5 / 1C.3) ─────────────────────────────────────

/**
 * Body: optional override dict (name, system_prompt, model_provider, etc.).
 * Returns the new ai_employees row. 400 if assignment not active; 409 if
 * max_instances cap hit.
 */
export const provisionInstance = (tenantId, assignmentId, overrides) =>
  api.post(
    `${API}/api/tenants/${tenantId}/assignments/${assignmentId}/instances`,
    overrides || {},
  );

/**
 * Legacy endpoint for client-side join — see PHASE_1D_DESIGN.md §1
 * "Honest gaps". Used to count instances per assignment until the
 * backlog'd include_instance_counts flag lands.
 */
/**
 * List AI Employee INSTANCES belonging to a specific tenant.
 *
 * Uses the target-scoped `/api/tenants/{id}/instances` endpoint (1D.6),
 * NOT the caller-scoped `/api/ai-employees` one — that older endpoint
 * has a strict Phase 1B.3 tenant filter that rejects cross-tenant reads
 * even for root admins, which would silently return zero instances when
 * a SimpliMeta admin views a partner/account tenant's detail page.
 */
export const listInstancesForTenant = (tenantId) =>
  api.get(`${API}/api/tenants/${tenantId}/instances`);

/**
 * Delete a single AI Employee INSTANCE belonging to a tenant.
 *
 * Uses the target-scoped DELETE `/api/tenants/{id}/instances/{eid}`
 * endpoint (1D.7), symmetric to the list endpoint above. Same reason
 * the caller-scoped DELETE `/api/ai-employees/{id}` won't work for
 * SimpliMeta admins managing partner/account tenants.
 *
 * Frees one slot against the parent assignment's max_instances cap.
 */
export const deleteInstance = (tenantId, employeeId) =>
  api.delete(`${API}/api/tenants/${tenantId}/instances/${employeeId}`);

// ─── Tenant Users (Phase 1D.8) ─────────────────────────────────────

/**
 * List users belonging to a specific tenant.
 *
 * Uses the target-scoped GET `/api/tenants/{id}/users` endpoint (1D.8).
 * Returns user records with status fields derived for the Users tab,
 * with password_hash and invite_token explicitly stripped server-side.
 * Bare array response.
 */
export const listTenantUsers = (tenantId) =>
  api.get(`${API}/api/tenants/${tenantId}/users`);

/**
 * Invite a user INTO a specific tenant.
 *
 * Wraps the existing POST `/api/admin/invite` endpoint, adding the
 * `target_tenant_id` field that 1D.8 extended the schema with. The
 * generic invite flow (UserManagement.js) doesn't pass it and gets
 * the legacy inherit-from-caller behavior.
 *
 * data: { email, name, role }
 */
export const inviteUserToTenant = (tenantId, data) =>
  api.post(`${API}/api/admin/invite`, { ...data, target_tenant_id: tenantId });

// ─── Tenant App Grants (Phase 1E.1) ────────────────────────────────

/**
 * List apps eligible to be granted to a tenant.
 *
 * Returns app_registry rows filtered to status='active' server-side.
 * Pending / failed / archived apps are not surfaced. Root-only in
 * 1E.1; opens up to the rest of the team in the final 1E step.
 */
export const listGrantableApps = () =>
  api.get(`${API}/api/tenant-app-grants/catalog`);

/**
 * List all app grants on a tenant. Optional filters: { status }.
 * Root-only in 1E.1.
 */
export const listAppGrants = (tenantId, filters) =>
  api.get(`${API}/api/tenants/${tenantId}/app-grants${qs(filters)}`);

/** Body: { app_id, tenant_id, expires_at?, notes? }. */
export const grantApp = (tenantId, data) =>
  api.post(`${API}/api/tenants/${tenantId}/app-grants`, data);

/**
 * Body: subset of { status, expires_at, notes }.
 * Revoke uses { status: 'revoked' }; reactivate uses { status: 'active' }.
 */
export const updateAppGrant = (tenantId, grantId, patch) =>
  api.patch(
    `${API}/api/tenants/${tenantId}/app-grants/${grantId}`,
    patch,
  );
