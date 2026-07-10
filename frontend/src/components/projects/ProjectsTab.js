import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Plus, Search } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';
import { useAuth } from '@/contexts/AuthContext';
import TabStrip from '@/components/shared/TabStrip';
import ProjectDetailPage from '@/components/projects/ProjectDetailPage';
import CreateProjectWizard from '@/components/projects/CreateProjectWizard';
import PaginationControls from '@/components/common/PaginationControls';

// Default page size for the Projects list. Independent of the Time
// Management Quick Add picker, which calls /api/projects/projects with
// limit=500 directly — that caller is unaffected by this constant.
const DEFAULT_PAGE_SIZE = 25;

const SERVICE_LINES = [
  'ucaas', 'networking', 'cloud', 'security', 'ai_product', 'other',
];

const METHODOLOGY_TABS = [
  { id: 'all', label: 'All' },
  { id: 'waterfall', label: 'Waterfall' },
  { id: 'agile', label: 'Agile' },
  { id: 'waves_threads', label: 'Waves & Threads' },
];

const HEALTH_DOT = {
  green: '#4ade80',
  yellow: '#facc15',
  red: '#f87171',
};

const STATUS_PILL = {
  planning: 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted',
  active: 'bg-blue-500/15 text-blue-400',
  on_hold: 'bg-yellow-500/15 text-yellow-400',
  closed: 'bg-green-500/15 text-green-400',
};

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    const m = typeof iso === 'string' && /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    const d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return iso;
  }
}

