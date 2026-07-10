import React, { useEffect, useMemo, useState } from 'react';
import { format, parseISO } from 'date-fns';
import { CheckCircle2, Square } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { extractTaskErrorMessage } from '@/lib/safeXhr';
import ConfirmModal from '@/components/shared/ConfirmModal';
import { usePermissions } from '@/hooks/usePermissions';
import StatusPill from '../../widgets/StatusPill';
import PriorityFlag from '../../widgets/PriorityFlag';
import UserAvatar from '../../widgets/UserAvatar';
import VelocityChart from '../../widgets/VelocityChart';

// Wave 6 / 6B3 — sprint review ceremony view.
//
// Layout:
//   - Header: velocity number prominent + sprint meta + close-sprint
//     action when status is 'active'.
//   - Body: two columns — completed tasks (left), retrospective form
//     (right). Retro fields PATCH /sprints/{id} on save.
//
// VelocityChart embeds in the header in a follow-up task (6B4).
//
// Sprint selection rule: prefer the most-recently-closed sprint by default
// (so opening this view after running /close lands on the sprint you just
// closed). Fall back to active, then to the first available sprint.

function isDoneTask(task, doneColumnIds) {
  const colId = task.methodology_fields?.column_id;
  if (colId && doneColumnIds.has(colId)) return true;
  return task.status === 'complete' || task.status === 'shipped';
}

function pickDefaultSprintId(sprints) {
  if (!sprints?.length) return null;
  const closed = sprints
    .filter((s) => s.status === 'closed')
    .sort((a, b) => (b.closed_at ?? b.end_date ?? '').localeCompare(a.closed_at ?? a.end_date ?? ''));
  if (closed[0]) return closed[0].sprint_id;
  const active = sprints.find((s) => s.status === 'active');
  if (active) return active.sprint_id;
  return sprints[0].sprint_id;
}

