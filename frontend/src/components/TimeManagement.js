import React, { useEffect, useState, useMemo, useCallback } from 'react';
import { api, unwrap } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/hooks/usePermissions';
import {
  Clock,
  Plus,
  Send,
  Check,
  X,
  ChevronLeft,
  ChevronRight,
  CheckCircle,
  Trash2,
  Lock,
} from 'lucide-react';
import { toast } from 'sonner';
import API_URL from '../lib/apiConfig';
import {
  addDays,
  fromLocalISO,
  startOfMondayWeek,
  toLocalISO,
} from '@/lib/localDate';

const API = API_URL;
// Display time in hours and minutes
const fmtTime = (hours) => {
  const h = Math.floor(hours || 0);
  const m = Math.round(((hours || 0) - h) * 60);
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
};
const fmt$ = (n) => `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}`;

// Parse an HH:MM string into minute-of-day. Returns null when the
// input is empty or malformed so the caller can render a friendly
// "End must be after start" message instead of NaN.
const parseHHMM = (s) => {
  if (!s || typeof s !== 'string') return null;
  const m = /^(\d{1,2}):(\d{2})(?::\d{2})?$/.exec(s.trim());
  if (!m) return null;
  const hh = parseInt(m[1], 10);
  const mm = parseInt(m[2], 10);
  if (Number.isNaN(hh) || Number.isNaN(mm)) return null;
  if (hh < 0 || hh > 24 || mm < 0 || mm > 59) return null;
  return hh * 60 + mm;
};
// Mirror of services/time_entry_input.parse_time_entry_payload —
// duration in decimal hours, or 0 when the pair is incomplete /
// inverted. Server still re-runs this on save (single source of
// truth), so this is purely for the inline UX hint.
const timeRangeDurationHours = (start, end) => {
  const a = parseHHMM(start);
  const b = parseHHMM(end);
  if (a == null || b == null) return 0;
  if (b <= a) return 0;
  return (b - a) / 60;
};
// Snap to the same 10-minute boundary the backend enforces.
const snapToTenMinutes = (hours) => {
  if (!hours || hours <= 0) return 0;
  const minutes = Math.round(hours * 60);
  return (Math.round(minutes / 10) * 10) / 60;
};

// Mirror of services.time_entry_input.parse_manual_duration used
// for the inline UX preview only. The server re-parses
// authoritatively on submit; this function returns
// {minutes, hours, label} on success or {error: '...'} on bad input.
//
// May 24, 2026 (revised): the dot is a TIME SEPARATOR, not a
// decimal point. "2.30" means 2h30m (150 min), same as "2:30".
// "2.03" means 2h3m. Decimal-hours interpretation is gone.
// Users who need decimal hours via automation keep using the
// numeric `hours` payload field on the API.
const parseManualDurationPreview = (raw) => {
  const s = String(raw || '').trim().toLowerCase();
  if (!s) return { error: 'Enter a valid duration.' };

  const labelHM = (minutes) => {
    const hh = Math.floor(minutes / 60);
    const mm = minutes % 60;
    return mm ? `${hh}h ${mm}m` : `${hh}h`;
  };
  const formatHours = (minutes) => (minutes / 60).toFixed(2);

  // H + M combo form (most explicit).
  let m = /^(\d+)\s*(?:h|hr|hrs|hour|hours)(?:\s*(\d+)\s*(?:m|min|mins|minute|minutes))?$/.exec(s);
  if (m) {
    const hh = parseInt(m[1], 10);
    const mm = m[2] !== undefined ? parseInt(m[2], 10) : 0;
    if (mm >= 60) return { error: 'Minutes must be 00–59.' };
    const minutes = hh * 60 + mm;
    if (minutes <= 0) return { error: 'Duration must be greater than 0.' };
    return { minutes, hours: minutes / 60, label: `${labelHM(minutes)} = ${formatHours(minutes)}h` };
  }

  // H:MM form — minutes must be exactly two digits.
  m = /^(\d{1,3}):(\d{2})$/.exec(s);
  if (m) {
    const hh = parseInt(m[1], 10);
    const mm = parseInt(m[2], 10);
    if (mm >= 60) return { error: 'Minutes must be 00–59.' };
    const minutes = hh * 60 + mm;
    if (minutes <= 0) return { error: 'Duration must be greater than 0.' };
    return { minutes, hours: minutes / 60, label: `${s} = ${labelHM(minutes)}` };
  }
  // Reject loose H:M (single-digit minutes) and >= 60 minutes.
  let bad = /^(\d{1,3}):(\d{1,3})$/.exec(s);
  if (bad) {
    if (bad[2].length !== 2) return { error: 'Enter a valid duration.' };
    if (parseInt(bad[2], 10) >= 60) return { error: 'Minutes must be 00–59.' };
  }

  // H.MM form — minutes must be exactly two digits. THE rule that
  // changed on 2026-05-24: "2.30" -> 2h30m, NOT 2.3 decimal hours.
  m = /^(\d+)\.(\d{2})$/.exec(s);
  if (m) {
    const hh = parseInt(m[1], 10);
    const mm = parseInt(m[2], 10);
    if (mm >= 60) return { error: 'Minutes must be 00–59.' };
    const minutes = hh * 60 + mm;
    if (minutes <= 0) return { error: 'Duration must be greater than 0.' };
    return { minutes, hours: minutes / 60, label: `${s} = ${labelHM(minutes)}` };
  }
  // Reject 2.3 (one digit) and 2.75 (minutes >= 60).
  bad = /^(\d+)\.(\d+)$/.exec(s);
  if (bad) {
    if (bad[2].length !== 2) return { error: 'Enter a valid duration.' };
    if (parseInt(bad[2], 10) >= 60) return { error: 'Minutes must be 00–59.' };
  }

  // Minutes-only form.
  m = /^(\d+)\s*(?:m|min|mins|minute|minutes)$/.exec(s);
  if (m) {
    const minutes = parseInt(m[1], 10);
    if (minutes <= 0) return { error: 'Duration must be greater than 0.' };
    return { minutes, hours: minutes / 60, label: `${minutes}m = ${labelHM(minutes)}` };
  }

  // Whole-number HOURS form (no dot, no colon, no suffix).
  m = /^(\d+)$/.exec(s);
  if (m) {
    const hh = parseInt(m[1], 10);
    if (hh <= 0) return { error: 'Duration must be greater than 0.' };
    const minutes = hh * 60;
    return { minutes, hours: hh, label: `${hh}h` };
  }

  return { error: 'Enter a valid duration.' };
};

const HOUR_PRESETS = [0.17, 0.33, 0.5, 0.67, 0.83, 1, 1.5, 2, 2.5, 3, 3.5, 4];
// May 24 (revised): chip labels use h/m time notation, never
// decimal-hour shorthand. The internal numeric value still drives
// the API payload (backward compat with the numeric `hours` field);
// only the user-facing label changes.
const PRESET_LABELS = {
  0.17: '10m',
  0.33: '20m',
  0.5: '30m',
  0.67: '40m',
  0.83: '50m',
  1: '1h',
  1.5: '1h 30m',
  2: '2h',
  2.5: '2h 30m',
  3: '3h',
  3.5: '3h 30m',
  4: '4h',
};
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

