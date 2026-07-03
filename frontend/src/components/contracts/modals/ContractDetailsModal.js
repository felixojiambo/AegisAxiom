import React, { useState, useEffect } from 'react';
import { X, FileText, Calendar, DollarSign, Users, Clock, Package, Briefcase } from 'lucide-react';
import { api } from '../../../lib/api';
import API_URL from '../../../lib/apiConfig';
import ContractApprovalPanel from '../sections/ContractApprovalPanel';
import ContractPerContractApproversPanel from '../sections/ContractPerContractApproversPanel';
import ContractAdjustmentsPanel from '../sections/ContractAdjustmentsPanel';
import ContractBillingTriggersPanel from '../sections/ContractBillingTriggersPanel';
import ContractProjectsPanel from '../sections/ContractProjectsPanel';

const API = API_URL;
const fmt = (n) =>
  `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const TYPE_LABELS = {
  boh: 'Block of Hours',
  boa: 'Bill on Actuals',
  fixed_fee: 'Fixed Fee',
  monthly: 'Monthly',
  wholesale_mrr: 'Wholesale MRR',
  simplisecure: 'SimpliSecure',
  simplicomply: 'SimpliComply',
  other: 'Other',
};

const STATUS_LABELS = {
  active: 'Active',
  on_hold: 'On Hold',
  completed: 'Completed',
  expired: 'Expired',
  cancelled: 'Cancelled',
};

export default function ContractDetailsModal({ contractId, onClose, onEdit }) {
  const [contract, setContract] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadContract();
  }, [contractId]);

  const loadContract = async () => {
    setLoading(true);
    try {
      const res = await api.get(`${API}/api/contracts/${contractId}`);
      if (res.ok) {
        const data = await res.json();
        setContract(data);
      }
    } catch (err) {
      console.error('Failed to load contract:', err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50">
        <div className="bg-aegisaxiom-surface rounded-lg p-8 w-full max-w-4xl max-h-[90vh] overflow-y-auto">
          <div className="text-center text-aegisaxiom-text-secondary">Loading contract details...</div>
        </div>
      </div>
    );
  }

  if (!contract) {
    return null;
  }

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-aegisaxiom-surface rounded-lg w-full max-w-5xl max-h-[90vh] overflow-y-auto border border-aegisaxiom-border">
        {/* Header */}
        <div className="sticky top-0 bg-aegisaxiom-surface border-b border-aegisaxiom-border p-6 flex items-center justify-between z-10">
          <div className="flex items-center gap-3">
            <FileText className="text-amber-600 dark:text-amber-400" size={24} />
            <div>
              <h2 className="text-xl font-bold text-aegisaxiom-text">
                {contract.name || 'Contract Details'}
              </h2>
              <div className="flex items-center gap-3 mt-1">
                <span
                  className={`px-2 py-0.5 rounded text-xs font-medium ${
                    contract.status === 'active'
                      ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300'
                      : contract.status === 'on_hold'
                        ? 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300'
                        : contract.status === 'completed'
                          ? 'bg-aegisaxiom-elevated/20 text-aegisaxiom-text-secondary'
                          : 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300'
                  }`}
                >
                  {STATUS_LABELS[contract.status] || contract.status}
                </span>
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300">
                  {TYPE_LABELS[contract.contract_type] || contract.contract_type}
                </span>
              </div>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-aegisaxiom-elevated rounded-lg transition text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
          >
            <X size={20} />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6">
          {/* Approval workflow — submit / approve / reject /
              request-changes against the contracts approval routes.
              Permission and status gating live inside the panel so
              we render it unconditionally and let it decide. */}
          <ContractApprovalPanel
            contract={contract}
            onContractUpdated={(updated) => {
              if (updated && updated.contract_id) {
                // The route returns the full refreshed contract; mirror
                // it into local state so the badge + buttons reflect
                // the new approval_status without a second round-trip.
                setContract({ ...contract, ...updated });
              } else {
                loadContract();
              }
            }}
          />

          {/* Per-contract approver overrides (Wave 6) — distinct
              from the global /admin/contract-approvers page. Mounts
              just below the approval workflow panel so admins can
              see who else can decide on the contract they are
              looking at. The panel hides itself for users who can
              neither manage nor observe overrides. */}
          <ContractPerContractApproversPanel contract={contract} />

          {/* Adjustments — credit/debit corrections outside the
              time-entry approval pathway. The POST also runs the
              billing-trigger evaluator on the contract, so we ask
              the modal to re-load the contract after each create
              to reflect refreshed hours_consumed / financial_commit
              / overage_hours on the panels below. */}
          <ContractAdjustmentsPanel
            contract={contract}
            onContractUpdated={() => loadContract()}
          />

          {/* Billing triggers — automated rules that fire actions
              when contract state crosses a threshold. Manual evaluate
              may flip a trigger to "fired" or queue an invoice batch
              for the bi-weekly cadence, so we re-load the contract
              after evaluate as well. */}
          <ContractBillingTriggersPanel
            contract={contract}
            onContractUpdated={() => loadContract()}
          />

          {/* Linked projects — link/unlink existing projects to this
              contract via the Wave-1 attach/detach routes. Each
              mutation returns the full refreshed contract; mirror it
              into local state so the project_ids list updates in
              place without a second round-trip. */}
          <ContractProjectsPanel
            contract={contract}
            onContractUpdated={(updated) => {
              if (updated && updated.contract_id) {
                setContract({ ...contract, ...updated });
              } else {
                loadContract();
              }
            }}
          />

          {/* Basic Information */}
          <div className="glass-card p-4">
            <h3 className="text-sm font-semibold text-aegisaxiom-text mb-4 flex items-center gap-2">
              <Briefcase size={16} className="text-amber-600 dark:text-amber-400" />
              Basic Information
            </h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Contract ID</label>
                <p className="text-sm text-aegisaxiom-text font-mono">{contract.contract_id}</p>
              </div>
              <div>
                <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Account</label>
                <p className="text-sm text-aegisaxiom-text">{contract.account_name || 'N/A'}</p>
              </div>
              <div>
                <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Line of Business</label>
                <p className="text-sm text-aegisaxiom-text">{contract.line_of_business || 'N/A'}</p>
              </div>
              <div>
                <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Start Date</label>
                <p className="text-sm text-aegisaxiom-text">{contract.start_date || 'N/A'}</p>
              </div>
              <div>
                <label className="block text-xs text-aegisaxiom-text-secondary mb-1">End Date</label>
                <p className="text-sm text-aegisaxiom-text">{contract.end_date || 'Ongoing'}</p>
              </div>
            </div>
          </div>

          {/* Financial Details */}
          {(contract.contract_type === 'boh' ||
            contract.contract_type === 'boa' ||
            contract.contract_type === 'fixed_fee') && (
            <div className="glass-card p-4">
              <h3 className="text-sm font-semibold text-aegisaxiom-text mb-4 flex items-center gap-2">
                <DollarSign size={16} className="text-emerald-600 dark:text-emerald-400" />
                Financial Details
              </h3>
              <div className="grid grid-cols-3 gap-4">
                {contract.contract_type === 'boh' && (
                  <>
                    <div>
                      <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Total Hours</label>
                      <p className="text-sm text-aegisaxiom-text font-semibold">
                        {contract.total_hours || 0}h
                      </p>
                    </div>
                    <div>
                      <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Hours Consumed</label>
                      <p className="text-sm text-aegisaxiom-text font-semibold">
                        {contract.hours_consumed || 0}h
                      </p>
                    </div>
                    <div>
                      <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Hours Remaining</label>
                      <p className="text-sm text-emerald-600 dark:text-emerald-400 font-semibold">
                        {(contract.total_hours || 0) - (contract.hours_consumed || 0)}h
                      </p>
                    </div>
                  </>
                )}
                {contract.contract_type === 'boa' && (
                  <>
                    <div>
                      <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Billing Period</label>
                      <p className="text-sm text-aegisaxiom-text capitalize">
                        {contract.billing_period || 'N/A'}
                      </p>
                    </div>
                    <div>
                      <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Invoice Terms</label>
                      <p className="text-sm text-aegisaxiom-text">
                        {contract.invoice_terms?.replace('_', ' ').toUpperCase() || 'N/A'}
                      </p>
                    </div>
                    <div>
                      <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Total Billed</label>
                      <p className="text-sm text-aegisaxiom-text font-semibold">
                        {fmt(contract.total_billed || 0)}
                      </p>
                    </div>
                  </>
                )}
                {contract.contract_type === 'fixed_fee' && (
                  <>
                    <div>
                      <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Total Amount</label>
                      <p className="text-sm text-aegisaxiom-text font-semibold">
                        {fmt(contract.total_amount || 0)}
                      </p>
                    </div>
                    <div>
                      <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Amount Invoiced</label>
                      <p className="text-sm text-emerald-600 dark:text-emerald-400 font-semibold">
                        {fmt(contract.amount_invoiced || 0)}
                      </p>
                    </div>
                    <div>
                      <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Remaining</label>
                      <p className="text-sm text-amber-600 dark:text-amber-400 font-semibold">
                        {fmt((contract.total_amount || 0) - (contract.amount_invoiced || 0))}
                      </p>
                    </div>
                  </>
                )}
              </div>
            </div>
          )}

          {/* Wholesale MRR Details */}
          {(contract.contract_type === 'monthly' || contract.contract_type === 'wholesale_mrr') && (
            <div className="glass-card p-4">
              <h3 className="text-sm font-semibold text-aegisaxiom-text mb-4 flex items-center gap-2">
                <Package size={16} className="text-cyan-600 dark:text-cyan-400" />
                Subscription Details
              </h3>
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Monthly Rate</label>
                  <p className="text-sm text-aegisaxiom-text font-semibold">
                    {fmt(contract.monthly_rate || 0)}
                  </p>
                </div>
                <div>
                  <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Billing Day</label>
                  <p className="text-sm text-aegisaxiom-text">{contract.billing_day || 1}</p>
                </div>
                {contract.product_name && (
                  <div>
                    <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Product</label>
                    <p className="text-sm text-aegisaxiom-text">{contract.product_name}</p>
                    {contract.product_sku && (
                      <p className="text-xs text-aegisaxiom-text-muted mt-0.5">SKU: {contract.product_sku}</p>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Resource Types */}
          {contract.resource_types && contract.resource_types.length > 0 && (
            <div className="glass-card p-4">
              <h3 className="text-sm font-semibold text-aegisaxiom-text mb-4 flex items-center gap-2">
                <Clock size={16} className="text-blue-600 dark:text-blue-400" />
                Resource Types
              </h3>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-aegisaxiom-border">
                      <th className="text-left text-xs font-medium text-aegisaxiom-text-secondary pb-2">
                        Resource Type
                      </th>
                      <th className="text-right text-xs font-medium text-aegisaxiom-text-secondary pb-2">
                        Sell Rate
                      </th>
                      <th className="text-right text-xs font-medium text-aegisaxiom-text-secondary pb-2">
                        Cost Rate
                      </th>
                      {contract.contract_type === 'boh' && (
                        <>
                          <th className="text-right text-xs font-medium text-aegisaxiom-text-secondary pb-2">
                            Hours
                          </th>
                          <th className="text-right text-xs font-medium text-aegisaxiom-text-secondary pb-2">
                            Used
                          </th>
                          <th className="text-right text-xs font-medium text-aegisaxiom-text-secondary pb-2">
                            Remaining
                          </th>
                        </>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {contract.resource_types.map((rt, idx) => (
                      <tr key={idx} className="border-b border-aegisaxiom-border last:border-0">
                        <td className="py-2 text-sm text-aegisaxiom-text">{rt.name}</td>
                        <td className="py-2 text-sm text-right text-aegisaxiom-text">
                          {fmt(rt.sell_rate || 0)}/hr
                        </td>
                        <td className="py-2 text-sm text-right text-aegisaxiom-text-secondary">
                          {fmt(rt.cost_rate || 0)}/hr
                        </td>
                        {contract.contract_type === 'boh' && (
                          <>
                            <td className="py-2 text-sm text-right text-aegisaxiom-text">
                              {rt.hours_purchased || 0}h
                            </td>
                            <td className="py-2 text-sm text-right text-amber-600 dark:text-amber-400">
                              {rt.hours_used || 0}h
                            </td>
                            <td className="py-2 text-sm text-right text-emerald-600 dark:text-emerald-400">
                              {rt.hours_remaining || 0}h
                            </td>
                          </>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Assigned Resources */}
          {contract.resources && contract.resources.length > 0 && (
            <div className="glass-card p-4">
              <h3 className="text-sm font-semibold text-aegisaxiom-text mb-4 flex items-center gap-2">
                <Users size={16} className="text-purple-400" />
                Assigned Resources
              </h3>
              <div className="space-y-2">
                {contract.resources.map((resource, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between p-2 bg-aegisaxiom-elevated rounded"
                  >
                    <div>
                      <p className="text-sm text-aegisaxiom-text">{resource.name}</p>
                      <p className="text-xs text-aegisaxiom-text-secondary">{resource.role}</p>
                    </div>
                    <span className="text-xs text-aegisaxiom-text-secondary">
                      {fmt(resource.cost_rate || 0)}/hr
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Milestones (Fixed Fee) */}
          {contract.contract_type === 'fixed_fee' &&
            contract.milestones &&
            contract.milestones.length > 0 && (
              <div className="glass-card p-4">
                <h3 className="text-sm font-semibold text-aegisaxiom-text mb-4 flex items-center gap-2">
                  <Calendar size={16} className="text-purple-400" />
                  Milestones
                </h3>
                <div className="space-y-2">
                  {contract.milestones.map((milestone, idx) => (
                    <div
                      key={idx}
                      className="flex items-center justify-between p-3 bg-aegisaxiom-elevated rounded"
                    >
                      <div className="flex-1">
                        <p className="text-sm text-aegisaxiom-text font-medium">{milestone.name}</p>
                        <div className="flex items-center gap-2 mt-1">
                          <span
                            className={`px-2 py-0.5 rounded text-xs ${
                              milestone.status === 'complete'
                                ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300'
                                : milestone.status === 'in_progress'
                                  ? 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300'
                                  : 'bg-aegisaxiom-elevated/20 text-aegisaxiom-text-secondary'
                            }`}
                          >
                            {milestone.status?.replace('_', ' ') || 'Pending'}
                          </span>
                          <span className="text-xs text-aegisaxiom-text-secondary">{milestone.percentage}%</span>
                        </div>
                      </div>
                      <span className="text-sm text-aegisaxiom-text font-semibold">
                        {fmt(milestone.amount || 0)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

          {/* Notes */}
          {contract.notes && (
            <div className="glass-card p-4">
              <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3">Notes</h3>
              <p className="text-sm text-aegisaxiom-text-secondary whitespace-pre-wrap">{contract.notes}</p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="sticky bottom-0 bg-aegisaxiom-surface border-t border-aegisaxiom-border p-4 flex justify-end gap-3">
          <button onClick={onClose} className="btn-secondary">
            Close
          </button>
          {onEdit && (
            <button onClick={() => onEdit(contract)} className="btn-primary">
              Edit
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
