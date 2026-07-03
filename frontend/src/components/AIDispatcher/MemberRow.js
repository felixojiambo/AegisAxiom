import React, { useState } from 'react';
import { GripVertical, Trash2, AlertCircle } from 'lucide-react';
import { AVAILABILITY_OPTIONS, AVAILABILITY_LABELS } from './constants';

/**
 * MemberRow — renders a single team member for both round-robin and skill-based modes.
 *
 * Props:
 *   member         — the member object
 *   mode           — "round_robin" | "skill_based"
 *   onUpdate       — (memberId, patch) => void   (called on field blur/change)
 *   onRemove       — (memberId) => void
 *   isDragging     — bool (visual feedback during drag)
 *   dragHandleProps — spread onto the drag handle element
 */
export default function MemberRow({
  member,
  mode,
  onUpdate,
  onRemove,
  isDragging = false,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
}) {
  const [skillValue, setSkillValue] = useState(member.skill_description ?? '');
  const showSkillWarning = mode === 'skill_based' && !skillValue.trim();

  const handleAvailChange = (e) => {
    onUpdate(member.member_id, { availability: e.target.value });
  };

  const handleSkillBlur = () => {
    if (skillValue !== (member.skill_description ?? '')) {
      onUpdate(member.member_id, { skill_description: skillValue });
    }
  };

  return (
    <div
      draggable={mode === 'round_robin'}
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onDragEnd={onDragEnd}
      className={`flex items-start gap-3 py-3 px-3 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-elevated transition-opacity ${
        isDragging ? 'opacity-40' : 'opacity-100'
      }`}
    >
      {/* Drag handle (round-robin only) */}
      {mode === 'round_robin' && (
        <div className="mt-2.5 text-aegisaxiom-text-muted cursor-grab active:cursor-grabbing shrink-0">
          <GripVertical size={16} />
        </div>
      )}

      {/* Name */}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-aegisaxiom-text truncate">{member.name}</p>

        {/* Round-robin: order index display */}
        {mode === 'round_robin' && (
          <p className="text-xs text-aegisaxiom-text-muted mt-0.5">Order #{member.order_index + 1}</p>
        )}

        {/* Skill-based: skill description textarea */}
        {mode === 'skill_based' && (
          <div className="mt-2">
            <textarea
              value={skillValue}
              onChange={(e) => setSkillValue(e.target.value)}
              onBlur={handleSkillBlur}
              placeholder="Describe this member's skills (used by the AI to match requests)…"
              rows={2}
              className="input-field text-xs resize-none"
            />
            {showSkillWarning && (
              <div className="flex items-center gap-1 mt-1">
                <AlertCircle size={12} className="text-amber-400 shrink-0" />
                <span className="text-xs text-amber-400">
                  No skill description — AI may not route to this member effectively.
                </span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Availability */}
      <div className="shrink-0 w-32">
        <select
          value={member.availability}
          onChange={handleAvailChange}
          className="aegisaxiom-input w-full text-xs"
          aria-label={`Availability for ${member.name}`}
        >
          {AVAILABILITY_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>

      {/* Remove */}
      <button
        onClick={() => onRemove(member.member_id)}
        className="mt-1 p-1.5 text-aegisaxiom-text-muted hover:text-red-400 transition-colors shrink-0"
        aria-label={`Remove ${member.name}`}
        title="Remove member"
      >
        <Trash2 size={15} />
      </button>
    </div>
  );
}
