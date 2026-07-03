import React, { useState, useEffect, useCallback } from 'react';
import { X, AlertCircle, Bot, Workflow, Infinity as InfinityIcon } from 'lucide-react';
import { toast } from 'sonner';
import { provisionInstance } from './api';
import { formatMaxInstances } from './helpers';

/**
 * Phase 1D.6 — Provision Instance modal.
 *
 * Opens from the Provision button on each assignment row. Spins up a
 * fresh instance of the assignment's definition for the target tenant,
 * counting against the assignment's `max_instances` cap.
 *
 * Backend (POST /api/tenants/{id}/assignments/{aid}/instances) accepts
 * an `overrides` dict. The only field we surface in the modal today is
 * `name` (required at the product level — the instance needs a label).
 * For n8n-type definitions we also collect the three n8n connection
 * fields so the per-tenant clone has somewhere to talk to. Everything
 * else (system_prompt, model overrides, allowed_roles) inherits from
 * the definition defaults — those advanced overrides are 1D.9 polish
 * territory if anyone asks.
 *
 * The backend enforces:
 *   • Assignment is active (returns 400 if revoked/suspended)
 *   • max_instances cap not reached (returns 409 with a clear message)
 *   • Caller permission and own-tenant scoping (returns 403)
 *
 * Props
 *   isOpen                boolean
 *   tenantId              string — target tenant
 *   tenantName            string — for the modal title
 *   assignment            Assignment object (provides assignment_id, max_instances, status)
 *   definition            Definition object joined from the catalog (type, name, defaults)
 *   currentInstanceCount  number — for the capacity preview
 *   onClose               () => void
 *   onSuccess             (instance) => void
 */

