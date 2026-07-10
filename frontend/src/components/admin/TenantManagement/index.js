import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { toast } from 'sonner';
import { Plus, AlertCircle, RefreshCw, Building2 } from 'lucide-react';
import TenantList from './TenantList';
import TenantFormModal from './TenantFormModal';
import ConfirmModal from '../../shared/ConfirmModal';
import { listTenants, archiveTenant } from './api';

/**
 * Phase 1D.3 — Tenants admin page with full CRUD plumbing.
 *
 * 1D.2 introduced the read-only list. This phase adds:
 *   • A wired-up Create button that opens TenantFormModal in create mode.
 *   • Per-row kebab actions (Edit, Archive) on TenantList, both routed
 *     through this container for state ownership and refresh-on-success.
 *   • Soft-archive flow via ConfirmModal → DELETE /api/tenants/{id}, with
 *     proper 409 handling for tenants that still have active children.
 *
 * The Create + Edit modal is a single component (TenantFormModal) driven
 * by `modalState` here: null when closed, 'create' for new, or
 * { mode: 'edit', tenant } when editing.
 */

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'partner', label: 'Partners' },
  { id: 'account', label: 'Accounts' },
  { id: 'archived', label: 'Archived' },
];

function matchesFilter(tenant, filterId) {
  if (filterId === 'archived') return tenant.status === 'archived';
  if (tenant.status === 'archived') return false;
  if (tenant.tenant_type === 'root') return false;
  if (filterId === 'all') return true;
  return tenant.tenant_type === filterId;
}

