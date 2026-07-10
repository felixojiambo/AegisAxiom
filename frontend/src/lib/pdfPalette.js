// Shared color palette for PDF exports. Matches the user's active app theme
// (.light class on documentElement) so generated PDFs don't mix dark and light
// styling. Extracted so any palette change applies to every PDF surface.
export function getPdfPalette() {
  const isLight = document.documentElement.classList.contains('light');
  if (isLight) {
    return {
      bg: '#ffffff',
      text: '#0f172a',
      textSecondary: '#334155',
      textMuted: '#64748b',
      border: '#e2e8f0',
      borderSubtle: '#f1f5f9',
      accent: '#7c3aed',
      danger: '#dc2626',
      success: '#059669',
      warning: '#d97706',
      info: '#2563eb',
      successPanelBg: 'rgba(5, 150, 105, 0.06)',
      successPanelBorder: 'rgba(5, 150, 105, 0.25)',
    };
  }
  return {
    bg: '#1e293b',
    text: '#e2e8f0',
    textSecondary: '#cbd5e1',
    textMuted: '#94a3b8',
    border: '#334155',
    borderSubtle: '#28384d',
    accent: '#a78bfa',
    danger: '#f87171',
    success: '#34d399',
    warning: '#fbbf24',
    info: '#60a5fa',
    successPanelBg: 'rgba(52, 211, 153, 0.10)',
    successPanelBorder: 'rgba(52, 211, 153, 0.20)',
  };
}
