/**
 * ContractApprovalAccess — admin audit + override surface for the
 * tenant-wide `contracts.approve` permission.
 *
 * What this page is for:
 *   Answer "who in this tenant can approve contracts, and why?"
 *   Two grant paths exist — the user's role carries the permission,
 *   or the user has been granted a direct override on top of their
 *   role. The page surfaces both and lets admins manage only the
 *   override path. Role-based access is shown for visibility but
 *   has to be changed through Roles & Permissions.
 *
 * What this page is NOT:
 *   Per-contract approver assignment. The backend persistence model
 *   is tenant-scoped — there is no per-contract approver collection.
 *   That capability would require a separate backend change and is
 *   not in scope here.
 *
 * Data flow:
 *   GET /api/admin/contract-approval-access?search=&approval_filter=
 *       &page=&page_size=
 *     -> { items, page, page_size, total }
 *   PUT /api/admin/users/{user_id} with { extra_permissions: [...] }
 *     -> grant/revoke the override only.
 *
 * The PUT route already preserves every non-`contracts.approve`
 * entry in extra_permissions verbatim (see saveOverride below),
 * so this page never disturbs unrelated grants.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { toast } from 'sonner';
import {
  Shield,
  RefreshCw,
  AlertTriangle,
  Search,
  Info,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';

const API = API_URL;

const SEARCH_DEBOUNCE_MS = 250;
const APPROVER_OVERRIDE_KEY = 'contracts.approve';
const PAGE_SIZE_OPTIONS = [25, 50, 100];
const DEFAULT_PAGE_SIZE = 50;

const APPROVAL_FILTER_OPTIONS = [
  { value: 'all', label: 'All users' },
  { value: 'can_approve', label: 'Can approve' },
  { value: 'role', label: 'Role-based' },
  { value: 'override', label: 'Override' },
  { value: 'none', label: 'No approval access' },
];

function SourceBadge({ source }) {
  const base =
    'inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium border';
  if (source === 'role') {
    return (
      <span
        className={`${base} text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-600/20 border-emerald-300/40`}
        data-testid="contract-approval-access-source-role"
      >
        Role
      </span>
    );
  }
  if (source === 'override') {
    return (
      <span
        className={`${base} text-violet-700 dark:text-violet-300 bg-violet-50 dark:bg-violet-600/20 border-violet-300/40`}
        data-testid="contract-approval-access-source-override"
      >
        Override
      </span>
    );
  }
  if (source === 'role_and_override') {
    return (
      <span
        className={`${base} text-sky-700 dark:text-sky-300 bg-sky-50 dark:bg-sky-600/20 border-sky-300/40`}
        data-testid="contract-approval-access-source-role-and-override"
      >
        Role + Override
      </span>
    );
  }
  return (
    <span
      className={`${base} text-aegisaxiom-text-muted bg-aegisaxiom-elevated border-aegisaxiom-border`}
      data-testid="contract-approval-access-source-none"
    >
      None
    </span>
  );
}

export default function ContractApprovalAccess() {
  const { hasPermission, isAdmin } = usePermissions();
  const [searchParams, setSearchParams] = useSearchParams();

  // Viewing the page needs users.view; mutating (PUT
  // extra_permissions) needs the admin role at the backend layer.
  // Hiding the page from users who can't mutate avoids a panel of
  // dead buttons.
  const canSee =
    Boolean(hasPermission('users.view') || hasPermission('*')) && isAdmin();

  // URL-backed state — search/filter/page/page_size live in the
  // query string so refreshing the page or pasting the URL preserves
  // the view.
  const search = searchParams.get('search') || '';
  const approvalFilter = searchParams.get('approval_filter') || 'all';
  const page = Math.max(1, parseInt(searchParams.get('page') || '1', 10) || 1);
  const rawPageSize = parseInt(
    searchParams.get('page_size') || String(DEFAULT_PAGE_SIZE),
    10,
  );
  const pageSize = PAGE_SIZE_OPTIONS.includes(rawPageSize)
    ? rawPageSize
    : DEFAULT_PAGE_SIZE;

  const setQuery = useCallback(
    (patch) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(patch)) {
            if (v === '' || v === null || v === undefined) {
              next.delete(k);
            } else {
              next.set(k, String(v));
            }
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // The search input updates immediately, but the URL/fetch only
  // updates after a debounce so each keystroke doesn't fire a GET.
  const [searchDraft, setSearchDraft] = useState(search);
  useEffect(() => {
    setSearchDraft(search);
  }, [search]);
  useEffect(() => {
    if (searchDraft === search) return;
    const t = setTimeout(() => {
      setQuery({ search: searchDraft, page: 1 });
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [searchDraft, search, setQuery]);

  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const [error, setError] = useState(null);
  const [savingUserId, setSavingUserId] = useState(null);

  const requestSeqRef = useRef(0);

  const loadAccess = useCallback(async () => {
    const seq = ++requestSeqRef.current;
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({
        search,
        approval_filter: approvalFilter,
        page: String(page),
        page_size: String(pageSize),
      });
      const res = await api.get(
        `${API}/api/admin/contract-approval-access?${qs.toString()}`,
      );
      if (seq !== requestSeqRef.current) return;
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        setError(
          typeof detail?.detail === 'string'
            ? detail.detail
            : 'Could not load contract approval access. Please try again.',
        );
        setItems([]);
        setTotal(0);
        return;
      }
      const data = await res.json();
      setItems(Array.isArray(data?.items) ? data.items : []);
      setTotal(Number.isFinite(data?.total) ? data.total : 0);
    } catch (e) {
      if (seq !== requestSeqRef.current) return;
      setError(e?.message || 'Could not load contract approval access.');
      setItems([]);
      setTotal(0);
    } finally {
      if (seq === requestSeqRef.current) {
        setLoading(false);
        setHasLoadedOnce(true);
      }
    }
  }, [search, approvalFilter, page, pageSize]);

  useEffect(() => {
    if (!canSee) return;
    loadAccess();
  }, [canSee, loadAccess]);

  // Toggles ONLY the contracts.approve key on the user's
  // extra_permissions array. Every other entry is preserved verbatim.
  const saveOverride = async (user, nextGranted) => {
    if (!user?.user_id) return;
    setSavingUserId(user.user_id);
    try {
      // The list endpoint doesn't return extra_permissions, so we
      // pull the full user record first to preserve other extras.
      const userRes = await api.get(`${API}/api/admin/users`);
      if (!userRes.ok) {
        toast.error('Could not refresh user list before saving.');
        return;
      }
      const userList = await userRes.json();
      const fresh = Array.isArray(userList)
        ? userList.find((u) => u.user_id === user.user_id)
        : null;
      const current = Array.isArray(fresh?.extra_permissions)
        ? fresh.extra_permissions
        : [];
      const nextExtras = nextGranted
        ? current.includes(APPROVER_OVERRIDE_KEY)
          ? current
          : [...current, APPROVER_OVERRIDE_KEY]
        : current.filter((p) => p !== APPROVER_OVERRIDE_KEY);

      if (
        nextExtras.length === current.length
        && nextExtras.every((p, i) => p === current[i])
      ) {
        return;
      }

      const res = await api.put(`${API}/api/admin/users/${user.user_id}`, {
        extra_permissions: nextExtras,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        toast.error(
          typeof detail?.detail === 'string'
            ? detail.detail
            : 'Could not update contract approval access.',
        );
        return;
      }
      toast.success(
        nextGranted
          ? `${user.email || user.name || user.user_id}: granted override`
          : `${user.email || user.name || user.user_id}: revoked override`,
      );
      await loadAccess();
    } catch {
      toast.error('Could not update contract approval access.');
    } finally {
      setSavingUserId(null);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const showingStart = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const showingEnd = Math.min(total, page * pageSize);

  const emptyMessage = useMemo(() => {
    if (search.trim()) return 'No users match your search.';
    if (approvalFilter === 'override') {
      return 'No users have direct contract approval overrides.';
    }
    if (approvalFilter === 'can_approve') {
      return 'No users currently have contract approval access.';
    }
    if (approvalFilter === 'role') {
      return 'No users have role-based contract approval access.';
    }
    if (approvalFilter === 'none') {
      return 'Every user in this tenant has some contract approval access.';
    }
    return 'No users found.';
  }, [search, approvalFilter]);

  if (!canSee) {
    return (
      <div
        className="flex items-center justify-center h-64"
        data-testid="contract-approval-access-denied"
      >
        <p className="text-aegisaxiom-text-muted">
          You do not have permission to manage contract approval access.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-5 p-6" data-testid="contract-approval-access-page">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <Shield className="w-6 h-6 text-violet-500" />
          <div>
            <h1 className="text-xl font-semibold text-aegisaxiom-text">
              Contract Approval Access
            </h1>
            <p className="text-xs text-aegisaxiom-text-secondary">
              Who can approve contracts in this tenant, and why.
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={loadAccess}
          disabled={loading}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded border border-aegisaxiom-border hover:bg-aegisaxiom-elevated text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text disabled:opacity-60"
          data-testid="contract-approval-access-refresh"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Friendly explanation */}
      <div
        className="flex items-start gap-2 p-3 bg-violet-50/30 dark:bg-violet-600/10 border border-violet-300/30 rounded text-[12px] text-aegisaxiom-text-secondary"
        data-testid="contract-approval-access-explainer"
      >
        <Info className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 text-violet-500" />
        <div className="space-y-1">
          <p>
            Manage who can approve contracts across this tenant. Users whose
            role already includes contract approval are shown for visibility,
            but their access must be changed through role management. Overrides
            grant approval access directly to a user without changing their
            role.
          </p>
          <p className="text-[11px] text-aegisaxiom-text-muted">
            Approval access here is tenant-wide. Per-contract approver
            assignment is not currently supported.
          </p>
          <p className="text-[11px] text-aegisaxiom-text-muted">
            Manage roles and base permissions in{' '}
            <Link
              to="/admin/roles"
              className="text-violet-500 hover:text-violet-400 underline"
              data-testid="contract-approval-access-roles-link"
            >
              Roles &amp; Permissions
            </Link>
            .
          </p>
        </div>
      </div>

      {/* Filter bar */}
      <div
        className="flex flex-wrap items-center gap-3"
        data-testid="contract-approval-access-filter-bar"
      >
        <div className="relative flex-1 min-w-[240px] max-w-md">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-aegisaxiom-text-muted" />
          <input
            type="text"
            value={searchDraft}
            onChange={(e) => setSearchDraft(e.target.value)}
            placeholder="Search name, email, or role…"
            className="aegisaxiom-input w-full text-sm pl-7"
            data-testid="contract-approval-access-search"
          />
        </div>
        <select
          value={approvalFilter}
          onChange={(e) => setQuery({ approval_filter: e.target.value, page: 1 })}
          className="aegisaxiom-input text-sm py-1.5"
          data-testid="contract-approval-access-filter"
        >
          {APPROVAL_FILTER_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <select
          value={pageSize}
          onChange={(e) =>
            setQuery({ page_size: parseInt(e.target.value, 10), page: 1 })
          }
          className="aegisaxiom-input text-sm py-1.5"
          data-testid="contract-approval-access-page-size"
        >
          {PAGE_SIZE_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {opt} per page
            </option>
          ))}
        </select>
      </div>

      {error && (
        <div
          className="flex items-center justify-between gap-2 p-3 glass-card text-red-600 dark:text-red-400 text-sm"
          data-testid="contract-approval-access-error"
        >
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" />
            <span>{error}</span>
          </div>
          <button
            type="button"
            onClick={loadAccess}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded border border-red-500/30 hover:bg-red-500/10 text-xs disabled:opacity-60"
            data-testid="contract-approval-access-retry"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
            Retry
          </button>
        </div>
      )}

      {/* Table */}
      <div className="glass-card overflow-hidden">
        {!hasLoadedOnce && loading ? (
          <p
            className="text-xs text-aegisaxiom-text-muted text-center py-8"
            data-testid="contract-approval-access-loading"
          >
            Loading…
          </p>
        ) : items.length === 0 ? (
          <p
            className="text-xs text-aegisaxiom-text-muted text-center py-8"
            data-testid="contract-approval-access-empty"
          >
            {emptyMessage}
          </p>
        ) : (
          <table className="w-full text-sm" data-testid="contract-approval-access-table">
            <thead>
              <tr className="border-b border-aegisaxiom-border text-left text-aegisaxiom-text-muted text-[11px] uppercase tracking-wide">
                <th className="px-4 py-2 font-medium">User</th>
                <th className="px-4 py-2 font-medium">Email</th>
                <th className="px-4 py-2 font-medium">Role</th>
                <th className="px-4 py-2 font-medium">Approval source</th>
                <th className="px-4 py-2 font-medium text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {items.map((row) => (
                <AccessRow
                  key={row.user_id}
                  row={row}
                  saving={savingUserId === row.user_id}
                  onGrant={() => saveOverride(row, true)}
                  onRevoke={() => saveOverride(row, false)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination footer */}
      {items.length > 0 && (
        <div
          className="flex items-center justify-between gap-3 flex-wrap text-xs text-aegisaxiom-text-secondary"
          data-testid="contract-approval-access-pagination"
        >
          <span data-testid="contract-approval-access-range">
            Showing {showingStart}–{showingEnd} of {total}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setQuery({ page: page - 1 })}
              disabled={page <= 1 || loading}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded border border-aegisaxiom-border hover:bg-aegisaxiom-elevated disabled:opacity-40"
              data-testid="contract-approval-access-prev"
            >
              <ChevronLeft className="w-3 h-3" />
              Previous
            </button>
            <span className="px-1">
              Page {page} of {totalPages}
            </span>
            <button
              type="button"
              onClick={() => setQuery({ page: page + 1 })}
              disabled={page >= totalPages || loading}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded border border-aegisaxiom-border hover:bg-aegisaxiom-elevated disabled:opacity-40"
              data-testid="contract-approval-access-next"
            >
              Next
              <ChevronRight className="w-3 h-3" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function AccessRow({ row, saving, onGrant, onRevoke }) {
  const { approval_source: source, has_override_permission, user_id, name, email, role } = row;
  const showRevoke = has_override_permission;
  const showGrant = source === 'none';
  const managedByRole = source === 'role';

  return (
    <tr
      className="border-b border-aegisaxiom-border/40 hover:bg-aegisaxiom-surface/40 transition-colors"
      data-testid={`contract-approval-access-row-${user_id}`}
    >
      <td className="px-4 py-2.5 text-aegisaxiom-text">{name || '—'}</td>
      <td className="px-4 py-2.5 text-aegisaxiom-text-secondary text-xs">{email || '—'}</td>
      <td className="px-4 py-2.5 text-aegisaxiom-text-secondary text-xs">{role || '—'}</td>
      <td className="px-4 py-2.5">
        <SourceBadge source={source} />
      </td>
      <td className="px-4 py-2.5 text-right">
        {managedByRole && (
          <span
            className="text-[11px] text-aegisaxiom-text-muted"
            data-testid={`contract-approval-access-managed-${user_id}`}
            title="This user can approve contracts because their role includes contract approval. Change their role or role permissions to remove access."
          >
            Managed by role
          </span>
        )}
        {showRevoke && (
          <div className="inline-flex flex-col items-end gap-0.5">
            <button
              type="button"
              onClick={onRevoke}
              disabled={saving}
              className="px-2.5 py-1 text-xs rounded bg-red-600 hover:bg-red-500 text-white disabled:opacity-50"
              data-testid={`contract-approval-access-revoke-${user_id}`}
            >
              {saving ? 'Saving…' : 'Revoke override'}
            </button>
            {source === 'role_and_override' && (
              <span className="text-[10px] text-aegisaxiom-text-muted max-w-[220px] text-right">
                This will remove only the direct override. The user may still
                be able to approve contracts through their role.
              </span>
            )}
          </div>
        )}
        {showGrant && (
          <button
            type="button"
            onClick={onGrant}
            disabled={saving}
            className="px-2.5 py-1 text-xs rounded bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-50"
            data-testid={`contract-approval-access-grant-${user_id}`}
          >
            {saving ? 'Saving…' : 'Grant override'}
          </button>
        )}
      </td>
    </tr>
  );
}
