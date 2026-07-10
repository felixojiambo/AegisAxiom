import React, { useEffect, useState, useCallback, useRef } from 'react';
import { ChevronDown, ChevronUp, Save, RefreshCw, Webhook, Database, Calendar, Link } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';
import N8nWebhooksSection from './settings/N8nWebhooksSection';
import AppDirectSection from './settings/AppDirectSection';
import CrmSection from './settings/CrmSection';
import ScheduleSection from './settings/ScheduleSection';

/**
 * SettingsTab — MATCH admin settings (admin-gated).
 *
 * Permission model:
 *   match.settings.view  — can see this tab at all (gated at MatchPortal level)
 *   match.settings.edit  — can make changes and save (gated here on Save + all inputs)
 *
 * Architecture:
 *   - Fetch once on mount from GET /api/match/settings
 *   - State is a single `settings` object mirroring the backend Settings model
 *   - Each Section receives its sub-block + an onChange callback
 *   - Single "Save Changes" PUT at the bottom sends the FULL settings object
 *     (not partial; this is simpler and the backend handles deep-merge safely)
 *   - After save, refetch to confirm and get updated sentinel values
 *   - Dirty tracking via JSON.stringify comparison against last-fetched snapshot
 *
 * Encrypted fields (shared_auth_token, bearer_token, service_password, api_key)
 * are handled by SecretField components inside each Section. The sentinel
 * "••••••••••••" is preserved on save when the user didn't change the field.
 */

const SECTIONS = [
  {
    id: 'n8n',
    label: 'n8n Webhooks',
    Icon: Webhook,
    description: 'Webhook URLs and shared auth token for the n8n matching workflow.',
    Component: N8nWebhooksSection,
    valueKey: 'n8n',
    changeKey: 'n8n',
  },
  {
    id: 'appdirect',
    label: 'AppDirect',
    Icon: Link,
    description: 'AppDirect API connection, push config, defaults, and lookup tables.',
    Component: AppDirectSection,
    valueKey: 'appdirect',
    changeKey: 'appdirect',
  },
  {
    id: 'crm',
    label: 'CRM Data Source',
    Icon: Database,
    description: 'How MATCH reads existing customers and orders for matching.',
    Component: CrmSection,
    valueKey: 'crm',
    changeKey: 'crm',
  },
  {
    id: 'schedule',
    label: 'Schedule',
    Icon: Calendar,
    description: 'Processing schedule, confidence thresholds, and escalation config.',
    Component: ScheduleSection,
    valueKey: 'schedule',
    changeKey: 'schedule',
  },
];

