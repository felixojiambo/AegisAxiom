import React from 'react';
import { CheckCircle2, AlertTriangle, XCircle, Minus } from 'lucide-react';

/**
 * ScoreBreakdown — shows the reviewer WHY a row scored what it scored.
 *
 * Renders one line per matching field (customer name, address, BAN, advisor)
 * with a small inline bar showing how much that field contributed to the total
 * confidence.
 *
 * Props:
 *   row — full row object. Reads row.audit_metadata.
 */

const BREAKDOWN_FIELDS = [
  { key: 'customer_name',  label: 'Customer Name',        weight: 35 },
  { key: 'service_address', label: 'Service Address',     weight: 30 },
  { key: 'account_number',  label: 'Account Number / BAN', weight: 25 },
  { key: 'advisor',         label: 'Advisor / Partner',   weight: 10 },
];

function deriveFieldState(row, key) {
  const flags      = row?.audit_metadata?.flags || [];
  const top        = (row?.audit_metadata?.candidates_considered || [])[0];

  if (!top && key !== 'customer_name') {
    return { state: 'neutral', note: 'No candidate to compare' };
  }

  if (key === 'customer_name') {
    if (!top) return { state: 'diff', note: 'No matching customer found' };
    if (flags.includes('name_variation')) {
      const aiReason =
        row?.audit_metadata?.llm_disambiguation?.verdict?.reasoning ||
        row?.audit_metadata?.llm_disambiguation?.reasoning;
      return {
        state: 'partial',
        note: aiReason || 'Name variation — provider name differs from CRM canonical name',
      };
    }
    return { state: 'match', note: 'Exact match' };
  }

  if (key === 'service_address') {
    if (flags.includes('address_discrepancy')) {
      return { state: 'diff', note: 'Address mismatch — provider differs from CRM' };
    }
    if (top?.address_match === true)  return { state: 'match',   note: 'Address matches' };
    if (top?.address_match === false) return { state: 'diff',    note: 'Address differs' };
    return { state: 'neutral', note: 'Address not compared' };
  }

  if (key === 'account_number') {
    if (top?.ban_match === true)  return { state: 'match',   note: 'BAN matches' };
    if (top?.ban_match === false) return { state: 'diff',    note: 'BAN differs' };
    return { state: 'neutral', note: 'BAN missing — not used in score' };
  }

  if (key === 'advisor') {
    return { state: 'neutral', note: 'Advisor mismatch is allowed (advisors can switch)' };
  }

  return { state: 'neutral', note: '' };
}

function StateIcon({ state, className }) {
  const cls = className || 'h-3.5 w-3.5';
  if (state === 'match')   return <CheckCircle2  className={`${cls} text-aegisaxiom-status-success`} />;
  if (state === 'partial') return <AlertTriangle className={`${cls} text-aegisaxiom-status-warn`} />;
  if (state === 'diff')    return <XCircle       className={`${cls} text-aegisaxiom-status-danger`} />;
  return <Minus className={`${cls} text-aegisaxiom-text-muted`} />;
}

function rowClass(state) {
  if (state === 'match')   return 'bg-aegisaxiom-status-success-bg';
  if (state === 'partial') return 'bg-aegisaxiom-status-warn-bg';
  if (state === 'diff')    return 'bg-aegisaxiom-status-danger-bg';
  return '';
}

