import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { api } from '../../lib/api';
import API_URL from '../../lib/apiConfig';
import BillingConfigurationFields from '../shared/BillingConfigurationFields';
import {
  X,
  AlertCircle,
  TrendingUp,
  DollarSign,
  Users,
  Building2,
  Clock,
  FileText,
} from 'lucide-react';

const API = API_URL;

const CONTRACT_TYPES = [
  { value: 'boh', label: 'Block of Hours (BOH)' },
  { value: 'boa', label: 'Bill on Actuals (BOA)' },
  { value: 'fixed_fee', label: 'Fixed Fee' },
  { value: 'monthly', label: 'Monthly Retainer' },
];

const BILLING_SCHEDULE_TYPES = [
  { value: 'date', label: 'Specific Date (Invoice on a set date)' },
  { value: 'recurring', label: 'Recurring on a Schedule' },
  { value: 'usage', label: 'Usage-Based (Trigger when hours hit threshold)' },
];

const RECURRING_FREQUENCIES = [
  { value: 'weekly', label: 'Weekly' },
  { value: 'biweekly', label: 'Bi-weekly' },
  { value: 'monthly', label: 'Monthly' },
];

const FIXED_PRICE_BILLING_TERMS = [
  { value: 'one_time', label: 'One-Time Payment' },
  { value: 'milestone', label: 'Milestone-Based' },
];

const PAYMENT_TERMS = [
  { value: 'net_7', label: 'Net 7' },
  { value: 'net_14', label: 'Net 14' },
  { value: 'net_30', label: 'Net 30' },
  { value: 'net_45', label: 'Net 45' },
  { value: 'net_60', label: 'Net 60' },
];

const PAY_TYPES = [
  { value: 'block_hours', label: 'Block Hours' },
  { value: 'bill_actuals', label: 'Bill Actuals' },
  { value: 'fixed_price', label: 'Fixed Price' },
  { value: 'monthly', label: 'Monthly' },
];

