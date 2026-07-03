// Client-side PDF exporter for Vega v3 artifacts (design docs, EADDs, analysis).
//
// Why client-side and not server reportlab?
// The v2 dev branch uses html2canvas + jsPDF to render a richly-styled HTML
// container — including an embedded bpmn-js SVG of the process diagram — into
// a paginated PDF. The output matches the in-app dark theme exactly and
// embeds the BPMN diagram inline. ReportLab can't easily do either of those
// things, so we mirror the dev approach for v3 and extend it for the new
// fields (acceptance criteria, mmi_hypothesis, dev-build-spec EADD sections).

import { api } from './api';
import API_URL from './apiConfig';
import { getPdfPalette } from './pdfPalette';

function escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function slug(text, fallback = 'vega') {
  if (!text) return fallback;
  return String(text)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 60) || fallback;
}

async function captureBpmnImage({ bpmnXml, bpmnId } = {}) {
  // Render BPMN to a high-DPI PNG. The earlier "inline SVG, scale to column"
  // approach pancaked long horizontal pipelines into ~40px tall strips and
  // labels became unreadable. Now we rasterize the saved SVG into a 2400px
  // wide canvas at the diagram's natural aspect ratio — html2canvas embeds
  // it as a raster image, so PDF readers can zoom to 200%+ and the labels
  // stay crisp.
  //
  // v3 typically embeds bpmn_xml inline on the workflow; fall back to
  // /api/bpmn/{id} only when the id is present and inline is missing.
  let xml = bpmnXml;
  if (!xml && bpmnId) {
    try {
      const res = await api.get(`${API_URL}/api/bpmn/${bpmnId}`);
      if (res.ok) {
        const data = await res.json();
        xml = data.bpmn_xml || data.edited_xml;
      }
    } catch {
      return null;
    }
  }
  if (!xml) return null;

  let tmpContainer = null;
  let viewer = null;
  let svgUrl = null;
  try {
    // Generous offscreen viewport so ELK has room to lay out wide pipelines
    // before fit-viewport rescales — small viewports cram labels.
    tmpContainer = document.createElement('div');
    tmpContainer.style.cssText =
      'position:fixed;left:-9999px;top:0;width:1800px;height:1200px;background:#ffffff;';
    document.body.appendChild(tmpContainer);
    const { default: BpmnViewer } = await import('bpmn-js/lib/Viewer');
    viewer = new BpmnViewer({ container: tmpContainer });
    await viewer.importXML(xml);
    const canvas = viewer.get('canvas');
    canvas.zoom('fit-viewport');
    const { svg } = await viewer.saveSVG();
    const cleanSvg = svg.replace(/^<\?xml[^>]*\?>\s*/, '');

    // Load the SVG into an Image to read its natural aspect
    const blob = new Blob([cleanSvg], { type: 'image/svg+xml;charset=utf-8' });
    svgUrl = URL.createObjectURL(blob);
    const img = new Image();
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = reject;
      img.src = svgUrl;
    });
    const naturalW = img.naturalWidth || 1800;
    const naturalH = img.naturalHeight || 600;
    const aspect = naturalH / naturalW; // height / width

    // Rasterize at 2400px wide so PDF readers can zoom without blurring.
    // Cap aspect at 0.18 minimum so 12-node linear pipelines aren't pancaked
    // (renders extra whitespace top/bottom rather than a 40px-tall strip).
    const targetW = 2400;
    const renderAspect = Math.max(aspect, 0.18);
    const targetH = Math.round(targetW * renderAspect);

    const off = document.createElement('canvas');
    off.width = targetW;
    off.height = targetH;
    const ctx = off.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, targetW, targetH);
    // Center vertically when we padded the aspect
    const drawH = Math.round(targetW * aspect);
    const yOffset = Math.round((targetH - drawH) / 2);
    ctx.drawImage(img, 0, yOffset, targetW, drawH);

    return { dataUrl: off.toDataURL('image/png'), aspect: renderAspect };
  } catch {
    return null;
  } finally {
    if (svgUrl) URL.revokeObjectURL(svgUrl);
    if (viewer) viewer.destroy();
    if (tmpContainer) tmpContainer.remove();
  }
}

// ---------------------------------------------------------------------------
// Shared HTML primitives
// ---------------------------------------------------------------------------

function sectionLabel(title, palette) {
  return `<p style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.10em;color:${palette.textMuted};margin:22px 0 10px;">${escapeHtml(title)}</p>`;
}

function buildHeader({ title, subtitle, badge, badgeColor, palette }) {
  const badgeHtml = badge
    ? `<span style="font-size:10px;padding:4px 12px;border-radius:20px;border:1px solid ${badgeColor}55;color:${badgeColor};background:${badgeColor}1a;text-transform:lowercase;">${escapeHtml(badge)}</span>`
    : '';
  return `
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
      <h1 style="font-size:20px;font-weight:700;margin:0;flex:1;color:${palette.text};line-height:1.25;">${escapeHtml(title)}</h1>
      ${badgeHtml}
    </div>
    ${subtitle ? `<p style="font-size:11px;color:${palette.textMuted};margin:0 0 18px;">${escapeHtml(subtitle)}</p>` : ''}
  `;
}

function sideBySidePanels(left, right, palette) {
  return `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:6px;">
      ${left || ''}
      ${right || ''}
    </div>
  `;
}

// ---------------------------------------------------------------------------
// PDF rendering — html2canvas + jsPDF, paginated
// ---------------------------------------------------------------------------

