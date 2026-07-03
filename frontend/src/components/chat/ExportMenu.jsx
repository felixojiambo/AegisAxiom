import React, { useState } from 'react';
import { api, apiDownloadRich } from '../../lib/api';
import API_URL from '../../lib/apiConfig';
import {
  exportV3DesignDocPdf,
  exportV3EaddPdf,
  exportV3AnalysisPdf,
} from '../../lib/v3PdfExporter';

const STYLE_ID = 'vega-v3-export-menu-styles';

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .vega-export-menu { position: relative; }
    .vega-export-menu .trigger {
      appearance: none;
      background: rgba(139, 92, 246, 0.1);
      color: #a78bfa;
      border: 1px solid rgba(139, 92, 246, 0.35);
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 600;
      border-radius: 6px;
      cursor: pointer;
    }
    .vega-export-menu .trigger:hover { background: rgba(139, 92, 246, 0.18); }
    .vega-export-menu .panel {
      position: absolute;
      top: calc(100% + 6px);
      right: 0;
      min-width: 220px;
      background: var(--aegisaxiom-elevated, #16162e);
      border: 1px solid var(--aegisaxiom-border, #2a2a44);
      border-radius: 8px;
      padding: 6px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
      z-index: 20;
    }
    .vega-export-menu .panel button {
      display: block;
      width: 100%;
      text-align: left;
      appearance: none;
      background: transparent;
      color: var(--aegisaxiom-text, #e6e2ff);
      border: none;
      padding: 6px 8px;
      font-size: 12px;
      cursor: pointer;
      border-radius: 4px;
    }
    .vega-export-menu .panel button:hover {
      background: rgba(139, 92, 246, 0.12);
    }
    .vega-export-menu .panel .group-label {
      font-size: 10px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      padding: 6px 8px 2px;
    }
    .vega-export-menu .panel .divider {
      height: 1px;
      background: var(--aegisaxiom-border-soft, #1f1f3a);
      margin: 4px 0;
    }
    .vega-export-status {
      position: fixed;
      top: 16px;
      right: 16px;
      padding: 8px 12px;
      background: var(--aegisaxiom-elevated, #16162e);
      border: 1px solid var(--aegisaxiom-border, #2a2a44);
      border-radius: 6px;
      color: var(--aegisaxiom-text, #e6e2ff);
      font-size: 12px;
      z-index: 100;
    }
    .vega-export-status.error { color: #f87171; border-color: rgba(248, 113, 113, 0.4); }
  `;
  document.head.appendChild(style);
}

async function downloadFile(url, filenameHint) {
  // Use apiDownloadRich — api.get() routes through safeXhr and parses
  // every response as JSON, so .blob() is undefined and the export
  // would silently throw. apiDownloadRich is a raw XHR with
  // responseType=blob and surfaces headers + JSON error bodies.
  const res = await apiDownloadRich(url);
  if (!res.ok || !res.blob) {
    let detail = `Export failed: ${res.status || 'network error'}`;
    if (res.errorBlob) {
      try {
        const txt = await res.errorBlob.text();
        try {
          const parsed = JSON.parse(txt);
          detail = parsed.detail || txt || detail;
        } catch {
          detail = txt || detail;
        }
      } catch {
        /* ignore */
      }
    }
    throw new Error(detail);
  }
  const cd = res.getHeader('Content-Disposition') || '';
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
  const fname = (m && m[1]) || filenameHint;
  const objectUrl = URL.createObjectURL(res.blob);
  const a = document.createElement('a');
  a.href = objectUrl;
  a.download = fname;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(objectUrl);
}

export default function ExportMenu({ artifact }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);

  const artifactId = artifact?.artifact_id;
  if (!artifactId) return null;
  const payload = artifact.payload || {};
  const workflows = payload.workflows || [];
  const apps = payload.apps || [];

  const showStatus = (msg, isError = false) => {
    setStatus({ msg, isError });
    setTimeout(() => setStatus(null), 4000);
  };

  const exportArtifact = async (target, format, entityId) => {
    setBusy(true);
    setOpen(false);
    try {
      if (format === 'google_doc') {
        const params = new URLSearchParams({ target });
        if (entityId) params.set('entity_id', entityId);
        const res = await api.post(
          `${API_URL}/api/vega-artifacts/${artifactId}/export/google-doc?${params.toString()}`,
        );
        if (!res.ok) {
          const detail = await res.json().catch(() => ({}));
          throw new Error(detail.detail || `Google Doc export failed: ${res.status}`);
        }
        const data = await res.json();
        if (data.doc_url) {
          window.open(data.doc_url, '_blank', 'noopener,noreferrer');
          showStatus('Google Doc opened in new tab.');
        }
      } else if (format === 'pdf') {
        // Client-side PDF — matches in-app theme + embeds BPMN diagrams.
        showStatus('Rendering PDF…');
        if (target === 'analysis') {
          await exportV3AnalysisPdf({ payload });
        } else if (target === 'design_doc') {
          const wf = workflows.find((w) => w.workflow_id === entityId);
          if (!wf) throw new Error('Workflow not found in artifact.');
          await exportV3DesignDocPdf({ workflow: wf, payload });
        } else if (target === 'eadd') {
          const app = apps.find((a) => a.app_id === entityId);
          if (!app) throw new Error('App not found in artifact.');
          await exportV3EaddPdf({ app, payload });
        }
        showStatus('PDF downloaded.');
      } else {
        const params = new URLSearchParams({ target, format });
        if (entityId) params.set('entity_id', entityId);
        const url = `${API_URL}/api/vega-artifacts/${artifactId}/export?${params.toString()}`;
        await downloadFile(url, `vega_v3_${target}.${format === 'pdf' ? 'pdf' : 'md'}`);
        showStatus('Download started.');
      }
    } catch (err) {
      showStatus(err.message || 'Export failed.', true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="vega-export-menu">
        <button type="button" className="trigger" disabled={busy} onClick={() => setOpen((v) => !v)}>
          {busy ? 'Exporting…' : 'Export ▾'}
        </button>
        {open ? (
          <div className="panel" onMouseLeave={() => setOpen(false)}>
            <div className="group-label">Whole analysis</div>
            <button onClick={() => exportArtifact('analysis', 'pdf')}>PDF</button>
            <button onClick={() => exportArtifact('analysis', 'markdown')}>Markdown</button>
            <button onClick={() => exportArtifact('analysis', 'google_doc')}>Google Doc</button>
            {workflows.length > 0 ? (
              <>
                <div className="divider" />
                <div className="group-label">Workflow design docs</div>
                {workflows.map((wf) =>
                  wf.design_doc_status === 'generated' ? (
                    <div key={wf.workflow_id} style={{ display: 'flex', gap: 4 }}>
                      <button
                        style={{ flex: 1 }}
                        onClick={() => exportArtifact('design_doc', 'pdf', wf.workflow_id)}
                      >
                        {wf.workflow_name}
                      </button>
                      <button
                        style={{ width: 60 }}
                        title="Google Doc"
                        onClick={() => exportArtifact('design_doc', 'google_doc', wf.workflow_id)}
                      >
                        ↗
                      </button>
                    </div>
                  ) : null,
                )}
              </>
            ) : null}
            {apps.length > 0 ? (
              <>
                <div className="divider" />
                <div className="group-label">App design docs</div>
                {apps.map((app) =>
                  app.design_doc_status === 'generated' ? (
                    <div key={app.app_id} style={{ display: 'flex', gap: 4 }}>
                      <button
                        style={{ flex: 1 }}
                        onClick={() => exportArtifact('eadd', 'pdf', app.app_id)}
                      >
                        {app.app_name}
                      </button>
                      <button
                        style={{ width: 60 }}
                        title="Google Doc"
                        onClick={() => exportArtifact('eadd', 'google_doc', app.app_id)}
                      >
                        ↗
                      </button>
                    </div>
                  ) : null,
                )}
              </>
            ) : null}
          </div>
        ) : null}
      </div>
      {status ? (
        <div className={`vega-export-status ${status.isError ? 'error' : ''}`}>{status.msg}</div>
      ) : null}
    </>
  );
}
