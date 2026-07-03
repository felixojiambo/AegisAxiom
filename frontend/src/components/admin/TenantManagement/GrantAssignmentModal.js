import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { X, AlertCircle, Bot, Workflow, Check, Infinity as InfinityIcon } from 'lucide-react';
import { toast } from 'sonner';
import { grantAssignment } from './api';

/**
 * Phase 1D.5 — Grant Assignment modal.
 *
 * Opens from the Tenant Detail page's "+ Grant assignment" button. The
 * caller (TenantDetail) provides the target tenant, the full list of
 * AI Employee definitions loaded for the page, and the tenant's
 * existing assignments. We filter the picker client-side to:
 *
 *   • definitions that are is_active && is_published (drafts hidden)
 *   • definitions NOT already granted to this tenant in ANY status
 *     (active, suspended, or revoked) — see Phase 1D.9. Revoked rows
 *     are reactivated from the assignment-list kebab, not re-granted
 *     from this picker, because the backend's compound unique index
 *     on (tenant_id, definition_id) makes "regrant" a status
 *     transition rather than a new insert.
 *
 * Filtering client-side gives a clean UX — the user can't even attempt
 * a duplicate grant — but we still handle the server's 409 case as a
 * safety net in case the page's view is stale (someone else granted
 * the same definition in another tab between our page load and the
 * user's submit).
 *
 * Props
 *   isOpen               boolean
 *   tenantId             string — target tenant for the grant
 *   tenantName           string — for the modal title
 *   definitions          Definition[] — full list, we filter internally
 *   existingAssignments  Assignment[] — already-held, we filter internally
 *   onClose              () => void
 *   onSuccess            (assignment) => void
 */

