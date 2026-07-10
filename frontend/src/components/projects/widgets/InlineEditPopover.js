/**
 * InlineEditPopover — shared popover shell for inline task-field edits.
 *
 * Wraps @radix-ui/react-popover with keyboard handling (ESC closes,
 * Enter commits highlighted option) and single-instance enforcement
 * via a global atom exposed through the openPopoverId context.
 *
 * Sub-components exported:
 *   InlineStatusPicker   — status enum list
 *   InlinePriorityPicker — priority enum list
 *   InlineAssigneePicker — assignee user list
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';
import * as Popover from '@radix-ui/react-popover';
import { Check, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';
import { showUndoToast } from '@/components/UndoToast';

// ---------------------------------------------------------------------------
// Single-instance context — only one popover can be open at a time.
// BoardView / BacklogView provide this so all cards in the view share it.
// ---------------------------------------------------------------------------

export const OpenPopoverCtx = createContext({ openId: null, setOpenId: () => {} });

export function OpenPopoverProvider({ children }) {
  const [openId, setOpenId] = useState(null);
  return (
    <OpenPopoverCtx.Provider value={{ openId, setOpenId }}>
      {children}
    </OpenPopoverCtx.Provider>
  );
}

// ---------------------------------------------------------------------------
// Generic popover shell
// ---------------------------------------------------------------------------

function InlinePopoverShell({ id, trigger, children, align = 'start', disabled }) {
  const { openId, setOpenId } = useContext(OpenPopoverCtx);
  const open = openId === id;

  const handleOpenChange = useCallback(
    (next) => {
      setOpenId(next ? id : null);
    },
    [id, setOpenId],
  );

  return (
    <Popover.Root open={open} onOpenChange={handleOpenChange}>
      <Popover.Trigger asChild disabled={disabled}>
        {trigger}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align={align}
          sideOffset={4}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.stopPropagation();
              setOpenId(null);
            }
          }}
          className="z-50 min-w-[160px] rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface shadow-xl animate-in fade-in-0 zoom-in-95 duration-100"
          data-testid={`inline-popover-${id}`}
        >
          {children}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

// ---------------------------------------------------------------------------
// Shared option row inside a popover
// ---------------------------------------------------------------------------

function Option({ label, active, onSelect, colorDot }) {
  const ref = useRef(null);
  return (
    <button
      ref={ref}
      type="button"
      role="option"
      aria-selected={active}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === 'Enter') { e.preventDefault(); onSelect(); }
      }}
      className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-aegisaxiom-text hover:bg-aegisaxiom-elevated rounded-md transition-colors"
      data-testid={`inline-option-${label.toLowerCase().replace(/\s+/g, '-')}`}
    >
      {colorDot && (
        <span className={`w-2 h-2 rounded-full shrink-0 ${colorDot}`} aria-hidden="true" />
      )}
      <span className="flex-1 text-left">{label}</span>
      {active && <Check size={11} className="text-violet-400 shrink-0" aria-hidden="true" />}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Shared patch helper — mirrors TaskDetailDrawer.js:112-152 exactly.
//
// onConflict shape (when provided): { myDoc, theirDoc, retryFn }
// Matches TaskDetailDrawer so ConflictDialog receives the same props.
// ---------------------------------------------------------------------------

async function patchTask({ task, taskId, partial, setSaving, onTaskMutated, onConflict, forceWrite = false }) {
  setSaving(true);
  try {
    // forceWrite skips the If-Match header — used by undo paths where the
    // user has explicitly asked to revert and a concurrency check is the
    // wrong semantics (also avoids stale-closure 409s).
    const ifMatchValue = forceWrite ? null : (task?.updated_at ?? null);
    const res = await api.patchConditional(
      `${API_URL}/api/projects/tasks/${taskId}`,
      partial,
      ifMatchValue,
    );
    const data = await res.json().catch(() => ({}));

    if (res.status === 409) {
      if (onConflict) {
        onConflict({
          myDoc: { ...task, ...partial },
          theirDoc: data?.current_doc ?? null,
          retryFn: () => patchTask({ task, taskId, partial, setSaving, onTaskMutated, onConflict: null, forceWrite: true }),
        });
      } else {
        toast.error('Another user changed this task. Refresh to see latest.');
      }
      return null;
    }

    if (!res.ok) {
      const detail =
        typeof data?.detail === 'string'
          ? data.detail
          : data?.detail?.message ?? 'Could not update task.';
      toast.error(detail);
      return null;
    }
    if (onTaskMutated) onTaskMutated(data);
    return data;
  } catch {
    toast.error('Could not update task.');
    return null;
  } finally {
    setSaving(false);
  }
}

// ---------------------------------------------------------------------------
// Status config — methodology-aware. Callers pass `project` so the picker
// shows the valid status set for that project's methodology, not a
// generic global list. Falls back to the global list when no project is
// supplied (legacy callers).
// ---------------------------------------------------------------------------

const STATUS_OPTIONS_GLOBAL = [
  { value: 'not_started', label: 'Not Started', dot: 'bg-aegisaxiom-text-muted' },
  { value: 'open', label: 'Open', dot: 'bg-aegisaxiom-text-muted' },
  { value: 'planning', label: 'Planning', dot: 'bg-aegisaxiom-text-muted' },
  { value: 'in_progress', label: 'In Progress', dot: 'bg-blue-400' },
  { value: 'active', label: 'Active', dot: 'bg-blue-400' },
  { value: 'blocked', label: 'Blocked', dot: 'bg-red-400' },
  { value: 'on_hold', label: 'On Hold', dot: 'bg-yellow-400' },
  { value: 'complete', label: 'Complete', dot: 'bg-green-400' },
  { value: 'closed', label: 'Closed', dot: 'bg-green-400' },
];

const STATUS_OPTIONS_WATERFALL = [
  { value: 'not_started', label: 'Not Started', dot: 'bg-aegisaxiom-text-muted' },
  { value: 'in_progress', label: 'In Progress', dot: 'bg-blue-400' },
  { value: 'blocked', label: 'Blocked', dot: 'bg-red-400' },
  { value: 'complete', label: 'Complete', dot: 'bg-green-400' },
];

const STATUS_OPTIONS_WAVES_THREADS = [
  { value: 'scoped', label: 'Scoped', dot: 'bg-aegisaxiom-text-muted' },
  { value: 'in_progress', label: 'In Progress', dot: 'bg-blue-400' },
  { value: 'blocked', label: 'Blocked', dot: 'bg-red-400' },
  { value: 'shipped', label: 'Shipped', dot: 'bg-green-400' },
  { value: 'carried_forward', label: 'Carried Forward', dot: 'bg-yellow-400' },
];

function statusOptionsForProject(project) {
  if (!project) return STATUS_OPTIONS_GLOBAL;
  const ptype = project.project_type;
  if (ptype === 'waterfall') return STATUS_OPTIONS_WATERFALL;
  if (ptype === 'waves_threads') return STATUS_OPTIONS_WAVES_THREADS;
  if (ptype === 'agile') {
    const cols = project.methodology_config?.columns ?? [];
    return cols.map((c) => ({
      value: c.name,
      label: c.name,
      dot: c.is_done ? 'bg-green-400' : 'bg-aegisaxiom-text-muted',
    }));
  }
  return STATUS_OPTIONS_GLOBAL;
}

// ---------------------------------------------------------------------------
// InlineStatusPicker
// ---------------------------------------------------------------------------

export function InlineStatusPicker({ task, project, onTaskMutated, onConflict, children }) {
  const { hasPermission } = usePermissions();
  const canChangeStatus = hasPermission('projects.task.change_status');
  const taskId = task.task_id ?? task._id;
  const id = `status-${taskId}`;
  const { setOpenId } = useContext(OpenPopoverCtx);

  const [saving, setSaving] = useState(false);
  const [localStatus, setLocalStatus] = useState(task.status);
  const statusOptions = statusOptionsForProject(project);

  // Keep local status in sync when parent refreshes the task doc.
  useEffect(() => { setLocalStatus(task.status); }, [task.status]);

  const handleSelect = useCallback(
    async (value) => {
      if (value === localStatus) { setOpenId(null); return; }
      const prev = localStatus;
      setLocalStatus(value);         // optimistic
      setOpenId(null);
      const ok = await patchTask({
        task,
        taskId,
        partial: { status: value },
        setSaving,
        onTaskMutated,
        onConflict: onConflict
          ? (conflict) => { setLocalStatus(prev); onConflict(conflict); }
          : null,
      });
      if (!ok) {
        setLocalStatus(prev);        // revert on non-409 failure
      } else {
        showUndoToast({
          message: `Status changed to "${value}"`,
          onUndo: async () => {
            setLocalStatus(prev);
            await patchTask({ task, taskId, partial: { status: prev }, setSaving, onTaskMutated, onConflict: null, forceWrite: true });
          },
        });
      }
    },
    [localStatus, task, taskId, onTaskMutated, onConflict, setOpenId],
  );

  if (!canChangeStatus) return children ?? null;

  return (
    <InlinePopoverShell
      id={id}
      disabled={saving}
      trigger={
        <span
          onClick={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
          className={`inline-flex cursor-pointer ${saving ? 'opacity-50 pointer-events-none' : ''}`}
          aria-label={`Change status: ${localStatus}`}
          data-testid={`inline-status-trigger-${taskId}`}
        >
          {children}
        </span>
      }
    >
      <div className="p-1" role="listbox" aria-label="Select status">
        {statusOptions.map((opt) => (
          <Option
            key={opt.value}
            label={opt.label}
            active={localStatus === opt.value}
            colorDot={opt.dot}
            onSelect={() => handleSelect(opt.value)}
          />
        ))}
      </div>
      {saving && (
        <div className="flex items-center justify-center py-1">
          <Loader2 size={12} className="animate-spin text-aegisaxiom-text-muted" />
        </div>
      )}
    </InlinePopoverShell>
  );
}

// ---------------------------------------------------------------------------
// Priority config
// ---------------------------------------------------------------------------

const PRIORITY_OPTIONS = [
  { value: 'critical', label: 'Critical', dot: 'bg-red-400' },
  { value: 'high', label: 'High', dot: 'bg-orange-400' },
  { value: 'medium', label: 'Medium', dot: 'bg-yellow-400' },
  { value: 'low', label: 'Low', dot: 'bg-aegisaxiom-text-muted' },
];

// ---------------------------------------------------------------------------
// InlinePriorityPicker
// ---------------------------------------------------------------------------

export function InlinePriorityPicker({ task, onTaskMutated, onConflict, children }) {
  const { hasPermission } = usePermissions();
  const canEdit = hasPermission('projects.task.edit');
  const taskId = task.task_id ?? task._id;
  const id = `priority-${taskId}`;
  const { setOpenId } = useContext(OpenPopoverCtx);

  const [saving, setSaving] = useState(false);
  const [localPriority, setLocalPriority] = useState(task.priority);

  useEffect(() => { setLocalPriority(task.priority); }, [task.priority]);

  const handleSelect = useCallback(
    async (value) => {
      if (value === localPriority) { setOpenId(null); return; }
      const prev = localPriority;
      setLocalPriority(value);
      setOpenId(null);
      const ok = await patchTask({
        task,
        taskId,
        partial: { priority: value },
        setSaving,
        onTaskMutated,
        onConflict: onConflict
          ? (conflict) => { setLocalPriority(prev); onConflict(conflict); }
          : null,
      });
      if (!ok) {
        setLocalPriority(prev);
      } else {
        showUndoToast({
          message: `Priority changed to "${value}"`,
          onUndo: async () => {
            setLocalPriority(prev);
            await patchTask({ task, taskId, partial: { priority: prev }, setSaving, onTaskMutated, onConflict: null, forceWrite: true });
          },
        });
      }
    },
    [localPriority, task, taskId, onTaskMutated, onConflict, setOpenId],
  );

  if (!canEdit) return children ?? null;

  return (
    <InlinePopoverShell
      id={id}
      disabled={saving}
      trigger={
        <span
          onClick={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
          className={`inline-flex cursor-pointer ${saving ? 'opacity-50 pointer-events-none' : ''}`}
          aria-label={`Change priority: ${localPriority ?? 'none'}`}
          data-testid={`inline-priority-trigger-${taskId}`}
        >
          {children}
        </span>
      }
    >
      <div className="p-1" role="listbox" aria-label="Select priority">
        {PRIORITY_OPTIONS.map((opt) => (
          <Option
            key={opt.value}
            label={opt.label}
            active={localPriority === opt.value}
            colorDot={opt.dot}
            onSelect={() => handleSelect(opt.value)}
          />
        ))}
      </div>
    </InlinePopoverShell>
  );
}

// ---------------------------------------------------------------------------
// InlineAssigneePicker
// ---------------------------------------------------------------------------

export function InlineAssigneePicker({ task, assignees, onTaskMutated, onConflict, children }) {
  const { hasPermission } = usePermissions();
  const canEdit = hasPermission('projects.task.edit');
  const taskId = task.task_id ?? task._id;
  const id = `assignee-${taskId}`;
  const { setOpenId } = useContext(OpenPopoverCtx);

  const [saving, setSaving] = useState(false);
  const [localAssigneeId, setLocalAssigneeId] = useState(task.assignee_id ?? null);

  useEffect(() => { setLocalAssigneeId(task.assignee_id ?? null); }, [task.assignee_id]);

  const handleSelect = useCallback(
    async (userId) => {
      if (userId === localAssigneeId) { setOpenId(null); return; }
      const prev = localAssigneeId;
      setLocalAssigneeId(userId);
      setOpenId(null);
      const ok = await patchTask({
        task,
        taskId,
        partial: { assignee_id: userId },
        setSaving,
        onTaskMutated,
        onConflict: onConflict
          ? (conflict) => { setLocalAssigneeId(prev); onConflict(conflict); }
          : null,
      });
      if (!ok) {
        setLocalAssigneeId(prev);
      } else {
        const nextUser = (assignees ?? []).find((u) => (u.user_id ?? u._id) === userId);
        const nextName = userId
          ? (nextUser?.display_name ?? nextUser?.name ?? 'someone')
          : 'Unassigned';
        showUndoToast({
          message: `Assigned to ${nextName}`,
          onUndo: async () => {
            setLocalAssigneeId(prev);
            await patchTask({ task, taskId, partial: { assignee_id: prev }, setSaving, onTaskMutated, onConflict: null, forceWrite: true });
          },
        });
      }
    },
    [localAssigneeId, task, taskId, onTaskMutated, onConflict, setOpenId, assignees],
  );

  if (!canEdit) return children ?? null;

  const list = assignees ?? [];

  return (
    <InlinePopoverShell
      id={id}
      disabled={saving}
      trigger={
        <span
          onClick={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
          className={`inline-flex cursor-pointer ${saving ? 'opacity-50 pointer-events-none' : ''}`}
          aria-label="Change assignee"
          data-testid={`inline-assignee-trigger-${taskId}`}
        >
          {children}
        </span>
      }
    >
      <div className="p-1 max-h-[220px] overflow-y-auto" role="listbox" aria-label="Select assignee">
        <Option
          label="Unassigned"
          active={localAssigneeId === null}
          onSelect={() => handleSelect(null)}
        />
        {list.map((u) => {
          const uid = u.user_id ?? u._id;
          return (
            <Option
              key={uid}
              label={u.display_name ?? u.name ?? uid}
              active={localAssigneeId === uid}
              onSelect={() => handleSelect(uid)}
            />
          );
        })}
      </div>
    </InlinePopoverShell>
  );
}
