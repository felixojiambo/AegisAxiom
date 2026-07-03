import React, { useEffect, useState } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import { useTheme } from '@/contexts/ThemeContext';
import { usePermissions } from '@/hooks/usePermissions';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import {
  TrendingUp,
  DollarSign,
  Users,
  Briefcase,
  AlertCircle,
  CheckCircle,
  FileText,
} from 'lucide-react';
import { toast } from 'sonner';
import { api, unwrap } from '@/lib/api';
import API_URL from '../lib/apiConfig';

const API = API_URL;

const fmt = (n) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(n || 0);

// Chart fill colors — semantic, work in both themes
const STAGE_CHART_COLORS = {
  Prospecting: '#7C3AED',
  Qualification: '#6D28D9',
  Proposal: '#C026D3',
  Negotiation: '#F59E0B',
  'Closed Won': '#10B981',
  'Closed Lost': '#EF4444',
};

// Badge class pairs — aligned with Deals.js for app-wide consistency
const STAGE_BADGE_CLASSES = {
  Prospecting: 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300',
  Qualification: 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300',
  Proposal: 'bg-fuchsia-50 dark:bg-fuchsia-600/20 text-fuchsia-700 dark:text-fuchsia-300',
  Negotiation: 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300',
  'Closed Won': 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300',
  'Closed Lost': 'bg-rose-50 dark:bg-rose-600/20 text-rose-700 dark:text-rose-300',
};

// Abbreviated stage labels for cramped mobile x-axis
const STAGE_SHORT = {
  Prospecting: 'Prosp.',
  Qualification: 'Qual.',
  Proposal: 'Prop.',
  Negotiation: 'Neg.',
  'Closed Won': 'Won',
  'Closed Lost': 'Lost',
};

/* ──────────────────────────────────────────────
   Helpers
   ────────────────────────────────────────────── */

function getTimeGreeting() {
  const h = new Date().getHours();
  if (h < 12) return 'Good morning';
  if (h < 18) return 'Good afternoon';
  return 'Good evening';
}

