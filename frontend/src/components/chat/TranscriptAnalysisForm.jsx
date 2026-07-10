import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Plus, Trash2, CheckCircle, ChevronDown, Download } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '../../lib/api';
import API_URL from '../../lib/apiConfig';
import { getPdfPalette } from '../../lib/pdfPalette';
import BpmnViewer from './BpmnViewer';

function escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function exportCallAnalysisPdf({ analysis, callNumber, opportunityName, clientPart, datePart }) {
  const html2canvas = (await import('html2canvas')).default;
  const { jsPDF } = await import('jspdf');
  const p = getPdfPalette();

  const container = document.createElement('div');
  container.style.cssText =
    `position:fixed;left:-9999px;top:0;width:860px;background:${p.bg};color:${p.text};font-family:sans-serif;padding:36px;box-sizing:border-box;`;

  const section = (title) =>
    `<p style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:${p.textMuted};margin:24px 0 10px;">${title}</p>`;

  const lines = [];

  lines.push(`<h1 style="font-size:18px;font-weight:700;margin:0 0 6px;color:${p.text};">Call ${callNumber} Analysis</h1>`);
  if (opportunityName) {
    lines.push(`<p style="font-size:11px;color:${p.textMuted};margin:0 0 24px;">${escapeHtml(opportunityName)} &mdash; ${escapeHtml(datePart)}</p>`);
  } else {
    lines.push(`<p style="font-size:11px;color:${p.textMuted};margin:0 0 24px;">${escapeHtml(datePart)}</p>`);
  }

  if (analysis.summary) {
    lines.push(section('Summary'));
    lines.push(`<p style="font-size:12px;line-height:1.7;color:${p.textSecondary};margin:0;">${escapeHtml(analysis.summary)}</p>`);
  }

  if (analysis.industry_context) {
    lines.push(section('Industry Context'));
    lines.push(`<p style="font-size:12px;line-height:1.7;color:${p.textSecondary};margin:0;">${escapeHtml(analysis.industry_context)}</p>`);
  }

  if (analysis.data_state_assessment || analysis.data_state_justification) {
    lines.push(section('Data State Assessment'));
    if (analysis.data_state_assessment) {
      lines.push(`<p style="font-size:12px;color:${p.text};margin:0 0 6px;"><span style="color:${p.accent};font-weight:600;">${escapeHtml(analysis.data_state_assessment)}</span></p>`);
    }
    if (analysis.data_state_justification) {
      lines.push(`<p style="font-size:11px;line-height:1.7;color:${p.textSecondary};margin:0;">${escapeHtml(analysis.data_state_justification)}</p>`);
    }
  }

  if (analysis.identified_pain_points?.length) {
    lines.push(section('Identified Pain Points'));
    analysis.identified_pain_points.forEach((pp) => {
      if (typeof pp === 'string') {
        lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:6px 0;">- ${escapeHtml(pp)}</p>`);
      } else {
        const sev = (pp.severity || '').toLowerCase();
        const sevColor = sev === 'high' ? p.danger : sev === 'medium' ? p.warning : p.success;
        lines.push(`<div style="border-left:3px solid ${sevColor};padding:6px 0 6px 12px;margin:10px 0;">`);
        if (pp.severity) lines.push(`<p style="font-size:10px;font-weight:700;text-transform:uppercase;color:${sevColor};margin:0 0 4px;">${escapeHtml(pp.severity)}</p>`);
        lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.text};margin:0 0 4px;">${escapeHtml(pp.description || '')}</p>`);
        if (pp.business_impact) lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textMuted};margin:4px 0;">Impact: ${escapeHtml(pp.business_impact)}</p>`);
        if (pp.direct_quote) lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textMuted};font-style:italic;margin:4px 0 0;">"${escapeHtml(pp.direct_quote)}"</p>`);
        lines.push(`</div>`);
      }
    });
  }

  if (analysis.stakeholders_mentioned?.length) {
    lines.push(section('Stakeholders Mentioned'));
    analysis.stakeholders_mentioned.forEach((sm) => {
      lines.push(`<p style="font-size:12px;color:${p.text};margin:8px 0 2px;"><span style="font-weight:600;">${escapeHtml(sm.name || '')}</span><span style="color:${p.textMuted};font-weight:400;"> (${escapeHtml(sm.role || '')})</span></p>`);
      if (sm.concerns) lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:2px 0 8px;">${escapeHtml(sm.concerns)}</p>`);
    });
  }

  if (analysis.stakeholder_strategies?.length) {
    lines.push(section('Stakeholder Selling Strategies'));
    analysis.stakeholder_strategies.forEach((ss) => {
      lines.push(`<div style="border:1px solid ${p.border};border-radius:8px;padding:14px;margin-bottom:10px;">`);
      lines.push(`<p style="font-size:12px;color:${p.text};margin:0 0 6px;"><span style="font-weight:600;">${escapeHtml(ss.stakeholder_name || '')}</span><span style="color:${p.textMuted};font-weight:400;"> (${escapeHtml(ss.role || '')})</span>${ss.buyer_type ? ` <span style="font-size:10px;padding:2px 8px;border-radius:10px;border:1px solid ${p.accent}55;color:${p.accent};margin-left:6px;">${escapeHtml(ss.buyer_type)}</span>` : ''}</p>`);
      if (ss.pain_summary) lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:4px 0;"><span style="color:${p.textMuted};font-weight:600;">Pain:</span> ${escapeHtml(ss.pain_summary)}</p>`);
      if (ss.selling_strategy) lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:4px 0;"><span style="color:${p.textMuted};font-weight:600;">Strategy:</span> ${escapeHtml(ss.selling_strategy)}</p>`);
      if (ss.hook_question) lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.accent};font-style:italic;margin:4px 0;border-left:2px solid ${p.accent}80;padding-left:10px;">Hook: "${escapeHtml(ss.hook_question)}"</p>`);
      if (ss.tactical_advice) lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:4px 0;"><span style="color:${p.textMuted};font-weight:600;">Tactical:</span> ${escapeHtml(ss.tactical_advice)}</p>`);
      lines.push(`</div>`);
    });
  }

  if (analysis.current_tech_stack?.length) {
    lines.push(section('Current Technology Stack'));
    analysis.current_tech_stack.forEach((t) => {
      lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:4px 0;">- ${escapeHtml(t)}</p>`);
    });
  }

  if (analysis.suggested_tools?.length) {
    lines.push(section('Suggested / Proposed Tools'));
    analysis.suggested_tools.forEach((t) => {
      lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:4px 0;">- ${escapeHtml(t)}</p>`);
    });
  }

  if (analysis.data_sources_inventory?.length) {
    lines.push(section('Data Sources Inventory'));
    analysis.data_sources_inventory.forEach((ds) => {
      lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:4px 0;"><span style="color:${p.text};font-weight:600;">${escapeHtml(ds.source_name || '')}</span>${ds.type ? ` <span style="color:${p.textMuted};">[${escapeHtml(ds.type)}]</span>` : ''}${ds.accessibility ? ` — ${escapeHtml(ds.accessibility)}` : ''}</p>`);
    });
  }

  if (analysis.ai_readiness_signals?.length) {
    lines.push(section('AI Readiness Signals'));
    analysis.ai_readiness_signals.forEach((sig) => {
      const indColor = sig.indicator === 'positive' ? p.success : sig.indicator === 'negative' ? p.danger : p.textMuted;
      lines.push(`<div style="margin:8px 0;">`);
      lines.push(`<p style="font-size:11px;color:${p.text};margin:0 0 2px;"><span style="font-size:10px;padding:2px 8px;border-radius:10px;background:${indColor}1a;color:${indColor};margin-right:6px;">${escapeHtml(sig.indicator || '')}</span>${escapeHtml(sig.signal || '')}</p>`);
      if (sig.evidence) lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textMuted};font-style:italic;margin:2px 0 0;border-left:2px solid ${p.border};padding-left:10px;">${escapeHtml(sig.evidence)}</p>`);
      lines.push(`</div>`);
    });
  }

  if (analysis.missed_questions?.length) {
    lines.push(section('Missed Questions'));
    analysis.missed_questions.forEach((q) => {
      lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:4px 0;">- ${escapeHtml(q)}</p>`);
    });
  }

  if (analysis.potential_workflows_spotted?.length) {
    lines.push(section('Potential Workflows Spotted'));
    analysis.potential_workflows_spotted.forEach((w) => {
      lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:4px 0;">- ${escapeHtml(w)}</p>`);
    });
  }

  // The "next call" sections only make sense up through Call 2 (Call 3 is terminal).
  const nextCallNumber = callNumber < 3 ? callNumber + 1 : null;

  if (nextCallNumber && analysis.workflow_investigation_guides?.length) {
    const subList = (label, color, items) => {
      if (!items?.length) return '';
      return `<div style="margin:8px 0;"><p style="font-size:11px;font-weight:600;color:${color};margin:0 0 4px;">${label}</p><ul style="margin:0;padding-left:0;list-style:none;">${items.map((q) => `<li style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:3px 0;border-left:2px solid ${color}55;padding-left:10px;">${escapeHtml(q)}</li>`).join('')}</ul></div>`;
    };
    lines.push(section(`Workflow Investigation Guides (for Call ${nextCallNumber})`));
    analysis.workflow_investigation_guides.forEach((guide) => {
      lines.push(`<div style="border:1px solid ${p.border};border-radius:8px;padding:14px;margin-bottom:12px;">`);
      lines.push(`<p style="font-size:12px;font-weight:600;color:${p.text};margin:0 0 8px;">${escapeHtml(guide.workflow_name || '')}</p>`);
      lines.push(subList('Walkthrough Questions', p.success, guide.walkthrough_questions));
      lines.push(subList('Decision & Exception Probes', p.warning, guide.decision_probes));
      lines.push(subList('Technical / Security Checks', p.info, guide.technical_checks));
      lines.push(subList('MMI Quantification Questions', p.accent, guide.mmi_quantification));
      if (guide.pro_tip) lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.accent};margin:8px 0 0;"><span style="font-weight:600;">Pro Tip:</span> ${escapeHtml(guide.pro_tip)}</p>`);
      lines.push(`</div>`);
    });
  }

  // Field is named call_${nextCall}_approach_summary, e.g. call_2_approach_summary on
  // Call 1 output, call_3_approach_summary on Call 2 output. Use the dynamic key so
  // both forms are picked up.
  const approachField = nextCallNumber ? `call_${nextCallNumber}_approach_summary` : null;
  if (approachField && analysis[approachField]) {
    lines.push(section(`Call ${nextCallNumber} Approach Summary`));
    lines.push(`<p style="font-size:12px;line-height:1.7;color:${p.textSecondary};white-space:pre-wrap;margin:0;">${escapeHtml(analysis[approachField])}</p>`);
  }

  if (analysis.next_call_agenda?.length) {
    lines.push(section('Next Call Agenda'));
    analysis.next_call_agenda.forEach((item, i) => {
      lines.push(`<p style="font-size:11px;line-height:1.6;color:${p.textSecondary};margin:4px 0;">${i + 1}. ${escapeHtml(item)}</p>`);
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

    pdf.save(`${clientPart}-call_${callNumber}_analysis-${datePart}.pdf`);
  } finally {
    document.body.removeChild(container);
  }
}

// Auto-resizing textarea hook
function useAutoResize(value) {
  const ref = useRef(null);
  const resize = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = el.scrollHeight + 'px';
  }, []);
  useEffect(() => { resize(); }, [value, resize]);
  return { ref, onInput: resize };
}

