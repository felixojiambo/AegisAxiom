// Formatting helpers for the Financial Command Center — ported from source lib/format.js
export const fmtCurrency = (n, opts = {}) => {
  const { compact = false, sign = false, currency = 'USD' } = opts;
  const v = Number(n ?? 0);
  if (compact && Math.abs(v) >= 1000) {
    const compactNumber = new Intl.NumberFormat('en-US', {
      notation: 'compact',
      maximumFractionDigits: 1,
    }).format(v);
    return `$${compactNumber}`;
  }
  const fmt = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: sign ? 'exceptZero' : 'auto',
  });
  return fmt.format(v);
};

export const fmtNumber = (n, opts = {}) => {
  const { decimals = 0, compact = false } = opts;
  const v = Number(n ?? 0);
  if (compact && Math.abs(v) >= 1000) {
    return new Intl.NumberFormat('en-US', {
      notation: 'compact',
      maximumFractionDigits: 1,
    }).format(v);
  }
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(v);
};

export const fmtPercent = (n, decimals = 1) => {
  if (n == null || isNaN(n)) return '—';
  return `${Number(n).toFixed(decimals)}%`;
};

export const fmtDate = (d) => {
  if (!d) return '—';
  try {
    const dt = new Date(d);
    if (isNaN(dt.getTime())) return String(d);
    return dt.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: '2-digit' });
  } catch {
    return String(d);
  }
};

export const fmtDateRelative = (d) => {
  if (!d) return '—';
  try {
    const dt = new Date(d);
    const now = new Date();
    const diffMs = now - dt;
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 30) return `${days}d ago`;
    return dt.toLocaleDateString('en-US', { month: 'short', day: '2-digit', year: 'numeric' });
  } catch {
    return String(d);
  }
};

export const colorForDelta = (n) => {
  if (n == null) return 'neutral';
  if (n > 0) return 'positive';
  if (n < 0) return 'negative';
  return 'neutral';
};
