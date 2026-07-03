import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '../../lib/apiConfig';
import ConfirmModal from '../shared/ConfirmModal';
import {
  FileText,
  Plus,
  Edit2,
  Trash2,
  X,
  Save,
  ChevronDown,
  ChevronRight,
  Briefcase,
  Wrench,
  Package,
  Layers,
} from 'lucide-react';

const API = API_URL;

// Static color classes — Tailwind cannot scan dynamic `text-${color}-400` strings,
// so we resolve them up-front from a closed set keyed by LOB color.
const ICON_COLOR_CLASS = {
  violet: 'text-violet-600 dark:text-violet-400',
  emerald: 'text-emerald-600 dark:text-emerald-400',
  amber: 'text-amber-600 dark:text-amber-400',
};

const LOBS = [
  {
    value: 'professional_services',
    label: 'Professional Services',
    short: 'PS',
    icon: Briefcase,
    color: 'violet',
  },
  { value: 'field_services', label: 'Field Services', short: 'FS', icon: Wrench, color: 'emerald' },
  { value: 'wholesale_mrr', label: 'Wholesale MRR', short: 'MRR', icon: Package, color: 'amber' },
];

const lobConfig = (value) => LOBS.find((l) => l.value === value) || LOBS[0];

export default function SOWMilestoneTemplates() {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState(null);
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });
  const [collapsed, setCollapsed] = useState({});

  useEffect(() => {
    load();
  }, []);

  const load = async () => {
    try {
      setLoading(true);
      const response = await api.get(`${API}/api/admin/sow-milestone-templates`);
      if (response.ok) {
        const data = await response.json();
        setTemplates(data.items || []);
      } else {
        toast.error('Failed to load templates');
      }
    } catch (error) {
      console.error('Error loading templates:', error);
      toast.error('Failed to load templates');
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = (template) => {
    setConfirmState({
      open: true,
      title: 'Delete Template',
      message: `Delete "${template.template_name}"? This cannot be undone.`,
      action: async () => {
        try {
          const response = await api.delete(
            `${API}/api/admin/sow-milestone-templates/${template.template_id}`,
          );
          if (response.ok) {
            toast.success('Template deleted');
            load();
          } else {
            toast.error('Failed to delete template');
          }
        } catch (error) {
          console.error('Error deleting template:', error);
          toast.error('Failed to delete template');
        }
      },
    });
  };

  // Group templates by LOB
  const grouped = LOBS.map((lob) => ({
    ...lob,
    templates: templates.filter((t) => t.line_of_business === lob.value),
  }));

  const toggleGroup = (lob) => setCollapsed((c) => ({ ...c, [lob]: !c[lob] }));

  return (
    <div data-testid="sow-milestone-templates-page">
      {/* Page header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-aegisaxiom-text mb-1.5 flex items-center gap-2">
            <Layers size={22} className="text-violet-600 dark:text-violet-400" />
            SOW Milestone Templates
          </h1>
          <p className="text-aegisaxiom-text-secondary text-sm leading-snug">
            Reusable milestone presets for the SOW Builder, organized by Line of Business
          </p>
        </div>
        <button
          onClick={() => {
            setEditing(null);
            setShowModal(true);
          }}
          className="flex items-center justify-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-700 text-white rounded-lg transition-colors w-full sm:w-auto shrink-0"
          data-testid="create-template-btn"
        >
          <Plus className="w-4 h-4" />
          Create Template
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-violet-500"></div>
        </div>
      ) : templates.length === 0 ? (
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-8 sm:p-12 text-center">
          <div className="w-14 h-14 rounded-full bg-violet-100 dark:bg-violet-600/15 flex items-center justify-center mx-auto mb-4">
            <FileText className="w-6 h-6 text-violet-600 dark:text-violet-300" />
          </div>
          <h3 className="text-base sm:text-lg font-semibold text-aegisaxiom-text mb-2">
            No Templates Yet
          </h3>
          <p className="text-aegisaxiom-text-secondary text-sm mb-6 max-w-sm mx-auto leading-relaxed">
            Create your first milestone template to streamline SOW building
          </p>
          <button
            onClick={() => {
              setEditing(null);
              setShowModal(true);
            }}
            className="inline-flex items-center gap-2 px-5 py-2.5 bg-violet-600 hover:bg-violet-700 text-white rounded-lg transition-colors text-sm font-medium"
          >
            <Plus className="w-4 h-4" />
            Create First Template
          </button>
        </div>
      ) : (
        <div>
          {grouped.map((group, gIdx) => {
            const Icon = group.icon;
            const isCollapsed = collapsed[group.value];
            const iconColorClass = ICON_COLOR_CLASS[group.color] || 'text-aegisaxiom-text-secondary';
            return (
              <div
                key={group.value}
                className={`bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg overflow-hidden ${gIdx < grouped.length - 1 ? 'mb-4 sm:mb-5' : ''}`}
                data-testid={`lob-group-${group.value}`}
              >
                <button
                  onClick={() => toggleGroup(group.value)}
                  className="w-full flex items-center justify-between gap-3 px-4 sm:px-5 py-3 hover:bg-aegisaxiom-elevated transition-colors text-left"
                  aria-expanded={!isCollapsed}
                >
                  <div className="flex items-center gap-2.5 sm:gap-3 min-w-0 flex-1">
                    {isCollapsed ? (
                      <ChevronRight className="w-4 h-4 text-aegisaxiom-text-secondary shrink-0" />
                    ) : (
                      <ChevronDown className="w-4 h-4 text-aegisaxiom-text-secondary shrink-0" />
                    )}
                    <Icon className={`w-5 h-5 shrink-0 ${iconColorClass}`} />
                    <h2 className="text-aegisaxiom-text font-semibold text-sm sm:text-base truncate">
                      {group.label}
                    </h2>
                    <span className="text-xs px-2 py-0.5 bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border rounded-full shrink-0">
                      {group.templates.length}
                    </span>
                  </div>
                </button>

                {!isCollapsed && (
                  <div className="p-4 sm:p-5 pt-2 border-t border-aegisaxiom-border">
                    {group.templates.length === 0 ? (
                      <p className="text-sm text-aegisaxiom-text-muted italic px-1 py-3">
                        No {group.short} templates yet
                      </p>
                    ) : (
                      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 sm:gap-4">
                        {group.templates.map((template) => {
                          const milestoneCount = (template.milestones || []).length;
                          const taskCount = (template.milestones || []).reduce(
                            (sum, m) => sum + (m.bullets?.length || 0),
                            0,
                          );
                          return (
                            <div
                              key={template.template_id}
                              className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4 sm:p-5 hover:border-aegisaxiom-border-hover transition-colors"
                              data-testid={`template-card-${template.template_id}`}
                            >
                              <div className="flex items-start justify-between gap-2 mb-3">
                                <div className="flex-1 min-w-0">
                                  <h3 className="font-semibold text-aegisaxiom-text text-sm sm:text-base mb-1 truncate">
                                    {template.template_name}
                                  </h3>
                                  {template.description && (
                                    <p className="text-xs sm:text-sm text-aegisaxiom-text-secondary line-clamp-2 leading-snug">
                                      {template.description}
                                    </p>
                                  )}
                                </div>
                                <div className="flex gap-0.5 shrink-0 -mr-1.5 -mt-1">
                                  <button
                                    onClick={() => {
                                      setEditing(template);
                                      setShowModal(true);
                                    }}
                                    className="p-2 rounded-md text-aegisaxiom-text-secondary hover:text-violet-600 dark:hover:text-violet-400 hover:bg-aegisaxiom-elevated transition-colors"
                                    aria-label={`Edit ${template.template_name}`}
                                    title="Edit"
                                  >
                                    <Edit2 className="w-4 h-4" />
                                  </button>
                                  <button
                                    onClick={() => handleDelete(template)}
                                    className="p-2 rounded-md text-aegisaxiom-text-secondary hover:text-red-600 dark:hover:text-red-400 hover:bg-aegisaxiom-elevated transition-colors"
                                    aria-label={`Delete ${template.template_name}`}
                                    title="Delete"
                                  >
                                    <Trash2 className="w-4 h-4" />
                                  </button>
                                </div>
                              </div>
                              <div className="text-xs text-aegisaxiom-text-muted border-t border-aegisaxiom-border pt-3 mt-3">
                                {milestoneCount} milestone{milestoneCount !== 1 ? 's' : ''}
                                {' \u2022 '}
                                {taskCount} task{taskCount !== 1 ? 's' : ''}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {showModal && (
        <TemplateFormModal
          template={editing}
          onClose={() => {
            setShowModal(false);
            setEditing(null);
          }}
          onSuccess={() => {
            setShowModal(false);
            setEditing(null);
            load();
          }}
        />
      )}

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

// ───────────────────────────────────────────────
// Template Form Modal — create/edit with milestone+bullet builder
// ───────────────────────────────────────────────

function TemplateFormModal({ template, onClose, onSuccess }) {
  const [form, setForm] = useState({
    template_name: '',
    description: '',
    line_of_business: 'professional_services',
    milestones: [],
  });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (template) {
      setForm({
        template_name: template.template_name || '',
        description: template.description || '',
        line_of_business: template.line_of_business || 'professional_services',
        milestones: (template.milestones || []).map((m) => ({
          milestone_name: m.milestone_name || '',
          bullets: (m.bullets || []).map((b) => ({
            bullet_text: b.bullet_text || '',
            estimated_hours: b.estimated_hours ?? '',
            deliverable: b.deliverable || '',
          })),
        })),
      });
    }
  }, [template]);

  const addMilestone = () => {
    setForm((f) => ({ ...f, milestones: [...f.milestones, { milestone_name: '', bullets: [] }] }));
  };

  const updateMilestone = (idx, field, value) => {
    setForm((f) => ({
      ...f,
      milestones: f.milestones.map((m, i) => (i === idx ? { ...m, [field]: value } : m)),
    }));
  };

  const removeMilestone = (idx) => {
    setForm((f) => ({ ...f, milestones: f.milestones.filter((_, i) => i !== idx) }));
  };

  const addBullet = (mIdx) => {
    setForm((f) => ({
      ...f,
      milestones: f.milestones.map((m, i) =>
        i === mIdx
          ? {
              ...m,
              bullets: [...m.bullets, { bullet_text: '', estimated_hours: '', deliverable: '' }],
            }
          : m,
      ),
    }));
  };

  const updateBullet = (mIdx, bIdx, field, value) => {
    setForm((f) => ({
      ...f,
      milestones: f.milestones.map((m, i) =>
        i === mIdx
          ? { ...m, bullets: m.bullets.map((b, j) => (j === bIdx ? { ...b, [field]: value } : b)) }
          : m,
      ),
    }));
  };

  const removeBullet = (mIdx, bIdx) => {
    setForm((f) => ({
      ...f,
      milestones: f.milestones.map((m, i) =>
        i === mIdx ? { ...m, bullets: m.bullets.filter((_, j) => j !== bIdx) } : m,
      ),
    }));
  };

  const handleSave = async () => {
    if (!form.template_name.trim()) {
      toast.error('Template name is required');
      return;
    }
    if (form.milestones.length === 0) {
      toast.error('Add at least one milestone');
      return;
    }
    if (form.milestones.some((m) => !m.milestone_name.trim())) {
      toast.error('All milestones need a name');
      return;
    }

    try {
      setSaving(true);
      const payload = {
        template_name: form.template_name.trim(),
        description: form.description.trim() || null,
        line_of_business: form.line_of_business,
        milestones: form.milestones.map((m) => ({
          milestone_name: m.milestone_name.trim(),
          bullets: m.bullets
            .filter((b) => b.bullet_text.trim())
            .map((b) => ({
              bullet_text: b.bullet_text.trim(),
              estimated_hours: b.estimated_hours === '' ? null : parseFloat(b.estimated_hours),
              deliverable: b.deliverable.trim() || null,
            })),
        })),
      };

      const response = template
        ? await api.put(`${API}/api/admin/sow-milestone-templates/${template.template_id}`, payload)
        : await api.post(`${API}/api/admin/sow-milestone-templates`, payload);

      if (response.ok) {
        toast.success(template ? 'Template updated' : 'Template created');
        onSuccess();
      } else {
        const err = await response.json();
        toast.error(err.detail || 'Failed to save template');
      }
    } catch (error) {
      console.error('Error saving template:', error);
      toast.error('Failed to save template');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-3xl max-h-[90vh] flex flex-col">
        <div className="border-b border-aegisaxiom-border p-6 flex items-center justify-between">
          <h2 className="text-xl font-bold text-aegisaxiom-text">
            {template ? 'Edit Milestone Template' : 'Create Milestone Template'}
          </h2>
          <button
            onClick={onClose}
            className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
          >
            <X className="w-6 h-6" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          <div>
            <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
              Template Name *
            </label>
            <input
              type="text"
              value={form.template_name}
              onChange={(e) => setForm((f) => ({ ...f, template_name: e.target.value }))}
              placeholder="e.g., Standard Network Cutover"
              className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
              Description
            </label>
            <textarea
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              placeholder="When to use this template..."
              rows={2}
              className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 resize-none"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
              Line of Business *
            </label>
            <div className="grid grid-cols-3 gap-3">
              {LOBS.map((lob) => {
                const Icon = lob.icon;
                const isSelected = form.line_of_business === lob.value;
                return (
                  <button
                    key={lob.value}
                    type="button"
                    onClick={() => setForm((f) => ({ ...f, line_of_business: lob.value }))}
                    className={`flex items-center gap-2 px-4 py-3 border-2 rounded-lg transition ${
                      isSelected
                        ? 'bg-violet-50 dark:bg-violet-600/20 border-violet-500 text-white'
                        : 'bg-aegisaxiom-surface border-aegisaxiom-border text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover'
                    }`}
                  >
                    <Icon
                      className={`w-5 h-5 ${isSelected ? 'text-violet-600 dark:text-violet-400' : 'text-aegisaxiom-text-muted'}`}
                    />
                    <span className="font-medium text-sm">{lob.short}</span>
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <div className="flex items-center justify-between mb-3">
              <label className="text-sm font-medium text-aegisaxiom-text-secondary">Milestones *</label>
              <button
                onClick={addMilestone}
                className="flex items-center gap-1 px-3 py-1.5 text-xs bg-violet-600 hover:bg-violet-500 text-white rounded transition"
              >
                <Plus className="w-3 h-3" />
                Add Milestone
              </button>
            </div>

            {form.milestones.length === 0 ? (
              <div className="border border-dashed border-aegisaxiom-border rounded-lg p-6 text-center">
                <p className="text-sm text-aegisaxiom-text-muted">
                  No milestones yet — click "Add Milestone" to start
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                {form.milestones.map((m, mIdx) => (
                  <div
                    key={mIdx}
                    className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4"
                  >
                    <div className="flex items-center gap-2 mb-3">
                      <span className="text-xs text-aegisaxiom-text-muted font-mono">M{mIdx + 1}</span>
                      <input
                        type="text"
                        value={m.milestone_name}
                        onChange={(e) => updateMilestone(mIdx, 'milestone_name', e.target.value)}
                        placeholder="Milestone name (e.g., Discovery & Planning)"
                        className="flex-1 px-3 py-1.5 bg-aegisaxiom-base border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500"
                      />
                      <button
                        onClick={() => removeMilestone(mIdx)}
                        className="p-1.5 text-aegisaxiom-text-secondary hover:text-red-600 dark:text-red-400 transition"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>

                    <div className="space-y-2 ml-6">
                      {m.bullets.map((b, bIdx) => (
                        <div key={bIdx} className="flex items-start gap-2">
                          <span className="text-xs text-aegisaxiom-text-muted font-mono mt-2">•</span>
                          <div className="flex-1 grid grid-cols-12 gap-2">
                            <input
                              type="text"
                              value={b.bullet_text}
                              onChange={(e) =>
                                updateBullet(mIdx, bIdx, 'bullet_text', e.target.value)
                              }
                              placeholder="Task description"
                              className="col-span-7 px-2 py-1.5 bg-aegisaxiom-base border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500"
                            />
                            <input
                              type="number"
                              value={b.estimated_hours}
                              onChange={(e) =>
                                updateBullet(mIdx, bIdx, 'estimated_hours', e.target.value)
                              }
                              placeholder="Hrs"
                              step="0.5"
                              className="col-span-2 px-2 py-1.5 bg-aegisaxiom-base border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500"
                            />
                            <input
                              type="text"
                              value={b.deliverable}
                              onChange={(e) =>
                                updateBullet(mIdx, bIdx, 'deliverable', e.target.value)
                              }
                              placeholder="Deliverable"
                              className="col-span-3 px-2 py-1.5 bg-aegisaxiom-base border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500"
                            />
                          </div>
                          <button
                            onClick={() => removeBullet(mIdx, bIdx)}
                            className="p-1 text-aegisaxiom-text-muted hover:text-red-600 dark:text-red-400 transition"
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </div>
                      ))}
                      <button
                        onClick={() => addBullet(mIdx)}
                        className="text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 transition ml-4"
                      >
                        + Add task
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="border-t border-aegisaxiom-border p-6 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg transition"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 px-6 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg transition disabled:opacity-50"
          >
            <Save className="w-4 h-4" />
            {saving ? 'Saving...' : template ? 'Update Template' : 'Create Template'}
          </button>
        </div>
      </div>
    </div>
  );
}