export default function SprintReviewView({
  project,
  tasks,
  sprints,
  assignees,
  onTasksChanged,
  onSprintsChanged,
}) {
  const { hasPermission } = usePermissions();
  const canEditSprint = hasPermission('projects.sprint.edit');
  const canCloseSprint = hasPermission('projects.sprint.change_status');

  const sortedSprints = useMemo(() => {
    const order = { closed: 0, active: 1, planned: 2 };
    return [...(sprints ?? [])].sort((a, b) => {
      const ao = order[a.status] ?? 9;
      const bo = order[b.status] ?? 9;
      if (ao !== bo) return ao - bo;
      return (b.start_date ?? '').localeCompare(a.start_date ?? '');
    });
  }, [sprints]);

  const [selectedId, setSelectedId] = useState(() => pickDefaultSprintId(sprints));
  // Auto-pick a default once sprints arrive (or if it changes underneath us).
  useEffect(() => {
    if (selectedId && (sprints ?? []).some((s) => s.sprint_id === selectedId)) return;
    setSelectedId(pickDefaultSprintId(sprints));
  }, [sprints, selectedId]);

  const sprint = useMemo(
    () => (sprints ?? []).find((s) => s.sprint_id === selectedId) ?? null,
    [sprints, selectedId],
  );

  // Retrospective form state — mirrors sprint.retrospective and resets when
  // the sprint switches.
  const [wentWell, setWentWell] = useState('');
  const [didnt, setDidnt] = useState('');
  const [actionItems, setActionItems] = useState('');
  const [savingRetro, setSavingRetro] = useState(false);
  const [closeOpen, setCloseOpen] = useState(false);
  const [closing, setClosing] = useState(false);

  useEffect(() => {
    setWentWell(sprint?.retrospective?.went_well ?? '');
    setDidnt(sprint?.retrospective?.didnt_go_well ?? '');
    setActionItems(sprint?.retrospective?.action_items ?? '');
  }, [sprint?.sprint_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const doneColumnIds = useMemo(() => {
    const cols = project?.methodology_config?.columns ?? [];
    return new Set(cols.filter((c) => c.is_done).map((c) => c.id));
  }, [project?.methodology_config]);

  const sprintTasks = useMemo(() => {
    if (!sprint) return [];
    return tasks.filter((t) => t.methodology_fields?.sprint_id === sprint.sprint_id);
  }, [tasks, sprint?.sprint_id]);

  const completedTasks = useMemo(
    () => sprintTasks.filter((t) => isDoneTask(t, doneColumnIds)),
    [sprintTasks, doneColumnIds],
  );
  const incompleteTasks = useMemo(
    () => sprintTasks.filter((t) => !isDoneTask(t, doneColumnIds)),
    [sprintTasks, doneColumnIds],
  );

  // Velocity = sum of story_points on completed tasks. We prefer the
  // server-stored `completed_points` (only populated post-close) when
  // available, since /close may have looked at columns that have since
  // shifted. For active sprints, compute live.
  const liveCompletedPoints = useMemo(() => {
    let pts = 0;
    for (const t of completedTasks) {
      const sp = t.methodology_fields?.story_points;
      if (typeof sp === 'number') pts += sp;
    }
    return pts;
  }, [completedTasks]);
  const velocity =
    sprint?.status === 'closed' && typeof sprint?.completed_points === 'number'
      ? sprint.completed_points
      : liveCompletedPoints;

  const capacityPoints = sprint?.capacity_points;
  const completionPct =
    typeof capacityPoints === 'number' && capacityPoints > 0
      ? Math.round((velocity / capacityPoints) * 100)
      : null;

  const handleSaveRetro = async () => {
    if (!sprint || !canEditSprint) return;
    setSavingRetro(true);
    try {
      const res = await api.patch(`${API_URL}/api/projects/sprints/${sprint.sprint_id}`, {
        retrospective: {
          went_well: wentWell,
          didnt_go_well: didnt,
          action_items: actionItems,
        },
      });
      if (!res.ok) {
        toast.error(await extractTaskErrorMessage(res, 'Could not save retrospective.'));
        return;
      }
      toast.success('Retrospective saved');
      onSprintsChanged?.();
    } catch {
      toast.error('Could not save retrospective.');
    } finally {
      setSavingRetro(false);
    }
  };

  const performClose = async () => {
    if (!sprint) return;
    setCloseOpen(false);
    setClosing(true);
    try {
      const res = await api.post(
        `${API_URL}/api/projects/sprints/${sprint.sprint_id}/close`,
        {},
      );
      if (!res.ok) {
        toast.error(await extractTaskErrorMessage(res, 'Could not close sprint.'));
        return;
      }
      const data = await res.json();
      toast.success(
        `Sprint closed · velocity ${data?.velocity?.completed_points ?? 0} pts`,
      );
      onSprintsChanged?.();
      onTasksChanged?.();
    } catch {
      toast.error('Could not close sprint.');
    } finally {
      setClosing(false);
    }
  };

  const retroDirty =
    (wentWell ?? '') !== (sprint?.retrospective?.went_well ?? '') ||
    (didnt ?? '') !== (sprint?.retrospective?.didnt_go_well ?? '') ||
    (actionItems ?? '') !== (sprint?.retrospective?.action_items ?? '');

  if (sortedSprints.length === 0) {
    return (
      <section
        data-testid="sprint-review-empty"
        className="glass-card p-8 text-center mt-4"
      >
        <p className="text-sm text-aegisaxiom-text font-medium mb-1">No sprints to review.</p>
        <p className="text-xs text-aegisaxiom-text-muted">
          Run a sprint first — once it closes, the retrospective lives here.
        </p>
      </section>
    );
  }

  return (
    <section data-testid="sprint-review-view" className="flex flex-col h-[calc(100vh-210px)]">
      {/* Header */}
      <header className="px-4 py-3 border-b border-aegisaxiom-border bg-aegisaxiom-surface space-y-3">
        <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-3">
          <label className="text-xs uppercase tracking-wide text-aegisaxiom-text-muted">Review</label>
          <select
            className="aegisaxiom-input h-9 text-sm max-w-[260px]"
            value={sprint?.sprint_id ?? ''}
            onChange={(e) => setSelectedId(e.target.value)}
            data-testid="review-sprint-select"
          >
            {sortedSprints.map((s) => (
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
          {sprint ? <StatusPill status={sprint.status} size="sm" /> : null}
        </div>

        {/* Velocity number — the headline metric on this view. */}
        <div
          className="flex items-baseline gap-2 ml-auto"
          data-testid="review-velocity-block"
        >
          <span className="text-[10px] uppercase tracking-wide text-aegisaxiom-text-muted">
            Velocity
          </span>
          <span
            className="text-[34px] leading-none font-bold text-aegisaxiom-text"
            data-testid="review-velocity-number"
          >
            {velocity}
          </span>
          <span className="text-xs text-aegisaxiom-text-muted">
            pts
            {typeof capacityPoints === 'number' && capacityPoints > 0
              ? ` / ${capacityPoints} cap`
              : ''}
            {completionPct !== null ? ` · ${completionPct}%` : ''}
          </span>
        </div>

        {/* Close-sprint action available on active sprints. */}
        {sprint?.status === 'active' && canCloseSprint ? (
          <button
            type="button"
            onClick={() => setCloseOpen(true)}
            disabled={closing}
            className="btn-secondary h-8 px-3 text-xs inline-flex items-center gap-1.5 disabled:opacity-60"
            data-testid="review-close-sprint"
          >
            <Square size={12} />
            {closing ? 'Closing…' : 'Close sprint'}
          </button>
        ) : null}
        </div>

        {/* Velocity trend — last N closed sprints for this project. The
            current sprint shows highlighted when it's already closed. */}
        <div className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-elevated/30 p-2">
          <div className="text-[10px] uppercase tracking-wide text-aegisaxiom-text-muted px-1 mb-1">
            Velocity trend (last 10 sprints)
          </div>
          <VelocityChart
            projectId={project?.project_id}
            highlightSprintId={sprint?.sprint_id}
            testid="review-velocity-chart"
          />
        </div>
      </header>

      {/* Body */}
      <div className="flex-1 grid grid-cols-1 lg:grid-cols-2 gap-3 p-3 overflow-hidden">
        {/* Completed tasks pane */}
        <div className="flex flex-col rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface overflow-hidden">
          <header className="px-4 py-2 border-b border-aegisaxiom-border flex items-center gap-2">
            <CheckCircle2 size={14} className="text-green-400" />
            <h3 className="text-sm font-semibold text-aegisaxiom-text">Completed</h3>
            <span className="text-xs text-aegisaxiom-text-muted">
              {completedTasks.length} of {sprintTasks.length} tasks
              {incompleteTasks.length > 0
                ? ` · ${incompleteTasks.length} carried`
                : ''}
            </span>
          </header>
          <ul className="flex-1 overflow-y-auto" data-testid="review-completed-list">
            {completedTasks.length === 0 ? (
              <li className="p-6 text-xs text-aegisaxiom-text-muted text-center">
                No tasks completed in this sprint.
              </li>
            ) : (
              completedTasks.map((t) => {
                const tid = t.task_id ?? t._id;
                const assignee = (assignees ?? []).find(
                  (u) => (u.user_id ?? u._id) === t.assignee_id,
                );
                return (
                  <li
                    key={tid}
                    className="h-11 flex items-center gap-3 px-3 border-b border-aegisaxiom-border text-sm"
                    data-testid={`review-completed-row-${tid}`}
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
                  </li>
                );
              })
            )}
          </ul>
          {incompleteTasks.length > 0 ? (
            <details
              className="border-t border-aegisaxiom-border bg-aegisaxiom-elevated/40"
              data-testid="review-incomplete-block"
            >
              <summary className="px-4 py-2 text-xs text-aegisaxiom-text-muted cursor-pointer hover:text-aegisaxiom-text">
                {incompleteTasks.length} incomplete task(s) — click to expand
              </summary>
              <ul className="max-h-[200px] overflow-y-auto">
                {incompleteTasks.map((t) => {
                  const tid = t.task_id ?? t._id;
                  return (
                    <li
                      key={tid}
                      className="h-9 flex items-center gap-3 px-3 border-t border-aegisaxiom-border text-xs text-aegisaxiom-text-muted"
                      data-testid={`review-incomplete-row-${tid}`}
                    >
                      <span className="flex-1 min-w-0 truncate">
                        <span className="font-mono mr-1.5">
                          #{t.task_number ?? '—'}
                        </span>
                        {t.title}
                      </span>
                      <StatusPill status={t.status} size="sm" />
                    </li>
                  );
                })}
              </ul>
            </details>
          ) : null}
        </div>

        {/* Retrospective form pane */}
        <div className="flex flex-col rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface overflow-hidden">
          <header className="px-4 py-2 border-b border-aegisaxiom-border flex items-center gap-2">
            <h3 className="text-sm font-semibold text-aegisaxiom-text">Retrospective</h3>
            {retroDirty ? (
              <span
                className="text-[11px] text-yellow-400"
                data-testid="review-retro-dirty"
              >
                Unsaved changes
              </span>
            ) : null}
          </header>
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            <RetroField
              label="What went well"
              value={wentWell}
              onChange={setWentWell}
              testid="review-retro-went-well"
              disabled={!canEditSprint}
            />
            <RetroField
              label="What didn't go well"
              value={didnt}
              onChange={setDidnt}
              testid="review-retro-didnt"
              disabled={!canEditSprint}
            />
            <RetroField
              label="Action items"
              value={actionItems}
              onChange={setActionItems}
              testid="review-retro-actions"
              disabled={!canEditSprint}
            />
          </div>
          {canEditSprint ? (
            <footer className="px-4 py-3 border-t border-aegisaxiom-border flex justify-end gap-2 bg-aegisaxiom-elevated/40">
              <button
                type="button"
                onClick={handleSaveRetro}
                disabled={savingRetro || !retroDirty || !sprint}
                className="btn-primary h-8 px-3 text-xs disabled:opacity-60"
                data-testid="review-retro-save"
              >
                {savingRetro ? 'Saving…' : 'Save retrospective'}
              </button>
            </footer>
          ) : null}
        </div>
      </div>

      <ConfirmModal
        isOpen={closeOpen}
        title="Close sprint?"
        message={
          incompleteTasks.length > 0
            ? `Close this sprint? ${incompleteTasks.length} task(s) won't be marked done — they'll need to be moved back to the backlog manually.`
            : 'Close this sprint? Velocity will be computed and the retrospective form will open for editing.'
        }
        confirmText="Close sprint"
        cancelText="Cancel"
        variant="danger"
        onConfirm={performClose}
        onCancel={() => setCloseOpen(false)}
      />
    </section>
  );
}

function RetroField({ label, value, onChange, testid, disabled }) {
  return (
    <div>
      <label className="block text-[11px] uppercase tracking-wide text-aegisaxiom-text-muted mb-1">
        {label}
      </label>
      <textarea
        rows={4}
        className="aegisaxiom-input w-full text-sm"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        maxLength={4000}
        data-testid={testid}
      />
    </div>
  );
}
