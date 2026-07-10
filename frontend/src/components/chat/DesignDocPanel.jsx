import React, { useState } from 'react';
import { api, apiDownloadRich } from '../../lib/api';
import API_URL from '../../lib/apiConfig';
import { exportV3DesignDocPdf, exportV3EaddPdf } from '../../lib/v3PdfExporter';

const STYLE_ID = 'vega-v3-design-doc-panel-styles';

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .vega-doc-panel {
      margin-top: 8px;
      background: var(--aegisaxiom-base, #08081a);
      border: 1px solid var(--aegisaxiom-border, #2a2a44);
      border-radius: 8px;
      padding: 12px 14px;
    }
    .vega-doc-panel .actions {
      display: flex;
      gap: 6px;
      justify-content: flex-end;
      margin-bottom: 10px;
    }
    .vega-doc-panel .actions button {
      appearance: none;
      background: rgba(139, 92, 246, 0.1);
      color: #a78bfa;
      border: 1px solid rgba(139, 92, 246, 0.35);
      padding: 3px 9px;
      font-size: 11px;
      font-weight: 600;
      border-radius: 5px;
      cursor: pointer;
    }
    .vega-doc-panel .actions button:hover { background: rgba(139, 92, 246, 0.18); }
    .vega-doc-panel .actions button:disabled { opacity: 0.5; cursor: not-allowed; }
    .vega-doc-panel h4 {
      font-size: 10px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      margin: 12px 0 4px;
      font-weight: 600;
    }
    .vega-doc-panel h4:first-child { margin-top: 0; }
    .vega-doc-panel p, .vega-doc-panel li {
      font-size: 12px;
      color: var(--aegisaxiom-text, #e6e2ff);
      line-height: 1.5;
      margin: 0 0 4px;
    }
    .vega-doc-panel ul {
      padding-left: 18px;
      margin: 0 0 4px;
    }
    .vega-doc-panel .kv {
      font-size: 12px;
      color: var(--aegisaxiom-text, #e6e2ff);
      margin: 2px 0;
    }
    .vega-doc-panel .kv strong {
      color: var(--aegisaxiom-text-muted, #6e6a90);
      font-weight: 600;
      margin-right: 6px;
    }
    .vega-doc-panel pre.starter-prompt {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 11px;
      color: var(--aegisaxiom-text, #e6e2ff);
      background: var(--aegisaxiom-elevated, #16162e);
      border: 1px solid var(--aegisaxiom-border, #2a2a44);
      border-radius: 6px;
      padding: 10px 12px;
      white-space: pre-wrap;
      word-wrap: break-word;
      margin: 4px 0 0;
      max-height: 360px;
      overflow-y: auto;
    }
    .vega-doc-panel table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
      color: var(--aegisaxiom-text, #e6e2ff);
      margin-top: 4px;
    }
    .vega-doc-panel table th, .vega-doc-panel table td {
      text-align: left;
      padding: 4px 8px;
      border-bottom: 1px solid var(--aegisaxiom-border-soft, #1f1f3a);
      vertical-align: top;
    }
    .vega-doc-panel table th {
      color: var(--aegisaxiom-text-muted, #6e6a90);
      font-size: 11px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      font-weight: 600;
    }
    .vega-doc-panel .copy-btn {
      appearance: none;
      background: transparent;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      border: 1px solid var(--aegisaxiom-border, #2a2a44);
      font-size: 10px;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 4px;
      cursor: pointer;
      margin-left: 8px;
    }
    .vega-doc-panel .copy-btn:hover {
      color: var(--aegisaxiom-text, #e6e2ff);
      background: rgba(139, 92, 246, 0.1);
    }
  `;
  document.head.appendChild(style);
}

async function downloadFile(url, fallbackName = 'export') {
  // Use the rich blob downloader — api.get() routes through safeXhr,
  // which parses responses as JSON only and has no .blob() method, so
  // it cannot be used for binary file downloads (this was the root
  // cause of the design-doc export buttons doing nothing).
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
  const fname = (m && m[1]) || fallbackName;
  const objectUrl = URL.createObjectURL(res.blob);
  const a = document.createElement('a');
  a.href = objectUrl;
  a.download = fname;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(objectUrl);
}

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  if (!text) return null;
  return (
    <button
      type="button"
      className="copy-btn"
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? 'COPIED' : 'COPY'}
    </button>
  );
}

function DesignDocBody({ doc }) {
  if (!doc) return <p style={{ fontStyle: 'italic', color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}>No content.</p>;

  return (
    <>
      {doc.executive_summary ? (
        <>
          <h4>Executive Summary</h4>
          <p>{doc.executive_summary}</p>
        </>
      ) : null}

      {doc.current_state ? (
        <>
          <h4>Current State</h4>
          {doc.current_state.process_description ? <p>{doc.current_state.process_description}</p> : null}
          {doc.current_state.manual_effort ? (
            <div className="kv"><strong>Manual effort:</strong>{doc.current_state.manual_effort}</div>
          ) : null}
          {Array.isArray(doc.current_state.pain_points) && doc.current_state.pain_points.length > 0 ? (
            <>
              <div className="kv"><strong>Pain points:</strong></div>
              <ul>{doc.current_state.pain_points.map((p, i) => <li key={i}>{p}</li>)}</ul>
            </>
          ) : null}
          {Array.isArray(doc.current_state.systems_involved) && doc.current_state.systems_involved.length > 0 ? (
            <div className="kv"><strong>Systems:</strong>{doc.current_state.systems_involved.join(', ')}</div>
          ) : null}
        </>
      ) : null}

      {doc.proposed_ai_solution ? (
        <>
          <h4>Proposed AI Solution</h4>
          {doc.proposed_ai_solution.solution_description ? <p>{doc.proposed_ai_solution.solution_description}</p> : null}
          {doc.proposed_ai_solution.ai_role ? (
            <div className="kv"><strong>AI role:</strong>{doc.proposed_ai_solution.ai_role}</div>
          ) : null}
          {doc.proposed_ai_solution.human_role ? (
            <div className="kv"><strong>Human role:</strong>{doc.proposed_ai_solution.human_role}</div>
          ) : null}
          {doc.proposed_ai_solution.automation_percentage ? (
            <div className="kv"><strong>Automation:</strong>{doc.proposed_ai_solution.automation_percentage}</div>
          ) : null}
          {Array.isArray(doc.proposed_ai_solution.key_capabilities) && doc.proposed_ai_solution.key_capabilities.length > 0 ? (
            <>
              <div className="kv"><strong>Key capabilities:</strong></div>
              <ul>{doc.proposed_ai_solution.key_capabilities.map((c, i) => <li key={i}>{c}</li>)}</ul>
            </>
          ) : null}
        </>
      ) : null}

      {doc.bpmn_process_narrative ? (
        <>
          <h4>Process Narrative</h4>
          <p>{doc.bpmn_process_narrative}</p>
        </>
      ) : null}

      {Array.isArray(doc.integration_requirements) && doc.integration_requirements.length > 0 ? (
        <>
          <h4>Integration Requirements</h4>
          <table>
            <thead>
              <tr>
                <th>System</th><th>Type</th><th>Data</th><th>Complexity</th>
              </tr>
            </thead>
            <tbody>
              {doc.integration_requirements.map((ir, i) => (
                <tr key={i}>
                  <td>{ir.system_name || ''}</td>
                  <td>{ir.integration_type || ''}</td>
                  <td>{ir.data_exchanged || ''}</td>
                  <td>{ir.complexity || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}

      {doc.mmi_justification ? (
        <>
          <h4>MMI Justification</h4>
          <ul>
            {['tss', 'ie', 'time', 'cost', 'quality', 'satisfaction']
              .filter((k) => doc.mmi_justification[`${k}_rationale`])
              .map((k) => (
                <li key={k}>
                  <strong style={{ color: 'var(--aegisaxiom-text-muted, #6e6a90)', textTransform: 'uppercase', fontSize: 10 }}>
                    {k}:
                  </strong>{' '}
                  {doc.mmi_justification[`${k}_rationale`]}
                </li>
              ))}
          </ul>
        </>
      ) : null}

      {doc.implementation_considerations ? (
        <>
          <h4>Implementation Considerations</h4>
          {doc.implementation_considerations.estimated_complexity ? (
            <div className="kv">
              <strong>Estimated complexity:</strong>
              {doc.implementation_considerations.estimated_complexity}
            </div>
          ) : null}
          {[['Risks', 'risks'], ['Dependencies', 'dependencies'], ['Assumptions', 'assumptions'], ['Open questions', 'open_questions']].map(([label, key]) => {
            const items = doc.implementation_considerations[key];
            if (!Array.isArray(items) || items.length === 0) return null;
            return (
              <React.Fragment key={key}>
                <div className="kv"><strong>{label}:</strong></div>
                <ul>{items.map((it, i) => <li key={i}>{it}</li>)}</ul>
              </React.Fragment>
            );
          })}
        </>
      ) : null}

      {Array.isArray(doc.human_touchpoints) && doc.human_touchpoints.length > 0 ? (
        <>
          <h4>Human Touchpoints</h4>
          <ul>
            {doc.human_touchpoints.map((ht, i) => (
              <li key={i}>
                <strong>{ht.touchpoint || ''}</strong>
                {ht.reason ? <> — {ht.reason}</> : null}
                {ht.frequency ? <> ({ht.frequency})</> : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </>
  );
}

function EaddBody({ doc }) {
  if (!doc) return <p style={{ fontStyle: 'italic', color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}>No content.</p>;

  return (
    <>
      {doc.app_pitch_summary ? (
        <>
          <h4>Pitch Summary</h4>
          <p>{doc.app_pitch_summary}</p>
        </>
      ) : null}

      {doc.primary_persona_hint ? (
        <div className="kv"><strong>Primary persona:</strong>{doc.primary_persona_hint}</div>
      ) : null}

      {Array.isArray(doc.core_flows) && doc.core_flows.length > 0 ? (
        <>
          <h4>Core Flows</h4>
          <ul>
            {doc.core_flows.map((f, i) => {
              if (typeof f === 'string') return <li key={i}>{f}</li>;
              if (!f || typeof f !== 'object') return <li key={i}>{String(f)}</li>;
              const name = f.name || f.title || `Flow ${i + 1}`;
              const success = f.success_when || f.success_criteria || f.acceptance_criteria;
              const steps = Array.isArray(f.steps) ? f.steps : (typeof f.steps === 'string' ? [f.steps] : []);
              return (
                <li key={i} style={{ marginBottom: 8 }}>
                  <strong>{name}</strong>
                  {f.description ? <div style={{ marginTop: 2 }}>{f.description}</div> : null}
                  {steps.length > 0 ? (
                    <ul style={{ marginTop: 4, marginBottom: 4 }}>
                      {steps.map((s, j) => <li key={j}>{String(s)}</li>)}
                    </ul>
                  ) : null}
                  <div style={{ fontSize: 11, color: 'var(--aegisaxiom-text-muted, #9ca3af)', marginTop: 2 }}>
                    <strong>Success when:</strong>{' '}
                    {success || <em>(not captured — re-run analysis to extract)</em>}
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      ) : null}

      {Array.isArray(doc.data_model) && doc.data_model.length > 0 ? (
        <>
          <h4>Data Model</h4>
          <ul>
            {doc.data_model.map((entry, i) => {
              if (typeof entry === 'string') return <li key={i}>{entry}</li>;
              const fields = Array.isArray(entry.fields) ? entry.fields : entry.key_fields;
              return (
                <li key={i} style={{ marginBottom: 8 }}>
                  <strong style={{ color: 'var(--aegisaxiom-text, #e6e2ff)' }}>{entry.entity || entry.name || ''}</strong>
                  {entry.description ? <div style={{ fontSize: 11 }}>{entry.description}</div> : null}
                  {Array.isArray(fields) && fields.length > 0 ? (
                    <ul style={{ marginTop: 4 }}>
                      {fields.map((f, j) => {
                        if (typeof f === 'string') return <li key={j}>{f}</li>;
                        if (!f || typeof f !== 'object') return <li key={j}>{String(f)}</li>;
                        return (
                          <li key={j}>
                            <strong>{f.name}</strong>
                            {f.type ? <> ({f.type}{f.nullable === false ? ', required' : f.nullable === true ? ', nullable' : ''})</> : null}
                            {f.description ? <> — {f.description}</> : null}
                            {Array.isArray(f.enum_values) && f.enum_values.length > 0 ? (
                              <div style={{ fontSize: 10, color: 'var(--aegisaxiom-text-muted, #9ca3af)' }}>enum: {f.enum_values.join(', ')}</div>
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                  ) : null}
                  {entry.state_machine && (Array.isArray(entry.state_machine.states) || Array.isArray(entry.state_machine.transitions)) ? (
                    <div style={{ marginTop: 4, fontSize: 11 }}>
                      <strong>State machine:</strong>
                      {Array.isArray(entry.state_machine.states) && entry.state_machine.states.length > 0 ? (
                        <div>States: {entry.state_machine.states.join(', ')}</div>
                      ) : null}
                      {Array.isArray(entry.state_machine.transitions) && entry.state_machine.transitions.map((t, k) => (
                        <div key={k}>{t.from} → {t.to} on '{t.trigger}'</div>
                      ))}
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </>
      ) : null}

      {Array.isArray(doc.integrations) && doc.integrations.length > 0 ? (
        <>
          <h4>Integrations</h4>
          <ul>
            {doc.integrations.map((ig, i) => {
              if (!ig || typeof ig !== 'object') return <li key={i}>{String(ig)}</li>;
              const target = ig.target_name || ig.system || ig.name || '';
              const direction = ig.direction || '';
              const trigger = ig.trigger || '';
              const payload = ig.payload;
              return (
                <li key={i} style={{ marginBottom: 8 }}>
                  <strong style={{ color: 'var(--aegisaxiom-text, #e6e2ff)' }}>{target}</strong>
                  {direction ? <> ({direction})</> : null}
                  {ig.purpose ? <> — {ig.purpose}</> : null}
                  {trigger ? <div style={{ fontSize: 11 }}>Trigger: {trigger}</div> : null}
                  {Array.isArray(payload) && payload.length > 0 ? (
                    <div style={{ fontSize: 11 }}>
                      Payload:
                      <ul>
                        {payload.map((pf, j) => {
                          if (typeof pf === 'string') return <li key={j}>{pf}</li>;
                          if (!pf || typeof pf !== 'object') return <li key={j}>{String(pf)}</li>;
                          return <li key={j}><strong>{pf.field}</strong> ({pf.type}){pf.description ? <> — {pf.description}</> : null}</li>;
                        })}
                      </ul>
                    </div>
                  ) : (typeof payload === 'string' && payload ? (
                    <div style={{ fontSize: 11 }}>Payload: {payload}</div>
                  ) : null)}
                  {ig.auth_note ? <div style={{ fontSize: 11 }}>Auth: {ig.auth_note}</div> : null}
                </li>
              );
            })}
          </ul>
        </>
      ) : null}

      {Array.isArray(doc.business_rules) && doc.business_rules.length > 0 ? (
        <>
          <h4>Business Rules</h4>
          <ul>
            {doc.business_rules.map((r, i) => {
              if (typeof r === 'string') return <li key={i}>{r}</li>;
              if (!r || typeof r !== 'object') return <li key={i}>{String(r)}</li>;
              return (
                <li key={i}>
                  <strong>{r.name || ''}</strong>: {r.rule || ''}
                  {r.rationale ? <div style={{ fontSize: 11, color: 'var(--aegisaxiom-text-muted, #9ca3af)' }}>Rationale: {r.rationale}</div> : null}
                </li>
              );
            })}
          </ul>
        </>
      ) : null}

      {Array.isArray(doc.api_surface) && doc.api_surface.length > 0 ? (
        <>
          <h4>API Surface</h4>
          <ul>
            {doc.api_surface.map((ep, i) => {
              if (!ep || typeof ep !== 'object') return <li key={i}>{String(ep)}</li>;
              return (
                <li key={i} style={{ marginBottom: 6 }}>
                  <code>{ep.method} {ep.path}</code>
                  {ep.purpose ? <> — {ep.purpose}</> : null}
                  {ep.auth_required ? <div style={{ fontSize: 11 }}>Auth: {ep.auth_required}</div> : null}
                  {ep.request_shape ? <div style={{ fontSize: 11 }}>Request: <code>{ep.request_shape}</code></div> : null}
                  {ep.response_shape ? <div style={{ fontSize: 11 }}>Response: <code>{ep.response_shape}</code></div> : null}
                </li>
              );
            })}
          </ul>
        </>
      ) : null}

      {doc.auth_model && (doc.auth_model.session_model || (Array.isArray(doc.auth_model.roles) && doc.auth_model.roles.length > 0)) ? (
        <>
          <h4>Auth Model (app users)</h4>
          {doc.auth_model.session_model ? <div><strong>Session:</strong> {doc.auth_model.session_model}</div> : null}
          {Array.isArray(doc.auth_model.roles) && doc.auth_model.roles.length > 0 ? (
            <ul>
              {doc.auth_model.roles.map((r, i) => {
                if (typeof r === 'string') return <li key={i}>{r}</li>;
                if (!r || typeof r !== 'object') return <li key={i}>{String(r)}</li>;
                const perms = Array.isArray(r.permissions) ? r.permissions.join('; ') : '';
                return <li key={i}><strong>{r.name}</strong>{perms ? `: ${perms}` : ''}</li>;
              })}
            </ul>
          ) : null}
          {doc.auth_model.ownership_model ? <div><strong>Ownership:</strong> {doc.auth_model.ownership_model}</div> : null}
        </>
      ) : null}

      {doc.observability && (Array.isArray(doc.observability.events_to_log) || Array.isArray(doc.observability.metrics_to_track) || Array.isArray(doc.observability.alerts)) ? (
        <>
          <h4>Observability</h4>
          {Array.isArray(doc.observability.events_to_log) && doc.observability.events_to_log.length > 0 ? (
            <div>Events: <ul>{doc.observability.events_to_log.map((e, i) => <li key={i}>{String(e)}</li>)}</ul></div>
          ) : null}
          {Array.isArray(doc.observability.metrics_to_track) && doc.observability.metrics_to_track.length > 0 ? (
            <div>Metrics: <ul>{doc.observability.metrics_to_track.map((m, i) => <li key={i}>{String(m)}</li>)}</ul></div>
          ) : null}
          {Array.isArray(doc.observability.alerts) && doc.observability.alerts.length > 0 ? (
            <div>Alerts: <ul>{doc.observability.alerts.map((a, i) => <li key={i}>{String(a)}</li>)}</ul></div>
          ) : null}
        </>
      ) : null}

      {doc.file_handling && (Array.isArray(doc.file_handling.accepts) && doc.file_handling.accepts.length > 0) ? (
        <>
          <h4>File Handling</h4>
          <div>Accepts: {doc.file_handling.accepts.join(', ')}</div>
          {doc.file_handling.extraction_strategy ? <div>Extraction: {doc.file_handling.extraction_strategy}</div> : null}
          {doc.file_handling.max_size_mb ? <div>Max size: {doc.file_handling.max_size_mb} MB</div> : null}
        </>
      ) : null}

      {Array.isArray(doc.out_of_scope) && doc.out_of_scope.length > 0 ? (
        <>
          <h4>Out of Scope</h4>
          <ul>{doc.out_of_scope.map((o, i) => <li key={i}>{o}</li>)}</ul>
        </>
      ) : null}

      {doc.visual_style ? (
        <>
          <h4>Visual Style</h4>
          <p>{doc.visual_style}</p>
        </>
      ) : null}

      {Array.isArray(doc.linked_workflow_ids) && doc.linked_workflow_ids.length > 0 ? (
        <div className="kv"><strong>Linked workflows:</strong>{doc.linked_workflow_ids.join(', ')}</div>
      ) : null}

      {doc.starter_prompt ? (
        <>
          <h4>
            Starter Prompt (POC)
            <CopyButton text={doc.starter_prompt} />
          </h4>
          <pre className="starter-prompt">{doc.starter_prompt}</pre>
        </>
      ) : null}
    </>
  );
}

export default function DesignDocPanel({ doc, kind, entityId, artifactId }) {
  // kind: "design_doc" (workflow) | "eadd" (app)
  const [busy, setBusy] = useState(null); // 'pdf' | 'gdoc' | null
  const [error, setError] = useState(null);

  const triggerExport = async (format) => {
    if (!artifactId || !entityId) {
      setError('Cannot export — artifact or entity ID missing.');
      return;
    }
    setError(null);
    setBusy(format);
    try {
      if (format === 'gdoc') {
        const params = new URLSearchParams({ target: kind, entity_id: entityId });
        const res = await api.post(
          `${API_URL}/api/vega-artifacts/${artifactId}/export/google-doc?${params.toString()}`,
        );
        if (!res.ok) {
          const detail = await res.json().catch(() => ({}));
          throw new Error(detail.detail || `Google Doc export failed: ${res.status}`);
        }
        const data = await res.json();
        if (data.doc_url) window.open(data.doc_url, '_blank', 'noopener,noreferrer');
      } else if (format === 'pdf') {
        // Client-side PDF: pull the full payload once so we can look up
        // linked workflows / shared infra and embed the BPMN SVG, then
        // hand off to the v3 exporter that mirrors the in-app theme.
        const params = new URLSearchParams({ target: 'analysis', format: 'json' });
        const res = await api.get(
          `${API_URL}/api/vega-artifacts/${artifactId}/export?${params.toString()}`,
        );
        if (!res.ok) {
          const detail = await res.json().catch(() => ({}));
          throw new Error(detail.detail || `Failed to load artifact for PDF: ${res.status}`);
        }
        const payload = await res.json();
        if (kind === 'design_doc') {
          const wf = (payload.workflows || []).find((w) => w.workflow_id === entityId);
          if (!wf) throw new Error('Workflow not found in artifact.');
          await exportV3DesignDocPdf({ workflow: wf, payload });
        } else if (kind === 'eadd') {
          const app = (payload.apps || []).find((a) => a.app_id === entityId);
          if (!app) throw new Error('App not found in artifact.');
          await exportV3EaddPdf({ app, payload });
        }
      } else {
        const params = new URLSearchParams({ target: kind, entity_id: entityId, format });
        await downloadFile(
          `${API_URL}/api/vega-artifacts/${artifactId}/export?${params.toString()}`,
          `vega_v3_${kind}_${entityId}.md`,
        );
      }
    } catch (err) {
      setError(err.message || 'Export failed.');
    } finally {
      setBusy(null);
    }
  };

  const Body = kind === 'eadd' ? EaddBody : DesignDocBody;

  return (
    <div className="vega-doc-panel">
      {artifactId && entityId ? (
        <div className="actions">
          <button type="button" disabled={busy != null} onClick={() => triggerExport('pdf')}>
            {busy === 'pdf' ? '…' : 'PDF'}
          </button>
          <button type="button" disabled={busy != null} onClick={() => triggerExport('markdown')}>
            {busy === 'markdown' ? '…' : 'Markdown'}
          </button>
          <button type="button" disabled={busy != null} onClick={() => triggerExport('gdoc')}>
            {busy === 'gdoc' ? '…' : 'Google Doc'}
          </button>
        </div>
      ) : null}
      {error ? (
        <div style={{
          padding: 8, marginBottom: 8, fontSize: 11, color: '#f87171',
          border: '1px solid rgba(248, 113, 113, 0.3)', borderRadius: 4,
        }}>
          {error}
        </div>
      ) : null}
      <Body doc={doc} />
    </div>
  );
}
