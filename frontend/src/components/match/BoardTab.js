import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  RefreshCw,
  Sparkles,
  Download,
  Search,
  Filter,
  ArrowUpDown,
  ArrowDownUp,
  CheckCheck,
  ChevronRight,
  Loader2,
  X,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { api, unwrap } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';
import StatCard from '@/components/match/board/StatCard';
import { StatusPill } from '@/components/match/board/StatusPill';
import { ConfidenceBar } from '@/components/match/board/ConfidenceBar';
import { FlagChip } from '@/components/match/board/FlagChip';
import RowDetailDrawer from '@/components/match/board/RowDetailDrawer';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const BASE = `${API_URL}/api/match`;

const STAT_CARDS = [
  { key: 'ready_to_draft', label: 'READY TO DRAFT', accent: 'warn' },
  { key: 'needs_review',   label: 'NEEDS REVIEW',   accent: 'warn' },
  { key: 'approved',       label: 'APPROVED',        accent: 'success' },
  { key: 'rejected',       label: 'REJECTED',        accent: 'danger' },
  { key: 'escalated',      label: 'ESCALATED',       accent: 'info' },
];

// Flag filter definitions — labels map raw flag keys to human labels.
// Backend canonical flag keys come from match-main/backend/utils.py — do NOT
// invent friendlier-looking names; the chip won't filter if it doesn't match.
const FLAG_FILTER_DEFS = [
  { key: 'name_variation',       label: 'Name variation' },
  { key: 'address_discrepancy',  label: 'Address mismatch' },
  { key: 'new_customer',         label: 'New customer' },
  { key: 'new_vs_existing',      label: 'New vs existing' },
];

