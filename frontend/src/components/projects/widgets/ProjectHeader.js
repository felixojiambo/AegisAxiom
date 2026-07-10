import React from 'react';
import { ArrowLeft } from 'lucide-react';
import StatusPill from './StatusPill';

const HEALTH_DOT = {
  green: 'bg-green-400',
  yellow: 'bg-yellow-400',
  red: 'bg-red-400',
};

export default function ProjectHeader({ project, onBack }) {
  if (!project) return null;

  return (
    <header
      className="px-6 py-4 border-b border-aegisaxiom-border flex items-center gap-3"
      data-testid="project-header"
    >
      {onBack ? (
        <button
          type="button"
          onClick={onBack}
          className="h-7 w-7 inline-flex items-center justify-center rounded-md border border-aegisaxiom-border hover:bg-aegisaxiom-elevated"
          aria-label="Back to projects"
          data-testid="project-header-back"
        >
          <ArrowLeft size={14} className="text-aegisaxiom-text-muted" />
        </button>
      ) : null}

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          {project.health ? (
            <span
              className={`h-2 w-2 rounded-full shrink-0 ${HEALTH_DOT[project.health] ?? HEALTH_DOT.green}`}
              aria-label={`Health: ${project.health}`}
              data-testid="project-health-dot"
            />
          ) : null}
          <h1
            className="text-base font-bold text-aegisaxiom-text truncate"
            data-testid="project-header-name"
          >
            {project.name}
          </h1>
          <StatusPill status={project.status} size="sm" />
        </div>
        {project.description ? (
          <p className="text-xs text-aegisaxiom-text-muted truncate mt-0.5" data-testid="project-header-desc">
            {project.description}
          </p>
        ) : null}
      </div>

      <div className="flex items-center gap-3 shrink-0 text-[11px] text-aegisaxiom-text-muted">
        {project.project_type ? (
          <span className="capitalize" data-testid="project-header-type">
            {project.project_type.replace(/_/g, ' ')}
          </span>
        ) : null}
        {project.service_line ? (
          <span className="capitalize" data-testid="project-header-service-line">
            {project.service_line}
          </span>
        ) : null}
      </div>
    </header>
  );
}
