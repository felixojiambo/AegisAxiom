import React, { useState } from 'react';
import FlagPill from './FlagPill';
import DocStatusBadge from './DocStatusBadge';
import BpmnViewer from './BpmnViewer';
import AIEmployeeBlock from './AIEmployeeBlock';
import AppBlock from './AppBlock';

const STYLE_ID = 'vega-v3-workflow-row-styles';

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .vega-wf-row {
      margin: 8px 0;
      background: var(--aegisaxiom-surface, #0f0f24);
      border: 1px solid var(--aegisaxiom-border, #2a2a44);
      border-radius: 10px;
      overflow: hidden;
    }
    .vega-wf-row > .summary {
      padding: 10px 14px;
      display: flex;
      align-items: center;
      gap: 10px;
      cursor: pointer;
      user-select: none;
    }
    .vega-wf-row > .summary:hover {
      background: rgba(139, 92, 246, 0.04);
    }
    .vega-wf-row .summary .name {
      font-weight: 600;
      color: var(--aegisaxiom-text, #e6e2ff);
      font-size: 13px;
      flex: 1;
    }
    .vega-wf-row .summary .chev {
      color: var(--aegisaxiom-text-muted, #6e6a90);
      font-size: 14px;
      transition: transform 0.15s;
    }
    .vega-wf-row.open .summary .chev { transform: rotate(90deg); }
    .vega-wf-row .body {
      padding: 10px 14px 14px;
      border-top: 1px solid var(--aegisaxiom-border-soft, #1f1f3a);
      background: var(--aegisaxiom-base, #08081a);
    }
    .vega-wf-row .source-quote {
      font-size: 12px;
      font-style: italic;
      color: var(--aegisaxiom-text, #e6e2ff);
      padding: 8px 10px;
      background: rgba(139, 92, 246, 0.06);
      border-left: 3px solid #8b5cf6;
      border-radius: 4px;
      margin-top: 8px;
    }
    .vega-wf-row .source-meta {
      font-size: 11px;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      margin-top: 4px;
    }
    .vega-wf-row .lab {
      font-size: 10px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      margin-top: 14px;
      margin-bottom: 6px;
    }
    .vega-wf-row .ac-list {
      list-style: none;
      padding-left: 0;
      margin: 0;
    }
    .vega-wf-row .ac-list li {
      padding: 4px 0;
      border-bottom: 1px solid var(--aegisaxiom-border-soft, #1f1f3a);
      font-size: 12px;
      color: var(--aegisaxiom-text, #e6e2ff);
      display: flex;
      gap: 8px;
      align-items: flex-start;
    }
    .vega-wf-row .ac-list li:last-child { border-bottom: none; }
    .vega-wf-row .ac-list .marker {
      color: #10b981;
      flex-shrink: 0;
      margin-top: 2px;
    }
    .vega-wf-row .step-list {
      counter-reset: step;
      padding-left: 0;
      margin: 0;
      list-style: none;
    }
    .vega-wf-row .step-list li {
      padding: 4px 0;
      font-size: 12px;
      color: var(--aegisaxiom-text, #e6e2ff);
      counter-increment: step;
    }
    .vega-wf-row .step-list li::before {
      content: counter(step) ".";
      color: var(--aegisaxiom-text-muted, #6e6a90);
      margin-right: 6px;
    }
    .vega-wf-row .partial-banner {
      margin: 8px 0;
      padding: 8px 10px;
      font-size: 11px;
      color: #f87171;
      background: rgba(248, 113, 113, 0.08);
      border: 1px solid rgba(248, 113, 113, 0.3);
      border-radius: 4px;
    }
    .vega-wf-row .empty {
      font-size: 11px;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      font-style: italic;
    }
  `;
  document.head.appendChild(style);
}

function formatTimestamp(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return null;
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`;
}

function SourceCitation({ source }) {
  if (!source) return null;
  const ts = formatTimestamp(source.timestamp_seconds);
  if (!source.quote && !ts && !source.speaker && !source.transcript_filename) return null;
  return (
    <>
      {source.quote ? <div className="source-quote">"{source.quote}"</div> : null}
      <div className="source-meta">
        {source.speaker ? <>{source.speaker}</> : null}
        {ts ? <> · {ts}</> : null}
        {source.transcript_filename ? <> · {source.transcript_filename}</> : null}
        {source.source_flag ? <> · <FlagPill flag={source.source_flag} /></> : null}
      </div>
    </>
  );
}

export default function WorkflowRow({ workflow, apps, artifactId, callMode = 'single_call' }) {
  const [open, setOpen] = useState(false);
  if (!workflow) return null;

  const isCall3OrFull = callMode === 'call3' || callMode === 'single_call';

  const linkedApps = (apps || []).filter((app) =>
    Array.isArray(app.linked_workflow_ids) &&
    app.linked_workflow_ids.includes(workflow.workflow_id),
  );

  return (
    <div className={`vega-wf-row ${open ? 'open' : ''}`}>
      <div
        className="summary"
        onClick={() => setOpen((v) => !v)}
        role="button"
        aria-expanded={open}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setOpen((v) => !v);
          }
        }}
      >
        <span className="chev">▸</span>
        <span className="name">{workflow.workflow_name}</span>
        {workflow.complexity ? <FlagPill complexity={workflow.complexity} /> : null}
        {workflow.mmi_hypothesis?.score != null ? (
          <FlagPill score={workflow.mmi_hypothesis.score} />
        ) : null}
        {workflow.bpmn_status ? (
          <DocStatusBadge status={workflow.bpmn_status === 'generated' ? 'generated' : workflow.bpmn_status} label="BPMN" />
        ) : null}
        {workflow.design_doc_status ? (
          <DocStatusBadge status={workflow.design_doc_status} label="Design doc" />
        ) : null}
      </div>

      {open ? (
        <div className="body">
          {workflow.partial_extraction_failed ? (
            <div className="partial-banner">
              Partial extraction — {workflow.partial_extraction_reason || 'detail call did not complete'}
            </div>
          ) : null}

          <SourceCitation source={workflow.source} />

          <div className="lab">Acceptance Criteria</div>
          {Array.isArray(workflow.acceptance_criteria) && workflow.acceptance_criteria.length > 0 ? (
            <ul className="ac-list">
              {workflow.acceptance_criteria.map((ac, i) => (
                <li key={i}>
                  <span className="marker">✓</span>
                  <span>
                    {ac.flow_name ? (
                      <strong style={{ color: 'var(--aegisaxiom-text, #e6e2ff)' }}>{ac.flow_name}: </strong>
                    ) : null}
                    {ac.success_when || (
                      <span style={{ color: 'var(--aegisaxiom-text-muted, #6e6a90)', fontStyle: 'italic' }}>
                        success criterion not captured
                      </span>
                    )}
                    {ac.flag ? <> <FlagPill flag={ac.flag} /></> : null}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="empty">No acceptance criteria captured.</div>
          )}

          {isCall3OrFull && (workflow.bpmn_xml ? (
            <>
              <div className="lab">BPMN Diagram</div>
              <BpmnViewer inlineXml={workflow.bpmn_xml} bpmnId={workflow.bpmn_id} height={320} />
            </>
          ) : workflow.bpmn_id ? (
            <>
              <div className="lab">BPMN Diagram</div>
              <BpmnViewer bpmnId={workflow.bpmn_id} height={320} />
            </>
          ) : null)}

          {callMode !== 'call1' && Array.isArray(workflow.process_steps) && workflow.process_steps.length > 0 ? (
            <>
              <div className="lab">Process Steps</div>
              <ol className="step-list">
                {workflow.process_steps.map((step, i) => (
                  <li key={step.id || i}>
                    <strong style={{ color: 'var(--aegisaxiom-text, #e6e2ff)' }}>
                      {step.name || step.id || `Step ${i + 1}`}
                    </strong>
                    {step.description ? (
                      <span style={{ color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}> — {step.description}</span>
                    ) : null}
                    {step.task_type ? (
                      <span style={{ color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}> [{step.task_type}]</span>
                    ) : null}
                    {step.actor_id ? (
                      <span style={{ color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}> · {step.actor_id}</span>
                    ) : null}
                    {step.inferred ? <> <FlagPill flag="INFERRED_CONFIRM" /></> : null}
                  </li>
                ))}
              </ol>
            </>
          ) : null}

          {callMode !== 'call1' && Array.isArray(workflow.decision_logic) && workflow.decision_logic.length > 0 ? (
            <>
              <div className="lab">Decision Logic</div>
              <ul style={{ paddingLeft: 18, margin: 0, fontSize: 12 }}>
                {workflow.decision_logic.map((d, i) => (
                  <li key={d.id || i} style={{ color: 'var(--aegisaxiom-text, #e6e2ff)' }}>
                    <strong style={{ color: 'var(--aegisaxiom-text, #e6e2ff)' }}>
                      {d.name || d.id || `Decision ${i + 1}`}
                    </strong>
                    {d.type ? (
                      <span style={{ color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}> [{d.type}]</span>
                    ) : null}
                    {d.description ? (
                      <span style={{ color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}> — {d.description}</span>
                    ) : null}
                    {Array.isArray(d.branches) && d.branches.length > 0 ? (
                      <ul style={{ marginTop: 2, paddingLeft: 18, fontSize: 11 }}>
                        {d.branches.map((b, bi) => (
                          <li key={bi} style={{ color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}>
                            {b.condition || ''}
                            {b.leads_to_step_id ? <> → {b.leads_to_step_id}</> : null}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    {d.inferred ? <> <FlagPill flag="INFERRED_CONFIRM" /></> : null}
                  </li>
                ))}
              </ul>
            </>
          ) : null}

          <AIEmployeeBlock workflow={workflow} artifactId={artifactId} callMode={callMode} />

          {linkedApps.length > 0 ? (
            <>
              <div className="lab">Apps used by this workflow</div>
              {linkedApps.map((app) => (
                <AppBlock key={app.app_id} app={app} artifactId={artifactId} callMode={callMode} />
              ))}
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
