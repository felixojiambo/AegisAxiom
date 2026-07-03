import React from 'react';
import { useState } from 'react';
import { Copy, CheckCircle2, XCircle, Loader2, Webhook } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import SecretField from './SecretField';

/**
 * N8nWebhooksSection
 *
 * Manages n8n.trigger_webhook_url, magic_mapping_webhook_url,
 * reviewer_action_webhook_url, shared_auth_token (encrypted), and
 * callback_url (read-only, auto-derived).
 *
 * Test Connection POSTs {"mode":"ping"} to the trigger_webhook_url itself
 * (matches MATCH source POC behavior — no separate ping endpoint exists
 * in the n8n workflow). Uses the CURRENTLY SAVED settings server-side.
 * Save changes first, then test.
 */
export default function N8nWebhooksSection({ value = {}, onChange, readOnly }) {
  const callbackUrl = value.callback_url ?? '';

  const [testState, setTestState] = useState('idle'); // idle | testing | success | error
  const [testMessage, setTestMessage] = useState('');
  const [testLatency, setTestLatency] = useState(null);
  const [copied, setCopied] = useState(false);

  function patch(changes) {
    onChange({ ...value, ...changes });
  }

  function handleCopyCallback() {
    if (!callbackUrl) return;
    navigator.clipboard.writeText(callbackUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  async function handleTest() {
    setTestState('testing');
    setTestMessage('');
    setTestLatency(null);
    try {
      const res = await api.post(`${API_URL}/api/match/settings/test-n8n`);
      const data = await res.json();
      if (res.ok && data.status === 'success') {
        setTestState('success');
        setTestMessage(data.message ?? 'n8n ping ok');
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

  return (
    <div className="space-y-4">
      {/* Webhook URLs */}
      <div className="grid gap-3">
        <UrlField
          id="n8n-trigger"
          label="Trigger Webhook URL"
          placeholder="https://n8n.example.com/webhook/process-provider-report"
          value={value.trigger_webhook_url ?? ''}
          onChange={(v) => patch({ trigger_webhook_url: v })}
          disabled={readOnly}
        />
        <UrlField
          id="n8n-magic"
          label="Magic Mapping Webhook URL"
          placeholder="https://n8n.example.com/webhook/suggest-mappings"
          value={value.magic_mapping_webhook_url ?? ''}
          onChange={(v) => patch({ magic_mapping_webhook_url: v })}
          disabled={readOnly}
        />
        <UrlField
          id="n8n-reviewer"
          label="Reviewer Action Webhook URL"
          placeholder="https://n8n.example.com/webhook/reviewer-action (optional)"
          value={value.reviewer_action_webhook_url ?? ''}
          onChange={(v) => patch({ reviewer_action_webhook_url: v })}
          disabled={readOnly}
        />
      </div>

      {/* Shared Auth Token */}
      <div className="space-y-1.5">
        <label htmlFor="n8n-token" className="block text-sm font-medium text-aegisaxiom-text">
          Shared Auth Token
          <span className="ml-1.5 text-xs font-normal text-aegisaxiom-text-muted">(encrypted at rest)</span>
        </label>
        <SecretField
          id="n8n-token"
          value={value.shared_auth_token ?? ''}
          onChange={(v) => patch({ shared_auth_token: v })}
          placeholder="Paste bearer token"
          disabled={readOnly}
        />
      </div>

      {/* Callback URL — read-only */}
      <div className="space-y-1.5">
        <div className="text-sm font-medium text-aegisaxiom-text">
          Callback URL
          <span className="ml-1.5 text-xs font-normal text-aegisaxiom-text-muted">
            Auto-derived from FRONTEND_URL; n8n posts back here
          </span>
        </div>
        <div className="flex items-center gap-2">
          <code className="input-field flex-1 text-xs font-mono text-aegisaxiom-text-muted overflow-x-auto whitespace-nowrap">
            {callbackUrl || '—'}
          </code>
          {callbackUrl && (
            <button
              type="button"
              className="btn-secondary text-xs px-2.5 py-1.5 flex items-center gap-1.5 whitespace-nowrap"
              onClick={handleCopyCallback}
              aria-label="Copy callback URL"
            >
              <Copy className="h-3 w-3" />
              {copied ? 'Copied' : 'Copy'}
            </button>
          )}
        </div>
      </div>

      {/* Test Connection */}
      <div className="border-t border-aegisaxiom-border pt-4 mt-2">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2 text-sm" aria-live="polite">
            {testState === 'idle' && (
              <span className="text-aegisaxiom-text-muted text-xs">
                Save your changes first, then test.
              </span>
            )}
            {testState === 'testing' && (
              <span className="flex items-center gap-1.5 text-aegisaxiom-text-muted text-xs">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Pinging n8n...
              </span>
            )}
            {testState === 'success' && (
              <span className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--color-success, #22c55e)' }}>
                <CheckCircle2 className="h-3.5 w-3.5" />
                {testMessage}{testLatency != null ? ` (${testLatency}ms)` : ''}
              </span>
            )}
            {testState === 'error' && (
              <div className="space-y-0.5">
                <span className="flex items-center gap-1.5 text-xs text-red-400">
                  <XCircle className="h-3.5 w-3.5" />
                  {testMessage}
                </span>
              </div>
            )}
          </div>
          <button
            type="button"
            className="btn-secondary text-sm flex items-center gap-1.5"
            disabled={testState === 'testing'}
            onClick={handleTest}
          >
            <Webhook className="h-3.5 w-3.5" />
            Test Connection
          </button>
        </div>
      </div>
    </div>
  );
}

function UrlField({ id, label, placeholder, value, onChange, disabled }) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-xs font-medium text-aegisaxiom-text-muted">
        {label}
      </label>
      <input
        id={id}
        type="url"
        className="input-field w-full text-sm font-mono"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      />
    </div>
  );
}
