import React, { useState, useCallback } from 'react'; // useState kept for testState/lookupLoading
import { CheckCircle2, XCircle, Loader2, RefreshCw, Zap, ZapOff } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import SecretField from './SecretField';

/**
 * AppDirectSection
 *
 * Fields: base_url, bearer_token (encrypted), multipass_enabled, live_post_enabled,
 * defaults (contract type / term IDs, sales_agent_id), last_test (read-only),
 * cached_lookups summary + refresh, lookups_fetched_at (read-only).
 */

function formatRelativeTime(iso) {
  if (!iso) return 'never';
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export default function AppDirectSection({ value = {}, onChange, readOnly }) {
  const defaults = value.defaults ?? {};

  const [testState, setTestState] = useState('idle');
  const [testMessage, setTestMessage] = useState('');
  const [testLatency, setTestLatency] = useState(null);
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookups, setLookups] = useState(value.cached_lookups ?? null);
  const [lookupsFetchedAt, setLookupsFetchedAt] = useState(value.lookups_fetched_at ?? null);

  function patch(changes) {
    onChange({ ...value, ...changes });
  }

  function patchDefault(field, val) {
    onChange({ ...value, defaults: { ...defaults, [field]: Number(val) || 0 } });
  }

  async function handleTest() {
    setTestState('testing');
    setTestMessage('');
    setTestLatency(null);
    try {
      const res = await api.post(`${API_URL}/api/match/settings/test-appdirect`);
      const data = await res.json();
      if (res.ok && data.status === 'success') {
        setTestState('success');
        setTestMessage(data.message ?? 'Connected');
        setTestLatency(data.latency_ms ?? null);
        setTimeout(() => setTestState('idle'), 10000);
      } else {
        setTestState('error');
        setTestMessage(data.message ?? data.detail ?? 'Test failed');
      }
    } catch {
      setTestState('error');
      setTestMessage('Could not reach backend');
    }
  }

  const handleRefreshLookups = useCallback(async () => {
    setLookupLoading(true);
    try {
      const res = await api.get(`${API_URL}/api/match/settings/appdirect-lookups`);
      const data = await res.json();
      if (res.ok) {
        setLookups(data);
        setLookupsFetchedAt(new Date().toISOString());
      }
    } finally {
      setLookupLoading(false);
    }
  }, []);

  const lastTest = value.last_test;

  return (
    <div className="space-y-6">
      {/* ── Connection ── */}
      <div className="space-y-3">
        <div className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
          API Connection
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <label htmlFor="ad-base-url" className="block text-xs font-medium text-aegisaxiom-text-muted">
              API Base URL
            </label>
            <input
              id="ad-base-url"
              type="url"
              className="input-field w-full text-sm"
              placeholder="https://your-appdirect-instance.com"
              value={value.base_url ?? ''}
              onChange={(e) => patch({ base_url: e.target.value })}
              disabled={readOnly}
            />
          </div>

          <div className="space-y-1">
            <label htmlFor="ad-bearer" className="block text-xs font-medium text-aegisaxiom-text-muted">
              Bearer Token
              <span className="ml-1 font-normal text-aegisaxiom-text-muted">(encrypted)</span>
            </label>
            <SecretField
              id="ad-bearer"
              value={value.bearer_token ?? ''}
              onChange={(v) => patch({ bearer_token: v })}
              placeholder="Paste bearer token from AppDirect"
              disabled={readOnly}
            />
          </div>
        </div>

        {/* Last tested */}
        {lastTest && (
          <div className="text-xs text-aegisaxiom-text-muted">
            Last tested:{' '}
            {lastTest.tested_at
              ? new Date(lastTest.tested_at).toLocaleString()
              : 'unknown'}
            {lastTest.status === 'success' && lastTest.latency_ms != null
              ? ` — ${lastTest.latency_ms}ms`
              : ''}
          </div>
        )}

        {/* Test Connection */}
        <div className="flex items-center justify-between gap-4">
          <div aria-live="polite" className="flex items-center gap-1.5 text-xs">
            {testState === 'idle' && (
              <span className="text-aegisaxiom-text-muted">Save changes first, then test.</span>
            )}
            {testState === 'testing' && (
              <span className="flex items-center gap-1.5 text-aegisaxiom-text-muted">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Testing...
              </span>
            )}
            {testState === 'success' && (
              <span className="flex items-center gap-1.5" style={{ color: 'var(--color-success, #22c55e)' }}>
                <CheckCircle2 className="h-3.5 w-3.5" />
                {testMessage}{testLatency != null ? ` (${testLatency}ms)` : ''}
              </span>
            )}
            {testState === 'error' && (
              <span className="flex items-center gap-1.5 text-red-400">
                <XCircle className="h-3.5 w-3.5" /> {testMessage}
              </span>
            )}
          </div>
          <button
            type="button"
            className="btn-secondary text-sm flex items-center gap-1.5"
            disabled={testState === 'testing' || readOnly}
            onClick={handleTest}
          >
            Test AppDirect
          </button>
        </div>
      </div>

      {/* ── Push Config ── */}
      <div className="space-y-3">
        <div className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
          Push Configuration
        </div>

        {/* Live Post toggle */}
        <div
          className={`glass-card flex items-center justify-between gap-4 p-4 rounded-lg border transition-colors ${
            value.live_post_enabled
              ? 'border-green-500/40 bg-green-500/5'
              : 'border-aegisaxiom-border'
          }`}
        >
          <div className="flex items-start gap-3 flex-1">
            {value.live_post_enabled ? (
              <Zap className="h-4 w-4 text-green-400 mt-0.5 shrink-0" />
            ) : (
              <ZapOff className="h-4 w-4 text-aegisaxiom-text-muted mt-0.5 shrink-0" />
            )}
            <div>
              <div className="text-sm font-medium text-aegisaxiom-text">
                Live Post {value.live_post_enabled ? 'ON' : 'OFF'}
              </div>
              <div className="text-xs text-aegisaxiom-text-muted mt-0.5">
                {value.live_post_enabled
                  ? 'Reviewer actions will push live to AppDirect. Ensure token is valid and connection tests green.'
                  : 'Reviewer actions produce a preview only. Nothing is sent to AppDirect.'}
              </div>
              {value.live_post_enabled && (
                <div className="mt-1.5 inline-flex items-center gap-1.5 rounded-full bg-orange-500/10 border border-orange-500/30 px-2.5 py-0.5 text-xs text-orange-400 font-medium">
                  Live posting will push to AppDirect for real
                </div>
              )}
            </div>
          </div>
          {!readOnly && (
            <button
              type="button"
              role="switch"
              aria-checked={value.live_post_enabled}
              className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border-2 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 ${
                value.live_post_enabled
                  ? 'bg-green-500 border-green-500'
                  : 'bg-aegisaxiom-bg-elevated border-aegisaxiom-border'
              }`}
              onClick={() => patch({ live_post_enabled: !value.live_post_enabled })}
            >
              <span
                className={`block h-3.5 w-3.5 rounded-full bg-aegisaxiom-text shadow transition-transform ${
                  value.live_post_enabled ? 'translate-x-4' : 'translate-x-0.5'
                }`}
              />
            </button>
          )}
        </div>

        {/* Multipass */}
        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            className="mt-0.5 h-4 w-4 rounded border-aegisaxiom-border accent-aegisaxiom-primary"
            checked={value.multipass_enabled ?? false}
            onChange={(e) => patch({ multipass_enabled: e.target.checked })}
            disabled={readOnly}
          />
          <div>
            <div className="text-sm font-medium text-aegisaxiom-text">
              Multi-commission-account orders (multipass)
            </div>
            <div className="text-xs text-aegisaxiom-text-muted mt-0.5">
              Enable if your AppDirect tenant uses multipass commission tracking.
              Check with your AppDirect account manager. When in doubt, leave off.
            </div>
          </div>
        </label>
      </div>

      {/* ── Defaults ── */}
      <div className="space-y-3">
        <div className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
          AppDirect Enum Defaults
        </div>
        <p className="text-xs text-aegisaxiom-text-muted">
          Fallback AppDirect FK IDs used when cached lookup tables don't yield a match.
        </p>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <NumberField
            id="ad-ct-new"
            label="Contract Type ID (New)"
            value={defaults.contract_type_id_new}
            onChange={(v) => patchDefault('contract_type_id_new', v)}
            disabled={readOnly}
          />
          <NumberField
            id="ad-term-12"
            label="Contract Term ID (12mo)"
            value={defaults.contract_term_id_12mo}
            onChange={(v) => patchDefault('contract_term_id_12mo', v)}
            disabled={readOnly}
          />
          <NumberField
            id="ad-term-24"
            label="Contract Term ID (24mo)"
            value={defaults.contract_term_id_24mo}
            onChange={(v) => patchDefault('contract_term_id_24mo', v)}
            disabled={readOnly}
          />
          <NumberField
            id="ad-term-36"
            label="Contract Term ID (36mo)"
            value={defaults.contract_term_id_36mo}
            onChange={(v) => patchDefault('contract_term_id_36mo', v)}
            disabled={readOnly}
          />
          <NumberField
            id="ad-agent"
            label="Sales Agent ID"
            value={defaults.sales_agent_id}
            onChange={(v) => patchDefault('sales_agent_id', v)}
            disabled={readOnly}
          />
        </div>
      </div>

      {/* ── Lookup Tables ── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
              Lookup Tables
            </div>
            <p className="text-xs text-aegisaxiom-text-muted mt-0.5">
              Cached AppDirect product catalog, contract types, terms, and user directory.
            </p>
          </div>
          <button
            type="button"
            className="btn-secondary text-sm flex items-center gap-1.5"
            disabled={lookupLoading || readOnly}
            onClick={handleRefreshLookups}
          >
            {lookupLoading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            Refresh Lookups
          </button>
        </div>

        {lookups && Object.keys(lookups).length > 0 ? (
          <div className="glass-card rounded-lg p-3 text-xs text-aegisaxiom-text-muted border border-aegisaxiom-border">
            <div className="flex items-center gap-1.5 mb-1 text-aegisaxiom-text font-medium">
              <CheckCircle2 className="h-3.5 w-3.5 text-green-400" />
              Lookups cached
            </div>
            <span>
              {Array.isArray(lookups.products) ? lookups.products.length : 0} products,{' '}
              {Array.isArray(lookups.providers) ? lookups.providers.length : 0} providers,{' '}
              {Array.isArray(lookups.contractTypes) ? lookups.contractTypes.length : 0} contract types,{' '}
              {Array.isArray(lookups.contractTerms) ? lookups.contractTerms.length : 0} contract terms.
            </span>
            {lookupsFetchedAt && (
              <div className="mt-1 text-aegisaxiom-text-muted">
                Fetched {formatRelativeTime(lookupsFetchedAt)}
              </div>
            )}
          </div>
        ) : (
          <div className="glass-card rounded-lg p-3 text-xs text-aegisaxiom-text-muted border border-aegisaxiom-border">
            {lookupsFetchedAt
              ? `Last synced: ${formatRelativeTime(lookupsFetchedAt)}. Click "Refresh Lookups" to update.`
              : 'Not yet synced. Save base URL and token, then click "Refresh Lookups".'}
          </div>
        )}
      </div>
    </div>
  );
}

function NumberField({ id, label, value, onChange, disabled }) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-xs font-medium text-aegisaxiom-text-muted">
        {label}
      </label>
      <input
        id={id}
        type="number"
        min="0"
        className="input-field w-full text-sm"
        placeholder="0"
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      />
    </div>
  );
}
