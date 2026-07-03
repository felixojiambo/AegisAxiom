import React, { useState, useRef, useEffect } from 'react';
import { ChevronLeft, ChevronRight, Calendar, X } from 'lucide-react';

const DAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

const pad = (n) => String(n).padStart(2, '0');
const toISO = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const today = () => {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
};
const addDays = (d, n) => {
  const r = new Date(d);
  r.setDate(r.getDate() + n);
  return r;
};
const addMonths = (d, n) => {
  const r = new Date(d);
  r.setMonth(r.getMonth() + n);
  return r;
};

function isoToDate(s) {
  if (!s) return null;
  const [y, m, d] = s.split('-').map(Number);
  return new Date(y, m - 1, d);
}

function fmtLabel(iso) {
  if (!iso) return null;
  const d = isoToDate(iso);
  return d.toLocaleDateString('en-US', { day: '2-digit', month: 'short', year: '2-digit' });
}

const PRESETS = [
  { key: 'all', label: 'All Time', get: () => ({ from: '', to: '' }) },
  {
    key: '2w',
    label: 'Past 2 Weeks',
    get: () => {
      const t = today();
      return { from: toISO(addDays(t, -14)), to: toISO(t) };
    },
  },
  {
    key: '1m',
    label: 'Past 1 Month',
    get: () => {
      const t = today();
      return { from: toISO(addMonths(t, -1)), to: toISO(t) };
    },
  },
  {
    key: '3m',
    label: 'Past 3 Months',
    get: () => {
      const t = today();
      return { from: toISO(addMonths(t, -3)), to: toISO(t) };
    },
  },
  {
    key: '6m',
    label: 'Past 6 Months',
    get: () => {
      const t = today();
      return { from: toISO(addMonths(t, -6)), to: toISO(t) };
    },
  },
];

function buildCalendar(year, month) {
  const first = new Date(year, month, 1);
  const last = new Date(year, month + 1, 0);
  const cells = [];
  // pad start
  for (let i = 0; i < first.getDay(); i++) {
    cells.push({ date: addDays(first, -(first.getDay() - i)), cur: false });
  }
  for (let d = 1; d <= last.getDate(); d++) {
    cells.push({ date: new Date(year, month, d), cur: true });
  }
  // pad end
  const rem = 42 - cells.length;
  for (let i = 1; i <= rem; i++) {
    cells.push({ date: addDays(last, i), cur: false });
  }
  return cells;
}

const MONTH_NAMES = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
];

