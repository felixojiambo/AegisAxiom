import React from 'react';
import {
  Plus,
  X,
  DollarSign,
  Clock,
  Briefcase,
  CheckCircle,
  AlertTriangle,
  Link2,
  Shield,
  FileCheck,
} from 'lucide-react';
import { useProducts } from './hooks/useProducts';
import WholesaleProductsSection from './sections/WholesaleProductsSection';
import ServiceCategorySelector from './sections/ServiceCategorySelector';
import ContractTypeSelector from './sections/ContractTypeSelector';

// Service Categories & Type Configurations — synced with Contracts.js
const SERVICE_CATEGORIES = {
  field_services: {
    label: 'Field Services',
    icon: Clock,
    types: ['boh', 'boa', 'fixed_fee'],
  },
  professional_services: {
    label: 'Professional Services',
    icon: Briefcase,
    types: ['boh', 'boa', 'fixed_fee', 'monthly'],
  },
  wholesale_mrr: {
    label: 'Wholesale MRR',
    icon: DollarSign,
    types: [], // Legacy buttons removed - use product search with filter
  },
  // referral_mrr removed - referral deals bypass contract module
};

const TYPE_CONFIG = {
  boh: { label: 'Block of Hours', icon: Clock, color: 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300' },
  boa: { label: 'Bill on Actuals', icon: Briefcase, color: 'bg-purple-600/20 text-purple-300' },
  fixed_fee: { label: 'Fixed Fee', icon: DollarSign, color: 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300' },
  monthly: { label: 'Monthly', icon: Clock, color: 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300' },
};

/**
 * Shared Contract Form Fields Component
 * Used in both:
 * 1. Contract Management → New Contract modal
 * 2. Deal Import → Create New Contract modal
 */
export default function ContractFormFields({
  form,
  setForm,
  accounts = [],
  users = [],
  defaultResourceTypes = [],
  showServiceCategory = true, // Only show for new contracts
  compact = false, // Use compact layout for deal import
  isAdmin = false, // Show test data filter for admins
  deals = [], // All deals for linking
  showDealLinking = false, // Whether to show deal linking section
}) {
  const [showTestDataOnly, setShowTestDataOnly] = React.useState(false);

  // Use custom hook for product management
  const {
    products,
    productSearch,
    setProductSearch,
    productTypeFilter,
    setProductTypeFilter,
    loadingProducts,
    loadProducts,
    getFilteredProducts,
  } = useProducts(form.service_category);

  const addResourceType = () => {
    setForm((f) => ({
      ...f,
      resource_types: [
        ...(f.resource_types || []),
        { id: `rt_${Date.now()}`, name: '', sell_rate: '', hours_purchased: '', description: '' },
      ],
    }));
  };

  const updateResourceType = (idx, field, value) => {
    setForm((f) => {
      const updated = [...(f.resource_types || [])];
      updated[idx] = { ...updated[idx], [field]: value };
      return { ...f, resource_types: updated };
    });
  };

  const removeResourceType = (idx) => {
    setForm((f) => ({
      ...f,
      resource_types: (f.resource_types || []).filter((_, i) => i !== idx),
    }));
  };

  const addResource = () => {
    setForm((f) => ({
      ...f,
      resources: [
        ...(f.resources || []),
        {
          user_id: '',
          resource_type_id: '',
          cost_rate: '',
          role: '',
          allocation_percentage: 0,
          hours_allocated: 0,
          allocation_mode: 'percent',
        },
      ],
    }));
  };

  const updateResource = (idx, field, value) => {
    setForm((f) => {
      const updated = [...(f.resources || [])];
      updated[idx] = { ...updated[idx], [field]: value };

      // Auto-fill cost rate when user is selected
      if (field === 'user_id' && value) {
        const selectedUser = users.find((u) => u.user_id === value);
        if (selectedUser && updated[idx].resource_type_id) {
          const resourceTypeName = (f.resource_types || []).find(
            (rt) => rt.id === updated[idx].resource_type_id,
          )?.name;
          if (resourceTypeName) {
            const matchingRT = (selectedUser.resource_type_assignments || []).find(
              (assignment) => assignment.resource_type_name === resourceTypeName,
            );
            if (matchingRT) {
              updated[idx].cost_rate = matchingRT.cost_rate;
            }
          }
        }
      }

      return { ...f, resources: updated };
    });
  };

  const removeResource = (idx) => {
    setForm((f) => ({ ...f, resources: (f.resources || []).filter((_, i) => i !== idx) }));
  };

  // Product Management Functions
  const addProduct = () => {
    setForm((f) => ({
      ...f,
      products: [
        ...(f.products || []),
        {
          product_id: '',
          product_name: '',
          sku: '',
          pricing_tier: 'monthly', // monthly, quarterly, annual
          cost_price: 0, // locked
          sell_price: 0, // editable
          quantity: 1,
        },
      ],
    }));
  };

  const updateProduct = (idx, field, value) => {
    setForm((f) => {
      const updated = [...(f.products || [])];
      updated[idx] = { ...updated[idx], [field]: value };

      // Auto-fill pricing when product is selected
      if (field === 'product_id' && value) {
        const selectedProduct = products.find((p) => p.product_id === value);
        if (selectedProduct) {
          const tier = updated[idx].pricing_tier || 'monthly';
          const costPrice = selectedProduct.cost_monthly || 0;
          let sellPrice = 0;

          // Get retail pricing based on tier
          if (tier === 'monthly') {
            sellPrice = selectedProduct.retail_pricing?.monthly || 0;
          } else if (tier === 'quarterly') {
            sellPrice = selectedProduct.retail_pricing?.quarterly || 0;
          } else if (tier === 'annual') {
            sellPrice = selectedProduct.retail_pricing?.annual || 0;
          }

          updated[idx] = {
            ...updated[idx],
            product_name: selectedProduct.name,
            sku: selectedProduct.sku,
            cost_price: costPrice,
            sell_price: sellPrice,
          };
        }
      }

      // Update pricing when tier changes
      if (field === 'pricing_tier' && updated[idx].product_id) {
        const selectedProduct = products.find((p) => p.product_id === updated[idx].product_id);
        if (selectedProduct) {
          const costPrice = selectedProduct.cost_monthly || 0;
          let sellPrice = 0;

          if (value === 'monthly') {
            sellPrice = selectedProduct.retail_pricing?.monthly || 0;
          } else if (value === 'quarterly') {
            sellPrice = selectedProduct.retail_pricing?.quarterly || 0;
          } else if (value === 'annual') {
            sellPrice = selectedProduct.retail_pricing?.annual || 0;
          }

          updated[idx].cost_price = costPrice;
          updated[idx].sell_price = sellPrice;
        }
      }

      return { ...f, products: updated };
    });
  };

  const removeProduct = (idx) => {
    setForm((f) => ({ ...f, products: (f.products || []).filter((_, i) => i !== idx) }));
  };

  const labelClass = compact ? 'text-[10px] text-aegisaxiom-text-muted' : 'text-xs text-aegisaxiom-text-secondary';
  const inputClass = compact
    ? 'w-full px-2 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none'
    : 'input-field text-sm';

  return (
    <div className="space-y-3">
      {/* Contract Name */}
      <div>
        <label className={`${labelClass} block mb-1`}>
          Contract Name <span className="text-red-600 dark:text-red-400">*</span>
        </label>
        <input
          name="name"
          value={form.name || ''}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          placeholder="e.g., 2024 Q1 Security Audit"
          className={inputClass}
          required
        />
      </div>

      {/* Account Selection - Only show if account not already set (e.g., from deal context) */}
      {accounts.length > 0 && !form.account_id && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className={labelClass}>
              Account/Client <span className="text-red-600 dark:text-red-400">*</span>
            </label>
            {isAdmin && (
              <label className="flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400 cursor-pointer">
                <input
                  type="checkbox"
                  checked={showTestDataOnly}
                  onChange={(e) => setShowTestDataOnly(e.target.checked)}
                  className="w-3 h-3 rounded border-aegisaxiom-border-hover bg-aegisaxiom-elevated text-amber-500 focus:ring-0 focus:ring-offset-0"
                />
                <span className="text-[10px]">Test accounts only</span>
              </label>
            )}
          </div>
          <div className="relative">
            <input
              type="text"
              placeholder="Search accounts..."
              value={form._accountSearch || ''}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  _accountSearch: e.target.value,
                  _showAccountDropdown: true,
                }))
              }
              onFocus={() => setForm((f) => ({ ...f, _showAccountDropdown: true }))}
              className={inputClass}
            />
            {form._showAccountDropdown && (
              <>
                <div
                  className="fixed inset-0 z-10"
                  onClick={() => setForm((f) => ({ ...f, _showAccountDropdown: false }))}
                />
                <div className="absolute z-20 mt-1 w-full max-h-60 overflow-y-auto bg-aegisaxiom-elevated border border-aegisaxiom-border rounded shadow-lg">
                  {(() => {
                    let filtered = accounts.filter((a) => {
                      if (!form._accountSearch) return true;
                      const search = form._accountSearch.toLowerCase();
                      return a.name.toLowerCase().includes(search);
                    });

                    // Apply test data filter
                    if (showTestDataOnly) {
                      filtered = filtered.filter((a) => a.is_test_data === true);
                    }

                    const limited = filtered.slice(0, 50);

                    return (
                      <>
                        {limited.map((a) => (
                          <div
                            key={a.account_id}
                            onClick={() => {
                              setForm((f) => ({
                                ...f,
                                account_id: a.account_id,
                                account_name: a.name,
                                _accountSearch: a.name,
                                _showAccountDropdown: false,
                              }));
                            }}
                            className={`px-3 py-2 text-xs cursor-pointer hover:bg-aegisaxiom-elevated transition ${
                              form.account_id === a.account_id
                                ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300'
                                : 'text-aegisaxiom-text'
                            }`}
                          >
                            <div className="flex items-center gap-2">
                              <span className="font-medium flex-1">{a.name}</span>
                              {a.is_test_data && (
                                <span className="px-1.5 py-0.5 bg-amber-50 dark:bg-amber-600/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 rounded text-[9px] font-medium">
                                  TEST
                                </span>
                              )}
                            </div>
                          </div>
                        ))}
                        {filtered.length === 0 && (
                          <div className="px-3 py-4 text-xs text-aegisaxiom-text-muted text-center">
                            No accounts found
                          </div>
                        )}
                        {filtered.length > 50 && (
                          <div className="px-3 py-2 text-xs text-amber-600 dark:text-amber-400 border-t border-aegisaxiom-border text-center">
                            Showing 50 of {filtered.length} results. Type to narrow search.
                          </div>
                        )}
                      </>
                    );
                  })()}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Link Deals Section - Compact dropdown selector showing only selected deals */}
      {showDealLinking && deals.length > 0 && form.account_id && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-xs text-aegisaxiom-text-secondary">
              Link Deals
              <span className="text-[10px] text-aegisaxiom-text-muted ml-1">
                · Closed Won deals for this account
              </span>
            </label>
          </div>

          {/* Dropdown to add deals */}
          <select
            value=""
            onChange={(e) => {
              const dealId = e.target.value;
              if (dealId && !(form.deal_ids || []).includes(dealId)) {
                setForm((f) => ({ ...f, deal_ids: [...(f.deal_ids || []), dealId] }));
              }
            }}
            className="w-full px-3 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text focus:border-violet-500 outline-none mb-2"
          >
            <option value="">+ Add deal...</option>
            {deals
              .filter((d) => {
                // Filter by test deals only (if enabled)
                if (form._testDealsOnly) {
                  const account = accounts.find((a) => a.account_id === d.account_id);
                  if (!account || !account.is_test_data) return false;
                }
                // Filter by selected account
                if (d.account_id !== form.account_id) return false;
                // Filter by Closed Won stage
                if (d.stage !== 'Closed Won') return false;
                // Don't show already selected deals
                const dealId = d.deal_id || d.zoho_deal_id;
                if ((form.deal_ids || []).includes(dealId)) return false;
                return true;
              })
              .map((d) => {
                const dealId = d.deal_id || d.zoho_deal_id;
                const fmt = (n) =>
                  `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
                return (
                  <option key={dealId} value={dealId}>
                    {d.name || d.deal_name} {d.amount > 0 ? `(${fmt(d.amount)})` : ''}
                  </option>
                );
              })}
          </select>

          {/* Show selected deals with remove buttons */}
          {(form.deal_ids || []).length > 0 && (
            <div className="space-y-1">
              {(form.deal_ids || []).map((dealId) => {
                const deal = deals.find((d) => (d.deal_id || d.zoho_deal_id) === dealId);
                if (!deal) return null;
                const fmt = (n) =>
                  `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
                return (
                  <div
                    key={dealId}
                    className="flex items-center gap-2 bg-violet-50 dark:bg-violet-600/10 border border-violet-500/30 rounded px-2 py-1.5"
                  >
                    <Link2 size={12} className="text-violet-600 dark:text-violet-400 flex-shrink-0" />
                    <div className="flex-1 min-w-0">
                      <span className="text-xs text-aegisaxiom-text truncate block">
                        {deal.name || deal.deal_name}
                      </span>
                      {deal.amount > 0 && (
                        <span className="text-[10px] text-aegisaxiom-text-secondary">{fmt(deal.amount)}</span>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setForm((f) => ({
                          ...f,
                          deal_ids: (f.deal_ids || []).filter((id) => id !== dealId),
                        }));
                      }}
                      className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text flex-shrink-0"
                    >
                      <X size={14} />
                    </button>
                  </div>
                );
              })}
            </div>
          )}

          {/* Empty state when no deals available */}
          {deals.filter((d) => {
            if (form._testDealsOnly) {
              const account = accounts.find((a) => a.account_id === d.account_id);
              if (!account || (account.source !== 'test' && account.source !== 'local'))
                return false;
            }
            if (d.account_id !== form.account_id) return false;
            if (d.stage !== 'Closed Won') return false;
            const dealId = d.deal_id || d.zoho_deal_id;
            if ((form.deal_ids || []).includes(dealId)) return false;
            return true;
          }).length === 0 &&
            (form.deal_ids || []).length === 0 && (
              <div className="px-3 py-3 text-center text-xs text-aegisaxiom-text-muted bg-aegisaxiom-accent-muted rounded border border-aegisaxiom-border">
                <Link2 size={14} className="mx-auto mb-1 opacity-30" />
                <p>No Closed Won deals available for this account</p>
              </div>
            )}
        </div>
      )}

      {/* Service Category (only for new contracts) */}
      {showServiceCategory && (
        <ServiceCategorySelector
          serviceCategories={SERVICE_CATEGORIES}
          selectedCategory={form.service_category}
          onSelect={(key) => {
            const cfg = SERVICE_CATEGORIES[key];
            setForm((f) => ({
              ...f,
              service_category: key,
              contract_type: cfg.types[0] || key,
              wholesale_product: key === 'wholesale_mrr' ? 'simplisecure' : '',
            }));
          }}
          compact={compact}
        />
      )}

      {/* Contract Type Selection */}
      {form.service_category && (
        <ContractTypeSelector
          serviceCategory={form.service_category}
          serviceCategories={SERVICE_CATEGORIES}
          typeConfig={TYPE_CONFIG}
          selectedType={form.contract_type}
          onSelect={(typeKey) => {
            setForm((f) => ({
              ...f,
              contract_type: typeKey,
              wholesale_product: form.service_category === 'wholesale_mrr' ? typeKey : '',
            }));
          }}
          compact={compact}
          wholesaleProduct={form.wholesale_product}
        />
      )}

      {/* Dates */}
      <div className={`grid gap-3 ${form.contract_type === 'boh' ? '' : 'grid-cols-2'}`}>
        <div>
          <label className={`${labelClass} block mb-1`}>Start Date</label>
          <input
            type="date"
            value={form.start_date || ''}
            onChange={(e) => setForm((f) => ({ ...f, start_date: e.target.value }))}
            className={inputClass}
          />
        </div>
        {form.contract_type !== 'boh' && (
          <div>
            <label className={`${labelClass} block mb-1`}>End Date</label>
            <input
              type="date"
              value={form.end_date || ''}
              onChange={(e) => setForm((f) => ({ ...f, end_date: e.target.value }))}
              className={inputClass}
            />
          </div>
        )}
      </div>

      {/* Resource Types Section */}
      {(form.contract_type === 'boh' ||
        form.contract_type === 'boa' ||
        form.contract_type === 'monthly') && (
        <div className="border border-aegisaxiom-border rounded-lg p-3">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-3">
              <label className={`${labelClass} font-medium`}>
                Resource Types{' '}
                {form.contract_type === 'boh' && <span className="text-red-600 dark:text-red-400">*</span>}
              </label>
              {form.contract_type === 'boh' && (form.resource_types || []).length > 0 && (
                <span className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-600/10 px-2 py-0.5 rounded">
                  Total:{' '}
                  {(form.resource_types || []).reduce(
                    (sum, rt) => sum + (parseFloat(rt.hours_purchased) || 0),
                    0,
                  )}{' '}
                  hrs
                </span>
              )}
            </div>
            <button
              type="button"
              onClick={addResourceType}
              className="text-xs px-2 py-1 bg-violet-50 dark:bg-violet-600/20 text-violet-600 dark:text-violet-400 hover:bg-violet-600/30 rounded transition flex items-center gap-1"
            >
              <Plus size={12} /> Add Type
            </button>
          </div>

          {(form.resource_types || []).length === 0 ? (
            <p className="text-xs text-aegisaxiom-text-muted text-center py-4">
              No resource types yet. Add one above.
            </p>
          ) : (
            <div className="space-y-2">
              {(form.resource_types || []).map((rt, i) => (
                <div key={rt.id || i} className="bg-aegisaxiom-accent-soft rounded p-2 space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[10px] text-aegisaxiom-text-muted">Type *</label>
                      <select
                        value={rt.name}
                        onChange={(e) => {
                          const selectedType = defaultResourceTypes.find(
                            (drt) => drt.name === e.target.value,
                          );
                          updateResourceType(i, 'name', e.target.value);
                          // Auto-populate sell rate from selected type
                          if (selectedType && selectedType.sell_rate) {
                            updateResourceType(i, 'sell_rate', selectedType.sell_rate);
                          }
                        }}
                        className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                      >
                        <option value="">Select...</option>
                        {defaultResourceTypes.map((drt) => (
                          <option key={drt.name} value={drt.name}>
                            {drt.name}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="text-[10px] text-aegisaxiom-text-muted">Sell Rate ($/hr) *</label>
                      <input
                        type="number"
                        step="0.01"
                        value={rt.sell_rate}
                        onChange={(e) => updateResourceType(i, 'sell_rate', e.target.value)}
                        className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                        placeholder="0.00"
                      />
                    </div>
                  </div>
                  {form.contract_type === 'boh' && (
                    <div>
                      <label className="text-[10px] text-aegisaxiom-text-muted">Hours Purchased *</label>
                      <input
                        type="number"
                        step="0.25"
                        value={rt.hours_purchased}
                        onChange={(e) => updateResourceType(i, 'hours_purchased', e.target.value)}
                        className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                        placeholder="0"
                      />
                    </div>
                  )}
                  <div className="flex justify-end">
                    <button
                      type="button"
                      onClick={() => removeResourceType(i)}
                      className="text-xs text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300 flex items-center gap-1"
                    >
                      <X size={12} /> Remove
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Resources (Contractor Assignments) Section */}
      {!compact && (form.resource_types || []).length > 0 && (
        <div className="border border-aegisaxiom-border rounded-lg p-3">
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs text-aegisaxiom-text-secondary font-medium">
              Assigned Resources ({(form.resources || []).length})
            </label>
            <button
              type="button"
              onClick={addResource}
              className="text-xs px-2 py-1 bg-emerald-50 dark:bg-emerald-600/20 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-600/30 rounded transition flex items-center gap-1"
            >
              <Plus size={12} /> Add Resource
            </button>
          </div>

          <div className="space-y-2">
            {(form.resources || []).map((r, i) => (
              <div key={i} className="flex items-start gap-2 bg-aegisaxiom-surface p-2 rounded">
                <div className="flex-1 space-y-2">
                  {/* Resource Type Selection FIRST */}
                  <div className="flex items-center gap-2">
                    <div className="flex-1">
                      <select
                        value={r.resource_type_id || ''}
                        onChange={(e) => updateResource(i, 'resource_type_id', e.target.value)}
                        className="w-full px-2 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none"
                      >
                        <option value="">1. Select resource type first</option>
                        {(form.resource_types || []).map((rt) => (
                          <option key={rt.id} value={rt.id}>
                            {rt.name} (${rt.sell_rate}/hr)
                          </option>
                        ))}
                      </select>
                    </div>
                    <button
                      type="button"
                      onClick={() => removeResource(i)}
                      className="text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300"
                    >
                      <X size={14} />
                    </button>
                  </div>

                  {/* User Selection - Filtered by Resource Type */}
                  <div className="grid grid-cols-2 gap-2">
                    <select
                      value={r.user_id}
                      onChange={(e) => {
                        const userId = e.target.value;
                        const resourceTypeId = r.resource_type_id;

                        // Check for duplicate
                        const isDuplicate = (form.resources || []).some(
                          (res, idx) =>
                            idx !== i &&
                            res.user_id === userId &&
                            res.resource_type_id === resourceTypeId,
                        );

                        if (isDuplicate) {
                          alert(
                            'This contractor is already assigned to this resource type. Please select a different contractor.',
                          );
                          return;
                        }

                        updateResource(i, 'user_id', userId);
                      }}
                      disabled={!r.resource_type_id}
                      className="px-2 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none disabled:opacity-50"
                    >
                      <option value="">
                        {r.resource_type_id ? '2. Select contractor' : 'Select resource type first'}
                      </option>
                      {r.resource_type_id &&
                        (() => {
                          const selectedResourceType = (form.resource_types || []).find(
                            (rt) => rt.id === r.resource_type_id,
                          );
                          const resourceTypeName = selectedResourceType?.name;

                          if (!resourceTypeName) return null;

                          // Match is case-insensitive + whitespace-
                          // trimmed because the contract's name string
                          // comes from a free-text default-resource-types
                          // table whereas the user's
                          // resource_type_assignments[].resource_type_name
                          // can be entered by a different admin in
                          // User Management — historical data showed
                          // mismatches like "L1 Engineer" vs
                          // "l1 engineer ". Strict ===  matching meant
                          // the dropdown silently showed zero options.
                          const want = String(resourceTypeName).trim().toLowerCase();
                          const matches = users
                            .map((u) => {
                              const assignment = (u.resource_type_assignments || []).find(
                                (a) =>
                                  String(a?.resource_type_name || '').trim().toLowerCase() === want,
                              );
                              return assignment ? { u, assignment } : null;
                            })
                            .filter(Boolean);
                          return matches.map(({ u, assignment }) => (
                            <option key={u.user_id} value={u.user_id}>
                              {u.name} - ${(assignment?.cost_rate || 0).toFixed(2)}/hr
                            </option>
                          ));
                        })()}
                    </select>
                    {/* Explicit empty-state for the contractor picker
                        so users understand WHY the list is empty —
                        previously the dropdown silently showed only
                        the placeholder which read as a broken control. */}
                    {r.resource_type_id &&
                      (() => {
                        const selectedResourceType = (form.resource_types || []).find(
                          (rt) => rt.id === r.resource_type_id,
                        );
                        const resourceTypeName = selectedResourceType?.name;
                        if (!resourceTypeName) return null;
                        const want = String(resourceTypeName).trim().toLowerCase();
                        const hasAny = (users || []).some((u) =>
                          (u.resource_type_assignments || []).some(
                            (a) =>
                              String(a?.resource_type_name || '').trim().toLowerCase() === want,
                          ),
                        );
                        if (hasAny) return null;
                        return (
                          <p
                            className="text-[10px] text-amber-600 dark:text-amber-400 mt-1"
                            data-testid={`contract-resource-picker-empty-${i}`}
                          >
                            No users carry the <code>{resourceTypeName}</code> resource-type
                            assignment yet. Assign one in <strong>Admin → User
                            Management</strong> before picking a contractor here.
                          </p>
                        );
                      })()}

                    {/* Allocation Input — Percentage or Hours toggle */}
                    <div>
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => {
                            const currentMode = r.allocation_mode || 'percent';
                            const newMode = currentMode === 'percent' ? 'hours' : 'percent';
                            // Convert value when switching modes
                            const resourceType = (form.resource_types || []).find(
                              (rt) => rt.id === r.resource_type_id,
                            );
                            const totalHours = parseFloat(resourceType?.hours_purchased) || 0;
                            let updates = { allocation_mode: newMode };
                            if (newMode === 'hours' && totalHours > 0) {
                              const pct = parseFloat(r.allocation_percentage) || 0;
                              updates.hours_allocated = ((pct / 100) * totalHours).toFixed(1);
                            } else if (newMode === 'percent' && totalHours > 0) {
                              const hrs = parseFloat(r.hours_allocated) || 0;
                              updates.allocation_percentage = ((hrs / totalHours) * 100).toFixed(0);
                            }
                            const newResources = [...(form.resources || [])];
                            newResources[i] = { ...newResources[i], ...updates };
                            setForm((f) => ({ ...f, resources: newResources }));
                          }}
                          disabled={!r.user_id}
                          className="px-1.5 py-1 bg-aegisaxiom-elevated hover:bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded text-[9px] text-aegisaxiom-text-secondary font-medium disabled:opacity-50 transition whitespace-nowrap"
                          title="Toggle between percentage and hours"
                        >
                          {(r.allocation_mode || 'percent') === 'percent' ? '%' : 'hrs'}
                        </button>
                        {(r.allocation_mode || 'percent') === 'percent' ? (
                          <input
                            type="number"
                            min="0"
                            max="100"
                            step="1"
                            placeholder="%"
                            value={r.allocation_percentage || ''}
                            onChange={(e) =>
                              updateResource(i, 'allocation_percentage', e.target.value)
                            }
                            disabled={!r.user_id}
                            className="w-20 px-2 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none disabled:opacity-50 text-right"
                            title="Percentage of total hours allocated to this contractor"
                          />
                        ) : (
                          <input
                            type="number"
                            min="0"
                            step="0.5"
                            placeholder="hrs"
                            value={r.hours_allocated || ''}
                            onChange={(e) => {
                              const hrs = e.target.value;
                              const resourceType = (form.resource_types || []).find(
                                (rt) => rt.id === r.resource_type_id,
                              );
                              const totalHours = parseFloat(resourceType?.hours_purchased) || 0;
                              const pct =
                                totalHours > 0
                                  ? (((parseFloat(hrs) || 0) / totalHours) * 100).toFixed(0)
                                  : '0';
                              const newResources = [...(form.resources || [])];
                              newResources[i] = {
                                ...newResources[i],
                                hours_allocated: hrs,
                                allocation_percentage: pct,
                              };
                              setForm((f) => ({ ...f, resources: newResources }));
                            }}
                            disabled={!r.user_id}
                            className="w-20 px-2 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none disabled:opacity-50 text-right"
                            title="Total hours allocated to this contractor"
                          />
                        )}
                        <span className="text-xs text-aegisaxiom-text-secondary">
                          {(r.allocation_mode || 'percent') === 'percent' ? '%' : 'hrs'}
                        </span>
                      </div>
                      <div className="text-[9px] text-aegisaxiom-text-muted mt-0.5">
                        {(() => {
                          const resourceType = (form.resource_types || []).find(
                            (rt) => rt.id === r.resource_type_id,
                          );
                          const totalHours = parseFloat(resourceType?.hours_purchased) || 0;
                          if ((r.allocation_mode || 'percent') === 'percent') {
                            const percentage = parseFloat(r.allocation_percentage) || 0;
                            const calculatedHours = (percentage / 100) * totalHours;
                            if (totalHours > 0 && percentage > 0) {
                              return `= ${calculatedHours.toFixed(1)} hrs`;
                            }
                            return 'Enter %';
                          } else {
                            const hrs = parseFloat(r.hours_allocated) || 0;
                            if (totalHours > 0 && hrs > 0) {
                              return `= ${((hrs / totalHours) * 100).toFixed(0)}%`;
                            }
                            return 'Enter hrs';
                          }
                        })()}
                      </div>
                    </div>
                  </div>

                  {/* Allocation Validation for this Resource Type */}
                  {r.resource_type_id &&
                    (() => {
                      const sameTypeResources = (form.resources || []).filter(
                        (res) => res.resource_type_id === r.resource_type_id,
                      );
                      const totalPercentage = sameTypeResources.reduce(
                        (sum, res) => sum + (parseFloat(res.allocation_percentage) || 0),
                        0,
                      );

                      if (sameTypeResources.length > 1) {
                        return (
                          <div
                            className={`text-[10px] px-2 py-1 rounded flex items-center gap-1 ${
                              totalPercentage === 100
                                ? 'text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20'
                                : totalPercentage > 100
                                  ? 'text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20'
                                  : 'text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20'
                            }`}
                          >
                            {totalPercentage === 100 ? (
                              <>✓ Total allocation: {totalPercentage}%</>
                            ) : totalPercentage > 100 ? (
                              <>⚠ Over-allocated: {totalPercentage}% (should be 100%)</>
                            ) : (
                              <>⚠ Under-allocated: {totalPercentage}% (should be 100%)</>
                            )}
                          </div>
                        );
                      }
                      return null;
                    })()}

                  <div className="flex items-center gap-2">
                    <div className="relative flex-1">
                      <span className="absolute left-1.5 top-1/2 -translate-y-1/2 text-[10px] text-red-600 dark:text-red-400">
                        Cost
                      </span>
                      <input
                        type="number"
                        step="0.01"
                        placeholder="0.00"
                        value={r.cost_rate}
                        readOnly
                        className="w-full pl-9 pr-2 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none"
                        title="Cost rate is pulled from contractor's profile"
                      />
                    </div>
                    <input
                      placeholder="Role/Title (optional)"
                      value={r.role || ''}
                      onChange={(e) => updateResource(i, 'role', e.target.value)}
                      className="flex-1 px-2 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none placeholder:text-aegisaxiom-text-muted"
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ========== WHOLESALE MRR PRODUCTS SECTION ========== */}
      {form.service_category === 'wholesale_mrr' && (
        <WholesaleProductsSection
          form={form}
          products={products}
          productSearch={productSearch}
          setProductSearch={setProductSearch}
          productTypeFilter={productTypeFilter}
          setProductTypeFilter={setProductTypeFilter}
          loadingProducts={loadingProducts}
          loadProducts={loadProducts}
          addProduct={addProduct}
          updateProduct={updateProduct}
          removeProduct={removeProduct}
          getFilteredProducts={getFilteredProducts}
          compact={compact}
        />
      )}

      {/* Type-Specific Fields (Fixed Fee, Monthly) */}
      {form.contract_type === 'fixed_fee' && (
        <div>
          <label className={`${labelClass} block mb-1`}>
            Total Amount ($) <span className="text-red-600 dark:text-red-400">*</span>
          </label>
          <input
            type="number"
            step="0.01"
            value={form.total_amount || ''}
            onChange={(e) => setForm((f) => ({ ...f, total_amount: e.target.value }))}
            className={inputClass}
            placeholder="0.00"
          />
        </div>
      )}

      {form.contract_type === 'monthly' && (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={`${labelClass} block mb-1`}>
              Monthly Rate ($) <span className="text-red-600 dark:text-red-400">*</span>
            </label>
            <input
              type="number"
              step="0.01"
              value={form.monthly_rate || ''}
              onChange={(e) => setForm((f) => ({ ...f, monthly_rate: e.target.value }))}
              className={inputClass}
              placeholder="0.00"
            />
          </div>
          <div>
            <label className={`${labelClass} block mb-1`}>Billing Day (1-28)</label>
            <input
              type="number"
              min="1"
              max="28"
              value={form.billing_day || 1}
              onChange={(e) => setForm((f) => ({ ...f, billing_day: e.target.value }))}
              className={inputClass}
            />
          </div>
        </div>
      )}

      {/* ========== BILLING TERMS SECTION ========== */}

      {/* Fixed Fee: Milestone-Based Billing */}
      {form.contract_type === 'fixed_fee' && !compact && (
        <div className="border border-amber-200 dark:border-amber-700/30 rounded-lg p-3 bg-amber-50 dark:bg-amber-900/10">
          <h4 className="text-xs font-semibold text-amber-700 dark:text-amber-300 mb-2">
            💰 Billing Terms - Fixed Fee
          </h4>

          <div className="mb-3">
            <label className={`${labelClass} block mb-1`}>Billing Method</label>
            <select
              value={form.billing_method || 'percentage'}
              onChange={(e) =>
                setForm((f) => ({ ...f, billing_method: e.target.value, milestones: [] }))
              }
              className={inputClass}
            >
              <option value="percentage">
                Percentage-Based (e.g., 50% upfront, 25% mid, 25% end)
              </option>
              <option value="date">Date-Based (Invoice on specific dates)</option>
              <option value="milestone">Milestone-Based (Invoice when milestones complete)</option>
            </select>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-xs text-aegisaxiom-text-secondary">Milestones</label>
              <button
                type="button"
                onClick={() =>
                  setForm((f) => ({
                    ...f,
                    milestones: [
                      ...(f.milestones || []),
                      {
                        id: `ms_${Date.now()}`,
                        name: '',
                        percentage: form.billing_method === 'percentage' ? 0 : undefined,
                        amount: form.billing_method !== 'percentage' ? 0 : undefined,
                        due_date: '',
                        status: 'pending',
                      },
                    ],
                  }))
                }
                className="text-xs px-2 py-1 bg-amber-50 dark:bg-amber-600/20 text-amber-600 dark:text-amber-400 hover:bg-amber-600/30 rounded flex items-center gap-1"
              >
                <Plus size={12} /> Add Milestone
              </button>
            </div>

            {(form.milestones || []).map((ms, i) => (
              <div key={ms.id || i} className="bg-aegisaxiom-accent-soft rounded p-2 space-y-2">
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[10px] text-aegisaxiom-text-muted">Milestone Name</label>
                    <input
                      value={ms.name}
                      onChange={(e) => {
                        const updated = [...(form.milestones || [])];
                        updated[i] = { ...updated[i], name: e.target.value };
                        setForm((f) => ({ ...f, milestones: updated }));
                      }}
                      placeholder="e.g., Upfront Payment"
                      className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] text-aegisaxiom-text-muted">
                      {form.billing_method === 'percentage' ? 'Percentage (%)' : 'Amount ($)'}
                    </label>
                    <input
                      type="number"
                      step={form.billing_method === 'percentage' ? '0.1' : '0.01'}
                      value={form.billing_method === 'percentage' ? ms.percentage : ms.amount}
                      onChange={(e) => {
                        const updated = [...(form.milestones || [])];
                        const field =
                          form.billing_method === 'percentage' ? 'percentage' : 'amount';
                        updated[i] = { ...updated[i], [field]: e.target.value };
                        setForm((f) => ({ ...f, milestones: updated }));
                      }}
                      placeholder="0"
                      className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                    />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {form.billing_method === 'date' && (
                    <div>
                      <label className="text-[10px] text-aegisaxiom-text-muted">Invoice Date</label>
                      <input
                        type="date"
                        value={ms.invoice_date || ms.due_date}
                        onChange={(e) => {
                          const updated = [...(form.milestones || [])];
                          updated[i] = {
                            ...updated[i],
                            invoice_date: e.target.value,
                            due_date: e.target.value,
                          };
                          setForm((f) => ({ ...f, milestones: updated }));
                        }}
                        className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                      />
                      <div className="text-[9px] text-aegisaxiom-text-muted mt-0.5">
                        Due date = Invoice + Net terms
                      </div>
                    </div>
                  )}
                  <div className="flex items-end">
                    <button
                      type="button"
                      onClick={() => {
                        const updated = (form.milestones || []).filter((_, idx) => idx !== i);
                        setForm((f) => ({ ...f, milestones: updated }));
                      }}
                      className="text-xs text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300"
                    >
                      Remove
                    </button>
                  </div>
                </div>
              </div>
            ))}

            {form.billing_method === 'percentage' && (form.milestones || []).length > 0 && (
              <div className="text-xs text-aegisaxiom-text-secondary mt-2">
                Total:{' '}
                {(form.milestones || []).reduce(
                  (sum, ms) => sum + (parseFloat(ms.percentage) || 0),
                  0,
                )}
                %
                {(form.milestones || []).reduce(
                  (sum, ms) => sum + (parseFloat(ms.percentage) || 0),
                  0,
                ) !== 100 && <span className="text-amber-600 dark:text-amber-400 ml-2">⚠️ Should equal 100%</span>}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Block of Hours: Billing Schedule */}
      {form.contract_type === 'boh' && !compact && (
        <div className="border border-blue-700/30 rounded-lg p-3 bg-blue-900/10">
          <h4 className="text-xs font-semibold text-blue-700 dark:text-blue-300 mb-2">
            💰 Billing Terms - Block of Hours
          </h4>

          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <label className="text-xs text-aegisaxiom-text-secondary">Billing Schedule</label>
              <button
                type="button"
                onClick={() =>
                  setForm((f) => ({
                    ...f,
                    billing_schedule: [
                      ...(f.billing_schedule || []),
                      {
                        id: `bs_${Date.now()}`,
                        trigger_type: 'date', // 'date' | 'recurring' | 'usage'
                        description: '',
                        hours: 0,
                        amount: 0,
                        invoice_date: '',
                        recurring_frequency: 'monthly', // 'weekly' | 'monthly'
                        recurring_count: 1,
                        usage_threshold: 10, // hours remaining
                        status: 'pending',
                      },
                    ],
                  }))
                }
                className="text-xs px-2 py-1 bg-blue-50 dark:bg-blue-600/20 text-blue-600 dark:text-blue-400 hover:bg-blue-600/30 rounded flex items-center gap-1"
              >
                <Plus size={12} /> Add Billing Item
              </button>
            </div>

            {(form.billing_schedule || []).map((bs, i) => (
              <div
                key={bs.id || i}
                className="bg-aegisaxiom-accent-soft rounded p-3 space-y-2 border border-blue-800/30"
              >
                {/* Trigger Type Selection */}
                <div>
                  <label className="text-[10px] text-aegisaxiom-text-muted font-semibold">
                    Billing Trigger
                  </label>
                  <select
                    value={bs.trigger_type || 'date'}
                    onChange={(e) => {
                      const updated = [...(form.billing_schedule || [])];
                      updated[i] = { ...updated[i], trigger_type: e.target.value };
                      setForm((f) => ({ ...f, billing_schedule: updated }));
                    }}
                    className="w-full px-2 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                  >
                    <option value="date">Specific Date (Invoice on a set date)</option>
                    <option value="recurring">Recurring (Weekly/Monthly from first invoice)</option>
                    <option value="usage">Usage-Based (When hours remaining hits threshold)</option>
                  </select>
                </div>

                {/* Basic Fields */}
                <div className="grid grid-cols-3 gap-2">
                  <div>
                    <label className="text-[10px] text-aegisaxiom-text-muted">Description</label>
                    <input
                      value={bs.description}
                      onChange={(e) => {
                        const updated = [...(form.billing_schedule || [])];
                        updated[i] = { ...updated[i], description: e.target.value };
                        setForm((f) => ({ ...f, billing_schedule: updated }));
                      }}
                      placeholder="e.g., Upfront Payment"
                      className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] text-aegisaxiom-text-muted">Hours</label>
                    <input
                      type="number"
                      value={bs.hours}
                      onChange={(e) => {
                        const updated = [...(form.billing_schedule || [])];
                        updated[i] = { ...updated[i], hours: e.target.value };
                        setForm((f) => ({ ...f, billing_schedule: updated }));
                      }}
                      placeholder="0"
                      className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] text-aegisaxiom-text-muted">Amount ($)</label>
                    <input
                      type="number"
                      step="0.01"
                      value={bs.amount}
                      onChange={(e) => {
                        const updated = [...(form.billing_schedule || [])];
                        updated[i] = { ...updated[i], amount: e.target.value };
                        setForm((f) => ({ ...f, billing_schedule: updated }));
                      }}
                      placeholder="0.00"
                      className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                    />
                  </div>
                </div>

                {/* Trigger-Specific Fields */}
                {bs.trigger_type === 'date' && (
                  <div className="grid grid-cols-2 gap-2 bg-aegisaxiom-surface p-2 rounded">
                    <div>
                      <label className="text-[10px] text-aegisaxiom-text-muted">Invoice Date</label>
                      <input
                        type="date"
                        value={bs.invoice_date}
                        onChange={(e) => {
                          const updated = [...(form.billing_schedule || [])];
                          updated[i] = { ...updated[i], invoice_date: e.target.value };
                          setForm((f) => ({ ...f, billing_schedule: updated }));
                        }}
                        className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                      />
                    </div>
                    <div className="text-[10px] text-aegisaxiom-text-secondary flex items-center">
                      📅 Due date = Invoice date + Payment terms (Net 30, etc.)
                    </div>
                  </div>
                )}

                {bs.trigger_type === 'recurring' && (
                  <div className="grid grid-cols-3 gap-2 bg-aegisaxiom-surface p-2 rounded">
                    <div>
                      <label className="text-[10px] text-aegisaxiom-text-muted">First Invoice Date</label>
                      <input
                        type="date"
                        value={bs.invoice_date}
                        onChange={(e) => {
                          const updated = [...(form.billing_schedule || [])];
                          updated[i] = { ...updated[i], invoice_date: e.target.value };
                          setForm((f) => ({ ...f, billing_schedule: updated }));
                        }}
                        className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                      />
                    </div>
                    <div>
                      <label className="text-[10px] text-aegisaxiom-text-muted">Frequency</label>
                      <select
                        value={bs.recurring_frequency || 'monthly'}
                        onChange={(e) => {
                          const updated = [...(form.billing_schedule || [])];
                          updated[i] = { ...updated[i], recurring_frequency: e.target.value };
                          setForm((f) => ({ ...f, billing_schedule: updated }));
                        }}
                        className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                      >
                        <option value="weekly">Weekly</option>
                        <option value="biweekly">Bi-Weekly</option>
                        <option value="monthly">Monthly</option>
                      </select>
                    </div>
                    <div>
                      <label className="text-[10px] text-aegisaxiom-text-muted">Number of Invoices</label>
                      <input
                        type="number"
                        min="1"
                        value={bs.recurring_count || 1}
                        onChange={(e) => {
                          const updated = [...(form.billing_schedule || [])];
                          updated[i] = { ...updated[i], recurring_count: e.target.value };
                          setForm((f) => ({ ...f, billing_schedule: updated }));
                        }}
                        placeholder="1"
                        className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                      />
                    </div>
                  </div>
                )}

                {bs.trigger_type === 'usage' && (
                  <div className="grid grid-cols-2 gap-2 bg-aegisaxiom-surface p-2 rounded">
                    <div>
                      <label className="text-[10px] text-aegisaxiom-text-muted">
                        Trigger When Hours Remaining
                      </label>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-aegisaxiom-text-muted">≤</span>
                        <input
                          type="number"
                          min="0"
                          value={bs.usage_threshold || 10}
                          onChange={(e) => {
                            const updated = [...(form.billing_schedule || [])];
                            updated[i] = { ...updated[i], usage_threshold: e.target.value };
                            setForm((f) => ({ ...f, billing_schedule: updated }));
                          }}
                          placeholder="10"
                          className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                        />
                        <span className="text-xs text-aegisaxiom-text-secondary">hours</span>
                      </div>
                    </div>
                    <div className="text-[10px] text-aegisaxiom-text-secondary flex items-center">
                      ⚡ Auto-invoice when hours remaining drops to or below this threshold
                    </div>
                  </div>
                )}

                {/* Remove Button */}
                <div className="flex justify-end">
                  <button
                    type="button"
                    onClick={() => {
                      const updated = (form.billing_schedule || []).filter((_, idx) => idx !== i);
                      setForm((f) => ({ ...f, billing_schedule: updated }));
                    }}
                    className="text-xs text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300 flex items-center gap-1"
                  >
                    <X size={12} /> Remove
                  </button>
                </div>
              </div>
            ))}

            {(form.billing_schedule || []).length === 0 && (
              <div className="text-xs text-aegisaxiom-text-muted text-center py-4 bg-aegisaxiom-accent-muted rounded border border-dashed border-aegisaxiom-border">
                No billing schedule defined. Add billing items to configure when invoices should be
                generated.
              </div>
            )}
          </div>
        </div>
      )}

      {/* Bill on Actuals: Billing Frequency */}
      {form.contract_type === 'boa' && !compact && (
        <div className="border border-purple-700/30 rounded-lg p-3 bg-purple-900/10">
          <h4 className="text-xs font-semibold text-purple-300 mb-2">
            💰 Billing Terms - Bill on Actuals
          </h4>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={`${labelClass} block mb-1`}>Billing Frequency</label>
              <select
                value={form.billing_frequency || 'monthly'}
                onChange={(e) => setForm((f) => ({ ...f, billing_frequency: e.target.value }))}
                className={inputClass}
              >
                <option value="monthly">Monthly</option>
                <option value="biweekly">Bi-Weekly</option>
                <option value="manual">Manual (Admin creates)</option>
              </select>
            </div>
            {form.billing_frequency === 'monthly' && (
              <div>
                <label className={`${labelClass} block mb-1`}>Billing Day (1-28)</label>
                <input
                  type="number"
                  min="1"
                  max="28"
                  value={form.boa_billing_day || 1}
                  onChange={(e) => setForm((f) => ({ ...f, boa_billing_day: e.target.value }))}
                  className={inputClass}
                />
              </div>
            )}
          </div>

          {/* Estimated BOA Cost - Field Services Only */}
          {form.service_category === 'field_services' && (
            <div className="mt-3 pt-3 border-t border-purple-700/20">
              <label className={`${labelClass} block mb-1`}>
                Estimated BOA Cost{' '}
                <span className="text-xs text-aegisaxiom-text-muted">(for Field Services)</span>
              </label>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted">$</span>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  value={form.estimated_boa_cost || ''}
                  onChange={(e) => setForm((f) => ({ ...f, estimated_boa_cost: e.target.value }))}
                  placeholder="0.00"
                  className={`${inputClass} pl-7`}
                />
              </div>
              <p className="text-[10px] text-aegisaxiom-text-muted mt-1">
                Estimated total cost for this BOA contract (Field Services tracking)
              </p>
            </div>
          )}
        </div>
      )}

      {/* Payment Terms (All Contract Types) */}
      {!compact && form.contract_type && (
        <div className="border border-aegisaxiom-border rounded-lg p-3">
          <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-2">💳 Payment Terms</h4>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={`${labelClass} block mb-1`}>Payment Terms</label>
              <select
                value={form.payment_terms || 'net_30'}
                onChange={(e) => setForm((f) => ({ ...f, payment_terms: e.target.value }))}
                className={inputClass}
              >
                <option value="net_7">Net 7 (Due in 7 days)</option>
                <option value="net_14">Net 14 (Due in 14 days)</option>
                <option value="net_30">Net 30 (Due in 30 days)</option>
                <option value="net_45">Net 45 (Due in 45 days)</option>
                <option value="due_on_receipt">Due on Receipt</option>
              </select>
            </div>
            <div>
              <label className={`${labelClass} block mb-1`}>Invoice Prefix (Optional)</label>
              <input
                value={form.invoice_prefix || ''}
                onChange={(e) => setForm((f) => ({ ...f, invoice_prefix: e.target.value }))}
                placeholder="e.g., INV-"
                className={inputClass}
              />
            </div>
          </div>
        </div>
      )}

      {/* ========== CONTRACT SUMMARY & VALIDATION ========== */}
      {!compact && form.contract_type && (
        <div className="border-2 border-emerald-200 dark:border-emerald-700/40 rounded-lg p-4 bg-emerald-50 dark:bg-emerald-900/10">
          <div className="flex items-center gap-2 mb-3">
            <CheckCircle className="text-emerald-600 dark:text-emerald-400" size={18} />
            <h4 className="text-sm font-semibold text-emerald-700 dark:text-emerald-300">
              Contract Summary & Validation
            </h4>
          </div>

          {/* Validation Messages */}
          <div className="mb-4 space-y-2">
            {/* Required Fields Check */}
            {!form.name && (
              <div className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 px-3 py-1.5 rounded">
                <AlertTriangle size={14} />
                <span>Contract Name is required</span>
              </div>
            )}
            {!form.account_id && (
              <div className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 px-3 py-1.5 rounded">
                <AlertTriangle size={14} />
                <span>Account/Client must be selected</span>
              </div>
            )}
            {!form.service_category && showServiceCategory && (
              <div className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 px-3 py-1.5 rounded">
                <AlertTriangle size={14} />
                <span>Service Category is required</span>
              </div>
            )}

            {/* Contract Type Specific Validation */}
            {form.contract_type === 'boh' && (form.resource_types || []).length === 0 && (
              <div className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 px-3 py-1.5 rounded">
                <AlertTriangle size={14} />
                <span>Block of Hours requires at least one Resource Type</span>
              </div>
            )}

            {/* Assigned Resources Validation - CRITICAL */}
            {(form.contract_type === 'boh' ||
              form.contract_type === 'boa' ||
              form.contract_type === 'monthly') &&
              (form.resources || []).length === 0 && (
                <div className="flex items-center gap-2 text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 px-3 py-1.5 rounded border border-red-200 dark:border-red-700/30">
                  <AlertTriangle size={14} />
                  <span className="font-semibold">
                    REQUIRED: At least one contractor must be assigned
                  </span>
                </div>
              )}

            {form.contract_type === 'fixed_fee' && form.billing_method === 'percentage' && (
              <div className="flex items-center gap-2 text-xs">
                {(() => {
                  const totalPercentage = (form.milestones || []).reduce(
                    (sum, ms) => sum + (parseFloat(ms.percentage) || 0),
                    0,
                  );
                  if (totalPercentage !== 100) {
                    return (
                      <div className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 px-3 py-1.5 rounded w-full">
                        <AlertTriangle size={14} />
                        <span>Fixed Fee milestones total {totalPercentage}% (should be 100%)</span>
                      </div>
                    );
                  }
                  return (
                    <div className="flex items-center gap-2 text-xs text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 px-3 py-1.5 rounded w-full">
                      <CheckCircle size={14} />
                      <span>Milestones add up to 100% ✓</span>
                    </div>
                  );
                })()}
              </div>
            )}

            {/* All Valid */}
            {form.name &&
              form.account_id &&
              (showServiceCategory ? form.service_category : true) &&
              (form.contract_type === 'boh' ? (form.resource_types || []).length > 0 : true) &&
              (['boh', 'boa', 'monthly'].includes(form.contract_type)
                ? (form.resources || []).length > 0
                : true) && (
                <div className="flex items-center gap-2 text-xs text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 px-3 py-1.5 rounded">
                  <CheckCircle size={14} />
                  <span>All required fields completed</span>
                </div>
              )}
          </div>

          {/* Contract Summary */}
          <div className="bg-aegisaxiom-elevated rounded-lg p-3 space-y-2">
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div>
                <div className="text-aegisaxiom-text-muted text-[10px] mb-0.5">Contract Name</div>
                <div className="text-aegisaxiom-text font-medium">
                  {form.name || <span className="text-aegisaxiom-text-muted italic">Not set</span>}
                </div>
              </div>
              <div>
                <div className="text-aegisaxiom-text-muted text-[10px] mb-0.5">Account/Client</div>
                <div className="text-aegisaxiom-text font-medium">
                  {form.account_name || <span className="text-aegisaxiom-text-muted italic">Not selected</span>}
                </div>
              </div>
              {showServiceCategory && (
                <div>
                  <div className="text-aegisaxiom-text-muted text-[10px] mb-0.5">Service Category</div>
                  <div className="text-aegisaxiom-text font-medium">
                    {form.service_category ? (
                      SERVICE_CATEGORIES[form.service_category]?.label
                    ) : (
                      <span className="text-aegisaxiom-text-muted italic">Not selected</span>
                    )}
                  </div>
                </div>
              )}
              <div>
                <div className="text-aegisaxiom-text-muted text-[10px] mb-0.5">Contract Type</div>
                <div className="text-aegisaxiom-text font-medium">
                  {form.contract_type ? (
                    TYPE_CONFIG[form.contract_type]?.label
                  ) : (
                    <span className="text-aegisaxiom-text-muted italic">Not selected</span>
                  )}
                </div>
              </div>
              <div>
                <div className="text-aegisaxiom-text-muted text-[10px] mb-0.5">Start Date</div>
                <div className="text-aegisaxiom-text font-medium">
                  {form.start_date || <span className="text-aegisaxiom-text-muted italic">Not set</span>}
                </div>
              </div>
              <div>
                <div className="text-aegisaxiom-text-muted text-[10px] mb-0.5">End Date</div>
                <div className="text-aegisaxiom-text font-medium">
                  {form.end_date || <span className="text-aegisaxiom-text-muted italic">Not set</span>}
                </div>
              </div>

              {/* Contract Type Specific Summary */}
              {form.contract_type === 'boh' && (
                <>
                  <div>
                    <div className="text-aegisaxiom-text-muted text-[10px] mb-0.5">Total Hours</div>
                    <div className="text-aegisaxiom-text font-medium">
                      {(form.resource_types || []).reduce(
                        (sum, rt) => sum + (parseFloat(rt.hours_purchased) || 0),
                        0,
                      ) || 0}{' '}
                      hours
                    </div>
                  </div>
                  <div>
                    <div className="text-aegisaxiom-text-muted text-[10px] mb-0.5">Resource Types</div>
                    <div className="text-aegisaxiom-text font-medium">
                      {(form.resource_types || []).length} types
                    </div>
                  </div>
                </>
              )}

              {form.contract_type === 'fixed_fee' && (
                <>
                  <div>
                    <div className="text-aegisaxiom-text-muted text-[10px] mb-0.5">Total Amount</div>
                    <div className="text-aegisaxiom-text font-medium">
                      ${parseFloat(form.total_amount || 0).toLocaleString()}
                    </div>
                  </div>
                  <div>
                    <div className="text-aegisaxiom-text-muted text-[10px] mb-0.5">Milestones</div>
                    <div className="text-aegisaxiom-text font-medium">
                      {(form.milestones || []).length} milestones
                    </div>
                  </div>
                </>
              )}

              {form.contract_type === 'monthly' && (
                <div>
                  <div className="text-aegisaxiom-text-muted text-[10px] mb-0.5">Monthly Rate</div>
                  <div className="text-aegisaxiom-text font-medium">
                    ${parseFloat(form.monthly_rate || 0).toLocaleString()}/month
                  </div>
                </div>
              )}

              <div>
                <div className="text-aegisaxiom-text-muted text-[10px] mb-0.5">Payment Terms</div>
                <div className="text-aegisaxiom-text font-medium">
                  {form.payment_terms
                    ? form.payment_terms.replace('_', ' ').toUpperCase()
                    : 'Net 30'}
                </div>
              </div>

              {form.contract_type === 'boh' && (form.billing_schedule || []).length > 0 && (
                <div className="col-span-2">
                  <div className="text-aegisaxiom-text-muted text-[10px] mb-0.5">Billing Schedule</div>
                  <div className="text-aegisaxiom-text font-medium">
                    {(form.billing_schedule || []).length} billing items configured
                  </div>
                </div>
              )}
            </div>

            {/* Billing Summary for BoH */}
            {form.contract_type === 'boh' && (form.billing_schedule || []).length > 0 && (
              <div className="mt-3 pt-3 border-t border-aegisaxiom-border">
                <div className="text-[10px] text-aegisaxiom-text-secondary mb-2">Billing Schedule Preview:</div>
                <div className="space-y-1">
                  {(form.billing_schedule || []).map((bs, i) => (
                    <div
                      key={i}
                      className="text-xs text-aegisaxiom-text-secondary flex items-center justify-between"
                    >
                      <span>
                        {bs.description || `Item ${i + 1}`} ({bs.hours || 0} hrs)
                      </span>
                      <span className="font-medium">
                        ${parseFloat(bs.amount || 0).toLocaleString()}
                      </span>
                    </div>
                  ))}
                  <div className="text-xs text-emerald-600 dark:text-emerald-400 font-semibold flex items-center justify-between pt-1 border-t border-aegisaxiom-border">
                    <span>Total</span>
                    <span>
                      $
                      {(form.billing_schedule || [])
                        .reduce((sum, bs) => sum + (parseFloat(bs.amount) || 0), 0)
                        .toLocaleString()}
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* Milestone Summary for Fixed Fee */}
            {form.contract_type === 'fixed_fee' && (form.milestones || []).length > 0 && (
              <div className="mt-3 pt-3 border-t border-aegisaxiom-border">
                <div className="text-[10px] text-aegisaxiom-text-secondary mb-2">Milestone Schedule Preview:</div>
                <div className="space-y-1">
                  {(form.milestones || []).map((ms, i) => (
                    <div
                      key={i}
                      className="text-xs text-aegisaxiom-text-secondary flex items-center justify-between"
                    >
                      <span>{ms.name || `Milestone ${i + 1}`}</span>
                      <span className="font-medium">
                        {form.billing_method === 'percentage'
                          ? `${ms.percentage || 0}%`
                          : `$${parseFloat(ms.amount || 0).toLocaleString()}`}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* ========== FINANCIAL ANALYSIS ========== */}
          {(form.contract_type === 'boh' || form.contract_type === 'boa') &&
            (form.resource_types || []).length > 0 && (
              <div className="mt-4 bg-aegisaxiom-surface rounded-lg p-4 border border-emerald-200 dark:border-emerald-700/30">
                <h5 className="text-xs font-semibold text-emerald-700 dark:text-emerald-300 mb-3 flex items-center gap-2">
                  <DollarSign size={14} />
                  Financial Analysis & Forecasting
                </h5>

                {/* Revenue Analysis */}
                <div className="mb-4">
                  <div className="text-[10px] text-aegisaxiom-text-secondary font-semibold mb-2 uppercase">
                    Revenue Model
                  </div>
                  <div className="space-y-2">
                    {/* Resource Type Breakdown */}
                    {(form.resource_types || []).map((rt, i) => {
                      const hours = parseFloat(rt.hours_purchased) || 0;
                      const sellRate = parseFloat(rt.sell_rate) || 0;
                      const revenue = hours * sellRate;
                      return (
                        <div
                          key={i}
                          className="flex items-center justify-between text-xs bg-aegisaxiom-elevated px-3 py-2 rounded"
                        >
                          <div className="flex-1">
                            <span className="text-aegisaxiom-text-secondary">{rt.name || 'Resource Type'}</span>
                            <span className="text-aegisaxiom-text-muted text-[10px] ml-2">
                              ({hours} hrs × ${sellRate}/hr)
                            </span>
                          </div>
                          <span className="font-semibold text-emerald-600 dark:text-emerald-400">
                            ${revenue.toLocaleString()}
                          </span>
                        </div>
                      );
                    })}

                    {/* Total Revenue */}
                    <div className="flex items-center justify-between text-sm bg-emerald-50 dark:bg-emerald-900/30 px-3 py-2 rounded border border-emerald-200 dark:border-emerald-700/30">
                      <div className="flex items-center gap-2">
                        <span className="text-emerald-700 dark:text-emerald-300 font-semibold">
                          Total Revenue Potential
                        </span>
                        <span className="text-[10px] text-aegisaxiom-text-secondary">
                          (
                          {(form.resource_types || []).reduce(
                            (sum, rt) => sum + (parseFloat(rt.hours_purchased) || 0),
                            0,
                          )}{' '}
                          total hours)
                        </span>
                      </div>
                      <span className="font-bold text-emerald-700 dark:text-emerald-300">
                        $
                        {(form.resource_types || [])
                          .reduce((sum, rt) => {
                            const hours = parseFloat(rt.hours_purchased) || 0;
                            const rate = parseFloat(rt.sell_rate) || 0;
                            return sum + hours * rate;
                          }, 0)
                          .toLocaleString()}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Cost Analysis (if resources assigned) */}
                {(form.resources || []).length > 0 && (
                  <div className="mb-4">
                    <div className="text-[10px] text-aegisaxiom-text-secondary font-semibold mb-2 uppercase">
                      Cost Model (Assigned Resources)
                    </div>
                    <div className="space-y-2">
                      {(form.resources || []).map((res, i) => {
                        const user = users.find((u) => u.user_id === res.user_id);
                        const costRate = parseFloat(res.cost_rate) || 0;

                        // Calculate hours from percentage
                        const resourceType = (form.resource_types || []).find(
                          (rt) => rt.id === res.resource_type_id,
                        );
                        const totalHours = parseFloat(resourceType?.hours_purchased) || 0;
                        const percentage = parseFloat(res.allocation_percentage) || 0;
                        const allocatedHours = (percentage / 100) * totalHours;

                        const totalCost = allocatedHours * costRate;

                        return (
                          <div
                            key={i}
                            className="flex items-center justify-between text-xs bg-aegisaxiom-elevated px-3 py-2 rounded"
                          >
                            <div className="flex-1">
                              <span className="text-aegisaxiom-text-secondary">{user?.name || 'Contractor'}</span>
                              <span className="text-aegisaxiom-text-muted text-[10px] ml-2">
                                {res.resource_type_name} ({percentage}% ={' '}
                                {allocatedHours.toFixed(1)} hrs × ${costRate}/hr)
                              </span>
                            </div>
                            <span className="font-semibold text-red-600 dark:text-red-400">
                              ${totalCost.toLocaleString()}
                            </span>
                          </div>
                        );
                      })}

                      {/* Total Cost */}
                      <div className="flex items-center justify-between text-sm bg-red-50 dark:bg-red-900/20 px-3 py-2 rounded border border-red-200 dark:border-red-700/30">
                        <span className="text-red-700 dark:text-red-300 font-semibold">Total Cost</span>
                        <span className="font-bold text-red-700 dark:text-red-300">
                          $
                          {(form.resources || [])
                            .reduce((sum, res) => {
                              const costRate = parseFloat(res.cost_rate) || 0;
                              const resourceType = (form.resource_types || []).find(
                                (rt) => rt.id === res.resource_type_id,
                              );
                              const totalHours = parseFloat(resourceType?.hours_purchased) || 0;
                              const percentage = parseFloat(res.allocation_percentage) || 0;
                              const allocatedHours = (percentage / 100) * totalHours;
                              return sum + allocatedHours * costRate;
                            }, 0)
                            .toLocaleString()}
                        </span>
                      </div>
                    </div>
                  </div>
                )}

                {/* Margin Analysis */}
                {(form.resources || []).length > 0 && (
                  <div className="bg-gradient-to-r from-emerald-900/20 to-blue-900/20 rounded-lg p-3 border border-emerald-200 dark:border-emerald-700/30">
                    {(() => {
                      const totalRevenue = (form.resource_types || []).reduce((sum, rt) => {
                        const hours = parseFloat(rt.hours_purchased) || 0;
                        const rate = parseFloat(rt.sell_rate) || 0;
                        return sum + hours * rate;
                      }, 0);

                      const totalCost = (form.resources || []).reduce((sum, res) => {
                        const costRate = parseFloat(res.cost_rate) || 0;
                        const resourceType = (form.resource_types || []).find(
                          (rt) => rt.id === res.resource_type_id,
                        );
                        const totalHours = parseFloat(resourceType?.hours_purchased) || 0;
                        const percentage = parseFloat(res.allocation_percentage) || 0;
                        const allocatedHours = (percentage / 100) * totalHours;
                        return sum + allocatedHours * costRate;
                      }, 0);

                      const margin = totalRevenue - totalCost;
                      const marginPercent =
                        totalRevenue > 0 ? ((margin / totalRevenue) * 100).toFixed(1) : 0;

                      return (
                        <>
                          <div className="flex items-center justify-between mb-2">
                            <span className="text-xs text-aegisaxiom-text-secondary">Gross Margin</span>
                            <div className="text-right">
                              <div className="text-lg font-bold text-aegisaxiom-text">
                                ${margin.toLocaleString()}
                              </div>
                              <div
                                className={`text-xs font-semibold ${
                                  marginPercent >= 30
                                    ? 'text-emerald-600 dark:text-emerald-400'
                                    : marginPercent >= 15
                                      ? 'text-amber-600 dark:text-amber-400'
                                      : 'text-red-600 dark:text-red-400'
                                }`}
                              >
                                {marginPercent}% Margin
                              </div>
                            </div>
                          </div>

                          {/* Margin Health Indicator */}
                          <div className="flex items-center gap-2 text-[10px]">
                            {marginPercent >= 30 ? (
                              <div className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
                                <CheckCircle size={12} />
                                <span>Healthy margin (≥30%)</span>
                              </div>
                            ) : marginPercent >= 15 ? (
                              <div className="flex items-center gap-1 text-amber-600 dark:text-amber-400">
                                <AlertTriangle size={12} />
                                <span>Acceptable margin (15-30%)</span>
                              </div>
                            ) : (
                              <div className="flex items-center gap-1 text-red-600 dark:text-red-400">
                                <AlertTriangle size={12} />
                                <span>Low margin (&lt;15%) - Review pricing</span>
                              </div>
                            )}
                          </div>

                          {/* Visual Breakdown */}
                          <div className="mt-3 pt-3 border-t border-aegisaxiom-border grid grid-cols-2 gap-2 text-xs">
                            <div>
                              <div className="text-[9px] text-aegisaxiom-text-muted mb-1">Revenue</div>
                              <div className="text-emerald-600 dark:text-emerald-400 font-semibold">
                                ${totalRevenue.toLocaleString()}
                              </div>
                            </div>
                            <div>
                              <div className="text-[9px] text-aegisaxiom-text-muted mb-1">Cost</div>
                              <div className="text-red-600 dark:text-red-400 font-semibold">
                                ${totalCost.toLocaleString()}
                              </div>
                            </div>
                          </div>
                        </>
                      );
                    })()}
                  </div>
                )}

                {/* Resource Blend Summary */}
                {(form.resource_types || []).length > 1 && (
                  <div className="mt-3 pt-3 border-t border-aegisaxiom-border">
                    <div className="text-[10px] text-aegisaxiom-text-secondary font-semibold mb-2 uppercase">
                      Resource Blend
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {(form.resource_types || []).map((rt, i) => {
                        const totalHours = (form.resource_types || []).reduce(
                          (sum, r) => sum + (parseFloat(r.hours_purchased) || 0),
                          0,
                        );
                        const rtHours = parseFloat(rt.hours_purchased) || 0;
                        const percentage =
                          totalHours > 0 ? ((rtHours / totalHours) * 100).toFixed(0) : 0;

                        return (
                          <div
                            key={i}
                            className="bg-aegisaxiom-elevated px-2 py-1 rounded text-[10px] text-aegisaxiom-text-secondary"
                          >
                            {rt.name}:{' '}
                            <span className="font-semibold text-blue-600 dark:text-blue-400">{percentage}%</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}

          <div className="mt-3 text-xs text-aegisaxiom-text-secondary text-center">
            Review the summary above before submitting the contract
          </div>
        </div>
      )}

      {/* Notes */}
      <div>
        <label className={`${labelClass} block mb-1`}>Notes</label>
        <textarea
          value={form.notes || ''}
          onChange={(e) => setForm({ ...form, notes: e.target.value })}
          className={inputClass}
          rows={compact ? 2 : 3}
          placeholder="Additional contract details..."
        />
      </div>
    </div>
  );
}
