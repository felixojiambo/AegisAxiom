import React from 'react';

const STATUS_MAP = {
  Synced: { cls: 'bg-green-500/15 text-green-400', label: 'Synced' },
  Pending: { cls: 'bg-amber-500/15 text-amber-400', label: 'Pending' },
  Failed: { cls: 'bg-red-500/15 text-red-400', label: 'Failed' },
  Draft: { cls: 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted', label: 'Draft' },
};

export default function SyncStatusBadge({ status }) {
  const cfg = STATUS_MAP[status] || { cls: 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted', label: status || '—' };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium ${cfg.cls}`}>
      {cfg.label}
    </span>
  );
}
