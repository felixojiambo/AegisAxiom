import { canViewResource } from './permissions';

/**
 * Permission/role-to-route mapping, ordered by priority.
 * Each entry: { path, resource?, permissions?, roles? }
 *   - resource: resource name — matches via canViewResource, which
 *     accepts `${resource}.view`, `.view.all`, or `.view.own`
 *   - permissions: literal permission keys (any match) — use when no
 *     resource convention applies (e.g. 'dashboard.main', 'chat.access')
 *   - roles: role names that grant access regardless of permissions
 * Mirrors the nav items in Layout.js — keep in sync.
 */
const ROUTE_MAP = [
  // Dashboard routes
  { path: '/dashboard', permissions: ['dashboard.main'], roles: ['admin', 'director', 'finance'] },
  { path: '/sales-rep-dashboard', permissions: ['dashboard.sales_rep'], roles: ['sales_rep'] },
  { path: '/my-dashboard', permissions: ['dashboard.agent'], roles: ['agent'] },
  { path: '/contractor-dashboard', permissions: ['dashboard.contractor'], roles: ['contractor'] },
  { path: '/finance', permissions: ['finance.portal'], roles: ['finance'] },
  // Feature pages
  { path: '/deals', resource: 'deals' },
  { path: '/opportunities', resource: 'opportunities' },
  { path: '/ai-employees', permissions: ['ai_employees.view'] },
  { path: '/contacts', resource: 'contacts' },
  { path: '/accounts', resource: 'accounts' },
  { path: '/commissions', resource: 'commissions' },
  { path: '/contracts', resource: 'contracts' },
  { path: '/projects', resource: 'projects' },
  { path: '/time-management', resource: 'time', permissions: ['time.entry'] },
  { path: '/invoices', resource: 'invoices' },
  { path: '/field-jobs', resource: 'field_jobs' },
  { path: '/chat', permissions: ['chat.access'] },
];

/**
 * Determine the best home route for a user based on their permissions and role.
 * Accepts either a role string (legacy) or a user object
 * { role, permissions, effective_permissions }.
 *
 * Prefers `effective_permissions` (the closure-aware list resolved by the
 * backend's implication graph) so a director landing here is matched
 * against routes their `*.edit.all` perms imply view access to. Falls
 * back to `permissions` for older payloads or callers that pass a
 * partial user object.
 */
export default function getRoleHome(roleOrUser) {
  const role = typeof roleOrUser === 'string' ? roleOrUser : roleOrUser?.role;
  const perms = typeof roleOrUser === 'object'
    ? roleOrUser?.effective_permissions || roleOrUser?.permissions || []
    : [];
  const isAdmin = perms.includes('*');

  // chat_only is a special case — always goes to chat
  if (role === 'chat_only') return '/chat';

  // Admin wildcard — go to main dashboard
  if (isAdmin) return '/dashboard';

  // Find the first route the user has access to (by resource, permission, or role)
  if (perms.length > 0) {
    for (const route of ROUTE_MAP) {
      const byResource = route.resource && canViewResource(perms, route.resource);
      const byPermission =
        route.permissions?.length > 0 && route.permissions.some((p) => perms.includes(p));
      const byRole = route.roles?.includes(role);
      if (byResource || byPermission || byRole) return route.path;
    }
  }

  // Legacy fallback for users without permissions array (role-name only)
  if (role === 'finance') return '/finance';
  if (role === 'contractor') return '/contractor-dashboard';
  if (role === 'sales_rep') return '/sales-rep-dashboard';
  if (role === 'agent') return '/my-dashboard';

  // Ultimate fallback
  return '/dashboard';
}
