import React, { useState } from "react";
import { AlertCircle } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { FlagChip } from "./FlagChip";

// ── Helpers ──────────────────────────────────────────────────────────────────

function getDot(obj, path) {
  if (!obj || !path) return undefined;
  return path.split(".").reduce((acc, k) => (acc == null ? acc : acc[k]), obj);
}

function fmtMoney(v) {
  if (v == null || v === "") return null;
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(n);
}

function prettyValue(v, isMoney) {
  if (v == null || v === "") return <span className="text-aegisaxiom-text-muted">—</span>;
  if (isMoney) return <span className="font-mono">{fmtMoney(v)}</span>;
  return String(v);
}

// Fields that must be filled for AppDirect to accept a new order.
const CRITICAL_FOR_NEW_ORDER = new Set([
  "order_details.provider_order_id",
  "order_details.contract_type",
  "order_details.mrgmv",
  "order_details.contract_start",
]);

// Map a flag to the field paths it's relevant to (mirrors POC logic).
function flagsForField(path, allFlags) {
  const flags = allFlags || [];
  const relevant = [];
  if (flags.includes("name_variation") && path.includes("customer.name")) relevant.push("name_variation");
  if (flags.includes("address_discrepancy") && path.includes("location.")) relevant.push("address_discrepancy");
  if (flags.includes("new_customer") && path.includes("customer.")) relevant.push("new_customer");
  if (flags.includes("new_vs_existing_discrepancy") && path.includes("contract_type")) relevant.push("new_vs_existing_discrepancy");
  return relevant;
}

function valuesEqual(a, b) {
  if (a == null && b == null) return true;
  if (a == null || b == null) return false;
  return String(a).trim().toLowerCase() === String(b).trim().toLowerCase();
}

// Spec §3.3 row tinting — compares vendor vs initial INTERNAL MATCH value.
// Returns a CSS class string (or inline-style equivalent via data-attr).
function computeRowTone(srcVal, initialRight, fieldFlags, derivedOnly) {
  const srcEmpty = srcVal == null || srcVal === "";
  if (srcEmpty || derivedOnly) return "neutral";
  if (valuesEqual(srcVal, initialRight)) return "match";
  if (fieldFlags.includes("name_variation")) return "partial";
  if (fieldFlags.includes("address_discrepancy")) return "diff";
  // Substring containment heuristic — soft amber instead of hard red.
  if (initialRight != null && initialRight !== "") {
    const a = String(srcVal).trim().toLowerCase();
    const b = String(initialRight).trim().toLowerCase();
    if (a.length > 3 && b.length > 3 && (a.includes(b) || b.includes(a))) return "partial";
  }
  return "diff";
}

// Tone → left-border color using AEGISAXIOM CSS variables.
const TONE_BORDER = {
  match:   "border-l-[var(--aegisaxiom-success)]",
  partial: "border-l-[var(--aegisaxiom-warning)]",
  diff:    "border-l-[var(--aegisaxiom-danger)]",
  neutral: "border-l-transparent",
};

// ── DropdownInput ─────────────────────────────────────────────────────────────

function DropdownInput({ field, cur, onChange, testId, changedCls }) {
  const opts = field.options || [];
  const allowCustom = !!field.allowCustom;
  const valueIsCustom = cur != null && cur !== "" && !opts.includes(cur);
  const [customMode, setCustomMode] = useState(allowCustom && valueIsCustom);

  if (allowCustom && customMode) {
    return (
      <div className="flex items-center gap-2">
        <Input
          value={cur ?? ""}
          onChange={(e) => onChange(field.path, e.target.value)}
          placeholder="Type custom value…"
          className={`h-8 text-[13px] font-mono aegisaxiom-input ${changedCls}`}
          data-testid={testId}
          autoFocus
        />
        <button
          type="button"
          onClick={() => { onChange(field.path, ""); setCustomMode(false); }}
          className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted hover:text-aegisaxiom-text whitespace-nowrap"
          data-testid={`${testId}-use-list`}
        >
          Use list
        </button>
      </div>
    );
  }

  return (
    <Select
      value={cur ?? ""}
      onValueChange={(val) => {
        if (val === "__custom__") {
          setCustomMode(true);
          onChange(field.path, "");
        } else {
          onChange(field.path, val);
        }
      }}
    >
      <SelectTrigger
        className={`h-8 text-[13px] font-mono aegisaxiom-input ${changedCls}`}
        data-testid={testId}
      >
        <SelectValue placeholder="Select…" />
      </SelectTrigger>
      <SelectContent>
        {opts.map((opt) => (
          <SelectItem key={opt} value={opt}>{opt}</SelectItem>
        ))}
        {allowCustom && (
          <SelectItem value="__custom__" data-testid={`${testId}-custom-option`}>
            Custom…
          </SelectItem>
        )}
      </SelectContent>
    </Select>
  );
}

