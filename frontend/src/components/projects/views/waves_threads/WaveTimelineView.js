import React, { useEffect } from 'react';
import { useState } from 'react';
import { addDays, differenceInCalendarDays, format, parseISO } from 'date-fns';
import { CalendarOff, FileDown } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { unwrap } from '@/lib/api';
import LoadingSpinner from '../../widgets/LoadingSpinner';

const ROW_HEIGHT = 44;
const BAR_HEIGHT = 26;
const HEADER_HEIGHT = 44;
const LEFT_GUTTER = 160;
const DAY_PX = 14;

const STATUS_LABEL = {
  planning: 'Planning',
  executing: 'Executing',
  postmortem: 'Postmortem',
  closed: 'Closed',
};
const WAVE_BAR_BG = {
  planning: 'bg-violet-500/20',
  executing: 'bg-yellow-500/20',
  postmortem: 'bg-orange-500/20',
  closed: 'bg-green-500/20',
};
const WAVE_BORDER_COLOR = {
  planning: '#9B5DD1',
  executing: '#F2B05E',
  postmortem: '#E0853D',
  closed: '#5BA85B',
};

function monthMarkers(start, end, dayPx) {
  const out = [];
  const totalDays = differenceInCalendarDays(end, start);
  let cursor = new Date(start.getFullYear(), start.getMonth(), 1);
  while (cursor <= end) {
    const dx = differenceInCalendarDays(cursor, start) * dayPx;
    if (dx >= 0 && dx <= totalDays * dayPx) {
      out.push({ x: dx, label: format(cursor, 'MMM yyyy') });
    }
    cursor = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1);
  }
  return out;
}

function Legend() {
  return (
    <div className="flex items-center gap-3 text-[11px] text-aegisaxiom-text-muted">
      {Object.entries(STATUS_LABEL).map(([key, label]) => (
        <span key={key} className="inline-flex items-center gap-1.5">
          <span
            className="inline-block h-2.5 w-2.5 rounded-sm"
            style={{ backgroundColor: WAVE_BORDER_COLOR[key] }}
          />
          {label}
        </span>
      ))}
    </div>
  );
}

