import React from 'react';
import { AlertTriangle, X } from 'lucide-react';

/**
 * Reusable confirm modal — dark theme, slate/violet palette.
 * Drop-in replacement for window.confirm() that works in iframes (Emergent platform).
 *
 * Props:
 *   isOpen      — boolean
 *   onConfirm   — () => void   called when user clicks the confirm button
 *   onCancel    — () => void   called when user dismisses (X, Cancel, or backdrop)
 *   title       — string       modal heading
 *   message     — string       body text (supports newlines via white-space pre-line)
 *   confirmText — string       confirm button label  (default: "Confirm")
 *   cancelText  — string       cancel button label   (default: "Cancel")
 *   variant     — 'danger' | 'warning' | 'primary'   (default: 'danger')
 */
export default function ConfirmModal({
  isOpen,
  onConfirm,
  onCancel,
  title = 'Are you sure?',
  message,
  confirmText = 'Confirm',
  cancelText = 'Cancel',
  variant = 'danger',
}) {
  if (!isOpen) return null;

  const confirmClasses =
    {
      danger: 'bg-red-600 hover:bg-red-500 text-white',
      warning: 'bg-amber-600 hover:bg-amber-500 text-white',
      primary: 'bg-violet-600 hover:bg-violet-500 text-white',
    }[variant] || 'bg-red-600 hover:bg-red-500 text-white';

  const iconClasses =
    {
      danger: 'text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-600/10 border-red-200 dark:border-red-600/20',
      warning: 'text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-600/10 border-amber-200 dark:border-amber-600/20',
      primary: 'text-violet-600 dark:text-violet-400 bg-violet-50 dark:bg-violet-600/10 border-violet-200 dark:border-violet-600/20',
    }[variant] || 'text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-600/10 border-red-200 dark:border-red-600/20';

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-modal-title"
    >
      {/* Backdrop click to cancel */}
      <div className="absolute inset-0" onClick={onCancel} aria-hidden="true" />

      <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl w-full max-w-sm">
        {/* Header */}
        <div className="flex items-start gap-3 p-5 pb-4">
          <div
            className={`w-9 h-9 rounded-lg border flex items-center justify-center shrink-0 mt-0.5 ${iconClasses}`}
          >
            <AlertTriangle size={18} />
          </div>
          <div className="flex-1 min-w-0">
            <h3
              id="confirm-modal-title"
              className="text-base font-semibold text-aegisaxiom-text leading-snug"
            >
              {title}
            </h3>
            {message && (
              <p className="mt-1.5 text-sm text-aegisaxiom-text-secondary leading-relaxed whitespace-pre-line">
                {message}
              </p>
            )}
          </div>
          <button
            onClick={onCancel}
            className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary transition shrink-0"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Footer */}
        <div className="flex gap-2 px-5 pb-5">
          <button
            onClick={onCancel}
            className="flex-1 px-4 py-2 rounded-lg text-sm font-medium bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary border border-aegisaxiom-border hover:border-aegisaxiom-border-hover transition"
          >
            {cancelText}
          </button>
          <button
            onClick={() => {
              onConfirm();
              onCancel();
            }}
            className={`flex-1 px-4 py-2 rounded-lg text-sm font-medium transition ${confirmClasses}`}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
