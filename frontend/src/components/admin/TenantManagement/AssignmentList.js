import React, { useState, useEffect, useRef } from 'react';
import { Bot, Workflow, Plus, MoreVertical, List, Ban, RotateCcw } from 'lucide-react';
import { STATUS_PILL_CLASS, statusLabel, formatMaxInstances } from './helpers';

/**
 * Phase 1D.4 → 1D.7 → 1D.9 — Assignment list presentation.
 *
 * Each row shows:
 *   • Definition cell (avatar + name + type pill)
 *   • Status pill
 *   • "count / max instances" (clickable in 1D.7 — opens InstanceListModal)
 *   • Granted by + date
 *   • Provision button (1D.6)
 *   • Kebab menu (1D.7, extended 1D.9) with:
 *       - Active/suspended → "View instances", "Revoke assignment"
 *       - Revoked          → "View instances", "Reactivate assignment"
 *
 * Provision button visibility rules (unchanged from 1D.6):
 *   • Revoked → hidden
 *   • Suspended → disabled with tooltip
 *   • Cap reached → disabled with tooltip
 *   • Otherwise → enabled
 *
 * Kebab menu rules:
 *   • Active or suspended → "View instances", "Revoke assignment"
 *   • Revoked             → "View instances", "Reactivate assignment" (1D.9)
 *   • All actions emit callbacks; parent decides modal flow.
 *
 * Props
 *   assignments                — Assignment[]
 *   definitionsById            — Map<definition_id, Definition>
 *   instanceCountsByAssignment — Map<assignment_id, number>
 *   onProvision                — (assignment, definition, currentCount) => void
 *   onViewInstances            — (assignment, definition) => void
 *   onRevoke                   — (assignment, definition, currentCount) => void
 *   onReactivate               — (assignment, definition) => void  [1D.9]
 */
