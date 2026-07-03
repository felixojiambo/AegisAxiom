import React from 'react';
import AppBlock from './AppBlock';

export default function StandaloneAppsTab({ apps, artifactId }) {
  if (!apps || apps.length === 0) {
    return (
      <div style={{ fontSize: 12, color: 'var(--aegisaxiom-text-muted, #6e6a90)', fontStyle: 'italic' }}>
        No standalone apps. All discovered apps are linked to at least one workflow.
      </div>
    );
  }
  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--aegisaxiom-text-muted, #6e6a90)', marginBottom: 8 }}>
        Apps discovered without an explicit workflow link.
      </div>
      {apps.map((app) => <AppBlock key={app.app_id} app={app} artifactId={artifactId} />)}
    </div>
  );
}
