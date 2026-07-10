/**
 * ContractBillingTriggersPanel — Billing triggers CRUD for a contract.
 *
 * Wires the Wave-4 contract billing-trigger routes to the contract
 * detail surface so finance / sales-ops can manage automated billing
 * triggers without API tooling.
 *
 * Backend routes consumed (no inventions):
 *   GET    /api/contracts/{id}/billing-triggers
 *   POST   /api/contracts/{id}/billing-triggers
 *   DELETE /api/contracts/{id}/billing-triggers/{trigger_id}
 *   POST   /api/contracts/{id}/billing-triggers/evaluate
 *
 * Permissions (mirror backend/routes/contract_triggers.py):
 *   - list     : RBAC contract filter (same as the contract itself).
 *                The route 404s on RBAC miss, never 403.
 *   - create   : contracts.edit | contracts.edit.all
 *   - disable  : contracts.edit | contracts.edit.all
 *   - evaluate : contracts.approve | finance.portal | *
 *
 * The DELETE is a soft-disable — it flips the trigger state to
 * "disabled" and keeps the row for the audit chain. The panel
 * matches that contract: the trash button calls the DELETE route
 * but labels itself "Disable".
 *
 * Trigger types and their threshold semantics (mirror
 * backend/services/contract_trigger_evaluator.py::validate_trigger_payload):
 *   - date                : threshold = ISO date / datetime string
 *   - pct_consumed        : threshold = number 0..100
 *   - remaining_threshold : threshold = non-negative number (hours)
 *   - block_exhausted     : threshold = ignored / not used
 *   - biweekly_cycle      : threshold = ISO date anchor (optional)
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import {
  Plus,
  RefreshCw,
  AlertTriangle,
  Calendar,
  Zap,
  Trash2,
  PlayCircle,
} from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';

const API = API_URL;

// Mirror backend VALID_TRIGGER_TYPES. The threshold semantics
// column drives the form's per-type threshold field.
const TRIGGER_TYPES = [
  {
    value: 'date',
    label: 'Date',
    thresholdLabel: 'Fire on or after (ISO date)',
    thresholdRequired: true,
    thresholdPlaceholder: 'YYYY-MM-DD',
  },
  {
    value: 'pct_consumed',
    label: 'Percent consumed',
    thresholdLabel: 'Percent threshold (0–100)',
    thresholdRequired: true,
    thresholdPlaceholder: 'e.g. 80',
  },
  {
    value: 'remaining_threshold',
    label: 'Remaining hours',
    thresholdLabel: 'Remaining hours threshold (≥ 0)',
    thresholdRequired: true,
    thresholdPlaceholder: 'e.g. 8',
  },
  {
    value: 'block_exhausted',
    label: 'Block exhausted',
    thresholdLabel: null,
    thresholdRequired: false,
    thresholdPlaceholder: null,
  },
  {
    value: 'biweekly_cycle',
    label: 'Biweekly cycle',
    thresholdLabel: 'Cycle anchor (optional ISO date)',
    thresholdRequired: false,
    thresholdPlaceholder: 'YYYY-MM-DD',
  },
];

// Mirror backend VALID_TRIGGER_ACTIONS.
const TRIGGER_ACTIONS = [
  { value: 'notify_finance', label: 'Notify finance' },
  { value: 'create_task', label: 'Create task' },
  { value: 'queue_invoice_batch', label: 'Queue invoice batch' },
];

// Mirror backend VALID_TRIGGER_STATES. "fired" is set by the
// evaluator only; the create form offers armed / disabled.
const STATE_META = {
  armed: {
    label: 'Armed',
    className:
      'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border border-emerald-300/40',
  },
  fired: {
    label: 'Fired',
    className:
      'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border border-amber-300/40',
  },
  disabled: {
    label: 'Disabled',
    className:
      'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border',
  },
};

function StateBadge({ state }) {
  const meta = STATE_META[state] || STATE_META.disabled;
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium ${meta.className}`}
      data-testid={`trigger-state-${state || 'unknown'}`}
    >
      {meta.label}
    </span>
  );
}

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch (_) {
    return String(iso);
  }
}

function describeThreshold(t) {
  if (t.threshold === null || t.threshold === undefined || t.threshold === '') return null;
  if (t.trigger_type === 'pct_consumed') return `${t.threshold}%`;
  if (t.trigger_type === 'remaining_threshold') return `${t.threshold} h remaining`;
  if (t.trigger_type === 'date' || t.trigger_type === 'biweekly_cycle') {
    return `anchor ${t.threshold}`;
  }
  return String(t.threshold);
}

const EMPTY_FORM = {
  trigger_type: 'pct_consumed',
  threshold: '',
  action: 'notify_finance',
  state: 'armed',
  label: '',
};

export default function ContractBillingTriggersPanel({ contract, onContractUpdated }) {
  const { hasPermission } = usePermissions();

  const canEdit =
    hasPermission('contracts.edit')
    || hasPermission('contracts.edit.all')
    || hasPermission('*');

  const canEvaluate =
    hasPermission('contracts.approve')
    || hasPermission('finance.portal')
    || hasPermission('*');

  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [evaluating, setEvaluating] = useState(false);

  const contractId = contract?.contract_id;

  const loadTriggers = useCallback(async () => {
    if (!contractId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.get(`${API}/api/contracts/${contractId}/billing-triggers`);
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        setError(
          typeof detail?.detail === 'string'
            ? detail.detail
            : `Failed to load billing triggers (HTTP ${res.status}).`,
        );
        setItems([]);
        return;
      }
      const data = await res.json();
      setItems(Array.isArray(data?.items) ? data.items : []);
    } catch (e) {
      setError(e?.message || 'Failed to load billing triggers.');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [contractId]);

  useEffect(() => {
    loadTriggers();
  }, [loadTriggers]);

  const selectedTypeMeta = useMemo(
    () => TRIGGER_TYPES.find((t) => t.value === form.trigger_type) || TRIGGER_TYPES[0],
    [form.trigger_type],
  );

  // Per-trigger-type threshold validation that mirrors
  // validate_trigger_payload on the backend. The lock is intentional:
  // a future backend rule change must surface here too.
  const thresholdValidationError = useMemo(() => {
    const raw = (form.threshold ?? '').toString().trim();
    if (form.trigger_type === 'date') {
      if (!raw) return 'Date triggers require an ISO date threshold.';
      if (Number.isNaN(Date.parse(raw))) return 'Threshold must be a valid ISO date.';
      return null;
    }
    if (form.trigger_type === 'pct_consumed') {
      const n = Number(raw);
      if (raw === '' || !Number.isFinite(n)) return 'Percent threshold required.';
      if (n < 0 || n > 100) return 'Percent must be between 0 and 100.';
      return null;
    }
    if (form.trigger_type === 'remaining_threshold') {
      const n = Number(raw);
      if (raw === '' || !Number.isFinite(n)) return 'Remaining-hours threshold required.';
      if (n < 0) return 'Remaining hours must be ≥ 0.';
      return null;
    }
    if (form.trigger_type === 'biweekly_cycle') {
      if (raw === '') return null; // optional
      if (Number.isNaN(Date.parse(raw))) return 'If provided, anchor must be a valid ISO date.';
      return null;
    }
    // block_exhausted ignores threshold
    return null;
  }, [form.threshold, form.trigger_type]);

  const formValid = !thresholdValidationError;

  const submit = async (e) => {
    e?.preventDefault?.();
    if (!formValid || !contractId) return;
    setSubmitting(true);
    try {
      const body = {
        trigger_type: form.trigger_type,
        action: form.action,
        state: form.state, // armed | disabled (fired is evaluator-only)
      };
      const rawThreshold = (form.threshold ?? '').toString().trim();
      // The backend stores threshold verbatim per trigger_type; we
      // coerce numeric types client-side so the wire shape matches
      // what _validate_payload expects when it calls float(threshold).
      if (form.trigger_type === 'pct_consumed' || form.trigger_type === 'remaining_threshold') {
        body.threshold = Number(rawThreshold);
      } else if (rawThreshold) {
        body.threshold = rawThreshold;
      }
      const label = (form.label || '').trim();
      if (label) body.label = label;

      const res = await api.post(
        `${API}/api/contracts/${contractId}/billing-triggers`,
        body,
      );
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        const message = typeof detail?.detail === 'string'
          ? detail.detail
          : 'Failed to create billing trigger';
        toast.error(message);
        return;
      }
      toast.success('Billing trigger created');
      setForm(EMPTY_FORM);
      setShowForm(false);
      loadTriggers();
    } catch (err) {
      toast.error('Failed to create billing trigger');
    } finally {
      setSubmitting(false);
    }
  };

  const disableTrigger = async (trigger) => {
    if (!contractId || !trigger?.trigger_id) return;
    if (trigger.state === 'disabled') return;
    // No window.confirm — finance can re-create a trigger trivially
    // and the row is preserved for the audit chain. A confirm here
    // would just slow down a routine action.
    try {
      const res = await api.delete(
        `${API}/api/contracts/${contractId}/billing-triggers/${trigger.trigger_id}`,
      );
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        toast.error(
          typeof detail?.detail === 'string'
            ? detail.detail
            : 'Failed to disable trigger',
        );
        return;
      }
      toast.success('Trigger disabled');
      loadTriggers();
    } catch (err) {
      toast.error('Failed to disable trigger');
    }
  };

  const runEvaluator = async () => {
    if (!contractId) return;
    setEvaluating(true);
    try {
      const res = await api.post(
        `${API}/api/contracts/${contractId}/billing-triggers/evaluate`,
        {},
      );
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        toast.error(
          typeof detail?.detail === 'string'
            ? detail.detail
            : 'Failed to run evaluator',
        );
        return;
      }
      const result = await res.json().catch(() => ({}));
      const firedCount = Array.isArray(result?.fired) ? result.fired.length : 0;
      const evaluatedCount = Array.isArray(result?.evaluated) ? result.evaluated.length : null;
      if (firedCount > 0) {
        toast.success(`Evaluator fired ${firedCount} trigger(s)`);
      } else if (evaluatedCount === 0) {
        toast.success('No triggers to evaluate');
      } else {
        toast.success('Evaluator ran — no triggers fired');
      }
      // Refresh both the trigger list (state may have flipped to
      // "fired") and the parent contract (queue_invoice_batch may
      // have created a batch / notify_finance may have updated
      // last_action_result that downstream panels surface).
      loadTriggers();
      onContractUpdated?.();
    } catch (err) {
      toast.error('Failed to run evaluator');
    } finally {
      setEvaluating(false);
    }
  };

  if (!contractId) return null;

  return (
    <div className="glass-card p-4" data-testid="contract-billing-triggers-panel">
      <div className="flex items-center justify-between gap-2 flex-wrap mb-3">
        <h3 className="text-sm font-semibold text-aegisaxiom-text flex items-center gap-2">
          <Zap size={16} className="text-violet-500" />
          Billing triggers
          {items.length > 0 && (
            <span className="text-[11px] font-normal text-aegisaxiom-text-muted">
              ({items.length})
            </span>
          )}
        </h3>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            onClick={loadTriggers}
            className="inline-flex items-center gap-1 px-2 py-1 rounded border border-aegisaxiom-border hover:bg-aegisaxiom-elevated text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
            data-testid="contract-billing-triggers-refresh"
          >
            <RefreshCw className="w-3 h-3" />
            Refresh
          </button>
          {canEvaluate && (
            <button
              type="button"
              onClick={runEvaluator}
              disabled={evaluating}
              className="inline-flex items-center gap-1 px-2 py-1 rounded bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface border border-aegisaxiom-border text-aegisaxiom-text text-xs disabled:opacity-50"
              data-testid="contract-billing-triggers-evaluate"
            >
              <PlayCircle className="w-3 h-3" />
              {evaluating ? 'Evaluating…' : 'Run evaluator'}
            </button>
          )}
          {canEdit && (
            <button
              type="button"
              onClick={() => setShowForm((v) => !v)}
              className="inline-flex items-center gap-1 px-2 py-1 rounded bg-violet-600 hover:bg-violet-500 text-white text-xs"
              data-testid="contract-billing-triggers-toggle-form"
            >
              <Plus className="w-3 h-3" />
              {showForm ? 'Cancel' : 'New trigger'}
            </button>
          )}
        </div>
      </div>

      {error && (
        <div
          className="flex items-center gap-2 p-2 mb-3 bg-red-50 dark:bg-red-600/10 border border-red-300/40 rounded text-red-700 dark:text-red-300 text-xs"
          data-testid="contract-billing-triggers-error"
        >
          <AlertTriangle className="w-3.5 h-3.5" />
          <span>{error}</span>
        </div>
      )}

      {showForm && canEdit && (
        <form
          onSubmit={submit}
          className="bg-aegisaxiom-elevated/40 border border-aegisaxiom-border rounded p-3 mb-3 space-y-2"
          data-testid="contract-billing-triggers-form"
        >
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-[11px] text-aegisaxiom-text-secondary mb-0.5">Trigger type *</label>
              <select
                value={form.trigger_type}
                onChange={(e) =>
                  setForm((p) => ({ ...p, trigger_type: e.target.value, threshold: '' }))
                }
                className="aegisaxiom-input w-full text-xs"
                data-testid="contract-billing-triggers-type"
              >
                {TRIGGER_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[11px] text-aegisaxiom-text-secondary mb-0.5">Action *</label>
              <select
                value={form.action}
                onChange={(e) => setForm((p) => ({ ...p, action: e.target.value }))}
                className="aegisaxiom-input w-full text-xs"
                data-testid="contract-billing-triggers-action"
              >
                {TRIGGER_ACTIONS.map((a) => (
                  <option key={a.value} value={a.value}>{a.label}</option>
                ))}
              </select>
            </div>
          </div>

          {selectedTypeMeta.thresholdLabel && (
            <div>
              <label className="block text-[11px] text-aegisaxiom-text-secondary mb-0.5">
                {selectedTypeMeta.thresholdLabel}
                {selectedTypeMeta.thresholdRequired ? ' *' : ' (optional)'}
              </label>
              <input
                type="text"
                value={form.threshold}
                onChange={(e) => setForm((p) => ({ ...p, threshold: e.target.value }))}
                placeholder={selectedTypeMeta.thresholdPlaceholder || ''}
                className="aegisaxiom-input w-full text-xs"
                data-testid="contract-billing-triggers-threshold"
              />
              {thresholdValidationError && (
                <p
                  className="text-[10px] text-red-500 mt-0.5"
                  data-testid="contract-billing-triggers-threshold-error"
                >
                  {thresholdValidationError}
                </p>
              )}
            </div>
          )}

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-[11px] text-aegisaxiom-text-secondary mb-0.5">Initial state</label>
              <select
                value={form.state}
                onChange={(e) => setForm((p) => ({ ...p, state: e.target.value }))}
                className="aegisaxiom-input w-full text-xs"
                data-testid="contract-billing-triggers-state-select"
              >
                <option value="armed">Armed</option>
                <option value="disabled">Disabled</option>
              </select>
            </div>
            <div>
              <label className="block text-[11px] text-aegisaxiom-text-secondary mb-0.5">Label (optional)</label>
              <input
                type="text"
                value={form.label}
                onChange={(e) => setForm((p) => ({ ...p, label: e.target.value }))}
                placeholder="Friendly label for ops"
                className="aegisaxiom-input w-full text-xs"
                data-testid="contract-billing-triggers-label"
              />
            </div>
          </div>

          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={() => { setShowForm(false); setForm(EMPTY_FORM); }}
              className="px-3 py-1.5 text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!formValid || submitting}
              className="px-3 py-1.5 text-xs rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white"
              data-testid="contract-billing-triggers-submit"
            >
              {submitting ? 'Creating…' : 'Create trigger'}
            </button>
          </div>
        </form>
      )}

      <div className="space-y-2">
        {loading ? (
          <p
            className="text-xs text-aegisaxiom-text-muted text-center py-4"
            data-testid="contract-billing-triggers-loading"
          >
            Loading…
          </p>
        ) : items.length === 0 ? (
          <p
            className="text-xs text-aegisaxiom-text-muted text-center py-4"
            data-testid="contract-billing-triggers-empty"
          >
            No billing triggers on this contract.
          </p>
        ) : (
          items.map((t) => {
            const typeMeta = TRIGGER_TYPES.find((x) => x.value === t.trigger_type);
            const actionMeta = TRIGGER_ACTIONS.find((x) => x.value === t.action);
            const thresholdText = describeThreshold(t);
            const canDisable = canEdit && t.state !== 'disabled';
            return (
              <div
                key={t.trigger_id}
                className="bg-aegisaxiom-elevated/30 border border-aegisaxiom-border rounded p-2.5"
                data-testid={`contract-billing-triggers-row-${t.trigger_id}`}
              >
                <div className="flex items-center justify-between gap-2 flex-wrap mb-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <StateBadge state={t.state} />
                    <span className="text-xs font-semibold text-aegisaxiom-text">
                      {typeMeta?.label || t.trigger_type}
                    </span>
                    {thresholdText && (
                      <span className="text-[11px] text-aegisaxiom-text-secondary">
                        {thresholdText}
                      </span>
                    )}
                    <span className="text-[10px] text-aegisaxiom-text-muted">→</span>
                    <span className="text-[11px] text-aegisaxiom-text-secondary">
                      {actionMeta?.label || t.action}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-aegisaxiom-text-muted inline-flex items-center gap-1">
                      <Calendar className="w-3 h-3" />
                      {fmtDate(t.created_at)}
                    </span>
                    {canDisable && (
                      <button
                        type="button"
                        onClick={() => disableTrigger(t)}
                        className="text-aegisaxiom-text-muted hover:text-red-500 p-1"
                        title="Disable trigger"
                        data-testid={`contract-billing-triggers-disable-${t.trigger_id}`}
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    )}
                  </div>
                </div>
                {t.label && (
                  <p className="text-[11px] text-aegisaxiom-text-secondary">{t.label}</p>
                )}
                {(t.last_fired_at || t.last_fire_reason) && (
                  <p className="text-[10px] text-aegisaxiom-text-muted mt-1">
                    {t.last_fired_at && <>Last fired {fmtDate(t.last_fired_at)}</>}
                    {t.last_fired_at && t.last_fire_reason && ' · '}
                    {t.last_fire_reason}
                  </p>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
