import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/hooks/usePermissions';
import {
  Plus,
  Edit2,
  Trash2,
  X,
  DollarSign,
  Package,
  TrendingUp,
  Upload,
  Wrench,
  Briefcase,
  Cpu,
  Package2,
  Shield,
} from 'lucide-react';
import { toast } from 'sonner';
import API_URL from '../lib/apiConfig';
import TabStrip from './shared/TabStrip';

const API = API_URL;
const fmt = (n) =>
  `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const PRODUCT_CATEGORIES = {
  services: {
    label: 'Services',
    icon: Wrench,
    iconColor: 'text-emerald-600 dark:text-emerald-400',
    iconBg: 'bg-emerald-50 dark:bg-emerald-600/20',
    color:
      'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border-emerald-500/30',
    description:
      'Your owned professional and field services (installation, consulting, maintenance)',
    pricing_model: 'flexible', // Can be recurring OR one-time
  },
  wholesale_mrr: {
    label: 'Wholesale MRR',
    icon: Briefcase,
    iconColor: 'text-violet-600 dark:text-violet-400',
    iconBg: 'bg-violet-50 dark:bg-violet-600/20',
    color:
      'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border-violet-500/30',
    description: 'Recurring revenue products and managed services (resale)',
    pricing_model: 'recurring', // Monthly/Quarterly/Annual only
  },
  hardware: {
    label: 'Hardware',
    icon: Cpu,
    iconColor: 'text-blue-600 dark:text-blue-400',
    iconBg: 'bg-blue-50 dark:bg-blue-600/20',
    color: 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300 border-blue-500/30',
    description: 'Network equipment, servers, and physical devices',
    pricing_model: 'one_time', // One-time purchase only
  },
  materials: {
    label: 'Materials',
    icon: Package2,
    iconColor: 'text-amber-600 dark:text-amber-400',
    iconBg: 'bg-amber-50 dark:bg-amber-600/20',
    color:
      'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border-amber-500/30',
    description: 'Installation materials, cables, and consumables',
    pricing_model: 'one_time', // One-time purchase only
  },
};

const PRODUCT_TYPES = {
  // Services Types (NEW - for Field Services & Professional Services)
  installation: {
    label: 'Installation',
    color:
      'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border-emerald-500/30',
    category: 'services',
  },
  maintenance: {
    label: 'Maintenance',
    color:
      'bg-green-50 dark:bg-green-600/20 text-green-700 dark:text-green-300 border-green-500/30',
    category: 'services',
  },
  consulting: {
    label: 'Consulting',
    color: 'bg-teal-600/20 text-teal-700 dark:text-teal-300 border-teal-500/30',
    category: 'services',
  },
  design: {
    label: 'Design',
    color: 'bg-cyan-600/20 text-cyan-700 dark:text-cyan-300 border-cyan-500/30',
    category: 'services',
  },
  support: {
    label: 'Support',
    color: 'bg-lime-600/20 text-lime-300 border-lime-500/30',
    category: 'services',
  },

  // Wholesale MRR Types
  simplisecure: {
    label: 'SimpliSecure',
    color: 'bg-cyan-600/20 text-cyan-700 dark:text-cyan-300 border-cyan-500/30',
    category: 'wholesale_mrr',
  },
  simplicomply: {
    label: 'SimpliComply',
    color: 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300 border-blue-500/30',
    category: 'wholesale_mrr',
  },
  microsoft365: {
    label: 'Microsoft 365',
    color: 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300 border-blue-500/30',
    category: 'wholesale_mrr',
  },
  custom: {
    label: 'Custom Product',
    color: 'bg-purple-600/20 text-purple-300 border-purple-500/30',
    category: 'wholesale_mrr',
  },

  // Hardware Types
  networking: {
    label: 'Networking',
    color: 'bg-sky-600/20 text-sky-700 dark:text-sky-300 border-sky-500/30',
    category: 'hardware',
  },
  servers: {
    label: 'Servers',
    color: 'bg-indigo-600/20 text-indigo-700 dark:text-indigo-300 border-indigo-500/30',
    category: 'hardware',
  },
  security_appliance: {
    label: 'Security Appliance',
    color:
      'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border-violet-500/30',
    category: 'hardware',
  },

  // Materials Types
  cables: {
    label: 'Cables',
    color:
      'bg-orange-50 dark:bg-orange-600/20 text-orange-700 dark:text-orange-300 border-orange-500/30',
    category: 'materials',
  },
  tools: {
    label: 'Tools',
    color:
      'bg-yellow-50 dark:bg-yellow-600/20 text-yellow-700 dark:text-yellow-300 border-yellow-500/30',
    category: 'materials',
  },
  accessories: {
    label: 'Accessories',
    color:
      'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border-amber-500/30',
    category: 'materials',
  },
};

const BILLING_PERIODS = {
  daily: { label: 'Daily', months: 1 / 30 },
  weekly: { label: 'Weekly', months: 1 / 4.33 },
  monthly: { label: 'Monthly', months: 1 },
  quarterly: { label: 'Quarterly', months: 3 },
  annual: { label: 'Annual', months: 12 },
};

// Services-specific billing periods (more granular)
const SERVICE_BILLING_PERIODS = {
  daily: { label: 'Daily', months: 1 / 30 },
  weekly: { label: 'Weekly', months: 1 / 4.33 },
  monthly: { label: 'Monthly', months: 1 },
  quarterly: { label: 'Quarterly', months: 3 },
  annual: { label: 'Annual', months: 12 },
};

// Wholesale MRR billing periods (standard)
const MRR_BILLING_PERIODS = {
  monthly: { label: 'Monthly', months: 1 },
  quarterly: { label: 'Quarterly', months: 3 },
  annual: { label: 'Annual', months: 12 },
};

export default function ProductsServices() {
  const { user } = useAuth();
  const [products, setProducts] = useState([]);
  const [distributors, setDistributors] = useState([]);
  const [loading, setLoading] = useState(false);
  const [initialLoad, setInitialLoad] = useState(true);
  const [editing, setEditing] = useState(null);
  const [selectedCategory, setSelectedCategory] = useState('services'); // Default to Services category
  const [showBulkImport, setShowBulkImport] = useState(false);
  const [bulkImportMethod, setBulkImportMethod] = useState('ai'); // 'ai' | 'csv'
  const [bulkText, setBulkText] = useState('');
  const [parsedProducts, setParsedProducts] = useState([]);
  const [editingParsedIndex, setEditingParsedIndex] = useState(null); // Track which parsed product is being edited
  const [importResults, setImportResults] = useState(null); // { success: number, failed: number, errors: [] }
  const [form, setForm] = useState({
    name: '',
    description: '',
    sku: '',
    product_type: 'custom',
    item_category: 'services',
    retail_pricing: { daily: '', weekly: '', monthly: '', quarterly: '', annual: '', one_time: '' },
    msp_pricing: { daily: '', weekly: '', monthly: '', quarterly: '', annual: '', one_time: '' },
    cost_monthly: '',
    cost_one_time: '',
    distributor_id: '',
    distributor_sku: '',
    is_active: true,
  });

  const { isAdmin: isWildcardAdmin } = usePermissions();
  const isAdmin = isWildcardAdmin();

  useEffect(() => {
    if (isAdmin) loadProducts();
  }, [isAdmin, selectedCategory]); // Reload when category changes

  useEffect(() => {
    if (isAdmin) loadDistributors();
  }, [isAdmin]);

  const loadProducts = async () => {
    try {
      const res = await api.get(`${API}/api/admin/products?item_category=${selectedCategory}`);
      if (res.ok) {
        const data = await res.json();
        setProducts(data.products || []); // Fixed: backend returns 'products' not 'items'
      }
    } catch (err) {
      toast.error('Failed to load products');
    } finally {
      setInitialLoad(false);
    }
  };

  const loadDistributors = async () => {
    try {
      const res = await api.get(`${API}/api/admin/distributors`);
      if (res.ok) {
        const data = await res.json();
        setDistributors(data.items || []);
      }
    } catch (err) {
      // Distributor dropdown is optional — fall back to empty but still log.
      console.error('Failed to load distributors:', err);
    }
  };

  const distributorName = (id) => {
    if (!id) return null;
    const d = distributors.find((x) => x.distributor_id === id);
    return d ? d.name : null;
  };

  const openEdit = (product) => {
    setForm({
      name: product.name,
      description: product.description || '',
      sku: product.sku,
      product_type: product.product_type,
      item_category: product.item_category || 'wholesale_mrr', // Default to wholesale_mrr for legacy products
      retail_pricing: product.retail_pricing || { monthly: 0, quarterly: 0, annual: 0 },
      msp_pricing: product.msp_pricing || { monthly: 0, quarterly: 0, annual: 0 },
      cost_monthly: product.cost_monthly,
      distributor_id: product.distributor_id || '',
      distributor_sku: product.distributor_sku || '',
      is_active: product.is_active,
    });
    setEditing(product.product_id);
  };

  const openNew = () => {
    setForm({
      name: '',
      description: '',
      sku: '',
      product_type: 'custom',
      item_category: selectedCategory, // Auto-set to current category filter
      retail_pricing: { monthly: '', quarterly: '', annual: '' },
      msp_pricing: { monthly: '', quarterly: '', annual: '' },
      cost_monthly: '',
      distributor_id: '',
      distributor_sku: '',
      is_active: true,
    });
    setEditing('new');
  };

  const handleSave = async () => {
    if (!form.name || !form.sku || !form.cost_monthly) {
      toast.error('Please fill in name, SKU, and cost');
      return;
    }

    try {
      setLoading(true);
      const url =
        editing === 'new' ? `${API}/api/admin/products` : `${API}/api/admin/products/${editing}`;
      const method = editing === 'new' ? 'POST' : 'PUT';

      const payload = {
        name: form.name,
        description: form.description,
        sku: form.sku,
        product_type: form.product_type,
        item_category: form.item_category,
        retail_pricing: {
          monthly: parseFloat(form.retail_pricing.monthly) || 0,
          quarterly: parseFloat(form.retail_pricing.quarterly) || 0,
          annual: parseFloat(form.retail_pricing.annual) || 0,
        },
        msp_pricing: {
          monthly: parseFloat(form.msp_pricing.monthly) || 0,
          quarterly: parseFloat(form.msp_pricing.quarterly) || 0,
          annual: parseFloat(form.msp_pricing.annual) || 0,
        },
        cost_monthly: parseFloat(form.cost_monthly) || 0,
        cost_one_time: parseFloat(form.cost_one_time) || 0,
        distributor_id: form.distributor_id || null,
        distributor_sku: form.distributor_sku || null,
        is_active: form.is_active,
      };

      const res = await api[method.toLowerCase()](url, payload);

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to save product');
      }

      toast.success(editing === 'new' ? 'Product created!' : 'Product updated!');
      setEditing(null);
      loadProducts();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (productId) => {
    if (!confirm('Are you sure you want to delete this product?')) return;

    try {
      const res = await api.delete(`${API}/api/admin/products/${productId}`);
      if (!res.ok) throw new Error('Failed to delete product');
      toast.success('Product deleted');
      loadProducts();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const parseWithAI = async () => {
    if (!bulkText.trim()) {
      toast.error('Please enter product information');
      return;
    }

    try {
      setLoading(true);
      const res = await api.post(`${API}/api/admin/products/parse-bulk`, {
        text: bulkText,
      });

      if (!res.ok) {
        let errorMessage = 'Failed to parse products';
        try {
          const err = await res.json();
          errorMessage = err?.detail || errorMessage;
        } catch (e) {
          // Response wasn't JSON, use status text
          errorMessage = res.statusText || errorMessage;
        }
        throw new Error(errorMessage);
      }

      const data = await res.json();
      setParsedProducts(data.products || []);
      toast.success(`Parsed ${data.products.length} products! Review and save.`);
    } catch (err) {
      console.error('AI Parse Error:', err);
      toast.error(err.message || 'Failed to parse products');
    } finally {
      setLoading(false);
    }
  };

  const handleCSVUpload = (event) => {
    const file = event.target.files[0];
    if (!file) return;

    toast.info('Parsing CSV file...');

    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const text = e.target.result;
        const lines = text.split('\n').filter((l) => l.trim());

        if (lines.length < 2) {
          toast.error('CSV file is empty or has no data rows');
          return;
        }

        // Parse CSV (assuming header row)
        const headers = lines[0].split(',').map((h) => h.trim());

        const products = [];

        for (let i = 1; i < lines.length; i++) {
          const values = lines[i].split(',').map((v) => v.trim());
          if (values.length < headers.length) continue;

          const product = {
            name: values[headers.indexOf('name')] || '',
            description: values[headers.indexOf('description')] || '',
            sku: values[headers.indexOf('sku')] || '',
            product_type: values[headers.indexOf('product_type')] || 'custom',
            retail_pricing: {
              monthly: parseFloat(values[headers.indexOf('retail_monthly')]) || 0,
              quarterly: parseFloat(values[headers.indexOf('retail_quarterly')]) || 0,
              annual: parseFloat(values[headers.indexOf('retail_annual')]) || 0,
            },
            msp_pricing: {
              monthly: parseFloat(values[headers.indexOf('msp_monthly')]) || 0,
              quarterly: parseFloat(values[headers.indexOf('msp_quarterly')]) || 0,
              annual: parseFloat(values[headers.indexOf('msp_annual')]) || 0,
            },
            cost_monthly: parseFloat(values[headers.indexOf('cost_monthly')]) || 0,
            is_active: true,
          };

          if (product.name && product.sku) {
            products.push(product);
          }
        }

        if (products.length === 0) {
          toast.error(
            'No valid products found in CSV. Check format and that name & sku columns exist.',
          );
          return;
        }

        setParsedProducts(products);
        toast.success(
          `✅ Parsed ${products.length} products! Review below and click "Create Products".`,
        );
      } catch (err) {
        console.error('[CSV Upload] Parse error:', err);
        toast.error('Failed to parse CSV: ' + err.message);
      }
    };

    reader.onerror = () => {
      toast.error('Failed to read file');
    };

    reader.readAsText(file);

    // Reset file input so same file can be selected again
    event.target.value = '';
  };

  const saveBulkProducts = async () => {
    if (parsedProducts.length === 0) {
      toast.error('No products to save');
      return;
    }

    try {
      setLoading(true);
      let successCount = 0;
      let errorCount = 0;
      const errors = [];

      // Process each product with progress updates
      for (let i = 0; i < parsedProducts.length; i++) {
        const product = parsedProducts[i];
        try {
          const res = await api.post(`${API}/api/admin/products`, product);
          if (res.ok) {
            successCount++;
          } else {
            const errorData = await res.json();
            errorCount++;
            errors.push({ product: product.name, error: errorData.detail || 'Failed to create' });
          }
        } catch (err) {
          errorCount++;
          errors.push({ product: product.name, error: err.message || 'Network error' });
        }
      }

      // Set results to show in UI
      setImportResults({
        success: successCount,
        failed: errorCount,
        errors: errors,
      });

      // Reload products to show new additions
      await loadProducts();

      // Show toast notification
      if (errorCount === 0) {
        toast.success(`🎉 Successfully created ${successCount} products!`);
      } else if (successCount > 0) {
        toast.warning(
          `Created ${successCount} products, but ${errorCount} failed. See details below.`,
        );
      } else {
        toast.error(`Failed to create all ${errorCount} products. See details below.`);
      }
    } catch (err) {
      toast.error('Failed to save products: ' + err.message);
    } finally {
      setLoading(false);
    }
  };

  const closeBulkImportModal = () => {
    setShowBulkImport(false);
    setBulkText('');
    setParsedProducts([]);
    setEditingParsedIndex(null);
    setImportResults(null);
  };

  const updateParsedProduct = (index, updates) => {
    setParsedProducts((prev) => prev.map((p, i) => (i === index ? { ...p, ...updates } : p)));
  };

  const validateParsedProduct = (product) => {
    const errors = [];
    if (!product.name || product.name.trim() === '') errors.push('Name is required');
    if (!product.sku || product.sku.trim() === '') errors.push('SKU is required');
    if (!product.cost_monthly || product.cost_monthly <= 0)
      errors.push('Cost must be greater than 0');
    return errors;
  };

  const calcMargin = (price, costMonthly, months) => {
    const totalCost = costMonthly * months;
    const margin = price - totalCost;
    const marginPercent = price > 0 ? (margin / price) * 100 : 0;
    return { margin, marginPercent };
  };

  if (!isAdmin) {
    return (
      <div className="p-4 sm:p-6">
        <div className="bg-red-50 dark:bg-red-900/20 border border-red-500/30 rounded-lg p-4 text-red-700 dark:text-red-300 text-sm flex items-center gap-2">
          <Shield size={16} className="shrink-0" />
          <span>Admin access required</span>
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 sm:p-6 space-y-5">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-lg sm:text-xl font-bold text-aegisaxiom-text flex items-center gap-2">
            <Package size={18} className="text-violet-600 dark:text-violet-400 shrink-0" />
            Products &amp; Services
          </h1>
          <p className="text-xs sm:text-sm text-aegisaxiom-text-secondary mt-1">
            Unified catalog for Wholesale MRR, Hardware, and Materials
          </p>
        </div>
        <div className="flex items-center gap-2 w-full sm:w-auto">
          <button
            onClick={() => setShowBulkImport(true)}
            className="flex-1 sm:flex-none flex items-center justify-center gap-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface border border-aegisaxiom-border text-aegisaxiom-text text-sm px-4 py-2 rounded-lg transition"
          >
            <Upload size={15} />
            <span className="whitespace-nowrap">Bulk Import</span>
          </button>
          <button
            onClick={openNew}
            className="flex-1 sm:flex-none flex items-center justify-center gap-2 bg-violet-600 hover:bg-violet-500 text-white text-sm px-4 py-2 rounded-lg transition shrink-0"
          >
            <Plus size={16} />
            <span className="whitespace-nowrap">Add Product</span>
          </button>
        </div>
      </div>

      {/* Category Tabs */}
      <TabStrip
        variant="pill"
        size="sm"
        stretch
        activeId={selectedCategory}
        onChange={setSelectedCategory}
        tabs={Object.entries(PRODUCT_CATEGORIES).map(([key, config]) => ({
          id: key,
          label: config.label,
          icon: config.icon,
        }))}
      />

      {/* Category Description */}
      <p className="text-xs sm:text-sm text-aegisaxiom-text-muted -mt-1">
        {PRODUCT_CATEGORIES[selectedCategory].description}
      </p>

      {/* Products List */}
      <div className="space-y-3">
        {initialLoad ? (
          <div className="flex items-center justify-center py-16">
            <div className="w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : products.length === 0 ? (
          (() => {
            const cat = PRODUCT_CATEGORIES[selectedCategory];
            const Icon = cat.icon;
            return (
              <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-8 sm:p-10 text-center">
                <div
                  className={`inline-flex items-center justify-center w-12 h-12 rounded-full ${cat.iconBg} mb-3`}
                >
                  <Icon size={22} className={cat.iconColor} />
                </div>
                <p className="text-sm font-medium text-aegisaxiom-text mb-1">
                  No {cat.label} products yet
                </p>
                <p className="text-xs text-aegisaxiom-text-muted">
                  Click “Add Product” to create your first one.
                </p>
              </div>
            );
          })()
        ) : null}

        {products.map((product) => {
          const typeConfig = PRODUCT_TYPES[product.product_type] || PRODUCT_TYPES.custom;

          return (
            <div
              key={product.product_id}
              className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4"
            >
              {/* Header */}
              <div className="flex items-start justify-between mb-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="text-base font-semibold text-aegisaxiom-text">{product.name}</h3>
                    <span
                      className={`inline-flex px-2 py-0.5 rounded text-xs border ${typeConfig.color}`}
                    >
                      {typeConfig.label}
                    </span>
                  </div>
                  <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">SKU: {product.sku}</p>
                  {distributorName(product.distributor_id) && (
                    <p className="text-xs text-aegisaxiom-text-muted mt-0.5">
                      Distributor: {distributorName(product.distributor_id)}
                      {product.distributor_sku ? ` (SKU: ${product.distributor_sku})` : ''}
                    </p>
                  )}
                  {product.description && (
                    <p className="text-xs text-aegisaxiom-text-muted mt-1">{product.description}</p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => openEdit(product)}
                    className="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:text-blue-300 transition"
                  >
                    <Edit2 size={14} />
                  </button>
                  <button
                    onClick={() => handleDelete(product.product_id)}
                    className="text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300 transition"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>

              {/* Pricing Display - Conditional */}
              {product.item_category === 'hardware' || product.item_category === 'materials' ? (
                /* One-Time Display for Hardware/Materials */
                <div className="bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-700/30 rounded-lg p-3 mt-3">
                  <div className="grid grid-cols-3 gap-4 text-center">
                    <div>
                      <div className="text-xs text-aegisaxiom-text-secondary">Retail</div>
                      <div className="text-base font-bold text-aegisaxiom-text mt-1">
                        ${(product.retail_pricing?.one_time || 0).toFixed(2)}
                      </div>
                      <div className="text-xs text-aegisaxiom-text-muted">one-time</div>
                    </div>
                    <div>
                      <div className="text-xs text-aegisaxiom-text-secondary">MSP</div>
                      <div className="text-base font-bold text-emerald-600 dark:text-emerald-400 mt-1">
                        ${(product.msp_pricing?.one_time || 0).toFixed(2)}
                      </div>
                      <div className="text-xs text-aegisaxiom-text-muted">one-time</div>
                    </div>
                    <div>
                      <div className="text-xs text-aegisaxiom-text-secondary">Cost</div>
                      <div className="text-base font-bold text-red-600 dark:text-red-400 mt-1">
                        ${(product.cost_one_time || 0).toFixed(2)}
                      </div>
                      <div className="text-xs text-aegisaxiom-text-muted">one-time</div>
                    </div>
                  </div>
                </div>
              ) : (
                /* Recurring Pricing Display for Services/Wholesale MRR */
                <div className="grid grid-cols-3 gap-3 mt-3">
                  {Object.entries(BILLING_PERIODS).map(([key, period]) => {
                    const retailPrice = product.retail_pricing?.[key] || 0;
                    const mspPrice = product.msp_pricing?.[key] || 0;
                    const costMonthly = product.cost_monthly || 0;

                    const retailCalc = calcMargin(retailPrice, costMonthly, period.months);
                    const mspCalc = calcMargin(mspPrice, costMonthly, period.months);

                    return (
                      <div
                        key={key}
                        className="bg-aegisaxiom-accent-muted border border-aegisaxiom-border rounded-lg p-3"
                      >
                        <div className="text-xs font-medium text-aegisaxiom-text-secondary mb-2">
                          {period.label}
                        </div>

                        {/* Retail */}
                        <div className="mb-2">
                          <div className="flex items-center justify-between text-xs mb-0.5">
                            <span className="text-emerald-600 dark:text-emerald-400">Retail</span>
                            <span className="text-aegisaxiom-text font-medium">{fmt(retailPrice)}</span>
                          </div>
                          <div className="flex items-center justify-between text-[10px]">
                            <span className="text-aegisaxiom-text-muted">Margin</span>
                            <span
                              className={
                                retailCalc.margin > 0
                                  ? 'text-emerald-600 dark:text-emerald-400'
                                  : 'text-red-600 dark:text-red-400'
                              }
                            >
                              {fmt(retailCalc.margin)} ({retailCalc.marginPercent.toFixed(1)}%)
                            </span>
                          </div>
                        </div>

                        {/* MSP */}
                        <div className="pt-2 border-t border-aegisaxiom-border">
                          <div className="flex items-center justify-between text-xs mb-0.5">
                            <span className="text-blue-600 dark:text-blue-400">MSP</span>
                            <span className="text-aegisaxiom-text font-medium">{fmt(mspPrice)}</span>
                          </div>
                          <div className="flex items-center justify-between text-[10px]">
                            <span className="text-aegisaxiom-text-muted">Margin</span>
                            <span
                              className={
                                mspCalc.margin > 0
                                  ? 'text-emerald-600 dark:text-emerald-400'
                                  : 'text-red-600 dark:text-red-400'
                              }
                            >
                              {fmt(mspCalc.margin)} ({mspCalc.marginPercent.toFixed(1)}%)
                            </span>
                          </div>
                        </div>

                        {/* Cost */}
                        <div className="mt-2 pt-2 border-t border-aegisaxiom-border text-[10px] text-aegisaxiom-text-muted">
                          Cost: {fmt(costMonthly * period.months)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Edit/Create Modal */}
      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 overflow-y-auto">
          <div className="absolute inset-0 bg-black/60" />
          <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-4xl my-8">
            <div className="bg-aegisaxiom-surface backdrop-blur border-b border-aegisaxiom-border p-4 flex justify-between items-center">
              <h2 className="text-sm font-semibold text-aegisaxiom-text flex items-center gap-2">
                <Package size={16} className="text-violet-600 dark:text-violet-400" />
                {editing === 'new' ? 'Add Product' : 'Edit Product'}
              </h2>
              <button
                onClick={() => setEditing(null)}
                className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
              >
                <X size={18} />
              </button>
            </div>

            <div className="p-6 space-y-4 max-h-[calc(100vh-200px)] overflow-y-auto">
              {/* Basic Info */}
              <div className="grid grid-cols-2 gap-4">
                <div className="col-span-2">
                  <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                    Product Name *
                  </label>
                  <input
                    type="text"
                    value={form.name}
                    onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                    placeholder="e.g., SimpliSecure Standard"
                    className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                    required
                  />
                </div>

                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary block mb-1">SKU *</label>
                  <input
                    type="text"
                    value={form.sku}
                    onChange={(e) => setForm((f) => ({ ...f, sku: e.target.value }))}
                    placeholder="SS-STD-001"
                    className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                    required
                  />
                </div>

                {/* Category Selection */}
                <div className="col-span-2">
                  <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-2">
                    Category *
                  </label>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    {Object.entries(PRODUCT_CATEGORIES).map(([key, config]) => {
                      const Icon = config.icon;
                      return (
                        <button
                          key={key}
                          type="button"
                          onClick={() => {
                            setForm((f) => ({
                              ...f,
                              item_category: key,
                              product_type: 'custom', // Reset type when category changes
                            }));
                          }}
                          className={`flex items-center justify-center gap-2 px-3 py-2 text-xs font-medium rounded-lg border transition ${
                            form.item_category === key
                              ? 'bg-violet-600 text-white border-violet-500'
                              : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary border-aegisaxiom-border hover:border-aegisaxiom-border-hover'
                          }`}
                        >
                          <Icon size={14} className="shrink-0" />
                          <span className="truncate">{config.label}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Product Type (filtered by category) */}
                <div className="col-span-2">
                  <label className="text-xs text-aegisaxiom-text-secondary block mb-2">
                    Product Type *
                  </label>
                  <div className="grid grid-cols-4 gap-2">
                    {Object.entries(PRODUCT_TYPES)
                      .filter(([_, cfg]) => cfg.category === form.item_category)
                      .map(([key, cfg]) => (
                        <button
                          key={key}
                          type="button"
                          onClick={() => setForm((f) => ({ ...f, product_type: key }))}
                          className={`p-2 rounded-lg border text-xs transition ${
                            form.product_type === key
                              ? cfg.color + ' border-current font-medium'
                              : 'border-aegisaxiom-border text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover'
                          }`}
                        >
                          {cfg.label}
                        </button>
                      ))}
                  </div>
                </div>

                <div className="col-span-2">
                  <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Description</label>
                  <textarea
                    value={form.description}
                    onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                    placeholder="Product description..."
                    rows={2}
                    className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500 resize-none"
                  />
                </div>

                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Distributor</label>
                  <select
                    value={form.distributor_id || ''}
                    onChange={(e) => setForm((f) => ({ ...f, distributor_id: e.target.value }))}
                    className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                  >
                    <option value="">— None —</option>
                    {distributors.map((d) => (
                      <option key={d.distributor_id} value={d.distributor_id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                    Distributor SKU
                  </label>
                  <input
                    type="text"
                    value={form.distributor_sku || ''}
                    onChange={(e) => setForm((f) => ({ ...f, distributor_sku: e.target.value }))}
                    placeholder="SKU as known by distributor"
                    className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                  />
                </div>
              </div>

              {/* Cost - Conditional based on pricing model */}
              <div className="bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800/30 rounded-lg p-4">
                {PRODUCT_CATEGORIES[form.item_category].pricing_model === 'one_time' ? (
                  <>
                    <label className="text-xs text-red-700 dark:text-red-300 font-medium block mb-2">
                      One-Time Cost *
                    </label>
                    <input
                      type="number"
                      step="0.01"
                      min="0"
                      value={form.cost_one_time || ''}
                      onChange={(e) =>
                        setForm((f) => ({ ...f, cost_one_time: e.target.value, cost_monthly: 0 }))
                      }
                      placeholder="350.00"
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                      required
                    />
                    <p className="text-xs text-aegisaxiom-text-muted mt-1">
                      Your cost for this item (one-time)
                    </p>
                  </>
                ) : (
                  <>
                    <label className="text-xs text-red-700 dark:text-red-300 font-medium block mb-2">
                      Monthly Cost *
                    </label>
                    <input
                      type="number"
                      step="0.01"
                      min="0"
                      value={form.cost_monthly || ''}
                      onChange={(e) =>
                        setForm((f) => ({ ...f, cost_monthly: e.target.value, cost_one_time: 0 }))
                      }
                      placeholder="15.00"
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                      required
                    />
                    <p className="text-xs text-aegisaxiom-text-muted mt-1">
                      Base monthly cost for this product/service
                    </p>
                  </>
                )}
              </div>

              {/* Retail Pricing - Conditional */}
              {PRODUCT_CATEGORIES[form.item_category].pricing_model === 'one_time' ? (
                <div className="bg-emerald-50 dark:bg-emerald-900/10 border border-emerald-200 dark:border-emerald-800/30 rounded-lg p-4">
                  <label className="text-xs text-emerald-700 dark:text-emerald-300 font-medium block mb-2">
                    Retail Price (One-Time)
                  </label>
                  <div className="flex items-center gap-1">
                    <span className="text-aegisaxiom-text-muted">$</span>
                    <input
                      type="number"
                      step="0.01"
                      value={form.retail_pricing.one_time || ''}
                      onChange={(e) =>
                        setForm((f) => ({
                          ...f,
                          retail_pricing: {
                            one_time: parseFloat(e.target.value) || 0,
                            monthly: 0,
                            quarterly: 0,
                            annual: 0,
                          },
                        }))
                      }
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
                      placeholder="599.00"
                    />
                  </div>
                </div>
              ) : (
                <div className="bg-emerald-50 dark:bg-emerald-900/10 border border-emerald-200 dark:border-emerald-800/30 rounded-lg p-4">
                  <label className="text-xs text-emerald-700 dark:text-emerald-300 font-medium block mb-3">
                    Retail Pricing (Recurring)
                  </label>

                  {/* Services: Show all 5 periods + one-time */}
                  {form.item_category === 'services' ? (
                    <>
                      <div className="grid grid-cols-5 gap-2">
                        {Object.entries(SERVICE_BILLING_PERIODS).map(([key, period]) => (
                          <div key={key}>
                            <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                              {period.label}
                            </label>
                            <input
                              type="number"
                              step="0.01"
                              value={form.retail_pricing[key] || ''}
                              onChange={(e) =>
                                setForm((f) => ({
                                  ...f,
                                  retail_pricing: { ...f.retail_pricing, [key]: e.target.value },
                                }))
                              }
                              placeholder="0"
                              className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
                            />
                          </div>
                        ))}
                      </div>
                      <div className="mt-3">
                        <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                          One-Time (Alternative)
                        </label>
                        <input
                          type="number"
                          step="0.01"
                          value={form.retail_pricing.one_time || ''}
                          onChange={(e) =>
                            setForm((f) => ({
                              ...f,
                              retail_pricing: { ...f.retail_pricing, one_time: e.target.value },
                            }))
                          }
                          className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
                          placeholder="5000.00"
                        />
                      </div>
                      <p className="text-xs text-aegisaxiom-text-muted mt-2">
                        Set recurring rates OR one-time price
                      </p>
                    </>
                  ) : (
                    /* Wholesale MRR: Standard monthly/quarterly/annual */
                    <div className="grid grid-cols-3 gap-3">
                      {Object.entries(MRR_BILLING_PERIODS).map(([key, period]) => {
                        const price = parseFloat(form.retail_pricing[key]) || 0;
                        const costMonthly = parseFloat(form.cost_monthly) || 0;
                        const calc = calcMargin(price, costMonthly, period.months);

                        return (
                          <div key={key}>
                            <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                              {period.label}
                            </label>
                            <input
                              type="number"
                              step="0.01"
                              value={form.retail_pricing[key] || ''}
                              onChange={(e) =>
                                setForm((f) => ({
                                  ...f,
                                  retail_pricing: { ...f.retail_pricing, [key]: e.target.value },
                                }))
                              }
                              placeholder="299.00"
                              className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
                            />
                            {price > 0 && costMonthly > 0 && (
                              <div className="text-[10px] text-emerald-600 dark:text-emerald-400 mt-1">
                                Margin: {fmt(calc.margin)} ({calc.marginPercent.toFixed(1)}%)
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}

              {/* MSP Pricing - Conditional */}
              {PRODUCT_CATEGORIES[form.item_category].pricing_model === 'one_time' ? (
                <div className="bg-blue-900/10 border border-blue-800/30 rounded-lg p-4">
                  <label className="text-xs text-blue-700 dark:text-blue-300 font-medium block mb-2">
                    MSP Price (One-Time)
                  </label>
                  <div className="flex items-center gap-1">
                    <span className="text-aegisaxiom-text-muted">$</span>
                    <input
                      type="number"
                      step="0.01"
                      value={form.msp_pricing.one_time || ''}
                      onChange={(e) =>
                        setForm((f) => ({
                          ...f,
                          msp_pricing: {
                            one_time: parseFloat(e.target.value) || 0,
                            monthly: 0,
                            quarterly: 0,
                            annual: 0,
                          },
                        }))
                      }
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
                      placeholder="499.00"
                    />
                  </div>
                </div>
              ) : (
                <div className="bg-blue-900/10 border border-blue-800/30 rounded-lg p-4">
                  <label className="text-xs text-blue-700 dark:text-blue-300 font-medium block mb-3">
                    MSP Pricing (Recurring)
                  </label>

                  {/* Services: Show all 5 periods + one-time */}
                  {form.item_category === 'services' ? (
                    <>
                      <div className="grid grid-cols-5 gap-2">
                        {Object.entries(SERVICE_BILLING_PERIODS).map(([key, period]) => (
                          <div key={key}>
                            <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                              {period.label}
                            </label>
                            <input
                              type="number"
                              step="0.01"
                              value={form.msp_pricing[key] || ''}
                              onChange={(e) =>
                                setForm((f) => ({
                                  ...f,
                                  msp_pricing: { ...f.msp_pricing, [key]: e.target.value },
                                }))
                              }
                              placeholder="0"
                              className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
                            />
                          </div>
                        ))}
                      </div>
                      <div className="mt-3">
                        <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                          One-Time (Alternative)
                        </label>
                        <input
                          type="number"
                          step="0.01"
                          value={form.msp_pricing.one_time || ''}
                          onChange={(e) =>
                            setForm((f) => ({
                              ...f,
                              msp_pricing: { ...f.msp_pricing, one_time: e.target.value },
                            }))
                          }
                          className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
                          placeholder="4200.00"
                        />
                      </div>
                      <p className="text-xs text-aegisaxiom-text-muted mt-2">
                        Set recurring rates OR one-time price
                      </p>
                    </>
                  ) : (
                    /* Wholesale MRR: Standard monthly/quarterly/annual */
                    <div className="grid grid-cols-3 gap-3">
                      {Object.entries(MRR_BILLING_PERIODS).map(([key, period]) => {
                        const price = parseFloat(form.msp_pricing[key]) || 0;
                        const costMonthly = parseFloat(form.cost_monthly) || 0;
                        const calc = calcMargin(price, costMonthly, period.months);

                        return (
                          <div key={key}>
                            <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                              {period.label}
                            </label>
                            <input
                              type="number"
                              step="0.01"
                              value={form.msp_pricing[key] || ''}
                              onChange={(e) =>
                                setForm((f) => ({
                                  ...f,
                                  msp_pricing: { ...f.msp_pricing, [key]: e.target.value },
                                }))
                              }
                              placeholder="249.00"
                              className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
                            />
                            {price > 0 && costMonthly > 0 && (
                              <div className="text-[10px] text-blue-600 dark:text-blue-400 mt-1">
                                Margin: {fmt(calc.margin)} ({calc.marginPercent.toFixed(1)}%)
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}

              {/* Actions */}
              <div className="flex gap-2 pt-2">
                <button
                  onClick={handleSave}
                  disabled={loading}
                  className="flex-1 bg-violet-600 hover:bg-violet-500 text-white text-sm py-2.5 rounded-lg transition disabled:opacity-50 font-medium"
                >
                  {loading ? 'Saving...' : editing === 'new' ? 'Create Product' : 'Save Changes'}
                </button>
                <button
                  onClick={() => setEditing(null)}
                  className="px-6 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Bulk Import Modal */}
      {showBulkImport && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 overflow-y-auto">
          <div className="absolute inset-0 bg-black/60" />
          <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-5xl my-8">
            <div className="bg-aegisaxiom-surface backdrop-blur border-b border-aegisaxiom-border p-4 flex justify-between items-center">
              <h2 className="text-sm font-semibold text-aegisaxiom-text flex items-center gap-2">
                <Package size={16} className="text-blue-600 dark:text-blue-400" />
                Bulk Import Products
                {importResults && (
                  <span
                    className={`text-xs px-2 py-0.5 rounded ${
                      importResults.failed === 0
                        ? 'bg-emerald-50 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-300'
                        : 'bg-yellow-500/20 text-yellow-700 dark:text-yellow-300'
                    }`}
                  >
                    {importResults.success} created, {importResults.failed} failed
                  </span>
                )}
              </h2>
              <button
                onClick={closeBulkImportModal}
                className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
                disabled={loading}
              >
                <X size={18} />
              </button>
            </div>

            <div className="p-6 space-y-4 max-h-[calc(100vh-200px)] overflow-y-auto">
              {/* Import Method Selection */}
              <div className="grid grid-cols-2 gap-2 mb-4">
                <button
                  onClick={() => setBulkImportMethod('ai')}
                  className={`p-3 rounded-lg border text-sm transition ${
                    bulkImportMethod === 'ai'
                      ? 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300 border-blue-500'
                      : 'border-aegisaxiom-border text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover'
                  }`}
                >
                  <div className="font-medium mb-1">🤖 AI Parser (Natural Language)</div>
                  <div className="text-xs opacity-70">
                    Paste product descriptions, we'll parse them
                  </div>
                </button>
                <button
                  onClick={() => setBulkImportMethod('csv')}
                  className={`p-3 rounded-lg border text-sm transition ${
                    bulkImportMethod === 'csv'
                      ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border-emerald-500'
                      : 'border-aegisaxiom-border text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover'
                  }`}
                >
                  <div className="font-medium mb-1">📄 CSV Import</div>
                  <div className="text-xs opacity-70">Upload structured CSV file</div>
                </button>
              </div>

              {/* AI Parser */}
              {bulkImportMethod === 'ai' && (
                <div className="space-y-3">
                  <div className="bg-blue-900/10 border border-blue-800/30 rounded-lg p-3">
                    <p className="text-xs text-blue-700 dark:text-blue-300 font-medium mb-2">
                      How to use:
                    </p>
                    <ul className="text-xs text-aegisaxiom-text-secondary space-y-1 list-disc list-inside">
                      <li>Paste or type your product list in natural language</li>
                      <li>Include: name, SKU, pricing, cost for each product</li>
                      <li>
                        Example: "SimpliSecure Standard, SKU: SS-STD-001, Retail: $299/mo, MSP:
                        $249/mo, Cost: $150/mo"
                      </li>
                      <li>AI will parse and structure the data automatically</li>
                    </ul>
                  </div>

                  <textarea
                    value={bulkText}
                    onChange={(e) => setBulkText(e.target.value)}
                    placeholder="Paste your product list here...

Example:
SimpliSecure Standard - SKU: SS-STD-001
Retail: Monthly $299, Quarterly $837, Annual $3228
MSP: Monthly $249, Quarterly $697, Annual $2688
Cost: $150/month

SimpliComply Basic - SKU: SC-BAS-001
Retail: $199/mo, $557/qtr, $2148/yr
MSP: $159/mo, $437/qtr, $1668/yr
Cost: $80/mo"
                    rows={12}
                    className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-blue-500 resize-none font-mono"
                  />

                  <button
                    onClick={parseWithAI}
                    disabled={loading || !bulkText.trim()}
                    className="w-full bg-blue-600 hover:bg-blue-500 text-white text-sm py-2.5 rounded-lg transition disabled:opacity-50 font-medium"
                  >
                    {loading ? '🤖 Parsing with AI...' : '🤖 Parse Products'}
                  </button>
                </div>
              )}

              {/* CSV Upload */}
              {bulkImportMethod === 'csv' && (
                <div className="space-y-3">
                  <div className="bg-emerald-50 dark:bg-emerald-900/10 border border-emerald-200 dark:border-emerald-800/30 rounded-lg p-3">
                    <p className="text-xs text-emerald-700 dark:text-emerald-300 font-medium mb-2">
                      CSV Format:
                    </p>
                    <pre className="text-xs text-aegisaxiom-text-secondary font-mono bg-aegisaxiom-base/50 p-2 rounded overflow-x-auto">
                      name,description,sku,product_type,retail_monthly,retail_quarterly,retail_annual,msp_monthly,msp_quarterly,msp_annual,cost_monthly
                      {'\n'}
                      SimpliSecure Standard,Standard
                      package,SS-STD-001,simplisecure,299,837,3228,249,697,2688,150
                    </pre>

                    {/* Download Template Button */}
                    <button
                      onClick={() => {
                        const headers =
                          'name,description,sku,product_type,retail_monthly,retail_quarterly,retail_annual,msp_monthly,msp_quarterly,msp_annual,cost_monthly\n';
                        const exampleRow =
                          'SimpliSecure Standard,Standard package,SS-STD-001,simplisecure,299,837,3228,249,697,2688,150\n';
                        const blob = new Blob([headers + exampleRow], { type: 'text/csv' });
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = 'product_import_template.csv';
                        a.click();
                        URL.revokeObjectURL(url);
                        toast.success('📥 Template downloaded!');
                      }}
                      className="mt-3 w-full text-xs px-3 py-2 bg-blue-50 dark:bg-blue-600/20 text-blue-600 dark:text-blue-400 hover:bg-blue-600/30 border border-blue-500/30 rounded-lg transition flex items-center justify-center gap-2"
                    >
                      <svg
                        className="w-4 h-4"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                        />
                      </svg>
                      Download Template CSV
                    </button>
                  </div>

                  <div className="border-2 border-dashed border-aegisaxiom-border rounded-lg p-8 text-center hover:border-emerald-500 transition">
                    <input
                      type="file"
                      accept=".csv"
                      onChange={handleCSVUpload}
                      className="hidden"
                      id="csv-upload"
                    />
                    <label htmlFor="csv-upload" className="cursor-pointer">
                      <div className="text-4xl mb-2">📄</div>
                      <div className="text-sm text-aegisaxiom-text font-medium">Click to upload CSV</div>
                      <div className="text-xs text-aegisaxiom-text-muted mt-1">or drag and drop</div>
                    </label>
                  </div>
                </div>
              )}

              {/* Parsed Products Preview */}
              {parsedProducts.length > 0 && !importResults && (
                <div className="bg-aegisaxiom-accent-muted border border-aegisaxiom-border rounded-lg p-4">
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-semibold text-aegisaxiom-text">
                      Parsed Products ({parsedProducts.length})
                    </h3>
                    <button
                      onClick={() => setParsedProducts([])}
                      className="text-xs text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300"
                      disabled={loading}
                    >
                      Clear All
                    </button>
                  </div>

                  <div className="space-y-2 max-h-96 overflow-y-auto">
                    {parsedProducts.map((product, idx) => {
                      const isEditing = editingParsedIndex === idx;
                      const validationErrors = validateParsedProduct(product);
                      const hasErrors = validationErrors.length > 0;
                      const retailMonthly = product.retail_pricing?.monthly || 0;
                      const costMonthly = product.cost_monthly || 0;
                      const margin = retailMonthly - costMonthly;

                      return (
                        <div
                          key={idx}
                          className={`bg-aegisaxiom-surface border rounded p-3 ${
                            hasErrors ? 'border-yellow-500/50' : 'border-aegisaxiom-border'
                          }`}
                        >
                          {!isEditing ? (
                            // View Mode
                            <div className="flex items-start justify-between">
                              <div className="flex-1">
                                <div className="flex items-center gap-2">
                                  <div className="text-xs font-medium text-aegisaxiom-text">
                                    {product.name}
                                  </div>
                                  {hasErrors && (
                                    <span className="text-xs px-1.5 py-0.5 bg-yellow-500/20 text-yellow-700 dark:text-yellow-300 rounded">
                                      ⚠️ Needs Review
                                    </span>
                                  )}
                                </div>
                                <div className="text-xs text-aegisaxiom-text-secondary">
                                  SKU: {product.sku}
                                </div>
                                <div className="text-xs text-aegisaxiom-text-muted mt-1">
                                  Retail: ${retailMonthly}/mo • Cost: ${costMonthly}/mo • Margin: $
                                  {margin}
                                </div>
                                {hasErrors && (
                                  <div className="mt-2 text-xs text-yellow-600 dark:text-yellow-400">
                                    {validationErrors.map((err, i) => (
                                      <div key={i}>• {err}</div>
                                    ))}
                                  </div>
                                )}
                              </div>
                              <div className="flex items-center gap-1">
                                <button
                                  onClick={() => setEditingParsedIndex(idx)}
                                  className="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:text-blue-300 p-1"
                                  disabled={loading}
                                  title="Edit product"
                                >
                                  <Edit2 size={14} />
                                </button>
                                <button
                                  onClick={() =>
                                    setParsedProducts(parsedProducts.filter((_, i) => i !== idx))
                                  }
                                  className="text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300 p-1"
                                  disabled={loading}
                                  title="Remove product"
                                >
                                  <X size={14} />
                                </button>
                              </div>
                            </div>
                          ) : (
                            // Edit Mode
                            <div className="space-y-2">
                              <div className="flex items-center justify-between mb-2">
                                <span className="text-xs font-medium text-aegisaxiom-text">
                                  Edit Product
                                </span>
                                <button
                                  onClick={() => setEditingParsedIndex(null)}
                                  className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
                                >
                                  <X size={14} />
                                </button>
                              </div>

                              <div className="grid grid-cols-2 gap-2">
                                <div>
                                  <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                                    Name *
                                  </label>
                                  <input
                                    type="text"
                                    value={product.name}
                                    onChange={(e) =>
                                      updateParsedProduct(idx, { name: e.target.value })
                                    }
                                    className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                                    placeholder="Product name"
                                  />
                                </div>
                                <div>
                                  <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                                    SKU *
                                  </label>
                                  <input
                                    type="text"
                                    value={product.sku}
                                    onChange={(e) =>
                                      updateParsedProduct(idx, { sku: e.target.value })
                                    }
                                    className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                                    placeholder="SKU-001"
                                  />
                                </div>
                              </div>

                              <div>
                                <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                                  Description
                                </label>
                                <input
                                  type="text"
                                  value={product.description || ''}
                                  onChange={(e) =>
                                    updateParsedProduct(idx, { description: e.target.value })
                                  }
                                  className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                                  placeholder="Product description"
                                />
                              </div>

                              <div className="grid grid-cols-4 gap-2">
                                <div>
                                  <label className="text-xs text-red-700 dark:text-red-300 block mb-1">
                                    Cost/mo *
                                  </label>
                                  <input
                                    type="number"
                                    step="0.01"
                                    min="0"
                                    value={product.cost_monthly}
                                    onChange={(e) =>
                                      updateParsedProduct(idx, {
                                        cost_monthly: parseFloat(e.target.value) || 0,
                                      })
                                    }
                                    className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                                  />
                                </div>
                                <div>
                                  <label className="text-xs text-emerald-700 dark:text-emerald-300 block mb-1">
                                    Retail/mo
                                  </label>
                                  <input
                                    type="number"
                                    step="0.01"
                                    min="0"
                                    value={product.retail_pricing?.monthly || 0}
                                    onChange={(e) =>
                                      updateParsedProduct(idx, {
                                        retail_pricing: {
                                          ...product.retail_pricing,
                                          monthly: parseFloat(e.target.value) || 0,
                                        },
                                      })
                                    }
                                    className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                                  />
                                </div>
                                <div>
                                  <label className="text-xs text-emerald-700 dark:text-emerald-300 block mb-1">
                                    Retail/qtr
                                  </label>
                                  <input
                                    type="number"
                                    step="0.01"
                                    min="0"
                                    value={product.retail_pricing?.quarterly || 0}
                                    onChange={(e) =>
                                      updateParsedProduct(idx, {
                                        retail_pricing: {
                                          ...product.retail_pricing,
                                          quarterly: parseFloat(e.target.value) || 0,
                                        },
                                      })
                                    }
                                    className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                                  />
                                </div>
                                <div>
                                  <label className="text-xs text-emerald-700 dark:text-emerald-300 block mb-1">
                                    Retail/yr
                                  </label>
                                  <input
                                    type="number"
                                    step="0.01"
                                    min="0"
                                    value={product.retail_pricing?.annual || 0}
                                    onChange={(e) =>
                                      updateParsedProduct(idx, {
                                        retail_pricing: {
                                          ...product.retail_pricing,
                                          annual: parseFloat(e.target.value) || 0,
                                        },
                                      })
                                    }
                                    className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                                  />
                                </div>
                              </div>

                              <div className="grid grid-cols-3 gap-2">
                                <div>
                                  <label className="text-xs text-blue-700 dark:text-blue-300 block mb-1">
                                    MSP/mo
                                  </label>
                                  <input
                                    type="number"
                                    step="0.01"
                                    min="0"
                                    value={product.msp_pricing?.monthly || 0}
                                    onChange={(e) =>
                                      updateParsedProduct(idx, {
                                        msp_pricing: {
                                          ...product.msp_pricing,
                                          monthly: parseFloat(e.target.value) || 0,
                                        },
                                      })
                                    }
                                    className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                                  />
                                </div>
                                <div>
                                  <label className="text-xs text-blue-700 dark:text-blue-300 block mb-1">
                                    MSP/qtr
                                  </label>
                                  <input
                                    type="number"
                                    step="0.01"
                                    min="0"
                                    value={product.msp_pricing?.quarterly || 0}
                                    onChange={(e) =>
                                      updateParsedProduct(idx, {
                                        msp_pricing: {
                                          ...product.msp_pricing,
                                          quarterly: parseFloat(e.target.value) || 0,
                                        },
                                      })
                                    }
                                    className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                                  />
                                </div>
                                <div>
                                  <label className="text-xs text-blue-700 dark:text-blue-300 block mb-1">
                                    MSP/yr
                                  </label>
                                  <input
                                    type="number"
                                    step="0.01"
                                    min="0"
                                    value={product.msp_pricing?.annual || 0}
                                    onChange={(e) =>
                                      updateParsedProduct(idx, {
                                        msp_pricing: {
                                          ...product.msp_pricing,
                                          annual: parseFloat(e.target.value) || 0,
                                        },
                                      })
                                    }
                                    className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
                                  />
                                </div>
                              </div>

                              <button
                                onClick={() => setEditingParsedIndex(null)}
                                className="w-full bg-violet-600 hover:bg-violet-500 text-white text-xs py-1.5 rounded transition"
                              >
                                Done Editing
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>

                  <button
                    onClick={saveBulkProducts}
                    disabled={
                      loading || parsedProducts.some((p) => validateParsedProduct(p).length > 0)
                    }
                    className="w-full mt-3 bg-violet-600 hover:bg-violet-500 text-white text-sm py-2.5 rounded-lg transition disabled:opacity-50 font-medium"
                  >
                    {loading ? (
                      <span className="flex items-center justify-center gap-2">
                        <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                        Creating Products...
                      </span>
                    ) : parsedProducts.some((p) => validateParsedProduct(p).length > 0) ? (
                      `⚠️ Fix ${parsedProducts.filter((p) => validateParsedProduct(p).length > 0).length} Products Before Creating`
                    ) : (
                      `Create ${parsedProducts.length} Products`
                    )}
                  </button>
                </div>
              )}

              {/* Import Results */}
              {importResults && (
                <div className="space-y-3">
                  {/* Success Summary */}
                  <div
                    className={`border rounded-lg p-4 ${
                      importResults.failed === 0
                        ? 'bg-emerald-50 dark:bg-emerald-900/10 border-emerald-200 dark:border-emerald-800/30'
                        : 'bg-yellow-50 dark:bg-yellow-900/10 border-yellow-200 dark:border-yellow-800/30'
                    }`}
                  >
                    <h3 className="text-sm font-semibold text-aegisaxiom-text mb-2">Import Complete</h3>
                    <div className="space-y-1 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="text-aegisaxiom-text-secondary">✅ Successfully Created:</span>
                        <span className="text-emerald-600 dark:text-emerald-400 font-medium">
                          {importResults.success} products
                        </span>
                      </div>
                      {importResults.failed > 0 && (
                        <div className="flex items-center justify-between">
                          <span className="text-aegisaxiom-text-secondary">❌ Failed:</span>
                          <span className="text-red-600 dark:text-red-400 font-medium">
                            {importResults.failed} products
                          </span>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Error Details */}
                  {importResults.errors.length > 0 && (
                    <div className="bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800/30 rounded-lg p-4">
                      <h4 className="text-xs font-semibold text-red-700 dark:text-red-300 mb-2">
                        Errors ({importResults.errors.length})
                      </h4>
                      <div className="space-y-2 max-h-48 overflow-y-auto">
                        {importResults.errors.map((err, idx) => (
                          <div
                            key={idx}
                            className="bg-red-950/30 border border-red-900/50 rounded p-2"
                          >
                            <div className="text-xs font-medium text-red-200">{err.product}</div>
                            <div className="text-xs text-red-600 dark:text-red-400/70 mt-0.5">
                              {err.error}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Action Buttons */}
                  <div className="flex gap-2">
                    <button
                      onClick={closeBulkImportModal}
                      className="flex-1 bg-violet-600 hover:bg-violet-500 text-white text-sm py-2.5 rounded-lg transition font-medium"
                    >
                      Done - Close Import
                    </button>
                    {importResults.failed > 0 && (
                      <button
                        onClick={() => {
                          setImportResults(null);
                          // Keep parsedProducts so user can try again
                        }}
                        className="flex-1 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm text-sm py-2.5 rounded-lg transition font-medium"
                      >
                        Try Again
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
