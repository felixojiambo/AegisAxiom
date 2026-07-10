import React from 'react';
import { ArrowUpRight, ArrowDownRight } from 'lucide-react';
import { fmtPercent } from '@/lib/financeFormat';

/**
 * KPICard — used in CommandCenterTab static KPI grid.
 * Updated to AEGISAXIOM CSS-var styling (no dark: prefix, no hardcoded bg-white).
 */
export function KPICard({ label, value, hint, delta, deltaLabel, accent: AccentIcon, testid }) {
  let deltaCls = 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted';
  let DeltaIcon = ArrowUpRight;
  if (delta != null && !isNaN(delta)) {
    const positive = delta > 0;
    const negative = delta < 0;
    deltaCls = positive
      ? 'bg-green-500/15 text-green-400'
      : negative
        ? 'bg-red-500/15 text-red-400'
        : 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted';
    DeltaIcon = positive ? ArrowUpRight : ArrowDownRight;
  }

  return (
    <div className="glass-card p-5" data-testid={testid || `kpi-${label.toLowerCase().replace(/\s/g, '-')}`}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">{label}</span>
        {AccentIcon && <span className="text-aegisaxiom-text-muted">{AccentIcon}</span>}
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-[26px] font-bold tabular-nums text-aegisaxiom-text leading-none">{value}</span>
        {delta != null && !isNaN(delta) && (
          <span className={`inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[11px] font-medium font-mono ${deltaCls}`}>
            <DeltaIcon className="h-3 w-3" />
            {fmtPercent(Math.abs(delta), 1)}
          </span>
        )}
      </div>
      {hint && <p className="mt-1.5 text-[12px] text-aegisaxiom-text-muted">{hint}</p>}
      {deltaLabel && delta != null && (
        <p className="text-[11px] text-aegisaxiom-text-muted mt-0.5">{deltaLabel}</p>
      )}
    </div>
  );
}

export function DeltaBadge({ value, invert = false }) {
  if (value == null || isNaN(value)) return null;
  const positive = invert ? value < 0 : value > 0;
  const cls = positive ? 'bg-green-500/15 text-green-400' : 'bg-red-500/15 text-red-400';
  const Icon = positive ? ArrowUpRight : ArrowDownRight;
  return (
    <span className={`inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[11px] font-medium font-mono ${cls}`}>
      <Icon className="h-3 w-3" />
      {fmtPercent(Math.abs(value), 1)}
    </span>
  );
}

export default KPICard;
