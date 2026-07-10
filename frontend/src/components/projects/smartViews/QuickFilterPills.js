/**
 * QuickFilterPills — horizontal strip of clickable filter chips.
 *
 * Interface contract (published to Thread 2A):
 *
 *   <QuickFilterPills
 *     view="board"              // 'board' | 'backlog' | 'list'
 *     tasks={tasks}             // full task array from parent
 *     projectId={projectId}     // current project context (localStorage key)
 *     onFilterChange={fn}       // (filteredTaskIds: Set<string> | null) => void
 *   />
 *
 * Additional (internal / design-doc §5.3) props accepted but not required:
 *   savedViews, onSaveView, onLoadView, onDeleteView
 *
 * Design doc: .waves/projects-v1.2-smart-views-design.md §5.3
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Bookmark, BookmarkCheck, ChevronDown, Trash2, X } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/hooks/usePermissions';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { applyPills } from './applyPills';
import {
  DEFAULT_ACTIVE,
  deriveRole,
  PILLS,
  visiblePillIds,
} from './pillRegistry';
import {
  createSavedView,
  deleteSavedView,
  listSavedViews,
} from './savedViewsApi';
import {
  shouldUseServerSideFiltering,
  buildServerQueryFromPills,
} from './serverSideMode';

// ---------------------------------------------------------------------------
// localStorage helpers
// ---------------------------------------------------------------------------
const LS_PREFIX = 'aegisaxiom_smart_filters_';

function lsKey(projectId, view) {
  return `${LS_PREFIX}project_${projectId}_${view}`;
}

function loadActivePills(projectId, view, defaultPills) {
  try {
    const raw = localStorage.getItem(lsKey(projectId, view));
    if (!raw) return defaultPills;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed.active_pills)) return parsed.active_pills;
  } catch {
    // fall through
  }
  return defaultPills;
}

function saveActivePills(projectId, view, pillIds) {
  try {
    localStorage.setItem(
      lsKey(projectId, view),
      JSON.stringify({ active_pills: pillIds, updated_at: new Date().toISOString() }),
    );
  } catch {
    // Ignore storage errors (private browsing, quota)
  }
}

// ---------------------------------------------------------------------------
// Save-view modal
// ---------------------------------------------------------------------------
function SaveViewModal({ activePillIds, view, projectId, onSave, onClose }) {
  const [name, setName] = useState('');
  const [scope, setScope] = useState('global');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const inputRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  async function handleSubmit(e) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) { setError('Name is required'); return; }
    if (trimmed.length > 80) { setError('Name must be 80 characters or fewer'); return; }
    setSaving(true);
    setError('');
    try {
      await onSave({
        name: trimmed,
        scope,
        project_id: scope === 'project' ? projectId : null,
        view_type: view,
        pills: activePillIds,
        is_default: false,
      });
      onClose();
    } catch (err) {
      setError(err.message || 'Failed to save view');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      role="dialog"
      aria-modal="true"
      aria-label="Save view"
    >
      <div className="glass-card w-full max-w-sm p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-aegisaxiom-text">Save view</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="block text-xs text-aegisaxiom-text-muted mb-1" htmlFor="sv-name">
              Name
            </label>
            <input
              ref={inputRef}
              id="sv-name"
              type="text"
              className="w-full bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-md px-3 py-2 text-sm text-aegisaxiom-text focus:outline-none focus:ring-1 focus:ring-violet-500"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={80}
              placeholder="e.g. My weekly review"
            />
          </div>
          <div>
            <label className="block text-xs text-aegisaxiom-text-muted mb-1" htmlFor="sv-scope">
              Scope
            </label>
            <select
              id="sv-scope"
              className="w-full bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-md px-3 py-2 text-sm text-aegisaxiom-text focus:outline-none focus:ring-1 focus:ring-violet-500"
              value={scope}
              onChange={(e) => setScope(e.target.value)}
            >
              <option value="global">Global (all projects)</option>
              <option value="project">This project only</option>
            </select>
          </div>
          {error && <p className="text-xs text-red-400">{error}</p>}
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="btn-secondary text-xs px-3 py-1.5"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="btn-primary text-xs px-3 py-1.5 disabled:opacity-60"
            >
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Saved-views dropdown
// ---------------------------------------------------------------------------
function SavedViewsMenu({ savedViews, onLoad, onDelete, onClose }) {
  const menuRef = useRef(null);

  // Close on outside click
  useEffect(() => {
    function handler(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) onClose();
    }
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);

  return (
    <div
      ref={menuRef}
      className="absolute right-0 top-full mt-1 z-40 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg shadow-lg min-w-[200px] py-1"
      role="menu"
    >
      {savedViews.length === 0 && (
        <p className="px-3 py-2 text-xs text-aegisaxiom-text-muted">No saved views yet</p>
      )}
      {savedViews.map((sv) => (
        <div
          key={sv.saved_view_id}
          className="flex items-center gap-2 px-3 py-1.5 hover:bg-aegisaxiom-elevated group"
          data-testid={`quick-filter-saved-view-${sv.saved_view_id}`}
        >
          <button
            type="button"
            className="flex-1 text-left text-xs text-aegisaxiom-text truncate"
            onClick={() => { onLoad(sv); onClose(); }}
            role="menuitem"
          >
            {sv.name}
            {sv.is_default && (
              <span className="ml-1 text-violet-400 text-[10px]">(default)</span>
            )}
          </button>
          <button
            type="button"
            onClick={() => onDelete(sv.saved_view_id)}
            className="opacity-0 group-hover:opacity-100 text-aegisaxiom-text-muted hover:text-red-400 transition-opacity"
            aria-label={`Delete saved view ${sv.name}`}
          >
            <Trash2 size={12} />
          </button>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export default function QuickFilterPills({ view, tasks, projectId, onFilterChange }) {
  const { user } = useAuth();
  const { hasPermission } = usePermissions();
  const role = deriveRole(hasPermission);
  const availablePillIds = visiblePillIds(role, view);
  const defaultActive = DEFAULT_ACTIVE[role]?.[view] ?? [];

  const [activePillIds, setActivePillIds] = useState(() =>
    loadActivePills(projectId, view, defaultActive),
  );
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [showMenu, setShowMenu] = useState(false);
  const [savedViews, setSavedViews] = useState([]);
  const [loadingViews, setLoadingViews] = useState(false);
  const [serverFiltering, setServerFiltering] = useState(false);

  const project = null; // project context not passed at pill level — host view provides it via tasks

  // Build ctx for applyPills
  const ctx = {
    currentUserId: user?.user_id,
    now: new Date(),
    project,
  };

  // Recompute filter whenever active pills or tasks change.
  // When above the 500-task threshold, bypass client-side applyPills and fetch
  // filtered task ids from the server instead.
  useEffect(() => {
    const taskCount = (tasks ?? []).length;
    const useServer = shouldUseServerSideFiltering(taskCount, activePillIds.length);
    setServerFiltering(useServer);

    if (!useServer) {
      // Client-side path (< 500 tasks or no active pills)
      const result = applyPills(tasks ?? [], activePillIds, ctx);
      onFilterChange?.(result);
      return;
    }

    // Server-side path: translate pills → query params → fetch task ids
    const pillParams = buildServerQueryFromPills(activePillIds, ctx);
    const qs = new URLSearchParams({ project_id: projectId, ...pillParams }).toString();

    api.get(`${API_URL}/api/projects/tasks?${qs}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        const items = Array.isArray(data) ? data : (data.items ?? data.tasks ?? []);
        const ids = new Set(items.map((t) => t.task_id ?? t._id ?? t.id).filter(Boolean));
        onFilterChange?.(ids);
      })
      .catch(() => {
        // Fall back to client-side on error
        setServerFiltering(false);
        onFilterChange?.(applyPills(tasks ?? [], activePillIds, ctx));
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePillIds, tasks]);

  // Persist to localStorage when active pills change
  useEffect(() => {
    saveActivePills(projectId, view, activePillIds);
  }, [activePillIds, projectId, view]);

  function togglePill(pillId) {
    setActivePillIds((prev) =>
      prev.includes(pillId) ? prev.filter((id) => id !== pillId) : [...prev, pillId],
    );
  }

  function clearAll() {
    setActivePillIds([]);
  }

  // Fetch saved views when the "..." menu opens
  async function openMenu() {
    setShowMenu(true);
    if (loadingViews) return;
    setLoadingViews(true);
    try {
      const data = await listSavedViews({ view_type: view, project_id: projectId });
      setSavedViews(data.items ?? []);
    } catch {
      // Non-fatal — leave savedViews empty
    } finally {
      setLoadingViews(false);
    }
  }

  function loadView(sv) {
    setActivePillIds(sv.pills ?? []);
  }

  async function handleSaveView(body) {
    const created = await createSavedView(body);
    setSavedViews((prev) => [created, ...prev]);
  }

  async function handleDeleteView(savedViewId) {
    try {
      await deleteSavedView(savedViewId);
      setSavedViews((prev) => prev.filter((sv) => sv.saved_view_id !== savedViewId));
    } catch {
      // Non-fatal toast would be ideal; keep it silent for now
    }
  }

  if (availablePillIds.length === 0) return null;

  return (
    <>
      <div
        className="flex items-center gap-1.5 overflow-x-auto scrollbar-none"
        data-testid="quick-filter-pills"
        role="group"
        aria-label="Quick filters"
      >
        {availablePillIds.map((pillId) => {
          const pill = PILLS[pillId];
          const isActive = activePillIds.includes(pillId);
          return (
            <button
              key={pillId}
              type="button"
              onClick={() => togglePill(pillId)}
              data-testid={`quick-filter-pill-${pillId}`}
              data-active={isActive ? 'true' : undefined}
              title={pill.tooltip}
              aria-pressed={isActive}
              className={[
                'inline-flex items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors border',
                isActive
                  ? 'bg-violet-100 dark:bg-violet-600/20 border-violet-400 dark:border-violet-500/60 text-violet-700 dark:text-violet-300'
                  : 'bg-aegisaxiom-elevated border-aegisaxiom-border text-aegisaxiom-text-muted hover:border-aegisaxiom-text-muted hover:text-aegisaxiom-text',
              ].join(' ')}
            >
              {pill.label}
            </button>
          );
        })}

        {activePillIds.length > 0 && (
          <>
            <button
              type="button"
              onClick={clearAll}
              className="inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition-colors"
              aria-label="Clear all filters"
            >
              <X size={11} />
              Clear
            </button>
            {serverFiltering && (
              <span
                className="whitespace-nowrap text-[10px] text-violet-400/70 select-none"
                title="Filter applied server-side (project has >500 tasks)"
              >
                server-filtered
              </span>
            )}
          </>
        )}

        <div className="flex items-center gap-1 ml-auto shrink-0">
          <button
            type="button"
            onClick={() => setShowSaveModal(true)}
            data-testid="quick-filter-save"
            className="inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs text-aegisaxiom-text-muted hover:text-aegisaxiom-text border border-transparent hover:border-aegisaxiom-border transition-colors"
            title="Save current filter as a view"
          >
            <Bookmark size={11} />
            Save...
          </button>

          <div className="relative">
            <button
              type="button"
              onClick={openMenu}
              className="inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-xs text-aegisaxiom-text-muted hover:text-aegisaxiom-text border border-transparent hover:border-aegisaxiom-border transition-colors"
              aria-label="Manage saved views"
              aria-expanded={showMenu}
            >
              <ChevronDown size={12} />
            </button>
            {showMenu && (
              <SavedViewsMenu
                savedViews={savedViews}
                onLoad={loadView}
                onDelete={handleDeleteView}
                onClose={() => setShowMenu(false)}
              />
            )}
          </div>
        </div>
      </div>

      {showSaveModal && (
        <SaveViewModal
          activePillIds={activePillIds}
          view={view}
          projectId={projectId}
          onSave={handleSaveView}
          onClose={() => setShowSaveModal(false)}
        />
      )}
    </>
  );
}
