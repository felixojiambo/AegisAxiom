import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import {
  ArrowLeft,
  AlertCircle,
  RefreshCw,
  Building2,
  Edit,
  Archive,
  Plus,
  MoreVertical,
  Bot,
  Users,
  Package,
} from 'lucide-react';
import TenantList from './TenantList';
import AssignmentList from './AssignmentList';
import TenantUserList from './TenantUserList';
import TenantFormModal from './TenantFormModal';
import GrantAssignmentModal from './GrantAssignmentModal';
import ProvisionInstanceModal from './ProvisionInstanceModal';
import RevokeAssignmentModal from './RevokeAssignmentModal';
import RevokeConflictModal from './RevokeConflictModal';
import ReactivateAssignmentModal from './ReactivateAssignmentModal';
import InstanceListModal from './InstanceListModal';
import InviteUserModal from './InviteUserModal';
import AppGrantList from './AppGrantList';
import GrantAppModal from './GrantAppModal';
import RevokeAppGrantModal from './RevokeAppGrantModal';
import ReactivateAppGrantModal from './ReactivateAppGrantModal';
import ConfirmModal from '../../shared/ConfirmModal';
import {
  listTenants,
  listAssignments,
  listDefinitions,
  listInstancesForTenant,
  listTenantUsers,
  archiveTenant,
  listAppGrants,
  listGrantableApps,
} from './api';
import {
  STATUS_PILL_CLASS,
  TENANT_TYPE_CONFIG,
  statusLabel,
  buildAncestry,
} from './helpers';

/**
 * Phase 1D.4 — Tenant detail page.
 *
 * One page, four parallel data loads on mount:
 *   1. listTenants()           → all tenants (used to derive current,
 *                                 children, and parent-name lookup map)
 *   2. listAssignments(id)     → the assignments granted to this tenant
 *   3. listDefinitions()       → the AI Employee catalog, for joining
 *                                 to assignments by definition_id
 *   4. listInstancesForTenant  → all instances at this tenant, used to
 *                                 compute per-assignment instance counts
 *
 * Two tabs:
 *   • Assignments — uses <AssignmentList>; surfaces a "+ Grant
 *     assignment" button that toasts until 1D.5 lands the real modal.
 *   • Sub-tenants — reuses <TenantList> for visual consistency with the
 *     main /admin/tenants page. Edit / Archive on child rows route to
 *     the same TenantFormModal + ConfirmModal the current-tenant Edit
 *     button uses; the modal doesn't care which tenant it's editing.
 *
 * Edit and Archive on the current tenant itself live in the identity
 * panel header. Both hide entirely for root tenants (system-managed).
 *
 * Loads are best-effort: if any one of the four parallel calls fails
 * (definitions or instances especially), the page still renders with
 * fallback displays — instance counts show 0, unknown definitions render
 * as "Unknown definition" cells. The only fatal failures are loading
 * the tenant list (which means we don't know if this tenant exists) or
 * a confirmed 404 on the URL ID.
 */

