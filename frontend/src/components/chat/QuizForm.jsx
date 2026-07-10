import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  CheckCircle,
  XCircle,
  Clock,
  Send,
  GripVertical,
  AlertTriangle,
  Trophy,
  BookOpen,
  ChevronDown,
} from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '../../lib/apiConfig';

function formatTime(seconds) {
  const m = Math.floor(seconds / 60)
    .toString()
    .padStart(2, '0');
  const s = (seconds % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
}

const isTouchDevice = () => typeof window !== 'undefined' && 'ontouchstart' in window;

function MultipleChoice({ question, value, onChange, disabled }) {
  return (
    <fieldset className="space-y-2.5">
      <legend className="sr-only">{question.prompt}</legend>
      {question.options.map((opt) => {
        const checked = value === opt.value;
        return (
          <label
            key={opt.value}
            className={`flex items-center gap-3 px-4 py-3 rounded-xl border cursor-pointer transition-all select-none ${
              disabled
                ? 'cursor-default'
                : 'hover:border-violet-500/50 hover:bg-violet-100 dark:hover:bg-violet-500/10'
            } ${
              checked
                ? 'border-violet-500 dark:border-violet-500/60 bg-violet-50 dark:bg-violet-500/15 text-violet-700 dark:text-violet-200 ring-1 ring-violet-200 dark:ring-violet-500/30'
                : disabled
                  ? 'border-aegisaxiom-border bg-aegisaxiom-accent-soft text-aegisaxiom-text-secondary opacity-60'
                  : 'border-aegisaxiom-border bg-aegisaxiom-accent-soft text-aegisaxiom-text hover:text-violet-700 dark:hover:text-violet-200'
            }`}
          >
            <input
              type="radio"
              name={question.id}
              value={opt.value}
              checked={checked}
              onChange={() => !disabled && onChange(opt.value)}
              disabled={disabled}
              className="sr-only"
            />
            <span
              className={`w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors ${
                checked ? 'border-violet-400 bg-violet-400' : 'border-aegisaxiom-border-hover'
              }`}
            >
              {checked && <span className="w-1.5 h-1.5 rounded-full bg-white" />}
            </span>
            <span className="text-sm leading-snug">{opt.label}</span>
          </label>
        );
      })}
    </fieldset>
  );
}

function MultiSelect({ question, value = [], onChange, disabled }) {
  const toggle = (optValue) => {
    if (disabled) return;
    const next = value.includes(optValue)
      ? value.filter((v) => v !== optValue)
      : [...value, optValue];
    onChange(next);
  };

  return (
    <fieldset className="space-y-2.5">
      <legend className="sr-only">{question.prompt}</legend>
      <p className="text-xs text-aegisaxiom-text-muted mb-1">Select all that apply</p>
      {question.options.map((opt) => {
        const checked = value.includes(opt.value);
        return (
          <label
            key={opt.value}
            className={`flex items-center gap-3 px-4 py-3 rounded-xl border cursor-pointer transition-all select-none ${
              disabled
                ? 'cursor-default'
                : 'hover:border-violet-500/50 hover:bg-violet-100 dark:hover:bg-violet-500/10'
            } ${
              checked
                ? 'border-violet-500 dark:border-violet-500/60 bg-violet-50 dark:bg-violet-500/15 text-violet-700 dark:text-violet-200 ring-1 ring-violet-200 dark:ring-violet-500/30'
                : disabled
                  ? 'border-aegisaxiom-border bg-aegisaxiom-accent-soft text-aegisaxiom-text-secondary opacity-60'
                  : 'border-aegisaxiom-border bg-aegisaxiom-accent-soft text-aegisaxiom-text hover:text-violet-700 dark:hover:text-violet-200'
            }`}
          >
            <input
              type="checkbox"
              value={opt.value}
              checked={checked}
              onChange={() => toggle(opt.value)}
              disabled={disabled}
              className="sr-only"
            />
            <span
              className={`w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 transition-colors ${
                checked ? 'border-violet-400 bg-violet-400' : 'border-aegisaxiom-border-hover'
              }`}
            >
              {checked && (
                <svg
                  viewBox="0 0 10 8"
                  className="w-2.5 h-2.5 text-aegisaxiom-text"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M1 4l3 3 5-6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
            </span>
            <span className="text-sm leading-snug">{opt.label}</span>
          </label>
        );
      })}
    </fieldset>
  );
}

function TrueFalse({ question, value, onChange, disabled }) {
  const options = question.options || [
    { value: 'true', label: 'True' },
    { value: 'false', label: 'False' },
  ];
  return (
    <div className="flex gap-3">
      {options.map((opt) => {
        const checked = value === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => !disabled && onChange(opt.value)}
            disabled={disabled}
            className={`flex-1 py-3 rounded-xl border text-sm font-medium transition-all ${
              checked
                ? 'border-violet-500/60 bg-violet-50 dark:bg-violet-500/10 text-violet-700 dark:text-violet-300'
                : 'border-aegisaxiom-border bg-aegisaxiom-accent-soft text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover hover:text-aegisaxiom-text'
            } ${disabled ? 'cursor-default' : 'cursor-pointer'}`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

function FreeText({ value = '', onChange, disabled }) {
  return (
    <textarea
      rows={4}
      value={value}
      onChange={(e) => !disabled && onChange(e.target.value)}
      disabled={disabled}
      placeholder="Type your answer here..."
      className="w-full bg-aegisaxiom-surface border border-aegisaxiom-border focus:border-violet-500 rounded-xl px-4 py-3 text-sm text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted outline-none transition-colors resize-none disabled:opacity-60 disabled:cursor-default"
    />
  );
}

function ScenarioMatch({ question, value = {}, onChange, disabled }) {
  const touch = isTouchDevice();
  const dragItemRef = useRef(null);
  const [dragOver, setDragOver] = useState(null);

  const handleDragStart = (e, itemId) => {
    dragItemRef.current = itemId;
    e.dataTransfer.effectAllowed = 'move';
  };

  const handleDragOver = (e, catValue) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragOver(catValue);
  };

  const handleDrop = (e, catValue) => {
    e.preventDefault();
    setDragOver(null);
    if (!dragItemRef.current || disabled) return;
    onChange({ ...value, [dragItemRef.current]: catValue });
    dragItemRef.current = null;
  };

  const handleDragLeave = () => setDragOver(null);

  if (touch) {
    return (
      <div className="space-y-3">
        {question.items.map((item) => (
          <div key={item.id} className="flex items-center gap-3">
            <span className="flex-1 text-sm text-aegisaxiom-text-secondary bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-xl px-4 py-2.5">
              {item.label}
            </span>
            <div className="relative shrink-0 w-40">
              <select
                value={value[item.id] || ''}
                onChange={(e) => onChange({ ...value, [item.id]: e.target.value })}
                disabled={disabled}
                className="w-full appearance-none bg-aegisaxiom-surface border border-aegisaxiom-border focus:border-violet-500 rounded-xl px-3 py-2.5 pr-8 text-sm text-aegisaxiom-text outline-none transition-colors disabled:opacity-60 disabled:cursor-default"
              >
                <option value="">-- Select --</option>
                {question.categories.map((cat) => (
                  <option key={cat.value} value={cat.value}>
                    {cat.label}
                  </option>
                ))}
              </select>
              <ChevronDown
                size={14}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted pointer-events-none"
              />
            </div>
          </div>
        ))}
      </div>
    );
  }

  const assignedItems = (catValue) => question.items.filter((item) => value[item.id] === catValue);
  const unassignedItems = question.items.filter((item) => !value[item.id]);

  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs text-aegisaxiom-text-muted mb-2 font-medium uppercase tracking-wide">
          Items — drag to a category
        </p>
        <div className="flex flex-wrap gap-2 min-h-[48px] p-3 rounded-xl border border-dashed border-aegisaxiom-border bg-aegisaxiom-accent-muted">
          {unassignedItems.length === 0 && (
            <span className="text-xs text-aegisaxiom-text-muted self-center">All items placed</span>
          )}
          {unassignedItems.map((item) => (
            <div
              key={item.id}
              draggable={!disabled}
              onDragStart={(e) => handleDragStart(e, item.id)}
              className={`flex items-center gap-1.5 px-3 py-2 rounded-lg border text-sm text-aegisaxiom-text-secondary bg-aegisaxiom-elevated border-aegisaxiom-border select-none transition-all ${
                disabled
                  ? 'cursor-default opacity-70'
                  : 'cursor-grab active:cursor-grabbing hover:border-violet-500/50 hover:bg-violet-500/5'
              }`}
            >
              <GripVertical size={13} className="text-aegisaxiom-text-muted shrink-0" />
              {item.label}
            </div>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        {question.categories.map((cat) => {
          const placed = assignedItems(cat.value);
          const isOver = dragOver === cat.value;
          return (
            <div
              key={cat.value}
              onDragOver={(e) => handleDragOver(e, cat.value)}
              onDrop={(e) => handleDrop(e, cat.value)}
              onDragLeave={handleDragLeave}
              className={`min-h-[80px] rounded-xl border p-3 transition-all ${
                isOver
                  ? 'border-violet-500/60 bg-violet-50 dark:bg-violet-500/10'
                  : 'border-aegisaxiom-border bg-aegisaxiom-accent-soft'
              }`}
            >
              <p className="text-xs font-semibold text-violet-600 dark:text-violet-400 mb-2">
                {cat.label}
              </p>
              <div className="space-y-1.5">
                {placed.map((item) => (
                  <div
                    key={item.id}
                    draggable={!disabled}
                    onDragStart={(e) => handleDragStart(e, item.id)}
                    className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs text-aegisaxiom-text-secondary bg-aegisaxiom-elevated border-aegisaxiom-border-hover/60 select-none ${
                      disabled ? 'cursor-default' : 'cursor-grab active:cursor-grabbing'
                    }`}
                  >
                    <GripVertical size={11} className="text-aegisaxiom-text-muted shrink-0" />
                    {item.label}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function QuestionResult({ feedback }) {
  if (!feedback) return null;
  const { correct, explanation } = feedback;

  return (
    <div
      className={`flex items-start gap-2 mt-3 p-3 rounded-xl border text-sm ${
        correct
          ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-600 dark:text-emerald-400'
          : 'border-red-500/30 bg-red-500/5 text-red-600 dark:text-red-400'
      }`}
    >
      {correct ? (
        <CheckCircle size={15} className="shrink-0 mt-0.5" />
      ) : (
        <XCircle size={15} className="shrink-0 mt-0.5" />
      )}
      <div>
        <p className="font-medium">{correct ? 'Correct' : 'Incorrect'}</p>
        {!correct && explanation && (
          <p className="text-aegisaxiom-text-secondary mt-1 text-xs leading-relaxed">{explanation}</p>
        )}
      </div>
    </div>
  );
}

function Timer({ initialSeconds, onExpire }) {
  const [remaining, setRemaining] = useState(initialSeconds);
  const onExpireRef = useRef(onExpire);
  onExpireRef.current = onExpire;

  useEffect(() => {
    // Single stable interval; functional updater avoids depending on `remaining`
    const id = setInterval(() => {
      setRemaining((s) => {
        if (s <= 1) {
          clearInterval(id);
          onExpireRef.current?.();
          return 0;
        }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(id);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const color =
    remaining <= 30
      ? 'text-red-600 dark:text-red-400'
      : remaining <= 120
        ? 'text-yellow-600 dark:text-yellow-400'
        : 'text-aegisaxiom-text-secondary';

  return (
    <span
      className={`flex items-center gap-1.5 font-mono text-sm font-medium tabular-nums ${color}`}
    >
      <Clock size={14} />
      {formatTime(remaining)}
    </span>
  );
}

export default function QuizForm({ artifact, onSubmitted }) {
  const { payload, status, ai_feedback } = artifact;
  const {
    title,
    description,
    instructions,
    passing_score,
    time_limit_seconds,
    questions = [],
  } = payload;

  const isGraded = status === 'graded';
  const isPending = status === 'pending';
  const isSubmitted = status === 'submitted' || status === 'grading';

  const [answers, setAnswers] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [timedOut, setTimedOut] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [locked, setLocked] = useState(false); // Locked = can't re-open (retry in progress)

  // When a new pending quiz appears, collapse and lock old graded quizzes
  useEffect(() => {
    if (isPending) {
      window.dispatchEvent(
        new CustomEvent('arc-new-quiz', { detail: { artifactId: artifact.artifact_id } }),
      );
    }
  }, [isPending, artifact.artifact_id]);

  useEffect(() => {
    if (!isGraded) return;
    const handler = (e) => {
      if (e.detail?.artifactId !== artifact.artifact_id) {
        setCollapsed(true);
        setLocked(true); // Prevent re-opening — answers are hidden during retry
      }
    };
    window.addEventListener('arc-new-quiz', handler);
    return () => window.removeEventListener('arc-new-quiz', handler);
  }, [isGraded, artifact.artifact_id]);

  // Ref so handleTimerExpire can read the latest answers without becoming stale
  const answersRef = useRef(answers);
  answersRef.current = answers;

  const setAnswer = useCallback((qId, val) => {
    setAnswers((prev) => ({ ...prev, [qId]: val }));
  }, []);

  const submittingRef = useRef(false);

  const handleSubmit = useCallback(
    async (e) => {
      e?.preventDefault();
      if (submittingRef.current) return;
      submittingRef.current = true;
      setSubmitting(true);
      setSubmitError(null);

      const body = {
        answers: questions.map((q) => ({
          question_id: q.id,
          answer: answersRef.current[q.id] ?? null,
        })),
      };

      try {
        const res = await api.post(
          `${API_URL}/api/ai-chat-artifacts/${artifact.artifact_id}/submit`,
          body,
        );
        if (res.ok) {
          onSubmitted?.();
        } else {
          const data = await res.json();
          setSubmitError(data?.detail || 'Submission failed. Please try again.');
        }
      } catch {
        setSubmitError('Connection error. Please try again.');
      } finally {
        submittingRef.current = false;
        setSubmitting(false);
      }
    },
    [questions, artifact.artifact_id, onSubmitted],
  );

  const handleTimerExpire = useCallback(() => {
    setTimedOut(true);
    handleSubmit();
  }, [handleSubmit]);

  const feedbackMap = useMemo(() => {
    if (!isGraded || !ai_feedback?.feedback) return {};
    return Object.fromEntries(ai_feedback.feedback.map((fb) => [fb.question_id, fb]));
  }, [isGraded, ai_feedback]);

  const totalPoints = useMemo(
    () => questions.reduce((sum, q) => sum + (q.points || 0), 0),
    [questions],
  );

  // ai_feedback.score is already a percentage (0-100), ai_feedback.earned is raw points
  const scorePercent = isGraded ? (ai_feedback?.score ?? null) : null;
  const earnedPoints = isGraded ? (ai_feedback?.earned ?? 0) : 0;
  const passed = scorePercent !== null && scorePercent >= passing_score;

  const disabled = isGraded || isSubmitted || submitting;

  // Collapsed graded quiz — show only score summary
  if (isGraded && collapsed) {
    return (
      <div
        className={`my-4 rounded-2xl border border-aegisaxiom-border bg-aegisaxiom-surface overflow-hidden transition-colors ${locked ? 'opacity-60' : 'cursor-pointer hover:border-aegisaxiom-border-hover/50'}`}
        onClick={locked ? undefined : () => setCollapsed(false)}
      >
        <div className="px-6 py-3 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <Trophy
              size={16}
              className={
                passed ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'
              }
            />
            <span className="text-sm text-aegisaxiom-text-secondary truncate">{title}</span>
            <span
              className={`text-sm font-bold ${passed ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}
            >
              {ai_feedback?.score ?? 0}%
            </span>
            <span
              className={`text-xs font-medium ${passed ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}
            >
              {passed ? 'Passed' : 'Failed'}
            </span>
          </div>
          {!locked && <ChevronDown size={16} className="text-aegisaxiom-text-muted shrink-0" />}
        </div>
      </div>
    );
  }

  return (
    <div className="my-4 rounded-2xl border border-aegisaxiom-border bg-aegisaxiom-surface overflow-hidden">
      <div
        className={`px-6 py-4 border-b border-aegisaxiom-border flex items-start justify-between gap-4 ${isGraded ? 'cursor-pointer hover:bg-aegisaxiom-elevated' : ''}`}
        onClick={isGraded ? () => setCollapsed(true) : undefined}
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-violet-600/30 to-fuchsia-600/30 border border-violet-500/20 flex items-center justify-center shrink-0">
            <BookOpen size={16} className="text-violet-600 dark:text-violet-400" />
          </div>
          <div className="min-w-0">
            <h2
              className="text-sm font-semibold text-aegisaxiom-text truncate"
              style={{ fontFamily: 'Manrope, sans-serif' }}
            >
              {title}
            </h2>
            <p className="text-xs text-aegisaxiom-text-muted mt-0.5">
              {questions.length} question{questions.length !== 1 ? 's' : ''} &middot; {totalPoints}{' '}
              pts &middot; Pass at {passing_score}%
            </p>
          </div>
        </div>
        {isGraded && (
          <ChevronDown size={16} className="text-aegisaxiom-text-muted shrink-0 mt-1 rotate-180" />
        )}
        {isSubmitted && (
          <span className="flex items-center gap-1.5 text-xs text-yellow-600 dark:text-yellow-400 font-medium shrink-0">
            <Clock size={13} />
            Grading…
          </span>
        )}
      </div>

      {timedOut && !isGraded && (
        <div className="px-6 py-3 bg-red-50 dark:bg-red-500/10 border-b border-red-500/20 flex items-center gap-2 text-red-600 dark:text-red-400 text-xs">
          <AlertTriangle size={13} />
          Time expired — your answers were submitted automatically.
        </div>
      )}

      {isGraded && scorePercent !== null && (
        <div
          className={`px-6 py-4 border-b flex items-center gap-4 ${
            passed
              ? 'bg-emerald-50 dark:bg-emerald-500/10 border-emerald-500/20'
              : 'bg-red-50 dark:bg-red-500/10 border-red-500/20'
          }`}
        >
          <div
            className={`w-12 h-12 rounded-2xl flex items-center justify-center shrink-0 ${
              passed ? 'bg-emerald-50 dark:bg-emerald-500/20' : 'bg-red-50 dark:bg-red-500/20'
            }`}
          >
            <Trophy
              size={22}
              className={
                passed ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'
              }
            />
          </div>
          <div>
            <p
              className={`text-2xl font-bold ${passed ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}
              style={{ fontFamily: 'Manrope, sans-serif' }}
            >
              {scorePercent}%
            </p>
            <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">
              {earnedPoints} / {totalPoints} points &middot;{' '}
              <span
                className={
                  passed
                    ? 'text-emerald-600 dark:text-emerald-400 font-medium'
                    : 'text-red-600 dark:text-red-400 font-medium'
                }
              >
                {passed ? 'Passed' : 'Did not pass'}
              </span>
            </p>
          </div>
          {ai_feedback?.overall_feedback && (
            <p className="flex-1 text-xs text-aegisaxiom-text-secondary leading-relaxed border-l border-aegisaxiom-border pl-4">
              {ai_feedback.overall_feedback}
            </p>
          )}
        </div>
      )}

      {(description || instructions) && (
        <div className="px-6 pt-5 pb-1 space-y-1">
          {description && (
            <p className="text-sm text-aegisaxiom-text-secondary leading-relaxed">{description}</p>
          )}
          {instructions && (
            <p className="text-xs text-aegisaxiom-text-muted leading-relaxed italic">{instructions}</p>
          )}
        </div>
      )}

      <form onSubmit={handleSubmit} className="px-6 py-5 space-y-8">
        {questions.map((q, idx) => {
          const fb = feedbackMap[q.id];
          const answeredValue = answers[q.id];
          const displayValue = isGraded ? (fb?.submitted_answer ?? answeredValue) : answeredValue;

          return (
            <div key={q.id} className="space-y-3">
              <div className="flex items-start justify-between gap-3">
                <p className="text-sm font-medium text-aegisaxiom-text leading-snug">
                  <span className="text-violet-600 dark:text-violet-400 font-semibold mr-2">
                    {idx + 1}.
                  </span>
                  {q.prompt}
                </p>
                <span className="text-xs text-aegisaxiom-text-muted font-mono shrink-0 mt-0.5">
                  {q.points} pt{q.points !== 1 ? 's' : ''}
                </span>
              </div>

              {q.type === 'multiple_choice' && (
                <MultipleChoice
                  question={q}
                  value={displayValue}
                  onChange={(val) => setAnswer(q.id, val)}
                  disabled={disabled}
                />
              )}
              {q.type === 'multi_select' && (
                <MultiSelect
                  question={q}
                  value={displayValue ?? []}
                  onChange={(val) => setAnswer(q.id, val)}
                  disabled={disabled}
                />
              )}
              {q.type === 'true_false' && (
                <TrueFalse
                  question={q}
                  value={displayValue}
                  onChange={(val) => setAnswer(q.id, val)}
                  disabled={disabled}
                />
              )}
              {q.type === 'free_text' && (
                <FreeText
                  value={displayValue ?? ''}
                  onChange={(val) => setAnswer(q.id, val)}
                  disabled={disabled}
                />
              )}
              {q.type === 'scenario_match' && (
                <ScenarioMatch
                  question={q}
                  value={displayValue ?? {}}
                  onChange={(val) => setAnswer(q.id, val)}
                  disabled={disabled}
                />
              )}

              {isGraded && <QuestionResult feedback={fb} />}
            </div>
          );
        })}

        {isPending && (
          <div className="pt-2 flex items-center justify-between gap-4 border-t border-aegisaxiom-border">
            {submitError ? (
              <p className="text-xs text-red-600 dark:text-red-400 flex items-center gap-1.5">
                <AlertTriangle size={12} />
                {submitError}
              </p>
            ) : (
              <p className="text-xs text-aegisaxiom-text-muted">
                Answer all questions before submitting.
              </p>
            )}
            <button
              type="submit"
              disabled={submitting}
              className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-violet-600 to-fuchsia-600 text-aegisaxiom-text text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-all shrink-0"
            >
              {submitting ? (
                <>
                  <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Submitting…
                </>
              ) : (
                <>
                  <Send size={14} />
                  Submit Quiz
                </>
              )}
            </button>
          </div>
        )}

        {isSubmitted && (
          <div className="pt-2 flex items-center justify-center gap-2 border-t border-aegisaxiom-border text-sm text-aegisaxiom-text-muted">
            <div className="w-3.5 h-3.5 border-2 border-aegisaxiom-border-hover border-t-violet-400 rounded-full animate-spin" />
            Grading your submission…
          </div>
        )}
      </form>
    </div>
  );
}