export default function TenantManagement() {
  const [tenants, setTenants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeFilter, setActiveFilter] = useState('all');

  // 1D.3 — modal + archive state
  const [modalState, setModalState] = useState(null); // null | 'create' | { mode: 'edit', tenant }
  const [archiveTarget, setArchiveTarget] = useState(null);
  const [archiving, setArchiving] = useState(false);

  const loadTenants = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listTenants();
      if (!res.ok) {
        const body = await res.json();
        const message = body?.detail || `Failed to load tenants (HTTP ${res.status})`;
        setError(message);
        toast.error(message);
        return;
      }
      const data = await res.json();
      setTenants(data?.tenants || []);
    } catch (e) {
      const message = 'Network error loading tenants.';
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTenants();
  }, [loadTenants]);

  const counts = useMemo(() => {
    const out = { all: 0, partner: 0, account: 0, archived: 0 };
    for (const t of tenants) {
      for (const f of FILTERS) {
        if (matchesFilter(t, f.id)) out[f.id] += 1;
      }
    }
    return out;
  }, [tenants]);

  const visible = useMemo(
    () => tenants.filter((t) => matchesFilter(t, activeFilter)),
    [tenants, activeFilter],
  );

  const parentNameById = useMemo(() => {
    const map = new Map();
    for (const t of tenants) map.set(t.tenant_id, t.name);
    return map;
  }, [tenants]);

  const hasAnyManageable = useMemo(
    () => tenants.some((t) => t.tenant_type !== 'root'),
    [tenants],
  );

  // ─── Action handlers ──────────────────────────────────────────────

  const handleCreateClick = () => setModalState('create');

  const handleEdit = useCallback((tenant) => {
    setModalState({ mode: 'edit', tenant });
  }, []);

  const handleArchive = useCallback((tenant) => {
    setArchiveTarget(tenant);
  }, []);

  const handleModalSuccess = () => {
    setModalState(null);
    loadTenants();
  };

  const handleArchiveConfirm = async () => {
    if (!archiveTarget) return;
    setArchiving(true);
    try {
      const res = await archiveTenant(archiveTarget.tenant_id);
      if (res.ok) {
        toast.success(`${archiveTarget.name} archived.`);
        setArchiveTarget(null);
        loadTenants();
      } else {
        const body = await res.json().catch(() => ({}));
        if (res.status === 409) {
          toast.error(
            'Cannot archive — this tenant has active children. Archive them first.',
          );
        } else if (res.status === 403) {
          toast.error("You don't have permission to archive tenants.");
        } else {
          toast.error(body?.detail || `Archive failed (HTTP ${res.status}).`);
        }
        setArchiveTarget(null);
      }
    } catch (err) {
      toast.error('Network error during archive.');
      setArchiveTarget(null);
    } finally {
      setArchiving(false);
    }
  };

  const handleArchiveCancel = () => {
    if (!archiving) setArchiveTarget(null);
  };

  return (
    <div data-testid="tenant-management-page">
      {/* Header */}
      <div className="flex items-start justify-between gap-3 mb-5">
        <div>
          <h1 className="text-2xl font-bold text-aegisaxiom-text">Tenants</h1>
          <p className="text-sm text-aegisaxiom-text-secondary mt-1">
            Manage the SimpliMeta tenant hierarchy
          </p>
        </div>
        <button
          onClick={handleCreateClick}
          className="btn-primary inline-flex items-center gap-1.5 shrink-0"
          data-testid="create-tenant-button"
        >
          <Plus size={16} />
          <span className="hidden sm:inline">Create tenant</span>
          <span className="sm:hidden">New</span>
        </button>
      </div>

      {/* Filter pills */}
      <div className="flex flex-wrap gap-2 mb-4">
        {FILTERS.map((f) => {
          const isActive = activeFilter === f.id;
          return (
            <button
              key={f.id}
              onClick={() => setActiveFilter(f.id)}
              data-testid={`filter-pill-${f.id}`}
              className={
                'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border transition ' +
                (isActive
                  ? 'bg-violet-100 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border-violet-300 dark:border-violet-500/30'
                  : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border-aegisaxiom-border hover:border-aegisaxiom-border-hover')
              }
            >
              {f.label}
              <span
                className={
                  'inline-flex items-center justify-center min-w-[18px] px-1 rounded-full text-[10px] font-semibold ' +
                  (isActive
                    ? 'bg-violet-200 dark:bg-violet-500/30 text-violet-800 dark:text-violet-200'
                    : 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted')
                }
              >
                {counts[f.id]}
              </span>
            </button>
          );
        })}
      </div>

      {/* Body */}
      {loading ? (
        <LoadingSkeleton />
      ) : error ? (
        <ErrorPanel message={error} onRetry={loadTenants} />
      ) : visible.length === 0 ? (
        <EmptyState
          filter={activeFilter}
          totalCount={tenants.length}
          hasAnyManageable={hasAnyManageable}
          onRefresh={loadTenants}
        />
      ) : (
        <TenantList
          tenants={visible}
          parentNameById={parentNameById}
          onEdit={handleEdit}
          onArchive={handleArchive}
        />
      )}

      {/* Modals */}
      <TenantFormModal
        isOpen={modalState !== null}
        mode={modalState === 'create' ? 'create' : 'edit'}
        tenant={modalState && modalState !== 'create' ? modalState.tenant : null}
        allTenants={tenants}
        onClose={() => setModalState(null)}
        onSuccess={handleModalSuccess}
      />

      <ConfirmModal
        isOpen={archiveTarget !== null}
        onConfirm={handleArchiveConfirm}
        onCancel={handleArchiveCancel}
        title={archiveTarget ? `Archive ${archiveTarget.name}?` : ''}
        message={
          archiveTarget
            ? 'This tenant will be marked as archived. Existing data is preserved, but no new operations will be possible. Tenants with active children cannot be archived — archive the children first.'
            : ''
        }
        confirmText={archiving ? 'Archiving…' : 'Archive'}
        cancelText="Cancel"
        variant="danger"
      />
    </div>
  );
}

// ─── Internal: state components ─────────────────────────────────────

function LoadingSkeleton() {
  return (
    <div
      className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl overflow-hidden"
      data-testid="tenants-loading"
    >
      <div className="hidden md:block">
        {[1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className="flex items-center gap-4 px-4 py-4 border-b border-aegisaxiom-border last:border-b-0"
          >
            <div className="h-3 bg-aegisaxiom-elevated rounded animate-pulse flex-1 max-w-[200px]" />
            <div className="h-3 bg-aegisaxiom-elevated rounded animate-pulse w-20" />
            <div className="h-3 bg-aegisaxiom-elevated rounded animate-pulse w-24" />
            <div className="h-3 bg-aegisaxiom-elevated rounded animate-pulse w-16" />
            <div className="h-3 bg-aegisaxiom-elevated rounded animate-pulse w-24" />
          </div>
        ))}
      </div>
      <div className="md:hidden p-3 space-y-2.5">
        {[1, 2, 3].map((i) => (
          <div
            key={i}
            className="h-[68px] bg-aegisaxiom-elevated rounded-lg animate-pulse"
          />
        ))}
      </div>
    </div>
  );
}

