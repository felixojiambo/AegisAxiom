import React, { useEffect, useMemo, useState } from 'react';
import {
  Plus,
  X,
  GripVertical,
  Trash2,
  Pencil,
  Check,
  Search,
  LayoutDashboard,
  Settings,
} from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { WIDGET_CATALOG, WIDGET_BY_ID } from '@/lib/financeWidgets';
import ConfirmModal from '@/components/shared/ConfirmModal';

const BASE = `${API_URL}/api/finance`;

// Size system — 4-state cycle: S → M → L → XL → S
const SIZE_TO_CLASS = {
  small: 'lg:col-span-1 md:col-span-1',
  medium: 'lg:col-span-2 md:col-span-2',
  large: 'lg:col-span-3 md:col-span-2',
  full: 'lg:col-span-4 md:col-span-2',
};
const SIZE_TO_HEIGHT = {
  small: 'h-[160px]',
  medium: 'h-[260px]',
  large: 'h-[260px]',
  full: 'h-[320px]',
};
const SIZE_LABELS = { small: 'S', medium: 'M', large: 'L', full: 'XL' };
const NEXT_SIZE = { small: 'medium', medium: 'large', large: 'full', full: 'small' };

export default function WorkspaceTab() {
  const [dashboards, setDashboards] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [renaming, setRenaming] = useState(null);
  const [renameValue, setRenameValue] = useState('');
  const [showAddWidget, setShowAddWidget] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState({ open: false, id: null });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  const refresh = async () => {
    try {
      const r = await api.get(`${BASE}/workspace/dashboards`);
      if (!r.ok) {
        setLoadError(`Failed to load workspace (${r.status})`);
        return;
      }
      const ds = (await r.json()).rows || [];
      setDashboards(ds);
      setLoadError(null);
      if (ds.length && !ds.find((d) => d.id === activeId)) {
        setActiveId(ds[0].id);
      }
    } catch (err) {
      setLoadError(err?.message || 'Failed to load workspace');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); }, []);

  const active = useMemo(
    () => dashboards.find((d) => d.id === activeId) || null,
    [dashboards, activeId],
  );

  const addDashboard = async () => {
    const r = await api.post(`${BASE}/workspace/dashboards`, {
      name: `Dashboard ${dashboards.length + 1}`,
    });
    if (!r.ok) { toast.error('Failed to create dashboard'); return; }
    const created = await r.json();
    await refresh();
    setActiveId(created.id);
    toast.success('Dashboard created');
  };

  const renameDashboard = async (id) => {
    if (!renameValue.trim()) { setRenaming(null); return; }
    await api.patch(`${BASE}/workspace/dashboards/${id}`, { name: renameValue.trim() });
    setRenaming(null);
    refresh();
  };

  const deleteDashboard = async () => {
    const id = confirmDelete.id;
    setConfirmDelete({ open: false, id: null });
    await api.delete(`${BASE}/workspace/dashboards/${id}`);
    toast.success('Dashboard deleted');
    refresh();
  };

  const updateFilters = async (next) => {
    await api.patch(`${BASE}/workspace/dashboards/${active.id}`, { filters: next });
    refresh();
  };

  const addWidget = async (widgetType) => {
    const widget = WIDGET_BY_ID[widgetType];
    if (!widget) return;
    await api.post(`${BASE}/workspace/dashboards/${active.id}/widgets`, {
      widget_type: widgetType,
      size: widget.defaultSize,
    });
    toast.success(`Added "${widget.name}"`);
    setShowAddWidget(false);
    refresh();
  };

  const removeWidget = async (wid) => {
    await api.delete(`${BASE}/workspace/dashboards/${active.id}/widgets/${wid}`);
    refresh();
  };

  const cycleSize = async (w) => {
    const next = NEXT_SIZE[w.size] || 'small';
    await api.patch(`${BASE}/workspace/dashboards/${active.id}/widgets/${w.id}`, { size: next });
    refresh();
  };

  const moveWidget = async (wid, dir) => {
    if (!active) return;
    const ordered = [...active.widgets].sort((a, b) => a.position - b.position);
    const idx = ordered.findIndex((w) => w.id === wid);
    if (idx < 0) return;
    const newIdx = idx + (dir === 'left' ? -1 : 1);
    if (newIdx < 0 || newIdx >= ordered.length) return;
    [ordered[idx], ordered[newIdx]] = [ordered[newIdx], ordered[idx]];
    const order = ordered.map((w) => w.id);
    await api.post(`${BASE}/workspace/dashboards/${active.id}/widgets/reorder`, { order });
    refresh();
  };

  if (loading) {
    return (
      <div className="py-20 text-center text-sm text-aegisaxiom-text-muted" data-testid="loading-workspace">
        Loading workspace…
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="glass-card p-12 text-center" data-testid="workspace-load-error">
        <div className="text-[16px] font-semibold text-aegisaxiom-text mb-2">Couldn't load workspace</div>
        <div className="text-[13px] text-aegisaxiom-text-secondary mb-6">{loadError}</div>
        <button onClick={refresh} className="btn-secondary inline-flex items-center gap-2 h-9 px-4 text-[13px]">
          Try again
        </button>
      </div>
    );
  }

  if (!dashboards.length) {
    return (
      <div className="glass-card p-12 text-center" data-testid="empty-workspace">
        <div className="text-[18px] font-semibold text-aegisaxiom-text mb-2">No dashboards yet</div>
        <div className="text-[13px] text-aegisaxiom-text-secondary mb-6 max-w-md mx-auto">
          Create your first dashboard to start composing finance widgets the way you want them.
        </div>
        <button
          onClick={addDashboard}
          data-testid="create-first-dashboard"
          className="btn-primary inline-flex items-center gap-2 h-9 px-4 text-[13px]"
        >
          <Plus className="h-3.5 w-3.5" />
          Create your first dashboard
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-5" data-testid="page-workspace">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <p className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-0.5">WORKSPACE</p>
          <h2 className="page-title">Custom Dashboards</h2>
          <p className="text-sm text-aegisaxiom-text-secondary mt-1">
            Build your own dashboards from a library of finance widgets.
          </p>
        </div>
        {active && (
          <div className="flex items-center gap-2">
            <button
              data-testid="filters-btn"
              onClick={() => setShowFilters((s) => !s)}
              className="btn-secondary h-9 px-3 flex items-center gap-2 text-[13px]"
            >
              <Settings className="h-3.5 w-3.5" />
              <span>Filters</span>
              <span className="font-mono text-[11px] text-aegisaxiom-text-muted">
                {active.filters?.period || 'YTD'}
              </span>
            </button>
            <button
              data-testid="add-widget-btn"
              onClick={() => setShowAddWidget(true)}
              className="btn-primary h-9 px-3 flex items-center gap-2 text-[13px]"
            >
              <Plus className="h-3.5 w-3.5" />
              <span>Add Widget</span>
            </button>
          </div>
        )}
      </div>

      {/* Dashboard tabs */}
      <div
        className="flex items-center gap-1 border-b border-aegisaxiom-border overflow-x-auto"
        data-testid="tabs-bar"
      >
        {dashboards.map((d) => (
          <div key={d.id} className="relative flex items-center group shrink-0">
            {renaming === d.id ? (
              <div className="flex items-center gap-1 px-2">
                <input
                  autoFocus
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') renameDashboard(d.id);
                    if (e.key === 'Escape') setRenaming(null);
                  }}
                  className="h-8 px-2 text-[13px] rounded-md border border-aegisaxiom-border bg-aegisaxiom-elevated text-aegisaxiom-text outline-none focus:border-violet-500"
                  data-testid={`rename-input-${d.id}`}
                />
                <button
                  onClick={() => renameDashboard(d.id)}
                  className="h-7 w-7 grid place-items-center rounded-md hover:bg-aegisaxiom-elevated text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition-colors"
                >
                  <Check className="h-3.5 w-3.5" />
                </button>
              </div>
            ) : (
              <button
                data-testid={`tab-${d.id}`}
                onClick={() => setActiveId(d.id)}
                className={`relative px-4 h-10 text-[13px] flex items-center gap-2 transition-colors ${
                  activeId === d.id
                    ? 'text-aegisaxiom-text font-medium'
                    : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
                }`}
              >
                <LayoutDashboard className="h-3.5 w-3.5" />
                <span>{d.name}</span>
                <span className="font-mono text-[10px] text-aegisaxiom-text-muted">{d.widgets?.length || 0}</span>
                {activeId === d.id && (
                  <span className="absolute bottom-0 left-2 right-2 h-[2px] bg-violet-500 rounded-t" />
                )}
              </button>
            )}
            {activeId === d.id && renaming !== d.id && (
              <div className="flex items-center gap-0.5 pr-2">
                <button
                  data-testid={`rename-${d.id}`}
                  onClick={() => { setRenaming(d.id); setRenameValue(d.name); }}
                  className="h-7 w-7 grid place-items-center rounded-md hover:bg-aegisaxiom-elevated text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition-colors"
                  title="Rename"
                >
                  <Pencil className="h-3 w-3" />
                </button>
                {dashboards.length > 1 && (
                  <button
                    data-testid={`delete-dash-${d.id}`}
                    onClick={() => setConfirmDelete({ open: true, id: d.id })}
                    className="h-7 w-7 grid place-items-center rounded-md hover:bg-red-500/10 text-aegisaxiom-text-muted hover:text-red-400 transition-colors"
                    title="Delete dashboard"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                )}
              </div>
            )}
          </div>
        ))}
        <button
          data-testid="add-dashboard-btn"
          onClick={addDashboard}
          className="h-10 px-3 text-[13px] text-aegisaxiom-text-secondary hover:text-aegisaxiom-text flex items-center gap-1 ml-1 shrink-0"
        >
          <Plus className="h-3.5 w-3.5" />
          <span>New tab</span>
        </button>
      </div>

      {/* Filters panel */}
      {active && showFilters && (
        <DashboardFilters dashboard={active} onChange={updateFilters} />
      )}

      {/* Widget grid */}
      {active && (
        active.widgets?.length ? (
          <div
            className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 auto-rows-min"
            data-testid="widget-grid"
          >
            {[...active.widgets]
              .sort((a, b) => a.position - b.position)
              .map((w) => {
                const def = WIDGET_BY_ID[w.widget_type];
                if (!def) {
                  return (
                    <div
                      key={w.id}
                      className="glass-card p-4 col-span-1 text-[12px] text-aegisaxiom-text-muted flex items-center gap-2"
                    >
                      <span>Unknown widget: {w.widget_type}</span>
                      <button onClick={() => removeWidget(w.id)} className="underline hover:text-aegisaxiom-text">
                        remove
                      </button>
                    </div>
                  );
                }
                const heightCls = SIZE_TO_HEIGHT[w.size] || 'h-[180px]';
                const colSpan = SIZE_TO_CLASS[w.size] || SIZE_TO_CLASS.small;
                return (
                  <div
                    key={w.id}
                    data-testid={`widget-${w.widget_type}`}
                    className={`glass-card p-4 relative group overflow-hidden ${colSpan} ${heightCls}`}
                  >
                    {/* Hover toolbar */}
                    <div className="absolute top-2 right-2 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity bg-aegisaxiom-elevated/90 backdrop-blur rounded-md border border-aegisaxiom-border z-10">
                      <button
                        data-testid={`move-left-${w.id}`}
                        onClick={() => moveWidget(w.id, 'left')}
                        className="h-7 w-7 grid place-items-center text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition-colors"
                        title="Move left"
                      >
                        <GripVertical className="h-3 w-3" />
                      </button>
                      <button
                        data-testid={`size-${w.id}`}
                        onClick={() => cycleSize(w)}
                        className="h-7 px-1.5 text-[10px] font-mono text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition-colors"
                        title={`Size: ${w.size} (click to cycle)`}
                      >
                        {SIZE_LABELS[w.size]}
                      </button>
                      <button
                        data-testid={`remove-${w.id}`}
                        onClick={() => removeWidget(w.id)}
                        className="h-7 w-7 grid place-items-center text-aegisaxiom-text-muted hover:text-red-400 transition-colors"
                        title="Remove"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                    <div className="h-full">
                      <def.Component widget={w} filters={active.filters} />
                    </div>
                  </div>
                );
              })}
          </div>
        ) : (
          <div className="glass-card p-12 text-center" data-testid="empty-dashboard">
            <div className="text-[18px] font-semibold text-aegisaxiom-text mb-2">This dashboard is empty</div>
            <div className="text-[13px] text-aegisaxiom-text-secondary mb-6 max-w-md mx-auto">
              Add finance widgets from the library to start tracking what matters to you.
            </div>
            <button
              onClick={() => setShowAddWidget(true)}
              data-testid="empty-add-widget"
              className="btn-primary inline-flex items-center gap-2 h-9 px-4 text-[13px]"
            >
              <Plus className="h-3.5 w-3.5" />
              Add your first widget
            </button>
          </div>
        )
      )}

      {/* Add widget modal */}
      {showAddWidget && (
        <AddWidgetModal onAdd={addWidget} onClose={() => setShowAddWidget(false)} active={active} />
      )}

      {/* Confirm delete dashboard */}
      <ConfirmModal
        isOpen={confirmDelete.open}
        title="Delete Dashboard"
        message="Delete this dashboard? All widgets on it will be removed."
        confirmText="Delete"
        variant="danger"
        onConfirm={deleteDashboard}
        onCancel={() => setConfirmDelete({ open: false, id: null })}
      />
    </div>
  );
}

