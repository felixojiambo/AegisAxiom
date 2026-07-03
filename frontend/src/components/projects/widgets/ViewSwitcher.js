import React from 'react';

// Views are provided by the parent based on project_type.
// waterfall: [{ id: 'phases', label: 'Phases' }, { id: 'gantt', label: 'Gantt' }, { id: 'calendar', label: 'Calendar' }]
// agile:     [{ id: 'board', label: 'Board' }, { id: 'backlog', label: 'Backlog' }]
// waves_threads: [{ id: 'timeline', label: 'Timeline' }, { id: 'threads', label: 'Threads' }, ...]

export default function ViewSwitcher({ views, activeView, onChange }) {
  return (
    <nav
      className="flex items-center gap-1 px-6 py-2 border-b border-aegisaxiom-border"
      aria-label="View switcher"
      data-testid="view-switcher"
    >
      {views.map((v) => {
        const active = v.id === activeView;
        return (
          <button
            key={v.id}
            type="button"
            onClick={() => onChange(v.id)}
            data-testid={`view-tab-${v.id}`}
            aria-current={active ? 'page' : undefined}
            className={`h-7 px-3 rounded-md text-xs font-medium transition-colors ${
              active
                ? 'bg-aegisaxiom-elevated text-aegisaxiom-text border border-aegisaxiom-border'
                : 'text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated/50'
            }`}
          >
            {v.label}
          </button>
        );
      })}
    </nav>
  );
}
