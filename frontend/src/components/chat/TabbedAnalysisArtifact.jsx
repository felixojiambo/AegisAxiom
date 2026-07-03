import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../lib/api';
import API_URL from '../../lib/apiConfig';
import AnalysisLoadingState from './AnalysisLoadingState';
import RegenerateProgress from './RegenerateProgress';
import OverviewTab from './vegaOverview';
import StandaloneAppsTab from './vegaStandaloneApps';
import OpenItemsTab from './vegaOpenItems';
import WorkflowRow from './WorkflowRow';
import ExportMenu from './ExportMenu';

const STYLE_ID = 'vega-tabbed-artifact-styles';

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .vega-v3-card {
      margin: 12px 0;
      border: 1px solid var(--aegisaxiom-border, #2a2a44);
      background: var(--aegisaxiom-elevated, #16162e);
      border-radius: 12px;
      overflow: hidden;
    }
    .vega-v3-card .head {
      padding: 10px 14px 8px;
      border-bottom: 1px solid var(--aegisaxiom-border, #2a2a44);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .vega-v3-card .head .title {
      font-weight: 600;
      color: var(--aegisaxiom-text, #e6e2ff);
      font-size: 13px;
    }
    .vega-v3-card .head .sub {
      font-size: 11px;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      margin-top: 2px;
    }
    .vega-v3-card .head .actions {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .vega-v3-state-banner {
      padding: 6px 14px;
      font-size: 11px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      font-weight: 600;
      border-bottom: 1px solid transparent;
    }
    .vega-v3-state-banner.review {
      background: rgba(245, 158, 11, 0.12);
      color: #f59e0b;
      border-bottom-color: rgba(245, 158, 11, 0.25);
    }
    .vega-v3-state-banner.approved {
      background: rgba(16, 185, 129, 0.12);
      color: #10b981;
      border-bottom-color: rgba(16, 185, 129, 0.25);
    }
    .vega-v3-state-banner.partial {
      background: rgba(248, 113, 113, 0.12);
      color: #f87171;
      border-bottom-color: rgba(248, 113, 113, 0.25);
    }
    .vega-v3-tabs {
      display: flex;
      border-bottom: 1px solid var(--aegisaxiom-border, #2a2a44);
      padding: 0 12px;
      background: var(--aegisaxiom-surface, #0f0f24);
    }
    .vega-v3-tabs button {
      appearance: none;
      background: transparent;
      border: none;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      padding: 10px 14px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      border-bottom: 2px solid transparent;
      letter-spacing: 0.02em;
    }
    .vega-v3-tabs button.active {
      color: var(--aegisaxiom-text, #e6e2ff);
      border-bottom-color: #8b5cf6;
    }
    .vega-v3-tabs button .count {
      display: inline-block;
      margin-left: 6px;
      padding: 0 6px;
      background: var(--aegisaxiom-border, #2a2a44);
      border-radius: 999px;
      font-size: 10px;
      color: var(--aegisaxiom-text, #e6e2ff);
    }
    .vega-v3-body {
      padding: 12px 14px 16px;
    }
    .vega-v3-version-badge {
      display: inline-flex;
      align-items: center;
      font-size: 10px;
      font-weight: 600;
      padding: 1px 7px;
      border-radius: 999px;
      border: 1px solid rgba(16, 185, 129, 0.4);
      background: rgba(16, 185, 129, 0.12);
      color: #10b981;
      letter-spacing: 0.04em;
      transition: opacity 0.5s;
    }
    .vega-v3-version-badge.fading {
      opacity: 0;
    }
    .vega-v3-toast {
      margin-bottom: 10px;
      padding: 8px 14px;
      border-radius: 8px;
      font-size: 12px;
      display: flex;
      align-items: center;
      gap: 8px;
      background: rgba(16, 185, 129, 0.1);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: #10b981;
      transition: opacity 0.4s;
    }
    .vega-v3-toast.fading {
      opacity: 0;
    }
    .vega-v3-error-card {
      margin-bottom: 12px;
      padding: 10px 14px;
      border-radius: 8px;
      font-size: 12px;
      background: rgba(248, 113, 113, 0.08);
      border: 1px solid rgba(248, 113, 113, 0.3);
      color: #f87171;
    }
    .vega-v3-error-card .error-actions {
      margin-top: 8px;
      display: flex;
      gap: 8px;
    }
    .vega-v3-error-action-btn {
      appearance: none;
      background: rgba(248, 113, 113, 0.15);
      border: 1px solid rgba(248, 113, 113, 0.35);
      border-radius: 5px;
      color: #f87171;
      font-size: 11px;
      padding: 3px 10px;
      cursor: pointer;
      transition: background 0.15s;
    }
    .vega-v3-error-action-btn:hover { background: rgba(248, 113, 113, 0.25); }
    .vega-v3-call-mode-badge {
      display: inline-flex;
      align-items: center;
      font-size: 10px;
      font-weight: 600;
      padding: 1px 7px;
      border-radius: 999px;
      letter-spacing: 0.04em;
    }
    .vega-v3-call-mode-badge.call1 {
      background: rgba(245, 158, 11, 0.12);
      border: 1px solid rgba(245, 158, 11, 0.35);
      color: #f59e0b;
    }
    .vega-v3-call-mode-badge.call2 {
      background: rgba(59, 130, 246, 0.12);
      border: 1px solid rgba(59, 130, 246, 0.35);
      color: #3b82f6;
    }
    .vega-v3-call-mode-badge.call3 {
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.35);
      color: #10b981;
    }
    .vega-v3-mmi-pending-notice {
      padding: 8px 10px;
      font-size: 12px;
      color: #f59e0b;
      background: rgba(245, 158, 11, 0.08);
      border: 1px solid rgba(245, 158, 11, 0.25);
      border-radius: 6px;
      margin: 6px 0;
    }
  `;
  document.head.appendChild(style);
}

const ALL_TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'workflows', label: 'Workflows' },
  { id: 'standalone', label: 'Standalone Apps' },
  { id: 'open_items', label: 'Open Items' },
];

const CALL_MODE_LABELS = {
  call1: 'Call 1 · Discovery',
  call2: 'Call 2 · Depth',
  call3: 'Call 3 · Final',
};

const PHASES = [
  'Skeleton extraction',
  'Per-workflow + per-app detail',
  'BPMN diagrams + design docs',
  'Post-processing',
];

function deriveLifecycleState(artifact) {
  const status = artifact?.status;
  if (status === 'pending' || status === 'in_progress') return 'analyzing';
  if (status === 'approved') return 'approved';
  return 'review';
}

function phasesCompleteFromMetadata(payload) {
  if (!payload?.pipeline_metadata) return 0;
  const md = payload.pipeline_metadata;
  let n = 0;
  if (md.stage_1_duration_ms != null) n++;
  if (md.stage_2_duration_ms != null) n++;
  if (md.stage_3_duration_ms != null) n++;
  if (md.stage_4_duration_ms != null) n++;
  return n;
}

/**
 * Derive a human-readable regen scope summary from the response.
 * Falls back gracefully when the field is absent.
 */
function formatRegenScope(regenScope) {
  if (!regenScope) return null;
  if (typeof regenScope === 'string') return regenScope;
  const parts = [];
  if (regenScope.workflows > 0)
    parts.push(`${regenScope.workflows} workflow${regenScope.workflows !== 1 ? 's' : ''}`);
  if (regenScope.apps > 0)
    parts.push(`${regenScope.apps} app${regenScope.apps !== 1 ? 's' : ''}`);
  if (parts.length === 0) return null;
  return `Regenerated ${parts.join(', ')}`;
}

/**
 * TabbedAnalysisArtifact
 *
 * Wraps the full v3 tabbed view. Now also manages:
 *   - Regeneration flow (queue → submit → progress → swap)
 *   - Version badge after successful regen
 *   - Error states for regen failures (cap, stale index, permission, generic)
 *
 * Props:
 *   artifact  {object}   — the ai_chat_artifact document (top-level, from ChatArtifact)
 *   onRefresh {function} — optional callback to ask ChatArtifact to re-fetch (unused,
 *                          we manage the live copy internally)
 */
export default function TabbedAnalysisArtifact({ artifact: initialArtifact }) {
  const [activeTab, setActiveTab] = useState('overview');
  // callMode is derived once on mount; it is stable for the lifetime of this artifact.
  const [liveArtifact, setLiveArtifact] = useState(initialArtifact);

  // Keep liveArtifact in sync when the parent re-renders with a fresher prop
  // (e.g. after the chat surface polls on its own). Only sync when artifact_id
  // or current_version changes so we don't overwrite local regen state.
  const prevVersionRef = useRef(initialArtifact?.current_version);
  useEffect(() => {
    const incomingVersion = initialArtifact?.current_version;
    if (incomingVersion !== prevVersionRef.current) {
      prevVersionRef.current = incomingVersion;
      setLiveArtifact(initialArtifact);
    }
  }, [initialArtifact]);

  // Regen state machine: idle | submitting | running | done | error
  const [regenState, setRegenState] = useState('idle'); // 'idle' | 'submitting' | 'running' | 'done' | 'error'
  const [regenError, setRegenError] = useState(null); // { type: 'cap'|'stale'|'permission'|'generic', message }
  const [regenPendingPayload, setRegenPendingPayload] = useState(null);

  // Version badge: shown for 3s after successful regen
  const [versionBadge, setVersionBadge] = useState(null); // null | { version, fading }
  const badgeTimerRef = useRef(null);

  // Success toast
  const [successToast, setSuccessToast] = useState(null); // null | { message, fading }
  const toastTimerRef = useRef(null);

  // Artifact being actively tracked by RegenerateProgress
  const [regenArtifactId, setRegenArtifactId] = useState(null);

  const artifact = liveArtifact;
  const lifecycle = deriveLifecycleState(artifact);
  const payload = artifact?.payload || {};
  const workflows = useMemo(() => payload.workflows || [], [payload.workflows]);
  const apps = useMemo(() => payload.apps || [], [payload.apps]);
  const standaloneApps = useMemo(
    () => apps.filter((a) => !a.linked_workflow_ids || a.linked_workflow_ids.length === 0),
    [apps],
  );
  const openItems = useMemo(() => payload.open_items || [], [payload.open_items]);

  // Derive call mode from pipeline_metadata. Absent = treat as single_call (full render).
  const callMode = payload.pipeline_metadata?.call_mode || 'single_call';

  // Only show Standalone Apps tab for call2, call3, and single_call.
  const visibleTabs = callMode === 'call1'
    ? ALL_TABS.filter((t) => t.id !== 'standalone')
    : ALL_TABS;

  const tabCounts = {
    overview: null,
    workflows: workflows.length,
    standalone: standaloneApps.length,
    open_items: openItems.length,
  };

  const artifactId = artifact?.artifact_id;
  const sessionId = artifact?.session_id;
  const currentVersion = artifact?.current_version;

  const showVersionBadge = (version) => {
    clearTimeout(badgeTimerRef.current);
    setVersionBadge({ version, fading: false });
    badgeTimerRef.current = setTimeout(() => {
      setVersionBadge((v) => v ? { ...v, fading: true } : null);
      setTimeout(() => setVersionBadge(null), 600);
    }, 3000);
  };

  const showSuccessToast = (message) => {
    clearTimeout(toastTimerRef.current);
    setSuccessToast({ message, fading: false });
    toastTimerRef.current = setTimeout(() => {
      setSuccessToast((t) => t ? { ...t, fading: true } : null);
      setTimeout(() => setSuccessToast(null), 500);
    }, 4000);
  };

  useEffect(() => {
    return () => {
      clearTimeout(badgeTimerRef.current);
      clearTimeout(toastTimerRef.current);
    };
  }, []);

  const handleRegenStart = async (queuePayload) => {
    if (!artifactId) return;
    setRegenState('submitting');
    setRegenError(null);
    setRegenPendingPayload(queuePayload);

    try {
      const res = await api.post(
        `${API_URL}/api/vega-artifacts/${artifactId}/regenerate`,
        queuePayload,
      );

      if (res.ok) {
        // Backend accepted — start polling progress
        setRegenArtifactId(artifactId);
        setRegenState('running');
      } else {
        const data = await res.json().catch(() => ({}));
        const detail = data?.detail || '';
        if (res.status === 400 && detail.includes('cap')) {
          setRegenError({
            type: 'cap',
            message: detail || 'Regeneration cap reached — open a new session to continue.',
          });
        } else if (res.status === 400) {
          setRegenError({
            type: 'stale',
            message: 'Some answers reference items that no longer exist — please reload and try again.',
          });
        } else if (res.status === 403) {
          setRegenError({
            type: 'permission',
            message: 'Permission denied — your access may have changed.',
          });
        } else {
          setRegenError({
            type: 'generic',
            message: detail || 'Regeneration failed. Please try again.',
          });
        }
        setRegenState('error');
      }
    } catch {
      setRegenError({ type: 'generic', message: 'Connection error. Please try again.' });
      setRegenState('error');
    }
  };

  const handleRegenComplete = useCallback(
    (freshArtifact) => {
      setLiveArtifact(freshArtifact);
      setRegenState('done');
      setRegenArtifactId(null);

      const newVersion = freshArtifact?.current_version;
      if (newVersion) showVersionBadge(newVersion);

      const scope = formatRegenScope(freshArtifact?.payload?.regen_scope);
      showSuccessToast(scope || 'Analysis regenerated successfully.');
    },
    // showVersionBadge / showSuccessToast are stable (no deps) — intentionally omitted
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const handleRegenError = useCallback((errMsg) => {
    setRegenState('error');
    setRegenError({ type: 'generic', message: errMsg });
    setRegenArtifactId(null);
  }, []);

  const handleRetry = () => {
    if (regenPendingPayload) {
      handleRegenStart(regenPendingPayload);
    }
  };

  if (lifecycle === 'analyzing') {
    return (
      <div className="vega-v3-card">
        <div className="head">
          <div>
            <div className="title">{payload.client_company || 'Discovery analysis'}</div>
            <div className="sub">Analyzing transcript</div>
          </div>
        </div>
        <div className="vega-v3-body">
          <AnalysisLoadingState
            phases={PHASES}
            phasesComplete={phasesCompleteFromMetadata(payload)}
            etaSeconds={180}
          />
        </div>
      </div>
    );
  }

  const extractionStatus = payload.extraction_status || 'complete';
  const showPartialBanner = extractionStatus === 'partial';

  return (
    <div className="vega-v3-card">
      <div className="head">
        <div>
          <div className="title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {payload.client_company || 'Discovery analysis'}
            {versionBadge && (
              <span
                className={`vega-v3-version-badge${versionBadge.fading ? ' fading' : ''}`}
                aria-live="polite"
                aria-atomic="true"
              >
                Updated · v{versionBadge.version}
              </span>
            )}
            {!versionBadge && currentVersion > 1 && (
              <span className="vega-v3-version-badge" style={{ opacity: 0.6 }}>
                v{currentVersion}
              </span>
            )}
            {CALL_MODE_LABELS[callMode] && (
              <span className={`vega-v3-call-mode-badge ${callMode}`}>
                {CALL_MODE_LABELS[callMode]}
              </span>
            )}
          </div>
          <div className="sub">
            {workflows.length} workflow{workflows.length === 1 ? '' : 's'} · {apps.length} app
            {apps.length === 1 ? '' : 's'} · {openItems.length} open item
            {openItems.length === 1 ? '' : 's'}
          </div>
        </div>
        <div className="actions">
          <ExportMenu artifact={artifact} />
        </div>
      </div>

      {showPartialBanner ? (
        <div className="vega-v3-state-banner partial">
          Partial extraction — one or more workflows or apps could not be fully analyzed
        </div>
      ) : lifecycle === 'approved' ? (
        <div className="vega-v3-state-banner approved">Approved</div>
      ) : (
        <div className="vega-v3-state-banner review">Review</div>
      )}

      <div className="vega-v3-tabs" role="tablist">
        {visibleTabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={activeTab === t.id}
            className={activeTab === t.id ? 'active' : ''}
            onClick={() => setActiveTab(t.id)}
          >
            {t.label}
            {tabCounts[t.id] != null ? <span className="count">{tabCounts[t.id]}</span> : null}
          </button>
        ))}
      </div>

      <div className="vega-v3-body">
        {/* Success toast */}
        {successToast && (
          <div
            className={`vega-v3-toast${successToast.fading ? ' fading' : ''}`}
            role="status"
            aria-live="polite"
          >
            <span aria-hidden="true">&#10003;</span>
            {successToast.message}
          </div>
        )}

        {/* Regen error states */}
        {regenState === 'error' && regenError && (
          <div className="vega-v3-error-card" role="alert">
            {regenError.message}
            <div className="error-actions">
              {regenError.type === 'stale' && (
                <button
                  type="button"
                  className="vega-v3-error-action-btn"
                  onClick={() => window.location.reload()}
                >
                  Reload
                </button>
              )}
              {regenError.type === 'generic' && (
                <button
                  type="button"
                  className="vega-v3-error-action-btn"
                  onClick={handleRetry}
                >
                  Retry
                </button>
              )}
              <button
                type="button"
                className="vega-v3-error-action-btn"
                onClick={() => { setRegenState('idle'); setRegenError(null); }}
              >
                Dismiss
              </button>
            </div>
          </div>
        )}

        {/* Per-entity regen progress card */}
        {(regenState === 'submitting' || regenState === 'running') && regenArtifactId && (
          <RegenerateProgress
            artifactId={regenArtifactId}
            onComplete={handleRegenComplete}
            onError={handleRegenError}
          />
        )}

        {/* Loading spinner while POST is in flight before running state */}
        {regenState === 'submitting' && !regenArtifactId && (
          <div style={{ padding: '8px 0', fontSize: 12, color: 'var(--aegisaxiom-text-muted, #6e6a90)' }}>
            Submitting…
          </div>
        )}

        {activeTab === 'overview' ? (
          <OverviewTab payload={payload} />
        ) : activeTab === 'workflows' ? (
          <div>
            {workflows.length === 0 ? (
              <div style={{ color: 'var(--aegisaxiom-text-muted, #6e6a90)', fontSize: 12 }}>
                No workflows extracted from this transcript.
              </div>
            ) : (
              workflows.map((wf) => (
                <WorkflowRow
                  key={wf.workflow_id}
                  workflow={wf}
                  apps={apps}
                  artifactId={artifact?.artifact_id}
                  callMode={callMode}
                />
              ))
            )}
          </div>
        ) : activeTab === 'standalone' ? (
          <StandaloneAppsTab apps={standaloneApps} artifactId={artifact?.artifact_id} callMode={callMode} />
        ) : (
          <OpenItemsTab
            openItems={openItems}
            workflows={workflows}
            apps={apps}
            artifactId={artifactId}
            sessionId={sessionId}
            onRegenStart={
              regenState === 'running' || regenState === 'submitting'
                ? undefined
                : handleRegenStart
            }
          />
        )}
      </div>
    </div>
  );
}
