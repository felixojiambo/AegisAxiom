import React, { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Search } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { fmtCurrency, fmtDate } from '@/lib/financeFormat';

const BASE = `${API_URL}/api/finance`;

function AgingCell({ v, bold, warn, bad }) {
  const num = v === '' || v == null ? null : Number(v);
  let cls = 'text-aegisaxiom-text';
  if (warn && num) cls = 'text-amber-400';
  if (bad && num) cls = 'text-red-400';
  return (
    <td
      className={`px-5 py-2 text-right font-mono tabular-nums ${cls} ${bold ? 'font-medium' : ''}`}
    >
      {num == null || isNaN(num) || num === 0 ? (
        <span className="text-aegisaxiom-text-muted">—</span>
      ) : (
        fmtCurrency(num)
      )}
    </td>
  );
}

export default function ARAgingTab() {
  const [data, setData] = useState(null);
  const [detail, setDetail] = useState({});
  const [open, setOpen] = useState({});
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  const refresh = async () => {
    try {
      const r = await api.get(`${BASE}/working-capital/ar-aging`);
      if (!r.ok) {
        setLoadError(`Failed to load AR aging (${r.status})`);
        return;
      }
      setData(await r.json());
      setLoadError(null);
    } catch (err) {
      setLoadError(err?.message || 'Failed to load AR aging');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const rows = useMemo(() => {
    if (!data) return [];
    return (data.line_items || []).filter(
      (li) =>
        !li.is_summary &&
        (!search || (li.Customer || '').toLowerCase().includes(search.toLowerCase())),
    );
  }, [data, search]);

  const totals = useMemo(() => {
    const acc = { Current: 0, '1 - 30': 0, '31 - 60': 0, '61 - 90': 0, '91 and over': 0, Total: 0 };
    rows.forEach((li) =>
      Object.keys(acc).forEach((k) => {
        acc[k] += parseFloat(li[k] || 0) || 0;
      }),
    );
    return acc;
  }, [rows]);

  const toggle = async (li) => {
    const id = li.Customer_id;
    setOpen((p) => ({ ...p, [id]: !p[id] }));
    if (!detail[id]) {
      const r = await api.get(`${BASE}/working-capital/ar-aging-detail?customer_id=${id}`);
      if (r.ok) {
        const d = await r.json();
        setDetail((p) => ({ ...p, [id]: d.line_items }));
      }
    }
  };

  if (loading)
    return (
      <div
        className="py-20 text-center text-sm text-aegisaxiom-text-muted"
        data-testid="loading-ar-aging"
      >
        Loading…
      </div>
    );
  if (loadError)
    return (
      <div className="glass-card p-12 text-center" data-testid="ar-aging-load-error">
        <div className="text-[16px] font-semibold text-aegisaxiom-text mb-2">Couldn't load AR aging</div>
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

  return (
    <div className="space-y-6" data-testid="page-ar-aging">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 md:gap-4 md:flex-wrap">
        <div>
          <p className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-0.5">
            WORKING CAPITAL
          </p>
          <h2 className="page-title">AR Aging</h2>
          <p className="text-sm text-aegisaxiom-text-secondary mt-1">
            Outstanding receivables by bucket — {data.end_period}. Click a row to drill into open
            invoices.
          </p>
        </div>
        <div className="flex items-center gap-2 px-3 h-9 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface w-full md:w-[280px]">
          <Search className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
          <input
            data-testid="ar-search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search customer…"
            className="bg-transparent outline-none text-[13px] flex-1 text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted"
          />
        </div>
      </div>

      <div className="glass-card overflow-hidden">
        {/* Desktop table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-aegisaxiom-border bg-aegisaxiom-elevated/40">
                {['Customer', 'Current', '1–30', '31–60', '61–90', '91+', 'Total'].map((h, i) => (
                  <th
                    key={h}
                    className={`px-5 py-2.5 text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted ${i === 0 ? 'text-left' : 'text-right'}`}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((li, i) => {
                const id = li.Customer_id;
                const isOpen = !!open[id];
                return (
                  <React.Fragment key={`${id}-${i}`}>
                    <tr
                      className="border-b border-aegisaxiom-border/50 hover:bg-aegisaxiom-elevated/30 cursor-pointer"
                      onClick={() => toggle(li)}
                      data-testid={`ar-row-${id}`}
                    >
                      <td className="px-5 py-2">
                        <div className="flex items-center gap-2">
                          {isOpen ? (
                            <ChevronDown className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
                          ) : (
                            <ChevronRight className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
                          )}
                          <span className="text-aegisaxiom-text">{li.Customer}</span>
                        </div>
                      </td>
                      <AgingCell v={li.Current} />
                      <AgingCell v={li['1 - 30']} />
                      <AgingCell v={li['31 - 60']} warn />
                      <AgingCell v={li['61 - 90']} warn />
                      <AgingCell v={li['91 and over']} bad />
                      <AgingCell v={li.Total} bold />
                    </tr>
                    {isOpen && detail[id] ? (
                      <tr className="bg-aegisaxiom-elevated/20">
                        <td colSpan={7} className="p-0">
                          <div className="px-5 py-4">
                            <div className="text-[11px] text-aegisaxiom-text-muted uppercase tracking-wider mb-2">
                              Open invoices
                            </div>
                            <div className="rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface overflow-x-auto">
                              <table className="w-full text-[12px]">
                                <thead>
                                  <tr className="bg-aegisaxiom-elevated/60 border-b border-aegisaxiom-border">
                                    {[
                                      'Date',
                                      'Invoice #',
                                      'Due Date',
                                      'Bucket',
                                      'Amount',
                                      'Open Balance',
                                    ].map((h, i) => (
                                      <th
                                        key={h}
                                        className={`px-3 py-2 text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted ${i >= 4 ? 'text-right' : 'text-left'}`}
                                      >
                                        {h}
                                      </th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {detail[id]
                                    .filter((d) => !d.is_summary)
                                    .map((d, j) => (
                                      <tr key={j} className="border-b border-aegisaxiom-border/60">
                                        <td className="px-3 py-1.5 font-mono text-aegisaxiom-text">
                                          {fmtDate(d.Date)}
                                        </td>
                                        <td className="px-3 py-1.5 text-aegisaxiom-text">{d.Num}</td>
                                        <td className="px-3 py-1.5 font-mono text-aegisaxiom-text-secondary">
                                          {fmtDate(d['Due Date'])}
                                        </td>
                                        <td className="px-3 py-1.5 text-aegisaxiom-text-secondary">
                                          {d.section}
                                        </td>
                                        <td className="px-3 py-1.5 text-right font-mono text-aegisaxiom-text">
                                          {fmtCurrency(d.Amount)}
                                        </td>
                                        <td className="px-3 py-1.5 text-right font-mono text-aegisaxiom-text font-medium">
                                          {fmtCurrency(d['Open Balance'])}
                                        </td>
                                      </tr>
                                    ))}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </React.Fragment>
                );
              })}
            </tbody>
            <tfoot>
              <tr className="bg-aegisaxiom-elevated/40">
                <td className="px-5 py-3 text-[12px] font-medium text-aegisaxiom-text">
                  Total · {rows.length} customers
                </td>
                <AgingCell v={totals.Current} bold />
                <AgingCell v={totals['1 - 30']} bold />
                <AgingCell v={totals['31 - 60']} bold />
                <AgingCell v={totals['61 - 90']} bold />
                <AgingCell v={totals['91 and over']} bold bad />
                <AgingCell v={totals.Total} bold />
              </tr>
            </tfoot>
          </table>
        </div>

        {/* Mobile card list */}
        <div className="md:hidden">
          {rows.length === 0 ? (
            <div className="px-5 py-10 text-center text-[13px] text-aegisaxiom-text-muted">
              No customers match.
            </div>
          ) : (
            <>
              <div className="p-3 flex flex-col gap-2.5">
                {rows.map((li, i) => {
                  const id = li.Customer_id;
                  const isOpen = !!open[id];
                  const buckets = [
                    { label: 'Current', value: li.Current },
                    { label: '1–30', value: li['1 - 30'] },
                    { label: '31–60', value: li['31 - 60'], warn: true },
                    { label: '61–90', value: li['61 - 90'], warn: true },
                    { label: '91+', value: li['91 and over'], bad: true },
                  ];
                  return (
                    <div
                      key={`${id}-${i}`}
                      className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg overflow-hidden"
                      data-testid={`ar-card-${id}`}
                    >
                      <button
                        type="button"
                        onClick={() => toggle(li)}
                        className="w-full text-left p-3.5 active:bg-aegisaxiom-elevated transition-colors"
                      >
                        <div className="flex items-center justify-between gap-2 mb-2.5">
                          <div className="flex items-center gap-1.5 min-w-0">
                            {isOpen ? (
                              <ChevronDown className="h-3.5 w-3.5 text-aegisaxiom-text-muted shrink-0" />
                            ) : (
                              <ChevronRight className="h-3.5 w-3.5 text-aegisaxiom-text-muted shrink-0" />
                            )}
                            <span className="text-[13px] font-medium text-aegisaxiom-text truncate">
                              {li.Customer}
                            </span>
                          </div>
                          <span className="text-[14px] font-mono font-semibold text-aegisaxiom-text shrink-0">
                            {fmtCurrency(li.Total || 0)}
                          </span>
                        </div>
                        <div className="grid grid-cols-5 gap-1 pt-2 border-t border-aegisaxiom-border">
                          {buckets.map((b) => {
                            const num = Number(b.value || 0);
                            let cls = 'text-aegisaxiom-text';
                            if (b.warn && num) cls = 'text-amber-400';
                            if (b.bad && num) cls = 'text-red-400';
                            return (
                              <div key={b.label} className="text-center">
                                <p className="text-[9px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                                  {b.label}
                                </p>
                                <p className={`text-[10.5px] font-mono ${cls}`}>
                                  {num === 0 ? (
                                    <span className="text-aegisaxiom-text-muted">—</span>
                                  ) : (
                                    fmtCurrency(num, { compact: true })
                                  )}
                                </p>
                              </div>
                            );
                          })}
                        </div>
                      </button>
                      {isOpen && detail[id] ? (
                        <div className="px-3 pb-3 bg-aegisaxiom-elevated/20">
                          <div className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mt-2 mb-1.5">
                            Open invoices
                          </div>
                          <div className="flex flex-col gap-1.5">
                            {detail[id]
                              .filter((d) => !d.is_summary)
                              .map((d, j) => (
                                <div
                                  key={j}
                                  className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface p-2.5 text-[11px]"
                                >
                                  <div className="flex items-center justify-between gap-2 mb-1">
                                    <span className="font-medium text-aegisaxiom-text">#{d.Num}</span>
                                    <span className="font-mono font-medium text-aegisaxiom-text">
                                      {fmtCurrency(d['Open Balance'])}
                                    </span>
                                  </div>
                                  <div className="flex items-center justify-between gap-2 text-aegisaxiom-text-muted">
                                    <span className="font-mono">
                                      {fmtDate(d.Date)} → {fmtDate(d['Due Date'])}
                                    </span>
                                    <span className="truncate">{d.section}</span>
                                  </div>
                                </div>
                              ))}
                          </div>
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
              <div className="border-t border-aegisaxiom-border bg-aegisaxiom-elevated/40 px-3 py-3">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-[11px] font-semibold text-aegisaxiom-text">
                    Total · {rows.length} customers
                  </p>
                  <span className="text-[14px] font-mono font-semibold text-aegisaxiom-text">
                    {fmtCurrency(totals.Total || 0)}
                  </span>
                </div>
                <div className="grid grid-cols-5 gap-1">
                  {[
                    { label: 'Current', value: totals.Current },
                    { label: '1–30', value: totals['1 - 30'] },
                    { label: '31–60', value: totals['31 - 60'], warn: true },
                    { label: '61–90', value: totals['61 - 90'], warn: true },
                    { label: '91+', value: totals['91 and over'], bad: true },
                  ].map((b) => {
                    const num = Number(b.value || 0);
                    let cls = 'text-aegisaxiom-text';
                    if (b.warn && num) cls = 'text-amber-400';
                    if (b.bad && num) cls = 'text-red-400';
                    return (
                      <div key={b.label} className="text-center">
                        <p className="text-[9px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                          {b.label}
                        </p>
                        <p className={`text-[10.5px] font-mono font-medium ${cls}`}>
                          {num === 0 ? (
                            <span className="text-aegisaxiom-text-muted">—</span>
                          ) : (
                            fmtCurrency(num, { compact: true })
                          )}
                        </p>
                      </div>
                    );
                  })}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
