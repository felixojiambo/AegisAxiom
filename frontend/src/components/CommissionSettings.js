import React, { useState, useEffect } from 'react';
import { DollarSign, Plus, Edit2, Trash2, Save, X, Lightbulb } from 'lucide-react';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import API_URL from '../lib/apiConfig';
import ConfirmModal from './shared/ConfirmModal';

const API = API_URL;

const EMPTY_TEMPLATE = {
  name: '',
  tier1_percentage: 10,
  tier2_percentage: 5,
  tier3_percentage: 15,
  cost_type: 'percentage',
  cost_default: 50,
  is_active: true,
  tier1_default_user: '',
  tier2_default_user: '',
  tier3_default_user: '',
};

export default function CommissionSettings() {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(false);
  const [initialLoad, setInitialLoad] = useState(true);
  const [editing, setEditing] = useState(null); // template_id or 'new'
  const [form, setForm] = useState(EMPTY_TEMPLATE);
  const [users, setUsers] = useState([]);
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });

  useEffect(() => {
    loadTemplates();
    loadUsers();
  }, []);

  const loadTemplates = async () => {
    try {
      const res = await api.get(`${API}/api/commission-management/templates`);
      if (res.ok) {
        const data = await res.json();
        setTemplates(data.items || []);
      }
    } catch (err) {
      toast.error('Failed to load templates');
    } finally {
      setInitialLoad(false);
    }
  };

  const loadUsers = async () => {
    try {
      const res = await api.get(`${API}/api/admin/users`);
      if (res.ok) {
        const data = await res.json();
        setUsers(data);
      }
    } catch (err) {
      console.error('Failed to load users');
    }
  };

  const startEdit = (template) => {
    setEditing(template.template_id);
    setForm({
      name: template.name,
      tier1_percentage: template.tier1_percentage,
      tier2_percentage: template.tier2_percentage,
      tier3_percentage: template.tier3_percentage,
      cost_type: template.cost_type,
      cost_default: template.cost_default,
      is_active: template.is_active,
      tier1_default_user: template.tier1_default_user || '',
      tier2_default_user: template.tier2_default_user || '',
      tier3_default_user: template.tier3_default_user || '',
    });
  };

  const startNew = () => {
    setEditing('new');
    setForm(EMPTY_TEMPLATE);
  };

  const cancelEdit = () => {
    setEditing(null);
    setForm(EMPTY_TEMPLATE);
  };

  const saveTemplate = async () => {
    if (!form.name) {
      toast.error('Template name is required');
      return;
    }

    setLoading(true);
    try {
      const url =
        editing === 'new'
          ? `${API}/api/commission-management/templates`
          : `${API}/api/commission-management/templates/${editing}`;

      const res = editing === 'new' ? await api.post(url, form) : await api.put(url, form);

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to save template');
      }

      toast.success(editing === 'new' ? 'Template created' : 'Template updated');
      setEditing(null);
      setForm(EMPTY_TEMPLATE);
      loadTemplates();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };

  const deleteTemplate = (templateId) => {
    setConfirmState({
      open: true,
      title: 'Delete Commission Template',
      message: 'Are you sure you want to delete this template? This action cannot be undone.',
      action: async () => {
        try {
          const res = await api.delete(`${API}/api/commission-management/templates/${templateId}`);
          if (!res.ok) throw new Error('Failed to delete');
          toast.success('Template deleted');
          loadTemplates();
        } catch (err) {
          toast.error(err.message);
        }
      },
    });
  };

  return (
    <div className="p-4 sm:p-6 space-y-5 sm:space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-lg sm:text-xl font-semibold text-aegisaxiom-text flex items-center gap-2">
            <DollarSign size={20} className="text-violet-600 dark:text-violet-400 shrink-0" />
            Commission Templates
          </h1>
          <p className="text-xs sm:text-sm text-aegisaxiom-text-secondary mt-1">
            Manage default commission structures for quick deal processing
          </p>
        </div>
        {!editing && (
          <button
            onClick={startNew}
            className="flex items-center justify-center gap-2 bg-violet-600 hover:bg-violet-500 text-white px-4 py-2 rounded-lg transition shrink-0 w-full sm:w-auto"
          >
            <Plus size={16} />
            <span className="whitespace-nowrap">New Template</span>
          </button>
        )}
      </div>

      {/* Edit Form */}
      {editing && (
        <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-4 sm:p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-aegisaxiom-text">
              {editing === 'new' ? 'Create New Template' : 'Edit Template'}
            </h3>
            <button onClick={cancelEdit} className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text">
              <X size={18} />
            </button>
          </div>

          {/* Template Name */}
          <div>
            <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Template Name *</label>
            <input
              type="text"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="e.g., Standard Deal, High Touch, Enterprise"
              className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
            />
          </div>

          {/* Tier Percentages */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4">
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                Tier 1: Top Line (%)
              </label>
              <input
                type="number"
                step="0.1"
                min="0"
                max="100"
                value={form.tier1_percentage}
                onChange={(e) =>
                  setForm((f) => ({ ...f, tier1_percentage: parseFloat(e.target.value) || 0 }))
                }
                className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
              />
            </div>
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                Tier 2: Director (%)
              </label>
              <input
                type="number"
                step="0.1"
                min="0"
                max="100"
                value={form.tier2_percentage}
                onChange={(e) =>
                  setForm((f) => ({ ...f, tier2_percentage: parseFloat(e.target.value) || 0 }))
                }
                className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
              />
            </div>
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                Tier 3: Sales Rep (%)
              </label>
              <input
                type="number"
                step="0.1"
                min="0"
                max="100"
                value={form.tier3_percentage}
                onChange={(e) =>
                  setForm((f) => ({ ...f, tier3_percentage: parseFloat(e.target.value) || 0 }))
                }
                className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
              />
            </div>
          </div>

          {/* Default Users for Each Tier */}
          <div className="border-t border-aegisaxiom-border pt-4">
            <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-3">
              Default Users (Optional)
            </h4>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4">
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                  Tier 1 Default User
                </label>
                <select
                  value={form.tier1_default_user || ''}
                  onChange={(e) => setForm((f) => ({ ...f, tier1_default_user: e.target.value }))}
                  className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                >
                  <option value="">None (manual selection)</option>
                  <optgroup label="Dynamic Variables">
                    <option value="{deal_owner}">Deal Owner</option>
                    <option value="{agent_of_record}">Agent of Record</option>
                  </optgroup>
                  <optgroup label="Specific Users">
                    {users.map((u) => (
                      <option key={u.user_id} value={u.user_id}>
                        {u.name} ({u.role})
                      </option>
                    ))}
                  </optgroup>
                </select>
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                  Tier 2 Default User
                </label>
                <select
                  value={form.tier2_default_user || ''}
                  onChange={(e) => setForm((f) => ({ ...f, tier2_default_user: e.target.value }))}
                  className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                >
                  <option value="">None (manual selection)</option>
                  <optgroup label="Dynamic Variables">
                    <option value="{deal_owner}">Deal Owner</option>
                    <option value="{agent_of_record}">Agent of Record</option>
                  </optgroup>
                  <optgroup label="Specific Users">
                    {users.map((u) => (
                      <option key={u.user_id} value={u.user_id}>
                        {u.name} ({u.role})
                      </option>
                    ))}
                  </optgroup>
                </select>
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                  Tier 3 Default User
                </label>
                <select
                  value={form.tier3_default_user || ''}
                  onChange={(e) => setForm((f) => ({ ...f, tier3_default_user: e.target.value }))}
                  className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                >
                  <option value="">None (manual selection)</option>
                  <optgroup label="Dynamic Variables">
                    <option value="{deal_owner}">Deal Owner</option>
                    <option value="{agent_of_record}">Agent of Record</option>
                  </optgroup>
                  <optgroup label="Specific Users">
                    {users.map((u) => (
                      <option key={u.user_id} value={u.user_id}>
                        {u.name} ({u.role})
                      </option>
                    ))}
                  </optgroup>
                </select>
              </div>
            </div>
            <p className="text-[10px] text-aegisaxiom-text-muted mt-2 flex items-start gap-1.5">
              <Lightbulb size={11} className="shrink-0 mt-0.5 text-amber-500" />
              <span>
                Dynamic variables will be replaced with actual users when applying template to a
                deal
              </span>
            </p>
          </div>

          {/* Cost Default */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary block mb-2">
                Cost Calculation
              </label>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setForm((f) => ({ ...f, cost_type: 'percentage' }))}
                  className={`flex-1 px-3 py-2 rounded border text-xs transition ${
                    form.cost_type === 'percentage'
                      ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border-violet-500/30'
                      : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border-aegisaxiom-border hover:border-aegisaxiom-border-hover'
                  }`}
                >
                  Percentage
                </button>
                <button
                  type="button"
                  onClick={() => setForm((f) => ({ ...f, cost_type: 'actual' }))}
                  className={`flex-1 px-3 py-2 rounded border text-xs transition ${
                    form.cost_type === 'actual'
                      ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border-violet-500/30'
                      : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border-aegisaxiom-border hover:border-aegisaxiom-border-hover'
                  }`}
                >
                  Actual $
                </button>
              </div>
            </div>
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                Default Cost {form.cost_type === 'percentage' ? '(%)' : '($)'}
              </label>
              <input
                type="number"
                step={form.cost_type === 'percentage' ? '0.1' : '1'}
                min="0"
                value={form.cost_default}
                onChange={(e) =>
                  setForm((f) => ({ ...f, cost_default: parseFloat(e.target.value) || 0 }))
                }
                placeholder={form.cost_type === 'percentage' ? '50' : '50000'}
                className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
              />
            </div>
          </div>

          {/* Example Calculation */}
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded p-3">
            <p className="text-xs text-aegisaxiom-text-muted mb-2">Example (on $100K deal):</p>
            <div className="space-y-1 text-xs text-aegisaxiom-text-secondary">
              <div className="flex justify-between">
                <span>Tier 1 ({form.tier1_percentage}%):</span>
                <span className="text-aegisaxiom-text font-medium">
                  ${((100000 * form.tier1_percentage) / 100).toLocaleString()}
                </span>
              </div>
              <div className="flex justify-between">
                <span>Tier 2 ({form.tier2_percentage}%):</span>
                <span className="text-aegisaxiom-text font-medium">
                  ~$
                  {(
                    ((100000 -
                      (form.cost_type === 'percentage'
                        ? (100000 * form.cost_default) / 100
                        : form.cost_default) -
                      (100000 * form.tier1_percentage) / 100) *
                      form.tier2_percentage) /
                    100
                  ).toLocaleString()}
                </span>
              </div>
              <div className="flex justify-between">
                <span>Tier 3 ({form.tier3_percentage}%):</span>
                <span className="text-aegisaxiom-text font-medium">
                  ~$
                  {(
                    ((100000 -
                      (form.cost_type === 'percentage'
                        ? (100000 * form.cost_default) / 100
                        : form.cost_default) -
                      (100000 * form.tier1_percentage) / 100 -
                      ((100000 -
                        (form.cost_type === 'percentage'
                          ? (100000 * form.cost_default) / 100
                          : form.cost_default) -
                        (100000 * form.tier1_percentage) / 100) *
                        form.tier2_percentage) /
                        100) *
                      form.tier3_percentage) /
                    100
                  ).toLocaleString()}
                </span>
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="flex flex-col-reverse sm:flex-row gap-2 pt-2">
            <button
              onClick={cancelEdit}
              className="sm:px-4 py-2 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
            >
              Cancel
            </button>
            <button
              onClick={saveTemplate}
              disabled={loading}
              className="flex-1 flex items-center justify-center gap-2 bg-violet-600 hover:bg-violet-500 text-white py-2 rounded-lg transition disabled:opacity-50"
            >
              <Save size={16} />
              {loading ? 'Saving...' : editing === 'new' ? 'Create Template' : 'Save Changes'}
            </button>
          </div>
        </div>
      )}

      {/* Templates List */}
      <div className="grid gap-3">
        {initialLoad ? (
          <div className="flex items-center justify-center py-16">
            <div className="w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : templates.length === 0 ? (
          <div className="bg-aegisaxiom-accent-muted border border-aegisaxiom-border rounded-lg p-8 sm:p-10 text-center">
            <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-violet-50 dark:bg-violet-600/20 mb-3">
              <DollarSign size={22} className="text-violet-600 dark:text-violet-400" />
            </div>
            <p className="text-sm font-medium text-aegisaxiom-text mb-1">No commission templates yet</p>
            <p className="text-xs text-aegisaxiom-text-muted">
              Create your first template to get started
            </p>
          </div>
        ) : (
          templates.map((template) => (
            <div
              key={template.template_id}
              className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-4 hover:border-aegisaxiom-border-hover transition"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h4 className="text-sm font-medium text-aegisaxiom-text truncate">{template.name}</h4>
                    {template.is_active && (
                      <span className="px-2 py-0.5 bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30 rounded text-[10px] font-medium shrink-0">
                        Active
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-x-3 gap-y-1 mt-2 text-xs text-aegisaxiom-text-secondary flex-wrap">
                    <span>Tier 1: {template.tier1_percentage}%</span>
                    <span>Tier 2: {template.tier2_percentage}%</span>
                    <span>Tier 3: {template.tier3_percentage}%</span>
                    <span className="hidden sm:inline text-aegisaxiom-text-muted">|</span>
                    <span>
                      Cost:{' '}
                      {template.cost_type === 'percentage'
                        ? `${template.cost_default}%`
                        : `$${template.cost_default.toLocaleString()}`}
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    onClick={() => startEdit(template)}
                    className="p-2 rounded-md text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-surface transition"
                    aria-label={`Edit ${template.name}`}
                  >
                    <Edit2 size={14} />
                  </button>
                  <button
                    onClick={() => deleteTemplate(template.template_id)}
                    className="p-2 rounded-md text-aegisaxiom-text-secondary hover:text-red-600 dark:hover:text-red-400 hover:bg-aegisaxiom-surface transition"
                    aria-label={`Delete ${template.name}`}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
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
