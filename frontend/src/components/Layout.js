import React, { useState, useMemo, useEffect } from 'react';
import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import { useTheme } from '@/contexts/ThemeContext';
import { usePermissions } from '@/hooks/usePermissions';
import { computeActiveNavPath, findActiveGroupId } from '@/lib/navActive';
import {
  LayoutDashboard,
  Users,
  Briefcase,
  DollarSign,
  FileText,
  Wrench,
  Building2,
  Settings,
  Webhook,
  LogOut,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Menu,
  X,
  Shield,
  TrendingUp,
  ScrollText,
  Bot,
  Landmark,
  KeyRound,
  Clock,
  FolderKanban,
  Layers,
  Package,
  Target,
  Lightbulb,
  UserCircle,
  Sparkles,
  Zap,
  Award,
  Sun,
  Moon,
  BarChart3,
  Scale,
  Droplets,
  ClipboardList,
  RefreshCw,
  BookOpen,
  Receipt,
  Map,
  GitMerge,
  Users2,
  BookMarked,
  Workflow,
  Network,
  FileCheck,
} from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '../lib/apiConfig';
import { useIsRootTenant } from '@/lib/rootTenant';
import { fetchTenantSidebarConfig } from '@/lib/tenantSidebarApi';
import { buildTenantNavGroups } from '@/lib/tenantAppNav';

const API = API_URL;

// ──────────────────────────────────────────────
//  GROUPED NAVIGATION STRUCTURE
//  Regrouped per Aidan's specifications
//
//  Each item supports:
//    - resource: resource name gated via canView (covers `.view`,
//      `.view.all`, `.view.own`) — preferred for data-resource pages
//    - permission: string or array of literal permission keys,
//      any-match via hasAnyPermission — use for non-resource pages
//      (`products.view`, `chat.access`, `users.manage`, etc.)
//    - roles: role names that bypass permission checks
//  resource + permission can coexist; either passing grants access.
// ──────────────────────────────────────────────