const STATUS_COLORS = {
  draft: 'bg-aegisaxiom-elevated/20 text-aegisaxiom-text-secondary',
  submitted: 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300',
  approved: 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300',
  rejected: 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300',
};

// Local-calendar helpers — wired through @/lib/localDate so the
// week start, day columns, and submitted ISO dates all use the
// same UTC-safe arithmetic. Previous implementations used
// `new Date('YYYY-MM-DD' + 'T00:00:00')` plus `toISOString().split`
// which silently shifted the calendar by one day in positive-
// offset timezones (Africa/Nairobi etc.) — see investigation
// notes in docs/contracts-time-mgmt/.

function getWeekStart(anchor) {
  // anchor can be either a Date (today) or a YYYY-MM-DD string
  // (clicking back/forward week from the current weekStart).
  const date = anchor instanceof Date ? anchor : fromLocalISO(anchor);
  return toLocalISO(startOfMondayWeek(date));
}

function getWeekDates(weekStartIso) {
  const start = fromLocalISO(weekStartIso);
  return Array.from({ length: 7 }, (_, i) => toLocalISO(addDays(start, i)));
}

function formatDate(dateStr) {
  if (!dateStr) return '';
  return fromLocalISO(dateStr).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  });
}

export default function TimeManagement() {
  const { user } = useAuth();
  const { isAdmin, hasPermission, hasAnyPermission } = usePermissions();
  const [view, setView] = useState('timesheet'); // 'timesheet' | 'approvals' | 'summary'
  const [weekStart, setWeekStart] = useState(getWeekStart(new Date()));
  const [entries, setEntries] = useState([]);
  const [projects, setProjects] = useState([]);
  // 'idle' before the first fetch resolves, 'loaded' after a 2xx,
  // 'error' after a non-OK response. Lets the picker distinguish a
  // genuine "no eligible projects" empty state from a load failure.
  const [projectsLoadStatus, setProjectsLoadStatus] = useState('idle');
  const [contracts, setContracts] = useState([]); // Load contracts for context
  const [users, setUsers] = useState([]); // All users (for admins creating entries for others)
  const [resourceTypes, setResourceTypes] = useState([]); // Available resource types for selected project/contract
  const [weekData, setWeekData] = useState(null);
  const [approvalQueue, setApprovalQueue] = useState([]);
  const [summaryData, setSummaryData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [testDataOnly, setTestDataOnly] = useState(false); // Default: show real data
  const [showAllUnapproved, setShowAllUnapproved] = useState(false); // Filter for all unapproved timesheets
  const [allUnapprovedEntries, setAllUnapprovedEntries] = useState([]); // All unapproved entries sorted by date

  // Quick-add form state.
  // Zoho-parity (May 22, 2026):
  //   * `hours`        — decimal hours; accepts 1.5; snapped server-side
  //                       to the documented 10-minute increment.
  //   * `start_time`   — optional HH:MM picker; when paired with end_time
  //                       AND hours is blank, the server derives hours.
  //   * `end_time`     — paired with start_time.
  // The backend helper services.time_entry_input is the source of truth
  // for the snap math; the frontend just renders a human-friendly
  // duration label below the inputs.
  const [addRow, setAddRow] = useState({
    user_id: '',
    project_id: '',
    pm_task_id: '',
    date: '',
    hours: 1,
    start_time: '',
    end_time: '',
    // Manual duration free-text input (May 24, 2026). Accepts
    // "6h 15m", "2:03", "375m", "2.05 hours", and bare decimals
    // like "6.25". Parsed server-side by
    // services.time_entry_input.parse_manual_duration; the route
    // persists exact minutes on raw_minutes alongside the snapped
    // `hours` field used by billing snapshots.
    manual_duration: '',
    notes: '',
    is_billable: true,
    resource_type_id: '',
  });
  const [showAdd, setShowAdd] = useState(false);

  // Quick-add chip range — mirrors the backend's documented
  // QUICK_ADD_MIN_HOURS / QUICK_ADD_MAX_HOURS (1.0 to 4.0 in 0.5h
  // ticks). Selecting a chip overrides any start/end picker values
  // so the user's most recent action wins.
  const QUICK_ADD_HOUR_CHIPS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0];

  const canApprove = isAdmin() || hasPermission('time.approve');
  const canViewAll = isAdmin() || hasPermission('time.view.all');
  const canCreateForOthers = isAdmin() || hasPermission('time.entry.admin');

  const weekDates = useMemo(() => getWeekDates(weekStart), [weekStart]);

  useEffect(() => {
    loadData();
  }, [weekStart, view, testDataOnly]);

  // Fetch available resource types when project is selected
  useEffect(() => {
    if (addRow.project_id) {
      loadResourceTypes(addRow.project_id);
    } else {
      setResourceTypes([]);
      setAddRow((r) => ({ ...r, resource_type_id: '' }));
    }
  }, [addRow.project_id]);

  const loadResourceTypes = async (projectId) => {
    try {
      const project = projects.find((p) => p.project_id === projectId);
      if (!project || !project.contract_id) {
        setResourceTypes([]);
        return;
      }

      const res = await api.get(
        `${API}/api/contractor-resource-types?contract_id=${project.contract_id}`,
      );
      if (res.ok) {
        const data = await res.json();
        setResourceTypes(data.resource_types || []);
        // Auto-select if only one option
        if (data.resource_types?.length === 1) {
          setAddRow((r) => ({ ...r, resource_type_id: data.resource_types[0].resource_type_id }));
        } else {
          setAddRow((r) => ({ ...r, resource_type_id: '' }));
        }
      }
    } catch (err) {
      console.error('Failed to load resource types:', err);
      setResourceTypes([]);
    }
  };

  const loadData = async () => {
    setLoading(true);
    try {
      // The canonical project list is GET /api/projects/projects
      // (the projects_project router is mounted with prefix "/projects"
      // and the list route is also "/projects" — hence the doubled
      // segment). The earlier GET /api/projects call 404'd silently
      // and left the picker empty.
      const promises = [
        api.get(`${API}/api/projects/projects?limit=500`),
        api.get(`${API}/api/contracts?limit=500`), // Load contracts for context
      ];

      // Load all users if user has permission to create entries for others
      if (canCreateForOthers) {
        promises.push(api.get(`${API}/api/admin/users?limit=500`));
      }

      const results = await Promise.all(promises);
      const [pRes, cRes, uRes] = results;

      if (pRes.ok) {
        setProjects(unwrap(await pRes.json()));
        setProjectsLoadStatus('loaded');
      } else {
        setProjectsLoadStatus('error');
      }
      if (cRes.ok) setContracts(unwrap(await cRes.json()));
      if (uRes && uRes.ok) {
        const usersData = unwrap(await uRes.json());
        // Filter to show only contractors and active users
        const contractorUsers = usersData.filter(
          (u) =>
            u.is_active && (u.role === 'contractor' || u.role === 'agent' || u.role === 'customer'),
        );
        setUsers(contractorUsers);
      }

      if (view === 'timesheet') {
        const testParam = isAdmin ? `&is_test_data=${testDataOnly}` : '';
        const res = await api.get(
          `${API}/api/time-entries/my-week?week_start=${weekStart}${testParam}`,
        );
        if (res.ok) {
          const data = await res.json();
          setWeekData(data);
          setEntries(data.entries || []);
        }
      } else if (view === 'approvals') {
        const testParam = isAdmin ? `&is_test_data=${testDataOnly}` : '';
        const res = await api.get(
          `${API}/api/time-entries/approval-queue?week_start=${weekStart}${testParam}`,
        );
        if (res.ok) {
          const data = await res.json();
          setApprovalQueue(data.items || []);
        }

        // Also load all unapproved entries for the filter
        const allRes = await api.get(
          `${API}/api/time-entries/all-unapproved${testParam ? '?is_test_data=' + testDataOnly : ''}`,
        );
        if (allRes.ok) {
          const allData = await allRes.json();
          // Sort by date descending (most recent first)
          const sorted = (allData.items || []).sort((a, b) => new Date(b.date) - new Date(a.date));
          setAllUnapprovedEntries(sorted);
        }
      } else if (view === 'summary') {
        const testParam = isAdmin ? `&is_test_data=${testDataOnly}` : '';
        const res = await api.get(
          `${API}/api/time-entries/weekly-summary?week_start=${weekStart}${testParam}`,
        );
        if (res.ok) setSummaryData(await res.json());
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const navigateWeek = (dir) => {
    // Local-calendar arithmetic only — never round-trip through
    // toISOString here or evenings in Africa/Nairobi land you a
    // day early.
    setWeekStart(toLocalISO(addDays(fromLocalISO(weekStart), dir * 7)));
  };

  // Jump-to-current-week anchors to the user's REAL local "today"
  // (not the test-data fixture week, not the first loaded entry).
  // Test Data Only filter only changes which entries are listed.
  const goToCurrentWeek = () => setWeekStart(getWeekStart(new Date()));

  // Quick-add time entry
  const handleQuickAdd = async () => {
    if (!addRow.project_id || !addRow.date || !addRow.notes.trim()) {
      toast.error('Project, date, and notes are required');
      return;
    }
    if (resourceTypes.length > 0 && !addRow.resource_type_id) {
      toast.error('Please select your role/resource type');
      return;
    }
    try {
      const res = await api.post(`${API}/api/time-entries`, addRow);
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail);
      }
      const entry = await res.json();
      if (entry.note_feedback?.quality === 'fair') {
        toast.info(entry.note_feedback.message);
      }
      const userName = addRow.user_id
        ? users.find((u) => u.user_id === addRow.user_id)?.name
        : 'yourself';
      toast.success(`Added ${addRow.hours}h for ${userName}`);
      setAddRow({
        user_id: addRow.user_id, // Keep selected user
        project_id: addRow.project_id,
        date: '',
        hours: 1,
        // Clear the Zoho-style range picker AND the manual-duration
        // text input too so the next entry starts on the chips path,
        // not on a stale window/text.
        start_time: '',
        end_time: '',
        manual_duration: '',
        notes: '',
        is_billable: true,
        resource_type_id: addRow.resource_type_id,
      });
      loadData();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const deleteEntry = async (entryId) => {
    try {
      const res = await api.delete(`${API}/api/time-entries/${entryId}`);
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail);
      }
      toast.success('Entry deleted');
      loadData();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const submitWeek = async () => {
    try {
      const res = await api.post(`${API}/api/time-entries/submit-week`, { week_start: weekStart });
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail);
      }
      const data = await res.json();
      toast.success(data.message);
      loadData();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const approveEntry = async (entryId, opts = {}) => {
    try {
      const res = await api.post(`${API}/api/time-entries/${entryId}/approve`, opts);
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail);
      }
      toast.success('Entry approved');
      loadData();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const rejectEntry = async (entryId) => {
    const notes = prompt('Rejection reason (required):');
    if (!notes || !notes.trim()) return;
    try {
      const res = await api.post(`${API}/api/time-entries/${entryId}/reject`, { notes });
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail);
      }
      toast.success('Entry rejected');
      loadData();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const bulkApprove = async (entryIds) => {
    try {
      const res = await api.post(`${API}/api/time-entries/bulk-approve`, { entry_ids: entryIds });
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail);
      }
      const data = await res.json();
      toast.success(data.message);
      loadData();
    } catch (err) {
      toast.error(err.message);
    }
  };

  // Group entries by project for timesheet view
  const entriesByProject = useMemo(() => {
    const map = {};
    entries.forEach((e) => {
      const pid = e.project_id || '_none';
      if (!map[pid]) map[pid] = { project_id: pid, project_name: '', entries: [], totalHours: 0 };
      map[pid].entries.push(e);
      map[pid].totalHours += e.hours || 0;
    });
    // Enrich with project name
    Object.values(map).forEach((g) => {
      const proj = projects.find((p) => p.project_id === g.project_id);
      g.project_name = proj?.name || (g.project_id === '_none' ? 'No Project' : g.project_id);
    });
    return Object.values(map);
  }, [entries, projects]);

  const draftCount = entries.filter((e) => e.status === 'draft').length;
  const weekEnd = weekDates[6];
  const isLocked = weekData?.locked;

  return (
    <div data-testid="time-management-page">
      {/* Header */}
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3 mb-6">
        <div>
          <h1 className="page-title flex items-center gap-2">
            <Clock size={20} className="text-violet-600 dark:text-violet-400" /> Time Management
          </h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">
            Track hours, submit timesheets, manage approvals
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {/* Test Data Filter (Admin Only) */}
          {isAdmin && (
            <label className="flex items-center gap-2 px-3 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-xs cursor-pointer hover:bg-aegisaxiom-elevated transition">
              <input
                type="checkbox"
                checked={testDataOnly}
                onChange={(e) => setTestDataOnly(e.target.checked)}
                className="w-3.5 h-3.5 rounded border-aegisaxiom-border-hover bg-aegisaxiom-elevated text-violet-600 focus:ring-2 focus:ring-violet-500 focus:ring-offset-0"
              />
              <span className="text-aegisaxiom-text-secondary">Test Data Only</span>
            </label>
          )}
          {/* View Tabs — horizontally scrollable on mobile so all tabs are reachable */}
          <div className="overflow-x-auto -mx-1 px-1">
            <div className="flex gap-1.5 bg-aegisaxiom-elevated rounded-lg p-0.5 w-fit">
              <button
                onClick={() => setView('timesheet')}
                className={`px-3 py-1 text-xs rounded-md transition whitespace-nowrap shrink-0 ${view === 'timesheet' ? 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
                data-testid="time-tab-timesheet"
              >
                My Timesheet
              </button>
              {canApprove && (
                <button
                  onClick={() => setView('approvals')}
                  className={`px-3 py-1 text-xs rounded-md transition whitespace-nowrap shrink-0 ${view === 'approvals' ? 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
                  data-testid="time-tab-approvals"
                >
                  Approvals
                </button>
              )}
              {(canViewAll || canApprove) && (
                <button
                  onClick={() => setView('summary')}
                  className={`px-3 py-1 text-xs rounded-md transition whitespace-nowrap shrink-0 ${view === 'summary' ? 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
                  data-testid="time-tab-summary"
                >
                  AP / AR Summary
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Week Navigator */}
      <div className="flex items-center gap-2 bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg px-3 sm:px-4 py-2.5 mb-6">
        <button
          onClick={() => navigateWeek(-1)}
          className="p-1.5 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated rounded transition shrink-0"
          title="Previous week"
          aria-label="Previous week"
        >
          <ChevronLeft size={18} />
        </button>
        <div className="flex items-center justify-center gap-2 sm:gap-3 flex-wrap flex-1 min-w-0">
          <span className="text-xs sm:text-sm font-medium text-aegisaxiom-text">
            Week of {formatDate(weekStart)} — {formatDate(weekEnd)}
          </span>
          <button
            onClick={goToCurrentWeek}
            className="px-2 py-1 text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:hover:text-violet-300 hover:bg-violet-50 dark:hover:bg-violet-600/10 rounded transition"
          >
            Jump to Current Week
          </button>
          {isLocked && !canCreateForOthers && (
            <span className="flex items-center gap-1 text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-600/10 px-2 py-1 rounded">
              <Lock size={12} /> Locked (Mon 5pm ET deadline passed)
            </span>
          )}
          {canCreateForOthers && isLocked && (
            <span className="text-xs text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-600/10 px-2 py-1 rounded">
              ✓ Admin: Can create past due entries
            </span>
          )}
        </div>
        <button
          onClick={() => navigateWeek(1)}
          className="p-1.5 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated rounded transition shrink-0"
          title="Next week"
          aria-label="Next week"
        >
          <ChevronRight size={18} />
        </button>
      </div>

      {/* ============ TIMESHEET VIEW ============ */}
      {view === 'timesheet' && (
        <div>
          {/* Summary Cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-5 mt-6 mb-6">
            <div className="glass-card p-5">
              <p className="text-sm text-aegisaxiom-text-secondary mb-2">Total Hours</p>
              <p className="text-2xl font-semibold text-aegisaxiom-text">
                {fmtTime(weekData?.total_hours || 0)}
              </p>
            </div>
            <div className="glass-card p-5">
              <p className="text-sm text-aegisaxiom-text-secondary mb-2">Billable Hours</p>
              <p className="text-2xl font-semibold text-emerald-600 dark:text-emerald-400">
                {fmtTime(weekData?.billable_hours || 0)}
              </p>
            </div>
            <div className="glass-card p-5">
              <p className="text-sm text-aegisaxiom-text-secondary mb-2">Entries</p>
              <p className="text-2xl font-semibold text-aegisaxiom-text">{entries.length}</p>
            </div>
            <div className="glass-card p-5">
              <p className="text-sm text-aegisaxiom-text-secondary mb-2">Status</p>
              <div className="flex gap-1.5 mt-1 flex-wrap">
                {Object.entries(weekData?.status_counts || {}).map(([s, c]) => (
                  <span key={s} className={`px-1.5 py-0.5 rounded text-[10px] ${STATUS_COLORS[s]}`}>
                    {s}: {c}
                  </span>
                ))}
                {entries.length === 0 && (
                  <span className="text-xs text-aegisaxiom-text-muted">No entries</span>
                )}
              </div>
            </div>
          </div>

          {/* Quick Add Row - Show for non-locked weeks OR for admins who can create past due entries */}
          {(!isLocked || canCreateForOthers) && (
            <div className="mb-6 bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg p-3">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-xs font-semibold text-aegisaxiom-text flex items-center gap-1.5">
                  <Plus size={14} /> Quick Add Time Entry
                  {isLocked && canCreateForOthers && (
                    <span className="text-[10px] text-emerald-600 dark:text-emerald-400 ml-2">
                      (Admin override - week is locked for contractors)
                    </span>
                  )}
                </h3>
              </div>
              <div className="flex flex-wrap gap-2 items-end">
                {/* User Selector (Only for admins/PMs creating entries for others) */}
                {canCreateForOthers && (
                  <div className="flex-1 min-w-[150px]">
                    <label className="text-[10px] text-aegisaxiom-text-muted block mb-0.5">
                      User{' '}
                      {addRow.user_id && (
                        <span className="text-emerald-600 dark:text-emerald-400">
                          (creating for {users.find((u) => u.user_id === addRow.user_id)?.name})
                        </span>
                      )}
                    </label>
                    <select
                      value={addRow.user_id}
                      onChange={(e) =>
                        setAddRow((r) => ({ ...r, user_id: e.target.value, resource_type_id: '' }))
                      }
                      className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                    >
                      <option value="">Me (create for myself)</option>
                      {users.map((u) => (
                        <option key={u.user_id} value={u.user_id}>
                          {u.name} ({u.email})
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                {/* Project */}
                <div className="flex-1 min-w-[200px]">
                  <label className="text-[10px] text-aegisaxiom-text-muted block mb-0.5">Project</label>
                  <select
                    value={addRow.project_id}
                    onChange={(e) => setAddRow((r) => ({ ...r, project_id: e.target.value }))}
                    className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                    data-testid="time-entry-project-picker"
                  >
                    <option value="">Select project</option>
                    {projects.map((p) => {
                      const contract = contracts.find((c) => c.contract_id === p.contract_id);
                      return (
                        <option key={p.project_id} value={p.project_id}>
                          {p.name}{' '}
                          {contract
                            ? `[${contract.name || contract.contract_type?.toUpperCase()}]`
                            : ''}
                        </option>
                      );
                    })}
                  </select>
                  {/* Distinguish "load failed" from "loaded but nothing
                      eligible" so the user knows whether to retry or to
                      ask their PM for a project assignment. */}
                  {projectsLoadStatus === 'error' && (
                    <p
                      className="text-[10px] text-red-600 dark:text-red-400 mt-0.5"
                      data-testid="time-entry-project-picker-error"
                    >
                      Failed to load projects. Refresh the page or contact support.
                    </p>
                  )}
                  {projectsLoadStatus === 'loaded' && projects.length === 0 && (
                    <p
                      className="text-[10px] text-amber-600 dark:text-amber-400 mt-0.5"
                      data-testid="time-entry-project-picker-empty"
                    >
                      No projects available for this user. Assign the user to a project or select another user.
                    </p>
                  )}
                  {/* Linked Contract surface — when the user has
                      picked a project we either confirm the link
                      (so the operator knows their hours WILL roll
                      up) or warn that the project has no contract
                      (in which case approved billable time will
                      NOT consume contract hours). The warning is
                      verbatim from the May 25 spec. */}
                  {addRow.project_id && (() => {
                    const selectedProject = projects.find(
                      (p) => p.project_id === addRow.project_id,
                    );
                    if (!selectedProject) return null;
                    const linkedContract = selectedProject.contract_id
                      ? contracts.find(
                          (c) => c.contract_id === selectedProject.contract_id,
                        )
                      : null;
                    if (linkedContract) {
                      return (
                        <p
                          className="text-[10px] text-emerald-600 dark:text-emerald-400 mt-0.5"
                          data-testid="time-entry-linked-contract"
                        >
                          Linked contract:{' '}
                          <span className="font-medium">
                            {linkedContract.name}
                          </span>
                          {linkedContract.contract_type
                            ? ` · ${linkedContract.contract_type}`
                            : ''}
                          {linkedContract.account_name
                            ? ` · ${linkedContract.account_name}`
                            : ''}
                        </p>
                      );
                    }
                    return (
                      <p
                        className="text-[10px] text-amber-600 dark:text-amber-400 mt-0.5"
                        data-testid="time-entry-no-contract-warning"
                      >
                        This project is not linked to a contract. Approved
                        billable time will not consume contract hours.
                      </p>
                    );
                  })()}
                </div>
                {/* Resource Type (Role) - Only show if available */}
                {resourceTypes.length > 0 && (
                  <div className="flex-1 min-w-[150px]">
                    <label className="text-[10px] text-aegisaxiom-text-muted block mb-0.5">
                      Role <span className="text-red-600 dark:text-red-400">*</span>
                    </label>
                    <select
                      value={addRow.resource_type_id}
                      onChange={(e) =>
                        setAddRow((r) => ({ ...r, resource_type_id: e.target.value }))
                      }
                      className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                      required
                    >
                      <option value="">Select role...</option>
                      {resourceTypes.map((rt) => (
                        <option key={rt.resource_type_id} value={rt.resource_type_id}>
                          {rt.name} (${rt.sell_rate}/hr)
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                {/* Date */}
                <div className="w-32">
                  <label className="text-[10px] text-aegisaxiom-text-muted block mb-0.5">Date</label>
                  <select
                    value={addRow.date}
                    onChange={(e) => setAddRow((r) => ({ ...r, date: e.target.value }))}
                    className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                  >
                    <option value="">Day</option>
                    {weekDates.map((d, i) => (
                      <option key={d} value={d}>
                        {DAYS[i]} {formatDate(d)}
                      </option>
                    ))}
                  </select>
                </div>
                {/* Hours - Quick Buttons */}
                <div className="w-48">
                  <label className="text-[10px] text-aegisaxiom-text-muted block mb-0.5">
                    Time ({fmtTime(addRow.hours || timeRangeDurationHours(addRow.start_time, addRow.end_time))})
                  </label>
                  <div className="flex flex-wrap gap-0.5">
                    {HOUR_PRESETS.map((h) => (
                      <button
                        key={h}
                        type="button"
                        onClick={() => setAddRow((r) => ({
                          ...r, hours: h,
                          // Selecting a chip clears the start/end pair AND
                          // the manual_duration field so the server doesn't
                          // have to disambiguate which duration the user
                          // meant. Matches the resolution order documented
                          // in services/time_entry_input.
                          start_time: '', end_time: '',
                          manual_duration: '',
                        }))}
                        data-testid={`time-entry-chip-${h}`}
                        className={`px-1.5 py-0.5 text-[10px] rounded transition ${addRow.hours === h && !addRow.start_time && !addRow.end_time && !addRow.manual_duration ? 'bg-violet-600 text-white' : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'}`}
                      >
                        {PRESET_LABELS[h]}
                      </button>
                    ))}
                  </div>
                </div>
                {/* Start / End time picker. Minute precision (step=60
                    seconds = 1 minute) per the May 24, 2026 review —
                    chips already cover the coarse case; the picker
                    now allows precise start/end too. The backend
                    still snaps the resulting duration to a 10-min
                    boundary for billing; raw_minutes carries the
                    exact value if the user wants per-minute precision
                    (use the manual_duration field below). */}
                <div className="w-44">
                  <label className="text-[10px] text-aegisaxiom-text-muted block mb-0.5">
                    Or start / end
                  </label>
                  <div className="flex items-center gap-1">
                    <input
                      type="time"
                      step="60"
                      value={addRow.start_time}
                      onChange={(e) => setAddRow((r) => ({
                        ...r, start_time: e.target.value,
                        // Clear the chip + manual_duration so the
                        // server reads the start/end pair.
                        hours: 0,
                        manual_duration: '',
                      }))}
                      data-testid="time-entry-start-time"
                      className="w-20 px-1.5 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                    />
                    <span className="text-[10px] text-aegisaxiom-text-muted">to</span>
                    <input
                      type="time"
                      step="60"
                      value={addRow.end_time}
                      onChange={(e) => setAddRow((r) => ({
                        ...r, end_time: e.target.value, hours: 0,
                        manual_duration: '',
                      }))}
                      data-testid="time-entry-end-time"
                      className="w-20 px-1.5 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                    />
                  </div>
                  {addRow.start_time && addRow.end_time ? (
                    <p
                      className="text-[10px] text-aegisaxiom-text-muted mt-0.5"
                      data-testid="time-entry-range-duration"
                    >
                      {(() => {
                        const d = timeRangeDurationHours(addRow.start_time, addRow.end_time);
                        if (d <= 0) return 'End must be after start';
                        return `Duration: ${fmtTime(d)} (snaps to ${fmtTime(snapToTenMinutes(d))})`;
                      })()}
                    </p>
                  ) : null}
                </div>
                {/* Manual duration free-text. The dot is a TIME
                    SEPARATOR, not a decimal point — "2.30" means
                    2h30m, just like "2:30". Parser source of truth:
                    services.time_entry_input.parse_manual_duration;
                    the inline preview is a JS mirror for instant
                    UX feedback. */}
                <div className="w-56">
                  <label className="text-[10px] text-aegisaxiom-text-muted block mb-0.5">
                    Manual duration
                  </label>
                  <input
                    type="text"
                    value={addRow.manual_duration}
                    onChange={(e) => setAddRow((r) => ({
                      ...r, manual_duration: e.target.value,
                      // Typing in manual_duration clears the chip
                      // + start/end pair so the server reads the
                      // text input authoritatively.
                      hours: 0,
                      start_time: '', end_time: '',
                    }))}
                    placeholder="6h 15m, 2:03, 2.30, 375m"
                    data-testid="time-entry-manual-duration"
                    className="w-full px-1.5 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500 placeholder:text-aegisaxiom-text-muted"
                  />
                  {addRow.manual_duration ? (
                    <p
                      className="text-[10px] text-aegisaxiom-text-muted mt-0.5"
                      data-testid="time-entry-manual-duration-preview"
                    >
                      {(() => {
                        const r = parseManualDurationPreview(addRow.manual_duration);
                        if (r.error) return r.error;
                        return r.label;
                      })()}
                    </p>
                  ) : (
                    <p
                      className="text-[10px] text-aegisaxiom-text-muted mt-0.5"
                      data-testid="time-entry-manual-duration-helper"
                    >
                      Use hours/minutes, H:MM, H.MM, or total minutes.
                    </p>
                  )}
                </div>
                {/* Notes */}
                <div className="flex-1 min-w-[200px]">
                  <label className="text-[10px] text-aegisaxiom-text-muted block mb-0.5">
                    Notes (required)
                  </label>
                  <input
                    value={addRow.notes}
                    onChange={(e) => setAddRow((r) => ({ ...r, notes: e.target.value }))}
                    placeholder="What did you work on?"
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleQuickAdd();
                    }}
                    className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500 placeholder:text-aegisaxiom-text-muted"
                  />
                </div>
                {/* Billable Toggle */}
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => setAddRow((r) => ({ ...r, is_billable: !r.is_billable }))}
                    className={`px-2 py-1.5 text-[10px] rounded border transition ${addRow.is_billable ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border-emerald-500/30' : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border-aegisaxiom-border hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'}`}
                  >
                    {addRow.is_billable ? '$ Billable' : 'Non-Bill'}
                  </button>
                </div>
                {/* Add Button */}
                <button
                  onClick={handleQuickAdd}
                  className="bg-violet-600 hover:bg-violet-500 text-white text-xs px-3 py-1.5 rounded-md transition flex items-center gap-1"
                >
                  <Plus size={14} /> Add
                </button>
              </div>
            </div>
          )}

          {/* Weekly Grid */}
          <div className="bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg overflow-hidden">
            {/* Horizontal scroll wrapper — keeps the 7-day grid readable on narrow viewports */}
            <div className="overflow-x-auto">
              <div className="min-w-[640px]">
                {/* Header row with days */}
                <div className="grid grid-cols-[1fr,repeat(7,60px),80px] gap-0 border-b border-aegisaxiom-border">
                  <div className="px-3 py-2 text-xs font-medium text-aegisaxiom-text-secondary">
                    Project
                  </div>
                  {weekDates.map((d, i) => {
                    // Use the local-calendar helpers — never
                    // `new Date('YYYY-MM-DD')` (UTC parse) or
                    // `new Date(d + 'T00:00:00')` (DST/midnight
                    // wobble). `d` is already a local ISO string
                    // produced by getWeekDates above.
                    const isToday = d === toLocalISO(new Date());
                    return (
                      <div
                        key={d}
                        className={`px-1 py-2 text-center text-[10px] font-medium ${isToday ? 'text-violet-600 dark:text-violet-400 bg-violet-50 dark:bg-violet-600/10' : 'text-aegisaxiom-text-secondary'}`}
                      >
                        <div>{DAYS[i]}</div>
                        <div className="text-aegisaxiom-text-muted">
                          {fromLocalISO(d).getDate()}
                        </div>
                      </div>
                    );
                  })}
                  <div className="px-2 py-2 text-center text-xs font-medium text-aegisaxiom-text-secondary">
                    Total
                  </div>
                </div>

                {/* Entry rows grouped by project */}
                {entriesByProject.length > 0 ? (
                  entriesByProject.map((group) => {
                    const dayTotals = {};
                    weekDates.forEach((d) => {
                      dayTotals[d] = 0;
                    });
                    group.entries.forEach((e) => {
                      if (dayTotals[e.date] !== undefined) dayTotals[e.date] += e.hours || 0;
                    });

                    return (
                      <div key={group.project_id} className="border-b border-aegisaxiom-border">
                        <div className="grid grid-cols-[1fr,repeat(7,60px),80px] gap-0 items-center">
                          <div className="px-3 py-2">
                            <div className="text-xs font-medium text-aegisaxiom-text truncate">
                              {group.project_name}
                            </div>
                            {(() => {
                              const contract = contracts.find(
                                (c) => c.contract_id === group.entries[0]?.contract_id,
                              );
                              if (contract) {
                                return (
                                  <div className="text-[10px] text-aegisaxiom-text-muted mt-0.5 flex items-center gap-1.5">
                                    <span>Contract: {contract.name || contract.contract_id}</span>
                                    <span className="px-1 py-0.5 bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 rounded">
                                      {contract.contract_type?.toUpperCase() || 'N/A'}
                                    </span>
                                  </div>
                                );
                              }
                            })()}
                          </div>
                          {weekDates.map((d) => (
                            <div
                              key={d}
                              className="px-1 py-2 text-center text-xs text-aegisaxiom-text-secondary font-mono"
                            >
                              {dayTotals[d] > 0 ? (
                                Math.round(dayTotals[d] * 60)
                              ) : (
                                <span className="text-aegisaxiom-text-muted">—</span>
                              )}
                            </div>
                          ))}
                          <div className="px-2 py-2 text-center text-xs font-semibold text-aegisaxiom-text font-mono">
                            {Math.round(group.totalHours * 60)}
                          </div>
                        </div>
                        {/* Expandable entries */}
                        <div className="px-3 pb-2 space-y-1">
                          {group.entries.map((e) => {
                            const contract = contracts.find((c) => c.contract_id === e.contract_id);
                            return (
                              <div
                                key={e.entry_id}
                                className="flex items-center gap-2 text-[10px] py-0.5 px-2 bg-aegisaxiom-accent-muted rounded group"
                              >
                                <span className={`px-1 py-0.5 rounded ${STATUS_COLORS[e.status]}`}>
                                  {e.status}
                                </span>
                                <span className="text-aegisaxiom-text-muted">{formatDate(e.date)}</span>
                                <span className="font-mono text-aegisaxiom-text-secondary">
                                  {Math.round(e.hours * 60)}m
                                </span>
                                {/* Billable indicator - more prominent */}
                                {e.is_billable ? (
                                  <span
                                    className="px-1.5 py-0.5 bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 rounded font-medium"
                                    title="Billable"
                                  >
                                    $
                                  </span>
                                ) : (
                                  <span
                                    className="px-1.5 py-0.5 bg-aegisaxiom-elevated/40 text-aegisaxiom-text-muted rounded"
                                    title="Non-billable"
                                  >
                                    —
                                  </span>
                                )}
                                {contract && (
                                  <span
                                    className="text-aegisaxiom-text-muted text-[9px]"
                                    title={`Contract: ${contract.name || contract.contract_id}`}
                                  >
                                    [{contract.contract_type?.substring(0, 3).toUpperCase()}]
                                  </span>
                                )}
                                <span className="text-aegisaxiom-text-secondary flex-1 truncate">
                                  {e.notes}
                                </span>
                                {e.approval_notes && (
                                  <span
                                    className="text-amber-600 dark:text-amber-400 truncate max-w-[150px]"
                                    title={e.approval_notes}
                                  >
                                    PM: {e.approval_notes}
                                  </span>
                                )}
                                {(e.status === 'draft' || e.status === 'rejected') && !isLocked && (
                                  <button
                                    onClick={() => deleteEntry(e.entry_id)}
                                    className="opacity-0 group-hover:opacity-100 text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 transition"
                                    aria-label="Delete entry"
                                  >
                                    <Trash2 size={12} />
                                  </button>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <div className="py-12 text-center text-aegisaxiom-text-muted text-sm">
                    <Clock size={28} className="mx-auto mb-2 opacity-40" />
                    No time entries for this week. Use Quick Add above to log time.
                  </div>
                )}

                {/* Total footer */}
                {entries.length > 0 && (
                  <div className="grid grid-cols-[1fr,repeat(7,60px),80px] gap-0 bg-aegisaxiom-accent-muted border-t border-aegisaxiom-border">
                    <div className="px-3 py-2 text-xs font-semibold text-aegisaxiom-text-secondary">
                      Total
                    </div>
                    {weekDates.map((d) => {
                      const dayTotal = entries.reduce(
                        (s, e) => (e.date === d ? s + (e.hours || 0) : s),
                        0,
                      );
                      return (
                        <div
                          key={d}
                          className="px-1 py-2 text-center text-xs font-semibold text-aegisaxiom-text font-mono"
                        >
                          {dayTotal > 0 ? dayTotal.toFixed(2) : '—'}
                        </div>
                      );
                    })}
                    <div className="px-2 py-2 text-center text-xs font-bold text-violet-600 dark:text-violet-400 font-mono">
                      {(weekData?.total_hours || 0).toFixed(2)}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Submit Button */}
          {draftCount > 0 && !isLocked && (
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-500/30 rounded-lg p-3 mt-4">
              <div>
                <p className="text-xs text-blue-700 dark:text-blue-300 font-medium">
                  {draftCount} draft entries ready to submit
                </p>
                <p className="text-[10px] text-aegisaxiom-text-muted">Deadline: Monday 5:00 PM ET</p>
              </div>
              <button
                onClick={submitWeek}
                className="bg-blue-600 hover:bg-blue-500 text-white text-xs px-4 py-1.5 rounded-md flex items-center justify-center gap-1.5 transition w-full sm:w-auto"
                data-testid="submit-week-btn"
              >
                <Send size={14} /> Submit Week
              </button>
            </div>
          )}

          {isLocked && !canCreateForOthers && (
            <div className="bg-red-50 dark:bg-red-900/15 border border-red-500/20 rounded-lg p-3 text-center mt-4">
              <p className="text-xs text-red-600 dark:text-red-400 flex items-center justify-center gap-1.5">
                <Lock size={14} /> This week is locked. Time entries can no longer be modified.
              </p>
            </div>
          )}
        </div>
      )}

      {/* ============ APPROVALS VIEW ============ */}
      {view === 'approvals' && (
        <div>
          {/* Filter Option */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
            <div className="flex items-center gap-4 flex-wrap">
              <label className="flex items-center gap-2 cursor-pointer text-xs text-aegisaxiom-text-secondary">
                <input
                  type="checkbox"
                  checked={showAllUnapproved}
                  onChange={(e) => setShowAllUnapproved(e.target.checked)}
                  className="w-4 h-4 rounded border-aegisaxiom-border-hover bg-aegisaxiom-elevated text-amber-500 focus:ring-amber-500"
                />
                Show All Unapproved (Sorted by Date)
              </label>
              {showAllUnapproved && (
                <span className="text-xs text-aegisaxiom-text-muted">
                  Showing {allUnapprovedEntries.length} entries across all weeks
                </span>
              )}
            </div>
            <div className="text-xs text-aegisaxiom-text-muted">
              {!showAllUnapproved ? `Week of ${formatDate(weekStart)}` : 'All Time'}
            </div>
          </div>

          <div className="space-y-3">
            {showAllUnapproved ? (
              // Show all unapproved entries sorted by date
              allUnapprovedEntries.length > 0 ? (
                <div className="bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg overflow-hidden">
                  <div className="px-4 py-2.5 border-b border-aegisaxiom-border bg-aegisaxiom-surface flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="text-sm font-medium text-aegisaxiom-text">
                      All Unapproved Timesheets
                    </span>
                    <span className="text-xs text-aegisaxiom-text-muted">
                      {fmtTime(allUnapprovedEntries.reduce((sum, e) => sum + e.hours, 0))} total ·{' '}
                      {fmtTime(
                        allUnapprovedEntries
                          .filter((e) => e.is_billable)
                          .reduce((sum, e) => sum + e.hours, 0),
                      )}{' '}
                      billable
                    </span>
                  </div>
                  {/* Horizontal scroll on mobile to keep the dense column layout readable */}
                  <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
                    <div className="min-w-[720px] divide-y divide-aegisaxiom-border/30">
                      {allUnapprovedEntries.map((e) => {
                        const contract = contracts.find((c) => c.contract_id === e.contract_id);
                        const project = projects.find((p) => p.project_id === e.project_id);
                        return (
                          <div
                            key={e.entry_id}
                            className="flex items-center gap-3 px-4 py-2 text-xs hover:bg-aegisaxiom-accent-muted"
                          >
                            <span className="text-aegisaxiom-text-muted w-24 shrink-0">
                              {formatDate(e.date)}
                            </span>
                            <span className="text-aegisaxiom-text-secondary w-32 shrink-0">
                              {e.user_name}
                            </span>
                            <span className="font-mono text-aegisaxiom-text w-16 shrink-0">
                              {fmtTime(e.hours)}
                            </span>
                            {e.is_billable ? (
                              <span
                                className="px-1.5 py-0.5 bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 rounded font-medium w-8 text-center shrink-0"
                                title="Billable"
                              >
                                $
                              </span>
                            ) : (
                              <span
                                className="px-1.5 py-0.5 bg-aegisaxiom-elevated/40 text-aegisaxiom-text-muted rounded w-8 text-center shrink-0"
                                title="Non-billable"
                              >
                                —
                              </span>
                            )}
                            {contract && (
                              <span
                                className="px-1.5 py-0.5 bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 rounded text-[10px] w-14 text-center shrink-0"
                                title={contract.name || contract.contract_id}
                              >
                                {contract.contract_type?.substring(0, 3).toUpperCase()}
                              </span>
                            )}
                            <span
                              className="flex-1 text-aegisaxiom-text-secondary truncate"
                              title={e.notes}
                            >
                              {e.notes}
                            </span>
                            <div className="flex gap-1 shrink-0">
                              <button
                                onClick={() => approveEntry(e.entry_id)}
                                className="p-1 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-50 dark:hover:bg-emerald-600/20 rounded transition"
                                title="Approve"
                                aria-label="Approve entry"
                              >
                                <Check size={14} />
                              </button>
                              <button
                                onClick={() => rejectEntry(e.entry_id)}
                                className="p-1 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-600/20 rounded transition"
                                title="Reject"
                                aria-label="Reject entry"
                              >
                                <X size={14} />
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="text-center py-12 text-aegisaxiom-text-muted">
                  No unapproved timesheets
                </div>
              )
            ) : // Show week-by-week grouped approvals
            approvalQueue.length > 0 ? (
              approvalQueue.map((group, gi) => (
                <div
                  key={gi}
                  className="bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg overflow-hidden"
                >
                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 px-4 py-2.5 border-b border-aegisaxiom-border">
                    <div>
                      <span className="text-sm font-medium text-aegisaxiom-text">{group.user_name}</span>
                      <span className="text-xs text-aegisaxiom-text-muted ml-2">
                        Week of {formatDate(group.week_start)}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 flex-wrap">
                      <span className="text-xs text-aegisaxiom-text-secondary">
                        {fmtTime(group.total_hours)} total · {fmtTime(group.billable_hours)}{' '}
                        billable
                      </span>
                      <button
                        onClick={() => bulkApprove(group.entries.map((e) => e.entry_id))}
                        className="bg-emerald-600 hover:bg-emerald-500 text-white text-xs px-3 py-1 rounded flex items-center gap-1 transition shrink-0"
                      >
                        <CheckCircle size={12} /> Approve All
                      </button>
                    </div>
                  </div>
                  <div className="overflow-x-auto">
                    <div className="min-w-[640px] divide-y divide-aegisaxiom-border/30">
                      {group.entries.map((e) => {
                        const contract = contracts.find((c) => c.contract_id === e.contract_id);
                        const project = projects.find((p) => p.project_id === e.project_id);
                        return (
                          <div
                            key={e.entry_id}
                            className="flex items-center gap-3 px-4 py-2 text-xs hover:bg-aegisaxiom-accent-muted"
                          >
                            <span className="text-aegisaxiom-text-muted w-20 shrink-0">
                              {formatDate(e.date)}
                            </span>
                            <span className="font-mono text-aegisaxiom-text w-16 shrink-0">
                              {Math.round(e.hours * 60)}m
                            </span>
                            {/* Billable indicator - more prominent */}
                            {e.is_billable ? (
                              <span
                                className="px-1.5 py-0.5 bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 rounded font-medium w-8 text-center shrink-0"
                                title="Billable"
                              >
                                $
                              </span>
                            ) : (
                              <span
                                className="px-1.5 py-0.5 bg-aegisaxiom-elevated/40 text-aegisaxiom-text-muted rounded w-8 text-center shrink-0"
                                title="Non-billable"
                              >
                                —
                              </span>
                            )}
                            {/* Contract info */}
                            {contract && (
                              <span
                                className="px-1.5 py-0.5 bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 rounded text-[10px] w-14 text-center shrink-0"
                                title={contract.name || contract.contract_id}
                              >
                                {contract.contract_type?.substring(0, 3).toUpperCase()}
                              </span>
                            )}
                            <span
                              className="flex-1 text-aegisaxiom-text-secondary truncate"
                              title={project?.name || 'N/A'}
                            >
                              {e.notes}
                            </span>
                            <span
                              className={`px-1.5 py-0.5 rounded text-[10px] shrink-0 ${e.note_quality === 'good' ? 'text-emerald-600 dark:text-emerald-400' : e.note_quality === 'fair' ? 'text-amber-600 dark:text-amber-400' : 'text-red-600 dark:text-red-400'}`}
                            >
                              {e.note_quality || 'ok'}
                            </span>
                            <div className="flex gap-1 shrink-0">
                              <button
                                onClick={() => approveEntry(e.entry_id)}
                                className="p-1 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-50 dark:hover:bg-emerald-600/20 rounded transition"
                                title="Approve"
                                aria-label="Approve entry"
                              >
                                <Check size={14} />
                              </button>
                              <button
                                onClick={() => rejectEntry(e.entry_id)}
                                className="p-1 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-600/20 rounded transition"
                                title="Reject"
                                aria-label="Reject entry"
                              >
                                <X size={14} />
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <div className="text-center py-12 text-aegisaxiom-text-muted text-sm">
                <CheckCircle size={28} className="mx-auto mb-2 opacity-40" />
                No entries pending approval for this week.
              </div>
            )}
          </div>
        </div>
      )}

      {/* ============ AP/AR SUMMARY VIEW ============ */}
      {view === 'summary' && summaryData && (
        <div className="space-y-4">
          {/* AP: Accounts Payable */}
          <div className="bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-aegisaxiom-border">
              <h3 className="text-sm font-semibold text-aegisaxiom-text">
                Accounts Payable — Contractor Bills
              </h3>
              <p className="text-xs text-aegisaxiom-text-muted">
                {summaryData.accounts_payable?.description}
              </p>
            </div>
            {(summaryData.accounts_payable?.items || []).length > 0 ? (
              <div className="divide-y divide-aegisaxiom-border/30">
                {summaryData.accounts_payable.items.map((item, i) => (
                  <div
                    key={i}
                    className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1 sm:gap-2 px-4 py-2.5 text-xs"
                  >
                    <span className="text-aegisaxiom-text font-medium">{item.user_name}</span>
                    <div className="flex flex-wrap gap-x-4 gap-y-1">
                      <span className="text-aegisaxiom-text-secondary">{item.entry_count} entries</span>
                      <span className="text-aegisaxiom-text font-mono">{fmtTime(item.total_hours)}</span>
                      <span className="text-aegisaxiom-text-muted">
                        {fmtTime(item.billable_hours)} billable
                      </span>
                    </div>
                  </div>
                ))}
                <div className="px-4 py-2.5 bg-aegisaxiom-accent-muted flex justify-between text-xs font-semibold">
                  <span className="text-aegisaxiom-text-secondary">Total</span>
                  <span className="text-aegisaxiom-text">
                    {fmtTime(summaryData.accounts_payable?.total_hours || 0)}
                  </span>
                </div>
              </div>
            ) : (
              <div className="p-4 text-center text-xs text-aegisaxiom-text-muted">
                No approved time entries this week
              </div>
            )}
          </div>

          {/* AR: Accounts Receivable */}
          <div className="bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-aegisaxiom-border">
              <h3 className="text-sm font-semibold text-aegisaxiom-text">
                Accounts Receivable — BOA Invoices
              </h3>
              <p className="text-xs text-aegisaxiom-text-muted">
                {summaryData.accounts_receivable?.description}
              </p>
            </div>
            {(summaryData.accounts_receivable?.items || []).length > 0 ? (
              <div className="divide-y divide-aegisaxiom-border/30">
                {summaryData.accounts_receivable.items.map((item, i) => (
                  <div
                    key={i}
                    className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1 sm:gap-2 px-4 py-2.5 text-xs"
                  >
                    <div>
                      <span className="text-aegisaxiom-text font-medium">{item.contract_name}</span>
                      <span className="text-aegisaxiom-text-muted ml-2">{item.account_name}</span>
                    </div>
                    <div className="flex flex-wrap gap-x-4 gap-y-1">
                      <span className="text-aegisaxiom-text-secondary">
                        {item.billing_period} · {item.invoice_terms?.replace('_', ' ')}
                      </span>
                      <span className="text-aegisaxiom-text font-mono">{fmtTime(item.total_hours)}</span>
                      <span className="text-emerald-600 dark:text-emerald-400 font-semibold">
                        {fmt$(item.total_amount)}
                      </span>
                    </div>
                  </div>
                ))}
                <div className="px-4 py-2.5 bg-aegisaxiom-accent-muted flex justify-between text-xs font-semibold">
                  <span className="text-aegisaxiom-text-secondary">Total Invoiceable</span>
                  <span className="text-emerald-600 dark:text-emerald-400">
                    {fmt$(summaryData.accounts_receivable?.total_amount || 0)}
                  </span>
                </div>
              </div>
            ) : (
              <div className="p-4 text-center text-xs text-aegisaxiom-text-muted">
                No BOA invoices due this week
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
