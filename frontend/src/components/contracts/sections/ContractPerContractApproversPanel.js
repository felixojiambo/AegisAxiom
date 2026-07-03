/**
 * ContractPerContractApproversPanel — manage per-contract approver
 * overrides on the contract detail surface.
 *
 * Distinct from the GLOBAL /admin/contract-approvers page, which
 * grants `contracts.approve` tenant-wide. This panel grants
 * approval authority for ONE specific contract only. The
 * persistence shape is the `contract_approver_overrides`
 * collection (see
 * backend/services/contract_per_contract_overrides.py).
 *
 * Backend routes consumed (no inventions):
 *   GET    /api/contracts/{id}/approver-overrides
 *   POST   /api/contracts/{id}/approver-overrides
 *           body: { user_id: str, note?: str }
 *   DELETE /api/contracts/{id}/approver-overrides/{user_id}
 *           body (optional): { revoke_note?: str }
 *
 * Permission gate
 * ---------------
 * Management requires users.edit AND the admin role (mirrors
 * routes/admin.py:288 and the global override page). Normal
 * contract editors cannot grant approval authority casually.
 * The page also surfaces to global contracts.approve users in
 * read mode — they need to know who else can decide on a
 * contract they may also be deciding on. Non-admin viewers
 * see the panel as informational only (no grant/revoke buttons).
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import {
  ShieldCheck,
  ShieldOff,
  RefreshCw,
  AlertTriangle,
  Info,
  Plus,
} from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';

const API = API_URL;

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch (_) {
    return String(iso);
  }
}

function StateBadge({ state }) {
  const cls =
    state === 'active'
      ? 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border border-emerald-300/40'
      : 'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border border-aegisaxiom-border';
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium ${cls}`}
      data-testid={`contract-per-contract-approvers-state-${state}`}
    >
      {state || 'unknown'}
    </span>
  );
}

export default function ContractPerContractApproversPanel({ contract }) {
  const { hasPermission, isAdmin } = usePermissions();

  const contractId = contract?.contract_id;

  // Backend gates the list endpoint on either contracts.approve OR
  // the manage-permission union. Mirror that here to decide whether
  // the panel is visible at all.
  const canManage =
    (hasPermission('users.edit') && isAdmin)
    || hasPermission('*');
  const canSee =
    canManage
    || hasPermission('contracts.approve')
    || hasPermission('*');

  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Grant form state — kept inline rather than as a separate modal
  // to match the existing per-bullet / per-trigger inline-create
  // pattern in the other contract-detail panels.
  const [showGrantForm, setShowGrantForm] = useState(false);
  const [grantUserId, setGrantUserId] = useState('');
  const [grantNote, setGrantNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [revokingUserId, setRevokingUserId] = useState(null);

  const loadOverrides = useCallback(async () => {
    if (!contractId || !canSee) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.get(
        `${API}/api/contracts/${contractId}/approver-overrides`,
      );
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        setError(
          typeof detail?.detail === 'string'
            ? detail.detail
            : `Failed to load overrides (HTTP ${res.status}).`,
        );
        setItems([]);
        return;
      }
      const data = await res.json();
      setItems(Array.isArray(data?.items) ? data.items : []);
    } catch (e) {
      setError(e?.message || 'Failed to load overrides.');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [contractId, canSee]);

  useEffect(() => {
    loadOverrides();
  }, [loadOverrides]);

  const grant = async (e) => {
    e?.preventDefault?.();
    if (!contractId) return;
    const trimmedId = grantUserId.trim();
    if (!trimmedId) {
      toast.error('User ID is required');
      return;
    }
    setSubmitting(true);
    try {
      const body = { user_id: trimmedId };
      const note = grantNote.trim();
      if (note) body.note = note;
      const res = await api.post(
        `${API}/api/contracts/${contractId}/approver-overrides`,
        body,
      );
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        toast.error(
          typeof detail?.detail === 'string'
            ? detail.detail
            : 'Failed to grant per-contract override',
        );
        return;
      }
      toast.success(`Granted per-contract approval to ${trimmedId}`);
      setShowGrantForm(false);
      setGrantUserId('');
      setGrantNote('');
      loadOverrides();
    } catch (err) {
      toast.error('Failed to grant per-contract override');
    } finally {
      setSubmitting(false);
    }
  };

  const revoke = async (userId) => {
    if (!contractId || !userId) return;
    setRevokingUserId(userId);
    try {
      const res = await api.delete(
        `${API}/api/contracts/${contractId}/approver-overrides/${userId}`,
      );
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        toast.error(
          typeof detail?.detail === 'string'
            ? detail.detail
            : 'Failed to revoke per-contract override',
        );
        return;
      }
      toast.success(`Revoked per-contract approval for ${userId}`);
      loadOverrides();
    } catch (err) {
      toast.error('Failed to revoke per-contract override');
    } finally {
      setRevokingUserId(null);
    }
  };

  // Derive active / revoked partitions BEFORE the visibility
  // early-return so the hook list stays unconditional. The Rules
  // of Hooks require every render to call the same hooks in the
  // same order; placing useMemo after `if (!canSee) return null`
  // skipped these calls for hidden viewers and tripped the
  // react-hooks/rules-of-hooks lint as a hard compile error.
  const activeOverrides = useMemo(
    () => items.filter((o) => o.state === 'active'),
    [items],
  );
  const revokedOverrides = useMemo(
    () => items.filter((o) => o.state !== 'active'),
    [items],
  );

  // Hide the panel entirely for users who can neither manage nor
  // observe overrides. Those users have no business knowing who
  // can approve this contract.
  if (!canSee || !contractId) return null;

  return (
    <div
      className="glass-card p-4"
      data-testid="contract-per-contract-approvers-panel"
    >
      <div className="flex items-center justify-between gap-2 flex-wrap mb-3">
        <h3 className="text-sm font-semibold text-aegisaxiom-text flex items-center gap-2">
          <ShieldCheck size={16} className="text-violet-500" />
          Contract-specific approvers
          {activeOverrides.length > 0 && (
            <span className="text-[11px] font-normal text-aegisaxiom-text-muted">
              ({activeOverrides.length} active)
            </span>
          )}
        </h3>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            onClick={loadOverrides}
            className="inline-flex items-center gap-1 px-2 py-1 rounded border border-aegisaxiom-border hover:bg-aegisaxiom-elevated text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
            data-testid="contract-per-contract-approvers-refresh"
          >
            <RefreshCw className="w-3 h-3" />
            Refresh
          </button>
          {canManage && (
            <button
              type="button"
              onClick={() => {
                setShowGrantForm((v) => !v);
                setGrantUserId('');
                setGrantNote('');
              }}
              className="inline-flex items-center gap-1 px-2 py-1 rounded bg-violet-600 hover:bg-violet-500 text-white text-xs"
              data-testid="contract-per-contract-approvers-toggle-form"
            >
              <Plus className="w-3 h-3" />
              {showGrantForm ? 'Cancel' : 'Grant approval'}
            </button>
          )}
        </div>
      </div>

      <div
        className="flex items-start gap-2 p-2 mb-3 bg-violet-50/30 dark:bg-violet-600/10 border border-violet-300/30 rounded text-[11px] text-aegisaxiom-text-secondary"
        data-testid="contract-per-contract-approvers-notice"
      >
        <Info className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 text-violet-500" />
        <div>
          Grants apply to <strong className="text-aegisaxiom-text">this contract only</strong>.
          For tenant-wide approval authority, use the{' '}
          <strong className="text-aegisaxiom-text">Contract Approval Access</strong>{' '}
          admin page. A user can carry both — the per-contract grant unlocks
          approve / reject / request-changes on this one contract without
          changing the user&apos;s global permissions.
        </div>
      </div>

      {error && (
        <div
          className="flex items-center gap-2 p-2 mb-3 bg-red-50 dark:bg-red-600/10 border border-red-300/40 rounded text-red-700 dark:text-red-300 text-xs"
          data-testid="contract-per-contract-approvers-error"
        >
          <AlertTriangle className="w-3.5 h-3.5" />
          <span>{error}</span>
        </div>
      )}

      {showGrantForm && canManage && (
        <form
          onSubmit={grant}
          className="bg-aegisaxiom-elevated/40 border border-aegisaxiom-border rounded p-3 mb-3 space-y-2"
          data-testid="contract-per-contract-approvers-form"
        >
          <div>
            <label className="block text-[11px] text-aegisaxiom-text-secondary mb-0.5">
              User ID *
            </label>
            <input
              type="text"
              value={grantUserId}
              onChange={(e) => setGrantUserId(e.target.value)}
              placeholder="user_…"
              className="aegisaxiom-input w-full text-xs font-mono"
              data-testid="contract-per-contract-approvers-user-id"
              autoFocus
            />
            <p className="text-[10px] text-aegisaxiom-text-muted mt-0.5">
              The user must already exist in this tenant. Cross-tenant
              grants are refused server-side.
            </p>
          </div>
          <div>
            <label className="block text-[11px] text-aegisaxiom-text-secondary mb-0.5">
              Note (optional)
            </label>
            <input
              type="text"
              value={grantNote}
              onChange={(e) => setGrantNote(e.target.value)}
              placeholder="e.g. covering for director during PTO"
              className="aegisaxiom-input w-full text-xs"
              data-testid="contract-per-contract-approvers-note"
            />
          </div>
          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={() => {
                setShowGrantForm(false);
                setGrantUserId('');
                setGrantNote('');
              }}
              className="px-3 py-1.5 text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || !grantUserId.trim()}
              className="px-3 py-1.5 text-xs rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white"
              data-testid="contract-per-contract-approvers-submit"
            >
              {submitting ? 'Granting…' : 'Grant approval'}
            </button>
          </div>
        </form>
      )}

      <div className="space-y-2">
        {loading ? (
          <p
            className="text-xs text-aegisaxiom-text-muted text-center py-4"
            data-testid="contract-per-contract-approvers-loading"
          >
            Loading…
          </p>
        ) : activeOverrides.length === 0 && revokedOverrides.length === 0 ? (
          <p
            className="text-xs text-aegisaxiom-text-muted text-center py-4"
            data-testid="contract-per-contract-approvers-empty"
          >
            No contract-specific approvers on this contract. Approval
            requires either a tenant-wide{' '}
            <code>contracts.approve</code> grant or a per-contract
            override granted here.
          </p>
        ) : (
          <>
            {activeOverrides.length > 0 && (
              <div className="space-y-2">
                <div className="text-[10px] uppercase tracking-wide text-aegisaxiom-text-muted">
                  Active
                </div>
                {activeOverrides.map((o) => (
                  <OverrideRow
                    key={o.override_id}
                    override={o}
                    canManage={canManage}
                    onRevoke={() => revoke(o.user_id)}
                    revoking={revokingUserId === o.user_id}
                  />
                ))}
              </div>
            )}
            {revokedOverrides.length > 0 && (
              <div className="space-y-2 mt-3 pt-3 border-t border-aegisaxiom-border">
                <div className="text-[10px] uppercase tracking-wide text-aegisaxiom-text-muted">
                  Revoked history
                </div>
                {revokedOverrides.map((o) => (
                  <OverrideRow
                    key={o.override_id}
                    override={o}
                    canManage={false}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function OverrideRow({ override, canManage, onRevoke, revoking }) {
  const active = override.state === 'active';
  return (
    <div
      className="bg-aegisaxiom-elevated/30 border border-aegisaxiom-border rounded p-2.5"
      data-testid={`contract-per-contract-approvers-row-${override.override_id}`}
    >
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap min-w-0">
          <StateBadge state={override.state} />
          <span className="text-xs font-mono text-aegisaxiom-text truncate">
            {override.user_id}
          </span>
        </div>
        {canManage && active && (
          <button
            type="button"
            onClick={onRevoke}
            disabled={revoking}
            className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white"
            data-testid={`contract-per-contract-approvers-revoke-${override.user_id}`}
            title="Revoke this user's approval authority for this contract"
          >
            <ShieldOff className="w-3 h-3" />
            {revoking ? 'Revoking…' : 'Revoke'}
          </button>
        )}
      </div>
      {override.note && (
        <p className="text-[11px] text-aegisaxiom-text-secondary mt-1 whitespace-pre-wrap">
          {override.note}
        </p>
      )}
      <div className="text-[10px] text-aegisaxiom-text-muted mt-1 space-y-0.5">
        <div>
          Granted by{' '}
          <span className="font-mono text-aegisaxiom-text-secondary">{override.granted_by}</span>{' '}
          on {fmtDate(override.granted_at)}
        </div>
        {!active && override.revoked_at && (
          <div>
            Revoked by{' '}
            <span className="font-mono text-aegisaxiom-text-secondary">
              {override.revoked_by}
            </span>{' '}
            on {fmtDate(override.revoked_at)}
            {override.revoke_note ? ` — ${override.revoke_note}` : ''}
          </div>
        )}
      </div>
    </div>
  );
}
