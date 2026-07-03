import React, { useMemo, useState } from 'react';
import { Upload, X, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';

// Wave 6 / Thread C — Contract 6
//
// ImportTasksModal: file picker -> client-side preview (parsed rows) -> commit.
//
// We parse the CSV client-side just for preview UX (showing the first N rows
// + column count). The authoritative validation happens server-side; the
// preview is intentionally lenient and never blocks submission.

const MAX_PREVIEW_ROWS = 25;

// Tiny CSV parser for preview only. Handles quoted fields with embedded commas
// and escaped quotes. We keep it minimal — a 30-line preview helper is plenty
// for UX, and the backend re-parses with Python's csv module which is the
// authority.
function parseCsvForPreview(text) {
  const rows = [];
  let cur = [];
  let field = '';
  let inQuotes = false;
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i += 2; continue; }
        inQuotes = false; i += 1; continue;
      }
      field += ch; i += 1; continue;
    }
    if (ch === '"') { inQuotes = true; i += 1; continue; }
    if (ch === ',') { cur.push(field); field = ''; i += 1; continue; }
    if (ch === '\r') { i += 1; continue; }
    if (ch === '\n') {
      cur.push(field);
      rows.push(cur);
      cur = []; field = '';
      i += 1;
      continue;
    }
    field += ch; i += 1;
  }
  if (field.length > 0 || cur.length > 0) {
    cur.push(field);
    rows.push(cur);
  }
  return rows;
}

