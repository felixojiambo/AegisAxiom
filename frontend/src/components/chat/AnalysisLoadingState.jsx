import React, { useMemo } from 'react';

// Detect reduced-motion preference once at module load (safe: no SSR in this app).
const prefersReducedMotion =
  typeof window !== 'undefined' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// Inject animation keyframes once at module load. Both AnalysisLoadingState
// and CompactSpinner reference these — without a single hoisted injection,
// every (un)mount cycle could append a duplicate <style> block.
if (typeof document !== 'undefined' && !prefersReducedMotion) {
  const STYLE_ID = 'vega-loading-state-keyframes';
  if (!document.getElementById(STYLE_ID)) {
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      @keyframes vega-sweep {
        from { transform: translateX(-100%); }
        to   { transform: translateX(433%); }
      }
      @keyframes vega-shimmer {
        0%   { background-position: 200% 0; }
        100% { background-position: -200% 0; }
      }
      @keyframes vega-pulse {
        0%, 100% { opacity: 0.4; }
        50%       { opacity: 1; }
      }
    `;
    document.head.appendChild(style);
  }
}

/**
 * Formats etaSeconds into a human-readable ETA string.
 *   <60  → "ETA <1 min"
 *   60-120 → "ETA ~2 min"
 *   >120   → "ETA ~N min"
 */
function formatEta(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  if (seconds < 60) return 'ETA <1 min';
  const mins = Math.round(seconds / 60);
  return `ETA ~${mins} min`;
}

/**
 * Widths for the shimmer placeholder bars, mirroring the mockup exactly.
 */
const SHIMMER_WIDTHS = ['70%', '50%', '80%', '60%', '40%'];

/**
 * AnalysisLoadingState
 *
 * Presentational component shown while the Vega pipeline is running.
 * Matches the State 1 "Analyzing" section in option-a-tabbed-artifact.html exactly.
 *
 * Props:
 *   phases       {string[]}  — ordered list of phase names
 *   phasesComplete {number}  — how many phases are done (0-indexed count)
 *   etaSeconds   {number}    — estimated seconds remaining
 *   startedAt    {Date}      — optional; reserved for future elapsed-time display
 */
export default function AnalysisLoadingState({
  phases,
  phasesComplete = 0,
  etaSeconds,
}) {
  // Defensive: treat missing/empty phases as a compact fallback.
  const safePhases = Array.isArray(phases) && phases.length > 0 ? phases : null;

  const etaString = useMemo(() => formatEta(etaSeconds), [etaSeconds]);

  const bannerText = safePhases
    ? `Analyzing · ${phasesComplete} of ${safePhases.length} phases${etaString ? ` · ${etaString}` : ''}`
    : 'Analyzing…';

  if (!safePhases) {
    return <CompactSpinner />;
  }

  return (
    <>
      {/* 1. State banner */}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="flex items-center gap-2.5 px-[18px] py-2.5 text-xs
                   bg-violet-500/10 border-b border-violet-500/30
                   text-violet-600 dark:text-violet-400"
      >
        {/* Pulsing dot */}
        <span
          aria-hidden="true"
          style={
            prefersReducedMotion
              ? { width: 8, height: 8, borderRadius: '50%', background: 'currentColor', opacity: 0.7 }
              : {
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: 'currentColor',
                  flexShrink: 0,
                  animation: 'vega-pulse 1.2s ease-in-out infinite',
                }
          }
        />
        <span>
          <strong className="font-semibold">Analyzing</strong>
          {` · ${phasesComplete} of ${safePhases.length} phases`}
          {etaString ? ` · ${etaString}` : ''}
        </span>
      </div>

      {/* 2. Sweeping progress bar */}
      <div
        className="relative h-0.5 w-full overflow-hidden"
        style={{ background: 'var(--aegisaxiom-border)' }}
        aria-hidden="true"
      >
        {prefersReducedMotion ? (
          /* Static gradient when motion is reduced */
          <div
            className="absolute inset-0"
            style={{ background: 'linear-gradient(90deg, transparent, rgb(139 92 246 / 0.5), transparent)' }}
          />
        ) : (
          <div
            className="absolute top-0 h-full"
            style={{
              left: 0,
              width: '30%',
              background: 'linear-gradient(90deg, transparent, rgb(139 92 246), transparent)',
              animation: 'vega-sweep 1.4s linear infinite',
            }}
          />
        )}
      </div>

      {/* 3. Phase checklist row + 4. Shimmer placeholders */}
      <div className="px-[18px] py-[18px]">
        {/* Phase checklist */}
        <div className="mb-2.5 text-xs text-aegisaxiom-text-secondary" aria-hidden="true">
          {safePhases.map((phase, i) => {
            const done = i < phasesComplete;
            return (
              <React.Fragment key={phase}>
                {i > 0 && <span className="mx-1 text-aegisaxiom-text-muted">&middot;</span>}
                <span className={done ? 'text-emerald-500' : 'text-aegisaxiom-text-muted'}>
                  {done ? '✓' : '⋯'} {phase}
                </span>
              </React.Fragment>
            );
          })}
        </div>

        {/* Screen-reader-only phase summary (the visual row above is aria-hidden) */}
        <span className="sr-only">
          {`${phasesComplete} of ${safePhases.length} phases complete: `}
          {safePhases.map((p, i) => `${p} ${i < phasesComplete ? 'done' : 'pending'}`).join(', ')}
        </span>

        {/* Shimmer placeholder bars */}
        {SHIMMER_WIDTHS.map((width, i) => (
          <div
            key={i}
            aria-hidden="true"
            className="rounded"
            style={{
              height: 14,
              width,
              marginBottom: i < SHIMMER_WIDTHS.length - 1 ? 8 : 0,
              borderRadius: 4,
              ...(prefersReducedMotion
                ? { background: 'var(--aegisaxiom-border)' }
                : {
                    background:
                      'linear-gradient(90deg, var(--aegisaxiom-bg-surface), var(--aegisaxiom-border), var(--aegisaxiom-bg-surface))',
                    backgroundSize: '200% 100%',
                    animation: 'vega-shimmer 1.6s infinite',
                  }),
            }}
          />
        ))}
      </div>
    </>
  );
}

/**
 * Compact fallback when phases prop is missing or empty.
 */
function CompactSpinner() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center gap-2 px-[18px] py-3 text-xs text-violet-600 dark:text-violet-400"
    >
      <span
        aria-hidden="true"
        style={
          prefersReducedMotion
            ? { width: 8, height: 8, borderRadius: '50%', background: 'currentColor', opacity: 0.7 }
            : {
                width: 8,
                height: 8,
                borderRadius: '50%',
                background: 'currentColor',
                flexShrink: 0,
                animation: 'vega-pulse 1.2s ease-in-out infinite',
              }
        }
      />
      <span>Analyzing…</span>
    </div>
  );
}
