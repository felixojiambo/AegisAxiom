/**
 * ConfidenceBar — inline confidence pill for the AEGISAXIOM MATCH module.
 *
 * Renders: colored dot + mono percent + verbal label
 * (MATCH / REVIEW / NO MATCH).
 *
 * Thresholds: >=95% → MATCH (green), >=70% → REVIEW (amber), <70% → NO MATCH (red).
 * These match the requirements doc: >=95% auto-draft, <70% no match,
 * everything between needs review.
 *
 * Accepts score as 0-1 (n8n/fixture format) OR 0-100 (legacy callers).
 *
 * Props:
 *   score    — number, 0-1 or 0-100
 *   rowId    — optional, woven into data-testid
 *   compact  — boolean (legacy alias for size="sm")
 *   size     — "sm" | "md" | "lg"
 *   className — additional class overrides
 */
export function ConfidenceBar({ score, rowId, compact = false, size, className = "" }) {
  const raw = score == null ? 0 : Number(score);
  const pct = raw <= 1 ? raw * 100 : raw;

  const tone = pct >= 95 ? "good" : pct >= 70 ? "mid" : "bad";

  // Use AEGISAXIOM CSS variable colours — no raw Tailwind status colours.
  const palette = {
    good: {
      bg:     "bg-aegisaxiom-elevated",
      text:   "text-aegisaxiom-text",
      border: "border-aegisaxiom-border",
      dot:    "bg-[var(--aegisaxiom-success)]",
      label:  "MATCH",
    },
    mid: {
      bg:     "bg-aegisaxiom-elevated",
      text:   "text-[var(--aegisaxiom-warning)]",
      border: "border-[var(--aegisaxiom-warning)]/40",
      dot:    "bg-[var(--aegisaxiom-warning)]",
      label:  "REVIEW",
    },
    bad: {
      bg:     "bg-aegisaxiom-elevated",
      text:   "text-[var(--aegisaxiom-danger)]",
      border: "border-[var(--aegisaxiom-danger)]/40",
      dot:    "bg-[var(--aegisaxiom-danger)]",
      label:  "NO MATCH",
    },
  }[tone];

  const effectiveSize = size || (compact ? "sm" : "md");
  const sizing =
    effectiveSize === "sm" ? "px-2 py-0.5 text-[10px]" :
    effectiveSize === "lg" ? "px-3 py-1.5 text-[12px]" :
                             "px-2.5 py-1 text-[11px]";

  const testId = `badge-confidence-score${rowId ? `-${rowId}` : ""}`;

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border font-mono font-semibold ${sizing} ${palette.bg} ${palette.text} ${palette.border} ${className}`}
      data-testid={testId}
      title={`${pct.toFixed(1)}% — ${palette.label}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${palette.dot}`} />
      <span data-testid={rowId ? `confidence-value-${rowId}` : undefined}>
        {pct.toFixed(1)}%
      </span>
      <span className="opacity-70 ml-0.5 hidden sm:inline">{palette.label}</span>
    </span>
  );
}

export default ConfidenceBar;
