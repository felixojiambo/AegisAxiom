/**
 * InvoiceBatchesPage — Bi-weekly invoice batch approval queue.
 *
 * Operational entry point for Finance to review and approve the
 * bi-weekly invoice batches the backend builds from contract
 * time-entry billing snapshots. The backend already exposes the
 * full lifecycle (build, list, get, approve, reject) at
 * /api/invoice-batches; this page is the first UI surface for that
 * flow so finance no longer needs API tooling to release the
 * recurring billing cadence.
 *
 * Permissions (match backend/routes/invoice_batches.py):
 *   - view    : invoice_batches.view OR invoice_batches.approve OR
 *               invoice_batches.build OR finance.portal OR
 *               contracts.view.all OR *
 *   - approve : invoice_batches.approve OR finance.portal OR
 *               contracts.approve OR *
 *
 * Decisions:
 *   - Default filter = pending_finance_approval (the actionable
 *     queue). Status pill in the toolbar lets the user switch.
 *   - Approve action takes optional notes; Reject requires notes
 *     (the route returns 400 without them so the UI enforces too).
 *   - Re-approve idempotency: when the route returns
 *     already_approved=true, the toast says "Already approved" and
 *     the list still refreshes — no error.
 *   - Manual build trigger (added in a later commit) lets an
 *     operator run the bi-weekly build for an explicit period
 *     without waiting for a scheduler. The button is gated by the
 *     same invoice_batches.build / finance.portal / contracts.approve
 *     union the backend route uses.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';
import {
  CheckCircle,
  XCircle,
  FileText,
  RefreshCw,
  AlertTriangle,
  Calendar,
  DollarSign,
  Clock,
  Hammer,
  Activity,
  ShieldOff,
  Server,
} from 'lucide-react';

const API = API_URL;

const fmtMoney = (n) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(Number(n) || 0);

const fmtHours = (n) => `${(Number(n) || 0).toFixed(1)}h`;

const fmtDate = (iso) => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString();
  } catch (_) {
    return iso;
  }
};

const STATUS_META = {
  draft: {
    label: 'Draft',
    className:
      'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border',
  },
  pending_finance_approval: {
    label: 'Pending Approval',
    className:
      'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border border-amber-300/40',
  },
  approved: {
    label: 'Approved',
    className:
      'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border border-emerald-300/40',
  },
  rejected: {
    label: 'Rejected',
    className:
      'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300 border border-red-300/40',
  },
  sent_to_qbo: {
    label: 'Sent to QBO',
    className:
      'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300 border border-blue-300/40',
  },
};

const STATUS_FILTERS = [
  ['pending_finance_approval', 'Pending'],
  ['draft', 'Draft'],
  ['approved', 'Approved'],
  ['rejected', 'Rejected'],
  ['', 'All'],
];

function StatusPill({ status }) {
  const meta = STATUS_META[status] || STATUS_META.draft;
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium ${meta.className}`}
      data-testid={`batch-status-${status}`}
    >
      {meta.label}
    </span>
  );
}

function DecisionModal({ open, mode, batch, onClose, onConfirm, busy }) {
  const [notes, setNotes] = useState('');
  useEffect(() => {
    if (open) setNotes('');
  }, [open, batch?.batch_id]);

  if (!open || !batch) return null;
  const isReject = mode === 'reject';
  const title = isReject ? 'Reject invoice batch' : 'Approve invoice batch';
  const submitLabel = isReject ? 'Reject batch' : 'Approve batch';
  const notesRequired = isReject;
  const canSubmit = !busy && (!notesRequired || notes.trim().length > 0);

  return (
    <div
      className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4"
      role="dialog"
      aria-modal="true"
      data-testid="batch-decision-modal"
    >
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-lg">
        <div className="p-5 border-b border-aegisaxiom-border">
          <h3 className="text-base font-semibold text-aegisaxiom-text">{title}</h3>
          <p className="text-xs text-aegisaxiom-text-secondary mt-1">
            Batch <span className="font-mono">{batch.batch_id}</span> &middot;{' '}
            {batch.contract_name || batch.contract_id} &middot;{' '}
            {fmtDate(batch.period_start)} → {fmtDate(batch.period_end)}
          </p>
        </div>
        <div className="p-5 space-y-3">
          <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">
            {isReject ? 'Reason for rejection (required)' : 'Notes (optional)'}
          </label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={4}
            className="aegisaxiom-input w-full text-sm"
            placeholder={
              isReject
                ? 'Explain why this batch is being rejected so the builder can fix it.'
                : 'Optional context for the audit log.'
            }
            data-testid="batch-decision-notes"
          />
          <p className="text-[11px] text-aegisaxiom-text-muted">
            {isReject
              ? 'Rejection notes are stored on the batch and written to the audit log.'
              : 'Approval promotes this batch to a finance_drafts row keyed by batch_id. Re-approving is idempotent — no duplicate draft is created.'}
          </p>
        </div>
        <div className="p-4 border-t border-aegisaxiom-border flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onConfirm(notes)}
            disabled={!canSubmit}
            className={`px-3 py-1.5 text-sm rounded transition ${
              isReject
                ? 'bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white'
                : 'bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white'
            }`}
            data-testid={`batch-decision-${mode}-confirm`}
          >
            {busy ? 'Working…' : submitLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

// Returns YYYY-MM-DD for a given JS Date. Backend
// /invoice-batches/build accepts ISO YYYY-MM-DD strings (see
// backend/routes/invoice_batches.py:139-143).
function isoDate(d) {
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

// Default to the most recent bi-weekly cadence ending yesterday so a
// fresh open of the modal pre-fills a sane period the user can adjust
// or accept. The cadence is 14 days; backend doesn't enforce the
// length but the operator expectation matches the route's
// build-biweekly alias.
function defaultBiweeklyPeriod() {
  const end = new Date();
  end.setDate(end.getDate() - 1);
  const start = new Date(end);
  start.setDate(end.getDate() - 13);
  return { period_start: isoDate(start), period_end: isoDate(end) };
}

function BuildModal({ open, onClose, onConfirm, busy }) {
  const [{ period_start, period_end }, setPeriod] = useState(defaultBiweeklyPeriod);
  const [contractId, setContractId] = useState('');
  const [dryRun, setDryRun] = useState(true);

  useEffect(() => {
    if (open) {
      setPeriod(defaultBiweeklyPeriod());
      setContractId('');
      setDryRun(true);
    }
  }, [open]);

  if (!open) return null;

  const canSubmit = !busy && !!period_start && !!period_end && period_start <= period_end;

  return (
    <div
      className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4"
      role="dialog"
      aria-modal="true"
      data-testid="batch-build-modal"
    >
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-lg">
        <div className="p-5 border-b border-aegisaxiom-border">
          <h3 className="text-base font-semibold text-aegisaxiom-text">Build invoice batches</h3>
          <p className="text-xs text-aegisaxiom-text-secondary mt-1">
            Materializes the bi-weekly batches for the selected period.
            Defaults to the most recent fortnight; dry-run is on by default
            so you can review what would be built before committing.
          </p>
        </div>
        <div className="p-5 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] text-aegisaxiom-text-secondary mb-1">
                Period start *
              </label>
              <input
                type="date"
                value={period_start}
                onChange={(e) => setPeriod((p) => ({ ...p, period_start: e.target.value }))}
                className="aegisaxiom-input w-full text-sm"
                data-testid="batch-build-period-start"
              />
            </div>
            <div>
              <label className="block text-[11px] text-aegisaxiom-text-secondary mb-1">
                Period end *
              </label>
              <input
                type="date"
                value={period_end}
                onChange={(e) => setPeriod((p) => ({ ...p, period_end: e.target.value }))}
                className="aegisaxiom-input w-full text-sm"
                data-testid="batch-build-period-end"
              />
            </div>
          </div>
          <div>
            <label className="block text-[11px] text-aegisaxiom-text-secondary mb-1">
              Limit to contract (optional)
            </label>
            <input
              type="text"
              value={contractId}
              onChange={(e) => setContractId(e.target.value)}
              placeholder="contract_…"
              className="aegisaxiom-input w-full text-sm font-mono"
              data-testid="batch-build-contract-id"
            />
          </div>
          <label className="inline-flex items-center gap-2 cursor-pointer text-xs text-aegisaxiom-text">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              data-testid="batch-build-dry-run"
            />
            Dry run — preview the build without persisting batches
          </label>
        </div>
        <div className="p-4 border-t border-aegisaxiom-border flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onConfirm({
              period_start,
              period_end,
              contract_id: contractId.trim() || undefined,
              dry_run: dryRun,
            })}
            disabled={!canSubmit}
            className="px-3 py-1.5 text-sm rounded bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white"
            data-testid="batch-build-submit"
          >
            {busy ? 'Building…' : dryRun ? 'Dry-run build' : 'Build batches'}
          </button>
        </div>
      </div>
    </div>
  );
}

function SchedulerStatusCard({ status, loading, onRefresh }) {
  const health = status?.health || (loading ? 'loading' : 'unknown');
  const meta = {
    ok: {
      label: 'OK',
      className:
        'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border border-emerald-300/40',
      icon: CheckCircle,
    },
    no_success_yet: {
      label: 'No success yet',
      className:
        'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300 border border-blue-300/40',
      icon: Activity,
    },
    not_configured: {
      label: 'Not configured',
      className:
        'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border',
      icon: ShieldOff,
    },
    critical: {
      label: 'Stale — last success older than threshold',
      className:
        'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300 border border-red-300/40',
      icon: AlertTriangle,
    },
    warning: {
      label: 'Warning',
      className:
        'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border border-amber-300/40',
      icon: AlertTriangle,
    },
    loading: {
      label: 'Loading…',
      className:
        'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border',
      icon: RefreshCw,
    },
    unknown: {
      label: 'Unavailable',
      className:
        'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border',
      icon: ShieldOff,
    },
  }[health] || {
    label: String(health),
    className: 'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border',
    icon: ShieldOff,
  };
  const HealthIcon = meta.icon;

  return (
    <div
      className="glass-card p-3"
      data-testid="invoice-batch-scheduler-status-card"
    >
      <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
        <div className="flex items-center gap-2">
          <Server className="w-4 h-4 text-violet-500" />
          <p className="text-[11px] uppercase tracking-wide text-aegisaxiom-text-muted">
            Scheduled bi-weekly build
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-aegisaxiom-border hover:bg-aegisaxiom-elevated text-[11px] text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
          data-testid="invoice-batch-scheduler-refresh"
        >
          <RefreshCw className="w-3 h-3" />
          Refresh
        </button>
      </div>
      <div className="flex items-center gap-2 mb-2">
        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium ${meta.className}`}
          data-testid="invoice-batch-scheduler-health"
        >
          <HealthIcon className="w-3 h-3" />
          {meta.label}
        </span>
        {status?.stale_after_hours && (
          <span className="text-[10px] text-aegisaxiom-text-muted">
            stale after {status.stale_after_hours}h
          </span>
        )}
      </div>
      <div className="text-[11px] text-aegisaxiom-text-secondary space-y-1">
        {status?.last_success ? (
          <div data-testid="invoice-batch-scheduler-last-success">
            Last success:{' '}
            <span className="text-aegisaxiom-text">
              {fmtDateRelative(status.last_success.finished_at, status.last_success_age_hours)}
            </span>
            {status.last_success.batches_count != null && (
              <>
                {' '}
                — {status.last_success.batches_count} batch
                {status.last_success.batches_count === 1 ? '' : 'es'}
                {(status.last_success.skipped_count || 0) > 0 && (
                  <>, {status.last_success.skipped_count} skipped</>
                )}
                {(status.last_success.warnings_count || 0) > 0 && (
                  <>, {status.last_success.warnings_count} warning(s)</>
                )}
              </>
            )}
          </div>
        ) : (
          <div data-testid="invoice-batch-scheduler-last-success">
            Last success: <span className="text-aegisaxiom-text-muted">never</span>
          </div>
        )}
        {status?.last_failure ? (
          <div
            className="text-red-600 dark:text-red-400"
            data-testid="invoice-batch-scheduler-last-failure"
          >
            Last failure:{' '}
            {fmtDateRelative(status.last_failure.finished_at)}
            {status.last_failure.error && (
              <> — {status.last_failure.error}</>
            )}
          </div>
        ) : (
          <div data-testid="invoice-batch-scheduler-last-failure">
            Last failure: <span className="text-aegisaxiom-text-muted">none recorded</span>
          </div>
        )}
      </div>
    </div>
  );
}

function fmtDateRelative(iso, ageHours) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    const local = d.toLocaleString();
    if (ageHours != null && Number.isFinite(ageHours)) {
      if (ageHours < 1) return `${local} (just now)`;
      if (ageHours < 24) return `${local} (${Math.round(ageHours)}h ago)`;
      const days = ageHours / 24;
      return `${local} (${days.toFixed(1)}d ago)`;
    }
    return local;
  } catch (_) {
    return String(iso);
  }
}

export default function InvoiceBatchesPage() {
  const { hasPermission } = usePermissions();

  const canView =
    hasPermission('invoice_batches.view') ||
    hasPermission('invoice_batches.approve') ||
    hasPermission('invoice_batches.build') ||
    hasPermission('finance.portal') ||
    hasPermission('contracts.view.all') ||
    hasPermission('*');

  const canApprove =
    hasPermission('invoice_batches.approve') ||
    hasPermission('finance.portal') ||
    hasPermission('contracts.approve') ||
    hasPermission('*');

  // Manual-build permission union — mirrors _can_build in
  // backend/routes/invoice_batches.py:72-82. When the scheduler
  // exists this button is the manual fallback; today it is the
  // only way to materialize a batch outside hitting the API.
  const canBuild =
    hasPermission('invoice_batches.build') ||
    hasPermission('finance.portal') ||
    hasPermission('contracts.approve') ||
    hasPermission('*');

  const [statusFilter, setStatusFilter] = useState('pending_finance_approval');
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [decision, setDecision] = useState({ open: false, mode: null, batch: null });
  const [busy, setBusy] = useState(false);
  // Build trigger state — separate from `busy` so the user can
  // queue a build while a decision modal is open without state bleed.
  const [buildOpen, setBuildOpen] = useState(false);
  const [buildBusy, setBuildBusy] = useState(false);

  const loadBatches = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (statusFilter) params.append('status', statusFilter);
      const res = await api.get(`${API}/api/invoice-batches?${params.toString()}`);
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        setError(detail?.detail || `Failed to load batches (HTTP ${res.status}).`);
        setItems([]);
        return;
      }
      const data = await res.json();
      setItems(Array.isArray(data.items) ? data.items : []);
    } catch (e) {
      setError(e?.message || 'Failed to load invoice batches.');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    if (!canView) return;
    loadBatches();
  }, [canView, loadBatches]);

  const totals = useMemo(() => {
    return items.reduce(
      (acc, b) => {
        acc.amount += Number(b.total_amount) || 0;
        acc.hours += Number(b.total_hours) || 0;
        acc.count += 1;
        return acc;
      },
      { amount: 0, hours: 0, count: 0 },
    );
  }, [items]);

  // Scheduler status — small operational read so operators can
  // verify the external scheduler is running, when it last
  // succeeded, and whether its run is stale.
  const [schedulerStatus, setSchedulerStatus] = useState(null);
  const [schedulerStatusLoading, setSchedulerStatusLoading] = useState(false);
  const loadSchedulerStatus = useCallback(async () => {
    if (!canView) return;
    setSchedulerStatusLoading(true);
    try {
      const res = await api.get(`${API}/api/invoice-batches/scheduler/status`);
      if (!res.ok) {
        // 403 / 404 etc. mean we couldn't read; surface nothing
        // rather than a noisy toast — the status card is operational
        // context, not actionable user input.
        setSchedulerStatus(null);
        return;
      }
      const data = await res.json();
      setSchedulerStatus(data);
    } catch (_e) {
      setSchedulerStatus(null);
    } finally {
      setSchedulerStatusLoading(false);
    }
  }, [canView]);

  useEffect(() => {
    loadSchedulerStatus();
  }, [loadSchedulerStatus]);

  const handleAct = (batch, mode) => {
    setDecision({ open: true, mode, batch });
  };

  const closeDecision = () => setDecision({ open: false, mode: null, batch: null });

  const confirmDecision = async (notes) => {
    const { batch, mode } = decision;
    if (!batch || !mode) return;
    setBusy(true);
    try {
      const url = `${API}/api/invoice-batches/${batch.batch_id}/${mode}`;
      const body = notes && notes.trim() ? { notes: notes.trim() } : {};
      const res = await api.post(url, body);
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        toast.error(detail?.detail || `Failed to ${mode} batch`);
        return;
      }
      const payload = await res.json();
      // Idempotent re-approve / re-reject: the route flags it so the
      // toast is informative rather than alarming.
      if (mode === 'approve' && payload?.already_approved) {
        toast.success('Batch was already approved');
      } else if (mode === 'reject' && payload?.already_rejected) {
        toast.success('Batch was already rejected');
      } else if (mode === 'approve') {
        toast.success('Batch approved and queued as finance draft');
      } else {
        toast.success('Batch rejected');
      }
      closeDecision();
      loadBatches();
    } catch (e) {
      toast.error(`Failed to ${mode} batch`);
    } finally {
      setBusy(false);
    }
  };

  const confirmBuild = async ({ period_start, period_end, contract_id, dry_run }) => {
    setBuildBusy(true);
    try {
      // POST body matches routes/invoice_batches.py:132-145 verbatim —
      // period_start / period_end required (ISO YYYY-MM-DD), optional
      // contract_id filter, optional dry_run boolean. The /build
      // endpoint and the /build-biweekly alias share the same body
      // (line 184-186); we hit /build because the cadence is
      // operator-driven for now (see docs/contracts/scheduler.md).
      const body = { period_start, period_end, dry_run: !!dry_run };
      if (contract_id) body.contract_id = contract_id;
      const res = await api.post(`${API}/api/invoice-batches/build`, body);
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        toast.error(
          typeof detail?.detail === 'string'
            ? detail.detail
            : 'Failed to build batches',
        );
        return;
      }
      const payload = await res.json();
      const created = Array.isArray(payload?.batches) ? payload.batches.length : 0;
      const skipped = Array.isArray(payload?.skipped) ? payload.skipped.length : 0;
      const warnings = Array.isArray(payload?.warnings) ? payload.warnings.length : 0;
      const dryRunLabel = payload?.dry_run ? ' (dry run)' : '';
      if (created === 0) {
        toast.success(`No batches built${dryRunLabel}${skipped ? ` — ${skipped} skipped` : ''}`);
      } else {
        toast.success(
          `${created} batch(es) built${dryRunLabel}` +
            (skipped ? ` — ${skipped} skipped` : '') +
            (warnings ? `, ${warnings} warning(s)` : ''),
        );
      }
      setBuildOpen(false);
      // Always refresh — even dry runs may have flipped status
      // filters' visible totals via concurrent activity, and a real
      // build creates rows that should appear in the table.
      loadBatches();
    } catch (e) {
      toast.error('Failed to build batches');
    } finally {
      setBuildBusy(false);
    }
  };

  if (!canView) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-aegisaxiom-text-muted">
          You do not have permission to view invoice batches.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-5 p-6" data-testid="invoice-batches-page">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <FileText className="w-6 h-6 text-violet-500" />
          <div>
            <h1 className="text-xl font-semibold text-aegisaxiom-text">
              Invoice batches
            </h1>
            <p className="text-xs text-aegisaxiom-text-secondary">
              Bi-weekly billing batches built from contract time-entry snapshots.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {canBuild && (
            <button
              type="button"
              onClick={() => setBuildOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-violet-600 hover:bg-violet-500 text-white text-sm"
              data-testid="batch-build-open"
            >
              <Hammer className="w-3.5 h-3.5" />
              Build batches
            </button>
          )}
          <button
            type="button"
            onClick={loadBatches}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded border border-aegisaxiom-border hover:bg-aegisaxiom-elevated text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
            data-testid="batch-refresh"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Refresh
          </button>
        </div>
      </div>

      {/* Status filter chips */}
      <div className="flex items-center gap-1 flex-wrap" data-testid="batch-status-filter">
        {STATUS_FILTERS.map(([value, label]) => (
          <button
            key={value || 'all'}
            type="button"
            onClick={() => setStatusFilter(value)}
            className={`px-2.5 py-1 rounded text-xs font-medium transition border ${
              statusFilter === value
                ? 'bg-violet-600 text-white border-violet-500'
                : 'bg-aegisaxiom-base text-aegisaxiom-text-secondary border-aegisaxiom-border hover:text-aegisaxiom-text'
            }`}
            data-testid={`batch-filter-${value || 'all'}`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Scheduler status — small operational card so operators can
          verify the bi-weekly cadence is healthy without leaving the
          page. */}
      <SchedulerStatusCard
        status={schedulerStatus}
        loading={schedulerStatusLoading}
        onRefresh={loadSchedulerStatus}
      />

      {/* Summary tiles */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <div className="glass-card p-3">
          <p className="text-[11px] uppercase tracking-wide text-aegisaxiom-text-muted">Batches</p>
          <p className="text-lg font-semibold text-aegisaxiom-text">{totals.count}</p>
        </div>
        <div className="glass-card p-3">
          <p className="text-[11px] uppercase tracking-wide text-aegisaxiom-text-muted">Total amount</p>
          <p className="text-lg font-semibold text-aegisaxiom-text">{fmtMoney(totals.amount)}</p>
        </div>
        <div className="glass-card p-3">
          <p className="text-[11px] uppercase tracking-wide text-aegisaxiom-text-muted">Total hours</p>
          <p className="text-lg font-semibold text-aegisaxiom-text">{fmtHours(totals.hours)}</p>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 glass-card text-red-600 dark:text-red-400 text-sm">
          <AlertTriangle className="w-4 h-4" />
          <span>{error}</span>
        </div>
      )}

      <div className="glass-card overflow-hidden">
        <table className="w-full text-sm" data-testid="batch-table">
          <thead>
            <tr className="border-b border-aegisaxiom-border text-left text-aegisaxiom-text-muted text-xs uppercase tracking-wide">
              <th className="px-4 py-2 font-medium">Contract</th>
              <th className="px-4 py-2 font-medium">Period</th>
              <th className="px-4 py-2 font-medium text-right">Hours</th>
              <th className="px-4 py-2 font-medium text-right">Amount</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Decided</th>
              <th className="px-4 py-2 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-aegisaxiom-text-muted">
                  Loading…
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-aegisaxiom-text-muted">
                  No invoice batches{statusFilter ? ` with status "${STATUS_META[statusFilter]?.label || statusFilter}"` : ''}.
                </td>
              </tr>
            ) : (
              items.map((b) => {
                const isActionable =
                  canApprove
                  && (b.status === 'pending_finance_approval' || b.status === 'draft');
                return (
                  <tr
                    key={b.batch_id}
                    className="border-b border-aegisaxiom-border/40 hover:bg-aegisaxiom-surface/40 transition-colors"
                    data-testid={`batch-row-${b.batch_id}`}
                  >
                    <td className="px-4 py-2.5">
                      <p className="font-medium text-aegisaxiom-text">
                        {b.contract_name || b.contract_id || '—'}
                      </p>
                      <p className="text-[11px] text-aegisaxiom-text-muted font-mono">{b.batch_id}</p>
                    </td>
                    <td className="px-4 py-2.5 text-aegisaxiom-text">
                      <div className="inline-flex items-center gap-1.5">
                        <Calendar className="w-3.5 h-3.5 text-aegisaxiom-text-muted" />
                        <span>
                          {fmtDate(b.period_start)} → {fmtDate(b.period_end)}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-right text-aegisaxiom-text">
                      <div className="inline-flex items-center gap-1.5">
                        <Clock className="w-3.5 h-3.5 text-aegisaxiom-text-muted" />
                        <span>{fmtHours(b.total_hours)}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-right text-aegisaxiom-text">
                      <div className="inline-flex items-center gap-1.5">
                        <DollarSign className="w-3.5 h-3.5 text-aegisaxiom-text-muted" />
                        <span>{fmtMoney(b.total_amount)}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5">
                      <StatusPill status={b.status} />
                    </td>
                    <td className="px-4 py-2.5 text-aegisaxiom-text-secondary text-xs">
                      {b.decided_by ? (
                        <>
                          <p>{fmtDate(b.decided_at)}</p>
                          <p className="text-aegisaxiom-text-muted">by {b.decided_by_name || b.decided_by}</p>
                        </>
                      ) : (
                        <span className="text-aegisaxiom-text-muted">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      {isActionable ? (
                        <div className="inline-flex gap-1 justify-end">
                          <button
                            type="button"
                            onClick={() => handleAct(b, 'approve')}
                            className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded bg-emerald-600 hover:bg-emerald-500 text-white"
                            data-testid={`batch-approve-${b.batch_id}`}
                          >
                            <CheckCircle className="w-3 h-3" />
                            Approve
                          </button>
                          <button
                            type="button"
                            onClick={() => handleAct(b, 'reject')}
                            className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded bg-red-600 hover:bg-red-500 text-white"
                            data-testid={`batch-reject-${b.batch_id}`}
                          >
                            <XCircle className="w-3 h-3" />
                            Reject
                          </button>
                        </div>
                      ) : (
                        <span className="text-aegisaxiom-text-muted text-xs">—</span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <DecisionModal
        open={decision.open}
        mode={decision.mode}
        batch={decision.batch}
        onClose={closeDecision}
        onConfirm={confirmDecision}
        busy={busy}
      />

      <BuildModal
        open={buildOpen}
        onClose={() => setBuildOpen(false)}
        onConfirm={confirmBuild}
        busy={buildBusy}
      />

      <p className="text-[11px] text-aegisaxiom-text-muted text-center">
        Backend at <code>/api/invoice-batches</code>. Approval promotes a batch
        into <code>finance_drafts</code>; rejection is logged with required notes.
      </p>
    </div>
  );
}
