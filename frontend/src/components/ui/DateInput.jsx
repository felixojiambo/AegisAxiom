/**
 * DateInput — date picker styled to AEGISAXIOM dark/light theme.
 *
 * Drop-in replacement for `<input type="date">`:
 *   - `value` is a YYYY-MM-DD string (or '' / null).
 *   - `onChange(next)` receives a YYYY-MM-DD string when the user picks a
 *     date, or `''` when the user clears it. Matches the contract every
 *     existing call site uses, so swapping is a one-line change.
 *
 * UX notes:
 *   - Click or focus the input to open the calendar popover.
 *   - Esc, click-outside, and selecting a day close the popover.
 *   - Today + Clear buttons in the footer.
 *   - Week starts on Monday and the week number is shown on the left.
 *   - Rendered through a Radix Popover Portal so it escapes any ancestor
 *     with `backdrop-filter` / `transform` / `filter` that would otherwise
 *     create a containing block and trap `position: fixed`.
 */
import React, { useCallback, useMemo, useRef, useState } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { DayPicker } from 'react-day-picker';
import { Calendar as CalendarIcon, X as XIcon } from 'lucide-react';
import 'react-day-picker/dist/style.css';
import './DateInput.css';

// Parse a YYYY-MM-DD string as a Date in local time (avoids the UTC-
// midnight off-by-one we already hit elsewhere — see fmtDate in
// ProjectDetailPage.js).
function parseLocalDate(value) {
  if (!value || typeof value !== 'string') return undefined;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!m) return undefined;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isNaN(d.getTime()) ? undefined : d;
}

function formatLocalDate(date) {
  if (!date) return '';
  const y = date.getFullYear();
  const mo = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${mo}-${d}`;
}

function formatDisplay(date) {
  if (!date) return '';
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export default function DateInput({
  value,
  onChange,
  disabled = false,
  placeholder = 'Select a date',
  min,
  max,
  id,
  className = '',
  'data-testid': dataTestid,
  ariaLabel,
}) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef(null);

  const selectedDate = useMemo(() => parseLocalDate(value), [value]);
  const minDate = useMemo(() => parseLocalDate(min), [min]);
  const maxDate = useMemo(() => parseLocalDate(max), [max]);

  const handleSelect = useCallback(
    (date) => {
      if (!date) {
        onChange?.('');
        setOpen(false);
        return;
      }
      onChange?.(formatLocalDate(date));
      setOpen(false);
    },
    [onChange],
  );

  const handleToday = useCallback(() => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    handleSelect(today);
  }, [handleSelect]);

  const handleClear = useCallback(
    (e) => {
      // Stop the click from reaching the trigger button (which would
      // toggle the popover open).
      e.stopPropagation();
      e.preventDefault();
      onChange?.('');
    },
    [onChange],
  );

  const display = formatDisplay(selectedDate);

  return (
    <Popover.Root open={open} onOpenChange={(next) => !disabled && setOpen(next)}>
      <Popover.Trigger asChild>
        <button
          ref={buttonRef}
          type="button"
          id={id}
          disabled={disabled}
          aria-label={ariaLabel ?? placeholder}
          data-testid={dataTestid}
          className={`aegisaxiom-input w-full h-9 inline-flex items-center justify-between gap-2 text-left text-sm cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed ${className}`}
        >
          <span className={display ? 'text-aegisaxiom-text' : 'text-aegisaxiom-text-muted'}>
            {display || placeholder}
          </span>
          <span className="inline-flex items-center gap-1 shrink-0">
            {display && !disabled && (
              <span
                role="button"
                tabIndex={0}
                onClick={handleClear}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleClear(e); }
                }}
                aria-label="Clear date"
                className="h-5 w-5 inline-flex items-center justify-center rounded text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors"
              >
                <XIcon size={12} />
              </span>
            )}
            <CalendarIcon size={14} className="text-aegisaxiom-text-muted shrink-0" aria-hidden="true" />
          </span>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={4}
          onOpenAutoFocus={(e) => e.preventDefault()}
          className="z-[60] rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface shadow-xl animate-in fade-in-0 zoom-in-95 duration-100 aegisaxiom-date-popover"
          data-testid={dataTestid ? `${dataTestid}-popover` : undefined}
        >
          <DayPicker
            mode="single"
            selected={selectedDate}
            onSelect={handleSelect}
            defaultMonth={selectedDate ?? undefined}
            weekStartsOn={1}
            showWeekNumber
            disabled={
              minDate || maxDate
                ? [
                    ...(minDate ? [{ before: minDate }] : []),
                    ...(maxDate ? [{ after: maxDate }] : []),
                  ]
                : undefined
            }
          />
          <div className="flex items-center justify-between gap-2 px-3 pb-3 pt-1 border-t border-aegisaxiom-border">
            <button
              type="button"
              onClick={() => onChange?.('')}
              className="text-xs text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition-colors"
              data-testid={dataTestid ? `${dataTestid}-clear` : undefined}
            >
              Clear
            </button>
            <button
              type="button"
              onClick={handleToday}
              className="text-xs font-medium text-violet-400 hover:text-violet-300 transition-colors"
              data-testid={dataTestid ? `${dataTestid}-today` : undefined}
            >
              Today
            </button>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