const NAV_GROUPS = [
  // ── Dashboards (ungrouped, shown at top) ──
  {
    id: 'dashboards',
    label: null,
    collapsible: false,
    items: [
      {
        path: '/dashboard',
        label: 'Dashboard',
        icon: LayoutDashboard,
        permission: ['dashboard.main'],
        roles: ['admin', 'director', 'finance'],
      },
      {
        path: '/sales-rep-dashboard',
        label: 'My Dashboard',
        icon: TrendingUp,
        permission: ['dashboard.sales_rep'],
        roles: ['sales_rep'],
      },
      {
        path: '/my-dashboard',
        label: 'Agent Dashboard',
        icon: TrendingUp,
        permission: ['dashboard.agent'],
        roles: ['agent'],
      },
    ],
  },

  // ── Sales & CRM ──
  {
    id: 'sales',
    label: 'Sales & CRM',
    icon: Target,
    collapsible: true,
    defaultOpen: false,
    items: [
      {
        path: '/opportunities',
        label: 'Opportunities',
        icon: Lightbulb,
        resource: 'opportunities',
      },
      {
        path: '/sales-ops-workspace',
        label: 'Sales Ops',
        icon: Briefcase,
        permission: ['sales_ops.solutions_design.view'],
        roles: ['admin', 'sales_ops', 'director'],
      },
      {
        path: '/deals',
        label: 'Deal Management',
        icon: Target,
        resource: 'deals',
      },
      {
        path: '/contacts',
        label: 'Contacts',
        icon: UserCircle,
        resource: 'contacts',
      },
      {
        path: '/accounts',
        label: 'Accounts',
        icon: Building2,
        resource: 'accounts',
      },
      {
        path: '/commissions',
        label: 'Commissions',
        icon: DollarSign,
        resource: 'commissions',
      },
      {
        path: '/admin/commission-settings',
        label: 'Commission Settings',
        icon: DollarSign,
        permission: 'commissions.template.view',
      },
      {
        path: '/admin/products',
        label: 'Products & Services',
        icon: Package,
        permission: 'products.view',
      },
    ],
  },

  // ── Operations ──
  {
    id: 'operations',
    label: 'Operations',
    icon: FolderKanban,
    collapsible: true,
    defaultOpen: false,
    items: [
      {
        path: '/contracts',
        label: 'Contracts',
        icon: FileText,
        resource: 'contracts',
      },
      {
        // Contract Approvals queue. Route is mounted in App.js at
        // `contracts/approval-queue` with permission gate
        // [contracts.approve, contracts.view.all, contracts.view,
        // contracts.view.own]. The sidebar entry mirrors the first
        // two so the link only appears for users who can actually
        // take an approval action — backend remains the canonical
        // security boundary either way (see App.js:404-417).
        path: '/contracts/approval-queue',
        label: 'Contract Approvals',
        icon: FileCheck,
        permission: ['contracts.approve', 'contracts.view.all'],
      },
      {
        path: '/operations/contracts',
        label: 'Contract Financials',
        icon: BarChart3,
        permission: ['operations.contracts.view'],
      },
      {
        path: '/projects',
        label: 'Projects',
        icon: FolderKanban,
        permission: ['projects.access'],
      },
      {
        path: '/time-management',
        label: 'Time Management',
        icon: Clock,
        resource: 'time',
        permission: ['time.entry'],
      },
      {
        path: '/field-jobs',
        label: 'Field Jobs',
        icon: Wrench,
        resource: 'field_jobs',
      },
      {
        path: '/match',
        label: 'MATCH',
        icon: Network,
        permission: ['match.access'],
      },
      {
        path: '/admin/contractor-businesses',
        label: 'Contractor Businesses',
        icon: Building2,
        permission: 'admin.contractors.view',
      },
      {
        path: '/admin/contract-templates',
        label: 'Contract Templates',
        icon: FileText,
        permission: 'contract_templates.view',
      },
      {
        path: '/admin/resource-types',
        label: 'Resource Types',
        icon: Layers,
        permission: 'admin.resource_types.view',
      },
      {
        // Tenant-wide audit + override surface for contracts.approve.
        // Route is mounted in App.js at `admin/contract-approvers`
        // (preserved for backward compat — the URL didn't change
        // when the page was repositioned from "Contract Approvers"
        // to "Contract Approval Access"). Gate matches the route.
        path: '/admin/contract-approvers',
        label: 'Contract Approval Access',
        icon: Shield,
        permission: ['users.edit', 'users.view'],
      },
    ],
  },

  // ── Finance (Financial Command Center) ──
  // Single sidebar entry — the FCC portal renders its own tab strip
  // (Command Center, Workspace, P&L, BS, CF, TB, AR/AP Aging, DSO/DPO,
  // Invoices, Bills, CoA Mapping, Sync Queue, Customers, Vendors, GL,
  // Commissions, Referral MRR) internally. Deep-link via `?tab=<id>`.
  {
    id: 'finance',
    label: 'Finance',
    icon: DollarSign,
    collapsible: true,
    defaultOpen: false,
    items: [
      {
        path: '/finance',
        label: 'Financial Command Center',
        icon: Landmark,
        permission: ['finance.access', 'finance.portal'],
      },
    ],
  },

  // ── AI & Automation ──
  {
    id: 'ai',
    label: 'AI & Automation',
    icon: Sparkles,
    collapsible: true,
    defaultOpen: false,
    items: [
      {
        path: '/ai-employees',
        label: 'My AI Employees',
        icon: Sparkles,
        permission: ['ai_employees.view'],
      },
      { path: '/chat', label: 'AI Chat', icon: Bot, permission: ['chat.access'] },
      {
        path: '/discovery-sessions',
        label: 'My Discovery Sessions',
        icon: ClipboardList,
        permission: 'vega_sessions.view_own',
      },
      {
        path: '/admin/workflow-rules',
        label: 'Workflow Rules',
        icon: Zap,
        permission: 'workflow_rules.view',
      },
      {
        path: '/ai-dispatcher',
        label: 'AI Dispatcher',
        icon: Workflow,
        permission: ['ai_dispatcher.teams.view'],
      },
    ],
  },

  // ── Admin ──
  {
    id: 'admin',
    label: 'Admin',
    icon: Settings,
    collapsible: true,
    defaultOpen: false,
    items: [
      {
        path: '/admin/users',
        label: 'User & Resource Mgmt',
        icon: Users,
        permission: 'users.manage',
      },
      {
        path: '/admin/tenants',
        label: 'Tenants',
        icon: GitMerge,
        permission: 'tenants.view',
      },
      {
        path: '/admin/roles',
        label: 'Roles & Permissions',
        icon: Shield,
        permission: 'roles.manage',
      },
      {
        path: '/admin/integrations',
        label: 'Integrations',
        icon: Settings,
        permission: 'integrations.manage',
      },
      {
        path: '/admin/branding',
        label: 'Organization Branding',
        icon: Building2,
        permission: 'branding.manage',
      },
      {
        path: '/admin/sow-milestone-templates',
        label: 'SOW Milestone Templates',
        icon: Layers,
        permission: 'sales_ops.solutions_design.view',
      },
      { path: '/admin/webhooks', label: 'Webhooks', icon: Webhook, permission: 'webhooks.view' },
      {
        path: '/admin/policies',
        label: 'Policy Engine',
        icon: Shield,
        permission: 'policies.view',
      },
      { path: '/admin/audit', label: 'Audit Log', icon: ScrollText, permission: 'audit.view' },
      {
        path: '/admin/app-ingestion',
        label: 'App Ingestion',
        icon: Package,
        permission: 'admin.manage',
      },
    ],
  },
];

