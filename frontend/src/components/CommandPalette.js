/**
 * CommandPalette — global Ctrl/Cmd + K overlay.
 *
 * Sections:
 *  1. Recent projects  — top 5, localStorage-cached for instant paint
 *  2. My tasks         — typeahead against /api/projects/tasks?assignee_id=__me
 *  3. People           — search assignable users (localStorage-cached)
 *  4. Recent activity  — /api/projects/recent-changes?hours=24
 *  5. Ask PMO Pilot    — always-visible bottom row; opens chat with query pre-sent
 *
 * Keyboard: up/down arrows, Enter to activate, Esc to close.
 * Typeahead is debounced 200ms. Each section capped at 5 visible results.
 */

import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { Command } from 'cmdk';
import {
  Bot,
  Clock,
  FolderKanban,
  Search,
  User,
  X,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { useAuth } from '@/contexts/AuthContext';

// ---------------------------------------------------------------------------
// localStorage cache helpers
// ---------------------------------------------------------------------------
const LS_RECENT_PROJECTS = 'aegisaxiom_cmd_recent_projects';
const LS_PEOPLE = 'aegisaxiom_cmd_people';
const MAX_SECTION = 5;

function lsGet(key, fallback = []) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function lsSet(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Ignore quota / private-browsing errors
  }
}

