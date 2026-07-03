import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '../lib/apiConfig';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/hooks/usePermissions';
import ContractorManagementTab from './sales-ops/ContractorManagementTab';
import ProcurementTab from './sales-ops/ProcurementTab_New';
import SolutionsDesignTab from './sales-ops/SolutionsDesignTab';
import KnowledgeBaseTab from './sales-ops/KnowledgeBaseTab';
import ConfirmModal from './shared/ConfirmModal';
import TabStrip from './shared/TabStrip';
import {
  Briefcase,
  Package,
  Users,
  Search,
  DollarSign,
  Building2,
  User,
  Calendar,
  Flag,
  Eye,
  CheckCircle,
  ArrowRight,
  Loader,
  BookOpen,
  ChevronDown,
} from 'lucide-react';

const API = API_URL;

// Internal Sales Ops stages (doesn't affect opportunity.stage)
const SALES_OPS_STAGES = {
  discovery: { label: 'Discovery', color: 'bg-blue-500', order: 1 },
  designing: { label: 'Designing', color: 'bg-purple-500', order: 2 },
  procurement: { label: 'Procurement', color: 'bg-amber-500', order: 3 },
  review: { label: 'Review', color: 'bg-violet-500', order: 4 },
  complete: { label: 'Complete', color: 'bg-green-500', order: 5 },
};

const PRIORITY_CONFIG = {
  high: { label: 'High', color: 'bg-red-100 text-red-700 border-red-300', icon: '🔴' },
  medium: { label: 'Medium', color: 'bg-yellow-100 text-yellow-700 border-yellow-300', icon: '🟡' },
  low: { label: 'Low', color: 'bg-green-100 text-green-700 border-green-300', icon: '🟢' },
};

