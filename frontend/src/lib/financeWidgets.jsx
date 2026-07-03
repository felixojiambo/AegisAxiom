// Widget Registry — 20 finance widgets available in the Workspace.
// Ported from source lib/widgets.jsx. API calls use AEGISAXIOM safeXhr pattern.
import React, { useEffect, useState } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  LineChart,
  Line,
  BarChart,
  Bar,
  Cell as RCell,
} from 'recharts';
import {
  Wallet,
  TrendingUp,
  TrendingDown,
  Activity,
  Receipt,
  FileText,
  Banknote,
  Gauge,
  Timer,
  PieChart as PieIcon,
  Users,
  Building2,
  ArrowUpRight,
  ArrowDownRight,
} from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { fmtCurrency, fmtPercent, fmtNumber } from '@/lib/financeFormat';

const BASE = `${API_URL}/api/finance`;

const monthLabel = (ym) => {
  if (!ym) return '';
  const [y, m] = ym.split('-');
  return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString('en-US', { month: 'short' });
};

// ---------- Tooltip helpers ----------
const ChartTooltipMoney = ({ active, payload, label }) => {
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
};

const ChartTooltipDays = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface px-3 py-2 shadow-sm text-[12px]">
      <div className="font-mono text-[11px] text-aegisaxiom-text-muted mb-1">{label}</div>
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-3">
          <span className="h-2 w-2 rounded-sm" style={{ background: p.color }} />
          <span className="text-aegisaxiom-text-secondary">{p.name}</span>
          <span className="font-mono text-aegisaxiom-text ml-auto">{fmtNumber(p.value, { decimals: 1 })} d</span>
        </div>
      ))}
    </div>
  );
};

