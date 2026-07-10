import React, { useState, useEffect, useCallback } from 'react';
import { X, AlertCircle, UserPlus, Mail } from 'lucide-react';
import { toast } from 'sonner';
import { inviteUserToTenant } from './api';

/**
 * Phase 1D.8 — Invite User modal, scoped to a specific tenant.
 *
 * Wraps the extended POST /api/admin/invite endpoint. The backend
 * stamps the new user with `tenant_id = tenantId` once we pass
 * `target_tenant_id`. The user receives an invite email via the
 * existing `send_invite_email` flow, clicks the link, lands on the
 * existing /accept-invite page, and sets a password. On their next
 * login they're scoped to this tenant by the JWT.
 *
 * For 1D.8 the role picker offers `tenant_admin` only. Rationale:
 * tighter security model, matches the role's stated purpose
 * ("administers a single tenant"), and any future role additions
 * (tenant_member, tenant_viewer) are additive. The UI can expand
 * the picker later without behavioral changes elsewhere.
 *
 * Validation:
 *   • email: required, format-validated, not already in use (server)
 *   • name: required
 *   • role: required (only one option in v1, but kept as a select for
 *     forward-compat)
 *
 * Server errors handled inline:
 *   • 400 — "Email already exists" or "archived tenant"
 *   • 403 — permission denied (shouldn't happen for root admin, but
 *     surface it cleanly if it does)
 *   • 404 — tenant no longer exists
 *   • 429 — rate limit (the backend caps at 10/hour on the invite
 *     endpoint via slowapi)
 *   • 500 + "FRONTEND_URL" in detail — friendlier copy explaining
 *     the misconfiguration and that only a SimpliMeta admin can fix
 *     it (added 1D.9)
 *
 * Props
 *   isOpen       boolean
 *   tenantId     string — target tenant for the invite
 *   tenantName   string — for header context
 *   onClose      () => void
 *   onSuccess    (user) => void  — caller reloads page state
 */

// Roles offered in the picker. Keep as an array so adding tenant_member
// or similar in a future phase is a one-line change.
const ROLE_OPTIONS = [
  {
    value: 'tenant_admin',
    label: 'Tenant Administrator',
    description:
      'Can view users, manage AI Employee assignments and instances within this tenant, and delegate to child tenants.',
  },
];

