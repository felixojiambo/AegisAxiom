import React, { useEffect, useState, useCallback } from 'react';
import { Eye, EyeOff, Copy, RefreshCw, AlertCircle, Check, Save } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';
import { toast } from 'sonner';

function maskToken(token) {
  if (!token) return '••••••••-••••-••••-••••-••••••••••••';
  // Show last 4 chars, mask everything else (preserve dashes)
  const suffix = token.slice(-4);
  const prefix = token.slice(0, -4).replace(/[^-]/g, '•');
  return prefix + suffix;
}

export default function SettingsTab() {
  const { hasPermission } = usePermissions();
  const canUpdate = hasPermission('ai_dispatcher.settings.update');
  const canRotate = hasPermission('ai_dispatcher.settings.rotate_token');

  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Token reveal / copy state
  const [tokenRevealed, setTokenRevealed] = useState(false);
  const [copied, setCopied] = useState(false);

  // Rotate confirmation
  const [rotateConfirmOpen, setRotateConfirmOpen] = useState(false);
  const [rotating, setRotating] = useState(false);

  // Workflow URL
  const [workflowUrl, setWorkflowUrl] = useState('');
  const [savingUrl, setSavingUrl] = useState(false);

  const fetchSettings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get(`${API_URL}/api/ai-dispatcher/settings`);
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || 'Failed to load settings');
      setSettings(data);
      setWorkflowUrl(data?.linked_workflow_url ?? '');
    } catch (err) {
      setError(err?.message || 'Failed to load settings');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  const handleCopy = async () => {
    if (!settings?.n8n_token) return;
    try {
      await navigator.clipboard.writeText(settings.n8n_token);
      setCopied(true);
      toast.success('Token copied to clipboard');
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error('Failed to copy — please copy manually after revealing');
    }
  };

  const handleRotate = async () => {
    setRotating(true);
    try {
      const res = await api.post(`${API_URL}/api/ai-dispatcher/settings/rotate-token`, {});
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || 'Failed to rotate token');
      setSettings((prev) => ({ ...prev, n8n_token: data.n8n_token }));
      setRotateConfirmOpen(false);
      setTokenRevealed(false);
      toast.success('Token rotated. Update your n8n credential now.');
    } catch (err) {
      toast.error(err?.message || 'Failed to rotate token');
    } finally {
      setRotating(false);
    }
  };

  const handleSaveUrl = async () => {
    if (!canUpdate) return;
    setSavingUrl(true);
    try {
      const res = await api.patch(`${API_URL}/api/ai-dispatcher/settings`, {
        linked_workflow_url: workflowUrl,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || 'Failed to save');
      toast.success('Workflow URL saved');
    } catch (err) {
      toast.error(err?.message || 'Failed to save');
    } finally {
      setSavingUrl(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="glass-card p-4 flex items-start gap-3 max-w-xl">
        <AlertCircle size={18} className="text-red-400 shrink-0 mt-0.5" />
        <div>
          <p className="text-sm text-aegisaxiom-text">{error}</p>
          <button onClick={fetchSettings} className="text-xs text-violet-400 hover:text-violet-300 mt-1">
            Retry
          </button>
        </div>
      </div>
    );
  }

  const displayToken = tokenRevealed
    ? (settings?.n8n_token || '—')
    : maskToken(settings?.n8n_token);

  return (
    <div className="space-y-6 max-w-xl">
      <div>
        <h2 className="text-lg font-semibold text-aegisaxiom-text">Dispatcher Settings</h2>
        <p className="text-sm text-aegisaxiom-text-secondary mt-0.5">
          Integration credentials and workflow configuration.
        </p>
      </div>

      {/* n8n Token */}
      <div className="glass-card p-5 space-y-4">
        <div>
          <h3 className="text-sm font-semibold text-aegisaxiom-text mb-1">n8n Workflow Token</h3>
          <p className="text-xs text-aegisaxiom-text-muted">
            Used by your n8n workflow to authenticate dispatch requests. Keep this secret.
          </p>
        </div>

        {/* Token display */}
        <div className="flex items-center gap-2">
          <div
            className="flex-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg px-3 py-2.5 font-mono text-sm text-aegisaxiom-text truncate select-all min-w-0"
            aria-label="n8n token"
            title={tokenRevealed ? settings?.n8n_token : undefined}
          >
            {displayToken}
          </div>

          {/* Reveal */}
          <button
            onClick={() => setTokenRevealed((v) => !v)}
            className="p-2.5 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface transition-colors text-aegisaxiom-text-secondary hover:text-aegisaxiom-text shrink-0"
            title={tokenRevealed ? 'Hide token' : 'Reveal token'}
            aria-label={tokenRevealed ? 'Hide token' : 'Reveal token'}
          >
            {tokenRevealed ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>

          {/* Copy */}
          <button
            onClick={handleCopy}
            disabled={!settings?.n8n_token}
            className="p-2.5 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface transition-colors text-aegisaxiom-text-secondary hover:text-aegisaxiom-text disabled:opacity-40 shrink-0"
            title="Copy token to clipboard"
            aria-label="Copy token to clipboard"
          >
            {copied ? <Check size={16} className="text-green-400" /> : <Copy size={16} />}
          </button>
        </div>

        {/* Rotate */}
        {canRotate && (
          <div>
            <button
              onClick={() => setRotateConfirmOpen(true)}
              className="btn-secondary flex items-center gap-2 text-sm"
            >
              <RefreshCw size={15} />
              Regenerate Token
            </button>
            <p className="text-xs text-aegisaxiom-text-muted mt-1.5">
              Rotating the token immediately invalidates the old one. Update your n8n
              credential before the next workflow run.
            </p>
          </div>
        )}
      </div>

      {/* Linked Workflow URL */}
      <div className="glass-card p-5 space-y-4">
        <div>
          <h3 className="text-sm font-semibold text-aegisaxiom-text mb-1">Linked Workflow URL</h3>
          <p className="text-xs text-aegisaxiom-text-muted">
            Paste the n8n workflow URL here for visibility (optional). This is not used for auth.
          </p>
        </div>

        <div className="flex gap-2">
          <input
            className="input-field flex-1"
            placeholder="https://your-n8n-instance.com/workflow/…"
            value={workflowUrl}
            onChange={(e) => setWorkflowUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSaveUrl();
            }}
            disabled={!canUpdate}
            aria-label="Linked workflow URL"
          />
          {canUpdate && (
            <button
              onClick={handleSaveUrl}
              disabled={savingUrl}
              className="btn-primary flex items-center gap-2 shrink-0"
            >
              <Save size={15} />
              {savingUrl ? 'Saving…' : 'Save'}
            </button>
          )}
        </div>

        {settings?.linked_workflow_url && (
          <a
            href={settings.linked_workflow_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-violet-400 hover:text-violet-300 break-all block"
          >
            {settings.linked_workflow_url}
          </a>
        )}
      </div>

      {/* Rotate confirmation modal */}
      {rotateConfirmOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={(e) => { if (e.target === e.currentTarget) setRotateConfirmOpen(false); }}
        >
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl w-full max-w-md mx-4 p-6">
            <h2 className="text-base font-semibold text-aegisaxiom-text mb-2">Regenerate Token?</h2>
            <div className="bg-amber-500/10 border border-amber-500/20 rounded-lg p-4 mb-5">
              <p className="text-sm text-amber-300 leading-relaxed">
                Rotating this token will <strong>immediately break</strong> the n8n workflow
                until you paste the new value into the n8n credential. Make sure you can
                update n8n right now before proceeding.
              </p>
            </div>
            <div className="flex gap-3">
              <button
                onClick={() => setRotateConfirmOpen(false)}
                className="btn-secondary flex-1"
                disabled={rotating}
              >
                Cancel
              </button>
              <button
                onClick={handleRotate}
                disabled={rotating}
                className="flex-1 px-4 py-2.5 bg-red-600 hover:bg-red-700 text-white rounded-lg transition-colors text-sm font-medium"
              >
                {rotating ? 'Rotating…' : 'Yes, Rotate Token'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
