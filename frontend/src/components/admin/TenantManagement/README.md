# TenantManagement

UI for managing tenants, AI Employee assignments, and provisioned instances. Wraps the tenant CRUD + catalog / assignment / instance APIs.

Access is gated by the standard permission system: any role holding `tenants.view` can see the Tenants section in the sidebar and reach `/admin/tenants` directly. By default this is the `admin` role only — modify role permissions through the Roles & Permissions admin UI to widen access.

## File map

| File | Purpose |
|---|---|
| `index.js` | Tenants list page — filter chips, create button, archive flow |
| `TenantDetail.js` | Single tenant detail page — Overview / Apps / Assignments / Sub-tenants / Users tabs |
| `TenantList.js` | Reusable tenant table (used by index + sub-tenant view on detail page) |
| `TenantFormModal.js` | Create / Edit tenant modal |
| `AssignmentList.js` | Assignments table with kebab actions (revoke / reactivate / provision / view instances) |
| `GrantAssignmentModal.js` | Grant a definition to a tenant |
| `RevokeAssignmentModal.js` | Confirmation for revoking an assignment |
| `ReactivateAssignmentModal.js` | Confirmation for reactivating a revoked assignment |
| `RevokeConflictModal.js` | Surfaces the delegation chain-check 409 when a revoke would orphan descendants |
| `ProvisionInstanceModal.js` | Provision an instance from an assignment, with optional per-instance overrides |
| `InstanceListModal.js` | Instance drill-down for an assignment |
| `AppList.js` | App grants table (Apps tab on Tenant Detail) |
| `GrantAppModal.js` | Grant an ingested app to a tenant |
| `RevokeAppGrantModal.js` | Confirmation for revoking an app grant |
| `TenantUserList.js` | Tenant-scoped user list (Users tab) |
| `InviteUserModal.js` | Invite a user to a tenant (sends accept-invite email) |
| `helpers.js` | Slug validation, status/type config, ancestry builder |
| `api.js` | Endpoint wrappers over `lib/api.js` |

## Design system

Strictly `aegisaxiom-*` design tokens for theme-aware surfaces. Status / type pills follow the project-wide `dark:` prefix patterns. The `md:` (768px) breakpoint is the universal table-to-card boundary.
