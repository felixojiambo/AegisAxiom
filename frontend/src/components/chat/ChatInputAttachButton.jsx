import React, { useEffect, useRef, useState } from 'react';
import { Paperclip, X, Loader2, Plus } from 'lucide-react';
import { apiFormRequest } from '../../lib/api';
import API_URL from '../../lib/apiConfig';

const ACCEPTED_TYPES = '.txt,.md,.docx,.pdf';
const MAX_SIZE_MB = 10;
const MAX_ATTACHMENTS = 6;

/**
 * Multi-file attach button for the chat composer.
 *
 * Controlled API:
 *   <ChatInputAttachButton
 *     sessionId={...}
 *     attachments={attachments}            // array of {upload_id, filename}
 *     onChange={setAttachments}            // receives the next array
 *   />
 *
 * Backwards-compatible legacy single-attachment API:
 *   onAttach(info | null)  // first attachment / cleared
 *   attached: {upload_id, filename} | null
 *
 * The component normalizes both paths internally so existing call sites
 * (ChatPage / AIEmployees) keep working as I migrate them.
 */
export default function ChatInputAttachButton({
  sessionId,
  onChange,
  onAttach,
  attachments: controlledAttachments,
  attached: controlledAttached, // legacy single-file
  disabled,
}) {
  const fileRef = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [internalAttachments, setInternalAttachments] = useState([]);
  const [error, setError] = useState(null);

  // Normalize: prefer the controlled multi-attachments prop, else legacy
  // single-attached prop, else internal state.
  const isControlledMulti = controlledAttachments !== undefined;
  const isControlledSingle = controlledAttached !== undefined;
  let attachments;
  if (isControlledMulti) {
    attachments = controlledAttachments || [];
  } else if (isControlledSingle) {
    attachments = controlledAttached ? [controlledAttached] : [];
  } else {
    attachments = internalAttachments;
  }

  const writeAttachments = (next) => {
    if (!isControlledMulti && !isControlledSingle) setInternalAttachments(next);
    if (onChange) onChange(next);
    // Legacy single-file callback: pass the first item or null.
    if (onAttach) onAttach(next[0] || null);
  };

  const atCapacity = attachments.length >= MAX_ATTACHMENTS;

  const handleClick = () => {
    if (atCapacity || disabled || uploading) return;
    fileRef.current?.click();
  };

  const uploadFile = async (file) => {
    if (!file) return;
    if (file.size > MAX_SIZE_MB * 1024 * 1024) {
      setError(`File too large (${(file.size / 1024 / 1024).toFixed(1)} MB). Max: ${MAX_SIZE_MB} MB.`);
      return;
    }
    setError(null);
    setUploading(true);
    const formData = new FormData();
    formData.append('file', file);
    formData.append('session_id', sessionId);
    try {
      const resp = await apiFormRequest('POST', `${API_URL}/api/chat-uploads`, formData);
      const data = await resp.json();
      if (!resp.ok) {
        setError(data.detail || 'Upload failed');
        setUploading(false);
        return;
      }
      const next = [...attachments, { upload_id: data.upload_id, filename: data.filename }];
      writeAttachments(next);
    } catch {
      setError('Upload failed. Please try again.');
    } finally {
      setUploading(false);
    }
  };

  const handleFile = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = '';
    await uploadFile(file);
  };

  // Wave 7 — image paste support. Listens on window so a paste anywhere
  // in the chat surface (textarea or otherwise) is captured. Filters to
  // image MIME types so plain-text paste into the textarea isn't
  // intercepted. Names the file with a timestamp since pasted-image
  // blobs typically arrive without a filename.
  useEffect(() => {
    if (disabled || atCapacity || !sessionId) return undefined;
    const onPaste = async (event) => {
      if (!event.clipboardData) return;
      const items = Array.from(event.clipboardData.items || []);
      const images = items.filter((i) => i.kind === 'file' && (i.type || '').startsWith('image/'));
      if (images.length === 0) return;
      event.preventDefault();
      for (const item of images) {
        const blob = item.getAsFile();
        if (!blob) continue;
        const ext = (blob.type.split('/')[1] || 'png').replace('+xml', '');
        const stamp = new Date().toISOString().replace(/[:.]/g, '-');
        const named = new File([blob], `pasted-image-${stamp}.${ext}`, { type: blob.type });
        await uploadFile(named);
      }
    };
    window.addEventListener('paste', onPaste);
    return () => window.removeEventListener('paste', onPaste);
  }, [disabled, atCapacity, sessionId, attachments]); // eslint-disable-line react-hooks/exhaustive-deps

  const removeAt = (idx) => {
    const next = attachments.filter((_, i) => i !== idx);
    writeAttachments(next);
    setError(null);
  };

  return (
    <div className="relative flex items-end gap-1.5">
      <input
        ref={fileRef}
        type="file"
        accept={ACCEPTED_TYPES}
        onChange={handleFile}
        className="hidden"
      />

      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-1.5 max-w-[360px] py-1">
          {attachments.map((a, idx) => (
            <div
              key={`${a.upload_id}-${idx}`}
              className="flex items-center gap-1.5 px-2.5 py-1.5 bg-violet-50 dark:bg-violet-600/20 border border-violet-500/40 rounded-lg text-xs text-violet-700 dark:text-violet-300 max-w-[180px]"
              title={a.filename}
            >
              <Paperclip size={12} className="shrink-0" />
              <span className="truncate">{a.filename}</span>
              <button
                type="button"
                onClick={() => removeAt(idx)}
                className="shrink-0 hover:text-red-600 dark:text-red-400 transition-colors"
                aria-label={`Remove ${a.filename}`}
              >
                <X size={12} />
              </button>
            </div>
          ))}
        </div>
      )}

      <button
        type="button"
        onClick={handleClick}
        disabled={disabled || uploading || atCapacity}
        className="w-11 h-11 rounded-xl bg-aegisaxiom-elevated hover:bg-aegisaxiom-elevated border border-aegisaxiom-border disabled:opacity-30 flex items-center justify-center transition-all shrink-0"
        title={
          atCapacity
            ? `Maximum ${MAX_ATTACHMENTS} files`
            : attachments.length > 0
              ? 'Attach another file (.txt, .md, .docx, .pdf)'
              : 'Attach file (.txt, .md, .docx, .pdf)'
        }
      >
        {uploading ? (
          <Loader2 size={16} className="text-violet-600 dark:text-violet-400 animate-spin" />
        ) : attachments.length > 0 ? (
          <Plus size={16} className="text-aegisaxiom-text-secondary" />
        ) : (
          <Paperclip size={16} className="text-aegisaxiom-text-secondary" />
        )}
      </button>

      {error && (
        <div className="absolute bottom-full mb-2 left-0 bg-red-900/90 border border-red-700 rounded-lg px-3 py-1.5 text-xs text-red-200 whitespace-nowrap">
          {error}
          <button
            type="button"
            onClick={() => setError(null)}
            className="ml-2 text-red-600 dark:text-red-400 hover:text-red-200"
          >
            <X size={10} />
          </button>
        </div>
      )}
    </div>
  );
}
