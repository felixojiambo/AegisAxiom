/**
 * Phase 1E.2 — Tenant sidebar nav structure.
 *
 * The dynamic tenant sidebar reuses the same NavGroup/NavItem render
 * components as the root sidebar (in Layout.js) — it just feeds them
 * a different `groups` array. This module owns the construction of
 * that array.
 *
 * ## Shape mirrors NAV_GROUPS
 *
 * Each group has { id, label, icon?, collapsible, defaultOpen?, items }
 * and each item has { path, label, icon }. Permission gates are
 * intentionally omitted — tenant users by definition have access to
 * what's granted to their tenant. Fine-grained role permissions
 * within a tenant (regular user vs tenant_admin) are enforced at the
 * route layer in 1E.3+, not by hiding sidebar items.
 *
 * ## Slug → route/icon map (APP_SLUG_NAV)
 *
 * The backend returns granted apps with `slug` (e.g. "projects",
 * "finance"). The SPA has its own routing table; we map slug to the
 * existing route and a lucide icon component here. Apps with no
 * mapping are skipped from the sidebar with a console warning — the
 * deployment ordering for new apps is:
 *
 *   1. Build the app + register its route in the SPA
 *   2. Add an entry to APP_SLUG_NAV below
 *   3. Ingest the app via the App Ingestion module
 *   4. Grant it to whichever tenants should see it (1E.1 admin Apps tab)
 *
 * Skipping the map entry between steps 1 and 2 just means the app
 * doesn't appear in tenant sidebars yet — no broken links, no crashes.
 */
import {
  Sparkles,
  Bot,
  Package,
  Landmark,
  FolderKanban,
} from 'lucide-react';

/**
 * Map from app_registry.slug to the SPA's nav configuration for that
 * app. Add new entries here as new apps come online. The "icon" is a
 * lucide component (imported above); "route" is the SPA path the
 * sidebar item links to. Both fields required.
 */
export const APP_SLUG_NAV = {
  finance: { route: '/finance', icon: Landmark },
  projects: { route: '/projects', icon: FolderKanban },
  // Register additional app slugs here as their routes get scoped
  // by tenant_id in 1E.3+. Each new entry needs a corresponding
  // route already mounted in the SPA router.
};

/**
 * Builds the NAV_GROUPS-shaped array for a non-root tenant user.
 *
 * @param {Array<{ slug: string, name: string }>} grantedApps —
 *   the `apps` field from /tenant/sidebar-config.
 * @returns NAV_GROUPS-shaped array consumable by SidebarContent.
 */
export function buildTenantNavGroups(grantedApps) {
  const apps = (grantedApps || [])
    .map((app) => {
      const cfg = APP_SLUG_NAV[app.slug];
      if (!cfg) {
        if (typeof console !== 'undefined') {
          console.warn(
            `[TenantSidebar] No frontend nav config for app slug='${app.slug}'. ` +
              'Skipping. Add it to APP_SLUG_NAV in lib/tenantAppNav.js.',
          );
        }
        return null;
      }
      return {
        path: cfg.route,
        label: app.name,
        icon: cfg.icon,
      };
    })
    .filter(Boolean);

  // Spine — always present. AI Employees and AI Chat are universally
  // available to tenant users. Permission gates intentionally omitted
  // (see module docstring).
  const groups = [
    {
      id: 'tenant-core',
      label: null, // ungrouped, no header
      collapsible: false,
      items: [
        { path: '/ai-employees', label: 'AI Employees', icon: Sparkles },
        { path: '/chat', label: 'AI Chat', icon: Bot },
      ],
    },
  ];

  // Apps group — only renders when at least one app is granted. An
  // empty Apps section would be confusing for users on a freshly
  // provisioned tenant; better to show nothing than an empty header.
  if (apps.length > 0) {
    groups.push({
      id: 'tenant-apps',
      label: 'Apps',
      icon: Package,
      collapsible: false,
      items: apps,
    });
  }

  return groups;
}
