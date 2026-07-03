import React from 'react';
import StatusPill from './StatusPill';
import PriorityFlag from './PriorityFlag';
import UserAvatar from './UserAvatar';
import {
  InlineStatusPicker,
  InlinePriorityPicker,
  InlineAssigneePicker,
} from './InlineEditPopover';

/**
 * TaskCard
 *
 * Props:
 *   task            — task document
 *   onClick         — (task) => void  — open the detail drawer
 *   compact         — boolean
 *   assignee        — resolved user object for task.assignee_id
 *   assignees       — full assignee array (needed for the inline picker list)
 *   dragHandleProps — spread onto root div for DnD
 *   onTaskMutated   — (updatedDoc) => void — threaded down from ProjectDetailPage
 *   onConflict      — ({ myDoc, theirDoc, retryFn }) => void — show ConflictDialog on 409
 *   selectMode      — boolean — whether bulk-select mode is active
 *   selected        — boolean — whether this card is selected
 *   onSelectToggle  — (taskId) => void — called when checkbox is toggled
 */
export default function TaskCard({
  task,
  project,
  onClick,
  compact,
  assignee,
  assignees,
  dragHandleProps,
  onTaskMutated,
  onConflict,
  selectMode,
  selected,
  onSelectToggle,
}) {
  const ms = task.methodology_fields?.milestone;
  const taskId = task.task_id ?? task._id;

  const stopProp = (e) => e.stopPropagation();

  return (
    <div
      data-testid={`task-card-${taskId}`}
      onClick={() => !selectMode && onClick?.(task)}
      className={`group glass-card p-2.5 text-sm hover:shadow-md transition-shadow duration-100 ${
        selectMode ? 'cursor-default' : 'cursor-pointer'
      } ${selected ? 'ring-2 ring-violet-500/60' : ''}`}
      {...dragHandleProps}
    >
      <div className="flex items-center gap-1.5 mb-1 text-[11px] text-aegisaxiom-text-muted">
        {/* Bulk-select checkbox */}
        {selectMode && (
          <input
            type="checkbox"
            checked={!!selected}
            onChange={(e) => { e.stopPropagation(); onSelectToggle?.(taskId); }}
            onClick={stopProp}
            className="shrink-0 accent-violet-500"
            aria-label={`Select task ${task.task_number ?? taskId}`}
            data-testid={`task-card-checkbox-${taskId}`}
          />
        )}

        {/* Priority — inline editable */}
        <InlinePriorityPicker task={task} onTaskMutated={onTaskMutated} onConflict={onConflict}>
          <PriorityFlag priority={task.priority} />
        </InlinePriorityPicker>

        {task.task_number != null ? (
          <span className="font-mono">#{task.task_number}</span>
        ) : null}

        {ms ? (
          <span className="ml-auto inline-flex items-center h-4 px-1.5 rounded bg-blue-500/15 text-blue-400 text-[9px] font-semibold">
            MILESTONE
          </span>
        ) : null}
      </div>

      <p className="font-semibold text-aegisaxiom-text line-clamp-2 mb-2">{task.title}</p>

      <div className="flex items-center justify-between gap-2">
        {/* Status — inline editable */}
        <InlineStatusPicker task={task} project={project} onTaskMutated={onTaskMutated} onConflict={onConflict}>
          <StatusPill status={task.status} size="sm" />
        </InlineStatusPicker>

        <div className="flex items-center gap-2 shrink-0">
          {task.due_date ? (
            <span className="text-[11px] text-aegisaxiom-text-muted">{task.due_date}</span>
          ) : null}

          {/* Assignee avatar — inline editable */}
          <InlineAssigneePicker task={task} assignees={assignees} onTaskMutated={onTaskMutated} onConflict={onConflict}>
            <UserAvatar user={assignee ?? null} size="sm" />
          </InlineAssigneePicker>
        </div>
      </div>
    </div>
  );
}
