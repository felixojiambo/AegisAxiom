import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Search, Settings2, Check, X, Download, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { fmtCurrency, fmtDate } from '@/lib/financeFormat';
import { downloadCSV } from '@/lib/financeCSV';

const BASE = `${API_URL}/api/finance`;
const LIMIT = 100;

const ALL_COLUMNS = [
  { key: 'tx_date', label: 'Date', default: true },
  { key: 'txn_type', label: 'Type', default: true },
  { key: 'doc_num', label: 'Doc #', default: true },
  { key: 'sales1', label: 'Deal ID', default: true },
  { key: 'location', label: 'Location', default: true },
  { key: 'class_name', label: 'Class', default: false },
  { key: 'name', label: 'Name', default: true },
  { key: 'account_name', label: 'Account', default: true },
  { key: 'memo', label: 'Memo', default: false },
  { key: 'basis', label: 'Basis', default: false },
  { key: 'amount', label: 'Amount (signed)', default: false },
  { key: 'debit', label: 'Debit', default: true },
  { key: 'credit', label: 'Credit', default: true },
];

const LS_COLS_KEY = 'aegisaxiom.fcc.gl.cols.v2';

function loadColumnState() {
  try {
    const raw = localStorage.getItem(LS_COLS_KEY);
    if (raw) return JSON.parse(raw);
  } catch {
    /* ignore */
  }
  return Object.fromEntries(ALL_COLUMNS.map((c) => [c.key, c.default]));
}

function Pagination({ total, page, setPage }) {
  const pages = Math.ceil(total / LIMIT);
  if (pages <= 1) return null;
  return (
    <div className="flex items-center justify-between px-5 py-3 border-t border-aegisaxiom-border text-[12px] text-aegisaxiom-text-secondary">
      <span>
        Showing{' '}
        <span className="font-mono">
          {page * LIMIT + 1}–{Math.min((page + 1) * LIMIT, total)}
        </span>{' '}
        of <span className="font-mono">{total.toLocaleString()}</span>
      </span>
      <div className="flex items-center gap-1">
        <button
          data-testid="pg-prev"
          onClick={() => setPage(Math.max(0, page - 1))}
          disabled={page === 0}
          className="h-7 px-2 rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface disabled:opacity-40 hover:bg-aegisaxiom-elevated text-aegisaxiom-text"
        >
          Prev
        </button>
        <span className="px-2 font-mono">
          {page + 1} / {pages}
        </span>
        <button
          data-testid="pg-next"
          onClick={() => setPage(Math.min(pages - 1, page + 1))}
          disabled={page >= pages - 1}
          className="h-7 px-2 rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface disabled:opacity-40 hover:bg-aegisaxiom-elevated text-aegisaxiom-text"
        >
          Next
        </button>
      </div>
    </div>
  );
}

