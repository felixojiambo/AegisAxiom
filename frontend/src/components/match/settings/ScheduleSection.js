import React from 'react';
import { Clock } from 'lucide-react';

/**
 * ScheduleSection
 *
 * Fields: mode, daily_batch_time, timezone, auto_approve_threshold,
 * reviewer_threshold, escalation_email.
 *
 * No test endpoint for this section.
 */

const MODES = [
  {
    value: 'immediate',
    label: 'Immediately on receipt',
    description: 'Rows are processed as soon as a session is created.',
  },
  {
    value: 'daily_batch',
    label: 'Daily batch',
    description: 'Collect rows throughout the day; run processing once at the configured time.',
  },
  {
    value: 'manual_approval',
    label: 'Manual approval before processing',
    description: 'An admin must explicitly kick off processing on each session.',
  },
];

const COMMON_TIMEZONES = [
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'America/Phoenix',
  'America/Anchorage',
  'Pacific/Honolulu',
  'UTC',
];

export default function ScheduleSection({ value = {}, onChange, readOnly }) {
  function patch(changes) {
    onChange({ ...value, ...changes });
  }

  return (
    <div className="space-y-5">
      {/* Mode */}
      <fieldset>
        <legend className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted mb-2.5">
          Processing Mode
        </legend>
        <div className="space-y-2">
          {MODES.map(({ value: modeVal, label, description }) => (
            <label
              key={modeVal}
              className={`flex items-start gap-3 rounded-lg border p-3.5 cursor-pointer transition-colors ${
                (value.mode ?? 'immediate') === modeVal
                  ? 'border-aegisaxiom-primary bg-aegisaxiom-primary/10'
                  : 'border-aegisaxiom-border hover:border-aegisaxiom-primary/40'
              } ${readOnly ? 'cursor-default' : ''}`}
            >
              <input
                type="radio"
                name="schedule-mode"
                value={modeVal}
                checked={(value.mode ?? 'immediate') === modeVal}
                onChange={() => !readOnly && patch({ mode: modeVal })}
                disabled={readOnly}
                className="mt-0.5 h-4 w-4 accent-aegisaxiom-primary shrink-0"
              />
              <div>
                <div className="text-sm font-medium text-aegisaxiom-text">{label}</div>
                <div className="text-xs text-aegisaxiom-text-muted mt-0.5">{description}</div>
              </div>
            </label>
          ))}
        </div>
      </fieldset>

      {/* Daily batch time + timezone (only visible when daily_batch) */}
      {(value.mode ?? 'immediate') === 'daily_batch' && (
        <div className="glass-card rounded-lg border border-aegisaxiom-border p-4 flex flex-wrap items-end gap-4">
          <div className="space-y-1">
            <label htmlFor="batch-time" className="block text-xs font-medium text-aegisaxiom-text-muted flex items-center gap-1">
              <Clock className="h-3 w-3" />
              Batch time
            </label>
            <input
              id="batch-time"
              type="time"
              className="input-field text-sm"
              value={value.daily_batch_time ?? '09:00'}
              onChange={(e) => patch({ daily_batch_time: e.target.value })}
              disabled={readOnly}
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="batch-tz" className="block text-xs font-medium text-aegisaxiom-text-muted">
              Timezone
            </label>
            <select
              id="batch-tz"
              className="input-field text-sm"
              value={value.timezone ?? 'America/New_York'}
              onChange={(e) => patch({ timezone: e.target.value })}
              disabled={readOnly}
            >
              {COMMON_TIMEZONES.map((tz) => (
                <option key={tz} value={tz}>
                  {tz}
                </option>
              ))}
              {/* If a custom value isn't in the list, still render it */}
              {!COMMON_TIMEZONES.includes(value.timezone ?? 'America/New_York') && (
                <option value={value.timezone}>{value.timezone}</option>
              )}
            </select>
          </div>
        </div>
      )}

      {/* Thresholds */}
      <div className="space-y-3">
        <div className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
          Confidence Thresholds
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <ThresholdField
            id="auto-approve"
            label="Auto-approve threshold"
            hint="Rows with confidence >= this are auto-approved without reviewer action."
            value={value.auto_approve_threshold ?? 95}
            onChange={(v) => patch({ auto_approve_threshold: v })}
            disabled={readOnly}
          />
          <ThresholdField
            id="reviewer-threshold"
            label="Reviewer required below"
            hint="Rows with confidence below this are flagged for reviewer attention."
            value={value.reviewer_threshold ?? 70}
            onChange={(v) => patch({ reviewer_threshold: v })}
            disabled={readOnly}
          />
        </div>
      </div>

      {/* Escalation email */}
      <div className="space-y-1">
        <label htmlFor="escalation-email" className="block text-xs font-medium text-aegisaxiom-text-muted">
          Escalation Email
          <span className="ml-1 font-normal text-aegisaxiom-text-muted">(optional — rows that can't be auto-approved or reviewed)</span>
        </label>
        <input
          id="escalation-email"
          type="email"
          className="input-field w-full text-sm sm:w-80"
          placeholder="admin@yourcompany.com"
          value={value.escalation_email ?? ''}
          onChange={(e) => patch({ escalation_email: e.target.value })}
          disabled={readOnly}
        />
      </div>
    </div>
  );
}

function ThresholdField({ id, label, hint, value, onChange, disabled }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-xs font-medium text-aegisaxiom-text">
        {label}
      </label>
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="number"
          min="0"
          max="100"
          className="input-field w-16 text-sm text-center"
          value={value}
          onChange={(e) => {
            const n = Math.min(100, Math.max(0, parseInt(e.target.value, 10) || 0));
            onChange(n);
          }}
          disabled={disabled}
        />
        <span className="text-xs text-aegisaxiom-text-muted">%</span>
      </div>
      <p className="text-xs text-aegisaxiom-text-muted leading-relaxed">{hint}</p>
    </div>
  );
}
