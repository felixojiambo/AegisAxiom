import React, { useEffect, useMemo, useRef, useState } from 'react';
import { MessageSquare, Paperclip, Plus, Settings, Trash2, X } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { showUndoToast } from '@/components/UndoToast';
import DateInput from '@/components/ui/DateInput';
import StatusPill from './StatusPill';
import UserAvatar from './UserAvatar';
import ConfirmModal from '@/components/shared/ConfirmModal';
import { usePermissions } from '@/hooks/usePermissions';
import TaskCommentsTab from './TaskCommentsTab';
import TaskAttachmentsTab from './TaskAttachmentsTab';

function statusOptionsForProject(project) {
  if (!project) return [];
  if (project.project_type === 'waterfall') {
    return [
      { value: 'not_started', label: 'Not started' },
      { value: 'in_progress', label: 'In progress' },
      { value: 'blocked', label: 'Blocked' },
      { value: 'complete', label: 'Complete' },
    ];
  }
  if (project.project_type === 'agile') {
    return (project.methodology_config?.columns ?? []).map((c) => ({
      value: c.name,
      label: c.name,
    }));
  }
  return [
    { value: 'scoped', label: 'Scoped' },
    { value: 'in_progress', label: 'In progress' },
    { value: 'blocked', label: 'Blocked' },
    { value: 'shipped', label: 'Shipped' },
    { value: 'carried_forward', label: 'Carried forward' },
  ];
}

const TABS = [
  { id: 'details', label: 'Details', icon: Settings },
  { id: 'comments', label: 'Comments', icon: MessageSquare },
  { id: 'attachments', label: 'Files', icon: Paperclip },
];