// ── FieldInput ────────────────────────────────────────────────────────────────

function FieldInput({ field, cur, orig, changed, onChange }) {
  const testId = `row-detail-derived-input-${field.path}`;
  const changedCls = changed ? "border-[var(--aegisaxiom-info)]" : "";

  if (field.type === "textarea") {
    return (
      <Textarea
        value={cur ?? ""}
        onChange={(e) => onChange(field.path, e.target.value)}
        className={`min-h-[64px] text-[13px] font-mono aegisaxiom-input ${changedCls}`}
        data-testid={testId}
      />
    );
  }

  if (field.type === "select") {
    return (
      <DropdownInput
        field={field}
        cur={cur}
        onChange={onChange}
        testId={testId}
        changedCls={changedCls}
      />
    );
  }

  if (field.type === "date") {
    return (
      <input
        type="date"
        value={cur ?? ""}
        onChange={(e) => onChange(field.path, e.target.value)}
        className={`h-8 w-full rounded-md border border-aegisaxiom-border bg-aegisaxiom-base px-3 text-[13px] font-mono text-aegisaxiom-text shadow-sm transition-colors focus:outline-none focus:ring-1 focus:ring-aegisaxiom-accent ${changedCls}`}
        data-testid={testId}
      />
    );
  }

  if (field.type === "boolean") {
    return (
      <div className="flex items-center gap-2 py-1">
        <Checkbox
          id={testId}
          checked={!!cur}
          onCheckedChange={(checked) => onChange(field.path, checked)}
          data-testid={testId}
        />
        <label htmlFor={testId} className="text-[13px] cursor-pointer select-none text-aegisaxiom-text">
          {cur ? "Yes" : "No"}
        </label>
      </div>
    );
  }

  if (field.type === "percentage") {
    return (
      <div className="relative flex items-center">
        <Input
          type="number"
          value={cur ?? ""}
          onChange={(e) => onChange(field.path, e.target.value !== "" ? Number(e.target.value) : "")}
          className={`h-8 text-[13px] font-mono aegisaxiom-input pr-6 ${changedCls}`}
          min={0}
          max={100}
          step={0.01}
          data-testid={testId}
        />
        <span className="absolute right-2 text-[12px] text-aegisaxiom-text-muted pointer-events-none">%</span>
      </div>
    );
  }

  // Default: text or money
  return (
    <Input
      value={cur ?? ""}
      onChange={(e) =>
        onChange(field.path, field.type === "money" && e.target.value !== "" ? Number(e.target.value) : e.target.value)
      }
      className={`h-8 text-[13px] font-mono aegisaxiom-input ${changedCls}`}
      data-testid={testId}
    />
  );
}

// ── FieldEvidence ─────────────────────────────────────────────────────────────

function FieldEvidence({ path, flags }) {
  const relevant = flagsForField(path, flags);
  if (relevant.length === 0) {
    return <span className="text-[12px] text-aegisaxiom-text-muted">No flags</span>;
  }
  return (
    <div className="flex flex-col gap-1">
      {relevant.map((f) => (
        <FlagChip key={f} flag={f} />
      ))}
    </div>
  );
}

// ── FieldRow (exported) ───────────────────────────────────────────────────────

