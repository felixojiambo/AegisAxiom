import React, { useMemo, useState } from 'react';
import { differenceInCalendarDays, format, parseISO } from 'date-fns';
import { Plus, Play, Square } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import ConfirmModal from '@/components/shared/ConfirmModal';
import { usePermissions } from '@/hooks/usePermissions';
import DateInput from '@/components/ui/DateInput';

export const BACKLOG_KEY = '__backlog__';
export const ALL_KEY = '__all__';

function getErrorDetail(e) {
  return e?.message ?? null;
}

export default function SprintSwitcher({ projectId, sprints, onSprintsChange, selectedSprintId, onChange }) {
  const { hasPermission } = usePermissions();
  const canCreate = hasPermission('projects.sprint.create');
  const canChangeStatus = hasPermission('projects.sprint.change_status');

  const [createOpen, setCreateOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState({ name: '', start_date: '', end_date: '', goal: '' });
  const [closeConfirmId, setCloseConfirmId] = useState(null);

  const grouped = useMemo(() => {
    const all = sprints ?? [];
    return {
      active: all.filter((s) => s.status === 'active'),
      planned: all.filter((s) => s.status === 'planned'),
      closed: all.filter((s) => s.status === 'closed').slice(0, 5),
    };
  }, [sprints]);

  const activeSprint = grouped.active[0] ?? null;
  const currentSprint = (sprints ?? []).find((s) => s.sprint_id === selectedSprintId) ?? null;

  const handleCreate = async () => {
    if (!draft.name.trim() || !draft.start_date || !draft.end_date) {
      toast.error('Name, start date and end date are required.');
      return;
    }
    setSaving(true);
    try {
      const res = await api.post(`${API_URL}/api/projects/sprints`, {
        project_id: projectId,
        name: draft.name.trim(),
        start_date: draft.start_date,
        end_date: draft.end_date,
        goal: draft.goal.trim() || undefined,
      });
      const data = await res.json();
      if (!res.ok) { toast.error(data.detail ?? 'Could not create sprint.'); return; }
      toast.success(`Sprint "${data.name}" created`);
      onSprintsChange?.();
      onChange(data.sprint_id);
      setCreateOpen(false);
      setDraft({ name: '', start_date: '', end_date: '', goal: '' });
    } catch {
      toast.error('Could not create sprint.');
    } finally {
      setSaving(false);
    }
  };

  const handleStart = async (sprintId) => {
    const res = await api.patch(`${API_URL}/api/projects/sprints/${sprintId}`, { status: 'active' });
    const data = await res.json();
    if (!res.ok) { toast.error(data.detail ?? 'Could not start sprint.'); return; }
    toast.success('Sprint started');
    onSprintsChange?.();
  };

  const handleClose = (sprintId) => {
    setCloseConfirmId(sprintId);
  };

  const performClose = async () => {
    const sprintId = closeConfirmId;
    setCloseConfirmId(null);
    if (!sprintId) return;
    const res = await api.patch(`${API_URL}/api/projects/sprints/${sprintId}`, { status: 'closed' });
    const data = await res.json();
    if (!res.ok) { toast.error(data.detail ?? 'Could not close sprint.'); return; }
    toast.success('Sprint closed');
    onSprintsChange?.();
  };

  return (
    <div className="flex items-center gap-3 px-6 py-3 border-b border-aegisaxiom-border" data-testid="sprint-switcher">
      <label className="text-xs text-aegisaxiom-text-muted uppercase tracking-wide">Sprint</label>
      <select
        value={selectedSprintId}
        onChange={(e) => onChange(e.target.value)}
        className="aegisaxiom-input h-9 text-sm max-w-[280px]"
        data-testid="sprint-select"
      >
        <option value={ALL_KEY}>All tasks</option>
        <option value={BACKLOG_KEY}>Backlog only</option>
        {grouped.active.length > 0 ? (
          <optgroup label="Active">
            {grouped.active.map((s) => <option key={s.sprint_id} value={s.sprint_id}>{s.name}</option>)}
          </optgroup>
        ) : null}
        {grouped.planned.length > 0 ? (
          <optgroup label="Planned">
            {grouped.planned.map((s) => <option key={s.sprint_id} value={s.sprint_id}>{s.name}</option>)}
          </optgroup>
        ) : null}
        {grouped.closed.length > 0 ? (
          <optgroup label={`Closed (last ${grouped.closed.length})`}>
            {grouped.closed.map((s) => <option key={s.sprint_id} value={s.sprint_id}>{s.name}</option>)}
          </optgroup>
        ) : null}
      </select>

      {currentSprint ? <SprintMeta sprint={currentSprint} /> : null}

      <div className="ml-auto flex items-center gap-2">
        {canChangeStatus && currentSprint?.status === 'planned' ? (
          <button
            type="button"
            onClick={() => handleStart(currentSprint.sprint_id)}
            className="btn-primary h-8 px-3 inline-flex items-center gap-1.5 text-xs"
            data-testid="sprint-start"
          >
            <Play size={12} /> Start sprint
          </button>
        ) : null}
        {canChangeStatus && currentSprint?.status === 'active' ? (
          <button
            type="button"
            onClick={() => handleClose(currentSprint.sprint_id)}
            className="btn-secondary h-8 px-3 inline-flex items-center gap-1.5 text-xs"
            data-testid="sprint-close"
          >
            <Square size={12} /> Close sprint
          </button>
        ) : null}
        {activeSprint && currentSprint?.sprint_id !== activeSprint.sprint_id ? (
          <span className="text-[11px] text-aegisaxiom-text-muted hidden md:inline" data-testid="sprint-active-hint">
            Active: {activeSprint.name}
          </span>
        ) : null}
        {canCreate ? (
          <button
            type="button"
            onClick={() => setCreateOpen(true)}
            className="btn-secondary h-8 px-3 inline-flex items-center gap-1.5 text-xs"
            data-testid="sprint-new"
          >
            <Plus size={12} /> New sprint
          </button>
        ) : null}
      </div>

      {createOpen ? (
        <div
          className="fixed inset-0 bg-black/40 z-40 flex items-center justify-center"
          onClick={() => setCreateOpen(false)}
          data-testid="sprint-create-backdrop"
        >
          <div
            className="glass-card w-[420px] p-5 space-y-3"
            onClick={(e) => e.stopPropagation()}
            data-testid="sprint-create-dialog"
          >
            <h3 className="text-sm font-bold text-aegisaxiom-text">New sprint</h3>
            <div>
              <label className="block text-xs mb-1 text-aegisaxiom-text-muted">Name</label>
              <input
                className="aegisaxiom-input w-full"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="Sprint 1"
                data-testid="sprint-create-name"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs mb-1 text-aegisaxiom-text-muted">Start</label>
                <DateInput
                  value={draft.start_date}
                  onChange={(next) => setDraft({ ...draft, start_date: next })}
                  data-testid="sprint-create-start"
                  placeholder="Sprint start"
                />
              </div>
              <div>
                <label className="block text-xs mb-1 text-aegisaxiom-text-muted">End</label>
                <DateInput
                  value={draft.end_date}
                  onChange={(next) => setDraft({ ...draft, end_date: next })}
                  data-testid="sprint-create-end"
                  placeholder="Sprint end"
                  min={draft.start_date || undefined}
                />
              </div>
            </div>
            <div>
              <label className="block text-xs mb-1 text-aegisaxiom-text-muted">Goal (optional)</label>
              <input
                className="aegisaxiom-input w-full"
                value={draft.goal}
                onChange={(e) => setDraft({ ...draft, goal: e.target.value })}
                placeholder="Ship onboarding v2"
                data-testid="sprint-create-goal"
              />
            </div>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setCreateOpen(false)}
                className="btn-secondary h-8 px-3 text-xs"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleCreate}
                disabled={saving}
                className="btn-primary h-8 px-3 text-xs disabled:opacity-60"
                data-testid="sprint-create-submit"
              >
                {saving ? 'Creating…' : 'Create sprint'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <ConfirmModal
        isOpen={closeConfirmId !== null}
        title="Close sprint"
        message="Close this sprint? Unfinished tasks will return to the backlog."
        confirmText="Close sprint"
        variant="danger"
        onConfirm={performClose}
        onCancel={() => setCloseConfirmId(null)}
      />
    </div>
  );
}

function SprintMeta({ sprint }) {
  const start = sprint.start_date ? parseISO(sprint.start_date) : null;
  const end = sprint.end_date ? parseISO(sprint.end_date) : null;
  const dateRange = (start && end)
    ? `${format(start, 'MMM d')} – ${format(end, 'MMM d')}`
    : '— no dates —';
  const daysLeft = end ? differenceInCalendarDays(end, new Date()) : null;
  const chipColor = daysLeft === null
    ? 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted'
    : daysLeft > 3 ? 'bg-green-500/20 text-green-400' :
      daysLeft >= 1 ? 'bg-yellow-500/20 text-yellow-400' :
      'bg-red-500/20 text-red-400';

  return (
    <div className="flex items-center gap-2 text-xs text-aegisaxiom-text-muted">
      <span>{dateRange}</span>
      {sprint.status === 'active' && daysLeft !== null ? (
        <span
          className={`inline-flex items-center h-5 px-2 rounded-full text-[10px] font-semibold ${chipColor}`}
          data-testid="sprint-days-remaining"
        >
          {daysLeft > 0
            ? `${daysLeft} day${daysLeft === 1 ? '' : 's'} left`
            : daysLeft === 0
              ? 'Today'
              : `${-daysLeft} overdue`}
        </span>
      ) : null}
    </div>
  );
}
