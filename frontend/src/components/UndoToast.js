/**
 * UndoToast — undoable mutation toast built on top of sonner.
 *
 * Usage:
 *   import { showUndoToast } from '@/components/UndoToast';
 *
 *   showUndoToast({
 *     message: "Status changed to 'In Progress'",
 *     duration: 5000,          // optional, default 5000ms
 *     onUndo: async () => { ... },
 *   });
 *
 * Behavior:
 *   - Renders a toast with a countdown bar and an Undo button.
 *   - Clicking Undo fires onUndo(), dismisses the toast, shows "Undone."
 *   - After duration the toast vanishes silently.
 *   - If onUndo is absent the Undo button is not rendered (use for non-invertible ops).
 */

import React, { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { Undo2 } from 'lucide-react';

// ---------------------------------------------------------------------------
// Internal countdown toast component rendered by sonner's custom JSX API.
// ---------------------------------------------------------------------------

function UndoToastContent({ message, duration, onUndo, toastId }) {
  const [undone, setUndone] = useState(false);
  const [progress, setProgress] = useState(100);
  const startRef = useRef(Date.now());
  const rafRef = useRef(null);

  useEffect(() => {
    const tick = () => {
      const elapsed = Date.now() - startRef.current;
      const pct = Math.max(0, 100 - (elapsed / duration) * 100);
      setProgress(pct);
      if (pct > 0) {
        rafRef.current = requestAnimationFrame(tick);
      }
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [duration]);

  const handleUndo = async () => {
    cancelAnimationFrame(rafRef.current);
    setUndone(true);
    try {
      await onUndo?.();
    } catch {
      // onUndo failed — already reverted in caller; toast.error shown there if needed
    }
    // Brief "Undone" confirmation, then dismiss
    setTimeout(() => toast.dismiss(toastId), 1200);
  };

  return (
    <div className="w-full" data-testid="undo-toast">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm text-aegisaxiom-text flex-1 min-w-0 truncate">
          {undone ? 'Undone.' : message}
        </span>
        {!undone && onUndo && (
          <button
            type="button"
            onClick={handleUndo}
            className="shrink-0 inline-flex items-center gap-1 text-xs font-semibold text-violet-400 hover:text-violet-300 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
            data-testid="undo-toast-btn"
            aria-label="Undo last action"
          >
            <Undo2 size={12} aria-hidden="true" />
            Undo
          </button>
        )}
      </div>
      {!undone && (
        <div
          className="mt-2 h-0.5 rounded-full bg-aegisaxiom-border overflow-hidden"
          role="progressbar"
          aria-valuenow={Math.round(progress)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Time remaining"
        >
          <div
            className="h-full bg-violet-500 transition-none"
            style={{ width: `${progress}%` }}
            data-testid="undo-toast-progress"
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * @param {object} opts
 * @param {string} opts.message         — Human-readable description of what changed
 * @param {number} [opts.duration=5000] — How long before auto-dismiss (ms)
 * @param {Function} [opts.onUndo]      — Async function that reverts the change.
 *                                        Omit for non-invertible actions.
 */
export function showUndoToast({ message, duration = 5000, onUndo }) {
  const id = `undo-${Date.now()}`;
  toast.custom(
    (t) => (
      <div className="w-full max-w-sm bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-lg px-4 py-3 pointer-events-auto">
        <UndoToastContent
          message={message}
          duration={duration}
          onUndo={onUndo}
          toastId={t}
        />
      </div>
    ),
    {
      id,
      duration,
      position: 'bottom-center',
    },
  );
  return id;
}