export default function AssignmentList({
  assignments,
  definitionsById,
  instanceCountsByAssignment,
  onProvision,
  onViewInstances,
  onRevoke,
  onReactivate,
}) {
  return (
    <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl overflow-hidden">
      {/* Desktop table — md+ */}
      <div className="hidden md:block overflow-x-auto">
        <table className="w-full text-sm" data-testid="assignments-table">
          <thead>
            <tr className="border-b border-aegisaxiom-border">
              {['Definition', 'Status', 'Instances', 'Granted by'].map((h) => (
                <th
                  key={h}
                  className="text-left py-3 px-4 text-xs font-medium text-aegisaxiom-text-muted uppercase tracking-wider"
                >
                  {h}
                </th>
              ))}
              <th className="w-40" />
            </tr>
          </thead>
          <tbody>
            {assignments.map((a) => {
              const def = definitionsById.get(a.definition_id);
              const count = instanceCountsByAssignment.get(a.assignment_id) || 0;
              const max = formatMaxInstances(a.max_instances);
              const isRevoked = a.status === 'revoked';
              const provisionState = computeProvisionState(a, count);
              return (
                <tr
                  key={a.assignment_id}
                  className={
                    'border-b border-aegisaxiom-border last:border-b-0 transition-colors ' +
                    (isRevoked ? 'opacity-55' : '')
                  }
                  data-testid={`assignment-row-${a.assignment_id}`}
                >
                  <td className="py-3 px-4">
                    <DefinitionCell definition={def} fallbackId={a.definition_id} />
                  </td>
                  <td className="py-3 px-4">
                    <span
                      className={`inline-flex text-xs px-2 py-0.5 rounded-full w-fit ${STATUS_PILL_CLASS[a.status] || ''}`}
                    >
                      {statusLabel(a.status)}
                    </span>
                  </td>
                  <td className="py-3 px-4">
                    <InstanceCountButton
                      count={count}
                      maxLabel={max}
                      onClick={() => onViewInstances(a, def)}
                    />
                  </td>
                  <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                    SimpliMeta
                    <div className="text-[11px] text-aegisaxiom-text-muted mt-0.5">
                      {isRevoked
                        ? `Revoked ${formatDate(a.revoked_at)}`
                        : formatDate(a.granted_at)}
                    </div>
                  </td>
                  <td className="py-3 px-2 text-right">
                    <div className="inline-flex items-center gap-1.5">
                      {provisionState.show && (
                        <ProvisionButton
                          state={provisionState}
                          onClick={() => onProvision(a, def, count)}
                        />
                      )}
                      <KebabMenu
                        assignment={a}
                        definition={def}
                        currentCount={count}
                        onViewInstances={() => onViewInstances(a, def)}
                        onRevoke={() => onRevoke(a, def, count)}
                        onReactivate={() => onReactivate(a, def)}
                      />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Mobile cards — below md */}
      <div className="md:hidden p-3 space-y-2.5">
        {assignments.map((a) => {
          const def = definitionsById.get(a.definition_id);
          const count = instanceCountsByAssignment.get(a.assignment_id) || 0;
          const max = formatMaxInstances(a.max_instances);
          const isRevoked = a.status === 'revoked';
          const provisionState = computeProvisionState(a, count);
          return (
            <div
              key={a.assignment_id}
              className={
                'bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5 transition ' +
                (isRevoked ? 'opacity-55' : '')
              }
              data-testid={`assignment-card-${a.assignment_id}`}
            >
              <div className="flex items-center gap-3 mb-3">
                <DefinitionCell definition={def} fallbackId={a.definition_id} />
                <span
                  className={`inline-flex text-[10px] px-2 py-0.5 rounded-full shrink-0 ml-auto ${STATUS_PILL_CLASS[a.status] || ''}`}
                >
                  {statusLabel(a.status)}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-3 text-xs mb-3">
                <div>
                  <div className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                    Instances
                  </div>
                  <InstanceCountButton
                    count={count}
                    maxLabel={max}
                    onClick={() => onViewInstances(a, def)}
                    compact
                  />
                </div>
                <div>
                  <div className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                    Granted by
                  </div>
                  <div className="text-aegisaxiom-text-secondary">
                    SimpliMeta
                    <div className="text-[10px] text-aegisaxiom-text-muted mt-0.5">
                      {isRevoked
                        ? `Revoked ${formatDate(a.revoked_at)}`
                        : formatDate(a.granted_at)}
                    </div>
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {provisionState.show && (
                  <ProvisionButton
                    state={provisionState}
                    fullWidth
                    onClick={() => onProvision(a, def, count)}
                  />
                )}
                <KebabMenu
                  assignment={a}
                  definition={def}
                  currentCount={count}
                  onViewInstances={() => onViewInstances(a, def)}
                  onRevoke={() => onRevoke(a, def, count)}
                  onReactivate={() => onReactivate(a, def)}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Internal: clickable instance count ─────────────────────────────

/**
 * Renders the "X / Y instances" cell as a button that opens the
 * instance drill-down. Visual treatment is subtle (just a hover state +
 * focus ring) so it doesn't compete with the Provision button for
 * attention. Compact mode is for the mobile-card layout where vertical
 * space matters.
 */
function InstanceCountButton({ count, maxLabel, onClick, compact }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        'inline-flex items-center gap-1 rounded transition hover:bg-aegisaxiom-elevated focus:outline-none focus:ring-2 focus:ring-violet-500 focus:ring-offset-2 focus:ring-offset-aegisaxiom-surface text-left ' +
        (compact ? 'px-0 py-0' : 'px-2 py-1 -mx-2')
      }
      data-testid="instance-count-button"
      title="View instances"
    >
      <span className="text-sm font-medium text-aegisaxiom-text tabular-nums">
        {count} / {maxLabel}
      </span>
      {!compact && (
        <span className="text-[11px] text-aegisaxiom-text-muted ml-1.5">instances</span>
      )}
    </button>
  );
}

// ─── Internal: provision button state ──────────────────────────────

function computeProvisionState(assignment, currentCount) {
  if (assignment.status === 'revoked') {
    return { show: false };
  }
  if (assignment.status === 'suspended') {
    return {
      show: true,
      disabled: true,
      reason: 'Assignment is suspended. Reactivate it via Edit before provisioning.',
    };
  }
  const max = assignment.max_instances;
  if (max !== null && max !== undefined && currentCount >= max) {
    return {
      show: true,
      disabled: true,
      reason: `Cap reached (${currentCount}/${max}). Revoke an instance first or grant more capacity.`,
    };
  }
  return { show: true, disabled: false };
}

function ProvisionButton({ state, onClick, fullWidth }) {
  const base =
    'inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition ';
  const enabled =
    'bg-violet-600 hover:bg-violet-500 text-white shadow-sm cursor-pointer';
  const disabled =
    'bg-aegisaxiom-elevated text-aegisaxiom-text-muted border border-aegisaxiom-border cursor-not-allowed';
  return (
    <button
      type="button"
      onClick={state.disabled ? undefined : onClick}
      disabled={state.disabled}
      title={state.disabled ? state.reason : undefined}
      className={
        base + (state.disabled ? disabled : enabled) + (fullWidth ? ' w-full' : '')
      }
      data-testid="provision-button"
    >
      <Plus size={12} />
      Provision
    </button>
  );
}

// ─── Internal: kebab menu ──────────────────────────────────────────

/**
 * Three-dot menu with row-level actions. Manages its own open/closed
 * state plus click-outside dismissal.
 *
 * Menu items by status:
 *   • Active/suspended → "View instances", "Revoke assignment"
 *   • Revoked          → "View instances", "Reactivate assignment" (1D.9)
 *
 * If there are zero instances on a revoked row the user just gets the
 * empty state in the drill-down modal, which is fine.
 */
function KebabMenu({ assignment, onViewInstances, onRevoke, onReactivate }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const handleClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const handleKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handleClick);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleKey);
    };
  }, [open]);

  const isRevoked = assignment.status === 'revoked';

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="p-1.5 rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:border-aegisaxiom-border-hover transition"
        aria-label="More actions"
        aria-expanded={open}
        data-testid={`assignment-kebab-${assignment.assignment_id}`}
      >
        <MoreVertical size={14} />
      </button>
      {open && (
        <div
          className="absolute right-0 top-full mt-1 z-50 min-w-[180px] bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg shadow-lg py-1 animate-fadeIn"
          role="menu"
        >
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onViewInstances();
            }}
            className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition text-left"
            role="menuitem"
            data-testid="kebab-view-instances"
          >
            <List size={13} className="text-aegisaxiom-text-secondary" />
            View instances
          </button>
          {!isRevoked && (
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                onRevoke();
              }}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-600/10 transition text-left"
              role="menuitem"
              data-testid="kebab-revoke"
            >
              <Ban size={13} />
              Revoke assignment
            </button>
          )}
          {isRevoked && (
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                onReactivate();
              }}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-emerald-700 dark:text-emerald-400 hover:bg-emerald-50 dark:hover:bg-emerald-600/10 transition text-left"
              role="menuitem"
              data-testid="kebab-reactivate"
            >
              <RotateCcw size={13} />
              Reactivate assignment
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Internal: definition cell renderer ─────────────────────────────

function DefinitionCell({ definition, fallbackId }) {
  if (!definition) {
    return (
      <div className="flex items-center gap-3 min-w-0">
        <div className="w-8 h-8 rounded-md bg-aegisaxiom-elevated flex items-center justify-center shrink-0">
          <Bot size={16} className="text-aegisaxiom-text-muted" />
        </div>
        <div className="min-w-0">
          <div className="font-medium text-aegisaxiom-text-muted truncate">
            Unknown definition
          </div>
          <div className="text-[11px] text-aegisaxiom-text-muted font-mono mt-0.5 truncate">
            {fallbackId}
          </div>
        </div>
      </div>
    );
  }

  const isN8n = definition.type === 'n8n';
  const Icon = isN8n ? Workflow : Bot;
  const avatarClass = isN8n
    ? 'bg-indigo-100 dark:bg-indigo-600/20 text-indigo-700 dark:text-indigo-300'
    : 'bg-violet-100 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300';
  const typeLabel = isN8n ? 'n8n' : 'Conversational';

  return (
    <div className="flex items-center gap-3 min-w-0">
      <div
        className={`w-8 h-8 rounded-md flex items-center justify-center shrink-0 ${avatarClass}`}
      >
        {definition.default_avatar ? (
          <span className="text-sm">{definition.default_avatar}</span>
        ) : (
          <Icon size={16} />
        )}
      </div>
      <div className="min-w-0">
        <div className="font-medium text-aegisaxiom-text truncate">{definition.name}</div>
        <div className="mt-0.5">
          <span className="inline-flex text-[10px] uppercase tracking-wider font-medium px-1.5 py-0.5 rounded bg-aegisaxiom-elevated text-aegisaxiom-text-muted">
            {typeLabel}
          </span>
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
});

function formatDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return dateFmt.format(d);
}
