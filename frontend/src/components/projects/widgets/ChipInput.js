import React, { useState, useRef } from 'react';
import { X, Plus } from 'lucide-react';

export default function ChipInput({ value = [], onChange, placeholder = 'Add item…', disabled }) {
  const [draft, setDraft] = useState('');
  const inputRef = useRef(null);

  const add = () => {
    const v = draft.trim();
    if (!v) return;
    if (!value.includes(v)) onChange([...value, v]);
    setDraft('');
  };

  const remove = (chip) => onChange(value.filter((c) => c !== chip));

  return (
    <div className="flex flex-wrap gap-1.5 p-2 rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface min-h-[36px]" data-testid="chip-input">
      {value.map((chip) => (
        <span
          key={chip}
          className="inline-flex items-center gap-1 h-5 pl-2 pr-1 rounded-full bg-aegisaxiom-elevated border border-aegisaxiom-border text-[11px] text-aegisaxiom-text"
          data-testid={`chip-${chip}`}
        >
          {chip}
          {!disabled ? (
            <button
              type="button"
              onClick={() => remove(chip)}
              className="hover:text-red-400"
              aria-label={`Remove ${chip}`}
            >
              <X size={10} />
            </button>
          ) : null}
        </span>
      ))}
      {!disabled ? (
        <div className="flex items-center gap-1">
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); add(); }
              if (e.key === 'Backspace' && !draft && value.length > 0) {
                remove(value[value.length - 1]);
              }
            }}
            placeholder={placeholder}
            className="aegisaxiom-input h-5 text-[11px] min-w-[80px] border-none bg-transparent focus:outline-none p-0"
            data-testid="chip-input-field"
          />
          {draft ? (
            <button type="button" onClick={add} className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text">
              <Plus size={12} />
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
