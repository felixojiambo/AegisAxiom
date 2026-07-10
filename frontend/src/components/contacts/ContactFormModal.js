import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { api } from '../../lib/api';
import API_URL from '../../lib/apiConfig';
import {
  X,
  Building2,
  User,
  Mail,
  Phone,
  Smartphone,
  Briefcase,
  Linkedin,
  Star,
} from 'lucide-react';

const API = API_URL;

const CONTACT_METHODS = [
  { value: 'email', label: 'Email' },
  { value: 'phone', label: 'Phone' },
  { value: 'mobile', label: 'Mobile' },
];

export default function ContactFormModal({
  contact,
  editMode,
  prefilledAccountId,
  onClose,
  onSave,
}) {
  const [loading, setLoading] = useState(false);
  const [accounts, setAccounts] = useState([]);
  const [salesReps, setSalesReps] = useState([]);

  const [formData, setFormData] = useState({
    account_id: prefilledAccountId || '',
    first_name: '',
    last_name: '',
    email: '',
    phone: '',
    mobile_phone: '',
    title: '',
    department: '',
    linkedin_url: '',
    preferred_contact_method: '',
    is_primary: false,
    notes: '',
    is_test_data: false,
    assigned_sales_rep_id: '',
    assigned_sales_rep_name: '',
  });

  useEffect(() => {
    loadAccounts();
    loadSalesReps();

    if (contact && editMode) {
      setFormData({
        account_id: contact.account_id || '',
        first_name: contact.first_name || '',
        last_name: contact.last_name || '',
        email: contact.email || '',
        phone: contact.phone || '',
        mobile_phone: contact.mobile_phone || '',
        title: contact.title || '',
        department: contact.department || '',
        linkedin_url: contact.linkedin_url || '',
        preferred_contact_method: contact.preferred_contact_method || '',
        is_primary: contact.is_primary || false,
        notes: contact.notes || '',
        is_test_data: contact.is_test_data || false,
        assigned_sales_rep_id: contact.assigned_sales_rep_id || '',
        assigned_sales_rep_name: contact.assigned_sales_rep_name || '',
      });
    } else if (prefilledAccountId) {
      setFormData((prev) => ({ ...prev, account_id: prefilledAccountId }));
    }
  }, [contact, editMode, prefilledAccountId]);

  const loadAccounts = async () => {
    try {
      const response = await api.get(`${API}/api/accounts?limit=1000`);
      const data = await response.json();
      setAccounts(data.items || []);
    } catch (error) {
      console.error('Error loading accounts:', error);
    }
  };

  const loadSalesReps = async () => {
    try {
      const response = await api.get(`${API}/api/contacts/sales-reps`);
      if (response.ok) {
        const data = await response.json();
        setSalesReps(Array.isArray(data) ? data : []);
      }
    } catch (error) {
      console.error('Error loading sales reps:', error);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    // Validation
    if (!formData.first_name.trim() || !formData.last_name.trim()) {
      toast.error('First and last name are required');
      return;
    }
    if (!formData.account_id) {
      toast.error('Account is required');
      return;
    }

    setLoading(true);
    try {
      if (editMode && contact) {
        const response = await api.put(`${API}/api/contacts/${contact.contact_id}`, formData);
        await response.json();
        toast.success('Contact updated successfully');
      } else {
        const response = await api.post(`${API}/api/contacts`, formData);
        await response.json();
        toast.success('Contact created successfully');
      }
      onSave();
    } catch (error) {
      console.error('Error saving contact:', error);
      const errorData = await error.response?.json();
      toast.error(errorData?.detail || 'Failed to save contact');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-aegisaxiom-border">
          <h2 className="text-xl font-bold text-aegisaxiom-text">
            {editMode ? 'Edit Contact' : 'New Contact'}
          </h2>
          <button onClick={onClose} className="p-2 hover:bg-aegisaxiom-elevated rounded-lg transition-colors">
            <X className="w-5 h-5 text-aegisaxiom-text-secondary" />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-6">
          <div className="space-y-6">
            {/* Account Selection */}
            <div>
              <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                <Building2 className="w-4 h-4 inline mr-1" />
                Account <span className="text-red-600 dark:text-red-400">*</span>
              </label>
              <select
                value={formData.account_id}
                onChange={(e) => {
                  const selectedAccountId = e.target.value;
                  const selectedAccount = accounts.find((a) => a.account_id === selectedAccountId);
                  setFormData((prev) => ({
                    ...prev,
                    account_id: selectedAccountId,
                    assigned_sales_rep_id: selectedAccount?.assigned_sales_rep_id || '',
                    assigned_sales_rep_name: selectedAccount?.assigned_sales_rep_name || '',
                  }));
                }}
                className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                required
                disabled={editMode || prefilledAccountId}
              >
                <option value="">Select account</option>
                {accounts.map((acc) => (
                  <option key={acc.account_id} value={acc.account_id}>
                    {acc.name}
                  </option>
                ))}
              </select>
              {(editMode || prefilledAccountId) && (
                <p className="text-xs text-aegisaxiom-text-muted mt-1">Account cannot be changed</p>
              )}
            </div>

            {/* Assigned Sales Rep */}
            <div>
              <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                <User className="w-4 h-4 inline mr-1" />
                Assigned Sales Rep
              </label>
              <select
                value={formData.assigned_sales_rep_id}
                onChange={(e) => {
                  const selectedUser = salesReps.find((u) => u.user_id === e.target.value);
                  setFormData((prev) => ({
                    ...prev,
                    assigned_sales_rep_id: e.target.value,
                    assigned_sales_rep_name: selectedUser?.name || '',
                  }));
                }}
                className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
              >
                <option value="">None</option>
                {salesReps.map((u) => (
                  <option key={u.user_id} value={u.user_id}>
                    {u.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Name */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  <User className="w-4 h-4 inline mr-1" />
                  First Name <span className="text-red-600 dark:text-red-400">*</span>
                </label>
                <input
                  type="text"
                  value={formData.first_name}
                  onChange={(e) => setFormData((prev) => ({ ...prev, first_name: e.target.value }))}
                  className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                  required
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  Last Name <span className="text-red-600 dark:text-red-400">*</span>
                </label>
                <input
                  type="text"
                  value={formData.last_name}
                  onChange={(e) => setFormData((prev) => ({ ...prev, last_name: e.target.value }))}
                  className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                  required
                />
              </div>
            </div>

            {/* Contact Info */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  <Mail className="w-4 h-4 inline mr-1" />
                  Email
                </label>
                <input
                  type="email"
                  value={formData.email}
                  onChange={(e) => setFormData((prev) => ({ ...prev, email: e.target.value }))}
                  className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                  placeholder="email@example.com"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  <Phone className="w-4 h-4 inline mr-1" />
                  Phone
                </label>
                <input
                  type="tel"
                  value={formData.phone}
                  onChange={(e) => setFormData((prev) => ({ ...prev, phone: e.target.value }))}
                  className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                  placeholder="555-0123"
                />
              </div>
            </div>

            {/* Mobile & Preferred Method */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  <Smartphone className="w-4 h-4 inline mr-1" />
                  Mobile Phone
                </label>
                <input
                  type="tel"
                  value={formData.mobile_phone}
                  onChange={(e) =>
                    setFormData((prev) => ({ ...prev, mobile_phone: e.target.value }))
                  }
                  className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                  placeholder="555-0124"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  Preferred Contact Method
                </label>
                <select
                  value={formData.preferred_contact_method}
                  onChange={(e) =>
                    setFormData((prev) => ({ ...prev, preferred_contact_method: e.target.value }))
                  }
                  className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                >
                  <option value="">Not specified</option>
                  {CONTACT_METHODS.map((method) => (
                    <option key={method.value} value={method.value}>
                      {method.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Title & Department */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  <Briefcase className="w-4 h-4 inline mr-1" />
                  Title
                </label>
                <input
                  type="text"
                  value={formData.title}
                  onChange={(e) => setFormData((prev) => ({ ...prev, title: e.target.value }))}
                  className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                  placeholder="IT Director"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">Department</label>
                <input
                  type="text"
                  value={formData.department}
                  onChange={(e) => setFormData((prev) => ({ ...prev, department: e.target.value }))}
                  className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                  placeholder="Information Technology"
                />
              </div>
            </div>

            {/* LinkedIn */}
            <div>
              <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                <Linkedin className="w-4 h-4 inline mr-1" />
                LinkedIn URL
              </label>
              <input
                type="url"
                value={formData.linkedin_url}
                onChange={(e) => setFormData((prev) => ({ ...prev, linkedin_url: e.target.value }))}
                className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                placeholder="https://linkedin.com/in/username"
              />
            </div>

            {/* Notes */}
            <div>
              <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">Notes</label>
              <textarea
                value={formData.notes}
                onChange={(e) => setFormData((prev) => ({ ...prev, notes: e.target.value }))}
                rows={3}
                className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500 resize-none"
                placeholder="Additional notes about this contact"
              />
            </div>

            {/* Flags */}
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="is_primary"
                  checked={formData.is_primary}
                  onChange={(e) =>
                    setFormData((prev) => ({ ...prev, is_primary: e.target.checked }))
                  }
                  className="w-4 h-4 rounded border-aegisaxiom-border bg-aegisaxiom-base text-violet-600 focus:ring-violet-500"
                />
                <label
                  htmlFor="is_primary"
                  className="text-sm text-aegisaxiom-text-secondary flex items-center gap-1"
                >
                  <Star className="w-4 h-4 text-yellow-500" />
                  Mark as primary contact for this account
                </label>
              </div>

              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="is_test_data"
                  checked={formData.is_test_data}
                  onChange={(e) =>
                    setFormData((prev) => ({ ...prev, is_test_data: e.target.checked }))
                  }
                  className="w-4 h-4 rounded border-aegisaxiom-border bg-aegisaxiom-base text-violet-600 focus:ring-violet-500"
                />
                <label htmlFor="is_test_data" className="text-sm text-aegisaxiom-text-secondary">
                  Mark as test data
                </label>
              </div>
            </div>
          </div>
        </form>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-6 border-t border-aegisaxiom-border">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-elevated rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading}
            className="px-4 py-2 bg-violet-600 hover:bg-violet-700 rounded-lg transition-colors disabled:opacity-50"
          >
            {loading ? 'Saving...' : editMode ? 'Update Contact' : 'Create Contact'}
          </button>
        </div>
      </div>
    </div>
  );
}