// ---------- KPI primitive ----------
function KPIBlock({ label, value, hint, delta, icon: Icon, invertDelta }) {
  let deltaCls = 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted';
  let DeltaIcon = ArrowUpRight;
  if (delta != null && !isNaN(delta)) {
    const positive = invertDelta ? delta < 0 : delta > 0;
    const negative = invertDelta ? delta > 0 : delta < 0;
    deltaCls = positive
      ? 'bg-green-500/15 text-green-400'
      : negative
        ? 'bg-red-500/15 text-red-400'
        : 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted';
    DeltaIcon = positive ? ArrowUpRight : ArrowDownRight;
  }
  return (
    <div className="h-full flex flex-col justify-between">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">{label}</span>
        {Icon ? <Icon className="h-4 w-4 text-aegisaxiom-text-muted" /> : null}
      </div>
      <div className="flex items-baseline gap-2 mt-3">
        <div className="text-[28px] tracking-tight font-semibold tabular-nums text-aegisaxiom-text">
          {value}
        </div>
        {delta != null && !isNaN(delta) ? (
          <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium font-mono ${deltaCls}`}>
            <DeltaIcon className="h-3 w-3" />
            <span>{fmtPercent(Math.abs(delta), 1)}</span>
          </span>
        ) : null}
      </div>
      {hint ? <div className="text-[12px] text-aegisaxiom-text-muted mt-1">{hint}</div> : null}
    </div>
  );
}

// Generic data hook — fetches once on mount + when deps change
function useFetch(fetcher, deps = []) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let cancel = false;
    fetcher()
      .then((d) => !cancel && setData(d))
      .catch((e) => !cancel && setErr(e));
    return () => { cancel = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, err };
}

function Loader() {
  return <div className="h-full grid place-items-center text-[12px] text-aegisaxiom-text-muted">Loading…</div>;
}

// Fetch via AEGISAXIOM api — returns parsed data or throws
async function apiFetch(path) {
  const res = await api.get(`${BASE}${path}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ---------- Reusable chart wrappers ----------
function AreaTrend({ data, dataKeys, colors }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
        <defs>
          {dataKeys.map((k, i) => (
            <linearGradient key={k} id={`fw-grd-${k}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={colors[i]} stopOpacity={0.18} />
              <stop offset="100%" stopColor={colors[i]} stopOpacity={0} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid strokeDasharray="2 4" vertical={false} className="stroke-aegisaxiom-border" />
        <XAxis dataKey="month" axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: 'var(--aegisaxiom-text-muted)' }} tickFormatter={monthLabel} />
        <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: 'var(--aegisaxiom-text-muted)' }} tickFormatter={(v) => fmtCurrency(v, { compact: true })} width={60} />
        <Tooltip content={<ChartTooltipMoney />} />
        {dataKeys.map((k, i) => (
          <Area key={k} type="monotone" dataKey={k} name={k.charAt(0).toUpperCase() + k.slice(1)} stroke={colors[i]} fill={`url(#fw-grd-${k})`} strokeWidth={2} />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}

function LineTrend({ data, dataKeys, colors }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid strokeDasharray="2 4" vertical={false} className="stroke-aegisaxiom-border" />
        <XAxis dataKey="month" axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: 'var(--aegisaxiom-text-muted)' }} tickFormatter={monthLabel} />
        <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: 'var(--aegisaxiom-text-muted)' }} tickFormatter={(v) => `${v}d`} width={42} />
        <Tooltip content={<ChartTooltipDays />} />
        {dataKeys.map((k, i) => (
          <Line key={k} type="monotone" dataKey={k} name={k.toUpperCase()} stroke={colors[i]} strokeWidth={2} dot={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

function AgingBars({ data, color }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid strokeDasharray="2 4" vertical={false} className="stroke-aegisaxiom-border" />
        <XAxis dataKey="bucket" axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: 'var(--aegisaxiom-text-muted)' }} />
        <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: 'var(--aegisaxiom-text-muted)' }} tickFormatter={(v) => fmtCurrency(v, { compact: true })} width={60} />
        <Tooltip content={<ChartTooltipMoney />} />
        <Bar dataKey="value" name="Total" radius={[4, 4, 0, 0]}>
          {data.map((d, i) => (
            <RCell key={i} fill={d.color || color} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function HorizontalList({ rows, color }) {
  const max = Math.max(...rows.map((r) => Math.abs(r.value || 0)), 1);
  return (
    <div className="space-y-2 overflow-auto h-full">
      {rows.map((r, i) => (
        <div key={i}>
          <div className="flex items-center gap-3 text-[12.5px]">
            <span className="w-5 font-mono text-[10px] text-aegisaxiom-text-muted tabular-nums">{i + 1}</span>
            <span className="flex-1 truncate text-aegisaxiom-text">{r.name}</span>
            <span className="font-mono text-[12px] text-aegisaxiom-text tabular-nums">{fmtCurrency(Math.abs(r.value || 0), { compact: true })}</span>
          </div>
          <div className="mt-1.5 h-1 w-full rounded-full bg-aegisaxiom-elevated overflow-hidden">
            <div className="h-full transition-all duration-500" style={{ width: `${(Math.abs(r.value || 0) / max) * 100}%`, background: color }} />
          </div>
        </div>
      ))}
    </div>
  );
}

// ============================================================
// Aggregating helpers (for aging buckets)
// ============================================================
const aggregateBuckets = (lineItems = []) => {
  const acc = { Current: 0, '1 - 30': 0, '31 - 60': 0, '61 - 90': 0, '91 and over': 0 };
  lineItems
    .filter((li) => !li.is_summary)
    .forEach((li) => {
      Object.keys(acc).forEach((k) => {
        acc[k] += parseFloat(li[k] || 0) || 0;
      });
    });
  return [
    { bucket: 'Current', value: acc.Current, color: '#027A48' },
    { bucket: '1–30', value: acc['1 - 30'], color: '#65A30D' },
    { bucket: '31–60', value: acc['31 - 60'], color: '#B54708' },
    { bucket: '61–90', value: acc['61 - 90'], color: '#DC6803' },
    { bucket: '91+', value: acc['91 and over'], color: '#B42318' },
  ];
};

// ============================================================
// Widget Components (20 total)
// ============================================================
function WCash() {
  const { data } = useFetch(() => apiFetch('/dashboard/kpis'));
  if (!data) return <Loader />;
  return <KPIBlock label="Cash Balance" icon={Wallet} value={fmtCurrency(data.cash_balance, { compact: true })} hint="Across all bank accounts" />;
}

function WNetIncome() {
  const { data } = useFetch(() => apiFetch('/dashboard/kpis'));
  if (!data) return <Loader />;
  return <KPIBlock label="Net Income · YTD" icon={Activity} value={fmtCurrency(data.net_income, { compact: true })} hint={`Margin ${fmtPercent(data.net_margin_pct)}`} delta={data.net_income_yoy_pct} />;
}

function WTotalIncome() {
  const { data } = useFetch(() => apiFetch('/dashboard/kpis'));
  if (!data) return <Loader />;
  return <KPIBlock label="Total Income · YTD" icon={TrendingUp} value={fmtCurrency(data.total_income, { compact: true })} hint={`Gross margin ${fmtPercent(data.gross_margin_pct)}`} delta={data.income_yoy_pct} />;
}

function WGrossMargin() {
  const { data } = useFetch(() => apiFetch('/dashboard/kpis'));
  if (!data) return <Loader />;
  return <KPIBlock label="Gross Margin" icon={TrendingUp} value={fmtPercent(data.gross_margin_pct)} hint="Revenue less COGS" />;
}

function WNetMargin() {
  const { data } = useFetch(() => apiFetch('/dashboard/kpis'));
  if (!data) return <Loader />;
  return <KPIBlock label="Net Margin" icon={Activity} value={fmtPercent(data.net_margin_pct)} hint="Net income / revenue" />;
}

function WCashRunway() {
  const { data } = useFetch(() => apiFetch('/dashboard/kpis'));
  if (!data) return <Loader />;
  return <KPIBlock label="Cash Runway" icon={Timer} value={data.cash_runway_months != null ? `${data.cash_runway_months} mo` : '—'} hint="Based on average burn" />;
}

function WCurrentRatio() {
  const { data } = useFetch(() => apiFetch('/dashboard/kpis'));
  if (!data) return <Loader />;
  return <KPIBlock label="Current Ratio" icon={Gauge} value={data.current_ratio != null ? fmtNumber(data.current_ratio, { decimals: 2 }) : '—'} hint="Current Assets / Liabilities" />;
}

function WQuickRatio() {
  const { data } = useFetch(() => apiFetch('/dashboard/kpis'));
  if (!data) return <Loader />;
  return <KPIBlock label="Quick Ratio" icon={Gauge} value={data.quick_ratio != null ? fmtNumber(data.quick_ratio, { decimals: 2 }) : '—'} hint="(Cash + AR) / Current Liab." />;
}

function WAR() {
  const { data } = useFetch(() => apiFetch('/dashboard/kpis'));
  if (!data) return <Loader />;
  return <KPIBlock label="AR Outstanding" icon={Receipt} value={fmtCurrency(data.ar_total, { compact: true })} hint={`${fmtCurrency(data.ar_overdue, { compact: true })} overdue`} />;
}

function WAP() {
  const { data } = useFetch(() => apiFetch('/dashboard/kpis'));
  if (!data) return <Loader />;
  return <KPIBlock label="AP Outstanding" icon={FileText} value={fmtCurrency(data.ap_total, { compact: true })} hint="Open vendor bills" />;
}

function WRevenueTrend() {
  const { data } = useFetch(() => apiFetch('/revenue/trend?months=12'));
  if (!data) return <Loader />;
  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">REVENUE · 12-MONTH</div>
        <Banknote className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
      </div>
      <div className="flex-1 min-h-0">
        <AreaTrend data={data.series} dataKeys={['revenue']} colors={['#7c3aed']} />
      </div>
    </div>
  );
}

function WExpenseTrend() {
  const { data } = useFetch(() => apiFetch('/revenue/trend?months=12'));
  if (!data) return <Loader />;
  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">EXPENSE · 12-MONTH</div>
        <TrendingDown className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
      </div>
      <div className="flex-1 min-h-0">
        <AreaTrend data={data.series} dataKeys={['expense']} colors={['#6172F3']} />
      </div>
    </div>
  );
}

function WRevExp() {
  const { data } = useFetch(() => apiFetch('/revenue/trend?months=12'));
  if (!data) return <Loader />;
  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">REVENUE VS EXPENSE</div>
        <Activity className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
      </div>
      <div className="flex-1 min-h-0">
        <AreaTrend data={data.series} dataKeys={['revenue', 'expense']} colors={['#7c3aed', '#6172F3']} />
      </div>
    </div>
  );
}

function WDsoTrend() {
  const { data } = useFetch(() => apiFetch('/working-capital/dso-dpo?months=12'));
  if (!data) return <Loader />;
  return (
    <div className="h-full flex flex-col">
      <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-2">DSO · 12-MONTH</div>
      <div className="flex-1 min-h-0">
        <LineTrend data={data.series} dataKeys={['dso']} colors={['#7c3aed']} />
      </div>
    </div>
  );
}

function WDpoTrend() {
  const { data } = useFetch(() => apiFetch('/working-capital/dso-dpo?months=12'));
  if (!data) return <Loader />;
  return (
    <div className="h-full flex flex-col">
      <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-2">DPO · 12-MONTH</div>
      <div className="flex-1 min-h-0">
        <LineTrend data={data.series} dataKeys={['dpo']} colors={['#6172F3']} />
      </div>
    </div>
  );
}

function WArAging() {
  const { data } = useFetch(() => apiFetch('/working-capital/ar-aging'));
  if (!data) return <Loader />;
  const buckets = aggregateBuckets(data.line_items);
  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">AR AGING BUCKETS</div>
        <Receipt className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
      </div>
      <div className="flex-1 min-h-0">
        <AgingBars data={buckets} color="#7c3aed" />
      </div>
    </div>
  );
}

function WApAging() {
  const { data } = useFetch(() => apiFetch('/working-capital/ap-aging'));
  if (!data) return <Loader />;
  const buckets = aggregateBuckets(data.line_items);
  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">AP AGING BUCKETS</div>
        <FileText className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
      </div>
      <div className="flex-1 min-h-0">
        <AgingBars data={buckets} color="#6172F3" />
      </div>
    </div>
  );
}

function WTopCustomers() {
  const { data } = useFetch(() => apiFetch('/revenue/by-customer?limit=8'));
  if (!data) return <Loader />;
  const rows = (data.rows || []).map((c) => ({ name: c.customer, value: c.income }));
  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">TOP CUSTOMERS · INCOME YTD</div>
        <Building2 className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
      </div>
      <div className="flex-1 min-h-0">
        <HorizontalList rows={rows} color="#7c3aed" />
      </div>
    </div>
  );
}

function WTopVendors() {
  const { data } = useFetch(() => apiFetch('/revenue/by-vendor?limit=8'));
  if (!data) return <Loader />;
  const rows = (data.rows || []).map((v) => ({ name: v.vendor, value: v.total }));
  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">TOP VENDORS · SPEND YTD</div>
        <Users className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
      </div>
      <div className="flex-1 min-h-0">
        <HorizontalList rows={rows} color="#6172F3" />
      </div>
    </div>
  );
}

function WPnlMini() {
  const { data } = useFetch(() => apiFetch('/dashboard/kpis'));
  if (!data) return <Loader />;
  const rows = [
    { label: 'Total Income', value: data.total_income },
    { label: 'Cost of Goods Sold', value: data.total_income - data.gross_profit },
    { label: 'Gross Profit', value: data.gross_profit, bold: true },
    { label: 'Total Expenses', value: data.total_expenses },
    { label: 'Net Income', value: data.net_income, bold: true, accent: true },
  ];
  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between mb-3">
        <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">P&L SUMMARY · YTD</div>
        <PieIcon className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
      </div>
      <div className="flex-1 space-y-1.5">
        {rows.map((r, i) => (
          <div key={i} className={`flex items-center justify-between py-1 ${r.bold ? 'border-t border-aegisaxiom-border pt-2' : ''}`}>
            <span className={`text-[13px] ${r.bold ? 'font-medium text-aegisaxiom-text' : 'text-aegisaxiom-text-secondary'}`}>{r.label}</span>
            <span className={`font-mono text-[13px] tabular-nums ${r.bold ? 'text-aegisaxiom-text font-semibold' : 'text-aegisaxiom-text'}`}>{fmtCurrency(r.value || 0)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ============================================================
// Catalog (exported) — 20 widgets
// ============================================================
export const WIDGET_CATALOG = [
  { id: 'kpi_cash_balance', name: 'Cash Balance', description: 'Total cash across all bank accounts', category: 'KPI', defaultSize: 'small', Component: WCash },
  { id: 'kpi_net_income_ytd', name: 'Net Income · YTD', description: 'Year-to-date net income with prior-year delta', category: 'KPI', defaultSize: 'small', Component: WNetIncome },
  { id: 'kpi_total_income_ytd', name: 'Total Income · YTD', description: 'Year-to-date revenue with prior-year delta', category: 'KPI', defaultSize: 'small', Component: WTotalIncome },
  { id: 'kpi_gross_margin', name: 'Gross Margin %', description: 'Revenue less COGS as a percentage', category: 'KPI', defaultSize: 'small', Component: WGrossMargin },
  { id: 'kpi_net_margin', name: 'Net Margin %', description: 'Net income as a percentage of revenue', category: 'KPI', defaultSize: 'small', Component: WNetMargin },
  { id: 'kpi_cash_runway', name: 'Cash Runway', description: 'Months of cash at current burn', category: 'KPI', defaultSize: 'small', Component: WCashRunway },
  { id: 'kpi_current_ratio', name: 'Current Ratio', description: 'Liquidity ratio: Current Assets / Current Liabilities', category: 'KPI', defaultSize: 'small', Component: WCurrentRatio },
  { id: 'kpi_quick_ratio', name: 'Quick Ratio', description: '(Cash + AR) / Current Liabilities', category: 'KPI', defaultSize: 'small', Component: WQuickRatio },
  { id: 'kpi_ar', name: 'AR Outstanding', description: 'Total accounts receivable + overdue amount', category: 'KPI', defaultSize: 'small', Component: WAR },
  { id: 'kpi_ap', name: 'AP Outstanding', description: 'Total accounts payable', category: 'KPI', defaultSize: 'small', Component: WAP },
  { id: 'revenue_trend', name: 'Revenue Trend · 12-month', description: 'Monthly revenue area chart', category: 'Trend', defaultSize: 'medium', Component: WRevenueTrend },
  { id: 'expense_trend', name: 'Expense Trend · 12-month', description: 'Monthly expense area chart', category: 'Trend', defaultSize: 'medium', Component: WExpenseTrend },
  { id: 'revenue_vs_expense', name: 'Revenue vs Expense', description: '12-month combined area chart', category: 'Trend', defaultSize: 'large', Component: WRevExp },
  { id: 'dso_trend', name: 'DSO Trend', description: 'Days Sales Outstanding · 12-month', category: 'Trend', defaultSize: 'medium', Component: WDsoTrend },
  { id: 'dpo_trend', name: 'DPO Trend', description: 'Days Payable Outstanding · 12-month', category: 'Trend', defaultSize: 'medium', Component: WDpoTrend },
  { id: 'ar_aging_buckets', name: 'AR Aging Buckets', description: 'AR exposure by aging bucket', category: 'Aging', defaultSize: 'medium', Component: WArAging },
  { id: 'ap_aging_buckets', name: 'AP Aging Buckets', description: 'AP exposure by aging bucket', category: 'Aging', defaultSize: 'medium', Component: WApAging },
  { id: 'top_customers_income', name: 'Top Customers', description: 'Top 8 customers by income YTD', category: 'Lists', defaultSize: 'medium', Component: WTopCustomers },
  { id: 'top_vendors_spend', name: 'Top Vendors', description: 'Top 8 vendors by spend YTD', category: 'Lists', defaultSize: 'medium', Component: WTopVendors },
  { id: 'pnl_mini', name: 'P&L Summary', description: 'Compact income statement view', category: 'Summary', defaultSize: 'medium', Component: WPnlMini },
];

export const WIDGET_BY_ID = Object.fromEntries(WIDGET_CATALOG.map((w) => [w.id, w]));
