import { useAuth } from '../contexts/AuthContext';
import { canViewResource, canEditResource } from '../lib/permissions';

/**
 * Custom hook for checking user permissions.
 * Supports wildcard (*) permissions and specific permission strings.
 *
 * Reads from `user.effective_permissions` first (the closure-aware list
 * the backend resolves via the implication graph; e.g., a director
 * with `opportunities.edit.all` has `opportunities.view.all` here too).
 * Falls back to raw `user.permissions` for compatibility with payloads
 * from older clients or auth paths that did not populate the closure.
 *
 * The frontend never recomputes the implication graph; it reads what
 * the backend serialized. If `effective_permissions` is missing, the
 * fallback to `permissions` is conservative — gates can still under-grant
 * for trimmed roles, but never over-grant.
 */
export function usePermissions() {
  const { user } = useAuth();

  /**
   * Effective permission list for this user. Computed once per render
   * and reused by every helper below — single source of truth; never
   * reach for `user.permissions` directly outside this resolution.
   * Prefers `user.effective_permissions` (closure-aware) and falls back
   * to raw `user.permissions` for legacy payloads.
   */
  const effectivePerms = Array.isArray(user?.effective_permissions)
    ? user.effective_permissions
    : Array.isArray(user?.permissions)
      ? user.permissions
      : [];

  /**
   * Check if user has a specific permission
   * @param {string} permission - Permission key to check (e.g., "deals.create")
   * @returns {boolean}
   */
  const hasPermission = (permission) => {
    if (effectivePerms.length === 0) return false;
    // Admin wildcard check
    if (effectivePerms.includes('*')) return true;
    // Specific permission check
    return effectivePerms.includes(permission);
  };

  /**
   * Check if user has ANY of the provided permissions
   * @param {string[]} permissions - Array of permission keys
   * @returns {boolean}
   */
  const hasAnyPermission = (permissions) => {
    if (effectivePerms.length === 0) return false;
    if (effectivePerms.includes('*')) return true;
    return permissions.some((perm) => effectivePerms.includes(perm));
  };

  /**
   * Check if user has ALL of the provided permissions
   * @param {string[]} permissions - Array of permission keys
   * @returns {boolean}
   */
  const hasAllPermissions = (permissions) => {
    if (effectivePerms.length === 0) return false;
    if (effectivePerms.includes('*')) return true;
    return permissions.every((perm) => effectivePerms.includes(perm));
  };

  /**
   * Check if user is admin (has wildcard permission)
   * @returns {boolean}
   */
  const isAdmin = () => effectivePerms.includes('*');

  /**
   * Check if user has a specific role
   * @param {string} role - Role name to check
   * @returns {boolean}
   */
  const hasRole = (role) => {
    return user?.role?.toLowerCase() === role.toLowerCase();
  };

  /**
   * Check if user can view a resource. Matches `${resource}.view`,
   * `${resource}.view.all`, or `${resource}.view.own` — the single
   * source of truth for view gating (see lib/permissions.js).
   * @param {string} resource - Resource name (e.g., "opportunities")
   * @returns {boolean}
   */
  const canView = (resource) => canViewResource(effectivePerms, resource);

  /**
   * Check if user can edit a resource. Matches `${resource}.edit`,
   * `${resource}.edit.all`, or `${resource}.edit.own`.
   * @param {string} resource - Resource name
   * @returns {boolean}
   */
  const canEdit = (resource) => canEditResource(effectivePerms, resource);

  return {
    hasPermission,
    hasAnyPermission,
    hasAllPermissions,
    isAdmin,
    hasRole,
    canView,
    canEdit,
    /**
     * Closure-aware permission list (effective_permissions if the
     * backend supplied it, else raw permissions). Use this for display
     * — e.g., "what permissions does this user effectively have?". For
     * gating, prefer the helpers above (hasPermission/canView/etc.).
     */
    permissions: effectivePerms,
    /**
     * Raw permission grants exactly as stored on the user (role +
     * extras, no implication closure applied). Use only when you
     * specifically need the un-resolved grants — e.g., admin UI that
     * renders what was assigned vs what was derived. Most callers
     * should use `permissions` instead.
     */
    rawPermissions: Array.isArray(user?.permissions) ? user.permissions : [],
    role: user?.role || null,
  };
}
