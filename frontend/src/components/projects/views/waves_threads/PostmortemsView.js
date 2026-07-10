import React, { useEffect, useMemo, useState } from 'react';
import { format, parseISO } from 'date-fns';
import { api, unwrap } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import LoadingSpinner from '../../widgets/LoadingSpinner';

export default function PostmortemsView({ project }) {
  const [waves, setWaves] = useState([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(null);

  const projectId = project?.project_id ?? project?._id;

  useEffect(() => {
    if (!projectId) return;
    setLoading(true);
    api.get(`${API_URL}/api/projects/waves?project_id=${projectId}`)
      .then((res) => res.json())
      .then((data) => setWaves(unwrap(data)))
      .finally(() => setLoading(false));
  }, [projectId]);

  const list = useMemo(
    () =>
      waves
        .filter((w) => w.postmortem != null)
        .sort((a, b) => b.wave_number - a.wave_number),
    [waves],
  );

  if (loading) {
    return (
      <div className="min-h-[40vh] flex items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  return (
    <section data-testid="postmortems-view" className="px-6 py-4">
      <header className="mb-4 flex items-baseline gap-3">
        <h2 className="text-sm font-bold text-aegisaxiom-text">Postmortems</h2>
        <span className="text-[11px] text-aegisaxiom-text-muted">{list.length} recorded</span>
      </header>

      {list.length === 0 ? (
        <div className="rounded-md border border-dashed border-aegisaxiom-border p-8 text-center" data-testid="postmortems-empty">
          <p className="text-sm text-aegisaxiom-text-muted">No postmortems yet. They appear here once a wave is closed.</p>
        </div>
      ) : (
        <ul className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface overflow-hidden">
          {list.map((w) => (
            <li
              key={w.wave_id ?? w._id}
              className="flex items-center gap-3 px-4 py-3 border-b last:border-b-0 border-aegisaxiom-border"
              data-testid={`postmortem-row-${w.wave_number}`}
            >
              <span className="font-mono text-xs font-bold text-aegisaxiom-text">WAVE {w.wave_number}</span>
              <span className="flex-1 truncate text-sm text-aegisaxiom-text">{w.goal}</span>
              <span className="text-[11px] text-aegisaxiom-text-muted">
                {(w.start_date && w.end_date)
                  ? `${format(parseISO(w.start_date), 'MMM d')} – ${format(parseISO(w.end_date), 'MMM d')}`
                  : '—'}
              </span>
              <button
                type="button"
                onClick={() => setOpen(w)}
                className="btn-secondary h-7 px-3 text-xs"
                data-testid={`postmortem-view-${w.wave_number}`}
              >
                View
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* Viewer modal */}
      {open ? (
        <div
          className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center"
          onClick={() => setOpen(null)}
          data-testid="postmortem-dialog-backdrop"
        >
          <div
            className="glass-card w-[640px] max-h-[80vh] overflow-y-auto p-5"
            onClick={(e) => e.stopPropagation()}
            data-testid="postmortem-dialog"
            role="dialog"
          >
            <h3 className="text-sm font-bold text-aegisaxiom-text mb-3">
              Wave {open.wave_number} — {open.goal}
            </h3>
            {open.postmortem ? (
              <div className="space-y-3">
                {Object.entries(open.postmortem).map(([k, v]) => (
                  <section key={k}>
                    <h4 className="text-[11px] uppercase font-bold text-aegisaxiom-text-muted mb-1">{k}</h4>
                    <pre className="text-[12px] bg-aegisaxiom-elevated rounded-md p-2 max-h-[200px] overflow-y-auto whitespace-pre-wrap break-words text-aegisaxiom-text">
                      {typeof v === 'string' ? v : JSON.stringify(v, null, 2)}
                    </pre>
                  </section>
                ))}
              </div>
            ) : null}
            <div className="flex justify-end mt-4">
              <button type="button" onClick={() => setOpen(null)} className="btn-secondary h-8 px-3 text-xs">
                Close
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
