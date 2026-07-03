import React, { useState, useCallback } from 'react';
import TabStrip from '@/components/shared/TabStrip';
import { usePermissions } from '@/hooks/usePermissions';
import TeamsTab from '@/components/AIDispatcher/TeamsTab';
import DecisionsTab from '@/components/AIDispatcher/DecisionsTab';
import SettingsTab from '@/components/AIDispatcher/SettingsTab';

const ALL_TABS = [
  { id: 'teams', label: 'Teams', permKey: 'ai_dispatcher.teams.view' },
  { id: 'decisions', label: 'Decisions', permKey: 'ai_dispatcher.decisions.view' },
  { id: 'settings', label: 'Settings', permKey: 'ai_dispatcher.settings.view' },
];

function readTabFromHash(validIds) {
  const hash = window.location.hash.replace('#', '');
  return validIds.includes(hash) ? hash : validIds[0];
}

export default function AIDispatcherPortal() {
  const { hasPermission, isAdmin } = usePermissions();

  const visibleTabs = ALL_TABS.filter(
    (t) => isAdmin() || hasPermission(t.permKey)
  );

  const validIds = visibleTabs.map((t) => t.id);

  const [activeTab, setActiveTab] = useState(() => readTabFromHash(validIds));

  const handleTabChange = useCallback((id) => {
    setActiveTab(id);
    window.history.replaceState(null, '', `#${id}`);
  }, []);

  // If user has no tabs visible at all, show nothing (PermissionRoute handles
  // the outer gate, but individual tabs may still be invisible).
  if (visibleTabs.length === 0) {
    return (
      <div className="px-4 py-6">
        <p className="text-sm text-aegisaxiom-text-secondary">
          You don't have access to any AI Dispatcher views.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-0" data-testid="ai-dispatcher-portal">
      {/* Tab bar */}
      <div className="border-b border-aegisaxiom-border bg-aegisaxiom-surface">
        <div className="px-4 md:px-6">
          <TabStrip
            tabs={visibleTabs}
            activeId={activeTab}
            onChange={handleTabChange}
            variant="underline"
            size="md"
          />
        </div>
      </div>

      {/* Content */}
      <div className="px-4 md:px-6 py-6">
        {activeTab === 'teams' && <TeamsTab />}
        {activeTab === 'decisions' && <DecisionsTab />}
        {activeTab === 'settings' && <SettingsTab />}
      </div>
    </div>
  );
}
