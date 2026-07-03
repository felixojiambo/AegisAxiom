import React, { useState, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '../../lib/apiConfig';
import ConfirmModal from '../shared/ConfirmModal';
import ResourceAssignmentForm from './ResourceAssignmentForm';
import SolutionsDesignKanban from './SolutionsDesignKanban';
import AlertBanner, { AlertBadge } from '../shared/AlertBanner';
import AutoGrowTextarea from '../shared/AutoGrowTextarea';
import RichProseSection, {
  RegenerateSectionModal,
} from './sow-builder/RichProseSection';
import * as sowReorderHelpers from './sow-builder/reorderHelpers';
import BlockShell from './sow-builder/BlockShell';
import {
  BLOCK_TYPES,
  isReorderable as registryIsReorderable,
  isRemovable as registryIsRemovable,
} from './sow-builder/blockRegistry';
import { useAuth } from '@/contexts/AuthContext';
import {
  FileText,
  Plus,
  Search,
  Save,
  Trash2,
  X,
  ChevronUp,
  ChevronDown,
  Sparkles,
  Building2,
  Mail,
  Phone,
  DollarSign,
  Calendar,
  User,
  AlertCircle,
  Eye,
  Loader,
  TrendingUp,
  Users,
  CheckCircle,
  Clock,
  ArrowRight,
  UserCheck,
  LayoutGrid,
  List as ListIcon,
  Bell,
  Upload,
  ShieldCheck,
  AlertTriangle,
  BookOpen,
  ChevronLeft,
  CircleDot,
  MessageSquare,
  GripVertical,
  ArrowUp,
  ArrowDown,
} from 'lucide-react';

const API = API_URL;

// Resolve any error response body to a single user facing string.
// Backend returns detail as a structured object of shape
// { error_code, message } for Fireflies and SOW drafter failures. Older
// surfaces may still emit a plain string detail. Both shapes work here
// and a missing detail falls back to the literal Unexpected error
// string so the toast is never empty.
//
// Use this at every Fireflies and SOW drafter error surfacing site so a
// structured object does not render as the literal text shown by
// stringifying an object in the toast. Local to this file by intent so
// the cross file utility extraction can wait for a future refactor.
function resolveErrorDetail(err) {
  const detail = err && err.detail;
  if (detail && typeof detail === 'object') {
    return detail.message || detail.error_code || 'Unexpected error';
  }
  return detail || 'Unexpected error';
}


// Translate a structured backend error into a title + description
// pair for toast/notification surfaces. Backend returns
// ``detail = {error_code, message}``; older surfaces may still emit a
// plain-string ``detail``. Both shapes work here.
//
// Keep the error_code values aligned with the constants in:
//   backend/services/fireflies_service.py  FIREFLIES_ERR_*
//   backend/routes/fireflies.py            SOW_DRAFTER_ERR_*
// SOW-drafter codes share this helper because both surfaces feed the
// same "Generate with AI" toast — splitting helpers would force the
// caller to know whether the failure was Fireflies-side or LLM-side
// before parsing the response, which it cannot.
function describeFirefliesError(errorBody, fallbackTitle) {
  const detail = errorBody && errorBody.detail;
  if (detail && typeof detail === 'object') {
    const code = detail.error_code;
    const message = detail.message || fallbackTitle;
    switch (code) {
      case 'fireflies_api_key_missing':
        return {
          title: 'Fireflies.ai is not configured',
          description:
            message ||
            'Ask your admin to set FIREFLIES_API_KEY in the backend environment.',
        };
      case 'transcript_not_found':
        return {
          title: 'Transcript not found',
          description: message,
        };
      case 'transcript_empty':
        return {
          title: 'Transcript has no content',
          description: message,
        };
      case 'fireflies_api_error':
        return {
          title: 'Fireflies.ai request failed',
          description: message,
        };
      case 'sow_drafter_api_key_missing':
        return {
          title: 'SOW AI generation is not configured',
          description:
            message ||
            'Ask your admin to set ANTHROPIC_API_KEY (or the configured SOW drafter key) in the backend environment.',
        };
      case 'sow_drafter_api_error':
        return {
          title: 'SOW AI generation failed',
          description: message,
        };
      default:
        return { title: message || fallbackTitle, description: '' };
    }
  }
  if (typeof detail === 'string') {
    return { title: detail, description: '' };
  }
  return { title: fallbackTitle, description: '' };
}

// Legal statement constant
const LEGAL_STATEMENT =
  "Unless otherwise agreed to in this documents, the Terms and conditions are governed by MetaData Engineering d/b/a SimpliMeta's Master Services Agreement available for review at https://simplimeta.io/legal";

// Configuration constants
const CONTRACT_TYPES = [
  { value: 'boh', label: 'Block of Hours (BOH)' },
  { value: 'boa', label: 'Bill on Actuals (BOA)' },
  { value: 'fixed_fee', label: 'Fixed Fee' },
  { value: 'monthly', label: 'Monthly Retainer' },
  { value: 'wholesale_mrr', label: 'Wholesale MRR' },
];

const LOB_LABELS = {
  field_services: 'Field Services',
  professional_services: 'Professional Services',
  wholesale_mrr: 'Wholesale MRR',
  referral_mrr: 'Referral MRR',
};

const LOB_COLORS = {
  field_services:
    'bg-blue-50 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300 border-blue-500/30',
  professional_services: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
  wholesale_mrr:
    'bg-green-50 dark:bg-green-500/20 text-green-700 dark:text-green-300 border-green-500/30',
  referral_mrr:
    'bg-amber-50 dark:bg-amber-500/20 text-amber-700 dark:text-amber-300 border-amber-500/30',
};

const BILLING_TERMS = [
  { value: 'weekly', label: 'Weekly' },
  { value: 'biweekly', label: 'Bi-weekly' },
  { value: 'monthly', label: 'Monthly' },
];

const PAYMENT_TERMS = [
  { value: 'net_7', label: 'Net 7' },
  { value: 'net_14', label: 'Net 14' },
  { value: 'net_30', label: 'Net 30' },
  { value: 'net_45', label: 'Net 45' },
  { value: 'net_60', label: 'Net 60' },
];

export default function SolutionsDesignTab() {
  const [opportunities, setOpportunities] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedOpportunity, setSelectedOpportunity] = useState(null);
  const [selectedLOB, setSelectedLOB] = useState(null);
  const [sowAlertContext, setSOWAlertContext] = useState(null); // NEW: Track alert context for SOW navigation
  const [view, setView] = useState('list'); // 'list' or 'kanban'
  const [sowTasks, setSOWTasks] = useState([]);
  const [salesOpsUsers, setSalesOpsUsers] = useState([]);
  const [opportunityAlerts, setOpportunityAlerts] = useState({}); // NEW: Store alerts by opportunity_id
  // Stage filter for the workspace. The original loadOpportunities call
  // hardcoded stage=proposal_development which silently hid every SOW
  // and proposal once the parent opportunity moved on. Three options now.
  //   active        the original Proposal Development queue
  //   handoff       Sales Ops handoff stages where the SOW or proposal
  //                 may still need attention even though the
  //                 opportunity has moved past Proposal Development
  //   all           the full Sales Ops surface
  // Default stays active so the existing experience is unchanged.
  const [stageFilter, setStageFilter] = useState('active');

  useEffect(() => {
    loadOpportunities();
    loadSalesOpsUsers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stageFilter]);

  useEffect(() => {
    if (opportunities.length > 0) {
      loadAllSOWTasks();
      loadAllAlerts(); // NEW: Load alerts for all opportunities
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opportunities]);

  // Stage sets the workspace cares about. Centralised so the dropdown
  // labels and the API query stay in sync. The names below are the
  // canonical stage slugs from backend/models.py OPPORTUNITY_STAGES.
  const STAGE_FILTER_SETS = {
    active: ['proposal_development'],
    handoff: [
      'proposal_development',
      'scoping_complete',
      'proposal_sent',
      'negotiation_review',
      'verbal_agreement',
    ],
    all: [
      'prospect_lead',
      'prospect_opp',
      'proposal_development',
      'scoping_complete',
      'proposal_sent',
      'negotiation_review',
      'verbal_agreement',
      'closed_won',
      'closed_lost',
    ],
  };

  const loadOpportunities = async () => {
    try {
      setLoading(true);
      const stages = STAGE_FILTER_SETS[stageFilter] || STAGE_FILTER_SETS.active;
      const stageParam = stages.join(',');
      const response = await api.get(
        `${API}/api/opportunities?stage=${encodeURIComponent(stageParam)}&limit=500`,
      );

      if (response.ok) {
        const data = await response.json();
        setOpportunities(data.items || []);
      }
    } catch (error) {
      console.error('Error loading opportunities:', error);
      toast.error('Failed to load opportunities');
    } finally {
      setLoading(false);
    }
  };
  const handleDeleteOpportunity = async (opportunityId, opportunityName) => {
    if (!window.confirm(`Delete "${opportunityName}"? This cannot be undone.`)) return;
    try {
      const response = await api.delete(`${API}/api/opportunities/${opportunityId}`);
      if (response.ok) {
        toast.success(`"${opportunityName}" deleted`);
        loadOpportunities();
      } else {
        const err = await response.json();
        toast.error(err.detail || 'Failed to delete opportunity');
      }
    } catch (error) {
      console.error('Error deleting opportunity:', error);
      toast.error('Failed to delete opportunity');
    }
  };
  const loadSalesOpsUsers = async () => {
    try {
      const response = await api.get(`${API}/api/sales-ops/sales-ops-users`);
      if (response.ok) {
        const data = await response.json();
        setSalesOpsUsers(data.items || data || []);
      }
    } catch (error) {
      console.error('Error loading sales ops users:', error);
    }
  };

  // I2. Fan out the per opportunity SOW task fetches in parallel so the
  // initial page load is bounded by the slowest request rather than the
  // sum of every request. A per opportunity try catch keeps one failed
  // call from killing the whole page and a single aggregate toast names
  // the failure count instead of spamming one toast per row.
  const loadAllSOWTasks = async () => {
    if (!opportunities || opportunities.length === 0) {
      setSOWTasks([]);
      return;
    }
    const results = await Promise.all(
      opportunities.map(async (opp) => {
        try {
          const response = await api.get(
            `${API}/api/sales-ops/opportunities/${opp.opportunity_id}/sow-tasks`,
          );
          if (!response.ok) {
            return { opp, tasks: [], error: new Error(`HTTP ${response.status}`) };
          }
          const data = await response.json();
          const tasks = (data.sow_tasks || []).map((sow) => ({
            ...sow,
            opportunity_name: opp.name,
            opportunity_id: opp.opportunity_id,
            desired_proposal_date: opp.desired_proposal_date,
          }));
          return { opp, tasks, error: null };
        } catch (err) {
          return { opp, tasks: [], error: err };
        }
      }),
    );
    const allSOWs = [];
    let failed = 0;
    for (const row of results) {
      allSOWs.push(...row.tasks);
      if (row.error) {
        failed += 1;
        console.error('Error loading SOW tasks for opportunity', row.opp.opportunity_id, row.error);
      }
    }
    setSOWTasks(allSOWs);
    if (failed > 0) {
      toast.error(`Failed to load SOW tasks for ${failed} of ${results.length} opportunities`);
    }
  };

  const loadAllAlerts = async () => {
    if (!opportunities || opportunities.length === 0) {
      setOpportunityAlerts({});
      return;
    }
    const results = await Promise.all(
      opportunities.map(async (opp) => {
        try {
          const response = await api.get(
            `${API}/api/sales-ops/opportunities/${opp.opportunity_id}/alerts`,
          );
          if (!response.ok) {
            return { opp, items: [], error: new Error(`HTTP ${response.status}`) };
          }
          const data = await response.json();
          return { opp, items: data.items || [], error: null };
        } catch (err) {
          return { opp, items: [], error: err };
        }
      }),
    );
    const alertsMap = {};
    let failed = 0;
    for (const row of results) {
      alertsMap[row.opp.opportunity_id] = row.items;
      if (row.error) {
        failed += 1;
        console.error('Error loading alerts for opportunity', row.opp.opportunity_id, row.error);
      }
    }
    setOpportunityAlerts(alertsMap);
    if (failed > 0) {
      toast.error(`Failed to load alerts for ${failed} of ${results.length} opportunities`);
    }
  };

  const handleDismissAlert = async (alertId) => {
    try {
      const response = await api.patch(`${API}/api/sales-ops/alerts/${alertId}/dismiss`);
      if (response.ok) {
        toast.success('Alert dismissed');
        loadAllAlerts();
      }
    } catch (error) {
      console.error('Error dismissing alert:', error);
      toast.error('Failed to dismiss alert');
    }
  };

  const handleAcknowledgeAlert = async (alertId) => {
    try {
      const response = await api.patch(`${API}/api/sales-ops/alerts/${alertId}/acknowledge`);
      if (response.ok) {
        toast.success('Alert acknowledged');
        loadAllAlerts();
      }
    } catch (error) {
      console.error('Error acknowledging alert:', error);
      toast.error('Failed to acknowledge alert');
    }
  };

  const handleSOWStatusChange = async (sowId, newStatus) => {
    try {
      const sow = sowTasks.find((s) => s.sow_id === sowId);
      if (!sow) return;

      const response = await api.patch(
        `${API}/api/sales-ops/opportunities/${sow.opportunity_id}/sows/${sowId}/status?status=${newStatus}`,
      );

      if (response.ok) {
        toast.success('Status updated');
        loadAllSOWTasks();
      } else {
        const errorData = await response.json();
        toast.error(errorData.detail || 'Failed to update status');
      }
    } catch (error) {
      console.error('Error updating SOW status:', error);
      toast.error('Failed to update status');
    }
  };

  const handleAssignSOW = async (sowId, userId) => {
    try {
      const sow = sowTasks.find((s) => s.sow_id === sowId);
      if (!sow) return;

      const response = await api.patch(
        `${API}/api/sales-ops/opportunities/${sow.opportunity_id}/sows/${sowId}/assign?assigned_to=${userId}`,
      );

      if (response.ok) {
        const data = await response.json();
        toast.success(`SOW assigned to ${data.assigned_to_name}`);
        loadAllSOWTasks();
      } else {
        const errorData = await response.json();
        toast.error(errorData.detail || 'Failed to assign SOW');
      }
    } catch (error) {
      console.error('Error assigning SOW:', error);
      toast.error('Failed to assign SOW');
    }
  };

  const handleViewSOWFromKanban = (sow) => {
    // Directly open SOW Builder with auto-navigation to problem tab
    const opp = opportunities.find((o) => o.opportunity_id === sow.opportunity_id);
    if (!opp) {
      // Without this branch the click was a silent no-op: the kanban
      // can show a SOW whose opportunity has slipped out of the current
      // page (stage change, pagination, etc.) and the modal would never
      // mount because selectedOpportunity stayed null.
      toast.error(
        'Could not open this SOW — the underlying opportunity is no longer in Proposal Development. Refresh the page to resync.',
      );
      return;
    }

    // Detect blocker category and map to tab
    let initialTab = 'smart'; // default
    let alertContext = null;

    // Check blocker messages
    const blockers = sow.blockers || [];
    const alerts = opportunityAlerts[sow.opportunity_id] || [];
    const sowAlerts = alerts.filter((a) => a.sow_id === sow.sow_id);

    // Map blocker/alert types to tabs
    if (blockers.some((b) => b.includes('Missing contractor') || b.includes('margin'))) {
      initialTab = 'resources';
      alertContext = {
        type: 'blocker',
        messages: blockers,
      };
    } else if (sowAlerts.some((a) => a.rule_name.includes('Margin'))) {
      initialTab = 'resources';
      alertContext = {
        type: 'alert',
        alerts: sowAlerts.filter((a) => a.rule_name.includes('Margin')),
      };
    } else if (blockers.some((b) => b.includes('billing') || b.includes('payment'))) {
      initialTab = 'billing';
      alertContext = {
        type: 'blocker',
        messages: blockers,
      };
    } else if (sowAlerts.length > 0) {
      // Generic alerts - check functional area
      const firstAlert = sowAlerts[0];
      if (
        firstAlert.rule_name.includes('Contractor') ||
        firstAlert.rule_name.includes('Resource')
      ) {
        initialTab = 'resources';
      } else if (firstAlert.rule_name.includes('Billing')) {
        initialTab = 'billing';
      }
      alertContext = {
        type: 'alert',
        alerts: sowAlerts,
      };
    }

    // line_of_business may legitimately be null on legacy SOW rows. The
    // parent gate `selectedOpportunity && selectedLOB` would silently
    // refuse to mount the builder in that case, so guard explicitly and
    // surface the reason instead of failing silently.
    if (!sow.line_of_business) {
      toast.error(
        'This SOW is missing a line of business and cannot be opened. Edit the parent opportunity to set its lines_of_business, then try again.',
      );
      return;
    }

    setSelectedOpportunity(opp);
    setSelectedLOB(sow.line_of_business);
    setSOWAlertContext({ initialTab, alertContext });
  };

  const filteredOpportunities = opportunities.filter((opp) => {
    if (!searchQuery) return true;
    const search = searchQuery.toLowerCase();
    return (
      opp.name?.toLowerCase().includes(search) || opp.account_name?.toLowerCase().includes(search)
    );
  });

  return (
    <div>
      {/* Search & Toolbar */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
        <div className="relative w-full sm:w-72 lg:w-80">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-aegisaxiom-text-secondary" />
          <input
            type="text"
            placeholder="Search opportunities..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 text-sm"
          />
        </div>

        <div className="flex items-center justify-between sm:justify-end gap-3 sm:gap-4 shrink-0">
          {/* Stage filter. Originally hidden behind a hardcoded
              proposal_development query. The handoff option keeps
              SOW and proposal records reachable after the parent
              opportunity has moved on. */}
          <select
            value={stageFilter}
            onChange={(e) => setStageFilter(e.target.value)}
            aria-label="Opportunity stage filter"
            className="px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
            data-testid="sales-ops-stage-filter"
          >
            <option value="active">Active — Proposal Dev</option>
            <option value="handoff">Sales Ops handoff (incl. Scoping Complete / Proposal Sent)</option>
            <option value="all">All stages</option>
          </select>
          {/* View Toggle */}
          <div className="flex items-center gap-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-1">
            <button
              onClick={() => setView('list')}
              className={`px-3 py-1.5 rounded flex items-center gap-2 text-sm transition ${
                view === 'list'
                  ? 'bg-violet-600 text-white'
                  : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
              }`}
            >
              <ListIcon className="w-4 h-4" />
              <span>List</span>
            </button>
            <button
              onClick={() => setView('kanban')}
              className={`px-3 py-1.5 rounded flex items-center gap-2 text-sm transition ${
                view === 'kanban'
                  ? 'bg-violet-600 text-white'
                  : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
              }`}
            >
              <LayoutGrid className="w-4 h-4" />
              <span>Kanban</span>
            </button>
          </div>

          <div className="text-xs sm:text-sm text-aegisaxiom-text-secondary whitespace-nowrap">
            {view === 'list'
              ? `${filteredOpportunities.length} ${filteredOpportunities.length === 1 ? 'opp' : 'opps'}`
              : `${sowTasks.length} SOW${sowTasks.length !== 1 ? 's' : ''}`}
          </div>
        </div>
      </div>

      {/* Kanban View */}
      {view === 'kanban' &&
        (loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-violet-500"></div>
          </div>
        ) : sowTasks.length === 0 ? (
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-12 text-center">
            <FileText className="w-16 h-16 text-aegisaxiom-text-muted mx-auto mb-4" />
            <h3 className="text-lg font-semibold text-aegisaxiom-text mb-2">No SOW Tasks</h3>
            <p className="text-aegisaxiom-text-secondary text-sm">
              SOW tasks will appear here when opportunities reach Proposal Development stage
            </p>
          </div>
        ) : (
          <SolutionsDesignKanban
            sowTasks={sowTasks}
            salesOpsUsers={salesOpsUsers}
            opportunityAlerts={opportunityAlerts}
            onStatusChange={handleSOWStatusChange}
            onAssignSOW={handleAssignSOW}
            onViewSOW={handleViewSOWFromKanban}
          />
        ))}

      {/* List View (Opportunity Cards) */}
      {view === 'list' &&
        (loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-violet-500"></div>
          </div>
        ) : filteredOpportunities.length === 0 ? (
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-12 text-center">
            <FileText className="w-16 h-16 text-aegisaxiom-text-muted mx-auto mb-4" />
            <h3 className="text-lg font-semibold text-aegisaxiom-text mb-2">No Opportunities</h3>
            <p className="text-aegisaxiom-text-secondary text-sm">
              {searchQuery
                ? 'No opportunities match your search'
                : 'Opportunities will appear here when they reach Proposal Development stage'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filteredOpportunities.map((opp) => (
              <OpportunityCard
                key={opp.opportunity_id}
                opportunity={opp}
                alerts={opportunityAlerts[opp.opportunity_id] || []}
                onSelect={() => setSelectedOpportunity(opp)}
                onDelete={() => handleDeleteOpportunity(opp.opportunity_id, opp.name)}
              />
            ))}
          </div>
        ))}

      {/* Opportunity Detail Modal */}
      {selectedOpportunity && !selectedLOB && (
        <OpportunityDetailModal
          opportunity={selectedOpportunity}
          onClose={() => setSelectedOpportunity(null)}
          onUpdate={loadOpportunities}
        />
      )}

      {/* SOW Builder Modal (Direct from Kanban) */}
      {selectedOpportunity && selectedLOB && (
        <SOWBuilderModal
          opportunity={selectedOpportunity}
          lob={selectedLOB}
          alertContext={sowAlertContext}
          onClose={() => {
            setSelectedOpportunity(null);
            setSelectedLOB(null);
            setSOWAlertContext(null);
            loadAllSOWTasks(); // Refresh Kanban
          }}
          onBack={() => {
            setSelectedLOB(null); // Go back to opportunity detail
            setSOWAlertContext(null);
          }}
        />
      )}
    </div>
  );
}

// Opportunity Card Component

