import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { X, AlertCircle, Package, Check } from 'lucide-react';
import { toast } from 'sonner';
import { grantApp } from './api';

/**
 * Phase 1E.1 — Grant App modal.
 *
 * Opens from the Tenant Detail page's Apps tab "+ Grant app" button.
 * The caller (TenantDetail) provides the target tenant, the full list
 * of grantable apps (already filtered to status=active on the
 * backend), and the tenant's existing app grants. We filter the
 * picker client-side to exclude apps that already have a grant on
 * this tenant in ANY status — revoked grants are reactivated from the
 * grant-row kebab (mirrors the 1D.9 reactivation flow for AI Employee
 * assignments), not re-granted from this picker. The backend's
 * compound unique index on (tenant_id, app_id) enforces this at the
 * DB layer too; we just save the user a confused error message.
 *
 * Props
 *   isOpen           boolean
 *   tenantId         string — target tenant for the grant
 *   tenantName       string — for the modal title
 *   apps             AppRegistry[] — full list from the catalog; we filter internally
 *   existingGrants   Grant[] — already-held grants; we filter internally
 *   onClose          () => void
 *   onSuccess        (grant) => void
 */
export default function GrantAppModal({
  isOpen,
  tenantId,
  tenantName,
  apps,
  existingGrants,
  onClose,
  onSuccess,
}) {
  const [appId, setAppId] = useState('');
  const [notes, setNotes] = useState('');
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);

  // Reset state when the modal opens.
  useEffect(() => {
    if (!isOpen) return;
    setAppId('');
    setNotes('');
    setErrors({});
    setSubmitting(false);
  }, [isOpen]);

  // ESC to close (when not submitting).
  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e) => {
      if (e.key === 'Escape' && !submitting) onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose, submitting]);

  // Apps already granted to this tenant — ANY status. Same Phase 1D.9
  // rationale as GrantAssignmentModal: the backend's compound unique
  // index treats revoked rows the same as any other status, so a
  // re-grant is a PATCH back to active (the Reactivate kebab option),
  // not a new insert.
  const grantedAppIds = useMemo(() => {
    const set = new Set();
    for (const g of existingGrants || []) {
      set.add(g.app_id);
    }
    return set;
  }, [existingGrants]);

  // Available apps: active (already filtered server-side) + not
  // already granted on this tenant.
  const availableApps = useMemo(
    () =>
      (apps || [])
        .filter((a) => !grantedAppIds.has(a.app_id))
        .sort((a, b) => (a.name || '').localeCompare(b.name || '')),
    [apps, grantedAppIds],
  );

  const selectedApp = useMemo(
    () => availableApps.find((a) => a.app_id === appId) || null,
    [availableApps, appId],
  );

  const validate = useCallback(() => {
    const errs = {};
    if (!appId) errs.appId = 'Pick an app.';
    return errs;
  }, [appId]);

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
      const body = {
        app_id: appId,
        tenant_id: tenantId,
      };
      const trimmedNotes = notes.trim();
      if (trimmedNotes) body.notes = trimmedNotes;

      const res = await grantApp(tenantId, body);
      if (res.ok) {
        const data = await res.json();
        toast.success(
          `${selectedApp?.name || 'App'} granted to ${tenantName}.`,
        );
        onSuccess(data);
        return;
      }

      const errBody = await res.json().catch(() => ({}));
      const detail = errBody?.detail;
      if (res.status === 409) {
        // A revoked row already exists for this (tenant, app) pair;
        // user should reactivate from the row, not regrant.
        setErrors({
          appId:
            'This app already has a grant on this tenant (possibly revoked). Close this dialog and reactivate it from the grant row.',
        });
      } else if (res.status === 400 || res.status === 422) {
        setErrors({ _global: detail || 'Some values are invalid.' });
      } else if (res.status === 403) {
        setErrors({ _global: "You don't have permission to grant apps." });
      } else if (res.status === 404) {
        setErrors({ _global: 'Tenant or app no longer exists.' });
      } else {
        setErrors({ _global: detail || `Request failed (HTTP ${res.status}).` });
      }
    } catch (err) {
      setErrors({ _global: 'Network error. Please try again.' });
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-labelledby="grant-app-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-lg p-6 animate-fadeInUp max-h-[90vh] overflow-y-auto"
        data-testid="grant-app-modal"
      >
        {/* Header */}
        <div className="flex items-start justify-between mb-5">
          <div>
            <h2
              id="grant-app-modal-title"
              className="text-lg font-bold text-aegisaxiom-text"
            >
              Grant app
            </h2>
            <p className="text-xs text-aegisaxiom-text-secondary mt-1">
              Pick an ingested app for{' '}
              <span className="font-medium text-aegisaxiom-text">{tenantName}</span>.
              The tenant's sidebar will surface it once 1E.2 lands.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text disabled:opacity-50"
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

        <form onSubmit={handleSubmit}>
          {/* App picker */}
          <div className="mb-5">
            <label className="block text-xs font-medium text-aegisaxiom-text mb-2">
              App
            </label>
            {availableApps.length === 0 ? (
              <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4 text-center">
                <Package
                  size={20}
                  className="mx-auto mb-2 text-aegisaxiom-text-muted"
                />
                <p className="text-xs text-aegisaxiom-text-secondary">
                  No grantable apps remaining for this tenant. Either every
                  active app has already been granted, or no apps are in
                  the catalog with status=active yet.
                </p>
              </div>
            ) : (
              <div
                className="space-y-2 max-h-60 overflow-y-auto pr-1"
                data-testid="grant-app-picker"
              >
                {availableApps.map((a) => {
                  const isSelected = appId === a.app_id;
                  return (
                    <button
                      key={a.app_id}
                      type="button"
                      onClick={() => {
                        setAppId(a.app_id);
                        if (errors.appId) {
                          setErrors((prev) => ({ ...prev, appId: undefined }));
                        }
                      }}
                      className={
                        'w-full text-left flex items-center gap-3 p-3 rounded-lg border transition ' +
                        (isSelected
                          ? 'border-violet-500 bg-violet-50 dark:bg-violet-600/10'
                          : 'border-aegisaxiom-border bg-aegisaxiom-base hover:border-aegisaxiom-border-hover')
                      }
                      data-testid={`grant-app-option-${a.app_id}`}
                    >
                      <div className="w-9 h-9 rounded-md flex items-center justify-center shrink-0 bg-blue-100 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300">
                        <Package size={18} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="font-medium text-sm text-aegisaxiom-text truncate">
                          {a.name}
                        </div>
                        <div className="text-[11px] text-aegisaxiom-text-muted font-mono mt-0.5 truncate">
                          {a.slug}
                        </div>
                        {a.description && (
                          <div className="text-[11px] text-aegisaxiom-text-secondary mt-1 line-clamp-2">
                            {a.description}
                          </div>
                        )}
                      </div>
                      {isSelected && (
                        <Check
                          size={16}
                          className="text-violet-600 dark:text-violet-400 shrink-0"
                        />
                      )}
                    </button>
                  );
                })}
              </div>
            )}
            {errors.appId && (
              <p className="text-[11px] text-red-600 dark:text-red-400 mt-2">
                {errors.appId}
              </p>
            )}
          </div>

          {/* Notes */}
          <div className="mb-5">
            <label
              htmlFor="grant-app-notes"
              className="block text-xs font-medium text-aegisaxiom-text mb-2"
            >
              Notes <span className="text-aegisaxiom-text-muted">(optional)</span>
            </label>
            <textarea
              id="grant-app-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              maxLength={500}
              placeholder="e.g. Granted as part of AppDirect Tier-1 onboarding, 23 May 2026."
              className="w-full bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg px-3 py-2 text-sm text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:ring-2 focus:ring-violet-500"
              data-testid="grant-app-notes-input"
            />
            <p className="text-[11px] text-aegisaxiom-text-muted mt-1">
              {notes.length}/500
            </p>
          </div>

          {/* Actions */}
          <div className="flex items-center justify-end gap-2 pt-2 border-t border-aegisaxiom-border">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="btn-secondary"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || availableApps.length === 0}
              className="btn-primary"
              data-testid="grant-app-submit"
            >
              {submitting ? 'Granting…' : 'Grant app'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
