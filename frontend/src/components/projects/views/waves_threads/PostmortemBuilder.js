import React, { useEffect, useMemo, useRef, useState } from 'react';
import { format, parseISO } from 'date-fns';
import { Link2, Plus, Trash2, X } from 'lucide-react';
import { toast } from 'sonner';
import { api, unwrap } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import UserAvatar from '../../widgets/UserAvatar';
import { usePermissions } from '@/hooks/usePermissions';

const SECTIONS = [
  { id: 'header', label: '1 · Header' },
  { id: 'thread-status', label: '2 · Thread status' },
  { id: 'buckets', label: '3 · Bucket items' },
  { id: 'discoveries', label: '4 · Discoveries' },
  { id: 'stakeholder', label: '5 · Stakeholder update' },
];

function randomKey() { return Math.random().toString(36).slice(2); }

export default function PostmortemBuilder({ open, onOpenChange, project, wave, tasks, onClosed }) {
  const { hasPermission } = usePermissions();
  const canCloseWave = hasPermission('projects.wave.change_status');
  const [threads, setThreads] = useState([]);
  const [users, setUsers] = useState([]);
  const [statusRows, setStatusRows] = useState([]);
  const [bucketItems, setBucketItems] = useState([]);
  const [discoveries, setDiscoveries] = useState('');
  const [stakeholder, setStakeholder] = useState('');
  const [stakeholderTouched, setStakeholderTouched] = useState(false);
  const [draftSavedAt, setDraftSavedAt] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [dragOver, setDragOver] = useState(null);

  const waveId = wave?.wave_id ?? wave?._id;
  const draftKey = `aegisaxiom_pm_postmortem_draft_${waveId}`;

  const tasksInWave = useMemo(
    () => (tasks ?? []).filter((t) => t.methodology_fields?.wave_id === waveId),
    [tasks, waveId],
  );

  useEffect(() => {
    if (!open || !waveId) return;
    Promise.all([
      api.get(`${API_URL}/api/projects/threads?wave_id=${waveId}`).then((r) => r.json()).then(unwrap),
      api.get(`${API_URL}/api/projects/assignable-users?role=member`)
        .then((r) => (r.ok ? r.json() : []))
        .then(unwrap)
        .catch(() => []),
    ]).then(([th, us]) => {
      setThreads(th);
      setUsers(us);
    });
    // Restore draft
    const raw = localStorage.getItem(draftKey);
    if (raw) {
      try {
        const d = JSON.parse(raw);
        setStatusRows(d.statusRows ?? []);
        setBucketItems(d.bucketItems ?? []);
        setDiscoveries(d.discoveries ?? '');
        setStakeholder(d.stakeholder ?? '');
        setStakeholderTouched(d.stakeholderTouched ?? false);
        setDraftSavedAt(d.savedAt ?? null);
      } catch {}
    }
  }, [open, waveId]);

  // Init status rows when threads load
  useEffect(() => {
    if (!open || threads.length === 0) return;
    setStatusRows((prev) => {
      const existingMap = new Map(prev.map((r) => [r.thread_id, r]));
      return threads.map((t) => {
        const tid = t.thread_id ?? t._id;
        const ex = existingMap.get(tid);
        const tTasks = tasksInWave.filter((tt) => tt.methodology_fields?.thread_id === tid);
        const scoped_count = tTasks.filter((tt) => tt.status === 'scoped' || tt.status === 'in_progress').length;
        const drift = tTasks.some((tt) => tt.methodology_fields?.scope_drift);
        const standup = t.standup ?? {};
        return ex ?? {
          thread_id: tid,
          owner_id: t.owner_id,
          scoped_count,
          shipped_summary: standup.shipped_yesterday ?? '',
          scope_drift: drift,
          blockers_summary: standup.blockers_or_scope_drift ?? '',
        };
      });
    });
  }, [open, threads, tasksInWave]);

  const usedSourceIds = useMemo(
    () => new Set(bucketItems.map((b) => b.source_task_id).filter(Boolean)),
    [bucketItems],
  );
  const sourceTasks = useMemo(
    () =>
      tasksInWave
        .filter((t) => ['scoped', 'in_progress', 'blocked'].includes(t.status))
        .filter((t) => !usedSourceIds.has(t.task_id ?? t._id)),
    [tasksInWave, usedSourceIds],
  );

  const buildStakeholderDraft = () => {
    const lines = [];
    lines.push(`## Wave ${wave.wave_number} Recap\n`);
    lines.push(`Goal: ${wave.goal}`);
    const win = (wave.start_date && wave.end_date)
      ? `${format(parseISO(wave.start_date), 'MMM d')} → ${format(parseISO(wave.end_date), 'MMM d')}`
      : '— no dates —';
    lines.push(`Window: ${win}\n`);
    const shipped = statusRows.filter((r) => r.shipped_summary.trim()).map((r) => {
      const t = threads.find((th) => (th.thread_id ?? th._id) === r.thread_id);
      return `- **${t?.module ?? '?'}**: ${r.shipped_summary.trim()}`;
    });
    if (shipped.length) { lines.push('Shipped:'); lines.push(...shipped); lines.push(''); }
    const must = bucketItems.filter((b) => b.bucket === 'must_fix');
    if (must.length) { lines.push('Going into next wave:'); must.forEach((m) => lines.push(`- ${m.title}`)); lines.push(''); }
    if (discoveries.trim()) { lines.push('Discoveries:'); lines.push(discoveries.trim()); }
    return lines.join('\n');
  };

  const saveDraft = (silent = false) => {
    const payload = { statusRows, bucketItems, discoveries, stakeholder, stakeholderTouched, savedAt: Date.now() };
    localStorage.setItem(draftKey, JSON.stringify(payload));
    setDraftSavedAt(payload.savedAt);
    if (!silent) toast.success('Draft saved');
  };

  const draftTimer = useRef(null);
  useEffect(() => {
    if (!open) return;
    if (draftTimer.current) window.clearTimeout(draftTimer.current);
    draftTimer.current = window.setTimeout(() => saveDraft(true), 2000);
    return () => { if (draftTimer.current) window.clearTimeout(draftTimer.current); };
  }, [statusRows, bucketItems, discoveries, stakeholder]);

  // Bucket drag-and-drop via HTML5 DnD
  const handleTaskDrop = (e, bucket) => {
    const taskId = e.dataTransfer.getData('taskId');
    const t = sourceTasks.find((x) => (x.task_id ?? x._id) === taskId);
    if (!t) return;
    const mf = t.methodology_fields ?? {};
    setBucketItems((prev) => [...prev, {
      _key: randomKey(),
      title: t.title,
      source_task_id: t.task_id ?? t._id,
      module_owned: mf.module_owned ?? null,
      priority: bucket === 'must_fix' ? 'high' : bucket === 'should_add' ? 'medium' : 'low',
      bucket,
    }]);
    setDragOver(null);
  };

  const addFreeformItem = (bucket) =>
    setBucketItems((prev) => [...prev, { _key: randomKey(), title: '', source_task_id: null, module_owned: null, priority: 'medium', bucket }]);

  const removeItem = (key) => setBucketItems((prev) => prev.filter((i) => i._key !== key));
  const updateItem = (key, patch) => setBucketItems((prev) => prev.map((i) => (i._key === key ? { ...i, ...patch } : i)));

  const submit = async () => {
    if (!canCloseWave) {
      toast.error('You do not have permission to close waves.');
      return;
    }
    setSubmitting(true);
    try {
      const developers = threads.filter((t) => t.owner_id).map((t) => {
        const u = users.find((uu) => (uu.user_id ?? uu._id) === t.owner_id);
        return { user_id: t.owner_id, display_name: u?.name ?? u?.display_name ?? null };
      });
      const dedupDevs = Array.from(new Map(developers.map((d) => [d.user_id, d])).values());
      const payload = {
        postmortem: {
          header: { wave_number: wave.wave_number, start_date: wave.start_date, end_date: wave.end_date, developers: dedupDevs },
          thread_status: statusRows,
          buckets: {
            must_fix: bucketItems.filter((i) => i.bucket === 'must_fix' && i.title.trim()).map(({ _key, bucket, ...rest }) => rest),
            should_add: bucketItems.filter((i) => i.bucket === 'should_add' && i.title.trim()).map(({ _key, bucket, ...rest }) => rest),
            watch: bucketItems.filter((i) => i.bucket === 'watch' && i.title.trim()).map(({ _key, bucket, ...rest }) => rest),
          },
          discoveries,
          stakeholder_update: stakeholder,
        },
        status: 'closed',
      };
      const res = await api.patch(`${API_URL}/api/projects/waves/${waveId}`, payload);
      const data = await res.json();
      if (!res.ok) { toast.error(data.detail ?? 'Could not close wave.'); return; }
      toast.success(`Wave ${wave.wave_number} closed.`);
      localStorage.removeItem(draftKey);
      onClosed?.(data.next_wave_id ?? null);
      onOpenChange(false);
    } catch {
      toast.error('Could not close wave.');
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center"
      onClick={() => onOpenChange(false)}
      data-testid="postmortem-builder-backdrop"
    >
      <div
        className="glass-card w-[min(1280px,98vw)] max-h-[95vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
        data-testid="postmortem-builder"
        role="dialog"
        aria-label="Postmortem builder"
      >
        {/* Header */}
        <header className="px-6 py-3 border-b border-aegisaxiom-border flex items-center gap-3 shrink-0">
          <h2 className="text-base font-bold text-aegisaxiom-text">Postmortem · Wave {wave.wave_number}</h2>
          <span className="text-[11px] text-aegisaxiom-text-muted">{wave.goal}</span>
          <div className="ml-auto flex items-center gap-2">
            {draftSavedAt ? (
              <span className="text-[11px] text-aegisaxiom-text-muted" data-testid="draft-saved-indicator">
                Draft saved · {Math.max(1, Math.floor((Date.now() - draftSavedAt) / 1000))}s ago
              </span>
            ) : null}
            <button type="button" onClick={() => saveDraft(false)} className="btn-secondary h-8 px-3 text-xs" data-testid="postmortem-save-draft">
              Save draft
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={submitting}
              className="btn-primary h-8 px-3 text-xs disabled:opacity-60"
              data-testid="postmortem-submit"
            >
              {submitting ? 'Closing…' : 'Submit & close wave'}
            </button>
            <button type="button" onClick={() => onOpenChange(false)} className="ml-1 h-6 w-6 inline-flex items-center justify-center text-aegisaxiom-text-muted hover:text-aegisaxiom-text" aria-label="Close">
              <X size={14} />
            </button>
          </div>
        </header>

        {/* Body */}
        <div className="grid flex-1 overflow-hidden" style={{ gridTemplateColumns: '200px 1fr' }}>
          {/* Left rail nav */}
          <aside className="border-r border-aegisaxiom-border p-3 overflow-y-auto bg-aegisaxiom-elevated">
            <ul className="space-y-1">
              {SECTIONS.map((s) => (
                <li key={s.id}>
                  <a href={`#pm-${s.id}`} className="block px-2 py-1.5 rounded text-[12px] font-semibold text-aegisaxiom-text hover:bg-aegisaxiom-surface">
                    {s.label}
                  </a>
                </li>
              ))}
            </ul>
            <div className="mt-4">
              <button
                type="button"
                onClick={() => {
                  const url = `${window.location.origin}/projects/${project.project_id ?? project._id}?view=threads`;
                  navigator.clipboard.writeText(url).then(() => toast.success('Share link copied'));
                }}
                className="btn-secondary w-full h-8 inline-flex items-center justify-center gap-1.5 text-[11px]"
                data-testid="postmortem-share"
              >
                <Link2 size={12} /> Share link
              </button>
            </div>
          </aside>

          {/* Scroll body */}
          <div className="overflow-y-auto p-6 space-y-8" data-testid="postmortem-body">
            {/* Section 1: Header */}
            <section id="pm-header" data-testid="pm-section-header">
              <SectionTitle n="1" title="Header" />
              <div className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface p-4 grid grid-cols-3 gap-4">
                <Stat label="Wave" value={`#${wave.wave_number}`} />
                <Stat label="Window" value={(wave.start_date && wave.end_date) ? `${format(parseISO(wave.start_date), 'MMM d')} → ${format(parseISO(wave.end_date), 'MMM d')}` : '—'} />
                <Stat label="Threads" value={String(threads.length)} />
                <div className="col-span-3">
                  <p className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-1">Developers</p>
                  <div className="flex flex-wrap gap-2">
                    {threads
                      .map((t) => users.find((u) => (u.user_id ?? u._id) === t.owner_id))
                      .filter(Boolean)
                      .filter((u, i, arr) => arr.findIndex((x) => (x.user_id ?? x._id) === (u.user_id ?? u._id)) === i)
                      .map((u) => (
                        <span key={u.user_id ?? u._id} className="inline-flex items-center gap-1.5 h-6 pl-1 pr-2 rounded-full bg-aegisaxiom-elevated border border-aegisaxiom-border">
                          <UserAvatar user={u} size="sm" />
                          <span className="text-[11px] text-aegisaxiom-text">{u.name ?? u.display_name}</span>
                        </span>
                      ))}
                  </div>
                </div>
              </div>
            </section>

            {/* Section 2: Thread status */}
            <section id="pm-thread-status" data-testid="pm-section-thread-status">
              <SectionTitle n="2" title="Thread status" />
              <div className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-aegisaxiom-elevated text-[10px] uppercase text-aegisaxiom-text-muted">
                    <tr>
                      <th className="px-3 py-2 text-left">Dev</th>
                      <th className="px-3 py-2 text-left">Scoped</th>
                      <th className="px-3 py-2 text-left">Shipped</th>
                      <th className="px-3 py-2 text-left">Drift</th>
                      <th className="px-3 py-2 text-left">Blockers</th>
                    </tr>
                  </thead>
                  <tbody>
                    {statusRows.map((row) => {
                      const t = threads.find((th) => (th.thread_id ?? th._id) === row.thread_id);
                      const owner = users.find((u) => (u.user_id ?? u._id) === row.owner_id);
                      return (
                        <tr key={row.thread_id} className="border-t border-aegisaxiom-border align-top" data-testid={`pm-thread-row-${row.thread_id}`}>
                          <td className="px-3 py-2">
                            <div className="flex items-center gap-2">
                              <UserAvatar user={owner ?? null} size="sm" />
                              <div className="min-w-0">
                                <div className="text-[11px] font-bold text-aegisaxiom-text">{t?.module}</div>
                                <div className="text-[10px] text-aegisaxiom-text-muted truncate">{owner?.name ?? owner?.display_name ?? 'Unassigned'}</div>
                              </div>
                            </div>
                          </td>
                          <td className="px-3 py-2 font-mono">{row.scoped_count}</td>
                          <td className="px-3 py-2 w-[26%]">
                            <textarea
                              rows={2}
                              value={row.shipped_summary}
                              onChange={(e) => setStatusRows((prev) => prev.map((r) => r.thread_id === row.thread_id ? { ...r, shipped_summary: e.target.value } : r))}
                              className="aegisaxiom-input w-full text-[12px]"
                              data-testid={`pm-shipped-${row.thread_id}`}
                            />
                          </td>
                          <td className="px-3 py-2">
                            <button
                              type="button"
                              onClick={() => setStatusRows((prev) => prev.map((r) => r.thread_id === row.thread_id ? { ...r, scope_drift: !r.scope_drift } : r))}
                              className={`h-6 px-2 rounded-full text-[10px] font-bold ${row.scope_drift ? 'bg-red-500/15 text-red-400' : 'bg-green-500/15 text-green-400'}`}
                              data-testid={`pm-drift-${row.thread_id}`}
                            >
                              {row.scope_drift ? 'Yes' : 'No'}
                            </button>
                          </td>
                          <td className="px-3 py-2 w-[26%]">
                            <textarea
                              rows={2}
                              value={row.blockers_summary}
                              onChange={(e) => setStatusRows((prev) => prev.map((r) => r.thread_id === row.thread_id ? { ...r, blockers_summary: e.target.value } : r))}
                              className="aegisaxiom-input w-full text-[12px]"
                              data-testid={`pm-blockers-${row.thread_id}`}
                            />
                          </td>
                        </tr>
                      );
                    })}
                    {statusRows.length === 0 ? (
                      <tr><td colSpan={5} className="px-3 py-3 text-xs text-aegisaxiom-text-muted">No threads in this wave.</td></tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </section>

            {/* Section 3: Buckets */}
            <section id="pm-buckets" data-testid="pm-section-buckets">
              <SectionTitle n="3" title="Bucketed items" />
              {/* Source list */}
              <div className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface p-3 mb-3">
                <h4 className="text-[10px] uppercase tracking-wider font-bold text-aegisaxiom-text-muted mb-2">
                  Source tasks ({sourceTasks.length})
                </h4>
                <div className="flex gap-2 overflow-x-auto pb-1" data-testid="pm-source-list">
                  {sourceTasks.length === 0 ? (
                    <p className="text-[11px] text-aegisaxiom-text-muted">All tasks bucketed.</p>
                  ) : (
                    sourceTasks.map((t) => (
                      <div
                        key={t.task_id ?? t._id}
                        draggable
                        onDragStart={(e) => e.dataTransfer.setData('taskId', t.task_id ?? t._id)}
                        className="shrink-0 max-w-[260px] rounded border border-aegisaxiom-border bg-aegisaxiom-elevated px-2 py-1 text-[11px] cursor-grab"
                        data-testid={`pm-source-${t.task_id ?? t._id}`}
                      >
                        <span className="font-mono text-[10px] text-aegisaxiom-text-muted mr-1">#{t.task_number}</span>
                        <span className="font-medium text-aegisaxiom-text">{t.title.slice(0, 36)}</span>
                      </div>
                    ))
                  )}
                </div>
              </div>
              {/* Three bucket columns */}
              <div className="grid grid-cols-3 gap-3">
                {[
                  { bucket: 'must_fix', label: 'MUST FIX', accent: '#A02620' },
                  { bucket: 'should_add', label: 'SHOULD ADD', accent: '#7A5800' },
                  { bucket: 'watch', label: 'WATCH', accent: '#1F5A1F' },
                ].map(({ bucket, label, accent }) => (
                  <BucketColumn
                    key={bucket}
                    bucket={bucket}
                    label={label}
                    accent={accent}
                    items={bucketItems.filter((i) => i.bucket === bucket)}
                    isOver={dragOver === bucket}
                    onDragOver={(e) => { e.preventDefault(); setDragOver(bucket); }}
                    onDragLeave={() => setDragOver(null)}
                    onDrop={(e) => handleTaskDrop(e, bucket)}
                    onAddFreeform={() => addFreeformItem(bucket)}
                    onUpdate={updateItem}
                    onRemove={removeItem}
                  />
                ))}
              </div>
            </section>

            {/* Section 4: Discoveries */}
            <section id="pm-discoveries" data-testid="pm-section-discoveries">
              <SectionTitle n="4" title="Discoveries" />
              <p className="text-[11px] text-aegisaxiom-text-muted mb-2">2-5 bullet points capturing surprises this wave.</p>
              <textarea
                rows={5}
                value={discoveries}
                onChange={(e) => setDiscoveries(e.target.value)}
                className="aegisaxiom-input w-full resize-y"
                data-testid="pm-discoveries"
                placeholder="What surprised you this wave?"
              />
            </section>

            {/* Section 5: Stakeholder update */}
            <section id="pm-stakeholder" data-testid="pm-section-stakeholder">
              <SectionTitle n="5" title="Stakeholder update" />
              <p className="text-[11px] text-aegisaxiom-text-muted mb-2">Auto-drafted on request from sections 1–4. Edit before submit.</p>
              {!stakeholderTouched && !stakeholder ? (
                <button
                  type="button"
                  onClick={() => { setStakeholder(buildStakeholderDraft()); setStakeholderTouched(true); }}
                  className="btn-secondary h-7 px-2 text-[11px] mb-2"
                  data-testid="pm-stakeholder-autodraft"
                >
                  Auto-draft from sections 1–4
                </button>
              ) : null}
              <textarea
                rows={10}
                value={stakeholder}
                onChange={(e) => { setStakeholder(e.target.value); setStakeholderTouched(true); }}
                className="aegisaxiom-input w-full resize-y"
                data-testid="pm-stakeholder"
                placeholder="Stakeholder update…"
              />
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}

function SectionTitle({ n, title }) {
  return (
    <h3 className="mb-3">
      <span className="text-[11px] font-mono text-aegisaxiom-text-muted mr-2">{n}</span>
      <span className="text-base font-bold text-aegisaxiom-text">{title}</span>
    </h3>
  );
}

function Stat({ label, value }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted">{label}</p>
      <p className="text-sm font-bold text-aegisaxiom-text">{value}</p>
    </div>
  );
}

function BucketColumn({ bucket, label, accent, items, isOver, onDragOver, onDragLeave, onDrop, onAddFreeform, onUpdate, onRemove }) {
  return (
    <div
      data-testid={`pm-bucket-${bucket}`}
      className={`rounded-md border ${isOver ? 'border-blue-500/40' : 'border-aegisaxiom-border'} bg-aegisaxiom-surface`}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <header className="h-10 px-3 flex items-center gap-2 rounded-t-md" style={{ backgroundColor: `${accent}22` }}>
        <span className="text-xs font-bold uppercase" style={{ color: accent }}>{label}</span>
        <span className="ml-auto inline-flex items-center h-5 px-2 rounded-full text-white text-[10px] font-semibold" style={{ backgroundColor: accent }}>
          {items.length}
        </span>
      </header>
      <div className="p-2 space-y-2 min-h-[140px]">
        {items.map((it) => (
          <div key={it._key} className="rounded border border-aegisaxiom-border p-2 text-[11px] bg-aegisaxiom-elevated" data-testid={`pm-bucket-item-${it._key}`}>
            <input
              value={it.title}
              onChange={(e) => onUpdate(it._key, { title: e.target.value })}
              placeholder="Item title"
              className="aegisaxiom-input w-full h-7 text-xs"
            />
            <div className="mt-1 flex items-center gap-1">
              <input
                value={it.module_owned ?? ''}
                onChange={(e) => onUpdate(it._key, { module_owned: e.target.value || null })}
                placeholder="Module"
                className="aegisaxiom-input h-7 text-[11px] flex-1"
              />
              <select
                value={it.priority}
                onChange={(e) => onUpdate(it._key, { priority: e.target.value })}
                className="aegisaxiom-input h-7 text-[11px]"
              >
                <option value="low">low</option>
                <option value="medium">medium</option>
                <option value="high">high</option>
                <option value="critical">critical</option>
              </select>
              <button type="button" onClick={() => onRemove(it._key)} className="h-7 w-7 inline-flex items-center justify-center text-aegisaxiom-text-muted hover:text-red-400" aria-label="Remove">
                <Trash2 size={11} />
              </button>
            </div>
            {it.source_task_id ? (
              <span className="mt-1 inline-block text-[9px] text-aegisaxiom-text-muted font-mono">
                from #{it.source_task_id.slice(-4)}
              </span>
            ) : null}
          </div>
        ))}
      </div>
      <footer className="p-2 border-t border-aegisaxiom-border/70">
        <button
          type="button"
          onClick={onAddFreeform}
          className="btn-secondary w-full h-7 inline-flex items-center justify-center gap-1.5 text-[11px]"
          data-testid={`pm-bucket-add-${bucket}`}
        >
          <Plus size={11} /> Add new item
        </button>
      </footer>
    </div>
  );
}
