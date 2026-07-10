import React, { useEffect, useState } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import {
  Wallet,
  TrendingUp,
  Activity,
  Receipt,
  FileText,
  Timer,
  Gauge,
} from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { fmtCurrency, fmtNumber, fmtPercent } from '@/lib/financeFormat';
import { KPICard } from '@/components/finance/shared/KPICard';

const BASE = `${API_URL}/api/finance`;

const monthLabel = (ym) => {
  if (!ym) return '';
  const [y, m] = ym.split('-');
  return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString('en-US', { month: 'short' });
};

const CHART_COLORS = { revenue: '#7c3aed', expense: '#6172F3' };

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface px-3 py-2 shadow-sm text-[12px]">
      <div className="font-mono text-[11px] text-aegisaxiom-text-muted mb-1">{label}</div>
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-3">
          <span className="h-2 w-2 rounded-sm" style={{ background: p.color }} />
          <span className="text-aegisaxiom-text-secondary">{p.name}</span>
          <span className="font-mono text-aegisaxiom-text ml-auto">{fmtCurrency(p.value, { compact: true })}</span>
        </div>
      ))}
    </div>
  );
}

function Legend({ color, label }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-aegisaxiom-text-secondary">
      <span className="h-2 w-2 rounded-sm" style={{ background: color }} />
      {label}
    </span>
  );
}

function aggregateBuckets(report) {
  if (!report) return null;
  const items = (report.line_items || []).filter((li) => !li.is_summary);
  const acc = { current: 0, d_1_30: 0, d_31_60: 0, d_61_90: 0, d_91: 0 };
  items.forEach((li) => {
    acc.current += parseFloat(li.Current || 0) || 0;
    acc.d_1_30 += parseFloat(li['1 - 30'] || 0) || 0;
    acc.d_31_60 += parseFloat(li['31 - 60'] || 0) || 0;
    acc.d_61_90 += parseFloat(li['61 - 90'] || 0) || 0;
    acc.d_91 += parseFloat(li['91 and over'] || 0) || 0;
  });
  return acc;
}

