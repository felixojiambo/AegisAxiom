import React, { useState } from 'react';
import { createPortal } from 'react-dom';
import { BookmarkPlus, X } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';

// Backend currently accepts only `scope='org'` (Literal). 'Just me' / private
// templates are reserved for v1.4 once row-level filtering lands on the
// template-list endpoint. Showing it as 'Coming soon' avoids the silent-
// store-but-ignore footgun the QA reviewer flagged.
const SCOPE_OPTS = [
  { value: 'user', label: 'Just me', disabled: true, hint: 'Coming in v1.4' },
  { value: 'org', label: 'My organization' },
];

/**
 * SaveAsTemplateButton — kebab-menu entry that opens a modal to snapshot
 * the current project as a template via POST /api/projects/projects/{id}/save-as-template.
 *
 * Props:
 *   project      object   { project_id|_id, name, ... }
 *   onSaved      fn(templateId)  optional — called on success
 *   onViewAll    fn()  optional — called when user clicks "View in Templates"
 */
export default function SaveAsTemplateButton({ project, onSaved, onViewAll }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(() => `${project?.name ?? ''} template`.trim());
  const [description, setDescription] = useState('');
  const [scope, setScope] = useState('org');
  const [includeDates, setIncludeDates] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const handleOpen = () => {
    setName(`${project?.name ?? ''} template`.trim());
    setDescription('');
    setScope('org');
    setIncludeDates(false);
    setError(null);
    setOpen(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!name.trim()) {
      setError('Name is required.');
      return;
    }

    setError(null);
    setSubmitting(true);

    const projectId = project?.project_id ?? project?._id;

    try {
      const res = await api.post(
        `${API_URL}/api/projects/projects/${projectId}/save-as-template`,
        {
          name: name.trim(),
          description: description.trim() || undefined,
          scope,
          include_dates: includeDates,
        },
      );

      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(
          typeof d?.detail === 'string'
            ? d.detail
            : d?.detail?.message ?? 'Could not save template.',
        );
      }

      const data = await res.json();
      const templateId = data.template_id ?? data._id;
      const savedName = name.trim();

      setOpen(false);

      toast.success(
        <span>
          Template &ldquo;{savedName}&rdquo; saved.{' '}
          {onViewAll && (
            <button
              type="button"
              className="underline font-medium"
              onClick={() => {
                toast.dismiss();
                onViewAll();
              }}
            >
              View in Templates
            </button>
          )}
        </span>,
        { duration: 6000 },
      );

      onSaved?.(templateId);
    } catch (err) {
      setError(err.message ?? 'Could not save template.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleBackdrop = (e) => {
    if (e.target === e.currentTarget) setOpen(false);
  };

  return (
    <>
      <button
        type="button"
        onClick={handleOpen}
        className="btn-secondary inline-flex items-center gap-1.5 text-xs shrink-0"
        data-testid="save-as-template-button"
      >
        <BookmarkPlus size={14} />
        Save as template
      </button>

      {/*
        Portal to document.body so the modal escapes the project header's
        .glass-card ancestor. backdrop-filter on that ancestor creates a
        CSS containing block, which would otherwise scope `position: fixed`
        to the card's box instead of the viewport.
      */}
      {open &&
        createPortal(
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
            onMouseDown={handleBackdrop}
            data-testid="save-as-template-modal"
          >
            <div className="w-full max-w-[480px] bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl">
              <div className="flex items-center justify-between px-5 py-4 border-b border-aegisaxiom-border">
                <h2 className="text-base font-semibold text-aegisaxiom-text">Save as template</h2>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="h-7 w-7 inline-flex items-center justify-center rounded-md text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors"
                  aria-label="Close"
                >
                  <X size={14} />
                </button>
              </div>

              <form onSubmit={handleSubmit}>
                <div className="px-5 py-4 space-y-4">
                  <div>
                    <label
                      className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1"
                      htmlFor="sat-name"
                    >
                      Template name <span className="text-red-400">*</span>
                    </label>
                    <input
                      id="sat-name"
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      autoFocus
                      maxLength={120}
                      className="aegisaxiom-input w-full"
                      data-testid="sat-name"
                    />
                  </div>

                  <div>
                    <label
                      className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1"
                      htmlFor="sat-description"
                    >
                      Description
                    </label>
                    <textarea
                      id="sat-description"
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                      rows={2}
                      placeholder="Describe when to use this template"
                      className="aegisaxiom-input w-full"
                      data-testid="sat-description"
                    />
                  </div>

                  <fieldset>
                    <legend className="block text-xs font-medium text-aegisaxiom-text-secondary mb-2">
                      Visibility
                    </legend>
                    <div className="flex gap-4">
                      {SCOPE_OPTS.map((opt) => (
                        <label
                          key={opt.value}
                          title={opt.hint ?? undefined}
                          className={`flex items-center gap-2 text-sm ${
                            opt.disabled
                              ? 'text-aegisaxiom-text-muted cursor-not-allowed'
                              : 'text-aegisaxiom-text cursor-pointer'
                          }`}
                        >
                          <input
                            type="radio"
                            name="scope"
                            value={opt.value}
                            checked={scope === opt.value}
                            disabled={opt.disabled}
                            onChange={() => setScope(opt.value)}
                            className="accent-violet-500 disabled:opacity-50"
                            data-testid={`sat-scope-${opt.value}`}
                          />
                          <span>{opt.label}</span>
                          {opt.hint && (
                            <span className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted">
                              {opt.hint}
                            </span>
                          )}
                        </label>
                      ))}
                    </div>
                  </fieldset>

                  <label className="flex items-center gap-2 text-sm text-aegisaxiom-text cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={includeDates}
                      onChange={(e) => setIncludeDates(e.target.checked)}
                      className="accent-violet-500"
                      data-testid="sat-include-dates"
                    />
                    Include date offsets
                    <span className="text-xs text-aegisaxiom-text-muted">
                      (preserves relative schedule)
                    </span>
                  </label>

                  {error && (
                    <p className="text-xs text-red-400" data-testid="sat-error">
                      {error}
                    </p>
                  )}
                </div>

                <div className="px-5 py-3 border-t border-aegisaxiom-border flex items-center justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setOpen(false)}
                    className="btn-secondary text-sm"
                    data-testid="sat-cancel"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={submitting || !name.trim()}
                    className="btn-primary inline-flex items-center gap-2 text-sm disabled:opacity-50"
                    data-testid="sat-submit"
                  >
                    {submitting && (
                      <div className="animate-spin w-3.5 h-3.5 rounded-full border-2 border-white border-t-transparent" />
                    )}
                    Save template
                  </button>
                </div>
              </form>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}