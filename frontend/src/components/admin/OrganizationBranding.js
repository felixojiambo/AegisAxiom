import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import API_URL from '../../lib/apiConfig';
import { Save, RotateCcw, Palette, Image as ImageIcon } from 'lucide-react';

const API = API_URL;

// Cyan is the default *document* brand color (used in generated SOW PDFs),
// not the AEGISAXIOM UI accent. Keeping it preserves existing branding for orgs
// that haven't customized.
const DEFAULT_PRIMARY = '#06b6d4';

const HEX_PATTERN = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;

export default function OrganizationBranding() {
  const [branding, setBranding] = useState(null);
  const [form, setForm] = useState({
    provider_name: '',
    legal_text: '',
    logo_url: '',
    primary_color: '',
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [logoLoadError, setLogoLoadError] = useState(false);

  useEffect(() => {
    loadBranding();
  }, []);

  // Reset logo error state whenever the URL changes so a corrected URL
  // gets a fresh load attempt.
  useEffect(() => {
    setLogoLoadError(false);
  }, [form.logo_url]);

  const loadBranding = async () => {
    setLoading(true);
    const res = await api.get(`${API}/api/admin/branding`);
    if (res.ok) {
      const data = await res.json();
      setBranding(data);
      setForm({
        provider_name: data.provider_name || '',
        legal_text: data.legal_text || '',
        logo_url: data.logo_url || '',
        primary_color: data.primary_color || '',
      });
      setDirty(false);
    } else {
      toast.error('Failed to load branding settings');
    }
    setLoading(false);
  };

  const handleChange = (field, value) => {
    setForm((prev) => ({ ...prev, [field]: value }));
    setDirty(true);
  };

  const handleSave = async () => {
    setSaving(true);
    const payload = {};
    if (form.provider_name) payload.provider_name = form.provider_name;
    if (form.legal_text) payload.legal_text = form.legal_text;
    if (form.logo_url) payload.logo_url = form.logo_url;
    if (form.primary_color) payload.primary_color = form.primary_color;

    const res = await api.put(`${API}/api/admin/branding`, payload);
    if (res.ok) {
      const data = await res.json();
      setBranding(data);
      setDirty(false);
      toast.success('Branding settings saved');
    } else {
      const err = await res.json();
      toast.error(err.detail || 'Failed to save branding settings');
    }
    setSaving(false);
  };

  const handleReset = () => {
    if (branding) {
      setForm({
        provider_name: branding.provider_name || '',
        legal_text: branding.legal_text || '',
        logo_url: branding.logo_url || '',
        primary_color: branding.primary_color || '',
      });
      setDirty(false);
    }
  };

  if (loading) {
    return (
      <div className="p-6 flex items-center justify-center min-h-[400px]">
        <div className="animate-spin h-8 w-8 border-2 border-violet-500 border-t-transparent rounded-full" />
      </div>
    );
  }

  const hexValid = !form.primary_color || HEX_PATTERN.test(form.primary_color);
  const showLogoPreview = form.logo_url && !logoLoadError;

  return (
    <div className="p-4 sm:p-6 max-w-3xl mx-auto" data-testid="organization-branding-page">
      {/* Page header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-aegisaxiom-text flex items-center gap-2">
            <Palette size={22} className="text-violet-600 dark:text-violet-400" />
            Organization Branding
          </h1>
          <p className="text-aegisaxiom-text-secondary text-sm mt-1 leading-snug">
            Customize how your organization appears on generated SOW documents.
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {dirty && (
            <button
              onClick={handleReset}
              className="btn-secondary text-xs px-3 py-2 flex items-center gap-1.5 flex-1 sm:flex-none justify-center"
              data-testid="discard-btn"
            >
              <RotateCcw size={14} /> Discard
            </button>
          )}
          <button
            onClick={handleSave}
            disabled={!dirty || saving || !hexValid}
            className="btn-primary text-xs px-4 py-2 flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed flex-1 sm:flex-none justify-center"
            data-testid="save-btn"
          >
            <Save size={14} /> {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>

      {/* Form card */}
      <div className="glass-card p-4 sm:p-6">
        <div className="mb-5">
          <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-1">
            Provider Name
          </label>
          <input
            type="text"
            value={form.provider_name}
            onChange={(e) => handleChange('provider_name', e.target.value)}
            placeholder="MetaData Engineering d/b/a SimpliMeta"
            className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-500/20 transition-colors"
            data-testid="provider-name-input"
          />
          <p className="text-aegisaxiom-text-muted text-xs mt-1">
            Appears in the signature block of SOW documents.
          </p>
        </div>

        <div className="mb-5">
          <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-1">
            Legal Text
          </label>
          <textarea
            value={form.legal_text}
            onChange={(e) => handleChange('legal_text', e.target.value)}
            rows={4}
            placeholder="Unless otherwise agreed to in this document..."
            className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-500/20 resize-vertical transition-colors"
            data-testid="legal-text-input"
          />
          <p className="text-aegisaxiom-text-muted text-xs mt-1">
            Legal terms and conditions displayed in the SOW document footer.
          </p>
        </div>

        <div className="mb-5">
          <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-1">
            Logo URL
          </label>
          <input
            type="url"
            value={form.logo_url}
            onChange={(e) => handleChange('logo_url', e.target.value)}
            placeholder="https://example.com/logo.png"
            className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-500/20 transition-colors"
            data-testid="logo-url-input"
          />
          <p className="text-aegisaxiom-text-muted text-xs mt-1">
            URL to your organization's logo (used in document headers).
          </p>
          {form.logo_url && (
            <div
              className="mt-3 p-3 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg flex items-center gap-3"
              data-testid="logo-preview"
            >
              <div className="w-12 h-12 rounded-md bg-aegisaxiom-base border border-aegisaxiom-border flex items-center justify-center overflow-hidden shrink-0">
                {showLogoPreview ? (
                  <img
                    src={form.logo_url}
                    alt="Logo preview"
                    className="max-w-full max-h-full object-contain"
                    onError={() => setLogoLoadError(true)}
                  />
                ) : (
                  <ImageIcon size={18} className="text-aegisaxiom-text-muted" />
                )}
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium text-aegisaxiom-text-secondary mb-0.5">Logo preview</p>
                <p
                  className={`text-[11px] leading-tight ${logoLoadError ? 'text-red-600 dark:text-red-400' : 'text-aegisaxiom-text-muted'}`}
                >
                  {logoLoadError
                    ? 'Could not load image — verify the URL is correct and publicly accessible.'
                    : 'Image loaded successfully.'}
                </p>
              </div>
            </div>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-1">
            Primary Color
          </label>
          <div className="flex items-center gap-3">
            <input
              type="color"
              value={form.primary_color || DEFAULT_PRIMARY}
              onChange={(e) => handleChange('primary_color', e.target.value)}
              className="h-10 w-14 rounded-lg cursor-pointer border border-aegisaxiom-border bg-aegisaxiom-elevated shrink-0"
              aria-label="Primary color picker"
              data-testid="color-picker"
            />
            <input
              type="text"
              value={form.primary_color}
              onChange={(e) => handleChange('primary_color', e.target.value)}
              placeholder={DEFAULT_PRIMARY}
              className={`flex-1 min-w-0 px-3 py-2 bg-aegisaxiom-elevated border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:ring-2 transition-colors font-mono ${
                hexValid
                  ? 'border-aegisaxiom-border focus:border-violet-500 focus:ring-violet-500/20'
                  : 'border-red-500 focus:border-red-500 focus:ring-red-500/20'
              }`}
              data-testid="color-hex-input"
            />
          </div>
          <p
            className={`text-xs mt-1 ${
              hexValid ? 'text-aegisaxiom-text-muted' : 'text-red-600 dark:text-red-400'
            }`}
          >
            {hexValid
              ? 'Brand color used for document accents.'
              : 'Enter a valid hex color (e.g. #06b6d4 or #fff).'}
          </p>
        </div>
      </div>

      {branding?.updated_at && (
        <p className="text-aegisaxiom-text-muted text-xs text-right mt-4">
          Last updated: {new Date(branding.updated_at).toLocaleString()}
        </p>
      )}
    </div>
  );
}