function DashboardFilters({ dashboard, onChange }) {
  const [locations, setLocations] = useState([]);
  const [classes, setClasses] = useState([]);

  useEffect(() => {
    api.get(`${BASE}/gl/locations`).then(async (r) => {
      if (r.ok) { const d = await r.json(); setLocations(d.rows || []); }
    });
    api.get(`${BASE}/gl/classes`).then(async (r) => {
      if (r.ok) { const d = await r.json(); setClasses(d.rows || []); }
    });
  }, []);

  const f = dashboard.filters || {};
  const upd = (k, v) => onChange({ ...f, [k]: v });

  return (
    <div className="glass-card p-4 grid grid-cols-1 md:grid-cols-4 gap-3" data-testid="dashboard-filters">
      <div>
        <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-1.5">PERIOD</div>
        <select
          data-testid="filter-period"
          value={f.period || 'YTD'}
          onChange={(e) => upd('period', e.target.value)}
          className="w-full h-9 px-3 rounded-md border border-aegisaxiom-border bg-aegisaxiom-elevated text-[13px] text-aegisaxiom-text"
        >
          <option value="MTD">Month to date</option>
          <option value="QTD">Quarter to date</option>
          <option value="YTD">Year to date</option>
          <option value="ALL">All time</option>
        </select>
      </div>
      <div>
        <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-1.5">BASIS</div>
        <select
          data-testid="filter-basis"
          value={f.basis || 'accrual'}
          onChange={(e) => upd('basis', e.target.value)}
          className="w-full h-9 px-3 rounded-md border border-aegisaxiom-border bg-aegisaxiom-elevated text-[13px] text-aegisaxiom-text"
        >
          <option value="accrual">Accrual</option>
          <option value="cash">Cash</option>
        </select>
      </div>
      <div>
        <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-1.5">LOCATION</div>
        <select
          data-testid="filter-location"
          value={f.location || ''}
          onChange={(e) => upd('location', e.target.value)}
          className="w-full h-9 px-3 rounded-md border border-aegisaxiom-border bg-aegisaxiom-elevated text-[13px] text-aegisaxiom-text"
        >
          <option value="">All locations</option>
          {locations.map((l) => <option key={l} value={l}>{l}</option>)}
        </select>
      </div>
      <div>
        <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-1.5">CLASS</div>
        <select
          data-testid="filter-class"
          value={f.class_name || ''}
          onChange={(e) => upd('class_name', e.target.value)}
          className="w-full h-9 px-3 rounded-md border border-aegisaxiom-border bg-aegisaxiom-elevated text-[13px] text-aegisaxiom-text"
        >
          <option value="">All classes</option>
          {classes.map((l) => <option key={l} value={l}>{l}</option>)}
        </select>
      </div>
    </div>
  );
}

