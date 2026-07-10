import React from 'react';
import { CheckCircle, X } from 'lucide-react';

const fmt = (n) =>
  `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function ImportDealModal({
  importDeal,
  importMode,
  setImportMode,
  importForm,
  setImportForm,
  contracts,
  TYPE_CONFIG,
  onImport,
  onCreateNew,
  onClose,
  loading,
}) {
  if (!importDeal) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" />
      <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-4xl max-h-[90vh] overflow-y-auto">
        <div className="sticky top-0 bg-aegisaxiom-surface backdrop-blur border-b border-aegisaxiom-border p-4">
          <div className="flex justify-between items-start">
            <div>
              <h2 className="text-sm font-semibold text-aegisaxiom-text flex items-center gap-2">
                <CheckCircle size={16} className="text-emerald-600 dark:text-emerald-400" /> Accept & Attach Deal
              </h2>
              <p className="text-xs text-aegisaxiom-text-secondary mt-1">
                <span className="text-aegisaxiom-text font-medium">
                  {importDeal.name || importDeal.deal_name}
                </span>
                {importDeal.amount > 0 && <span className="ml-2">{fmt(importDeal.amount)}</span>}
                <span className="ml-2 text-aegisaxiom-text-muted">{importDeal.account_name}</span>
              </p>
            </div>
            <button onClick={onClose} className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text">
              <X size={18} />
            </button>
          </div>
        </div>

        <div className="p-4 space-y-4">
          {/* Mode Toggle */}
          <div className="flex gap-2">
            <button
              onClick={() => setImportMode('existing')}
              className={`flex-1 px-3 py-2 rounded-lg border text-xs transition ${
                importMode === 'existing'
                  ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border-violet-500/30'
                  : 'border-aegisaxiom-border text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover'
              }`}
            >
              Attach to Existing Contract
            </button>
            <button
              onClick={onCreateNew}
              className="flex-1 px-3 py-2 rounded-lg border text-xs transition bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border-emerald-500/30 hover:bg-emerald-600/30"
            >
              Create New Contract
            </button>
          </div>

          {/* Existing Contract Mode */}
          {importMode === 'existing' && (
            <div className="space-y-3">
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Select Contract *</label>
                <select
                  value={importForm.contract_id}
                  onChange={(e) => setImportForm((f) => ({ ...f, contract_id: e.target.value }))}
                  className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text focus:border-violet-500 outline-none"
                >
                  <option value="">Choose a contract...</option>
                  {contracts
                    .filter((c) => c.status === 'active')
                    .map((c) => (
                      <option key={c.contract_id} value={c.contract_id}>
                        {c.name} ({TYPE_CONFIG[c.contract_type]?.label || c.contract_type})
                        {c.account_name ? ` — ${c.account_name}` : ''}
                      </option>
                    ))}
                </select>
              </div>

              {/* Show BOH hour adjustment if selected contract is BOH */}
              {importForm.contract_id &&
                contracts.find((c) => c.contract_id === importForm.contract_id)?.contract_type ===
                  'boh' && (
                  <div className="bg-blue-900/15 border border-blue-700/30 rounded-lg p-3 space-y-2">
                    <p className="text-xs text-blue-700 dark:text-blue-300 font-medium">BOH Hour Adjustment</p>
                    <p className="text-[10px] text-aegisaxiom-text-muted">
                      Optionally add or remove hours from this block when attaching this deal.
                    </p>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="text-[10px] text-aegisaxiom-text-muted block mb-0.5">
                          Hours (+/-)
                        </label>
                        <input
                          type="number"
                          step="0.25"
                          value={importForm.hour_adjustment}
                          placeholder="e.g. 40"
                          onChange={(e) =>
                            setImportForm((f) => ({ ...f, hour_adjustment: e.target.value }))
                          }
                          className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none"
                        />
                      </div>
                      <div>
                        <label className="text-[10px] text-aegisaxiom-text-muted block mb-0.5">Note</label>
                        <input
                          value={importForm.note}
                          placeholder="Adjustment reason"
                          onChange={(e) => setImportForm((f) => ({ ...f, note: e.target.value }))}
                          className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none"
                        />
                      </div>
                    </div>
                  </div>
                )}
            </div>
          )}

          {/* Action Buttons */}
          <div className="flex gap-2 pt-2">
            <button
              onClick={onImport}
              disabled={loading}
              className="flex-1 bg-emerald-600 hover:bg-emerald-500 text-white text-xs py-2 rounded-md transition disabled:opacity-50 flex items-center justify-center gap-1.5"
            >
              {loading ? (
                'Processing...'
              ) : (
                <>
                  <CheckCircle size={14} />{' '}
                  {importMode === 'existing' ? 'Attach Deal' : 'Create Contract & Attach'}
                </>
              )}
            </button>
            <button
              onClick={onClose}
              className="px-4 text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
