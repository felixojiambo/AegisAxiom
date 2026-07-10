import React, { useEffect, useState, useCallback } from 'react';
import TabStrip from '@/components/shared/TabStrip';
import { usePermissions } from '@/hooks/usePermissions';

// Overview
import CommandCenterTab from '@/components/finance/CommandCenterTab';
import WorkspaceTab from '@/components/finance/WorkspaceTab';

// Financials
import PnLTab from '@/components/finance/PnLTab';
import BalanceSheetTab from '@/components/finance/BalanceSheetTab';
import CashFlowTab from '@/components/finance/CashFlowTab';
import TrialBalanceTab from '@/components/finance/TrialBalanceTab';

// Working Capital
import ARAgingTab from '@/components/finance/ARAgingTab';
import APAgingTab from '@/components/finance/APAgingTab';
import DsoDpoTab from '@/components/finance/DsoDpoTab';

// Operations
import InvoicesTab from '@/components/finance/InvoicesTab';
import BillsTab from '@/components/finance/BillsTab';
import SyncQueueTab from '@/components/finance/SyncQueueTab';
import CoAMappingTab from '@/components/finance/CoAMappingTab';

// Lists
import CustomersTab from '@/components/finance/CustomersTab';
import CustomerDetailModal from '@/components/finance/CustomerDetailModal';
import VendorsTab from '@/components/finance/VendorsTab';
import VendorDetailModal from '@/components/finance/VendorDetailModal';
import GLExplorerTab from '@/components/finance/GLExplorerTab';

// Legacy
import CommissionsTab from '@/components/finance/CommissionsTab';
import ReferralMRRTab from '@/components/finance/ReferralMRRTab';

// Tab group definitions
const TAB_GROUPS = [
  {
    group: 'Overview',
    tabs: [
      { id: 'command-center', label: 'Command Center' },
      { id: 'workspace', label: 'Workspace' },
    ],
  },
  {
    group: 'Financials',
    tabs: [
      { id: 'pnl', label: 'P&L' },
      { id: 'balance-sheet', label: 'Balance Sheet' },
      { id: 'cash-flow', label: 'Cash Flow' },
      { id: 'trial-balance', label: 'Trial Balance' },
    ],
  },
  {
    group: 'Working Capital',
    tabs: [
      { id: 'ar-aging', label: 'AR Aging' },
      { id: 'ap-aging', label: 'AP Aging' },
      { id: 'dso-dpo', label: 'DSO / DPO' },
    ],
  },
  {
    group: 'Operations',
    tabs: [
      { id: 'invoices', label: 'Invoices' },
      { id: 'bills', label: 'Bills' },
      { id: 'sync-queue', label: 'Sync Queue' },
      { id: 'coa-mapping', label: 'CoA Mapping' },
    ],
  },
  {
    group: 'Lists',
    tabs: [
      { id: 'customers', label: 'Customers' },
      { id: 'vendors', label: 'Vendors' },
      { id: 'gl-explorer', label: 'GL Explorer' },
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

// Derive flat tab list (for TabStrip) with group separators via a label trick
// Each group renders its own pill TabStrip row.

const ALL_TAB_IDS = TAB_GROUPS.flatMap((g) => g.tabs.map((t) => t.id));

function readHash() {
  const hash = window.location.hash.replace('#', '');
  if (hash && ALL_TAB_IDS.includes(hash)) return hash;
  const params = new URLSearchParams(window.location.search);
  const tab = params.get('ftab');
  if (tab && ALL_TAB_IDS.includes(tab)) return tab;
  return 'command-center';
}

export default function FinancePortal() {
  const { hasPermission } = usePermissions();
  const [activeTab, setActiveTab] = useState(readHash);

  // Detail modal state
  const [selectedCustomerId, setSelectedCustomerId] = useState(null);
  const [selectedVendorId, setSelectedVendorId] = useState(null);

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

  // Close detail modals when tab changes away from the lists group
  useEffect(() => {
    if (!['customers', 'vendors'].includes(activeTab)) {
      setSelectedCustomerId(null);
      setSelectedVendorId(null);
    }
  }, [activeTab]);

  const activeGroup = TAB_GROUPS.find((g) => g.tabs.some((t) => t.id === activeTab));

  return (
    <div className="space-y-0" data-testid="finance-portal">
      {/* Group navigation (underline style) — uses TabStrip so mobile gets
          edge-fade indicators + auto-scroll-into-view of the active group */}
      <div className="border-b border-aegisaxiom-border bg-aegisaxiom-surface">
        <div className="px-4 md:px-6">
          <TabStrip
            tabs={TAB_GROUPS.map((g) => ({
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
        {activeTab === 'command-center' && (
          <CommandCenterTab
            onSelectCustomer={(id) => {
              setSelectedCustomerId(id);
              handleTabChange('customers');
            }}
            onSelectVendor={(id) => {
              setSelectedVendorId(id);
              handleTabChange('vendors');
            }}
          />
        )}
        {activeTab === 'workspace' && <WorkspaceTab />}

        {activeTab === 'pnl' && <PnLTab />}
        {activeTab === 'balance-sheet' && <BalanceSheetTab />}
        {activeTab === 'cash-flow' && <CashFlowTab />}
        {activeTab === 'trial-balance' && <TrialBalanceTab />}

        {activeTab === 'ar-aging' && <ARAgingTab />}
        {activeTab === 'ap-aging' && <APAgingTab />}
        {activeTab === 'dso-dpo' && <DsoDpoTab />}

        {activeTab === 'invoices' && <InvoicesTab />}
        {activeTab === 'bills' && <BillsTab />}
        {activeTab === 'sync-queue' && <SyncQueueTab />}
        {activeTab === 'coa-mapping' && <CoAMappingTab />}

        {activeTab === 'customers' && (
          <CustomersTab onSelectCustomer={(id) => setSelectedCustomerId(id)} />
        )}
        {activeTab === 'vendors' && <VendorsTab onSelectVendor={(id) => setSelectedVendorId(id)} />}
        {activeTab === 'gl-explorer' && <GLExplorerTab />}

        {activeTab === 'commissions' && <CommissionsTab />}
        {activeTab === 'referral-mrr' && <ReferralMRRTab />}
      </div>

      {/* Detail modals — rendered above everything */}
      <CustomerDetailModal
        customerId={selectedCustomerId}
        onClose={() => setSelectedCustomerId(null)}
      />
      <VendorDetailModal vendorId={selectedVendorId} onClose={() => setSelectedVendorId(null)} />
    </div>
  );
}
