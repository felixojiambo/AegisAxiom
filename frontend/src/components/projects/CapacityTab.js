import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Search, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { extractTaskErrorMessage } from '@/lib/safeXhr';
import ConfirmModal from '@/components/shared/ConfirmModal';
import UserAvatar from '@/components/projects/widgets/UserAvatar';
import LoadingSpinner from '@/components/projects/widgets/LoadingSpinner';
import EmptyState from '@/components/projects/widgets/EmptyState';

// ---------------------------------------------------------------------------
// Tier metadata — drives pill labels, badge colors, and confirm-modal copy.
// ---------------------------------------------------------------------------
const TIER_OPTIONS = [
  {
    id: 'main_pm',
    label: 'Main PM',
    blurb: 'Top-of-funnel project manager pool — appears first in PM dropdowns.',
    pillClass: 'bg-violet-500/15 text-violet-300 border-violet-500/30',
    activePillClass: 'bg-violet-500 text-white border-violet-500 shadow-sm-secondary',
  },
  {
    id: 'overflow_pm',
    label: 'Overflow PM',
    blurb: 'Capacity backstop — assignable as PM when main pool is full.',
    pillClass: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
    activePillClass: 'bg-sky-500 text-white border-sky-500 shadow-sm-secondary',
  },
  {
    id: 'member_only',
    label: 'Member Only',
    blurb: 'Assignable to projects as a team member; not eligible as PM.',
    pillClass: 'bg-teal-500/15 text-teal-300 border-teal-500/30',
    activePillClass: 'bg-teal-500 text-white border-teal-500 shadow-sm-secondary',
  },
  {
    id: 'none',
    label: 'None',
    blurb: 'Excluded from project pickers entirely.',
    pillClass: 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted border-aegisaxiom-border',
    activePillClass: 'bg-aegisaxiom-text-muted text-aegisaxiom-surface border-aegisaxiom-text-muted',
  },
];

const TIER_INDEX = TIER_OPTIONS.reduce((acc, t) => {
  acc[t.id] = t;
  return acc;
}, {});

const FILTER_OPTIONS = [
  { id: 'all', label: 'All tiers' },
  ...TIER_OPTIONS.map((t) => ({ id: t.id, label: t.label })),
];

// Impact copy used in the ConfirmModal body. Keyed by `${from}->${to}`.
function impactCopy(fromTier, toTier, name) {
  const fromLabel = TIER_INDEX[fromTier]?.label ?? 'Unset';
  const toLabel = TIER_INDEX[toTier]?.label ?? 'Unset';
  if (fromTier === toTier) {
    return `${name} is already ${toLabel}.`;
  }
  // Specific transitions worth calling out by name.
  if (toTier === 'none') {
    return (
      `Setting ${name} to None will remove them from PM dropdowns AND ` +
      `team-member pickers across all projects. Existing assignments stay ` +
      `intact, but they won't be selectable for new ones.`
    );
  }
  if (toTier === 'member_only') {
    if (fromTier === 'main_pm' || fromTier === 'overflow_pm') {
      return (
        `Demoting ${name} from ${fromLabel} to ${toLabel} will remove them ` +
        `from PM dropdowns. They'll still be assignable as a team member.`
      );
    }
    return (
      `Setting ${name} to ${toLabel} makes them assignable as a team member ` +
      `but not as a project manager.`
    );
  }
  if (toTier === 'overflow_pm') {
    return (
      `Promoting ${name} to ${toLabel} adds them to the PM pool as a ` +
      `capacity backstop — they'll appear in PM dropdowns when the main ` +
      `pool is exhausted.`
    );
  }
  if (toTier === 'main_pm') {
    return (
      `Promoting ${name} to ${toLabel} adds them to the top-of-funnel PM ` +
      `pool. They'll appear first in PM dropdowns on new projects.`
    );
  }
  return `Change ${name}'s capacity from ${fromLabel} to ${toLabel}.`;
}

