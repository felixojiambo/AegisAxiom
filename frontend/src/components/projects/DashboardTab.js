import React, { useCallback, useEffect, useState } from 'react';
import MyDayTile from './smartViews/MyDayTile';
import {
  RefreshCw,
  ArrowUp,
  ArrowDown,
  Minus,
  AlertCircle,
  AlertTriangle,
  Clock,
  Flag,
} from 'lucide-react';
import {
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import VelocityChart from '@/components/projects/widgets/VelocityChart';

// ---- helpers ----

function fmtRelative(iso) {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 2) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function dueDatePill(dueDateStr) {
  if (!dueDateStr) return { text: '—', cls: 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted' };
  const diffDays = Math.ceil(
    (new Date(dueDateStr).getTime() - Date.now()) / 86400000,
  );
  if (diffDays < 0) return { text: 'Overdue', cls: 'bg-red-500/15 text-red-400 font-bold' };
  if (diffDays === 0) return { text: 'Today', cls: 'bg-red-500/15 text-red-400' };
  if (diffDays <= 3) return { text: `${diffDays}d`, cls: 'bg-red-500/15 text-red-400' };
  if (diffDays <= 7) return { text: `${diffDays}d`, cls: 'bg-yellow-500/15 text-yellow-400' };
  return { text: `${diffDays}d`, cls: 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted' };
}

function humanizeMethodology(m) {
  if (m === 'waterfall') return 'Waterfall';
  if (m === 'agile') return 'Agile';
  if (m === 'waves_threads') return 'W&T';
  return m;
}

function humanizeStatus(s) {
  return s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

const STATUS_COLORS_MAP = {
  not_started: '#94a3b8',
  in_progress: '#6B2BA8',
  blocked: '#ef4444',
  complete: '#22c55e',
  scoped: '#94a3b8',
  shipped: '#22c55e',
  carried_forward: '#f59e0b',
};

function statusColor(key) {
  return STATUS_COLORS_MAP[key] ?? '#94a3b8';
}

// ---- Card chrome ----

function DashboardCard({ title, testid, children, onRefresh }) {
  return (
    <div
      data-testid={testid}
      className="glass-card flex flex-col p-5"
    >
      <div className="flex items-center justify-between mb-4 shrink-0">
        <h3
          className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted"
          data-testid={`${testid}-title`}
        >
          {title}
        </h3>
        {onRefresh && (
          <button
            type="button"
            onClick={onRefresh}
            className="h-7 w-7 inline-flex items-center justify-center rounded-md text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors"
            aria-label={`Refresh ${title}`}
          >
            <RefreshCw size={13} />
          </button>
        )}
      </div>
      <div className="flex-1 min-h-0">{children}</div>
    </div>
  );
}

function CardEmpty({ title }) {
  return (
    <p className="text-sm text-aegisaxiom-text-muted py-6 text-center">{title}</p>
  );
}

// ---- Card 1: Project Health ----

const HEALTH_COLORS = {
  green: '#4ade80',
  yellow: '#facc15',
  red: '#f87171',
};

function ProjectHealthCard({ data, onRefresh }) {
  const total = (data?.green ?? 0) + (data?.yellow ?? 0) + (data?.red ?? 0);
  const rows = [
    { key: 'green', label: 'Healthy', value: data?.green ?? 0 },
    { key: 'yellow', label: 'At risk', value: data?.yellow ?? 0 },
    { key: 'red', label: 'Critical', value: data?.red ?? 0 },
  ];
  const pieData = rows
    .filter((r) => r.value > 0)
    .map((r) => ({ name: r.label, value: r.value, color: HEALTH_COLORS[r.key] }));

  return (
    <DashboardCard title="Project Health" testid="dashboard-card-health" onRefresh={onRefresh}>
      {total === 0 ? (
        <CardEmpty title="No projects yet" />
      ) : (
        <div className="flex items-center gap-4 h-[200px]">
          <div className="w-[180px] h-full shrink-0 relative">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={pieData}
                  dataKey="value"
                  nameKey="name"
                  innerRadius="50%"
                  outerRadius="80%"
                  strokeWidth={0}
                  isAnimationActive={false}
                >
                  {pieData.map((entry, idx) => (
                    <Cell key={idx} fill={entry.color} />
                  ))}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
            <div
              className="absolute inset-0 flex items-center justify-center pointer-events-none text-aegisaxiom-text"
              style={{ fontSize: '22px', fontWeight: 700 }}
              data-testid="project-health-total"
            >
              {total}
            </div>
          </div>
          <ul className="flex-1 space-y-2 text-sm">
            {rows.map((r) => {
              const pct = total > 0 ? Math.round((r.value / total) * 100) : 0;
              return (
                <li key={r.key} className="flex items-center gap-2" data-testid={`project-health-legend-${r.key}`}>
                  <span
                    className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
                    style={{ backgroundColor: HEALTH_COLORS[r.key] }}
                  />
                  <span className="text-aegisaxiom-text font-medium">{r.label}</span>
                  <span className="ml-auto text-aegisaxiom-text-secondary">
                    {r.value} <span className="text-xs">({pct}%)</span>
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </DashboardCard>
  );
}

// ---- Card 2: Active Projects ----

function ActiveProjectsCard({ data, onRefresh }) {
  const delta = data?.delta_vs_last_week ?? 0;
  let Icon = Minus;
  let colorCls = 'text-aegisaxiom-text-secondary';
  let label = 'No change this week';
  if (delta > 0) { Icon = ArrowUp; colorCls = 'text-green-400'; label = `+${delta} this week`; }
  else if (delta < 0) { Icon = ArrowDown; colorCls = 'text-red-400'; label = `${delta} this week`; }

  return (
    <DashboardCard title="Active Projects" testid="dashboard-card-active" onRefresh={onRefresh}>
      <div className="flex flex-col justify-center h-[200px]">
        <div className="text-[48px] leading-none font-bold text-aegisaxiom-text" data-testid="active-projects-count">
          {data?.count ?? 0}
        </div>
        <div className={`mt-3 inline-flex items-center gap-1.5 text-sm font-medium ${colorCls}`} data-testid="active-projects-delta">
          <Icon size={14} />
          <span>{label}</span>
        </div>
        <p className="mt-6 text-xs text-aegisaxiom-text-muted">
          Projects currently in <span className="font-mono text-[11px]">active</span> status.
        </p>
      </div>
    </DashboardCard>
  );
}

// ---- Card 3: Tasks by Status ----

function TasksByStatusCard({ rows, onRefresh }) {
  // Filter the backend's self-describing `_unknown` sentinel entry out of
  // the chart — it represents tasks with out-of-enum status that backend
  // logged for debugging, not something a PM should see as a bar.
  const visibleRows = (rows ?? []).filter(
    (row) => row && row.methodology && row.methodology !== '_unknown',
  );
  const allKeys = [];
  const data = visibleRows.map((row) => {
    const entry = { methodology: humanizeMethodology(row.methodology) };
    for (const [status, count] of Object.entries(row.buckets ?? {})) {
      if (!allKeys.includes(status)) allKeys.push(status);
      entry[status] = count;
    }
    return entry;
  });
  const totalTasks = visibleRows.reduce(
    (acc, row) => acc + Object.values(row.buckets ?? {}).reduce((a, b) => a + b, 0),
    0,
  );

  return (
    <DashboardCard title="Tasks by Status" testid="dashboard-card-tasks" onRefresh={onRefresh}>
      {totalTasks === 0 ? (
        <CardEmpty title="No tasks yet" />
      ) : (
        <div className="h-[240px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 8, right: 8, left: -10, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--aegisaxiom-border)" vertical={false} />
              <XAxis
                dataKey="methodology"
                tick={{ fontSize: 11, fill: 'var(--aegisaxiom-text-secondary)' }}
                axisLine={{ stroke: 'var(--aegisaxiom-border)' }}
                tickLine={false}
              />
              <YAxis
                allowDecimals={false}
                tick={{ fontSize: 11, fill: 'var(--aegisaxiom-text-secondary)' }}
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
                itemStyle={{ color: 'var(--aegisaxiom-text-primary)' }}
                labelStyle={{ color: 'var(--aegisaxiom-text-primary)', fontWeight: 600 }}
              />
              <Legend
                iconType="circle"
                wrapperStyle={{ fontSize: 11, paddingTop: 4 }}
                formatter={(value) => (
                  <span style={{ color: 'var(--aegisaxiom-text-secondary)' }}>{humanizeStatus(String(value))}</span>
                )}
              />
              {allKeys.map((key) => (
                <Bar key={key} dataKey={key} stackId="a" fill={statusColor(key)} isAnimationActive={false} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </DashboardCard>
  );
}

// ---- Card 4: Workload by Assignee ----

const MAX_ROWS = 8;

function WorkloadByAssigneeCard({ rows, onRefresh }) {
  const sorted = [...(rows ?? [])].sort((a, b) => b.open_task_count - a.open_task_count);
  const shown = sorted.slice(0, MAX_ROWS);
  const hidden = sorted.length - shown.length;

  return (
    <DashboardCard title="Workload by Assignee" testid="dashboard-card-workload" onRefresh={onRefresh}>
      {shown.length === 0 ? (
        <CardEmpty title="No open tasks assigned yet." />
      ) : (
        <>
          <div className="w-full" style={{ height: Math.max(120, shown.length * 28 + 32) }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={shown} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--aegisaxiom-border)" horizontal={false} />
                <XAxis
                  type="number"
                  allowDecimals={false}
                  tick={{ fontSize: 11, fill: 'var(--aegisaxiom-text-secondary)' }}
                  axisLine={{ stroke: 'var(--aegisaxiom-border)' }}
                  tickLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="display_name"
                  width={100}
                  tick={{ fontSize: 11, fill: 'var(--aegisaxiom-text-secondary)' }}
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
                  itemStyle={{ color: 'var(--aegisaxiom-text-primary)' }}
                  labelStyle={{ color: 'var(--aegisaxiom-text-primary)', fontWeight: 600 }}
                />
                <Bar dataKey="open_task_count" fill="#6B2BA8" isAnimationActive={false} radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          {hidden > 0 && (
            <p className="mt-2 text-xs text-aegisaxiom-text-muted text-center" data-testid="workload-more">
              +{hidden} more
            </p>
          )}
        </>
      )}
    </DashboardCard>
  );
}

// ---- Card 5: Upcoming Deadlines ----

function UpcomingDeadlinesCard({ rows, onRefresh, onProjectClick }) {
  return (
    <DashboardCard title="Upcoming Deadlines" testid="dashboard-card-deadlines" onRefresh={onRefresh}>
      {(rows ?? []).length === 0 ? (
        <CardEmpty title="Nothing due in the next 14 days." />
      ) : (
        <ul className="divide-y divide-aegisaxiom-border" data-testid="deadlines-list">
          {(rows ?? []).map((row) => {
            const pill = dueDatePill(row.due_date);
            return (
              <li key={row.task_id}>
                <button
                  type="button"
                  onClick={() => onProjectClick?.(row.project_id)}
                  className="w-full h-14 flex items-center gap-3 text-left hover:bg-aegisaxiom-elevated transition-colors px-1 rounded-md"
                  data-testid={`deadline-row-${row.task_id}`}
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-aegisaxiom-text truncate">{row.title}</p>
                    <p className="text-[11px] text-aegisaxiom-text-muted truncate">{row.project_name ?? '—'}</p>
                  </div>
                  <span className={`shrink-0 inline-flex items-center h-6 px-2.5 rounded-md text-[11px] ${pill.cls}`}>
                    {pill.text}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </DashboardCard>
  );
}

// ---- Card 6: Recent Activity ----

function statusPillClass(status) {
  if (!status) return 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted';
  if (['complete', 'shipped'].includes(status)) return 'bg-green-500/15 text-green-400';
  if (['blocked', 'red'].includes(status)) return 'bg-red-500/15 text-red-400';
  if (['in_progress', 'active'].includes(status)) return 'bg-blue-500/15 text-blue-400';
  if (['on_hold', 'carried_forward'].includes(status)) return 'bg-yellow-500/15 text-yellow-400';
  return 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted';
}

function MiniStatusPill({ status }) {
  if (!status) return null;
  return (
    <span className={`inline-flex items-center h-5 px-1.5 rounded text-[10px] font-medium ${statusPillClass(status)}`}>
      {humanizeStatus(status)}
    </span>
  );
}

function RecentActivityCard({ rows, onRefresh }) {
  return (
    <DashboardCard title="Recent Activity" testid="dashboard-card-activity" onRefresh={onRefresh}>
      {(rows ?? []).length === 0 ? (
        <CardEmpty title="No status changes yet." />
      ) : (
        <ol className="relative pl-5 space-y-3" data-testid="activity-timeline">
          <span
            className="absolute left-[7px] top-1 bottom-1 w-px bg-aegisaxiom-border"
            aria-hidden="true"
          />
          {(rows ?? []).map((row) => (
            <li
              key={`${row.task_id}-${row.changed_at}`}
              className="relative"
              data-testid={`activity-row-${row.task_id}`}
            >
              <span className="absolute -left-[18px] top-1.5 w-2 h-2 rounded-full bg-violet-500 ring-2 ring-aegisaxiom-surface" aria-hidden="true" />
              <div className="flex flex-wrap items-center gap-1.5 text-sm text-aegisaxiom-text">
                <span className="font-medium truncate max-w-[200px]">
                  {row.title ?? '(deleted task)'}
                </span>
                {row.from_status && (
                  <>
                    <MiniStatusPill status={row.from_status} />
                    <span className="text-aegisaxiom-text-muted">→</span>
                  </>
                )}
                <MiniStatusPill status={row.to_status} />
              </div>
              <p className="text-[11px] text-aegisaxiom-text-muted mt-0.5 truncate">
                {row.project_name ?? '—'} · {fmtRelative(row.changed_at)}
              </p>
            </li>
          ))}
        </ol>
      )}
    </DashboardCard>
  );
}

// ---- Card 7: My Work inbox (Wave 6 / Thread A / Contract 3) ----
//
// Pulls from GET /api/projects/notifications/my-summary, which is the
// dashboard-card aggregation of unread notifications for the calling
// user, grouped by event key. Three rows: overdue tasks, red-health
// projects, stale blockers — each with an unread count and an icon.
// Total unread count surfaces as a header badge.
//
// The full inbox lives at GET /api/notifications (workflow_rules.py).
// This card is intentionally a counts-only summary; clicking through to
// a filtered task / project / blocker view is a follow-up wave.

const NOTIFICATION_ROWS = [
  {
    key: 'projects.task.overdue',
    label: 'Overdue tasks',
    Icon: AlertCircle,
    iconCls: 'text-red-400',
  },
  {
    key: 'projects.project.health_red',
    label: 'Red-health projects',
    Icon: AlertTriangle,
    iconCls: 'text-yellow-400',
  },
  {
    key: 'projects.blocker.stale',
    label: 'Stale blockers',
    Icon: Clock,
    iconCls: 'text-orange-400',
  },
];

function MyWorkInboxCard({ data, onRefresh }) {
  const total = data?.unread_total ?? 0;
  const byEvent = data?.by_event ?? {};

  return (
    <DashboardCard
      title="My Work Inbox"
      testid="dashboard-card-my-work"
      onRefresh={onRefresh}
    >
      <div className="flex flex-col h-[200px]">
        <div className="flex items-baseline gap-2 mb-3">
          <span
            className="text-[36px] leading-none font-bold text-aegisaxiom-text"
            data-testid="my-work-unread-total"
          >
            {total}
          </span>
          <span className="text-xs text-aegisaxiom-text-muted">unread</span>
        </div>
        <ul className="flex-1 space-y-2 text-sm" data-testid="my-work-rows">
          {NOTIFICATION_ROWS.map(({ key, label, Icon, iconCls }) => {
            const count = byEvent[key] ?? 0;
            return (
              <li
                key={key}
                className="flex items-center gap-2"
                data-testid={`my-work-row-${key}`}
              >
                <Icon size={14} className={`shrink-0 ${iconCls}`} />
                <span className="text-aegisaxiom-text-secondary">{label}</span>
                <span
                  className={
                    count > 0
                      ? 'ml-auto inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded text-[11px] font-bold bg-violet-500/15 text-violet-300'
                      : 'ml-auto inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded text-[11px] bg-aegisaxiom-elevated text-aegisaxiom-text-muted'
                  }
                >
                  {count}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </DashboardCard>
  );
}

// ---- Card 8: Sprint Velocity (agile only) ----

// Wave 6 / 6B4 — portfolio-wide velocity card. Only mounts when at least
// one agile project shows up in `tasks_by_status`. The chart itself
// handles the empty state when no closed sprints exist yet, so we don't
// double-gate.
function SprintVelocityCard({ onRefresh }) {
  return (
    <DashboardCard title="Sprint Velocity" testid="dashboard-card-velocity" onRefresh={onRefresh}>
      <div className="h-[200px] flex items-center">
        <VelocityChart testid="dashboard-velocity-chart" className="w-full" />
      </div>
    </DashboardCard>
  );
}

function hasAgileProjects(summary) {
  const rows = summary?.tasks_by_status ?? [];
  for (const row of rows) {
    if (row?.methodology === 'agile') {
      const buckets = row.buckets ?? {};
      for (const v of Object.values(buckets)) {
        if (typeof v === 'number' && v > 0) return true;
      }
    }
  }
  return false;
}

// ---- Card 9: Milestones Summary (Wave 6 / Thread C — Contract 5) ----
//
// Aggregates waterfall milestone tasks across all projects. At-risk count is
// computed client-side using the same rule as backend
// compute_milestone_at_risk: target_date < today AND status !== 'complete'.

function isMilestoneAtRisk(task, todayMs) {
  const mf = task?.methodology_fields ?? {};
  if (!mf.milestone) return false;
  const target = mf.milestone_target_date;
  if (!target) return false;
  if (task.status === 'complete') return false;
  try {
    const t = new Date(target.slice(0, 10)).getTime();
    return t < todayMs;
  } catch {
    return false;
  }
}

function MilestonesSummaryCard({ onRefresh, onMilestoneClick }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // The list endpoint has no milestone filter today, so we pull a bounded
      // page and post-filter. Milestones are by definition rare relative to
      // tasks; capping at 500 covers the realistic deployment.
      const res = await api.get(`${API_URL}/api/projects/tasks?limit=500`);
      if (!res.ok) {
        setItems([]);
        return;
      }
      const data = await res.json();
      const all = Array.isArray(data) ? data : data.items ?? [];
      setItems(all.filter((t) => t?.methodology_fields?.milestone === true));
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const total = items.length;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const todayMs = today.getTime();
  const atRiskItems = items.filter((m) => isMilestoneAtRisk(m, todayMs));
  const atRisk = atRiskItems.length;
  const complete = items.filter((m) => m.status === 'complete').length;

  const handleRefresh = () => {
    load();
    onRefresh?.();
  };

  return (
    <DashboardCard title="Milestones" testid="dashboard-card-milestones" onRefresh={handleRefresh}>
      {loading ? (
        <CardEmpty title="Loading…" />
      ) : total === 0 ? (
        <CardEmpty title="No milestones across projects yet." />
      ) : (
        <div className="flex flex-col h-[200px]">
          <div className="flex items-center gap-6 mb-4">
            <div className="flex items-center gap-2" data-testid="milestones-total">
              <Flag size={16} className="text-violet-400" />
              <span className="text-2xl font-bold text-aegisaxiom-text">{total}</span>
              <span className="text-xs text-aegisaxiom-text-muted">total</span>
            </div>
            <div className="flex items-center gap-2" data-testid="milestones-at-risk">
              <AlertTriangle size={16} className={atRisk > 0 ? 'text-red-400' : 'text-aegisaxiom-text-muted'} />
              <span className={`text-2xl font-bold ${atRisk > 0 ? 'text-red-400' : 'text-aegisaxiom-text'}`}>{atRisk}</span>
              <span className="text-xs text-aegisaxiom-text-muted">at risk</span>
            </div>
            <div className="flex items-center gap-2" data-testid="milestones-complete">
              <span className="inline-block w-2 h-2 rounded-full bg-green-400" />
              <span className="text-2xl font-bold text-aegisaxiom-text">{complete}</span>
              <span className="text-xs text-aegisaxiom-text-muted">complete</span>
            </div>
          </div>
          {atRiskItems.length > 0 ? (
            <div className="flex-1 min-h-0 overflow-y-auto pr-1">
              <p className="text-[10px] uppercase tracking-wider text-red-400 mb-1.5 font-semibold">At-risk milestones</p>
              <ul className="space-y-1">
                {atRiskItems.slice(0, 5).map((m) => {
                  const taskId = m.task_id ?? m._id;
                  return (
                    <li key={taskId}>
                      <button
                        type="button"
                        onClick={() => onMilestoneClick?.(m)}
                        className="w-full text-left px-2 py-1 rounded text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors truncate"
                        data-testid={`milestones-at-risk-row-${taskId}`}
                      >
                        {m.methodology_fields?.milestone_name ?? m.title}
                      </button>
                    </li>
                  );
                })}
              </ul>
              {atRiskItems.length > 5 ? (
                <p className="text-[11px] text-aegisaxiom-text-muted mt-1">+{atRiskItems.length - 5} more</p>
              ) : null}
            </div>
          ) : (
            <p className="text-xs text-aegisaxiom-text-muted">All milestones on track.</p>
          )}
        </div>
      )}
    </DashboardCard>
  );
}

// ---- Main DashboardTab ----

export default function DashboardTab() {
  const [summary, setSummary] = useState(null);
  const [myWork, setMyWork] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [fetching, setFetching] = useState(false);

  const load = useCallback(async () => {
    setFetching(true);
    setError(false);
    try {
      // Fan out the two dashboard fetches in parallel — the My Work card's
      // failure should not block the rest of the dashboard from rendering,
      // so we tolerate a notifications fetch error and just leave the card
      // in its empty state.
      const [summaryRes, myWorkRes] = await Promise.all([
        api.get(`${API_URL}/api/projects/dashboard/summary`),
        api.get(`${API_URL}/api/projects/notifications/my-summary`),
      ]);
      if (!summaryRes.ok) throw new Error('Failed');
      const summaryData = await summaryRes.json();
      setSummary(summaryData);
      if (myWorkRes.ok) {
        setMyWork(await myWorkRes.json());
      } else {
        setMyWork(null);
      }
    } catch {
      setError(true);
    } finally {
      setLoading(false);
      setFetching(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const refresh = () => load();

  if (loading) {
    return (
      <div className="min-h-[50vh] flex items-center justify-center" data-testid="dashboard-loading">
        <div className="animate-spin w-8 h-8 rounded-full border-2 border-violet-500 border-t-transparent" />
      </div>
    );
  }

  if (error || !summary) {
    return (
      <div className="glass-card p-8 text-center text-sm text-aegisaxiom-text-muted" data-testid="dashboard-error">
        Couldn&apos;t load dashboard.{' '}
        <button onClick={refresh} className="underline text-aegisaxiom-text-secondary hover:text-aegisaxiom-text">
          Try refreshing.
        </button>
      </div>
    );
  }

  return (
    <section data-testid="dashboard-page">
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-lg font-semibold text-aegisaxiom-text" data-testid="dashboard-title">Dashboard</h2>
        <button
          type="button"
          onClick={refresh}
          disabled={fetching}
          className="btn-secondary inline-flex items-center gap-2 text-xs disabled:opacity-60"
          data-testid="dashboard-refresh"
          aria-label="Refresh dashboard"
        >
          <RefreshCw size={13} className={fetching ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      <MyDayTile />

      <div className="grid gap-4 grid-cols-1 md:grid-cols-2 xl:grid-cols-3" data-testid="dashboard-grid">
        <MyWorkInboxCard data={myWork} onRefresh={refresh} />
        <ProjectHealthCard data={summary.project_health} onRefresh={refresh} />
        <ActiveProjectsCard data={summary.active_projects} onRefresh={refresh} />
        <TasksByStatusCard rows={summary.tasks_by_status} onRefresh={refresh} />
        <WorkloadByAssigneeCard rows={summary.workload_by_assignee} onRefresh={refresh} />
        <UpcomingDeadlinesCard rows={summary.upcoming_deadlines} onRefresh={refresh} />
        <RecentActivityCard rows={summary.recent_activity} onRefresh={refresh} />
        {hasAgileProjects(summary) ? (
          <SprintVelocityCard onRefresh={refresh} />
        ) : null}
        <MilestonesSummaryCard onRefresh={refresh} />
      </div>
    </section>
  );
}
