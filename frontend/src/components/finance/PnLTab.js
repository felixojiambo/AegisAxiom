import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import HierarchicalReport from '@/components/finance/shared/HierarchicalReport';

const BASE = `${API_URL}/api/finance`;

export default function PnLTab() {
  const [data, setData] = useState(null);
  const [prior, setPrior] = useState(null);
  const [compare, setCompare] = useState(true);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  const refresh = async () => {
    try {
      const [c, p] = await Promise.all([
        api.get(`${BASE}/financials/pnl?period=current`),
        api.get(`${BASE}/financials/pnl?period=prior`),
      ]);
      if (!c.ok) {
        setLoadError(`Failed to load P&L (${c.status})`);
        return;
      }
      setData(await c.json());
      if (p.ok) setPrior(await p.json());
      setLoadError(null);
    } catch (err) {
      setLoadError(err?.message || 'Failed to load P&L');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); }, []);

  if (loading) return <div className="py-20 text-center text-sm text-aegisaxiom-text-muted" data-testid="loading-pnl">Loading…</div>;
  if (loadError) return (
    <div className="glass-card p-12 text-center" data-testid="pnl-load-error">
      <div className="text-[16px] font-semibold text-aegisaxiom-text mb-2">Couldn't load P&amp;L</div>
      <div className="text-[13px] text-aegisaxiom-text-secondary mb-6">{loadError}</div>
      <button onClick={refresh} className="btn-secondary inline-flex items-center gap-2 h-9 px-4 text-[13px]">Try again</button>
    </div>
  );

  return (
    <div className="space-y-6" data-testid="page-pnl">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-0.5">FINANCIALS</p>
          <h2 className="page-title">Profit &amp; Loss</h2>
          {data && <p className="text-sm text-aegisaxiom-text-secondary mt-1">Income statement as of {data.end_period}. Hierarchical view with prior-year comparison.</p>}
        </div>
        <label className="flex items-center gap-2 text-[13px] text-aegisaxiom-text-secondary cursor-pointer select-none">
          <input
            type="checkbox"
            checked={compare}
            onChange={(e) => setCompare(e.target.checked)}
            className="accent-violet-600"
            data-testid="toggle-compare-prior"
          />
          Compare prior year
        </label>
      </div>
      {data ? (
        <HierarchicalReport
          lineItems={data.line_items}
          columns={data.column_names}
          compareItems={compare ? prior?.line_items || [] : []}
          testid="pnl-table"
        />
      ) : (
        <div className="glass-card p-8 text-center text-sm text-aegisaxiom-text-muted">No data returned from server.</div>
      )}
    </div>
  );
}
