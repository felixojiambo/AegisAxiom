import React, { useState } from 'react';
import {
  Clock,
  FileText,
  AlertCircle,
  Eye,
  CheckCircle,
  DollarSign,
  TrendingUp,
  Users,
  Building2,
  GripVertical,
  User,
  Calendar,
} from 'lucide-react';
import { AlertBadge } from '../shared/AlertBanner';

// Status configuration (Blocked removed - critical issues shown in separate section)
const STATUS_CONFIG = {
  not_started: {
    label: 'Not Started',
    color: 'bg-aegisaxiom-elevated',
    textColor: 'text-aegisaxiom-text',
    icon: Clock,
  },
  in_progress: {
    label: 'In Progress',
    color: 'bg-blue-500',
    textColor: 'text-blue-100',
    icon: FileText,
  },
  ready_for_review: {
    label: 'Ready for Review',
    color: 'bg-yellow-500',
    textColor: 'text-yellow-100',
    icon: Eye,
  },
  complete: {
    label: 'Complete',
    color: 'bg-green-500',
    textColor: 'text-green-100',
    icon: CheckCircle,
  },
};

const STATUS_ORDER = ['not_started', 'in_progress', 'ready_for_review', 'complete'];

const LOB_COLORS = {
  field_services: 'bg-blue-50 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300 border-blue-500/30',
  professional_services: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
  wholesale_mrr: 'bg-green-50 dark:bg-green-500/20 text-green-700 dark:text-green-300 border-green-500/30',
  referral_mrr: 'bg-amber-50 dark:bg-amber-500/20 text-amber-700 dark:text-amber-300 border-amber-500/30',
};

const LOB_LABELS = {
  field_services: 'Field Services',
  professional_services: 'Professional Services',
  wholesale_mrr: 'Wholesale MRR',
  referral_mrr: 'Referral MRR',
};

