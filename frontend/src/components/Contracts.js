import React, { useEffect, useState, useMemo, useCallback } from 'react';
import { api, unwrap } from '@/lib/api';
import PaginationControls from '@/components/common/PaginationControls';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/hooks/usePermissions';
import { useSearchParams } from 'react-router-dom';
import ConfirmModal from './shared/ConfirmModal';
import {
  Plus,
  Search,
  Edit2,
  X,
  FileText,
  AlertTriangle,
  Clock,
  ChevronDown,
  ChevronRight,
  Link2,
  Milestone,
  DollarSign,
  Calendar,
  Users,
  Trash2,
  Download,
  ArrowRight,
  CheckCircle,
  RefreshCw,
  Briefcase,
  AlertCircle,
  Folder,
  Wrench,
  Layers,
  Shield,
  FileCheck,
  TrendingUp,
  TrendingDown,
} from 'lucide-react';
import { toast } from 'sonner';
import ContractFormFields from './contracts/ContractFormFields';
import FixedFeeMilestones from './contracts/modals/FixedFeeMilestones';
import CommissionPlanModal from './contracts/modals/CommissionPlanModal';
import ContractFormModal from './contracts/modals/ContractFormModal';
import ImportDealModal from './contracts/modals/ImportDealModal';
import API_URL from '../lib/apiConfig';

