import React, { useEffect, useState } from 'react';
import { api } from '../lib/api';
import API_URL from '../lib/apiConfig';
import { Search, ChevronRight, FileText, Clock, CheckCircle, ArrowRight } from 'lucide-react';

const STATUS_LABELS = {
  in_progress: { label: 'In Progress', color: 'text-blue-600 dark:text-blue-400 bg-blue-400/10' },
  ready_for_sow: { label: 'Ready for SOW', color: 'text-green-600 dark:text-green-400 bg-green-400/10' },
  handed_off_to_orion: { label: 'With Orion', color: 'text-violet-600 dark:text-violet-400 bg-violet-400/10' },
  sow_in_revision: { label: 'SOW Revision', color: 'text-yellow-600 dark:text-yellow-400 bg-yellow-400/10' },
  complete: { label: 'Complete', color: 'text-emerald-600 dark:text-emerald-400 bg-emerald-400/10' },
};

function SessionCard({ session, onClick }) {
  const status = STATUS_LABELS[session.status] || STATUS_LABELS.in_progress;
  const callCount = session.calls?.length || 0;
  const workflowCount = session.ai_employee_matrix?.length || 0;

  return (
    <button
      onClick={() => onClick(session)}
      className="w-full text-left p-4 rounded-xl border border-aegisaxiom-border bg-aegisaxiom-elevated hover:border-violet-500/40 hover:bg-aegisaxiom-elevated transition-all group"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <FileText size={14} className="text-violet-600 dark:text-violet-400 shrink-0" />
            <h3 className="text-sm font-semibold text-aegisaxiom-text truncate">
              {session.opportunity_name || session.client_company || 'Untitled Session'}
            </h3>
          </div>
          {session.client_company && session.opportunity_name && (
            <p className="text-xs text-aegisaxiom-text-secondary mb-2 truncate">{session.client_company}</p>
          )}
          <div className="flex items-center gap-3 text-xs text-aegisaxiom-text-muted">
            <span className="flex items-center gap-1">
              <Clock size={11} />
              {callCount} call{callCount !== 1 ? 's' : ''} analyzed
            </span>
            {workflowCount > 0 && (
              <span className="flex items-center gap-1">
                <CheckCircle size={11} />
                {workflowCount} workflow{workflowCount !== 1 ? 's' : ''}
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-col items-end gap-2 shrink-0">
          <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${status.color}`}>
            {status.label}
          </span>
          <ChevronRight size={14} className="text-aegisaxiom-text-muted group-hover:text-violet-600 dark:text-violet-400 transition-colors" />
        </div>
      </div>
    </button>
  );
}

function SessionDetail({ session, onBack }) {
  const status = STATUS_LABELS[session.status] || STATUS_LABELS.in_progress;
  const matrix = session.ai_employee_matrix || [];
  const calls = session.calls || [];

  return (
    <div>
      <button onClick={onBack} className="flex items-center gap-1 text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 mb-4 transition-colors">
        <ArrowRight size={12} className="rotate-180" /> Back to sessions
      </button>

      <div className="rounded-xl border border-aegisaxiom-border bg-aegisaxiom-elevated p-5 mb-4">
        <div className="flex items-start justify-between mb-3">
          <div>
            <h2 className="text-lg font-semibold text-aegisaxiom-text">{session.opportunity_name || 'Untitled'}</h2>
            {session.client_company && <p className="text-sm text-aegisaxiom-text-secondary">{session.client_company}</p>}
          </div>
          <span className={`text-xs font-medium px-2.5 py-1 rounded-full ${status.color}`}>{status.label}</span>
        </div>
        <div className="grid grid-cols-3 gap-4 text-sm">
          <div>
            <span className="text-aegisaxiom-text-muted text-xs">Current Call</span>
            <p className="text-aegisaxiom-text font-medium">{session.current_call_number || 0}</p>
          </div>
          <div>
            <span className="text-aegisaxiom-text-muted text-xs">Workflows</span>
            <p className="text-aegisaxiom-text font-medium">{matrix.length}</p>
          </div>
          {(session.ai_design_documents?.length > 0) && (
            <div>
              <span className="text-aegisaxiom-text-muted text-xs">Design Docs</span>
              <p className="text-aegisaxiom-text font-medium">{session.ai_design_documents.length}</p>
            </div>
          )}
          <div>
            <span className="text-aegisaxiom-text-muted text-xs">Created</span>
            <p className="text-aegisaxiom-text font-medium text-xs">{new Date(session.created_at).toLocaleDateString()}</p>
          </div>
        </div>

        {/* Export Actions */}
        {(session.status === 'complete' || session.status === 'ready_for_sow') && (
          <div className="mt-4 pt-3 border-t border-aegisaxiom-border">
            <p className="text-xs text-aegisaxiom-text-muted mb-2 font-medium uppercase tracking-wider">Export</p>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={async () => {
                  try {
                    const blob = await api.download(`${API_URL}/api/vega-sessions/${session.session_id}/export/mmi?format=csv`);
                    if (blob) {
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      const _cn = (session.client_company || '').replace(/[^a-zA-Z0-9_-]/g, '_').replace(/_+/g, '_') || 'client';
                      a.download = `${_cn}-mmi_matrix-${new Date().toISOString().slice(0, 10)}.csv`;
                      a.click();
                      URL.revokeObjectURL(url);
                    }
                  } catch {}
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:border-violet-500/40 hover:text-aegisaxiom-text transition-all"
              >
                Matrix (CSV)
              </button>
              <button
                onClick={async () => {
                  try {
                    const blob = await api.download(`${API_URL}/api/vega-sessions/${session.session_id}/export/design-docs?format=markdown`);
                    if (blob) {
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      const _cn2 = (session.client_company || '').replace(/[^a-zA-Z0-9_-]/g, '_').replace(/_+/g, '_') || 'client';
                      a.download = `${_cn2}-design_docs-${new Date().toISOString().slice(0, 10)}.md`;
                      a.click();
                      URL.revokeObjectURL(url);
                    }
                  } catch {}
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:border-violet-500/40 hover:text-aegisaxiom-text transition-all"
              >
                Design Docs (MD)
              </button>
              <button
                onClick={async () => {
                  try {
                    const blob = await api.download(`${API_URL}/api/vega-sessions/${session.session_id}/export/package`);
                    if (blob) {
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      const _cn3 = (session.client_company || '').replace(/[^a-zA-Z0-9_-]/g, '_').replace(/_+/g, '_') || 'client';
                      a.download = `${_cn3}-discovery_package-${new Date().toISOString().slice(0, 10)}.zip`;
                      a.click();
                      URL.revokeObjectURL(url);
                    }
                  } catch {}
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-gradient-to-r from-violet-600 to-fuchsia-600 text-aegisaxiom-text font-medium hover:opacity-90 transition-all"
              >
                Full Package (ZIP)
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Calls */}
      {calls.length > 0 && (
        <div className="mb-4">
          <h3 className="text-sm font-semibold text-aegisaxiom-text mb-2">Call History</h3>
          <div className="space-y-2">
            {calls.map((call, i) => (
              <div key={i} className="p-3 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-accent-soft text-sm">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-medium text-aegisaxiom-text">Call {call.call_number}</span>
                  <span className={`text-xs ${call.user_approved ? 'text-green-600 dark:text-green-400' : 'text-yellow-600 dark:text-yellow-400'}`}>
                    {call.user_approved ? 'Approved' : 'Pending'}
                  </span>
                </div>
                {call.analysis?.summary && (
                  <p className="text-xs text-aegisaxiom-text-secondary line-clamp-2">{call.analysis.summary}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Matrix summary */}
      {matrix.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-aegisaxiom-text mb-2">AI Employee Matrix</h3>
          <div className="space-y-2">
            {matrix.map((wf, i) => (
              <div key={i} className="p-3 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-accent-soft text-sm">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-aegisaxiom-text">{wf.workflow_name}</span>
                  {wf.mmi_hypothesis?.score != null && (
                    <span className="text-xs font-mono text-violet-600 dark:text-violet-400">MMI: {wf.mmi_hypothesis.score}</span>
                  )}
                </div>
                {wf.description && <p className="text-xs text-aegisaxiom-text-secondary mt-1 line-clamp-1">{wf.description}</p>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function MyDiscoverySessions() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await api.get(`${API_URL}/api/vega-sessions`);
        if (res.ok) {
          const data = await res.json();
          setSessions(Array.isArray(data) ? data : data?.sessions || []);
        }
      } catch { /* ignore */ }
      setLoading(false);
    };
    load();
  }, []);

  const filtered = sessions.filter(s => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (s.opportunity_name || '').toLowerCase().includes(q)
      || (s.client_company || '').toLowerCase().includes(q);
  });

  if (selected) {
    return (
      <div className="max-w-3xl mx-auto">
        <SessionDetail session={selected} onBack={() => setSelected(null)} />
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto">
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-lg font-semibold text-aegisaxiom-text">My Discovery Sessions</h1>
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search sessions..."
            className="pl-8 pr-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-xs text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted outline-none focus:border-violet-500 transition-colors w-48"
          />
        </div>
      </div>

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map(i => (
            <div key={i} className="h-20 rounded-xl bg-aegisaxiom-accent-soft animate-pulse" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-12 text-aegisaxiom-text-muted text-sm">
          {search ? 'No matching sessions found.' : 'No discovery sessions yet. Open Vega from the AI Employees tab to start one.'}
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map(s => (
            <SessionCard key={s.session_id} session={s} onClick={setSelected} />
          ))}
        </div>
      )}
    </div>
  );
}
