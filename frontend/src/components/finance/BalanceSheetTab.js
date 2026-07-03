import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import HierarchicalReport from '@/components/finance/shared/HierarchicalReport';

const BASE = `${API_URL}/api/finance`;

export default function BalanceSheetTab() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  const refresh = async () => {
    try {
      const r = await api.get(`${BASE}/financials/balance-sheet`);
      if (!r.ok) {
        setLoadError(`Failed to load balance sheet (${r.status})`);
        return;
      }
      setData(await r.json());
      setLoadError(null);
    } catch (err) {
      setLoadError(err?.message || 'Failed to load balance sheet');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); }, []);

  if (loading) return <div className="py-20 text-center text-sm text-aegisaxiom-text-muted" data-testid="loading-balance-sheet">Loading…</div>;
  if (loadError) return (
    <div className="glass-card p-12 text-center" data-testid="balance-sheet-load-error">
      <div className="text-[16px] font-semibold text-aegisaxiom-text mb-2">Couldn't load balance sheet</div>
      <div className="text-[13px] text-aegisaxiom-text-secondary mb-6">{loadError}</div>
      <button onClick={refresh} className="btn-secondary inline-flex items-center gap-2 h-9 px-4 text-[13px]">Try again</button>
    </div>
  );
  if (!data) return null;

  return (
    <div className="space-y-6" data-testid="page-balance-sheet">
      <div>
        <p className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-0.5">FINANCIALS</p>
        <h2 className="page-title">Balance Sheet</h2>
        <p className="text-sm text-aegisaxiom-text-secondary mt-1">Assets, Liabilities &amp; Equity as of {data.end_period}.</p>
      </div>
      <HierarchicalReport lineItems={data.line_items} columns={data.column_names} testid="bs-table" />
    </div>
  );
}
