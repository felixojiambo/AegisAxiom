import React, { useEffect, useState, useRef } from 'react';
import { X, Shield, Loader2, AlertCircle, ChevronDown, ChevronRight } from 'lucide-react';
import { api } from '../../lib/api';
import API_URL from '../../lib/apiConfig';

const API = API_URL;

/**
 * AIAccessSummary — side panel showing an AI employee's app_access
 * grouped by app. Fetches GET /api/ai-employees/{employeeId}/access-summary.
 *
 * Props:
 *   employeeId  string | null — when null/undefined the panel renders nothing
 *   employeeName string       — display name for the header
 *   onClose     () => void
 */
export default function AIAccessSummary({ employeeId, employeeName, onClose }) {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [expandedApps, setExpandedApps] = useState({});
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  // Escape key to close
  useEffect(() => {
    if (!employeeId) return;
    const onKeyDown = (e) => {
      if (e.key === 'Escape') onCloseRef.current?.();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [employeeId]);

  useEffect(() => {
    if (!employeeId) return;

    let cancelled = false;
    setLoading(true);
    setError(null);
    setSummary(null);
    setExpandedApps({});

    api
      .get(`${API}/api/ai-employees/${employeeId}/access-summary`)
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body?.detail || `Error ${res.status}`);
        }
        return res.json();
      })
      .then((data) => {
        if (cancelled) return;
        setSummary(data);
        if (data?.by_app?.length <= 4) {
          const expanded = {};
          data.by_app.forEach((a) => {
            expanded[a.app_id] = true;
          });
          setExpandedApps(expanded);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || 'Failed to load access summary');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [employeeId]);

  if (!employeeId) return null;

  const toggleApp = (appId) => {
    setExpandedApps((prev) => ({ ...prev, [appId]: !prev[appId] }));
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/40 z-40"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Panel */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Access summary for ${employeeName || 'AI employee'}`}
        className="fixed top-0 right-0 h-full w-full sm:w-[420px] bg-aegisaxiom-surface border-l border-aegisaxiom-border z-50 flex flex-col shadow-2xl"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-aegisaxiom-border shrink-0">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="p-1.5 bg-violet-500/10 rounded-lg shrink-0">
              <Shield className="w-4 h-4 text-violet-500" />
            </div>
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-aegisaxiom-text truncate">
                Access Summary
              </h2>
              {employeeName && (
                <p className="text-xs text-aegisaxiom-text-muted truncate">{employeeName}</p>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close access summary"
            className="w-8 h-8 rounded-full bg-aegisaxiom-elevated hover:bg-aegisaxiom-base flex items-center justify-center transition shrink-0"
          >
            <X className="w-4 h-4 text-aegisaxiom-text-secondary" />
          </button>
        </div>

        {/* Summary totals */}
        {summary && !loading && (
          <div className="flex gap-4 px-5 py-3 bg-aegisaxiom-elevated border-b border-aegisaxiom-border shrink-0">
            <div>
              <p className="text-[11px] text-aegisaxiom-text-muted uppercase tracking-wider">Apps</p>
              <p className="text-xl font-bold text-aegisaxiom-text">{summary.by_app?.length ?? 0}</p>
            </div>
            <div className="w-px bg-aegisaxiom-border" />
            <div>
              <p className="text-[11px] text-aegisaxiom-text-muted uppercase tracking-wider">
                Total Permissions
              </p>
              <p className="text-xl font-bold text-violet-500">{summary.total_permissions ?? 0}</p>
            </div>
          </div>
        )}

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading && (
            <div className="flex flex-col items-center justify-center py-16 gap-3">
              <Loader2 className="w-6 h-6 text-violet-500 animate-spin" />
              <p className="text-sm text-aegisaxiom-text-muted">Loading access data…</p>
            </div>
          )}

          {!loading && error && (
            <div className="flex items-start gap-2.5 bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/30 rounded-lg p-4">
              <AlertCircle className="w-4 h-4 text-red-500 shrink-0 mt-0.5" />
              <p className="text-sm text-red-700 dark:text-red-300">{error}</p>
            </div>
          )}

          {!loading && !error && summary && summary.by_app?.length === 0 && (
            <div className="text-center py-16">
              <Shield className="w-10 h-10 text-aegisaxiom-text-muted/40 mx-auto mb-3" />
              <p className="text-sm font-medium text-aegisaxiom-text-secondary">No app access configured</p>
              <p className="text-xs text-aegisaxiom-text-muted mt-1">
                This employee has no app_access entries yet.
              </p>
            </div>
          )}

          {!loading && !error && summary?.by_app?.length > 0 && (
            <div className="space-y-2">
              {summary.by_app.map((app) => {
                const isOpen = expandedApps[app.app_id] ?? false;
                return (
                  <div
                    key={app.app_id}
                    className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg overflow-hidden"
                  >
                    <button
                      onClick={() => toggleApp(app.app_id)}
                      className="w-full flex items-center justify-between px-4 py-3 hover:bg-aegisaxiom-base transition text-left"
                      aria-expanded={isOpen}
                    >
                      <div className="flex items-center gap-2.5 min-w-0">
                        {isOpen ? (
                          <ChevronDown className="w-3.5 h-3.5 text-aegisaxiom-text-muted shrink-0" />
                        ) : (
                          <ChevronRight className="w-3.5 h-3.5 text-aegisaxiom-text-muted shrink-0" />
                        )}
                        <span className="text-sm font-medium text-aegisaxiom-text truncate">
                          {app.app_name || app.app_slug || app.app_id}
                        </span>
                      </div>
                      <span className="text-[11px] text-aegisaxiom-text-muted shrink-0 ml-2">
                        {app.permissions?.length ?? 0} perm
                        {(app.permissions?.length ?? 0) !== 1 ? 's' : ''}
                      </span>
                    </button>

                    {isOpen && (
                      <div className="px-4 pb-3 border-t border-aegisaxiom-border">
                        {app.permissions?.length > 0 ? (
                          <ul className="space-y-1 pt-2">
                            {app.permissions.map((perm) => (
                              <li key={perm} className="flex items-center gap-2">
                                <span className="w-1.5 h-1.5 rounded-full bg-violet-400 shrink-0" />
                                <code className="text-[11px] text-aegisaxiom-text-secondary font-mono">
                                  {perm}
                                </code>
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <p className="text-xs text-aegisaxiom-text-muted pt-2 italic">
                            No permissions listed for this app.
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-aegisaxiom-border shrink-0">
          <p className="text-[11px] text-aegisaxiom-text-muted text-center">
            Permissions shown are stored grants — implied permissions (verb-tier) may also apply.
          </p>
        </div>
      </div>
    </>
  );
}
