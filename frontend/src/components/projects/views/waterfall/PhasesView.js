import React, { useMemo, useRef, useState } from 'react';
import { Plus } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import TaskCard from '../../widgets/TaskCard';
import TaskDetailDrawer from '../../widgets/TaskDetailDrawer';
import { extractTaskErrorMessage } from '@/lib/safeXhr';
import { usePermissions } from '@/hooks/usePermissions';
import ConflictDialog from '../../widgets/ConflictDialog';

// HTML5 drag-and-drop implementation (no react-beautiful-dnd dep).

function PhaseLane({ phase, project, tasks, assignees, canCreate, canChangeStatus, onAdd, onOpen, onDragOver, onDrop, onTaskMutated, onTaskDeleted, onConflict }) {
  const [draft, setDraft] = useState('');
  const [composing, setComposing] = useState(false);
  const [isOver, setIsOver] = useState(false);

  return (
    <div
      data-testid={`phase-lane-${phase}`}
      className={`w-[320px] shrink-0 flex flex-col rounded-lg border transition-colors ${
        isOver ? 'border-blue-500/40 bg-aegisaxiom-elevated/60' : 'border-aegisaxiom-border bg-aegisaxiom-elevated/40'
      }`}
      onDragOver={(e) => { if (!canChangeStatus) return; e.preventDefault(); setIsOver(true); onDragOver?.(e, phase); }}
      onDragLeave={() => setIsOver(false)}
      onDrop={(e) => { setIsOver(false); if (canChangeStatus) onDrop?.(e, phase); }}
    >
      <header className="h-10 px-3 sticky top-0 bg-aegisaxiom-elevated/80 border-b border-aegisaxiom-border flex items-center justify-between rounded-t-lg backdrop-blur-sm">
        <span className="text-sm font-semibold text-aegisaxiom-text">{phase}</span>
        <span className="inline-flex items-center h-5 px-2 rounded bg-aegisaxiom-surface text-aegisaxiom-text-muted text-[10px] font-semibold">
          {tasks.length}
        </span>
      </header>
      <div className="flex-1 p-2 space-y-2 min-h-[80px]">
        {tasks.map((t) => {
          const assignee = (assignees ?? []).find((u) => (u.user_id ?? u._id) === t.assignee_id);
          return (
            <div
              key={t.task_id ?? t._id}
              draggable
              onDragStart={(e) => e.dataTransfer.setData('taskId', t.task_id ?? t._id)}
            >
              <TaskCard task={t} project={project} onClick={onOpen} assignee={assignee} onTaskMutated={onTaskMutated} onTaskDeleted={onTaskDeleted} onConflict={onConflict} />
            </div>
          );
        })}
      </div>
      {canCreate ? (
      <footer className="p-2 border-t border-aegisaxiom-border/70">
        {composing ? (
          <form
            onSubmit={async (ev) => {
              ev.preventDefault();
              const v = draft.trim();
              if (!v) { setComposing(false); return; }
              await onAdd(v);
              setDraft('');
              setComposing(false);
            }}
          >
            <input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => setComposing(false)}
              onKeyDown={(e) => { if (e.key === 'Escape') setComposing(false); }}
              placeholder={`New task in ${phase}…`}
              className="aegisaxiom-input w-full h-8 text-xs"
              data-testid={`phase-lane-input-${phase}`}
            />
          </form>
        ) : (
          <button
            type="button"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => setComposing(true)}
            className="w-full h-8 inline-flex items-center gap-1.5 px-2 rounded-md text-xs text-aegisaxiom-text-muted hover:bg-aegisaxiom-elevated hover:text-aegisaxiom-text transition-colors"
            data-testid={`phase-lane-add-${phase}`}
          >
            <Plus size={12} /> Add task
          </button>
        )}
      </footer>
      ) : null}
    </div>
  );
}

// Extract a canonical phase name from either a string (legacy shape) or
// a `{name, start_date?, end_date?}` dict (canonical shape). Returns null
// if the entry is malformed.
const phaseName = (p) => (typeof p === 'string' ? p : p?.name ?? null);

// ---- Add-phase form (mirroring TemplatesTab.PhasesTab) ----

