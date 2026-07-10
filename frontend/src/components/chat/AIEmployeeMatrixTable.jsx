import React, { useState, useMemo, useEffect } from 'react';
import { ChevronDown, ChevronUp, Eye, Edit3, Download, CheckCircle } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '../../lib/apiConfig';
import { getPdfPalette } from '../../lib/pdfPalette';
import BpmnViewer from './BpmnViewer';
import BpmnModeler from './BpmnModeler';
import AIDesignDocumentViewer from './AIDesignDocumentViewer';

function BpmnSection({ bpmnId, status }) {
  const [showViewer, setShowViewer] = useState(false);
  const [showModeler, setShowModeler] = useState(false);

  if (status === 'generating') {
    return (
      <div className="mt-3 p-3 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface text-xs text-aegisaxiom-text-secondary animate-pulse">
        Generating BPMN diagram...
      </div>
    );
  }

  if (status === 'failed') {
    return (
      <div className="mt-3 p-3 rounded-lg border border-red-500/30 bg-red-500/5 text-xs text-red-600 dark:text-red-400">
        Diagram generation failed.
      </div>
    );
  }

  return (
    <div className="mt-3">
      <div className="flex items-center gap-2 mb-2">
        <p className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
          Process Diagram
        </p>
        <button
          onClick={() => setShowViewer((v) => !v)}
          className="flex items-center gap-1 text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 transition-colors"
        >
          <Eye size={11} /> {showViewer ? 'Hide' : 'View'}
        </button>
        <button
          onClick={() => setShowModeler(true)}
          className="flex items-center gap-1 text-xs text-aegisaxiom-text-secondary hover:text-violet-700 dark:text-violet-300 transition-colors"
        >
          <Edit3 size={11} /> Edit
        </button>
      </div>
      {showViewer && <BpmnViewer bpmnId={bpmnId} height={350} />}
      {showModeler && <BpmnModeler bpmnId={bpmnId} onClose={() => setShowModeler(false)} />}
    </div>
  );
}

const DATA_STATE_LABELS = {
  blind: { label: 'Blind', cls: 'text-red-600 dark:text-red-400 bg-red-400/10 border-red-400/30' },
  siloed: {
    label: 'Siloed',
    cls: 'text-yellow-600 dark:text-yellow-400 bg-yellow-400/10 border-yellow-400/30',
  },
  visible: {
    label: 'Visible',
    cls: 'text-emerald-600 dark:text-emerald-400 bg-emerald-400/10 border-emerald-400/30',
  },
};

const MMI_FIELDS = [
  { key: 'tss', label: 'TSS', inverse: false, tooltip: 'Task Suitability (higher = better)' },
  { key: 'ie', label: 'IE', inverse: true, tooltip: 'Integration Effort (lower = better)' },
  { key: 'time', label: 'Time', inverse: false, tooltip: 'Time Savings (higher = better)' },
  { key: 'cost', label: 'Cost', inverse: false, tooltip: 'Cost Reduction (higher = better)' },
  {
    key: 'quality',
    label: 'Quality',
    inverse: false,
    tooltip: 'Quality Improvement (higher = better)',
  },
  {
    key: 'satisfaction',
    label: 'Satisfaction',
    inverse: false,
    tooltip: 'Satisfaction Gain (higher = better)',
  },
];

function mmiCellClass(value, inverse = false) {
  if (value === null || value === undefined)
    return 'text-aegisaxiom-text-muted bg-aegisaxiom-accent-muted border-aegisaxiom-border';
  if (inverse) {
    if (value <= 2)
      return 'text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-500/10 border-emerald-500/20';
    if (value <= 3)
      return 'text-yellow-700 dark:text-yellow-300 bg-yellow-500/10 border-yellow-500/20';
    return 'text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-500/10 border-red-500/20';
  }
  if (value >= 4)
    return 'text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-500/10 border-emerald-500/20';
  if (value >= 2)
    return 'text-yellow-700 dark:text-yellow-300 bg-yellow-500/10 border-yellow-500/20';
  return 'text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-500/10 border-red-500/20';
}

function scoreClass(score) {
  if (score === null || score === undefined) return 'text-aegisaxiom-text-secondary';
  if (score >= 20) return 'text-emerald-600 dark:text-emerald-400 font-bold';
  if (score >= 10) return 'text-yellow-600 dark:text-yellow-400 font-semibold';
  return 'text-red-600 dark:text-red-400 font-semibold';
}

