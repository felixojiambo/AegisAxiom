import React from 'react';

const STYLE_ID = 'vega-doc-status-badge-styles';

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .vega-doc-status {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 1px 7px;
      border-radius: 999px;
      font-size: 10.5px;
      font-weight: 600;
      letter-spacing: 0.02em;
      border: 1px solid transparent;
      white-space: nowrap;
      line-height: 1.5;
    }
    .vega-doc-status.generated {
      color: #10b981;
      background: rgba(16, 185, 129, 0.12);
      border-color: rgba(16, 185, 129, 0.35);
    }
    .vega-doc-status.pending {
      color: #94a3b8;
      background: rgba(148, 163, 184, 0.12);
      border-color: rgba(148, 163, 184, 0.35);
    }
    .vega-doc-status.failed {
      color: #f87171;
      background: rgba(248, 113, 113, 0.12);
      border-color: rgba(248, 113, 113, 0.35);
    }
    .vega-doc-status .dot {
      width: 5px;
      height: 5px;
      border-radius: 999px;
      background: currentColor;
      display: inline-block;
    }
  `;
  document.head.appendChild(style);
}

const LABELS = {
  generated: 'Generated',
  pending: 'Pending',
  failed: 'Generation failed',
};

const TOOLTIPS = {
  generated: 'Design document generated successfully.',
  pending: 'Design document not yet generated.',
  failed: 'Design document generation failed — re-run analysis or escalate for technical follow-up.',
};

export default function DocStatusBadge({ status, label }) {
  if (!status) return null;
  const className = ['generated', 'pending', 'failed'].includes(status) ? status : 'pending';
  const text = label || LABELS[className];
  return (
    <span className={`vega-doc-status ${className}`} title={TOOLTIPS[className]}>
      <span className="dot" />
      {text}
    </span>
  );
}
