import React, { useEffect, useState } from 'react';
import { Sparkles, Upload, X } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import { extractTaskErrorMessage } from '@/lib/safeXhr';
import API_URL from '@/lib/apiConfig';
import DateInput from '@/components/ui/DateInput';

const TABS = [
  { id: 'manual', label: 'Manual', help: 'Type the three answers yourself.' },
  { id: 'paste', label: 'Paste transcript', help: 'Paste a meeting transcript; AI will extract bullets.' },
  { id: 'upload', label: 'Upload .md', help: 'Upload a Fireflies markdown export.' },
];

function todayIso() {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

/**
 * Modal for creating a new standup. 3-tab UX:
 *  - Manual: 3 textareas, packed into raw_content + structured directly.
 *  - Paste: textarea, raw_content only (offer Summarize after save).
 *  - Upload: .md file picker, raw_content only (offer Summarize after save).
 *
 * The modal stays open after a non-manual save and reveals a "Summarize"
 * button so the user can run AI extraction without navigating away.
 */
export default function NewStandupModal({
  open,
  onOpenChange,
  project,
  waves,
  defaultWaveId,
  onCreated,
}) {
  const [activeTab, setActiveTab] = useState('manual');
  const [date, setDate] = useState(todayIso());
  const [waveId, setWaveId] = useState(defaultWaveId ?? '');

  // Manual tab state
  const [shipped, setShipped] = useState('');
  const [today, setToday] = useState('');
  const [blockers, setBlockers] = useState('');

  // Paste tab state
  const [pasted, setPasted] = useState('');

  // Upload tab state
  const [uploadedName, setUploadedName] = useState('');
  const [uploadedContent, setUploadedContent] = useState('');

  // Post-save state
  const [savedStandup, setSavedStandup] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [summarizing, setSummarizing] = useState(false);

  const isWT = project?.project_type === 'waves_threads';

  useEffect(() => {
    if (open) {
      setActiveTab('manual');
      setDate(todayIso());
      setWaveId(defaultWaveId ?? '');
      setShipped('');
      setToday('');
      setBlockers('');
      setPasted('');
      setUploadedName('');
      setUploadedContent('');
      setSavedStandup(null);
    }
  }, [open, defaultWaveId]);

  if (!open) return null;

  const projectId = project?.project_id ?? project?._id;

  const splitLines = (s) => s.split('\n').map((x) => x.trim()).filter(Boolean);

  const buildPayload = () => {
    if (activeTab === 'manual') {
      const shippedList = splitLines(shipped);
      const todayList = splitLines(today);
      const blockersList = splitLines(blockers);
      const sections = [];
      if (shippedList.length) sections.push(`## Shipped yesterday\n${shippedList.map((x) => `- ${x}`).join('\n')}`);
      if (todayList.length) sections.push(`## Working today\n${todayList.map((x) => `- ${x}`).join('\n')}`);
      if (blockersList.length) sections.push(`## Blockers\n${blockersList.map((x) => `- ${x}`).join('\n')}`);
      return {
        source: 'manual',
        raw_content: sections.join('\n\n') || '(no entries)',
        structured: {
          shipped_yesterday: shippedList,
          working_today: todayList,
          blockers: blockersList,
        },
      };
    }
    if (activeTab === 'paste') {
      return { source: 'transcript_paste', raw_content: pasted };
    }
    return { source: 'fireflies_md', raw_content: uploadedContent };
  };

  const validate = (payload) => {
    if (isWT && !waveId) {
      toast.error('Pick a wave for this standup.');
      return false;
    }
    if (!date) {
      toast.error('Pick a date.');
      return false;
    }
    if (!payload.raw_content?.trim()) {
      toast.error(
        activeTab === 'manual'
          ? 'Fill in at least one section before saving.'
          : activeTab === 'paste'
          ? 'Paste a transcript before saving.'
          : 'Upload a .md file before saving.',
      );
      return false;
    }
    return true;
  };

  const handleSave = async () => {
    const payload = buildPayload();
    if (!validate(payload)) return;
    setSubmitting(true);
    try {
      const body = {
        project_id: projectId,
        wave_id: isWT ? waveId : null,
        date,
        ...payload,
      };
      const res = await api.post(`${API_URL}/api/projects/standups`, body);
      if (!res.ok) {
        const msg = await extractTaskErrorMessage(res, 'Could not save standup.');
        toast.error(msg);
        return;
      }
      const data = await res.json();
      toast.success('Standup saved');
      setSavedStandup(data);
      onCreated?.(data);

      // Manual standups are already structured — no need to keep modal open.
      if (activeTab === 'manual') {
        onOpenChange(false);
      }
    } catch {
      toast.error('Could not save standup.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleSummarize = async () => {
    if (!savedStandup) return;
    setSummarizing(true);
    try {
      const res = await api.post(
        `${API_URL}/api/projects/standups/${savedStandup.standup_id}/summarize`,
        {},
      );
      if (!res.ok) {
        const msg = await extractTaskErrorMessage(res, 'Could not summarize standup.');
        toast.error(msg);
        return;
      }
      const data = await res.json();
      toast.success('Summary generated');
      setSavedStandup(data);
      onCreated?.(data);
    } catch {
      toast.error('Could not summarize standup.');
    } finally {
      setSummarizing(false);
    }
  };

  const handleFile = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!/\.md$/i.test(file.name) && file.type !== 'text/markdown') {
      toast.error('Pick a .md file.');
      return;
    }
    const reader = new FileReader();
    reader.onload = (ev) => {
      setUploadedName(file.name);
      setUploadedContent(String(ev.target?.result ?? ''));
    };
    reader.onerror = () => toast.error('Could not read file.');
    reader.readAsText(file);
  };

  const showSummarize = savedStandup && activeTab !== 'manual';
  const isSavedAndStructured = savedStandup?.structured && (
    (savedStandup.structured.shipped_yesterday?.length ?? 0)
    + (savedStandup.structured.working_today?.length ?? 0)
    + (savedStandup.structured.blockers?.length ?? 0)
  ) > 0;

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
      onClick={() => onOpenChange(false)}
      data-testid="new-standup-backdrop"
    >
      <div
        className="glass-card w-[min(820px,98vw)] max-h-[92vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="New standup"
        data-testid="new-standup-modal"
      >
        <header className="px-5 py-3 border-b border-aegisaxiom-border flex items-center gap-3 shrink-0">
          <h3 className="text-sm font-bold text-aegisaxiom-text">New Standup</h3>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="ml-auto h-7 w-7 inline-flex items-center justify-center text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
            aria-label="Close"
          >
            <X size={14} />
          </button>
        </header>

        {/* Date + Wave selector */}
        <div className="px-5 py-3 border-b border-aegisaxiom-border flex items-end gap-3 shrink-0 bg-aegisaxiom-surface">
          <div>
            <label className="block text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-1">Date</label>
            <DateInput
              value={date}
              onChange={setDate}
              data-testid="new-standup-date"
              placeholder="Standup date"
            />
          </div>
          {isWT ? (
            <div className="flex-1">
              <label className="block text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-1">Wave</label>
              <select
                value={waveId}
                onChange={(e) => setWaveId(e.target.value)}
                className="aegisaxiom-input h-8 text-sm w-full"
                data-testid="new-standup-wave"
              >
                <option value="">Select a wave…</option>
                {(waves ?? []).map((w) => (
                  <option key={w.wave_id ?? w._id} value={w.wave_id ?? w._id}>
                    Wave {w.wave_number} — {w.status} {w.goal ? `· ${w.goal}` : ''}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
        </div>

        {/* Tabs */}
        {!savedStandup ? (
          <>
            <nav className="px-5 pt-3 flex gap-1 border-b border-aegisaxiom-border shrink-0" aria-label="Standup source">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setActiveTab(t.id)}
                  className={`relative h-8 px-3 text-xs font-medium transition-colors ${
                    activeTab === t.id
                      ? 'text-aegisaxiom-text border-b-2 border-violet-500 -mb-px'
                      : 'text-aegisaxiom-text-muted hover:text-aegisaxiom-text'
                  }`}
                  data-testid={`new-standup-tab-${t.id}`}
                >
                  {t.label}
                </button>
              ))}
            </nav>

            <div className="overflow-y-auto p-5 flex-1">
              <p className="text-[11px] text-aegisaxiom-text-muted mb-4">
                {TABS.find((t) => t.id === activeTab)?.help}
              </p>

              {activeTab === 'manual' ? (
                <ManualTab
                  shipped={shipped} onShipped={setShipped}
                  today={today} onToday={setToday}
                  blockers={blockers} onBlockers={setBlockers}
                />
              ) : null}
              {activeTab === 'paste' ? (
                <PasteTab pasted={pasted} onPasted={setPasted} />
              ) : null}
              {activeTab === 'upload' ? (
                <UploadTab
                  onFile={handleFile}
                  fileName={uploadedName}
                  preview={uploadedContent}
                />
              ) : null}
            </div>

            <footer className="px-5 py-3 border-t border-aegisaxiom-border flex items-center justify-end gap-2 shrink-0">
              <button
                type="button"
                onClick={() => onOpenChange(false)}
                className="btn-secondary h-8 px-3 text-xs"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSave}
                disabled={submitting}
                className="btn-primary h-8 px-3 text-xs disabled:opacity-60"
                data-testid="new-standup-save"
              >
                {submitting ? 'Saving…' : 'Save standup'}
              </button>
            </footer>
          </>
        ) : (
          <SavedView
            standup={savedStandup}
            isSavedAndStructured={isSavedAndStructured}
            showSummarize={showSummarize}
            onSummarize={handleSummarize}
            summarizing={summarizing}
            onClose={() => onOpenChange(false)}
          />
        )}
      </div>
    </div>
  );
}