export default function ResourceAssignmentForm({
  opportunity,
  sow,
  contractors,
  resourceTypes,
  templates,
  updateSOW,
  onClose,
  onSuccess,
}) {
  // Template Selection
  const [selectedTemplateId, setSelectedTemplateId] = useState('');

  // LEFT Side State (Customer Billing - What We Sell)
  const [leftForm, setLeftForm] = useState({
    resource_type_id: '',
    resource_type_name: '',
    customer_contract_type: 'boh',
    customer_billing_schedule: [], // Array of billing items (unified with ContractFormFields)
    customer_payment_terms: 'net_30',
    customer_hours_purchased: '',
    customer_sell_rate: '',
    customer_fixed_amount: '',
    customer_monthly_rate: '',
    // BOA-specific fields
    billing_frequency: 'monthly',
    boa_billing_day: 1,
    estimated_boa_cost: '',
    // Fixed Fee-specific fields
    billing_method: 'percentage',
    milestones: [],
  });

  // RIGHT Side State (Contractor Cost - What We Buy)
  const [rightForm, setRightForm] = useState({
    contractor_business_id: '',
    contractor_business_name: '',
    contractor_user_id: '',
    contractor_user_name: '',
    contractor_pay_type: '',
    contractor_billing_terms: 'weekly',
    contractor_payment_terms: 'net_7',
    contractor_hours_estimated: '',
    contractor_cost_rate: '',
    contractor_fixed_cost: '',
    contractor_monthly_cost: '',
  });

  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [filteredContractors, setFilteredContractors] = useState([]);
  const [loadingContractors, setLoadingContractors] = useState(false);
  const [availableContractorUsers, setAvailableContractorUsers] = useState([]);

  const selectedResourceType = resourceTypes.find(
    (rt) => rt.resource_type_id === leftForm.resource_type_id,
  );
  const selectedTemplate = templates.find((t) => t.template_id === selectedTemplateId);

  // Apply template to LEFT side when template is selected
  useEffect(() => {
    if (selectedTemplate) {
      setLeftForm((prev) => ({
        ...prev,
        customer_contract_type: selectedTemplate.contract_type || 'boh',
        customer_billing_terms: selectedTemplate.billing_terms || 'monthly',
        customer_payment_terms: selectedTemplate.payment_terms || 'net_30',
        customer_hours_purchased: selectedTemplate.default_hours || '',
        customer_sell_rate: selectedTemplate.default_sell_rate || '',
        customer_fixed_amount: selectedTemplate.default_fixed_amount || '',
        customer_monthly_rate: selectedTemplate.default_monthly_rate || '',
        convert_to_boa_at_exhaustion: selectedTemplate.convert_to_boa_at_exhaustion || false,
      }));
      toast.success(`Template "${selectedTemplate.template_name}" applied`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTemplateId]);

  // Auto-filter contractors when resource type is selected
  useEffect(() => {
    if (leftForm.resource_type_id && selectedResourceType) {
      filterContractorsByResourceType(selectedResourceType.name);
    } else {
      setFilteredContractors([]);
      setAvailableContractorUsers([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leftForm.resource_type_id]);

  const filterContractorsByResourceType = async (resourceTypeName) => {
    setLoadingContractors(true);
    try {
      const response = await api.get(`${API}/api/sales-ops/contractor-businesses`);
      if (response.ok) {
        const data = await response.json();
        const businesses = data.items || [];

        const filtered = businesses.filter((biz) => {
          const offered = biz.resource_types_offered || [];
          return offered.some((rt) => rt.resource_type_name === resourceTypeName);
        });

        setFilteredContractors(filtered);

        const usersRes = await api.get(`${API}/api/admin/users?role=contractor&limit=100`);
        if (usersRes.ok) {
          const users = await usersRes.json();
          const filteredUsers = users.filter((user) => {
            const hasBusinessId = !!user.contractor_business_id;
            const assignments = user.resource_type_assignments || [];
            const hasResourceType = assignments.some(
              (rt) => rt.resource_type_name === resourceTypeName,
            );
            return hasBusinessId && hasResourceType;
          });
          setAvailableContractorUsers(filteredUsers);
        }
      }
    } catch (error) {
      console.error('Error filtering contractors:', error);
      toast.error('Failed to filter contractors');
    } finally {
      setLoadingContractors(false);
    }
  };

  // Auto-update billing terms when contractor pay type changes to/from fixed_price
  useEffect(() => {
    if (
      rightForm.contractor_pay_type === 'fixed_price' &&
      rightForm.contractor_billing_terms === 'weekly'
    ) {
      setRightForm((prev) => ({ ...prev, contractor_billing_terms: 'one_time' }));
    } else if (
      rightForm.contractor_pay_type !== 'fixed_price' &&
      rightForm.contractor_billing_terms === 'one_time'
    ) {
      setRightForm((prev) => ({ ...prev, contractor_billing_terms: 'weekly' }));
    }
  }, [rightForm.contractor_pay_type]);

  // Auto-fill cost rate when contractor user is selected
  useEffect(() => {
    if (rightForm.contractor_user_id && selectedResourceType) {
      const user = availableContractorUsers.find((u) => u.user_id === rightForm.contractor_user_id);
      if (user) {
        const assignment = user.resource_type_assignments?.find(
          (rt) => rt.resource_type_name === selectedResourceType.name,
        );
        if (assignment) {
          setRightForm((prev) => ({
            ...prev,
            contractor_cost_rate: assignment.cost_rate || '',
          }));
        }
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rightForm.contractor_user_id, selectedResourceType]);

  // Get available pay types for selected resource from selected business
  const getAvailablePayTypesForResource = () => {
    if (!rightForm.contractor_business_id || !selectedResourceType) return [];

    const business = filteredContractors.find(
      (b) => b.business_id === rightForm.contractor_business_id,
    );
    if (!business) return [];

    const offeredResource = business.resource_types_offered?.find(
      (rt) => rt.resource_type_name === selectedResourceType.name,
    );

    return offeredResource?.pricing_models_accepted || [];
  };

  const availablePayTypes = getAvailablePayTypesForResource();
  const contractorUsersForBusiness = rightForm.contractor_business_id
    ? availableContractorUsers.filter(
        (u) => u.contractor_business_id === rightForm.contractor_business_id,
      )
    : availableContractorUsers;

  // Calculate profit metrics in real-time
  const calculateMetrics = () => {
    let revenue = 0;
    let cost = 0;

    // Customer revenue calculation
    if (leftForm.customer_contract_type === 'boh') {
      const hours = parseFloat(leftForm.customer_hours_purchased || 0);
      const rate = parseFloat(leftForm.customer_sell_rate || 0);
      revenue = hours * rate;
    } else if (leftForm.customer_contract_type === 'boa') {
      const hours = parseFloat(rightForm.contractor_hours_estimated || 0);
      const rate = parseFloat(leftForm.customer_sell_rate || 0);
      revenue = hours * rate;
    } else if (leftForm.customer_contract_type === 'fixed_fee') {
      revenue = parseFloat(leftForm.customer_fixed_amount || 0);
    } else if (leftForm.customer_contract_type === 'monthly') {
      revenue = parseFloat(leftForm.customer_monthly_rate || 0);
    }

    // Contractor cost calculation
    if (
      rightForm.contractor_pay_type === 'block_hours' ||
      rightForm.contractor_pay_type === 'bill_actuals'
    ) {
      const hours = parseFloat(
        rightForm.contractor_hours_estimated || leftForm.customer_hours_purchased || 0,
      );
      const rate = parseFloat(rightForm.contractor_cost_rate || 0);
      cost = hours * rate;
    } else if (rightForm.contractor_pay_type === 'fixed_price') {
      cost = parseFloat(rightForm.contractor_fixed_cost || 0);
    } else if (rightForm.contractor_pay_type === 'monthly') {
      cost = parseFloat(rightForm.contractor_monthly_cost || 0);
    }

    const profit = revenue - cost;
    const margin = revenue > 0 ? (profit / revenue) * 100 : 0;

    return { revenue, cost, profit, margin };
  };

  const metrics = calculateMetrics();

  const handleSubmit = async (saveAsBlocked = false) => {
    // Validation
    if (!leftForm.resource_type_id) {
      toast.error('Please select a resource type');
      return;
    }

    // Validate based on contract type
    if (leftForm.customer_contract_type === 'boh') {
      if (!leftForm.customer_hours_purchased || !leftForm.customer_sell_rate) {
        toast.error('Please enter hours purchased and sell rate for BOH');
        return;
      }
    }
    if (
      leftForm.customer_contract_type === 'boa' ||
      leftForm.customer_contract_type === 'monthly'
    ) {
      if (!leftForm.customer_sell_rate) {
        toast.error('Please enter sell rate');
        return;
      }
    }
    if (leftForm.customer_contract_type === 'fixed_fee' && !leftForm.customer_fixed_amount) {
      toast.error('Please enter fixed amount');
      return;
    }
    if (leftForm.customer_contract_type === 'monthly' && !leftForm.customer_monthly_rate) {
      toast.error('Please enter monthly rate');
      return;
    }

    // Allow submission without contractor if saveAsBlocked is true
    if (!saveAsBlocked) {
      if (!rightForm.contractor_user_id) {
        toast.error('Please select a contractor or save as blocked');
        return;
      }
      if (!rightForm.contractor_pay_type) {
        toast.error('Please select contractor pay type');
        return;
      }
    }

    setSubmitting(true);
    try {
      // Determine status and blocker info
      let resourceStatus = 'active';
      let blockerReason = null;

      // Check for missing contractor
      if (!rightForm.contractor_user_id) {
        resourceStatus = 'blocked_missing_contractor';
        blockerReason = `No contractor assigned for ${selectedResourceType?.name}`;
      }

      // Check for negative margin (also a blocker)
      if (metrics.margin < 0) {
        resourceStatus = 'blocked_missing_contractor'; // Use same status for any blocker
        blockerReason = blockerReason
          ? `${blockerReason}; Negative margin (${metrics.margin.toFixed(1)}%)`
          : `Negative margin (${metrics.margin.toFixed(1)}%) for ${selectedResourceType?.name}`;
      }

      const payload = {
        resource_type_id: leftForm.resource_type_id,
        resource_type_name: selectedResourceType?.name,

        // Customer billing configuration
        customer_contract_type: leftForm.customer_contract_type,
        customer_billing_schedule: leftForm.customer_billing_schedule, // Unified billing schedule array
        customer_payment_terms: leftForm.customer_payment_terms,
        customer_hours_purchased: leftForm.customer_hours_purchased
          ? parseFloat(leftForm.customer_hours_purchased)
          : null,
        customer_sell_rate: leftForm.customer_sell_rate
          ? parseFloat(leftForm.customer_sell_rate)
          : null,
        customer_fixed_amount: leftForm.customer_fixed_amount
          ? parseFloat(leftForm.customer_fixed_amount)
          : null,
        customer_monthly_rate: leftForm.customer_monthly_rate
          ? parseFloat(leftForm.customer_monthly_rate)
          : null,
        total_sell: metrics.revenue,

        // Contractor fulfillment configuration (can be null if blocked)
        contractor_business_id: rightForm.contractor_business_id || null,
        contractor_business_name: rightForm.contractor_business_name || null,
        contractor_user_id: rightForm.contractor_user_id || null,
        contractor_user_name: rightForm.contractor_user_name || null,
        contractor_pay_type: rightForm.contractor_pay_type || null,
        contractor_billing_terms: rightForm.contractor_billing_terms || null,
        contractor_payment_terms: rightForm.contractor_payment_terms || null,
        contractor_hours_estimated: rightForm.contractor_hours_estimated
          ? parseFloat(rightForm.contractor_hours_estimated)
          : null,
        contractor_cost_rate: rightForm.contractor_cost_rate
          ? parseFloat(rightForm.contractor_cost_rate)
          : null,
        contractor_fixed_cost: rightForm.contractor_fixed_cost
          ? parseFloat(rightForm.contractor_fixed_cost)
          : null,
        contractor_monthly_cost: rightForm.contractor_monthly_cost
          ? parseFloat(rightForm.contractor_monthly_cost)
          : null,
        total_cost: metrics.cost,

        // Profit metrics
        profit_amount: metrics.profit,
        profit_margin: metrics.margin,

        // Status & blocker tracking
        status: resourceStatus,
        blocker_reason: blockerReason,
        procurement_notified: saveAsBlocked, // True if we're saving without contractor

        notes: notes,
      };

      const response = await api.post(
        `${API}/api/sales-ops/opportunities/${opportunity.opportunity_id}/resource-assignments`,
        payload,
      );

      if (response.ok) {
        const newAssignment = await response.json();

        const currentSelections = sow.selected_resource_assignment_ids || [];
        const updatedSelections = [...currentSelections, newAssignment.assignment_id];

        updateSOW('selected_resource_assignment_ids', updatedSelections);

        // Check for blockers (missing contractor OR negative margin)
        const hasBlocker = resourceStatus === 'blocked_missing_contractor' || metrics.margin < 0;

        if (hasBlocker) {
          // Build blocker messages
          const blockerMessages = [];
          if (resourceStatus === 'blocked_missing_contractor') {
            blockerMessages.push(`Missing contractor for ${selectedResourceType?.name}`);
          }
          if (metrics.margin < 0) {
            blockerMessages.push(
              `Negative margin (${metrics.margin.toFixed(1)}%) for ${selectedResourceType?.name}`,
            );
          }

          // Update SOW to blocked status
          await api.put(
            `${API}/api/sales-ops/opportunities/${opportunity.opportunity_id}/sows/${sow.sow_id}`,
            {
              status: 'blocked',
              has_critical_blockers: true,
              blockers: blockerMessages,
            },
          );

          toast.warning('⚠️ Resource added but SOW marked as BLOCKED', {
            description: blockerMessages.join(', '),
          });
        } else if (metrics.margin < 20) {
          // Low margin warning (not a blocker)
          toast.warning(`Resource added with low margin (${metrics.margin.toFixed(1)}%)`);
        } else {
          toast.success('✅ Resource assigned to SOW');
        }

        onSuccess();
      } else {
        const errorData = await response.json();
        toast.error(errorData.detail || 'Failed to assign resource');
      }
    } catch (error) {
      console.error('Error assigning resource:', error);
      toast.error('Failed to assign resource');
    } finally {
      setSubmitting(false);
    }
  };

  const formatCurrency = (amount) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
    }).format(amount || 0);
  };

  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-[60] p-4">
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg w-full max-w-6xl max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="p-6 border-b border-aegisaxiom-border sticky top-0 bg-aegisaxiom-base z-10">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-lg font-bold text-aegisaxiom-text">Add Resource to SOW</h3>
              <p className="text-sm text-aegisaxiom-text-secondary mt-1">
                Configure customer billing (LEFT) and contractor costs (RIGHT) independently
              </p>
            </div>
            <button onClick={onClose} className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        <div className="p-6 space-y-6">
          {/* Resource Type Selection (Full Width) */}
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4">
            <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">Resource Type *</label>
            <select
              value={leftForm.resource_type_id}
              onChange={(e) => {
                const rt = resourceTypes.find((r) => r.resource_type_id === e.target.value);
                setLeftForm((prev) => ({
                  ...prev,
                  resource_type_id: e.target.value,
                  resource_type_name: rt?.name || '',
                  customer_sell_rate: rt?.sell_rate || '',
                }));
                setSelectedTemplateId('');
                setRightForm({
                  contractor_business_id: '',
                  contractor_business_name: '',
                  contractor_user_id: '',
                  contractor_user_name: '',
                  contractor_pay_type: '',
                  contractor_billing_terms: 'weekly',
                  contractor_payment_terms: 'net_7',
                  contractor_hours_estimated: '',
                  contractor_cost_rate: '',
                  contractor_fixed_cost: '',
                  contractor_monthly_cost: '',
                });
              }}
              className="w-full px-4 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
            >
              <option value="">Select resource type...</option>
              {resourceTypes
                .filter((rt) => rt.is_active)
                .map((rt) => (
                  <option key={rt.resource_type_id} value={rt.resource_type_id}>
                    {rt.name} - Default Sell Rate: ${rt.sell_rate || 0}/hr
                  </option>
                ))}
            </select>
          </div>

          {leftForm.resource_type_id && (
            <>
              {/* Template Selection (Full Width) */}
              <div className="bg-violet-900/20 border border-violet-700/50 rounded-lg p-4">
                <label className="block text-sm font-medium text-violet-700 dark:text-violet-300 mb-2 flex items-center gap-2">
                  <FileText className="w-4 h-4" />
                  Contract Template (Optional - Auto-fills LEFT side)
                </label>
                <select
                  value={selectedTemplateId}
                  onChange={(e) => setSelectedTemplateId(e.target.value)}
                  className="w-full px-4 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                >
                  <option value="">No template - Manual entry</option>
                  {templates
                    .filter((t) => t.is_active)
                    .map((t) => (
                      <option key={t.template_id} value={t.template_id}>
                        {t.template_name} -{' '}
                        {CONTRACT_TYPES.find((ct) => ct.value === t.contract_type)?.label}
                      </option>
                    ))}
                </select>
                <p className="text-xs text-violet-700 dark:text-violet-300/60 mt-2">
                  Selecting a template will pre-fill customer billing fields below (you can still
                  edit them)
                </p>
              </div>

              {/* THREE COLUMN LAYOUT: LEFT (Customer) | MIDDLE (Profit) | RIGHT (Contractor) */}
              <div className="grid grid-cols-12 gap-4">
                {/* LEFT COLUMN - Customer Billing (5 cols) */}
                <div className="col-span-5 bg-blue-900/20 border-2 border-blue-700/50 rounded-lg p-5">
                  <h4 className="text-sm font-bold text-blue-700 dark:text-blue-300 mb-4 uppercase tracking-wide flex items-center gap-2">
                    <DollarSign className="w-4 h-4" />
                    Customer Billing (What We Sell)
                  </h4>

                  <div className="space-y-4">
                    {/* Contract Type */}
                    <div>
                      <label className="block text-xs text-aegisaxiom-text-secondary mb-2">Contract Type *</label>
                      <select
                        value={leftForm.customer_contract_type}
                        onChange={(e) =>
                          setLeftForm((prev) => ({
                            ...prev,
                            customer_contract_type: e.target.value,
                            convert_to_boa_at_exhaustion:
                              e.target.value === 'boh' ? prev.convert_to_boa_at_exhaustion : false,
                          }))
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                      >
                        {CONTRACT_TYPES.map((type) => (
                          <option key={type.value} value={type.value}>
                            {type.label}
                          </option>
                        ))}
                      </select>
                    </div>

                    {/* Conditional Fields Based on Contract Type */}
                    {leftForm.customer_contract_type === 'boh' && (
                      <>
                        <div>
                          <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                            Hours Purchased *
                          </label>
                          <input
                            type="number"
                            value={leftForm.customer_hours_purchased}
                            onChange={(e) =>
                              setLeftForm((prev) => ({
                                ...prev,
                                customer_hours_purchased: e.target.value,
                              }))
                            }
                            placeholder="200"
                            step="1"
                            className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                          />
                        </div>
                        <div>
                          <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                            Sell Rate ($/hr) *
                          </label>
                          <input
                            type="number"
                            value={leftForm.customer_sell_rate}
                            onChange={(e) =>
                              setLeftForm((prev) => ({
                                ...prev,
                                customer_sell_rate: e.target.value,
                              }))
                            }
                            placeholder="180"
                            step="0.01"
                            className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                          />
                        </div>
                      </>
                    )}

                    {leftForm.customer_contract_type === 'boa' && (
                      <div>
                        <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                          Sell Rate ($/hr) *
                        </label>
                        <input
                          type="number"
                          value={leftForm.customer_sell_rate}
                          onChange={(e) =>
                            setLeftForm((prev) => ({ ...prev, customer_sell_rate: e.target.value }))
                          }
                          placeholder="180"
                          step="0.01"
                          className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                        />
                      </div>
                    )}

                    {leftForm.customer_contract_type === 'fixed_fee' && (
                      <div>
                        <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                          Fixed Fee Amount (Total) *
                        </label>
                        <input
                          type="number"
                          value={leftForm.customer_fixed_amount}
                          onChange={(e) =>
                            setLeftForm((prev) => ({
                              ...prev,
                              customer_fixed_amount: e.target.value,
                            }))
                          }
                          placeholder="50000"
                          step="0.01"
                          className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                        />
                      </div>
                    )}

                    {leftForm.customer_contract_type === 'monthly' && (
                      <div>
                        <label className="block text-xs text-aegisaxiom-text-secondary mb-2">Monthly Rate *</label>
                        <input
                          type="number"
                          value={leftForm.customer_monthly_rate}
                          onChange={(e) =>
                            setLeftForm((prev) => ({
                              ...prev,
                              customer_monthly_rate: e.target.value,
                            }))
                          }
                          placeholder="15000"
                          step="0.01"
                          className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                        />
                      </div>
                    )}

                    {/* Billing Schedule Configuration - Unified Component (Same as Contract Management) */}
                    <div className="col-span-2">
                      <BillingConfigurationFields
                        contractType={leftForm.customer_contract_type}
                        billingSchedule={leftForm.customer_billing_schedule}
                        hoursPurchased={parseFloat(leftForm.customer_hours_purchased) || 0}
                        sellRate={parseFloat(leftForm.customer_sell_rate) || 0}
                        fixedAmount={parseFloat(leftForm.customer_fixed_amount) || 0}
                        monthlyRate={parseFloat(leftForm.customer_monthly_rate) || 0}
                        onChange={(newSchedule) =>
                          setLeftForm((prev) => ({
                            ...prev,
                            customer_billing_schedule: newSchedule,
                          }))
                        }
                        onFormChange={(field, value) =>
                          setLeftForm((prev) => ({ ...prev, [field]: value }))
                        }
                        formData={{
                          billing_frequency: leftForm.billing_frequency,
                          boa_billing_day: leftForm.boa_billing_day,
                          billing_method: leftForm.billing_method,
                          milestones: leftForm.milestones,
                          estimated_boa_cost: leftForm.estimated_boa_cost,
                        }}
                        serviceCategory="field_services"
                      />
                    </div>

                    {/* Payment Terms */}
                    <div>
                      <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                        Payment Terms (When Customer Pays)
                      </label>
                      <select
                        value={leftForm.customer_payment_terms}
                        onChange={(e) =>
                          setLeftForm((prev) => ({
                            ...prev,
                            customer_payment_terms: e.target.value,
                          }))
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
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

                {/* MIDDLE COLUMN - Profit Metrics (2 cols) */}
                <div className="col-span-2 flex flex-col justify-center">
                  <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4 space-y-3">
                    <div className="text-center">
                      <div className="text-xs text-aegisaxiom-text-secondary mb-1">Revenue</div>
                      <div className="text-lg font-bold text-green-600 dark:text-green-400">
                        {formatCurrency(metrics.revenue)}
                      </div>
                    </div>

                    <div className="border-t border-aegisaxiom-border pt-3 text-center">
                      <div className="text-xs text-aegisaxiom-text-secondary mb-1">Cost</div>
                      <div className="text-lg font-bold text-red-600 dark:text-red-400">
                        {formatCurrency(metrics.cost)}
                      </div>
                    </div>

                    <div className="border-t border-aegisaxiom-border pt-3 text-center">
                      <div className="text-xs text-aegisaxiom-text-secondary mb-1">Profit</div>
                      <div
                        className={`text-lg font-bold ${metrics.profit >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}
                      >
                        {formatCurrency(metrics.profit)}
                      </div>
                    </div>

                    <div className="border-t border-aegisaxiom-border pt-3 text-center">
                      <div className="text-xs text-aegisaxiom-text-secondary mb-1">Margin</div>
                      <div
                        className={`text-2xl font-bold ${metrics.margin >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}
                      >
                        {metrics.margin.toFixed(1)}%
                      </div>
                    </div>
                  </div>
                </div>

                {/* RIGHT COLUMN - Contractor Cost (5 cols) */}
                <div className="col-span-5 bg-purple-900/20 border-2 border-purple-700/50 rounded-lg p-5">
                  <h4 className="text-sm font-bold text-purple-300 mb-4 uppercase tracking-wide flex items-center gap-2">
                    <Users className="w-4 h-4" />
                    Contractor Cost (What We Buy)
                  </h4>

                  {loadingContractors ? (
                    <div className="flex items-center justify-center py-8">
                      <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-purple-400"></div>
                    </div>
                  ) : filteredContractors.length === 0 ? (
                    <div className="text-center py-8">
                      <AlertCircle className="w-12 h-12 text-yellow-600 dark:text-yellow-400 mx-auto mb-3" />
                      <p className="text-aegisaxiom-text-secondary font-medium mb-2">No Contractors Available</p>
                      <p className="text-sm text-aegisaxiom-text-secondary mb-4">
                        No contractors currently offer <strong>{selectedResourceType?.name}</strong>
                      </p>
                      <button
                        onClick={async () => {
                          try {
                            const response = await api.post(
                              `${API}/api/sales-ops/procurement-notifications`,
                              {
                                notification_type: 'missing_resource_type',
                                resource_type_name: selectedResourceType?.name,
                                opportunity_id: opportunity.opportunity_id,
                                opportunity_name: opportunity.name,
                                message: `Sales Ops needs "${selectedResourceType?.name}" for opportunity "${opportunity.name}"`,
                              },
                            );

                            if (response.ok) {
                              toast.success(
                                'Procurement notified - they will approve contractors for this resource type',
                              );
                            } else {
                              toast.error('Failed to notify procurement');
                            }
                          } catch (error) {
                            console.error('Error notifying procurement:', error);
                            toast.error('Failed to notify procurement');
                          }
                        }}
                        className="px-6 py-2 bg-yellow-600 hover:bg-yellow-500 text-white rounded-lg transition flex items-center gap-2 mx-auto"
                      >
                        <AlertCircle className="w-4 h-4" />
                        Notify Procurement
                      </button>
                      <p className="text-xs text-aegisaxiom-text-muted mt-3">
                        Procurement will be notified to approve contractors for this resource type
                      </p>
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {/* Contractor Business */}
                      <div>
                        <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                          Contractor Business *
                        </label>
                        <select
                          value={rightForm.contractor_business_id}
                          onChange={(e) => {
                            const biz = filteredContractors.find(
                              (b) => b.business_id === e.target.value,
                            );
                            setRightForm((prev) => ({
                              ...prev,
                              contractor_business_id: e.target.value,
                              contractor_business_name: biz?.business_name || '',
                              contractor_user_id: '',
                              contractor_user_name: '',
                              contractor_pay_type: '',
                            }));
                          }}
                          className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                        >
                          <option value="">Select contractor business...</option>
                          {filteredContractors.map((biz) => (
                            <option key={biz.business_id} value={biz.business_id}>
                              {biz.business_name}
                            </option>
                          ))}
                        </select>
                        <p className="text-xs text-purple-300/60 mt-1">
                          Showing {filteredContractors.length} businesses offering{' '}
                          {selectedResourceType?.name}
                        </p>
                      </div>

                      {/* Contractor Person */}
                      {rightForm.contractor_business_id && (
                        <div>
                          <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                            Contractor Person *
                          </label>
                          <select
                            value={rightForm.contractor_user_id}
                            onChange={(e) => {
                              const user = availableContractorUsers.find(
                                (u) => u.user_id === e.target.value,
                              );
                              setRightForm((prev) => ({
                                ...prev,
                                contractor_user_id: e.target.value,
                                contractor_user_name: user?.name || '',
                              }));
                            }}
                            className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                          >
                            <option value="">Select contractor person...</option>
                            {contractorUsersForBusiness.map((user) => (
                              <option key={user.user_id} value={user.user_id}>
                                {user.name}
                              </option>
                            ))}
                          </select>
                        </div>
                      )}

                      {/* Contractor Pay Type */}
                      {rightForm.contractor_user_id && availablePayTypes.length > 0 && (
                        <div>
                          <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                            Contractor Pay Type *
                          </label>
                          <select
                            value={rightForm.contractor_pay_type}
                            onChange={(e) =>
                              setRightForm((prev) => ({
                                ...prev,
                                contractor_pay_type: e.target.value,
                              }))
                            }
                            className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                          >
                            <option value="">Select pay type...</option>
                            {availablePayTypes.map((payType) => (
                              <option key={payType} value={payType}>
                                {payType.replace('_', ' ').toUpperCase()}
                              </option>
                            ))}
                          </select>
                        </div>
                      )}

                      {/* Contractor Pricing Fields Based on Pay Type */}
                      {rightForm.contractor_pay_type && (
                        <>
                          {(rightForm.contractor_pay_type === 'block_hours' ||
                            rightForm.contractor_pay_type === 'bill_actuals') && (
                            <>
                              <div>
                                <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                                  Estimated Hours
                                </label>
                                <input
                                  type="number"
                                  value={rightForm.contractor_hours_estimated}
                                  onChange={(e) =>
                                    setRightForm((prev) => ({
                                      ...prev,
                                      contractor_hours_estimated: e.target.value,
                                    }))
                                  }
                                  placeholder={leftForm.customer_hours_purchased || '200'}
                                  step="1"
                                  className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                                />
                                <p className="text-xs text-purple-300/60 mt-1">
                                  Leave blank to use customer hours
                                </p>
                              </div>
                              <div>
                                <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                                  Cost Rate ($/hr) *
                                </label>
                                <input
                                  type="number"
                                  value={rightForm.contractor_cost_rate}
                                  onChange={(e) =>
                                    setRightForm((prev) => ({
                                      ...prev,
                                      contractor_cost_rate: e.target.value,
                                    }))
                                  }
                                  placeholder="150"
                                  step="0.01"
                                  className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                                />
                              </div>
                            </>
                          )}

                          {rightForm.contractor_pay_type === 'fixed_price' && (
                            <div>
                              <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                                Fixed Cost *
                              </label>
                              <input
                                type="number"
                                value={rightForm.contractor_fixed_cost}
                                onChange={(e) =>
                                  setRightForm((prev) => ({
                                    ...prev,
                                    contractor_fixed_cost: e.target.value,
                                  }))
                                }
                                placeholder="20000"
                                step="0.01"
                                className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                              />
                            </div>
                          )}

                          {rightForm.contractor_pay_type === 'monthly' && (
                            <div>
                              <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                                Monthly Cost *
                              </label>
                              <input
                                type="number"
                                value={rightForm.contractor_monthly_cost}
                                onChange={(e) =>
                                  setRightForm((prev) => ({
                                    ...prev,
                                    contractor_monthly_cost: e.target.value,
                                  }))
                                }
                                placeholder="4000"
                                step="0.01"
                                className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                              />
                            </div>
                          )}

                          {/* Billing Terms */}
                          <div>
                            <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                              Billing Terms
                            </label>
                            <select
                              value={rightForm.contractor_billing_terms}
                              onChange={(e) =>
                                setRightForm((prev) => ({
                                  ...prev,
                                  contractor_billing_terms: e.target.value,
                                }))
                              }
                              className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                            >
                              {rightForm.contractor_pay_type === 'fixed_price' ? (
                                <>
                                  {FIXED_PRICE_BILLING_TERMS.map((term) => (
                                    <option key={term.value} value={term.value}>
                                      {term.label}
                                    </option>
                                  ))}
                                </>
                              ) : (
                                <>
                                  {RECURRING_FREQUENCIES.map((term) => (
                                    <option key={term.value} value={term.value}>
                                      {term.label}
                                    </option>
                                  ))}
                                </>
                              )}
                            </select>
                          </div>

                          {/* Payment Terms */}
                          <div>
                            <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                              Payment Terms
                            </label>
                            <select
                              value={rightForm.contractor_payment_terms}
                              onChange={(e) =>
                                setRightForm((prev) => ({
                                  ...prev,
                                  contractor_payment_terms: e.target.value,
                                }))
                              }
                              className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                            >
                              {PAYMENT_TERMS.map((term) => (
                                <option key={term.value} value={term.value}>
                                  {term.label}
                                </option>
                              ))}
                            </select>
                          </div>
                        </>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {/* Notes (Full Width) */}
              <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4">
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">Notes</label>
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  rows={2}
                  placeholder="Additional notes..."
                  className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm resize-none"
                />
              </div>
            </>
          )}
        </div>

        {/* Footer - Conditional buttons based on contractor availability */}
        <div className="p-6 border-t border-aegisaxiom-border sticky bottom-0 bg-aegisaxiom-base flex justify-between items-center gap-3">
          <button
            onClick={onClose}
            className="px-6 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg transition"
          >
            Cancel
          </button>

          <div className="flex gap-3">
            {/* Show "Add as Blocked" button if no contractors available */}
            {filteredContractors.length === 0 && leftForm.resource_type_id && (
              <button
                onClick={() => handleSubmit(true)}
                disabled={submitting}
                className="px-6 py-2 bg-red-600 hover:bg-red-500 text-white rounded-lg transition disabled:opacity-50 flex items-center gap-2"
                title="Add resource without contractor - marks as BLOCKED"
              >
                <AlertCircle className="w-4 h-4" />
                {submitting ? 'Adding...' : 'Add as Blocked (No Contractor)'}
              </button>
            )}

            {/* Standard Add button - only show if contractor selected */}
            {rightForm.contractor_user_id && (
              <button
                onClick={() => handleSubmit(false)}
                disabled={submitting}
                className="px-6 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg transition disabled:opacity-50"
              >
                {submitting ? 'Adding...' : 'Add Resource'}
              </button>
            )}

            {/* Disabled state if resource type not selected */}
            {!leftForm.resource_type_id && (
              <button
                disabled
                className="px-6 py-2 bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-muted rounded-lg cursor-not-allowed"
              >
                Add Resource
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
