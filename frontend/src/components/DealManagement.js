import React, { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/hooks/usePermissions';
import { useNavigate } from 'react-router-dom';
import {
  Target,
  Search,
  Filter,
  DollarSign,
  FileText,
  CheckCircle,
  AlertCircle,
  Clock,
  Building2,
  User,
  Users,
  ChevronDown,
  ChevronUp,
  X,
  Plus,
  Trash2,
  RefreshCw,
  Paperclip,
  MessageSquare,
  ExternalLink,
  Mail,
  Globe,
} from 'lucide-react';
import { toast } from 'sonner';
import DOMPurify from 'dompurify';
import ContractFormFields from './contracts/ContractFormFields';
import API_URL from '../lib/apiConfig';

const API = API_URL;
const fmt = (n) =>
  `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function DealManagement() {
  const { user } = useAuth();
  const { hasPermission, hasAnyPermission, isAdmin } = usePermissions();
  const navigate = useNavigate();
  const [deals, setDeals] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [users, setUsers] = useState([]);
  const [contracts, setContracts] = useState([]);
  const [defaultResourceTypes, setDefaultResourceTypes] = useState([]);
  const [loading, setLoading] = useState(false);

  // Filters
  const [filters, setFilters] = useState({
    search: '',
    account_id: '',
    deal_owner_id: '',
    agent_of_record_id: '',
    contract_status: 'all', // 'all' | 'with_contract' | 'no_contract'
    commission_status: 'all', // 'all' | 'with_commission' | 'no_commission'
  });

  const [showFilters, setShowFilters] = useState(false);
  const [selectedDeal, setSelectedDeal] = useState(null);
  const [quickFilter, setQuickFilter] = useState('all'); // 'all' | 'needs_both' | 'needs_contract' | 'needs_commission' | 'complete'

  // Assign Contract Modal
  const [assignContractDeal, setAssignContractDeal] = useState(null);
  const [assignMode, setAssignMode] = useState('new'); // 'new' | 'existing'
  const [assignForm, setAssignForm] = useState({ contract_id: '' });
  const [assignLoading, setAssignLoading] = useState(false);
  const [newContractForm, setNewContractForm] = useState({
    name: '',
    service_category: '',
    contract_type: 'boh',
    start_date: new Date().toISOString().split('T')[0],
    end_date: '',
    total_hours: '',
    hourly_rate: '',
    monthly_rate: '',
    billing_day: 1,
    total_amount: '',
    milestones: [],
    billing_period: 'biweekly',
    invoice_terms: 'net_30',
    notes: '',
    resource_types: [],
    resources: [],
    _accountSearch: '',
    _showAccountDropdown: false,
  });

  // Commission Modal
  const [commissionDeal, setCommissionDeal] = useState(null);
  const [commissionTemplates, setCommissionTemplates] = useState([]);
  const [commissionForm, setCommissionForm] = useState({
    deal_id: '',
    template_id: '',
    revenue: '',
    cost_type: 'percentage',
    cost_value: 50,
    tiers: [
      { tier_number: 1, tier_name: 'Top Line', percentage: 10, users: [] },
      { tier_number: 2, tier_name: 'Override', percentage: 5, users: [] },
      { tier_number: 3, tier_name: 'Sales Rep', percentage: 15, users: [] },
    ],
  });
  const [commissionLoading, setCommissionLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [dataFilter, setDataFilter] = useState('real'); // 'test' | 'real' | 'all'

  const canEdit = isAdmin() || hasAnyPermission(['deals.edit.all', 'deals.edit.own']);
  const canSync = isAdmin() || hasPermission('deals.sync');
  const canDelete = isAdmin() || hasPermission('deals.delete');

  // Modal behavior: Escape to close + body scroll lock when any modal is open
  const anyModalOpen = Boolean(assignContractDeal || commissionDeal);
  useEffect(() => {
    if (!anyModalOpen) return;
    const onKeyDown = (e) => {
      if (e.key !== 'Escape') return;
      if (assignContractDeal) setAssignContractDeal(null);
      else if (commissionDeal) setCommissionDeal(null);
    };
    window.addEventListener('keydown', onKeyDown);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = prev;
    };
  }, [anyModalOpen, assignContractDeal, commissionDeal]);

  const loadDeals = useCallback(async () => {
    try {
      let qs = '';
      if (dataFilter === 'test') qs = '?test_only=true';
      else if (dataFilter === 'real') qs = '?test_only=false';
      // if dataFilter === 'all', no query string

      const res = await api.get(`${API}/api/contracts/deal-pipeline${qs}`);
      if (res.ok) {
        const data = await res.json();
        // Combine all deals
        const allDeals = [...(data.unassigned?.items || []), ...(data.assigned?.items || [])];
        setDeals(allDeals);
      }
    } catch (err) {
      toast.error('Failed to load deals');
    }
  }, [dataFilter]);

  const loadAccounts = useCallback(async () => {
    try {
      const res = await api.get(`${API}/api/accounts?limit=1000`);
      if (res.ok) {
        const data = await res.json();
        setAccounts(data.items || []);
      }
    } catch (err) {
      console.error('Failed to load accounts');
    }
  }, []);

  const loadUsers = useCallback(async () => {
    try {
      const res = await api.get(`${API}/api/admin/users`);
      if (res.ok) {
        const data = await res.json();
        setUsers(data || []);
      }
    } catch (err) {
      console.error('Failed to load users');
    }
  }, []);

  const loadContracts = useCallback(async () => {
    try {
      const res = await api.get(`${API}/api/contracts`);
      if (res.ok) {
        const data = await res.json();
        setContracts(data.items || []);
      }
    } catch (err) {
      console.error('Failed to load contracts');
    }
  }, []);

  const loadResourceTypes = useCallback(async () => {
    try {
      const res = await api.get(`${API}/api/admin/default-resource-types`);
      if (res.ok) {
        const data = await res.json();
        const types = (data.default_resource_types || []).filter((t) => t.is_active);
        // Add "Custom" option at the end
        setDefaultResourceTypes([
          ...types,
          { name: 'Custom', sell_rate: 0, description: 'Define your own' },
        ]);
      }
    } catch (err) {
      console.error('Failed to load resource types', err);
    }
  }, []);

  const loadCommissionTemplates = useCallback(async () => {
    try {
      const res = await api.get(`${API}/api/commission-management/templates`);
      if (res.ok) {
        const data = await res.json();
        setCommissionTemplates(data.items || []); // Backend returns "items" not "templates"
      }
    } catch (err) {
      console.error('Failed to load commission templates:', err);
    }
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      await Promise.all([
        loadDeals(),
        loadAccounts(),
        loadUsers(),
        loadContracts(),
        loadResourceTypes(),
      ]);
    } finally {
      setLoading(false);
    }
  }, [loadDeals, loadAccounts, loadUsers, loadContracts, loadResourceTypes]);

  useEffect(() => {
    loadData();
    loadCommissionTemplates();
  }, [loadData, loadCommissionTemplates]);

  // Pre-populate newContractForm when a deal is selected for contract assignment
  useEffect(() => {
    if (assignContractDeal) {
      setNewContractForm((prev) => ({
        ...prev,
        name: assignContractDeal.name || assignContractDeal.deal_name || '',
        account_id: assignContractDeal.account_id || '',
        account_name: assignContractDeal.account_name || '',
        notes: `Created from deal: ${assignContractDeal.name || assignContractDeal.deal_name || ''}`,
      }));
    }
  }, [assignContractDeal]);

  const handleSyncDeals = async () => {
    setSyncing(true);
    try {
      const res = await api.post(`${API}/api/integrations/sync/zoho/deals`);
      if (res.ok) {
        const data = await res.json();
        toast.success(data.message || 'Deals synced successfully');
        // Reload deals to show updated data
        await loadDeals();
      } else {
        const err = await res.json();
        toast.error(err.detail || 'Failed to sync deals');
      }
    } catch (err) {
      toast.error('Sync failed: ' + (err.message || 'Unknown error'));
    } finally {
      setSyncing(false);
    }
  };

  const openCommissionModal = (deal) => {
    setCommissionDeal(deal);
    setCommissionForm({
      deal_id: deal.deal_id || deal.zoho_deal_id,
      template_id: '',
      revenue: deal.amount || '',
      cost_type: 'percentage',
      cost_value: 50,
      tiers: [
        { tier_number: 1, tier_name: 'Top Line', percentage: 10, users: [] },
        { tier_number: 2, tier_name: 'Override', percentage: 5, users: [] },
        { tier_number: 3, tier_name: 'Sales Rep', percentage: 15, users: [] },
      ],
    });
    // Templates are already loaded on mount
  };

  const saveCommissionPlan = async () => {
    if (!commissionForm.revenue || parseFloat(commissionForm.revenue) <= 0) {
      toast.error('Please enter deal revenue');
      return;
    }

    const hasUsers = commissionForm.tiers.some(
      (t) => t.users && t.users.length > 0 && t.users.some((u) => u.user_id),
    );
    if (!hasUsers) {
      toast.error('Please add at least one user to receive commission');
      return;
    }

    setCommissionLoading(true);
    try {
      const payload = {
        deal_id: commissionForm.deal_id,
        template_id: commissionForm.template_id || null,
        revenue: parseFloat(commissionForm.revenue),
        cost_type: commissionForm.cost_type,
        cost_value: parseFloat(commissionForm.cost_value) || 0,
        tiers: commissionForm.tiers.map((tier) => ({
          tier_number: tier.tier_number,
          tier_name: tier.tier_name,
          percentage: tier.percentage,
          users: tier.users
            .filter((u) => u.user_id)
            .map((u) => ({
              user_id: u.user_id,
              user_name: u.user_name,
              split_percentage: parseFloat(u.split_percentage) || 0,
              commission_amount: 0,
            })),
        })),
      };

      const res = await api.post(`${API}/api/commission-management/plans`, payload);
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to save commission plan');
      }

      toast.success('Commission plan committed!');
      setCommissionDeal(null);
      loadDeals(); // Refresh deals
    } catch (err) {
      toast.error(err.message);
    } finally {
      setCommissionLoading(false);
    }
  };

  const handleAssignContract = async () => {
    if (!assignContractDeal) return;

    setAssignLoading(true);
    try {
      if (assignMode === 'existing') {
        if (!assignForm.contract_id) {
          toast.error('Please select a contract');
          setAssignLoading(false);
          return;
        }

        // Attach to existing contract
        const res = await api.post(`${API}/api/contracts/import-deal`, {
          deal_id: assignContractDeal.deal_id || assignContractDeal.zoho_deal_id,
          contract_id: assignForm.contract_id,
        });

        if (res.ok) {
          toast.success('Deal attached to contract');
          setAssignContractDeal(null);
          setAssignForm({ contract_id: '' });
          loadDeals();
        } else {
          const data = await res.json();
          toast.error(data.detail || 'Failed to attach deal');
        }
      } else {
        // Create new contract
        if (!newContractForm.name || !newContractForm.service_category) {
          toast.error('Contract name and service category are required');
          setAssignLoading(false);
          return;
        }

        // Build contract payload
        const contractPayload = {
          name: newContractForm.name,
          account_id: assignContractDeal.account_id,
          service_category: newContractForm.service_category,
          contract_type: newContractForm.contract_type,
          start_date: newContractForm.start_date,
          end_date: newContractForm.end_date || null,
          deal_ids: [assignContractDeal.deal_id || assignContractDeal.zoho_deal_id],
          status: 'active',
          notes: newContractForm.notes || '',
        };

        // Add type-specific fields
        if (newContractForm.contract_type === 'boh') {
          contractPayload.total_hours = parseFloat(newContractForm.total_hours) || 0;
          contractPayload.hourly_rate = parseFloat(newContractForm.hourly_rate) || 0;
        } else if (newContractForm.contract_type === 'boa') {
          contractPayload.billing_period = newContractForm.billing_period;
          contractPayload.invoice_terms = newContractForm.invoice_terms;
        } else if (newContractForm.contract_type === 'monthly') {
          contractPayload.monthly_rate = parseFloat(newContractForm.monthly_rate) || 0;
          contractPayload.billing_day = parseInt(newContractForm.billing_day) || 1;
        } else if (newContractForm.contract_type === 'fixed_fee') {
          contractPayload.total_amount = parseFloat(newContractForm.total_amount) || 0;
          contractPayload.milestones = [];
        }

        const res = await api.post(`${API}/api/contracts`, contractPayload);

        if (res.ok) {
          toast.success('Contract created and deal attached!');
          setAssignContractDeal(null);
          setAssignForm({ contract_id: '' });
          setNewContractForm({
            name: '',
            contract_type: 'boh',
            start_date: new Date().toISOString().split('T')[0],
            total_hours: '',
            hourly_rate: '',
            monthly_rate: '',
            total_amount: '',
            notes: '',
          });
          loadDeals();
        } else {
          const data = await res.json();
          toast.error(data.detail || 'Failed to create contract');
        }
      }
    } catch (err) {
      toast.error(err.message || 'Failed to process request');
    } finally {
      setAssignLoading(false);
    }
  };

  // Apply advanced filters (search, account, owner, etc.)
  const baseFilteredDeals = deals.filter((deal) => {
    // Search
    if (filters.search) {
      const search = filters.search.toLowerCase();
      const matchName = (deal.name || deal.deal_name || '').toLowerCase().includes(search);
      const matchAccount = (deal.account_name || '').toLowerCase().includes(search);
      if (!matchName && !matchAccount) return false;
    }

    // Account filter
    if (filters.account_id && deal.account_id !== filters.account_id) return false;

    // Deal owner filter
    if (filters.deal_owner_id && deal.deal_owner_id !== filters.deal_owner_id) return false;

    // Agent of record filter
    if (filters.agent_of_record_id && deal.assigned_agent_id !== filters.agent_of_record_id)
      return false;

    // Contract status
    if (
      filters.contract_status === 'with_contract' &&
      (!deal.contracts || deal.contracts.length === 0)
    )
      return false;
    if (filters.contract_status === 'no_contract' && deal.contracts && deal.contracts.length > 0)
      return false;

    // Commission status
    if (
      filters.commission_status === 'with_commission' &&
      !deal.commission_id &&
      !deal.commission_plan_id
    )
      return false;
    if (
      filters.commission_status === 'no_commission' &&
      (deal.commission_id || deal.commission_plan_id)
    )
      return false;

    return true;
  });

  // Statistics — always computed from base filter (unaffected by quick filter)
  const stats = {
    total: baseFilteredDeals.length,
    needsContract: baseFilteredDeals.filter((d) => !d.contracts || d.contracts.length === 0).length,
    needsCommission: baseFilteredDeals.filter((d) => !d.commission_id && !d.commission_plan_id)
      .length,
    needsBoth: baseFilteredDeals.filter(
      (d) =>
        (!d.contracts || d.contracts.length === 0) && !d.commission_id && !d.commission_plan_id,
    ).length,
    complete: baseFilteredDeals.filter(
      (d) => d.contracts && d.contracts.length > 0 && (d.commission_id || d.commission_plan_id),
    ).length,
  };

  // Apply quick filter on top of base
  const filteredDeals = baseFilteredDeals.filter((deal) => {
    const hasContract = deal.contracts && deal.contracts.length > 0;
    const hasCommission = deal.commission_id || deal.commission_plan_id;

    switch (quickFilter) {
      case 'needs_both':
        return !hasContract && !hasCommission;
      case 'needs_contract':
        return !hasContract;
      case 'needs_commission':
        return !hasCommission;
      case 'complete':
        return hasContract && hasCommission;
      default: // 'all' - show ALL deals regardless of status
        return true;
    }
  });

  const getDealStatus = (deal) => {
    const hasContract = deal.contracts && deal.contracts.length > 0;
    const hasCommission = deal.commission_id || deal.commission_plan_id;

    if (hasContract && hasCommission)
      return {
        label: 'Complete',
        color: 'text-emerald-600 dark:text-emerald-400',
        icon: CheckCircle,
      };
    if (!hasContract && !hasCommission)
      return { label: 'Needs Both', color: 'text-red-600 dark:text-red-400', icon: AlertCircle };
    if (!hasContract)
      return {
        label: 'Needs Contract',
        color: 'text-amber-600 dark:text-amber-400',
        icon: FileText,
      };
    if (!hasCommission)
      return {
        label: 'Needs Commission',
        color: 'text-violet-600 dark:text-violet-400',
        icon: DollarSign,
      };
  };

  return (
    <div className="p-4 sm:p-6 space-y-4">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-lg sm:text-xl font-bold text-aegisaxiom-text flex items-center gap-2">
            <Target size={20} className="text-emerald-600 dark:text-emerald-400 shrink-0" />
            Deal Management
          </h1>
          <p className="text-xs sm:text-sm text-aegisaxiom-text-secondary mt-1">
            Closed Won deals requiring contract and commission assignment
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {isAdmin() && (
            <div className="flex items-center gap-2 text-xs">
              <Filter size={14} className="text-aegisaxiom-text-muted shrink-0" />
              <select
                value={dataFilter}
                onChange={(e) => setDataFilter(e.target.value)}
                className="px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text-secondary font-medium focus:border-aegisaxiom-border-hover focus:outline-none cursor-pointer hover:border-aegisaxiom-border-hover transition"
              >
                <option value="all">All Data</option>
                <option value="test">Test Data</option>
                <option value="real">Real Data</option>
              </select>
            </div>
          )}
          {canSync && (
            <button
              onClick={handleSyncDeals}
              disabled={syncing}
              className={`flex items-center gap-2 text-sm px-3 sm:px-4 py-2 rounded-lg transition border ${
                syncing
                  ? 'bg-emerald-50 dark:bg-emerald-900/30 border-emerald-200 dark:border-emerald-700/30 text-emerald-600 dark:text-emerald-400'
                  : 'bg-emerald-50 dark:bg-emerald-900/20 hover:bg-emerald-100 dark:hover:bg-emerald-900/40 border-emerald-200 dark:border-emerald-700/40 text-emerald-600 dark:text-emerald-400 hover:text-emerald-700 dark:hover:text-emerald-300'
              }`}
            >
              <RefreshCw size={16} className={syncing ? 'animate-spin' : ''} />
              <span className="hidden sm:inline">
                {syncing ? 'Syncing Deals...' : 'Sync Deals'}
              </span>
              <span className="sm:hidden">{syncing ? 'Syncing...' : 'Sync'}</span>
            </button>
          )}
          <button
            onClick={() => setShowFilters(!showFilters)}
            aria-expanded={showFilters}
            className="flex items-center gap-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text text-sm px-3 sm:px-4 py-2 rounded-lg transition border border-aegisaxiom-border"
          >
            <Filter size={16} />
            Filters {showFilters && <X size={14} />}
          </button>
        </div>
      </div>

      {/* Statistics — Clickable Quick Filters */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2 sm:gap-3">
        <button
          onClick={() => setQuickFilter('all')}
          aria-pressed={quickFilter === 'all'}
          className={`text-left rounded-lg p-2.5 sm:p-3 transition border ${
            quickFilter === 'all'
              ? 'bg-aegisaxiom-surface border-violet-300 dark:border-white/30 ring-1 ring-violet-200 dark:ring-white/20 shadow-sm'
              : 'bg-aegisaxiom-surface border-aegisaxiom-border hover:border-aegisaxiom-border-hover shadow-sm'
          }`}
        >
          <div className="text-xl sm:text-2xl font-bold text-aegisaxiom-text">{stats.total}</div>
          <div className="text-[11px] sm:text-xs text-aegisaxiom-text-secondary mt-1">Total Deals</div>
        </button>
        <button
          onClick={() => setQuickFilter(quickFilter === 'needs_both' ? 'all' : 'needs_both')}
          aria-pressed={quickFilter === 'needs_both'}
          className={`text-left rounded-lg p-2.5 sm:p-3 transition border ${
            quickFilter === 'needs_both'
              ? 'bg-red-100 dark:bg-red-900/40 border-red-500/50 ring-1 ring-red-500/30'
              : 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800/30 hover:border-red-600/50'
          }`}
        >
          <div className="text-xl sm:text-2xl font-bold text-red-600 dark:text-red-400">
            {stats.needsBoth}
          </div>
          <div className="text-[11px] sm:text-xs text-red-700 dark:text-red-300 mt-1">
            Needs Both
          </div>
        </button>
        <button
          onClick={() =>
            setQuickFilter(quickFilter === 'needs_contract' ? 'all' : 'needs_contract')
          }
          aria-pressed={quickFilter === 'needs_contract'}
          className={`text-left rounded-lg p-2.5 sm:p-3 transition border ${
            quickFilter === 'needs_contract'
              ? 'bg-amber-100 dark:bg-amber-900/40 border-amber-500/50 ring-1 ring-amber-500/30'
              : 'bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800/30 hover:border-amber-600/50'
          }`}
        >
          <div className="text-xl sm:text-2xl font-bold text-amber-600 dark:text-amber-400">
            {stats.needsContract}
          </div>
          <div className="text-[11px] sm:text-xs text-amber-700 dark:text-amber-300 mt-1">
            Needs Contract
          </div>
        </button>
        <button
          onClick={() =>
            setQuickFilter(quickFilter === 'needs_commission' ? 'all' : 'needs_commission')
          }
          aria-pressed={quickFilter === 'needs_commission'}
          className={`text-left rounded-lg p-2.5 sm:p-3 transition border ${
            quickFilter === 'needs_commission'
              ? 'bg-violet-100 dark:bg-violet-900/40 border-violet-500/50 ring-1 ring-violet-500/30'
              : 'bg-violet-50 dark:bg-violet-900/20 border-violet-200 dark:border-violet-800/30 hover:border-violet-600/50'
          }`}
        >
          <div className="text-xl sm:text-2xl font-bold text-violet-600 dark:text-violet-400">
            {stats.needsCommission}
          </div>
          <div className="text-[11px] sm:text-xs text-violet-700 dark:text-violet-300 mt-1">
            Needs Commission
          </div>
        </button>
        <button
          onClick={() => setQuickFilter(quickFilter === 'complete' ? 'all' : 'complete')}
          aria-pressed={quickFilter === 'complete'}
          className={`col-span-2 sm:col-span-1 text-left rounded-lg p-2.5 sm:p-3 transition border ${
            quickFilter === 'complete'
              ? 'bg-emerald-100 dark:bg-emerald-900/40 border-emerald-500/50 ring-1 ring-emerald-500/30'
              : 'bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800/30 hover:border-emerald-600/50'
          }`}
        >
          <div className="text-xl sm:text-2xl font-bold text-emerald-600 dark:text-emerald-400">
            {stats.complete}
          </div>
          <div className="text-[11px] sm:text-xs text-emerald-700 dark:text-emerald-300 mt-1">
            Complete
          </div>
        </button>
      </div>

      {/* Filters Panel */}
      {showFilters && (
        <div className="mt-4 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-3 sm:p-4 space-y-3 shadow-sm">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {/* Search */}
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Search</label>
              <div className="relative">
                <Search
                  size={14}
                  className="absolute left-3 top-2.5 text-aegisaxiom-text-muted pointer-events-none"
                />
                <input
                  type="text"
                  value={filters.search}
                  onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
                  placeholder="Deal or account name..."
                  className="w-full pl-9 pr-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                />
              </div>
            </div>

            {/* Account */}
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Account</label>
              <select
                value={filters.account_id}
                onChange={(e) => setFilters((f) => ({ ...f, account_id: e.target.value }))}
                className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
              >
                <option value="">All Accounts</option>
                {accounts.map((a) => (
                  <option key={a.account_id} value={a.account_id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Deal Owner */}
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Deal Owner</label>
              <select
                value={filters.deal_owner_id}
                onChange={(e) => setFilters((f) => ({ ...f, deal_owner_id: e.target.value }))}
                className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
              >
                <option value="">All Owners</option>
                {users
                  .filter((u) => u.role === 'sales' || u.role === 'admin')
                  .map((u) => (
                    <option key={u.user_id} value={u.user_id}>
                      {u.name}
                    </option>
                  ))}
              </select>
            </div>

            {/* Agent of Record */}
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Agent of Record</label>
              <select
                value={filters.agent_of_record_id}
                onChange={(e) => setFilters((f) => ({ ...f, agent_of_record_id: e.target.value }))}
                className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
              >
                <option value="">All Agents</option>
                {users
                  .filter((u) => u.role === 'agent' || u.role === 'admin')
                  .map((u) => (
                    <option key={u.user_id} value={u.user_id}>
                      {u.name}
                    </option>
                  ))}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {/* Contract Status */}
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Contract Status</label>
              <select
                value={filters.contract_status}
                onChange={(e) => setFilters((f) => ({ ...f, contract_status: e.target.value }))}
                className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
              >
                <option value="all">All</option>
                <option value="no_contract">No Contract</option>
                <option value="with_contract">With Contract</option>
              </select>
            </div>

            {/* Commission Status */}
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                Commission Status
              </label>
              <select
                value={filters.commission_status}
                onChange={(e) => setFilters((f) => ({ ...f, commission_status: e.target.value }))}
                className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
              >
                <option value="all">All</option>
                <option value="no_commission">No Commission</option>
                <option value="with_commission">With Commission</option>
              </select>
            </div>
          </div>

          {/* Clear Filters */}
          <button
            onClick={() => {
              setFilters({
                search: '',
                account_id: '',
                deal_owner_id: '',
                agent_of_record_id: '',
                contract_status: 'all',
                commission_status: 'all',
              });
              setQuickFilter('all');
            }}
            className="text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:hover:text-violet-300 py-1"
          >
            Clear all filters
          </button>
        </div>
      )}

      {/* Active Quick Filter Indicator + Deal Count */}
      {quickFilter !== 'all' && (
        <div className="flex items-center gap-2 text-xs">
          <span className="text-aegisaxiom-text-secondary">Showing:</span>
          <span
            className={`px-2 py-0.5 rounded-full font-medium ${
              quickFilter === 'needs_both'
                ? 'bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800/30'
                : quickFilter === 'needs_contract'
                  ? 'bg-amber-50 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400 border border-amber-200 dark:border-amber-800/30'
                  : quickFilter === 'needs_commission'
                    ? 'bg-violet-50 dark:bg-violet-900/30 text-violet-600 dark:text-violet-400 border border-violet-200 dark:border-violet-800/30'
                    : 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-800/30'
            }`}
          >
            {quickFilter === 'needs_both'
              ? 'Needs Both'
              : quickFilter === 'needs_contract'
                ? 'Needs Contract'
                : quickFilter === 'needs_commission'
                  ? 'Needs Commission'
                  : 'Complete'}{' '}
            ({filteredDeals.length})
          </span>
          <button
            onClick={() => setQuickFilter('all')}
            aria-label="Clear filter"
            className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition p-1"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* Deals List */}
      <div className="space-y-2">
        {loading && (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <div
                key={i}
                className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4 animate-pulse"
              >
                <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                  <div className="flex-1 space-y-3">
                    <div className="flex items-center gap-2">
                      <div className="h-4 w-40 bg-aegisaxiom-elevated rounded" />
                      <div className="h-3 w-16 bg-aegisaxiom-elevated rounded" />
                    </div>
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                      {[0, 1, 2, 3].map((j) => (
                        <div key={j} className="space-y-1.5">
                          <div className="h-3 w-16 bg-aegisaxiom-elevated rounded" />
                          <div className="h-3 w-24 bg-aegisaxiom-elevated rounded" />
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="flex gap-2 sm:ml-4">
                    <div className="h-7 w-28 bg-aegisaxiom-elevated rounded" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {!loading && filteredDeals.length === 0 && deals.length === 0 && (
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-6 sm:p-8 text-center">
            <Target size={48} className="mx-auto text-aegisaxiom-text-muted/60 mb-3" />
            <p className="text-aegisaxiom-text font-medium mb-2">No Closed Won Deals Found</p>
            <p className="text-aegisaxiom-text-secondary text-sm mb-4">
              Get started by syncing deals/migrate CRM or checking your deal stage filters.
            </p>
            <div className="flex flex-col sm:flex-row items-stretch sm:items-center sm:justify-center gap-2 sm:gap-3">
              <button
                onClick={() => (window.location.href = '/admin/integrations')}
                className="bg-blue-600 hover:bg-blue-500 text-white text-sm px-4 py-2 rounded-lg transition"
              >
                Configure Zoho Sync
              </button>
              <button
                onClick={() => loadDeals()}
                className="bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text text-sm px-4 py-2 rounded-lg transition"
              >
                Refresh Data
              </button>
            </div>
          </div>
        )}

        {!loading && filteredDeals.length === 0 && deals.length > 0 && (
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-6 sm:p-8 text-center">
            <Filter size={48} className="mx-auto text-aegisaxiom-text-muted/60 mb-3" />
            <p className="text-aegisaxiom-text font-medium mb-2">No deals match your filters</p>
            <p className="text-aegisaxiom-text-secondary text-sm mb-4">
              Try adjusting your filters or clearing them to see all deals.
            </p>
            <button
              onClick={() => {
                setFilters({
                  search: '',
                  account_id: '',
                  deal_owner_id: '',
                  agent_of_record_id: '',
                  contract_status: 'all',
                  commission_status: 'all',
                });
                setQuickFilter('all');
              }}
              className="bg-violet-600 hover:bg-violet-500 text-white text-sm px-4 py-2 rounded-lg transition"
            >
              Clear All Filters
            </button>
          </div>
        )}

        {!loading &&
          filteredDeals.map((deal) => {
            const status = getDealStatus(deal);
            const StatusIcon = status.icon;
            const hasContract = deal.contracts && deal.contracts.length > 0;
            const hasCommission = deal.commission_id || deal.commission_plan_id;
            const isExpanded = selectedDeal?.deal_id === deal.deal_id;
            const notes = deal.zoho_notes || [];
            const attachments = deal.zoho_attachments || [];
            const pandaDocs = deal.pandadoc_documents || [];

            return (
              <div
                key={deal.deal_id || deal.zoho_deal_id}
                className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg hover:border-aegisaxiom-border-hover transition"
              >
                {/* Clickable Header Row */}
                <div
                  className="p-3 sm:p-4 cursor-pointer"
                  onClick={() => setSelectedDeal(isExpanded ? null : deal)}
                >
                  <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                    {/* Deal Info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-2 flex-wrap">
                        <h3 className="text-sm sm:text-base font-semibold text-aegisaxiom-text">
                          {deal.name || deal.deal_name}
                        </h3>
                        <span className={`flex items-center gap-1 text-xs ${status.color}`}>
                          <StatusIcon size={12} />
                          {status.label}
                        </span>
                        {/* Enrichment badges */}
                        {notes.length > 0 && (
                          <span className="flex items-center gap-0.5 text-[10px] bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 border border-blue-200 dark:border-blue-800/30 px-1.5 py-0.5 rounded-full">
                            <MessageSquare size={10} /> {notes.length}
                          </span>
                        )}
                        {attachments.length > 0 && (
                          <span className="flex items-center gap-0.5 text-[10px] bg-amber-50 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400 border border-amber-200 dark:border-amber-800/30 px-1.5 py-0.5 rounded-full">
                            <Paperclip size={10} /> {attachments.length}
                          </span>
                        )}
                        {pandaDocs.length > 0 && (
                          <span className="flex items-center gap-0.5 text-[10px] bg-green-50 dark:bg-green-900/30 text-green-600 dark:text-green-400 border border-green-200 dark:border-green-800/30 px-1.5 py-0.5 rounded-full">
                            <ExternalLink size={10} /> PandaDoc
                          </span>
                        )}
                      </div>

                      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4 text-xs">
                        {/* Column 1: Basic Info */}
                        <div className="min-w-0">
                          <div className="flex items-center gap-1 text-aegisaxiom-text-muted mb-1">
                            <Building2 size={12} className="shrink-0" />
                            <span>Account</span>
                          </div>
                          <div
                            className="text-aegisaxiom-text font-medium cursor-pointer hover:text-amber-600 dark:hover:text-amber-400 transition truncate"
                            onClick={(e) => {
                              e.stopPropagation();
                              if (deal.account_id) {
                                navigate(`/accounts?id=${deal.account_id}`);
                              }
                            }}
                          >
                            {deal.account_name}
                          </div>
                          <div className="text-aegisaxiom-text-secondary mt-1">
                            {fmt(deal.amount || 0)}
                          </div>
                        </div>

                        {/* Column 2: People */}
                        <div className="min-w-0">
                          <div className="flex items-center gap-1 text-aegisaxiom-text-muted mb-1">
                            <Users size={12} className="shrink-0" />
                            <span>Team</span>
                          </div>
                          <div className="text-aegisaxiom-text truncate">
                            {deal.deal_owner_name || 'No owner'}
                          </div>
                          {deal.agent_of_record_name && (
                            <div className="text-aegisaxiom-text-secondary text-[10px] mt-0.5 truncate">
                              AoR: {deal.agent_of_record_name}
                            </div>
                          )}
                        </div>

                        {/* Column 3: Contract */}
                        <div className="min-w-0">
                          <div className="flex items-center gap-1 text-aegisaxiom-text-muted mb-1">
                            <FileText size={12} className="shrink-0" />
                            <span>Contract</span>
                          </div>
                          {hasContract ? (
                            <div className="space-y-0.5">
                              {deal.contracts.slice(0, 2).map((c, i) => (
                                <div
                                  key={i}
                                  className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400 font-medium text-[11px] cursor-pointer hover:text-emerald-700 dark:hover:text-emerald-300 transition truncate"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    if (c.contract_id) {
                                      navigate(`/contracts?id=${c.contract_id}`);
                                    }
                                  }}
                                >
                                  <CheckCircle size={11} className="shrink-0" />
                                  <span className="truncate">
                                    {c.name} • {c.contract_type.toUpperCase()}
                                  </span>
                                </div>
                              ))}
                              {deal.contracts.length > 2 && (
                                <div className="text-aegisaxiom-text-muted text-[10px]">
                                  +{deal.contracts.length - 2} more
                                </div>
                              )}
                            </div>
                          ) : deal.deal_types?.includes('referral_mrr') ? (
                            <div className="text-aegisaxiom-text-muted italic text-xs">
                              N/A (Referral)
                            </div>
                          ) : (
                            <div
                              className="flex items-center gap-1 text-amber-600 dark:text-amber-400 cursor-pointer hover:text-amber-700 dark:hover:text-amber-300 transition"
                              onClick={(e) => {
                                e.stopPropagation();
                                if (canEdit) {
                                  setAssignContractDeal(deal);
                                  setAssignMode('new');
                                  setAssignForm({ contract_id: '' });
                                }
                              }}
                            >
                              <AlertCircle size={11} className="shrink-0" />
                              Not assigned
                            </div>
                          )}
                        </div>

                        {/* Column 4: Commission */}
                        <div className="min-w-0">
                          <div className="flex items-center gap-1 text-aegisaxiom-text-muted mb-1">
                            <DollarSign size={12} className="shrink-0" />
                            <span>Commission</span>
                          </div>
                          {hasCommission ? (
                            <div className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400 font-medium">
                              <CheckCircle size={11} className="shrink-0" />
                              Plan committed
                            </div>
                          ) : (
                            <div
                              className="flex items-center gap-1 text-violet-600 dark:text-violet-400 cursor-pointer hover:text-violet-700 dark:hover:text-violet-300 transition"
                              onClick={(e) => {
                                e.stopPropagation();
                                if (canEdit) {
                                  openCommissionModal(deal);
                                }
                              }}
                            >
                              <AlertCircle size={11} className="shrink-0" />
                              Not committed
                            </div>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Expand/Actions Column */}
                    <div
                      className="flex flex-wrap items-center gap-2 sm:ml-4 sm:shrink-0"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {canEdit && !hasContract && !deal.deal_types?.includes('referral_mrr') && (
                        <button
                          onClick={() => {
                            setAssignContractDeal(deal);
                            setAssignMode('new');
                            setAssignForm({ contract_id: '' });
                          }}
                          className="flex items-center gap-1.5 bg-amber-600 hover:bg-amber-500 text-white text-xs px-3 py-1.5 rounded-md transition whitespace-nowrap"
                        >
                          <FileText size={14} /> Assign Contract
                        </button>
                      )}
                      {canEdit && !hasCommission && (
                        <button
                          onClick={() => openCommissionModal(deal)}
                          className="flex items-center gap-1.5 bg-violet-600 hover:bg-violet-500 text-white text-xs px-3 py-1.5 rounded-md transition whitespace-nowrap"
                        >
                          <DollarSign size={14} /> Commit Commission
                        </button>
                      )}
                      <button
                        type="button"
                        aria-label={isExpanded ? 'Collapse deal' : 'Expand deal'}
                        onClick={() => setSelectedDeal(isExpanded ? null : deal)}
                        className="flex items-center justify-center ml-auto sm:ml-0 w-8 h-8 rounded-md text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition"
                      >
                        <ChevronDown
                          size={18}
                          className={`transition-transform duration-200 ${isExpanded ? 'rotate-180' : ''}`}
                        />
                      </button>
                    </div>
                  </div>
                </div>

                {/* ========= EXPANDED DETAIL PANEL ========= */}
                {isExpanded && (
                  <div className="border-t border-aegisaxiom-border p-3 sm:p-4 space-y-4 bg-aegisaxiom-base/50">
                    {/* Row 1: Deal Details + Account Info */}
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 sm:gap-4">
                      {/* Deal Details */}
                      <div className="bg-aegisaxiom-accent-muted rounded-lg p-3">
                        <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-2 flex items-center gap-1.5">
                          <Target size={14} className="text-emerald-600 dark:text-emerald-400" />{' '}
                          Deal Details
                        </h4>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
                          <div>
                            <span className="text-aegisaxiom-text-muted">Deal ID:</span>
                            <span className="text-aegisaxiom-text-secondary ml-1 font-mono text-[10px]">
                              {deal.zoho_deal_id || deal.deal_id}
                            </span>
                          </div>
                          <div>
                            <span className="text-aegisaxiom-text-muted">Amount:</span>
                            <span className="text-aegisaxiom-text ml-1 font-semibold">
                              {fmt(deal.amount || 0)}
                            </span>
                          </div>
                          <div>
                            <span className="text-aegisaxiom-text-muted">Close Date:</span>
                            <span className="text-aegisaxiom-text-secondary ml-1">
                              {deal.close_date || '—'}
                            </span>
                          </div>
                          <div>
                            <span className="text-aegisaxiom-text-muted">Stage:</span>
                            <span className="text-emerald-600 dark:text-emerald-400 ml-1">
                              {deal.stage}
                            </span>
                          </div>
                          <div>
                            <span className="text-aegisaxiom-text-muted">Owner:</span>
                            <span className="text-aegisaxiom-text ml-1">
                              {deal.deal_owner_name || '—'}
                            </span>
                            {deal.deal_owner_email && (
                              <span className="text-aegisaxiom-text-muted ml-1 text-[10px]">
                                ({deal.deal_owner_email})
                              </span>
                            )}
                          </div>
                          <div>
                            <span className="text-aegisaxiom-text-muted">Agent of Record:</span>
                            <span className="text-aegisaxiom-text ml-1">
                              {deal.agent_of_record_name || '—'}
                            </span>
                          </div>
                          {deal.deal_types && deal.deal_types.length > 0 && (
                            <div className="col-span-2">
                              <span className="text-aegisaxiom-text-muted">Line of Business:</span>
                              <div className="flex flex-wrap gap-1 mt-1">
                                {deal.deal_types.map((type, idx) => {
                                  const typeLabels = {
                                    field_services: {
                                      label: 'Field Services',
                                      color:
                                        'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300 border-blue-500/30',
                                    },
                                    professional_services: {
                                      label: 'Professional Services',
                                      color:
                                        'bg-purple-600/20 text-purple-300 border-purple-500/30',
                                    },
                                    referral_mrr: {
                                      label: 'Referral MRR',
                                      color:
                                        'bg-pink-50 dark:bg-pink-600/20 text-pink-700 dark:text-pink-300 border-pink-500/30',
                                    },
                                    wholesale_mrr: {
                                      label: 'Wholesale MRR',
                                      color:
                                        'bg-cyan-600/20 text-cyan-700 dark:text-cyan-300 border-cyan-500/30',
                                    },
                                  };
                                  const config = typeLabels[type] || {
                                    label: type,
                                    color:
                                      'bg-aegisaxiom-elevated/20 text-aegisaxiom-text-secondary border-aegisaxiom-border-hover/30',
                                  };
                                  return (
                                    <span
                                      key={idx}
                                      className={`px-2 py-0.5 rounded text-[10px] font-medium border ${config.color}`}
                                    >
                                      {config.label}
                                    </span>
                                  );
                                })}
                              </div>
                            </div>
                          )}
                        </div>
                        {deal.description && (
                          <div className="mt-2 pt-2 border-t border-aegisaxiom-border">
                            <span className="text-aegisaxiom-text-muted text-[10px]">Description:</span>
                            <p className="text-aegisaxiom-text-secondary text-xs mt-0.5 leading-relaxed">
                              {deal.description}
                            </p>
                          </div>
                        )}
                      </div>

                      {/* Contract + Commission Details */}
                      <div className="space-y-3">
                        {/* Contract Details */}
                        <div className="bg-aegisaxiom-accent-muted rounded-lg p-3">
                          <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-2 flex items-center gap-1.5">
                            <FileText size={14} className="text-amber-600 dark:text-amber-400" />{' '}
                            Contracts
                          </h4>
                          {hasContract ? (
                            deal.contracts.map((contract, idx) => (
                              <div
                                key={idx}
                                className="bg-aegisaxiom-surface rounded p-2 mb-1.5 last:mb-0 cursor-pointer hover:bg-aegisaxiom-surface transition"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  if (contract.contract_id) {
                                    navigate(`/contracts?id=${contract.contract_id}`);
                                  }
                                }}
                              >
                                <div className="text-aegisaxiom-text font-medium text-xs hover:text-amber-600 dark:text-amber-400 transition">
                                  {contract.name}
                                </div>
                                <div className="text-aegisaxiom-text-secondary text-[10px] mt-0.5">
                                  Type: {contract.contract_type?.toUpperCase()}
                                  {contract.total_amount
                                    ? ` • Value: ${fmt(contract.total_amount)}`
                                    : ''}
                                  {contract.monthly_rate
                                    ? ` • ${fmt(contract.monthly_rate)}/month`
                                    : ''}
                                  {contract.status ? ` • Status: ${contract.status}` : ''}
                                </div>
                              </div>
                            ))
                          ) : (
                            <div
                              className="text-amber-600 dark:text-amber-400 text-xs italic cursor-pointer hover:text-amber-700 dark:text-amber-300 transition"
                              onClick={(e) => {
                                e.stopPropagation();
                                if (canEdit) {
                                  setAssignContractDeal(deal);
                                  setAssignMode('new');
                                  setAssignForm({ contract_id: '' });
                                }
                              }}
                            >
                              No contract assigned
                            </div>
                          )}
                        </div>
                        {/* Commission Details */}
                        <div className="bg-aegisaxiom-accent-muted rounded-lg p-3">
                          <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-2 flex items-center gap-1.5">
                            <DollarSign
                              size={14}
                              className="text-violet-600 dark:text-violet-400"
                            />{' '}
                            Commission
                          </h4>
                          {hasCommission ? (
                            <div className="text-emerald-600 dark:text-emerald-400 text-xs font-medium">
                              ✓ Plan Active — {deal.commission_id || deal.commission_plan_id}
                            </div>
                          ) : (
                            <div
                              className="text-violet-600 dark:text-violet-400 text-xs italic cursor-pointer hover:text-violet-700 dark:text-violet-300 transition"
                              onClick={(e) => {
                                e.stopPropagation();
                                if (canEdit) {
                                  openCommissionModal(deal);
                                }
                              }}
                            >
                              No commission plan committed
                            </div>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Row 2: Notes */}
                    {notes.length > 0 && (
                      <div className="bg-aegisaxiom-accent-muted rounded-lg p-3">
                        <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-2 flex items-center gap-1.5">
                          <MessageSquare size={14} className="text-blue-600 dark:text-blue-400" />{' '}
                          Notes ({notes.length})
                        </h4>
                        <div className="space-y-2 max-h-60 overflow-y-auto">
                          {notes.map((note, idx) => (
                            <div
                              key={idx}
                              className="bg-aegisaxiom-surface rounded p-2 border-l-2 border-blue-800/50"
                            >
                              {note.title && (
                                <div className="text-aegisaxiom-text font-medium text-xs mb-0.5">
                                  {note.title}
                                </div>
                              )}
                              <div
                                className="text-aegisaxiom-text-secondary text-xs leading-relaxed"
                                dangerouslySetInnerHTML={{
                                  __html: note.content
                                    ? DOMPurify.sanitize(note.content)
                                    : '<em class="text-aegisaxiom-text-muted">No content</em>',
                                }}
                              />
                              <div className="text-aegisaxiom-text-muted text-[10px] mt-1">
                                {note.created_by && `By ${note.created_by}`}
                                {note.created_time &&
                                  ` • ${new Date(note.created_time).toLocaleDateString()}`}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Row 3: Attachments + PandaDoc */}
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 sm:gap-4">
                      {/* Attachments */}
                      {attachments.length > 0 && (
                        <div className="bg-aegisaxiom-accent-muted rounded-lg p-3">
                          <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-2 flex items-center gap-1.5">
                            <Paperclip size={14} className="text-amber-600 dark:text-amber-400" />{' '}
                            Attachments ({attachments.length})
                          </h4>
                          <div className="space-y-1.5 max-h-40 overflow-y-auto">
                            {attachments.map((att, idx) => (
                              <div
                                key={idx}
                                className="flex items-center justify-between bg-aegisaxiom-surface rounded px-2 py-1.5"
                              >
                                <div className="flex items-center gap-2 min-w-0">
                                  <Paperclip
                                    size={12}
                                    className="text-aegisaxiom-text-muted flex-shrink-0"
                                  />
                                  <span className="text-aegisaxiom-text-secondary text-xs truncate">
                                    {att.file_name}
                                  </span>
                                </div>
                                <span className="text-aegisaxiom-text-muted text-[10px] flex-shrink-0 ml-2">
                                  {att.size ? `${(att.size / 1024).toFixed(0)} KB` : ''}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* PandaDoc Documents */}
                      {pandaDocs.length > 0 && (
                        <div className="bg-aegisaxiom-accent-muted rounded-lg p-3">
                          <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-2 flex items-center gap-1.5">
                            <ExternalLink
                              size={14}
                              className="text-green-600 dark:text-green-400"
                            />{' '}
                            PandaDoc Documents ({pandaDocs.length})
                          </h4>
                          <div className="space-y-1.5">
                            {pandaDocs.map((doc, idx) => (
                              <div
                                key={idx}
                                className="flex items-center justify-between bg-aegisaxiom-surface rounded px-2 py-1.5"
                              >
                                <span className="text-aegisaxiom-text-secondary text-xs truncate">
                                  {doc.name || 'Document'}
                                </span>
                                {doc.link && (
                                  <a
                                    href={doc.link}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="text-green-600 dark:text-green-400 hover:text-green-700 dark:text-green-300 text-[10px] flex items-center gap-0.5 flex-shrink-0 ml-2"
                                  >
                                    Open <ExternalLink size={10} />
                                  </a>
                                )}
                                {doc.status && (
                                  <span className="text-aegisaxiom-text-muted text-[10px] ml-1">
                                    {doc.status}
                                  </span>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Empty state for no enrichment */}
                      {attachments.length === 0 && pandaDocs.length === 0 && notes.length === 0 && (
                        <div className="col-span-2 text-center py-2 text-aegisaxiom-text-muted text-xs italic">
                          No enrichment data (notes, attachments, or documents) from Zoho for this
                          deal.
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
      </div>

      {/* ============ ASSIGN CONTRACT MODAL ============ */}
      {assignContractDeal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center sm:p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Assign Contract"
        >
          <div
            className="absolute inset-0 bg-black/60"
            onClick={() => setAssignContractDeal(null)}
          />
          <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border w-full h-full sm:rounded-xl sm:max-w-4xl sm:max-h-[90vh] sm:h-auto overflow-y-auto">
            <div className="sticky top-0 bg-aegisaxiom-surface backdrop-blur border-b border-aegisaxiom-border p-3 sm:p-4 z-10">
              <div className="flex justify-between items-start gap-3">
                <div className="min-w-0">
                  <h2 className="text-sm font-semibold text-aegisaxiom-text flex items-center gap-2">
                    <FileText size={16} className="text-amber-600 dark:text-amber-400 shrink-0" />
                    Assign Contract
                  </h2>
                  <p className="text-xs text-aegisaxiom-text-secondary mt-1">
                    <span className="text-aegisaxiom-text font-medium">
                      {assignContractDeal.name || assignContractDeal.deal_name}
                    </span>
                    {assignContractDeal.amount > 0 && (
                      <span className="ml-2">{fmt(assignContractDeal.amount)}</span>
                    )}
                    <span className="ml-2 text-aegisaxiom-text-muted">
                      {assignContractDeal.account_name}
                    </span>
                  </p>
                </div>
                <button
                  onClick={() => setAssignContractDeal(null)}
                  aria-label="Close"
                  className="w-9 h-9 rounded-full flex items-center justify-center text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition shrink-0"
                >
                  <X size={18} />
                </button>
              </div>
            </div>

            <div className="p-3 sm:p-4 space-y-4">
              {/* Mode Toggle */}
              <div className="flex gap-2">
                <button
                  onClick={() => setAssignMode('existing')}
                  className={`flex-1 px-3 py-2 rounded-lg border text-xs transition ${
                    assignMode === 'existing'
                      ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border-violet-500/30'
                      : 'border-aegisaxiom-border text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover'
                  }`}
                >
                  Attach to Existing Contract
                </button>
                <button
                  onClick={() => setAssignMode('new')}
                  className={`flex-1 px-3 py-2 rounded-lg border text-xs transition ${
                    assignMode === 'new'
                      ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border-emerald-500/30'
                      : 'border-aegisaxiom-border text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover'
                  }`}
                >
                  Create New Contract
                </button>
              </div>

              {/* Existing Contract Mode */}
              {assignMode === 'existing' && (
                <div className="space-y-3">
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                      Select Contract *
                    </label>
                    <select
                      value={assignForm.contract_id}
                      onChange={(e) =>
                        setAssignForm((f) => ({ ...f, contract_id: e.target.value }))
                      }
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text focus:border-violet-500 outline-none"
                    >
                      <option value="">Choose a contract...</option>
                      {contracts
                        .filter(
                          (c) =>
                            c.status === 'active' &&
                            (!assignContractDeal.account_id ||
                              c.account_id === assignContractDeal.account_id),
                        )
                        .map((c) => (
                          <option key={c.contract_id} value={c.contract_id}>
                            {c.name} ({c.contract_type?.toUpperCase()})
                            {c.account_name ? ` — ${c.account_name}` : ''}
                          </option>
                        ))}
                    </select>
                    {contracts.filter(
                      (c) =>
                        c.status === 'active' && c.account_id === assignContractDeal.account_id,
                    ).length === 0 && (
                      <p className="text-xs text-aegisaxiom-text-muted mt-1">
                        No active contracts found for this account. Create a new one instead.
                      </p>
                    )}
                  </div>

                  <div className="bg-blue-900/15 border border-blue-700/30 rounded-lg p-3">
                    <p className="text-xs text-blue-700 dark:text-blue-300 font-medium mb-1">
                      Note:
                    </p>
                    <p className="text-xs text-aegisaxiom-text-secondary">
                      This will link the deal to the selected contract. The contract's financials
                      and hours will be available for this deal.
                    </p>
                  </div>
                </div>
              )}

              {/* New Contract Mode */}
              {assignMode === 'new' && (
                <ContractFormFields
                  form={newContractForm}
                  setForm={setNewContractForm}
                  accounts={accounts}
                  users={users}
                  defaultResourceTypes={defaultResourceTypes}
                  showServiceCategory={true}
                  compact={false}
                  isAdmin={isAdmin()}
                />
              )}

              {/* Action Buttons */}
              <div className="flex gap-2 pt-2">
                <button
                  onClick={() => setAssignContractDeal(null)}
                  className="flex-1 px-3 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm text-xs rounded-lg transition"
                >
                  Cancel
                </button>
                <button
                  onClick={handleAssignContract}
                  disabled={
                    assignLoading ||
                    (assignMode === 'existing' && !assignForm.contract_id) ||
                    (assignMode === 'new' &&
                      (!newContractForm.name || !newContractForm.service_category))
                  }
                  className="flex-1 px-3 py-2 bg-amber-600 hover:bg-amber-500 text-white text-xs rounded-lg transition disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {assignLoading
                    ? 'Processing...'
                    : assignMode === 'existing'
                      ? 'Attach to Contract'
                      : 'Create & Attach'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ============ COMMISSION MODAL ============ */}
      {commissionDeal &&
        (() => {
          // Calculate commission in real-time
          const revenue = parseFloat(commissionForm.revenue) || 0;
          const costValue = parseFloat(commissionForm.cost_value) || 0;
          const actualCost =
            commissionForm.cost_type === 'percentage' ? revenue * (costValue / 100) : costValue;

          const tier1 = commissionForm.tiers[0] || {};
          const tier2 = commissionForm.tiers[1] || {};
          const tier3 = commissionForm.tiers[2] || {};

          const tier1Commission = revenue * ((tier1.percentage || 0) / 100);
          const tier2Base = revenue - actualCost - tier1Commission;
          const tier2Commission = tier2Base * ((tier2.percentage || 0) / 100);
          const tier3Base = tier2Base - tier2Commission;
          const tier3Commission = tier3Base * ((tier3.percentage || 0) / 100);

          const totalCommission = tier1Commission + tier2Commission + tier3Commission;
          const profitAfterCommission = revenue - actualCost - totalCommission;
          const profitPercentage = revenue > 0 ? (profitAfterCommission / revenue) * 100 : 0;

          return (
            <div
              className="fixed inset-0 z-50 flex items-center justify-center sm:p-4"
              role="dialog"
              aria-modal="true"
              aria-label="Commit Commission Plan"
            >
              <div
                className="absolute inset-0 bg-black/60"
                onClick={() => setCommissionDeal(null)}
              />
              <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border w-full h-full sm:rounded-xl sm:max-w-3xl sm:max-h-[90vh] sm:h-auto overflow-y-auto">
                {/* Header */}
                <div className="sticky top-0 bg-aegisaxiom-surface backdrop-blur border-b border-aegisaxiom-border p-3 sm:p-4 z-10">
                  <div className="flex justify-between items-start gap-3">
                    <div className="min-w-0">
                      <h2 className="text-sm font-semibold text-aegisaxiom-text flex items-center gap-2">
                        <DollarSign
                          size={16}
                          className="text-violet-600 dark:text-violet-400 shrink-0"
                        />
                        Commit Commission Plan
                      </h2>
                      <p className="text-xs text-aegisaxiom-text-secondary mt-1">
                        <span className="text-aegisaxiom-text font-medium">
                          {commissionDeal.name || commissionDeal.deal_name}
                        </span>
                        {commissionDeal.amount > 0 && (
                          <span className="ml-2">{fmt(commissionDeal.amount)}</span>
                        )}
                      </p>
                    </div>
                    <button
                      onClick={() => setCommissionDeal(null)}
                      aria-label="Close"
                      className="w-9 h-9 rounded-full flex items-center justify-center text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition shrink-0"
                    >
                      <X size={18} />
                    </button>
                  </div>
                </div>

                <div className="p-3 sm:p-4 space-y-4">
                  {/* Template Selection */}
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                      Load Template (optional)
                    </label>
                    <select
                      value={commissionForm.template_id}
                      onChange={(e) => {
                        const template = commissionTemplates.find(
                          (t) => t.template_id === e.target.value,
                        );
                        if (template) {
                          setCommissionForm((f) => ({
                            ...f,
                            template_id: template.template_id,
                            cost_type: template.cost_type,
                            cost_value: template.cost_default,
                            tiers: [
                              { ...f.tiers[0], percentage: template.tier1_percentage },
                              { ...f.tiers[1], percentage: template.tier2_percentage },
                              { ...f.tiers[2], percentage: template.tier3_percentage },
                            ],
                          }));
                        }
                      }}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                    >
                      <option value="">
                        {commissionTemplates.length === 0
                          ? 'No templates available'
                          : 'Select a template...'}
                      </option>
                      {commissionTemplates.map((t) => (
                        <option key={t.template_id} value={t.template_id}>
                          {t.name} (T1: {t.tier1_percentage}% • T2: {t.tier2_percentage}% • T3:{' '}
                          {t.tier3_percentage}%)
                        </option>
                      ))}
                    </select>
                    {commissionTemplates.length === 0 && (
                      <p className="text-xs text-aegisaxiom-text-muted mt-1">
                        Create templates in Admin → Commission Settings
                      </p>
                    )}
                  </div>

                  {/* Revenue & Cost */}
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                        Deal Revenue ($) *
                      </label>
                      <input
                        type="number"
                        step="0.01"
                        value={commissionForm.revenue}
                        onChange={(e) =>
                          setCommissionForm((f) => ({ ...f, revenue: e.target.value }))
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Cost</label>
                      <div className="flex gap-2">
                        <select
                          value={commissionForm.cost_type}
                          onChange={(e) =>
                            setCommissionForm((f) => ({ ...f, cost_type: e.target.value }))
                          }
                          className="w-20 px-2 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                        >
                          <option value="percentage">%</option>
                          <option value="actual">$</option>
                        </select>
                        <input
                          type="number"
                          step="0.01"
                          value={commissionForm.cost_value}
                          onChange={(e) =>
                            setCommissionForm((f) => ({ ...f, cost_value: e.target.value }))
                          }
                          className="flex-1 px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                        />
                      </div>
                      <p className="text-[10px] text-aegisaxiom-text-muted mt-1">
                        Actual cost: {fmt(actualCost)}
                      </p>
                    </div>
                  </div>

                  {/* Tier Percentages */}
                  <div className="bg-violet-900/10 border border-violet-800/30 rounded-lg p-3 space-y-2">
                    <h4 className="text-xs font-semibold text-violet-700 dark:text-violet-300">
                      Tier Percentages
                    </h4>
                    {commissionForm.tiers.map((tier, idx) => (
                      <div key={idx} className="flex items-center gap-2">
                        <span className="text-xs text-aegisaxiom-text-secondary w-24">
                          {tier.tier_name}:
                        </span>
                        <input
                          type="number"
                          step="0.1"
                          value={tier.percentage}
                          onChange={(e) => {
                            const updated = [...commissionForm.tiers];
                            updated[idx].percentage = parseFloat(e.target.value) || 0;
                            setCommissionForm((f) => ({ ...f, tiers: updated }));
                          }}
                          className="w-20 px-2 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                        />
                        <span className="text-xs text-aegisaxiom-text-secondary">%</span>
                      </div>
                    ))}
                  </div>

                  {/* User Assignment */}
                  {commissionForm.tiers.map((tier, tierIdx) => (
                    <div
                      key={tierIdx}
                      className="bg-aegisaxiom-accent-muted border border-aegisaxiom-border rounded-lg p-3"
                    >
                      <div className="flex items-center justify-between mb-2">
                        <h4 className="text-xs font-semibold text-aegisaxiom-text">
                          Tier {tier.tier_number}: {tier.tier_name}
                        </h4>
                        <button
                          onClick={() => {
                            const updated = [...commissionForm.tiers];
                            updated[tierIdx].users = [
                              ...(updated[tierIdx].users || []),
                              { user_id: '', user_name: '', split_percentage: 100 },
                            ];
                            setCommissionForm((f) => ({ ...f, tiers: updated }));
                          }}
                          className="text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 flex items-center gap-1"
                        >
                          <Plus size={12} /> Add User
                        </button>
                      </div>
                      {tier.users && tier.users.length > 0 ? (
                        <div className="space-y-2">
                          {tier.users.map((u, userIdx) => (
                            <div key={userIdx} className="flex items-center gap-2">
                              <select
                                value={u.user_id}
                                onChange={(e) => {
                                  const updated = [...commissionForm.tiers];
                                  updated[tierIdx].users[userIdx].user_id = e.target.value;
                                  const user = users.find((usr) => usr.user_id === e.target.value);
                                  updated[tierIdx].users[userIdx].user_name = user ? user.name : '';
                                  setCommissionForm((f) => ({ ...f, tiers: updated }));
                                }}
                                className="flex-1 px-2 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                              >
                                <option value="">Select user...</option>
                                {users.map((usr) => (
                                  <option key={usr.user_id} value={usr.user_id}>
                                    {usr.name}
                                  </option>
                                ))}
                              </select>
                              <input
                                type="number"
                                step="1"
                                value={u.split_percentage}
                                onChange={(e) => {
                                  const updated = [...commissionForm.tiers];
                                  updated[tierIdx].users[userIdx].split_percentage =
                                    parseFloat(e.target.value) || 0;
                                  setCommissionForm((f) => ({ ...f, tiers: updated }));
                                }}
                                className="w-16 px-2 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                              />
                              <span className="text-xs text-aegisaxiom-text-secondary">%</span>
                              <button
                                onClick={() => {
                                  const updated = [...commissionForm.tiers];
                                  updated[tierIdx].users = updated[tierIdx].users.filter(
                                    (_, i) => i !== userIdx,
                                  );
                                  setCommissionForm((f) => ({ ...f, tiers: updated }));
                                }}
                                className="text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300"
                              >
                                <Trash2 size={14} />
                              </button>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="text-xs text-aegisaxiom-text-muted italic">No users assigned yet</p>
                      )}
                    </div>
                  ))}

                  {/* Live Calculation */}
                  <div className="bg-emerald-50 dark:bg-emerald-900/10 border border-emerald-200 dark:border-emerald-800/30 rounded-lg p-3 space-y-2">
                    <h4 className="text-xs font-semibold text-emerald-700 dark:text-emerald-300">
                      Live Calculation
                    </h4>
                    <div className="space-y-1 text-xs">
                      <div className="flex justify-between">
                        <span className="text-aegisaxiom-text-secondary">Revenue:</span>
                        <span className="text-aegisaxiom-text">{fmt(revenue)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-aegisaxiom-text-secondary">Cost:</span>
                        <span className="text-red-600 dark:text-red-400">-{fmt(actualCost)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-aegisaxiom-text-secondary">Tier 1 Commission:</span>
                        <span className="text-blue-600 dark:text-blue-400">
                          -{fmt(tier1Commission)}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-aegisaxiom-text-secondary">Tier 2 Commission:</span>
                        <span className="text-amber-600 dark:text-amber-400">
                          -{fmt(tier2Commission)}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-aegisaxiom-text-secondary">Tier 3 Commission:</span>
                        <span className="text-purple-400">-{fmt(tier3Commission)}</span>
                      </div>
                      <div className="border-t border-aegisaxiom-border pt-2 flex justify-between">
                        <span className="text-emerald-700 dark:text-emerald-300 font-medium">
                          Profit After Commission:
                        </span>
                        <span className="text-emerald-700 dark:text-emerald-300 font-bold">
                          {fmt(profitAfterCommission)}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-emerald-700 dark:text-emerald-300 font-medium">
                          Profit Margin:
                        </span>
                        <span className="text-emerald-700 dark:text-emerald-300 font-bold">
                          {profitPercentage.toFixed(2)}%
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Action Buttons */}
                  <div className="flex gap-2 pt-2">
                    <button
                      onClick={() => setCommissionDeal(null)}
                      className="flex-1 px-3 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm text-xs rounded-lg transition"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={saveCommissionPlan}
                      disabled={commissionLoading}
                      className="flex-1 px-3 py-2 bg-violet-600 hover:bg-violet-500 text-white text-xs rounded-lg transition disabled:opacity-50 font-medium"
                    >
                      {commissionLoading ? 'Saving...' : 'Commit Commission Plan'}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          );
        })()}
    </div>
  );
}
