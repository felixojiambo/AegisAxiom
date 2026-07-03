import React, { useMemo, useState } from 'react';
import { format, parseISO } from 'date-fns';
import StatusPill from '../../widgets/StatusPill';
import TaskCard from '../../widgets/TaskCard';
import TaskDetailDrawer from '../../widgets/TaskDetailDrawer';

// Sprint list + active sprint board composite view.
export default function SprintsView({ project, tasks, sprints, assignees, onTasksChanged, onTaskMutated, onTaskDeleted, onConflict }) {
  const [selected, setSelected] = useState(null);
  const [openTask, setOpenTask] = useState(null);

  const sortedSprints = useMemo(() => {
    const order = { active: 0, planned: 1, closed: 2 };
    return [...(sprints ?? [])].sort((a, b) => order[a.status] - order[b.status]);
  }, [sprints]);

  const activeSprint = sortedSprints.find((s) => s.status === 'active') ?? null;
  const displaySprint = selected ?? activeSprint ?? sortedSprints[0] ?? null;

  const sprintTasks = useMemo(
    () =>
      displaySprint
        ? tasks.filter((t) => t.methodology_fields?.sprint_id === displaySprint.sprint_id)
        : [],
    [tasks, displaySprint],
  );

  const columns = project?.methodology_config?.columns ?? [];
  const byColumn = useMemo(() => {
    const m = new Map();
    columns.forEach((c) => m.set(c.id, []));
    sprintTasks.forEach((t) => {
      const cid = t.methodology_fields?.column_id;
      if (cid && m.has(cid)) m.get(cid).push(t);
      else if (columns[0]) m.get(columns[0].id).push(t);
    });
    return m;
  }, [sprintTasks, columns]);

  return (
    <div data-testid="sprints-view" className="flex h-[calc(100vh-210px)]">
      {/* Sprint rail */}
      <aside className="w-[220px] shrink-0 border-r border-aegisaxiom-border bg-aegisaxiom-surface overflow-y-auto" data-testid="sprints-rail">
        {sortedSprints.map((s) => {
          const isActive = (displaySprint?.sprint_id) === s.sprint_id;
          return (
            <button
              key={s.sprint_id}
              type="button"
              onClick={() => setSelected(s)}
              data-testid={`sprint-rail-${s.sprint_id}`}
              className={`w-full text-left px-3 py-2 border-b border-aegisaxiom-border transition-colors ${isActive ? 'bg-aegisaxiom-elevated' : 'hover:bg-aegisaxiom-elevated/50'}`}
            >
              <div className="flex items-center gap-1.5">
                <span className="text-sm font-semibold text-aegisaxiom-text truncate flex-1">{s.name}</span>
                <StatusPill status={s.status} size="sm" />
              </div>
              <div className="text-[10px] text-aegisaxiom-text-muted mt-0.5">
                {s.start_date ? format(parseISO(s.start_date), 'MMM d') : '—'} – {s.end_date ? format(parseISO(s.end_date), 'MMM d') : '—'}
              </div>
            </button>
          );
        })}
        {sortedSprints.length === 0 ? (
          <p className="p-4 text-xs text-aegisaxiom-text-muted">No sprints yet.</p>
        ) : null}
      </aside>

      {/* Sprint board */}
      <div className="flex-1 overflow-auto p-4">
        {displaySprint ? (
          <>
            <header className="mb-3 flex items-center gap-3">
              <h2 className="text-sm font-bold text-aegisaxiom-text">{displaySprint.name}</h2>
              <StatusPill status={displaySprint.status} size="sm" />
              <span className="text-xs text-aegisaxiom-text-muted">
                {displaySprint.start_date ? format(parseISO(displaySprint.start_date), 'MMM d') : '—'} – {displaySprint.end_date ? format(parseISO(displaySprint.end_date), 'MMM d') : '—'}
              </span>
              {displaySprint.goal ? (
                <span className="text-xs text-aegisaxiom-text-muted italic truncate max-w-[320px]">
                  {displaySprint.goal}
                </span>
              ) : null}
            </header>
            <div className="flex gap-3 overflow-x-auto pb-4">
              {columns.map((c) => {
                const colTasks = byColumn.get(c.id) ?? [];
                return (
                  <div
                    key={c.id}
                    className="w-[280px] shrink-0 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface flex flex-col"
                    data-testid={`sprints-column-${c.id}`}
                  >
                    <header className="h-9 px-3 flex items-center gap-2 border-b border-aegisaxiom-border">
                      <span className="text-xs font-semibold text-aegisaxiom-text">{c.name}</span>
                      <span className="ml-auto text-[10px] text-aegisaxiom-text-muted">{colTasks.length}</span>
                    </header>
                    <div className="p-2 space-y-2 flex-1 min-h-[60px]">
                      {colTasks.map((t) => {
                        const assignee = (assignees ?? []).find((u) => (u.user_id ?? u._id) === t.assignee_id);
                        return (
                          <TaskCard key={t.task_id ?? t._id} task={t} project={project} onClick={setOpenTask} assignee={assignee} onTaskMutated={onTaskMutated} onTaskDeleted={onTaskDeleted} onConflict={onConflict} />
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        ) : (
          <p className="text-sm text-aegisaxiom-text-muted">Select a sprint from the list.</p>
        )}
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
    </div>
  );
}