const API = API_URL;
const fmt = (n) =>
  `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtH = (n) => `${(n || 0).toFixed(1)}h`;

const TYPE_CONFIG = {
  boh: {
    label: 'Block of Hours',
    color: 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300 border-blue-500/30',
    icon: Clock,
  },
  boa: {
    label: 'Bill on Actuals',
    color:
      'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border-amber-500/30',
    icon: DollarSign,
  },
  fixed_fee: {
    label: 'Fixed Fee',
    color: 'bg-purple-600/20 text-purple-300 border-purple-500/30',
    icon: Milestone,
  },
  monthly: {
    label: 'Monthly',
    color:
      'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border-emerald-500/30',
    icon: Calendar,
  },
  wholesale_mrr: {
    label: 'Wholesale MRR',
    color: 'bg-cyan-600/20 text-cyan-700 dark:text-cyan-300 border-cyan-500/30',
    icon: Briefcase,
  },
  // referral_mrr removed - referral deals bypass contract module
  simplisecure: {
    label: 'SimpliSecure',
    color: 'bg-cyan-600/20 text-cyan-700 dark:text-cyan-300 border-cyan-500/30',
    icon: Shield,
  },
  simplicomply: {
    label: 'SimpliComply',
    color: 'bg-cyan-600/20 text-cyan-700 dark:text-cyan-300 border-cyan-500/30',
    icon: FileCheck,
  },
  other: {
    label: 'Other',
    color: 'bg-aegisaxiom-elevated/20 text-aegisaxiom-text-secondary border-aegisaxiom-border-hover/30',
    icon: DollarSign,
  },
};

// Service Categories for hierarchical contract type selection
const SERVICE_CATEGORIES = {
  field_services: {
    label: 'Field Services',
    icon: Wrench,
    types: ['boh', 'boa', 'fixed_fee'],
  },
  professional_services: {
    label: 'Professional Services',
    icon: Briefcase,
    types: ['boh', 'boa', 'fixed_fee'],
  },
  wholesale_mrr: {
    label: 'Wholesale MRR',
    icon: Layers,
    types: ['simplisecure', 'simplicomply', 'other'],
  },
  // referral_mrr removed - referral deals bypass contract module
};

const STATUS_COLORS = {
  active: 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300',
  on_hold: 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300',
  completed: 'bg-aegisaxiom-elevated/20 text-aegisaxiom-text-secondary',
  expired: 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300',
  cancelled: 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300',
};

const BILLING_PERIODS = [
  { value: 'weekly', label: 'Weekly' },
  { value: 'biweekly', label: 'Bi-Weekly' },
  { value: 'monthly', label: 'Monthly' },
];

const INVOICE_TERMS = [
  { value: 'net_14', label: 'Net 14' },
  { value: 'net_30', label: 'Net 30' },
  { value: 'net_45', label: 'Net 45' },
];

const EMPTY_CONTRACT = {
  name: '',
  account_id: '',
  service_category: '', // 'field_services', 'professional_services', 'wholesale_mrr'
  contract_type: 'boh',
  deal_ids: [],
  start_date: '',
  end_date: '',
  notes: '',
  assigned_sales_rep_id: '',
  assigned_agent_id: '',
  total_hours: '',
  hourly_rate: '',
  billing_period: 'monthly',
  invoice_terms: 'net_30',
  total_amount: '',
  milestones: [],
  monthly_rate: '',
  billing_day: 1,
  resources: [],
  resource_types: [],
  products: [], // Wholesale MRR products
  estimated_boa_cost: '', // Field Services BOA estimated cost
  // Wholesale MRR fields (legacy - keeping for compatibility)
  wholesale_product: '', // 'SimpliSecure' | 'SimplyComply'
  product_id: '', // ID of the selected product from products catalog
  quantity: 1, // Number of licenses/seats
  pricing_tier: 'retail', // 'retail' | 'msp'
  billing_frequency: 'monthly', // 'monthly' | 'quarterly' | 'annual'
  // Project fields
  project_mode: 'none', // 'none' | 'existing' | 'new'
  project_id: '',
  new_project_name: '',
  new_project_description: '',
};

// FixedFeeMilestones component now imported from './contracts/modals/FixedFeeMilestones'

export default function Contracts() {
  const { user } = useAuth();
  const { hasPermission, hasAnyPermission } = usePermissions();
  const [searchParams, setSearchParams] = useSearchParams();
  const [contracts, setContracts] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [users, setUsers] = useState([]);
  const [deals, setDeals] = useState([]);
  const [projects, setProjects] = useState([]);
  const [warnings, setWarnings] = useState([]);
  const [closedWonDeals, setClosedWonDeals] = useState([]);
  const [defaultResourceTypes, setDefaultResourceTypes] = useState([]);
  const [search, setSearch] = useState('');
  // Debounced mirror of `search`. The visible input updates immediately
  // for typing feedback; the debounced value is what we send to the
  // backend, so each keystroke does not trigger a round-trip.
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const [showTestAccountsOnly, setShowTestAccountsOnly] = useState(false);
  // Server-side pagination + server-side search. The earlier slice
  // only sent skip/limit and filtered search client-side over the
  // current page, which made search miss any matching contract that
  // happened to live on a later page. Now the backend's q parameter
  // searches across contract_number / name / account_name /
  // external_contract_id / external_ids.zoho_contract_id /
  // contract_type / status.
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [contractsTotal, setContractsTotal] = useState(0);
  const [modal, setModal] = useState(null); // null | 'create' | 'edit' | 'detail' | 'import' | 'commission'
  const [form, setForm] = useState(EMPTY_CONTRACT);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });
  const [showImportPanel, setShowImportPanel] = useState(false);
  const [importDeal, setImportDeal] = useState(null); // deal being imported
  const [commissionDeal, setCommissionDeal] = useState(null); // deal for commission plan
  const [commissionForm, setCommissionForm] = useState({
    deal_id: '',
    template_id: '',
    revenue: '',
    cost_type: 'percentage',
    cost_value: '',
    tiers: [
      { tier_number: 1, tier_name: 'Top Line', percentage: 10, users: [] },
      { tier_number: 2, tier_name: 'Director Override', percentage: 5, users: [] },
      { tier_number: 3, tier_name: 'Sales Rep', percentage: 15, users: [] },
    ],
    notes: '',
  });
  const [commissionTemplates, setCommissionTemplates] = useState([]);
  const [importMode, setImportMode] = useState('existing'); // 'existing' | 'new'
  const [importForm, setImportForm] = useState({ contract_id: '', hour_adjustment: '', note: '' });
  const [newContractForm, setNewContractForm] = useState({
    name: '',
    service_category: '', // 'field_services', 'professional_services', 'wholesale_mrr'
    contract_type: 'boh',
    total_hours: '',
    hourly_rate: '',
    billing_period: 'monthly',
    invoice_terms: 'net_30',
    total_amount: '',
    monthly_rate: '',
    billing_day: 1,
    start_date: '',
    end_date: '',
    notes: '',
    wholesale_product: '', // for wholesale_mrr
    resource_types: [], // NEW: resource types with hours/rates
    resources: [], // NEW: contractor assignments
  });
  const [syncing, setSyncing] = useState(false);
  const [pageView, setPageView] = useState('contracts'); // 'contracts' | 'deals'
  const [dealsFilter, setDealsFilter] = useState({
    dataFilter: 'all', // 'test' | 'real' | 'all'
    contractStatus: 'all', // 'all' | 'with_contract' | 'without_contract'
    commissionStatus: 'all', // 'all' | 'with_commission' | 'without_commission'
  });
  const [pipeline, setPipeline] = useState({
    unassigned: { items: [] },
    assigned: { items: [] },
    total_closed_won: 0,
  });
  const canEdit = hasPermission('contracts.edit');
  const canCreate = hasPermission('contracts.create');
  const canDelete = hasPermission('contracts.delete');
  const isAdmin = hasPermission('*');
  const canViewFinancials = isAdmin || hasPermission('finance.portal');

  // Filter deals based on current filters
  const filterDeals = (deals) => {
    return deals.filter((d) => {
      // Data filter (test/real/all)
      if (dealsFilter.dataFilter === 'test') {
        // Only show local/test deals
        if (d.source !== 'local' && !d.is_test_data) {
          return false;
        }
      } else if (dealsFilter.dataFilter === 'real') {
        // Only show real deals (not test)
        if (d.source === 'local' || d.is_test_data) {
          return false;
        }
      }
      // if dataFilter === 'all', show everything

      // Contract status filter
      if (dealsFilter.contractStatus === 'with_contract') {
        // Deal must have a contract_id or be in assigned list
        if (
          !d.contract_id &&
          !(pipeline.assigned?.items || []).some((ad) => ad.deal_id === d.deal_id)
        ) {
          return false;
        }
      }
      if (dealsFilter.contractStatus === 'without_contract') {
        // Deal must NOT have a contract
        if (
          d.contract_id ||
          (pipeline.assigned?.items || []).some((ad) => ad.deal_id === d.deal_id)
        ) {
          return false;
        }
      }

      // Commission status filter
      if (dealsFilter.commissionStatus === 'with_commission') {
        // Deal must have a commission_id or commission_plan_id
        if (!d.commission_id && !d.commission_plan_id) {
          return false;
        }
      }
      if (dealsFilter.commissionStatus === 'without_commission') {
        // Deal must NOT have a commission
        if (d.commission_id || d.commission_plan_id) {
          return false;
        }
      }

      return true; // 'all' - no filter
    });
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showTestAccountsOnly, page, pageSize, typeFilter, debouncedSearch]);

  // Debounce the search input. The visible input updates immediately;
  // the debounced value drives the actual /api/contracts?q=... fetch
  // 250ms after the user stops typing.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 250);
    return () => clearTimeout(t);
  }, [search]);

  // Reset to page 1 whenever any filter that affects the result set
  // changes — including the (now server-side) search. The reset
  // itself triggers the page-effect above to re-fetch.
  useEffect(() => {
    setPage(1);
  }, [showTestAccountsOnly, typeFilter, pageSize, debouncedSearch]);

  // Auto-open contract from URL parameter
  useEffect(() => {
    const contractId = searchParams.get('id');
    const mode = searchParams.get('mode');

    if (contractId && contracts.length > 0) {
      const contract = contracts.find((c) => c.contract_id === contractId);
      if (contract) {
        if (mode === 'edit') {
          // Open edit modal using the proper function
          openEdit(contract);
        } else {
          // Open detail view
          openDetail(contract);
        }
        // Clear the parameters after opening
        setSearchParams({});
      }
    }
  }, [contracts, searchParams, setSearchParams]);

  const loadCommissionTemplates = async () => {
    try {
      const res = await api.get(`${API}/api/commission-management/templates`);
      if (res.ok) {
        const data = await res.json();
        setCommissionTemplates(data.items || []);
      }
    } catch (err) {
      console.error('Failed to load commission templates:', err);
    }
  };

  const load = async () => {
    // Build API URLs with optional test data filter
    const testParam = showTestAccountsOnly ? '&test_only=true' : '';
    const accountsUrl = `${API}/api/accounts?limit=500${testParam}`;
    // Server-side pagination + server-side search. The backend caps
    // limit at 500 internally; we never need more than one visible
    // page at a time. q is the debounced search string and is escaped
    // server-side, so regex metacharacters in user input are safe.
    const skip = Math.max(0, (page - 1) * pageSize);
    const contractsParams = new URLSearchParams();
    if (showTestAccountsOnly) contractsParams.set('test_only', 'true');
    if (typeFilter && typeFilter !== 'all') contractsParams.set('contract_type', typeFilter);
    if (debouncedSearch && debouncedSearch.trim()) {
      contractsParams.set('q', debouncedSearch.trim());
    }
    contractsParams.set('skip', String(skip));
    contractsParams.set('limit', String(pageSize));
    const contractsUrl = `${API}/api/contracts?${contractsParams.toString()}`;

    const [cr, ar, ur, dr, pr, drt] = await Promise.all([
      api.get(contractsUrl),
      api.get(accountsUrl),
      api.get(`${API}/api/admin/users`).catch(() => ({ ok: false })),
      api.get(`${API}/api/deals`).catch(() => ({ ok: false })),
      api.get(`${API}/api/projects`).catch(() => ({ ok: false })),
      api.get(`${API}/api/admin/default-resource-types`).catch(() => ({ ok: false })),
    ]);
    if (cr.ok) {
      const raw = await cr.json();
      setContracts(unwrap(raw));
      // The contracts endpoint always returns {items,total,skip,limit};
      // fall back to items.length only if a legacy caller ever swaps
      // the response shape.
      if (raw && typeof raw === 'object' && Number.isFinite(raw.total)) {
        setContractsTotal(raw.total);
      } else if (Array.isArray(raw)) {
        setContractsTotal(raw.length);
      }
    }
    if (ar.ok) setAccounts(unwrap(await ar.json()));
    if (ur.ok) {
      const udata = await ur.json();
      setUsers(Array.isArray(udata) ? udata : udata.items || udata.users || []);
    }
    if (dr.ok) setDeals(unwrap(await dr.json()));
    if (pr.ok) setProjects(unwrap(await pr.json()));
    if (drt.ok) {
      const drtData = await drt.json();
      const types = (drtData.default_resource_types || []).filter((t) => t.is_active);
      // Add "Custom" option at the end
      setDefaultResourceTypes([
        ...types,
        { name: 'Custom', sell_rate: 0, description: 'Define your own' },
      ]);
    }
    // Load warnings
    try {
      const wr = await api.get(`${API}/api/contracts/warnings`);
      if (wr.ok) {
        const wdata = await wr.json();
        setWarnings(wdata.warnings || []);
      }
    } catch {}
    // Load closed won deals
    loadClosedWon();
    loadPipeline();
  };

  const loadClosedWon = async () => {
    try {
      const r = await api.get(`${API}/api/contracts/closed-won-deals?unattached_only=true`);
      if (r.ok) {
        const d = await r.json();
        setClosedWonDeals(d.items || []);
      }
    } catch {}
  };

  const loadPipeline = async () => {
    try {
      const r = await api.get(`${API}/api/contracts/deal-pipeline`);
      if (r.ok) {
        setPipeline(await r.json());
      }
    } catch {}
  };

  const syncZohoDeals = async () => {
    setSyncing(true);
    try {
      const res = await api.post(`${API}/api/integrations/sync/zoho/deals`);
      if (res.ok) {
        const d = await res.json();
        toast.success(d.message);
        await load(); // Refresh everything
      } else {
        let errorMsg = 'Sync failed';
        try {
          const e = await res.json();
          errorMsg = e?.detail || e?.message || errorMsg;
        } catch (parseErr) {
          errorMsg = res.statusText || errorMsg;
        }
        toast.error(errorMsg);
      }
    } catch (err) {
      toast.error('Zoho sync failed');
    } finally {
      setSyncing(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);

    // Frontend validation with clear error messages
    const errors = [];

    if (!form.name?.trim()) errors.push('Contract name is required');
    if (!form.account_id) errors.push('Account/Client is required');
    if (!form.contract_type) errors.push('Contract type is required');

    // Service category only required for NEW contracts (has the selection UI)
    if (modal === 'create' && !form.service_category) {
      errors.push('Service category is required');
    }

    // Type-specific validation
    if (form.contract_type === 'boh') {
      // BOH can use either:
      // 1. Old style: total_hours + hourly_rate
      // 2. New style: resource_types with hours_purchased
      const hasResourceTypes =
        (form.resource_types || []).length > 0 &&
        (form.resource_types || []).some((rt) => parseFloat(rt.hours_purchased || 0) > 0);
      const hasOldStyle = form.total_hours && parseFloat(form.total_hours) > 0;

      if (!hasResourceTypes && !hasOldStyle) {
        errors.push('Total hours must be greater than 0 (add resource types with hours)');
      }
      // Hourly rate is not required for BOH anymore (it's in resource types)
    }
    if (form.contract_type === 'boa') {
      const hasResourceTypeSellRates =
        (form.resource_types || []).length > 0 &&
        (form.resource_types || []).some((rt) => parseFloat(rt.sell_rate || 0) > 0);
      if (!hasResourceTypeSellRates && (!form.hourly_rate || parseFloat(form.hourly_rate) <= 0)) {
        errors.push('Hourly rate or resource types with sell rates are required');
      }
      if (!form.billing_period) errors.push('Billing period is required');
    }
    if (form.contract_type === 'fixed_fee') {
      if (!form.total_amount || parseFloat(form.total_amount) <= 0)
        errors.push('Total amount is required');
    }
    if (form.contract_type === 'monthly') {
      if (!form.monthly_rate || parseFloat(form.monthly_rate) <= 0)
        errors.push('Monthly rate is required');
    }

    if (errors.length > 0) {
      toast.error(
        <div>
          <div className="font-semibold mb-1">Cannot save contract:</div>
          <ul className="text-xs space-y-0.5">
            {errors.map((err, i) => (
              <li key={i}>• {err}</li>
            ))}
          </ul>
        </div>,
        { duration: 5000 },
      );
      setLoading(false);
      return;
    }

    try {
      const body = { ...form };
      delete body._dealSearch; // Remove search helper fields
      delete body._accountSearch;
      delete body._showAccountDropdown;
      delete body.project_mode; // Remove UI state
      delete body.new_project_name;
      delete body.new_project_description;

      if (body.total_hours) body.total_hours = parseFloat(body.total_hours) || 0;
      if (body.hourly_rate) body.hourly_rate = parseFloat(body.hourly_rate) || 0;
      if (body.total_amount) body.total_amount = parseFloat(body.total_amount) || 0;
      if (body.monthly_rate) body.monthly_rate = parseFloat(body.monthly_rate) || 0;
      if (body.billing_day) body.billing_day = parseInt(body.billing_day) || 1;

      const url =
        modal === 'edit' ? `${API}/api/contracts/${form.contract_id}` : `${API}/api/contracts`;
      const res = modal === 'edit' ? await api.put(url, body) : await api.post(url, body);
      if (!res.ok) {
        let errorMsg = 'Failed to save contract';
        try {
          const e = await res.json();
          errorMsg = e.detail || e.message || errorMsg;
        } catch (parseErr) {
          errorMsg = res.statusText || errorMsg;
        }
        throw new Error(errorMsg);
      }

      const savedContract = await res.json();
      const contractId = savedContract.contract_id || form.contract_id;

      // Create project if mode is 'new'
      if (form.project_mode === 'new' && form.new_project_name && contractId) {
        try {
          // Gather contractor IDs from assigned resources
          const contractorIds = (form.assigned_resources || form.resources || [])
            .map((r) => r.user_id)
            .filter(Boolean);

          const projectBody = {
            name: form.new_project_name,
            description: form.new_project_description || '',
            contract_id: contractId,
            account_id: form.account_id,
            contractor_ids: contractorIds,
            assigned_sales_rep_id: form.assigned_sales_rep_id,
            project_manager_id: form.assigned_agent_id, // Use agent as PM
            status: 'active',
            start_date: form.start_date || null,
            end_date: form.end_date || null,
          };

          const projectRes = await api.post(`${API}/api/projects`, projectBody);
          if (projectRes.ok) {
            toast.success('Project created and linked to contract');
          } else {
            const err = await projectRes.json();
            toast.warning(
              `Contract saved, but project creation failed: ${err.detail || 'Unknown error'}`,
            );
          }
        } catch (projErr) {
          toast.warning(`Contract saved, but project creation failed: ${projErr.message}`);
        }
      }

      toast.success(modal === 'edit' ? 'Contract updated' : 'Contract created');
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
      title: 'Delete Contract',
      message: 'Delete this contract and all related data? This action cannot be undone.',
      action: async () => {
        const res = await api.delete(`${API}/api/contracts/${id}`);
        if (res.ok) {
          toast.success('Contract deleted');
          load();
        }
      },
    });
  };

  const openDetail = async (contract) => {
    try {
      const res = await api.get(`${API}/api/contracts/${contract.contract_id}`);
      if (res.ok) {
        setSelected(await res.json());
        setModal('detail');
      }
    } catch {}
  };

  const openEdit = (contract) => {
    const account = accounts.find((a) => a.account_id === contract.account_id);
    setForm({
      ...EMPTY_CONTRACT,
      ...contract,
      total_hours: contract.total_hours || '',
      hourly_rate: contract.hourly_rate || '',
      total_amount: contract.total_amount || '',
      monthly_rate: contract.monthly_rate || '',
      resources: contract.resources || [],
      resource_types: contract.resource_types || [],
      _accountSearch: account?.name || contract.account_name || '',
    });
    setModal('edit');
  };

  // ---- Import Deal Flow ----
  const startImportDeal = (deal) => {
    setImportDeal(deal);
    setImportMode('existing');
    setImportForm({ contract_id: '', hour_adjustment: '', note: '' });
    setNewContractForm({
      name: deal.name || '',
      contract_type: 'boh',
      total_hours: '',
      hourly_rate: '',
      billing_period: 'monthly',
      invoice_terms: 'net_30',
      total_amount: deal.amount || '',
      monthly_rate: '',
      billing_day: 1,
      start_date: '',
      end_date: '',
      notes: `Created from deal: ${deal.name || ''}`,
    });
    setModal('import');
  };

  const handleImportDeal = async () => {
    if (!importDeal) return;
    setLoading(true);
    try {
      const body = {
        deal_id: importDeal.deal_id || importDeal.zoho_deal_id,
      };

      // Only "existing" mode uses this function now
      // "new" mode opens the main contract modal instead
      if (importMode === 'existing') {
        if (!importForm.contract_id) {
          toast.error('Select a contract');
          setLoading(false);
          return;
        }
        body.contract_id = importForm.contract_id;
        if (importForm.hour_adjustment)
          body.hour_adjustment = parseFloat(importForm.hour_adjustment) || 0;
        if (importForm.note) body.note = importForm.note;
      } else {
        toast.error('Invalid import mode');
        setLoading(false);
        return;
      }

      const res = await api.post(`${API}/api/contracts/import-deal`, body);
      if (!res.ok) {
        let errorMsg = 'Failed to import deal';
        try {
          const e = await res.json();
          errorMsg = e.detail || e.message || errorMsg;
        } catch (parseErr) {
          errorMsg = res.statusText || errorMsg;
        }
        throw new Error(errorMsg);
      }
      const data = await res.json();
      toast.success(data.message);
      setModal(null);
      setImportDeal(null);
      load();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };

  const addResource = () => {
    setForm((f) => ({
      ...f,
      resources: [
        ...(f.resources || []),
        { user_id: '', name: '', cost_rate: '', resource_type_id: '', role: '' },
      ],
    }));
  };

  const updateResource = (idx, field, value) => {
    setForm((f) => {
      const res = [...(f.resources || [])];
      res[idx] = { ...res[idx], [field]: value };
      // Auto-fill name from users list when user_id is selected
      if (field === 'user_id' && value) {
        const u = users.find((u) => u.user_id === value);
        if (u) res[idx].name = u.name;
      }
      return { ...f, resources: res };
    });
  };

  const removeResource = (idx) => {
    setForm((f) => ({ ...f, resources: (f.resources || []).filter((_, i) => i !== idx) }));
  };

  // Resource Type helpers
  const addResourceType = () => {
    setForm((f) => ({
      ...f,
      resource_types: [
        ...(f.resource_types || []),
        {
          id: `rt_${Date.now()}`,
          name: '',
          sell_rate: '',
          hours_purchased: '',
          hours_used: 0,
          hours_remaining: 0,
          description: '',
          _isNew: true, // Flag to show dropdown
        },
      ],
    }));
  };

  const quickAddResourceType = (template, idx) => {
    setForm((f) => {
      const types = [...(f.resource_types || [])];
      if (template.name === 'Custom') {
        // Just remove the flag, let user fill manually
        types[idx] = {
          ...types[idx],
          _isNew: false,
          name: '',
          sell_rate: '',
          description: '',
        };
      } else {
        // Pre-fill from template
        types[idx] = {
          ...types[idx],
          _isNew: false,
          name: template.name,
          sell_rate: template.sell_rate,
          description: template.description || '',
        };
      }
      return { ...f, resource_types: types };
    });
  };

  const updateResourceType = (idx, field, value) => {
    setForm((f) => {
      const types = [...(f.resource_types || [])];
      types[idx] = { ...types[idx], [field]: value };
      // Auto-calculate hours_remaining for BOH
      if (f.contract_type === 'boh' && (field === 'hours_purchased' || field === 'hours_used')) {
        const purchased = parseFloat(types[idx].hours_purchased) || 0;
        const used = parseFloat(types[idx].hours_used) || 0;
        types[idx].hours_remaining = purchased - used;
      }
      return { ...f, resource_types: types };
    });
  };

  const removeResourceType = (idx) => {
    setForm((f) => ({
      ...f,
      resource_types: (f.resource_types || []).filter((_, i) => i !== idx),
    }));
  };

  // Pass-through: the backend now applies both contract_type and the
  // free-text q search server-side, so the page-level list is
  // exactly what /api/contracts returned. We keep the name `filtered`
  // because every JSX consumer below already reads it.
  const filtered = contracts;

  const TypeInfo = ({ contract }) => {
    const t = contract.contract_type;
    if (t === 'boh') {
      const total = contract.total_hours || 0;
      const consumed = contract.hours_consumed || 0;
      const remaining = total - consumed;
      const pct = total > 0 ? (consumed / total) * 100 : 0;
      const isLow = total > 0 && remaining / total < 0.1;
      return (
        <div className="space-y-1.5">
          <div className="flex items-center gap-2 text-xs">
            <span className="text-aegisaxiom-text-secondary">
              {fmtH(consumed)} / {fmtH(total)} used
            </span>
            {isLow && (
              <span className="text-red-600 dark:text-red-400 font-medium flex items-center gap-1">
                <AlertTriangle size={12} /> Low
              </span>
            )}
          </div>
          <div className="h-1.5 bg-aegisaxiom-elevated rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${isLow ? 'bg-red-500' : pct > 75 ? 'bg-amber-500' : 'bg-blue-500'}`}
              style={{ width: `${Math.min(pct, 100)}%` }}
            />
          </div>
          {contract.hourly_rate > 0 && (
            <p className="text-xs text-aegisaxiom-text-muted">{fmt(contract.hourly_rate)}/hr</p>
          )}
          {contract.use_by_date &&
            (() => {
              const daysLeft = Math.ceil(
                (new Date(contract.use_by_date) - new Date()) / (1000 * 60 * 60 * 24),
              );
              return (
                <p
                  className={`text-xs ${daysLeft <= 0 ? 'text-red-600 dark:text-red-400 font-medium' : daysLeft <= 30 ? 'text-amber-600 dark:text-amber-400' : 'text-aegisaxiom-text-muted'}`}
                >
                  {daysLeft <= 0
                    ? 'Usage window expired'
                    : `Use by: ${new Date(contract.use_by_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })} (${daysLeft}d left)`}
                </p>
              );
            })()}
        </div>
      );
    }
    if (t === 'boa') {
      return (
        <div className="space-y-0.5 text-xs text-aegisaxiom-text-secondary">
          <p>
            {fmt(contract.hourly_rate || 0)}/hr ·{' '}
            {BILLING_PERIODS.find((b) => b.value === contract.billing_period)?.label ||
              contract.billing_period}
          </p>
          <p>
            {INVOICE_TERMS.find((i) => i.value === contract.invoice_terms)?.label ||
              contract.invoice_terms}
          </p>
        </div>
      );
    }
    if (t === 'fixed_fee') {
      const completed = (contract.milestones || []).filter((m) => m.status === 'completed').length;
      const total = (contract.milestones || []).length;
      return (
        <div className="space-y-0.5 text-xs text-aegisaxiom-text-secondary">
          <p>{fmt(contract.total_amount || 0)} total</p>
          {total > 0 && (
            <p>
              {completed}/{total} milestones complete
            </p>
          )}
        </div>
      );
    }
    if (t === 'monthly') {
      return (
        <p className="text-xs text-aegisaxiom-text-secondary">{fmt(contract.monthly_rate || 0)}/month</p>
      );
    }
    return null;
  };

  return (
    <div data-testid="contracts-page">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
        <div>
          <h1 className="page-title flex items-center gap-2">
            <FileText size={22} className="text-violet-600 dark:text-violet-400" />
            Contracts
          </h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">
            {contracts.length} contracts · {pipeline.total_closed_won} closed won deals
          </p>
        </div>
        <div className="flex flex-col sm:flex-row gap-2 w-full sm:w-auto">
          {canEdit && (
            <button
              onClick={syncZohoDeals}
              disabled={syncing}
              className="flex items-center justify-center gap-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm text-sm px-4 py-2 rounded-lg transition disabled:opacity-50 w-full sm:w-auto"
            >
              <RefreshCw size={16} className={syncing ? 'animate-spin' : ''} /> Sync Zoho
            </button>
          )}
          {canCreate && (
            <button
              onClick={() => {
                setForm(EMPTY_CONTRACT);
                setModal('create');
              }}
              className="flex items-center justify-center gap-2 bg-violet-600 hover:bg-violet-500 text-white text-sm px-4 py-2 rounded-lg transition w-full sm:w-auto"
              data-testid="new-contract-btn"
            >
              <Plus size={16} /> New Contract
            </button>
          )}
        </div>
      </div>

      {/* Page View Toggle */}
      <div className="flex gap-1.5 bg-aegisaxiom-elevated rounded-lg p-0.5 w-fit mb-6">
        <button
          onClick={() => setPageView('contracts')}
          className={`px-4 py-1.5 text-xs rounded-md transition whitespace-nowrap ${pageView === 'contracts' ? 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm font-medium' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
        >
          <FileText size={12} className="inline mr-1.5" />
          Contracts <span className="text-aegisaxiom-text-muted ml-1">{contractsTotal}</span>
        </button>
        <button
          onClick={() => setPageView('deals')}
          className={`px-4 py-1.5 text-xs rounded-md transition relative whitespace-nowrap ${pageView === 'deals' ? 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm font-medium' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
        >
          <Link2 size={12} className="inline mr-1.5" />
          Closed Won Deals{' '}
          <span className="text-aegisaxiom-text-muted ml-1">{pipeline.total_closed_won}</span>
          {pipeline.unassigned?.total > 0 && (
            <span className="absolute -top-1.5 -right-1.5 bg-red-500 text-white text-[10px] font-bold min-w-[16px] h-4 rounded-full flex items-center justify-center px-1">
              {pipeline.unassigned.total}
            </span>
          )}
        </button>
      </div>

      {/* ============ DEALS FILTERS (when on deals view) ============ */}
      {pageView === 'deals' && (
        <div className="flex flex-wrap items-center gap-3 sm:gap-4 bg-aegisaxiom-accent-muted border border-aegisaxiom-border rounded-lg p-3 mb-6">
          {/* Test Deals Only Toggle */}
          {isAdmin && (
            <div className="flex items-center gap-2 text-xs">
              <select
                value={dealsFilter.dataFilter}
                onChange={(e) => setDealsFilter((f) => ({ ...f, dataFilter: e.target.value }))}
                className="px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text-secondary font-medium focus:border-aegisaxiom-border-hover focus:outline-none cursor-pointer hover:border-aegisaxiom-border-hover transition"
              >
                <option value="all">All Data</option>
                <option value="test">Test Data</option>
                <option value="real">Real Data</option>
              </select>
            </div>
          )}

          {/* Contract Status Filter */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-aegisaxiom-text-muted">Contract:</span>
            <div className="flex gap-1 flex-wrap">
              <button
                onClick={() => setDealsFilter((f) => ({ ...f, contractStatus: 'all' }))}
                className={`px-2.5 py-1 rounded text-xs transition ${
                  dealsFilter.contractStatus === 'all'
                    ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-500/30'
                    : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'
                }`}
              >
                All
              </button>
              <button
                onClick={() =>
                  setDealsFilter((f) => ({ ...f, contractStatus: 'without_contract' }))
                }
                className={`px-2.5 py-1 rounded text-xs transition ${
                  dealsFilter.contractStatus === 'without_contract'
                    ? 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300 border border-red-500/30'
                    : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'
                }`}
              >
                No Contract
              </button>
              <button
                onClick={() => setDealsFilter((f) => ({ ...f, contractStatus: 'with_contract' }))}
                className={`px-2.5 py-1 rounded text-xs transition ${
                  dealsFilter.contractStatus === 'with_contract'
                    ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30'
                    : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'
                }`}
              >
                Has Contract
              </button>
            </div>
          </div>

          {/* Commission Status Filter */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-aegisaxiom-text-muted">Commission:</span>
            <div className="flex gap-1 flex-wrap">
              <button
                onClick={() => setDealsFilter((f) => ({ ...f, commissionStatus: 'all' }))}
                className={`px-2.5 py-1 rounded text-xs transition ${
                  dealsFilter.commissionStatus === 'all'
                    ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-500/30'
                    : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'
                }`}
              >
                All
              </button>
              <button
                onClick={() =>
                  setDealsFilter((f) => ({ ...f, commissionStatus: 'without_commission' }))
                }
                className={`px-2.5 py-1 rounded text-xs transition ${
                  dealsFilter.commissionStatus === 'without_commission'
                    ? 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300 border border-red-500/30'
                    : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'
                }`}
              >
                No Commission
              </button>
              <button
                onClick={() =>
                  setDealsFilter((f) => ({ ...f, commissionStatus: 'with_commission' }))
                }
                className={`px-2.5 py-1 rounded text-xs transition ${
                  dealsFilter.commissionStatus === 'with_commission'
                    ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30'
                    : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'
                }`}
              >
                Has Commission
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ============ DEALS PIPELINE VIEW ============ */}
      {pageView === 'deals' && (
        <div className="space-y-5">
          {/* Unassigned Closed Won */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-aegisaxiom-text flex items-center gap-2">
                <AlertTriangle size={16} className="text-amber-600 dark:text-amber-400" />
                No Contract Assigned
                <span className="px-2 py-0.5 rounded-full bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 text-[10px] font-bold">
                  {pipeline.unassigned?.total || 0}
                </span>
              </h2>
            </div>
            {filterDeals(pipeline.unassigned?.items || []).length > 0 ? (
              <div className="space-y-2">
                {filterDeals(pipeline.unassigned.items).map((d) => (
                  <div
                    key={d.deal_id || d.zoho_deal_id}
                    className="bg-aegisaxiom-accent-soft border border-amber-500/20 rounded-lg px-4 py-3 flex flex-col md:flex-row md:items-center md:justify-between gap-3 hover:border-amber-500/40 transition"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium text-aegisaxiom-text">
                          {d.name || d.deal_name}
                        </span>
                        <span className="px-1.5 py-0.5 rounded text-[10px] bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300">
                          Closed Won
                        </span>
                      </div>
                      <div className="flex items-center gap-x-3 gap-y-1 mt-1 text-xs text-aegisaxiom-text-secondary flex-wrap">
                        {d.account_name && (
                          <span className="flex items-center gap-1">
                            <Users size={11} />
                            {d.account_name}
                          </span>
                        )}
                        {d.amount > 0 && (
                          <span className="text-aegisaxiom-text font-medium">{fmt(d.amount)}</span>
                        )}
                        {d.close_date && <span>Closed: {d.close_date}</span>}
                        {d.deal_owner_name && <span>Owner: {d.deal_owner_name}</span>}
                        {(d.commission_id || d.commission_plan_id) && (
                          <span className="px-1.5 py-0.5 bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-500/30 rounded text-[10px] font-medium">
                            ✓ Commission Set
                          </span>
                        )}
                        {d.source === 'local' && (
                          <span className="px-1.5 py-0.5 bg-amber-50 dark:bg-amber-600/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 rounded text-[10px] font-medium">
                            TEST
                          </span>
                        )}
                        {d.zoho_deal_id && (
                          <span className="text-aegisaxiom-text-muted font-mono text-[10px]">
                            Zoho: {d.zoho_deal_id}
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="flex flex-col sm:flex-row gap-2 md:shrink-0">
                      {canEdit && (
                        <>
                          <button
                            onClick={() => startImportDeal(d)}
                            className="flex items-center justify-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-xs px-3 py-1.5 rounded-md transition whitespace-nowrap"
                          >
                            <ArrowRight size={14} /> Assign Contract
                          </button>
                          <button
                            onClick={() => {
                              setCommissionDeal(d);
                              // Pre-fill form with deal data
                              const dealRevenue = d.amount || 0;
                              const dealCost = d.actual_cost || d.cost || null;

                              setCommissionForm({
                                deal_id: d.deal_id || d.zoho_deal_id,
                                template_id: '',
                                revenue: d.amount || '',
                                cost_type: dealCost ? 'actual' : 'percentage',
                                cost_value: dealCost ? String(dealCost) : '60',
                                tiers: [
                                  {
                                    tier_number: 1,
                                    tier_name: 'Top Line',
                                    percentage: 10,
                                    base_amount: 0,
                                    commission_total: 0,
                                    users: d.deal_owner_id
                                      ? [
                                          {
                                            user_id: d.deal_owner_id,
                                            user_name: d.deal_owner_name || '',
                                            split_percentage: 100,
                                            commission_amount: 0,
                                          },
                                        ]
                                      : [],
                                  },
                                  {
                                    tier_number: 2,
                                    tier_name: 'Director Override',
                                    percentage: 5,
                                    base_amount: 0,
                                    commission_total: 0,
                                    users: [],
                                  },
                                  {
                                    tier_number: 3,
                                    tier_name: 'Sales Rep',
                                    percentage: 15,
                                    base_amount: 0,
                                    commission_total: 0,
                                    users: d.assigned_agent_id
                                      ? [
                                          {
                                            user_id: d.assigned_agent_id,
                                            user_name: '',
                                            split_percentage: 100,
                                            commission_amount: 0,
                                          },
                                        ]
                                      : [],
                                  },
                                ],
                                notes: '',
                              });
                              setModal('commission');
                            }}
                            className="flex items-center justify-center gap-1.5 bg-violet-600 hover:bg-violet-500 text-white text-xs px-3 py-1.5 rounded-md transition whitespace-nowrap"
                          >
                            <DollarSign size={14} /> Commit Commission
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="bg-aegisaxiom-accent-muted border border-aegisaxiom-border rounded-lg p-6 text-center">
                <CheckCircle size={24} className="mx-auto mb-2 text-emerald-500 opacity-60" />
                <p className="text-xs text-aegisaxiom-text-secondary">
                  {dealsFilter.dataFilter === 'test'
                    ? 'No test deals without contracts'
                    : dealsFilter.dataFilter === 'real'
                      ? 'No real deals without contracts'
                      : 'All Closed Won deals have contracts assigned'}
                </p>
              </div>
            )}
          </div>

          {/* Assigned to Active Contracts */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-aegisaxiom-text flex items-center gap-2">
                <CheckCircle size={16} className="text-emerald-600 dark:text-emerald-400" />
                Active Contract Deals
                <span className="px-2 py-0.5 rounded-full bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 text-[10px] font-bold">
                  {pipeline.assigned?.total || 0}
                </span>
              </h2>
            </div>
            {filterDeals(pipeline.assigned?.items || []).length > 0 ? (
              <div className="space-y-2">
                {filterDeals(pipeline.assigned.items).map((d) => (
                  <div
                    key={d.deal_id || d.zoho_deal_id}
                    className="bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg px-4 py-3 hover:border-aegisaxiom-border-hover/50 transition"
                  >
                    <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-medium text-aegisaxiom-text">
                            {d.name || d.deal_name}
                          </span>
                          <span className="px-1.5 py-0.5 rounded text-[10px] bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300">
                            Closed Won
                          </span>
                        </div>
                        <div className="flex items-center gap-x-3 gap-y-1 mt-1 text-xs text-aegisaxiom-text-secondary flex-wrap">
                          {d.account_name && (
                            <span className="flex items-center gap-1">
                              <Users size={11} />
                              {d.account_name}
                            </span>
                          )}
                          {d.amount > 0 && (
                            <span className="text-aegisaxiom-text font-medium">{fmt(d.amount)}</span>
                          )}
                          {d.close_date && <span>Closed: {d.close_date}</span>}
                          {d.deal_owner_name && <span>Owner: {d.deal_owner_name}</span>}
                          {(d.commission_id || d.commission_plan_id) && (
                            <span className="px-1.5 py-0.5 bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-500/30 rounded text-[10px] font-medium">
                              ✓ Commission Set
                            </span>
                          )}
                          {d.source === 'local' && (
                            <span className="px-1.5 py-0.5 bg-amber-50 dark:bg-amber-600/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 rounded text-[10px] font-medium">
                              TEST
                            </span>
                          )}
                        </div>
                      </div>
                      {/* Action Buttons for deals with contracts but no commission */}
                      {canEdit && !d.commission_id && !d.commission_plan_id && (
                        <div className="sm:shrink-0">
                          <button
                            onClick={() => {
                              setCommissionDeal(d);
                              // Pre-fill form with deal data
                              const dealRevenue = d.amount || 0;
                              const dealCost = d.actual_cost || d.cost || null;

                              setCommissionForm({
                                deal_id: d.deal_id || d.zoho_deal_id,
                                template_id: '',
                                revenue: d.amount || '',
                                cost_type: dealCost ? 'actual' : 'percentage',
                                cost_value: dealCost ? String(dealCost) : '60',
                                tiers: [
                                  {
                                    tier_number: 1,
                                    tier_name: 'Top Line',
                                    percentage: 10,
                                    base_amount: 0,
                                    commission_total: 0,
                                    users: d.deal_owner_id
                                      ? [
                                          {
                                            user_id: d.deal_owner_id,
                                            user_name: d.deal_owner_name || '',
                                            split_percentage: 100,
                                            commission_amount: 0,
                                          },
                                        ]
                                      : [],
                                  },
                                  {
                                    tier_number: 2,
                                    tier_name: 'Director Override',
                                    percentage: 5,
                                    base_amount: 0,
                                    commission_total: 0,
                                    users: [],
                                  },
                                  {
                                    tier_number: 3,
                                    tier_name: 'Sales Rep',
                                    percentage: 15,
                                    base_amount: 0,
                                    commission_total: 0,
                                    users: d.assigned_agent_id
                                      ? [
                                          {
                                            user_id: d.assigned_agent_id,
                                            user_name: '',
                                            split_percentage: 100,
                                            commission_amount: 0,
                                          },
                                        ]
                                      : [],
                                  },
                                ],
                                notes: '',
                              });
                              setModal('commission');
                            }}
                            className="w-full sm:w-auto flex items-center justify-center gap-1.5 bg-violet-600 hover:bg-violet-500 text-white text-xs px-3 py-1.5 rounded-md transition whitespace-nowrap"
                          >
                            <DollarSign size={14} /> Commit Commission Plan
                          </button>
                        </div>
                      )}
                    </div>
                    {/* Linked Contracts */}
                    {(d.contracts || []).length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-2">
                        {d.contracts.map((c, ci) => {
                          const cfg = TYPE_CONFIG[c.contract_type] || TYPE_CONFIG.fixed_fee;
                          return (
                            <div
                              key={ci}
                              onClick={() => openDetail(c)}
                              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border text-xs cursor-pointer hover:opacity-80 transition ${cfg.color}`}
                            >
                              <cfg.icon size={12} />
                              <span className="font-medium">{c.name}</span>
                              {c.contract_type === 'boh' && c.total_hours > 0 && (
                                <span className="text-[10px] opacity-70">
                                  {fmtH((c.total_hours || 0) - (c.hours_consumed || 0))} left
                                </span>
                              )}
                              {c.contract_type === 'boa' && (
                                <span className="text-[10px] opacity-70">
                                  {fmt(c.hourly_rate)}/hr
                                </span>
                              )}
                              {c.contract_type === 'fixed_fee' && (
                                <span className="text-[10px] opacity-70">
                                  {fmt(c.total_amount)}
                                </span>
                              )}
                              {(c.contract_type === 'monthly' ||
                                c.contract_type === 'wholesale_mrr') && (
                                <span className="text-[10px] opacity-70">
                                  {fmt(c.monthly_rate)}/mo
                                </span>
                              )}
                              {c.contract_type === 'wholesale_mrr' && c.wholesale_product && (
                                <span className="text-[10px] opacity-70 ml-1">
                                  · {c.wholesale_product}
                                </span>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="bg-aegisaxiom-accent-muted border border-aegisaxiom-border rounded-lg p-6 text-center">
                <p className="text-xs text-aegisaxiom-text-secondary">
                  No deals attached to active contracts yet
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ============ CONTRACTS VIEW ============ */}
      {pageView === 'contracts' && (
        <>
          {/* Warnings Banner */}
          {warnings.length > 0 && (
            <div className="bg-red-50 dark:bg-red-900/20 border border-red-500/30 rounded-lg p-3">
              <h3 className="text-red-700 dark:text-red-300 text-xs font-semibold flex items-center gap-1.5 mb-2">
                <AlertTriangle size={14} /> Contract Warnings ({warnings.length})
              </h3>
              <div className="space-y-1.5">
                {warnings.slice(0, 5).map((w, i) => (
                  <div key={i} className="flex items-center justify-between text-xs">
                    <span className="text-aegisaxiom-text-secondary">
                      <span className="font-medium">{w.contract_name}</span>
                      <span className="text-aegisaxiom-text-muted ml-1">({w.account_name})</span>
                    </span>
                    <span
                      className={`px-2 py-0.5 rounded-full ${w.warning_type === 'usage_window_expired' ? 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300' : w.warning_type === 'usage_window_expiring' ? 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300' : w.warning_type === 'low_hours' ? 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300' : 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300'}`}
                    >
                      {w.warning_type === 'usage_window_expired'
                        ? '6mo Expired'
                        : w.warning_type === 'usage_window_expiring'
                          ? 'Expiring Soon'
                          : w.warning_type === 'low_hours'
                            ? 'Low Hours'
                            : w.warning_type.replace(/_/g, ' ')}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Type Tabs + Search */}
          <div className="flex flex-col sm:flex-row sm:items-center gap-3 mt-1">
            {/* Horizontally scrollable on mobile to fit all 9 categories */}
            <div className="overflow-x-auto -mx-2 px-2 sm:mx-0 sm:px-0">
              <div className="flex bg-aegisaxiom-elevated rounded-lg p-1 gap-1 w-fit">
                {[
                  { key: 'all', label: 'All' },
                  ...Object.entries(TYPE_CONFIG).map(([k, v]) => ({ key: k, label: v.label })),
                ].map((tab) => (
                  <button
                    key={tab.key}
                    onClick={() => setTypeFilter(tab.key)}
                    className={`px-3 py-1 text-xs rounded-md transition whitespace-nowrap shrink-0 ${typeFilter === tab.key ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-200 dark:border-violet-600/30' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text border border-transparent hover:border-aegisaxiom-border'}`}
                  >
                    {tab.label}
                    {tab.key === 'all' && (
                      <span className="ml-1 text-aegisaxiom-text-muted">{contractsTotal}</span>
                    )}
                  </button>
                ))}
              </div>
            </div>
            <div className="relative flex-1 sm:max-w-xs">
              <Search
                size={14}
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted"
              />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search contracts..."
                className="w-full pl-8 pr-3 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-md text-xs text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted focus:border-violet-500 outline-none"
              />
            </div>
          </div>

          {/* Contract Cards */}
          <div className="grid gap-4 mt-1">
            {filtered.map((c) => {
              const cfg = TYPE_CONFIG[c.contract_type] || TYPE_CONFIG.fixed_fee;
              const Icon = cfg.icon;
              return (
                <div
                  key={c.contract_id}
                  className="bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg p-4 hover:border-aegisaxiom-border-hover/50 transition cursor-pointer"
                  onClick={() => openDetail(c)}
                  data-testid={`contract-card-${c.contract_id}`}
                >
                  <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                    <div className="flex items-start gap-3 flex-1 min-w-0">
                      <div className={`p-2 rounded-lg ${cfg.color} border shrink-0`}>
                        <Icon size={16} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <h3 className="text-sm font-medium text-aegisaxiom-text truncate">{c.name}</h3>
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] ${STATUS_COLORS[c.status] || STATUS_COLORS.active}`}
                          >
                            {c.status}
                          </span>
                          {/* Simple health indicator (no numbers) */}
                          {c.margin_summary && (
                            <div
                              className={`w-2 h-2 rounded-full ${
                                c.margin_summary.health_color === 'green'
                                  ? 'bg-emerald-500'
                                  : c.margin_summary.health_color === 'yellow'
                                    ? 'bg-yellow-500'
                                    : c.margin_summary.health_color === 'orange'
                                      ? 'bg-orange-500'
                                      : 'bg-red-500'
                              }`}
                              title={`Contract health: ${c.margin_summary.margin_health}`}
                            />
                          )}
                        </div>
                        <p className="text-xs text-aegisaxiom-text-muted mt-0.5">
                          {c.account_name || 'No account'}
                        </p>
                        <div className="mt-2">
                          <TypeInfo contract={c} />
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5 sm:shrink-0 flex-wrap">
                      {(c.deal_ids || []).length > 0 && (
                        <span className="text-xs text-aegisaxiom-text-muted flex items-center gap-1">
                          <Link2 size={12} />
                          {c.deal_ids.length} deals
                        </span>
                      )}
                      {(c.project_ids || []).length > 0 && (
                        <span className="text-xs text-aegisaxiom-text-muted flex items-center gap-1 ml-2">
                          <Users size={12} />
                          {c.project_ids.length} projects
                        </span>
                      )}
                      {canEdit && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            openEdit(c);
                          }}
                          className="ml-1 p-1.5 text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated rounded transition"
                          aria-label={`Edit ${c.name}`}
                        >
                          <Edit2 size={14} />
                        </button>
                      )}
                      {canDelete && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDelete(c.contract_id);
                          }}
                          className="p-1.5 text-aegisaxiom-text-muted hover:text-red-600 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition"
                          aria-label={`Delete ${c.name}`}
                        >
                          <Trash2 size={14} />
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
            {filtered.length === 0 && (
              <div className="text-center py-12 text-aegisaxiom-text-muted text-sm">
                <FileText size={32} className="mx-auto mb-2 opacity-40" />
                {contracts.length === 0
                  ? 'No contracts yet. Create your first contract or import a Closed Won deal.'
                  : 'No contracts match your filter.'}
              </div>
            )}
            <PaginationControls
              page={page}
              pageSize={pageSize}
              total={contractsTotal}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
              loading={loading}
              testid="contracts-pagination"
            />
          </div>
        </>
      )}

      {/* ============ IMPORT DEAL MODAL ============ */}
      <ImportDealModal
        importDeal={importDeal}
        importMode={importMode}
        setImportMode={setImportMode}
        importForm={importForm}
        setImportForm={setImportForm}
        contracts={contracts}
        TYPE_CONFIG={TYPE_CONFIG}
        onImport={handleImportDeal}
        onCreateNew={() => {
          setModal('create');
          setForm({
            name: importDeal?.name || importDeal?.deal_name || '',
            account_id: importDeal?.account_id || '',
            account_name: importDeal?.account_name || '',
            service_category: '',
            contract_type: 'boh',
            start_date: '',
            end_date: '',
            resource_types: [],
            resources: [],
            notes: `Created from deal: ${importDeal?.name || importDeal?.deal_name || ''}`,
            deal_ids: [importDeal?.deal_id || importDeal?.zoho_deal_id],
            _fromDealImport: true,
          });
          setImportDeal(null);
        }}
        onClose={() => {
          setModal(null);
          setImportDeal(null);
        }}
        loading={loading}
      />

      {/* ============ DETAIL MODAL ============ */}
      {modal === 'detail' && selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60" />
          <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-2xl max-h-[85vh] overflow-y-auto">
            <div className="sticky top-0 bg-aegisaxiom-surface backdrop-blur border-b border-aegisaxiom-border p-4 flex justify-between items-start">
              <div>
                <h2 className="text-lg font-semibold text-aegisaxiom-text">{selected.name}</h2>
                <div className="flex items-center gap-2 mt-1">
                  <span
                    className={`px-2 py-0.5 rounded text-xs ${(TYPE_CONFIG[selected.contract_type] || TYPE_CONFIG.fixed_fee).color} border`}
                  >
                    {(TYPE_CONFIG[selected.contract_type] || TYPE_CONFIG.fixed_fee).label}
                  </span>
                  <span className={`px-2 py-0.5 rounded text-xs ${STATUS_COLORS[selected.status]}`}>
                    {selected.status}
                  </span>
                </div>
              </div>
              <button
                onClick={() => setModal(null)}
                className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
              >
                <X size={18} />
              </button>
            </div>
            <div className="p-4 space-y-4">
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <span className="text-aegisaxiom-text-muted">Account:</span>{' '}
                  <span className="text-aegisaxiom-text ml-1">{selected.account_name || '-'}</span>
                </div>
                <div>
                  <span className="text-aegisaxiom-text-muted">Start:</span>{' '}
                  <span className="text-aegisaxiom-text ml-1">{selected.start_date || '-'}</span>
                </div>
                {selected.contract_type !== 'boh' && selected.end_date && (
                  <div>
                    <span className="text-aegisaxiom-text-muted">End:</span>{' '}
                    <span className="text-aegisaxiom-text ml-1">{selected.end_date}</span>
                  </div>
                )}
              </div>

              {/* Operational Health Indicator (Simple visual, no financial details) */}
              {selected.margin_summary && (
                <div className="flex items-center gap-2 px-3 py-2 bg-aegisaxiom-accent-muted rounded-lg border border-aegisaxiom-border">
                  <div
                    className={`w-2 h-2 rounded-full ${
                      selected.margin_summary.health_color === 'green'
                        ? 'bg-emerald-500'
                        : selected.margin_summary.health_color === 'yellow'
                          ? 'bg-yellow-500'
                          : selected.margin_summary.health_color === 'orange'
                            ? 'bg-orange-500'
                            : 'bg-red-500'
                    }`}
                  />
                  <span className="text-xs text-aegisaxiom-text-secondary">
                    Contract Health:{' '}
                    <span
                      className={`font-medium ${
                        selected.margin_summary.health_color === 'green'
                          ? 'text-emerald-600 dark:text-emerald-400'
                          : selected.margin_summary.health_color === 'yellow'
                            ? 'text-yellow-600 dark:text-yellow-400'
                            : selected.margin_summary.health_color === 'orange'
                              ? 'text-orange-600 dark:text-orange-400'
                              : 'text-red-600 dark:text-red-400'
                      }`}
                    >
                      {selected.margin_summary.margin_health?.charAt(0).toUpperCase() +
                        selected.margin_summary.margin_health?.slice(1) || 'Unknown'}
                    </span>
                  </span>
                  {canViewFinancials && (
                    <span className="text-xs text-aegisaxiom-text-muted ml-auto">
                      View details in Finance Dashboard →
                    </span>
                  )}
                </div>
              )}

              {/* Financial Summary - Finance/Admin Only */}
              {canViewFinancials && (selected.ar_summary || selected.ap_summary) && (
                <div className="bg-aegisaxiom-accent-muted rounded-lg border border-amber-200 dark:border-amber-700/30 p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs font-semibold text-amber-700 dark:text-amber-300 flex items-center gap-2">
                      <DollarSign size={14} />
                      Financial Summary (Finance View)
                    </h4>
                    <button
                      onClick={() => navigate('/finance?tab=contracts')}
                      className="text-xs text-amber-600 dark:text-amber-400 hover:text-amber-700 dark:text-amber-300 underline"
                    >
                      View in Finance Portal →
                    </button>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    {/* Revenue (AR) */}
                    <div className="space-y-2">
                      <p className="text-xs font-medium text-emerald-700 dark:text-emerald-300 flex items-center gap-1">
                        <TrendingUp size={12} />
                        Revenue (AR)
                      </p>
                      <div className="space-y-1.5 pl-4">
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-aegisaxiom-text-secondary">Projected/Fixed:</span>
                          <span className="text-aegisaxiom-text font-medium">
                            {fmt(selected.ar_summary?.total_contract_value || 0)}
                          </span>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-aegisaxiom-text-secondary">Invoiced to Date:</span>
                          <span className="text-emerald-600 dark:text-emerald-400 font-medium">
                            {fmt(selected.ar_summary?.invoiced_to_date || 0)}
                          </span>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-aegisaxiom-text-secondary">Outstanding:</span>
                          <span className="text-aegisaxiom-text-secondary">
                            {fmt(selected.ar_summary?.outstanding || 0)}
                          </span>
                        </div>
                        {selected.ar_summary?.percent_consumed > 0 && (
                          <div className="pt-1 border-t border-aegisaxiom-border">
                            <div className="flex items-center justify-between text-xs">
                              <span className="text-aegisaxiom-text-muted">% Complete:</span>
                              <span
                                className={`font-medium ${
                                  selected.ar_summary.percent_consumed > 100
                                    ? 'text-red-600 dark:text-red-400'
                                    : selected.ar_summary.percent_consumed > 80
                                      ? 'text-amber-600 dark:text-amber-400'
                                      : 'text-emerald-600 dark:text-emerald-400'
                                }`}
                              >
                                {selected.ar_summary.percent_consumed.toFixed(1)}%
                              </span>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Cost (AP) */}
                    <div className="space-y-2">
                      <p className="text-xs font-medium text-red-700 dark:text-red-300 flex items-center gap-1">
                        <TrendingDown size={12} />
                        Cost (AP)
                      </p>
                      <div className="space-y-1.5 pl-4">
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-aegisaxiom-text-secondary">Projected/Fixed:</span>
                          <span className="text-aegisaxiom-text font-medium">
                            {fmt(selected.ap_summary?.total_cost_estimate || 0)}
                          </span>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-aegisaxiom-text-secondary">Billed to Date:</span>
                          <span className="text-red-600 dark:text-red-400 font-medium">
                            {fmt(selected.ap_summary?.billed_to_date || 0)}
                          </span>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-aegisaxiom-text-secondary">Outstanding:</span>
                          <span className="text-aegisaxiom-text-secondary">
                            {fmt(selected.ap_summary?.outstanding || 0)}
                          </span>
                        </div>
                        {selected.ap_summary?.resources_assigned > 0 && (
                          <div className="pt-1 border-t border-aegisaxiom-border">
                            <div className="flex items-center justify-between text-xs">
                              <span className="text-aegisaxiom-text-muted">Resources:</span>
                              <span className="text-aegisaxiom-text-secondary">
                                {selected.ap_summary.resources_assigned}
                              </span>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Margin Summary */}
                  {selected.margin_summary && (
                    <div className="pt-3 border-t border-aegisaxiom-border">
                      <div className="grid grid-cols-3 gap-4">
                        <div className="text-center">
                          <p className="text-[10px] text-aegisaxiom-text-muted mb-1">Gross Margin</p>
                          <p
                            className={`text-lg font-bold ${
                              selected.margin_summary.gross_margin_dollars >= 0
                                ? 'text-emerald-600 dark:text-emerald-400'
                                : 'text-red-600 dark:text-red-400'
                            }`}
                          >
                            {fmt(selected.margin_summary.gross_margin_dollars)}
                          </p>
                        </div>
                        <div className="text-center">
                          <p className="text-[10px] text-aegisaxiom-text-muted mb-1">Margin %</p>
                          <p
                            className={`text-lg font-bold ${
                              selected.margin_summary.gross_margin_percent >= 25
                                ? 'text-emerald-600 dark:text-emerald-400'
                                : selected.margin_summary.gross_margin_percent >= 0
                                  ? 'text-amber-600 dark:text-amber-400'
                                  : 'text-red-600 dark:text-red-400'
                            }`}
                          >
                            {selected.margin_summary.gross_margin_percent?.toFixed(1)}%
                          </p>
                        </div>
                        <div className="text-center">
                          <p className="text-[10px] text-aegisaxiom-text-muted mb-1">Health</p>
                          <p
                            className={`text-xs font-semibold px-2 py-1 rounded ${
                              selected.margin_summary.health_color === 'green'
                                ? 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-700/30'
                                : selected.margin_summary.health_color === 'yellow'
                                  ? 'bg-yellow-50 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-300 border border-yellow-200 dark:border-yellow-700/30'
                                  : selected.margin_summary.health_color === 'orange'
                                    ? 'bg-orange-50 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 border border-orange-200 dark:border-orange-700/30'
                                    : 'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-700/30'
                            }`}
                          >
                            {selected.margin_summary.margin_health?.charAt(0).toUpperCase() +
                              selected.margin_summary.margin_health?.slice(1)}
                          </p>
                        </div>
                      </div>
                    </div>
                  )}

                  <p className="text-[10px] text-aegisaxiom-text-muted italic">
                    💡 Projected values based on contract terms. Actuals updated from time tracking
                    & invoicing.
                  </p>
                </div>
              )}

              {selected.contract_type === 'boh' && (
                <div className="bg-aegisaxiom-elevated rounded-lg p-3 space-y-2">
                  <h4 className="text-xs font-semibold text-blue-700 dark:text-blue-300">
                    Block of Hours — 6-Month Usage Window
                  </h4>
                  <div className="grid grid-cols-3 gap-3 text-xs">
                    <div>
                      <span className="text-aegisaxiom-text-muted">Total:</span>{' '}
                      <span className="text-aegisaxiom-text font-medium ml-1">
                        {fmtH(selected.total_hours)}
                      </span>
                    </div>
                    <div>
                      <span className="text-aegisaxiom-text-muted">Used:</span>{' '}
                      <span className="text-aegisaxiom-text font-medium ml-1">
                        {fmtH(selected.hours_consumed)}
                      </span>
                    </div>
                    <div>
                      <span className="text-aegisaxiom-text-muted">Remaining:</span>{' '}
                      <span className="text-aegisaxiom-text font-medium ml-1">
                        {fmtH((selected.total_hours || 0) - (selected.hours_consumed || 0))}
                      </span>
                    </div>
                  </div>
                  {selected.hourly_rate > 0 && (
                    <p className="text-xs text-aegisaxiom-text-secondary">
                      Rate: {fmt(selected.hourly_rate)}/hr
                    </p>
                  )}
                  {/* Usage window info */}
                  {selected.use_by_date &&
                    (() => {
                      const daysLeft = Math.ceil(
                        (new Date(selected.use_by_date) - new Date()) / (1000 * 60 * 60 * 24),
                      );
                      return (
                        <div
                          className={`mt-1 px-2 py-1.5 rounded border text-xs ${daysLeft <= 0 ? 'bg-red-50 dark:bg-red-900/20 border-red-500/30 text-red-700 dark:text-red-300' : daysLeft <= 30 ? 'bg-amber-50 dark:bg-amber-900/20 border-amber-500/30 text-amber-700 dark:text-amber-300' : 'bg-blue-900/15 border-blue-700/30 text-blue-700 dark:text-blue-300'}`}
                        >
                          <span className="font-medium">
                            {daysLeft <= 0 ? 'EXPIRED' : `${daysLeft} days remaining`}
                          </span>
                          <span className="ml-2 opacity-70">
                            Use by:{' '}
                            {new Date(selected.use_by_date).toLocaleDateString('en-US', {
                              month: 'short',
                              day: 'numeric',
                              year: 'numeric',
                            })}
                          </span>
                          {selected.last_usage_date && (
                            <span className="ml-2 opacity-70">
                              · Last used:{' '}
                              {new Date(selected.last_usage_date).toLocaleDateString('en-US', {
                                month: 'short',
                                day: 'numeric',
                                year: 'numeric',
                              })}
                            </span>
                          )}
                          <p className="text-[10px] opacity-60 mt-0.5">
                            Window resets to 6 months on each billable usage
                          </p>
                        </div>
                      );
                    })()}
                  {(selected.hour_adjustments || []).length > 0 && (
                    <div className="mt-2">
                      <p className="text-xs text-aegisaxiom-text-muted font-medium mb-1">
                        Hour Adjustments
                      </p>
                      {selected.hour_adjustments.map((a, i) => (
                        <div key={i} className="text-xs text-aegisaxiom-text-secondary flex gap-2">
                          <span
                            className={
                              a.adjustment > 0
                                ? 'text-emerald-600 dark:text-emerald-400'
                                : 'text-red-600 dark:text-red-400'
                            }
                          >
                            {a.adjustment > 0 ? '+' : ''}
                            {a.adjustment}h
                          </span>
                          <span>{a.note}</span>
                          <span className="text-aegisaxiom-text-muted">{a.date?.split('T')[0]}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {selected.contract_type === 'boa' && (
                <div className="bg-aegisaxiom-elevated rounded-lg p-3 space-y-2">
                  <h4 className="text-xs font-semibold text-amber-700 dark:text-amber-300">
                    Bill on Actuals
                  </h4>
                  <div className="grid grid-cols-2 gap-3 text-xs">
                    <div>
                      <span className="text-aegisaxiom-text-muted">Period:</span>{' '}
                      <span className="text-aegisaxiom-text ml-1">
                        {BILLING_PERIODS.find((b) => b.value === selected.billing_period)?.label}
                      </span>
                    </div>
                    <div>
                      <span className="text-aegisaxiom-text-muted">Terms:</span>{' '}
                      <span className="text-aegisaxiom-text ml-1">
                        {INVOICE_TERMS.find((i) => i.value === selected.invoice_terms)?.label}
                      </span>
                    </div>
                  </div>
                  {/* Rate only for finance/admin */}
                  {canViewFinancials && selected.hourly_rate > 0 && (
                    <p className="text-xs text-aegisaxiom-text-secondary">
                      Blended Rate: {fmt(selected.hourly_rate)}/hr
                    </p>
                  )}
                </div>
              )}
              {selected.contract_type === 'fixed_fee' && (
                <div className="bg-aegisaxiom-elevated rounded-lg p-3 space-y-2">
                  <h4 className="text-xs font-semibold text-purple-300">
                    Fixed Fee — {fmt(selected.total_amount)}
                  </h4>
                  {(selected.milestones || []).length > 0 && (
                    <div className="space-y-1.5">
                      {selected.milestones.map((m, i) => (
                        <div
                          key={i}
                          className="flex items-center justify-between text-xs p-2 bg-aegisaxiom-surface rounded"
                        >
                          <div className="flex items-center gap-2">
                            <div
                              className={`w-2 h-2 rounded-full ${m.status === 'completed' ? 'bg-emerald-500' : 'bg-aegisaxiom-elevated'}`}
                            />
                            <span className="text-aegisaxiom-text">{m.name || `Milestone ${i + 1}`}</span>
                          </div>
                          <div className="flex items-center gap-3">
                            <span className="text-aegisaxiom-text-secondary">
                              {m.percentage}% · {fmt(m.amount)}
                            </span>
                            <span
                              className={`px-1.5 py-0.5 rounded ${m.status === 'completed' ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300' : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary'}`}
                            >
                              {m.status}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {selected.contract_type === 'monthly' && (
                <div className="bg-aegisaxiom-elevated rounded-lg p-3">
                  <h4 className="text-xs font-semibold text-emerald-700 dark:text-emerald-300">
                    Monthly
                  </h4>
                  <p className="text-xs text-aegisaxiom-text-secondary mt-1">
                    {fmt(selected.monthly_rate)}/month · Billing day: {selected.billing_day}
                  </p>
                </div>
              )}

              {/* Resources */}
              {(selected.resources || []).length > 0 && (
                <div>
                  <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-1.5 flex items-center gap-1">
                    <Users size={12} /> Assigned Resources
                  </h4>
                  <div className="space-y-1">
                    {selected.resources.map((r, i) => (
                      <div
                        key={i}
                        className="text-xs p-2 bg-aegisaxiom-elevated rounded flex items-center justify-between"
                      >
                        <div className="flex items-center gap-2">
                          <span className="text-aegisaxiom-text font-medium">{r.name || 'Unnamed'}</span>
                          {r.role && (
                            <span className="px-1.5 py-0.5 rounded bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary text-[10px]">
                              {r.role}
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-3">
                          {r.hours_allocated && (
                            <span className="text-aegisaxiom-text-secondary">
                              {r.hours_allocated}h allocated
                            </span>
                          )}
                          {/* Financial details: Only visible to Finance/Admin */}
                          {canViewFinancials && (
                            <>
                              <span className="text-red-600 dark:text-red-400">
                                Cost: {fmt(r.cost_rate || 0)}/hr
                              </span>
                              <span className="text-emerald-600 dark:text-emerald-400">
                                Sell: {fmt(r.sell_rate || 0)}/hr
                              </span>
                              {(r.sell_rate || 0) > 0 && (r.cost_rate || 0) > 0 && (
                                <span className="text-violet-600 dark:text-violet-400 text-[10px]">
                                  {(((r.sell_rate - r.cost_rate) / r.sell_rate) * 100).toFixed(0)}%
                                  margin
                                </span>
                              )}
                            </>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                  {!canViewFinancials && (
                    <p className="text-[10px] text-aegisaxiom-text-muted mt-2 italic">
                      Rate details available in Finance Dashboard
                    </p>
                  )}
                </div>
              )}
              {(selected.deals || []).length > 0 && (
                <div>
                  <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-1.5 flex items-center gap-1">
                    <Link2 size={12} /> Linked Deals
                  </h4>
                  <div className="space-y-1">
                    {selected.deals.map((d, i) => (
                      <div
                        key={i}
                        className="text-xs p-2 bg-aegisaxiom-elevated rounded flex justify-between"
                      >
                        <span className="text-aegisaxiom-text">{d.name || d.deal_name || d.deal_id}</span>
                        <span className="text-aegisaxiom-text-muted">
                          {d.stage} · {fmt(d.amount)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {(selected.projects || []).length > 0 && (
                <div>
                  <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-1.5 flex items-center gap-1">
                    <Users size={12} /> Projects
                  </h4>
                  <div className="space-y-1">
                    {selected.projects.map((p, i) => (
                      <div
                        key={i}
                        className="text-xs p-2 bg-aegisaxiom-elevated rounded flex justify-between"
                      >
                        <span className="text-aegisaxiom-text">{p.name}</span>
                        <span className="text-aegisaxiom-text-muted">
                          {p.status} · {fmtH(p.hours_logged || 0)} logged
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {selected.notes && (
                <div>
                  <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-1">Notes</h4>
                  <p className="text-xs text-aegisaxiom-text-secondary">{selected.notes}</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ============ CREATE / EDIT MODAL ============ */}
      <ContractFormModal
        modal={modal}
        form={form}
        setForm={setForm}
        accounts={accounts}
        users={users}
        defaultResourceTypes={defaultResourceTypes}
        deals={deals}
        projects={projects}
        isAdmin={isAdmin}
        onSubmit={handleSubmit}
        onClose={() => setModal(null)}
        loading={loading}
      />

      {/* ============ COMMISSION PLAN MODAL (3-TIER) ============ */}
      <CommissionPlanModal
        commissionDeal={commissionDeal}
        commissionForm={commissionForm}
        setCommissionForm={setCommissionForm}
        commissionTemplates={commissionTemplates}
        users={users}
        onClose={() => {
          setModal(null);
          setCommissionDeal(null);
        }}
        onSave={(data) => {
          setModal(null);
          setCommissionDeal(null);
          load(); // Reload contracts
        }}
        loading={loading}
        setLoading={setLoading}
      />
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
