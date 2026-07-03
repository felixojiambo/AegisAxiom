import React from 'react';
import { Plus, X } from 'lucide-react';

/**
 * Wholesale Products Section Component
 * Displays and manages wholesale MRR products in a contract
 */
export default function WholesaleProductsSection({
  form,
  products,
  productSearch,
  setProductSearch,
  productTypeFilter,
  setProductTypeFilter,
  loadingProducts,
  loadProducts,
  addProduct,
  updateProduct,
  removeProduct,
  getFilteredProducts,
  compact = false,
}) {
  const labelClass = compact ? 'text-xs text-aegisaxiom-text-secondary' : 'text-sm text-aegisaxiom-text-secondary';

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <label className={`${labelClass} font-semibold`}>Wholesale Products</label>
        <div className="flex items-center gap-2">
          {products.length > 0 && (
            <span className="text-xs text-aegisaxiom-text-muted">{products.length} available</span>
          )}
          <button
            type="button"
            onClick={loadProducts}
            disabled={loadingProducts}
            className="text-xs px-2 py-1 bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:bg-aegisaxiom-elevated/50 rounded flex items-center gap-1"
            title="Reload products"
          >
            {loadingProducts ? '...' : '↻'}
          </button>
          <button
            type="button"
            onClick={addProduct}
            className="text-xs px-2 py-1 bg-cyan-600/20 text-cyan-600 dark:text-cyan-400 hover:bg-cyan-600/30 rounded flex items-center gap-1"
          >
            <Plus size={12} /> Add Product
          </button>
        </div>
      </div>

      {(form.products || []).length === 0 && (
        <div className="bg-cyan-50 dark:bg-cyan-900/10 border border-cyan-800/30 rounded-lg p-4 text-center">
          <p className="text-xs text-aegisaxiom-text-secondary">
            No products added yet. Click "Add Product" to get started.
          </p>
        </div>
      )}

      {(form.products || []).length > 0 && (
        <div className="space-y-3">
          {(form.products || []).map((product, i) => {
            const filteredProducts = getFilteredProducts();
            const margin = (product.sell_price || 0) - (product.cost_price || 0);
            const marginPercent =
              product.sell_price > 0 ? ((margin / product.sell_price) * 100).toFixed(1) : 0;

            return (
              <div
                key={i}
                className="bg-aegisaxiom-accent-muted border border-aegisaxiom-border rounded-lg p-3 space-y-2"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-cyan-700 dark:text-cyan-300">Product {i + 1}</span>
                  <button
                    type="button"
                    onClick={() => removeProduct(i)}
                    className="text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300"
                  >
                    <X size={14} />
                  </button>
                </div>

                {/* Product Search & Filter */}
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[10px] text-aegisaxiom-text-muted block mb-1">Product Type</label>
                    <select
                      value={productTypeFilter}
                      onChange={(e) => setProductTypeFilter(e.target.value)}
                      className="w-full px-2 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-cyan-500"
                    >
                      <option value="all">All Types</option>
                      <option value="simplisecure">SimpliSecure</option>
                      <option value="simplicomply">SimpliComply</option>
                      <option value="custom">Custom/Other</option>
                    </select>
                  </div>
                  <div>
                    <label className="text-[10px] text-aegisaxiom-text-muted block mb-1">
                      Search (Name/SKU)
                    </label>
                    <input
                      type="text"
                      placeholder="e.g., 'XDR' or 'SimpliSecure'"
                      value={productSearch}
                      onChange={(e) => setProductSearch(e.target.value)}
                      className="w-full px-2 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-cyan-500"
                    />
                  </div>
                </div>

                {/* Product Selection Dropdown */}
                <div>
                  <label className="text-[10px] text-aegisaxiom-text-muted block mb-1">Select Product *</label>
                  <select
                    value={product.product_id || ''}
                    onChange={(e) => updateProduct(i, 'product_id', e.target.value)}
                    className="w-full px-2 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-cyan-500"
                  >
                    <option value="">Select a product...</option>
                    {loadingProducts && <option disabled>Loading products...</option>}
                    {!loadingProducts && products.length === 0 && (
                      <option disabled>
                        No products available. Add products first in Admin → Products
                      </option>
                    )}
                    {!loadingProducts && filteredProducts.length === 0 && products.length > 0 && (
                      <option disabled>No products match your filters</option>
                    )}
                    {filteredProducts.map((p) => (
                      <option key={p.product_id} value={p.product_id}>
                        {p.name} - {p.sku} (Cost: ${p.cost_monthly}/mo)
                      </option>
                    ))}
                  </select>
                </div>

                {product.product_id && (
                  <>
                    {/* Pricing Tier Selection */}
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="text-[10px] text-aegisaxiom-text-muted block mb-1">
                          Pricing Tier
                        </label>
                        <select
                          value={product.pricing_tier || 'monthly'}
                          onChange={(e) => updateProduct(i, 'pricing_tier', e.target.value)}
                          className="w-full px-2 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none"
                        >
                          <option value="monthly">Monthly</option>
                          <option value="quarterly">Quarterly</option>
                          <option value="annual">Annual</option>
                        </select>
                      </div>
                      <div>
                        <label className="text-[10px] text-aegisaxiom-text-muted block mb-1">Quantity</label>
                        <input
                          type="number"
                          min="1"
                          value={product.quantity || 1}
                          onChange={(e) =>
                            updateProduct(i, 'quantity', parseInt(e.target.value) || 1)
                          }
                          className="w-full px-2 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none"
                        />
                      </div>
                    </div>

                    {/* Pricing Display */}
                    <div className="grid grid-cols-3 gap-2">
                      <div>
                        <label className="text-[10px] text-red-600 dark:text-red-400 block mb-1">
                          Cost Price (Locked)
                        </label>
                        <div className="px-2 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text-secondary">
                          ${(product.cost_price || 0).toFixed(2)}
                        </div>
                      </div>
                      <div>
                        <label className="text-[10px] text-emerald-600 dark:text-emerald-400 block mb-1">
                          Sell Price (Editable)
                        </label>
                        <input
                          type="number"
                          step="0.01"
                          min="0"
                          value={product.sell_price || 0}
                          onChange={(e) =>
                            updateProduct(i, 'sell_price', parseFloat(e.target.value) || 0)
                          }
                          className="w-full px-2 py-1.5 bg-aegisaxiom-surface border border-emerald-200 dark:border-emerald-700/50 rounded text-xs text-aegisaxiom-text outline-none focus:border-emerald-500"
                        />
                      </div>
                      <div>
                        <label className="text-[10px] text-violet-600 dark:text-violet-400 block mb-1">Margin</label>
                        <div
                          className={`px-2 py-1.5 bg-aegisaxiom-surface border rounded text-xs font-medium ${
                            margin > 0
                              ? 'text-emerald-600 dark:text-emerald-400 border-emerald-200 dark:border-emerald-700/50'
                              : 'text-red-600 dark:text-red-400 border-red-200 dark:border-red-700/50'
                          }`}
                        >
                          ${margin.toFixed(2)} ({marginPercent}%)
                        </div>
                      </div>
                    </div>

                    {/* Product Summary */}
                    <div className="bg-aegisaxiom-surface rounded p-2 text-[10px] text-aegisaxiom-text-secondary">
                      <div className="flex items-center justify-between">
                        <span>SKU: {product.sku}</span>
                        <span className="font-medium text-aegisaxiom-text">
                          Total: ${((product.sell_price || 0) * (product.quantity || 1)).toFixed(2)}
                          /{product.pricing_tier}
                        </span>
                      </div>
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
