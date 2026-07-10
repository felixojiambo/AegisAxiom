import React, { useEffect, useState } from 'react';
import { api, unwrap } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/hooks/usePermissions';
import { useSearchParams } from 'react-router-dom';
import {
  Plus,
  Search,
  Edit2,
  Trash2,
  X,
  Building2,
  Users,
  Mail,
  Phone,
  Smartphone,
  Briefcase,
  Linkedin,
  Star,
  UserPlus,
  User,
  Eye,
  TestTube,
} from 'lucide-react';
import { toast } from 'sonner';
import API_URL from '../lib/apiConfig';
import ContactFormModal from './contacts/ContactFormModal';
import ConfirmModal from './shared/ConfirmModal';
import TabStrip from './shared/TabStrip';

const API = API_URL;

// Shared predicate: what counts as a "partner" account in the UI.
// Kept here (not in a util) because Accounts is the only consumer — the
// backend does the same check server-side in dropdown-data and create/update
// validators (see routes/opportunities.py). Count + filter MUST use the same
// predicate or the tab count drifts from the visible rows.
export const isPartnerAccount = (a) =>
  a?.account_type === 'partner_agency' || a?.is_partner === true;

const EMPTY = {
  name: '',
  account_type: 'company',
  email: '',
  phone: '',
  website: '',
  address: '',
  industry: '',
  assigned_sales_rep_id: '',
  assigned_agent_id: '',
  is_test_data: false,
};
const INDUSTRIES = [
  'Technology',
  'Healthcare',
  'Finance',
  'Energy',
  'Manufacturing',
  'Retail',
  'Education',
  'Government',
  'Other',
];

