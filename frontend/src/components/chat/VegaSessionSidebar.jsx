/**
 * Wave 7c — Vega past-sessions sidebar.
 *
 * Mounts inside EmbeddedChatTab when employee.employee_id === 'ai_vega'.
 * Lists the current user's Vega sessions, click → fires onResume(session)
 * with the full session row so the parent can rehydrate.
 *
 * Collapsible. Toggle persisted to localStorage under
 * `vega_session_sidebar_open` (default true).
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Loader2, Pencil, Plus, RefreshCw } from 'lucide-react';
import { api } from '../../lib/api';
import API_URL from '../../lib/apiConfig';

const API = API_URL;

const TOGGLE_KEY = 'vega_session_sidebar_open';
const PAGE_SIZE = 20;

function formatRelative(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    const diff = Date.now() - d.getTime();
    const day = 24 * 3600 * 1000;
    if (diff < 60_000) return 'just now';
    if (diff < 3600_000) return `${Math.floor(diff / 60_000)}m`;
    if (diff < day) return `${Math.floor(diff / 3600_000)}h`;
    if (diff < 7 * day) return `${Math.floor(diff / day)}d`;
    return d.toLocaleDateString();
  } catch {
    return iso.slice(0, 10);
  }
}

function CallModePill({ mode }) {
  if (!mode) return null;
  const map = {
    call1: { bg: 'rgba(245, 158, 11, 0.12)', color: '#f59e0b', label: 'Call 1' },
    call2: { bg: 'rgba(59, 130, 246, 0.12)', color: '#3b82f6', label: 'Call 2' },
    call3: { bg: 'rgba(16, 185, 129, 0.12)', color: '#10b981', label: 'Call 3' },
  };
  const def = map[mode];
  if (!def) return null;
  return (
    <span
      style={{
        display: 'inline-block',
        background: def.bg,
        color: def.color,
        fontSize: 9,
        fontWeight: 600,
        padding: '1px 6px',
        borderRadius: 999,
        letterSpacing: '0.04em',
      }}
    >
      {def.label}
    </span>
  );
}

export default function VegaSessionSidebar({ activeSessionId, onResume, onNewSession, refreshKey }) {
  const [open, setOpen] = useState(() => {
    try {
      const v = localStorage.getItem(TOGGLE_KEY);
      return v === null ? true : v === 'true';
    } catch {
      return true;
    }
  });
  const [sessions, setSessions] = useState([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [renaming, setRenaming] = useState(null); // session_id being renamed
  const [renameValue, setRenameValue] = useState('');
  const offsetRef = useRef(0);

  const fetchPage = useCallback(async (replace) => {
    setLoading(true);
    setError(null);
    try {
      const offset = replace ? 0 : offsetRef.current;
      const res = await api.get(
        `${API}/api/vega/my-sessions?limit=${PAGE_SIZE}&offset=${offset}`,
      );
      if (!res.ok) {
        const txt = await res.text().catch(() => '');
        throw new Error(`HTTP ${res.status}${txt ? `: ${txt.slice(0, 80)}` : ''}`);
      }
      const data = await res.json();
      setTotal(data.total || 0);
      setHasMore(Boolean(data.has_more));
      setSessions((prev) => {
        if (replace) return data.sessions || [];
        const seen = new Set(prev.map((s) => s.session_id));
        const merged = [...prev];
        for (const s of data.sessions || []) {
          if (!seen.has(s.session_id)) merged.push(s);
        }
        return merged;
      });
      offsetRef.current = offset + (data.sessions?.length || 0);
    } catch (exc) {
      setError(exc.message || 'Failed to load sessions');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPage(true);
  }, [fetchPage, refreshKey]);

  const toggle = () => {
    setOpen((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(TOGGLE_KEY, String(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  const submitRename = async (session_id) => {
    const next = (renameValue || '').trim();
    if (!next) {
      setRenaming(null);
      return;
    }
    try {
      const res = await api.post(
        `${API}/api/vega/my-sessions/${session_id}/rename`,
        { session_name: next },
      );
      if (res.ok) {
        const data = await res.json();
        setSessions((prev) =>
          prev.map((s) =>
            s.session_id === session_id
              ? { ...s, session_name: data.session_name, session_name_override: data.session_name }
              : s,
          ),
        );
      }
    } finally {
      setRenaming(null);
      setRenameValue('');
    }
  };

  if (!open) {
    return (
      <div
        style={{
          width: 32,
          borderRight: '1px solid var(--aegisaxiom-border, rgba(255,255,255,0.08))',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          paddingTop: 12,
        }}
      >
        <button
          onClick={toggle}
          aria-label="Open sessions sidebar"
          title="Show past sessions"
          style={{
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            color: 'var(--aegisaxiom-text-secondary, #9ca3af)',
            padding: 6,
          }}
        >
          <ChevronRight size={16} />
        </button>
      </div>
    );
  }

  return (
    <aside
      style={{
        width: 280,
        flexShrink: 0,
        borderRight: '1px solid var(--aegisaxiom-border, rgba(255,255,255,0.08))',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      <header
        style={{
          padding: '10px 12px',
          borderBottom: '1px solid var(--aegisaxiom-border, rgba(255,255,255,0.08))',
          display: 'flex',
          alignItems: 'center',
          gap: 6,
        }}
      >
        <div style={{ flex: 1, fontSize: 12, fontWeight: 600, color: 'var(--aegisaxiom-text, #e5e7eb)' }}>
          Your sessions {total > 0 && <span style={{ color: '#9ca3af', fontWeight: 400 }}>· {total}</span>}
        </div>
        {onNewSession && (
          <button
            onClick={onNewSession}
            title="Start a new session"
            style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: '#9ca3af', padding: 4 }}
          >
            <Plus size={14} />
          </button>
        )}
        <button
          onClick={() => fetchPage(true)}
          title="Refresh"
          disabled={loading}
          style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: '#9ca3af', padding: 4 }}
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
        <button
          onClick={toggle}
          title="Hide sidebar"
          style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: '#9ca3af', padding: 4 }}
        >
          <ChevronLeft size={14} />
        </button>
      </header>

      <div style={{ flex: 1, overflowY: 'auto', padding: '4px 0' }}>
        {error && (
          <div style={{ padding: '8px 12px', fontSize: 11, color: '#f87171' }}>{error}</div>
        )}
        {sessions.length === 0 && !loading && !error && (
          <div style={{ padding: '20px 12px', textAlign: 'center', color: '#6b7280', fontSize: 11 }}>
            No past sessions yet. Start one by uploading a transcript.
          </div>
        )}
        {sessions.map((s) => {
          const active = s.session_id === activeSessionId;
          return (
            <div
              key={s.session_id}
              role="button"
              tabIndex={0}
              onClick={() => onResume?.(s)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onResume?.(s);
                }
              }}
              style={{
                padding: '10px 12px',
                cursor: 'pointer',
                borderLeft: active ? '2px solid #8b5cf6' : '2px solid transparent',
                background: active ? 'rgba(139, 92, 246, 0.08)' : 'transparent',
                fontSize: 12,
                color: 'var(--aegisaxiom-text, #e5e7eb)',
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
              }}
              onMouseEnter={(e) => {
                if (!active) e.currentTarget.style.background = 'rgba(255, 255, 255, 0.03)';
              }}
              onMouseLeave={(e) => {
                if (!active) e.currentTarget.style.background = 'transparent';
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                {renaming === s.session_id ? (
                  <input
                    autoFocus
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onClick={(e) => e.stopPropagation()}
                    onKeyDown={(e) => {
                      e.stopPropagation();
                      if (e.key === 'Enter') submitRename(s.session_id);
                      if (e.key === 'Escape') setRenaming(null);
                    }}
                    onBlur={() => submitRename(s.session_id)}
                    style={{
                      flex: 1,
                      background: 'rgba(255,255,255,0.05)',
                      border: '1px solid rgba(255,255,255,0.15)',
                      borderRadius: 4,
                      padding: '2px 6px',
                      fontSize: 12,
                      color: 'inherit',
                    }}
                  />
                ) : (
                  <>
                    <div style={{ flex: 1, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {s.session_name}
                    </div>
                    <CallModePill mode={s.latest_artifact_call_mode} />
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setRenameValue(s.session_name);
                        setRenaming(s.session_id);
                      }}
                      title="Rename"
                      style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: '#6b7280', padding: 0 }}
                    >
                      <Pencil size={11} />
                    </button>
                  </>
                )}
              </div>
              <div style={{ fontSize: 10, color: '#9ca3af', display: 'flex', gap: 8 }}>
                <span>{formatRelative(s.updated_at)}</span>
                {s.current_version > 1 && <span>v{s.current_version}</span>}
                {s.artifact_count > 0 && <span>{s.artifact_count} artifact{s.artifact_count !== 1 ? 's' : ''}</span>}
              </div>
            </div>
          );
        })}
        {loading && sessions.length === 0 && (
          <div style={{ padding: 16, display: 'flex', justifyContent: 'center', color: '#6b7280' }}>
            <Loader2 size={16} className="animate-spin" />
          </div>
        )}
        {hasMore && !loading && (
          <button
            onClick={() => fetchPage(false)}
            style={{
              display: 'block',
              width: '100%',
              padding: '8px 12px',
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              fontSize: 11,
              color: '#9ca3af',
            }}
          >
            Load older
          </button>
        )}
      </div>
    </aside>
  );
}
