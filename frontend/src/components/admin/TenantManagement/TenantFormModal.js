import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { X, Briefcase, Building2, AlertCircle } from 'lucide-react';
import { toast } from 'sonner';
import { createTenant, updateTenant } from './api';
import { isValidSlug, deriveSlug } from './helpers';

/**
 * Phase 1D.3 — Create / Edit tenant modal.
 *
 * One modal handles both create and edit, distinguished by the `mode` prop.
 * The form fields adapt:
 *   • Create mode shows Type (radio) and a context-appropriate Parent picker.
 *   • Edit mode shows Type as read-only (changing partner ↔ account is risky
 *     and intentionally not exposed) and surfaces a Status radio for
 *     active ↔ suspended. Archive is a separate destructive action handled
 *     by the kebab in TenantList, not through this modal.
 *
 * Slug auto-derives from Name until the user manually edits the slug field,
 * after which we stop overwriting. In edit mode the existing slug is treated
 * as "already touched" so the auto-derivation never runs.
 *
 * Server-side 409s on slug uniqueness surface as inline errors on the slug
 * field. All other API errors surface in a global banner at the top of the
 * form to keep field-level UX clean.
 *
 * Props
 *   isOpen        boolean
 *   mode          'create' | 'edit'
 *   tenant        existing tenant object (required in edit mode)
 *   allTenants    full tenant list, used to resolve the root tenant and
 *                 build the partner-picker for accounts
 *   onClose       () => void
 *   onSuccess     (tenant) => void   called with the created/updated tenant
 */

const TYPES = [
  {
    id: 'partner',
    label: 'Partner',
    icon: Briefcase,
    description: 'A reseller or distributor who can have sub-accounts beneath them.',
  },
  {
    id: 'account',
    label: 'Account',
    icon: Building2,
    description: 'A customer of SimpliMeta or one of its partners.',
  },
];

const STATUSES = [
  { id: 'active', label: 'Active', description: 'Tenant is fully operational.' },
  { id: 'suspended', label: 'Suspended', description: 'Tenant is read-only until reactivated.' },
];