export default function TaskDetailDrawer({ open, onOpenChange, task, project, allTasks, assignees: assigneesProp, onTaskUpdated, onTaskMutated, onTaskDeleted, onConflict }) {
  const { hasPermission } = usePermissions();
  const canEdit = hasPermission('projects.task.edit');
  const canDelete = hasPermission('projects.task.delete');
  const canChangeStatus = hasPermission('projects.task.change_status');
  const canViewComments = hasPermission('projects.task.comment.view');
  const canViewAttachments = hasPermission('projects.task.attachment.view');

  const [title, setTitle] = useState(task?.title ?? '');
  const [description, setDescription] = useState(task?.description ?? '');
  const [status, setStatus] = useState(task?.status);
  const [priority, setPriority] = useState(task?.priority);
  const [startDate, setStartDate] = useState(task?.start_date ?? '');
  const [dueDate, setDueDate] = useState(task?.due_date ?? '');
  const [assigneeId, setAssigneeId] = useState(task?.assignee_id ?? '');
  const [phase, setPhase] = useState(task?.methodology_fields?.phase ?? '');
  const [savedAt, setSavedAt] = useState(null);
  const [saving, setSaving] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [fetchedAssignees, setFetchedAssignees] = useState([]);
  const [showDepPicker, setShowDepPicker] = useState(false);
  const [activeTab, setActiveTab] = useState('details');

  const taskKey = task?.task_id ?? task?._id;

  // Track the freshest updated_at we've observed for this task — either from
  // the parent passing in a new prop OR from a successful PATCH response.
  // Using a ref (not state) means a sequence of quick edits ('change start',
  // then immediately 'change due') uses the right If-Match without waiting
  // for the parent to re-render with the updated task prop.
  const latestUpdatedAtRef = useRef(task?.updated_at);
  useEffect(() => {
    if (task?.updated_at) latestUpdatedAtRef.current = task.updated_at;
  }, [task?.updated_at]);

  useEffect(() => {
    setTitle(task?.title ?? '');
    setDescription(task?.description ?? '');
    setStatus(task?.status);
    setPriority(task?.priority);
    setStartDate(task?.start_date ?? '');
    setDueDate(task?.due_date ?? '');
    setAssigneeId(task?.assignee_id ?? '');
    setPhase(task?.methodology_fields?.phase ?? '');
    setSavedAt(null);
    setShowDepPicker(false);
    setActiveTab('details');
  }, [taskKey]);

  useEffect(() => { setStatus(task?.status); }, [task?.status]);
  useEffect(() => { setPriority(task?.priority); }, [task?.priority]);
  useEffect(() => { setStartDate(task?.start_date ?? ''); }, [task?.start_date]);
  useEffect(() => { setDueDate(task?.due_date ?? ''); }, [task?.due_date]);
  useEffect(() => { setAssigneeId(task?.assignee_id ?? ''); }, [task?.assignee_id]);
  useEffect(() => { setPhase(task?.methodology_fields?.phase ?? ''); }, [task?.methodology_fields?.phase]);

  // Fetch assignees if parent didn't pass them (so this works from every mount site)
  useEffect(() => {
    if (!open) return;
    if (assigneesProp && assigneesProp.length > 0) return;
    api.get(`${API_URL}/api/projects/assignable-users?role=member`)
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => setFetchedAssignees(Array.isArray(data) ? data : data.items ?? []))
      .catch(() => {});
  }, [open, assigneesProp]);

  const assignees = (assigneesProp && assigneesProp.length > 0) ? assigneesProp : fetchedAssignees;
  const assigneeMap = useMemo(() => {
    const m = new Map();
    (assignees ?? []).forEach((u) => m.set(u.user_id ?? u._id, u));
    return m;
  }, [assignees]);

  if (!open || !task) return null;

  const taskId = task.task_id ?? task._id;

  const patch = async (partial, opts = {}) => {
    setSaving(true);
    try {
      // `ifMatch: null` means force-write (used by undo paths). `??` would
      // coalesce null back to the ref, so an explicit `in` check is needed.
      // Otherwise read from the ref so quick sequential edits ('start',
      // then immediately 'due') don't race the parent's re-render.
      const ifMatchValue = 'ifMatch' in opts ? opts.ifMatch : (latestUpdatedAtRef.current ?? null);
      const res = await api.patchConditional(
        `${API_URL}/api/projects/tasks/${taskId}`,
        partial,
        ifMatchValue,
      );
      const data = await res.json().catch(() => ({}));

      if (res.status === 409) {
        // Conflict: another user edited this task concurrently.
        if (onConflict) {
          onConflict({
            myDoc: { ...task, ...partial },
            theirDoc: data?.current_doc ?? null,
            retryFn: () => patch(partial, { ifMatch: null }),
          });
        } else {
          toast.error('Editing conflict — another user changed this task. Refresh and retry.');
        }
        return null;
      }

      if (!res.ok) {
        const detail = typeof data?.detail === 'string'
          ? data.detail
          : data?.detail?.message ?? 'Could not update task.';
        toast.error(detail);
        return null;
      }
      // Roll our own observation of updated_at forward before notifying
      // the parent — the next sequential edit needs the fresh value
      // immediately, not after React schedules the re-render round-trip.
      if (data?.updated_at) latestUpdatedAtRef.current = data.updated_at;
      setSavedAt(new Date());
      if (onTaskMutated) {
        onTaskMutated(data);
      } else {
        onTaskUpdated?.();
      }
      return data;
    } catch {
      toast.error('Could not update task.');
      return null;
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    const res = await api.delete(`${API_URL}/api/projects/tasks/${taskId}`);
    if (!res.ok) { toast.error('Could not delete task.'); return; }
    toast.success('Task deleted');
    onOpenChange(false);
    if (onTaskDeleted) {
      onTaskDeleted(taskId);
    } else {
      onTaskUpdated?.();
    }
  };

  const addDependency = async (depId) => {
    if (!depId) return;
    try {
      const res = await api.post(`${API_URL}/api/projects/tasks/${taskId}/dependencies`, { depends_on_task_id: depId });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        const detail = typeof data?.detail === 'string'
          ? data.detail
          : data?.detail?.message ?? 'Could not add dependency.';
        toast.error(detail);
        return;
      }
      toast.success('Dependency added');
      setShowDepPicker(false);
      if (data && (data.task_id || data._id)) {
        onTaskMutated?.(data);
      } else {
        onTaskUpdated?.();
      }
    } catch {
      toast.error('Could not add dependency.');
    }
  };

  const removeDependency = async (depId) => {
    try {
      const res = await api.delete(`${API_URL}/api/projects/tasks/${taskId}/dependencies/${depId}`);
      if (!res.ok) {
        toast.error('Could not remove dependency.');
        return;
      }
      const data = await res.json().catch(() => null);
      toast.success('Dependency removed');
      if (data && (data.task_id || data._id)) {
        onTaskMutated?.(data);
      } else {
        onTaskUpdated?.();
      }
    } catch {
      toast.error('Could not remove dependency.');
    }
  };

  const statusOptions = statusOptionsForProject(project);
  const phases = project?.methodology_config?.phases ?? [];
  const mf = task.methodology_fields ?? {};

  // Filter tabs by perm — viewers without comment/attachment perms see fewer.
  const visibleTabs = TABS.filter((t) => {
    if (t.id === 'comments') return canViewComments;
    if (t.id === 'attachments') return canViewAttachments;
    return true;
  });

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/20 z-30"
        onClick={() => onOpenChange(false)}
        aria-hidden="true"
        data-testid="task-drawer-backdrop"
      />

      {/* Drawer */}
      <aside
        className="fixed top-0 right-0 bottom-0 w-[420px] z-40 bg-aegisaxiom-surface border-l border-aegisaxiom-border shadow-lg overflow-y-auto"
        data-testid={`task-drawer-${taskId}`}
        role="dialog"
        aria-label="Task details"
      >
        <header className="px-4 py-3 border-b border-aegisaxiom-border flex items-center gap-2 sticky top-0 bg-aegisaxiom-surface z-10">
          <span className="font-mono text-xs text-aegisaxiom-text-muted">
            #{task.task_number ?? '—'}
          </span>
          <span className="text-sm font-semibold text-aegisaxiom-text">Task details</span>
          {savedAt ? (
            <span className="ml-auto text-[11px] text-aegisaxiom-text-muted" data-testid="drawer-saved">
              Saved · {savedAt.toLocaleTimeString()}
            </span>
          ) : null}
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="ml-auto h-6 w-6 inline-flex items-center justify-center text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
            aria-label="Close drawer"
          >
            <X size={14} />
          </button>
        </header>

        {/* Tab strip — shows 1-3 tabs based on perms; degrades to invisible when only Details */}
        {visibleTabs.length > 1 ? (
          <div
            className="flex items-center gap-1 px-2 pt-2 border-b border-aegisaxiom-border bg-aegisaxiom-surface"
            role="tablist"
            data-testid="drawer-tabs"
          >
            {visibleTabs.map((t) => {
              const Icon = t.icon;
              const active = activeTab === t.id;
              return (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => setActiveTab(t.id)}
                  className={`h-8 px-3 inline-flex items-center gap-1.5 text-xs font-medium rounded-t border-b-2 transition-colors ${
                    active
                      ? 'border-violet-400 text-aegisaxiom-text'
                      : 'border-transparent text-aegisaxiom-text-muted hover:text-aegisaxiom-text'
                  }`}
                  data-testid={`drawer-tab-${t.id}`}
                >
                  <Icon size={12} />
                  {t.label}
                </button>
              );
            })}
          </div>
        ) : null}

        <div className="p-4 space-y-4">
          {activeTab === 'details' ? (
            <>
              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-muted mb-1" htmlFor="drawer-title">
                  Title
                </label>
                <input
                  id="drawer-title"
                  className="aegisaxiom-input w-full disabled:opacity-60"
                  value={title}
                  disabled={!canEdit || saving}
                  onChange={(e) => setTitle(e.target.value)}
                  onBlur={async () => {
                    const trimmed = title.trim();
                    if (trimmed && trimmed !== task.title) {
                      const ok = await patch({ title: trimmed });
                      if (!ok) setTitle(task.title ?? '');
                    }
                  }}
                  data-testid="drawer-title"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-muted mb-1" htmlFor="drawer-description">
                  Description
                </label>
                <textarea
                  id="drawer-description"
                  rows={4}
                  className="aegisaxiom-input w-full resize-y text-sm disabled:opacity-60"
                  value={description ?? ''}
                  disabled={!canEdit || saving}
                  onChange={(e) => setDescription(e.target.value)}
                  onBlur={async () => {
                    if ((description ?? '') !== (task.description ?? '')) {
                      const ok = await patch({ description });
                      if (!ok) setDescription(task.description ?? '');
                    }
                  }}
                  data-testid="drawer-description"
                  placeholder="Add a description…"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-aegisaxiom-text-muted mb-1" htmlFor="drawer-status">
                    Status
                  </label>
                  <select
                    id="drawer-status"
                    value={status ?? ''}
                    disabled={!canChangeStatus || saving}
                    onChange={async (e) => {
                      const next = e.target.value;
                      const prev = status;
                      setStatus(next);
                      const ok = await patch({ status: next });
                      if (!ok) {
                        setStatus(prev);
                      } else {
                        showUndoToast({
                          message: `Status changed to "${next}"`,
                          onUndo: async () => {
                            setStatus(prev);
                            // Force-write on undo: the user explicitly asked
                            // to revert, so skip the If-Match concurrency
                            // check (the closure's task.updated_at is stale
                            // after the original mutation, which would
                            // otherwise trigger a spurious 409).
                            await patch({ status: prev }, { ifMatch: null });
                          },
                        });
                      }
                    }}
                    className="aegisaxiom-input w-full disabled:opacity-60"
                    data-testid="drawer-status"
                  >
                    {statusOptions.map((s) => (
                      <option key={s.value} value={s.value}>{s.label}</option>
                    ))}
                  </select>
                  <div className="mt-1">
                    <StatusPill status={status} size="sm" />
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-medium text-aegisaxiom-text-muted mb-1" htmlFor="drawer-priority">
                    Priority
                  </label>
                  <select
                    id="drawer-priority"
                    value={priority ?? ''}
                    disabled={!canEdit || saving}
                    onChange={async (e) => {
                      const next = e.target.value;
                      const prev = priority;
                      setPriority(next);
                      const ok = await patch({ priority: next });
                      if (!ok) {
                        setPriority(prev);
                      } else {
                        showUndoToast({
                          message: `Priority changed to "${next}"`,
                          onUndo: async () => {
                            setPriority(prev);
                            await patch({ priority: prev }, { ifMatch: null });
                          },
                        });
                      }
                    }}
                    className="aegisaxiom-input w-full disabled:opacity-60"
                    data-testid="drawer-priority"
                  >
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                    <option value="critical">Critical</option>
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-medium text-aegisaxiom-text-muted mb-1" htmlFor="drawer-start-date">
                    Start date
                  </label>
                  <DateInput
                    id="drawer-start-date"
                    value={startDate}
                    disabled={!canEdit || saving}
                    onChange={async (next) => {
                      const prev = startDate;
                      setStartDate(next);
                      const ok = await patch({ start_date: next || null });
                      if (!ok) setStartDate(prev);
                    }}
                    data-testid="drawer-start-date"
                    placeholder="No start date"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-aegisaxiom-text-muted mb-1" htmlFor="drawer-due-date">
                    Due date
                  </label>
                  <DateInput
                    id="drawer-due-date"
                    value={dueDate}
                    disabled={!canEdit || saving}
                    onChange={async (next) => {
                      const prev = dueDate;
                      setDueDate(next);
                      const ok = await patch({ due_date: next || null });
                      if (!ok) setDueDate(prev);
                    }}
                    data-testid="drawer-due-date"
                    placeholder="No due date"
                    min={startDate || undefined}
                  />
                </div>

                <div className="col-span-2">
                  <label className="block text-xs font-medium text-aegisaxiom-text-muted mb-1" htmlFor="drawer-assignee">
                    Assignee
                  </label>
                  <select
                    id="drawer-assignee"
                    value={assigneeId}
                    disabled={!canEdit || saving}
                    onChange={async (e) => {
                      const next = e.target.value;
                      const prev = assigneeId;
                      setAssigneeId(next);
                      const ok = await patch({ assignee_id: next || null });
                      if (!ok) {
                        setAssigneeId(prev);
                      } else {
                        const nextName = next
                          ? (assigneeMap.get(next)?.name ?? assigneeMap.get(next)?.display_name ?? 'someone')
                          : 'Unassigned';
                        showUndoToast({
                          message: `Assigned to ${nextName}`,
                          onUndo: async () => {
                            setAssigneeId(prev);
                            await patch({ assignee_id: prev || null }, { ifMatch: null });
                          },
                        });
                      }
                    }}
                    className="aegisaxiom-input w-full disabled:opacity-60"
                    data-testid="drawer-assignee"
                  >
                    <option value="">— Unassigned —</option>
                    {(assignees ?? [])
                      .slice()
                      .sort((a, b) => (a.name ?? a.display_name ?? '').localeCompare(b.name ?? b.display_name ?? ''))
                      .map((u) => (
                        <option key={u.user_id ?? u._id} value={u.user_id ?? u._id}>
                          {u.name ?? u.display_name}
                        </option>
                      ))}
                  </select>
                  {assigneeId && assigneeMap.get(assigneeId) ? (
                    <div className="mt-1 inline-flex items-center gap-1.5 text-[11px] text-aegisaxiom-text-muted">
                      <UserAvatar user={assigneeMap.get(assigneeId)} size="sm" />
                      <span>{assigneeMap.get(assigneeId).name ?? assigneeMap.get(assigneeId).display_name}</span>
                    </div>
                  ) : null}
                </div>

                {project?.project_type === 'waterfall' && phases.length > 0 ? (
                  <div className="col-span-2">
                    <label className="block text-xs font-medium text-aegisaxiom-text-muted mb-1" htmlFor="drawer-phase">
                      Phase
                    </label>
                    <select
                      id="drawer-phase"
                      value={phase}
                      disabled={!canEdit || saving}
                      onChange={async (e) => {
                        const next = e.target.value;
                        const prev = phase;
                        setPhase(next);
                        const ok = await patch({ methodology_fields: { ...mf, phase: next } });
                        if (!ok) setPhase(prev);
                      }}
                      className="aegisaxiom-input w-full disabled:opacity-60"
                      data-testid="drawer-phase"
                    >
                      {phases.map((p) => {
                        // phases may be List[str] (legacy) or List[Dict[name,...]] (canonical)
                        const name = typeof p === 'string' ? p : p?.name;
                        if (!name) return null;
                        return <option key={name} value={name}>{name}</option>;
                      })}
                    </select>
                  </div>
                ) : null}
              </div>

              <div data-testid="drawer-dependencies">
                <div className="flex items-center justify-between mb-1">
                  <p className="text-xs font-medium text-aegisaxiom-text-muted">Dependencies</p>
                  {canEdit ? (
                    <button
                      type="button"
                      onClick={() => setShowDepPicker((v) => !v)}
                      className="h-6 px-2 inline-flex items-center gap-1 text-[11px] text-aegisaxiom-text-muted hover:text-aegisaxiom-text border border-aegisaxiom-border rounded"
                      data-testid="drawer-dep-add-toggle"
                    >
                      <Plus size={11} /> Add
                    </button>
                  ) : null}
                </div>
                {(task.dependencies ?? []).length === 0 ? (
                  <p className="text-[11px] text-aegisaxiom-text-muted italic">No dependencies.</p>
                ) : (
                  <ul className="space-y-1 text-xs">
                    {task.dependencies.map((depId) => {
                      const dep = (allTasks ?? []).find(
                        (t) => (t.task_id ?? t._id) === depId,
                      );
                      return (
                        <li
                          key={depId}
                          className="flex items-center gap-2 px-2 py-1 rounded bg-aegisaxiom-elevated"
                          data-testid={`drawer-dep-row-${depId}`}
                        >
                          <span className="font-mono text-aegisaxiom-text-muted">
                            #{dep?.task_number ?? '—'}
                          </span>
                          <span className="truncate text-aegisaxiom-text flex-1">
                            {dep?.title ?? '(deleted)'}
                          </span>
                          {canEdit ? (
                            <button
                              type="button"
                              onClick={() => removeDependency(depId)}
                              className="h-5 w-5 inline-flex items-center justify-center text-aegisaxiom-text-muted hover:text-red-400"
                              aria-label={`Remove dependency #${dep?.task_number ?? ''}`}
                              data-testid={`drawer-dep-remove-${depId}`}
                            >
                              <Trash2 size={10} />
                            </button>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                )}
                {showDepPicker && canEdit ? (
                  <div className="mt-2">
                    <select
                      defaultValue=""
                      onChange={(e) => addDependency(e.target.value)}
                      className="aegisaxiom-input w-full text-xs"
                      data-testid="drawer-dep-picker"
                    >
                      <option value="">— Pick a task to depend on —</option>
                      {(allTasks ?? [])
                        .filter((t) => (t.task_id ?? t._id) !== taskId)
                        .filter((t) => !(task.dependencies ?? []).includes(t.task_id ?? t._id))
                        .map((t) => (
                          <option key={t.task_id ?? t._id} value={t.task_id ?? t._id}>
                            #{t.task_number ?? '—'} · {t.title}
                          </option>
                        ))}
                    </select>
                  </div>
                ) : null}
              </div>

              {canDelete ? (
                <div className="pt-2 border-t border-aegisaxiom-border">
                  <button
                    type="button"
                    onClick={() => setDeleteConfirm(true)}
                    className="h-8 px-3 rounded-md border border-aegisaxiom-border text-xs text-red-400 hover:bg-red-500/10 transition-colors"
                    data-testid="drawer-delete"
                  >
                    Delete task
                  </button>
                </div>
              ) : null}
            </>
          ) : null}

          {activeTab === 'comments' && canViewComments ? (
            <TaskCommentsTab
              task={task}
              assignees={assignees}
              onChanged={onTaskUpdated}
            />
          ) : null}

          {activeTab === 'attachments' && canViewAttachments ? (
            <TaskAttachmentsTab
              task={task}
              assignees={assignees}
              onChanged={onTaskUpdated}
            />
          ) : null}
        </div>
      </aside>

      <ConfirmModal
        isOpen={deleteConfirm}
        title="Delete task"
        message={`Delete "${task.title}"? This cannot be undone.`}
        confirmText="Delete"
        variant="danger"
        onConfirm={handleDelete}
        onCancel={() => setDeleteConfirm(false)}
      />
    </>
  );
}
