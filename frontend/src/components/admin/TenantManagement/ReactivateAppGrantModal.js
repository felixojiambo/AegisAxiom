import React, { useState, useEffect } from 'react';
import { X, AlertCircle, RotateCcw } from 'lucide-react';
import { toast } from 'sonner';
import { updateAppGrant } from './api';

/**
 * Phase 1E.1 — Reactivate App Grant confirmation modal.
 *
 * Re-grants a previously-revoked app grant by transitioning its
 * status back to 'active'. Mechanically a single
 *   PATCH /tenants/{tid}/app-grants/{gid}  body: { status: "active" }
 * call. Mirrors the 1D.9 ReactivateAssignmentModal pattern for AI
 * Employee assignments.
 *
 * No max_instances panel because apps don't have instances. No chain
 * validation fires on reactivation because tenant-side delegation of
 * app grants doesn't exist in 1E.1.
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

export default function ReactivateAppGrantModal({
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
        status: 'active',
      });

      if (res.ok) {
        toast.success(
          `${app?.name || 'App'} reactivated for ${tenantName}.`,
        );
        onSuccess();
        return;
      }

      const errBody = await res.json().catch(() => ({}));
      const detail = errBody?.detail;

      if (res.status === 403) {
        setError("You don't have permission to reactivate app grants.");
      } else if (res.status === 404) {
        setError('App grant no longer exists. Refresh the page.');
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

  if (!isOpen || !grant) return null;

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-labelledby="reactivate-app-grant-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-md p-6 animate-fadeInUp"
        data-testid="reactivate-app-grant-modal"
      >
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-start gap-3">
            <div className="w-10 h-10 rounded-full bg-emerald-50 dark:bg-emerald-600/10 flex items-center justify-center shrink-0">
              <RotateCcw
                size={18}
                className="text-emerald-600 dark:text-emerald-400"
              />
            </div>
            <div>
              <h2
                id="reactivate-app-grant-modal-title"
                className="text-base font-bold text-aegisaxiom-text"
              >
                Reactivate {app?.name || 'app'} for {tenantName}?
              </h2>
              <p className="text-xs text-aegisaxiom-text-secondary mt-1 leading-relaxed">
                The app grant will return to active status. {tenantName}{' '}
                will see the app in their sidebar again once 1E.2 lands.
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
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-50 disabled:cursor-not-allowed transition"
            data-testid="reactivate-app-grant-confirm"
          >
            <RotateCcw size={14} />
            {submitting ? 'Reactivating…' : 'Reactivate'}
          </button>
        </div>
      </div>
    </div>
  );
}
