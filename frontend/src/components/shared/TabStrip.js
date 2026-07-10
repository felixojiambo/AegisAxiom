import React, { useRef, useEffect, useState } from 'react';

/**
 * TabStrip - Unified tab primitive for the AEGISAXIOM app.
 *
 * Handles horizontal overflow on mobile with scroll-snap + edge fade indicators.
 * Used across Sales Ops (main tabs + inner sub-tabs) and can be reused anywhere
 * a consistent tab pattern is needed.
 *
 * @param {Array} tabs - [{ id, label, icon?, badge?, hint? }]
 * @param {string} activeId
 * @param {(id: string) => void} onChange
 * @param {'underline' | 'pill' | 'segmented'} variant
 *   - underline: main-page tabs (border-bottom style)
 *   - pill:      violet filled active, ghost inactive (Procurement sub-tabs style)
 *   - segmented: inline-flex bordered container (Contractor Management style)
 * @param {'sm' | 'md'} size
 */
export default function TabStrip({
  tabs,
  activeId,
  onChange,
  variant = 'underline',
  size = 'md',
  stretch = false,
  className = '',
}) {
  const scrollRef = useRef(null);
  const [showLeftFade, setShowLeftFade] = useState(false);
  const [showRightFade, setShowRightFade] = useState(false);

  // Track scroll position to show/hide edge fades
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const updateFades = () => {
      const { scrollLeft, scrollWidth, clientWidth } = el;
      setShowLeftFade(scrollLeft > 4);
      setShowRightFade(scrollLeft + clientWidth < scrollWidth - 4);
    };

    updateFades();
    el.addEventListener('scroll', updateFades, { passive: true });
    window.addEventListener('resize', updateFades);
    return () => {
      el.removeEventListener('scroll', updateFades);
      window.removeEventListener('resize', updateFades);
    };
  }, [tabs]);

  // Scroll active tab into view when it changes (important on mobile)
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const activeBtn = el.querySelector(`[data-tab-id="${activeId}"]`);
    if (activeBtn && typeof activeBtn.scrollIntoView === 'function') {
      activeBtn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
    }
  }, [activeId]);

  const pad = size === 'sm' ? 'px-3 py-1.5 text-sm' : 'px-4 py-2.5 text-sm';

  // When stretch is on, pills/segments take equal 1/Nth of the width on mobile
  // and revert to content-width on sm: breakpoint and up.
  const flexClass = stretch
    ? 'flex-1 sm:flex-none justify-center sm:justify-start min-w-0'
    : 'shrink-0';

  const getButtonClasses = (isActive) => {
    const base = `${pad} font-medium transition whitespace-nowrap flex items-center gap-2 ${flexClass}`;
    if (variant === 'pill') {
      return `${base} rounded-lg ${
        isActive
          ? 'bg-violet-600 text-white shadow-sm'
          : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'
      }`;
    }
    if (variant === 'segmented') {
      return `${base} rounded-md ${
        isActive ? 'bg-violet-600 text-white' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
      }`;
    }
    // underline (default)
    return `${base} border-b-2 -mb-px ${
      isActive
        ? 'border-violet-500 text-aegisaxiom-text'
        : 'border-transparent text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
    }`;
  };

  const stretchWrapper = stretch ? 'w-full sm:w-auto' : '';

  const wrapperClasses =
    variant === 'segmented'
      ? `inline-flex bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-1 gap-1 ${stretchWrapper}`
      : variant === 'pill'
        ? `flex gap-2 ${stretchWrapper}`
        : `flex gap-1 border-b border-aegisaxiom-border ${stretchWrapper}`;

  return (
    <div className={`relative ${className}`}>
      {/* Left fade */}
      {showLeftFade && (
        <div
          className="pointer-events-none absolute left-0 top-0 bottom-0 w-8 z-10"
          style={{
            background:
              'linear-gradient(to right, var(--aegisaxiom-base, transparent) 0%, transparent 100%)',
          }}
        />
      )}
      {/* Right fade */}
      {showRightFade && (
        <div
          className="pointer-events-none absolute right-0 top-0 bottom-0 w-8 z-10"
          style={{
            background:
              'linear-gradient(to left, var(--aegisaxiom-base, transparent) 0%, transparent 100%)',
          }}
        />
      )}

      <div
        ref={scrollRef}
        className={`${wrapperClasses} overflow-x-auto scrollbar-hide`}
        style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}
      >
        {tabs.map((tab) => {
          const isActive = tab.id === activeId;
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              data-tab-id={tab.id}
              onClick={() => onChange(tab.id)}
              className={getButtonClasses(isActive)}
              title={tab.hint || tab.label}
            >
              {Icon && <Icon className={`w-4 h-4 shrink-0 ${stretch ? 'hidden sm:block' : ''}`} />}
              <span className={stretch ? 'truncate min-w-0' : ''}>{tab.label}</span>
              {tab.badge !== undefined && tab.badge !== null && tab.badge !== 0 && (
                <span
                  className={`text-xs font-bold px-1.5 py-0.5 rounded-full shrink-0 ${
                    isActive ? 'bg-white/25 text-white' : 'bg-amber-500 text-white'
                  }`}
                >
                  {tab.badge}
                </span>
              )}
              {tab.hint && tab.id === activeId && tab.hint !== tab.label && (
                <span className="text-xs opacity-75 ml-1 hidden sm:inline">{tab.hint}</span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
