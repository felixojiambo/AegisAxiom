import React, { useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, Circle, Flag, Plus } from 'lucide-react';
import { format, parseISO } from 'date-fns';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import TaskDetailDrawer from '../../widgets/TaskDetailDrawer';
import DateInput from '@/components/ui/DateInput';
import { extractTaskErrorMessage } from '@/lib/safeXhr';
import { usePermissions } from '@/hooks/usePermissions';
import ConflictDialog from '../../widgets/ConflictDialog';

// Wave 6 / Thread C — Contract 5
// Milestone tasks are waterfall tasks with methodology_fields.milestone === true.
// `milestone_at_risk` is computed client-side here (mirrors backend
// `compute_milestone_at_risk` logic in services/projects_methodology.py):
//   target_date < today AND status !== 'complete'

function isAtRisk(task) {
  const mf = task?.methodology_fields ?? {};
  if (!mf.milestone) return false;
  const target = mf.milestone_target_date;
  if (!target) return false;
  if (task.status === 'complete') return false;
  try {
    const targetDate = parseISO(target.slice(0, 10));
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return targetDate.getTime() < today.getTime();
  } catch {
    return false;
  }
}

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    return format(parseISO(iso.slice(0, 10)), 'MMM d, yyyy');
  } catch {
    return iso;
  }
}

