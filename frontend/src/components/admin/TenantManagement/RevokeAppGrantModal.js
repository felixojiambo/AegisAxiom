import React, { useState, useEffect } from 'react';
import { X, AlertCircle, Ban } from 'lucide-react';
import { toast } from 'sonner';
import { updateAppGrant } from './api';

/**
 * Phase 1E.1 — Revoke App Grant confirmation modal.
 *
 * Mechanically calls `PATCH /tenants/{tid}/app-grants/{gid}` with
 * `{status: "revoked"}`. Same status-transition convention as the
 * AI Employee assignment revoke (1B.1 design): the row stays in the
 * DB so the audit chain stays intact. The backend stamps
 * `revoked_at`, `revoked_by_user_id`, and `revoked_by_tenant_id`
 * automatically.
 *
 * Unlike RevokeAssignmentModal, there's no instance-count warning —
 * apps don't have instances. There's also no 409 chain-check
 * conflict handling in 1E.1 because tenant-side delegation of app
 * grants doesn't exist yet (deferred to 1F). If/when it lands, this
 * modal will need the same conflict-handoff plumbing that
 * RevokeAssignmentModal carries.
 *
 * Props
 *   isOpen     boolean
 *   tenantId   string — owning tenant of the grant
 *   tenantName string — for the confirmation copy
 *   grant      Grant object (provides grant_id)
 *   app        AppRegistry doc joined from the catalog (provides name)
 *   onClose    () => void
 *   onSuccess  () => void  — called after a clean 200
 */

export default function RevokeAppGrantModal({
  isOpen,
  tenantId,
  tenantName,
  grant,
  app,
  onClose,
  onSuccess,
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!isOpen) return;
    setSubmitting(false);
    setError(null);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e) => {
      if (e.key === 'Escape' && !submitting) onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose, submitting]);

  const handleConfirm = async () => {
    setSubmitting(true);
    setError(null);

    try {
      const res = await updateAppGrant(tenantId, grant.grant_id, {
        status: 'revoked',
      });

      if (res.ok) {
        toast.success(`${app?.name || 'App'} revoked from ${tenantName}.`);
        onSuccess();
        return;
      }

      const errBody = await res.json().catch(() => ({}));
      const detail = errBody?.detail;

      if (res.status === 403) {
        setError("You don't have permission to revoke app grants.");
      } else if (res.status === 404) {
        setError('App grant no longer exists. Refresh the page.');
      } else if (res.status === 400) {
        setError(
          (typeof detail === 'string' ? detail : null) || 'Invalid request.',
        );
      } else {
        setError(
          (typeof detail === 'string' ? detail : null) ||
            `Request failed (HTTP ${res.status}).`,
        );
      }
    } catch (e) {
      setError('Network error. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen || !grant) return null;

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-labelledby="revoke-app-grant-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-md p-6 animate-fadeInUp"
        data-testid="revoke-app-grant-modal"
      >
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-start gap-3">
            <div className="w-10 h-10 rounded-full bg-red-50 dark:bg-red-600/10 flex items-center justify-center shrink-0">
              <Ban size={18} className="text-red-600 dark:text-red-400" />
            </div>
            <div>
              <h2
                id="revoke-app-grant-modal-title"
                className="text-base font-bold text-aegisaxiom-text"
              >
                Revoke {app?.name || 'app'} from {tenantName}?
              </h2>
              <p className="text-xs text-aegisaxiom-text-secondary mt-1 leading-relaxed">
                The app grant will be marked as revoked. The audit record
                is preserved, and the grant can be reactivated later from
                the row's kebab menu if needed.
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

        {error && (
          <div className="bg-red-50 dark:bg-red-600/10 border border-red-200 dark:border-red-600/20 rounded-lg p-3 mb-4 flex items-start gap-2">
            <AlertCircle
              size={14}
              className="text-red-600 dark:text-red-400 shrink-0 mt-0.5"
            />
            <p className="text-xs text-red-700 dark:text-red-300 leading-relaxed">
              {error}
            </p>
          </div>
        )}

        <div className="flex items-center justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="btn-secondary disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={submitting}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-red-600 hover:bg-red-500 text-white disabled:opacity-50 disabled:cursor-not-allowed transition"
            data-testid="revoke-app-grant-confirm"
          >
            <Ban size={14} />
            {submitting ? 'Revoking…' : 'Revoke'}
          </button>
        </div>
      </div>
    </div>
  );
}
