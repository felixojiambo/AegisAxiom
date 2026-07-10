import React, { useState } from 'react';
import { X, Save } from 'lucide-react';
import { api } from '../lib/api';
import { toast } from 'sonner';
import { ZohoConfirmDialog, ZohoConflictDialog } from './ZohoSyncDialogs';
import API_URL from '../lib/apiConfig';

const API = API_URL;
const STAGES = [
  'Prospecting',
  'Suspect',
  'Qualification',
  'Needs Analysis',
  'Value Proposition',
  'Proposal/Price Quote',
  'Negotiation/Review',
  'Closed Won',
  'Closed Lost',
];
const STATUS_OPTIONS = ['Paid', 'Owed', 'Potential'];

/**
 * Modal to edit a Zoho Deal record with write-back + conflict detection.
 */
export function ZohoDealEditModal({ deal, onClose, onSaved }) {
  const [form, setForm] = useState({
    deal_name: deal.deal_name || '',
    stage: deal.stage || '',
    amount: deal.amount || 0,
    close_date: deal.close_date || '',
  });
  const [saving, setSaving] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [conflict, setConflict] = useState(null);

  const changes = {};
  if (form.deal_name !== (deal.deal_name || '')) changes.deal_name = form.deal_name;
  if (form.stage !== (deal.stage || '')) changes.stage = form.stage;
  if (Number(form.amount) !== Number(deal.amount || 0)) changes.amount = Number(form.amount);
  if (form.close_date !== (deal.close_date || '')) changes.close_date = form.close_date;
  const hasChanges = Object.keys(changes).length > 0;

  const handleSave = () => {
    if (hasChanges) setShowConfirm(true);
  };

  const doSave = async (force = false) => {
    setSaving(true);
    setShowConfirm(false);
    setConflict(null);
    try {
      const body = { ...changes, force };
      const res = await api.put(`${API}/api/zoho-sync/deals/${deal.zoho_id}`, body);
      const data = await res.json();
      if (data.conflict) {
        setConflict(data);
        setSaving(false);
        return;
      }
      if (!res.ok) throw new Error(data.detail || 'Update failed');
      toast.success('Deal updated and synced to Zoho');
      onSaved?.();
      onClose();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleKeepRemote = async () => {
    // Apply Zoho's values locally, don't push
    if (conflict?.remote) {
      setForm({
        deal_name: conflict.remote.deal_name || form.deal_name,
        stage: conflict.remote.stage || form.stage,
        amount: conflict.remote.amount || form.amount,
        close_date: conflict.remote.close_date || form.close_date,
      });
    }
    setConflict(null);
    toast.info('Zoho version applied. Review and save again if needed.');
  };

  const DEAL_FIELDS = [
    { key: 'deal_name', label: 'Deal Name' },
    { key: 'stage', label: 'Stage' },
    { key: 'amount', label: 'Amount' },
    { key: 'close_date', label: 'Close Date' },
  ];

  return (
    <>
      <div
        className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 backdrop-blur-sm"
        data-testid="zoho-deal-edit-modal"
      >
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl w-full max-w-md p-6 mx-4">
          <div className="flex items-center justify-between mb-5">
            <h3 className="text-lg font-semibold text-aegisaxiom-text">Edit Zoho Deal</h3>
            <button onClick={onClose} className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary">
              <X size={18} />
            </button>
          </div>
          <div className="space-y-4">
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Deal Name</label>
              <input
                className="input-field"
                value={form.deal_name}
                onChange={(e) => setForm({ ...form, deal_name: e.target.value })}
                data-testid="edit-zoho-deal-name"
              />
            </div>
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Stage</label>
              <select
                className="input-field"
                value={form.stage}
                onChange={(e) => setForm({ ...form, stage: e.target.value })}
                data-testid="edit-zoho-deal-stage"
              >
                {STAGES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Amount ($)</label>
                <input
                  type="number"
                  className="input-field"
                  value={form.amount}
                  onChange={(e) => setForm({ ...form, amount: e.target.value })}
                  data-testid="edit-zoho-deal-amount"
                />
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Close Date</label>
                <input
                  type="date"
                  className="input-field"
                  value={form.close_date}
                  onChange={(e) => setForm({ ...form, close_date: e.target.value })}
                  data-testid="edit-zoho-deal-close-date"
                />
              </div>
            </div>
          </div>
          <div className="flex gap-3 justify-end mt-6">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={!hasChanges || saving}
              className="px-4 py-2 text-sm bg-violet-600 hover:bg-violet-500 text-white rounded-lg font-medium disabled:opacity-40 flex items-center gap-2"
              data-testid="save-zoho-deal-btn"
            >
              <Save size={14} />
              {saving ? 'Syncing...' : 'Save & Sync'}
            </button>
          </div>
        </div>
      </div>

      <ZohoConfirmDialog
        open={showConfirm}
        changes={changes}
        onConfirm={() => doSave(false)}
        onCancel={() => setShowConfirm(false)}
      />
      <ZohoConflictDialog
        open={!!conflict}
        localData={conflict?.local || {}}
        remoteData={conflict?.remote || {}}
        fields={DEAL_FIELDS}
        onKeepLocal={() => doSave(true)}
        onKeepRemote={handleKeepRemote}
        onCancel={() => setConflict(null)}
      />
    </>
  );
}

/**
 * Modal to edit a Zoho Commission record with write-back + conflict detection.
 */
export function ZohoCommissionEditModal({ commission, onClose, onSaved }) {
  const [form, setForm] = useState({
    sales_rep_commission: commission.sales_rep_commission ?? 0,
    sales_rep_commission_status: commission.sales_rep_commission_status || 'Owed',
    agent_commission: commission.agent_commission ?? 0,
    agent_commission_status: commission.agent_commission_status || 'Owed',
    dir_commission: commission.dir_commission ?? 0,
    dir_commission_status: commission.dir_commission_status || 'Owed',
    payment_date: commission.payment_date || '',
    invoice_no: commission.invoice_no || '',
  });
  const [saving, setSaving] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [conflict, setConflict] = useState(null);

  const changes = {};
  if (Number(form.sales_rep_commission) !== Number(commission.sales_rep_commission ?? 0))
    changes.sales_rep_commission = Number(form.sales_rep_commission);
  if (form.sales_rep_commission_status !== (commission.sales_rep_commission_status || ''))
    changes.sales_rep_commission_status = form.sales_rep_commission_status;
  if (Number(form.agent_commission) !== Number(commission.agent_commission ?? 0))
    changes.agent_commission = Number(form.agent_commission);
  if (form.agent_commission_status !== (commission.agent_commission_status || ''))
    changes.agent_commission_status = form.agent_commission_status;
  if (Number(form.dir_commission) !== Number(commission.dir_commission ?? 0))
    changes.dir_commission = Number(form.dir_commission);
  if (form.dir_commission_status !== (commission.dir_commission_status || ''))
    changes.dir_commission_status = form.dir_commission_status;
  if (form.payment_date !== (commission.payment_date || ''))
    changes.payment_date = form.payment_date;
  if (form.invoice_no !== (commission.invoice_no || '')) changes.invoice_no = form.invoice_no;
  const hasChanges = Object.keys(changes).length > 0;

  const handleSave = () => {
    if (hasChanges) setShowConfirm(true);
  };

  const doSave = async (force = false) => {
    setSaving(true);
    setShowConfirm(false);
    setConflict(null);
    try {
      const body = { ...changes, force };
      const res = await api.put(`${API}/api/zoho-sync/commissions/${commission.zoho_id}`, body);
      const data = await res.json();
      if (data.conflict) {
        setConflict(data);
        setSaving(false);
        return;
      }
      if (!res.ok) throw new Error(data.detail || 'Update failed');
      toast.success('Commission updated and synced to Zoho');
      onSaved?.();
      onClose();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleKeepRemote = async () => {
    if (conflict?.remote) {
      setForm((prev) => ({
        ...prev,
        sales_rep_commission: conflict.remote.sales_rep_commission ?? prev.sales_rep_commission,
        sales_rep_commission_status:
          conflict.remote.sales_rep_commission_status ?? prev.sales_rep_commission_status,
        agent_commission: conflict.remote.agent_commission ?? prev.agent_commission,
        agent_commission_status:
          conflict.remote.agent_commission_status ?? prev.agent_commission_status,
        payment_date: conflict.remote.payment_date ?? prev.payment_date,
      }));
    }
    setConflict(null);
    toast.info('Zoho version applied. Review and save again if needed.');
  };

  const COMM_FIELDS = [
    { key: 'deal_name', label: 'Deal' },
    { key: 'sales_rep_commission', label: 'Rep Commission' },
    { key: 'sales_rep_commission_status', label: 'Rep Status' },
    { key: 'agent_commission', label: 'Agent Commission' },
    { key: 'agent_commission_status', label: 'Agent Status' },
    { key: 'payment_date', label: 'Payment Date' },
  ];

  const fmt = (n) =>
    new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: 0,
    }).format(n || 0);

  return (
    <>
      <div
        className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 backdrop-blur-sm"
        data-testid="zoho-commission-edit-modal"
      >
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl w-full max-w-lg p-6 mx-4">
          <div className="flex items-center justify-between mb-1">
            <h3 className="text-lg font-semibold text-aegisaxiom-text">Edit Zoho Commission</h3>
            <button onClick={onClose} className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary">
              <X size={18} />
            </button>
          </div>
          <p className="text-xs text-aegisaxiom-text-muted mb-5">
            {commission.deal_name} — {commission.customer_name || commission.sales_rep_name}
          </p>

          <div className="space-y-4">
            {/* Sales Rep Commission */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Rep Commission ($)</label>
                <input
                  type="number"
                  className="input-field"
                  value={form.sales_rep_commission}
                  onChange={(e) => setForm({ ...form, sales_rep_commission: e.target.value })}
                  data-testid="edit-rep-commission"
                />
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Rep Status</label>
                <select
                  className="input-field"
                  value={form.sales_rep_commission_status}
                  onChange={(e) =>
                    setForm({ ...form, sales_rep_commission_status: e.target.value })
                  }
                  data-testid="edit-rep-status"
                >
                  {STATUS_OPTIONS.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            {/* Agent Commission */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Agent Commission ($)</label>
                <input
                  type="number"
                  className="input-field"
                  value={form.agent_commission}
                  onChange={(e) => setForm({ ...form, agent_commission: e.target.value })}
                  data-testid="edit-agent-commission"
                />
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Agent Status</label>
                <select
                  className="input-field"
                  value={form.agent_commission_status}
                  onChange={(e) => setForm({ ...form, agent_commission_status: e.target.value })}
                  data-testid="edit-agent-status"
                >
                  {STATUS_OPTIONS.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            {/* Director Commission */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Dir Commission ($)</label>
                <input
                  type="number"
                  className="input-field"
                  value={form.dir_commission}
                  onChange={(e) => setForm({ ...form, dir_commission: e.target.value })}
                  data-testid="edit-dir-commission"
                />
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Dir Status</label>
                <select
                  className="input-field"
                  value={form.dir_commission_status}
                  onChange={(e) => setForm({ ...form, dir_commission_status: e.target.value })}
                  data-testid="edit-dir-status"
                >
                  {STATUS_OPTIONS.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            {/* Payment Date & Invoice */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Payment Date</label>
                <input
                  type="date"
                  className="input-field"
                  value={form.payment_date}
                  onChange={(e) => setForm({ ...form, payment_date: e.target.value })}
                  data-testid="edit-payment-date"
                />
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Invoice #</label>
                <input
                  className="input-field"
                  value={form.invoice_no}
                  onChange={(e) => setForm({ ...form, invoice_no: e.target.value })}
                  data-testid="edit-invoice-no"
                />
              </div>
            </div>
          </div>

          <div className="flex gap-3 justify-end mt-6">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={!hasChanges || saving}
              className="px-4 py-2 text-sm bg-violet-600 hover:bg-violet-500 text-white rounded-lg font-medium disabled:opacity-40 flex items-center gap-2"
              data-testid="save-zoho-commission-btn"
            >
              <Save size={14} />
              {saving ? 'Syncing...' : 'Save & Sync'}
            </button>
          </div>
        </div>
      </div>

      <ZohoConfirmDialog
        open={showConfirm}
        changes={changes}
        onConfirm={() => doSave(false)}
        onCancel={() => setShowConfirm(false)}
      />
      <ZohoConflictDialog
        open={!!conflict}
        localData={conflict?.local || {}}
        remoteData={conflict?.remote || {}}
        fields={COMM_FIELDS}
        onKeepLocal={() => doSave(true)}
        onKeepRemote={handleKeepRemote}
        onCancel={() => setConflict(null)}
      />
    </>
  );
}
