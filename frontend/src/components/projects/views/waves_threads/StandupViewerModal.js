import React, { useState, useEffect } from 'react';
import { Edit2, Sparkles, Trash2, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import { extractTaskErrorMessage } from '@/lib/safeXhr';
import API_URL from '@/lib/apiConfig';
import ConfirmModal from '@/components/shared/ConfirmModal';
import { usePermissions } from '@/hooks/usePermissions';

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
 * Read-only modal for an existing standup. Permission-gated edit/delete
 * affordances. Edit can update summary + structured bullets only —
 * raw_content is immutable post-create per the backend contract.
 */
export default function StandupViewerModal({ standup, onClose, onChanged }) {
  const { hasPermission } = usePermissions();
  const canEdit = hasPermission('projects.standup.edit');
  const canDelete = hasPermission('projects.standup.delete');
  const canSummarize = hasPermission('projects.standup.create');

  const [editing, setEditing] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [summarizing, setSummarizing] = useState(false);
  const [current, setCurrent] = useState(standup);

  useEffect(() => {
    setCurrent(standup);
    setEditing(false);
  }, [standup?.standup_id]);

  if (!current) return null;

  const standupId = current.standup_id;
  const sourceClass = SOURCE_BADGE_COLORS[current.source] ?? 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted';
  const structured = current.structured ?? null;

  const handleDelete = async () => {
    try {
      const res = await api.delete(`${API_URL}/api/projects/standups/${standupId}`);
      if (!res.ok) {
        const msg = await extractTaskErrorMessage(res, 'Could not delete standup.');
        toast.error(msg);
        return;
      }
      toast.success('Standup deleted');
      onChanged?.();
      onClose();
    } catch {
      toast.error('Could not delete standup.');
    }
  };

  const handleSummarize = async () => {
    setSummarizing(true);
    try {
      const res = await api.post(`${API_URL}/api/projects/standups/${standupId}/summarize`, {});
      if (!res.ok) {
        const msg = await extractTaskErrorMessage(res, 'Could not summarize standup.');
        toast.error(msg);
        return;
      }
      const data = await res.json();
      setCurrent(data);
      onChanged?.();
      toast.success('Summary generated');
    } catch {
      toast.error('Could not summarize standup.');
    } finally {
      setSummarizing(false);
    }
  };

  return (
    <>
      <div
        className="fixed inset-0 bg-black/50 z-40 flex items-center justify-center p-4"
        onClick={onClose}
        data-testid="standup-viewer-backdrop"
      >
        <div
          className="glass-card w-[min(820px,98vw)] max-h-[92vh] overflow-hidden flex flex-col"
          onClick={(e) => e.stopPropagation()}
          role="dialog"
          aria-label="Standup viewer"
          data-testid="standup-viewer"
        >
          {/* Header */}
          <header className="px-5 py-3 border-b border-aegisaxiom-border flex items-center gap-3 shrink-0">
            <h3 className="text-sm font-bold text-aegisaxiom-text">Standup · {current.date}</h3>
            <span className={`inline-flex items-center h-5 px-2 rounded text-[10px] font-medium ${sourceClass}`}>
              {SOURCE_LABELS[current.source] ?? current.source}
            </span>
            <div className="ml-auto flex items-center gap-1.5">
              {canSummarize && current.source !== 'manual' ? (
                <button
                  type="button"
                  onClick={handleSummarize}
                  disabled={summarizing}
                  className="btn-secondary h-7 px-2 inline-flex items-center gap-1.5 text-xs disabled:opacity-60"
                  data-testid="standup-viewer-summarize"
                  title={structured ? 'Re-run summary' : 'Summarize with PMO AI'}
                >
                  <Sparkles size={11} />
                  {summarizing ? 'Summarizing…' : structured ? 'Re-summarize' : 'Summarize'}
                </button>
              ) : null}
              {canEdit ? (
                <button
                  type="button"
                  onClick={() => setEditing((v) => !v)}
                  className="h-7 w-7 inline-flex items-center justify-center rounded text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated"
                  aria-label="Edit"
                  data-testid="standup-viewer-edit"
                >
                  <Edit2 size={12} />
                </button>
              ) : null}
              {canDelete ? (
                <button
                  type="button"
                  onClick={() => setConfirmDeleteOpen(true)}
                  className="h-7 w-7 inline-flex items-center justify-center rounded text-aegisaxiom-text-muted hover:text-red-400 hover:bg-red-500/10"
                  aria-label="Delete"
                  data-testid="standup-viewer-delete"
                >
                  <Trash2 size={12} />
                </button>
              ) : null}
              <button
                type="button"
                onClick={onClose}
                className="h-7 w-7 inline-flex items-center justify-center rounded text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
                aria-label="Close"
              >
                <X size={14} />
              </button>
            </div>
          </header>

          {/* Body */}
          <div className="overflow-y-auto p-5 space-y-5">
            {editing ? (
              <EditForm
                standup={current}
                onCancel={() => setEditing(false)}
                onSaved={(updated) => {
                  setCurrent(updated);
                  setEditing(false);
                  onChanged?.();
                }}
              />
            ) : (
              <ReadOnlyBody standup={current} />
            )}
          </div>
        </div>
      </div>

      <ConfirmModal
        isOpen={confirmDeleteOpen}
        title="Delete this standup?"
        message="This permanently removes the standup record and its AI-generated summary. The raw transcript cannot be recovered."
        confirmText="Delete standup"
        cancelText="Cancel"
        variant="danger"
        onConfirm={handleDelete}
        onCancel={() => setConfirmDeleteOpen(false)}
      />
    </>
  );
}

function ReadOnlyBody({ standup }) {
  const structured = standup.structured ?? null;
  const hasStructured = structured && (
    (structured.shipped_yesterday?.length ?? 0)
    + (structured.working_today?.length ?? 0)
    + (structured.blockers?.length ?? 0)
  ) > 0;

  return (
    <>
      {standup.summary ? (
        <section data-testid="standup-summary">
          <h4 className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-1.5">Summary</h4>
          <p className="text-sm text-aegisaxiom-text leading-relaxed">{standup.summary}</p>
        </section>
      ) : null}

      {hasStructured ? (
        <section data-testid="standup-structured" className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <BulletColumn label="Shipped yesterday" items={structured.shipped_yesterday} accent="text-green-400" />
          <BulletColumn label="Working today" items={structured.working_today} accent="text-blue-400" />
          <BulletColumn label="Blockers" items={structured.blockers} accent="text-red-400" />
        </section>
      ) : null}

      <section data-testid="standup-raw">
        <h4 className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-1.5">
          Raw content
        </h4>
        <div className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface p-4 prose prose-invert prose-sm max-w-none text-aegisaxiom-text">
          <ReactMarkdown>
            {standup.raw_content || '_(empty)_'}
          </ReactMarkdown>
        </div>
      </section>
    </>
  );
}

function BulletColumn({ label, items, accent }) {
  const list = items ?? [];
  return (
    <div className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface p-3">
      <h5 className={`text-[10px] uppercase tracking-wider font-bold mb-2 ${accent}`}>{label}</h5>
      {list.length === 0 ? (
        <p className="text-[11px] text-aegisaxiom-text-muted italic">No items</p>
      ) : (
        <ul className="space-y-1.5">
          {list.map((item, idx) => (
            <li key={idx} className="text-[12px] text-aegisaxiom-text leading-snug flex gap-1.5">
              <span className="text-aegisaxiom-text-muted mt-0.5">·</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EditForm({ standup, onCancel, onSaved }) {
  const [summary, setSummary] = useState(standup.summary ?? '');
  const [shipped, setShipped] = useState((standup.structured?.shipped_yesterday ?? []).join('\n'));
  const [today, setToday] = useState((standup.structured?.working_today ?? []).join('\n'));
  const [blockers, setBlockers] = useState((standup.structured?.blockers ?? []).join('\n'));
  const [saving, setSaving] = useState(false);

  const splitLines = (s) => s.split('\n').map((x) => x.trim()).filter(Boolean);

  const handleSave = async () => {
    setSaving(true);
    try {
      const payload = {
        summary: summary.trim() || null,
        structured: {
          shipped_yesterday: splitLines(shipped),
          working_today: splitLines(today),
          blockers: splitLines(blockers),
        },
      };
      const res = await api.patch(`${API_URL}/api/projects/standups/${standup.standup_id}`, payload);
      if (!res.ok) {
        const msg = await extractTaskErrorMessage(res, 'Could not save changes.');
        toast.error(msg);
        return;
      }
      const data = await res.json();
      toast.success('Standup updated');
      onSaved(data);
    } catch {
      toast.error('Could not save changes.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4" data-testid="standup-edit-form">
      <p className="text-[11px] text-aegisaxiom-text-muted">
        Edit the AI-generated summary and bullets. Raw content is immutable —
        re-summarize from the original transcript if you need to start over.
      </p>
      <div>
        <label className="block text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-1">Summary</label>
        <textarea
          rows={2}
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          className="aegisaxiom-input w-full text-sm resize-y"
          placeholder="One-sentence recap"
          data-testid="standup-edit-summary"
        />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <BulletEditor label="Shipped yesterday" value={shipped} onChange={setShipped} testid="standup-edit-shipped" />
        <BulletEditor label="Working today" value={today} onChange={setToday} testid="standup-edit-today" />
        <BulletEditor label="Blockers" value={blockers} onChange={setBlockers} testid="standup-edit-blockers" />
      </div>
      <div className="flex gap-2 justify-end pt-2 border-t border-aegisaxiom-border">
        <button type="button" onClick={onCancel} className="btn-secondary h-8 px-3 text-xs">
          Cancel
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="btn-primary h-8 px-3 text-xs disabled:opacity-60"
          data-testid="standup-edit-save"
        >
          {saving ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </div>
  );
}

function BulletEditor({ label, value, onChange, testid }) {
  return (
    <div>
      <label className="block text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-1">{label}</label>
      <textarea
        rows={5}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="aegisaxiom-input w-full text-[12px] resize-y font-mono"
        placeholder="One bullet per line"
        data-testid={testid}
      />
    </div>
  );
}
