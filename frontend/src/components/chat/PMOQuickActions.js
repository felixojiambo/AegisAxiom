import React, { useState, useEffect } from 'react';
import { Clock, AlertTriangle, Plus, FileText, X, Sparkles } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';

/**
 * PMOQuickActions — suggestion chips shown at the top of the PMO chat.
 *
 * Only renders when the active agent is the PMO agent (?agent=pmo or
 * pinned_agent === 'pmo'). Chips are hidden after the first user message in
 * the session so they don't clutter an in-flight conversation.
 *
 * Props:
 *   onSend(text: string)   — call the parent's sendMessage equivalent
 *   effectivePinnedAgent   — the resolved agent key ("pmo" | other | null)
 *   messageCount           — number of messages in the current session
 */

const CHIPS = [
  {
    id: 'changed_today',
    label: 'What changed today?',
    message: 'Summarize what changed in my projects in the last 24 hours.',
    Icon: Clock,
  },
  {
    id: 'my_blockers',
    label: 'Show my blockers',
    message: 'What blockers do I currently own?',
    Icon: AlertTriangle,
  },
  {
    id: 'create_project',
    label: 'Create a new project',
    message: 'Help me create a new project.',
    Icon: Plus,
  },
  {
    id: 'client_update',
    label: 'Client update for...',
    message: null, // handled specially — opens inline project input
    Icon: FileText,
  },
];

// localStorage key for onboarding hint dismissal
function onboardingKey(userId) {
  return `pmo_chat_v1.2_onboarding_dismissed:${userId}`;
}

export default function PMOQuickActions({ onSend, effectivePinnedAgent, messageCount }) {
  const { user } = useAuth();
  const [clientUpdateMode, setClientUpdateMode] = useState(false);
  const [projectName, setProjectName] = useState('');
  const [onboardingDismissed, setOnboardingDismissed] = useState(() => {
    if (!user?.user_id) return true; // hide if no user yet
    return localStorage.getItem(onboardingKey(user.user_id)) === '1';
  });

  // Sync dismissal flag when user loads
  useEffect(() => {
    if (!user?.user_id) return;
    setOnboardingDismissed(localStorage.getItem(onboardingKey(user.user_id)) === '1');
  }, [user?.user_id]);

  // Only render in PMO agent context
  if (effectivePinnedAgent !== 'pmo') return null;

  // Hide chips after first user message — conversation is underway
  if (messageCount > 0) return null;

  const handleChip = (chip) => {
    if (chip.id === 'client_update') {
      setClientUpdateMode(true);
      return;
    }
    onSend(chip.message);
  };

  const handleClientUpdateSubmit = (e) => {
    e.preventDefault();
    const name = projectName.trim();
    if (!name) return;
    onSend(`Generate a client update for "${name}" covering the last week.`);
    setClientUpdateMode(false);
    setProjectName('');
  };

  const dismissOnboarding = () => {
    if (user?.user_id) {
      localStorage.setItem(onboardingKey(user.user_id), '1');
    }
    setOnboardingDismissed(true);
  };

  return (
    <div
      className="px-4 md:px-6 pt-4 pb-2 flex flex-col gap-3"
      data-testid="pmo-quick-actions"
    >
      {/* One-time onboarding hint */}
      {!onboardingDismissed && (
        <div
          className="relative flex items-start gap-3 rounded-xl border border-violet-200 dark:border-violet-600/30 bg-violet-50 dark:bg-violet-600/10 px-4 py-3 text-sm text-aegisaxiom-text leading-relaxed"
          role="status"
          data-testid="pmo-onboarding-hint"
        >
          <Sparkles
            size={16}
            className="text-violet-500 shrink-0 mt-0.5"
            aria-hidden="true"
          />
          <p className="flex-1 text-xs text-aegisaxiom-text-secondary">
            <span className="font-semibold text-aegisaxiom-text">Hey — AegisAxiom AI just got new powers.</span>{' '}
            I can now create projects, update tasks, assign work, and summarize what changed today.
            Try one of the quick actions below, or just ask me.
          </p>
          <button
            onClick={dismissOnboarding}
            className="shrink-0 text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition-colors p-0.5 rounded"
            aria-label="Dismiss hint"
            data-testid="pmo-onboarding-dismiss"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* Quick-action chips */}
      {!clientUpdateMode ? (
        <div
          className="flex flex-wrap gap-2"
          role="group"
          aria-label="PMO quick actions"
        >
          {CHIPS.map((chip) => (
            <button
              key={chip.id}
              onClick={() => handleChip(chip)}
              data-testid={`pmo-chip-${chip.id}`}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-aegisaxiom-border bg-aegisaxiom-surface hover:bg-aegisaxiom-elevated hover:border-violet-300 dark:hover:border-violet-600/40 text-xs text-aegisaxiom-text-secondary hover:text-violet-700 dark:hover:text-violet-300 transition-all active:scale-95"
            >
              <chip.Icon size={13} className="shrink-0" aria-hidden="true" />
              {chip.label}
            </button>
          ))}
        </div>
      ) : (
        /* Inline project picker for "Client update for..." */
        <form
          onSubmit={handleClientUpdateSubmit}
          className="flex items-center gap-2"
          data-testid="pmo-client-update-form"
        >
          <label htmlFor="pmo-project-name" className="sr-only">
            Project name
          </label>
          <input
            id="pmo-project-name"
            type="text"
            autoFocus
            placeholder="Project name..."
            value={projectName}
            onChange={(e) => setProjectName(e.target.value)}
            className="flex-1 bg-aegisaxiom-surface border border-aegisaxiom-border focus:border-violet-500 focus:ring-2 focus:ring-violet-500/10 rounded-lg px-3 py-1.5 text-xs text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted outline-none transition-all"
          />
          <button
            type="submit"
            disabled={!projectName.trim()}
            className="px-3 py-1.5 rounded-lg bg-gradient-to-r from-violet-600 to-fuchsia-600 text-white text-xs font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-all active:scale-95"
            data-testid="pmo-client-update-submit"
          >
            Go
          </button>
          <button
            type="button"
            onClick={() => {
              setClientUpdateMode(false);
              setProjectName('');
            }}
            className="p-1.5 rounded-lg text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors"
            aria-label="Cancel"
          >
            <X size={14} />
          </button>
        </form>
      )}
    </div>
  );
}
