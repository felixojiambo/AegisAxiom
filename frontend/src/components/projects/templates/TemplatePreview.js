import React, { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Calendar } from 'lucide-react';
import { format, parseISO } from 'date-fns';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';

const PRIORITY_CLS = {
  critical: 'text-red-400',
  high: 'text-orange-400',
  medium: 'text-yellow-400',
  low: 'text-aegisaxiom-text-muted',
};

function PriorityDot({ priority }) {
  return (
    <span
      className={`w-2 h-2 rounded-full inline-block ${
        priority === 'critical' ? 'bg-red-400'
        : priority === 'high' ? 'bg-orange-400'
        : priority === 'medium' ? 'bg-yellow-400'
        : 'bg-aegisaxiom-text-muted'
      }`}
      aria-label={priority}
    />
  );
}

function StatusChip({ status }) {
  const cls =
    status === 'complete' ? 'bg-green-500/15 text-green-400'
    : status === 'in_progress' ? 'bg-blue-500/15 text-blue-400'
    : status === 'blocked' ? 'bg-red-500/15 text-red-400'
    : 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted';
  return (
    <span className={`inline-flex items-center h-5 px-2 rounded text-[10px] font-medium ${cls}`}>
      {status?.replace(/_/g, ' ') ?? 'not started'}
    </span>
  );
}

function PreviewTab({ label, active, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`h-8 px-3 text-xs font-medium transition-colors border-b-2 -mb-px ${
        active
          ? 'border-violet-500 text-aegisaxiom-text'
          : 'border-transparent text-aegisaxiom-text-muted hover:text-aegisaxiom-text'
      }`}
    >
      {label}
    </button>
  );
}

/**
 * TemplatePreview — fetches a dry-run preview for a template given a start date.
 *
 * Props:
 *   templateId    string
 *   startDate     string  (yyyy-MM-dd, may be empty)
 *   onEndDate     (endDateISO: string|null) => void   optional callback
 */
