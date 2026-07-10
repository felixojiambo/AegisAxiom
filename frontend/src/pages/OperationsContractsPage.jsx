/**
 * OperationsContractsPage — Contract Financials
 *
 * Hour-based contract financials view sourced from the hydrated
 * Zoho contract fields. AR/AP totals are NOT shown on this page
 * (the banner says so).
 *
 * Endpoint:
 *   GET /api/operations/contracts/hours
 *   ?q=&contract_type=&status=&health=&page=&page_size=
 *
 * Response (May 25, 2026 reshape):
 *   {
 *     items: [...current page only...],
 *     summary: {
 *       total_contracts, purchased_hours, consumed_hours,
 *       remaining_hours, derived_remaining_hours,
 *       health_counts: { over_consumed, warning, healthy,
 *                        missing_data, unknown, n_a }
 *     },
 *     page, page_size, total, total_unfiltered
 *   }
 *
 * Pagination is server-side (default 50, options 25/50/100). The
 * summary is computed against the FILTERED set, not the current
 * page. All search/filter/page state lives in the URL so refresh
 * preserves the view.
 *
 * Read-only. No DB writes. Never touches resources / resource_types
 * / hourly_rate / project links.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';
import {
  DollarSign,
  AlertTriangle,
  CheckCircle,
  Activity,
  Clock,
  Search,
  Info,
  HelpCircle,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';

const API = API_URL;

const PAGE_SIZE_OPTIONS = [25, 50, 100];
const DEFAULT_PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

const fmtHours = (n) => {
  if (n === null || n === undefined) return '—';
  const num = Number(n);
  if (Number.isNaN(num)) return '—';
  return num.toFixed(2);
};

const fmtPct = (n) => {
  if (n === null || n === undefined) return '—';
  const num = Number(n);
  if (Number.isNaN(num)) return '—';
  return `${num.toFixed(2)}%`;
};

const fmtDate = (s) => {
  if (!s) return '—';
  try {
    const d = new Date(s);
    if (Number.isNaN(d.getTime())) return s;
    return d.toLocaleDateString();
  } catch {
    return s;
  }
};

// ---------------------------------------------------------------------------
// Health badge — single source of truth for the cell + summary cards
// ---------------------------------------------------------------------------

const HEALTH_META = {
  over_consumed: {
    label: 'Over-consumed',
    cls: 'bg-red-500/20 text-red-300 border border-red-500/30',
    icon: AlertTriangle,
  },
  warning: {
    label: 'Warning',
    cls: 'bg-amber-500/20 text-amber-300 border border-amber-500/30',
    icon: AlertTriangle,
  },
  healthy: {
    label: 'Healthy',
    cls: 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30',
    icon: CheckCircle,
  },
  missing_data: {
    label: 'Missing data',
    cls: 'bg-aegisaxiom-elevated/40 text-aegisaxiom-text-muted border border-aegisaxiom-border',
    icon: HelpCircle,
  },
  n_a: {
    label: 'N/A',
    cls: 'bg-aegisaxiom-elevated/40 text-aegisaxiom-text-muted border border-aegisaxiom-border',
    icon: HelpCircle,
  },
  unknown: {
    label: 'Unknown',
    cls: 'bg-aegisaxiom-elevated/40 text-aegisaxiom-text-muted border border-aegisaxiom-border',
    icon: HelpCircle,
  },
};

function HealthBadge({ value }) {
  const meta = HEALTH_META[value] || HEALTH_META.unknown;
  const Icon = meta.icon;
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium ${meta.cls}`}
      data-testid={`contract-financials-health-${value}`}
    >
      <Icon className="w-3 h-3" />
      {meta.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Summary cards
// ---------------------------------------------------------------------------

function SummaryCard({ label, value, sub, icon: Icon, accentClass, testid, title }) {
  return (
    <div className="glass-card p-4 space-y-1" data-testid={testid} title={title || undefined}>
      <div className="flex items-center justify-between">
        <p className="text-xs text-aegisaxiom-text-muted uppercase tracking-wide">{label}</p>
        {Icon ? <Icon className={`w-4 h-4 ${accentClass || 'text-aegisaxiom-text-muted'}`} /> : null}
      </div>
      <p className="text-lg font-semibold text-aegisaxiom-text">{value}</p>
      {sub ? <p className="text-[11px] text-aegisaxiom-text-muted">{sub}</p> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function OperationsContractsPage() {
  const { hasPermission } = usePermissions();
  const canView = hasPermission('operations.contracts.view') || hasPermission('*');

  const [searchParams, setSearchParams] = useSearchParams();

  // URL-backed state — search/filter/page/page_size live in the
  // query string so refresh preserves the view.
  const q = searchParams.get('q') || '';
  const typeFilter = searchParams.get('contract_type') || '';
  const statusFilter = searchParams.get('status') || '';
  const healthFilter = searchParams.get('health') || '';
  const page = Math.max(1, parseInt(searchParams.get('page') || '1', 10) || 1);
  const rawPageSize = parseInt(
    searchParams.get('page_size') || String(DEFAULT_PAGE_SIZE),
    10,
  );
  const pageSize = PAGE_SIZE_OPTIONS.includes(rawPageSize)
    ? rawPageSize
    : DEFAULT_PAGE_SIZE;

  const setQuery = useCallback(
    (patch) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(patch)) {
            if (v === '' || v === null || v === undefined) {
              next.delete(k);
            } else {
              next.set(k, String(v));
            }
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // Search input updates immediately; URL/fetch updates after debounce.
  const [qDraft, setQDraft] = useState(q);
  useEffect(() => { setQDraft(q); }, [q]);
  useEffect(() => {
    if (qDraft === q) return;
    const t = setTimeout(() => {
      // Reset to page 1 on every query change.
      setQuery({ q: qDraft, page: 1 });
    }, 250);
    return () => clearTimeout(t);
  }, [qDraft, q, setQuery]);

  const [items, setItems] = useState([]);
  const [summary, setSummary] = useState(null);
  const [total, setTotal] = useState(0);
  const [totalUnfiltered, setTotalUnfiltered] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Track the most recent in-flight request so a stale response
  // can't overwrite a fresher one (the user might be typing into q
  // or paging quickly).
  const requestSeqRef = useRef(0);

  const load = useCallback(async () => {
    if (!canView) return;
    const seq = ++requestSeqRef.current;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (q.trim()) params.set('q', q.trim());
      if (typeFilter) params.set('contract_type', typeFilter);
      if (statusFilter) params.set('status', statusFilter);
      if (healthFilter) params.set('health', healthFilter);
      params.set('page', String(page));
      params.set('page_size', String(pageSize));
      const res = await api.get(
        `${API}/api/operations/contracts/hours?${params.toString()}`,
      );
      if (seq !== requestSeqRef.current) return;
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        setError(
          typeof detail?.detail === 'string'
            ? detail.detail
            : 'Could not load contract financials. Please try again.',
        );
        return;
      }
      const data = await res.json();
      setItems(Array.isArray(data?.items) ? data.items : []);
      setSummary(data?.summary || null);
      setTotal(Number.isFinite(data?.total) ? data.total : 0);
      setTotalUnfiltered(
        Number.isFinite(data?.total_unfiltered) ? data.total_unfiltered : 0,
      );
    } catch (e) {
      if (seq !== requestSeqRef.current) return;
      setError(e?.message || 'Could not load contract financials.');
    } finally {
      if (seq === requestSeqRef.current) setLoading(false);
    }
  }, [canView, q, typeFilter, statusFilter, healthFilter, page, pageSize]);

  useEffect(() => { load(); }, [load]);

  const contractTypeOptions = useMemo(() => {
    const seen = new Set([
      'boh', 'boh_with_boa', 'boa', 'fixed_fee', 'monthly',
      'wholesale_mrr', 'simplisecure', 'simplicomply', 'other',
    ]);
    for (const it of items) {
      if (it.contract_type) seen.add(it.contract_type);
    }
    return Array.from(seen).sort();
  }, [items]);

  // Truthy when ANY user-facing filter is active. Drives the
  // "matching contracts" suffix on the count chip.
  const filtersActive = Boolean(
    q.trim() || typeFilter || statusFilter || healthFilter,
  );

  // Pagination math.
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const showingStart = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const showingEnd = Math.min(total, page * pageSize);

  if (!canView) {
    return (
      <div className="flex items-center justify-center h-64" data-testid="contract-financials-denied">
        <p className="text-aegisaxiom-text-muted">
          You do not have permission to view contract financials.
        </p>
      </div>
    );
  }

  // Health-count cards — render only buckets that have at least
  // one contract. Hiding zero buckets avoids clutter; the visible
  // cards always reconcile because every classified contract lands
  // in exactly one bucket server-side.
  const healthCounts = summary?.health_counts || {};
  const showBucket = (key) => Number(healthCounts[key] || 0) > 0;

  return (
    <div className="space-y-6 p-6" data-testid="contract-financials-page">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <DollarSign className="w-6 h-6 text-violet-400" />
          <div>
            <h1 className="text-xl font-semibold text-aegisaxiom-text">Contract Financials</h1>
            <p className="text-xs text-aegisaxiom-text-secondary">
              Hours, consumption, and contract health at a glance.
            </p>
          </div>
        </div>
      </div>

      {/* AR/AP disclaimer — neutral copy (no aspirational release ETA). */}
      <div
        className="flex items-start gap-2 p-3 rounded border border-aegisaxiom-border bg-aegisaxiom-elevated/30 text-[11px] text-aegisaxiom-text-secondary"
        data-testid="contract-financials-arap-notice"
      >
        <Info className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 text-violet-400" />
        <div>
          <strong className="text-aegisaxiom-text">
            AR/AP data is not connected on this page.
          </strong>{' '}
          This view currently uses imported contract-hour data: contract size,
          consumed hours, remaining hours, and consumption health.
        </div>
      </div>

      {/* Summary cards — backed by the server's health_counts so the
          cards always reconcile to total_contracts. */}
      {summary && (
        <div className="grid gap-3 grid-cols-2 md:grid-cols-4 xl:grid-cols-5" data-testid="contract-financials-summary">
          <SummaryCard
            label="Total Contracts"
            value={summary.total_contracts}
            icon={Activity}
            testid="summary-card-total"
          />
          <SummaryCard
            label="Over-consumed"
            value={healthCounts.over_consumed ?? 0}
            sub="remaining < 0"
            icon={AlertTriangle}
            accentClass="text-red-400"
            testid="summary-card-over-consumed"
          />
          <SummaryCard
            label="Warning (>= 90%)"
            value={healthCounts.warning ?? 0}
            icon={AlertTriangle}
            accentClass="text-amber-400"
            testid="summary-card-warning"
          />
          <SummaryCard
            label="Healthy"
            value={healthCounts.healthy ?? 0}
            icon={CheckCircle}
            accentClass="text-emerald-400"
            testid="summary-card-healthy"
          />
          <SummaryCard
            label="Missing data"
            value={healthCounts.missing_data ?? 0}
            sub="block-type contract with no consumption signal"
            icon={HelpCircle}
            accentClass="text-aegisaxiom-text-muted"
            testid="summary-card-missing-data"
          />
          {showBucket('unknown') && (
            <SummaryCard
              label="Unknown"
              value={healthCounts.unknown}
              sub="contract type the classifier doesn't recognise"
              icon={HelpCircle}
              accentClass="text-aegisaxiom-text-muted"
              testid="summary-card-unknown"
            />
          )}
          {showBucket('n_a') && (
            <SummaryCard
              label="N/A"
              value={healthCounts.n_a}
              sub="not an hour-consumption contract type"
              icon={HelpCircle}
              accentClass="text-aegisaxiom-text-muted"
              testid="summary-card-na"
            />
          )}
          <SummaryCard
            label="Purchased hours"
            value={fmtHours(summary.purchased_hours)}
            icon={Clock}
            testid="summary-card-purchased"
          />
          <SummaryCard
            label="Consumed hours"
            value={fmtHours(summary.consumed_hours)}
            icon={Clock}
            testid="summary-card-consumed"
          />
          <SummaryCard
            label="Remaining hours"
            value={fmtHours(summary.remaining_hours)}
            sub={
              typeof summary.derived_remaining_hours === 'number'
                ? `Purchased − Consumed: ${fmtHours(summary.derived_remaining_hours)}`
                : 'negative = over-consumed across portfolio'
            }
            icon={Clock}
            accentClass={
              summary.remaining_hours < 0 ? 'text-red-400' : 'text-emerald-400'
            }
            testid="summary-card-remaining"
            title="Remaining Hours is summed from imported contract remaining-hour values where available. It may not equal Purchased Hours minus Consumed Hours when imported fields are missing or not applicable."
          />
        </div>
      )}

      {/* Filter bar */}
      <div className="flex items-center gap-3 flex-wrap" data-testid="contract-financials-filters">
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-aegisaxiom-text-muted" />
          <input
            type="text"
            value={qDraft}
            onChange={(e) => setQDraft(e.target.value)}
            placeholder="Search number / name / account"
            className="aegisaxiom-input pl-7 w-64 text-xs h-8"
            data-testid="contract-financials-search"
          />
        </div>
        <select
          value={typeFilter}
          onChange={(e) => setQuery({ contract_type: e.target.value, page: 1 })}
          className="aegisaxiom-input text-xs h-8 px-2"
          data-testid="contract-financials-type-filter"
        >
          <option value="">All types</option>
          {contractTypeOptions.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <select
          value={statusFilter}
          onChange={(e) => setQuery({ status: e.target.value, page: 1 })}
          className="aegisaxiom-input text-xs h-8 px-2"
          data-testid="contract-financials-status-filter"
        >
          <option value="">All statuses</option>
          <option value="draft">draft</option>
          <option value="active">active</option>
          <option value="closed">closed</option>
        </select>
        <select
          value={healthFilter}
          onChange={(e) => setQuery({ health: e.target.value, page: 1 })}
          className="aegisaxiom-input text-xs h-8 px-2"
          data-testid="contract-financials-health-filter"
        >
          <option value="">All health</option>
          <option value="over_consumed">Over-consumed</option>
          <option value="warning">Warning</option>
          <option value="healthy">Healthy</option>
          <option value="missing_data">Missing data</option>
          <option value="unknown">Unknown</option>
          <option value="n_a">N/A</option>
        </select>
        <select
          value={pageSize}
          onChange={(e) =>
            setQuery({ page_size: parseInt(e.target.value, 10), page: 1 })
          }
          className="aegisaxiom-input text-xs h-8 px-2"
          data-testid="contract-financials-page-size"
        >
          {PAGE_SIZE_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>{opt} per page</option>
          ))}
        </select>
        <span
          className="text-[11px] text-aegisaxiom-text-muted ml-auto"
          data-testid="contract-financials-count"
        >
          {summary
            ? filtersActive
              ? `Showing ${showingStart}–${showingEnd} of ${total} matching contracts${
                  totalUnfiltered && totalUnfiltered !== total
                    ? ` (${totalUnfiltered} total)`
                    : ''
                }`
              : `Showing ${showingStart}–${showingEnd} of ${total}`
            : ''}
        </span>
      </div>

      {error ? (
        <div className="flex items-center gap-2 p-3 glass-card text-red-400 text-sm" data-testid="contract-financials-error">
          <AlertTriangle className="w-4 h-4" />
          <span>{error}</span>
        </div>
      ) : null}

      {/* Table */}
      <div className="glass-card overflow-x-auto" data-testid="contract-financials-table-wrap">
        <table className="w-full text-xs" data-testid="contract-financials-table">
          <thead className="sticky top-0 bg-aegisaxiom-surface/80 backdrop-blur z-10">
            <tr className="border-b border-aegisaxiom-border text-left text-aegisaxiom-text-muted">
              <th className="px-3 py-2 font-medium">Contract #</th>
              <th className="px-3 py-2 font-medium">Account</th>
              <th className="px-3 py-2 font-medium">Name</th>
              <th className="px-3 py-2 font-medium">Type</th>
              <th className="px-3 py-2 font-medium text-right">Total</th>
              <th className="px-3 py-2 font-medium text-right">Remaining</th>
              <th className="px-3 py-2 font-medium text-right">Consumed</th>
              <th className="px-3 py-2 font-medium text-right">%</th>
              <th className="px-3 py-2 font-medium">Health</th>
              <th className="px-3 py-2 font-medium">Invoice Type</th>
              <th className="px-3 py-2 font-medium">Contract Status</th>
              <th
                className="px-3 py-2 font-medium"
                title="Imported AP note. Structured AR/AP rollups are not connected on this page."
              >
                AP Issues
              </th>
              <th className="px-3 py-2 font-medium">Created By</th>
              <th className="px-3 py-2 font-medium">Created</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={14} className="px-3 py-8 text-center text-aegisaxiom-text-muted" data-testid="contract-financials-loading">
                  Loading…
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={14} className="px-3 py-8 text-center text-aegisaxiom-text-muted" data-testid="contract-financials-empty">
                  {filtersActive
                    ? 'No contracts match the current filters.'
                    : 'No contracts available.'}
                </td>
              </tr>
            ) : (
              items.map((c) => {
                const remaining = c.remaining_hours;
                const remainingNegative = typeof remaining === 'number' && remaining < 0;
                return (
                  <tr key={c.contract_id} className="border-b border-aegisaxiom-border/40 hover:bg-aegisaxiom-elevated/30 transition-colors">
                    <td className="px-3 py-2 text-aegisaxiom-text font-mono text-[11px]">
                      {c.contract_number || '—'}
                    </td>
                    <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                      {c.account_name || '—'}
                    </td>
                    <td className="px-3 py-2 text-aegisaxiom-text">
                      {c.name || '—'}
                    </td>
                    <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                      {c.contract_type_raw || c.contract_type || '—'}
                    </td>
                    <td className="px-3 py-2 text-right text-aegisaxiom-text">
                      {fmtHours(c.total_hours)}
                    </td>
                    <td
                      className={
                        remainingNegative
                          ? 'px-3 py-2 text-right text-red-400 font-medium'
                          : 'px-3 py-2 text-right text-aegisaxiom-text'
                      }
                    >
                      {fmtHours(remaining)}
                    </td>
                    <td className="px-3 py-2 text-right text-aegisaxiom-text">
                      {fmtHours(c.consumed_hours)}
                    </td>
                    <td className="px-3 py-2 text-right text-aegisaxiom-text">
                      {fmtPct(c.consumed_hours_percent)}
                    </td>
                    <td className="px-3 py-2">
                      <HealthBadge value={c.hour_health} />
                    </td>
                    <td className="px-3 py-2 text-aegisaxiom-text-secondary truncate max-w-[180px]" title={c.invoice_type_raw || ''}>
                      {c.invoice_type_raw || '—'}
                    </td>
                    <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                      {c.contract_status_raw || c.status || '—'}
                    </td>
                    <td className="px-3 py-2">
                      {c.ap_issues_raw ? (
                        <span
                          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-amber-500/20 text-amber-300 border border-amber-500/30"
                          title={c.ap_issues_raw}
                        >
                          <AlertTriangle className="w-3 h-3" /> AP
                        </span>
                      ) : (
                        <span className="text-aegisaxiom-text-muted">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                      {c.source_created_by || '—'}
                    </td>
                    <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                      {fmtDate(c.source_created_at || c.created_at)}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination footer */}
      {summary && total > 0 && (
        <div
          className="flex items-center justify-between gap-3 flex-wrap text-xs text-aegisaxiom-text-secondary"
          data-testid="contract-financials-pagination"
        >
          <span data-testid="contract-financials-range">
            Showing {showingStart}–{showingEnd} of {total}
            {filtersActive ? ' matching contracts' : ''}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setQuery({ page: page - 1 })}
              disabled={page <= 1 || loading}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded border border-aegisaxiom-border hover:bg-aegisaxiom-elevated disabled:opacity-40"
              data-testid="contract-financials-prev"
            >
              <ChevronLeft className="w-3 h-3" />
              Previous
            </button>
            <span className="px-1">
              Page {page} of {totalPages}
            </span>
            <button
              type="button"
              onClick={() => setQuery({ page: page + 1 })}
              disabled={page >= totalPages || loading}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded border border-aegisaxiom-border hover:bg-aegisaxiom-elevated disabled:opacity-40"
              data-testid="contract-financials-next"
            >
              Next
              <ChevronRight className="w-3 h-3" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