// ---------------------------------------------------------------------------
// Tier pill — used both in the row and in the toggle column.
// ---------------------------------------------------------------------------
function TierPill({ tier, active, onClick, disabled, testid }) {
  const meta = TIER_INDEX[tier];
  if (!meta) return null;
  const cls = active ? meta.activePillClass : meta.pillClass;
  const base =
    'inline-flex items-center px-2.5 py-1 rounded-full text-[11px] font-semibold border transition-colors';
  const interact = disabled
    ? 'cursor-default'
    : 'cursor-pointer hover:opacity-90';
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      data-testid={testid}
      title={meta.blurb}
      className={`${base} ${cls} ${interact}`}
    >
      {meta.label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Stats row — count per tier across the (filtered) population.
// ---------------------------------------------------------------------------
function StatsRow({ counts }) {
  return (
    <div
      className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-aegisaxiom-text-secondary"
      data-testid="capacity-stats-row"
    >
      {TIER_OPTIONS.map((t, idx) => (
        <span key={t.id} className="flex items-center gap-2">
          <span className={`inline-block h-2.5 w-2.5 rounded-full ${
            t.id === 'main_pm' ? 'bg-violet-400'
              : t.id === 'overflow_pm' ? 'bg-sky-400'
                : t.id === 'member_only' ? 'bg-teal-400'
                  : 'bg-aegisaxiom-text-muted'
          }`} />
          <span className="font-semibold text-aegisaxiom-text" data-testid={`capacity-count-${t.id}`}>
            {counts[t.id] ?? 0}
          </span>
          <span>{t.label}</span>
          {idx < TIER_OPTIONS.length - 1 && (
            <span className="text-aegisaxiom-text-muted ml-3">·</span>
          )}
        </span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row — single user with tier toggle.
// ---------------------------------------------------------------------------
function CapacityRow({ user, onTierChange, saving }) {
  const currentTier = user.pm_capacity_tier ?? 'none';
  return (
    <tr
      className="border-b border-aegisaxiom-border last:border-b-0 hover:bg-aegisaxiom-elevated transition-colors"
      data-testid={`capacity-row-${user.user_id}`}
    >
      <td className="py-3 pl-5 pr-4">
        <div className="flex items-center gap-3">
          <UserAvatar user={user} size="md" />
          <div className="min-w-0">
            <div className="text-sm font-medium text-aegisaxiom-text truncate">
              {user.name ?? user.email ?? user.user_id}
              {!user.is_active && (
                <span className="ml-2 text-[10px] font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
                  inactive
                </span>
              )}
            </div>
            {user.email && (
              <div className="text-xs text-aegisaxiom-text-muted truncate">{user.email}</div>
            )}
          </div>
        </div>
      </td>
      <td className="py-3 pr-4 text-sm text-aegisaxiom-text-secondary whitespace-nowrap">
        {user.role ?? '—'}
      </td>
      <td className="py-3 pr-5">
        <div
          className="inline-flex flex-wrap gap-1.5"
          data-testid={`capacity-toggle-${user.user_id}`}
        >
          {TIER_OPTIONS.map((t) => (
            <TierPill
              key={t.id}
              tier={t.id}
              active={t.id === currentTier}
              disabled={saving}
              testid={`capacity-tier-${user.user_id}-${t.id}`}
              onClick={() => {
                if (t.id === currentTier || saving) return;
                onTierChange(user, t.id);
              }}
            />
          ))}
        </div>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export default function CapacityTab() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const [tierFilter, setTierFilter] = useState('all');
  const [pendingChange, setPendingChange] = useState(null); // {user, toTier}
  const [savingId, setSavingId] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get(`${API_URL}/api/projects/users/capacity`);
      if (!res.ok) {
        const msg = await extractTaskErrorMessage(res, 'Could not load capacity');
        throw new Error(msg);
      }
      const data = await res.json();
      setUsers(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e.message ?? 'Could not load capacity');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // ----- derived state -----
  const counts = useMemo(() => {
    const c = { main_pm: 0, overflow_pm: 0, member_only: 0, none: 0 };
    for (const u of users) {
      const t = u.pm_capacity_tier ?? 'none';
      if (t in c) c[t] += 1;
    }
    return c;
  }, [users]);

  const filteredUsers = useMemo(() => {
    const q = search.trim().toLowerCase();
    return users.filter((u) => {
      if (tierFilter !== 'all') {
        const t = u.pm_capacity_tier ?? 'none';
        if (t !== tierFilter) return false;
      }
      if (!q) return true;
      const name = (u.name ?? '').toLowerCase();
      const email = (u.email ?? '').toLowerCase();
      return name.includes(q) || email.includes(q);
    });
  }, [users, search, tierFilter]);

  // ----- tier change handler -----
  const requestTierChange = useCallback((user, toTier) => {
    setPendingChange({ user, toTier });
  }, []);

  const confirmTierChange = useCallback(async () => {
    if (!pendingChange) return;
    const { user, toTier } = pendingChange;
    setSavingId(user.user_id);
    try {
      const res = await api.patch(
        `${API_URL}/api/projects/users/${user.user_id}/capacity-tier`,
        { tier: toTier },
      );
      if (!res.ok) {
        const msg = await extractTaskErrorMessage(res, 'Could not update tier');
        throw new Error(msg);
      }
      const updated = await res.json();
      setUsers((prev) => prev.map(
        (u) => (u.user_id === user.user_id ? { ...u, ...updated } : u),
      ));
      toast.success(
        `${user.name ?? 'User'} → ${TIER_INDEX[toTier]?.label ?? toTier}`,
      );
    } catch (e) {
      toast.error(e.message ?? 'Could not update tier');
    } finally {
      setSavingId(null);
      setPendingChange(null);
    }
  }, [pendingChange]);

  // ----- render -----
  return (
    <div className="space-y-5" data-testid="capacity-tab">
      {/* Header with stats + refresh */}
      <div className="glass-card p-5">
        <div className="flex items-start justify-between gap-3 mb-4">
          <div>
            <h2 className="text-lg font-semibold text-aegisaxiom-text mb-1">
              PM Capacity
            </h2>
            <p className="text-sm text-aegisaxiom-text-muted max-w-prose">
              Tier each user as Main PM, Overflow PM, Member Only, or None.
              Tiers drive visibility in project-creation and task-assignee pickers.
            </p>
          </div>
          <button
            type="button"
            onClick={load}
            className="btn-primary inline-flex items-center gap-2 text-xs"
            data-testid="capacity-refresh"
            disabled={loading}
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
        <StatsRow counts={counts} />
      </div>

      {/* Filters */}
      <div className="glass-card p-4">
        <div className="flex flex-col sm:flex-row gap-3">
          <div className="relative flex-1">
            <Search
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted pointer-events-none"
            />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name or email"
              className="aegisaxiom-input w-full pl-9"
              data-testid="capacity-search"
            />
          </div>
          <select
            value={tierFilter}
            onChange={(e) => setTierFilter(e.target.value)}
            className="aegisaxiom-input sm:w-48"
            data-testid="capacity-tier-filter"
          >
            {FILTER_OPTIONS.map((f) => (
              <option key={f.id} value={f.id}>{f.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="glass-card p-0 overflow-hidden">
        {loading ? (
          <div className="p-10 flex items-center justify-center">
            <LoadingSpinner />
          </div>
        ) : error ? (
          <div className="p-6 text-center text-sm text-red-400" data-testid="capacity-error">
            {error}
          </div>
        ) : filteredUsers.length === 0 ? (
          <div className="p-6">
            <EmptyState
              title={
                users.length === 0
                  ? 'No users found'
                  : 'No matches'
              }
              description={
                users.length === 0
                  ? 'Once users exist in the system, you can tier them here.'
                  : 'Try clearing the filter or search.'
              }
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm" data-testid="capacity-table">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wider text-aegisaxiom-text-muted border-b border-aegisaxiom-border">
                  <th className="font-semibold py-3 px-5">User</th>
                  <th className="font-semibold py-3 pr-4">Role</th>
                  <th className="font-semibold py-3 pr-5">Capacity tier</th>
                </tr>
              </thead>
              <tbody>
                {filteredUsers.map((u) => (
                  <CapacityRow
                    key={u.user_id}
                    user={u}
                    onTierChange={requestTierChange}
                    saving={savingId === u.user_id}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <ConfirmModal
        isOpen={!!pendingChange}
        title={pendingChange
          ? `Change capacity for ${pendingChange.user.name ?? 'user'}?`
          : 'Change capacity?'}
        message={pendingChange
          ? impactCopy(
            pendingChange.user.pm_capacity_tier ?? 'none',
            pendingChange.toTier,
            pendingChange.user.name ?? 'this user',
          )
          : ''}
        confirmText="Apply change"
        cancelText="Cancel"
        variant="primary"
        onConfirm={confirmTierChange}
        onCancel={() => setPendingChange(null)}
      />
    </div>
  );
}