export default function Accounts() {
  const { user } = useAuth();
  const { isAdmin, hasPermission, hasAnyPermission } = usePermissions();
  const [searchParams, setSearchParams] = useSearchParams();
  const [accounts, setAccounts] = useState([]);
  const [users, setUsers] = useState([]);
  const [search, setSearch] = useState('');
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [loading, setLoading] = useState(false);
  const [testOnly, setTestOnly] = useState(false);
  const [viewType, setViewType] = useState('all'); // NEW: 'all' or 'partners'
  const canEdit = hasAnyPermission(['accounts.edit', 'accounts.edit.own', 'accounts.create']);

  // Account detail modal with contacts
  const [viewAccount, setViewAccount] = useState(null);
  const [accountContacts, setAccountContacts] = useState([]);
  const [activeTab, setActiveTab] = useState('details'); // 'details' or 'contacts'
  const [showContactModal, setShowContactModal] = useState(false);
  const [selectedContact, setSelectedContact] = useState(null);
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });

  useEffect(() => {
    load();
  }, [testOnly]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-open account from URL parameter
  useEffect(() => {
    const accountId = searchParams.get('id');
    if (accountId && accounts.length > 0) {
      const account = accounts.find((a) => a.account_id === accountId);
      if (account) {
        openView(account);
        // Clear the parameter after opening
        setSearchParams({});
      }
    }
  }, [accounts, searchParams]);

  const load = async () => {
    const qs = testOnly ? '?test_only=true' : '?test_only=false';
    const ar = await api.get(`${API}/api/accounts${qs}`);
    if (ar.ok) setAccounts(unwrap(await ar.json()));
    if (hasAnyPermission(['users.manage', 'users.view'])) {
      const ur = await api.get(`${API}/api/admin/users`);
      if (ur.ok) setUsers(await ur.json());
    }
  };

  const openCreate = () => {
    setForm(EMPTY);
    setModal('create');
  };
  const openEdit = (a) => {
    setForm(a);
    setModal('edit');
  };

  const openView = async (account) => {
    setViewAccount(account);
    setActiveTab('details');
    await loadAccountContacts(account.account_id);
  };

  const loadAccountContacts = async (accountId) => {
    try {
      const response = await api.get(`${API}/api/contacts?account_id=${accountId}`);
      const data = await response.json();
      setAccountContacts(data.items || []);
    } catch (error) {
      console.error('Error loading contacts:', error);
      toast.error('Failed to load contacts');
    }
  };

  const handleAddContact = () => {
    setSelectedContact(null);
    setShowContactModal(true);
  };

  const handleEditContact = (contact) => {
    setSelectedContact(contact);
    setShowContactModal(true);
  };

  const handleDeleteContact = (contactId) => {
    setConfirmState({
      open: true,
      title: 'Delete Contact',
      message: 'Delete this contact? This action cannot be undone.',
      action: async () => {
        try {
          const response = await api.delete(`${API}/api/contacts/${contactId}`);
          await response.json();
          toast.success('Contact deleted');
          await loadAccountContacts(viewAccount.account_id);
        } catch (error) {
          console.error('Error deleting contact:', error);
          const errorData = await error.response?.json();
          toast.error(errorData?.detail || 'Failed to delete contact');
        }
      },
    });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const url =
        modal === 'edit' ? `${API}/api/accounts/${form.account_id}` : `${API}/api/accounts`;
      const res = await (modal === 'edit' ? api.put(url, form) : api.post(url, form));
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail);
      }
      toast.success(modal === 'edit' ? 'Account updated' : 'Account created');
      setModal(null);
      load();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = (id) => {
    setConfirmState({
      open: true,
      title: 'Delete Account',
      message: 'Delete this account? This action cannot be undone.',
      action: async () => {
        const res = await api.delete(`${API}/api/accounts/${id}`);
        if (res.ok) {
          toast.success('Account deleted');
          load();
        } else toast.error('Failed to delete');
      },
    });
  };

  // Filter by search and view type (all/partners)
  const partnerCount = accounts.filter(isPartnerAccount).length;
  const filtered = accounts.filter((a) => {
    const matchesSearch =
      a.name?.toLowerCase().includes(search.toLowerCase()) ||
      a.industry?.toLowerCase().includes(search.toLowerCase());
    const matchesViewType = viewType === 'all' || (viewType === 'partners' && isPartnerAccount(a));
    return matchesSearch && matchesViewType;
  });

  const salesReps = users.filter((u) => u.role === 'sales_rep');
  const agents = users.filter((u) => u.role === 'agent');

  return (
    <div className="space-y-5" data-testid="accounts-page">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
        <div className="min-w-0">
          <h1 className="page-title">Accounts</h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">{accounts.length} total</p>
        </div>
        <div className="flex items-center gap-2 sm:gap-3 flex-wrap sm:flex-nowrap">
          {isAdmin && (
            <button
              type="button"
              onClick={() => setTestOnly(!testOnly)}
              aria-pressed={testOnly}
              className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-xs font-medium transition shrink-0 ${
                testOnly
                  ? 'bg-amber-50 dark:bg-amber-600/20 border-amber-500 text-amber-700 dark:text-amber-300'
                  : 'bg-aegisaxiom-elevated border-aegisaxiom-border text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover hover:text-aegisaxiom-text'
              }`}
            >
              <TestTube className="w-3.5 h-3.5 shrink-0" />
              <span className="whitespace-nowrap">Test Data Only</span>
            </button>
          )}
          {canEdit && (
            <button
              onClick={openCreate}
              className="btn-primary flex items-center justify-center gap-2 shrink-0"
              data-testid="create-account-btn"
            >
              <Plus size={15} />
              <span className="whitespace-nowrap">New Account</span>
            </button>
          )}
        </div>
      </div>

      {/* Tabs */}
      <TabStrip
        variant="underline"
        activeId={viewType}
        onChange={setViewType}
        tabs={[
          { id: 'all', label: 'All Accounts', icon: Building2 },
          {
            id: 'partners',
            label: `Partners (${partnerCount})`,
            icon: Star,
          },
        ]}
      />

      {/* Search */}
      <div className="relative">
        <Search
          size={15}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted"
        />
        <input
          className="input-field pl-9"
          placeholder="Search accounts..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          data-testid="accounts-search"
        />
      </div>

      {/* Accounts grid */}
      {loading ? (
        <div className="flex items-center justify-center py-16">
          <div className="w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="glass-card p-10 sm:p-16 text-center">
          <Building2 className="w-12 h-12 text-aegisaxiom-text-muted mx-auto mb-3" />
          <h3 className="text-aegisaxiom-text font-semibold mb-1">
            {search
              ? 'No accounts match your search'
              : viewType === 'partners'
                ? 'No partner accounts yet'
                : 'No accounts yet'}
          </h3>
          <p className="text-aegisaxiom-text-muted text-sm">
            {search
              ? 'Try a different search term or clear the filter.'
              : viewType === 'partners'
                ? 'Accounts marked as partners will appear here.'
                : 'Create your first account to get started.'}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((a) => (
            <div
              key={a.account_id}
              className="glass-card p-4 sm:p-5 hover:border-violet-500/30 transition-all group"
              data-testid={`account-card-${a.account_id}`}
            >
              <div className="flex items-start justify-between gap-2 mb-3">
                <div className="flex items-center gap-3 min-w-0 flex-1">
                  <div className="w-9 h-9 rounded-lg bg-violet-50 dark:bg-violet-600/20 border border-violet-200 dark:border-violet-600/20 flex items-center justify-center shrink-0">
                    <Building2 size={16} className="text-violet-600 dark:text-violet-400" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-aegisaxiom-text font-semibold text-sm truncate">{a.name}</p>
                    <p className="text-aegisaxiom-text-muted text-xs truncate">
                      {a.industry || 'No industry'}
                    </p>
                  </div>
                </div>
                {canEdit && (
                  <div className="flex gap-1 shrink-0 opacity-100 md:opacity-0 md:group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => openView(a)}
                      className="p-1.5 rounded-md text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 hover:bg-aegisaxiom-elevated"
                      title="View details"
                      aria-label={`View ${a.name} details`}
                    >
                      <Eye size={14} />
                    </button>
                    <button
                      onClick={() => openEdit(a)}
                      className="p-1.5 rounded-md text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 hover:bg-aegisaxiom-elevated"
                      title="Edit"
                      aria-label={`Edit ${a.name}`}
                    >
                      <Edit2 size={14} />
                    </button>
                    {hasPermission('accounts.delete') && (
                      <button
                        onClick={() => handleDelete(a.account_id)}
                        className="p-1.5 rounded-md text-aegisaxiom-text-muted hover:text-red-600 dark:hover:text-red-400 hover:bg-aegisaxiom-elevated"
                        title="Delete"
                        aria-label={`Delete ${a.name}`}
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                )}
              </div>
              <div className="space-y-1 text-xs text-aegisaxiom-text-muted">
                {a.email && <p>{a.email}</p>}
                {a.phone && <p>{a.phone}</p>}
                {a.website && (
                  <a
                    href={a.website}
                    target="_blank"
                    rel="noreferrer"
                    className="text-violet-600 dark:text-violet-400 hover:underline"
                  >
                    {a.website}
                  </a>
                )}
              </div>
              {/* Owner */}
              {(a.assigned_sales_rep_name || a.assigned_sales_rep_id) && (
                <div className="flex items-center gap-1.5 mt-2 text-xs">
                  <User size={11} className="text-aegisaxiom-text-muted flex-shrink-0" />
                  <span className="text-aegisaxiom-text-secondary">
                    {a.assigned_sales_rep_name ||
                      users.find((u) => u.user_id === a.assigned_sales_rep_id)?.name ||
                      'Assigned Rep'}
                  </span>
                </div>
              )}

              {/* Partner-specific info */}
              {a.is_partner && (
                <div className="mt-3 pt-3 border-t border-aegisaxiom-border space-y-2">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-aegisaxiom-text-muted">Tier:</span>
                    <span
                      className={`px-2 py-0.5 rounded-full font-medium ${
                        a.partner_tier === 'gold'
                          ? 'bg-yellow-500/20 text-yellow-700 dark:text-yellow-300'
                          : a.partner_tier === 'silver'
                            ? 'bg-aegisaxiom-accent-muted text-aegisaxiom-text-secondary'
                            : 'bg-amber-700/20 text-amber-700 dark:text-amber-300'
                      }`}
                    >
                      {a.partner_tier?.toUpperCase() || 'BRONZE'}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-aegisaxiom-text-muted">Active Deals:</span>
                    <span className="text-aegisaxiom-text font-medium">
                      {a.partner_active_deals_count || 0}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-aegisaxiom-text-muted">Total Revenue:</span>
                    <span className="text-green-600 dark:text-green-400 font-medium">
                      ${(a.partner_total_revenue || 0).toLocaleString()}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-aegisaxiom-text-muted">Commission:</span>
                    <span className="text-violet-600 dark:text-violet-400 font-medium">
                      {a.partner_commission_rate || 0}%
                    </span>
                  </div>
                </div>
              )}

              <div className="flex items-center justify-between mt-3 pt-3 border-t border-aegisaxiom-border">
                <div className="flex items-center gap-2">
                  <span
                    className={`text-xs px-2 py-0.5 rounded-full ${a.account_type === 'partner_agency' ? 'bg-fuchsia-50 dark:bg-fuchsia-600/20 text-fuchsia-700 dark:text-fuchsia-300' : 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300'}`}
                  >
                    {a.account_type}
                  </span>
                  {a.is_partner && (
                    <Star
                      className="w-3 h-3 text-yellow-600 dark:text-yellow-400 fill-yellow-400"
                      title="Partner Account"
                    />
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
      {modal && (
        <div
          className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-4"
          data-testid="account-modal"
        >
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-lg p-6 animate-fadeInUp">
            <div className="flex items-center justify-between mb-5">
              <h2
                className="text-lg font-bold text-aegisaxiom-text"
                style={{ fontFamily: 'Manrope, sans-serif' }}
              >
                {modal === 'edit' ? 'Edit Account' : 'New Account'}
              </h2>
              <button onClick={() => setModal(null)}>
                <X size={18} className="text-aegisaxiom-text-secondary" />
              </button>
            </div>
            <form onSubmit={handleSubmit} className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2">
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                    Company Name *
                  </label>
                  <input
                    className="input-field"
                    value={form.name || ''}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    required
                  />
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Type</label>
                  <select
                    className="input-field"
                    value={form.account_type || 'company'}
                    onChange={(e) => setForm({ ...form, account_type: e.target.value })}
                  >
                    <option value="company">Company</option>
                    <option value="partner_agency">Partner Agency</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Industry</label>
                  <select
                    className="input-field"
                    value={form.industry || ''}
                    onChange={(e) => setForm({ ...form, industry: e.target.value })}
                  >
                    <option value="">Select...</option>
                    {INDUSTRIES.map((i) => (
                      <option key={i}>{i}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Email</label>
                  <input
                    type="email"
                    className="input-field"
                    value={form.email || ''}
                    onChange={(e) => setForm({ ...form, email: e.target.value })}
                  />
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Phone</label>
                  <input
                    className="input-field"
                    value={form.phone || ''}
                    onChange={(e) => setForm({ ...form, phone: e.target.value })}
                  />
                </div>
                <div className="col-span-2">
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Website</label>
                  <input
                    className="input-field"
                    value={form.website || ''}
                    onChange={(e) => setForm({ ...form, website: e.target.value })}
                  />
                </div>
                {hasAnyPermission(['users.manage', 'users.view']) && (
                  <>
                    <div>
                      <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                        Sales Rep
                      </label>
                      <select
                        className="input-field"
                        value={form.assigned_sales_rep_id || ''}
                        onChange={(e) =>
                          setForm({ ...form, assigned_sales_rep_id: e.target.value })
                        }
                      >
                        <option value="">None</option>
                        {salesReps.map((u) => (
                          <option key={u.user_id} value={u.user_id}>
                            {u.name}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Agent</label>
                      <select
                        className="input-field"
                        value={form.assigned_agent_id || ''}
                        onChange={(e) => setForm({ ...form, assigned_agent_id: e.target.value })}
                      >
                        <option value="">None</option>
                        {agents.map((u) => (
                          <option key={u.user_id} value={u.user_id}>
                            {u.name}
                          </option>
                        ))}
                      </select>
                    </div>
                  </>
                )}
                {isAdmin && (
                  <div className="col-span-2 flex items-center gap-2 pt-1">
                    <input
                      type="checkbox"
                      id="account_is_test_data"
                      checked={form.is_test_data || false}
                      onChange={(e) => setForm({ ...form, is_test_data: e.target.checked })}
                      className="w-4 h-4 rounded border-aegisaxiom-border-hover bg-aegisaxiom-elevated text-amber-500 focus:ring-amber-500"
                    />
                    <label
                      htmlFor="account_is_test_data"
                      className="text-sm text-aegisaxiom-text-secondary"
                    >
                      Mark as test data
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
                  disabled={loading}
                  className="btn-primary flex-1"
                  data-testid="account-save-btn"
                >
                  {loading ? 'Saving...' : 'Save'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Account Detail Modal with Contacts Tab */}
      {viewAccount && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-5xl max-h-[90vh] overflow-hidden flex flex-col">
            {/* Header */}
            <div className="flex items-start justify-between p-6 border-b border-aegisaxiom-border">
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 rounded-lg bg-violet-50 dark:bg-violet-600/20 border border-violet-200 dark:border-violet-600/20 flex items-center justify-center">
                  <Building2 className="w-6 h-6 text-violet-600 dark:text-violet-400" />
                </div>
                <div>
                  <h2 className="text-xl font-bold text-aegisaxiom-text">{viewAccount.name}</h2>
                  <p className="text-sm text-aegisaxiom-text-secondary">
                    {viewAccount.industry || 'No industry'}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => {
                    setViewAccount(null);
                    openEdit(viewAccount);
                  }}
                  className="p-2 hover:bg-aegisaxiom-elevated rounded-lg transition-colors"
                  title="Edit Account"
                >
                  <Edit2 className="w-5 h-5 text-aegisaxiom-text-secondary" />
                </button>
                <button
                  onClick={() => setViewAccount(null)}
                  className="p-2 hover:bg-aegisaxiom-elevated rounded-lg transition-colors"
                >
                  <X className="w-5 h-5 text-aegisaxiom-text-secondary" />
                </button>
              </div>
            </div>

            {/* Tabs */}
            <div className="flex border-b border-aegisaxiom-border px-6">
              <button
                onClick={() => setActiveTab('details')}
                className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === 'details'
                    ? 'border-violet-500 text-aegisaxiom-text'
                    : 'border-transparent text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
                }`}
              >
                Details
              </button>
              <button
                onClick={() => setActiveTab('contacts')}
                className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === 'contacts'
                    ? 'border-violet-500 text-aegisaxiom-text'
                    : 'border-transparent text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
                }`}
              >
                Contacts ({accountContacts.length})
              </button>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto p-6">
              {/* Details Tab */}
              {activeTab === 'details' && (
                <div className="space-y-4">
                  {/* Partnership block — only renders for partner accounts. */}
                  {isPartnerAccount(viewAccount) && (
                    <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                      <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3 flex items-center gap-2">
                        <Star
                          className="w-3.5 h-3.5 text-yellow-600 dark:text-yellow-400 fill-yellow-400"
                        />
                        Partnership
                      </h3>
                      <div className="grid grid-cols-3 gap-4">
                        <div>
                          <div className="text-xs text-aegisaxiom-text-secondary mb-1">Status</div>
                          <div className="text-sm text-aegisaxiom-text">
                            {viewAccount.partnership_status
                              ? viewAccount.partnership_status.charAt(0).toUpperCase() +
                                viewAccount.partnership_status.slice(1)
                              : 'Pending'}
                          </div>
                        </div>
                        <div>
                          <div className="text-xs text-aegisaxiom-text-secondary mb-1">Signed Date</div>
                          <div className="text-sm text-aegisaxiom-text">
                            {viewAccount.partnership_signed_date
                              ? new Date(
                                  viewAccount.partnership_signed_date,
                                ).toLocaleDateString('en-US', {
                                  year: 'numeric',
                                  month: 'short',
                                  day: 'numeric',
                                })
                              : '—'}
                          </div>
                        </div>
                        <div>
                          <div className="text-xs text-aegisaxiom-text-secondary mb-1">
                            Commission Rate
                          </div>
                          <div className="text-sm text-aegisaxiom-text">
                            {viewAccount.commission_rate === null ||
                            viewAccount.commission_rate === undefined
                              ? '—'
                              : `${viewAccount.commission_rate}%`}
                          </div>
                        </div>
                      </div>
                      <p className="text-xs text-aegisaxiom-text-muted mt-3">
                        Edit from User Management → Partners.
                      </p>
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-4">
                    {viewAccount.email && (
                      <div>
                        <div className="text-xs text-aegisaxiom-text-secondary mb-1">Email</div>
                        <a
                          href={`mailto:${viewAccount.email}`}
                          className="text-sm text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300"
                        >
                          {viewAccount.email}
                        </a>
                      </div>
                    )}
                    {viewAccount.phone && (
                      <div>
                        <div className="text-xs text-aegisaxiom-text-secondary mb-1">Phone</div>
                        <div className="text-sm text-aegisaxiom-text">{viewAccount.phone}</div>
                      </div>
                    )}
                    {viewAccount.website && (
                      <div>
                        <div className="text-xs text-aegisaxiom-text-secondary mb-1">Website</div>
                        <a
                          href={viewAccount.website}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-sm text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300"
                        >
                          {viewAccount.website}
                        </a>
                      </div>
                    )}
                    {viewAccount.address && (
                      <div>
                        <div className="text-xs text-aegisaxiom-text-secondary mb-1">Address</div>
                        <div className="text-sm text-aegisaxiom-text">{viewAccount.address}</div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Contacts Tab */}
              {activeTab === 'contacts' && (
                <div className="space-y-4">
                  {/* Add Contact Button */}
                  <div className="flex justify-end">
                    <button
                      onClick={handleAddContact}
                      className="flex items-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-700 rounded-lg transition-colors text-sm"
                    >
                      <Plus className="w-4 h-4" />
                      Add Contact
                    </button>
                  </div>

                  {/* Contacts List */}
                  {accountContacts.length === 0 ? (
                    <div className="text-center py-12 text-aegisaxiom-text-muted">
                      <Users className="w-12 h-12 mx-auto mb-3 opacity-50" />
                      <p>No contacts for this account yet</p>
                      <button
                        onClick={handleAddContact}
                        className="mt-4 text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 text-sm"
                      >
                        Add your first contact
                      </button>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {accountContacts.map((contact) => (
                        <div
                          key={contact.contact_id}
                          className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4 hover:border-aegisaxiom-border-hover transition-colors"
                        >
                          <div className="flex items-start justify-between">
                            <div className="flex-1">
                              <div className="flex items-center gap-2 mb-2">
                                {contact.is_primary && (
                                  <Star
                                    className="w-4 h-4 text-yellow-500 fill-yellow-500"
                                    title="Primary Contact"
                                  />
                                )}
                                <h4 className="text-aegisaxiom-text font-medium">{contact.name}</h4>
                                {!contact.is_active && (
                                  <span className="text-xs px-2 py-0.5 bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary rounded">
                                    Inactive
                                  </span>
                                )}
                              </div>

                              <div className="space-y-1 text-sm">
                                {contact.title && (
                                  <div className="flex items-center gap-2 text-aegisaxiom-text-secondary">
                                    <Briefcase className="w-3 h-3 text-aegisaxiom-text-muted" />
                                    <span>{contact.title}</span>
                                    {contact.department && (
                                      <span className="text-aegisaxiom-text-muted">
                                        • {contact.department}
                                      </span>
                                    )}
                                  </div>
                                )}
                                {contact.email && (
                                  <div className="flex items-center gap-2">
                                    <Mail className="w-3 h-3 text-aegisaxiom-text-muted" />
                                    <a
                                      href={`mailto:${contact.email}`}
                                      className="text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300"
                                    >
                                      {contact.email}
                                    </a>
                                  </div>
                                )}
                                {contact.phone && (
                                  <div className="flex items-center gap-2 text-aegisaxiom-text-secondary">
                                    <Phone className="w-3 h-3 text-aegisaxiom-text-muted" />
                                    <span>{contact.phone}</span>
                                  </div>
                                )}
                                {contact.mobile_phone && (
                                  <div className="flex items-center gap-2 text-aegisaxiom-text-secondary">
                                    <Smartphone className="w-3 h-3 text-aegisaxiom-text-muted" />
                                    <span>{contact.mobile_phone}</span>
                                  </div>
                                )}
                                {contact.linkedin_url && (
                                  <div className="flex items-center gap-2">
                                    <Linkedin className="w-3 h-3 text-blue-600 dark:text-blue-400" />
                                    <a
                                      href={contact.linkedin_url}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:text-blue-300 text-xs"
                                    >
                                      View LinkedIn Profile
                                    </a>
                                  </div>
                                )}
                              </div>
                            </div>

                            <div className="flex gap-2 flex-shrink-0">
                              <button
                                onClick={() => handleEditContact(contact)}
                                className="p-2 hover:bg-aegisaxiom-elevated rounded transition-colors"
                                title="Edit"
                              >
                                <Edit2 className="w-4 h-4 text-aegisaxiom-text-secondary" />
                              </button>
                              <button
                                onClick={() => handleDeleteContact(contact.contact_id)}
                                className="p-2 hover:bg-aegisaxiom-elevated rounded transition-colors"
                                title="Delete"
                              >
                                <Trash2 className="w-4 h-4 text-red-600 dark:text-red-400" />
                              </button>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Contact Form Modal (for inline creation) */}
      {showContactModal && (
        <ContactFormModal
          contact={selectedContact}
          editMode={!!selectedContact}
          prefilledAccountId={viewAccount?.account_id}
          onClose={() => {
            setShowContactModal(false);
            setSelectedContact(null);
          }}
          onSave={async () => {
            setShowContactModal(false);
            setSelectedContact(null);
            if (viewAccount) {
              await loadAccountContacts(viewAccount.account_id);
            }
          }}
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
