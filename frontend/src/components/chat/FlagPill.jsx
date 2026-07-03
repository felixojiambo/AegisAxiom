import React, { useMemo } from 'react';
import { displayLabel, longExplanation, pillKind } from '../../lib/vegaFlags';

const STYLE_ID = 'vega-flag-pill-styles';

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .vega-pill {
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
    .vega-pill.missing {
      color: #f59e0b;
      background: rgba(245, 158, 11, 0.12);
      border-color: rgba(245, 158, 11, 0.35);
    }
    .vega-pill.tech {
      color: #3b82f6;
      background: rgba(59, 130, 246, 0.12);
      border-color: rgba(59, 130, 246, 0.35);
    }
    .vega-pill.infer {
      color: #c084fc;
      background: rgba(192, 132, 252, 0.12);
      border-color: rgba(192, 132, 252, 0.35);
    }
    .vega-pill.notdiscussed {
      color: #6e6a90;
      background: #1f1f3a;
      border-color: #2a2a44;
    }
    .vega-pill.sourceunclear {
      color: #94a3b8;
      background: rgba(148, 163, 184, 0.12);
      border-color: rgba(148, 163, 184, 0.35);
    }
    .vega-pill.complexity-S {
      color: #10b981;
      background: rgba(16, 185, 129, 0.12);
      border-color: rgba(16, 185, 129, 0.35);
    }
    .vega-pill.complexity-M {
      color: #f59e0b;
      background: rgba(245, 158, 11, 0.12);
      border-color: rgba(245, 158, 11, 0.35);
    }
    .vega-pill.complexity-L {
      color: #f87171;
      background: rgba(248, 113, 113, 0.12);
      border-color: rgba(248, 113, 113, 0.35);
    }
    .vega-pill.complexity-XL {
      color: #f87171;
      background: rgba(248, 113, 113, 0.20);
      border-color: rgba(248, 113, 113, 0.5);
    }
    .vega-pill.score {
      color: #8b5cf6;
      background: rgba(139, 92, 246, 0.12);
      border-color: rgba(139, 92, 246, 0.35);
    }
  `;
  document.head.appendChild(style);
}

const COMPLEXITY_KINDS = new Set(['S', 'M', 'L', 'XL']);

export default function FlagPill({ flag, complexity, score, label, kind, title }) {
  const resolved = useMemo(() => {
    if (complexity && COMPLEXITY_KINDS.has(complexity)) {
      return {
        className: `complexity-${complexity}`,
        text: complexity,
        tooltip: title || `Complexity: ${complexity}`,
      };
    }
    if (score != null) {
      return {
        className: 'score',
        text: typeof score === 'number' ? `${score}` : `${score}`,
        tooltip: title || 'MMI score',
      };
    }
    if (kind) {
      return {
        className: kind,
        text: label || kind.toUpperCase(),
        tooltip: title || label || '',
      };
    }
    if (flag) {
      return {
        className: pillKind(flag) || 'notdiscussed',
        text: label || displayLabel(flag),
        tooltip: title || longExplanation(flag),
      };
    }
    return null;
  }, [flag, complexity, score, label, kind, title]);

  if (!resolved) return null;

  return (
    <span className={`vega-pill ${resolved.className}`} title={resolved.tooltip}>
      {resolved.text}
    </span>
  );
}
