import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { RefreshCw, CheckCircle, XCircle, Activity } from 'lucide-react';
import API_URL from '../../lib/apiConfig';

const API = API_URL;

const SOURCE_COLORS = {
  zoho_crm: 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300',
  qbo: 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300',
  zoho_desk: 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300',
};

export default function WebhookLog() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState('all');

  useEffect(() => {
    load();
  }, []);

  const load = async () => {
    setLoading(true);
    const res = await api.get(`${API}/api/integrations/webhook-logs`);
    if (res.ok) setLogs(await res.json());
    setLoading(false);
  };

  const filtered =
    filter === 'all'
      ? logs
      : logs.filter((l) => (filter === 'success' ? l.processed : !l.processed));

  return (
    <div data-testid="webhook-log-page">
      {/* Page header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
        <div>
          <h1 className="page-title flex items-center gap-2">
            <Activity size={22} className="text-violet-600 dark:text-violet-400" />
            Webhook Activity
          </h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">{logs.length} recent events</p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="btn-secondary flex items-center justify-center gap-2 w-full sm:w-auto shrink-0"
          data-testid="refresh-logs-btn"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2 mb-6">
        {['all', 'success', 'error'].map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            data-testid={`filter-${f}`}
            className={`text-xs px-3 py-1.5 rounded-lg capitalize transition-all ${
              filter === f
                ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-200 dark:border-violet-600/30'
                : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary hover:text-aegisaxiom-text border border-aegisaxiom-border'
            }`}
          >
            {f}
          </button>
        ))}
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-3 sm:gap-4 md:gap-6 mb-8 sm:mb-10">
        <div className="glass-card p-3 sm:p-5 md:p-6 text-center">
          <p
            className="text-xl sm:text-2xl md:text-3xl font-bold text-aegisaxiom-text leading-none"
            style={{ fontFamily: 'Manrope, sans-serif' }}
          >
            {logs.length}
          </p>
          <p className="text-aegisaxiom-text-secondary text-[11px] sm:text-xs md:text-sm mt-1.5 sm:mt-2 leading-tight">
            Total Events
          </p>
        </div>
        <div className="glass-card p-3 sm:p-5 md:p-6 text-center">
          <p
            className="text-xl sm:text-2xl md:text-3xl font-bold text-emerald-600 dark:text-emerald-400 leading-none"
            style={{ fontFamily: 'Manrope, sans-serif' }}
          >
            {logs.filter((l) => l.processed).length}
          </p>
          <p className="text-aegisaxiom-text-secondary text-[11px] sm:text-xs md:text-sm mt-1.5 sm:mt-2 leading-tight">
            Processed
          </p>
        </div>
        <div className="glass-card p-3 sm:p-5 md:p-6 text-center">
          <p
            className="text-xl sm:text-2xl md:text-3xl font-bold text-red-600 dark:text-red-400 leading-none"
            style={{ fontFamily: 'Manrope, sans-serif' }}
          >
            {logs.filter((l) => !l.processed).length}
          </p>
          <p className="text-aegisaxiom-text-secondary text-[11px] sm:text-xs md:text-sm mt-1.5 sm:mt-2 leading-tight">
            Failed
          </p>
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="glass-card p-12 sm:p-16 text-center">
          <Activity size={32} className="text-aegisaxiom-text-muted mx-auto mb-3" />
          <p className="text-aegisaxiom-text-secondary font-medium mb-1">
            {filter === 'all'
              ? 'No webhook events yet'
              : filter === 'success'
                ? 'No successful events'
                : 'No failed events'}
          </p>
          <p className="text-aegisaxiom-text-muted text-xs mt-1 max-w-sm mx-auto leading-relaxed">
            {filter === 'all'
              ? 'Events will appear here when Zoho or QuickBooks send webhook notifications.'
              : 'Try a different filter, or wait for new events to come in.'}
          </p>
        </div>
      ) : (
        <div className="glass-card overflow-hidden">
          {/* Desktop table */}
          <div className="hidden md:block overflow-x-auto">
            <table className="w-full text-sm" data-testid="webhook-logs-table">
              <thead>
                <tr className="border-b border-aegisaxiom-border">
                  {['Source', 'Event Type', 'Status', 'Timestamp', 'Error'].map((h) => (
                    <th
                      key={h}
                      className="text-left py-3 px-4 text-xs font-medium text-aegisaxiom-text-muted uppercase tracking-wider"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((log) => (
                  <tr
                    key={log.log_id}
                    className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted transition-colors"
                    data-testid={`log-row-${log.log_id}`}
                  >
                    <td className="py-3 px-4">
                      <span
                        className={`badge-stage ${SOURCE_COLORS[log.source] || 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border'}`}
                      >
                        {log.source}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs font-mono">
                      {log.event_type}
                    </td>
                    <td className="py-3 px-4">
                      {log.processed ? (
                        <div className="flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400">
                          <CheckCircle size={13} />
                          <span className="text-xs">OK</span>
                        </div>
                      ) : (
                        <div className="flex items-center gap-1.5 text-red-600 dark:text-red-400">
                          <XCircle size={13} />
                          <span className="text-xs">Failed</span>
                        </div>
                      )}
                    </td>
                    <td className="py-3 px-4 text-aegisaxiom-text-muted text-xs">
                      {new Date(log.created_at).toLocaleString()}
                    </td>
                    <td className="py-3 px-4 text-red-600 dark:text-red-400 text-xs max-w-xs truncate">
                      {log.error || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile card list */}
          <div className="md:hidden divide-y divide-aegisaxiom-border">
            {filtered.map((log) => (
              <div key={log.log_id} className="p-3.5" data-testid={`log-card-${log.log_id}`}>
                {/* Row 1: source + status */}
                <div className="flex items-center justify-between gap-2 mb-2">
                  <span
                    className={`badge-stage ${SOURCE_COLORS[log.source] || 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border'} shrink-0`}
                  >
                    {log.source}
                  </span>
                  {log.processed ? (
                    <div className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400 shrink-0">
                      <CheckCircle size={12} />
                      <span className="text-[11px] font-medium">OK</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1 text-red-600 dark:text-red-400 shrink-0">
                      <XCircle size={12} />
                      <span className="text-[11px] font-medium">Failed</span>
                    </div>
                  )}
                </div>

                {/* Row 2: event type */}
                <p className="text-xs font-mono text-aegisaxiom-text break-all leading-snug mb-1.5">
                  {log.event_type}
                </p>

                {/* Row 3: timestamp */}
                <p className="text-[11px] text-aegisaxiom-text-muted">
                  {new Date(log.created_at).toLocaleString()}
                </p>

                {/* Error block when failed */}
                {log.error && (
                  <div className="mt-2.5 pt-2.5 border-t border-aegisaxiom-border">
                    <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-1">
                      Error
                    </p>
                    <p className="text-xs text-red-600 dark:text-red-400 break-words leading-snug">
                      {log.error}
                    </p>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
