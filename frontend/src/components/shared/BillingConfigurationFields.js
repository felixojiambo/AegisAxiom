import React from 'react';
import { Plus, Calendar, Clock, TrendingUp, X } from 'lucide-react';

/**
 * Reusable Billing Configuration Component
 * Used in both SOW Builder (Sales Ops) and Deal Management
 * Ensures unified contract configuration across the application
 *
 * Supports: BOH, BOA, Fixed Fee, Monthly
 */

const BILLING_SCHEDULE_TRIGGER_TYPES = [
  { value: 'date', label: 'Specific Date (Invoice on a set date)' },
  { value: 'recurring', label: 'Recurring (Weekly/Monthly from first invoice)' },
  { value: 'usage', label: 'Usage-Based (When hours remaining hits threshold)' },
];

const RECURRING_FREQUENCIES = [
  { value: 'weekly', label: 'Weekly' },
  { value: 'biweekly', label: 'Bi-weekly' },
  { value: 'monthly', label: 'Monthly' },
];

export default function BillingConfigurationFields({
  contractType,
  billingSchedule = [],
  hoursPurchased = 0,
  sellRate = 0,
  fixedAmount = 0,
  monthlyRate = 0,
  onChange,
  onFormChange, // For BOA/Fixed Fee/Monthly specific fields
  formData = {}, // Contains billing_frequency, boa_billing_day, billing_method, milestones, etc.
  compact = false,
  serviceCategory = null, // field_services, professional_services (for BOA cost estimation)
}) {
  const addBillingItem = () => {
    const newItem = {
      id: `bs_${Date.now()}`,
      trigger_type: 'date',
      description: '',
      hours: 0,
      amount: 0,
      invoice_date: '',
      recurring_frequency: 'monthly',
      recurring_count: 1,
      usage_threshold: 10,
      status: 'pending',
    };
    onChange([...billingSchedule, newItem]);
  };

  const updateBillingItem = (index, field, value) => {
    const updated = [...billingSchedule];
    updated[index] = { ...updated[index], [field]: value };

    // Auto-calculate amount when hours change
    if (field === 'hours' && sellRate > 0) {
      const hours = parseFloat(value) || 0;
      updated[index].amount = (hours * sellRate).toFixed(2);
    }

    onChange(updated);
  };

  const removeBillingItem = (index) => {
    const updated = billingSchedule.filter((_, idx) => idx !== index);
    onChange(updated);
  };

  const addMilestone = () => {
    const newMilestone = {
      id: `ms_${Date.now()}`,
      name: '',
      percentage: formData.billing_method === 'percentage' ? 0 : undefined,
      amount: formData.billing_method !== 'percentage' ? 0 : undefined,
      due_date: '',
      status: 'pending',
    };
    const updated = [...(formData.milestones || []), newMilestone];
    onFormChange?.('milestones', updated);
  };

  const updateMilestone = (index, field, value) => {
    const updated = [...(formData.milestones || [])];
    updated[index] = { ...updated[index], [field]: value };
    onFormChange?.('milestones', updated);
  };

  const removeMilestone = (index) => {
    const updated = (formData.milestones || []).filter((_, idx) => idx !== index);
    onFormChange?.('milestones', updated);
  };

  const inputClass =
    'w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text focus:outline-none focus:border-violet-500';

  // ===== BOH: Block of Hours =====
  if (contractType === 'boh') {
    return (
      <div className="border border-blue-700/30 rounded-lg p-3 bg-blue-900/10">
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-xs font-semibold text-blue-700 dark:text-blue-300">
            💰 Billing Schedule - Block of Hours
          </h4>
          <button
            type="button"
            onClick={addBillingItem}
            className="text-xs px-2 py-1 bg-blue-50 dark:bg-blue-600/20 text-blue-600 dark:text-blue-400 hover:bg-blue-600/30 rounded flex items-center gap-1"
          >
            <Plus size={12} /> Add Billing Item
          </button>
        </div>

        <div className="space-y-3">
          {billingSchedule.length === 0 && (
            <p className="text-xs text-aegisaxiom-text-muted text-center py-4">
              Add billing items to define when invoices should be sent
            </p>
          )}

          {billingSchedule.map((bs, i) => (
            <div
              key={bs.id || i}
              className="bg-aegisaxiom-accent-soft rounded p-3 space-y-2 border border-blue-800/30"
            >
              {/* Trigger Type Selection */}
              <div>
                <label className="text-[10px] text-aegisaxiom-text-muted font-semibold block mb-1">
                  Billing Trigger
                </label>
                <select
                  value={bs.trigger_type || 'date'}
                  onChange={(e) => updateBillingItem(i, 'trigger_type', e.target.value)}
                  className={inputClass}
                >
                  {BILLING_SCHEDULE_TRIGGER_TYPES.map((type) => (
                    <option key={type.value} value={type.value}>
                      {type.label}
                    </option>
                  ))}
                </select>
              </div>

              {/* Basic Fields */}
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="text-[10px] text-aegisaxiom-text-muted block mb-1">Description</label>
                  <input
                    value={bs.description || ''}
                    onChange={(e) => updateBillingItem(i, 'description', e.target.value)}
                    placeholder="e.g., Upfront Payment"
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className="text-[10px] text-aegisaxiom-text-muted block mb-1">Hours</label>
                  <input
                    type="number"
                    value={bs.hours || 0}
                    onChange={(e) => updateBillingItem(i, 'hours', e.target.value)}
                    placeholder="0"
                    step="0.1"
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className="text-[10px] text-aegisaxiom-text-muted block mb-1">Amount ($)</label>
                  <input
                    type="number"
                    value={bs.amount || 0}
                    onChange={(e) => updateBillingItem(i, 'amount', e.target.value)}
                    placeholder="0"
                    step="0.01"
                    className={inputClass}
                  />
                </div>
              </div>

              {/* Trigger-Specific Fields */}
              {bs.trigger_type === 'date' && (
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[10px] text-aegisaxiom-text-muted block mb-1 flex items-center gap-1">
                      <Calendar size={10} />
                      Invoice Date
                    </label>
                    <input
                      type="date"
                      value={bs.invoice_date || ''}
                      onChange={(e) => updateBillingItem(i, 'invoice_date', e.target.value)}
                      className={inputClass}
                    />
                  </div>
                  <div className="flex items-end">
                    <label className="flex items-center gap-2 text-xs text-aegisaxiom-text-secondary cursor-pointer">
                      <input
                        type="checkbox"
                        checked={bs.convert_to_boa_at_exhaustion || false}
                        onChange={(e) =>
                          updateBillingItem(i, 'convert_to_boa_at_exhaustion', e.target.checked)
                        }
                        className="w-3 h-3"
                      />
                      <span>Convert to BOA at exhaustion</span>
                    </label>
                  </div>
                </div>
              )}

              {bs.trigger_type === 'recurring' && (
                <div className="grid grid-cols-3 gap-2">
                  <div>
                    <label className="text-[10px] text-aegisaxiom-text-muted block mb-1 flex items-center gap-1">
                      <Calendar size={10} />
                      First Invoice Date
                    </label>
                    <input
                      type="date"
                      value={bs.invoice_date || ''}
                      onChange={(e) => updateBillingItem(i, 'invoice_date', e.target.value)}
                      className={inputClass}
                    />
                  </div>
                  <div>
                    <label className="text-[10px] text-aegisaxiom-text-muted block mb-1 flex items-center gap-1">
                      <Clock size={10} />
                      Frequency
                    </label>
                    <select
                      value={bs.recurring_frequency || 'monthly'}
                      onChange={(e) => updateBillingItem(i, 'recurring_frequency', e.target.value)}
                      className={inputClass}
                    >
                      {RECURRING_FREQUENCIES.map((freq) => (
                        <option key={freq.value} value={freq.value}>
                          {freq.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-[10px] text-aegisaxiom-text-muted block mb-1">Occurrences</label>
                    <input
                      type="number"
                      value={bs.recurring_count || 1}
                      onChange={(e) => updateBillingItem(i, 'recurring_count', e.target.value)}
                      placeholder="1"
                      min="1"
                      className={inputClass}
                    />
                  </div>
                </div>
              )}

              {bs.trigger_type === 'usage' && (
                <div>
                  <label className="text-[10px] text-aegisaxiom-text-muted block mb-1 flex items-center gap-1">
                    <TrendingUp size={10} />
                    Trigger Threshold (Hours Remaining)
                  </label>
                  <input
                    type="number"
                    value={bs.usage_threshold || 10}
                    onChange={(e) => updateBillingItem(i, 'usage_threshold', e.target.value)}
                    placeholder="10"
                    step="1"
                    className={inputClass}
                  />
                  <p className="text-[9px] text-aegisaxiom-text-muted mt-1">
                    Invoice automatically sent when remaining hours reach this threshold
                  </p>
                </div>
              )}

              {/* Remove Button */}
              <div className="flex justify-end pt-2 border-t border-aegisaxiom-border">
                <button
                  type="button"
                  onClick={() => removeBillingItem(i)}
                  className="text-xs text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300"
                >
                  Remove Item
                </button>
              </div>
            </div>
          ))}

          {/* Auto-convert to BOA checkbox - shows ONLY for One-Time BOH */}
          {billingSchedule.length > 0 &&
            billingSchedule.some((bs) => bs.trigger_type === 'date') && (
              <div className="bg-yellow-50 dark:bg-yellow-900/10 border border-yellow-200 dark:border-yellow-700/30 rounded p-2 text-xs text-yellow-700 dark:text-yellow-300">
                <p className="font-semibold mb-1">💡 One-Time Block of Hours Detected</p>
                <p className="text-aegisaxiom-text-secondary text-[10px]">
                  One or more billing items are set to "Specific Date". You can enable
                  auto-conversion to BOA for these items using the checkbox above.
                </p>
              </div>
            )}
        </div>
      </div>
    );
  }

  // ===== BOA: Bill on Actuals =====
  if (contractType === 'boa') {
    return (
      <div className="border border-purple-700/30 rounded-lg p-3 bg-purple-900/10">
        <h4 className="text-xs font-semibold text-purple-300 mb-3">
          💰 Billing Terms - Bill on Actuals
        </h4>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-[10px] text-aegisaxiom-text-muted font-semibold block mb-1">
              Billing Frequency
            </label>
            <select
              value={formData.billing_frequency || 'monthly'}
              onChange={(e) => onFormChange?.('billing_frequency', e.target.value)}
              className={inputClass}
            >
              <option value="monthly">Monthly</option>
              <option value="biweekly">Bi-Weekly</option>
              <option value="manual">Manual (Admin creates)</option>
            </select>
          </div>
          {formData.billing_frequency === 'monthly' && (
            <div>
              <label className="text-[10px] text-aegisaxiom-text-muted font-semibold block mb-1">
                Billing Day (1-28)
              </label>
              <input
                type="number"
                min="1"
                max="28"
                value={formData.boa_billing_day || 1}
                onChange={(e) => onFormChange?.('boa_billing_day', parseInt(e.target.value))}
                className={inputClass}
              />
            </div>
          )}
        </div>

        {/* Estimated BOA Cost - Field Services Only */}
        {serviceCategory === 'field_services' && (
          <div className="mt-3 pt-3 border-t border-purple-700/20">
            <label className="text-[10px] text-aegisaxiom-text-muted font-semibold block mb-1">
              Estimated BOA Cost{' '}
              <span className="text-xs text-aegisaxiom-text-secondary">(for Field Services)</span>
            </label>
            <div className="relative">
              <span className="absolute left-2 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted text-xs">
                $
              </span>
              <input
                type="number"
                step="0.01"
                min="0"
                value={formData.estimated_boa_cost || ''}
                onChange={(e) => onFormChange?.('estimated_boa_cost', e.target.value)}
                placeholder="0.00"
                className={`${inputClass} pl-6`}
              />
            </div>
            <p className="text-[9px] text-aegisaxiom-text-muted mt-1">
              Estimated total cost for this BOA contract (Field Services tracking)
            </p>
          </div>
        )}
      </div>
    );
  }

  // ===== FIXED FEE =====
  if (contractType === 'fixed_fee') {
    return (
      <div className="border border-amber-200 dark:border-amber-700/30 rounded-lg p-3 bg-amber-50 dark:bg-amber-900/10">
        <h4 className="text-xs font-semibold text-amber-700 dark:text-amber-300 mb-3">💰 Billing Terms - Fixed Fee</h4>

        <div className="mb-3">
          <label className="text-[10px] text-aegisaxiom-text-muted font-semibold block mb-1">
            Billing Method
          </label>
          <select
            value={formData.billing_method || 'percentage'}
            onChange={(e) => {
              onFormChange?.('billing_method', e.target.value);
              onFormChange?.('milestones', []); // Reset milestones on method change
            }}
            className={inputClass}
          >
            <option value="percentage">
              Percentage-Based (e.g., 50% upfront, 25% mid, 25% end)
            </option>
            <option value="date">Date-Based (Invoice on specific dates)</option>
            <option value="milestone">Milestone-Based (Invoice when milestones complete)</option>
          </select>
        </div>

        {/* Milestones */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="text-[10px] text-aegisaxiom-text-secondary">Milestones</label>
            <button
              type="button"
              onClick={addMilestone}
              className="text-xs px-2 py-1 bg-amber-50 dark:bg-amber-600/20 text-amber-600 dark:text-amber-400 hover:bg-amber-600/30 rounded flex items-center gap-1"
            >
              <Plus size={12} /> Add Milestone
            </button>
          </div>

          {(formData.milestones || []).length === 0 && (
            <p className="text-xs text-aegisaxiom-text-muted text-center py-4">
              Add milestones to define payment schedule
            </p>
          )}

          {(formData.milestones || []).map((ms, i) => (
            <div
              key={ms.id || i}
              className="bg-aegisaxiom-accent-soft rounded p-2 space-y-2 border border-amber-200 dark:border-amber-800/30"
            >
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[10px] text-aegisaxiom-text-muted block mb-1">Milestone Name</label>
                  <input
                    value={ms.name || ''}
                    onChange={(e) => updateMilestone(i, 'name', e.target.value)}
                    placeholder="e.g., Upfront Payment"
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className="text-[10px] text-aegisaxiom-text-muted block mb-1">
                    {formData.billing_method === 'percentage' ? 'Percentage (%)' : 'Amount ($)'}
                  </label>
                  <input
                    type="number"
                    step={formData.billing_method === 'percentage' ? '0.1' : '0.01'}
                    value={
                      formData.billing_method === 'percentage'
                        ? ms.percentage || ''
                        : ms.amount || ''
                    }
                    onChange={(e) =>
                      updateMilestone(
                        i,
                        formData.billing_method === 'percentage' ? 'percentage' : 'amount',
                        e.target.value,
                      )
                    }
                    placeholder="0"
                    className={inputClass}
                  />
                </div>
              </div>

              {formData.billing_method !== 'milestone' && (
                <div>
                  <label className="text-[10px] text-aegisaxiom-text-muted block mb-1">Due Date</label>
                  <input
                    type="date"
                    value={ms.due_date || ''}
                    onChange={(e) => updateMilestone(i, 'due_date', e.target.value)}
                    className={inputClass}
                  />
                </div>
              )}

              <div className="flex justify-end pt-1">
                <button
                  type="button"
                  onClick={() => removeMilestone(i)}
                  className="text-xs text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300 flex items-center gap-1"
                >
                  <X size={12} /> Remove
                </button>
              </div>
            </div>
          ))}

          {/* Milestone Total Validation */}
          {formData.billing_method === 'percentage' && (formData.milestones || []).length > 0 && (
            <div
              className={`text-xs px-2 py-1 rounded ${
                Math.abs(
                  (formData.milestones || []).reduce(
                    (sum, ms) => sum + (parseFloat(ms.percentage) || 0),
                    0,
                  ) - 100,
                ) < 0.01
                  ? 'bg-green-50 dark:bg-green-900/30 text-green-600 dark:text-green-400'
                  : 'bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400'
              }`}
            >
              Total:{' '}
              {(formData.milestones || []).reduce(
                (sum, ms) => sum + (parseFloat(ms.percentage) || 0),
                0,
              )}
              %
              {Math.abs(
                (formData.milestones || []).reduce(
                  (sum, ms) => sum + (parseFloat(ms.percentage) || 0),
                  0,
                ) - 100,
              ) < 0.01
                ? ' ✓ Equals 100%'
                : ' ⚠ Must equal 100%'}
            </div>
          )}

          {formData.billing_method !== 'percentage' &&
            (formData.milestones || []).length > 0 &&
            fixedAmount > 0 && (
              <div
                className={`text-xs px-2 py-1 rounded ${
                  Math.abs(
                    (formData.milestones || []).reduce(
                      (sum, ms) => sum + (parseFloat(ms.amount) || 0),
                      0,
                    ) - fixedAmount,
                  ) < 0.01
                    ? 'bg-green-50 dark:bg-green-900/30 text-green-600 dark:text-green-400'
                    : 'bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400'
                }`}
              >
                Total: $
                {(formData.milestones || [])
                  .reduce((sum, ms) => sum + (parseFloat(ms.amount) || 0), 0)
                  .toLocaleString()}
                {Math.abs(
                  (formData.milestones || []).reduce(
                    (sum, ms) => sum + (parseFloat(ms.amount) || 0),
                    0,
                  ) - fixedAmount,
                ) < 0.01
                  ? ` ✓ Equals Fixed Amount ($${fixedAmount.toLocaleString()})`
                  : ` ⚠ Must equal $${fixedAmount.toLocaleString()}`}
              </div>
            )}
        </div>
      </div>
    );
  }

  // ===== MONTHLY: Monthly Retainer =====
  if (contractType === 'monthly') {
    return (
      <div className="border border-amber-200 dark:border-amber-700/30 rounded-lg p-3 bg-amber-50 dark:bg-amber-900/10">
        <h4 className="text-xs font-semibold text-amber-700 dark:text-amber-300 mb-3">
          💰 Billing Terms - Monthly Retainer
        </h4>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-[10px] text-aegisaxiom-text-muted font-semibold block mb-1">
              Billing Frequency
            </label>
            <select
              value={formData.billing_frequency || 'monthly'}
              onChange={(e) => onFormChange?.('billing_frequency', e.target.value)}
              className={inputClass}
            >
              <option value="monthly">Monthly</option>
              <option value="quarterly">Quarterly</option>
              <option value="annual">Annual</option>
            </select>
          </div>
          <div>
            <label className="text-[10px] text-aegisaxiom-text-muted font-semibold block mb-1">
              Billing Day (1-28)
            </label>
            <input
              type="number"
              min="1"
              max="28"
              value={formData.boa_billing_day || 1}
              onChange={(e) => onFormChange?.('boa_billing_day', parseInt(e.target.value))}
              className={inputClass}
            />
          </div>
        </div>

        <p className="text-[10px] text-aegisaxiom-text-muted mt-2">
          Monthly rate:{' '}
          <span className="text-green-600 dark:text-green-400 font-semibold">
            ${(monthlyRate || 0).toLocaleString()}/month
          </span>
        </p>
      </div>
    );
  }

  // Default: return null for unsupported contract types
  return null;
}
