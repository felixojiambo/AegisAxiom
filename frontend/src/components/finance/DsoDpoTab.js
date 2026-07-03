import React, { useEffect, useState } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { fmtCurrency, fmtNumber } from '@/lib/financeFormat';

const BASE = `${API_URL}/api/finance`;

const monthLabel = (ym) => {
  if (!ym) return '';
  const [y, m] = ym.split('-');
  return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString('en-US', { month: 'short' });
};

const ChartTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface px-3 py-2 shadow-sm text-[12px]">
      <div className="font-mono text-[11px] text-aegisaxiom-text-muted mb-1">{label}</div>
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-3">
          <span className="h-2 w-2 rounded-sm" style={{ background: p.color }} />
          <span className="text-aegisaxiom-text-secondary">{p.name}</span>
          <span className="font-mono text-aegisaxiom-text ml-auto">
            {fmtNumber(p.value, { decimals: 1 })} d
          </span>
        </div>
      ))}
    </div>
  );
};

function Stat({ label, value, prev }) {
  const delta = prev ? value - prev : 0;
  return (
    <div className="glass-card p-5">
      <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-1">
        {label}
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-[28px] font-semibold tabular-nums text-aegisaxiom-text">
          {(value || 0).toFixed(1)}
        </span>
        <span className="text-[12px] text-aegisaxiom-text-muted font-mono">days</span>
      </div>
      <div className="text-[12px] text-aegisaxiom-text-muted mt-1 font-mono">
        Δ vs prior month: {delta >= 0 ? '+' : ''}
        {delta.toFixed(1)}d
      </div>
    </div>
  );
}

export default function DsoDpoTab() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  const refresh = async () => {
    try {
      const r = await api.get(`${BASE}/working-capital/dso-dpo?months=12`);
      if (!r.ok) {
        setLoadError(`Failed to load DSO/DPO (${r.status})`);
        return;
      }
      setData(await r.json());
      setLoadError(null);
    } catch (err) {
      setLoadError(err?.message || 'Failed to load DSO/DPO');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  if (loading)
    return (
      <div className="py-20 text-center text-sm text-aegisaxiom-text-muted" data-testid="loading-dso-dpo">
        Loading…
      </div>
    );
  if (loadError)
    return (
      <div className="glass-card p-12 text-center" data-testid="dso-dpo-load-error">
        <div className="text-[16px] font-semibold text-aegisaxiom-text mb-2">Couldn't load DSO/DPO</div>
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

  const series = data.series || [];
  const last = series[series.length - 1] || {};
  const prev = series[series.length - 2] || {};

  return (
    <div className="space-y-6" data-testid="page-dso-dpo">
      <div>
        <p className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-0.5">
          WORKING CAPITAL
        </p>
        <h2 className="page-title">DSO / DPO</h2>
        <p className="text-sm text-aegisaxiom-text-secondary mt-1">
          Days Sales Outstanding and Days Payable Outstanding — operational efficiency over the last
          12 months.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Stat label="Days Sales Outstanding" value={last.dso} prev={prev.dso} />
        <Stat label="Days Payable Outstanding" value={last.dpo} prev={prev.dpo} />
      </div>

      <div className="glass-card p-6">
        <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-1">
          12-MONTH TREND
        </div>
        <div className="text-[18px] font-semibold text-aegisaxiom-text mb-4">DSO vs DPO</div>
        <div style={{ width: '100%', height: 320 }}>
          <ResponsiveContainer>
            <LineChart data={series} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
              <CartesianGrid
                strokeDasharray="2 4"
                vertical={false}
                className="stroke-aegisaxiom-border"
              />
              <XAxis
                dataKey="month"
                axisLine={false}
                tickLine={false}
                tick={{ fontSize: 11, fill: 'var(--aegisaxiom-text-muted)' }}
                tickFormatter={monthLabel}
              />
              <YAxis
                axisLine={false}
                tickLine={false}
                tick={{ fontSize: 11, fill: 'var(--aegisaxiom-text-muted)' }}
                tickFormatter={(v) => `${v}d`}
              />
              <Tooltip content={<ChartTooltip />} />
              <Line
                type="monotone"
                dataKey="dso"
                name="DSO"
                stroke="#7c3aed"
                strokeWidth={2}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="dpo"
                name="DPO"
                stroke="#6172F3"
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="glass-card overflow-hidden">
        {/* Desktop table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-aegisaxiom-border bg-aegisaxiom-elevated/40">
                {['Month', 'Revenue', 'Expense', 'DSO', 'DPO'].map((h, i) => (
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
              {series.map((r, i) => (
                <tr key={i} className="border-b border-aegisaxiom-border/50 hover:bg-aegisaxiom-elevated/30">
                  <td className="px-5 py-2 font-mono text-aegisaxiom-text">{r.month}</td>
                  <td className="px-5 py-2 text-right font-mono text-aegisaxiom-text">
                    {fmtCurrency(r.revenue, { compact: true })}
                  </td>
                  <td className="px-5 py-2 text-right font-mono text-aegisaxiom-text">
                    {fmtCurrency(r.expense, { compact: true })}
                  </td>
                  <td className="px-5 py-2 text-right font-mono text-aegisaxiom-text">
                    {r.dso?.toFixed(1)} d
                  </td>
                  <td className="px-5 py-2 text-right font-mono text-aegisaxiom-text">
                    {r.dpo?.toFixed(1)} d
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Mobile card list */}
        <div className="md:hidden">
          {series.length === 0 ? (
            <div className="px-5 py-10 text-center text-[13px] text-aegisaxiom-text-muted">
              No data yet.
            </div>
          ) : (
            <div className="p-3 flex flex-col gap-2.5">
              {series.map((r, i) => (
                <div
                  key={i}
                  className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3"
                  data-testid={`dso-dpo-card-${r.month || i}`}
                >
                  <div className="flex items-center justify-between mb-2.5">
                    <p className="text-[13px] font-mono font-medium text-aegisaxiom-text">{r.month}</p>
                    <div className="flex items-center gap-3 text-[12px] font-mono">
                      <span className="text-violet-400">DSO {r.dso?.toFixed(1)}d</span>
                      <span className="text-blue-400">DPO {r.dpo?.toFixed(1)}d</span>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2 pt-2 border-t border-aegisaxiom-border">
                    <div>
                      <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                        Revenue
                      </p>
                      <p className="text-[12px] font-mono text-aegisaxiom-text">
                        {fmtCurrency(r.revenue, { compact: true })}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                        Expense
                      </p>
                      <p className="text-[12px] font-mono text-aegisaxiom-text">
                        {fmtCurrency(r.expense, { compact: true })}
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
