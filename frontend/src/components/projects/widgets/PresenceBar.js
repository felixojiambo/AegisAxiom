import React, { useState } from 'react';
import UserAvatar from './UserAvatar';

const MAX_VISIBLE = 5;

/**
 * Renders a compact avatar stack for users currently viewing the project.
 * - Shows up to MAX_VISIBLE avatars, then a "+N" overflow badge.
 * - Tooltip on hover/focus: name + viewport label.
 * - CSS transition on join/leave (opacity + scale via Tailwind).
 * - Mobile: the stack shrinks; tooltips work on tap (toggle via focus).
 */
export default function PresenceBar({ viewing = [] }) {
  const [tipId, setTipId] = useState(null);

  if (viewing.length === 0) return null;

  const visible = viewing.slice(0, MAX_VISIBLE);
  const overflow = viewing.length - MAX_VISIBLE;

  const viewportLabel = (vp) => {
    if (!vp) return null;
    const labels = {
      board: 'Board',
      backlog: 'Backlog',
      gantt: 'Gantt',
      calendar: 'Calendar',
      milestones: 'Milestones',
      phases: 'Phases',
      sprints: 'Sprints',
      planning: 'Planning',
      review: 'Review',
      threads: 'Threads',
      timeline: 'Wave Timeline',
      standup: 'Standup',
      postmortem: 'Postmortems',
      overview: 'Overview',
    };
    return labels[vp] ?? vp;
  };

  return (
    <div
      className="flex items-center gap-0.5"
      role="list"
      aria-label="Currently viewing"
      data-testid="presence-bar"
    >
      {visible.map((u) => {
        const uid = u.user_id ?? u._id ?? u.name;
        const label = viewportLabel(u.viewport);
        const tooltipText = label
          ? `${u.name ?? 'Someone'} is viewing ${label}`
          : (u.name ?? 'Someone');
        const showTip = tipId === uid;

        return (
          <div
            key={uid}
            className="relative"
            role="listitem"
          >
            <button
              type="button"
              aria-label={tooltipText}
              className="block rounded-full ring-2 ring-aegisaxiom-bg transition-all duration-200 ease-out hover:ring-violet-400 hover:scale-110 focus:outline-none focus:ring-violet-400 focus:scale-110 animate-fade-in"
              onMouseEnter={() => setTipId(uid)}
              onMouseLeave={() => setTipId(null)}
              onFocus={() => setTipId(uid)}
              onBlur={() => setTipId(null)}
              onClick={() => setTipId(showTip ? null : uid)}
            >
              <UserAvatar user={u} size="sm" />
            </button>

            {showTip && (
              <div
                role="tooltip"
                className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2 py-1 rounded bg-aegisaxiom-surface border border-aegisaxiom-border text-[11px] text-aegisaxiom-text whitespace-nowrap z-50 shadow-lg pointer-events-none"
              >
                {tooltipText}
                <div className="absolute top-full left-1/2 -translate-x-1/2 w-0 h-0 border-x-4 border-x-transparent border-t-4 border-t-aegisaxiom-border" />
              </div>
            )}
          </div>
        );
      })}

      {overflow > 0 && (
        <span
          className="inline-flex items-center justify-center h-5 w-5 rounded-full bg-aegisaxiom-elevated border border-aegisaxiom-border text-[9px] font-semibold text-aegisaxiom-text-muted shrink-0"
          aria-label={`${overflow} more viewer${overflow === 1 ? '' : 's'}`}
          title={`${overflow} more viewer${overflow === 1 ? '' : 's'}`}
        >
          +{overflow}
        </span>
      )}
    </div>
  );
}
