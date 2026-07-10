import React, { useEffect, useState, useRef } from 'react';
import { api, unwrap } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/hooks/usePermissions';
import {
  Plus,
  Mail,
  Trash2,
  X,
  UserCheck,
  UserX,
  RefreshCw,
  Search,
  Edit2,
  Info,
  ChevronDown,
  Briefcase,
  DollarSign,
  FileText,
  ChevronRight,
  Eye,
  Star,
  Calendar,
  Percent,
  Users,
  Bot,
} from 'lucide-react';

// Signed-partnership status pills — reuses existing dark/light Tailwind pairs
// seen elsewhere on this page so the look is consistent.
const PARTNERSHIP_STATUS_CONFIG = {
  none: {
    label: 'None',
    pill: 'bg-aegisaxiom-elevated/20 text-aegisaxiom-text-muted',
  },
  pending: {
    label: 'Pending',
    pill: 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300',
  },
  active: {
    label: 'Active',
    pill: 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300',
  },
  terminated: {
    label: 'Terminated',
    pill: 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300',
  },
};
const PARTNERSHIP_STATUSES = Object.keys(PARTNERSHIP_STATUS_CONFIG);

const formatDate = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
};
const toDateInput = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toISOString().slice(0, 10);
};
import { toast } from 'sonner';
import API_URL from '../../lib/apiConfig';
import ConfirmModal from '../shared/ConfirmModal';
import { isPartnerAccount } from '../Accounts';

const API = API_URL;
const ROLE_COLORS = {
  admin: 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300',
  director: 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300',
  sales_rep: 'bg-green-50 dark:bg-green-600/20 text-green-700 dark:text-green-300',
  agent: 'bg-fuchsia-50 dark:bg-fuchsia-600/20 text-fuchsia-700 dark:text-fuchsia-300',
  customer: 'bg-aegisaxiom-elevated/20 text-aegisaxiom-text-secondary',
  chat_only: 'bg-sky-600/20 text-sky-700 dark:text-sky-300',
  finance: 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300',
};

function Tip({ text, children }) {
  return (
    <div className="relative group/tip">
      {children}
      <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 px-2 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border text-aegisaxiom-text-secondary text-xs rounded-lg whitespace-nowrap opacity-0 group-hover/tip:opacity-100 pointer-events-none transition-opacity z-50 shadow-lg">
        {text}
      </div>
    </div>
  );
}

