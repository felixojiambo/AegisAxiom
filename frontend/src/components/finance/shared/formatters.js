export const fmt = (n) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(n || 0);

export const STATUS_COLORS = {
  pending: '#F59E0B',
  approved: '#3B82F6',
  paid: '#10B981',
  rejected: '#EF4444',
  Owed: '#F59E0B',
  Paid: '#10B981',
  Potential: '#8B5CF6',
};