function formatDateTime(iso) {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

export default function SettingsTab() {
  const { hasPermission } = usePermissions();
  const canEdit = hasPermission('match.settings.edit');

  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  // Dirty tracking: snapshot of the last successfully-fetched settings
  const lastFetchedRef = useRef(null);
  const [isDirty, setIsDirty] = useState(false);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Expanded state — n8n expanded by default
  const [expandedSections, setExpandedSections] = useState({ n8n: true });

  const fetchSettings = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await api.get(`${API_URL}/api/match/settings`);
      const data = await res.json();
      if (!res.ok) {
        setLoadError(data?.detail ?? 'Failed to load settings');
        return;
      }
      setSettings(data);
      lastFetchedRef.current = JSON.stringify(data);
      setIsDirty(false);
    } catch {
      setLoadError('Could not reach backend');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  function handleSubBlockChange(blockKey, newSubBlock) {
    setSettings((prev) => {
      const next = { ...prev, [blockKey]: newSubBlock };
      setIsDirty(JSON.stringify(next) !== lastFetchedRef.current);
      return next;
    });
    setSaveSuccess(false);
  }

  async function handleSave() {
    if (!canEdit || !isDirty) return;
    setSaving(true);
    setSaveError(null);
    setSaveSuccess(false);
    try {
      const res = await api.put(`${API_URL}/api/match/settings`, settings);
      const data = await res.json();
      if (!res.ok) {
        setSaveError(data?.detail ?? 'Save failed');
        return;
      }
      // Refetch after save so sentinel values are fresh from the server
      const refetchRes = await api.get(`${API_URL}/api/match/settings`);
      const refetchData = await refetchRes.json();
      if (refetchRes.ok) {
        setSettings(refetchData);
        lastFetchedRef.current = JSON.stringify(refetchData);
      } else {
        // Use the PUT response as fallback
        setSettings(data);
        lastFetchedRef.current = JSON.stringify(data);
      }
      setIsDirty(false);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 4000);
    } catch {
      setSaveError('Could not reach backend');
    } finally {
      setSaving(false);
    }
  }

  function toggleSection(id) {
    setExpandedSections((prev) => ({ ...prev, [id]: !prev[id] }));
  }

  // ── Loading ──
  if (loading) {
    return (
      <div className="space-y-3 py-6">
        {[1, 2, 3, 4].map((n) => (
          <div key={n} className="glass-card rounded-xl border border-aegisaxiom-border h-16 animate-pulse" />
        ))}
      </div>
    );
  }

  // ── Load error ──
  if (loadError) {
    return (
      <div className="py-10 text-center space-y-3">
        <p className="text-aegisaxiom-text-muted">{loadError}</p>
        <button
          type="button"
          className="btn-secondary flex items-center gap-2 mx-auto text-sm"
          onClick={fetchSettings}
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Retry
        </button>
      </div>
    );
  }

  const lastSaved = settings?.updated_at ? formatDateTime(settings.updated_at) : null;

  return (
    <div className="space-y-4 pb-24">
      {/* Header row — save button is here too so it's visible without
          scrolling past 4 expanded sections */}
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="page-title text-aegisaxiom-text">MATCH Settings</h2>
          {lastSaved && (
            <p className="text-xs text-aegisaxiom-text-muted mt-0.5">
              Last saved {lastSaved}
            </p>
          )}
        </div>
        <div className="flex items-center gap-3">
          {!canEdit && (
            <span className="text-xs text-aegisaxiom-text-muted border border-aegisaxiom-border rounded-full px-3 py-1">
              Read-only
            </span>
          )}
          {canEdit && (
            <button
              type="button"
              className="btn-primary flex items-center gap-2 text-sm disabled:opacity-50"
              disabled={!isDirty || saving}
              onClick={handleSave}
            >
              {saving ? (
                <RefreshCw className="h-4 w-4 animate-spin" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              {saving ? 'Saving...' : 'Save Changes'}
            </button>
          )}
        </div>
      </div>

      {/* Inline status pill — visible when scrolled to top */}
      {(saveError || saveSuccess) && (
        <div className="text-sm">
          {saveError && <span className="text-red-400">{saveError}</span>}
          {saveSuccess && <span className="text-green-400">Settings saved.</span>}
        </div>
      )}

      {/* Section cards */}
      {SECTIONS.map(({ id, label, Icon, description, Component, valueKey, changeKey }) => {
        const isOpen = expandedSections[id] ?? false;
        const subBlock = settings?.[valueKey] ?? {};

        return (
          <div key={id} className="glass-card rounded-xl border border-aegisaxiom-border overflow-hidden">
            {/* Header — clickable to expand/collapse */}
            <button
              type="button"
              className="w-full flex items-center justify-between gap-4 px-5 py-4 text-left hover:bg-aegisaxiom-bg-elevated/50 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset"
              onClick={() => toggleSection(id)}
              aria-expanded={isOpen}
              aria-controls={`settings-section-${id}`}
            >
              <div className="flex items-center gap-3">
                <Icon className="h-4 w-4 text-aegisaxiom-text-muted shrink-0" />
                <div>
                  <div className="text-sm font-semibold text-aegisaxiom-text">{label}</div>
                  <div className="text-xs text-aegisaxiom-text-muted mt-0.5">{description}</div>
                </div>
              </div>
              {isOpen ? (
                <ChevronUp className="h-4 w-4 text-aegisaxiom-text-muted shrink-0" />
              ) : (
                <ChevronDown className="h-4 w-4 text-aegisaxiom-text-muted shrink-0" />
              )}
            </button>

            {/* Body */}
            {isOpen && (
              <div
                id={`settings-section-${id}`}
                className="px-5 pb-5 pt-1 border-t border-aegisaxiom-border"
              >
                <Component
                  value={subBlock}
                  onChange={(newSubBlock) => handleSubBlockChange(changeKey, newSubBlock)}
                  readOnly={!canEdit}
                />
              </div>
            )}
          </div>
        );
      })}

      {/* Sticky save bar — pinned to bottom of viewport so users editing
          the bottom of any expanded section can still click save without
          scrolling. The header-row save button is the primary, but this
          catches the "deep in the form" case. */}
      {canEdit && isDirty && (
        <div className="fixed bottom-0 left-0 right-0 z-30 bg-aegisaxiom-surface/95 backdrop-blur border-t border-aegisaxiom-border">
          <div className="max-w-7xl mx-auto px-4 md:px-6 py-3 flex items-center justify-between gap-4">
            <div className="text-sm text-aegisaxiom-text-muted">
              You have unsaved changes.
            </div>
            <button
              type="button"
              className="btn-primary flex items-center gap-2 text-sm disabled:opacity-50"
              disabled={!isDirty || saving}
              onClick={handleSave}
            >
              {saving ? (
                <RefreshCw className="h-4 w-4 animate-spin" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              {saving ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
