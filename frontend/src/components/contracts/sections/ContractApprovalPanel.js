/**
 * ContractApprovalPanel — Approval-workflow actions for a contract.
 *
 * Wires the existing backend approval routes to the contract detail
 * surface so finance / sales ops can move contracts through the
 * submit → pending → approved/rejected/changes_requested lifecycle
 * without API tooling.
 *
 * Backend routes consumed (verbatim, no inventions):
 *   POST /api/contracts/{id}/submit-for-approval
 *   POST /api/contracts/{id}/approve
 *   POST /api/contracts/{id}/reject
 *   POST /api/contracts/{id}/request-changes
 *
 * Permissions (match backend/routes/contracts.py):
 *   - submit  : contracts.edit
 *   - approve : contracts.approve
 *   - reject  : contracts.approve
 *   - request : contracts.approve
 *
 * Status-action gating (mirrors backend _LEGAL_PRIOR_STATES):
 *   submit          : approval_status in {None, "", "draft",
 *                     "rejected", "changes_requested"}
 *   approve         : approval_status === "pending"
 *   reject          : approval_status === "pending"
 *   request_changes : approval_status === "pending"
 *
 * Body shape: the backend accepts `{ "notes": "..." }` OR
 * `{ "approval_notes": "..." }`. We send `notes`. The backend
 * stamps `approved_by` / `approved_at` only on the approve action;
 * the other actions leave prior approval metadata in place and
 * record the decision in `contract_approvals`.
 *
 * PS auto-create: approving a Professional Services contract may
 * auto-create a project. The response carries `_auto_created_project_id`
 * which we surface as a success-detail toast so the user knows.
 */
import React, { useState } from 'react';
import { toast } from 'sonner';
import { CheckCircle2, XCircle, Send, MessageSquareWarning, AlertTriangle } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';

const API = API_URL;

const APPROVAL_STATUS_META = {
  draft: {
    label: 'Draft',
    className:
      'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border',
  },
  pending: {
    label: 'Pending approval',
    className:
      'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border border-amber-300/40',
  },
  approved: {
    label: 'Approved',
    className:
      'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border border-emerald-300/40',
  },
  rejected: {
    label: 'Rejected',
    className:
      'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300 border border-red-300/40',
  },
  changes_requested: {
    label: 'Changes requested',
    className:
      'bg-orange-50 dark:bg-orange-600/20 text-orange-700 dark:text-orange-300 border border-orange-300/40',
  },
};

// Match backend _LEGAL_PRIOR_STATES. The None/missing/empty prior
// approval_status normalizes to "draft" for the submit action so a
// brand-new contract can be submitted without ever having had its
// approval_status field written.
const SUBMIT_LEGAL_PRIORS = new Set(['', 'draft', 'rejected', 'changes_requested']);
const DECISION_LEGAL_PRIORS = new Set(['pending']);

function normalizeApprovalStatus(value) {
  if (value === null || value === undefined) return '';
  return String(value);
}

