import React, { useEffect, useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { usePermissions } from '@/hooks/usePermissions';
import TabStrip from '@/components/shared/TabStrip';
import DashboardTab from '@/components/projects/DashboardTab';
import ProjectsTab from '@/components/projects/ProjectsTab';
import TemplatesTab from '@/components/projects/TemplatesTab';
import CapacityTab from '@/components/projects/CapacityTab';

const TABS = [
  { id: 'dashboard', label: 'Dashboard', permission: 'projects.dashboard.view' },
  { id: 'projects', label: 'Projects', permission: 'projects.project.view' },
  { id: 'templates', label: 'Templates', permission: 'projects.template.view' },
  { id: 'capacity', label: 'Capacity', permission: 'projects.capacity.manage' },
];

// Parse the URL fragment after `/projects/` into { tab, projectId }.
// `/projects` -> { tab: null, projectId: null }
// `/projects/dashboard` -> { tab: 'dashboard', projectId: null }
// `/projects/projects` -> { tab: 'projects', projectId: null }
// `/projects/projects/proj_123` -> { tab: 'projects', projectId: 'proj_123' }
function parsePath(pathname) {
  const m = pathname.match(/^\/projects(?:\/([^/]+))?(?:\/([^/]+))?\/?$/);
  if (!m) return { tab: null, projectId: null };
  const tab = m[1] ?? null;
  const projectId = m[2] ?? null;
  return { tab, projectId };
}

export default function ProjectsPortal() {
  const perms = usePermissions();
  const location = useLocation();
  const navigate = useNavigate();

  const visibleTabs = useMemo(
    () => TABS.filter((t) => perms.hasPermission(t.permission)),
    [perms],
  );

  const { tab: urlTab, projectId: urlProjectId } = parsePath(location.pathname);

  // Resolve the active tab: URL wins if valid, else first visible tab.
  const activeTab = useMemo(() => {
    if (urlTab && visibleTabs.some((t) => t.id === urlTab)) return urlTab;
    return visibleTabs[0]?.id ?? 'dashboard';
  }, [urlTab, visibleTabs]);

  // If we landed on a bare `/projects` (no tab in URL), push the canonical URL
  // so back-button history works correctly thereafter.
  useEffect(() => {
    if (!urlTab && visibleTabs.length > 0) {
      navigate(`/projects/${visibleTabs[0].id}`, { replace: true });
    }
  }, [urlTab, visibleTabs, navigate]);

  const handleTabChange = (tabId) => {
    navigate(`/projects/${tabId}`);
  };

  if (visibleTabs.length === 0) {
    return (
      <div className="p-8 text-center text-aegisaxiom-text-muted text-sm">
        You don&apos;t have access to the Projects module.
      </div>
    );
  }

  return (
    <div className="flex flex-col min-h-0">
      <div className="px-6 pt-4 border-b border-aegisaxiom-border bg-aegisaxiom-surface shrink-0">
        <h1 className="page-title mb-3">Projects</h1>
        <TabStrip
          tabs={visibleTabs}
          activeId={activeTab}
          onChange={handleTabChange}
          variant="underline"
        />
      </div>

      <div className="flex-1 overflow-auto p-6">
        {activeTab === 'dashboard' && perms.hasPermission('projects.dashboard.view') && (
          <DashboardTab />
        )}
        {activeTab === 'projects' && perms.hasPermission('projects.project.view') && (
          <ProjectsTab
            urlProjectId={urlProjectId}
            onProjectIdChange={(pid) => {
              if (pid) navigate(`/projects/projects/${pid}`);
              else navigate('/projects/projects');
            }}
          />
        )}
        {activeTab === 'templates' && perms.hasPermission('projects.template.view') && (
          <TemplatesTab />
        )}
        {activeTab === 'capacity' && perms.hasPermission('projects.capacity.view') && (
          <CapacityTab />
        )}
      </div>
    </div>
  );
}
