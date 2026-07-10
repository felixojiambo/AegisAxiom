import React, { useState } from 'react';
import { CheckCircle } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '../../lib/apiConfig';

export default function SingleSelectArtifact({ artifact }) {
  const { artifact_id, payload, status } = artifact;
  const { question, title, selected_id: initialSelected } = payload;
  // Normalize options: LLM may send [{id, label}], [{name}], or plain strings
  const rawOptions = payload.options || [];
  const options = rawOptions.map((opt) => {
    if (typeof opt === 'string') return { id: opt, label: opt };
    const label = opt.label || opt.name || opt.id || opt.text || '';
    return { id: opt.id || label, label };
  });

  const alreadySubmitted = status === 'submitted' || status === 'graded' || !!initialSelected;

  const [selectedId, setSelectedId] = useState(initialSelected || null);
  const [submitted, setSubmitted] = useState(alreadySubmitted);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const handleSelect = async (optionId) => {
    if (submitted || submitting) return;

    setSubmitting(true);
    setError(null);

    try {
      const res = await api.post(`${API_URL}/api/ai-chat-artifacts/${artifact_id}/submit`, {
        selected_id: optionId,
      });
      if (res.ok) {
        setSelectedId(optionId);
        setSubmitted(true);
        // Notify parent to refresh messages — Vega's next turn was auto-generated server-side
        window.dispatchEvent(new CustomEvent('artifact-submitted', { detail: { artifact_id } }));
      } else {
        const data = await res.json();
        setError(data?.detail || 'Submission failed. Please try again.');
      }
    } catch {
      setError('Connection error. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="my-3 rounded-xl border border-violet-500/30 bg-aegisaxiom-elevated overflow-hidden">
      <div className="px-5 py-4 border-b border-aegisaxiom-border">
        <p className="text-sm font-semibold text-aegisaxiom-text leading-snug">{question || title}</p>
      </div>

      <div className="px-5 py-4 space-y-2.5">
        {options.map((opt) => {
          const isSelected = selectedId === opt.id;
          const isReadOnly = submitted;

          let stateClasses;
          if (isSelected) {
            // Submitted/selected — strong, on-brand, readable in both themes
            stateClasses =
              'border-violet-500 dark:border-violet-500/60 bg-violet-50 dark:bg-violet-500/20 text-violet-700 dark:text-violet-200 ring-1 ring-violet-200 dark:ring-violet-500/30';
          } else if (isReadOnly) {
            // Non-selected after submit — dimmed but still legible
            stateClasses =
              'border-aegisaxiom-border bg-aegisaxiom-accent-soft text-aegisaxiom-text-secondary opacity-60 cursor-default';
          } else {
            // Interactive — full contrast, proper hover text for both themes
            stateClasses =
              'border-aegisaxiom-border bg-aegisaxiom-accent-soft text-aegisaxiom-text hover:border-violet-500/50 hover:bg-violet-100 dark:hover:bg-violet-500/10 hover:text-violet-700 dark:hover:text-violet-200 cursor-pointer';
          }

          return (
            <button
              key={opt.id}
              type="button"
              onClick={() => handleSelect(opt.id)}
              disabled={isReadOnly || submitting}
              aria-pressed={isSelected}
              className={`w-full flex items-center justify-between gap-3 px-4 py-3 rounded-xl border text-sm text-left transition-all disabled:cursor-default ${stateClasses}`}
            >
              <span className="leading-snug">{opt.label}</span>
              {isSelected && (
                <CheckCircle size={16} className="text-violet-600 dark:text-violet-300 shrink-0" />
              )}
            </button>
          );
        })}
      </div>

      {error && <div className="px-5 pb-4 text-xs text-red-600 dark:text-red-400">{error}</div>}

      {submitting && (
        <div className="px-5 pb-4 flex items-center gap-2 text-xs text-aegisaxiom-text-muted">
          <div className="w-3 h-3 border-2 border-aegisaxiom-border-hover border-t-violet-400 rounded-full animate-spin" />
          Saving…
        </div>
      )}
    </div>
  );
}
