import React, { useMemo, useState } from 'react';
import { format, parseISO } from 'date-fns';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import StatusPill from '../../widgets/StatusPill';
import PriorityFlag from '../../widgets/PriorityFlag';
import UserAvatar from '../../widgets/UserAvatar';
import TaskDetailDrawer from '../../widgets/TaskDetailDrawer';
import BulkActionBar from '../../widgets/BulkActionBar';
import { OpenPopoverProvider, InlineStatusPicker, InlinePriorityPicker, InlineAssigneePicker } from '../../widgets/InlineEditPopover';
import { usePermissions } from '@/hooks/usePermissions';
import QuickFilterPills from '../../smartViews/QuickFilterPills';

const BACKLOG_RAIL_KEY = '__backlog__';

export default function BacklogView({ project, tasks, sprints, assignees, onTasksChanged, onTaskMutated, onTaskDeleted, onConflict }) {
  const { hasPermission } = usePermissions();
  const canEditTask = hasPermission('projects.task.edit');
  const canChangeStatus = hasPermission('projects.task.change_status');
  const canDeleteTask = hasPermission('projects.task.delete');

  const [selectedKey, setSelectedKey] = useState(BACKLOG_RAIL_KEY);
  const [openTask, setOpenTask] = useState(null);
  const [selectedTasks, setSelectedTasks] = useState(new Set());
  const [moveOpen, setMoveOpen] = useState(false);
  const [moving, setMoving] = useState(false);

  // QuickFilterPills state
  const [filteredTaskIds, setFilteredTaskIds] = useState(null);

  const projectId = project?.project_id ?? project?._id;

  const sprintList = useMemo(() => {
    const all = sprints ?? [];
    const order = { active: 0, planned: 1, closed: 2 };
    return [...all].sort((a, b) => {
      if (order[a.status] !== order[b.status]) return order[a.status] - order[b.status];
      return (b.start_date ?? '').localeCompare(a.start_date ?? '');
    });
  }, [sprints]);

  const tasksFor = (railKey) => {
    if (railKey === BACKLOG_RAIL_KEY) return tasks.filter((t) => !t.methodology_fields?.sprint_id);
    return tasks.filter((t) => t.methodology_fields?.sprint_id === railKey);
  };

  const sprintFiltered = tasksFor(selectedKey);

  // Combine sprint rail filter + quick-filter pills
  const visible = useMemo(() => {
    if (!filteredTaskIds) return sprintFiltered;
    return sprintFiltered.filter((t) => filteredTaskIds.has(t.task_id ?? t._id));
  }, [sprintFiltered, filteredTaskIds]);

  const rollup = useMemo(() => {
    let points = 0;
    let hours = 0;
    for (const t of visible) {
      const sp = t.methodology_fields?.story_points;
      if (typeof sp === 'number') points += sp;
      if (typeof t.estimated_duration_hours === 'number') hours += t.estimated_duration_hours;
    }
    return { count: visible.length, points, hours };
  }, [visible]);

  const toggleSel = (id) =>
    setSelectedTasks((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });

  const handleClearSelection = () => setSelectedTasks(new Set());

  // Legacy move-to-sprint (kept for compat; BulkActionBar also handles this via bulk-sprint endpoint)
  const move = async (sprintId) => {
    setMoving(true);
    try {
      const taskIds = Array.from(selectedTasks);
      const responses = await Promise.all(
        taskIds.map((tid) => {
          const task = tasks.find((t) => (t.task_id ?? t._id) === tid);
          if (!task) return Promise.resolve(null);
          return api.patch(`${API_URL}/api/projects/tasks/${tid}`, {
            methodology_fields: { ...task.methodology_fields, sprint_id: sprintId },
          }).then(async (res) => {
            const data = await res.json().catch(() => null);
            if (!res.ok) {
              toast.error(data?.detail?.message ?? data?.detail ?? 'Could not move task.');
              return null;
            }
            return data;
          });
        }),
      );
      const sprint = sprintList.find((s) => s.sprint_id === sprintId);
      const succeeded = responses.filter(Boolean);
      if (succeeded.length > 0) {
        toast.success(`Moved ${succeeded.length} task(s) to ${sprint?.name ?? 'sprint'}`);
        setSelectedTasks(new Set());
        setMoveOpen(false);
        if (onTaskMutated) {
          succeeded.forEach((data) => { if (data.task_id ?? data._id) onTaskMutated(data); });
        } else {
          onTasksChanged?.();
        }
      }
    } catch {
      toast.error('Could not move tasks.');
    } finally {
      setMoving(false);
    }
  };

  return (
    <OpenPopoverProvider>
      <section data-testid="backlog-view" className="flex h-[calc(100vh-210px)]">
        {/* Left rail */}
        <aside className="w-[280px] shrink-0 border-r border-aegisaxiom-border bg-aegisaxiom-surface overflow-y-auto" data-testid="backlog-rail">
          <RailRow
            label="Backlog"
            sub={`${tasksFor(BACKLOG_RAIL_KEY).length} tasks`}
            active={selectedKey === BACKLOG_RAIL_KEY}
            onClick={() => setSelectedKey(BACKLOG_RAIL_KEY)}
            testid="rail-backlog"
          />
          {sprintList.map((s) => {
            const dateRange = (s.start_date && s.end_date)
              ? `${format(parseISO(s.start_date), 'MMM d')} – ${format(parseISO(s.end_date), 'MMM d')}`
              : '— no dates —';
            return (
              <RailRow
                key={s.sprint_id}
                label={s.name}
                sub={`${dateRange} · ${tasksFor(s.sprint_id).length} tasks`}
                badge={s.status}
                active={selectedKey === s.sprint_id}
                onClick={() => setSelectedKey(s.sprint_id)}
                testid={`rail-sprint-${s.sprint_id}`}
              />
            );
          })}
        </aside>

        {/* Right pane */}
        <div className="flex-1 overflow-y-auto flex flex-col">
          {/* QuickFilterPills slot */}
          {QuickFilterPills ? (
            <div className="px-6 pt-3 pb-1">
              <QuickFilterPills
                view="backlog"
                tasks={tasks}
                projectId={projectId}
                onFilterChange={(ids) => setFilteredTaskIds(ids?.size > 0 ? ids : null)}
              />
            </div>
          ) : null}

          <header className="px-6 py-3 border-b border-aegisaxiom-border flex items-center gap-3">
            <h2 className="text-sm font-semibold text-aegisaxiom-text" data-testid="backlog-title">
              {selectedKey === BACKLOG_RAIL_KEY
                ? 'Backlog'
                : sprintList.find((s) => s.sprint_id === selectedKey)?.name ?? '—'}
            </h2>
            <span className="text-xs text-aegisaxiom-text-muted" data-testid="backlog-rollup">
              {rollup.count} tasks · {rollup.points} pts · ~{rollup.hours}h
            </span>
            {canEditTask && selectedTasks.size > 0 ? (
              <button
                type="button"
                onClick={() => setMoveOpen(true)}
                className="btn-primary ml-auto h-8 px-3 text-xs"
                data-testid="backlog-move-to-sprint"
              >
                Move {selectedTasks.size} to sprint…
              </button>
            ) : null}
          </header>

          <div className="px-6 py-3 flex-1">
            {visible.length === 0 ? (
              <p className="text-sm text-aegisaxiom-text-muted" data-testid="backlog-empty">No tasks in this view.</p>
            ) : (
              <ul className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface overflow-hidden">
                {visible.map((t) => {
                  const assignee = (assignees ?? []).find((u) => (u.user_id ?? u._id) === t.assignee_id);
                  const sp = t.methodology_fields?.story_points;
                  const tid = t.task_id ?? t._id;
                  return (
                    <li
                      key={tid}
                      className="h-10 flex items-center gap-3 px-3 border-b last:border-b-0 border-aegisaxiom-border text-sm hover:bg-aegisaxiom-elevated/50"
                      data-testid={`backlog-row-${tid}`}
                    >
                      <input
                        type="checkbox"
                        checked={selectedTasks.has(tid)}
                        disabled={!canEditTask}
                        onChange={() => toggleSel(tid)}
                        data-testid={`backlog-check-${tid}`}
                      />
                      <button
                        type="button"
                        onClick={() => setOpenTask(t)}
                        className="flex-1 text-left truncate font-medium text-aegisaxiom-text hover:text-aegisaxiom-text-muted"
                      >
                        <span className="text-[10px] text-aegisaxiom-text-muted font-mono mr-1.5">
                          #{t.task_number ?? '—'}
                        </span>
                        {t.title}
                      </button>

                      {/* Inline priority */}
                      <InlinePriorityPicker task={t} onTaskMutated={onTaskMutated} onConflict={onConflict}>
                        <PriorityFlag priority={t.priority} />
                      </InlinePriorityPicker>

                      {typeof sp === 'number' ? (
                        <span className="text-[10px] w-8 text-right font-semibold text-aegisaxiom-text">{sp}pt</span>
                      ) : (
                        <span className="w-8" />
                      )}

                      {/* Inline status */}
                      <InlineStatusPicker task={t} project={project} onTaskMutated={onTaskMutated} onConflict={onConflict}>
                        <StatusPill status={t.status} size="sm" />
                      </InlineStatusPicker>

                      {/* Inline assignee */}
                      <InlineAssigneePicker task={t} assignees={assignees} onTaskMutated={onTaskMutated} onConflict={onConflict}>
                        <UserAvatar user={assignee ?? null} size="sm" />
                      </InlineAssigneePicker>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>

        {/* Move-to-sprint modal (legacy; BulkActionBar's bulk-sprint is preferred) */}
        {moveOpen ? (
          <div
            className="fixed inset-0 bg-black/40 z-40 flex items-center justify-center"
            onClick={() => setMoveOpen(false)}
            data-testid="backlog-move-modal"
          >
            <div className="glass-card w-[360px] p-4" onClick={(e) => e.stopPropagation()}>
              <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3">Move to sprint</h3>
              <ul className="space-y-1 max-h-[300px] overflow-y-auto">
                {sprintList.filter((s) => s.status !== 'closed').map((s) => (
                  <li key={s.sprint_id}>
                    <button
                      type="button"
                      onClick={() => move(s.sprint_id)}
                      disabled={moving}
                      className="w-full text-left px-3 py-2 rounded-md hover:bg-aegisaxiom-elevated text-sm text-aegisaxiom-text disabled:opacity-60"
                      data-testid={`backlog-move-option-${s.sprint_id}`}
                    >
                      <span className="font-medium">{s.name}</span>
                      <span className="ml-2 text-[11px] text-aegisaxiom-text-muted">{s.status}</span>
                    </button>
                  </li>
                ))}
              </ul>
              <div className="mt-3 text-right">
                <button type="button" onClick={() => setMoveOpen(false)} className="btn-secondary h-8 px-3 text-xs">
                  Cancel
                </button>
              </div>
            </div>
          </div>
        ) : null}

        <TaskDetailDrawer
          open={openTask !== null}
          onOpenChange={(v) => { if (!v) setOpenTask(null); }}
          task={openTask}
          project={project}
          allTasks={tasks}
          onTaskUpdated={onTaskMutated ?? onTasksChanged}
          onTaskDeleted={onTaskDeleted}
          onConflict={onConflict}
        />

        {/* Floating bulk bar */}
        <BulkActionBar
          selectedIds={selectedTasks}
          onClear={handleClearSelection}
          onTaskMutated={onTaskMutated}
          onTaskDeleted={onTaskDeleted}
          assignees={assignees}
          sprints={sprints}
          canDelete={canDeleteTask}
          tasks={tasks}
          project={project}
        />
      </section>
    </OpenPopoverProvider>
  );
}

function RailRow({ label, sub, badge, active, onClick, testid }) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testid}
      className={`w-full text-left px-3 py-2 border-b border-aegisaxiom-border transition-colors ${
        active ? 'bg-aegisaxiom-elevated' : 'hover:bg-aegisaxiom-elevated/50'
      }`}
    >
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold text-aegisaxiom-text truncate flex-1">{label}</span>
        {badge ? (
          <span className="inline-flex items-center h-4 px-1.5 rounded bg-aegisaxiom-elevated text-[9px] font-bold text-aegisaxiom-text-muted border border-aegisaxiom-border uppercase">
            {badge}
          </span>
        ) : null}
      </div>
      <div className="text-[10px] text-aegisaxiom-text-muted mt-0.5">{sub}</div>
    </button>
  );
}
