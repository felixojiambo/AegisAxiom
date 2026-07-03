import React, { useEffect, useState } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { usePermissions } from '../hooks/usePermissions';
import { AlertCircle, ShieldOff, ArrowLeft, Sparkles } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useIsRootTenant } from '../lib/rootTenant';

/**
 * PermissionRoute - Wrapper component that checks if user has required access.
 *
 * Prefer `resource` for view-gated pages — it collapses `{res}.view`,
 * `{res}.view.all`, and `{res}.view.own` into a single check (see
 * lib/permissions.js).
 *   <PermissionRoute resource="opportunities"><Opps /></PermissionRoute>
 *
 * For non-resource pages (`users.manage`, `chat.access`, etc.) use permission:
 *   <PermissionRoute permission="users.manage"><UserMgmt /></PermissionRoute>
 *   <PermissionRoute permission={['time.view', 'time.entry']}><Time /></PermissionRoute>
 *
 * `resource` and `permission` can be combined — access is granted if EITHER
 * passes. `roles` allows a role-name bypass regardless of perms.
 *
 * ─── Phase 1E.2.1 — Tenant-aware fallback ────────────────────────
 *
 * Previously this component hardcoded /dashboard as the fallback
 * destination when access was denied. That works for SimpliMeta root
 * admins (who have a Dashboard nav item) but strands non-root tenant
 * users on a denial page they can't navigate away from — their
 * sidebar doesn't include Dashboard.
 *
 * The case we're solving: tenant user is mid-session on a granted
 * app, admin revokes the grant, tenant refreshes. The sidebar drops
 * the app correctly, but the URL is still pointing at it and React
 * Router renders this denial. Both buttons (Go Back, Go to Dashboard)
 * fail for the tenant user.
 *
 * The 1E.2.1 fix:
 *   1. Non-root tenants get "Go to AI Employees" instead of
 *      "Go to Dashboard" — AI Employees is the canonical home page
 *      for tenant users (always-on spine item from 1E.2).
 *   2. Non-root tenants auto-redirect to /ai-employees after 5 seconds,
 *      with a visible countdown so they understand what's happening.
 *      They can still click manually or use Go Back during the
 *      countdown.
 *   3. Root admins get the existing behaviour unchanged — no
 *      auto-redirect, "Go to Dashboard" still points at /dashboard.
 */
export default function PermissionRoute({
  children,
  resource,
  permission,
  roles,
  redirectTo = '/dashboard',
}) {
  const { canView, hasPermission, hasAnyPermission, isAdmin, role } = usePermissions();
  const location = useLocation();
  const navigate = useNavigate();
  const isRoot = useIsRootTenant();

  // Admin bypass
  if (isAdmin()) {
    return children;
  }

  // Role-based bypass — if roles prop is provided and user's role matches, allow access
  if (roles) {
    const allowedRoles = Array.isArray(roles) ? roles : [roles];
    if (allowedRoles.includes(role)) {
      return children;
    }
  }

  // No gate specified at all — allow access
  if (!resource && !permission) {
    return children;
  }

  const byResource = resource ? canView(resource) : false;
  const byPermission = permission
    ? Array.isArray(permission)
      ? hasAnyPermission(permission)
      : hasPermission(permission)
    : false;

  const hasAccess = byResource || byPermission;

  // If user doesn't have permission, show access denied page
  if (!hasAccess) {
    // 1E.2.1 — tenant users get a different fallback target. Root
    // admins keep the existing /dashboard target.
    const fallbackPath = isRoot ? redirectTo : '/ai-employees';
    const fallbackLabel = isRoot ? 'Go to Dashboard' : 'Go to AI Employees';

    return (
      <AccessDeniedScreen
        isRoot={isRoot}
        fallbackPath={fallbackPath}
        fallbackLabel={fallbackLabel}
        onBack={() => navigate(-1)}
        onForward={() => navigate(fallbackPath)}
      />
    );
  }

  return children;
}

// ─── Internal: the denial UI, extracted so the auto-redirect timer ──
//    can hook into a useEffect cleanly. The hook can't live inside the
//    parent conditional return above, so we lift it into its own
//    component.

function AccessDeniedScreen({
  isRoot,
  fallbackPath,
  fallbackLabel,
  onBack,
  onForward,
}) {
  const navigate = useNavigate();

  // 1E.2.1 — auto-redirect for non-root tenants. The 5-second window
  // is long enough that a user who wants to act manually (Go Back,
  // contact admin) can do so. The countdown gives the redirect
  // explicit affordance — silent redirects feel like the app is
  // misbehaving, an explicit "redirecting in N seconds" is honest
  // UX. Root admins never auto-redirect; they hit denial pages
  // legitimately (testing roles, etc.) and shouldn't be bounced.
  const AUTO_REDIRECT_SECONDS = 5;
  const [countdown, setCountdown] = useState(
    isRoot ? null : AUTO_REDIRECT_SECONDS,
  );

  useEffect(() => {
    if (isRoot) return;
    if (countdown === null) return;
    if (countdown <= 0) {
      navigate(fallbackPath, { replace: true });
      return;
    }
    const timer = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [countdown, isRoot, fallbackPath, navigate]);

  const ForwardIcon = isRoot ? null : Sparkles;

  return (
    <div className="min-h-screen bg-aegisaxiom-base flex items-center justify-center p-6">
      <div className="max-w-md w-full">
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-2xl p-8 text-center">
          {/* Icon */}
          <div className="mb-6 flex justify-center">
            <div className="w-20 h-20 rounded-full bg-red-50 dark:bg-red-500/10 flex items-center justify-center">
              <ShieldOff size={40} className="text-red-600 dark:text-red-400" />
            </div>
          </div>

          {/* Title */}
          <h1 className="text-2xl font-bold text-aegisaxiom-text mb-3">Access Denied</h1>

          {/* Message — copy varies by user type */}
          <p className="text-aegisaxiom-text-secondary mb-6">
            {isRoot
              ? "You don't have permission to access this page."
              : "This page isn't available for your tenant right now. The app may have been revoked or hasn't been granted yet."}
          </p>

          {/* Alert Box */}
          <div className="bg-amber-50 dark:bg-amber-500/10 border border-amber-500/20 rounded-lg p-4 mb-6">
            <div className="flex items-start gap-3">
              <AlertCircle size={20} className="text-amber-600 dark:text-amber-400 flex-shrink-0 mt-0.5" />
              <div className="text-left">
                <p className="text-sm text-aegisaxiom-text-secondary font-medium mb-1">
                  Contact your administrator
                </p>
                <p className="text-xs text-aegisaxiom-text-secondary">
                  If you believe you should have access to this page, please contact your system
                  administrator to request the necessary permissions.
                </p>
              </div>
            </div>
          </div>

          {/* Auto-redirect countdown — non-root only */}
          {!isRoot && countdown !== null && countdown > 0 && (
            <div className="mb-4 text-xs text-aegisaxiom-text-secondary">
              Redirecting to AI Employees in {countdown} second{countdown === 1 ? '' : 's'}…
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3">
            <button
              onClick={onBack}
              className="flex-1 px-4 py-2.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg transition-colors flex items-center justify-center gap-2"
            >
              <ArrowLeft size={16} />
              Go Back
            </button>
            <button
              onClick={onForward}
              className="flex-1 px-4 py-2.5 bg-violet-600 hover:bg-violet-700 text-white rounded-lg transition-colors flex items-center justify-center gap-2"
            >
              {ForwardIcon && <ForwardIcon size={16} />}
              {fallbackLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
