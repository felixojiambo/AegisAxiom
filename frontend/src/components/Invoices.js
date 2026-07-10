import React, { useEffect, useState } from 'react';
import { api, unwrap } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/hooks/usePermissions';
import { Plus, Search, Edit2, X, RefreshCw, Receipt } from 'lucide-react';
import { toast } from 'sonner';
import API_URL from '../lib/apiConfig';

const API = API_URL;
const fmt = (n) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(n || 0);
const STATUS_COLORS = {
  draft: 'bg-aegisaxiom-elevated/20 text-aegisaxiom-text-secondary',
  sent: 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300',
  paid: 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300',
  overdue: 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300',
  void: 'bg-aegisaxiom-elevated/40 text-aegisaxiom-text-muted line-through',
  pending_approval: 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300',
  approved: 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300',
  rejected: 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300',
};
// Pretty-print enum-style status values: "pending_approval" → "Pending approval"
const formatStatus = (s) => {
  if (!s) return '—';
  const spaced = s.replace(/_/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
};
const EMPTY = {
  account_id: '',
  deal_id: '',
  contract_id: '',
  amount: '',
  tax_amount: '0',
  issued_date: '',
  due_date: '',
  description: '',
};
export default function Invoices() {
  const { user } = useAuth();
  const { hasPermission } = usePermissions();
  const [invoices, setInvoices] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [search, setSearch] = useState('');
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [loading, setLoading] = useState(false);
  const canCreate = hasPermission('invoices.create');
  useEffect(() => {
    load();
  }, []);
  const load = async () => {
    const [ir, ar] = await Promise.all([
      api.get(`${API}/api/finance/ar-invoices`), // Use AR invoices instead
      api.get(`${API}/api/accounts`),
    ]);
    if (ir.ok) {
      const data = await ir.json();
      setInvoices(data.items || []);
    }
    if (ar.ok) setAccounts(unwrap(await ar.json()));
  };
  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    const body = {
      ...form,
      amount: parseFloat(form.amount) || 0,
      tax_amount: parseFloat(form.tax_amount) || 0,
    };
    try {
      const res = await api.post(`${API}/api/invoices`, body);
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail);
      }
      toast.success('Invoice created');
      setModal(null);
      load();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };
  const updateStatus = async (id, status) => {
    const body = {
      status,
      ...(status === 'paid' ? { paid_date: new Date().toISOString().split('T')[0] } : {}),
    };
    try {
      const res = await api.put(`${API}/api/invoices/${id}`, body);
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to update status');
      }
      toast.success('Invoice updated');
      load();
    } catch (err) {
      toast.error(err.message);
    }
  };
  const filtered = invoices.filter(
    (i) =>
      i.invoice_number?.toLowerCase().includes(search.toLowerCase()) ||
      i.account_name?.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <div data-testid="invoices-page">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5">
        <div>
          <h1 className="page-title flex items-center gap-2">
            <Receipt size={22} className="text-violet-600 dark:text-violet-400" />
            Invoices
          </h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">{invoices.length} total</p>
        </div>
        {canCreate && (
          <button
            onClick={() => {
              setForm(EMPTY);
              setModal('new');
            }}
            className="btn-primary flex items-center justify-center gap-2 w-full sm:w-auto shrink-0"
            data-testid="new-invoice-btn"
          >
            <Plus size={16} />
            New Invoice
          </button>
        )}
      </div>
      <div className="relative mb-5">
        <Search
          size={15}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted"
        />
        <input
          className="input-field pl-9"
          placeholder="Search invoices..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          data-testid="invoices-search"
        />
      </div>
      <div className="glass-card overflow-hidden">
        {/* Desktop table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-sm" data-testid="invoices-table">
            <thead>
              <tr className="border-b border-aegisaxiom-border">
                {[
                  'Invoice #',
                  'Account',
                  'Amount',
                  'Tax',
                  'Total',
                  'Issued Date',
                  'Due Date',
                  'Status',
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
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-16 text-center text-aegisaxiom-text-muted">
                    No invoices found.
                  </td>
                </tr>
              ) : (
                filtered.map((inv) => (
                  <tr
                    key={inv.invoice_id}
                    className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted transition-colors"
                    data-testid={`invoice-row-${inv.invoice_id}`}
                  >
                    <td className="py-3 px-4 text-aegisaxiom-text font-medium font-mono text-xs">
                      {inv.invoice_number}
                    </td>
                    <td className="py-3 px-4 text-aegisaxiom-text-secondary">{inv.account_name}</td>
                    <td className="py-3 px-4 text-aegisaxiom-text-secondary">
                      {fmt(inv.subtotal || inv.amount)}
                    </td>
                    <td className="py-3 px-4 text-aegisaxiom-text-secondary">
                      {fmt(inv.tax || inv.tax_amount || 0)}
                    </td>
                    <td className="py-3 px-4 text-aegisaxiom-text font-semibold">
                      {fmt(inv.total || inv.total_amount)}
                    </td>
                    <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                      {inv.issued_date || '—'}
                    </td>
                    <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                      {inv.due_date || '—'}
                    </td>
                    <td className="py-3 px-4">
                      <span
                        className={`badge-stage ${STATUS_COLORS[inv.status] || 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary'}`}
                      >
                        {formatStatus(inv.status)}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Mobile card list */}
        <div className="md:hidden">
          {filtered.length === 0 ? (
            <div className="py-16 px-4 text-center text-aegisaxiom-text-muted text-sm">
              No invoices found.
            </div>
          ) : (
            <div className="p-3 space-y-2.5">
              {filtered.map((inv) => (
                <div
                  key={inv.invoice_id}
                  className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5"
                  data-testid={`invoice-card-${inv.invoice_id}`}
                >
                  {/* Header: invoice # + status */}
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-mono font-medium text-aegisaxiom-text truncate">
                        {inv.invoice_number}
                      </p>
                      <p className="text-[11px] text-aegisaxiom-text-secondary truncate mt-0.5">
                        {inv.account_name || '—'}
                      </p>
                    </div>
                    <span
                      className={`badge-stage shrink-0 ${STATUS_COLORS[inv.status] || 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary'}`}
                    >
                      {formatStatus(inv.status)}
                    </span>
                  </div>

                  {/* Total — most important figure, gets largest weight */}
                  <p className="text-base font-bold text-aegisaxiom-text mb-2.5">
                    {fmt(inv.total || inv.total_amount)}
                  </p>

                  {/* Amount + Tax */}
                  <div className="grid grid-cols-2 gap-2 mb-2.5 text-xs">
                    <div>
                      <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                        Amount
                      </p>
                      <p className="text-aegisaxiom-text-secondary">{fmt(inv.subtotal || inv.amount)}</p>
                    </div>
                    <div>
                      <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                        Tax
                      </p>
                      <p className="text-aegisaxiom-text-secondary">
                        {fmt(inv.tax || inv.tax_amount || 0)}
                      </p>
                    </div>
                  </div>

                  {/* Issued + Due */}
                  <div className="grid grid-cols-2 gap-2 text-xs pt-2 border-t border-aegisaxiom-border">
                    <div>
                      <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                        Issued
                      </p>
                      <p className="text-aegisaxiom-text-secondary">{inv.issued_date || '—'}</p>
                    </div>
                    <div>
                      <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                        Due
                      </p>
                      <p className="text-aegisaxiom-text-secondary">{inv.due_date || '—'}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      {modal && (
        <div
          className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-4"
          data-testid="invoice-modal"
        >
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-md p-6 animate-fadeInUp">
            <div className="flex items-center justify-between mb-5">
              <h2
                className="text-lg font-bold text-aegisaxiom-text"
                style={{ fontFamily: 'Manrope, sans-serif' }}
              >
                New Invoice
              </h2>
              <button onClick={() => setModal(null)}>
                <X size={18} className="text-aegisaxiom-text-secondary" />
              </button>
            </div>
            <form onSubmit={handleSubmit} className="space-y-3">
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Account *</label>
                <select
                  className="input-field"
                  value={form.account_id}
                  onChange={(e) => setForm({ ...form, account_id: e.target.value })}
                  required
                >
                  <option value="">Select Account</option>
                  {accounts.map((a) => (
                    <option key={a.account_id} value={a.account_id}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                    Amount ($) *
                  </label>
                  <input
                    type="number"
                    className="input-field"
                    value={form.amount}
                    onChange={(e) => setForm({ ...form, amount: e.target.value })}
                    required
                  />
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Tax ($)</label>
                  <input
                    type="number"
                    className="input-field"
                    value={form.tax_amount}
                    onChange={(e) => setForm({ ...form, tax_amount: e.target.value })}
                  />
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Issued Date</label>
                  <input
                    type="date"
                    className="input-field [&::-webkit-calendar-picker-indicator]:invert"
                    value={form.issued_date}
                    onChange={(e) => setForm({ ...form, issued_date: e.target.value })}
                  />
                  <p className="text-xs text-aegisaxiom-text-muted mt-1">Defaults to today if blank</p>
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Due Date</label>
                  <input
                    type="date"
                    className="input-field [&::-webkit-calendar-picker-indicator]:invert"
                    value={form.due_date}
                    onChange={(e) => setForm({ ...form, due_date: e.target.value })}
                  />
                </div>
                <div className="col-span-2">
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Description</label>
                  <textarea
                    rows={2}
                    className="input-field resize-none"
                    value={form.description}
                    onChange={(e) => setForm({ ...form, description: e.target.value })}
                  />
                </div>
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
                  data-testid="invoice-save-btn"
                >
                  {loading ? 'Saving...' : 'Create Invoice'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
