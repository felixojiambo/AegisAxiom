import React, { useEffect, useState, useCallback } from 'react';
import { ChevronUp, ChevronDown, AlertCircle, Filter } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { DEPARTMENTS } from './constants';
import DecisionDrawer from './DecisionDrawer';
import { formatDistanceToNow, parseISO } from 'date-fns';

const DISTRO_OPTIONS = [
  { value: '', label: 'All distros' },
  { value: 'quotes', label: 'quotes@' },
  { value: 'orders', label: 'orders@' },
  { value: 'cable', label: 'cable@' },
  { value: 'oni', label: 'ONI' },
  { value: 'invest', label: 'invest@' },
];

const PAGE_SIZE = 100;

function formatConfidence(val) {
  if (val == null) return { text: '—', cls: 'text-aegisaxiom-text-muted' };
  const pct = Math.round(val * 100);
  let cls = 'text-green-400';
  if (val < 0.7) cls = 'text-red-400';
  else if (val < 0.85) cls = 'text-amber-400';
  return { text: `${pct}%`, cls };
}

function formatReceived(iso) {
  if (!iso) return '—';
  try {
    return formatDistanceToNow(parseISO(iso), { addSuffix: true });
  } catch {
    return iso;
  }
}

function truncate(str, max = 60) {
  if (!str) return '';
  return str.length > max ? str.slice(0, max) + '…' : str;
}