/**
 * FieldRow — one row inside FieldGrid.
 *
 * Renders the three-column layout:
 *   VENDOR RECORD (read-only source value) |
 *   GOING TO APPDIRECT (editable internal match) |
 *   EVIDENCE/FLAGS (per-field flags — hidden when showEvidenceColumn=false)
 *
 * Props:
 *   field              — field definition from FIELDS_BY_TAB
 *   source             — row.source_row (raw provider CSV row)
 *   edited             — local edited copy of output
 *   original           — row.output (read-only, used for diff indicators)
 *   flags              — row.audit_metadata.flags (string[])
 *   showEvidenceColumn — boolean
 *   colsTemplate       — CSS grid-template-columns string
 *   onChange           — (dotPath: string, value: any) => void
 */
export function FieldRow({ field, source, edited, original, flags, showEvidenceColumn, colsTemplate, onChange }) {
  // Resolve source value from the source_row.
  // We use a simple key-lookup: the field path's last segment plus common
  // source_key aliases defined in the schema. For AEGISAXIOM we keep it straightforward
  // rather than re-implementing the full POC source extractor.
  const lastKey = field.path.split(".").pop();
  const srcVal = source?.[lastKey] ?? null;
  const isMoney = field.type === "money";

  const orig = getDot(original, field.path);
  const cur = getDot(edited, field.path);
  const changed = JSON.stringify(orig) !== JSON.stringify(cur);

  const fieldFlags = flagsForField(field.path, flags);
  const sourceIsEmpty = srcVal == null || srcVal === "";

  const tone = computeRowTone(srcVal, orig, fieldFlags, !!field.derivedOnly);

  return (
    <div
      className={`grid border-t border-aegisaxiom-border min-h-[48px] border-l-2 ${TONE_BORDER[tone]}`}
      style={{ gridTemplateColumns: colsTemplate }}
      data-match-state={tone}
      data-testid={`field-row-${field.path}`}
    >
      {/* VENDOR RECORD (left, read-only) */}
      <div className="px-4 py-3.5 text-[13px] border-r border-aegisaxiom-border">
        <div className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-0.5 font-semibold">
          {field.label}
        </div>
        <div className="font-mono text-aegisaxiom-text" data-testid={`row-detail-source-value-${field.path}`}>
          {sourceIsEmpty && field.derivedOnly ? (
            <span className="italic text-aegisaxiom-text-muted text-[12.5px] leading-snug">
              (derived field)
            </span>
          ) : (
            prettyValue(srcVal, isMoney)
          )}
        </div>
      </div>

      {/* GOING TO APPDIRECT (middle, editable) */}
      <div
        className={`px-4 py-3.5 ${showEvidenceColumn ? "border-r border-aegisaxiom-border" : ""}`}
        data-testid={`row-detail-internal-match-${field.path}`}
      >
        <div className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-0.5 font-semibold">
          {field.label}
        </div>
        <FieldInput
          field={field}
          cur={cur}
          orig={orig}
          changed={changed}
          onChange={onChange}
        />
        {changed && (
          <div className="mt-1 text-[11px] text-[var(--aegisaxiom-info)]">
            Edited (was: {prettyValue(orig, isMoney)})
          </div>
        )}
        {CRITICAL_FOR_NEW_ORDER.has(field.path) && (cur == null || cur === "") && (
          <div
            className="mt-1.5 inline-flex items-center gap-1 rounded-md border border-[var(--aegisaxiom-warning)]/40 bg-[var(--aegisaxiom-warning-soft)] px-1.5 py-0.5 text-[10.5px] uppercase tracking-wider text-[var(--aegisaxiom-warning)]"
            data-testid={`row-detail-required-chip-${field.path}`}
          >
            <AlertCircle className="h-3 w-3" />
            Required for new order
          </div>
        )}
      </div>

      {/* EVIDENCE/FLAGS (right, narrow, read-only) */}
      {showEvidenceColumn && (
        <div className="px-4 py-3.5" data-testid={`row-detail-evidence-${field.path}`}>
          <div className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted mb-0.5 font-semibold">
            &nbsp;
          </div>
          <FieldEvidence path={field.path} flags={flags} />
        </div>
      )}
    </div>
  );
}
