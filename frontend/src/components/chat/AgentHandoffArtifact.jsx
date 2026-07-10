import React from 'react';
import { ArrowRight } from 'lucide-react';

export default function AgentHandoffArtifact({ artifact }) {
  const { from_agent_name, to_agent_name, reason, handed_off_at } = artifact.payload;

  const formattedTime = handed_off_at
    ? new Date(handed_off_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : null;

  return (
    <div className="my-4 relative">
      {/* Gradient border via pseudo-wrapper */}
      <div className="rounded-xl p-px bg-gradient-to-r from-violet-600/40 via-fuchsia-500/30 to-violet-600/40">
        <div className="rounded-[11px] bg-aegisaxiom-surface px-5 py-4">
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-xs font-semibold uppercase tracking-widest text-aegisaxiom-text-muted">
              Agent Handoff
            </span>
            {formattedTime && <span className="text-xs text-aegisaxiom-text-muted">{formattedTime}</span>}
          </div>

          <div className="mt-3 flex items-center gap-3 flex-wrap">
            <span className="px-3 py-1.5 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border text-sm font-medium text-violet-700 dark:text-violet-300">
              {from_agent_name || 'Previous Agent'}
            </span>
            <ArrowRight size={16} className="text-fuchsia-600 dark:text-fuchsia-400 shrink-0" />
            <span className="px-3 py-1.5 rounded-lg bg-violet-50 dark:bg-violet-500/10 border border-violet-500/40 text-sm font-medium text-violet-700 dark:text-violet-200">
              {to_agent_name || 'Next Agent'}
            </span>
          </div>

          {reason && (
            <p className="mt-3 text-xs text-aegisaxiom-text-secondary leading-relaxed italic border-t border-aegisaxiom-border pt-3">
              {reason}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
