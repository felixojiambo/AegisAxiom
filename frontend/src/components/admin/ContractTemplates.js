import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '../../lib/apiConfig';
import ConfirmModal from '../shared/ConfirmModal';
import {
  FileText,
  Plus,
  Edit2,
  Trash2,
  X,
  Save,
  DollarSign,
  Clock,
  Briefcase,
  AlertCircle,
} from 'lucide-react';

const API = API_URL;

const CONTRACT_TYPES = [
  { value: 'boh', label: 'Block of Hours (BOH)', icon: Clock },
  { value: 'boa', label: 'Bill on Actuals (BOA)', icon: Briefcase },
  { value: 'fixed_fee', label: 'Fixed Fee', icon: DollarSign },
  { value: 'monthly', label: 'Monthly Retainer', icon: Clock },
];

const BILLING_TERMS = [
  { value: 'weekly', label: 'Weekly' },
  { value: 'biweekly', label: 'Bi-weekly' },
  { value: 'monthly', label: 'Monthly' },
];

const PAYMENT_TERMS = [
  { value: 'net_7', label: 'Net 7' },
  { value: 'net_14', label: 'Net 14' },
  { value: 'net_30', label: 'Net 30' },
  { value: 'net_45', label: 'Net 45' },
  { value: 'net_60', label: 'Net 60' },
];

