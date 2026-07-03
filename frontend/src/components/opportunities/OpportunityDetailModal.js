import React, { useState, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { api, apiDownload } from '@/lib/api';
import API_URL from '../../lib/apiConfig';
import { useNavigate } from 'react-router-dom';
import { usePermissions } from '../../hooks/usePermissions';
import ConfirmModal from '../shared/ConfirmModal';
import { getCloseDateAlert } from '../OpportunityManagement';
import {
  X,
  Edit,
  Trash2,
  Building2,
  User,
  Calendar,
  DollarSign,
  Flag,
  Briefcase,
  FileText,
  MessageSquare,
  Paperclip,
  Plus,
  Download,
  Upload,
  Send,
  CheckCircle,
  ArrowRight,
  AlertCircle,
  TrendingUp,
  Clock,
  ExternalLink,
  Package,
  Flame,
  AlertTriangle,
  Info,
  Copy,
} from 'lucide-react';

// Copy tracking number (OPP-YYYY-NNNNNN) to the clipboard. navigator.clipboard.writeText
// returns a Promise — await it so permission/insecure-context rejections surface as a
// toast instead of being silently swallowed by a sync try/catch.
const copyTrackingNumber = async (tn) => {
  if (!tn) return;
  try {
    await navigator.clipboard.writeText(tn);
    toast.success(`Copied ${tn}`);
  } catch {
    toast.error('Could not copy to clipboard');
  }
};

const API = API_URL;

const STAGE_CONFIG = {
  prospect_opp: { label: 'Prospect Opportunity', color: 'bg-aegisaxiom-elevated' },
  prospect_lead: { label: 'Prospect Lead', color: 'bg-blue-500' },
  proposal_development: { label: 'Proposal Development', color: 'bg-indigo-500' },
  scoping_complete: { label: 'Scoping Complete', color: 'bg-purple-500' },
  proposal_sent: { label: 'Proposal Sent', color: 'bg-violet-500' },
  negotiation_review: { label: 'Negotiation/Review', color: 'bg-fuchsia-500' },
  verbal_agreement: { label: 'Verbal Agreement', color: 'bg-pink-500' },
  closed_won: { label: 'Closed Won', color: 'bg-green-500' },
  closed_lost: { label: 'Closed Lost', color: 'bg-red-500' },
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

const SOW_STATUS_CONFIG = {
  draft: {
    label: 'Draft',
    color: 'bg-aegisaxiom-elevated/20 text-aegisaxiom-text-secondary border-aegisaxiom-border-hover/30',
  },
  in_progress: {
    label: 'In Progress',
    color: 'bg-blue-50 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300 border-blue-500/30',
  },
  ready_to_submit: {
    label: 'Ready to Submit',
    color:
      'bg-amber-50 dark:bg-amber-500/20 text-amber-700 dark:text-amber-300 border-amber-500/30',
  },
  submitted: {
    label: 'Submitted',
    color:
      'bg-violet-50 dark:bg-violet-500/20 text-violet-700 dark:text-violet-300 border-violet-500/30',
  },
  complete: {
    label: 'Complete',
    color:
      'bg-green-50 dark:bg-green-500/20 text-green-700 dark:text-green-300 border-green-500/30',
  },
};

const LOB_CONFIG = {
  professional_services: {
    label: 'Professional Services',
    color: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
  },
  field_services: {
    label: 'Field Services',
    color: 'bg-blue-50 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300 border-blue-500/30',
  },
  referral_mrr: {
    label: 'Referral MRR',
    color:
      'bg-amber-50 dark:bg-amber-500/20 text-amber-700 dark:text-amber-300 border-amber-500/30',
  },
  wholesale_mrr: {
    label: 'Wholesale MRR',
    color:
      'bg-green-50 dark:bg-green-500/20 text-green-700 dark:text-green-300 border-green-500/30',
  },
};

export default function OpportunityDetailModal({
  opportunity,
  onClose,
  onEdit,
  onDelete,
  onUpdate,
}) {
  const navigate = useNavigate();
  const { hasPermission } = usePermissions();
  const canCreateRequest = hasPermission('hardware_material_requests.create');
  const [activeTab, setActiveTab] = useState('details'); // details, notes, procurement, attachments
  const [procurementRequests, setProcurementRequests] = useState([]);
  const [procurementDistributors, setProcurementDistributors] = useState([]);
  const [procurementProducts, setProcurementProducts] = useState([]);
  const [newRequest, setNewRequest] = useState({
    distributor_id: '',
    product_id: '',
    item_description: '',
    quantity: 1,
    needed_by_date: '',
    priority: 'medium',
    notes: '',
  });
  const [creatingRequest, setCreatingRequest] = useState(false);

  // Escape to close + body scroll lock.
  // Ref avoids re-running effect when parent passes a new onClose ref each render
  // (which would otherwise corrupt the prev-overflow snapshot).
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === 'Escape') onCloseRef.current?.();
    };
    window.addEventListener('keydown', onKeyDown);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = prev;
    };
  }, []);
  const [noteText, setNoteText] = useState('');
  const [uploading, setUploading] = useState(false);
  const [converting, setConverting] = useState(false);
  const [sowsData, setSOWsData] = useState([]); // NEW: Store SOW data for each LOB
  const [alertsData, setAlertsData] = useState([]); // NEW: Store alerts for this opportunity
  const [loadingSOWs, setLoadingSOWs] = useState(false);
  const [localOpportunity, setLocalOpportunity] = useState(opportunity);
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
    confirmText: 'Confirm',
  });

  // Resync local copy only when the parent switches to a different opportunity.
  // Depending on the full prop object would cause a race with refreshOpportunity:
  // after a mutation we setLocalOpportunity(fresh), then onUpdate?.() triggers a
  // parent refetch, the parent passes a new prop reference, and this effect
  // would clobber our fresh data with the parent's (potentially older) copy.
  React.useEffect(() => {
    setLocalOpportunity(opportunity);
  }, [opportunity?.opportunity_id]);

  const refreshOpportunity = async () => {
    try {
      const res = await api.get(`${API}/api/opportunities/${opportunity.opportunity_id}`);
      if (res.ok) {
        const fresh = await res.json();
        setLocalOpportunity(fresh);
      } else {
        toast.error('Failed to refresh opportunity data');
      }
    } catch (error) {
      console.error('Error refreshing opportunity:', error);
      toast.error('Failed to refresh opportunity data');
    }
    // Always notify parent — the mutation already succeeded server-side,
    // so the list view should refresh regardless of whether our local refetch worked.
    // Gating this on refetch success would leave the list stale whenever only
    // the in-modal refresh fails.
    onUpdate?.();
  };

  // NEW: Load SOW and alerts data for the opportunity
  React.useEffect(() => {
    if (opportunity?.opportunity_id) {
      loadSOWsAndAlerts();
    }
  }, [opportunity?.opportunity_id]);

  // Procurement tab: load requests + dropdown options when opened.
  useEffect(() => {
    if (activeTab !== 'procurement' || !opportunity?.opportunity_id) return;

    const loadProcurement = async () => {
      try {
        const [reqRes, distRes, hwRes, matRes] = await Promise.all([
          api.get(
            `${API}/api/sales-ops/hardware-material-requests?opportunity_id=${opportunity.opportunity_id}`,
          ),
          api.get(`${API}/api/admin/distributors`),
          api.get(`${API}/api/admin/products?item_category=hardware`),
          api.get(`${API}/api/admin/products?item_category=materials`),
        ]);
        if (reqRes.ok) {
          const data = await reqRes.json();
          setProcurementRequests(data.items || []);
        }
        if (distRes.ok) {
          const data = await distRes.json();
          setProcurementDistributors(data.items || []);
        }
        const products = [];
        if (hwRes.ok) {
          const data = await hwRes.json();
          products.push(...(data.products || []));
        }
        if (matRes.ok) {
          const data = await matRes.json();
          products.push(...(data.products || []));
        }
        setProcurementProducts(products);
      } catch (e) {
        console.error('Error loading procurement data:', e);
      }
    };
    loadProcurement();
  }, [activeTab, opportunity?.opportunity_id]);

  const handleCreateRequest = async () => {
    if (!newRequest.product_id && !newRequest.item_description.trim()) {
      toast.error('Pick a product or enter an item description.');
      return;
    }
    try {
      setCreatingRequest(true);
      const payload = {
        opportunity_id: opportunity.opportunity_id,
        distributor_id: newRequest.distributor_id || null,
        product_id: newRequest.product_id || null,
        item_description: newRequest.item_description || null,
        quantity: parseInt(newRequest.quantity, 10) || 1,
        needed_by_date: newRequest.needed_by_date || null,
        priority: newRequest.priority,
        notes: newRequest.notes || null,
        status: 'open',
      };
      const res = await api.post(
        `${API}/api/sales-ops/hardware-material-requests`,
        payload,
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || 'Failed to create request');
      }
      toast.success('Hardware/material request created');
      setNewRequest({
        distributor_id: '',
        product_id: '',
        item_description: '',
        quantity: 1,
        needed_by_date: '',
        priority: 'medium',
        notes: '',
      });
      // Refresh list
      const listRes = await api.get(
        `${API}/api/sales-ops/hardware-material-requests?opportunity_id=${opportunity.opportunity_id}`,
      );
      if (listRes.ok) {
        const data = await listRes.json();
        setProcurementRequests(data.items || []);
      }
    } catch (e) {
      toast.error(e.message);
    } finally {
      setCreatingRequest(false);
    }
  };

  const loadSOWsAndAlerts = async () => {
    try {
      setLoadingSOWs(true);
      const [sowsResponse, alertsResponse] = await Promise.all([
        api.get(`${API}/api/sales-ops/opportunities/${opportunity.opportunity_id}/sows/all`),
        api.get(`${API}/api/sales-ops/opportunities/${opportunity.opportunity_id}/alerts`),
      ]);

      if (sowsResponse.ok) {
        const sowsData = await sowsResponse.json();
        setSOWsData(sowsData.sows || []);
      }

      if (alertsResponse.ok) {
        const alertsData = await alertsResponse.json();
        setAlertsData(alertsData.items || []);
      }
    } catch (error) {
      console.error('Error loading SOWs and alerts:', error);
    } finally {
      setLoadingSOWs(false);
    }
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

  const formatDateTime = (dateString) => {
    if (!dateString) return 'Unknown';
    return new Date(dateString).toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  };

  const handleAddNote = async () => {
    if (!noteText.trim()) {
      toast.error('Please enter a note');
      return;
    }

    try {
      const response = await api.post(
        `${API}/api/opportunities/${opportunity.opportunity_id}/notes?note_text=${encodeURIComponent(noteText)}`,
      );
      if (response.ok) {
        toast.success('Note added');
        setNoteText('');
        refreshOpportunity();
      } else {
        const errorData = await response.json();
        toast.error(errorData.detail || 'Failed to add note');
      }
    } catch (error) {
      console.error('Error adding note:', error);
      toast.error('Failed to add note');
    }
  };

  const handleDeleteNote = (noteId) => {
    setConfirmState({
      open: true,
      title: 'Delete Note',
      message: 'Delete this note? This action cannot be undone.',
      confirmText: 'Delete',
      action: async () => {
        try {
          const response = await api.delete(
            `${API}/api/opportunities/${opportunity.opportunity_id}/notes/${noteId}`,
          );
          if (response.ok) {
            toast.success('Note deleted');
            refreshOpportunity();
          } else {
            toast.error('Failed to delete note');
          }
        } catch (error) {
          console.error('Error deleting note:', error);
          toast.error('Failed to delete note');
        }
      },
    });
  };

  const handleFileUpload = async (e) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;

    setUploading(true);
    let uploaded = 0;
    try {
      for (const file of files) {
        // Check file size (max 10MB)
        if (file.size > 10 * 1024 * 1024) {
          toast.error(`${file.name} exceeds 10MB — skipped`);
          continue;
        }

        const formData = new FormData();
        formData.append('file', file);

        // Use api.postForm (apiFormRequest under the hood) so uploads go through
        // the XHR path that tolerates emergent-main.js patching of fetch/XHR.
        const res = await api.postForm(
          `${API}/api/opportunities/${opportunity.opportunity_id}/attachments`,
          formData,
        );

        if (res.ok) {
          uploaded += 1;
        } else {
          const errorData = await res.json().catch(() => ({}));
          toast.error(errorData.detail || `Failed to upload ${file.name}`);
        }
      }
      if (uploaded > 0) {
        toast.success(uploaded > 1 ? `${uploaded} files uploaded` : 'File uploaded');
        refreshOpportunity();
      }
    } catch (error) {
      console.error('Error uploading file:', error);
      toast.error('Failed to upload file');
    } finally {
      setUploading(false);
      e.target.value = ''; // Reset file input
    }
  };

  const handleDeleteAttachment = (attachmentId) => {
    setConfirmState({
      open: true,
      title: 'Delete Attachment',
      message: 'Delete this attachment? This action cannot be undone.',
      confirmText: 'Delete',
      action: async () => {
        try {
          const response = await api.delete(
            `${API}/api/opportunities/${opportunity.opportunity_id}/attachments/${attachmentId}`,
          );
          if (response.ok) {
            toast.success('Attachment deleted');
            refreshOpportunity();
          } else {
            toast.error('Failed to delete attachment');
          }
        } catch (error) {
          console.error('Error deleting attachment:', error);
          toast.error('Failed to delete attachment');
        }
      },
    });
  };

  const handleViewAttachment = async (attachment) => {
    // Claim the new tab synchronously, inside the click handler, so the
    // browser treats it as user-initiated. Opening it after the `await`
    // below would otherwise be caught by the popup blocker.
    const win = window.open('', '_blank');
    try {
      const blob = await apiDownload(
        `${API}/api/opportunities/${opportunity.opportunity_id}/attachments/${attachment.attachment_id}/download`,
      );
      if (!blob) {
        win?.close();
        toast.error('Failed to open attachment');
        return;
      }
      const url = URL.createObjectURL(blob);
      if (win) {
        win.location = url;
      } else {
        toast.error('Allow pop-ups to view attachments');
      }
      // Release the object URL once the tab has had time to load it.
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (error) {
      win?.close();
      console.error('Error opening attachment:', error);
      toast.error('Failed to open attachment');
    }
  };

  const handleConvertToDeal = () => {
    setConfirmState({
      open: true,
      title: 'Convert to Deal',
      message:
        'Convert this Closed Won opportunity to a Deal?\n\nThis will:\n• Create a new Deal in Deal Management\n• Transfer all opportunity data\n• Mark this opportunity as converted\n\nYou will then need to assign a contract and commit commission.',
      confirmText: 'Convert',
      action: async () => {
        setConverting(true);
        try {
          const response = await api.post(
            `${API}/api/opportunities/${opportunity.opportunity_id}/convert-to-deal`,
          );

          if (response.ok) {
            toast.success('Deal created! Redirecting to Deal Management...');
            onClose();
            setTimeout(() => {
              navigate('/deal-management');
            }, 1500);
          } else {
            const errorData = await response.json();
            toast.error(errorData.detail || 'Failed to convert to deal');
          }
        } catch (error) {
          console.error('Error converting to deal:', error);
          toast.error('Failed to convert to deal');
        } finally {
          setConverting(false);
        }
      },
    });
  };

  // Use localOpportunity for all rendering and edit pre-fill so post-mutation
  // state (new notes, attachments) is reflected. Delete still uses the stable
  // ID from the original prop.
  const opp = localOpportunity;
  const stageConfig = STAGE_CONFIG[opp.stage] || STAGE_CONFIG.prospect_opp;
  const priorityConfig = PRIORITY_CONFIG[opp.priority] || PRIORITY_CONFIG.medium;

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`Opportunity: ${opp.name}`}
      onClick={onClose}
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border w-full h-full sm:rounded-lg sm:max-w-4xl sm:max-h-[90vh] sm:h-auto overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between p-4 sm:p-6 border-b border-aegisaxiom-border shrink-0">
          <div className="flex-1 min-w-0 mr-3 sm:mr-4">
            <h2 className="text-lg sm:text-xl font-bold text-aegisaxiom-text mb-2 truncate">
              {opp.name}
            </h2>
            <div className="flex items-center gap-2 flex-wrap">
              {opp.tracking_number && (
                <button
                  type="button"
                  onClick={() => copyTrackingNumber(opp.tracking_number)}
                  title="Click to copy tracking number"
                  className="group inline-flex items-center gap-1 text-xs font-mono px-2 py-1 rounded bg-aegisaxiom-elevated text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-surface border border-aegisaxiom-border transition-colors"
                  data-testid="tracking-number-badge"
                >
                  <span>{opp.tracking_number}</span>
                  <Copy className="w-3 h-3 opacity-60 group-hover:opacity-100" />
                </button>
              )}
              <span className={`text-xs px-2 py-1 rounded ${stageConfig.color} text-aegisaxiom-text`}>
                {stageConfig.label}
              </span>
              <span
                className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded border ${priorityConfig.color}`}
              >
                <priorityConfig.Icon className="w-3 h-3" />
                {priorityConfig.label}
              </span>
              {(() => {
                const alert = getCloseDateAlert(opp);
                if (alert === 'normal') return null;
                const overdue = alert === 'overdue';
                const label = overdue ? 'Close date overdue' : 'Close date approaching';
                const palette = overdue
                  ? 'bg-red-50 text-red-700 border-red-300 dark:bg-red-900/30 dark:text-red-300 dark:border-red-800/40'
                  : 'bg-amber-50 text-amber-700 border-amber-300 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-800/40';
                return (
                  <span
                    className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded border ${palette}`}
                    title={label}
                    data-testid={`detail-close-date-alert-${alert}`}
                  >
                    <AlertTriangle className="w-3 h-3" />
                    {label}
                  </span>
                );
              })()}
              {opp.is_test_data && (
                <span className="text-xs px-2 py-1 rounded bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border border-amber-600">
                  Test Data
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {/* Convert to Deal Button - Show only for Closed Won opportunities that haven't been converted */}
            {opp.stage === 'closed_won' && !opp.converted_to_deal_id && (
              <button
                onClick={handleConvertToDeal}
                disabled={converting}
                className="flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                title="Convert to Deal"
              >
                <CheckCircle className="w-4 h-4" />
                {converting ? 'Converting...' : 'Convert to Deal'}
                <ArrowRight className="w-4 h-4" />
              </button>
            )}

            {/* Show "Already Converted" badge if converted */}
            {opp.converted_to_deal_id && (
              <div className="flex items-center gap-2 px-3 py-2 bg-emerald-50 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400 text-xs rounded-lg border border-emerald-200 dark:border-emerald-700/40">
                <CheckCircle className="w-4 h-4" />
                Converted to Deal
              </div>
            )}

            <button
              onClick={() => onEdit(opp)}
              className="p-2 hover:bg-aegisaxiom-elevated rounded-lg transition-colors"
              title="Edit"
            >
              <Edit className="w-5 h-5 text-aegisaxiom-text-secondary" />
            </button>
            <button
              onClick={() => onDelete(opportunity.opportunity_id)}
              className="p-2 hover:bg-aegisaxiom-elevated rounded-lg transition-colors"
              title="Delete"
            >
              <Trash2 className="w-5 h-5 text-red-600 dark:text-red-400" />
            </button>
            <button
              onClick={onClose}
              className="p-2 hover:bg-aegisaxiom-elevated rounded-lg transition-colors"
            >
              <X className="w-5 h-5 text-aegisaxiom-text-secondary" />
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-aegisaxiom-border px-6">
          <button
            onClick={() => setActiveTab('details')}
            className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
              activeTab === 'details'
                ? 'border-violet-500 text-aegisaxiom-text'
                : 'border-transparent text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
            }`}
          >
            Details
          </button>
          <button
            onClick={() => setActiveTab('notes')}
            className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
              activeTab === 'notes'
                ? 'border-violet-500 text-aegisaxiom-text'
                : 'border-transparent text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
            }`}
          >
            Notes ({opp.notes?.length || 0})
          </button>
          <button
            onClick={() => setActiveTab('procurement')}
            className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
              activeTab === 'procurement'
                ? 'border-violet-500 text-aegisaxiom-text'
                : 'border-transparent text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
            }`}
          >
            Procurement ({procurementRequests.length})
          </button>
          <button
            onClick={() => setActiveTab('attachments')}
            className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
              activeTab === 'attachments'
                ? 'border-violet-500 text-aegisaxiom-text'
                : 'border-transparent text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
            }`}
          >
            Attachments ({opp.attachments?.length || 0})
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {/* Details Tab */}
          {activeTab === 'details' && (
            <div className="space-y-6">
              {/* Financial Info */}
              <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3">
                  Financial Information
                </h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <div className="flex items-center gap-2 text-aegisaxiom-text-secondary text-sm mb-1">
                      <DollarSign className="w-4 h-4" />
                      <span>Opportunity Value</span>
                    </div>
                    <div className="text-xl font-bold text-green-600 dark:text-green-400">
                      {formatCurrency(opp.amount)}
                    </div>
                  </div>
                  <div>
                    <div className="text-aegisaxiom-text-secondary text-sm mb-1">Commission Method</div>
                    <div className="text-aegisaxiom-text">
                      {opp.commission_payment_method || 'Not specified'}
                    </div>
                  </div>
                </div>
              </div>

              {/* Account & Contact */}
              <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3">
                  Account & Contact
                </h3>
                <div className="space-y-3">
                  <div>
                    <div className="flex items-center gap-2 text-aegisaxiom-text-secondary text-sm mb-1">
                      <Building2 className="w-4 h-4" />
                      <span>Account</span>
                    </div>
                    <div className="text-aegisaxiom-text">{opp.account_name}</div>
                  </div>
                  <div>
                    <div className="flex items-center gap-2 text-aegisaxiom-text-secondary text-sm mb-1">
                      <User className="w-4 h-4" />
                      <span>Contact</span>
                    </div>
                    <div className="text-aegisaxiom-text">
                      {opp.contact_name || 'No contact assigned'}
                    </div>
                  </div>
                </div>
              </div>

              {/* Sales Operations Status - NEW SECTION */}
              {opp.lines_of_business && opp.lines_of_business.length > 0 && (
                <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary">
                      Sales Operations Status
                    </h3>
                    {loadingSOWs && <Clock className="w-4 h-4 text-aegisaxiom-text-muted animate-spin" />}
                  </div>

                  {opp.lines_of_business.map((lob) => {
                    const sowForLOB = sowsData.find((s) => s.line_of_business === lob);
                    const alertsForLOB = alertsData.filter(
                      (a) => a.line_of_business === lob && !a.dismissed,
                    );
                    const criticalAlerts = alertsForLOB.filter((a) => a.severity === 'critical');
                    const warningAlerts = alertsForLOB.filter((a) => a.severity === 'warning');
                    const successAlerts = alertsForLOB.filter((a) => a.severity === 'success');
                    const lobConfig = LOB_CONFIG[lob] || {};
                    const statusConfig = sowForLOB
                      ? SOW_STATUS_CONFIG[sowForLOB.status] || SOW_STATUS_CONFIG.draft
                      : SOW_STATUS_CONFIG.draft;

                    return (
                      <div
                        key={lob}
                        className="mb-3 last:mb-0 p-3 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg"
                      >
                        {/* LOB Header */}
                        <div className="flex items-center justify-between mb-2">
                          <div className="flex items-center gap-2">
                            <span
                              className={`px-2 py-1 rounded text-xs font-medium border ${lobConfig.color}`}
                            >
                              {lobConfig.label || lob}
                            </span>
                            <span
                              className={`px-2 py-1 rounded text-xs font-medium border ${statusConfig.color}`}
                            >
                              {statusConfig.label}
                            </span>
                          </div>
                          {sowForLOB && (
                            <button
                              onClick={() => {
                                onClose(); // Close modal first
                                setTimeout(() => {
                                  navigate('/sales-ops-workspace', {
                                    state: {
                                      openOpportunity: opportunity.opportunity_id,
                                      openLOB: lob,
                                    },
                                  });
                                }, 100);
                              }}
                              className="flex items-center gap-1 text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 transition"
                            >
                              <span>View SOW</span>
                              <ExternalLink className="w-3 h-3" />
                            </button>
                          )}
                        </div>

                        {/* SOW Owner */}
                        {sowForLOB?.assigned_to && (
                          <div className="text-xs text-aegisaxiom-text-secondary mb-2">
                            <span className="font-medium">Owner:</span>{' '}
                            {sowForLOB.assigned_to_name || sowForLOB.assigned_to}
                          </div>
                        )}

                        {/* Alerts & Notes */}
                        <div className="space-y-1">
                          {/* Critical Alerts */}
                          {criticalAlerts.map((alert, idx) => (
                            <div
                              key={`critical-${idx}`}
                              className="flex items-start gap-2 text-xs p-2 bg-red-50 dark:bg-red-500/10 border border-red-500/30 rounded"
                            >
                              <AlertCircle className="w-3 h-3 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
                              <div className="flex-1">
                                <span className="font-medium text-red-700 dark:text-red-300">
                                  {alert.rule_name}:
                                </span>
                                <span className="text-red-200 ml-1">{alert.message}</span>
                              </div>
                            </div>
                          ))}

                          {/* Warning Alerts */}
                          {warningAlerts.map((alert, idx) => (
                            <div
                              key={`warning-${idx}`}
                              className="flex items-start gap-2 text-xs p-2 bg-yellow-500/10 border border-yellow-500/30 rounded"
                            >
                              <AlertCircle className="w-3 h-3 text-yellow-600 dark:text-yellow-400 flex-shrink-0 mt-0.5" />
                              <div className="flex-1">
                                <span className="font-medium text-yellow-700 dark:text-yellow-300">
                                  {alert.rule_name}:
                                </span>
                                <span className="text-yellow-200 ml-1">{alert.message}</span>
                              </div>
                            </div>
                          ))}

                          {/* Success/Positive Notes */}
                          {successAlerts.map((alert, idx) => (
                            <div
                              key={`success-${idx}`}
                              className="flex items-start gap-2 text-xs p-2 bg-green-50 dark:bg-green-500/10 border border-green-500/30 rounded"
                            >
                              <CheckCircle className="w-3 h-3 text-green-600 dark:text-green-400 flex-shrink-0 mt-0.5" />
                              <div className="flex-1">
                                <span className="font-medium text-green-700 dark:text-green-300">
                                  {alert.rule_name}:
                                </span>
                                <span className="text-green-200 ml-1">{alert.message}</span>
                              </div>
                            </div>
                          ))}

                          {/* No SOW Created Yet */}
                          {!sowForLOB && (
                            <div className="text-xs text-aegisaxiom-text-muted italic p-2">
                              SOW not yet created for this line of business
                            </div>
                          )}

                          {/* No Alerts */}
                          {sowForLOB && alertsForLOB.length === 0 && (
                            <div className="flex items-center gap-2 text-xs text-aegisaxiom-text-muted italic p-2">
                              <CheckCircle className="w-3 h-3 text-aegisaxiom-text-muted" />
                              No active alerts
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Description */}
              {opp.description && (
                <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                  <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-2">
                    Description
                  </h3>
                  <p className="text-aegisaxiom-text-secondary text-sm whitespace-pre-wrap">
                    {opp.description}
                  </p>
                </div>
              )}

              {/* Assignment & Dates */}
              <div className="grid grid-cols-2 gap-4">
                <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                  <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3">
                    Assignment
                  </h3>
                  <div className="space-y-2 text-sm">
                    <div>
                      <div className="text-aegisaxiom-text-secondary">Owner</div>
                      <div className="text-aegisaxiom-text">{opp.opportunity_owner_name}</div>
                    </div>
                    <div>
                      <div className="text-aegisaxiom-text-secondary">Agent of Record</div>
                      <div className="text-aegisaxiom-text">{opp.agent_of_record_name || 'None'}</div>
                    </div>
                    <div>
                      <div className="text-aegisaxiom-text-secondary">Sales Ops</div>
                      <div className="text-aegisaxiom-text">{opp.assigned_sales_ops_name || 'None'}</div>
                    </div>
                  </div>
                </div>

                <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                  <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3">Dates</h3>
                  <div className="space-y-2 text-sm">
                    <div>
                      <div className="flex items-center gap-1 text-aegisaxiom-text-secondary">
                        <Calendar className="w-3 h-3" />
                        <span>Desired Proposal Date</span>
                      </div>
                      <div className="text-aegisaxiom-text">{formatDate(opp.desired_proposal_date)}</div>
                    </div>
                    <div>
                      <div className="text-aegisaxiom-text-secondary">Created</div>
                      <div className="text-aegisaxiom-text">{formatDateTime(opp.created_at)}</div>
                    </div>
                    <div>
                      <div className="text-aegisaxiom-text-secondary">Last Updated</div>
                      <div className="text-aegisaxiom-text">{formatDateTime(opp.updated_at)}</div>
                    </div>
                  </div>
                </div>
              </div>

              {/* Lines of Business */}
              {opp.lines_of_business?.length > 0 && (
                <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                  <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-2">
                    Lines of Business
                  </h3>
                  <div className="flex flex-wrap gap-2">
                    {opp.lines_of_business.map((lob) => (
                      <span
                        key={lob}
                        className="text-xs px-2 py-1 bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 rounded border border-violet-600"
                      >
                        {lob.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Notes Tab */}
          {activeTab === 'notes' && (
            <div className="space-y-4">
              {/* Add Note */}
              <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                <div className="flex items-start gap-3">
                  <textarea
                    value={noteText}
                    onChange={(e) => setNoteText(e.target.value)}
                    placeholder="Add a note..."
                    rows={3}
                    className="flex-1 px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 resize-none"
                  />
                  <button
                    onClick={handleAddNote}
                    disabled={!noteText.trim()}
                    className="px-4 py-2 bg-violet-600 hover:bg-violet-700 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <Plus className="w-4 h-4" />
                  </button>
                </div>
              </div>

              {/* Notes List */}
              <div className="space-y-3">
                {opp.notes?.length === 0 ? (
                  <div className="text-center py-8 text-aegisaxiom-text-muted">
                    No notes yet. Add one above!
                  </div>
                ) : (
                  opp.notes?.map((note) => (
                    <div
                      key={note.note_id}
                      className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4"
                    >
                      <div className="flex items-start justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <MessageSquare className="w-4 h-4 text-aegisaxiom-text-secondary" />
                          <span className="text-sm font-medium text-aegisaxiom-text">
                            {note.created_by_name}
                          </span>
                          <span className="text-xs text-aegisaxiom-text-muted">
                            {formatDateTime(note.created_at)}
                          </span>
                        </div>
                        <button
                          onClick={() => handleDeleteNote(note.note_id)}
                          className="p-1 hover:bg-aegisaxiom-elevated rounded"
                        >
                          <Trash2 className="w-3 h-3 text-aegisaxiom-text-secondary" />
                        </button>
                      </div>
                      <p className="text-sm text-aegisaxiom-text-secondary whitespace-pre-wrap">
                        {note.text}
                      </p>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}

          {/* Procurement Tab */}
          {activeTab === 'procurement' && (
            <div className="space-y-4">
              {/* Existing requests */}
              <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3 flex items-center gap-2">
                  <Package className="w-4 h-4" />
                  Requests ({procurementRequests.length})
                </h3>
                {procurementRequests.length === 0 ? (
                  <p className="text-xs text-aegisaxiom-text-muted">
                    No hardware or material requests yet for this opportunity.
                  </p>
                ) : (
                  <ul className="divide-y divide-aegisaxiom-border">
                    {procurementRequests.map((r) => (
                      <li key={r.request_id} className="py-2 text-sm">
                        <div className="flex items-center justify-between">
                          <div className="flex-1 min-w-0">
                            <p className="text-aegisaxiom-text truncate">
                              {r.product_name || r.item_description || '(no item)'}
                              {r.quantity ? ` × ${r.quantity}` : ''}
                            </p>
                            <p className="text-xs text-aegisaxiom-text-muted">
                              {r.distributor_name ? `${r.distributor_name} · ` : ''}
                              {r.priority} priority
                              {r.needed_by_date ? ` · needed by ${r.needed_by_date}` : ''}
                            </p>
                          </div>
                          <span
                            className={`text-xs px-2 py-0.5 rounded ml-2 ${
                              r.status === 'open'
                                ? 'bg-amber-500/10 text-amber-500'
                                : r.status === 'sourcing'
                                ? 'bg-blue-500/10 text-blue-500'
                                : r.status === 'ordered'
                                ? 'bg-violet-500/10 text-violet-500'
                                : r.status === 'received'
                                ? 'bg-emerald-500/10 text-emerald-500'
                                : 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted'
                            }`}
                          >
                            {r.status}
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/* Create form — gated on permission so users without it don't see a 403-bound form */}
              {canCreateRequest && (
              <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3">
                  New Hardware / Material Request
                </h3>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Distributor</label>
                    <select
                      value={newRequest.distributor_id}
                      onChange={(e) =>
                        setNewRequest((r) => ({ ...r, distributor_id: e.target.value }))
                      }
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
                    >
                      <option value="">— Any —</option>
                      {procurementDistributors.map((d) => (
                        <option key={d.distributor_id} value={d.distributor_id}>
                          {d.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Product</label>
                    <select
                      value={newRequest.product_id}
                      onChange={(e) =>
                        setNewRequest((r) => ({ ...r, product_id: e.target.value }))
                      }
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
                    >
                      <option value="">— None (use description) —</option>
                      {procurementProducts.map((p) => (
                        <option key={p.product_id} value={p.product_id}>
                          {p.name} ({p.sku})
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="col-span-2">
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                      Item Description
                    </label>
                    <input
                      type="text"
                      value={newRequest.item_description}
                      onChange={(e) =>
                        setNewRequest((r) => ({ ...r, item_description: e.target.value }))
                      }
                      placeholder="Required if no catalog product selected"
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Quantity</label>
                    <input
                      type="number"
                      min="1"
                      value={newRequest.quantity}
                      onChange={(e) =>
                        setNewRequest((r) => ({ ...r, quantity: e.target.value }))
                      }
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Needed By</label>
                    <input
                      type="date"
                      value={newRequest.needed_by_date}
                      onChange={(e) =>
                        setNewRequest((r) => ({ ...r, needed_by_date: e.target.value }))
                      }
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Priority</label>
                    <select
                      value={newRequest.priority}
                      onChange={(e) =>
                        setNewRequest((r) => ({ ...r, priority: e.target.value }))
                      }
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
                    >
                      <option value="low">low</option>
                      <option value="medium">medium</option>
                      <option value="high">high</option>
                    </select>
                  </div>
                  <div className="col-span-2">
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Notes</label>
                    <textarea
                      value={newRequest.notes}
                      onChange={(e) =>
                        setNewRequest((r) => ({ ...r, notes: e.target.value }))
                      }
                      rows={2}
                      className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text resize-none"
                    />
                  </div>
                </div>
                <div className="flex justify-end mt-3">
                  <button
                    type="button"
                    onClick={handleCreateRequest}
                    disabled={creatingRequest}
                    className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium rounded disabled:opacity-50"
                  >
                    {creatingRequest ? 'Creating…' : 'Create Request'}
                  </button>
                </div>
              </div>
              )}
            </div>
          )}

          {/* Attachments Tab */}
          {activeTab === 'attachments' && (
            <div className="space-y-4">
              {/* Upload */}
              <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
                <label className="flex items-center justify-center gap-2 cursor-pointer">
                  <input
                    type="file"
                    multiple
                    onChange={handleFileUpload}
                    className="hidden"
                    disabled={uploading}
                  />
                  <div className="flex items-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-700 rounded-lg transition-colors">
                    <Upload className="w-4 h-4" />
                    <span>{uploading ? 'Uploading...' : 'Upload File'}</span>
                  </div>
                </label>
                <p className="text-xs text-aegisaxiom-text-muted text-center mt-2">
                  Maximum file size: 10MB
                </p>
              </div>

              {/* Attachments List */}
              <div className="space-y-2">
                {opp.attachments?.length === 0 ? (
                  <div className="text-center py-8 text-aegisaxiom-text-muted">
                    No attachments yet. Upload one above!
                  </div>
                ) : (
                  opp.attachments?.map((attachment) => (
                    <div
                      key={attachment.attachment_id}
                      className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4 flex items-center justify-between"
                    >
                      <button
                        type="button"
                        onClick={() => handleViewAttachment(attachment)}
                        className="flex items-center gap-3 flex-1 min-w-0 text-left"
                        title="Click to view"
                      >
                        <Paperclip className="w-4 h-4 text-aegisaxiom-text-secondary flex-shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm text-aegisaxiom-text truncate hover:underline">
                            {attachment.filename}
                          </div>
                          <div className="text-xs text-aegisaxiom-text-muted">
                            {(attachment.file_size / 1024).toFixed(1)} KB • Uploaded by{' '}
                            {attachment.uploaded_by_name} •{formatDateTime(attachment.uploaded_at)}
                          </div>
                        </div>
                      </button>
                      <button
                        onClick={() => handleDeleteAttachment(attachment.attachment_id)}
                        className="p-2 hover:bg-aegisaxiom-elevated rounded transition-colors flex-shrink-0"
                      >
                        <Trash2 className="w-4 h-4 text-red-600 dark:text-red-400" />
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </div>
      </div>
      <ConfirmModal
        isOpen={confirmState.open}
        title={confirmState.title}
        message={confirmState.message}
        confirmText={confirmState.confirmText}
        onConfirm={() => confirmState.action?.()}
        onCancel={() =>
          setConfirmState({
            open: false,
            action: null,
            title: '',
            message: '',
            confirmText: 'Confirm',
          })
        }
      />
    </div>
  );
}
