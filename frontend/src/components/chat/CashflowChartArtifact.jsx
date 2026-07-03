import React from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { TrendingUp } from 'lucide-react';

// Brand colors mirror VelocityChart.js / FinancePortal.js usage:
// AEGISAXIOM violet for the primary line, brighter violet for the
// starting-cash baseline, green/red for the optional revenue/cost
// overlays, neutral border via CSS var.
const COLOR_ENDING_CASH = '#6B2BA8'; // aegisaxiom violet (primary)
const COLOR_BASELINE = '#8b5cf6'; // lighter violet (starting-cash ref line)
const COLOR_REVENUE = '#16a34a'; // green-600
const COLOR_COSTS = '#dc2626'; // red-600
const COLOR_ZERO_LINE = '#9ca3af'; // gray-400

function formatDollars(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  const sign = n < 0 ? '-' : '';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 10_000) return `${sign}$${Math.round(abs / 1000)}K`;
  return `${sign}$${abs.toLocaleString()}`;
}

function formatWeekStarting(iso) {
  if (!iso || typeof iso !== 'string') return '';
  // iso is YYYY-MM-DD. Build "MMM D" without date-fns to avoid a new dep.
  const parts = iso.split('-');
  if (parts.length !== 3) return iso;
  const [, mm, dd] = parts;
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const monthIdx = parseInt(mm, 10) - 1;
  const dayNum = parseInt(dd, 10);
  if (Number.isNaN(monthIdx) || Number.isNaN(dayNum) || monthIdx < 0 || monthIdx > 11) return iso;
  return `${months[monthIdx]} ${dayNum}`;
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;
  const row = payload[0]?.payload || {};
  return (
    <div className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-elevated px-3 py-2 text-xs shadow-lg">
      <div className="font-semibold text-aegisaxiom-text mb-1">
        Week {row.week_number} · {label}
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-aegisaxiom-text-secondary">
        <span style={{ color: COLOR_ENDING_CASH }}>Ending cash</span>
        <span className="text-right font-mono text-aegisaxiom-text">{formatDollars(row.ending_cash)}</span>
        <span style={{ color: COLOR_REVENUE }}>Revenue</span>
        <span className="text-right font-mono text-aegisaxiom-text">{formatDollars(row.revenue)}</span>
        <span style={{ color: COLOR_COSTS }}>Costs</span>
        <span className="text-right font-mono text-aegisaxiom-text">{formatDollars(row.total_costs)}</span>
      </div>
    </div>
  );
}

export default function CashflowChartArtifact({ artifact }) {
  const payload = artifact?.payload || {};
  const weeks = Array.isArray(payload.weeks) ? payload.weeks : [];
  const startingCash = payload.starting_cash;
  const horizonWeeks = payload.horizon_weeks;
  const projectionRunId = payload.projection_run_id;

  const chartData = weeks.map((w) => ({
    ...w,
    week_starting_label: formatWeekStarting(w.week_starting),
  }));

  const endingCashAtHorizon = weeks.length ? weeks[weeks.length - 1].ending_cash : null;

  return (
    <div className="my-3 rounded-xl border border-aegisaxiom-border bg-aegisaxiom-elevated overflow-hidden">
      <div className="px-5 py-3 border-b border-aegisaxiom-border bg-gradient-to-r from-violet-600/10 to-purple-600/10">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <TrendingUp size={18} className="text-violet-600 dark:text-violet-400" />
            <span className="text-sm font-semibold text-aegisaxiom-text">
              {horizonWeeks ? `${horizonWeeks}-week cash flow forecast` : 'Cash flow forecast'}
            </span>
          </div>
          <div className="flex items-center gap-4 text-xs text-aegisaxiom-text-secondary">
            <span>
              Start <span className="font-mono text-aegisaxiom-text">{formatDollars(startingCash)}</span>
            </span>
            <span>
              End <span className="font-mono text-aegisaxiom-text">{formatDollars(endingCashAtHorizon)}</span>
            </span>
          </div>
        </div>
        {projectionRunId && (
          <div className="mt-1 text-[10px] font-mono text-aegisaxiom-text-muted">{projectionRunId}</div>
        )}
      </div>

      <div className="px-2 py-3" style={{ width: '100%', height: 300 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={chartData}
            margin={{ top: 8, right: 16, left: 8, bottom: 8 }}
          >
            <CartesianGrid stroke="var(--aegisaxiom-border)" strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="week_starting_label"
              tick={{ fill: 'var(--aegisaxiom-text-secondary)', fontSize: 11 }}
              stroke="var(--aegisaxiom-border)"
              interval="preserveStartEnd"
            />
            <YAxis
              tick={{ fill: 'var(--aegisaxiom-text-secondary)', fontSize: 11 }}
              stroke="var(--aegisaxiom-border)"
              tickFormatter={formatDollars}
              width={60}
            />
            <Tooltip content={<ChartTooltip />} />
            <Legend
              wrapperStyle={{ fontSize: 11, paddingTop: 4 }}
              iconType="line"
            />
            {/* Reference lines: zero (negative-cash visibility) + starting cash baseline. */}
            <ReferenceLine y={0} stroke={COLOR_ZERO_LINE} strokeDasharray="4 2" />
            {typeof startingCash === 'number' && (
              <ReferenceLine
                y={startingCash}
                stroke={COLOR_BASELINE}
                strokeDasharray="4 2"
                strokeOpacity={0.5}
              />
            )}
            <Line
              type="monotone"
              dataKey="ending_cash"
              name="Ending cash"
              stroke={COLOR_ENDING_CASH}
              strokeWidth={2.5}
              dot={false}
              activeDot={{ r: 4 }}
            />
            <Line
              type="monotone"
              dataKey="revenue"
              name="Revenue"
              stroke={COLOR_REVENUE}
              strokeWidth={1.25}
              dot={false}
              strokeOpacity={0.8}
            />
            <Line
              type="monotone"
              dataKey="total_costs"
              name="Costs"
              stroke={COLOR_COSTS}
              strokeWidth={1.25}
              dot={false}
              strokeOpacity={0.8}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