export default function ContractTemplates() {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editingTemplate, setEditingTemplate] = useState(null);
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });

  useEffect(() => {
    loadTemplates();
  }, []);

  const loadTemplates = async () => {
    try {
      setLoading(true);
      const response = await api.get(`${API}/api/admin/contract-templates`);

      if (response.ok) {
        const data = await response.json();
        setTemplates(data.items || []);
      }
    } catch (error) {
      console.error('Error loading templates:', error);
      toast.error('Failed to load contract templates');
    } finally {
      setLoading(false);
    }
  };

  const handleEdit = (template) => {
    setEditingTemplate(template);
    setShowModal(true);
  };

  const handleDelete = (templateId) => {
    setConfirmState({
      open: true,
      title: 'Delete Template',
      message: 'Are you sure you want to delete this template? This action cannot be undone.',
      action: async () => {
        try {
          const response = await api.delete(`${API}/api/admin/contract-templates/${templateId}`);

          if (response.ok) {
            toast.success('Template deleted successfully');
            loadTemplates();
          } else {
            toast.error('Failed to delete template');
          }
        } catch (error) {
          console.error('Error deleting template:', error);
          toast.error('Failed to delete template');
        }
      },
    });
  };

  const formatCurrency = (amount) => {
    if (!amount) return '$0';
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
    }).format(amount);
  };

  return (
    <div data-testid="contract-templates-page">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-3 mb-6">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-aegisaxiom-text mb-1 flex items-center gap-2">
            <FileText size={24} className="text-violet-600 dark:text-violet-400" />
            Contract Templates
          </h1>
          <p className="text-aegisaxiom-text-secondary text-sm">
            Create reusable contract configurations for Sales Ops to use in SOW Builder
          </p>
        </div>
        <button
          onClick={() => {
            setEditingTemplate(null);
            setShowModal(true);
          }}
          className="flex items-center justify-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg transition w-full sm:w-auto"
          data-testid="create-template-btn"
        >
          <Plus className="w-4 h-4" />
          Create Template
        </button>
      </div>

      {/* Templates List */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-violet-500"></div>
        </div>
      ) : templates.length === 0 ? (
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-12 text-center">
          <FileText className="w-16 h-16 text-aegisaxiom-text-muted mx-auto mb-4" />
          <h3 className="text-lg font-semibold text-aegisaxiom-text mb-2">No Templates Yet</h3>
          <p className="text-aegisaxiom-text-secondary text-sm mb-6">
            Create your first contract template to streamline Sales Ops workflows
          </p>
          <button
            onClick={() => setShowModal(true)}
            className="px-6 py-3 bg-violet-600 hover:bg-violet-500 text-white rounded-lg transition"
          >
            Create First Template
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {templates.map((template) => {
            const typeConfig = CONTRACT_TYPES.find((t) => t.value === template.contract_type);
            const TypeIcon = typeConfig?.icon || FileText;

            return (
              <div
                key={template.template_id}
                className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-5 hover:border-aegisaxiom-border-hover transition"
                data-testid={`template-${template.template_id}`}
              >
                {/* Header */}
                <div className="flex items-start justify-between gap-3 mb-4">
                  <div className="flex-1 min-w-0">
                    <h3 className="font-semibold text-aegisaxiom-text text-lg mb-1">
                      {template.template_name}
                    </h3>
                    {template.description && (
                      <p className="text-sm text-aegisaxiom-text-secondary">{template.description}</p>
                    )}
                  </div>
                  <div className="flex gap-1 shrink-0">
                    <button
                      onClick={() => handleEdit(template)}
                      className="p-2 text-aegisaxiom-text-secondary hover:text-violet-600 dark:hover:text-violet-400 hover:bg-violet-50 dark:hover:bg-violet-600/10 rounded transition"
                      title="Edit template"
                      aria-label={`Edit ${template.template_name}`}
                    >
                      <Edit2 className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleDelete(template.template_id)}
                      className="p-2 text-aegisaxiom-text-secondary hover:text-red-600 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition"
                      title="Delete template"
                      aria-label={`Delete ${template.template_name}`}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* Contract Type Badge */}
                <div className="flex items-center gap-2 mb-4">
                  <TypeIcon className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                  <span className="text-sm font-medium text-violet-700 dark:text-violet-300">
                    {typeConfig?.label || template.contract_type}
                  </span>
                </div>

                {/* Configuration Summary */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-2 text-sm">
                  <div>
                    <span className="text-aegisaxiom-text-secondary">Billing Terms:</span>
                    <span className="text-aegisaxiom-text ml-2 font-medium capitalize">
                      {template.billing_terms?.replace('_', ' ')}
                    </span>
                  </div>
                  <div>
                    <span className="text-aegisaxiom-text-secondary">Payment Terms:</span>
                    <span className="text-aegisaxiom-text ml-2 font-medium">
                      {template.payment_terms?.toUpperCase().replace('_', ' ')}
                    </span>
                  </div>

                  {template.default_hours && (
                    <div>
                      <span className="text-aegisaxiom-text-secondary">Default Hours:</span>
                      <span className="text-aegisaxiom-text ml-2 font-medium">
                        {template.default_hours}h
                      </span>
                    </div>
                  )}

                  {template.default_sell_rate && (
                    <div>
                      <span className="text-aegisaxiom-text-secondary">Default Sell Rate:</span>
                      <span className="text-emerald-600 dark:text-emerald-400 ml-2 font-semibold">
                        {formatCurrency(template.default_sell_rate)}/hr
                      </span>
                    </div>
                  )}

                  {template.default_fixed_amount && (
                    <div>
                      <span className="text-aegisaxiom-text-secondary">Fixed Amount:</span>
                      <span className="text-emerald-600 dark:text-emerald-400 ml-2 font-semibold">
                        {formatCurrency(template.default_fixed_amount)}
                      </span>
                    </div>
                  )}

                  {template.default_monthly_rate && (
                    <div>
                      <span className="text-aegisaxiom-text-secondary">Monthly Rate:</span>
                      <span className="text-emerald-600 dark:text-emerald-400 ml-2 font-semibold">
                        {formatCurrency(template.default_monthly_rate)}/mo
                      </span>
                    </div>
                  )}
                </div>

                {template.convert_to_boa_at_exhaustion && (
                  <div className="mt-3 flex items-center gap-2 text-xs text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700/30 rounded px-3 py-2">
                    <AlertCircle className="w-3 h-3 shrink-0" />
                    Auto-convert to BOA at exhaustion
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Create/Edit Modal */}
      {showModal && (
        <TemplateFormModal
          template={editingTemplate}
          onClose={() => {
            setShowModal(false);
            setEditingTemplate(null);
          }}
          onSuccess={() => {
            setShowModal(false);
            setEditingTemplate(null);
            loadTemplates();
          }}
        />
      )}
      <ConfirmModal
        isOpen={confirmState.open}
        title={confirmState.title}
        message={confirmState.message}
        confirmText="Delete"
        onConfirm={() => confirmState.action?.()}
        onCancel={() => setConfirmState({ open: false, action: null, title: '', message: '' })}
      />
    </div>
  );
}

// Template Form Modal
function TemplateFormModal({ template, onClose, onSuccess }) {
  const [form, setForm] = useState({
    template_name: '',
    description: '',
    contract_type: 'boh',
    default_hours: '',
    default_sell_rate: '',
    default_fixed_amount: '',
    default_monthly_rate: '',
    billing_terms: 'monthly',
    payment_terms: 'net_30',
    convert_to_boa_at_exhaustion: false,
    default_contract_duration_days: '',
  });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (template) {
      setForm({
        template_name: template.template_name || '',
        description: template.description || '',
        contract_type: template.contract_type || 'boh',
        default_hours: template.default_hours || '',
        default_sell_rate: template.default_sell_rate || '',
        default_fixed_amount: template.default_fixed_amount || '',
        default_monthly_rate: template.default_monthly_rate || '',
        billing_terms: template.billing_terms || 'monthly',
        payment_terms: template.payment_terms || 'net_30',
        convert_to_boa_at_exhaustion: template.convert_to_boa_at_exhaustion || false,
        default_contract_duration_days: template.default_contract_duration_days || '',
      });
    }
  }, [template]);

  const handleSave = async () => {
    // Validation
    if (!form.template_name.trim()) {
      toast.error('Template name is required');
      return;
    }
    if (!form.contract_type) {
      toast.error('Contract type is required');
      return;
    }

    try {
      setSaving(true);

      const payload = {
        template_name: form.template_name.trim(),
        description: form.description.trim() || null,
        contract_type: form.contract_type,
        default_hours: form.default_hours ? parseFloat(form.default_hours) : null,
        default_sell_rate: form.default_sell_rate ? parseFloat(form.default_sell_rate) : null,
        default_fixed_amount: form.default_fixed_amount
          ? parseFloat(form.default_fixed_amount)
          : null,
        default_monthly_rate: form.default_monthly_rate
          ? parseFloat(form.default_monthly_rate)
          : null,
        billing_terms: form.billing_terms,
        payment_terms: form.payment_terms,
        convert_to_boa_at_exhaustion: form.convert_to_boa_at_exhaustion,
        default_contract_duration_days: form.default_contract_duration_days
          ? parseInt(form.default_contract_duration_days)
          : null,
      };

      const response = template
        ? await api.put(`${API}/api/admin/contract-templates/${template.template_id}`, payload)
        : await api.post(`${API}/api/admin/contract-templates`, payload);

      if (response.ok) {
        toast.success(template ? 'Template updated successfully' : 'Template created successfully');
        onSuccess();
      } else {
        const error = await response.json();
        toast.error(error.detail || 'Failed to save template');
      }
    } catch (error) {
      console.error('Error saving template:', error);
      toast.error('Failed to save template');
    } finally {
      setSaving(false);
    }
  };

  const selectedType = CONTRACT_TYPES.find((t) => t.value === form.contract_type);
  const TypeIcon = selectedType?.icon || FileText;

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-2 sm:p-4">
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-2xl max-h-[95vh] sm:max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="sticky top-0 z-10 bg-aegisaxiom-surface border-b border-aegisaxiom-border p-4 sm:p-6 rounded-t-lg">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-base sm:text-xl font-bold text-aegisaxiom-text">
              {template ? 'Edit Template' : 'Create Contract Template'}
            </h2>
            <button
              onClick={onClose}
              className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text p-1 -m-1 rounded transition shrink-0"
              aria-label="Close"
            >
              <X className="w-6 h-6" />
            </button>
          </div>
        </div>

        {/* Form */}
        <div className="p-4 sm:p-6 space-y-6">
          {/* Template Name */}
          <div>
            <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
              Template Name *
            </label>
            <input
              type="text"
              value={form.template_name}
              onChange={(e) => setForm((f) => ({ ...f, template_name: e.target.value }))}
              placeholder="e.g., Standard BOH Net-30"
              className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500"
            />
          </div>

          {/* Description */}
          <div>
            <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
              Description
            </label>
            <textarea
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              placeholder="Brief description of this template..."
              rows={2}
              className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 resize-none"
            />
          </div>

          {/* Contract Type */}
          <div>
            <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
              Contract Type *
            </label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {CONTRACT_TYPES.map((type) => {
                const Icon = type.icon;
                const isSelected = form.contract_type === type.value;

                return (
                  <button
                    key={type.value}
                    type="button"
                    onClick={() => setForm((f) => ({ ...f, contract_type: type.value }))}
                    className={`flex items-center gap-3 px-4 py-3 border-2 rounded-lg transition ${
                      isSelected
                        ? 'bg-violet-50 dark:bg-violet-600/20 border-violet-500 text-violet-700 dark:text-violet-300'
                        : 'bg-aegisaxiom-surface border-aegisaxiom-border text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover'
                    }`}
                  >
                    <Icon
                      className={`w-5 h-5 shrink-0 ${isSelected ? 'text-violet-600 dark:text-violet-400' : 'text-aegisaxiom-text-muted'}`}
                    />
                    <span className="font-medium text-sm">{type.label}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Customer Billing Configuration */}
          <div className="bg-blue-50 dark:bg-blue-900/10 border border-blue-200 dark:border-blue-700/30 rounded-lg p-4 sm:p-5">
            <h3 className="text-sm font-bold text-blue-700 dark:text-blue-300 mb-4 uppercase tracking-wide">
              Customer Billing Configuration
            </h3>

            <div className="space-y-4">
              {/* Conditional Fields Based on Contract Type */}
              {(form.contract_type === 'boh' ||
                form.contract_type === 'boa' ||
                form.contract_type === 'monthly') && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                  {form.contract_type === 'boh' && (
                    <div>
                      <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                        Default Hours
                      </label>
                      <input
                        type="number"
                        value={form.default_hours}
                        onChange={(e) => setForm((f) => ({ ...f, default_hours: e.target.value }))}
                        placeholder="200"
                        step="1"
                        className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                      />
                    </div>
                  )}

                  <div>
                    <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                      Default Sell Rate ($/hr)
                    </label>
                    <input
                      type="number"
                      value={form.default_sell_rate}
                      onChange={(e) =>
                        setForm((f) => ({ ...f, default_sell_rate: e.target.value }))
                      }
                      placeholder="180"
                      step="0.01"
                      className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                    />
                  </div>
                </div>
              )}

              {form.contract_type === 'fixed_fee' && (
                <div>
                  <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                    Default Fixed Amount
                  </label>
                  <input
                    type="number"
                    value={form.default_fixed_amount}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, default_fixed_amount: e.target.value }))
                    }
                    placeholder="25000"
                    step="0.01"
                    className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                  />
                </div>
              )}

              {form.contract_type === 'monthly' && (
                <div>
                  <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                    Default Monthly Rate
                  </label>
                  <input
                    type="number"
                    value={form.default_monthly_rate}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, default_monthly_rate: e.target.value }))
                    }
                    placeholder="5000"
                    step="0.01"
                    className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                  />
                </div>
              )}

              {/* Billing & Payment Terms */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                <div>
                  <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                    Billing Terms
                  </label>
                  <select
                    value={form.billing_terms}
                    onChange={(e) => setForm((f) => ({ ...f, billing_terms: e.target.value }))}
                    className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                  >
                    {BILLING_TERMS.map((term) => (
                      <option key={term.value} value={term.value}>
                        {term.label}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                    Payment Terms
                  </label>
                  <select
                    value={form.payment_terms}
                    onChange={(e) => setForm((f) => ({ ...f, payment_terms: e.target.value }))}
                    className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                  >
                    {PAYMENT_TERMS.map((term) => (
                      <option key={term.value} value={term.value}>
                        {term.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* BOH Specific Options */}
              {form.contract_type === 'boh' && (
                <div className="flex items-center gap-3 pt-2">
                  <input
                    type="checkbox"
                    id="convert-to-boa"
                    checked={form.convert_to_boa_at_exhaustion}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, convert_to_boa_at_exhaustion: e.target.checked }))
                    }
                    className="w-4 h-4 text-violet-600 bg-aegisaxiom-surface border-aegisaxiom-border rounded focus:ring-violet-500"
                  />
                  <label htmlFor="convert-to-boa" className="text-sm text-aegisaxiom-text-secondary">
                    Auto-convert to BOA when hours exhausted
                  </label>
                </div>
              )}

              {/* Contract Duration */}
              <div>
                <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                  Default Contract Duration (days)
                </label>
                <input
                  type="number"
                  value={form.default_contract_duration_days}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, default_contract_duration_days: e.target.value }))
                  }
                  placeholder="90"
                  step="1"
                  className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                />
                <p className="text-xs text-aegisaxiom-text-muted mt-1">
                  Optional: Auto-calculate end date based on start date + this duration
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="sticky bottom-0 z-10 bg-aegisaxiom-surface border-t border-aegisaxiom-border p-4 sm:p-6 rounded-b-lg flex flex-col-reverse sm:flex-row sm:justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg transition w-full sm:w-auto"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center justify-center gap-2 px-6 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg transition disabled:opacity-50 w-full sm:w-auto"
            data-testid="save-template-btn"
          >
            <Save className="w-4 h-4" />
            {saving ? 'Saving...' : template ? 'Update Template' : 'Create Template'}
          </button>
        </div>
      </div>
    </div>
  );
}
