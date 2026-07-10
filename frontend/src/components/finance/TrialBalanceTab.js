import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { fmtCurrency } from '@/lib/financeFormat';

const BASE = `${API_URL}/api/finance`;

export default function TrialBalanceTab() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  const refresh = async () => {
    try {
      const r = await api.get(`${BASE}/financials/trial-balance`);
      if (!r.ok) {
        setLoadError(`Failed to load trial balance (${r.status})`);
        return;
      }
      setData(await r.json());
      setLoadError(null);
    } catch (err) {
      setLoadError(err?.message || 'Failed to load trial balance');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  if (loading)
    return (
      <div
        className="py-20 text-center text-sm text-aegisaxiom-text-muted"
        data-testid="loading-trial-balance"
      >
        Loading…
      </div>
    );
  if (loadError)
    return (
      <div className="glass-card p-12 text-center" data-testid="trial-balance-load-error">
        <div className="text-[16px] font-semibold text-aegisaxiom-text mb-2">
          Couldn't load trial balance
        </div>
        <div className="text-[13px] text-aegisaxiom-text-secondary mb-6">{loadError}</div>
        <button
          onClick={refresh}
          className="btn-secondary inline-flex items-center gap-2 h-9 px-4 text-[13px]"
        >
          Try again
        </button>
      </div>
    );
  if (!data) return null;

  const items = (data.line_items || []).filter(
    (li) => !li.is_summary || li.account_name?.toLowerCase().includes('total'),
  );

  return (
    <div className="space-y-6" data-testid="page-trial-balance">
      <div>
        <p className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-0.5">
          FINANCIALS
        </p>
        <h2 className="page-title">Trial Balance</h2>
        <p className="text-sm text-aegisaxiom-text-secondary mt-1">
          As of {data.end_period}. Sum of all account debits and credits.
        </p>
      </div>
      <div className="glass-card overflow-hidden">
        {/* Desktop table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-aegisaxiom-border bg-aegisaxiom-elevated/40">
                <th className="text-left px-5 py-2.5 text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">
                  Account
                </th>
                <th className="text-right px-5 py-2.5 text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">
                  Debit
                </th>
                <th className="text-right px-5 py-2.5 text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">
                  Credit
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((li, i) => (
                <tr
                  key={i}
                  className={`border-b border-aegisaxiom-border/50 hover:bg-aegisaxiom-elevated/30 ${li.is_summary ? 'bg-aegisaxiom-elevated/20 font-medium' : ''}`}
                >
                  <td className="px-5 py-2 text-aegisaxiom-text">{li.Account || li.account_name}</td>
                  <td className="px-5 py-2 text-right font-mono tabular-nums">
                    {li.Debit ? (
                      fmtCurrency(li.Debit)
                    ) : (
                      <span className="text-aegisaxiom-text-muted">—</span>
                    )}
                  </td>
                  <td className="px-5 py-2 text-right font-mono tabular-nums">
                    {li.Credit ? (
                      fmtCurrency(li.Credit)
                    ) : (
                      <span className="text-aegisaxiom-text-muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Mobile card list */}
        <div className="md:hidden">
          {items.length === 0 ? (
            <div className="px-5 py-10 text-center text-[13px] text-aegisaxiom-text-muted">
              No accounts to show.
            </div>
          ) : (
            <div className="p-3 flex flex-col gap-2">
              {items.map((li, i) => (
                <div
                  key={i}
                  className={`border border-aegisaxiom-border rounded-lg p-3 ${li.is_summary ? 'bg-aegisaxiom-elevated/40 font-medium' : 'bg-aegisaxiom-base'}`}
                  data-testid={`trial-balance-card-${i}`}
                >
                  <p className="text-[13px] text-aegisaxiom-text mb-2 truncate">
                    {li.Account || li.account_name}
                  </p>
                  <div className="grid grid-cols-2 gap-2 pt-2 border-t border-aegisaxiom-border">
                    <div>
                      <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                        Debit
                      </p>
                      <p className="text-[12px] font-mono tabular-nums text-aegisaxiom-text">
                        {li.Debit ? (
                          fmtCurrency(li.Debit)
                        ) : (
                          <span className="text-aegisaxiom-text-muted">—</span>
                        )}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                        Credit
                      </p>
                      <p className="text-[12px] font-mono tabular-nums text-aegisaxiom-text">
                        {li.Credit ? (
                          fmtCurrency(li.Credit)
                        ) : (
                          <span className="text-aegisaxiom-text-muted">—</span>
                        )}
                      </p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
