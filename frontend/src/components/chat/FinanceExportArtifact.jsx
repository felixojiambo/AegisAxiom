import React, { useCallback, useState } from 'react';
import { Download, FileSpreadsheet, FileText, AlertTriangle } from 'lucide-react';
import { apiDownloadRich } from '../../lib/api';
import API_URL from '../../lib/apiConfig';

// Nova chat-native finance export download card.
//
// The backend persists the export bytes in GridFS (bucket
// `nova_export_files`) and writes one ai_chat_artifacts doc with
// artifact_type="finance_export". ChatArtifact.jsx fetches the doc
// and dispatches it here. The card renders the human-readable
// metadata and a download button that hits
// GET /api/nova/exports/{export_id}/download — the backend streams
// the bytes back with the right Content-Disposition.

function formatGeneratedAt(iso) {
  if (!iso || typeof iso !== 'string') return '';
  // YYYY-MM-DDTHH:MM:SS+00:00 — render as "MMM D, HH:MM" without
  // pulling in date-fns. Mirrors CashflowChartArtifact's approach.
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

function FormatBadge({ format }) {
  const upper = (format || '').toUpperCase();
  const isXlsx = upper === 'XLSX';
  const Icon = isXlsx ? FileSpreadsheet : FileText;
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium bg-violet-500/10 text-violet-700 dark:text-violet-300 border border-violet-500/20">
      <Icon size={12} />
      {upper || 'CSV'}
    </span>
  );
}

export default function FinanceExportArtifact({ artifact }) {
  const payload = artifact?.payload || {};
  const {
    export_id,
    filename,
    format,
    row_count,
    source_tool_name,
    generated_at,
    expires_at,
    download_url,
    warnings,
  } = payload;

  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState(null);

  const handleDownload = useCallback(async () => {
    if (!download_url) return;
    setDownloading(true);
    setError(null);

    // Use apiDownloadRich because api.get routes through safeXhr and
    // parses every response as JSON. Calling res.blob on a JSON helper
    // throws, which is why a valid 200 XLSX response was surfacing as
    // a generic network error in the UI. The PK byte signature
    // confirmed in DevTools proves the backend is correct, so the
    // bug lives entirely in the client helper choice.
    let res;
    try {
      res = await apiDownloadRich(`${API_URL}${download_url}`);
    } catch (err) {
      // apiDownloadRich resolves rather than rejects on transport
      // failure, so reaching this branch means an unexpected JS error.
      console.error('[FinanceExportArtifact] download helper threw', err);
      setError('Browser blocked the download. Open in new tab or allow downloads.');
      setDownloading(false);
      return;
    }

    if (!res.ok || !res.blob) {
      // Error envelopes are JSON, never XLSX, so reading the error
      // blob as text and parsing as JSON is safe here. The user
      // facing message follows the prescribed wording per status
      // code. Server detail is logged to the console only.
      let serverDetail = '';
      if (res.errorBlob) {
        try {
          const txt = await res.errorBlob.text();
          try {
            const parsed = JSON.parse(txt);
            serverDetail = parsed && parsed.detail ? String(parsed.detail) : txt;
          } catch {
            serverDetail = txt;
          }
        } catch {
          serverDetail = '';
        }
      }
      if (serverDetail) {
        console.error('[FinanceExportArtifact] download failed', res.status, serverDetail);
      }
      if (res.status === 401 || res.status === 403) {
        setError('You are not authorized to download this file.');
      } else if (res.status === 404) {
        setError('This file no longer exists.');
      } else if (res.status === 410) {
        setError('This export has expired. Generate it again.');
      } else if (res.status === 0) {
        setError('Network error during download.');
      } else {
        setError('Download failed. Try again or contact support.');
      }
      setDownloading(false);
      return;
    }

    // 2xx success path. The success path must never call
    // response.json on the body because the body is XLSX binary
    // starting with the PK signature 50 4B 03 04. apiDownloadRich
    // returns the binary as a Blob via responseType blob, so the
    // body is never run through a JSON parser.
    try {
      const headerFilename = (() => {
        const cd = (res.getHeader && res.getHeader('Content-Disposition')) || '';
        // Match either filename or RFC 5987 filename star variant.
        const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
        return m && m[1] ? m[1] : '';
      })();
      const finalName = headerFilename || filename || 'nova-export';

      const objectUrl = URL.createObjectURL(res.blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = finalName;
      // Anchor must be attached to the DOM for Firefox to honour
      // a programmatic click. Revoke is deferred via setTimeout so
      // the browser has begun the download before the blob URL is
      // freed. Synchronous revoke can race the download in some
      // browsers.
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
    } catch (err) {
      console.error('[FinanceExportArtifact] blob handoff failed', err);
      setError('Browser blocked the download. Open in new tab or allow downloads.');
    } finally {
      setDownloading(false);
    }
  }, [download_url, filename]);

  if (!export_id) {
    return (
      <div className="my-3 p-4 rounded-xl border border-aegisaxiom-border bg-aegisaxiom-elevated text-sm text-aegisaxiom-text-secondary">
        Export metadata is missing. Ask Nova to recreate the export.
      </div>
    );
  }

  return (
    <div className="my-3 rounded-xl border border-violet-500/30 bg-aegisaxiom-elevated overflow-hidden">
      <div className="px-5 py-3 bg-gradient-to-r from-violet-600/15 to-purple-600/15 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <FormatBadge format={format} />
          <span className="text-sm font-semibold text-aegisaxiom-text truncate" title={filename}>
            {filename || 'nova-export'}
          </span>
        </div>
        <button
          type="button"
          onClick={handleDownload}
          disabled={downloading}
          data-testid="finance-export-download"
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-xs font-medium disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
        >
          <Download size={14} />
          {downloading ? 'Downloading…' : 'Download'}
        </button>
      </div>
      <div className="px-5 py-3 text-xs text-aegisaxiom-text-secondary space-y-1">
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          <span>
            <strong className="text-aegisaxiom-text">{row_count ?? 0}</strong> rows
          </span>
          {source_tool_name && (
            <span>
              source:&nbsp;
              <code className="text-aegisaxiom-text">{source_tool_name}</code>
            </span>
          )}
          {generated_at && <span>created {formatGeneratedAt(generated_at)}</span>}
          {expires_at && <span>expires {formatGeneratedAt(expires_at)}</span>}
        </div>
        {Array.isArray(warnings) && warnings.length > 0 && (
          <div className="mt-2 flex items-start gap-1.5 text-amber-600 dark:text-amber-400">
            <AlertTriangle size={12} className="mt-0.5 flex-shrink-0" />
            <span>{warnings.join(' ')}</span>
          </div>
        )}
        {error && (
          <div
            data-testid="finance-export-error"
            className="mt-2 flex items-start gap-1.5 text-red-600 dark:text-red-400"
          >
            <AlertTriangle size={12} className="mt-0.5 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}
      </div>
    </div>
  );
}
