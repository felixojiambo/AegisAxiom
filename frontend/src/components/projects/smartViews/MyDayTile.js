/**
 * MyDayTile — full-width "My Day" panel mounted above the dashboard grid.
 *
 * Self-fetching, independently loaded sections so a slow endpoint doesn't
 * block the rest. Skeleton loading states (not spinners).
 *
 * Sections:
 *   - Due today
 *   - Blockers I own
 *   - My PM projects  (PM / Admin only)
 *   - Recently changed (Thread 1B endpoint; graceful empty state if 404)
 *
 * Design doc: .waves/projects-v1.2-smart-views-design.md §3
 */

import React, { useCallback, useEffect, useState } from 'react';
import { AlertCircle, Calendar, ExternalLink, RefreshCw } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/hooks/usePermissions';
import { deriveRole } from './pillRegistry';

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------
function Skeleton({ className = '' }) {
  return (
    <div
      className={`animate-pulse rounded bg-aegisaxiom-elevated ${className}`}
      aria-hidden="true"
    />
  );
}

function SectionSkeleton() {
  return (
    <div className="space-y-2">
      <Skeleton className="h-3 w-24" />
      <Skeleton className="h-3 w-40" />
      <Skeleton className="h-3 w-32" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------
function fmtRelative(iso) {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 2) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

// ---------------------------------------------------------------------------
// Due Today section
// ---------------------------------------------------------------------------
function DueTodayCard({ tasks, loading }) {
  const today = todayIso();
  const dueToday = (tasks ?? []).filter((t) => t.due_date === today);
  const visible = dueToday.slice(0, 5);
  const overflow = dueToday.length - visible.length;

  return (
    <div data-testid="my-day-due-today" className="flex-1 min-w-0">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-aegisaxiom-text-muted mb-2 flex items-center gap-1.5">
        <Calendar size={11} />
        Due today
      </h4>
      {loading ? (
        <SectionSkeleton />
      ) : dueToday.length === 0 ? (
        <p className="text-xs text-aegisaxiom-text-muted">Nothing due today.</p>
      ) : (
        <ul className="space-y-1.5">
          {visible.map((t) => (
            <li key={t.task_id} className="text-xs text-aegisaxiom-text truncate flex items-start gap-1">
              <span className="text-aegisaxiom-text-muted shrink-0">•</span>
              <span className="truncate">{t.title}</span>
              {t.project_name && (
                <span className="shrink-0 text-aegisaxiom-text-muted truncate ml-1">
                  · {t.project_name}
                </span>
              )}
            </li>
          ))}
          {overflow > 0 && (
            <li className="text-xs text-violet-400">+{overflow} more</li>
          )}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Blockers I Own section
// ---------------------------------------------------------------------------
function BlockersCard({ blockers, loading }) {
  const visible = (blockers ?? []).slice(0, 5);
  const overflow = (blockers?.length ?? 0) - visible.length;

  return (
    <div data-testid="my-day-blockers" className="flex-1 min-w-0">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-aegisaxiom-text-muted mb-2 flex items-center gap-1.5">
        <AlertCircle size={11} />
        Blockers I own
      </h4>
      {loading ? (
        <SectionSkeleton />
      ) : visible.length === 0 ? (
        <p className="text-xs text-aegisaxiom-text-muted">No open blockers.</p>
      ) : (
        <ul className="space-y-1.5">
          {visible.map((b) => (
            <li key={b.blocker_ticket_id || b.id} className="text-xs text-aegisaxiom-text truncate flex items-start gap-1">
              <span className="text-red-400 shrink-0">•</span>
              <span className="truncate">{b.title || b.description || 'Blocker'}</span>
              {b.project_name && (
                <span className="shrink-0 text-aegisaxiom-text-muted ml-1">· {b.project_name}</span>
              )}
            </li>
          ))}
          {overflow > 0 && (
            <li className="text-xs text-violet-400">+{overflow} more</li>
          )}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// My PM Projects section (PM / Admin only)
// ---------------------------------------------------------------------------
function PmProjectsCard({ projects, loading, currentUserId }) {
  const mine = (projects ?? []).filter((p) => p.pm_id === currentUserId);
  const visible = mine.slice(0, 6);

  return (
    <div data-testid="my-day-pm-projects" className="flex-1 min-w-0">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-aegisaxiom-text-muted mb-2">
        My PM projects
      </h4>
      {loading ? (
        <SectionSkeleton />
      ) : visible.length === 0 ? (
        <p className="text-xs text-aegisaxiom-text-muted">No PM projects.</p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {visible.map((p) => (
            <span
              key={p.project_id}
              className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs bg-aegisaxiom-elevated border border-aegisaxiom-border text-aegisaxiom-text truncate max-w-[140px]"
              title={p.name}
            >
              {p.name}
            </span>
          ))}
          {mine.length > visible.length && (
            <span className="text-xs text-violet-400">
              +{mine.length - visible.length} more
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recently Changed section
// ---------------------------------------------------------------------------
function RecentChangesCard({ changes, loading }) {
  const visible = (changes ?? []).slice(0, 10);

  return (
    <div
      data-testid="my-day-recent-changes"
      className="border-t border-aegisaxiom-border pt-3 mt-3"
    >
      <h4 className="text-xs font-semibold uppercase tracking-wide text-aegisaxiom-text-muted mb-2">
        Recently changed (last 24h)
      </h4>
      {loading ? (
        <div className="flex gap-6">
          <SectionSkeleton />
          <SectionSkeleton />
        </div>
      ) : visible.length === 0 ? (
        <p className="text-xs text-aegisaxiom-text-muted">No recent changes.</p>
      ) : (
        <ul className="space-y-1">
          {visible.map((c, i) => (
            <li key={c.audit_id || i} className="text-xs text-aegisaxiom-text-muted truncate">
              <span className="text-aegisaxiom-text">
                {c.summary || `${c.action} — ${c.resource_type}`}
              </span>
              {c.timestamp && (
                <span className="ml-1 text-[10px]">{fmtRelative(c.timestamp)}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main tile
// ---------------------------------------------------------------------------
export default function MyDayTile() {
  const { user } = useAuth();
  const { hasPermission } = usePermissions();
  const role = deriveRole(hasPermission);
  const isPmOrAdmin = role === 'pm' || role === 'admin';
  const currentUserId = user?.user_id;
  const firstName = user?.first_name || user?.name?.split(' ')[0] || 'there';

  const [myTasks, setMyTasks] = useState(null);
  const [blockers, setBlockers] = useState(null);
  const [projects, setProjects] = useState(null);
  const [changes, setChanges] = useState(null);

  const [loadingTasks, setLoadingTasks] = useState(true);
  const [loadingBlockers, setLoadingBlockers] = useState(true);
  const [loadingProjects, setLoadingProjects] = useState(isPmOrAdmin);
  const [loadingChanges, setLoadingChanges] = useState(true);

  const [refreshKey, setRefreshKey] = useState(0);

  const fetchSection = useCallback(async (url, setter, setLoading) => {
    setLoading(true);
    try {
      const res = await api.get(url);
      if (!res.ok) { setter([]); return; }
      const data = await res.json();
      setter(data.items ?? data ?? []);
    } catch {
      setter([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // My tasks (all assigned, client-filtered to today in child component)
    fetchSection(
      `${API_URL}/api/projects/tasks/assigned-to-me?limit=100`,
      setMyTasks,
      setLoadingTasks,
    );

    // Blockers I own
    fetchSection(
      `${API_URL}/api/projects/blocker-tickets/owned-by-me`,
      setBlockers,
      setLoadingBlockers,
    );

    // My PM projects (gated)
    if (isPmOrAdmin) {
      fetchSection(
        `${API_URL}/api/projects/projects/mine?limit=50`,
        setProjects,
        setLoadingProjects,
      );
    }

    // Recent changes (Thread 1B — may not exist yet; treat any error as empty)
    fetchSection(
      `${API_URL}/api/projects/recent-changes?hours=24&limit=10`,
      setChanges,
      setLoadingChanges,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey, isPmOrAdmin]);

  return (
    <div
      data-testid="my-day-tile"
      className="glass-card p-5 mb-4"
    >
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-sm font-semibold text-aegisaxiom-text">My Day</h3>
          <p className="text-xs text-aegisaxiom-text-muted">Good morning, {firstName}</p>
        </div>
        <button
          type="button"
          onClick={() => setRefreshKey((k) => k + 1)}
          className="h-7 w-7 inline-flex items-center justify-center rounded-md text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors"
          aria-label="Refresh My Day"
        >
          <RefreshCw size={13} />
        </button>
      </div>

      {/* Top row — up to 3 sections side by side */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        <DueTodayCard tasks={myTasks} loading={loadingTasks} />
        <BlockersCard blockers={blockers} loading={loadingBlockers} />
        {isPmOrAdmin && (
          <PmProjectsCard
            projects={projects}
            loading={loadingProjects}
            currentUserId={currentUserId}
          />
        )}
      </div>

      {/* Recently changed — full-width below */}
      <RecentChangesCard changes={changes} loading={loadingChanges} />
    </div>
  );
}
