import React from 'react';
import ReactDOM from 'react-dom/client';
import '@/index.css';
import App from '@/App';
import { installGlobalHandlers } from '@/lib/errorReporting';

// Wire GCP Error Reporting global listeners before the app boots.
// installGlobalHandlers() is idempotent and skips chunk-load errors
// (those are handled by the chunk-reload guard below).
installGlobalHandlers();

// Add dark class to HTML element for Tailwind dark mode
document.documentElement.classList.add('dark');

// Recover from stale chunk references after a deploy. Webpack invalidates
// chunk hashes on each build, so a tab that was open before the deploy will
// fail to lazy-load a chunk it references. We reload once per session so the
// user gets the new bundle instead of a broken UI.
//
// Deliberate one-shot via sessionStorage: if the reload itself fails (CDN
// hasn't propagated, or a second deploy races the first reload), we don't
// loop. Cost is the user must hard-refresh manually in that rare case —
// preferable to an infinite reload loop.
window.addEventListener('error', (event) => {
  const msg = event?.message || '';
  if (/Loading chunk \d+ failed/i.test(msg) || /Loading CSS chunk \d+ failed/i.test(msg)) {
    if (!sessionStorage.getItem('aegisaxiom_chunk_reload')) {
      sessionStorage.setItem('aegisaxiom_chunk_reload', '1');
      window.location.reload();
    }
  }
});
window.addEventListener('unhandledrejection', (event) => {
  const msg = event?.reason?.message || String(event?.reason || '');
  if (/Loading chunk \d+ failed/i.test(msg) || /Loading CSS chunk \d+ failed/i.test(msg)) {
    if (!sessionStorage.getItem('aegisaxiom_chunk_reload')) {
      sessionStorage.setItem('aegisaxiom_chunk_reload', '1');
      window.location.reload();
    }
  }
});

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