/** Track a window matchMedia query reactively. */
function useMediaQuery(query) {
  const [matches, setMatches] = useState(
    typeof window !== 'undefined' ? window.matchMedia(query).matches : false,
  );
  useEffect(() => {
    const mq = window.matchMedia(query);
    const handler = (e) => setMatches(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, [query]);
  return matches;
}

/* ──────────────────────────────────────────────
   Presentational components
   ────────────────────────────────────────────── */

function StatCard({ label, value, icon: Icon, color = 'violet' }) {
  const colors = {
    violet:
      'text-violet-600 dark:text-violet-400 bg-violet-50 dark:bg-violet-600/10 border-violet-200 dark:border-violet-600/20',
    green:
      'text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-600/10 border-emerald-200 dark:border-emerald-600/20',
    blue: 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-600/10 border-blue-200 dark:border-blue-600/20',
    fuchsia:
      'text-fuchsia-600 dark:text-fuchsia-400 bg-fuchsia-50 dark:bg-fuchsia-600/10 border-fuchsia-200 dark:border-fuchsia-600/20',
    amber:
      'text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-600/10 border-amber-200 dark:border-amber-600/20',
  };
  return (
    <div
      className="stat-card animate-fadeInUp p-4 sm:p-6"
      data-testid={`stat-${label.toLowerCase().replace(/\s/g, '-')}`}
    >
      <div
        className={`w-9 h-9 sm:w-10 sm:h-10 rounded-lg border flex items-center justify-center mb-3 sm:mb-4 ${colors[color]}`}
      >
        <Icon size={18} />
      </div>
      <p
        className="text-xl sm:text-2xl font-bold text-aegisaxiom-text leading-tight"
        style={{ fontFamily: 'Manrope, sans-serif' }}
      >
        {value}
      </p>
      <p className="text-aegisaxiom-text-muted text-xs mt-1">{label}</p>
    </div>
  );
}

function StatCardSkeleton() {
  return (
    <div className="stat-card p-4 sm:p-6">
      <div className="w-9 h-9 sm:w-10 sm:h-10 rounded-lg bg-aegisaxiom-elevated animate-pulse mb-3 sm:mb-4" />
      <div className="h-6 w-16 bg-aegisaxiom-elevated animate-pulse rounded mb-2" />
      <div className="h-3 w-24 bg-aegisaxiom-elevated animate-pulse rounded" />
    </div>
  );
}

function DashboardSkeleton({ title = 'Dashboard' }) {
  return (
    <div className="space-y-5 sm:space-y-6">
      <div>
        <h1 className="page-title text-xl sm:text-2xl">{title}</h1>
        <div className="h-3 w-40 bg-aegisaxiom-elevated animate-pulse rounded mt-2" />
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <StatCardSkeleton key={i} />
        ))}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 sm:gap-6">
        <div className="lg:col-span-7 glass-card p-5 sm:p-6">
          <div className="h-4 w-32 bg-aegisaxiom-elevated animate-pulse rounded mb-4" />
          <div className="h-48 bg-aegisaxiom-elevated/40 animate-pulse rounded" />
        </div>
        <div className="lg:col-span-5 glass-card p-5 sm:p-6">
          <div className="h-4 w-40 bg-aegisaxiom-elevated animate-pulse rounded mb-4" />
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="flex justify-between py-2 border-b border-aegisaxiom-border last:border-0"
              >
                <div className="h-3 w-20 bg-aegisaxiom-elevated animate-pulse rounded" />
                <div className="h-3 w-16 bg-aegisaxiom-elevated animate-pulse rounded" />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function DealListItem({ deal }) {
  const badgeClass = STAGE_BADGE_CLASSES[deal.stage] || 'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary';
  return (
    <div className="flex items-start sm:items-center justify-between gap-3 py-2.5 border-b border-aegisaxiom-border last:border-0">
      <div className="min-w-0 flex-1">
        <p className="text-aegisaxiom-text text-sm font-medium truncate">{deal.name}</p>
        <p className="text-aegisaxiom-text-muted text-xs truncate">{deal.account_name}</p>
      </div>
      <div className="text-right shrink-0 flex flex-col items-end gap-1">
        <p className="text-aegisaxiom-text text-sm font-semibold whitespace-nowrap">{fmt(deal.amount)}</p>
        {deal.stage && <span className={`badge-stage ${badgeClass}`}>{deal.stage}</span>}
      </div>
    </div>
  );
}

/* ──────────────────────────────────────────────
   Main Dashboard
   ────────────────────────────────────────────── */

export default function Dashboard() {
  const { user } = useAuth();
  const { isDark } = useTheme();
  const { hasPermission, hasAnyPermission } = usePermissions();
  const [stats, setStats] = useState(null);
  const [myDeals, setMyDeals] = useState([]);
  const [myComms, setMyComms] = useState(null);
  const [myContracts, setMyContracts] = useState([]);
  const [loading, setLoading] = useState(true);

  const isMobile = useMediaQuery('(max-width: 640px)');
  const greeting = getTimeGreeting();
  const firstName = user?.name?.split(' ')[0] || user?.name || '';

  const showMainDash = hasPermission('dashboard.main');
  const showSalesDash = hasAnyPermission(['dashboard.sales_rep', 'dashboard.agent']);
  const showContractsDash = hasAnyPermission([
    'dashboard.contractor',
    'contracts.view',
    'contracts.view.own',
  ]);

  // Theme-aware chart colors
  const axisTickColor = isDark ? '#9CA3AF' : '#64748B';
  const tooltipBg = 'var(--aegisaxiom-bg-elevated)';
  const tooltipBorder = isDark ? 'rgba(255,255,255,0.08)' : 'rgba(124,58,237,0.18)';
  const tooltipText = 'var(--aegisaxiom-text-primary)';

  useEffect(() => {
    const load = async () => {
      try {
        if (showMainDash) {
          const res = await api.get(`${API}/api/admin/stats`);
          if (res.ok) setStats(await res.json());
        }
        if (showSalesDash) {
          const [dr, cr] = await Promise.all([
            api.get(`${API}/api/deals`),
            api.get(`${API}/api/commissions/summary`),
          ]);
          if (dr.ok) setMyDeals(unwrap(await dr.json()));
          if (cr.ok) setMyComms(await cr.json());
        }
        if (showContractsDash) {
          const [cr] = await Promise.all([api.get(`${API}/api/contracts`)]);
          if (cr.ok) setMyContracts(unwrap(await cr.json()));
        }
      } catch {
        toast.error('Failed to load dashboard data');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [user, showMainDash, showSalesDash, showContractsDash]);

  if (loading) return <DashboardSkeleton title="Dashboard" />;

  const stageData = stats
    ? Object.entries(stats.deals_by_stage || {}).map(([k, v]) => ({
        name: k,
        shortName: STAGE_SHORT[k] || k,
        count: v,
      }))
    : [];

  /* ─── Admin / Director Dashboard ─── */
  if (showMainDash && stats)
    return (
      <div className="space-y-5 sm:space-y-6">
        {/* Header */}
        <div>
          <h1 className="page-title text-xl sm:text-2xl">Dashboard</h1>
          <p className="text-aegisaxiom-text-muted text-sm mt-1">
            {greeting}, {firstName}
          </p>
        </div>

        {/* Action-required alert — surfaced near the top */}
        {stats.pending_partner_applications > 0 && (
          <div className="glass-card p-3 sm:p-4 border-amber-200 dark:border-amber-600/30 flex items-center gap-3">
            <AlertCircle size={18} className="text-amber-600 dark:text-amber-400 shrink-0" />
            <p className="text-aegisaxiom-text-secondary text-sm min-w-0 flex-1">
              <strong className="text-amber-600 dark:text-amber-400">
                {stats.pending_partner_applications}
              </strong>{' '}
              partner application
              {stats.pending_partner_applications !== 1 ? 's' : ''} awaiting review
            </p>
            <a
              href="/admin/users"
              className="text-violet-600 dark:text-violet-400 text-xs font-medium hover:underline shrink-0 px-2 py-1 -mr-2"
            >
              Review →
            </a>
          </div>
        )}

        {/* Stats Grid */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
          <StatCard label="Total Accounts" value={stats.total_accounts} icon={Users} color="blue" />
          <StatCard
            label="Active Deals"
            value={stats.total_deals}
            icon={Briefcase}
            color="violet"
          />
          <StatCard
            label="Commissions Owed"
            value={fmt(stats.total_commissions_owed)}
            icon={DollarSign}
            color="amber"
          />
          <StatCard
            label="Revenue Invoiced"
            value={fmt(stats.total_invoiced)}
            icon={TrendingUp}
            color="green"
          />
        </div>

        {/* Pipeline + Commission Row */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 sm:gap-6">
          {/* Pipeline Chart */}
          <div className="lg:col-span-7 glass-card p-5 sm:p-6">
            <h3
              className="text-sm font-semibold text-aegisaxiom-text mb-4"
              style={{ fontFamily: 'Manrope, sans-serif' }}
            >
              Deal Pipeline
            </h3>
            {stageData.length > 0 ? (
              <ResponsiveContainer width="100%" height={isMobile ? 220 : 240}>
                <BarChart
                  data={stageData}
                  barSize={isMobile ? 24 : 32}
                  margin={{ top: 5, right: 10, left: -20, bottom: isMobile ? 20 : 5 }}
                >
                  <XAxis
                    dataKey={isMobile ? 'shortName' : 'name'}
                    tick={{ fill: axisTickColor, fontSize: isMobile ? 10 : 11 }}
                    axisLine={false}
                    tickLine={false}
                    interval={0}
                    angle={isMobile ? -25 : 0}
                    textAnchor={isMobile ? 'end' : 'middle'}
                    height={isMobile ? 50 : 30}
                  />
                  <YAxis
                    tick={{ fill: axisTickColor, fontSize: 11 }}
                    axisLine={false}
                    tickLine={false}
                    allowDecimals={false}
                  />
                  <Tooltip
                    contentStyle={{
                      background: tooltipBg,
                      border: `1px solid ${tooltipBorder}`,
                      borderRadius: '8px',
                      color: tooltipText,
                      fontSize: '12px',
                    }}
                    labelStyle={{ color: tooltipText, fontWeight: 500 }}
                    cursor={{ fill: 'rgba(124,58,237,0.08)' }}
                    formatter={(value) => [value, 'Deals']}
                    labelFormatter={(_, payload) => payload?.[0]?.payload?.name || ''}
                  />
                  <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                    {stageData.map((entry) => (
                      <Cell key={entry.name} fill={STAGE_CHART_COLORS[entry.name] || '#7C3AED'} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="text-center py-12 sm:py-16">
                <Briefcase className="w-10 h-10 text-aegisaxiom-text-muted/60 mx-auto mb-3" />
                <p className="text-aegisaxiom-text-secondary text-sm">No deal data yet</p>
                <p className="text-aegisaxiom-text-muted text-xs mt-1">
                  Deals will appear here as they're added
                </p>
              </div>
            )}
          </div>

          {/* Commission Summary */}
          <div className="lg:col-span-5 glass-card p-5 sm:p-6">
            <h3
              className="text-sm font-semibold text-aegisaxiom-text mb-4"
              style={{ fontFamily: 'Manrope, sans-serif' }}
            >
              Commission Summary
            </h3>
            <div className="space-y-1">
              {[
                {
                  label: 'Owed',
                  value: fmt(stats.total_commissions_owed),
                  color: 'text-amber-600 dark:text-amber-400',
                  accent: 'bg-amber-500',
                  emphasis: true,
                },
                {
                  label: 'Paid',
                  value: fmt(stats.total_commissions_paid),
                  color: 'text-emerald-600 dark:text-emerald-400',
                  accent: 'bg-emerald-500',
                },
                {
                  label: 'Invoiced',
                  value: fmt(stats.total_invoiced),
                  color: 'text-blue-600 dark:text-blue-400',
                  accent: 'bg-blue-500',
                },
                {
                  label: 'Paid Invoices',
                  value: fmt(stats.total_paid_invoices),
                  color: 'text-violet-600 dark:text-violet-400',
                  accent: 'bg-violet-500',
                },
              ].map((row) => (
                <div
                  key={row.label}
                  className="flex justify-between items-center gap-3 py-2.5 border-b border-aegisaxiom-border last:border-0"
                >
                  <div className="flex items-center gap-2.5 min-w-0">
                    <span className={`w-1.5 h-1.5 rounded-full ${row.accent} shrink-0`} />
                    <span
                      className={`text-sm truncate ${
                        row.emphasis ? 'text-aegisaxiom-text font-medium' : 'text-aegisaxiom-text-secondary'
                      }`}
                    >
                      {row.label}
                    </span>
                  </div>
                  <span
                    className={`font-semibold whitespace-nowrap ${row.color} ${
                      row.emphasis ? 'text-base' : 'text-sm'
                    }`}
                  >
                    {row.value}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Recent Deals */}
        {stats.recent_deals?.length > 0 && (
          <div className="glass-card p-5 sm:p-6">
            <h3
              className="text-sm font-semibold text-aegisaxiom-text mb-4"
              style={{ fontFamily: 'Manrope, sans-serif' }}
            >
              Recent Deals
            </h3>
            <div className="space-y-0">
              {stats.recent_deals.map((d) => (
                <DealListItem key={d.deal_id} deal={d} />
              ))}
            </div>
          </div>
        )}
      </div>
    );

  /* ─── Sales Rep / Agent Dashboard ─── */
  if (showSalesDash)
    return (
      <div className="space-y-5 sm:space-y-6">
        <div>
          <h1 className="page-title text-xl sm:text-2xl">My Dashboard</h1>
          <p className="text-aegisaxiom-text-muted text-sm mt-1">
            {greeting}, {firstName}
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4">
          <StatCard label="My Deals" value={myDeals.length} icon={Briefcase} color="violet" />
          <StatCard
            label="Commissions Owed"
            value={fmt(myComms?.total_owed)}
            icon={DollarSign}
            color="amber"
          />
          <StatCard
            label="Commissions Paid"
            value={fmt(myComms?.total_paid)}
            icon={CheckCircle}
            color="green"
          />
        </div>

        <div className="glass-card p-5 sm:p-6">
          <h3
            className="text-sm font-semibold text-aegisaxiom-text mb-4"
            style={{ fontFamily: 'Manrope, sans-serif' }}
          >
            My Active Deals
          </h3>
          {myDeals.length === 0 ? (
            <div className="text-center py-10 sm:py-12">
              <Briefcase className="w-10 h-10 text-aegisaxiom-text-muted/60 mx-auto mb-3" />
              <p className="text-aegisaxiom-text-secondary text-sm">No deals assigned yet</p>
              <p className="text-aegisaxiom-text-muted text-xs mt-1">
                New deals will appear here as they're created
              </p>
            </div>
          ) : (
            <div className="space-y-0">
              {myDeals.slice(0, 8).map((d) => (
                <DealListItem key={d.deal_id} deal={d} />
              ))}
            </div>
          )}
        </div>
      </div>
    );

  /* ─── Contracts Portal ─── */
  if (showContractsDash && myContracts.length > 0)
    return (
      <div className="space-y-5 sm:space-y-6">
        <div>
          <h1 className="page-title text-xl sm:text-2xl">My Portal</h1>
          <p className="text-aegisaxiom-text-muted text-sm mt-1">
            {greeting}, {firstName}
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
          <StatCard
            label="Active Contracts"
            value={myContracts.filter((c) => c.status === 'active').length}
            icon={FileText}
            color="violet"
          />
          <StatCard
            label="Total Contracts"
            value={myContracts.length}
            icon={Briefcase}
            color="blue"
          />
        </div>

        <div className="glass-card p-5 sm:p-6">
          <h3
            className="text-sm font-semibold text-aegisaxiom-text mb-4"
            style={{ fontFamily: 'Manrope, sans-serif' }}
          >
            My Contracts
          </h3>
          <div className="space-y-2.5">
            {myContracts.map((c) => (
              <div
                key={c.contract_id}
                className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-2 p-3 sm:p-3.5 bg-aegisaxiom-elevated/60 hover:bg-aegisaxiom-elevated rounded-lg border border-aegisaxiom-border transition-colors"
              >
                <div className="min-w-0">
                  <p className="text-aegisaxiom-text text-sm font-medium truncate">{c.name}</p>
                  <p className="text-aegisaxiom-text-muted text-xs mt-0.5">
                    {c.contract_type === 'hourly'
                      ? `${c.hours_consumed}/${c.total_hours_purchased} hrs used`
                      : `$${c.fixed_amount?.toLocaleString()}`}
                  </p>
                </div>
                <span
                  className={`badge-stage self-start sm:self-center ${
                    c.status === 'active'
                      ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300'
                      : 'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary'
                  }`}
                >
                  {c.status}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    );

  /* ─── Generic welcome ─── */
  return (
    <div className="space-y-5 sm:space-y-6">
      <div>
        <h1 className="page-title text-xl sm:text-2xl">Dashboard</h1>
        <p className="text-aegisaxiom-text-muted text-sm mt-1">
          {greeting}, {firstName}
        </p>
      </div>
      <div className="glass-card p-5 sm:p-6">
        <p className="text-aegisaxiom-text-secondary text-sm">
          Use the sidebar to navigate to the pages available to your role.
        </p>
      </div>
    </div>
  );
}
