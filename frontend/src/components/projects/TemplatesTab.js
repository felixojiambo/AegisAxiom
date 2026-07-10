import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Plus, Trash2, Copy, LayoutGrid, List } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';
import { useAuth } from '@/contexts/AuthContext';
import TabStrip from '@/components/shared/TabStrip';
import ConfirmModal from '@/components/shared/ConfirmModal';
import TemplateGallery from '@/components/projects/templates/TemplateGallery';
import InstantiateDialog from '@/components/projects/templates/InstantiateDialog';
import TemplatePreview from '@/components/projects/templates/TemplatePreview';

const SERVICE_LINES = [
  { value: 'ucaas', label: 'UCaaS' },
  { value: 'networking', label: 'Networking' },
  { value: 'cloud', label: 'Cloud' },
  { value: 'security', label: 'Security' },
  { value: 'ai_product', label: 'AI Product' },
  { value: 'other', label: 'Other' },
];

const METHODOLOGY_TABS = [
  { id: 'all', label: 'All' },
  { id: 'waterfall', label: 'Waterfall' },
  { id: 'agile', label: 'Agile' },
  { id: 'waves_threads', label: 'W&T' },
];

// ---- Inline editable text cell ----

function InlineText({ value, onSave }) {
  const [v, setV] = useState(value);
  const ref = useRef(null);
  useEffect(() => setV(value), [value]);
  return (
    <input
      ref={ref}
      value={v}
      onChange={(e) => setV(e.target.value)}
      onBlur={() => { if (v !== value && v.trim()) onSave(v.trim()); }}
      onKeyDown={(e) => { if (e.key === 'Enter') ref.current?.blur(); }}
      className="w-full h-7 px-1.5 rounded outline-none hover:bg-aegisaxiom-elevated focus:bg-aegisaxiom-elevated text-sm text-aegisaxiom-text transition-colors bg-transparent"
    />
  );
}

// ---- Template Editor ----

function EditorTab({ label, active, onClick, testid }) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testid}
      className={`h-8 px-3 text-xs font-medium transition-colors border-b-2 -mb-px ${
        active
          ? 'border-violet-500 text-aegisaxiom-text'
          : 'border-transparent text-aegisaxiom-text-muted hover:text-aegisaxiom-text'
      }`}
    >
      {label}
    </button>
  );
}

function OverviewTab({ template, onPatch }) {
  const [serviceLine, setServiceLine] = useState(template.service_line);
  useEffect(() => setServiceLine(template.service_line), [template.template_id ?? template._id, template.service_line]);

  return (
    <div className="space-y-4" data-testid="template-overview">
      <div>
        <p className="text-xs font-medium text-aegisaxiom-text-secondary mb-1">Service line</p>
        <select
          value={serviceLine}
          onChange={(e) => { setServiceLine(e.target.value); onPatch({ service_line: e.target.value }); }}
          data-testid="template-service-line"
          className="aegisaxiom-input w-full"
        >
          {SERVICE_LINES.map((s) => (<option key={s.value} value={s.value}>{s.label}</option>))}
        </select>
      </div>
    </div>
  );
}

function PhasesTab({ template, onPatch }) {
  const phases = template.default_phases ?? [];
  const [draft, setDraft] = useState('');

  const add = () => {
    const v = draft.trim();
    if (!v || phases.some((p) => p.name.toLowerCase() === v.toLowerCase())) return;
    onPatch({ default_phases: [...phases, { name: v }] });
    setDraft('');
  };
  const remove = (name) => onPatch({ default_phases: phases.filter((p) => p.name !== name) });

  return (
    <div data-testid="template-phases">
      <div className="flex flex-wrap gap-2 mb-3">
        {phases.map((p) => (
          <span key={p.name} className="inline-flex items-center gap-1 h-7 px-3 rounded-md bg-violet-500/15 text-violet-400 text-xs font-medium">
            {p.name}
            <button type="button" onClick={() => remove(p.name)} className="hover:text-violet-300" aria-label={`remove ${p.name}`}>×</button>
          </span>
        ))}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); add(); }} className="flex gap-2">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Add phase"
          className="aegisaxiom-input h-8 w-[240px] text-xs"
          data-testid="template-phase-add-input"
        />
        <button type="submit" className="btn-secondary h-8 px-3 text-xs" data-testid="template-phase-add">Add phase</button>
      </form>
    </div>
  );
}

