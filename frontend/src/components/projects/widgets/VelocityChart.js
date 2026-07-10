import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { format, parseISO } from 'date-fns';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  Cell,
  ResponsiveContainer,
} from 'recharts';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';

// Wave 6 / 6B4 — bar chart of last N closed sprints' completed-points.
//
// Two embed contexts:
//   - SprintReviewView header (single-project): pass `projectId` to scope
//     the query.
//   - DashboardTab (agile portfolio): omit `projectId` to pull closed
//     sprints across all agile projects. Sprints render newest-on-the-right
//     so the trend line reads left-to-right like every other velocity chart
//     a PM has seen.
//
// We don't depend on a velocity-specific dashboard endpoint — the existing
// /api/projects/sprints listing already returns `completed_points` once a
// sprint has been closed via /close. That keeps this widget shippable in
// the same wave without a parallel backend change.

const DEFAULT_LIMIT = 10;
const HIGHLIGHT_COLOR = '#6B2BA8'; // aegisaxiom violet
const NORMAL_COLOR = '#8b5cf6';
const AVG_COLOR = '#facc15';

function shortLabel(s) {
  // Try the end-date for ordering hints (e.g. "Mar 14"), fall back to
  // sprint name truncated.
  if (s.end_date) {
    try {
      return format(parseISO(s.end_date), 'MMM d');
    } catch {
      // ignore
    }
  }
  return (s.name ?? '').slice(0, 10);
}

export default function VelocityChart({
  projectId,
  limit = DEFAULT_LIMIT,
  highlightSprintId,
  className,
  testid = 'velocity-chart',
}) {
  const [sprints, setSprints] = useState(null);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    setError(false);
    try {
      const params = new URLSearchParams();
      params.set('status', 'closed');
      // Pull more than `limit` and slice client-side; the API sorts by
      // start_date desc, so we need the most-recent N regardless of the
      // limit param's behaviour.
      params.set('limit', String(Math.max(limit * 2, 25)));
      if (projectId) params.set('project_id', projectId);
      const res = await api.get(`${API_URL}/api/projects/sprints?${params.toString()}`);
      if (!res.ok) {
        setError(true);
        return;
      }
      const data = await res.json();
      const items = Array.isArray(data) ? data : data.items ?? [];
      setSprints(items);
    } catch {
      setError(true);
    }
  }, [projectId, limit]);

  useEffect(() => {
    load();
  }, [load]);

  const chartData = useMemo(() => {
    if (!sprints) return [];
    // Sort oldest → newest by end_date (or start_date as fallback) and take
    // the last `limit` entries so the most recent sit on the right.
    const sorted = [...sprints]
      .filter((s) => typeof s.completed_points === 'number')
      .sort((a, b) => {
        const av = a.end_date ?? a.start_date ?? '';
        const bv = b.end_date ?? b.start_date ?? '';
        return av.localeCompare(bv);
      });
    return sorted.slice(-limit).map((s) => ({
      sprint_id: s.sprint_id,
      label: shortLabel(s),
      name: s.name,
      points: s.completed_points ?? 0,
      capacity: s.capacity_points ?? null,
    }));
  }, [sprints, limit]);

  const avg = useMemo(() => {
    if (chartData.length === 0) return 0;
    const total = chartData.reduce((acc, d) => acc + d.points, 0);
    return Math.round(total / chartData.length);
  }, [chartData]);

  if (sprints === null && !error) {
    return (
      <div
        className={`flex items-center justify-center text-xs text-aegisaxiom-text-muted ${className ?? ''}`}
        data-testid={`${testid}-loading`}
        style={{ minHeight: 120 }}
      >
        Loading velocity…
      </div>
    );
  }

  if (error) {
    return (
      <div
        className={`flex items-center justify-center text-xs text-aegisaxiom-text-muted ${className ?? ''}`}
        data-testid={`${testid}-error`}
        style={{ minHeight: 120 }}
      >
        Couldn&apos;t load velocity.
      </div>
    );
  }

  if (chartData.length === 0) {
    return (
      <div
        className={`flex items-center justify-center text-xs text-aegisaxiom-text-muted ${className ?? ''}`}
        data-testid={`${testid}-empty`}
        style={{ minHeight: 120 }}
      >
        Close a sprint to start tracking velocity.
      </div>
    );
  }

  return (
    <div className={`w-full ${className ?? ''}`} data-testid={testid}>
      <div style={{ width: '100%', height: 160 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 8, right: 8, left: -10, bottom: 4 }}>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="var(--aegisaxiom-border)"
              vertical={false}
            />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: 'var(--aegisaxiom-text-secondary)' }}
              axisLine={{ stroke: 'var(--aegisaxiom-border)' }}
              tickLine={false}
            />
            <YAxis
              allowDecimals={false}
              tick={{ fontSize: 10, fill: 'var(--aegisaxiom-text-secondary)' }}
              axisLine={{ stroke: 'var(--aegisaxiom-border)' }}
              tickLine={false}
            />
            <Tooltip
              cursor={{ fill: 'rgba(107,43,168,0.08)' }}
              contentStyle={{
                backgroundColor: 'var(--aegisaxiom-surface)',
                border: '1px solid var(--aegisaxiom-border)',
                borderRadius: 6,
                fontSize: 12,
                color: 'var(--aegisaxiom-text-primary)',
              }}
              labelStyle={{ color: 'var(--aegisaxiom-text-primary)', fontWeight: 600 }}
              formatter={(value, _name, ctx) => {
                const cap = ctx?.payload?.capacity;
                if (typeof cap === 'number' && cap > 0) {
                  return [`${value} pts (cap ${cap})`, ctx.payload.name];
                }
                return [`${value} pts`, ctx.payload.name];
              }}
            />
            <ReferenceLine
              y={avg}
              stroke={AVG_COLOR}
              strokeDasharray="4 2"
              ifOverflow="extendDomain"
              label={{
                value: `avg ${avg}`,
                position: 'right',
                fill: AVG_COLOR,
                fontSize: 10,
              }}
            />
            <Bar dataKey="points" radius={[4, 4, 0, 0]} isAnimationActive={false}>
              {chartData.map((entry) => (
                <Cell
                  key={entry.sprint_id}
                  fill={
                    highlightSprintId && entry.sprint_id === highlightSprintId
                      ? HIGHLIGHT_COLOR
                      : NORMAL_COLOR
                  }
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
