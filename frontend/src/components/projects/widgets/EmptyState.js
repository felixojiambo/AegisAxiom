import React from 'react';
import { Inbox } from 'lucide-react';

export default function EmptyState({ title = 'Nothing here yet', description, action }) {
  return (
    <div
      className="flex flex-col items-center justify-center py-16 px-6 text-center"
      data-testid="empty-state"
    >
      <Inbox size={36} className="text-aegisaxiom-text-muted mb-3" />
      <p className="text-sm font-semibold text-aegisaxiom-text">{title}</p>
      {description ? (
        <p className="text-xs text-aegisaxiom-text-muted mt-1 max-w-[320px]">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}
