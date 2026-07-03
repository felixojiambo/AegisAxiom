import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { X, AlertCircle, Bot, Workflow, Trash2, Check } from 'lucide-react';
import { toast } from 'sonner';
import { deleteInstance } from './api';
import { formatMaxInstances } from './helpers';

/**
 * Phase 1D.7 — Instance List drill-down modal.
 *
 * Opens when the user clicks the "X / Y instances" count on an
 * assignment row. Lists the assignment's individual instances and
 * offers per-row delete with inline confirmation (no nested modal —
 * the confirm UI swaps in on the same row).
 *
 * Each delete frees one slot against the parent assignment's
 * max_instances cap. After a successful delete we tell the parent
 * to reload page data so the assignment row's count re-derives.
 *
 * The instances list is computed at open time from the page's existing
 * `instances` state (filtered by assignment_id) and passed in as a
 * prop. We could re-fetch on open for freshness, but the parent's
 * loadAll() pattern after every mutation keeps things in lockstep
 * already.
 *
 * Props
 *   isOpen           boolean
 *   tenantId         string — owning tenant
 *   tenantName       string — for header context
 *   assignment       Assignment — for max_instances + title
 *   definition       Definition — joined for name + type rendering
 *   instances        Instance[] — pre-filtered to this assignment
 *   onClose          () => void
 *   onInstanceDeleted () => void  — caller reloads page state
 */

