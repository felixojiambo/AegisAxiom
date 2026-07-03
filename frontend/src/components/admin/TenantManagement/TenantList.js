import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronRight, MoreVertical, Edit, Archive } from 'lucide-react';
import { STATUS_PILL_CLASS, TENANT_TYPE_CONFIG, statusLabel } from './helpers';

/**
 * Phase 1D.2 → 1D.3 — Tenant list presentation.
 *
 * Renders a desktop table at md+ and stacked cards on mobile. Row / card
 * is the drill-down target into the detail page. A per-row kebab menu
 * (1D.3) exposes Edit and Archive actions; both bubble up via callbacks
 * to the parent for state ownership and refresh-on-success.
 *
 * Root tenants don't get a kebab (they're system-managed). Archived
 * tenants get only the Edit option (Archive on an already-archived row
 * is meaningless).
 *
 * Props
 *   tenants          — Tenant[] (already filtered)
 *   parentNameById   — Map<tenant_id, name>
 *   onEdit           — (tenant) => void
 *   onArchive        — (tenant) => void
 */
export default function TenantList({ tenants, parentNameById, onEdit, onArchive }) {
  const navigate = useNavigate();
  const goTo = (tenant) => navigate(`/admin/tenants/${tenant.tenant_id}`);

  return (
    <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl overflow-hidden">
      {/* Desktop table — md+ */}
      <div className="hidden md:block overflow-x-auto">
        <table className="w-full text-sm" data-testid="tenants-table">
          <thead>
            <tr className="border-b border-aegisaxiom-border">
              {['Name', 'Type', 'Parent', 'Status', 'Created'].map((h) => (
                <th
                  key={h}
                  className="text-left py-3 px-4 text-xs font-medium text-aegisaxiom-text-muted uppercase tracking-wider"
                >
                  {h}
                </th>
              ))}
              <th className="w-12" />
            </tr>
          </thead>
          <tbody>
            {tenants.map((t) => {
              const typeCfg =
                TENANT_TYPE_CONFIG[t.tenant_type] || TENANT_TYPE_CONFIG.account;
              const TypeIcon = typeCfg.icon;
              const parentName = t.parent_tenant_id
                ? parentNameById.get(t.parent_tenant_id) || '—'
                : '—';
              return (
                <tr
                  key={t.tenant_id}
                  onClick={() => goTo(t)}
                  className="border-b border-aegisaxiom-border last:border-b-0 hover:bg-aegisaxiom-accent-muted cursor-pointer transition-colors"
                  data-testid={`tenant-row-${t.tenant_id}`}
                >
                  <td className="py-3 px-4">
                    <div className="font-medium text-aegisaxiom-text">{t.name}</div>
                    <div className="text-[11px] text-aegisaxiom-text-muted font-mono mt-0.5">
                      {t.slug}
                    </div>
                  </td>
                  <td className="py-3 px-4">
                    <span className="inline-flex items-center gap-1.5 text-aegisaxiom-text-secondary">
                      <TypeIcon size={14} />
                      {typeCfg.label}
                    </span>
                  </td>
                  <td className="py-3 px-4 text-aegisaxiom-text-secondary">
                    {parentName}
                  </td>
                  <td className="py-3 px-4">
                    <span
                      className={`inline-flex text-xs px-2 py-0.5 rounded-full w-fit ${STATUS_PILL_CLASS[t.status] || ''}`}
                    >
                      {statusLabel(t.status)}
                    </span>
                  </td>
                  <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs">
                    {formatDate(t.created_at)}
                  </td>
                  <td className="py-3 px-2 text-right">
                    <RowKebab
                      tenant={t}
                      onEdit={onEdit}
                      onArchive={onArchive}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Mobile cards — below md.
        * Note: changed from <button> in 1D.2 to <div role="button"> here
        * because we now host a nested <button> (the kebab) inside the
        * tappable card surface, and button-in-button is invalid HTML. */}
      <div className="md:hidden p-3 space-y-2.5">
        {tenants.map((t) => {
          const typeCfg =
            TENANT_TYPE_CONFIG[t.tenant_type] || TENANT_TYPE_CONFIG.account;
          const TypeIcon = typeCfg.icon;
          const parentName = t.parent_tenant_id
            ? parentNameById.get(t.parent_tenant_id) || null
            : null;
          return (
            <div
              key={t.tenant_id}
              onClick={() => goTo(t)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  goTo(t);
                }
              }}
              role="button"
              tabIndex={0}
              className="w-full text-left bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5 hover:border-aegisaxiom-border-hover cursor-pointer transition focus:outline-none focus:ring-2 focus:ring-violet-500/40"
              data-testid={`tenant-card-${t.tenant_id}`}
            >
              <div className="flex items-start justify-between gap-2 mb-2">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-aegisaxiom-text truncate">
                    {t.name}
                  </p>
                  <p className="text-[11px] text-aegisaxiom-text-muted flex items-center gap-1.5 mt-0.5">
                    <TypeIcon size={11} />
                    {typeCfg.label}
                    <span className="opacity-50">·</span>
                    <span className="font-mono truncate">{t.slug}</span>
                  </p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <span
                    className={`inline-flex text-[10px] px-2 py-0.5 rounded-full ${STATUS_PILL_CLASS[t.status] || ''}`}
                  >
                    {statusLabel(t.status)}
                  </span>
                  <RowKebab
                    tenant={t}
                    onEdit={onEdit}
                    onArchive={onArchive}
                  />
                </div>
              </div>
              <div className="flex items-center justify-between pt-2 mt-1 border-t border-aegisaxiom-border text-[11px] text-aegisaxiom-text-muted">
                <span className="truncate">
                  {parentName ? `Parent: ${parentName}` : 'Direct customer'}
                  <span className="mx-1.5 opacity-60">·</span>
                  {formatDate(t.created_at)}
                </span>
                <ChevronRight
                  size={14}
                  className="shrink-0 ml-2 text-aegisaxiom-text-muted"
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Row kebab menu ────────────────────────────────────────────────

/**
 * Per-row Edit / Archive dropdown. Hidden entirely for root tenants
 * (system-managed). Archive option is hidden on already-archived rows.
 *
 * Click is stopped from propagating to the row so opening the menu
 * doesn't accidentally drill into the detail page.
 */
function RowKebab({ tenant, onEdit, onArchive }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const handleClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const handleEsc = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handleClick);
    document.addEventListener('keydown', handleEsc);
    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleEsc);
    };
  }, [open]);

  if (tenant.tenant_type === 'root') return null;

  const canArchive = tenant.status !== 'archived';

  return (
    <div
      className="relative inline-block"
      ref={ref}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        aria-label="Actions"
        aria-haspopup="menu"
        aria-expanded={open}
        className="p-1.5 rounded hover:bg-aegisaxiom-elevated text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition"
        data-testid={`tenant-kebab-${tenant.tenant_id}`}
      >
        <MoreVertical size={16} />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full mt-1 z-20 min-w-[140px] bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg shadow-lg py-1 animate-fadeInUp"
        >
          <button
            type="button"
            role="menuitem"
            onClick={(e) => {
              e.stopPropagation();
              setOpen(false);
              onEdit(tenant);
            }}
            className="w-full text-left flex items-center gap-2 px-3 py-2 text-sm text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition"
            data-testid={`tenant-edit-${tenant.tenant_id}`}
          >
            <Edit size={14} />
            Edit
          </button>
          {canArchive && (
            <button
              type="button"
              role="menuitem"
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
                onArchive(tenant);
              }}
              className="w-full text-left flex items-center gap-2 px-3 py-2 text-sm text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-600/10 transition"
              data-testid={`tenant-archive-${tenant.tenant_id}`}
            >
              <Archive size={14} />
              Archive
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Helpers ───────────────────────────────────────────────────────

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
