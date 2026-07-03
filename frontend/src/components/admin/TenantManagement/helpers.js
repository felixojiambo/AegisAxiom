/**
 * Shared helpers for the Phase 1D tenant admin UI.
 *
 * Pure functions and config constants only — no React, no hooks.
 * Safe to import from anywhere in the TenantManagement folder.
 */

import { Crown, Briefcase, Building2 } from 'lucide-react';

// Tenant slug must match the backend regex in routes/tenants.py:
//   ^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$
// Lower-case alphanumeric or hyphens, 1-64 chars, no leading/trailing hyphen.
const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;

/** True if the given string is a valid tenant slug per the backend regex. */
export function isValidSlug(slug) {
  if (typeof slug !== 'string') return false;
  return SLUG_RE.test(slug);
}

/** Derive a slug from a tenant name on first keystroke. Lossy but useful. */
export function deriveSlug(name) {
  return (name || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64);
}

// Tenant type display config. Used by the type icon + label rendering.
export const TENANT_TYPE_CONFIG = {
  root: {
    label: 'Root',
    icon: Crown,
    pillClass:
      'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300',
  },
  partner: {
    label: 'Partner',
    icon: Briefcase,
    pillClass:
      'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300',
  },
  account: {
    label: 'Account',
    icon: Building2,
    pillClass:
      'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300',
  },
};

// Status pill class mapping. Tenants use active/suspended/archived;
// assignments use active/suspended/revoked. Both share active/suspended.
export const STATUS_PILL_CLASS = {
  active:
    'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300',
  suspended:
    'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300',
  archived:
    'bg-slate-50 dark:bg-slate-600/20 text-slate-600 dark:text-slate-300',
  revoked:
    'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300',
};

/** Capitalize a status string for display. "active" → "Active". */
export function statusLabel(status) {
  if (!status) return '';
  return status.charAt(0).toUpperCase() + status.slice(1);
}

/**
 * Format a max_instances value for display.
 * null / undefined → "∞" (unlimited)
 * number → the number as a string
 */
export function formatMaxInstances(max) {
  if (max === null || max === undefined) return '∞';
  return String(max);
}

/**
 * Build an ancestry chain (leaf → root) from a tenant + the full list
 * of tenants. Walks parent_tenant_id pointers, capped at 10 levels to
 * defend against any misconfigured cycle.
 */
export function buildAncestry(tenant, allTenants) {
  if (!tenant) return [];
  const byId = new Map((allTenants || []).map((t) => [t.tenant_id, t]));
  const chain = [];
  const seen = new Set();
  let cursor = tenant;
  for (let i = 0; i < 10 && cursor && !seen.has(cursor.tenant_id); i++) {
    seen.add(cursor.tenant_id);
    chain.push(cursor);
    cursor = byId.get(cursor.parent_tenant_id || '');
  }
  return chain;
}
