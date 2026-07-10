/**
 * CommissionsTab — preserves the existing legacy commissions UI.
 * Adapted from frontend/src/components/Commissions.js for the Finance Portal tab context.
 * No new features added; this is a straight port into the tab shell.
 */
import React, { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/hooks/usePermissions';
import {
  DollarSign,
  X,
  CheckCircle,
  XCircle,
  BarChart2,
  Users,
  Search,
  Calendar,
  Edit2,
  Filter,
  ChevronDown,
} from 'lucide-react';
import { SortTh, useSorting } from '@/components/shared/SortTh';
import { toast } from 'sonner';
import { ZohoCommissionEditModal } from '@/components/ZohoEditModals';
import API_URL from '@/lib/apiConfig';

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

const STATUS_COLORS = {
  pending: 'bg-amber-500/15 text-amber-400',
  approved: 'bg-blue-500/15 text-blue-400',
  paid: 'bg-green-500/15 text-green-400',
  Paid: 'bg-green-500/15 text-green-400',
  Owed: 'bg-amber-500/15 text-amber-400',
  Potential: 'bg-violet-500/15 text-violet-400',
  rejected: 'bg-red-500/15 text-red-400',
  partial: 'bg-violet-500/15 text-violet-400',
};

const TABS = ['All', 'Pending', 'Approved', 'Paid', 'Rejected'];
const ZOHO_TABS = ['All', 'Owed', 'Paid', 'Potential'];

export default function CommissionsTab() {
  const { user } = useAuth();
  const { isAdmin, hasPermission } = usePermissions();
  const [commissions, setCommissions] = useState([]);
  const [commTotal, setCommTotal] = useState(0);
  const [zohoCommissions, setZohoCommissions] = useState([]);
  const [zohoCommTotal, setZohoCommTotal] = useState(0);
  const [users, setUsers] = useState([]);
  const [commSource, setCommSource] = useState('zoho');
  const [tab, setTab] = useState('All');
  const [zohoTab, setZohoTab] = useState('All');
  const [searchQuery, setSearchQuery] = useState('');
  const [dateFilter, setDateFilter] = useState({ startDate: '', endDate: '' });
  const [userFilter, setUserFilter] = useState('');
  const [loading, setLoading] = useState(false);
  const [showZohoEditModal, setShowZohoEditModal] = useState(false);
  const [selectedCommission, setSelectedCommission] = useState(null);

  const loadCommissions = useCallback(async () => {
    setLoading(true);
    try {
      const [r1, r2] = await Promise.all([
        api.get(`${API}/api/finance/commissions`),
        api.get(`${API}/api/finance/zoho-commissions`),
      ]);
      if (r1.ok) {
        const d = await r1.json();
        const items = Array.isArray(d) ? d : d.items || [];
        setCommissions(items);
        setCommTotal(items.reduce((s, c) => s + (c.amount_owed || 0), 0));
      }
      if (r2.ok) {
        const d = await r2.json();
        const items = Array.isArray(d) ? d : d.items || [];
        setZohoCommissions(items);
        setZohoCommTotal(items.reduce((s, c) => s + (parseFloat(c.commission_amount) || 0), 0));
      }
      if (isAdmin) {
        const ru = await api.get(`${API}/api/users`);
        if (ru.ok) {
          const d = await ru.json();
          setUsers(Array.isArray(d) ? d : d.items || []);
        }
      }
    } finally {
      setLoading(false);
    }
  }, [isAdmin]);

  useEffect(() => {
    loadCommissions();
  }, [loadCommissions]);

  const handleApprove = async (id) => {
    const r = await api.put(`${API}/api/finance/commissions/${id}`, { status: 'approved' });
    if (r.ok) {
      toast.success('Commission approved');
      loadCommissions();
    } else {
      toast.error('Failed to approve');
    }
  };

  const handleReject = async (id) => {
    const r = await api.put(`${API}/api/finance/commissions/${id}`, { status: 'rejected' });
    if (r.ok) {
      toast.success('Commission rejected');
      loadCommissions();
    } else {
      toast.error('Failed to reject');
    }
  };

  const handleMarkPaid = async (id) => {
    const r = await api.put(`${API}/api/finance/commissions/${id}`, { status: 'paid' });
    if (r.ok) {
      toast.success('Marked as paid');
      loadCommissions();
    } else {
      toast.error('Failed');
    }
  };

  const filteredCommissions = commissions.filter((c) => {
    if (tab !== 'All' && c.status?.toLowerCase() !== tab.toLowerCase()) return false;
    if (
      searchQuery &&
      !`${c.deal_name} ${c.user_name}`.toLowerCase().includes(searchQuery.toLowerCase())
    )
      return false;
    if (userFilter && c.user_id !== userFilter) return false;
    if (dateFilter.startDate && new Date(c.created_at) < new Date(dateFilter.startDate))
      return false;
    if (dateFilter.endDate && new Date(c.created_at) > new Date(dateFilter.endDate)) return false;
    return true;
  });

  const filteredZoho = zohoCommissions.filter((c) => {
    if (zohoTab !== 'All' && c.commission_status !== zohoTab) return false;
    if (
      searchQuery &&
      !`${c.deal_name || ''} ${c.sales_rep_name || ''}`
        .toLowerCase()
        .includes(searchQuery.toLowerCase())
    )
      return false;
    return true;
  });

  const inputCls =
    'h-9 px-3 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface text-[13px] text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted outline-none focus:border-violet-500';

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
        <div>
          <h2 className="page-title">Commissions</h2>
          <p className="text-xs text-aegisaxiom-text-muted">Track and manage sales commissions</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setCommSource('zoho')}
            className={`flex-1 md:flex-none px-3 py-1.5 rounded-lg text-[13px] font-medium transition ${commSource === 'zoho' ? 'bg-violet-600 text-white' : 'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
          >
            Zoho
          </button>
          <button
            onClick={() => setCommSource('aegisaxiom')}
            className={`flex-1 md:flex-none px-3 py-1.5 rounded-lg text-[13px] font-medium transition ${commSource === 'aegisaxiom' ? 'bg-violet-600 text-white' : 'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
          >
            AEGISAXIOM
          </button>
        </div>
      </div>

      {/* Summary KPIs */}
      <div className="grid grid-cols-2 gap-4">
        <div className="glass-card p-4">
          <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-1">
            Total {commSource === 'zoho' ? 'Zoho' : 'AEGISAXIOM'} Commissions
          </div>
          <div className="text-[22px] font-bold text-aegisaxiom-text">
            {fmt(commSource === 'zoho' ? zohoCommTotal : commTotal)}
          </div>
        </div>
        <div className="glass-card p-4">
          <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-1">
            Records
          </div>
          <div className="text-[22px] font-bold text-aegisaxiom-text">
            {commSource === 'zoho' ? filteredZoho.length : filteredCommissions.length}
          </div>
        </div>
      </div>

      {/* Filters */}
      <div className="glass-card p-4 grid grid-cols-1 md:flex md:items-center gap-3 md:flex-wrap">
        <div className="flex items-center gap-2 px-3 h-9 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface md:flex-1 md:min-w-[200px]">
          <Search className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search deal / rep…"
            className="bg-transparent outline-none text-[13px] flex-1 text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted"
          />
        </div>
        <div className="grid grid-cols-2 md:flex md:items-center gap-2">
          <input
            type="date"
            value={dateFilter.startDate}
            onChange={(e) => setDateFilter((p) => ({ ...p, startDate: e.target.value }))}
            className={`${inputCls} font-mono min-w-0`}
          />
          <input
            type="date"
            value={dateFilter.endDate}
            onChange={(e) => setDateFilter((p) => ({ ...p, endDate: e.target.value }))}
            className={`${inputCls} font-mono min-w-0`}
          />
        </div>
        {isAdmin && users.length > 0 && (
          <select
            value={userFilter}
            onChange={(e) => setUserFilter(e.target.value)}
            className={`${inputCls} min-w-0`}
          >
            <option value="">All reps</option>
            {users.map((u) => (
              <option key={u.user_id || u.id} value={u.user_id || u.id}>
                {u.name || u.email}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Tabs */}
      {commSource === 'aegisaxiom' && (
        <div
          className="flex gap-1 border-b border-aegisaxiom-border overflow-x-auto scrollbar-hide"
          style={{ scrollbarWidth: 'none' }}
        >
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`shrink-0 px-4 py-2.5 text-sm font-medium transition border-b-2 -mb-px ${tab === t ? 'border-violet-500 text-aegisaxiom-text' : 'border-transparent text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
            >
              {t}
            </button>
          ))}
        </div>
      )}
      {commSource === 'zoho' && (
        <div
          className="flex gap-1 border-b border-aegisaxiom-border overflow-x-auto scrollbar-hide"
          style={{ scrollbarWidth: 'none' }}
        >
          {ZOHO_TABS.map((t) => (
            <button
              key={t}
              onClick={() => setZohoTab(t)}
              className={`shrink-0 px-4 py-2.5 text-sm font-medium transition border-b-2 -mb-px ${zohoTab === t ? 'border-violet-500 text-aegisaxiom-text' : 'border-transparent text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
            >
              {t}
            </button>
          ))}
        </div>
      )}

      {/* Commission rows */}
      <div className="glass-card overflow-hidden">
        {loading ? (
          <div className="py-16 text-center text-[13px] text-aegisaxiom-text-muted">Loading…</div>
        ) : commSource === 'aegisaxiom' ? (
          filteredCommissions.length === 0 ? (
            <div className="py-16 text-center text-[13px] text-aegisaxiom-text-muted">
              No commissions match the filters.
            </div>
          ) : (
            <>
              {/* Desktop table */}
              <div className="hidden md:block overflow-x-auto">
                <table className="w-full text-[13px]">
                  <thead>
                    <tr className="border-b border-aegisaxiom-border bg-aegisaxiom-elevated/40">
                      {['Deal', 'Rep', 'Type', 'Amount Owed', 'Rate', 'Status', 'Date', ''].map(
                        (h, i) => (
                          <th
                            key={i}
                            className={`px-5 py-2.5 text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted ${i === 3 ? 'text-right' : 'text-left'}`}
                          >
                            {h}
                          </th>
                        ),
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {filteredCommissions.map((c) => (
                      <tr
                        key={c.id || c.commission_id}
                        className="border-b border-aegisaxiom-border/50 hover:bg-aegisaxiom-elevated/30"
                      >
                        <td className="px-5 py-2 text-aegisaxiom-text font-medium">
                          {c.deal_name || '—'}
                        </td>
                        <td className="px-5 py-2 text-aegisaxiom-text-secondary">{c.user_name || '—'}</td>
                        <td className="px-5 py-2">
                          <span className="px-2 py-0.5 rounded-md text-[11px] bg-aegisaxiom-elevated text-aegisaxiom-text-muted">
                            {c.commission_type}
                          </span>
                        </td>
                        <td className="px-5 py-2 text-right font-mono text-aegisaxiom-text font-medium">
                          {fmt(c.amount_owed)}
                        </td>
                        <td className="px-5 py-2 font-mono text-aegisaxiom-text-muted">
                          {c.commission_rate ? `${c.commission_rate}%` : '—'}
                        </td>
                        <td className="px-5 py-2">
                          <span
                            className={`px-2 py-0.5 rounded-md text-[11px] ${STATUS_COLORS[c.status] || 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted'}`}
                          >
                            {c.status}
                          </span>
                        </td>
                        <td className="px-5 py-2 font-mono text-[12px] text-aegisaxiom-text-muted">
                          {fmtDate(c.created_at)}
                        </td>
                        <td className="px-5 py-2 text-right space-x-1">
                          {isAdmin && c.status === 'pending' && (
                            <>
                              <button
                                onClick={() => handleApprove(c.id)}
                                className="p-1.5 rounded-md hover:bg-green-500/15 text-aegisaxiom-text-muted hover:text-green-400 transition-colors"
                                title="Approve"
                              >
                                <CheckCircle className="h-3.5 w-3.5" />
                              </button>
                              <button
                                onClick={() => handleReject(c.id)}
                                className="p-1.5 rounded-md hover:bg-red-500/15 text-aegisaxiom-text-muted hover:text-red-400 transition-colors"
                                title="Reject"
                              >
                                <XCircle className="h-3.5 w-3.5" />
                              </button>
                            </>
                          )}
                          {isAdmin && c.status === 'approved' && (
                            <button
                              onClick={() => handleMarkPaid(c.id)}
                              className="px-2 h-7 text-[12px] rounded-md btn-primary"
                              title="Mark paid"
                            >
                              Paid
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Mobile card list */}
              <div className="md:hidden p-3 flex flex-col gap-2.5">
                {filteredCommissions.map((c) => (
                  <div
                    key={c.id || c.commission_id}
                    className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5"
                    data-testid={`commission-card-${c.id || c.commission_id}`}
                  >
                    <div className="flex items-start justify-between gap-2 mb-2">
                      <div className="min-w-0 flex-1">
                        <p className="text-[13px] font-medium text-aegisaxiom-text truncate">
                          {c.deal_name || '—'}
                        </p>
                        <p className="text-[11px] text-aegisaxiom-text-secondary mt-0.5 truncate">
                          {c.user_name || '—'}
                        </p>
                      </div>
                      <span
                        className={`shrink-0 px-2 py-0.5 rounded-md text-[10px] ${STATUS_COLORS[c.status] || 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted'}`}
                      >
                        {c.status}
                      </span>
                    </div>

                    <p className="text-[16px] font-bold text-aegisaxiom-text mb-2.5 font-mono">
                      {fmt(c.amount_owed)}
                    </p>

                    <div className="flex items-center justify-between gap-2 pt-2 border-t border-aegisaxiom-border text-[11px]">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="px-2 py-0.5 rounded-md text-[10px] bg-aegisaxiom-elevated text-aegisaxiom-text-muted shrink-0">
                          {c.commission_type}
                        </span>
                        {c.commission_rate && (
                          <span className="font-mono text-aegisaxiom-text-muted">
                            {c.commission_rate}%
                          </span>
                        )}
                        <span className="font-mono text-aegisaxiom-text-muted truncate">
                          {fmtDate(c.created_at)}
                        </span>
                      </div>
                    </div>

                    {isAdmin && (c.status === 'pending' || c.status === 'approved') && (
                      <div className="flex items-center gap-1.5 mt-2.5 pt-2.5 border-t border-aegisaxiom-border">
                        {c.status === 'pending' && (
                          <>
                            <button
                              onClick={() => handleApprove(c.id)}
                              className="flex-1 h-8 text-[12px] rounded-md border border-aegisaxiom-border text-aegisaxiom-text active:bg-green-500/15 active:text-green-400 transition-colors flex items-center justify-center gap-1"
                            >
                              <CheckCircle className="h-3.5 w-3.5" /> Approve
                            </button>
                            <button
                              onClick={() => handleReject(c.id)}
                              className="flex-1 h-8 text-[12px] rounded-md border border-aegisaxiom-border text-aegisaxiom-text active:bg-red-500/15 active:text-red-400 transition-colors flex items-center justify-center gap-1"
                            >
                              <XCircle className="h-3.5 w-3.5" /> Reject
                            </button>
                          </>
                        )}
                        {c.status === 'approved' && (
                          <button
                            onClick={() => handleMarkPaid(c.id)}
                            className="flex-1 h-8 text-[12px] rounded-md btn-primary"
                          >
                            Mark paid
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )
        ) : filteredZoho.length === 0 ? (
          <div className="py-16 text-center text-[13px] text-aegisaxiom-text-muted">
            No Zoho commissions match the filters.
          </div>
        ) : (
          <>
            {/* Desktop table */}
            <div className="hidden md:block overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="border-b border-aegisaxiom-border bg-aegisaxiom-elevated/40">
                    {['Deal', 'Rep', 'Amount', 'Status', 'Period', ''].map((h, i) => (
                      <th
                        key={i}
                        className={`px-5 py-2.5 text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted ${i === 2 ? 'text-right' : 'text-left'}`}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filteredZoho.map((c) => (
                    <tr
                      key={c.id || c.commission_id}
                      className="border-b border-aegisaxiom-border/50 hover:bg-aegisaxiom-elevated/30"
                    >
                      <td className="px-5 py-2 text-aegisaxiom-text font-medium">{c.deal_name || '—'}</td>
                      <td className="px-5 py-2 text-aegisaxiom-text-secondary">
                        {c.sales_rep_name || '—'}
                      </td>
                      <td className="px-5 py-2 text-right font-mono text-aegisaxiom-text font-medium">
                        {fmt(parseFloat(c.commission_amount) || 0)}
                      </td>
                      <td className="px-5 py-2">
                        <span
                          className={`px-2 py-0.5 rounded-md text-[11px] ${STATUS_COLORS[c.commission_status] || 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted'}`}
                        >
                          {c.commission_status}
                        </span>
                      </td>
                      <td className="px-5 py-2 font-mono text-[12px] text-aegisaxiom-text-muted">
                        {c.commission_period || '—'}
                      </td>
                      <td className="px-5 py-2 text-right">
                        {isAdmin && (
                          <button
                            onClick={() => {
                              setSelectedCommission(c);
                              setShowZohoEditModal(true);
                            }}
                            className="p-1.5 rounded-md hover:bg-aegisaxiom-elevated text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition-colors"
                          >
                            <Edit2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mobile card list */}
            <div className="md:hidden p-3 flex flex-col gap-2.5">
              {filteredZoho.map((c) => (
                <div
                  key={c.id || c.commission_id}
                  className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5"
                  data-testid={`zoho-commission-card-${c.id || c.commission_id}`}
                >
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] font-medium text-aegisaxiom-text truncate">
                        {c.deal_name || '—'}
                      </p>
                      <p className="text-[11px] text-aegisaxiom-text-secondary mt-0.5 truncate">
                        {c.sales_rep_name || '—'}
                      </p>
                    </div>
                    <span
                      className={`shrink-0 px-2 py-0.5 rounded-md text-[10px] ${STATUS_COLORS[c.commission_status] || 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted'}`}
                    >
                      {c.commission_status}
                    </span>
                  </div>

                  <p className="text-[16px] font-bold text-aegisaxiom-text mb-2.5 font-mono">
                    {fmt(parseFloat(c.commission_amount) || 0)}
                  </p>

                  <div className="flex items-center justify-between gap-2 pt-2 border-t border-aegisaxiom-border">
                    <span className="text-[11px] font-mono text-aegisaxiom-text-muted truncate">
                      {c.commission_period || '—'}
                    </span>
                    {isAdmin && (
                      <button
                        onClick={() => {
                          setSelectedCommission(c);
                          setShowZohoEditModal(true);
                        }}
                        className="p-1.5 -mr-1.5 rounded-md text-aegisaxiom-text-muted active:bg-aegisaxiom-elevated active:text-aegisaxiom-text transition-colors shrink-0"
                        data-testid={`zoho-edit-mobile-${c.id || c.commission_id}`}
                      >
                        <Edit2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {showZohoEditModal && selectedCommission && (
        <ZohoCommissionEditModal
          commission={selectedCommission}
          onClose={() => setShowZohoEditModal(false)}
          onSaved={loadCommissions}
        />
      )}
    </div>
  );
}
