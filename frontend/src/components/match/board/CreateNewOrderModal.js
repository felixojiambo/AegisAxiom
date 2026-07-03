import React, { useEffect, useState } from 'react';
import { FilePlus } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

// ── Option lists ──────────────────────────────────────────────────────────────
// Derived from POC main RowDetailDrawer.js lines 57–98.

const ORDER_STATUS_OPTIONS = [
  'New',
  'Pending Activation',
  'Active',
  'Suspended',
  'Cancelled',
];

const CONTRACT_TYPE_OPTIONS = ['New', 'MAC', 'MACD', 'Renewal', 'Upgrade'];

const CONTRACT_TERM_OPTIONS = [
  'Month-to-Month',
  '12 Months',
  '24 Months',
  '36 Months',
  '60 Months',
];

// ── CreateNewOrderModal ───────────────────────────────────────────────────────
//
// Pops when the reviewer clicks "Create New Order" in the row drawer.
// Pre-filled from `draft` (sourced from row.output.order_details +
// row.output.order_attributes). The reviewer can edit all fields before
// submitting to AppDirect.
//
// Props:
//   open          {boolean}
//   onOpenChange  {(boolean) => void}
//   draft         {object}  — initial field values:
//                   { product, provider, order_status, commission_ban,
//                     provider_order_id, contract_type, contract_term,
//                     contract_signed, mrgmv, nrc, ... }
//   submitting    {boolean} — disables submit while parent is saving
//   onConfirm     {(editedDraft) => Promise<void>}
//
// `editedDraft` passed to onConfirm has the same flat shape as the local state.