function GLCell({ col, row }) {
  const v = row[col];
  if (col === 'tx_date')
    return <td className="px-4 py-1.5 font-mono whitespace-nowrap text-aegisaxiom-text">{fmtDate(v)}</td>;
  if (col === 'txn_type' || col === 'basis')
    return <td className="px-4 py-1.5 text-aegisaxiom-text-secondary whitespace-nowrap">{v || '—'}</td>;
  if (col === 'doc_num')
    return <td className="px-4 py-1.5 font-mono whitespace-nowrap text-aegisaxiom-text">{v || '—'}</td>;
  if (col === 'sales1')
    return (
      <td className="px-4 py-1.5 font-mono text-[12px] whitespace-nowrap text-aegisaxiom-text">
        {v || <span className="text-aegisaxiom-text-muted">—</span>}
      </td>
    );
  if (col === 'location')
    return (
      <td className="px-4 py-1.5 whitespace-nowrap">
        {v ? (
          <span className="px-2 py-0.5 rounded-md text-[11px] bg-violet-500/15 text-violet-400">
            {v}
          </span>
        ) : (
          <span className="text-aegisaxiom-text-muted">—</span>
        )}
      </td>
    );
  if (col === 'class_name')
    return (
      <td className="px-4 py-1.5 whitespace-nowrap">
        {v ? (
          <span className="px-2 py-0.5 rounded-md text-[11px] bg-aegisaxiom-elevated text-aegisaxiom-text-muted">
            {v}
          </span>
        ) : (
          <span className="text-aegisaxiom-text-muted">—</span>
        )}
      </td>
    );
  if (col === 'name')
    return (
      <td
        className="px-4 py-1.5 whitespace-nowrap max-w-[200px] truncate text-aegisaxiom-text"
        title={v || ''}
      >
        {v || '—'}
      </td>
    );
  if (col === 'account_name')
    return (
      <td
        className="px-4 py-1.5 text-aegisaxiom-text-secondary whitespace-nowrap max-w-[260px] truncate"
        title={v || ''}
      >
        {v || '—'}
      </td>
    );
  if (col === 'memo')
    return (
      <td className="px-4 py-1.5 text-aegisaxiom-text-secondary max-w-[280px] truncate" title={v || ''}>
        {v || '—'}
      </td>
    );
  if (col === 'amount') {
    const n = Number(v || 0);
    return (
      <td
        className={`px-4 py-1.5 text-right font-mono tabular-nums ${n < 0 ? 'text-red-400' : 'text-aegisaxiom-text'}`}
      >
        {fmtCurrency(n)}
      </td>
    );
  }
  if (col === 'debit' || col === 'credit')
    return (
      <td className="px-4 py-1.5 text-right font-mono tabular-nums text-aegisaxiom-text">
        {v ? fmtCurrency(v) : <span className="text-aegisaxiom-text-muted">—</span>}
      </td>
    );
  return <td className="px-4 py-1.5 text-aegisaxiom-text">{v}</td>;
}

