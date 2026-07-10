import React from 'react';
import FlagPill from './FlagPill';

const STYLE_ID = 'vega-v3-overview-styles';

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .vega-overview-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 8px;
      margin-bottom: 14px;
    }
    .vega-stat {
      background: var(--aegisaxiom-surface, #0f0f24);
      border: 1px solid var(--aegisaxiom-border, #2a2a44);
      border-radius: 8px;
      padding: 8px 10px;
    }
    .vega-stat .lab {
      font-size: 10px;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }
    .vega-stat .val {
      font-size: 18px;
      font-weight: 700;
      color: var(--aegisaxiom-text, #e6e2ff);
      margin-top: 2px;
    }
    .vega-overview-section {
      margin-top: 14px;
    }
    .vega-overview-section .lab {
      font-size: 10px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      margin-bottom: 6px;
    }
    .vega-overview-section table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
      color: var(--aegisaxiom-text, #e6e2ff);
    }
    .vega-overview-section th, .vega-overview-section td {
      text-align: left;
      padding: 6px 8px;
      border-bottom: 1px solid var(--aegisaxiom-border-soft, #1f1f3a);
      vertical-align: top;
    }
    .vega-overview-section th {
      font-weight: 600;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      font-size: 11px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .vega-flag-row {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      align-items: center;
      font-size: 12px;
      color: var(--aegisaxiom-text, #e6e2ff);
      padding: 4px 0;
    }
    .vega-flag-row .label {
      color: var(--aegisaxiom-text-muted, #6e6a90);
      min-width: 160px;
    }
  `;
  document.head.appendChild(style);
}

function SessionFlagRow({ label, value, flag, valueRender }) {
  return (
    <div className="vega-flag-row">
      <span className="label">{label}</span>
      {value != null && value !== '' ? (
        <span>{valueRender ? valueRender(value) : String(value)}</span>
      ) : flag ? (
        <FlagPill flag={flag} />
      ) : (
        <span style={{ color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}>—</span>
      )}
    </div>
  );
}

export default function OverviewTab({ payload }) {
  const workflows = payload?.workflows || [];
  const apps = payload?.apps || [];
  const standalone = apps.filter((a) => !a.linked_workflow_ids || a.linked_workflow_ids.length === 0);
  const sharedInfra = payload?.shared_infrastructure || [];
  const openItems = payload?.open_items || [];
  const flags = payload?.session_flags || {};

  return (
    <div>
      <div className="vega-overview-grid">
        <div className="vega-stat">
          <div className="lab">Workflows</div>
          <div className="val">{workflows.length}</div>
        </div>
        <div className="vega-stat">
          <div className="lab">Apps</div>
          <div className="val">{apps.length}</div>
        </div>
        <div className="vega-stat">
          <div className="lab">Standalone Apps</div>
          <div className="val">{standalone.length}</div>
        </div>
        <div className="vega-stat">
          <div className="lab">Open Items</div>
          <div className="val">{openItems.length}</div>
        </div>
      </div>

      <div className="vega-overview-section">
        <div className="lab">Session Flags</div>
        <SessionFlagRow
          label="Technical contact"
          value={flags.tech_contact}
          flag={flags.tech_contact_flag}
        />
        <SessionFlagRow
          label="Engineering capacity"
          value={flags.engineering_capacity}
          flag={flags.engineering_capacity_flag}
          valueRender={(v) => (v === true ? 'Yes' : v === false ? 'No' : String(v))}
        />
        <SessionFlagRow
          label="IT / infosec gate"
          value={flags.it_infosec_gate}
          flag={flags.it_infosec_gate_flag}
          valueRender={(v) => (v === true ? 'Yes' : v === false ? 'No' : String(v))}
        />
      </div>

      {apps.length >= 2 ? (
        <div className="vega-overview-section">
          <div className="lab">Shared Infrastructure</div>
          {sharedInfra.length === 0 ? (
            <div style={{ fontSize: 11, color: 'var(--aegisaxiom-text-muted, #6e6a90)', fontStyle: 'italic' }}>
              No systems shared across multiple apps.
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>System</th>
                  <th>Used by</th>
                  <th>R/W</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {sharedInfra.map((row, i) => (
                  <tr key={`${row.system_name}-${i}`}>
                    <td>{row.system_name}</td>
                    <td>{(row.used_by_app_ids || []).join(', ')}</td>
                    <td>{row.read_write || '—'}</td>
                    <td>{row.notes || ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : null}
    </div>
  );
}
