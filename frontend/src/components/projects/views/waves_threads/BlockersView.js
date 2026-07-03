import React, { useEffect, useMemo, useState } from 'react';
import { Plus, X } from 'lucide-react';
import { toast } from 'sonner';
import { api, unwrap } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import StatusPill from '../../widgets/StatusPill';
import { usePermissions } from '@/hooks/usePermissions';

const BLOCKER_BAR_COLOR = {
  open: '#A02620',
  acknowledged: '#FFC107',
  resolved: '#4CAF50',
  escalated: '#6B2BA8',
};

export default function BlockersView({ project }) {
  const { hasPermission } = usePermissions();
  const canCreate = hasPermission('projects.blocker_ticket.create');
  const canChangeStatus = hasPermission('projects.blocker_ticket.change_status');
  const [waves, setWaves] = useState([]);
  const [selectedWaveId, setSelectedWaveId] = useState(null);
  const [blockers, setBlockers] = useState([]);
  const [threads, setThreads] = useState([]);
  const [loading, setLoading] = useState(false);
  const [composeOpen, setComposeOpen] = useState(false);
  const [draft, setDraft] = useState({ thread_id: '', target_module: '', target_thread_id: '', description: '' });
  const [saving, setSaving] = useState(false);

  const projectId = project?.project_id ?? project?._id;

  useEffect(() => {
    if (!projectId) return;
    api.get(`${API_URL}/api/projects/waves?project_id=${projectId}`)
      .then((r) => r.json())
      .then((data) => {
        const list = unwrap(data);
        setWaves(list);
        const exec = list.find((w) => w.status === 'executing');
        if (exec) setSelectedWaveId(exec.wave_id ?? exec._id);
        else if (list[0]) setSelectedWaveId(list[0].wave_id ?? list[0]._id);
      });
  }, [projectId]);

  useEffect(() => {
    if (!selectedWaveId) return;
    setLoading(true);
    Promise.all([
      api.get(`${API_URL}/api/projects/blocker-tickets?wave_id=${selectedWaveId}`).then((r) => r.json()).then(unwrap),
      api.get(`${API_URL}/api/projects/threads?wave_id=${selectedWaveId}`).then((r) => r.json()).then(unwrap),
    ]).then(([bl, th]) => {
      setBlockers(bl);
      setThreads(th);
    }).finally(() => setLoading(false));
  }, [selectedWaveId]);

  const grouped = useMemo(() => {
    const map = new Map();
    for (const b of blockers) {
      const key = b.target_thread_id ?? null;
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(b);
    }
    return map;
  }, [blockers]);

  const patchBlocker = async (blockerId, patch) => {
    const res = await api.patch(`${API_URL}/api/projects/blocker-tickets/${blockerId}`, patch);
    if (!res.ok) { toast.error('Could not update blocker.'); return; }
    setBlockers((prev) => prev.map((b) => (b.blocker_ticket_id ?? b._id) === blockerId ? { ...b, ...patch } : b));
  };

  const submitBlocker = async () => {
    if (!draft.thread_id || !draft.target_module.trim() || !draft.description.trim()) {
      toast.error('Source thread, target module and description are required.');
      return;
    }
    setSaving(true);
    try {
      const res = await api.post(`${API_URL}/api/projects/blocker-tickets`, {
        wave_id: selectedWaveId,
        thread_id: draft.thread_id,
        target_module: draft.target_module.trim(),
        target_thread_id: draft.target_thread_id || undefined,
        description: draft.description.trim(),
      });
      const data = await res.json();
      if (!res.ok) { toast.error(data.detail ?? 'Could not raise blocker.'); return; }
      toast.success('Blocker raised');
      setBlockers((prev) => [...prev, data]);
      setComposeOpen(false);
      setDraft({ thread_id: '', target_module: '', target_thread_id: '', description: '' });
    } catch {
      toast.error('Could not raise blocker.');
    } finally {
      setSaving(false);
    }
  };

  const selectedWave = waves.find((w) => (w.wave_id ?? w._id) === selectedWaveId);

  return (
    <div data-testid="blockers-view" className="flex h-[calc(100vh-210px)]">
      {/* Wave rail */}
      <aside className="w-[200px] shrink-0 border-r border-aegisaxiom-border bg-aegisaxiom-surface overflow-y-auto">
        {waves.map((w) => {
          const wid = w.wave_id ?? w._id;
          const active = wid === selectedWaveId;
          return (
            <button
              key={wid}
              type="button"
              onClick={() => setSelectedWaveId(wid)}
              className={`w-full text-left px-3 py-2 border-b border-aegisaxiom-border transition-colors ${active ? 'bg-aegisaxiom-elevated' : 'hover:bg-aegisaxiom-elevated/50'}`}
              data-testid={`blockers-wave-${w.wave_number}`}
            >
              <div className="text-xs font-mono font-bold text-aegisaxiom-text">WAVE {w.wave_number}</div>
              <div className="text-[10px] text-aegisaxiom-text-muted">{w.status}</div>
            </button>
          );
        })}
      </aside>

      {/* Main content */}
      <div className="flex-1 overflow-y-auto">
        <header className="px-6 py-3 border-b border-aegisaxiom-border flex items-center gap-3">
          <h2 className="text-sm font-semibold text-aegisaxiom-text">
            Blockers{selectedWave ? ` · Wave ${selectedWave.wave_number}` : ''}
          </h2>
          <span className="text-xs text-aegisaxiom-text-muted">{blockers.length} total</span>
          {canCreate ? (
            <button
              type="button"
              onClick={() => setComposeOpen(true)}
              className="btn-primary ml-auto h-7 px-3 inline-flex items-center gap-1 text-[11px]"
              data-testid="blocker-new"
            >
              <Plus size={11} /> New blocker
            </button>
          ) : null}
        </header>

        {loading ? (
          <div className="p-6 text-sm text-aegisaxiom-text-muted">Loading…</div>
        ) : blockers.length === 0 ? (
          <p className="p-6 text-xs text-aegisaxiom-text-muted" data-testid="blockers-empty">No blockers in this wave.</p>
        ) : (
          Array.from(grouped.entries()).map(([targetId, list]) => {
            const targetThread = threads.find((t) => (t.thread_id ?? t._id) === targetId);
            const groupLabel = targetThread ? `→ ${targetThread.module}` : 'Unassigned target';
            return (
              <section key={targetId ?? 'none'}>
                <header className="h-8 px-4 bg-aegisaxiom-elevated flex items-center justify-between text-[11px] font-bold uppercase text-aegisaxiom-text-muted border-b border-aegisaxiom-border">
                  <span>{groupLabel}</span>
                  <span>{list.length}</span>
                </header>
                <ul>
                  {list.map((b) => {
                    const bid = b.blocker_ticket_id ?? b._id;
                    return (
                      <li
                        key={bid}
                        className="px-4 py-2 border-b border-aegisaxiom-border flex gap-2 items-start"
                        data-testid={`blocker-row-${bid}`}
                      >
                        <div
                          className="w-1 self-stretch rounded-sm shrink-0"
                          style={{ backgroundColor: BLOCKER_BAR_COLOR[b.status] ?? '#6B7280' }}
                        />
                        <div className="flex-1 min-w-0">
                          <div className="text-xs text-aegisaxiom-text truncate">{b.description.slice(0, 80)}</div>
                          <div className="mt-1 flex items-center gap-2 text-[10px] text-aegisaxiom-text-muted">
                            <span className="font-mono">{b.target_module}</span>
                            <select
                              value={b.status}
                              disabled={!canChangeStatus}
                              onChange={(e) => patchBlocker(bid, { status: e.target.value })}
                              className="aegisaxiom-input h-5 text-[10px] px-1 disabled:opacity-60"
                              data-testid={`blocker-status-${bid}`}
                            >
                              <option value="open">open</option>
                              <option value="acknowledged">acknowledged</option>
                              <option value="resolved">resolved</option>
                              <option value="escalated">escalated</option>
                            </select>
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </section>
            );
          })
        )}
      </div>

      {/* Compose blocker modal */}
      {composeOpen ? (
        <div
          className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center"
          onClick={() => setComposeOpen(false)}
          data-testid="blocker-compose-backdrop"
        >
          <div
            className="glass-card w-[480px] p-5 space-y-3"
            onClick={(e) => e.stopPropagation()}
            data-testid="blocker-compose-modal"
            role="dialog"
            aria-label="Raise blocker"
          >
            <div className="flex items-center gap-2 mb-1">
              <h3 className="text-sm font-bold text-aegisaxiom-text">Raise blocker</h3>
              <button type="button" onClick={() => setComposeOpen(false)} className="ml-auto text-aegisaxiom-text-muted hover:text-aegisaxiom-text" aria-label="Close">
                <X size={14} />
              </button>
            </div>
            <div>
              <label className="block text-xs text-aegisaxiom-text-muted mb-1">Source thread</label>
              <select value={draft.thread_id} onChange={(e) => setDraft({ ...draft, thread_id: e.target.value })} className="aegisaxiom-input w-full">
                <option value="">— Pick a thread —</option>
                {threads.map((t) => <option key={t.thread_id ?? t._id} value={t.thread_id ?? t._id}>{t.module}</option>)}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-aegisaxiom-text-muted mb-1">Target module</label>
                <input className="aegisaxiom-input w-full" value={draft.target_module} onChange={(e) => setDraft({ ...draft, target_module: e.target.value })} placeholder="payments" />
              </div>
              <div>
                <label className="block text-xs text-aegisaxiom-text-muted mb-1">Target thread (optional)</label>
                <select value={draft.target_thread_id} onChange={(e) => setDraft({ ...draft, target_thread_id: e.target.value })} className="aegisaxiom-input w-full">
                  <option value="">—</option>
                  {threads.map((t) => <option key={t.thread_id ?? t._id} value={t.thread_id ?? t._id}>{t.module}</option>)}
                </select>
              </div>
            </div>
            <div>
              <label className="block text-xs text-aegisaxiom-text-muted mb-1">Description</label>
              <textarea rows={4} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} className="aegisaxiom-input w-full resize-y" />
            </div>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setComposeOpen(false)} className="btn-secondary h-8 px-3 text-xs">Cancel</button>
              <button type="button" onClick={submitBlocker} disabled={saving} className="btn-primary h-8 px-3 text-xs disabled:opacity-60">
                {saving ? 'Raising…' : 'Raise blocker'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