function StatusBadge({ task }) {
  const status = task.status;
  if (status === 'complete') {
    return (
      <span className="inline-flex items-center gap-1 h-6 px-2 rounded-md bg-green-500/15 text-green-400 text-[11px] font-medium">
        <CheckCircle2 size={11} /> Complete
      </span>
    );
  }
  if (isAtRisk(task)) {
    return (
      <span className="inline-flex items-center gap-1 h-6 px-2 rounded-md bg-red-500/15 text-red-400 text-[11px] font-medium" data-testid="milestone-at-risk-badge">
        <AlertTriangle size={11} /> At risk
      </span>
    );
  }
  if (status === 'blocked') {
    return (
      <span className="inline-flex items-center gap-1 h-6 px-2 rounded-md bg-red-500/15 text-red-400 text-[11px] font-medium">
        Blocked
      </span>
    );
  }
  if (status === 'in_progress') {
    return (
      <span className="inline-flex items-center gap-1 h-6 px-2 rounded-md bg-blue-500/15 text-blue-400 text-[11px] font-medium">
        In progress
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 h-6 px-2 rounded-md bg-aegisaxiom-elevated text-aegisaxiom-text-muted text-[11px] font-medium">
      <Circle size={11} /> Not started
    </span>
  );
}

function NewMilestoneForm({ project, onCreated }) {
  const [name, setName] = useState('');
  const [phase, setPhase] = useState(() => {
    const first = project?.methodology_config?.phases?.[0];
    return typeof first === 'string' ? first : first?.name ?? '';
  });
  const [target, setTarget] = useState('');
  const [saving, setSaving] = useState(false);

  const phaseOptions = useMemo(() => {
    const raw = project?.methodology_config?.phases ?? [];
    return raw
      .map((p) => (typeof p === 'string' ? p : p?.name))
      .filter((n) => typeof n === 'string' && n.length > 0);
  }, [project]);

  if (phaseOptions.length === 0) {
    return (
      <p className="text-sm text-aegisaxiom-text-muted">
        Add at least one phase to the project before creating milestones.
      </p>
    );
  }

  const handleSubmit = async (e) => {
    e.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName || !target) return;
    setSaving(true);
    try {
      const res = await api.post(`${API_URL}/api/projects/tasks`, {
        project_id: project.project_id ?? project._id,
        title: trimmedName,
        due_date: target,
        methodology_fields: {
          phase,
          milestone: true,
          milestone_name: trimmedName,
          milestone_target_date: target,
        },
      });
      if (!res.ok) {
        toast.error(await extractTaskErrorMessage(res, 'Could not create milestone.'));
        return;
      }
      setName('');
      setTarget('');
      onCreated?.();
    } catch {
      toast.error('Could not create milestone.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-2" data-testid="milestone-new-form">
      <div className="flex flex-col">
        <label className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-1">Milestone</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Beta launch"
          className="aegisaxiom-input h-8 w-[220px] text-xs"
          data-testid="milestone-new-name"
        />
      </div>
      <div className="flex flex-col">
        <label className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-1">Phase</label>
        <select
          value={phase}
          onChange={(e) => setPhase(e.target.value)}
          className="aegisaxiom-input h-8 text-xs"
          data-testid="milestone-new-phase"
        >
          {phaseOptions.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </div>
      <div className="flex flex-col">
        <label className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-1">Target date</label>
        <DateInput
          value={target}
          onChange={setTarget}
          data-testid="milestone-new-target"
          placeholder="Target date"
        />
      </div>
      <button
        type="submit"
        disabled={saving || !name.trim() || !target}
        className="btn-primary h-8 px-3 text-xs inline-flex items-center gap-1.5 disabled:opacity-50"
        data-testid="milestone-new-submit"
      >
        <Plus size={12} />
        {saving ? 'Adding…' : 'Add milestone'}
      </button>
    </form>
  );
}

export default function MilestonesView({ project, tasks, assignees, onTasksChanged, onTaskMutated, onTaskDeleted, onConflict }) {
  const { hasPermission } = usePermissions();
  const canCreate = hasPermission('projects.task.create');
  const [openTask, setOpenTask] = useState(null);
  const [conflictState, setConflictState] = useState(null);

  // Inline quick-complete: optimistically toggle a milestone's status
  const handleToggleComplete = async (milestone) => {
    const taskId = milestone.task_id ?? milestone._id;
    const newStatus = milestone.status === 'complete' ? 'not_started' : 'complete';
    const optimisticDoc = { ...milestone, status: newStatus };
    onTaskMutated?.(optimisticDoc);
    const patch = { status: newStatus };
    try {
      const res = await api.patch(`${API_URL}/api/projects/tasks/${taskId}`, patch, {
        headers: milestone.updated_at ? { 'If-Match': milestone.updated_at } : {},
      });
      if (res.status === 409) {
        const d = await res.json().catch(() => ({}));
        onTaskMutated?.(milestone);
        setConflictState({ myDoc: optimisticDoc, theirDoc: d.current_doc ?? d, pendingPatch: patch });
        return;
      }
      if (!res.ok) {
        onTaskMutated?.(milestone);
        toast.error(await extractTaskErrorMessage(res, 'Could not update milestone.'));
        return;
      }
      onTaskMutated?.(await res.json());
    } catch {
      onTaskMutated?.(milestone);
      toast.error('Could not update milestone.');
    }
  };

  const milestones = useMemo(() => {
    return (tasks ?? [])
      .filter((t) => t.methodology_fields?.milestone === true)
      .sort((a, b) => {
        const ta = a.methodology_fields?.milestone_target_date ?? a.due_date ?? '';
        const tb = b.methodology_fields?.milestone_target_date ?? b.due_date ?? '';
        return ta.localeCompare(tb);
      });
  }, [tasks]);

  const atRiskCount = milestones.filter(isAtRisk).length;
  const completeCount = milestones.filter((m) => m.status === 'complete').length;

  return (
    <div data-testid="milestones-view" className="p-6">
      {/* Summary strip */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
        <div className="glass-card px-4 py-3 flex items-center gap-3" data-testid="milestone-summary-total">
          <Flag size={18} className="text-violet-400" />
          <div>
            <p className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted">Milestones</p>
            <p className="text-xl font-bold text-aegisaxiom-text">{milestones.length}</p>
          </div>
        </div>
        <div className="glass-card px-4 py-3 flex items-center gap-3" data-testid="milestone-summary-at-risk">
          <AlertTriangle size={18} className={atRiskCount > 0 ? 'text-red-400' : 'text-aegisaxiom-text-muted'} />
          <div>
            <p className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted">At risk</p>
            <p className={`text-xl font-bold ${atRiskCount > 0 ? 'text-red-400' : 'text-aegisaxiom-text'}`}>{atRiskCount}</p>
          </div>
        </div>
        <div className="glass-card px-4 py-3 flex items-center gap-3" data-testid="milestone-summary-complete">
          <CheckCircle2 size={18} className="text-green-400" />
          <div>
            <p className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted">Complete</p>
            <p className="text-xl font-bold text-aegisaxiom-text">{completeCount}</p>
          </div>
        </div>
      </div>

      {/* Add-milestone form */}
      {canCreate ? (
        <div className="glass-card px-4 py-3 mb-4" data-testid="milestone-create-strip">
          <NewMilestoneForm project={project} onCreated={onTasksChanged} />
        </div>
      ) : null}

      {/* List */}
      {milestones.length === 0 ? (
        <div className="glass-card p-8 text-center text-sm text-aegisaxiom-text-muted" data-testid="milestones-empty">
          No milestones yet. {canCreate ? 'Add one above to track key delivery dates.' : 'Ask a PM to add one.'}
        </div>
      ) : (
        <div className="glass-card overflow-hidden" data-testid="milestones-list">
          <table className="w-full text-sm">
            <thead className="bg-aegisaxiom-elevated/60 text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted">
              <tr>
                <th className="text-left px-4 py-2 font-semibold">Milestone</th>
                <th className="text-left px-4 py-2 font-semibold">Phase</th>
                <th className="text-left px-4 py-2 font-semibold">Target</th>
                <th className="text-left px-4 py-2 font-semibold">Actual</th>
                <th className="text-left px-4 py-2 font-semibold">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-aegisaxiom-border">
              {milestones.map((m) => {
                const mf = m.methodology_fields ?? {};
                const taskId = m.task_id ?? m._id;
                return (
                  <tr
                    key={taskId}
                    className="hover:bg-aegisaxiom-elevated/40 cursor-pointer transition-colors"
                    onClick={() => setOpenTask(m)}
                    data-testid={`milestone-row-${taskId}`}
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <Flag size={12} className="text-violet-400 shrink-0" />
                        <span className="font-medium text-aegisaxiom-text truncate">
                          {mf.milestone_name ?? m.title}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-aegisaxiom-text-secondary">{mf.phase ?? '—'}</td>
                    <td className="px-4 py-3 text-aegisaxiom-text-secondary">{fmtDate(mf.milestone_target_date ?? m.due_date)}</td>
                    <td className="px-4 py-3 text-aegisaxiom-text-secondary">{fmtDate(mf.milestone_actual_date)}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <StatusBadge task={m} />
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); handleToggleComplete(m); }}
                          className="text-[10px] text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition-colors"
                          aria-label={m.status === 'complete' ? 'Mark incomplete' : 'Mark complete'}
                          data-testid={`milestone-toggle-${taskId}`}
                        >
                          {m.status === 'complete' ? 'Reopen' : 'Complete'}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <TaskDetailDrawer
        open={openTask !== null}
        onOpenChange={(v) => { if (!v) setOpenTask(null); }}
        task={openTask}
        project={project}
        allTasks={tasks}
        assignees={assignees}
        onTaskUpdated={onTasksChanged}
        onTaskMutated={onTaskMutated}
        onTaskDeleted={onTaskDeleted}
        onConflict={onConflict}
      />

      <ConflictDialog
        isOpen={conflictState !== null}
        myDoc={conflictState?.myDoc}
        theirDoc={conflictState?.theirDoc}
        onDiscard={() => {
          if (conflictState?.theirDoc) onTaskMutated?.(conflictState.theirDoc);
          setConflictState(null);
        }}
        onOverwrite={async () => {
          if (!conflictState) return;
          setConflictState(null);
          const taskId = conflictState.myDoc.task_id ?? conflictState.myDoc._id;
          try {
            const res = await api.patch(`${API_URL}/api/projects/tasks/${taskId}`, conflictState.pendingPatch);
            if (!res.ok) { toast.error('Could not overwrite — try again.'); return; }
            onTaskMutated?.(await res.json());
          } catch {
            toast.error('Could not overwrite.');
          }
        }}
        onMerge={() => {
          if (conflictState?.theirDoc) onTaskMutated?.(conflictState.theirDoc);
          setConflictState(null);
          toast.info('Reverted to server version. Re-apply your changes.');
        }}
      />
    </div>
  );
}
