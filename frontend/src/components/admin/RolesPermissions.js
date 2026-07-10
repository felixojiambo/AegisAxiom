import React, { useEffect, useState, useMemo } from 'react';
import { api } from '@/lib/api';
import {
  Plus,
  Shield,
  Trash2,
  Copy,
  Save,
  X,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Lock,
  Check,
  Search,
  Eye,
  EyeOff,
  Pencil,
} from 'lucide-react';
import { toast } from 'sonner';
import API_URL from '../../lib/apiConfig';
import ConfirmModal from '../shared/ConfirmModal';
import RolePreviewModal from './RolePreviewModal';

const API = API_URL;
const ROLE_COLORS = [
  '#7C3AED',
  '#2563EB',
  '#059669',
  '#C026D3',
  '#D97706',
  '#DC2626',
  '#0891B2',
  '#64748B',
  '#0EA5E9',
];

// Parent category → sub-groups mapping
const CATEGORY_ORDER = ['Sales', 'Finance', 'Operations', 'AI', 'Dashboards', 'Admin', 'Partners'];
const CATEGORY_MAP = {
  Opportunities: 'Sales',
  Contacts: 'Sales',
  Accounts: 'Sales',
  Deals: 'Sales',
  'Products & Services': 'Sales',
  'Sales Ops': 'Sales',
  Finance: 'Finance',
  'Finance - AP': 'Finance',
  'Finance - AR': 'Finance',
  'Finance - Past Due': 'Finance',
  'Finance - Referral MRR': 'Finance',
  Commissions: 'Finance',
  Invoices: 'Finance',
  Payments: 'Finance',
  Contracts: 'Operations',
  Projects: 'Operations',
  'Field Jobs': 'Operations',
  'Time Mgmt': 'Operations',
  'Admin - Contractors': 'Operations',
  'Admin - Resources': 'Operations',
  'AI Chat': 'AI',
  'AI Usage': 'AI',
  'AI Employees': 'AI',
  Certifications: 'AI',
  'Workflow Rules': 'AI',
  Dashboards: 'Dashboards',
  'Custom Dashboards': 'Dashboards',
  'User Mgmt': 'Admin',
  Roles: 'Admin',
  Integrations: 'Admin',
  Reports: 'Admin',
  Audit: 'Admin',
  'Policy Engine': 'Admin',
  Partners: 'Partners',
};

// ---------------------------------------------------------------------------
// Implied-permission logic (mirrors backend static_implications convention)
// Verb ladder: delete > edit > view   Scope ladder: all > own
// ---------------------------------------------------------------------------
const VERB_LADDER = ['delete', 'edit', 'view'];
const SCOPE_LADDER = ['all', 'own'];
const SKIP_VERBS = new Set(['create', 'change_status', 'assign', 'access']);

function getImpliedByKey(key) {
  // key shape examples:
  //   crm.contacts.delete.all  → prefix=crm.contacts verb=delete scope=all
  //   reports.edit             → prefix=reports verb=edit scope=null
  const parts = key.split('.');
  if (parts.length < 2) return [];

  const last = parts[parts.length - 1];
  const secondLast = parts[parts.length - 2];

  let prefix, verb, scope;

  if (SCOPE_LADDER.includes(last) && VERB_LADDER.includes(secondLast)) {
    // e.g. crm.contacts.delete.all
    scope = last;
    verb = secondLast;
    prefix = parts.slice(0, parts.length - 2).join('.');
  } else if (VERB_LADDER.includes(last)) {
    // e.g. reports.edit
    scope = null;
    verb = last;
    prefix = parts.slice(0, parts.length - 1).join('.');
  } else {
    return [];
  }

  if (SKIP_VERBS.has(verb)) return [];

  const implied = [];
  const verbIdx = VERB_LADDER.indexOf(verb);
  const scopeIdx = scope ? SCOPE_LADDER.indexOf(scope) : -1;

  // All lower verbs (higher index = lower tier)
  for (let vi = verbIdx + 1; vi < VERB_LADDER.length; vi++) {
    const lowerVerb = VERB_LADDER[vi];
    if (scope !== null) {
      // All scope combinations at or below the current scope
      for (let si = scopeIdx; si < SCOPE_LADDER.length; si++) {
        implied.push(`${prefix}.${lowerVerb}.${SCOPE_LADDER[si]}`);
      }
    } else {
      implied.push(`${prefix}.${lowerVerb}`);
    }
  }

  // Same verb, lower scope (e.g. delete.all implies delete.own)
  if (scope !== null) {
    for (let si = scopeIdx + 1; si < SCOPE_LADDER.length; si++) {
      implied.push(`${prefix}.${verb}.${SCOPE_LADDER[si]}`);
    }
  }

  return implied;
}

/**
 * Given a set of explicitly-checked keys, return the full set of keys that
 * are implied (but not already explicit). The return value does NOT include
 * the explicitly-checked keys themselves.
 */
function computeImpliedKeys(checkedKeys) {
  const implied = new Set();
  for (const key of checkedKeys) {
    for (const imp of getImpliedByKey(key)) {
      if (!checkedKeys.has(imp)) implied.add(imp);
    }
  }
  return implied;
}