export default function WaveTimelineView({ project, onNavigateToThreads }) {
  const [allWaves, setAllWaves] = useState([]);
  const [loading, setLoading] = useState(true);

  const projectId = project?.project_id ?? project?._id;

  useEffect(() => {
    if (!projectId) return;
    setLoading(true);
    api.get(`${API_URL}/api/projects/waves?project_id=${projectId}`)
      .then((res) => res.json())
      .then((data) => {
        const list = unwrap(data);
        list.sort((a, b) => (a.wave_number ?? 0) - (b.wave_number ?? 0));
        setAllWaves(list);
      })
      .finally(() => setLoading(false));
  }, [projectId]);

  if (loading) {
    return (
      <div className="min-h-[40vh] flex items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (allWaves.length === 0) {
    return (
      <div className="px-6 py-16 text-center" data-testid="timeline-empty">
        <p className="text-sm text-aegisaxiom-text-muted">
          No waves yet. Create your first one from the Threads view.
        </p>
      </div>
    );
  }

  // Waves with both dates set drive the timeline scale; waves missing dates
  // get a ghost row so the user can see the gap and click through to fix it.
  const dated = allWaves.filter((w) => w.start_date && w.end_date);
  const undated = allWaves.filter((w) => !w.start_date || !w.end_date);

  const projectStart = project?.start_date
    ? parseISO(project.start_date)
    : dated.length > 0
      ? parseISO(dated[0].start_date)
      : new Date();
  const projectEnd = project?.target_end_date
    ? addDays(parseISO(project.target_end_date), 14)
    : dated.length > 0
      ? addDays(parseISO(dated[dated.length - 1].end_date), 14)
      : addDays(projectStart, 30);

  const totalDays = Math.max(differenceInCalendarDays(projectEnd, projectStart) + 1, 7);
  const totalWidth = LEFT_GUTTER + totalDays * DAY_PX + 40;
  const today = new Date();
  const todayDx = LEFT_GUTTER + differenceInCalendarDays(today, projectStart) * DAY_PX;
  const todayInRange = todayDx >= LEFT_GUTTER && todayDx <= totalWidth;

  const idIndex = new Map(allWaves.map((w, i) => [w.wave_id ?? w._id, i]));
  const xFor = (iso) => LEFT_GUTTER + differenceInCalendarDays(parseISO(iso), projectStart) * DAY_PX;

  const totalRows = allWaves.length;
  const canvasHeight = HEADER_HEIGHT + totalRows * ROW_HEIGHT + 12;

  return (
    <div data-testid="wave-timeline-view" className="px-6 py-4 wave-timeline-printable">
      <style>{`
        @media print {
          @page { size: landscape; margin: 0.4in; }
          body * { visibility: hidden; }
          .wave-timeline-printable, .wave-timeline-printable * { visibility: visible; }
          .wave-timeline-printable {
            position: absolute; left: 0; top: 0; width: 100%; padding: 0 !important;
            color: #111 !important; background: white !important;
          }
          .wave-timeline-printable [data-testid="timeline-canvas"] { overflow: visible !important; }
          .wave-timeline-printable .no-print { display: none !important; }
          .wave-timeline-printable .text-aegisaxiom-text,
          .wave-timeline-printable .text-aegisaxiom-text-muted { color: #111 !important; }
        }
      `}</style>
      <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
        <div className="flex items-baseline gap-3">
          <h2 className="text-sm font-bold text-aegisaxiom-text">Wave Timeline</h2>
          <span className="text-[11px] text-aegisaxiom-text-muted">
            {format(projectStart, 'MMM d, yyyy')} → {format(projectEnd, 'MMM d, yyyy')}
          </span>
          {undated.length > 0 ? (
            <span className="text-[11px] text-yellow-400" data-testid="timeline-skipped">
              {undated.length} wave{undated.length === 1 ? '' : 's'} missing dates — click to set
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-3">
          <Legend />
          <button
            type="button"
            onClick={() => window.print()}
            className="btn-secondary inline-flex items-center gap-1.5 text-xs shrink-0 no-print"
            data-testid="timeline-export-pdf"
            title="Open print dialog — choose 'Save as PDF' as the destination"
          >
            <FileDown size={14} />
            Export PDF
          </button>
        </div>
      </div>

      <div className="rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface overflow-x-auto" data-testid="timeline-canvas">
        <div className="relative" style={{ width: totalWidth, height: canvasHeight }}>
          {/* Header background spanning the gutter + month track */}
          <div
            className="absolute top-0 left-0 right-0 bg-aegisaxiom-elevated border-b border-aegisaxiom-border"
            style={{ height: HEADER_HEIGHT }}
          />

          {/* Gutter header label */}
          <div
            className="absolute top-0 flex items-center px-3 text-[10px] uppercase tracking-wide text-aegisaxiom-text-muted font-semibold border-r border-aegisaxiom-border"
            style={{ left: 0, width: LEFT_GUTTER, height: HEADER_HEIGHT }}
          >
            Wave
          </div>

          {/* Month markers */}
          {monthMarkers(projectStart, projectEnd, DAY_PX).map((m) => (
            <div
              key={m.x}
              className="absolute top-0 px-2 flex items-center text-[10px] uppercase text-aegisaxiom-text-muted"
              style={{ left: LEFT_GUTTER + m.x, height: HEADER_HEIGHT }}
            >
              {m.label}
            </div>
          ))}

          {/* Vertical month grid lines */}
          {monthMarkers(projectStart, projectEnd, DAY_PX).map((m) => (
            <div
              key={`grid-${m.x}`}
              className="absolute border-l border-aegisaxiom-border/40 pointer-events-none"
              style={{ left: LEFT_GUTTER + m.x, top: HEADER_HEIGHT, height: canvasHeight - HEADER_HEIGHT }}
            />
          ))}

          {/* Today marker */}
          {todayInRange ? (
            <div
              className="absolute z-10 pointer-events-none"
              style={{
                left: todayDx,
                top: HEADER_HEIGHT,
                width: 2,
                height: canvasHeight - HEADER_HEIGHT,
                backgroundColor: '#6B2BA8',
              }}
              data-testid="timeline-today"
            >
              <span className="absolute -top-3 -translate-x-1/2 left-1/2 inline-flex items-center bg-aegisaxiom-elevated text-aegisaxiom-text text-[9px] font-semibold px-1 rounded">
                TODAY
              </span>
            </div>
          ) : null}

          {/* Dependency arrows (only for waves with dates) */}
          <svg
            className="absolute pointer-events-none"
            style={{ top: HEADER_HEIGHT, left: 0 }}
            width={totalWidth}
            height={canvasHeight - HEADER_HEIGHT}
          >
            <defs>
              <marker id="arrowDep" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#6B7280" />
              </marker>
            </defs>
            {allWaves.map((w, i) =>
              (w.depends_on_wave_ids ?? []).map((depId) => {
                const j = idIndex.get(depId);
                if (j === undefined) return null;
                const dep = allWaves[j];
                if (!dep.end_date || !w.start_date) return null;
                const fromX = xFor(dep.end_date) + DAY_PX;
                const fromY = j * ROW_HEIGHT + ROW_HEIGHT / 2;
                const toX = xFor(w.start_date);
                const toY = i * ROW_HEIGHT + ROW_HEIGHT / 2;
                const midX = (fromX + toX) / 2;
                const path = `M ${fromX} ${fromY} C ${midX} ${fromY}, ${midX} ${toY}, ${toX} ${toY}`;
                return (
                  <path
                    key={`${dep.wave_id ?? dep._id}->${w.wave_id ?? w._id}`}
                    d={path}
                    fill="none"
                    stroke="#6B7280"
                    strokeWidth={1.5}
                    markerEnd="url(#arrowDep)"
                    data-testid={`timeline-arrow-${dep.wave_number}-${w.wave_number}`}
                  />
                );
              }),
            )}
          </svg>

          {/* Rows */}
          <div className="absolute left-0 right-0" style={{ top: HEADER_HEIGHT }}>
            {allWaves.map((w, i) => {
              const hasDates = !!(w.start_date && w.end_date);
              const x = hasDates ? xFor(w.start_date) : null;
              const width = hasDates
                ? Math.max(
                  (differenceInCalendarDays(parseISO(w.end_date), parseISO(w.start_date)) + 1) * DAY_PX,
                  60,
                )
                : 0;
              const dateLabel = hasDates
                ? `${format(parseISO(w.start_date), 'MMM d')} → ${format(parseISO(w.end_date), 'MMM d')}`
                : 'No dates set';
              return (
                <div
                  key={w.wave_id ?? w._id}
                  className={`relative ${i % 2 === 0 ? 'bg-transparent' : 'bg-aegisaxiom-elevated/30'}`}
                  style={{ height: ROW_HEIGHT }}
                  data-testid={`timeline-row-${w.wave_number}`}
                >
                  {/* Left gutter: wave number + status */}
                  <button
                    type="button"
                    onClick={() => onNavigateToThreads?.(w)}
                    className="absolute left-0 h-full flex items-center gap-2 px-3 border-r border-aegisaxiom-border hover:bg-aegisaxiom-elevated/60 transition-colors text-left"
                    style={{ width: LEFT_GUTTER }}
                    data-testid={`timeline-gutter-${w.wave_number}`}
                    title={`Open Wave ${w.wave_number} in Threads view`}
                  >
                    <span
                      className="font-mono font-bold text-xs text-aegisaxiom-text"
                      style={{ minWidth: 28 }}
                    >
                      W{w.wave_number}
                    </span>
                    <span
                      className="inline-block h-2 w-2 rounded-full shrink-0"
                      style={{ backgroundColor: WAVE_BORDER_COLOR[w.status] ?? '#9B5DD1' }}
                      title={STATUS_LABEL[w.status] ?? w.status}
                    />
                    <span className="text-[11px] text-aegisaxiom-text-muted truncate">
                      {STATUS_LABEL[w.status] ?? w.status}
                    </span>
                  </button>

                  {/* Bar (or "missing dates" placeholder) */}
                  {hasDates ? (
                    <button
                      type="button"
                      onClick={() => onNavigateToThreads?.(w)}
                      className={`absolute rounded-md text-[11px] truncate text-left px-2 hover:shadow-md transition-shadow text-aegisaxiom-text ${WAVE_BAR_BG[w.status] ?? 'bg-aegisaxiom-elevated'}`}
                      style={{
                        left: x,
                        top: (ROW_HEIGHT - BAR_HEIGHT) / 2,
                        height: BAR_HEIGHT,
                        width,
                        borderLeft: `3px solid ${WAVE_BORDER_COLOR[w.status] ?? '#9B5DD1'}`,
                      }}
                      data-testid={`timeline-bar-${w.wave_number}`}
                      title={`Wave ${w.wave_number} — ${w.goal ?? '(no goal)'} • ${dateLabel} • ${STATUS_LABEL[w.status] ?? w.status}`}
                    >
                      <span className="font-medium">{w.goal ?? '(no goal)'}</span>
                      <span className="text-aegisaxiom-text-muted ml-2 text-[10px]">{dateLabel}</span>
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => onNavigateToThreads?.(w)}
                      className="absolute inline-flex items-center gap-1.5 rounded-md border border-dashed border-yellow-500/50 bg-yellow-500/5 hover:bg-yellow-500/15 text-[11px] text-yellow-400 px-2 transition-colors"
                      style={{
                        left: LEFT_GUTTER + 8,
                        top: (ROW_HEIGHT - BAR_HEIGHT) / 2,
                        height: BAR_HEIGHT,
                      }}
                      data-testid={`timeline-bar-undated-${w.wave_number}`}
                      title="Open in Threads view to set start/end dates"
                    >
                      <CalendarOff size={12} />
                      No dates set — {w.goal ?? '(no goal)'}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