export default function ScoreBreakdown({ row }) {
  const raw        = row?.audit_metadata?.confidence_score ?? 0;
  const pct        = raw <= 1 ? raw * 100 : raw;
  const flags      = row?.audit_metadata?.flags || [];
  const matchedId  = row?.audit_metadata?.matched_candidate_id;
  const topCand    = (row?.audit_metadata?.candidates_considered || [])[0];
  const aiReason   =
    row?.audit_metadata?.llm_disambiguation?.verdict?.reasoning ||
    row?.audit_metadata?.llm_disambiguation?.reasoning;

  const isNewCustomer =
    flags.includes('new_customer') ||
    (matchedId === null && !topCand?.is_enabled);

  const rulesApplied = Array.isArray(row?.audit_metadata?.rules_applied)
    ? row.audit_metadata.rules_applied
    : [];

  return (
    <div
      className="rounded-xl border border-aegisaxiom-border bg-aegisaxiom-surface overflow-hidden"
      data-testid="score-breakdown"
    >
      {/* Header strip */}
      <div className="flex items-baseline justify-between px-4 py-3 border-b border-aegisaxiom-border bg-aegisaxiom-elevated">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
          Match breakdown
        </span>
        <span className="font-mono text-[18px] font-semibold tracking-tight text-aegisaxiom-text">
          {pct.toFixed(1)}
          <span className="text-aegisaxiom-text-muted text-[12px] ml-0.5">%</span>
        </span>
      </div>

      {/* Body */}
      {isNewCustomer ? (
        <div
          className="px-4 py-3 text-[13px] text-aegisaxiom-text-muted"
          data-testid="score-breakdown-empty"
        >
          <div className="flex items-start gap-2">
            <Minus className="h-4 w-4 mt-0.5 shrink-0" />
            <span>
              No candidate found above the confidence threshold. This appears to be a new customer.
            </span>
          </div>
        </div>
      ) : (
        <ul className="divide-y divide-aegisaxiom-border">
          {BREAKDOWN_FIELDS.map((f) => {
            const { state, note } = deriveFieldState(row, f.key);
            return (
              <li
                key={f.key}
                className={`flex items-start gap-3 px-4 py-2.5 text-[13px] ${rowClass(state)}`}
                data-testid={`score-breakdown-${f.key}`}
              >
                <StateIcon state={state} className="h-4 w-4 mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="font-medium text-aegisaxiom-text">{f.label}</span>
                    <span className="text-[10px] font-semibold uppercase tracking-wider text-aegisaxiom-text-muted shrink-0">
                      Weight {f.weight}%
                    </span>
                  </div>
                  {note && (
                    <div className="text-[12px] text-aegisaxiom-text-muted mt-0.5">{note}</div>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {/* Rules applied */}
      {rulesApplied.length > 0 && (
        <div className="px-4 py-2.5 border-t border-aegisaxiom-border bg-aegisaxiom-elevated/60 text-[12px]">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-aegisaxiom-text-muted mb-1">
            Rules applied
          </div>
          <ul className="space-y-1">
            {rulesApplied.map((r) => (
              <li key={r} className="flex items-start gap-2">
                <span className="font-mono text-aegisaxiom-text-muted mt-0.5">›</span>
                <span className="text-aegisaxiom-text-secondary">
                  {r === 'mac_ratnest_rule' && (
                    <>
                      <strong className="text-aegisaxiom-text">Rat's-nest rule applied:</strong>{' '}
                      Provider classified this as MAC, but no parent order was found in AppDirect.
                      Booked as Contract Type = New, Provider Revenue Category = MAC.
                    </>
                  )}
                  {r === 'llm_disambiguation_matched' && (
                    <>
                      <strong className="text-aegisaxiom-text">AI disambiguation:</strong>{' '}
                      {aiReason ||
                        'Names differed in formatting but were determined to be the same legal entity.'}
                    </>
                  )}
                  {r === 'llm_disambiguation_rejected' && (
                    <>
                      <strong className="text-aegisaxiom-text">AI disambiguation:</strong>{' '}
                      {aiReason ||
                        'Names looked similar but were determined to be different entities.'}
                    </>
                  )}
                  {r === 'llm_fallback_to_fuzzy' && (
                    <>
                      <strong className="text-aegisaxiom-text">AI unavailable:</strong>{' '}
                      Fell back to fuzzy match — manual review recommended.
                    </>
                  )}
                  {![
                    'mac_ratnest_rule',
                    'llm_disambiguation_matched',
                    'llm_disambiguation_rejected',
                    'llm_fallback_to_fuzzy',
                  ].includes(r) && <span className="font-mono">{r}</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
