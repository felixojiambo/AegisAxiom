import React from 'react';
import { AlertTriangle, CloudUpload, X, Check } from 'lucide-react';

/**
 * ZohoConfirmDialog — shown before pushing edits to Zoho CRM.
 * Props: open, onConfirm, onCancel, changes (object of field→value)
 */
export function ZohoConfirmDialog({ open, onConfirm, onCancel, changes = {} }) {
  if (!open) return null;
  const entries = Object.entries(changes).filter(([, v]) => v !== null && v !== undefined);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      data-testid="zoho-confirm-dialog"
    >
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl w-full max-w-md p-6 mx-4">
        <div className="flex items-center gap-3 mb-4">
          <div className="p-2 rounded-lg bg-amber-50 dark:bg-amber-500/10">
            <CloudUpload className="w-5 h-5 text-amber-600 dark:text-amber-400" />
          </div>
          <h3 className="text-lg font-semibold text-aegisaxiom-text">Sync to Zoho CRM</h3>
        </div>
        <p className="text-sm text-aegisaxiom-text-secondary mb-4">
          These changes will be pushed to Zoho CRM immediately. This action cannot be undone from
          AEGISAXIOM.
        </p>
        {entries.length > 0 && (
          <div className="bg-aegisaxiom-elevated rounded-lg p-3 mb-4 space-y-1">
            {entries.map(([k, v]) => (
              <div key={k} className="flex justify-between text-xs">
                <span className="text-aegisaxiom-text-muted capitalize">{k.replace(/_/g, ' ')}</span>
                <span className="text-aegisaxiom-text font-medium">{String(v)}</span>
              </div>
            ))}
          </div>
        )}
        <div className="flex gap-3 justify-end">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition-colors"
            data-testid="zoho-confirm-cancel"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 text-sm bg-amber-600 hover:bg-amber-500 text-white rounded-lg font-medium transition-colors flex items-center gap-2"
            data-testid="zoho-confirm-ok"
          >
            <Check className="w-4 h-4" /> Confirm & Sync
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * ZohoConflictDialog — shown when the Zoho record has been modified since last sync.
 * Props: open, onKeepLocal, onKeepRemote, onCancel, localData, remoteData, fields (array of {key, label})
 */
export function ZohoConflictDialog({
  open,
  onKeepLocal,
  onKeepRemote,
  onCancel,
  localData = {},
  remoteData = {},
  fields = [],
}) {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      data-testid="zoho-conflict-dialog"
    >
      <div className="bg-aegisaxiom-surface border border-red-200 dark:border-red-800/40 rounded-xl shadow-2xl w-full max-w-lg p-6 mx-4">
        <div className="flex items-center gap-3 mb-4">
          <div className="p-2 rounded-lg bg-red-50 dark:bg-red-500/10">
            <AlertTriangle className="w-5 h-5 text-red-600 dark:text-red-400" />
          </div>
          <div>
            <h3 className="text-lg font-semibold text-aegisaxiom-text">Sync Conflict Detected</h3>
            <p className="text-xs text-red-600 dark:text-red-400">
              This record was modified in Zoho since the last sync.
            </p>
          </div>
          <button onClick={onCancel} className="ml-auto text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="grid grid-cols-2 gap-3 mb-4">
          <div className="bg-aegisaxiom-elevated rounded-lg p-3 border border-blue-800/30">
            <p className="text-xs text-blue-600 dark:text-blue-400 font-semibold mb-2 uppercase tracking-wide">
              Your Changes (AEGISAXIOM)
            </p>
            {fields.map((f) => (
              <div
                key={f.key}
                className="flex justify-between text-xs py-1 border-b border-aegisaxiom-border last:border-0"
              >
                <span className="text-aegisaxiom-text-muted">{f.label}</span>
                <span className="text-aegisaxiom-text">{String(localData[f.key] ?? '—')}</span>
              </div>
            ))}
          </div>
          <div className="bg-aegisaxiom-elevated rounded-lg p-3 border border-amber-200 dark:border-amber-800/30">
            <p className="text-xs text-amber-600 dark:text-amber-400 font-semibold mb-2 uppercase tracking-wide">
              Zoho CRM Version
            </p>
            {fields.map((f) => (
              <div
                key={f.key}
                className="flex justify-between text-xs py-1 border-b border-aegisaxiom-border last:border-0"
              >
                <span className="text-aegisaxiom-text-muted">{f.label}</span>
                <span className="text-aegisaxiom-text">{String(remoteData[f.key] ?? '—')}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="flex gap-3 justify-end">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition-colors"
            data-testid="conflict-cancel"
          >
            Cancel
          </button>
          <button
            onClick={onKeepRemote}
            className="px-4 py-2 text-sm bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 hover:bg-amber-600/40 rounded-lg font-medium transition-colors"
            data-testid="conflict-keep-remote"
          >
            Keep Zoho
          </button>
          <button
            onClick={onKeepLocal}
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium transition-colors"
            data-testid="conflict-keep-local"
          >
            Push My Changes
          </button>
        </div>
      </div>
    </div>
  );
}
