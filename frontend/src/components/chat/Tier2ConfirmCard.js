/**
 * Tier2ConfirmCard — inline confirmation card for blocked Tier-2 tool calls.
 *
 * Rendered inside the chat message list when the AI's function response
 * contains `{ _blocked: true, error: "tier_2_confirmation_required", tool, intended_params }`.
 *
 * "Confirm" → sends "Yes, proceed." to the chat conductor.
 * "Cancel"  → sends "No, don't do that."
 *
 * Mobile-friendly: card fills available width, buttons stack on very small screens.
 */

import React, { useState } from 'react';
import { ShieldAlert, CheckCircle2, XCircle } from 'lucide-react';

// ---------------------------------------------------------------------------
// Tool-name pretty-mapping
// ---------------------------------------------------------------------------

const TOOL_DISPLAY_NAMES = {
  create_project: 'Create a new project',
  create_task: 'Create a new task',
  delete_task: 'Delete a task',
  bulk_update_task_status: 'Change status on multiple tasks',
  bulk_assign_tasks: 'Reassign multiple tasks',
  create_project_from_template: 'Create a project from a template',
};

function toolDisplayName(tool) {
  return TOOL_DISPLAY_NAMES[tool] ?? tool ?? 'Perform an action';
}

// ---------------------------------------------------------------------------
// Param summary — render intended_params as a readable list
// ---------------------------------------------------------------------------

function ParamSummary({ params }) {
  if (!params || typeof params !== 'object') return null;
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '');
  if (entries.length === 0) return null;

  return (
    <ul className="mt-2 space-y-0.5 text-xs text-aegisaxiom-text-secondary" aria-label="Action details">
      {entries.map(([key, value]) => (
        <li key={key} className="flex gap-1.5 min-w-0">
          <span className="font-medium text-aegisaxiom-text-muted shrink-0 capitalize">
            {key.replace(/_/g, ' ')}:
          </span>
          <span className="truncate">
            {Array.isArray(value) ? value.join(', ') : String(value)}
          </span>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Tier2ConfirmCard
// ---------------------------------------------------------------------------

/**
 * @param {object} props
 * @param {string}   props.tool            — Raw tool name from the blocked response
 * @param {object}   [props.intendedParams] — The params the AI intended to pass
 * @param {string}   [props.detail]         — Optional detail string from the backend
 * @param {Function} props.onSend           — (text: string) => void — sends a chat message
 */
export default function Tier2ConfirmCard({ tool, intendedParams, detail, onSend }) {
  const [resolved, setResolved] = useState(null); // 'confirmed' | 'cancelled'

  const handleConfirm = () => {
    setResolved('confirmed');
    onSend('Yes, proceed.');
  };

  const handleCancel = () => {
    setResolved('cancelled');
    onSend("No, don't do that.");
  };

  const displayName = toolDisplayName(tool);

  if (resolved) {
    return (
      <div
        className="rounded-xl border border-aegisaxiom-border bg-aegisaxiom-surface px-4 py-3 text-sm text-aegisaxiom-text-muted flex items-center gap-2"
        data-testid="tier2-confirm-resolved"
        role="status"
      >
        {resolved === 'confirmed' ? (
          <>
            <CheckCircle2 size={15} className="text-green-400 shrink-0" aria-hidden="true" />
            Confirmed — proceeding.
          </>
        ) : (
          <>
            <XCircle size={15} className="text-aegisaxiom-text-muted shrink-0" aria-hidden="true" />
            Cancelled.
          </>
        )}
      </div>
    );
  }

  return (
    <div
      className="rounded-xl border border-amber-500/30 bg-amber-500/5 dark:bg-amber-900/10 px-4 py-3.5 w-full max-w-sm"
      data-testid="tier2-confirm-card"
      role="alertdialog"
      aria-label="Confirmation needed"
      aria-modal="false"
    >
      {/* Header */}
      <div className="flex items-center gap-2 mb-2">
        <ShieldAlert
          size={15}
          className="text-amber-500 dark:text-amber-400 shrink-0"
          aria-hidden="true"
        />
        <span className="text-xs font-semibold text-amber-600 dark:text-amber-400 uppercase tracking-wide">
          Confirmation needed
        </span>
      </div>

      {/* Proposed action */}
      <p className="text-sm font-medium text-aegisaxiom-text">
        I want to{' '}
        <span className="text-amber-600 dark:text-amber-400">{displayName.toLowerCase()}</span>
      </p>

      {/* Param summary */}
      <ParamSummary params={intendedParams} />

      {/* Optional detail from backend */}
      {detail && (
        <p className="mt-2 text-xs text-aegisaxiom-text-secondary">{detail}</p>
      )}

      {/* Actions */}
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={handleConfirm}
          className="flex-1 sm:flex-none inline-flex items-center justify-center gap-1.5 h-8 px-4 rounded-lg text-xs font-semibold bg-violet-600 hover:bg-violet-700 text-white transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
          data-testid="tier2-confirm-btn"
        >
          <CheckCircle2 size={13} aria-hidden="true" />
          Confirm
        </button>
        <button
          type="button"
          onClick={handleCancel}
          className="flex-1 sm:flex-none inline-flex items-center justify-center gap-1.5 h-8 px-4 rounded-lg text-xs font-medium border border-aegisaxiom-border bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
          data-testid="tier2-cancel-btn"
        >
          <XCircle size={13} aria-hidden="true" />
          Cancel
        </button>
      </div>
    </div>
  );
}
