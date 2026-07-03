import React, { useEffect, useState } from 'react';
import { UserPlus } from 'lucide-react';
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

// ── CreateNewCustomerModal ────────────────────────────────────────────────────
//
// Pops when the reviewer clicks "Create New Customer" in the row drawer.
// Pre-filled from `draft` (sourced from row.output.top_level + row.source_row).
// Every field is editable so the reviewer can correct the customer record
// before submitting to AppDirect.
//
// Props:
//   open          {boolean}
//   onOpenChange  {(boolean) => void}
//   draft         {object}  — initial field values:
//                   { advisor_company, sales_rep,
//                     customer: { name },
//                     location: { street, city, state, zip } }
//   submitting    {boolean} — disables submit while parent is saving
//   onConfirm     {(editedDraft) => Promise<void>}
//
// `editedDraft` passed to onConfirm mirrors the flat shape used internally:
//   { name, street, city, state, zip, advisor_company, sales_rep }

export default function CreateNewCustomerModal({
  open,
  onOpenChange,
  draft,
  submitting,
  onConfirm,
}) {
  // Flatten the nested draft prop into a local flat state that mirrors the
  // field layout. Re-seed whenever `draft` changes (i.e. a new row is opened).
  const [fields, setFields] = useState(() => flattenDraft(draft));

  useEffect(() => {
    if (open) {
      setFields(flattenDraft(draft));
    }
  }, [open, draft]);

  function flattenDraft(d) {
    if (!d) {
      return {
        name: '',
        advisor_company: '',
        sales_rep: '',
        street: '',
        city: '',
        state: '',
        zip: '',
      };
    }
    return {
      name: d.customer?.name ?? d.name ?? '',
      advisor_company: d.advisor_company ?? '',
      sales_rep: d.sales_rep ?? '',
      street: d.location?.street ?? d.street ?? '',
      city: d.location?.city ?? d.city ?? '',
      state: d.location?.state ?? d.state ?? '',
      zip: d.location?.zip ?? d.zip ?? '',
    };
  }

  function set(key, value) {
    setFields((prev) => ({ ...prev, [key]: value }));
  }

  function handleCancel() {
    onOpenChange(false);
  }

  async function handleSubmit() {
    if (!fields.name?.trim()) return;
    await onConfirm(fields);
  }

  const nameEmpty = !fields.name?.trim();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="sm:max-w-[540px] bg-aegisaxiom-surface border border-aegisaxiom-border text-aegisaxiom-text"
        data-testid="create-customer-modal"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-aegisaxiom-text">
            <UserPlus className="h-5 w-5 text-aegisaxiom-text-secondary" />
            Create New AppDirect Customer
          </DialogTitle>
          <DialogDescription className="text-aegisaxiom-text-secondary">
            Pre-filled from the comparison screen. Edit any field before submitting — these
            values will be sent to create the customer in AppDirect.
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-x-4 gap-y-4 py-2">
          {/* Customer Name */}
          <div className="col-span-2">
            <Label htmlFor="ccm-name" className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
              Customer Name <span className="text-aegisaxiom-status-danger">*</span>
            </Label>
            <Input
              id="ccm-name"
              className="aegisaxiom-input"
              value={fields.name}
              onChange={(e) => set('name', e.target.value)}
              placeholder="Customer name"
              data-testid="create-customer-name-input"
            />
          </div>

          {/* AppDirect Customer ID — read-only, assigned on creation */}
          <div className="col-span-2">
            <Label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
              AppDirect Customer ID
            </Label>
            <Input
              className="aegisaxiom-input opacity-60 cursor-not-allowed"
              value=""
              readOnly
              disabled
              placeholder="—"
              data-testid="create-customer-appdirect-id-input"
            />
            <p className="text-[11px] text-aegisaxiom-text-secondary italic mt-1">
              Will be assigned by AppDirect when the new customer is created.
            </p>
          </div>

          {/* Advisor Company */}
          <div className="col-span-2">
            <Label htmlFor="ccm-advisor-company" className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
              Advisor Company
            </Label>
            <Input
              id="ccm-advisor-company"
              className="aegisaxiom-input"
              value={fields.advisor_company}
              onChange={(e) => set('advisor_company', e.target.value)}
              data-testid="create-customer-advisor-company-input"
            />
          </div>

          {/* Sales Rep */}
          <div className="col-span-2">
            <Label htmlFor="ccm-sales-rep" className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
              Sales Rep
            </Label>
            <Input
              id="ccm-sales-rep"
              className="aegisaxiom-input"
              value={fields.sales_rep}
              onChange={(e) => set('sales_rep', e.target.value)}
              placeholder="Will default from template if left blank"
              data-testid="create-customer-sales-rep-input"
            />
          </div>

          {/* Street */}
          <div className="col-span-2">
            <Label htmlFor="ccm-street" className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
              Street
            </Label>
            <Input
              id="ccm-street"
              className="aegisaxiom-input"
              value={fields.street}
              onChange={(e) => set('street', e.target.value)}
              data-testid="create-customer-street-input"
            />
          </div>

          {/* City */}
          <div>
            <Label htmlFor="ccm-city" className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
              City
            </Label>
            <Input
              id="ccm-city"
              className="aegisaxiom-input"
              value={fields.city}
              onChange={(e) => set('city', e.target.value)}
              data-testid="create-customer-city-input"
            />
          </div>

          {/* State + Zip side by side */}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label htmlFor="ccm-state" className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
                State
              </Label>
              <Input
                id="ccm-state"
                className="aegisaxiom-input"
                value={fields.state}
                onChange={(e) => set('state', e.target.value)}
                data-testid="create-customer-state-input"
              />
            </div>
            <div>
              <Label htmlFor="ccm-zip" className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">
                ZIP
              </Label>
              <Input
                id="ccm-zip"
                className="aegisaxiom-input"
                value={fields.zip}
                onChange={(e) => set('zip', e.target.value)}
                data-testid="create-customer-zip-input"
              />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={handleCancel}
            disabled={submitting}
            className="border-aegisaxiom-border text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-surface-elevated"
            data-testid="create-customer-cancel-button"
          >
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={submitting || nameEmpty}
            className="btn-primary flex items-center gap-1.5"
            data-testid="create-customer-submit-button"
          >
            <UserPlus className="h-4 w-4" />
            {submitting ? 'Creating…' : 'Create Customer'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
