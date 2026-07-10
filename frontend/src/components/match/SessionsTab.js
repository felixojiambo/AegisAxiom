import React, { useCallback, useEffect, useState } from 'react';
import { ExternalLink, PlusCircle, Trash2 } from 'lucide-react';
import { api, unwrap } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';
import ConfirmModal from '@/components/shared/ConfirmModal';
import UploadField from '@/components/match/sessions/UploadField';

const BASE = `${API_URL}/api/match`;

const MODE_LABELS = {
  upload: 'Upload',
  live_sql: 'Live SQL',
  live_api: 'Live API',
};

const MODE_BADGE = {
  upload: 'bg-blue-500/15 text-blue-400 border-blue-500/25',
  live_sql: 'bg-amber-500/15 text-amber-400 border-amber-500/25',
  live_api: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/25',
};

function ModeBadge({ mode }) {
  const cls = MODE_BADGE[mode] || 'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border-aegisaxiom-border';
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${cls}`}>
      {MODE_LABELS[mode] || mode}
    </span>
  );
}

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// New Session Modal
// ---------------------------------------------------------------------------

const EMPTY_UPLOADS = {
  provider_report: null,      // { url, filename, original_filename, size, kind }
  snapshot_file: null,
  snapshot_customers: null,
  snapshot_orders: null,
};

const EMPTY_FIELDS = {
  label: '',
  provider: '',
  mode: 'upload',
  template_id: '',
  sql_connection_string: '',
};

function NewSessionModal({ isOpen, templates, onSuccess, onClose }) {
  const [fields, setFields] = useState(EMPTY_FIELDS);
  const [uploads, setUploads] = useState(EMPTY_UPLOADS);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (isOpen) {
      setFields(EMPTY_FIELDS);
      setUploads(EMPTY_UPLOADS);
      setError(null);
    }
  }, [isOpen]);

  function setField(key, value) {
    setFields((prev) => ({ ...prev, [key]: value }));
  }

  function setUpload(slot, info) {
    setUploads((prev) => ({ ...prev, [slot]: info }));
    // Auto-populate provider from detected_provider in the report upload
    if (slot === 'provider_report' && info?.detected_provider) {
      setField('provider', info.detected_provider);
    }
  }

  function validate() {
    if (!fields.provider.trim()) return 'Provider is required.';
    if (!fields.template_id) return 'Template is required.';
    if (!uploads.provider_report) return 'Upload the Provider Report file first.';
    if (fields.mode === 'upload' && !uploads.snapshot_customers) {
      return 'Upload the Snapshot Customers file first (required for Upload mode).';
    }
    return null;
  }

  async function handleSubmit(e) {
    e.preventDefault();
    const msg = validate();
    if (msg) { setError(msg); return; }

    setSaving(true);
    setError(null);
    try {
      const body = {
        label: fields.label || null,
        provider: fields.provider,
        mode: fields.mode,
        template_id: fields.template_id,
        sql_connection_string: fields.sql_connection_string || null,
        provider_report_url: uploads.provider_report?.url ?? null,
        provider_report_filename: uploads.provider_report?.filename ?? null,
        snapshot_file_url: uploads.snapshot_file?.url ?? null,
        snapshot_file_filename: uploads.snapshot_file?.filename ?? null,
        snapshot_customers_url: uploads.snapshot_customers?.url ?? null,
        snapshot_customers_filename: uploads.snapshot_customers?.filename ?? null,
        snapshot_orders_url: uploads.snapshot_orders?.url ?? null,
        snapshot_orders_filename: uploads.snapshot_orders?.filename ?? null,
      };
      const res = await api.post(`${BASE}/sessions`, body);
      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail || 'Failed to create session.');
        return;
      }
      // Auto-fire the n8n match workflow. We swallow process errors here
      // (the session exists; the user can manually retry from the Sessions
      // tab) but show the detail so they know what to fix.
      try {
        const procRes = await api.post(`${BASE}/sessions/${data.session_id}/process`, {});
        if (!procRes.ok) {
          const procErr = await procRes.json().catch(() => ({}));
          setError(`Session created, but n8n trigger failed: ${procErr?.detail || procRes.status}`);
        }
      } catch (procExc) {
        setError(`Session created, but could not reach n8n: ${procExc?.message || procExc}`);
      }
      onSuccess(data.session_id);
    } finally {
      setSaving(false);
    }
  }

  if (!isOpen) return null;

  const showSql = fields.mode === 'live_sql';
  const showSnapshotFile = fields.mode === 'live_sql';
  const showSnapshotCsvs = fields.mode === 'upload';

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-start justify-center p-4 pt-16 bg-black/60 overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-labelledby="new-session-modal-title"
    >
      <div className="absolute inset-0" onClick={onClose} aria-hidden="true" />

      <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl w-full max-w-xl">
        {/* Header */}
        <div className="px-6 py-5 border-b border-aegisaxiom-border">
          <h2 id="new-session-modal-title" className="text-base font-semibold text-aegisaxiom-text">
            New Session
          </h2>
          <p className="mt-1 text-sm text-aegisaxiom-text-secondary">
            Upload your provider report and any required snapshot files, then create the session.
          </p>
        </div>

        {/* Body */}
        <form onSubmit={handleSubmit} className="px-6 py-5 space-y-4">
          {error && (
            <div className="px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
              {error}
            </div>
          )}

          {/* Label */}
          <div>
            <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">
              Label <span className="text-aegisaxiom-text-muted">(optional)</span>
            </label>
            <input
              className="input-field w-full"
              type="text"
              placeholder="e.g. Nextiva May Run"
              value={fields.label}
              onChange={(e) => setField('label', e.target.value)}
            />
          </div>

          {/* Provider */}
          <div>
            <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">
              Provider <span className="text-red-400">*</span>
            </label>
            <input
              className="input-field w-full"
              type="text"
              placeholder="e.g. Nextiva (auto-detected from report)"
              value={fields.provider}
              onChange={(e) => setField('provider', e.target.value)}
            />
          </div>

          {/* Mode */}
          <div>
            <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">
              Mode <span className="text-red-400">*</span>
            </label>
            <select
              className="input-field w-full"
              value={fields.mode}
              onChange={(e) => setField('mode', e.target.value)}
            >
              <option value="upload">Upload</option>
              <option value="live_sql">Live SQL</option>
              <option value="live_api">Live API</option>
            </select>
          </div>

          {/* Template */}
          <div>
            <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">
              Template <span className="text-red-400">*</span>
            </label>
            <select
              className="input-field w-full"
              value={fields.template_id}
              onChange={(e) => setField('template_id', e.target.value)}
              required
            >
              <option value="">— select template —</option>
              {templates.map((t) => (
                <option key={t.template_id} value={t.template_id}>
                  {t.name}
                </option>
              ))}
            </select>
            {templates.length === 0 && (
              <p className="mt-1 text-xs text-amber-400">
                No templates yet. Create one in the Provider Templates tab first.
              </p>
            )}
          </div>

          {/* SQL connection string — only for live_sql mode */}
          {showSql && (
            <div>
              <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">
                SQL Connection String
              </label>
              <input
                className="input-field w-full font-mono text-xs"
                type="text"
                placeholder="Server=...;Database=...;User Id=...;Password=..."
                value={fields.sql_connection_string}
                onChange={(e) => setField('sql_connection_string', e.target.value)}
              />
            </div>
          )}

          {/* File uploads */}
          <div className="space-y-3">
            <p className="text-xs font-medium text-aegisaxiom-text-secondary">
              File uploads
            </p>

            {/* Provider Report — required for all modes */}
            <div>
              <label className="block text-xs text-aegisaxiom-text-muted mb-1">
                Provider Report <span className="text-red-400">*</span>
              </label>
              <UploadField
                label="Provider Report"
                endpoint="/api/match/uploads/provider-report"
                required
                onChange={(info) => setUpload('provider_report', info)}
              />
            </div>

            {/* Snapshot File — optional, shown for live_sql */}
            {showSnapshotFile && (
              <div>
                <label className="block text-xs text-aegisaxiom-text-muted mb-1">
                  Snapshot File{' '}
                  <span className="text-aegisaxiom-text-muted font-normal">(optional for Live SQL)</span>
                </label>
                <UploadField
                  label="Snapshot File"
                  endpoint="/api/match/uploads/snapshot?kind=snapshot"
                  onChange={(info) => setUpload('snapshot_file', info)}
                />
              </div>
            )}

            {/* Snapshot Customers + Orders — upload mode */}
            {showSnapshotCsvs && (
              <>
                <div>
                  <label className="block text-xs text-aegisaxiom-text-muted mb-1">
                    Snapshot Customers <span className="text-red-400">*</span>
                  </label>
                  <UploadField
                    label="Snapshot Customers"
                    endpoint="/api/match/uploads/snapshot?kind=customers"
                    required
                    onChange={(info) => setUpload('snapshot_customers', info)}
                  />
                </div>
                <div>
                  <label className="block text-xs text-aegisaxiom-text-muted mb-1">
                    Snapshot Orders{' '}
                    <span className="text-aegisaxiom-text-muted font-normal">(recommended)</span>
                  </label>
                  <UploadField
                    label="Snapshot Orders"
                    endpoint="/api/match/uploads/snapshot?kind=orders"
                    onChange={(info) => setUpload('snapshot_orders', info)}
                  />
                </div>
              </>
            )}
          </div>

          {/* Actions */}
          <div className="flex gap-2 pt-2">
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
              {saving ? 'Creating…' : 'Create Session'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SessionsTab
// ---------------------------------------------------------------------------

export default function SessionsTab({ onNavigate }) {
  const { hasPermission } = usePermissions();
  const canView = hasPermission('match.session.view');
  const canCreate = hasPermission('match.session.create');
  const canDelete = hasPermission('match.session.delete');

  const [sessions, setSessions] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [showNewModal, setShowNewModal] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null); // session object to confirm deletion

  // Template lookup map: template_id -> name
  const templateMap = Object.fromEntries(templates.map((t) => [t.template_id, t.name]));

  const loadData = useCallback(async () => {
    if (!canView) return;
    setLoading(true);
    setError(null);
    try {
      const [sessRes, tmplRes] = await Promise.all([
        api.get(`${BASE}/sessions`),
        api.get(`${BASE}/provider-templates`),
      ]);
      const [sessData, tmplData] = await Promise.all([sessRes.json(), tmplRes.json()]);
      if (!sessRes.ok) throw new Error(sessData?.detail || 'Failed to load sessions.');
      setSessions(unwrap(sessData));
      if (tmplRes.ok) setTemplates(unwrap(tmplData));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [canView]);

  useEffect(() => { loadData(); }, [loadData]);

  function handleNewSessionSuccess(newSessionId) {
    setShowNewModal(false);
    loadData();
    // Navigate to the Board tab focused on the new session
    if (onNavigate) onNavigate('board', { session: newSessionId });
  }

  async function handleDelete(session) {
    try {
      const res = await api.delete(`${BASE}/sessions/${session.session_id}`);
      if (!res.ok) {
        const data = await res.json();
        setError(data?.detail || 'Failed to delete session.');
        return;
      }
      setDeleteTarget(null);
      loadData();
    } catch (err) {
      setError(err.message);
    }
  }

  if (!canView) {
    return (
      <div className="flex items-center justify-center h-48 text-aegisaxiom-text-secondary text-sm">
        You don't have permission to view sessions.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-aegisaxiom-text">Sessions</h2>
        {canCreate && (
          <button
            className="btn-primary flex items-center gap-2 text-sm px-3 py-1.5"
            onClick={() => setShowNewModal(true)}
          >
            <PlusCircle size={15} />
            New Session
          </button>
        )}
      </div>

      {error && (
        <div className="px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Table */}
      <div className="glass-card overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-36 text-aegisaxiom-text-secondary text-sm">
            Loading sessions…
          </div>
        ) : sessions.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-36 gap-2 text-aegisaxiom-text-secondary text-sm">
            <span>No sessions yet.</span>
            {canCreate && (
              <button
                className="text-violet-400 hover:underline text-sm"
                onClick={() => setShowNewModal(true)}
              >
                Click + New Session to start.
              </button>
            )}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-aegisaxiom-text">
              <thead>
                <tr className="border-b border-aegisaxiom-border text-xs text-aegisaxiom-text-secondary uppercase tracking-wide">
                  <th className="px-4 py-3 text-left font-medium">Created</th>
                  <th className="px-4 py-3 text-left font-medium">Label</th>
                  <th className="px-4 py-3 text-left font-medium">Provider</th>
                  <th className="px-4 py-3 text-left font-medium">Mode</th>
                  <th className="px-4 py-3 text-left font-medium">Template</th>
                  <th className="px-4 py-3 text-left font-medium">Report File</th>
                  <th className="px-4 py-3 text-left font-medium">Rows</th>
                  <th className="px-4 py-3 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-aegisaxiom-border">
                {sessions.map((s) => (
                  <tr
                    key={s.session_id}
                    className="hover:bg-aegisaxiom-elevated transition-colors"
                  >
                    <td className="px-4 py-3 text-xs text-aegisaxiom-text-secondary whitespace-nowrap">
                      {fmtDate(s.created_at)}
                    </td>
                    <td className="px-4 py-3 font-medium max-w-[160px] truncate">
                      {s.label || <span className="text-aegisaxiom-text-muted font-mono text-xs">{s.session_id}</span>}
                    </td>
                    <td className="px-4 py-3 text-aegisaxiom-text-secondary">{s.provider}</td>
                    <td className="px-4 py-3">
                      <ModeBadge mode={s.mode} />
                    </td>
                    <td className="px-4 py-3 text-aegisaxiom-text-secondary text-xs">
                      {templateMap[s.template_id] || (
                        <span className="font-mono text-aegisaxiom-text-muted">{s.template_id || '—'}</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-aegisaxiom-text-muted max-w-[140px] truncate">
                      {s.provider_report_filename || '—'}
                    </td>
                    <td className="px-4 py-3 text-xs text-aegisaxiom-text-muted">
                      Load board to see
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          title="View on Board"
                          onClick={() => onNavigate && onNavigate('board', { session: s.session_id })}
                          className="p-1 rounded-md text-aegisaxiom-text-muted hover:text-violet-400 hover:bg-violet-500/10 transition"
                        >
                          <ExternalLink size={15} />
                        </button>
                        {canDelete && (
                          <button
                            title="Delete session"
                            onClick={() => setDeleteTarget(s)}
                            className="p-1 rounded-md text-aegisaxiom-text-muted hover:text-red-400 hover:bg-red-500/10 transition"
                          >
                            <Trash2 size={15} />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* New Session Modal */}
      <NewSessionModal
        isOpen={showNewModal}
        templates={templates}
        onSuccess={handleNewSessionSuccess}
        onClose={() => setShowNewModal(false)}
      />

      {/* Delete Confirm */}
      <ConfirmModal
        isOpen={!!deleteTarget}
        title="Delete session?"
        message={`This will also delete all rows belonging to session "${deleteTarget?.label || deleteTarget?.session_id}". This cannot be undone.`}
        confirmText="Delete"
        variant="danger"
        onConfirm={() => deleteTarget && handleDelete(deleteTarget)}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
