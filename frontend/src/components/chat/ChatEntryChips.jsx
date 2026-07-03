import React, { useRef, useState } from 'react';
import { api } from '@/lib/api';
import API_URL from '../../lib/apiConfig';

export default function ChatEntryChips({ artifact }) {
  const { artifact_id, payload, status } = artifact;
  const chips = payload?.chips || [];
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(status === 'submitted');
  const [selectedId, setSelectedId] = useState(null);
  const [pendingId, setPendingId] = useState(null);
  const [error, setError] = useState(null);
  // Synchronous guard against double-submit. React state updates are async,
  // so two clicks landing in the same tick both pass the `submitting` state
  // check before either setSubmitting(true) takes effect — the backend then
  // 400s the second one and the UI shows "submission failed" for what was
  // actually a successful click. The ref flips immediately so the second
  // click bails out before hitting the network.
  const submittingRef = useRef(false);

  const handleSelect = async (chipId) => {
    if (submitted || submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setPendingId(chipId);
    setError(null);
    try {
      const res = await api.post(`${API_URL}/api/ai-chat-artifacts/${artifact_id}/submit`, {
        selected_id: chipId,
      });
      if (res.ok) {
        setSelectedId(chipId);
        setSubmitted(true);
        window.dispatchEvent(new CustomEvent('artifact-submitted', { detail: { artifact_id } }));
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data?.detail || 'Submission failed. Please try again.');
        setPendingId(null);
      }
    } catch {
      setError('Connection error. Please try again.');
      setPendingId(null);
    } finally {
      setSubmitting(false);
      submittingRef.current = false;
    }
  };

  if (submitted && selectedId) {
    const picked = chips.find((c) => c.id === selectedId);
    if (picked) {
      return (
        <div className="my-3 text-xs text-aegisaxiom-text-muted italic">
          You picked: {picked.label}
        </div>
      );
    }
  }

  return (
    <div className="my-4">
      <div className="flex flex-col gap-2">
        {chips.map((chip) => {
          const isPrimary = chip.primary;
          const isPending = pendingId === chip.id && submitting;
          const base =
            'group relative w-full text-left rounded-xl border px-4 py-3 transition-all duration-150 ' +
            'disabled:cursor-default focus:outline-none focus:ring-2 focus:ring-violet-400/60 focus:ring-offset-2 focus:ring-offset-aegisaxiom-bg';
          const styles = isPrimary
            ? 'border-violet-500/70 bg-gradient-to-br from-violet-500 to-violet-600 text-white shadow-sm shadow-violet-500/20 ' +
              'hover:from-violet-600 hover:to-violet-700 hover:shadow-md hover:shadow-violet-500/30 hover:-translate-y-px ' +
              'disabled:opacity-70'
            : 'border-aegisaxiom-border bg-aegisaxiom-elevated text-aegisaxiom-text ' +
              'hover:border-violet-400/60 hover:bg-violet-50/40 dark:hover:bg-violet-500/5 hover:-translate-y-px ' +
              'disabled:opacity-50';
          const labelClass = isPrimary
            ? 'text-sm font-semibold text-white'
            : 'text-sm font-semibold text-aegisaxiom-text';
          const descClass = isPrimary
            ? 'mt-0.5 text-xs text-white/80 leading-snug'
            : 'mt-0.5 text-xs text-aegisaxiom-text-muted leading-snug';
          const arrowClass = isPrimary
            ? 'text-white/70 group-hover:text-white group-hover:translate-x-0.5'
            : 'text-aegisaxiom-text-muted group-hover:text-violet-500 group-hover:translate-x-0.5 dark:group-hover:text-violet-300';
          return (
            <button
              key={chip.id}
              type="button"
              disabled={submitting || submitted}
              onClick={() => handleSelect(chip.id)}
              className={`${base} ${styles}`}
            >
              <div className="flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <div className={labelClass}>{chip.label}</div>
                  {chip.description && (
                    <div className={descClass}>{chip.description}</div>
                  )}
                </div>
                <div className="flex-shrink-0 self-center">
                  {isPending ? (
                    <div
                      className={`w-4 h-4 border-2 rounded-full animate-spin ${
                        isPrimary
                          ? 'border-white/30 border-t-white'
                          : 'border-aegisaxiom-border-hover border-t-violet-400'
                      }`}
                    />
                  ) : (
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      viewBox="0 0 20 20"
                      fill="currentColor"
                      className={`w-4 h-4 transition-transform duration-150 ${arrowClass}`}
                    >
                      <path
                        fillRule="evenodd"
                        d="M7.21 14.77a.75.75 0 0 1 .02-1.06L11.168 10 7.23 6.29a.75.75 0 1 1 1.04-1.08l4.5 4.25a.75.75 0 0 1 0 1.08l-4.5 4.25a.75.75 0 0 1-1.06-.02Z"
                        clipRule="evenodd"
                      />
                    </svg>
                  )}
                </div>
              </div>
            </button>
          );
        })}
      </div>
      {error && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-400">
          {error}
        </div>
      )}
    </div>
  );
}
