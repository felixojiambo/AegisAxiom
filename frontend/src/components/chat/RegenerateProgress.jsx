import React, { useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import API_URL from '../../lib/apiConfig';

/**
 * RegenerateProgress
 *
 * Shown while a partial-regen run is in flight. Polls the artifact endpoint
 * every 2 seconds to detect completion — this app has no real-time event
 * channel for vega_events (no WebSocket or SSE surface exists on the frontend).
 * If one is added in the future, replace the polling interval with a
 * subscription that filters v3_partial_regen_* events by artifact_id.
 *
 * Coarse phases:
 *   1. "Computing changes"     — first 2 poll cycles
 *   2. "Re-extracting"         — middle cycles
 *   3. "Updating diagrams + design docs" — late cycles
 *   4. "Finalizing"            — final cycle before completion
 *
 * Props:
 *   artifactId  {string}           — artifact being regenerated
 *   onComplete  {(artifact) => void} — called when the new version is ready
 *   onError     {(err: string) => void}
 */

const REGEN_PHASES = [
  'Computing changes',
  'Re-extracting',
  'Updating diagrams + design docs',
  'Finalizing',
];

const STYLE_ID = 'vega-regen-progress-styles';

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .vega-regen-card {
      background: var(--aegisaxiom-surface, #0f0f24);
      border: 1px solid var(--aegisaxiom-border, #2a2a44);
      border-radius: 10px;
      overflow: hidden;
      margin-bottom: 12px;
    }
    .vega-regen-banner {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 14px;
      background: rgba(139, 92, 246, 0.1);
      border-bottom: 1px solid rgba(139, 92, 246, 0.25);
      font-size: 11px;
      font-weight: 600;
      color: #a78bfa;
      letter-spacing: 0.04em;
    }
    .vega-regen-phases {
      display: flex;
      gap: 6px;
      padding: 10px 14px 4px;
      flex-wrap: wrap;
    }
    .vega-regen-phase {
      font-size: 11px;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      display: flex;
      align-items: center;
      gap: 4px;
    }
    .vega-regen-phase.active {
      color: #a78bfa;
    }
    .vega-regen-phase.done {
      color: #10b981;
    }
    .vega-regen-phase + .vega-regen-phase::before {
      content: '·';
      margin-right: 6px;
      color: var(--aegisaxiom-border, #2a2a44);
    }
    .vega-regen-entities {
      padding: 8px 14px 12px;
    }
    .vega-regen-entity-row {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 3px 0;
      font-size: 12px;
      color: var(--aegisaxiom-text-muted, #6e6a90);
    }
    .vega-regen-entity-row.complete {
      color: #10b981;
    }
    .vega-regen-entity-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--aegisaxiom-border, #2a2a44);
      flex-shrink: 0;
    }
    .vega-regen-entity-dot.complete {
      background: #10b981;
    }
    .vega-regen-sweep-bar {
      height: 2px;
      width: 100%;
      background: var(--aegisaxiom-border, #2a2a44);
      position: relative;
      overflow: hidden;
    }
    .vega-regen-sweep-inner {
      position: absolute;
      top: 0;
      height: 100%;
      width: 30%;
      background: linear-gradient(90deg, transparent, #8b5cf6, transparent);
      animation: vega-sweep 1.4s linear infinite;
    }
  `;
  document.head.appendChild(style);
}

export default function RegenerateProgress({ artifactId, onComplete, onError }) {
  const [phaseIndex, setPhaseIndex] = useState(0);
  const [completedEntities, setCompletedEntities] = useState([]);
  const [pendingEntities, setPendingEntities] = useState([]);
  const pollCount = useRef(0);
  const intervalRef = useRef(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;

    const poll = async () => {
      pollCount.current += 1;
      const count = pollCount.current;

      // Advance coarse phase based on elapsed poll cycles
      // 2s interval → phase 0 stays for ~0-4s, phase 1 for ~4-12s, phase 2 for ~12-20s, phase 3 after
      const newPhaseIndex = count <= 2 ? 0 : count <= 6 ? 1 : count <= 10 ? 2 : 3;
      if (mountedRef.current) setPhaseIndex(newPhaseIndex);

      try {
        const res = await api.get(`${API_URL}/api/ai-chat-artifacts/${artifactId}`);
        if (!mountedRef.current) return;

        if (res.ok) {
          const data = await res.json();
          const latestVersion = data?.current_version;

          // Collect any entity-level info exposed in the payload
          const regen = data?.payload?.regen_progress || {};
          if (regen.completed_entities) {
            setCompletedEntities(regen.completed_entities);
          }
          if (regen.pending_entities) {
            setPendingEntities(regen.pending_entities);
          }

          // Backend bumps current_version once regen is done
          if (latestVersion && latestVersion > 1) {
            // Give phase indicator a moment to reach "Finalizing"
            setPhaseIndex(3);
            clearInterval(intervalRef.current);
            // Small visual delay so the user sees "Finalizing" before the swap
            setTimeout(() => {
              if (mountedRef.current) onComplete(data);
            }, 600);
          }
        }
      } catch {
        // Non-fatal — keep polling
      }
    };

    // Kick off first poll immediately, then every 2 seconds
    poll();
    intervalRef.current = setInterval(poll, 2000);

    return () => {
      mountedRef.current = false;
      clearInterval(intervalRef.current);
    };
  }, [artifactId, onComplete, onError]);

  const allEntities = [
    ...completedEntities.map((e) => ({ label: e, done: true })),
    ...pendingEntities.map((e) => ({ label: e, done: false })),
  ];
  const showEntities = allEntities.length > 0;

  return (
    <div className="vega-regen-card" role="status" aria-live="polite" aria-label="Regenerating analysis">
      <div className="vega-regen-sweep-bar" aria-hidden="true">
        <div className="vega-regen-sweep-inner" />
      </div>

      <div className="vega-regen-banner">
        <span
          aria-hidden="true"
          style={{
            width: 7,
            height: 7,
            borderRadius: '50%',
            background: 'currentColor',
            flexShrink: 0,
            animation: 'vega-pulse 1.2s ease-in-out infinite',
          }}
        />
        Regenerating analysis
      </div>

      <div className="vega-regen-phases" aria-hidden="true">
        {REGEN_PHASES.map((phase, i) => (
          <span
            key={phase}
            className={`vega-regen-phase${i < phaseIndex ? ' done' : i === phaseIndex ? ' active' : ''}`}
          >
            {i < phaseIndex ? '✓' : i === phaseIndex ? '⋯' : ''} {phase}
          </span>
        ))}
      </div>

      <span className="sr-only">
        {REGEN_PHASES[phaseIndex]} — phase {phaseIndex + 1} of {REGEN_PHASES.length}
      </span>

      {showEntities && (
        <div className="vega-regen-entities">
          {allEntities.map((entity) => (
            <div
              key={entity.label}
              className={`vega-regen-entity-row${entity.done ? ' complete' : ''}`}
            >
              <span className={`vega-regen-entity-dot${entity.done ? ' complete' : ''}`} aria-hidden="true" />
              {entity.label}
              {entity.done && <span className="sr-only"> — complete</span>}
            </div>
          ))}
        </div>
      )}

      {!showEntities && (
        <div style={{ padding: '4px 14px 12px', fontSize: 12, color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}>
          Re-extracting affected entities…
        </div>
      )}
    </div>
  );
}
