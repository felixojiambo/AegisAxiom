import React from 'react';
import { api } from '../../../lib/api';
import { toast } from 'sonner';
import { DollarSign, X, Plus } from 'lucide-react';
import API_URL from '../../../lib/apiConfig';

const API = API_URL;
const fmt = (n) =>
  `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function CommissionPlanModal({
  commissionDeal,
  commissionForm,
  setCommissionForm,
  commissionTemplates,
  users,
  onClose,
  onSave,
  loading,
  setLoading,
}) {
  if (!commissionDeal) return null;

  // Calculate commission in real-time
  const revenue = parseFloat(commissionForm.revenue) || 0;
  const costValue = parseFloat(commissionForm.cost_value) || 0;
  const actualCost =
    commissionForm.cost_type === 'percentage' ? revenue * (costValue / 100) : costValue;

  // Calculate each tier
  const tier1 = commissionForm.tiers[0] || {};
  const tier2 = commissionForm.tiers[1] || {};
  const tier3 = commissionForm.tiers[2] || {};

  const tier1Commission = revenue * ((tier1.percentage || 0) / 100);
  const tier2Base = revenue - actualCost - tier1Commission;
  const tier2Commission = tier2Base * ((tier2.percentage || 0) / 100);
  const tier3Base = tier2Base - tier2Commission;
  const tier3Commission = tier3Base * ((tier3.percentage || 0) / 100);

  const totalCommission = tier1Commission + tier2Commission + tier3Commission;
  const profitAfterCommission = revenue - actualCost - totalCommission;
  const profitPercentage = revenue > 0 ? (profitAfterCommission / revenue) * 100 : 0;

  const loadTemplate = (template) => {
    // Resolve default users for each tier
    const resolveDefaultUser = (defaultUser, tierIndex) => {
      if (!defaultUser) return [];

      // Handle dynamic variables
      if (defaultUser === '{deal_owner}') {
        const userId = commissionDeal.deal_owner_id;
        const userName = commissionDeal.deal_owner_name;
        if (userId) {
          return [
            {
              user_id: userId,
              user_name: userName || '',
              split_percentage: 100,
              commission_amount: 0,
            },
          ];
        }
      } else if (defaultUser === '{agent_of_record}') {
        const userId = commissionDeal.assigned_agent_id;
        const user = users.find((u) => u.user_id === userId);
        if (userId) {
          return [
            {
              user_id: userId,
              user_name: user?.name || '',
              split_percentage: 100,
              commission_amount: 0,
            },
          ];
        }
      } else {
        // It's a specific user_id
        const user = users.find((u) => u.user_id === defaultUser);
        if (user) {
          return [
            {
              user_id: user.user_id,
              user_name: user.name,
              split_percentage: 100,
              commission_amount: 0,
            },
          ];
        }
      }

      return [];
    };

    setCommissionForm((f) => ({
      ...f,
      template_id: template.template_id,
      cost_type: template.cost_type,
      cost_value: template.cost_default,
      tiers: [
        {
          ...f.tiers[0],
          percentage: template.tier1_percentage,
          users: resolveDefaultUser(template.tier1_default_user, 0),
        },
        {
          ...f.tiers[1],
          percentage: template.tier2_percentage,
          users: resolveDefaultUser(template.tier2_default_user, 1),
        },
        {
          ...f.tiers[2],
          percentage: template.tier3_percentage,
          users: resolveDefaultUser(template.tier3_default_user, 2),
        },
      ],
    }));
  };

  const addUserToTier = (tierIndex) => {
    const updated = [...commissionForm.tiers];
    updated[tierIndex].users = [
      ...(updated[tierIndex].users || []),
      { user_id: '', user_name: '', split_percentage: 100, commission_amount: 0 },
    ];
    setCommissionForm((f) => ({ ...f, tiers: updated }));
  };

  const removeUserFromTier = (tierIndex, userIndex) => {
    const updated = [...commissionForm.tiers];
    updated[tierIndex].users = updated[tierIndex].users.filter((_, i) => i !== userIndex);
    setCommissionForm((f) => ({ ...f, tiers: updated }));
  };

  const updateTierUser = (tierIndex, userIndex, field, value) => {
    const updated = [...commissionForm.tiers];
    updated[tierIndex].users[userIndex][field] = value;
    // Update user name if user_id changes
    if (field === 'user_id') {
      const user = users.find((u) => u.user_id === value);
      updated[tierIndex].users[userIndex].user_name = user ? user.name : '';
    }
    setCommissionForm((f) => ({ ...f, tiers: updated }));
  };

  const saveCommissionPlan = async () => {
    // Validation
    if (!commissionForm.revenue || parseFloat(commissionForm.revenue) <= 0) {
      toast.error('Please enter deal revenue');
      return;
    }

    // Validate at least one user in at least one tier
    const hasUsers = commissionForm.tiers.some(
      (t) => t.users && t.users.length > 0 && t.users.some((u) => u.user_id),
    );
    if (!hasUsers) {
      toast.error('Please add at least one user to receive commission');
      return;
    }

    try {
      setLoading(true);
      const payload = {
        deal_id: commissionForm.deal_id,
        template_id: commissionForm.template_id || null,
        revenue: parseFloat(commissionForm.revenue),
        cost_type: commissionForm.cost_type,
        cost_value: parseFloat(commissionForm.cost_value) || 0,
        tiers: commissionForm.tiers.map((tier) => ({
          tier_number: tier.tier_number,
          tier_name: tier.tier_name,
          percentage: tier.percentage,
          users: tier.users
            .filter((u) => u.user_id)
            .map((u) => ({
              user_id: u.user_id,
              user_name: u.user_name,
              split_percentage: parseFloat(u.split_percentage) || 0,
              commission_amount: 0, // Will be calculated by backend
            })),
        })),
      };

      const res = await api.post(`${API}/api/commission-management/plans`, payload);
      if (!res.ok) {
        const err = await res.json();
        // Handle Pydantic validation errors (array) vs simple string errors
        let errorMsg = 'Failed to save commission plan';
        if (err.detail) {
          if (Array.isArray(err.detail)) {
            // Pydantic validation errors - extract messages
            errorMsg = err.detail.map((e) => e.msg || e.message || JSON.stringify(e)).join(', ');
          } else if (typeof err.detail === 'string') {
            errorMsg = err.detail;
          } else {
            errorMsg = JSON.stringify(err.detail);
          }
        }
        throw new Error(errorMsg);
      }

      const data = await res.json();
      toast.success('Commission plan committed!');
      onSave(data);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 overflow-y-auto">
      <div className="absolute inset-0 bg-black/60" />
      <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-4xl my-8">
        {/* Header */}
        <div className="bg-aegisaxiom-surface backdrop-blur border-b border-aegisaxiom-border p-4">
          <div className="flex justify-between items-start">
            <div>
              <h2 className="text-sm font-semibold text-aegisaxiom-text flex items-center gap-2">
                <DollarSign size={16} className="text-violet-600 dark:text-violet-400" /> Commit Commission Plan
              </h2>
              <p className="text-xs text-aegisaxiom-text-secondary mt-1">
                <span className="text-aegisaxiom-text font-medium">
                  {commissionDeal.name || commissionDeal.deal_name}
                </span>
              </p>
            </div>
            <button onClick={onClose} className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text">
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="p-6 space-y-6 max-h-[calc(100vh-200px)] overflow-y-auto">
          {/* Template Selection */}
          <div className="bg-violet-900/10 border border-violet-800/30 rounded-lg p-4">
            <label className="text-xs text-violet-700 dark:text-violet-300 font-medium block mb-2">
              Load Commission Template (Optional)
            </label>
            <div className="flex gap-2">
              <select
                value={commissionForm.template_id}
                onChange={(e) => {
                  const templateId = e.target.value;
                  if (templateId) {
                    const template = commissionTemplates.find((t) => t.template_id === templateId);
                    if (template) loadTemplate(template);
                  } else {
                    // Clear template - allow custom percentages
                    setCommissionForm((f) => ({ ...f, template_id: '' }));
                  }
                }}
                className="flex-1 px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none focus:border-violet-500"
              >
                <option value="">Custom (No Template)</option>
                {commissionTemplates.map((t) => (
                  <option key={t.template_id} value={t.template_id}>
                    {t.name} — T1: {t.tier1_percentage}%, T2: {t.tier2_percentage}%, T3:{' '}
                    {t.tier3_percentage}%
                  </option>
                ))}
              </select>
            </div>
            <p className="text-xs text-aegisaxiom-text-muted mt-2">
              {commissionForm.template_id
                ? '🔒 Template selected: Tier percentages are locked. Select "Custom" to edit percentages.'
                : 'Templates auto-fill percentages and cost defaults. You can still customize user assignments and splits.'}
            </p>
          </div>

          {/* Revenue & Cost */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Deal Revenue ($) *</label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={commissionForm.revenue}
                onChange={(e) => setCommissionForm((f) => ({ ...f, revenue: e.target.value }))}
                placeholder="e.g., 100000"
                className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                required
              />
            </div>
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary block mb-2">Cost Type & Value *</label>
              <div className="flex gap-2">
                <select
                  value={commissionForm.cost_type}
                  onChange={(e) => setCommissionForm((f) => ({ ...f, cost_type: e.target.value }))}
                  className="px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none"
                >
                  <option value="percentage">% of Revenue</option>
                  <option value="actual">Actual $</option>
                </select>
                <input
                  type="number"
                  step={commissionForm.cost_type === 'percentage' ? '0.1' : '0.01'}
                  min="0"
                  value={commissionForm.cost_value}
                  onChange={(e) => setCommissionForm((f) => ({ ...f, cost_value: e.target.value }))}
                  placeholder={commissionForm.cost_type === 'percentage' ? '50' : '50000'}
                  className="flex-1 px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text outline-none focus:border-violet-500"
                  required
                />
              </div>
              <p className="text-xs text-aegisaxiom-text-muted mt-1">Actual Cost: {fmt(actualCost)}</p>
            </div>
          </div>

          {/* Three Tiers */}
          {commissionForm.tiers.map((tier, tierIndex) => {
            const tierCommission =
              tierIndex === 0
                ? tier1Commission
                : tierIndex === 1
                  ? tier2Commission
                  : tier3Commission;
            const tierBase = tierIndex === 0 ? revenue : tierIndex === 1 ? tier2Base : tier3Base;

            return (
              <div
                key={tier.tier_number}
                className="bg-aegisaxiom-accent-muted border border-aegisaxiom-border rounded-lg p-4 space-y-3"
              >
                <div className="flex items-center justify-between">
                  <div>
                    <h4 className="text-xs font-semibold text-aegisaxiom-text">
                      Tier {tier.tier_number}: {tier.tier_name}
                    </h4>
                    <p className="text-xs text-aegisaxiom-text-muted">
                      Base: {fmt(tierBase)} → Commission: {fmt(tierCommission)}
                    </p>
                  </div>
                  <div className="flex items-center gap-1">
                    <input
                      type="number"
                      step="0.1"
                      min="0"
                      max="100"
                      value={tier.percentage}
                      onChange={(e) => {
                        const updated = [...commissionForm.tiers];
                        updated[tierIndex].percentage = parseFloat(e.target.value) || 0;
                        setCommissionForm((f) => ({ ...f, tiers: updated }));
                      }}
                      disabled={!!commissionForm.template_id}
                      className={`w-20 px-2 py-1 border rounded text-xs text-aegisaxiom-text text-right outline-none ${
                        commissionForm.template_id
                          ? 'bg-aegisaxiom-elevated border-aegisaxiom-border-hover cursor-not-allowed opacity-70'
                          : 'bg-aegisaxiom-surface border-aegisaxiom-border focus:border-violet-500'
                      }`}
                    />
                    <span className="text-xs text-aegisaxiom-text-secondary">%</span>
                    {commissionForm.template_id && (
                      <span className="text-[10px] text-amber-600 dark:text-amber-400 ml-1" title="Locked by template">
                        🔒
                      </span>
                    )}
                  </div>
                </div>

                {/* Users in this tier */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <label className="text-xs text-aegisaxiom-text-secondary">Commission Recipients</label>
                    <button
                      type="button"
                      onClick={() => addUserToTier(tierIndex)}
                      className="text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 flex items-center gap-1"
                    >
                      <Plus size={12} /> Add User
                    </button>
                  </div>

                  {(!tier.users || tier.users.length === 0) && (
                    <p className="text-xs text-aegisaxiom-text-muted text-center py-2">
                      No users. Click "Add User" to assign recipients.
                    </p>
                  )}

                  {tier.users &&
                    tier.users.map((user, userIndex) => {
                      const totalSplit = tier.users.reduce(
                        (sum, u) => sum + (parseFloat(u.split_percentage) || 0),
                        0,
                      );
                      const userCommission =
                        totalSplit > 0
                          ? tierCommission * ((parseFloat(user.split_percentage) || 0) / totalSplit)
                          : 0;

                      return (
                        <div
                          key={userIndex}
                          className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded p-2 space-y-2"
                        >
                          <div className="flex items-center justify-between">
                            <span className="text-xs text-aegisaxiom-text-secondary">
                              Recipient #{userIndex + 1}
                            </span>
                            <button
                              type="button"
                              onClick={() => removeUserFromTier(tierIndex, userIndex)}
                              className="text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300"
                            >
                              <X size={12} />
                            </button>
                          </div>
                          <select
                            value={user.user_id}
                            onChange={(e) =>
                              updateTierUser(tierIndex, userIndex, 'user_id', e.target.value)
                            }
                            className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none"
                          >
                            <option value="">Select user...</option>
                            {users.map((u) => (
                              <option key={u.user_id} value={u.user_id}>
                                {u.name} ({u.role})
                              </option>
                            ))}
                          </select>
                          <div className="flex items-center gap-2">
                            <input
                              type="number"
                              step="0.1"
                              min="0"
                              max="100"
                              value={user.split_percentage}
                              onChange={(e) =>
                                updateTierUser(
                                  tierIndex,
                                  userIndex,
                                  'split_percentage',
                                  e.target.value,
                                )
                              }
                              placeholder="Split %"
                              className="flex-1 px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none"
                            />
                            <span className="text-xs text-aegisaxiom-text-muted">
                              % = {fmt(userCommission)}
                            </span>
                          </div>
                        </div>
                      );
                    })}
                </div>
              </div>
            );
          })}

          {/* Live Calculation Summary */}
          <div className="bg-emerald-50 dark:bg-emerald-900/10 border border-emerald-200 dark:border-emerald-800/30 rounded-lg p-4">
            <h4 className="text-xs font-semibold text-emerald-700 dark:text-emerald-300 mb-3">
              Live Commission Calculation
            </h4>
            <div className="space-y-3 text-xs">
              {/* Revenue & Cost */}
              <div className="space-y-1">
                <div className="flex justify-between">
                  <span className="text-aegisaxiom-text-secondary">Revenue:</span>
                  <span className="text-aegisaxiom-text font-medium">{fmt(revenue)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-aegisaxiom-text-secondary">Cost:</span>
                  <span className="text-aegisaxiom-text font-medium">-{fmt(actualCost)}</span>
                </div>
              </div>

              {/* Tier 1 Breakdown */}
              <div className="border-t border-aegisaxiom-border pt-2">
                <div className="flex justify-between mb-1">
                  <span className="text-blue-700 dark:text-blue-300 font-medium">Tier 1: Top Line</span>
                  <span className="text-blue-600 dark:text-blue-400 font-medium">-{fmt(tier1Commission)}</span>
                </div>
                {commissionForm.tiers[0]?.users?.filter((u) => u.user_id).length > 0 ? (
                  <div className="ml-3 space-y-0.5 text-[11px]">
                    {commissionForm.tiers[0].users
                      .filter((u) => u.user_id)
                      .map((user, idx) => {
                        const totalSplit = commissionForm.tiers[0].users.reduce(
                          (sum, u) => sum + (parseFloat(u.split_percentage) || 0),
                          0,
                        );
                        const userAmount =
                          totalSplit > 0
                            ? tier1Commission *
                              ((parseFloat(user.split_percentage) || 0) / totalSplit)
                            : 0;
                        return (
                          <div key={idx} className="flex justify-between text-aegisaxiom-text-secondary">
                            <span>
                              •{' '}
                              {user.user_name ||
                                users.find((u) => u.user_id === user.user_id)?.name ||
                                'Unknown'}{' '}
                              ({user.split_percentage}%)
                            </span>
                            <span className="text-blue-700 dark:text-blue-300">{fmt(userAmount)}</span>
                          </div>
                        );
                      })}
                  </div>
                ) : (
                  <div className="ml-3 text-[11px] text-aegisaxiom-text-muted italic">No users assigned</div>
                )}
              </div>

              {/* Tier 2 Breakdown */}
              <div className="border-t border-aegisaxiom-border pt-2">
                <div className="flex justify-between mb-1">
                  <span className="text-amber-700 dark:text-amber-300 font-medium">Tier 2: Director Override</span>
                  <span className="text-amber-600 dark:text-amber-400 font-medium">-{fmt(tier2Commission)}</span>
                </div>
                {commissionForm.tiers[1]?.users?.filter((u) => u.user_id).length > 0 ? (
                  <div className="ml-3 space-y-0.5 text-[11px]">
                    {commissionForm.tiers[1].users
                      .filter((u) => u.user_id)
                      .map((user, idx) => {
                        const totalSplit = commissionForm.tiers[1].users.reduce(
                          (sum, u) => sum + (parseFloat(u.split_percentage) || 0),
                          0,
                        );
                        const userAmount =
                          totalSplit > 0
                            ? tier2Commission *
                              ((parseFloat(user.split_percentage) || 0) / totalSplit)
                            : 0;
                        return (
                          <div key={idx} className="flex justify-between text-aegisaxiom-text-secondary">
                            <span>
                              •{' '}
                              {user.user_name ||
                                users.find((u) => u.user_id === user.user_id)?.name ||
                                'Unknown'}{' '}
                              ({user.split_percentage}%)
                            </span>
                            <span className="text-amber-700 dark:text-amber-300">{fmt(userAmount)}</span>
                          </div>
                        );
                      })}
                  </div>
                ) : (
                  <div className="ml-3 text-[11px] text-aegisaxiom-text-muted italic">No users assigned</div>
                )}
              </div>

              {/* Tier 3 Breakdown */}
              <div className="border-t border-aegisaxiom-border pt-2">
                <div className="flex justify-between mb-1">
                  <span className="text-purple-300 font-medium">Tier 3: Sales Rep</span>
                  <span className="text-purple-400 font-medium">-{fmt(tier3Commission)}</span>
                </div>
                {commissionForm.tiers[2]?.users?.filter((u) => u.user_id).length > 0 ? (
                  <div className="ml-3 space-y-0.5 text-[11px]">
                    {commissionForm.tiers[2].users
                      .filter((u) => u.user_id)
                      .map((user, idx) => {
                        const totalSplit = commissionForm.tiers[2].users.reduce(
                          (sum, u) => sum + (parseFloat(u.split_percentage) || 0),
                          0,
                        );
                        const userAmount =
                          totalSplit > 0
                            ? tier3Commission *
                              ((parseFloat(user.split_percentage) || 0) / totalSplit)
                            : 0;
                        return (
                          <div key={idx} className="flex justify-between text-aegisaxiom-text-secondary">
                            <span>
                              •{' '}
                              {user.user_name ||
                                users.find((u) => u.user_id === user.user_id)?.name ||
                                'Unknown'}{' '}
                              ({user.split_percentage}%)
                            </span>
                            <span className="text-purple-300">{fmt(userAmount)}</span>
                          </div>
                        );
                      })}
                  </div>
                ) : (
                  <div className="ml-3 text-[11px] text-aegisaxiom-text-muted italic">No users assigned</div>
                )}
              </div>

              {/* Totals */}
              <div className="border-t border-aegisaxiom-border pt-2 space-y-1">
                <div className="flex justify-between">
                  <span className="text-aegisaxiom-text-secondary font-medium">Total Commission:</span>
                  <span className="text-red-600 dark:text-red-400 font-medium">-{fmt(totalCommission)}</span>
                </div>
              </div>

              {/* Final Profit */}
              <div className="border-t border-emerald-200 dark:border-emerald-700/50 pt-2 space-y-1">
                <div className="flex justify-between">
                  <span className="text-emerald-700 dark:text-emerald-300 font-medium">Profit After Commission:</span>
                  <span className="text-emerald-700 dark:text-emerald-300 font-bold">{fmt(profitAfterCommission)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-emerald-700 dark:text-emerald-300 font-medium">Profit Margin:</span>
                  <span className="text-emerald-700 dark:text-emerald-300 font-bold">{profitPercentage.toFixed(2)}%</span>
                </div>
              </div>
            </div>
          </div>

          {/* Notes */}
          <div>
            <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Notes (optional)</label>
            <textarea
              value={commissionForm.notes}
              onChange={(e) => setCommissionForm((f) => ({ ...f, notes: e.target.value }))}
              placeholder="Additional commission details..."
              rows={2}
              className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text focus:border-violet-500 outline-none resize-none"
            />
          </div>

          {/* Action Buttons */}
          <div className="flex gap-2 pt-2">
            <button
              onClick={saveCommissionPlan}
              disabled={loading}
              className="flex-1 bg-violet-600 hover:bg-violet-500 text-white text-sm py-2.5 rounded-lg transition disabled:opacity-50 font-medium"
            >
              {loading ? 'Saving...' : 'Commit Commission Plan'}
            </button>
            <button
              onClick={onClose}
              className="px-6 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
