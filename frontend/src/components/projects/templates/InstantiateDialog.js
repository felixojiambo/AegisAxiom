import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, X } from 'lucide-react';
import { toast } from 'sonner';
import { format } from 'date-fns';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import TemplatePreview from './TemplatePreview';
import DateInput from '@/components/ui/DateInput';

function todayISO() {
  return format(new Date(), 'yyyy-MM-dd');
}

/**
 * InstantiateDialog — modal for creating a project from a template.
 *
 * Props:
 *   template         object   { template_id|_id, name, name_pattern, project_type, ... }
 *   assignees        object[] assignable users list from parent
 *   currentUser      object   { user_id|_id, name }
 *   onClose          fn
 *   onCreated        fn(projectId: string)  — navigate to the new project
 */
export default function InstantiateDialog({ template, assignees, currentUser, onClose, onCreated }) {
  const templateId = template?.template_id ?? template?._id;
  const defaultName = template?.name_pattern ?? template?.name ?? '';

  const [name, setName] = useState(defaultName);
  const [startDate, setStartDate] = useState(todayISO);
  const [pmId, setPmId] = useState(currentUser?.user_id ?? currentUser?._id ?? '');
  const [description, setDescription] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [titlePrefix, setTitlePrefix] = useState('');
  const [skipIds, setSkipIds] = useState('');

  const [previewEndDate, setPreviewEndDate] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  // Debounce the start date so we don't hammer the preview endpoint on every keystroke
  const [debouncedStartDate, setDebouncedStartDate] = useState(startDate);
  const debounceRef = useRef(null);
  useEffect(() => {
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedStartDate(startDate), 400);
    return () => clearTimeout(debounceRef.current);
  }, [startDate]);

  const handleEndDate = useCallback((d) => setPreviewEndDate(d), []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!name.trim()) { setError('Project name is required.'); return; }
    if (!startDate) { setError('Start date is required.'); return; }
    setError(null);
    setSubmitting(true);
    try {
      const body = {
        name: name.trim(),
        start_date: startDate,
        pm_user_id: pmId || undefined,
        description: description.trim() || undefined,
      };
      // Per-task customizations go under `overrides` on the instantiate body;
      // the route model rejects extra top-level keys via extra="forbid".
      const overrides = {};
      if (titlePrefix.trim()) overrides.task_title_prefix = titlePrefix.trim();
      if (skipIds.trim()) {
        overrides.skip_task_ids = skipIds
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean);
      }
      if (Object.keys(overrides).length > 0) body.overrides = overrides;

      const res = await api.post(`${API_URL}/api/projects/templates/${templateId}/instantiate`, body);
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        const detail = d?.detail;
        let msg = 'Could not create project.';
        if (typeof detail === 'string') msg = detail;
        else if (Array.isArray(detail)) {
          const msgs = detail.map((e) => (typeof e === 'string' ? e : e?.msg)).filter(Boolean);
          if (msgs.length) msg = msgs.join('; ');
        } else if (detail?.message) msg = detail.message;
        throw new Error(msg);
      }
      const data = await res.json();
      const newProjectId = data.project_id ?? data._id;
      toast.success(`Project "${name.trim()}" created`);
      onCreated?.(newProjectId);
    } catch (err) {
      setError(err.message ?? 'Could not create project.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleBackdrop = (e) => {
    if (e.target === e.currentTarget) onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-4 overflow-y-auto"
      onMouseDown={handleBackdrop}
      data-testid="instantiate-dialog"
    >
      <div className="relative w-full max-w-3xl my-8 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-aegisaxiom-border">
          <div>
            <h2 className="text-base font-semibold text-aegisaxiom-text">Create project from template</h2>
            <p className="text-xs text-aegisaxiom-text-muted mt-0.5">{template?.name}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="h-8 w-8 inline-flex items-center justify-center rounded-md text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors"
            aria-label="Close"
            data-testid="instantiate-close"
          >
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="px-6 py-5 grid grid-cols-1 md:grid-cols-2 gap-5">
            {/* Left column — inputs */}
            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1" htmlFor="inst-name">
                  Project name <span className="text-red-400">*</span>
                </label>
                <input
                  id="inst-name"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoFocus
                  maxLength={120}
                  className="aegisaxiom-input w-full"
                  data-testid="instantiate-name"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1" htmlFor="inst-start">
                  Start date <span className="text-red-400">*</span>
                </label>
                <DateInput
                  id="inst-start"
                  value={startDate}
                  onChange={setStartDate}
                  data-testid="instantiate-start-date"
                  placeholder="Pick a start date"
                />
              </div>
              {previewEndDate && (
                <p className="text-xs text-aegisaxiom-text-muted -mt-2">
                  Estimated end: <span className="text-aegisaxiom-text font-medium">
                    {format(new Date(previewEndDate), 'MMM d, yyyy')}
                  </span>
                </p>
              )}
              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1" htmlFor="inst-pm">
                  Project manager
                </label>
                <select
                  id="inst-pm"
                  value={pmId}
                  onChange={(e) => setPmId(e.target.value)}
                  className="aegisaxiom-input w-full"
                  data-testid="instantiate-pm"
                >
                  <option value="">— Unassigned —</option>
                  {assignees.map((u) => {
                    const uid = u.user_id ?? u._id;
                    return (
                      <option key={uid} value={uid}>
                        {u.name ?? u.display_name ?? uid}
                        {uid === (currentUser?.user_id ?? currentUser?._id) ? ' (you)' : ''}
                      </option>
                    );
                  })}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1" htmlFor="inst-desc">
                  Description
                </label>
                <textarea
                  id="inst-desc"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={3}
                  placeholder="Optional notes about this project"
                  className="aegisaxiom-input w-full"
                  data-testid="instantiate-description"
                />
              </div>

              {/* Advanced section */}
              <div className="border border-aegisaxiom-border rounded-lg overflow-hidden">
                <button
                  type="button"
                  onClick={() => setAdvancedOpen((v) => !v)}
                  className="w-full flex items-center justify-between px-3 py-2 text-xs text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors"
                  aria-expanded={advancedOpen}
                  data-testid="instantiate-advanced-toggle"
                >
                  <span className="font-medium">Advanced options</span>
                  {advancedOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                </button>
                {advancedOpen && (
                  <div className="px-3 py-3 border-t border-aegisaxiom-border space-y-3" data-testid="instantiate-advanced">
                    <div>
                      <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1" htmlFor="inst-prefix">
                        Task title prefix
                      </label>
                      <input
                        id="inst-prefix"
                        type="text"
                        value={titlePrefix}
                        onChange={(e) => setTitlePrefix(e.target.value)}
                        placeholder="e.g. [ACME]"
                        className="aegisaxiom-input w-full h-8 text-xs"
                        data-testid="instantiate-title-prefix"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1" htmlFor="inst-skip">
                        Skip task definition IDs
                      </label>
                      <input
                        id="inst-skip"
                        type="text"
                        value={skipIds}
                        onChange={(e) => setSkipIds(e.target.value)}
                        placeholder="Comma-separated IDs to omit"
                        className="aegisaxiom-input w-full h-8 text-xs"
                        data-testid="instantiate-skip-ids"
                      />
                      <p className="mt-1 text-[11px] text-aegisaxiom-text-muted">
                        Enter task definition indices (0-based) or IDs to exclude from this instance.
                      </p>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Right column — live preview */}
            <div className="glass-card p-4 overflow-hidden min-h-[200px]">
              <p className="text-[10px] uppercase tracking-wider font-semibold text-aegisaxiom-text-muted mb-3">
                Live preview
              </p>
              <TemplatePreview
                templateId={templateId}
                startDate={debouncedStartDate}
                onEndDate={handleEndDate}
              />
            </div>
          </div>

          {/* Footer */}
          <div className="px-6 py-4 border-t border-aegisaxiom-border flex items-center justify-between gap-3">
            {error && (
              <p className="text-xs text-red-400 flex-1" data-testid="instantiate-error">
                {error}
              </p>
            )}
            <div className="flex items-center gap-2 ml-auto">
              <button
                type="button"
                onClick={onClose}
                className="btn-secondary text-sm"
                data-testid="instantiate-cancel"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting || !name.trim() || !startDate}
                className="btn-primary inline-flex items-center gap-2 text-sm disabled:opacity-50"
                data-testid="instantiate-submit"
              >
                {submitting && (
                  <div className="animate-spin w-3.5 h-3.5 rounded-full border-2 border-white border-t-transparent" />
                )}
                Create project
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
