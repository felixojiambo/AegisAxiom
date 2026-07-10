import React, { useState } from 'react';
import FlagPill from './FlagPill';
import DocStatusBadge from './DocStatusBadge';
import DesignDocPanel from './DesignDocPanel';

const MMI_DIMS = [
  { key: 'tss', label: 'TSS' },
  { key: 'ie', label: 'IE' },
  { key: 'time', label: 'Time' },
  { key: 'cost', label: 'Cost' },
  { key: 'quality', label: 'Quality' },
  { key: 'satisfaction', label: 'Satisfaction' },
];

export default function AIEmployeeBlock({ workflow, artifactId, callMode = 'single_call' }) {
  const [docOpen, setDocOpen] = useState(false);
  if (!workflow) return null;
  const mmi = workflow.mmi_hypothesis || {};
  const score = mmi.score;
  const designDoc = workflow.design_doc;
  const designStatus = workflow.design_doc_status;
  const isCall3OrFull = callMode === 'call3' || callMode === 'single_call';

  return (
    <div className="vega-v3-block">
      <div className="head">
        <span className="kind ai">AI Employee</span>
        <span className="name">{workflow.workflow_name}</span>
        <span className="spacer" />
        {score != null ? <FlagPill score={score} /> : null}
        {isCall3OrFull && designStatus ? <DocStatusBadge status={designStatus} label="Design doc" /> : null}
      </div>
      <div className="lab">MMI Hypothesis</div>
      {callMode === 'call1' ? (
        <div className="vega-v3-mmi-pending-notice">
          MMI not yet scored — runs in Call 2 (depth pass)
        </div>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>Dim</th>
                <th>Value</th>
                <th>Reasoning</th>
              </tr>
            </thead>
            <tbody>
              {MMI_DIMS.map(({ key, label }) => {
                const dim = mmi[key] || {};
                return (
                  <tr key={key}>
                    <td style={{ width: 90, color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}>{label}</td>
                    <td style={{ width: 70 }}>
                      {dim.value != null ? dim.value : <FlagPill flag={dim.value_flag} />}
                    </td>
                    <td>{dim.reasoning || ''}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {Array.isArray(mmi.needs_client_input) && mmi.needs_client_input.length > 0 ? (
            <>
              <div className="lab">Needs client input</div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                {mmi.needs_client_input.map((item, i) => (
                  <li key={i} style={{ color: 'var(--aegisaxiom-text, #e6e2ff)' }}>{item}</li>
                ))}
              </ul>
            </>
          ) : null}
        </>
      )}

      {isCall3OrFull && (designDoc || designStatus === 'failed') ? (
        <div style={{ marginTop: 12 }}>
          <button
            type="button"
            onClick={() => setDocOpen((v) => !v)}
            style={{
              appearance: 'none',
              width: '100%',
              textAlign: 'left',
              background: 'rgba(59, 130, 246, 0.06)',
              color: '#3b82f6',
              border: '1px solid rgba(59, 130, 246, 0.3)',
              borderRadius: 6,
              padding: '6px 10px',
              fontSize: 12,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            {docOpen ? '▾' : '▸'} {docOpen ? 'Hide AI Design Document' : 'View AI Design Document'}
          </button>
          {docOpen ? (
            designDoc ? (
              <DesignDocPanel
                doc={designDoc}
                kind="design_doc"
                entityId={workflow.workflow_id}
                artifactId={artifactId}
              />
            ) : (
              <div style={{
                marginTop: 8, padding: 10, fontSize: 12, color: '#f87171',
                border: '1px solid rgba(248, 113, 113, 0.3)',
                background: 'rgba(248, 113, 113, 0.06)', borderRadius: 6,
              }}>
                Design document generation failed. Re-run the analysis or escalate to technical follow-up.
              </div>
            )
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
