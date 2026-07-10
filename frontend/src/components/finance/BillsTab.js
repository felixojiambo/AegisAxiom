import React, { useEffect, useState } from 'react';
import { Search } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { fmtCurrency, fmtDate } from '@/lib/financeFormat';
import SyncStatusBadge from '@/components/finance/shared/SyncStatusBadge';

const BASE = `${API_URL}/api/finance`;
const LIMIT = 50;

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
        of <span className="font-mono">{total}</span>
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

export default function BillsTab() {
  const [data, setData] = useState({ rows: [], total: 0 });
  const [search, setSearch] = useState('');
  const [openOnly, setOpenOnly] = useState(false);
  const [location, setLocation] = useState('');
  const [className, setClassName] = useState('');
  const [locations, setLocations] = useState([]);
  const [classes, setClasses] = useState([]);
  const [page, setPage] = useState(0);

  useEffect(() => {
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
    if (search) params.set('search', search);
    if (openOnly) params.set('open_only', 'true');
    if (location) params.set('location', location);
    if (className) params.set('class_name', className);
    params.set('limit', String(LIMIT));
    params.set('skip', String(page * LIMIT));
    api.get(`${BASE}/operations/bills?${params.toString()}`).then(async (r) => {
      if (r.ok) setData(await r.json());
    });
  }, [search, openOnly, location, className, page]);

  const selectCls =
    'h-9 px-3 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface text-[13px] text-aegisaxiom-text';

  return (
    <div className="space-y-6" data-testid="page-bills">
      <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-3 md:gap-4 md:flex-wrap">
        <div>
          <p className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-0.5">
            OPERATIONS
          </p>
          <h2 className="page-title">Bills</h2>
          <p className="text-sm text-aegisaxiom-text-secondary mt-1">
            Accounts Payable bills · {data.total} total in the data lake.
          </p>
        </div>
        <div className="grid grid-cols-2 md:flex md:items-center gap-2 md:flex-wrap">
          <label className="col-span-2 md:col-auto flex items-center gap-2 text-[13px] text-aegisaxiom-text-secondary cursor-pointer select-none px-3 h-9 md:h-auto md:px-0 rounded-lg border border-aegisaxiom-border md:border-0 bg-aegisaxiom-surface md:bg-transparent">
            <input
              type="checkbox"
              checked={openOnly}
              onChange={(e) => {
                setOpenOnly(e.target.checked);
                setPage(0);
              }}
              className="accent-violet-600"
              data-testid="open-only-toggle"
            />
            Open only
          </label>
          <select
            data-testid="bill-location-filter"
            value={location}
            onChange={(e) => {
              setLocation(e.target.value);
              setPage(0);
            }}
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
            data-testid="bill-class-filter"
            value={className}
            onChange={(e) => {
              setClassName(e.target.value);
              setPage(0);
            }}
            className={`${selectCls} min-w-0`}
          >
            <option value="">All classes</option>
            {classes.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
          <div className="col-span-2 md:col-auto flex items-center gap-2 px-3 h-9 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface w-full md:w-[280px]">
            <Search className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
            <input
              data-testid="bill-search"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(0);
              }}
              placeholder="Search vendor / doc #"
              className="bg-transparent outline-none text-[13px] flex-1 text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted"
            />
          </div>
        </div>
      </div>

      <div className="glass-card overflow-hidden">
        {/* Desktop table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-aegisaxiom-border bg-aegisaxiom-elevated/40">
                {[
                  'Txn Date',
                  'Doc #',
                  'Vendor',
                  'Location',
                  'Class',
                  'Due',
                  'Amount',
                  'Balance',
                  'Memo',
                  'Sync',
                ].map((h, i) => (
                  <th
                    key={h}
                    className={`px-5 py-2.5 text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted ${[6, 7].includes(i) ? 'text-right' : 'text-left'}`}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r, i) => (
                <tr
                  key={`${r.id}-${i}`}
                  className="border-b border-aegisaxiom-border/50 hover:bg-aegisaxiom-elevated/30"
                  data-testid={`bill-row-${r.id}`}
                >
                  <td className="px-5 py-2 font-mono text-aegisaxiom-text">{fmtDate(r.txn_date)}</td>
                  <td className="px-5 py-2 text-aegisaxiom-text">{r.doc_num || '—'}</td>
                  <td className="px-5 py-2 text-aegisaxiom-text">{r.vendor || '—'}</td>
                  <td className="px-5 py-2">
                    {r.location ? (
                      <span className="px-2 py-0.5 rounded-md text-[11px] bg-violet-500/15 text-violet-400">
                        {r.location}
                      </span>
                    ) : (
                      <span className="text-aegisaxiom-text-muted">—</span>
                    )}
                  </td>
                  <td className="px-5 py-2">
                    {r.class_name ? (
                      <span className="px-2 py-0.5 rounded-md text-[11px] bg-aegisaxiom-elevated text-aegisaxiom-text-muted">
                        {r.class_name}
                      </span>
                    ) : (
                      <span className="text-aegisaxiom-text-muted">—</span>
                    )}
                  </td>
                  <td className="px-5 py-2 font-mono text-aegisaxiom-text-secondary">
                    {fmtDate(r.due_date)}
                  </td>
                  <td className="px-5 py-2 text-right font-mono text-aegisaxiom-text">
                    {fmtCurrency(r.amount)}
                  </td>
                  <td
                    className={`px-5 py-2 text-right font-mono font-medium ${r.balance > 0 ? 'text-aegisaxiom-text' : 'text-aegisaxiom-text-muted'}`}
                  >
                    {fmtCurrency(r.balance)}
                  </td>
                  <td
                    className="px-5 py-2 max-w-[280px] truncate text-aegisaxiom-text-secondary"
                    title={r.memo || ''}
                  >
                    {r.memo || ''}
                  </td>
                  <td className="px-5 py-2">
                    <SyncStatusBadge status={r.sync_status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Mobile card list */}
        <div className="md:hidden">
          {data.rows.length === 0 ? (
            <div className="px-5 py-10 text-center text-[13px] text-aegisaxiom-text-muted">
              No bills match the filters.
            </div>
          ) : (
            <div className="p-3 flex flex-col gap-2.5">
              {data.rows.map((r, i) => (
                <div
                  key={`${r.id}-${i}`}
                  className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5"
                  data-testid={`bill-card-${r.id}`}
                >
                  <div className="flex items-start justify-between gap-2 mb-1.5">
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] font-medium text-aegisaxiom-text truncate">
                        {r.vendor || '—'}
                      </p>
                      <p className="text-[11px] text-aegisaxiom-text-secondary mt-0.5 font-mono">
                        #{r.doc_num || '—'}
                      </p>
                    </div>
                    <SyncStatusBadge status={r.sync_status} />
                  </div>

                  <p
                    className={`text-[16px] font-bold mb-2.5 font-mono ${r.balance > 0 ? 'text-aegisaxiom-text' : 'text-aegisaxiom-text-muted'}`}
                  >
                    {fmtCurrency(r.balance)}
                  </p>

                  <div className="grid grid-cols-2 gap-2 mb-2.5 text-[11px]">
                    <div>
                      <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                        Txn Date
                      </p>
                      <p className="text-aegisaxiom-text-secondary font-mono">{fmtDate(r.txn_date)}</p>
                    </div>
                    <div>
                      <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                        Due
                      </p>
                      <p className="text-aegisaxiom-text-secondary font-mono">{fmtDate(r.due_date)}</p>
                    </div>
                    <div>
                      <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                        Amount
                      </p>
                      <p className="text-aegisaxiom-text-secondary font-mono">{fmtCurrency(r.amount)}</p>
                    </div>
                  </div>

                  {(r.location || r.class_name) && (
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
                    </div>
                  )}
                  {r.memo && (
                    <p className="text-[11px] text-aegisaxiom-text-secondary mt-2 pt-2 border-t border-aegisaxiom-border line-clamp-2">
                      {r.memo}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
        {data.rows.length === 0 && (
          <div className="hidden md:block px-5 py-10 text-center text-[13px] text-aegisaxiom-text-muted">
            No bills match the filters.
          </div>
        )}
        <Pagination total={data.total} page={page} setPage={setPage} />
      </div>
    </div>
  );
}
