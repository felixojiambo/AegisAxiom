import React, { useEffect, useState, useMemo, useRef } from 'react';
import { X, ChevronRight, Shield, AlertCircle } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '../../lib/apiConfig';

const API = API_URL;

// Roles with this many or more explicit perms are treated as wildcard-equivalent
// in the preview UI to avoid rendering an unwieldy list. Tuned to be safely above
// the largest non-wildcard role (director ~60 perms) with comfortable headroom.
const WILDCARD_PERM_THRESHOLD = 200;

// Derive a human-readable group label from a perm key prefix.
// e.g. "crm.contacts.edit.all" -> "crm.contacts"
function prefixOf(key) {
  const parts = key.split('.');
  // Keep up to 2 segments as the group label; more granular would be noise.
  return parts.slice(0, 2).join('.');
}

// Group an array of perm strings into { prefix: [key, ...] }.
function groupPermKeys(keys) {
  return keys.reduce((acc, key) => {
    const prefix = prefixOf(key);
    if (!acc[prefix]) acc[prefix] = [];
    acc[prefix].push(key);
    return acc;
  }, {});
}

// Single accordion row for one perm-prefix group.
function PermGroup({ prefix, keys, impliedSet }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border border-aegisaxiom-border rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center justify-between w-full px-3 py-2 bg-aegisaxiom-surface hover:bg-aegisaxiom-accent-muted transition-colors text-left"
      >
        <div className="flex items-center gap-2">
          <ChevronRight
            size={12}
            className={`text-aegisaxiom-text-muted transition-transform ${open ? 'rotate-90' : ''}`}
          />
          <span className="text-xs font-semibold text-aegisaxiom-text-secondary font-mono">
            {prefix}.*
          </span>
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-muted">
            {keys.length}
          </span>
        </div>
      </button>

      {open && (
        <div className="grid grid-cols-1 gap-1 p-2 bg-aegisaxiom-base/30">
          {keys.map((key) => {
            const isImplied = impliedSet.has(key);
            return (
              <div
                key={key}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs bg-aegisaxiom-surface border border-aegisaxiom-border text-aegisaxiom-text-secondary"
              >
                <div className="w-1.5 h-1.5 rounded-full bg-violet-500 shrink-0" />
                <span className="flex-1 font-mono">{key}</span>
                {isImplied && (
                  <span className="text-[10px] text-aegisaxiom-text-muted italic shrink-0">
                    (implied)
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Loading spinner — matches the inline pattern used in other admin components.
function Spinner() {
  return (
    <div className="flex items-center justify-center py-12">
      <div className="w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
    </div>
  );
}

export default function RolePreviewModal({ roleId, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // Default to "effective" — it's the more useful view.
  const [tab, setTab] = useState('effective');

  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  // Escape key to close
  useEffect(() => {
    if (!roleId) return;
    const onKeyDown = (e) => {
      if (e.key === 'Escape') onCloseRef.current?.();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [roleId]);

  useEffect(() => {
    if (!roleId) return;

    let cancelled = false;
    setData(null);
    setError(null);
    setLoading(true);

    api
      .get(`${API}/api/roles/${roleId}/preview`)
      .then(async (res) => {
        if (cancelled) return;
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          const msg = body?.detail || `Error ${res.status}`;
          setError(msg);
          toast.error(msg);
        } else {
          setData(await res.json());
        }
      })
      .catch((err) => {
        if (cancelled) return;
        const msg = err?.message || 'Network error — could not load role preview.';
        setError(msg);
        toast.error(msg);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [roleId]);

  // Derived perm sets for the two tabs.
  const { explicitKeys, effectiveKeys, impliedSet, isWildcard } = useMemo(() => {
    if (!data) return { explicitKeys: [], effectiveKeys: [], impliedSet: new Set(), isWildcard: false };

    const explicit = data.explicit_perms || [];
    const implied = data.implied_perms || [];
    const wildcard =
      explicit.includes('*') || explicit.length > WILDCARD_PERM_THRESHOLD;

    // Effective = explicit ∪ implied (deduplicated, implied tagged)
    const allEffective = [...new Set([...explicit, ...implied])];
    return {
      explicitKeys: explicit,
      effectiveKeys: allEffective,
      impliedSet: new Set(implied),
      isWildcard: wildcard,
    };
  }, [data]);

  const activeKeys = tab === 'stored' ? explicitKeys : effectiveKeys;
  const grouped = useMemo(() => groupPermKeys(activeKeys), [activeKeys]);
  const hasImplied = data?.implied_perms?.length > 0;
  const hasNavOrRoutes =
    (data?.nav_items?.length || 0) > 0 || (data?.accessible_routes?.length || 0) > 0;
  const hasResourceTypes = (data?.visible_resource_types?.length || 0) > 0;

  // Don't render anything when closed.
  if (!roleId) return null;

  return (
    <div
      className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-2xl animate-fadeInUp flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-start justify-between px-6 pt-5 pb-4 border-b border-aegisaxiom-border shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-violet-50 dark:bg-violet-600/20 border border-violet-200 dark:border-violet-600/30 flex items-center justify-center shrink-0">
              <Shield size={18} className="text-violet-600 dark:text-violet-400" />
            </div>
            <div>
              <h2
                className="text-lg font-bold text-aegisaxiom-text leading-tight"
                style={{ fontFamily: 'Manrope, sans-serif' }}
              >
                {data?.role_name
                  ? data.role_name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
                  : 'Role Preview'}
              </h2>
              {data?.role_name && (
                <p className="text-[11px] text-aegisaxiom-text-muted font-mono mt-0.5">
                  {data.role_name}
                </p>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors"
            aria-label="Close preview"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1 px-6 py-4 space-y-5">
          {loading && <Spinner />}

          {error && !loading && (
            <div className="flex flex-col items-center gap-3 py-8 text-center">
              <AlertCircle size={32} className="text-red-400" />
              <p className="text-aegisaxiom-text-secondary text-sm">{error}</p>
              <button onClick={onClose} className="btn-secondary">
                Close
              </button>
            </div>
          )}

          {data && !loading && !error && (
            <>
              {/* Wildcard callout */}
              {isWildcard ? (
                <div className="flex items-center gap-3 p-4 bg-violet-50 dark:bg-violet-600/10 border border-violet-200 dark:border-violet-600/20 rounded-xl">
                  <Shield size={18} className="text-violet-600 dark:text-violet-400 shrink-0" />
                  <div>
                    <p className="text-violet-700 dark:text-violet-300 text-sm font-semibold">
                      All Permissions Granted
                    </p>
                    <p className="text-aegisaxiom-text-secondary text-xs">
                      This role has wildcard (*) access — every permission in the system is
                      available.
                    </p>
                  </div>
                </div>
              ) : (
                <>
                  {/* Stored / Effective tab toggle */}
                  <div>
                    <div className="flex items-center gap-1 p-1 bg-aegisaxiom-base rounded-lg w-fit">
                      {['effective', 'stored'].map((t) => (
                        <button
                          key={t}
                          onClick={() => setTab(t)}
                          className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                            tab === t
                              ? 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm border border-aegisaxiom-border'
                              : 'text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary'
                          }`}
                        >
                          {t === 'effective' ? 'Effective' : 'Stored'}
                        </button>
                      ))}
                    </div>

                    {/* Implication closure note */}
                    {!hasImplied && (
                      <p className="text-[11px] text-aegisaxiom-text-muted mt-2 italic">
                        Implication closure not yet computed — only explicit permissions shown.
                      </p>
                    )}
                  </div>

                  {/* Permissions accordion */}
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <p className="text-sm font-semibold text-aegisaxiom-text">
                        Permissions
                        <span className="ml-2 text-xs text-aegisaxiom-text-muted font-normal">
                          {activeKeys.length} total
                          {tab === 'effective' && hasImplied
                            ? ` (${data.explicit_perms.length} explicit, ${data.implied_perms.length} implied)`
                            : ''}
                        </span>
                      </p>
                    </div>

                    {activeKeys.length === 0 ? (
                      <p className="text-aegisaxiom-text-muted text-xs py-4 text-center">
                        No permissions assigned to this role.
                      </p>
                    ) : (
                      <div className="space-y-1.5 max-h-64 overflow-y-auto pr-0.5">
                        {Object.entries(grouped).map(([prefix, keys]) => (
                          <PermGroup
                            key={prefix}
                            prefix={prefix}
                            keys={keys}
                            impliedSet={impliedSet}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* Resource types */}
              {hasResourceTypes && (
                <div>
                  <p className="text-sm font-semibold text-aegisaxiom-text mb-2">Resource Types</p>
                  <div className="flex flex-wrap gap-1.5">
                    {data.visible_resource_types.map((rt) => (
                      <span
                        key={rt}
                        className="text-[11px] px-2.5 py-1 rounded-full bg-aegisaxiom-base border border-aegisaxiom-border text-aegisaxiom-text-secondary font-mono"
                      >
                        {rt}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Nav items / routes — hidden when both empty */}
              {!hasNavOrRoutes && (
                <p className="text-[11px] text-aegisaxiom-text-muted italic">
                  Page-level preview will be enabled when nav metadata is registered.
                </p>
              )}
              {(data?.nav_items?.length || 0) > 0 && (
                <div>
                  <p className="text-sm font-semibold text-aegisaxiom-text mb-2">Nav Items</p>
                  <div className="flex flex-wrap gap-1.5">
                    {data.nav_items.map((item, i) => (
                      <span
                        key={i}
                        className="text-[11px] px-2.5 py-1 rounded-full bg-aegisaxiom-base border border-aegisaxiom-border text-aegisaxiom-text-secondary"
                      >
                        {typeof item === 'string' ? item : item.label || item.path || JSON.stringify(item)}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {(data?.accessible_routes?.length || 0) > 0 && (
                <div>
                  <p className="text-sm font-semibold text-aegisaxiom-text mb-2">Accessible Routes</p>
                  <div className="space-y-1">
                    {data.accessible_routes.map((route, i) => (
                      <p key={i} className="text-xs text-aegisaxiom-text-secondary font-mono">
                        {typeof route === 'string' ? route : route.path || JSON.stringify(route)}
                      </p>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        {!loading && (
          <div className="px-6 py-4 border-t border-aegisaxiom-border shrink-0 flex justify-end">
            <button onClick={onClose} className="btn-secondary">
              Close
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
