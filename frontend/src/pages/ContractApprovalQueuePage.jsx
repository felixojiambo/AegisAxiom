/**
 * ContractApprovalQueuePage — pending contract approval queue.
 *
 * Lists every contract waiting on a decision. Row click (or the
 * "Open contract" button) opens ContractDetailsModal; the workflow
 * panel inside that modal owns the actual approve / reject /
 * request-changes action so the queue rows never carry duplicate
 * decision buttons. After a decision the modal closes and the
 * queue refreshes, dropping the just-decided contract.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';
import {
  RefreshCw,
  ClipboardCheck,
  Calendar,
  DollarSign,
  AlertTriangle,
  User,
  Building2,
  ExternalLink,
} from 'lucide-react';
import ContractDetailsModal from '../components/contracts/modals/ContractDetailsModal';

const API = API_URL;

const fmtMoney = (n) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(Number(n) || 0);

const fmtDate = (iso) => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch (_) {
    return String(iso);
  }
};

const TYPE_LABELS = {
  boh: 'Block of Hours',
  boa: 'Bill on Actuals',
  fixed_fee: 'Fixed Fee',
  monthly: 'Monthly',
  wholesale_mrr: 'Wholesale MRR',
  simplisecure: 'SimpliSecure',
  simplicomply: 'SimpliComply',
  other: 'Other',
};

// Per-type "amount of interest" — the queue surfaces the most
// relevant money field so an approver doesn't have to click in to
// gauge the contract's size. Falls back to total_amount on unknown
// types so a new contract_type doesn't show a blank cell.
function contractValue(c) {
  if (!c) return null;
  switch (c.contract_type) {
    case 'boh':
      return c.total_amount ?? null;
    case 'boa':
      return c.total_billed ?? c.total_amount ?? null;
    case 'fixed_fee':
      return c.total_amount ?? null;
    case 'monthly':
    case 'wholesale_mrr':
      return c.monthly_rate ?? null;
    default:
      return c.total_amount ?? null;
  }
}

function StatusBadge({ status, kind }) {
  if (!status) return <span className="text-aegisaxiom-text-muted">—</span>;
  const base = 'inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium';
  // Approval status palette mirrors the workflow panel.
  if (kind === 'approval') {
    const cls = {
      pending: 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border border-amber-300/40',
      approved: 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border border-emerald-300/40',
      rejected: 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300 border border-red-300/40',
      changes_requested: 'bg-orange-50 dark:bg-orange-600/20 text-orange-700 dark:text-orange-300 border border-orange-300/40',
      draft: 'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border',
    }[status] || 'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border';
    return <span className={`${base} ${cls}`}>{status.replace('_', ' ')}</span>;
  }
  // Contract lifecycle status palette mirrors ContractDetailsModal.
  const cls = {
    active: 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border border-emerald-300/40',
    on_hold: 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border border-amber-300/40',
    completed: 'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border',
    draft: 'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border',
    expired: 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300 border border-red-300/40',
    cancelled: 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300 border border-red-300/40',
  }[status] || 'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border';
  return <span className={`${base} ${cls}`}>{status.replace('_', ' ')}</span>;
}

export default function ContractApprovalQueuePage() {
  const { hasPermission } = usePermissions();

  // Mirror the server-side permission gate so a denied user sees a
  // clear message instead of a 403 spinner.
  const canView = hasPermission('contracts.approve') || hasPermission('*');

  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [openContractId, setOpenContractId] = useState(null);

  const loadQueue = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get(`${API}/api/contracts/approval-queue`);
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        setError(
          typeof detail?.detail === 'string'
            ? detail.detail
            : 'Could not load the approval queue. Please try again.',
        );
        setItems([]);
        return;
      }
      const data = await res.json();
      setItems(Array.isArray(data?.items) ? data.items : []);
    } catch (e) {
      setError(e?.message || 'Could not load the approval queue.');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!canView) return;
    loadQueue();
  }, [canView, loadQueue]);

  const totals = useMemo(() => {
    return items.reduce(
      (acc, c) => {
        acc.count += 1;
        const v = Number(contractValue(c)) || 0;
        acc.amount += v;
        return acc;
      },
      { count: 0, amount: 0 },
    );
  }, [items]);

  const closeDetail = () => {
    // Always refresh on close so a decision made inside the modal
    // drops the row out of the pending queue. A no-op close (user
    // hit Escape without acting) just re-fetches an unchanged list.
    setOpenContractId(null);
    loadQueue();
  };

  if (!canView) {
    return (
      <div className="flex items-center justify-center h-64" data-testid="contract-approval-queue-denied">
        <p className="text-aegisaxiom-text-muted">
          You do not have permission to view the contract approval queue.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-5 p-6" data-testid="contract-approval-queue-page">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <ClipboardCheck className="w-6 h-6 text-violet-500" />
          <div>
            <h1 className="text-xl font-semibold text-aegisaxiom-text">Contract approval queue</h1>
            <p className="text-xs text-aegisaxiom-text-secondary">
              Contracts waiting on your decision. Click a row to review and approve, reject, or request changes.
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={loadQueue}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded border border-aegisaxiom-border hover:bg-aegisaxiom-elevated text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
          data-testid="contract-approval-queue-refresh"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <div className="glass-card p-3">
          <p className="text-[11px] uppercase tracking-wide text-aegisaxiom-text-muted">Pending</p>
          <p className="text-lg font-semibold text-aegisaxiom-text">{totals.count}</p>
        </div>
        <div className="glass-card p-3">
          <p className="text-[11px] uppercase tracking-wide text-aegisaxiom-text-muted">Total value</p>
          <p className="text-lg font-semibold text-aegisaxiom-text">{fmtMoney(totals.amount)}</p>
        </div>
      </div>

      {error && (
        <div
          className="flex items-center gap-2 p-3 glass-card text-red-600 dark:text-red-400 text-sm"
          data-testid="contract-approval-queue-error"
        >
          <AlertTriangle className="w-4 h-4" />
          <span>{error}</span>
        </div>
      )}

      <div className="glass-card overflow-hidden">
        <table className="w-full text-sm" data-testid="contract-approval-queue-table">
          <thead>
            <tr className="border-b border-aegisaxiom-border text-left text-aegisaxiom-text-muted text-xs uppercase tracking-wide">
              <th className="px-4 py-2 font-medium">Contract</th>
              <th className="px-4 py-2 font-medium">Account</th>
              <th className="px-4 py-2 font-medium">Type</th>
              <th className="px-4 py-2 font-medium text-right">Value</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Approval</th>
              <th className="px-4 py-2 font-medium">Submitted</th>
              <th className="px-4 py-2 font-medium text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {loading && items.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-aegisaxiom-text-muted" data-testid="contract-approval-queue-loading">
                  Loading…
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-aegisaxiom-text-muted" data-testid="contract-approval-queue-empty">
                  Nothing waiting on you right now.
                </td>
              </tr>
            ) : (
              items.map((c) => {
                const value = contractValue(c);
                const submittedAt = c.updated_at; // backend sorts by updated_at desc as the "queued at" proxy
                return (
                  <tr
                    key={c.contract_id}
                    onClick={() => setOpenContractId(c.contract_id)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        setOpenContractId(c.contract_id);
                      }
                    }}
                    className="border-b border-aegisaxiom-border/40 hover:bg-aegisaxiom-surface/40 transition-colors cursor-pointer focus:outline-none focus:bg-aegisaxiom-surface/40"
                    data-testid={`contract-approval-queue-row-${c.contract_id}`}
                  >
                    <td className="px-4 py-2.5">
                      <p className="font-medium text-aegisaxiom-text">{c.name || c.contract_id}</p>
                      <p className="text-[11px] text-aegisaxiom-text-muted font-mono">{c.contract_id}</p>
                    </td>
                    <td className="px-4 py-2.5 text-aegisaxiom-text">
                      <div className="inline-flex items-center gap-1.5">
                        <Building2 className="w-3.5 h-3.5 text-aegisaxiom-text-muted" />
                        <span>{c.account_name || '—'}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-aegisaxiom-text-secondary">
                      {TYPE_LABELS[c.contract_type] || c.contract_type || '—'}
                    </td>
                    <td className="px-4 py-2.5 text-right text-aegisaxiom-text">
                      {value !== null && value !== undefined ? (
                        <div className="inline-flex items-center gap-1.5">
                          <DollarSign className="w-3.5 h-3.5 text-aegisaxiom-text-muted" />
                          <span>{fmtMoney(value)}</span>
                        </div>
                      ) : (
                        <span className="text-aegisaxiom-text-muted">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <StatusBadge status={c.status} />
                    </td>
                    <td className="px-4 py-2.5">
                      <StatusBadge status={c.approval_status} kind="approval" />
                    </td>
                    <td className="px-4 py-2.5 text-xs text-aegisaxiom-text-secondary">
                      <div className="inline-flex items-center gap-1.5">
                        <Calendar className="w-3 h-3 text-aegisaxiom-text-muted" />
                        <span>{fmtDate(submittedAt)}</span>
                      </div>
                      {c.opportunity_owner_name && (
                        <div className="inline-flex items-center gap-1 mt-0.5 text-aegisaxiom-text-muted">
                          <User className="w-3 h-3" />
                          <span>{c.opportunity_owner_name}</span>
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenContractId(c.contract_id);
                        }}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded border border-aegisaxiom-border hover:bg-aegisaxiom-elevated text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
                        data-testid={`contract-approval-queue-open-${c.contract_id}`}
                      >
                        <ExternalLink className="w-3 h-3" />
                        Open contract
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {openContractId && (
        <ContractDetailsModal
          contractId={openContractId}
          onClose={closeDetail}
        />
      )}
    </div>
  );
}
