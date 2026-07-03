/**
 * Wave 7d — small banner shown at the top of the chat when a resumed
 * session loads, fades out after 5 seconds.
 */

import React, { useEffect, useState } from 'react';
import { Clock } from 'lucide-react';

export default function ResumeBanner({ session, onDismiss }) {
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const t = setTimeout(() => {
      setVisible(false);
      onDismiss?.();
    }, 5000);
    return () => clearTimeout(t);
  }, [onDismiss]);

  if (!visible || !session) return null;

  let dateStr = '';
  try {
    if (session.updated_at) {
      const d = new Date(session.updated_at);
      dateStr = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    }
  } catch {
    /* ignore */
  }

  return (
    <div
      style={{
        margin: '6px 12px',
        padding: '6px 10px',
        background: 'rgba(139, 92, 246, 0.10)',
        border: '1px solid rgba(139, 92, 246, 0.25)',
        borderRadius: 6,
        fontSize: 11,
        color: '#a78bfa',
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        animation: 'fade-in 0.2s ease',
      }}
    >
      <Clock size={11} />
      <span>
        Resumed{dateStr ? ` from ${dateStr}` : ''}
        {session.session_name ? ` · ${session.session_name}` : ''}
      </span>
    </div>
  );
}