export default function SolutionsDesignKanban({
  sowTasks,
  salesOpsUsers = [],
  opportunityAlerts = {},
  onStatusChange,
  onViewSOW,
  onAssignSOW,
}) {
  const [draggedSOW, setDraggedSOW] = useState(null);

  // Separate critical SOWs from regular SOWs (Hybrid Approach - Option C)
  const criticalSOWs = sowTasks.filter((sow) => {
    const alerts = opportunityAlerts[sow.opportunity_id] || [];
    const hasCriticalAlerts = alerts.some((a) => a.severity === 'critical' && !a.dismissed);
    // Show in Critical section if has blockers OR critical alerts
    // Defensive check: treat undefined/null has_critical_blockers as false
    const hasBlockers = sow.has_critical_blockers === true;
    return hasBlockers || hasCriticalAlerts;
  });

  const normalSOWs = sowTasks.filter((sow) => {
    const alerts = opportunityAlerts[sow.opportunity_id] || [];
    const hasCriticalAlerts = alerts.some((a) => a.severity === 'critical' && !a.dismissed);
    const hasBlockers = sow.has_critical_blockers === true;
    // Show in normal columns if not critical AND status is in workflow stages
    // Exclude 'blocked' status from normal columns (these go to critical section)
    return !hasBlockers && !hasCriticalAlerts && sow.status !== 'blocked';
  });

  const handleDragStart = (e, sow) => {
    // Don't allow dragging critical SOWs
    const alerts = opportunityAlerts[sow.opportunity_id] || [];
    const hasCriticalAlerts = alerts.some((a) => a.severity === 'critical' && !a.dismissed);

    if (sow.has_critical_blockers || hasCriticalAlerts) {
      e.preventDefault();
      return;
    }

    setDraggedSOW(sow);
    e.dataTransfer.effectAllowed = 'move';
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  };

  const handleDrop = async (e, targetStatus) => {
    e.preventDefault();
    if (!draggedSOW || draggedSOW.status === targetStatus) {
      setDraggedSOW(null);
      return;
    }

    await onStatusChange(draggedSOW.sow_id, targetStatus);
    setDraggedSOW(null);
  };

  const formatCurrency = (amount) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
    }).format(amount || 0);
  };

  const renderSOWCard = (sow, isCritical = false) => {
    // Calculate blocker info from resource assignments (if available)
    const hasBlockers = sow.has_critical_blockers === true || sow.status === 'blocked';
    const resourceCount = (sow.selected_resource_assignment_ids || []).length;

    // Check for date warning (Expected > Desired)
    const hasDateWarning =
      sow.expected_completion_date &&
      sow.desired_proposal_date &&
      new Date(sow.expected_completion_date) > new Date(sow.desired_proposal_date);

    // Get alerts for this opportunity
    const alerts = opportunityAlerts[sow.opportunity_id] || [];
    const criticalAlerts = alerts.filter((a) => a.severity === 'critical' && !a.dismissed);
    const warningAlerts = alerts.filter((a) => a.severity === 'warning' && !a.dismissed);
    const successAlerts = alerts.filter((a) => a.severity === 'success' && !a.dismissed);

    // Prepare blocker text array (for critical section display)
    const blockerTexts = sow.blockers && Array.isArray(sow.blockers) ? sow.blockers : [];

    // Show details if in critical section
    const showCriticalDetails = isCritical;

    return (
      <div
        key={sow.sow_id}
        draggable={!isCritical} // Can't drag critical items
        onDragStart={(e) => handleDragStart(e, sow)}
        className={`bg-aegisaxiom-surface border rounded-lg p-4 transition-all group ${
          isCritical
            ? 'border-red-600/70 bg-red-50 dark:bg-red-900/20 shadow-lg shadow-red-900/20'
            : 'border-aegisaxiom-border cursor-move hover:border-violet-500'
        } ${isCritical ? '' : 'mb-3'}`}
      >
        {/* Header */}
        <div className="flex items-start justify-between mb-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <span
                className={`px-2 py-1 rounded text-xs font-medium border ${LOB_COLORS[sow.line_of_business]}`}
              >
                {LOB_LABELS[sow.line_of_business]}
              </span>

              {/* Alert Badges (NEW) */}
              {criticalAlerts.length > 0 && (
                <AlertBadge severity="critical" count={criticalAlerts.length} />
              )}
              {warningAlerts.length > 0 && (
                <AlertBadge severity="warning" count={warningAlerts.length} />
              )}
              {successAlerts.length > 0 && (
                <AlertBadge severity="success" count={successAlerts.length} />
              )}

              {/* DATE RISK Badge (domain-specific warning) */}
              {hasDateWarning && (
                <span
                  className="flex items-center gap-1 px-2 py-1 bg-yellow-50 dark:bg-yellow-900/40 border border-yellow-200 dark:border-yellow-700/50 rounded text-xs font-semibold text-yellow-700 dark:text-yellow-300"
                  title="Expected completion after desired proposal date"
                >
                  <Calendar className="w-3 h-3" />
                  DATE RISK
                </span>
              )}
            </div>
            <p className="text-sm text-aegisaxiom-text-secondary font-medium truncate">
              {sow.opportunity_name || 'SOW'}
            </p>
          </div>
          {!isCritical && (
            <GripVertical className="w-4 h-4 text-aegisaxiom-text-muted opacity-0 group-hover:opacity-100 flex-shrink-0" />
          )}
        </div>

        {/* Assignment Dropdown */}
        <div className="mb-3">
          <div className="flex items-center gap-2">
            <User className="w-3 h-3 text-aegisaxiom-text-secondary flex-shrink-0" />
            <select
              value={sow.assigned_to || ''}
              onChange={(e) => onAssignSOW && onAssignSOW(sow.sow_id, e.target.value)}
              onClick={(e) => e.stopPropagation()} // Prevent card drag when clicking dropdown
              className="flex-1 px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs focus:outline-none focus:border-violet-500"
            >
              <option value="">Assign to...</option>
              {salesOpsUsers.map((user) => (
                <option key={user.user_id} value={user.user_id}>
                  {user.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Stats */}
        <div className="space-y-2 mb-3">
          <div className="flex items-center justify-between text-sm">
            <span className="text-aegisaxiom-text-secondary flex items-center gap-1">
              <Users className="w-3 h-3" />
              Resources
            </span>
            <span className="text-aegisaxiom-text font-medium">{resourceCount}</span>
          </div>

          {sow.total_amount > 0 && (
            <div className="flex items-center justify-between text-sm">
              <span className="text-aegisaxiom-text-secondary flex items-center gap-1">
                <DollarSign className="w-3 h-3" />
                Amount
              </span>
              <span className="text-green-600 dark:text-green-400 font-semibold">
                {formatCurrency(sow.total_amount)}
              </span>
            </div>
          )}

          {/* Expected Completion Date */}
          {sow.expected_completion_date && (
            <div className="flex items-center justify-between text-xs">
              <span className="text-aegisaxiom-text-muted flex items-center gap-1">
                <Calendar className="w-3 h-3" />
                Expected
              </span>
              <span className={hasDateWarning ? 'text-yellow-700 dark:text-yellow-300 font-semibold' : 'text-aegisaxiom-text-secondary'}>
                {new Date(sow.expected_completion_date).toLocaleDateString()}
              </span>
            </div>
          )}
        </div>

        {/* Critical Details (Blockers or Alert Messages) */}
        {showCriticalDetails && (
          <div className="mb-3 p-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-700/30 rounded">
            {blockerTexts.length > 0 ? (
              <>
                {blockerTexts.slice(0, 2).map((blocker, idx) => (
                  <div
                    key={idx}
                    className="flex items-start gap-1 text-xs text-red-700 dark:text-red-300 mb-1 last:mb-0"
                  >
                    <AlertCircle className="w-3 h-3 flex-shrink-0 mt-0.5" />
                    <span className="line-clamp-1">{blocker}</span>
                  </div>
                ))}
                {blockerTexts.length > 2 && (
                  <p className="text-xs text-red-600 dark:text-red-400 mt-1">+{blockerTexts.length - 2} more...</p>
                )}
              </>
            ) : criticalAlerts.length > 0 ? (
              <>
                {criticalAlerts.slice(0, 2).map((alert, idx) => (
                  <div
                    key={idx}
                    className="flex items-start gap-1 text-xs text-red-700 dark:text-red-300 mb-1 last:mb-0"
                  >
                    <AlertCircle className="w-3 h-3 flex-shrink-0 mt-0.5" />
                    <span className="line-clamp-1">
                      {alert.rule_name}: {alert.message}
                    </span>
                  </div>
                ))}
                {criticalAlerts.length > 2 && (
                  <p className="text-xs text-red-600 dark:text-red-400 mt-1">+{criticalAlerts.length - 2} more...</p>
                )}
              </>
            ) : (
              <div className="flex items-start gap-1 text-xs text-red-700 dark:text-red-300">
                <AlertCircle className="w-3 h-3 flex-shrink-0 mt-0.5" />
                <span className="line-clamp-1">Critical issue - view SOW for details</span>
              </div>
            )}
          </div>
        )}

        {/* View Button */}
        <button
          onClick={() => onViewSOW(sow)}
          className="w-full py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded transition flex items-center justify-center gap-2"
        >
          <Eye className="w-4 h-4" />
          View SOW
        </button>
      </div>
    );
  };

  return (
    <div className="space-y-6">
      {/* Critical Issues Section (Hybrid Approach - Option C) */}
      {criticalSOWs.length > 0 && (
        <div className="bg-red-50 dark:bg-red-900/20 border-2 border-red-600/50 rounded-xl p-5">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-red-600/30 rounded-lg">
              <AlertCircle className="w-6 h-6 text-red-600 dark:text-red-400 animate-pulse" />
            </div>
            <div>
              <h3 className="text-red-700 dark:text-red-300 font-bold text-lg">
                🚨 Critical Issues Requiring Attention
              </h3>
              <p className="text-red-600 dark:text-red-400/80 text-sm">
                {criticalSOWs.length} SOW{criticalSOWs.length > 1 ? 's' : ''} with blockers or
                critical alerts — resolve issues to unblock
              </p>
            </div>
          </div>

          {/* Critical SOW Cards in horizontal scroll */}
          <div className="flex gap-4 overflow-x-auto pb-2">
            {criticalSOWs.map((sow) => (
              <div key={sow.sow_id} className="min-w-[340px]">
                {renderSOWCard(sow, true)}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Regular Kanban Board (4 columns - Blocked removed) */}
      <div className="overflow-x-auto -mx-6 px-6">
        <div className="flex gap-4 pb-4 min-w-min">
          {STATUS_ORDER.map((status) => {
            const config = STATUS_CONFIG[status];
            const StatusIcon = config.icon;
            // Filter out critical SOWs from regular columns
            const sowsInStatus = normalSOWs.filter((s) => s.status === status);
            const totalAmount = sowsInStatus.reduce((sum, s) => sum + (s.total_amount || 0), 0);

            return (
              <div
                key={status}
                onDragOver={handleDragOver}
                onDrop={(e) => handleDrop(e, status)}
                className="flex-shrink-0 w-80 bg-aegisaxiom-base rounded-lg border border-aegisaxiom-border"
              >
                {/* Column Header */}
                <div className={`${config.color} ${config.textColor} p-3 rounded-t-lg`}>
                  <div className="flex items-center gap-2 mb-1">
                    <StatusIcon className="w-4 h-4" />
                    <h3 className="font-semibold text-sm">{config.label}</h3>
                  </div>
                  <div className="flex items-center justify-between text-xs opacity-90">
                    <span>
                      {sowsInStatus.length} SOW{sowsInStatus.length !== 1 ? 's' : ''}
                    </span>
                    {totalAmount > 0 && <span>{formatCurrency(totalAmount)}</span>}
                  </div>
                </div>

                {/* Cards */}
                <div className="p-3 max-h-[calc(100vh-280px)] overflow-y-auto">
                  {sowsInStatus.length === 0 ? (
                    <div className="text-center py-8 text-aegisaxiom-text-muted text-sm">No SOWs</div>
                  ) : (
                    sowsInStatus.map((sow) => renderSOWCard(sow, false))
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