export default function DateRangePicker({ dateFrom, dateTo, onChange }) {
  const [open, setOpen] = useState(false);
  const [viewYear, setViewYear] = useState(() => {
    const t = today();
    return t.getFullYear();
  });
  const [viewMonth, setViewMonth] = useState(() => {
    const t = today();
    return t.getMonth();
  });
  const [hovered, setHovered] = useState(null); // ISO string of hovered day
  const [activePreset, setActivePreset] = useState('all');
  const [tempFrom, setTempFrom] = useState(dateFrom || '');
  const [tempTo, setTempTo] = useState(dateTo || '');
  const ref = useRef(null);

  // sync from parent
  useEffect(() => {
    setTempFrom(dateFrom || '');
    setTempTo(dateTo || '');
  }, [dateFrom, dateTo]);

  // close on outside click
  useEffect(() => {
    const h = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  const cells = buildCalendar(viewYear, viewMonth);

  const prevMonth = () => {
    if (viewMonth === 0) {
      setViewMonth(11);
      setViewYear((y) => y - 1);
    } else setViewMonth((m) => m - 1);
  };
  const nextMonth = () => {
    if (viewMonth === 11) {
      setViewMonth(0);
      setViewYear((y) => y + 1);
    } else setViewMonth((m) => m + 1);
  };

  const handleDayClick = (iso) => {
    if (!tempFrom || (tempFrom && tempTo)) {
      // Start new selection
      setTempFrom(iso);
      setTempTo('');
      setActivePreset('custom');
    } else {
      // Complete selection
      if (iso < tempFrom) {
        setTempFrom(iso);
        setTempTo(tempFrom);
      } else {
        setTempTo(iso);
      }
      setActivePreset('custom');
    }
  };

  const applyAndClose = (from, to, preset) => {
    setTempFrom(from);
    setTempTo(to);
    setActivePreset(preset);
    onChange({ date_from: from, date_to: to });
    setOpen(false);
  };

  const handlePreset = (p) => {
    const { from, to } = p.get();
    applyAndClose(from, to, p.key);
  };

  const handleApply = () => {
    onChange({ date_from: tempFrom, date_to: tempTo });
    setOpen(false);
  };

  const handleReset = () => {
    setTempFrom('');
    setTempTo('');
    setActivePreset('all');
    onChange({ date_from: '', date_to: '' });
    setOpen(false);
  };

  // Determine day state for styling
  const getDayState = (iso) => {
    const effectiveTo = tempTo || (tempFrom && hovered && hovered > tempFrom ? hovered : null);
    const isStart = iso === tempFrom;
    const isEnd = iso === (tempTo || null);
    const inRange = tempFrom && effectiveTo && iso > tempFrom && iso < effectiveTo;
    const isToday = iso === toISO(today());
    const isHoverEnd = !tempTo && hovered === iso && tempFrom && iso > tempFrom;
    return { isStart, isEnd, inRange, isToday, isHoverEnd };
  };

  const buttonLabel = () => {
    if (!tempFrom && !tempTo) return 'All Time';
    if (tempFrom && tempTo) return `${fmtLabel(tempFrom)} – ${fmtLabel(tempTo)}`;
    if (tempFrom) return `From ${fmtLabel(tempFrom)}`;
    return 'All Time';
  };

  const hasFilter = !!(tempFrom || tempTo);

  return (
    <div className="relative" ref={ref}>
      {/* Trigger button */}
      <div className="flex items-center gap-1">
        <button
          onClick={() => setOpen((o) => !o)}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-medium transition-all
            ${
              hasFilter
                ? 'bg-violet-50 dark:bg-violet-600/15 border-violet-500/40 text-violet-700 dark:text-violet-300'
                : 'bg-aegisaxiom-surface border-aegisaxiom-border text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:border-aegisaxiom-border-hover'
            }`}
        >
          <Calendar size={12} className="shrink-0" />
          <span className="max-w-[150px] truncate">{buttonLabel()}</span>
          <ChevronRight
            size={11}
            className={`transition-transform duration-200 ${open ? 'rotate-90' : ''}`}
          />
        </button>
        {hasFilter && (
          <button
            onClick={handleReset}
            className="text-aegisaxiom-text-muted hover:text-red-600 dark:text-red-400 transition-colors p-1 rounded"
            title="Clear"
          >
            <X size={12} />
          </button>
        )}
      </div>

      {/* Dropdown panel */}
      {open && (
        <div
          className="absolute right-0 top-full mt-2 z-50 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl animate-fadeInUp overflow-hidden"
          style={{ minWidth: '480px' }}
        >
          <div className="flex">
            {/* LEFT: Presets */}
            <div className="w-40 border-r border-aegisaxiom-border py-3 flex flex-col">
              {PRESETS.map((p) => (
                <button
                  key={p.key}
                  onClick={() => handlePreset(p)}
                  className={`text-left px-4 py-2.5 text-xs font-medium transition-colors
                    ${
                      activePreset === p.key
                        ? 'text-violet-700 dark:text-violet-300 bg-violet-50 dark:bg-violet-600/15'
                        : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'
                    }`}
                >
                  {p.label}
                </button>
              ))}
              <div className="flex-1" />
              <button
                onClick={handleReset}
                className="mx-4 mt-3 mb-3 text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 font-medium text-left transition-colors"
              >
                Reset
              </button>
            </div>

            {/* RIGHT: Calendar */}
            <div className="flex-1 p-4">
              {/* Month nav */}
              <div className="flex items-center justify-between mb-4">
                <button
                  onClick={prevMonth}
                  className="p-1.5 rounded-lg text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors"
                >
                  <ChevronLeft size={14} />
                </button>
                <span className="text-sm font-semibold text-aegisaxiom-text">
                  {MONTH_NAMES[viewMonth]} {viewYear}
                </span>
                <button
                  onClick={nextMonth}
                  className="p-1.5 rounded-lg text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors"
                >
                  <ChevronRight size={14} />
                </button>
              </div>

              {/* Day headers */}
              <div className="grid grid-cols-7 mb-1">
                {DAYS.map((d) => (
                  <div key={d} className="text-center text-xs font-medium text-aegisaxiom-text-muted py-1">
                    {d}
                  </div>
                ))}
              </div>

              {/* Day cells */}
              <div className="grid grid-cols-7">
                {cells.map(({ date, cur }, i) => {
                  const iso = toISO(date);
                  const { isStart, isEnd, inRange, isToday, isHoverEnd } = getDayState(iso);
                  const isSelected = isStart || isEnd || isHoverEnd;
                  const isInRange = inRange;

                  return (
                    <div key={i} className="relative flex items-center justify-center py-0.5">
                      {/* Range background (spans full width) */}
                      {isInRange && (
                        <div className="absolute inset-y-0.5 left-0 right-0 bg-violet-50 dark:bg-violet-600/15" />
                      )}
                      {/* Start cap */}
                      {isStart && (tempTo || (hovered && hovered > tempFrom)) && (
                        <div className="absolute inset-y-0.5 left-1/2 right-0 bg-violet-50 dark:bg-violet-600/15" />
                      )}
                      {/* End cap */}
                      {(isEnd || isHoverEnd) && tempFrom && (
                        <div className="absolute inset-y-0.5 left-0 right-1/2 bg-violet-50 dark:bg-violet-600/15" />
                      )}

                      <button
                        onClick={() => cur && handleDayClick(iso)}
                        onMouseEnter={() => setHovered(iso)}
                        onMouseLeave={() => setHovered(null)}
                        className={`relative z-10 w-7 h-7 rounded-full text-xs font-medium transition-all flex items-center justify-center
                          ${!cur ? 'text-aegisaxiom-text-muted cursor-default' : 'cursor-pointer'}
                          ${
                            isSelected && cur
                              ? 'bg-violet-600 text-white font-bold'
                              : isToday && cur
                                ? 'text-violet-600 dark:text-violet-400 font-bold'
                                : cur
                                  ? 'text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated hover:text-aegisaxiom-text'
                                  : ''
                          }`}
                      >
                        {date.getDate()}
                      </button>
                    </div>
                  );
                })}
              </div>

              {/* Apply button (only when custom range with both dates) */}
              {tempFrom && tempTo && activePreset === 'custom' && (
                <div className="mt-3 pt-3 border-t border-aegisaxiom-border flex justify-end gap-2">
                  <span className="text-xs text-aegisaxiom-text-muted self-center">
                    {fmtLabel(tempFrom)} – {fmtLabel(tempTo)}
                  </span>
                  <button
                    onClick={handleApply}
                    className="px-4 py-1.5 bg-violet-600 hover:bg-violet-500 text-white text-xs font-medium rounded-lg transition-colors"
                  >
                    Apply
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