export default function InstanceListModal({
  isOpen,
  tenantId,
  tenantName,
  assignment,
  definition,
  instances,
  onClose,
  onInstanceDeleted,
}) {
  // The instance currently in "confirm delete?" state. Click trash on
  // a row → that row's id lands here → row swaps to a confirm UI.
  // Clicking trash again on a different row moves the pending slot —
  // only one inline confirm at a time, simpler mental model.
  const [pendingDeleteId, setPendingDeleteId] = useState(null);
  // Set of instance ids currently submitting a delete. Multiple
  // concurrent deletes shouldn't be possible from a single-pending
  // UI, but keeping this as a set is robust to fast clicks.
  const [deletingIds, setDeletingIds] = useState(() => new Set());
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!isOpen) return;
    setPendingDeleteId(null);
    setDeletingIds(new Set());
    setError(null);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e) => {
      if (e.key === 'Escape') {
        // ESC first cancels any pending confirm; second ESC closes.
        if (pendingDeleteId) setPendingDeleteId(null);
        else onClose();
      }
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose, pendingDeleteId]);

  const sortedInstances = useMemo(() => {
    const arr = [...(instances || [])];
    arr.sort((a, b) => {
      // Most recent first — matches the backend's default sort.
      const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
      const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
      return tb - ta;
    });
    return arr;
  }, [instances]);

  const max = assignment?.max_instances;
  const count = sortedInstances.length;
  const maxLabel = formatMaxInstances(max);

  const handleDeleteConfirm = useCallback(
    async (employeeId, instanceName) => {
      setError(null);
      setDeletingIds((prev) => {
        const next = new Set(prev);
        next.add(employeeId);
        return next;
      });

      try {
        const res = await deleteInstance(tenantId, employeeId);
        if (res.ok) {
          toast.success(`${instanceName || 'Instance'} deleted.`);
          setPendingDeleteId(null);
          // Parent reloads its instance list; this modal will re-receive
          // a smaller `instances` prop on next render.
          onInstanceDeleted();
          return;
        }

        const errBody = await res.json().catch(() => ({}));
        const detail = errBody?.detail;
        if (res.status === 403) {
          setError("You don't have permission to delete instances.");
        } else if (res.status === 404) {
          // Already gone — treat as success-ish, just refresh.
          toast.info(`${instanceName || 'Instance'} was already gone.`);
          setPendingDeleteId(null);
          onInstanceDeleted();
          return;
        } else {
          setError(
            (typeof detail === 'string' ? detail : null) ||
              `Delete failed (HTTP ${res.status}).`,
          );
        }
      } catch (e) {
        setError('Network error. Please try again.');
      } finally {
        setDeletingIds((prev) => {
          const next = new Set(prev);
          next.delete(employeeId);
          return next;
        });
      }
    },
    [tenantId, onInstanceDeleted],
  );

  if (!isOpen || !assignment || !definition) return null;

  const isN8n = definition.type === 'n8n';
  const TypeIcon = isN8n ? Workflow : Bot;

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-labelledby="instance-list-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-2xl p-6 animate-fadeInUp max-h-[90vh] overflow-y-auto"
        data-testid="instance-list-modal"
      >
        <div className="flex items-start justify-between mb-5">
          <div>
            <h2
              id="instance-list-title"
              className="text-lg font-bold text-aegisaxiom-text"
            >
              {definition.name} instances
            </h2>
            <p className="text-xs text-aegisaxiom-text-secondary mt-1">
              <span className="font-medium text-aegisaxiom-text">{tenantName}</span>{' '}
              · <span className="tabular-nums">{count}</span> of{' '}
              <span className="tabular-nums">{maxLabel}</span>{' '}
              instances provisioned
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {error && (
          <div className="bg-red-50 dark:bg-red-600/10 border border-red-200 dark:border-red-600/20 rounded-lg p-3 mb-4 flex items-start gap-2">
            <AlertCircle
              size={14}
              className="text-red-600 dark:text-red-400 shrink-0 mt-0.5"
            />
            <p className="text-xs text-red-700 dark:text-red-300 leading-relaxed">
              {error}
            </p>
          </div>
        )}

        {sortedInstances.length === 0 ? (
          <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-8 text-center">
            <TypeIcon size={28} className="text-aegisaxiom-text-muted mx-auto mb-2" />
            <h3 className="text-sm font-semibold text-aegisaxiom-text mb-1">
              No instances provisioned yet
            </h3>
            <p className="text-xs text-aegisaxiom-text-secondary max-w-sm mx-auto">
              Provision one from the assignment row to get started.
            </p>
          </div>
        ) : (
          <div className="border border-aegisaxiom-border rounded-lg overflow-hidden">
            {sortedInstances.map((inst, idx) => {
              const isPending = pendingDeleteId === inst.employee_id;
              const isDeleting = deletingIds.has(inst.employee_id);
              const isLast = idx === sortedInstances.length - 1;
              return (
                <div
                  key={inst.employee_id}
                  className={
                    'flex items-center justify-between p-3 transition-colors ' +
                    (isLast ? '' : 'border-b border-aegisaxiom-border ') +
                    (isPending ? 'bg-red-50 dark:bg-red-600/5 ' : '')
                  }
                  data-testid={`instance-row-${inst.employee_id}`}
                >
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <div
                      className={
                        'w-8 h-8 rounded-md flex items-center justify-center shrink-0 ' +
                        (isN8n
                          ? 'bg-indigo-100 dark:bg-indigo-600/20 text-indigo-700 dark:text-indigo-300'
                          : 'bg-violet-100 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300')
                      }
                    >
                      {definition.default_avatar ? (
                        <span className="text-sm">{definition.default_avatar}</span>
                      ) : (
                        <TypeIcon size={14} />
                      )}
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-aegisaxiom-text truncate">
                        {inst.name || 'Untitled instance'}
                      </div>
                      <div className="text-[11px] text-aegisaxiom-text-muted truncate">
                        Created {formatDate(inst.created_at)}
                        {inst.employee_id && (
                          <span className="ml-2 font-mono">{inst.employee_id}</span>
                        )}
                      </div>
                    </div>
                  </div>

                  {isPending ? (
                    <div className="flex items-center gap-2 shrink-0 ml-3">
                      <span className="text-xs text-red-700 dark:text-red-300 mr-1">
                        Delete this instance?
                      </span>
                      <button
                        type="button"
                        onClick={() => setPendingDeleteId(null)}
                        disabled={isDeleting}
                        className="text-xs px-2.5 py-1 rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface text-aegisaxiom-text hover:border-aegisaxiom-border-hover disabled:opacity-50"
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          handleDeleteConfirm(inst.employee_id, inst.name)
                        }
                        disabled={isDeleting}
                        className="text-xs px-2.5 py-1 rounded-md bg-red-600 hover:bg-red-500 text-white disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1"
                        data-testid={`confirm-delete-${inst.employee_id}`}
                      >
                        <Check size={12} />
                        {isDeleting ? 'Deleting…' : 'Delete'}
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setPendingDeleteId(inst.employee_id)}
                      className="text-aegisaxiom-text-muted hover:text-red-600 dark:hover:text-red-400 p-1.5 rounded-md hover:bg-red-50 dark:hover:bg-red-600/10 transition shrink-0 ml-3"
                      aria-label="Delete instance"
                      data-testid={`delete-${inst.employee_id}`}
                    >
                      <Trash2 size={15} />
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <div className="flex items-center justify-end mt-4 pt-1">
          <button type="button" onClick={onClose} className="btn-secondary">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Helpers ────────────────────────────────────────────────────────

const dateFmt = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

function formatDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return dateFmt.format(d);
}
