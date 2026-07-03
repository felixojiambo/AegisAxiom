import { useEffect, useRef, useState, useCallback } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';

const MAX_BACKOFF_MS = 30_000;
const GAP_THRESHOLD_MS = 5_000;

// Feature flag — realtime SSE delivery. Set REACT_APP_ENABLE_REALTIME_SSE=true
// in the frontend build env to enable (also requires backend ENABLE_REALTIME_SSE
// + Cloud Run --timeout=3600). Default: DISABLED (fail-safe — without opt-in,
// the hook short-circuits and we avoid broken-realtime mode in any environment
// where the matching infra config hasn't been set). See backend's
// projects_events.py for the full activation runbook.
const REALTIME_ENABLED = (() => {
  const raw = (process.env.REACT_APP_ENABLE_REALTIME_SSE ?? 'false').toString().trim().toLowerCase();
  return ['true', '1', 'yes', 'on'].includes(raw);
})();

/**
 * Consumes the project SSE stream at /api/projects/{projectId}/events.
 *
 * Auth: EventSource does not support custom headers, so the connect URL
 * needs a query-param token. We use a single-use, short-TTL "ticket"
 * exchange — never the session token itself:
 *
 *   1. POST /api/projects/{projectId}/events/ticket  (cookie/Bearer auth)
 *      → { ticket, expires_in }
 *   2. EventSource(...events?ticket=<ticket>)
 *   3. Server consumes the ticket on first read; replay fails.
 *
 * Tickets are single-use, so we fetch a fresh one on every connect AND
 * every reconnect cycle.
 *
 * Self-event filtering: events from the current user are dropped (optimistic
 * updates mean the local state already reflects the change).
 *
 * Reconnect: exponential backoff starting at 1s, capped at 30s.
 * On reconnect after a gap >5s the hook calls onGap() so the parent can
 * backfill from /recent-changes.
 *
 * Auth failure: if ticket fetch returns 401/403, we surface the error
 * and stop reconnecting (no point retrying a permission failure).
 */
export function useProjectEventStream(projectId, { onEvent, onGap } = {}) {
  const { user } = useAuth();
  const currentUserId = user?.user_id ?? user?._id ?? null;

  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);
  const [lastEventId, setLastEventId] = useState(null);

  // Stable refs so callbacks stay fresh without re-triggering the effect.
  const onEventRef = useRef(onEvent);
  const onGapRef = useRef(onGap);
  const currentUserIdRef = useRef(currentUserId);
  useEffect(() => { onEventRef.current = onEvent; }, [onEvent]);
  useEffect(() => { onGapRef.current = onGap; }, [onGap]);
  useEffect(() => { currentUserIdRef.current = currentUserId; }, [currentUserId]);

  const lastEventIdRef = useRef(null);
  const disconnectedAtRef = useRef(null);
  const retryCountRef = useRef(0);
  const esRef = useRef(null);
  const timeoutRef = useRef(null);
  // Set to true when we hit a fatal auth/RBAC error and should stop
  // reconnecting. Cleared on every fresh mount of the effect.
  const stoppedRef = useRef(false);

  const cleanup = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    clearTimeout(timeoutRef.current);
  }, []);

  const fetchTicket = useCallback(async (pid) => {
    // POST goes through the normal `api` wrapper, which sends the
    // Authorization: Bearer header from localStorage. No token in the URL.
    const res = await api.post(`${API_URL}/api/projects/${pid}/events/ticket`);
    if (!res.ok) {
      const err = new Error(`ticket fetch failed: ${res.status}`);
      err.status = res.status;
      throw err;
    }
    const data = await res.json();
    if (!data?.ticket) {
      throw new Error('ticket fetch returned no ticket');
    }
    return data.ticket;
  }, []);

  const connect = useCallback(async () => {
    if (!projectId) return;
    if (stoppedRef.current) return;

    let ticket;
    try {
      ticket = await fetchTicket(projectId);
    } catch (e) {
      // 401/403 is fatal — no amount of retry will fix a missing session
      // or a revoked permission. Surface the error and stop the loop.
      if (e?.status === 401 || e?.status === 403) {
        stoppedRef.current = true;
        setConnected(false);
        setError(e.status === 401 ? 'Authentication lost' : 'Permission denied');
        return;
      }
      // Other failures (network blip, 5xx) — fall back to the standard
      // exponential backoff. Re-use the existing onerror path by
      // pretending the connect itself errored.
      setConnected(false);
      if (!disconnectedAtRef.current) {
        disconnectedAtRef.current = Date.now();
      }
      const delay = Math.min(1_000 * 2 ** retryCountRef.current, MAX_BACKOFF_MS);
      retryCountRef.current += 1;
      setError('Connection lost — reconnecting…');
      timeoutRef.current = setTimeout(() => { connect(); }, delay);
      return;
    }

    // EventSource automatically sends `Last-Event-ID` as a request header
    // only during its own native reconnect cycle. This hook closes and
    // recreates EventSource so it can fetch a fresh single-use ticket each
    // time; gap backfill is handled by disconnectedAtRef + onGap().
    const url = `${API_URL}/api/projects/${projectId}/events?ticket=${encodeURIComponent(ticket)}`;

    const es = new EventSource(url);
    esRef.current = es;

    es.onopen = () => {
      setConnected(true);
      setError(null);
      retryCountRef.current = 0;

      // Check if we missed events during the gap.
      if (disconnectedAtRef.current) {
        const gapMs = Date.now() - disconnectedAtRef.current;
        if (gapMs > GAP_THRESHOLD_MS) {
          onGapRef.current?.();
        }
        disconnectedAtRef.current = null;
      }
    };

    es.onmessage = (e) => {
      // heartbeat events have no meaningful data
      if (!e.data || e.data === 'heartbeat') return;

      let parsed;
      try {
        parsed = JSON.parse(e.data);
      } catch {
        return;
      }

      // Track last event ID for UI/diagnostics.
      if (e.lastEventId) {
        lastEventIdRef.current = e.lastEventId;
        setLastEventId(e.lastEventId);
      }

      // Drop self-originated events — optimistic update already applied.
      if (parsed.actor_user_id && parsed.actor_user_id === currentUserIdRef.current) {
        return;
      }

      onEventRef.current?.(parsed);
    };

    es.onerror = () => {
      setConnected(false);
      es.close();
      esRef.current = null;

      if (!disconnectedAtRef.current) {
        disconnectedAtRef.current = Date.now();
      }

      // Exponential backoff: 1s, 2s, 4s, 8s, 16s, 30s (max). On each
      // reconnect we fetch a FRESH ticket (tickets are single-use).
      const delay = Math.min(1_000 * 2 ** retryCountRef.current, MAX_BACKOFF_MS);
      retryCountRef.current += 1;
      setError('Connection lost — reconnecting…');

      timeoutRef.current = setTimeout(() => {
        connect();
      }, delay);
    };
  }, [projectId, fetchTicket]);

  useEffect(() => {
    if (!projectId) return;
    if (!REALTIME_ENABLED) {
      // Feature flag disabled — never open EventSource, never fetch ticket.
      // PresenceBar (separate hook) and ConflictDialog still work; only
      // cross-user live push is off. UI degrades to refresh-to-see-changes.
      return undefined;
    }
    stoppedRef.current = false;
    connect();
    return cleanup;
  }, [projectId, connect, cleanup]);

  return { connected, lastEventId, error };
}