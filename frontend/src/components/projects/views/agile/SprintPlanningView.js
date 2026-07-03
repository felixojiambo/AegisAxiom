import React, { useMemo, useState } from 'react';
import { format, parseISO } from 'date-fns';
import { Plus, ArrowLeftCircle } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { extractTaskErrorMessage } from '@/lib/safeXhr';
import { usePermissions } from '@/hooks/usePermissions';
import StatusPill from '../../widgets/StatusPill';
import PriorityFlag from '../../widgets/PriorityFlag';
import UserAvatar from '../../widgets/UserAvatar';

// Wave 6 / 6B2 — sprint planning ceremony view.
//
// Two panes:
//   - Left: backlog (tasks with no sprint_id). Drag rows or use the "+"
//     button to add to the selected planning sprint.
//   - Right: the selected upcoming sprint with goal + capacity inputs and
//     the working set of committed tasks. Each row has a "send back" button.
//
// We pick the first non-closed sprint by default (active first, then
// planned). PMs select another via the dropdown if multiple are open.
//
// The view PATCHes individual task.methodology_fields.sprint_id and the
// sprint goal/capacity_points via /sprints/{id}. No /close from here —
// that lives on SprintReviewView.

function pointsTotal(tasks) {
  let pts = 0;
  for (const t of tasks) {
    const sp = t.methodology_fields?.story_points;
    if (typeof sp === 'number') pts += sp;
  }
  return pts;
}

function sortByPriorityThenCreated(tasks) {
  // High priority first, then most-recently-created so freshly groomed
  // backlog items don't sink to the bottom.
  const order = { critical: 0, high: 1, medium: 2, low: 3 };
  return [...tasks].sort((a, b) => {
    const ap = order[a.priority] ?? 9;
    const bp = order[b.priority] ?? 9;
    if (ap !== bp) return ap - bp;
    return (b.created_at ?? '').localeCompare(a.created_at ?? '');
  });
}

