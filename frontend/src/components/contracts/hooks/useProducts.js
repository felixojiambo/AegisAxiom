import { useState, useEffect } from 'react';
import API_URL from '../../../lib/apiConfig';

/**
 * Custom hook for managing product data
 * Handles loading products from the API and managing filters
 */
export function useProducts(serviceCategory) {
  const [products, setProducts] = useState([]);
  const [productSearch, setProductSearch] = useState('');
  const [productTypeFilter, setProductTypeFilter] = useState('all');
  const [loadingProducts, setLoadingProducts] = useState(false);

  // Load products when service category changes to wholesale_mrr
  useEffect(() => {
    if (serviceCategory === 'wholesale_mrr') {
      loadProducts();
    }
  }, [serviceCategory]);

  const loadProducts = async () => {
    try {
      setLoadingProducts(true);
      const url = `${API_URL}/api/admin/products`;
      const res = await fetch(url, {
        credentials: 'include',
      });
      if (res.ok) {
        const data = await res.json();
        setProducts(data.products || []);
      } else {
        console.error('Failed to load products:', res.status, res.statusText);
      }
    } catch (err) {
      console.error('Failed to load products:', err);
    } finally {
      setLoadingProducts(false);
    }
  };

  /**
   * Filter products based on search term and type filter
   */
  const getFilteredProducts = () => {
    return products.filter((p) => {
      // Apply search filter
      const matchesSearch =
        !productSearch ||
        p.name.toLowerCase().includes(productSearch.toLowerCase()) ||
        p.sku.toLowerCase().includes(productSearch.toLowerCase());

      // Apply product type filter
      const matchesType = productTypeFilter === 'all' || p.product_type === productTypeFilter;

      return matchesSearch && matchesType;
    });
  };

  return {
    products,
    productSearch,
    setProductSearch,
    productTypeFilter,
    setProductTypeFilter,
    loadingProducts,
    loadProducts,
    getFilteredProducts,
  };
}
