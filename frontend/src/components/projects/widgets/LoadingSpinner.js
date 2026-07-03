import React from 'react';
import { Loader2 } from 'lucide-react';

const SIZE = {
  sm: 16,
  md: 24,
  lg: 36,
};

export default function LoadingSpinner({ size = 'md' }) {
  const px = SIZE[size] ?? SIZE.md;
  return (
    <Loader2
      size={px}
      className="animate-spin text-aegisaxiom-text-muted"
      aria-label="Loading"
      data-testid="loading-spinner"
    />
  );
}