async function renderHtmlToPdf({ html, filename, palette }) {
  const html2canvas = (await import('html2canvas')).default;
  const { jsPDF } = await import('jspdf');

  const container = document.createElement('div');
  container.style.cssText =
    `position:fixed;left:-9999px;top:0;width:880px;background:${palette.bg};color:${palette.text};font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:40px 44px;box-sizing:border-box;`;
  container.innerHTML = html;
  document.body.appendChild(container);

  try {
    const canvas = await html2canvas(container, {
      backgroundColor: palette.bg,
      scale: 1.6,
      useCORS: true,
      logging: false,
      width: 880,
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
      const sourceH = Math.min(
        (contentHeight / imgHeight) * canvas.height,
        canvas.height - sourceY,
      );
      const sliceHeight = (sourceH / canvas.height) * imgHeight;
      const pageCanvas = document.createElement('canvas');
      pageCanvas.width = canvas.width;
      pageCanvas.height = sourceH;
      const ctx = pageCanvas.getContext('2d');
      // Paint background so transparent regions don't render black on macOS Preview.
      ctx.fillStyle = palette.bg;
      ctx.fillRect(0, 0, pageCanvas.width, pageCanvas.height);
      ctx.drawImage(canvas, 0, sourceY, canvas.width, sourceH, 0, 0, canvas.width, sourceH);
      const pageImgData = pageCanvas.toDataURL('image/png');
      pdf.addImage(pageImgData, 'PNG', margin, margin, contentWidth, sliceHeight);
      yOffset += contentHeight;
    }
    pdf.save(filename);
  } finally {
    document.body.removeChild(container);
  }
}

// ---------------------------------------------------------------------------
// Workflow Design Doc
// ---------------------------------------------------------------------------

export async function exportV3DesignDocPdf({ workflow, payload }) {
  const palette = getPdfPalette();
  const doc = workflow?.design_doc || {};
  const wfName = workflow?.workflow_name || 'AI Design Document';
  const client = payload?.client_company || 'Unknown client';
  const today = new Date().toISOString().slice(0, 10);
  const complexity = doc.implementation_considerations?.estimated_complexity || 'medium';
  const complexityColor =
    complexity === 'low' ? palette.success : complexity === 'high' ? palette.danger : palette.warning;

  const lines = [];
  lines.push(buildHeader({
    title: wfName,
    subtitle: `${client} · Generated ${today}`,
    badge: complexity,
    badgeColor: complexityColor,
    palette,
  }));

  if (doc.executive_summary) {
    lines.push(sectionLabel('Executive Summary', palette));
    lines.push(`<p style="font-size:12px;line-height:1.7;color:${palette.textSecondary};margin:0;">${escapeHtml(doc.executive_summary)}</p>`);
  }

  // Current state vs proposed AI solution side-by-side panels
  const cs = doc.current_state || {};
  const ps = doc.proposed_ai_solution || {};
  if (cs.process_description || ps.solution_description) {
    let leftPanel = '';
    if (cs.process_description || cs.pain_points?.length) {
      leftPanel = `
        <div style="border:1px solid ${palette.border};border-radius:8px;padding:16px;background:${palette.bg};">
          <p style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:${palette.danger};margin:0 0 8px;">Current State</p>
          ${cs.process_description ? `<p style="font-size:11px;line-height:1.6;color:${palette.textSecondary};margin:0;">${escapeHtml(cs.process_description)}</p>` : ''}
          ${cs.manual_effort ? `<p style="font-size:11px;color:${palette.textMuted};margin:8px 0 0;">Manual Effort: <span style="color:${palette.text};">${escapeHtml(cs.manual_effort)}</span></p>` : ''}
          ${cs.pain_points?.length ? `<ul style="margin:8px 0 0;padding-left:0;list-style:none;">${cs.pain_points.map((pp) => `<li style="font-size:11px;line-height:1.5;color:${palette.textMuted};margin-bottom:4px;">- ${escapeHtml(pp)}</li>`).join('')}</ul>` : ''}
          ${cs.systems_involved?.length ? `<div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:4px;">${cs.systems_involved.map((s) => `<span style="font-size:10px;padding:2px 8px;border-radius:4px;background:${palette.borderSubtle};color:${palette.textSecondary};">${escapeHtml(s)}</span>`).join('')}</div>` : ''}
        </div>`;
    }
    let rightPanel = '';
    if (ps.solution_description) {
      rightPanel = `
        <div style="border:1px solid ${palette.successPanelBorder};border-radius:8px;padding:16px;background:${palette.successPanelBg};">
          <p style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:${palette.success};margin:0 0 8px;">Proposed AI Solution</p>
          <p style="font-size:11px;line-height:1.6;color:${palette.textSecondary};margin:0;">${escapeHtml(ps.solution_description)}</p>
          ${ps.automation_percentage ? `<p style="font-size:11px;color:${palette.success};margin:8px 0 0;">Automation: ${escapeHtml(ps.automation_percentage)}</p>` : ''}
          ${ps.ai_role ? `<p style="font-size:11px;color:${palette.textMuted};margin:6px 0 0;">AI Role: <span style="color:${palette.text};">${escapeHtml(ps.ai_role)}</span></p>` : ''}
          ${ps.human_role ? `<p style="font-size:11px;color:${palette.textMuted};margin:2px 0 0;">Human Role: <span style="color:${palette.text};">${escapeHtml(ps.human_role)}</span></p>` : ''}
        </div>`;
    }
    lines.push(sideBySidePanels(leftPanel, rightPanel, palette));
  }

  if (doc.bpmn_process_narrative) {
    lines.push(sectionLabel('Process Narrative', palette));
    lines.push(`<p style="font-size:11px;line-height:1.7;color:${palette.textSecondary};white-space:pre-wrap;margin:0;">${escapeHtml(doc.bpmn_process_narrative)}</p>`);
  }

  // Embedded BPMN diagram — high-DPI PNG so the user can zoom in PDF readers.
  const bpmn = await captureBpmnImage({
    bpmnXml: workflow?.bpmn_xml,
    bpmnId: workflow?.bpmn_id,
  });
  if (bpmn) {
    lines.push(sectionLabel('Process Diagram', palette));
    lines.push(`<div style="border:1px solid ${palette.border};border-radius:6px;padding:8px;background:#ffffff;overflow:hidden;">`);
    lines.push(`<img src="${bpmn.dataUrl}" style="width:100%;height:auto;display:block;" alt="BPMN diagram" />`);
    lines.push(`</div>`);
    lines.push(`<p style="font-size:10px;color:${palette.textMuted};margin:4px 0 0;font-style:italic;">Tip: zoom in your PDF reader to read step labels — the diagram is rendered at high DPI for that.</p>`);
  }

  // Acceptance criteria — v3 specific (per-flow success criteria)
  const ac = workflow?.acceptance_criteria || [];
  if (ac.length) {
    lines.push(sectionLabel('Acceptance Criteria', palette));
    lines.push(`<ul style="margin:0;padding-left:0;list-style:none;">${ac.map((c) => `<li style="font-size:11px;line-height:1.6;color:${palette.textSecondary};margin-bottom:6px;"><span style="color:${palette.accent};font-weight:600;">✓</span> <span style="color:${palette.text};font-weight:600;">${escapeHtml(c.flow_name || '')}</span>${c.flow_name ? ' — ' : ''}${escapeHtml(c.success_when || '(success criterion not captured)')}</li>`).join('')}</ul>`);
  }

  if (doc.integration_requirements?.length) {
    lines.push(sectionLabel('Integration Requirements', palette));
    lines.push(`<table style="width:100%;border-collapse:collapse;font-size:11px;">`);
    lines.push(`<tr style="color:${palette.textMuted};border-bottom:1px solid ${palette.border};text-transform:uppercase;letter-spacing:.06em;font-size:10px;">`);
    lines.push(`<th style="text-align:left;padding:8px 8px 8px 0;">System</th>`);
    lines.push(`<th style="text-align:left;padding:8px 8px 8px 0;">Type</th>`);
    lines.push(`<th style="text-align:left;padding:8px 8px 8px 0;">Data</th>`);
    lines.push(`<th style="text-align:left;padding:8px 0;">Complexity</th></tr>`);
    doc.integration_requirements.forEach((ir) => {
      lines.push(`<tr style="border-bottom:1px solid ${palette.borderSubtle};">`);
      lines.push(`<td style="padding:8px 8px 8px 0;color:${palette.text};font-weight:600;">${escapeHtml(ir.system_name || '')}</td>`);
      lines.push(`<td style="padding:8px 8px 8px 0;color:${palette.textMuted};">${escapeHtml((ir.integration_type || '').replace('_', ' '))}</td>`);
      lines.push(`<td style="padding:8px 8px 8px 0;color:${palette.textMuted};">${escapeHtml(ir.data_exchanged || '')}</td>`);
      lines.push(`<td style="padding:8px 0;color:${palette.textMuted};">${escapeHtml(ir.complexity || '')}</td></tr>`);
    });
    lines.push(`</table>`);
  }

  // MMI hypothesis — v3 carries explicit numeric scores + confidence + reasoning per dimension.
  const mmi = workflow?.mmi_hypothesis || {};
  const mj = doc.mmi_justification || {};
  if (Object.keys(mmi).length || Object.keys(mj).length) {
    lines.push(sectionLabel('MMI Score Justification', palette));
    const dims = [
      ['tss', 'TSS (Task Suitability)'],
      ['ie', 'IE (Integration Effort)'],
      ['time', 'Time Savings'],
      ['cost', 'Cost Reduction'],
      ['quality', 'Quality Improvement'],
      ['satisfaction', 'Satisfaction'],
    ];
    dims.forEach(([key, label]) => {
      const dim = mmi[key] || {};
      const rationale = dim.reasoning || mj[`${key}_rationale`];
      if (!rationale && dim.value == null) return;
      const scoreBits = [];
      if (dim.value != null) scoreBits.push(`Score ${dim.value}`);
      if (dim.confidence) scoreBits.push(`${dim.confidence} confidence`);
      const scorePill = scoreBits.length
        ? `<span style="font-size:10px;padding:1px 8px;border-radius:10px;background:${palette.borderSubtle};color:${palette.textSecondary};margin-right:6px;">${escapeHtml(scoreBits.join(' · '))}</span>`
        : '';
      lines.push(`<p style="font-size:11px;line-height:1.6;color:${palette.textSecondary};margin:6px 0;"><span style="color:${palette.text};font-weight:600;">${label}:</span> ${scorePill}${escapeHtml(rationale || '')}</p>`);
    });
    if (mmi.score != null || mj.overall_score != null) {
      lines.push(`<p style="font-size:11px;color:${palette.textMuted};margin:10px 0 0;">Overall: <span style="color:${palette.accent};font-weight:600;">${escapeHtml(String(mmi.score ?? mj.overall_score))}</span></p>`);
    }
  }

  if (doc.implementation_considerations) {
    const ic = doc.implementation_considerations;
    lines.push(sectionLabel('Implementation Considerations', palette));
    lines.push(`<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">`);
    const subsection = (title, color, items, prefix) => {
      if (!items?.length) return '';
      return `<div><p style="font-size:11px;font-weight:600;color:${color};margin:0 0 6px;">${title}</p><ul style="margin:0;padding-left:0;list-style:none;">${items.map((r) => `<li style="font-size:11px;line-height:1.5;color:${palette.textMuted};margin-bottom:4px;"><span style="color:${color};margin-right:6px;">${prefix}</span>${escapeHtml(r)}</li>`).join('')}</ul></div>`;
    };
    lines.push(subsection('Risks', palette.danger, ic.risks, '!'));
    lines.push(subsection('Dependencies', palette.accent, ic.dependencies, '→'));
    lines.push(subsection('Assumptions', palette.warning, ic.assumptions, '~'));
    lines.push(subsection('Open Questions', palette.info, ic.open_questions, '?'));
    lines.push(`</div>`);
  }

  if (doc.human_touchpoints?.length) {
    lines.push(sectionLabel('Human Touchpoints', palette));
    doc.human_touchpoints.forEach((ht) => {
      lines.push(`<p style="font-size:11px;line-height:1.6;color:${palette.textSecondary};margin:6px 0;"><span style="font-weight:600;color:${palette.text};">${escapeHtml(ht.touchpoint || '')}</span> — ${escapeHtml(ht.reason || '')}${ht.frequency ? ` <span style="color:${palette.textMuted};">(${escapeHtml(ht.frequency)})</span>` : ''}</p>`);
    });
  }

  const filename = `ai_design_doc_${slug(client, 'client')}_${slug(wfName, 'workflow')}.pdf`;
  await renderHtmlToPdf({ html: lines.join(''), filename, palette });
}

// ---------------------------------------------------------------------------
// App Design Doc (developer build spec)
// ---------------------------------------------------------------------------

function notCaptured(palette) {
  return `<span style="color:${palette.textMuted};font-style:italic;">(NOT CAPTURED)</span>`;
}

export async function exportV3EaddPdf({ app, payload }) {
  const palette = getPdfPalette();
  const eadd = app?.eadd_doc || {};
  const appName = app?.app_name || 'App Design Document';
  const client = payload?.client_company || 'Unknown client';
  const today = new Date().toISOString().slice(0, 10);
  const complexity = app?.complexity || 'medium';
  const complexityColor =
    complexity === 'low' ? palette.success : complexity === 'high' ? palette.danger : palette.warning;

  const lines = [];
  lines.push(buildHeader({
    title: appName,
    subtitle: `${client} · Generated ${today} · Developer Build Specification`,
    badge: complexity,
    badgeColor: complexityColor,
    palette,
  }));

  if (app?.app_pitch_summary) {
    lines.push(sectionLabel('Overview', palette));
    lines.push(`<p style="font-size:12px;line-height:1.7;color:${palette.textSecondary};margin:0 0 8px;">${escapeHtml(app.app_pitch_summary)}</p>`);
    if (app.primary_persona_hint) {
      lines.push(`<p style="font-size:11px;color:${palette.textMuted};margin:0;">Primary persona: <span style="color:${palette.text};">${escapeHtml(app.primary_persona_hint)}</span></p>`);
    }
    const entityCount = (eadd.data_model || []).length;
    const integCount = (app.integrations || []).length;
    const flowCount = (eadd.core_flows || []).length;
    const novelCount = (app.novel_ui_patterns || []).length;
    const customCount = (app.custom_logic || []).length;
    lines.push(`<p style="font-size:10px;color:${palette.textMuted};margin:8px 0 0;text-transform:uppercase;letter-spacing:.06em;">Sizing snapshot: ${entityCount} entities · ${integCount} integrations · ${flowCount} flows · ${novelCount} novel UI · ${customCount} custom rules</p>`);
  }

  // Linked AI workflows
  const linkedIds = app?.linked_workflow_ids || [];
  if (linkedIds.length) {
    const wfById = Object.fromEntries((payload.workflows || []).map((w) => [w.workflow_id, w]));
    lines.push(sectionLabel('Linked AI Workflows', palette));
    linkedIds.forEach((wid) => {
      const wf = wfById[wid];
      const name = wf?.workflow_name || wid;
      const summary = wf?.design_doc?.executive_summary || '';
      lines.push(`<p style="font-size:11px;line-height:1.6;color:${palette.textSecondary};margin:6px 0;"><span style="color:${palette.text};font-weight:600;">${escapeHtml(name)}</span>${summary ? ` — ${escapeHtml(summary)}` : ''}</p>`);
    });
  }

  // Personas
  const personas = app?.personas || [];
  if (personas.length) {
    lines.push(sectionLabel('Personas', palette));
    personas.forEach((p) => {
      lines.push(`<div style="margin:8px 0 12px;border-left:2px solid ${palette.accent};padding-left:12px;">`);
      lines.push(`<p style="font-size:12px;font-weight:600;color:${palette.text};margin:0 0 4px;">${escapeHtml(p.role || '(unnamed role)')}</p>`);
      const kv = (label, val) => `<p style="font-size:11px;color:${palette.textMuted};margin:2px 0;">${label}: <span style="color:${palette.textSecondary};">${val ? escapeHtml(String(val)) : notCaptured(palette)}</span></p>`;
      lines.push(kv('User count', p.count));
      lines.push(kv('Technical literacy', p.technical_literacy));
      lines.push(kv('Primary device', p.device));
      lines.push(kv('Concurrent usage', p.concurrent_usage));
      lines.push(kv('Accessibility', p.accessibility_needs));
      lines.push(`</div>`);
    });
  }

  // Core User Flows
  if (eadd.core_flows?.length) {
    lines.push(sectionLabel('Core User Flows', palette));
    eadd.core_flows.forEach((flow, idx) => {
      if (typeof flow === 'string') {
        lines.push(`<p style="font-size:11px;color:${palette.textSecondary};margin:6px 0;"><span style="color:${palette.accent};font-weight:600;">${idx + 1}.</span> ${escapeHtml(flow)}</p>`);
        return;
      }
      const title = flow.name || flow.title || `Flow ${idx + 1}`;
      const success = flow.success_when || flow.success_criteria || flow.acceptance_criteria;
      lines.push(`<div style="margin:10px 0;border:1px solid ${palette.border};border-radius:6px;padding:12px;">`);
      lines.push(`<p style="font-size:12px;font-weight:600;color:${palette.text};margin:0 0 4px;">${idx + 1}. ${escapeHtml(title)}</p>`);
      if (flow.actor) lines.push(`<p style="font-size:10px;color:${palette.textMuted};margin:2px 0;text-transform:uppercase;letter-spacing:.06em;">Actor: ${escapeHtml(flow.actor)} · Trigger: ${escapeHtml(flow.trigger || '?')}</p>`);
      if (flow.description) lines.push(`<p style="font-size:11px;color:${palette.textSecondary};margin:6px 0;">${escapeHtml(flow.description)}</p>`);
      if (Array.isArray(flow.steps) && flow.steps.length) {
        lines.push(`<ol style="font-size:11px;color:${palette.textSecondary};margin:6px 0;padding-left:18px;">${flow.steps.map((s) => `<li style="margin-bottom:2px;">${escapeHtml(String(s))}</li>`).join('')}</ol>`);
      }
      lines.push(`<p style="font-size:11px;color:${palette.success};margin:6px 0 0;"><span style="font-weight:600;">Success when:</span> ${success ? escapeHtml(success) : notCaptured(palette)}</p>`);
      if (Array.isArray(flow.error_states) && flow.error_states.length) {
        const errRows = flow.error_states
          .map((e) => {
            if (e == null) return null;
            if (typeof e === 'string') return { cond: e, handling: '' };
            if (typeof e === 'object') {
              const cond = e.condition || e.trigger || e.case || e.when || e.scenario || '';
              const handling = e.handling || e.behavior || e.recovery || e.action || e.response || '';
              if (cond || handling) return { cond, handling };
              return { cond: e.description || e.message || JSON.stringify(e), handling: '' };
            }
            return { cond: String(e), handling: '' };
          })
          .filter(Boolean);
        if (errRows.length) {
          lines.push(`<p style="font-size:11px;color:${palette.danger};margin:6px 0 2px;font-weight:600;">Error states:</p>`);
          lines.push(`<ul style="margin:0;padding-left:0;list-style:none;">`);
          errRows.forEach(({ cond, handling }) => {
            const condHtml = cond ? `<span style="color:${palette.text};font-weight:600;">${escapeHtml(cond)}</span>` : '';
            const handHtml = handling ? `<span style="color:${palette.textSecondary};"> → ${escapeHtml(handling)}</span>` : '';
            lines.push(`<li style="font-size:11px;color:${palette.textMuted};margin:2px 0;line-height:1.5;"><span style="color:${palette.danger};margin-right:6px;">!</span>${condHtml}${handHtml}</li>`);
          });
          lines.push(`</ul>`);
        }
      }
      lines.push(`</div>`);
    });
  }

  // Data Model
  if (eadd.data_model?.length) {
    lines.push(sectionLabel('Data Model', palette));
    eadd.data_model.forEach((entry) => {
      if (typeof entry === 'string') {
        lines.push(`<p style="font-size:11px;color:${palette.textSecondary};">${escapeHtml(entry)}</p>`);
        return;
      }
      const ename = entry.entity || entry.name || '(unnamed entity)';
      const fields = entry.fields || entry.key_fields || [];
      lines.push(`<div style="margin:8px 0 12px;">`);
      lines.push(`<p style="font-size:12px;font-weight:600;color:${palette.text};margin:0 0 4px;">${escapeHtml(ename)}</p>`);
      if (entry.description) lines.push(`<p style="font-size:11px;color:${palette.textSecondary};margin:0 0 6px;">${escapeHtml(entry.description)}</p>`);
      if (Array.isArray(fields) && fields.length) {
        lines.push(`<table style="width:100%;border-collapse:collapse;font-size:10px;margin-top:4px;">`);
        lines.push(`<tr style="color:${palette.textMuted};border-bottom:1px solid ${palette.border};"><th style="text-align:left;padding:4px 8px 4px 0;">Field</th><th style="text-align:left;padding:4px 8px 4px 0;">Type</th><th style="text-align:left;padding:4px 0;">Notes</th></tr>`);
        fields.forEach((f) => {
          if (typeof f !== 'object' || f === null) {
            lines.push(`<tr><td colspan="3" style="padding:4px 0;color:${palette.textSecondary};">${escapeHtml(String(f))}</td></tr>`);
            return;
          }
          const ftype = f.type || '(NOT TYPED)';
          const nullable = f.nullable === true ? ', nullable' : f.nullable === false ? ', required' : '';
          const enums = Array.isArray(f.enum_values) && f.enum_values.length ? ` enum: ${f.enum_values.join(', ')}` : '';
          lines.push(`<tr style="border-bottom:1px solid ${palette.borderSubtle};">`);
          lines.push(`<td style="padding:6px 8px 6px 0;color:${palette.text};font-family:ui-monospace,SFMono-Regular,monospace;">${escapeHtml(f.name || '')}</td>`);
          lines.push(`<td style="padding:6px 8px 6px 0;color:${palette.accent};font-family:ui-monospace,SFMono-Regular,monospace;">${escapeHtml(ftype + nullable)}</td>`);
          lines.push(`<td style="padding:6px 0;color:${palette.textMuted};">${escapeHtml(f.description || f.notes || '')}${escapeHtml(enums)}</td></tr>`);
        });
        lines.push(`</table>`);
      }
      const sm = entry.state_machine || {};
      if ((sm.states || []).length || (sm.transitions || []).length) {
        lines.push(`<p style="font-size:10px;color:${palette.textMuted};margin:10px 0 6px;text-transform:uppercase;letter-spacing:.06em;font-weight:600;">State Machine</p>`);
        if (sm.states?.length) {
          lines.push(`<div style="font-size:11px;color:${palette.textSecondary};margin:0 0 8px;line-height:2;">`);
          lines.push(`<span style="color:${palette.textMuted};margin-right:8px;">States:</span>`);
          lines.push(sm.states.map((s) => `<code style="display:inline-block;background:${palette.borderSubtle};padding:3px 8px;margin:0 4px 4px 0;border-radius:4px;color:${palette.text};font-size:10px;">${escapeHtml(s)}</code>`).join(''));
          lines.push(`</div>`);
        }
        if (sm.transitions?.length) {
          lines.push(`<ul style="margin:6px 0 0;padding-left:0;list-style:none;">`);
          sm.transitions.forEach((t) => {
            lines.push(`<li style="font-size:11px;color:${palette.textSecondary};margin:6px 0;line-height:1.6;display:flex;align-items:baseline;gap:6px;flex-wrap:wrap;">`);
            lines.push(`<code style="background:${palette.borderSubtle};padding:2px 6px;border-radius:3px;color:${palette.text};font-size:10px;">${escapeHtml(t.from || '?')}</code>`);
            lines.push(`<span style="color:${palette.textMuted};">→</span>`);
            lines.push(`<code style="background:${palette.borderSubtle};padding:2px 6px;border-radius:3px;color:${palette.text};font-size:10px;">${escapeHtml(t.to || '?')}</code>`);
            lines.push(`<span style="color:${palette.textMuted};">on</span>`);
            lines.push(`<span style="color:${palette.accent};">${escapeHtml(t.trigger || '?')}</span>`);
            lines.push(`</li>`);
          });
          lines.push(`</ul>`);
        }
      }
      lines.push(`</div>`);
    });
  }

  // API Surface
  if (eadd.api_surface?.length) {
    lines.push(sectionLabel('API Surface', palette));
    lines.push(`<table style="width:100%;border-collapse:collapse;font-size:11px;">`);
    lines.push(`<tr style="color:${palette.textMuted};border-bottom:1px solid ${palette.border};text-transform:uppercase;letter-spacing:.06em;font-size:10px;"><th style="text-align:left;padding:6px 8px 6px 0;">Method</th><th style="text-align:left;padding:6px 8px 6px 0;">Path</th><th style="text-align:left;padding:6px 0;">Purpose</th></tr>`);
    eadd.api_surface.forEach((ep) => {
      if (typeof ep !== 'object' || ep === null) return;
      lines.push(`<tr style="border-bottom:1px solid ${palette.borderSubtle};">`);
      lines.push(`<td style="padding:8px 8px 8px 0;color:${palette.accent};font-family:ui-monospace,SFMono-Regular,monospace;font-weight:600;">${escapeHtml(ep.method || '?')}</td>`);
      lines.push(`<td style="padding:8px 8px 8px 0;color:${palette.text};font-family:ui-monospace,SFMono-Regular,monospace;">${escapeHtml(ep.path || '?')}</td>`);
      lines.push(`<td style="padding:8px 0;color:${palette.textMuted};">${escapeHtml(ep.purpose || '')}</td></tr>`);
    });
    lines.push(`</table>`);
  }

  // Auth Model
  const authModel = eadd.auth_model || {};
  if (authModel.session_model || authModel.roles?.length || authModel.ownership_model) {
    lines.push(sectionLabel('Auth Model', palette));
    if (authModel.session_model) lines.push(`<p style="font-size:11px;color:${palette.textSecondary};margin:4px 0;">Session: <span style="color:${palette.text};">${escapeHtml(authModel.session_model)}</span></p>`);
    if (authModel.ownership_model) lines.push(`<p style="font-size:11px;color:${palette.textSecondary};margin:4px 0;">Ownership: <span style="color:${palette.text};">${escapeHtml(authModel.ownership_model)}</span></p>`);
    if (authModel.roles?.length) {
      lines.push(`<p style="font-size:10px;color:${palette.textMuted};margin:8px 0 4px;text-transform:uppercase;letter-spacing:.06em;">Roles</p>`);
      authModel.roles.forEach((r) => {
        if (typeof r !== 'object' || r === null) return;
        const perms = Array.isArray(r.permissions) ? r.permissions.join('; ') : (r.permissions || '');
        lines.push(`<p style="font-size:11px;color:${palette.textSecondary};margin:2px 0;"><span style="color:${palette.text};font-weight:600;">${escapeHtml(r.name || '?')}</span>: ${escapeHtml(perms)}</p>`);
      });
    }
  }

  // Integrations (with depth)
  const integs = app?.integrations || [];
  if (integs.length || eadd.integrations?.length) {
    lines.push(sectionLabel('Integrations', palette));
    integs.forEach((it) => {
      if (typeof it !== 'object' || it === null) return;
      const bucket = { 1: 'pre-research', 2: 'transcript', 3: 'needs-followup' }[it.bucket] || '';
      lines.push(`<div style="margin:8px 0;border-left:2px solid ${palette.accent};padding:6px 12px;">`);
      lines.push(`<p style="font-size:12px;font-weight:600;color:${palette.text};margin:0 0 4px;">${escapeHtml(it.system_name || '(unnamed)')}${bucket ? ` <span style="font-size:10px;color:${palette.textMuted};font-weight:400;">[${bucket}]</span>` : ''}</p>`);
      if (it.notes) lines.push(`<p style="font-size:11px;color:${palette.textSecondary};margin:2px 0;">${escapeHtml(it.notes)}</p>`);
      const depth = [
        ['Disposition', it.disposition],
        ['Auth method', it.auth_method],
        ['Rate limits', it.rate_limits],
        ['Sandbox', it.sandbox_availability],
        ['Pattern', it.interaction_pattern],
        ['Read/write', it.read_write],
        ['Access', it.access_status],
      ];
      depth.forEach(([k, v]) => {
        lines.push(`<p style="font-size:10px;color:${palette.textMuted};margin:1px 0;">${k}: <span style="color:${v ? palette.textSecondary : palette.textMuted};">${v ? escapeHtml(String(v)) : '(NOT CAPTURED)'}</span></p>`);
      });
      lines.push(`</div>`);
    });
  }

  // NFRs
  const nfrs = app?.nfrs || {};
  if (Object.values(nfrs).some(Boolean)) {
    lines.push(sectionLabel('Non-Functional Requirements', palette));
    const nfrRows = [
      ['RBAC', nfrs.rbac],
      ['SLA', nfrs.sla],
      ['Compliance', nfrs.compliance],
      ['Multi-region', nfrs.multi_region],
      ['Data freshness', nfrs.data_freshness],
      ['Audit logging', nfrs.audit_logging],
      ['Performance', nfrs.performance_budgets],
      ['Devices', nfrs.device_support],
      ['Browsers', nfrs.browser_support],
    ].filter(([, v]) => v);
    lines.push(`<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">`);
    nfrRows.forEach(([k, v]) => {
      lines.push(`<p style="font-size:11px;color:${palette.textSecondary};margin:0;"><span style="color:${palette.textMuted};">${k}:</span> ${escapeHtml(String(v))}</p>`);
    });
    lines.push(`</div>`);
  }

  // Business Rules
  if (eadd.business_rules?.length) {
    lines.push(sectionLabel('Business Rules', palette));
    eadd.business_rules.forEach((rule) => {
      if (typeof rule === 'string') {
        lines.push(`<p style="font-size:11px;color:${palette.textSecondary};margin:4px 0;">• ${escapeHtml(rule)}</p>`);
        return;
      }
      lines.push(`<p style="font-size:11px;color:${palette.textSecondary};margin:6px 0;"><span style="color:${palette.text};font-weight:600;">${escapeHtml(rule.name || '(unnamed)')}:</span> ${escapeHtml(rule.rule || '')}</p>`);
      if (rule.rationale) lines.push(`<p style="font-size:10px;color:${palette.textMuted};margin:2px 0 6px 12px;">${escapeHtml(rule.rationale)}</p>`);
    });
  }

  // Observability
  const obs = eadd.observability || {};
  if (obs.events_to_log?.length || obs.metrics_to_track?.length || obs.alerts?.length) {
    lines.push(sectionLabel('Observability', palette));
    const subList = (label, items, color) => {
      if (!items?.length) return '';
      return `<div style="margin-bottom:8px;"><p style="font-size:10px;color:${color};margin:0 0 4px;text-transform:uppercase;letter-spacing:.06em;font-weight:600;">${label}</p><ul style="margin:0;padding-left:16px;">${items.map((i) => `<li style="font-size:11px;color:${palette.textSecondary};margin:2px 0;">${escapeHtml(String(i))}</li>`).join('')}</ul></div>`;
    };
    lines.push(subList('Events to log', obs.events_to_log, palette.info));
    lines.push(subList('Metrics', obs.metrics_to_track, palette.success));
    lines.push(subList('Alerts', obs.alerts, palette.warning));
  }

  // Out of scope
  if (eadd.out_of_scope?.length) {
    lines.push(sectionLabel('Out of Scope (v1)', palette));
    lines.push(`<ul style="margin:0;padding-left:0;list-style:none;">${eadd.out_of_scope.map((o) => `<li style="font-size:11px;color:${palette.textMuted};margin:3px 0;"><span style="color:${palette.warning};">×</span> ${escapeHtml(String(o))}</li>`).join('')}</ul>`);
  }

  const filename = `app_design_doc_${slug(client, 'client')}_${slug(appName, 'app')}.pdf`;
  await renderHtmlToPdf({ html: lines.join(''), filename, palette });
}

// ---------------------------------------------------------------------------
// Discovery Analysis (top-level report)
// ---------------------------------------------------------------------------

export async function exportV3AnalysisPdf({ payload }) {
  const palette = getPdfPalette();
  const client = payload?.client_company || 'Unknown client';
  const today = new Date().toISOString().slice(0, 10);

  const lines = [];
  lines.push(buildHeader({
    title: `Vega Discovery Report — ${client}`,
    subtitle: `Generated ${today}`,
    palette,
  }));

  const flags = payload.session_flags || {};
  if (Object.keys(flags).length) {
    lines.push(sectionLabel('Session Flags', palette));
    const row = (k, v) => `<p style="font-size:11px;color:${palette.textSecondary};margin:3px 0;"><span style="color:${palette.textMuted};">${k}:</span> ${escapeHtml(String(v ?? '—'))}</p>`;
    lines.push(row('Technical contact', flags.tech_contact ?? flags.tech_contact_flag ?? '—'));
    lines.push(row('Engineering capacity', flags.engineering_capacity ?? flags.engineering_capacity_flag ?? '—'));
    lines.push(row('IT/infosec gate', flags.it_infosec_gate ?? flags.it_infosec_gate_flag ?? '—'));
  }

  // Workflows summary
  const workflows = payload.workflows || [];
  if (workflows.length) {
    lines.push(sectionLabel('AI Workflows', palette));
    workflows.forEach((wf) => {
      const mmi = wf.mmi_hypothesis || {};
      const score = mmi.score ?? wf.design_doc?.mmi_justification?.overall_score;
      const complexity = wf.design_doc?.implementation_considerations?.estimated_complexity || '';
      const cColor = complexity === 'low' ? palette.success : complexity === 'high' ? palette.danger : palette.warning;
      lines.push(`<div style="margin:10px 0;padding:12px;border:1px solid ${palette.border};border-radius:8px;">`);
      lines.push(`<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">`);
      lines.push(`<p style="font-size:13px;font-weight:600;color:${palette.text};margin:0;flex:1;">${escapeHtml(wf.workflow_name || 'Unknown workflow')}</p>`);
      if (score != null) lines.push(`<span style="font-size:10px;padding:2px 8px;border-radius:10px;background:${palette.accent}1a;color:${palette.accent};font-weight:600;">MMI ${escapeHtml(String(score))}</span>`);
      if (complexity) lines.push(`<span style="font-size:10px;padding:2px 8px;border-radius:10px;background:${cColor}1a;color:${cColor};">${escapeHtml(complexity)}</span>`);
      lines.push(`</div>`);
      if (wf.design_doc?.executive_summary) lines.push(`<p style="font-size:11px;color:${palette.textSecondary};margin:0;line-height:1.6;">${escapeHtml(wf.design_doc.executive_summary)}</p>`);
      lines.push(`</div>`);
    });
  }

  // Apps summary
  const apps = payload.apps || [];
  if (apps.length) {
    lines.push(sectionLabel('Apps', palette));
    apps.forEach((app) => {
      const cColor = app.complexity === 'low' ? palette.success : app.complexity === 'high' ? palette.danger : palette.warning;
      lines.push(`<div style="margin:10px 0;padding:12px;border:1px solid ${palette.border};border-radius:8px;">`);
      lines.push(`<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">`);
      lines.push(`<p style="font-size:13px;font-weight:600;color:${palette.text};margin:0;flex:1;">${escapeHtml(app.app_name || 'Unknown app')}</p>`);
      if (app.complexity) lines.push(`<span style="font-size:10px;padding:2px 8px;border-radius:10px;background:${cColor}1a;color:${cColor};">${escapeHtml(app.complexity)}</span>`);
      lines.push(`</div>`);
      if (app.app_pitch_summary) lines.push(`<p style="font-size:11px;color:${palette.textSecondary};margin:0;line-height:1.6;">${escapeHtml(app.app_pitch_summary)}</p>`);
      lines.push(`</div>`);
    });
  }

  // Open items
  const openItems = payload.open_items || [];
  if (openItems.length) {
    lines.push(sectionLabel('Open Items', palette));
    openItems.forEach((oi) => {
      const flagColor = oi.flag === 'BLOCKING' ? palette.danger : oi.flag === 'NEEDS_TECHNICAL_FOLLOWUP' ? palette.warning : palette.info;
      lines.push(`<p style="font-size:11px;color:${palette.textSecondary};margin:6px 0;"><span style="font-size:9px;padding:1px 6px;border-radius:4px;background:${flagColor}1a;color:${flagColor};font-weight:600;margin-right:6px;">${escapeHtml(oi.flag || '')}</span><span style="color:${palette.text};font-weight:600;">${escapeHtml(oi.label || '')}</span>${oi.location ? ` <span style="color:${palette.textMuted};">(${escapeHtml(oi.location)})</span>` : ''}${oi.detail ? `<br/><span style="color:${palette.textMuted};margin-left:12px;">${escapeHtml(oi.detail)}</span>` : ''}</p>`);
    });
  }

  const filename = `vega_discovery_${slug(client, 'client')}.pdf`;
  await renderHtmlToPdf({ html: lines.join(''), filename, palette });
}
