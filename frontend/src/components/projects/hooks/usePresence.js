import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';

const HEARTBEAT_INTERVAL_MS = 30_000;

/**
 * Sends a presence heartbeat every 30s for the given project/viewport.
 * Pauses when the tab is backgrounded (document.visibilityState !== 'visible').
 * Returns the `viewing` array from the last successful heartbeat.
 */
export function usePresence(projectId, viewport) {
  const [viewing, setViewing] = useState([]);
  const timerRef = useRef(null);
  const viewportRef = useRef(viewport);

  useEffect(() => { viewportRef.current = viewport; }, [viewport]);

  useEffect(() => {
    if (!projectId) return;

    const beat = async () => {
      if (document.visibilityState !== 'visible') return;
      try {
        const res = await api.post(
          `${API_URL}/api/projects/${projectId}/presence/heartbeat`,
          viewportRef.current ? { viewport: viewportRef.current } : {},
        );
        if (res.ok) {
          const data = await res.json();
          setViewing(data?.viewing ?? []);
        }
      } catch {
        // Non-fatal — presence is best-effort.
      }
    };

    // Fire immediately on mount (don't wait the full 30s for first data).
    beat();
    timerRef.current = setInterval(beat, HEARTBEAT_INTERVAL_MS);

    const onVisibility = () => {
      if (document.visibilityState === 'visible') beat();
    };
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      clearInterval(timerRef.current);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [projectId]);

  return { viewing };
}