function OpportunityCard({ opportunity: opp, onSelect, onDelete, alerts = [] }) {
  const { user } = useAuth();
  const canDelete = user?.role === 'admin' || user?.role === 'director';
  const [showAlertModal, setShowAlertModal] = useState(false);
  const [selectedSeverity, setSelectedSeverity] = useState(null);

  const formatCurrency = (amount) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
    }).format(amount || 0);
  };

  const formatRelative = (dateString) => {
    if (!dateString) return null;
    const d = new Date(dateString);
    if (isNaN(d.getTime())) return null;
    const diffMs = Date.now() - d.getTime();
    const mins = Math.round(diffMs / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.round(hours / 24);
    if (days < 30) return `${days}d ago`;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  };

  const lobs = opp.lines_of_business || [];

  // Sales Ops stage chip
  const SALES_OPS_STAGE_COLORS = {
    discovery: 'bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 border-blue-500/30',
    designing:
      'bg-purple-50 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 border-purple-500/30',
    procurement:
      'bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 border-amber-500/30',
    review:
      'bg-violet-50 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300 border-violet-500/30',
    complete:
      'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300 border-green-500/30',
  };
  const SALES_OPS_STAGE_LABELS = {
    discovery: 'Discovery',
    designing: 'Designing',
    procurement: 'Procurement',
    review: 'Review',
    complete: 'Complete',
  };
  const stage = opp.sales_ops_stage || 'discovery';

  // Probability bar color scales with value
  const prob = opp.probability || 0;
  const probBarColor =
    prob >= 75 ? 'bg-green-500' : prob >= 40 ? 'bg-amber-500' : 'bg-aegisaxiom-border-hover';

  // Count alerts by severity
  const criticalAlerts = alerts.filter((a) => a.severity === 'critical' && !a.dismissed);
  const warningAlerts = alerts.filter((a) => a.severity === 'warning' && !a.dismissed);
  const successAlerts = alerts.filter((a) => a.severity === 'success' && !a.dismissed);

  const handleBadgeClick = (e, severity) => {
    e.stopPropagation();
    setSelectedSeverity(severity);
    setShowAlertModal(true);
  };

  const filteredAlerts = selectedSeverity
    ? alerts.filter((a) => a.severity === selectedSeverity && !a.dismissed)
    : alerts.filter((a) => !a.dismissed);

  const updatedLabel = formatRelative(opp.updated_at || opp.created_at);

  return (
    <>
      <div
        onClick={onSelect}
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-4 hover:border-violet-500 hover:shadow-sm transition cursor-pointer flex flex-col"
      >
        {/* Header: stage chip + alert badges */}
        <div className="flex items-center justify-between gap-2 mb-3">
          <span
            className={`px-2 py-0.5 rounded text-[10px] font-medium border uppercase tracking-wider ${SALES_OPS_STAGE_COLORS[stage]}`}
          >
            {SALES_OPS_STAGE_LABELS[stage]}
          </span>

          <div className="flex gap-1.5 shrink-0">
            {criticalAlerts.length > 0 && (
              <div
                onClick={(e) => handleBadgeClick(e, 'critical')}
                className="cursor-pointer hover:opacity-80 transition"
              >
                <AlertBadge severity="critical" count={criticalAlerts.length} />
              </div>
            )}
            {warningAlerts.length > 0 && (
              <div
                onClick={(e) => handleBadgeClick(e, 'warning')}
                className="cursor-pointer hover:opacity-80 transition"
              >
                <AlertBadge severity="warning" count={warningAlerts.length} />
              </div>
            )}
            {successAlerts.length > 0 && (
              <div
                onClick={(e) => handleBadgeClick(e, 'success')}
                className="cursor-pointer hover:opacity-80 transition"
              >
                <AlertBadge severity="success" count={successAlerts.length} />
              </div>
            )}
          </div>
        </div>

        {/* Title + account */}
        <div className="mb-3">
          <h3 className="font-semibold text-aegisaxiom-text text-sm sm:text-base leading-snug mb-1.5 line-clamp-2">
            {opp.name}
          </h3>
          <div className="flex items-center gap-1.5 text-xs text-aegisaxiom-text-secondary min-w-0">
            <Building2 className="w-3.5 h-3.5 shrink-0" />
            <span className="truncate">{opp.account_name}</span>
          </div>
        </div>

        {/* Lines of business */}
        {lobs.length > 0 && (
          <div className="flex items-center gap-1.5 mb-3 flex-wrap">
            {lobs.map((lob) => (
              <span
                key={lob}
                className={`px-1.5 py-0.5 rounded text-[10px] font-medium border ${LOB_COLORS[lob]}`}
              >
                {LOB_LABELS[lob]}
              </span>
            ))}
          </div>
        )}

        {/* Value + probability bar */}
        <div className="mb-3">
          <div className="flex items-baseline justify-between mb-1.5">
            <span className="text-green-600 dark:text-green-400 font-bold text-base tabular-nums">
              {formatCurrency(opp.amount)}
            </span>
            <span className="text-[11px] text-aegisaxiom-text-muted tabular-nums">
              {prob}% probability
            </span>
          </div>
          <div className="h-1 w-full bg-aegisaxiom-elevated rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${probBarColor}`}
              style={{ width: `${Math.max(2, prob)}%` }}
            />
          </div>
        </div>

        {/* Last-activity footer + actions */}
        <div className="mt-auto pt-3 border-t border-aegisaxiom-border flex items-center justify-between gap-2">
          {updatedLabel && (
            <div className="flex items-center gap-1 text-[11px] text-aegisaxiom-text-muted min-w-0">
              <Clock className="w-3 h-3 shrink-0" />
              <span className="truncate">Updated {updatedLabel}</span>
            </div>
          )}
          <div className="flex gap-1.5 shrink-0 ml-auto">
            <button
              onClick={(e) => {
                e.stopPropagation();
                onSelect();
              }}
              className="px-3 py-1.5 bg-violet-600 hover:bg-violet-500 text-white text-xs font-medium rounded transition flex items-center gap-1.5"
            >
              <Eye className="w-3.5 h-3.5" />
              View
            </button>
            {canDelete && onDelete && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete();
                }}
                className="p-1.5 bg-aegisaxiom-elevated hover:bg-red-600 text-aegisaxiom-text-secondary hover:text-white rounded transition"
                title="Delete opportunity"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Alert Modal (Quick View) */}
      {showAlertModal && (
        <div
          className="fixed inset-0 bg-black/70 flex items-center justify-center z-[100]"
          onClick={(e) => {
            e.stopPropagation();
            setShowAlertModal(false);
          }}
        >
          <div
            className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-2xl m-4 max-h-[80vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="bg-gradient-to-r from-violet-600/20 to-purple-600/20 border-b border-aegisaxiom-border p-4">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="text-lg font-bold text-aegisaxiom-text mb-1">{opp.name}</h3>
                  <p className="text-sm text-aegisaxiom-text-secondary">{opp.account_name}</p>
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowAlertModal(false);
                  }}
                  className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            <div className="p-4 space-y-3">
              <h4 className="text-sm font-semibold text-aegisaxiom-text mb-2">
                {selectedSeverity
                  ? `${selectedSeverity.charAt(0).toUpperCase() + selectedSeverity.slice(1)} Alerts (${filteredAlerts.length})`
                  : `All Alerts (${filteredAlerts.length})`}
              </h4>
              {filteredAlerts.length === 0 ? (
                <p className="text-aegisaxiom-text-muted text-sm text-center py-4">No alerts found</p>
              ) : (
                filteredAlerts.map((alert) => (
                  <AlertBanner
                    key={alert.alert_id}
                    alert={alert}
                    compact={false}
                    showActions={false}
                  />
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// Opportunity Detail Modal
function OpportunityDetailModal({ opportunity, onClose, onUpdate }) {
  const [sowTasks, setSOWTasks] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selectedLOB, setSelectedLOB] = useState(null);
  const [salesOpsUsers, setSalesOpsUsers] = useState([]);
  const [alerts, setAlerts] = useState([]); // NEW: Alerts for this opportunity
  const [sowAlertContext, setSOWAlertContext] = useState(null); // Track alert context for SOW navigation
  const [notes, setNotes] = useState([]);
  const [proposals, setProposals] = useState([]);
  const [proposalsLoading, setProposalsLoading] = useState(false);
  // I3. Inline email modal state. Replaces the blocking window.prompt
  // for the send recipient. The dialog stays open across re renders by
  // keying off proposalId. lastFocusedEl captures the element that
  // triggered the dialog so focus returns to it when the dialog closes.
  const [sendDialogProposalId, setSendDialogProposalId] = useState(null);
  const [sendDialogLastFocused, setSendDialogLastFocused] = useState(null);

  useEffect(() => {
    loadSOWTasks();
    loadSalesOpsUsers();
    loadAlerts();
    loadNotes();
    loadProposals();
  }, [opportunity.opportunity_id]);

  const loadSOWTasks = async () => {
    try {
      setLoading(true);
      const response = await api.get(
        `${API}/api/sales-ops/opportunities/${opportunity.opportunity_id}/sow-tasks`,
      );

      if (response.ok) {
        const data = await response.json();
        setSOWTasks(data);
      }
    } catch (error) {
      console.error('Error loading SOW tasks:', error);
      toast.error('Failed to load SOW tasks');
    } finally {
      setLoading(false);
    }
  };

  const loadSalesOpsUsers = async () => {
    try {
      const response = await api.get(`${API}/api/sales-ops/sales-ops-users`);
      if (response.ok) {
        const data = await response.json();
        setSalesOpsUsers(data.items || []);
      }
    } catch (error) {
      console.error('Error loading sales ops users:', error);
    }
  };

  const loadAlerts = async () => {
    try {
      const response = await api.get(
        `${API}/api/sales-ops/opportunities/${opportunity.opportunity_id}/alerts`,
      );
      if (response.ok) {
        const data = await response.json();
        setAlerts(data.items || []);
      }
    } catch (error) {
      console.error('Error loading alerts:', error);
    }
  };
  const loadNotes = async () => {
    try {
      const response = await api.get(`${API}/api/opportunities/${opportunity.opportunity_id}`);
      if (response.ok) {
        const data = await response.json();
        setNotes(data.notes || []);
      }
    } catch (error) {
      console.error('Error loading notes:', error);
    }
  };

  const loadProposals = async () => {
    try {
      setProposalsLoading(true);
      const response = await api.get(
        `${API}/api/sales-ops/opportunities/${opportunity.opportunity_id}/proposals`,
      );
      if (response.ok) {
        const data = await response.json();
        setProposals(data.items || []);
      } else if (response.status !== 403) {
        // 403 means the user just can't see proposals — that's fine, leave
        // the list empty. Anything else is an unexpected error worth toasting.
        toast.error('Failed to load proposals');
      }
    } catch (error) {
      console.error('Error loading proposals:', error);
    } finally {
      setProposalsLoading(false);
    }
  };

  const [generatingProposal, setGeneratingProposal] = useState(false);

  const handleGenerateProposal = async () => {
    // Eligibility mirrors the backend's _SOW_READY_STATUSES gate. The
    // backend re-validates — this is a UX guard so we don't even make
    // the round-trip when nothing is ready.
    const readySows = (sowTasks?.sow_tasks || []).filter(
      (s) => s.status === 'ready_for_review' || s.status === 'complete',
    );
    if (readySows.length === 0) {
      toast.error(
        'No SOW is ready yet. Move at least one SOW to Ready for Review or Complete before generating a proposal.',
      );
      return;
    }
    try {
      setGeneratingProposal(true);
      const response = await api.post(
        `${API}/api/sales-ops/opportunities/${opportunity.opportunity_id}/proposal`,
        {
          sow_ids: readySows.map((s) => s.sow_id),
        },
      );
      if (response.ok) {
        toast.success('Proposal draft generated');
        loadProposals();
      } else {
        let detail = `Failed to create proposal (HTTP ${response.status}).`;
        try {
          const errBody = await response.json();
          if (errBody && typeof errBody.detail === 'string') detail = errBody.detail;
        } catch (_e) {
          // body wasn't JSON; keep the HTTP fallback
        }
        toast.error(detail);
      }
    } catch (error) {
      console.error('Error creating proposal:', error);
      toast.error('Failed to create proposal');
    } finally {
      setGeneratingProposal(false);
    }
  };

  const handleRegenerateProposal = async (proposalId) => {
    if (
      !window.confirm(
        'Regenerate proposal content from the latest SOW data? Any hand-edited content will be overwritten.',
      )
    ) {
      return;
    }
    try {
      const response = await api.post(
        `${API}/api/sales-ops/proposals/${proposalId}/regenerate`,
        {},
      );
      if (response.ok) {
        // The regenerate response carries three possible outcomes,
        // surfaced as policy-aligned toasts. See
        // docs/sales-ops/PROPOSAL_LIFECYCLE_V2.md.
        //   1. no_op: source unchanged, content was not rewritten.
        //   2. drift_detected: AI polish changed factual numbers and
        //      AEGISAXIOM kept the deterministic skeleton.
        //   3. default: content was successfully regenerated.
        let body = {};
        try {
          body = await response.json();
        } catch (_e) {
          // Tolerate a non-JSON success body.
        }
        if (body && body.no_op) {
          toast.success('Proposal already up to date.');
        } else if (body && body.drift_detected) {
          toast.success('AI polish changed proposal numbers; safe version kept.');
        } else {
          toast.success('Proposal regenerated.');
        }
        loadProposals();
      } else {
        let detail = `Failed to regenerate proposal (HTTP ${response.status}).`;
        try {
          const errBody = await response.json();
          if (errBody && typeof errBody.detail === 'string') detail = errBody.detail;
        } catch (_e) {
          // body wasn't JSON; keep the HTTP fallback
        }
        toast.error(detail);
      }
    } catch (error) {
      console.error('Error regenerating proposal:', error);
      toast.error('Failed to regenerate proposal.');
    }
  };

  const handleEditProposal = async (proposalId, patch) => {
    try {
      const response = await api.patch(
        `${API}/api/sales-ops/proposals/${proposalId}`,
        patch,
      );
      if (response.ok) {
        toast.success('Proposal updated');
        loadProposals();
        return true;
      }
      let detail = `Failed to update proposal (HTTP ${response.status}).`;
      try {
        const errBody = await response.json();
        if (errBody && typeof errBody.detail === 'string') detail = errBody.detail;
      } catch (_e) {
        // body wasn't JSON; keep the HTTP fallback
      }
      toast.error(detail);
      return false;
    } catch (error) {
      console.error('Error updating proposal:', error);
      toast.error('Failed to update proposal');
      return false;
    }
  };

  const handleAttachProposalDoc = async (proposalId, url) => {
    if (!url || !url.trim()) return;
    try {
      const response = await api.patch(`${API}/api/sales-ops/proposals/${proposalId}`, {
        google_doc_url: url.trim(),
      });
      if (response.ok) {
        toast.success('Linked Google Doc.');
        loadProposals();
      } else {
        // Hostname allowlist + URL validation rejections come back
        // with a fixed detail string. Surface an actionable, URL
        // -specific message rather than the generic save-failed
        // fallback. See
        // docs/sales-ops/GOOGLE_DOC_ARTIFACT_PATH.md.
        let body = {};
        try {
          body = await response.json();
        } catch (_e) {
          // body wasn't JSON
        }
        const detail = body && body.detail;
        const isHostError =
          typeof detail === 'string' && detail.toLowerCase().includes('allowlist');
        toast.error(
          isHostError
            ? 'Enter a valid Google Docs URL.'
            : 'Failed to save Google Doc URL.',
        );
      }
    } catch (error) {
      console.error('Error attaching proposal doc:', error);
      toast.error('Failed to save Google Doc URL.');
    }
  };

  // I3. The send trigger now opens an inline dialog that collects the
  // recipient through a controlled input. The actual POST happens in
  // submitSendProposal once the user picks one of the two paths in the
  // dialog. window.prompt was inaccessible, suppressible, and offered
  // no mobile support or input validation.
  const handleSendProposal = (proposalId) => {
    setSendDialogLastFocused(typeof document !== 'undefined' ? document.activeElement : null);
    setSendDialogProposalId(proposalId);
  };

  const closeSendDialog = () => {
    const restore = sendDialogLastFocused;
    setSendDialogProposalId(null);
    setSendDialogLastFocused(null);
    // Return focus to the element that opened the dialog so keyboard
    // navigation remains stable across the modal lifecycle.
    if (restore && typeof restore.focus === 'function') {
      setTimeout(() => {
        try {
          restore.focus();
        } catch (_focusError) {
          // restore can be detached by the time the timeout fires.
        }
      }, 0);
    }
  };

  const submitSendProposal = async (proposalId, recipient) => {
    try {
      const response = await api.post(`${API}/api/sales-ops/proposals/${proposalId}/send`, {
        sent_to_email: recipient || null,
      });
      if (response.ok) {
        toast.success('Proposal handed off to Sales.');
        loadProposals();
      } else {
        let detail = `Failed to hand off. HTTP ${response.status}`;
        try {
          const errBody = await response.json();
          if (errBody && typeof errBody.detail === 'string') detail = errBody.detail;
        } catch (_parseError) {
          // body was not JSON. Keep the HTTP status fallback.
        }
        toast.error(detail);
      }
    } catch (error) {
      console.error('Error handing off proposal:', error);
      toast.error('Failed to hand off proposal.');
    } finally {
      closeSendDialog();
    }
  };

  // Mark client sent. Sales-owned action that records the external
  // delivery already happened outside AEGISAXIOM. See policy doc at
  // docs/sales-ops/PROPOSAL_HANDOFF_AND_SEND_POLICY.md. No external
  // email or signing tool integration is performed.
  const handleMarkClientSent = async (proposalId) => {
    try {
      const response = await api.post(
        `${API}/api/sales-ops/proposals/${proposalId}/mark-client-sent`,
        {},
      );
      if (response.ok) {
        toast.success('Proposal marked as sent to client.');
        loadProposals();
      } else {
        let detail = `Failed to mark client sent. HTTP ${response.status}`;
        try {
          const errBody = await response.json();
          if (errBody && typeof errBody.detail === 'string') detail = errBody.detail;
        } catch (_parseError) {
          // body was not JSON. Keep the HTTP status fallback.
        }
        toast.error(detail);
      }
    } catch (error) {
      console.error('Error marking client sent:', error);
      toast.error('Failed to mark client sent.');
    }
  };

  const handleProposalStatusChange = async (proposalId, status) => {
    try {
      const response = await api.patch(`${API}/api/sales-ops/proposals/${proposalId}`, { status });
      if (response.ok) {
        toast.success('Proposal updated');
        loadProposals();
      } else {
        let detail = 'Failed to update proposal';
        try {
          const errBody = await response.json();
          if (errBody && typeof errBody.detail === 'string') detail = errBody.detail;
        } catch (_e) {
          // body wasn't JSON; keep the fallback
        }
        toast.error(detail);
      }
    } catch (error) {
      console.error('Error updating proposal:', error);
      toast.error('Failed to update proposal');
    }
  };
  const handleDismissAlert = async (alertId) => {
    try {
      const response = await api.patch(`${API}/api/sales-ops/alerts/${alertId}/dismiss`);
      if (response.ok) {
        toast.success('Alert dismissed');
        loadAlerts();
      }
    } catch (error) {
      console.error('Error dismissing alert:', error);
      toast.error('Failed to dismiss alert');
    }
  };

  const handleAcknowledgeAlert = async (alertId) => {
    try {
      const response = await api.patch(`${API}/api/sales-ops/alerts/${alertId}/acknowledge`);
      if (response.ok) {
        toast.success('Alert acknowledged');
        loadAlerts();
      }
    } catch (error) {
      console.error('Error acknowledging alert:', error);
      toast.error('Failed to acknowledge alert');
    }
  };

  const assignSOW = async (lob, userId) => {
    try {
      const response = await api.post(
        `${API}/api/sales-ops/opportunities/${opportunity.opportunity_id}/sow-tasks/${lob}/assign?assigned_to=${userId}`,
        {},
      );

      if (response.ok) {
        toast.success('SOW task assigned');
        loadSOWTasks();
      } else {
        toast.error('Failed to assign SOW');
      }
    } catch (error) {
      console.error('Error assigning SOW:', error);
      toast.error('Failed to assign SOW');
    }
  };

  const formatCurrency = (amount) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
    }).format(amount || 0);
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return 'Not set';
    return new Date(dateStr).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  };

  if (loading) {
    return (
      <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-violet-500"></div>
      </div>
    );
  }

  // If single LOB and SOW builder is open
  if (selectedLOB) {
    return (
      <SOWBuilderModal
        opportunity={opportunity}
        lob={selectedLOB}
        alertContext={sowAlertContext}
        onClose={() => {
          setSelectedLOB(null);
          setSOWAlertContext(null);
          onClose(); // Close the parent modal
        }}
        onBack={() => {
          setSelectedLOB(null);
          setSOWAlertContext(null);
        }}
      />
    );
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-5xl my-8">
        {/* Header */}
        <div className="bg-gradient-to-r from-violet-600/20 to-purple-600/20 border-b border-aegisaxiom-border p-6">
          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-2xl font-bold text-aegisaxiom-text mb-2">{opportunity.name}</h2>
              <div className="flex items-center gap-4 text-sm text-aegisaxiom-text-secondary">
                <span className="flex items-center gap-1">
                  <Building2 className="w-4 h-4" />
                  {opportunity.account_name}
                </span>
                <span className="flex items-center gap-1">
                  <DollarSign className="w-4 h-4 text-green-600 dark:text-green-400" />
                  <span className="text-green-600 dark:text-green-400 font-semibold">
                    {formatCurrency(opportunity.amount)}
                  </span>
                </span>
                <span className="flex items-center gap-1">
                  <TrendingUp className="w-4 h-4" />
                  {opportunity.probability || 0}% probability
                </span>
              </div>
            </div>
            <button
              onClick={onClose}
              className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
            >
              <X className="w-6 h-6" />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="p-6">
          {/* Opportunity Details Grid */}
          <div className="grid grid-cols-2 gap-6 mb-6 pb-6 border-b border-aegisaxiom-border">
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Owner</label>
              <p className="text-aegisaxiom-text">{opportunity.opportunity_owner_name || 'Unassigned'}</p>
            </div>
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                Expected Close Date
              </label>
              <p className="text-aegisaxiom-text">{formatDate(opportunity.expected_close_date)}</p>
            </div>
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                Desired Proposal Date
              </label>
              <p className="text-aegisaxiom-text">{formatDate(opportunity.desired_proposal_date)}</p>
            </div>
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                SOW Expected Completion
              </label>
              <div className="flex flex-wrap gap-2">
                {sowTasks?.sow_tasks?.some((s) => s.expected_completion_date) ? (
                  sowTasks.sow_tasks
                    .filter((s) => s.expected_completion_date)
                    .map((s) => {
                      const isLate =
                        opportunity.desired_proposal_date &&
                        new Date(s.expected_completion_date) >
                          new Date(opportunity.desired_proposal_date);
                      return (
                        <div key={s.sow_id} className="flex items-center gap-1">
                          <span
                            className={`text-xs px-2 py-0.5 rounded border ${LOB_COLORS[s.line_of_business]}`}
                          >
                            {LOB_LABELS[s.line_of_business].split(' ')[0]}:
                          </span>
                          <span
                            className={
                              isLate
                                ? 'text-yellow-600 dark:text-yellow-400 font-semibold text-sm'
                                : 'text-aegisaxiom-text text-sm'
                            }
                          >
                            {new Date(s.expected_completion_date).toLocaleDateString()}
                          </span>
                          {isLate && (
                            <AlertCircle
                              className="w-3 h-3 text-yellow-600 dark:text-yellow-400"
                              title="After desired proposal date"
                            />
                          )}
                        </div>
                      );
                    })
                ) : (
                  <p className="text-aegisaxiom-text-muted text-sm">Not set</p>
                )}
              </div>
            </div>
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Stage</label>
              <p className="text-aegisaxiom-text capitalize">{opportunity.stage?.replace('_', ' ')}</p>
            </div>
          </div>

          {/* Sales Notes Section */}
          {notes.length > 0 && (
            <div className="mb-6 pb-6 border-b border-aegisaxiom-border">
              <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
                <MessageSquare className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                Sales Notes ({notes.length})
              </h3>
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {[...notes].reverse().map((note) => (
                  <div
                    key={note.note_id}
                    className="p-3 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg"
                  >
                    <p className="text-sm text-aegisaxiom-text">{note.text}</p>
                    <p className="text-xs text-aegisaxiom-text-muted mt-1">
                      {note.created_by_name} •{' '}
                      {note.created_at
                        ? new Date(note.created_at).toLocaleString('en-US', {
                            month: 'short',
                            day: 'numeric',
                            hour: 'numeric',
                            minute: '2-digit',
                          })
                        : ''}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Alerts Section (NEW) */}
          {alerts.length > 0 && (
            <div className="mb-6 space-y-2">
              <h3 className="text-sm font-semibold text-aegisaxiom-text mb-2 flex items-center gap-2">
                <AlertCircle className="w-4 h-4 text-yellow-600 dark:text-yellow-400" />
                Active Alerts ({alerts.length})
              </h3>
              {alerts.slice(0, 3).map((alert) => (
                <AlertBanner
                  key={alert.alert_id}
                  alert={alert}
                  onDismiss={handleDismissAlert}
                  onAcknowledge={handleAcknowledgeAlert}
                  compact={false}
                  showActions={true}
                />
              ))}
              {alerts.length > 3 && (
                <p className="text-xs text-aegisaxiom-text-muted text-center py-1">
                  +{alerts.length - 3} more alert{alerts.length - 3 > 1 ? 's' : ''}
                </p>
              )}
            </div>
          )}

          {/* SOW Tasks Section */}
          <div>
            <h3 className="text-lg font-semibold text-aegisaxiom-text mb-4">Statement of Work Tasks</h3>

            {/* Referral MRR Routing */}
            {sowTasks?.referral_mrr_exists && (
              <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700/30 rounded-lg p-4 mb-4 flex items-start gap-3">
                <AlertCircle className="w-5 h-5 text-amber-600 dark:text-amber-400 flex-shrink-0 mt-0.5" />
                <div className="flex-1">
                  <h4 className="font-semibold text-amber-700 dark:text-amber-300 mb-1">
                    Referral MRR Detected
                  </h4>
                  <p className="text-amber-200/80 text-sm mb-3">
                    This opportunity includes Referral MRR. Route to Procurement for referral
                    agreement setup.
                  </p>
                  <button
                    onClick={() => {
                      toast.info('Navigate to Procurement tab → Referral Agreements section');
                      // Future: Auto-navigate or open procurement modal
                    }}
                    className="px-4 py-2 bg-amber-600 hover:bg-amber-500 text-white text-sm rounded-lg transition flex items-center gap-2"
                  >
                    <ArrowRight className="w-4 h-4" />
                    Route to Procurement
                  </button>
                </div>
              </div>
            )}

            {/* SOW Task Board (for multiple LOBs) or Direct Button (single LOB) */}
            {sowTasks?.sow_tasks?.length === 0 ? (
              <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-8 text-center">
                <FileText className="w-12 h-12 text-aegisaxiom-text-muted mx-auto mb-3" />
                <p className="text-aegisaxiom-text-secondary text-sm">
                  No LOBs configured for this opportunity
                </p>
              </div>
            ) : sowTasks?.sow_tasks?.length === 1 ? (
              // Single LOB - Direct Build SOW Button
              <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-6 text-center">
                <div className="mb-4">
                  <span
                    className={`inline-block px-3 py-1 rounded-lg text-sm font-medium border ${LOB_COLORS[sowTasks.sow_tasks[0].line_of_business]}`}
                  >
                    {LOB_LABELS[sowTasks.sow_tasks[0].line_of_business]}
                  </span>
                </div>
                <button
                  onClick={() => setSelectedLOB(sowTasks.sow_tasks[0].line_of_business)}
                  className="px-6 py-3 bg-violet-600 hover:bg-violet-500 text-white rounded-lg transition flex items-center gap-2 mx-auto"
                >
                  <FileText className="w-5 h-5" />
                  Build SOW
                </button>
              </div>
            ) : (
              // Multiple LOBs - Task Board
              <SOWTaskBoard
                sowTasks={sowTasks.sow_tasks}
                salesOpsUsers={salesOpsUsers}
                onAssign={assignSOW}
                onBuildSOW={(lob) => setSelectedLOB(lob)}
              />
            )}
          </div>

          {/* Proposals Section (V1) */}
          <ProposalsSection
            proposals={proposals}
            loading={proposalsLoading}
            generating={generatingProposal}
            sowTasks={sowTasks?.sow_tasks || []}
            onGenerate={handleGenerateProposal}
            onAttachDoc={handleAttachProposalDoc}
            onSend={handleSendProposal}
            onMarkClientSent={handleMarkClientSent}
            onStatusChange={handleProposalStatusChange}
            onRegenerate={handleRegenerateProposal}
            onEdit={handleEditProposal}
          />
        </div>
      </div>
      {sendDialogProposalId && (
        <SendProposalDialog
          proposalId={sendDialogProposalId}
          onSend={submitSendProposal}
          onMarkSentOnly={(pid) => submitSendProposal(pid, null)}
          onCancel={closeSendDialog}
        />
      )}
    </div>
  );
}

// SOW Task Board (for multiple LOBs)
function SOWTaskBoard({ sowTasks, salesOpsUsers, onAssign, onBuildSOW }) {
  // I5. ready_for_review was missing from this map so SOWs in that
  // status rendered with the not_started colour. The four entries here
  // mirror the four columns SolutionsDesignKanban already renders so a
  // SOW in any first class status shows the right pill.
  const statusConfig = {
    not_started: {
      label: 'Not Started',
      color: 'bg-aegisaxiom-elevated/20 text-aegisaxiom-text-secondary',
      icon: Clock,
    },
    in_progress: {
      label: 'In Progress',
      color: 'bg-blue-50 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300',
      icon: FileText,
    },
    ready_for_review: {
      label: 'Ready for Review',
      color: 'bg-violet-50 dark:bg-violet-500/20 text-violet-700 dark:text-violet-300',
      icon: CheckCircle,
    },
    complete: {
      label: 'Complete',
      color: 'bg-green-50 dark:bg-green-500/20 text-green-700 dark:text-green-300',
      icon: CheckCircle,
    },
  };

  return (
    <div className="space-y-3">
      {sowTasks.map((task) => {
        const StatusIcon = statusConfig[task.status]?.icon || Clock;

        return (
          <div key={task.sow_id} className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
            <div className="flex items-start justify-between gap-4">
              {/* LOB Info */}
              <div className="flex-1">
                <div className="flex items-center gap-3 mb-2">
                  <span
                    className={`px-3 py-1 rounded-lg text-sm font-medium border ${LOB_COLORS[task.line_of_business]}`}
                  >
                    {LOB_LABELS[task.line_of_business]}
                  </span>
                  <span
                    className={`px-2 py-1 rounded text-xs font-medium ${statusConfig[task.status]?.color || statusConfig.not_started.color}`}
                  >
                    <StatusIcon className="w-3 h-3 inline mr-1" />
                    {statusConfig[task.status]?.label || 'Not Started'}
                  </span>
                </div>

                {/* Assignment */}
                <div className="flex items-center gap-2 mb-2">
                  <User className="w-4 h-4 text-aegisaxiom-text-secondary" />
                  <select
                    value={task.assigned_to || ''}
                    onChange={(e) => onAssign(task.line_of_business, e.target.value)}
                    className="flex-1 px-3 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                  >
                    <option value="">Assign to...</option>
                    {salesOpsUsers.map((user) => (
                      <option key={user.user_id} value={user.user_id}>
                        {user.name}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Stats */}
                <div className="flex items-center gap-4 text-xs text-aegisaxiom-text-secondary">
                  <span>{(task.milestones || []).length} milestones</span>
                  <span>Version {task.version}</span>
                  {task.expected_completion_date && (
                    <span className="flex items-center gap-1 text-aegisaxiom-text-secondary">
                      <Calendar className="w-3 h-3" />
                      Due: {new Date(task.expected_completion_date).toLocaleDateString()}
                    </span>
                  )}
                </div>
              </div>

              {/* Build SOW Button */}
              <button
                onClick={() => onBuildSOW(task.line_of_business)}
                className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition flex items-center gap-2"
              >
                <FileText className="w-4 h-4" />
                Build SOW
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// Proposals Section (MVP) — Generate Proposal button + simple list with
// status pill, Google Doc URL field, Send action, and a status dropdown
// for accepted/rejected updates. Mirrors the SOW pattern; no preview, no
// PDF, mark-as-sent only — matches the backend MVP at routes/proposals.py.
// Proposal status badge mapping. "sent" means internal handoff to
// Sales (Sales Ops finished prep). "client_sent" means the Sales
// owner delivered the proposal to the client externally. The two
// must not collapse into a single "Sent" label. See
// docs/sales-ops/PROPOSAL_HANDOFF_AND_SEND_POLICY.md.
const PROPOSAL_STATUS_CONFIG = {
  draft: {
    label: 'Draft',
    color:
      'bg-aegisaxiom-elevated/30 text-aegisaxiom-text-secondary border border-aegisaxiom-border',
  },
  ready_for_review: {
    label: 'Ready for Review',
    color:
      'bg-blue-50 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300 border border-blue-500/30',
  },
  sent: {
    label: 'Handed off to Sales',
    color:
      'bg-violet-50 dark:bg-violet-500/20 text-violet-700 dark:text-violet-300 border border-violet-500/30',
  },
  client_sent: {
    label: 'Sent to client',
    color:
      'bg-violet-50 dark:bg-violet-500/20 text-violet-700 dark:text-violet-300 border border-violet-500/30',
  },
  accepted: {
    label: 'Accepted',
    color:
      'bg-green-50 dark:bg-green-500/20 text-green-700 dark:text-green-300 border border-green-500/30',
  },
  rejected: {
    label: 'Rejected',
    color:
      'bg-red-50 dark:bg-red-500/20 text-red-700 dark:text-red-300 border border-red-500/30',
  },
};

function ProposalsSection({
  proposals,
  loading,
  generating,
  sowTasks,
  onGenerate,
  onAttachDoc,
  onSend,
  onMarkClientSent,
  onStatusChange,
  onRegenerate,
  onEdit,
}) {
  const hasReadySow = sowTasks.some(
    (s) => s.status === 'ready_for_review' || s.status === 'complete',
  );

  return (
    <div className="mt-6 pt-6 border-t border-aegisaxiom-border">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-aegisaxiom-text flex items-center gap-2">
          <FileText className="w-5 h-5 text-violet-600 dark:text-violet-400" />
          Proposals ({proposals.length})
        </h3>
        <button
          onClick={onGenerate}
          disabled={!hasReadySow || generating}
          title={
            hasReadySow
              ? 'Create a new proposal draft from every Ready/Complete SOW'
              : 'No SOW is Ready for Review or Complete yet'
          }
          className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {generating ? (
            <>
              <Loader className="w-4 h-4 animate-spin" />
              Generating…
            </>
          ) : (
            <>
              <Sparkles className="w-4 h-4" />
              Generate Proposal
            </>
          )}
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-6">
          <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-violet-500" />
        </div>
      ) : proposals.length === 0 ? (
        <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-6 text-center">
          <FileText className="w-10 h-10 text-aegisaxiom-text-muted mx-auto mb-2" />
          <p className="text-sm text-aegisaxiom-text-secondary">
            No proposals yet. Once a SOW is Ready for Review or Complete, click
            <span className="font-medium"> Generate Proposal </span>
            to create a draft.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {proposals.map((p) => (
            <ProposalRow
              key={p.proposal_id}
              proposal={p}
              onAttachDoc={onAttachDoc}
              onSend={onSend}
              onMarkClientSent={onMarkClientSent}
              onStatusChange={onStatusChange}
              onRegenerate={onRegenerate}
              onEdit={onEdit}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ProposalRow({
  proposal,
  onAttachDoc,
  onSend,
  onMarkClientSent,
  onStatusChange,
  onRegenerate,
  onEdit,
}) {
  const statusCfg =
    PROPOSAL_STATUS_CONFIG[proposal.status] || PROPOSAL_STATUS_CONFIG.draft;
  const [docInput, setDocInput] = useState(proposal.google_doc_url || '');
  const [view, setView] = useState('collapsed'); // 'collapsed' | 'preview' | 'edit'
  const [editTitle, setEditTitle] = useState(proposal.title || '');
  const [editSummary, setEditSummary] = useState(proposal.generated_summary || '');
  const [editContent, setEditContent] = useState(proposal.generated_content || '');
  const isLocked =
    proposal.status === 'sent' ||
    proposal.status === 'client_sent' ||
    proposal.status === 'accepted' ||
    proposal.status === 'rejected';
  const hasInlineContent = !!(proposal.generated_content && proposal.generated_content.trim());
  const hasArtifact = !!proposal.google_doc_url || hasInlineContent;

  // Reset edit fields when the proposal payload changes (regenerate /
  // PATCH refresh) so the form doesn't strand stale text.
  useEffect(() => {
    setDocInput(proposal.google_doc_url || '');
    setEditTitle(proposal.title || '');
    setEditSummary(proposal.generated_summary || '');
    setEditContent(proposal.generated_content || '');
  }, [
    proposal.proposal_id,
    proposal.version,
    proposal.regenerated_at,
    proposal.updated_at,
  ]);

  const handleSaveEdit = async () => {
    const patch = {};
    if (editTitle !== (proposal.title || '')) patch.title = editTitle;
    if (editSummary !== (proposal.generated_summary || '')) {
      patch.generated_summary = editSummary;
    }
    if (editContent !== (proposal.generated_content || '')) {
      patch.generated_content = editContent;
    }
    if (Object.keys(patch).length === 0) {
      setView('preview');
      return;
    }
    const ok = await onEdit(proposal.proposal_id, patch);
    if (ok) setView('preview');
  };

  return (
    <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
      <div className="flex items-start justify-between gap-4 mb-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span
              className={`px-2 py-0.5 rounded text-xs font-medium ${statusCfg.color}`}
            >
              {statusCfg.label}
            </span>
            <span className="text-xs text-aegisaxiom-text-muted">
              v{proposal.version}
            </span>
            <span className="text-xs text-aegisaxiom-text-muted">
              {proposal.sow_ids?.length || 0} SOW
              {(proposal.sow_ids?.length || 0) === 1 ? '' : 's'}
            </span>
            {proposal.regenerated_at && (
              <span
                className="text-xs text-aegisaxiom-text-muted"
                title="Most recent regeneration timestamp"
              >
                Regenerated {new Date(proposal.regenerated_at).toLocaleDateString()}
              </span>
            )}
          </div>
          <p className="text-sm font-medium text-aegisaxiom-text truncate">
            {proposal.title || 'Untitled proposal'}
          </p>
          {proposal.generated_summary && (
            <p className="text-xs text-aegisaxiom-text-secondary mt-0.5 line-clamp-2">
              {proposal.generated_summary}
            </p>
          )}
          {proposal.sent_to_email && (
            <p className="text-xs text-aegisaxiom-text-muted mt-0.5">
              Handed off to {proposal.sent_to_email}
              {proposal.sent_at
                ? ` · ${new Date(proposal.sent_at).toLocaleDateString()}`
                : ''}
            </p>
          )}
        </div>
      </div>

      {/* Google Doc URL — locked once the proposal reaches a sent /
          accepted / rejected status so the audit trail stays stable. */}
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <input
          type="url"
          value={docInput}
          onChange={(e) => setDocInput(e.target.value)}
          placeholder="https://docs.google.com/document/d/... (optional — inline content works too)"
          disabled={isLocked}
          className="flex-1 min-w-[200px] px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500 disabled:opacity-60"
        />
        <button
          onClick={() => onAttachDoc(proposal.proposal_id, docInput)}
          disabled={
            isLocked ||
            !docInput.trim() ||
            docInput.trim() === proposal.google_doc_url
          }
          className="px-3 py-1.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-border text-aegisaxiom-text text-sm rounded transition disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Save URL
        </button>
        {proposal.google_doc_url && (
          <a
            href={proposal.google_doc_url}
            target="_blank"
            rel="noopener noreferrer"
            className="px-3 py-1.5 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded transition flex items-center gap-1"
          >
            <Eye className="w-3 h-3" />
            Open Doc
          </a>
        )}
      </div>

      {/* Inline preview / edit affordances. View toggles the generated
          content; Edit swaps to a textarea form. */}
      {view === 'preview' && hasInlineContent && (
        <pre className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded p-3 text-xs text-aegisaxiom-text whitespace-pre-wrap max-h-80 overflow-y-auto mb-3">
{proposal.generated_content}
        </pre>
      )}
      {view === 'edit' && (
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded p-3 mb-3 space-y-2">
          <label className="block text-xs text-aegisaxiom-text-secondary">Title</label>
          <input
            type="text"
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            className="w-full px-3 py-1.5 bg-aegisaxiom-base border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
          />
          <label className="block text-xs text-aegisaxiom-text-secondary">Summary</label>
          <textarea
            rows={3}
            value={editSummary}
            onChange={(e) => setEditSummary(e.target.value)}
            className="w-full px-3 py-1.5 bg-aegisaxiom-base border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
          />
          <label className="block text-xs text-aegisaxiom-text-secondary">Content (Markdown)</label>
          <textarea
            rows={12}
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            className="w-full px-3 py-1.5 bg-aegisaxiom-base border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs font-mono focus:outline-none focus:border-violet-500"
          />
          <div className="flex justify-end gap-2 pt-1">
            <button
              onClick={() => setView('preview')}
              className="px-3 py-1.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-border text-aegisaxiom-text text-sm rounded transition"
            >
              Cancel
            </button>
            <button
              onClick={handleSaveEdit}
              className="px-3 py-1.5 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded transition"
            >
              Save
            </button>
          </div>
        </div>
      )}

      {/* Actions row. View toggles the inline preview; Edit/Regenerate
          mutate the draft; Send marks it sent (no email). */}
      <div className="flex items-center justify-end gap-2 flex-wrap">
        {hasInlineContent && view !== 'edit' && (
          <button
            onClick={() => setView(view === 'preview' ? 'collapsed' : 'preview')}
            className="px-3 py-1.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-border text-aegisaxiom-text text-sm rounded transition flex items-center gap-1"
          >
            <Eye className="w-3 h-3" />
            {view === 'preview' ? 'Hide' : 'View'}
          </button>
        )}
        {!isLocked && view !== 'edit' && (
          <button
            onClick={() => setView('edit')}
            className="px-3 py-1.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-border text-aegisaxiom-text text-sm rounded transition flex items-center gap-1"
          >
            <FileText className="w-3 h-3" />
            Edit
          </button>
        )}
        {!isLocked && (
          <button
            onClick={() => onRegenerate(proposal.proposal_id)}
            title="Rebuild content from the latest SOW data"
            className="px-3 py-1.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-border text-aegisaxiom-text text-sm rounded transition flex items-center gap-1"
          >
            <Sparkles className="w-3 h-3" />
            Regenerate
          </button>
        )}
        {!isLocked && (
          <button
            onClick={() => onSend(proposal.proposal_id)}
            disabled={!hasArtifact}
            title={
              hasArtifact
                ? 'Hand off this proposal to Sales. Sales owns the client-facing send. Records the handoff in the audit log. No email leaves AEGISAXIOM.'
                : 'Generate content or attach a Google Doc URL before handing off to Sales'
            }
            className="px-3 py-1.5 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded transition flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
            data-testid="proposal-mark-sent-to-sales"
          >
            <ArrowRight className="w-3 h-3" />
            Mark Sent to Sales
          </button>
        )}
        {proposal.status === 'sent' && onMarkClientSent && (
          <button
            onClick={() => onMarkClientSent(proposal.proposal_id)}
            title="Record that Sales delivered this proposal to the client. Sales prepares the external PDF or signing link and emails the client from their own tool. AEGISAXIOM does not perform the external send."
            className="px-3 py-1.5 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded transition flex items-center gap-1"
            data-testid="proposal-mark-client-sent"
          >
            <ArrowRight className="w-3 h-3" />
            Mark client sent
          </button>
        )}
        {(proposal.status === 'sent' || proposal.status === 'client_sent') && (
          <select
            value=""
            onChange={(e) => {
              if (e.target.value)
                onStatusChange(proposal.proposal_id, e.target.value);
            }}
            className="px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
          >
            <option value="">Record client response…</option>
            <option value="accepted">Accepted</option>
            <option value="rejected">Rejected</option>
          </select>
        )}
      </div>
    </div>
  );
}

// SOW Status Bar Component
function SOWStatusBar({ currentStatus, blockerInfo, loadingBlockers }) {
  const statuses = [
    { value: 'not_started', label: 'Not Started', color: 'bg-aegisaxiom-elevated', icon: Clock },
    { value: 'in_progress', label: 'In Progress', color: 'bg-blue-500', icon: FileText },
    { value: 'ready_for_review', label: 'Ready for Review', color: 'bg-violet-500', icon: Eye },
    { value: 'complete', label: 'Complete', color: 'bg-green-500', icon: CheckCircle },
  ];

  const currentIndex = statuses.findIndex((s) => s.value === currentStatus);

  return (
    <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-semibold text-aegisaxiom-text-secondary">SOW Status Pipeline</h4>
        {blockerInfo && (
          <div className="flex items-center gap-3 text-xs">
            {blockerInfo.blocker_count > 0 && (
              <span className="flex items-center gap-1 text-red-600 dark:text-red-400 font-medium">
                <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse"></span>
                {blockerInfo.blocker_count} Critical Blocker
                {blockerInfo.blocker_count > 1 ? 's' : ''}
              </span>
            )}
            {blockerInfo.warning_count > 0 && (
              <span className="flex items-center gap-1 text-yellow-600 dark:text-yellow-400 font-medium">
                <span className="w-2 h-2 rounded-full bg-yellow-500"></span>
                {blockerInfo.warning_count} Warning{blockerInfo.warning_count > 1 ? 's' : ''}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Status Progress Bar */}
      <div className="flex items-center gap-2">
        {statuses.map((status, index) => {
          const Icon = status.icon;
          const isActive = status.value === currentStatus;
          const isPast = index < currentIndex;
          const isBlocked = status.value === 'blocked' && currentStatus === 'blocked';

          return (
            <React.Fragment key={status.value}>
              <div
                className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg border transition ${
                  isActive
                    ? `${status.color} text-aegisaxiom-text border-transparent font-semibold ${isBlocked ? 'animate-pulse' : ''}`
                    : isPast
                      ? 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary border-aegisaxiom-border-hover'
                      : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-muted border-aegisaxiom-border'
                }`}
              >
                <Icon className={`w-4 h-4 ${isActive && isBlocked ? 'animate-pulse' : ''}`} />
                <span className="text-xs font-medium">{status.label}</span>
              </div>
              {index < statuses.length - 1 && (
                <ArrowRight
                  className={`w-4 h-4 flex-shrink-0 ${
                    isPast ? 'text-aegisaxiom-text-secondary' : 'text-aegisaxiom-text-muted'
                  }`}
                />
              )}
            </React.Fragment>
          );
        })}
      </div>

      {/* Blocker Details */}
      {blockerInfo && blockerInfo.blockers && blockerInfo.blockers.length > 0 && (
        <div className="mt-4 space-y-2">
          {blockerInfo.blockers
            .filter((b) => b.type === 'critical')
            .map((blocker, idx) => (
              <div
                key={idx}
                className="flex items-start gap-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-700/30 rounded px-3 py-2"
              >
                <AlertCircle className="w-4 h-4 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
                <span className="text-xs text-red-700 dark:text-red-300">{blocker.message}</span>
              </div>
            ))}
          {blockerInfo.blockers
            .filter((b) => b.type === 'warning')
            .map((blocker, idx) => (
              <div
                key={idx}
                className="flex items-start gap-2 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-700/30 rounded px-3 py-2"
              >
                <AlertCircle className="w-4 h-4 text-yellow-600 dark:text-yellow-400 flex-shrink-0 mt-0.5" />
                <span className="text-xs text-yellow-700 dark:text-yellow-300">
                  {blocker.message}
                </span>
              </div>
            ))}
        </div>
      )}

      {loadingBlockers && (
        <div className="mt-3 flex items-center gap-2 text-xs text-aegisaxiom-text-secondary">
          <div className="animate-spin rounded-full h-3 w-3 border-b border-aegisaxiom-border-hover"></div>
          Checking for blockers...
        </div>
      )}
    </div>
  );
}

// SOW Builder Modal Component
function SOWBuilderModal({ opportunity, lob, alertContext, onClose, onBack }) {
  const { user } = useAuth();
  const [sow, setSOW] = useState(null);
  const [loading, setLoading] = useState(true);
  // Error string surfaced when the SOW load fails. Previously a failed
  // load left sow=null and the modal rendered nothing, which read to
  // the user as the click doing nothing.
  const [loadError, setLoadError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [activeSection, setActiveSection] = useState('smart');
  const [blockerInfo, setBlockerInfo] = useState(null);
  const [loadingBlockers, setLoadingBlockers] = useState(false);
  const [alerts, setAlerts] = useState([]); // NEW: Alerts for this SOW
  const [savedTabs, setSavedTabs] = useState(new Set()); // Track which tabs have been saved
  const [selectedTranscripts, setSelectedTranscripts] = useState([]); // Shared across tabs: [{id, title}]
  const [validationResult, setValidationResult] = useState(null); // Guide validation checklist from Milestones step
  const [showFinalizeConfirm, setShowFinalizeConfirm] = useState(false);
  const [showSignedConfirm, setShowSignedConfirm] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [resetting, setResetting] = useState(false);

  const canResetSOW =
    (user?.role === 'admin' || user?.role === 'director') && sow?.status !== 'complete';

  useEffect(() => {
    loadSOW();
    loadAlertsForSOW(); // NEW: Load alerts
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opportunity.opportunity_id, lob]);

  // Auto-navigate to problem tab if alertContext provided
  useEffect(() => {
    if (alertContext && alertContext.initialTab) {
      setActiveSection(alertContext.initialTab);
    }
  }, [alertContext]);

  // Check for blockers whenever SOW changes
  useEffect(() => {
    if (sow && sow.sow_id) {
      checkBlockers();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sow?.selected_resource_assignment_ids]);

  const loadSOW = async () => {
    try {
      setLoading(true);
      setLoadError(null);
      const response = await api.get(
        `${API}/api/sales-ops/opportunities/${opportunity.opportunity_id}/sow?lob=${lob}`,
      );

      if (response.ok) {
        const data = await response.json();
        setSOW(data);
        return;
      }
      // Previously a non-OK response was swallowed: sow stayed null,
      // the modal returned null on the next render, and the click read
      // as a no-op. Surface the server's reason so the user knows what
      // to fix (commonly: LOB not present on the opportunity, or the
      // opportunity was renamed/deleted out from under the kanban).
      let detail = `Failed to load SOW (HTTP ${response.status}).`;
      try {
        const errBody = await response.json();
        if (errBody && typeof errBody.detail === 'string') {
          detail = errBody.detail;
        }
      } catch (_parseErr) {
        // body wasn't JSON; keep the HTTP fallback message
      }
      setLoadError(detail);
      toast.error(detail);
    } catch (error) {
      console.error('Error loading SOW:', error);
      const detail = 'Failed to load SOW (network error).';
      setLoadError(detail);
      toast.error(detail);
    } finally {
      setLoading(false);
    }
  };

  const loadAlertsForSOW = async () => {
    try {
      const response = await api.get(
        `${API}/api/sales-ops/opportunities/${opportunity.opportunity_id}/alerts`,
      );
      if (response.ok) {
        const data = await response.json();
        const allAlerts = data.items || [];

        // Filter alerts for this specific SOW (by line_of_business match)
        const sowAlerts = allAlerts.filter(
          (alert) => alert.line_of_business === lob && !alert.dismissed,
        );
        setAlerts(sowAlerts);
      }
    } catch (error) {
      console.error('Error loading alerts:', error);
    }
  };

  const handleDismissAlert = async (alertId) => {
    try {
      const response = await api.patch(`${API}/api/sales-ops/alerts/${alertId}/dismiss`);
      if (response.ok) {
        toast.success('Alert dismissed');
        loadAlertsForSOW();
      }
    } catch (error) {
      console.error('Error dismissing alert:', error);
      toast.error('Failed to dismiss alert');
    }
  };

  const handleAcknowledgeAlert = async (alertId) => {
    try {
      const response = await api.patch(`${API}/api/sales-ops/alerts/${alertId}/acknowledge`);
      if (response.ok) {
        toast.success('Alert acknowledged');
        loadAlertsForSOW();
      }
    } catch (error) {
      console.error('Error acknowledging alert:', error);
      toast.error('Failed to acknowledge alert');
    }
  };

  const checkBlockers = async () => {
    if (!sow || !sow.sow_id) return;

    try {
      setLoadingBlockers(true);
      const response = await api.get(
        `${API}/api/sales-ops/opportunities/${opportunity.opportunity_id}/sows/${sow.sow_id}/blockers`,
      );

      if (response.ok) {
        const data = await response.json();
        setBlockerInfo(data);

        // Auto-update SOW status if needed, but never downgrade from ready_for_review/complete
        const protectedStatuses = ['ready_for_review', 'complete', 'ready_for_review'];
        if (data.status && data.status !== sow.status && !protectedStatuses.includes(sow.status)) {
          setSOW((prev) => ({ ...prev, status: data.status }));
        }
      }
    } catch (error) {
      console.error('Error checking blockers:', error);
    } finally {
      setLoadingBlockers(false);
    }
  };

  const saveSOW = async () => {
    try {
      setSaving(true);

      // Determine the correct status after this save
      const newSavedTabs = new Set(savedTabs).add(activeSection);
      const newSavedCount = visibleTabIds.filter((id) => newSavedTabs.has(id)).length;
      const allCoreSaved = newSavedCount >= 3;

      // Auto-advance to in_progress on first save
      let newStatus = sow.status;
      if (sow.status === 'not_started') {
        newStatus = 'in_progress';
      }

      const updatedSOW = {
        ...sow,
        status: newStatus,
      };

      const response = await api.post(
        `${API}/api/sales-ops/opportunities/${opportunity.opportunity_id}/sow?lob=${lob}`,
        updatedSOW,
      );

      if (response.ok) {
        const updated = await response.json();
        setSOW(updated);
        setSavedTabs(newSavedTabs);
        toast.success('SOW saved successfully');
      } else {
        toast.error('Failed to save SOW');
      }
    } catch (error) {
      console.error('Error saving SOW:', error);
      toast.error('Failed to save SOW');
    } finally {
      setSaving(false);
    }
  };

  const handleResetSOW = async () => {
    setShowResetConfirm(false);
    setResetting(true);
    try {
      const response = await api.delete(
        `${API}/api/opportunities/${opportunity.opportunity_id}/sow?lob=${lob}`,
      );
      if (response.ok) {
        toast.success('SOW reset — all data cleared');
        await loadSOW();
      } else {
        const err = await response.json();
        toast.error(err.detail || 'Failed to reset SOW');
      }
    } catch (error) {
      console.error('Error resetting SOW:', error);
      toast.error('Failed to reset SOW');
    } finally {
      setResetting(false);
    }
  };

  // Build the visible tab IDs for this LOB (must match the sections array below)
  const visibleTabIds = [
    'smart',
    'milestones',
    ...(lob === 'field_services' || lob === 'professional_services' ? ['resources'] : []),
    ...(lob === 'field_services' ? ['materials', 'hardware', 'services'] : []),
    ...(lob === 'professional_services' ? ['hardware', 'services'] : []),
    ...(lob === 'wholesale_mrr' ? ['wholesale_mrr'] : []),
    'billing',
  ];
  const savedTabCount = visibleTabIds.filter((id) => savedTabs.has(id)).length;
  const allTabsSaved = savedTabCount >= 3;

  const handleMarkComplete = () => {
    if (blockerInfo && blockerInfo.has_blockers) {
      toast.error('Cannot finalize - Critical blockers exist', {
        description: blockerInfo.blockers
          .filter((b) => b.type === 'critical')
          .map((b) => b.message)
          .join(', '),
      });
      return;
    }
    if (!allTabsSaved) {
      const tabLabelMap = {
        smart: 'SMART Proposal',
        milestones: 'Detailed SOW',
        resources: 'Contract & Resources',
        materials: 'Materials',
        hardware: 'Hardware',
        services: 'Services',
        wholesale_mrr: 'Products & Services',
        billing: 'Billing Details',
      };
      const unsaved = visibleTabIds.filter((id) => !savedTabs.has(id));
      const tabNames = unsaved.map((id) => tabLabelMap[id] || id);
      toast.error(`Save all tabs before finalizing: ${tabNames.join(', ')}`);
      return;
    }
    setShowFinalizeConfirm(true);
  };

  const finalizeSOW = async () => {
    setShowFinalizeConfirm(false);
    setFinalizing(true);

    try {
      const response = await api.post(`${API}/api/sales-ops/sow/finalize`, {
        opportunity_id: opportunity.opportunity_id,
        lob: lob,
      });

      if (response.ok) {
        const data = await response.json();
        setSOW((prev) => ({
          ...prev,
          status: 'ready_for_review',
          google_doc_url: data.google_doc_url,
          google_doc_id: data.google_doc_id,
        }));

        // The finalize response now distinguishes a fresh export
        // from a duplicate-prevented no-op so the toast can match.
        // See docs/sales-ops/GOOGLE_DOC_ARTIFACT_PATH.md.
        if (data.duplicate_prevented || data.no_op) {
          toast.success('Google Doc already exists.', {
            description: 'Opening the existing Google Doc.',
            action: data.google_doc_url
              ? {
                  label: 'Open Doc',
                  onClick: () => window.open(data.google_doc_url, '_blank'),
                }
              : undefined,
          });
        } else {
          toast.success('Google Doc exported.', {
            description: 'Google Doc has been created successfully.',
            action: data.google_doc_url
              ? {
                  label: 'Open Doc',
                  onClick: () => window.open(data.google_doc_url, '_blank'),
                }
              : undefined,
          });
        }
      } else {
        // Map the structured Google Docs error codes to actionable
        // user-facing copy. The codes are listed in
        // docs/sales-ops/GOOGLE_DOC_ARTIFACT_PATH.md. Fall back to
        // resolveErrorDetail for any other error.
        let body = {};
        try {
          body = await response.json();
        } catch (_e) {
          // Non-JSON response. Use generic copy.
        }
        const code = body && body.detail && body.detail.code;
        const GOOGLE_DOC_ERROR_COPY = {
          google_docs_not_configured:
            'Google Docs export is not configured. Paste a Google Doc URL on the proposal as the escape hatch.',
          google_docs_folder_not_configured:
            'Google Docs export is not configured. Ask an admin to configure the Drive folder.',
          google_docs_folder_not_found:
            'Google Docs export failed. The configured Drive folder was not found.',
          google_docs_permission_denied:
            'AEGISAXIOM does not have access to the configured Drive folder. Ask an admin to share it.',
          google_docs_export_failed:
            'Google Docs export failed. Try again or paste a Google Doc URL.',
        };
        const message = GOOGLE_DOC_ERROR_COPY[code] || resolveErrorDetail(body);
        toast.error(message);
      }
    } catch (error) {
      console.error('Error finalizing SOW:', error);
      toast.error('Failed to finalize SOW.');
    } finally {
      setFinalizing(false);
    }
  };

  const confirmClientSigned = async () => {
    setShowSignedConfirm(false);
    try {
      const response = await api.patch(
        `${API}/api/sales-ops/opportunities/${opportunity.opportunity_id}/sows/${sow.sow_id}/status?status=complete`,
      );

      if (response.ok) {
        setSOW((prev) => ({ ...prev, status: 'complete' }));
        toast.success('SOW marked as signed and complete!');
      } else {
        const err = await response.json();
        toast.error(err.detail || 'Failed to update status');
      }
    } catch (error) {
      console.error('Error confirming signature:', error);
      toast.error('Failed to update status');
    }
  };

  const updateSOWField = (field, value) => {
    setSOW((prev) => ({ ...prev, [field]: value }));
  };

  const addMilestone = () => {
    const newMilestone = {
      milestone_id: `ms_${Date.now()}`,
      milestone_name: '',
      order: (sow.milestones || []).length,
      bullets: [],
    };
    updateSOWField('milestones', [...(sow.milestones || []), newMilestone]);
  };

  const updateMilestone = (milestoneId, field, value) => {
    const updated = (sow.milestones || []).map((m) =>
      m.milestone_id === milestoneId ? { ...m, [field]: value } : m,
    );
    updateSOWField('milestones', updated);
  };

  const deleteMilestone = (milestoneId) => {
    const updated = (sow.milestones || []).filter((m) => m.milestone_id !== milestoneId);
    updateSOWField('milestones', updated);
  };

  const addBullet = (milestoneId) => {
    const updated = (sow.milestones || []).map((m) => {
      if (m.milestone_id === milestoneId) {
        const newBullet = {
          bullet_id: `bullet_${Date.now()}`,
          bullet_text: '',
          estimated_hours: null,
          deliverable: '',
          order: (m.bullets || []).length,
        };
        return { ...m, bullets: [...(m.bullets || []), newBullet] };
      }
      return m;
    });
    updateSOWField('milestones', updated);
  };

  const updateBullet = (milestoneId, bulletId, field, value) => {
    const updated = (sow.milestones || []).map((m) => {
      if (m.milestone_id === milestoneId) {
        const updatedBullets = (m.bullets || []).map((b) =>
          b.bullet_id === bulletId ? { ...b, [field]: value } : b,
        );
        return { ...m, bullets: updatedBullets };
      }
      return m;
    });
    updateSOWField('milestones', updated);
  };

  const deleteBullet = (milestoneId, bulletId) => {
    const updated = (sow.milestones || []).map((m) => {
      if (m.milestone_id === milestoneId) {
        return { ...m, bullets: (m.bullets || []).filter((b) => b.bullet_id !== bulletId) };
      }
      return m;
    });
    updateSOWField('milestones', updated);
  };

  // Subtask CRUD — subtasks live under bullet.subtasks (added lazily so
  // existing bullets without subtasks are unaffected). The reducer
  // pattern mirrors the bullet helpers: find the milestone, find the
  // bullet inside it, mutate its subtasks array, write the entire
  // milestones array back. No backend route changes are required —
  // SOWBullet carries `subtasks: List[SOWSubtask]` with default empty.
  const addSubtask = (milestoneId, bulletId) => {
    const updated = (sow.milestones || []).map((m) => {
      if (m.milestone_id !== milestoneId) return m;
      const updatedBullets = (m.bullets || []).map((b) => {
        if (b.bullet_id !== bulletId) return b;
        const existing = b.subtasks || [];
        const newSubtask = {
          subtask_id: `subtask_${Date.now()}`,
          description: '',
          order: existing.length,
        };
        return { ...b, subtasks: [...existing, newSubtask] };
      });
      return { ...m, bullets: updatedBullets };
    });
    updateSOWField('milestones', updated);
  };

  const updateSubtask = (milestoneId, bulletId, subtaskId, field, value) => {
    const updated = (sow.milestones || []).map((m) => {
      if (m.milestone_id !== milestoneId) return m;
      const updatedBullets = (m.bullets || []).map((b) => {
        if (b.bullet_id !== bulletId) return b;
        const updatedSubtasks = (b.subtasks || []).map((s) =>
          s.subtask_id === subtaskId ? { ...s, [field]: value } : s,
        );
        return { ...b, subtasks: updatedSubtasks };
      });
      return { ...m, bullets: updatedBullets };
    });
    updateSOWField('milestones', updated);
  };

  const deleteSubtask = (milestoneId, bulletId, subtaskId) => {
    const updated = (sow.milestones || []).map((m) => {
      if (m.milestone_id !== milestoneId) return m;
      const updatedBullets = (m.bullets || []).map((b) => {
        if (b.bullet_id !== bulletId) return b;
        const remaining = (b.subtasks || []).filter((s) => s.subtask_id !== subtaskId);
        // Re-stamp order so persisted indexes stay contiguous after delete.
        const restamped = remaining.map((s, i) => ({ ...s, order: i }));
        return { ...b, subtasks: restamped };
      });
      return { ...m, bullets: updatedBullets };
    });
    updateSOWField('milestones', updated);
  };

  const addAuthorizedUser = () => {
    const newUser = {
      user_id: `auth_user_${Date.now()}`,
      name: '',
      email: '',
      phone: '',
      max_hours_allowed: null,
    };
    updateSOWField('boa_authorized_users', [...(sow.boa_authorized_users || []), newUser]);
  };

  const updateAuthorizedUser = (userId, field, value) => {
    const updated = (sow.boa_authorized_users || []).map((u) =>
      u.user_id === userId ? { ...u, [field]: value } : u,
    );
    updateSOWField('boa_authorized_users', updated);
  };

  const deleteAuthorizedUser = (userId) => {
    const updated = (sow.boa_authorized_users || []).filter((u) => u.user_id !== userId);
    updateSOWField('boa_authorized_users', updated);
  };

  const handleAIGenerate = (feature) => {
    toast.info('🤖 AI automation coming soon!', {
      description: `The "${feature}" feature will be available in the next release.`,
    });
  };

  if (loading) {
    return (
      <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-violet-500"></div>
      </div>
    );
  }

  if (!sow) {
    // Visible failure state replaces the previous silent ``return null``.
    // Without this branch a non-2xx loadSOW response made the click read
    // as a no-op: the parent had set selectedOpportunity + selectedLOB,
    // the modal mounted, fetched, failed, and returned null on the next
    // render with no indication of what happened.
    return (
      <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
        <div
          role="alertdialog"
          aria-labelledby="sow-load-error-title"
          className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-md p-6"
        >
          <div className="flex items-start gap-3 mb-4">
            <AlertCircle className="w-6 h-6 text-red-500 shrink-0 mt-0.5" />
            <div>
              <h3 id="sow-load-error-title" className="text-lg font-semibold text-aegisaxiom-text mb-1">
                SOW could not be loaded
              </h3>
              <p className="text-sm text-aegisaxiom-text-secondary">
                {loadError || 'The SOW endpoint returned no data.'}
              </p>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 bg-aegisaxiom-elevated text-aegisaxiom-text text-sm rounded-lg hover:bg-aegisaxiom-border transition"
            >
              Close
            </button>
            <button
              onClick={() => loadSOW()}
              className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition"
            >
              Retry
            </button>
          </div>
        </div>
      </div>
    );
  }

  const sections = [
    { id: 'smart', label: 'SMART Proposal', icon: Sparkles },
    { id: 'milestones', label: 'Detailed SOW', icon: FileText },
    // Resource Assignment (FS and PS only) - NOW INCLUDES CONTRACT CONFIG
    ...(lob === 'field_services' || lob === 'professional_services'
      ? [{ id: 'resources', label: 'Contract & Resources', icon: UserCheck }]
      : []),
    // LOB-specific product section
    ...(lob === 'field_services'
      ? [
          { id: 'materials', label: 'Materials', icon: Building2 },
          { id: 'hardware', label: 'Hardware', icon: Building2 },
          { id: 'services', label: 'Services', icon: Building2 },
        ]
      : []),
    ...(lob === 'professional_services'
      ? [
          { id: 'hardware', label: 'Hardware', icon: Building2 },
          { id: 'services', label: 'Services', icon: Building2 },
        ]
      : []),
    ...(lob === 'wholesale_mrr'
      ? [{ id: 'wholesale_mrr', label: 'Products & Services', icon: Building2 }]
      : []),
    { id: 'billing', label: 'Billing Details', icon: DollarSign },
  ];

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-6xl my-8 flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="sticky top-0 bg-aegisaxiom-surface border-b border-aegisaxiom-border p-6 rounded-t-lg z-10">
          <div className="flex items-start justify-between mb-4">
            <div className="flex-1">
              <div className="flex items-center gap-3 mb-2">
                <h2 className="text-2xl font-bold text-aegisaxiom-text">Statement of Work</h2>
                <span
                  className={`px-3 py-1 rounded-lg text-sm font-medium border ${LOB_COLORS[lob]}`}
                >
                  {LOB_LABELS[lob]}
                </span>

                {/* Expected Completion Date - Sales Ops Internal */}
                <div className="ml-4 flex items-center gap-2">
                  <label className="text-sm font-medium text-aegisaxiom-text-secondary flex items-center gap-2">
                    <Calendar className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                    Expected Completion:
                    {opportunity.desired_proposal_date &&
                      sow.expected_completion_date &&
                      new Date(sow.expected_completion_date) >
                        new Date(opportunity.desired_proposal_date) && (
                        <span className="text-yellow-600 dark:text-yellow-400 text-xs font-semibold flex items-center gap-1">
                          <AlertCircle className="w-3 h-3" />
                          After desired!
                        </span>
                      )}
                  </label>
                  <input
                    type="date"
                    value={sow.expected_completion_date || ''}
                    onChange={(e) => updateSOWField('expected_completion_date', e.target.value)}
                    className={`px-3 py-1.5 bg-aegisaxiom-base border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500 ${
                      opportunity.desired_proposal_date &&
                      sow.expected_completion_date &&
                      new Date(sow.expected_completion_date) >
                        new Date(opportunity.desired_proposal_date)
                        ? 'border-yellow-500'
                        : 'border-aegisaxiom-border'
                    }`}
                  />
                </div>
              </div>
              <p className="text-aegisaxiom-text-secondary text-sm">
                {opportunity.name} - {opportunity.account_name}
              </p>
            </div>
            <div className="flex gap-2">
              {onBack && (
                <button
                  onClick={onBack}
                  className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
                >
                  ← Back
                </button>
              )}
              <button
                onClick={onClose}
                className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
              >
                <X className="w-6 h-6" />
              </button>
            </div>
          </div>

          {/* Section Tabs */}
          <div className="flex gap-2 overflow-x-auto">
            {sections.map((section) => {
              const Icon = section.icon;
              const isSaved = savedTabs.has(section.id);
              return (
                <button
                  key={section.id}
                  onClick={() => setActiveSection(section.id)}
                  className={`flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg transition whitespace-nowrap ${
                    activeSection === section.id
                      ? 'bg-violet-600 text-white'
                      : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'
                  }`}
                >
                  <Icon className="w-4 h-4" />
                  {section.label}
                  {isSaved && (
                    <CheckCircle className="w-3.5 h-3.5 text-green-600 dark:text-green-400" />
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* Status Bar - Separate from sticky header to prevent content overlap */}
        <div className="px-6 pb-4 bg-aegisaxiom-surface border-b border-aegisaxiom-border">
          <SOWStatusBar
            currentStatus={sow.status || 'not_started'}
            blockerInfo={blockerInfo}
            loadingBlockers={loadingBlockers}
          />

          {/* Alert Context Banner (when opened from critical Kanban card) */}
          {alertContext && alertContext.alertContext && (
            <div className="mt-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-600/50 rounded-lg">
              <div className="flex items-start gap-2">
                <AlertCircle className="w-5 h-5 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
                <div className="flex-1">
                  <h4 className="text-sm font-semibold text-red-700 dark:text-red-300 mb-1">
                    Critical Issues Detected
                  </h4>
                  {alertContext.alertContext.type === 'blocker' && (
                    <ul className="space-y-1">
                      {alertContext.alertContext.messages.slice(0, 3).map((msg, idx) => (
                        <li key={idx} className="text-xs text-red-700 dark:text-red-300">
                          • {msg}
                        </li>
                      ))}
                    </ul>
                  )}
                  {alertContext.alertContext.type === 'alert' && (
                    <ul className="space-y-1">
                      {alertContext.alertContext.alerts.slice(0, 3).map((alert, idx) => (
                        <li key={idx} className="text-xs text-red-700 dark:text-red-300">
                          • {alert.rule_name}: {alert.message}
                        </li>
                      ))}
                    </ul>
                  )}
                  <p className="text-xs text-red-600 dark:text-red-400 mt-2">
                    👉 Review the{' '}
                    <span className="font-semibold">
                      {activeSection === 'smart'
                        ? 'SMART Proposal'
                        : activeSection === 'resources'
                          ? 'Contract & Resources'
                          : activeSection === 'billing'
                            ? 'Billing Details'
                            : 'current'}
                    </span>{' '}
                    section below
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Active Alerts Section - NEW: Shows all alerts for this SOW with LOB badges */}
          {alerts.length > 0 && (
            <div className="mt-4 space-y-2">
              <h4 className="text-sm font-semibold text-aegisaxiom-text flex items-center gap-2">
                <Bell className="w-4 h-4 text-yellow-600 dark:text-yellow-400" />
                Active Alerts for this SOW ({alerts.length})
              </h4>
              {alerts.map((alert) => (
                <AlertBanner
                  key={alert.alert_id}
                  alert={alert}
                  onDismiss={handleDismissAlert}
                  onAcknowledge={handleAcknowledgeAlert}
                  compact={false}
                  showActions={true}
                />
              ))}
            </div>
          )}
        </div>

        {/* Content */}
        <div className="p-6 flex-1 overflow-y-auto">
          {/* SMART Proposal Section */}
          {activeSection === 'smart' && (
            <SMARTSection
              sow={sow}
              updateSOW={updateSOWField}
              opportunity={opportunity}
              lob={lob}
              selectedTranscripts={selectedTranscripts}
              onTranscriptsSelected={setSelectedTranscripts}
            />
          )}

          {/* Contract Configuration Section */}
          {/* Detailed SOW (Milestones) Section */}
          {activeSection === 'milestones' && (
            <MilestonesSection
              sow={sow}
              updateSOW={updateSOWField}
              opportunity={opportunity}
              lob={lob}
              onAddMilestone={addMilestone}
              onUpdateMilestone={updateMilestone}
              onDeleteMilestone={deleteMilestone}
              onAddBullet={addBullet}
              onUpdateBullet={updateBullet}
              onDeleteBullet={deleteBullet}
              onAddSubtask={addSubtask}
              onUpdateSubtask={updateSubtask}
              onDeleteSubtask={deleteSubtask}
              selectedTranscripts={selectedTranscripts}
              onTranscriptsSelected={setSelectedTranscripts}
              onValidationResult={setValidationResult}
            />
          )}

          {/* Resource Assignments Section (Field Services & Professional Services only) */}
          {activeSection === 'resources' &&
            (lob === 'field_services' || lob === 'professional_services') && (
              <div className="space-y-8">
                {/* Resource Assignments */}
                <IntegratedResourceView
                  opportunity={opportunity}
                  sow={sow}
                  updateSOW={updateSOWField}
                  onResourceChange={checkBlockers}
                />
              </div>
            )}

          {/* Materials Section (Field Services only) */}
          {activeSection === 'materials' && lob === 'field_services' && (
            <ProductItemsSection
              sow={sow}
              updateSOW={updateSOWField}
              lob={lob}
              itemCategory="materials"
              sectionTitle="Materials"
              sectionDescription="Installation materials and consumables (one-time purchases)"
            />
          )}

          {/* Hardware Section (Field Services & Professional Services) */}
          {activeSection === 'hardware' &&
            (lob === 'field_services' || lob === 'professional_services') && (
              <ProductItemsSection
                sow={sow}
                updateSOW={updateSOWField}
                lob={lob}
                itemCategory="hardware"
                sectionTitle="Hardware"
                sectionDescription="Network equipment, servers, and devices (one-time purchases)"
              />
            )}

          {/* Services Section (Field Services & Professional Services) - NEW */}
          {activeSection === 'services' &&
            (lob === 'field_services' || lob === 'professional_services') && (
              <ProductItemsSection
                sow={sow}
                updateSOW={updateSOWField}
                lob={lob}
                itemCategory="services"
                sectionTitle="Services"
                sectionDescription="Your owned professional/field services (recurring or one-time)"
              />
            )}

          {/* Products & Services Section (Wholesale MRR only) */}
          {activeSection === 'wholesale_mrr' && lob === 'wholesale_mrr' && (
            <ProductItemsSection
              sow={sow}
              updateSOW={updateSOWField}
              lob={lob}
              itemCategory="wholesale_mrr"
              sectionTitle="Products & Services"
              sectionDescription="Wholesale recurring revenue products (resale)"
            />
          )}

          {/* Billing Details Section */}
          {activeSection === 'billing' && (
            <BillingDetailsSection
              sow={sow}
              updateSOW={updateSOWField}
              onAddAuthorizedUser={addAuthorizedUser}
              onUpdateAuthorizedUser={updateAuthorizedUser}
              onDeleteAuthorizedUser={deleteAuthorizedUser}
              opportunity={opportunity}
              lob={lob}
              selectedTranscripts={selectedTranscripts}
              onTranscriptsSelected={setSelectedTranscripts}
            />
          )}
        </div>

        {/* Footer Actions */}
        <div className="sticky bottom-0 bg-aegisaxiom-surface border-t border-aegisaxiom-border p-6 rounded-b-lg flex justify-between items-center">
          <div className="flex items-center gap-3">
            <span className="text-sm text-aegisaxiom-text-secondary">
              Version {sow.version || 1} • {sow.status || 'not_started'}
            </span>
            {sow.google_doc_url && (
              <a
                href={sow.google_doc_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 flex items-center gap-1 transition"
              >
                <FileText className="w-3.5 h-3.5" />
                View Google Doc
              </a>
            )}
          </div>
          <div className="flex gap-3">
            {/* Reset SOW — admin/director only, hidden when complete */}
            {canResetSOW && (
              <button
                onClick={() => setShowResetConfirm(true)}
                disabled={resetting}
                className="flex items-center gap-2 px-4 py-2 bg-aegisaxiom-elevated hover:bg-red-600 text-red-600 dark:text-red-400 hover:text-white rounded-lg transition border border-red-900/50 mr-4 disabled:opacity-50"
              >
                <Trash2 className="w-4 h-4" />
                {resetting ? 'Resetting...' : 'Reset SOW'}
              </button>
            )}

            <button
              onClick={onClose}
              className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg transition"
            >
              Cancel
            </button>

            {/* Generate & Send — available once 3+ tabs are saved, before sent/complete */}
            {allTabsSaved && sow.status !== 'ready_for_review' && sow.status !== 'complete' && (
              <button
                onClick={handleMarkComplete}
                disabled={finalizing}
                className="px-4 py-2 bg-green-600 hover:bg-green-500 text-white rounded-lg transition flex items-center gap-2"
              >
                <FileText className="w-4 h-4" />
                {finalizing ? 'Finalizing...' : 'Generate & Send'}
              </button>
            )}

            {/* Client Has Signed — only when ready_for_review */}
            {sow.status === 'ready_for_review' && (
              <button
                onClick={() => setShowSignedConfirm(true)}
                className="px-4 py-2 bg-green-600 hover:bg-green-500 text-white rounded-lg transition flex items-center gap-2"
              >
                <CheckCircle className="w-4 h-4" />
                Client Has Signed
              </button>
            )}

            {/* Save SOW — hide when complete */}
            {sow.status !== 'complete' && (
              <button
                onClick={saveSOW}
                disabled={saving}
                className="px-6 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg transition disabled:opacity-50 flex items-center gap-2"
              >
                <Save className="w-4 h-4" />
                {saving ? 'Saving...' : 'Save SOW'}
              </button>
            )}
          </div>
        </div>

        {/* Finalize Confirmation Modal */}
        {showFinalizeConfirm && (
          <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-[120]">
            <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-md p-6 m-4">
              <h3 className="text-lg font-bold text-aegisaxiom-text mb-3">Finalize Statement of Work</h3>

              {/* Validation Score Warning */}
              {validationResult?.has_guide && validationResult?.checklist && (
                <div
                  className={`mb-4 border rounded-lg p-3 ${
                    validationResult.checklist.overall_score >= 80
                      ? 'bg-emerald-50 dark:bg-emerald-500/10 border-emerald-500/30'
                      : validationResult.checklist.overall_score >= 50
                        ? 'bg-amber-50 dark:bg-amber-500/10 border-amber-500/30'
                        : 'bg-red-50 dark:bg-red-500/10 border-red-500/30'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-medium text-aegisaxiom-text-secondary flex items-center gap-1.5">
                      <ShieldCheck className="w-3.5 h-3.5 text-violet-600 dark:text-violet-400" />
                      Guide Validation Score
                    </span>
                    <span
                      className={`text-lg font-bold ${
                        validationResult.checklist.overall_score >= 80
                          ? 'text-emerald-600 dark:text-emerald-400'
                          : validationResult.checklist.overall_score >= 50
                            ? 'text-amber-600 dark:text-amber-400'
                            : 'text-red-600 dark:text-red-400'
                      }`}
                    >
                      {validationResult.checklist.overall_score}%
                    </span>
                  </div>
                  <p className="text-xs text-aegisaxiom-text-secondary">
                    {validationResult.checklist.summary}
                  </p>
                  {validationResult.checklist.missing?.length > 0 && (
                    <div className="mt-2 text-xs text-red-700 dark:text-red-300/80">
                      <span className="font-medium">
                        {validationResult.checklist.missing.length} missing item
                        {validationResult.checklist.missing.length !== 1 ? 's' : ''}:
                      </span>{' '}
                      {validationResult.checklist.missing.map((m) => m.item).join(', ')}
                    </div>
                  )}
                </div>
              )}

              {!validationResult?.has_guide && (
                <div className="mb-4 bg-amber-50 dark:bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 flex items-start gap-2">
                  <AlertTriangle className="w-4 h-4 text-amber-600 dark:text-amber-400 mt-0.5 flex-shrink-0" />
                  <p className="text-xs text-amber-700 dark:text-amber-300">
                    No best-practice guide was used to validate this SOW. The document was generated
                    without guide validation.
                  </p>
                </div>
              )}

              <p className="text-aegisaxiom-text-secondary text-sm mb-2">This will:</p>
              <ul className="text-aegisaxiom-text-secondary text-sm space-y-1.5 mb-6 ml-4">
                <li>• Generate an AI "Assumptions & Risks" section</li>
                <li>• Create a formatted Google Document</li>
              </ul>
              <p className="text-aegisaxiom-text-secondary text-sm mb-6">
                Are you sure you want to proceed?
              </p>
              <div className="flex gap-3 justify-end">
                <button
                  onClick={() => setShowFinalizeConfirm(false)}
                  className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm text-sm rounded-lg transition"
                >
                  Review Again
                </button>
                <button
                  onClick={finalizeSOW}
                  className="px-5 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition flex items-center gap-2"
                >
                  <FileText className="w-4 h-4" />
                  Generate Document
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Client Signed Confirmation Modal */}
        {showSignedConfirm && (
          <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-[120]">
            <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-md p-6 m-4">
              <h3 className="text-lg font-bold text-aegisaxiom-text mb-3">Confirm Client Signature</h3>
              <p className="text-aegisaxiom-text-secondary text-sm mb-4">
                Please verify that the client has signed the Statement of Work via Google Docs
                eSignature.
              </p>
              {sow.google_doc_url && (
                <a
                  href={sow.google_doc_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 text-sm text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 mb-4 transition"
                >
                  <FileText className="w-4 h-4" />
                  Open Google Doc to verify
                </a>
              )}
              <p className="text-aegisaxiom-text-secondary text-sm mb-6">
                This will mark the SOW as complete and notify the sales team.
              </p>
              <div className="flex gap-3 justify-end">
                <button
                  onClick={() => setShowSignedConfirm(false)}
                  className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm text-sm rounded-lg transition"
                >
                  Not Yet
                </button>
                <button
                  onClick={confirmClientSigned}
                  className="px-5 py-2 bg-green-600 hover:bg-green-500 text-white text-sm rounded-lg transition flex items-center gap-2"
                >
                  <CheckCircle className="w-4 h-4" />
                  Confirm Signed
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Finalizing Loading Overlay */}
        {finalizing && (
          <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-[120]">
            <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-8 flex flex-col items-center gap-4">
              <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-violet-500"></div>
              <p className="text-aegisaxiom-text font-medium">Generating Statement of Work...</p>
              <p className="text-aegisaxiom-text-secondary text-sm">Creating Google Document</p>
            </div>
          </div>
        )}

        <ConfirmModal
          isOpen={showResetConfirm}
          title="Reset SOW?"
          message={
            'This permanently deletes all milestones, materials, hardware, services, and billing details for this LOB.\n\nThe opportunity itself will NOT be deleted. This cannot be undone.'
          }
          confirmText="Reset SOW"
          variant="danger"
          onConfirm={handleResetSOW}
          onCancel={() => setShowResetConfirm(false)}
        />
      </div>
    </div>
  );
}

// SMART Proposal Section
function SMARTSection({
  sow,
  updateSOW,
  opportunity,
  lob,
  selectedTranscripts,
  onTranscriptsSelected,
}) {
  const [showTranscriptSelector, setShowTranscriptSelector] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generatingStatus, setGeneratingStatus] = useState('');

  const smartFields = [
    { key: 'smart_specific', label: 'Specific', placeholder: 'What exactly will be delivered?' },
    { key: 'smart_measurable', label: 'Measurable', placeholder: 'How will success be measured?' },
    { key: 'smart_achievable', label: 'Achievable', placeholder: 'What resources are needed?' },
    {
      key: 'smart_relevant',
      label: 'Relevant',
      placeholder: 'Why does this matter to the client?',
    },
    { key: 'smart_time_bound', label: 'Time-Bound', placeholder: 'What are the key deadlines?' },
  ];

  const handleTranscriptsSelected = async (selected) => {
    setShowTranscriptSelector(false);
    // Store selected transcripts in shared state for other tabs to reuse
    onTranscriptsSelected(selected);
    const transcriptIds = selected.map((t) => t.id);
    setGenerating(true);
    setGeneratingStatus('Reading transcripts...');

    try {
      // Call the backend to fetch transcripts + generate SMART
      setGeneratingStatus('Generating SMART proposal...');
      const response = await api.post(`${API}/api/sales-ops/fireflies/generate-smart`, {
        transcript_ids: transcriptIds,
        opportunity_id: opportunity.opportunity_id,
        lob: lob,
      });

      if (response.ok) {
        const data = await response.json();
        const smart = data.smart;

        // Populate executive summary + all 5 SMART fields
        updateSOW('executive_summary', smart.executive_summary || '');
        updateSOW('smart_specific', smart.smart_specific || '');
        updateSOW('smart_measurable', smart.smart_measurable || '');
        updateSOW('smart_achievable', smart.smart_achievable || '');
        updateSOW('smart_relevant', smart.smart_relevant || '');
        updateSOW('smart_time_bound', smart.smart_time_bound || '');

        toast.success(`SMART proposal generated from ${data.transcripts_used} transcript(s)`, {
          description: data.transcript_titles?.join(', '),
        });
      } else {
        // I8. Mirror the loadSOW pattern. Some upstream layers serve an
        // HTML 502 page rather than JSON. response.json then throws and
        // the outer catch reports a generic network error which hides
        // the real status. Wrap the parse in its own try and fall back
        // to status plus statusText when the body is not JSON.
        let err = null;
        try {
          err = await response.json();
        } catch (_parseError) {
          err = null;
        }
        if (err) {
          const { title, description } = describeFirefliesError(
            err,
            'Failed to generate SMART proposal',
          );
          toast.error(title, description ? { description } : undefined);
        } else {
          const statusText = `${response.status} ${response.statusText || ''}`.trim();
          toast.error(statusText || 'Unexpected error');
        }
      }
    } catch (error) {
      console.error('Error generating SMART proposal:', error);
      toast.error('Failed to generate SMART proposal');
    } finally {
      setGenerating(false);
      setGeneratingStatus('');
    }
  };

  return (
    <div className="space-y-6">
      {/* AI Generate Button */}
      <div className="flex justify-between items-center">
        <h3 className="text-lg font-semibold text-aegisaxiom-text">SMART Proposal Framework</h3>
        <button
          onClick={() => setShowTranscriptSelector(true)}
          disabled={generating}
          className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-violet-600 to-purple-600 hover:from-violet-500 hover:to-purple-500 text-aegisaxiom-text text-sm rounded-lg transition disabled:opacity-50"
        >
          <Sparkles className="w-4 h-4" />
          {generating ? 'Generating...' : 'Generate with AI'}
        </button>
      </div>

      {/* Selected Transcripts Banner */}
      {selectedTranscripts.length > 0 && (
        <SelectedTranscriptsBanner transcripts={selectedTranscripts} />
      )}

      {/* Generation Loading State */}
      {generating && (
        <div className="bg-violet-900/20 border border-violet-200 dark:border-violet-600/30 rounded-lg p-6 flex items-center justify-center gap-3">
          <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-violet-400"></div>
          <span className="text-violet-700 dark:text-violet-300 text-sm font-medium">
            {generatingStatus}
          </span>
        </div>
      )}

      {smartFields.map((field) => (
        <div key={field.key}>
          <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
            {field.label}
          </label>
          <AutoGrowTextarea
            value={sow[field.key] || ''}
            onChange={(e) => updateSOW(field.key, e.target.value)}
            placeholder={field.placeholder}
            rows={3}
            className="w-full px-4 py-3 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 resize-none"
          />
        </div>
      ))}

      {/* Simple Transcript Selector Modal (SMART — no validation) */}
      {showTranscriptSelector && (
        <SimpleTranscriptSelector
          onSelect={handleTranscriptsSelected}
          onClose={() => setShowTranscriptSelector(false)}
        />
      )}
    </div>
  );
}

// Selected Transcripts Banner (shared across SMART + Milestones tabs)
function SelectedTranscriptsBanner({ transcripts }) {
  if (!transcripts || transcripts.length === 0) return null;

  return (
    <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg px-4 py-3 flex items-start gap-3">
      <FileText className="w-4 h-4 text-violet-600 dark:text-violet-400 mt-0.5 flex-shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">
          Generated from {transcripts.length} Fireflies transcript
          {transcripts.length !== 1 ? 's' : ''}
        </p>
        <div className="flex flex-wrap gap-1.5">
          {transcripts.map((t) => (
            <span
              key={t.id}
              className="inline-flex items-center gap-1 px-2 py-0.5 bg-violet-50 dark:bg-violet-600/20 border border-violet-500/30 rounded text-xs text-violet-700 dark:text-violet-300 truncate max-w-[250px]"
            >
              {t.title}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

// Fireflies.ai Transcript Selector Modal — Multi-step: Select → Validate → Checklist → Generate
function FirefliesTranscriptSelector({ onSelect, onClose, onValidationComplete }) {
  // Step management: 'select' | 'validate' | 'checklist'
  const [step, setStep] = useState('select');

  // Step 1: Transcript selection
  const [transcripts, setTranscripts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedIds, setSelectedIds] = useState(new Set());

  // Step 2: Guide validation
  const [guides, setGuides] = useState([]);
  const [loadingGuides, setLoadingGuides] = useState(false);
  const [selectedGuideId, setSelectedGuideId] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [validating, setValidating] = useState(false);

  // Step 3: Checklist results
  const [validationResult, setValidationResult] = useState(null);

  const API = API_URL;

  useEffect(() => {
    loadTranscripts();
  }, []);

  const loadTranscripts = async () => {
    try {
      setLoading(true);
      const response = await api.get(`${API}/api/sales-ops/fireflies/transcripts`);
      if (response.ok) {
        const data = await response.json();
        setTranscripts(data.items || []);
      } else {
        // I8. Mirror the loadSOW pattern. When the upstream returns a
        // non JSON body, response.json throws and the outer catch hides
        // the real status. Parse the body inside its own try and fall
        // back to status plus statusText on parse failure.
        let err = null;
        try {
          err = await response.json();
        } catch (_parseError) {
          err = null;
        }
        if (err) {
          const { title, description } = describeFirefliesError(
            err,
            'Failed to load transcripts from Fireflies.ai',
          );
          toast.error(title, description ? { description } : undefined);
        } else {
          const statusText = `${response.status} ${response.statusText || ''}`.trim();
          toast.error(statusText || 'Unexpected error');
        }
      }
    } catch (error) {
      console.error('Error loading transcripts:', error);
      toast.error('Failed to connect to Fireflies.ai');
    } finally {
      setLoading(false);
    }
  };

  const loadGuides = async () => {
    try {
      setLoadingGuides(true);
      const response = await api.get(`${API}/api/sales-ops/guides`);
      if (response.ok) {
        const data = await response.json();
        setGuides(data.items || []);
      }
    } catch (error) {
      console.error('Error loading guides:', error);
    } finally {
      setLoadingGuides(false);
    }
  };

  const toggleSelect = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const filteredTranscripts = transcripts.filter((t) => {
    if (!searchQuery) return true;
    const search = searchQuery.toLowerCase();
    return (
      t.title?.toLowerCase().includes(search) ||
      t.summary?.toLowerCase().includes(search) ||
      (t.participants || []).some((p) => p?.toLowerCase().includes(search))
    );
  });

  const formatDate = (timestamp) => {
    if (!timestamp) return '';
    const date = typeof timestamp === 'number' ? new Date(timestamp) : new Date(timestamp);
    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  };

  const formatDuration = (seconds) => {
    if (!seconds) return '';
    const mins = Math.floor(seconds / 60);
    if (mins < 60) return `${mins}m`;
    const hrs = Math.floor(mins / 60);
    const remainMins = mins % 60;
    return `${hrs}h ${remainMins}m`;
  };

  // Move to validation step
  const handleProceedToValidation = () => {
    setStep('validate');
    loadGuides();
  };

  // Upload a new guide
  const handleGuideUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);
    formData.append('title', file.name.replace(/\.[^/.]+$/, ''));

    try {
      setUploading(true);
      const response = await api.postForm(`${API}/api/sales-ops/guides/upload`, formData);
      if (response.ok) {
        const data = await response.json();
        toast.success(`Guide "${data.title}" uploaded (${data.chunk_count} sections indexed)`);
        setSelectedGuideId(data.guide_id);
        await loadGuides();
      } else {
        const err = await response.json();
        toast.error(err.detail || 'Failed to upload guide');
      }
    } catch (error) {
      console.error('Error uploading guide:', error);
      toast.error('Failed to upload guide');
    } finally {
      setUploading(false);
      // Reset the file input
      e.target.value = '';
    }
  };

  // Run validation
  const handleValidate = async () => {
    const transcriptIds = transcripts.filter((t) => selectedIds.has(t.id)).map((t) => t.id);

    try {
      setValidating(true);
      const response = await api.post(`${API}/api/sales-ops/fireflies/validate-transcripts`, {
        transcript_ids: transcriptIds,
        guide_id: selectedGuideId || undefined,
      });

      if (response.ok) {
        const data = await response.json();
        setValidationResult(data);
        setStep('checklist');
        // Bubble validation result up to parent for use in finalize modal
        if (onValidationComplete) onValidationComplete(data);
      } else {
        const err = await response.json();
        toast.error(resolveErrorDetail(err));
      }
    } catch (error) {
      console.error('Error validating transcripts:', error);
      toast.error('Failed to validate transcripts');
    } finally {
      setValidating(false);
    }
  };

  // Skip validation and generate directly
  const handleSkipValidation = () => {
    const selected = transcripts
      .filter((t) => selectedIds.has(t.id))
      .map((t) => ({ id: t.id, title: t.title }));
    onSelect(selected);
  };

  // Proceed to generate after reviewing checklist
  const handleProceedToGenerate = () => {
    const selected = transcripts
      .filter((t) => selectedIds.has(t.id))
      .map((t) => ({ id: t.id, title: t.title }));
    onSelect(selected);
  };

  const getScoreColor = (score) => {
    if (score >= 80) return 'text-emerald-600 dark:text-emerald-400';
    if (score >= 50) return 'text-amber-600 dark:text-amber-400';
    return 'text-red-600 dark:text-red-400';
  };

  const getScoreBg = (score) => {
    if (score >= 80) return 'bg-emerald-50 dark:bg-emerald-500/20 border-emerald-500/30';
    if (score >= 50) return 'bg-amber-50 dark:bg-amber-500/20 border-amber-500/30';
    return 'bg-red-50 dark:bg-red-500/20 border-red-500/30';
  };

  // Step indicator
  const steps = [
    { key: 'select', label: 'Select Transcripts' },
    { key: 'validate', label: 'Guide Validation' },
    { key: 'checklist', label: 'Review Checklist' },
  ];

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-[110] p-4">
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-3xl max-h-[85vh] flex flex-col">
        {/* Header with Step Indicator */}
        <div className="bg-gradient-to-r from-violet-600/20 to-purple-600/20 border-b border-aegisaxiom-border p-5">
          <div className="flex items-start justify-between mb-4">
            <div>
              <h3 className="text-lg font-bold text-aegisaxiom-text flex items-center gap-2">
                <Sparkles className="w-5 h-5 text-violet-600 dark:text-violet-400" />
                {step === 'select' && 'Select Meeting Transcripts'}
                {step === 'validate' && 'Validate Against Best Practices'}
                {step === 'checklist' && 'Validation Checklist'}
              </h3>
              <p className="text-sm text-aegisaxiom-text-secondary mt-1">
                {step === 'select' && 'Choose one or more Fireflies.ai transcripts'}
                {step === 'validate' &&
                  'Upload or select a best-practice guide to validate your transcripts'}
                {step === 'checklist' && 'Review gaps before generating your SOW'}
              </p>
            </div>
            <button
              onClick={onClose}
              className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Step Progress */}
          <div className="flex items-center gap-2">
            {steps.map((s, i) => (
              <React.Fragment key={s.key}>
                <div
                  className={`flex items-center gap-1.5 text-xs font-medium ${
                    s.key === step
                      ? 'text-violet-700 dark:text-violet-300'
                      : steps.indexOf(steps.find((x) => x.key === step)) > i
                        ? 'text-emerald-600 dark:text-emerald-400'
                        : 'text-aegisaxiom-text-muted'
                  }`}
                >
                  <div
                    className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold border ${
                      s.key === step
                        ? 'bg-violet-600 border-violet-500 text-white'
                        : steps.indexOf(steps.find((x) => x.key === step)) > i
                          ? 'bg-emerald-600 border-emerald-500 text-white'
                          : 'border-aegisaxiom-border-hover text-aegisaxiom-text-muted'
                    }`}
                  >
                    {steps.indexOf(steps.find((x) => x.key === step)) > i ? (
                      <CheckCircle className="w-3.5 h-3.5" />
                    ) : (
                      i + 1
                    )}
                  </div>
                  <span className="hidden sm:inline">{s.label}</span>
                </div>
                {i < steps.length - 1 && (
                  <div
                    className={`flex-1 h-px ${
                      steps.indexOf(steps.find((x) => x.key === step)) > i
                        ? 'bg-emerald-500/50'
                        : 'bg-aegisaxiom-elevated'
                    }`}
                  />
                )}
              </React.Fragment>
            ))}
          </div>

          {/* Search Bar (Step 1 only) */}
          {step === 'select' && (
            <div className="mt-4 relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-aegisaxiom-text-secondary" />
              <input
                type="text"
                placeholder="Search by title, participant, or summary..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-10 pr-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 text-sm"
              />
            </div>
          )}
        </div>

        {/* ─── STEP 1: Transcript Selection ─── */}
        {step === 'select' && (
          <>
            <div className="flex-1 overflow-y-auto p-4 space-y-2">
              {loading ? (
                <div className="flex items-center justify-center py-12">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-violet-500"></div>
                </div>
              ) : filteredTranscripts.length === 0 ? (
                <div className="text-center py-12">
                  <FileText className="w-12 h-12 text-aegisaxiom-text-muted mx-auto mb-3" />
                  <p className="text-aegisaxiom-text-secondary text-sm">
                    {searchQuery
                      ? 'No transcripts match your search'
                      : 'No transcripts found in Fireflies.ai'}
                  </p>
                </div>
              ) : (
                filteredTranscripts.map((t) => {
                  const isSelected = selectedIds.has(t.id);
                  return (
                    <div
                      key={t.id}
                      onClick={() => toggleSelect(t.id)}
                      className={`p-4 rounded-lg border cursor-pointer transition ${
                        isSelected
                          ? 'bg-violet-50 dark:bg-violet-600/20 border-violet-500'
                          : 'bg-aegisaxiom-base border-aegisaxiom-border hover:border-aegisaxiom-border-hover'
                      }`}
                    >
                      <div className="flex items-start gap-3">
                        <div
                          className={`mt-0.5 w-5 h-5 rounded border-2 flex items-center justify-center flex-shrink-0 transition ${
                            isSelected
                              ? 'bg-violet-600 border-violet-600'
                              : 'border-aegisaxiom-border-hover'
                          }`}
                        >
                          {isSelected && <CheckCircle className="w-3.5 h-3.5 text-aegisaxiom-text" />}
                        </div>
                        <div className="flex-1 min-w-0">
                          <h4 className="font-medium text-aegisaxiom-text text-sm truncate">{t.title}</h4>
                          <div className="flex items-center gap-3 mt-1 text-xs text-aegisaxiom-text-secondary">
                            {t.date && (
                              <span className="flex items-center gap-1">
                                <Calendar className="w-3 h-3" />
                                {formatDate(t.date)}
                              </span>
                            )}
                            {t.duration && (
                              <span className="flex items-center gap-1">
                                <Clock className="w-3 h-3" />
                                {formatDuration(t.duration)}
                              </span>
                            )}
                            {t.participants?.length > 0 && (
                              <span className="flex items-center gap-1">
                                <Users className="w-3 h-3" />
                                {t.participants.length} participant
                                {t.participants.length !== 1 ? 's' : ''}
                              </span>
                            )}
                          </div>
                          {t.summary && (
                            <p className="text-xs text-aegisaxiom-text-muted mt-1.5 line-clamp-2">
                              {t.summary}
                            </p>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>

            <div className="border-t border-aegisaxiom-border p-4 flex items-center justify-between">
              <span className="text-sm text-aegisaxiom-text-secondary">
                {selectedIds.size} transcript{selectedIds.size !== 1 ? 's' : ''} selected
              </span>
              <div className="flex gap-3">
                <button
                  onClick={onClose}
                  className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm text-sm rounded-lg transition"
                >
                  Cancel
                </button>
                <button
                  onClick={handleProceedToValidation}
                  disabled={selectedIds.size === 0}
                  className="px-5 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                >
                  Next: Validate
                  <ArrowRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          </>
        )}

        {/* ─── STEP 2: Guide Validation ─── */}
        {step === 'validate' && (
          <>
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {/* Upload New Guide */}
              <div className="bg-aegisaxiom-base border border-dashed border-aegisaxiom-border-hover rounded-lg p-6">
                <div className="text-center">
                  <Upload className="w-10 h-10 text-aegisaxiom-text-muted mx-auto mb-3" />
                  <h4 className="text-aegisaxiom-text font-medium text-sm mb-1">
                    Upload a Best Practice Guide
                  </h4>
                  <p className="text-aegisaxiom-text-secondary text-xs mb-4">
                    PDF, DOCX, or TXT — will be saved to the knowledge base for future use
                  </p>
                  <label
                    className={`inline-flex items-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition cursor-pointer ${
                      uploading ? 'opacity-50 pointer-events-none' : ''
                    }`}
                  >
                    {uploading ? (
                      <>
                        <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
                        Uploading...
                      </>
                    ) : (
                      <>
                        <Upload className="w-4 h-4" />
                        Choose File
                      </>
                    )}
                    <input
                      type="file"
                      accept=".pdf,.docx,.doc,.txt"
                      onChange={handleGuideUpload}
                      className="hidden"
                      disabled={uploading}
                    />
                  </label>
                </div>
              </div>

              {/* Existing Guides */}
              <div>
                <h4 className="text-sm font-medium text-aegisaxiom-text-secondary mb-2 flex items-center gap-2">
                  <BookOpen className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                  Knowledge Base Guides
                </h4>
                {loadingGuides ? (
                  <div className="flex items-center justify-center py-8">
                    <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-violet-500"></div>
                  </div>
                ) : guides.length === 0 ? (
                  <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-6 text-center">
                    <BookOpen className="w-8 h-8 text-aegisaxiom-text-muted mx-auto mb-2" />
                    <p className="text-aegisaxiom-text-secondary text-sm">
                      No guides in the knowledge base yet
                    </p>
                    <p className="text-aegisaxiom-text-muted text-xs mt-1">
                      Upload one above, or proceed to auto-search
                    </p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {guides.map((g) => (
                      <div
                        key={g.guide_id}
                        onClick={() =>
                          setSelectedGuideId(selectedGuideId === g.guide_id ? null : g.guide_id)
                        }
                        className={`p-3 rounded-lg border cursor-pointer transition flex items-center gap-3 ${
                          selectedGuideId === g.guide_id
                            ? 'bg-violet-50 dark:bg-violet-600/20 border-violet-500'
                            : 'bg-aegisaxiom-base border-aegisaxiom-border hover:border-aegisaxiom-border-hover'
                        }`}
                      >
                        <div
                          className={`w-5 h-5 rounded border-2 flex items-center justify-center flex-shrink-0 transition ${
                            selectedGuideId === g.guide_id
                              ? 'bg-violet-600 border-violet-600'
                              : 'border-aegisaxiom-border-hover'
                          }`}
                        >
                          {selectedGuideId === g.guide_id && (
                            <CheckCircle className="w-3.5 h-3.5 text-aegisaxiom-text" />
                          )}
                        </div>
                        <div className="flex-1 min-w-0">
                          <h5 className="text-sm font-medium text-aegisaxiom-text truncate">{g.title}</h5>
                          <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">
                            {g.chunk_count} sections &middot; {g.filename}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Auto-search info */}
              {!selectedGuideId && guides.length > 0 && (
                <div className="bg-blue-50 dark:bg-blue-500/10 border border-blue-500/20 rounded-lg p-3 flex items-start gap-2">
                  <Search className="w-4 h-4 text-blue-600 dark:text-blue-400 mt-0.5 flex-shrink-0" />
                  <p className="text-xs text-blue-700 dark:text-blue-300">
                    No guide selected — the system will automatically search the knowledge base for
                    the most relevant guide based on your transcript content.
                  </p>
                </div>
              )}
            </div>

            <div className="border-t border-aegisaxiom-border p-4 flex items-center justify-between">
              <button
                onClick={() => setStep('select')}
                className="flex items-center gap-2 px-4 py-2 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text text-sm transition"
              >
                <ChevronLeft className="w-4 h-4" />
                Back
              </button>
              <div className="flex gap-3">
                <button
                  onClick={handleSkipValidation}
                  className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm text-sm rounded-lg transition flex items-center gap-2"
                >
                  <AlertTriangle className="w-3.5 h-3.5 text-amber-600 dark:text-amber-400" />
                  Skip & Generate
                </button>
                <button
                  onClick={handleValidate}
                  disabled={validating}
                  className="px-5 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition disabled:opacity-50 flex items-center gap-2"
                >
                  {validating ? (
                    <>
                      <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
                      Validating...
                    </>
                  ) : (
                    <>
                      <ShieldCheck className="w-4 h-4" />
                      Validate Transcripts
                    </>
                  )}
                </button>
              </div>
            </div>
          </>
        )}

        {/* ─── STEP 3: Checklist Review ─── */}
        {step === 'checklist' && validationResult && (
          <>
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {/* No guide found */}
              {!validationResult.has_guide && (
                <div className="bg-amber-50 dark:bg-amber-500/10 border border-amber-500/30 rounded-lg p-5 text-center">
                  <AlertTriangle className="w-10 h-10 text-amber-600 dark:text-amber-400 mx-auto mb-3" />
                  <h4 className="text-aegisaxiom-text font-medium mb-1">No Best Practice Guide Found</h4>
                  <p className="text-aegisaxiom-text-secondary text-sm mb-4">
                    {validationResult.message}
                  </p>
                  <div className="flex gap-3 justify-center">
                    <button
                      onClick={() => setStep('validate')}
                      className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm text-sm rounded-lg transition"
                    >
                      Go Back & Upload
                    </button>
                    <button
                      onClick={handleProceedToGenerate}
                      className="px-4 py-2 bg-amber-600 hover:bg-amber-500 text-white text-sm rounded-lg transition flex items-center gap-2"
                    >
                      <AlertTriangle className="w-3.5 h-3.5" />
                      Proceed Without Guide
                    </button>
                  </div>
                </div>
              )}

              {/* Checklist results */}
              {validationResult.has_guide && validationResult.checklist && (
                <>
                  {/* Score + Summary */}
                  <div
                    className={`border rounded-lg p-4 ${getScoreBg(validationResult.checklist.overall_score)}`}
                  >
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <ShieldCheck className="w-5 h-5 text-violet-600 dark:text-violet-400" />
                        <span className="text-sm font-medium text-aegisaxiom-text">
                          Validated against: {validationResult.guide_title}
                        </span>
                      </div>
                      <span
                        className={`text-2xl font-bold ${getScoreColor(validationResult.checklist.overall_score)}`}
                      >
                        {validationResult.checklist.overall_score}%
                      </span>
                    </div>
                    <p className="text-sm text-aegisaxiom-text-secondary">
                      {validationResult.checklist.summary}
                    </p>
                  </div>

                  {/* Covered Items */}
                  {validationResult.checklist.covered?.length > 0 && (
                    <div>
                      <h4 className="text-sm font-medium text-emerald-600 dark:text-emerald-400 mb-2 flex items-center gap-2">
                        <CheckCircle className="w-4 h-4" />
                        Covered ({validationResult.checklist.covered.length})
                      </h4>
                      <div className="space-y-1.5">
                        {validationResult.checklist.covered.map((item, i) => (
                          <div
                            key={i}
                            className="bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-500/20 rounded-lg p-3"
                          >
                            <p className="text-sm text-aegisaxiom-text">{item.item}</p>
                            <p className="text-xs text-emerald-700 dark:text-emerald-300/70 mt-1">
                              {item.evidence}
                            </p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Partial Items */}
                  {validationResult.checklist.partial?.length > 0 && (
                    <div>
                      <h4 className="text-sm font-medium text-amber-600 dark:text-amber-400 mb-2 flex items-center gap-2">
                        <CircleDot className="w-4 h-4" />
                        Needs More Detail ({validationResult.checklist.partial.length})
                      </h4>
                      <div className="space-y-1.5">
                        {validationResult.checklist.partial.map((item, i) => (
                          <div
                            key={i}
                            className="bg-amber-50 dark:bg-amber-500/10 border border-amber-500/20 rounded-lg p-3"
                          >
                            <p className="text-sm text-aegisaxiom-text">{item.item}</p>
                            <p className="text-xs text-amber-700 dark:text-amber-300/70 mt-1">
                              {item.detail_needed}
                            </p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Missing Items */}
                  {validationResult.checklist.missing?.length > 0 && (
                    <div>
                      <h4 className="text-sm font-medium text-red-600 dark:text-red-400 mb-2 flex items-center gap-2">
                        <AlertTriangle className="w-4 h-4" />
                        Missing ({validationResult.checklist.missing.length})
                      </h4>
                      <div className="space-y-1.5">
                        {validationResult.checklist.missing.map((item, i) => (
                          <div
                            key={i}
                            className="bg-red-50 dark:bg-red-500/10 border border-red-500/20 rounded-lg p-3"
                          >
                            <p className="text-sm text-aegisaxiom-text">{item.item}</p>
                            <p className="text-xs text-red-700 dark:text-red-300/70 mt-1">
                              {item.why_important}
                            </p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Footer for checklist step */}
            {validationResult.has_guide && (
              <div className="border-t border-aegisaxiom-border p-4 flex items-center justify-between">
                <button
                  onClick={() => setStep('validate')}
                  className="flex items-center gap-2 px-4 py-2 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text text-sm transition"
                >
                  <ChevronLeft className="w-4 h-4" />
                  Back
                </button>
                <div className="flex gap-3">
                  <button
                    onClick={onClose}
                    className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm text-sm rounded-lg transition"
                  >
                    Schedule Another Call
                  </button>
                  <button
                    onClick={handleProceedToGenerate}
                    className={`px-5 py-2 text-aegisaxiom-text text-sm rounded-lg transition flex items-center gap-2 ${
                      validationResult.checklist?.overall_score >= 50
                        ? 'bg-violet-600 hover:bg-violet-500'
                        : 'bg-amber-600 hover:bg-amber-500'
                    }`}
                  >
                    {validationResult.checklist?.overall_score < 50 && (
                      <AlertTriangle className="w-3.5 h-3.5" />
                    )}
                    {validationResult.checklist?.overall_score < 50
                      ? 'Proceed Anyway'
                      : 'Proceed to Generate'}
                    <Sparkles className="w-4 h-4" />
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// Simple Transcript Selector — used by SMART and Billing (no validation step)
function SimpleTranscriptSelector({ onSelect, onClose }) {
  const [transcripts, setTranscripts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedIds, setSelectedIds] = useState(new Set());

  const API = API_URL;

  useEffect(() => {
    loadTranscripts();
  }, []);

  const loadTranscripts = async () => {
    try {
      setLoading(true);
      const response = await api.get(`${API}/api/sales-ops/fireflies/transcripts`);
      if (response.ok) {
        const data = await response.json();
        setTranscripts(data.items || []);
      } else {
        // I8. Mirror the loadSOW pattern. When the upstream returns a
        // non JSON body, response.json throws and the outer catch hides
        // the real status. Parse the body inside its own try and fall
        // back to status plus statusText on parse failure.
        let err = null;
        try {
          err = await response.json();
        } catch (_parseError) {
          err = null;
        }
        if (err) {
          const { title, description } = describeFirefliesError(
            err,
            'Failed to load transcripts from Fireflies.ai',
          );
          toast.error(title, description ? { description } : undefined);
        } else {
          const statusText = `${response.status} ${response.statusText || ''}`.trim();
          toast.error(statusText || 'Unexpected error');
        }
      }
    } catch (error) {
      console.error('Error loading transcripts:', error);
      toast.error('Failed to connect to Fireflies.ai');
    } finally {
      setLoading(false);
    }
  };

  const toggleSelect = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const filteredTranscripts = transcripts.filter((t) => {
    if (!searchQuery) return true;
    const search = searchQuery.toLowerCase();
    return (
      t.title?.toLowerCase().includes(search) ||
      t.summary?.toLowerCase().includes(search) ||
      (t.participants || []).some((p) => p?.toLowerCase().includes(search))
    );
  });

  const formatDate = (timestamp) => {
    if (!timestamp) return '';
    const date = typeof timestamp === 'number' ? new Date(timestamp) : new Date(timestamp);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  };

  const formatDuration = (seconds) => {
    if (!seconds) return '';
    const mins = Math.floor(seconds / 60);
    if (mins < 60) return `${mins}m`;
    const hrs = Math.floor(mins / 60);
    return `${hrs}h ${mins % 60}m`;
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-[110] p-4">
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-3xl max-h-[80vh] flex flex-col">
        <div className="bg-gradient-to-r from-violet-600/20 to-purple-600/20 border-b border-aegisaxiom-border p-5">
          <div className="flex items-start justify-between">
            <div>
              <h3 className="text-lg font-bold text-aegisaxiom-text flex items-center gap-2">
                <Sparkles className="w-5 h-5 text-violet-600 dark:text-violet-400" />
                Select Meeting Transcripts
              </h3>
              <p className="text-sm text-aegisaxiom-text-secondary mt-1">
                Choose one or more Fireflies.ai transcripts to generate from
              </p>
            </div>
            <button
              onClick={onClose}
              className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
          <div className="mt-4 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-aegisaxiom-text-secondary" />
            <input
              type="text"
              placeholder="Search by title, participant, or summary..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 text-sm"
            />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-2">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-violet-500"></div>
            </div>
          ) : filteredTranscripts.length === 0 ? (
            <div className="text-center py-12">
              <FileText className="w-12 h-12 text-aegisaxiom-text-muted mx-auto mb-3" />
              <p className="text-aegisaxiom-text-secondary text-sm">
                {searchQuery
                  ? 'No transcripts match your search'
                  : 'No transcripts found in Fireflies.ai'}
              </p>
            </div>
          ) : (
            filteredTranscripts.map((t) => {
              const isSelected = selectedIds.has(t.id);
              return (
                <div
                  key={t.id}
                  onClick={() => toggleSelect(t.id)}
                  className={`p-4 rounded-lg border cursor-pointer transition ${
                    isSelected
                      ? 'bg-violet-50 dark:bg-violet-600/20 border-violet-500'
                      : 'bg-aegisaxiom-base border-aegisaxiom-border hover:border-aegisaxiom-border-hover'
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <div
                      className={`mt-0.5 w-5 h-5 rounded border-2 flex items-center justify-center flex-shrink-0 transition ${
                        isSelected ? 'bg-violet-600 border-violet-600' : 'border-aegisaxiom-border-hover'
                      }`}
                    >
                      {isSelected && <CheckCircle className="w-3.5 h-3.5 text-aegisaxiom-text" />}
                    </div>
                    <div className="flex-1 min-w-0">
                      <h4 className="font-medium text-aegisaxiom-text text-sm truncate">{t.title}</h4>
                      <div className="flex items-center gap-3 mt-1 text-xs text-aegisaxiom-text-secondary">
                        {t.date && (
                          <span className="flex items-center gap-1">
                            <Calendar className="w-3 h-3" />
                            {formatDate(t.date)}
                          </span>
                        )}
                        {t.duration && (
                          <span className="flex items-center gap-1">
                            <Clock className="w-3 h-3" />
                            {formatDuration(t.duration)}
                          </span>
                        )}
                        {t.participants?.length > 0 && (
                          <span className="flex items-center gap-1">
                            <Users className="w-3 h-3" />
                            {t.participants.length} participant
                            {t.participants.length !== 1 ? 's' : ''}
                          </span>
                        )}
                      </div>
                      {t.summary && (
                        <p className="text-xs text-aegisaxiom-text-muted mt-1.5 line-clamp-2">
                          {t.summary}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>

        <div className="border-t border-aegisaxiom-border p-4 flex items-center justify-between">
          <span className="text-sm text-aegisaxiom-text-secondary">
            {selectedIds.size} transcript{selectedIds.size !== 1 ? 's' : ''} selected
          </span>
          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm text-sm rounded-lg transition"
            >
              Cancel
            </button>
            <button
              onClick={() => {
                const selected = transcripts
                  .filter((t) => selectedIds.has(t.id))
                  .map((t) => ({ id: t.id, title: t.title }));
                onSelect(selected);
              }}
              disabled={selectedIds.size === 0}
              className="px-5 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            >
              <Sparkles className="w-4 h-4" />
              Select & Generate
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// Milestones Section
function MilestonesSection({
  sow,
  updateSOW,
  opportunity,
  lob,
  onAddMilestone,
  onUpdateMilestone,
  onDeleteMilestone,
  onAddBullet,
  onUpdateBullet,
  onDeleteBullet,
  onAddSubtask,
  onUpdateSubtask,
  onDeleteSubtask,
  selectedTranscripts,
  onTranscriptsSelected,
  onValidationResult,
}) {
  const [showTranscriptSelector, setShowTranscriptSelector] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generatingStatus, setGeneratingStatus] = useState('');

  // Phase (milestone) reorder — native HTML5 DnD + keyboard fallback.
  // Reuses the same pure helpers as the optional-prose-section reorder.
  const dragMilestoneIdRef = useRef(null);
  const [dragOverMilestoneId, setDragOverMilestoneId] = useState(null);

  const rewriteMilestoneOrder = (nextIds) => {
    const byId = Object.fromEntries(
      (sow.milestones || []).map((m) => [m.milestone_id, m]),
    );
    return nextIds.map((id, i) => ({ ...byId[id], order: i }));
  };

  const movePhaseToIndex = (milestoneId, toIndex) => {
    const ids = (sow.milestones || []).map((m) => m.milestone_id);
    const nextIds = sowReorderHelpers.moveItemToIndex(ids, milestoneId, toIndex);
    if (nextIds === ids) return;
    updateSOW('milestones', rewriteMilestoneOrder(nextIds));
  };

  const movePhaseByOne = (milestoneId, direction) => {
    const ids = (sow.milestones || []).map((m) => m.milestone_id);
    const nextIds = sowReorderHelpers.swapByOne(ids, milestoneId, direction);
    if (nextIds === ids) return;
    updateSOW('milestones', rewriteMilestoneOrder(nextIds));
  };

  const handleMilestoneDragStart = (milestoneId) => (event) => {
    dragMilestoneIdRef.current = milestoneId;
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      try {
        event.dataTransfer.setData('text/plain', milestoneId);
      } catch (_) {
        // some browsers reject custom data types — safe to ignore
      }
    }
  };

  const handleMilestoneDragOver = (milestoneId) => (event) => {
    if (!dragMilestoneIdRef.current) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    if (dragOverMilestoneId !== milestoneId) setDragOverMilestoneId(milestoneId);
  };

  const handleMilestoneDragLeave = (milestoneId) => () => {
    if (dragOverMilestoneId === milestoneId) setDragOverMilestoneId(null);
  };

  const handleMilestoneDragEnd = () => {
    dragMilestoneIdRef.current = null;
    setDragOverMilestoneId(null);
  };

  const handleMilestoneDrop = (targetId) => (event) => {
    event.preventDefault();
    const sourceId = dragMilestoneIdRef.current;
    dragMilestoneIdRef.current = null;
    setDragOverMilestoneId(null);
    if (!sourceId || sourceId === targetId) return;
    const ids = (sow.milestones || []).map((m) => m.milestone_id);
    const targetIdx = ids.indexOf(targetId);
    if (targetIdx === -1) return;
    movePhaseToIndex(sourceId, targetIdx);
  };

  const handleAIGenerate = () => {
    // Always open the transcript selector with validation flow,
    // even if transcripts were already selected in the SMART step.
    // The engineer must go through the guide validation checklist
    // before generating the detailed SOW.
    setShowTranscriptSelector(true);
  };

  const handleTranscriptsSelected = (selected) => {
    setShowTranscriptSelector(false);
    onTranscriptsSelected(selected);
    generateMilestones(selected.map((t) => t.id));
  };

  const generateMilestones = async (transcriptIds) => {
    setGenerating(true);
    setGeneratingStatus('Reading transcripts...');

    try {
      setGeneratingStatus('Generating milestones...');
      const response = await api.post(`${API}/api/sales-ops/fireflies/generate-milestones`, {
        transcript_ids: transcriptIds,
        opportunity_id: opportunity.opportunity_id,
        lob: lob,
      });

      if (response.ok) {
        const data = await response.json();

        // Replace milestones with AI-generated ones
        updateSOW('milestones', data.milestones);

        // Auto-populate expected completion date if suggested
        if (data.suggested_completion_date) {
          updateSOW('expected_completion_date', data.suggested_completion_date);
        }

        const totalTasks = data.milestones.reduce((sum, ms) => sum + (ms.bullets || []).length, 0);
        const totalHours = data.milestones.reduce(
          (sum, ms) => sum + (ms.bullets || []).reduce((s, b) => s + (b.estimated_hours || 0), 0),
          0,
        );

        toast.success(`Generated ${data.milestones.length} milestones with ${totalTasks} tasks`, {
          description: `${totalHours} total estimated hours`,
        });
      } else {
        const err = await response.json();
        toast.error(resolveErrorDetail(err));
      }
    } catch (error) {
      console.error('Error generating milestones:', error);
      toast.error('Failed to generate milestones');
    } finally {
      setGenerating(false);
      setGeneratingStatus('');
    }
  };

  // Calculate totals
  const totalMilestones = (sow.milestones || []).length;
  const totalHours = (sow.milestones || []).reduce(
    (sum, ms) => sum + (ms.bullets || []).reduce((s, b) => s + (b.estimated_hours || 0), 0),
    0,
  );

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h3 className="text-lg font-semibold text-aegisaxiom-text">Detailed Statement of Work</h3>
          {totalMilestones > 0 && (
            <p className="text-sm text-aegisaxiom-text-secondary mt-1">
              {totalMilestones} milestone{totalMilestones !== 1 ? 's' : ''} &middot; {totalHours}{' '}
              total hours
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleAIGenerate}
            disabled={generating}
            className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-violet-600 to-purple-600 hover:from-violet-500 hover:to-purple-500 text-aegisaxiom-text text-sm rounded-lg transition disabled:opacity-50"
          >
            <Sparkles className="w-4 h-4" />
            {generating ? 'Generating...' : 'AI Generate'}
          </button>
          <button
            onClick={onAddMilestone}
            className="flex items-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition"
          >
            <Plus className="w-4 h-4" />
            Add Milestone
          </button>
        </div>
      </div>

      {/* Selected Transcripts Banner */}
      {selectedTranscripts.length > 0 && (
        <SelectedTranscriptsBanner transcripts={selectedTranscripts} />
      )}

      {/* Generation Loading State */}
      {generating && (
        <div className="bg-violet-900/20 border border-violet-200 dark:border-violet-600/30 rounded-lg p-6 flex items-center justify-center gap-3">
          <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-violet-400"></div>
          <span className="text-violet-700 dark:text-violet-300 text-sm font-medium">
            {generatingStatus}
          </span>
        </div>
      )}

      {!generating && (sow.milestones || []).length === 0 ? (
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-12 text-center">
          <FileText className="w-16 h-16 text-aegisaxiom-text-muted mx-auto mb-4" />
          <h4 className="text-aegisaxiom-text font-semibold mb-2">No Milestones Yet</h4>
          <p className="text-aegisaxiom-text-secondary text-sm mb-4">
            Start building your SOW by adding milestones and tasks
          </p>
          <div className="flex gap-3 justify-center">
            <button
              onClick={handleAIGenerate}
              className="px-4 py-2 bg-gradient-to-r from-violet-600 to-purple-600 hover:from-violet-500 hover:to-purple-500 text-aegisaxiom-text text-sm rounded-lg transition flex items-center gap-2"
            >
              <Sparkles className="w-4 h-4" />
              Generate with AI
            </button>
            <button
              onClick={onAddMilestone}
              className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm text-sm rounded-lg transition"
            >
              Add Manually
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          {(sow.milestones || []).map((milestone, idx) => (
            <MilestoneCard
              key={milestone.milestone_id}
              milestone={milestone}
              index={idx}
              totalCount={(sow.milestones || []).length}
              onUpdate={onUpdateMilestone}
              onDelete={onDeleteMilestone}
              onAddBullet={onAddBullet}
              onUpdateBullet={onUpdateBullet}
              onDeleteBullet={onDeleteBullet}
              onAddSubtask={onAddSubtask}
              onUpdateSubtask={onUpdateSubtask}
              onDeleteSubtask={onDeleteSubtask}
              isDraggable
              isDragOver={dragOverMilestoneId === milestone.milestone_id}
              onMilestoneDragStart={handleMilestoneDragStart(milestone.milestone_id)}
              onMilestoneDragOver={handleMilestoneDragOver(milestone.milestone_id)}
              onMilestoneDragLeave={handleMilestoneDragLeave(milestone.milestone_id)}
              onMilestoneDragEnd={handleMilestoneDragEnd}
              onMilestoneDrop={handleMilestoneDrop(milestone.milestone_id)}
              onMilestoneMoveUp={() => movePhaseByOne(milestone.milestone_id, 'up')}
              onMilestoneMoveDown={() => movePhaseByOne(milestone.milestone_id, 'down')}
            />
          ))}
        </div>
      )}

      {/* Fireflies Transcript Selector Modal with validation flow */}
      {showTranscriptSelector && (
        <FirefliesTranscriptSelector
          onSelect={handleTranscriptsSelected}
          onClose={() => setShowTranscriptSelector(false)}
          onValidationComplete={onValidationResult}
        />
      )}
    </div>
  );
}

// Milestone Card Component
function MilestoneCard({
  milestone,
  index,
  totalCount,
  onUpdate,
  onDelete,
  onAddBullet,
  onUpdateBullet,
  onDeleteBullet,
  onAddSubtask,
  onUpdateSubtask,
  onDeleteSubtask,
  isDraggable,
  isDragOver,
  onMilestoneDragStart,
  onMilestoneDragOver,
  onMilestoneDragLeave,
  onMilestoneDragEnd,
  onMilestoneDrop,
  onMilestoneMoveUp,
  onMilestoneMoveDown,
}) {
  const [expanded, setExpanded] = useState(true);
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });

  // Bullet (task) reorder inside this milestone. Scoped to this card so
  // bullets cannot be dragged across milestones — cross-milestone moves
  // would require inventing additional semantics (e.g. which milestone
  // owns hours / deliverable) that the current data model does not
  // express. Within-milestone reorder simply rewrites `bullets[].order`.
  const dragBulletIdRef = useRef(null);
  const [dragOverBulletId, setDragOverBulletId] = useState(null);

  const rewriteBulletOrder = (nextIds) => {
    const byId = Object.fromEntries(
      (milestone.bullets || []).map((b) => [b.bullet_id, b]),
    );
    return nextIds.map((id, i) => ({ ...byId[id], order: i }));
  };

  const moveBulletToIndex = (bulletId, toIndex) => {
    const ids = (milestone.bullets || []).map((b) => b.bullet_id);
    const nextIds = sowReorderHelpers.moveItemToIndex(ids, bulletId, toIndex);
    if (nextIds === ids) return;
    onUpdate(milestone.milestone_id, 'bullets', rewriteBulletOrder(nextIds));
  };

  const moveBulletByOne = (bulletId, direction) => {
    const ids = (milestone.bullets || []).map((b) => b.bullet_id);
    const nextIds = sowReorderHelpers.swapByOne(ids, bulletId, direction);
    if (nextIds === ids) return;
    onUpdate(milestone.milestone_id, 'bullets', rewriteBulletOrder(nextIds));
  };

  const handleBulletDragStart = (bulletId) => (event) => {
    event.stopPropagation();
    dragBulletIdRef.current = bulletId;
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      try {
        event.dataTransfer.setData('text/plain', bulletId);
      } catch (_) {
        // ignore — some browsers reject custom data types
      }
    }
  };

  const handleBulletDragOver = (bulletId) => (event) => {
    if (!dragBulletIdRef.current) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    if (dragOverBulletId !== bulletId) setDragOverBulletId(bulletId);
  };

  const handleBulletDragLeave = (bulletId) => () => {
    if (dragOverBulletId === bulletId) setDragOverBulletId(null);
  };

  const handleBulletDragEnd = () => {
    dragBulletIdRef.current = null;
    setDragOverBulletId(null);
  };

  const handleBulletDrop = (targetId) => (event) => {
    const sourceId = dragBulletIdRef.current;
    if (!sourceId) {
      // No active bullet drag — let the event bubble to the milestone card
      // so a phase drop dragged onto a bullet still lands at the phase
      // boundary instead of being silently swallowed.
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dragBulletIdRef.current = null;
    setDragOverBulletId(null);
    if (sourceId === targetId) return;
    const ids = (milestone.bullets || []).map((b) => b.bullet_id);
    const targetIdx = ids.indexOf(targetId);
    if (targetIdx === -1) return;
    moveBulletToIndex(sourceId, targetIdx);
  };

  // Subtask reorder inside a parent bullet. Drag state is keyed by
  // bullet_id so two bullets cannot share a dragging subtask — a
  // subtask dragged from bullet A and released on bullet B is
  // ignored (crossParent=false in the registry). Persistence rewrites
  // only the parent bullet's subtasks[] array via onUpdate(milestone_id,
  // 'bullets', ...) — siblings are untouched.
  const dragSubtaskRef = useRef({ bulletId: null, subtaskId: null });
  const [dragOverSubtask, setDragOverSubtask] = useState({ bulletId: null, subtaskId: null });

  const rewriteSubtaskOrder = (bulletId, nextIds) => {
    const bullet = (milestone.bullets || []).find((b) => b.bullet_id === bulletId);
    if (!bullet) return null;
    const byId = Object.fromEntries(
      (bullet.subtasks || []).map((s) => [s.subtask_id, s]),
    );
    const reordered = nextIds.map((id, i) => ({ ...byId[id], order: i }));
    const nextBullets = (milestone.bullets || []).map((b) =>
      b.bullet_id === bulletId ? { ...b, subtasks: reordered } : b,
    );
    return nextBullets;
  };

  const moveSubtaskToIndex = (bulletId, subtaskId, toIndex) => {
    const bullet = (milestone.bullets || []).find((b) => b.bullet_id === bulletId);
    if (!bullet) return;
    const ids = (bullet.subtasks || []).map((s) => s.subtask_id);
    const nextIds = sowReorderHelpers.moveItemToIndex(ids, subtaskId, toIndex);
    if (nextIds === ids) return;
    const nextBullets = rewriteSubtaskOrder(bulletId, nextIds);
    if (nextBullets) onUpdate(milestone.milestone_id, 'bullets', nextBullets);
  };

  const moveSubtaskByOne = (bulletId, subtaskId, direction) => {
    const bullet = (milestone.bullets || []).find((b) => b.bullet_id === bulletId);
    if (!bullet) return;
    const ids = (bullet.subtasks || []).map((s) => s.subtask_id);
    const nextIds = sowReorderHelpers.swapByOne(ids, subtaskId, direction);
    if (nextIds === ids) return;
    const nextBullets = rewriteSubtaskOrder(bulletId, nextIds);
    if (nextBullets) onUpdate(milestone.milestone_id, 'bullets', nextBullets);
  };

  const handleSubtaskDragStart = (bulletId, subtaskId) => (event) => {
    event.stopPropagation();
    dragSubtaskRef.current = { bulletId, subtaskId };
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      try {
        event.dataTransfer.setData('text/plain', subtaskId);
      } catch (_) {
        // ignore — some browsers reject custom data types
      }
    }
  };

  const handleSubtaskDragOver = (bulletId, subtaskId) => (event) => {
    const dragging = dragSubtaskRef.current;
    if (!dragging.subtaskId) return;
    // crossParent=false — only allow drag-over highlight inside the
    // same parent bullet. A drag attempted across bullets is ignored.
    if (dragging.bulletId !== bulletId) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    if (dragOverSubtask.subtaskId !== subtaskId || dragOverSubtask.bulletId !== bulletId) {
      setDragOverSubtask({ bulletId, subtaskId });
    }
  };

  const handleSubtaskDragLeave = (bulletId, subtaskId) => () => {
    if (
      dragOverSubtask.bulletId === bulletId
      && dragOverSubtask.subtaskId === subtaskId
    ) {
      setDragOverSubtask({ bulletId: null, subtaskId: null });
    }
  };

  const handleSubtaskDragEnd = () => {
    dragSubtaskRef.current = { bulletId: null, subtaskId: null };
    setDragOverSubtask({ bulletId: null, subtaskId: null });
  };

  const handleSubtaskDrop = (bulletId, targetSubtaskId) => (event) => {
    const { bulletId: sourceBulletId, subtaskId: sourceSubtaskId } = dragSubtaskRef.current;
    if (!sourceSubtaskId) return;
    // crossParent=false — silently refuse cross-bullet drops.
    if (sourceBulletId !== bulletId) {
      dragSubtaskRef.current = { bulletId: null, subtaskId: null };
      setDragOverSubtask({ bulletId: null, subtaskId: null });
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dragSubtaskRef.current = { bulletId: null, subtaskId: null };
    setDragOverSubtask({ bulletId: null, subtaskId: null });
    if (sourceSubtaskId === targetSubtaskId) return;
    const bullet = (milestone.bullets || []).find((b) => b.bullet_id === bulletId);
    if (!bullet) return;
    const ids = (bullet.subtasks || []).map((s) => s.subtask_id);
    const targetIdx = ids.indexOf(targetSubtaskId);
    if (targetIdx === -1) return;
    moveSubtaskToIndex(bulletId, sourceSubtaskId, targetIdx);
  };

  const totalHours = (milestone.bullets || []).reduce(
    (sum, b) => sum + (b.estimated_hours || 0),
    0,
  );

  const canMovePhaseUp = typeof index === 'number' && index > 0;
  const canMovePhaseDown =
    typeof index === 'number' && typeof totalCount === 'number' && index < totalCount - 1;

  // Registry guards. The milestone block model supports reorder
  // (within the parent SOW) and removal but does NOT support
  // deterministic per-block regenerate today — that gap is documented
  // on row 5 of the master tickboard. The guards layer over the
  // caller's opt-ins so a buggy parent cannot expose buttons that
  // contradict the block taxonomy.
  const milestoneCanReorder = isDraggable && registryIsReorderable(BLOCK_TYPES.MILESTONE);
  const milestoneCanRemove = registryIsRemovable(BLOCK_TYPES.MILESTONE);

  return (
    <BlockShell
      blockType={BLOCK_TYPES.MILESTONE}
      blockId={milestone.milestone_id}
      testId={`block-milestone-${milestone.milestone_id}`}
      isDragOver={isDragOver}
      onDragOver={milestoneCanReorder ? onMilestoneDragOver : undefined}
      onDragLeave={milestoneCanReorder ? onMilestoneDragLeave : undefined}
      onDrop={milestoneCanReorder ? onMilestoneDrop : undefined}
    >
      {/* Milestone Header */}
      <div className="bg-aegisaxiom-elevated p-4">
        <div className="flex items-start gap-3">
          {milestoneCanReorder && (
            <button
              type="button"
              draggable
              onDragStart={onMilestoneDragStart}
              onDragEnd={onMilestoneDragEnd}
              aria-label="Drag handle — reorder this milestone"
              title="Drag to reorder milestone"
              data-testid={`milestone-drag-handle-${milestone.milestone_id}`}
              className="mt-2 p-1 text-aegisaxiom-text-muted hover:text-aegisaxiom-text cursor-grab active:cursor-grabbing focus:outline-none focus:ring-2 focus:ring-violet-500 rounded"
            >
              <GripVertical className="w-4 h-4" />
            </button>
          )}
          <div className="flex-1">
            <input
              type="text"
              value={milestone.milestone_name}
              onChange={(e) => onUpdate(milestone.milestone_id, 'milestone_name', e.target.value)}
              placeholder={`Milestone ${index + 1} Name...`}
              className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500"
            />
            <div className="flex items-center gap-4 mt-2 text-xs text-aegisaxiom-text-secondary">
              <span>{(milestone.bullets || []).length} tasks</span>
              <span>Est. {totalHours.toFixed(1)} hours</span>
            </div>
          </div>
          <div className="flex gap-2 items-start">
            {onMilestoneMoveUp && (
              <button
                type="button"
                onClick={onMilestoneMoveUp}
                disabled={!canMovePhaseUp}
                aria-label="Move milestone up"
                title="Move milestone up"
                className="p-2 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition disabled:opacity-30 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-violet-500 rounded"
              >
                <ArrowUp className="w-4 h-4" />
              </button>
            )}
            {onMilestoneMoveDown && (
              <button
                type="button"
                onClick={onMilestoneMoveDown}
                disabled={!canMovePhaseDown}
                aria-label="Move milestone down"
                title="Move milestone down"
                className="p-2 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition disabled:opacity-30 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-violet-500 rounded"
              >
                <ArrowDown className="w-4 h-4" />
              </button>
            )}
            <button
              onClick={() => setExpanded(!expanded)}
              className="p-2 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
            >
              {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </button>
            <button
              onClick={() =>
                setConfirmState({
                  open: true,
                  title: 'Delete Milestone',
                  message: 'Delete this milestone and all its tasks? This action cannot be undone.',
                  action: () => onDelete(milestone.milestone_id),
                })
              }
              className="p-2 text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300 transition"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Milestone Bullets */}
      {expanded && (
        <div className="p-4 space-y-3">
          {(milestone.bullets || []).map((bullet, bulletIdx) => {
            const bulletCount = (milestone.bullets || []).length;
            const canMoveTaskUp = bulletIdx > 0;
            const canMoveTaskDown = bulletIdx < bulletCount - 1;
            const isBulletDragOver = dragOverBulletId === bullet.bullet_id;
            // Bullet block is a drop target (the outer card) but its
            // drag SOURCE is the GripVertical button below. The shell
            // wires only the drop-target handlers; the grip handle
            // stays draggable=true and owns onDragStart / onDragEnd.
            return (
            <BlockShell
              key={bullet.bullet_id}
              variant="surface"
              blockType={BLOCK_TYPES.BULLET}
              blockId={bullet.bullet_id}
              testId={`block-bullet-${bullet.bullet_id}`}
              className="p-3"
              isDragOver={isBulletDragOver}
              onDragOver={handleBulletDragOver(bullet.bullet_id)}
              onDragLeave={handleBulletDragLeave(bullet.bullet_id)}
              onDrop={handleBulletDrop(bullet.bullet_id)}
            >
              <div className="flex items-start gap-3 mb-3">
                <button
                  type="button"
                  draggable
                  onDragStart={handleBulletDragStart(bullet.bullet_id)}
                  onDragEnd={handleBulletDragEnd}
                  aria-label="Drag handle — reorder this task within milestone"
                  title="Drag to reorder task"
                  data-testid={`bullet-drag-handle-${bullet.bullet_id}`}
                  className="mt-1 p-1 text-aegisaxiom-text-muted hover:text-aegisaxiom-text cursor-grab active:cursor-grabbing focus:outline-none focus:ring-2 focus:ring-violet-500 rounded flex-shrink-0"
                >
                  <GripVertical className="w-4 h-4" />
                </button>
                <div className="flex-shrink-0 w-6 h-6 bg-violet-50 dark:bg-violet-600/20 rounded-full flex items-center justify-center text-violet-600 dark:text-violet-400 text-xs font-semibold mt-1">
                  {bulletIdx + 1}
                </div>
                <div className="flex-1">
                  <textarea
                    value={bullet.bullet_text}
                    onChange={(e) =>
                      onUpdateBullet(
                        milestone.milestone_id,
                        bullet.bullet_id,
                        'bullet_text',
                        e.target.value,
                      )
                    }
                    placeholder="Task description..."
                    rows={2}
                    className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 resize-none text-sm"
                  />
                </div>
                <div className="flex flex-col gap-1 items-center mt-1">
                  <button
                    type="button"
                    onClick={() => moveBulletByOne(bullet.bullet_id, 'up')}
                    disabled={!canMoveTaskUp}
                    aria-label="Move task up"
                    title="Move task up"
                    className="p-1 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition disabled:opacity-30 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-violet-500 rounded"
                  >
                    <ArrowUp className="w-3 h-3" />
                  </button>
                  <button
                    type="button"
                    onClick={() => moveBulletByOne(bullet.bullet_id, 'down')}
                    disabled={!canMoveTaskDown}
                    aria-label="Move task down"
                    title="Move task down"
                    className="p-1 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition disabled:opacity-30 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-violet-500 rounded"
                  >
                    <ArrowDown className="w-3 h-3" />
                  </button>
                </div>
                <button
                  onClick={() => onDeleteBullet(milestone.milestone_id, bullet.bullet_id)}
                  className="text-aegisaxiom-text-secondary hover:text-red-600 dark:text-red-400 transition mt-1"
                  aria-label="Delete task"
                  title="Delete task"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="grid grid-cols-2 gap-3 ml-9">
                <div>
                  <label className="block text-xs text-aegisaxiom-text-secondary mb-1">
                    Estimated Hours
                  </label>
                  <input
                    type="number"
                    value={bullet.estimated_hours || ''}
                    onChange={(e) =>
                      onUpdateBullet(
                        milestone.milestone_id,
                        bullet.bullet_id,
                        'estimated_hours',
                        parseFloat(e.target.value) || null,
                      )
                    }
                    placeholder="0"
                    step="0.5"
                    className="w-full px-3 py-1.5 bg-aegisaxiom-base border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                  />
                </div>
                <div>
                  <label className="block text-xs text-aegisaxiom-text-secondary mb-1">Deliverable</label>
                  <input
                    type="text"
                    value={bullet.deliverable || ''}
                    onChange={(e) =>
                      onUpdateBullet(
                        milestone.milestone_id,
                        bullet.bullet_id,
                        'deliverable',
                        e.target.value,
                      )
                    }
                    placeholder="What client receives..."
                    className="w-full px-3 py-1.5 bg-aegisaxiom-base border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                  />
                </div>
              </div>

              {/* Subtasks — optional third level. Renders only when
                  bullet.subtasks is non-empty; the Add subtask button
                  lazily creates the array on first use so existing
                  bullets without subtasks stay unchanged on disk. */}
              <div className="mt-3 ml-9 space-y-2">
                {((bullet.subtasks || []).length > 0) && (
                  <div className="text-[10px] uppercase tracking-wide text-aegisaxiom-text-muted">
                    Subtasks
                  </div>
                )}
                {(bullet.subtasks || []).map((subtask, subtaskIdx) => {
                  const subtaskCount = (bullet.subtasks || []).length;
                  const canMoveSubtaskUp = subtaskIdx > 0;
                  const canMoveSubtaskDown = subtaskIdx < subtaskCount - 1;
                  const isSubtaskDragOver =
                    dragOverSubtask.bulletId === bullet.bullet_id
                    && dragOverSubtask.subtaskId === subtask.subtask_id;
                  return (
                    <BlockShell
                      key={subtask.subtask_id}
                      variant="surface"
                      blockType={BLOCK_TYPES.SUBTASK}
                      blockId={subtask.subtask_id}
                      testId={`block-subtask-${subtask.subtask_id}`}
                      className="p-2"
                      isDragOver={isSubtaskDragOver}
                      onDragOver={handleSubtaskDragOver(bullet.bullet_id, subtask.subtask_id)}
                      onDragLeave={handleSubtaskDragLeave(bullet.bullet_id, subtask.subtask_id)}
                      onDrop={handleSubtaskDrop(bullet.bullet_id, subtask.subtask_id)}
                    >
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          draggable
                          onDragStart={handleSubtaskDragStart(bullet.bullet_id, subtask.subtask_id)}
                          onDragEnd={handleSubtaskDragEnd}
                          aria-label="Drag handle — reorder this subtask within task"
                          title="Drag to reorder subtask"
                          data-testid={`subtask-drag-handle-${subtask.subtask_id}`}
                          className="p-1 text-aegisaxiom-text-muted hover:text-aegisaxiom-text cursor-grab active:cursor-grabbing focus:outline-none focus:ring-2 focus:ring-violet-500 rounded flex-shrink-0"
                        >
                          <GripVertical className="w-3.5 h-3.5" />
                        </button>
                        <span className="text-[10px] text-aegisaxiom-text-muted w-5 flex-shrink-0">
                          {subtaskIdx + 1}.
                        </span>
                        <input
                          type="text"
                          value={subtask.description || ''}
                          onChange={(e) =>
                            onUpdateSubtask(
                              milestone.milestone_id,
                              bullet.bullet_id,
                              subtask.subtask_id,
                              'description',
                              e.target.value,
                            )
                          }
                          placeholder="Subtask description..."
                          className="flex-1 px-2 py-1 bg-aegisaxiom-base border border-aegisaxiom-border rounded text-aegisaxiom-text placeholder-aegisaxiom-text-muted text-xs focus:outline-none focus:border-violet-500"
                        />
                        <div className="flex flex-col gap-0.5 items-center flex-shrink-0">
                          <button
                            type="button"
                            onClick={() => moveSubtaskByOne(bullet.bullet_id, subtask.subtask_id, 'up')}
                            disabled={!canMoveSubtaskUp}
                            aria-label="Move subtask up"
                            title="Move subtask up"
                            className="p-0.5 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition disabled:opacity-30 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-violet-500 rounded"
                          >
                            <ArrowUp className="w-3 h-3" />
                          </button>
                          <button
                            type="button"
                            onClick={() => moveSubtaskByOne(bullet.bullet_id, subtask.subtask_id, 'down')}
                            disabled={!canMoveSubtaskDown}
                            aria-label="Move subtask down"
                            title="Move subtask down"
                            className="p-0.5 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition disabled:opacity-30 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-violet-500 rounded"
                          >
                            <ArrowDown className="w-3 h-3" />
                          </button>
                        </div>
                        <button
                          type="button"
                          onClick={() =>
                            onDeleteSubtask(
                              milestone.milestone_id,
                              bullet.bullet_id,
                              subtask.subtask_id,
                            )
                          }
                          className="p-0.5 text-aegisaxiom-text-secondary hover:text-red-600 dark:hover:text-red-400 transition flex-shrink-0"
                          aria-label="Delete subtask"
                          title="Delete subtask"
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </BlockShell>
                  );
                })}
                {onAddSubtask && (
                  <button
                    type="button"
                    onClick={() => onAddSubtask(milestone.milestone_id, bullet.bullet_id)}
                    className="w-full py-1.5 border border-dashed border-aegisaxiom-border-hover rounded text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:border-violet-500 transition text-[11px] flex items-center justify-center gap-1.5"
                    data-testid={`add-subtask-${bullet.bullet_id}`}
                  >
                    <Plus className="w-3 h-3" />
                    Add subtask
                  </button>
                )}
              </div>
            </BlockShell>
            );
          })}

          {/* Add Bullet Button */}
          <button
            onClick={() => onAddBullet(milestone.milestone_id)}
            className="w-full py-2 border border-dashed border-aegisaxiom-border-hover rounded-lg text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:border-violet-500 transition text-sm flex items-center justify-center gap-2"
          >
            <Plus className="w-4 h-4" />
            Add Task
          </button>
        </div>
      )}
      <ConfirmModal
        isOpen={confirmState.open}
        title={confirmState.title}
        message={confirmState.message}
        confirmText="Delete"
        onConfirm={() => confirmState.action?.()}
        onCancel={() => setConfirmState({ open: false, action: null, title: '', message: '' })}
      />
    </BlockShell>
  );
}

// Billing Details Section
// Required contact roles for Field Services dispatch readiness.
// Mirrors FS_REQUIRED_CONTACT_ROLES in backend/routes/solutions_design.py.
// Each required role must have a name plus an email or phone before
// the SOW can transition to ready_for_review.
const FS_REQUIRED_CONTACT_ROLES = [
  { key: 'primary', label: 'Primary Contact' },
  { key: 'billing', label: 'Billing Contact' },
  { key: 'service_location', label: 'On-Site / Service Location Contact' },
];

// Field Services dispatch details panel. Captures service location
// and required contact roles directly on the shared SOW. Used only
// for field_services LOB. Styling mirrors the BillingDetailsSection
// card pattern (bg-aegisaxiom-base + border-aegisaxiom-border).
function FieldServicesDispatchSection({ sow, updateSOW }) {
  const location = sow.service_location || {};
  const rolesByKey = {};
  for (const entry of sow.contact_roles || []) {
    if (entry && typeof entry.role === 'string') {
      rolesByKey[entry.role] = entry;
    }
  }

  const updateLocationField = (field, value) => {
    const next = { ...location, [field]: value };
    updateSOW('service_location', next);
  };

  const updateRoleField = (roleKey, field, value) => {
    const current = rolesByKey[roleKey] || { role: roleKey };
    const nextEntry = { ...current, role: roleKey, [field]: value };
    const remaining = (sow.contact_roles || []).filter(
      (r) => !(r && r.role === roleKey),
    );
    updateSOW('contact_roles', [...remaining, nextEntry]);
  };

  const locationReady = !!(
    (location.address || '').trim() && (location.city || '').trim()
  );

  return (
    <>
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg overflow-hidden">
        <div className="bg-aegisaxiom-elevated px-4 py-3 border-b border-aegisaxiom-border flex items-center justify-between">
          <h4 className="font-semibold text-aegisaxiom-text text-sm">
            Service / Site Location
          </h4>
          {!locationReady && (
            <span className="text-xs text-amber-600 dark:text-amber-400">
              Address and city are required before validation.
            </span>
          )}
        </div>
        <div className="divide-y divide-aegisaxiom-border">
          <div className="grid grid-cols-2">
            <div className="px-4 py-3 bg-aegisaxiom-surface border-r border-aegisaxiom-border">
              <span className="text-aegisaxiom-text-secondary text-sm font-medium">
                Address
              </span>
            </div>
            <div className="px-4 py-3">
              <input
                type="text"
                value={location.address || ''}
                onChange={(e) => updateLocationField('address', e.target.value)}
                placeholder="123 Main St"
                className="w-full bg-transparent text-aegisaxiom-text text-sm focus:outline-none"
                data-testid="fs-service-location-address"
              />
            </div>
          </div>
          <div className="grid grid-cols-2">
            <div className="px-4 py-3 bg-aegisaxiom-surface border-r border-aegisaxiom-border">
              <span className="text-aegisaxiom-text-secondary text-sm font-medium">City</span>
            </div>
            <div className="px-4 py-3">
              <input
                type="text"
                value={location.city || ''}
                onChange={(e) => updateLocationField('city', e.target.value)}
                placeholder=""
                className="w-full bg-transparent text-aegisaxiom-text text-sm focus:outline-none"
                data-testid="fs-service-location-city"
              />
            </div>
          </div>
          <div className="grid grid-cols-2">
            <div className="px-4 py-3 bg-aegisaxiom-surface border-r border-aegisaxiom-border">
              <span className="text-aegisaxiom-text-secondary text-sm font-medium">
                State / Region
              </span>
            </div>
            <div className="px-4 py-3">
              <input
                type="text"
                value={location.state || ''}
                onChange={(e) => updateLocationField('state', e.target.value)}
                className="w-full bg-transparent text-aegisaxiom-text text-sm focus:outline-none"
              />
            </div>
          </div>
          <div className="grid grid-cols-2">
            <div className="px-4 py-3 bg-aegisaxiom-surface border-r border-aegisaxiom-border">
              <span className="text-aegisaxiom-text-secondary text-sm font-medium">
                Postal Code
              </span>
            </div>
            <div className="px-4 py-3">
              <input
                type="text"
                value={location.postal_code || ''}
                onChange={(e) => updateLocationField('postal_code', e.target.value)}
                className="w-full bg-transparent text-aegisaxiom-text text-sm focus:outline-none"
              />
            </div>
          </div>
          <div className="grid grid-cols-2">
            <div className="px-4 py-3 bg-aegisaxiom-surface border-r border-aegisaxiom-border">
              <span className="text-aegisaxiom-text-secondary text-sm font-medium">
                Site Notes
              </span>
            </div>
            <div className="px-4 py-3">
              <input
                type="text"
                value={location.notes || ''}
                onChange={(e) => updateLocationField('notes', e.target.value)}
                placeholder="Gate code, dock hours, parking, etc."
                className="w-full bg-transparent text-aegisaxiom-text text-sm focus:outline-none"
              />
            </div>
          </div>
        </div>
      </div>

      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg overflow-hidden">
        <div className="bg-aegisaxiom-elevated px-4 py-3 border-b border-aegisaxiom-border">
          <h4 className="font-semibold text-aegisaxiom-text text-sm">
            Required Contact Roles
          </h4>
          <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">
            Each role needs a name plus an email or a phone before validation.
          </p>
        </div>
        <div className="divide-y divide-aegisaxiom-border">
          {FS_REQUIRED_CONTACT_ROLES.map(({ key, label }) => {
            const entry = rolesByKey[key] || {};
            const name = (entry.name || '').trim();
            const email = (entry.email || '').trim();
            const phone = (entry.phone || '').trim();
            const ready = !!(name && (email || phone));
            return (
              <div key={key} className="px-4 py-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-aegisaxiom-text text-sm font-medium">
                    {label}
                  </span>
                  {!ready && (
                    <span className="text-xs text-amber-600 dark:text-amber-400">
                      Required for validation.
                    </span>
                  )}
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <input
                    type="text"
                    value={entry.name || ''}
                    onChange={(e) => updateRoleField(key, 'name', e.target.value)}
                    placeholder="Name"
                    className="px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                    data-testid={`fs-contact-role-name-${key}`}
                  />
                  <input
                    type="email"
                    value={entry.email || ''}
                    onChange={(e) => updateRoleField(key, 'email', e.target.value)}
                    placeholder="Email"
                    className="px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                  />
                  <input
                    type="tel"
                    value={entry.phone || ''}
                    onChange={(e) => updateRoleField(key, 'phone', e.target.value)}
                    placeholder="Phone"
                    className="px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}

// Required Professional Services prose sections that come from the
// editor (not computed from structured SOW fields). The full seven
// canonical PS sections also include Executive Summary, SMART
// Objective, Scope of Work, and Estimated Time, which render from
// the existing executive_summary / smart_* / milestones structured
// fields and from computed totals. This list is the prose subset.
// Mirrors PS_REQUIRED_SECTION_KINDS in
// backend/services/proposal_generation.py.
const PS_REQUIRED_PROSE_SECTIONS = [
  {
    kind: 'assumptions',
    label: 'Assumptions',
    placeholder: 'List the assumptions the proposal depends on.',
  },
  {
    kind: 'responsibilities',
    label: 'Responsibilities',
    placeholder:
      'Describe what the client owns and what the delivery team owns.',
  },
  {
    kind: 'out_of_scope',
    label: 'Out of Scope',
    placeholder:
      'List what this engagement explicitly does not include.',
  },
];

// Optional PS prose sections. Sales Ops can leave any of these
// blank; the proposal generator skips an empty optional section
// silently. Mirrors PS_OPTIONAL_SECTION_KINDS in
// backend/services/proposal_generation.py.
const PS_OPTIONAL_PROSE_SECTIONS = [
  { kind: 'risk_assessment', label: 'Risk Assessment' },
  { kind: 'solution_overview', label: 'Solution Overview' },
  { kind: 'in_scope_inventory', label: 'In-Scope Inventory' },
  { kind: 'conclusion', label: 'Conclusion' },
];

// Professional Services prose sections panel. Renders the required
// prose sections (Assumptions, Responsibilities, Out of Scope) and
// the optional sections (Risk Assessment, Solution Overview,
// In-Scope Inventory, Conclusion) as RichProseSection blocks with
// markdown toolbar + sanitized live preview, plus block-level
// controls: move up/down (optional sections only), remove (clears
// content for optional sections), and Regenerate (deterministic
// candidate composed from this SOW's context). Storage stays as
// markdown on SOWProseSection.content — no migration needed; the
// proposal pipeline and Google Doc export already consume strings.
function ProfessionalServicesProseSections({ sow, updateSOW, opportunity }) {
  const proseByKind = {};
  for (const entry of sow.prose_sections || []) {
    if (entry && typeof entry.kind === 'string') {
      proseByKind[entry.kind] = entry;
    }
  }

  const [regenKind, setRegenKind] = useState(null);
  const [regenLabel, setRegenLabel] = useState('');
  const [regenCandidate, setRegenCandidate] = useState('');
  const [regenCitations, setRegenCitations] = useState([]);
  const [regenGenerating, setRegenGenerating] = useState(false);

  const sowId = sow?.sow_id;
  const opportunityId = sow?.opportunity_id || opportunity?.opportunity_id;
  const canRegenerate = Boolean(sowId && opportunityId);

  const updateProseField = (kind, content, removable, extras = {}) => {
    const remaining = (sow.prose_sections || []).filter(
      (e) => !(e && e.kind === kind),
    );
    const existing = proseByKind[kind] || {};
    const nextEntry = {
      ...existing,
      kind,
      content,
      removable,
      ...extras,
    };
    updateSOW('prose_sections', [...remaining, nextEntry]);
  };

  // Optional-section ordering. We honor the order field on each
  // entry when present so reorder persists across save/reload; if
  // absent we fall back to the canonical PS_OPTIONAL_PROSE_SECTIONS
  // order. The required sections render in their canonical
  // PS_REQUIRED_PROSE_SECTIONS order and do not expose reorder
  // controls — required ordering is part of the proposal contract.
  const orderedOptionalKinds = (() => {
    const known = PS_OPTIONAL_PROSE_SECTIONS.map((s) => s.kind);
    const withOrder = known.map((kind, fallbackIdx) => {
      const entry = proseByKind[kind];
      const orderValue =
        entry && typeof entry.order === 'number' ? entry.order : null;
      return { kind, order: orderValue, fallbackIdx };
    });
    withOrder.sort((a, b) => {
      if (a.order !== null && b.order !== null) return a.order - b.order;
      if (a.order !== null) return -1;
      if (b.order !== null) return 1;
      return a.fallbackIdx - b.fallbackIdx;
    });
    return withOrder.map((s) => s.kind);
  })();

  // Pure reorder helpers live in ./sow-builder/reorderHelpers so the
  // swap-by-one (keyboard) path and the new HTML5 drag/drop path
  // share one implementation. Callers below convert the resulting
  // kind-order into the persisted prose_sections shape.
  const persistOptionalOrder = (nextOrder) => {
    if (nextOrder === orderedOptionalKinds) return; // no-op
    // Re-stamp every optional section with its new index. The
    // proposal pipeline does not currently sort optional sections by
    // order (it iterates PS_OPTIONAL_SECTION_KINDS), but persisting
    // order here means the UI reorder survives save/reload and is
    // ready for the renderer to honor when that ships.
    const otherSections = (sow.prose_sections || []).filter(
      (e) =>
        !(
          e &&
          typeof e.kind === 'string' &&
          nextOrder.includes(e.kind)
        ),
    );
    const reorderedSections = nextOrder.map((k, i) => {
      const existing = proseByKind[k] || {};
      return {
        ...existing,
        kind: k,
        content: existing.content || '',
        removable: true,
        order: i,
      };
    });
    updateSOW('prose_sections', [...otherSections, ...reorderedSections]);
  };

  const swapOptionalOrder = (kind, direction) => {
    const nextOrder = sowReorderHelpers.swapByOne(
      orderedOptionalKinds, kind, direction,
    );
    persistOptionalOrder(nextOrder);
  };

  const moveOptionalSectionTo = (kind, toIndex) => {
    const nextOrder = sowReorderHelpers.moveItemToIndex(
      orderedOptionalKinds, kind, toIndex,
    );
    persistOptionalOrder(nextOrder);
  };

  // HTML5 drag/drop state. The dragged kind is held in a ref so a
  // re-render does not lose it mid-drag. ``dragOverKind`` drives the
  // visual highlight on the hovered target. Both clear on dragend
  // and drop so an aborted drag does not leave the UI in a stuck
  // highlighted state.
  const dragKindRef = useRef(null);
  const [dragOverKind, setDragOverKind] = useState(null);

  const handleOptionalDragStart = (kind) => (event) => {
    dragKindRef.current = kind;
    // Setting dataTransfer is required by some browsers (Firefox) to
    // actually start the drag. The payload is the kind so a future
    // multi-list drop target can read it.
    try { event.dataTransfer.setData('text/plain', kind); } catch (_e) { /* ignore */ }
    try { event.dataTransfer.effectAllowed = 'move'; } catch (_e) { /* ignore */ }
  };

  const handleOptionalDragOver = (kind) => (event) => {
    event.preventDefault(); // permits the drop
    try { event.dataTransfer.dropEffect = 'move'; } catch (_e) { /* ignore */ }
    if (dragOverKind !== kind) setDragOverKind(kind);
  };

  const handleOptionalDragLeave = (kind) => () => {
    if (dragOverKind === kind) setDragOverKind(null);
  };

  const handleOptionalDragEnd = () => {
    dragKindRef.current = null;
    setDragOverKind(null);
  };

  const handleOptionalDrop = (targetKind) => (event) => {
    event.preventDefault();
    const sourceKind = dragKindRef.current;
    dragKindRef.current = null;
    setDragOverKind(null);
    if (!sourceKind || sourceKind === targetKind) return;
    const targetIdx = orderedOptionalKinds.indexOf(targetKind);
    if (targetIdx === -1) return;
    moveOptionalSectionTo(sourceKind, targetIdx);
  };

  const removeOptionalSection = (kind) => {
    // Optional sections are "removed" by clearing the content; the
    // proposal renderer silently skips empty optional sections per
    // backend/services/proposal_generation.py. The entry is kept so
    // its order persists across save/reload — re-add is just typing
    // back into the same card with the same slot index.
    const existing = proseByKind[kind] || {};
    updateProseField(kind, '', true, {
      order: typeof existing.order === 'number' ? existing.order : null,
      source_citations: null,
    });
  };

  const openRegenerate = (kind, label) => {
    setRegenKind(kind);
    setRegenLabel(label);
    setRegenCandidate('');
    setRegenCitations([]);
  };

  const closeRegenerate = () => {
    setRegenKind(null);
    setRegenLabel('');
    setRegenCandidate('');
    setRegenCitations([]);
    setRegenGenerating(false);
  };

  const requestRegenerate = async (instruction) => {
    if (!canRegenerate || !regenKind) return;
    // Capture the label at call time so a slow request that races a
    // close+reopen on a different section still reports the failing
    // block by its actual label.
    const failureLabel = regenLabel || regenKind;
    setRegenGenerating(true);
    try {
      const res = await api.post(
        `${API}/api/sales-ops/opportunities/${opportunityId}/sows/${sowId}/sections/${regenKind}/regenerate`,
        { user_instruction: instruction || '', mode: 'preview' },
      );
      if (res.ok) {
        const data = await res.json();
        setRegenCandidate(data.candidate_content || '');
        setRegenCitations(data.source_citations || []);
      } else {
        const err = await res.json().catch(() => ({}));
        const detail = err?.detail?.message || 'Regeneration failed';
        toast.error(`${detail} (“${failureLabel}”)`);
      }
    } catch (e) {
      console.error('Regenerate failed:', e);
      toast.error(`Regeneration failed (“${failureLabel}”)`);
    } finally {
      setRegenGenerating(false);
    }
  };

  const applyRegenerate = async () => {
    if (!canRegenerate || !regenKind || !regenCandidate) return;
    // Replace goes through the route in mode=replace so the backend
    // bumps the SOW version, writes the audit row
    // (sales_ops.sow.section_regenerated), and the change lands in
    // Mongo immediately rather than waiting on the next SOW Save.
    // Any other unsaved local edits the user has on the form remain
    // in local state; the eventual Save SOW reconciles them.
    const failureLabel = regenLabel || regenKind;
    setRegenGenerating(true);
    try {
      const res = await api.post(
        `${API}/api/sales-ops/opportunities/${opportunityId}/sows/${sowId}/sections/${regenKind}/regenerate`,
        { user_instruction: '', mode: 'replace' },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        const detail = err?.detail?.message || 'Replace failed';
        toast.error(`${detail} (“${failureLabel}”)`);
        return;
      }
      const data = await res.json();
      // Mirror the route's persisted shape back into local state so
      // the editor reflects what is now on disk. The candidate the
      // user just previewed is what was written.
      const isRequired = PS_REQUIRED_PROSE_SECTIONS.some((s) => s.kind === regenKind);
      updateProseField(regenKind, data.candidate_content || regenCandidate, !isRequired, {
        source_citations: data.source_citations && data.source_citations.length
          ? data.source_citations
          : null,
      });
      toast.success(`Replaced “${regenLabel}”`);
      closeRegenerate();
    } catch (e) {
      console.error('Replace section failed:', e);
      toast.error(`Replace failed (“${failureLabel}”)`);
    } finally {
      setRegenGenerating(false);
    }
  };

  return (
    <>
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg overflow-hidden">
        <div className="bg-aegisaxiom-elevated px-4 py-3 border-b border-aegisaxiom-border">
          <h4 className="font-semibold text-aegisaxiom-text text-sm">
            Required Proposal Sections
          </h4>
          <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">
            These appear in every Professional Services proposal alongside Executive Summary, SMART Objective, Scope of Work, and Estimated Time. Markdown is supported; preview to see the rendered output.
          </p>
        </div>
        <div className="p-4 space-y-3">
          {PS_REQUIRED_PROSE_SECTIONS.map(({ kind, label, placeholder }) => {
            const entry = proseByKind[kind] || {};
            return (
              <RichProseSection
                key={kind}
                kind={kind}
                label={label}
                placeholder={placeholder}
                content={entry.content || ''}
                removable={false}
                hasMoveControls={false}
                canRegenerate={canRegenerate}
                onContentChange={(next) => updateProseField(kind, next, false)}
                onOpenRegenerate={() => openRegenerate(kind, label)}
              />
            );
          })}
        </div>
      </div>

      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg overflow-hidden">
        <div className="bg-aegisaxiom-elevated px-4 py-3 border-b border-aegisaxiom-border">
          <h4 className="font-semibold text-aegisaxiom-text text-sm">
            Optional Proposal Sections
          </h4>
          <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">
            Removable. Drag a section by its grip handle, or use the arrows for keyboard reorder. Use the trash icon to drop a section, and the regenerate button to refresh content from this SOW's context. Empty optional sections are skipped from the proposal output.
          </p>
        </div>
        <div className="p-4 space-y-3">
          {orderedOptionalKinds.map((kind, idx) => {
            const meta = PS_OPTIONAL_PROSE_SECTIONS.find((s) => s.kind === kind);
            if (!meta) return null;
            const entry = proseByKind[kind] || {};
            return (
              <RichProseSection
                key={kind}
                kind={kind}
                label={meta.label}
                placeholder=""
                content={entry.content || ''}
                removable
                hasMoveControls
                canMoveUp={idx > 0}
                canMoveDown={idx < orderedOptionalKinds.length - 1}
                canRegenerate={canRegenerate}
                onContentChange={(next) => updateProseField(kind, next, true, { order: idx })}
                onMoveUp={() => swapOptionalOrder(kind, 'up')}
                onMoveDown={() => swapOptionalOrder(kind, 'down')}
                onRemove={() => removeOptionalSection(kind)}
                onOpenRegenerate={() => openRegenerate(kind, meta.label)}
                isDraggable
                isDragOver={dragOverKind === kind}
                onDragStart={handleOptionalDragStart(kind)}
                onDragOver={handleOptionalDragOver(kind)}
                onDragLeave={handleOptionalDragLeave(kind)}
                onDragEnd={handleOptionalDragEnd}
                onDrop={handleOptionalDrop(kind)}
              />
            );
          })}
        </div>
      </div>

      <RegenerateSectionModal
        open={Boolean(regenKind)}
        onClose={closeRegenerate}
        sectionLabel={regenLabel}
        currentContent={regenKind ? (proseByKind[regenKind]?.content || '') : ''}
        onGenerate={requestRegenerate}
        onReplace={applyRegenerate}
        generating={regenGenerating}
        candidate={regenCandidate}
        citations={regenCitations}
      />
    </>
  );
}

function BillingDetailsSection({
  sow,
  updateSOW,
  onAddAuthorizedUser,
  onUpdateAuthorizedUser,
  onDeleteAuthorizedUser,
  opportunity,
  lob,
  selectedTranscripts,
  onTranscriptsSelected,
}) {
  const [showTranscriptSelector, setShowTranscriptSelector] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generatingStatus, setGeneratingStatus] = useState('');

  // Auto-prefill billing email/phone from account/contact on mount
  useEffect(() => {
    if (!opportunity?.opportunity_id) return;
    const prefillBilling = async () => {
      try {
        const response = await api.get(
          `${API}/api/sales-ops/billing-prefill/${opportunity.opportunity_id}`,
        );
        if (response.ok) {
          const data = await response.json();
          // Only fill empty fields — never overwrite existing values
          if (!sow.billing_account_email && data.billing_account_email) {
            updateSOW('billing_account_email', data.billing_account_email);
          }
          if (!sow.billing_account_phone && data.billing_account_phone) {
            updateSOW('billing_account_phone', data.billing_account_phone);
          }
        }
      } catch (error) {
        console.error('Error prefilling billing info:', error);
      }
    };
    prefillBilling();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opportunity?.opportunity_id]);

  const handleExtractBilling = async (selected) => {
    if (selected) {
      setShowTranscriptSelector(false);
      onTranscriptsSelected(selected);
    }
    const transcriptsToUse = selected || selectedTranscripts;
    const transcriptIds = transcriptsToUse.map((t) => t.id);

    setGenerating(true);
    setGeneratingStatus('Extracting billing details...');

    try {
      const response = await api.post(`${API}/api/sales-ops/fireflies/generate-billing`, {
        transcript_ids: transcriptIds,
        opportunity_id: opportunity.opportunity_id,
        lob: lob,
      });

      if (response.ok) {
        const data = await response.json();
        const billing = data.billing;

        if (billing.payment_terms) {
          updateSOW('payment_terms', billing.payment_terms);
        }
        if (billing.billing_terms) {
          updateSOW('billing_terms', billing.billing_terms);
        }
        if (billing.special_billing_instructions) {
          updateSOW('special_billing_instructions', billing.special_billing_instructions);
        }
        if (billing.boa_authorized_users && billing.boa_authorized_users.length > 0) {
          updateSOW(
            'boa_authorized_users',
            billing.boa_authorized_users.map((u, i) => ({
              user_id: `boa_${Date.now()}_${i}`,
              name: u.name || '',
              email: u.email || '',
              phone: u.phone || '',
              max_hours_allowed: u.max_hours_allowed || null,
            })),
          );
        }

        toast.success(`Billing details extracted from ${data.transcripts_used} transcript(s)`);
      } else {
        const err = await response.json();
        toast.error(resolveErrorDetail(err));
      }
    } catch (error) {
      console.error('Error extracting billing details:', error);
      toast.error('Failed to extract billing details');
    } finally {
      setGenerating(false);
      setGeneratingStatus('');
    }
  };

  return (
    <div className="space-y-6">
      {/* Header with AI Extract Button */}
      <div className="flex justify-between items-center">
        <h3 className="text-lg font-semibold text-aegisaxiom-text">Billing Details</h3>
        <button
          onClick={() => {
            if (selectedTranscripts.length > 0) {
              handleExtractBilling(null);
            } else {
              setShowTranscriptSelector(true);
            }
          }}
          disabled={generating}
          className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-violet-600 to-purple-600 hover:from-violet-500 hover:to-purple-500 text-aegisaxiom-text text-sm rounded-lg transition disabled:opacity-50"
        >
          <Sparkles className="w-4 h-4" />
          {generating ? 'Extracting...' : 'Extract from Transcripts'}
        </button>
      </div>

      {/* Selected Transcripts Banner */}
      {selectedTranscripts.length > 0 && (
        <SelectedTranscriptsBanner transcripts={selectedTranscripts} />
      )}

      {/* Generation Loading State */}
      {generating && (
        <div className="bg-violet-900/20 border border-violet-200 dark:border-violet-600/30 rounded-lg p-6 flex items-center justify-center gap-3">
          <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-violet-400"></div>
          <span className="text-violet-700 dark:text-violet-300 text-sm font-medium">
            {generatingStatus}
          </span>
        </div>
      )}

      {/* Billing Contact Table */}
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg overflow-hidden">
        <div className="bg-aegisaxiom-elevated px-4 py-3 border-b border-aegisaxiom-border">
          <h4 className="font-semibold text-aegisaxiom-text text-sm">Billing Contact Information</h4>
        </div>
        <div className="divide-y divide-aegisaxiom-border">
          <div className="grid grid-cols-2">
            <div className="px-4 py-3 bg-aegisaxiom-surface border-r border-aegisaxiom-border">
              <span className="text-aegisaxiom-text-secondary text-sm font-medium">
                Billing Account Email Address
              </span>
            </div>
            <div className="px-4 py-3">
              <input
                type="email"
                value={sow.billing_account_email || ''}
                onChange={(e) => updateSOW('billing_account_email', e.target.value)}
                placeholder="billing@example.com"
                className="w-full bg-transparent text-aegisaxiom-text text-sm focus:outline-none"
              />
            </div>
          </div>
          <div className="grid grid-cols-2">
            <div className="px-4 py-3 bg-aegisaxiom-surface border-r border-aegisaxiom-border">
              <span className="text-aegisaxiom-text-secondary text-sm font-medium">
                Billing Account Telephone Number
              </span>
            </div>
            <div className="px-4 py-3">
              <input
                type="tel"
                value={sow.billing_account_phone || ''}
                onChange={(e) => updateSOW('billing_account_phone', e.target.value)}
                placeholder="941-468-2838"
                className="w-full bg-transparent text-aegisaxiom-text text-sm focus:outline-none"
              />
            </div>
          </div>
          <div className="grid grid-cols-2">
            <div className="px-4 py-3 bg-aegisaxiom-surface border-r border-aegisaxiom-border">
              <span className="text-aegisaxiom-text-secondary text-sm font-medium">Payment Terms</span>
            </div>
            <div className="px-4 py-3">
              <select
                value={sow.payment_terms || ''}
                onChange={(e) => updateSOW('payment_terms', e.target.value)}
                className="w-full bg-transparent text-aegisaxiom-text text-sm focus:outline-none [&>option]:bg-aegisaxiom-elevated"
              >
                <option value="">Select payment terms...</option>
                {PAYMENT_TERMS.map((pt) => (
                  <option key={pt.value} value={pt.value}>
                    {pt.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2">
            <div className="px-4 py-3 bg-aegisaxiom-surface border-r border-aegisaxiom-border">
              <span className="text-aegisaxiom-text-secondary text-sm font-medium">Billing Terms</span>
            </div>
            <div className="px-4 py-3">
              <select
                value={sow.billing_terms || ''}
                onChange={(e) => updateSOW('billing_terms', e.target.value)}
                className="w-full bg-transparent text-aegisaxiom-text text-sm focus:outline-none [&>option]:bg-aegisaxiom-elevated"
              >
                <option value="">Select billing terms...</option>
                {BILLING_TERMS.map((bt) => (
                  <option key={bt.value} value={bt.value}>
                    {bt.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2">
            <div className="px-4 py-3 bg-aegisaxiom-surface border-r border-aegisaxiom-border">
              <span className="text-aegisaxiom-text-secondary text-sm font-medium">
                Special Billing Instructions
              </span>
            </div>
            <div className="px-4 py-3">
              <input
                type="text"
                value={sow.special_billing_instructions || ''}
                onChange={(e) => updateSOW('special_billing_instructions', e.target.value)}
                placeholder="PO requirements, invoice format, etc."
                className="w-full bg-transparent text-aegisaxiom-text text-sm focus:outline-none"
              />
            </div>
          </div>
        </div>
      </div>

      {/* Simple Transcript Selector Modal (Billing — no validation) */}
      {showTranscriptSelector && (
        <SimpleTranscriptSelector
          onSelect={handleExtractBilling}
          onClose={() => setShowTranscriptSelector(false)}
        />
      )}

      {/* Field Services dispatch details. Service location and the
          required contact roles must be present before this SOW can
          be requested for validation. Visible only for Field Services
          SOWs because the dispatch contract does not apply to
          Professional Services or Wholesale MRR. */}
      {lob === 'field_services' && (
        <FieldServicesDispatchSection sow={sow} updateSOW={updateSOW} />
      )}

      {/* Professional Services prose sections. Carries the canonical
          seven-section proposal shape (Assumptions, Responsibilities,
          Out of Scope) plus four optional sections. Visible only for
          Professional Services SOWs. The required canonical sections
          Executive Summary, SMART Objective, Scope of Work, and
          Estimated Time render from structured SOW fields elsewhere
          and are not duplicated here. */}
      {lob === 'professional_services' && (
        <ProfessionalServicesProseSections
          sow={sow}
          updateSOW={updateSOW}
          opportunity={opportunity}
        />
      )}

      {/* BOA Authorized Users Table */}
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg overflow-hidden">
        <div className="bg-amber-50 dark:bg-amber-900/20 border-b border-amber-200 dark:border-amber-700/30 px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-amber-600 dark:text-amber-400" />
            <h4 className="font-semibold text-amber-700 dark:text-amber-300 text-sm">
              Authorized Users (Bill on Actuals)
            </h4>
            <span className="text-xs text-amber-600 dark:text-amber-400/70">
              Optional - Only needed if using BOA resources
            </span>
          </div>
          <button
            onClick={onAddAuthorizedUser}
            className="flex items-center gap-1 px-3 py-1 bg-amber-600 hover:bg-amber-500 text-white text-xs rounded transition"
          >
            <Plus className="w-3 h-3" />
            Add User
          </button>
        </div>

        {(sow.boa_authorized_users || []).length === 0 ? (
          <div className="p-6 text-center text-aegisaxiom-text-secondary text-sm">
            No authorized users added yet
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-aegisaxiom-elevated border-b border-aegisaxiom-border">
                <tr>
                  <th className="px-4 py-2 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                    Name
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                    Email
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                    Phone
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                    Max Hours
                  </th>
                  <th className="px-4 py-2 text-right text-xs font-semibold text-aegisaxiom-text-secondary">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-aegisaxiom-border">
                {(sow.boa_authorized_users || []).map((authUser) => (
                  <tr key={authUser.user_id} className="hover:bg-aegisaxiom-elevated">
                    <td className="px-4 py-2">
                      <input
                        type="text"
                        value={authUser.name}
                        onChange={(e) =>
                          onUpdateAuthorizedUser(authUser.user_id, 'name', e.target.value)
                        }
                        placeholder="Name"
                        className="w-full bg-transparent text-aegisaxiom-text text-sm focus:outline-none"
                      />
                    </td>
                    <td className="px-4 py-2">
                      <input
                        type="email"
                        value={authUser.email || ''}
                        onChange={(e) =>
                          onUpdateAuthorizedUser(authUser.user_id, 'email', e.target.value)
                        }
                        placeholder="email@example.com"
                        className="w-full bg-transparent text-aegisaxiom-text text-sm focus:outline-none"
                      />
                    </td>
                    <td className="px-4 py-2">
                      <input
                        type="tel"
                        value={authUser.phone || ''}
                        onChange={(e) =>
                          onUpdateAuthorizedUser(authUser.user_id, 'phone', e.target.value)
                        }
                        placeholder="Phone"
                        className="w-full bg-transparent text-aegisaxiom-text text-sm focus:outline-none"
                      />
                    </td>
                    <td className="px-4 py-2">
                      <input
                        type="number"
                        value={authUser.max_hours_allowed || ''}
                        onChange={(e) =>
                          onUpdateAuthorizedUser(
                            authUser.user_id,
                            'max_hours_allowed',
                            parseFloat(e.target.value) || null,
                          )
                        }
                        placeholder="0"
                        className="w-full bg-transparent text-aegisaxiom-text text-sm focus:outline-none"
                      />
                    </td>
                    <td className="px-4 py-2 text-right">
                      <button
                        onClick={() => onDeleteAuthorizedUser(authUser.user_id)}
                        className="text-aegisaxiom-text-secondary hover:text-red-600 dark:text-red-400 transition"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Legal Statement */}
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4">
        <h4 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3">Legal Terms</h4>
        <p className="text-aegisaxiom-text-secondary text-sm leading-relaxed">{LEGAL_STATEMENT}</p>
      </div>
    </div>
  );
}

// Product Items Section (Materials/Hardware/Products based on LOB)
// Field Services Sales Ops must be able to add line items even when the
// product catalog has no match — Procurement prices/sources later. The
// manual-fallback flow is gated to Field Services so Professional Services
// behavior is unchanged.
const MANUAL_FALLBACK_LOBS = ['field_services'];

const CATEGORY_SINGULAR = {
  materials: 'material',
  hardware: 'hardware',
  services: 'service',
  wholesale_mrr: 'product',
};

const CATEGORY_DEFAULT_UNIT = {
  materials: 'each',
  hardware: 'each',
  services: 'hour',
  wholesale_mrr: 'each',
};

function ProductItemsSection({
  sow,
  updateSOW,
  lob,
  itemCategory,
  sectionTitle,
  sectionDescription,
}) {
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [showAddModal, setShowAddModal] = useState(false);
  const [modalMode, setModalMode] = useState('catalog'); // 'catalog' | 'manual'
  const blankManualForm = {
    name: '',
    quantity: 1,
    unit: CATEGORY_DEFAULT_UNIT[itemCategory] || 'each',
    unitPrice: '',
    supplierName: '',
    notes: '',
    needsSourcing: false,
  };
  const [manualForm, setManualForm] = useState(blankManualForm);
  const allowManual = MANUAL_FALLBACK_LOBS.includes(lob);
  const categorySingular = CATEGORY_SINGULAR[itemCategory] || 'item';
  const manualCtaLabel =
    itemCategory === 'hardware'
      ? 'Add manual hardware'
      : `Add manual ${categorySingular}`;

  useEffect(() => {
    loadProducts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemCategory]);

  const resetModal = () => {
    setShowAddModal(false);
    setModalMode('catalog');
    setSearchQuery('');
    setManualForm(blankManualForm);
  };

  const loadProducts = async () => {
    try {
      setLoading(true);
      const response = await api.get(`${API}/api/admin/products?item_category=${itemCategory}`);

      if (response.ok) {
        const data = await response.json();
        setProducts(data.products || []);
      }
    } catch (error) {
      console.error('Error loading products:', error);
      toast.error(`Failed to load ${sectionTitle.toLowerCase()}`);
    } finally {
      setLoading(false);
    }
  };

  const addProductItem = (product) => {
    const newItem = {
      item_id: `item_${Date.now()}`,
      product_id: product.product_id,
      product_name: product.name,
      product_sku: product.sku,
      item_category: itemCategory,
      quantity: 1,
      unit_price: product.msp_pricing?.monthly || product.cost_monthly || 0,
      total_price: product.msp_pricing?.monthly || product.cost_monthly || 0,
      notes: '',
      source: 'catalog',
    };

    const updated = [...(sow.sow_product_items || []), newItem];
    updateSOW('sow_product_items', updated);
    resetModal();
    toast.success(`${product.name} added to SOW`);
  };

  const addManualItem = () => {
    const name = (manualForm.name || '').trim();
    if (!name) {
      toast.error('Item name is required');
      return;
    }
    const quantity = parseInt(manualForm.quantity, 10);
    if (!Number.isFinite(quantity) || quantity < 1) {
      toast.error('Quantity must be at least 1');
      return;
    }
    const rawPrice = `${manualForm.unitPrice}`.trim();
    const parsedPrice = rawPrice === '' ? NaN : parseFloat(rawPrice);
    const hasPrice = Number.isFinite(parsedPrice) && parsedPrice > 0;
    const unitPrice = hasPrice ? parsedPrice : 0;
    const source = !hasPrice || manualForm.needsSourcing ? 'needs_sourcing' : 'manual';
    const supplier = (manualForm.supplierName || '').trim();
    const unit = (manualForm.unit || '').trim() || CATEGORY_DEFAULT_UNIT[itemCategory] || 'each';
    const notes = (manualForm.notes || '').trim();

    const newItem = {
      item_id: `item_${Date.now()}`,
      product_id: null,
      product_name: name,
      product_sku: null,
      item_category: itemCategory,
      quantity,
      unit_price: unitPrice,
      total_price: unitPrice * quantity,
      notes,
      source,
      supplier_name: supplier || null,
      unit,
    };

    const updated = [...(sow.sow_product_items || []), newItem];
    updateSOW('sow_product_items', updated);
    resetModal();
    toast.success(
      source === 'needs_sourcing'
        ? `${name} added — flagged for sourcing`
        : `${name} added to SOW`,
    );
  };

  const updateProductItem = (itemId, field, value) => {
    const updated = (sow.sow_product_items || []).map((item) => {
      if (item.item_id === itemId) {
        const updatedItem = { ...item, [field]: value };
        // Recalculate total if quantity or unit_price changed
        if (field === 'quantity' || field === 'unit_price') {
          updatedItem.total_price = (updatedItem.quantity || 0) * (updatedItem.unit_price || 0);
        }
        // Manual rows that gain a real price are no longer "needs_sourcing".
        if (
          field === 'unit_price'
          && item.source === 'needs_sourcing'
          && (updatedItem.unit_price || 0) > 0
        ) {
          updatedItem.source = 'manual';
        }
        return updatedItem;
      }
      return item;
    });
    updateSOW('sow_product_items', updated);
  };

  const deleteProductItem = (itemId) => {
    const updated = (sow.sow_product_items || []).filter((item) => item.item_id !== itemId);
    updateSOW('sow_product_items', updated);
  };

  const sowItems = (sow.sow_product_items || []).filter(
    (item) => item.item_category === itemCategory,
  );
  const totalCost = sowItems.reduce((sum, item) => sum + (item.total_price || 0), 0);

  const filteredProducts = products.filter((p) => {
    if (!searchQuery) return true;
    const search = searchQuery.toLowerCase();
    return (
      p.name?.toLowerCase().includes(search) ||
      p.sku?.toLowerCase().includes(search) ||
      p.description?.toLowerCase().includes(search)
    );
  });

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h3 className="text-lg font-semibold text-aegisaxiom-text">{sectionTitle}</h3>
          <p className="text-sm text-aegisaxiom-text-secondary mt-1">{sectionDescription}</p>
        </div>
        <button
          onClick={() => setShowAddModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition"
        >
          <Plus className="w-4 h-4" />
          Add {sectionTitle}
        </button>
      </div>

      {/* Current Items */}
      {sowItems.length === 0 ? (
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-8 text-center">
          <Building2 className="w-12 h-12 text-aegisaxiom-text-muted mx-auto mb-3" />
          <p className="text-aegisaxiom-text-secondary text-sm mb-4">
            No {sectionTitle.toLowerCase()} added yet
          </p>
          <button
            onClick={() => setShowAddModal(true)}
            className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition"
          >
            Add First Item
          </button>
        </div>
      ) : (
        <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg overflow-hidden">
          {/* Header */}
          <div className="bg-aegisaxiom-elevated px-4 py-3 border-b border-aegisaxiom-border flex justify-between items-center">
            <h4 className="font-semibold text-aegisaxiom-text text-sm">{sectionTitle} List</h4>
            <div className="text-sm text-aegisaxiom-text-secondary">
              Total:{' '}
              <span className="text-green-600 dark:text-green-400 font-semibold">
                ${totalCost.toFixed(2)}
              </span>
            </div>
          </div>

          {/* Table */}
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-aegisaxiom-surface border-b border-aegisaxiom-border">
                <tr>
                  <th className="px-4 py-2 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                    Item Name
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                    SKU
                  </th>
                  <th className="px-4 py-2 text-center text-xs font-semibold text-aegisaxiom-text-secondary">
                    Quantity
                  </th>
                  <th className="px-4 py-2 text-right text-xs font-semibold text-aegisaxiom-text-secondary">
                    Unit Price
                  </th>
                  <th className="px-4 py-2 text-right text-xs font-semibold text-aegisaxiom-text-secondary">
                    Total
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                    Notes
                  </th>
                  <th className="px-4 py-2 text-right text-xs font-semibold text-aegisaxiom-text-secondary">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-aegisaxiom-border">
                {sowItems.map((item) => (
                  <tr key={item.item_id} className="hover:bg-aegisaxiom-elevated">
                    <td className="px-4 py-2 text-aegisaxiom-text text-sm">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span>{item.product_name}</span>
                        {item.source === 'needs_sourcing' && (
                          <span className="px-2 py-0.5 text-[10px] uppercase tracking-wide rounded border border-amber-500/40 text-amber-300 bg-amber-500/10">
                            Needs sourcing
                          </span>
                        )}
                        {item.source === 'manual' && (
                          <span className="px-2 py-0.5 text-[10px] uppercase tracking-wide rounded border border-violet-500/40 text-violet-300 bg-violet-500/10">
                            Manual
                          </span>
                        )}
                        {item.supplier_name && (
                          <span className="text-xs text-aegisaxiom-text-muted">
                            ({item.supplier_name})
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-2 text-aegisaxiom-text-secondary text-sm">
                      {item.product_sku || '—'}
                    </td>
                    <td className="px-4 py-2 text-center">
                      <input
                        type="number"
                        value={item.quantity}
                        onChange={(e) =>
                          updateProductItem(item.item_id, 'quantity', parseInt(e.target.value) || 0)
                        }
                        min="1"
                        className="w-20 px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm text-center focus:outline-none focus:border-violet-500"
                      />
                    </td>
                    <td className="px-4 py-2 text-right">
                      <input
                        type="number"
                        value={item.unit_price}
                        onChange={(e) =>
                          updateProductItem(
                            item.item_id,
                            'unit_price',
                            parseFloat(e.target.value) || 0,
                          )
                        }
                        step="0.01"
                        className="w-24 px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm text-right focus:outline-none focus:border-violet-500"
                      />
                    </td>
                    <td className="px-4 py-2 text-right text-green-600 dark:text-green-400 font-semibold text-sm">
                      ${item.total_price.toFixed(2)}
                    </td>
                    <td className="px-4 py-2">
                      <input
                        type="text"
                        value={item.notes || ''}
                        onChange={(e) => updateProductItem(item.item_id, 'notes', e.target.value)}
                        placeholder="Notes..."
                        className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                      />
                    </td>
                    <td className="px-4 py-2 text-right">
                      <button
                        onClick={() => deleteProductItem(item.item_id)}
                        className="text-aegisaxiom-text-secondary hover:text-red-600 dark:text-red-400 transition"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Add Product Modal */}
      {showAddModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div
            className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-2xl mx-4"
            data-testid={`add-${itemCategory}-modal`}
          >
            <div className="border-b border-aegisaxiom-border p-4 flex justify-between items-center">
              <h3 className="text-lg font-semibold text-aegisaxiom-text">
                {modalMode === 'manual' ? `Add manual ${categorySingular}` : `Add ${sectionTitle}`}
              </h3>
              <button
                onClick={resetModal}
                className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {modalMode === 'catalog' ? (
              <>
                <div className="p-4">
                  {/* Search */}
                  <div className="mb-4 relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-aegisaxiom-text-secondary" />
                    <input
                      type="text"
                      placeholder="Search products..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      className="w-full pl-10 pr-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500"
                    />
                  </div>

                  {/* Product List */}
                  <div className="max-h-96 overflow-y-auto space-y-2">
                    {loading ? (
                      <div className="flex items-center justify-center py-8">
                        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-violet-500"></div>
                      </div>
                    ) : filteredProducts.length === 0 ? (
                      <div className="text-center py-8 text-aegisaxiom-text-secondary">
                        <p className="mb-3">
                          No {sectionTitle.toLowerCase()} found
                          {searchQuery && ' matching your search'}.
                          {allowManual && ` Add this as a manual ${categorySingular}.`}
                        </p>
                        {allowManual && (
                          <button
                            type="button"
                            onClick={() => setModalMode('manual')}
                            data-testid={`add-manual-${itemCategory}-empty-cta`}
                            className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition inline-flex items-center gap-2"
                          >
                            <Plus className="w-4 h-4" />
                            {manualCtaLabel}
                          </button>
                        )}
                      </div>
                    ) : (
                      filteredProducts.map((product) => (
                        <div
                          key={product.product_id}
                          className="bg-aegisaxiom-base border border-aegisaxiom-border hover:border-violet-500 rounded-lg p-3 cursor-pointer transition"
                          onClick={() => addProductItem(product)}
                        >
                          <div className="flex justify-between items-start mb-1">
                            <h4 className="font-medium text-aegisaxiom-text">{product.name}</h4>
                            <span className="text-green-600 dark:text-green-400 font-semibold text-sm">
                              ${(product.msp_pricing?.monthly || product.cost_monthly || 0).toFixed(2)}
                              /mo
                            </span>
                          </div>
                          <p className="text-xs text-aegisaxiom-text-secondary mb-2">
                            {product.description}
                          </p>
                          <div className="flex items-center gap-3 text-xs text-aegisaxiom-text-muted">
                            <span>SKU: {product.sku}</span>
                            <span>Type: {product.product_type}</span>
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </div>

                <div className="border-t border-aegisaxiom-border p-4 flex justify-between items-center">
                  {allowManual ? (
                    <button
                      type="button"
                      onClick={() => setModalMode('manual')}
                      data-testid={`add-manual-${itemCategory}-footer-cta`}
                      className="px-3 py-2 text-sm text-violet-300 hover:text-violet-200 transition inline-flex items-center gap-1"
                    >
                      <Plus className="w-4 h-4" />
                      {manualCtaLabel}
                    </button>
                  ) : (
                    <span />
                  )}
                  <button
                    onClick={resetModal}
                    className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg transition"
                  >
                    Close
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="p-4 space-y-3" data-testid={`manual-${itemCategory}-form`}>
                  <div>
                    <label className="block text-xs font-semibold text-aegisaxiom-text-secondary mb-1">
                      Item name *
                    </label>
                    <input
                      type="text"
                      value={manualForm.name}
                      onChange={(e) =>
                        setManualForm((f) => ({ ...f, name: e.target.value }))
                      }
                      placeholder={`Describe the ${categorySingular}`}
                      className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500"
                    />
                  </div>

                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className="block text-xs font-semibold text-aegisaxiom-text-secondary mb-1">
                        Quantity *
                      </label>
                      <input
                        type="number"
                        min="1"
                        value={manualForm.quantity}
                        onChange={(e) =>
                          setManualForm((f) => ({ ...f, quantity: e.target.value }))
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-semibold text-aegisaxiom-text-secondary mb-1">
                        Unit
                      </label>
                      <input
                        type="text"
                        value={manualForm.unit}
                        onChange={(e) =>
                          setManualForm((f) => ({ ...f, unit: e.target.value }))
                        }
                        placeholder={CATEGORY_DEFAULT_UNIT[itemCategory] || 'each'}
                        className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-semibold text-aegisaxiom-text-secondary mb-1">
                        Unit price
                      </label>
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        value={manualForm.unitPrice}
                        onChange={(e) =>
                          setManualForm((f) => ({ ...f, unitPrice: e.target.value }))
                        }
                        placeholder="Leave blank if unknown"
                        className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500"
                      />
                    </div>
                  </div>

                  <div>
                    <label className="block text-xs font-semibold text-aegisaxiom-text-secondary mb-1">
                      Supplier (optional)
                    </label>
                    <input
                      type="text"
                      value={manualForm.supplierName}
                      onChange={(e) =>
                        setManualForm((f) => ({ ...f, supplierName: e.target.value }))
                      }
                      placeholder="Procurement will fill if blank"
                      className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500"
                    />
                  </div>

                  <div>
                    <label className="block text-xs font-semibold text-aegisaxiom-text-secondary mb-1">
                      Notes
                    </label>
                    <textarea
                      value={manualForm.notes}
                      onChange={(e) =>
                        setManualForm((f) => ({ ...f, notes: e.target.value }))
                      }
                      rows={2}
                      placeholder="Specs, model, scope context..."
                      className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 resize-y"
                    />
                  </div>

                  <label className="flex items-center gap-2 text-sm text-aegisaxiom-text-secondary cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={manualForm.needsSourcing}
                      onChange={(e) =>
                        setManualForm((f) => ({ ...f, needsSourcing: e.target.checked }))
                      }
                      className="rounded border-aegisaxiom-border bg-aegisaxiom-base"
                    />
                    Needs sourcing (Procurement will price)
                  </label>
                </div>

                <div className="border-t border-aegisaxiom-border p-4 flex justify-between items-center">
                  <button
                    type="button"
                    onClick={() => setModalMode('catalog')}
                    className="px-3 py-2 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
                  >
                    Back to catalog search
                  </button>
                  <div className="flex gap-2">
                    <button
                      onClick={resetModal}
                      className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg transition"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={addManualItem}
                      data-testid={`save-manual-${itemCategory}`}
                      className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition"
                    >
                      Add to SOW
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// Resource Assignments Section (Field Services & Professional Services)

// LEFT/RIGHT Split Resource View - Customer Needs (LEFT) + Contractor Fulfillment (RIGHT)
function IntegratedResourceView({ opportunity, sow, updateSOW, onResourceChange }) {
  const [availableResources, setAvailableResources] = useState([]);
  const [resourceTypes, setResourceTypes] = useState([]);
  const [contractors, setContractors] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAddForm, setShowAddForm] = useState(false);

  useEffect(() => {
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opportunity.opportunity_id]);

  const loadData = async () => {
    try {
      setLoading(true);

      const [resourcesRes, rtRes, contractorsRes, templatesRes] = await Promise.all([
        api.get(
          `${API}/api/sales-ops/opportunities/${opportunity.opportunity_id}/resource-assignments`,
        ),
        api.get(`${API}/api/admin/default-resource-types`),
        api.get(`${API}/api/sales-ops/contractor-businesses`),
        api.get(`${API}/api/admin/contract-templates`),
      ]);

      if (resourcesRes.ok) {
        const data = await resourcesRes.json();
        setAvailableResources(data.items || []);
      }

      if (rtRes.ok) {
        const data = await rtRes.json();
        setResourceTypes(data.default_resource_types || []);
      }

      if (contractorsRes.ok) {
        const data = await contractorsRes.json();
        setContractors(data.items || []);
      }

      if (templatesRes.ok) {
        const data = await templatesRes.json();
        setTemplates(data.items || []);
      }
    } catch (error) {
      console.error('Error loading resources:', error);
      toast.error('Failed to load resource data');
    } finally {
      setLoading(false);
    }
  };

  const removeResource = (assignmentId) => {
    const currentSelections = sow.selected_resource_assignment_ids || [];
    const updated = currentSelections.filter((id) => id !== assignmentId);
    updateSOW('selected_resource_assignment_ids', updated);
  };

  // Notify Procurement when a selected resource is blocked because a
  // contractor is missing. POSTs to the existing procurement
  // notifications endpoint with the SOW context plus a flat
  // site_location_summary string built from the SOW's
  // service_location field so the procurement queue can render
  // dispatch context without joining back to the SOW. No external
  // tool integration is performed; this is AEGISAXIOM-side internal
  // tracking only. See
  // docs/sales-ops/SHARED_SOW_BUILDER_SUBSTRATE.md.
  const notifyProcurement = async (resource) => {
    const loc = sow.service_location || {};
    const summaryParts = [loc.address, loc.city, loc.state, loc.postal_code]
      .map((p) => (p || '').trim())
      .filter(Boolean);
    const siteSummary = summaryParts.join(', ');
    try {
      const response = await api.post(
        `${API}/api/sales-ops/procurement-notifications`,
        {
          notification_type: 'missing_resource',
          resource_type_name: resource.resource_type_name,
          resource_assignment_id: resource.assignment_id,
          sow_id: sow.sow_id,
          line_of_business: sow.line_of_business,
          opportunity_id: opportunity.opportunity_id,
          site_location_summary: siteSummary || null,
          message: 'Sales Ops needs procurement to source a contractor for this resource.',
        },
      );
      if (response.ok) {
        toast.success('Procurement notified.');
      } else {
        let detail = `Failed to notify Procurement (HTTP ${response.status}).`;
        try {
          const body = await response.json();
          if (body && typeof body.detail === 'string') detail = body.detail;
        } catch (_e) {
          // body was not JSON.
        }
        toast.error(detail);
      }
    } catch (error) {
      console.error('Error notifying procurement:', error);
      toast.error('Failed to notify Procurement.');
    }
  };

  const selectedIds = sow.selected_resource_assignment_ids || [];
  const selectedResources = availableResources.filter((r) => selectedIds.includes(r.assignment_id));

  const formatCurrency = (amount) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
    }).format(amount || 0);
  };

  const totalResourceCost = selectedResources.reduce((sum, r) => sum + (r.total_sell || 0), 0);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-violet-500"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h3 className="text-lg font-semibold text-aegisaxiom-text flex items-center gap-2">
            <UserCheck className="w-5 h-5 text-violet-600 dark:text-violet-400" />
            Resource Assignments
          </h3>
          <p className="text-sm text-aegisaxiom-text-secondary mt-1">
            Define customer needs and contractor fulfillment independently
          </p>
        </div>
        {selectedResources.length > 0 && (
          <div className="text-sm">
            <span className="text-aegisaxiom-text-secondary">Total: </span>
            <span className="text-green-600 dark:text-green-400 font-semibold text-lg">
              {formatCurrency(totalResourceCost)}
            </span>
          </div>
        )}
      </div>

      {/* Selected Resources List - LEFT/RIGHT SPLIT DISPLAY */}
      {selectedResources.length === 0 ? (
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-8 text-center">
          <UserCheck className="w-12 h-12 text-aegisaxiom-text-muted mx-auto mb-3" />
          <p className="text-aegisaxiom-text-secondary text-sm mb-4">No resources assigned yet</p>
          <button
            onClick={() => setShowAddForm(true)}
            className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg transition text-sm"
          >
            Add First Resource
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          {selectedResources.map((resource) => {
            // Check if customer billing term matches contractor pay term
            const customerTerm = resource.customer_billing_term || 'boh';
            const contractorTerm = resource.pricing_model || 'block_hours';

            // Normalize for comparison
            const normCustomer =
              customerTerm === 'boh'
                ? 'block_hours'
                : customerTerm === 'boa'
                  ? 'bill_actuals'
                  : customerTerm === 'fixed_fee'
                    ? 'fixed_price'
                    : customerTerm === 'monthly'
                      ? 'monthly'
                      : customerTerm === 'one_time'
                        ? 'one_time'
                        : customerTerm;

            const hasMismatch = normCustomer !== contractorTerm;

            // Determine health badge
            const isBlocked =
              resource.status === 'blocked_missing_contractor' || (resource.profit_margin || 0) < 0;
            const isWarning = !isBlocked && (resource.profit_margin || 0) < 20;
            const isHealthy = !isBlocked && !isWarning;

            return (
              <div
                key={resource.assignment_id}
                className={`bg-aegisaxiom-surface border-2 rounded-lg p-5 hover:border-aegisaxiom-border-hover transition ${
                  isBlocked
                    ? 'border-red-200 dark:border-red-700/50 bg-red-50 dark:bg-red-900/10'
                    : isWarning
                      ? 'border-yellow-200 dark:border-yellow-700/50'
                      : 'border-aegisaxiom-border'
                }`}
              >
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h5 className="font-semibold text-aegisaxiom-text text-lg">
                      {resource.resource_type_name}
                    </h5>

                    {/* Visual Health Badge */}
                    {isBlocked && (
                      <span className="flex items-center gap-1 px-2 py-1 bg-red-50 dark:bg-red-900/40 border border-red-200 dark:border-red-700/50 rounded text-xs font-semibold text-red-700 dark:text-red-300">
                        <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse"></span>
                        CRITICAL BLOCKER
                      </span>
                    )}
                    {/* Notify Procurement. Visible only when the
                        resource is blocked specifically because a
                        contractor is missing. The button fires the
                        procurement-notification create endpoint with
                        SOW context plus a site location summary
                        built from the SOW's service_location field. */}
                    {resource.status === 'blocked_missing_contractor' && (
                      <button
                        type="button"
                        onClick={() => notifyProcurement(resource)}
                        title="Send this gap to Procurement with the SOW context and site location summary."
                        className="px-2 py-1 bg-violet-600 hover:bg-violet-500 text-white text-xs rounded transition"
                        data-testid="fs-notify-procurement"
                      >
                        Notify Procurement
                      </button>
                    )}
                    {isWarning && (
                      <span className="flex items-center gap-1 px-2 py-1 bg-yellow-50 dark:bg-yellow-900/40 border border-yellow-200 dark:border-yellow-700/50 rounded text-xs font-semibold text-yellow-700 dark:text-yellow-300">
                        <span className="w-2 h-2 rounded-full bg-yellow-500"></span>
                        LOW MARGIN
                      </span>
                    )}
                    {isHealthy && (
                      <span className="flex items-center gap-1 px-2 py-1 bg-green-50 dark:bg-green-900/40 border border-green-200 dark:border-green-700/50 rounded text-xs font-semibold text-green-700 dark:text-green-300">
                        <span className="w-2 h-2 rounded-full bg-green-500"></span>
                        HEALTHY
                      </span>
                    )}

                    <p className="text-sm text-aegisaxiom-text-secondary w-full mt-1">
                      Customer Rate:{' '}
                      {formatCurrency(resource.sell_rate || resource.fixed_sell || 0)}
                      {resource.pricing_model !== 'fixed_price' && '/hr'}
                    </p>
                  </div>
                  <button
                    onClick={() => removeResource(resource.assignment_id)}
                    className="text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300 transition p-2 hover:bg-red-50 dark:bg-red-900/20 rounded"
                    title="Remove resource"
                  >
                    <Trash2 className="w-5 h-5" />
                  </button>
                </div>

                {/* LEFT/RIGHT SPLIT GRID */}
                <div className="grid grid-cols-2 gap-4">
                  {/* LEFT: Customer Needs */}
                  <div className="bg-blue-900/20 border border-blue-700/40 rounded-lg p-4">
                    <h6 className="text-xs font-bold text-blue-700 dark:text-blue-300 mb-3 uppercase tracking-wide">
                      Customer Needs
                    </h6>
                    <div className="space-y-2 text-sm">
                      <div className="flex justify-between">
                        <span className="text-aegisaxiom-text-secondary">Billing Term:</span>
                        <span className="text-aegisaxiom-text font-medium">
                          {customerTerm.toUpperCase()}
                        </span>
                      </div>
                      {resource.payment_terms && (
                        <div className="flex justify-between">
                          <span className="text-aegisaxiom-text-secondary">Payment:</span>
                          <span className="text-aegisaxiom-text text-xs">
                            {resource.payment_terms.replace('_', ' ').toUpperCase()}
                          </span>
                        </div>
                      )}
                      {resource.convert_to_boa_at_exhaustion && (
                        <div className="text-xs text-amber-700 dark:text-amber-300 flex items-center gap-1 mt-1">
                          <AlertCircle className="w-3 h-3" />
                          Convert to BOA at exhaustion
                        </div>
                      )}
                      <div className="flex justify-between">
                        <span className="text-aegisaxiom-text-secondary">Hours:</span>
                        <span className="text-aegisaxiom-text">{resource.estimated_hours || 0}h</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-aegisaxiom-text-secondary">Rate:</span>
                        <span className="text-green-600 dark:text-green-400 font-semibold">
                          ${resource.sell_rate || 0}/hr
                        </span>
                      </div>
                      <div className="flex justify-between border-t border-blue-800/50 pt-2 mt-2">
                        <span className="text-aegisaxiom-text-secondary">Total:</span>
                        <span className="text-green-600 dark:text-green-400 font-bold">
                          {formatCurrency(resource.total_sell || 0)}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* RIGHT: Contractor Fulfillment */}
                  <div className="bg-purple-900/20 border border-purple-700/40 rounded-lg p-4">
                    <h6 className="text-xs font-bold text-purple-300 mb-3 uppercase tracking-wide">
                      Contractor Fulfillment
                    </h6>
                    <div className="space-y-2 text-sm">
                      <div className="flex justify-between">
                        <span className="text-aegisaxiom-text-secondary">Contractor:</span>
                        <span className="text-aegisaxiom-text font-medium truncate ml-2">
                          {resource.contractor_user_name}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-aegisaxiom-text-secondary">Business:</span>
                        <span className="text-aegisaxiom-text truncate ml-2">
                          {resource.contractor_business_name}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-aegisaxiom-text-secondary">Pay Type:</span>
                        <span
                          className={`font-semibold ${hasMismatch ? 'text-yellow-600 dark:text-yellow-400' : 'text-green-600 dark:text-green-400'}`}
                        >
                          {contractorTerm.replace('_', ' ').toUpperCase()}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-aegisaxiom-text-secondary">Cost Rate:</span>
                        <span className="text-red-700 dark:text-red-300">
                          ${resource.cost_rate || 0}/hr
                        </span>
                      </div>
                      {resource.contractor_payment_terms && (
                        <div className="flex justify-between">
                          <span className="text-aegisaxiom-text-secondary">Payment:</span>
                          <span className="text-aegisaxiom-text text-xs">
                            {resource.contractor_payment_terms.replace('_', ' ').toUpperCase()}
                          </span>
                        </div>
                      )}
                      <div className="flex justify-between border-t border-purple-800/50 pt-2 mt-2">
                        <span className="text-aegisaxiom-text-secondary">Total Cost:</span>
                        <span className="text-red-600 dark:text-red-400 font-bold">
                          {formatCurrency(resource.total_cost || 0)}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Profitability Bar (Full Width Below Grid) */}
                {resource.total_sell > 0 && resource.total_cost > 0 && (
                  <div className="mt-3 bg-aegisaxiom-elevated rounded-lg p-3">
                    <div className="flex justify-between items-center mb-2">
                      <span className="text-xs font-semibold text-aegisaxiom-text-secondary">
                        PROFITABILITY
                      </span>
                      <div className="flex items-center gap-4 text-sm">
                        <span className="text-green-600 dark:text-green-400">
                          Revenue: {formatCurrency(resource.total_sell)}
                        </span>
                        <span className="text-aegisaxiom-text-muted">-</span>
                        <span className="text-red-600 dark:text-red-400">
                          Cost: {formatCurrency(resource.total_cost)}
                        </span>
                        <span className="text-aegisaxiom-text-muted">=</span>
                        <span className="text-blue-600 dark:text-blue-400 font-bold">
                          Profit: {formatCurrency(resource.total_sell - resource.total_cost)}
                        </span>
                        <span
                          className={`font-bold text-lg ${
                            resource.profit_margin >= 30
                              ? 'text-green-600 dark:text-green-400'
                              : resource.profit_margin >= 20
                                ? 'text-yellow-600 dark:text-yellow-400'
                                : 'text-red-600 dark:text-red-400'
                          }`}
                        >
                          {resource.profit_margin?.toFixed(1) ||
                            (
                              ((resource.total_sell - resource.total_cost) / resource.total_sell) *
                              100
                            ).toFixed(1)}
                          %
                        </span>
                      </div>
                    </div>
                    {/* Visual Progress Bar */}
                    <div className="w-full bg-aegisaxiom-elevated rounded-full h-2">
                      <div
                        className={`h-2 rounded-full ${
                          (resource.profit_margin || 0) >= 30
                            ? 'bg-green-500'
                            : (resource.profit_margin || 0) >= 20
                              ? 'bg-yellow-500'
                              : 'bg-red-500'
                        }`}
                        style={{ width: `${Math.min(resource.profit_margin || 0, 100)}%` }}
                      />
                    </div>
                  </div>
                )}

                {/* Mismatch Warning (Below the grid) */}
                {hasMismatch && (
                  <div className="mt-3">
                    <div className="flex items-start gap-2 text-xs bg-yellow-50 dark:bg-yellow-900/30 border border-yellow-200 dark:border-yellow-700/40 rounded px-3 py-2">
                      <AlertCircle className="w-4 h-4 text-yellow-600 dark:text-yellow-400 flex-shrink-0 mt-0.5" />
                      <div className="text-yellow-200">
                        <span className="font-semibold">Billing Term Mismatch:</span> Customer (
                        {normCustomer.replace('_', ' ')}) ≠ Contractor (
                        {contractorTerm.replace('_', ' ')})
                      </div>
                    </div>
                  </div>
                )}

                {resource.notes && (
                  <div className="mt-3 text-xs text-aegisaxiom-text-secondary italic bg-aegisaxiom-elevated rounded px-3 py-2">
                    {resource.notes}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Add Resource Button */}
      {selectedResources.length > 0 && (
        <div className="flex justify-center">
          <button
            onClick={() => setShowAddForm(true)}
            className="px-6 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg transition flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            Add Resource Type
          </button>
        </div>
      )}

      {/* Add Resource Form Modal */}
      {showAddForm && (
        <ResourceAssignmentForm
          opportunity={opportunity}
          sow={sow}
          contractors={contractors}
          resourceTypes={resourceTypes}
          templates={templates}
          updateSOW={updateSOW}
          onClose={() => setShowAddForm(false)}
          onSuccess={() => {
            setShowAddForm(false);
            loadData();
            if (onResourceChange) onResourceChange(); // Trigger blocker recheck
          }}
        />
      )}
    </div>
  );
}

function ResourceCard({ resource, isSelected, onToggle }) {
  const pricingModelLabels = {
    block_hours: {
      label: 'Block of Hours',
      color: 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300',
    },
    bill_actuals: { label: 'Bill on Actuals', color: 'bg-purple-600/20 text-purple-300' },
    fixed_price: {
      label: 'Fixed Price',
      color: 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300',
    },
  };

  const pricingConfig =
    pricingModelLabels[resource.pricing_model] || pricingModelLabels.block_hours;

  const formatCurrency = (amount) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
    }).format(amount || 0);
  };

  return (
    <div
      className={`bg-aegisaxiom-surface border rounded-lg p-4 cursor-pointer transition ${
        isSelected
          ? 'border-green-500 bg-green-50 dark:bg-green-900/10'
          : 'border-aegisaxiom-border hover:border-violet-500'
      }`}
      onClick={() => onToggle(resource.assignment_id)}
    >
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-2">
            <div
              className={`w-5 h-5 rounded border-2 flex items-center justify-center ${
                isSelected ? 'bg-green-600 border-green-600' : 'border-aegisaxiom-border-hover'
              }`}
            >
              {isSelected && <CheckCircle className="w-4 h-4 text-aegisaxiom-text" />}
            </div>
            <h4 className="font-semibold text-aegisaxiom-text">{resource.resource_type_name}</h4>
            <span className={`px-2 py-0.5 rounded text-xs font-medium ${pricingConfig.color}`}>
              {pricingConfig.label}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm ml-7">
            <div>
              <span className="text-aegisaxiom-text-secondary">Business:</span>
              <span className="text-aegisaxiom-text ml-2">
                {resource.contractor_business_name || 'Internal'}
              </span>
            </div>
            <div>
              <span className="text-aegisaxiom-text-secondary">Person:</span>
              <span className="text-aegisaxiom-text ml-2">
                {resource.contractor_user_name || resource.internal_user_name || 'TBD'}
              </span>
            </div>

            {resource.pricing_model === 'fixed_price' ? (
              <>
                <div>
                  <span className="text-aegisaxiom-text-secondary">Fixed Cost:</span>
                  <span className="text-red-700 dark:text-red-300 ml-2 font-semibold">
                    {formatCurrency(resource.fixed_cost)}
                  </span>
                </div>
                <div>
                  <span className="text-aegisaxiom-text-secondary">Fixed Sell:</span>
                  <span className="text-green-600 dark:text-green-400 ml-2 font-semibold">
                    {formatCurrency(resource.fixed_sell)}
                  </span>
                </div>
              </>
            ) : (
              <>
                <div>
                  <span className="text-aegisaxiom-text-secondary">Hours:</span>
                  <span className="text-aegisaxiom-text ml-2">{resource.estimated_hours}h</span>
                </div>
                <div>
                  <span className="text-aegisaxiom-text-secondary">Rates:</span>
                  <span className="text-aegisaxiom-text ml-2">
                    ${resource.cost_rate}/hr → ${resource.sell_rate}/hr
                  </span>
                </div>
              </>
            )}

            <div>
              <span className="text-aegisaxiom-text-secondary">Total Sell:</span>
              <span className="text-green-600 dark:text-green-400 ml-2 font-semibold">
                {formatCurrency(resource.total_sell)}
              </span>
            </div>
            <div>
              <span className="text-aegisaxiom-text-secondary">Status:</span>
              <span
                className={`ml-2 text-xs px-2 py-0.5 rounded ${
                  resource.status === 'confirmed'
                    ? 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300'
                    : resource.status === 'in_progress'
                      ? 'bg-blue-900/30 text-blue-700 dark:text-blue-300'
                      : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary'
                }`}
              >
                {resource.status}
              </span>
            </div>
          </div>

          {resource.notes && (
            <div className="mt-2 text-xs text-aegisaxiom-text-secondary italic ml-7">
              {resource.notes}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// I3. Inline dialog used by Send Proposal.
//
// Two confirm paths.
//   1. Send. Requires a value that matches a minimal email pattern. The
//      Send button stays disabled until the input passes validation.
//   2. Mark as sent only. Posts the proposal with no recipient and
//      skips any email send.
//
// The dialog manages a minimal focus trap. The email input receives
// focus on mount. Tab and Shift Tab cycle between the email input, the
// Send button, the Mark as sent only button, and the Cancel button.
// Escape cancels. The dialog parent decides where focus returns when
// the dialog closes.
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function SendProposalDialog({ proposalId, onSend, onMarkSentOnly, onCancel }) {
  const [email, setEmail] = useState('');
  const inputRef = useRef(null);
  const sendButtonRef = useRef(null);
  const markButtonRef = useRef(null);
  const cancelButtonRef = useRef(null);

  useEffect(() => {
    if (inputRef.current && typeof inputRef.current.focus === 'function') {
      inputRef.current.focus();
    }
  }, []);

  const trimmed = (email || '').trim();
  const isValidEmail = EMAIL_PATTERN.test(trimmed);

  const handleKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onCancel();
      return;
    }
    if (event.key !== 'Tab') {
      return;
    }
    // Manual focus trap across the four tabbable elements in the
    // dialog. The order mirrors the visual order.
    const order = [
      inputRef.current,
      sendButtonRef.current,
      markButtonRef.current,
      cancelButtonRef.current,
    ].filter(Boolean);
    if (order.length === 0) {
      return;
    }
    const active = typeof document !== 'undefined' ? document.activeElement : null;
    const currentIndex = order.indexOf(active);
    let nextIndex;
    if (event.shiftKey) {
      nextIndex = currentIndex <= 0 ? order.length - 1 : currentIndex - 1;
    } else {
      nextIndex = currentIndex === order.length - 1 || currentIndex === -1 ? 0 : currentIndex + 1;
    }
    event.preventDefault();
    const target = order[nextIndex];
    if (target && typeof target.focus === 'function') {
      target.focus();
    }
  };

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-labelledby="send-proposal-dialog-title"
      onKeyDown={handleKeyDown}
    >
      <div className="absolute inset-0" onClick={onCancel} aria-hidden="true" />

      <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl w-full max-w-md">
        <div className="p-5 pb-3">
          <h3
            id="send-proposal-dialog-title"
            className="text-base font-semibold text-aegisaxiom-text leading-snug"
          >
            Hand off proposal to Sales
          </h3>
          <p className="mt-1.5 text-sm text-aegisaxiom-text-secondary leading-relaxed">
            Enter the Sales recipient email to record the internal handoff. AEGISAXIOM does not email the client. The client-facing send is a separate Sales action.
          </p>
        </div>

        <div className="px-5 pb-2">
          <label
            htmlFor="send-proposal-email-input"
            className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5"
          >
            Sales recipient email
          </label>
          <input
            id="send-proposal-email-input"
            ref={inputRef}
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="name@example.com"
            className="w-full px-3 py-2 rounded-lg text-sm bg-aegisaxiom-base border border-aegisaxiom-border text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500"
            aria-invalid={trimmed.length > 0 && !isValidEmail}
          />
          {trimmed.length > 0 && !isValidEmail && (
            <p className="mt-1.5 text-xs text-red-600 dark:text-red-400">
              Enter a valid email address
            </p>
          )}
        </div>

        <div className="flex flex-col gap-2 px-5 pb-5 pt-3">
          <button
            ref={sendButtonRef}
            type="button"
            onClick={() => {
              if (!isValidEmail) {
                return;
              }
              onSend(proposalId, trimmed);
            }}
            disabled={!isValidEmail}
            className="w-full px-4 py-2 rounded-lg text-sm font-medium bg-violet-600 hover:bg-violet-500 text-white transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Hand off
          </button>
          <button
            ref={markButtonRef}
            type="button"
            onClick={() => onMarkSentOnly(proposalId)}
            className="w-full px-4 py-2 rounded-lg text-sm font-medium bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text border border-aegisaxiom-border hover:border-aegisaxiom-border-hover transition"
          >
            Hand off without recipient
          </button>
          <button
            ref={cancelButtonRef}
            type="button"
            onClick={onCancel}
            className="w-full px-4 py-2 rounded-lg text-sm font-medium text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