export default function TemplatePreview({ templateId, startDate, onEndDate }) {
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState('tasks');

  const fetchPreview = useCallback(async () => {
    if (!templateId) return;
    setLoading(true);
    setError(null);
    try {
      // start_date is required by the preview endpoint; default to today
      // when the caller didn't supply one (gallery preview path).
      const effectiveStart = startDate || new Date().toISOString().slice(0, 10);
      const qs = `?start_date=${effectiveStart}`;
      const res = await api.get(`${API_URL}/api/projects/templates/${templateId}/preview${qs}`);
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        const detail = d?.detail;
        let msg = 'Preview failed';
        if (typeof detail === 'string') msg = detail;
        else if (Array.isArray(detail)) {
          const msgs = detail.map((e) => (typeof e === 'string' ? e : e?.msg)).filter(Boolean);
          if (msgs.length) msg = msgs.join('; ');
        } else if (detail?.message) msg = detail.message;
        throw new Error(msg);
      }
      const data = await res.json();
      setPreview(data);
      onEndDate?.(data.computed_end_date ?? null);
    } catch (e) {
      setError(e.message ?? 'Could not load preview');
    } finally {
      setLoading(false);
    }
  }, [templateId, startDate, onEndDate]);

  useEffect(() => { fetchPreview(); }, [fetchPreview]);

  if (!templateId) return null;

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8" data-testid="template-preview-loading">
        <div className="animate-spin w-6 h-6 rounded-full border-2 border-violet-500 border-t-transparent" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center gap-2 py-4 text-sm text-red-400" data-testid="template-preview-error">
        <AlertTriangle size={15} />
        {error}
      </div>
    );
  }

  if (!preview) return null;

  const tasks = preview.tasks ?? [];
  const phases = preview.phases ?? [];
  const sprints = preview.sprints ?? [];
  const warnings = preview.warnings ?? [];

  const hasTabs = phases.length > 0 || sprints.length > 0;
  const tabs = [
    { id: 'tasks', label: `Tasks (${tasks.length})` },
    ...(phases.length > 0 ? [{ id: 'phases', label: `Phases (${phases.length})` }] : []),
    ...(sprints.length > 0 ? [{ id: 'sprints', label: `Sprints (${sprints.length})` }] : []),
  ];

  return (
    <div data-testid="template-preview">
      {/* Sticky header */}
      <div className="flex items-center gap-4 mb-3 flex-wrap">
        <h4 className="text-sm font-semibold text-aegisaxiom-text">{preview.template_name ?? 'Preview'}</h4>
        {preview.computed_end_date && (
          <span className="inline-flex items-center gap-1.5 text-xs text-aegisaxiom-text-muted">
            <Calendar size={12} />
            Ends {format(parseISO(preview.computed_end_date), 'MMM d, yyyy')}
          </span>
        )}
      </div>

      {/* Warnings */}
      {warnings.length > 0 && (
        <div className="rounded-md border border-yellow-500/30 bg-yellow-500/10 px-3 py-2 mb-3 space-y-1" data-testid="template-preview-warnings">
          {warnings.map((w, i) => (
            <p key={i} className="text-xs text-yellow-300 flex items-center gap-1.5">
              <AlertTriangle size={11} className="shrink-0" />
              {w}
            </p>
          ))}
        </div>
      )}

      {/* Tab strip */}
      {hasTabs && (
        <div className="flex items-center gap-1 border-b border-aegisaxiom-border mb-3">
          {tabs.map((t) => (
            <PreviewTab
              key={t.id}
              label={t.label}
              active={activeTab === t.id}
              onClick={() => setActiveTab(t.id)}
            />
          ))}
        </div>
      )}

      {/* Tasks table */}
      {(!hasTabs || activeTab === 'tasks') && (
        tasks.length === 0 ? (
          <p className="text-xs text-aegisaxiom-text-muted py-2" data-testid="template-preview-no-tasks">
            No tasks defined in this template.
          </p>
        ) : (
          <div className="overflow-auto max-h-[320px] rounded-md border border-aegisaxiom-border" data-testid="template-preview-tasks">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-aegisaxiom-elevated border-b border-aegisaxiom-border">
                <tr className="h-8 text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted">
                  <th className="text-left px-3 font-medium">Task</th>
                  <th className="text-left px-3 font-medium">Priority</th>
                  <th className="text-left px-3 font-medium">Phase</th>
                  <th className="text-left px-3 font-medium">Due</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-aegisaxiom-border">
                {tasks.map((t, i) => (
                  <tr key={i} className="hover:bg-aegisaxiom-elevated/40">
                    <td className="px-3 py-2 text-aegisaxiom-text font-medium">{t.title}</td>
                    <td className="px-3 py-2">
                      <span className={`inline-flex items-center gap-1 ${PRIORITY_CLS[t.priority] ?? 'text-aegisaxiom-text-muted'}`}>
                        <PriorityDot priority={t.priority} />
                        {t.priority ?? 'medium'}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-aegisaxiom-text-secondary">{t.phase ?? '—'}</td>
                    <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                      {t.due_date ? format(parseISO(t.due_date), 'MMM d') : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      {/* Phases tab */}
      {activeTab === 'phases' && phases.length > 0 && (
        <div className="overflow-auto max-h-[320px] rounded-md border border-aegisaxiom-border" data-testid="template-preview-phases">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-aegisaxiom-elevated border-b border-aegisaxiom-border">
              <tr className="h-8 text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted">
                <th className="text-left px-3 font-medium">Phase</th>
                <th className="text-left px-3 font-medium">Start</th>
                <th className="text-left px-3 font-medium">End</th>
                <th className="text-left px-3 font-medium">Tasks</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-aegisaxiom-border">
              {phases.map((p, i) => (
                <tr key={i} className="hover:bg-aegisaxiom-elevated/40">
                  <td className="px-3 py-2 font-medium text-aegisaxiom-text">{p.name}</td>
                  <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                    {p.start_date ? format(parseISO(p.start_date), 'MMM d') : '—'}
                  </td>
                  <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                    {p.end_date ? format(parseISO(p.end_date), 'MMM d') : '—'}
                  </td>
                  <td className="px-3 py-2 text-aegisaxiom-text-muted">{p.task_count ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Sprints tab */}
      {activeTab === 'sprints' && sprints.length > 0 && (
        <div className="overflow-auto max-h-[320px] rounded-md border border-aegisaxiom-border" data-testid="template-preview-sprints">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-aegisaxiom-elevated border-b border-aegisaxiom-border">
              <tr className="h-8 text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted">
                <th className="text-left px-3 font-medium">Sprint</th>
                <th className="text-left px-3 font-medium">Start</th>
                <th className="text-left px-3 font-medium">End</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-aegisaxiom-border">
              {sprints.map((s, i) => (
                <tr key={i} className="hover:bg-aegisaxiom-elevated/40">
                  <td className="px-3 py-2 font-medium text-aegisaxiom-text">{s.name ?? `Sprint ${i + 1}`}</td>
                  <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                    {s.start_date ? format(parseISO(s.start_date), 'MMM d') : '—'}
                  </td>
                  <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                    {s.end_date ? format(parseISO(s.end_date), 'MMM d') : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
