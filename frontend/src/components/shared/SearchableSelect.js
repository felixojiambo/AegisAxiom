import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, Search } from 'lucide-react';

/**
 * SearchableSelect — accessible, searchable dropdown with optional grouped options.
 *
 * Props:
 *   label        — optional label text above the field
 *   value        — currently selected value
 *   onChange      — callback with the selected value string
 *   options       — [{ value, label, subtitle? }]
 *   placeholder   — text shown when nothing is selected
 *   disabled      — disables the control
 *   required      — shows a red asterisk on the label
 *   grouped       — optional { "Category Name": ["value1", "value2"] } to group options
 */
export default function SearchableSelect({
  label,
  value,
  onChange,
  options,
  placeholder,
  disabled,
  required,
  grouped,
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [activeIndex, setActiveIndex] = useState(-1);
  const ref = useRef(null);
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const listId = useRef(`ss-list-${Math.random().toString(36).slice(2, 8)}`).current;

  // Close on outside click
  useEffect(() => {
    function handleClick(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  // Focus search input when opened
  useEffect(() => {
    if (open && inputRef.current) inputRef.current.focus();
  }, [open]);

  // Reset active index when search or open state changes
  useEffect(() => {
    setActiveIndex(-1);
  }, [search, open]);

  const lowerSearch = search.toLowerCase();
  const filtered = options.filter(
    (o) =>
      o.label.toLowerCase().includes(lowerSearch) || o.value.toLowerCase().includes(lowerSearch),
  );

  const selected = options.find((o) => o.value === value);

  // Build flat list of filtered options (for keyboard nav)
  const flatFiltered = (() => {
    if (!grouped) return filtered;
    const result = [];
    const categorized = new Set();
    for (const [, keys] of Object.entries(grouped)) {
      const items = filtered.filter((o) => keys.includes(o.value));
      items.forEach((i) => {
        result.push(i);
        categorized.add(i.value);
      });
    }
    const uncategorized = filtered.filter((o) => !categorized.has(o.value));
    uncategorized.forEach((i) => result.push(i));
    return result;
  })();

  // Build grouped options for rendering
  let groupedOptions = null;
  if (grouped) {
    groupedOptions = {};
    const categorized = new Set();
    for (const [cat, keys] of Object.entries(grouped)) {
      const items = filtered.filter((o) => keys.includes(o.value));
      if (items.length > 0) {
        groupedOptions[cat] = items;
        items.forEach((i) => categorized.add(i.value));
      }
    }
    const uncategorized = filtered.filter((o) => !categorized.has(o.value));
    if (uncategorized.length > 0) groupedOptions['Other'] = uncategorized;
  }

  const selectOption = useCallback(
    (val) => {
      onChange(val);
      setOpen(false);
      setSearch('');
    },
    [onChange],
  );

  function handleKeyDown(e) {
    if (!open) {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
        e.preventDefault();
        setOpen(true);
      }
      return;
    }
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setActiveIndex((prev) => Math.min(prev + 1, flatFiltered.length - 1));
        break;
      case 'ArrowUp':
        e.preventDefault();
        setActiveIndex((prev) => Math.max(prev - 1, 0));
        break;
      case 'Enter':
        e.preventDefault();
        if (activeIndex >= 0 && activeIndex < flatFiltered.length) {
          selectOption(flatFiltered[activeIndex].value);
        }
        break;
      case 'Escape':
        e.preventDefault();
        setOpen(false);
        setSearch('');
        break;
      default:
        break;
    }
  }

  // Scroll active item into view
  useEffect(() => {
    if (activeIndex >= 0 && listRef.current) {
      const items = listRef.current.querySelectorAll('[role="option"]');
      if (items[activeIndex]) items[activeIndex].scrollIntoView({ block: 'nearest' });
    }
  }, [activeIndex]);

  function renderOption(opt, idx) {
    const flatIdx = flatFiltered.indexOf(opt);
    return (
      <button
        key={opt.value}
        type="button"
        role="option"
        aria-selected={opt.value === value}
        onClick={() => selectOption(opt.value)}
        className={`w-full text-left px-3 py-2 text-sm hover:bg-aegisaxiom-elevated flex flex-col
          ${opt.value === value ? 'bg-violet-900/30 text-violet-700 dark:text-violet-300' : 'text-white'}
          ${flatIdx === activeIndex ? 'bg-aegisaxiom-elevated' : ''}`}
      >
        <span>{opt.label}</span>
        {opt.subtitle && (
          <span className="text-[11px] text-aegisaxiom-text-muted leading-tight">{opt.subtitle}</span>
        )}
      </button>
    );
  }

  return (
    <div ref={ref} className="relative" onKeyDown={handleKeyDown}>
      {label && (
        <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
          {label}
          {required && <span className="text-red-600 dark:text-red-400 ml-0.5">*</span>}
        </label>
      )}
      <button
        type="button"
        disabled={disabled}
        onClick={() => !disabled && setOpen(!open)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        className={`bg-aegisaxiom-elevated border rounded-lg px-3 py-2 text-sm w-full text-left flex items-center justify-between
          ${open ? 'border-violet-500' : 'border-aegisaxiom-border-hover'} ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}
          ${value ? 'text-aegisaxiom-text' : 'text-aegisaxiom-text-secondary'}`}
      >
        <div className="truncate">
          {selected?.label || placeholder || 'Select...'}
          {selected?.subtitle && (
            <span className="text-aegisaxiom-text-muted text-xs ml-1.5">— {selected.subtitle}</span>
          )}
        </div>
        <ChevronDown
          size={14}
          className={`text-aegisaxiom-text-secondary shrink-0 ml-2 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg shadow-xl max-h-72 flex flex-col">
          <div className="p-2 border-b border-aegisaxiom-border">
            <div className="relative">
              <Search
                size={14}
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-aegisaxiom-text-secondary"
              />
              <input
                ref={inputRef}
                autoFocus
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search..."
                aria-label="Search options"
                className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded px-3 py-1.5 pl-8 text-aegisaxiom-text text-sm w-full
                  focus:border-violet-500 focus:outline-none placeholder-aegisaxiom-text-muted"
              />
            </div>
          </div>
          <div
            ref={listRef}
            role="listbox"
            id={listId}
            aria-label={label || 'Options'}
            className="overflow-y-auto flex-1"
          >
            {groupedOptions
              ? Object.entries(groupedOptions).map(([cat, items]) => (
                  <div key={cat} role="group" aria-label={cat}>
                    <div className="px-3 py-1.5 text-[10px] font-semibold text-aegisaxiom-text-muted uppercase tracking-wider bg-aegisaxiom-elevated sticky top-0">
                      {cat}
                    </div>
                    {items.map(renderOption)}
                  </div>
                ))
              : filtered.map(renderOption)}
            {flatFiltered.length === 0 && (
              <p className="px-3 py-3 text-sm text-aegisaxiom-text-muted">No matches</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
