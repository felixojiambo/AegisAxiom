import React, { useEffect, useState } from 'react';
import { Plus, Trash2, GripVertical } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';

const PRESET_COLORS = [
  '#6B2BA8', '#9B5DD1', '#E8DAEF',
  '#5E81FF', '#7BC4F2', '#9CE3D5',
  '#4CAF50', '#AEE6AE', '#FFC107',
  '#F2B05E', '#F56E6E', '#A02620',
];

export default function ColumnManager({ open, onOpenChange, project, tasks, onSaved }) {
  const { hasPermission } = usePermissions();
  const canEdit = hasPermission('projects.project.edit');

  const initial = (project?.methodology_config?.columns ?? []).map((c) => ({
    id: c.id,
    name: c.name,
    color: c.color ?? null,
    is_done: !!c.is_done,
  }));
  const [cols, setCols] = useState(initial);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) setCols(initial);
  }, [open, project?.project_id ?? project?._id]);

  const taskCount = (cid) =>
    (tasks ?? []).filter((t) => t.methodology_fields?.column_id === cid).length;

  const toggleDone = (cid) =>
    setCols((prev) => prev.map((c) => ({ ...c, is_done: c.id === cid })));

  const setColor = (cid, color) =>
    setCols((prev) => prev.map((c) => (c.id === cid ? { ...c, color } : c)));

  const setName = (cid, name) =>
    setCols((prev) => prev.map((c) => (c.id === cid ? { ...c, name } : c)));

  const removeCol = (cid) => {
    if (cols.length <= 1) { toast.error('At least one column is required.'); return; }
    if (taskCount(cid) > 0) { toast.error('Move tasks out of this column first.'); return; }
    setCols((prev) => prev.filter((c) => c.id !== cid));
  };

  const addCol = () => {
    const id = `col_${Date.now().toString(36)}`;
    setCols((prev) => [...prev, { id, name: 'New column', color: '#9B5DD1', is_done: false }]);
  };

  const moveCol = (idx, dir) => {
    setCols((prev) => {
      const next = [...prev];
      const target = idx + dir;
      if (target < 0 || target >= next.length) return prev;
      [next[idx], next[target]] = [next[target], next[idx]];
      return next;
    });
  };

  const save = async () => {
    const payload = cols.map((c) => ({
      id: c.id,
      name: c.name.trim() || c.id,
      ...(c.color ? { color: c.color } : {}),
      ...(c.is_done ? { is_done: true } : {}),
    }));
    setSaving(true);
    try {
      const id = project.project_id ?? project._id;
      const res = await api.patch(`${API_URL}/api/projects/projects/${id}`, {
        methodology_config: { ...project.methodology_config, columns: payload },
      });
      const data = await res.json();
      if (!res.ok) { toast.error(data.detail ?? 'Could not save columns.'); return; }
      toast.success('Columns saved');
      onSaved?.();
      onOpenChange(false);
    } catch {
      toast.error('Could not save columns.');
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 bg-black/40 z-40 flex items-center justify-center"
      onClick={() => onOpenChange(false)}
      data-testid="column-manager-backdrop"
    >
      <div
        className="glass-card w-[640px] max-h-[80vh] overflow-y-auto p-5"
        onClick={(e) => e.stopPropagation()}
        data-testid="column-manager-dialog"
      >
        <h3 className="text-sm font-bold text-aegisaxiom-text mb-4">Manage columns</h3>
        <div className="space-y-2">
          {cols.map((c, idx) => {
            const count = taskCount(c.id);
            const canDelete = cols.length > 1 && count === 0;
            return (
              <div
                key={c.id}
                className="flex items-center gap-2 p-2 rounded-md border border-aegisaxiom-border bg-aegisaxiom-elevated"
                data-testid={`column-row-${c.id}`}
              >
                <div className="flex flex-col">
                  <button
                    type="button"
                    onClick={() => moveCol(idx, -1)}
                    disabled={idx === 0}
                    className="h-4 w-5 text-[10px] text-aegisaxiom-text-muted disabled:opacity-30"
                    aria-label="Move up"
                  >▲</button>
                  <button
                    type="button"
                    onClick={() => moveCol(idx, 1)}
                    disabled={idx === cols.length - 1}
                    className="h-4 w-5 text-[10px] text-aegisaxiom-text-muted disabled:opacity-30"
                    aria-label="Move down"
                  >▼</button>
                </div>
                <GripVertical size={14} className="text-aegisaxiom-text-muted" />
                <input
                  value={c.name}
                  onChange={(e) => setName(c.id, e.target.value)}
                  className="aegisaxiom-input h-8 text-sm flex-1"
                  data-testid={`column-name-${c.id}`}
                />
                <ColorPicker value={c.color} onChange={(v) => setColor(c.id, v)} testid={`column-color-${c.id}`} />
                <label className="inline-flex items-center gap-1 text-xs cursor-pointer text-aegisaxiom-text-muted">
                  <input
                    type="radio"
                    name="done-col"
                    checked={!!c.is_done}
                    onChange={() => toggleDone(c.id)}
                    data-testid={`column-done-${c.id}`}
                  />
                  Done
                </label>
                <span className="text-[10px] text-aegisaxiom-text-muted w-14 text-right">
                  {count} task{count === 1 ? '' : 's'}
                </span>
                <button
                  type="button"
                  onClick={() => removeCol(c.id)}
                  disabled={!canDelete}
                  className="h-7 w-7 inline-flex items-center justify-center rounded text-aegisaxiom-text-muted hover:text-red-400 disabled:opacity-30"
                  data-testid={`column-delete-${c.id}`}
                  aria-label="Delete column"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            );
          })}
          <button
            type="button"
            onClick={addCol}
            className="btn-secondary w-full h-9 inline-flex items-center justify-center gap-1.5 text-xs border-dashed"
            data-testid="column-add"
          >
            <Plus size={12} /> Add column
          </button>
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <button type="button" onClick={() => onOpenChange(false)} className="btn-secondary h-8 px-3 text-xs">
            Cancel
          </button>
          <button
            type="button"
            onClick={save}
            disabled={saving || !canEdit}
            className="btn-primary h-8 px-3 text-xs disabled:opacity-60"
            data-testid="column-save"
            title={!canEdit ? 'You do not have permission to edit columns' : undefined}
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

function ColorPicker({ value, onChange, testid }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative" data-testid={testid}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="h-6 w-6 rounded border border-aegisaxiom-border"
        style={{ backgroundColor: value ?? '#9B5DD1' }}
        aria-label="Pick column color"
      />
      {open ? (
        <div className="absolute right-0 top-8 z-20 p-2 rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface shadow-md">
          <div className="grid grid-cols-6 gap-1">
            {PRESET_COLORS.map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => { onChange(c); setOpen(false); }}
                className="h-5 w-5 rounded border border-aegisaxiom-border"
                style={{ backgroundColor: c }}
                aria-label={c}
              />
            ))}
          </div>
          <input
            type="color"
            value={value ?? '#9B5DD1'}
            onChange={(e) => onChange(e.target.value)}
            className="w-full mt-2 h-6"
          />
        </div>
      ) : null}
    </div>
  );
}
