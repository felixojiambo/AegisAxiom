import React, { useEffect, useState } from 'react';
import { Plus, Trash2, RefreshCw, Zap } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { fmtCurrency, fmtDateRelative } from '@/lib/financeFormat';
import SyncStatusBadge from '@/components/finance/shared/SyncStatusBadge';
import ConfirmModal from '@/components/shared/ConfirmModal';

const BASE = `${API_URL}/api/finance`;

export default function SyncQueueTab() {
  const [drafts, setDrafts] = useState([]);
  const [showCreate, setShowCreate] = useState(false);
  const [draft, setDraft] = useState({ kind: 'ar_invoice', counterparty: '', amount: 0, memo: '' });
  const [confirmAR, setConfirmAR] = useState(false);
  const [confirmAP, setConfirmAP] = useState(false);
  const [periodStart, setPeriodStart] = useState('');
  const [periodEnd, setPeriodEnd] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  const refresh = async () => {
    try {
      const r = await api.get(`${BASE}/sync-queue`);
      if (!r.ok) {
        setLoadError(`Failed to load sync queue (${r.status})`);
        return;
      }
      const d = await r.json();
      setDrafts(d.rows || []);
      setLoadError(null);
    } catch (err) {
      setLoadError(err?.message || 'Failed to load sync queue');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const handleCreate = async () => {
    if (!draft.counterparty || !draft.amount) {
      toast.error('Counterparty and amount are required');
      return;
    }
    try {
      const r = await api.post(`${BASE}/drafts`, {
        ...draft,
        line_items: [{ description: draft.memo, amount: draft.amount }],
      });
      if (r.ok) {
        toast.success('Draft created');
        setDraft({ kind: 'ar_invoice', counterparty: '', amount: 0, memo: '' });
        setShowCreate(false);
        refresh();
      } else {
        toast.error('Failed to create draft');
      }
    } catch (err) {
      toast.error(err?.message || 'Failed to create draft');
    }
  };

  const handleSync = async (id) => {
    try {
      const r = await api.post(`${BASE}/drafts/${id}/sync`, {});
      if (r.ok) {
        toast.success('Synced to QBO');
        refresh();
      } else {
        toast.error('Sync failed');
      }
    } catch (err) {
      toast.error(err?.message || 'Sync failed');
    }
  };

  const handleDelete = async (id) => {
    try {
      const r = await api.delete(`${BASE}/drafts/${id}`);
      if (r.ok) {
        toast.success('Deleted');
        refresh();
      } else {
        toast.error('Failed to delete draft');
      }
    } catch (err) {
      toast.error(err?.message || 'Failed to delete draft');
    }
  };

  const handleAutoGenerateAR = async () => {
    const r = await api.post(`${BASE}/drafts/auto-generate-ar`, {
      period_start: periodStart,
      period_end: periodEnd,
    });
    if (r.ok) {
      toast.success('AR drafts generated');
      refresh();
    } else {
      toast.error('Auto-generate AR failed');
    }
  };

  const handleAutoGenerateAP = async () => {
    const r = await api.post(`${BASE}/drafts/auto-generate-ap`, {
      period_start: periodStart,
      period_end: periodEnd,
    });
    if (r.ok) {
      toast.success('AP drafts generated');
      refresh();
    } else {
      toast.error('Auto-generate AP failed');
    }
  };

  const inputCls =
    'h-9 px-3 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface text-[13px] text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted outline-none focus:border-violet-500';

  if (loading)
    return (
      <div
        className="py-20 text-center text-sm text-aegisaxiom-text-muted"
        data-testid="loading-sync-queue"
      >
        Loading…
      </div>
    );
  if (loadError)
    return (
      <div className="glass-card p-12 text-center" data-testid="sync-queue-load-error">
        <div className="text-[16px] font-semibold text-aegisaxiom-text mb-2">
          Couldn't load sync queue
        </div>
        <div className="text-[13px] text-aegisaxiom-text-secondary mb-6">{loadError}</div>
        <button
          onClick={refresh}
          className="btn-secondary inline-flex items-center gap-2 h-9 px-4 text-[13px]"
        >
          Try again
        </button>
      </div>
    );

  return (
    <div className="space-y-6" data-testid="page-sync-queue">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <p className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-0.5">
            OPERATIONS
          </p>
          <h2 className="page-title">Sync Queue</h2>
          <p className="text-sm text-aegisaxiom-text-secondary mt-1">
            Locally generated AR invoices and AP bills awaiting push-back to QuickBooks Online.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            data-testid="auto-gen-ar-btn"
            onClick={() => setConfirmAR(true)}
            className="h-9 px-3 btn-secondary flex items-center gap-1.5 text-[13px]"
          >
            <Zap className="h-3.5 w-3.5" />
            Auto-generate AR
          </button>
          <button
            data-testid="auto-gen-ap-btn"
            onClick={() => setConfirmAP(true)}
            className="h-9 px-3 btn-secondary flex items-center gap-1.5 text-[13px]"
          >
            <Zap className="h-3.5 w-3.5" />
            Auto-generate AP
          </button>
          <button
            data-testid="new-draft-btn"
            onClick={() => setShowCreate((s) => !s)}
            className="h-9 px-3 btn-primary flex items-center gap-1.5 text-[13px]"
          >
            <Plus className="h-3.5 w-3.5" />
            New draft
          </button>
        </div>
      </div>

      {/* Period selectors for auto-generate */}
      <div className="glass-card p-4 grid grid-cols-1 md:grid-cols-2 gap-3 max-w-md">
        <div>
          <label className="block text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-1">
            Period Start
          </label>
          <input
            type="date"
            value={periodStart}
            onChange={(e) => setPeriodStart(e.target.value)}
            className={`w-full ${inputCls} font-mono`}
          />
        </div>
        <div>
          <label className="block text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-1">
            Period End
          </label>
          <input
            type="date"
            value={periodEnd}
            onChange={(e) => setPeriodEnd(e.target.value)}
            className={`w-full ${inputCls} font-mono`}
          />
        </div>
      </div>

      {showCreate ? (
        <div className="glass-card p-6" data-testid="draft-creator">
          <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-3">
            NEW DRAFT
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
            <select
              value={draft.kind}
              onChange={(e) => setDraft({ ...draft, kind: e.target.value })}
              className={`lg:col-span-2 ${inputCls}`}
              data-testid="draft-kind"
            >
              <option value="ar_invoice">AR Invoice</option>
              <option value="ap_bill">AP Bill</option>
            </select>
            <input
              value={draft.counterparty}
              onChange={(e) => setDraft({ ...draft, counterparty: e.target.value })}
              placeholder={draft.kind === 'ar_invoice' ? 'Customer name' : 'Vendor name'}
              className={`lg:col-span-3 ${inputCls}`}
              data-testid="draft-counterparty"
            />
            <input
              type="number"
              value={draft.amount}
              onChange={(e) => setDraft({ ...draft, amount: parseFloat(e.target.value) })}
              placeholder="Amount"
              className={`lg:col-span-2 ${inputCls} font-mono`}
              data-testid="draft-amount"
            />
            <input
              value={draft.memo}
              onChange={(e) => setDraft({ ...draft, memo: e.target.value })}
              placeholder="Memo / description"
              className={`lg:col-span-3 ${inputCls}`}
              data-testid="draft-memo"
            />
            <button
              onClick={handleCreate}
              className="lg:col-span-2 h-9 btn-primary text-[13px] font-medium"
              data-testid="draft-save"
            >
              Save draft
            </button>
          </div>
        </div>
      ) : null}

      <div className="glass-card overflow-hidden">
        {drafts.length === 0 ? (
          <div className="px-5 py-16 text-center">
            <div className="text-[13px] text-aegisaxiom-text-muted mb-1">Sync queue is empty</div>
            <div className="text-[12px] text-aegisaxiom-text-muted">
              Create a draft above to test the QBO sync flow.
            </div>
          </div>
        ) : (
          <>
            {/* Desktop table */}
            <div className="hidden md:block overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="border-b border-aegisaxiom-border bg-aegisaxiom-elevated/40">
                    {[
                      'Kind',
                      'Counterparty',
                      'Memo',
                      'Amount',
                      'Status',
                      'QBO ID',
                      'Created',
                      '',
                    ].map((h, i) => (
                      <th
                        key={i}
                        className={`px-5 py-2.5 text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted ${i === 3 ? 'text-right' : 'text-left'}`}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {drafts.map((d) => (
                    <tr
                      key={d.id}
                      className="border-b border-aegisaxiom-border/50 hover:bg-aegisaxiom-elevated/30"
                      data-testid={`draft-row-${d.id}`}
                    >
                      <td className="px-5 py-2">
                        <span className="px-2 py-0.5 rounded-md text-[11px] bg-violet-500/15 text-violet-400">
                          {d.kind === 'ar_invoice' ? 'AR Invoice' : 'AP Bill'}
                        </span>
                      </td>
                      <td className="px-5 py-2 text-aegisaxiom-text">{d.counterparty}</td>
                      <td className="px-5 py-2 text-aegisaxiom-text-secondary max-w-[220px] truncate">
                        {d.memo}
                      </td>
                      <td className="px-5 py-2 text-right font-mono text-aegisaxiom-text">
                        {fmtCurrency(d.amount)}
                      </td>
                      <td className="px-5 py-2">
                        <SyncStatusBadge status={d.sync_status} />
                      </td>
                      <td className="px-5 py-2 font-mono text-[12px] text-aegisaxiom-text-muted">
                        {d.qbo_id || '—'}
                      </td>
                      <td className="px-5 py-2 text-[12px] text-aegisaxiom-text-muted font-mono">
                        {fmtDateRelative(d.created_at)}
                      </td>
                      <td className="px-5 py-2 text-right space-x-1">
                        {d.sync_status !== 'Synced' && (
                          <button
                            data-testid={`sync-btn-${d.id}`}
                            onClick={() => handleSync(d.id)}
                            className="px-2.5 h-7 text-[12px] rounded-md btn-primary inline-flex items-center gap-1"
                          >
                            <RefreshCw className="h-3 w-3" /> Sync
                          </button>
                        )}
                        <button
                          data-testid={`delete-draft-${d.id}`}
                          onClick={() => handleDelete(d.id)}
                          className="px-2.5 h-7 text-[12px] rounded-md border border-aegisaxiom-border text-aegisaxiom-text-secondary hover:bg-red-500/15 hover:text-red-400 hover:border-red-400/30"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mobile card list */}
            <div className="md:hidden p-3 flex flex-col gap-2.5">
              {drafts.map((d) => (
                <div
                  key={d.id}
                  className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5"
                  data-testid={`draft-card-${d.id}`}
                >
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="px-2 py-0.5 rounded-md text-[10px] bg-violet-500/15 text-violet-400">
                          {d.kind === 'ar_invoice' ? 'AR Invoice' : 'AP Bill'}
                        </span>
                        <SyncStatusBadge status={d.sync_status} />
                      </div>
                      <p className="text-[13px] font-medium text-aegisaxiom-text truncate">
                        {d.counterparty}
                      </p>
                    </div>
                    <span className="text-[14px] font-mono font-semibold text-aegisaxiom-text shrink-0">
                      {fmtCurrency(d.amount)}
                    </span>
                  </div>

                  {d.memo && (
                    <p className="text-[11px] text-aegisaxiom-text-secondary mb-2 line-clamp-2">
                      {d.memo}
                    </p>
                  )}

                  <div className="flex items-center justify-between gap-2 pt-2 border-t border-aegisaxiom-border text-[11px]">
                    <div className="flex items-center gap-3 min-w-0">
                      {d.qbo_id && (
                        <span className="font-mono text-aegisaxiom-text-muted truncate">
                          QBO {d.qbo_id}
                        </span>
                      )}
                      <span className="font-mono text-aegisaxiom-text-muted shrink-0">
                        {fmtDateRelative(d.created_at)}
                      </span>
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      {d.sync_status !== 'Synced' && (
                        <button
                          data-testid={`sync-btn-mobile-${d.id}`}
                          onClick={() => handleSync(d.id)}
                          className="px-2.5 h-7 text-[11px] rounded-md btn-primary inline-flex items-center gap-1"
                        >
                          <RefreshCw className="h-3 w-3" /> Sync
                        </button>
                      )}
                      <button
                        data-testid={`delete-draft-mobile-${d.id}`}
                        onClick={() => handleDelete(d.id)}
                        className="px-2.5 h-7 rounded-md border border-aegisaxiom-border text-aegisaxiom-text-secondary active:bg-red-500/15 active:text-red-400"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      <ConfirmModal
        isOpen={confirmAR}
        onCancel={() => setConfirmAR(false)}
        onConfirm={handleAutoGenerateAR}
        title="Auto-generate AR Drafts"
        message={`This will create AR invoice drafts from approved time entries for the selected period (${periodStart || 'all'} – ${periodEnd || 'all'}). Continue?`}
        confirmText="Generate AR Drafts"
        variant="primary"
      />
      <ConfirmModal
        isOpen={confirmAP}
        onCancel={() => setConfirmAP(false)}
        onConfirm={handleAutoGenerateAP}
        title="Auto-generate AP Drafts"
        message={`This will create AP bill drafts from approved time entries for the selected period (${periodStart || 'all'} – ${periodEnd || 'all'}). Continue?`}
        confirmText="Generate AP Drafts"
        variant="primary"
      />
    </div>
  );
}
