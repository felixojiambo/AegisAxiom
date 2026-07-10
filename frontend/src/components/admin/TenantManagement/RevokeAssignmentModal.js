import React, { useState, useEffect } from 'react';
import { X, AlertCircle, AlertTriangle, Ban } from 'lucide-react';
import { toast } from 'sonner';
import { updateAssignment } from './api';

/**
 * Phase 1D.7 — Revoke Assignment confirmation modal.
 *
 * Mechanically calls `PATCH /tenants/{tid}/assignments/{aid}` with
 * `{status: "revoked"}`. Revocation is a status transition (per the
 * 1B.1 design), not a DELETE — the row stays in the DB so the audit
 * chain stays intact and any provisioned instances retain their back-
 * references. The backend stamps `revoked_at`, `revoked_by_user_id`,
 * and `revoked_by_tenant_id` automatically.
 *
 * Two outcomes that matter:
 *   • 200 — assignment status is now 'revoked'. We close + reload.
 *   • 409 — Phase 1C.4 conflict. The assignment has active sub-
 *     delegations downstream (some tenant_admin delegated this same
 *     definition to their children) and can't be revoked until those
 *     are revoked first. We close ourselves and hand the conflict
 *     detail up to the parent so it can open RevokeConflictModal.
 *
 * The cascade flag exists on the backend's roadmap (?cascade=true,
 * future) but 1D.7 explicitly does NOT support cascade — admins must
 * walk the chain manually for now.
 *
 * Props
 *   isOpen          boolean
 *   tenantId        string — owning tenant of the assignment
 *   tenantName      string — for the confirmation copy
 *   assignment      Assignment object (provides assignment_id, max_instances)
 *   definition      Definition joined from the catalog (provides name)
 *   instanceCount   number — for the warning banner about active instances
 *   onClose         () => void
 *   onSuccess       () => void  — called after a clean 200
 *   onConflict      (conflictDetail) => void  — called after a 409
 */

export default function RevokeAssignmentModal({
  isOpen,
  tenantId,
  tenantName,
  assignment,
  definition,
  instanceCount,
  onClose,
  onSuccess,
  onConflict,
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
      const res = await updateAssignment(tenantId, assignment.assignment_id, {
        status: 'revoked',
      });

      if (res.ok) {
        toast.success(`${definition?.name || 'Assignment'} revoked from ${tenantName}.`);
        onSuccess();
        return;
      }

      const errBody = await res.json().catch(() => ({}));
      const detail = errBody?.detail;

      if (res.status === 409) {
        // Phase 1C.4 conflict — sub-delegations block this revoke.
        // detail is the structured object: { message, blocking_sub_assignments }
        if (detail && typeof detail === 'object' && Array.isArray(detail.blocking_sub_assignments)) {
          onConflict(detail);
          return;
        }
        // Fallback if the response shape isn't what we expect — show
        // whatever message the server gave us inline.
        setError(
          (typeof detail === 'string' ? detail : detail?.message) ||
            'Cannot revoke — there is a conflict on the server.',
        );
      } else if (res.status === 403) {
        setError("You don't have permission to revoke assignments.");
      } else if (res.status === 404) {
        setError('Assignment no longer exists. Refresh the page.');
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

  if (!isOpen || !assignment || !definition) return null;

  const hasActiveInstances = instanceCount > 0;

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-labelledby="revoke-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-md p-6 animate-fadeInUp"
        data-testid="revoke-assignment-modal"
      >
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-start gap-3">
            <div className="w-10 h-10 rounded-full bg-red-50 dark:bg-red-600/10 flex items-center justify-center shrink-0">
              <Ban size={18} className="text-red-600 dark:text-red-400" />
            </div>
            <div>
              <h2 id="revoke-modal-title" className="text-base font-bold text-aegisaxiom-text">
                Revoke {definition.name} from {tenantName}?
              </h2>
              <p className="text-xs text-aegisaxiom-text-secondary mt-1 leading-relaxed">
                The assignment will be marked as revoked. The audit record is
                preserved, but no new instances can be provisioned.
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

        {hasActiveInstances && (
          <div className="bg-amber-50 dark:bg-amber-600/10 border border-amber-200 dark:border-amber-600/20 rounded-lg p-3 mb-4 flex items-start gap-2">
            <AlertTriangle
              size={14}
              className="text-amber-600 dark:text-amber-400 shrink-0 mt-0.5"
            />
            <div className="text-xs text-amber-700 dark:text-amber-300 leading-relaxed">
              <p className="font-medium mb-0.5">
                {instanceCount} active instance{instanceCount === 1 ? '' : 's'} will keep
                running
              </p>
              <p>
                Revoking the assignment doesn't delete its instances. If {tenantName}{' '}
                should lose access entirely, delete the instances first via the count
                drill-down.
              </p>
            </div>
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
            data-testid="revoke-confirm"
          >
            <Ban size={14} />
            {submitting ? 'Revoking…' : 'Revoke'}
          </button>
        </div>
      </div>
    </div>
  );
}
