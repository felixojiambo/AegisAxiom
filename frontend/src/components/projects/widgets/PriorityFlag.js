import React from 'react';
import { Flag } from 'lucide-react';

const PRIORITY_COLOR = {
  critical: 'text-red-400',
  high: 'text-orange-400',
  medium: 'text-yellow-400',
  low: 'text-aegisaxiom-text-muted',
};

export default function PriorityFlag({ priority }) {
  const color = PRIORITY_COLOR[priority] ?? PRIORITY_COLOR.low;
  return (
    <Flag
      size={11}
      className={color}
      aria-label={priority ? `Priority: ${priority}` : 'No priority'}
      data-testid={`priority-flag-${priority ?? 'none'}`}
    />
  );
}