function WorkflowCard({ workflow, approved, onScoresChange, linkedAppNames }) {
  const [expanded, setExpanded] = useState(false);
  const { mmi_hypothesis = {}, data_state } = workflow;
  const dataStateMeta = DATA_STATE_LABELS[data_state] || {
    label: data_state || '—',
    cls: 'text-aegisaxiom-text-secondary bg-aegisaxiom-accent-muted border-aegisaxiom-border-hover/30',
  };

  const [scores, setScores] = useState(() => {
    const initial = {};
    MMI_FIELDS.forEach(({ key }) => {
      initial[key] = mmi_hypothesis[key] ?? null;
    });
    return initial;
  });

  const computedScore = useMemo(() => {
    const { tss, ie, time, cost, quality, satisfaction } = scores;
    if (!tss || !ie || ie === 0) return null;
    return Math.round((tss / ie) * (time + cost + quality + satisfaction) * 10) / 10;
  }, [scores]);

  useEffect(() => {
    onScoresChange?.(workflow.workflow_id, scores, computedScore);
  }, [scores, computedScore]);

  return (
    <div className="rounded-xl border border-aegisaxiom-border bg-aegisaxiom-elevated overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-start gap-4 px-5 py-4 text-left hover:bg-aegisaxiom-accent-muted transition-colors"
      >
        <div className="flex-1 min-w-0 space-y-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-aegisaxiom-text leading-snug">
              {workflow.workflow_name}
            </span>
            <span className={`text-xs px-2 py-0.5 rounded-full border ${dataStateMeta.cls}`}>
              {dataStateMeta.label}
            </span>
            {workflow.manual_hours_per_week != null && (
              <span className="text-xs text-aegisaxiom-text-muted">
                {workflow.manual_hours_per_week}h / week
              </span>
            )}
          </div>
          {workflow.description && (
            <p className="text-xs text-aegisaxiom-text-secondary leading-relaxed line-clamp-2">
              {workflow.description}
            </p>
          )}
          {linkedAppNames?.length > 0 && (
            <div className="flex items-center gap-1 flex-wrap pt-0.5">
              <span className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted">Linked App Design{linkedAppNames.length > 1 ? 's' : ''}:</span>
              {linkedAppNames.map((name, i) => (
                <span
                  key={i}
                  className="text-xs px-2 py-0.5 rounded-md bg-violet-500/10 text-violet-600 dark:text-violet-300 border border-violet-500/30"
                >
                  {name}
                </span>
              ))}
            </div>
          )}
          {workflow.source_apps?.length > 0 && (
            <div className="flex items-center gap-1 flex-wrap pt-0.5">
              {workflow.source_apps.map((app, i) => (
                <span
                  key={i}
                  className="text-xs px-2 py-0.5 rounded-md bg-aegisaxiom-elevated border border-aegisaxiom-border-hover/40 text-aegisaxiom-text-secondary"
                >
                  {app}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="shrink-0 flex items-center gap-3">
          <span
            className={`text-lg tabular-nums ${scoreClass(computedScore ?? mmi_hypothesis.score)}`}
          >
            {computedScore ?? mmi_hypothesis.score ?? '—'}
          </span>
          <span className="text-xs text-aegisaxiom-text-muted">MMI</span>
          {expanded ? (
            <ChevronUp size={15} className="text-aegisaxiom-text-secondary" />
          ) : (
            <ChevronDown size={15} className="text-aegisaxiom-text-secondary" />
          )}
        </div>
      </button>

      {expanded && (
        <div className="border-t border-aegisaxiom-border px-5 py-4 space-y-4">
          {/* MMI Score Breakdown */}
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted mb-2">
              MMI Score Breakdown
            </p>
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
              {MMI_FIELDS.map(({ key, label, inverse, tooltip }) => {
                const val = scores[key];
                return (
                  <div
                    key={key}
                    className={`flex flex-col items-center gap-1 px-2 py-2 rounded-lg border text-center ${mmiCellClass(val, inverse)}`}
                  >
                    <span className="text-xs text-aegisaxiom-text-secondary" title={tooltip}>
                      {label}
                    </span>
                    <input
                      type="number"
                      min={1}
                      max={5}
                      value={val ?? ''}
                      onChange={(e) => {
                        const raw = e.target.value;
                        if (raw === '') {
                          setScores((prev) => ({ ...prev, [key]: null }));
                          return;
                        }
                        const v = parseInt(raw);
                        if (!isNaN(v) && v >= 1 && v <= 5)
                          setScores((prev) => ({ ...prev, [key]: v }));
                      }}
                      disabled={approved}
                      style={{
                        width: '2.5rem',
                        background: 'transparent',
                        textAlign: 'center',
                        border: 'none',
                        borderBottom: approved ? 'none' : '1px dashed rgba(148,163,184,0.5)',
                        outline: 'none',
                        cursor: approved ? 'default' : 'text',
                      }}
                      className="text-sm font-semibold tabular-nums [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                    />
                  </div>
                );
              })}
            </div>
            {mmi_hypothesis.needs_client_input && (
              <p className="mt-2 text-xs text-yellow-600 dark:text-yellow-400 italic">
                Needs client input to finalize scoring.
              </p>
            )}
          </div>

          {/* Integration Points */}
          {workflow.integration_points?.length > 0 && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted mb-2">
                Integration Points
              </p>
              <div className="space-y-1.5">
                {workflow.integration_points.map((ip, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className="text-aegisaxiom-text font-medium">{ip.system_name}</span>
                    <span className="px-1.5 py-0.5 rounded bg-aegisaxiom-surface text-aegisaxiom-text-secondary">
                      {ip.integration_type?.replace('_', ' ')}
                    </span>
                    {ip.data_exchanged && (
                      <span className="text-aegisaxiom-text-muted">— {ip.data_exchanged}</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* SLA Requirements */}
          {workflow.sla_requirements &&
            (workflow.sla_requirements.expected_duration ||
              workflow.sla_requirements.target_duration) && (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted mb-2">
                  SLA / Timing
                </p>
                <div className="flex items-center gap-4 text-xs">
                  {workflow.sla_requirements.expected_duration && (
                    <span className="text-aegisaxiom-text-secondary">
                      Current:{' '}
                      <span className="text-aegisaxiom-text font-medium">
                        {workflow.sla_requirements.expected_duration}
                      </span>
                    </span>
                  )}
                  {workflow.sla_requirements.target_duration && (
                    <span className="text-aegisaxiom-text-secondary">
                      Target:{' '}
                      <span className="text-emerald-600 dark:text-emerald-400 font-medium">
                        {workflow.sla_requirements.target_duration}
                      </span>
                    </span>
                  )}
                  {workflow.sla_requirements.current_bottleneck && (
                    <span className="text-yellow-600 dark:text-yellow-400">
                      Bottleneck: {workflow.sla_requirements.current_bottleneck}
                    </span>
                  )}
                </div>
              </div>
            )}

          {/* BPMN Diagram */}
          {workflow.bpmn_id && (
            <BpmnSection bpmnId={workflow.bpmn_id} status={workflow.bpmn_status} />
          )}
        </div>
      )}
    </div>
  );
}

// Escape user/LLM-generated text before interpolating into PDF HTML strings.
// Without this, characters like `<` in summaries (e.g. "< 20%") cause the
// browser parser to consume following content, dropping it from the rendered PDF.
function escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function captureBpmnSvg(bpmnId) {
  let tmpContainer = null;
  let viewer = null;
  try {
    const res = await api.get(`${API_URL}/api/bpmn/${bpmnId}`);
    if (!res.ok) return null;
    const data = await res.json();
    const xml = data.bpmn_xml || data.edited_xml;
    if (!xml) return null;

    tmpContainer = document.createElement('div');
    tmpContainer.style.cssText =
      'position:fixed;left:-9999px;top:0;width:800px;height:500px;background:#ffffff;';
    document.body.appendChild(tmpContainer);

    const { default: BpmnViewer } = await import('bpmn-js/lib/Viewer');
    viewer = new BpmnViewer({ container: tmpContainer });

    await viewer.importXML(xml);
    const canvas = viewer.get('canvas');
    canvas.zoom('fit-viewport');
    const { svg } = await viewer.saveSVG();
    return svg.replace(/^<\?xml[^>]*\?>\s*/, '');
  } catch {
    return null;
  } finally {
    if (viewer) viewer.destroy();
    if (tmpContainer) tmpContainer.remove();
  }
}

async function exportMatrixPdf({
  matrix,
  gaps,
  callAnalysis,
  nextCallAgenda,
  clientName,
  datePart,
  stakeholderStrategies,
  workflowFeedback,
  call3Approach,
  designDocs,
}) {
  const html2canvas = (await import('html2canvas')).default;
  const { jsPDF } = await import('jspdf');
  const p = getPdfPalette();

  const container = document.createElement('div');
  container.style.cssText =
    `position:fixed;left:-9999px;top:0;width:900px;background:${p.bg};color:${p.text};font-family:sans-serif;padding:36px;box-sizing:border-box;`;

  const section = (title) =>
    `<p style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:${p.textMuted};margin:24px 0 10px;">${title}</p>`;

  const lines = [];

  lines.push(`<h1 style="font-size:18px;font-weight:700;margin:0 0 6px;color:${p.text};">AI Discovery Report</h1>`);
  lines.push(`<p style="font-size:11px;color:${p.textMuted};margin:0 0 24px;">${escapeHtml(clientName.replace(/_/g, ' '))} &mdash; ${escapeHtml(datePart)}</p>`);

  if (callAnalysis?.summary) {
    lines.push(section('Call Summary'));
    lines.push(`<p style="font-size:12px;line-height:1.7;color:${p.textSecondary};margin:0;">${escapeHtml(callAnalysis.summary)}</p>`);
  }

  if (stakeholderStrategies?.length) {
    lines.push(section('Stakeholder Strategies'));
    stakeholderStrategies.forEach((ss) => {
      lines.push(`<p style="font-size:12px;font-weight:600;color:${p.text};margin:14px 0 4px;">${escapeHtml(ss.stakeholder_name)} <span style="font-weight:400;color:${p.textMuted};">(${escapeHtml(ss.role)})</span></p>`);
      if (ss.pain_summary) lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:4px 0;">Pain: ${escapeHtml(ss.pain_summary)}</p>`);
      if (ss.selling_strategy) lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:4px 0;">Strategy: ${escapeHtml(ss.selling_strategy)}</p>`);
    });
  }

  lines.push(section('Workflows'));
  for (const wf of matrix) {
    const mmi = wf.mmi_hypothesis || {};
    lines.push(`<div style="border:1px solid ${p.border};border-radius:8px;padding:16px;margin-bottom:14px;">`);
    lines.push(`<p style="font-size:13px;font-weight:600;color:${p.text};margin:0 0 6px;">${escapeHtml(wf.workflow_name)}</p>`);
    if (wf.description) lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textMuted};margin:0 0 10px;">${escapeHtml(wf.description)}</p>`);
    lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textMuted};margin:6px 0;">MMI Score: <span style="color:${p.accent};font-weight:600;">${mmi.score ?? 'pending'}</span> &nbsp;|&nbsp; TSS=${mmi.tss ?? '?'} IE=${mmi.ie ?? '?'} Time=${mmi.time ?? '?'} Cost=${mmi.cost ?? '?'} Quality=${mmi.quality ?? '?'} Sat=${mmi.satisfaction ?? '?'}</p>`);
    if (wf.manual_hours_per_week != null) lines.push(`<p style="font-size:11px;color:${p.textMuted};margin:4px 0;">Manual Hours/Week: ${escapeHtml(wf.manual_hours_per_week)}</p>`);
    if (wf.source_apps?.length) lines.push(`<p style="font-size:11px;color:${p.textMuted};margin:4px 0;">Source Apps: ${escapeHtml(wf.source_apps.join(', '))}</p>`);
    if (wf.bpmn_id) {
      const svg = await captureBpmnSvg(wf.bpmn_id);
      if (svg) {
        lines.push(`<p style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:${p.textMuted};margin:14px 0 6px;">Process Diagram</p>`);
        lines.push(`<div style="border:1px solid ${p.border};border-radius:6px;padding:8px;background:#ffffff;overflow:hidden;">`);
        lines.push(svg.replace('<svg', '<svg style="width:100%;height:auto;display:block;"'));
        lines.push(`</div>`);
      }
    }
    lines.push(`</div>`);
  }

  if (workflowFeedback?.length) {
    lines.push(section('Workflow Completeness Assessment'));
    workflowFeedback.forEach((wf) => {
      lines.push(`<p style="font-size:12px;font-weight:600;color:${p.text};margin:14px 0 4px;">${escapeHtml(wf.workflow_name)}</p>`);
      if (wf.completeness_assessment) lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:4px 0;">${escapeHtml(wf.completeness_assessment)}</p>`);
    });
  }

  if (call3Approach) {
    lines.push(section('Call 3 Approach Summary'));
    lines.push(`<p style="font-size:12px;line-height:1.7;color:${p.textSecondary};margin:0;">${escapeHtml(call3Approach)}</p>`);
  }

  if (gaps?.length) {
    lines.push(section('Information Gaps'));
    gaps.forEach((g) => {
      lines.push(`<p style="font-size:11px;color:${p.warning};font-weight:600;margin:10px 0 2px;">${escapeHtml(g.topic)}</p>`);
      if (g.what_we_know) lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textMuted};margin:2px 0;">Know: ${escapeHtml(g.what_we_know)}</p>`);
      if (g.what_we_need_to_ask) lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textMuted};margin:2px 0;">Need to ask: ${escapeHtml(g.what_we_need_to_ask)}</p>`);
    });
  }

  if (nextCallAgenda?.length) {
    lines.push(section('Next Call Agenda'));
    nextCallAgenda.forEach((item, i) => {
      lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:4px 0;">${i + 1}. ${escapeHtml(item)}</p>`);
    });
  }

  if (designDocs?.length) {
    lines.push(section('AI Design Documents'));
    for (const doc of designDocs) {
      lines.push(`<div style="border:1px solid ${p.border};border-radius:8px;padding:16px;margin-bottom:16px;">`);
      lines.push(`<p style="font-size:13px;font-weight:600;color:${p.accent};margin:0 0 8px;">${escapeHtml(doc.workflow_name)}</p>`);
      if (doc.executive_summary) lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:0 0 10px;">${escapeHtml(doc.executive_summary)}</p>`);
      if (doc.current_state?.process_description) {
        lines.push(`<p style="font-size:11px;font-weight:600;color:${p.danger};margin:10px 0 4px;">Current State</p>`);
        lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textMuted};margin:0;">${escapeHtml(doc.current_state.process_description)}</p>`);
      }
      if (doc.proposed_ai_solution?.solution_description) {
        lines.push(`<p style="font-size:11px;font-weight:600;color:${p.success};margin:10px 0 4px;">Proposed AI Solution</p>`);
        lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textMuted};margin:0;">${escapeHtml(doc.proposed_ai_solution.solution_description)}</p>`);
      }
      if (doc.bpmn_process_narrative) {
        lines.push(`<p style="font-size:11px;font-weight:600;color:${p.textMuted};margin:10px 0 4px;">Process Narrative</p>`);
        lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textMuted};white-space:pre-wrap;margin:0;">${escapeHtml(doc.bpmn_process_narrative)}</p>`);
      }
      if (doc.bpmn_id) {
        const svg = await captureBpmnSvg(doc.bpmn_id);
        if (svg) {
          lines.push(`<p style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:${p.textMuted};margin:14px 0 6px;">Process Diagram</p>`);
          lines.push(`<div style="border:1px solid ${p.border};border-radius:6px;padding:8px;background:#ffffff;overflow:hidden;">`);
          lines.push(svg.replace('<svg', '<svg style="width:100%;height:auto;display:block;"'));
          lines.push(`</div>`);
        }
      }
      const complexity = doc.implementation_considerations?.estimated_complexity;
      if (complexity) lines.push(`<p style="font-size:10px;color:${p.textMuted};margin:10px 0 0;">Implementation Complexity: ${escapeHtml(complexity)}</p>`);
      lines.push(`</div>`);
    }
  }

  container.innerHTML = lines.join('');
  document.body.appendChild(container);

  try {
    const canvas = await html2canvas(container, {
      backgroundColor: p.bg,
      scale: 1.5,
      useCORS: true,
      logging: false,
      width: 900,
    });

    const margin = 10;
    const pageWidth = 210;
    const pageHeight = 297;
    const contentWidth = pageWidth - margin * 2;
    const contentHeight = pageHeight - margin * 2;
    const imgHeight = (canvas.height * contentWidth) / canvas.width;

    const pdf = new jsPDF('p', 'mm', 'a4');
    let yOffset = 0;

    while (yOffset < imgHeight) {
      if (yOffset > 0) pdf.addPage();

      const sourceY = (yOffset / imgHeight) * canvas.height;
      const sourceH = Math.min((contentHeight / imgHeight) * canvas.height, canvas.height - sourceY);
      const sliceHeight = (sourceH / canvas.height) * imgHeight;

      const pageCanvas = document.createElement('canvas');
      pageCanvas.width = canvas.width;
      pageCanvas.height = sourceH;
      const ctx = pageCanvas.getContext('2d');
      ctx.drawImage(canvas, 0, sourceY, canvas.width, sourceH, 0, 0, canvas.width, sourceH);

      const pageImgData = pageCanvas.toDataURL('image/png');
      pdf.addImage(pageImgData, 'PNG', margin, margin, contentWidth, sliceHeight);

      yOffset += contentHeight;
    }

    pdf.save(`${clientName}-discovery_report-${datePart}.pdf`);
  } finally {
    document.body.removeChild(container);
  }
}

function ExportDropdown({
  matrix,
  gaps,
  callAnalysis,
  nextCallAgenda,
  sessionId,
  designDocs,
  clientName,
  datePart,
  stakeholderStrategies,
  workflowFeedback,
  call3Approach,
}) {
  const [show, setShow] = useState(false);
  const [exportingGdoc, setExportingGdoc] = useState(false);
  const [exportingPdf, setExportingPdf] = useState(false);

  return (
    <div className="relative">
      <button
        onClick={() => setShow((s) => !s)}
        className="flex items-center gap-1.5 text-xs text-aegisaxiom-text-secondary hover:text-violet-700 dark:text-violet-300 transition-colors"
        title="Export options"
      >
        <Download size={13} /> Export
      </button>
      {show && (
        <>
          {/* backdrop to close on outside click */}
          <div className="fixed inset-0 z-10" onClick={() => setShow(false)} />
          <div className="absolute right-0 top-full mt-1 w-52 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-elevated shadow-xl z-20 py-1">
            <button
              disabled={exportingGdoc}
              onClick={async () => {
                setExportingGdoc(true);
                setShow(false);
                try {
                  const res = await api.post(
                    `${API_URL}/api/vega-sessions/${sessionId}/export/google-doc`,
                    {},
                  );
                  if (res.ok) {
                    const data = await res.json();
                    if (data.doc_url) window.open(data.doc_url, '_blank', 'noopener,noreferrer');
                  } else {
                    const data = await res.json();
                    toast.error(data?.detail || 'Google Doc export failed');
                  }
                } catch {
                  toast.error('Connection error. Please try again.');
                } finally {
                  setExportingGdoc(false);
                }
              }}
              className="w-full text-left px-3 py-2 text-xs text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated transition-colors disabled:opacity-50"
              aria-label="Export to Google Doc"
            >
              {exportingGdoc ? (
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-3 border-2 border-aegisaxiom-border-hover/30 border-t-violet-400 rounded-full animate-spin inline-block" />
                  Exporting...
                </span>
              ) : (
                'Google Doc'
              )}
            </button>
            <button
              disabled={exportingPdf}
              onClick={async () => {
                setExportingPdf(true);
                setShow(false);
                try {
                  await exportMatrixPdf({
                    matrix,
                    gaps,
                    callAnalysis,
                    nextCallAgenda,
                    clientName,
                    datePart,
                    stakeholderStrategies,
                    workflowFeedback,
                    call3Approach,
                    designDocs,
                  });
                } catch (err) {
                  console.error('PDF export failed:', err);
                  toast.error('PDF generation failed. Please try again.');
                } finally {
                  setExportingPdf(false);
                }
              }}
              className="w-full text-left px-3 py-2 text-xs text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated transition-colors disabled:opacity-50"
              aria-label="Export comprehensive PDF report"
            >
              {exportingPdf ? (
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-3 border-2 border-aegisaxiom-border-hover/30 border-t-violet-400 rounded-full animate-spin inline-block" />
                  Generating PDF...
                </span>
              ) : (
                'PDF Report'
              )}
            </button>
            <div className="border-t border-aegisaxiom-border my-1" />
            <button
              onClick={() => {
                exportMatrix(
                  matrix,
                  gaps,
                  callAnalysis,
                  nextCallAgenda,
                  clientName,
                  datePart,
                  stakeholderStrategies,
                  workflowFeedback,
                  call3Approach,
                );
                setShow(false);
              }}
              className="w-full text-left px-3 py-2 text-xs text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated transition-colors"
            >
              Matrix (Markdown)
            </button>
            <button
              onClick={async () => {
                setShow(false);
                try {
                  const blob = await api.download(
                    `${API_URL}/api/vega-sessions/${sessionId}/export/mmi?format=csv`,
                  );
                  if (blob) {
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `${clientName}-mmi_matrix-${datePart}.csv`;
                    a.click();
                    URL.revokeObjectURL(url);
                  } else {
                    toast.error('Download failed — please try again');
                  }
                } catch {
                  toast.error('Download failed — please try again');
                }
              }}
              className="w-full text-left px-3 py-2 text-xs text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated transition-colors"
            >
              Matrix (CSV)
            </button>
            {designDocs.length > 0 && (
              <button
                onClick={async () => {
                  setShow(false);
                  try {
                    const blob = await api.download(
                      `${API_URL}/api/vega-sessions/${sessionId}/export/design-docs?format=markdown`,
                    );
                    if (blob) {
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      a.download = `${clientName}-design_docs-${datePart}.md`;
                      a.click();
                      URL.revokeObjectURL(url);
                    } else {
                      toast.error('Download failed — please try again');
                    }
                  } catch {
                    toast.error('Download failed — please try again');
                  }
                }}
                className="w-full text-left px-3 py-2 text-xs text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated transition-colors"
              >
                Design Docs (Markdown)
              </button>
            )}
            <div className="border-t border-aegisaxiom-border mt-1 pt-1">
              <button
                onClick={async () => {
                  setShow(false);
                  try {
                    const blob = await api.download(
                      `${API_URL}/api/vega-sessions/${sessionId}/export/package`,
                    );
                    if (blob) {
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      a.download = `${clientName}-discovery_package-${datePart}.zip`;
                      a.click();
                      URL.revokeObjectURL(url);
                    } else {
                      toast.error('Download failed — please try again');
                    }
                  } catch {
                    toast.error('Download failed — please try again');
                  }
                }}
                className="w-full text-left px-3 py-2 text-xs text-violet-700 dark:text-violet-300 hover:bg-aegisaxiom-elevated transition-colors font-medium"
              >
                Full Package (ZIP)
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function exportMatrix(
  matrix,
  gaps,
  callAnalysis,
  nextCallAgenda,
  clientName,
  datePart,
  stakeholderStrategies,
  workflowFeedback,
  call3Approach,
) {
  const lines = ['# AI Employee Matrix\n'];

  // Call Analysis
  if (callAnalysis) {
    if (callAnalysis.summary) lines.push(`## Call Summary\n${callAnalysis.summary}\n`);
    if (callAnalysis.what_went_well?.length)
      lines.push(
        `### What Went Well\n${callAnalysis.what_went_well.map((i) => `- ${i}`).join('\n')}\n`,
      );
    if (callAnalysis.areas_for_improvement?.length)
      lines.push(
        `### Areas for Improvement\n${callAnalysis.areas_for_improvement.map((i) => `- ${i}`).join('\n')}\n`,
      );
    if (callAnalysis.missed_items?.length)
      lines.push(
        `### Missed Items\n${callAnalysis.missed_items.map((i) => `- ${i}`).join('\n')}\n`,
      );
  }

  // Stakeholder Strategies
  if (stakeholderStrategies?.length) {
    lines.push(`## Stakeholder Selling Strategies\n`);
    for (const ss of stakeholderStrategies) {
      lines.push(
        `### ${ss.stakeholder_name} (${ss.role})${ss.buyer_type ? ` — ${ss.buyer_type}` : ''}`,
      );
      if (ss.pain_summary) lines.push(`**Pain:** ${ss.pain_summary}`);
      if (ss.selling_strategy) lines.push(`**Strategy:** ${ss.selling_strategy}`);
      if (ss.hook_question) lines.push(`> Hook: "${ss.hook_question}"`);
      if (ss.tactical_advice) lines.push(`**Tactical:** ${ss.tactical_advice}\n`);
    }
  }

  // Workflows with full detail
  lines.push(`## Workflows\n`);
  for (const wf of matrix) {
    lines.push(`### ${wf.workflow_name} (${wf.data_state || '?'})\n${wf.description || ''}\n`);
    if (wf.source_apps?.length) lines.push(`**Source Apps:** ${wf.source_apps.join(', ')}`);
    if (wf.manual_hours_per_week) lines.push(`**Manual Hours/Week:** ${wf.manual_hours_per_week}`);
    const mmi = wf.mmi_hypothesis || {};
    lines.push(
      `**MMI Scores:** TSS=${mmi.tss ?? '?'} IE=${mmi.ie ?? '?'} Time=${mmi.time ?? '?'} Cost=${mmi.cost ?? '?'} Quality=${mmi.quality ?? '?'} Satisfaction=${mmi.satisfaction ?? '?'} | **Score=${mmi.score ?? 'pending'}**`,
    );
    if (mmi.needs_client_input?.length)
      lines.push(`**Needs Client Input:** ${mmi.needs_client_input.join(', ')}`);
    // Integration points
    if (wf.integration_points?.length) {
      lines.push(`\n**Integration Points:**`);
      for (const ip of wf.integration_points) {
        lines.push(
          `- ${ip.system_name} (${ip.integration_type?.replace('_', ' ') || '?'})${ip.data_exchanged ? ` — ${ip.data_exchanged}` : ''}`,
        );
      }
    }
    // SLA
    if (wf.sla_requirements) {
      const sla = wf.sla_requirements;
      const parts = [];
      if (sla.expected_duration) parts.push(`Current: ${sla.expected_duration}`);
      if (sla.target_duration) parts.push(`Target: ${sla.target_duration}`);
      if (sla.current_bottleneck) parts.push(`Bottleneck: ${sla.current_bottleneck}`);
      if (parts.length) lines.push(`**SLA:** ${parts.join(' | ')}`);
    }
    lines.push('');
  }

  // Workflow Completeness Assessment
  if (workflowFeedback?.length) {
    lines.push(`## Workflow Completeness Assessment\n`);
    for (const wf of workflowFeedback) {
      lines.push(`### ${wf.workflow_name}`);
      if (wf.completeness_assessment) lines.push(wf.completeness_assessment);
      if (wf.call_3_questions?.length) {
        lines.push(`**Questions for Next Call:**`);
        wf.call_3_questions.forEach((q) => lines.push(`- ${q}`));
      }
      if (wf.pro_tip) lines.push(`> **Pro Tip:** ${wf.pro_tip}`);
      lines.push('');
    }
  }

  // Call 3 Approach
  if (call3Approach) {
    lines.push(`## Call 3 Approach Summary\n${call3Approach}\n`);
  }

  // Gaps
  if (gaps.length) {
    lines.push(`## Information Gaps\n`);
    for (const g of gaps)
      lines.push(
        `- **${g.topic}**: Know: ${g.what_we_know || '?'} | Need: ${g.what_we_need_to_ask || '?'}`,
      );
    lines.push('');
  }

  // Next Call Agenda
  if (nextCallAgenda.length) {
    lines.push(
      `## Next Call Agenda\n${nextCallAgenda.map((a, i) => `${i + 1}. ${a}`).join('\n')}\n`,
    );
  }

  const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${clientName || 'client'}-ai_employee_matrix-${datePart || new Date().toISOString().slice(0, 10)}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function AIEmployeeMatrixTable({ artifact }) {
  const { artifact_id, payload, status } = artifact;
  const { matrix = [], gaps = [], next_call_agenda = [], call_analysis } = payload;
  const ai_design_documents = payload.ai_design_documents || [];
  const apps = payload.emergent_app_design_documents || [];
  // Build workflow_id → list of app names that link to it (cross-link badge data)
  const workflowToApps = useMemo(() => {
    const m = new Map();
    for (const app of apps) {
      for (const wid of app.linked_workflow_ids || []) {
        if (!m.has(wid)) m.set(wid, []);
        m.get(wid).push(app.app_name || '(app)');
      }
    }
    return m;
  }, [apps]);
  const stakeholder_strategies = payload.stakeholder_strategies || [];
  const workflow_deep_dive_feedback = payload.workflow_deep_dive_feedback || [];
  const call_3_approach_summary = payload.call_3_approach_summary || '';
  const call_number = payload.call_number || 2;
  const sessionId = payload.session_id || payload.opportunity_id || `vega_${artifact.session_id}`;
  const clientName =
    (payload.client_name || '')
      .replace(/[^a-zA-Z0-9_-]/g, '_')
      .replace(/_+/g, '_')
      .replace(/^_|_$/g, '') || 'client';
  const datePart = new Date().toISOString().slice(0, 10);

  const isApproved = status === 'submitted' || status === 'graded';
  const [approved, setApproved] = useState(isApproved);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [editedMatrix, setEditedMatrix] = useState(() => [...matrix]);

  const enrichedDesignDocs = useMemo(() => {
    return ai_design_documents.map((doc) => {
      const matchedWf = editedMatrix.find((wf) => wf.workflow_id === doc.workflow_id);
      return matchedWf?.bpmn_id ? { ...doc, bpmn_id: matchedWf.bpmn_id } : doc;
    });
  }, [ai_design_documents, editedMatrix]);

  const handleApprove = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const approvePayload = {
        ...payload,
        matrix: editedMatrix,
        chat_session_id: artifact.session_id,
      };
      const res = await api.post(
        `${API_URL}/api/vega-sessions/${sessionId}/calls/${call_number}/approve`,
        approvePayload,
      );
      if (res.ok) {
        setApproved(true);
        window.dispatchEvent(new CustomEvent('artifact-submitted', { detail: { artifact_id } }));
      } else {
        const data = await res.json();
        setSubmitError(data?.detail || 'Failed to save.');
      }
    } catch {
      setSubmitError('Connection error.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="my-3 rounded-xl border border-violet-500/30 bg-aegisaxiom-elevated overflow-hidden min-w-[560px] w-[720px] max-w-full">
      <div className="px-5 py-4 border-b border-aegisaxiom-border flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-aegisaxiom-text">AI Employee Matrix</h3>
          <p className="text-xs text-aegisaxiom-text-muted mt-0.5">
            {matrix.length} workflow{matrix.length !== 1 ? 's' : ''} identified
          </p>
        </div>
        <div className="flex items-center gap-3">
          {approved && (
            <span className="flex items-center gap-1.5 text-xs text-emerald-600 dark:text-emerald-400 font-medium">
              <CheckCircle size={14} /> Approved
            </span>
          )}
          <button
            onClick={async () => {
              try {
                const blob = await api.download(
                  `${API_URL}/api/bpmn/export-all?session_id=${sessionId}`,
                );
                if (blob) {
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement('a');
                  a.href = url;
                  a.download = `${clientName}-bpmn_diagrams-${datePart}.zip`;
                  a.click();
                  URL.revokeObjectURL(url);
                }
              } catch {
                /* ignore */
              }
            }}
            className="flex items-center gap-1.5 text-xs text-aegisaxiom-text-secondary hover:text-violet-700 dark:text-violet-300 transition-colors"
            title="Download all BPMN diagrams as ZIP"
          >
            <Download size={13} /> BPMNs
          </button>
          <ExportDropdown
            matrix={editedMatrix}
            gaps={gaps}
            callAnalysis={call_analysis}
            nextCallAgenda={next_call_agenda}
            sessionId={sessionId}
            designDocs={enrichedDesignDocs}
            clientName={clientName}
            datePart={datePart}
            stakeholderStrategies={stakeholder_strategies}
            workflowFeedback={workflow_deep_dive_feedback}
            call3Approach={call_3_approach_summary}
          />
        </div>
      </div>

      {/* Call Analysis Section */}
      {call_analysis && (
        <div className="border-b border-aegisaxiom-border px-5 py-4 space-y-3">
          {call_analysis.summary && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted mb-1">
                Call Summary
              </p>
              <p className="text-sm text-aegisaxiom-text-secondary leading-relaxed">
                {call_analysis.summary}
              </p>
            </div>
          )}
          {call_analysis.what_went_well?.length > 0 && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-emerald-500 mb-1">
                What Went Well
              </p>
              <ul className="space-y-1">
                {call_analysis.what_went_well.map((item, i) => (
                  <li key={i} className="text-xs text-aegisaxiom-text-secondary flex items-start gap-2">
                    <span className="text-emerald-600 dark:text-emerald-400 mt-0.5">+</span>
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {call_analysis.areas_for_improvement?.length > 0 && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-yellow-500 mb-1">
                Areas for Improvement
              </p>
              <ul className="space-y-1">
                {call_analysis.areas_for_improvement.map((item, i) => (
                  <li key={i} className="text-xs text-aegisaxiom-text-secondary flex items-start gap-2">
                    <span className="text-yellow-600 dark:text-yellow-400 mt-0.5">~</span>
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {call_analysis.missed_items?.length > 0 && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-red-500 mb-1">
                Missed Items
              </p>
              <ul className="space-y-1">
                {call_analysis.missed_items.map((item, i) => (
                  <li key={i} className="text-xs text-aegisaxiom-text-secondary flex items-start gap-2">
                    <span className="text-red-600 dark:text-red-400 mt-0.5">-</span>
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Stakeholder Strategies */}
      {stakeholder_strategies.length > 0 && (
        <div className="border-b border-aegisaxiom-border px-5 py-4 space-y-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
            Stakeholder Strategies
          </p>
          {stakeholder_strategies.map((ss, idx) => (
            <div
              key={idx}
              className="rounded-lg border border-violet-500/20 bg-violet-500/5 p-3 space-y-1.5"
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-semibold text-aegisaxiom-text">{ss.stakeholder_name}</span>
                <span className="text-xs text-aegisaxiom-text-secondary">({ss.role})</span>
                {ss.buyer_type && (
                  <span className="text-xs px-2 py-0.5 rounded-full bg-violet-50 dark:bg-violet-500/20 border border-violet-500/30 text-violet-700 dark:text-violet-300">
                    {ss.buyer_type}
                  </span>
                )}
              </div>
              {ss.pain_summary && (
                <p className="text-xs text-aegisaxiom-text-secondary">Pain: {ss.pain_summary}</p>
              )}
              {ss.selling_strategy && (
                <p className="text-xs text-aegisaxiom-text-secondary">Strategy: {ss.selling_strategy}</p>
              )}
              {ss.hook_question && (
                <div className="pl-3 border-l-2 border-violet-500/40">
                  <p className="text-xs text-violet-700 dark:text-violet-300 italic">
                    Hook: "{ss.hook_question}"
                  </p>
                </div>
              )}
              {ss.tactical_advice && (
                <p className="text-xs text-aegisaxiom-text-secondary">Tactical: {ss.tactical_advice}</p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Workflow Deep-Dive Feedback */}
      {workflow_deep_dive_feedback.length > 0 && (
        <div className="border-b border-aegisaxiom-border px-5 py-4 space-y-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
            Workflow Completeness Assessment
          </p>
          {workflow_deep_dive_feedback.map((wf, idx) => (
            <div
              key={idx}
              className="rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface p-3 space-y-2"
            >
              <span className="text-sm font-medium text-aegisaxiom-text">{wf.workflow_name}</span>
              {wf.completeness_assessment && (
                <p className="text-xs text-aegisaxiom-text-secondary">{wf.completeness_assessment}</p>
              )}
              {wf.call_3_questions?.length > 0 && (
                <div>
                  <p className="text-xs text-yellow-600 dark:text-yellow-400 font-medium mb-1">
                    Questions for Call 3:
                  </p>
                  <ul className="space-y-0.5">
                    {wf.call_3_questions.map((q, i) => (
                      <li
                        key={i}
                        className="text-xs text-aegisaxiom-text-secondary pl-3 border-l border-yellow-500/30"
                      >
                        {q}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {wf.pro_tip && (
                <div className="rounded-md bg-violet-50 dark:bg-violet-500/10 border border-violet-500/20 px-2 py-1.5">
                  <p className="text-xs text-violet-700 dark:text-violet-300">
                    <span className="font-medium">Pro Tip:</span> {wf.pro_tip}
                  </p>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Call 3 Approach Summary */}
      {call_3_approach_summary && (
        <div className="border-b border-aegisaxiom-border px-5 py-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted mb-2">
            Call 3 Approach Summary
          </p>
          <div className="rounded-lg border border-violet-500/20 bg-violet-500/5 px-4 py-3">
            <p className="text-sm text-aegisaxiom-text leading-relaxed whitespace-pre-wrap">
              {call_3_approach_summary}
            </p>
          </div>
        </div>
      )}

      {/* MMI Legend */}
      <div className="px-5 pt-3 pb-1 flex items-center gap-4 text-[10px] text-aegisaxiom-text-muted">
        <span className="font-semibold uppercase tracking-wider">MMI Scale:</span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-emerald-500"></span> 4-5 Strong
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-yellow-500"></span> 2-3 Moderate
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-red-500"></span> 1 Weak
        </span>
      </div>

      {/* Workflow Cards */}
      <div className="px-5 py-4 space-y-2.5">
        {matrix.length === 0 ? (
          <p className="text-sm text-aegisaxiom-text-muted text-center py-4">
            No workflows recorded yet.
          </p>
        ) : (
          matrix.map((workflow) => (
            <WorkflowCard
              key={workflow.workflow_id}
              workflow={workflow}
              approved={approved}
              linkedAppNames={workflowToApps.get(workflow.workflow_id) || []}
              onScoresChange={(workflowId, newScores, newScore) => {
                setEditedMatrix((prev) =>
                  prev.map((wf) =>
                    wf.workflow_id === workflowId
                      ? {
                          ...wf,
                          mmi_hypothesis: { ...wf.mmi_hypothesis, ...newScores, score: newScore },
                        }
                      : wf,
                  ),
                );
              }}
            />
          ))
        )}
      </div>

      {/* Gaps Section */}
      {gaps.length > 0 && (
        <div className="border-t border-aegisaxiom-border px-5 py-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted mb-3">
            Information Gaps
          </p>
          <div className="space-y-2.5">
            {gaps.map((gap, idx) => (
              <div
                key={idx}
                className="rounded-lg border border-yellow-500/20 bg-yellow-500/5 px-4 py-3"
              >
                <p className="text-sm font-medium text-yellow-700 dark:text-yellow-300">
                  {gap.topic}
                </p>
                {gap.what_we_know && (
                  <p className="text-xs text-aegisaxiom-text-secondary mt-1">
                    <span className="text-aegisaxiom-text-muted">Know: </span>
                    {gap.what_we_know}
                  </p>
                )}
                {gap.what_we_need_to_ask && (
                  <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">
                    <span className="text-aegisaxiom-text-muted">Need to ask: </span>
                    {gap.what_we_need_to_ask}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Next Call Agenda */}
      {next_call_agenda.length > 0 && (
        <div className="border-t border-aegisaxiom-border px-5 py-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted mb-3">
            Next Call Agenda
          </p>
          <ul className="space-y-1.5">
            {next_call_agenda.map((item, idx) => (
              <li key={idx} className="flex items-start gap-2 text-sm text-aegisaxiom-text-secondary">
                <span className="text-violet-600 dark:text-violet-400 font-semibold shrink-0 mt-0.5">
                  {idx + 1}.
                </span>
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* AI Design Documents (Call 3) */}
      {ai_design_documents.length > 0 && (
        <div className="border-t border-aegisaxiom-border px-5 py-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted mb-3">
            AI Design Documents
          </p>
          <AIDesignDocumentViewer documents={enrichedDesignDocs} />
        </div>
      )}

      {/* Approve & Save */}
      {!approved && (
        <div className="px-5 py-4 border-t border-aegisaxiom-border flex items-center justify-between gap-3">
          {submitError ? (
            <p className="text-xs text-red-600 dark:text-red-400">{submitError}</p>
          ) : (
            <p className="text-xs text-aegisaxiom-text-muted">
              Review the matrix above before approving.
            </p>
          )}
          <button
            type="button"
            onClick={handleApprove}
            disabled={submitting}
            className="flex items-center gap-2 px-5 py-2 rounded-xl bg-gradient-to-r from-violet-600 to-fuchsia-600 text-aegisaxiom-text text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-all"
          >
            {submitting ? (
              <>
                <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <CheckCircle size={14} />
                Approve & Save
              </>
            )}
          </button>
        </div>
      )}
    </div>
  );
}