export default function DecisionsTab() {
  const [decisions, setDecisions] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Filters
  const [distroFilter, setDistroFilter] = useState('');
  const [deptFilter, setDeptFilter] = useState('');
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');

  // Pagination
  const [offset, setOffset] = useState(0);

  // Sort
  const [sortField, setSortField] = useState('received_at');
  const [sortDir, setSortDir] = useState('desc');

  // Drawer
  const [selectedDecision, setSelectedDecision] = useState(null);

  const fetchDecisions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      params.set('limit', PAGE_SIZE);
      params.set('offset', offset);
      if (distroFilter) params.set('distro', distroFilter);
      if (deptFilter) params.set('department', deptFilter);
      if (fromDate) params.set('from_date', fromDate);
      if (toDate) params.set('to_date', toDate);

      const res = await api.get(`${API_URL}/api/ai-dispatcher/decisions?${params.toString()}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || 'Failed to load decisions');

      let rows = Array.isArray(data?.decisions) ? data.decisions : [];

      // Client-side sort
      rows = [...rows].sort((a, b) => {
        const aVal = a[sortField] ?? '';
        const bVal = b[sortField] ?? '';
        if (sortDir === 'asc') return aVal < bVal ? -1 : aVal > bVal ? 1 : 0;
        return aVal > bVal ? -1 : aVal < bVal ? 1 : 0;
      });

      setDecisions(rows);
      setTotal(data?.total ?? rows.length);
    } catch (err) {
      setError(err?.message || 'Failed to load decisions');
    } finally {
      setLoading(false);
    }
  }, [offset, distroFilter, deptFilter, fromDate, toDate, sortField, sortDir]);

  useEffect(() => {
    fetchDecisions();
  }, [fetchDecisions]);

  const handleSort = (field) => {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortDir('desc');
    }
  };

  const SortIcon = ({ field }) => {
    if (sortField !== field) return null;
    return sortDir === 'asc' ? (
      <ChevronUp size={14} className="inline ml-1" />
    ) : (
      <ChevronDown size={14} className="inline ml-1" />
    );
  };

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <h2 className="text-lg font-semibold text-aegisaxiom-text">Routing Decisions</h2>
        <p className="text-sm text-aegisaxiom-text-secondary mt-0.5">
          Every AI dispatch decision, in shadow mode.{' '}
          {total > 0 && <span>Total: {total.toLocaleString()}</span>}
        </p>
      </div>

      {/* Filters */}
      <div className="glass-card p-4">
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Distro</label>
            <select
              className="aegisaxiom-input"
              value={distroFilter}
              onChange={(e) => { setDistroFilter(e.target.value); setOffset(0); }}
            >
              {DISTRO_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Department</label>
            <select
              className="aegisaxiom-input"
              value={deptFilter}
              onChange={(e) => { setDeptFilter(e.target.value); setOffset(0); }}
            >
              <option value="">All departments</option>
              {DEPARTMENTS.map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-aegisaxiom-text-secondary mb-1">From</label>
            <input
              type="date"
              className="aegisaxiom-input"
              value={fromDate}
              onChange={(e) => { setFromDate(e.target.value); setOffset(0); }}
            />
          </div>
          <div>
            <label className="block text-xs text-aegisaxiom-text-secondary mb-1">To</label>
            <input
              type="date"
              className="aegisaxiom-input"
              value={toDate}
              onChange={(e) => { setToDate(e.target.value); setOffset(0); }}
            />
          </div>
          {(distroFilter || deptFilter || fromDate || toDate) && (
            <button
              className="btn-secondary text-xs"
              onClick={() => {
                setDistroFilter('');
                setDeptFilter('');
                setFromDate('');
                setToDate('');
                setOffset(0);
              }}
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {/* Error */}
      {!loading && error && (
        <div className="glass-card p-4 flex items-start gap-3">
          <AlertCircle size={18} className="text-red-400 shrink-0 mt-0.5" />
          <div>
            <p className="text-sm text-aegisaxiom-text">{error}</p>
            <button onClick={fetchDecisions} className="text-xs text-violet-400 hover:text-violet-300 mt-1">
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && decisions.length === 0 && (
        <div className="glass-card p-12 text-center">
          <Filter size={36} className="mx-auto text-aegisaxiom-text-muted mb-3" />
          <p className="text-aegisaxiom-text font-medium">No decisions yet</p>
          <p className="text-sm text-aegisaxiom-text-secondary mt-1 max-w-sm mx-auto">
            The n8n workflow will start posting routing decisions here once it's enabled.
          </p>
        </div>
      )}

      {/* Table */}
      {!loading && !error && decisions.length > 0 && (
        <>
          <div className="glass-card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-aegisaxiom-border">
                    <th
                      className="text-left px-3 py-3 text-aegisaxiom-text-secondary font-medium cursor-pointer hover:text-aegisaxiom-text whitespace-nowrap select-none"
                      onClick={() => handleSort('received_at')}
                    >
                      Received <SortIcon field="received_at" />
                    </th>
                    <th className="text-left px-3 py-3 text-aegisaxiom-text-secondary font-medium whitespace-nowrap">Distro</th>
                    <th className="text-left px-3 py-3 text-aegisaxiom-text-secondary font-medium whitespace-nowrap">Sender domain</th>
                    <th className="text-left px-3 py-3 text-aegisaxiom-text-secondary font-medium">Subject</th>
                    <th className="text-left px-3 py-3 text-aegisaxiom-text-secondary font-medium whitespace-nowrap">Dept</th>
                    <th className="text-left px-3 py-3 text-aegisaxiom-text-secondary font-medium whitespace-nowrap">Ticket type</th>
                    <th className="text-left px-3 py-3 text-aegisaxiom-text-secondary font-medium whitespace-nowrap">Owner</th>
                    <th
                      className="text-right px-3 py-3 text-aegisaxiom-text-secondary font-medium cursor-pointer hover:text-aegisaxiom-text whitespace-nowrap select-none"
                      onClick={() => handleSort('ai_confidence')}
                    >
                      Confidence <SortIcon field="ai_confidence" />
                    </th>
                    <th className="text-left px-3 py-3 text-aegisaxiom-text-secondary font-medium">Flags</th>
                  </tr>
                </thead>
                <tbody>
                  {decisions.map((d) => {
                    const conf = formatConfidence(d.ai_confidence);
                    const flags = Array.isArray(d.flags) ? d.flags : [];
                    return (
                      <tr
                        key={d.dispatch_item_id}
                        onClick={() => setSelectedDecision(d)}
                        className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-elevated cursor-pointer transition-colors last:border-0"
                      >
                        <td className="px-3 py-2.5 text-aegisaxiom-text-secondary whitespace-nowrap text-xs">
                          {formatReceived(d.received_at)}
                        </td>
                        <td className="px-3 py-2.5 text-aegisaxiom-text-secondary text-xs whitespace-nowrap">
                          {d.distro || '—'}
                        </td>
                        <td className="px-3 py-2.5 text-aegisaxiom-text-secondary text-xs whitespace-nowrap">
                          {d.sender_domain || '—'}
                        </td>
                        <td className="px-3 py-2.5 text-aegisaxiom-text max-w-xs">
                          <span title={d.subject} className="block truncate text-sm">
                            {truncate(d.subject, 60)}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 text-aegisaxiom-text-secondary text-xs whitespace-nowrap">
                          {d.ai_department || '—'}
                        </td>
                        <td className="px-3 py-2.5 text-aegisaxiom-text-secondary text-xs whitespace-nowrap">
                          {d.ai_ticket_type || '—'}
                        </td>
                        <td className="px-3 py-2.5 text-aegisaxiom-text-secondary text-xs whitespace-nowrap">
                          {d.ai_owner || '—'}
                        </td>
                        <td className={`px-3 py-2.5 text-right font-medium text-xs whitespace-nowrap ${conf.cls}`}>
                          {conf.text}
                        </td>
                        <td className="px-3 py-2.5">
                          <FlagsBadgeCluster flags={flags} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between text-sm text-aegisaxiom-text-secondary">
              <span>
                Page {currentPage} of {totalPages} ({total.toLocaleString()} total)
              </span>
              <div className="flex gap-2">
                <button
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  className="btn-secondary text-xs disabled:opacity-40"
                >
                  Previous
                </button>
                <button
                  disabled={offset + PAGE_SIZE >= total}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                  className="btn-secondary text-xs disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </>
      )}

      {/* Decision detail drawer */}
      {selectedDecision && (
        <DecisionDrawer
          decision={selectedDecision}
          onClose={() => setSelectedDecision(null)}
        />
      )}
    </div>
  );
}

function FlagsBadgeCluster({ flags }) {
  if (!flags || flags.length === 0) return null;
  const MAX_SHOW = 2;
  const visible = flags.slice(0, MAX_SHOW);
  const rest = flags.length - MAX_SHOW;
  return (
    <div className="flex flex-wrap gap-1">
      {visible.map((f) => (
        <span
          key={f}
          title={f}
          className="bg-amber-500/15 text-amber-400 px-1.5 py-0.5 rounded text-xs max-w-[120px] truncate"
        >
          {f}
        </span>
      ))}
      {rest > 0 && (
        <span
          title={flags.slice(MAX_SHOW).join(', ')}
          className="bg-aegisaxiom-elevated text-aegisaxiom-text-muted px-1.5 py-0.5 rounded text-xs"
        >
          +{rest}
        </span>
      )}
    </div>
  );
}
