import React, { useState } from 'react';
import { ChevronDown, ChevronUp, FileText, AlertTriangle, Users, Cpu, ArrowRight, Download, FileDown } from 'lucide-react';
import { api } from '../../lib/api';
import API_URL from '../../lib/apiConfig';
import { getPdfPalette } from '../../lib/pdfPalette';

const COMPLEXITY_COLORS = {
  low: 'text-emerald-600 dark:text-emerald-400 bg-emerald-400/10 border-emerald-400/30',
  medium: 'text-yellow-600 dark:text-yellow-400 bg-yellow-400/10 border-yellow-400/30',
  high: 'text-red-600 dark:text-red-400 bg-red-400/10 border-red-400/30',
};

function SectionTitle({ children }) {
  return <p className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted mb-2 mt-4 first:mt-0">{children}</p>;
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

async function exportDesignDocPdf(doc) {
  const html2canvas = (await import('html2canvas')).default;
  const { jsPDF } = await import('jspdf');
  const p = getPdfPalette();

  // Render a complete, fully-expanded view into a hidden off-screen container
  // so the card doesn't need to be open in the viewport during capture.
  const container = document.createElement('div');
  container.style.cssText =
    `position:fixed;left:-9999px;top:0;width:860px;background:${p.bg};color:${p.text};font-family:sans-serif;padding:36px;box-sizing:border-box;`;

  const complexity = doc.implementation_considerations?.estimated_complexity || 'medium';
  const complexityColor = complexity === 'low' ? p.success : complexity === 'high' ? p.danger : p.warning;

  const section = (title) =>
    `<p style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:${p.textMuted};margin:24px 0 10px;">${title}</p>`;

  const lines = [];

  lines.push(`<div style="display:flex;align-items:center;gap:12px;margin-bottom:22px;">`);
  lines.push(`<h1 style="font-size:18px;font-weight:700;margin:0;flex:1;color:${p.text};">${escapeHtml(doc.workflow_name)}</h1>`);
  lines.push(`<span style="font-size:10px;padding:4px 10px;border-radius:20px;border:1px solid ${complexityColor}55;color:${complexityColor};background:${complexityColor}1a;">${escapeHtml(complexity)}</span>`);
  lines.push(`</div>`);

  if (doc.executive_summary) {
    lines.push(section('Executive Summary'));
    lines.push(`<p style="font-size:12px;line-height:1.7;color:${p.textSecondary};margin:0;">${escapeHtml(doc.executive_summary)}</p>`);
  }

  if (doc.current_state?.process_description || doc.proposed_ai_solution?.solution_description) {
    lines.push(`<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:18px;">`);

    if (doc.current_state) {
      lines.push(`<div style="border:1px solid ${p.border};border-radius:8px;padding:16px;">`);
      lines.push(`<p style="font-size:10px;font-weight:700;text-transform:uppercase;color:${p.danger};margin:0 0 8px;">Current State</p>`);
      lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:0;">${escapeHtml(doc.current_state.process_description || '')}</p>`);
      if (doc.current_state.manual_effort) lines.push(`<p style="font-size:11px;color:${p.textMuted};margin:6px 0 0;">Manual Effort: ${escapeHtml(doc.current_state.manual_effort)}</p>`);
      if (doc.current_state.pain_points?.length) {
        lines.push(`<ul style="margin:8px 0 0;padding-left:0;list-style:none;">`);
        doc.current_state.pain_points.forEach((pp) =>
          lines.push(`<li style="font-size:11px;line-height:1.5;color:${p.textMuted};margin-bottom:4px;">- ${escapeHtml(pp)}</li>`),
        );
        lines.push(`</ul>`);
      }
      lines.push(`</div>`);
    }

    if (doc.proposed_ai_solution) {
      lines.push(`<div style="border:1px solid ${p.successPanelBorder};border-radius:8px;padding:16px;background:${p.successPanelBg};">`);
      lines.push(`<p style="font-size:10px;font-weight:700;text-transform:uppercase;color:${p.success};margin:0 0 8px;">Proposed AI Solution</p>`);
      lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:0;">${escapeHtml(doc.proposed_ai_solution.solution_description || '')}</p>`);
      if (doc.proposed_ai_solution.automation_percentage) lines.push(`<p style="font-size:11px;color:${p.success};margin:6px 0 0;">Automation: ${escapeHtml(doc.proposed_ai_solution.automation_percentage)}</p>`);
      if (doc.proposed_ai_solution.ai_role) lines.push(`<p style="font-size:11px;color:${p.textMuted};margin:6px 0 0;">AI Role: ${escapeHtml(doc.proposed_ai_solution.ai_role)}</p>`);
      if (doc.proposed_ai_solution.human_role) lines.push(`<p style="font-size:11px;color:${p.textMuted};margin:2px 0 0;">Human Role: ${escapeHtml(doc.proposed_ai_solution.human_role)}</p>`);
      lines.push(`</div>`);
    }

    lines.push(`</div>`);
  }

  if (doc.bpmn_process_narrative) {
    lines.push(section('Process Narrative'));
    lines.push(`<p style="font-size:11px;line-height:1.7;color:${p.textSecondary};white-space:pre-wrap;margin:0;">${escapeHtml(doc.bpmn_process_narrative)}</p>`);
  }

  if (doc.bpmn_id) {
    const svg = await captureBpmnSvg(doc.bpmn_id);
    if (svg) {
      lines.push(section('Process Diagram'));
      lines.push(`<div style="border:1px solid ${p.border};border-radius:6px;padding:8px;background:#ffffff;overflow:hidden;">`);
      lines.push(svg.replace('<svg', '<svg style="width:100%;height:auto;display:block;"'));
      lines.push(`</div>`);
    }
  }

  if (doc.integration_requirements?.length) {
    lines.push(section('Integration Requirements'));
    lines.push(`<table style="width:100%;border-collapse:collapse;font-size:11px;">`);
    lines.push(`<tr style="color:${p.textMuted};border-bottom:1px solid ${p.border};"><th style="text-align:left;padding:6px 8px 6px 0;">System</th><th style="text-align:left;padding:6px 8px 6px 0;">Type</th><th style="text-align:left;padding:6px 8px 6px 0;">Data</th><th style="text-align:left;padding:6px 0;">Complexity</th></tr>`);
    doc.integration_requirements.forEach((ir) => {
      lines.push(`<tr style="border-bottom:1px solid ${p.borderSubtle};"><td style="padding:6px 8px 6px 0;color:${p.text};">${escapeHtml(ir.system_name || '')}</td><td style="padding:6px 8px 6px 0;color:${p.textMuted};">${escapeHtml(ir.integration_type?.replace('_', ' ') || '')}</td><td style="padding:6px 8px 6px 0;color:${p.textMuted};">${escapeHtml(ir.data_exchanged || '')}</td><td style="padding:6px 0;color:${p.textMuted};">${escapeHtml(ir.complexity || '')}</td></tr>`);
    });
    lines.push(`</table>`);
  }

  if (doc.mmi_justification) {
    lines.push(section('MMI Score Justification'));
    [
      ['tss_rationale', 'TSS (Task Suitability)'],
      ['ie_rationale', 'IE (Integration Effort)'],
      ['time_rationale', 'Time Savings'],
      ['cost_rationale', 'Cost Reduction'],
      ['quality_rationale', 'Quality Improvement'],
      ['satisfaction_rationale', 'Satisfaction'],
    ].forEach(([key, label]) => {
      if (doc.mmi_justification[key]) {
        lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:6px 0;"><span style="color:${p.textMuted};font-weight:600;">${label}:</span> ${escapeHtml(doc.mmi_justification[key])}</p>`);
      }
    });
  }

  if (doc.implementation_considerations) {
    const ic = doc.implementation_considerations;
    lines.push(section('Implementation Considerations'));
    lines.push(`<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">`);
    const subsection = (title, color, items, prefix) => {
      if (!items?.length) return '';
      return `<div><p style="font-size:11px;font-weight:600;color:${color};margin:0 0 6px;">${title}</p><ul style="margin:0;padding-left:0;list-style:none;">${items.map((r) => `<li style="font-size:11px;line-height:1.5;color:${p.textMuted};margin-bottom:4px;">${prefix} ${escapeHtml(r)}</li>`).join('')}</ul></div>`;
    };
    lines.push(subsection('Risks', p.danger, ic.risks, '!'));
    lines.push(subsection('Dependencies', p.accent, ic.dependencies, '→'));
    lines.push(subsection('Assumptions', p.warning, ic.assumptions, '~'));
    lines.push(subsection('Open Questions', p.info, ic.open_questions, '?'));
    lines.push(`</div>`);
  }

  if (doc.human_touchpoints?.length) {
    lines.push(section('Human Touchpoints'));
    doc.human_touchpoints.forEach((ht) => {
      lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:6px 0;"><span style="font-weight:600;color:${p.text};">${escapeHtml(ht.touchpoint)}</span> — ${escapeHtml(ht.reason)}${ht.frequency ? ` (${escapeHtml(ht.frequency)})` : ''}</p>`);
    });
  }

  container.innerHTML = lines.join('');
  document.body.appendChild(container);

  try {
    const canvas = await html2canvas(container, {
      backgroundColor: p.bg,
      scale: 1.5,
      useCORS: true,
      logging: false,
      width: 860,
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

    const datePart = new Date().toISOString().slice(0, 10);
    const safeName = (doc.workflow_name || 'design_doc').replace(/[^a-zA-Z0-9]/g, '_');
    pdf.save(`${safeName}-design_doc-${datePart}.pdf`);
  } finally {
    document.body.removeChild(container);
  }
}

function DesignDocCard({ doc }) {
  const [expanded, setExpanded] = useState(false);
  const [exporting, setExporting] = useState(false);
  const complexity = doc.implementation_considerations?.estimated_complexity || 'medium';

  const handlePdfExport = async (e) => {
    e.stopPropagation();
    setExporting(true);
    try {
      await exportDesignDocPdf(doc);
    } catch (err) {
      console.error('PDF export failed:', err);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="rounded-xl border border-aegisaxiom-border bg-aegisaxiom-elevated overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-start gap-4 px-5 py-4 text-left hover:bg-aegisaxiom-accent-muted transition-colors"
      >
        <FileText size={16} className="text-violet-600 dark:text-violet-400 mt-0.5 shrink-0" />
        <div className="flex-1 min-w-0">
          <span className="text-sm font-semibold text-aegisaxiom-text">{doc.workflow_name}</span>
          {doc.executive_summary && (
            <p className="text-xs text-aegisaxiom-text-secondary mt-1 line-clamp-2">{doc.executive_summary}</p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className={`text-xs px-2 py-0.5 rounded-full border ${COMPLEXITY_COLORS[complexity]}`} title="Implementation Complexity">
            {complexity}
          </span>
          <button
            onClick={(e) => {
              e.stopPropagation();
              // Build markdown for this single design doc
              const lines = [`# ${doc.workflow_name}\n`];
              if (doc.executive_summary) lines.push(`## Executive Summary\n${doc.executive_summary}\n`);
              if (doc.current_state?.process_description) lines.push(`## Current State\n${doc.current_state.process_description}\n`);
              if (doc.proposed_ai_solution?.solution_description) lines.push(`## Proposed Solution\n${doc.proposed_ai_solution.solution_description}\n`);
              if (doc.bpmn_process_narrative) lines.push(`## Process Narrative\n${doc.bpmn_process_narrative}\n`);
              if (doc.integration_requirements?.length) {
                lines.push(`## Integration Requirements\n| System | Type | Data | Complexity |\n|--------|------|------|------------|`);
                doc.integration_requirements.forEach(ir => lines.push(`| ${ir.system_name || ''} | ${ir.integration_type || ''} | ${ir.data_exchanged || ''} | ${ir.complexity || ''} |`));
                lines.push('');
              }
              if (doc.mmi_justification) {
                lines.push(`## MMI Justification`);
                ['tss', 'ie', 'time', 'cost', 'quality', 'satisfaction'].forEach(dim => {
                  const key = `${dim}_rationale`;
                  if (doc.mmi_justification[key]) lines.push(`- **${dim.toUpperCase()}:** ${doc.mmi_justification[key]}`);
                });
                lines.push('');
              }
              if (doc.implementation_considerations) {
                lines.push(`## Implementation\n**Complexity:** ${doc.implementation_considerations.estimated_complexity || '?'}`);
                ['risks', 'dependencies', 'assumptions', 'open_questions'].forEach(k => {
                  if (doc.implementation_considerations[k]?.length) {
                    lines.push(`\n**${k.replace('_', ' ')}:**`);
                    doc.implementation_considerations[k].forEach(item => lines.push(`- ${item}`));
                  }
                });
                lines.push('');
              }
              if (doc.human_touchpoints?.length) {
                lines.push(`## Human Touchpoints`);
                doc.human_touchpoints.forEach(ht => lines.push(`- **${ht.touchpoint}:** ${ht.reason}${ht.frequency ? ` (${ht.frequency})` : ''}`));
              }
              const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              const datePart = new Date().toISOString().slice(0, 10);
              const wfName = (doc.workflow_name || 'design_doc').replace(/[^a-zA-Z0-9]/g, '_');
              a.download = `${wfName}-design_doc-${datePart}.md`;
              a.click();
              URL.revokeObjectURL(url);
            }}
            className="text-aegisaxiom-text-secondary hover:text-violet-700 dark:text-violet-300 transition-colors"
            title="Download this design document"
          >
            <Download size={14} />
          </button>
          <button
            onClick={handlePdfExport}
            disabled={exporting}
            className="text-aegisaxiom-text-secondary hover:text-violet-700 dark:text-violet-300 transition-colors disabled:opacity-50"
            title="Export as PDF"
          >
            {exporting ? (
              <div className="w-3.5 h-3.5 border-2 border-aegisaxiom-border-hover border-t-violet-400 rounded-full animate-spin" />
            ) : (
              <FileDown size={14} />
            )}
          </button>
          {expanded ? <ChevronUp size={15} className="text-aegisaxiom-text-secondary" /> : <ChevronDown size={15} className="text-aegisaxiom-text-secondary" />}
        </div>
      </button>

      {expanded && (
        <div className="border-t border-aegisaxiom-border px-5 py-4 space-y-1">
          {/* Executive Summary */}
          <SectionTitle>Executive Summary</SectionTitle>
          <p className="text-sm text-aegisaxiom-text-secondary leading-relaxed whitespace-pre-wrap">{doc.executive_summary}</p>

          {/* Current State vs Proposed */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
            {doc.current_state && (
              <div className="rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface p-4">
                <p className="text-xs font-semibold uppercase tracking-wider text-red-600 dark:text-red-400 mb-2">Current State</p>
                <p className="text-sm text-aegisaxiom-text-secondary mb-2">{doc.current_state.process_description}</p>
                {doc.current_state.manual_effort && (
                  <p className="text-xs text-aegisaxiom-text-secondary mb-2">Manual Effort: <span className="text-aegisaxiom-text">{doc.current_state.manual_effort}</span></p>
                )}
                {doc.current_state.pain_points?.length > 0 && (
                  <ul className="space-y-1 mb-2">
                    {doc.current_state.pain_points.map((p, i) => (
                      <li key={i} className="text-xs text-aegisaxiom-text-secondary flex items-start gap-1.5">
                        <span className="text-red-600 dark:text-red-400 mt-0.5">-</span>{p}
                      </li>
                    ))}
                  </ul>
                )}
                {doc.current_state.systems_involved?.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {doc.current_state.systems_involved.map((s, i) => (
                      <span key={i} className="text-xs px-2 py-0.5 rounded-md bg-aegisaxiom-elevated border border-aegisaxiom-border-hover/40 text-aegisaxiom-text-secondary">{s}</span>
                    ))}
                  </div>
                )}
              </div>
            )}
            {doc.proposed_ai_solution && (
              <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-4">
                <p className="text-xs font-semibold uppercase tracking-wider text-emerald-600 dark:text-emerald-400 mb-2">Proposed AI Solution</p>
                <p className="text-sm text-aegisaxiom-text-secondary mb-2">{doc.proposed_ai_solution.solution_description}</p>
                {doc.proposed_ai_solution.automation_percentage && (
                  <p className="text-xs text-aegisaxiom-text-secondary mb-2">Automation: <span className="text-emerald-600 dark:text-emerald-400 font-medium">{doc.proposed_ai_solution.automation_percentage}</span></p>
                )}
                <div className="space-y-1 text-xs">
                  <p className="text-aegisaxiom-text-secondary"><Cpu size={10} className="inline mr-1" />AI Role: <span className="text-aegisaxiom-text">{doc.proposed_ai_solution.ai_role}</span></p>
                  <p className="text-aegisaxiom-text-secondary"><Users size={10} className="inline mr-1" />Human Role: <span className="text-aegisaxiom-text">{doc.proposed_ai_solution.human_role}</span></p>
                </div>
                {doc.proposed_ai_solution.key_capabilities?.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {doc.proposed_ai_solution.key_capabilities.map((c, i) => (
                      <span key={i} className="text-xs px-2 py-0.5 rounded-md bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-500/20 text-emerald-700 dark:text-emerald-300">{c}</span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* BPMN Process Narrative */}
          {doc.bpmn_process_narrative && (
            <>
              <SectionTitle>Process Narrative</SectionTitle>
              <p className="text-sm text-aegisaxiom-text-secondary leading-relaxed whitespace-pre-wrap">{doc.bpmn_process_narrative}</p>
            </>
          )}

          {/* Integration Requirements */}
          {doc.integration_requirements?.length > 0 && (
            <>
              <SectionTitle>Integration Requirements</SectionTitle>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-aegisaxiom-text-muted border-b border-aegisaxiom-border">
                      <th className="text-left py-2 pr-3 font-medium">System</th>
                      <th className="text-left py-2 pr-3 font-medium">Type</th>
                      <th className="text-left py-2 pr-3 font-medium">Data</th>
                      <th className="text-left py-2 font-medium">Complexity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {doc.integration_requirements.map((ir, i) => (
                      <tr key={i} className="border-b border-aegisaxiom-border">
                        <td className="py-2 pr-3 text-aegisaxiom-text font-medium">{ir.system_name}</td>
                        <td className="py-2 pr-3 text-aegisaxiom-text-secondary">{ir.integration_type?.replace('_', ' ')}</td>
                        <td className="py-2 pr-3 text-aegisaxiom-text-secondary">{ir.data_exchanged}</td>
                        <td className="py-2">
                          <span className={`px-1.5 py-0.5 rounded ${COMPLEXITY_COLORS[ir.complexity] || ''}`}>{ir.complexity}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {/* Data Flow */}
          {doc.data_flow && (
            <>
              <SectionTitle>Data Flow</SectionTitle>
              <div className="flex items-start gap-3 text-xs">
                <div className="flex-1 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface p-3">
                  <p className="text-aegisaxiom-text-muted font-medium mb-1">Inputs</p>
                  {doc.data_flow.inputs?.map((inp, i) => (
                    <p key={i} className="text-aegisaxiom-text-secondary">{inp.name} <span className="text-aegisaxiom-text-muted">from {inp.source}</span></p>
                  ))}
                </div>
                <ArrowRight size={16} className="text-violet-600 dark:text-violet-400 mt-6 shrink-0" />
                <div className="flex-1 rounded-lg border border-violet-500/20 bg-violet-500/5 p-3">
                  <p className="text-violet-600 dark:text-violet-400 font-medium mb-1">Processing</p>
                  <p className="text-aegisaxiom-text-secondary">{doc.data_flow.processing}</p>
                </div>
                <ArrowRight size={16} className="text-violet-600 dark:text-violet-400 mt-6 shrink-0" />
                <div className="flex-1 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface p-3">
                  <p className="text-aegisaxiom-text-muted font-medium mb-1">Outputs</p>
                  {doc.data_flow.outputs?.map((out, i) => (
                    <p key={i} className="text-aegisaxiom-text-secondary">{out.name} <span className="text-aegisaxiom-text-muted">to {out.destination}</span></p>
                  ))}
                </div>
              </div>
              {doc.data_flow.data_sensitivity && (
                <p className="text-xs text-yellow-600 dark:text-yellow-400 mt-2">
                  <AlertTriangle size={10} className="inline mr-1" />
                  Data Sensitivity: {doc.data_flow.data_sensitivity}
                </p>
              )}
            </>
          )}

          {/* MMI Justification */}
          {doc.mmi_justification && (
            <>
              <SectionTitle>MMI Score Justification</SectionTitle>
              <div className="space-y-2">
                {[
                  { key: 'tss_rationale', label: 'TSS (Task Suitability)' },
                  { key: 'ie_rationale', label: 'IE (Integration Effort)' },
                  { key: 'time_rationale', label: 'Time Savings' },
                  { key: 'cost_rationale', label: 'Cost Reduction' },
                  { key: 'quality_rationale', label: 'Quality Improvement' },
                  { key: 'satisfaction_rationale', label: 'Satisfaction' },
                ].map(({ key, label }) => doc.mmi_justification[key] ? (
                  <div key={key} className="text-xs">
                    <span className="text-aegisaxiom-text-secondary font-medium">{label}:</span>{' '}
                    <span className="text-aegisaxiom-text-secondary">{doc.mmi_justification[key]}</span>
                  </div>
                ) : null)}
              </div>
            </>
          )}

          {/* Implementation Considerations */}
          {doc.implementation_considerations && (
            <>
              <SectionTitle>Implementation Considerations</SectionTitle>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {doc.implementation_considerations.risks?.length > 0 && (
                  <div>
                    <p className="text-xs text-red-600 dark:text-red-400 font-medium mb-1">Risks</p>
                    <ul className="space-y-0.5">{doc.implementation_considerations.risks.map((r, i) => (
                      <li key={i} className="text-xs text-aegisaxiom-text-secondary flex items-start gap-1.5"><span className="text-red-600 dark:text-red-400">!</span>{r}</li>
                    ))}</ul>
                  </div>
                )}
                {doc.implementation_considerations.dependencies?.length > 0 && (
                  <div>
                    <p className="text-xs text-violet-600 dark:text-violet-400 font-medium mb-1">Dependencies</p>
                    <ul className="space-y-0.5">{doc.implementation_considerations.dependencies.map((d, i) => (
                      <li key={i} className="text-xs text-aegisaxiom-text-secondary flex items-start gap-1.5"><span className="text-violet-600 dark:text-violet-400">&rarr;</span>{d}</li>
                    ))}</ul>
                  </div>
                )}
                {doc.implementation_considerations.assumptions?.length > 0 && (
                  <div>
                    <p className="text-xs text-yellow-600 dark:text-yellow-400 font-medium mb-1">Assumptions</p>
                    <ul className="space-y-0.5">{doc.implementation_considerations.assumptions.map((a, i) => (
                      <li key={i} className="text-xs text-aegisaxiom-text-secondary flex items-start gap-1.5"><span className="text-yellow-600 dark:text-yellow-400">~</span>{a}</li>
                    ))}</ul>
                  </div>
                )}
                {doc.implementation_considerations.open_questions?.length > 0 && (
                  <div>
                    <p className="text-xs text-blue-600 dark:text-blue-400 font-medium mb-1">Open Questions</p>
                    <ul className="space-y-0.5">{doc.implementation_considerations.open_questions.map((q, i) => (
                      <li key={i} className="text-xs text-aegisaxiom-text-secondary flex items-start gap-1.5"><span className="text-blue-600 dark:text-blue-400">?</span>{q}</li>
                    ))}</ul>
                  </div>
                )}
              </div>
            </>
          )}

          {/* Human Touchpoints */}
          {doc.human_touchpoints?.length > 0 && (
            <>
              <SectionTitle>Human Touchpoints</SectionTitle>
              <div className="space-y-2">
                {doc.human_touchpoints.map((ht, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs">
                    <Users size={12} className="text-aegisaxiom-text-secondary mt-0.5 shrink-0" />
                    <div>
                      <span className="text-aegisaxiom-text font-medium">{ht.touchpoint}</span>
                      <span className="text-aegisaxiom-text-muted"> — {ht.reason}</span>
                      {ht.frequency && <span className="text-aegisaxiom-text-muted"> ({ht.frequency})</span>}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function AIDesignDocumentViewer({ documents }) {
  if (!documents?.length) return null;

  return (
    <div className="space-y-2.5">
      {/* Implementation Complexity Key */}
      <div className="flex items-center gap-3 text-[10px] text-aegisaxiom-text-muted px-1">
        <span className="font-semibold uppercase tracking-wider">Implementation Complexity:</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-emerald-500" />Low</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-yellow-500" />Medium</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-500" />High</span>
      </div>
      {documents.map(doc => (
        <DesignDocCard key={doc.workflow_id} doc={doc} />
      ))}
    </div>
  );
}
