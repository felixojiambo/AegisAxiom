import React, { useEffect, useState, useCallback } from 'react';
import { api } from '../lib/api';
import { useAuth } from '../contexts/AuthContext';
import { usePermissions } from '../hooks/usePermissions';
import {
  Search,
  TrendingUp,
  Edit2,
  Trash2,
  X,
  ChevronLeft,
  ChevronRight,
  Filter,
} from 'lucide-react';
import { SortTh, useSorting } from './shared/SortTh';
import { toast } from 'sonner';
import DateRangePicker from './DateRangePicker';
import { ZohoDealEditModal } from './ZohoEditModals';
import API_URL from '../lib/apiConfig';
import ConfirmModal from './shared/ConfirmModal';

const API = API_URL;
const fmt = (n) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(n || 0);
const fmtDate = (d) =>
  d
    ? new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    : '—';
const STAGES = [
  'Prospecting',
  'Qualification',
  'Proposal',
  'Negotiation',
  'Closed Won',
  'Closed Lost',
];
const STAGE_COLORS = {
  Prospecting: 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300',
  Qualification: 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300',
  Proposal: 'bg-fuchsia-50 dark:bg-fuchsia-600/20 text-fuchsia-700 dark:text-fuchsia-300',
  Negotiation: 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300',
  'Closed Won': 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300',
  'Closed Lost': 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300',
};
const EMPTY = {
  name: '',
  account_id: '',
  stage: 'Prospecting',
  amount: '',
  close_date: '',
  probability: 0,
  description: '',
  assigned_sales_rep_id: '',
  assigned_agent_id: '',
  is_test_data: false,
};
const PAGE_SIZES = [25, 50, 100, 200];

