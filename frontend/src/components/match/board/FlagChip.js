import { UserRoundSearch, MapPinOff, UserPlus, Split, Flag } from "lucide-react";

// Human-readable labels for every flag value the backend can emit.
// Keep in sync with backend/routers/match_rows.py flag enum values.
export const FLAG_LABELS = {
  name_variation:               "Name variation",
  address_discrepancy:          "Address mismatch",
  new_customer:                 "New customer",
  new_vs_existing_discrepancy:  "New vs existing",
};

const FLAG_ICONS = {
  name_variation:               UserRoundSearch,
  address_discrepancy:          MapPinOff,
  new_customer:                 UserPlus,
  new_vs_existing_discrepancy:  Split,
};

/**
 * FlagChip — a small inline pill for a single flag value.
 *
 * Props:
 *   flag   — string, e.g. "name_variation"
 *   rowId  — optional, woven into data-testid for E2E targeting
 */
export function FlagChip({ flag, rowId }) {
  const Icon = FLAG_ICONS[flag] || Flag;
  const label = FLAG_LABELS[flag] || flag;

  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-aegisaxiom-border bg-aegisaxiom-elevated px-2 py-0.5 text-[12px] text-aegisaxiom-text-muted"
      data-testid={`flag-chip-${rowId || "x"}-${flag}`}
    >
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}

/**
 * FlagChipGroup — renders up to `max` chips, then an overflow "+N" pill.
 *
 * Props:
 *   flags  — string[]
 *   rowId  — optional, passed through to each FlagChip
 *   max    — max chips before collapsing (default 2)
 */
export function FlagChipGroup({ flags = [], rowId, max = 2 }) {
  if (!flags || flags.length === 0) {
    return <span className="text-[12px] text-aegisaxiom-text-muted">—</span>;
  }

  const visible = flags.slice(0, max);
  const overflow = flags.length - visible.length;

  return (
    <div className="flex items-center gap-1 flex-wrap">
      {visible.map((f) => (
        <FlagChip key={f} flag={f} rowId={rowId} />
      ))}
      {overflow > 0 && (
        <span
          title={flags.slice(max).map((f) => FLAG_LABELS[f] || f).join(", ")}
          className="inline-flex items-center rounded-full border border-aegisaxiom-border bg-aegisaxiom-elevated px-1.5 py-0.5 text-[11px] text-aegisaxiom-text-muted"
          data-testid={`flag-chip-overflow-${rowId || "x"}`}
        >
          +{overflow}
        </span>
      )}
    </div>
  );
}
