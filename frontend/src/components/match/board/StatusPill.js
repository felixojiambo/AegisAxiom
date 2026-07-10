/**
 * StatusPill — compact inline status badge for the board table.
 *
 * Maps the row `status` value to a AEGISAXIOM status color token pair
 * (text + muted background). Renders as a small pill consistent with
 * AEGISAXIOM's status language across the rest of the product.
 *
 * Props
 *   status  {string}  — e.g. "needs_review", "approved", "rejected"
 *   dense   {boolean} — tighter padding (default false)
 */

const STATUS_LABEL = {
  ready: 'Ready',
  ready_to_draft: 'Ready to Draft',
  needs_review: 'Needs Review',
  approved: 'Approved',
  edited: 'Approved',
  rejected: 'Rejected',
  escalated: 'Escalated',
  create_new: 'Create New',
  create_new_order: 'Create New Order',
  error: 'Error',
};

function statusTokens(status) {
  switch (status) {
    case 'approved':
    case 'edited':
    case 'create_new':
    case 'create_new_order':
      return 'text-aegisaxiom-status-success bg-aegisaxiom-status-success-bg';
    case 'needs_review':
    case 'ready_to_draft':
    case 'ready':
      return 'text-aegisaxiom-status-warn bg-aegisaxiom-status-warn-bg';
    case 'rejected':
    case 'error':
      return 'text-aegisaxiom-status-danger bg-aegisaxiom-status-danger-bg';
    case 'escalated':
      return 'text-aegisaxiom-status-info bg-aegisaxiom-status-info-bg';
    default:
      return 'text-aegisaxiom-text-secondary bg-aegisaxiom-surface-elevated';
  }
}

export function StatusPill({ status, dense = false }) {
  const label = STATUS_LABEL[status] ?? status ?? '—';
  const tokens = statusTokens(status);
  const padding = dense ? 'px-2 py-0' : 'px-2.5 py-0.5';

  return (
    <span
      className={[
        'inline-flex items-center rounded-full text-xs font-medium whitespace-nowrap',
        padding,
        tokens,
      ].join(' ')}
      data-status={status}
    >
      {label}
    </span>
  );
}
