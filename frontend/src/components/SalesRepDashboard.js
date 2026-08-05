import React, { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/hooks/usePermissions';
import {
  TrendingUp,
  DollarSign,
  Clock,
  CheckCircle,
  RefreshCw,
  ChevronDown,
  User,
  Filter,
  Search,
  Users,
} from 'lucide-react';
import { SortTh, useSorting } from './shared/SortTh';
import { toast } from 'sonner';
import DateRangePicker from './DateRangePicker';
import API_URL from '../lib/apiConfig';

const API = API_URL;
const fmt = (n) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
  }).format(n || 0);
const fmtDate = (d) => {
  if (!d) return '—';
  // Date-only strings (YYYY-MM-DD) must be parsed as local, not UTC,
  // to avoid timezone shift. new Date("2025-01-10") is parsed as UTC midnight,
  // which renders as the previous day for users west of UTC.
  const dateOnlyMatch = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d);
  if (dateOnlyMatch) {
    const [, y, m, day] = dateOnlyMatch;
    return new Date(Number(y), Number(m) - 1, Number(day)).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  }
  // Fallback for full ISO timestamps (e.g. payment_date if it ever comes through with time)
  return new Date(d).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
};
const STATUS_PILLS = {
  Paid: 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-600/30',
  Owed: 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border-amber-200 dark:border-amber-600/30',
  Potential: 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border-violet-200 dark:border-violet-600/30',
};
const STAGE_PILLS = {
  'Closed Won': 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300',
  'Closed Lost': 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300',
  'Proposal Sent': 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300',
  Suspect: 'bg-aegisaxiom-elevated/20 text-aegisaxiom-text-secondary',
};
const COMM_TABS = ['All', 'Owed', 'Paid', 'Potential'];