export default function GrantAssignmentModal({
  isOpen,
  tenantId,
  tenantName,
  definitions,
  existingAssignments,
  onClose,
  onSuccess,
}) {
  const [definitionId, setDefinitionId] = useState('');
  const [maxInstances, setMaxInstances] = useState('1');
  const [unlimited, setUnlimited] = useState(false);
  const [notes, setNotes] = useState('');
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);

  // Reset state when the modal opens.
  useEffect(() => {
    if (!isOpen) return;
    setDefinitionId('');
    setMaxInstances('1');
    setUnlimited(false);
    setNotes('');
    setErrors({});
    setSubmitting(false);
  }, [isOpen]);

  // ESC to close (when not submitting).
  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e) => {
      if (e.key === 'Escape' && !submitting) onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose, submitting]);

  // Definitions already granted to this tenant — ANY status.
  //
  // Phase 1D.9 fix: previously this excluded only non-revoked, on the
  // assumption that revoked rows would be re-grantable as new rows.
  // That contradicted the backend's compound unique index on
  // (tenant_id, definition_id), which rejects a second insert for the
  // same pair regardless of the first row's status, producing a
  // confusing 409 + "refresh the page" error. The intended model is:
  // revoke is a status transition on a permanent row; re-granting is
  // a PATCH back to active (the Reactivate flow added in 1D.9,
  // surfaced on the assignment-row kebab). So we hide all existing
  // assignments from the picker; admins reactivate revoked ones from
  // the row itself.
  const grantedDefinitionIds = useMemo(() => {
    const set = new Set();
    for (const a of existingAssignments || []) {
      set.add(a.definition_id);
    }
    return set;
  }, [existingAssignments]);

  // Available definitions: published + active + not already granted.
  const availableDefinitions = useMemo(
    () =>
      (definitions || [])
        .filter(
          (d) =>
            d.is_active &&
            d.is_published &&
            !grantedDefinitionIds.has(d.definition_id),
        )
        // Stable sort by name for predictable picker order.
        .sort((a, b) => (a.name || '').localeCompare(b.name || '')),
    [definitions, grantedDefinitionIds],
  );

  const selectedDefinition = useMemo(
    () => availableDefinitions.find((d) => d.definition_id === definitionId) || null,
    [availableDefinitions, definitionId],
  );

  const validate = useCallback(() => {
    const errs = {};
    if (!definitionId) errs.definitionId = 'Pick an AI Employee.';
    if (!unlimited) {
      const n = parseInt(maxInstances, 10);
      if (isNaN(n) || n < 1) {
        errs.maxInstances = 'Must be a positive number, or use Unlimited.';
      } else if (n > 10000) {
        errs.maxInstances = 'Suspiciously high — cap at 10,000 or use Unlimited.';
      }
    }
    return errs;
  }, [definitionId, maxInstances, unlimited]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    const validationErrors = validate();
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      return;
    }
    setSubmitting(true);
    setErrors({});

    try {
      const body = {
        definition_id: definitionId,
        tenant_id: tenantId,
        max_instances: unlimited ? null : parseInt(maxInstances, 10),
      };
      const trimmedNotes = notes.trim();
      if (trimmedNotes) body.notes = trimmedNotes;

      const res = await grantAssignment(tenantId, body);
      if (res.ok) {
        const data = await res.json();
        toast.success(
          `${selectedDefinition?.name || 'Assignment'} granted to ${tenantName}.`,
        );
        onSuccess(data);
        return;
      }

      const errBody = await res.json().catch(() => ({}));
      const detail = errBody?.detail;
      if (res.status === 409) {
        // Most likely cause: a revoked row already exists for this
        // (tenant, definition) pair (the backend's compound unique
        // index treats revoked the same as any other status). Less
        // likely: stale view race where someone else just granted in
        // a different tab. Either way the fix is the same — refresh
        // and reactivate from the row.
        setErrors({
          definitionId:
            'This AI Employee already has an assignment on this tenant (possibly revoked). Close this dialog and reactivate it from the assignment row.',
        });
      } else if (res.status === 400 || res.status === 422) {
        setErrors({ _global: detail || 'Some values are invalid.' });
      } else if (res.status === 403) {
        setErrors({ _global: "You don't have permission to grant assignments." });
      } else if (res.status === 404) {
        setErrors({ _global: 'Tenant or definition no longer exists.' });
      } else {
        setErrors({ _global: detail || `Request failed (HTTP ${res.status}).` });
      }
    } catch (err) {
      setErrors({ _global: 'Network error. Please try again.' });
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-labelledby="grant-assignment-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-lg p-6 animate-fadeInUp max-h-[90vh] overflow-y-auto"
        data-testid="grant-assignment-modal"
      >
        {/* Header */}
        <div className="flex items-start justify-between mb-5">
          <div>
            <h2
              id="grant-assignment-modal-title"
              className="text-lg font-bold text-aegisaxiom-text"
            >
              Grant assignment
            </h2>
            <p className="text-xs text-aegisaxiom-text-secondary mt-1">
              Pick an AI Employee for{' '}
              <span className="font-medium text-aegisaxiom-text">{tenantName}</span>{' '}
              and set how many instances they can spin up.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text disabled:opacity-50"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Global error */}
        {errors._global && (
          <div className="bg-red-50 dark:bg-red-600/10 border border-red-200 dark:border-red-600/20 rounded-lg p-3 mb-4 flex items-start gap-2">
            <AlertCircle
              size={14}
              className="text-red-600 dark:text-red-400 shrink-0 mt-0.5"
            />
            <p className="text-xs text-red-700 dark:text-red-300 leading-relaxed">
              {errors._global}
            </p>
          </div>
        )}

        {/* No definitions available — total empty state */}
        {availableDefinitions.length === 0 ? (
          <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-5 text-center my-2">
            <Bot
              size={28}
              className="text-aegisaxiom-text-muted mx-auto mb-2"
            />
            <h3 className="text-sm font-semibold text-aegisaxiom-text mb-1">
              No AI Employees available to grant
            </h3>
            <p className="text-xs text-aegisaxiom-text-secondary max-w-sm mx-auto">
              {(definitions || []).length === 0
                ? 'No AI Employee definitions are published yet. Have SimpliMeta publish a definition first, then come back.'
                : 'Every published AI Employee already has an assignment on this tenant. If a previous grant was revoked, reactivate it from its row instead of creating a new one.'}
            </p>
            <div className="flex items-center justify-end mt-4">
              <button
                type="button"
                onClick={onClose}
                className="btn-secondary"
              >
                Close
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Definition picker — list of clickable cards */}
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
                AI Employee *
              </label>
              <div
                className="space-y-2 max-h-[280px] overflow-y-auto pr-1"
                data-testid="definition-picker"
              >
                {availableDefinitions.map((d) => {
                  const isSelected = definitionId === d.definition_id;
                  return (
                    <DefinitionCard
                      key={d.definition_id}
                      definition={d}
                      isSelected={isSelected}
                      onClick={() => setDefinitionId(d.definition_id)}
                      disabled={submitting}
                    />
                  );
                })}
              </div>
              {errors.definitionId && (
                <p className="text-xs text-red-600 dark:text-red-400 mt-1.5">
                  {errors.definitionId}
                </p>
              )}
            </div>

            {/* Selected definition's defaults — informational */}
            {selectedDefinition && (
              <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-3 text-xs">
                <div className="text-aegisaxiom-text-muted uppercase tracking-wider text-[10px] mb-1.5">
                  Defaults from definition
                </div>
                <div className="space-y-1">
                  <div className="flex gap-2">
                    <span className="text-aegisaxiom-text-muted shrink-0 w-20">Model</span>
                    <span className="text-aegisaxiom-text">
                      {selectedDefinition.default_model_name || '—'}
                      {selectedDefinition.default_model_provider && (
                        <span className="text-aegisaxiom-text-muted ml-1.5">
                          ({selectedDefinition.default_model_provider})
                        </span>
                      )}
                    </span>
                  </div>
                  {Array.isArray(selectedDefinition.default_capabilities) &&
                    selectedDefinition.default_capabilities.length > 0 && (
                      <div className="flex gap-2">
                        <span className="text-aegisaxiom-text-muted shrink-0 w-20">
                          Capabilities
                        </span>
                        <span className="text-aegisaxiom-text">
                          {selectedDefinition.default_capabilities.join(', ')}
                        </span>
                      </div>
                    )}
                </div>
              </div>
            )}

            {/* Max instances */}
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                Max instances *
              </label>
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  min="1"
                  max="10000"
                  value={unlimited ? '' : maxInstances}
                  onChange={(e) => setMaxInstances(e.target.value)}
                  disabled={submitting || unlimited}
                  className="input-field flex-1 disabled:opacity-50"
                  placeholder={unlimited ? 'Unlimited' : 'e.g. 10'}
                  data-testid="grant-max-instances"
                />
                <label className="inline-flex items-center gap-1.5 px-3 py-2.5 rounded-lg border border-aegisaxiom-border cursor-pointer hover:border-aegisaxiom-border-hover transition text-sm text-aegisaxiom-text-secondary shrink-0 select-none">
                  <input
                    type="checkbox"
                    checked={unlimited}
                    onChange={(e) => setUnlimited(e.target.checked)}
                    disabled={submitting}
                    className="accent-violet-600"
                    data-testid="grant-unlimited-toggle"
                  />
                  <InfinityIcon size={14} />
                  Unlimited
                </label>
              </div>
              {errors.maxInstances ? (
                <p className="text-xs text-red-600 dark:text-red-400 mt-1">
                  {errors.maxInstances}
                </p>
              ) : (
                <p className="text-xs text-aegisaxiom-text-muted mt-1">
                  How many parallel instances of this AI Employee {tenantName} can
                  provision.
                </p>
              )}
            </div>

            {/* Notes (optional) */}
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                Notes <span className="text-aegisaxiom-text-muted">(optional)</span>
              </label>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                disabled={submitting}
                className="input-field resize-none"
                rows={2}
                placeholder="Internal note, e.g. contract reference or rationale"
                maxLength={500}
                data-testid="grant-notes"
              />
              <p className="text-xs text-aegisaxiom-text-muted mt-1">
                Visible only to SimpliMeta admins.
              </p>
            </div>

            {/* Footer */}
            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={onClose}
                disabled={submitting}
                className="btn-secondary disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting}
                className="btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
                data-testid="grant-submit"
              >
                {submitting ? 'Granting…' : 'Grant assignment'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

// ─── Internal: definition card ──────────────────────────────────────

/**
 * One row in the definition picker. Click to select. The card visually
 * indicates selection with a violet border + check mark.
 */
function DefinitionCard({ definition, isSelected, onClick, disabled }) {
  const isN8n = definition.type === 'n8n';
  const Icon = isN8n ? Workflow : Bot;
  const avatarClass = isN8n
    ? 'bg-indigo-100 dark:bg-indigo-600/20 text-indigo-700 dark:text-indigo-300'
    : 'bg-violet-100 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300';
  const typeLabel = isN8n ? 'n8n' : 'Conversational';

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      data-testid={`definition-option-${definition.definition_id}`}
      className={
        'w-full text-left flex items-center gap-3 p-3 rounded-lg border transition disabled:opacity-50 disabled:cursor-not-allowed ' +
        (isSelected
          ? 'border-violet-400 dark:border-violet-500/50 bg-violet-50 dark:bg-violet-600/10'
          : 'border-aegisaxiom-border hover:border-aegisaxiom-border-hover bg-aegisaxiom-base')
      }
    >
      <div
        className={`w-9 h-9 rounded-md flex items-center justify-center shrink-0 ${avatarClass}`}
      >
        {definition.default_avatar ? (
          <span className="text-base">{definition.default_avatar}</span>
        ) : (
          <Icon size={18} />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="text-sm font-medium text-aegisaxiom-text truncate">
            {definition.name}
          </span>
          <span className="inline-flex text-[10px] uppercase tracking-wider font-medium px-1.5 py-0.5 rounded bg-aegisaxiom-elevated text-aegisaxiom-text-muted shrink-0">
            {typeLabel}
          </span>
        </div>
        {definition.role && (
          <p className="text-xs text-aegisaxiom-text-secondary mt-0.5 truncate">
            {definition.role}
          </p>
        )}
      </div>
      {isSelected && (
        <Check
          size={16}
          className="text-violet-600 dark:text-violet-400 shrink-0"
        />
      )}
    </button>
  );
}
