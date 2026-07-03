import React, { useMemo, useState } from 'react';
import {
  addDays,
  addMonths,
  format,
  isSameDay,
  isSameMonth,
  isWithinInterval,
  parseISO,
  startOfMonth,
  startOfWeek,
} from 'date-fns';
import { ChevronLeft, ChevronRight, Plus } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import TaskDetailDrawer from '../../widgets/TaskDetailDrawer';
import { extractTaskErrorMessage } from '@/lib/safeXhr';
import { usePermissions } from '@/hooks/usePermissions';
import ConflictDialog from '../../widgets/ConflictDialog';

const MAX_PILLS = 3;

export default function CalendarView({ project, tasks, onTasksChanged, onTaskMutated, onTaskDeleted, onConflict }) {
  const { hasPermission } = usePermissions();
  const canCreate = hasPermission('projects.task.create');

  const [monthDate, setMonthDate] = useState(() => startOfMonth(new Date()));
  const [openTask, setOpenTask] = useState(null);
  const [composingDate, setComposingDate] = useState(null);
  const [draft, setDraft] = useState('');
  // Drag-to-reschedule state
  const [draggingTaskId, setDraggingTaskId] = useState(null);
  const [conflictState, setConflictState] = useState(null);

  const weeks = useMemo(() => {
    const start = startOfWeek(startOfMonth(monthDate), { weekStartsOn: 1 });
    const rows = [];
    for (let r = 0; r < 6; r++) {
      const row = [];
      for (let c = 0; c < 7; c++) row.push(addDays(start, r * 7 + c));
      rows.push(row);
    }
    return rows;
  }, [monthDate]);

  const tasksOnDay = (day) =>
    tasks.filter((t) => {
      const due = t.due_date ? parseISO(t.due_date) : null;
      const start = t.start_date ? parseISO(t.start_date) : due;
      if (!due) return false;
      if (start && due) return isWithinInterval(day, { start, end: due });
      return isSameDay(day, due);
    });

  const firstPhaseRaw = project?.methodology_config?.phases?.[0];
  // phases are List[Dict[name, ...]] (canonical) or List[str] (legacy).
  const firstPhase = typeof firstPhaseRaw === 'string' ? firstPhaseRaw : firstPhaseRaw?.name;

  const addTask = async (dateISO, title) => {
    try {
      const res = await api.post(`${API_URL}/api/projects/tasks`, {
        project_id: project.project_id ?? project._id,
        title,
        due_date: dateISO,
        start_date: dateISO,
        methodology_fields: firstPhase ? { phase: firstPhase, milestone: false } : {},
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

  // Drag-to-reschedule: drop a task pill onto a different day
  const handleDayDrop = async (e, targetISO) => {
    e.preventDefault();
    const taskId = e.dataTransfer.getData('taskId');
    if (!taskId) return;
    const task = tasks.find((t) => (t.task_id ?? t._id) === taskId);
    if (!task) return;
    if (task.due_date === targetISO) return;

    const patch = { due_date: targetISO, start_date: targetISO };
    const optimisticDoc = { ...task, ...patch };
    onTaskMutated?.(optimisticDoc);

    try {
      const res = await api.patch(`${API_URL}/api/projects/tasks/${taskId}`, patch, {
        headers: task.updated_at ? { 'If-Match': task.updated_at } : {},
      });
      if (res.status === 409) {
        const d = await res.json().catch(() => ({}));
        onTaskMutated?.(task);
        setConflictState({ myDoc: optimisticDoc, theirDoc: d.current_doc ?? d, pendingPatch: patch });
        return;
      }
      if (!res.ok) {
        onTaskMutated?.(task);
        toast.error(await extractTaskErrorMessage(res, 'Could not reschedule task.'));
        return;
      }
      onTaskMutated?.(await res.json());
    } catch {
      onTaskMutated?.(task);
      toast.error('Could not reschedule task.');
    } finally {
      setDraggingTaskId(null);
    }
  };

  return (
    <div data-testid="calendar-view" className="p-6">
      {/* Toolbar */}
      <div className="flex items-center gap-2 mb-3">
        <button
          type="button"
          onClick={() => setMonthDate((d) => addMonths(d, -1))}
          className="btn-secondary h-8 w-8 inline-flex items-center justify-center"
          data-testid="calendar-prev"
          aria-label="Previous month"
        >
          <ChevronLeft size={14} />
        </button>
        <button
          type="button"
          onClick={() => setMonthDate(startOfMonth(new Date()))}
          className="btn-secondary h-8 px-3 text-xs"
          data-testid="calendar-today"
        >
          Today
        </button>
        <button
          type="button"
          onClick={() => setMonthDate((d) => addMonths(d, 1))}
          className="btn-secondary h-8 w-8 inline-flex items-center justify-center"
          data-testid="calendar-next"
          aria-label="Next month"
        >
          <ChevronRight size={14} />
        </button>
        <h2 className="ml-2 text-lg font-semibold text-aegisaxiom-text" data-testid="calendar-title">
          {format(monthDate, 'MMMM yyyy')}
        </h2>
      </div>

      <div className="rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface overflow-hidden">
        <div className="grid grid-cols-7 bg-aegisaxiom-elevated border-b border-aegisaxiom-border">
          {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d) => (
            <div key={d} className="h-8 flex items-center justify-center text-[11px] uppercase tracking-wide text-aegisaxiom-text-muted">
              {d}
            </div>
          ))}
        </div>
        {weeks.map((row, ri) => (
          <div key={ri} className="grid grid-cols-7 border-b last:border-b-0 border-aegisaxiom-border">
            {row.map((day) => {
              const iso = format(day, 'yyyy-MM-dd');
              const inMonth = isSameMonth(day, monthDate);
              const today = isSameDay(day, new Date());
              const t = tasksOnDay(day);
              const visible = t.slice(0, MAX_PILLS);
              const more = t.length - visible.length;
              return (
                <div
                  key={iso}
                  data-testid={`calendar-day-${iso}`}
                  className={`min-h-[96px] p-1 border-r last:border-r-0 border-aegisaxiom-border ${inMonth ? '' : 'bg-aegisaxiom-elevated/40'} ${today ? 'bg-blue-500/5' : ''}`}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => handleDayDrop(e, iso)}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className={`text-[11px] font-medium ${inMonth ? 'text-aegisaxiom-text' : 'text-aegisaxiom-text-muted'} ${today ? 'text-blue-400 font-bold' : ''}`}>
                      {format(day, 'd')}
                    </span>
                    {canCreate ? (
                      <button
                        type="button"
                        onClick={() => { setComposingDate(iso); setDraft(''); }}
                        className="h-4 w-4 inline-flex items-center justify-center text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
                        aria-label={`Add task on ${iso}`}
                        data-testid={`calendar-add-${iso}`}
                      >
                        <Plus size={10} />
                      </button>
                    ) : null}
                  </div>
                  <div className="space-y-0.5">
                    {visible.map((task) => (
                      <button
                        key={task.task_id ?? task._id}
                        type="button"
                        draggable
                        onDragStart={(e) => {
                          e.dataTransfer.setData('taskId', task.task_id ?? task._id);
                          setDraggingTaskId(task.task_id ?? task._id);
                        }}
                        onDragEnd={() => setDraggingTaskId(null)}
                        onClick={() => setOpenTask(task)}
                        data-testid={`calendar-task-${task.task_id ?? task._id}`}
                        className={`block w-full text-left px-1.5 py-0.5 rounded text-[10px] bg-blue-500/10 text-blue-300 truncate hover:bg-blue-500/20 transition-colors cursor-grab ${
                          draggingTaskId === (task.task_id ?? task._id) ? 'opacity-50' : ''
                        }`}
                        title={task.title}
                      >
                        {task.title}
                      </button>
                    ))}
                    {more > 0 ? (
                      <span className="text-[10px] text-aegisaxiom-text-muted" data-testid={`calendar-more-${iso}`}>
                        +{more} more
                      </span>
                    ) : null}
                  </div>
                  {composingDate === iso ? (
                    <form
                      onSubmit={async (e) => {
                        e.preventDefault();
                        const v = draft.trim();
                        if (v) await addTask(iso, v);
                        setComposingDate(null);
                        setDraft('');
                      }}
                    >
                      <input
                        autoFocus
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        onBlur={() => setComposingDate(null)}
                        onKeyDown={(e) => { if (e.key === 'Escape') setComposingDate(null); }}
                        placeholder="Title…"
                        className="aegisaxiom-input w-full h-6 text-[11px] px-1.5 mt-1"
                        data-testid={`calendar-input-${iso}`}
                      />
                    </form>
                  ) : null}
                </div>
              );
            })}
          </div>
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
