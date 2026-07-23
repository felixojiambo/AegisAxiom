import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { useAuth } from '../../contexts/AuthContext';
import { useSearchParams } from 'react-router-dom';
import { ExternalLink, CheckCircle, XCircle, RefreshCw, AlertTriangle, Plug } from 'lucide-react';
import { toast } from 'sonner';
import API_URL from '../../lib/apiConfig';

const API = API_URL;
function IntegrationCard({ name, icon, status, onConnect, onDisconnect, onSync, description }) {
  return (
    <div
      className="glass-card p-5 sm:p-6"
      data-testid={`integration-${name.toLowerCase().replace(/\s/g, '-')}`}
    >
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 mb-4">
        <div className="flex items-start gap-3 min-w-0 flex-1">
          <div className="w-10 h-10 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border flex items-center justify-center text-lg shrink-0">
            {icon}
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-aegisaxiom-text font-semibold text-sm">{name}</p>
            <p className="text-aegisaxiom-text-muted text-xs leading-snug mt-0.5">{description}</p>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {status?.connected ? (
            <CheckCircle size={16} className="text-emerald-600 dark:text-emerald-400" />
          ) : (
            <XCircle size={16} className="text-aegisaxiom-text-muted" />
          )}
          <span
            className={`text-xs ${status?.connected ? 'text-emerald-600 dark:text-emerald-400' : 'text-aegisaxiom-text-muted'}`}
          >
            {status?.connected ? 'Connected' : 'Not Connected'}
          </span>
        </div>
      </div>
      {status?.connected && status?.expires_at && (
        <p className="text-aegisaxiom-text-muted text-xs mb-4">
          Token expires: {new Date(status.expires_at).toLocaleString()}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {!status?.connected ? (
          <button
            onClick={onConnect}
            className="btn-primary text-xs px-4 py-2 flex items-center gap-1.5"
            data-testid={`connect-${name.toLowerCase().replace(/\s/g, '-')}`}
          >
            <ExternalLink size={13} />
            Connect
          </button>
        ) : (
          <>
            {onSync && (
              <button
                onClick={onSync}
                className="btn-secondary text-xs px-3 py-2 flex items-center gap-1.5"
                data-testid={`sync-${name.toLowerCase().replace(/\s/g, '-')}`}
              >
                <RefreshCw size={13} />
                Sync Now
              </button>
            )}
            <button
              onClick={onDisconnect}
              className="text-xs px-3 py-2 text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 bg-red-50 dark:bg-red-600/10 hover:bg-red-100 dark:hover:bg-red-600/20 rounded-lg transition-colors border border-red-200 dark:border-red-900/30"
              data-testid={`disconnect-${name.toLowerCase().replace(/\s/g, '-')}`}
            >
              Disconnect
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export default function Integrations() {
  const [status, setStatus] = useState({ zoho: {} });
  const [loading, setLoading] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    loadStatus();
    // Handle OAuth callback redirects
    const zohoParam = searchParams.get('zoho');
    if (zohoParam === 'connected') {
      toast.success('Zoho connected successfully! Starting initial sync...');
      setSearchParams({});
      // Auto-trigger sync after connection
      setTimeout(async () => {
        await syncZoho();
        await loadStatus();
      }, 500);
    } else if (zohoParam === 'error') {
      toast.error(
        `Zoho connection failed: ${decodeURIComponent(searchParams.get('msg') || 'Unknown error')}`,
      );
      setSearchParams({});
    }
  }, []);
  const loadStatus = async () => {
    const res = await api.get(`${API}/api/integrations/status`);
    if (res.ok) setStatus(await res.json());
  };
  const connectZoho = async () => {
    const res = await api.get(`${API}/api/integrations/zoho/auth-url`);
    if (res.ok) {
      const { url } = await res.json();
      if (url.startsWith('#')) {
        toast.error(
          'Zoho credentials not configured. Please add ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET to your backend .env file.',
        );
        return;
      }
      window.location.href = url;
    }
  };
  const disconnectZoho = async () => {
    const res = await api.delete(`${API}/api/integrations/zoho/disconnect`);
    if (res.ok) {
      toast.success('Zoho disconnected');
      loadStatus();
    }
  };
  const syncZoho = async () => {
    setLoading(true);
    try {
      const results = await Promise.all([
        api.post(`${API}/api/integrations/sync/zoho`, {}),
        api.post(`${API}/api/integrations/sync/zoho/deals`, {}),
        api.post(`${API}/api/integrations/sync/zoho/commissions`, {}),
        api.post(`${API}/api/integrations/sync/zoho/deals-enriched`, {}),
      ]);
      const messages = await Promise.all(results.filter((r) => r.ok).map((r) => r.json()));
      messages.forEach((d) => toast.success(d.message));
    } catch (e) {
      toast.error('Sync failed');
    }
    setLoading(false);
  };
  return (
    <div data-testid="integrations-page">
      {/* Page header */}
      <div className="mb-6">
        <h1 className="page-title flex items-center gap-2">
          <Plug size={22} className="text-violet-600 dark:text-violet-400" />
          Integrations
        </h1>
        <p className="text-aegisaxiom-text-muted text-sm mt-1">
          Connect AEGISAXIOM to your Zoho environment.
        </p>
      </div>

      {/* Credentials banner */}
      <div className="glass-card p-4 border-amber-200 dark:border-amber-600/30 flex items-start gap-3 mb-6">
        <AlertTriangle size={18} className="text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
        <div className="min-w-0">
          <p className="text-amber-700 dark:text-amber-300 text-sm font-medium">
            Credentials Required
          </p>
          <p className="text-aegisaxiom-text-secondary text-xs mt-1 leading-relaxed">
            To activate these integrations, add your API credentials to{' '}
            <code className="text-violet-700 dark:text-violet-300 font-mono break-all">
              /app/backend/.env
            </code>
            . Contact your SimpliMeta technical team for OAuth app setup.
          </p>
        </div>
      </div>

      {/* Integration cards grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        <IntegrationCard
          name="Zoho"
          icon="🟦"
          description="Sync Accounts, Deals, Commission Data & Support Tickets"
          status={status.zoho}
          onConnect={connectZoho}
          onDisconnect={disconnectZoho}
          onSync={syncZoho}
        />
        <div className="glass-card p-5 sm:p-6 opacity-60">
          <div className="flex items-start gap-3 mb-3">
            <div className="w-10 h-10 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border flex items-center justify-center shrink-0">
              🟦
            </div>
            <div className="min-w-0">
              <p className="text-aegisaxiom-text-secondary text-sm font-semibold">Zoho Desk</p>
              <p className="text-aegisaxiom-text-muted text-xs leading-snug mt-0.5">
                Support ticket management
              </p>
            </div>
          </div>
          <span className="inline-block text-xs px-2 py-1 bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border rounded-lg">
            Configured via Zoho token
          </span>
        </div>
        <div className="glass-card p-5 sm:p-6 opacity-60">
          <div className="flex items-start gap-3 mb-3">
            <div className="w-10 h-10 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border flex items-center justify-center shrink-0">
              🟦
            </div>
            <div className="min-w-0">
              <p className="text-aegisaxiom-text-secondary text-sm font-semibold">Zoho Projects</p>
              <p className="text-aegisaxiom-text-muted text-xs leading-snug mt-0.5">
                Time logs, billable hours sync
              </p>
            </div>
          </div>
          <span className="inline-block text-xs px-2 py-1 bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border rounded-lg">
            Configured via Zoho token
          </span>
        </div>
      </div>

      {/* OAuth + Webhook URLs reference card */}
      <div className="glass-card p-5 sm:p-6">
        <h3
          className="text-sm font-semibold text-aegisaxiom-text mb-1"
          style={{ fontFamily: 'Manrope, sans-serif' }}
        >
          OAuth Redirect URIs
        </h3>
        <p className="text-aegisaxiom-text-muted text-xs mb-4 leading-relaxed">
          Register these callback URLs in your Zoho API console under{' '}
          <strong className="text-aegisaxiom-text-secondary">Authorized Redirect URIs</strong>:
        </p>
        <div className="mb-6">
          {[
            {
              label: 'Zoho CRM — Redirect URI',
              url: `${window.location.origin}/api/integrations/zoho/callback`,
            },
          ].map((w) => (
            <UrlRow key={w.label} label={w.label} url={w.url} />
          ))}
        </div>

        <h3
          className="text-sm font-semibold text-aegisaxiom-text mb-1"
          style={{ fontFamily: 'Manrope, sans-serif' }}
        >
          Webhook Endpoints
        </h3>
        <p className="text-aegisaxiom-text-muted text-xs mb-4 leading-relaxed">
          Configure these URLs in your Zoho settings to enable real-time sync:
        </p>
        <div>
          {[
            { label: 'Zoho', url: `${API}/api/integrations/webhooks/zoho/crm` },
          ].map((w) => (
            <UrlRow key={w.label} label={w.label} url={w.url} />
          ))}
        </div>
      </div>
    </div>
  );
}

// Reusable label + URL + Copy button row, responsive
function UrlRow({ label, url }) {
  const handleCopy = () => {
    try {
      navigator.clipboard.writeText(url);
      toast.success('Copied!');
    } catch {
      toast.error('Copy failed — use manual copy');
    }
  };
  return (
    <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 sm:gap-3 bg-aegisaxiom-base rounded-lg px-3 sm:px-4 py-3 border border-aegisaxiom-border mb-2 last:mb-0">
      <span className="text-aegisaxiom-text-secondary text-xs shrink-0 font-medium">{label}</span>
      <div className="flex items-center gap-2 min-w-0 sm:flex-1 sm:justify-end">
        <code className="text-violet-700 dark:text-violet-300 text-xs font-mono truncate min-w-0 flex-1 sm:flex-initial sm:max-w-md">
          {url}
        </code>
        <button
          onClick={handleCopy}
          className="text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 text-xs transition-colors shrink-0 px-1.5 py-0.5 rounded"
          aria-label={`Copy ${label}`}
        >
          Copy
        </button>
      </div>
    </div>
  );
}