function AddWidgetModal({ onAdd, onClose, active }) {
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('All');
  const cats = ['All', ...Array.from(new Set(WIDGET_CATALOG.map((w) => w.category)))];
  const filtered = WIDGET_CATALOG.filter((w) => {
    if (category !== 'All' && w.category !== category) return false;
    if (search && !`${w.name} ${w.description}`.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });
  const installed = new Set((active?.widgets || []).map((w) => w.widget_type));

  return (
    <div
      className="fixed inset-0 z-40 grid place-items-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
      data-testid="add-widget-modal"
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl w-full max-w-3xl max-h-[80vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-6 py-4 border-b border-aegisaxiom-border flex items-center justify-between">
          <div>
            <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-1">
              WIDGET LIBRARY
            </div>
            <div className="text-[18px] font-semibold text-aegisaxiom-text">
              Add a widget to "{active?.name}"
            </div>
          </div>
          <button
            data-testid="close-modal"
            onClick={onClose}
            className="h-8 w-8 grid place-items-center rounded-md hover:bg-aegisaxiom-elevated text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-6 py-3 border-b border-aegisaxiom-border flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2 px-3 h-9 rounded-md bg-aegisaxiom-elevated flex-1 min-w-[160px]">
            <Search className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
            <input
              data-testid="widget-search"
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search widgets…"
              className="bg-transparent outline-none text-[13px] flex-1 text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted"
            />
          </div>
          <div className="flex items-center gap-1 flex-wrap">
            {cats.map((c) => (
              <button
                key={c}
                data-testid={`cat-${c}`}
                onClick={() => setCategory(c)}
                className={`h-8 px-3 text-[12px] rounded-md transition-colors ${
                  category === c
                    ? 'bg-violet-600 text-white'
                    : 'text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated hover:text-aegisaxiom-text'
                }`}
              >
                {c}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {filtered.map((w) => (
              <button
                key={w.id}
                data-testid={`add-${w.id}`}
                onClick={() => onAdd(w.id)}
                className="text-left p-3 rounded-lg border border-aegisaxiom-border hover:border-violet-500/40 hover:bg-aegisaxiom-elevated transition-colors flex items-start gap-3"
              >
                <div className="h-8 w-8 rounded-md bg-aegisaxiom-elevated grid place-items-center shrink-0">
                  <Plus className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[13px] font-medium text-aegisaxiom-text truncate">{w.name}</div>
                    {installed.has(w.id) && (
                      <span className="text-[10px] bg-emerald-500/15 text-emerald-400 px-1.5 py-0.5 rounded font-mono shrink-0">
                        ADDED
                      </span>
                    )}
                  </div>
                  <div className="text-[12px] text-aegisaxiom-text-secondary mt-0.5 line-clamp-2">
                    {w.description}
                  </div>
                  <div className="mt-1.5 flex items-center gap-2">
                    <span className="text-[10px] bg-aegisaxiom-elevated text-aegisaxiom-text-muted px-1.5 py-0.5 rounded font-mono">
                      {w.category}
                    </span>
                    <span className="text-[10px] text-aegisaxiom-text-muted font-mono">
                      default: {w.defaultSize}
                    </span>
                  </div>
                </div>
              </button>
            ))}
          </div>
          {filtered.length === 0 && (
            <div className="text-center py-12 text-[13px] text-aegisaxiom-text-muted">
              No widgets match your search.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
