/**
 * Phase 1D.9 — SimpliMeta root-tenant detection.
 *
 * The "root" tenant is the SimpliMeta-internal tenant
 * (`ten_simplimeta_root` in the backend, see `seeds/seed_tenants.py`).
 * Some UI elements — hardcoded mock/showcase AI Employee cards, demo
 * stat tiles — are meaningful for SimpliMeta-internal users as
 * walkthrough content, but actively confusing for partner/account
 * tenants who see them next to their real instances.
 *
 * This module provides a single source of truth for that check so we
 * don't sprinkle string comparisons through the codebase. If the
 * constant ever changes (it shouldn't), this is the one file to edit.
 *
 * Mirrors `backend/seeds/seed_tenants.py::SIMPLIMETA_ROOT_TENANT_ID`.
 */

import { useAuth } from '../contexts/AuthContext';

export const SIMPLIMETA_ROOT_TENANT_ID = 'ten_simplimeta_root';

/**
 * True iff the user belongs to the SimpliMeta root tenant.
 *
 * @param {{ tenant_id?: string } | null | undefined} user
 * @returns {boolean}
 */
export function isRootTenantUser(user) {
  return !!user && user.tenant_id === SIMPLIMETA_ROOT_TENANT_ID;
}

/**
 * React hook — returns true iff the current authenticated user is on
 * the SimpliMeta root tenant. Returns false during auth loading or
 * when logged out, so callers can use it directly as a render gate
 * without an extra loading branch (the gated content will simply not
 * appear until auth resolves; for non-root tenants it never appears).
 *
 * @returns {boolean}
 */
export function useIsRootTenant() {
  const { user } = useAuth();
  return isRootTenantUser(user);
}