export default function SalesOpsWorkspace() {
  const { user } = useAuth();
  const { isAdmin, hasAnyPermission } = usePermissions();
  const [activeTab, setActiveTab] = useState('solutions_design');
  const [opportunities, setOpportunities] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedOpportunity, setSelectedOpportunity] = useState(null);
  const [updatingStage, setUpdatingStage] = useState(null);
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });

  // Check access (Sales Ops or Admin only)
  const hasAccess = isAdmin() || hasAnyPermission(['sales_ops.solutions_design.view']);

  useEffect(() => {
    if (hasAccess) {
      loadOpportunities();
    }
  }, [hasAccess]);

  const loadOpportunities = async () => {
    try {
      setLoading(true);

      // Fetch only opportunities in "proposal_development" stage
      const response = await api.get(
        `${API}/api/opportunities?stage=proposal_development&limit=500`,
      );

      if (response.ok) {
        const data = await response.json();
        const items = data.items || [];
        setOpportunities(items);
      } else {
        toast.error('Failed to load opportunities');
      }
    } catch (error) {
      console.error('Error loading opportunities:', error);
      toast.error('Failed to load opportunities');
    } finally {
      setLoading(false);
    }
  };

  const updateSalesOpsStage = async (opportunityId, newStage) => {
    setUpdatingStage(opportunityId);
    try {
      // Update the sales_ops_stage field (internal stage, doesn't affect main stage)
      const response = await api.put(`${API}/api/opportunities/${opportunityId}`, {
        sales_ops_stage: newStage,
      });

      if (response.ok) {
        toast.success(`Moved to ${SALES_OPS_STAGES[newStage].label}`);
        loadOpportunities();
      } else {
        const errorData = await response.json();
        toast.error(errorData.detail || 'Failed to update stage');
      }
    } catch (error) {
      console.error('Error updating stage:', error);
      toast.error('Failed to update stage');
    } finally {
      setUpdatingStage(null);
    }
  };

  const markComplete = (opportunityId) => {
    setConfirmState({
      open: true,
      title: 'Mark Opportunity Complete',
      message:
        'This will:\n• Move the opportunity to "Scoping Complete" stage\n• Remove it from Sales Ops Workspace\n• Return it to the Sales team for next steps',
      action: async () => {
        setUpdatingStage(opportunityId);
        try {
          // Change main opportunity stage to "scoping_complete"
          const response = await api.put(`${API}/api/opportunities/${opportunityId}`, {
            stage: 'scoping_complete',
            sales_ops_stage: null, // Clear internal stage
          });

          if (response.ok) {
            toast.success('Scoping complete! Opportunity moved to next stage.');
            loadOpportunities(); // Refresh (opportunity will disappear)
          } else {
            const errorData = await response.json();
            toast.error(errorData.detail || 'Failed to complete');
          }
        } catch (error) {
          console.error('Error completing opportunity:', error);
          toast.error('Failed to complete');
        } finally {
          setUpdatingStage(null);
        }
      },
    });
  };

  const formatCurrency = (amount) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
    }).format(amount || 0);
  };

  const formatDate = (dateString) => {
    if (!dateString) return 'Not set';
    return new Date(dateString).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  };

  // Filter opportunities by search
  const filteredOpportunities = opportunities.filter((opp) => {
    if (!searchQuery) return true;
    const search = searchQuery.toLowerCase();
    return (
      opp.name?.toLowerCase().includes(search) ||
      opp.account_name?.toLowerCase().includes(search) ||
      opp.description?.toLowerCase().includes(search)
    );
  });

  // Group by internal sales ops stage
  const groupedByStage = {};
  Object.keys(SALES_OPS_STAGES).forEach((stage) => {
    groupedByStage[stage] = filteredOpportunities.filter(
      (opp) => (opp.sales_ops_stage || 'discovery') === stage,
    );
  });

  if (!hasAccess) {
    return (
      <div className="min-h-screen bg-aegisaxiom-base flex items-center justify-center p-6">
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-8 max-w-md text-center">
          <div className="text-red-600 dark:text-red-400 text-5xl mb-4">🚫</div>
          <h2 className="text-xl font-bold text-aegisaxiom-text mb-2">Access Denied</h2>
          <p className="text-aegisaxiom-text-secondary">
            This workspace is restricted to Sales Operations and Admin personnel only.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-aegisaxiom-base text-aegisaxiom-text p-4 sm:p-6">
      {/* Header */}
      <div className="mb-5 sm:mb-6">
        {/* Title row */}
        <div className="flex items-start justify-between gap-4 mb-4">
          <div className="min-w-0 flex-1">
            <h1 className="text-xl sm:text-2xl font-bold text-aegisaxiom-text flex items-center gap-2">
              <Briefcase className="w-5 h-5 sm:w-6 sm:h-6 text-violet-600 dark:text-violet-400 shrink-0" />
              <span className="truncate">Sales Operations Workspace</span>
            </h1>
            <p className="text-aegisaxiom-text-secondary text-xs sm:text-sm mt-1">
              Opportunities in Proposal Development stage requiring Sales Ops action
            </p>
          </div>

          {/* Desktop search (inline) */}
          <div className="hidden md:block w-72 lg:w-80 relative shrink-0">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-aegisaxiom-text-secondary" />
            <input
              type="text"
              placeholder="Search opportunities..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500"
            />
          </div>
        </div>

        {/* Mobile search (full-width, below title) */}
        <div className="md:hidden mb-4 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-aegisaxiom-text-secondary" />
          <input
            type="text"
            placeholder="Search opportunities..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 text-sm"
          />
        </div>

        {/* Tab switcher — dropdown on mobile, scroll tab strip on desktop */}
        <TabNav activeTab={activeTab} setActiveTab={setActiveTab} />
      </div>

      {/* Tab Content */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader className="w-8 h-8 text-violet-500 animate-spin" />
        </div>
      ) : (
        <>
          {/* Solutions Design Tab */}
          {activeTab === 'solutions_design' && <SolutionsDesignTab />}

          {/* Procurement Tab */}
          {activeTab === 'procurement' && <ProcurementTab />}

          {/* Contractor Management Tab */}
          {activeTab === 'contractor_management' && <ContractorManagementTab />}

          {/* Knowledge Base Tab */}
          {activeTab === 'knowledge_base' && <KnowledgeBaseTab />}
        </>
      )}

      {/* Opportunity Detail Modal */}
      {selectedOpportunity && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-3xl max-h-[90vh] overflow-y-auto">
            {/* Header */}
            <div className="p-6 border-b border-aegisaxiom-border">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h2 className="text-xl font-bold text-aegisaxiom-text mb-2">
                    {selectedOpportunity.name}
                  </h2>
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-xs px-2 py-1 rounded ${
                        SALES_OPS_STAGES[selectedOpportunity.sales_ops_stage || 'discovery'].color
                      } text-aegisaxiom-text`}
                    >
                      {SALES_OPS_STAGES[selectedOpportunity.sales_ops_stage || 'discovery'].label}
                    </span>
                    <span
                      className={`text-xs px-2 py-1 rounded border ${
                        PRIORITY_CONFIG[selectedOpportunity.priority || 'medium'].color
                      }`}
                    >
                      {PRIORITY_CONFIG[selectedOpportunity.priority || 'medium'].icon}{' '}
                      {PRIORITY_CONFIG[selectedOpportunity.priority || 'medium'].label}
                    </span>
                  </div>
                </div>
                <button
                  onClick={() => setSelectedOpportunity(null)}
                  className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
                >
                  ✕
                </button>
              </div>
            </div>

            {/* Content */}
            <div className="p-6 space-y-4">
              {/* Financial Info */}
              <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3">
                  Financial Information
                </h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <div className="text-aegisaxiom-text-secondary text-sm mb-1">Opportunity Value</div>
                    <div className="text-xl font-bold text-green-600 dark:text-green-400">
                      {formatCurrency(selectedOpportunity.amount)}
                    </div>
                  </div>
                  <div>
                    <div className="text-aegisaxiom-text-secondary text-sm mb-1">Commission Method</div>
                    <div className="text-aegisaxiom-text text-sm">
                      {selectedOpportunity.commission_payment_method || 'Not specified'}
                    </div>
                  </div>
                </div>
              </div>

              {/* Account & Contact */}
              <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3">
                  Account & Contact
                </h3>
                <div className="space-y-2 text-sm">
                  <div>
                    <div className="text-aegisaxiom-text-secondary">Account</div>
                    <div className="text-aegisaxiom-text">{selectedOpportunity.account_name}</div>
                  </div>
                  <div>
                    <div className="text-aegisaxiom-text-secondary">Contact</div>
                    <div className="text-aegisaxiom-text">
                      {selectedOpportunity.contact_name || 'No contact assigned'}
                    </div>
                  </div>
                </div>
              </div>

              {/* Description */}
              {selectedOpportunity.description && (
                <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                  <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-2">
                    Description
                  </h3>
                  <p className="text-aegisaxiom-text-secondary text-sm whitespace-pre-wrap">
                    {selectedOpportunity.description}
                  </p>
                </div>
              )}

              {/* Assignment */}
              <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3">Assignment</h3>
                <div className="space-y-2 text-sm">
                  <div>
                    <div className="text-aegisaxiom-text-secondary">Owner</div>
                    <div className="text-aegisaxiom-text">
                      {selectedOpportunity.opportunity_owner_name}
                    </div>
                  </div>
                  <div>
                    <div className="text-aegisaxiom-text-secondary">Agent of Record</div>
                    <div className="text-aegisaxiom-text">
                      {selectedOpportunity.agent_of_record_name || 'None'}
                    </div>
                  </div>
                  <div>
                    <div className="text-aegisaxiom-text-secondary">Sales Ops Assigned</div>
                    <div className="text-aegisaxiom-text">
                      {selectedOpportunity.assigned_sales_ops_name || 'None'}
                    </div>
                  </div>
                </div>
              </div>

              {/* Lines of Business */}
              {selectedOpportunity.lines_of_business?.length > 0 && (
                <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                  <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-2">
                    Lines of Business
                  </h3>
                  <div className="flex flex-wrap gap-2">
                    {selectedOpportunity.lines_of_business.map((lob, idx) => {
                      const lobLabels = {
                        field_services: {
                          label: 'Field Services',
                          color:
                            'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300 border-blue-500/30',
                        },
                        professional_services: {
                          label: 'Professional Services',
                          color: 'bg-purple-600/20 text-purple-300 border-purple-500/30',
                        },
                        referral_mrr: {
                          label: 'Referral MRR',
                          color:
                            'bg-pink-50 dark:bg-pink-600/20 text-pink-700 dark:text-pink-300 border-pink-500/30',
                        },
                        wholesale_mrr: {
                          label: 'Wholesale MRR',
                          color:
                            'bg-cyan-600/20 text-cyan-700 dark:text-cyan-300 border-cyan-500/30',
                        },
                      };
                      const config = lobLabels[lob] || {
                        label: lob,
                        color:
                          'bg-aegisaxiom-elevated/20 text-aegisaxiom-text-secondary border-aegisaxiom-border-hover/30',
                      };
                      return (
                        <span
                          key={idx}
                          className={`px-3 py-1 rounded text-xs font-medium border ${config.color}`}
                        >
                          {config.label}
                        </span>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Action: Mark Complete */}
              {(selectedOpportunity.sales_ops_stage || 'discovery') === 'complete' && (
                <div className="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-700/40 rounded-lg p-4">
                  <h3 className="text-sm font-semibold text-green-700 dark:text-green-300 mb-2">
                    Ready to Complete
                  </h3>
                  <p className="text-aegisaxiom-text-secondary text-sm mb-3">
                    This opportunity has passed all Sales Ops stages and is ready to move back to
                    Sales for proposal delivery.
                  </p>
                  <button
                    onClick={() => {
                      markComplete(selectedOpportunity.opportunity_id);
                      setSelectedOpportunity(null);
                    }}
                    disabled={updatingStage === selectedOpportunity.opportunity_id}
                    className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-green-600 hover:bg-green-500 text-white text-sm font-medium rounded-lg transition disabled:opacity-50"
                  >
                    <CheckCircle className="w-4 h-4" />
                    Mark Complete & Move to Scoping Complete
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <ConfirmModal
        isOpen={confirmState.open}
        title={confirmState.title}
        message={confirmState.message}
        confirmText="Confirm"
        variant="primary"
        onConfirm={() => confirmState.action?.()}
        onCancel={() => setConfirmState({ open: false, action: null, title: '', message: '' })}
      />
    </div>
  );
}

// Shared tab list so the mobile select and desktop tab strip stay in sync.
const TAB_ITEMS = [
  { id: 'solutions_design', label: 'Solutions Design', icon: Briefcase },
  { id: 'procurement', label: 'Procurement', icon: Package },
  { id: 'contractor_management', label: 'Contractor Management', icon: Users },
  { id: 'knowledge_base', label: 'Knowledge Base', icon: BookOpen },
];

function TabNav({ activeTab, setActiveTab }) {
  const activeItem = TAB_ITEMS.find((t) => t.id === activeTab) || TAB_ITEMS[0];
  const ActiveIcon = activeItem.icon;

  return (
    <>
      {/* Mobile: styled select — all tabs visible via the native picker */}
      <div className="md:hidden relative">
        <div className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none">
          <ActiveIcon className="w-4 h-4 text-violet-600 dark:text-violet-400" />
        </div>
        <select
          aria-label="Select workspace tab"
          value={activeTab}
          onChange={(e) => setActiveTab(e.target.value)}
          className="w-full appearance-none pl-10 pr-10 py-3 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm font-medium focus:outline-none focus:border-violet-500 cursor-pointer"
        >
          {TAB_ITEMS.map((t) => (
            <option key={t.id} value={t.id}>
              {t.label}
            </option>
          ))}
        </select>
        <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-aegisaxiom-text-secondary pointer-events-none" />
      </div>

      {/* Desktop: classic underline tab strip */}
      <div className="hidden md:block">
        <TabStrip
          variant="underline"
          activeId={activeTab}
          onChange={setActiveTab}
          tabs={TAB_ITEMS}
        />
      </div>
    </>
  );
}