function AccountSelect({ accounts, value, onChange, placeholder = 'None' }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const ref = useRef(null);
  const selected = accounts.find((a) => a.account_id === value);
  const filtered = [...accounts]
    .sort((a, b) => a.name.localeCompare(b.name))
    .filter((a) => a.name.toLowerCase().includes(search.toLowerCase()));
  useEffect(() => {
    const handle = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, []);
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="input-field text-left flex items-center justify-between w-full"
        data-testid="account-select-trigger"
      >
        <span className={selected ? 'text-aegisaxiom-text' : 'text-aegisaxiom-text-muted'}>
          {selected ? selected.name : placeholder}
        </span>
        <ChevronDown
          size={14}
          className={`text-aegisaxiom-text-muted transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>
      {open && (
        <div className="absolute z-50 top-full mt-1 w-full bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-xl overflow-hidden">
          <div className="p-2 border-b border-aegisaxiom-border">
            <div className="flex items-center gap-2 bg-aegisaxiom-elevated rounded-lg px-3 py-1.5">
              <Search size={12} className="text-aegisaxiom-text-muted shrink-0" />
              <input
                autoFocus
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="bg-transparent text-xs text-aegisaxiom-text-secondary placeholder-aegisaxiom-text-muted outline-none flex-1"
                placeholder="Search accounts..."
              />
            </div>
          </div>
          <div className="max-h-52 overflow-y-auto">
            <button
              type="button"
              onClick={() => {
                onChange('');
                setOpen(false);
                setSearch('');
              }}
              className={`w-full text-left px-3 py-2 text-xs transition-colors ${!value ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300' : 'text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated'}`}
            >
              None
            </button>
            {filtered.map((a) => (
              <button
                type="button"
                key={a.account_id}
                onClick={() => {
                  onChange(a.account_id);
                  setOpen(false);
                  setSearch('');
                }}
                className={`w-full text-left px-3 py-2 text-xs transition-colors ${value === a.account_id ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300' : 'text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated'}`}
              >
                {a.name}
              </button>
            ))}
            {filtered.length === 0 && (
              <p className="px-3 py-4 text-xs text-aegisaxiom-text-muted text-center">
                No accounts found
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function UserManagement() {
  const { user } = useAuth();
  const { hasPermission, isAdmin } = usePermissions();
  const [users, setUsers] = useState([]);
  const [contractors, setContractors] = useState([]);
  const [apps, setApps] = useState([]);
  const [tab, setTab] = useState('internal-users');
  const [modal, setModal] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [roles, setRoles] = useState([]);
  const [form, setForm] = useState({ email: '', name: '', role: 'sales_rep', account_id: '' });
  const [loading, setLoading] = useState(false);
  const [resending, setResending] = useState('');

  // AI Employee Access state
  const [aiAccessUser, setAiAccessUser] = useState(null);
  const [aiEmployees, setAiEmployees] = useState([]);
  const [aiEmployeeAccess, setAiEmployeeAccess] = useState({}); // { employee_id: { allowed_roles, allowed_user_ids } }
  const [aiAccessLoading, setAiAccessLoading] = useState(false);
  const [aiAccessSaving, setAiAccessSaving] = useState(null); // employee_id being saved

  // Resource Type Management
  const [selectedUser, setSelectedUser] = useState(null);
  const [resourceTypes, setResourceTypes] = useState([]);
  const [userResourceTypes, setUserResourceTypes] = useState([]);
  const [resourceForm, setResourceForm] = useState({ resource_type_id: '', cost_rate: '' });

  // Contractor Contracts
  const [contractorContracts, setContractorContracts] = useState([]);
  const [availableContracts, setAvailableContracts] = useState([]);
  const [assignContractForm, setAssignContractForm] = useState({
    contract_id: '',
    resource_type_id: '',
  });
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });

  // AI Usage viewable users
  const [viewableUsersModal, setViewableUsersModal] = useState(null); // user object or null
  const [viewableUserIds, setViewableUserIds] = useState([]);
  const [viewableSearch, setViewableSearch] = useState('');

  // Partnership edit modal — lets admins/directors set signed-partnership status,
  // signed date, and commission rate for partner accounts (director ask).
  const [partnerEditModal, setPartnerEditModal] = useState(null); // account object or null
  const [partnerForm, setPartnerForm] = useState({
    partnership_status: 'pending',
    partnership_signed_date: '',
    commission_rate: '',
  });
  const [savingPartner, setSavingPartner] = useState(false);

  useEffect(() => {
    loadAll();
    loadResourceTypes();
  }, []);

  const loadAll = async () => {
    const [ur, ar, accr, rr] = await Promise.all([
      api.get(`${API}/api/admin/users`),
      api.get(`${API}/api/admin/partner-applications`),
      api.get(`${API}/api/accounts`),
      api.get(`${API}/api/roles`),
    ]);
    if (ur.ok) {
      const allUsers = await ur.json();
      // Split users into internal users and contractors
      setUsers(allUsers.filter((u) => u.role !== 'contractor'));
      setContractors(allUsers.filter((u) => u.role === 'contractor'));
    }
    if (ar.ok) setApps(await ar.json());
    if (accr.ok) {
      const d = unwrap(await accr.json());
      setAccounts(d.sort((a, b) => a.name.localeCompare(b.name)));
    }
    if (rr.ok) setRoles(await rr.json());
  };

  const loadResourceTypes = async () => {
    try {
      const res = await api.get(`${API}/api/admin/default-resource-types`);
      if (res.ok) {
        const data = await res.json();
        // API returns default_resource_types - transform to add id field (use name as id)
        const types = (data.default_resource_types || []).map((rt) => ({
          ...rt,
          id: rt.name, // Use name as the unique identifier
        }));
        setResourceTypes(types);
      }
    } catch (err) {
      console.error('Failed to load resource types:', err);
    }
  };

  // Check if a user has ai_usage.view.managed permission (from role or extra_permissions)
  const hasViewManagedPerm = (u) => {
    const role = roles.find((r) => r.name === u.role);
    const rolePerms = role?.permissions || [];
    const extraPerms = u.extra_permissions || [];
    const allPerms = [...rolePerms, ...extraPerms];
    return allPerms.includes('*') || allPerms.includes('ai_usage.view.managed');
  };

  const openViewableUsersModal = (u) => {
    setViewableUsersModal(u);
    setViewableUserIds(u.ai_usage_viewable_users || []);
    setViewableSearch('');
  };

  const saveViewableUsers = async () => {
    if (!viewableUsersModal) return;
    const res = await api.put(`${API}/api/admin/users/${viewableUsersModal.user_id}`, {
      ai_usage_viewable_users: viewableUserIds,
    });
    if (res.ok) {
      toast.success('Viewable users updated');
      setViewableUsersModal(null);
      loadAll();
    } else {
      toast.error('Failed to update');
    }
  };

  const toggleViewableUser = (userId) => {
    setViewableUserIds((prev) =>
      prev.includes(userId) ? prev.filter((id) => id !== userId) : [...prev, userId],
    );
  };

  // AI Employee Access helpers
  const openAiAccessModal = async (u) => {
    setAiAccessUser(u);
    setAiAccessLoading(true);
    try {
      const empR = await api.get(`${API}/api/ai-employees`);
      const emps = empR.ok ? await empR.json() : [];
      setAiEmployees(Array.isArray(emps) ? emps : []);
      const accessResults = await Promise.all(
        (Array.isArray(emps) ? emps : []).map((emp) => {
          const empId = emp.employee_id || emp.id;
          return api
            .get(`${API}/api/ai-employees/${empId}/access`)
            .then((r) => r.json())
            .then((data) => ({ id: empId, data }))
            .catch(() => ({ id: empId, data: { allowed_roles: [], allowed_user_ids: [] } }));
        }),
      );
      const accessMap = {};
      accessResults.forEach(({ id, data }) => {
        accessMap[id] = data;
      });
      setAiEmployeeAccess(accessMap);
    } catch (err) {
      console.error('Failed to load AI employee access:', err);
      toast.error('Failed to load AI employee access data');
    } finally {
      setAiAccessLoading(false);
    }
  };

  const toggleAiEmployeeAccess = async (employeeId, userId) => {
    const current = aiEmployeeAccess[employeeId] || { allowed_roles: [], allowed_user_ids: [] };
    const hasAccess = current.allowed_user_ids.includes(userId);
    const updatedUserIds = hasAccess
      ? current.allowed_user_ids.filter((id) => id !== userId)
      : [...current.allowed_user_ids, userId];

    setAiAccessSaving(employeeId);
    try {
      const res = await api.put(`${API}/api/ai-employees/${employeeId}/access`, {
        allowed_roles: current.allowed_roles,
        allowed_user_ids: updatedUserIds,
      });
      if (res.ok) {
        setAiEmployeeAccess((prev) => ({
          ...prev,
          [employeeId]: { ...current, allowed_user_ids: updatedUserIds },
        }));
      } else {
        const data = await res.json().catch(() => ({}));
        toast.error(data.detail || 'Failed to update access');
      }
    } catch {
      toast.error('Failed to update access');
    } finally {
      setAiAccessSaving(null);
    }
  };

  const canManageAiEmployees = isAdmin() || hasPermission('ai_employees.manage');

  const openResourceModal = async (u) => {
    setSelectedUser(u);
    setModal('resource-types');
    setResourceForm({ resource_type_id: '', cost_rate: '' });

    // Load user's current resource type assignments
    try {
      const res = await api.get(`${API}/api/users/${u.user_id}/resource-types`);
      if (res.ok) {
        const data = await res.json();
        setUserResourceTypes(data.assignments || []);
      }
    } catch (err) {
      toast.error('Failed to load resource types');
    }
  };

  const addResourceType = async (e) => {
    e.preventDefault();
    if (!resourceForm.resource_type_id || !resourceForm.cost_rate) {
      toast.error('Please select a resource type and enter a cost rate');
      return;
    }

    setLoading(true);
    try {
      const res = await api.post(`${API}/api/users/${selectedUser.user_id}/resource-types`, {
        resource_type_id: resourceForm.resource_type_id,
        cost_rate: parseFloat(resourceForm.cost_rate),
        is_active: true,
      });

      if (res.ok) {
        toast.success('Resource type assigned successfully');
        setResourceForm({ resource_type_id: '', cost_rate: '' });
        openResourceModal(selectedUser); // Reload
      } else {
        const err = await res.json();
        toast.error(err.detail || 'Failed to assign resource type');
      }
    } catch (err) {
      toast.error('Failed to assign resource type');
    } finally {
      setLoading(false);
    }
  };

  const removeResourceType = async (resourceTypeId) => {
    if (!confirm('Remove this resource type assignment?')) return;

    try {
      const res = await api.delete(
        `${API}/api/users/${selectedUser.user_id}/resource-types/${resourceTypeId}`,
      );
      if (res.ok) {
        toast.success('Resource type removed');
        openResourceModal(selectedUser); // Reload
      } else {
        toast.error('Failed to remove resource type');
      }
    } catch (err) {
      toast.error('Failed to remove resource type');
    }
  };

  // Contract assignment functions
  const openContractsModal = async (contractor) => {
    setSelectedUser(contractor);
    setModal('contracts');
    setAssignContractForm({ contract_id: '', resource_type_id: '' });

    // Load contractor's assigned contracts
    try {
      const res = await api.get(`${API}/api/contracts?user_id=${contractor.user_id}`);
      if (res.ok) {
        const allContracts = await res.json();
        // Filter to only contracts where this user is in resources array
        const assigned = allContracts.filter((c) =>
          (c.resources || []).some((r) => r.user_id === contractor.user_id),
        );
        setContractorContracts(assigned);
      }
    } catch (err) {
      console.error('Failed to load contracts:', err);
      setContractorContracts([]);
    }

    // Load available contracts (have matching resource types)
    loadAvailableContracts(contractor);
  };

  const loadAvailableContracts = async (contractor) => {
    try {
      const res = await api.get(`${API}/api/contracts?status=active`);
      if (res.ok) {
        const allContracts = await res.json();
        const contractorResourceTypes = (contractor.resource_type_assignments || []).map(
          (rt) => rt.resource_type_name,
        );

        // Filter contracts that have resource types matching contractor's skills
        const available = allContracts.filter((c) => {
          const contractResourceTypes = (c.resource_types || []).map((rt) => rt.name);
          return contractResourceTypes.some((rt) => contractorResourceTypes.includes(rt));
        });

        setAvailableContracts(available);
      }
    } catch (err) {
      console.error('Failed to load available contracts:', err);
      setAvailableContracts([]);
    }
  };

  const assignToContract = async (e) => {
    e.preventDefault();
    if (!assignContractForm.contract_id || !assignContractForm.resource_type_id) {
      toast.error('Please select both contract and resource type');
      return;
    }

    setLoading(true);
    try {
      // Get the selected contract
      const contract = availableContracts.find(
        (c) => c.contract_id === assignContractForm.contract_id,
      );
      if (!contract) {
        toast.error('Contract not found');
        return;
      }

      // Get the resource type details from the contract
      const resourceType = (contract.resource_types || []).find(
        (rt) => rt.id === assignContractForm.resource_type_id,
      );
      if (!resourceType) {
        toast.error('Resource type not found in contract');
        return;
      }

      // Get contractor's cost rate for this resource type
      const contractorAssignment = (selectedUser.resource_type_assignments || []).find(
        (rt) => rt.resource_type_name === resourceType.name,
      );

      if (!contractorAssignment) {
        toast.error('Contractor does not have this resource type assigned');
        return;
      }

      // Add contractor to contract's resources array
      const updatedResources = [
        ...(contract.resources || []),
        {
          user_id: selectedUser.user_id,
          name: selectedUser.name,
          resource_type_id: resourceType.id,
          resource_type_name: resourceType.name,
          cost_rate: contractorAssignment.cost_rate,
          sell_rate: resourceType.sell_rate,
          role: '',
          assigned_at: new Date().toISOString(),
          assigned_by: user.user_id,
        },
      ];

      // Update contract
      const res = await api.put(`${API}/api/contracts/${contract.contract_id}`, {
        ...contract,
        resources: updatedResources,
      });

      if (res.ok) {
        toast.success('Contractor assigned to contract');
        setAssignContractForm({ contract_id: '', resource_type_id: '' });
        openContractsModal(selectedUser); // Reload
      } else {
        const err = await res.json();
        toast.error(err.detail || 'Failed to assign contractor');
      }
    } catch (err) {
      toast.error('Failed to assign contractor');
    } finally {
      setLoading(false);
    }
  };

  const removeFromContract = async (contract) => {
    if (!confirm(`Remove ${selectedUser.name} from "${contract.name}"?`)) return;

    try {
      const updatedResources = (contract.resources || []).filter(
        (r) => r.user_id !== selectedUser.user_id,
      );

      const res = await api.put(`${API}/api/contracts/${contract.contract_id}`, {
        ...contract,
        resources: updatedResources,
      });

      if (res.ok) {
        toast.success('Removed from contract');
        openContractsModal(selectedUser); // Reload
      } else {
        toast.error('Failed to remove from contract');
      }
    } catch (err) {
      toast.error('Failed to remove from contract');
    }
  };

  const sendInvite = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await api.post(`${API}/api/admin/invite`, form);
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail);
      }
      const data = await res.json();
      toast.success(`Invite sent to ${form.email}`);
      if (data.invite_link) {
        try {
          navigator.clipboard.writeText(data.invite_link);
          toast.info('Invite link copied to clipboard');
        } catch {
          /* clipboard blocked in iframe */
        }
      }
      setModal(null);
      loadAll();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };

  const resendInvite = async (userId, userName) => {
    setResending(userId);
    try {
      const res = await api.post(`${API}/api/admin/resend-invite/${userId}`, {});
      if (res.ok) {
        const data = await res.json();
        toast.success(`Invite resent to ${userName}`);
        if (data.invite_link) {
          try {
            navigator.clipboard.writeText(data.invite_link);
            toast.info('New invite link copied to clipboard');
          } catch {
            /* clipboard blocked in iframe */
          }
        }
      }
      loadAll();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setResending('');
    }
  };

  const updateUser = async (userId, updates) => {
    const res = await api.put(`${API}/api/admin/users/${userId}`, updates);
    if (res.ok) {
      toast.success('User updated');
      loadAll();
    } else {
      const err = await res.json().catch(() => null);
      toast.error(err?.detail || 'Update failed');
    }
  };

  const deleteUser = async (userId) => {
    // UX FIX L-2: Confirmation already handled inline, just remove the window.confirm
    const res = await api.delete(`${API}/api/admin/users/${userId}`);
    if (res.ok) {
      toast.success('User deleted');
      loadAll();
    }
  };

  // Open the partnership edit modal for a partner account. Backend enforces
  // permissions via the existing PUT /api/accounts/{id} route (edit.all / edit.own).
  const openPartnerEdit = (account) => {
    setPartnerForm({
      partnership_status: account.partnership_status || 'pending',
      partnership_signed_date: toDateInput(account.partnership_signed_date),
      commission_rate:
        account.commission_rate === null || account.commission_rate === undefined
          ? ''
          : String(account.commission_rate),
    });
    setPartnerEditModal(account);
  };

  const savePartner = async (e) => {
    e?.preventDefault?.();
    if (!partnerEditModal) return;
    const rate =
      partnerForm.commission_rate === '' ? null : parseFloat(partnerForm.commission_rate);
    if (rate !== null && (Number.isNaN(rate) || rate < 0 || rate > 100)) {
      toast.error('Commission rate must be between 0 and 100.');
      return;
    }
    const payload = {
      partnership_status: partnerForm.partnership_status || null,
      partnership_signed_date: partnerForm.partnership_signed_date
        ? new Date(partnerForm.partnership_signed_date).toISOString()
        : null,
      commission_rate: rate,
    };
    setSavingPartner(true);
    try {
      const res = await api.put(`${API}/api/accounts/${partnerEditModal.account_id}`, payload);
      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.detail || 'Failed to save partnership details');
      }
      toast.success('Partnership details saved');
      setPartnerEditModal(null);
      loadAll();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSavingPartner(false);
    }
  };

  const reviewApp = async (appId, action, reason = '') => {
    // SECURITY FIX H-1: Use PUT method to match backend endpoint definition
    const res = await api.put(`${API}/api/admin/partner-applications/${appId}/${action}`, {
      reason,
    });
    if (res.ok) {
      toast.success(`Application ${action}d`);
      loadAll();
    } else toast.error('Action failed');
  };

  const pendingApps = apps.filter((a) => a.status === 'pending');
  // Partner accounts source the Partners tab (director ask — tab to manage
  // signed partnerships and commission rates). Same predicate as Accounts.js.
  const partnerAccounts = [...accounts]
    .filter(isPartnerAccount)
    .sort((a, b) => (a.name || '').localeCompare(b.name || ''));

  return (
    <div data-testid="user-management-page">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5">
        <div>
          <h1 className="page-title flex items-center gap-2">
            <Users size={22} className="text-violet-600 dark:text-violet-400" />
            User Management
          </h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">
            {users.length} internal users · {contractors.length} contractors · {pendingApps.length}{' '}
            pending applications
          </p>
        </div>
        <button
          onClick={() => {
            setForm({
              email: '',
              name: '',
              role: 'sales_rep',
              account_id: '',
              resource_type_assignments: [],
            });
            setModal('invite');
          }}
          className="btn-primary flex items-center justify-center gap-2 w-full sm:w-auto shrink-0"
          data-testid="invite-user-btn"
        >
          <Plus size={16} />
          Invite User
        </button>
      </div>
      {/* Tabs */}
      <div className="flex flex-wrap border-b border-aegisaxiom-border mb-6 sm:mb-8">
        {[
          { id: 'internal-users', label: 'Internal Users', count: users.length },
          { id: 'contractors', label: 'Contractors', count: contractors.length },
          {
            id: 'partner-applications',
            label: 'Partners',
            count: partnerAccounts.length,
            badge: pendingApps.length,
          },
        ].map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            data-testid={`tab-${t.id}`}
            className={`pb-3 px-3 sm:px-4 text-sm font-medium transition-colors ${tab === t.id ? 'text-violet-600 dark:text-violet-400 border-b-2 border-violet-500' : 'text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary'}`}
          >
            {t.label}
            {t.count !== undefined && (
              <span className="ml-1.5 text-xs text-aegisaxiom-text-muted">({t.count})</span>
            )}
            {t.badge > 0 && (
              <span className="ml-1.5 bg-amber-500 text-black text-xs rounded-full px-1.5 py-0.5 font-bold">
                {t.badge}
              </span>
            )}
          </button>
        ))}
      </div>
      {/* --- INTERNAL USERS TAB --- */}
      {tab === 'internal-users' && (
        <div className="glass-card overflow-hidden">
          {/* Desktop table */}
          <div className="hidden md:block overflow-x-auto">
            <table className="w-full text-sm" data-testid="users-table">
              <thead>
                <tr className="border-b border-aegisaxiom-border">
                  {['Name', 'Email', 'Role', 'Status', 'Account', 'Actions'].map((h) => (
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
                {users.map((u) => (
                  <tr
                    key={u.user_id}
                    className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted transition-colors"
                    data-testid={`user-row-${u.user_id}`}
                  >
                    <td className="py-3 px-4">
                      <div className="flex items-center gap-2.5">
                        <div className="w-8 h-8 rounded-full bg-violet-50 dark:bg-violet-600/20 border border-violet-500/20 flex items-center justify-center text-violet-700 dark:text-violet-300 text-xs font-bold">
                          {u.name?.[0]?.toUpperCase() || 'U'}
                        </div>
                        <div>
                          <p className="text-aegisaxiom-text font-medium">{u.name}</p>
                          {!u.is_active && (
                            <p className="text-xs text-amber-600 dark:text-amber-400">
                              ⏳ Invite pending
                            </p>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">{u.email}</td>
                    <td className="py-3 px-4">
                      {u.user_id !== user.user_id ? (
                        <select
                          className={`text-xs px-2 py-1 rounded-full cursor-pointer outline-none border-0 ${ROLE_COLORS[u.role] || 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary'}`}
                          style={{ width: 'fit-content', minWidth: 'fit-content' }}
                          value={u.role}
                          onChange={(e) => updateUser(u.user_id, { role: e.target.value })}
                          data-testid={`user-role-select-${u.user_id}`}
                        >
                          {roles.map((r) => (
                            <option
                              key={r.name}
                              value={r.name}
                              style={{ background: 'var(--aegisaxiom-bg-elevated)' }}
                            >
                              {r.display_name}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <span
                          className={`inline-flex text-xs px-2 py-0.5 rounded-full w-fit ${ROLE_COLORS[u.role]}`}
                        >
                          {u.role}
                        </span>
                      )}
                    </td>
                    <td className="py-3 px-4">
                      <span
                        className={`inline-flex text-xs px-2 py-0.5 rounded-full w-fit ${u.is_active ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300' : 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300'}`}
                      >
                        {u.is_active ? 'Active' : 'Pending'}
                      </span>
                    </td>
                    <td className="py-3 px-4">
                      {['agent', 'customer'].includes(u.role) && u.user_id !== user.user_id ? (
                        <select
                          className="text-xs bg-transparent text-aegisaxiom-text-secondary hover:text-aegisaxiom-text cursor-pointer outline-none max-w-[160px] truncate"
                          value={u.account_id || ''}
                          onChange={(e) =>
                            updateUser(u.user_id, { account_id: e.target.value || null })
                          }
                          data-testid={`user-account-select-${u.user_id}`}
                        >
                          <option value="" style={{ background: 'var(--aegisaxiom-bg-elevated)' }}>
                            — None —
                          </option>
                          {[...accounts]
                            .sort((a, b) => a.name.localeCompare(b.name))
                            .map((a) => (
                              <option
                                key={a.account_id}
                                value={a.account_id}
                                style={{ background: 'var(--aegisaxiom-bg-elevated)' }}
                              >
                                {a.name}
                              </option>
                            ))}
                        </select>
                      ) : ['agent', 'customer'].includes(u.role) ? (
                        <span className="text-aegisaxiom-text-muted text-xs">
                          {accounts.find((a) => a.account_id === u.account_id)?.name || '—'}
                        </span>
                      ) : (
                        <span className="text-aegisaxiom-text-muted text-xs italic">internal</span>
                      )}
                    </td>
                    <td className="py-3 px-4">
                      {u.user_id !== user.user_id && (
                        <div className="flex items-center gap-2">
                          {!u.is_active && (
                            <Tip text="Resend Invite Email">
                              <button
                                onClick={() => resendInvite(u.user_id, u.name)}
                                disabled={resending === u.user_id}
                                className="text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 transition-colors"
                                data-testid={`resend-invite-${u.user_id}`}
                              >
                                {resending === u.user_id ? (
                                  <RefreshCw size={14} className="animate-spin" />
                                ) : (
                                  <Mail size={14} />
                                )}
                              </button>
                            </Tip>
                          )}
                          <Tip text={u.is_active ? 'Deactivate User' : 'Activate User'}>
                            <button
                              onClick={() => updateUser(u.user_id, { is_active: !u.is_active })}
                              className="text-aegisaxiom-text-muted hover:text-amber-600 dark:hover:text-amber-400 transition-colors"
                              data-testid={`toggle-user-${u.user_id}`}
                            >
                              {u.is_active ? <UserX size={14} /> : <UserCheck size={14} />}
                            </button>
                          </Tip>
                          <Tip text="Delete User">
                            <button
                              onClick={() =>
                                setConfirmState({
                                  open: true,
                                  title: 'Delete User',
                                  message: `Delete ${u.name || u.email}? This cannot be undone.`,
                                  action: () => deleteUser(u.user_id),
                                })
                              }
                              className="text-aegisaxiom-text-muted hover:text-red-600 dark:hover:text-red-400 transition-colors"
                              data-testid={`delete-user-${u.user_id}`}
                            >
                              <Trash2 size={14} />
                            </button>
                          </Tip>
                          {hasViewManagedPerm(u) && (
                            <Tip text="AI Usage Access">
                              <button
                                onClick={() => openViewableUsersModal(u)}
                                className="text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 transition-colors"
                                data-testid={`ai-usage-access-${u.user_id}`}
                              >
                                <Eye size={14} />
                              </button>
                            </Tip>
                          )}
                          {hasPermission('ai_usage.manage') && (
                            <Tip text="Reset AI Token Budget">
                              <button
                                onClick={() =>
                                  setConfirmState({
                                    open: true,
                                    title: 'Reset AI Budget',
                                    message: `Reset the daily AI token budget for ${u.name || u.email}? This will allow them to use AI features again today without deleting their usage history.`,
                                    action: async () => {
                                      const res = await api.post(
                                        `${API}/api/admin/users/${u.user_id}/reset-ai-budget`,
                                      );
                                      if (res.ok) {
                                        toast.success(`AI budget reset for ${u.name || u.email}`);
                                      } else {
                                        const d = await res.json();
                                        toast.error(d.detail || 'Failed to reset budget');
                                      }
                                    },
                                  })
                                }
                                className="text-aegisaxiom-text-muted hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors"
                                data-testid={`reset-ai-budget-${u.user_id}`}
                              >
                                <RefreshCw size={14} />
                              </button>
                            </Tip>
                          )}
                          {canManageAiEmployees && (
                            <Tip text="AI Employee Access">
                              <button
                                onClick={() => openAiAccessModal(u)}
                                className="text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 transition-colors"
                                data-testid={`ai-employee-access-${u.user_id}`}
                              >
                                <Bot size={14} />
                              </button>
                            </Tip>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile card list */}
          <div className="md:hidden p-3 space-y-2.5">
            {users.map((u) => {
              const isSelf = u.user_id === user.user_id;
              const accountName = accounts.find((a) => a.account_id === u.account_id)?.name;
              return (
                <div
                  key={u.user_id}
                  className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5"
                  data-testid={`user-card-${u.user_id}`}
                >
                  {/* Top row: avatar + name + status pill */}
                  <div className="flex items-start justify-between gap-2 mb-2.5">
                    <div className="flex items-center gap-2.5 min-w-0 flex-1">
                      <div className="w-9 h-9 rounded-full bg-violet-50 dark:bg-violet-600/20 border border-violet-500/20 flex items-center justify-center text-violet-700 dark:text-violet-300 text-xs font-bold shrink-0">
                        {u.name?.[0]?.toUpperCase() || 'U'}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium text-aegisaxiom-text truncate">{u.name}</p>
                        <p className="text-[11px] text-aegisaxiom-text-muted truncate">{u.email}</p>
                      </div>
                    </div>
                    <span
                      className={`inline-flex text-[10px] px-2 py-0.5 rounded-full shrink-0 ${u.is_active ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300' : 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300'}`}
                    >
                      {u.is_active ? 'Active' : 'Pending'}
                    </span>
                  </div>

                  {/* Pending notice (if applicable) */}
                  {!u.is_active && (
                    <p className="text-[11px] text-amber-600 dark:text-amber-400 mb-2">
                      ⏳ Invite pending
                    </p>
                  )}

                  {/* Role + Account row */}
                  <div className="grid grid-cols-2 gap-2 mb-3">
                    <div>
                      <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-1">
                        Role
                      </p>
                      {!isSelf ? (
                        <select
                          className={`text-[11px] px-2 py-1 rounded-full cursor-pointer outline-none border-0 w-full ${ROLE_COLORS[u.role] || 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary'}`}
                          value={u.role}
                          onChange={(e) => updateUser(u.user_id, { role: e.target.value })}
                          data-testid={`user-role-select-mobile-${u.user_id}`}
                        >
                          {roles.map((r) => (
                            <option
                              key={r.name}
                              value={r.name}
                              style={{ background: 'var(--aegisaxiom-bg-elevated)' }}
                            >
                              {r.display_name}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <span
                          className={`inline-flex text-[11px] px-2 py-0.5 rounded-full ${ROLE_COLORS[u.role] || 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary'}`}
                        >
                          {u.role}
                        </span>
                      )}
                    </div>
                    <div>
                      <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-1">
                        Account
                      </p>
                      {['agent', 'customer'].includes(u.role) && !isSelf ? (
                        <select
                          className="text-[11px] bg-aegisaxiom-elevated border border-aegisaxiom-border text-aegisaxiom-text-secondary rounded px-2 py-1 cursor-pointer outline-none w-full truncate"
                          value={u.account_id || ''}
                          onChange={(e) =>
                            updateUser(u.user_id, { account_id: e.target.value || null })
                          }
                          data-testid={`user-account-select-mobile-${u.user_id}`}
                        >
                          <option value="">— None —</option>
                          {[...accounts]
                            .sort((a, b) => a.name.localeCompare(b.name))
                            .map((a) => (
                              <option key={a.account_id} value={a.account_id}>
                                {a.name}
                              </option>
                            ))}
                        </select>
                      ) : ['agent', 'customer'].includes(u.role) ? (
                        <p className="text-xs text-aegisaxiom-text-muted truncate">
                          {accountName || '—'}
                        </p>
                      ) : (
                        <p className="text-xs text-aegisaxiom-text-muted italic">internal</p>
                      )}
                    </div>
                  </div>

                  {/* Actions row */}
                  {!isSelf && (
                    <div className="flex items-center gap-1 pt-2 border-t border-aegisaxiom-border flex-wrap">
                      {!u.is_active && (
                        <button
                          onClick={() => resendInvite(u.user_id, u.name)}
                          disabled={resending === u.user_id}
                          className="inline-flex items-center justify-center w-9 h-9 rounded-md text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 hover:bg-aegisaxiom-elevated transition-colors"
                          aria-label="Resend invite email"
                          data-testid={`resend-invite-mobile-${u.user_id}`}
                        >
                          {resending === u.user_id ? (
                            <RefreshCw size={15} className="animate-spin" />
                          ) : (
                            <Mail size={15} />
                          )}
                        </button>
                      )}
                      <button
                        onClick={() => updateUser(u.user_id, { is_active: !u.is_active })}
                        className="inline-flex items-center justify-center w-9 h-9 rounded-md text-aegisaxiom-text-muted hover:text-amber-600 dark:hover:text-amber-400 hover:bg-aegisaxiom-elevated transition-colors"
                        aria-label={u.is_active ? 'Deactivate user' : 'Activate user'}
                        data-testid={`toggle-user-mobile-${u.user_id}`}
                      >
                        {u.is_active ? <UserX size={15} /> : <UserCheck size={15} />}
                      </button>
                      <button
                        onClick={() =>
                          setConfirmState({
                            open: true,
                            title: 'Delete User',
                            message: `Delete ${u.name || u.email}? This cannot be undone.`,
                            action: () => deleteUser(u.user_id),
                          })
                        }
                        className="inline-flex items-center justify-center w-9 h-9 rounded-md text-aegisaxiom-text-muted hover:text-red-600 dark:hover:text-red-400 hover:bg-aegisaxiom-elevated transition-colors"
                        aria-label="Delete user"
                        data-testid={`delete-user-mobile-${u.user_id}`}
                      >
                        <Trash2 size={15} />
                      </button>
                      {hasViewManagedPerm(u) && (
                        <button
                          onClick={() => openViewableUsersModal(u)}
                          className="inline-flex items-center justify-center w-9 h-9 rounded-md text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 hover:bg-aegisaxiom-elevated transition-colors"
                          aria-label="AI usage access"
                          data-testid={`ai-usage-access-mobile-${u.user_id}`}
                        >
                          <Eye size={15} />
                        </button>
                      )}
                      {hasPermission('ai_usage.manage') && (
                        <button
                          onClick={() =>
                            setConfirmState({
                              open: true,
                              title: 'Reset AI Budget',
                              message: `Reset the daily AI token budget for ${u.name || u.email}? This will allow them to use AI features again today without deleting their usage history.`,
                              action: async () => {
                                const res = await api.post(
                                  `${API}/api/admin/users/${u.user_id}/reset-ai-budget`,
                                );
                                if (res.ok) {
                                  toast.success(`AI budget reset for ${u.name || u.email}`);
                                } else {
                                  const d = await res.json();
                                  toast.error(d.detail || 'Failed to reset budget');
                                }
                              },
                            })
                          }
                          className="inline-flex items-center justify-center w-9 h-9 rounded-md text-aegisaxiom-text-muted hover:text-emerald-600 dark:hover:text-emerald-400 hover:bg-aegisaxiom-elevated transition-colors"
                          aria-label="Reset AI token budget"
                          data-testid={`reset-ai-budget-mobile-${u.user_id}`}
                        >
                          <RefreshCw size={15} />
                        </button>
                      )}
                      {canManageAiEmployees && (
                        <button
                          onClick={() => openAiAccessModal(u)}
                          className="inline-flex items-center justify-center w-9 h-9 rounded-md text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 hover:bg-aegisaxiom-elevated transition-colors"
                          aria-label="AI employee access"
                          data-testid={`ai-employee-access-mobile-${u.user_id}`}
                        >
                          <Bot size={15} />
                        </button>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
      {/* --- CONTRACTORS TAB --- */}
      {tab === 'contractors' && (
        <div className="">
          <div className="glass-card p-4 border-violet-200 dark:border-violet-600/20 bg-violet-600/5 flex items-start gap-3 mb-6">
            <Info size={16} className="text-violet-600 dark:text-violet-400 shrink-0 mt-0.5" />
            <div>
              <p className="text-violet-700 dark:text-violet-300 text-sm font-medium">
                Contractor Management
              </p>
              <p className="text-aegisaxiom-text-secondary text-xs mt-1 leading-relaxed">
                Contractors are external professionals who can be assigned to contracts based on
                their resource types (skills). Manage their capabilities, rates, and contract
                assignments here.
              </p>
            </div>
          </div>

          {contractors.length === 0 ? (
            <div className="glass-card p-12 sm:p-16 text-center">
              <Briefcase size={32} className="text-aegisaxiom-text-muted mx-auto mb-3" />
              <p className="text-aegisaxiom-text-muted text-sm">No contractors yet</p>
              <p className="text-aegisaxiom-text-muted text-xs mt-1">
                Invite a user with the "Contractor" role to get started
              </p>
            </div>
          ) : (
            <div className="glass-card overflow-hidden">
              {/* Desktop table */}
              <div className="hidden md:block overflow-x-auto">
                <table className="w-full text-sm" data-testid="contractors-table">
                  <thead>
                    <tr className="border-b border-aegisaxiom-border">
                      {[
                        'Name',
                        'Email',
                        'Status',
                        'Resource Types',
                        'Assigned Contracts',
                        'Actions',
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
                    {contractors.map((c) => (
                      <tr
                        key={c.user_id}
                        className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted transition-colors"
                        data-testid={`contractor-row-${c.user_id}`}
                      >
                        <td className="py-3 px-4">
                          <div className="flex items-center gap-2.5">
                            <div className="w-8 h-8 rounded-full bg-violet-50 dark:bg-violet-600/20 border border-violet-500/20 flex items-center justify-center text-violet-700 dark:text-violet-300 text-xs font-bold">
                              {c.name?.[0]?.toUpperCase() || 'C'}
                            </div>
                            <div>
                              <p className="text-aegisaxiom-text font-medium">{c.name}</p>
                              {!c.is_active && (
                                <p className="text-xs text-amber-600 dark:text-amber-400">
                                  ⏳ Invite pending
                                </p>
                              )}
                            </div>
                          </div>
                        </td>
                        <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">{c.email}</td>
                        <td className="py-3 px-4">
                          <span
                            className={`inline-flex text-xs px-2 py-0.5 rounded-full w-fit ${c.is_active ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300' : 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300'}`}
                          >
                            {c.is_active ? 'Active' : 'Pending'}
                          </span>
                        </td>
                        <td className="py-3 px-4">
                          <button
                            onClick={() => openResourceModal(c)}
                            className="flex items-center gap-1.5 text-xs px-2 py-1 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text-secondary hover:text-violet-600 dark:hover:text-violet-400 border border-aegisaxiom-border rounded-lg transition-colors"
                            data-testid={`manage-resources-${c.user_id}`}
                          >
                            <Briefcase size={12} />
                            <span>{c.resource_type_assignments?.length || 0} types</span>
                            <Edit2 size={10} />
                          </button>
                        </td>
                        <td className="py-3 px-4">
                          <button
                            onClick={() => openContractsModal(c)}
                            className="flex items-center gap-1.5 text-xs px-2 py-1 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text-secondary hover:text-blue-600 dark:hover:text-blue-400 border border-aegisaxiom-border rounded-lg transition-colors"
                            data-testid={`view-contracts-${c.user_id}`}
                          >
                            <FileText size={12} />
                            <span>{c.assigned_contracts_count || 0} contracts</span>
                            <ChevronRight size={10} />
                          </button>
                        </td>
                        <td className="py-3 px-4">
                          <div className="flex items-center gap-2">
                            {!c.is_active && (
                              <Tip text="Resend Invite Email">
                                <button
                                  onClick={() => resendInvite(c.user_id, c.name)}
                                  disabled={resending === c.user_id}
                                  className="text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 transition-colors"
                                  data-testid={`resend-invite-${c.user_id}`}
                                >
                                  {resending === c.user_id ? (
                                    <RefreshCw size={14} className="animate-spin" />
                                  ) : (
                                    <Mail size={14} />
                                  )}
                                </button>
                              </Tip>
                            )}
                            <Tip
                              text={c.is_active ? 'Deactivate Contractor' : 'Activate Contractor'}
                            >
                              <button
                                onClick={() => updateUser(c.user_id, { is_active: !c.is_active })}
                                className="text-aegisaxiom-text-muted hover:text-amber-600 dark:hover:text-amber-400 transition-colors"
                                data-testid={`toggle-contractor-${c.user_id}`}
                              >
                                {c.is_active ? <UserX size={14} /> : <UserCheck size={14} />}
                              </button>
                            </Tip>
                            <Tip text="Delete Contractor">
                              <button
                                onClick={() => {
                                  if (
                                    confirm(`Delete ${c.name || c.email}? This cannot be undone.`)
                                  ) {
                                    deleteUser(c.user_id);
                                  }
                                }}
                                className="text-aegisaxiom-text-muted hover:text-red-600 dark:hover:text-red-400 transition-colors"
                                data-testid={`delete-contractor-${c.user_id}`}
                              >
                                <Trash2 size={14} />
                              </button>
                            </Tip>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Mobile card list */}
              <div className="md:hidden p-3 space-y-2.5">
                {contractors.map((c) => (
                  <div
                    key={c.user_id}
                    className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5"
                    data-testid={`contractor-card-${c.user_id}`}
                  >
                    {/* Header row: avatar + name + status */}
                    <div className="flex items-start justify-between gap-2 mb-3">
                      <div className="flex items-center gap-2.5 min-w-0 flex-1">
                        <div className="w-9 h-9 rounded-full bg-violet-50 dark:bg-violet-600/20 border border-violet-500/20 flex items-center justify-center text-violet-700 dark:text-violet-300 text-xs font-bold shrink-0">
                          {c.name?.[0]?.toUpperCase() || 'C'}
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium text-aegisaxiom-text truncate">{c.name}</p>
                          <p className="text-[11px] text-aegisaxiom-text-muted truncate">{c.email}</p>
                        </div>
                      </div>
                      <span
                        className={`inline-flex text-[10px] px-2 py-0.5 rounded-full shrink-0 ${c.is_active ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300' : 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300'}`}
                      >
                        {c.is_active ? 'Active' : 'Pending'}
                      </span>
                    </div>

                    {!c.is_active && (
                      <p className="text-[11px] text-amber-600 dark:text-amber-400 mb-2">
                        ⏳ Invite pending
                      </p>
                    )}

                    {/* Resource types + contracts row */}
                    <div className="flex flex-wrap gap-2 mb-3">
                      <button
                        onClick={() => openResourceModal(c)}
                        className="flex items-center gap-1.5 text-xs px-2 py-1 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text-secondary hover:text-violet-600 dark:hover:text-violet-400 border border-aegisaxiom-border rounded-lg transition-colors"
                        data-testid={`manage-resources-mobile-${c.user_id}`}
                      >
                        <Briefcase size={12} />
                        <span>{c.resource_type_assignments?.length || 0} types</span>
                        <Edit2 size={10} />
                      </button>
                      <button
                        onClick={() => openContractsModal(c)}
                        className="flex items-center gap-1.5 text-xs px-2 py-1 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text-secondary hover:text-blue-600 dark:hover:text-blue-400 border border-aegisaxiom-border rounded-lg transition-colors"
                        data-testid={`view-contracts-mobile-${c.user_id}`}
                      >
                        <FileText size={12} />
                        <span>{c.assigned_contracts_count || 0} contracts</span>
                        <ChevronRight size={10} />
                      </button>
                    </div>

                    {/* Actions row */}
                    <div className="flex items-center gap-1 pt-2 border-t border-aegisaxiom-border">
                      {!c.is_active && (
                        <button
                          onClick={() => resendInvite(c.user_id, c.name)}
                          disabled={resending === c.user_id}
                          className="inline-flex items-center justify-center w-9 h-9 rounded-md text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 hover:bg-aegisaxiom-elevated transition-colors"
                          aria-label="Resend invite email"
                          data-testid={`resend-invite-mobile-${c.user_id}`}
                        >
                          {resending === c.user_id ? (
                            <RefreshCw size={15} className="animate-spin" />
                          ) : (
                            <Mail size={15} />
                          )}
                        </button>
                      )}
                      <button
                        onClick={() => updateUser(c.user_id, { is_active: !c.is_active })}
                        className="inline-flex items-center justify-center w-9 h-9 rounded-md text-aegisaxiom-text-muted hover:text-amber-600 dark:hover:text-amber-400 hover:bg-aegisaxiom-elevated transition-colors"
                        aria-label={c.is_active ? 'Deactivate contractor' : 'Activate contractor'}
                        data-testid={`toggle-contractor-mobile-${c.user_id}`}
                      >
                        {c.is_active ? <UserX size={15} /> : <UserCheck size={15} />}
                      </button>
                      <button
                        onClick={() => {
                          if (confirm(`Delete ${c.name || c.email}? This cannot be undone.`)) {
                            deleteUser(c.user_id);
                          }
                        }}
                        className="inline-flex items-center justify-center w-9 h-9 rounded-md text-aegisaxiom-text-muted hover:text-red-600 dark:hover:text-red-400 hover:bg-aegisaxiom-elevated transition-colors"
                        aria-label="Delete contractor"
                        data-testid={`delete-contractor-mobile-${c.user_id}`}
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
      {/* --- PARTNERS TAB --- */}
      {tab === 'partner-applications' && (
        <div className="">
          <div className="glass-card p-4 border-violet-200 dark:border-violet-600/20 bg-violet-600/5 flex items-start gap-3 mb-6">
            <Info size={16} className="text-violet-600 dark:text-violet-400 shrink-0 mt-0.5" />
            <div>
              <p className="text-violet-700 dark:text-violet-300 text-sm font-medium">
                About Partners
              </p>
              <p className="text-aegisaxiom-text-secondary text-xs mt-1 leading-relaxed">
                Active partners are the partner-agency accounts we have signed relationships with.
                Track their signed-partnership status, signed date, and commission rate here. New
                applicants submit via the external partner form and appear under Pending
                Applications below; approved applicants are onboarded as{' '}
                <strong className="text-aegisaxiom-text-secondary">Agent</strong>-role users attached to a
                new <strong className="text-aegisaxiom-text-secondary">Partner Agency</strong> account.
              </p>
            </div>
          </div>

          {/* --- Active Partners --- */}
          <div className="mb-8">
            <h2
              className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3 flex items-center gap-2"
              style={{ fontFamily: 'Manrope, sans-serif' }}
            >
              <Star size={14} className="text-yellow-500 fill-yellow-400" />
              Active Partners
              <span className="text-xs text-aegisaxiom-text-muted font-normal">
                ({partnerAccounts.length})
              </span>
            </h2>
            {partnerAccounts.length === 0 ? (
              <div className="glass-card p-10 text-center text-aegisaxiom-text-muted">
                No partner accounts yet. Partners are accounts marked{' '}
                <strong className="text-aegisaxiom-text-secondary">Partner Agency</strong> on the Accounts
                page.
              </div>
            ) : (
              <div className="glass-card overflow-hidden">
                {/* Desktop table */}
                <div className="hidden md:block overflow-x-auto">
                  <table className="w-full text-sm" data-testid="partners-table">
                    <thead>
                      <tr className="border-b border-aegisaxiom-border">
                        {['Partner', 'Status', 'Signed', 'Commission', 'Actions'].map((h) => (
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
                      {partnerAccounts.map((a) => {
                        const statusKey = a.partnership_status || 'pending';
                        const statusConfig =
                          PARTNERSHIP_STATUS_CONFIG[statusKey] || PARTNERSHIP_STATUS_CONFIG.pending;
                        return (
                          <tr
                            key={a.account_id}
                            className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted transition-colors"
                            data-testid={`partner-row-${a.account_id}`}
                          >
                            <td className="py-3 px-4">
                              <div className="flex items-center gap-2.5">
                                <div className="w-8 h-8 rounded-full bg-fuchsia-50 dark:bg-fuchsia-600/20 border border-fuchsia-500/20 flex items-center justify-center text-fuchsia-700 dark:text-fuchsia-300 text-xs font-bold">
                                  {a.name?.[0]?.toUpperCase() || 'P'}
                                </div>
                                <div>
                                  <p className="text-aegisaxiom-text font-medium">{a.name}</p>
                                  <p className="text-xs text-aegisaxiom-text-muted">
                                    {a.industry || a.email || 'No details'}
                                  </p>
                                </div>
                              </div>
                            </td>
                            <td className="py-3 px-4">
                              <span
                                className={`inline-flex text-xs px-2 py-0.5 rounded-full w-fit ${statusConfig.pill}`}
                              >
                                {statusConfig.label}
                              </span>
                            </td>
                            <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                              {formatDate(a.partnership_signed_date)}
                            </td>
                            <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                              {a.commission_rate === null || a.commission_rate === undefined
                                ? '—'
                                : `${a.commission_rate}%`}
                            </td>
                            <td className="py-3 px-4">
                              <Tip text="Edit partnership details">
                                <button
                                  onClick={() => openPartnerEdit(a)}
                                  className="text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 transition-colors"
                                  data-testid={`edit-partner-${a.account_id}`}
                                >
                                  <Edit2 size={14} />
                                </button>
                              </Tip>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                {/* Mobile card list */}
                <div className="md:hidden p-3 space-y-2.5">
                  {partnerAccounts.map((a) => {
                    const statusKey = a.partnership_status || 'pending';
                    const statusConfig =
                      PARTNERSHIP_STATUS_CONFIG[statusKey] || PARTNERSHIP_STATUS_CONFIG.pending;
                    return (
                      <div
                        key={a.account_id}
                        className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5"
                        data-testid={`partner-card-${a.account_id}`}
                      >
                        {/* Header row */}
                        <div className="flex items-start justify-between gap-2 mb-3">
                          <div className="flex items-center gap-2.5 min-w-0 flex-1">
                            <div className="w-9 h-9 rounded-full bg-fuchsia-50 dark:bg-fuchsia-600/20 border border-fuchsia-500/20 flex items-center justify-center text-fuchsia-700 dark:text-fuchsia-300 text-xs font-bold shrink-0">
                              {a.name?.[0]?.toUpperCase() || 'P'}
                            </div>
                            <div className="min-w-0 flex-1">
                              <p className="text-sm font-medium text-aegisaxiom-text truncate">
                                {a.name}
                              </p>
                              <p className="text-[11px] text-aegisaxiom-text-muted truncate">
                                {a.industry || a.email || 'No details'}
                              </p>
                            </div>
                          </div>
                          <span
                            className={`inline-flex text-[10px] px-2 py-0.5 rounded-full shrink-0 ${statusConfig.pill}`}
                          >
                            {statusConfig.label}
                          </span>
                        </div>

                        {/* Stats grid */}
                        <div className="grid grid-cols-2 gap-2 mb-3 text-xs">
                          <div>
                            <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                              Signed
                            </p>
                            <p className="text-aegisaxiom-text-secondary">
                              {formatDate(a.partnership_signed_date) || '—'}
                            </p>
                          </div>
                          <div>
                            <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                              Commission
                            </p>
                            <p className="text-aegisaxiom-text-secondary">
                              {a.commission_rate === null || a.commission_rate === undefined
                                ? '—'
                                : `${a.commission_rate}%`}
                            </p>
                          </div>
                        </div>

                        {/* Edit button */}
                        <div className="flex items-center justify-end pt-2 border-t border-aegisaxiom-border">
                          <button
                            onClick={() => openPartnerEdit(a)}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium text-aegisaxiom-text-secondary hover:text-violet-600 dark:hover:text-violet-400 hover:bg-aegisaxiom-elevated transition-colors"
                            aria-label={`Edit partnership details for ${a.name}`}
                            data-testid={`edit-partner-mobile-${a.account_id}`}
                          >
                            <Edit2 size={13} />
                            Edit details
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {/* --- Pending Applications (existing workflow) --- */}
          <h2
            className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3 flex items-center gap-2"
            style={{ fontFamily: 'Manrope, sans-serif' }}
          >
            Pending Applications
            <span className="text-xs text-aegisaxiom-text-muted font-normal">({pendingApps.length})</span>
          </h2>
          {apps.length === 0 ? (
            <div className="glass-card p-10 text-center text-aegisaxiom-text-muted">
              No partner applications yet.
            </div>
          ) : (
            <div className="space-y-3">
              {apps.map((app) => (
                <div
                  key={app.application_id}
                  className="glass-card p-4 sm:p-5"
                  data-testid={`app-${app.application_id}`}
                >
                  <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 sm:gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1 flex-wrap">
                        <p className="text-aegisaxiom-text font-semibold">{app.company_name}</p>
                        <span
                          className={`text-xs px-2 py-0.5 rounded-full ${app.status === 'pending' ? 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300' : app.status === 'approved' ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300' : 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300'}`}
                        >
                          {app.status}
                        </span>
                      </div>
                      <p className="text-aegisaxiom-text-secondary text-sm break-words">
                        {app.contact_name} ·{' '}
                        <span className="text-violet-600 dark:text-violet-400">{app.email}</span>
                      </p>
                      {app.phone && (
                        <p className="text-aegisaxiom-text-muted text-xs mt-0.5">{app.phone}</p>
                      )}
                      {app.website && (
                        <a
                          href={app.website}
                          target="_blank"
                          rel="noreferrer"
                          className="text-violet-600 dark:text-violet-400 text-xs hover:underline break-all"
                        >
                          {app.website}
                        </a>
                      )}
                      {app.business_description && (
                        <p className="text-aegisaxiom-text-muted text-xs mt-2 max-w-xl leading-relaxed">
                          {app.business_description}
                        </p>
                      )}
                      {app.services_interested?.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-2">
                          {app.services_interested.map((s) => (
                            <span
                              key={s}
                              className="text-xs px-2 py-0.5 bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border rounded-full"
                            >
                              {s}
                            </span>
                          ))}
                        </div>
                      )}
                      <p className="text-aegisaxiom-text-muted text-xs mt-2">
                        Applied: {new Date(app.applied_at).toLocaleDateString()}
                      </p>
                      {app.notes && (
                        <p className="text-aegisaxiom-text-muted text-xs mt-1 italic">
                          Note: {app.notes}
                        </p>
                      )}
                    </div>
                    {app.status === 'pending' && (
                      <div className="flex items-center gap-2 shrink-0 w-full sm:w-auto">
                        <button
                          onClick={() => reviewApp(app.application_id, 'approve')}
                          className="text-xs px-3 py-1.5 bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 rounded-lg hover:bg-emerald-100 dark:hover:bg-emerald-600/30 transition-colors font-medium flex-1 sm:flex-none"
                          data-testid={`approve-app-${app.application_id}`}
                        >
                          Approve
                        </button>
                        <button
                          onClick={() =>
                            reviewApp(app.application_id, 'reject', 'Not a match at this time.')
                          }
                          className="text-xs px-3 py-1.5 bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300 rounded-lg hover:bg-red-100 dark:hover:bg-red-600/30 transition-colors font-medium flex-1 sm:flex-none"
                          data-testid={`reject-app-${app.application_id}`}
                        >
                          Reject
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {/* --- RESOURCE TYPES MODAL --- */}
      {modal === 'resource-types' && selectedUser && (
        <div
          className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-4"
          data-testid="resource-types-modal"
        >
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-2xl p-6 animate-fadeInUp max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-5">
              <div>
                <h2
                  className="text-lg font-bold text-aegisaxiom-text flex items-center gap-2"
                  style={{ fontFamily: 'Manrope, sans-serif' }}
                >
                  <Briefcase size={20} className="text-violet-600 dark:text-violet-400" />
                  Resource Type Assignments
                </h2>
                <p className="text-aegisaxiom-text-secondary text-xs mt-1">
                  {selectedUser.name} <span className="text-aegisaxiom-text-muted">·</span>{' '}
                  {selectedUser.email}
                </p>
              </div>
              <button onClick={() => setModal(null)}>
                <X size={18} className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text-secondary" />
              </button>
            </div>

            {/* Info Banner */}
            <div className="bg-violet-50 dark:bg-violet-600/10 border border-violet-200 dark:border-violet-600/20 rounded-lg p-3 mb-5 flex items-start gap-2">
              <Info size={14} className="text-violet-600 dark:text-violet-400 shrink-0 mt-0.5" />
              <p className="text-xs text-aegisaxiom-text-secondary leading-relaxed">
                Resource types define what roles a user can perform and their associated cost rate.
                These will be used when creating contracts to filter available users and
                auto-populate rates.
              </p>
            </div>

            {/* Add New Resource Type Form */}
            <form
              onSubmit={addResourceType}
              className="bg-aegisaxiom-accent-muted rounded-lg p-4 mb-5 border border-aegisaxiom-border"
            >
              <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3 flex items-center gap-2">
                <Plus size={14} />
                Assign New Resource Type
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                    Resource Type *
                  </label>
                  <select
                    className="input-field text-sm"
                    value={resourceForm.resource_type_id}
                    onChange={(e) =>
                      setResourceForm({ ...resourceForm, resource_type_id: e.target.value })
                    }
                    required
                    data-testid="resource-type-select"
                  >
                    <option value="">Select resource type...</option>
                    {resourceTypes
                      .filter(
                        (rt) => !userResourceTypes.find((urt) => urt.resource_type_id === rt.id),
                      )
                      .map((rt) => (
                        <option key={rt.id} value={rt.id}>
                          {rt.name}
                        </option>
                      ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                    Cost Rate ($/hr) *
                  </label>
                  <div className="relative">
                    <DollarSign
                      size={14}
                      className="absolute left-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted"
                    />
                    <input
                      type="number"
                      step="0.01"
                      min="0"
                      className="input-field text-sm pl-9"
                      value={resourceForm.cost_rate}
                      onChange={(e) =>
                        setResourceForm({ ...resourceForm, cost_rate: e.target.value })
                      }
                      placeholder="0.00"
                      required
                      data-testid="cost-rate-input"
                    />
                  </div>
                </div>
              </div>
              <button
                type="submit"
                disabled={loading || !resourceForm.resource_type_id || !resourceForm.cost_rate}
                className="btn-primary mt-3 flex items-center gap-2 text-xs"
                data-testid="add-resource-type-btn"
              >
                <Plus size={14} />
                {loading ? 'Adding...' : 'Add Resource Type'}
              </button>
            </form>

            {/* Current Assignments */}
            <div>
              <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3">
                Current Assignments ({userResourceTypes.length})
              </h3>
              {userResourceTypes.length === 0 ? (
                <div className="bg-aegisaxiom-accent-muted rounded-lg p-8 text-center">
                  <Briefcase size={24} className="text-aegisaxiom-text-muted mx-auto mb-2" />
                  <p className="text-aegisaxiom-text-muted text-sm">No resource types assigned yet</p>
                  <p className="text-aegisaxiom-text-muted text-xs mt-1">Add one above to get started</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {userResourceTypes.map((assignment) => (
                    <div
                      key={assignment.resource_type_id}
                      className="bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg p-3 flex items-center justify-between hover:bg-aegisaxiom-elevated transition-colors"
                      data-testid={`resource-assignment-${assignment.resource_type_id}`}
                    >
                      <div className="flex-1">
                        <p className="text-aegisaxiom-text font-medium text-sm">
                          {assignment.resource_type_name || 'Unknown'}
                        </p>
                        <div className="flex items-center gap-3 mt-1">
                          <span className="text-xs text-aegisaxiom-text-secondary flex items-center gap-1">
                            <DollarSign size={12} />
                            <strong className="text-violet-600 dark:text-violet-400">
                              ${assignment.cost_rate?.toFixed(2)}
                            </strong>
                            /hr
                          </span>
                          {assignment.assigned_at && (
                            <span className="text-xs text-aegisaxiom-text-muted">
                              Assigned {new Date(assignment.assigned_at).toLocaleDateString()}
                            </span>
                          )}
                        </div>
                      </div>
                      <button
                        onClick={() => removeResourceType(assignment.resource_type_id)}
                        className="text-aegisaxiom-text-muted hover:text-red-600 dark:text-red-400 transition-colors p-2"
                        data-testid={`remove-resource-${assignment.resource_type_id}`}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="flex justify-end gap-3 mt-5 pt-5 border-t border-aegisaxiom-border">
              <button
                type="button"
                onClick={() => setModal(null)}
                className="btn-secondary text-sm"
                data-testid="close-resource-modal"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* --- CONTRACTS MODAL --- */}
      {modal === 'contracts' && selectedUser && (
        <div
          className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-4"
          data-testid="contracts-modal"
        >
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-3xl p-6 animate-fadeInUp max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-5">
              <div>
                <h2
                  className="text-lg font-bold text-aegisaxiom-text flex items-center gap-2"
                  style={{ fontFamily: 'Manrope, sans-serif' }}
                >
                  <FileText size={20} className="text-blue-600 dark:text-blue-400" />
                  Contract Assignments
                </h2>
                <p className="text-aegisaxiom-text-secondary text-xs mt-1">
                  {selectedUser.name} <span className="text-aegisaxiom-text-muted">·</span>{' '}
                  {selectedUser.email}
                </p>
              </div>
              <button onClick={() => setModal(null)}>
                <X size={18} className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text-secondary" />
              </button>
            </div>

            {/* Assign to New Contract */}
            <form
              onSubmit={assignToContract}
              className="bg-aegisaxiom-accent-muted rounded-lg p-4 mb-5 border border-aegisaxiom-border"
            >
              <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3 flex items-center gap-2">
                <Plus size={14} />
                Assign to Contract
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Contract *</label>
                  <select
                    className="input-field text-sm"
                    value={assignContractForm.contract_id}
                    onChange={(e) => {
                      setAssignContractForm({
                        ...assignContractForm,
                        contract_id: e.target.value,
                        resource_type_id: '',
                      });
                    }}
                    required
                    data-testid="contract-select"
                  >
                    <option value="">Select contract...</option>
                    {availableContracts.map((c) => (
                      <option key={c.contract_id} value={c.contract_id}>
                        {c.name} ({c.account_name})
                      </option>
                    ))}
                  </select>
                  {availableContracts.length === 0 && (
                    <p className="text-xs text-aegisaxiom-text-muted mt-1">
                      No active contracts with matching resource types
                    </p>
                  )}
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Assign As *</label>
                  <select
                    className="input-field text-sm"
                    value={assignContractForm.resource_type_id}
                    onChange={(e) =>
                      setAssignContractForm({
                        ...assignContractForm,
                        resource_type_id: e.target.value,
                      })
                    }
                    disabled={!assignContractForm.contract_id}
                    required
                    data-testid="resource-type-select-assign"
                  >
                    <option value="">Select resource type...</option>
                    {assignContractForm.contract_id &&
                      (() => {
                        const contract = availableContracts.find(
                          (c) => c.contract_id === assignContractForm.contract_id,
                        );
                        const contractorResourceTypes = (
                          selectedUser.resource_type_assignments || []
                        ).map((rt) => rt.resource_type_name);
                        return (contract?.resource_types || [])
                          .filter((rt) => contractorResourceTypes.includes(rt.name))
                          .map((rt) => (
                            <option key={rt.id} value={rt.id}>
                              {rt.name} (${rt.sell_rate}/hr client rate)
                            </option>
                          ));
                      })()}
                  </select>
                </div>
              </div>
              <button
                type="submit"
                disabled={
                  loading || !assignContractForm.contract_id || !assignContractForm.resource_type_id
                }
                className="btn-primary mt-3 flex items-center gap-2 text-xs"
                data-testid="assign-contract-btn"
              >
                <Plus size={14} />
                {loading ? 'Assigning...' : 'Assign to Contract'}
              </button>
            </form>

            {/* Current Assignments */}
            <div>
              <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3">
                Current Assignments ({contractorContracts.length})
              </h3>
              {contractorContracts.length === 0 ? (
                <div className="bg-aegisaxiom-accent-muted rounded-lg p-8 text-center">
                  <FileText size={24} className="text-aegisaxiom-text-muted mx-auto mb-2" />
                  <p className="text-aegisaxiom-text-muted text-sm">Not assigned to any contracts yet</p>
                  <p className="text-aegisaxiom-text-muted text-xs mt-1">
                    Assign them to a contract above
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  {contractorContracts.map((contract) => {
                    const resource = (contract.resources || []).find(
                      (r) => r.user_id === selectedUser.user_id,
                    );
                    return (
                      <div
                        key={contract.contract_id}
                        className="bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg p-3 hover:bg-aegisaxiom-elevated transition-colors"
                        data-testid={`contract-assignment-${contract.contract_id}`}
                      >
                        <div className="flex items-start justify-between">
                          <div className="flex-1">
                            <p className="text-aegisaxiom-text font-medium text-sm">{contract.name}</p>
                            <p className="text-xs text-aegisaxiom-text-muted mt-0.5">
                              {contract.account_name}
                            </p>
                            <div className="flex items-center gap-3 mt-2">
                              <span className="text-xs text-aegisaxiom-text-secondary flex items-center gap-1">
                                <Briefcase size={12} />
                                <strong className="text-violet-600 dark:text-violet-400">
                                  {resource?.resource_type_name || 'Unknown'}
                                </strong>
                              </span>
                              <span className="text-xs text-aegisaxiom-text-secondary flex items-center gap-1">
                                <DollarSign size={12} />
                                Cost:{' '}
                                <strong className="text-emerald-600 dark:text-emerald-400">
                                  ${resource?.cost_rate?.toFixed(2) || 0}/hr
                                </strong>
                              </span>
                              {resource?.sell_rate && (
                                <span className="text-xs text-aegisaxiom-text-muted">
                                  Client: ${resource.sell_rate}/hr
                                </span>
                              )}
                            </div>
                          </div>
                          <button
                            onClick={() => removeFromContract(contract)}
                            className="text-aegisaxiom-text-muted hover:text-red-600 dark:text-red-400 transition-colors p-2"
                            data-testid={`remove-contract-${contract.contract_id}`}
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            <div className="flex justify-end gap-3 mt-5 pt-5 border-t border-aegisaxiom-border">
              <button
                type="button"
                onClick={() => setModal(null)}
                className="btn-secondary text-sm"
                data-testid="close-contracts-modal"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* --- INVITE MODAL --- */}
      {modal === 'invite' && (
        <div
          className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-4"
          data-testid="invite-modal"
        >
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-md p-6 animate-fadeInUp">
            <div className="flex items-center justify-between mb-5">
              <h2
                className="text-lg font-bold text-aegisaxiom-text"
                style={{ fontFamily: 'Manrope, sans-serif' }}
              >
                Invite User
              </h2>
              <button onClick={() => setModal(null)}>
                <X size={18} className="text-aegisaxiom-text-secondary" />
              </button>
            </div>
            <form onSubmit={sendInvite} className="space-y-3">
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Full Name *</label>
                <input
                  className="input-field"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  required
                  data-testid="invite-name-input"
                />
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Email *</label>
                <input
                  type="email"
                  className="input-field"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  required
                  data-testid="invite-email-input"
                />
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Role</label>
                <select
                  className="input-field"
                  value={form.role}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      role: e.target.value,
                      account_id: ['agent', 'customer'].includes(e.target.value)
                        ? form.account_id
                        : '',
                      resource_type_assignments:
                        e.target.value === 'contractor' ? form.resource_type_assignments || [] : [],
                    })
                  }
                  data-testid="invite-role-select"
                >
                  {roles.map((r) => (
                    <option key={r.name} value={r.name}>
                      {r.display_name}
                    </option>
                  ))}
                </select>
              </div>
              {['agent', 'customer'].includes(form.role) && (
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                    Account <span className="text-aegisaxiom-text-muted">(partner company)</span>
                  </label>
                  <AccountSelect
                    accounts={accounts}
                    value={form.account_id}
                    onChange={(val) => setForm({ ...form, account_id: val })}
                    placeholder="Search and select account..."
                  />
                </div>
              )}
              {form.role === 'contractor' && (
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
                    Resource Types & Cost Rates
                  </label>
                  <div className="space-y-2">
                    {(form.resource_type_assignments || []).map((rta, idx) => (
                      <div key={idx} className="flex items-center gap-2">
                        <select
                          className="input-field flex-1 text-xs"
                          value={rta.resource_type_name}
                          onChange={(e) => {
                            const updated = [...form.resource_type_assignments];
                            const selectedType = resourceTypes.find(
                              (rt) => rt.name === e.target.value,
                            );
                            updated[idx] = {
                              ...updated[idx],
                              resource_type_name: e.target.value,
                              resource_type_id: e.target.value,
                              sell_rate: selectedType?.sell_rate || 0,
                            };
                            setForm({ ...form, resource_type_assignments: updated });
                          }}
                        >
                          <option value="">Select resource type...</option>
                          {resourceTypes
                            .filter(
                              (rt) =>
                                !form.resource_type_assignments.some(
                                  (a, i) => i !== idx && a.resource_type_name === rt.name,
                                ),
                            )
                            .map((rt) => (
                              <option key={rt.name} value={rt.name}>
                                {rt.name} (${rt.sell_rate}/hr)
                              </option>
                            ))}
                        </select>
                        <div className="flex items-center gap-1">
                          <span className="text-xs text-aegisaxiom-text-muted">$</span>
                          <input
                            type="number"
                            min="0"
                            step="0.01"
                            placeholder="Cost/hr"
                            value={rta.cost_rate}
                            onChange={(e) => {
                              const updated = [...form.resource_type_assignments];
                              updated[idx] = { ...updated[idx], cost_rate: e.target.value };
                              setForm({ ...form, resource_type_assignments: updated });
                            }}
                            className="w-24 px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none"
                          />
                          <span className="text-xs text-aegisaxiom-text-muted">/hr</span>
                        </div>
                        <button
                          type="button"
                          onClick={() => {
                            const updated = form.resource_type_assignments.filter(
                              (_, i) => i !== idx,
                            );
                            setForm({ ...form, resource_type_assignments: updated });
                          }}
                          className="text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300 p-1"
                        >
                          <X size={14} />
                        </button>
                      </div>
                    ))}
                    <button
                      type="button"
                      onClick={() =>
                        setForm({
                          ...form,
                          resource_type_assignments: [
                            ...(form.resource_type_assignments || []),
                            {
                              resource_type_name: '',
                              resource_type_id: '',
                              cost_rate: '',
                              sell_rate: 0,
                            },
                          ],
                        })
                      }
                      className="text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 flex items-center gap-1"
                    >
                      <Plus size={12} /> Add Resource Type
                    </button>
                  </div>
                  {(form.resource_type_assignments || []).length > 0 &&
                    form.resource_type_assignments.some(
                      (a) => a.resource_type_name && a.cost_rate,
                    ) && (
                      <div className="mt-2 p-2 bg-aegisaxiom-elevated rounded border border-aegisaxiom-border">
                        <p className="text-[10px] text-aegisaxiom-text-muted mb-1">Summary</p>
                        {form.resource_type_assignments
                          .filter((a) => a.resource_type_name && a.cost_rate)
                          .map((a, i) => (
                            <div key={i} className="flex justify-between text-xs">
                              <span className="text-aegisaxiom-text-secondary">
                                {a.resource_type_name}
                              </span>
                              <span className="text-aegisaxiom-text-secondary">
                                Cost: ${parseFloat(a.cost_rate).toFixed(2)}/hr
                                {a.sell_rate > 0 && (
                                  <span className="text-emerald-600 dark:text-emerald-400 ml-2">
                                    Sell: ${a.sell_rate}/hr
                                  </span>
                                )}
                              </span>
                            </div>
                          ))}
                      </div>
                    )}
                </div>
              )}
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
                  className="btn-primary flex-1 flex items-center justify-center gap-2"
                  data-testid="send-invite-btn"
                >
                  <Mail size={14} />
                  {loading ? 'Sending...' : 'Send Invite'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
      {/* --- PARTNERSHIP EDIT MODAL --- */}
      {partnerEditModal && (
        <div
          className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-4"
          data-testid="partner-edit-modal"
        >
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-md p-6 animate-fadeInUp">
            <div className="flex items-center justify-between mb-5">
              <div>
                <h2
                  className="text-lg font-bold text-aegisaxiom-text flex items-center gap-2"
                  style={{ fontFamily: 'Manrope, sans-serif' }}
                >
                  <Star size={18} className="text-yellow-500 fill-yellow-400" />
                  Edit Partnership
                </h2>
                <p className="text-aegisaxiom-text-secondary text-xs mt-1">{partnerEditModal.name}</p>
              </div>
              <button onClick={() => setPartnerEditModal(null)} aria-label="Close">
                <X size={18} className="text-aegisaxiom-text-secondary" />
              </button>
            </div>
            <form onSubmit={savePartner} className="space-y-3">
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                  Signed Partnership Status
                </label>
                <select
                  className="input-field"
                  value={partnerForm.partnership_status}
                  onChange={(e) =>
                    setPartnerForm((f) => ({ ...f, partnership_status: e.target.value }))
                  }
                  data-testid="partnership-status-select"
                >
                  {PARTNERSHIP_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {PARTNERSHIP_STATUS_CONFIG[s].label}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                  <Calendar size={12} className="inline mr-1 -mt-0.5" />
                  Signed Date
                </label>
                <input
                  type="date"
                  className="input-field"
                  value={partnerForm.partnership_signed_date}
                  onChange={(e) =>
                    setPartnerForm((f) => ({ ...f, partnership_signed_date: e.target.value }))
                  }
                  data-testid="partnership-signed-date-input"
                />
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                  <Percent size={12} className="inline mr-1 -mt-0.5" />
                  Commission Rate (%)
                </label>
                <input
                  type="number"
                  min="0"
                  max="100"
                  step="0.1"
                  className="input-field"
                  placeholder="e.g. 7.5"
                  value={partnerForm.commission_rate}
                  onChange={(e) =>
                    setPartnerForm((f) => ({ ...f, commission_rate: e.target.value }))
                  }
                  data-testid="commission-rate-input"
                />
                <p className="text-xs text-aegisaxiom-text-muted mt-1">
                  Percentage (0–100). Leave blank if not yet negotiated.
                </p>
              </div>
              <div className="flex gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setPartnerEditModal(null)}
                  className="btn-secondary flex-1"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={savingPartner}
                  className="btn-primary flex-1"
                  data-testid="save-partner-btn"
                >
                  {savingPartner ? 'Saving...' : 'Save'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <ConfirmModal
        isOpen={confirmState.open}
        title={confirmState.title}
        message={confirmState.message}
        confirmText="Delete"
        onConfirm={() => confirmState.action?.()}
        onCancel={() => setConfirmState({ open: false, action: null, title: '', message: '' })}
      />

      {/* --- AI USAGE VIEWABLE USERS MODAL --- */}
      {viewableUsersModal && (
        <div
          className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-4"
          data-testid="ai-usage-viewable-modal"
        >
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-lg p-6 animate-fadeInUp max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-5">
              <div>
                <h2
                  className="text-lg font-bold text-aegisaxiom-text flex items-center gap-2"
                  style={{ fontFamily: 'Manrope, sans-serif' }}
                >
                  <Eye size={20} className="text-violet-600 dark:text-violet-400" />
                  AI Usage Viewable Users
                </h2>
                <p className="text-aegisaxiom-text-secondary text-xs mt-1">
                  {viewableUsersModal.name} <span className="text-aegisaxiom-text-muted">&middot;</span>{' '}
                  {viewableUsersModal.email}
                </p>
              </div>
              <button onClick={() => setViewableUsersModal(null)}>
                <X size={18} className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text-secondary" />
              </button>
            </div>

            <div className="bg-violet-50 dark:bg-violet-600/10 border border-violet-200 dark:border-violet-600/20 rounded-lg p-3 mb-5 flex items-start gap-2">
              <Info size={14} className="text-violet-600 dark:text-violet-400 shrink-0 mt-0.5" />
              <p className="text-xs text-aegisaxiom-text-secondary leading-relaxed">
                Select which users this person can view AI usage data for on the Usage Dashboard.
                They will always see their own usage in addition to the selected users.
              </p>
            </div>

            {/* Search */}
            <div className="relative mb-3">
              <Search
                size={13}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted"
              />
              <input
                type="text"
                placeholder="Search users..."
                value={viewableSearch}
                onChange={(e) => setViewableSearch(e.target.value)}
                className="input-field text-xs w-full pl-8"
              />
            </div>

            {/* User list */}
            <div className="space-y-1 max-h-64 overflow-y-auto mb-5">
              {[...users, ...contractors]
                .filter((u) => u.user_id !== viewableUsersModal.user_id)
                .filter(
                  (u) =>
                    !viewableSearch ||
                    u.name?.toLowerCase().includes(viewableSearch.toLowerCase()) ||
                    u.email?.toLowerCase().includes(viewableSearch.toLowerCase()),
                )
                .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
                .map((u) => (
                  <label
                    key={u.user_id}
                    className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-aegisaxiom-elevated cursor-pointer transition-colors"
                  >
                    <input
                      type="checkbox"
                      checked={viewableUserIds.includes(u.user_id)}
                      onChange={() => toggleViewableUser(u.user_id)}
                      className="rounded border-aegisaxiom-border-hover text-violet-500 focus:ring-violet-500 focus:ring-offset-0 bg-aegisaxiom-elevated"
                    />
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-aegisaxiom-text truncate">{u.name || 'Unnamed'}</p>
                      <p className="text-[11px] text-aegisaxiom-text-muted truncate">{u.email}</p>
                    </div>
                    <span
                      className={`text-[11px] px-2 py-0.5 rounded-full shrink-0 ${ROLE_COLORS[u.role] || 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary'}`}
                    >
                      {u.role}
                    </span>
                  </label>
                ))}
            </div>

            <div className="flex items-center justify-between border-t border-aegisaxiom-border pt-4">
              <span className="text-xs text-aegisaxiom-text-muted">
                {viewableUserIds.length} user{viewableUserIds.length !== 1 ? 's' : ''} selected
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setViewableUsersModal(null)}
                  className="btn-secondary text-xs"
                >
                  Cancel
                </button>
                <button
                  onClick={saveViewableUsers}
                  className="btn-primary text-xs flex items-center gap-1.5"
                >
                  <Eye size={12} />
                  Save
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* --- AI EMPLOYEE ACCESS MODAL --- */}
      {aiAccessUser && (
        <div
          className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-4"
          data-testid="ai-employee-access-modal"
        >
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-lg p-6 animate-fadeInUp max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-5">
              <div>
                <h2
                  className="text-lg font-bold text-aegisaxiom-text flex items-center gap-2"
                  style={{ fontFamily: 'Manrope, sans-serif' }}
                >
                  <Bot size={20} className="text-violet-600 dark:text-violet-400" />
                  AI Employee Access
                </h2>
                <p className="text-aegisaxiom-text-secondary text-xs mt-1">
                  {aiAccessUser.name} <span className="text-aegisaxiom-text-muted">&middot;</span>{' '}
                  {aiAccessUser.email}
                </p>
              </div>
              <button onClick={() => setAiAccessUser(null)}>
                <X size={18} className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text" />
              </button>
            </div>

            <div className="bg-violet-50 dark:bg-violet-600/10 border border-violet-200 dark:border-violet-600/20 rounded-lg p-3 mb-5 flex items-start gap-2">
              <Info size={14} className="text-violet-600 dark:text-violet-400 shrink-0 mt-0.5" />
              <p className="text-xs text-aegisaxiom-text-secondary leading-relaxed">
                Toggle individual access overrides for this user. Role-based access is managed from
                the <strong className="text-aegisaxiom-text-secondary">AI Employees</strong> page.
              </p>
            </div>

            {aiAccessLoading ? (
              <div className="flex items-center justify-center py-10">
                <RefreshCw size={18} className="text-violet-500 animate-spin" />
              </div>
            ) : aiEmployees.length === 0 ? (
              <p className="text-sm text-aegisaxiom-text-muted text-center py-8">
                No AI employees found.
              </p>
            ) : (
              <div className="space-y-2">
                {aiEmployees.map((emp) => {
                  const empId = emp.employee_id || emp.id;
                  const access = aiEmployeeAccess[empId] || {
                    allowed_roles: [],
                    allowed_user_ids: [],
                  };
                  const hasOverride = access.allowed_user_ids.includes(aiAccessUser.user_id);
                  const isSaving = aiAccessSaving === empId;
                  return (
                    <label
                      key={empId}
                      className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-aegisaxiom-elevated cursor-pointer transition-colors"
                    >
                      <input
                        type="checkbox"
                        checked={hasOverride}
                        disabled={isSaving}
                        onChange={() => toggleAiEmployeeAccess(empId, aiAccessUser.user_id)}
                        className="rounded border-aegisaxiom-border-hover text-violet-500 focus:ring-violet-500 focus:ring-offset-0 bg-aegisaxiom-elevated disabled:opacity-50"
                      />
                      <span className="text-xl shrink-0" aria-hidden="true">
                        {emp.avatar || '🤖'}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="text-xs text-aegisaxiom-text font-medium truncate">{emp.name}</p>
                        <p className="text-[11px] text-aegisaxiom-text-muted truncate">{emp.role}</p>
                      </div>
                      {isSaving && (
                        <RefreshCw size={12} className="text-violet-500 animate-spin shrink-0" />
                      )}
                    </label>
                  );
                })}
              </div>
            )}

            <div className="flex justify-end border-t border-aegisaxiom-border pt-4 mt-5">
              <button onClick={() => setAiAccessUser(null)} className="btn-secondary text-xs">
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
