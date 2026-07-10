/**
 * ContractProjectsPanel — Link/unlink projects on a contract.
 *
 * Wires the existing Wave-1 contract project-linking routes to the
 * contract detail surface so finance / sales-ops can attach and
 * detach projects without API tooling.
 *
 * Backend routes consumed (no inventions):
 *   POST   /api/contracts/{id}/projects
 *          body: { project_ids: [str, ...] }
 *          idempotent — duplicates and already-linked ids are deduped
 *          returns the updated contract
 *   DELETE /api/contracts/{id}/projects/{project_id}
 *          idempotent — detaching a never-linked id returns 200 with
 *          the unchanged contract
 *
 * Permissions (mirror backend/routes/contracts.py):
 *   - link / unlink : contracts.edit | contracts.edit.all
 *                     | contracts.edit.own | '*'
 *
 * Optional project-name resolution + search (used only when the
 * caller also has projects.project.view). The canonical paths are
 * doubled because the projects_project router is mounted under the
 * /api/projects prefix and its own route literals start with
 * /projects — see backend/server.py:261 and the @router.get
 * declarations in backend/routes/projects_project.py.
 *   GET /api/projects/projects/{id}         — resolve one linked id
 *   GET /api/projects/projects?q=…&limit=… — search candidates
 *
 * If the caller lacks projects.project.view the panel falls back to
 * a plain project_id input (no fake picker) so the user can still
 * paste an id and link. We never invent a candidate list.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import {
  Plus,
  RefreshCw,
  AlertTriangle,
  Link2,
  Trash2,
  Search,
  Briefcase,
  ExternalLink,
} from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';

const API = API_URL;

// Project status palette — mirrors what ProjectDetailPage / the
// project routes write. The panel only RENDERS these values; it
// never writes them, so an unknown status falls through to the
// neutral pill.
const PROJECT_STATUS_META = {
  planning: {
    label: 'Planning',
    className:
      'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300 border border-blue-300/40',
  },
  active: {
    label: 'Active',
    className:
      'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border border-emerald-300/40',
  },
  on_hold: {
    label: 'On hold',
    className:
      'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border border-amber-300/40',
  },
  closed: {
    label: 'Closed',
    className:
      'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border',
  },
  cancelled: {
    label: 'Cancelled',
    className:
      'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300 border border-red-300/40',
  },
};

function ProjectStatusBadge({ status }) {
  if (!status) return <span className="text-aegisaxiom-text-muted text-[10px]">—</span>;
  const meta = PROJECT_STATUS_META[status] || {
    label: status,
    className: 'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border',
  };
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${meta.className}`}>
      {meta.label}
    </span>
  );
}

export default function ContractProjectsPanel({ contract, onContractUpdated }) {
  const { hasPermission } = usePermissions();
  const navigate = useNavigate();

  const canEdit =
    hasPermission('contracts.edit')
    || hasPermission('contracts.edit.all')
    || hasPermission('contracts.edit.own')
    || hasPermission('*');

  // The candidate-search and per-id resolve routes both require
  // projects.project.view. Without it we cannot show project names
  // and we cannot offer the picker — fall back to a plain id input.
  const canSeeProjects = hasPermission('projects.project.view') || hasPermission('*');

  const contractId = contract?.contract_id;
  // Linked-project ids combine two sources:
  //   (1) contract.project_ids[] — the forward Wave-1 linker list.
  //   (2) contract.projects[].project_id — the back-edge fan-in the
  //       backend computes from project.contract_id. Imported Zoho
  //       contracts only carry the back-edge: project_ids[] is empty
  //       but 101 imported projects point at imported contracts via
  //       project.contract_id. Without the back-edge merge below the
  //       panel rendered "no projects linked" on every imported
  //       contract.
  // The back-edge ids are NOT writable via the link/unlink form —
  // unlinking would have no effect because project_ids does not
  // carry them. Visible navigation works for both sources.
  const linkedIds = useMemo(() => {
    const forward = Array.isArray(contract?.project_ids) ? contract.project_ids : [];
    const backEdge = Array.isArray(contract?.projects)
      ? contract.projects.map((p) => p?.project_id).filter(Boolean)
      : [];
    // Stable order: forward first, then any back-edge ids not on the
    // forward side, deduped by first occurrence.
    const seen = new Set();
    const merged = [];
    for (const id of [...forward, ...backEdge]) {
      if (id && !seen.has(id)) {
        seen.add(id);
        merged.push(id);
      }
    }
    return merged;
  }, [contract?.project_ids, contract?.projects]);

  // Ids that came only from the back-edge — the form cannot unlink
  // these (they're not in contract.project_ids[]), so we hide the
  // unlink trash icon for them and surface a small note instead.
  const backEdgeOnlyIds = useMemo(() => {
    const forward = new Set(
      Array.isArray(contract?.project_ids) ? contract.project_ids : [],
    );
    const back = Array.isArray(contract?.projects)
      ? contract.projects.map((p) => p?.project_id).filter(Boolean)
      : [];
    return new Set(back.filter((id) => !forward.has(id)));
  }, [contract?.project_ids, contract?.projects]);

  // Seed the resolver with whatever the backend already returned so
  // we don't re-fetch ids the caller has names for.
  const initialProjectDocs = useMemo(() => {
    const out = {};
    for (const p of Array.isArray(contract?.projects) ? contract.projects : []) {
      if (p?.project_id) out[p.project_id] = p;
    }
    return out;
  }, [contract?.projects]);

  const openProjectDetail = useCallback(
    (pid) => {
      if (!pid) return;
      // ProjectsPortal parses /projects/projects/<id> -> detail tab.
      navigate(`/projects/projects/${pid}`);
    },
    [navigate],
  );

  // projectDocs maps project_id -> resolved project doc (or null if
  // we tried to fetch and it 404'd / RBAC'd). Loading state is
  // tracked separately because the GET requests run lazily as
  // linkedIds changes. We seed with the back-edge projects[] the
  // backend already enriched so the panel never re-fetches what it
  // was just given.
  const [projectDocs, setProjectDocs] = useState(initialProjectDocs);

  // Refresh the seeded docs when a new contract prop arrives.
  useEffect(() => {
    setProjectDocs(initialProjectDocs);
  }, [initialProjectDocs]);
  const [resolvingDocs, setResolvingDocs] = useState(false);
  const [error, setError] = useState(null);

  // Form state.
  const [showForm, setShowForm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // pickerMode = true uses the search box; false uses the plain id
  // input. Defaults to picker when canSeeProjects.
  const [pickerMode, setPickerMode] = useState(canSeeProjects);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [stagedIds, setStagedIds] = useState([]); // ids selected from the picker
  const [manualId, setManualId] = useState('');

  const searchDebounceRef = useRef(null);

  // Resolve project docs for currently-linked ids. Each id 404 or
  // RBAC-403s is stored as null in the map so we don't retry it.
  const resolveDocs = useCallback(async () => {
    if (!canSeeProjects || linkedIds.length === 0) {
      setProjectDocs({});
      return;
    }
    setResolvingDocs(true);
    setError(null);
    try {
      const missingIds = linkedIds.filter((id) => !(id in projectDocs));
      if (missingIds.length === 0) {
        return;
      }
      const results = await Promise.all(
        missingIds.map(async (id) => {
          try {
            const res = await api.get(`${API}/api/projects/projects/${id}`);
            if (!res.ok) return [id, null];
            const doc = await res.json();
            return [id, doc];
          } catch (_) {
            return [id, null];
          }
        }),
      );
      setProjectDocs((prev) => {
        const next = { ...prev };
        for (const [id, doc] of results) next[id] = doc;
        return next;
      });
    } finally {
      setResolvingDocs(false);
    }
    // We intentionally exclude projectDocs from the dep list — we
    // only re-resolve when canSeeProjects or linkedIds change. The
    // missingIds filter inside guarantees we never re-fetch an id
    // we've already tried.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canSeeProjects, linkedIds]);

  useEffect(() => {
    resolveDocs();
  }, [resolveDocs]);

  // Debounced project search via GET /api/projects?q=…
  useEffect(() => {
    if (!pickerMode || !canSeeProjects) return;
    const q = searchQuery.trim();
    if (q.length < 2) {
      setSearchResults([]);
      return;
    }
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    searchDebounceRef.current = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await api.get(
          `${API}/api/projects/projects?q=${encodeURIComponent(q)}&limit=10`,
        );
        if (!res.ok) {
          setSearchResults([]);
          return;
        }
        const data = await res.json();
        // The list endpoint returns either an array or { items: [...] }
        // depending on the deploy; tolerate both.
        const items = Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : [];
        setSearchResults(items);
      } catch (_) {
        setSearchResults([]);
      } finally {
        setSearching(false);
      }
    }, 250);
    return () => {
      if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    };
  }, [searchQuery, pickerMode, canSeeProjects]);

  const resetForm = () => {
    setStagedIds([]);
    setManualId('');
    setSearchQuery('');
    setSearchResults([]);
  };

  const stageProject = (project) => {
    const pid = project?.project_id;
    if (!pid || stagedIds.includes(pid) || linkedIds.includes(pid)) return;
    setStagedIds((prev) => [...prev, pid]);
    setSearchQuery('');
    setSearchResults([]);
  };

  const unstageProject = (pid) => {
    setStagedIds((prev) => prev.filter((p) => p !== pid));
  };

  const idsToSubmit = useMemo(() => {
    if (pickerMode) return stagedIds.filter(Boolean);
    const trimmed = (manualId || '').trim();
    return trimmed ? [trimmed] : [];
  }, [pickerMode, stagedIds, manualId]);

  const linkProjects = async (e) => {
    e?.preventDefault?.();
    if (!contractId || idsToSubmit.length === 0) return;
    setSubmitting(true);
    try {
      const res = await api.post(
        `${API}/api/contracts/${contractId}/projects`,
        { project_ids: idsToSubmit },
      );
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        toast.error(
          typeof detail?.detail === 'string'
            ? detail.detail
            : 'Failed to link project(s)',
        );
        return;
      }
      const updated = await res.json();
      toast.success(
        idsToSubmit.length === 1
          ? 'Project linked'
          : `${idsToSubmit.length} projects linked`,
      );
      resetForm();
      setShowForm(false);
      onContractUpdated?.(updated);
    } catch (err) {
      toast.error('Failed to link project(s)');
    } finally {
      setSubmitting(false);
    }
  };

  const unlinkProject = async (pid) => {
    if (!contractId || !pid) return;
    try {
      const res = await api.delete(
        `${API}/api/contracts/${contractId}/projects/${pid}`,
      );
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        toast.error(
          typeof detail?.detail === 'string'
            ? detail.detail
            : 'Failed to unlink project',
        );
        return;
      }
      const updated = await res.json();
      toast.success('Project unlinked');
      // Forget the cached project doc for this id so a re-link
      // freshly resolves it.
      setProjectDocs((prev) => {
        const next = { ...prev };
        delete next[pid];
        return next;
      });
      onContractUpdated?.(updated);
    } catch (err) {
      toast.error('Failed to unlink project');
    }
  };

  if (!contractId) return null;

  const hasLinkedProjects = linkedIds.length > 0;

  return (
    <div className="glass-card p-4" data-testid="contract-projects-panel">
      <div className="flex items-center justify-between gap-2 flex-wrap mb-3">
        <h3 className="text-sm font-semibold text-aegisaxiom-text flex items-center gap-2">
          <Briefcase size={16} className="text-violet-500" />
          Linked projects
          {hasLinkedProjects && (
            <span className="text-[11px] font-normal text-aegisaxiom-text-muted">
              ({linkedIds.length})
            </span>
          )}
        </h3>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            onClick={resolveDocs}
            disabled={resolvingDocs || !canSeeProjects}
            className="inline-flex items-center gap-1 px-2 py-1 rounded border border-aegisaxiom-border hover:bg-aegisaxiom-elevated text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text disabled:opacity-50"
            data-testid="contract-projects-refresh"
            title={canSeeProjects ? 'Refresh project details' : 'projects.project.view required'}
          >
            <RefreshCw className="w-3 h-3" />
            Refresh
          </button>
          {canEdit && (
            <button
              type="button"
              onClick={() => {
                setShowForm((v) => !v);
                resetForm();
              }}
              className="inline-flex items-center gap-1 px-2 py-1 rounded bg-violet-600 hover:bg-violet-500 text-white text-xs"
              data-testid="contract-projects-toggle-form"
            >
              <Plus className="w-3 h-3" />
              {showForm ? 'Cancel' : 'Link project'}
            </button>
          )}
        </div>
      </div>

      {error && (
        <div
          className="flex items-center gap-2 p-2 mb-3 bg-red-50 dark:bg-red-600/10 border border-red-300/40 rounded text-red-700 dark:text-red-300 text-xs"
          data-testid="contract-projects-error"
        >
          <AlertTriangle className="w-3.5 h-3.5" />
          <span>{error}</span>
        </div>
      )}

      {showForm && canEdit && (
        <form
          onSubmit={linkProjects}
          className="bg-aegisaxiom-elevated/40 border border-aegisaxiom-border rounded p-3 mb-3 space-y-2"
          data-testid="contract-projects-form"
        >
          {/* Picker / manual id toggle. Picker is only available when
              the caller has projects.project.view — otherwise the
              search route 403s and we fall back to manual id input. */}
          {canSeeProjects && (
            <div className="flex items-center gap-3 text-[11px] text-aegisaxiom-text-secondary">
              <label className="inline-flex items-center gap-1.5 cursor-pointer">
                <input
                  type="radio"
                  checked={pickerMode}
                  onChange={() => { setPickerMode(true); resetForm(); }}
                  data-testid="contract-projects-mode-picker"
                />
                Search
              </label>
              <label className="inline-flex items-center gap-1.5 cursor-pointer">
                <input
                  type="radio"
                  checked={!pickerMode}
                  onChange={() => { setPickerMode(false); resetForm(); }}
                  data-testid="contract-projects-mode-manual"
                />
                Paste project ID
              </label>
            </div>
          )}

          {pickerMode && canSeeProjects ? (
            <div className="space-y-2">
              <div className="relative">
                <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-aegisaxiom-text-muted" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search projects by name (min 2 chars)…"
                  className="aegisaxiom-input w-full text-xs pl-7"
                  data-testid="contract-projects-search-input"
                />
              </div>
              {searchQuery.trim().length >= 2 && (
                <div className="border border-aegisaxiom-border rounded max-h-48 overflow-y-auto bg-aegisaxiom-base">
                  {searching ? (
                    <p className="text-[11px] text-aegisaxiom-text-muted p-2 text-center">Searching…</p>
                  ) : searchResults.length === 0 ? (
                    <p className="text-[11px] text-aegisaxiom-text-muted p-2 text-center">No matches.</p>
                  ) : (
                    searchResults.map((p) => {
                      const alreadyLinked = linkedIds.includes(p.project_id);
                      const alreadyStaged = stagedIds.includes(p.project_id);
                      const disabled = alreadyLinked || alreadyStaged;
                      return (
                        <button
                          key={p.project_id}
                          type="button"
                          onClick={() => stageProject(p)}
                          disabled={disabled}
                          className="w-full text-left px-2 py-1.5 border-b border-aegisaxiom-border last:border-0 hover:bg-aegisaxiom-elevated/60 disabled:opacity-50 disabled:cursor-not-allowed"
                          data-testid={`contract-projects-search-result-${p.project_id}`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-xs text-aegisaxiom-text truncate">
                              {p.name || p.project_id}
                            </span>
                            <span className="flex items-center gap-1.5 text-[10px] text-aegisaxiom-text-muted">
                              <ProjectStatusBadge status={p.status} />
                              {alreadyLinked && <span>(linked)</span>}
                              {!alreadyLinked && alreadyStaged && <span>(staged)</span>}
                            </span>
                          </div>
                          <p className="text-[10px] text-aegisaxiom-text-muted font-mono">{p.project_id}</p>
                        </button>
                      );
                    })
                  )}
                </div>
              )}
              {stagedIds.length > 0 && (
                <div className="flex items-center gap-1 flex-wrap" data-testid="contract-projects-staged">
                  <span className="text-[11px] text-aegisaxiom-text-secondary">To link:</span>
                  {stagedIds.map((sid) => (
                    <span
                      key={sid}
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-300/40"
                    >
                      {sid}
                      <button
                        type="button"
                        onClick={() => unstageProject(sid)}
                        className="hover:text-violet-900 dark:hover:text-violet-100"
                        aria-label={`Remove ${sid} from selection`}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div>
              <label className="block text-[11px] text-aegisaxiom-text-secondary mb-0.5">
                Project ID *
              </label>
              <input
                type="text"
                value={manualId}
                onChange={(e) => setManualId(e.target.value)}
                placeholder="proj_…"
                className="aegisaxiom-input w-full text-xs font-mono"
                data-testid="contract-projects-manual-id"
              />
              {!canSeeProjects && (
                <p className="text-[10px] text-aegisaxiom-text-muted mt-0.5">
                  Project search requires the <code>projects.project.view</code> permission.
                  Paste an existing project ID to link it.
                </p>
              )}
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={() => { setShowForm(false); resetForm(); }}
              className="px-3 py-1.5 text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || idsToSubmit.length === 0}
              className="px-3 py-1.5 text-xs rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white"
              data-testid="contract-projects-submit"
            >
              {submitting
                ? 'Linking…'
                : idsToSubmit.length === 0
                  ? 'Link'
                  : idsToSubmit.length === 1
                    ? 'Link project'
                    : `Link ${idsToSubmit.length} projects`}
            </button>
          </div>
        </form>
      )}

      <div className="space-y-2">
        {!hasLinkedProjects ? (
          <p
            className="text-xs text-aegisaxiom-text-muted text-center py-4"
            data-testid="contract-projects-empty"
          >
            No projects linked to this contract.
          </p>
        ) : resolvingDocs && canSeeProjects && Object.keys(projectDocs).length === 0 ? (
          <p
            className="text-xs text-aegisaxiom-text-muted text-center py-4"
            data-testid="contract-projects-loading"
          >
            Loading project details…
          </p>
        ) : (
          linkedIds.map((pid) => {
            const doc = projectDocs[pid];
            const resolved = !!doc;
            const isBackEdgeOnly = backEdgeOnlyIds.has(pid);
            const canNavigate = canSeeProjects && pid;
            return (
              <div
                key={pid}
                className="bg-aegisaxiom-elevated/30 border border-aegisaxiom-border rounded p-2.5 flex items-center justify-between gap-2"
                data-testid={`contract-projects-row-${pid}`}
              >
                {/* Project name is now a button that routes to the
                    canonical project detail. Keyboard-accessible. The
                    button-as-link pattern matches how the Projects
                    list opens detail via /projects/projects/{id}. */}
                <button
                  type="button"
                  onClick={() => openProjectDetail(pid)}
                  disabled={!canNavigate}
                  className="min-w-0 flex-1 text-left group disabled:cursor-default"
                  data-testid={`contract-projects-open-${pid}`}
                  aria-label={`Open project ${resolved ? (doc.name || pid) : pid}`}
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <Link2 className="w-3 h-3 text-aegisaxiom-text-muted flex-shrink-0" />
                    <span
                      className={
                        canNavigate
                          ? 'text-xs text-aegisaxiom-text font-medium truncate group-hover:text-violet-400 group-hover:underline'
                          : 'text-xs text-aegisaxiom-text font-medium truncate'
                      }
                    >
                      {resolved ? (doc.name || pid) : pid}
                    </span>
                    {canNavigate && (
                      <ExternalLink className="w-3 h-3 text-aegisaxiom-text-muted group-hover:text-violet-400 flex-shrink-0" />
                    )}
                    {resolved && <ProjectStatusBadge status={doc.status} />}
                    {isBackEdgeOnly && (
                      <span
                        className="text-[10px] text-aegisaxiom-text-muted"
                        data-testid={`contract-projects-backedge-${pid}`}
                        title="Linked via project.contract_id (imported); not on contract.project_ids[]"
                      >
                        (imported link)
                      </span>
                    )}
                    {!canSeeProjects && (
                      <span className="text-[10px] text-aegisaxiom-text-muted">
                        (need <code>projects.project.view</code> for details)
                      </span>
                    )}
                  </div>
                  <p className="text-[10px] text-aegisaxiom-text-muted font-mono">{pid}</p>
                </button>
                {canEdit && !isBackEdgeOnly && (
                  <button
                    type="button"
                    onClick={() => unlinkProject(pid)}
                    className="text-aegisaxiom-text-muted hover:text-red-500 p-1 flex-shrink-0"
                    title="Unlink project"
                    data-testid={`contract-projects-unlink-${pid}`}
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