export default function TenantFormModal({
  isOpen,
  mode,
  tenant,
  allTenants,
  onClose,
  onSuccess,
}) {
  const isEdit = mode === 'edit';
  const original = isEdit ? tenant : null;

  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [slugTouched, setSlugTouched] = useState(false);
  const [tenantType, setTenantType] = useState('partner');
  const [parentTenantId, setParentTenantId] = useState('');
  const [status, setStatus] = useState('active');
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);

  const rootTenant = useMemo(
    () => (allTenants || []).find((t) => t.tenant_type === 'root'),
    [allTenants],
  );
  const activePartners = useMemo(
    () =>
      (allTenants || []).filter(
        (t) => t.tenant_type === 'partner' && t.status === 'active',
      ),
    [allTenants],
  );

  // Initialize / reset form when the modal opens.
  useEffect(() => {
    if (!isOpen) return;
    if (isEdit && tenant) {
      setName(tenant.name || '');
      setSlug(tenant.slug || '');
      setSlugTouched(true);
      setTenantType(tenant.tenant_type || 'partner');
      setParentTenantId(tenant.parent_tenant_id || '');
      // Map archived → active in the radio default so reactivation works
      // if someone opens edit on an archived row. The radio is only shown
      // for non-archived tenants today, but defensive fallback either way.
      setStatus(tenant.status === 'archived' ? 'active' : tenant.status || 'active');
    } else {
      setName('');
      setSlug('');
      setSlugTouched(false);
      setTenantType('partner');
      setParentTenantId(rootTenant?.tenant_id || '');
      setStatus('active');
    }
    setErrors({});
    setSubmitting(false);
  }, [isOpen, isEdit, tenant, rootTenant]);

  // Auto-derive slug from name in create mode until user manually touches slug.
  useEffect(() => {
    if (!isOpen || slugTouched || isEdit) return;
    setSlug(deriveSlug(name));
  }, [name, isOpen, slugTouched, isEdit]);

  // When type changes in create mode, snap parent to the appropriate default.
  useEffect(() => {
    if (!isOpen || isEdit) return;
    if (tenantType === 'partner') {
      setParentTenantId(rootTenant?.tenant_id || '');
    } else {
      setParentTenantId('');
    }
  }, [tenantType, isOpen, isEdit, rootTenant]);

  // ESC to close (when not submitting).
  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e) => {
      if (e.key === 'Escape' && !submitting) onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose, submitting]);

  const validate = useCallback(() => {
    const errs = {};
    if (!name.trim()) errs.name = 'Name is required.';
    if (!slug.trim()) errs.slug = 'Slug is required.';
    else if (!isValidSlug(slug))
      errs.slug = 'Lowercase, alphanumeric and hyphens, 1–64 characters.';
    if (!isEdit && !tenantType) errs.tenantType = 'Type is required.';
    if (!isEdit && tenantType === 'partner' && !parentTenantId)
      errs.parentTenantId = 'Root tenant not loaded — refresh and try again.';
    return errs;
  }, [name, slug, isEdit, tenantType, parentTenantId]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    const validationErrors = validate();
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      return;
    }
    setSubmitting(true);
    setErrors({});

    try {
      let res;
      if (isEdit) {
        // Build PATCH body with diff only — avoids sending unchanged fields
        // and surfaces "no changes" as a friendly info toast instead of a
        // pointless API round-trip.
        const patch = {};
        if (name !== original.name) patch.name = name;
        if (slug !== original.slug) patch.slug = slug;
        if (parentTenantId !== (original.parent_tenant_id || '')) {
          patch.parent_tenant_id = parentTenantId || null;
        }
        if (original.status !== 'archived' && status !== original.status) {
          patch.status = status;
        }
        if (Object.keys(patch).length === 0) {
          toast.info('No changes to save.');
          setSubmitting(false);
          return;
        }
        res = await updateTenant(original.tenant_id, patch);
      } else {
        const body = {
          name: name.trim(),
          slug: slug.trim(),
          tenant_type: tenantType,
          parent_tenant_id:
            tenantType === 'partner'
              ? rootTenant?.tenant_id || null
              : parentTenantId || null,
        };
        res = await createTenant(body);
      }

      if (res.ok) {
        const data = await res.json();
        toast.success(isEdit ? 'Tenant updated.' : 'Tenant created.');
        onSuccess(data);
        return;
      }

      const body = await res.json().catch(() => ({}));
      const detail = body?.detail;
      if (res.status === 409) {
        // 409s for tenants are most commonly slug collisions; surface inline
        // when the message confirms it, fall back to global error otherwise.
        if (detail && /slug/i.test(String(detail))) {
          setErrors({ slug: 'A tenant with this slug already exists.' });
        } else {
          setErrors({
            _global: detail || 'Conflict: a tenant with these values already exists.',
          });
        }
      } else if (res.status === 400 || res.status === 422) {
        setErrors({ _global: detail || 'Some values are invalid.' });
      } else if (res.status === 403) {
        setErrors({ _global: "You don't have permission to perform this action." });
      } else {
        setErrors({ _global: detail || `Request failed (HTTP ${res.status}).` });
      }
    } catch (err) {
      setErrors({ _global: 'Network error. Please try again.' });
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-labelledby="tenant-form-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-lg p-6 animate-fadeInUp max-h-[90vh] overflow-y-auto"
        data-testid="tenant-form-modal"
      >
        {/* Header */}
        <div className="flex items-start justify-between mb-5">
          <div>
            <h2
              id="tenant-form-modal-title"
              className="text-lg font-bold text-aegisaxiom-text"
            >
              {isEdit ? `Edit ${original?.name || 'tenant'}` : 'Create tenant'}
            </h2>
            <p className="text-xs text-aegisaxiom-text-secondary mt-1">
              {isEdit
                ? 'Update name, slug, parent, or status. Archive uses a separate action.'
                : 'Add a new partner or account to the SimpliMeta hierarchy.'}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text disabled:opacity-50"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Global error */}
        {errors._global && (
          <div className="bg-red-50 dark:bg-red-600/10 border border-red-200 dark:border-red-600/20 rounded-lg p-3 mb-4 flex items-start gap-2">
            <AlertCircle
              size={14}
              className="text-red-600 dark:text-red-400 shrink-0 mt-0.5"
            />
            <p className="text-xs text-red-700 dark:text-red-300 leading-relaxed">
              {errors._global}
            </p>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name */}
          <div>
            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Name *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={submitting}
              className="input-field"
              placeholder="e.g. Acme Industries"
              data-testid="tenant-form-name"
              autoFocus
              maxLength={120}
            />
            {errors.name && (
              <p className="text-xs text-red-600 dark:text-red-400 mt-1">{errors.name}</p>
            )}
          </div>

          {/* Slug */}
          <div>
            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Slug *</label>
            <input
              type="text"
              value={slug}
              onChange={(e) => {
                setSlug(e.target.value);
                setSlugTouched(true);
              }}
              disabled={submitting}
              className="input-field font-mono"
              placeholder="e.g. acme-industries"
              data-testid="tenant-form-slug"
              maxLength={64}
            />
            {errors.slug ? (
              <p className="text-xs text-red-600 dark:text-red-400 mt-1">{errors.slug}</p>
            ) : (
              <p className="text-xs text-aegisaxiom-text-muted mt-1">
                Lowercase, alphanumeric and hyphens, 1–64 characters. Used in URLs.
              </p>
            )}
          </div>

          {/* Type — create mode */}
          {!isEdit && (
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">Type *</label>
              <div className="space-y-2">
                {TYPES.map((t) => {
                  const Icon = t.icon;
                  const isActive = tenantType === t.id;
                  return (
                    <label
                      key={t.id}
                      className={
                        'flex items-start gap-2.5 p-3 rounded-lg border cursor-pointer transition ' +
                        (isActive
                          ? 'border-violet-400 dark:border-violet-500/50 bg-violet-50 dark:bg-violet-600/10'
                          : 'border-aegisaxiom-border hover:border-aegisaxiom-border-hover')
                      }
                    >
                      <input
                        type="radio"
                        name="tenantType"
                        value={t.id}
                        checked={isActive}
                        onChange={() => setTenantType(t.id)}
                        disabled={submitting}
                        className="mt-1 accent-violet-600"
                        data-testid={`tenant-form-type-${t.id}`}
                      />
                      <div className="flex-1">
                        <div className="flex items-center gap-1.5 text-sm font-medium text-aegisaxiom-text">
                          <Icon size={14} />
                          {t.label}
                        </div>
                        <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">
                          {t.description}
                        </p>
                      </div>
                    </label>
                  );
                })}
              </div>
              {errors.tenantType && (
                <p className="text-xs text-red-600 dark:text-red-400 mt-1">
                  {errors.tenantType}
                </p>
              )}
            </div>
          )}

          {/* Type — edit mode (read-only) */}
          {isEdit && (
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Type</label>
              <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border text-sm">
                {original?.tenant_type === 'partner' ? (
                  <Briefcase size={14} className="text-aegisaxiom-text-secondary" />
                ) : (
                  <Building2 size={14} className="text-aegisaxiom-text-secondary" />
                )}
                <span className="text-aegisaxiom-text">
                  {original?.tenant_type === 'partner' ? 'Partner' : 'Account'}
                </span>
                <span className="text-aegisaxiom-text-muted text-xs ml-auto">
                  cannot be changed
                </span>
              </div>
            </div>
          )}

          {/* Parent — partner (fixed to root) */}
          {tenantType === 'partner' && (
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                Parent tenant
              </label>
              <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border text-sm">
                <span className="text-aegisaxiom-text">
                  {rootTenant?.name || 'SimpliMeta'} (root)
                </span>
                <span className="text-aegisaxiom-text-muted text-xs ml-auto">fixed</span>
              </div>
              <p className="text-xs text-aegisaxiom-text-muted mt-1">
                Partners always sit directly under the root tenant.
              </p>
            </div>
          )}

          {/* Parent — account (dropdown) */}
          {tenantType === 'account' && (
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                Parent tenant
              </label>
              <select
                value={parentTenantId}
                onChange={(e) => setParentTenantId(e.target.value)}
                disabled={submitting}
                className="input-field"
                data-testid="tenant-form-parent"
              >
                <option value="">— Direct customer (no parent) —</option>
                {activePartners.map((p) => (
                  <option key={p.tenant_id} value={p.tenant_id}>
                    {p.name} ({p.slug})
                  </option>
                ))}
              </select>
              {errors.parentTenantId && (
                <p className="text-xs text-red-600 dark:text-red-400 mt-1">
                  {errors.parentTenantId}
                </p>
              )}
              <p className="text-xs text-aegisaxiom-text-muted mt-1">
                Leave empty for a direct customer, or pick a partner to nest this
                account beneath them.
              </p>
            </div>
          )}

          {/* Status — edit mode, non-archived only */}
          {isEdit && original?.status !== 'archived' && (
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block">Status</label>
              <div className="space-y-2">
                {STATUSES.map((s) => {
                  const isActive = status === s.id;
                  return (
                    <label
                      key={s.id}
                      className={
                        'flex items-start gap-2.5 p-3 rounded-lg border cursor-pointer transition ' +
                        (isActive
                          ? 'border-violet-400 dark:border-violet-500/50 bg-violet-50 dark:bg-violet-600/10'
                          : 'border-aegisaxiom-border hover:border-aegisaxiom-border-hover')
                      }
                    >
                      <input
                        type="radio"
                        name="status"
                        value={s.id}
                        checked={isActive}
                        onChange={() => setStatus(s.id)}
                        disabled={submitting}
                        className="mt-1 accent-violet-600"
                        data-testid={`tenant-form-status-${s.id}`}
                      />
                      <div className="flex-1">
                        <div className="text-sm font-medium text-aegisaxiom-text">{s.label}</div>
                        <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">
                          {s.description}
                        </p>
                      </div>
                    </label>
                  );
                })}
              </div>
            </div>
          )}

          {/* Footer */}
          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="btn-secondary disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
              data-testid="tenant-form-submit"
            >
              {submitting
                ? isEdit
                  ? 'Saving…'
                  : 'Creating…'
                : isEdit
                  ? 'Save changes'
                  : 'Create tenant'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
