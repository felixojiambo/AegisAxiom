import React, { useMemo, useRef, useState } from 'react';
import { apiFormRequest } from '../../lib/api';
import API_URL from '../../lib/apiConfig';
import FlagPill from './FlagPill';
import { displayLabel } from '../../lib/vegaFlags';

const STYLE_ID = 'vega-v3-open-items-styles';

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .vega-oi-group {
      margin-bottom: 14px;
      background: var(--aegisaxiom-surface, #0f0f24);
      border: 1px solid var(--aegisaxiom-border, #2a2a44);
      border-radius: 8px;
      padding: 8px 12px;
    }
    .vega-oi-group .head {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 4px;
    }
    .vega-oi-group .head .label {
      font-size: 11px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--aegisaxiom-text-muted, #6e6a90);
    }
    .vega-oi-group .head .count {
      font-size: 11px;
      color: var(--aegisaxiom-text-muted, #6e6a90);
    }
    .vega-oi-item {
      display: flex;
      flex-direction: column;
      padding: 5px 0;
      border-bottom: 1px solid var(--aegisaxiom-border-soft, #1f1f3a);
      font-size: 12px;
      color: var(--aegisaxiom-text, #e6e2ff);
    }
    .vega-oi-item:last-child { border-bottom: none; }
    .vega-oi-item-row {
      display: flex;
      gap: 10px;
      align-items: flex-start;
    }
    .vega-oi-item .where {
      color: var(--aegisaxiom-text-muted, #6e6a90);
      font-size: 11px;
      margin-left: auto;
      max-width: 50%;
      overflow: hidden;
      text-overflow: ellipsis;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      line-clamp: 2;
      text-align: right;
    }
    .vega-oi-item .detail {
      color: var(--aegisaxiom-text-muted, #6e6a90);
      font-size: 11px;
      margin-top: 2px;
    }
    .vega-oi-answer-btn {
      appearance: none;
      background: transparent;
      border: 1px solid var(--aegisaxiom-border, #2a2a44);
      color: var(--aegisaxiom-text-muted, #6e6a90);
      border-radius: 5px;
      font-size: 10.5px;
      padding: 2px 8px;
      cursor: pointer;
      white-space: nowrap;
      transition: border-color 0.15s, color 0.15s;
      flex-shrink: 0;
      margin-top: 1px;
    }
    .vega-oi-answer-btn:hover {
      border-color: #8b5cf6;
      color: #a78bfa;
    }
    .vega-oi-answer-form {
      margin: 6px 0 4px;
      background: var(--aegisaxiom-elevated, #16162e);
      border: 1px solid var(--aegisaxiom-border, #2a2a44);
      border-radius: 7px;
      padding: 10px 12px;
    }
    .vega-oi-answer-form textarea {
      width: 100%;
      max-width: 300px;
      min-height: 68px;
      resize: vertical;
      background: var(--aegisaxiom-surface, #0f0f24);
      border: 1px solid var(--aegisaxiom-border, #2a2a44);
      border-radius: 5px;
      color: var(--aegisaxiom-text, #e6e2ff);
      font-size: 12px;
      padding: 6px 8px;
      font-family: inherit;
      outline: none;
      transition: border-color 0.15s;
      box-sizing: border-box;
    }
    .vega-oi-answer-form textarea:focus {
      border-color: #8b5cf6;
    }
    .vega-oi-divider {
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 8px 0;
      max-width: 300px;
      font-size: 11px;
      color: var(--aegisaxiom-text-muted, #6e6a90);
    }
    .vega-oi-divider::before,
    .vega-oi-divider::after {
      content: '';
      flex: 1;
      height: 1px;
      background: var(--aegisaxiom-border, #2a2a44);
    }
    .vega-oi-upload-btn {
      appearance: none;
      background: var(--aegisaxiom-surface, #0f0f24);
      border: 1px dashed var(--aegisaxiom-border, #2a2a44);
      border-radius: 5px;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      font-size: 11px;
      padding: 5px 10px;
      cursor: pointer;
      max-width: 300px;
      width: 100%;
      text-align: left;
      display: flex;
      align-items: center;
      gap: 6px;
      transition: border-color 0.15s, color 0.15s;
      box-sizing: border-box;
    }
    .vega-oi-upload-btn:hover:not(:disabled) {
      border-color: #8b5cf6;
      color: #a78bfa;
    }
    .vega-oi-upload-btn:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }
    .vega-oi-form-actions {
      display: flex;
      gap: 8px;
      margin-top: 10px;
    }
    .vega-oi-save-btn {
      appearance: none;
      background: #6d28d9;
      border: none;
      border-radius: 6px;
      color: #fff;
      font-size: 11px;
      font-weight: 600;
      padding: 4px 12px;
      cursor: pointer;
      transition: background 0.15s;
    }
    .vega-oi-save-btn:hover:not(:disabled) { background: #7c3aed; }
    .vega-oi-save-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .vega-oi-cancel-btn {
      appearance: none;
      background: transparent;
      border: 1px solid var(--aegisaxiom-border, #2a2a44);
      border-radius: 6px;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      font-size: 11px;
      padding: 4px 10px;
      cursor: pointer;
      transition: border-color 0.15s;
    }
    .vega-oi-cancel-btn:hover { border-color: #6e6a90; }
    .vega-oi-queued-indicator {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-top: 5px;
      font-size: 11px;
      color: #10b981;
    }
    .vega-oi-queued-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: #10b981;
      flex-shrink: 0;
    }
    .vega-oi-queued-preview {
      color: var(--aegisaxiom-text-muted, #6e6a90);
      font-size: 11px;
      max-width: 280px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .vega-oi-queue-bar {
      margin-bottom: 14px;
      background: rgba(109, 40, 217, 0.1);
      border: 1px solid rgba(109, 40, 217, 0.3);
      border-radius: 8px;
      padding: 10px 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }
    .vega-oi-queue-summary {
      font-size: 12px;
      color: #a78bfa;
    }
    .vega-oi-queue-actions {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .vega-oi-regen-btn {
      appearance: none;
      background: linear-gradient(135deg, #6d28d9, #9333ea);
      border: none;
      border-radius: 7px;
      color: #fff;
      font-size: 12px;
      font-weight: 600;
      padding: 6px 16px;
      cursor: pointer;
      transition: opacity 0.15s;
    }
    .vega-oi-regen-btn:hover:not(:disabled) { opacity: 0.88; }
    .vega-oi-regen-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .vega-oi-discard-link {
      appearance: none;
      background: transparent;
      border: none;
      color: var(--aegisaxiom-text-muted, #6e6a90);
      font-size: 11px;
      cursor: pointer;
      text-decoration: underline;
      padding: 0;
    }
    .vega-oi-discard-link:hover { color: var(--aegisaxiom-text, #e6e2ff); }
    .vega-oi-error-card {
      background: rgba(248, 113, 113, 0.08);
      border: 1px solid rgba(248, 113, 113, 0.3);
      border-radius: 8px;
      padding: 10px 14px;
      font-size: 12px;
      color: #f87171;
      margin-bottom: 14px;
    }
    .vega-oi-error-card .era-actions {
      margin-top: 8px;
      display: flex;
      gap: 8px;
    }
  `;
  document.head.appendChild(style);
}

function locationLabel(location, workflows, apps) {
  if (!location) return '—';
  if (location === 'session') return 'Session';
  if (location === 'shared_infra') return 'Shared infrastructure';
  if (location.startsWith('workflow:')) {
    const id = location.slice('workflow:'.length);
    const wf = workflows.find((w) => w.workflow_id === id);
    return wf ? `Workflow · ${wf.workflow_name}` : `Workflow · ${id}`;
  }
  if (location.startsWith('app:')) {
    const id = location.slice('app:'.length);
    const app = apps.find((a) => a.app_id === id);
    return app ? `App · ${app.app_name}` : `App · ${id}`;
  }
  return location;
}

/**
 * Inline form for answering a single open item.
 * Calls onSave({ text, uploadId, uploadFilename }) when the user saves.
 */
function AnswerForm({ initialText, initialUploadId, initialFilename, sessionId, onSave, onCancel }) {
  const [text, setText] = useState(initialText || '');
  const [uploading, setUploading] = useState(false);
  const [uploadId, setUploadId] = useState(initialUploadId || null);
  const [uploadFilename, setUploadFilename] = useState(initialFilename || null);
  const [uploadError, setUploadError] = useState(null);
  const fileInputRef = useRef(null);

  const handleUpload = async (file) => {
    setUploading(true);
    setUploadError(null);
    const formData = new FormData();
    formData.append('file', file);
    if (sessionId) formData.append('session_id', sessionId);

    try {
      const res = await apiFormRequest('POST', `${API_URL}/api/chat-uploads`, formData);
      if (res.ok) {
        const data = await res.json();
        setUploadId(data.upload_id);
        setUploadFilename(data.filename || file.name);
      } else {
        const data = await res.json().catch(() => ({}));
        setUploadError(data?.detail || 'Upload failed. Please try again.');
      }
    } catch {
      setUploadError('Connection error. Please try again.');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const canSave = text.trim().length > 0 || uploadId;

  return (
    <div className="vega-oi-answer-form" role="group" aria-label="Answer open item">
      <label htmlFor={`oi-text-${Math.random()}`} style={{ fontSize: 11, color: 'var(--aegisaxiom-text-muted, #6e6a90)', display: 'block', marginBottom: 4 }}>
        Your answer
      </label>
      <textarea
        rows={4}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Type your answer here…"
        aria-label="Answer text"
      />

      <div className="vega-oi-divider">or</div>

      {uploadId ? (
        <div style={{ fontSize: 11, color: '#10b981', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
          <span>&#10003;</span>
          <span style={{ maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {uploadFilename}
          </span>
          <button
            type="button"
            style={{ fontSize: 11, color: 'var(--aegisaxiom-text-muted, #6e6a90)', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline', padding: 0 }}
            onClick={() => {
              setUploadId(null);
              setUploadFilename(null);
            }}
          >
            Remove
          </button>
        </div>
      ) : (
        <button
          type="button"
          className="vega-oi-upload-btn"
          disabled={uploading}
          onClick={() => fileInputRef.current?.click()}
          aria-label="Upload additional transcript"
        >
          {uploading ? (
            <>
              <span
                style={{
                  width: 10, height: 10, borderRadius: '50%',
                  border: '2px solid #6e6a90', borderTopColor: '#8b5cf6',
                  animation: 'spin 0.8s linear infinite', display: 'inline-block',
                }}
                aria-hidden="true"
              />
              Uploading…
            </>
          ) : (
            <>
              <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path d="M8 1v10M4 5l4-4 4 4M2 13h12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              Upload more transcript
            </>
          )}
        </button>
      )}

      <input
        ref={fileInputRef}
        type="file"
        accept=".txt,.md,.docx,.pdf"
        className="sr-only"
        aria-label="Upload transcript file"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleUpload(file);
        }}
      />

      {uploadError && (
        <div style={{ fontSize: 11, color: '#f87171', marginTop: 4 }}>{uploadError}</div>
      )}

      <div className="vega-oi-form-actions">
        <button
          type="button"
          className="vega-oi-save-btn"
          disabled={!canSave}
          onClick={() => onSave({ text: text.trim(), uploadId, uploadFilename })}
        >
          Save
        </button>
        <button type="button" className="vega-oi-cancel-btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/**
 * Single open item row with inline answer affordance.
 */
function OpenItemRow({ item, itemIndex, locationStr, sessionId, queued, onQueue }) {
  const [open, setOpen] = useState(false);

  const handleSave = (answer) => {
    onQueue(itemIndex, answer);
    setOpen(false);
  };

  return (
    <div className="vega-oi-item">
      <div className="vega-oi-item-row">
        <div style={{ flex: 1 }}>
          <div>{item.label}</div>
          {item.detail ? <div className="detail">{item.detail}</div> : null}
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
          {locationStr && (
            <div className="where">{locationStr}</div>
          )}
          {!open && (
            <button
              type="button"
              className="vega-oi-answer-btn"
              onClick={() => setOpen(true)}
              aria-expanded={false}
              aria-label={`Answer: ${item.label}`}
            >
              {queued ? 'Edit' : 'Answer'}
            </button>
          )}
        </div>
      </div>

      {queued && !open && (
        <div className="vega-oi-queued-indicator" aria-label="Answer queued">
          <span className="vega-oi-queued-dot" aria-hidden="true" />
          <span style={{ color: '#10b981', fontWeight: 600 }}>Queued</span>
          <span className="vega-oi-queued-preview" title={queued.text || queued.uploadFilename}>
            {queued.text
              ? queued.text.length > 80
                ? queued.text.slice(0, 77) + '…'
                : queued.text
              : queued.uploadFilename || 'transcript attached'}
          </span>
        </div>
      )}

      {open && (
        <AnswerForm
          initialText={queued?.text}
          initialUploadId={queued?.uploadId}
          initialFilename={queued?.uploadFilename}
          sessionId={sessionId}
          onSave={handleSave}
          onCancel={() => setOpen(false)}
        />
      )}
    </div>
  );
}

/**
 * OpenItemsTab
 *
 * Props:
 *   openItems   {array}    — from artifact payload
 *   workflows   {array}
 *   apps        {array}
 *   artifactId  {string}
 *   sessionId   {string}   — needed for chat-upload API
 *   onRegenStart {(queue: RegenQueue) => void} — parent handles submission
 */
export default function OpenItemsTab({
  openItems,
  workflows = [],
  apps = [],
  artifactId,
  sessionId,
  onRegenStart,
}) {
  // Map of itemIndex -> { text, uploadId, uploadFilename }
  const [answers, setAnswers] = useState({});

  const handleQueue = (index, answer) => {
    setAnswers((prev) => ({ ...prev, [index]: answer }));
  };

  const grouped = useMemo(() => {
    const map = new Map();
    for (const item of openItems || []) {
      const key = item.flag || 'OTHER';
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(item);
    }
    return Array.from(map.entries());
  }, [openItems]);

  // Flat indexed list for queue references
  const flatItems = useMemo(() => openItems || [], [openItems]);

  const queuedCount = Object.keys(answers).length;
  const uploadIds = useMemo(
    () =>
      Object.values(answers)
        .map((a) => a.uploadId)
        .filter(Boolean),
    [answers],
  );
  const uploadCount = uploadIds.length;

  const handleDiscard = () => setAnswers({});

  const handleRegen = () => {
    if (!onRegenStart) return;
    const payload = {
      answers: Object.entries(answers).map(([idx, a]) => ({
        open_item_index: Number(idx),
        value: a.uploadId
          ? `[transcript: ${a.uploadFilename || a.uploadId}]${a.text ? ' ' + a.text : ''}`
          : a.text,
      })),
      new_upload_ids: uploadIds.length > 0 ? uploadIds : undefined,
    };
    onRegenStart(payload);
  };

  if (!openItems || openItems.length === 0) {
    return (
      <div style={{ fontSize: 12, color: 'var(--aegisaxiom-text-muted, #6e6a90)', fontStyle: 'italic' }}>
        No open items. Discovery is fully captured.
      </div>
    );
  }

  // Build a flat index-to-item lookup for queued row rendering.
  // Grouped rendering needs the original index for keying into `answers`.
  let globalIndex = -1;

  return (
    <div>
      {queuedCount > 0 && (
        <div className="vega-oi-queue-bar" role="region" aria-label="Queued answers">
          <span className="vega-oi-queue-summary">
            {queuedCount} answer{queuedCount !== 1 ? 's' : ''} queued
            {uploadCount > 0 ? ` · ${uploadCount} new transcript${uploadCount !== 1 ? 's' : ''} attached` : ''}
          </span>
          <div className="vega-oi-queue-actions">
            <button
              type="button"
              className="vega-oi-regen-btn"
              onClick={handleRegen}
            >
              Regenerate analysis
            </button>
            <button
              type="button"
              className="vega-oi-discard-link"
              onClick={handleDiscard}
            >
              Discard queued answers
            </button>
          </div>
        </div>
      )}

      {grouped.map(([flag, items]) => (
        <div key={flag} className="vega-oi-group">
          <div className="head">
            <FlagPill flag={flag} />
            <span className="label">{displayLabel(flag)}</span>
            <span className="count">· {items.length}</span>
          </div>
          {items.map((item) => {
            globalIndex++;
            const capturedIndex = globalIndex;
            return (
              <OpenItemRow
                key={capturedIndex}
                item={item}
                itemIndex={capturedIndex}
                locationStr={locationLabel(item.location, workflows, apps)}
                sessionId={sessionId}
                queued={answers[capturedIndex] || null}
                onQueue={handleQueue}
              />
            );
          })}
        </div>
      ))}

      {/* Accessible location labels — rendered for items inside the group */}
      <span className="sr-only">
        {flatItems.map((item, i) =>
          `${i + 1}. ${item.label} — ${locationLabel(item.location, workflows, apps)}`
        ).join('; ')}
      </span>
    </div>
  );
}
