import React from 'react';
import { AlertTriangle } from 'lucide-react';

/**
 * 409 conflict resolution modal.
 *
 * Props:
 *   isOpen        boolean
 *   myDoc         object   — the user's pending (locally applied) version of the task
 *   theirDoc      object   — current_doc from the 409 response (the conflicting server state)
 *   onDiscard     fn       — revert local state to theirDoc; close dialog
 *   onOverwrite   fn       — re-PATCH without If-Match (force-write myDoc); close dialog
 *   onMerge       fn       — apply theirDoc to local state, close dialog, let user re-edit
 */
export default function ConflictDialog({ isOpen, myDoc, theirDoc, onDiscard, onOverwrite, onMerge }) {
  if (!isOpen) return null;

  const DIFF_FIELDS = ['title', 'status', 'priority', 'assignee_id', 'due_date', 'start_date', 'description'];

  const changedFields = DIFF_FIELDS.filter(
    (f) => myDoc?.[f] !== undefined && theirDoc?.[f] !== undefined && myDoc[f] !== theirDoc[f],
  );

  const fmt = (v) => {
    if (v === null || v === undefined || v === '') return <span className="italic text-aegisaxiom-text-muted">—</span>;
    return String(v);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="conflict-dialog-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      data-testid="conflict-dialog"
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60" aria-hidden="true" />

      <div className="relative w-full max-w-lg bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl p-6 flex flex-col gap-5">
        {/* Header */}
        <div className="flex items-start gap-3">
          <AlertTriangle size={20} className="text-yellow-400 shrink-0 mt-0.5" aria-hidden="true" />
          <div>
            <h2 id="conflict-dialog-title" className="text-sm font-semibold text-aegisaxiom-text leading-snug">
              Editing conflict
            </h2>
            <p className="text-xs text-aegisaxiom-text-muted mt-0.5">
              Another user changed this task while you were editing. How would you like to proceed?
            </p>
          </div>
        </div>

        {/* Side-by-side diff */}
        {changedFields.length > 0 ? (
          <div className="rounded-lg border border-aegisaxiom-border overflow-hidden text-xs">
            <div className="grid grid-cols-2 divide-x divide-aegisaxiom-border bg-aegisaxiom-elevated">
              <div className="px-3 py-2 font-semibold text-aegisaxiom-text-muted uppercase tracking-wider text-[10px]">Your changes</div>
              <div className="px-3 py-2 font-semibold text-aegisaxiom-text-muted uppercase tracking-wider text-[10px]">Their changes</div>
            </div>
            {changedFields.map((field) => (
              <div key={field} className="grid grid-cols-2 divide-x divide-aegisaxiom-border border-t border-aegisaxiom-border">
                <div className="px-3 py-2">
                  <div className="text-[10px] text-aegisaxiom-text-muted capitalize mb-0.5">{field.replace(/_/g, ' ')}</div>
                  <div className="text-aegisaxiom-text">{fmt(myDoc?.[field])}</div>
                </div>
                <div className="px-3 py-2">
                  <div className="text-[10px] text-aegisaxiom-text-muted capitalize mb-0.5">{field.replace(/_/g, ' ')}</div>
                  <div className="text-aegisaxiom-text">{fmt(theirDoc?.[field])}</div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-aegisaxiom-text-muted">
            The conflicting fields could not be determined. Inspect both versions before choosing an action.
          </p>
        )}

        {/* Actions */}
        <div className="flex flex-col sm:flex-row gap-2 pt-1">
          <button
            type="button"
            onClick={onDiscard}
            className="flex-1 h-9 px-3 rounded-md border border-aegisaxiom-border text-xs text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated transition-colors"
            data-testid="conflict-discard"
          >
            Discard mine
          </button>
          <button
            type="button"
            onClick={onMerge}
            className="flex-1 h-9 px-3 rounded-md border border-aegisaxiom-border text-xs text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors"
            data-testid="conflict-merge"
          >
            Merge manually
          </button>
          <button
            type="button"
            onClick={onOverwrite}
            className="flex-1 h-9 px-3 rounded-md bg-violet-600 hover:bg-violet-500 text-xs text-white font-medium transition-colors"
            data-testid="conflict-overwrite"
          >
            Overwrite theirs
          </button>
        </div>
      </div>
    </div>
  );
}