export default function TenantDetail() {
  const { tenantId } = useParams();
  const navigate = useNavigate();

  const [allTenants, setAllTenants] = useState([]);
  const [assignments, setAssignments] = useState([]);
  const [definitions, setDefinitions] = useState([]);
  const [instances, setInstances] = useState([]);
  // 1D.8 — Users belonging to this tenant. Loaded in parallel with the
  // other four data sources; the Users tab renders from it.
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState('assignments');

  // Modal state — shared between current-tenant actions (identity panel)
  // and child-tenant actions (Sub-tenants tab via TenantList).
  const [modalState, setModalState] = useState(null);
  const [archiveTarget, setArchiveTarget] = useState(null);
  const [archiving, setArchiving] = useState(false);
  // 1D.5 — Grant assignment modal (separate from the tenant form modal
  // because it has different fields and a different API endpoint).
  const [grantModalOpen, setGrantModalOpen] = useState(false);
  // 1D.6 — Provision instance modal. The full target (assignment +
  // definition + currentCount) is captured at click time so the modal
  // can render its capacity preview and defaults panel without needing
  // to re-derive anything from props.
  const [provisionTarget, setProvisionTarget] = useState(null);
  // 1D.7 — Revoke + instance management state.
  // revokeTarget feeds the confirmation modal; revokeConflict is set
  // when the backend returns a 409 with sub-delegation blockers, and
  // feeds the conflict modal. They never coexist — one closes as the
  // other opens. instanceListTarget feeds the drill-down modal that
  // opens when an admin clicks the "X / Y instances" count.
  const [revokeTarget, setRevokeTarget] = useState(null);
  const [revokeConflict, setRevokeConflict] = useState(null);
  // 1D.9 — Reactivate (revoked → active) flow. Mirror of revokeTarget;
  // no conflict mode because reactivation doesn't trigger chain checks.
  const [reactivateTarget, setReactivateTarget] = useState(null);
  const [instanceListTarget, setInstanceListTarget] = useState(null);
  // 1D.8 — Invite user modal. Open/closed only (the target tenant is
  // implicit — it's the tenant being viewed).
  const [inviteModalOpen, setInviteModalOpen] = useState(false);

  // 1E.1 — App grants on this tenant + the grantable apps catalog.
  // Loaded in parallel with the other data sources; the Apps tab
  // renders from these.
  const [appGrants, setAppGrants] = useState([]);
  const [grantableApps, setGrantableApps] = useState([]);

  // 1E.1 — Modal state for the Apps tab. Parallel to the Assignment
  // tab's grantModalOpen / revokeTarget / reactivateTarget. No
  // conflict modal because tenant-side delegation of app grants
  // doesn't exist yet — that's deferred to 1F.
  const [grantAppModalOpen, setGrantAppModalOpen] = useState(false);
  const [appRevokeTarget, setAppRevokeTarget] = useState(null);
  const [appReactivateTarget, setAppReactivateTarget] = useState(null);

  // Kebab on current-tenant identity panel
  const [identityKebabOpen, setIdentityKebabOpen] = useState(false);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [tRes, aRes, dRes, iRes, uRes, gRes, cRes] = await Promise.all([
        listTenants(),
        listAssignments(tenantId),
        listDefinitions(),
        listInstancesForTenant(tenantId),
        listTenantUsers(tenantId),
        listAppGrants(tenantId),
        listGrantableApps(),
      ]);

      // Tenants list — the load-bearing call. If this fails we can't
      // resolve the current tenant or its parent, so surface as error.
      if (!tRes.ok) {
        const body = await tRes.json().catch(() => ({}));
        const message =
          body?.detail || `Failed to load tenant list (HTTP ${tRes.status}).`;
        setError(message);
        return;
      }
      const tData = await tRes.json();
      setAllTenants(tData?.tenants || []);

      // Assignments / definitions / instances / users / app-grants /
      // grantable-apps — best effort. If they fail, log a toast but
      // let the page render with empty/fallback data rather than
      // blocking the whole page.
      //
      // Note on response shapes: the backend list endpoints are
      // inconsistent — `/tenants` wraps as { tenants: [...] } but
      // `/ai-employee-definitions`, `/tenants/{id}/assignments`,
      // `/ai-employees`, `/tenants/{id}/users`, `/tenants/{id}/app-grants`,
      // and `/tenant-app-grants/catalog` all return bare arrays. The
      // Array.isArray guards below handle both shapes so future
      // backend normalisation doesn't silently break the page.
      if (aRes.ok) {
        const aData = await aRes.json();
        setAssignments(
          Array.isArray(aData) ? aData : aData?.assignments || [],
        );
      } else if (aRes.status !== 404) {
        toast.error('Failed to load assignments. Tab will be empty.');
      }
      if (dRes.ok) {
        const dData = await dRes.json();
        setDefinitions(
          Array.isArray(dData) ? dData : dData?.definitions || [],
        );
      } else {
        toast.error('Failed to load AI Employee catalog. Some rows may show as unknown.');
      }
      if (iRes.ok) {
        const iData = await iRes.json();
        setInstances(
          Array.isArray(iData)
            ? iData
            : iData?.ai_employees || iData?.instances || [],
        );
      } else {
        // Non-fatal; instance counts will show 0
      }
      if (uRes.ok) {
        const uData = await uRes.json();
        setUsers(Array.isArray(uData) ? uData : uData?.users || []);
      } else if (uRes.status !== 404) {
        // Non-fatal — Users tab will be empty
        toast.error('Failed to load tenant users. Tab will be empty.');
      }
      // 1E.1 — App grants on this tenant.
      if (gRes.ok) {
        const gData = await gRes.json();
        setAppGrants(Array.isArray(gData) ? gData : []);
      } else if (gRes.status !== 404) {
        // Non-fatal — Apps tab will show the empty state.
      }
      // 1E.1 — Grantable apps catalog. Used to populate the picker
      // and to join grants to app metadata in AppGrantList.
      if (cRes.ok) {
        const cData = await cRes.json();
        setGrantableApps(Array.isArray(cData) ? cData : []);
      } else {
        // Non-fatal — picker will show "no grantable apps" empty state.
      }
    } catch (e) {
      setError('Network error loading tenant data.');
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // ─── Derived data ──────────────────────────────────────────────

  const tenant = useMemo(
    () => allTenants.find((t) => t.tenant_id === tenantId) || null,
    [allTenants, tenantId],
  );

  const children = useMemo(
    () => allTenants.filter((t) => t.parent_tenant_id === tenantId),
    [allTenants, tenantId],
  );

  const parentNameById = useMemo(() => {
    const map = new Map();
    for (const t of allTenants) map.set(t.tenant_id, t.name);
    return map;
  }, [allTenants]);

  const ancestry = useMemo(
    () => buildAncestry(tenant, allTenants),
    [tenant, allTenants],
  );

  const definitionsById = useMemo(() => {
    const map = new Map();
    for (const d of definitions) map.set(d.definition_id, d);
    return map;
  }, [definitions]);

  // 1E.1 — App catalog lookup map. Used to join grants to app
  // metadata in AppGrantList. Grants may reference apps that have
  // since been archived from the catalog (status != 'active'); the
  // list component handles unknown apps with a fallback render, so
  // we don't need to backfill.
  const appsById = useMemo(() => {
    const map = new Map();
    for (const a of grantableApps) map.set(a.app_id, a);
    return map;
  }, [grantableApps]);

  const instanceCountsByAssignment = useMemo(() => {
    const map = new Map();
    for (const inst of instances) {
      const aid = inst.assignment_id;
      if (!aid) continue;
      map.set(aid, (map.get(aid) || 0) + 1);
    }
    return map;
  }, [instances]);

  const activeAssignmentCount = useMemo(
    () => assignments.filter((a) => a.status !== 'revoked').length,
    [assignments],
  );

  // 1E.1 — Active app-grant count for the tab badge.
  const activeAppGrantCount = useMemo(
    () => appGrants.filter((g) => g.status !== 'revoked').length,
    [appGrants],
  );

  const activeChildCount = useMemo(
    () => children.filter((c) => c.status !== 'archived').length,
    [children],
  );

  const isRoot = tenant?.tenant_type === 'root';

  // ─── Action handlers ───────────────────────────────────────────

  const handleEdit = useCallback((target) => {
    setIdentityKebabOpen(false);
    setModalState({ mode: 'edit', tenant: target });
  }, []);

  const handleArchive = useCallback((target) => {
    setIdentityKebabOpen(false);
    setArchiveTarget(target);
  }, []);

  const handleModalSuccess = (updated) => {
    setModalState(null);
    // If the slug or ID of the current tenant changed via edit, refresh
    // and let the page resolve. The tenant_id is stable so we just
    // reload everything to pick up new name / parent / status.
    loadAll();
  };

  const handleArchiveConfirm = async () => {
    if (!archiveTarget) return;
    setArchiving(true);
    try {
      const res = await archiveTenant(archiveTarget.tenant_id);
      if (res.ok) {
        toast.success(`${archiveTarget.name} archived.`);
        const archivedSelf = archiveTarget.tenant_id === tenantId;
        setArchiveTarget(null);
        if (archivedSelf) {
          // If they archived the page's own tenant, route back to the
          // list — the detail page for an archived tenant is still
          // valid but the user almost certainly wants to navigate away
          // after the destructive action.
          navigate('/admin/tenants');
        } else {
          loadAll();
        }
      } else {
        const body = await res.json().catch(() => ({}));
        if (res.status === 409) {
          toast.error(
            'Cannot archive — this tenant has active children. Archive them first.',
          );
        } else if (res.status === 403) {
          toast.error("You don't have permission to archive tenants.");
        } else {
          toast.error(body?.detail || `Archive failed (HTTP ${res.status}).`);
        }
        setArchiveTarget(null);
      }
    } catch (e) {
      toast.error('Network error during archive.');
      setArchiveTarget(null);
    } finally {
      setArchiving(false);
    }
  };

  const handleArchiveCancel = () => {
    if (!archiving) setArchiveTarget(null);
  };

  const handleGrantClick = () => {
    setGrantModalOpen(true);
  };

  const handleGrantSuccess = () => {
    setGrantModalOpen(false);
    loadAll();
  };

  // 1D.6 — Provision flow. The AssignmentList row hands us the
  // assignment + (possibly null) definition + currentCount so the
  // modal can render without a re-derive. We refuse to open if the
  // definition isn't present (catalog gone weird), since the modal
  // needs it for the defaults preview.
  const handleProvisionClick = (assignment, definition, currentCount) => {
    if (!definition) {
      toast.error(
        "Can't provision — this assignment's definition is missing from the catalog.",
      );
      return;
    }
    setProvisionTarget({ assignment, definition, currentCount });
  };

  const handleProvisionSuccess = () => {
    setProvisionTarget(null);
    // Reload so the instance count on the row reflects the new instance.
    loadAll();
  };

  // 1D.7 — View instances drill-down. The modal works off the page's
  // existing `instances` state filtered by assignment_id; we don't
  // re-fetch here.
  const handleViewInstancesClick = (assignment, definition) => {
    if (!definition) {
      toast.error(
        "Can't open instances — this assignment's definition is missing from the catalog.",
      );
      return;
    }
    setInstanceListTarget({ assignment, definition });
  };

  // 1D.7 — Revoke flow. Start with the confirmation modal. On 409
  // it'll hand back a conflict detail object via onConflict and we
  // pivot to the conflict modal showing the blockers.
  const handleRevokeClick = (assignment, definition, currentCount) => {
    if (!definition) {
      toast.error(
        "Can't revoke — this assignment's definition is missing from the catalog.",
      );
      return;
    }
    setRevokeTarget({ assignment, definition, currentCount });
  };

  const handleRevokeSuccess = () => {
    setRevokeTarget(null);
    setRevokeConflict(null);
    loadAll();
  };

  const handleRevokeConflict = (conflictDetail) => {
    // Capture the assignment context BEFORE closing revokeTarget,
    // since the conflict modal needs the definition name for its title.
    const definitionName = revokeTarget?.definition?.name || 'This assignment';
    setRevokeTarget(null);
    setRevokeConflict({ conflictDetail, definitionName });
  };

  // 1D.9 — Reactivate flow. Opens the confirmation modal for revoked
  // rows from the kebab. On success we just reload — there's no
  // conflict branch to handle (reactivation doesn't validate
  // sub-delegations because there can't be any on a revoked row).
  const handleReactivateClick = (assignment, definition) => {
    if (!definition) {
      toast.error(
        "Can't reactivate — this assignment's definition is missing from the catalog.",
      );
      return;
    }
    setReactivateTarget({ assignment, definition });
  };

  const handleReactivateSuccess = () => {
    setReactivateTarget(null);
    loadAll();
  };

  // 1D.7 — After deleting an instance from the drill-down, reload page
  // state so the assignment row's count re-derives. We deliberately do
  // NOT close the drill-down — the admin probably wants to delete more
  // than one. They can close it manually when done.
  const handleInstanceDeleted = () => {
    loadAll();
  };

  // 1D.8 — Invite user flow. Open the modal; on success, refresh so
  // the Users tab list reflects the new pending invite.
  const handleInviteClick = () => {
    setInviteModalOpen(true);
  };

  const handleInviteSuccess = () => {
    setInviteModalOpen(false);
    loadAll();
  };

  // ─── 1E.1 App-grant handlers ───────────────────────────────────

  const handleGrantAppClick = () => {
    setGrantAppModalOpen(true);
  };

  const handleAppRevokeClick = (grant, app) => {
    setAppRevokeTarget({ grant, app });
  };

  const handleAppReactivateClick = (grant, app) => {
    setAppReactivateTarget({ grant, app });
  };

  const handleAppGrantSuccess = () => {
    setGrantAppModalOpen(false);
    loadAll();
  };

  const handleAppRevokeSuccess = () => {
    setAppRevokeTarget(null);
    loadAll();
  };

  const handleAppReactivateSuccess = () => {
    setAppReactivateTarget(null);
    loadAll();
  };

  // ─── Render branches ───────────────────────────────────────────

  if (loading) return <LoadingState />;
  if (error)
    return <ErrorState message={error} onRetry={loadAll} />;
  if (!tenant) return <NotFoundState tenantId={tenantId} />;

  const typeCfg = TENANT_TYPE_CONFIG[tenant.tenant_type] || TENANT_TYPE_CONFIG.account;
  const TypeIcon = typeCfg.icon;
  const parentName = tenant.parent_tenant_id
    ? parentNameById.get(tenant.parent_tenant_id) || null
    : null;

  return (
    <div data-testid="tenant-detail-page">
      {/* Breadcrumb */}
      <Link
        to="/admin/tenants"
        className="inline-flex items-center gap-1.5 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text mb-4"
      >
        <ArrowLeft size={14} />
        Tenants
      </Link>

      {/* Identity panel */}
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-5 md:p-6 mb-5">
        <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-bold text-aegisaxiom-text break-words">
              {tenant.name}
            </h1>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2 text-sm">
              <span className="font-mono text-xs text-aegisaxiom-text-muted">
                {tenant.slug}
              </span>
              <span className="text-aegisaxiom-text-muted">·</span>
              <span className="inline-flex items-center gap-1.5 text-aegisaxiom-text-secondary">
                <TypeIcon size={14} />
                {typeCfg.label}
              </span>
              <span className="text-aegisaxiom-text-muted">·</span>
              <span
                className={`inline-flex text-xs px-2 py-0.5 rounded-full ${STATUS_PILL_CLASS[tenant.status] || ''}`}
              >
                {statusLabel(tenant.status)}
              </span>
            </div>
            <div className="mt-3 text-xs text-aegisaxiom-text-secondary space-y-0.5">
              {parentName ? (
                <div>
                  Parent: <span className="text-aegisaxiom-text">{parentName}</span>
                </div>
              ) : isRoot ? (
                <div className="text-aegisaxiom-text-muted">System-managed root tenant</div>
              ) : (
                <div className="text-aegisaxiom-text-muted">Direct customer (no parent)</div>
              )}
              <div>
                Created <span className="text-aegisaxiom-text">{formatDate(tenant.created_at)}</span>
              </div>
              {ancestry.length > 2 && (
                <div className="text-aegisaxiom-text-muted">
                  Hierarchy:{' '}
                  {ancestry
                    .slice()
                    .reverse()
                    .map((a, i, arr) => (
                      <span key={a.tenant_id}>
                        {a.tenant_id === tenantId ? (
                          <span className="text-aegisaxiom-text">{a.name}</span>
                        ) : (
                          <Link
                            to={`/admin/tenants/${a.tenant_id}`}
                            className="hover:text-aegisaxiom-text"
                          >
                            {a.name}
                          </Link>
                        )}
                        {i < arr.length - 1 && (
                          <span className="mx-1.5 opacity-60">/</span>
                        )}
                      </span>
                    ))}
                </div>
              )}
            </div>
          </div>

          {/* Right side — actions. Hidden entirely for root. */}
          {!isRoot && (
            <div className="flex items-center gap-2 shrink-0">
              <button
                type="button"
                onClick={() => handleEdit(tenant)}
                className="btn-secondary inline-flex items-center gap-1.5"
                data-testid="tenant-detail-edit-button"
              >
                <Edit size={14} />
                Edit
              </button>
              <IdentityKebab
                open={identityKebabOpen}
                onToggle={() => setIdentityKebabOpen((o) => !o)}
                onClose={() => setIdentityKebabOpen(false)}
                tenant={tenant}
                onArchive={handleArchive}
              />
            </div>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-aegisaxiom-border mb-4 flex gap-1">
        <TabButton
          id="assignments"
          label="Assignments"
          count={activeAssignmentCount}
          totalCount={assignments.length}
          isActive={activeTab === 'assignments'}
          onClick={() => setActiveTab('assignments')}
        />
        <TabButton
          id="apps"
          label="Apps"
          count={activeAppGrantCount}
          totalCount={appGrants.length}
          isActive={activeTab === 'apps'}
          onClick={() => setActiveTab('apps')}
        />
        <TabButton
          id="subtenants"
          label="Sub-tenants"
          count={activeChildCount}
          totalCount={children.length}
          isActive={activeTab === 'subtenants'}
          onClick={() => setActiveTab('subtenants')}
        />
        <TabButton
          id="users"
          label="Users"
          count={users.length}
          totalCount={users.length}
          isActive={activeTab === 'users'}
          onClick={() => setActiveTab('users')}
        />
      </div>

      {/* Tab content */}
      {activeTab === 'assignments' && (
        <AssignmentsTabContent
          assignments={assignments}
          definitionsById={definitionsById}
          instanceCountsByAssignment={instanceCountsByAssignment}
          onGrantClick={handleGrantClick}
          onProvision={handleProvisionClick}
          onViewInstances={handleViewInstancesClick}
          onRevoke={handleRevokeClick}
          onReactivate={handleReactivateClick}
          isRoot={isRoot}
        />
      )}
      {activeTab === 'apps' && (
        <AppsTabContent
          grants={appGrants}
          appsById={appsById}
          onGrantClick={handleGrantAppClick}
          onRevoke={handleAppRevokeClick}
          onReactivate={handleAppReactivateClick}
          isRoot={isRoot}
        />
      )}
      {activeTab === 'subtenants' && (
        <SubTenantsTabContent
          subTenants={children}
          parentNameById={parentNameById}
          onEdit={handleEdit}
          onArchive={handleArchive}
          tenant={tenant}
        />
      )}
      {activeTab === 'users' && (
        <UsersTabContent
          users={users}
          tenantName={tenant.name}
          onInviteClick={handleInviteClick}
        />
      )}

      {/* Modals — shared between identity panel and sub-tenant rows */}
      <TenantFormModal
        isOpen={modalState !== null}
        mode={modalState === 'create' ? 'create' : 'edit'}
        tenant={modalState && modalState !== 'create' ? modalState.tenant : null}
        allTenants={allTenants}
        onClose={() => setModalState(null)}
        onSuccess={handleModalSuccess}
      />

      <ConfirmModal
        isOpen={archiveTarget !== null}
        onConfirm={handleArchiveConfirm}
        onCancel={handleArchiveCancel}
        title={archiveTarget ? `Archive ${archiveTarget.name}?` : ''}
        message={
          archiveTarget
            ? archiveTarget.tenant_id === tenantId
              ? 'This will archive the tenant you\'re currently viewing. You\'ll be returned to the tenants list afterward. Tenants with active children cannot be archived — archive the children first.'
              : 'This tenant will be marked as archived. Existing data is preserved, but no new operations will be possible. Tenants with active children cannot be archived — archive the children first.'
            : ''
        }
        confirmText={archiving ? 'Archiving…' : 'Archive'}
        cancelText="Cancel"
        variant="danger"
      />

      <GrantAssignmentModal
        isOpen={grantModalOpen}
        tenantId={tenantId}
        tenantName={tenant.name}
        definitions={definitions}
        existingAssignments={assignments}
        onClose={() => setGrantModalOpen(false)}
        onSuccess={handleGrantSuccess}
      />

      <ProvisionInstanceModal
        isOpen={provisionTarget !== null}
        tenantId={tenantId}
        tenantName={tenant.name}
        assignment={provisionTarget?.assignment}
        definition={provisionTarget?.definition}
        currentInstanceCount={provisionTarget?.currentCount ?? 0}
        onClose={() => setProvisionTarget(null)}
        onSuccess={handleProvisionSuccess}
      />

      <RevokeAssignmentModal
        isOpen={revokeTarget !== null}
        tenantId={tenantId}
        tenantName={tenant.name}
        assignment={revokeTarget?.assignment}
        definition={revokeTarget?.definition}
        instanceCount={revokeTarget?.currentCount ?? 0}
        onClose={() => setRevokeTarget(null)}
        onSuccess={handleRevokeSuccess}
        onConflict={handleRevokeConflict}
      />

      <RevokeConflictModal
        isOpen={revokeConflict !== null}
        conflictDetail={revokeConflict?.conflictDetail}
        allTenants={allTenants}
        definitionName={revokeConflict?.definitionName}
        onClose={() => setRevokeConflict(null)}
      />

      <ReactivateAssignmentModal
        isOpen={reactivateTarget !== null}
        tenantId={tenantId}
        tenantName={tenant.name}
        assignment={reactivateTarget?.assignment}
        definition={reactivateTarget?.definition}
        onClose={() => setReactivateTarget(null)}
        onSuccess={handleReactivateSuccess}
      />

      <InstanceListModal
        isOpen={instanceListTarget !== null}
        tenantId={tenantId}
        tenantName={tenant.name}
        assignment={instanceListTarget?.assignment}
        definition={instanceListTarget?.definition}
        instances={
          instanceListTarget
            ? instances.filter(
                (i) => i.assignment_id === instanceListTarget.assignment.assignment_id,
              )
            : []
        }
        onClose={() => setInstanceListTarget(null)}
        onInstanceDeleted={handleInstanceDeleted}
      />

      <InviteUserModal
        isOpen={inviteModalOpen}
        tenantId={tenantId}
        tenantName={tenant.name}
        onClose={() => setInviteModalOpen(false)}
        onSuccess={handleInviteSuccess}
      />

      {/* 1E.1 — App grant modals */}
      <GrantAppModal
        isOpen={grantAppModalOpen}
        tenantId={tenantId}
        tenantName={tenant.name}
        apps={grantableApps}
        existingGrants={appGrants}
        onClose={() => setGrantAppModalOpen(false)}
        onSuccess={handleAppGrantSuccess}
      />

      <RevokeAppGrantModal
        isOpen={appRevokeTarget !== null}
        tenantId={tenantId}
        tenantName={tenant.name}
        grant={appRevokeTarget?.grant}
        app={appRevokeTarget?.app}
        onClose={() => setAppRevokeTarget(null)}
        onSuccess={handleAppRevokeSuccess}
      />

      <ReactivateAppGrantModal
        isOpen={appReactivateTarget !== null}
        tenantId={tenantId}
        tenantName={tenant.name}
        grant={appReactivateTarget?.grant}
        app={appReactivateTarget?.app}
        onClose={() => setAppReactivateTarget(null)}
        onSuccess={handleAppReactivateSuccess}
      />
    </div>
  );
}

// ─── Tab button ────────────────────────────────────────────────────

function TabButton({ id, label, count, totalCount, isActive, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={`tenant-detail-tab-${id}`}
      className={
        'px-3 py-2 text-sm border-b-2 -mb-px transition ' +
        (isActive
          ? 'border-violet-500 text-aegisaxiom-text font-medium'
          : 'border-transparent text-aegisaxiom-text-secondary hover:text-aegisaxiom-text')
      }
    >
      {label}
      <span
        className={
          'ml-2 inline-flex items-center justify-center min-w-[20px] px-1.5 py-0.5 rounded-full text-[10px] font-semibold ' +
          (isActive
            ? 'bg-violet-100 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300'
            : 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted')
        }
      >
        {count}
      </span>
    </button>
  );
}

// ─── Identity panel kebab (current tenant) ────────────────────────

function IdentityKebab({ open, onToggle, onClose, tenant, onArchive }) {
  const canArchive = tenant.status !== 'archived';

  useEffect(() => {
    if (!open) return;
    const handleEsc = (e) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleEsc);
    return () => document.removeEventListener('keydown', handleEsc);
  }, [open, onClose]);

  // Click-outside via a transparent backdrop. Simpler than ref-tracking
  // and works reliably inside other positioned containers.
  return (
    <div className="relative inline-block">
      <button
        type="button"
        onClick={onToggle}
        aria-label="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
        className="p-2 rounded-lg hover:bg-aegisaxiom-elevated text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition border border-aegisaxiom-border"
        data-testid="tenant-detail-kebab"
      >
        <MoreVertical size={16} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={onClose} />
          <div
            role="menu"
            className="absolute right-0 top-full mt-1 z-20 min-w-[160px] bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg shadow-lg py-1 animate-fadeInUp"
          >
            {canArchive ? (
              <button
                type="button"
                role="menuitem"
                onClick={() => onArchive(tenant)}
                className="w-full text-left flex items-center gap-2 px-3 py-2 text-sm text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-600/10 transition"
                data-testid="tenant-detail-archive"
              >
                <Archive size={14} />
                Archive tenant
              </button>
            ) : (
              <div className="px-3 py-2 text-xs text-aegisaxiom-text-muted">
                Already archived
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ─── Tab content: assignments ──────────────────────────────────────

function AssignmentsTabContent({
  assignments,
  definitionsById,
  instanceCountsByAssignment,
  onGrantClick,
  onProvision,
  onViewInstances,
  onRevoke,
  onReactivate,
  isRoot,
}) {
  return (
    <div>
      {!isRoot && (
        <div className="flex items-center justify-end mb-3">
          <button
            type="button"
            onClick={onGrantClick}
            className="btn-primary inline-flex items-center gap-1.5"
            data-testid="tenant-detail-grant-button"
          >
            <Plus size={14} />
            Grant assignment
          </button>
        </div>
      )}

      {assignments.length === 0 ? (
        <div
          className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-10 text-center"
          data-testid="assignments-empty"
        >
          <div className="w-14 h-14 rounded-xl bg-violet-50 dark:bg-violet-600/10 mx-auto mb-4 flex items-center justify-center">
            <Bot className="w-7 h-7 text-violet-600 dark:text-violet-400" />
          </div>
          <h2 className="text-base font-semibold text-aegisaxiom-text mb-1">
            No assignments yet
          </h2>
          <p className="text-sm text-aegisaxiom-text-secondary max-w-md mx-auto">
            {isRoot
              ? "The root tenant doesn't carry assignments directly — assignments are granted to child tenants."
              : 'Grant an AI Employee definition to this tenant to get started.'}
          </p>
        </div>
      ) : (
        <AssignmentList
          assignments={assignments}
          definitionsById={definitionsById}
          instanceCountsByAssignment={instanceCountsByAssignment}
          onProvision={onProvision}
          onViewInstances={onViewInstances}
          onRevoke={onRevoke}
          onReactivate={onReactivate}
        />
      )}
    </div>
  );
}

// ─── Tab content: apps (1E.1) ──────────────────────────────────────

function AppsTabContent({
  grants,
  appsById,
  onGrantClick,
  onRevoke,
  onReactivate,
  isRoot,
}) {
  return (
    <div>
      {!isRoot && (
        <div className="flex items-center justify-end mb-3">
          <button
            type="button"
            onClick={onGrantClick}
            className="btn-primary inline-flex items-center gap-1.5"
            data-testid="tenant-detail-grant-app-button"
          >
            <Plus size={14} />
            Grant app
          </button>
        </div>
      )}

      {grants.length === 0 ? (
        <div
          className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-10 text-center"
          data-testid="app-grants-empty"
        >
          <div className="w-14 h-14 rounded-xl bg-blue-50 dark:bg-blue-600/10 mx-auto mb-4 flex items-center justify-center">
            <Package className="w-7 h-7 text-blue-600 dark:text-blue-400" />
          </div>
          <h2 className="text-base font-semibold text-aegisaxiom-text mb-1">
            No apps granted yet
          </h2>
          <p className="text-sm text-aegisaxiom-text-secondary max-w-md mx-auto">
            {isRoot
              ? "The root tenant doesn't carry app grants directly — apps are granted to child tenants."
              : 'Grant an ingested app to this tenant to surface it in their sidebar (once 1E.2 lands).'}
          </p>
        </div>
      ) : (
        <AppGrantList
          grants={grants}
          appsById={appsById}
          onRevoke={onRevoke}
          onReactivate={onReactivate}
        />
      )}
    </div>
  );
}

// ─── Tab content: sub-tenants ──────────────────────────────────────

function SubTenantsTabContent({ subTenants, parentNameById, onEdit, onArchive, tenant }) {
  if (subTenants.length === 0) {
    return (
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-10 text-center"
        data-testid="subtenants-empty"
      >
        <div className="w-14 h-14 rounded-xl bg-violet-50 dark:bg-violet-600/10 mx-auto mb-4 flex items-center justify-center">
          <Users className="w-7 h-7 text-violet-600 dark:text-violet-400" />
        </div>
        <h2 className="text-base font-semibold text-aegisaxiom-text mb-1">
          No sub-tenants
        </h2>
        <p className="text-sm text-aegisaxiom-text-secondary max-w-md mx-auto">
          {tenant.tenant_type === 'partner'
            ? 'This partner has no accounts beneath them yet. Create one from the main Tenants page to nest it here.'
            : tenant.tenant_type === 'account'
              ? 'Accounts sit at the leaf of the hierarchy — they don\'t have sub-tenants.'
              : 'No tenants are currently nested beneath this one.'}
        </p>
      </div>
    );
  }

  return (
    <TenantList
      tenants={subTenants}
      parentNameById={parentNameById}
      onEdit={onEdit}
      onArchive={onArchive}
    />
  );
}

// ─── Tab content: users ────────────────────────────────────────────

function UsersTabContent({ users, tenantName, onInviteClick }) {
  return (
    <div>
      <div className="flex items-center justify-end mb-3">
        <button
          type="button"
          onClick={onInviteClick}
          className="btn-primary inline-flex items-center gap-1.5"
          data-testid="tenant-detail-invite-button"
        >
          <Plus size={14} />
          Invite user
        </button>
      </div>

      {users.length === 0 ? (
        <div
          className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-10 text-center"
          data-testid="users-empty"
        >
          <div className="w-14 h-14 rounded-xl bg-violet-50 dark:bg-violet-600/10 mx-auto mb-4 flex items-center justify-center">
            <Users className="w-7 h-7 text-violet-600 dark:text-violet-400" />
          </div>
          <h2 className="text-base font-semibold text-aegisaxiom-text mb-1">
            No users in {tenantName} yet
          </h2>
          <p className="text-sm text-aegisaxiom-text-secondary max-w-md mx-auto">
            Invite someone here to give them sign-in access scoped to this tenant.
            They'll see only this tenant's AI Employees once they accept and log in.
          </p>
        </div>
      ) : (
        <TenantUserList users={users} />
      )}
    </div>
  );
}

// ─── State branches ────────────────────────────────────────────────

function LoadingState() {
  return (
    <div data-testid="tenant-detail-page">
      <div className="inline-flex items-center gap-1.5 text-sm text-aegisaxiom-text-muted mb-4">
        <ArrowLeft size={14} />
        Tenants
      </div>
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-6 mb-5"
        data-testid="tenant-detail-loading"
      >
        <div className="h-6 bg-aegisaxiom-elevated rounded animate-pulse w-48 mb-3" />
        <div className="h-4 bg-aegisaxiom-elevated rounded animate-pulse w-72 mb-2" />
        <div className="h-3 bg-aegisaxiom-elevated rounded animate-pulse w-40" />
      </div>
      <div className="h-10 bg-aegisaxiom-elevated rounded animate-pulse w-64 mb-4" />
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-4 space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-12 bg-aegisaxiom-elevated rounded animate-pulse" />
        ))}
      </div>
    </div>
  );
}

function ErrorState({ message, onRetry }) {
  return (
    <div data-testid="tenant-detail-page">
      <Link
        to="/admin/tenants"
        className="inline-flex items-center gap-1.5 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text mb-4"
      >
        <ArrowLeft size={14} />
        Tenants
      </Link>
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-8 text-center">
        <div className="w-12 h-12 rounded-xl bg-red-50 dark:bg-red-600/10 mx-auto mb-3 flex items-center justify-center">
          <AlertCircle className="w-6 h-6 text-red-600 dark:text-red-400" />
        </div>
        <h2 className="text-base font-semibold text-aegisaxiom-text mb-1">
          Couldn't load tenant
        </h2>
        <p className="text-sm text-aegisaxiom-text-secondary mb-4 max-w-md mx-auto">
          {message}
        </p>
        <button
          onClick={onRetry}
          className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text border border-aegisaxiom-border hover:border-aegisaxiom-border-hover transition"
        >
          <RefreshCw size={14} />
          Retry
        </button>
      </div>
    </div>
  );
}

function NotFoundState({ tenantId }) {
  return (
    <div data-testid="tenant-detail-page">
      <Link
        to="/admin/tenants"
        className="inline-flex items-center gap-1.5 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text mb-4"
      >
        <ArrowLeft size={14} />
        Tenants
      </Link>
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-10 text-center">
        <div className="w-14 h-14 rounded-xl bg-amber-50 dark:bg-amber-600/10 mx-auto mb-4 flex items-center justify-center">
          <Building2 className="w-7 h-7 text-amber-600 dark:text-amber-400" />
        </div>
        <h2 className="text-base font-semibold text-aegisaxiom-text mb-1">
          Tenant not found
        </h2>
        <p className="text-sm text-aegisaxiom-text-secondary mb-4 max-w-md mx-auto">
          No tenant matches the ID{' '}
          <span className="font-mono text-xs text-aegisaxiom-text">{tenantId}</span>.
          It may have been deleted or you may have followed a stale link.
        </p>
        <Link
          to="/admin/tenants"
          className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text border border-aegisaxiom-border hover:border-aegisaxiom-border-hover transition"
        >
          <ArrowLeft size={14} />
          Back to tenants
        </Link>
      </div>
    </div>
  );
}

// ─── Helpers ────────────────────────────────────────────────────────

const dateFmt = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
});

function formatDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return dateFmt.format(d);
}
