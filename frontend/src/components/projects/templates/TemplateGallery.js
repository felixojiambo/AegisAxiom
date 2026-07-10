import React from 'react';
import { Eye, Play, Edit2, Trash2, Clock, CheckSquare } from 'lucide-react';

const METHODOLOGY_BADGE = {
  waterfall: { label: 'Waterfall', cls: 'bg-blue-500/15 text-blue-400' },
  agile: { label: 'Agile', cls: 'bg-green-500/15 text-green-400' },
  waves_threads: { label: 'W&T', cls: 'bg-violet-500/15 text-violet-400' },
};

function MethodologyBadge({ type }) {
  const b = METHODOLOGY_BADGE[type] ?? { label: type, cls: 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted' };
  return (
    <span className={`inline-flex items-center h-5 px-2 rounded text-[10px] font-semibold ${b.cls}`}>
      {b.label}
    </span>
  );
}

function fmtLastUsed(iso) {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    const diff = Date.now() - d.getTime();
    const days = Math.floor(diff / 86400_000);
    if (days === 0) return 'Today';
    if (days === 1) return 'Yesterday';
    if (days < 30) return `${days}d ago`;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch {
    return null;
  }
}

/**
 * TemplateGallery — card grid of templates.
 *
 * Props:
 *   templates    Template[]
 *   loading      boolean
 *   onPreview    (templateId) => void
 *   onUse        (templateId) => void
 *   onEdit       (templateId) => void
 *   onDelete     (templateId) => void
 *   canEdit      boolean
 *   canDelete    boolean
 */
export default function TemplateGallery({ templates, loading, onPreview, onUse, onEdit, onDelete, canEdit, canDelete }) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4" data-testid="template-gallery-loading">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="glass-card p-5 animate-pulse">
            <div className="h-4 w-2/3 rounded bg-aegisaxiom-elevated mb-3" />
            <div className="h-3 w-full rounded bg-aegisaxiom-elevated mb-2" />
            <div className="h-3 w-4/5 rounded bg-aegisaxiom-elevated" />
          </div>
        ))}
      </div>
    );
  }

  if (templates.length === 0) {
    return (
      <div className="glass-card p-12 flex flex-col items-center text-center" data-testid="template-gallery-empty">
        <p className="text-sm font-medium text-aegisaxiom-text mb-1">No templates yet</p>
        <p className="text-xs text-aegisaxiom-text-muted max-w-[280px]">
          Create a template from scratch or save any project as a template to reuse its structure.
        </p>
      </div>
    );
  }

  return (
    <div
      className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4"
      data-testid="template-gallery"
    >
      {templates.map((t) => {
        const tid = t.template_id ?? t._id;
        const taskCount = t.task_definitions?.length ?? 0;
        const lastUsed = fmtLastUsed(t.last_used_at);
        const useCount = t.use_count ?? 0;
        const descText = t.description ?? '';
        const truncDesc = descText.length > 80 ? descText.slice(0, 80) + '…' : descText;

        return (
          <article
            key={tid}
            className="glass-card p-5 flex flex-col gap-4 hover:border-violet-500/40 transition-colors"
            data-testid={`template-card-${tid}`}
          >
            {/* Card header */}
            <div className="flex items-start gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap mb-1">
                  <h3 className="text-sm font-semibold text-aegisaxiom-text leading-snug">{t.name}</h3>
                  {t.is_system && (
                    <span className="inline-flex items-center h-4 px-1.5 rounded bg-violet-500/15 text-violet-400 text-[9px] font-semibold">
                      SYSTEM
                    </span>
                  )}
                </div>
                {truncDesc ? (
                  <p className="text-xs text-aegisaxiom-text-muted leading-relaxed" title={descText}>
                    {truncDesc}
                  </p>
                ) : null}
              </div>
            </div>

            {/* Meta strip */}
            <div className="flex items-center gap-3 flex-wrap">
              <MethodologyBadge type={t.project_type} />
              <span className="inline-flex items-center gap-1 text-[11px] text-aegisaxiom-text-muted">
                <CheckSquare size={11} />
                {taskCount} {taskCount === 1 ? 'task' : 'tasks'}
              </span>
              {(useCount > 0 || lastUsed) ? (
                <span className="inline-flex items-center gap-1 text-[11px] text-aegisaxiom-text-muted">
                  <Clock size={11} />
                  {lastUsed ?? `${useCount}×`}
                  {lastUsed && useCount > 0 ? ` · ${useCount}×` : null}
                </span>
              ) : null}
            </div>

            {/* Action bar */}
            <div className="flex items-center gap-2 pt-1 border-t border-aegisaxiom-border/60 mt-auto">
              <button
                type="button"
                onClick={() => onPreview(tid)}
                className="inline-flex items-center gap-1.5 h-8 px-3 rounded-md text-xs text-aegisaxiom-text-secondary border border-aegisaxiom-border hover:bg-aegisaxiom-elevated hover:text-aegisaxiom-text transition-colors"
                data-testid={`template-preview-${tid}`}
                aria-label={`Preview ${t.name}`}
              >
                <Eye size={12} />
                Preview
              </button>
              <button
                type="button"
                onClick={() => onUse(tid)}
                className="btn-primary inline-flex items-center gap-1.5 h-8 px-3 text-xs"
                data-testid={`template-use-${tid}`}
                aria-label={`Use ${t.name}`}
              >
                <Play size={12} />
                Use
              </button>
              <div className="ml-auto flex items-center gap-1">
                {canEdit && (
                  <button
                    type="button"
                    onClick={() => onEdit(tid)}
                    className="h-8 w-8 inline-flex items-center justify-center rounded-md text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors"
                    aria-label={`Edit ${t.name}`}
                    data-testid={`template-edit-${tid}`}
                  >
                    <Edit2 size={13} />
                  </button>
                )}
                {canDelete && !t.is_system && (
                  <button
                    type="button"
                    onClick={() => onDelete(tid)}
                    className="h-8 w-8 inline-flex items-center justify-center rounded-md text-aegisaxiom-text-muted hover:text-red-400 hover:bg-red-500/10 transition-colors"
                    aria-label={`Delete ${t.name}`}
                    data-testid={`template-delete-${tid}`}
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}
