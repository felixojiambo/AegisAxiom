import React, { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/hooks/usePermissions';
import ConfirmModal from '../shared/ConfirmModal';
import {
  Building2,
  Plus,
  Edit2,
  Trash2,
  X,
  ChevronDown,
  ChevronRight,
  Users,
  Phone,
  Mail,
  Globe,
  MapPin,
  FileText,
  DollarSign,
  CheckCircle,
  AlertCircle,
  Calendar,
  CreditCard,
} from 'lucide-react';
import { toast } from 'sonner';
import API_URL from '../../lib/apiConfig';

const API = API_URL;

const BILLING_FREQUENCIES = [
  { value: 'weekly', label: 'Weekly (Mon-Sun)' },
  { value: 'biweekly', label: 'Bi-Weekly' },
  { value: 'monthly', label: 'Monthly' },
];

const PAYMENT_TERMS = [
  { value: 'net_14', label: 'Net 14' },
  { value: 'net_30', label: 'Net 30' },
  { value: 'net_45', label: 'Net 45' },
  { value: 'net_60', label: 'Net 60' },
];

const PAYMENT_METHODS = [
  { value: 'ACH', label: 'ACH / Direct Deposit' },
  { value: 'Wire', label: 'Wire Transfer' },
  { value: 'Check', label: 'Check / Paper Check' },
  { value: 'PayPal', label: 'PayPal' },
  { value: 'Venmo', label: 'Venmo' },
  { value: 'Other', label: 'Other' },
];

const EMPTY_BUSINESS = {
  business_name: '',
  contact_name: '',
  email: '',
  phone: '',
  website: '',
  address_street: '',
  address_city: '',
  address_state: '',
  address_zip: '',
  address_country: 'USA',
  tax_id_ein: '',
  w9_on_file: false,
  is_1099_eligible: true,
  billing_frequency: 'weekly',
  payment_terms: 'net_30',
  payment_method: '',
  payment_details: {},
  notes: '',
};

export default function ContractorBusinesses() {
  const { user } = useAuth();
  const [businesses, setBusinesses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState(null); // 'create' | 'edit' | null
  const [form, setForm] = useState(EMPTY_BUSINESS);
  const [saving, setSaving] = useState(false);
  const [expandedBusiness, setExpandedBusiness] = useState(null);
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });

  const { hasPermission } = usePermissions();
  const canCreate = hasPermission('users.create');
  const canEdit = hasPermission('users.edit');
  const canDelete = hasPermission('users.delete');

  useEffect(() => {
    loadBusinesses();
  }, []);

  const loadBusinesses = async () => {
    setLoading(true);
    try {
      const res = await api.get(`${API}/api/admin/contractor-businesses`);
      if (res.ok) {
        const data = await res.json();
        setBusinesses(data.items || []);
      }
    } catch (err) {
      console.error('Failed to load contractor businesses:', err);
      toast.error('Failed to load contractor businesses');
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    if (!form.business_name) {
      toast.error('Business name is required');
      return;
    }

    setSaving(true);
    try {
      const payload = {
        ...form,
        payment_details: form.payment_details || {},
      };

      let res;
      if (modal === 'edit') {
        res = await api.put(`${API}/api/admin/contractor-businesses/${form.business_id}`, payload);
      } else {
        res = await api.post(`${API}/api/admin/contractor-businesses`, payload);
      }

      if (res.ok) {
        toast.success(`Business ${modal === 'edit' ? 'updated' : 'created'} successfully`);
        setModal(null);
        setForm(EMPTY_BUSINESS);
        loadBusinesses();
      } else {
        const err = await res.json();
        toast.error(err.detail || 'Failed to save business');
      }
    } catch (err) {
      toast.error('Failed to save business');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = (business) => {
    setConfirmState({
      open: true,
      title: 'Deactivate Business',
      message: `Are you sure you want to deactivate "${business.business_name}"?`,
      action: async () => {
        try {
          const res = await api.delete(
            `${API}/api/admin/contractor-businesses/${business.business_id}`,
          );
          if (res.ok) {
            toast.success('Business deactivated successfully');
            loadBusinesses();
          } else {
            const err = await res.json();
            toast.error(err.detail || 'Failed to deactivate business');
          }
        } catch (err) {
          toast.error('Failed to deactivate business');
        }
      },
    });
  };

  const toggleExpand = async (business) => {
    if (expandedBusiness?.business_id === business.business_id) {
      setExpandedBusiness(null);
    } else {
      // Fetch full business details with contractors
      try {
        const res = await api.get(`${API}/api/admin/contractor-businesses/${business.business_id}`);
        if (res.ok) {
          const data = await res.json();
          setExpandedBusiness(data);
        }
      } catch (err) {
        console.error('Failed to load business details:', err);
      }
    }
  };

  return (
    <div className="p-4 sm:p-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="mb-6">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <h1 className="text-xl sm:text-2xl font-bold text-aegisaxiom-text flex items-center gap-2">
              <Building2 size={26} className="text-violet-600 dark:text-violet-400" />
              Contractor Businesses
            </h1>
            <p className="text-sm text-aegisaxiom-text-secondary mt-1">
              Manage contractor companies, billing terms, and payment details
            </p>
          </div>
          {canCreate && (
            <button
              onClick={() => {
                setForm(EMPTY_BUSINESS);
                setModal('create');
              }}
              className="flex items-center justify-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg transition w-full sm:w-auto"
              data-testid="new-business-btn"
            >
              <Plus size={18} />
              New Business
            </button>
          )}
        </div>
      </div>

      {/* Businesses List */}
      {loading ? (
        <div className="text-center py-12 text-aegisaxiom-text-muted">Loading businesses...</div>
      ) : businesses.length === 0 ? (
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-12 text-center">
          <Building2 size={48} className="mx-auto text-aegisaxiom-text-muted mb-3" />
          <h3 className="text-aegisaxiom-text font-semibold mb-2">No Contractor Businesses</h3>
          <p className="text-aegisaxiom-text-secondary text-sm mb-4">
            Get started by adding your first contractor business with billing terms and payment
            details.
          </p>
          {canCreate && (
            <button
              onClick={() => {
                setForm(EMPTY_BUSINESS);
                setModal('create');
              }}
              className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg transition"
            >
              Add First Business
            </button>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          {businesses.map((business) => {
            const isExpanded = expandedBusiness?.business_id === business.business_id;
            const contractors = isExpanded ? expandedBusiness.contractors || [] : [];

            return (
              <div
                key={business.business_id}
                className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg overflow-hidden hover:border-aegisaxiom-border transition"
                data-testid={`business-${business.business_id}`}
              >
                {/* Business Header Row */}
                <div className="p-3 sm:p-4 flex items-start justify-between gap-3">
                  <div className="flex items-start gap-2 sm:gap-4 flex-1 min-w-0">
                    <button
                      onClick={() => toggleExpand(business)}
                      className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition shrink-0 mt-0.5"
                      aria-label={isExpanded ? 'Collapse' : 'Expand'}
                    >
                      {isExpanded ? <ChevronDown size={20} /> : <ChevronRight size={20} />}
                    </button>

                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
                        <h3 className="text-aegisaxiom-text font-semibold">{business.business_name}</h3>
                        <span
                          className={`text-xs px-2 py-0.5 rounded-full ${
                            business.status === 'active'
                              ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300'
                              : 'bg-aegisaxiom-elevated/20 text-aegisaxiom-text-secondary'
                          }`}
                        >
                          {business.status}
                        </span>
                      </div>
                      <div className="flex items-center gap-x-3 sm:gap-x-4 gap-y-1 mt-1 text-xs text-aegisaxiom-text-secondary flex-wrap">
                        {business.contact_name && (
                          <span className="flex items-center gap-1">
                            <Users size={12} /> {business.contact_name}
                          </span>
                        )}
                        {business.contractor_count > 0 && (
                          <span className="flex items-center gap-1">
                            <Users size={12} /> {business.contractor_count} contractor
                            {business.contractor_count !== 1 ? 's' : ''}
                          </span>
                        )}
                        <span className="flex items-center gap-1">
                          <Calendar size={12} /> {business.billing_frequency}
                        </span>
                        <span className="flex items-center gap-1">
                          <DollarSign size={12} /> {business.payment_terms?.replace('_', ' ')}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2 shrink-0">
                    {canEdit && (
                      <button
                        onClick={() => {
                          setForm(business);
                          setModal('edit');
                        }}
                        className="p-2 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated rounded transition"
                        aria-label={`Edit ${business.business_name}`}
                      >
                        <Edit2 size={16} />
                      </button>
                    )}
                    {canDelete && business.contractor_count === 0 && (
                      <button
                        onClick={() => handleDelete(business)}
                        className="p-2 text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition"
                        aria-label={`Delete ${business.business_name}`}
                      >
                        <Trash2 size={16} />
                      </button>
                    )}
                  </div>
                </div>

                {/* Expanded Details */}
                {isExpanded && (
                  <div className="border-t border-aegisaxiom-border p-3 sm:p-4 bg-aegisaxiom-base/50 space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                      {/* Contact Info */}
                      <div>
                        <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-2">
                          Contact Information
                        </h4>
                        <div className="space-y-1 text-xs text-aegisaxiom-text-secondary">
                          {expandedBusiness.email && (
                            <div className="flex items-center gap-2">
                              <Mail size={12} className="text-aegisaxiom-text-muted" />
                              {expandedBusiness.email}
                            </div>
                          )}
                          {expandedBusiness.phone && (
                            <div className="flex items-center gap-2">
                              <Phone size={12} className="text-aegisaxiom-text-muted" />
                              {expandedBusiness.phone}
                            </div>
                          )}
                          {expandedBusiness.website && (
                            <div className="flex items-center gap-2">
                              <Globe size={12} className="text-aegisaxiom-text-muted" />
                              <a
                                href={expandedBusiness.website}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-violet-600 dark:text-violet-400 hover:underline"
                              >
                                {expandedBusiness.website}
                              </a>
                            </div>
                          )}
                          {(expandedBusiness.address_street || expandedBusiness.address_city) && (
                            <div className="flex items-start gap-2">
                              <MapPin size={12} className="text-aegisaxiom-text-muted mt-0.5" />
                              <div>
                                {expandedBusiness.address_street && (
                                  <div>{expandedBusiness.address_street}</div>
                                )}
                                {expandedBusiness.address_city && (
                                  <div>
                                    {expandedBusiness.address_city},{' '}
                                    {expandedBusiness.address_state} {expandedBusiness.address_zip}
                                  </div>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                      </div>

                      {/* Tax & Billing */}
                      <div>
                        <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-2">
                          Tax & Billing
                        </h4>
                        <div className="space-y-1 text-xs text-aegisaxiom-text-secondary">
                          {expandedBusiness.tax_id_ein && (
                            <div className="flex items-center gap-2">
                              <FileText size={12} className="text-aegisaxiom-text-muted" />
                              EIN: {expandedBusiness.tax_id_ein}
                            </div>
                          )}
                          <div className="flex items-center gap-2">
                            {expandedBusiness.w9_on_file ? (
                              <CheckCircle
                                size={12}
                                className="text-emerald-600 dark:text-emerald-400"
                              />
                            ) : (
                              <AlertCircle
                                size={12}
                                className="text-amber-600 dark:text-amber-400"
                              />
                            )}
                            W9 on file: {expandedBusiness.w9_on_file ? 'Yes' : 'No'}
                          </div>
                          <div className="flex items-center gap-2">
                            <DollarSign size={12} className="text-aegisaxiom-text-muted" />
                            1099 Eligible: {expandedBusiness.is_1099_eligible ? 'Yes' : 'No'}
                          </div>
                        </div>
                      </div>

                      {/* Payment Info */}
                      <div>
                        <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-2">
                          Payment Information
                        </h4>
                        <div className="space-y-1 text-xs text-aegisaxiom-text-secondary">
                          <div className="flex items-center gap-2">
                            <Calendar size={12} className="text-aegisaxiom-text-muted" />
                            {BILLING_FREQUENCIES.find(
                              (f) => f.value === expandedBusiness.billing_frequency,
                            )?.label || expandedBusiness.billing_frequency}
                          </div>
                          <div className="flex items-center gap-2">
                            <DollarSign size={12} className="text-aegisaxiom-text-muted" />
                            {PAYMENT_TERMS.find((t) => t.value === expandedBusiness.payment_terms)
                              ?.label || expandedBusiness.payment_terms}
                          </div>
                          {expandedBusiness.payment_method && (
                            <div className="flex items-center gap-2">
                              <CreditCard size={12} className="text-aegisaxiom-text-muted" />
                              {PAYMENT_METHODS.find(
                                (m) => m.value === expandedBusiness.payment_method,
                              )?.label || expandedBusiness.payment_method}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Contractors List */}
                    {contractors.length > 0 && (
                      <div>
                        <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-2">
                          Contractors ({contractors.length})
                        </h4>
                        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2">
                          {contractors.map((contractor) => (
                            <div
                              key={contractor.user_id}
                              className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded px-3 py-2"
                            >
                              <div className="text-xs text-aegisaxiom-text font-medium">
                                {contractor.name}
                              </div>
                              <div className="text-[10px] text-aegisaxiom-text-muted truncate">
                                {contractor.email}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Notes */}
                    {expandedBusiness.notes && (
                      <div>
                        <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-1">
                          Notes
                        </h4>
                        <p className="text-xs text-aegisaxiom-text-secondary">{expandedBusiness.notes}</p>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Create/Edit Modal */}
      {modal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4">
          <div className="absolute inset-0 bg-black/60" onClick={() => setModal(null)} />
          <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-3xl max-h-[95vh] sm:max-h-[90vh] overflow-y-auto">
            <div className="sticky top-0 z-10 bg-aegisaxiom-surface backdrop-blur border-b border-aegisaxiom-border p-3 sm:p-4 flex justify-between items-center">
              <h2 className="text-base sm:text-lg font-semibold text-aegisaxiom-text">
                {modal === 'edit' ? 'Edit' : 'New'} Contractor Business
              </h2>
              <button
                onClick={() => setModal(null)}
                className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text p-1 -m-1 rounded transition"
                aria-label="Close"
              >
                <X size={20} />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="p-4 sm:p-6 space-y-6">
              {/* Business Details Section */}
              <div>
                <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
                  <Building2 size={16} className="text-violet-600 dark:text-violet-400" />
                  Business Details
                </h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                  <div className="sm:col-span-2">
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                      Business Name <span className="text-red-600 dark:text-red-400">*</span>
                    </label>
                    <input
                      type="text"
                      value={form.business_name}
                      onChange={(e) => setForm((f) => ({ ...f, business_name: e.target.value }))}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                      placeholder="e.g., Acme Security LLC"
                      required
                    />
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                      Contact Name
                    </label>
                    <input
                      type="text"
                      value={form.contact_name}
                      onChange={(e) => setForm((f) => ({ ...f, contact_name: e.target.value }))}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                      placeholder="e.g., John Smith"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Email</label>
                    <input
                      type="email"
                      value={form.email}
                      onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                      placeholder="contact@business.com"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Phone</label>
                    <input
                      type="tel"
                      value={form.phone}
                      onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                      placeholder="(555) 123-4567"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Website</label>
                    <input
                      type="url"
                      value={form.website}
                      onChange={(e) => setForm((f) => ({ ...f, website: e.target.value }))}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                      placeholder="https://..."
                    />
                  </div>
                </div>
              </div>

              {/* Address Section */}
              <div>
                <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
                  <MapPin size={16} className="text-violet-600 dark:text-violet-400" />
                  Address
                </h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                  <div className="sm:col-span-2">
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                      Street Address
                    </label>
                    <input
                      type="text"
                      value={form.address_street}
                      onChange={(e) => setForm((f) => ({ ...f, address_street: e.target.value }))}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                      placeholder="123 Main Street"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">City</label>
                    <input
                      type="text"
                      value={form.address_city}
                      onChange={(e) => setForm((f) => ({ ...f, address_city: e.target.value }))}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                      placeholder="Austin"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">State</label>
                    <input
                      type="text"
                      value={form.address_state}
                      onChange={(e) => setForm((f) => ({ ...f, address_state: e.target.value }))}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                      placeholder="TX"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">ZIP Code</label>
                    <input
                      type="text"
                      value={form.address_zip}
                      onChange={(e) => setForm((f) => ({ ...f, address_zip: e.target.value }))}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                      placeholder="78701"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Country</label>
                    <input
                      type="text"
                      value={form.address_country}
                      onChange={(e) => setForm((f) => ({ ...f, address_country: e.target.value }))}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                      placeholder="USA"
                    />
                  </div>
                </div>
              </div>

              {/* Tax Information */}
              <div>
                <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
                  <FileText size={16} className="text-violet-600 dark:text-violet-400" />
                  Tax Information
                </h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                      EIN / Tax ID
                    </label>
                    <input
                      type="text"
                      value={form.tax_id_ein}
                      onChange={(e) => setForm((f) => ({ ...f, tax_id_ein: e.target.value }))}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                      placeholder="12-3456789"
                    />
                  </div>
                  <div className="flex items-center gap-6 sm:pt-6 flex-wrap">
                    <label className="flex items-center gap-2 text-sm text-aegisaxiom-text-secondary cursor-pointer">
                      <input
                        type="checkbox"
                        checked={form.w9_on_file}
                        onChange={(e) => setForm((f) => ({ ...f, w9_on_file: e.target.checked }))}
                        className="w-4 h-4 rounded border-aegisaxiom-border-hover bg-aegisaxiom-elevated text-violet-500"
                      />
                      W9 on file
                    </label>
                    <label className="flex items-center gap-2 text-sm text-aegisaxiom-text-secondary cursor-pointer">
                      <input
                        type="checkbox"
                        checked={form.is_1099_eligible}
                        onChange={(e) =>
                          setForm((f) => ({ ...f, is_1099_eligible: e.target.checked }))
                        }
                        className="w-4 h-4 rounded border-aegisaxiom-border-hover bg-aegisaxiom-elevated text-violet-500"
                      />
                      1099 Eligible
                    </label>
                  </div>
                </div>
              </div>

              {/* Billing Terms */}
              <div>
                <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
                  <DollarSign size={16} className="text-violet-600 dark:text-violet-400" />
                  Billing Terms
                </h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                      Billing Frequency
                    </label>
                    <select
                      value={form.billing_frequency}
                      onChange={(e) =>
                        setForm((f) => ({ ...f, billing_frequency: e.target.value }))
                      }
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                    >
                      {BILLING_FREQUENCIES.map((freq) => (
                        <option key={freq.value} value={freq.value}>
                          {freq.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                      Payment Terms
                    </label>
                    <select
                      value={form.payment_terms}
                      onChange={(e) => setForm((f) => ({ ...f, payment_terms: e.target.value }))}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                    >
                      {PAYMENT_TERMS.map((term) => (
                        <option key={term.value} value={term.value}>
                          {term.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </div>

              {/* Payment Method */}
              <div>
                <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
                  <CreditCard size={16} className="text-violet-600 dark:text-violet-400" />
                  Payment Method
                </h3>
                <div className="space-y-4">
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                      Preferred Payment Method
                    </label>
                    <select
                      value={form.payment_method}
                      onChange={(e) => setForm((f) => ({ ...f, payment_method: e.target.value }))}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                    >
                      <option value="">Select method...</option>
                      {PAYMENT_METHODS.map((method) => (
                        <option key={method.value} value={method.value}>
                          {method.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  {form.payment_method && (
                    <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-4">
                      <label className="text-xs text-aegisaxiom-text-secondary block mb-2">
                        Payment Details {form.payment_method === 'ACH' && '(Bank Info)'}
                        {form.payment_method === 'Check' && '(Mailing Address)'}
                      </label>
                      <textarea
                        value={form.payment_details?.notes || ''}
                        onChange={(e) =>
                          setForm((f) => ({
                            ...f,
                            payment_details: { ...f.payment_details, notes: e.target.value },
                          }))
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                        rows={4}
                        placeholder={
                          form.payment_method === 'ACH'
                            ? 'Bank Name: ...\nRouting: ...\nAccount: ...'
                            : form.payment_method === 'Check'
                              ? 'Mailing Address:\n...'
                              : 'Payment details, account info, etc.'
                        }
                      />
                      <p className="text-[10px] text-aegisaxiom-text-muted mt-1">
                        ⚠️ This information is visible to admins and finance team only
                      </p>
                    </div>
                  )}
                </div>
              </div>

              {/* Notes */}
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                  Internal Notes
                </label>
                <textarea
                  value={form.notes}
                  onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
                  className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                  rows={3}
                  placeholder="Internal notes about this business..."
                />
              </div>

              {/* Actions */}
              <div className="flex flex-col-reverse sm:flex-row gap-3 pt-4 border-t border-aegisaxiom-border">
                <button
                  type="button"
                  onClick={() => setModal(null)}
                  className="flex-1 px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="flex-1 px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg transition disabled:opacity-50"
                  data-testid="save-business-btn"
                >
                  {saving ? 'Saving...' : modal === 'edit' ? 'Update Business' : 'Create Business'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
      <ConfirmModal
        isOpen={confirmState.open}
        title={confirmState.title}
        message={confirmState.message}
        confirmText="Deactivate"
        onConfirm={() => confirmState.action?.()}
        onCancel={() => setConfirmState({ open: false, action: null, title: '', message: '' })}
      />
    </div>
  );
}
