import React, { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, RefreshCw, Loader2 } from 'lucide-react';
import { api, unwrap } from '@/lib/api';
import API_URL from '@/lib/apiConfig';

function formatDate(iso) {
  if (!iso) return '—';
  try {
    return new Intl.DateTimeFormat('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      timeZoneName: 'short',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function modeBadgeClass(mode) {
  if (mode === 'live') return 'bg-green-500/15 text-green-400';
  return 'bg-amber-500/15 text-amber-400';
}

function JsonBlock({ label, data }) {
  if (!data) return null;
  return (
    <div className="mt-2">
      <div className="text-[10px] font-medium text-aegisaxiom-text-muted uppercase tracking-wide mb-1">
        {label}
      </div>
      <pre className="text-[10px] font-mono bg-aegisaxiom-bg border border-aegisaxiom-border rounded p-2 overflow-auto max-h-40 text-aegisaxiom-text-secondary whitespace-pre-wrap break-words">
        {JSON.stringify(data, null, 2)}
      </pre>
    </div>
  );
}

function CallEntry({ call }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border border-aegisaxiom-border rounded-lg overflow-hidden">
      {/* Summary row */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-start gap-3 px-3 py-2.5 text-left hover:bg-aegisaxiom-elevated/50 transition-colors"
      >
        <div className="mt-0.5 shrink-0 text-aegisaxiom-text-muted">
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
        </div>

        <div className="flex-1 min-w-0 space-y-0.5">
          <div className="flex flex-wrap items-center gap-2">
            {/* Mode badge */}
            <span
              className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium ${modeBadgeClass(call.mode)}`}
            >
              {call.mode === 'live' ? 'Live' : 'Dry Run'}
            </span>
            {/* Status code */}
            <span
              className={`text-xs font-mono font-medium ${
                call.response_status >= 200 && call.response_status < 300
                  ? 'text-green-400'
                  : 'text-red-400'
              }`}
            >
              {call.response_status ?? '—'}
            </span>
            {/* Latency */}
            {call.latency_ms != null && (
              <span className="text-[10px] text-aegisaxiom-text-muted">{call.latency_ms} ms</span>
            )}
            {/* Error short-form */}
            {call.error && (
              <span className="text-[10px] text-red-400 truncate max-w-[200px]">{call.error}</span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3 text-[10px] text-aegisaxiom-text-muted">
            <span>{formatDate(call.created_at)}</span>
            {call.actor && <span>by {call.actor}</span>}
            {call.endpoint && (
              <span className="font-mono truncate max-w-[300px]">{call.endpoint}</span>
            )}
          </div>
        </div>
      </button>

      {/* Expanded body */}
      {expanded && (
        <div className="px-3 pb-3 border-t border-aegisaxiom-border bg-aegisaxiom-bg/50 space-y-1">
          <JsonBlock label="Request Body" data={call.request_body} />
          <JsonBlock label="Response Body" data={call.response_body} />
          {call.request_headers_redacted && (
            <JsonBlock label="Request Headers (redacted)" data={call.request_headers_redacted} />
          )}
        </div>
      )}
    </div>
  );
}

/**
 * PushHistorySection — renders the AppDirect call log for a single row.
 * Owned by Thread 3A.
 *
 * Props:
 *   rowId {string} — the row whose calls to load
 */
export default function PushHistorySection({ rowId }) {
  const [calls, setCalls] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchCalls = useCallback(() => {
    if (!rowId) return;
    setLoading(true);
    setError(null);
    api
      .get(`${API_URL}/api/match/rows/${rowId}/appdirect-calls`)
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) {
          setError(data?.detail ?? 'Failed to load push history.');
          setCalls([]);
        } else {
          setCalls(unwrap(data) ?? []);
        }
      })
      .catch((err) =>
        setError(err?.data?.detail || err?.message || 'Failed to load push history.'),
      )
      .finally(() => setLoading(false));
  }, [rowId]);

  useEffect(() => {
    fetchCalls();
  }, [fetchCalls]);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-aegisaxiom-text-muted uppercase tracking-wide">
          Push History
        </span>
        <button
          type="button"
          onClick={fetchCalls}
          disabled={loading}
          className="btn-secondary flex items-center gap-1 px-2 py-0.5 text-xs"
        >
          {loading ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <RefreshCw className="h-3 w-3" />
          )}
          Refresh
        </button>
      </div>

      {error && (
        <div className="text-xs text-red-400">{error}</div>
      )}

      {!loading && !error && calls.length === 0 && (
        <div className="text-xs text-aegisaxiom-text-muted py-3 text-center">
          No AppDirect calls yet.
        </div>
      )}

      {loading && calls.length === 0 && (
        <div className="flex justify-center py-4">
          <Loader2 className="h-4 w-4 animate-spin text-aegisaxiom-text-muted" />
        </div>
      )}

      {/* Calls — backend returns newest-first (sort: created_at desc); render in that order */}
      <div className="space-y-1.5">
        {calls.map((call) => (
          <CallEntry key={call.call_id} call={call} />
        ))}
      </div>
    </div>
  );
}
