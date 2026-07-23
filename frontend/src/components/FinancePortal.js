import React, { useEffect, useState, useCallback } from 'react';
import TabStrip from '@/components/shared/TabStrip';
import { usePermissions } from '@/hooks/usePermissions';

// Surviving finance surfaces (internally sourced from the AEGISAXIOM app).
import WorkspaceTab from '@/components/finance/WorkspaceTab';
import CommissionsTab from '@/components/finance/CommissionsTab';
import ReferralMRRTab from '@/components/finance/ReferralMRRTab';

// Financial Command Center (FCC) feature flag. The FCC tabs were fed by the
// external accounting data lake, which was removed in Phase 1 (Nova/FCC park).
// They are dark by default; the leaf components that rendered the now-deleted
// /api/finance/* accounting endpoints were removed. A future internal-ledger
// effort rebuilds these behind this same flag. Default OFF.
const FCC_ENABLED = process.env.REACT_APP_FCC_ENABLED === 'true';

// Tab group definitions. Tabs marked `fcc: true` were fed by the removed
// external accounting lake and are hidden unless FCC_ENABLED. Survivors
// (workspace, commissions, referral-mrr) always show.
const TAB_GROUPS = [
  {
    group: 'Overview',
    tabs: [
      { id: 'command-center', label: 'Command Center', fcc: true },
      { id: 'workspace', label: 'Workspace' },
    ],
  },
  {
    group: 'Financials',
    tabs: [
      { id: 'pnl', label: 'P&L', fcc: true },
      { id: 'balance-sheet', label: 'Balance Sheet', fcc: true },
      { id: 'cash-flow', label: 'Cash Flow', fcc: true },
      { id: 'trial-balance', label: 'Trial Balance', fcc: true },
    ],
  },
  {
    group: 'Working Capital',
    tabs: [
      { id: 'ar-aging', label: 'AR Aging', fcc: true },
      { id: 'ap-aging', label: 'AP Aging', fcc: true },
      { id: 'dso-dpo', label: 'DSO / DPO', fcc: true },
    ],
  },
  {
    group: 'Operations',
    tabs: [
      { id: 'invoices', label: 'Invoices', fcc: true },
      { id: 'bills', label: 'Bills', fcc: true },
      { id: 'sync-queue', label: 'Sync Queue', fcc: true },
      { id: 'coa-mapping', label: 'CoA Mapping', fcc: true },
    ],
  },
  {
    group: 'Lists',
    tabs: [
      { id: 'customers', label: 'Customers', fcc: true },
      { id: 'vendors', label: 'Vendors', fcc: true },
      { id: 'gl-explorer', label: 'GL Explorer', fcc: true },
    ],
  },
  {
    group: 'Legacy',
    tabs: [
      { id: 'commissions', label: 'Commissions' },
      { id: 'referral-mrr', label: 'Referral MRR' },
    ],
  },
];

// Visible groups honor the FCC flag: drop fcc tabs (and any group left empty)
// when the flag is off.
const VISIBLE_GROUPS = TAB_GROUPS
  .map((g) => ({ ...g, tabs: g.tabs.filter((t) => FCC_ENABLED || !t.fcc) }))
  .filter((g) => g.tabs.length > 0);

const VISIBLE_TAB_IDS = VISIBLE_GROUPS.flatMap((g) => g.tabs.map((t) => t.id));
const DEFAULT_TAB = VISIBLE_TAB_IDS[0] || 'workspace';

function readHash() {
  const hash = window.location.hash.replace('#', '');
  if (hash && VISIBLE_TAB_IDS.includes(hash)) return hash;
  const params = new URLSearchParams(window.location.search);
  const tab = params.get('ftab');
  if (tab && VISIBLE_TAB_IDS.includes(tab)) return tab;
  return DEFAULT_TAB;
}

export default function FinancePortal() {
  const { hasPermission } = usePermissions();  // eslint-disable-line no-unused-vars
  const [activeTab, setActiveTab] = useState(readHash);

  // Sync tab to URL hash for deep-linking
  const handleTabChange = useCallback((id) => {
    setActiveTab(id);
    window.history.replaceState(null, '', `#${id}`);
  }, []);

  // Handle back/forward nav
  useEffect(() => {
    const onHashChange = () => {
      const next = readHash();
      setActiveTab(next);
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const activeGroup = VISIBLE_GROUPS.find((g) => g.tabs.some((t) => t.id === activeTab));
  const activeTabDef = activeGroup && activeGroup.tabs.find((t) => t.id === activeTab);

  return (
    <div className="space-y-0" data-testid="finance-portal">
      {/* Group navigation (underline style) — uses TabStrip so mobile gets
          edge-fade indicators + auto-scroll-into-view of the active group */}
      <div className="border-b border-aegisaxiom-border bg-aegisaxiom-surface">
        <div className="px-4 md:px-6">
          <TabStrip
            tabs={VISIBLE_GROUPS.map((g) => ({
              id: g.tabs[0].id,
              label: g.group,
              groupId: g.group,
            }))}
            activeId={(activeGroup && activeGroup.tabs[0].id) || activeTab}
            onChange={handleTabChange}
            variant="underline"
            size="md"
          />
        </div>
      </div>

      {/* Sub-tabs for the active group (pill style) */}
      {activeGroup && activeGroup.tabs.length > 1 && (
        <div className="px-4 md:px-6 py-3 border-b border-aegisaxiom-border bg-aegisaxiom-surface">
          <TabStrip
            tabs={activeGroup.tabs}
            activeId={activeTab}
            onChange={handleTabChange}
            variant="pill"
            size="sm"
          />
        </div>
      )}

      {/* Tab content */}
      <div className="px-4 md:px-6 py-6">
        {activeTab === 'workspace' && <WorkspaceTab />}
        {activeTab === 'commissions' && <CommissionsTab />}
        {activeTab === 'referral-mrr' && <ReferralMRRTab />}

        {/* FCC tabs are only reachable when FCC_ENABLED. Their externally-fed
            leaf components were deleted in Phase 1, so they render a parked
            notice until a future internal-ledger effort rebuilds them. */}
        {activeTabDef && activeTabDef.fcc && (
          <div className="max-w-xl rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface p-8 text-center">
            <h3 className="text-lg font-semibold text-aegisaxiom-text">
              Financial Command Center is parked
            </h3>
            <p className="mt-2 text-sm text-aegisaxiom-text-muted">
              This view was powered by an external accounting data lake, which
              has been removed. It will return with a future internal-ledger
              rebuild.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}