export default function SalesRepDashboard() {
  const { user } = useAuth();
  const { isAdmin, hasPermission } = usePermissions();
  const canViewAll = isAdmin() || hasPermission('opportunities.view.all');
  const [data, setData] = useState(null);
  const [salesReps, setSalesReps] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [commTab, setCommTab] = useState('All');
  const [searchTerm, setSearchTerm] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const commSort = useSorting('payment_date', 'desc');
  const dealSort = useSorting('deal_name', 'asc');
  const [dealSearch, setDealSearch] = useState('');
  // View As state (admin only)
  const [selectedRep, setSelectedRep] = useState('');
  const [showFilter, setShowFilter] = useState(false);
  const [repSearch, setRepSearch] = useState('');

  const load = useCallback(
    async (opts = {}) => {
      const isRefresh = opts.refresh;
      if (isRefresh) setRefreshing(true);
      else setLoading(true);
      try {
        const params = new URLSearchParams();
        // Only allow view_user_id if admin
        if (canViewAll && selectedRep) params.set('view_user_id', selectedRep);
        if (commTab !== 'All') params.set('status', commTab);
        if (dateFrom) params.set('date_from', dateFrom);
        if (dateTo) params.set('date_to', dateTo);
        const res = await api.get(`${API}/api/agent-dashboard?${params}`);
        if (!res.ok) {
          const e = await res.json();
          throw new Error(e.detail);
        }
        setData(await res.json());
      } catch (err) {
        toast.error(err.message || 'Failed to load dashboard');
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [canViewAll, selectedRep, commTab, dateFrom, dateTo],
  );

  useEffect(() => {
    // Only fetch sales reps list if user can view all
    if (canViewAll) {
      api
        .get(`${API}/api/agent-dashboard/users-list?role=sales_rep`)
        .then((r) => (r.ok ? r.json() : []))
        .then((data) => setSalesReps(data))
        .catch(() => {});
    }
  }, [canViewAll]);

  useEffect(() => {
    load();
  }, [load]);
  const handleRefresh = () => load({ refresh: true });

  const summary = data?.summary;

  // Tab counts come from the unfiltered backend summary, NOT from data.commissions —
  // because data.commissions is already filtered by the `status` query param, so
  // counting it would zero out the other tabs whenever a non-"All" tab is active.
  const tabCount = (tab) => {
    if (!summary) return 0;
    if (tab === 'All') {
      return (summary.owed_count || 0) + (summary.paid_count || 0) + (summary.potential_count || 0);
    }
    if (tab === 'Owed') return summary.owed_count || 0;
    if (tab === 'Paid') return summary.paid_count || 0;
    if (tab === 'Potential') return summary.potential_count || 0;
    return 0;
  };

  const rawDeals = data?.deals || [];
  const filteredComms = commSort.sortFn(
    (data?.commissions || []).filter((c) => {
      if (!searchTerm) return true;
      const term = searchTerm.toLowerCase();
      return (
        (c.deal_name || '').toLowerCase().includes(term) ||
        (c.sales_rep_name || '').toLowerCase().includes(term) ||
        (c.invoice_no || '').toLowerCase().includes(term)
      );
    }),
  );
  const deals = dealSort.sortFn(
    rawDeals.filter((d) => {
      if (!dealSearch) return true;
      const term = dealSearch.toLowerCase();
      return (
        (d.deal_name || '').toLowerCase().includes(term) ||
        (d.account_name || '').toLowerCase().includes(term)
      );
    }),
  );

  const selectedRepObj = salesReps.find((r) => r.user_id === selectedRep);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full" />
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="sales-rep-dashboard">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="page-title flex items-center gap-2">
            <Users size={22} className="text-violet-600 dark:text-violet-400" />
            Sales Rep Dashboard
          </h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">
            {selectedRep
              ? `Viewing as: ${selectedRepObj?.name || 'Sales Rep'} (${selectedRepObj?.email || ''})`
              : data?.view_label || 'Select a sales rep to view their dashboard'}
            {data?.last_synced && (
              <span className="ml-2 text-aegisaxiom-text-muted">· Last synced {fmtDate(data.last_synced)}</span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* View As dropdown (admin/director only) */}
          {canViewAll && (
            <div className="relative">
              <button
                onClick={() => setShowFilter((f) => !f)}
                className="btn-secondary flex items-center gap-2 text-sm"
                data-testid="salesrep-filter-btn"
              >
                <Filter size={14} />
                {selectedRep ? selectedRepObj?.name || 'Sales Rep' : 'View As'}
                <ChevronDown
                  size={12}
                  className={`transition-transform ${showFilter ? 'rotate-180' : ''}`}
                />
              </button>
              {showFilter && (
                <div className="absolute right-0 top-full mt-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-80 z-50 shadow-xl animate-fadeInUp overflow-hidden">
                  {/* My Dashboard (admin's own view) */}
                  <div className="p-2 border-b border-aegisaxiom-border">
                    <button
                      onClick={() => {
                        setSelectedRep('');
                        setShowFilter(false);
                      }}
                      data-testid="salesrep-view-my-btn"
                      className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors flex items-center gap-2
                      ${!selectedRep ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300' : 'text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated'}`}
                    >
                      <User size={13} />
                      My Dashboard
                    </button>
                  </div>
                  {/* By Sales Rep */}
                  <div className="p-2">
                    <p className="text-xs text-aegisaxiom-text-muted px-2 py-1 font-medium uppercase tracking-wider">
                      By Sales Rep
                    </p>
                    <div className="px-2 pb-1">
                      <div className="flex items-center gap-1 bg-aegisaxiom-elevated rounded-lg px-2 py-1.5 mb-1">
                        <Search size={11} className="text-aegisaxiom-text-muted shrink-0" />
                        <input
                          value={repSearch}
                          onChange={(e) => setRepSearch(e.target.value)}
                          className="bg-transparent text-xs text-aegisaxiom-text-secondary placeholder-aegisaxiom-text-muted outline-none flex-1"
                          placeholder="Search sales reps..."
                          data-testid="salesrep-search-input"
                        />
                      </div>
                    </div>
                    <div className="max-h-48 overflow-y-auto">
                      {salesReps
                        .filter(
                          (r) =>
                            r.name?.toLowerCase().includes(repSearch.toLowerCase()) ||
                            r.email?.toLowerCase().includes(repSearch.toLowerCase()),
                        )
                        .map((r) => (
                          <button
                            key={r.user_id}
                            onClick={() => {
                              setSelectedRep(r.user_id);
                              setShowFilter(false);
                              setRepSearch('');
                            }}
                            data-testid={`salesrep-view-${r.user_id}`}
                            className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-colors flex items-center justify-between
                            ${selectedRep === r.user_id ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300' : 'text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated'}`}
                          >
                            <div>
                              <span className="font-medium text-aegisaxiom-text-secondary">{r.name}</span>
                              <span className="block text-aegisaxiom-text-muted text-[10px]">{r.email}</span>
                            </div>
                            <User size={11} className="text-aegisaxiom-text-muted shrink-0" />
                          </button>
                        ))}
                      {salesReps.filter(
                        (r) =>
                          r.name?.toLowerCase().includes(repSearch.toLowerCase()) ||
                          r.email?.toLowerCase().includes(repSearch.toLowerCase()),
                      ).length === 0 && (
                        <p className="px-3 py-2 text-xs text-aegisaxiom-text-muted">No sales reps found</p>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="btn-secondary flex items-center gap-2 text-sm"
            data-testid="salesrep-refresh-btn"
          >
            <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
            {refreshing ? 'Syncing...' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Zoho not connected warning */}
      {data && !data.zoho_connected && (
        <div className="glass-card p-4 border border-amber-200 dark:border-amber-600/30 bg-amber-50 dark:bg-amber-600/10 flex items-start gap-3 mb-4 mt-4">
          <Clock size={16} className="text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
          <div>
            <p className="text-amber-700 dark:text-amber-300 text-sm font-medium">CRM Not Connected YET, WILL MIGRATE</p>
            <p className="text-aegisaxiom-text-secondary text-xs mt-0.5">
              Commission data is not available until CRM connected. Go to{' '}
              <a href="/admin/integrations" className="text-violet-600 dark:text-violet-400 hover:underline">
                Admin -> Integrations
              </a>{' '}
              to reconnect.
            </p>
          </div>
        </div>
      )}

      {/* Summary Cards */}
      {summary && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 my-2">
          {[
            {
              label: 'Owed Commissions',
              value: summary.owed_count,
              sub: fmt(summary.commission_owed),
              color: 'text-amber-600 dark:text-amber-400',
              bg: 'bg-amber-50 dark:bg-amber-600/10 border-amber-200 dark:border-amber-600/20',
              icon: Clock,
            },
            {
              label: 'Paid Commissions',
              value: summary.paid_count,
              sub: fmt(summary.commission_paid),
              color: 'text-emerald-600 dark:text-emerald-400',
              bg: 'bg-emerald-50 dark:bg-emerald-600/10 border-emerald-200 dark:border-emerald-600/20',
              icon: CheckCircle,
            },
            {
              label: 'Potential Commissions',
              value: summary.potential_count,
              sub: fmt(summary.commission_potential),
              color: 'text-violet-600 dark:text-violet-400',
              bg: 'bg-violet-50 dark:bg-violet-600/10 border-violet-200 dark:border-violet-600/20',
              icon: TrendingUp,
            },
          ].map((s) => (
            <div
              key={s.label}
              className={`glass-card p-6 border ${s.bg}`}
              data-testid={`salesrep-summary-${s.label.split(' ')[0].toLowerCase()}`}
            >
              <div className="flex items-center justify-between mb-4">
                <p className="text-aegisaxiom-text-secondary text-xs font-medium">{s.label}</p>
                <s.icon size={18} className={s.color} />
              </div>
              <p
                className={`text-4xl font-bold ${s.color}`}
                style={{ fontFamily: 'Manrope, sans-serif' }}
              >
                {s.value}
              </p>
              <p className="text-aegisaxiom-text-secondary text-sm mt-2 font-medium">{s.sub}</p>
            </div>
          ))}
        </div>
      )}

      {/* Commissions Table */}
      <div className="glass-card overflow-hidden">
        <div className="p-4 border-b border-aegisaxiom-border flex flex-col sm:flex-row sm:items-center gap-3">
          <h2 className="text-sm font-semibold text-aegisaxiom-text flex items-center gap-2">
            <DollarSign size={15} className="text-violet-600 dark:text-violet-400" />
            {selectedRep ? `${selectedRepObj?.name}'s Commissions` : 'Commissions'}
          </h2>
          <div className="flex-1" />
          <DateRangePicker
            dateFrom={dateFrom}
            dateTo={dateTo}
            onChange={({ date_from, date_to }) => {
              setDateFrom(date_from);
              setDateTo(date_to);
            }}
          />
          <div className="flex items-center gap-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg px-3 py-1.5">
            <Search size={12} className="text-aegisaxiom-text-muted" />
            <input
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="bg-transparent text-xs text-aegisaxiom-text-secondary placeholder-aegisaxiom-text-muted outline-none w-32"
              placeholder="Search deal, rep..."
            />
          </div>
        </div>
        <div className="flex gap-1 px-4 pt-3 border-b border-aegisaxiom-border overflow-x-auto">
          {COMM_TABS.map((t) => (
            <button
              key={t}
              onClick={() => setCommTab(t)}
              data-testid={`salesrep-comm-tab-${t.toLowerCase()}`}
              className={`px-3 py-1.5 rounded-t-lg text-xs font-medium whitespace-nowrap flex items-center gap-1.5 transition-all -mb-px
                ${commTab === t ? 'bg-aegisaxiom-surface text-violet-700 dark:text-violet-300 border border-b-aegisaxiom-surface border-aegisaxiom-border' : 'text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary'}`}
            >
              {t}
              <span
                className={`text-xs rounded-full px-1.5 py-0.5 ${commTab === t ? 'bg-violet-600/30' : 'bg-aegisaxiom-elevated'}`}
              >
                {tabCount(t)}
              </span>
            </button>
          ))}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="salesrep-commissions-table">
            <thead>
              <tr className="border-b border-aegisaxiom-border">
                <SortTh
                  col="deal_id"
                  label="Deal ID"
                  sortCol={commSort.sortCol}
                  sortDir={commSort.sortDir}
                  onSort={commSort.handleSort}
                />
                <SortTh
                  col="deal_name"
                  label="Deal Name"
                  sortCol={commSort.sortCol}
                  sortDir={commSort.sortDir}
                  onSort={commSort.handleSort}
                />
                <SortTh
                  col="sales_rep_name"
                  label="Sales Rep"
                  sortCol={commSort.sortCol}
                  sortDir={commSort.sortDir}
                  onSort={commSort.handleSort}
                />
                <SortTh
                  col="invoice_no"
                  label="Invoice #"
                  sortCol={commSort.sortCol}
                  sortDir={commSort.sortDir}
                  onSort={commSort.handleSort}
                />
                <SortTh
                  col="invoice_issued_date"
                  label="Issued Date"
                  sortCol={commSort.sortCol}
                  sortDir={commSort.sortDir}
                  onSort={commSort.handleSort}
                />
                <SortTh
                  col="invoice_due_date"
                  label="Due Date"
                  sortCol={commSort.sortCol}
                  sortDir={commSort.sortDir}
                  onSort={commSort.handleSort}
                />
                <SortTh
                  col="sales_rep_commission_status"
                  label="Status"
                  sortCol={commSort.sortCol}
                  sortDir={commSort.sortDir}
                  onSort={commSort.handleSort}
                />
                <SortTh
                  col="sales_rep_commission"
                  label="Commission $"
                  sortCol={commSort.sortCol}
                  sortDir={commSort.sortDir}
                  onSort={commSort.handleSort}
                />
                <SortTh
                  col="payment_date"
                  label="Payment Date"
                  sortCol={commSort.sortCol}
                  sortDir={commSort.sortDir}
                  onSort={commSort.handleSort}
                />
                <SortTh
                  col="payment_ref"
                  label="Payment Ref"
                  sortCol={commSort.sortCol}
                  sortDir={commSort.sortDir}
                  onSort={commSort.handleSort}
                />
              </tr>
            </thead>
            <tbody>
              {filteredComms.length === 0 ? (
                <tr>
                  <td colSpan={10} className="py-16 text-center text-aegisaxiom-text-muted text-sm">
                    {data?.zoho_connected === false
                      ? 'Reconnect Zoho to load commission data.'
                      : 'No commission records found.'}
                  </td>
                </tr>
              ) : (
                filteredComms.map((c, i) => {
                  const status = c.sales_rep_commission_status;
                  const amount = c.sales_rep_commission;
                  return (
                    <tr
                      key={c.zoho_id || i}
                      className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted"
                    >
                      <td className="py-3 px-4 text-aegisaxiom-text-muted text-xs font-mono">
                        {c.deal_id || '—'}
                      </td>
                      <td className="py-3 px-4 text-aegisaxiom-text font-medium">{c.deal_name || '—'}</td>
                      <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                        {c.sales_rep_name || c.sales_rep_email || '—'}
                      </td>
                      <td className="py-3 px-4 text-aegisaxiom-text-secondary">{c.invoice_no || '—'}</td>
                      <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                        {fmtDate(c.invoice_issued_date)}
                      </td>
                      <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                        {fmtDate(c.invoice_due_date)}
                      </td>
                      <td className="py-3 px-4">
                        <span
                          className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_PILLS[status] || 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary border-aegisaxiom-border-hover'}`}
                        >
                          {status || '—'}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-amber-600 dark:text-amber-400 font-medium">{fmt(amount)}</td>
                      <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                        {fmtDate(c.payment_date)}
                      </td>
                      <td className="py-3 px-4 text-aegisaxiom-text-muted text-xs">{c.payment_ref || '—'}</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Deals Table */}
      <div className="glass-card overflow-hidden">
        <div className="p-4 border-b border-aegisaxiom-border flex flex-col sm:flex-row sm:items-center gap-3">
          <h2 className="text-sm font-semibold text-aegisaxiom-text flex items-center gap-2">
            <TrendingUp size={15} className="text-violet-600 dark:text-violet-400" />
            {selectedRep ? `${selectedRepObj?.name}'s Deals` : 'Deals'}
            <span className="text-xs text-aegisaxiom-text-muted font-normal ml-1">
              ({deals.length} records)
            </span>
          </h2>
          <div className="flex-1" />
          <div className="flex items-center gap-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg px-3 py-1.5">
            <Search size={12} className="text-aegisaxiom-text-muted" />
            <input
              value={dealSearch}
              onChange={(e) => setDealSearch(e.target.value)}
              className="bg-transparent text-xs text-aegisaxiom-text-secondary placeholder-aegisaxiom-text-muted outline-none w-32"
              placeholder="Search deals..."
            />
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="salesrep-deals-table">
            <thead>
              <tr className="border-b border-aegisaxiom-border">
                <SortTh
                  col="zoho_id"
                  label="Deal ID"
                  sortCol={dealSort.sortCol}
                  sortDir={dealSort.sortDir}
                  onSort={dealSort.handleSort}
                />
                <SortTh
                  col="deal_name"
                  label="Deal Name"
                  sortCol={dealSort.sortCol}
                  sortDir={dealSort.sortDir}
                  onSort={dealSort.handleSort}
                />
                <SortTh
                  col="account_name"
                  label="Account"
                  sortCol={dealSort.sortCol}
                  sortDir={dealSort.sortDir}
                  onSort={dealSort.handleSort}
                />
                <SortTh
                  col="agent_of_record_name"
                  label="Agent of Record"
                  sortCol={dealSort.sortCol}
                  sortDir={dealSort.sortDir}
                  onSort={dealSort.handleSort}
                />
                <SortTh
                  col="stage"
                  label="Stage"
                  sortCol={dealSort.sortCol}
                  sortDir={dealSort.sortDir}
                  onSort={dealSort.handleSort}
                />
                <SortTh
                  col="amount"
                  label="Amount"
                  sortCol={dealSort.sortCol}
                  sortDir={dealSort.sortDir}
                  onSort={dealSort.handleSort}
                />
                <SortTh
                  col="close_date"
                  label="Close Date"
                  sortCol={dealSort.sortCol}
                  sortDir={dealSort.sortDir}
                  onSort={dealSort.handleSort}
                />
              </tr>
            </thead>
            <tbody>
              {deals.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-16 text-center text-aegisaxiom-text-muted text-sm">
                    {data?.zoho_connected === false
                      ? 'Reconnect CRM to load deal data.'
                      : 'No deals found.'}
                  </td>
                </tr>
              ) : (
                deals.map((d, i) => (
                  <tr
                    key={d.zoho_id || i}
                    className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted"
                  >
                    <td className="py-3 px-4 text-aegisaxiom-text-muted text-xs font-mono">
                      {d.zoho_id || '—'}
                    </td>
                    <td className="py-3 px-4 text-aegisaxiom-text font-medium">{d.deal_name}</td>
                    <td className="py-3 px-4 text-aegisaxiom-text-secondary">{d.account_name || '—'}</td>
                    <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                      {d.agent_of_record_name || '—'}
                    </td>
                    <td className="py-3 px-4">
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full ${STAGE_PILLS[d.stage] || 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary'}`}
                      >
                        {d.stage || '—'}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-emerald-600 dark:text-emerald-400 font-medium">{fmt(d.amount)}</td>
                    <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">{fmtDate(d.close_date)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
