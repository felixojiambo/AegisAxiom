import React, { useState, useEffect } from 'react';
import { X, AlertCircle, RotateCcw } from 'lucide-react';
import { toast } from 'sonner';
import { updateAssignment } from './api';
import { formatMaxInstances } from './helpers';

/**
 * Phase 1D.9 — Reactivate Assignment confirmation modal.
 *
 * Re-grants a previously-revoked assignment by transitioning its
 * status back to 'active'. Mechanically a single
 *   PATCH /tenants/{tid}/assignments/{aid}  body: { status: "active" }
 * call. The backend's PATCH handler accepts this transition cleanly —
 * no chain validation fires on reactivation, since there are no
 * sub-delegations on a revoked row (they had to have been revoked
 * first to allow the original revoke).
 *
 * Why a dedicated modal at all (vs. a one-click action)?
 *   • The admin should see the existing max_instances cap before
 *     bringing the assignment back online — if they want to change
 *     it, they can adjust after reactivating (1D.9 doesn't ship cap
 *     editing in this flow; that's deferred to 1E if it's actually
 *     requested).
 *   • The action is reversible (just re-revoke), so we don't need a
 *     full destructive-warning treatment — but a confirmation step
 *     prevents accidental kebab-click → action.
 *
 * Props
 *   isOpen          boolean
 *   tenantId        string — owning tenant of the assignment
 *   tenantName      string — for the confirmation copy
 *   assignment      Assignment object (provides assignment_id, max_instances)
 *   definition      Definition joined from the catalog (provides name)
 *   onClose         () => void
 *   onSuccess       () => void  — called after a clean 200
 */

export default function ReactivateAssignmentModal({
  isOpen,
  tenantId,
  tenantName,
  assignment,
  definition,
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
      const res = await updateAssignment(tenantId, assignment.assignment_id, {
        status: 'active',
      });

      if (res.ok) {
        toast.success(
          `${definition?.name || 'Assignment'} reactivated for ${tenantName}.`,
        );
        onSuccess();
        return;
      }

      const errBody = await res.json().catch(() => ({}));
      const detail = errBody?.detail;

      if (res.status === 403) {
        setError("You don't have permission to reactivate assignments.");
      } else if (res.status === 404) {
        setError('Assignment no longer exists. Refresh the page.');
      } else if (res.status === 400 || res.status === 422) {
        setError(
          (typeof detail === 'string' ? detail : null) ||
            'Reactivation was rejected by the server.',
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

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-labelledby="reactivate-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-md p-6 animate-fadeInUp"
        data-testid="reactivate-assignment-modal"
      >
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-start gap-3">
            <div className="w-10 h-10 rounded-full bg-emerald-50 dark:bg-emerald-600/10 flex items-center justify-center shrink-0">
              <RotateCcw size={18} className="text-emerald-600 dark:text-emerald-400" />
            </div>
            <div>
              <h2
                id="reactivate-modal-title"
                className="text-base font-bold text-aegisaxiom-text"
              >
                Reactivate {definition.name} for {tenantName}?
              </h2>
              <p className="text-xs text-aegisaxiom-text-secondary mt-1 leading-relaxed">
                The assignment will return to active status. {tenantName} will be
                able to provision instances again, up to the existing cap.
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

        {/* Informational: show the cap that will be in effect after reactivation. */}
        <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-3 mb-4 text-xs">
          <div className="text-aegisaxiom-text-muted uppercase tracking-wider text-[10px] mb-1.5">
            After reactivation
          </div>
          <div className="flex gap-2">
            <span className="text-aegisaxiom-text-muted shrink-0 w-24">Max instances</span>
            <span className="text-aegisaxiom-text">
              {formatMaxInstances(assignment.max_instances)}
            </span>
          </div>
          <p className="text-aegisaxiom-text-muted mt-1.5 leading-relaxed">
            To change the cap, reactivate first, then adjust from the row.
          </p>
        </div>

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
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-50 disabled:cursor-not-allowed transition"
            data-testid="reactivate-confirm"
          >
            <RotateCcw size={14} />
            {submitting ? 'Reactivating…' : 'Reactivate'}
          </button>
        </div>
      </div>
    </div>
  );
}
