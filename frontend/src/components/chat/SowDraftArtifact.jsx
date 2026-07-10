import React, { useState, useEffect } from 'react';
import { CheckCircle, Edit2, ChevronDown, ChevronUp, ExternalLink } from 'lucide-react';
import { api } from '../../lib/api';
import API_URL from '../../lib/apiConfig';
import ChatMarkdown from '../ChatMarkdown';

const STATUS_META = {
  pending: { label: 'Pending', cls: 'text-aegisaxiom-text-secondary bg-aegisaxiom-accent-soft border-aegisaxiom-border-hover/40' },
  editing: { label: 'Editing', cls: 'text-yellow-600 dark:text-yellow-400 bg-yellow-400/10 border-yellow-400/30' },
  approved: { label: 'Approved', cls: 'text-emerald-600 dark:text-emerald-400 bg-emerald-400/10 border-emerald-400/30' },
};

function SectionCard({ section, opportunityId, disabled, onStatusChange }) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState(section.content || '');
  const [status, setStatus] = useState(section.status || 'pending');
  const [saving, setSaving] = useState(false);
  const [approving, setApproving] = useState(false);
  const [error, setError] = useState(null);

  const statusMeta = STATUS_META[status] || STATUS_META.pending;

  const handleEdit = async () => {
    if (editing) {
      // Save the edit
      setSaving(true);
      setError(null);
      try {
        const res = await api.post(
          `${API_URL}/api/orion/sows/${opportunityId}/sections/${section.section_id}/edit`,
          { content },
        );
        if (res.ok) {
          setEditing(false);
          setStatus('editing');
        } else {
          const data = await res.json();
          setError(data?.detail || 'Failed to save. Please try again.');
        }
      } catch {
        setError('Connection error. Please try again.');
      } finally {
        setSaving(false);
      }
    } else {
      setEditing(true);
      setExpanded(true);
    }
  };

  const handleApprove = async () => {
    setApproving(true);
    setError(null);
    try {
      const res = await api.post(
        `${API_URL}/api/orion/sows/${opportunityId}/sections/${section.section_id}/approve`,
        {},
      );
      if (res.ok) {
        setStatus('approved');
        setEditing(false);
        setExpanded(false);
        onStatusChange?.('approved');
      } else {
        const data = await res.json();
        setError(data?.detail || 'Failed to approve. Please try again.');
      }
    } catch {
      setError('Connection error. Please try again.');
    } finally {
      setApproving(false);
    }
  };

  const isApproved = status === 'approved';

  return (
    <div className={`rounded-xl border overflow-hidden transition-colors ${
      isApproved ? 'border-emerald-500/25 bg-aegisaxiom-elevated' : 'border-aegisaxiom-border bg-aegisaxiom-elevated'
    }`}>
      {/* Card header */}
      <div
        className="flex items-center gap-3 px-5 py-3 cursor-pointer hover:bg-aegisaxiom-accent-muted transition-colors"
        onClick={() => setExpanded(e => !e)}
      >
        <div className="flex-1 min-w-0 flex items-center gap-2.5">
          <span className="text-sm font-semibold text-aegisaxiom-text truncate">{section.title}</span>
          <span className={`shrink-0 text-xs px-2 py-0.5 rounded-full border ${statusMeta.cls}`}>
            {statusMeta.label}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {!disabled && !isApproved && (
            <>
              <button
                type="button"
                onClick={e => { e.stopPropagation(); handleEdit(); }}
                disabled={saving}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${
                  editing
                    ? 'border-violet-500/50 bg-violet-50 dark:bg-violet-500/10 text-violet-700 dark:text-violet-300 hover:bg-violet-50 dark:bg-violet-500/20'
                    : 'border-aegisaxiom-border-hover/50 bg-aegisaxiom-accent-soft text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover'
                } disabled:opacity-50`}
              >
                {saving ? (
                  <div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                ) : (
                  <Edit2 size={12} />
                )}
                {editing ? (saving ? 'Saving…' : 'Save') : 'Edit'}
              </button>
              <button
                type="button"
                onClick={e => { e.stopPropagation(); handleApprove(); }}
                disabled={approving}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-emerald-500/40 bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 text-xs font-medium hover:bg-emerald-50 dark:bg-emerald-500/20 disabled:opacity-50 transition-all"
              >
                {approving ? (
                  <div className="w-3 h-3 border-2 border-emerald-400/30 border-t-emerald-400 rounded-full animate-spin" />
                ) : (
                  <CheckCircle size={12} />
                )}
                {approving ? 'Approving…' : 'Approve'}
              </button>
            </>
          )}
          {isApproved && <CheckCircle size={15} className="text-emerald-600 dark:text-emerald-400" />}
          {expanded
            ? <ChevronUp size={15} className="text-aegisaxiom-text-secondary" />
            : <ChevronDown size={15} className="text-aegisaxiom-text-secondary" />}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="px-5 pb-1 text-xs text-red-600 dark:text-red-400">{error}</div>
      )}

      {/* Content area */}
      {expanded && (
        <div className="border-t border-aegisaxiom-border px-5 py-4">
          {editing ? (
            <textarea
              rows={12}
              value={content}
              onChange={e => setContent(e.target.value)}
              className="w-full bg-aegisaxiom-surface border border-aegisaxiom-border focus:border-violet-500 rounded-xl px-4 py-3 text-sm text-aegisaxiom-text outline-none transition-colors resize-y font-mono leading-relaxed"
            />
          ) : (
            <div className="text-sm text-aegisaxiom-text-secondary leading-relaxed prose-sm max-h-[50vh] overflow-y-auto">
              <ChatMarkdown content={content} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function SowDraftArtifact({ artifact }) {
  const { payload } = artifact;
  const {
    opportunity_id,
    sow_id,
    client_name,
    sections = [],
  } = payload;

  const [sectionStatuses, setSectionStatuses] = useState(() => {
    const initial = {};
    sections.forEach(s => { initial[s.section_id] = s.approved ? 'approved' : (s.status || 'pending'); });
    return initial;
  });

  // Fetch live approval status from the SOW document (artifact payload is a frozen snapshot)
  useEffect(() => {
    if (!opportunity_id) return;
    api.get(`${API_URL}/api/orion/sows/${opportunity_id}`)
      .then(res => res.ok ? res.json() : null)
      .then(sow => {
        if (!sow?.sections) return;
        setSectionStatuses(prev => {
          const next = { ...prev };
          for (const [id, sec] of Object.entries(sow.sections)) {
            if (sec.status === 'approved' || sec.approved) next[id] = 'approved';
          }
          return next;
        });
      })
      .catch(() => {});
  }, [opportunity_id]);

  const approvedCount = Object.values(sectionStatuses).filter(s => s === 'approved').length;
  const allApproved = approvedCount === sections.length && sections.length > 0;

  // Finalize button triggers the Google Doc creation endpoint
  const [finalizing, setFinalizing] = useState(false);
  const [finalizeError, setFinalizeError] = useState(null);
  const [docUrl, setDocUrl] = useState(payload.doc_url || null);

  const canFinalize = allApproved && !docUrl;

  const handleFinalize = async () => {
    setFinalizing(true);
    setFinalizeError(null);
    try {
      const res = await api.post(
        `${API_URL}/api/orion/sows/${opportunity_id}/finalize`,
        { sow_id },
      );
      if (res.ok) {
        const data = await res.json();
        setDocUrl(data?.doc_url || null);
      } else {
        const data = await res.json();
        setFinalizeError(data?.detail || 'Finalization failed. Please try again.');
      }
    } catch {
      setFinalizeError('Connection error. Please try again.');
    } finally {
      setFinalizing(false);
    }
  };

  return (
    <div className="my-3 rounded-xl border border-violet-500/30 bg-aegisaxiom-elevated overflow-hidden">
      <div className="px-5 py-4 border-b border-aegisaxiom-border flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-aegisaxiom-text">SOW Draft</h3>
          {client_name && (
            <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">{client_name}</p>
          )}
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <span className="text-xs text-aegisaxiom-text-muted font-mono">
            {approvedCount}/{sections.length} approved
          </span>
        </div>
      </div>

      <div className="px-5 py-4 space-y-2.5">
        {sections.length === 0 ? (
          <p className="text-sm text-aegisaxiom-text-muted text-center py-4">No sections generated yet.</p>
        ) : (
          sections.map(section => (
            <SectionCard
              key={section.section_id}
              section={section}
              opportunityId={opportunity_id}
              disabled={!!docUrl}
              onStatusChange={(newStatus) => {
                setSectionStatuses(prev => ({ ...prev, [section.section_id]: newStatus }));
              }}
            />
          ))
        )}
      </div>

      {/* Finalize footer */}
      {(canFinalize || docUrl || finalizeError) && (
        <div className="border-t border-aegisaxiom-border px-5 py-4 flex items-center justify-between gap-3">
          {docUrl ? (
            <a
              href={docUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-sm font-medium transition-colors"
            >
              <ExternalLink size={14} />
              Open in Google Docs
            </a>
          ) : (
            <>
              {finalizeError && (
                <p className="text-xs text-red-600 dark:text-red-400">{finalizeError}</p>
              )}
              {canFinalize && (
                <button
                  type="button"
                  onClick={handleFinalize}
                  disabled={finalizing}
                  className="flex items-center gap-2 px-5 py-2 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 text-aegisaxiom-text text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-all ml-auto"
                >
                  {finalizing ? (
                    <>
                      <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      Finalizing…
                    </>
                  ) : (
                    'Finalize to Google Doc'
                  )}
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
