import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Plus, Sparkles } from 'lucide-react';
import { format, parseISO } from 'date-fns';
import { api, unwrap } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';
import NewStandupModal from './NewStandupModal';
import StandupViewerModal from './StandupViewerModal';

const SOURCE_LABELS = {
  manual: 'Manual',
  fireflies_md: 'Fireflies .md',
  transcript_paste: 'Pasted transcript',
};

const SOURCE_BADGE_COLORS = {
  manual: 'bg-blue-500/15 text-blue-400',
  fireflies_md: 'bg-purple-500/15 text-purple-400',
  transcript_paste: 'bg-amber-500/15 text-amber-400',
};

/**
 * StandupView — W&T detail-page tab content.
 *
 * Lists past standups for the project, grouped by date (descending).
 * Cards show source badge, summary preview, and creator. Click → viewer
 * modal. "+ New Standup" button opens NewStandupModal pre-filled with the
 * project's active wave (if a wave_id is supplied via prop).
 *
 * Props:
 *   project         — the project doc (required)
 *   activeWaveId    — optional; pre-fills the wave selector + scopes the list
 *   autoOpenNew     — if true, auto-opens NewStandupModal on mount (used by
 *                     the Wave actions bar's "Run Standup" button)
 *   onAutoOpenConsumed — callback after auto-open is consumed; lets the
 *                     parent reset its trigger flag
 */
export default function StandupView({ project, activeWaveId, autoOpenNew, onAutoOpenConsumed }) {
  const { hasPermission } = usePermissions();
  const canCreate = hasPermission('projects.standup.create');
  const canView = hasPermission('projects.standup.view');

  const [standups, setStandups] = useState([]);
  const [waves, setWaves] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newOpen, setNewOpen] = useState(false);
  const [viewerStandup, setViewerStandup] = useState(null);
  const [waveFilter, setWaveFilter] = useState(activeWaveId ?? '');

  const projectId = project?.project_id ?? project?._id;
  const isWT = project?.project_type === 'waves_threads';

  const fetchStandups = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const params = new URLSearchParams({ project_id: projectId, limit: '200' });
      if (waveFilter) params.set('wave_id', waveFilter);
      const res = await api.get(`${API_URL}/api/projects/standups?${params.toString()}`);
      if (!res.ok) {
        setStandups([]);
        return;
      }
      const data = await res.json();
      setStandups(unwrap(data));
    } finally {
      setLoading(false);
    }
  }, [projectId, waveFilter]);

  const fetchWaves = useCallback(async () => {
    if (!projectId || !isWT) return;
    const res = await api.get(`${API_URL}/api/projects/waves?project_id=${projectId}`);
    if (!res.ok) return;
    const data = await res.json();
    setWaves(unwrap(data));
  }, [projectId, isWT]);

  useEffect(() => {
    fetchStandups();
  }, [fetchStandups]);

  useEffect(() => {
    fetchWaves();
  }, [fetchWaves]);

  // Sync the wave filter when the parent's active wave changes (e.g. user
  // toggles waves on the Threads tab and then comes back to Standup).
  useEffect(() => {
    if (activeWaveId !== undefined) setWaveFilter(activeWaveId ?? '');
  }, [activeWaveId]);

  // Auto-open the New Standup modal when the parent flips the trigger.
  useEffect(() => {
    if (autoOpenNew && canCreate) {
      setNewOpen(true);
      onAutoOpenConsumed?.();
    }
  }, [autoOpenNew, canCreate, onAutoOpenConsumed]);

  const grouped = useMemo(() => {
    const map = new Map();
    for (const s of standups) {
      const key = s.date ?? '—';
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(s);
    }
    // Already sorted by backend; preserve insertion order.
    return Array.from(map.entries());
  }, [standups]);

  if (!canView) {
    return (
      <div className="glass-card mt-4 p-6 text-center text-aegisaxiom-text-muted text-sm">
        You do not have permission to view standups.
      </div>
    );
  }

  return (
    <div className="mt-4 space-y-4" data-testid="standup-view">
      {/* Toolbar */}
      <div className="flex items-center gap-3 flex-wrap">
        <h3 className="text-sm font-bold text-aegisaxiom-text">Standups</h3>
        {isWT && waves.length > 0 ? (
          <select
            value={waveFilter}
            onChange={(e) => setWaveFilter(e.target.value)}
            className="aegisaxiom-input h-8 text-xs"
            data-testid="standup-wave-filter"
          >
            <option value="">All waves</option>
            {waves.map((w) => (
              <option key={w.wave_id ?? w._id} value={w.wave_id ?? w._id}>
                Wave {w.wave_number} ({w.status})
              </option>
            ))}
          </select>
        ) : null}
        <span className="text-[11px] text-aegisaxiom-text-muted">
          {standups.length} {standups.length === 1 ? 'standup' : 'standups'}
        </span>
        {canCreate ? (
          <button
            type="button"
            onClick={() => setNewOpen(true)}
            className="btn-primary h-8 px-3 ml-auto inline-flex items-center gap-1.5 text-xs"
            data-testid="standup-new-btn"
          >
            <Plus size={12} /> New Standup
          </button>
        ) : null}
      </div>

      {/* List */}
      {loading ? (
        <div className="glass-card p-8 flex items-center justify-center">
          <div className="animate-spin w-6 h-6 rounded-full border-2 border-violet-500 border-t-transparent" />
        </div>
      ) : standups.length === 0 ? (
        <div className="glass-card p-8 text-center" data-testid="standup-empty">
          <p className="text-sm font-medium text-aegisaxiom-text mb-1">No standups recorded yet.</p>
          <p className="text-xs text-aegisaxiom-text-muted mb-4">
            Capture your first daily standup — type it manually, paste a transcript,
            or upload a Fireflies .md export.
          </p>
          {canCreate ? (
            <button
              type="button"
              onClick={() => setNewOpen(true)}
              className="btn-primary h-8 px-3 inline-flex items-center gap-1.5 text-xs"
              data-testid="standup-empty-cta"
            >
              <Plus size={12} /> Create your first standup
            </button>
          ) : null}
        </div>
      ) : (
        <div className="space-y-5">
          {grouped.map(([dateKey, list]) => (
            <DateGroup
              key={dateKey}
              dateKey={dateKey}
              standups={list}
              onClickStandup={setViewerStandup}
            />
          ))}
        </div>
      )}

      <NewStandupModal
        open={newOpen}
        onOpenChange={setNewOpen}
        project={project}
        waves={waves}
        defaultWaveId={waveFilter || activeWaveId || ''}
        onCreated={() => fetchStandups()}
      />

      <StandupViewerModal
        standup={viewerStandup}
        onClose={() => setViewerStandup(null)}
        onChanged={fetchStandups}
      />
    </div>
  );
}