export default function GLExplorerTab() {
  const [data, setData] = useState({ rows: [], total: 0 });
  const [filters, setFilters] = useState({
    date_from: '',
    date_to: '',
    account: '',
    txn_type: '',
    location: '',
    class_name: '',
    search: '',
    basis: 'accrual',
  });
  const [page, setPage] = useState(0);
  const [accounts, setAccounts] = useState([]);
  const [txnTypes, setTxnTypes] = useState([]);
  const [locations, setLocations] = useState([]);
  const [classes, setClasses] = useState([]);
  const [visible, setVisible] = useState(loadColumnState);
  const [colsOpen, setColsOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const colsRef = useRef(null);

  useEffect(() => {
    api.get(`${BASE}/gl/accounts`).then(async (r) => {
      if (r.ok) {
        const d = await r.json();
        setAccounts(d.rows || []);
      }
    });
    api.get(`${BASE}/gl/txn-types`).then(async (r) => {
      if (r.ok) {
        const d = await r.json();
        setTxnTypes(d.rows || []);
      }
    });
    api.get(`${BASE}/gl/locations`).then(async (r) => {
      if (r.ok) {
        const d = await r.json();
        setLocations(d.rows || []);
      }
    });
    api.get(`${BASE}/gl/classes`).then(async (r) => {
      if (r.ok) {
        const d = await r.json();
        setClasses(d.rows || []);
      }
    });
  }, []);

  useEffect(() => {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => v && params.set(k, v));
    params.set('limit', String(LIMIT));
    params.set('skip', String(page * LIMIT));
    api.get(`${BASE}/gl?${params.toString()}`).then(async (r) => {
      if (r.ok) setData(await r.json());
    });
  }, [filters, page]);

  useEffect(() => {
    const onDoc = (e) => {
      if (colsRef.current && !colsRef.current.contains(e.target)) setColsOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const upd = (k, v) => {
    setFilters((p) => ({ ...p, [k]: v }));
    setPage(0);
  };

  const toggleColumn = (key) => {
    setVisible((p) => {
      const next = { ...p, [key]: !p[key] };
      try {
        localStorage.setItem(LS_COLS_KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  const resetColumns = () => {
    const def = Object.fromEntries(ALL_COLUMNS.map((c) => [c.key, c.default]));
    setVisible(def);
    try {
      localStorage.setItem(LS_COLS_KEY, JSON.stringify(def));
    } catch {
      /* ignore */
    }
  };

  const visibleCols = useMemo(() => ALL_COLUMNS.filter((c) => visible[c.key]), [visible]);

  const activeFilters = useMemo(() => {
    const chips = [];
    if (filters.date_from) chips.push({ k: 'date_from', label: `From ${filters.date_from}` });
    if (filters.date_to) chips.push({ k: 'date_to', label: `To ${filters.date_to}` });
    if (filters.account) chips.push({ k: 'account', label: `Account: ${filters.account}` });
    if (filters.txn_type) chips.push({ k: 'txn_type', label: `Type: ${filters.txn_type}` });
    if (filters.location) chips.push({ k: 'location', label: `Location: ${filters.location}` });
    if (filters.class_name) chips.push({ k: 'class_name', label: `Class: ${filters.class_name}` });
    if (filters.basis !== 'accrual') chips.push({ k: 'basis', label: `Basis: ${filters.basis}` });
    if (filters.search) chips.push({ k: 'search', label: `Search: ${filters.search}` });
    return chips;
  }, [filters]);

  const handleExportCSV = async () => {
    if (data.total === 0) {
      toast.error('Nothing to export');
      return;
    }
    setExporting(true);
    try {
      const params = new URLSearchParams();
      Object.entries(filters).forEach(([k, v]) => v && params.set(k, v));
      const cap = Math.min(data.total, 10000);
      params.set('limit', String(cap));
      params.set('skip', '0');
      const r = await api.get(`${BASE}/gl?${params.toString()}`);
      if (!r.ok) throw new Error('Export failed');
      const rd = await r.json();
      const exportRows = (rd.rows || []).map((row) => {
        const out = {};
        visibleCols.forEach((c) => {
          let v = row[c.key];
          if (c.key === 'tx_date') v = fmtDate(v);
          out[c.key] = v;
        });
        return out;
      });
      downloadCSV(
        `gl-export-${new Date().toISOString().slice(0, 10)}.csv`,
        visibleCols,
        exportRows,
      );
      toast.success(`Exported ${exportRows.length.toLocaleString()} rows`);
      if (data.total > cap)
        toast.warning(
          `Capped at ${cap.toLocaleString()} rows. Narrow your filters to export the rest.`,
        );
    } catch {
      toast.error('Export failed');
    } finally {
      setExporting(false);
    }
  };

  const selectCls =
    'h-9 px-3 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface text-[13px] text-aegisaxiom-text';

  return (
    <div className="space-y-6" data-testid="page-gl-explorer">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <p className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-0.5">
            LISTS
          </p>
          <h2 className="page-title">General Ledger Explorer</h2>
          <p className="text-sm text-aegisaxiom-text-secondary mt-1">
            {data.total.toLocaleString()} transactions match your filters · {visibleCols.length} of{' '}
            {ALL_COLUMNS.length} columns visible
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            data-testid="export-csv-btn"
            onClick={handleExportCSV}
            disabled={exporting || data.total === 0}
            className="h-9 px-3 btn-secondary flex items-center gap-2 text-[13px] disabled:opacity-60"
          >
            {exporting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Download className="h-3.5 w-3.5" />
            )}
            {exporting ? 'Exporting…' : 'Export CSV'}
          </button>
          <div className="relative" ref={colsRef}>
            <button
              data-testid="columns-btn"
              onClick={() => setColsOpen((s) => !s)}
              className="h-9 px-3 btn-secondary flex items-center gap-2 text-[13px]"
            >
              <Settings2 className="h-3.5 w-3.5" />
              Columns
              <span className="font-mono text-[11px] text-aegisaxiom-text-muted">
                {visibleCols.length}/{ALL_COLUMNS.length}
              </span>
            </button>
            {colsOpen && (
              <div
                data-testid="columns-menu"
                className="absolute right-0 mt-1 w-64 rounded-xl border border-aegisaxiom-border bg-aegisaxiom-surface shadow-xl py-1 z-30"
              >
                <div className="px-3 py-2 border-b border-aegisaxiom-border flex items-center justify-between">
                  <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">
                    VISIBLE COLUMNS
                  </div>
                  <button
                    data-testid="reset-cols-btn"
                    onClick={resetColumns}
                    className="text-[11px] text-aegisaxiom-text-secondary hover:text-aegisaxiom-text underline-offset-2 hover:underline"
                  >
                    Reset
                  </button>
                </div>
                <div className="max-h-[320px] overflow-y-auto py-1">
                  {ALL_COLUMNS.map((c) => (
                    <button
                      key={c.key}
                      data-testid={`col-toggle-${c.key}`}
                      onClick={() => toggleColumn(c.key)}
                      className="w-full text-left px-3 py-1.5 text-[13px] hover:bg-aegisaxiom-elevated flex items-center gap-2"
                    >
                      <span
                        className={`h-4 w-4 rounded border flex items-center justify-center shrink-0 ${visible[c.key] ? 'bg-violet-600 border-violet-600' : 'bg-aegisaxiom-surface border-aegisaxiom-border'}`}
                      >
                        {visible[c.key] ? <Check className="h-3 w-3 text-white" /> : null}
                      </span>
                      <span className="text-aegisaxiom-text">{c.label}</span>
                      <span className="ml-auto font-mono text-[10px] text-aegisaxiom-text-muted">
                        {c.key}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="glass-card p-4 grid grid-cols-2 md:grid-cols-8 gap-3">
        <input
          data-testid="gl-date-from"
          type="date"
          value={filters.date_from}
          onChange={(e) => upd('date_from', e.target.value)}
          className={`${selectCls} font-mono min-w-0`}
        />
        <input
          data-testid="gl-date-to"
          type="date"
          value={filters.date_to}
          onChange={(e) => upd('date_to', e.target.value)}
          className={`${selectCls} font-mono min-w-0`}
        />
        <select
          data-testid="gl-account"
          value={filters.account}
          onChange={(e) => upd('account', e.target.value)}
          className={`${selectCls} min-w-0`}
        >
          <option value="">All accounts</option>
          {accounts.slice(0, 200).map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <select
          data-testid="gl-txn-type"
          value={filters.txn_type}
          onChange={(e) => upd('txn_type', e.target.value)}
          className={`${selectCls} min-w-0`}
        >
          <option value="">All txn types</option>
          {txnTypes.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <select
          data-testid="gl-location"
          value={filters.location}
          onChange={(e) => upd('location', e.target.value)}
          className={`${selectCls} min-w-0`}
        >
          <option value="">All locations</option>
          {locations.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
        <select
          data-testid="gl-class"
          value={filters.class_name}
          onChange={(e) => upd('class_name', e.target.value)}
          className={`${selectCls} min-w-0`}
        >
          <option value="">All classes</option>
          {classes.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
        <select
          data-testid="gl-basis"
          value={filters.basis}
          onChange={(e) => upd('basis', e.target.value)}
          className={`${selectCls} min-w-0`}
        >
          <option value="accrual">Accrual</option>
          <option value="cash">Cash</option>
        </select>
        <div className="col-span-2 md:col-auto flex items-center gap-2 px-3 h-9 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface min-w-0">
          <Search className="h-3.5 w-3.5 text-aegisaxiom-text-muted shrink-0" />
          <input
            data-testid="gl-search"
            value={filters.search}
            onChange={(e) => upd('search', e.target.value)}
            placeholder="Memo / name / doc # / deal id"
            className="bg-transparent outline-none text-[13px] flex-1 min-w-0 text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted"
          />
        </div>
      </div>

      {activeFilters.length ? (
        <div className="flex items-center gap-2 flex-wrap" data-testid="active-filters">
          {activeFilters.map((c) => (
            <button
              key={c.k}
              onClick={() => upd(c.k, c.k === 'basis' ? 'accrual' : '')}
              className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] bg-violet-500/15 text-violet-400 hover:opacity-80 transition-opacity"
              data-testid={`active-${c.k}`}
            >
              <span>{c.label}</span>
              <X className="h-3 w-3" />
            </button>
          ))}
        </div>
      ) : null}

      <div className="glass-card overflow-hidden">
        {/* Desktop table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="border-b border-aegisaxiom-border bg-aegisaxiom-elevated/40">
                {visibleCols.map((c) => (
                  <th
                    key={c.key}
                    className={`px-4 py-2 text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted whitespace-nowrap ${['debit', 'credit', 'amount'].includes(c.key) ? 'text-right' : 'text-left'}`}
                  >
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r, i) => (
                <tr key={i} className="border-b border-aegisaxiom-border/50 hover:bg-aegisaxiom-elevated/30">
                  {visibleCols.map((c) => (
                    <GLCell key={c.key} col={c.key} row={r} />
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Mobile card list — shows the most important fields regardless of column toggles */}
        <div className="md:hidden">
          {data.rows.length === 0 ? (
            <div className="px-5 py-10 text-center text-[13px] text-aegisaxiom-text-muted">
              No transactions match the current filters.
            </div>
          ) : (
            <div className="p-3 flex flex-col gap-2.5">
              {data.rows.map((r, i) => {
                const debit = Number(r.debit || 0);
                const credit = Number(r.credit || 0);
                return (
                  <div
                    key={i}
                    className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3"
                    data-testid={`gl-card-${i}`}
                  >
                    <div className="flex items-start justify-between gap-2 mb-2">
                      <div className="min-w-0 flex-1">
                        <p className="text-[11px] font-mono text-aegisaxiom-text-muted mb-0.5">
                          {fmtDate(r.tx_date)} · {r.txn_type || '—'}{' '}
                          {r.doc_num ? `· #${r.doc_num}` : ''}
                        </p>
                        <p className="text-[13px] font-medium text-aegisaxiom-text truncate">
                          {r.name || r.account_name || '—'}
                        </p>
                      </div>
                      <div className="text-right shrink-0">
                        {debit > 0 && (
                          <p className="text-[12px] font-mono tabular-nums text-aegisaxiom-text">
                            Dr {fmtCurrency(debit)}
                          </p>
                        )}
                        {credit > 0 && (
                          <p className="text-[12px] font-mono tabular-nums text-aegisaxiom-text">
                            Cr {fmtCurrency(credit)}
                          </p>
                        )}
                        {debit === 0 && credit === 0 && (
                          <span className="text-aegisaxiom-text-muted text-[11px]">—</span>
                        )}
                      </div>
                    </div>

                    {r.account_name && r.name !== r.account_name && (
                      <p className="text-[11px] text-aegisaxiom-text-secondary truncate mb-1.5">
                        {r.account_name}
                      </p>
                    )}

                    {r.memo && (
                      <p className="text-[11px] text-aegisaxiom-text-secondary line-clamp-2 mb-2">
                        {r.memo}
                      </p>
                    )}

                    {(r.location || r.class_name || r.sales1) && (
                      <div className="flex flex-wrap items-center gap-1.5 pt-2 border-t border-aegisaxiom-border">
                        {r.location && (
                          <span className="px-2 py-0.5 rounded-md text-[10px] bg-violet-500/15 text-violet-400">
                            {r.location}
                          </span>
                        )}
                        {r.class_name && (
                          <span className="px-2 py-0.5 rounded-md text-[10px] bg-aegisaxiom-elevated text-aegisaxiom-text-muted">
                            {r.class_name}
                          </span>
                        )}
                        {r.sales1 && (
                          <span className="px-2 py-0.5 rounded-md text-[10px] bg-aegisaxiom-elevated text-aegisaxiom-text-muted font-mono">
                            {r.sales1}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
        {data.rows.length === 0 && (
          <div className="hidden md:block px-5 py-10 text-center text-[13px] text-aegisaxiom-text-muted">
            No transactions match the current filters.
          </div>
        )}
        <Pagination total={data.total} page={page} setPage={setPage} />
      </div>
    </div>
  );
}