// ---------------------------------------------------------------------------
// Debounce hook
// ---------------------------------------------------------------------------
function useDebounced(value, delay) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export default function CommandPalette({ open, onOpenChange }) {
  const { user } = useAuth();
  const navigate = useNavigate();

  const [query, setQuery] = useState('');
  const debouncedQuery = useDebounced(query, 200);

  // Section data
  const [recentProjects, setRecentProjects] = useState(() => lsGet(LS_RECENT_PROJECTS));
  const [myTasks, setMyTasks] = useState([]);
  const [people, setPeople] = useState(() => lsGet(LS_PEOPLE));
  const [activity, setActivity] = useState([]);
  const [loading, setLoading] = useState(false);

  // Reset query when palette opens
  useEffect(() => {
    if (open) setQuery('');
  }, [open]);

  // On mount: fetch recent projects + people and refresh caches
  useEffect(() => {
    if (!user) return;

    api.get(`${API_URL}/api/projects/projects?limit=5`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (!data) return;
        const items = Array.isArray(data) ? data : (data.items ?? data.projects ?? []);
        const mapped = items.slice(0, MAX_SECTION).map((p) => ({
          id: p.project_id ?? p._id,
          label: p.name,
          type: p.project_type,
        }));
        setRecentProjects(mapped);
        lsSet(LS_RECENT_PROJECTS, mapped);
      })
      .catch(() => {});

    api.get(`${API_URL}/api/projects/assignable-users?role=member`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (!data) return;
        const items = Array.isArray(data) ? data : (data.items ?? []);
        const mapped = items.map((u) => ({
          id: u.user_id ?? u._id,
          name: u.name ?? u.display_name ?? u.email,
          email: u.email,
        }));
        setPeople(mapped);
        lsSet(LS_PEOPLE, mapped);
      })
      .catch(() => {});

    api.get(`${API_URL}/api/projects/recent-changes?hours=24`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (!data) return;
        const items = Array.isArray(data) ? data : (data.items ?? data.changes ?? []);
        setActivity(items.slice(0, MAX_SECTION));
      })
      .catch(() => {});
  }, [user]);

  // Fetch my tasks whenever the debounced query changes
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    // Backend `search` only filters inside the project-scoped smart-view
    // path; this is a cross-project list, so we filter client-side after
    // fetching a slightly wider window of my-tasks.
    const qs = new URLSearchParams({ assignee_id: '__me', limit: '50' });

    api.get(`${API_URL}/api/projects/tasks?${qs}`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (!data) return;
        const items = Array.isArray(data) ? data : (data.items ?? data.tasks ?? []);
        const q = (debouncedQuery ?? '').trim().toLowerCase();
        const filtered = q
          ? items.filter((t) =>
              (t.title ?? '').toLowerCase().includes(q) ||
              String(t.task_number ?? '').toLowerCase().includes(q),
            )
          : items;
        setMyTasks(filtered.slice(0, MAX_SECTION));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [debouncedQuery, open]);

  // Close on outside click (cmdk handles Esc natively)
  const containerRef = useRef(null);
  useEffect(() => {
    if (!open) return;
    function handleClick(e) {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        onOpenChange(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open, onOpenChange]);

  // ------------------------------------------------------------------
  // Actions
  // ------------------------------------------------------------------
  const goToProject = useCallback((id) => {
    navigate(`/projects/${id}`);
    onOpenChange(false);
  }, [navigate, onOpenChange]);

  const askPmoPilot = useCallback(async (text) => {
    onOpenChange(false);
    // Navigate with the query as a URL param. ChatPage reads `?q=…` on mount
    // and pre-fills the composer so the user can edit before sending. This
    // replaced an earlier auto-send path that lost the message because
    // sendMessage fired before ChatPage had bound the new session.
    const params = new URLSearchParams({ agent: 'pmo' });
    if (text) params.set('q', text);
    navigate(`/chat?${params}`);
  }, [navigate, onOpenChange]);

  const filterPeopleByQuery = (q) => {
    if (!q) return people.slice(0, MAX_SECTION);
    const lower = q.toLowerCase();
    return people
      .filter((p) => p.name?.toLowerCase().includes(lower) || p.email?.toLowerCase().includes(lower))
      .slice(0, MAX_SECTION);
  };

  if (!open) return null;

  const filteredPeople = filterPeopleByQuery(debouncedQuery);
  const pilotLabel = debouncedQuery
    ? `Ask PMO Pilot: "${debouncedQuery}"`
    : 'Ask PMO Pilot…';

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-start justify-center pt-[12vh] bg-black/60"
      data-testid="command-palette-overlay"
    >
      <div
        ref={containerRef}
        className="w-full max-w-xl"
        data-testid="command-palette"
      >
        <Command
          className="rounded-xl border border-aegisaxiom-border bg-aegisaxiom-surface shadow-2xl overflow-hidden"
          shouldFilter={false}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onOpenChange(false);
          }}
        >
          {/* Search input */}
          <div className="flex items-center border-b border-aegisaxiom-border px-3 gap-2">
            <Search size={15} className="text-aegisaxiom-text-muted shrink-0" />
            <Command.Input
              value={query}
              onValueChange={setQuery}
              placeholder="Search projects, tasks, people…"
              className="w-full h-12 bg-transparent text-sm text-aegisaxiom-text outline-none placeholder:text-aegisaxiom-text-muted"
              data-testid="command-palette-input"
              autoFocus
            />
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text shrink-0"
              aria-label="Close command palette"
            >
              <X size={14} />
            </button>
          </div>

          <Command.List
            className="max-h-[420px] overflow-y-auto py-2"
            data-testid="command-palette-list"
          >
            {/* Recent projects */}
            {recentProjects.length > 0 && (
              <Command.Group
                heading="Recent projects"
                className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wide [&_[cmdk-group-heading]]:text-aegisaxiom-text-muted"
              >
                {recentProjects
                  .filter((p) => !debouncedQuery || p.label?.toLowerCase().includes(debouncedQuery.toLowerCase()))
                  .slice(0, MAX_SECTION)
                  .map((p) => (
                    <Command.Item
                      key={p.id}
                      value={`project-${p.id}`}
                      onSelect={() => goToProject(p.id)}
                      className="flex items-center gap-2.5 px-3 py-2 text-sm text-aegisaxiom-text cursor-pointer aria-selected:bg-aegisaxiom-elevated rounded-md mx-1"
                      data-testid={`cmd-project-${p.id}`}
                    >
                      <FolderKanban size={14} className="text-violet-400 shrink-0" />
                      <span className="truncate">{p.label}</span>
                      {p.type && (
                        <span className="ml-auto text-[10px] text-aegisaxiom-text-muted shrink-0 capitalize">
                          {p.type.replace(/_/g, ' ')}
                        </span>
                      )}
                    </Command.Item>
                  ))}
              </Command.Group>
            )}

            {/* My tasks */}
            <Command.Group
              heading="My tasks"
              className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wide [&_[cmdk-group-heading]]:text-aegisaxiom-text-muted"
            >
              {loading && (
                <div className="px-3 py-2 text-xs text-aegisaxiom-text-muted" data-testid="cmd-tasks-loading">
                  Loading…
                </div>
              )}
              {!loading && myTasks.length === 0 && (
                <Command.Empty className="px-3 py-2 text-xs text-aegisaxiom-text-muted">
                  No tasks found
                </Command.Empty>
              )}
              {myTasks.map((t) => {
                const tid = t.task_id ?? t._id;
                const pid = t.project_id;
                return (
                  <Command.Item
                    key={tid}
                    value={`task-${tid}`}
                    onSelect={() => {
                      if (pid) navigate(`/projects/${pid}`);
                      onOpenChange(false);
                    }}
                    className="flex items-center gap-2.5 px-3 py-2 text-sm text-aegisaxiom-text cursor-pointer aria-selected:bg-aegisaxiom-elevated rounded-md mx-1"
                    data-testid={`cmd-task-${tid}`}
                  >
                    <span
                      className="h-2 w-2 rounded-full shrink-0"
                      style={{ backgroundColor: statusColor(t.status) }}
                    />
                    <span className="truncate flex-1">{t.title}</span>
                    {t.priority && (
                      <span className={`text-[10px] shrink-0 ${priorityClass(t.priority)}`}>
                        {t.priority}
                      </span>
                    )}
                  </Command.Item>
                );
              })}
            </Command.Group>

            {/* People */}
            {filteredPeople.length > 0 && (
              <Command.Group
                heading="People"
                className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wide [&_[cmdk-group-heading]]:text-aegisaxiom-text-muted"
              >
                {filteredPeople.map((person) => (
                  <Command.Item
                    key={person.id}
                    value={`person-${person.id}`}
                    onSelect={() => {
                      askPmoPilot(`Tell me about ${person.name}'s tasks and workload`);
                    }}
                    className="flex items-center gap-2.5 px-3 py-2 text-sm text-aegisaxiom-text cursor-pointer aria-selected:bg-aegisaxiom-elevated rounded-md mx-1"
                    data-testid={`cmd-person-${person.id}`}
                  >
                    <User size={14} className="text-blue-400 shrink-0" />
                    <span className="truncate">{person.name}</span>
                    {person.email && (
                      <span className="ml-auto text-[10px] text-aegisaxiom-text-muted shrink-0 truncate max-w-[140px]">
                        {person.email}
                      </span>
                    )}
                  </Command.Item>
                ))}
              </Command.Group>
            )}

            {/* Recent activity */}
            {!debouncedQuery && activity.length > 0 && (
              <Command.Group
                heading="Recent activity"
                className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wide [&_[cmdk-group-heading]]:text-aegisaxiom-text-muted"
              >
                {activity.map((a, i) => {
                  const pid = a.project_id;
                  return (
                    <Command.Item
                      key={a.activity_id ?? a._id ?? i}
                      value={`activity-${i}`}
                      onSelect={() => {
                        if (pid) navigate(`/projects/${pid}`);
                        onOpenChange(false);
                      }}
                      className="flex items-center gap-2.5 px-3 py-2 text-sm text-aegisaxiom-text cursor-pointer aria-selected:bg-aegisaxiom-elevated rounded-md mx-1"
                      data-testid={`cmd-activity-${i}`}
                    >
                      <Clock size={14} className="text-aegisaxiom-text-muted shrink-0" />
                      <span className="truncate flex-1">
                        {a.summary ?? a.description ?? `${a.action ?? 'Updated'} ${a.entity_type ?? 'item'}`}
                      </span>
                    </Command.Item>
                  );
                })}
              </Command.Group>
            )}

            {/* Ask PMO Pilot — always visible at the bottom */}
            <Command.Group
              className="border-t border-aegisaxiom-border mt-1 pt-1"
            >
              <Command.Item
                value="ask-pmo-pilot"
                onSelect={() => askPmoPilot(debouncedQuery || 'Hello, I need help with my projects.')}
                className="flex items-center gap-2.5 px-3 py-2 text-sm text-violet-300 cursor-pointer aria-selected:bg-violet-600/10 rounded-md mx-1"
                data-testid="cmd-ask-pmo-pilot"
              >
                <Bot size={14} className="text-violet-400 shrink-0" />
                <span className="truncate">{pilotLabel}</span>
              </Command.Item>
            </Command.Group>
          </Command.List>
        </Command>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small style helpers (no external dep)
// ---------------------------------------------------------------------------
function statusColor(status) {
  const map = {
    complete: '#4ade80',
    done: '#4ade80',
    in_progress: '#60a5fa',
    blocked: '#f87171',
    todo: '#9ca3af',
  };
  return map[status] ?? '#9ca3af';
}

function priorityClass(priority) {
  const map = {
    critical: 'text-red-400',
    high: 'text-orange-400',
    medium: 'text-yellow-400',
    low: 'text-aegisaxiom-text-muted',
  };
  return map[priority] ?? 'text-aegisaxiom-text-muted';
}