function DateGroup({ dateKey, standups, onClickStandup }) {
  const dateLabel = useMemo(() => {
    if (!dateKey || dateKey === '—') return 'Undated';
    try {
      return format(parseISO(dateKey), 'EEEE, MMM d, yyyy');
    } catch {
      return dateKey;
    }
  }, [dateKey]);

  return (
    <section data-testid={`standup-date-group-${dateKey}`}>
      <h4 className="text-[10px] uppercase tracking-wider font-bold text-aegisaxiom-text-muted mb-2">
        {dateLabel}
      </h4>
      <div className="space-y-2">
        {standups.map((s) => (
          <StandupCard key={s.standup_id} standup={s} onClick={() => onClickStandup(s)} />
        ))}
      </div>
    </section>
  );
}

function StandupCard({ standup, onClick }) {
  const sourceClass = SOURCE_BADGE_COLORS[standup.source] ?? 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted';
  const hasStructured = standup.structured && (
    (standup.structured.shipped_yesterday?.length ?? 0)
    + (standup.structured.working_today?.length ?? 0)
    + (standup.structured.blockers?.length ?? 0)
  ) > 0;

  const summaryPreview = standup.summary?.trim()
    || (hasStructured ? null : '(no summary yet — click to summarize)');

  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full glass-card p-4 text-left hover:bg-aegisaxiom-elevated/30 transition-colors"
      data-testid={`standup-card-${standup.standup_id}`}
    >
      <div className="flex items-center gap-2 mb-1.5">
        <span className={`inline-flex items-center h-5 px-2 rounded text-[10px] font-medium ${sourceClass}`}>
          {SOURCE_LABELS[standup.source] ?? standup.source}
        </span>
        {!hasStructured && standup.source !== 'manual' ? (
          <span className="inline-flex items-center gap-1 h-5 px-2 rounded text-[10px] font-medium bg-violet-500/15 text-violet-400">
            <Sparkles size={9} /> Needs summary
          </span>
        ) : null}
        <span className="ml-auto text-[10px] text-aegisaxiom-text-muted font-mono">
          {standup.standup_id?.slice(-6) ?? ''}
        </span>
      </div>

      {standup.summary ? (
        <p className="text-sm text-aegisaxiom-text leading-snug mb-2 line-clamp-2">{standup.summary}</p>
      ) : (
        <p className="text-xs text-aegisaxiom-text-muted italic mb-2">{summaryPreview}</p>
      )}

      {hasStructured ? (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-aegisaxiom-text-muted">
          <span>
            <span className="text-green-400 font-semibold">Shipped:</span>{' '}
            {standup.structured.shipped_yesterday?.length ?? 0}
          </span>
          <span>
            <span className="text-blue-400 font-semibold">Today:</span>{' '}
            {standup.structured.working_today?.length ?? 0}
          </span>
          <span>
            <span className="text-red-400 font-semibold">Blockers:</span>{' '}
            {standup.structured.blockers?.length ?? 0}
          </span>
        </div>
      ) : null}
    </button>
  );
}
