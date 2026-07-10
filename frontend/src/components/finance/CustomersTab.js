import React, { useEffect, useState } from 'react';
import { Search } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { fmtCurrency } from '@/lib/financeFormat';

const BASE = `${API_URL}/api/finance`;

export default function CustomersTab({ onSelectCustomer }) {
  const [data, setData] = useState({ rows: [], total: 0 });
  const [search, setSearch] = useState('');

  useEffect(() => {
    const t = setTimeout(() => {
      api
        .get(`${BASE}/lists/customers?search=${encodeURIComponent(search)}&limit=300`)
        .then(async (r) => {
          if (r.ok) setData(await r.json());
        });
    }, 200);
    return () => clearTimeout(t);
  }, [search]);

  return (
    <div className="space-y-6" data-testid="page-customers">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 md:gap-4 md:flex-wrap">
        <div>
          <p className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-0.5">
            LISTS
          </p>
          <h2 className="page-title">Customers</h2>
          <p className="text-sm text-aegisaxiom-text-secondary mt-1">
            {data.total} customers in the data lake. Sorted by outstanding balance.
          </p>
        </div>
        <div className="flex items-center gap-2 px-3 h-9 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface w-full md:w-[280px]">
          <Search className="h-3.5 w-3.5 text-aegisaxiom-text-muted" />
          <input
            data-testid="cust-search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search customers…"
            className="bg-transparent outline-none text-[13px] flex-1 text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted"
          />
        </div>
      </div>

      <div className="glass-card overflow-hidden">
        {/* Desktop table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-aegisaxiom-border bg-aegisaxiom-elevated/40">
                {['Customer', 'Open Balance', 'YTD Income', 'YTD Expense', 'Net Income'].map(
                  (h, i) => (
                    <th
                      key={h}
                      className={`px-5 py-2.5 text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted ${i === 0 ? 'text-left' : 'text-right'}`}
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((c) => (
                <tr
                  key={c.customer_id}
                  onClick={() => onSelectCustomer && onSelectCustomer(c.customer_id)}
                  className="border-b border-aegisaxiom-border/50 hover:bg-aegisaxiom-elevated/30 cursor-pointer"
                  data-testid={`customer-row-${c.customer_id}`}
                >
                  <td className="px-5 py-2 text-aegisaxiom-text hover:underline underline-offset-2">
                    {c.name}
                  </td>
                  <td
                    className={`px-5 py-2 text-right font-mono font-medium ${c.balance > 0 ? 'text-aegisaxiom-text' : 'text-aegisaxiom-text-muted'}`}
                  >
                    {fmtCurrency(c.balance)}
                  </td>
                  <td className="px-5 py-2 text-right font-mono text-aegisaxiom-text">
                    {fmtCurrency(c.income)}
                  </td>
                  <td className="px-5 py-2 text-right font-mono text-aegisaxiom-text-secondary">
                    {fmtCurrency(c.expenses)}
                  </td>
                  <td className="px-5 py-2 text-right font-mono text-aegisaxiom-text">
                    {fmtCurrency(c.net_income)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.rows.length === 0 && (
            <div className="px-5 py-10 text-center text-[13px] text-aegisaxiom-text-muted">
              No customers found.
            </div>
          )}
        </div>

        {/* Mobile card list */}
        <div className="md:hidden">
          {data.rows.length === 0 ? (
            <div className="px-5 py-10 text-center text-[13px] text-aegisaxiom-text-muted">
              No customers found.
            </div>
          ) : (
            <div className="p-3 flex flex-col gap-2.5">
              {data.rows.map((c) => (
                <button
                  key={c.customer_id}
                  type="button"
                  onClick={() => onSelectCustomer && onSelectCustomer(c.customer_id)}
                  className="w-full text-left bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5 active:bg-aegisaxiom-elevated transition-colors"
                  data-testid={`customer-card-${c.customer_id}`}
                >
                  <div className="flex items-start justify-between gap-2 mb-2.5">
                    <p className="text-[13px] font-medium text-aegisaxiom-text truncate">{c.name}</p>
                    <span
                      className={`shrink-0 text-[13px] font-mono font-medium ${c.balance > 0 ? 'text-aegisaxiom-text' : 'text-aegisaxiom-text-muted'}`}
                    >
                      {fmtCurrency(c.balance)}
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-2 pt-2 border-t border-aegisaxiom-border">
                    <div>
                      <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                        YTD Income
                      </p>
                      <p className="text-[12px] font-mono text-aegisaxiom-text">
                        {fmtCurrency(c.income)}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                        Expense
                      </p>
                      <p className="text-[12px] font-mono text-aegisaxiom-text-secondary">
                        {fmtCurrency(c.expenses)}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-0.5">
                        Net
                      </p>
                      <p className="text-[12px] font-mono text-aegisaxiom-text">
                        {fmtCurrency(c.net_income)}
                      </p>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