export default function CreateNewOrderModal({
  open,
  onOpenChange,
  draft,
  submitting,
  onConfirm,
}) {
  const [fields, setFields] = useState(() => flattenDraft(draft));

  useEffect(() => {
    if (open) {
      setFields(flattenDraft(draft));
    }
  }, [open, draft]);

  function flattenDraft(d) {
    if (!d) {
      return {
        provider: '',
        product: '',
        order_status: '',
        commission_ban: '',
        provider_order_id: '',
        contract_type: '',
        contract_term: '',
        contract_signed: '',
        mrgmv: '',
        nrc: '',
      };
    }
    // draft may come from order_details directly or from the merged output object
    return {
      provider: d.provider ?? '',
      product: d.product ?? '',
      order_status: d.order_status ?? '',
      commission_ban: d.commission_ban ?? '',
      provider_order_id: d.provider_order_id ?? '',
      contract_type: d.contract_type ?? '',
      contract_term: d.contract_term ?? '',
      contract_signed: d.contract_signed ?? '',
      mrgmv: d.mrgmv != null ? String(d.mrgmv) : '',
      nrc: d.nrc != null ? String(d.nrc) : '',
    };
  }

  function set(key, value) {
    setFields((prev) => ({ ...prev, [key]: value }));
  }

  function handleCancel() {
    onOpenChange(false);
  }

  async function handleSubmit() {
    const payload = {
      ...fields,
      mrgmv: fields.mrgmv !== '' ? parseFloat(fields.mrgmv) : null,
      nrc: fields.nrc !== '' ? parseFloat(fields.nrc) : null,
    };
    await onConfirm(payload);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="sm:max-w-[560px] bg-aegisaxiom-surface border border-aegisaxiom-border text-aegisaxiom-text"
        data-testid="create-order-modal"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-aegisaxiom-text">
            <FilePlus className="h-5 w-5 text-aegisaxiom-text-secondary" />
            Create New Order
          </DialogTitle>
          <DialogDescription className="text-aegisaxiom-text-secondary">
            Pre-filled from the comparison screen. Edit any field before submitting — these
            values will be sent to create the order in AppDirect.
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-x-4 gap-y-4 py-2">
          {/* Provider — locked to the row's provider, shown read-only */}
          <div>
            <Label htmlFor="com-provider" className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
              Provider
            </Label>
            <Input
              id="com-provider"
              className="aegisaxiom-input opacity-70 cursor-not-allowed"
              value={fields.provider}
              readOnly
              disabled
              data-testid="create-order-provider-input"
            />
          </div>

          {/* Product */}
          <div>
            <Label htmlFor="com-product" className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
              Product
            </Label>
            <Input
              id="com-product"
              className="aegisaxiom-input"
              value={fields.product}
              onChange={(e) => set('product', e.target.value)}
              data-testid="create-order-product-input"
            />
          </div>

          {/* Order Status */}
          <div>
            <Label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
              Order Status
            </Label>
            <Select
              value={fields.order_status}
              onValueChange={(v) => set('order_status', v)}
            >
              <SelectTrigger
                className="aegisaxiom-input w-full"
                data-testid="create-order-status-trigger"
              >
                <SelectValue placeholder="Select status" />
              </SelectTrigger>
              <SelectContent className="bg-aegisaxiom-surface border border-aegisaxiom-border text-aegisaxiom-text">
                {ORDER_STATUS_OPTIONS.map((opt) => (
                  <SelectItem
                    key={opt}
                    value={opt}
                    className="hover:bg-aegisaxiom-surface-elevated focus:bg-aegisaxiom-surface-elevated text-aegisaxiom-text"
                  >
                    {opt}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Commission BAN */}
          <div>
            <Label htmlFor="com-ban" className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
              Commission BAN
            </Label>
            <Input
              id="com-ban"
              className="aegisaxiom-input"
              value={fields.commission_ban}
              onChange={(e) => set('commission_ban', e.target.value)}
              data-testid="create-order-commission-ban-input"
            />
          </div>

          {/* Provider Sales Order ID */}
          <div className="col-span-2">
            <Label htmlFor="com-provider-order-id" className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
              Provider Sales Order ID
            </Label>
            <Input
              id="com-provider-order-id"
              className="aegisaxiom-input"
              value={fields.provider_order_id}
              onChange={(e) => set('provider_order_id', e.target.value)}
              data-testid="create-order-provider-order-id-input"
            />
          </div>

          {/* Contract Type */}
          <div>
            <Label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
              Contract Type
            </Label>
            <Select
              value={fields.contract_type}
              onValueChange={(v) => set('contract_type', v)}
            >
              <SelectTrigger
                className="aegisaxiom-input w-full"
                data-testid="create-order-contract-type-trigger"
              >
                <SelectValue placeholder="Select type" />
              </SelectTrigger>
              <SelectContent className="bg-aegisaxiom-surface border border-aegisaxiom-border text-aegisaxiom-text">
                {CONTRACT_TYPE_OPTIONS.map((opt) => (
                  <SelectItem
                    key={opt}
                    value={opt}
                    className="hover:bg-aegisaxiom-surface-elevated focus:bg-aegisaxiom-surface-elevated text-aegisaxiom-text"
                  >
                    {opt}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Contract Term */}
          <div>
            <Label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
              Contract Term
            </Label>
            <Select
              value={fields.contract_term}
              onValueChange={(v) => set('contract_term', v)}
            >
              <SelectTrigger
                className="aegisaxiom-input w-full"
                data-testid="create-order-contract-term-trigger"
              >
                <SelectValue placeholder="Select term" />
              </SelectTrigger>
              <SelectContent className="bg-aegisaxiom-surface border border-aegisaxiom-border text-aegisaxiom-text">
                {CONTRACT_TERM_OPTIONS.map((opt) => (
                  <SelectItem
                    key={opt}
                    value={opt}
                    className="hover:bg-aegisaxiom-surface-elevated focus:bg-aegisaxiom-surface-elevated text-aegisaxiom-text"
                  >
                    {opt}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Contract Signed */}
          <div>
            <Label htmlFor="com-contract-signed" className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
              Contract Signed
            </Label>
            <Input
              id="com-contract-signed"
              type="date"
              className="aegisaxiom-input"
              value={fields.contract_signed}
              onChange={(e) => set('contract_signed', e.target.value)}
              data-testid="create-order-contract-signed-input"
            />
          </div>

          {/* MRGMV */}
          <div>
            <Label htmlFor="com-mrgmv" className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
              MRGMV
            </Label>
            <Input
              id="com-mrgmv"
              type="number"
              step="0.01"
              min="0"
              className="aegisaxiom-input"
              value={fields.mrgmv}
              onChange={(e) => set('mrgmv', e.target.value)}
              placeholder="0.00"
              data-testid="create-order-mrgmv-input"
            />
          </div>

          {/* NRC */}
          <div>
            <Label htmlFor="com-nrc" className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
              NRC
            </Label>
            <Input
              id="com-nrc"
              type="number"
              step="0.01"
              min="0"
              className="aegisaxiom-input"
              value={fields.nrc}
              onChange={(e) => set('nrc', e.target.value)}
              placeholder="0.00"
              data-testid="create-order-nrc-input"
            />
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={handleCancel}
            disabled={submitting}
            className="border-aegisaxiom-border text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-surface-elevated"
            data-testid="create-order-cancel-button"
          >
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={submitting}
            className="btn-primary flex items-center gap-1.5"
            data-testid="create-order-submit-button"
          >
            <FilePlus className="h-4 w-4" />
            {submitting ? 'Creating…' : 'Create Order'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
