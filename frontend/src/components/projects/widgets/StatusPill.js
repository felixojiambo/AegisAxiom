import React from 'react';

// Maps every status enum from the projects mapping to a color pair.
// complete/shipped/closed/resolved → green
// active/in_progress/executing/acknowledged → blue
// blocked/escalated → red
// planning/planned/scoped/open → neutral
// on_hold/carried_forward/postmortem → yellow
function statusColor(status) {
  switch (status) {
    case 'complete':
    case 'shipped':
    case 'closed':
    case 'resolved':
      return { bg: 'bg-green-500/15', text: 'text-green-400' };
    case 'active':
    case 'in_progress':
    case 'executing':
    case 'acknowledged':
      return { bg: 'bg-blue-500/15', text: 'text-blue-400' };
    case 'blocked':
    case 'escalated':
      return { bg: 'bg-red-500/15', text: 'text-red-400' };
    case 'on_hold':
    case 'carried_forward':
    case 'postmortem':
      return { bg: 'bg-yellow-500/15', text: 'text-yellow-400' };
    case 'planning':
    case 'planned':
    case 'scoped':
    case 'open':
    case 'not_started':
    default:
      return { bg: 'bg-aegisaxiom-elevated', text: 'text-aegisaxiom-text-muted' };
  }
}

function humanize(status) {
  if (!status) return '';
  return status.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function StatusPill({ status, size = 'md', className = '', 'data-testid': testId }) {
  const { bg, text } = statusColor(status);
  const sizeClass = size === 'sm' ? 'h-5 px-2 text-[10px]' : 'h-6 px-3 text-xs';
  return (
    <span
      className={`inline-flex items-center rounded-md font-semibold ${sizeClass} ${bg} ${text} ${className}`}
      data-testid={testId ?? `status-pill-${status}`}
    >
      {humanize(status)}
    </span>
  );
}
