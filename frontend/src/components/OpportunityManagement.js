import React, { useState, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '../lib/apiConfig';
import { useAuth } from '@/contexts/AuthContext';
import {
  Plus,
  LayoutGrid,
  List,
  Filter,
  Search,
  X,
  DollarSign,
  Calendar,
  Flag,
  User,
  Building2,
  GripVertical,
  Eye,
  Edit,
  Trash2,
  MessageSquare,
  Paperclip,
  TestTube,
  CheckCircle,
  Users,
  ChevronDown,
  Flame,
  AlertTriangle,
  Info,
  Target,
  Copy,
} from 'lucide-react';

// Copy tracking number (OPP-YYYY-NNNNNN) to clipboard. navigator.clipboard.writeText
// returns a Promise — await it so permission/insecure-context rejections surface
// as a toast instead of being silently swallowed.
const copyTrackingNumber = async (e, tn) => {
  e?.stopPropagation?.();
  if (!tn) return;
  try {
    await navigator.clipboard.writeText(tn);
    toast.success(`Copied ${tn}`);
  } catch {
    toast.error('Could not copy to clipboard');
  }
};
import OpportunityFormModal from './opportunities/OpportunityFormModal';
import OpportunityDetailModal from './opportunities/OpportunityDetailModal';
import ConfirmModal from './shared/ConfirmModal';

const API = API_URL;

// Stage configuration with colors and display names
const STAGE_CONFIG = {
  prospect_opp: {
    label: 'Prospect Opportunity',
    color: 'bg-aegisaxiom-elevated',
    textColor: 'text-aegisaxiom-text',
  },
  prospect_lead: { label: 'Prospect Lead', color: 'bg-blue-500', textColor: 'text-blue-100' },
  proposal_development: {
    label: 'Proposal Development',
    color: 'bg-indigo-500',
    textColor: 'text-indigo-100',
  },
  scoping_complete: {
    label: 'Scoping Complete',
    color: 'bg-purple-500',
    textColor: 'text-purple-100',
  },
  proposal_sent: { label: 'Proposal Sent', color: 'bg-violet-500', textColor: 'text-violet-100' },
  negotiation_review: {
    label: 'Negotiation/Review',
    color: 'bg-fuchsia-500',
    textColor: 'text-fuchsia-100',
  },
  verbal_agreement: { label: 'Verbal Agreement', color: 'bg-pink-500', textColor: 'text-pink-100' },
  closed_won: { label: 'Closed Won', color: 'bg-green-500', textColor: 'text-green-100' },
  closed_lost: { label: 'Closed Lost', color: 'bg-red-500', textColor: 'text-red-100' },
};

const PRIORITY_CONFIG = {
  high: {
    label: 'High',
    color:
      'bg-red-50 text-red-700 border-red-300 dark:bg-red-900/30 dark:text-red-300 dark:border-red-800/40',
    Icon: Flame,
  },
  medium: {
    label: 'Medium',
    color:
      'bg-amber-50 text-amber-700 border-amber-300 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-800/40',
    Icon: AlertTriangle,
  },
  low: {
    label: 'Low',
    color:
      'bg-green-50 text-green-700 border-green-300 dark:bg-green-900/30 dark:text-green-300 dark:border-green-800/40',
    Icon: Info,
  },
};

// ---------------------------------------------------------------------------
// Close-date alert helper. Cody asked for visible alerts when close dates are
// approaching or overdue for funnel hygiene. Pure function, exported so tests
// can target it directly without rendering the page.
// ---------------------------------------------------------------------------
const OPEN_STAGES_FOR_CLOSE_ALERT = new Set([
  'prospect_lead',
  'prospect_opp',
  'proposal_development',
  'scoping_complete',
  'proposal_sent',
  'negotiation_review',
  'verbal_agreement',
]);

// Field-name aliases for the actual close-date column on the payload.
// Read in priority order; the first non-empty value wins. The previous
// implementation fell back to ``desired_proposal_date``, which is the
// prospective proposal-send date and NOT a close date. That fallback
// caused every opportunity with a past desired_proposal_date to render
// as "Close date overdue" even though it had no close date at all.
//
// Do NOT add created_at / updated_at / stage timestamps / tracking_number
// to this list — the alert must reflect the actual close-date field or
// stay quiet. Opportunity payloads from the current backend model do
// not carry any of these fields, so opportunities will simply not show
// a close-date alert until the model gains one. Deal-shaped payloads
// (which do carry ``close_date``) still light up correctly.
const CLOSE_DATE_FIELDS = [
  'close_date',
  'expected_close_date',
  'expected_close',
  'closeDate',
];

export function getCloseDateAlert(opportunity, today = new Date()) {
  if (!opportunity) return 'normal';
  if (!OPEN_STAGES_FOR_CLOSE_ALERT.has(opportunity.stage)) return 'normal';

  let rawDate = null;
  for (const field of CLOSE_DATE_FIELDS) {
    const value = opportunity[field];
    if (value !== undefined && value !== null && value !== '') {
      rawDate = value;
      break;
    }
  }
  if (!rawDate) return 'normal';

  const closeDate = new Date(rawDate);
  if (Number.isNaN(closeDate.getTime())) return 'normal';
  const todayMidnight = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const closeMidnight = new Date(closeDate.getFullYear(), closeDate.getMonth(), closeDate.getDate());
  const msPerDay = 24 * 60 * 60 * 1000;
  const diffDays = Math.round((closeMidnight - todayMidnight) / msPerDay);
  if (diffDays < 0) return 'overdue';
  if (diffDays <= 14) return 'due_soon';
  return 'normal';
}

// Small inline badge component used by Kanban card, list row, and detail
// modal header so all three surfaces render the alert with one wording.
function CloseDateAlertBadge({ opportunity, className = '' }) {
  const alert = getCloseDateAlert(opportunity);
  if (alert === 'normal') return null;
  const overdue = alert === 'overdue';
  const text = overdue ? 'Close date overdue' : 'Close date approaching';
  const palette = overdue
    ? 'bg-red-50 text-red-700 border-red-300 dark:bg-red-900/30 dark:text-red-300 dark:border-red-800/40'
    : 'bg-amber-50 text-amber-700 border-amber-300 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-800/40';
  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] sm:text-xs px-2 py-0.5 rounded-full border ${palette} ${className}`}
      title={text}
      data-testid={`close-date-alert-${alert}`}
    >
      <AlertTriangle className="w-3 h-3" />
      {text}
    </span>
  );
}


export default function OpportunityManagement() {
  const { user } = useAuth();
  const [opportunities, setOpportunities] = useState([]);
  const [stats, setStats] = useState({});
  const [loading, setLoading] = useState(true);
  const [initialLoad, setInitialLoad] = useState(true); // FIX 1
  const [view, setView] = useState('kanban'); // 'kanban' or 'list'
  const [showTestOnly, setShowTestOnly] = useState(false);
  const [hideClosedDeals, setHideClosedDeals] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState(''); // FIX 1
  const [filters, setFilters] = useState({
    stage: '',
    priority: '',
    owner: '',
  });

  // Modals
  const [showFormModal, setShowFormModal] = useState(false);
  const [showDetailModal, setShowDetailModal] = useState(false);
  const [selectedOpportunity, setSelectedOpportunity] = useState(null);
  const [editMode, setEditMode] = useState(false);

  // Pagination (list view only)
  const PAGE_SIZE = 50;
  const [listPage, setListPage] = useState(1);

  // View As (admin/director only)
  const [viewAs, setViewAs] = useState(null);
  const [hierarchyData, setHierarchyData] = useState(null);
  const [showViewAsDropdown, setShowViewAsDropdown] = useState(false);
  const viewAsRef = useRef(null);

  // Mobile detection — Kanban falls back to List view below lg
  const [isMobile, setIsMobile] = useState(
    typeof window !== 'undefined' ? window.matchMedia('(max-width: 1023px)').matches : false,
  );
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 1023px)');
    const handler = (e) => setIsMobile(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);
  // On mobile, Kanban isn't useful — force list view rendering
  const effectiveView = isMobile && view === 'kanban' ? 'list' : view;

  // Close View As dropdown on outside click / Escape
  useEffect(() => {
    if (!showViewAsDropdown) return;
    const onClickOutside = (e) => {
      if (viewAsRef.current && !viewAsRef.current.contains(e.target)) {
        setShowViewAsDropdown(false);
      }
    };
    const onKeyDown = (e) => {
      if (e.key === 'Escape') setShowViewAsDropdown(false);
    };
    document.addEventListener('mousedown', onClickOutside);
    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onClickOutside);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [showViewAsDropdown]);

  // Drag and drop
  const [draggedOpp, setDraggedOpp] = useState(null);
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });

  // FIX 2: Debounce search — wait 300ms after user stops typing
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(searchQuery), 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Fetch hierarchy data for View As dropdown (admin/director only)
  useEffect(() => {
    if (user?.role === 'admin' || user?.role === 'director') {
      api
        .get(`${API}/api/opportunities/hierarchy`)
        .then((res) => {
          if (res.ok) res.json().then(setHierarchyData);
        })
        .catch(() => {});
    }
  }, [user]);

  // FIX 3: Depend on debouncedSearch instead of searchQuery
  useEffect(() => {
    setListPage(1);
    loadData();
  }, [showTestOnly, filters, debouncedSearch, viewAs]);

  // Reset page when switching to list view or toggling client-side filters
  // that can shrink the filtered set below the current page
  useEffect(() => {
    setListPage(1);
  }, [view, hideClosedDeals]);

  const loadData = async () => {
    try {
      // FIX 4a: Only show full-page spinner on initial load
      if (initialLoad) setLoading(true);

      // Build query params
      const params = new URLSearchParams();
      if (showTestOnly) params.append('test_only', 'true');
      if (filters.stage) params.append('stage', filters.stage);
      if (filters.priority) params.append('priority', filters.priority);
      if (filters.owner) params.append('owner_id', filters.owner);
      if (debouncedSearch) params.append('search', debouncedSearch);
      if (viewAs) {
        if (viewAs.type === 'user') params.append('view_as_user_id', viewAs.id);
        if (viewAs.type === 'account') params.append('view_as_account_id', viewAs.id);
      }
      params.append('limit', '500');

      const [oppsResponse, statsResponse] = await Promise.all([
        api.get(`${API}/api/opportunities?${params.toString()}`),
        api.get(`${API}/api/opportunities/stats/by-stage`),
      ]);

      // Parse JSON from response objects
      if (oppsResponse.ok && statsResponse.ok) {
        const oppsData = await oppsResponse.json();
        const statsData = await statsResponse.json();

        setOpportunities(oppsData.items || []);
        setStats(statsData || {});
      } else {
        toast.error('Failed to load opportunities');
      }
    } catch (error) {
      console.error('Error loading opportunities:', error);
      toast.error('Failed to load opportunities');
    } finally {
      setLoading(false);
      setInitialLoad(false);
    }
  };

  const handleCreate = () => {
    setSelectedOpportunity(null);
    setEditMode(false);
    setShowFormModal(true);
  };

  const handleEdit = (opp) => {
    setSelectedOpportunity(opp);
    setEditMode(true);
    setShowFormModal(true);
  };

  const handleView = (opp) => {
    setSelectedOpportunity(opp);
    setShowDetailModal(true);
  };

  const handleDelete = (oppId) => {
    setConfirmState({
      open: true,
      title: 'Delete Opportunity',
      message: 'Are you sure you want to delete this opportunity? This action cannot be undone.',
      action: async () => {
        try {
          const response = await api.delete(`${API}/api/opportunities/${oppId}`);
          if (response.ok) {
            toast.success('Opportunity deleted');
            loadData();
          } else {
            const errorData = await response.json();
            toast.error(errorData.detail || 'Failed to delete opportunity');
          }
        } catch (error) {
          console.error('Error deleting opportunity:', error);
          toast.error('Failed to delete opportunity');
        }
      },
    });
  };

  const handleStageChange = async (oppId, newStage) => {
    try {
      const response = await api.patch(`${API}/api/opportunities/${oppId}/stage?stage=${newStage}`);
      if (response.ok) {
        toast.success('Stage updated');
        loadData();
      } else {
        const errorData = await response.json();
        toast.error(errorData.detail || 'Failed to update stage');
      }
    } catch (error) {
      console.error('Error updating stage:', error);
      toast.error('Failed to update stage');
    }
  };

  // Drag and drop handlers
  const handleDragStart = (e, opp) => {
    setDraggedOpp(opp);
    e.dataTransfer.effectAllowed = 'move';
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  };

  const handleDrop = async (e, targetStage) => {
    e.preventDefault();
    if (!draggedOpp || draggedOpp.stage === targetStage) {
      setDraggedOpp(null);
      return;
    }

    await handleStageChange(draggedOpp.opportunity_id, targetStage);
    setDraggedOpp(null);
  };

  const formatCurrency = (amount) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
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

  // Render Kanban Card
  const renderOpportunityCard = (opp) => {
    const priorityConfig = PRIORITY_CONFIG[opp.priority] || PRIORITY_CONFIG.medium;

    return (
      <div
        key={opp.opportunity_id}
        draggable
        onDragStart={(e) => handleDragStart(e, opp)}
        onClick={() => handleView(opp)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            handleView(opp);
          }
        }}
        aria-label={`Open ${opp.name}`}
        data-testid={`opportunity-card-${opp.opportunity_id}`}
        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-3 mb-3 cursor-pointer hover:border-violet-500 transition-all group focus:outline-none focus:ring-2 focus:ring-violet-500/50"
      >
        {/* Header */}
        <div className="flex items-start justify-between mb-2">
          <div className="flex-1 min-w-0">
            <h4 className="font-medium text-aegisaxiom-text text-sm truncate">{opp.name}</h4>
            {opp.tracking_number && (
              <button
                type="button"
                onClick={(e) => copyTrackingNumber(e, opp.tracking_number)}
                title="Click to copy tracking number"
                className="text-[10px] font-mono text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary transition-colors"
                data-testid={`card-tracking-${opp.opportunity_id}`}
              >
                {opp.tracking_number}
              </button>
            )}
            <p className="text-xs text-aegisaxiom-text-secondary truncate">{opp.account_name}</p>
          </div>
          <GripVertical className="w-4 h-4 text-aegisaxiom-text-muted opacity-0 group-hover:opacity-100 flex-shrink-0" />
        </div>

        {/* Amount */}
        <div className="flex items-center gap-1 mb-2">
          <DollarSign className="w-3 h-3 text-green-600 dark:text-green-400" />
          <span className="text-sm font-semibold text-green-600 dark:text-green-400">
            {formatCurrency(opp.amount)}
          </span>
        </div>

        {/* Priority + Close date alert */}
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          <span
            className={`inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border ${priorityConfig.color}`}
          >
            <priorityConfig.Icon className="w-2.5 h-2.5" />
            {priorityConfig.label}
          </span>
          <CloseDateAlertBadge opportunity={opp} />
          {opp.converted_to_deal_id && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-50 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-700/40 flex items-center gap-1">
              <CheckCircle className="w-2.5 h-2.5" />
              Deal
            </span>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between pt-2 border-t border-aegisaxiom-border">
          <div className="flex items-center gap-2 min-w-0">
            <User className="w-3 h-3 text-aegisaxiom-text-secondary shrink-0" />
            <span className="text-xs text-aegisaxiom-text-secondary truncate">
              {opp.opportunity_owner_name}
            </span>
          </div>
          {/* Actions — always visible on touch, hover-only on desktop.
              stopPropagation so action button clicks do not also fire
              the card body click that opens the detail modal. */}
          <div
            className="flex gap-1 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100 transition-opacity shrink-0"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => handleView(opp)}
              aria-label={`View ${opp.name}`}
              className="p-1.5 hover:bg-aegisaxiom-elevated rounded"
              title="View details"
            >
              <Eye className="w-3.5 h-3.5 text-aegisaxiom-text-secondary" />
            </button>
            <button
              onClick={() => handleEdit(opp)}
              aria-label={`Edit ${opp.name}`}
              className="p-1.5 hover:bg-aegisaxiom-elevated rounded"
              title="Edit"
            >
              <Edit className="w-3.5 h-3.5 text-aegisaxiom-text-secondary" />
            </button>
            <button
              onClick={() => handleDelete(opp.opportunity_id)}
              aria-label={`Delete ${opp.name}`}
              className="p-1.5 hover:bg-aegisaxiom-elevated rounded"
              title="Delete"
            >
              <Trash2 className="w-3.5 h-3.5 text-red-600 dark:text-red-400" />
            </button>
          </div>
        </div>

        {/* Notes/Attachments indicators */}
        {(opp.notes?.length > 0 || opp.attachments?.length > 0) && (
          <div className="flex gap-2 mt-2 pt-2 border-t border-aegisaxiom-border">
            {opp.notes?.length > 0 && (
              <div className="flex items-center gap-1 text-xs text-aegisaxiom-text-secondary">
                <MessageSquare className="w-3 h-3" />
                <span>{opp.notes.length}</span>
              </div>
            )}
            {opp.attachments?.length > 0 && (
              <div className="flex items-center gap-1 text-xs text-aegisaxiom-text-secondary">
                <Paperclip className="w-3 h-3" />
                <span>{opp.attachments.length}</span>
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  // Render Kanban View
  const renderKanbanView = () => {
    const stagesToShow = hideClosedDeals
      ? Object.entries(STAGE_CONFIG).filter(
          ([stage]) => stage !== 'closed_won' && stage !== 'closed_lost',
        )
      : Object.entries(STAGE_CONFIG);

    return (
      <div className="overflow-x-auto -mx-6 px-6">
        <div className="flex gap-4 pb-4 min-w-min">
          {stagesToShow.map(([stage, config]) => {
            const stageOpps = opportunities.filter((o) => o.stage === stage);
            const stageStats = stats[stage] || { count: 0, total_amount: 0 };

            return (
              <div
                key={stage}
                onDragOver={handleDragOver}
                onDrop={(e) => handleDrop(e, stage)}
                className="flex-shrink-0 w-80 bg-aegisaxiom-base rounded-lg border border-aegisaxiom-border"
              >
                {/* Stage Header */}
                <div className={`${config.color} ${config.textColor} p-3 rounded-t-lg`}>
                  <h3 className="font-semibold text-sm mb-1">{config.label}</h3>
                  <div className="flex items-center justify-between text-xs opacity-90">
                    <span>{stageStats.count} opportunities</span>
                    <span>{formatCurrency(stageStats.total_amount)}</span>
                  </div>
                </div>

                {/* Cards */}
                <div className="p-3 max-h-[calc(100vh-320px)] overflow-y-auto">
                  {stageOpps.length === 0 ? (
                    <div className="text-center py-8 text-aegisaxiom-text-muted text-sm">
                      No opportunities
                    </div>
                  ) : (
                    stageOpps.map(renderOpportunityCard)
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  // Render List View
  const renderListView = () => {
    const filteredOpportunities = hideClosedDeals
      ? opportunities.filter((opp) => opp.stage !== 'closed_won' && opp.stage !== 'closed_lost')
      : opportunities;

    const totalPages = Math.max(1, Math.ceil(filteredOpportunities.length / PAGE_SIZE));
    const pageStart = (listPage - 1) * PAGE_SIZE;
    const pageEnd = Math.min(pageStart + PAGE_SIZE, filteredOpportunities.length);
    const pagedOpps = filteredOpportunities.slice(pageStart, pageEnd);

    return (
      // List view wrapper. h-full plus min-h-0 plus flex flex-col so
      // the scroll happens on the inner table wrapper through flex-1
      // plus min-h-0 plus overflow-y-auto. The pagination footer sits
      // outside the scroll area with shrink-0 so it stays pinned at
      // the bottom of this section instead of scrolling out of view.
      // The earlier max-h calc 100vh minus 320px attempt was viewport
      // relative and competed with the Layout main scroll, which is
      // why rows ended up cut off below the viewport.
      <div
        data-testid="opportunity-list-view"
        className="h-full min-h-0 flex flex-col"
      >
        {/* Desktop table */}
        <div className="hidden md:flex flex-1 min-h-0 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg overflow-hidden">
          <div
            className="flex-1 min-h-0 overflow-y-auto overflow-x-auto w-full"
            data-testid="opportunity-list-scroll"
          >
            <table className="w-full">
              <thead className="bg-aegisaxiom-surface border-b border-aegisaxiom-border">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                    Tracking #
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                    Opportunity
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                    Account
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                    Stage
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                    Amount
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                    Priority
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                    Owner
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                    Created
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-semibold text-aegisaxiom-text-secondary">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-aegisaxiom-border">
                {pagedOpps.length === 0 ? (
                  <tr>
                    <td colSpan="9" className="px-4 py-8 text-center text-aegisaxiom-text-muted">
                      {hideClosedDeals && opportunities.length > 0
                        ? 'No active opportunities (closed deals are hidden)'
                        : 'No opportunities found'}
                    </td>
                  </tr>
                ) : (
                  pagedOpps.map((opp) => {
                    const stageConfig = STAGE_CONFIG[opp.stage] || STAGE_CONFIG.prospect_opp;
                    const priorityConfig = PRIORITY_CONFIG[opp.priority] || PRIORITY_CONFIG.medium;

                    return (
                      <tr
                        key={opp.opportunity_id}
                        onClick={() => handleView(opp)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            handleView(opp);
                          }
                        }}
                        aria-label={`Open ${opp.name}`}
                        data-testid={`opportunity-row-${opp.opportunity_id}`}
                        className="group hover:bg-aegisaxiom-surface transition-colors cursor-pointer focus:outline-none focus:ring-2 focus:ring-violet-500/50"
                      >
                        <td className="px-4 py-3">
                          {opp.tracking_number ? (
                            <button
                              type="button"
                              onClick={(e) => copyTrackingNumber(e, opp.tracking_number)}
                              title="Click to copy"
                              className="text-xs font-mono text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary transition-colors inline-flex items-center gap-1"
                              data-testid={`row-tracking-${opp.opportunity_id}`}
                            >
                              {opp.tracking_number}
                              <Copy className="w-3 h-3 opacity-0 group-hover:opacity-100" />
                            </button>
                          ) : (
                            <span className="text-xs text-aegisaxiom-text-muted">—</span>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <div className="font-medium text-aegisaxiom-text text-sm">{opp.name}</div>
                          {opp.description && (
                            <div className="text-xs text-aegisaxiom-text-secondary truncate max-w-xs">
                              {opp.description}
                            </div>
                          )}
                          <div className="mt-1">
                            <CloseDateAlertBadge opportunity={opp} />
                          </div>
                          {opp.converted_to_deal_id && (
                            <div className="text-xs text-emerald-600 dark:text-emerald-400 flex items-center gap-1 mt-1">
                              <CheckCircle className="w-3 h-3" />
                              <span>Converted to Deal</span>
                            </div>
                          )}
                        </td>
                        <td className="px-4 py-3 text-sm text-aegisaxiom-text-secondary">
                          {opp.account_name}
                        </td>
                        <td className="px-4 py-3">
                          <span
                            className={`text-xs px-2 py-1 rounded whitespace-nowrap ${stageConfig.color} ${stageConfig.textColor}`}
                          >
                            {stageConfig.label}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-sm font-semibold text-green-600 dark:text-green-400">
                          {formatCurrency(opp.amount)}
                        </td>
                        <td className="px-4 py-3">
                          <span
                            className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded border ${priorityConfig.color}`}
                          >
                            <priorityConfig.Icon className="w-3 h-3" />
                            {priorityConfig.label}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-sm text-aegisaxiom-text-secondary">
                          {opp.opportunity_owner_name}
                        </td>
                        <td className="px-4 py-3 text-sm text-aegisaxiom-text-secondary">
                          {formatDate(opp.created_at)}
                        </td>
                        <td
                          className="px-4 py-3 text-right"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <div className="flex justify-end gap-2">
                            <button
                              onClick={() => handleView(opp)}
                              aria-label={`View ${opp.name}`}
                              className="p-1 hover:bg-aegisaxiom-elevated rounded"
                              title="View"
                            >
                              <Eye className="w-4 h-4 text-aegisaxiom-text-secondary" />
                            </button>
                            <button
                              onClick={() => handleEdit(opp)}
                              aria-label={`Edit ${opp.name}`}
                              className="p-1 hover:bg-aegisaxiom-elevated rounded"
                              title="Edit"
                            >
                              <Edit className="w-4 h-4 text-aegisaxiom-text-secondary" />
                            </button>
                            <button
                              onClick={() => handleDelete(opp.opportunity_id)}
                              aria-label={`Delete ${opp.name}`}
                              className="p-1 hover:bg-aegisaxiom-elevated rounded"
                              title="Delete"
                            >
                              <Trash2 className="w-4 h-4 text-red-600 dark:text-red-400" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Mobile card list. flex-1 plus min-h-0 plus overflow-y-auto
            mirrors the desktop pattern. The list scrolls inside the
            page shell instead of running off the bottom of the
            viewport when many cards are present. */}
        <div
          className="md:hidden flex-1 min-h-0 overflow-y-auto pr-1 space-y-3"
          data-testid="opportunity-list-scroll-mobile"
        >
          {pagedOpps.length === 0 ? (
            <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-6 text-center">
              <Target className="w-10 h-10 text-aegisaxiom-text-muted/60 mx-auto mb-2" />
              <p className="text-sm text-aegisaxiom-text-secondary">
                {hideClosedDeals && opportunities.length > 0
                  ? 'No active opportunities (closed deals are hidden)'
                  : 'No opportunities found'}
              </p>
            </div>
          ) : (
            pagedOpps.map((opp) => {
              const stageConfig = STAGE_CONFIG[opp.stage] || STAGE_CONFIG.prospect_opp;
              const priorityConfig = PRIORITY_CONFIG[opp.priority] || PRIORITY_CONFIG.medium;

              return (
                <div
                  key={opp.opportunity_id}
                  onClick={() => handleView(opp)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      handleView(opp);
                    }
                  }}
                  className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4 hover:border-violet-500 transition-colors cursor-pointer focus:outline-none focus:ring-2 focus:ring-violet-500/50"
                >
                  {/* Top row — name + amount */}
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <div className="min-w-0 flex-1">
                      <h4 className="font-medium text-aegisaxiom-text text-sm truncate">{opp.name}</h4>
                      {opp.tracking_number && (
                        <p className="text-[10px] font-mono text-aegisaxiom-text-muted truncate">
                          {opp.tracking_number}
                        </p>
                      )}
                      <p className="text-xs text-aegisaxiom-text-secondary truncate mt-0.5">
                        <Building2 className="w-3 h-3 inline-block mr-1 -mt-0.5" />
                        {opp.account_name}
                      </p>
                    </div>
                    <div className="text-sm font-semibold text-green-600 dark:text-green-400 shrink-0">
                      {formatCurrency(opp.amount)}
                    </div>
                  </div>

                  {/* Badges row */}
                  <div className="flex items-center gap-2 flex-wrap mb-3">
                    <span
                      className={`text-[11px] px-2 py-0.5 rounded whitespace-nowrap ${stageConfig.color} ${stageConfig.textColor}`}
                    >
                      {stageConfig.label}
                    </span>
                    <span
                      className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded border ${priorityConfig.color}`}
                    >
                      <priorityConfig.Icon className="w-3 h-3" />
                      {priorityConfig.label}
                    </span>
                    <CloseDateAlertBadge opportunity={opp} />
                    {opp.converted_to_deal_id && (
                      <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded bg-emerald-50 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-700/40">
                        <CheckCircle className="w-3 h-3" />
                        Deal
                      </span>
                    )}
                  </div>

                  {/* Footer — owner + actions */}
                  <div className="flex items-center justify-between pt-2 border-t border-aegisaxiom-border">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <User className="w-3 h-3 text-aegisaxiom-text-secondary shrink-0" />
                      <span className="text-xs text-aegisaxiom-text-secondary truncate">
                        {opp.opportunity_owner_name}
                      </span>
                    </div>
                    <div className="flex gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                      <button
                        onClick={() => handleEdit(opp)}
                        aria-label={`Edit ${opp.name}`}
                        className="p-2 hover:bg-aegisaxiom-elevated rounded"
                        title="Edit"
                      >
                        <Edit className="w-4 h-4 text-aegisaxiom-text-secondary" />
                      </button>
                      <button
                        onClick={() => handleDelete(opp.opportunity_id)}
                        aria-label={`Delete ${opp.name}`}
                        className="p-2 hover:bg-aegisaxiom-elevated rounded"
                        title="Delete"
                      >
                        <Trash2 className="w-4 h-4 text-red-600 dark:text-red-400" />
                      </button>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
        {/* Pagination. shrink-0 so the footer stays pinned at the
            bottom of the list view rather than getting pushed below
            the scroll area when row counts grow. */}
        {filteredOpportunities.length > PAGE_SIZE && (
          <div className="shrink-0 mt-4 flex items-center justify-between text-sm">
            <span className="text-aegisaxiom-text-secondary">
              Showing {pageStart + 1}–{pageEnd} of {filteredOpportunities.length}
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setListPage((p) => Math.max(1, p - 1))}
                disabled={listPage === 1}
                className="px-3 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated hover:text-aegisaxiom-text transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Previous
              </button>
              <span className="text-aegisaxiom-text-secondary">
                Page {listPage} of {totalPages}
              </span>
              <button
                onClick={() => setListPage((p) => Math.min(totalPages, p + 1))}
                disabled={listPage === totalPages}
                className="px-3 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated hover:text-aegisaxiom-text transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    );
  };

  // FIX 4b: Only unmount page for spinner on initial load
  if (loading && initialLoad) {
    return (
      <div className="min-h-screen bg-aegisaxiom-base flex items-center justify-center">
        <div className="w-10 h-10 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    // Page root. h-full plus min-h-0 plus flex flex-col is the canonical
    // pattern for a page that lives inside the Layout shell. The shell
    // gives main a deterministic height through its own flex chain, so
    // h-full here resolves to that height and the flex children can
    // share it. The earlier min-h-screen made the page at least
    // viewport tall inside an already scrollable main, which let the
    // outer scroll fight any inner scroll cap and left rows trapped
    // below the viewport when the list grew.
    <div className="h-full min-h-0 flex flex-col bg-aegisaxiom-base text-aegisaxiom-text">
      {/* Header + toolbar + view-as banner. Locked above the scroll
          area with shrink-0 so the toolbar stays pinned while the list
          below scrolls. Padding kept on this section so the page top
          spacing matches the prior look. */}
      <div className="shrink-0 px-4 sm:px-6 pt-4 sm:pt-6">
      {/* Header */}
      <div className="mb-5 sm:mb-6">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 mb-4">
          <div className="min-w-0">
            <h1 className="text-xl sm:text-2xl font-bold text-aegisaxiom-text">Opportunity Management</h1>
            <p className="text-aegisaxiom-text-secondary text-xs sm:text-sm mt-1">
              Track sales opportunities from prospect to close
            </p>
          </div>
          <button
            onClick={handleCreate}
            className="flex items-center justify-center gap-2 px-4 py-2.5 bg-violet-600 hover:bg-violet-700 text-white rounded-lg transition-colors text-sm font-medium w-full sm:w-auto shrink-0"
          >
            <Plus className="w-4 h-4" />
            <span>New Opportunity</span>
          </button>
        </div>

        {/* Toolbar */}
        <div className="flex flex-col sm:flex-row sm:flex-wrap sm:items-center gap-3">
          {/* View Toggle — disabled on mobile (kanban unusable there) */}
          <div className="flex bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg overflow-hidden self-start">
            <button
              onClick={() => setView('kanban')}
              aria-pressed={view === 'kanban' && !isMobile}
              disabled={isMobile}
              title={isMobile ? 'Kanban view available on larger screens' : 'Kanban view'}
              className={`flex items-center gap-2 px-3 sm:px-4 py-2 text-sm transition-colors ${
                view === 'kanban' && !isMobile
                  ? 'bg-violet-600 text-white'
                  : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text disabled:opacity-50'
              }`}
            >
              <LayoutGrid className="w-4 h-4" />
              <span>Kanban</span>
            </button>
            <button
              onClick={() => setView('list')}
              aria-pressed={view === 'list' || isMobile}
              className={`flex items-center gap-2 px-3 sm:px-4 py-2 text-sm transition-colors ${
                view === 'list' || isMobile
                  ? 'bg-violet-600 text-white'
                  : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
              }`}
            >
              <List className="w-4 h-4" />
              <span>List</span>
            </button>
          </div>

          {/* Search */}
          <div className="flex-1 sm:min-w-[200px] sm:max-w-md relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-aegisaxiom-text-muted pointer-events-none" />
            <input
              type="text"
              placeholder="Search opportunities..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              aria-label="Search opportunities"
              className="w-full pl-10 pr-9 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 text-sm"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                aria-label="Clear search"
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded hover:bg-aegisaxiom-elevated"
              >
                <X className="w-3.5 h-3.5 text-aegisaxiom-text-secondary" />
              </button>
            )}
          </div>

          {/* Filter buttons wrap to a row of their own on tight screens */}
          <div className="flex flex-wrap gap-2">
            {/* Test Data Filter */}
            <button
              onClick={() => setShowTestOnly(!showTestOnly)}
              aria-pressed={showTestOnly}
              className={`flex items-center gap-2 px-3 sm:px-4 py-2 rounded-lg border transition-colors text-sm ${
                showTestOnly
                  ? 'bg-amber-50 dark:bg-amber-600/20 border-amber-600 text-amber-700 dark:text-amber-300'
                  : 'bg-aegisaxiom-base border-aegisaxiom-border text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
              }`}
            >
              <TestTube className="w-4 h-4" />
              <span className="hidden sm:inline">Test Data Only</span>
              <span className="sm:hidden">Test Only</span>
            </button>

            {/* Hide Closed Deals Toggle */}
            <button
              onClick={() => setHideClosedDeals(!hideClosedDeals)}
              aria-pressed={hideClosedDeals}
              className={`flex items-center gap-2 px-3 sm:px-4 py-2 rounded-lg border transition-colors text-sm ${
                hideClosedDeals
                  ? 'bg-emerald-50 dark:bg-emerald-600/20 border-emerald-600 text-emerald-700 dark:text-emerald-300'
                  : 'bg-aegisaxiom-base border-aegisaxiom-border text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
              }`}
            >
              <CheckCircle className="w-4 h-4" />
              <span className="hidden sm:inline">Hide Closed Deals</span>
              <span className="sm:hidden">Hide Closed</span>
            </button>

            {/* View As Dropdown (admin/director only) */}
            {(user?.role === 'admin' || user?.role === 'director') && hierarchyData && (
              <div className="relative" ref={viewAsRef}>
                <button
                  onClick={() => setShowViewAsDropdown(!showViewAsDropdown)}
                  aria-expanded={showViewAsDropdown}
                  aria-haspopup="menu"
                  className={`flex items-center gap-2 px-3 sm:px-4 py-2 rounded-lg border transition-colors text-sm ${
                    viewAs
                      ? 'bg-violet-50 dark:bg-violet-600/20 border-violet-500 text-violet-700 dark:text-violet-300'
                      : 'bg-aegisaxiom-base border-aegisaxiom-border text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
                  }`}
                >
                  <Users className="w-4 h-4" />
                  <span>{viewAs ? 'Viewing As...' : 'View As'}</span>
                  <ChevronDown
                    className={`w-3 h-3 transition-transform ${showViewAsDropdown ? 'rotate-180' : ''}`}
                  />
                </button>
                {showViewAsDropdown && (
                  <div
                    role="menu"
                    className="absolute left-0 sm:right-0 sm:left-auto top-full mt-1 w-[min(calc(100vw-2rem),18rem)] bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg shadow-xl z-50 max-h-80 overflow-y-auto"
                  >
                    {viewAs && (
                      <button
                        onClick={() => {
                          setViewAs(null);
                          setShowViewAsDropdown(false);
                        }}
                        className="w-full text-left px-4 py-2 text-sm text-red-600 dark:text-red-400 hover:bg-aegisaxiom-elevated border-b border-aegisaxiom-border"
                      >
                        Clear View As
                      </button>
                    )}

                    {hierarchyData.opportunity_owners?.length > 0 && (
                      <>
                        <div className="px-4 py-2 text-xs font-semibold text-aegisaxiom-text-muted uppercase tracking-wide bg-aegisaxiom-elevated sticky top-0">
                          Opportunity Owners
                        </div>
                        {hierarchyData.opportunity_owners.map((o) => (
                          <button
                            key={`opp-${o.user_id}`}
                            onClick={() => {
                              setViewAs({ type: 'user', id: o.user_id, label: o.name });
                              setShowViewAsDropdown(false);
                            }}
                            className={`w-full text-left px-4 py-2 text-sm hover:bg-aegisaxiom-elevated flex items-center justify-between ${
                              viewAs?.type === 'user' && viewAs?.id === o.user_id
                                ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300'
                                : 'text-aegisaxiom-text-secondary'
                            }`}
                          >
                            <span>
                              {o.name}{' '}
                              <span className="text-aegisaxiom-text-muted text-xs">({o.role})</span>
                            </span>
                            <span className="text-xs text-aegisaxiom-text-muted">{o.count} opps</span>
                          </button>
                        ))}
                      </>
                    )}

                    {hierarchyData.deal_owners?.length > 0 && (
                      <>
                        <div className="px-4 py-2 text-xs font-semibold text-aegisaxiom-text-muted uppercase tracking-wide bg-aegisaxiom-elevated sticky top-0 border-t border-aegisaxiom-border">
                          Deal Owners
                        </div>
                        {hierarchyData.deal_owners.map((d) => (
                          <button
                            key={`deal-${d.user_id}`}
                            onClick={() => {
                              setViewAs({ type: 'user', id: d.user_id, label: d.name });
                              setShowViewAsDropdown(false);
                            }}
                            className={`w-full text-left px-4 py-2 text-sm hover:bg-aegisaxiom-elevated flex items-center justify-between ${
                              viewAs?.type === 'user' && viewAs?.id === d.user_id
                                ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300'
                                : 'text-aegisaxiom-text-secondary'
                            }`}
                          >
                            <span>
                              {d.name}{' '}
                              <span className="text-aegisaxiom-text-muted text-xs">({d.role})</span>
                            </span>
                            <span className="text-xs text-aegisaxiom-text-muted">{d.count} deals</span>
                          </button>
                        ))}
                      </>
                    )}

                    {hierarchyData.accounts?.length > 0 && (
                      <>
                        <div className="px-4 py-2 text-xs font-semibold text-aegisaxiom-text-muted uppercase tracking-wide bg-aegisaxiom-elevated sticky top-0 border-t border-aegisaxiom-border">
                          Accounts
                        </div>
                        {hierarchyData.accounts.map((a) => (
                          <button
                            key={`acct-${a.account_id}`}
                            onClick={() => {
                              setViewAs({ type: 'account', id: a.account_id, label: a.name });
                              setShowViewAsDropdown(false);
                            }}
                            className={`w-full text-left px-4 py-2 text-sm hover:bg-aegisaxiom-elevated flex items-center justify-between ${
                              viewAs?.type === 'account' && viewAs?.id === a.account_id
                                ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300'
                                : 'text-aegisaxiom-text-secondary'
                            }`}
                          >
                            <span>{a.name}</span>
                            <span className="text-xs text-aegisaxiom-text-muted">{a.count} opps</span>
                          </button>
                        ))}
                      </>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* View As Banner */}
      {viewAs && (
        <div className="mb-4 flex items-center gap-3 bg-violet-50 dark:bg-violet-600/15 border border-violet-500/40 rounded-lg px-4 py-2.5">
          <Users className="w-4 h-4 text-violet-600 dark:text-violet-400 flex-shrink-0" />
          <span className="text-sm text-violet-700 dark:text-violet-300">
            Viewing as: <strong className="text-violet-200">{viewAs.label}</strong>
            <span className="text-violet-600 dark:text-violet-400/70 ml-1">
              ({viewAs.type === 'user' ? 'User' : 'Account'})
            </span>
          </span>
          <button
            onClick={() => setViewAs(null)}
            className="ml-auto p-1 hover:bg-violet-50 dark:bg-violet-500/20 rounded transition-colors"
            title="Clear View As"
          >
            <X className="w-4 h-4 text-violet-600 dark:text-violet-400" />
          </button>
        </div>
      )}

      </div>
      {/* Content area. flex-1 plus min-h-0 lets this child shrink
          below its intrinsic content size so an internal overflow-auto
          actually clips and scrolls. Without min-h-0 the flex child
          would grow to fit its content and the overflow inside would
          never trigger. The renderListView output puts its own
          flex-col structure inside so the table scrolls and the
          pagination footer stays pinned at the bottom of this area. */}
      <div className="flex-1 min-h-0 px-4 sm:px-6 pb-4 sm:pb-6 flex flex-col">
        {effectiveView === 'kanban' ? renderKanbanView() : renderListView()}
      </div>

      {/* Modals */}
      {showFormModal && (
        <OpportunityFormModal
          opportunity={selectedOpportunity}
          editMode={editMode}
          testOnlyMode={showTestOnly}
          onClose={() => {
            setShowFormModal(false);
            setSelectedOpportunity(null);
          }}
          onSave={() => {
            setShowFormModal(false);
            setSelectedOpportunity(null);
            loadData();
          }}
        />
      )}

      {showDetailModal && (
        <OpportunityDetailModal
          opportunity={selectedOpportunity}
          onClose={() => {
            setShowDetailModal(false);
            setSelectedOpportunity(null);
          }}
          onEdit={(opp) => {
            setShowDetailModal(false);
            handleEdit(opp);
          }}
          onDelete={(oppId) => {
            setShowDetailModal(false);
            handleDelete(oppId);
          }}
          onUpdate={loadData}
        />
      )}
      <ConfirmModal
        isOpen={confirmState.open}
        title={confirmState.title}
        message={confirmState.message}
        confirmText="Delete"
        onConfirm={() => confirmState.action?.()}
        onCancel={() => setConfirmState({ open: false, action: null, title: '', message: '' })}
      />
    </div>
  );
}
