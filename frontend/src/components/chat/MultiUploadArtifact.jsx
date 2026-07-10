import React, { useEffect, useState, useRef } from 'react';
import { Upload, CheckCircle, AlertTriangle } from 'lucide-react';
import { api, apiFormRequest } from '../../lib/api';
import API_URL from '../../lib/apiConfig';

function _extOf(name) {
  const i = name.lastIndexOf('.');
  return i >= 0 ? name.slice(i).toLowerCase() : '';
}

function UploadSlot({ slot, sessionId, onUploaded, submitted }) {
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  const hasFile = !!slot.upload_id;

  const acceptedExts = (slot.accepted_extensions || []).map((ext) =>
    (ext.startsWith('.') ? ext : `.${ext}`).toLowerCase()
  );

  const uploadFile = async (file) => {
    setUploading(true);
    setUploadError(null);

    const formData = new FormData();
    formData.append('file', file);
    if (sessionId) formData.append('session_id', sessionId);
    formData.append('slot_id', slot.slot_id);

    try {
      const res = await apiFormRequest('POST', `${API_URL}/api/chat-uploads`, formData);
      if (res.ok) {
        const data = await res.json();
        onUploaded(slot.slot_id, data.upload_id, data.filename || file.name);
      } else {
        const data = await res.json().catch(() => ({}));
        setUploadError(data?.detail || 'Upload failed. Please try again.');
      }
    } catch {
      setUploadError('Connection error. Please try again.');
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  const handleFileChange = (e) => {
    const file = e.target.files?.[0];
    if (file) uploadFile(file);
  };

  const handleDragEnter = (e) => {
    if (uploading || submitted) return;
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  };

  const handleDragOver = (e) => {
    if (uploading || submitted) return;
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = 'copy';
    if (!dragOver) setDragOver(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    // Only clear if we're actually leaving the drop zone (not entering a child)
    if (e.currentTarget.contains(e.relatedTarget)) return;
    setDragOver(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    if (uploading || submitted) return;

    const file = e.dataTransfer.files?.[0];
    if (!file) return;

    if (acceptedExts.length > 0) {
      const ext = _extOf(file.name);
      if (ext && !acceptedExts.includes(ext)) {
        setUploadError(`Unsupported file type. Accepted: ${acceptedExts.join(', ')}`);
        return;
      }
    }
    uploadFile(file);
  };

  const acceptAttr = acceptedExts.length > 0 ? acceptedExts.join(',') : undefined;

  return (
    <div
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={`relative flex items-center gap-3 py-3 border-b border-aegisaxiom-border last:border-b-0 -mx-2 px-2 rounded-md transition-colors ${
        dragOver ? 'bg-violet-50 dark:bg-violet-500/10 ring-2 ring-violet-400/60 ring-inset' : ''
      }`}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-sm text-aegisaxiom-text leading-snug">{slot.label}</span>
          {slot.required && (
            <span className="text-xs text-red-600 dark:text-red-400 font-medium">*</span>
          )}
        </div>
        {acceptedExts.length > 0 && (
          <p className="text-xs text-aegisaxiom-text-muted mt-0.5">
            {dragOver ? 'Drop to upload' : acceptedExts.join(', ')}
          </p>
        )}
      </div>

      <div className="shrink-0 flex items-center gap-2">
        {hasFile ? (
          <div className="flex items-center gap-2">
            <CheckCircle size={14} className="text-emerald-600 dark:text-emerald-400" />
            <span className="text-xs text-emerald-600 dark:text-emerald-400 max-w-[160px] truncate" title={slot.filename}>
              {slot.filename}
            </span>
            {!submitted && (
              <button
                type="button"
                onClick={() => inputRef.current?.click()}
                disabled={uploading}
                className="text-xs text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary underline transition-colors"
              >
                Replace
              </button>
            )}
          </div>
        ) : (
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={uploading || submitted}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border bg-aegisaxiom-elevated text-xs text-aegisaxiom-text-secondary hover:border-violet-500/50 hover:text-aegisaxiom-text disabled:opacity-50 disabled:cursor-default transition-all ${
              dragOver ? 'border-violet-500/70' : 'border-aegisaxiom-border-hover/60'
            }`}
          >
            {uploading ? (
              <>
                <div className="w-3 h-3 border-2 border-aegisaxiom-border-hover border-t-violet-400 rounded-full animate-spin" />
                Uploading…
              </>
            ) : (
              <>
                <Upload size={13} />
                {dragOver ? 'Drop file' : 'Upload'}
              </>
            )}
          </button>
        )}
        <input
          ref={inputRef}
          type="file"
          accept={acceptAttr}
          onChange={handleFileChange}
          className="sr-only"
          aria-label={`Upload file for ${slot.label}`}
        />
      </div>

      {uploadError && (
        <div className="absolute bottom-full mb-1 left-0 right-0 px-3 py-1.5 bg-red-900/80 border border-red-500/30 rounded-lg text-xs text-red-700 dark:text-red-300 flex items-center gap-1.5">
          <AlertTriangle size={12} />
          {uploadError}
        </div>
      )}
    </div>
  );
}

export default function MultiUploadArtifact({ artifact }) {
  const { artifact_id, payload } = artifact;
  const { title, instructions, submitted: initialSubmitted } = payload;
  // Normalize slots: LLM may use different field names or omit slots entirely
  const rawSlots = payload.slots || payload.files || payload.uploads || payload.documents || [];
  const slotsArray = Array.isArray(rawSlots) ? rawSlots : [rawSlots];
  // If LLM sent no slots at all, create a default one from the title
  const effectiveSlots = slotsArray.length > 0 ? slotsArray
    : [{ label: title || 'File Upload', required: true }];
  const normalizedSlots = effectiveSlots.map((s, i) => ({
    slot_id: s.slot_id || s.name || `slot_${i}`,
    label: s.label || s.name || s.description || `File ${i + 1}`,
    required: s.required !== false, // default true
    accepted_extensions: s.accepted_extensions || ['.txt', '.md', '.docx', '.pdf'],
    upload_id: s.upload_id || null,
    filename: s.filename || null,
  }));

  const [slots, setSlots] = useState(normalizedSlots);
  const [additionalSlots, setAdditionalSlots] = useState([]);
  const [submitted, setSubmitted] = useState(!!initialSubmitted);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  // Re-hydrate from the artifact when its identity changes. useState only
  // honors its initial value on first render; without this effect, a parent
  // re-fetch that swaps the artifact object would leave us showing stale
  // slot state (uploaded-but-now-empty after a refresh).
  useEffect(() => {
    setSlots(normalizedSlots);
    setSubmitted(!!initialSubmitted);
    // We intentionally key off artifact_id, not normalizedSlots, since the
    // latter is recomputed every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artifact_id]);

  const sessionId = artifact.session_id;

  const allSlots = [...slots, ...additionalSlots];

  const handleUploaded = (slotId, uploadId, filename) => {
    // Try base slots first, then additional slots
    setSlots(prev => {
      const updated = prev.map(s => s.slot_id === slotId ? { ...s, upload_id: uploadId, filename } : s);
      if (updated.some(s => s.slot_id === slotId)) return updated;
      return prev;
    });
    setAdditionalSlots(prev =>
      prev.map(s => s.slot_id === slotId ? { ...s, upload_id: uploadId, filename } : s)
    );
  };

  const allRequiredFilled = allSlots
    .filter(s => s.required)
    .every(s => !!s.upload_id);

  const uploadIds = allSlots
    .filter(s => !!s.upload_id)
    .map(s => s.upload_id);

  const handleContinue = async () => {
    if (!allRequiredFilled || submitted || submitting) return;

    setSubmitting(true);
    setSubmitError(null);

    try {
      const res = await api.post(
        `${API_URL}/api/ai-chat-artifacts/${artifact_id}/submit`,
        { upload_ids: uploadIds },
      );
      if (res.ok) {
        setSubmitted(true);
        // Notify parent to refresh messages — Vega's analysis was auto-generated server-side
        window.dispatchEvent(new CustomEvent('artifact-submitted', { detail: { artifact_id } }));
      } else {
        const data = await res.json();
        setSubmitError(data?.detail || 'Submission failed. Please try again.');
      }
    } catch {
      setSubmitError('Connection error. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="my-3 rounded-xl border border-violet-500/30 bg-aegisaxiom-elevated overflow-hidden">
      <div className="px-5 py-4 border-b border-aegisaxiom-border">
        <h3 className="text-sm font-semibold text-aegisaxiom-text">{title || 'Document Upload'}</h3>
        {instructions && (
          <p className="mt-1 text-xs text-aegisaxiom-text-secondary leading-relaxed">{instructions}</p>
        )}
        {!submitted && allSlots.length > 0 && (
          <p className="mt-1.5 text-[11px] text-aegisaxiom-text-muted">
            Tip: drag &amp; drop a file onto any row to upload.
          </p>
        )}
      </div>

      <div className="px-5 relative">
        {allSlots.map(slot => (
          <UploadSlot
            key={slot.slot_id}
            slot={slot}
            sessionId={sessionId}
            onUploaded={handleUploaded}
            submitted={submitted}
          />
        ))}
        {!submitted && (
          <div className="py-3 border-b border-aegisaxiom-border">
            <button
              type="button"
              onClick={() => {
                const newSlot = {
                  slot_id: `supporting_doc_${additionalSlots.length + 1}`,
                  label: `Supporting Document #${additionalSlots.length + 1}`,
                  required: false,
                  accepted_extensions: ['.txt', '.md', '.docx', '.pdf', '.csv', '.xlsx', '.bpmn', '.xml'],
                  upload_id: null,
                  filename: null,
                };
                setAdditionalSlots(prev => [...prev, newSlot]);
              }}
              className="flex items-center gap-1.5 text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 transition-colors px-1 py-1.5 rounded-lg hover:bg-violet-50 dark:bg-violet-500/10"
            >
              <span className="w-5 h-5 flex items-center justify-center rounded-full border border-violet-500/40 text-violet-600 dark:text-violet-400 text-sm">+</span>
              Add supporting document
            </button>
          </div>
        )}
      </div>

      <div className="px-5 py-4 border-t border-aegisaxiom-border flex items-center justify-between gap-3">
        <div className="text-xs text-aegisaxiom-text-muted">
          {submitted ? (
            <span className="text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5">
              <CheckCircle size={13} />
              Files submitted
            </span>
          ) : (
            <>
              {allSlots.filter(s => s.required && !s.upload_id).length > 0
                ? `${allSlots.filter(s => s.required && !s.upload_id).length} required file(s) remaining`
                : 'All required files uploaded'}
            </>
          )}
        </div>

        {!submitted && (
          <button
            type="button"
            onClick={handleContinue}
            disabled={!allRequiredFilled || submitting}
            className="flex items-center gap-2 px-5 py-2 rounded-xl bg-gradient-to-r from-violet-600 to-fuchsia-600 text-aegisaxiom-text text-sm font-medium hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
          >
            {submitting ? (
              <>
                <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Submitting…
              </>
            ) : (
              'Continue'
            )}
          </button>
        )}
      </div>

      {submitError && (
        <div className="px-5 pb-4 flex items-center gap-1.5 text-xs text-red-600 dark:text-red-400">
          <AlertTriangle size={12} />
          {submitError}
        </div>
      )}

      {submitting && (
        <div
          className="relative h-1 w-full overflow-hidden bg-violet-500/10"
          role="progressbar"
          aria-label="Analyzing — this can take a few minutes"
          aria-busy="true"
        >
          <div className="absolute inset-y-0 left-0 w-full bg-gradient-to-r from-violet-500 via-fuchsia-500 to-violet-500 animate-indeterminate-bar" />
        </div>
      )}
    </div>
  );
}