export default function ImportTasksModal({ isOpen, onClose, project, onImported }) {
  const { hasPermission } = usePermissions();
  const canImport = hasPermission('projects.task.create');

  const [file, setFile] = useState(null);
  const [previewText, setPreviewText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);

  const reset = () => {
    setFile(null);
    setPreviewText('');
    setResult(null);
    setSubmitting(false);
  };

  const handleFileChange = async (e) => {
    setResult(null);
    const f = e.target.files?.[0];
    if (!f) {
      setFile(null);
      setPreviewText('');
      return;
    }
    setFile(f);
    try {
      // Read up to ~512 KB of the file for preview only — full file goes to
      // backend on submit.
      const slice = f.slice(0, 512 * 1024);
      const text = await slice.text();
      setPreviewText(text);
    } catch {
      setPreviewText('');
    }
  };

  const previewRows = useMemo(() => {
    if (!previewText) return null;
    const parsed = parseCsvForPreview(previewText);
    if (parsed.length === 0) return null;
    const [header, ...rest] = parsed;
    return { header, rows: rest.slice(0, MAX_PREVIEW_ROWS), totalShown: rest.length };
  }, [previewText]);

  const handleSubmit = async () => {
    if (!file || !project) return;
    setSubmitting(true);
    setResult(null);
    try {
      const projectId = project.project_id ?? project._id;
      const fd = new FormData();
      fd.append('file', file);
      const res = await api.postForm(
        `${API_URL}/api/projects/projects/${projectId}/import`,
        fd,
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const msg = (data && (data.detail?.message ?? data.detail)) || 'Import failed.';
        toast.error(typeof msg === 'string' ? msg : 'Import failed.');
        return;
      }
      const data = await res.json();
      setResult(data);
      if ((data?.created_count ?? 0) > 0) {
        toast.success(`Imported ${data.created_count} task${data.created_count === 1 ? '' : 's'}`);
      }
      if ((data?.failed?.length ?? 0) === 0 && (data?.created_count ?? 0) > 0) {
        // Clean run — refresh and close after a beat so the user sees success.
        onImported?.();
      } else if ((data?.created_count ?? 0) > 0) {
        // Partial — refresh but stay open so the user can see the failed rows.
        onImported?.();
      }
    } catch {
      toast.error('Import failed.');
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen || !canImport) return null;

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-labelledby="import-tasks-modal-title"
      data-testid="import-tasks-modal"
    >
      <div
        className="absolute inset-0"
        onClick={() => { if (!submitting) { reset(); onClose?.(); } }}
        aria-hidden="true"
      />

      <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-5 pb-3 border-b border-aegisaxiom-border shrink-0">
          <div>
            <h3
              id="import-tasks-modal-title"
              className="text-base font-semibold text-aegisaxiom-text"
            >
              Import tasks from CSV
            </h3>
            <p className="text-xs text-aegisaxiom-text-muted mt-0.5">
              Upload a CSV. Each row is validated server-side and any failures are reported per-row.
            </p>
          </div>
          <button
            type="button"
            onClick={() => { if (!submitting) { reset(); onClose?.(); } }}
            className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary transition shrink-0"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {!file ? (
            <div className="border-2 border-dashed border-aegisaxiom-border rounded-lg p-8 text-center">
              <Upload className="mx-auto text-aegisaxiom-text-muted mb-2" size={28} />
              <p className="text-sm text-aegisaxiom-text-secondary mb-3">
                Pick a CSV file. Required column: <span className="font-mono text-xs">title</span>.
                Optional: <span className="font-mono text-xs">description</span>,
                <span className="font-mono text-xs"> status</span>,
                <span className="font-mono text-xs"> priority</span>,
                <span className="font-mono text-xs"> assignee_email</span>,
                <span className="font-mono text-xs"> due_date</span>,
                <span className="font-mono text-xs"> start_date</span>,
                <span className="font-mono text-xs"> labels</span>,
                <span className="font-mono text-xs"> methodology_field_*</span>.
              </p>
              <label className="btn-secondary inline-flex items-center gap-1.5 text-xs cursor-pointer">
                <Upload size={12} />
                Choose CSV
                <input
                  type="file"
                  accept=".csv,text/csv"
                  onChange={handleFileChange}
                  className="hidden"
                  data-testid="import-tasks-file-input"
                />
              </label>
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between bg-aegisaxiom-elevated/40 rounded-lg px-3 py-2">
                <div className="text-sm text-aegisaxiom-text truncate">
                  <span className="font-medium">{file.name}</span>
                  <span className="ml-2 text-aegisaxiom-text-muted text-xs">
                    {(file.size / 1024).toFixed(1)} KB
                  </span>
                </div>
                <button
                  type="button"
                  onClick={reset}
                  disabled={submitting}
                  className="text-xs text-aegisaxiom-text-muted hover:text-aegisaxiom-text disabled:opacity-50"
                  data-testid="import-tasks-clear"
                >
                  Clear
                </button>
              </div>

              {previewRows ? (
                <div data-testid="import-tasks-preview">
                  <p className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-1.5">
                    Preview ({previewRows.rows.length} of {previewRows.totalShown} row{previewRows.totalShown === 1 ? '' : 's'} shown)
                  </p>
                  <div className="border border-aegisaxiom-border rounded-md overflow-x-auto max-h-[260px] overflow-y-auto">
                    <table className="text-xs w-full">
                      <thead className="bg-aegisaxiom-elevated/60 sticky top-0">
                        <tr>
                          {previewRows.header.map((h, i) => (
                            <th key={i} className="text-left px-2 py-1.5 font-semibold text-aegisaxiom-text-secondary whitespace-nowrap">
                              {h}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-aegisaxiom-border">
                        {previewRows.rows.map((row, ri) => (
                          <tr key={ri}>
                            {previewRows.header.map((_, ci) => (
                              <td key={ci} className="px-2 py-1 text-aegisaxiom-text-secondary truncate max-w-[200px]">
                                {row[ci] ?? ''}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : null}

              {result ? (
                <div className="space-y-2" data-testid="import-tasks-result">
                  {result.created_count > 0 ? (
                    <div className="flex items-start gap-2 rounded-md bg-green-500/10 px-3 py-2 text-sm text-green-400">
                      <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
                      <span>Created {result.created_count} task{result.created_count === 1 ? '' : 's'}.</span>
                    </div>
                  ) : null}
                  {(result.failed?.length ?? 0) > 0 ? (
                    <div className="rounded-md bg-red-500/10 px-3 py-2">
                      <div className="flex items-center gap-2 text-sm text-red-400 font-medium mb-1.5">
                        <AlertTriangle size={14} />
                        {result.failed.length} row{result.failed.length === 1 ? '' : 's'} failed
                      </div>
                      <ul className="space-y-0.5 text-xs text-red-300 max-h-[160px] overflow-y-auto pr-1">
                        {result.failed.map((f, i) => (
                          <li key={i} data-testid={`import-tasks-failed-${f.row}`}>
                            <span className="font-mono">Row {f.row}:</span> {f.error}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 p-5 pt-3 border-t border-aegisaxiom-border shrink-0">
          <button
            type="button"
            onClick={() => { if (!submitting) { reset(); onClose?.(); } }}
            disabled={submitting}
            className="btn-secondary text-xs disabled:opacity-50"
          >
            {result ? 'Close' : 'Cancel'}
          </button>
          {!result ? (
            <button
              type="button"
              onClick={handleSubmit}
              disabled={!file || submitting}
              className="btn-primary inline-flex items-center gap-1.5 text-xs disabled:opacity-50"
              data-testid="import-tasks-submit"
            >
              {submitting ? 'Importing…' : 'Import'}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
