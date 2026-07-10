import React, { useState } from 'react';
import FlagPill from './FlagPill';
import DocStatusBadge from './DocStatusBadge';
import DesignDocPanel from './DesignDocPanel';

const STYLE_ID = 'vega-v3-shared-blocks-styles';

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .vega-v3-block {
      margin-top: 12px;
      background: var(--aegisaxiom-elevated, #16162e);
      border: 1px solid var(--aegisaxiom-border, #2a2a44);
      border-radius: 10px;
      padding: 10px 12px;
    }
    .vega-v3-block .head {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 6px;
    }
    .vega-v3-block .head .kind {
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      padding: 1px 7px;
      border-radius: 999px;
      border: 1px solid transparent;
    }
    .vega-v3-block .head .kind.ai {
      background: rgba(59, 130, 246, 0.12);
      color: #3b82f6;
      border-color: rgba(59, 130, 246, 0.35);
    }
    .vega-v3-block .head .kind.app {
      background: rgba(139, 92, 246, 0.12);
      color: #8b5cf6;
      border-color: rgba(139, 92, 246, 0.35);
    }
    .vega-v3-block .head .name {
      font-weight: 600;
      color: var(--aegisaxiom-text, #e6e2ff);
      font-size: 13px;
    }
    .vega-v3-block .head .spacer { flex: 1; }
    .vega-v3-block .lab {
      font-size: 10px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      margin-top: 10px;
      margin-bottom: 4px;
    }
    .vega-v3-block table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
      color: var(--aegisaxiom-text, #e6e2ff);
    }
    .vega-v3-block th, .vega-v3-block td {
      text-align: left;
      padding: 5px 8px;
      border-bottom: 1px solid var(--aegisaxiom-border-soft, #1f1f3a);
      vertical-align: top;
    }
    .vega-v3-block th {
      font-weight: 600;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      font-size: 11px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .vega-v3-block .empty {
      font-size: 11px;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      font-style: italic;
    }
  `;
  document.head.appendChild(style);
}

function PersonaTable({ personas }) {
  if (!personas || personas.length === 0) {
    return <div className="empty">No personas captured.</div>;
  }
  return (
    <table>
      <thead>
        <tr>
          <th>Role</th>
          <th>Count</th>
          <th>Device</th>
          <th>Tech Literacy</th>
          <th>Flags</th>
        </tr>
      </thead>
      <tbody>
        {personas.map((p, i) => (
          <tr key={`${p.role || 'unknown'}-${i}`}>
            <td>{p.role || 'unknown'}</td>
            <td>{p.count != null ? p.count : <span style={{ color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}>—</span>}</td>
            <td>{p.device || <span style={{ color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}>—</span>}</td>
            <td>{p.technical_literacy || <span style={{ color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}>—</span>}</td>
            <td>
              {Array.isArray(p.flags) && p.flags.length > 0 ? (
                <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
                  {p.flags.map((f, fi) => <FlagPill key={fi} flag={f} />)}
                </span>
              ) : null}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const BUCKET_LABELS = {
  1: '1 — Pre-research',
  2: '2 — Transcript-confirmed',
  3: '3 — Needs technical follow-up',
};

function IntegrationList({ integrations }) {
  if (!integrations || integrations.length === 0) {
    return <div className="empty">No integrations captured.</div>;
  }
  return (
    <table>
      <thead>
        <tr>
          <th>System</th>
          <th>Bucket</th>
          <th>Notes</th>
          <th>Flags</th>
        </tr>
      </thead>
      <tbody>
        {integrations.map((it, i) => (
          <tr key={`${it.system_name}-${i}`}>
            <td>{it.system_name}</td>
            <td>{BUCKET_LABELS[it.bucket] || (it.bucket != null ? String(it.bucket) : '—')}</td>
            <td>{it.notes || <span style={{ color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}>—</span>}</td>
            <td>
              {Array.isArray(it.flags) && it.flags.length > 0 ? (
                <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
                  {it.flags.map((f, fi) => <FlagPill key={fi} flag={f} />)}
                </span>
              ) : null}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ScaleProfile({ scale }) {
  if (!scale) return <div className="empty">No scale signals captured.</div>;
  const entries = [
    ['Transaction volume', scale.transaction_volume],
    ['Retention window', scale.retention_window],
    ['Growth trajectory', scale.growth_trajectory],
    ['Speed / storage pain', scale.speed_or_storage_pain],
    ['Inference', scale.inference],
  ].filter(([_, v]) => v != null && v !== '');
  if (entries.length === 0 && !scale.flag) return <div className="empty">No scale signals captured.</div>;
  return (
    <table>
      <tbody>
        {entries.map(([label, value]) => (
          <tr key={label}>
            <td style={{ width: '35%', color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}>{label}</td>
            <td>{value}</td>
          </tr>
        ))}
        {scale.flag ? (
          <tr>
            <td style={{ width: '35%', color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}>Flag</td>
            <td><FlagPill flag={scale.flag} /></td>
          </tr>
        ) : null}
      </tbody>
    </table>
  );
}

function Nfrs({ nfrs }) {
  if (!nfrs) return <div className="empty">No NFRs captured.</div>;
  const entries = [
    ['Device support', nfrs.device_support],
    ['Browser support', nfrs.browser_support],
    ['Data freshness', nfrs.data_freshness],
    ['RBAC', nfrs.rbac],
    ['SLA', nfrs.sla],
    ['Compliance', nfrs.compliance],
    ['Multi-region', nfrs.multi_region],
  ].filter(([_, v]) => v != null && v !== '');
  const flags = Array.isArray(nfrs.flags) ? nfrs.flags : [];
  if (entries.length === 0 && flags.length === 0) {
    return <div className="empty">No NFRs captured.</div>;
  }
  return (
    <table>
      <tbody>
        {entries.map(([label, value]) => (
          <tr key={label}>
            <td style={{ width: '35%', color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}>{label}</td>
            <td>{value}</td>
          </tr>
        ))}
        {flags.length > 0 ? (
          <tr>
            <td style={{ width: '35%', color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}>Flags</td>
            <td>
              <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
                {flags.map((f, fi) => <FlagPill key={fi} flag={f} />)}
              </span>
            </td>
          </tr>
        ) : null}
      </tbody>
    </table>
  );
}

export default function AppBlock({ app, artifactId, callMode = 'single_call' }) {
  const [eaddOpen, setEaddOpen] = useState(false);
  if (!app) return null;
  const eadd = app.eadd_doc;
  const eaddStatus = app.design_doc_status;
  const isCall3OrFull = callMode === 'call3' || callMode === 'single_call';
  // Wave 7 fix — only "build_for_customer" apps get an EADD panel. Existing
  // tools (Google Sheets, Salesforce, etc.) still appear in the list (they
  // showed up in the transcript) but with a clear "Existing tool" tag and
  // no design-doc affordances. Default to build_for_customer so legacy
  // artifacts without the field still render their EADDs.
  const buildIntent = app.build_intent || 'build_for_customer';
  const isBuildable = buildIntent === 'build_for_customer';
  const showEaddPanel = isCall3OrFull && isBuildable && (eadd || eaddStatus === 'failed');

  return (
    <div className="vega-v3-block">
      <div className="head">
        <span className="kind app">App</span>
        <span className="name">{app.app_name}</span>
        {app.complexity ? <FlagPill complexity={app.complexity} /> : null}
        {!isBuildable ? (
          <span
            title={
              buildIntent === 'existing_tool'
                ? 'Customer already uses this tool — no design doc needed'
                : 'Build intent unclear — flagged in Open Items'
            }
            style={{
              fontSize: 10,
              fontWeight: 600,
              padding: '1px 6px',
              borderRadius: 999,
              background:
                buildIntent === 'existing_tool'
                  ? 'rgba(148, 163, 184, 0.15)'
                  : 'rgba(245, 158, 11, 0.15)',
              color: buildIntent === 'existing_tool' ? '#94a3b8' : '#f59e0b',
              border:
                buildIntent === 'existing_tool'
                  ? '1px solid rgba(148, 163, 184, 0.3)'
                  : '1px solid rgba(245, 158, 11, 0.3)',
              letterSpacing: '0.04em',
            }}
          >
            {buildIntent === 'existing_tool' ? 'Existing tool' : 'Build intent unclear'}
          </span>
        ) : null}
        <span className="spacer" />
        {isCall3OrFull && isBuildable && eaddStatus ? (
          <DocStatusBadge status={eaddStatus} label="App Design Doc" />
        ) : null}
      </div>
      <div className="lab">Personas</div>
      <PersonaTable personas={app.personas} />
      <div className="lab">Integrations (3-bucket)</div>
      <IntegrationList integrations={app.integrations} />
      <div className="lab">Scale</div>
      <ScaleProfile scale={app.scale_profile} />
      {callMode !== 'call1' ? (
        <>
          <div className="lab">NFRs</div>
          <Nfrs nfrs={app.nfrs} />
        </>
      ) : null}

      {showEaddPanel ? (
        <div style={{ marginTop: 12 }}>
          <button
            type="button"
            onClick={() => setEaddOpen((v) => !v)}
            style={{
              appearance: 'none',
              width: '100%',
              textAlign: 'left',
              background: 'rgba(139, 92, 246, 0.06)',
              color: '#a78bfa',
              border: '1px solid rgba(139, 92, 246, 0.3)',
              borderRadius: 6,
              padding: '6px 10px',
              fontSize: 12,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            {eaddOpen ? '▾' : '▸'} {eaddOpen ? 'Hide App Design Document' : 'View App Design Document (full spec + builder prompt)'}
          </button>
          {eaddOpen ? (
            eadd ? (
              <DesignDocPanel
                doc={eadd}
                kind="eadd"
                entityId={app.app_id}
                artifactId={artifactId}
              />
            ) : (
              <div style={{
                marginTop: 8, padding: 10, fontSize: 12, color: '#f87171',
                border: '1px solid rgba(248, 113, 113, 0.3)',
                background: 'rgba(248, 113, 113, 0.06)', borderRadius: 6,
              }}>
                App Design Document generation failed. Re-run the analysis or escalate to technical follow-up.
              </div>
            )
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
