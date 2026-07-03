import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Plus, Edit2, Trash2, Save, X, DollarSign, CheckCircle, Layers } from 'lucide-react';
import { toast } from 'sonner';
import API_URL from '../lib/apiConfig';
import ConfirmModal from './shared/ConfirmModal';

const API = API_URL;

export default function DefaultResourceTypes() {
  const [types, setTypes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: '', sell_rate: '', description: '' });
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });

  useEffect(() => {
    load();
  }, []);

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.get(`${API}/api/admin/default-resource-types`);
      if (res.ok) {
        const data = await res.json();
        setTypes(data.default_resource_types || []);
      }
    } catch (err) {
      toast.error('Failed to load resource types');
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async (e) => {
    e.preventDefault();
    try {
      const res = await api.post(`${API}/api/admin/default-resource-types`, form);
      if (res.ok) {
        toast.success('Resource type created');
        setCreating(false);
        setForm({ name: '', sell_rate: '', description: '' });
        load();
      } else {
        const err = await res.json();
        toast.error(err.detail || 'Failed to create');
      }
    } catch (err) {
      toast.error('Error creating resource type');
    }
  };

  const handleUpdate = async (name) => {
    try {
      const res = await api.put(
        `${API}/api/admin/default-resource-types/${encodeURIComponent(name)}`,
        form,
      );
      if (res.ok) {
        toast.success('Resource type updated');
        setEditing(null);
        setForm({ name: '', sell_rate: '', description: '' });
        load();
      } else {
        const err = await res.json();
        toast.error(err.detail || 'Failed to update');
      }
    } catch (err) {
      toast.error('Error updating resource type');
    }
  };

  const handleDelete = (name) => {
    setConfirmState({
      open: true,
      title: 'Delete Resource Type',
      message: `Delete "${name}"? This will not affect existing contracts.`,
      action: async () => {
        try {
          const res = await api.delete(
            `${API}/api/admin/default-resource-types/${encodeURIComponent(name)}`,
          );
          if (res.ok) {
            toast.success('Resource type deleted');
            load();
          } else {
            const err = await res.json();
            toast.error(err.detail || 'Failed to delete');
          }
        } catch (err) {
          toast.error('Error deleting resource type');
        }
      },
    });
  };

  const startEdit = (type) => {
    setEditing(type.name);
    setForm({ name: type.name, sell_rate: type.sell_rate, description: type.description || '' });
  };

  const cancelEdit = () => {
    setEditing(null);
    setCreating(false);
    setForm({ name: '', sell_rate: '', description: '' });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="text-aegisaxiom-text-secondary">Loading...</div>
      </div>
    );
  }

  return (
    <div data-testid="resource-types-page">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-aegisaxiom-text flex items-center gap-2">
            <Layers size={24} className="text-violet-600 dark:text-violet-400" />
            Default Resource Types
          </h1>
          <p className="text-sm text-aegisaxiom-text-secondary mt-1">
            Manage default sell rates for resource types used in contracts
          </p>
        </div>
        {!creating && (
          <button
            onClick={() => setCreating(true)}
            className="flex items-center justify-center gap-2 bg-violet-600 hover:bg-violet-500 text-white px-4 py-2 rounded-lg transition text-sm w-full sm:w-auto"
            data-testid="add-resource-type-btn"
          >
            <Plus size={16} /> Add Resource Type
          </button>
        )}
      </div>

      {/* Info Banner */}
      <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800/30 rounded-lg p-4 mb-6">
        <h3 className="text-sm font-semibold text-blue-700 dark:text-blue-300 mb-1">
          About Default Resource Types
        </h3>
        <p className="text-xs text-blue-600 dark:text-blue-400/70">
          These are templates for quick selection when creating contracts. Changes here only affect
          NEW contracts. Existing contracts keep their rates. Users can edit rates after selection.
        </p>
      </div>

      {/* Create Form */}
      {creating && (
        <form
          onSubmit={handleCreate}
          className="bg-aegisaxiom-accent-soft border border-violet-600 rounded-lg p-4 space-y-3 mb-6"
        >
          <h3 className="text-sm font-semibold text-aegisaxiom-text">Create New Resource Type</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Name *</label>
              <input
                type="text"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="e.g., Senior Developer"
                required
                className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
              />
            </div>
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                Sell Rate ($/hr) *
              </label>
              <input
                type="number"
                step="0.01"
                value={form.sell_rate}
                onChange={(e) => setForm((f) => ({ ...f, sell_rate: e.target.value }))}
                placeholder="175.00"
                required
                className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
              />
            </div>
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Description</label>
              <input
                type="text"
                value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                placeholder="Optional description"
                className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
              />
            </div>
          </div>
          <div className="flex gap-2">
            <button
              type="submit"
              className="flex items-center gap-2 bg-violet-600 hover:bg-violet-500 text-white px-4 py-2 rounded text-sm transition"
            >
              <Save size={14} /> Create
            </button>
            <button
              type="button"
              onClick={cancelEdit}
              className="flex items-center gap-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm px-4 py-2 rounded text-sm transition"
            >
              <X size={14} /> Cancel
            </button>
          </div>
        </form>
      )}

      {/* Resource Types List */}
      <div className="flex flex-col gap-4">
        {types.length === 0 && (
          <div className="text-center py-12 text-aegisaxiom-text-secondary text-sm">
            No resource types yet. Click "Add Resource Type" to create one.
          </div>
        )}

        {types.map((type) => (
          <div
            key={type.name}
            className="bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg p-4 hover:border-aegisaxiom-border-hover/50 transition"
          >
            {editing === type.name ? (
              // Edit Mode
              <div className="space-y-3">
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Name</label>
                    <input
                      type="text"
                      value={form.name}
                      disabled
                      className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text-muted cursor-not-allowed"
                    />
                    <p className="text-[10px] text-aegisaxiom-text-muted mt-1">Name cannot be changed</p>
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                      Sell Rate ($/hr)
                    </label>
                    <input
                      type="number"
                      step="0.01"
                      value={form.sell_rate}
                      onChange={(e) => setForm((f) => ({ ...f, sell_rate: e.target.value }))}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                      Description
                    </label>
                    <input
                      type="text"
                      value={form.description}
                      onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                    />
                  </div>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => handleUpdate(type.name)}
                    className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded text-sm transition"
                  >
                    <Save size={14} /> Save Changes
                  </button>
                  <button
                    onClick={cancelEdit}
                    className="flex items-center gap-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm px-4 py-2 rounded text-sm transition"
                  >
                    <X size={14} /> Cancel
                  </button>
                </div>
              </div>
            ) : (
              // View Mode
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-x-3 gap-y-1 flex-wrap">
                    <h3 className="text-base font-semibold text-aegisaxiom-text">{type.name}</h3>
                    <div className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400 font-semibold">
                      <DollarSign size={16} />
                      <span>{parseFloat(type.sell_rate).toFixed(2)}/hr</span>
                    </div>
                    {type.is_active && (
                      <span className="px-2 py-0.5 bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 text-xs rounded-full border border-emerald-500/30 flex items-center gap-1">
                        <CheckCircle size={10} /> Active
                      </span>
                    )}
                  </div>
                  {type.description && (
                    <p className="text-xs text-aegisaxiom-text-muted mt-1">{type.description}</p>
                  )}
                  <p className="text-[10px] text-aegisaxiom-text-muted mt-1">
                    Last updated: {new Date(type.updated_at).toLocaleDateString()}
                  </p>
                </div>
                <div className="flex items-center gap-2 sm:shrink-0">
                  <button
                    onClick={() => startEdit(type)}
                    className="flex items-center gap-1 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm px-3 py-1.5 rounded text-xs transition"
                    aria-label={`Edit ${type.name}`}
                  >
                    <Edit2 size={12} /> Edit
                  </button>
                  <button
                    onClick={() => handleDelete(type.name)}
                    className="flex items-center gap-1 bg-red-50 dark:bg-red-900/30 hover:bg-red-100 dark:hover:bg-red-900/50 text-red-700 dark:text-red-300 px-3 py-1.5 rounded text-xs transition"
                    aria-label={`Delete ${type.name}`}
                  >
                    <Trash2 size={12} /> Delete
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
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
