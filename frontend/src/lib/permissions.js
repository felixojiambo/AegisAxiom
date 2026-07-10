/**
 * Pure permission-check helpers. No React, no hooks — safe to call
 * from any context (hooks, route matchers, non-component utilities).
 *
 * WHY THIS EXISTS
 * Roles in MongoDB grant `{resource}.view.all` or `{resource}.view.own`
 * (e.g. the director role has `opportunities.view.all`). Earlier frontend
 * gates asked for a bare `{resource}.view` key that never existed in any
 * role seed, so director users were denied pages they should see. These
 * helpers collapse all three conventional forms into a single resource
 * check and are the single source of truth for view/edit gating.
 *
 * TODO (tech debt, not this PR): several page components still use
 * `['admin', 'director'].includes(user.role)` role-string gates
 * (Accounts.js, Invoices.js, FieldServices.js, Contracts.js edit/create
 * buttons). Works today, but any new role (e.g. 'regional_director',
 * 'sales_manager') that should have the same access will silently miss
 * out. Migrate those to canViewResource / canEditResource when touching
 * those files.
 */

const ADMIN_WILDCARD = '*';

function hasWildcard(perms) {
  return Array.isArray(perms) && perms.includes(ADMIN_WILDCARD);
}

/**
 * True if `perms` grants view access to `resource`. Matches any of
 * `${resource}.view`, `${resource}.view.all`, `${resource}.view.own`.
 * Admin wildcard (`*`) short-circuits.
 */
export function canViewResource(perms, resource) {
  if (!Array.isArray(perms)) return false;
  if (hasWildcard(perms)) return true;
  return (
    perms.includes(`${resource}.view`) ||
    perms.includes(`${resource}.view.all`) ||
    perms.includes(`${resource}.view.own`)
  );
}

/**
 * True if `perms` grants edit access to `resource`. Matches any of
 * `${resource}.edit`, `${resource}.edit.all`, `${resource}.edit.own`.
 * Admin wildcard (`*`) short-circuits.
 */
export function canEditResource(perms, resource) {
  if (!Array.isArray(perms)) return false;
  if (hasWildcard(perms)) return true;
  return (
    perms.includes(`${resource}.edit`) ||
    perms.includes(`${resource}.edit.all`) ||
    perms.includes(`${resource}.edit.own`)
  );
}
