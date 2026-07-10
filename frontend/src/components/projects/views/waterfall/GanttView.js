import React, { useMemo, useRef, useState } from 'react';
import { addDays, differenceInDays, format, parseISO, startOfDay } from 'date-fns';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import TaskDetailDrawer from '../../widgets/TaskDetailDrawer';
import LoadingSpinner from '../../widgets/LoadingSpinner';
import { usePermissions } from '@/hooks/usePermissions';
import ConflictDialog from '../../widgets/ConflictDialog';

const DAY_WIDTH = 32;
const ROW_HEIGHT = 36;
const HEADER_HEIGHT = 32;
const LEFT_PANE_WIDTH = 320;

const PHASE_COLORS = [
  '#6B2BA8', '#9B5DD1', '#4CAF50', '#FFC107', '#F44336', '#5E81FF',
];

function phaseColor(phase) {
  if (!phase) return '#9B5DD1';
  let h = 0;
  for (let i = 0; i < phase.length; i++) h = ((h * 31 + phase.charCodeAt(i)) >>> 0);
  return PHASE_COLORS[h % PHASE_COLORS.length];
}

export default function GanttView({ project, tasks, assignees, onTasksChanged, onTaskMutated, onTaskDeleted, loading, onConflict }) {
  const { hasPermission } = usePermissions();
  const canEditTask = hasPermission('projects.task.edit');
  const [openTask, setOpenTask] = useState(null);
  const [conflictState, setConflictState] = useState(null);

  const { timelineStart, totalDays } = useMemo(() => {
    const candidates = [];
    const push = (iso) => { if (iso) candidates.push(parseISO(iso).getTime()); };
    tasks.forEach((t) => { push(t.start_date); push(t.due_date); });
    push(project?.start_date); push(project?.target_end_date);
    const today = startOfDay(new Date()).getTime();
    candidates.push(today);
    const min = candidates.length ? Math.min(...candidates) : today;
    const max = candidates.length ? Math.max(...candidates) : today + 14 * 86400_000;
    const start = addDays(new Date(min), -7);
    const days = Math.max(30, differenceInDays(new Date(max), start) + 14);
    return { timelineStart: startOfDay(start), totalDays: days };
  }, [tasks, project?.start_date, project?.target_end_date]);

  const todayOffsetDays = differenceInDays(startOfDay(new Date()), timelineStart);
  const canvasWidth = totalDays * DAY_WIDTH;

  const positioned = tasks.map((t, idx) => {
    const due = t.due_date ? parseISO(t.due_date) : null;
    const start = t.start_date
      ? parseISO(t.start_date)
      : (due ?? parseISO(project?.start_date ?? timelineStart.toISOString()));
    const end = due ?? start;
    const x = differenceInDays(start, timelineStart) * DAY_WIDTH;
    const w = Math.max(1, differenceInDays(end, start) + 1) * DAY_WIDTH;
    return { task: t, x, w, y: idx * ROW_HEIGHT, start, end };
  });

  const boundsById = new Map(positioned.map((p) => [p.task.task_id ?? p.task._id, p]));
  const arrows = [];
  positioned.forEach((p) => {
    (p.task.dependencies ?? []).forEach((depId) => {
      const dep = boundsById.get(depId);
      if (!dep) return;
      const sx = dep.x + dep.w;
      const sy = dep.y + ROW_HEIGHT / 2 + HEADER_HEIGHT;
      const tx = p.x;
      const ty = p.y + ROW_HEIGHT / 2 + HEADER_HEIGHT;
      const c = Math.max(20, Math.abs(tx - sx) / 2);
      arrows.push({
        id: `${depId}->${p.task.task_id ?? p.task._id}`,
        d: `M ${sx} ${sy} C ${sx + c} ${sy}, ${tx - c} ${ty}, ${tx} ${ty}`,
      });
    });
  });

  const patchDates = async (t, newStart, newEnd) => {
    const taskId = t.task_id ?? t._id;
    const si = format(newStart, 'yyyy-MM-dd');
    const di = format(newEnd, 'yyyy-MM-dd');
    const patch = { start_date: si, due_date: di };

    // Optimistic update
    const optimisticDoc = { ...t, start_date: si, due_date: di };
    onTaskMutated?.(optimisticDoc);

    try {
      const res = await api.patch(`${API_URL}/api/projects/tasks/${taskId}`, patch, {
        headers: t.updated_at ? { 'If-Match': t.updated_at } : {},
      });
      if (res.status === 409) {
        const d = await res.json().catch(() => ({}));
        onTaskMutated?.(t); // revert
        setConflictState({ myDoc: optimisticDoc, theirDoc: d.current_doc ?? d, pendingPatch: patch });
        return;
      }
      if (!res.ok) {
        onTaskMutated?.(t); // revert
        toast.error("Couldn't update dates.");
        return;
      }
      const updated = await res.json();
      onTaskMutated?.(updated);
    } catch {
      onTaskMutated?.(t); // revert
      toast.error("Couldn't update dates.");
    }
  };

  if (loading) {
    return (
      <div className="min-h-[40vh] flex items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  return (
    <div data-testid="gantt-view" className="flex h-[calc(100vh-210px)]">
      {/* Left pane */}
      <div style={{ width: LEFT_PANE_WIDTH }} className="shrink-0 border-r border-aegisaxiom-border bg-aegisaxiom-surface overflow-y-auto">
        <div
          style={{ height: HEADER_HEIGHT }}
          className="sticky top-0 z-10 bg-aegisaxiom-elevated border-b border-aegisaxiom-border flex items-center px-3 text-[11px] uppercase tracking-wide text-aegisaxiom-text-muted"
        >
          Task
        </div>
        {positioned.map((p) => (
          <button
            key={p.task.task_id ?? p.task._id}
            type="button"
            onClick={() => setOpenTask(p.task)}
            data-testid={`gantt-tree-row-${p.task.task_id ?? p.task._id}`}
            style={{ height: ROW_HEIGHT }}
            className="w-full px-3 flex items-center gap-2 text-left text-sm border-b border-aegisaxiom-border hover:bg-aegisaxiom-elevated transition-colors"
          >
            <span className="text-[10px] text-aegisaxiom-text-muted font-mono">#{p.task.task_number ?? '—'}</span>
            <span className="truncate text-xs text-aegisaxiom-text">{p.task.title}</span>
          </button>
        ))}
      </div>

      {/* Timeline */}
      <div className="flex-1 overflow-auto bg-aegisaxiom-surface">
        <div style={{ width: canvasWidth, position: 'relative' }}>
          {/* Header */}
          <div
            style={{ height: HEADER_HEIGHT, width: canvasWidth }}
            className="sticky top-0 z-10 bg-aegisaxiom-elevated border-b border-aegisaxiom-border flex"
          >
            {Array.from({ length: totalDays }).map((_, i) => {
              const d = addDays(timelineStart, i);
              const isWeekend = d.getDay() === 0 || d.getDay() === 6;
              return (
                <div
                  key={i}
                  style={{ width: DAY_WIDTH }}
                  className={`shrink-0 text-[9px] text-center flex flex-col items-center justify-center border-r border-aegisaxiom-border/30 ${isWeekend ? 'bg-aegisaxiom-bg/50' : ''}`}
                >
                  <span className="text-aegisaxiom-text-muted">{format(d, 'EEE')[0]}</span>
                  <span className="font-semibold text-aegisaxiom-text">{format(d, 'd')}</span>
                </div>
              );
            })}
          </div>

          {/* Body */}
          <div style={{ position: 'relative', width: canvasWidth, height: positioned.length * ROW_HEIGHT }}>
            {positioned.map((p, i) => (
              <div
                key={`row-${p.task.task_id ?? p.task._id}`}
                style={{ top: i * ROW_HEIGHT, height: ROW_HEIGHT, width: canvasWidth }}
                className="absolute border-b border-aegisaxiom-border/20"
              />
            ))}
            {Array.from({ length: totalDays }).map((_, i) => {
              const d = addDays(timelineStart, i);
              const w = d.getDay() === 0 || d.getDay() === 6;
              if (!w) return null;
              return (
                <div
                  key={`we-${i}`}
                  style={{ left: i * DAY_WIDTH, width: DAY_WIDTH, top: 0, height: positioned.length * ROW_HEIGHT }}
                  className="absolute bg-aegisaxiom-bg/30 pointer-events-none"
                />
              );
            })}
            {todayOffsetDays >= 0 && todayOffsetDays < totalDays ? (
              <div
                data-testid="gantt-today-marker"
                style={{ left: todayOffsetDays * DAY_WIDTH + DAY_WIDTH / 2 - 1, top: 0, height: positioned.length * ROW_HEIGHT }}
                className="absolute w-0.5 bg-blue-500 z-10 pointer-events-none"
              >
                <span className="absolute -top-5 left-1/2 -translate-x-1/2 text-[9px] font-bold text-blue-400 bg-aegisaxiom-surface px-1 rounded-sm border border-blue-500/40">
                  TODAY
                </span>
              </div>
            ) : null}
            {positioned.map((p) => (
              <GanttBar
                key={p.task.task_id ?? p.task._id}
                task={p.task}
                x={p.x}
                y={p.y}
                w={p.w}
                dayWidth={DAY_WIDTH}
                timelineStart={timelineStart}
                canEdit={canEditTask}
                onOpen={setOpenTask}
                onPatchDates={patchDates}
              />
            ))}
            <svg
              data-testid="gantt-deps-svg"
              style={{ position: 'absolute', top: -HEADER_HEIGHT, left: 0, width: canvasWidth, height: positioned.length * ROW_HEIGHT + HEADER_HEIGHT, pointerEvents: 'none' }}
            >
              <defs>
                <marker id="arrow-gnt" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                  <path d="M0,0 L10,5 L0,10 z" fill="#6B7280" />
                </marker>
              </defs>
              {arrows.map((a) => (
                <path key={a.id} d={a.d} stroke="#6B7280" strokeWidth="1.5" fill="none" markerEnd="url(#arrow-gnt)" />
              ))}
            </svg>
          </div>
        </div>
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

function GanttBar({ task, x, y, w, dayWidth, timelineStart, canEdit, onOpen, onPatchDates }) {
  const mf = task.methodology_fields ?? {};
  const isMilestone = !!mf.milestone;
  const color = phaseColor(mf.phase);
  const ref = useRef(null);

  const startDragging = (mode) => (e) => {
    if (!canEdit) return;
    e.stopPropagation();
    const startX = e.clientX;
    const origX = x;
    const origW = w;
    const onMove = (ev) => {
      const delta = ev.clientX - startX;
      if (!ref.current) return;
      if (mode === 'move') {
        ref.current.style.transform = `translate(${delta}px, 0)`;
      } else if (mode === 'right') {
        ref.current.style.width = `${Math.max(dayWidth, origW + delta)}px`;
      } else {
        const nx = Math.min(origX + origW - dayWidth, origX + delta);
        ref.current.style.left = `${nx}px`;
        ref.current.style.width = `${Math.max(dayWidth, origW - delta)}px`;
      }
    };
    const onUp = async (ev) => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      const delta = ev.clientX - startX;
      const snap = Math.round(delta / dayWidth);
      if (!snap) {
        if (ref.current) {
          ref.current.style.transform = '';
          ref.current.style.left = `${origX}px`;
          ref.current.style.width = `${origW}px`;
        }
        return;
      }
      const startDays = Math.round(origX / dayWidth);
      const durationDays = Math.max(1, Math.round(origW / dayWidth));
      let newStart = addDays(timelineStart, startDays);
      let newEnd = addDays(newStart, durationDays - 1);
      if (mode === 'move') { newStart = addDays(newStart, snap); newEnd = addDays(newEnd, snap); }
      else if (mode === 'right') { newEnd = addDays(newEnd, snap); }
      else { newStart = addDays(newStart, snap); }
      if (newStart > newEnd) { const tmp = newStart; newStart = newEnd; newEnd = tmp; }
      await onPatchDates(task, newStart, newEnd);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp, { once: false });
  };

  if (isMilestone) {
    return (
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onOpen(task); }}
        data-testid={`gantt-milestone-${task.task_id ?? task._id}`}
        style={{ position: 'absolute', left: x + w - 8, top: y + ROW_HEIGHT / 2 - 8, width: 16, height: 16, transform: 'rotate(45deg)', background: '#6B2BA8' }}
        title={task.title}
        aria-label={task.title}
      />
    );
  }

  return (
    <div
      ref={ref}
      data-testid={`gantt-bar-${task.task_id ?? task._id}`}
      style={{ position: 'absolute', left: x, top: y + (ROW_HEIGHT - 24) / 2, width: w, height: 24, backgroundColor: color, borderRadius: 4, color: 'white', fontSize: 11, padding: '0 6px', display: 'flex', alignItems: 'center', cursor: 'grab', border: `1px solid ${color}` }}
      onClick={(e) => { e.stopPropagation(); onOpen(task); }}
      onPointerDown={startDragging('move')}
      title={task.title}
    >
      <span onPointerDown={startDragging('left')} style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 5, cursor: 'ew-resize', background: 'rgba(0,0,0,0.15)', borderTopLeftRadius: 4, borderBottomLeftRadius: 4 }} />
      <span className="truncate">{task.title}</span>
      <span onPointerDown={startDragging('right')} style={{ position: 'absolute', right: 0, top: 0, bottom: 0, width: 5, cursor: 'ew-resize', background: 'rgba(0,0,0,0.15)', borderTopRightRadius: 4, borderBottomRightRadius: 4 }} />
    </div>
  );
}
