import { useCallback, useRef, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  FileText,
  Loader2,
  UploadCloud,
  X,
} from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';

const ALLOWED_EXT = new Set(['.csv', '.xls', '.xlsx']);
const MAX_BYTES = 25 * 1024 * 1024; // 25 MB

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  let v = bytes;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function extOf(filename) {
  const dot = (filename || '').lastIndexOf('.');
  return dot >= 0 ? filename.slice(dot).toLowerCase() : '';
}

/**
 * UploadField — a single drag-drop / click-to-pick upload slot.
 *
 * Props:
 *   label       — visible heading for the slot
 *   endpoint    — full URL to POST the file to (e.g. /api/match/uploads/provider-report)
 *   required    — whether the field is required before submit
 *   onChange    — called with { url, filename, original_filename, size, kind } on success
 *                 or with null when the file is cleared
 */
export default function UploadField({ label, endpoint, required = false, onChange }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploaded, setUploaded] = useState(null);   // { url, filename, original_filename, size, kind }
  const [error, setError] = useState(null);

  const validateAndUpload = useCallback(
    async (file) => {
      setError(null);

      const ext = extOf(file.name);
      if (!ALLOWED_EXT.has(ext)) {
        setError(`Unsupported file type "${ext || 'unknown'}". Allowed: .csv, .xls, .xlsx`);
        return;
      }
      if (file.size > MAX_BYTES) {
        setError('File too large. Maximum is 25 MB.');
        return;
      }

      setUploading(true);
      try {
        const fd = new FormData();
        fd.append('file', file);
        const res = await api.postForm(`${API_URL}${endpoint}`, fd);
        const data = await res.json();
        if (!res.ok) {
          setError(data?.detail || 'Upload failed.');
          return;
        }
        setUploaded(data);
        if (onChange) onChange(data);
      } catch (err) {
        setError(err?.message || 'Upload failed.');
      } finally {
        setUploading(false);
      }
    },
    [endpoint, onChange],
  );

  // Drag handlers
  const onDragOver = (e) => { e.preventDefault(); setDragging(true); };
  const onDragLeave = () => setDragging(false);
  const onDrop = useCallback(
    (e) => {
      e.preventDefault();
      setDragging(false);
      const file = e.dataTransfer.files?.[0];
      if (file) validateAndUpload(file);
    },
    [validateAndUpload],
  );

  const onInputChange = (e) => {
    const file = e.target.files?.[0];
    if (file) validateAndUpload(file);
    // Reset so the same file can be re-picked after a clear
    e.target.value = '';
  };

  const handleClear = () => {
    setUploaded(null);
    setError(null);
    if (onChange) onChange(null);
  };

  // -- Uploaded / success state --
  if (uploaded) {
    return (
      <div className="rounded-lg border border-aegisaxiom-border bg-aegisaxiom-elevated p-3 flex items-center gap-3">
        <div className="h-8 w-8 flex-shrink-0 rounded-md bg-violet-500/10 grid place-items-center">
          <CheckCircle2 className="h-4 w-4 text-violet-400" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-xs font-medium text-aegisaxiom-text truncate">
            {uploaded.original_filename}
          </div>
          <div className="text-xs text-aegisaxiom-text-muted">
            {formatBytes(uploaded.size)}
            {uploaded.detected_provider ? ` · detected: ${uploaded.detected_provider}` : ''}
          </div>
        </div>
        <button
          type="button"
          onClick={handleClear}
          className="flex-shrink-0 h-7 w-7 grid place-items-center rounded-md text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-surface transition"
          title="Replace file"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    );
  }

  // -- Drop zone --
  return (
    <div className="space-y-1.5">
      <div
        role="button"
        tabIndex={0}
        aria-label={`Upload ${label}`}
        className={[
          'rounded-lg border border-dashed p-4 cursor-pointer transition-colors',
          'flex flex-col items-center gap-2 text-center',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50',
          dragging
            ? 'border-violet-500/50 bg-violet-500/5'
            : 'border-aegisaxiom-border bg-aegisaxiom-elevated hover:border-violet-500/30 hover:bg-aegisaxiom-surface',
          uploading ? 'pointer-events-none' : '',
        ].join(' ')}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onClick={() => !uploading && inputRef.current?.click()}
        onKeyDown={(e) => {
          if ((e.key === 'Enter' || e.key === ' ') && !uploading) inputRef.current?.click();
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.xls,.xlsx"
          className="sr-only"
          onChange={onInputChange}
          aria-hidden="true"
        />

        {uploading ? (
          <>
            <Loader2 className="h-5 w-5 text-violet-400 animate-spin" />
            <span className="text-xs text-aegisaxiom-text-secondary">Uploading…</span>
          </>
        ) : (
          <>
            <div className="h-8 w-8 rounded-full bg-aegisaxiom-surface grid place-items-center">
              {dragging ? (
                <FileText className="h-4 w-4 text-violet-400" />
              ) : (
                <UploadCloud className="h-4 w-4 text-aegisaxiom-text-muted" />
              )}
            </div>
            <div>
              <span className="text-xs font-medium text-aegisaxiom-text">
                {dragging ? 'Drop to upload' : 'Drop here or click to browse'}
              </span>
              {required && (
                <span className="ml-1 text-xs text-red-400">*</span>
              )}
              <div className="text-xs text-aegisaxiom-text-muted mt-0.5">.csv, .xls, .xlsx · max 25 MB</div>
            </div>
          </>
        )}
      </div>

      {error && (
        <div className="flex items-start gap-1.5 text-xs text-red-400">
          <AlertCircle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}
    </div>
  );
}