function BucketBar({ title, total, buckets }) {
  if (!buckets) return null;
  const sum = total || Object.values(buckets).reduce((a, b) => a + b, 0);
  const pct = (n) => (sum ? (n / sum) * 100 : 0);
  const segments = [
    { label: 'Current', value: buckets.current, color: '#22c55e' },
    { label: '1-30', value: buckets.d_1_30, color: '#84cc16' },
    { label: '31-60', value: buckets.d_31_60, color: '#f59e0b' },
    { label: '61-90', value: buckets.d_61_90, color: '#f97316' },
    { label: '91+', value: buckets.d_91, color: '#ef4444' },
  ];
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-[13px] font-medium text-aegisaxiom-text">{title}</span>
        <span className="text-[12px] font-mono text-aegisaxiom-text-secondary">{fmtCurrency(sum, { compact: true })}</span>
      </div>
      <div className="h-2.5 w-full rounded-full bg-aegisaxiom-elevated flex overflow-hidden">
        {segments.map((s) => (
          <div
            key={s.label}
            className="h-full transition-all"
            style={{ width: `${pct(s.value)}%`, background: s.color }}
            title={`${s.label}: ${fmtCurrency(s.value)}`}
          />
        ))}
      </div>
      <div className="mt-3 grid grid-cols-5 gap-2 text-[11px]">
        {segments.map((s) => (
          <div key={s.label} className="flex flex-col">
            <div className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-sm" style={{ background: s.color }} />
              <span className="text-aegisaxiom-text-muted">{s.label}</span>
            </div>
            <span className="font-mono text-[11px] text-aegisaxiom-text-secondary mt-0.5">
              {fmtCurrency(s.value, { compact: true })}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function TopList({ title, rows, onSelectId, color = '#7c3aed' }) {
  const max = Math.max(...rows.map((r) => Math.abs(r.value || 0)), 1);
  return (
    <div className="glass-card p-6">
      <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-1">
        {title.split('·')[0].trim().toUpperCase()}
      </div>
      <div className="text-[16px] font-semibold text-aegisaxiom-text mb-4">{title}</div>
      <div className="space-y-2.5">
        {rows.slice(0, 8).map((r, i) => (
          <div key={`${r.id || r.name}-${i}`} className="group">
            <div className="flex items-center gap-3 text-[13px]">
              <span className="w-6 font-mono text-[11px] text-aegisaxiom-text-muted tabular-nums">{i + 1}</span>
              <button
                onClick={() => r.id && onSelectId && onSelectId(r.id)}
                className="flex-1 truncate text-left text-aegisaxiom-text hover:underline underline-offset-2 cursor-pointer"
              >
                {r.name}
              </button>
              <span className="font-mono text-[12px] text-aegisaxiom-text tabular-nums">
                {fmtCurrency(Math.abs(r.value || 0), { compact: true })}
              </span>
            </div>
            <div className="mt-1.5 h-1 w-full rounded-full bg-aegisaxiom-elevated overflow-hidden">
              <div
                className="h-full"
                style={{ width: `${(Math.abs(r.value || 0) / max) * 100}%`, background: color, transition: 'width 400ms ease-out' }}
              />
            </div>
          </div>
        ))}
        {rows.length === 0 && (
          <p className="text-[13px] text-aegisaxiom-text-muted py-4 text-center">No data available.</p>
        )}
      </div>
    </div>
  );
}

export default function CommandCenterTab({ onSelectCustomer, onSelectVendor }) {
  const [kpis, setKpis] = useState(null);
  const [trend, setTrend] = useState([]);
  const [byCustomer, setByCustomer] = useState([]);
  const [byVendor, setByVendor] = useState([]);
  const [arAging, setArAging] = useState(null);
  const [apAging, setApAging] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  const refresh = async () => {
    try {
      const [k, t, c, v, ar, ap] = await Promise.all([
        api.get(`${BASE}/dashboard/kpis`),
        api.get(`${BASE}/revenue/trend?months=12`),
        api.get(`${BASE}/revenue/by-customer?limit=8`),
        api.get(`${BASE}/revenue/by-vendor?limit=8`),
        api.get(`${BASE}/working-capital/ar-aging`),
        api.get(`${BASE}/working-capital/ap-aging`),
      ]);
      if (!k.ok) {
        setLoadError(`Failed to load command center (${k.status})`);
        return;
      }
      setKpis(await k.json());
      if (t.ok) { const d = await t.json(); setTrend(d.series || []); }
      if (c.ok) { const d = await c.json(); setByCustomer(d.rows || []); }
      if (v.ok) { const d = await v.json(); setByVendor(d.rows || []); }
      if (ar.ok) setArAging(await ar.json());
      if (ap.ok) setApAging(await ap.json());
      setLoadError(null);
    } catch (e) {
      setLoadError(e?.message || 'Failed to load command center');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); }, []);

  if (loading) {
    return (
      <div className="py-20 text-center text-sm text-aegisaxiom-text-muted" data-testid="loading-command-center">
        Loading command center…
      </div>
    );
  }
  if (loadError) {
    return (
      <div className="glass-card p-12 text-center" data-testid="command-center-load-error">
        <div className="text-[16px] font-semibold text-aegisaxiom-text mb-2">Couldn't load command center</div>
        <div className="text-[13px] text-aegisaxiom-text-secondary mb-6">{loadError}</div>
        <button onClick={refresh} className="btn-secondary inline-flex items-center gap-2 h-9 px-4 text-[13px]">Try again</button>
      </div>
    );
  }
  if (!kpis) return null;

  const arBuckets = aggregateBuckets(arAging);
  const apBuckets = aggregateBuckets(apAging);

  return (
    <div className="space-y-8" data-testid="page-command-center">
      {/* Header */}
      <div>
        <p className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-0.5">OVERVIEW</p>
        <h2 className="page-title">Command Center</h2>
        <p className="text-sm text-aegisaxiom-text-secondary mt-1">
          Real-time view of organizational financial health · As of {kpis.as_of || 'today'}
        </p>
      </div>

      {/* KPI Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <KPICard
          label="Cash Balance"
          value={fmtCurrency(kpis.cash_balance, { compact: true })}
          hint="Across all bank accounts"
          accent={<Wallet className="h-4 w-4" />}
          testid="kpi-cash"
        />
        <KPICard
          label="Net Income · YTD"
          value={fmtCurrency(kpis.net_income, { compact: true })}
          delta={kpis.net_income_yoy_pct}
          deltaLabel="vs prior year"
          hint={`Margin ${fmtPercent(kpis.net_margin_pct)}`}
          accent={<Activity className="h-4 w-4" />}
          testid="kpi-net-income"
        />
        <KPICard
          label="Total Income · YTD"
          value={fmtCurrency(kpis.total_income, { compact: true })}
          delta={kpis.income_yoy_pct}
          deltaLabel="vs prior year"
          hint={`Gross margin ${fmtPercent(kpis.gross_margin_pct)}`}
          accent={<TrendingUp className="h-4 w-4" />}
          testid="kpi-income"
        />
        <KPICard
          label="Cash Runway"
          value={kpis.cash_runway_months != null ? `${kpis.cash_runway_months} mo` : '—'}
          hint="Based on average burn"
          accent={<Timer className="h-4 w-4" />}
          testid="kpi-runway"
        />
        <KPICard
          label="Current Ratio"
          value={kpis.current_ratio != null ? fmtNumber(kpis.current_ratio, { decimals: 2 }) : '—'}
          hint="Current Assets / Liabilities"
          accent={<Gauge className="h-4 w-4" />}
          testid="kpi-current-ratio"
        />
        <KPICard
          label="Quick Ratio"
          value={kpis.quick_ratio != null ? fmtNumber(kpis.quick_ratio, { decimals: 2 }) : '—'}
          hint="(Cash + AR) / Current Liabilities"
          accent={<Gauge className="h-4 w-4" />}
          testid="kpi-quick-ratio"
        />
        <KPICard
          label="Accounts Receivable"
          value={fmtCurrency(kpis.ar_total, { compact: true })}
          hint={`${fmtCurrency(kpis.ar_overdue, { compact: true })} overdue`}
          accent={<Receipt className="h-4 w-4" />}
          testid="kpi-ar"
        />
        <KPICard
          label="Accounts Payable"
          value={fmtCurrency(kpis.ap_total, { compact: true })}
          hint="Outstanding bills"
          accent={<FileText className="h-4 w-4" />}
          testid="kpi-ap"
        />
      </div>

      {/* Revenue/Expense Trend + AR/AP Exposure */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 glass-card p-6" data-testid="card-trend">
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-1">
                REVENUE &amp; EXPENSE
              </div>
              <div className="text-[18px] font-semibold text-aegisaxiom-text">12-month trend</div>
            </div>
            <div className="flex items-center gap-3">
              <Legend color={CHART_COLORS.revenue} label="Revenue" />
              <Legend color={CHART_COLORS.expense} label="Expense" />
            </div>
          </div>
          <div style={{ width: '100%', height: 280 }}>
            <ResponsiveContainer>
              <AreaChart data={trend} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
                <defs>
                  <linearGradient id="cc-grevenue" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={CHART_COLORS.revenue} stopOpacity={0.18} />
                    <stop offset="100%" stopColor={CHART_COLORS.revenue} stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="cc-gexpense" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={CHART_COLORS.expense} stopOpacity={0.18} />
                    <stop offset="100%" stopColor={CHART_COLORS.expense} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="var(--aegisaxiom-border)" strokeDasharray="2 4" vertical={false} />
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
                  tickFormatter={(v) => fmtCurrency(v, { compact: true })}
                />
                <Tooltip content={<ChartTooltip />} />
                <Area
                  type="monotone"
                  dataKey="revenue"
                  name="Revenue"
                  stroke={CHART_COLORS.revenue}
                  fill="url(#cc-grevenue)"
                  strokeWidth={2}
                />
                <Area
                  type="monotone"
                  dataKey="expense"
                  name="Expense"
                  stroke={CHART_COLORS.expense}
                  fill="url(#cc-gexpense)"
                  strokeWidth={2}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* AR/AP Buckets */}
        <div className="glass-card p-6 flex flex-col" data-testid="card-aging-summary">
          <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-1">
            WORKING CAPITAL
          </div>
          <div className="text-[18px] font-semibold text-aegisaxiom-text mb-4">AR / AP exposure</div>

          <BucketBar title="Receivables" total={kpis.ar_total} buckets={arBuckets} />
          <div className="my-5 border-t border-aegisaxiom-border" />
          <BucketBar title="Payables" total={kpis.ap_total} buckets={apBuckets} />
        </div>
      </div>

      {/* Top Customers / Vendors */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <TopList
          title="Top customers by income · YTD"
          rows={byCustomer.map((c) => ({ id: c.customer_id, name: c.customer, value: c.income }))}
          onSelectId={onSelectCustomer}
          color={CHART_COLORS.revenue}
        />
        <TopList
          title="Top vendors by expense · YTD"
          rows={byVendor.map((v) => ({ id: v.vendor_id, name: v.vendor, value: v.total }))}
          onSelectId={onSelectVendor}
          color={CHART_COLORS.expense}
        />
      </div>
    </div>
  );
}
