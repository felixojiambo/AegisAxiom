import React, { useEffect, useMemo, useState } from 'react';
import { Plus, Trash2, Search } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';

const BASE = `${API_URL}/api/finance`;

export default function CoAMappingTab() {
  const { hasPermission } = usePermissions();
  const [coa, setCoa] = useState([]);
  const [mappings, setMappings] = useState([]);
  const [search, setSearch] = useState('');
  const [kindFilter, setKindFilter] = useState('');
  const [draft, setDraft] = useState({
    internal_item: '',
    internal_kind: 'expense',
    qbo_account_id: '',
    qbo_account_name: '',
  });

  const canCreate = hasPermission('finance.coa_mapping.create');
  const canDelete = hasPermission('finance.coa_mapping.delete');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  const refresh = async () => {
    try {
      const [c, m] = await Promise.all([
        api.get(`${BASE}/operations/coa`),
        api.get(`${BASE}/coa-mappings`),
      ]);
      if (!c.ok && !m.ok) {
        setLoadError('Failed to load chart of accounts data');
        return;
      }
      if (c.ok) {
        const d = await c.json();
        setCoa(d.rows || []);
      }
      if (m.ok) {
        const d = await m.json();
        setMappings(d.rows || []);
      }
      setLoadError(null);
    } catch (err) {
      setLoadError(err?.message || 'Failed to load chart of accounts data');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const filteredCoa = useMemo(
    () =>
      coa.filter((a) => {
        if (kindFilter && a.classification !== kindFilter) return false;
        if (
          search &&
          !`${a.name} ${a.account_type} ${a.fully_qualified_name}`
            .toLowerCase()
            .includes(search.toLowerCase())
        )
          return false;
        return true;
      }),
    [coa, search, kindFilter],
  );

  const handleAdd = async () => {
    if (!draft.internal_item.trim() || !draft.qbo_account_id) {
      toast.error('Please fill internal item and QBO account');
      return;
    }
    const r = await api.post(`${BASE}/coa-mappings`, draft);
    if (r.ok) {
      setDraft({
        internal_item: '',
        internal_kind: 'expense',
        qbo_account_id: '',
        qbo_account_name: '',
      });
      toast.success('Mapping saved');
      refresh();
    } else {
      toast.error('Failed to save');
    }
  };

  const handleDelete = async (id) => {
    const r = await api.delete(`${BASE}/coa-mappings/${id}`);
    if (r.ok) {
      toast.success('Removed');
      refresh();
    }
  };

  const inputCls =
    'h-9 px-3 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface text-[13px] text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted outline-none focus:border-violet-500';

  if (loading)
    return (
      <div
        className="py-20 text-center text-sm text-aegisaxiom-text-muted"
        data-testid="loading-coa-mapping"
      >
        Loading…
      </div>
    );
  if (loadError)
    return (
      <div className="glass-card p-12 text-center" data-testid="coa-mapping-load-error">
        <div className="text-[16px] font-semibold text-aegisaxiom-text mb-2">
          Couldn't load chart of accounts
        </div>
        <div className="text-[13px] text-aegisaxiom-text-secondary mb-6">{loadError}</div>
        <button
          onClick={refresh}
          className="btn-secondary inline-flex items-center gap-2 h-9 px-4 text-[13px]"
        >
          Try again
        </button>
      </div>
    );

  return (
    <div className="space-y-6" data-testid="page-coa-mapping">
      <div>
        <p className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-0.5">
          OPERATIONS
        </p>
        <h2 className="page-title">Chart of Accounts Mapping</h2>
        <p className="text-sm text-aegisaxiom-text-secondary mt-1">
          Map internal items to QBO accounts. Saved mappings auto-apply on AR / AP draft generation.
        </p>
      </div>

      {canCreate && (
        <div className="glass-card p-6" data-testid="mapping-creator">
          <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-3">
            CREATE MAPPING
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
            <input
              data-testid="internal-item-input"
              value={draft.internal_item}
              onChange={(e) => setDraft({ ...draft, internal_item: e.target.value })}
              placeholder="Internal item (e.g. 'Senior consultant hours')"
              className={`lg:col-span-4 ${inputCls}`}
            />
            <select
              data-testid="kind-select"
              value={draft.internal_kind}
              onChange={(e) => setDraft({ ...draft, internal_kind: e.target.value })}
              className={`lg:col-span-2 ${inputCls}`}
            >
              <option value="income">Income</option>
              <option value="expense">Expense</option>
            </select>
            <select
              data-testid="qbo-account-select"
              value={draft.qbo_account_id}
              onChange={(e) => {
                const acc = coa.find((a) => a.account_id === e.target.value);
                setDraft({
                  ...draft,
                  qbo_account_id: e.target.value,
                  qbo_account_name: acc?.name || '',
                });
              }}
              className={`lg:col-span-4 ${inputCls}`}
            >
              <option value="">Select QBO account…</option>
              {coa
                .filter((a) =>
                  draft.internal_kind === 'income'
                    ? a.classification === 'Revenue'
                    : ['Expense', 'Asset', 'Liability'].includes(a.classification),
                )
                .map((a) => (
                  <option key={a.account_id} value={a.account_id}>
                    {a.name} · {a.classification}
                  </option>
                ))}
            </select>
            <button
              data-testid="add-mapping-btn"
              onClick={handleAdd}
              className="lg:col-span-2 h-9 btn-primary flex items-center justify-center gap-1.5 text-[13px]"
            >
              <Plus className="h-3.5 w-3.5" />
              Save mapping
            </button>
          </div>
        </div>
      )}

      {/* Existing mappings */}
      <div className="glass-card overflow-hidden" data-testid="mappings-table">
        <div className="px-5 py-3 border-b border-aegisaxiom-border">
          <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">
            SAVED MAPPINGS
          </div>
          <div className="text-[13px] text-aegisaxiom-text-secondary">
            {mappings.length} active mappings
          </div>
        </div>
        {mappings.length === 0 ? (
          <div className="px-5 py-12 text-center text-[13px] text-aegisaxiom-text-muted">
            No mappings yet — create one above.
          </div>
        ) : (
          <>
            {/* Desktop table */}
            <div className="hidden md:block overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="border-b border-aegisaxiom-border bg-aegisaxiom-elevated/40">
                    {['Internal Item', 'Kind', 'QBO Account', 'Auto-apply', ''].map((h, i) => (
                      <th
                        key={i}
                        className="px-5 py-2.5 text-left text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {mappings.map((m) => (
                    <tr
                      key={m.id}
                      className="border-b border-aegisaxiom-border/50 hover:bg-aegisaxiom-elevated/30"
                      data-testid={`mapping-row-${m.id}`}
                    >
                      <td className="px-5 py-2 text-aegisaxiom-text">{m.internal_item}</td>
                      <td className="px-5 py-2">
                        <span
                          className={`px-2 py-0.5 rounded-md text-[11px] ${m.internal_kind === 'income' ? 'bg-green-500/15 text-green-400' : 'bg-violet-500/15 text-violet-400'}`}
                        >
                          {m.internal_kind}
                        </span>
                      </td>
                      <td className="px-5 py-2 font-mono text-[12.5px] text-aegisaxiom-text">
                        {m.qbo_account_name}
                      </td>
                      <td className="px-5 py-2">
                        <span
                          className={`px-2 py-0.5 rounded-md text-[11px] ${m.auto_apply ? 'bg-green-500/15 text-green-400' : 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted'}`}
                        >
                          {m.auto_apply ? 'On' : 'Off'}
                        </span>
                      </td>
                      <td className="px-5 py-2 text-right">
                        {canDelete && (
                          <button
                            data-testid={`delete-mapping-${m.id}`}
                            onClick={() => handleDelete(m.id)}
                            className="p-1.5 rounded-md hover:bg-red-500/15 text-aegisaxiom-text-muted hover:text-red-400 transition-colors"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mobile card list */}
            <div className="md:hidden p-3 flex flex-col gap-2.5">
              {mappings.map((m) => (
                <div
                  key={m.id}
                  className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5"
                  data-testid={`mapping-card-${m.id}`}
                >
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <p className="text-[13px] font-medium text-aegisaxiom-text flex-1 min-w-0 truncate">
                      {m.internal_item}
                    </p>
                    {canDelete && (
                      <button
                        data-testid={`delete-mapping-mobile-${m.id}`}
                        onClick={() => handleDelete(m.id)}
                        className="p-1.5 -mr-1.5 -mt-1 rounded-md text-aegisaxiom-text-muted active:bg-red-500/15 active:text-red-400 transition-colors shrink-0"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                  <p className="text-[11px] font-mono text-aegisaxiom-text-secondary mb-2.5 break-all">
                    {m.qbo_account_name}
                  </p>
                  <div className="flex items-center gap-2 pt-2 border-t border-aegisaxiom-border">
                    <span
                      className={`px-2 py-0.5 rounded-md text-[10px] ${m.internal_kind === 'income' ? 'bg-green-500/15 text-green-400' : 'bg-violet-500/15 text-violet-400'}`}
                    >
                      {m.internal_kind}
                    </span>
                    <span
                      className={`px-2 py-0.5 rounded-md text-[10px] ${m.auto_apply ? 'bg-green-500/15 text-green-400' : 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted'}`}
                    >
                      Auto-apply {m.auto_apply ? 'on' : 'off'}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* CoA browser */}
      <div className="glass-card overflow-hidden" data-testid="coa-browser">
        <div className="px-5 py-3 border-b border-aegisaxiom-border flex flex-col md:flex-row md:items-center md:justify-between gap-3">
          <div>
            <div className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted">
              QBO CHART OF ACCOUNTS
            </div>
            <div className="text-[13px] text-aegisaxiom-text-secondary">
              {filteredCoa.length} of {coa.length} accounts
            </div>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <select
              data-testid="kind-filter"
              value={kindFilter}
              onChange={(e) => setKindFilter(e.target.value)}
              className="h-8 px-3 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface text-[13px] text-aegisaxiom-text"
            >
              <option value="">All classes</option>
              {['Asset', 'Liability', 'Equity', 'Revenue', 'Expense'].map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
            <div className="flex items-center gap-2 px-3 h-8 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface flex-1 md:flex-none md:w-[260px]">
              <Search className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
              <input
                data-testid="coa-search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search accounts…"
                className="bg-transparent outline-none text-[13px] flex-1 text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted"
              />
            </div>
          </div>
        </div>
        {/* Desktop table */}
        <div className="hidden md:block max-h-[420px] overflow-y-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-aegisaxiom-border bg-aegisaxiom-elevated/40 sticky top-0">
                {['Account', 'Type', 'Classification', 'Active'].map((h, i) => (
                  <th
                    key={h}
                    className={`px-5 py-2.5 text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted ${i === 3 ? 'text-right' : 'text-left'}`}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filteredCoa.map((a) => (
                <tr
                  key={a.account_id}
                  className="border-b border-aegisaxiom-border/50 hover:bg-aegisaxiom-elevated/30"
                >
                  <td className="px-5 py-2 text-aegisaxiom-text">{a.name}</td>
                  <td className="px-5 py-2 text-aegisaxiom-text-secondary">{a.account_type}</td>
                  <td className="px-5 py-2 text-aegisaxiom-text-secondary">{a.classification}</td>
                  <td className="px-5 py-2 text-right">
                    {a.active ? (
                      <span className="px-2 py-0.5 rounded-md text-[11px] bg-green-500/15 text-green-400">
                        Active
                      </span>
                    ) : (
                      <span className="px-2 py-0.5 rounded-md text-[11px] bg-aegisaxiom-elevated text-aegisaxiom-text-muted">
                        Inactive
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Mobile card list */}
        <div className="md:hidden max-h-[480px] overflow-y-auto">
          {filteredCoa.length === 0 ? (
            <div className="px-5 py-10 text-center text-[13px] text-aegisaxiom-text-muted">
              No accounts match.
            </div>
          ) : (
            <div className="p-3 flex flex-col gap-2">
              {filteredCoa.map((a) => (
                <div
                  key={a.account_id}
                  className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3"
                  data-testid={`coa-card-${a.account_id}`}
                >
                  <div className="flex items-start justify-between gap-2 mb-1.5">
                    <p className="text-[13px] font-medium text-aegisaxiom-text flex-1 min-w-0 truncate">
                      {a.name}
                    </p>
                    {a.active ? (
                      <span className="shrink-0 px-2 py-0.5 rounded-md text-[10px] bg-green-500/15 text-green-400">
                        Active
                      </span>
                    ) : (
                      <span className="shrink-0 px-2 py-0.5 rounded-md text-[10px] bg-aegisaxiom-elevated text-aegisaxiom-text-muted">
                        Inactive
                      </span>
                    )}
                  </div>
                  <div className="flex items-center justify-between gap-2 text-[11px] text-aegisaxiom-text-secondary">
                    <span className="truncate">{a.account_type}</span>
                    <span className="shrink-0">{a.classification}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