export default function ProvisionInstanceModal({
  isOpen,
  tenantId,
  tenantName,
  assignment,
  definition,
  currentInstanceCount,
  onClose,
  onSuccess,
}) {
  const [name, setName] = useState('');
  const [n8nWorkflowId, setN8nWorkflowId] = useState('');
  const [n8nInstanceUrl, setN8nInstanceUrl] = useState('');
  const [n8nApiKey, setN8nApiKey] = useState('');
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);

  const isN8n = definition?.type === 'n8n';
  const max = assignment?.max_instances ?? null;
  const isUnlimited = max === null || max === undefined;
  const wouldHitCap = !isUnlimited && currentInstanceCount >= max;

  // Reset state when the modal opens.
  useEffect(() => {
    if (!isOpen) return;
    // Seed name with a friendly default: "[Tenant]'s [Definition]" so
    // admins don't have to type the obvious thing. They can edit before
    // submit. Empty if either piece is missing.
    const seed = `${tenantName} — ${definition.name}`;
    setName(seed);
    setN8nWorkflowId('');
    setN8nInstanceUrl('');
    setN8nApiKey('');
    setErrors({});
    setSubmitting(false);
  }, [isOpen, tenantName, definition]);

  // ESC to close (when not submitting).
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
    if (!name.trim()) errs.name = 'Instance name is required.';
    else if (name.trim().length > 120) errs.name = 'Keep the name under 120 characters.';
    return errs;
  }, [name]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (wouldHitCap) {
      setErrors({ _global: 'This assignment has reached its instance cap.' });
      return;
    }
    const validationErrors = validate();
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      return;
    }
    setSubmitting(true);
    setErrors({});

    try {
      const overrides = { name: name.trim() };
      if (isN8n) {
        const wid = n8nWorkflowId.trim();
        const url = n8nInstanceUrl.trim();
        const key = n8nApiKey.trim();
        if (wid) overrides.n8n_workflow_id = wid;
        if (url) overrides.n8n_instance_url = url;
        if (key) overrides.n8n_api_key = key;
      }

      const res = await provisionInstance(tenantId, assignment.assignment_id, overrides);
      if (res.ok) {
        const data = await res.json();
        toast.success(`${name.trim()} provisioned.`);
        onSuccess(data);
        return;
      }

      const errBody = await res.json().catch(() => ({}));
      const detail = errBody?.detail;
      if (res.status === 409) {
        // Cap race — count incremented between page load and submit
        setErrors({
          _global:
            (typeof detail === 'string' ? detail : null) ||
            'This assignment has reached its instance cap. Refresh the page to see the current count.',
        });
      } else if (res.status === 400) {
        // Assignment not active, or other validation failure
        setErrors({
          _global:
            (typeof detail === 'string' ? detail : null) ||
            'Assignment is not in a provisionable state.',
        });
      } else if (res.status === 403) {
        setErrors({ _global: "You don't have permission to provision instances." });
      } else if (res.status === 404) {
        setErrors({ _global: 'Assignment or definition no longer exists.' });
      } else {
        setErrors({
          _global:
            (typeof detail === 'string' ? detail : null) || `Request failed (HTTP ${res.status}).`,
        });
      }
    } catch (err) {
      setErrors({ _global: 'Network error. Please try again.' });
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
      aria-labelledby="provision-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-lg p-6 animate-fadeInUp max-h-[90vh] overflow-y-auto"
        data-testid="provision-instance-modal"
      >
        {/* Header */}
        <div className="flex items-start justify-between mb-5">
          <div>
            <h2 id="provision-modal-title" className="text-lg font-bold text-aegisaxiom-text">
              Provision instance
            </h2>
            <p className="text-xs text-aegisaxiom-text-secondary mt-1">
              Spin up a new instance of{' '}
              <span className="font-medium text-aegisaxiom-text">{definition.name}</span> for{' '}
              <span className="font-medium text-aegisaxiom-text">{tenantName}</span>.
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
            <AlertCircle size={14} className="text-red-600 dark:text-red-400 shrink-0 mt-0.5" />
            <p className="text-xs text-red-700 dark:text-red-300 leading-relaxed">
              {errors._global}
            </p>
          </div>
        )}

        {/* Capacity preview */}
        <CapacityPreview
          currentInstanceCount={currentInstanceCount}
          max={max}
          isUnlimited={isUnlimited}
          wouldHitCap={wouldHitCap}
        />

        <form onSubmit={handleSubmit} className="space-y-4 mt-4">
          {/* Name */}
          <div>
            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Instance name *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={submitting || wouldHitCap}
              className="input-field"
              placeholder={`e.g. ${tenantName} — ${definition.name}`}
              data-testid="provision-name"
              autoFocus
              maxLength={120}
            />
            {errors.name ? (
              <p className="text-xs text-red-600 dark:text-red-400 mt-1">{errors.name}</p>
            ) : (
              <p className="text-xs text-aegisaxiom-text-muted mt-1">
                Per-tenant brand for this instance. Visible inside the tenant's AEGISAXIOM app.
              </p>
            )}
          </div>

          {/* N8N connection fields — only for n8n-type definitions */}
          {isN8n && (
            <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-3 space-y-3">
              <div className="flex items-center gap-1.5 text-xs font-medium text-aegisaxiom-text">
                <Workflow size={12} className="text-indigo-600 dark:text-indigo-400" />
                n8n connection (optional)
              </div>
              <p className="text-xs text-aegisaxiom-text-muted -mt-1">
                Configure now or leave empty to set up after provisioning.
              </p>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Workflow ID</label>
                <input
                  type="text"
                  value={n8nWorkflowId}
                  onChange={(e) => setN8nWorkflowId(e.target.value)}
                  disabled={submitting}
                  className="input-field font-mono text-xs"
                  placeholder="e.g. 1ABCxyz..."
                  data-testid="provision-n8n-workflow-id"
                />
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Instance URL</label>
                <input
                  type="text"
                  value={n8nInstanceUrl}
                  onChange={(e) => setN8nInstanceUrl(e.target.value)}
                  disabled={submitting}
                  className="input-field text-xs"
                  placeholder="https://n8n.example.com"
                  data-testid="provision-n8n-url"
                />
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">API key</label>
                <input
                  type="password"
                  value={n8nApiKey}
                  onChange={(e) => setN8nApiKey(e.target.value)}
                  disabled={submitting}
                  className="input-field font-mono text-xs"
                  placeholder="Stored encrypted"
                  data-testid="provision-n8n-key"
                />
              </div>
            </div>
          )}

          {/* Defaults preview */}
          <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-3 text-xs">
            <div className="text-aegisaxiom-text-muted uppercase tracking-wider text-[10px] mb-1.5">
              Inheriting from definition
            </div>
            <div className="space-y-1">
              <div className="flex gap-2">
                <span className="text-aegisaxiom-text-muted shrink-0 w-24">Type</span>
                <span className="text-aegisaxiom-text inline-flex items-center gap-1.5">
                  {isN8n ? <Workflow size={12} /> : <Bot size={12} />}
                  {isN8n ? 'n8n' : 'Conversational'}
                </span>
              </div>
              {definition.default_model_name && (
                <div className="flex gap-2">
                  <span className="text-aegisaxiom-text-muted shrink-0 w-24">Model</span>
                  <span className="text-aegisaxiom-text">
                    {definition.default_model_name}
                    {definition.default_model_provider && (
                      <span className="text-aegisaxiom-text-muted ml-1.5">
                        ({definition.default_model_provider})
                      </span>
                    )}
                  </span>
                </div>
              )}
              {definition.provisioning_mode && (
                <div className="flex gap-2">
                  <span className="text-aegisaxiom-text-muted shrink-0 w-24">Mode</span>
                  <span className="text-aegisaxiom-text font-mono text-[11px]">
                    {definition.provisioning_mode}
                  </span>
                </div>
              )}
            </div>
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
              disabled={submitting || wouldHitCap}
              className="btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
              data-testid="provision-submit"
            >
              {submitting ? 'Provisioning…' : 'Provision'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─── Internal: capacity preview ────────────────────────────────────

/**
 * Shows the assignment's current and projected instance count, with a
 * subtle visual treatment that reflects whether there's room to provision.
 */
function CapacityPreview({ currentInstanceCount, max, isUnlimited, wouldHitCap }) {
  if (isUnlimited) {
    return (
      <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-3 flex items-center gap-2.5">
        <InfinityIcon size={16} className="text-aegisaxiom-text-secondary shrink-0" />
        <div className="text-xs">
          <div className="text-aegisaxiom-text">
            <span className="font-medium tabular-nums">{currentInstanceCount}</span> instance
            {currentInstanceCount === 1 ? '' : 's'} provisioned
          </div>
          <div className="text-aegisaxiom-text-muted mt-0.5">
            Unlimited capacity — provision as many as you need.
          </div>
        </div>
      </div>
    );
  }

  const after = currentInstanceCount + 1;
  const pct = max > 0 ? Math.min(100, (currentInstanceCount / max) * 100) : 0;

  return (
    <div
      className={
        'border rounded-lg p-3 ' +
        (wouldHitCap
          ? 'bg-red-50 dark:bg-red-600/10 border-red-200 dark:border-red-600/20'
          : 'bg-aegisaxiom-elevated border-aegisaxiom-border')
      }
    >
      <div className="flex items-center justify-between text-xs mb-2">
        <span className="text-aegisaxiom-text">
          <span className="font-medium tabular-nums">{currentInstanceCount}</span> of{' '}
          <span className="font-medium tabular-nums">{formatMaxInstances(max)}</span> instances used
        </span>
        {!wouldHitCap && (
          <span className="text-aegisaxiom-text-muted tabular-nums">
            → {after} of {formatMaxInstances(max)} after this
          </span>
        )}
      </div>
      <div className="h-1.5 bg-aegisaxiom-base rounded-full overflow-hidden">
        <div
          className={
            'h-full transition-all ' +
            (wouldHitCap ? 'bg-red-500 dark:bg-red-400' : 'bg-violet-500 dark:bg-violet-400')
          }
          style={{ width: `${pct}%` }}
        />
      </div>
      {wouldHitCap && (
        <p className="text-xs text-red-700 dark:text-red-300 mt-2">
          Cap reached. Revoke an existing instance or grant more capacity to provision a new one.
        </p>
      )}
    </div>
  );
}