export default function InviteUserModal({
  isOpen,
  tenantId,
  tenantName,
  onClose,
  onSuccess,
}) {
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState(ROLE_OPTIONS[0].value);
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setEmail('');
    setName('');
    setRole(ROLE_OPTIONS[0].value);
    setErrors({});
    setSubmitting(false);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e) => {
      if (e.key === 'Escape' && !submitting) onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose, submitting]);

  const validate = useCallback(() => {
    const errs = {};
    const trimmedEmail = email.trim();
    const trimmedName = name.trim();
    if (!trimmedEmail) {
      errs.email = 'Email is required.';
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmedEmail)) {
      errs.email = 'Enter a valid email address.';
    }
    if (!trimmedName) errs.name = 'Name is required.';
    if (!role) errs.role = 'Pick a role.';
    return errs;
  }, [email, name, role]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    const validationErrors = validate();
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      return;
    }
    setSubmitting(true);
    setErrors({});

    try {
      const res = await inviteUserToTenant(tenantId, {
        email: email.trim(),
        name: name.trim(),
        role,
      });

      if (res.ok) {
        const data = await res.json();
        toast.success(`Invite sent to ${email.trim()}.`);
        onSuccess(data);
        return;
      }

      const errBody = await res.json().catch(() => ({}));
      const detail = errBody?.detail;
      const detailString = typeof detail === 'string' ? detail : detail?.message;

      if (res.status === 400 && detailString?.toLowerCase().includes('email')) {
        setErrors({
          email: detailString || 'Email already in use.',
        });
      } else if (res.status === 400) {
        setErrors({ _global: detailString || 'Invalid request.' });
      } else if (res.status === 403) {
        setErrors({ _global: "You don't have permission to invite users here." });
      } else if (res.status === 404) {
        setErrors({ _global: 'Tenant no longer exists. Refresh the page.' });
      } else if (res.status === 429) {
        setErrors({
          _global: 'Too many invites recently. Wait a few minutes and try again.',
        });
      } else if (
        res.status === 500 &&
        detailString?.toLowerCase().includes('frontend_url')
      ) {
        // 1D.9 — friendlier copy for the FRONTEND_URL misconfiguration.
        // The backend raises this when it can't build the invite link
        // because the env var isn't set. End users can't fix it — only
        // a SimpliMeta admin can. Tell them what's actually wrong and
        // who to contact, instead of the raw "Server configuration
        // error: FRONTEND_URL is not set" string from the backend.
        setErrors({
          _global:
            "The invite link couldn't be generated — this AEGISAXIOM deployment is missing a config value (FRONTEND_URL). Contact a SimpliMeta admin to fix it; users can't resolve this on their own.",
        });
      } else {
        setErrors({
          _global: detailString || `Request failed (HTTP ${res.status}).`,
        });
      }
    } catch (err) {
      setErrors({ _global: 'Network error. Please try again.' });
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen) return null;

  const selectedRole = ROLE_OPTIONS.find((r) => r.value === role);

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-labelledby="invite-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-lg p-6 animate-fadeInUp max-h-[90vh] overflow-y-auto"
        data-testid="invite-user-modal"
      >
        {/* Header */}
        <div className="flex items-start justify-between mb-5">
          <div className="flex items-start gap-3">
            <div className="w-10 h-10 rounded-full bg-violet-50 dark:bg-violet-600/10 flex items-center justify-center shrink-0">
              <UserPlus size={18} className="text-violet-600 dark:text-violet-400" />
            </div>
            <div>
              <h2
                id="invite-modal-title"
                className="text-base font-bold text-aegisaxiom-text"
              >
                Invite a user to {tenantName}
              </h2>
              <p className="text-xs text-aegisaxiom-text-secondary mt-1 leading-relaxed">
                The user receives an email with a sign-in link. Once they accept and
                set a password, they can log in and access this tenant's AI Employees.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text disabled:opacity-50 ml-2"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Global error */}
        {errors._global && (
          <div className="bg-red-50 dark:bg-red-600/10 border border-red-200 dark:border-red-600/20 rounded-lg p-3 mb-4 flex items-start gap-2">
            <AlertCircle
              size={14}
              className="text-red-600 dark:text-red-400 shrink-0 mt-0.5"
            />
            <p className="text-xs text-red-700 dark:text-red-300 leading-relaxed">
              {errors._global}
            </p>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Email */}
          <div>
            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
              Email *
            </label>
            <div className="relative">
              <Mail
                size={14}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted pointer-events-none"
              />
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={submitting}
                className="input-field pl-9"
                placeholder="person@example.com"
                data-testid="invite-email"
                autoFocus
                autoComplete="off"
              />
            </div>
            {errors.email ? (
              <p className="text-xs text-red-600 dark:text-red-400 mt-1">
                {errors.email}
              </p>
            ) : (
              <p className="text-xs text-aegisaxiom-text-muted mt-1">
                The invite link is sent here. The user sets their own password.
              </p>
            )}
          </div>

          {/* Name */}
          <div>
            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
              Name *
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={submitting}
              className="input-field"
              placeholder="Full name"
              data-testid="invite-name"
              maxLength={100}
            />
            {errors.name && (
              <p className="text-xs text-red-600 dark:text-red-400 mt-1">
                {errors.name}
              </p>
            )}
          </div>

          {/* Role */}
          <div>
            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
              Role *
            </label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              disabled={submitting}
              className="input-field"
              data-testid="invite-role"
            >
              {ROLE_OPTIONS.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
            {errors.role ? (
              <p className="text-xs text-red-600 dark:text-red-400 mt-1">
                {errors.role}
              </p>
            ) : selectedRole ? (
              <p className="text-xs text-aegisaxiom-text-muted mt-1 leading-relaxed">
                {selectedRole.description}
              </p>
            ) : null}
          </div>

          {/* Footer */}
          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="btn-secondary disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="btn-primary disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
              data-testid="invite-submit"
            >
              <UserPlus size={14} />
              {submitting ? 'Sending invite…' : 'Send invite'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
