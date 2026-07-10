/**
 * ContractAdjustmentsPanel — Credit/debit adjustments on a contract.
 *
 * Wires the existing Wave-4 backend adjustment routes to the
 * contract detail surface so finance can apply credit / debit
 * corrections outside the time-entry-approval pathway without
 * needing API tooling.
 *
 * Backend routes consumed (no inventions):
 *   GET  /api/contracts/{id}/adjustments
 *   POST /api/contracts/{id}/adjustments
 *
 * Permissions (mirror backend/routes/contract_adjustments.py):
 *   - view list   : same RBAC filter as the contract itself
 *     (contracts.view / contracts.view.own / contracts.view.all /
 *     contracts.edit / contracts.approve). The route returns 404
 *     when RBAC excludes the caller, never 403, so we just render
 *     "no adjustments" when the fetch fails on permission.
 *   - create      : contracts.approve OR contracts.edit.all OR '*'.
 *
 * Request body for POST (matches _validate_payload verbatim):
 *   {
 *     kind: "hours_credit" | "hours_debit" |
 *           "billing_credit" | "billing_debit",
 *     amount: number > 0,
 *     reason_code: "late_timesheet" | "scope_change" |
 *                  "customer_credit" | "correction" | "goodwill" |
 *                  "manual_reconcile" | "other",
 *     reason_note?: string,
 *     linked_time_entry_id?: string,
 *   }
 *
 * The backend deliberately does not expose PATCH or DELETE on
 * adjustments — every adjustment is an immutable audit row with
 * before/after snapshots. This panel matches that contract: list
 * + create only, no edit/delete controls.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { Plus, RefreshCw, AlertTriangle, Calendar, User, Hash } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';

const API = API_URL;

// Mirror backend VALID_ADJUSTMENT_KINDS — change here only when the
// backend's frozenset changes (which would surface in
// test_adjustments_panel_uses_canonical_kinds below).
const ADJUSTMENT_KINDS = [
  { value: 'hours_credit', label: 'Hours credit', tone: 'emerald' },
  { value: 'hours_debit', label: 'Hours debit', tone: 'red' },
  { value: 'billing_credit', label: 'Billing credit', tone: 'emerald' },
  { value: 'billing_debit', label: 'Billing debit', tone: 'red' },
];

// Mirror backend VALID_REASON_CODES.
const REASON_CODES = [
  { value: 'late_timesheet', label: 'Late timesheet' },
  { value: 'scope_change', label: 'Scope change' },
  { value: 'customer_credit', label: 'Customer credit' },
  { value: 'correction', label: 'Correction' },
  { value: 'goodwill', label: 'Goodwill' },
  { value: 'manual_reconcile', label: 'Manual reconcile' },
  { value: 'other', label: 'Other' },
];

const TONE_CLASSES = {
  emerald:
    'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border border-emerald-300/40',
  red: 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300 border border-red-300/40',
  amber:
    'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border border-amber-300/40',
  neutral:
    'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border',
};

function KindBadge({ kind }) {
  const meta = ADJUSTMENT_KINDS.find((k) => k.value === kind);
  const cls = TONE_CLASSES[meta?.tone] || TONE_CLASSES.neutral;
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium ${cls}`}
      data-testid={`adjustment-kind-${kind}`}
    >
      {meta?.label || kind}
    </span>
  );
}

function ReasonBadge({ reasonCode }) {
  const meta = REASON_CODES.find((r) => r.value === reasonCode);
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${TONE_CLASSES.neutral}`}>
      {meta?.label || reasonCode || '—'}
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

function fmtAmount(kind, amount) {
  const n = Number(amount) || 0;
  if (kind === 'hours_credit' || kind === 'hours_debit') {
    return `${n.toFixed(2)} h`;
  }
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(n);
}

const EMPTY_FORM = {
  kind: 'hours_credit',
  amount: '',
  reason_code: 'correction',
  reason_note: '',
  linked_time_entry_id: '',
};

export default function ContractAdjustmentsPanel({ contract, onContractUpdated }) {
  const { hasPermission } = usePermissions();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [showForm, setShowForm] = useState(false);

  // Per backend create gate. Same union the route checks.
  const canCreate =
    hasPermission('contracts.approve')
    || hasPermission('contracts.edit.all')
    || hasPermission('*');

  const contractId = contract?.contract_id;

  const loadAdjustments = useCallback(async () => {
    if (!contractId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.get(`${API}/api/contracts/${contractId}/adjustments`);
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        setError(
          typeof detail?.detail === 'string'
            ? detail.detail
            : `Failed to load adjustments (HTTP ${res.status}).`,
        );
        setItems([]);
        return;
      }
      const data = await res.json();
      setItems(Array.isArray(data?.items) ? data.items : []);
    } catch (e) {
      setError(e?.message || 'Failed to load adjustments.');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [contractId]);

  useEffect(() => {
    loadAdjustments();
  }, [loadAdjustments]);

  // Client-side validation mirrors backend _validate_payload so the
  // user sees the error before the network round-trip. We do NOT
  // duplicate the backend's full set of rules — only those the user
  // can directly violate via the form.
  const amountNumber = Number(form.amount);
  const amountValid = Number.isFinite(amountNumber) && amountNumber > 0;
  const kindValid = ADJUSTMENT_KINDS.some((k) => k.value === form.kind);
  const reasonValid = REASON_CODES.some((r) => r.value === form.reason_code);
  const formValid = amountValid && kindValid && reasonValid;

  const submit = async (e) => {
    e?.preventDefault?.();
    if (!formValid || !contractId) return;
    setSubmitting(true);
    try {
      const body = {
        kind: form.kind,
        amount: amountNumber,
        reason_code: form.reason_code,
      };
      const note = (form.reason_note || '').trim();
      if (note) body.reason_note = note;
      const linked = (form.linked_time_entry_id || '').trim();
      if (linked) body.linked_time_entry_id = linked;

      const res = await api.post(
        `${API}/api/contracts/${contractId}/adjustments`,
        body,
      );
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        const message = typeof detail?.detail === 'string'
          ? detail.detail
          : 'Failed to record adjustment';
        toast.error(message);
        return;
      }
      const payload = await res.json();
      const kindLabel = ADJUSTMENT_KINDS.find((k) => k.value === form.kind)?.label || form.kind;
      toast.success(`${kindLabel} recorded`);
      // Triggers may have fired on the backend side — surface a hint.
      if (payload?.triggers?.fired?.length) {
        toast.success(`${payload.triggers.fired.length} billing trigger(s) fired`);
      }
      setForm(EMPTY_FORM);
      setShowForm(false);
      // Refresh the list + ask the parent to refresh the contract so
      // hours_consumed / financial_commit etc. reflect the new state.
      loadAdjustments();
      onContractUpdated?.();
    } catch (err) {
      toast.error('Failed to record adjustment');
    } finally {
      setSubmitting(false);
    }
  };

  const summary = useMemo(() => {
    return items.reduce(
      (acc, a) => {
        acc.count += 1;
        if (a.kind === 'hours_credit') acc.hoursCredit += Number(a.amount) || 0;
        else if (a.kind === 'hours_debit') acc.hoursDebit += Number(a.amount) || 0;
        else if (a.kind === 'billing_credit') acc.billingCredit += Number(a.amount) || 0;
        else if (a.kind === 'billing_debit') acc.billingDebit += Number(a.amount) || 0;
        return acc;
      },
      { count: 0, hoursCredit: 0, hoursDebit: 0, billingCredit: 0, billingDebit: 0 },
    );
  }, [items]);

  if (!contractId) return null;

  return (
    <div className="glass-card p-4" data-testid="contract-adjustments-panel">
      <div className="flex items-center justify-between gap-2 flex-wrap mb-3">
        <h3 className="text-sm font-semibold text-aegisaxiom-text flex items-center gap-2">
          <Hash size={16} className="text-violet-500" />
          Adjustments
          {summary.count > 0 && (
            <span className="text-[11px] font-normal text-aegisaxiom-text-muted">
              ({summary.count})
            </span>
          )}
        </h3>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={loadAdjustments}
            className="inline-flex items-center gap-1 px-2 py-1 rounded border border-aegisaxiom-border hover:bg-aegisaxiom-elevated text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
            data-testid="contract-adjustments-refresh"
          >
            <RefreshCw className="w-3 h-3" />
            Refresh
          </button>
          {canCreate && (
            <button
              type="button"
              onClick={() => setShowForm((v) => !v)}
              className="inline-flex items-center gap-1 px-2 py-1 rounded bg-violet-600 hover:bg-violet-500 text-white text-xs"
              data-testid="contract-adjustments-toggle-form"
            >
              <Plus className="w-3 h-3" />
              {showForm ? 'Cancel' : 'New adjustment'}
            </button>
          )}
        </div>
      </div>

      {error && (
        <div
          className="flex items-center gap-2 p-2 mb-3 bg-red-50 dark:bg-red-600/10 border border-red-300/40 rounded text-red-700 dark:text-red-300 text-xs"
          data-testid="contract-adjustments-error"
        >
          <AlertTriangle className="w-3.5 h-3.5" />
          <span>{error}</span>
        </div>
      )}

      {showForm && canCreate && (
        <form
          onSubmit={submit}
          className="bg-aegisaxiom-elevated/40 border border-aegisaxiom-border rounded p-3 mb-3 space-y-2"
          data-testid="contract-adjustments-form"
        >
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-[11px] text-aegisaxiom-text-secondary mb-0.5">Kind *</label>
              <select
                value={form.kind}
                onChange={(e) => setForm((p) => ({ ...p, kind: e.target.value }))}
                className="aegisaxiom-input w-full text-xs"
                data-testid="contract-adjustments-kind"
              >
                {ADJUSTMENT_KINDS.map((k) => (
                  <option key={k.value} value={k.value}>{k.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[11px] text-aegisaxiom-text-secondary mb-0.5">Reason code *</label>
              <select
                value={form.reason_code}
                onChange={(e) => setForm((p) => ({ ...p, reason_code: e.target.value }))}
                className="aegisaxiom-input w-full text-xs"
                data-testid="contract-adjustments-reason-code"
              >
                {REASON_CODES.map((r) => (
                  <option key={r.value} value={r.value}>{r.label}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-[11px] text-aegisaxiom-text-secondary mb-0.5">
                Amount *{' '}
                <span className="text-aegisaxiom-text-muted">
                  ({form.kind?.startsWith('hours') ? 'hours' : 'dollars'}, must be &gt; 0)
                </span>
              </label>
              <input
                type="number"
                min="0"
                step="0.01"
                value={form.amount}
                onChange={(e) => setForm((p) => ({ ...p, amount: e.target.value }))}
                className="aegisaxiom-input w-full text-xs"
                placeholder="0.00"
                data-testid="contract-adjustments-amount"
              />
              {form.amount !== '' && !amountValid && (
                <p className="text-[10px] text-red-500 mt-0.5" data-testid="contract-adjustments-amount-error">
                  Amount must be a number greater than zero.
                </p>
              )}
            </div>
            <div>
              <label className="block text-[11px] text-aegisaxiom-text-secondary mb-0.5">
                Linked time entry (optional)
              </label>
              <input
                type="text"
                value={form.linked_time_entry_id}
                onChange={(e) => setForm((p) => ({ ...p, linked_time_entry_id: e.target.value }))}
                className="aegisaxiom-input w-full text-xs"
                placeholder="entry_…"
                data-testid="contract-adjustments-linked-entry"
              />
            </div>
          </div>
          <div>
            <label className="block text-[11px] text-aegisaxiom-text-secondary mb-0.5">
              Reason note (optional)
            </label>
            <textarea
              value={form.reason_note}
              onChange={(e) => setForm((p) => ({ ...p, reason_note: e.target.value }))}
              rows={2}
              className="aegisaxiom-input w-full text-xs"
              placeholder="Context for the audit log."
              data-testid="contract-adjustments-reason-note"
            />
          </div>
          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={() => {
                setShowForm(false);
                setForm(EMPTY_FORM);
              }}
              className="px-3 py-1.5 text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!formValid || submitting}
              className="px-3 py-1.5 text-xs rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white"
              data-testid="contract-adjustments-submit"
            >
              {submitting ? 'Recording…' : 'Record adjustment'}
            </button>
          </div>
        </form>
      )}

      <div className="space-y-2">
        {loading ? (
          <p
            className="text-xs text-aegisaxiom-text-muted text-center py-4"
            data-testid="contract-adjustments-loading"
          >
            Loading…
          </p>
        ) : items.length === 0 ? (
          <p
            className="text-xs text-aegisaxiom-text-muted text-center py-4"
            data-testid="contract-adjustments-empty"
          >
            No adjustments recorded on this contract.
          </p>
        ) : (
          items.map((a) => (
            <div
              key={a.adjustment_id}
              className="bg-aegisaxiom-elevated/30 border border-aegisaxiom-border rounded p-2.5"
              data-testid={`contract-adjustments-row-${a.adjustment_id}`}
            >
              <div className="flex items-center justify-between gap-2 flex-wrap mb-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <KindBadge kind={a.kind} />
                  <span className="text-sm font-semibold text-aegisaxiom-text">
                    {fmtAmount(a.kind, a.amount)}
                  </span>
                  <ReasonBadge reasonCode={a.reason_code} />
                </div>
                <span className="text-[10px] text-aegisaxiom-text-muted inline-flex items-center gap-1">
                  <Calendar className="w-3 h-3" />
                  {fmtDate(a.created_at)}
                </span>
              </div>
              {a.reason_note && (
                <p className="text-[11px] text-aegisaxiom-text-secondary whitespace-pre-wrap">{a.reason_note}</p>
              )}
              <div className="flex items-center gap-3 mt-1 text-[10px] text-aegisaxiom-text-muted">
                {a.actor_email && (
                  <span className="inline-flex items-center gap-1">
                    <User className="w-3 h-3" />
                    {a.actor_email}
                  </span>
                )}
                {a.linked_time_entry_id && (
                  <span className="font-mono">
                    linked: {a.linked_time_entry_id}
                  </span>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
