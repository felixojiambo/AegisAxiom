import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { CheckCircle2, Clock, Package, Truck, AlertCircle } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';

const API = API_URL;

// Procurement-status visual map. Mirrors the badge styling used by
// SolutionsDesignTab so the scope checklist surface matches the
// existing item rendering. New statuses on the backend fall through
// to the neutral pill.
const STATUS_BADGE = {
  pending_pricing: 'bg-amber-50 dark:bg-amber-500/20 text-amber-700 dark:text-amber-300 border-amber-500/30',
  priced: 'bg-blue-50 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300 border-blue-500/30',
  ordered: 'bg-violet-50 dark:bg-violet-500/20 text-violet-700 dark:text-violet-300 border-violet-500/30',
  delivered: 'bg-emerald-50 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 border-emerald-500/30',
};

function StatusBadge({ status }) {
  const label = status || 'pending_pricing';
  const cls = STATUS_BADGE[label] || 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted border-aegisaxiom-border';
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium border ${cls}`}>
      {label}
    </span>
  );
}

function CategoryGroup({ category, items, canMarkDelivered, onMarkDelivered, savingItemId }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg overflow-hidden">
      <div className="px-4 py-2 bg-aegisaxiom-elevated border-b border-aegisaxiom-border">
        <h4 className="text-sm font-semibold text-aegisaxiom-text capitalize">{category.replace('_', ' ')}</h4>
        <p className="text-[10px] text-aegisaxiom-text-muted">
          {items.filter((i) => i.procurement_status === 'delivered').length} of {items.length} delivered
        </p>
      </div>
      <table className="w-full text-xs">
        <thead className="bg-aegisaxiom-elevated text-aegisaxiom-text-secondary text-[10px] uppercase">
          <tr>
            <th className="text-left px-4 py-2">Item</th>
            <th className="text-left px-4 py-2">Qty</th>
            <th className="text-left px-4 py-2">Status</th>
            <th className="text-left px-4 py-2">Supplier</th>
            <th className="text-left px-4 py-2">Order ref / tracking</th>
            <th className="text-left px-4 py-2">Action</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-aegisaxiom-border">
          {items.map((item) => {
            const isDelivered = item.procurement_status === 'delivered';
            const isOrdered = item.procurement_status === 'ordered';
            return (
              <tr key={item.item_id} className="hover:bg-aegisaxiom-elevated/40">
                <td className="px-4 py-2 text-aegisaxiom-text">
                  <div className="font-medium">{item.product_name}</div>
                  {item.notes && (
                    <div className="text-[10px] text-aegisaxiom-text-muted mt-0.5">{item.notes}</div>
                  )}
                </td>
                <td className="px-4 py-2 text-aegisaxiom-text-secondary">
                  {item.quantity}{item.unit ? ` ${item.unit}` : ''}
                </td>
                <td className="px-4 py-2">
                  <StatusBadge status={item.procurement_status} />
                </td>
                <td className="px-4 py-2 text-aegisaxiom-text-secondary">
                  {item.supplier_name || '—'}
                </td>
                <td className="px-4 py-2 text-aegisaxiom-text-secondary">
                  {item.order_reference && <div className="font-mono">{item.order_reference}</div>}
                  {item.tracking_number && (
                    <div className="font-mono text-[10px] text-aegisaxiom-text-muted">{item.tracking_number}</div>
                  )}
                  {!item.order_reference && !item.tracking_number && '—'}
                </td>
                <td className="px-4 py-2">
                  {isDelivered ? (
                    <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400 text-[11px]">
                      <CheckCircle2 size={12} /> Delivered
                    </span>
                  ) : isOrdered && canMarkDelivered ? (
                    <button
                      onClick={() => onMarkDelivered(item)}
                      disabled={savingItemId === item.item_id}
                      className="px-2 py-1 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-[11px] rounded transition"
                      data-testid={`scope-checklist-mark-delivered-${item.item_id}`}
                    >
                      {savingItemId === item.item_id ? 'Saving…' : 'Mark delivered'}
                    </button>
                  ) : (
                    <span className="text-aegisaxiom-text-muted text-[11px]">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Read-only PMO scope checklist for a Field Services SOW. Exposes a
// single mutating action — Mark Delivered — which posts to the SOW
// item single-item delivered route. Pricing and ordered transitions
// are not available from this surface; PMO drives only the delivery
// confirmation step.
//
// Props:
//   projectId — optional. When provided, fetches all FS SOW
//               checklists for the project's opportunity through
//               /api/sales-ops/pmo/projects/{project_id}/checklist.
//   sowId     — optional. When provided (and projectId is not),
//               fetches a single SOW via
//               /api/sales-ops/pmo/sows/{sow_id}/checklist.
//   canMarkDelivered — defaults to true; the route enforces perms
//                      regardless, this just hides the button for
//                      view-only callers.
export default function SOWScopeChecklist({ projectId, sowId, canMarkDelivered = true }) {
  const [checklists, setChecklists] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [savingItemId, setSavingItemId] = useState(null);

  const loadChecklists = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      let res;
      if (projectId) {
        res = await api.get(`${API}/api/sales-ops/pmo/projects/${projectId}/checklist`);
      } else if (sowId) {
        res = await api.get(`${API}/api/sales-ops/pmo/sows/${sowId}/checklist`);
      } else {
        setChecklists([]);
        setLoading(false);
        return;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body?.detail?.message || 'Failed to load scope checklist');
        setChecklists([]);
        return;
      }
      const data = await res.json();
      if (projectId) {
        setChecklists(data.checklists || []);
      } else {
        setChecklists(data ? [data] : []);
      }
    } catch (e) {
      console.error('Error loading scope checklist:', e);
      setError('Failed to load scope checklist');
    } finally {
      setLoading(false);
    }
  }, [projectId, sowId]);

  useEffect(() => {
    loadChecklists();
  }, [loadChecklists]);

  const onMarkDelivered = useCallback(
    async (item, sowIdForItem) => {
      setSavingItemId(item.item_id);
      try {
        const res = await api.post(
          `${API}/api/sales-ops/procurement/sows/${sowIdForItem}/items/${item.item_id}/delivered`,
          {},
        );
        if (res.ok) {
          toast.success(`Marked ${item.product_name} delivered`);
          await loadChecklists();
        } else {
          const body = await res.json().catch(() => ({}));
          toast.error(body?.detail?.message || 'Failed to mark delivered');
        }
      } catch (e) {
        console.error('Mark delivered failed:', e);
        toast.error('Failed to mark delivered');
      } finally {
        setSavingItemId(null);
      }
    },
    [loadChecklists],
  );

  const summary = useMemo(() => {
    let total = 0;
    let delivered = 0;
    checklists.forEach((cl) => {
      total += cl.totals?.total_items || 0;
      delivered += cl.totals?.delivered_items || 0;
    });
    return { total, delivered };
  }, [checklists]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12" data-testid="scope-checklist-loading">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-violet-500"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-6 text-center" data-testid="scope-checklist-error">
        <AlertCircle className="w-8 h-8 text-amber-500 mx-auto mb-2" />
        <p className="text-sm text-aegisaxiom-text">{error}</p>
      </div>
    );
  }

  if (checklists.length === 0) {
    return (
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-8 text-center" data-testid="scope-checklist-empty">
        <Package className="w-10 h-10 text-aegisaxiom-text-muted mx-auto mb-2" />
        <p className="text-sm text-aegisaxiom-text-secondary">No Field Services SOW found for this scope.</p>
        <p className="text-xs text-aegisaxiom-text-muted mt-2">
          A scope checklist appears once a Field Services SOW is created on the linked opportunity.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="scope-checklist">
      <div className="flex items-center justify-between flex-wrap gap-3 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
        <div className="flex items-center gap-3">
          <Truck className="w-5 h-5 text-violet-500" />
          <div>
            <h3 className="text-sm font-semibold text-aegisaxiom-text">Scope checklist</h3>
            <p className="text-[11px] text-aegisaxiom-text-muted">
              Verify technician execution against the approved Field Services SOW.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 text-xs text-aegisaxiom-text-secondary">
          <span className="inline-flex items-center gap-1">
            <CheckCircle2 size={12} className="text-emerald-500" />
            {summary.delivered} / {summary.total} items delivered
          </span>
        </div>
      </div>

      {checklists.map((cl) => (
        <div key={cl.sow_id} className="space-y-3" data-testid={`scope-checklist-sow-${cl.sow_id}`}>
          <div className="flex items-center gap-2 text-xs text-aegisaxiom-text-secondary">
            <Clock size={12} />
            <span>SOW status:</span>
            <span className="font-medium text-aegisaxiom-text">{cl.status || 'unknown'}</span>
            <span className="text-aegisaxiom-text-muted">·</span>
            <span className="font-mono">{cl.sow_id}</span>
          </div>
          {cl.executive_summary && (
            <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-3 text-xs text-aegisaxiom-text">
              <div className="text-[10px] uppercase text-aegisaxiom-text-muted mb-1">Scope summary</div>
              <p className="whitespace-pre-wrap">{cl.executive_summary}</p>
            </div>
          )}
          {['materials', 'hardware', 'services', 'wholesale_mrr', 'other'].map((cat) => (
            <CategoryGroup
              key={cat}
              category={cat}
              items={cl.items_by_category?.[cat]}
              canMarkDelivered={canMarkDelivered}
              onMarkDelivered={(it) => onMarkDelivered(it, cl.sow_id)}
              savingItemId={savingItemId}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
