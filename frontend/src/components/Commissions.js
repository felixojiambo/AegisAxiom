import React, { useEffect, useState, useCallback } from 'react';
import { api, unwrap } from '@/lib/api';
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
import { SortTh, useSorting } from './shared/SortTh';
import { toast } from 'sonner';
import { ZohoCommissionEditModal } from './ZohoEditModals';
import API_URL from '../lib/apiConfig';

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
  pending:
    'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border-amber-200 dark:border-amber-600/30',
  approved:
    'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-600/30',
  paid: 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-600/30',
  Paid: 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-600/30',
  Owed: 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border-amber-200 dark:border-amber-600/30',
  Potential:
    'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border-violet-200 dark:border-violet-600/30',
  rejected:
    'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300 border-red-200 dark:border-red-600/30',
  partial:
    'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border-violet-200 dark:border-violet-600/30',
};
const EMPTY = {
  deal_id: '',
  user_id: '',
  commission_type: 'agent',
  amount_owed: '',
  potential_amount: '',
  commission_rate: '',
  notes: '',
};
const TABS = ['All', 'Pending', 'Approved', 'Paid', 'Rejected'];
const ZOHO_TABS = ['All', 'Owed', 'Paid', 'Potential'];

export default function Commissions() {
  const { user } = useAuth();
  const { isAdmin, hasPermission, hasAnyPermission } = usePermissions();
  const isInternal = !hasPermission('commissions.view') && hasPermission('commissions.view.own');
  // Zoho commission state (for sales_rep / agent)
  const [zohoComms, setZohoComms] = useState([]);
  const [zohoSummary, setZohoSummary] = useState(null);
  const [zohoTargetRole, setZohoTargetRole] = useState('sales_rep');
  const [zohoTab, setZohoTab] = useState('All');
  const zohoSort = useSorting('deal_name', 'asc');
  // Admin/director local commission state
  const [comms, setComms] = useState([]);
  const [deals, setDeals] = useState([]);
  const [users, setUsers] = useState([]);
  const [summary, setSummary] = useState(null);
  const [report, setReport] = useState(null);
  const [tab, setTab] = useState('All');
  const [view, setView] = useState('table');
  const [modal, setModal] = useState(null);
  const [rejectModal, setRejectModal] = useState(null);
  const [rejectReason, setRejectReason] = useState('');
  const [form, setForm] = useState(EMPTY);
  const [loading, setLoading] = useState(false);
  const [editZohoComm, setEditZohoComm] = useState(null);
  // View As (admin/director only)
  const [viewAsUser, setViewAsUser] = useState(null); // { user_id, name, role }
  const [showViewAsDropdown, setShowViewAsDropdown] = useState(false);
  const [allUsers, setAllUsers] = useState([]);
  // Permission helpers
  const canCreate = hasPermission('commissions.create');
  const canApprove = hasPermission('commissions.mark_paid');
  const canViewAll = isAdmin() || hasPermission('commissions.view.all');

  // Load users for View As dropdown (admin/director only)
  useEffect(() => {
    if (canViewAll) {
      api
        .get(`${API}/api/admin/users?limit=200`)
        .then((r) => (r.ok ? r.json() : []))
        .then((d) => {
          const arr = Array.isArray(d) ? d : d.items || [];
          setAllUsers(arr.filter((u) => ['sales_rep', 'agent'].includes(u.role)));
        })
        .catch(() => {});
    }
  }, []);

  useEffect(() => {
    load();
  }, [viewAsUser]);
  const load = async () => {
    // Always load Zoho commission data (available for all roles via agent-dashboard)
    const params = new URLSearchParams();
    if (viewAsUser) params.append('user_id', viewAsUser.user_id);
    const zohoRes = await api.get(
      `${API}/api/agent-dashboard${params.toString() ? '?' + params : ''}`,
    );
    if (zohoRes.ok) {
      const data = await zohoRes.json();
      setZohoComms(data.commissions || []);
      setZohoSummary(data.summary || null);
      setZohoTargetRole(data.target_role || 'sales_rep');
    }
    if (isInternal) return;
    // Admin/director/finance: also load local commissions
    const fetches = [api.get(`${API}/api/commissions`), api.get(`${API}/api/commissions/summary`)];
    if (canCreate) {
      fetches.push(api.get(`${API}/api/deals`));
      fetches.push(api.get(`${API}/api/admin/users`));
    }
    if (canApprove) {
      fetches.push(api.get(`${API}/api/commissions/report`));
    }
    const results = await Promise.all(fetches);
    const [cr, sr] = results;
    if (cr.ok) setComms(unwrap(await cr.json()));
    if (sr.ok) setSummary(await sr.json());
    if (canCreate && results[2]?.ok) setDeals(unwrap(await results[2].json()));
    if (canCreate && results[3]?.ok) setUsers(await results[3].json());
    const reportRes = results[canCreate ? 4 : 2];
    if (reportRes?.ok) setReport(await reportRes.json());
  };
  // --- Zoho commissions filtering ---
  const statusField =
    zohoTargetRole === 'agent' ? 'agent_commission_status' : 'sales_rep_commission_status';
  const amountField = zohoTargetRole === 'agent' ? 'agent_commission' : 'sales_rep_commission';
  const filteredZoho = zohoSort.sortFn(
    zohoTab === 'All' ? zohoComms : zohoComms.filter((c) => c[statusField] === zohoTab),
  );
  // Column counts for empty-state colSpan — keep in sync with <thead> definitions below
  const INTERNAL_ZOHO_COL_COUNT = 9 + (canApprove ? 1 : 0); // 9 SortTh headers + optional action
  const ADMIN_ZOHO_COL_COUNT = 12; // Deal ID, Deal, Customer, Sales Rep, Agent, Rep $, Agent $, Status, Issued, Due, Payment, Action
  // --- Local commissions filtering ---
  const filtered =
    tab === 'All' ? comms : comms.filter((c) => c.status?.toLowerCase() === tab.toLowerCase());
  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    const body = {
      ...form,
      amount_owed: parseFloat(form.amount_owed) || 0,
      potential_amount: parseFloat(form.potential_amount) || 0,
      commission_rate: parseFloat(form.commission_rate) || 0,
    };
    try {
      const res = await api.post(`${API}/api/commissions`, body);
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail);
      }
      toast.success('Commission created');
      setModal(null);
      load();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };
  const approveComm = async (id) => {
    setLoading(true);
    try {
      const res = await api.post(`${API}/api/commissions/${id}/approve`, {});
      if (res.ok) {
        toast.success('Commission approved');
        load();
      }
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };
  const payComm = async (id) => {
    setLoading(true);
    try {
      const res = await api.post(`${API}/api/commissions/${id}/pay`, {});
      if (res.ok) {
        toast.success('Commission marked as paid');
        load();
      }
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };
  const rejectComm = async () => {
    if (!rejectModal) return;
    setLoading(true);
    try {
      const res = await api.post(`${API}/api/commissions/${rejectModal}/reject`, {
        reason: rejectReason,
      });
      if (res.ok) {
        toast.success('Commission rejected');
        setRejectModal(null);
        setRejectReason('');
        load();
      }
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };
  const tabCount = (t) =>
    t === 'All'
      ? comms.length
      : comms.filter((c) => c.status?.toLowerCase() === t.toLowerCase()).length;
  const zohoTabCount = (t) =>
    t === 'All' ? zohoComms.length : zohoComms.filter((c) => c[statusField] === t).length;

  // ---- RENDER: Internal role (sales_rep / agent) — Zoho commissions view ----
  if (isInternal) {
    return (
      <div className="space-y-5" data-testid="commissions-page">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="page-title">My Commissions</h1>
            <p className="text-aegisaxiom-text-muted text-xs mt-0.5">{zohoComms.length} records</p>
          </div>
        </div>
        {/* Summary Cards */}
        {zohoSummary && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-6 my-2">
            {[
              {
                label: 'Owed',
                value: zohoSummary.owed_count,
                sub: fmt(zohoSummary.commission_owed),
                color: 'text-amber-600 dark:text-amber-400',
                bg: 'bg-amber-50 dark:bg-amber-600/10 border-amber-200 dark:border-amber-600/20',
              },
              {
                label: 'Paid',
                value: zohoSummary.paid_count,
                sub: fmt(zohoSummary.commission_paid),
                color: 'text-emerald-600 dark:text-emerald-400',
                bg: 'bg-emerald-50 dark:bg-emerald-600/10 border-emerald-200 dark:border-emerald-600/20',
              },
              {
                label: 'Potential',
                value: zohoSummary.potential_count,
                sub: fmt(zohoSummary.commission_potential),
                color: 'text-violet-600 dark:text-violet-400',
                bg: 'bg-violet-50 dark:bg-violet-600/10 border-violet-200 dark:border-violet-600/20',
              },
            ].map((s) => (
              <div key={s.label} className={`glass-card p-6 border ${s.bg}`}>
                <p className="text-aegisaxiom-text-secondary text-sm font-medium mb-2">
                  {s.label} Commissions
                </p>
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
        <div className="glass-card overflow-hidden">
          {/* Tabs */}
          <div className="flex gap-1 p-3 border-b border-aegisaxiom-border overflow-x-auto">
            {ZOHO_TABS.map((t) => (
              <button
                key={t}
                onClick={() => setZohoTab(t)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all whitespace-nowrap flex items-center gap-1.5
                  ${zohoTab === t ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-500/40' : 'text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary border border-transparent'}`}
              >
                {t}
                <span
                  className={`text-xs rounded-full px-1.5 py-0.5 ${zohoTab === t ? 'bg-violet-600/30' : 'bg-aegisaxiom-elevated'}`}
                >
                  {zohoTabCount(t)}
                </span>
              </button>
            ))}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-aegisaxiom-border">
                  <SortTh
                    col="deal_id"
                    label="Deal ID"
                    sortCol={zohoSort.sortCol}
                    sortDir={zohoSort.sortDir}
                    onSort={zohoSort.handleSort}
                  />
                  <SortTh
                    col="deal_name"
                    label="Deal Name"
                    sortCol={zohoSort.sortCol}
                    sortDir={zohoSort.sortDir}
                    onSort={zohoSort.handleSort}
                  />
                  <SortTh
                    col="agent_of_records_name"
                    label="Account / Agent"
                    sortCol={zohoSort.sortCol}
                    sortDir={zohoSort.sortDir}
                    onSort={zohoSort.handleSort}
                  />
                  <SortTh
                    col="invoice_no"
                    label="Invoice #"
                    sortCol={zohoSort.sortCol}
                    sortDir={zohoSort.sortDir}
                    onSort={zohoSort.handleSort}
                  />
                  <SortTh
                    col="invoice_issued_date"
                    label="Issued Date"
                    sortCol={zohoSort.sortCol}
                    sortDir={zohoSort.sortDir}
                    onSort={zohoSort.handleSort}
                  />
                  <SortTh
                    col="invoice_due_date"
                    label="Due Date"
                    sortCol={zohoSort.sortCol}
                    sortDir={zohoSort.sortDir}
                    onSort={zohoSort.handleSort}
                  />
                  <SortTh
                    col={statusField}
                    label="Status"
                    sortCol={zohoSort.sortCol}
                    sortDir={zohoSort.sortDir}
                    onSort={zohoSort.handleSort}
                  />
                  <SortTh
                    col={amountField}
                    label="Commission $"
                    sortCol={zohoSort.sortCol}
                    sortDir={zohoSort.sortDir}
                    onSort={zohoSort.handleSort}
                  />
                  <SortTh
                    col="payment_date"
                    label="Payment Date"
                    sortCol={zohoSort.sortCol}
                    sortDir={zohoSort.sortDir}
                    onSort={zohoSort.handleSort}
                  />
                  {canApprove && (
                    <th className="text-left py-3 px-4 text-xs font-medium text-aegisaxiom-text-muted uppercase tracking-wider" />
                  )}
                </tr>
              </thead>
              <tbody>
                {filteredZoho.length === 0 ? (
                  <tr>
                    <td
                      colSpan={INTERNAL_ZOHO_COL_COUNT}
                      className="py-16 text-center text-aegisaxiom-text-muted text-sm"
                    >
                      No commission records found.
                    </td>
                  </tr>
                ) : (
                  filteredZoho.map((c, i) => {
                    const status = c[statusField];
                    const amount = c[amountField];
                    return (
                      <tr
                        key={c.zoho_id || i}
                        className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted"
                      >
                        <td className="py-3 px-4 text-aegisaxiom-text-muted text-xs font-mono">
                          {c.deal_id || '—'}
                        </td>
                        <td className="py-3 px-4 text-aegisaxiom-text font-medium">
                          {c.deal_name || '—'}
                        </td>
                        <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                          {c.agent_of_records_name || c.sales_rep_email || '—'}
                        </td>
                        <td className="py-3 px-4 text-aegisaxiom-text-secondary">
                          {c.invoice_no || '—'}
                        </td>
                        <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                          {fmtDate(c.invoice_issued_date)}
                        </td>
                        <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                          {fmtDate(c.invoice_due_date)}
                        </td>
                        <td className="py-3 px-4">
                          <span
                            className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_COLORS[status] || 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary border-aegisaxiom-border-hover'}`}
                          >
                            {status || '—'}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-amber-600 dark:text-amber-400 font-medium">
                          {fmt(amount)}
                        </td>
                        <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                          {fmtDate(c.payment_date)}
                        </td>
                        {canApprove && (
                          <td className="py-3 px-4">
                            <button
                              onClick={() => setEditZohoComm(c)}
                              className="text-aegisaxiom-text-muted hover:text-violet-600 dark:text-violet-400 transition-colors"
                              data-testid={`edit-zoho-comm-${c.zoho_id}`}
                            >
                              <Edit2 size={14} />
                            </button>
                          </td>
                        )}
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-5" data-testid="commissions-page">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
        <div className="min-w-0">
          <h1 className="page-title">Commissions</h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">
            {comms.length + zohoComms.length} records
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap sm:flex-nowrap">
          {/* View As (admin/director only) */}
          {canViewAll && allUsers.length > 0 && (
            <div className="relative">
              <button
                onClick={() => setShowViewAsDropdown((v) => !v)}
                className={`btn-secondary flex items-center gap-2 ${viewAsUser ? 'border-violet-500/50 text-violet-700 dark:text-violet-300' : ''}`}
                data-testid="commissions-view-as-btn"
              >
                <Filter size={14} />
                <span className="truncate max-w-[120px] sm:max-w-none">
                  {viewAsUser ? viewAsUser.name : 'View As'}
                </span>
                <ChevronDown
                  size={12}
                  className={`transition-transform shrink-0 ${showViewAsDropdown ? 'rotate-180' : ''}`}
                />
              </button>
              {showViewAsDropdown && (
                <div className="absolute right-0 top-full mt-1 w-60 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg shadow-xl z-50 max-h-72 overflow-y-auto">
                  {viewAsUser && (
                    <button
                      onClick={() => {
                        setViewAsUser(null);
                        setShowViewAsDropdown(false);
                      }}
                      className="w-full text-left px-4 py-2 text-sm text-red-600 dark:text-red-400 hover:bg-aegisaxiom-elevated border-b border-aegisaxiom-border"
                    >
                      Clear — View All
                    </button>
                  )}
                  <div className="px-4 py-2 text-xs font-semibold text-aegisaxiom-text-muted uppercase tracking-wide bg-aegisaxiom-elevated sticky top-0">
                    Sales Reps &amp; Agents
                  </div>
                  {allUsers.map((u) => (
                    <button
                      key={u.user_id}
                      onClick={() => {
                        setViewAsUser(u);
                        setShowViewAsDropdown(false);
                      }}
                      className={`w-full text-left px-4 py-2 text-sm hover:bg-aegisaxiom-elevated flex items-center justify-between ${
                        viewAsUser?.user_id === u.user_id
                          ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300'
                          : 'text-aegisaxiom-text-secondary'
                      }`}
                    >
                      <span>{u.name}</span>
                      <span className="text-xs text-aegisaxiom-text-muted capitalize">
                        {u.role?.replace('_', ' ')}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {canApprove && (
            <button
              onClick={() => setView((v) => (v === 'report' ? 'table' : 'report'))}
              className={`btn-secondary flex items-center gap-2 ${view === 'report' ? 'border-violet-500/50 text-violet-700 dark:text-violet-300' : ''}`}
              data-testid="toggle-report-btn"
            >
              <BarChart2 size={15} />
              <span className="whitespace-nowrap">
                {view === 'report' ? 'Table View' : 'Report View'}
              </span>
            </button>
          )}
          {canCreate && (
            <button
              onClick={() => {
                setForm(EMPTY);
                setModal('create');
              }}
              className="btn-primary flex items-center justify-center gap-2 shrink-0 w-full sm:w-auto"
              data-testid="create-commission-btn"
            >
              <DollarSign size={15} />
              <span className="whitespace-nowrap">New Commission</span>
            </button>
          )}
        </div>
      </div>

      {/* Summary Cards - use Zoho summary if local summary is empty */}
      {(summary || zohoSummary) && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-6 my-2">
          {(() => {
            const s = summary && summary.count > 0 ? summary : null;
            const z = zohoSummary || {};
            const owed = s ? s.total_owed : z.commission_owed || 0;
            const paid = s ? s.total_paid : z.commission_paid || 0;
            const potential = s ? s.total_potential : z.commission_potential || 0;
            return [
              {
                label: 'Total Owed',
                value: fmt(owed),
                color: 'text-amber-600 dark:text-amber-400',
                bg: 'bg-amber-50 dark:bg-amber-600/10 border-amber-200 dark:border-amber-600/20',
              },
              {
                label: 'Total Paid',
                value: fmt(paid),
                color: 'text-emerald-600 dark:text-emerald-400',
                bg: 'bg-emerald-50 dark:bg-emerald-600/10 border-emerald-200 dark:border-emerald-600/20',
              },
              {
                label: 'Potential',
                value: fmt(potential),
                color: 'text-violet-600 dark:text-violet-400',
                bg: 'bg-violet-50 dark:bg-violet-600/10 border-violet-200 dark:border-violet-600/20',
              },
            ];
          })().map((s) => (
            <div
              key={s.label}
              className={`glass-card p-6 border ${s.bg}`}
              data-testid={`commission-stat-${s.label.toLowerCase().replace(/\s/g, '-')}`}
            >
              <p className="text-aegisaxiom-text-muted text-sm mb-2">{s.label}</p>
              <p
                className={`text-3xl font-bold ${s.color}`}
                style={{ fontFamily: 'Manrope, sans-serif' }}
              >
                {s.value}
              </p>
            </div>
          ))}
        </div>
      )}
      {view === 'report' ? (
        /* ---- REPORT VIEW ---- */
        <div className="glass-card overflow-hidden" data-testid="commission-report">
          <div className="p-4 border-b border-aegisaxiom-border flex items-center gap-2">
            <Users size={16} className="text-violet-600 dark:text-violet-400" />
            <h2 className="text-sm font-semibold text-aegisaxiom-text">
              Commission Report — By Recipient
            </h2>
          </div>
          {report?.by_user?.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-aegisaxiom-border">
                    {[
                      'Recipient',
                      'Count',
                      'Total Owed',
                      'Total Paid',
                      'Pending',
                      'Approved',
                      'Paid',
                      'Rejected',
                    ].map((h) => (
                      <th
                        key={h}
                        className="text-left py-3 px-4 text-xs font-medium text-aegisaxiom-text-muted uppercase tracking-wider"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {report.by_user.map((r) => (
                    <tr
                      key={r.user_id}
                      className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted"
                    >
                      <td className="py-3 px-4 text-aegisaxiom-text font-medium">{r.user_name}</td>
                      <td className="py-3 px-4 text-aegisaxiom-text-secondary">{r.count}</td>
                      <td className="py-3 px-4 text-amber-600 dark:text-amber-400 font-medium">
                        {fmt(r.total_owed)}
                      </td>
                      <td className="py-3 px-4 text-emerald-600 dark:text-emerald-400 font-medium">
                        {fmt(r.total_paid)}
                      </td>
                      <td className="py-3 px-4">
                        <span
                          className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_COLORS.pending}`}
                        >
                          {r.pending || 0}
                        </span>
                      </td>
                      <td className="py-3 px-4">
                        <span
                          className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_COLORS.approved}`}
                        >
                          {r.approved || 0}
                        </span>
                      </td>
                      <td className="py-3 px-4">
                        <span
                          className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_COLORS.paid}`}
                        >
                          {r.paid || 0}
                        </span>
                      </td>
                      <td className="py-3 px-4">
                        <span
                          className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_COLORS.rejected}`}
                        >
                          {r.rejected || 0}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="py-16 text-center text-aegisaxiom-text-muted text-sm">
              No commission data available.
            </div>
          )}
        </div>
      ) : (
        /* ---- TABLE VIEW ---- */
        <div className="glass-card overflow-hidden">
          <div className="flex gap-1 p-3 border-b border-aegisaxiom-border overflow-x-auto">
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                data-testid={`tab-${t.toLowerCase()}`}
                className={`flex-1 sm:flex-none justify-center px-2 sm:px-3 py-1.5 rounded-lg text-xs font-medium transition-all whitespace-nowrap flex items-center gap-1.5 min-w-0
                  ${tab === t ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-500/40' : 'text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary border border-transparent'}`}
              >
                <span className="truncate">{t}</span>
                <span
                  className={`shrink-0 text-xs rounded-full px-1.5 py-0.5 ${tab === t ? 'bg-violet-600/30' : 'bg-aegisaxiom-elevated'}`}
                >
                  {tabCount(t)}
                </span>
              </button>
            ))}
          </div>
          <div className="hidden md:block overflow-x-auto">
            <table className="w-full text-sm" data-testid="commissions-table">
              <thead>
                <tr className="border-b border-aegisaxiom-border">
                  {['Deal', 'Recipient', 'Type', 'Owed', 'Paid', 'Rate', 'Status'].map((h) => (
                    <th
                      key={h}
                      className="text-left py-3 px-4 text-xs font-medium text-aegisaxiom-text-muted uppercase tracking-wider"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="py-16 text-center text-aegisaxiom-text-muted">
                      No commissions for this filter.
                    </td>
                  </tr>
                ) : (
                  filtered.map((c) => (
                    <tr
                      key={c.commission_id}
                      className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted"
                    >
                      <td className="py-3 px-4 text-aegisaxiom-text-secondary font-medium text-sm">
                        {c.deal_name || c.deal_id}
                      </td>
                      <td className="py-3 px-4 text-aegisaxiom-text-secondary">
                        {c.user_name || c.user_id}
                      </td>
                      <td className="py-3 px-4">
                        <span
                          className={`text-xs px-2 py-0.5 rounded-full ${c.commission_type === 'agent' ? 'bg-fuchsia-50 dark:bg-fuchsia-600/20 text-fuchsia-700 dark:text-fuchsia-300' : 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300'}`}
                        >
                          {c.commission_type}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-amber-600 dark:text-amber-400 font-medium">
                        {fmt(c.amount_owed)}
                      </td>
                      <td className="py-3 px-4 text-emerald-600 dark:text-emerald-400 font-medium">
                        {fmt(c.amount_paid)}
                      </td>
                      <td className="py-3 px-4 text-aegisaxiom-text-secondary">{c.commission_rate}%</td>
                      <td className="py-3 px-4">
                        <span
                          className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_COLORS[c.status] || 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary border-aegisaxiom-border-hover'}`}
                        >
                          {c.status}
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {/* Mobile card layout */}
          <div className="md:hidden p-3 space-y-3">
            {filtered.length === 0 ? (
              <div className="py-10 text-center text-aegisaxiom-text-muted text-sm">
                No commissions for this filter.
              </div>
            ) : (
              filtered.map((c) => (
                <div
                  key={c.commission_id}
                  className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5"
                >
                  {/* Header: deal name + status */}
                  <div className="flex items-start justify-between gap-2 mb-2.5">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-semibold text-aegisaxiom-text truncate">
                        {c.deal_name || c.deal_id}
                      </div>
                      <div className="text-xs text-aegisaxiom-text-muted truncate mt-0.5">
                        {c.user_name || c.user_id}
                      </div>
                    </div>
                    <span
                      className={`shrink-0 text-[10px] font-medium px-2 py-0.5 rounded-full border uppercase tracking-wide ${STATUS_COLORS[c.status] || 'bg-aegisaxiom-surface text-aegisaxiom-text border-aegisaxiom-border-hover'}`}
                    >
                      {c.status}
                    </span>
                  </div>

                  {/* Type pill */}
                  <div className="mb-3">
                    <span
                      className={`text-[10px] font-medium px-2 py-0.5 rounded-full uppercase tracking-wide ${c.commission_type === 'agent' ? 'bg-fuchsia-50 dark:bg-fuchsia-600/20 text-fuchsia-700 dark:text-fuchsia-300' : 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300'}`}
                    >
                      {c.commission_type}
                    </span>
                  </div>

                  {/* Amounts grid */}
                  <div className="grid grid-cols-3 gap-2 pt-3 border-t border-aegisaxiom-border">
                    <div>
                      <div className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wide mb-0.5">
                        Owed
                      </div>
                      <div className="text-sm font-semibold text-amber-600 dark:text-amber-400 tabular-nums">
                        {fmt(c.amount_owed)}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wide mb-0.5">
                        Paid
                      </div>
                      <div className="text-sm font-semibold text-emerald-600 dark:text-emerald-400 tabular-nums">
                        {fmt(c.amount_paid)}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wide mb-0.5">
                        Rate
                      </div>
                      <div className="text-sm font-semibold text-aegisaxiom-text tabular-nums">
                        {c.commission_rate}%
                      </div>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}
      {/* Zoho Synced Commissions — visible to admin/director/finance when Zoho data exists */}
      {zohoComms.length > 0 && (
        <div className="glass-card overflow-hidden" data-testid="zoho-commissions-section">
          <div className="p-4 border-b border-aegisaxiom-border">
            <h2
              className="text-sm font-semibold text-aegisaxiom-text"
              style={{ fontFamily: 'Manrope, sans-serif' }}
            >
              Zoho Synced Commissions
            </h2>
            <p className="text-aegisaxiom-text-muted text-xs mt-0.5">
              {zohoComms.length} records from Zoho CRM
            </p>
          </div>
          <div className="flex gap-1 p-3 border-b border-aegisaxiom-border overflow-x-auto">
            {ZOHO_TABS.map((t) => (
              <button
                key={t}
                onClick={() => setZohoTab(t)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all whitespace-nowrap flex items-center gap-1.5
                  ${zohoTab === t ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-500/40' : 'text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary border border-transparent'}`}
              >
                {t}
                <span
                  className={`text-xs rounded-full px-1.5 py-0.5 ${zohoTab === t ? 'bg-violet-600/30' : 'bg-aegisaxiom-elevated'}`}
                >
                  {zohoTabCount(t)}
                </span>
              </button>
            ))}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="zoho-commissions-table">
              <thead>
                <tr className="border-b border-aegisaxiom-border">
                  {[
                    'Deal ID',
                    'Deal',
                    'Customer',
                    'Sales Rep',
                    'Agent',
                    'Rep Commission',
                    'Agent Commission',
                    'Status',
                    'Issued Date',
                    'Due Date',
                    'Payment Date',
                    '',
                  ].map((h) => (
                    <th
                      key={h}
                      className="text-left py-3 px-4 text-xs font-medium text-aegisaxiom-text-muted uppercase tracking-wider"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredZoho.length === 0 ? (
                  <tr>
                    <td
                      colSpan={ADMIN_ZOHO_COL_COUNT}
                      className="py-12 text-center text-aegisaxiom-text-muted"
                    >
                      No records for this filter.
                    </td>
                  </tr>
                ) : (
                  filteredZoho.map((c, i) => (
                    <tr
                      key={c.zoho_id || i}
                      className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted"
                    >
                      <td className="py-2.5 px-4 text-aegisaxiom-text-muted text-xs font-mono">
                        {c.deal_id || '—'}
                      </td>
                      <td className="py-2.5 px-4 text-aegisaxiom-text text-sm">{c.deal_name || '—'}</td>
                      <td className="py-2.5 px-4 text-aegisaxiom-text-secondary text-sm">
                        {c.customer_name || '—'}
                      </td>
                      <td className="py-2.5 px-4 text-aegisaxiom-text-secondary text-sm">
                        {c.sales_rep_name || '—'}
                      </td>
                      <td className="py-2.5 px-4 text-aegisaxiom-text-secondary text-sm">
                        {c.agent_of_records_name || '—'}
                      </td>
                      <td className="py-2.5 px-4 text-emerald-600 dark:text-emerald-400 font-medium">
                        {fmt(c.sales_rep_commission)}
                      </td>
                      <td className="py-2.5 px-4 text-fuchsia-600 dark:text-fuchsia-400 font-medium">
                        {fmt(c.agent_commission)}
                      </td>
                      <td className="py-2.5 px-4">
                        <span
                          className={`text-xs px-2 py-0.5 rounded-full ${
                            c[statusField] === 'Paid'
                              ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300'
                              : c[statusField] === 'Owed'
                                ? 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300'
                                : 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300'
                          }`}
                        >
                          {c[statusField] || '—'}
                        </span>
                      </td>
                      <td className="py-2.5 px-4 text-aegisaxiom-text-muted text-xs">
                        {fmtDate(c.invoice_issued_date)}
                      </td>
                      <td className="py-2.5 px-4 text-aegisaxiom-text-muted text-xs">
                        {fmtDate(c.invoice_due_date)}
                      </td>
                      <td className="py-2.5 px-4 text-aegisaxiom-text-muted text-xs">
                        {fmtDate(c.payment_date)}
                      </td>
                      <td className="py-2.5 px-4">
                        <button
                          onClick={() => setEditZohoComm(c)}
                          className="text-aegisaxiom-text-muted hover:text-violet-600 dark:text-violet-400 transition-colors"
                          data-testid={`edit-zoho-comm-admin-${c.zoho_id}`}
                        >
                          <Edit2 size={14} />
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {/* Create Commission Modal */}
      {modal && (
        <div
          className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-4"
          data-testid="commission-modal"
        >
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-md p-6 animate-fadeInUp">
            <div className="flex items-center justify-between mb-5">
              <h2
                className="text-lg font-bold text-aegisaxiom-text"
                style={{ fontFamily: 'Manrope, sans-serif' }}
              >
                New Commission
              </h2>
              <button onClick={() => setModal(null)}>
                <X size={18} className="text-aegisaxiom-text-secondary" />
              </button>
            </div>
            <form onSubmit={handleSubmit} className="space-y-3">
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Deal *</label>
                <select
                  className="input-field"
                  value={form.deal_id}
                  onChange={(e) => setForm({ ...form, deal_id: e.target.value })}
                  required
                >
                  <option value="">Select Deal</option>
                  {deals.map((d) => (
                    <option key={d.deal_id} value={d.deal_id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Recipient *</label>
                <select
                  className="input-field"
                  value={form.user_id}
                  onChange={(e) => setForm({ ...form, user_id: e.target.value })}
                  required
                >
                  <option value="">Select User</option>
                  {users
                    .filter((u) => ['agent', 'sales_rep'].includes(u.role))
                    .map((u) => (
                      <option key={u.user_id} value={u.user_id}>
                        {u.name} ({u.role})
                      </option>
                    ))}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Type</label>
                  <select
                    className="input-field"
                    value={form.commission_type}
                    onChange={(e) => setForm({ ...form, commission_type: e.target.value })}
                  >
                    <option value="agent">Agent</option>
                    <option value="sales_rep">Sales Rep</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Rate (%)</label>
                  <input
                    type="number"
                    step="0.1"
                    className="input-field"
                    placeholder="e.g. 10"
                    value={form.commission_rate}
                    onChange={(e) => setForm({ ...form, commission_rate: e.target.value })}
                  />
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                    Amount Owed ($)
                  </label>
                  <input
                    type="number"
                    className="input-field"
                    placeholder="0"
                    value={form.amount_owed}
                    onChange={(e) => setForm({ ...form, amount_owed: e.target.value })}
                  />
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                    Potential ($)
                  </label>
                  <input
                    type="number"
                    className="input-field"
                    placeholder="0"
                    value={form.potential_amount}
                    onChange={(e) => setForm({ ...form, potential_amount: e.target.value })}
                  />
                </div>
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Notes</label>
                <input
                  type="text"
                  className="input-field"
                  placeholder="Optional notes..."
                  value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                />
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
                  disabled={loading}
                  className="btn-primary flex-1"
                  data-testid="commission-save-btn"
                >
                  {loading ? 'Saving...' : 'Save'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
      {/* Reject Modal */}
      {rejectModal && (
        <div
          className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-4"
          data-testid="reject-modal"
        >
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-sm p-6 animate-fadeInUp">
            <div className="flex items-center justify-between mb-4">
              <h2
                className="text-lg font-bold text-aegisaxiom-text"
                style={{ fontFamily: 'Manrope, sans-serif' }}
              >
                Reject Commission
              </h2>
              <button onClick={() => setRejectModal(null)}>
                <X size={18} className="text-aegisaxiom-text-secondary" />
              </button>
            </div>
            <p className="text-aegisaxiom-text-secondary text-sm mb-4">
              Please provide a reason for rejection.
            </p>
            <textarea
              className="input-field w-full h-24 resize-none"
              placeholder="Rejection reason..."
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              data-testid="reject-reason-input"
            />
            <div className="flex gap-3 mt-4">
              <button onClick={() => setRejectModal(null)} className="btn-secondary flex-1">
                Cancel
              </button>
              <button
                onClick={rejectComm}
                disabled={loading}
                className="btn-primary flex-1 !bg-red-50 dark:bg-red-600/20 !border-red-500/50 !text-red-700 dark:text-red-300 hover:!bg-red-600/30"
                data-testid="confirm-reject-btn"
              >
                {loading ? 'Rejecting...' : 'Reject'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Zoho Commission Edit Modal */}
      {editZohoComm && (
        <ZohoCommissionEditModal
          commission={editZohoComm}
          onClose={() => setEditZohoComm(null)}
          onSaved={load}
        />
      )}
    </div>
  );
}