function AddPhaseForm({ project, onProjectChanged }) {
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const rawPhases = project?.methodology_config?.phases ?? [];
  const existingNames = rawPhases
    .map(phaseName)
    .filter((n) => typeof n === 'string' && n.length > 0);

  const handleAdd = async (e) => {
    e.preventDefault();
    const v = draft.trim();
    if (!v) return;

    // Client-side dedup (case-insensitive)
    if (existingNames.some((n) => n.toLowerCase() === v.toLowerCase())) {
      setError(`"${v}" already exists.`);
      return;
    }

    setError(null);
    setSaving(true);
    const projectId = project.project_id ?? project._id;
    const newPhases = [...rawPhases, { name: v }];
    try {
      const res = await api.patch(`${API_URL}/api/projects/projects/${projectId}`, {
        methodology_config: {
          ...(project.methodology_config ?? {}),
          phases: newPhases,
        },
      });
      if (!res.ok) {
        const msg = await extractTaskErrorMessage(res, 'Could not add phase.');
        toast.error(msg);
        return;
      }
      setDraft('');
      onProjectChanged?.();
    } catch {
      toast.error('Could not add phase.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mt-4 pt-4 border-t border-aegisaxiom-border" data-testid="add-phase-form">
      {error && (
        <p className="text-xs text-red-400 mb-2" data-testid="add-phase-error">{error}</p>
      )}
      <form onSubmit={handleAdd} className="flex gap-2">
        <input
          type="text"
          value={draft}
          onChange={(e) => { setDraft(e.target.value); setError(null); }}
          placeholder="Add phase"
          className="aegisaxiom-input h-8 w-[240px] text-xs"
          data-testid="add-phase-input"
        />
        <button
          type="submit"
          disabled={saving || !draft.trim()}
          className="btn-secondary h-8 px-3 text-xs disabled:opacity-50"
          data-testid="add-phase-button"
        >
          {saving ? 'Adding…' : 'Add phase'}
        </button>
      </form>
    </div>
  );
}

// ---- Remove-phase chip cloud ----

function PhaseChipCloud({ project, onProjectChanged }) {
  const rawPhases = project?.methodology_config?.phases ?? [];
  const [removing, setRemoving] = useState(null);

  const handleRemove = async (name) => {
    const projectId = project.project_id ?? project._id;
    setRemoving(name);
    try {
      const remaining = rawPhases.filter((p) => phaseName(p) !== name);
      const res = await api.patch(`${API_URL}/api/projects/projects/${projectId}`, {
        methodology_config: {
          ...(project.methodology_config ?? {}),
          phases: remaining,
        },
      });
      if (!res.ok) {
        const msg = await extractTaskErrorMessage(res, 'Could not remove phase.');
        toast.error(msg);
        return;
      }
      onProjectChanged?.();
    } catch {
      toast.error('Could not remove phase.');
    } finally {
      setRemoving(null);
    }
  };

  if (rawPhases.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2 mb-2" data-testid="phase-chip-cloud">
      {rawPhases.map((p) => {
        const name = phaseName(p);
        if (!name) return null;
        return (
          <span
            key={name}
            className="inline-flex items-center gap-1 h-7 px-3 rounded-md bg-violet-500/15 text-violet-400 text-xs font-medium"
          >
            {name}
            <button
              type="button"
              disabled={removing === name}
              onClick={() => handleRemove(name)}
              className="hover:text-violet-300 disabled:opacity-40"
              aria-label={`Remove phase ${name}`}
              data-testid={`phase-chip-remove-${name}`}
            >
              ×
            </button>
          </span>
        );
      })}
    </div>
  );
}

export default function PhasesView({ project, tasks, assignees, onTasksChanged, onTaskMutated, onTaskDeleted, onProjectChanged, onConflict }) {
  const { hasPermission } = usePermissions();
  const canCreate = hasPermission('projects.task.create');
  const canChangeStatus = hasPermission('projects.task.change_status');
  const canEdit = hasPermission('projects.project.edit');
  const rawPhases = project?.methodology_config?.phases ?? [];
  const phases = useMemo(
    () => rawPhases.map(phaseName).filter((n) => typeof n === 'string' && n.length > 0),
    [rawPhases]
  );
  const [openTask, setOpenTask] = useState(null);
  const dragTaskId = useRef(null);

  // Conflict dialog state (409 handling)
  const [conflictState, setConflictState] = useState(null); // { myDoc, theirDoc, pendingPatch }

  const byPhase = useMemo(() => {
    const m = new Map();
    phases.forEach((p) => m.set(p, []));
    tasks.forEach((t) => {
      const p = t.methodology_fields?.phase ?? phases[0];
      if (!m.has(p)) m.set(p, []);
      m.get(p).push(t);
    });
    return m;
  }, [tasks, phases]);

  const handleDragOver = (e, phase) => {
    e.preventDefault();
  };

  const handleDrop = async (e, targetPhase) => {
    const taskId = e.dataTransfer.getData('taskId');
    const task = tasks.find((t) => (t.task_id ?? t._id) === taskId);
    if (!task) return;
    const currentPhase = task.methodology_fields?.phase;
    if (currentPhase === targetPhase) return;

    // Optimistic update
    const optimisticDoc = {
      ...task,
      methodology_fields: { ...task.methodology_fields, phase: targetPhase },
    };
    onTaskMutated?.(optimisticDoc);

    const patch = { methodology_fields: { ...task.methodology_fields, phase: targetPhase } };
    try {
      const res = await api.patch(`${API_URL}/api/projects/tasks/${taskId}`, patch, {
        headers: task.updated_at ? { 'If-Match': task.updated_at } : {},
      });
      if (res.status === 409) {
        const d = await res.json().catch(() => ({}));
        onTaskMutated?.(task); // revert
        setConflictState({ myDoc: optimisticDoc, theirDoc: d.current_doc ?? d, pendingPatch: patch });
        return;
      }
      if (!res.ok) {
        onTaskMutated?.(task); // revert
        toast.error(await extractTaskErrorMessage(res, 'Could not move task.'));
        return;
      }
      const updated = await res.json();
      onTaskMutated?.(updated);
    } catch {
      onTaskMutated?.(task); // revert
      toast.error('Could not move task.');
    }
  };

  const addTask = async (phase, title) => {
    try {
      const res = await api.post(`${API_URL}/api/projects/tasks`, {
        project_id: project.project_id ?? project._id,
        title,
        methodology_fields: { phase, milestone: false },
      });
      if (!res.ok) {
        toast.error(await extractTaskErrorMessage(res, 'Could not create task.'));
        return;
      }
      onTasksChanged?.();
    } catch {
      toast.error('Could not create task.');
    }
  };

  if (phases.length === 0) {
    return (
      <div className="p-6" data-testid="phases-no-phases">
        <p className="text-sm text-aegisaxiom-text-muted mb-4 text-center">
          No phases configured for this project.
        </p>
        {canEdit && (
          <div className="max-w-md mx-auto">
            <AddPhaseForm project={project} onProjectChanged={onProjectChanged} />
          </div>
        )}
      </div>
    );
  }

  return (
    <div data-testid="phases-view" className="p-6">
      {/* Phase management strip (edit-gated) */}
      {canEdit && (
        <div className="mb-4 glass-card px-4 py-3" data-testid="phase-management">
          <p className="text-xs font-medium text-aegisaxiom-text-muted mb-2">Phases</p>
          <PhaseChipCloud project={project} onProjectChanged={onProjectChanged} />
          <AddPhaseForm project={project} onProjectChanged={onProjectChanged} />
        </div>
      )}

      {/* Kanban lanes */}
      <div className="flex gap-3 overflow-x-auto pb-4">
        {phases.map((p) => (
          <PhaseLane
            key={p}
            phase={p}
            project={project}
            tasks={byPhase.get(p) ?? []}
            assignees={assignees}
            canCreate={canCreate}
            canChangeStatus={canChangeStatus}
            onAdd={(title) => addTask(p, title)}
            onOpen={setOpenTask}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
            onTaskMutated={onTaskMutated}
            onTaskDeleted={onTaskDeleted}
            onConflict={onConflict}
          />
        ))}
      </div>
      <TaskDetailDrawer
        open={openTask !== null}
        onOpenChange={(v) => { if (!v) setOpenTask(null); }}
        task={openTask}
        project={project}
        allTasks={tasks}
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
