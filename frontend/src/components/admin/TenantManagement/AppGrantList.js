import React, { useState, useEffect, useRef } from 'react';
import { Package, Plus, MoreVertical, Ban, RotateCcw } from 'lucide-react';
import { STATUS_PILL_CLASS, statusLabel } from './helpers';

/**
 * Phase 1E.1 — App grant list presentation.
 *
 * Mirrors AssignmentList.js structurally but stripped of the AI Employee
 * instance machinery. Apps are toggled on/off per tenant — there's no
 * "deploy N copies" concept like AI Employees have, so no instance
 * count cell, no Provision button, and no max_instances column.
 *
 * Each row shows:
 *   • App cell (icon + name + slug pill)
 *   • Status pill
 *   • Granted by + date
 *   • Kebab menu with:
 *       - Active/suspended → "Revoke grant"
 *       - Revoked          → "Reactivate grant"
 *
 * Props
 *   grants         — Grant[]
 *   appsById       — Map<app_id, AppRegistryDoc>
 *   onRevoke       — (grant, app) => void
 *   onReactivate   — (grant, app) => void
 */
export default function AppGrantList({
  grants,
  appsById,
  onRevoke,
  onReactivate,
}) {
  return (
    <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl overflow-hidden">
      {/* Desktop table — md+ */}
      <div className="hidden md:block overflow-x-auto">
        <table className="w-full text-sm" data-testid="app-grants-table">
          <thead>
            <tr className="border-b border-aegisaxiom-border">
              {['App', 'Status', 'Granted by'].map((h) => (
                <th
                  key={h}
                  className="text-left py-3 px-4 text-xs font-medium text-aegisaxiom-text-muted uppercase tracking-wider"
                >
                  {h}
                </th>
              ))}
              <th className="w-20" />
            </tr>
          </thead>
          <tbody>
            {grants.map((g) => {
              const app = appsById.get(g.app_id);
              const isRevoked = g.status === 'revoked';
              return (
                <tr
                  key={g.grant_id}
                  className={
                    'border-b border-aegisaxiom-border last:border-b-0 transition-colors ' +
                    (isRevoked ? 'opacity-55' : '')
                  }
                  data-testid={`app-grant-row-${g.grant_id}`}
                >
                  <td className="py-3 px-4">
                    <AppCell app={app} fallbackId={g.app_id} />
                  </td>
                  <td className="py-3 px-4">
                    <span
                      className={`inline-flex text-xs px-2 py-0.5 rounded-full w-fit ${STATUS_PILL_CLASS[g.status] || ''}`}
                    >
                      {statusLabel(g.status)}
                    </span>
                  </td>
                  <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                    SimpliMeta
                    <div className="text-[11px] text-aegisaxiom-text-muted mt-0.5">
                      {isRevoked
                        ? `Revoked ${formatDate(g.revoked_at)}`
                        : formatDate(g.granted_at)}
                    </div>
                  </td>
                  <td className="py-3 px-2 text-right">
                    <KebabMenu
                      grant={g}
                      onRevoke={() => onRevoke(g, app)}
                      onReactivate={() => onReactivate(g, app)}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Mobile cards — below md */}
      <div className="md:hidden p-3 space-y-2.5">
        {grants.map((g) => {
          const app = appsById.get(g.app_id);
          const isRevoked = g.status === 'revoked';
          return (
            <div
              key={g.grant_id}
              className={
                'bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5 transition ' +
                (isRevoked ? 'opacity-55' : '')
              }
              data-testid={`app-grant-card-${g.grant_id}`}
            >
              <div className="flex items-center gap-3 mb-3">
                <AppCell app={app} fallbackId={g.app_id} />
                <span
                  className={`inline-flex text-[10px] px-2 py-0.5 rounded-full shrink-0 ml-auto ${STATUS_PILL_CLASS[g.status] || ''}`}
                >
                  {statusLabel(g.status)}
                </span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <div>
                  <div className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                    Granted by
                  </div>
                  <div className="text-aegisaxiom-text-secondary">
                    SimpliMeta
                    <div className="text-[10px] text-aegisaxiom-text-muted mt-0.5">
                      {isRevoked
                        ? `Revoked ${formatDate(g.revoked_at)}`
                        : formatDate(g.granted_at)}
                    </div>
                  </div>
                </div>
                <KebabMenu
                  grant={g}
                  onRevoke={() => onRevoke(g, app)}
                  onReactivate={() => onReactivate(g, app)}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Internal: kebab menu ──────────────────────────────────────────

/**
 * Three-dot menu with row-level actions. Manages its own open/closed
 * state plus click-outside dismissal.
 *
 * Menu items by status:
 *   • Active/suspended → "Revoke grant"
 *   • Revoked          → "Reactivate grant"
 */
function KebabMenu({ grant, onRevoke, onReactivate }) {
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

  const isRevoked = grant.status === 'revoked';

  return (
    <div ref={ref} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="p-1.5 rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:border-aegisaxiom-border-hover transition"
        aria-label="More actions"
        aria-expanded={open}
        data-testid={`app-grant-kebab-${grant.grant_id}`}
      >
        <MoreVertical size={14} />
      </button>
      {open && (
        <div
          className="absolute right-0 top-full mt-1 z-50 min-w-[180px] bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg shadow-lg py-1 animate-fadeIn"
          role="menu"
        >
          {!isRevoked && (
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                onRevoke();
              }}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-600/10 transition text-left"
              role="menuitem"
              data-testid="app-grant-kebab-revoke"
            >
              <Ban size={13} />
              Revoke grant
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
              data-testid="app-grant-kebab-reactivate"
            >
              <RotateCcw size={13} />
              Reactivate grant
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Internal: app cell renderer ────────────────────────────────────

function AppCell({ app, fallbackId }) {
  if (!app) {
    return (
      <div className="flex items-center gap-3 min-w-0">
        <div className="w-8 h-8 rounded-md bg-aegisaxiom-elevated flex items-center justify-center shrink-0">
          <Package size={16} className="text-aegisaxiom-text-muted" />
        </div>
        <div className="min-w-0">
          <div className="font-medium text-aegisaxiom-text-muted truncate">
            Unknown app
          </div>
          <div className="text-[11px] text-aegisaxiom-text-muted font-mono mt-0.5 truncate">
            {fallbackId}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3 min-w-0">
      <div className="w-8 h-8 rounded-md flex items-center justify-center shrink-0 bg-blue-100 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300">
        <Package size={16} />
      </div>
      <div className="min-w-0">
        <div className="font-medium text-aegisaxiom-text truncate">{app.name}</div>
        <div className="mt-0.5">
          <span className="inline-flex text-[10px] uppercase tracking-wider font-medium px-1.5 py-0.5 rounded bg-aegisaxiom-elevated text-aegisaxiom-text-muted font-mono">
            {app.slug}
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
