import React, { useCallback, useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import TabStrip from '@/components/shared/TabStrip';
import { usePermissions } from '@/hooks/usePermissions';

import BoardTab from './match/BoardTab';
// Sibling thread files — will exist after Wave 3 completes
import SessionsTab from './match/SessionsTab';
import ProviderTemplatesTab from './match/ProviderTemplatesTab';
import SettingsTab from './match/SettingsTab';

const BASE_TABS = [
  { id: 'board', label: 'Board', path: '/match/board' },
  { id: 'sessions', label: 'Sessions', path: '/match/sessions' },
  { id: 'provider-templates', label: 'Provider Templates', path: '/match/provider-templates' },
];

const SETTINGS_TAB = { id: 'settings', label: 'Settings', path: '/match/settings' };

function tabIdFromPath(pathname) {
  if (pathname.startsWith('/match/sessions')) return 'sessions';
  if (pathname.startsWith('/match/provider-templates')) return 'provider-templates';
  if (pathname.startsWith('/match/settings')) return 'settings';
  return 'board';
}

export default function MatchPortal() {
  const { hasPermission } = usePermissions();
  const location = useLocation();
  const navigate = useNavigate();

  const canViewSettings = hasPermission('match.settings.view');

  const tabs = canViewSettings ? [...BASE_TABS, SETTINGS_TAB] : BASE_TABS;

  const [activeTab, setActiveTab] = useState(() => tabIdFromPath(location.pathname));

  // Keep tab state in sync with URL (back/forward, direct navigation)
  useEffect(() => {
    const id = tabIdFromPath(location.pathname);
    setActiveTab(id);
  }, [location.pathname]);

  const handleTabChange = useCallback(
    (id) => {
      setActiveTab(id);
      const tab = [...BASE_TABS, SETTINGS_TAB].find((t) => t.id === id);
      if (tab) navigate(tab.path, { replace: true });
    },
    [navigate],
  );

  // Cross-tab navigation prop consumed by SessionsTab (3B interface contract):
  //   onNavigate(tabId, params?) — e.g. after creating a session, jump to the
  //   Board tab with that session preselected via `?session=<id>`.
  const handleNavigate = useCallback(
    (tabId, params) => {
      const tab = [...BASE_TABS, SETTINGS_TAB].find((t) => t.id === tabId);
      if (!tab) return;
      setActiveTab(tabId);
      const qs = params && params.session ? `?session=${encodeURIComponent(params.session)}` : '';
      navigate(`${tab.path}${qs}`);
    },
    [navigate],
  );

  if (!hasPermission('match.access')) {
    return (
      <div className="px-4 md:px-6 py-12 text-center text-aegisaxiom-text-muted">
        You do not have permission to access MATCH.
      </div>
    );
  }

  return (
    <div className="space-y-0" data-testid="match-portal">
      {/* Header */}
      <div className="px-4 md:px-6 pt-6 pb-2">
        <h1 className="page-title">MATCH</h1>
      </div>

      {/* Tab navigation */}
      <div className="border-b border-aegisaxiom-border bg-aegisaxiom-surface">
        <div className="px-4 md:px-6">
          <TabStrip
            tabs={tabs}
            activeId={activeTab}
            onChange={handleTabChange}
            variant="underline"
            size="md"
          />
        </div>
      </div>

      {/* Tab content */}
      <div className="px-4 md:px-6 py-6">
        {activeTab === 'board' && <BoardTab />}
        {activeTab === 'sessions' && <SessionsTab onNavigate={handleNavigate} />}
        {activeTab === 'provider-templates' && <ProviderTemplatesTab />}
        {activeTab === 'settings' && canViewSettings && <SettingsTab />}
      </div>
    </div>
  );
}
