import React, { useState } from 'react';
import { Eye, EyeOff, RotateCcw } from 'lucide-react';

/**
 * SecretField — shared component for Fernet-encrypted fields.
 *
 * Wire protocol:
 *   - GET returns "••••••••••••" (SENTINEL) when a value is stored, "" when empty.
 *   - PUT preserves ciphertext when the submitted value is empty or equals SENTINEL.
 *   - PUT re-encrypts when the submitted value is any other non-empty string.
 *
 * Internal state machine:
 *   "idle"  — displaying the value as received from GET (sentinel or "")
 *   "edit"  — user clicked "Change", typing new plaintext (or can cancel back to idle)
 *
 * On save the parent calls getSubmitValue() — or just reads `value` via onChange —
 * both produce the correct string to POST: either the sentinel (preserve) or new plaintext.
 */

export const SENTINEL = '••••••••••••'; // 12 bullet chars

/**
 * Props:
 *   value       — current string value (sentinel or empty on initial load; plaintext while editing)
 *   onChange    — called with the new string whenever the user types or cancels
 *   placeholder — placeholder text for the input
 *   disabled    — disables the entire field (read-only mode)
 *   id          — html id for label association
 *   label       — optional label override (unused here; caller supplies <label> separately)
 */
export default function SecretField({ value, onChange, placeholder, disabled, id }) {
  const isSentinel = value === SENTINEL;
  const [showText, setShowText] = useState(false);
  const [editing, setEditing] = useState(false);

  function handleChangeClick() {
    // Enter edit mode: clear the sentinel so user types from scratch
    setEditing(true);
    onChange('');
  }

  function handleCancel() {
    // Restore sentinel — backend will preserve ciphertext on PUT
    setEditing(false);
    onChange(SENTINEL);
  }

  function handleInput(e) {
    onChange(e.target.value);
  }

  // In "configured + not editing" state, show a sentinel badge with a Change button
  if (isSentinel && !editing) {
    return (
      <div className="flex items-center gap-2">
        <div className="input-field flex-1 flex items-center gap-2 text-sm">
          <span className="tracking-widest text-aegisaxiom-text-muted select-none" aria-label="Value configured">
            ••••••••••••
          </span>
          <span className="text-xs text-aegisaxiom-text-muted ml-1 font-normal">(configured)</span>
        </div>
        {!disabled && (
          <button
            type="button"
            className="btn-secondary text-xs px-2.5 py-1.5 flex items-center gap-1.5 whitespace-nowrap"
            onClick={handleChangeClick}
          >
            <RotateCcw className="h-3 w-3" />
            Change
          </button>
        )}
      </div>
    );
  }

  // Edit mode or empty — show the actual input
  return (
    <div className="flex items-center gap-2">
      <div className="relative flex-1">
        <input
          id={id}
          type={showText ? 'text' : 'password'}
          autoComplete="new-password"
          className="input-field w-full pr-9 text-sm font-mono"
          placeholder={placeholder}
          value={value}
          onChange={handleInput}
          disabled={disabled}
        />
        <button
          type="button"
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition-colors"
          onClick={() => setShowText((v) => !v)}
          aria-label={showText ? 'Hide value' : 'Show value'}
          tabIndex={-1}
        >
          {showText ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
        </button>
      </div>
      {editing && (
        <button
          type="button"
          className="btn-secondary text-xs px-2.5 py-1.5 whitespace-nowrap"
          onClick={handleCancel}
        >
          Cancel
        </button>
      )}
    </div>
  );
}
