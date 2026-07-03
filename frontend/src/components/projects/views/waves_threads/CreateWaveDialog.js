import React, { useEffect, useMemo, useState } from 'react';
import { Plus, Trash2, X } from 'lucide-react';
import { toast } from 'sonner';
import { api, unwrap } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import DateInput from '@/components/ui/DateInput';

export default function CreateWaveDialog({ open, onOpenChange, projectId, projectModules = [], waves: wavesProp, onCreated }) {
  const hasModules = Array.isArray(projectModules) && projectModules.length > 0;
  const [step, setStep] = useState(1);
  const [waveNumber, setWaveNumber] = useState(1);
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [goal, setGoal] = useState('');
  const [dependsOn, setDependsOn] = useState([]);
  const [threads, setThreads] = useState([]);
  const [users, setUsers] = useState([]);
  const [submitting, setSubmitting] = useState(false);

  // Load assignable members once
  useEffect(() => {
    api.get(`${API_URL}/api/projects/assignable-users?role=member`)
      .then((res) => (res.ok ? res.json() : []))
      .then((data) => setUsers(unwrap(data)))
      .catch(() => toast.error('Could not load assignable users.'));
  }, []);

  const previousWaves = useMemo(
    () => [...(wavesProp ?? [])].sort((a, b) => a.wave_number - b.wave_number),
    [wavesProp],
  );

  useEffect(() => {
    if (!open) return;
    const next = previousWaves.length === 0 ? 1 : Math.max(...previousWaves.map((w) => w.wave_number)) + 1;
    setWaveNumber(next);
    setStep(1);
    setGoal('');
    setDependsOn([]);
    setThreads([]);
    const lastEnd = previousWaves.length > 0
      ? previousWaves[previousWaves.length - 1].end_date
      : null;
    if (lastEnd) {
      const d = new Date(lastEnd + 'T00:00:00');
      if (!Number.isNaN(d.getTime())) {
        d.setDate(d.getDate() + 1);
        setStartDate(d.toISOString().slice(0, 10));
      } else {
        setStartDate(new Date().toISOString().slice(0, 10));
      }
    } else {
      setStartDate(new Date().toISOString().slice(0, 10));
    }
    setEndDate('');
  }, [open]);

  const toggleDep = (id) =>
    setDependsOn((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]);

  const addThread = () => {
    setThreads((prev) => [...prev, { _key: Math.random().toString(36).slice(2), owner_id: '', module: '', integration_required: false, contractText: '' }]);
  };
  const removeThread = (k) => setThreads((prev) => prev.filter((t) => t._key !== k));
  const updateThread = (k, patch) => setThreads((prev) => prev.map((t) => t._key === k ? { ...t, ...patch } : t));

  const canStep2 = () => {
    if (!goal.trim()) return 'Goal is required.';
    if (!startDate || !endDate) return 'Start and end dates are required.';
    if (endDate < startDate) return 'End date must be on/after start date.';
    return null;
  };

  const submit = async () => {
    for (const t of threads) {
      if (!t.module.trim()) { toast.error('Every thread needs a module name.'); return; }
      if (t.integration_required && !t.contractText.trim()) {
        toast.error(`Thread "${t.module}" requires an interface contract.`);
        return;
      }
    }
    const wavePayload = {
      project_id: projectId,
      start_date: startDate,
      end_date: endDate,
      goal: goal.trim(),
      depends_on_wave_ids: dependsOn,
    };
    setSubmitting(true);
    try {
      const res = await api.post(`${API_URL}/api/projects/waves`, wavePayload);
      const data = await res.json();
      if (!res.ok) { toast.error(data.detail ?? 'Could not create wave.'); return; }
      const waveId = data.wave_id ?? data._id;
      const threadResults = await Promise.allSettled(
        threads.map((t) =>
          api.post(`${API_URL}/api/projects/threads`, {
            wave_id: waveId,
            project_id: projectId,
            owner_id: t.owner_id || undefined,
            module: t.module.trim(),
            integration_required: t.integration_required,
            interface_contract: t.contractText.trim() ? { markdown: t.contractText.trim() } : {},
          }).then((r) => (r.ok ? r : Promise.reject(r)))
        )
      );
      const failures = threadResults.filter((r) => r.status === 'rejected');
      if (failures.length > 0) {
        let firstDetail = '';
        try {
          const body = await failures[0].reason.json();
          firstDetail = body?.detail ? ` First error: ${body.detail}` : '';
        } catch { /* best effort */ }
        toast.error(`Wave ${data.wave_number ?? waveNumber} created, but ${failures.length} thread(s) failed.${firstDetail}`);
      } else {
        toast.success(`Wave ${data.wave_number ?? waveNumber} created`);
      }
      onCreated?.(waveId);
      onOpenChange(false);
    } catch {
      toast.error('Could not create wave.');
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center"
      onClick={() => onOpenChange(false)}
      data-testid="create-wave-dialog-backdrop"
    >
      <div
        className="glass-card w-[720px] max-h-[85vh] overflow-y-auto p-6 relative"
        onClick={(e) => e.stopPropagation()}
        data-testid="create-wave-dialog"
        role="dialog"
        aria-label="New wave"
      >
        <button
          type="button"
          onClick={() => onOpenChange(false)}
          className="absolute right-3 top-3 text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
          aria-label="Close"
        >
          <X size={14} />
        </button>

        <h3 className="text-sm font-bold text-aegisaxiom-text mb-4">New wave · Step {step} of 2</h3>

        {step === 1 ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-aegisaxiom-text-muted mb-1">Wave number</label>
                <input
                  type="number"
                  min={1}
                  className="aegisaxiom-input w-full"
                  value={waveNumber}
                  onChange={(e) => setWaveNumber(parseInt(e.target.value, 10) || 1)}
                  data-testid="wave-num"
                />
              </div>
              <div>
                <label className="block text-xs text-aegisaxiom-text-muted mb-1">
                  Goal <span className="text-red-400">*</span>
                </label>
                <input
                  className="aegisaxiom-input w-full"
                  value={goal}
                  onChange={(e) => setGoal(e.target.value)}
                  maxLength={240}
                  placeholder="Ship onboarding flow to 10% of new users"
                  data-testid="wave-goal"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-aegisaxiom-text-muted mb-1">Start <span className="text-red-400">*</span></label>
                <DateInput value={startDate} onChange={setStartDate} data-testid="wave-start" placeholder="Wave start" />
              </div>
              <div>
                <label className="block text-xs text-aegisaxiom-text-muted mb-1">End <span className="text-red-400">*</span></label>
                <DateInput value={endDate} onChange={setEndDate} data-testid="wave-end" placeholder="Wave end" min={startDate || undefined} />
              </div>
            </div>
            <div>
              <label className="block text-xs text-aegisaxiom-text-muted mb-1">Depends on</label>
              {previousWaves.length === 0 ? (
                <p className="text-[11px] text-aegisaxiom-text-muted">No earlier waves yet.</p>
              ) : (
                <div className="flex flex-wrap items-center gap-1.5 py-1">
                  {previousWaves.map((w) => {
                    const wid = w.wave_id ?? w._id;
                    const on = dependsOn.includes(wid);
                    return (
                      <button
                        key={wid}
                        type="button"
                        onClick={() => toggleDep(wid)}
                        data-testid={`wave-dep-${w.wave_number}`}
                        className={`inline-flex items-center justify-center h-6 px-2 rounded-full text-[11px] font-mono border transition-colors ${on ? 'btn-primary' : 'btn-secondary'}`}
                      >
                        WAVE {w.wave_number}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-[11px] text-aegisaxiom-text-muted">
              A wave is more useful with at least one thread. You can also add threads later.
            </p>
            <div className="space-y-2">
              {threads.map((t) => (
                <div key={t._key} className="rounded-md border border-aegisaxiom-border p-3 space-y-2" data-testid={`thread-row-${t._key}`}>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs text-aegisaxiom-text-muted mb-1">Owner</label>
                      <select
                        value={t.owner_id}
                        onChange={(e) => updateThread(t._key, { owner_id: e.target.value })}
                        className="aegisaxiom-input w-full"
                      >
                        <option value="">— Unassigned —</option>
                        {users.map((u) => (
                          <option key={u.user_id ?? u._id} value={u.user_id ?? u._id}>{u.name ?? u.display_name}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="block text-xs text-aegisaxiom-text-muted mb-1">Module</label>
                      {hasModules ? (
                        <select
                          className="aegisaxiom-input w-full"
                          value={t.module}
                          onChange={(e) => updateThread(t._key, { module: e.target.value })}
                        >
                          <option value="">— Select module —</option>
                          {projectModules.map((m) => (
                            <option key={m} value={m}>{m}</option>
                          ))}
                        </select>
                      ) : (
                        <input
                          className="aegisaxiom-input w-full"
                          value={t.module}
                          onChange={(e) => updateThread(t._key, { module: e.target.value })}
                          placeholder="auth / payments / search…"
                        />
                      )}
                    </div>
                  </div>
                  <label className="inline-flex items-center gap-2 text-xs text-aegisaxiom-text-muted">
                    <input
                      type="checkbox"
                      checked={t.integration_required}
                      onChange={(e) => updateThread(t._key, { integration_required: e.target.checked })}
                      data-testid={`thread-integration-${t._key}`}
                    />
                    Integration required (cross-thread interface)
                  </label>
                  {t.integration_required ? (
                    <div>
                      <label className="block text-xs text-aegisaxiom-text-muted mb-1">
                        Interface contract <span className="text-red-400">*</span>
                      </label>
                      <textarea
                        rows={3}
                        value={t.contractText}
                        onChange={(e) => updateThread(t._key, { contractText: e.target.value })}
                        placeholder={'### Endpoints\n- POST /api/charge'}
                        className="aegisaxiom-input w-full resize-y text-sm"
                      />
                    </div>
                  ) : null}
                  <div className="flex items-center justify-end">
                    <button
                      type="button"
                      onClick={() => removeThread(t._key)}
                      className="h-7 px-2 inline-flex items-center gap-1 text-[11px] text-aegisaxiom-text-muted hover:text-red-400"
                    >
                      <Trash2 size={11} /> Remove thread
                    </button>
                  </div>
                </div>
              ))}
              <button
                type="button"
                onClick={addThread}
                className="btn-secondary w-full h-9 inline-flex items-center justify-center gap-1.5 text-xs border-dashed"
                data-testid="add-thread"
              >
                <Plus size={12} /> Add thread
              </button>
            </div>
          </div>
        )}

        <div className="flex justify-end gap-2 mt-5">
          {step === 1 ? (
            <>
              <button type="button" onClick={() => onOpenChange(false)} className="btn-secondary h-8 px-3 text-xs">Cancel</button>
              <button
                type="button"
                onClick={() => {
                  const err = canStep2();
                  if (err) { toast.error(err); return; }
                  setStep(2);
                }}
                className="btn-primary h-8 px-3 text-xs"
                data-testid="wave-step2"
              >
                Next: Threads
              </button>
            </>
          ) : (
            <>
              <button type="button" onClick={() => setStep(1)} className="btn-secondary h-8 px-3 text-xs">Back</button>
              <button
                type="button"
                onClick={submit}
                disabled={submitting}
                className="btn-primary h-8 px-3 text-xs disabled:opacity-60"
                data-testid="wave-submit"
              >
                {submitting ? 'Creating…' : 'Create wave'}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
