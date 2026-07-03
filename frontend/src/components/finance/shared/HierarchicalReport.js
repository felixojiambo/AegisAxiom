import React, { useState, useMemo } from 'react';
import { ChevronRight } from 'lucide-react';
import { fmtCurrency } from '@/lib/financeFormat';

/**
 * HierarchicalReport — renders QBO-style P&L, Balance Sheet, Cash Flow reports.
 * Ported from source components/HierarchicalReport.jsx; uses AEGISAXIOM styling.
 */
export default function HierarchicalReport({ lineItems = [], columns = [], compareItems = [], testid }) {
  const [collapsed, setCollapsed] = useState({});

  const compareMap = useMemo(() => {
    const m = new Map();
    compareItems.forEach((li) => {
      const key = `${li.section || ''}|${li.account_name || li.Account || ''}|${li.is_summary ? '1' : '0'}`;
      m.set(key, li);
    });
    return m;
  }, [compareItems]);

  if (!lineItems.length) {
    return (
      <div className="glass-card p-8 text-center text-sm text-aegisaxiom-text-muted">No data available.</div>
    );
  }

  const totalCol = columns?.find((c) => c.toLowerCase() === 'total') || 'Total';
  const labelCol = columns?.[0] || 'Account';

  const toggleSection = (s) => setCollapsed((p) => ({ ...p, [s]: !p[s] }));

  return (
    <div className="glass-card overflow-hidden" data-testid={testid || 'hierarchical-report'}>
      <div className="overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-b border-aegisaxiom-border bg-aegisaxiom-elevated/40">
              <th className="text-left px-5 py-2.5 text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">{labelCol}</th>
              {compareItems.length ? (
                <th className="text-right px-5 py-2.5 text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">Prior Year</th>
              ) : null}
              <th className="text-right px-5 py-2.5 text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">{totalCol}</th>
            </tr>
          </thead>
          <tbody>
            {lineItems.map((li, idx) => {
              const depth = Number(li.depth || 0);
              const isSummary = !!li.is_summary;
              const name = li.Account || li.Customer || li.Vendor || li.account_name || '';
              const value = li[totalCol] ?? li.Total;
              const num = value === '' || value == null ? null : Number(value);

              const compareKey = `${li.section || ''}|${li.account_name || li.Account || ''}|${li.is_summary ? '1' : '0'}`;
              const cmp = compareMap.get(compareKey);
              const cmpVal = cmp ? Number(cmp[totalCol] ?? cmp.Total) : null;

              const sectionKey = li.section || '';
              const isSectionHeader = isSummary && depth <= 1;
              const hidden = sectionKey && collapsed[sectionKey] && !isSectionHeader && depth > 1;

              if (hidden) return null;

              return (
                <tr
                  key={idx}
                  className={`border-b border-aegisaxiom-border/50 transition-colors hover:bg-aegisaxiom-elevated/30 ${isSummary ? 'bg-aegisaxiom-elevated/20 font-medium' : ''}`}
                  data-testid={`row-${idx}`}
                >
                  <td className="px-5 py-2 align-middle">
                    <div
                      className="flex items-center gap-2"
                      style={{ paddingLeft: `${Math.max(0, depth - 1) * 20}px` }}
                    >
                      {isSectionHeader && sectionKey ? (
                        <button
                          onClick={() => toggleSection(sectionKey)}
                          className="h-4 w-4 grid place-items-center text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
                          aria-label="Toggle section"
                        >
                          <ChevronRight className={`h-3 w-3 transition-transform ${collapsed[sectionKey] ? '' : 'rotate-90'}`} />
                        </button>
                      ) : (
                        <span className="h-4 w-4" />
                      )}
                      <span className={isSummary ? 'text-aegisaxiom-text font-medium' : 'text-aegisaxiom-text-secondary'}>
                        {name}
                      </span>
                    </div>
                  </td>
                  {compareItems.length ? (
                    <td className="px-5 py-2 text-right font-mono text-[12.5px] text-aegisaxiom-text-secondary tabular-nums">
                      {cmpVal == null || isNaN(cmpVal) ? <span className="text-aegisaxiom-text-muted">—</span> : fmtCurrency(cmpVal)}
                    </td>
                  ) : null}
                  <td className={`px-5 py-2 text-right font-mono text-[12.5px] tabular-nums ${isSummary ? 'text-aegisaxiom-text font-medium' : 'text-aegisaxiom-text'}`}>
                    {num == null || isNaN(num) ? <span className="text-aegisaxiom-text-muted">—</span> : fmtCurrency(num)}
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