const ROLE_BADGE = {
  admin: 'bg-violet-500/15 text-violet-300 border border-violet-500/20',
  director: 'bg-blue-500/15 text-blue-300 border border-blue-500/20',
  finance: 'bg-amber-500/15 text-amber-300 border border-amber-500/20',
  sales_rep: 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/20',
  agent: 'bg-fuchsia-500/15 text-fuchsia-300 border border-fuchsia-500/20',
  customer: 'bg-gray-500/15 text-gray-300 border border-gray-500/20',
  chat_only: 'bg-sky-500/15 text-sky-300 border border-sky-500/20',
};

// ──────────────────────────────────────────────
//  COLLAPSIBLE NAV GROUP (accordion-aware)
// ──────────────────────────────────────────────

function NavGroup({ group, collapsed, visibleItems, isOpen, onToggle, onNavigate, activePath }) {
  if (visibleItems.length === 0) return null;

  const renderItem = (item) => (
    <NavItem
      key={item.path}
      item={item}
      collapsed={collapsed}
      onNavigate={onNavigate}
      isActive={item.path === activePath}
    />
  );

  // Ungrouped items (dashboards) — render directly
  if (!group.label) {
    return <div className="space-y-0.5">{visibleItems.map(renderItem)}</div>;
  }

  const GroupIcon = group.icon;

  // Collapsed sidebar — show only icons
  if (collapsed) {
    return <div className="space-y-0.5 py-1">{visibleItems.map(renderItem)}</div>;
  }

  // Expanded sidebar — non-collapsible groups render children directly
  if (!group.collapsible) {
    return (
      <div className="pt-3">
        <div className="flex items-center gap-2 px-3 py-1.5 text-xs font-semibold text-white uppercase tracking-wider">
          <GroupIcon size={12} className="text-white" />
          <span>{group.label}</span>
        </div>
        <div className="space-y-0.5 mt-1">{visibleItems.map(renderItem)}</div>
      </div>
    );
  }

  // Expanded sidebar — collapsible group with accordion behavior
  return (
    <div className="pt-3">
      <button
        onClick={() => onToggle(group.id)}
        className="flex items-center justify-between w-full px-3 py-1.5 text-xs font-semibold text-white uppercase tracking-wider hover:text-violet-300 transition-colors"
      >
        <div className="flex items-center gap-2">
          <GroupIcon size={12} className="text-white" />
          <span>{group.label}</span>
        </div>
        <ChevronDown
          size={12}
          className={`text-white transition-transform duration-200 ${isOpen ? '' : '-rotate-90'}`}
        />
      </button>

      <div
        className={`space-y-0.5 overflow-hidden transition-all duration-200 ${
          isOpen ? 'max-h-[500px] opacity-100 mt-1' : 'max-h-0 opacity-0'
        }`}
      >
        {visibleItems.map(renderItem)}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────
//  NAV ITEM
// ──────────────────────────────────────────────

function NavItem({ item, collapsed, onNavigate, isActive }) {
  const Icon = item.icon;
  // Active class is driven by the longest-match `isActive` prop
  // computed in Layout from computeActiveNavPath() — NOT by
  // NavLink's default isActive callback, which would mark
  // /contracts active whenever the pathname starts with
  // /contracts (including /contracts/approval-queue).
  return (
    <NavLink
      to={item.path}
      onClick={onNavigate}
      className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-150 group
        ${
          isActive
            ? 'bg-gradient-to-r from-violet-600/20 to-indigo-600/10 text-violet-300 border border-violet-500/20 shadow-[0_0_12px_-4px_rgba(139,92,246,0.2)]'
            : 'text-[#8b8da3] hover:text-[#c4b5fd] hover:bg-white/[0.04]'
        }
        ${collapsed ? 'justify-center' : ''}`}
      title={collapsed ? item.label : undefined}
      data-testid={`nav-${item.label.toLowerCase().replace(/\s/g, '-')}`}
      aria-current={isActive ? 'page' : undefined}
    >
      <Icon size={18} className="shrink-0" />
      {!collapsed && <span>{item.label}</span>}
    </NavLink>
  );
}

// ──────────────────────────────────────────────
//  SIDEBAR CONTENT
// ──────────────────────────────────────────────

function SidebarContent({
  groups,
  collapsed,
  user,
  changePwLoading,
  handleChangePassword,
  handleLogout,
  navigate,
  openGroupId,
  setOpenGroupId,
  onClose,
  onNavigate,
  isRoot,
  activePath,
}) {
  const handleToggle = (groupId) => {
    setOpenGroupId((prev) => (prev === groupId ? null : groupId));
  };

  return (
    <div className="flex flex-col h-full">
      {/* Logo */}
      <div
        className={`flex items-center gap-3 px-4 py-5 border-b border-white/[0.06] ${collapsed ? 'justify-center' : ''}`}
      >
        <div className="w-10 h-10 shrink-0 flex items-center justify-center">
          <img
            src="/logo-3.png"
            alt="AEGISAXIOM"
            className="w-[52px] h-[52px] object-contain drop-shadow-[0_0_8px_rgba(139,92,246,0.6)]"
          />
        </div>
        {!collapsed && (
          <span
            className="text-white font-bold text-lg tracking-widest"
            style={{ fontFamily: 'Manrope, sans-serif' }}
          >
            AEGISAXIOM
          </span>
        )}
        {/* Mobile close button — only rendered when onClose is passed in */}
        {onClose && (
          <button
            onClick={onClose}
            aria-label="Close menu"
            data-testid="mobile-sidebar-close-btn"
            className="ml-auto p-2 -mr-1 rounded-lg text-[#c4b5fd] hover:text-white hover:bg-white/[0.06] transition-colors"
          >
            <X size={20} />
          </button>
        )}
      </div>

      {/* Nav Groups */}
      <nav className="flex-1 overflow-y-auto py-2 px-2">
        {groups.map((g) => (
          <NavGroup
            key={g.group.id}
            group={g.group}
            collapsed={collapsed}
            visibleItems={g.items}
            isOpen={openGroupId === g.group.id}
            onToggle={handleToggle}
            onNavigate={onNavigate}
            activePath={activePath}
          />
        ))}
      </nav>

      {/* User Footer */}
      <div className="border-t border-white/[0.06] p-4">
        {!collapsed && (
          <div className="flex items-center gap-3 mb-3">
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-violet-600/40 to-indigo-600/30 border border-violet-500/20 flex items-center justify-center text-violet-200 text-sm font-bold shrink-0">
              {user?.name?.[0]?.toUpperCase() || 'U'}
            </div>
            <div className="min-w-0">
              <p className="text-xs font-medium text-[#ede9fe] truncate">{user?.name}</p>
              <span
                className={`text-xs px-1.5 py-0.5 rounded-full ${ROLE_BADGE[user?.role] || ROLE_BADGE.customer}`}
              >
                {user?.role?.replace(/_/g, ' ')}
              </span>
            </div>
          </div>
        )}
        {/* Certifications — SimpliMeta-internal; hidden from non-root
            tenant users per 1E.2 (decided with Samson 19 May 2026). */}
        {isRoot && (
          <button
            onClick={() => navigate('/certifications')}
            className={`flex items-center gap-2 text-white hover:text-violet-400 transition-colors text-xs w-full mb-2 ${collapsed ? 'justify-center' : ''}`}
          >
            <Award size={15} />
            {!collapsed && <span>Certifications</span>}
          </button>
        )}
        <button
          onClick={handleChangePassword}
          disabled={changePwLoading}
          data-testid="change-password-btn"
          className={`flex items-center gap-2 text-white hover:text-violet-400 transition-colors text-xs w-full mb-2 ${collapsed ? 'justify-center' : ''}`}
        >
          <KeyRound size={15} />
          {!collapsed && <span>{changePwLoading ? 'Sending...' : 'Change Password'}</span>}
        </button>
        <button
          onClick={handleLogout}
          data-testid="logout-btn"
          className={`flex items-center gap-2 text-white hover:text-red-400 transition-colors text-xs w-full ${collapsed ? 'justify-center' : ''}`}
        >
          <LogOut size={15} />
          {!collapsed && <span>Sign Out</span>}
        </button>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────
//  LAYOUT (MAIN EXPORT)
// ──────────────────────────────────────────────

export default function Layout() {
  const { user, logout } = useAuth();
  const { theme, toggleTheme, isDark } = useTheme();
  const { hasAnyPermission, isAdmin, canView } = usePermissions();
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  // Accordion state — only one group open at a time, null = all collapsed
  const [openGroupId, setOpenGroupId] = useState(null);

  // 1E.2 — Root tenants render the existing NAV_GROUPS (full admin
  // sidebar, unchanged). Non-root tenants render a minimal sidebar:
  // spine (AI Employees + AI Chat) plus an Apps group populated from
  // /api/tenant/sidebar-config. The branch happens at the structure-
  // selection level so the rest of the rendering pipeline doesn't
  // care which sidebar it's drawing.
  const isRoot = useIsRootTenant();
  const [tenantApps, setTenantApps] = useState([]);

  useEffect(() => {
    // Only fetch for non-root tenants. Defensive: also skip if user
    // hasn't loaded yet (no tenant_id to scope against).
    if (isRoot || !user?.tenant_id) return;
    let cancelled = false;
    fetchTenantSidebarConfig().then((cfg) => {
      if (!cancelled) setTenantApps(cfg.apps || []);
    });
    return () => {
      cancelled = true;
    };
  }, [isRoot, user?.tenant_id]);

  // The nav structure fed into the filter pipeline below. For root
  // tenants this is the full NAV_GROUPS (845-line const at the top
  // of this file). For non-root tenants it's the dynamic
  // spine+apps array. Both shapes are identical so SidebarContent
  // renders either without branching.
  const navGroupsForUser = useMemo(
    () => (isRoot ? NAV_GROUPS : buildTenantNavGroups(tenantApps)),
    [isRoot, tenantApps],
  );

  const filteredGroups = useMemo(() => {
    return navGroupsForUser.map((group) => {
      const visibleItems = group.items.filter((item) => {
        if (isAdmin()) return true;

        const roleList = item.roles
          ? Array.isArray(item.roles)
            ? item.roles
            : [item.roles]
          : null;
        const permList = item.permission
          ? Array.isArray(item.permission)
            ? item.permission
            : [item.permission]
          : null;

        // No gate at all — always visible
        if (!roleList && !permList && !item.resource) return true;

        if (roleList && roleList.includes(user?.role)) return true;
        if (item.resource && canView(item.resource)) return true;
        if (permList && hasAnyPermission(permList)) return true;
        return false;
      });

      return { group, items: visibleItems };
    }).filter((g) => g.items.length > 0);
  }, [navGroupsForUser, hasAnyPermission, isAdmin, canView, user]);

  // Longest-match active nav path. Flattening visible items here
  // means only currently-visible items can win — a permission-gated
  // item that the user can't see won't accidentally win the match.
  const activePath = useMemo(() => {
    const flat = filteredGroups.flatMap((g) => g.items);
    return computeActiveNavPath(flat, location.pathname);
  }, [filteredGroups, location.pathname]);

  // Auto-expand the group containing the active item — but ONLY
  // when the active path itself changes (route navigation, deep
  // link, browser back/forward). If we also depended on
  // filteredGroups, the effect would re-fire on every render
  // (usePermissions returns fresh function refs each render, which
  // gives filteredGroups a new identity each render) and snap
  // openGroupId back to the active group, making manual toggling
  // impossible. Once the user lands on a page, the open group is
  // theirs to control via the accordion header clicks.
  useEffect(() => {
    if (!activePath) return;
    const groupId = findActiveGroupId(filteredGroups, activePath);
    if (groupId) setOpenGroupId(groupId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePath]);

  const [changePwLoading, setChangePwLoading] = useState(false);

  const closeMobileSidebar = () => setMobileOpen(false);

  // Fix F — Escape key closes mobile sidebar
  useEffect(() => {
    if (!mobileOpen) return;
    const onKeyDown = (e) => {
      if (e.key === 'Escape') closeMobileSidebar();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [mobileOpen]);

  // Fix G — lock body scroll while mobile sidebar is open
  useEffect(() => {
    if (mobileOpen) {
      const prev = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.overflow = prev;
      };
    }
  }, [mobileOpen]);

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  const handleChangePassword = async () => {
    setChangePwLoading(true);
    try {
      const res = await api.post(`${API}/api/auth/request-password-change`);
      const data = await res.json();
      if (res.ok) {
        toast.success(data.message || 'Password change email sent!');
      } else {
        toast.error(data.detail || 'Failed to send password change email.');
      }
    } catch {
      toast.error('Network error. Please try again.');
    } finally {
      setChangePwLoading(false);
    }
  };

  return (
    <div
      className="flex h-screen overflow-hidden"
      style={{
        background: isDark
          ? `radial-gradient(ellipse at 0% 0%, rgba(139, 92, 246, 0.06) 0%, transparent 40%),
             radial-gradient(ellipse at 100% 100%, rgba(99, 102, 241, 0.04) 0%, transparent 40%),
             var(--aegisaxiom-bg-base)`
          : `radial-gradient(ellipse at 0% 0%, rgba(139, 92, 246, 0.04) 0%, transparent 40%),
             radial-gradient(ellipse at 100% 100%, rgba(99, 102, 241, 0.03) 0%, transparent 40%),
             var(--aegisaxiom-bg-base)`,
      }}
    >
      {/* Desktop Sidebar */}
      <aside
        className={`hidden lg:flex flex-col border-r border-white/[0.06] transition-all duration-300 ${collapsed ? 'w-16' : 'w-60'} shrink-0`}
        style={{ background: 'linear-gradient(180deg, #1e1640 0%, #141028 100%)' }}
      >
        <SidebarContent
          groups={filteredGroups}
          collapsed={collapsed}
          user={user}
          changePwLoading={changePwLoading}
          handleChangePassword={handleChangePassword}
          handleLogout={handleLogout}
          navigate={navigate}
          openGroupId={openGroupId}
          setOpenGroupId={setOpenGroupId}
          isRoot={isRoot}
          activePath={activePath}
        />
      </aside>

      {/* Collapse Toggle */}
      <button
        onClick={() => setCollapsed((p) => !p)}
        className="hidden lg:flex absolute top-1/2 -translate-y-1/2 z-50 w-5 h-10 items-center justify-center text-[#6b6d85] hover:text-violet-400 transition-all rounded-r-lg"
        style={{
          left: collapsed ? '64px' : '240px',
          background: 'linear-gradient(180deg, #1e1640, #141028)',
          border: '0.5px solid rgba(139, 92, 246, 0.12)',
          borderLeft: 'none',
        }}
      >
        {collapsed ? <ChevronRight size={12} /> : <ChevronLeft size={12} />}
      </button>

      {/* Mobile Sidebar — slide-in drawer with animation */}
      <div
        className={`lg:hidden fixed inset-0 z-50 flex pointer-events-none transition-opacity duration-200 ${
          mobileOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0'
        }`}
        aria-hidden={!mobileOpen}
      >
        {/* Backdrop */}
        <div
          className={`absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity duration-200 ${
            mobileOpen ? 'opacity-100' : 'opacity-0'
          }`}
          onClick={closeMobileSidebar}
        />
        {/* Drawer */}
        <div
          className={`relative w-[85%] max-w-[280px] border-r border-white/[0.06] flex flex-col shadow-2xl transform transition-transform duration-300 ease-out ${
            mobileOpen ? 'translate-x-0' : '-translate-x-full'
          }`}
          style={{ background: 'linear-gradient(180deg, #1e1640 0%, #141028 100%)' }}
          role="dialog"
          aria-modal="true"
          aria-label="Navigation menu"
        >
          <SidebarContent
            groups={filteredGroups}
            collapsed={false}
            user={user}
            changePwLoading={changePwLoading}
            handleChangePassword={handleChangePassword}
            handleLogout={handleLogout}
            navigate={navigate}
            openGroupId={openGroupId}
            setOpenGroupId={setOpenGroupId}
            onClose={closeMobileSidebar}
            onNavigate={closeMobileSidebar}
            isRoot={isRoot}
            activePath={activePath}
          />
        </div>
      </div>

      {/* Main */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top Header */}
        <header
          className="h-14 backdrop-blur-md flex items-center px-4 gap-4 shrink-0"
          style={{
            background: isDark
              ? 'linear-gradient(90deg, rgba(30, 22, 64, 0.4), rgba(12, 10, 26, 0.6), rgba(30, 22, 64, 0.4))'
              : 'linear-gradient(90deg, rgba(241, 238, 249, 0.95), rgba(255, 255, 255, 0.9), rgba(241, 238, 249, 0.95))',
            borderBottom: `0.5px solid var(--aegisaxiom-header-border)`,
          }}
        >
          <button
            className="lg:hidden p-2 -ml-2 rounded-lg hover:bg-aegisaxiom-elevated transition-colors"
            onClick={() => setMobileOpen(true)}
            aria-label="Open menu"
            data-testid="mobile-menu-btn"
          >
            <Menu size={22} style={{ color: 'var(--aegisaxiom-text-secondary)' }} />
          </button>
          <div className="flex-1" />
          <div className="flex items-center gap-3">
            {/* Theme Toggle */}
            <button
              onClick={toggleTheme}
              className="p-2 rounded-lg transition-all duration-200 hover:scale-105"
              style={{
                background: isDark ? 'rgba(255, 255, 255, 0.06)' : 'rgba(124, 58, 237, 0.06)',
              }}
              title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
              data-testid="theme-toggle-btn"
            >
              {isDark ? (
                <Sun size={16} className="text-amber-600 dark:text-amber-400" />
              ) : (
                <Moon size={16} className="text-violet-500" />
              )}
            </button>
            <div className="w-7 h-7 rounded-full bg-gradient-to-br from-violet-600/40 to-indigo-600/30 border border-violet-500/20 flex items-center justify-center text-violet-200 text-xs font-bold shrink-0">
              {user?.name?.[0]?.toUpperCase() || 'U'}
            </div>
            <span
              style={{ color: 'var(--aegisaxiom-text-primary)' }}
              className="text-sm font-medium hidden sm:block"
            >
              {user?.name}
            </span>
          </div>
        </header>

        {/* Page Content */}
        <main
          className="flex-1 overflow-y-auto p-6"
          style={{
            background: isDark
              ? 'radial-gradient(ellipse at 10% 0%, rgba(139, 92, 246, 0.04) 0%, transparent 50%)'
              : 'radial-gradient(ellipse at 10% 0%, rgba(124, 58, 237, 0.025) 0%, transparent 50%)',
          }}
        >
          <Outlet />
        </main>
      </div>
    </div>
  );
}