function ColumnsTab({ template, onPatch }) {
  const cols = template.default_columns ?? [];
  const [draft, setDraft] = useState('');

  const slugify = (s) => s.trim().toLowerCase().replace(/\s+/g, '_');
  const add = () => {
    const v = draft.trim();
    if (!v) return;
    const id = slugify(v);
    if (cols.some((c) => c.id === id)) return;
    onPatch({ default_columns: [...cols, { id, name: v }] });
    setDraft('');
  };
  const remove = (id) => onPatch({ default_columns: cols.filter((c) => c.id !== id) });

  return (
    <div data-testid="template-columns">
      <div className="flex flex-wrap gap-2 mb-3">
        {cols.map((c) => (
          <span key={c.id} className="inline-flex items-center gap-1 h-7 px-3 rounded-md bg-violet-500/15 text-violet-400 text-xs font-medium">
            {c.name}
            <button type="button" onClick={() => remove(c.id)} className="hover:text-violet-300" aria-label={`remove ${c.name}`}>×</button>
          </span>
        ))}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); add(); }} className="flex gap-2">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Add column"
          className="aegisaxiom-input h-8 w-[240px] text-xs"
          data-testid="template-column-add-input"
        />
        <button type="submit" className="btn-secondary h-8 px-3 text-xs" data-testid="template-column-add">Add column</button>
      </form>
    </div>
  );
}

