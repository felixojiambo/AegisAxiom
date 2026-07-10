import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Edit2, PlusCircle, Sparkles, Trash2, X } from 'lucide-react';
import { api, unwrap } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';
import ConfirmModal from '@/components/shared/ConfirmModal';

const BASE = `${API_URL}/api/match`;

// Known transform options. Kept in sync with backend _apply_transform().
const TRANSFORM_OPTIONS = ['strip_usd_prefix', 'normalize_prc', 'parse_date', 'none'];

// ---------------------------------------------------------------------------
// Phase 4 deferral — client-side n8n suggestion translation shim.
//
// The server-side N8nClient.suggest_mappings() translator has NOT been built
// yet (deferred to Phase 4). Until it lands, the n8n /webhook/suggest-mappings
// endpoint returns the legacy MATCH source shape:
//   { provider_column, appdirect_field, transform (singular) }
//
// This shim translates that into the AEGISAXIOM canonical ColumnMapping shape:
//   { source_column, target_path, transforms[] (list), required }
//
// REMOVAL NOTE: when the server-side translator in N8nClient.suggest_mappings()
// is implemented (Phase 4), delete this function and the call site below — the
// backend will return the canonical shape directly. Search for
// "translateN8nSuggestions" and "Phase 4 deferral" to find both locations.
// ---------------------------------------------------------------------------
function translateN8nSuggestions(rawSuggestions) {
  return (rawSuggestions || []).map((s) => ({
    source_column: s.provider_column ?? s.source_column ?? '',
    target_path:   s.appdirect_field ?? s.target_path ?? '',
    transforms:    s.transforms ?? (s.transform ? [s.transform] : []),
    required:      Boolean(s.required ?? false),
  }));
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function emptyMapping() {
  return { source_column: '', target_path: '', transforms: [], required: false };
}

function emptyRule() {
  return { name: '', condition_path: '', condition_op: '==', condition_value: '', action_path: '', action_value: '' };
}

function emptyTemplate() {
  return {
    name: '',
    default_product: '',
    sales_rep_priority: '',
    default_order_status: '',
    column_mappings: [],
    business_rules: [],
    appdirect_advisor_id: '',
    appdirect_product_id: '',
    appdirect_provider_id: '',
    appdirect_account_manager_id: '',
  };
}

// Convert template record -> form state (stringify numeric IDs to avoid empty-string issues)
function templateToForm(t) {
  return {
    name: t.name || '',
    default_product: t.default_product || '',
    sales_rep_priority: t.sales_rep_priority || '',
    default_order_status: t.default_order_status || '',
    column_mappings: (t.column_mappings || []).map((m) => ({
      source_column: m.source_column || '',
      target_path: m.target_path || '',
      transforms: Array.isArray(m.transforms) ? m.transforms : [],
      required: Boolean(m.required),
    })),
    business_rules: (t.business_rules || []).map((r) => ({
      name: r.name || '',
      condition_path: r.condition_path || '',
      condition_op: r.condition_op || '==',
      condition_value: r.condition_value ?? '',
      action_path: r.action_path || '',
      action_value: r.action_value ?? '',
    })),
    appdirect_advisor_id: t.appdirect_advisor_id != null ? String(t.appdirect_advisor_id) : '',
    appdirect_product_id: t.appdirect_product_id != null ? String(t.appdirect_product_id) : '',
    appdirect_provider_id: t.appdirect_provider_id != null ? String(t.appdirect_provider_id) : '',
    appdirect_account_manager_id: t.appdirect_account_manager_id != null ? String(t.appdirect_account_manager_id) : '',
  };
}

// Convert form state -> API body (parse numeric ID strings back to numbers or null)
function formToBody(form) {
  function toIntOrNull(v) {
    const n = parseInt(v, 10);
    return isNaN(n) ? null : n;
  }
  return {
    name: form.name,
    default_product: form.default_product || null,
    sales_rep_priority: form.sales_rep_priority || null,
    default_order_status: form.default_order_status || null,
    column_mappings: form.column_mappings.map((m) => ({
      source_column: m.source_column,
      target_path: m.target_path,
      transforms: m.transforms.filter((t) => t && t !== 'none'),
      required: Boolean(m.required),
    })),
    business_rules: form.business_rules.map((r) => ({
      name: r.name,
      condition_path: r.condition_path,
      condition_op: r.condition_op,
      condition_value: r.condition_value,
      action_path: r.action_path,
      action_value: r.action_value,
    })),
    appdirect_advisor_id: toIntOrNull(form.appdirect_advisor_id),
    appdirect_product_id: toIntOrNull(form.appdirect_product_id),
    appdirect_provider_id: toIntOrNull(form.appdirect_provider_id),
    appdirect_account_manager_id: toIntOrNull(form.appdirect_account_manager_id),
  };
}

// ---------------------------------------------------------------------------
// Column Mappings Editor — used inside the Edit Modal
// ---------------------------------------------------------------------------

function ColumnMappingsEditor({ mappings, onChange }) {
  function updateRow(idx, field, value) {
    const next = mappings.map((m, i) => (i === idx ? { ...m, [field]: value } : m));
    onChange(next);
  }

  function toggleTransform(idx, transform) {
    const m = mappings[idx];
    const current = m.transforms || [];
    const updated = current.includes(transform)
      ? current.filter((t) => t !== transform)
      : [...current, transform];
    updateRow(idx, 'transforms', updated);
  }

  function addRow() { onChange([...mappings, emptyMapping()]); }
  function removeRow(idx) { onChange(mappings.filter((_, i) => i !== idx)); }

  return (
    <div className="space-y-2">
      {mappings.length === 0 && (
        <p className="text-xs text-aegisaxiom-text-muted py-2">No mappings yet. Add one below.</p>
      )}
      {mappings.map((m, idx) => (
        <div key={idx} className="grid grid-cols-[1fr_1fr_auto] gap-2 items-start p-3 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border">
          <div className="space-y-1">
            <label className="block text-[10px] text-aegisaxiom-text-muted uppercase tracking-wide">Source Column</label>
            <input
              className="input-field w-full text-xs"
              placeholder="CSV header name"
              value={m.source_column}
              onChange={(e) => updateRow(idx, 'source_column', e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <label className="block text-[10px] text-aegisaxiom-text-muted uppercase tracking-wide">
              Target Path{' '}
              <span
                className="text-violet-400 cursor-default"
                title="AppDirect Contract H field path, e.g. output.customer.name"
              >
                (?)
              </span>
            </label>
            <input
              className="input-field w-full text-xs font-mono"
              placeholder="output.customer.name"
              value={m.target_path}
              onChange={(e) => updateRow(idx, 'target_path', e.target.value)}
            />
          </div>
          <button
            type="button"
            onClick={() => removeRow(idx)}
            className="mt-5 p-1 rounded text-aegisaxiom-text-muted hover:text-red-400 hover:bg-red-500/10 transition"
          >
            <X size={14} />
          </button>
          {/* Transforms + Required — full-width row beneath the two inputs */}
          <div className="col-span-3 flex flex-wrap items-center gap-2 pt-1">
            <span className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wide shrink-0">Transforms:</span>
            {TRANSFORM_OPTIONS.filter((t) => t !== 'none').map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => toggleTransform(idx, t)}
                className={`px-2 py-0.5 rounded text-[11px] border transition ${
                  m.transforms.includes(t)
                    ? 'bg-violet-500/20 border-violet-500/40 text-violet-300'
                    : 'bg-aegisaxiom-surface border-aegisaxiom-border text-aegisaxiom-text-muted hover:border-aegisaxiom-border-hover'
                }`}
              >
                {t}
              </button>
            ))}
            <label className="ml-auto flex items-center gap-1 text-xs text-aegisaxiom-text-secondary cursor-pointer select-none">
              <input
                type="checkbox"
                checked={Boolean(m.required)}
                onChange={(e) => updateRow(idx, 'required', e.target.checked)}
                className="rounded border-aegisaxiom-border"
              />
              Required
            </label>
          </div>
        </div>
      ))}
      <button
        type="button"
        onClick={addRow}
        className="flex items-center gap-1 text-xs text-violet-400 hover:text-violet-300 transition px-1 py-0.5"
      >
        <PlusCircle size={13} /> Add Mapping
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Business Rules Editor — used inside the Edit Modal
// ---------------------------------------------------------------------------

function BusinessRulesEditor({ rules, onChange }) {
  function updateRow(idx, field, value) {
    const next = rules.map((r, i) => (i === idx ? { ...r, [field]: value } : r));
    onChange(next);
  }
  function addRow() { onChange([...rules, emptyRule()]); }
  function removeRow(idx) { onChange(rules.filter((_, i) => i !== idx)); }

  return (
    <div className="space-y-2">
      {rules.length === 0 && (
        <p className="text-xs text-aegisaxiom-text-muted py-2">No business rules yet. Add one below.</p>
      )}
      {rules.map((r, idx) => (
        <div key={idx} className="p-3 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border space-y-2">
          <div className="flex items-center gap-2">
            <input
              className="input-field flex-1 text-xs"
              placeholder="Rule name"
              value={r.name}
              onChange={(e) => updateRow(idx, 'name', e.target.value)}
            />
            <button
              type="button"
              onClick={() => removeRow(idx)}
              className="p-1 rounded text-aegisaxiom-text-muted hover:text-red-400 hover:bg-red-500/10 transition"
            >
              <X size={14} />
            </button>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="block text-[10px] text-aegisaxiom-text-muted mb-1">Condition Path</label>
              <input
                className="input-field w-full text-xs font-mono"
                placeholder="output.customer.type"
                value={r.condition_path}
                onChange={(e) => updateRow(idx, 'condition_path', e.target.value)}
              />
            </div>
            <div>
              <label className="block text-[10px] text-aegisaxiom-text-muted mb-1">Operator</label>
              <select
                className="input-field w-full text-xs"
                value={r.condition_op}
                onChange={(e) => updateRow(idx, 'condition_op', e.target.value)}
              >
                {['==', '!=', '>', '<', '>=', '<=', 'contains', 'startsWith'].map((op) => (
                  <option key={op} value={op}>{op}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[10px] text-aegisaxiom-text-muted mb-1">Condition Value</label>
              <input
                className="input-field w-full text-xs"
                placeholder="Enterprise"
                value={r.condition_value}
                onChange={(e) => updateRow(idx, 'condition_value', e.target.value)}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-[10px] text-aegisaxiom-text-muted mb-1">Action Path</label>
              <input
                className="input-field w-full text-xs font-mono"
                placeholder="output.order_type"
                value={r.action_path}
                onChange={(e) => updateRow(idx, 'action_path', e.target.value)}
              />
            </div>
            <div>
              <label className="block text-[10px] text-aegisaxiom-text-muted mb-1">Action Value</label>
              <input
                className="input-field w-full text-xs"
                placeholder="Enterprise_Order"
                value={r.action_value}
                onChange={(e) => updateRow(idx, 'action_value', e.target.value)}
              />
            </div>
          </div>
        </div>
      ))}
      <button
        type="button"
        onClick={addRow}
        className="flex items-center gap-1 text-xs text-violet-400 hover:text-violet-300 transition px-1 py-0.5"
      >
        <PlusCircle size={13} /> Add Rule
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Magic Mapping sub-panel — lives inside the Column Mappings section of the
// Edit Modal. Calls /suggest-mappings, runs the Phase-4 translation shim,
// and lets the user merge or overwrite existing mappings.
// ---------------------------------------------------------------------------

function MagicMappingPanel({ onApply, onClose }) {
  const [reportUrl, setReportUrl] = useState('');
  const [customersUrl, setCustomersUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [suggestions, setSuggestions] = useState(null);

  async function handleFetch() {
    if (!reportUrl.trim() || !customersUrl.trim()) {
      setError('Both URLs are required.');
      return;
    }
    setLoading(true);
    setError(null);
    setSuggestions(null);
    try {
      const res = await api.post(`${BASE}/provider-templates/suggest-mappings`, {
        provider_report_url: reportUrl,
        snapshot_customers_url: customersUrl,
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail || 'Suggest-mappings request failed.');
        return;
      }
      // Phase 4 deferral: translate n8n legacy shape to AEGISAXIOM canonical shape.
      // See translateN8nSuggestions() above for removal instructions.
      const translated = translateN8nSuggestions(data);
      setSuggestions(translated);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="rounded-lg border border-violet-500/30 bg-violet-500/5 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-violet-300 text-sm font-medium">
          <Sparkles size={14} />
          Magic Mapping
        </div>
        <button
          type="button"
          onClick={onClose}
          className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary transition"
        >
          <X size={14} />
        </button>
      </div>

      {!suggestions ? (
        <div className="space-y-2">
          <div>
            <label className="block text-xs text-aegisaxiom-text-muted mb-1">Provider Report URL</label>
            <input
              className="input-field w-full text-xs"
              placeholder="https://..."
              value={reportUrl}
              onChange={(e) => setReportUrl(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs text-aegisaxiom-text-muted mb-1">Snapshot Customers URL</label>
            <input
              className="input-field w-full text-xs"
              placeholder="https://..."
              value={customersUrl}
              onChange={(e) => setCustomersUrl(e.target.value)}
            />
          </div>
          {error && (
            <p className="text-xs text-red-400">{error}</p>
          )}
          <button
            type="button"
            onClick={handleFetch}
            disabled={loading}
            className="btn-primary text-xs px-3 py-1.5 disabled:opacity-50"
          >
            {loading ? 'Asking n8n…' : 'Get Suggestions'}
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-aegisaxiom-text-secondary">
            n8n suggested <strong>{suggestions.length}</strong> mapping{suggestions.length !== 1 ? 's' : ''}.
          </p>
          <div className="max-h-48 overflow-y-auto space-y-1 pr-1">
            {suggestions.map((s, i) => (
              <div key={i} className="flex items-center gap-2 text-xs py-0.5">
                <span className="text-aegisaxiom-text-secondary font-mono flex-1 truncate">{s.source_column || '—'}</span>
                <span className="text-aegisaxiom-text-muted">→</span>
                <span className="text-violet-300 font-mono flex-1 truncate">{s.target_path || '—'}</span>
                {s.transforms?.length > 0 && (
                  <span className="text-aegisaxiom-text-muted text-[10px]">[{s.transforms.join(', ')}]</span>
                )}
              </div>
            ))}
          </div>
          <div className="flex gap-2 pt-1">
            <button
              type="button"
              onClick={() => { onApply(suggestions, 'overwrite'); setSuggestions(null); }}
              className="btn-primary text-xs px-3 py-1.5"
            >
              Overwrite
            </button>
            <button
              type="button"
              onClick={() => { onApply(suggestions, 'merge'); setSuggestions(null); }}
              className="flex-1 text-xs px-3 py-1.5 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text transition"
            >
              Merge
            </button>
            <button
              type="button"
              onClick={() => setSuggestions(null)}
              className="text-xs px-3 py-1.5 text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary transition"
            >
              Discard
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Test Template Modal — uploads a sample CSV, shows row-by-row preview
// ---------------------------------------------------------------------------

function TestTemplateModal({ template, onClose }) {
  const fileRef = useRef(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  async function handleTest() {
    const file = fileRef.current?.files?.[0];
    if (!file) { setError('Select a CSV file first.'); return; }

    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await api.postForm(`${BASE}/provider-templates/${template.template_id}/test`, fd);
      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail || 'Test request failed.');
        return;
      }
      setResult(data);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-start justify-center p-4 pt-16 bg-black/60 overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-labelledby="test-template-modal-title"
    >
      <div className="absolute inset-0" onClick={onClose} aria-hidden="true" />
      <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl w-full max-w-3xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-aegisaxiom-border">
          <h2 id="test-template-modal-title" className="text-sm font-semibold text-aegisaxiom-text">
            Test Template — <span className="text-aegisaxiom-text-secondary">{template.name}</span>
          </h2>
          <button onClick={onClose} className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary transition">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-4">
          <p className="text-sm text-aegisaxiom-text-secondary">
            Upload a sample provider CSV to preview how the template's column mappings will transform the first 5 rows.
          </p>
          <div className="flex items-center gap-3">
            <input
              ref={fileRef}
              type="file"
              accept=".csv"
              className="text-sm text-aegisaxiom-text-secondary file:mr-3 file:py-1 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-medium file:bg-violet-500/20 file:text-violet-300 hover:file:bg-violet-500/30 cursor-pointer"
            />
            <button
              type="button"
              onClick={handleTest}
              disabled={loading}
              className="btn-primary text-sm px-4 py-1.5 disabled:opacity-50"
            >
              {loading ? 'Running…' : 'Run Preview'}
            </button>
          </div>

          {error && (
            <div className="px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
              {error}
            </div>
          )}

          {result && (
            <div className="space-y-3">
              <div className="flex items-center gap-3 text-sm">
                <span className={`px-2 py-0.5 rounded text-xs font-medium border ${
                  result.status === 'success'
                    ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/25'
                    : 'bg-amber-500/15 text-amber-400 border-amber-500/25'
                }`}>
                  {result.status}
                </span>
                <span className="text-aegisaxiom-text-secondary">{result.message}</span>
              </div>

              {result.warnings?.length > 0 && (
                <div className="px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-400 text-xs space-y-0.5">
                  {result.warnings.map((w, i) => <p key={i}>{w}</p>)}
                </div>
              )}

              {result.preview?.length > 0 && (
                <div className="space-y-3 max-h-[420px] overflow-y-auto pr-1">
                  {result.preview.map((row) => (
                    <div key={row.row_index} className="rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border overflow-hidden">
                      <div className="px-3 py-1.5 border-b border-aegisaxiom-border text-xs font-medium text-aegisaxiom-text-secondary">
                        Row {row.row_index + 1}
                      </div>
                      <div className="grid grid-cols-2 divide-x divide-aegisaxiom-border">
                        <div className="p-3">
                          <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wide mb-2">Source</p>
                          <pre className="text-[11px] text-aegisaxiom-text-secondary whitespace-pre-wrap break-all leading-relaxed">
                            {JSON.stringify(row.source, null, 2)}
                          </pre>
                        </div>
                        <div className="p-3">
                          <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wide mb-2">Extracted</p>
                          <pre className="text-[11px] text-violet-300 whitespace-pre-wrap break-all leading-relaxed">
                            {JSON.stringify(row.extracted, null, 2)}
                          </pre>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Edit Modal — create or edit a provider template
// ---------------------------------------------------------------------------

const EDIT_SECTIONS = ['Basic', 'Column Mappings', 'Business Rules'];

function EditTemplateModal({ template, onSuccess, onClose }) {
  const isCreate = !template?.template_id;
  const [form, setForm] = useState(() => template ? templateToForm(template) : emptyTemplate());
  const [activeSection, setActiveSection] = useState('Basic');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [showMagic, setShowMagic] = useState(false);

  function setField(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!form.name.trim()) { setError('Name is required.'); return; }

    setSaving(true);
    setError(null);
    try {
      const body = formToBody(form);
      const url = isCreate
        ? `${BASE}/provider-templates`
        : `${BASE}/provider-templates/${template.template_id}`;
      const res = isCreate ? await api.post(url, body) : await api.put(url, body);
      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail || 'Failed to save template.');
        return;
      }
      onSuccess();
    } finally {
      setSaving(false);
    }
  }

  function handleMagicApply(suggestions, mode) {
    setForm((prev) => ({
      ...prev,
      column_mappings:
        mode === 'overwrite'
          ? suggestions
          : [
              ...prev.column_mappings,
              // Merge: skip duplicates by source_column
              ...suggestions.filter(
                (s) => !prev.column_mappings.some((m) => m.source_column === s.source_column)
              ),
            ],
    }));
    setShowMagic(false);
  }

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-start justify-center p-4 pt-12 bg-black/60 overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-labelledby="edit-template-modal-title"
    >
      <div className="absolute inset-0" onClick={onClose} aria-hidden="true" />
      <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl w-full max-w-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-aegisaxiom-border">
          <h2 id="edit-template-modal-title" className="text-base font-semibold text-aegisaxiom-text">
            {isCreate ? 'New Template' : `Edit — ${template.name}`}
          </h2>
          <button onClick={onClose} className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary transition">
            <X size={16} />
          </button>
        </div>

        {/* Section tabs */}
        <div className="flex border-b border-aegisaxiom-border px-6">
          {EDIT_SECTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setActiveSection(s)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition -mb-px ${
                activeSection === s
                  ? 'border-violet-500 text-violet-300'
                  : 'border-transparent text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
              }`}
            >
              {s}
              {s === 'Column Mappings' && form.column_mappings.length > 0 && (
                <span className="ml-1.5 px-1.5 py-0.5 rounded-full text-[10px] bg-aegisaxiom-elevated border border-aegisaxiom-border text-aegisaxiom-text-muted">
                  {form.column_mappings.length}
                </span>
              )}
              {s === 'Business Rules' && form.business_rules.length > 0 && (
                <span className="ml-1.5 px-1.5 py-0.5 rounded-full text-[10px] bg-aegisaxiom-elevated border border-aegisaxiom-border text-aegisaxiom-text-muted">
                  {form.business_rules.length}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit}>
          <div className="px-6 py-5 space-y-4 min-h-[320px]">
            {error && (
              <div className="px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
                {error}
              </div>
            )}

            {/* SECTION: Basic */}
            {activeSection === 'Basic' && (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div className="col-span-2">
                    <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">
                      Name <span className="text-red-400">*</span>
                    </label>
                    <input
                      className="input-field w-full"
                      required
                      placeholder="e.g. Nextiva Standard"
                      value={form.name}
                      onChange={(e) => setField('name', e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">Default Product</label>
                    <input className="input-field w-full" placeholder="UCaaS" value={form.default_product} onChange={(e) => setField('default_product', e.target.value)} />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">Default Order Status</label>
                    <input className="input-field w-full" placeholder="PENDING" value={form.default_order_status} onChange={(e) => setField('default_order_status', e.target.value)} />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">Sales Rep Priority</label>
                    <input className="input-field w-full" placeholder="Region:East" value={form.sales_rep_priority} onChange={(e) => setField('sales_rep_priority', e.target.value)} />
                  </div>
                </div>

                <div>
                  <p className="text-xs font-medium text-aegisaxiom-text-secondary mb-2">AppDirect FK Overrides</p>
                  <div className="grid grid-cols-2 gap-3">
                    {[
                      { key: 'appdirect_advisor_id', label: 'Advisor ID' },
                      { key: 'appdirect_product_id', label: 'Product ID' },
                      { key: 'appdirect_provider_id', label: 'Provider ID' },
                      { key: 'appdirect_account_manager_id', label: 'Account Mgr ID' },
                    ].map(({ key, label }) => (
                      <div key={key}>
                        <label className="block text-xs text-aegisaxiom-text-muted mb-1">{label}</label>
                        <input
                          className="input-field w-full text-xs"
                          type="number"
                          min="0"
                          placeholder="0"
                          value={form[key]}
                          onChange={(e) => setField(key, e.target.value)}
                        />
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* SECTION: Column Mappings */}
            {activeSection === 'Column Mappings' && (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <p className="text-xs text-aegisaxiom-text-secondary">
                    Map provider CSV columns to AppDirect Contract H output paths.
                  </p>
                  <button
                    type="button"
                    onClick={() => setShowMagic((v) => !v)}
                    className="flex items-center gap-1.5 text-xs px-3 py-1 rounded-lg border border-violet-500/30 bg-violet-500/10 text-violet-300 hover:bg-violet-500/20 transition"
                  >
                    <Sparkles size={12} />
                    Magic Mapping
                  </button>
                </div>
                {showMagic && (
                  <MagicMappingPanel
                    onApply={handleMagicApply}
                    onClose={() => setShowMagic(false)}
                  />
                )}
                <ColumnMappingsEditor
                  mappings={form.column_mappings}
                  onChange={(v) => setField('column_mappings', v)}
                />
              </div>
            )}

            {/* SECTION: Business Rules */}
            {activeSection === 'Business Rules' && (
              <BusinessRulesEditor
                rules={form.business_rules}
                onChange={(v) => setField('business_rules', v)}
              />
            )}
          </div>

          {/* Footer */}
          <div className="flex gap-2 px-6 pb-5">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 px-4 py-2 rounded-lg text-sm font-medium bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text border border-aegisaxiom-border hover:border-aegisaxiom-border-hover transition"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="flex-1 px-4 py-2 rounded-lg text-sm font-medium btn-primary disabled:opacity-50 disabled:cursor-not-allowed transition"
            >
              {saving ? 'Saving…' : isCreate ? 'Create Template' : 'Save Changes'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Template Card
// ---------------------------------------------------------------------------

function TemplateCard({ template, canEdit, canDelete, onEdit, onTest, onDelete }) {
  return (
    <div className="glass-card p-5 flex flex-col gap-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-aegisaxiom-text truncate">{template.name}</h3>
          {template.default_product && (
            <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">{template.default_product}</p>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {canEdit && (
            <button
              title="Edit"
              onClick={() => onEdit(template)}
              className="p-1.5 rounded-lg text-aegisaxiom-text-muted hover:text-violet-400 hover:bg-violet-500/10 transition"
            >
              <Edit2 size={14} />
            </button>
          )}
          <button
            title="Test with CSV"
            onClick={() => onTest(template)}
            className="p-1.5 rounded-lg text-aegisaxiom-text-muted hover:text-emerald-400 hover:bg-emerald-500/10 transition text-xs font-medium"
          >
            Test
          </button>
          {canDelete && (
            <button
              title="Delete"
              onClick={() => onDelete(template)}
              className="p-1.5 rounded-lg text-aegisaxiom-text-muted hover:text-red-400 hover:bg-red-500/10 transition"
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      </div>

      <div className="flex items-center gap-3 text-xs text-aegisaxiom-text-muted">
        {template.default_order_status && (
          <span className="px-2 py-0.5 rounded bg-aegisaxiom-elevated border border-aegisaxiom-border">
            {template.default_order_status}
          </span>
        )}
        <span>{template.column_mappings?.length ?? 0} mapping{template.column_mappings?.length !== 1 ? 's' : ''}</span>
        <span>{template.business_rules?.length ?? 0} rule{template.business_rules?.length !== 1 ? 's' : ''}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ProviderTemplatesTab
// ---------------------------------------------------------------------------

export default function ProviderTemplatesTab() {
  const { hasPermission } = usePermissions();
  const canView   = hasPermission('match.provider_template.view');
  const canCreate = hasPermission('match.provider_template.create');
  const canEdit   = hasPermission('match.provider_template.edit');
  const canDelete = hasPermission('match.provider_template.delete');

  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [editTarget, setEditTarget] = useState(null);      // null | {} (new) | template object
  const [testTarget, setTestTarget] = useState(null);      // template object | null
  const [deleteTarget, setDeleteTarget] = useState(null);  // template object | null

  const loadTemplates = useCallback(async () => {
    if (!canView) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.get(`${BASE}/provider-templates`);
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || 'Failed to load templates.');
      setTemplates(unwrap(data));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [canView]);

  useEffect(() => { loadTemplates(); }, [loadTemplates]);

  async function handleDelete(template) {
    try {
      const res = await api.delete(`${BASE}/provider-templates/${template.template_id}`);
      if (!res.ok) {
        const data = await res.json();
        setError(data?.detail || 'Failed to delete template.');
        return;
      }
      setDeleteTarget(null);
      loadTemplates();
    } catch (err) {
      setError(err.message);
    }
  }

  if (!canView) {
    return (
      <div className="flex items-center justify-center h-48 text-aegisaxiom-text-secondary text-sm">
        You don't have permission to view provider templates.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-aegisaxiom-text">Provider Templates</h2>
        {canCreate && (
          <button
            className="btn-primary flex items-center gap-2 text-sm px-3 py-1.5"
            onClick={() => setEditTarget({})}
          >
            <PlusCircle size={15} />
            New Template
          </button>
        )}
      </div>

      {error && (
        <div className="px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Card grid */}
      {loading ? (
        <div className="flex items-center justify-center h-36 text-aegisaxiom-text-secondary text-sm">
          Loading templates…
        </div>
      ) : templates.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-36 gap-2 text-aegisaxiom-text-secondary text-sm glass-card">
          <span>No provider templates yet.</span>
          {canCreate && (
            <button
              className="text-violet-400 hover:underline text-sm"
              onClick={() => setEditTarget({})}
            >
              Click + New Template to create the first one.
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {templates.map((t) => (
            <TemplateCard
              key={t.template_id}
              template={t}
              canEdit={canEdit}
              canDelete={canDelete}
              onEdit={setEditTarget}
              onTest={setTestTarget}
              onDelete={setDeleteTarget}
            />
          ))}
        </div>
      )}

      {/* Edit / Create Modal */}
      {editTarget !== null && (
        <EditTemplateModal
          template={editTarget.template_id ? editTarget : null}
          onSuccess={() => { setEditTarget(null); loadTemplates(); }}
          onClose={() => setEditTarget(null)}
        />
      )}

      {/* Test Modal */}
      {testTarget && (
        <TestTemplateModal
          template={testTarget}
          onClose={() => setTestTarget(null)}
        />
      )}

      {/* Delete Confirm */}
      <ConfirmModal
        isOpen={!!deleteTarget}
        title="Delete template?"
        message={`Delete "${deleteTarget?.name}"? Sessions already created with this template will retain a reference to the old template ID, but the template record itself will be gone.`}
        confirmText="Delete"
        variant="danger"
        onConfirm={() => deleteTarget && handleDelete(deleteTarget)}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
