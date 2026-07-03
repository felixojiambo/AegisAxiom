import React from 'react';
import { CheckCircle2 } from 'lucide-react';
import { FlagChip } from './FlagChip';

/**
 * CandidatesConsidered — collapsible table of fuzzy-match candidates.
 *
 * Renders inside the RowDetailDrawer below the tab strip. Hidden when
 * there are no candidates.
 *
 * Props:
 *   candidates  — row.audit_metadata.candidates_considered (array)
 *   flags       — row.audit_metadata.flags (string[])
 */
export default function CandidatesConsidered({ candidates = [], flags = [] }) {
  if (!candidates || candidates.length === 0) return null;

  const isNewCustomer = flags.includes('new_customer');

  return (
    <div>
      {isNewCustomer && (
        <p
          className="mb-2 text-[12px] text-aegisaxiom-text-muted italic"
          data-testid="row-detail-candidates-empty-context"
        >
          No candidate scored above the confidence threshold. The{' '}
          {candidates.length} closest fuzzy{' '}
          {candidates.length === 1 ? 'match is' : 'matches are'} shown for reference.
        </p>
      )}

      <div className="rounded-xl border border-aegisaxiom-border overflow-hidden">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="bg-aegisaxiom-elevated text-[11px] uppercase tracking-wider text-aegisaxiom-text-muted">
              <th className="px-4 py-2.5 text-left font-semibold border-r border-aegisaxiom-border">
                Candidate
              </th>
              <th className="px-4 py-2.5 text-left font-semibold border-r border-aegisaxiom-border">
                ID
              </th>
              <th className="px-4 py-2.5 text-left font-semibold border-r border-aegisaxiom-border">
                Enabled
              </th>
              <th className="px-4 py-2.5 text-left font-semibold border-r border-aegisaxiom-border">
                Name match
              </th>
              <th className="px-4 py-2.5 text-left font-semibold border-r border-aegisaxiom-border">
                BAN
              </th>
              <th className="px-4 py-2.5 text-left font-semibold">
                Address
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-aegisaxiom-border">
            {candidates.map((c, i) => {
              const name      = c.customer_name ?? c.name ?? '—';
              const id        = c.customer_id ?? c.id ?? '';
              const cityState = [c.city, c.state].filter(Boolean).join(', ');
              const nameScore = c.name_ratio ?? c.score ?? null;
              const ban       = c.primary_ban ?? c.ban ?? '';
              const street    = c.street ?? '';

              return (
                <tr
                  key={id || i}
                  className="hover:bg-aegisaxiom-elevated/50 transition-colors"
                  data-testid={`candidate-row-${id || i}`}
                >
                  {/* Candidate name + city/state */}
                  <td className="px-4 py-3 border-r border-aegisaxiom-border">
                    <div className="font-medium text-aegisaxiom-text">{name}</div>
                    {cityState && (
                      <div className="text-[11.5px] text-aegisaxiom-text-muted">{cityState}</div>
                    )}
                  </td>

                  {/* ID */}
                  <td className="px-4 py-3 border-r border-aegisaxiom-border font-mono text-[12px] text-aegisaxiom-text-muted">
                    {id || '—'}
                  </td>

                  {/* Enabled */}
                  <td className="px-4 py-3 border-r border-aegisaxiom-border text-aegisaxiom-text-secondary">
                    {c.is_enabled ? 'Yes' : 'No'}
                  </td>

                  {/* Name match % */}
                  <td className="px-4 py-3 border-r border-aegisaxiom-border font-mono text-aegisaxiom-text-secondary">
                    {nameScore == null
                      ? '—'
                      : `${(Number(nameScore) * 100).toFixed(0)}%`}
                  </td>

                  {/* BAN */}
                  <td className="px-4 py-3 border-r border-aegisaxiom-border">
                    {c.ban_match ? (
                      <span className="inline-flex items-center gap-1 text-aegisaxiom-status-success">
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        <span className="font-mono text-[12px]">{ban || 'match'}</span>
                      </span>
                    ) : (
                      <span className="text-aegisaxiom-text-muted font-mono text-[12px]">
                        {ban || '—'}
                      </span>
                    )}
                  </td>

                  {/* Address */}
                  <td className="px-4 py-3">
                    {c.address_match ? (
                      <span className="inline-flex items-center gap-1 text-aegisaxiom-status-success">
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        <span className="text-[12px]">match</span>
                      </span>
                    ) : (
                      <span
                        className="text-aegisaxiom-text-muted text-[12px]"
                        title={street}
                      >
                        {street
                          ? street.length > 28
                            ? `${street.slice(0, 28)}…`
                            : street
                          : '—'}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