function TasksTab({ template, onPatch }) {
  const defs = template.task_definitions ?? [];
  const phases = (template.default_phases ?? []).map((p) => p.name);
  const cols = template.default_columns ?? [];

  const updateAt = (i, patch) => {
    const next = defs.map((d, idx) => (idx === i ? { ...d, ...patch } : d));
    onPatch({ task_definitions: next });
  };
  const removeAt = (i) => onPatch({ task_definitions: defs.filter((_, idx) => idx !== i) });
  const add = () => {
    const next = { title: 'New task', priority: 'medium' };
    if (template.project_type === 'waterfall' && phases[0]) next.phase = phases[0];
    else if (template.project_type === 'agile' && cols[0]) next.column_id = cols[0].id;
    onPatch({ task_definitions: [...defs, next] });
  };

  return (
    <div data-testid="template-tasks">
      <table className="w-full text-xs border border-aegisaxiom-border rounded-md overflow-hidden">
        <thead>
          <tr className="h-8 bg-aegisaxiom-elevated text-[10px] uppercase tracking-wide text-aegisaxiom-text-muted border-b border-aegisaxiom-border">
            <th className="text-left px-3 font-medium">Title</th>
            <th className="text-left px-3 font-medium">Priority</th>
            {template.project_type === 'waterfall' && <th className="text-left px-3 font-medium">Phase</th>}
            {template.project_type === 'agile' && <th className="text-left px-3 font-medium">Column</th>}
            <th className="w-8" />
          </tr>
        </thead>
        <tbody>
          {defs.map((d, i) => (
            <tr key={i} className="border-t border-aegisaxiom-border" data-testid={`template-task-row-${i}`}>
              <td className="px-3 py-1">
                <InlineText value={d.title} onSave={(v) => updateAt(i, { title: v })} />
              </td>
              <td className="px-3">
                <select
                  value={d.priority ?? 'medium'}
                  onChange={(e) => updateAt(i, { priority: e.target.value })}
                  className="aegisaxiom-input h-7 text-xs"
                >
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                  <option value="critical">critical</option>
                </select>
              </td>
              {template.project_type === 'waterfall' && (
                <td className="px-3">
                  <select
                    value={d.phase ?? ''}
                    onChange={(e) => updateAt(i, { phase: e.target.value })}
                    className="aegisaxiom-input h-7 text-xs"
                  >
                    {phases.map((p) => (<option key={p} value={p}>{p}</option>))}
                  </select>
                </td>
              )}
              {template.project_type === 'agile' && (
                <td className="px-3">
                  <select
                    value={d.column_id ?? ''}
                    onChange={(e) => updateAt(i, { column_id: e.target.value })}
                    className="aegisaxiom-input h-7 text-xs"
                  >
                    {cols.map((c) => (<option key={c.id} value={c.id}>{c.name}</option>))}
                  </select>
                </td>
              )}
              <td className="px-2 text-right">
                <button
                  type="button"
                  onClick={() => removeAt(i)}
                  className="h-7 w-7 inline-flex items-center justify-center rounded-md text-aegisaxiom-text-muted hover:text-red-400 hover:bg-red-500/10 transition-colors"
                  aria-label={`Remove task ${i + 1}`}
                  data-testid={`template-task-remove-${i}`}
                >
                  <Trash2 size={12} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button
        type="button"
        onClick={add}
        className="btn-secondary mt-3 h-8 px-3 text-xs"
        data-testid="template-task-add"
      >
        + Add task
      </button>
    </div>
  );
}

function TemplateEditor({ template, onPatch, onDelete, onDeselect, deleting }) {
  const id = template.template_id ?? template._id;
  const [activeTab, setActiveTab] = useState('overview');
  const [savedAt, setSavedAt] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const [name, setName] = useState(template.name);
  useEffect(() => setName(template.name), [id, template.name]);

  const patch = async (p) => {
    await onPatch(p);
    setSavedAt(new Date());
  };

  const editorTabs = useMemo(() => {
    const tabs = [{ id: 'overview', label: 'Overview' }];
    if (template.project_type === 'waterfall') tabs.push({ id: 'phases', label: 'Phases' });
    if (template.project_type === 'agile') tabs.push({ id: 'columns', label: 'Columns' });
    tabs.push({ id: 'tasks', label: 'Tasks' });
    return tabs;
  }, [template.project_type]);

  return (
    <div data-testid={`template-editor-${id}`}>
      {/* Editor header */}
      <div className="flex items-start gap-3 mb-5">
        <div className="flex-1 min-w-0">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => { if (name.trim() && name !== template.name) patch({ name: name.trim() }); }}
            data-testid="template-name-input"
            className="w-full text-xl font-semibold text-aegisaxiom-text bg-transparent outline-none rounded hover:bg-aegisaxiom-elevated focus:bg-aegisaxiom-elevated px-1 -mx-1 transition-colors"
          />
          <div className="flex items-center gap-2 mt-1">
            <span className="text-[11px] uppercase tracking-wide text-aegisaxiom-text-muted">
              {template.project_type === 'waves_threads' ? 'Waves & Threads' : template.project_type}
            </span>
            {template.is_system && (
              <span className="inline-flex items-center h-5 px-2 rounded bg-violet-500/15 text-violet-400 text-[10px] font-semibold">SYSTEM</span>
            )}
            {savedAt && (
              <span className="text-[11px] text-aegisaxiom-text-muted" data-testid="template-saved-indicator">
                Saved · {savedAt.toLocaleTimeString()}
              </span>
            )}
          </div>
        </div>
        <button
          type="button"
          disabled
          title="Duplicate — coming soon"
          data-testid="template-duplicate"
          className="h-8 px-3 rounded-md border border-aegisaxiom-border text-xs text-aegisaxiom-text-muted inline-flex items-center gap-1.5 cursor-not-allowed opacity-50"
        >
          <Copy size={12} />
          Duplicate
        </button>
        <button
          type="button"
          onClick={() => setConfirmDelete(true)}
          disabled={template.is_system || deleting}
          title={template.is_system ? 'System templates cannot be deleted' : 'Delete template'}
          data-testid="template-delete"
          className="h-8 px-3 rounded-md border border-aegisaxiom-border text-xs inline-flex items-center gap-1.5 text-red-400 hover:bg-red-500/10 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent transition-colors"
        >
          <Trash2 size={12} />
          Delete
        </button>
      </div>

      {/* Sub-tabs */}
      <div className="flex items-center gap-1 border-b border-aegisaxiom-border mb-4">
        {editorTabs.map((t) => (
          <EditorTab
            key={t.id}
            label={t.label}
            active={activeTab === t.id}
            onClick={() => setActiveTab(t.id)}
            testid={`template-tab-${t.id}`}
          />
        ))}
      </div>

      {activeTab === 'overview' && <OverviewTab template={template} onPatch={patch} />}
      {activeTab === 'phases' && template.project_type === 'waterfall' && <PhasesTab template={template} onPatch={patch} />}
      {activeTab === 'columns' && template.project_type === 'agile' && <ColumnsTab template={template} onPatch={patch} />}
      {activeTab === 'tasks' && <TasksTab template={template} onPatch={patch} />}

      <ConfirmModal
        open={confirmDelete}
        title="Delete template"
        message={`Delete "${template.name}"? This cannot be undone.`}
        confirmText="Delete"
        onConfirm={() => { setConfirmDelete(false); onDelete(); }}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}

// ---- New Template Modal ----

function NewTemplateModal({ onClose, onCreated }) {
  const [name, setName] = useState('');
  const [methodology, setMethodology] = useState('waterfall');
  const [serviceLine, setServiceLine] = useState('cloud');
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!name.trim()) { setError('Name is required.'); return; }
    setError(null);
    setSubmitting(true);
    try {
      const payload = {
        name: name.trim(),
        project_type: methodology,
        service_line: serviceLine,
        task_definitions: [],
      };
      if (methodology === 'waterfall') {
        payload.default_phases = [{ name: 'Plan' }, { name: 'Build' }, { name: 'Ship' }];
      }
      if (methodology === 'agile') {
        payload.default_columns = [
          { id: 'todo', name: 'To Do' },
          { id: 'in_progress', name: 'In Progress' },
          { id: 'done', name: 'Done' },
        ];
      }
      const res = await api.post(`${API_URL}/api/projects/templates`, payload);
      if (!res.ok) { const d = await res.json(); throw new Error(d?.detail ?? 'Failed'); }
      const created = await res.json();
      onCreated(created.template_id ?? created._id);
    } catch (e) {
      setError(e.message ?? "Couldn't create.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
      data-testid="new-template-modal"
    >
      <div className="w-full max-w-[480px] bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl">
        <div className="px-5 py-4 border-b border-aegisaxiom-border">
          <h2 className="text-base font-semibold text-aegisaxiom-text">New template</h2>
        </div>
        <div className="px-5 py-4 space-y-3">
          <div>
            <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">Name *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
              className="aegisaxiom-input w-full"
              data-testid="new-template-name"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">Methodology</label>
            <select
              value={methodology}
              onChange={(e) => setMethodology(e.target.value)}
              className="aegisaxiom-input w-full"
              data-testid="new-template-methodology"
            >
              <option value="waterfall">Waterfall</option>
              <option value="agile">Agile</option>
              <option value="waves_threads">Waves & Threads</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">Service line</label>
            <select
              value={serviceLine}
              onChange={(e) => setServiceLine(e.target.value)}
              className="aegisaxiom-input w-full"
              data-testid="new-template-service-line"
            >
              {SERVICE_LINES.map((s) => (<option key={s.value} value={s.value}>{s.label}</option>))}
            </select>
          </div>
          {error && <p className="text-xs text-red-400">{error}</p>}
        </div>
        <div className="px-5 py-3 border-t border-aegisaxiom-border flex items-center justify-end gap-2">
          <button type="button" onClick={onClose} className="btn-secondary text-sm">Cancel</button>
          <button
            type="button"
            onClick={submit}
            disabled={submitting}
            className="btn-primary inline-flex items-center gap-2 text-sm disabled:opacity-50"
            data-testid="new-template-submit"
          >
            {submitting && <div className="animate-spin w-3.5 h-3.5 rounded-full border-2 border-white border-t-transparent" />}
            Create
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- Main TemplatesTab ----

export default function TemplatesTab() {
  const perms = usePermissions();
  const { user: currentUser } = useAuth();
  const canCreate = perms.hasPermission('projects.template.create');
  const canEdit = perms.hasPermission('projects.template.edit');
  const canDelete = perms.hasPermission('projects.template.delete');

  const [viewMode, setViewMode] = useState('gallery'); // 'gallery' | 'editor'
  const [tab, setTab] = useState('all');
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState(null);
  const [selectedTemplate, setSelectedTemplate] = useState(null);
  const [templateLoading, setTemplateLoading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [instantiateTemplate, setInstantiateTemplate] = useState(null);
  const [previewId, setPreviewId] = useState(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);
  const [assignees, setAssignees] = useState([]);

  // Load PM-assignable users once on mount — used by the Instantiate dialog's
  // Project manager dropdown. Mirrors the same call CreateProjectWizard makes.
  useEffect(() => {
    let cancelled = false;
    api.get(`${API_URL}/api/projects/assignable-users?role=pm`)
      .then(async (res) => {
        if (!res.ok || cancelled) return;
        const data = await res.json();
        setAssignees(Array.isArray(data) ? data : data.items ?? []);
      })
      .catch(() => { /* dropdown will gracefully degrade to 'Unassigned' */ });
    return () => { cancelled = true; };
  }, []);

  const fetchTemplates = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get(`${API_URL}/api/projects/templates?limit=500`);
      if (!res.ok) {
        toast.error('Could not load templates.');
        return;
      }
      const data = await res.json();
      setTemplates(Array.isArray(data) ? data : data.items ?? []);
    } catch {
      toast.error('Could not load templates.');
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchTemplate = useCallback(async (id) => {
    setTemplateLoading(true);
    try {
      const res = await api.get(`${API_URL}/api/projects/templates/${id}`);
      if (!res.ok) throw new Error('Not found');
      const data = await res.json();
      setSelectedTemplate(data);
    } catch {
      setSelectedTemplate(null);
    } finally {
      setTemplateLoading(false);
    }
  }, []);

  useEffect(() => { fetchTemplates(); }, [fetchTemplates]);
  useEffect(() => {
    if (selectedId) fetchTemplate(selectedId);
    else setSelectedTemplate(null);
  }, [selectedId, fetchTemplate]);

  const filtered = useMemo(() => {
    if (tab === 'all') return templates;
    return templates.filter((t) => t.project_type === tab);
  }, [templates, tab]);

  const counts = useMemo(() => ({
    all: templates.length,
    waterfall: templates.filter((t) => t.project_type === 'waterfall').length,
    agile: templates.filter((t) => t.project_type === 'agile').length,
    waves_threads: templates.filter((t) => t.project_type === 'waves_threads').length,
  }), [templates]);

  const tabsWithCounts = METHODOLOGY_TABS.map((t) => ({
    ...t,
    badge: counts[t.id] || undefined,
    label: t.label,
  }));

  const handlePatch = async (patch) => {
    if (!selectedId || !canEdit) return;
    try {
      const res = await api.patch(`${API_URL}/api/projects/templates/${selectedId}`, patch);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const detail = typeof data?.detail === 'string'
          ? data.detail
          : data?.detail?.message ?? 'Could not save template change.';
        toast.error(detail);
        return;
      }
      const updated = await res.json();
      setSelectedTemplate(updated);
      setTemplates((prev) => prev.map((t) => {
        const tid = t.template_id ?? t._id;
        return tid === selectedId ? { ...t, ...updated } : t;
      }));
    } catch {
      toast.error('Could not save template change.');
    }
  };

  const handleDelete = async () => {
    if (!selectedId || !canDelete) return;
    setDeleting(true);
    try {
      const res = await api.delete(`${API_URL}/api/projects/templates/${selectedId}`);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const detail = typeof data?.detail === 'string'
          ? data.detail
          : data?.detail?.message ?? 'Could not delete template.';
        toast.error(detail);
        return;
      }
      toast.success('Template deleted');
      setTemplates((prev) => prev.filter((t) => (t.template_id ?? t._id) !== selectedId));
      setSelectedId(null);
      setSelectedTemplate(null);
    } catch {
      toast.error('Could not delete template.');
    } finally {
      setDeleting(false);
    }
  };

  const handleGalleryDelete = async (templateId) => {
    if (!canDelete) return;
    setDeleting(true);
    try {
      const res = await api.delete(`${API_URL}/api/projects/templates/${templateId}`);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const detail = typeof data?.detail === 'string'
          ? data.detail
          : data?.detail?.message ?? 'Could not delete template.';
        toast.error(detail);
        return;
      }
      toast.success('Template deleted');
      setTemplates((prev) => prev.filter((t) => (t.template_id ?? t._id) !== templateId));
    } catch {
      toast.error('Could not delete template.');
    } finally {
      setDeleting(false);
      setConfirmDeleteId(null);
    }
  };

  const handleGalleryUse = (templateId) => {
    const t = templates.find((tmpl) => (tmpl.template_id ?? tmpl._id) === templateId);
    if (t) setInstantiateTemplate(t);
  };

  const handleGalleryEdit = (templateId) => {
    setViewMode('editor');
    setSelectedId(templateId);
  };

  return (
    <section data-testid="templates-page">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-aegisaxiom-text" data-testid="templates-title">Templates</h2>
        <div className="flex items-center gap-2">
          {/* View mode toggle */}
          <div className="flex items-center rounded-md border border-aegisaxiom-border overflow-hidden">
            <button
              type="button"
              onClick={() => setViewMode('gallery')}
              title="Gallery view"
              data-testid="templates-view-gallery"
              className={`h-8 w-8 inline-flex items-center justify-center transition-colors ${
                viewMode === 'gallery' ? 'bg-violet-500/15 text-violet-400' : 'text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'
              }`}
            >
              <LayoutGrid size={14} />
            </button>
            <button
              type="button"
              onClick={() => setViewMode('editor')}
              title="Editor view"
              data-testid="templates-view-editor"
              className={`h-8 w-8 inline-flex items-center justify-center border-l border-aegisaxiom-border transition-colors ${
                viewMode === 'editor' ? 'bg-violet-500/15 text-violet-400' : 'text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'
              }`}
            >
              <List size={14} />
            </button>
          </div>
          {canCreate && (
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              data-testid="new-template-button"
              className="btn-primary inline-flex items-center gap-2 text-sm"
            >
              <Plus size={15} />
              New Template
            </button>
          )}
        </div>
      </div>

      <TabStrip
        tabs={tabsWithCounts}
        activeId={tab}
        onChange={setTab}
        variant="underline"
        className="mb-4"
      />

      {/* Gallery view */}
      {viewMode === 'gallery' && (
        <div data-testid="templates-gallery-view">
          <TemplateGallery
            templates={filtered}
            loading={loading}
            canEdit={canEdit}
            canDelete={canDelete}
            onPreview={(id) => setPreviewId(id)}
            onUse={handleGalleryUse}
            onEdit={handleGalleryEdit}
            onDelete={(id) => setConfirmDeleteId(id)}
          />
        </div>
      )}

      {/* Editor view (original list+editor layout) */}
      {viewMode === 'editor' && (
        <div className="grid grid-cols-1 md:grid-cols-[300px_1fr] gap-4 min-h-[60vh]" data-testid="templates-editor-view">
          {/* Left rail */}
          <div className="glass-card overflow-hidden" data-testid="templates-rail">
            {loading ? (
              <div className="p-6 flex justify-center">
                <div className="animate-spin w-6 h-6 rounded-full border-2 border-violet-500 border-t-transparent" />
              </div>
            ) : filtered.length === 0 ? (
              <p className="text-sm text-aegisaxiom-text-muted p-6 text-center">No templates.</p>
            ) : (
              <ul className="divide-y divide-aegisaxiom-border">
                {filtered.map((t) => {
                  const tid = t.template_id ?? t._id;
                  return (
                    <li key={tid}>
                      <button
                        type="button"
                        onClick={() => setSelectedId(tid)}
                        data-testid={`template-rail-${tid}`}
                        className={`w-full min-h-[44px] px-3 py-2 flex items-center gap-2 text-left transition-colors ${
                          selectedId === tid ? 'bg-violet-500/10' : 'hover:bg-aegisaxiom-elevated'
                        }`}
                      >
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-semibold text-aegisaxiom-text truncate">{t.name}</p>
                          <p className="text-[11px] text-aegisaxiom-text-muted truncate capitalize">
                            {t.service_line?.replace(/_/g, ' ')} · {t.task_definitions?.length ?? 0} tasks
                          </p>
                        </div>
                        {t.is_system && (
                          <span className="shrink-0 inline-flex items-center h-5 px-1.5 rounded bg-violet-500/15 text-violet-400 text-[9px] font-semibold">SYSTEM</span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* Right editor pane */}
          <div className="glass-card p-5 min-h-[60vh]" data-testid="template-editor">
            {selectedId ? (
              templateLoading ? (
                <div className="flex justify-center mt-20">
                  <div className="animate-spin w-7 h-7 rounded-full border-2 border-violet-500 border-t-transparent" />
                </div>
              ) : selectedTemplate ? (
                <TemplateEditor
                  template={selectedTemplate}
                  onPatch={handlePatch}
                  onDelete={handleDelete}
                  onDeselect={() => setSelectedId(null)}
                  deleting={deleting}
                />
              ) : (
                <p className="text-sm text-aegisaxiom-text-muted">Template not found.</p>
              )
            ) : (
              <div className="flex flex-col items-center justify-center h-full text-center">
                <p className="text-sm font-medium text-aegisaxiom-text">Select a template to edit</p>
                <p className="mt-1 text-xs text-aegisaxiom-text-muted max-w-[320px]">
                  System templates can be reshaped — phases, columns, tasks. Deletion is blocked for system templates.
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* New template modal */}
      {createOpen && (
        <NewTemplateModal
          onClose={() => setCreateOpen(false)}
          onCreated={(id) => {
            setCreateOpen(false);
            fetchTemplates().then(() => {
              setSelectedId(id);
              setViewMode('editor');
            });
          }}
        />
      )}

      {/* Instantiate dialog */}
      {instantiateTemplate && (
        <InstantiateDialog
          template={instantiateTemplate}
          assignees={assignees}
          currentUser={currentUser}
          onClose={() => setInstantiateTemplate(null)}
          onCreated={(projectId) => {
            setInstantiateTemplate(null);
            fetchTemplates();
          }}
        />
      )}

      {/* Standalone preview pane (gallery "Preview" button) */}
      {previewId && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onMouseDown={(e) => { if (e.target === e.currentTarget) setPreviewId(null); }}
          data-testid="template-preview-modal"
        >
          <div className="w-full max-w-2xl bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold text-aegisaxiom-text">Template preview</h2>
              <button
                type="button"
                onClick={() => setPreviewId(null)}
                className="btn-secondary text-xs"
                data-testid="template-preview-close"
              >
                Close
              </button>
            </div>
            <TemplatePreview templateId={previewId} startDate={null} />
          </div>
        </div>
      )}

      {/* Confirm delete (gallery) */}
      <ConfirmModal
        open={confirmDeleteId !== null}
        title="Delete template"
        message="Delete this template? This cannot be undone."
        confirmText="Delete"
        onConfirm={() => handleGalleryDelete(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </section>
  );
}
