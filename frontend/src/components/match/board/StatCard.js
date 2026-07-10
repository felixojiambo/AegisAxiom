/**
 * StatCard — clickable summary tile used in the board header strip.
 *
 * Clicking toggles a status filter on the board table below, so the
 * card doubles as a filter chip. When `active` is true the card shows
 * an inset ring (matching AEGISAXIOM's selection pattern).
 *
 * Props
 *   label    {string}  — upper-case label above the count (e.g. "NEEDS REVIEW")
 *   value    {number}  — count to display
 *   accent   {"success"|"warn"|"danger"|"info"|"neutral"} — AEGISAXIOM status token
 *   active   {boolean} — whether this filter is currently active
 *   onClick  {() => void}
 *   testId   {string}  — data-testid for QA
 */
export default function StatCard({
  label,
  value,
  accent = 'neutral',
  active = false,
  onClick,
  testId,
}) {
  const valueColorClass = {
    success: 'text-aegisaxiom-status-success',
    warn: 'text-aegisaxiom-status-warn',
    danger: 'text-aegisaxiom-status-danger',
    info: 'text-aegisaxiom-status-info',
    neutral: 'text-aegisaxiom-text',
  }[accent] ?? 'text-aegisaxiom-text';

  const activeBg = {
    success: 'bg-aegisaxiom-status-success-bg',
    warn: 'bg-aegisaxiom-status-warn-bg',
    danger: 'bg-aegisaxiom-status-danger-bg',
    info: 'bg-aegisaxiom-status-info-bg',
    neutral: 'bg-aegisaxiom-surface-elevated',
  }[accent] ?? 'bg-aegisaxiom-surface-elevated';

  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      className={[
        'rounded-[10px] border px-3 py-2.5 text-left transition-colors min-w-0 w-full',
        active
          ? `border-aegisaxiom-border ring-1 ring-inset ring-aegisaxiom-border ${activeBg}`
          : 'border-aegisaxiom-border bg-aegisaxiom-surface hover:bg-aegisaxiom-surface-elevated',
      ].join(' ')}
    >
      <div className="text-[10px] font-semibold tracking-widest uppercase text-aegisaxiom-text-secondary truncate">
        {label}
      </div>
      <div className={`text-[22px] font-semibold tabular-nums mt-0.5 leading-none ${valueColorClass}`}>
        {value}
      </div>
    </button>
  );
}
