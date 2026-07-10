import React, { useState } from 'react';
import AIEmployeeMatrixTable from './AIEmployeeMatrixTable';
import AppDesignViewer from './AppDesignViewer';

const TABS = [
  { id: 'workflows', label: 'Workflows' },
  { id: 'apps', label: 'App Designs' },
];

export default function SingleCallSummaryArtifact({ artifact }) {
  const matrix = artifact?.payload?.matrix || [];
  const apps = artifact?.payload?.emergent_app_design_documents || [];
  const wfCount = matrix.length;
  const appCount = apps.length;
  const clientName = artifact?.payload?.client_name;

  const visibleTabs = TABS.filter((t) => t.id !== 'apps' || appCount > 0);
  const [active, setActive] = useState(visibleTabs[0]?.id || 'workflows');

  return (
    <div className="my-3 rounded-xl border border-violet-500/30 bg-aegisaxiom-elevated overflow-hidden">
      <div className="px-5 py-4 border-b border-aegisaxiom-border bg-gradient-to-r from-violet-600/10 to-purple-600/10">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-aegisaxiom-text">
              Single-call discovery summary
              {clientName ? <span className="text-aegisaxiom-text-muted font-normal"> — {clientName}</span> : null}
            </h3>
            <p className="text-xs text-aegisaxiom-text-secondary mt-1">
              {wfCount} workflow{wfCount === 1 ? '' : 's'} identified
              {appCount > 0 ? ` · ${appCount} App Design${appCount === 1 ? '' : 's'}` : ''}
            </p>
          </div>
        </div>
      </div>

      {visibleTabs.length > 1 && (
        <div className="px-5 pt-3 flex gap-2 border-b border-aegisaxiom-border">
          {visibleTabs.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setActive(t.id)}
              className={`px-3 py-2 text-xs font-medium rounded-t-lg transition-colors ${
                active === t.id
                  ? 'bg-aegisaxiom-elevated text-aegisaxiom-text border-x border-t border-aegisaxiom-border -mb-px'
                  : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
              }`}
            >
              {t.label}
              {t.id === 'apps' && (
                <span className="ml-1.5 px-1.5 py-0.5 rounded-full bg-violet-500/20 text-violet-600 dark:text-violet-300 text-[10px]">
                  {appCount}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      <div className="px-5 py-4 max-h-[80vh] overflow-y-auto">
        {active === 'workflows' && <AIEmployeeMatrixTable artifact={artifact} />}
        {active === 'apps' && <AppDesignViewer apps={apps} />}
      </div>
    </div>
  );
}