function ErrorPanel({ message, onRetry }) {
  return (
    <div
      className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-8 text-center"
      data-testid="tenants-error"
    >
      <div className="w-12 h-12 rounded-xl bg-red-50 dark:bg-red-600/10 mx-auto mb-3 flex items-center justify-center">
        <AlertCircle className="w-6 h-6 text-red-600 dark:text-red-400" />
      </div>
      <h2 className="text-base font-semibold text-aegisaxiom-text mb-1">
        Couldn't load tenants
      </h2>
      <p className="text-sm text-aegisaxiom-text-secondary mb-4 max-w-md mx-auto">
        {message}
      </p>
      <button
        onClick={onRetry}
        className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text border border-aegisaxiom-border hover:border-aegisaxiom-border-hover transition"
      >
        <RefreshCw size={14} />
        Retry
      </button>
    </div>
  );
}

function EmptyState({ filter, totalCount, hasAnyManageable, onRefresh }) {
  if (totalCount === 0) {
    return (
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-10 text-center"
        data-testid="tenants-empty-deploy"
      >
        <div className="w-14 h-14 rounded-xl bg-amber-50 dark:bg-amber-600/10 mx-auto mb-4 flex items-center justify-center">
          <AlertCircle className="w-7 h-7 text-amber-600 dark:text-amber-400" />
        </div>
        <h2 className="text-base font-semibold text-aegisaxiom-text mb-1">
          No tenants found
        </h2>
        <p className="text-sm text-aegisaxiom-text-secondary mb-4 max-w-md mx-auto">
          The SimpliMeta root tenant should have been seeded on first deploy.
          If you're seeing this on a fresh environment, check the seed scripts.
        </p>
        <button
          onClick={onRefresh}
          className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text border border-aegisaxiom-border hover:border-aegisaxiom-border-hover transition"
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </div>
    );
  }

  if (!hasAnyManageable) {
    return (
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-10 text-center"
        data-testid="tenants-empty-fresh"
      >
        <div className="w-14 h-14 rounded-xl bg-violet-50 dark:bg-violet-600/10 mx-auto mb-4 flex items-center justify-center">
          <Building2 className="w-7 h-7 text-violet-600 dark:text-violet-400" />
        </div>
        <h2 className="text-base font-semibold text-aegisaxiom-text mb-1">
          No partners or accounts yet
        </h2>
        <p className="text-sm text-aegisaxiom-text-secondary max-w-md mx-auto">
          Only the SimpliMeta root tenant exists right now. Use the "Create tenant"
          button above to add the first partner or account.
        </p>
      </div>
    );
  }

  const filterLabel = FILTERS.find((f) => f.id === filter)?.label || filter;
  return (
    <div
      className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-8 text-center"
      data-testid="tenants-empty-filter"
    >
      <p className="text-sm text-aegisaxiom-text-secondary">
        No tenants match the <span className="font-medium">{filterLabel}</span> filter.
      </p>
    </div>
  );
}