function ApprovalStatusBadge({ approvalStatus }) {
  const key = normalizeApprovalStatus(approvalStatus) || 'draft';
  const meta = APPROVAL_STATUS_META[key] || APPROVAL_STATUS_META.draft;
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium ${meta.className}`}
      data-testid={`contract-approval-status-${key}`}
    >
      {meta.label}
    </span>
  );
}

function fmtDate(iso) {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleString();
  } catch (_) {
    return String(iso);
  }
}

function NotesModal({ open, action, contractName, onClose, onConfirm, busy }) {
  const [notes, setNotes] = useState('');
  React.useEffect(() => {
    if (open) setNotes('');
  }, [open, action]);

  if (!open) return null;

  const titles = {
    submit: 'Submit contract for approval',
    approve: 'Approve contract',
    reject: 'Reject contract',
    request_changes: 'Request changes on contract',
  };
  const submitLabels = {
    submit: 'Submit for approval',
    approve: 'Approve',
    reject: 'Reject',
    request_changes: 'Request changes',
  };

  const isReject = action === 'reject';
  const isChanges = action === 'request_changes';
  const isApprove = action === 'approve';

  const submitClass = isReject
    ? 'bg-red-600 hover:bg-red-500'
    : isChanges
      ? 'bg-orange-600 hover:bg-orange-500'
      : isApprove
        ? 'bg-emerald-600 hover:bg-emerald-500'
        : 'bg-violet-600 hover:bg-violet-500';

  return (
    <div
      className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[60] p-4"
      role="dialog"
      aria-modal="true"
      data-testid="contract-approval-notes-modal"
    >
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-lg">
        <div className="p-5 border-b border-aegisaxiom-border">
          <h3 className="text-base font-semibold text-aegisaxiom-text">{titles[action]}</h3>
          {contractName ? (
            <p className="text-xs text-aegisaxiom-text-secondary mt-1 truncate">{contractName}</p>
          ) : null}
        </div>
        <div className="p-5 space-y-2">
          <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">
            Notes (optional — stored on the contract_approvals row and the audit log)
          </label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={4}
            className="aegisaxiom-input w-full text-sm"
            placeholder={
              isReject
                ? 'Why is this contract being rejected? Notes help the submitter fix and resubmit.'
                : isChanges
                  ? 'What needs to change before this contract can be approved?'
                  : isApprove
                    ? 'Optional approval context for the audit log.'
                    : 'Optional submission context.'
            }
            data-testid="contract-approval-notes-input"
          />
        </div>
        <div className="p-4 border-t border-aegisaxiom-border flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onConfirm(notes)}
            disabled={busy}
            className={`px-3 py-1.5 text-sm rounded text-white transition disabled:opacity-50 ${submitClass}`}
            data-testid={`contract-approval-confirm-${action}`}
          >
            {busy ? 'Working…' : submitLabels[action]}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function ContractApprovalPanel({ contract, onContractUpdated }) {
  const { hasPermission } = usePermissions();
  const [busy, setBusy] = useState(false);
  const [modal, setModal] = useState({ open: false, action: null });

  if (!contract || !contract.contract_id) return null;

  const approvalStatus = normalizeApprovalStatus(contract.approval_status);
  const canSubmit = hasPermission('contracts.edit') || hasPermission('*');
  const canDecide = hasPermission('contracts.approve') || hasPermission('*');

  // Mirror backend _LEGAL_PRIOR_STATES so the UI never offers a
  // transition that the route will reject with 409.
  const submitLegal = SUBMIT_LEGAL_PRIORS.has(approvalStatus);
  const decisionLegal = DECISION_LEGAL_PRIORS.has(approvalStatus);

  const showSubmit = canSubmit && submitLegal;
  const showApprove = canDecide && decisionLegal;
  const showReject = canDecide && decisionLegal;
  const showRequestChanges = canDecide && decisionLegal;

  const openModal = (action) => setModal({ open: true, action });
  const closeModal = () => setModal({ open: false, action: null });

  const runAction = async (action, notes) => {
    setBusy(true);
    try {
      const urlSuffix = action === 'request_changes' ? 'request-changes'
        : action === 'submit' ? 'submit-for-approval'
          : action;
      const url = `${API}/api/contracts/${contract.contract_id}/${urlSuffix}`;
      const body = notes && notes.trim() ? { notes: notes.trim() } : {};
      const res = await api.post(url, body);
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        const message = typeof detail?.detail === 'string'
          ? detail.detail
          : `Failed to ${action.replace('_', ' ')} contract`;
        toast.error(message);
        return;
      }
      const updated = await res.json();
      const successMsg = {
        submit: 'Contract submitted for approval',
        approve: 'Contract approved',
        reject: 'Contract rejected',
        request_changes: 'Changes requested on contract',
      }[action];
      if (action === 'approve' && updated && updated._auto_created_project_id) {
        toast.success(`${successMsg} (project auto-created)`);
      } else {
        toast.success(successMsg);
      }
      closeModal();
      onContractUpdated?.(updated);
    } catch (e) {
      toast.error(`Failed to ${action.replace('_', ' ')} contract`);
    } finally {
      setBusy(false);
    }
  };

  // Hide the panel entirely when the user has neither permission —
  // there is no actionable surface and the status badge alone is
  // already visible in the parent modal header.
  if (!canSubmit && !canDecide) {
    return null;
  }

  const approvedAt = fmtDate(contract.approved_at);
  const approvalNotes = (contract.approval_notes || '').trim();

  return (
    <div className="glass-card p-4" data-testid="contract-approval-panel">
      <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
        <CheckCircle2 size={16} className="text-violet-500" />
        Approval
      </h3>

      <div className="space-y-3">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-xs text-aegisaxiom-text-secondary">Status:</span>
          <ApprovalStatusBadge approvalStatus={approvalStatus} />
          {approvedAt ? (
            <span className="text-[11px] text-aegisaxiom-text-muted">
              Approved {approvedAt}{contract.approved_by ? ` by ${contract.approved_by}` : ''}
            </span>
          ) : null}
        </div>

        {approvalNotes ? (
          <div className="bg-aegisaxiom-elevated/40 border border-aegisaxiom-border rounded p-3">
            <div className="text-[10px] uppercase tracking-wide text-aegisaxiom-text-muted mb-1">
              Latest decision notes
            </div>
            <p className="text-xs text-aegisaxiom-text whitespace-pre-wrap">{approvalNotes}</p>
          </div>
        ) : null}

        {!canDecide && !showSubmit ? (
          <p className="text-xs text-aegisaxiom-text-muted">
            No approval actions available from the current status with your permissions.
          </p>
        ) : null}

        <div className="flex items-center gap-2 flex-wrap pt-1">
          {showSubmit && (
            <button
              type="button"
              onClick={() => openModal('submit')}
              disabled={busy}
              className="inline-flex items-center gap-1 px-3 py-1.5 text-xs rounded bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white"
              data-testid="contract-approval-submit"
            >
              <Send className="w-3 h-3" />
              Submit for approval
            </button>
          )}
          {showApprove && (
            <button
              type="button"
              onClick={() => openModal('approve')}
              disabled={busy}
              className="inline-flex items-center gap-1 px-3 py-1.5 text-xs rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white"
              data-testid="contract-approval-approve"
            >
              <CheckCircle2 className="w-3 h-3" />
              Approve
            </button>
          )}
          {showRequestChanges && (
            <button
              type="button"
              onClick={() => openModal('request_changes')}
              disabled={busy}
              className="inline-flex items-center gap-1 px-3 py-1.5 text-xs rounded bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white"
              data-testid="contract-approval-request-changes"
            >
              <MessageSquareWarning className="w-3 h-3" />
              Request changes
            </button>
          )}
          {showReject && (
            <button
              type="button"
              onClick={() => openModal('reject')}
              disabled={busy}
              className="inline-flex items-center gap-1 px-3 py-1.5 text-xs rounded bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white"
              data-testid="contract-approval-reject"
            >
              <XCircle className="w-3 h-3" />
              Reject
            </button>
          )}
        </div>

        {approvalStatus === 'approved' && (
          <p className="text-[11px] text-aegisaxiom-text-muted inline-flex items-center gap-1">
            <AlertTriangle className="w-3 h-3" />
            This contract is already approved — no further approval actions are available.
          </p>
        )}
      </div>

      <NotesModal
        open={modal.open}
        action={modal.action}
        contractName={contract.name || contract.contract_id}
        onClose={closeModal}
        onConfirm={(notes) => runAction(modal.action, notes)}
        busy={busy}
      />
    </div>
  );
}