export default function SprintPlanningView({
  project,
  tasks,
  sprints,
  assignees,
  onTasksChanged,
  onTaskMutated,
  onSprintsChanged,
}) {
  const { hasPermission } = usePermissions();
  const canEditTask = hasPermission('projects.task.edit');
  const canEditSprint = hasPermission('projects.sprint.edit');

  // Open sprints (not closed) — sort active before planned so a running
  // sprint is the default when no "next" sprint exists yet.
  const openSprints = useMemo(() => {
    const order = { active: 0, planned: 1 };
    return [...(sprints ?? [])]
      .filter((s) => s.status !== 'closed')
      .sort((a, b) => {
        const ao = order[a.status] ?? 9;
        const bo = order[b.status] ?? 9;
        if (ao !== bo) return ao - bo;
        return (a.start_date ?? '').localeCompare(b.start_date ?? '');
      });
  }, [sprints]);

  const [selectedId, setSelectedId] = useState(openSprints[0]?.sprint_id ?? null);
  const sprint = useMemo(
    () => (sprints ?? []).find((s) => s.sprint_id === selectedId) ?? openSprints[0] ?? null,
    [sprints, selectedId, openSprints],
  );

  // Local mirrors of goal + capacity so the inputs feel responsive; flushed
  // on blur via PATCH /sprints/{id}.
  const [goalDraft, setGoalDraft] = useState(sprint?.goal ?? '');
  const [capacityDraft, setCapacityDraft] = useState(
    sprint?.capacity_points != null ? String(sprint.capacity_points) : '',
  );
  const [savingMeta, setSavingMeta] = useState(false);

  // Re-sync drafts when the user picks a different sprint.
  React.useEffect(() => {
    setGoalDraft(sprint?.goal ?? '');
    setCapacityDraft(sprint?.capacity_points != null ? String(sprint.capacity_points) : '');
  }, [sprint?.sprint_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const backlog = useMemo(
    () => sortByPriorityThenCreated(tasks.filter((t) => !t.methodology_fields?.sprint_id)),
    [tasks],
  );
  const committed = useMemo(
    () =>
      sortByPriorityThenCreated(
        sprint
          ? tasks.filter((t) => t.methodology_fields?.sprint_id === sprint.sprint_id)
          : [],
      ),
    [tasks, sprint?.sprint_id],
  );

  const committedPoints = pointsTotal(committed);
  const capacityNum = sprint?.capacity_points;
  const overCapacity =
    typeof capacityNum === 'number' && capacityNum > 0 && committedPoints > capacityNum;
  const underCapacity =
    typeof capacityNum === 'number' && capacityNum > 0 && committedPoints < capacityNum;

  const movingRef = React.useRef(new Set());
  const [, forceTick] = useState(0);

  const moveTask = async (taskId, targetSprintId) => {
    if (!canEditTask) return;
    const task = tasks.find((t) => (t.task_id ?? t._id) === taskId);
    if (!task) return;
    movingRef.current.add(taskId);
    forceTick((n) => n + 1);
    try {
      const res = await api.patch(`${API_URL}/api/projects/tasks/${taskId}`, {
        methodology_fields: { ...task.methodology_fields, sprint_id: targetSprintId },
      });
      if (!res.ok) {
        toast.error(await extractTaskErrorMessage(res, 'Could not move task.'));
        return;
      }
      const data = await res.json().catch(() => ({}));
      if (onTaskMutated && (data?.task_id ?? data?._id)) {
        onTaskMutated(data);
      } else {
        onTasksChanged?.();
      }
    } catch {
      toast.error('Could not move task.');
    } finally {
      movingRef.current.delete(taskId);
      forceTick((n) => n + 1);
    }
  };

  const handleSaveMeta = async (patch) => {
    if (!sprint || !canEditSprint) return;
    setSavingMeta(true);
    try {
      const res = await api.patch(`${API_URL}/api/projects/sprints/${sprint.sprint_id}`, patch);
      if (!res.ok) {
        toast.error(await extractTaskErrorMessage(res, 'Could not save sprint.'));
        return;
      }
      onSprintsChanged?.();
    } catch {
      toast.error('Could not save sprint.');
    } finally {
      setSavingMeta(false);
    }
  };

  const handleGoalBlur = () => {
    const next = goalDraft.trim() || null;
    if ((sprint?.goal ?? null) === next) return;
    handleSaveMeta({ goal: next });
  };

  const handleCapacityBlur = () => {
    if (capacityDraft === '') {
      if (sprint?.capacity_points != null) handleSaveMeta({ capacity_points: null });
      return;
    }
    const parsed = parseInt(capacityDraft, 10);
    if (Number.isNaN(parsed) || parsed < 0) {
      toast.error('Capacity must be a non-negative integer.');
      setCapacityDraft(sprint?.capacity_points != null ? String(sprint.capacity_points) : '');
      return;
    }
    if (parsed === sprint?.capacity_points) return;
    handleSaveMeta({ capacity_points: parsed });
  };

  // ---- Drag-drop handlers (HTML5, native drag) ----
  const onDragStart = (e, taskId, fromKey) => {
    if (!canEditTask) return;
    e.dataTransfer.setData('text/plain', JSON.stringify({ taskId, fromKey }));
    e.dataTransfer.effectAllowed = 'move';
  };

  const onDragOver = (e) => {
    if (!canEditTask) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  };

  const onDropToSprint = (e) => {
    e.preventDefault();
    if (!sprint) return;
    try {
      const data = JSON.parse(e.dataTransfer.getData('text/plain'));
      if (data.fromKey === 'sprint') return;
      moveTask(data.taskId, sprint.sprint_id);
    } catch {
      // ignored
    }
  };

  const onDropToBacklog = (e) => {
    e.preventDefault();
    try {
      const data = JSON.parse(e.dataTransfer.getData('text/plain'));
      if (data.fromKey === 'backlog') return;
      moveTask(data.taskId, null);
    } catch {
      // ignored
    }
  };

  if (openSprints.length === 0) {
    return (
      <section
        data-testid="sprint-planning-empty"
        className="glass-card p-8 text-center mt-4"
      >
        <p className="text-sm text-aegisaxiom-text font-medium mb-1">No upcoming sprints to plan.</p>
        <p className="text-xs text-aegisaxiom-text-muted">
          Create a sprint from the Sprints tab to start planning.
        </p>
      </section>
    );
  }

  return (
    <section data-testid="sprint-planning-view" className="flex flex-col h-[calc(100vh-210px)]">
      {/* Header — sprint picker + meta */}
      <header className="flex flex-wrap items-center gap-3 px-4 py-3 border-b border-aegisaxiom-border bg-aegisaxiom-surface">
        <label className="text-xs uppercase tracking-wide text-aegisaxiom-text-muted">Planning</label>
        <select
          className="aegisaxiom-input h-9 text-sm max-w-[260px]"
          value={sprint?.sprint_id ?? ''}
          onChange={(e) => setSelectedId(e.target.value)}
          data-testid="planning-sprint-select"
        >
          {openSprints.map((s) => (
            <option key={s.sprint_id} value={s.sprint_id}>
              {s.name} ({s.status})
            </option>
          ))}
        </select>
        {sprint?.start_date && sprint?.end_date ? (
          <span className="text-xs text-aegisaxiom-text-muted">
            {format(parseISO(sprint.start_date), 'MMM d')} –{' '}
            {format(parseISO(sprint.end_date), 'MMM d')}
          </span>
        ) : null}
        <StatusPill status={sprint?.status} size="sm" />
        {savingMeta ? (
          <span className="text-[11px] text-aegisaxiom-text-muted" data-testid="planning-saving">
            Saving…
          </span>
        ) : null}
      </header>

      {/* Main planning area: backlog | committed sprint */}
      <div className="flex-1 grid grid-cols-1 lg:grid-cols-2 gap-3 p-3 overflow-hidden">
        {/* Backlog pane */}
        <div
          className="flex flex-col rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface overflow-hidden"
          onDragOver={onDragOver}
          onDrop={onDropToBacklog}
          data-testid="planning-backlog-pane"
        >
          <header className="px-4 py-2 border-b border-aegisaxiom-border flex items-center gap-2">
            <h3 className="text-sm font-semibold text-aegisaxiom-text">Backlog</h3>
            <span className="text-xs text-aegisaxiom-text-muted">
              {backlog.length} tasks · {pointsTotal(backlog)} pts
            </span>
          </header>
          <ul className="flex-1 overflow-y-auto" data-testid="planning-backlog-list">
            {backlog.length === 0 ? (
              <li className="p-6 text-xs text-aegisaxiom-text-muted text-center">
                Backlog is empty.
              </li>
            ) : (
              backlog.map((t) => {
                const tid = t.task_id ?? t._id;
                const assignee = (assignees ?? []).find(
                  (u) => (u.user_id ?? u._id) === t.assignee_id,
                );
                const moving = movingRef.current.has(tid);
                return (
                  <li
                    key={tid}
                    draggable={canEditTask && !moving}
                    onDragStart={(e) => onDragStart(e, tid, 'backlog')}
                    className={`h-11 flex items-center gap-3 px-3 border-b border-aegisaxiom-border text-sm hover:bg-aegisaxiom-elevated/50 ${
                      moving ? 'opacity-50' : ''
                    } ${canEditTask ? 'cursor-grab' : ''}`}
                    data-testid={`planning-backlog-row-${tid}`}
                  >
                    <span className="flex-1 min-w-0 truncate font-medium text-aegisaxiom-text">
                      <span className="text-[10px] text-aegisaxiom-text-muted font-mono mr-1.5">
                        #{t.task_number ?? '—'}
                      </span>
                      {t.title}
                    </span>
                    <PriorityFlag priority={t.priority} />
                    <span className="text-[10px] w-8 text-right font-semibold text-aegisaxiom-text">
                      {typeof t.methodology_fields?.story_points === 'number'
                        ? `${t.methodology_fields.story_points}pt`
                        : ''}
                    </span>
                    <UserAvatar user={assignee ?? null} size="sm" />
                    {canEditTask ? (
                      <button
                        type="button"
                        disabled={!sprint || moving}
                        onClick={() => moveTask(tid, sprint?.sprint_id)}
                        className="h-7 w-7 inline-flex items-center justify-center rounded-md text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors disabled:opacity-40"
                        aria-label="Add to sprint"
                        data-testid={`planning-add-${tid}`}
                      >
                        <Plus size={13} />
                      </button>
                    ) : null}
                  </li>
                );
              })
            )}
          </ul>
        </div>

        {/* Committed sprint pane */}
        <div
          className="flex flex-col rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface overflow-hidden"
          onDragOver={onDragOver}
          onDrop={onDropToSprint}
          data-testid="planning-sprint-pane"
        >
          <header className="px-4 py-2 border-b border-aegisaxiom-border space-y-2">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-aegisaxiom-text">
                {sprint?.name ?? 'Sprint'}
              </h3>
              <span
                className="text-xs text-aegisaxiom-text-muted"
                data-testid="planning-committed-rollup"
              >
                {committed.length} tasks · {committedPoints} pts
                {typeof capacityNum === 'number' && capacityNum > 0
                  ? ` / ${capacityNum} cap`
                  : ''}
              </span>
              {overCapacity ? (
                <span
                  className="inline-flex items-center h-5 px-2 rounded text-[10px] font-bold bg-red-500/15 text-red-400"
                  data-testid="planning-over-capacity"
                >
                  Over capacity
                </span>
              ) : underCapacity ? (
                <span
                  className="inline-flex items-center h-5 px-2 rounded text-[10px] font-medium bg-yellow-500/15 text-yellow-400"
                  data-testid="planning-under-capacity"
                >
                  Under target
                </span>
              ) : null}
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
              <div className="md:col-span-2">
                <label className="block text-[10px] uppercase tracking-wide text-aegisaxiom-text-muted mb-1">
                  Sprint goal
                </label>
                <input
                  className="aegisaxiom-input w-full h-8 text-sm"
                  value={goalDraft}
                  onChange={(e) => setGoalDraft(e.target.value)}
                  onBlur={handleGoalBlur}
                  placeholder="e.g. Ship onboarding v2"
                  disabled={!canEditSprint}
                  maxLength={2000}
                  data-testid="planning-goal-input"
                />
              </div>
              <div>
                <label className="block text-[10px] uppercase tracking-wide text-aegisaxiom-text-muted mb-1">
                  Capacity (pts)
                </label>
                <input
                  type="number"
                  min="0"
                  max="10000"
                  className="aegisaxiom-input w-full h-8 text-sm"
                  value={capacityDraft}
                  onChange={(e) => setCapacityDraft(e.target.value)}
                  onBlur={handleCapacityBlur}
                  placeholder="—"
                  disabled={!canEditSprint}
                  data-testid="planning-capacity-input"
                />
              </div>
            </div>
          </header>
          <ul className="flex-1 overflow-y-auto" data-testid="planning-committed-list">
            {committed.length === 0 ? (
              <li className="p-6 text-xs text-aegisaxiom-text-muted text-center">
                Drag tasks here, or use the + button on a backlog row.
              </li>
            ) : (
              committed.map((t) => {
                const tid = t.task_id ?? t._id;
                const assignee = (assignees ?? []).find(
                  (u) => (u.user_id ?? u._id) === t.assignee_id,
                );
                const moving = movingRef.current.has(tid);
                return (
                  <li
                    key={tid}
                    draggable={canEditTask && !moving}
                    onDragStart={(e) => onDragStart(e, tid, 'sprint')}
                    className={`h-11 flex items-center gap-3 px-3 border-b border-aegisaxiom-border text-sm hover:bg-aegisaxiom-elevated/50 ${
                      moving ? 'opacity-50' : ''
                    } ${canEditTask ? 'cursor-grab' : ''}`}
                    data-testid={`planning-committed-row-${tid}`}
                  >
                    <span className="flex-1 min-w-0 truncate font-medium text-aegisaxiom-text">
                      <span className="text-[10px] text-aegisaxiom-text-muted font-mono mr-1.5">
                        #{t.task_number ?? '—'}
                      </span>
                      {t.title}
                    </span>
                    <PriorityFlag priority={t.priority} />
                    <span className="text-[10px] w-8 text-right font-semibold text-aegisaxiom-text">
                      {typeof t.methodology_fields?.story_points === 'number'
                        ? `${t.methodology_fields.story_points}pt`
                        : ''}
                    </span>
                    <UserAvatar user={assignee ?? null} size="sm" />
                    {canEditTask ? (
                      <button
                        type="button"
                        disabled={moving}
                        onClick={() => moveTask(tid, null)}
                        className="h-7 w-7 inline-flex items-center justify-center rounded-md text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors disabled:opacity-40"
                        aria-label="Send back to backlog"
                        data-testid={`planning-remove-${tid}`}
                      >
                        <ArrowLeftCircle size={13} />
                      </button>
                    ) : null}
                  </li>
                );
              })
            )}
          </ul>
        </div>
      </div>
    </section>
  );
}