function debounce(fn, ms) {
  let t = null;
  return (...args) => {
    if (t) clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

function TypeChip({ type }) {
  if (!type) return null;
  const label = type === 'waves_threads' ? 'W&T' : String(type).toUpperCase();
  return (
    <span
      data-testid="project-type-chip"
      className="inline-flex items-center h-5 px-2 rounded bg-violet-500/15 text-violet-400 text-[10px] font-semibold tracking-wide"
    >
      {label}
    </span>
  );
}

function StatusPill({ status }) {
  if (!status) return null;
  const cls = STATUS_PILL[status] ?? 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted';
  const label = String(status).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  return (
    <span className={`inline-flex items-center h-5 px-2 rounded text-[10px] font-medium ${cls}`}>
      {label}
    </span>
  );
}

export default function ProjectsTab({ urlProjectId, onProjectIdChange }) {
  const perms = usePermissions();
  const { user } = useAuth();
  const myUserId = user?.user_id ?? user?._id;
  const canCreate = perms.hasPermission('projects.project.create');

  const [methodologyTab, setMethodologyTab] = useState('all');
  const [filters, setFilters] = useState({ status: '', service_line: '', pm_id: '', q: '', mine: false });
  const [qDraft, setQDraft] = useState('');
  // Server-side pagination. The Time Management Quick Add picker
  // calls /api/projects/projects?limit=500 directly and is unaffected
  // by these page controls.
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);

  const [projects, setProjects] = useState([]);
  const [total, setTotal] = useState(0);
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);

  const [wizardOpen, setWizardOpen] = useState(false);
  // detailProjectId is now URL-driven when running inside ProjectsPortal,
  // but we keep a local fallback so the component still works standalone.
  const [localDetailId, setLocalDetailId] = useState(null);
  const detailProjectId = onProjectIdChange ? (urlProjectId ?? null) : localDetailId;
  const setDetailProjectId = (id) => {
    if (onProjectIdChange) onProjectIdChange(id);
    else setLocalDetailId(id);
  };

  // Debounced search. Setting q resets page=1 below via the filter-
  // change effect.
  const applyQ = useMemo(() => debounce((v) => {
    setFilters((f) => ({ ...f, q: v }));
  }, 250), []);

  const fetchProjects = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams();
    if (methodologyTab !== 'all') params.set('project_type', methodologyTab);
    if (filters.status) params.set('status', filters.status);
    if (filters.service_line) params.set('service_line', filters.service_line);
    if (filters.pm_id) params.set('pm_id', filters.pm_id);
    if (filters.q) params.set('q', filters.q);
    if (filters.mine && myUserId) params.set('assigned_user_id', myUserId);
    params.set('limit', String(pageSize));
    params.set('skip', String(Math.max(0, (page - 1) * pageSize)));
    try {
      const res = await api.get(`${API_URL}/api/projects/projects?${params.toString()}`);
      if (!res.ok) throw new Error('Failed');
      const data = await res.json();
      setProjects(Array.isArray(data) ? data : data.items ?? []);
      setTotal(Array.isArray(data) ? data.length : data.total ?? 0);
    } catch {
      // keep stale data
    } finally {
      setLoading(false);
    }
  }, [methodologyTab, filters, page, pageSize, myUserId]);

  // Reset page=1 whenever a filter that changes the result set
  // changes. Page-size changes also reset to keep the displayed range
  // honest (e.g., on page 4 of 25/page, switching to 100/page would
  // otherwise show "Showing 76–100 of 12").
  useEffect(() => {
    setPage(1);
  }, [methodologyTab, filters, pageSize]);

  const fetchUsers = useCallback(async () => {
    try {
      const res = await api.get(`${API_URL}/api/projects/assignable-users?role=pm`);
      if (!res.ok) {
        toast.error('Could not load PM list.');
        return;
      }
      const data = await res.json();
      setUsers(Array.isArray(data) ? data : data.items ?? []);
    } catch {
      toast.error('Could not load PM list.');
    }
  }, []);

  useEffect(() => { fetchProjects(); }, [fetchProjects]);
  useEffect(() => { fetchUsers(); }, [fetchUsers]);

  const pmById = useMemo(() => {
    const m = new Map();
    users.forEach((u) => m.set(u.user_id ?? u._id, u));
    return m;
  }, [users]);

  const hasAnyFilter = filters.status || filters.service_line || filters.pm_id || filters.q || filters.mine;
  const clearFilters = () => { setFilters({ status: '', service_line: '', pm_id: '', q: '', mine: false }); setQDraft(''); };

  if (detailProjectId) {
    return (
      <ProjectDetailPage
        projectId={detailProjectId}
        onBack={() => setDetailProjectId(null)}
      />
    );
  }

  return (
    <section data-testid="projects-list-page">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-aegisaxiom-text" data-testid="projects-title">Projects</h2>
        {canCreate && (
          <button
            type="button"
            onClick={() => setWizardOpen(true)}
            data-testid="new-project-button"
            className="btn-primary inline-flex items-center gap-2 text-sm"
          >
            <Plus size={15} />
            New Project
          </button>
        )}
      </div>

      <TabStrip
        tabs={METHODOLOGY_TABS}
        activeId={methodologyTab}
        onChange={(id) => setMethodologyTab(id)}
        variant="underline"
        className="mb-4"
      />

      {/* Filter row */}
      <div className="flex flex-wrap items-center gap-3 mb-4" data-testid="projects-filter-row">
        <select
          data-testid="filter-status"
          value={filters.status}
          onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}
          className="aegisaxiom-input h-8 px-2 text-xs"
        >
          <option value="">All statuses</option>
          <option value="planning">Planning</option>
          <option value="active">Active</option>
          <option value="on_hold">On hold</option>
          <option value="closed">Closed</option>
        </select>

        <select
          data-testid="filter-service-line"
          value={filters.service_line}
          onChange={(e) => setFilters((f) => ({ ...f, service_line: e.target.value }))}
          className="aegisaxiom-input h-8 px-2 text-xs"
        >
          <option value="">All service lines</option>
          {SERVICE_LINES.map((s) => (
            <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>
          ))}
        </select>

        <select
          data-testid="filter-pm"
          value={filters.pm_id}
          onChange={(e) => setFilters((f) => ({ ...f, pm_id: e.target.value }))}
          className="aegisaxiom-input h-8 px-2 text-xs"
        >
          <option value="">All PMs</option>
          {users
            .slice()
            .sort((a, b) => (a.name ?? a.display_name ?? '').localeCompare(b.name ?? b.display_name ?? ''))
            .map((u) => (
              <option key={u.user_id ?? u._id} value={u.user_id ?? u._id}>
                {u.name ?? u.display_name}
              </option>
            ))}
        </select>

        <div className="relative" data-testid="filter-search">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted pointer-events-none" />
          <input
            type="text"
            value={qDraft}
            onChange={(e) => { setQDraft(e.target.value); applyQ(e.target.value); }}
            placeholder="Search projects…"
            className="aegisaxiom-input pl-7 h-8 w-56 text-xs"
          />
        </div>

        {myUserId ? (
          <button
            type="button"
            onClick={() => setFilters((f) => ({ ...f, mine: !f.mine }))}
            className={`h-8 px-3 rounded-md text-xs font-medium border transition-colors ${
              filters.mine
                ? 'border-violet-500 bg-violet-500/15 text-violet-300'
                : 'border-aegisaxiom-border text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated'
            }`}
            data-testid="filter-assigned-to-me"
          >
            Assigned to me
          </button>
        ) : null}

        {hasAnyFilter && (
          <button
            type="button"
            onClick={clearFilters}
            data-testid="clear-filters"
            className="ml-auto text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text underline-offset-4 hover:underline transition-colors"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Table */}
      {loading ? (
        <div className="min-h-[200px] flex items-center justify-center">
          <div className="animate-spin w-7 h-7 rounded-full border-2 border-violet-500 border-t-transparent" />
        </div>
      ) : projects.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <p className="text-sm font-medium text-aegisaxiom-text mb-1">No projects yet.</p>
          <p className="text-xs text-aegisaxiom-text-muted mb-4">Create your first project to get started.</p>
          {canCreate && (
            <button
              type="button"
              onClick={() => setWizardOpen(true)}
              className="btn-primary inline-flex items-center gap-2 text-sm"
              data-testid="empty-new-project"
            >
              <Plus size={14} />
              New Project
            </button>
          )}
        </div>
      ) : (
        <div className="glass-card overflow-hidden" data-testid="projects-table-wrap">
          <table className="w-full text-sm" data-testid="projects-table">
            <thead>
              <tr className="h-9 bg-aegisaxiom-elevated text-[11px] text-aegisaxiom-text-muted uppercase tracking-wide border-b border-aegisaxiom-border">
                <th className="text-left px-4 font-medium">Name</th>
                <th className="text-left px-3 font-medium">Type</th>
                <th className="text-left px-3 font-medium hidden sm:table-cell">Service line</th>
                <th className="text-left px-3 font-medium">Status</th>
                <th className="text-left px-3 font-medium">Health</th>
                <th className="text-left px-3 font-medium hidden md:table-cell">PM</th>
                <th className="text-left px-3 font-medium hidden lg:table-cell">Dates</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => {
                const pm = pmById.get(p.pm_id);
                return (
                  <tr
                    key={p.project_id ?? p._id}
                    onClick={() => setDetailProjectId(p.project_id ?? p._id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        setDetailProjectId(p.project_id ?? p._id);
                      }
                    }}
                    tabIndex={0}
                    role="button"
                    aria-label={`Open project ${p.name}`}
                    className="h-10 border-t border-aegisaxiom-border hover:bg-aegisaxiom-elevated cursor-pointer transition-colors focus:bg-aegisaxiom-elevated focus:outline-none focus:ring-1 focus:ring-violet-500"
                    data-testid={`project-row-${p.project_id ?? p._id}`}
                  >
                    <td className="px-4 max-w-[260px] truncate font-semibold text-aegisaxiom-text" title={p.description ?? p.name}>
                      {p.name}
                    </td>
                    <td className="px-3">
                      <TypeChip type={p.project_type} />
                    </td>
                    <td className="px-3 text-aegisaxiom-text-secondary capitalize hidden sm:table-cell">
                      {(p.service_line ?? '').replace(/_/g, ' ')}
                    </td>
                    <td className="px-3">
                      <StatusPill status={p.status} />
                    </td>
                    <td className="px-3">
                      <span className="inline-flex items-center gap-1.5 text-xs">
                        <span
                          className="inline-block w-2 h-2 rounded-full"
                          style={{ backgroundColor: HEALTH_DOT[p.health] ?? '#94a3b8' }}
                        />
                        <span className="capitalize text-aegisaxiom-text-secondary">{p.health}</span>
                      </span>
                    </td>
                    <td className="px-3 hidden md:table-cell">
                      <span className="text-xs text-aegisaxiom-text-secondary truncate max-w-[120px]">
                        {pm?.name ?? pm?.display_name ?? '—'}
                      </span>
                    </td>
                    <td className="px-3 text-xs text-aegisaxiom-text-muted whitespace-nowrap hidden lg:table-cell">
                      {fmtDate(p.start_date)} → {fmtDate(p.target_end_date)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="px-3 border-t border-aegisaxiom-border">
            <PaginationControls
              page={page}
              pageSize={pageSize}
              total={total}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
              loading={loading}
              testid="projects-pagination"
            />
          </div>
        </div>
      )}

      {wizardOpen && (
        <CreateProjectWizard
          open={wizardOpen}
          onClose={() => setWizardOpen(false)}
          onCreated={(projectId) => {
            setWizardOpen(false);
            setDetailProjectId(projectId);
          }}
        />
      )}
    </section>
  );
}
