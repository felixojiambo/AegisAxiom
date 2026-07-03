import React from 'react';
import { AlertTriangle, CheckCircle, Info, X } from 'lucide-react';

/**
 * Reusable Alert Banner Component
 * Used to display automation-triggered alerts in opportunity cards, detail modals, and dashboards
 */

const SEVERITY_CONFIG = {
  success: {
    bg: 'bg-green-50 dark:bg-green-900/30',
    border: 'border-green-500/30',
    text: 'text-green-700 dark:text-green-300',
    icon: CheckCircle,
    iconColor: 'text-green-600 dark:text-green-400',
  },
  warning: {
    bg: 'bg-yellow-50 dark:bg-yellow-900/30',
    border: 'border-yellow-500/30',
    text: 'text-yellow-700 dark:text-yellow-300',
    icon: AlertTriangle,
    iconColor: 'text-yellow-600 dark:text-yellow-400',
  },
  critical: {
    bg: 'bg-red-50 dark:bg-red-900/30',
    border: 'border-red-500/30',
    text: 'text-red-700 dark:text-red-300',
    icon: AlertTriangle,
    iconColor: 'text-red-600 dark:text-red-400',
  },
  info: {
    bg: 'bg-blue-900/30',
    border: 'border-blue-500/30',
    text: 'text-blue-700 dark:text-blue-300',
    icon: Info,
    iconColor: 'text-blue-600 dark:text-blue-400',
  },
};

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

export default function AlertBanner({
  alert,
  onDismiss,
  onAcknowledge,
  compact = false,
  showActions = true,
}) {
  const config = SEVERITY_CONFIG[alert.severity] || SEVERITY_CONFIG.info;
  const Icon = config.icon;

  if (compact) {
    // Compact badge for cards
    return (
      <div
        className={`flex items-center gap-2 px-2 py-1 rounded border ${config.bg} ${config.border}`}
      >
        <Icon className={`w-3 h-3 ${config.iconColor}`} />
        <span className={`text-xs font-medium ${config.text}`}>{alert.rule_name}</span>
      </div>
    );
  }

  return (
    <div className={`flex items-start gap-3 p-4 rounded-lg border ${config.bg} ${config.border}`}>
      <Icon className={`w-5 h-5 ${config.iconColor} flex-shrink-0 mt-0.5`} />

      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <h4 className={`text-sm font-semibold ${config.text}`}>{alert.rule_name}</h4>
              {/* SOW Context Badge (if alert is SOW-specific) */}
              {alert.line_of_business && (
                <span
                  className={`px-2 py-0.5 rounded text-[10px] font-medium border ${LOB_COLORS[alert.line_of_business]}`}
                >
                  {LOB_LABELS[alert.line_of_business]}
                </span>
              )}
            </div>
            <p className="text-sm text-aegisaxiom-text-secondary">{alert.message}</p>

            {/* Trigger context */}
            {alert.trigger_values && Object.keys(alert.trigger_values).length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {Object.entries(alert.trigger_values).map(([key, value]) => (
                  <span
                    key={key}
                    className="text-xs bg-aegisaxiom-elevated border border-aegisaxiom-border rounded px-2 py-0.5 text-aegisaxiom-text-secondary"
                  >
                    {key}: {typeof value === 'number' ? value.toFixed(1) : value}
                  </span>
                ))}
              </div>
            )}

            <p className="text-xs text-aegisaxiom-text-muted mt-2">
              Triggered {new Date(alert.triggered_at).toLocaleString()}
              {alert.acknowledged && alert.acknowledged_by && (
                <> • Acknowledged by {alert.acknowledged_by}</>
              )}
            </p>
          </div>

          {/* Actions */}
          {showActions && (
            <div className="flex gap-1 flex-shrink-0">
              {!alert.acknowledged && onAcknowledge && (
                <button
                  onClick={() => onAcknowledge(alert.alert_id)}
                  className="px-3 py-1 text-xs bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded transition"
                  title="Acknowledge alert"
                >
                  ✓ Ack
                </button>
              )}
              {onDismiss && (
                <button
                  onClick={() => onDismiss(alert.alert_id)}
                  className="p-1 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
                  title="Dismiss alert"
                >
                  <X className="w-4 h-4" />
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Alert Badge for Kanban Cards (Mini version)
 */
export function AlertBadge({ severity, count = 1 }) {
  const config = SEVERITY_CONFIG[severity] || SEVERITY_CONFIG.info;

  // Use appropriate icon based on severity
  const BadgeIcon = severity === 'success' ? CheckCircle : AlertTriangle;

  return (
    <div
      className={`flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold border ${config.bg} ${config.border} ${config.text}`}
    >
      <BadgeIcon className="w-3 h-3" />
      {count > 1 && <span>{count}</span>}
    </div>
  );
}
