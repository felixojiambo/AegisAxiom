import React from 'react';
import StatusPill from './StatusPill';
import PriorityFlag from './PriorityFlag';
import UserAvatar from './UserAvatar';

export default function TaskListView({ tasks, assignees, onOpen }) {
  const resolveAssignee = (assigneeId) =>
    (assignees ?? []).find((u) => (u.user_id ?? u._id) === assigneeId);

  if (!tasks || tasks.length === 0) {
    return (
      <p className="text-sm text-aegisaxiom-text-muted py-6 text-center" data-testid="task-list-empty">
        No tasks here.
      </p>
    );
  }

  return (
    <ul
      className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface overflow-hidden"
      data-testid="task-list"
    >
      {tasks.map((t) => {
        const assignee = resolveAssignee(t.assignee_id);
        return (
          <li
            key={t.task_id ?? t._id}
            className="h-10 flex items-center gap-3 px-3 border-b last:border-b-0 border-aegisaxiom-border text-sm hover:bg-aegisaxiom-elevated/50"
            data-testid={`task-list-row-${t.task_id ?? t._id}`}
          >
            <PriorityFlag priority={t.priority} />
            <button
              type="button"
              onClick={() => onOpen?.(t)}
              className="flex-1 text-left truncate font-medium text-aegisaxiom-text hover:text-aegisaxiom-text-muted"
            >
              {t.task_number != null ? (
                <span className="text-[10px] text-aegisaxiom-text-muted font-mono mr-1.5">
                  #{t.task_number}
                </span>
              ) : null}
              {t.title}
            </button>
            <StatusPill status={t.status} size="sm" />
            <UserAvatar user={assignee ?? null} size="sm" />
          </li>
        );
      })}
    </ul>
  );
}