/**
 * For a given implied key, find which explicitly-checked key implies it
 * (first match wins — used for tooltip text).
 */
function findImpliedBy(key, checkedKeys) {
  for (const checked of checkedKeys) {
    if (getImpliedByKey(checked).includes(key)) return checked;
  }
  return null;
}

// ---------------------------------------------------------------------------

function PermissionToggle({ perm, checked, onChange, disabled, implied, impliedBy }) {
  const isImplied = implied && !checked;
  const effectiveChecked = checked || isImplied;
  const tooltipTitle = isImplied
    ? `Implied by ${impliedBy}. Uncheck the higher perm to remove.`
    : undefined;

  return (
    <button
      onClick={() => !disabled && !isImplied && onChange(perm.key, !checked)}
      disabled={disabled || isImplied}
      title={tooltipTitle}
      data-testid={`perm-toggle-${perm.key}`}
      className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs transition-all border w-full text-left
        ${disabled || isImplied ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer'}
        ${
          effectiveChecked
            ? 'bg-violet-50 dark:bg-violet-600/20 border-violet-500/50 text-violet-700 dark:text-violet-300'
            : 'bg-aegisaxiom-surface border-aegisaxiom-border text-aegisaxiom-text-muted hover:border-aegisaxiom-border hover:text-aegisaxiom-text-secondary'
        }`}
    >
      <div
        className={`w-4 h-4 rounded flex items-center justify-center shrink-0 border transition-all
        ${effectiveChecked ? 'bg-violet-600 border-violet-500' : 'border-aegisaxiom-border-hover'}`}
      >
        {effectiveChecked && <Check size={10} className="text-aegisaxiom-text" />}
      </div>
      <span className="flex-1">
        {perm.label}
        {isImplied && <em className="text-violet-500/70 dark:text-violet-400/60"> (implied)</em>}
      </span>
    </button>
  );
}

function ColorPicker({ value, onChange }) {
  return (
    <div className="flex gap-2 flex-wrap">
      {ROLE_COLORS.map((c) => (
        <button
          key={c}
          onClick={() => onChange(c)}
          className={`w-6 h-6 rounded-full border-2 transition-all ${value === c ? 'border-white scale-110' : 'border-transparent'}`}
          style={{ background: c }}
        />
      ))}
    </div>
  );
}

// Reusable permission editor with search, accordion, and category hierarchy
function PermissionEditor({
  allPerms,
  grouped,
  permissions,
  onTogglePerm,
  onToggleGroup,
  searchQuery,
  setSearchQuery,
  expandedGroups,
  setExpandedGroups,
  showEnabledOnly,
  setShowEnabledOnly,
  // 3A: handlers for editing ingestion-app perms (optional)
  onAddIngestionPerm,
  onRemoveIngestionPerm,
}) {
  // Compute implied keys from the current explicit permission set (3E)
  const checkedSet = useMemo(() => new Set(permissions), [permissions]);
  const impliedSet = useMemo(() => computeImpliedKeys(checkedSet), [checkedSet]);

  // Build category → groups structure
  const categories = useMemo(() => {
    const cats = {};
    for (const group of Object.keys(grouped)) {
      const cat = CATEGORY_MAP[group] || 'Other';
      if (!cats[cat]) cats[cat] = [];
      cats[cat].push(group);
    }
    // Sort by CATEGORY_ORDER
    const ordered = {};
    for (const cat of CATEGORY_ORDER) {
      if (cats[cat]) ordered[cat] = cats[cat];
    }
    // Add any uncategorized
    for (const cat of Object.keys(cats)) {
      if (!ordered[cat]) ordered[cat] = cats[cat];
    }
    return ordered;
  }, [grouped]);

  // Filter by search
  const matchesSearch = (perm) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return perm.label.toLowerCase().includes(q) || perm.key.toLowerCase().includes(q);
  };

  const toggleGroupExpand = (group) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });
  };

  const toggleCategoryExpand = (cat) => {
    const groups = categories[cat] || [];
    const allExpanded = groups.every((g) => expandedGroups.has(g));
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      groups.forEach((g) => (allExpanded ? next.delete(g) : next.add(g)));
      return next;
    });
  };

  const expandEnabledOnly = () => {
    const next = new Set();
    for (const [group, perms] of Object.entries(grouped)) {
      if (perms.some((p) => permissions.includes(p.key))) next.add(group);
    }
    setExpandedGroups(next);
    setShowEnabledOnly(true);
  };

  const expandAll = () => {
    setExpandedGroups(new Set(Object.keys(grouped)));
    setShowEnabledOnly(false);
  };

  const collapseAll = () => {
    setExpandedGroups(new Set());
    setShowEnabledOnly(false);
  };

  return (
    <div>
      {/* Sticky search + controls */}
      <div className="sticky top-0 z-10 bg-aegisaxiom-surface backdrop-blur-sm pb-3 -mx-1 px-1">
        <div className="flex items-center gap-2 mb-2">
          <div className="relative flex-1">
            <Search
              size={13}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted"
            />
            <input
              type="text"
              placeholder="Search permissions..."
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                if (e.target.value) setExpandedGroups(new Set(Object.keys(grouped)));
              }}
              className="input-field text-xs w-full pl-8"
            />
          </div>
          <button
            onClick={expandEnabledOnly}
            title="Show enabled only"
            className={`p-2 rounded-lg border text-xs transition-all ${showEnabledOnly ? 'bg-violet-50 dark:bg-violet-600/20 border-violet-500/50 text-violet-700 dark:text-violet-300' : 'border-aegisaxiom-border text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary'}`}
          >
            <Eye size={14} />
          </button>
          <button
            onClick={expandAll}
            className="text-xs text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary px-2 py-1.5"
          >
            Expand all
          </button>
          <button
            onClick={collapseAll}
            className="text-xs text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary px-2 py-1.5"
          >
            Collapse
          </button>
        </div>
      </div>

      {/* Category → Group → Permissions */}
      <div className="space-y-4 max-h-[52vh] overflow-y-auto pr-1">
        {Object.entries(categories).map(([cat, groups]) => {
          // Filter groups that have matching permissions
          const visibleGroups = groups.filter((group) => {
            const perms = grouped[group] || [];
            return perms.some(matchesSearch);
          });
          if (visibleGroups.length === 0) return null;

          // Category-level counts — include implied in resolved count (3E)
          const catPerms = visibleGroups.flatMap((g) => grouped[g] || []);
          const catEnabled = catPerms.filter(
            (p) => checkedSet.has(p.key) || impliedSet.has(p.key),
          ).length;
          const catTotal = catPerms.length;
          const catAllExpanded = visibleGroups.every((g) => expandedGroups.has(g));

          return (
            <div key={cat}>
              {/* Category header */}
              <button
                onClick={() => toggleCategoryExpand(cat)}
                className="flex items-center gap-2 w-full text-left mb-2 group"
              >
                <ChevronRight
                  size={14}
                  className={`text-aegisaxiom-text-muted transition-transform ${catAllExpanded ? 'rotate-90' : ''}`}
                />
                <span className="text-sm font-bold text-aegisaxiom-text-secondary uppercase tracking-wider">
                  {cat}
                </span>
                <span
                  className={`text-[11px] px-1.5 py-0.5 rounded-full ml-1 ${catEnabled > 0 ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300' : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-muted'}`}
                >
                  {catEnabled}/{catTotal}
                </span>
              </button>

              {/* Sub-groups */}
              <div className="ml-3 space-y-1">
                {visibleGroups.map((group) => {
                  const perms = (grouped[group] || []).filter(matchesSearch);
                  if (perms.length === 0) return null;

                  const groupKeys = perms.map((p) => p.key);
                  // Resolved count includes implied perms (3E)
                  const enabledCount = groupKeys.filter(
                    (k) => checkedSet.has(k) || impliedSet.has(k),
                  ).length;
                  const allOn = groupKeys.every((k) => checkedSet.has(k));
                  const isExpanded = expandedGroups.has(group);
                  // For 3A: detect if this group belongs to an ingested app
                  const groupPerms = grouped[group] || [];
                  const groupAppId = groupPerms[0]?.app_id ?? null;
                  const isIngestionGroup = groupPerms.some(
                    (p) => p.source === 'ingestion' || p.source === 'manual_override',
                  );

                  return (
                    <div
                      key={group}
                      className="border border-aegisaxiom-border rounded-lg overflow-hidden"
                    >
                      {/* Group header */}
                      <div
                        className="flex items-center justify-between px-3 py-2 bg-aegisaxiom-surface cursor-pointer hover:bg-aegisaxiom-accent-muted transition-colors"
                        onClick={() => toggleGroupExpand(group)}
                      >
                        <div className="flex items-center gap-2">
                          <ChevronRight
                            size={12}
                            className={`text-aegisaxiom-text-muted transition-transform ${isExpanded ? 'rotate-90' : ''}`}
                          />
                          <span className="text-xs font-semibold text-aegisaxiom-text-secondary">
                            {group}
                          </span>
                          <span
                            className={`text-[10px] px-1.5 py-0.5 rounded-full ${enabledCount > 0 ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300' : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-muted'}`}
                          >
                            {enabledCount}/{perms.length}
                          </span>
                          {/* 3A: pencil icon for ingestion groups */}
                          {isIngestionGroup && onAddIngestionPerm && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                onAddIngestionPerm(groupAppId, group);
                              }}
                              title="Edit permissions for this app"
                              className="p-0.5 rounded text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 transition-colors"
                              aria-label={`Edit permissions for ${group}`}
                            >
                              <Pencil size={11} />
                            </button>
                          )}
                        </div>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            onToggleGroup(group, !allOn);
                          }}
                          className={`text-[11px] px-2 py-0.5 rounded transition-colors ${allOn ? 'text-violet-700 dark:text-violet-300 hover:text-aegisaxiom-text-secondary' : 'text-aegisaxiom-text-muted hover:text-violet-700 dark:text-violet-300'}`}
                          data-testid={`toggle-group-${group}`}
                        >
                          {allOn ? 'Revoke all' : 'Grant all'}
                        </button>
                      </div>
                      {/* Permissions (collapsible) */}
                      {isExpanded && (
                        <div className="grid grid-cols-2 gap-1.5 p-2 bg-aegisaxiom-base/30">
                          {perms.map((perm) => {
                            const isImplied = impliedSet.has(perm.key);
                            const impliedBy = isImplied
                              ? findImpliedBy(perm.key, checkedSet)
                              : null;
                            return (
                              <div key={perm.key} className="relative group">
                                <PermissionToggle
                                  perm={perm}
                                  checked={checkedSet.has(perm.key)}
                                  onChange={onTogglePerm}
                                  implied={isImplied}
                                  impliedBy={impliedBy}
                                />
                                {/* 3A: manual_override badge */}
                                {perm.source === 'manual_override' && (
                                  <span className="absolute top-1 right-1 text-[9px] px-1 rounded bg-amber-100 dark:bg-amber-800/40 text-amber-700 dark:text-amber-300 border border-amber-300 dark:border-amber-600/40 leading-tight pointer-events-none">
                                    manual
                                  </span>
                                )}
                                {/* 3A: remove button for ingestion/manual_override perms */}
                                {(perm.source === 'ingestion' || perm.source === 'manual_override') &&
                                  onRemoveIngestionPerm && (
                                    <button
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        onRemoveIngestionPerm(perm.app_id, perm.key);
                                      }}
                                      title="Remove this permission"
                                      className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-red-100 dark:bg-red-800/40 text-red-600 dark:text-red-400 flex items-center justify-center opacity-0 group-hover:opacity-100 hover:opacity-100 transition-opacity border border-red-300 dark:border-red-600/40"
                                      aria-label={`Remove permission ${perm.key}`}
                                    >
                                      <X size={8} />
                                    </button>
                                  )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function RolesPermissions() {
  const [roles, setRoles] = useState([]);
  const [allPerms, setAllPerms] = useState([]);
  const [selected, setSelected] = useState(null);
  const [editing, setEditing] = useState(null);
  const [creating, setCreating] = useState(false);
  const [newRole, setNewRole] = useState({
    name: '',
    display_name: '',
    description: '',
    color: 'var(--aegisaxiom-text-muted)',
    permissions: [],
  });
  const [cloneModal, setCloneModal] = useState(null);
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });
  const [cloneForm, setCloneForm] = useState({ name: '', display_name: '' });
  const [saving, setSaving] = useState(false);

  // 3B: role preview modal
  const [previewRoleId, setPreviewRoleId] = useState(null);

  // 3C: app name map (app_id → display name) for ingestion-generated role badges
  const [appNames, setAppNames] = useState({});

  // 3A: add-perm modal state (opened from pencil icon on ingestion group)
  const [addPermModal, setAddPermModal] = useState(null); // { appId, group }
  const [addPermForm, setAddPermForm] = useState({ key: '', label: '' });

  // Permission editor state
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedGroups, setExpandedGroups] = useState(new Set());
  const [showEnabledOnly, setShowEnabledOnly] = useState(false);

  // New role permission editor state
  const [newSearchQuery, setNewSearchQuery] = useState('');
  const [newExpandedGroups, setNewExpandedGroups] = useState(new Set());
  const [newShowEnabledOnly, setNewShowEnabledOnly] = useState(false);

  useEffect(() => {
    load();
  }, []);

  const load = async () => {
    const [rr, pr, ir] = await Promise.all([
      api.get(`${API}/api/roles`),
      api.get(`${API}/api/roles/permissions`),
      api.get(`${API}/api/app-ingestion/ingestions`).catch(() => null),
    ]);
    if (rr.ok) {
      const d = await rr.json();
      setRoles(d);
      if (!selected && d.length) selectRole(d[0]);
    }
    if (pr.ok) setAllPerms(await pr.json());
    // 3C: build app_id → name map; silently ignore 403/failures (non-admin users)
    if (ir && ir.ok) {
      const apps = await ir.json();
      const map = {};
      for (const app of Array.isArray(apps) ? apps : []) {
        if (app.app_id) map[app.app_id] = app.app_name || app.name || app.app_id;
      }
      setAppNames(map);
    }
  };

  const selectRole = (role) => {
    setSelected(role);
    setEditing({ ...role, permissions: [...(role.permissions || [])] });
    setCreating(false);
    setSearchQuery('');
    setExpandedGroups(new Set());
    setShowEnabledOnly(false);
  };

  // Group permissions by their group field
  const grouped = useMemo(
    () =>
      allPerms.reduce((acc, p) => {
        if (!acc[p.group]) acc[p.group] = [];
        acc[p.group].push(p);
        return acc;
      }, {}),
    [allPerms],
  );

  const togglePerm = (key, on) => {
    if (!editing) return;
    setEditing((prev) => ({
      ...prev,
      permissions: on ? [...prev.permissions, key] : prev.permissions.filter((p) => p !== key),
    }));
  };

  const toggleGroup = (group, allOn) => {
    const keys = (grouped[group] || []).map((p) => p.key);
    setEditing((prev) => {
      const without = prev.permissions.filter((p) => !keys.includes(p));
      return { ...prev, permissions: allOn ? [...without, ...keys] : without };
    });
  };

  const newTogglePerm = (key, on) => {
    setNewRole((prev) => ({
      ...prev,
      permissions: on ? [...prev.permissions, key] : prev.permissions.filter((p) => p !== key),
    }));
  };

  const newToggleGroup = (group, allOn) => {
    const keys = (grouped[group] || []).map((p) => p.key);
    setNewRole((prev) => {
      const without = prev.permissions.filter((p) => !keys.includes(p));
      return { ...prev, permissions: allOn ? [...without, ...keys] : without };
    });
  };

  // 3A: add a manual_override permission to an ingested app
  const addIngestionPerm = async () => {
    const { appId } = addPermModal;
    const { key, label } = addPermForm;
    if (!key.trim()) { toast.error('Permission key is required'); return; }
    const group = addPermModal.group;
    const res = await api.post(
      `${API}/api/app-ingestion/ingestions/${appId}/permissions`,
      { key: key.trim(), group, label: label.trim() || key.trim() },
    );
    if (res.ok) {
      toast.success('Permission added');
      setAddPermModal(null);
      setAddPermForm({ key: '', label: '' });
      await load();
    } else {
      const e = await res.json().catch(() => ({}));
      toast.error(e.detail || 'Failed to add permission');
    }
  };

  // 3A: remove an ingestion/manual_override permission
  const removeIngestionPerm = (appId, permKey) => {
    setConfirmState({
      open: true,
      title: 'Remove Permission',
      message: `Remove permission "${permKey}"? It will be revoked from all roles that currently have it.`,
      confirmText: 'Remove',
      action: async () => {
        const res = await api.delete(
          `${API}/api/app-ingestion/ingestions/${appId}/permissions/${encodeURIComponent(permKey)}`,
        );
        if (res.ok) {
          toast.success('Permission removed');
          await load();
        } else {
          const e = await res.json().catch(() => ({}));
          toast.error(e.detail || 'Failed to remove permission');
        }
      },
    });
  };

  const saveEdits = async () => {
    if (!editing || editing.is_system) return;
    setSaving(true);
    try {
      const res = await api.put(`${API}/api/roles/${editing.role_id}`, {
        display_name: editing.display_name,
        description: editing.description,
        color: editing.color,
        permissions: editing.permissions,
      });
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail);
      }
      toast.success('Role saved');
      await load();
      setSelected(editing);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  const createRole = async () => {
    if (!newRole.name.trim()) {
      toast.error('Role name is required');
      return;
    }
    try {
      const res = await api.post(`${API}/api/roles`, newRole);
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail);
      }
      toast.success('Role created');
      setCreating(false);
      setNewRole({
        name: '',
        display_name: '',
        description: '',
        color: 'var(--aegisaxiom-text-muted)',
        permissions: [],
      });
      await load();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const cloneRole = async () => {
    try {
      const res = await api.post(`${API}/api/roles/${cloneModal.role_id}/clone`, cloneForm);
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail);
      }
      toast.success('Role cloned');
      setCloneModal(null);
      await load();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const deleteRole = (role) => {
    setConfirmState({
      open: true,
      title: 'Delete Role',
      message: `Delete role "${role.display_name}"? This cannot be undone.`,
      action: async () => {
        const res = await api.delete(`${API}/api/roles/${role.role_id}`);
        if (res.ok) {
          toast.success('Role deleted');
          setSelected(null);
          setEditing(null);
          await load();
        } else {
          const e = await res.json();
          toast.error(e.detail);
        }
      },
    });
  };

  const hasChanged =
    editing &&
    selected &&
    JSON.stringify(editing.permissions.sort()) !==
      JSON.stringify([...(selected.permissions || [])].sort());
  const isSystem = editing?.is_system;

  return (
    <div data-testid="roles-page">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5">
        <div>
          <h1 className="page-title flex items-center gap-2">
            <Shield size={22} className="text-violet-600 dark:text-violet-400" />
            Roles & Permissions
          </h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">
            Manage role definitions and granular permissions for each role
          </p>
        </div>
        <button
          onClick={() => {
            setCreating(true);
            setSelected(null);
            setEditing(null);
            setNewSearchQuery('');
            setNewExpandedGroups(new Set());
            setNewShowEnabledOnly(false);
          }}
          className="btn-primary flex items-center justify-center gap-2 w-full sm:w-auto shrink-0"
          data-testid="new-role-btn"
        >
          <Plus size={16} />
          New Role
        </button>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 sm:gap-5">
        {/* Role List — hidden on mobile when a role is selected or creating */}
        <div
          className={`lg:col-span-3 flex flex-col gap-3 ${selected || creating ? 'hidden lg:flex' : ''}`}
        >
          {roles.map((role) => {
            const isSelected = selected?.role_id === role.role_id;
            return (
              <div
                key={role.role_id}
                onClick={() => selectRole(role)}
                data-testid={`role-item-${role.name}`}
                className={`w-full text-left px-4 py-3 rounded-xl border transition-all cursor-pointer ${
                  isSelected
                    ? 'border-violet-500/50 bg-violet-50 dark:bg-violet-600/10 border-l-4 border-l-violet-500'
                    : 'glass-card hover:border-aegisaxiom-border-hover'
                }`}
              >
                <div className="flex items-center gap-3">
                  <div
                    className="w-3 h-3 rounded-full shrink-0"
                    style={{ background: role.color }}
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-aegisaxiom-text truncate">
                      {role.display_name}
                    </p>
                    <p className="text-xs text-aegisaxiom-text-muted truncate">
                      {role.permissions?.includes('*')
                        ? 'All permissions'
                        : `${role.permissions?.length || 0} permissions`}
                    </p>
                  </div>
                  {/* Action buttons: always visible (touch-friendly, matches other admin tables) */}
                  <div className="flex gap-0.5 shrink-0">
                    {/* 3B: Preview button */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setPreviewRoleId(role.role_id);
                      }}
                      className="p-1.5 rounded text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 hover:bg-aegisaxiom-elevated transition-colors"
                      aria-label={`Preview ${role.display_name}`}
                      title="Preview effective permissions"
                    >
                      <Eye size={13} />
                    </button>
                    {!role.is_default && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          deleteRole(role);
                        }}
                        className="p-1.5 rounded text-aegisaxiom-text-muted hover:text-red-600 dark:hover:text-red-400 hover:bg-aegisaxiom-elevated transition-colors"
                        aria-label={`Delete ${role.display_name}`}
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setCloneModal(role);
                        setCloneForm({
                          name: `${role.name}_copy`,
                          display_name: `${role.display_name} (Copy)`,
                        });
                      }}
                      className="p-1.5 rounded text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 hover:bg-aegisaxiom-elevated transition-colors"
                      aria-label={`Clone ${role.display_name}`}
                    >
                      <Copy size={13} />
                    </button>
                  </div>
                </div>
                {role.is_system && (
                  <div className="flex items-center gap-1 mt-1">
                    <Lock size={10} className="text-aegisaxiom-text-muted" />
                    <span className="text-xs text-aegisaxiom-text-muted">
                      System — cannot be modified
                    </span>
                  </div>
                )}
                {/* 3C: Ingestion-generated role badge */}
                {role.app_id && (
                  <div className="flex items-center gap-1 mt-1">
                    <span className="text-[10px] text-aegisaxiom-text-muted italic">
                      from {appNames[role.app_id] || role.app_id}
                    </span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {/* Permission Editor — hidden on mobile when no role selected and not creating */}
        <div className={`lg:col-span-9 ${!selected && !creating ? 'hidden lg:block' : ''}`}>
          {creating ? (
            <div className="glass-card p-4 sm:p-6 animate-fadeInUp">
              <div className="flex items-center gap-2 mb-5">
                <button
                  onClick={() => setCreating(false)}
                  className="lg:hidden p-1.5 rounded-lg text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors shrink-0"
                  aria-label="Back to roles list"
                >
                  <ChevronLeft size={18} />
                </button>
                <h2
                  className="text-lg font-bold text-aegisaxiom-text"
                  style={{ fontFamily: 'Manrope, sans-serif' }}
                >
                  Create New Role
                </h2>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-5">
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
                    Internal Name (slug) *
                  </label>
                  <input
                    className="input-field"
                    placeholder="e.g. regional_manager"
                    value={newRole.name}
                    onChange={(e) =>
                      setNewRole((p) => ({
                        ...p,
                        name: e.target.value.toLowerCase().replace(/\s/g, '_'),
                      }))
                    }
                    data-testid="new-role-name"
                  />
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
                    Display Name *
                  </label>
                  <input
                    className="input-field"
                    placeholder="e.g. Regional Manager"
                    value={newRole.display_name}
                    onChange={(e) => setNewRole((p) => ({ ...p, display_name: e.target.value }))}
                    data-testid="new-role-display-name"
                  />
                </div>
                <div className="sm:col-span-2">
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
                    Description
                  </label>
                  <input
                    className="input-field"
                    placeholder="Brief description of this role"
                    value={newRole.description}
                    onChange={(e) => setNewRole((p) => ({ ...p, description: e.target.value }))}
                  />
                </div>
                <div className="sm:col-span-2">
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
                    Color
                  </label>
                  <ColorPicker
                    value={newRole.color}
                    onChange={(c) => setNewRole((p) => ({ ...p, color: c }))}
                  />
                </div>
              </div>
              <div className="mb-5">
                <p className="text-sm font-semibold text-aegisaxiom-text mb-3">
                  Assign Permissions
                  <span className="ml-2 text-xs text-aegisaxiom-text-muted">
                    ({newRole.permissions.length} / {allPerms.length} enabled)
                  </span>
                </p>
                <PermissionEditor
                  allPerms={allPerms}
                  grouped={grouped}
                  permissions={newRole.permissions}
                  onTogglePerm={newTogglePerm}
                  onToggleGroup={newToggleGroup}
                  searchQuery={newSearchQuery}
                  setSearchQuery={setNewSearchQuery}
                  expandedGroups={newExpandedGroups}
                  setExpandedGroups={setNewExpandedGroups}
                  showEnabledOnly={newShowEnabledOnly}
                  setShowEnabledOnly={setNewShowEnabledOnly}
                />
              </div>
              <div className="flex gap-3 pt-2">
                <button onClick={() => setCreating(false)} className="btn-secondary flex-1">
                  Cancel
                </button>
                <button
                  onClick={createRole}
                  disabled={saving}
                  className="btn-primary flex-1"
                  data-testid="create-role-submit"
                >
                  {saving ? 'Creating...' : 'Create Role'}
                </button>
              </div>
            </div>
          ) : editing ? (
            <div className="glass-card p-4 sm:p-6 animate-fadeInUp">
              {/* Role Header */}
              <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 mb-6">
                <div className="flex items-center gap-3 sm:gap-4 flex-1 min-w-0">
                  <button
                    onClick={() => {
                      setSelected(null);
                      setEditing(null);
                    }}
                    className="lg:hidden p-1.5 rounded-lg text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors shrink-0"
                    aria-label="Back to roles list"
                  >
                    <ChevronLeft size={18} />
                  </button>
                  <div
                    className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
                    style={{
                      background: `${editing.color}30`,
                      border: `1px solid ${editing.color}60`,
                    }}
                  >
                    <Shield size={20} style={{ color: editing.color }} />
                  </div>
                  <div className="flex-1 min-w-0">
                    {isSystem ? (
                      <h2
                        className="text-lg sm:text-xl font-bold text-aegisaxiom-text truncate"
                        style={{ fontFamily: 'Manrope, sans-serif' }}
                      >
                        {editing.display_name}
                      </h2>
                    ) : (
                      <input
                        className="input-field text-base sm:text-lg font-bold bg-transparent border-transparent focus:border-aegisaxiom-border px-0 text-aegisaxiom-text w-full"
                        style={{ fontFamily: 'Manrope, sans-serif' }}
                        value={editing.display_name}
                        onChange={(e) =>
                          setEditing((p) => ({ ...p, display_name: e.target.value }))
                        }
                      />
                    )}
                    <p className="text-aegisaxiom-text-muted text-xs font-mono mt-0.5 truncate">
                      {editing.name}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {!isSystem && hasChanged && (
                    <button
                      onClick={saveEdits}
                      disabled={saving}
                      className="btn-primary flex items-center gap-2 flex-1 sm:flex-none justify-center"
                      data-testid="save-role-btn"
                    >
                      <Save size={14} />
                      {saving ? 'Saving...' : 'Save Changes'}
                    </button>
                  )}
                  {!isSystem && !hasChanged && (
                    <button
                      onClick={() => {
                        setCloneModal(editing);
                        setCloneForm({
                          name: `${editing.name}_copy`,
                          display_name: `${editing.display_name} (Copy)`,
                        });
                      }}
                      className="btn-secondary flex items-center gap-2 flex-1 sm:flex-none justify-center"
                    >
                      <Copy size={14} />
                      Clone
                    </button>
                  )}
                </div>
              </div>
              {!isSystem && (
                <div className="mb-5">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                        Description
                      </label>
                      <input
                        className="input-field"
                        value={editing.description || ''}
                        onChange={(e) => setEditing((p) => ({ ...p, description: e.target.value }))}
                        placeholder="Role description..."
                      />
                    </div>
                    <div>
                      <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Color</label>
                      <ColorPicker
                        value={editing.color}
                        onChange={(c) => setEditing((p) => ({ ...p, color: c }))}
                      />
                    </div>
                  </div>
                </div>
              )}
              {isSystem ? (
                <div className="flex items-center gap-3 p-4 bg-violet-50 dark:bg-violet-600/10 border border-violet-200 dark:border-violet-600/20 rounded-xl">
                  <Lock size={18} className="text-violet-600 dark:text-violet-400 shrink-0" />
                  <div>
                    <p className="text-violet-700 dark:text-violet-300 text-sm font-medium">
                      System Role — Full Access
                    </p>
                    <p className="text-aegisaxiom-text-secondary text-xs">
                      The Administrator role has all permissions (*) and cannot be modified for
                      security.
                    </p>
                  </div>
                </div>
              ) : (
                <>
                  <div className="flex items-center justify-between mb-4">
                    <p className="text-sm font-semibold text-aegisaxiom-text">
                      Permissions
                      <span className="ml-2 text-xs text-aegisaxiom-text-muted">
                        ({editing.permissions.length} / {allPerms.length} enabled)
                      </span>
                    </p>
                    {hasChanged && (
                      <span className="text-xs text-amber-600 dark:text-amber-400 flex items-center gap-1">
                        <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
                        Unsaved changes
                      </span>
                    )}
                  </div>
                  <PermissionEditor
                    allPerms={allPerms}
                    grouped={grouped}
                    permissions={editing.permissions}
                    onTogglePerm={togglePerm}
                    onToggleGroup={toggleGroup}
                    searchQuery={searchQuery}
                    setSearchQuery={setSearchQuery}
                    expandedGroups={expandedGroups}
                    setExpandedGroups={setExpandedGroups}
                    showEnabledOnly={showEnabledOnly}
                    setShowEnabledOnly={setShowEnabledOnly}
                    onAddIngestionPerm={(appId, group) => {
                      setAddPermModal({ appId, group });
                      setAddPermForm({ key: '', label: '' });
                    }}
                    onRemoveIngestionPerm={removeIngestionPerm}
                  />
                  {hasChanged && (
                    <div className="mt-4 pt-4 border-t border-aegisaxiom-border flex gap-3">
                      <button
                        onClick={() =>
                          setEditing({
                            ...selected,
                            permissions: [...(selected.permissions || [])],
                          })
                        }
                        className="btn-secondary flex-1"
                      >
                        Discard
                      </button>
                      <button
                        onClick={saveEdits}
                        disabled={saving}
                        className="btn-primary flex-1 flex items-center justify-center gap-2"
                        data-testid="save-role-bottom-btn"
                      >
                        <Save size={14} />
                        {saving ? 'Saving...' : 'Save Changes'}
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>
          ) : (
            <div className="glass-card p-10 sm:p-16 text-center">
              <Shield size={40} className="text-aegisaxiom-text-muted mx-auto mb-3" />
              <p className="text-aegisaxiom-text-muted">Select a role to view and edit its permissions</p>
            </div>
          )}
        </div>
      </div>
      {/* Clone Modal */}
      {cloneModal && (
        <div className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-4">
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-sm p-6 animate-fadeInUp">
            <div className="flex items-center justify-between mb-4">
              <h2
                className="text-lg font-bold text-aegisaxiom-text"
                style={{ fontFamily: 'Manrope, sans-serif' }}
              >
                Clone Role
              </h2>
              <button onClick={() => setCloneModal(null)}>
                <X size={18} className="text-aegisaxiom-text-secondary" />
              </button>
            </div>
            <p className="text-aegisaxiom-text-secondary text-sm mb-4">
              Cloning: <strong className="text-aegisaxiom-text">{cloneModal.display_name}</strong>
            </p>
            <div>
              <div className="mb-3">
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                  New Internal Name
                </label>
                <input
                  className="input-field"
                  value={cloneForm.name}
                  onChange={(e) =>
                    setCloneForm((p) => ({
                      ...p,
                      name: e.target.value.toLowerCase().replace(/\s/g, '_'),
                    }))
                  }
                  data-testid="clone-role-name"
                />
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                  New Display Name
                </label>
                <input
                  className="input-field"
                  value={cloneForm.display_name}
                  onChange={(e) => setCloneForm((p) => ({ ...p, display_name: e.target.value }))}
                />
              </div>
            </div>
            <div className="flex gap-3 mt-4">
              <button onClick={() => setCloneModal(null)} className="btn-secondary flex-1">
                Cancel
              </button>
              <button
                onClick={cloneRole}
                disabled={saving}
                className="btn-primary flex-1"
                data-testid="clone-role-submit"
              >
                {saving ? 'Cloning...' : 'Clone Role'}
              </button>
            </div>
          </div>
        </div>
      )}
      <ConfirmModal
        isOpen={confirmState.open}
        title={confirmState.title}
        message={confirmState.message}
        confirmText={confirmState.confirmText || 'Delete'}
        onConfirm={() => confirmState.action?.()}
        onCancel={() => setConfirmState({ open: false, action: null, title: '', message: '' })}
      />

      {/* 3A: Add ingestion permission modal */}
      {addPermModal && (
        <div className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-4">
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-sm p-6 animate-fadeInUp">
            <div className="flex items-center justify-between mb-4">
              <h2
                className="text-lg font-bold text-aegisaxiom-text"
                style={{ fontFamily: 'Manrope, sans-serif' }}
              >
                Add Permission
              </h2>
              <button onClick={() => setAddPermModal(null)}>
                <X size={18} className="text-aegisaxiom-text-secondary" />
              </button>
            </div>
            <p className="text-aegisaxiom-text-secondary text-sm mb-4">
              Adding to group:{' '}
              <strong className="text-aegisaxiom-text">{addPermModal.group}</strong>
            </p>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                  Permission Key *
                </label>
                <input
                  className="input-field"
                  placeholder="e.g. myapp.feature.view"
                  value={addPermForm.key}
                  onChange={(e) => setAddPermForm((p) => ({ ...p, key: e.target.value }))}
                  autoFocus
                />
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                  Label (optional)
                </label>
                <input
                  className="input-field"
                  placeholder="Human-readable label"
                  value={addPermForm.label}
                  onChange={(e) => setAddPermForm((p) => ({ ...p, label: e.target.value }))}
                />
              </div>
            </div>
            <div className="flex gap-3 mt-4">
              <button onClick={() => setAddPermModal(null)} className="btn-secondary flex-1">
                Cancel
              </button>
              <button onClick={addIngestionPerm} className="btn-primary flex-1">
                Add Permission
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 3B: Role preview modal */}
      {previewRoleId && (
        <RolePreviewModal
          roleId={previewRoleId}
          onClose={() => setPreviewRoleId(null)}
        />
      )}
    </div>
  );
}