// Mode labels for subtitle
const MODE_LABEL = {
  snapshot: 'Snapshot',
  live_sql: 'Live SQL',
  live_api: 'Live API',
  upload: 'Upload',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Extract the flat display fields from the nested row shape.
 * The row data shape has output.top_level / output.order_details etc.
 * The existing BoardTab also stored some flat fields directly on the row
 * (customer_name, advisor_company) — we normalise both shapes here.
 */
function flattenRow(row) {
  const audit = row.audit_metadata ?? {};
  const out = row.output ?? {};
  const top = out.top_level ?? {};
  const cust = top.customer ?? {};
  const od = out.order_details ?? {};
  const loc = top.location ?? {};

  const customerName =
    cust.name ||
    row.customer_name ||
    row.source_row?.['Account Name'] ||
    row.source_row?.['Customer Name'] ||
    '';

  const advisorCompany =
    top.advisor_company ||
    row.advisor_company ||
    row.source_row?.['Agency'] ||
    row.source_row?.['Advisor'] ||
    '';

  const cityState = (loc.city || loc.state)
    ? `${loc.city || ''}, ${loc.state || ''}`.trim().replace(/^,\s*/, '')
    : (row.city_state || '');

  const mrc = od.mrgmv ?? row.mrc ?? null;
  const contractType = od.contract_type ?? row.contract_type ?? '';
  const provider = od.provider ?? row.provider ?? '';
  // Canonical field is audit_metadata.confidence_score (0..1). The old
  // BoardTab carried an `audit.score` fallback that never resolved in
  // real data — drop it now that Wave 6 normalized the upstream contract.
  const confidenceScore = audit.confidence_score ?? row.confidence_score ?? 0;
  const flags = audit.flags ?? row.flags ?? [];

  return {
    customerName,
    advisorCompany,
    cityState,
    mrc,
    contractType,
    provider,
    confidenceScore,
    flags,
  };
}

function fmtMoney(v) {
  if (v == null) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(v);
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SortableTh({ label, sortKey: k, activeKey, dir, onSort, className = '' }) {
  const active = activeKey === k;
  return (
    <th
      className={[
        'text-left px-3 py-2 text-xs font-medium text-aegisaxiom-text-secondary whitespace-nowrap',
        className,
      ].join(' ')}
    >
      <button
        onClick={() => onSort(k)}
        className={[
          'inline-flex items-center gap-1 hover:text-aegisaxiom-text transition-colors',
          active ? 'text-aegisaxiom-text' : '',
        ].join(' ')}
        data-testid={`board-sort-${k}`}
      >
        {label}
        {active ? (
          <ArrowDownUp className="h-3 w-3 opacity-70" />
        ) : (
          <ArrowUpDown className="h-3 w-3 opacity-40" />
        )}
      </button>
    </th>
  );
}

function SkeletonRows() {
  return [0, 1, 2, 3, 4].map((i) => (
    <tr key={i} className="border-b border-aegisaxiom-border">
      {[180, 120, 100, 60, 60, 110, 80, 80].map((w, j) => (
        <td key={j} className="px-3 py-3">
          <div
            className="h-3 rounded bg-aegisaxiom-surface-elevated animate-pulse"
            style={{ width: w }}
          />
        </td>
      ))}
      <td className="px-3 py-3 w-8" />
    </tr>
  ));
}

function EmptyState({ hasRows, onSimulate }) {
  return (
    <tr>
      <td colSpan={9}>
        <div className="py-12 text-center space-y-2">
          <p className="text-sm font-medium text-aegisaxiom-text">
            {hasRows ? 'No rows match your filters' : 'No rows yet'}
          </p>
          <p className="text-xs text-aegisaxiom-text-secondary">
            {hasRows
              ? 'Adjust the search or clear the filter chips.'
              : "Rows will appear here once n8n processes the session. Use 'Re-run simulate' to retrigger."}
          </p>
          {!hasRows && onSimulate && (
            <Button
              variant="outline"
              size="sm"
              onClick={onSimulate}
              className="mt-2"
              data-testid="empty-board-simulate-btn"
            >
              <Sparkles className="h-3.5 w-3.5 mr-1" />
              Re-run simulate
            </Button>
          )}
        </div>
      </td>
    </tr>
  );
}

function BoardRow({ row, selected, onClick }) {
  const flat = flattenRow(row);

  return (
    <tr
      className={[
        'border-b border-aegisaxiom-border cursor-pointer transition-colors group',
        selected
          ? 'bg-aegisaxiom-surface-elevated'
          : 'hover:bg-aegisaxiom-surface-elevated/60',
      ].join(' ')}
      onClick={onClick}
      data-testid={`board-row-${row.row_id}`}
    >
      {/* Customer */}
      <td className="px-3 py-2.5 max-w-[220px]">
        <div className="font-medium text-aegisaxiom-text truncate">{flat.customerName || '—'}</div>
      </td>

      {/* Advisor */}
      <td className="px-3 py-2.5 text-aegisaxiom-text-secondary text-sm truncate max-w-[160px]">
        {flat.advisorCompany || '—'}
      </td>

      {/* Location */}
      <td className="px-3 py-2.5 text-aegisaxiom-text-secondary text-sm truncate max-w-[120px]">
        {flat.cityState || '—'}
      </td>

      {/* MRC */}
      <td className="px-3 py-2.5 text-right text-sm font-medium text-aegisaxiom-text tabular-nums">
        {fmtMoney(flat.mrc)}
      </td>

      {/* Type */}
      <td className="px-3 py-2.5 text-aegisaxiom-text-secondary text-sm">
        {flat.contractType || '—'}
      </td>

      {/* Confidence */}
      <td className="px-3 py-2.5">
        <ConfidenceBar score={flat.confidenceScore} />
      </td>

      {/* Status */}
      <td className="px-3 py-2.5">
        <StatusPill status={row.status} dense />
      </td>

      {/* Flags */}
      <td className="px-3 py-2.5">
        <div className="flex flex-wrap gap-1">
          {flat.flags.slice(0, 3).map((f) => (
            <FlagChip key={f} flag={f} />
          ))}
          {flat.flags.length > 3 && (
            <span className="text-[10px] text-aegisaxiom-text-secondary">
              +{flat.flags.length - 3}
            </span>
          )}
        </div>
      </td>

      {/* Chevron */}
      <td className="px-3 py-2.5 w-8">
        <ChevronRight className="h-4 w-4 text-aegisaxiom-text-secondary opacity-0 group-hover:opacity-100 transition-opacity" />
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function BoardTab() {
  const { hasPermission } = usePermissions();

  // Session data
  const [sessions, setSessions]         = useState([]);
  const [selectedSessionId, setSelectedSessionId] = useState('');
  const [session, setSession]           = useState(null);
  const [rows, setRows]                 = useState([]);

  // UI state
  const [search, setSearch]             = useState('');
  const [flagFilters, setFlagFilters]   = useState([]);
  const [statusFilters, setStatusFilters] = useState([]);
  const [sortKey, setSortKey]           = useState('row_index');
  const [sortDir, setSortDir]           = useState('asc');

  // Loading flags
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [loadingRows, setLoadingRows]   = useState(false);
  const [bulkBusy, setBulkBusy]         = useState(false);
  const [simBusy, setSimBusy]           = useState(false);
  const [rowsError, setRowsError]       = useState(null);

  // Drawer state
  const [selectedRowId, setSelectedRowId] = useState(null);

  // Refresh trigger
  const [refreshKey, setRefreshKey]     = useState(0);

  // Search debounce
  const debounceRef = useRef(null);
  const [debouncedSearch, setDebouncedSearch] = useState('');

  // Permissions
  // Server-side gate is `match.row.change_status` (match_row.py); the
  // `bulk_approve` slug doesn't exist in the catalog. Keep it as a
  // forward-compat fallback in case the slug gets split out later.
  const canBulkApprove = hasPermission('match.row.change_status') || hasPermission('match.row.bulk_approve');

  // ---------------------------------------------------------------------------
  // Search debounce
  // ---------------------------------------------------------------------------

  const handleSearchChange = useCallback((e) => {
    const val = e.target.value;
    setSearch(val);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedSearch(val), 250);
  }, []);

  useEffect(() => () => { if (debounceRef.current) clearTimeout(debounceRef.current); }, []);

  // ---------------------------------------------------------------------------
  // Fetch session list
  // ---------------------------------------------------------------------------

  useEffect(() => {
    let alive = true;
    setLoadingSessions(true);
    api
      .get(`${BASE}/sessions`)
      .then(async (res) => {
        if (!alive) return;
        const data = await res.json();
        if (!res.ok) return;
        const items = unwrap(data) ?? [];
        items.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
        setSessions(items);
        if (items.length > 0 && !selectedSessionId) {
          setSelectedSessionId(items[0].session_id);
        }
      })
      .catch(() => {})
      .finally(() => { if (alive) setLoadingSessions(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  // ---------------------------------------------------------------------------
  // Fetch session detail + rows when session changes
  // ---------------------------------------------------------------------------

  useEffect(() => {
    if (!selectedSessionId) {
      setSession(null);
      setRows([]);
      setRowsError(null);
      return;
    }
    let alive = true;
    setLoadingRows(true);
    setRowsError(null);

    Promise.all([
      api.get(`${BASE}/sessions/${selectedSessionId}`).then((r) => r.json()),
      api.get(`${BASE}/rows/by-session/${selectedSessionId}`).then((r) => r.json()),
    ])
      .then(([sessData, rowsData]) => {
        if (!alive) return;
        setSession(sessData?.data ?? sessData ?? null);
        setRows(unwrap(rowsData) ?? []);
      })
      .catch((err) => {
        if (!alive) return;
        setRowsError(err?.message || 'Failed to load rows.');
        setRows([]);
      })
      .finally(() => { if (alive) setLoadingRows(false); });

    return () => { alive = false; };
  }, [selectedSessionId, refreshKey]);

  // ---------------------------------------------------------------------------
  // Auto-refresh while n8n is still processing
  //
  // n8n calls back to /api/match/webhooks/row-processed once per row, so we
  // poll the rows endpoint every 2s while the session is in 'processing'
  // status. Stops on any of:
  //   - session.status flips off 'processing'
  //   - we've polled 60 times (~2 min — n8n's worst-case is well under this)
  //   - the row count is stable across 5 consecutive polls (n8n is done OR
  //     stuck; operator can manually Refresh if it's the latter)
  //   - the user navigates away (cleanup unsubscribes)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!selectedSessionId) return;
    if (session?.status !== 'processing') return;

    let alive = true;
    let ticks = 0;
    let stableCount = 0;
    let lastRowCount = rows.length;

    const intervalId = setInterval(() => {
      if (!alive) return;
      ticks += 1;
      if (ticks > 60) {
        clearInterval(intervalId);
        return;
      }
      // Bump refreshKey to trigger the fetch effect above.
      setRefreshKey((k) => k + 1);
      // Stability check happens after the fetch lands — we read the latest
      // rows count via the closure of the next tick.
      if (rows.length === lastRowCount) {
        stableCount += 1;
        if (stableCount >= 5) {
          clearInterval(intervalId);
          return;
        }
      } else {
        stableCount = 0;
        lastRowCount = rows.length;
      }
    }, 2000);

    return () => {
      alive = false;
      clearInterval(intervalId);
    };
  }, [selectedSessionId, session?.status, rows.length]);

  // ---------------------------------------------------------------------------
  // Computed: status counters
  // ---------------------------------------------------------------------------

  const counters = useMemo(() => {
    const c = { ready_to_draft: 0, needs_review: 0, approved: 0, rejected: 0, escalated: 0 };
    for (const r of rows) {
      if (c[r.status] != null) c[r.status]++;
      else if (r.recommended_status && c[r.recommended_status] != null) c[r.recommended_status]++;
    }
    return c;
  }, [rows]);

  // ---------------------------------------------------------------------------
  // Computed: eligible for bulk approve (score >= 0.95 and actionable status)
  // ---------------------------------------------------------------------------

  const eligibleRows = useMemo(() =>
    rows.filter((r) => {
      const flat = flattenRow(r);
      return flat.confidenceScore >= 0.95 &&
        ['ready_to_draft', 'needs_review', 'ready'].includes(r.status);
    }),
    [rows]
  );

  // ---------------------------------------------------------------------------
  // Computed: flags present in current row set
  // ---------------------------------------------------------------------------

  const presentFlags = useMemo(() => {
    const set = new Set();
    for (const r of rows) {
      const f = flattenRow(r).flags;
      f.forEach((flag) => set.add(flag));
    }
    return FLAG_FILTER_DEFS.filter((d) => set.has(d.key));
  }, [rows]);

  // ---------------------------------------------------------------------------
  // Computed: filtered + sorted rows
  // ---------------------------------------------------------------------------

  const filteredRows = useMemo(() => {
    let out = rows.slice();

    if (debouncedSearch.trim()) {
      const q = debouncedSearch.toLowerCase();
      out = out.filter((r) => {
        const flat = flattenRow(r);
        return (
          flat.customerName.toLowerCase().includes(q) ||
          flat.advisorCompany.toLowerCase().includes(q) ||
          (r.row_id || '').toLowerCase().includes(q)
        );
      });
    }

    if (statusFilters.length > 0) {
      out = out.filter((r) => statusFilters.includes(r.status));
    }

    if (flagFilters.length > 0) {
      out = out.filter((r) =>
        flattenRow(r).flags.some((f) => flagFilters.includes(f))
      );
    }

    out.sort((a, b) => {
      let av, bv;
      const flatA = flattenRow(a);
      const flatB = flattenRow(b);
      switch (sortKey) {
        case 'confidence':
          av = flatA.confidenceScore;
          bv = flatB.confidenceScore;
          break;
        case 'customer':
          av = flatA.customerName.toLowerCase();
          bv = flatB.customerName.toLowerCase();
          break;
        case 'advisor':
          av = flatA.advisorCompany.toLowerCase();
          bv = flatB.advisorCompany.toLowerCase();
          break;
        case 'mrc':
          av = flatA.mrc ?? 0;
          bv = flatB.mrc ?? 0;
          break;
        case 'status':
          av = a.status ?? '';
          bv = b.status ?? '';
          break;
        default:
          av = a.row_index ?? 0;
          bv = b.row_index ?? 0;
      }
      if (av < bv) return sortDir === 'asc' ? -1 : 1;
      if (av > bv) return sortDir === 'asc' ? 1 : -1;
      return 0;
    });

    return out;
  }, [rows, debouncedSearch, statusFilters, flagFilters, sortKey, sortDir]);

  // ---------------------------------------------------------------------------
  // Event handlers
  // ---------------------------------------------------------------------------

  const handleSort = useCallback((key) => {
    setSortKey(key);
    setSortDir((cur) => (sortKey === key ? (cur === 'asc' ? 'desc' : 'asc') : 'asc'));
  }, [sortKey]);

  const toggleFlagFilter = useCallback((key) => {
    setFlagFilters((cur) =>
      cur.includes(key) ? cur.filter((x) => x !== key) : [...cur, key]
    );
  }, []);

  const toggleStatusFilter = useCallback((key) => {
    setStatusFilters((cur) =>
      cur.includes(key) ? cur.filter((x) => x !== key) : [...cur, key]
    );
  }, []);

  const clearFilters = useCallback(() => {
    setSearch('');
    setDebouncedSearch('');
    setFlagFilters([]);
    setStatusFilters([]);
  }, []);

  const hasActiveFilters = search || flagFilters.length > 0 || statusFilters.length > 0;

  const handleRefresh = useCallback(() => {
    setRefreshKey((k) => k + 1);
  }, []);

  // "Re-run simulate" maps to /process — re-kicks the n8n matching
  // workflow for the existing session. The source POC had a separate
  // /simulate endpoint that loaded fixtures; we didn't ingest it, so
  // this just re-fires the same n8n trigger SessionsTab uses on create.
  const handleSimulate = useCallback(async () => {
    if (!selectedSessionId) return;
    setSimBusy(true);
    try {
      const res = await api.post(`${BASE}/sessions/${selectedSessionId}/process`, {});
      const data = await res.json();
      if (!res.ok) {
        toast.error(data?.detail || 'Re-run failed');
      } else {
        toast.success('Processing queued — rows arriving shortly');
        setRefreshKey((k) => k + 1);
      }
    } catch {
      toast.error('Re-run failed');
    } finally {
      setSimBusy(false);
    }
  }, [selectedSessionId]);

  const handleBulkApprove = useCallback(async () => {
    if (!selectedSessionId) return;
    if (eligibleRows.length === 0) {
      toast.info('No rows with confidence ≥ 95% to bulk-approve.');
      return;
    }
    setBulkBusy(true);
    try {
      const res = await api.post(`${BASE}/rows/bulk-approve`, {
        session_id: selectedSessionId,
        row_ids: eligibleRows.map((r) => r.row_id),
        threshold: 0.95,
      });
      const data = await res.json();
      if (!res.ok) {
        toast.error(data?.detail || 'Bulk approve failed');
      } else {
        const count = data?.data?.approved_count ?? data?.approved_count ?? eligibleRows.length;
        toast.success(`Bulk-approved ${count} rows`);
        setRefreshKey((k) => k + 1);
      }
    } catch {
      toast.error('Bulk approve failed');
    } finally {
      setBulkBusy(false);
    }
  }, [selectedSessionId, eligibleRows]);

  const handleRowActioned = useCallback(() => {
    setRefreshKey((k) => k + 1);
  }, []);

  const handleOutput = useCallback(() => {
    if (!selectedSessionId) return;
    const url = `${BASE}/sessions/${selectedSessionId}/export`;
    const a = document.createElement('a');
    a.href = url;
    a.download = `session-${selectedSessionId}-output.csv`;
    a.click();
  }, [selectedSessionId]);

  // ---------------------------------------------------------------------------
  // Session subtitle
  // ---------------------------------------------------------------------------

  const subtitle = useMemo(() => {
    if (!session) return null;
    const provider = session.provider ?? '—';
    const mode = MODE_LABEL[session.mode] ?? session.mode ?? '—';
    const rowCount = rows.length;
    return `${provider} · ${mode} · ${rowCount} rows received`;
  }, [session, rows.length]);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="space-y-4" data-testid="match-board-tab">

      {/* ── Session selector + Header ── */}
      <div className="glass-card p-4 space-y-3">
        {/* Session selector row */}
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex flex-col gap-1 min-w-[240px] max-w-[360px] flex-1">
            <label className="text-xs font-medium text-aegisaxiom-text-secondary">Session</label>
            <select
              className="aegisaxiom-input px-2 py-1.5 text-sm rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface text-aegisaxiom-text"
              value={selectedSessionId}
              onChange={(e) => setSelectedSessionId(e.target.value)}
              disabled={loadingSessions}
              data-testid="board-session-select"
            >
              {loadingSessions && <option value="">Loading sessions…</option>}
              {!loadingSessions && sessions.length === 0 && (
                <option value="">No sessions available</option>
              )}
              {!loadingSessions && sessions.length > 0 && (
                <>
                  <option value="">— Select session —</option>
                  {sessions.map((s) => (
                    <option key={s.session_id} value={s.session_id}>
                      {s.label || s.session_id}
                      {s.provider ? ` (${s.provider})` : ''}
                    </option>
                  ))}
                </>
              )}
            </select>
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-2 ml-auto flex-wrap">
            <Button
              variant="outline"
              size="sm"
              onClick={handleRefresh}
              data-testid="board-refresh-btn"
            >
              <RefreshCw className="h-3.5 w-3.5 mr-1" />
              Refresh
            </Button>

            <Button
              variant="outline"
              size="sm"
              onClick={handleSimulate}
              disabled={simBusy || !selectedSessionId}
              data-testid="board-simulate-btn"
            >
              {simBusy ? (
                <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
              ) : (
                <Sparkles className="h-3.5 w-3.5 mr-1" />
              )}
              Re-run simulate
            </Button>

            {/* TODO(match-export): session-level export endpoint not wired —
                /api/match/rows/{row_id}/export exists per-row, but no
                session-wide CSV. Hidden until the backend lands it; tracked
                as a Wave 7 follow-up in the master postmortem. */}
            {false && (
              <Button
                variant="outline"
                size="sm"
                onClick={handleOutput}
                disabled={!selectedSessionId}
                data-testid="board-output-btn"
              >
                <Download className="h-3.5 w-3.5 mr-1" />
                Output
              </Button>
            )}
          </div>
        </div>

        {/* Session header info */}
        {session && (
          <div>
            <h2 className="text-base font-semibold text-aegisaxiom-text leading-tight">
              {session.label || selectedSessionId}
            </h2>
            {subtitle && (
              <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">{subtitle}</p>
            )}
          </div>
        )}
      </div>

      {/* ── Stat cards ── */}
      <div
        className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2"
        data-testid="board-stat-cards"
      >
        {STAT_CARDS.map((card) => (
          <StatCard
            key={card.key}
            label={card.label}
            value={counters[card.key] ?? 0}
            accent={card.accent}
            active={statusFilters.includes(card.key)}
            onClick={() => toggleStatusFilter(card.key)}
            testId={`board-stat-${card.key}`}
          />
        ))}
      </div>

      {/* ── Filter strip ── */}
      <div className="flex flex-wrap items-center gap-2" data-testid="board-filter-strip">
        {/* Search */}
        <div className="relative flex-1 min-w-[240px] max-w-[380px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-aegisaxiom-text-secondary pointer-events-none" />
          <Input
            value={search}
            onChange={handleSearchChange}
            placeholder="Search by customer, advisor, ID…"
            className="input-field h-9 pl-8"
            data-testid="board-search-input"
          />
        </div>

        {/* Flag chips */}
        {presentFlags.length > 0 && (
          <div className="flex items-center gap-1.5 flex-wrap" data-testid="board-flag-filters">
            <span className="text-xs text-aegisaxiom-text-secondary inline-flex items-center gap-1">
              <Filter className="h-3 w-3" />
              Flags:
            </span>
            {presentFlags.map((def) => {
              const on = flagFilters.includes(def.key);
              return (
                <button
                  key={def.key}
                  type="button"
                  onClick={() => toggleFlagFilter(def.key)}
                  className={[
                    'inline-flex items-center rounded-full border px-2 py-0.5 text-xs transition-colors',
                    on
                      ? 'border-aegisaxiom-border bg-aegisaxiom-status-info-bg text-aegisaxiom-status-info'
                      : 'border-aegisaxiom-border bg-aegisaxiom-surface text-aegisaxiom-text-secondary hover:bg-aegisaxiom-surface-elevated hover:text-aegisaxiom-text',
                  ].join(' ')}
                  data-testid={`board-flag-filter-${def.key}`}
                >
                  {def.label}
                </button>
              );
            })}
          </div>
        )}

        {/* Clear all */}
        {hasActiveFilters && (
          <button
            type="button"
            onClick={clearFilters}
            className="inline-flex items-center gap-1 text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition-colors"
            data-testid="board-clear-filters"
          >
            <X className="h-3 w-3" />
            Clear
          </button>
        )}

        {/* Bulk approve */}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-aegisaxiom-text-secondary">
            {eligibleRows.length} eligible
          </span>
          {canBulkApprove && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleBulkApprove}
              disabled={bulkBusy || eligibleRows.length === 0 || !selectedSessionId}
              data-testid="board-bulk-approve-btn"
            >
              {bulkBusy ? (
                <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
              ) : (
                <CheckCheck className="h-3.5 w-3.5 mr-1" />
              )}
              Bulk approve ≥ 95%
            </Button>
          )}
        </div>
      </div>

      {/* ── Data table ── */}
      <div className="glass-card overflow-hidden" data-testid="board-table-container">
        {!selectedSessionId ? (
          <div className="py-14 text-center text-aegisaxiom-text-secondary text-sm">
            Select a session above to view rows.
          </div>
        ) : rowsError ? (
          <div className="py-10 px-4 text-center text-aegisaxiom-status-danger text-sm">
            {rowsError}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="board-rows-table">
              <thead>
                <tr className="border-b border-aegisaxiom-border bg-aegisaxiom-surface-elevated">
                  <SortableTh
                    label="Customer"
                    sortKey="customer"
                    activeKey={sortKey}
                    dir={sortDir}
                    onSort={handleSort}
                  />
                  <SortableTh
                    label="Advisor"
                    sortKey="advisor"
                    activeKey={sortKey}
                    dir={sortDir}
                    onSort={handleSort}
                  />
                  <th className="text-left px-3 py-2 text-xs font-medium text-aegisaxiom-text-secondary">
                    Location
                  </th>
                  <SortableTh
                    label="MRC"
                    sortKey="mrc"
                    activeKey={sortKey}
                    dir={sortDir}
                    onSort={handleSort}
                    className="text-right"
                  />
                  <th className="text-left px-3 py-2 text-xs font-medium text-aegisaxiom-text-secondary">
                    Type
                  </th>
                  <SortableTh
                    label="Confidence"
                    sortKey="confidence"
                    activeKey={sortKey}
                    dir={sortDir}
                    onSort={handleSort}
                  />
                  <SortableTh
                    label="Status"
                    sortKey="status"
                    activeKey={sortKey}
                    dir={sortDir}
                    onSort={handleSort}
                  />
                  <th className="text-left px-3 py-2 text-xs font-medium text-aegisaxiom-text-secondary">
                    Flags
                  </th>
                  <th className="w-8 px-3" />
                </tr>
              </thead>
              <tbody>
                {loadingRows ? (
                  <SkeletonRows />
                ) : filteredRows.length === 0 ? (
                  <EmptyState
                    hasRows={rows.length > 0}
                    onSimulate={rows.length === 0 ? handleSimulate : null}
                  />
                ) : (
                  filteredRows.map((row) => (
                    <BoardRow
                      key={row.row_id}
                      row={row}
                      selected={selectedRowId === row.row_id}
                      onClick={() => setSelectedRowId(row.row_id)}
                    />
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Row detail drawer ── */}
      <RowDetailDrawer
        rowId={selectedRowId}
        open={!!selectedRowId}
        onOpenChange={(v) => { if (!v) setSelectedRowId(null); }}
        onActioned={handleRowActioned}
      />
    </div>
  );
}
