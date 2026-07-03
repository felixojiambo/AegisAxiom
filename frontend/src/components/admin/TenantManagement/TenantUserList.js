import React from 'react';
import { User, Mail, ShieldCheck } from 'lucide-react';

/**
 * Phase 1D.8 — Tenant user list presentation.
 *
 * Read-only display of users belonging to one tenant. Status pill is
 * derived client-side from is_active + has_password (per the backend
 * endpoint's contract). Three possible states:
 *
 *   • invited  — invite sent, password not yet set (is_active=false,
 *                has_password=false)
 *   • active   — accepted invite, password set, logging in normally
 *                (is_active=true, has_password=true)
 *   • inactive — was deactivated by an admin (is_active=false, but
 *                has_password=true, meaning they previously accepted)
 *
 * Write actions (resend invite, deactivate, change role) are NOT in
 * this list per the 1D.8 scope decision — link to the existing
 * UserManagement page if those are needed. Keeps the surface tight
 * and reduces the security model footprint of this view.
 *
 * Props
 *   users   — User[] with fields: user_id, email, name, role,
 *             is_active, is_email_verified, has_password,
 *             created_at, last_login_at?
 */
export default function TenantUserList({ users }) {
  return (
    <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl overflow-hidden">
      {/* Desktop table — md+ */}
      <div className="hidden md:block overflow-x-auto">
        <table className="w-full text-sm" data-testid="users-table">
          <thead>
            <tr className="border-b border-aegisaxiom-border">
              {['User', 'Role', 'Status', 'Joined'].map((h) => (
                <th
                  key={h}
                  className="text-left py-3 px-4 text-xs font-medium text-aegisaxiom-text-muted uppercase tracking-wider"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const status = deriveStatus(u);
              return (
                <tr
                  key={u.user_id}
                  className="border-b border-aegisaxiom-border last:border-b-0 transition-colors"
                  data-testid={`user-row-${u.user_id}`}
                >
                  <td className="py-3 px-4">
                    <UserCell user={u} />
                  </td>
                  <td className="py-3 px-4">
                    <RolePill role={u.role} />
                  </td>
                  <td className="py-3 px-4">
                    <StatusPill status={status} />
                  </td>
                  <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                    {formatDate(u.created_at)}
                    {u.last_login_at && (
                      <div className="text-[11px] text-aegisaxiom-text-muted mt-0.5">
                        Last seen {formatDate(u.last_login_at)}
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Mobile cards — below md */}
      <div className="md:hidden p-3 space-y-2.5">
        {users.map((u) => {
          const status = deriveStatus(u);
          return (
            <div
              key={u.user_id}
              className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5"
              data-testid={`user-card-${u.user_id}`}
            >
              <div className="flex items-center gap-3 mb-3">
                <UserCell user={u} />
                <StatusPill status={status} className="ml-auto shrink-0" />
              </div>
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <div className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                    Role
                  </div>
                  <RolePill role={u.role} />
                </div>
                <div>
                  <div className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                    Joined
                  </div>
                  <div className="text-aegisaxiom-text-secondary">
                    {formatDate(u.created_at)}
                    {u.last_login_at && (
                      <div className="text-[10px] text-aegisaxiom-text-muted mt-0.5">
                        Last seen {formatDate(u.last_login_at)}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Internal helpers ───────────────────────────────────────────────

/**
 * Map the boolean fields the backend returns to a single status string
 * the UI renders. Centralised here so the rules are visible in one
 * place rather than scattered in JSX.
 */
function deriveStatus(user) {
  if (user.is_active && user.has_password) return 'active';
  if (!user.is_active && !user.has_password) return 'invited';
  // Was active at some point (has_password true) but is_active false now.
  return 'inactive';
}

function UserCell({ user }) {
  const initials = (user.name || user.email || '?')
    .split(/\s+/)
    .map((s) => s.charAt(0))
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase();

  return (
    <div className="flex items-center gap-3 min-w-0">
      <div className="w-8 h-8 rounded-full bg-violet-100 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 flex items-center justify-center shrink-0 text-xs font-semibold">
        {initials || <User size={14} />}
      </div>
      <div className="min-w-0">
        <div className="font-medium text-aegisaxiom-text truncate">{user.name || '—'}</div>
        <div className="text-[11px] text-aegisaxiom-text-muted truncate flex items-center gap-1 mt-0.5">
          <Mail size={10} />
          {user.email}
        </div>
      </div>
    </div>
  );
}

function RolePill({ role }) {
  // Friendlier display labels for the roles we expect to see most often.
  const label =
    {
      tenant_admin: 'Tenant Admin',
      admin: 'Admin',
      contractor: 'Contractor',
      client: 'Client',
    }[role] || role;

  const isAdminish = role === 'admin' || role === 'tenant_admin';

  return (
    <span
      className={
        'inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full ' +
        (isAdminish
          ? 'bg-violet-100 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300'
          : 'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary')
      }
    >
      {isAdminish && <ShieldCheck size={10} />}
      {label}
    </span>
  );
}

const STATUS_PILL_CLASS = {
  active:
    'bg-emerald-50 dark:bg-emerald-600/10 text-emerald-700 dark:text-emerald-300',
  invited:
    'bg-amber-50 dark:bg-amber-600/10 text-amber-700 dark:text-amber-300',
  inactive:
    'bg-aegisaxiom-elevated text-aegisaxiom-text-muted',
};

const STATUS_LABEL = {
  active: 'Active',
  invited: 'Invited',
  inactive: 'Inactive',
};

function StatusPill({ status, className = '' }) {
  return (
    <span
      className={
        'inline-flex text-xs px-2 py-0.5 rounded-full w-fit ' +
        (STATUS_PILL_CLASS[status] || '') +
        (className ? ' ' + className : '')
      }
    >
      {STATUS_LABEL[status] || status}
    </span>
  );
}

const dateFmt = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
});

function formatDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return dateFmt.format(d);
}