export default function Deals() {
  const { user } = useAuth();
  const { isAdmin, hasPermission, hasAnyPermission } = usePermissions();
  const canView = hasAnyPermission(['deals.view', 'deals.view.own']);
  const isInternal = !hasPermission('deals.view') && hasPermission('deals.view.own');
  const [deals, setDeals] = useState([]);
  const [total, setTotal] = useState(0);
  const [accounts, setAccounts] = useState([]);
  const [search, setSearch] = useState('');
  const [stageFilter, setStageFilter] = useState('');
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [saving, setSaving] = useState(false);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const sort = useSorting('deal_name', 'asc');
  const adminSort = useSorting('name', 'asc');
  const canEdit = hasAnyPermission(['deals.edit.all', 'deals.edit']);
  const [editZohoDeal, setEditZohoDeal] = useState(null);
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });

  const load = useCallback(async () => {
    if (isInternal) {
      const params = new URLSearchParams();
      if (dateFrom) params.set('date_from', dateFrom);
      if (dateTo) params.set('date_to', dateTo);
      const res = await api.get(`${API}/api/agent-dashboard?${params}`);
      if (res.ok) {
        const data = await res.json();
        setDeals(data.deals || []);
        setTotal((data.deals || []).length);
      }
      return;
    }
    const [dr, ar] = await Promise.all([
      api.get(
        `${API}/api/deals?skip=${page * pageSize}&limit=${pageSize}${stageFilter ? `&stage=${stageFilter}` : ''}`,
      ),
      api.get(`${API}/api/accounts?limit=500`),
    ]);
    if (dr.ok) {
      const data = await dr.json();
      setDeals(data.items || []);
      setTotal(data.total || 0);
    }
    if (ar.ok) {
      const data = await ar.json();
      setAccounts(data.items || data || []);
    }
  }, [isInternal, dateFrom, dateTo, page, pageSize, stageFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const openCreate = () => {
    setForm(EMPTY);
    setModal('create');
  };
  const openEdit = (d) => {
    setForm({ ...d, amount: d.amount || '' });
    setModal('edit');
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    const body = {
      ...form,
      amount: parseFloat(form.amount) || 0,
      probability: parseInt(form.probability) || 0,
    };
    try {
      const url = modal === 'edit' ? `${API}/api/deals/${form.deal_id}` : `${API}/api/deals`;
      const res = await (modal === 'edit' ? api.put(url, body) : api.post(url, body));
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail);
      }
      toast.success(modal === 'edit' ? 'Deal updated' : 'Deal created');
      setModal(null);
      load();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = (id) => {
    setConfirmState({
      open: true,
      title: 'Delete Deal',
      message: 'Delete this deal? This action cannot be undone.',
      action: async () => {
        const res = await api.delete(`${API}/api/deals/${id}`);
        if (res.ok) {
          toast.success('Deal deleted');
          load();
        } else toast.error('Failed to delete');
      },
    });
  };

  // Client-side search filter (on top of server-side pagination)
  const filtered = isInternal
    ? sort.sortFn(
        deals.filter(
          (d) =>
            !search ||
            (d.deal_name || '').toLowerCase().includes(search.toLowerCase()) ||
            (d.account_name || '').toLowerCase().includes(search.toLowerCase()),
        ),
      )
    : adminSort.sortFn(
        deals.filter(
          (d) =>
            !search ||
            d.name?.toLowerCase().includes(search.toLowerCase()) ||
            d.account_name?.toLowerCase().includes(search.toLowerCase()),
        ),
      );

  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="space-y-5" data-testid="deals-page">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="page-title">{isInternal ? 'My Deals' : 'Deals'}</h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">{total} total</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {isInternal && (
            <DateRangePicker
              dateFrom={dateFrom}
              dateTo={dateTo}
              onChange={({ date_from, date_to }) => {
                setDateFrom(date_from);
                setDateTo(date_to);
              }}
            />
          )}
          {!isInternal && (
            <select
              value={stageFilter}
              onChange={(e) => {
                setStageFilter(e.target.value);
                setPage(0);
              }}
              className="input-field text-xs"
              data-testid="deals-stage-filter"
            >
              <option value="">All Stages</option>
              {STAGES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          )}
          <div className="relative">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted" />
            <input
              className="input-field pl-9"
              placeholder="Search deals..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              data-testid="deals-search"
            />
          </div>
          {canEdit && (
            <button
              onClick={openCreate}
              className="btn-primary flex items-center gap-2 text-xs"
              data-testid="create-deal-btn"
            >
              <TrendingUp size={14} />
              New Deal
            </button>
          )}
        </div>
      </div>

      <div className="glass-card overflow-hidden">
        <table className="w-full text-sm" data-testid="deals-table">
          {isInternal ? (
            <>
              <thead>
                <tr className="border-b border-aegisaxiom-border">
                  <SortTh
                    col="zoho_id"
                    label="Deal ID"
                    sortCol={sort.sortCol}
                    sortDir={sort.sortDir}
                    onSort={sort.handleSort}
                  />
                  <SortTh
                    col="deal_name"
                    label="Deal Name"
                    sortCol={sort.sortCol}
                    sortDir={sort.sortDir}
                    onSort={sort.handleSort}
                  />
                  <SortTh
                    col="account_name"
                    label="Account"
                    sortCol={sort.sortCol}
                    sortDir={sort.sortDir}
                    onSort={sort.handleSort}
                  />
                  <SortTh
                    col="agent_of_record_name"
                    label="Agent of Record"
                    sortCol={sort.sortCol}
                    sortDir={sort.sortDir}
                    onSort={sort.handleSort}
                  />
                  <SortTh
                    col="stage"
                    label="Stage"
                    sortCol={sort.sortCol}
                    sortDir={sort.sortDir}
                    onSort={sort.handleSort}
                  />
                  <SortTh
                    col="amount"
                    label="Amount"
                    sortCol={sort.sortCol}
                    sortDir={sort.sortDir}
                    onSort={sort.handleSort}
                  />
                  <SortTh
                    col="close_date"
                    label="Close Date"
                    sortCol={sort.sortCol}
                    sortDir={sort.sortDir}
                    onSort={sort.handleSort}
                  />
                  {canEdit && <th className="py-3 px-4" />}
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={canEdit ? 8 : 7} className="py-16 text-center text-aegisaxiom-text-muted">
                      No deals found.
                    </td>
                  </tr>
                ) : (
                  filtered.map((d, i) => (
                    <tr
                      key={d.zoho_id || i}
                      className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted transition-colors"
                    >
                      <td className="py-3 px-4 text-aegisaxiom-text-muted text-xs font-mono">
                        {d.zoho_id || '—'}
                      </td>
                      <td className="py-3 px-4 text-aegisaxiom-text font-medium">{d.deal_name || '—'}</td>
                      <td className="py-3 px-4 text-aegisaxiom-text-secondary">{d.account_name || '—'}</td>
                      <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                        {d.agent_of_record_name || '—'}
                      </td>
                      <td className="py-3 px-4">
                        <span
                          className={`badge-stage ${STAGE_COLORS[d.stage] || 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary'}`}
                        >
                          {d.stage || '—'}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-emerald-600 dark:text-emerald-400 font-medium">{fmt(d.amount)}</td>
                      <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">{fmtDate(d.close_date)}</td>
                      {canEdit && (
                        <td className="py-3 px-4">
                          <button
                            onClick={() => setEditZohoDeal(d)}
                            className="text-aegisaxiom-text-muted hover:text-violet-600 dark:text-violet-400 transition-colors"
                            data-testid={`edit-zoho-deal-${d.zoho_id}`}
                          >
                            <Edit2 size={14} />
                          </button>
                        </td>
                      )}
                    </tr>
                  ))
                )}
              </tbody>
            </>
          ) : (
            <>
              <thead>
                <tr className="border-b border-aegisaxiom-border">
                  <SortTh
                    col="zoho_deal_id"
                    label="Deal ID"
                    sortCol={adminSort.sortCol}
                    sortDir={adminSort.sortDir}
                    onSort={adminSort.handleSort}
                  />
                  <SortTh
                    col="name"
                    label="Deal Name"
                    sortCol={adminSort.sortCol}
                    sortDir={adminSort.sortDir}
                    onSort={adminSort.handleSort}
                  />
                  <SortTh
                    col="account_name"
                    label="Account"
                    sortCol={adminSort.sortCol}
                    sortDir={adminSort.sortDir}
                    onSort={adminSort.handleSort}
                  />
                  <SortTh
                    col="stage"
                    label="Stage"
                    sortCol={adminSort.sortCol}
                    sortDir={adminSort.sortDir}
                    onSort={adminSort.handleSort}
                  />
                  <SortTh
                    col="amount"
                    label="Amount"
                    sortCol={adminSort.sortCol}
                    sortDir={adminSort.sortDir}
                    onSort={adminSort.handleSort}
                  />
                  <SortTh
                    col="probability"
                    label="Probability"
                    sortCol={adminSort.sortCol}
                    sortDir={adminSort.sortDir}
                    onSort={adminSort.handleSort}
                  />
                  <th className="py-3 px-4" />
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="py-16 text-center text-aegisaxiom-text-muted">
                      No deals found.
                    </td>
                  </tr>
                ) : (
                  filtered.map((d) => (
                    <tr
                      key={d.deal_id}
                      className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted transition-colors"
                    >
                      <td className="py-3 px-4 text-aegisaxiom-text-muted text-xs font-mono">
                        {d.zoho_deal_id || '—'}
                      </td>
                      <td className="py-3 px-4">
                        <p className="text-aegisaxiom-text font-medium">{d.name}</p>
                        <p className="text-aegisaxiom-text-muted text-xs">{d.deal_owner_name}</p>
                      </td>
                      <td className="py-3 px-4 text-aegisaxiom-text-secondary">{d.account_name}</td>
                      <td className="py-3 px-4">
                        <span
                          className={`badge-stage ${STAGE_COLORS[d.stage] || 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary'}`}
                        >
                          {d.stage}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-aegisaxiom-text font-medium">{fmt(d.amount)}</td>
                      <td className="py-3 px-4">
                        <div className="flex items-center gap-2">
                          <div className="flex-1 bg-aegisaxiom-elevated rounded-full h-1.5 max-w-16">
                            <div
                              className="bg-violet-500 h-1.5 rounded-full"
                              style={{ width: `${d.probability}%` }}
                            />
                          </div>
                          <span className="text-aegisaxiom-text-secondary text-xs">{d.probability}%</span>
                        </div>
                      </td>
                      <td className="py-3 px-4">
                        {canEdit && (
                          <div className="flex gap-2 justify-end">
                            <button
                              onClick={() => openEdit(d)}
                              className="text-aegisaxiom-text-muted hover:text-violet-600 dark:text-violet-400 transition-colors"
                              data-testid={`edit-deal-${d.deal_id}`}
                            >
                              <Edit2 size={14} />
                            </button>
                            {hasPermission('deals.delete') && (
                              <button
                                onClick={() => handleDelete(d.deal_id)}
                                className="text-aegisaxiom-text-muted hover:text-red-600 dark:text-red-400 transition-colors"
                                data-testid={`delete-deal-${d.deal_id}`}
                              >
                                <Trash2 size={14} />
                              </button>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </>
          )}
        </table>

        {/* Pagination */}
        {!isInternal && totalPages >= 1 && (
          <div
            className="flex items-center justify-between px-4 py-3 border-t border-aegisaxiom-border"
            data-testid="deals-pagination"
          >
            <div className="flex items-center gap-3">
              <span className="text-xs text-aegisaxiom-text-muted">
                {total > 0
                  ? `${page * pageSize + 1}–${Math.min((page + 1) * pageSize, total)} of ${total}`
                  : '0 results'}
              </span>
              <div className="flex items-center gap-1.5">
                <span className="text-xs text-aegisaxiom-text-muted">Per page:</span>
                <select
                  value={pageSize}
                  onChange={(e) => {
                    setPageSize(Number(e.target.value));
                    setPage(0);
                  }}
                  className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-md text-xs text-aegisaxiom-text-secondary px-2 py-1 outline-none"
                  data-testid="deals-page-size"
                >
                  {PAGE_SIZES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="p-1.5 rounded-lg bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                data-testid="deals-prev-page"
              >
                <ChevronLeft size={14} />
              </button>
              <span className="text-xs text-aegisaxiom-text-secondary px-2">
                {page + 1} / {totalPages || 1}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="p-1.5 rounded-lg bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                data-testid="deals-next-page"
              >
                <ChevronRight size={14} />
              </button>
            </div>
          </div>
        )}
      </div>

      {modal && (
        <div
          className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-4"
          data-testid="deal-modal"
        >
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-lg p-6 animate-fadeInUp">
            <div className="flex items-center justify-between mb-5">
              <h2
                className="text-lg font-bold text-aegisaxiom-text"
                style={{ fontFamily: 'Manrope, sans-serif' }}
              >
                {modal === 'edit' ? 'Edit Deal' : 'New Deal'}
              </h2>
              <button onClick={() => setModal(null)}>
                <X size={18} className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text" />
              </button>
            </div>
            <form onSubmit={handleSubmit} className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2">
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Deal Name *</label>
                  <input
                    className="input-field"
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    required
                  />
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Account *</label>
                  <select
                    className="input-field"
                    value={form.account_id}
                    onChange={(e) => setForm({ ...form, account_id: e.target.value })}
                    required
                    data-testid="deal-account-select"
                  >
                    <option value="">Select Account</option>
                    {accounts.map((a) => (
                      <option key={a.account_id} value={a.account_id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Stage</label>
                  <select
                    className="input-field"
                    value={form.stage}
                    onChange={(e) => setForm({ ...form, stage: e.target.value })}
                    data-testid="deal-stage-select"
                  >
                    {STAGES.map((s) => (
                      <option key={s}>{s}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Amount ($)</label>
                  <input
                    type="number"
                    className="input-field"
                    value={form.amount}
                    onChange={(e) => setForm({ ...form, amount: e.target.value })}
                  />
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Probability (%)</label>
                  <input
                    type="number"
                    min="0"
                    max="100"
                    className="input-field"
                    value={form.probability}
                    onChange={(e) => setForm({ ...form, probability: e.target.value })}
                  />
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Close Date</label>
                  <input
                    type="date"
                    className="input-field"
                    value={form.close_date || ''}
                    onChange={(e) => setForm({ ...form, close_date: e.target.value })}
                  />
                </div>
                <div className="col-span-2">
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Description</label>
                  <textarea
                    rows={2}
                    className="input-field resize-none"
                    value={form.description || ''}
                    onChange={(e) => setForm({ ...form, description: e.target.value })}
                  />
                </div>
                {isAdmin() && (
                  <div className="col-span-2">
                    <label className="flex items-center gap-2 cursor-pointer group">
                      <input
                        type="checkbox"
                        checked={form.is_test_data || false}
                        onChange={(e) => setForm({ ...form, is_test_data: e.target.checked })}
                        className="w-4 h-4 rounded border-aegisaxiom-border bg-aegisaxiom-elevated text-violet-600 focus:ring-violet-600 focus:ring-offset-0 cursor-pointer"
                        data-testid="deal-test-data-checkbox"
                      />
                      <span className="text-xs text-aegisaxiom-text-secondary group-hover:text-aegisaxiom-text-secondary transition-colors">
                        Mark as Test Data
                      </span>
                    </label>
                  </div>
                )}
              </div>
              <div className="flex gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setModal(null)}
                  className="btn-secondary flex-1"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="btn-primary flex-1"
                  data-testid="deal-save-btn"
                >
                  {saving ? 'Saving...' : 'Save Deal'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Zoho Deal Edit Modal */}
      {editZohoDeal && (
        <ZohoDealEditModal
          deal={editZohoDeal}
          onClose={() => setEditZohoDeal(null)}
          onSaved={load}
        />
      )}
      <ConfirmModal
        isOpen={confirmState.open}
        title={confirmState.title}
        message={confirmState.message}
        confirmText="Delete"
        onConfirm={() => confirmState.action?.()}
        onCancel={() => setConfirmState({ open: false, action: null, title: '', message: '' })}
      />
    </div>
  );
}
