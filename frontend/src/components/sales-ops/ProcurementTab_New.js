import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '../../lib/apiConfig';
import TabStrip from '../shared/TabStrip';
import {
  CheckCircle,
  XCircle,
  Clock,
  DollarSign,
  TrendingUp,
  TrendingDown,
  Package,
  Users,
  AlertTriangle,
  Building2,
  Award,
  BarChart3,
  User,
} from 'lucide-react';

const API = API_URL;

export default function ProcurementTabNew() {
  const [activeView, setActiveView] = useState('approvals'); // approvals, analytics, distributors, requests
  const [pendingServices, setPendingServices] = useState([]);
  const [pendingResources, setPendingResources] = useState([]);
  const [pendingIndividualRates, setPendingIndividualRates] = useState([]);
  const [pendingNotifications, setPendingNotifications] = useState([]);
  const [acknowledgedNotifications, setAcknowledgedNotifications] = useState([]);
  const [showAcknowledged, setShowAcknowledged] = useState(true);
  const [selectedNotification, setSelectedNotification] = useState(null);
  const [showNotificationModal, setShowNotificationModal] = useState(false);
  const [notificationNotes, setNotificationNotes] = useState('');
  const [assignedTo, setAssignedTo] = useState('');
  const [procurementUsers, setProcurementUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sellPriceInputs, setSellPriceInputs] = useState({});
  const [distributors, setDistributors] = useState([]);
  const [distributorStats, setDistributorStats] = useState({});
  const [selectedDistributor, setSelectedDistributor] = useState(null);
  const [distributorProducts, setDistributorProducts] = useState([]);
  const [requestsList, setRequestsList] = useState([]);
  const [requestsStatusFilter, setRequestsStatusFilter] = useState('open');
  // Procurement Bot + Quote Scaffold (Ticket 9). Mode B
  // manual-review queue. See
  // docs/sales-ops/PROCUREMENT_BOT_SCAFFOLD.md.
  const [sourcingList, setSourcingList] = useState([]);
  const [sourcingStatusFilter, setSourcingStatusFilter] = useState('review_required');
  const [sourcingDetail, setSourcingDetail] = useState(null);
  const [sourcingDetailLoading, setSourcingDetailLoading] = useState(false);
  // SOW Item Execution worklist. Direct manual-portal pricing flow
  // for Field Services SOW product items. Separate from the
  // sourcing-request scaffold above — this surface drives the
  // SOWProductItem lifecycle, not ProcurementSourcingRequest.
  const [pricingQueue, setPricingQueue] = useState([]);
  const [pricingQueueLoading, setPricingQueueLoading] = useState(false);
  const [pricingRowDraft, setPricingRowDraft] = useState({}); // keyed by sow_id|item_id
  const [orderingRowDraft, setOrderingRowDraft] = useState({});
  const [savingRowKey, setSavingRowKey] = useState(null);

  useEffect(() => {
    loadPendingApprovals();
    loadProcurementUsers();
  }, []);

  useEffect(() => {
    if (activeView === 'distributors') {
      loadDistributors();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeView]);

  useEffect(() => {
    if (activeView === 'requests') {
      loadRequests();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeView, requestsStatusFilter]);

  useEffect(() => {
    if (activeView === 'sourcing') {
      loadSourcingRequests();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeView, sourcingStatusFilter]);

  useEffect(() => {
    if (activeView === 'pricing_queue') {
      loadPricingQueue();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeView]);

  const pricingRowKey = (row) => `${row.sow_id}|${row.item_id}`;

  const loadPricingQueue = async () => {
    setPricingQueueLoading(true);
    try {
      const res = await api.get(
        `${API}/api/sales-ops/procurement/sow-items/pricing-queue`,
      );
      if (res.ok) {
        const data = await res.json();
        setPricingQueue(data.items || []);
      } else {
        toast.error('Failed to load pricing queue');
      }
    } catch (e) {
      console.error('Error loading pricing queue:', e);
      toast.error('Failed to load pricing queue');
    } finally {
      setPricingQueueLoading(false);
    }
  };

  const updatePricingDraft = (row, field, value) => {
    const key = pricingRowKey(row);
    setPricingRowDraft((prev) => ({
      ...prev,
      [key]: { ...(prev[key] || {}), [field]: value },
    }));
  };

  const updateOrderingDraft = (row, field, value) => {
    const key = pricingRowKey(row);
    setOrderingRowDraft((prev) => ({
      ...prev,
      [key]: { ...(prev[key] || {}), [field]: value },
    }));
  };

  const savePricingRow = async (row) => {
    const key = pricingRowKey(row);
    const draft = pricingRowDraft[key] || {};
    const body = {};
    if (draft.unit_price !== undefined && draft.unit_price !== '') {
      const parsed = Number(draft.unit_price);
      if (Number.isNaN(parsed) || parsed < 0) {
        toast.error('Unit price must be a non-negative number');
        return;
      }
      body.unit_price = parsed;
    }
    ['supplier_name', 'supplier_source_url', 'availability_note', 'procurement_notes'].forEach(
      (f) => {
        if (draft[f] !== undefined && draft[f] !== '') body[f] = draft[f];
      },
    );
    if (Object.keys(body).length === 0) {
      toast.error('Enter at least one field to save');
      return;
    }
    setSavingRowKey(key);
    try {
      const res = await api.patch(
        `${API}/api/sales-ops/procurement/sows/${row.sow_id}/items/${row.item_id}/pricing`,
        body,
      );
      if (res.ok) {
        toast.success('Pricing saved');
        setPricingRowDraft((prev) => {
          const next = { ...prev };
          delete next[key];
          return next;
        });
        await loadPricingQueue();
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err?.detail?.message || 'Save failed');
      }
    } catch (e) {
      console.error('Save pricing failed:', e);
      toast.error('Save failed');
    } finally {
      setSavingRowKey(null);
    }
  };

  const markRowOrdered = async (row) => {
    const key = pricingRowKey(row);
    const draft = orderingRowDraft[key] || {};
    const body = {};
    ['expected_delivery_date', 'order_reference', 'tracking_number', 'tracking_url', 'procurement_notes'].forEach(
      (f) => {
        if (draft[f] !== undefined && draft[f] !== '') body[f] = draft[f];
      },
    );
    setSavingRowKey(key);
    try {
      const res = await api.post(
        `${API}/api/sales-ops/procurement/sows/${row.sow_id}/items/${row.item_id}/ordered`,
        body,
      );
      if (res.ok) {
        toast.success('Marked ordered');
        setOrderingRowDraft((prev) => {
          const next = { ...prev };
          delete next[key];
          return next;
        });
        await loadPricingQueue();
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err?.detail?.message || 'Failed to mark ordered');
      }
    } catch (e) {
      console.error('Mark ordered failed:', e);
      toast.error('Failed to mark ordered');
    } finally {
      setSavingRowKey(null);
    }
  };

  // Procurement Bot + Quote Scaffold handlers. The scaffold is
  // Mode B (manual review) per
  // docs/sales-ops/PROCUREMENT_BOT_SCAFFOLD.md. No real vendor
  // names are hardcoded; vendor_name comes from the quote
  // payload.
  const loadSourcingRequests = async () => {
    try {
      const qs =
        sourcingStatusFilter && sourcingStatusFilter !== 'all'
          ? `?status=${sourcingStatusFilter}`
          : '';
      const res = await api.get(
        `${API}/api/sales-ops/procurement/sourcing-requests${qs}`,
      );
      if (res.ok) {
        const data = await res.json();
        setSourcingList(data.items || []);
      }
    } catch (e) {
      console.error('Error loading sourcing requests:', e);
    }
  };

  const openSourcingDetail = async (sourcingRequestId) => {
    setSourcingDetailLoading(true);
    try {
      const res = await api.get(
        `${API}/api/sales-ops/procurement/sourcing-requests/${sourcingRequestId}`,
      );
      if (res.ok) {
        const data = await res.json();
        setSourcingDetail(data);
      } else {
        toast.error('Failed to load sourcing request detail.');
      }
    } catch (e) {
      console.error('Error loading sourcing detail:', e);
      toast.error('Failed to load sourcing request detail.');
    } finally {
      setSourcingDetailLoading(false);
    }
  };

  const selectSourcingQuote = async (sourcingRequestId, quoteId) => {
    try {
      const res = await api.post(
        `${API}/api/sales-ops/procurement/sourcing-requests/${sourcingRequestId}/quotes/${quoteId}/select`,
        {},
      );
      if (res.ok) {
        toast.success('Quote selected.');
        await openSourcingDetail(sourcingRequestId);
        await loadSourcingRequests();
      } else {
        toast.error('Failed to select quote.');
      }
    } catch (e) {
      console.error('Error selecting quote:', e);
      toast.error('Failed to select quote.');
    }
  };

  const markSourcingOrdered = async (sourcingRequestId) => {
    try {
      const res = await api.post(
        `${API}/api/sales-ops/procurement/sourcing-requests/${sourcingRequestId}/ordered`,
        {},
      );
      if (res.ok) {
        toast.success('Marked ordered.');
        await openSourcingDetail(sourcingRequestId);
        await loadSourcingRequests();
      } else {
        let detail = 'Failed to mark ordered.';
        try {
          const body = await res.json();
          if (body && body.detail && typeof body.detail.message === 'string') {
            detail = body.detail.message;
          }
        } catch (_e) {
          // body wasn't JSON
        }
        toast.error(detail);
      }
    } catch (e) {
      console.error('Error marking ordered:', e);
      toast.error('Failed to mark ordered.');
    }
  };

  const markSourcingDelivered = async (sourcingRequestId) => {
    try {
      const res = await api.post(
        `${API}/api/sales-ops/procurement/sourcing-requests/${sourcingRequestId}/delivered`,
        {},
      );
      if (res.ok) {
        toast.success('Marked delivered.');
        await openSourcingDetail(sourcingRequestId);
        await loadSourcingRequests();
      } else {
        let detail = 'Failed to mark delivered.';
        try {
          const body = await res.json();
          if (body && body.detail && typeof body.detail.message === 'string') {
            detail = body.detail.message;
          }
        } catch (_e) {
          // body wasn't JSON
        }
        toast.error(detail);
      }
    } catch (e) {
      console.error('Error marking delivered:', e);
      toast.error('Failed to mark delivered.');
    }
  };

  const loadRequests = async () => {
    try {
      const qs =
        requestsStatusFilter && requestsStatusFilter !== 'all'
          ? `?status=${requestsStatusFilter}`
          : '';
      const res = await api.get(`${API}/api/sales-ops/hardware-material-requests${qs}`);
      if (res.ok) {
        const data = await res.json();
        setRequestsList(data.items || []);
      }
    } catch (e) {
      console.error('Error loading requests:', e);
    }
  };

  const updateRequestStatus = async (requestId, newStatus) => {
    try {
      const res = await api.patch(`${API}/api/sales-ops/hardware-material-requests/${requestId}`, {
        status: newStatus,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || 'Failed to update status');
      }
      toast.success(`Status updated to ${newStatus}`);
      loadRequests();
    } catch (e) {
      toast.error(e.message);
    }
  };

  const loadDistributors = async () => {
    try {
      const res = await api.get(`${API}/api/admin/distributors`);
      if (!res.ok) return;
      const data = await res.json();
      const items = data.items || [];
      setDistributors(items);

      const statsEntries = await Promise.all(
        items.map(async (d) => {
          try {
            const s = await api.get(`${API}/api/admin/distributors/${d.distributor_id}/stats`);
            if (!s.ok)
              return [d.distributor_id, { linked_product_count: 0, open_request_count: 0 }];
            const body = await s.json();
            return [d.distributor_id, body];
          } catch {
            return [d.distributor_id, { linked_product_count: 0, open_request_count: 0 }];
          }
        }),
      );
      setDistributorStats(Object.fromEntries(statsEntries));
    } catch (error) {
      console.error('Error loading distributors:', error);
    }
  };

  const openDistributorDetail = async (distributor) => {
    setSelectedDistributor(distributor);
    setDistributorProducts([]);
    try {
      const res = await api.get(`${API}/api/admin/products?item_category=hardware`);
      const materialsRes = await api.get(`${API}/api/admin/products?item_category=materials`);
      const products = [];
      if (res.ok) {
        const data = await res.json();
        products.push(...(data.products || []));
      }
      if (materialsRes.ok) {
        const data = await materialsRes.json();
        products.push(...(data.products || []));
      }
      setDistributorProducts(
        products.filter((p) => p.distributor_id === distributor.distributor_id),
      );
    } catch (error) {
      console.error('Error loading distributor products:', error);
    }
  };

  const loadProcurementUsers = async () => {
    try {
      const response = await api.get(`${API}/api/admin/users`);
      if (response.ok) {
        const data = await response.json();
        const users = Array.isArray(data) ? data : data.items || [];
        const procUsers = users.filter(
          (u) => ['admin', 'director', 'finance'].includes(u.role) && u.is_active,
        );
        setProcurementUsers(procUsers);
      }
    } catch (error) {
      console.error('Error loading procurement users:', error);
    }
  };

  const loadPendingApprovals = async () => {
    setLoading(true);
    try {
      const response = await api.get(`${API}/api/admin/contractor-businesses?status=active`);
      if (response.ok) {
        const data = await response.json();
        const businesses = data.items || [];

        const services = [];
        const resources = [];

        businesses.forEach((business) => {
          (business.services_offered || []).forEach((service) => {
            if (service.approval_status === 'pending') {
              services.push({
                ...service,
                contractor_business_id: business.business_id,
                contractor_business_name: business.business_name,
                contractor_contact: business.contact_name,
              });
            }
          });

          (business.resource_types_offered || []).forEach((resource) => {
            if (resource.approval_status === 'pending') {
              resources.push({
                ...resource,
                contractor_business_id: business.business_id,
                contractor_business_name: business.business_name,
                contractor_contact: business.contact_name,
              });
            }
          });
        });

        setPendingServices(services);
        setPendingResources(resources);
      }

      const ratesResponse = await api.get(
        `${API}/api/sales-ops/procurement/pending-contractor-rates`,
      );
      if (ratesResponse.ok) {
        const ratesData = await ratesResponse.json();
        setPendingIndividualRates(ratesData.items || []);
      }

      const notificationsResponse = await api.get(
        `${API}/api/sales-ops/procurement-notifications?status=pending`,
      );
      if (notificationsResponse.ok) {
        const notificationsData = await notificationsResponse.json();
        setPendingNotifications(notificationsData.items || []);
      }

      const acknowledgedResponse = await api.get(
        `${API}/api/sales-ops/procurement-notifications?status=acknowledged`,
      );
      if (acknowledgedResponse.ok) {
        const acknowledgedData = await acknowledgedResponse.json();
        setAcknowledgedNotifications(acknowledgedData.items || []);
      }
    } catch (error) {
      console.error('Error loading pending approvals:', error);
      toast.error('Failed to load approval queue');
    } finally {
      setLoading(false);
    }
  };

  const handleApproveService = async (businessId, serviceId) => {
    const sellPrice = sellPriceInputs[serviceId];

    if (!sellPrice || sellPrice <= 0) {
      toast.error('Please enter a sell price before approving');
      return;
    }

    try {
      const response = await api.put(
        `${API}/api/sales-ops/contractor-businesses/${businessId}/services/${serviceId}/approve`,
        { action: 'approve', sell_price: parseFloat(sellPrice) },
      );

      if (response.ok) {
        toast.success('Service approved with sell price');
        setSellPriceInputs({});
        loadPendingApprovals();
      } else {
        const error = await response.json();
        toast.error(error.detail || 'Failed to approve service');
      }
    } catch (error) {
      console.error('Error approving service:', error);
      toast.error('Failed to approve service');
    }
  };

  const handleRejectService = async (businessId, serviceId) => {
    const reason = window.prompt('Enter rejection reason:');
    if (!reason) return;

    try {
      const response = await api.put(
        `${API}/api/sales-ops/contractor-businesses/${businessId}/services/${serviceId}/approve`,
        { action: 'reject', rejection_reason: reason },
      );

      if (response.ok) {
        toast.success('Service rejected');
        loadPendingApprovals();
      } else {
        const error = await response.json();
        toast.error(error.detail || 'Failed to reject service');
      }
    } catch (error) {
      console.error('Error rejecting service:', error);
      toast.error('Failed to reject service');
    }
  };

  const handleApproveResource = async (businessId, resourceTypeId) => {
    try {
      const response = await api.put(
        `${API}/api/admin/contractor-businesses/${businessId}/resource-types/${resourceTypeId}/approve`,
        { action: 'approve' },
      );

      if (response.ok) {
        toast.success('Resource type approved successfully');
        loadPendingApprovals();
      } else {
        const error = await response.json();
        toast.error(error.detail || 'Failed to approve resource type');
      }
    } catch (error) {
      console.error('Error approving resource type:', error);
      toast.error('Failed to approve resource type');
    }
  };

  const handleRejectResource = async (businessId, resourceTypeId) => {
    const reason = window.prompt('Enter rejection reason:');
    if (!reason) return;

    try {
      const response = await api.put(
        `${API}/api/admin/contractor-businesses/${businessId}/resource-types/${resourceTypeId}/approve`,
        { action: 'reject', rejection_reason: reason },
      );

      if (response.ok) {
        toast.success('Resource type rejected');
        loadPendingApprovals();
      } else {
        const error = await response.json();
        toast.error(error.detail || 'Failed to reject resource type');
      }
    } catch (error) {
      console.error('Error rejecting resource type:', error);
      toast.error('Failed to reject resource type');
    }
  };

  const handleApproveIndividualRate = async (userId, resourceTypeId) => {
    try {
      const response = await api.put(
        `${API}/api/sales-ops/procurement/contractor-rates/${userId}/${resourceTypeId}/approve`,
        { action: 'approve' },
      );

      if (response.ok) {
        toast.success('Individual rate exception approved');
        loadPendingApprovals();
      } else {
        const error = await response.json();
        toast.error(error.detail || 'Failed to approve rate');
      }
    } catch (error) {
      console.error('Error approving individual rate:', error);
      toast.error('Failed to approve rate');
    }
  };

  const handleRejectIndividualRate = async (userId, resourceTypeId) => {
    const reason = prompt('Enter rejection reason (will revert to business rate):');
    if (!reason) return;

    try {
      const response = await api.put(
        `${API}/api/sales-ops/procurement/contractor-rates/${userId}/${resourceTypeId}/approve`,
        { action: 'reject', rejection_reason: reason },
      );

      if (response.ok) {
        toast.success('Rate rejected - Reverted to business rate');
        loadPendingApprovals();
      } else {
        const error = await response.json();
        toast.error(error.detail || 'Failed to reject rate');
      }
    } catch (error) {
      console.error('Error rejecting individual rate:', error);
      toast.error('Failed to reject rate');
    }
  };

  const handleAcknowledgeNotification = async (notificationId) => {
    try {
      const response = await api.patch(
        `${API}/api/sales-ops/procurement-notifications/${notificationId}/status`,
        { status: 'acknowledged' },
      );

      if (response.ok) {
        toast.success('Notification acknowledged - Moved to In Progress');
        loadPendingApprovals();
      } else {
        toast.error('Failed to acknowledge notification');
      }
    } catch (error) {
      console.error('Error acknowledging notification:', error);
      toast.error('Failed to acknowledge notification');
    }
  };

  const handleResolveNotification = async (notificationId) => {
    try {
      const response = await api.patch(
        `${API}/api/sales-ops/procurement-notifications/${notificationId}/status`,
        { status: 'resolved' },
      );

      if (response.ok) {
        toast.success('Notification resolved');
        loadPendingApprovals();
      } else {
        toast.error('Failed to resolve notification');
      }
    } catch (error) {
      console.error('Error resolving notification:', error);
      toast.error('Failed to resolve notification');
    }
  };

  const handleOpenNotificationDetail = (notification) => {
    setSelectedNotification(notification);
    setNotificationNotes(notification.notes || '');
    setAssignedTo(notification.assigned_to || '');
    setShowNotificationModal(true);
  };

  const handleSaveNotificationDetails = async () => {
    if (!selectedNotification) return;

    try {
      const response = await api.patch(
        `${API}/api/sales-ops/procurement-notifications/${selectedNotification.notification_id}`,
        {
          notes: notificationNotes,
          assigned_to: assignedTo,
        },
      );

      if (response.ok) {
        toast.success('Notification updated');
        setShowNotificationModal(false);
        loadPendingApprovals();
      } else {
        toast.error('Failed to update notification');
      }
    } catch (error) {
      console.error('Error updating notification:', error);
      toast.error('Failed to update notification');
    }
  };

  const totalPending =
    pendingServices.length +
    pendingResources.length +
    pendingIndividualRates.length +
    pendingNotifications.length;

  return (
    <div className="space-y-5 sm:space-y-6">
      {/* Subtitle (no H2 — parent shell already owns the title) */}
      <p className="text-aegisaxiom-text-secondary text-sm">
        Control profitability, approve resources &amp; services, and manage vendor partnerships
      </p>

      {/* Sub-tabs */}
      <TabStrip
        variant="pill"
        size="sm"
        stretch
        className="pb-2 sm:pb-3 border-b border-aegisaxiom-border"
        activeId={activeView}
        onChange={setActiveView}
        tabs={[
          {
            id: 'approvals',
            label: 'Approvals',
            icon: Clock,
            badge: totalPending || null,
            hint:
              acknowledgedNotifications.length > 0
                ? `${acknowledgedNotifications.length} in progress`
                : undefined,
          },
          { id: 'analytics', label: 'Analytics', icon: BarChart3 },
          { id: 'distributors', label: 'Distributors', icon: Building2 },
          { id: 'requests', label: 'Requests', icon: Package },
          { id: 'sourcing', label: 'Sourcing', icon: Package },
          { id: 'pricing_queue', label: 'Pricing queue', icon: DollarSign },
        ]}
      />

      {/* APPROVAL QUEUE VIEW */}
      {activeView === 'approvals' && (
        <div className="space-y-6">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-violet-500"></div>
            </div>
          ) : totalPending === 0 && acknowledgedNotifications.length === 0 ? (
            <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-8 sm:p-10 text-center">
              <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-green-100 dark:bg-green-900/30 mb-4">
                <CheckCircle className="w-7 h-7 text-green-600 dark:text-green-400" />
              </div>
              <h3 className="text-lg font-semibold text-aegisaxiom-text mb-1">All caught up</h3>
              <p className="text-aegisaxiom-text-secondary text-sm max-w-sm mx-auto">
                No pending resource requests, service approvals, resource type assignments, or rate
                exceptions in the queue right now.
              </p>
            </div>
          ) : (
            <>
              {/* Resource Requests & Notifications */}
              <div>
                <div className="flex items-center gap-2 mb-4">
                  <AlertTriangle className="w-5 h-5 text-red-600 dark:text-red-400" />
                  <h3 className="text-lg font-bold text-aegisaxiom-text">Resource Requests</h3>
                  <span className="text-sm text-aegisaxiom-text-secondary">
                    ({pendingNotifications.length} new)
                  </span>
                </div>

                {pendingNotifications.length === 0 ? (
                  <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4 text-center">
                    <CheckCircle className="w-8 h-8 text-green-500 mx-auto mb-2" />
                    <p className="text-aegisaxiom-text-secondary text-sm">No new resource requests</p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {pendingNotifications.map((notification) => (
                      <div
                        key={notification.notification_id}
                        className="bg-aegisaxiom-surface border border-red-500/40 rounded-lg p-3"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-1">
                              <h4 className="font-semibold text-aegisaxiom-text text-sm">
                                🚨 {notification.resource_type_name}
                              </h4>
                              <span className="px-2 py-0.5 rounded text-xs bg-red-50 dark:bg-red-900/40 text-red-700 dark:text-red-300 border border-red-500/40 shrink-0">
                                New
                              </span>
                            </div>
                            <p className="text-xs text-aegisaxiom-text-secondary mb-1">
                              {notification.message}
                            </p>
                            <p className="text-xs text-aegisaxiom-text-muted">
                              {notification.created_by_name} •{' '}
                              {new Date(notification.created_at).toLocaleDateString()}
                            </p>
                          </div>

                          <div className="flex gap-2 shrink-0">
                            <button
                              onClick={() =>
                                handleAcknowledgeNotification(notification.notification_id)
                              }
                              className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium rounded transition"
                              title="Move to In Progress"
                            >
                              Start Working
                            </button>
                            <button
                              onClick={() =>
                                handleResolveNotification(notification.notification_id)
                              }
                              className="px-3 py-1.5 bg-green-600 hover:bg-green-500 text-white text-xs font-medium rounded transition"
                              title="Mark as fully resolved"
                            >
                              Resolve
                            </button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {acknowledgedNotifications.length > 0 && (
                  <div className="mt-4">
                    <button
                      onClick={() => setShowAcknowledged(!showAcknowledged)}
                      className="flex items-center gap-2 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition mb-2"
                    >
                      <span
                        className={`transform transition-transform ${showAcknowledged ? 'rotate-90' : ''}`}
                      >
                        ▶
                      </span>
                      In Progress ({acknowledgedNotifications.length})
                    </button>

                    {showAcknowledged && (
                      <div className="space-y-2 ml-4">
                        {acknowledgedNotifications.map((notification) => (
                          <div
                            key={notification.notification_id}
                            className="bg-aegisaxiom-surface border border-blue-500/30 rounded-lg p-2 text-xs hover:border-blue-400 transition cursor-pointer"
                            onClick={() => handleOpenNotificationDetail(notification)}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <div className="flex-1 min-w-0">
                                <span className="text-aegisaxiom-text font-medium">
                                  {notification.resource_type_name}
                                </span>
                                <span className="text-aegisaxiom-text-muted ml-2">
                                  • {notification.created_by_name}
                                </span>
                                {notification.assigned_to_name && (
                                  <span className="text-blue-600 dark:text-blue-400 ml-2">
                                    → {notification.assigned_to_name}
                                  </span>
                                )}
                              </div>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleResolveNotification(notification.notification_id);
                                }}
                                className="px-2 py-1 bg-green-600 hover:bg-green-500 text-white text-xs rounded transition shrink-0"
                              >
                                Resolve
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Pending Services */}
              <div>
                <div className="flex items-center gap-2 mb-4">
                  <Package className="w-5 h-5 text-violet-600 dark:text-violet-400" />
                  <h3 className="text-lg font-bold text-aegisaxiom-text">Pending Contractor Services</h3>
                  <span className="text-sm text-aegisaxiom-text-secondary">
                    ({pendingServices.length})
                  </span>
                </div>

                {pendingServices.length === 0 ? (
                  <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-6 text-center">
                    <CheckCircle className="w-10 h-10 text-green-500 mx-auto mb-2" />
                    <p className="text-aegisaxiom-text-secondary text-sm">No pending service approvals</p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {pendingServices.map((service) => (
                      <div
                        key={service.service_id}
                        className="bg-aegisaxiom-surface border border-amber-500/40 rounded-lg p-4"
                      >
                        <div className="flex items-start justify-between mb-3">
                          <div className="flex-1">
                            <div className="flex items-center gap-2 mb-2">
                              <h4 className="font-semibold text-aegisaxiom-text">
                                {service.service_name}
                              </h4>
                              <span className="px-2 py-1 rounded text-xs bg-amber-50 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 border border-amber-500/40">
                                ⏳ Pending Approval
                              </span>
                            </div>
                            <p className="text-xs text-aegisaxiom-text-secondary mb-2">
                              <strong>Contractor:</strong> {service.contractor_business_name} (
                              {service.contractor_contact})
                            </p>
                            <p className="text-xs text-aegisaxiom-text-muted mb-2">
                              <strong>Recurrency:</strong> {service.recurrency?.toUpperCase()}
                            </p>
                            {service.notes && (
                              <p className="text-xs text-aegisaxiom-text-secondary italic">
                                Note: {service.notes}
                              </p>
                            )}
                          </div>
                        </div>

                        <div className="grid grid-cols-2 gap-4 text-sm mb-4 p-3 bg-aegisaxiom-elevated rounded">
                          <div>
                            <span className="text-aegisaxiom-text-secondary block mb-1">
                              Contractor Price
                            </span>
                            <span className="text-violet-600 dark:text-violet-400 font-semibold">
                              ${service.price_per_recurrency?.toFixed(0)}/{service.recurrency}
                            </span>
                          </div>
                          <div>
                            <span className="text-aegisaxiom-text-secondary block mb-1">
                              Cost to Business
                            </span>
                            <span className="text-green-600 dark:text-green-400 font-semibold">
                              ${service.cost_to_business?.toFixed(0)}/{service.recurrency}
                            </span>
                          </div>
                        </div>

                        <div className="mb-4 p-3 bg-violet-900/20 border border-violet-700/40 rounded">
                          <label className="block text-sm font-medium text-violet-700 dark:text-violet-300 mb-2">
                            💰 Set Customer Sell Price (per {service.recurrency}) *
                          </label>
                          <input
                            type="number"
                            value={sellPriceInputs[service.service_id] || ''}
                            onChange={(e) =>
                              setSellPriceInputs({
                                ...sellPriceInputs,
                                [service.service_id]: e.target.value,
                              })
                            }
                            className="w-full px-3 py-2 bg-aegisaxiom-surface border border-violet-700 rounded-lg text-aegisaxiom-text"
                            placeholder="e.g., 799"
                          />
                          {sellPriceInputs[service.service_id] && service.cost_to_business && (
                            <div className="mt-2 text-xs text-aegisaxiom-text-secondary">
                              Margin:{' '}
                              <span className="text-violet-700 dark:text-violet-300 font-semibold">
                                $
                                {(
                                  parseFloat(sellPriceInputs[service.service_id]) -
                                  service.cost_to_business
                                ).toFixed(0)}
                                (
                                {(
                                  ((parseFloat(sellPriceInputs[service.service_id]) -
                                    service.cost_to_business) /
                                    parseFloat(sellPriceInputs[service.service_id])) *
                                  100
                                ).toFixed(1)}
                                %)
                              </span>
                            </div>
                          )}
                        </div>

                        <div className="flex gap-2">
                          <button
                            onClick={() =>
                              handleApproveService(
                                service.contractor_business_id,
                                service.service_id,
                              )
                            }
                            disabled={
                              !sellPriceInputs[service.service_id] ||
                              parseFloat(sellPriceInputs[service.service_id]) <= 0
                            }
                            className="flex-1 px-4 py-2 bg-green-600 hover:bg-green-500 text-white font-medium rounded-lg transition flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            <CheckCircle className="w-4 h-4" />
                            Approve with Sell Price
                          </button>
                          <button
                            onClick={() =>
                              handleRejectService(
                                service.contractor_business_id,
                                service.service_id,
                              )
                            }
                            className="flex-1 px-4 py-2 bg-red-600 hover:bg-red-500 text-white font-medium rounded-lg transition flex items-center justify-center gap-2"
                          >
                            <XCircle className="w-4 h-4" />
                            Reject
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Pending Resource Types */}
              <div>
                <div className="flex items-center gap-2 mb-4">
                  <Users className="w-5 h-5 text-violet-600 dark:text-violet-400" />
                  <h3 className="text-lg font-bold text-aegisaxiom-text">
                    Pending Resource Type Assignments
                  </h3>
                  <span className="text-sm text-aegisaxiom-text-secondary">
                    ({pendingResources.length})
                  </span>
                </div>

                {pendingResources.length === 0 ? (
                  <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-6 text-center">
                    <CheckCircle className="w-10 h-10 text-green-500 mx-auto mb-2" />
                    <p className="text-aegisaxiom-text-secondary text-sm">
                      No pending resource approvals
                    </p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {pendingResources.map((resource) => (
                      <div
                        key={`${resource.contractor_business_id}-${resource.resource_type_id}`}
                        className="bg-aegisaxiom-surface border border-amber-500/40 rounded-lg p-4"
                      >
                        <div className="flex items-start justify-between mb-3">
                          <div className="flex-1">
                            <div className="flex items-center gap-2 mb-2">
                              <h4 className="font-semibold text-aegisaxiom-text">
                                {resource.resource_type_name}
                              </h4>
                              <span className="px-2 py-1 rounded text-xs bg-amber-50 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 border border-amber-500/40">
                                ⏳ Pending Approval
                              </span>
                            </div>
                            <p className="text-xs text-aegisaxiom-text-secondary mb-2">
                              <strong>Contractor:</strong> {resource.contractor_business_name} (
                              {resource.contractor_contact})
                            </p>
                            <p className="text-xs text-aegisaxiom-text-secondary">
                              <strong>Pricing Models:</strong>{' '}
                              {resource.pricing_models_accepted?.join(', ').toUpperCase()}
                            </p>
                          </div>
                        </div>

                        <div className="grid grid-cols-3 gap-4 text-sm mb-4 p-3 bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-700/40 rounded">
                          <div>
                            <span className="text-aegisaxiom-text-secondary block mb-1">
                              Default Cost Rate
                            </span>
                            <span className="text-aegisaxiom-text-secondary font-semibold">
                              ${resource.default_cost_rate?.toFixed(0)}/hr
                            </span>
                          </div>
                          <div>
                            <span className="text-aegisaxiom-text-secondary block mb-1">
                              Requested Cost Rate
                            </span>
                            <span className="text-red-700 dark:text-red-300 font-semibold">
                              ${resource.cost_rate?.toFixed(0)}/hr
                            </span>
                          </div>
                          <div>
                            <span className="text-aegisaxiom-text-secondary block mb-1">Overage</span>
                            <span className="text-red-600 dark:text-red-400 font-semibold">
                              +$
                              {(
                                (resource.cost_rate || 0) - (resource.default_cost_rate || 0)
                              ).toFixed(0)}
                              /hr
                            </span>
                          </div>
                        </div>

                        <div className="flex gap-2">
                          <button
                            onClick={() =>
                              handleApproveResource(
                                resource.contractor_business_id,
                                resource.resource_type_id,
                              )
                            }
                            className="flex-1 px-4 py-2 bg-green-600 hover:bg-green-500 text-white font-medium rounded-lg transition flex items-center justify-center gap-2"
                          >
                            <CheckCircle className="w-4 h-4" />
                            Approve
                          </button>
                          <button
                            onClick={() =>
                              handleRejectResource(
                                resource.contractor_business_id,
                                resource.resource_type_id,
                              )
                            }
                            className="flex-1 px-4 py-2 bg-red-600 hover:bg-red-500 text-white font-medium rounded-lg transition flex items-center justify-center gap-2"
                          >
                            <XCircle className="w-4 h-4" />
                            Reject
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Pending Individual Contractor Rate Exceptions */}
              <div>
                <div className="flex items-center gap-2 mb-4">
                  <User className="w-5 h-5 text-violet-600 dark:text-violet-400" />
                  <h3 className="text-lg font-bold text-aegisaxiom-text">
                    Individual Contractor Rate Exceptions
                  </h3>
                  <span className="text-sm text-aegisaxiom-text-secondary">
                    ({pendingIndividualRates.length})
                  </span>
                </div>

                {pendingIndividualRates.length === 0 ? (
                  <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-6 text-center">
                    <CheckCircle className="w-10 h-10 text-green-500 mx-auto mb-2" />
                    <p className="text-aegisaxiom-text-secondary text-sm">
                      No pending individual rate approvals
                    </p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {pendingIndividualRates.map((rate) => (
                      <div
                        key={`${rate.user_id}-${rate.resource_type_id}`}
                        className="bg-aegisaxiom-surface border border-orange-500/40 rounded-lg p-4"
                      >
                        <div className="flex items-start justify-between mb-3">
                          <div className="flex-1">
                            <div className="flex items-center gap-2 mb-2">
                              <h4 className="font-semibold text-aegisaxiom-text">{rate.user_name}</h4>
                              <span className="px-2 py-1 rounded text-xs bg-orange-50 dark:bg-orange-900/40 text-orange-700 dark:text-orange-300 border border-orange-500/40">
                                ⏳ Rate Exception
                              </span>
                            </div>
                            <p className="text-xs text-aegisaxiom-text-secondary mb-1">
                              <strong>Resource Type:</strong> {rate.resource_type_name}
                            </p>
                            <p className="text-xs text-aegisaxiom-text-secondary mb-1">
                              <strong>Business:</strong> {rate.contractor_business_name}
                            </p>
                            <p className="text-xs text-aegisaxiom-text-secondary">
                              <strong>Email:</strong> {rate.user_email}
                            </p>
                          </div>
                        </div>

                        <div className="grid grid-cols-3 gap-4 text-sm mb-4 p-3 bg-orange-50 dark:bg-orange-900/10 border border-orange-200 dark:border-orange-700/40 rounded">
                          <div>
                            <span className="text-aegisaxiom-text-secondary block mb-1">
                              Business Approved Rate
                            </span>
                            <span className="text-aegisaxiom-text-secondary font-semibold">
                              ${rate.business_approved_rate?.toFixed(0) || 0}/hr
                            </span>
                          </div>
                          <div>
                            <span className="text-aegisaxiom-text-secondary block mb-1">
                              Requested Rate
                            </span>
                            <span className="text-red-700 dark:text-red-300 font-semibold">
                              ${rate.requested_rate?.toFixed(0)}/hr
                            </span>
                          </div>
                          <div>
                            <span className="text-aegisaxiom-text-secondary block mb-1">Overage</span>
                            <span className="text-red-600 dark:text-red-400 font-semibold">
                              +${rate.overage?.toFixed(0)}/hr
                            </span>
                          </div>
                        </div>

                        <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700/40 rounded-lg p-3 text-xs text-amber-700 dark:text-amber-300 mb-3">
                          <strong>ℹ️ Note:</strong> This contractor is requesting a rate higher than
                          their business's approved rate. Approving will allow them to be billed at
                          ${rate.requested_rate?.toFixed(0)}/hr. Rejecting will revert them to the
                          business rate of ${rate.business_approved_rate?.toFixed(0)}/hr.
                        </div>

                        <div className="flex gap-2">
                          <button
                            onClick={() =>
                              handleApproveIndividualRate(rate.user_id, rate.resource_type_id)
                            }
                            className="flex-1 px-4 py-2 bg-green-600 hover:bg-green-500 text-white font-medium rounded-lg transition flex items-center justify-center gap-2"
                          >
                            <CheckCircle className="w-4 h-4" />
                            Approve Exception
                          </button>
                          <button
                            onClick={() =>
                              handleRejectIndividualRate(rate.user_id, rate.resource_type_id)
                            }
                            className="flex-1 px-4 py-2 bg-red-600 hover:bg-red-500 text-white font-medium rounded-lg transition flex items-center justify-center gap-2"
                          >
                            <XCircle className="w-4 h-4" />
                            Reject & Revert
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {/* ANALYTICS VIEW */}
      {activeView === 'analytics' && <AnalyticsView />}

      {/* DISTRIBUTORS VIEW */}
      {activeView === 'distributors' && (
        <div className="space-y-6">
          {distributors.length === 0 ? (
            <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-8 text-center">
              <Building2 className="w-16 h-16 text-aegisaxiom-text-muted mx-auto mb-3" />
              <h3 className="text-lg font-semibold text-aegisaxiom-text mb-2">No Distributors Yet</h3>
              <p className="text-aegisaxiom-text-secondary text-sm">
                Add distributors from Products &amp; Services → Admin. Once added, link them to
                hardware and material products and they'll appear here.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {distributors.map((d) => {
                const stats = distributorStats[d.distributor_id] || {
                  linked_product_count: 0,
                  open_request_count: 0,
                };
                return (
                  <button
                    type="button"
                    key={d.distributor_id}
                    onClick={() => openDistributorDetail(d)}
                    className="text-left bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4 hover:border-violet-500 transition"
                  >
                    <div className="flex items-start justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <Building2 className="w-5 h-5 text-violet-500" />
                        <h3 className="text-base font-semibold text-aegisaxiom-text">{d.name}</h3>
                      </div>
                      <span
                        className={`text-xs px-2 py-0.5 rounded ${
                          d.status === 'active'
                            ? 'bg-emerald-500/10 text-emerald-500'
                            : 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted'
                        }`}
                      >
                        {d.status}
                      </span>
                    </div>
                    {d.contact_name && (
                      <p className="text-xs text-aegisaxiom-text-secondary mb-1">
                        Contact: {d.contact_name}
                      </p>
                    )}
                    {d.website && (
                      <p className="text-xs text-aegisaxiom-text-muted truncate mb-3">{d.website}</p>
                    )}
                    <div className="flex items-center gap-4 text-xs text-aegisaxiom-text-secondary mt-3 pt-3 border-t border-aegisaxiom-border">
                      <span className="flex items-center gap-1">
                        <Package className="w-3.5 h-3.5" />
                        {stats.linked_product_count} product
                        {stats.linked_product_count === 1 ? '' : 's'}
                      </span>
                      <span className="flex items-center gap-1">
                        <Clock className="w-3.5 h-3.5" />
                        {stats.open_request_count} open request
                        {stats.open_request_count === 1 ? '' : 's'}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* REQUESTS VIEW */}
      {activeView === 'requests' && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <label className="text-sm text-aegisaxiom-text-secondary">Status:</label>
            <select
              value={requestsStatusFilter}
              onChange={(e) => setRequestsStatusFilter(e.target.value)}
              className="px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
            >
              <option value="all">All</option>
              <option value="open">open</option>
              <option value="sourcing">sourcing</option>
              <option value="ordered">ordered</option>
              <option value="received">received</option>
              <option value="cancelled">cancelled</option>
            </select>
          </div>
          {requestsList.length === 0 ? (
            <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-8 text-center">
              <Package className="w-12 h-12 text-aegisaxiom-text-muted mx-auto mb-2" />
              <p className="text-sm text-aegisaxiom-text-secondary">
                No requests matching "{requestsStatusFilter}".
              </p>
              <p className="text-xs text-aegisaxiom-text-muted mt-1">
                Requests are created from the Procurement tab of an opportunity's detail view.
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs text-aegisaxiom-text-secondary border-b border-aegisaxiom-border">
                  <tr>
                    <th className="text-left py-2 px-2">Opportunity</th>
                    <th className="text-left py-2 px-2">Item</th>
                    <th className="text-left py-2 px-2">Distributor</th>
                    <th className="text-left py-2 px-2">Qty</th>
                    <th className="text-left py-2 px-2">Priority</th>
                    <th className="text-left py-2 px-2">Needed by</th>
                    <th className="text-left py-2 px-2">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {requestsList.map((r) => (
                    <tr
                      key={r.request_id}
                      className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-elevated/50"
                    >
                      <td className="py-2 px-2 text-aegisaxiom-text truncate max-w-xs">
                        {r.opportunity_name || r.opportunity_id}
                      </td>
                      <td className="py-2 px-2 text-aegisaxiom-text">
                        {r.product_name || r.item_description || '—'}
                      </td>
                      <td className="py-2 px-2 text-aegisaxiom-text-secondary">
                        {r.distributor_name || '—'}
                      </td>
                      <td className="py-2 px-2 text-aegisaxiom-text-secondary">{r.quantity}</td>
                      <td className="py-2 px-2 text-aegisaxiom-text-secondary">{r.priority}</td>
                      <td className="py-2 px-2 text-aegisaxiom-text-secondary">
                        {r.needed_by_date || '—'}
                      </td>
                      <td className="py-2 px-2">
                        <select
                          value={r.status}
                          onChange={(e) => updateRequestStatus(r.request_id, e.target.value)}
                          className="px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text"
                        >
                          <option value="open">open</option>
                          <option value="sourcing">sourcing</option>
                          <option value="ordered">ordered</option>
                          <option value="received">received</option>
                          <option value="cancelled">cancelled</option>
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Distributor Detail Modal */}
      {selectedDistributor && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
          <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between p-6 border-b border-aegisaxiom-border">
              <div>
                <h3 className="text-xl font-bold text-aegisaxiom-text mb-1">
                  {selectedDistributor.name}
                </h3>
                <p className="text-sm text-aegisaxiom-text-secondary">
                  Status: {selectedDistributor.status}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setSelectedDistributor(null)}
                className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
              >
                <XCircle className="w-6 h-6" />
              </button>
            </div>
            <div className="p-6 space-y-4">
              <div className="grid grid-cols-2 gap-4 text-sm">
                {selectedDistributor.contact_name && (
                  <div>
                    <span className="text-aegisaxiom-text-secondary">Contact:</span>
                    <p className="text-aegisaxiom-text font-medium mt-1">
                      {selectedDistributor.contact_name}
                    </p>
                  </div>
                )}
                {selectedDistributor.contact_email && (
                  <div>
                    <span className="text-aegisaxiom-text-secondary">Email:</span>
                    <p className="text-aegisaxiom-text font-medium mt-1 truncate">
                      {selectedDistributor.contact_email}
                    </p>
                  </div>
                )}
                {selectedDistributor.contact_phone && (
                  <div>
                    <span className="text-aegisaxiom-text-secondary">Phone:</span>
                    <p className="text-aegisaxiom-text font-medium mt-1">
                      {selectedDistributor.contact_phone}
                    </p>
                  </div>
                )}
                {selectedDistributor.website &&
                  (() => {
                    // Defense-in-depth against stored javascript:/data: URIs.
                    const safeUrl = /^https?:\/\//i.test(selectedDistributor.website)
                      ? selectedDistributor.website
                      : null;
                    return (
                      <div>
                        <span className="text-aegisaxiom-text-secondary">Website:</span>
                        {safeUrl ? (
                          <a
                            href={safeUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="text-violet-500 font-medium mt-1 block truncate hover:underline"
                          >
                            {safeUrl}
                          </a>
                        ) : (
                          <p className="text-aegisaxiom-text font-medium mt-1 truncate">
                            {selectedDistributor.website}
                          </p>
                        )}
                      </div>
                    );
                  })()}
              </div>
              {selectedDistributor.notes && (
                <div className="pt-3 border-t border-aegisaxiom-border">
                  <span className="text-aegisaxiom-text-secondary text-sm">Notes:</span>
                  <p className="text-aegisaxiom-text-secondary mt-1 text-sm">
                    {selectedDistributor.notes}
                  </p>
                </div>
              )}
              <div className="pt-3 border-t border-aegisaxiom-border">
                <h4 className="text-sm font-semibold text-aegisaxiom-text mb-2">
                  Linked Products ({distributorProducts.length})
                </h4>
                {distributorProducts.length === 0 ? (
                  <p className="text-xs text-aegisaxiom-text-muted">No products linked yet.</p>
                ) : (
                  <ul className="space-y-1">
                    {distributorProducts.map((p) => (
                      <li key={p.product_id} className="text-xs text-aegisaxiom-text-secondary">
                        {p.name} — {p.sku}
                        {p.distributor_sku ? ` (dist SKU: ${p.distributor_sku})` : ''}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* SOURCING VIEW — Procurement Bot + Quote Scaffold (Ticket 9).
          Mode B (manual review) per
          docs/sales-ops/PROCUREMENT_BOT_SCAFFOLD.md. Every quote
          arrives selected=False; the route never auto-selects.
          Vendor names are data-driven from the webhook payload —
          no real vendor names are hardcoded in this file. */}
      {activeView === 'sourcing' && (
        <div className="space-y-4" data-testid="procurement-sourcing-view">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <h3 className="text-lg font-semibold text-aegisaxiom-text">
              Sourcing requests
            </h3>
            <select
              value={sourcingStatusFilter}
              onChange={(e) => setSourcingStatusFilter(e.target.value)}
              className="px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
              data-testid="procurement-sourcing-status-filter"
            >
              <option value="all">All statuses</option>
              <option value="submitted">Submitted</option>
              <option value="sourcing_in_progress">Sourcing in progress</option>
              <option value="review_required">Review required</option>
              <option value="quote_selected">Quote selected</option>
              <option value="ordered">Ordered</option>
              <option value="delivered">Delivered</option>
              <option value="failed">Failed</option>
            </select>
          </div>
          {sourcingList.length === 0 ? (
            <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-8 text-center">
              <Package className="w-10 h-10 text-aegisaxiom-text-muted mx-auto mb-2" />
              <p className="text-sm text-aegisaxiom-text-secondary">
                No sourcing requests at this status.
              </p>
              <p className="text-xs text-aegisaxiom-text-muted mt-2">
                Manual-review queue. Vendor automation runs in a separate service.
              </p>
            </div>
          ) : (
            <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-aegisaxiom-elevated text-aegisaxiom-text-secondary text-xs uppercase">
                  <tr>
                    <th className="text-left px-4 py-3">Request</th>
                    <th className="text-left px-4 py-3">Opportunity</th>
                    <th className="text-left px-4 py-3">Status</th>
                    <th className="text-left px-4 py-3">Manual review</th>
                    <th className="text-left px-4 py-3">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-aegisaxiom-border">
                  {sourcingList.map((s) => (
                    <tr key={s.sourcing_request_id} className="hover:bg-aegisaxiom-elevated/40">
                      <td className="px-4 py-3 text-aegisaxiom-text font-mono text-xs">
                        {s.sourcing_request_id}
                      </td>
                      <td className="px-4 py-3 text-aegisaxiom-text-secondary text-xs font-mono">
                        {s.opportunity_id}
                      </td>
                      <td className="px-4 py-3">
                        <span className="px-2 py-0.5 rounded text-xs font-medium bg-violet-50 dark:bg-violet-500/20 text-violet-700 dark:text-violet-300 border border-violet-500/30">
                          {s.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-aegisaxiom-text-secondary">
                        {s.manual_review_required
                          ? s.manual_review_reason || 'pending review'
                          : 'no'}
                      </td>
                      <td className="px-4 py-3">
                        <button
                          onClick={() => openSourcingDetail(s.sourcing_request_id)}
                          className="px-3 py-1 bg-aegisaxiom-elevated hover:bg-aegisaxiom-border text-aegisaxiom-text text-xs rounded transition"
                          data-testid={`procurement-sourcing-open-${s.sourcing_request_id}`}
                        >
                          Review quotes
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Detail panel: quote review for the selected sourcing
              request. Renders only when openSourcingDetail has
              loaded a request. */}
          {sourcingDetail && (
            <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-5 space-y-3">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div>
                  <h4 className="font-semibold text-aegisaxiom-text">
                    {sourcingDetail.sourcing_request?.sourcing_request_id}
                  </h4>
                  <p className="text-xs text-aegisaxiom-text-secondary">
                    Opportunity:{' '}
                    {sourcingDetail.sourcing_request?.opportunity_id}
                    {sourcingDetail.sourcing_request?.manual_review_reason && (
                      <>
                        {' · '}Manual review:{' '}
                        {sourcingDetail.sourcing_request.manual_review_reason}
                      </>
                    )}
                  </p>
                </div>
                <div className="flex gap-2">
                  {sourcingDetail.sourcing_request?.status === 'quote_selected' && (
                    <button
                      onClick={() =>
                        markSourcingOrdered(
                          sourcingDetail.sourcing_request.sourcing_request_id,
                        )
                      }
                      className="px-3 py-1.5 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded transition"
                      data-testid="procurement-sourcing-mark-ordered"
                    >
                      Mark ordered
                    </button>
                  )}
                  {sourcingDetail.sourcing_request?.status === 'ordered' && (
                    <button
                      onClick={() =>
                        markSourcingDelivered(
                          sourcingDetail.sourcing_request.sourcing_request_id,
                        )
                      }
                      className="px-3 py-1.5 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded transition"
                      data-testid="procurement-sourcing-mark-delivered"
                    >
                      Mark delivered
                    </button>
                  )}
                </div>
              </div>

              {sourcingDetailLoading ? (
                <p className="text-sm text-aegisaxiom-text-secondary">Loading…</p>
              ) : sourcingDetail.quotes && sourcingDetail.quotes.length > 0 ? (
                <div className="overflow-hidden border border-aegisaxiom-border rounded">
                  <table className="w-full text-xs">
                    <thead className="bg-aegisaxiom-elevated text-aegisaxiom-text-secondary uppercase">
                      <tr>
                        <th className="text-left px-3 py-2">Vendor</th>
                        <th className="text-left px-3 py-2">Price</th>
                        <th className="text-left px-3 py-2">Availability</th>
                        <th className="text-left px-3 py-2">ETA</th>
                        <th className="text-left px-3 py-2">Confidence</th>
                        <th className="text-left px-3 py-2">Source</th>
                        <th className="text-left px-3 py-2">Manual review</th>
                        <th className="text-left px-3 py-2">Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-aegisaxiom-border">
                      {sourcingDetail.quotes.map((q) => (
                        <tr key={q.quote_id} className="hover:bg-aegisaxiom-elevated/40">
                          <td className="px-3 py-2 text-aegisaxiom-text">
                            {q.vendor_name}
                            {q.selected && (
                              <span className="ml-2 px-1.5 py-0.5 rounded bg-green-50 dark:bg-green-500/20 text-green-700 dark:text-green-300 border border-green-500/30 text-[10px] uppercase tracking-wide">
                                selected
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-aegisaxiom-text">
                            {q.price != null
                              ? `${q.price} ${q.currency || ''}`
                              : '—'}
                          </td>
                          <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                            {q.availability || '—'}
                          </td>
                          <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                            {q.delivery_eta || '—'}
                          </td>
                          <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                            {q.confidence != null ? q.confidence : '—'}
                          </td>
                          <td className="px-3 py-2">
                            {q.source_url &&
                            /^https?:\/\//i.test(q.source_url) ? (
                              <a
                                href={q.source_url}
                                target="_blank"
                                rel="noreferrer"
                                className="text-violet-600 dark:text-violet-300 hover:underline"
                              >
                                view
                              </a>
                            ) : (
                              <span className="text-amber-600 dark:text-amber-400">
                                {q.source_url ? 'unsafe url' : 'missing'}
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-xs text-aegisaxiom-text-secondary">
                            {q.manual_review_reason || '—'}
                          </td>
                          <td className="px-3 py-2">
                            {!q.selected &&
                              ['review_required', 'quote_selected'].includes(
                                sourcingDetail.sourcing_request?.status,
                              ) && (
                                <button
                                  onClick={() =>
                                    selectSourcingQuote(
                                      sourcingDetail.sourcing_request
                                        .sourcing_request_id,
                                      q.quote_id,
                                    )
                                  }
                                  className="px-2 py-1 bg-violet-600 hover:bg-violet-500 text-white rounded transition"
                                  data-testid={`procurement-sourcing-select-quote-${q.quote_id}`}
                                >
                                  Select
                                </button>
                              )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-sm text-aegisaxiom-text-muted">
                  No quotes yet. Awaiting bot response or manual entry.
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {/* PRICING QUEUE VIEW — direct SOW item lifecycle (manual portal pricing).
          Separate substrate from the sourcing-request scaffold above. */}
      {activeView === 'pricing_queue' && (
        <div className="space-y-4" data-testid="procurement-pricing-queue-view">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div>
              <h3 className="text-lg font-semibold text-aegisaxiom-text">
                Pricing queue
              </h3>
              <p className="text-xs text-aegisaxiom-text-muted mt-1">
                Field Services SOW items that need a price, supplier source, and availability check.
                Marking ordered moves the item out of this view; PMO marks delivered from the scope checklist.
              </p>
            </div>
            <button
              onClick={loadPricingQueue}
              className="px-3 py-1.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-border text-aegisaxiom-text text-xs rounded transition"
              data-testid="procurement-pricing-queue-refresh"
            >
              Refresh
            </button>
          </div>

          {pricingQueueLoading ? (
            <div className="flex items-center justify-center py-8">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-violet-500"></div>
            </div>
          ) : pricingQueue.length === 0 ? (
            <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-8 text-center">
              <DollarSign className="w-10 h-10 text-aegisaxiom-text-muted mx-auto mb-2" />
              <p className="text-sm text-aegisaxiom-text-secondary">
                No items currently waiting on pricing.
              </p>
              <p className="text-xs text-aegisaxiom-text-muted mt-2">
                Items marked "Needs sourcing" by Sales Ops on a Field Services SOW will appear here.
              </p>
            </div>
          ) : (
            <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-aegisaxiom-elevated text-aegisaxiom-text-secondary text-xs uppercase">
                  <tr>
                    <th className="text-left px-4 py-3">Item</th>
                    <th className="text-left px-4 py-3">Category</th>
                    <th className="text-left px-4 py-3">Qty</th>
                    <th className="text-left px-4 py-3">Unit price</th>
                    <th className="text-left px-4 py-3">Supplier</th>
                    <th className="text-left px-4 py-3">Source URL</th>
                    <th className="text-left px-4 py-3">Availability</th>
                    <th className="text-left px-4 py-3">Status</th>
                    <th className="text-left px-4 py-3">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-aegisaxiom-border">
                  {pricingQueue.map((row) => {
                    const key = pricingRowKey(row);
                    const draft = pricingRowDraft[key] || {};
                    const orderDraft = orderingRowDraft[key] || {};
                    const isPriced = row.procurement_status === 'priced';
                    const isSaving = savingRowKey === key;
                    return (
                      <tr key={key} className="hover:bg-aegisaxiom-elevated/40 align-top">
                        <td className="px-4 py-3 text-aegisaxiom-text">
                          <div className="font-medium">{row.product_name}</div>
                          <div className="text-xs text-aegisaxiom-text-muted font-mono mt-0.5">
                            {row.opportunity_id}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-xs text-aegisaxiom-text-secondary">
                          {row.item_category}
                        </td>
                        <td className="px-4 py-3 text-xs text-aegisaxiom-text">
                          {row.quantity}{row.unit ? ` ${row.unit}` : ''}
                        </td>
                        <td className="px-4 py-3">
                          <input
                            type="number"
                            min="0"
                            step="0.01"
                            placeholder={row.unit_price ? String(row.unit_price) : '0.00'}
                            value={draft.unit_price ?? ''}
                            onChange={(e) => updatePricingDraft(row, 'unit_price', e.target.value)}
                            className="w-24 px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs focus:outline-none focus:border-violet-500"
                            data-testid={`pricing-row-unit-price-${row.item_id}`}
                          />
                        </td>
                        <td className="px-4 py-3">
                          <input
                            type="text"
                            placeholder={row.supplier_name || ''}
                            value={draft.supplier_name ?? ''}
                            onChange={(e) => updatePricingDraft(row, 'supplier_name', e.target.value)}
                            className="w-32 px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs focus:outline-none focus:border-violet-500"
                            data-testid={`pricing-row-supplier-${row.item_id}`}
                          />
                        </td>
                        <td className="px-4 py-3">
                          <input
                            type="url"
                            placeholder="https://"
                            value={draft.supplier_source_url ?? ''}
                            onChange={(e) => updatePricingDraft(row, 'supplier_source_url', e.target.value)}
                            className="w-40 px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs focus:outline-none focus:border-violet-500"
                            data-testid={`pricing-row-source-url-${row.item_id}`}
                          />
                        </td>
                        <td className="px-4 py-3">
                          <input
                            type="text"
                            placeholder={row.availability_note || ''}
                            value={draft.availability_note ?? ''}
                            onChange={(e) => updatePricingDraft(row, 'availability_note', e.target.value)}
                            className="w-32 px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs focus:outline-none focus:border-violet-500"
                            data-testid={`pricing-row-availability-${row.item_id}`}
                          />
                        </td>
                        <td className="px-4 py-3">
                          {isPriced ? (
                            <span className="px-2 py-0.5 rounded text-xs font-medium bg-blue-50 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300 border border-blue-500/30">
                              priced
                            </span>
                          ) : (
                            <span className="px-2 py-0.5 rounded text-xs font-medium bg-amber-50 dark:bg-amber-500/20 text-amber-700 dark:text-amber-300 border border-amber-500/30">
                              {row.procurement_status || 'pending_pricing'}
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3 space-y-1.5">
                          <button
                            onClick={() => savePricingRow(row)}
                            disabled={isSaving}
                            className="block w-full px-3 py-1 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white text-xs rounded transition"
                            data-testid={`pricing-row-save-${row.item_id}`}
                          >
                            {isSaving ? 'Saving…' : isPriced ? 'Update pricing' : 'Save pricing'}
                          </button>
                          {isPriced && (
                            <div className="mt-1 pt-1.5 border-t border-aegisaxiom-border space-y-1">
                              <input
                                type="text"
                                placeholder="Order reference"
                                value={orderDraft.order_reference ?? ''}
                                onChange={(e) => updateOrderingDraft(row, 'order_reference', e.target.value)}
                                className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs focus:outline-none focus:border-violet-500"
                                data-testid={`pricing-row-order-ref-${row.item_id}`}
                              />
                              <input
                                type="text"
                                placeholder="Tracking #"
                                value={orderDraft.tracking_number ?? ''}
                                onChange={(e) => updateOrderingDraft(row, 'tracking_number', e.target.value)}
                                className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs focus:outline-none focus:border-violet-500"
                                data-testid={`pricing-row-tracking-${row.item_id}`}
                              />
                              <button
                                onClick={() => markRowOrdered(row)}
                                disabled={isSaving}
                                className="block w-full px-3 py-1 bg-aegisaxiom-elevated hover:bg-aegisaxiom-border disabled:opacity-50 text-aegisaxiom-text text-xs rounded transition border border-aegisaxiom-border"
                                data-testid={`pricing-row-mark-ordered-${row.item_id}`}
                              >
                                Mark ordered
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Notification Detail Modal */}
      {showNotificationModal && selectedNotification && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
          <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between p-6 border-b border-aegisaxiom-border">
              <div>
                <h3 className="text-xl font-bold text-aegisaxiom-text mb-1">Resource Request Details</h3>
                <p className="text-sm text-aegisaxiom-text-secondary">
                  {selectedNotification.resource_type_name}
                </p>
              </div>
              <button
                onClick={() => setShowNotificationModal(false)}
                className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
              >
                <XCircle className="w-6 h-6" />
              </button>
            </div>
            <div className="p-6 space-y-4">
              <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-4">
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <span className="text-aegisaxiom-text-secondary">Resource Type:</span>
                    <p className="text-aegisaxiom-text font-medium mt-1">
                      {selectedNotification.resource_type_name}
                    </p>
                  </div>
                  <div>
                    <span className="text-aegisaxiom-text-secondary">Requested By:</span>
                    <p className="text-aegisaxiom-text font-medium mt-1">
                      {selectedNotification.created_by_name}
                    </p>
                  </div>
                  <div>
                    <span className="text-aegisaxiom-text-secondary">Date:</span>
                    <p className="text-aegisaxiom-text font-medium mt-1">
                      {new Date(selectedNotification.created_at).toLocaleDateString()}
                    </p>
                  </div>
                  {selectedNotification.opportunity_id && (
                    <div>
                      <span className="text-aegisaxiom-text-secondary">Opportunity:</span>
                      <p className="text-aegisaxiom-text font-medium mt-1 text-xs truncate">
                        {selectedNotification.opportunity_id}
                      </p>
                    </div>
                  )}
                </div>
                {selectedNotification.message && (
                  <div className="mt-3 pt-3 border-t border-aegisaxiom-border">
                    <span className="text-aegisaxiom-text-secondary text-sm">Message:</span>
                    <p className="text-aegisaxiom-text-secondary mt-1">{selectedNotification.message}</p>
                  </div>
                )}
              </div>
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  Assign To
                </label>
                <select
                  value={assignedTo}
                  onChange={(e) => setAssignedTo(e.target.value)}
                  className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                >
                  <option value="">-- Unassigned --</option>
                  {procurementUsers.map((user) => (
                    <option key={user.user_id} value={user.user_id}>
                      {user.name} ({user.role})
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  Notes
                </label>
                <textarea
                  value={notificationNotes}
                  onChange={(e) => setNotificationNotes(e.target.value)}
                  className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text min-h-[120px]"
                  placeholder="Add notes about your progress, contacts made, next steps, etc."
                />
              </div>
              <div className="flex gap-3 pt-4">
                <button
                  onClick={handleSaveNotificationDetails}
                  className="flex-1 px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white font-medium rounded-lg transition"
                >
                  Save Changes
                </button>
                <button
                  onClick={() => handleResolveNotification(selectedNotification.notification_id)}
                  className="px-4 py-2 bg-green-600 hover:bg-green-500 text-white font-medium rounded-lg transition"
                >
                  Mark as Resolved
                </button>
                <button
                  onClick={() => setShowNotificationModal(false)}
                  className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm font-medium rounded-lg transition"
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Analytics subview
// ---------------------------------------------------------------------------

const DEMO_TOP_REVENUE = [
  { name: 'SimpliSecure Platinum', value: 127500 },
  { name: 'Microsoft 365 Business Premium', value: 89200 },
  { name: 'SimpliSecure Gold', value: 64300 },
];

const DEMO_BEST_MARGINS = [
  { name: 'SimpliComply Enterprise', value: 48.5 },
  { name: 'Field Service - Project Manager', value: 42.3 },
  { name: 'SimpliSecure Bronze', value: 38.7 },
];

const DEMO_WORST_MARGINS = [
  { name: 'Hardware Bundle A', value: 8.2 },
  { name: 'Network Switch Pro', value: 12.5 },
  { name: 'Cable Management Kit', value: 15.8 },
];

function BarRow({ label, value, max, formatValue, colorClass }) {
  const pct = max > 0 ? Math.max(4, (value / max) * 100) : 0;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-3">
        <span className="text-aegisaxiom-text text-sm truncate">{label}</span>
        <span className={`${colorClass} font-semibold text-sm tabular-nums shrink-0`}>
          {formatValue(value)}
        </span>
      </div>
      <div className="h-1.5 w-full bg-aegisaxiom-elevated rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${colorClass.replace('text-', 'bg-').replace('dark:text-', 'dark:bg-').split(' ')[0]}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function AnalyticsCard({ title, icon: Icon, iconColor, items, colorClass, formatValue }) {
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-5 sm:p-6">
      <div className="flex items-center gap-2 mb-5">
        <Icon className={`w-5 h-5 ${iconColor}`} />
        <h3 className="text-base sm:text-lg font-bold text-aegisaxiom-text">{title}</h3>
      </div>
      <div className="space-y-4">
        {items.map((item, idx) => (
          <BarRow
            key={idx}
            label={item.name}
            value={item.value}
            max={max}
            formatValue={formatValue}
            colorClass={colorClass}
          />
        ))}
      </div>
    </div>
  );
}

function AnalyticsView() {
  const fmtCurrency = (n) => `$${n.toLocaleString()}/mo`;
  const fmtPct = (n) => `${n.toFixed(1)}%`;

  return (
    <div className="space-y-5 sm:space-y-6">
      <div className="flex items-center gap-2 text-xs text-aegisaxiom-text-muted">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
        <span>
          Showing sample data — will switch to live figures once the data pipeline is wired up.
        </span>
      </div>

      <AnalyticsCard
        title="Top Products by Revenue"
        icon={TrendingUp}
        iconColor="text-green-600 dark:text-green-400"
        items={DEMO_TOP_REVENUE}
        colorClass="text-green-600 dark:text-green-400"
        formatValue={fmtCurrency}
      />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 sm:gap-6">
        <AnalyticsCard
          title="Best Margin Products"
          icon={TrendingUp}
          iconColor="text-green-600 dark:text-green-400"
          items={DEMO_BEST_MARGINS}
          colorClass="text-green-600 dark:text-green-400"
          formatValue={fmtPct}
        />
        <AnalyticsCard
          title="Worst Margin Products"
          icon={TrendingDown}
          iconColor="text-red-600 dark:text-red-400"
          items={DEMO_WORST_MARGINS}
          colorClass="text-red-600 dark:text-red-400"
          formatValue={fmtPct}
        />
      </div>
    </div>
  );
}
