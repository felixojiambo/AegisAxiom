/**
 * BulkActionBar — floating action bar for bulk task operations.
 *
 * Appears at the bottom of the view when ≥1 task is selected.
 *
 * Actions: Change status, Assign to, Move to sprint, Delete.
 *
 * Uses Wave 1C bulk endpoints that return:
 *   { updated: [...full docs], failed: [{task_id, error, code}], summary: {…} }
 *
 * Each updated doc is fed back through onTaskMutated (one call per doc).
 * Failed entries produce a toast with human-readable reasons.
 * Confirmation is required for Delete and for any action on >10 tasks.
 */

import React, { useCallback, useState } from 'react';
import { X, ChevronDown, Loader2, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import ConfirmModal from '../../shared/ConfirmModal';
import { showUndoToast } from '@/components/UndoToast';

// ---------------------------------------------------------------------------
// Error code → human label mapping (Wave 1C contract)
// ---------------------------------------------------------------------------

const ERROR_LABELS = {
  not_found: 'not found',
  forbidden: 'permission denied',
  project_frozen: 'project is frozen',
  invalid_status: 'invalid status',
  invalid_assignee: 'invalid assignee',
  invalid_sprint: 'invalid sprint',
  validation: 'validation error',
  unexpected: 'unexpected error',
};

function describeFailures(failed) {
  if (!failed?.length) return '';
  const reasons = [...new Set(failed.map((f) => ERROR_LABELS[f.code] ?? f.error ?? f.code))];
  return reasons.join(', ');
}

// ---------------------------------------------------------------------------
// Status options (subset — the most common transitions from a bulk bar)
// ---------------------------------------------------------------------------

const STATUS_OPTIONS_GLOBAL = [
  { value: 'not_started', label: 'Not Started' },
  { value: 'in_progress', label: 'In Progress' },
  { value: 'blocked', label: 'Blocked' },
  { value: 'on_hold', label: 'On Hold' },
  { value: 'complete', label: 'Complete' },
  { value: 'closed', label: 'Closed' },
];

const STATUS_OPTIONS_WATERFALL = [
  { value: 'not_started', label: 'Not Started' },
  { value: 'in_progress', label: 'In Progress' },
  { value: 'blocked', label: 'Blocked' },
  { value: 'complete', label: 'Complete' },
];

const STATUS_OPTIONS_WAVES_THREADS = [
  { value: 'scoped', label: 'Scoped' },
  { value: 'in_progress', label: 'In Progress' },
  { value: 'blocked', label: 'Blocked' },
  { value: 'shipped', label: 'Shipped' },
  { value: 'carried_forward', label: 'Carried Forward' },
];

function statusOptionsForProject(project) {
  if (!project) return STATUS_OPTIONS_GLOBAL;
  const ptype = project.project_type;
  if (ptype === 'waterfall') return STATUS_OPTIONS_WATERFALL;
  if (ptype === 'waves_threads') return STATUS_OPTIONS_WAVES_THREADS;
  if (ptype === 'agile') {
    const cols = project.methodology_config?.columns ?? [];
    return cols.map((c) => ({ value: c.name, label: c.name }));
  }
  return STATUS_OPTIONS_GLOBAL;
}

const PRIORITY_OPTIONS = [
  { value: 'critical', label: 'Critical' },
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
];

// ---------------------------------------------------------------------------
// A minimal inline dropdown for the bar (no Radix — keeps z-index simple)
// ---------------------------------------------------------------------------

function BarDropdown({ label, options, onSelect, disabled }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        className="inline-flex items-center gap-1.5 h-8 px-3 rounded-md text-xs font-medium bg-aegisaxiom-elevated border border-aegisaxiom-border text-aegisaxiom-text hover:bg-aegisaxiom-surface transition-colors disabled:opacity-50"
        data-testid={`bulk-bar-dropdown-${label.toLowerCase().replace(/\s+/g, '-')}`}
      >
        {label}
        <ChevronDown size={11} />
      </button>
      {open && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setOpen(false)}
            aria-hidden="true"
          />
          <div className="absolute bottom-full mb-1 left-0 z-50 min-w-[160px] rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface shadow-xl">
            <div className="p-1">
              {options.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => { setOpen(false); onSelect(opt.value); }}
                  className="w-full text-left px-3 py-1.5 text-xs text-aegisaxiom-text hover:bg-aegisaxiom-elevated rounded-md transition-colors"
                  data-testid={`bulk-bar-option-${opt.value}`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// BulkActionBar
// ---------------------------------------------------------------------------

export default function BulkActionBar({
  selectedIds,          // Set<string>
  onClear,              // () => void
  onTaskMutated,        // (updatedDoc) => void
  onTaskDeleted,        // (taskId) => void
  assignees,            // user[]
  sprints,              // sprint[] | null — omit/null to hide Move to sprint
  canDelete,            // boolean
  tasks,                // task[] — current task docs; used for undo snapshots
  project,              // project doc — picks methodology-aware status options
}) {
  const count = selectedIds?.size ?? 0;
  const [saving, setSaving] = useState(false);
  const statusOptions = statusOptionsForProject(project);

  // Confirm modal state.
  // We wrap `exec` as `{ fn }` to avoid React treating a function value
  // as a state-update callback inside setState.
  const [confirm, setConfirm] = useState({ open: false, title: '', message: '', fn: null, isDelete: false });
  const closeConfirm = () => setConfirm((c) => ({ ...c, open: false }));

  // kind: 'delete' | 'bulk' — determines whether confirmation is required.
  const requiresConfirm = (kind, taskCount) =>
    kind === 'delete' || taskCount > 10;

  const maybeConfirm = useCallback(
    ({ title, message, kind = 'bulk', exec }) => {
      if (requiresConfirm(kind, count)) {
        setConfirm({ open: true, title, message, fn: exec, isDelete: kind === 'delete' });
      } else {
        exec();
      }
    },
    [count],
  );

  // ---------------------------------------------------------------------------
  // Shared response handler for bulk endpoints
  // ---------------------------------------------------------------------------

  // undoConfig: { message, onUndo } — optional; only shown when all tasks succeed.
  const handleBulkResponse = useCallback(
    async (res, label, undoConfig) => {
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail =
          typeof data?.detail === 'string'
            ? data.detail
            : data?.detail?.message ?? `Could not ${label}.`;
        toast.error(detail);
        return;
      }
      const updated = data.updated ?? [];
      const failed = data.failed ?? [];
      updated.forEach((doc) => onTaskMutated?.(doc));
      if (failed.length === 0) {
        if (undoConfig) {
          showUndoToast(undoConfig);
        } else {
          toast.success(`Updated ${updated.length} task${updated.length === 1 ? '' : 's'}`);
        }
        onClear();
      } else {
        const reasons = describeFailures(failed);
        toast.warning(
          `Updated ${updated.length} task${updated.length === 1 ? '' : 's'}; ${failed.length} failed: ${reasons}`,
        );
        onClear();
      }
    },
    [onTaskMutated, onClear],
  );

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  const bulkStatus = useCallback(
    (status) => {
      // Snapshot prev statuses for undo before the request fires.
      const ids = Array.from(selectedIds);
      const prevStatuses = ids.reduce((acc, id) => {
        const t = (tasks ?? []).find((d) => (d.task_id ?? d._id) === id);
        if (t) acc[id] = t.status;
        return acc;
      }, {});
      // Display-friendly label for toast/confirm copy — fall back to the raw
      // slug if the option list does not include it (e.g. agile columns are
      // already display strings).
      const statusLabel = statusOptions.find((o) => o.value === status)?.label ?? status;

      const exec = async () => {
        setSaving(true);
        try {
          const res = await api.patch(`${API_URL}/api/projects/tasks/bulk-status`, {
            task_ids: ids,
            status,
          });
          await handleBulkResponse(res, 'change status', {
            message: `Changed status to "${statusLabel}" for ${count} task${count === 1 ? '' : 's'}`,
            onUndo: async () => {
              // Re-patch each task back to its previous status AND feed the
              // updated doc back through onTaskMutated so cards refresh in
              // place — without this, cards stay in the new column visually
              // until the user reloads.
              const results = await Promise.allSettled(
                Object.entries(prevStatuses).map(([id, prev]) =>
                  api.patch(`${API_URL}/api/projects/tasks/${id}`, { status: prev }),
                ),
              );
              for (const r of results) {
                if (r.status === 'fulfilled' && r.value?.ok) {
                  try { onTaskMutated?.(await r.value.json()); } catch { /* swallow */ }
                }
              }
            },
          });
        } catch {
          toast.error('Could not change status.');
        } finally {
          setSaving(false);
        }
      };
      maybeConfirm({
        title: `Change status for ${count} task${count === 1 ? '' : 's'}?`,
        message: `Set status to "${statusLabel}" for all selected tasks.`,
        exec,
      });
    },
    [selectedIds, count, maybeConfirm, handleBulkResponse, tasks, statusOptions, onTaskMutated],
  );

  const bulkAssign = useCallback(
    (userId) => {
      const userName =
        userId === null
          ? 'Unassigned'
          : (assignees ?? []).find((u) => (u.user_id ?? u._id) === userId)?.display_name ?? userId;

      // Snapshot prev assignees for undo.
      const ids = Array.from(selectedIds);
      const prevAssignees = ids.reduce((acc, id) => {
        const t = (tasks ?? []).find((d) => (d.task_id ?? d._id) === id);
        if (t) acc[id] = t.assignee_id ?? null;
        return acc;
      }, {});

      const exec = async () => {
        setSaving(true);
        try {
          const res = await api.patch(`${API_URL}/api/projects/tasks/bulk-assign`, {
            task_ids: ids,
            assignee_id: userId,
          });
          await handleBulkResponse(res, 'assign tasks', {
            message: `Assigned ${count} task${count === 1 ? '' : 's'} to ${userName}`,
            onUndo: async () => {
              const results = await Promise.allSettled(
                Object.entries(prevAssignees).map(([id, prev]) =>
                  api.patch(`${API_URL}/api/projects/tasks/${id}`, { assignee_id: prev }),
                ),
              );
              for (const r of results) {
                if (r.status === 'fulfilled' && r.value?.ok) {
                  try { onTaskMutated?.(await r.value.json()); } catch { /* swallow */ }
                }
              }
            },
          });
        } catch {
          toast.error('Could not assign tasks.');
        } finally {
          setSaving(false);
        }
      };
      maybeConfirm({
        title: `Assign ${count} task${count === 1 ? '' : 's'}?`,
        message: `Assign all selected tasks to ${userName}.`,
        exec,
      });
    },
    [selectedIds, count, maybeConfirm, handleBulkResponse, assignees, tasks],
  );

  const bulkSprint = useCallback(
    (sprintId) => {
      const sprintName =
        sprintId === null
          ? 'Backlog'
          : (sprints ?? []).find((s) => s.sprint_id === sprintId)?.name ?? sprintId;

      // Snapshot prev sprint ids for undo before the request fires.
      const ids = Array.from(selectedIds);
      const prevSprints = ids.reduce((acc, id) => {
        const t = (tasks ?? []).find((d) => (d.task_id ?? d._id) === id);
        // methodology_fields.sprint_id lives on agile tasks; null/undefined
        // means the task is in the backlog.
        if (t) acc[id] = t.methodology_fields?.sprint_id ?? null;
        return acc;
      }, {});

      const exec = async () => {
        setSaving(true);
        try {
          const res = await api.patch(`${API_URL}/api/projects/tasks/bulk-sprint`, {
            task_ids: ids,
            sprint_id: sprintId,
          });
          await handleBulkResponse(res, 'move to sprint', {
            message: `Moved ${count} task${count === 1 ? '' : 's'} to ${sprintName}`,
            onUndo: async () => {
              const results = await Promise.allSettled(
                Object.entries(prevSprints).map(([id, prev]) =>
                  api.patch(`${API_URL}/api/projects/tasks/${id}`, {
                    methodology_fields: { sprint_id: prev },
                  }),
                ),
              );
              for (const r of results) {
                if (r.status === 'fulfilled' && r.value?.ok) {
                  try { onTaskMutated?.(await r.value.json()); } catch { /* swallow */ }
                }
              }
            },
          });
        } catch {
          toast.error('Could not move tasks to sprint.');
        } finally {
          setSaving(false);
        }
      };
      maybeConfirm({
        title: `Move ${count} task${count === 1 ? '' : 's'} to sprint?`,
        message: `Move all selected tasks to ${sprintName}.`,
        exec,
      });
    },
    [selectedIds, count, maybeConfirm, handleBulkResponse, sprints, tasks, onTaskMutated],
  );

  const bulkDelete = useCallback(() => {
    const exec = async () => {
      setSaving(true);
      try {
        const ids = Array.from(selectedIds);
        const results = await Promise.allSettled(
          ids.map((id) => api.delete(`${API_URL}/api/projects/tasks/${id}`)),
        );
        let deleted = 0;
        let failed = 0;
        results.forEach((r, i) => {
          if (r.status === 'fulfilled' && r.value?.ok) {
            onTaskDeleted?.(ids[i]);
            deleted++;
          } else {
            failed++;
          }
        });
        if (failed === 0) {
          toast.success(`Deleted ${deleted} task${deleted === 1 ? '' : 's'}`);
        } else {
          toast.warning(`Deleted ${deleted}, failed to delete ${failed}`);
        }
        onClear();
      } catch {
        toast.error('Could not delete tasks.');
      } finally {
        setSaving(false);
      }
    };

    setConfirm({
      open: true,
      title: `Delete ${count} task${count === 1 ? '' : 's'}?`,
      message: `This will permanently delete ${count} task${count === 1 ? '' : 's'}. This cannot be undone.`,
      fn: exec,
      isDelete: true,
    });
  }, [selectedIds, count, onTaskDeleted, onClear]);

  if (count === 0) return null;

  // Sprints for the dropdown — exclude closed, add a backlog option
  const sprintOptions = sprints
    ? [
        { value: null, label: 'Move to Backlog' },
        ...(sprints ?? [])
          .filter((s) => s.status !== 'closed')
          .map((s) => ({ value: s.sprint_id, label: s.name })),
      ]
    : null;

  // Assignee options
  const assigneeOptions = [
    { value: null, label: 'Unassigned' },
    ...(assignees ?? []).map((u) => ({
      value: u.user_id ?? u._id,
      label: u.display_name ?? u.name ?? (u.user_id ?? u._id),
    })),
  ];

  return (
    <>
      <div
        role="toolbar"
        aria-label="Bulk task actions"
        data-testid="bulk-action-bar"
        className="fixed bottom-6 left-1/2 -translate-x-1/2 z-30 flex items-center gap-2 px-4 py-2 rounded-xl border border-aegisaxiom-border bg-aegisaxiom-surface shadow-2xl"
      >
        {/* Count + clear */}
        <span className="text-xs font-semibold text-aegisaxiom-text mr-1" data-testid="bulk-bar-count">
          {count} selected
        </span>

        {/* Status */}
        <BarDropdown
          label="Change status"
          options={statusOptions}
          onSelect={bulkStatus}
          disabled={saving}
        />

        {/* Assign */}
        <BarDropdown
          label="Assign to"
          options={assigneeOptions}
          onSelect={bulkAssign}
          disabled={saving}
        />

        {/* Sprint (optional) */}
        {sprintOptions ? (
          <BarDropdown
            label="Move to sprint"
            options={sprintOptions}
            onSelect={bulkSprint}
            disabled={saving}
          />
        ) : null}

        <div className="w-px h-5 bg-aegisaxiom-border mx-1" aria-hidden="true" />

        {/* Delete */}
        {canDelete ? (
          <button
            type="button"
            onClick={bulkDelete}
            disabled={saving}
            className="inline-flex items-center gap-1.5 h-8 px-3 rounded-md text-xs font-medium bg-red-600/15 border border-red-600/30 text-red-400 hover:bg-red-600/25 transition-colors disabled:opacity-50"
            data-testid="bulk-bar-delete"
            aria-label={`Delete ${count} selected tasks`}
          >
            <Trash2 size={12} />
            Delete
          </button>
        ) : null}

        {saving ? (
          <Loader2 size={14} className="animate-spin text-aegisaxiom-text-muted" aria-label="Saving" />
        ) : null}

        {/* Clear selection */}
        <button
          type="button"
          onClick={onClear}
          disabled={saving}
          className="ml-1 inline-flex items-center justify-center h-7 w-7 rounded-full text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors disabled:opacity-50"
          aria-label="Clear selection"
          data-testid="bulk-bar-clear"
        >
          <X size={14} />
        </button>
      </div>

      {/* Confirm modal */}
      <ConfirmModal
        isOpen={confirm.open}
        title={confirm.title}
        message={confirm.message}
        confirmText="Yes, proceed"
        cancelText="Cancel"
        variant={confirm.isDelete ? 'danger' : 'primary'}
        onConfirm={() => { confirm.fn?.(); }}
        onCancel={closeConfirm}
      />
    </>
  );
}