function AutoTextarea({ value, onChange, disabled, placeholder, className, minRows = 2 }) {
  const { ref, onInput } = useAutoResize(value);
  return (
    <textarea
      ref={ref}
      rows={minRows}
      value={value}
      onChange={onChange}
      onInput={onInput}
      disabled={disabled}
      placeholder={placeholder}
      className={className}
      style={{ overflow: 'hidden' }}
    />
  );
}

const DATA_STATE_OPTIONS = [
  { value: 'blind', label: 'Blind — no visibility into data' },
  { value: 'siloed', label: 'Siloed — data exists but is fragmented' },
  { value: 'visible', label: 'Visible — data is accessible and usable' },
];

function ListEditor({ items, onChange, placeholder, disabled }) {
  const handleChange = (idx, val) => {
    const next = [...items];
    next[idx] = val;
    onChange(next);
  };

  const handleAdd = () => onChange([...items, '']);

  const handleRemove = (idx) => onChange(items.filter((_, i) => i !== idx));

  return (
    <div className="space-y-2">
      {items.map((item, idx) => (
        <div key={idx} className="flex items-start gap-2">
          <AutoTextarea
            minRows={2}
            value={item}
            onChange={e => handleChange(idx, e.target.value)}
            disabled={disabled}
            placeholder={placeholder}
            className="flex-1 bg-aegisaxiom-surface border border-aegisaxiom-border focus:border-violet-500 rounded-lg px-3 py-2 text-sm text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted outline-none transition-colors resize-none disabled:opacity-60 disabled:cursor-default"
          />
          {!disabled && (
            <button
              type="button"
              onClick={() => handleRemove(idx)}
              className="mt-1.5 text-aegisaxiom-text-muted hover:text-red-600 dark:text-red-400 transition-colors"
              aria-label="Remove item"
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      ))}
      {!disabled && (
        <button
          type="button"
          onClick={handleAdd}
          className="flex items-center gap-1.5 text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 transition-colors"
        >
          <Plus size={13} />
          Add item
        </button>
      )}
    </div>
  );
}

function StakeholderEditor({ stakeholders, onChange, disabled }) {
  const handleFieldChange = (idx, field, val) => {
    const next = stakeholders.map((s, i) => i === idx ? { ...s, [field]: val } : s);
    onChange(next);
  };

  const handleAdd = () => onChange([...stakeholders, { name: '', role: '', concerns: '' }]);

  const handleRemove = (idx) => onChange(stakeholders.filter((_, i) => i !== idx));

  return (
    <div className="space-y-3">
      {stakeholders.map((s, idx) => (
        <div key={idx} className="rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface p-3 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <input
              type="text"
              value={s.name}
              onChange={e => handleFieldChange(idx, 'name', e.target.value)}
              disabled={disabled}
              placeholder="Name"
              className="flex-1 bg-transparent border-b border-aegisaxiom-border focus:border-violet-500 px-0 py-1 text-sm text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted outline-none transition-colors disabled:opacity-60"
            />
            {!disabled && (
              <button
                type="button"
                onClick={() => handleRemove(idx)}
                className="text-aegisaxiom-text-muted hover:text-red-600 dark:text-red-400 transition-colors"
                aria-label="Remove stakeholder"
              >
                <Trash2 size={14} />
              </button>
            )}
          </div>
          <input
            type="text"
            value={s.role}
            onChange={e => handleFieldChange(idx, 'role', e.target.value)}
            disabled={disabled}
            placeholder="Role / Title"
            className="w-full bg-transparent border-b border-aegisaxiom-border focus:border-violet-500 px-0 py-1 text-sm text-aegisaxiom-text-secondary placeholder:text-aegisaxiom-text-muted outline-none transition-colors disabled:opacity-60"
          />
          <AutoTextarea
            minRows={2}
            value={s.concerns}
            onChange={e => handleFieldChange(idx, 'concerns', e.target.value)}
            disabled={disabled}
            placeholder="Concerns / notes"
            className="w-full bg-aegisaxiom-surface border border-aegisaxiom-border focus:border-violet-500 rounded-lg px-3 py-2 text-sm text-aegisaxiom-text-secondary placeholder:text-aegisaxiom-text-muted outline-none transition-colors resize-none disabled:opacity-60"
          />
        </div>
      ))}
      {!disabled && (
        <button
          type="button"
          onClick={handleAdd}
          className="flex items-center gap-1.5 text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 transition-colors"
        >
          <Plus size={13} />
          Add stakeholder
        </button>
      )}
    </div>
  );
}

function SectionLabel({ children }) {
  return (
    <p className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted mb-2">{children}</p>
  );
}

function PainPointEditor({ painPoints, onChange, disabled }) {
  const items = painPoints.map(p =>
    typeof p === 'string' ? { description: p, severity: 'medium', frequency: 'unknown', business_impact: '', direct_quote: '' } : p
  );

  const handleFieldChange = (idx, field, val) => {
    const next = items.map((item, i) => i === idx ? { ...item, [field]: val } : item);
    onChange(next);
  };

  const handleAdd = () => onChange([...items, { description: '', severity: 'medium', frequency: 'unknown', business_impact: '', direct_quote: '' }]);
  const handleRemove = (idx) => onChange(items.filter((_, i) => i !== idx));

  const severityColors = {
    high: 'text-red-600 dark:text-red-400 bg-red-400/10 border-red-400/30',
    medium: 'text-yellow-600 dark:text-yellow-400 bg-yellow-400/10 border-yellow-400/30',
    low: 'text-emerald-600 dark:text-emerald-400 bg-emerald-400/10 border-emerald-400/30',
  };

  return (
    <div className="space-y-3">
      {items.map((item, idx) => (
        <div key={idx} className="rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface p-3 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <AutoTextarea
              minRows={2}
              value={item.description || ''}
              onChange={e => handleFieldChange(idx, 'description', e.target.value)}
              disabled={disabled}
              placeholder="Describe the pain point..."
              className="flex-1 bg-transparent border-b border-aegisaxiom-border focus:border-violet-500 px-0 py-1 text-sm text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted outline-none transition-colors resize-none disabled:opacity-60"
            />
            {!disabled && (
              <button type="button" onClick={() => handleRemove(idx)} className="text-aegisaxiom-text-muted hover:text-red-600 dark:text-red-400 transition-colors">
                <Trash2 size={14} />
              </button>
            )}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <select
              value={item.severity || 'medium'}
              onChange={e => handleFieldChange(idx, 'severity', e.target.value)}
              disabled={disabled}
              className={`text-xs px-2 py-0.5 rounded-full border appearance-none cursor-pointer ${severityColors[item.severity] || severityColors.medium} disabled:opacity-60 bg-transparent`}
            >
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
            <select
              value={item.frequency || 'unknown'}
              onChange={e => handleFieldChange(idx, 'frequency', e.target.value)}
              disabled={disabled}
              className="text-xs px-2 py-1 rounded-md bg-aegisaxiom-elevated border border-aegisaxiom-border text-aegisaxiom-text-secondary disabled:opacity-60"
            >
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
              <option value="monthly">Monthly</option>
              <option value="rare">Rare</option>
              <option value="unknown">Unknown</option>
            </select>
          </div>
          {(item.business_impact || !disabled) && (
            <input
              type="text"
              value={item.business_impact || ''}
              onChange={e => handleFieldChange(idx, 'business_impact', e.target.value)}
              disabled={disabled}
              placeholder="Business impact..."
              className="w-full bg-transparent border-b border-aegisaxiom-border focus:border-violet-500 px-0 py-1 text-xs text-aegisaxiom-text-secondary placeholder:text-aegisaxiom-text-muted outline-none transition-colors disabled:opacity-60"
            />
          )}
          {(item.direct_quote || !disabled) && (
            <div className="pl-3 border-l-2 border-violet-500/30">
              <input
                type="text"
                value={item.direct_quote || ''}
                onChange={e => handleFieldChange(idx, 'direct_quote', e.target.value)}
                disabled={disabled}
                placeholder="Direct quote from transcript..."
                className="w-full bg-transparent px-0 py-1 text-xs text-aegisaxiom-text-muted italic placeholder:text-aegisaxiom-text-muted outline-none transition-colors disabled:opacity-60"
              />
            </div>
          )}
        </div>
      ))}
      {!disabled && (
        <button type="button" onClick={handleAdd} className="flex items-center gap-1.5 text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 transition-colors">
          <Plus size={13} /> Add pain point
        </button>
      )}
    </div>
  );
}

export default function TranscriptAnalysisForm({ artifact }) {
  const { artifact_id, payload, status } = artifact;
  // Extract call_number — LLM may omit it; infer from intake context or default to 1
  const call_number = payload.call_number || payload.call || 1;
  const opportunity_name = payload.opportunity_name || payload.client_name || payload.opportunity_id || '';
  // Use Vega session ID if available, fall back to opportunity_id or generate from artifact
  const sessionId = payload.session_id || payload.opportunity_id
    || `vega_${artifact.session_id}`;

  const isApproved = status === 'submitted' || status === 'graded';

  // Structured output guarantees consistent field names.
  // Check payload.analysis first (new structured path), fall back to top-level (legacy).
  const normalizeAnalysis = () => {
    if (payload.analysis && typeof payload.analysis === 'object' && Object.keys(payload.analysis).length > 0) {
      return payload.analysis;
    }
    // Legacy fallback: try to find fields at top level for old artifacts
    const fields = {};
    const tryField = (canonical, ...aliases) => {
      for (const a of [canonical, ...aliases]) {
        if (payload[a] !== undefined) { fields[canonical] = payload[a]; return; }
      }
    };
    tryField('summary', 'call_summary', 'overall_summary');
    tryField('data_state_assessment', 'data_state', 'data_maturity');
    tryField('data_state_justification', 'data_state_reasoning', 'justification');
    tryField('identified_pain_points', 'pain_points', 'pains');
    tryField('stakeholders_mentioned', 'stakeholders_and_concerns', 'stakeholders');
    tryField('missed_questions', 'gaps', 'missed');
    tryField('potential_workflows_spotted', 'potential_workflows', 'workflows');
    tryField('next_call_agenda', 'call_2_agenda', 'call_3_agenda', 'agenda');
    tryField('industry_context');
    tryField('current_tech_stack', 'tech_stack', 'systems');
    tryField('ai_readiness_signals');
    tryField('data_sources_inventory', 'data_sources');
    tryField('suggested_tools');
    tryField('stakeholder_strategies');
    tryField('workflow_investigation_guides');
    tryField('call_2_approach_summary');
    return Object.keys(fields).length > 0 ? fields : {};
  };

  const [analysis, setAnalysis] = useState(normalizeAnalysis);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [approved, setApproved] = useState(isApproved);
  const [generatingBpmns, setGeneratingBpmns] = useState(false);
  const [bpmnIds, setBpmnIds] = useState([]);
  const [bpmnViewId, setBpmnViewId] = useState(null);
  const [exportingGdoc, setExportingGdoc] = useState(false);
  const [gdocError, setGdocError] = useState(null);
  const [exportingPdf, setExportingPdf] = useState(false);

  const setField = (field, val) => setAnalysis(prev => ({ ...prev, [field]: val }));

  const handleApprove = async () => {
    setSubmitting(true);
    setSubmitError(null);

    try {
      const res = await api.post(
        `${API_URL}/api/vega-sessions/${sessionId}/calls/${call_number}/approve`,
        { ...analysis, chat_session_id: artifact.session_id },
      );
      if (res.ok) {
        setApproved(true);
        // Notify parent to refresh messages — backend may have added a follow-up
        window.dispatchEvent(new CustomEvent('artifact-submitted', { detail: { artifact_id } }));
      } else {
        const data = await res.json();
        setSubmitError(data?.detail || 'Failed to save. Please try again.');
      }
    } catch {
      setSubmitError('Connection error. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  const disabled = approved;

  return (
    <div className="my-3 rounded-xl border border-violet-500/30 bg-aegisaxiom-elevated overflow-hidden min-w-[560px] w-[640px] max-w-full">
      <div className="px-5 py-4 border-b border-aegisaxiom-border flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-aegisaxiom-text">
            Call {call_number} Analysis
          </h3>
          {opportunity_name && (
            <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">{opportunity_name}</p>
          )}
        </div>
        <div className="flex items-center gap-3">
          {approved && (
            <button
              type="button"
              disabled={exportingGdoc}
              onClick={async () => {
                setExportingGdoc(true);
                setGdocError(null);
                try {
                  const res = await api.post(`${API_URL}/api/vega-sessions/${sessionId}/export/google-doc`, {});
                  if (res.ok) {
                    const data = await res.json();
                    if (data.doc_url) window.open(data.doc_url, '_blank');
                  } else {
                    const data = await res.json();
                    const msg = data?.detail || 'Google Doc export failed';
                    setGdocError(msg);
                    toast.error(msg);
                  }
                } catch (e) {
                  const msg = 'Connection error. Please try again.';
                  setGdocError(msg);
                  toast.error(msg);
                }
                setExportingGdoc(false);
              }}
              className="flex items-center gap-1.5 text-xs text-aegisaxiom-text-secondary hover:text-violet-700 dark:text-violet-300 transition-colors disabled:opacity-50"
              title="Export to Google Doc"
            >
              {exportingGdoc ? (
                <>
                  <div className="w-3 h-3 border-2 border-aegisaxiom-border-hover/30 border-t-violet-400 rounded-full animate-spin" />
                  Exporting...
                </>
              ) : 'Google Doc'}
            </button>
          )}
          {approved && (
            <button
              type="button"
              onClick={() => {
                const lines = [`# Call ${call_number} Analysis${opportunity_name ? ` — ${opportunity_name}` : ''}\n`];
                if (analysis.summary) lines.push(`## Summary\n${analysis.summary}\n`);
                if (analysis.industry_context) lines.push(`## Industry Context\n${analysis.industry_context}\n`);
                if (analysis.data_state_assessment) lines.push(`## Data State: ${analysis.data_state_assessment}\n${analysis.data_state_justification || ''}\n`);
                if (analysis.current_tech_stack?.length) lines.push(`## Technology Stack\n${analysis.current_tech_stack.map(t => `- ${t}`).join('\n')}\n`);
                if (analysis.identified_pain_points?.length) {
                  lines.push(`## Pain Points`);
                  analysis.identified_pain_points.forEach(p => {
                    if (typeof p === 'string') {
                      lines.push(`- ${p}`);
                    } else {
                      lines.push(`- **[${p.severity?.toUpperCase() || '?'}]** ${p.description}${p.business_impact ? ` — Impact: ${p.business_impact}` : ''}${p.direct_quote ? `\n  > "${p.direct_quote}"` : ''}`);
                    }
                  });
                  lines.push('');
                }
                if (analysis.stakeholders_mentioned?.length) lines.push(`## Stakeholders\n${analysis.stakeholders_mentioned.map(s => `- **${s.name}** (${s.role}): ${s.concerns}`).join('\n')}\n`);
                if (analysis.stakeholder_strategies?.length) {
                  lines.push(`## Stakeholder Selling Strategies`);
                  analysis.stakeholder_strategies.forEach(ss => {
                    lines.push(`\n### ${ss.stakeholder_name} (${ss.role})${ss.buyer_type ? ` — ${ss.buyer_type}` : ''}`);
                    if (ss.pain_summary) lines.push(`**Pain:** ${ss.pain_summary}`);
                    if (ss.selling_strategy) lines.push(`**Strategy:** ${ss.selling_strategy}`);
                    if (ss.hook_question) lines.push(`> Hook: "${ss.hook_question}"`);
                    if (ss.tactical_advice) lines.push(`**Tactical:** ${ss.tactical_advice}`);
                  });
                  lines.push('');
                }
                if (analysis.suggested_tools?.length) lines.push(`## Suggested Tools\n${analysis.suggested_tools.map(t => `- ${t}`).join('\n')}\n`);
                if (analysis.call_2_approach_summary) lines.push(`## Call 2 Approach Summary\n${analysis.call_2_approach_summary}\n`);
                if (analysis.missed_questions?.length) lines.push(`## Missed Questions\n${analysis.missed_questions.map(q => `- ${q}`).join('\n')}\n`);
                if (analysis.potential_workflows_spotted?.length) lines.push(`## Potential Workflows\n${analysis.potential_workflows_spotted.map(w => `- ${w}`).join('\n')}\n`);
                if (analysis.next_call_agenda?.length) lines.push(`## Next Call Agenda\n${analysis.next_call_agenda.map(a => `- ${a}`).join('\n')}\n`);
                const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                const datePart = new Date().toISOString().slice(0, 10);
                const clientPart = (analysis.client_name || payload?.analysis?.client_name || opportunity_name || 'unknown').replace(/[^a-zA-Z0-9]/g, '_');
                a.download = `${clientPart}-call_${call_number}_analysis-${datePart}.md`;
                a.click();
                URL.revokeObjectURL(url);
              }}
              className="flex items-center gap-1.5 text-xs text-aegisaxiom-text-secondary hover:text-violet-700 dark:text-violet-300 transition-colors"
              title="Download as Markdown"
            >
              <Download size={13} />
              Export
            </button>
          )}
          {approved && (
            <button
              type="button"
              disabled={exportingPdf}
              onClick={async () => {
                setExportingPdf(true);
                try {
                  const datePart = new Date().toISOString().slice(0, 10);
                  const clientPart = (analysis.client_name || opportunity_name || 'unknown').replace(/[^a-zA-Z0-9]/g, '_');
                  await exportCallAnalysisPdf({
                    analysis,
                    callNumber: call_number,
                    opportunityName: opportunity_name,
                    clientPart,
                    datePart,
                  });
                } catch (e) {
                  console.error('PDF export failed:', e);
                  toast.error('PDF generation failed. Please try again.');
                } finally {
                  setExportingPdf(false);
                }
              }}
              className="flex items-center gap-1.5 text-xs text-aegisaxiom-text-secondary hover:text-violet-700 dark:text-violet-300 transition-colors disabled:opacity-50"
              title="Download as PDF"
            >
              {exportingPdf ? (
                <>
                  <div className="w-3 h-3 border-2 border-aegisaxiom-border-hover/30 border-t-violet-400 rounded-full animate-spin" />
                  PDF...
                </>
              ) : 'PDF'}
            </button>
          )}
          {approved && (
            <span className="flex items-center gap-1.5 text-xs text-emerald-600 dark:text-emerald-400 font-medium">
              <CheckCircle size={14} />
              Approved
            </span>
          )}
        </div>
        {gdocError && (
          <div className="px-5 pb-0 -mt-2">
            <p className="text-xs text-red-600 dark:text-red-400">{gdocError}</p>
          </div>
        )}
      </div>

      <div id={`analysis-content-${artifact_id}`} className="px-5 py-5 space-y-6">
        {/* Summary */}
        <div>
          <SectionLabel>Summary</SectionLabel>
          <AutoTextarea
            minRows={3}
            value={analysis.summary || ''}
            onChange={e => setField('summary', e.target.value)}
            disabled={disabled}
            placeholder="Overall call summary…"
            className="w-full bg-aegisaxiom-surface border border-aegisaxiom-border focus:border-violet-500 rounded-xl px-4 py-3 text-sm text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted outline-none transition-colors resize-none disabled:opacity-60 disabled:cursor-default"
          />
        </div>

        {/* Data State */}
        <div>
          <SectionLabel>Data State Assessment</SectionLabel>
          <div className="relative">
            <select
              value={analysis.data_state_assessment || ''}
              onChange={e => setField('data_state_assessment', e.target.value)}
              disabled={disabled}
              className="w-full appearance-none bg-aegisaxiom-surface border border-aegisaxiom-border focus:border-violet-500 rounded-xl px-4 py-3 pr-9 text-sm text-aegisaxiom-text outline-none transition-colors disabled:opacity-60 disabled:cursor-default"
            >
              <option value="">Select data state…</option>
              {DATA_STATE_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            <ChevronDown size={15} className="absolute right-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted pointer-events-none" />
          </div>
          <AutoTextarea
            minRows={2}
            value={analysis.data_state_justification || ''}
            onChange={e => setField('data_state_justification', e.target.value)}
            disabled={disabled}
            placeholder="Justification for this assessment…"
            className="mt-2 w-full bg-aegisaxiom-surface border border-aegisaxiom-border focus:border-violet-500 rounded-xl px-4 py-3 text-sm text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted outline-none transition-colors resize-none disabled:opacity-60 disabled:cursor-default"
          />
        </div>

        {/* Industry Context */}
        <div>
          <SectionLabel>Industry Context</SectionLabel>
          <AutoTextarea
            minRows={2}
            value={analysis.industry_context || ''}
            onChange={e => setField('industry_context', e.target.value)}
            disabled={disabled}
            placeholder="Client's industry, market context, and business model..."
            className="w-full bg-aegisaxiom-surface border border-aegisaxiom-border focus:border-violet-500 rounded-xl px-4 py-3 text-sm text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted outline-none transition-colors resize-none disabled:opacity-60 disabled:cursor-default"
          />
        </div>

        {/* Pain Points */}
        <div>
          <SectionLabel>Identified Pain Points</SectionLabel>
          <PainPointEditor
            painPoints={analysis.identified_pain_points || []}
            onChange={val => setField('identified_pain_points', val)}
            disabled={disabled}
          />
        </div>

        {/* Stakeholders */}
        <div>
          <SectionLabel>Stakeholders Mentioned</SectionLabel>
          <StakeholderEditor
            stakeholders={analysis.stakeholders_mentioned || []}
            onChange={val => setField('stakeholders_mentioned', val)}
            disabled={disabled}
          />
        </div>

        {/* Stakeholder Selling Strategies */}
        {analysis.stakeholder_strategies?.length > 0 && (
          <div>
            <SectionLabel>Stakeholder Selling Strategies</SectionLabel>
            <div className="space-y-3">
              {analysis.stakeholder_strategies.map((ss, idx) => (
                <div key={idx} className="rounded-lg border border-violet-500/20 bg-violet-500/5 p-4 space-y-2">
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
                    <p className="text-xs text-aegisaxiom-text-secondary"><span className="text-aegisaxiom-text-muted font-medium">Pain:</span> {ss.pain_summary}</p>
                  )}
                  {ss.selling_strategy && (
                    <p className="text-xs text-aegisaxiom-text-secondary"><span className="text-aegisaxiom-text-muted font-medium">Strategy:</span> {ss.selling_strategy}</p>
                  )}
                  {ss.hook_question && (
                    <div className="pl-3 border-l-2 border-violet-500/40">
                      <p className="text-xs text-violet-700 dark:text-violet-300 italic">Hook: "{ss.hook_question}"</p>
                    </div>
                  )}
                  {ss.tactical_advice && (
                    <p className="text-xs text-aegisaxiom-text-secondary"><span className="text-aegisaxiom-text-muted font-medium">Tactical:</span> {ss.tactical_advice}</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Tech Stack */}
        <div>
          <SectionLabel>Current Technology Stack</SectionLabel>
          <ListEditor
            items={analysis.current_tech_stack || []}
            onChange={val => setField('current_tech_stack', val)}
            placeholder="System or tool name..."
            disabled={disabled}
          />
        </div>

        {/* Suggested Tools */}
        <div>
          <SectionLabel>Suggested / Proposed Tools</SectionLabel>
          <ListEditor
            items={analysis.suggested_tools || []}
            onChange={val => setField('suggested_tools', val)}
            placeholder="Tool suggested during the call..."
            disabled={disabled}
          />
        </div>

        {/* Data Sources Inventory */}
        {(analysis.data_sources_inventory?.length > 0 || !disabled) && (
          <div>
            <SectionLabel>Data Sources Inventory</SectionLabel>
            <div className="space-y-2">
              {(analysis.data_sources_inventory || []).map((ds, idx) => (
                <div key={idx} className="flex items-center gap-2 text-sm">
                  <span className="text-aegisaxiom-text font-medium">{ds.source_name}</span>
                  <span className="text-xs px-2 py-0.5 rounded-md bg-aegisaxiom-elevated border border-aegisaxiom-border-hover/40 text-aegisaxiom-text-secondary">{ds.type}</span>
                  <span className="text-xs text-aegisaxiom-text-muted">{ds.accessibility}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* AI Readiness Signals */}
        {(analysis.ai_readiness_signals?.length > 0 || !disabled) && (
          <div>
            <SectionLabel>AI Readiness Signals</SectionLabel>
            <div className="space-y-2">
              {(analysis.ai_readiness_signals || []).map((sig, idx) => {
                const indicatorColors = {
                  positive: 'text-emerald-600 dark:text-emerald-400 bg-emerald-400/10',
                  negative: 'text-red-600 dark:text-red-400 bg-red-400/10',
                  neutral: 'text-aegisaxiom-text-secondary bg-aegisaxiom-accent-muted',
                };
                return (
                  <div key={idx} className="rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface p-3">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${indicatorColors[sig.indicator] || indicatorColors.neutral}`}>
                        {sig.indicator}
                      </span>
                      <span className="text-sm text-aegisaxiom-text">{sig.signal}</span>
                    </div>
                    {sig.evidence && (
                      <p className="text-xs text-aegisaxiom-text-muted italic pl-3 border-l-2 border-aegisaxiom-border">{sig.evidence}</p>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Missed Questions */}
        <div>
          <SectionLabel>Missed Questions</SectionLabel>
          <ListEditor
            items={analysis.missed_questions || []}
            onChange={val => setField('missed_questions', val)}
            placeholder="Question that should have been asked…"
            disabled={disabled}
          />
        </div>

        {/* Potential Workflows */}
        <div>
          <SectionLabel>Potential Workflows Spotted</SectionLabel>
          <ListEditor
            items={analysis.potential_workflows_spotted || []}
            onChange={val => setField('potential_workflows_spotted', val)}
            placeholder="Describe a potential workflow…"
            disabled={disabled}
          />
        </div>

        {/* Workflow Investigation Guides */}
        {analysis.workflow_investigation_guides?.length > 0 && (
          <div>
            <SectionLabel>Workflow Investigation Guides (for Call 2)</SectionLabel>
            <div className="space-y-3">
              {analysis.workflow_investigation_guides.map((guide, idx) => (
                <details key={idx} className="rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface overflow-hidden group">
                  <summary className="px-4 py-3 cursor-pointer flex items-center gap-2 hover:bg-aegisaxiom-accent-soft transition-colors">
                    <ChevronDown size={14} className="text-aegisaxiom-text-secondary group-open:rotate-180 transition-transform" />
                    <span className="text-sm font-medium text-aegisaxiom-text">{guide.workflow_name}</span>
                  </summary>
                  <div className="px-4 pb-4 space-y-3">
                    {guide.walkthrough_questions?.length > 0 && (
                      <div>
                        <p className="text-xs font-medium text-emerald-600 dark:text-emerald-400 mb-1">Walkthrough Questions</p>
                        <ul className="space-y-1">{guide.walkthrough_questions.map((q, i) => (
                          <li key={i} className="text-xs text-aegisaxiom-text-secondary pl-3 border-l border-emerald-500/30">{q}</li>
                        ))}</ul>
                      </div>
                    )}
                    {guide.decision_probes?.length > 0 && (
                      <div>
                        <p className="text-xs font-medium text-yellow-600 dark:text-yellow-400 mb-1">Decision & Exception Probes</p>
                        <ul className="space-y-1">{guide.decision_probes.map((q, i) => (
                          <li key={i} className="text-xs text-aegisaxiom-text-secondary pl-3 border-l border-yellow-500/30">{q}</li>
                        ))}</ul>
                      </div>
                    )}
                    {guide.technical_checks?.length > 0 && (
                      <div>
                        <p className="text-xs font-medium text-blue-600 dark:text-blue-400 mb-1">Technical / Security Checks</p>
                        <ul className="space-y-1">{guide.technical_checks.map((q, i) => (
                          <li key={i} className="text-xs text-aegisaxiom-text-secondary pl-3 border-l border-blue-500/30">{q}</li>
                        ))}</ul>
                      </div>
                    )}
                    {guide.mmi_quantification?.length > 0 && (
                      <div>
                        <p className="text-xs font-medium text-violet-600 dark:text-violet-400 mb-1">MMI Quantification Questions</p>
                        <ul className="space-y-1">{guide.mmi_quantification.map((q, i) => (
                          <li key={i} className="text-xs text-aegisaxiom-text-secondary pl-3 border-l border-violet-500/30">{q}</li>
                        ))}</ul>
                      </div>
                    )}
                    {guide.pro_tip && (
                      <div className="rounded-md bg-violet-50 dark:bg-violet-500/10 border border-violet-500/20 px-3 py-2">
                        <p className="text-xs text-violet-700 dark:text-violet-300"><span className="font-medium">Pro Tip:</span> {guide.pro_tip}</p>
                      </div>
                    )}
                  </div>
                </details>
              ))}
            </div>
          </div>
        )}

        {/* Call 2 Approach Summary */}
        {analysis.call_2_approach_summary && (
          <div>
            <SectionLabel>Call 2 Approach Summary</SectionLabel>
            <div className="rounded-lg border border-violet-500/20 bg-violet-500/5 px-4 py-3">
              <p className="text-sm text-aegisaxiom-text leading-relaxed whitespace-pre-wrap">{analysis.call_2_approach_summary}</p>
            </div>
          </div>
        )}

        {/* Next Call Agenda */}
        <div>
          <SectionLabel>Next Call Agenda</SectionLabel>
          <ListEditor
            items={analysis.next_call_agenda || []}
            onChange={val => setField('next_call_agenda', val)}
            placeholder="Agenda item…"
            disabled={disabled}
          />
        </div>
      </div>

      {!approved && (
        <div className="px-5 py-4 border-t border-aegisaxiom-border flex items-center justify-between gap-3">
          {submitError ? (
            <p className="text-xs text-red-600 dark:text-red-400">{submitError}</p>
          ) : (
            <p className="text-xs text-aegisaxiom-text-muted">Review the analysis above before approving.</p>
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
                Saving…
              </>
            ) : (
              <>
                <CheckCircle size={14} />
                Approve &amp; Save
              </>
            )}
          </button>
        </div>
      )}
      {approved && (analysis.potential_workflows_spotted?.length > 0) && (
        <div className="px-5 py-3 border-t border-aegisaxiom-border">
          {bpmnIds.length > 0 ? (
            <div className="space-y-3">
              <p className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">Preliminary Process Diagrams</p>
              {bpmnIds.map((item, i) => (
                <div key={item.bpmn_id} className="rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface p-3">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm font-medium text-aegisaxiom-text">{item.workflow_name}</span>
                    <div className="flex gap-2">
                      <button onClick={() => setBpmnViewId(v => v === item.bpmn_id ? null : item.bpmn_id)} className="text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300">
                        {bpmnViewId === item.bpmn_id ? 'Hide' : 'View'}
                      </button>
                    </div>
                  </div>
                  {bpmnViewId === item.bpmn_id && <BpmnViewer bpmnId={item.bpmn_id} height={300} />}
                </div>
              ))}
            </div>
          ) : (
            <button
              type="button"
              disabled={generatingBpmns}
              onClick={async () => {
                setGeneratingBpmns(true);
                try {
                  const res = await api.post(`${API_URL}/api/vega-sessions/${sessionId}/generate-preliminary-bpmns`, {});
                  if (res.ok) {
                    const data = await res.json();
                    setBpmnIds((data.results || []).filter(r => r.status === 'generated'));
                  }
                } catch {}
                setGeneratingBpmns(false);
              }}
              className="flex items-center gap-2 text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 transition-colors disabled:opacity-50"
            >
              {generatingBpmns ? (
                <>
                  <div className="w-3 h-3 border-2 border-violet-500/30 border-t-violet-400 rounded-full animate-spin" />
                  Generating BPMN diagrams...
                </>
              ) : (
                <>Generate Preliminary BPMNs ({analysis.potential_workflows_spotted.length} workflows)</>
              )}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