function ManualTab({ shipped, onShipped, today, onToday, blockers, onBlockers }) {
  return (
    <div className="space-y-4">
      <Field
        label="Shipped yesterday"
        value={shipped}
        onChange={onShipped}
        placeholder="One bullet per line"
        testid="new-standup-shipped"
      />
      <Field
        label="Working today"
        value={today}
        onChange={onToday}
        placeholder="One bullet per line"
        testid="new-standup-today"
      />
      <Field
        label="Blockers"
        value={blockers}
        onChange={onBlockers}
        placeholder="One bullet per line"
        testid="new-standup-blockers"
      />
    </div>
  );
}

function PasteTab({ pasted, onPasted }) {
  return (
    <div>
      <label className="block text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-1">
        Transcript
      </label>
      <textarea
        rows={14}
        value={pasted}
        onChange={(e) => onPasted(e.target.value)}
        className="aegisaxiom-input w-full text-[12px] font-mono resize-y"
        placeholder="Paste the meeting transcript here…"
        data-testid="new-standup-paste"
      />
    </div>
  );
}

function UploadTab({ onFile, fileName, preview }) {
  return (
    <div>
      <label
        className="flex flex-col items-center justify-center h-40 rounded-md border border-dashed border-aegisaxiom-border bg-aegisaxiom-surface cursor-pointer hover:bg-aegisaxiom-elevated/50 transition-colors"
        data-testid="new-standup-upload-dropzone"
      >
        <Upload size={20} className="text-aegisaxiom-text-muted mb-2" />
        <span className="text-xs text-aegisaxiom-text-muted">
          {fileName || 'Click to upload .md file'}
        </span>
        <input
          type="file"
          accept=".md,text/markdown"
          onChange={onFile}
          className="hidden"
          data-testid="new-standup-upload-input"
        />
      </label>
      {preview ? (
        <div className="mt-3">
          <p className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-1">Preview</p>
          <pre
            className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface p-3 max-h-48 overflow-y-auto text-[11px] font-mono text-aegisaxiom-text whitespace-pre-wrap"
            data-testid="new-standup-upload-preview"
          >
            {preview.slice(0, 4000)}{preview.length > 4000 ? '\n…' : ''}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

function Field({ label, value, onChange, placeholder, testid }) {
  return (
    <div>
      <label className="block text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-1">{label}</label>
      <textarea
        rows={4}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="aegisaxiom-input w-full text-sm resize-y"
        placeholder={placeholder}
        data-testid={testid}
      />
    </div>
  );
}

function SavedView({ standup, isSavedAndStructured, showSummarize, onSummarize, summarizing, onClose }) {
  const structured = standup.structured ?? null;
  return (
    <>
      <div className="overflow-y-auto p-5 flex-1 space-y-4" data-testid="new-standup-saved">
        <div className="rounded-md border border-green-500/30 bg-green-500/5 p-3">
          <p className="text-xs text-green-400 font-semibold mb-0.5">Standup saved</p>
          <p className="text-[11px] text-aegisaxiom-text-muted">
            {showSummarize
              ? 'Run AI summarization to extract structured bullets, or close to view it later.'
              : 'You can edit the bullets later from the standup viewer.'}
          </p>
        </div>

        {isSavedAndStructured ? (
          <section className="grid grid-cols-1 md:grid-cols-3 gap-3" data-testid="new-standup-saved-structured">
            <SavedColumn label="Shipped yesterday" items={structured.shipped_yesterday} accent="text-green-400" />
            <SavedColumn label="Working today" items={structured.working_today} accent="text-blue-400" />
            <SavedColumn label="Blockers" items={structured.blockers} accent="text-red-400" />
          </section>
        ) : null}

        {standup.summary ? (
          <section>
            <h5 className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-1.5">Summary</h5>
            <p className="text-sm text-aegisaxiom-text leading-relaxed">{standup.summary}</p>
          </section>
        ) : null}
      </div>

      <footer className="px-5 py-3 border-t border-aegisaxiom-border flex items-center justify-end gap-2 shrink-0">
        {showSummarize ? (
          <button
            type="button"
            onClick={onSummarize}
            disabled={summarizing}
            className="btn-secondary h-8 px-3 inline-flex items-center gap-1.5 text-xs disabled:opacity-60"
            data-testid="new-standup-summarize"
          >
            <Sparkles size={11} />
            {summarizing ? 'Summarizing…' : isSavedAndStructured ? 'Re-summarize' : 'Summarize with PMO AI'}
          </button>
        ) : null}
        <button
          type="button"
          onClick={onClose}
          className="btn-primary h-8 px-3 text-xs"
          data-testid="new-standup-done"
        >
          Done
        </button>
      </footer>
    </>
  );
}

function SavedColumn({ label, items, accent }) {
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
