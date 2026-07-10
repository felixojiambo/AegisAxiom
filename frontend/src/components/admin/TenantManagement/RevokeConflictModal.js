import React, { useEffect } from 'react';
import { X, AlertCircle, ArrowRight, Workflow } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

/**
 * Phase 1D.7 — Revoke Conflict modal.
 *
 * The architecturally interesting modal in 1D.7: it renders the
 * structured 409 response from the backend's revoke endpoint when
 * sub-delegations block the requested revoke.
 *
 * Backend response shape (from update_assignment in
 * routes/ai_employee_catalog.py, the Phase 1C.4 chain check):
 *
 *   {
 *     "message": "Cannot revoke: N active sub-delegation(s) ...",
 *     "blocking_sub_assignments": [
 *       {"assignment_id": "asn_...", "tenant_id": "ten_..."},
 *       ...
 *     ]
 *   }
 *
 * Our job: render the blockers with their resolved tenant names (lifted
 * from `allTenants`, the same array TenantDetail already loaded for its
 * parent breadcrumb) and offer deep links so the admin can walk the
 * chain manually. No cascade in v1 — admins revoke blockers, navigate
 * back, retry.
 *
 * If a tenant_id can't be resolved (rare — data weirdness), we fall
 * back to displaying the raw ID. The deep link still works.
 *
 * Props
 *   isOpen          boolean
 *   conflictDetail  { message, blocking_sub_assignments } from server
 *   allTenants      Tenant[] — for resolving tenant_id → name
 *   definitionName  string — for context in the title
 *   onClose         () => void
 */

export default function RevokeConflictModal({
  isOpen,
  conflictDetail,
  allTenants,
  definitionName,
  onClose,
}) {
  const navigate = useNavigate();

  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose]);

  if (!isOpen || !conflictDetail) return null;

  const blockers = conflictDetail.blocking_sub_assignments || [];
  const tenantsById = new Map(
    (allTenants || []).map((t) => [t.tenant_id, t]),
  );

  const handleOpenTenant = (tenantId) => {
    onClose();
    navigate(`/admin/tenants/${tenantId}`);
  };

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-labelledby="revoke-conflict-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-lg p-6 animate-fadeInUp max-h-[90vh] overflow-y-auto"
        data-testid="revoke-conflict-modal"
      >
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-start gap-3">
            <div className="w-10 h-10 rounded-full bg-amber-50 dark:bg-amber-600/10 flex items-center justify-center shrink-0">
              <AlertCircle size={18} className="text-amber-600 dark:text-amber-400" />
            </div>
            <div>
              <h2
                id="revoke-conflict-title"
                className="text-base font-bold text-aegisaxiom-text"
              >
                Can't revoke yet — {blockers.length} sub-delegation
                {blockers.length === 1 ? '' : 's'} depend{blockers.length === 1 ? 's' : ''} on this
              </h2>
              <p className="text-xs text-aegisaxiom-text-secondary mt-1 leading-relaxed">
                {definitionName ? `${definitionName} has been ` : 'This assignment has been '}
                delegated downstream. Revoke each of the assignments below first,
                then return here to retry.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text ml-2"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Server's verbatim message — keep it visible because it may
            carry more nuance than our title can fit. */}
        {conflictDetail.message && (
          <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-3 mb-4">
            <p className="text-xs text-aegisaxiom-text-secondary leading-relaxed font-mono">
              {conflictDetail.message}
            </p>
          </div>
        )}

        {/* Blocker list */}
        <div className="border border-aegisaxiom-border rounded-lg overflow-hidden mb-4">
          {blockers.length === 0 ? (
            <div className="p-4 text-center text-xs text-aegisaxiom-text-muted">
              Server returned a conflict but no blocking assignments. Refresh and try
              again.
            </div>
          ) : (
            blockers.map((b, idx) => {
              const tenant = tenantsById.get(b.tenant_id);
              const tenantName = tenant?.name || 'Unknown tenant';
              const tenantSlug = tenant?.slug;
              const isLast = idx === blockers.length - 1;
              return (
                <div
                  key={b.assignment_id}
                  className={
                    'flex items-center justify-between p-3 ' +
                    (isLast ? '' : 'border-b border-aegisaxiom-border')
                  }
                  data-testid={`conflict-blocker-${b.assignment_id}`}
                >
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <div className="w-8 h-8 rounded-md bg-aegisaxiom-elevated flex items-center justify-center shrink-0">
                      <Workflow size={14} className="text-aegisaxiom-text-secondary" />
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-aegisaxiom-text truncate">
                        {tenantName}
                      </div>
                      <div className="text-[11px] text-aegisaxiom-text-muted font-mono truncate">
                        {tenantSlug
                          ? `${tenantSlug} · ${b.assignment_id}`
                          : b.assignment_id}
                      </div>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleOpenTenant(b.tenant_id)}
                    className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-aegisaxiom-elevated border border-aegisaxiom-border hover:border-aegisaxiom-border-hover text-aegisaxiom-text transition shrink-0 ml-2"
                    data-testid={`open-tenant-${b.tenant_id}`}
                  >
                    Open tenant
                    <ArrowRight size={12} />
                  </button>
                </div>
              );
            })
          )}
        </div>

        <div className="flex items-center justify-end pt-1">
          <button type="button" onClick={onClose} className="btn-secondary">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
