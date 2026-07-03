import React from "react";
import { Clipboard } from "lucide-react";
import { FieldRow } from "./FieldRow";

/**
 * FieldGrid — schema-driven side-by-side comparison for the AEGISAXIOM MATCH module.
 *
 * Renders a 3-column table (VENDOR RECORD | GOING TO APPDIRECT | EVIDENCE/FLAGS)
 * from the field definitions in FIELDS_BY_TAB[tabKey].
 *
 * Props:
 *   fields             — array from FIELDS_BY_TAB[tabKey]
 *   source             — row.source_row (raw provider CSV row object)
 *   edited             — local edited copy of output (object)
 *   original           — row.output (read-only original — used for diff indicators only)
 *   flags              — row.audit_metadata.flags (string[])
 *   isNewCustomer      — boolean — hides EVIDENCE column, changes middle header label
 *   matchedCandidateId — number | null — shown in the middle header as a copyable PSO pill
 *   onChange           — (dotPath: string, value: any) => void
 */
export function FieldGrid({
  fields = [],
  source,
  edited,
  original,
  flags = [],
  isNewCustomer = false,
  matchedCandidateId,
  onChange,
}) {
  // Spec §3.4 — new_customer rows hide the EVIDENCE/FLAGS column because
  // there is no matched CRM candidate to compare against.
  const showEvidenceColumn = !isNewCustomer;

  // Column widths: 3-column (existing customer) vs 2-column (new customer).
  const colsTemplate = showEvidenceColumn ? "36% 46% 18%" : "40% 60%";

  const middleLabel = isNewCustomer
    ? "GOING TO APPDIRECT (NEW CUSTOMER)"
    : "GOING TO APPDIRECT";

  return (
    <div className="rounded-xl border border-aegisaxiom-border overflow-hidden">
      {/* Column header strip */}
      <div
        className="grid bg-aegisaxiom-elevated text-[11px] uppercase tracking-wider text-aegisaxiom-text-muted"
        style={{ gridTemplateColumns: colsTemplate }}
      >
        <div className="px-4 py-3 border-r border-aegisaxiom-border font-semibold">
          VENDOR RECORD
        </div>
        <div
          className={`px-4 py-3 ${showEvidenceColumn ? "border-r border-aegisaxiom-border" : ""} flex items-center justify-between gap-2`}
        >
          <span className="font-semibold">{middleLabel}</span>
          {!isNewCustomer && matchedCandidateId && (
            <PsoPill matchedCandidateId={matchedCandidateId} />
          )}
        </div>
        {showEvidenceColumn && (
          <div className="px-4 py-3 font-semibold">EVIDENCE/FLAGS</div>
        )}
      </div>

      {/* Field rows — section headers are injected as non-row dividers */}
      {fields.map((f) => {
        if (f.sectionHeader) {
          return (
            <React.Fragment key={f.path}>
              <SectionHeader label={f.sectionHeader} />
              <FieldRow
                field={f}
                source={source}
                edited={edited}
                original={original}
                flags={flags}
                showEvidenceColumn={showEvidenceColumn}
                colsTemplate={colsTemplate}
                onChange={onChange}
              />
            </React.Fragment>
          );
        }
        return (
          <FieldRow
            key={f.path}
            field={f}
            source={source}
            edited={edited}
            original={original}
            flags={flags}
            showEvidenceColumn={showEvidenceColumn}
            colsTemplate={colsTemplate}
            onChange={onChange}
          />
        );
      })}
    </div>
  );
}

// ── Internal helpers ──────────────────────────────────────────────────────────

function SectionHeader({ label }) {
  return (
    <div className="border-t border-aegisaxiom-border bg-aegisaxiom-elevated/60 px-4 py-2.5">
      <h3
        className="text-[11px] font-semibold uppercase tracking-wider text-aegisaxiom-text-muted border-b border-aegisaxiom-border pb-1"
        data-testid={`row-detail-section-header-${label.replace(/\s+/g, "-").toLowerCase()}`}
      >
        {label}
      </h3>
    </div>
  );
}

function PsoPill({ matchedCandidateId }) {
  const handleCopy = () => {
    navigator.clipboard.writeText(String(matchedCandidateId)).catch(() => null);
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      className="inline-flex items-center gap-1 rounded-md border border-aegisaxiom-border bg-aegisaxiom-base px-2 py-1 font-mono text-[11.5px] normal-case tracking-normal text-aegisaxiom-text hover:bg-aegisaxiom-elevated/80 transition-colors"
      data-testid="row-detail-pso-pill"
      aria-label={`Copy matched candidate ID ${matchedCandidateId}`}
    >
      <Clipboard className="h-3 w-3" />
      {matchedCandidateId}
    </button>
  );
}
