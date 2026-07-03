import React, { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { ChevronDown, Clock, Edit2, History, Plus, Search, Trash2, X, Zap } from 'lucide-react';
import { toast } from 'sonner';
import API_URL from '../../lib/apiConfig';
import ConfirmModal from '../shared/ConfirmModal';
import SearchableSelect from '../shared/SearchableSelect';

const API = API_URL;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TRIGGER_TYPES = [
  { value: 'record_action', label: 'Record Action' },
  { value: 'field_change', label: 'Field Change' },
  { value: 'date_field', label: 'Date Field' },
  { value: 'scheduled', label: 'Scheduled' },
];

const RECORD_ACTIONS = [
  { value: 'created', label: 'Created' },
  { value: 'updated', label: 'Updated' },
  { value: 'deleted', label: 'Deleted' },
  { value: 'created_or_updated', label: 'Created or Updated' },
];

const OFFSET_UNITS = [
  { value: 'minutes', label: 'Minutes' },
  { value: 'hours', label: 'Hours' },
  { value: 'days', label: 'Days' },
];

const OFFSET_DIRECTIONS = [
  { value: 'before', label: 'Before' },
  { value: 'after', label: 'After' },
  { value: 'on', label: 'On' },
];

const REPEAT_OPTIONS = [
  { value: 'once', label: 'Once' },
  { value: 'daily', label: 'Daily' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'yearly', label: 'Yearly' },
];

const TIMEZONE_OPTIONS = [
  { value: 'America/New_York', label: 'Eastern (America/New_York)' },
  { value: 'America/Chicago', label: 'Central (America/Chicago)' },
  { value: 'America/Denver', label: 'Mountain (America/Denver)' },
  { value: 'America/Los_Angeles', label: 'Pacific (America/Los_Angeles)' },
  { value: 'UTC', label: 'UTC' },
  { value: 'Europe/London', label: 'London (Europe/London)' },
];

const WEBHOOK_METHODS = [
  { value: 'GET', label: 'GET' },
  { value: 'POST', label: 'POST' },
  { value: 'PUT', label: 'PUT' },
];

const ACTION_TYPES = [
  { value: 'update_field', label: 'Update Field' },
  { value: 'create_record', label: 'Create Record' },
  { value: 'send_notification', label: 'Send Notification' },
  { value: 'send_email', label: 'Send Email' },
  { value: 'trigger_webhook', label: 'Trigger Webhook' },
  { value: 'trigger_workflow', label: 'Trigger Workflow' },
  { value: 'delete_record', label: 'Delete Record' },
];

const DELAY_UNITS = [
  { value: 'minutes', label: 'Minutes' },
  { value: 'hours', label: 'Hours' },
  { value: 'days', label: 'Days' },
];

const OPERATORS_BY_TYPE = {
  string: ['equals', 'not_equals', 'contains', 'starts_with', 'is_empty', 'is_not_empty'],
  number: ['equals', 'not_equals', 'greater_than', 'less_than', 'between'],
  time: ['equals', 'before', 'after', 'between'],
  date: ['equals', 'before', 'after', 'between'],
  boolean: ['is_true', 'is_false'],
  list: ['in', 'not_in'],
};

const OPERATOR_LABELS = {
  equals: 'Equals',
  not_equals: 'Not Equals',
  contains: 'Contains',
  starts_with: 'Starts With',
  is_empty: 'Is Empty',
  is_not_empty: 'Is Not Empty',
  greater_than: 'Greater Than',
  less_than: 'Less Than',
  between: 'Between',
  before: 'Before',
  after: 'After',
  is_true: 'Is True',
  is_false: 'Is False',
  in: 'In',
  not_in: 'Not In',
};

const CONDITION_LOGIC_OPTIONS = [
  { value: 'AND', label: 'ALL (AND)' },
  { value: 'OR', label: 'ANY (OR)' },
  { value: 'CUSTOM', label: 'Custom Pattern' },
];

const NOTIFICATION_TARGETS = [
  { value: 'role', label: 'Role' },
  { value: 'user', label: 'User' },
  { value: 'record_field', label: 'Record Field' },
];

const EXECUTION_STATUS_STYLES = {
  success:
    'bg-green-50 dark:bg-green-900/40 text-green-700 dark:text-green-300 border border-green-200 dark:border-green-700/40',
  failed:
    'bg-red-50 dark:bg-red-900/40 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-700/40',
  skipped: 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary border border-aegisaxiom-border-hover/40',
  partial:
    'bg-amber-50 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-700/40',
};

const EMPTY_RULE = {
  name: '',
  description: '',
  module: '',
  trigger: { type: 'record_action', action: 'created' },
  condition_logic: 'AND',
  condition_pattern: '',
  conditions: [],
  instant_actions: [],
  scheduled_actions: [],
};

const EMPTY_ACTION = { type: 'update_field' };

function buildModuleCategories(modules) {
  const groups = {};
  for (const m of modules) {
    const cat = m.category || 'Other';
    if (!groups[cat]) groups[cat] = [];
    groups[cat].push(m.name);
  }
  return groups;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function humanize(str) {
  if (!str) return '';
  return str.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function fieldTypeFor(fieldName, fieldDefs) {
  const def = fieldDefs.find((f) => f.name === fieldName);
  return def?.type ?? 'string';
}

function formatTimestamp(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

// ---------------------------------------------------------------------------
// Small UI pieces
// ---------------------------------------------------------------------------

function Toggle({ enabled, onChange, disabled }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      disabled={disabled}
      onClick={() => !disabled && onChange(!enabled)}
      className={`relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 border-transparent transition-colors focus:outline-none
        ${disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}
        ${enabled ? 'bg-violet-600' : 'bg-aegisaxiom-elevated'}`}
    >
      <span
        className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow transition-transform
          ${enabled ? 'translate-x-4' : 'translate-x-0'}`}
      />
    </button>
  );
}

function SelectField({ label, value, onChange, options, placeholder, disabled, required }) {
  return (
    <div>
      {label && (
        <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
          {label}
          {required && <span className="text-red-600 dark:text-red-400 ml-0.5">*</span>}
        </label>
      )}
      <div className="relative">
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg px-3 py-2 text-aegisaxiom-text text-sm
            focus:border-violet-500 focus:outline-none w-full appearance-none pr-8
            disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {placeholder && <option value="">{placeholder}</option>}
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <ChevronDown
          size={14}
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-aegisaxiom-text-secondary pointer-events-none"
        />
      </div>
    </div>
  );
}

function InputField({
  label,
  value,
  onChange,
  placeholder,
  type = 'text',
  required,
  disabled,
  min,
}) {
  return (
    <div>
      {label && (
        <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
          {label}
          {required && <span className="text-red-600 dark:text-red-400 ml-0.5">*</span>}
        </label>
      )}
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        required={required}
        disabled={disabled}
        min={min}
        className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg px-3 py-2 text-aegisaxiom-text text-sm
          focus:border-violet-500 focus:outline-none w-full placeholder-aegisaxiom-text-muted
          disabled:opacity-50 disabled:cursor-not-allowed"
      />
    </div>
  );
}

function TextareaField({ label, value, onChange, placeholder, rows = 3, disabled }) {
  return (
    <div>
      {label && (
        <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">{label}</label>
      )}
      <textarea
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={rows}
        disabled={disabled}
        className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg px-3 py-2 text-aegisaxiom-text text-sm
          focus:border-violet-500 focus:outline-none w-full placeholder-aegisaxiom-text-muted resize-none
          disabled:opacity-50 disabled:cursor-not-allowed font-mono"
      />
    </div>
  );
}

function SectionLabel({ children }) {
  return (
    <p className="text-xs font-semibold text-aegisaxiom-text-secondary uppercase tracking-wider mb-3">
      {children}
    </p>
  );
}

function TemplateHint() {
  return (
    <p className="text-[11px] text-aegisaxiom-text-muted mt-1.5">
      Use{' '}
      <code className="text-violet-600 dark:text-violet-400 bg-aegisaxiom-elevated px-1 rounded">
        {'{{record.field_name}}'}
      </code>{' '}
      for template variables
    </p>
  );
}

// ---------------------------------------------------------------------------
// Condition Row
// ---------------------------------------------------------------------------

function ConditionRow({ condition, index, fieldDefs, onChange, onRemove }) {
  const fieldType = fieldTypeFor(condition.field, fieldDefs);
  const operators = OPERATORS_BY_TYPE[fieldType] ?? OPERATORS_BY_TYPE.string;

  const fieldOptions = fieldDefs.map((f) => ({
    value: f.name,
    label: f.computed ? `${f.label} (computed)` : (f.label ?? humanize(f.name)),
    subtitle: f.description || undefined,
  }));

  const operatorOptions = operators.map((op) => ({
    value: op,
    label: OPERATOR_LABELS[op] ?? humanize(op),
  }));

  const noValueOps = ['is_empty', 'is_not_empty', 'is_true', 'is_false'];
  const needsValue = !noValueOps.includes(condition.operator);
  const isBooleanField = fieldType === 'boolean';
  const isNumberField = fieldType === 'number';
  const isBetween = condition.operator === 'between';
  const fieldDef = fieldDefs.find((f) => f.name === condition.field);
  const hasOptions = fieldDef?.options?.length > 0;

  function handleFieldChange(fieldName) {
    const newType = fieldTypeFor(fieldName, fieldDefs);
    const newOps = OPERATORS_BY_TYPE[newType] ?? OPERATORS_BY_TYPE.string;
    const operator = newOps.includes(condition.operator) ? condition.operator : newOps[0];
    onChange(index, { ...condition, field: fieldName, operator, value: '', value_type: newType });
  }

  function handleOperatorChange(op) {
    onChange(index, { ...condition, operator: op, value: '' });
  }

  function handleValueChange(val) {
    onChange(index, { ...condition, value: val });
  }

  return (
    <div className="flex items-start gap-2 p-3 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg">
      <div className="flex-1 grid grid-cols-12 gap-2 min-w-0">
        <div className="col-span-4">
          <SearchableSelect
            value={condition.field}
            onChange={handleFieldChange}
            options={fieldOptions}
            placeholder="Select field..."
          />
        </div>
        <div className="col-span-4">
          <SelectField
            value={condition.operator}
            onChange={handleOperatorChange}
            options={operatorOptions}
            placeholder="Operator..."
            disabled={!condition.field}
          />
        </div>
        <div className="col-span-4">
          {needsValue && !isBooleanField && !isBetween && hasOptions && (
            <SelectField
              value={condition.value ?? ''}
              onChange={handleValueChange}
              options={fieldDef.options.map((o) => ({ value: o.value, label: o.label }))}
              placeholder="Select value..."
              disabled={!condition.field}
            />
          )}
          {needsValue && !isBooleanField && !isBetween && !hasOptions && (
            <input
              type={isNumberField ? 'number' : 'text'}
              value={condition.value ?? ''}
              onChange={(e) =>
                handleValueChange(
                  isNumberField
                    ? e.target.value === ''
                      ? ''
                      : parseFloat(e.target.value)
                    : e.target.value,
                )
              }
              placeholder="Value..."
              disabled={!condition.field}
              className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg px-3 py-2 text-aegisaxiom-text text-sm
                focus:border-violet-500 focus:outline-none w-full placeholder-aegisaxiom-text-muted
                disabled:opacity-50 disabled:cursor-not-allowed"
            />
          )}
          {needsValue && isBooleanField && (
            <div className="flex items-center gap-2 py-2">
              <Toggle
                enabled={!!condition.value}
                onChange={(val) => handleValueChange(val)}
                disabled={!condition.field}
              />
              <span className="text-sm text-aegisaxiom-text-secondary">
                {condition.value ? 'True' : 'False'}
              </span>
            </div>
          )}
          {isBetween && (
            <div className="flex items-center gap-1">
              <input
                type={isNumberField ? 'number' : 'text'}
                value={Array.isArray(condition.value) ? (condition.value[0] ?? '') : ''}
                onChange={(e) => {
                  const arr = Array.isArray(condition.value) ? [...condition.value] : ['', ''];
                  arr[0] = isNumberField
                    ? e.target.value === ''
                      ? ''
                      : parseFloat(e.target.value)
                    : e.target.value;
                  handleValueChange(arr);
                }}
                placeholder="From"
                className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg px-2 py-2 text-aegisaxiom-text text-sm
                  focus:border-violet-500 focus:outline-none w-full placeholder-aegisaxiom-text-muted"
              />
              <span className="text-aegisaxiom-text-muted text-xs shrink-0">to</span>
              <input
                type={isNumberField ? 'number' : 'text'}
                value={Array.isArray(condition.value) ? (condition.value[1] ?? '') : ''}
                onChange={(e) => {
                  const arr = Array.isArray(condition.value) ? [...condition.value] : ['', ''];
                  arr[1] = isNumberField
                    ? e.target.value === ''
                      ? ''
                      : parseFloat(e.target.value)
                    : e.target.value;
                  handleValueChange(arr);
                }}
                placeholder="To"
                className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg px-2 py-2 text-aegisaxiom-text text-sm
                  focus:border-violet-500 focus:outline-none w-full placeholder-aegisaxiom-text-muted"
              />
            </div>
          )}
          {!needsValue && (
            <div className="py-2 text-xs text-aegisaxiom-text-muted italic">No value needed</div>
          )}
        </div>
      </div>
      <button
        type="button"
        onClick={() => onRemove(index)}
        className="text-aegisaxiom-text-muted hover:text-red-600 dark:text-red-400 transition-colors p-1.5 rounded mt-0.5 shrink-0"
        aria-label="Remove condition"
      >
        <X size={14} />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Workflow Action Row
// ---------------------------------------------------------------------------

function WorkflowActionRow({
  action,
  index,
  modules,
  allRules,
  currentRuleId,
  onChange,
  onRemove,
  isScheduled,
}) {
  const moduleOptions = modules.map((m) => ({ value: m.name, label: m.label ?? humanize(m.name) }));
  const ruleOptions = allRules
    .filter((r) => {
      const rid = r.rule_id ?? r.id;
      return rid && rid !== currentRuleId;
    })
    .map((r) => ({ value: String(r.rule_id ?? r.id), label: r.name }));

  function patch(updates) {
    onChange(index, { ...action, ...updates });
  }

  function handleTypeChange(type) {
    const base = { type };
    if (isScheduled) {
      base.delay_value = action.delay_value ?? 0;
      base.delay_unit = action.delay_unit ?? 'minutes';
    }
    onChange(index, base);
  }

  const showUpdateField = action.type === 'update_field';
  const showCreateRecord = action.type === 'create_record';
  const showNotification = action.type === 'send_notification';
  const showEmail = action.type === 'send_email';
  const showWebhook = action.type === 'trigger_webhook';
  const showTriggerWorkflow = action.type === 'trigger_workflow';
  const showDeleteRecord = action.type === 'delete_record';

  const fieldPairs = action.field_map ?? [{ field: '', value: '' }];

  return (
    <div className="p-3 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg space-y-3">
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <SelectField
            label="Action Type"
            value={action.type ?? ''}
            onChange={handleTypeChange}
            options={ACTION_TYPES}
            placeholder="Select type..."
          />
        </div>
        {isScheduled && (
          <>
            <div className="w-24">
              <InputField
                label="Delay"
                type="number"
                value={String(action.delay_value ?? 0)}
                onChange={(v) => patch({ delay_value: v === '' ? 0 : parseInt(v, 10) })}
                min={0}
              />
            </div>
            <div className="w-32">
              <SelectField
                label="Unit"
                value={action.delay_unit ?? 'minutes'}
                onChange={(v) => patch({ delay_unit: v })}
                options={DELAY_UNITS}
              />
            </div>
          </>
        )}
        <button
          type="button"
          onClick={() => onRemove(index)}
          className="text-aegisaxiom-text-muted hover:text-red-600 dark:text-red-400 transition-colors p-1.5 rounded mb-0.5 shrink-0"
          aria-label="Remove action"
        >
          <X size={14} />
        </button>
      </div>

      {/* Type-specific fields */}
      {showUpdateField && (
        <div className="grid grid-cols-3 gap-3">
          <SelectField
            label="Module"
            value={action.module ?? ''}
            onChange={(v) => patch({ module: v })}
            options={moduleOptions}
            placeholder="Select module..."
          />
          <InputField
            label="Field"
            value={action.field ?? ''}
            onChange={(v) => patch({ field: v })}
            placeholder="field_name"
          />
          <InputField
            label="Value"
            value={action.value ?? ''}
            onChange={(v) => patch({ value: v })}
            placeholder="new value or {{record.field}}"
          />
        </div>
      )}

      {showCreateRecord && (
        <div className="space-y-3">
          <SelectField
            label="Module"
            value={action.module ?? ''}
            onChange={(v) => patch({ module: v })}
            options={moduleOptions}
            placeholder="Select module..."
          />
          <div>
            <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
              Field Map
            </label>
            <div className="space-y-2">
              {fieldPairs.map((pair, pairIdx) => (
                <div key={pairIdx} className="flex items-center gap-2">
                  <input
                    type="text"
                    value={pair.field}
                    onChange={(e) => {
                      const updated = [...fieldPairs];
                      updated[pairIdx] = { ...pair, field: e.target.value };
                      patch({ field_map: updated });
                    }}
                    placeholder="field_name"
                    className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg px-3 py-2 text-aegisaxiom-text text-sm
                      focus:border-violet-500 focus:outline-none flex-1 placeholder-aegisaxiom-text-muted"
                  />
                  <span className="text-aegisaxiom-text-muted text-xs shrink-0">=</span>
                  <input
                    type="text"
                    value={pair.value}
                    onChange={(e) => {
                      const updated = [...fieldPairs];
                      updated[pairIdx] = { ...pair, value: e.target.value };
                      patch({ field_map: updated });
                    }}
                    placeholder="value or {{record.field}}"
                    className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg px-3 py-2 text-aegisaxiom-text text-sm
                      focus:border-violet-500 focus:outline-none flex-1 placeholder-aegisaxiom-text-muted"
                  />
                  <button
                    type="button"
                    onClick={() => {
                      const updated = fieldPairs.filter((_, i) => i !== pairIdx);
                      patch({ field_map: updated.length ? updated : [{ field: '', value: '' }] });
                    }}
                    className="text-aegisaxiom-text-muted hover:text-red-600 dark:text-red-400 p-1 rounded transition-colors shrink-0"
                    aria-label="Remove field mapping"
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
            </div>
            <button
              type="button"
              onClick={() => patch({ field_map: [...fieldPairs, { field: '', value: '' }] })}
              className="mt-2 flex items-center gap-1 text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 transition-colors"
            >
              <Plus size={12} /> Add field
            </button>
          </div>
          <TemplateHint />
        </div>
      )}

      {showNotification && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <SelectField
              label="Target Type"
              value={action.target ?? ''}
              onChange={(v) => patch({ target: v })}
              options={NOTIFICATION_TARGETS}
              placeholder="Select target..."
            />
            <InputField
              label="Target Value"
              value={action.target_value ?? ''}
              onChange={(v) => patch({ target_value: v })}
              placeholder="e.g. admin, {{record.assigned_to}}"
            />
          </div>
          <TextareaField
            label="Message"
            value={action.message ?? ''}
            onChange={(v) => patch({ message: v })}
            placeholder="Notification message..."
            rows={2}
          />
          <TemplateHint />
        </div>
      )}

      {showEmail && (
        <div className="space-y-3">
          <InputField
            label="To"
            value={action.to ?? ''}
            onChange={(v) => patch({ to: v })}
            placeholder="recipient@example.com or {{record.email}}"
          />
          <InputField
            label="Subject"
            value={action.subject ?? ''}
            onChange={(v) => patch({ subject: v })}
            placeholder="Email subject..."
          />
          <TextareaField
            label="Body"
            value={action.body ?? ''}
            onChange={(v) => patch({ body: v })}
            placeholder="Email body..."
            rows={3}
          />
          <TemplateHint />
        </div>
      )}

      {showWebhook && (
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-2">
              <InputField
                label="URL"
                value={action.url ?? ''}
                onChange={(v) => patch({ url: v })}
                placeholder="https://example.com/webhook"
              />
            </div>
            <SelectField
              label="Method"
              value={action.method ?? 'POST'}
              onChange={(v) => patch({ method: v })}
              options={WEBHOOK_METHODS}
            />
          </div>
          <TextareaField
            label="Body (JSON)"
            value={action.body ?? ''}
            onChange={(v) => patch({ body: v })}
            placeholder={'{"key": "{{record.field}}"}'}
            rows={3}
          />
          <TemplateHint />
        </div>
      )}

      {showTriggerWorkflow && (
        <SelectField
          label="Workflow Rule"
          value={action.workflow_id ?? ''}
          onChange={(v) => patch({ workflow_id: v })}
          options={ruleOptions}
          placeholder="Select workflow..."
        />
      )}

      {showDeleteRecord && (
        <div className="space-y-3">
          <SelectField
            label="Module"
            value={action.module ?? ''}
            onChange={(v) => patch({ module: v })}
            options={moduleOptions}
            placeholder="Select module..."
          />
          <TextareaField
            label="Filter (JSON)"
            value={action.filter ?? ''}
            onChange={(v) => patch({ filter: v })}
            placeholder={'{"field": "value"}'}
            rows={2}
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Execution History Panel
// ---------------------------------------------------------------------------

function ExecutionHistoryPanel({ ruleId, onClose }) {
  const [executions, setExecutions] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const res = await api.get(`${API}/api/admin/workflow-rules/${ruleId}/executions`);
        if (res.ok && !cancelled) {
          const data = await res.json();
          setExecutions(data.executions ?? data ?? []);
        }
      } catch {
        // silently fail — list still functions without history
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [ruleId]);

  return (
    <div className="border-t border-aegisaxiom-border bg-aegisaxiom-surface">
      <div className="px-4 py-3 flex items-center justify-between border-b border-aegisaxiom-border">
        <div className="flex items-center gap-2">
          <History size={14} className="text-violet-600 dark:text-violet-400" />
          <span className="text-sm font-medium text-aegisaxiom-text-secondary">Execution History</span>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary p-1 rounded transition-colors"
          aria-label="Close history panel"
        >
          <X size={14} />
        </button>
      </div>

      {loading ? (
        <div className="p-8 text-center">
          <div className="w-5 h-5 border-2 border-violet-500 border-t-transparent rounded-full animate-spin mx-auto mb-2" />
          <p className="text-aegisaxiom-text-muted text-xs">Loading executions...</p>
        </div>
      ) : executions.length === 0 ? (
        <div className="p-8 text-center">
          <Clock size={28} className="text-aegisaxiom-text-muted mx-auto mb-2" />
          <p className="text-aegisaxiom-text-muted text-sm">No executions recorded yet</p>
        </div>
      ) : (
        <div className="divide-y divide-aegisaxiom-border/40 max-h-64 overflow-y-auto">
          {executions.map((exec, idx) => (
            <div
              key={exec.id ?? idx}
              className="px-4 py-3 flex items-start gap-4 text-sm hover:bg-aegisaxiom-accent-muted transition-colors"
            >
              <div className="shrink-0 pt-0.5">
                <span
                  className={`px-2 py-0.5 rounded-full text-xs font-medium ${EXECUTION_STATUS_STYLES[exec.status] ?? EXECUTION_STATUS_STYLES.skipped}`}
                >
                  {humanize(exec.status ?? 'unknown')}
                </span>
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-aegisaxiom-text-secondary text-xs truncate">
                  {exec.actions_summary ?? '—'}
                </p>
                <p className="text-aegisaxiom-text-muted text-[11px] mt-0.5">
                  {humanize(exec.trigger_type ?? '—')}
                  {exec.execution_time_ms ? ` · ${exec.execution_time_ms}ms` : ''}
                </p>
              </div>
              <div className="shrink-0 text-[11px] text-aegisaxiom-text-muted whitespace-nowrap">
                {formatTimestamp(exec.executed_at ?? exec.timestamp)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab bar (inline — no shadcn Tabs)
// ---------------------------------------------------------------------------

function TabBar({ tabs, activeTab, onSelect }) {
  return (
    <div className="flex border-b border-aegisaxiom-border px-6" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={activeTab === tab.id}
          onClick={() => onSelect(tab.id)}
          className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors -mb-px
            ${
              activeTab === tab.id
                ? 'border-violet-500 text-aegisaxiom-text'
                : 'border-transparent text-aegisaxiom-text-secondary hover:text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover'
            }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Workflow Rule Builder Modal
// ---------------------------------------------------------------------------

const MODAL_TABS = [
  { id: 'basics', label: 'Basics' },
  { id: 'trigger', label: 'Trigger' },
  { id: 'conditions', label: 'Conditions' },
  { id: 'actions', label: 'Actions' },
];

function WorkflowModal({ rule, modules, allRules, onSave, onClose }) {
  const isEditing = !!(rule?.rule_id ?? rule?.id);
  const ruleId = rule?.rule_id ?? rule?.id;

  const [form, setForm] = useState(() => ({
    ...EMPTY_RULE,
    ...(rule ?? {}),
    trigger: rule?.trigger ?? { type: 'record_action', action: 'created' },
    conditions: rule?.conditions ? [...rule.conditions] : [],
    instant_actions: rule?.instant_actions ? [...rule.instant_actions] : [],
    scheduled_actions: rule?.scheduled_actions ? [...rule.scheduled_actions] : [],
  }));

  const [activeTab, setActiveTab] = useState('basics');
  // fieldDefs state removed — using moduleFieldDefs directly from parent props
  const [saving, setSaving] = useState(false);

  const moduleOptions = modules.map((m) => ({ value: m.name, label: m.label ?? humanize(m.name) }));
  const selectedModule = modules.find((m) => m.name === form.module);
  const moduleFieldDefs = selectedModule?.fields ?? [];
  const fieldOptionsWithDesc = moduleFieldDefs.map((f) => ({
    value: f.name,
    label: f.computed ? `${f.label} (computed)` : (f.label ?? humanize(f.name)),
    subtitle: f.description || undefined,
  }));
  const moduleDateFields = moduleFieldDefs.filter(
    (f) => f.type === 'date' || f.type === 'datetime',
  );

  // Use module metadata directly — no need for a separate API call
  const conditionFieldDefs = moduleFieldDefs;

  function setField(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function setTrigger(updates) {
    setForm((prev) => ({ ...prev, trigger: { ...prev.trigger, ...updates } }));
  }

  function handleModuleChange(module) {
    setForm((prev) => ({
      ...prev,
      module,
      conditions: [],
      trigger: { type: prev.trigger.type, action: prev.trigger.action },
    }));
  }

  // Conditions
  function addCondition() {
    setForm((prev) => ({
      ...prev,
      conditions: [
        ...prev.conditions,
        { field: '', operator: 'equals', value: '', value_type: 'string' },
      ],
    }));
  }

  function updateCondition(idx, updated) {
    setForm((prev) => {
      const conditions = [...prev.conditions];
      conditions[idx] = updated;
      return { ...prev, conditions };
    });
  }

  function removeCondition(idx) {
    setForm((prev) => ({ ...prev, conditions: prev.conditions.filter((_, i) => i !== idx) }));
  }

  // Instant actions
  function addInstantAction() {
    setForm((prev) => ({
      ...prev,
      instant_actions: [...prev.instant_actions, { ...EMPTY_ACTION }],
    }));
  }

  function updateInstantAction(idx, updated) {
    setForm((prev) => {
      const instant_actions = [...prev.instant_actions];
      instant_actions[idx] = updated;
      return { ...prev, instant_actions };
    });
  }

  function removeInstantAction(idx) {
    setForm((prev) => ({
      ...prev,
      instant_actions: prev.instant_actions.filter((_, i) => i !== idx),
    }));
  }

  // Scheduled actions
  function addScheduledAction() {
    setForm((prev) => ({
      ...prev,
      scheduled_actions: [
        ...prev.scheduled_actions,
        { ...EMPTY_ACTION, delay_value: 0, delay_unit: 'minutes' },
      ],
    }));
  }

  function updateScheduledAction(idx, updated) {
    setForm((prev) => {
      const scheduled_actions = [...prev.scheduled_actions];
      scheduled_actions[idx] = updated;
      return { ...prev, scheduled_actions };
    });
  }

  function removeScheduledAction(idx) {
    setForm((prev) => ({
      ...prev,
      scheduled_actions: prev.scheduled_actions.filter((_, i) => i !== idx),
    }));
  }

  async function handleSave() {
    if (!form.name.trim()) {
      toast.error('Rule name is required');
      setActiveTab('basics');
      return;
    }
    if (!form.module) {
      toast.error('Module is required');
      setActiveTab('basics');
      return;
    }
    if (form.condition_logic === 'CUSTOM' && !form.condition_pattern?.trim()) {
      toast.error('Custom condition pattern is required');
      setActiveTab('conditions');
      return;
    }

    const payload = {
      name: form.name.trim(),
      description: form.description ?? '',
      module: form.module,
      trigger: form.trigger,
      condition_logic: form.condition_logic,
      condition_pattern: form.condition_logic === 'CUSTOM' ? form.condition_pattern : null,
      conditions: form.conditions,
      instant_actions: form.instant_actions,
      scheduled_actions: form.scheduled_actions,
    };

    setSaving(true);
    try {
      let res;
      if (isEditing) {
        res = await api.put(`${API}/api/admin/workflow-rules/${ruleId}`, payload);
      } else {
        res = await api.post(`${API}/api/admin/workflow-rules`, payload);
      }
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail ?? 'Failed to save rule');
      }
      const saved = await res.json();
      toast.success(isEditing ? 'Rule updated' : 'Rule created');
      onSave(saved);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  }

  const trigger = form.trigger ?? {};

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="workflow-modal-title"
    >
      <div className="absolute inset-0" onClick={onClose} aria-hidden="true" />

      <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border rounded-2xl w-full max-w-3xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="border-b border-aegisaxiom-border px-6 py-4 flex items-center justify-between shrink-0">
          <h2
            id="workflow-modal-title"
            className="text-lg font-bold text-aegisaxiom-text"
            style={{ fontFamily: 'Manrope, sans-serif' }}
          >
            {isEditing ? 'Edit Workflow Rule' : 'New Workflow Rule'}
          </h2>
          <button
            onClick={onClose}
            className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition-colors"
            aria-label="Close modal"
          >
            <X size={20} />
          </button>
        </div>

        {/* Tab bar */}
        <div className="shrink-0">
          <TabBar tabs={MODAL_TABS} activeTab={activeTab} onSelect={setActiveTab} />
        </div>

        {/* Scrollable tab content */}
        <div className="flex-1 overflow-y-auto">
          {/* ---- Tab 1: Basics ---- */}
          {activeTab === 'basics' && (
            <div className="p-6 space-y-4">
              <InputField
                label="Rule Name"
                value={form.name}
                onChange={(v) => setField('name', v)}
                placeholder="e.g. Notify manager on project creation"
                required
              />
              <SearchableSelect
                label="Module"
                value={form.module}
                onChange={handleModuleChange}
                options={moduleOptions}
                placeholder="Select module..."
                required
                grouped={buildModuleCategories(modules)}
              />
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
                  Description
                </label>
                <textarea
                  value={form.description ?? ''}
                  onChange={(e) => setField('description', e.target.value)}
                  placeholder="Optional description of what this rule does..."
                  rows={3}
                  className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg px-3 py-2 text-aegisaxiom-text text-sm
                    focus:border-violet-500 focus:outline-none w-full placeholder-aegisaxiom-text-muted resize-none"
                />
              </div>
            </div>
          )}

          {/* ---- Tab 2: Trigger ---- */}
          {activeTab === 'trigger' && (
            <div className="p-6 space-y-5">
              <div>
                <SectionLabel>Trigger Type</SectionLabel>
                <div className="space-y-2" role="radiogroup" aria-label="Trigger type">
                  {TRIGGER_TYPES.map((tt) => (
                    <label
                      key={tt.value}
                      className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors
                        ${
                          trigger.type === tt.value
                            ? 'bg-violet-50 dark:bg-violet-600/10 border-violet-500/50'
                            : 'bg-aegisaxiom-accent-soft border-aegisaxiom-border hover:border-aegisaxiom-border-hover'
                        }`}
                    >
                      <input
                        type="radio"
                        name="trigger_type"
                        value={tt.value}
                        checked={trigger.type === tt.value}
                        onChange={() => setTrigger({ type: tt.value })}
                        className="mt-0.5 accent-violet-500"
                      />
                      <span className="text-sm font-medium text-aegisaxiom-text">{tt.label}</span>
                    </label>
                  ))}
                </div>
              </div>

              {/* Trigger-type config */}
              {trigger.type === 'record_action' && (
                <div>
                  <SectionLabel>Action</SectionLabel>
                  <SelectField
                    value={trigger.action ?? 'created'}
                    onChange={(v) => setTrigger({ action: v })}
                    options={RECORD_ACTIONS}
                  />
                </div>
              )}

              {trigger.type === 'field_change' && (
                <div>
                  <SectionLabel>Watch Field</SectionLabel>
                  <SelectField
                    value={trigger.watch_field ?? ''}
                    onChange={(v) => setTrigger({ watch_field: v })}
                    options={moduleFieldDefs.map((f) => ({
                      value: f.name,
                      label: f.label ?? humanize(f.name),
                    }))}
                    placeholder="Select field to watch..."
                    disabled={!form.module}
                  />
                  {!form.module && (
                    <p className="text-xs text-aegisaxiom-text-muted mt-1.5">
                      Select a module on the Basics tab first
                    </p>
                  )}
                </div>
              )}

              {trigger.type === 'date_field' && (
                <div className="space-y-4">
                  <div>
                    <SectionLabel>Date Field</SectionLabel>
                    <SelectField
                      value={trigger.date_field ?? ''}
                      onChange={(v) => setTrigger({ date_field: v })}
                      options={moduleDateFields.map((f) => ({
                        value: f.name,
                        label: f.label ?? humanize(f.name),
                      }))}
                      placeholder="Select date field..."
                      disabled={!form.module}
                    />
                    {!form.module && (
                      <p className="text-xs text-aegisaxiom-text-muted mt-1.5">
                        Select a module on the Basics tab first
                      </p>
                    )}
                  </div>
                  <div className="grid grid-cols-3 gap-4">
                    <InputField
                      label="Offset Value"
                      type="number"
                      value={String(trigger.offset_value ?? 0)}
                      onChange={(v) => setTrigger({ offset_value: v === '' ? 0 : parseInt(v, 10) })}
                      min={0}
                    />
                    <SelectField
                      label="Offset Unit"
                      value={trigger.offset_unit ?? 'days'}
                      onChange={(v) => setTrigger({ offset_unit: v })}
                      options={OFFSET_UNITS}
                    />
                    <SelectField
                      label="Direction"
                      value={trigger.offset_direction ?? 'before'}
                      onChange={(v) => setTrigger({ offset_direction: v })}
                      options={OFFSET_DIRECTIONS}
                    />
                  </div>
                  <SelectField
                    label="Repeat"
                    value={trigger.repeat ?? 'once'}
                    onChange={(v) => setTrigger({ repeat: v })}
                    options={REPEAT_OPTIONS}
                  />
                </div>
              )}

              {trigger.type === 'scheduled' && (
                <div className="space-y-4">
                  <InputField
                    label="Cron Expression"
                    value={trigger.cron_expression ?? ''}
                    onChange={(v) => setTrigger({ cron_expression: v })}
                    placeholder="0 9 * * 1  (every Monday at 9am)"
                  />
                  <SelectField
                    label="Timezone"
                    value={trigger.timezone ?? 'UTC'}
                    onChange={(v) => setTrigger({ timezone: v })}
                    options={TIMEZONE_OPTIONS}
                  />
                </div>
              )}
            </div>
          )}

          {/* ---- Tab 3: Conditions ---- */}
          {activeTab === 'conditions' && (
            <div className="p-6 space-y-4">
              <div>
                <SectionLabel>Condition Logic</SectionLabel>
                <div className="flex items-center gap-3">
                  <span className="text-sm text-aegisaxiom-text-secondary">Match:</span>
                  {CONDITION_LOGIC_OPTIONS.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setField('condition_logic', opt.value)}
                      className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors border
                        ${
                          form.condition_logic === opt.value
                            ? 'bg-violet-50 dark:bg-violet-600/20 border-violet-500/50 text-violet-700 dark:text-violet-300'
                            : 'bg-aegisaxiom-elevated border-aegisaxiom-border-hover text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover hover:text-aegisaxiom-text-secondary'
                        }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
                {form.condition_logic === 'CUSTOM' && (
                  <div className="mt-3">
                    <InputField
                      label="Pattern (e.g. (1 AND 2) OR 3)"
                      value={form.condition_pattern ?? ''}
                      onChange={(v) => setField('condition_pattern', v)}
                      placeholder="(1 AND 2) OR 3"
                    />
                  </div>
                )}
              </div>

              <div className="space-y-2">
                {form.conditions.map((cond, idx) => (
                  <ConditionRow
                    key={idx}
                    condition={cond}
                    index={idx}
                    fieldDefs={conditionFieldDefs}
                    onChange={updateCondition}
                    onRemove={removeCondition}
                  />
                ))}
              </div>

              <button
                type="button"
                onClick={addCondition}
                disabled={!form.module}
                className="flex items-center gap-1.5 text-sm text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300
                  disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                <Plus size={14} /> Add Condition
              </button>

              {!form.module && (
                <p className="text-xs text-aegisaxiom-text-muted">
                  Select a module on the Basics tab first
                </p>
              )}
              {form.module && form.conditions.length === 0 && (
                <p className="text-xs text-aegisaxiom-text-muted">No conditions — rule always matches.</p>
              )}
            </div>
          )}

          {/* ---- Tab 4: Actions ---- */}
          {activeTab === 'actions' && (
            <div className="p-6 space-y-6">
              {/* Instant Actions */}
              <div>
                <SectionLabel>Instant Actions</SectionLabel>
                <div className="space-y-3">
                  {form.instant_actions.map((action, idx) => (
                    <WorkflowActionRow
                      key={idx}
                      action={action}
                      index={idx}
                      modules={modules}
                      allRules={allRules}
                      currentRuleId={ruleId}
                      onChange={updateInstantAction}
                      onRemove={removeInstantAction}
                      isScheduled={false}
                    />
                  ))}
                </div>
                <button
                  type="button"
                  onClick={addInstantAction}
                  className="mt-3 flex items-center gap-1.5 text-sm text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 transition-colors"
                >
                  <Plus size={14} /> Add Instant Action
                </button>
              </div>

              <div className="border-t border-aegisaxiom-border" />

              {/* Scheduled Actions */}
              <div>
                <SectionLabel>Scheduled Actions</SectionLabel>
                <p className="text-xs text-aegisaxiom-text-muted mb-3">
                  These actions run after a configurable delay from the trigger time.
                </p>
                <div className="space-y-3">
                  {form.scheduled_actions.map((action, idx) => (
                    <WorkflowActionRow
                      key={idx}
                      action={action}
                      index={idx}
                      modules={modules}
                      allRules={allRules}
                      currentRuleId={ruleId}
                      onChange={updateScheduledAction}
                      onRemove={removeScheduledAction}
                      isScheduled={true}
                    />
                  ))}
                </div>
                <button
                  type="button"
                  onClick={addScheduledAction}
                  className="mt-3 flex items-center gap-1.5 text-sm text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 transition-colors"
                >
                  <Plus size={14} /> Add Scheduled Action
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-aegisaxiom-border px-6 py-4 flex items-center justify-between shrink-0">
          {/* Tab progress dots */}
          <div className="flex items-center gap-2">
            {MODAL_TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`w-2 h-2 rounded-full transition-colors ${
                  activeTab === tab.id ? 'bg-violet-500' : 'bg-aegisaxiom-elevated hover:bg-aegisaxiom-elevated'
                }`}
                aria-label={`Go to ${tab.label} tab`}
              />
            ))}
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-lg text-sm font-medium bg-aegisaxiom-elevated hover:bg-aegisaxiom-elevated
                text-aegisaxiom-text-secondary border border-aegisaxiom-border hover:border-aegisaxiom-border-hover transition-colors"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="px-4 py-2 rounded-lg text-sm font-medium bg-violet-600 hover:bg-violet-700
                text-aegisaxiom-text transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {saving ? 'Saving...' : isEditing ? 'Save Changes' : 'Create Rule'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function WorkflowRulesAdmin() {
  const [rules, setRules] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [modules, setModules] = useState([]);

  // Filters
  const [filterModule, setFilterModule] = useState('');
  const [filterTrigger, setFilterTrigger] = useState('');
  const [search, setSearch] = useState('');

  // Modal
  const [builderRule, setBuilderRule] = useState(null);
  const [builderOpen, setBuilderOpen] = useState(false);

  // Inline execution history panel — keyed by rule id
  const [historyOpen, setHistoryOpen] = useState({});

  // Confirm delete
  const [confirmState, setConfirmState] = useState({ open: false, ruleId: null, name: '' });

  // ---------------------------------------------------------------------------
  // Data loading
  // ---------------------------------------------------------------------------

  const loadModules = useCallback(async () => {
    try {
      const res = await api.get(`${API}/api/admin/workflow-rules/modules`);
      if (res.ok) {
        const data = await res.json();
        setModules(data.modules ?? []);
      }
    } catch {
      // silently fail — builder still usable
    }
  }, []);

  const loadRules = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filterModule) params.set('module', filterModule);
      if (filterTrigger) params.set('trigger_type', filterTrigger);
      if (search.trim()) params.set('search', search.trim());
      const qs = params.toString() ? `?${params.toString()}` : '';
      const res = await api.get(`${API}/api/admin/workflow-rules${qs}`);
      if (res.ok) {
        const data = await res.json();
        setRules(data.rules ?? data.items ?? []);
        setTotal(data.total ?? 0);
      } else {
        toast.error('Failed to load workflow rules');
      }
    } catch {
      toast.error('Failed to load workflow rules');
    } finally {
      setLoading(false);
    }
  }, [filterModule, filterTrigger, search]);

  useEffect(() => {
    loadModules();
  }, [loadModules]);
  useEffect(() => {
    loadRules();
  }, [loadRules]);

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  async function handleToggle(rule) {
    const ruleId = rule.rule_id ?? rule.id;
    const originalEnabled = rule.enabled;
    setRules((rs) =>
      rs.map((r) => ((r.rule_id ?? r.id) === ruleId ? { ...r, enabled: !r.enabled } : r)),
    );
    const res = await api.patch(`${API}/api/admin/workflow-rules/${ruleId}/toggle`);
    if (res.ok) {
      const data = await res.json();
      setRules((rs) =>
        rs.map((r) => ((r.rule_id ?? r.id) === ruleId ? { ...r, enabled: data.enabled } : r)),
      );
    } else {
      setRules((rs) =>
        rs.map((r) => ((r.rule_id ?? r.id) === ruleId ? { ...r, enabled: originalEnabled } : r)),
      );
      toast.error('Failed to toggle rule');
    }
  }

  function openDeleteConfirm(rule) {
    setConfirmState({ open: true, ruleId: rule.rule_id ?? rule.id, name: rule.name });
  }

  async function handleDelete() {
    const { ruleId } = confirmState;
    if (!ruleId) return;
    setConfirmState((s) => ({ ...s, open: false }));
    const res = await api.delete(`${API}/api/admin/workflow-rules/${ruleId}`);
    if (res.ok) {
      toast.success('Rule deleted');
      loadRules();
    } else {
      let message = 'Failed to delete rule';
      try {
        message = (await res.json()).detail ?? message;
      } catch {}
      toast.error(message);
    }
    setConfirmState({ open: false, ruleId: null, name: '' });
  }

  function openCreate() {
    setBuilderRule(null);
    setBuilderOpen(true);
  }

  function openEdit(rule) {
    setBuilderRule(rule);
    setBuilderOpen(true);
  }

  function handleSaved() {
    setBuilderOpen(false);
    setBuilderRule(null);
    loadRules();
  }

  function closeBuilder() {
    setBuilderOpen(false);
    setBuilderRule(null);
  }

  function toggleHistory(ruleId) {
    setHistoryOpen((prev) => ({ ...prev, [ruleId]: !prev[ruleId] }));
  }

  // ---------------------------------------------------------------------------
  // Derived filter options
  // ---------------------------------------------------------------------------

  const moduleFilterOptions = [
    { value: '', label: 'All Modules' },
    ...modules.map((m) => ({ value: m.name, label: m.label ?? humanize(m.name) })),
  ];

  const triggerFilterOptions = [{ value: '', label: 'All Triggers' }, ...TRIGGER_TYPES];

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="space-y-5" data-testid="workflow-rules-admin-page">
      {/* Page header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="page-title flex items-center gap-2">
            <Zap size={22} className="text-violet-600 dark:text-violet-400" />
            Workflow Rules
          </h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">
            Automate actions in response to record changes, field updates, dates, and schedules
          </p>
        </div>
        <button
          onClick={openCreate}
          className="btn-primary flex items-center justify-center gap-2 w-full sm:w-auto shrink-0"
          data-testid="new-rule-btn"
        >
          <Plus size={16} />
          New Rule
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row sm:items-center gap-3 sm:flex-wrap">
        <div className="relative w-full sm:w-auto">
          <ChevronDown
            size={14}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-aegisaxiom-text-secondary pointer-events-none"
          />
          <select
            value={filterModule}
            onChange={(e) => setFilterModule(e.target.value)}
            className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg px-3 py-2 text-sm text-aegisaxiom-text
              focus:border-violet-500 focus:outline-none appearance-none pr-8 w-full sm:w-auto"
            aria-label="Filter by module"
          >
            {moduleFilterOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
        <div className="relative w-full sm:w-auto">
          <ChevronDown
            size={14}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-aegisaxiom-text-secondary pointer-events-none"
          />
          <select
            value={filterTrigger}
            onChange={(e) => setFilterTrigger(e.target.value)}
            className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg px-3 py-2 text-sm text-aegisaxiom-text
              focus:border-violet-500 focus:outline-none appearance-none pr-8 w-full sm:w-auto"
            aria-label="Filter by trigger type"
          >
            {triggerFilterOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
        <div className="relative w-full sm:flex-1 sm:max-w-xs">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted pointer-events-none"
          />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search rules..."
            aria-label="Search rules"
            className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg pl-9 pr-3 py-2 text-sm text-aegisaxiom-text
              focus:border-violet-500 focus:outline-none w-full placeholder-aegisaxiom-text-muted"
          />
        </div>
        {total > 0 && (
          <span className="text-xs text-aegisaxiom-text-muted sm:ml-auto">
            {total} {total === 1 ? 'rule' : 'rules'}
          </span>
        )}
      </div>

      {/* Table */}
      <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-xl overflow-hidden">
        {loading ? (
          <div className="p-16 text-center">
            <div className="w-6 h-6 border-2 border-violet-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
            <p className="text-aegisaxiom-text-muted text-sm">Loading rules...</p>
          </div>
        ) : rules.length === 0 ? (
          <div className="p-16 text-center">
            <Zap size={40} className="text-aegisaxiom-text-muted mx-auto mb-3" />
            <p className="text-aegisaxiom-text-secondary font-medium mb-1">No workflow rules found</p>
            <p className="text-aegisaxiom-text-muted text-sm mb-4">
              {search || filterModule || filterTrigger
                ? 'Try adjusting your filters.'
                : 'Create your first rule to start automating workflows.'}
            </p>
            {!search && !filterModule && !filterTrigger && (
              <button
                onClick={openCreate}
                className="bg-violet-600 hover:bg-violet-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
              >
                <Plus size={14} className="inline mr-1.5 -mt-0.5" />
                New Rule
              </button>
            )}
          </div>
        ) : (
          <>
            {/* Desktop table */}
            <div className="hidden md:block overflow-x-auto">
              <table className="w-full text-sm" role="table">
                <thead>
                  <tr className="border-b border-aegisaxiom-border text-xs text-aegisaxiom-text-muted uppercase tracking-wider">
                    <th className="text-left px-4 py-3 font-medium">Name</th>
                    <th className="text-left px-4 py-3 font-medium">Module</th>
                    <th className="text-left px-4 py-3 font-medium">Trigger</th>
                    <th className="text-left px-4 py-3 font-medium">Actions</th>
                    <th className="text-left px-4 py-3 font-medium">Status</th>
                    <th className="hidden lg:table-cell text-left px-4 py-3 font-medium">
                      Last Run
                    </th>
                    <th className="text-right px-4 py-3 font-medium">
                      <span className="sr-only">Row actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rules.map((rule) => {
                    const ruleId = rule.rule_id ?? rule.id;
                    const actionCount =
                      (rule.instant_actions?.length ?? 0) + (rule.scheduled_actions?.length ?? 0);
                    const isHistoryOpen = !!historyOpen[ruleId];

                    return (
                      <React.Fragment key={ruleId}>
                        <tr
                          className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted transition-colors"
                          data-testid={`rule-row-${ruleId}`}
                        >
                          {/* Name */}
                          <td className="px-4 py-3">
                            <p className="font-semibold text-aegisaxiom-text">{rule.name}</p>
                            {rule.description && (
                              <p className="text-xs text-aegisaxiom-text-muted mt-0.5 max-w-xs truncate">
                                {rule.description}
                              </p>
                            )}
                          </td>

                          {/* Module */}
                          <td className="px-4 py-3">
                            <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300 border border-violet-200 dark:border-violet-700/40">
                              {humanize(rule.module ?? '—')}
                            </span>
                          </td>

                          {/* Trigger */}
                          <td className="px-4 py-3">
                            <span className="text-aegisaxiom-text-secondary text-xs font-medium">
                              {humanize(rule.trigger?.type ?? '—')}
                            </span>
                            {rule.trigger?.action && (
                              <span className="ml-1.5 text-aegisaxiom-text-muted text-xs font-mono">
                                {rule.trigger.action}
                              </span>
                            )}
                            {rule.trigger?.watch_field && (
                              <span className="ml-1.5 text-aegisaxiom-text-muted text-xs font-mono">
                                {rule.trigger.watch_field}
                              </span>
                            )}
                            {rule.trigger?.cron_expression && (
                              <span className="ml-1.5 text-aegisaxiom-text-muted text-xs font-mono">
                                {rule.trigger.cron_expression}
                              </span>
                            )}
                          </td>

                          {/* Action count */}
                          <td className="px-4 py-3">
                            {actionCount === 0 ? (
                              <span className="text-aegisaxiom-text-muted text-xs italic">None</span>
                            ) : (
                              <span className="text-aegisaxiom-text-secondary text-xs">
                                {actionCount} action{actionCount !== 1 ? 's' : ''}
                              </span>
                            )}
                          </td>

                          {/* Status toggle */}
                          <td className="px-4 py-3">
                            <Toggle enabled={!!rule.enabled} onChange={() => handleToggle(rule)} />
                          </td>

                          {/* Last run */}
                          <td className="hidden lg:table-cell px-4 py-3">
                            <span className="text-aegisaxiom-text-muted text-xs">
                              {rule.last_run_at ? formatTimestamp(rule.last_run_at) : '—'}
                            </span>
                          </td>

                          {/* Row actions */}
                          <td className="px-4 py-3">
                            <div className="flex items-center justify-end gap-1">
                              <button
                                type="button"
                                onClick={() => toggleHistory(ruleId)}
                                className={`p-1.5 rounded transition-colors ${
                                  isHistoryOpen
                                    ? 'text-violet-600 dark:text-violet-400 bg-violet-50 dark:bg-violet-500/10'
                                    : 'text-aegisaxiom-text-muted hover:text-violet-600 dark:text-violet-400'
                                }`}
                                aria-label="View execution history"
                                title="History"
                              >
                                <History size={15} />
                              </button>
                              <button
                                type="button"
                                onClick={() => openEdit(rule)}
                                className="p-1.5 rounded text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary transition-colors"
                                aria-label="Edit rule"
                                title="Edit"
                              >
                                <Edit2 size={15} />
                              </button>
                              <button
                                type="button"
                                onClick={() => openDeleteConfirm(rule)}
                                className="p-1.5 rounded text-aegisaxiom-text-muted hover:text-red-600 dark:hover:text-red-400 transition-colors"
                                aria-label="Delete rule"
                                title="Delete"
                              >
                                <Trash2 size={15} />
                              </button>
                            </div>
                          </td>
                        </tr>

                        {/* Inline execution history panel */}
                        {isHistoryOpen && (
                          <tr key={`${ruleId}-history`}>
                            <td colSpan={7} className="p-0">
                              <ExecutionHistoryPanel
                                ruleId={ruleId}
                                onClose={() => toggleHistory(ruleId)}
                              />
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Mobile card list */}
            <div className="md:hidden divide-y divide-aegisaxiom-border">
              {rules.map((rule) => {
                const ruleId = rule.rule_id ?? rule.id;
                const actionCount =
                  (rule.instant_actions?.length ?? 0) + (rule.scheduled_actions?.length ?? 0);
                const isHistoryOpen = !!historyOpen[ruleId];

                return (
                  <React.Fragment key={ruleId}>
                    <div className="p-4" data-testid={`rule-card-${ruleId}`}>
                      {/* Header: name + status toggle */}
                      <div className="flex items-start justify-between gap-3 mb-2">
                        <div className="min-w-0 flex-1">
                          <p className="font-semibold text-aegisaxiom-text text-sm leading-tight">
                            {rule.name}
                          </p>
                          {rule.description && (
                            <p className="text-xs text-aegisaxiom-text-muted mt-1 leading-snug">
                              {rule.description}
                            </p>
                          )}
                        </div>
                        <div className="shrink-0 pt-0.5">
                          <Toggle enabled={!!rule.enabled} onChange={() => handleToggle(rule)} />
                        </div>
                      </div>

                      {/* Module pill + trigger info */}
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 mb-2.5">
                        <span className="px-2 py-0.5 rounded-full text-[11px] font-medium bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300 border border-violet-200 dark:border-violet-700/40">
                          {humanize(rule.module ?? '—')}
                        </span>
                        <span className="text-aegisaxiom-text-secondary text-xs font-medium">
                          {humanize(rule.trigger?.type ?? '—')}
                        </span>
                        {rule.trigger?.action && (
                          <span className="text-aegisaxiom-text-muted text-[11px] font-mono truncate">
                            {rule.trigger.action}
                          </span>
                        )}
                        {rule.trigger?.watch_field && (
                          <span className="text-aegisaxiom-text-muted text-[11px] font-mono truncate">
                            {rule.trigger.watch_field}
                          </span>
                        )}
                        {rule.trigger?.cron_expression && (
                          <span className="text-aegisaxiom-text-muted text-[11px] font-mono truncate">
                            {rule.trigger.cron_expression}
                          </span>
                        )}
                      </div>

                      {/* Action count + last run */}
                      <div className="flex items-center gap-3 text-xs text-aegisaxiom-text-muted mb-3">
                        <span className="inline-flex items-center gap-1">
                          <Zap size={12} className="opacity-70" />
                          {actionCount === 0
                            ? 'No actions'
                            : `${actionCount} action${actionCount !== 1 ? 's' : ''}`}
                        </span>
                        <span aria-hidden="true" className="text-aegisaxiom-text-muted/50">
                          ·
                        </span>
                        <span className="inline-flex items-center gap-1 min-w-0">
                          <Clock size={12} className="opacity-70 shrink-0" />
                          <span className="truncate">
                            {rule.last_run_at ? formatTimestamp(rule.last_run_at) : 'Never run'}
                          </span>
                        </span>
                      </div>

                      {/* Row actions */}
                      <div className="flex items-center justify-end gap-1 pt-2 border-t border-aegisaxiom-border">
                        <button
                          type="button"
                          onClick={() => toggleHistory(ruleId)}
                          className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
                            isHistoryOpen
                              ? 'text-violet-700 dark:text-violet-300 bg-violet-50 dark:bg-violet-500/10'
                              : 'text-aegisaxiom-text-secondary hover:text-violet-700 dark:hover:text-violet-300 hover:bg-aegisaxiom-accent-muted'
                          }`}
                          aria-label="View execution history"
                        >
                          <History size={13} />
                          History
                        </button>
                        <button
                          type="button"
                          onClick={() => openEdit(rule)}
                          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-accent-muted transition-colors"
                          aria-label="Edit rule"
                        >
                          <Edit2 size={13} />
                          Edit
                        </button>
                        <button
                          type="button"
                          onClick={() => openDeleteConfirm(rule)}
                          className="inline-flex items-center justify-center w-8 h-8 rounded-md text-aegisaxiom-text-muted hover:text-red-600 dark:hover:text-red-400 hover:bg-aegisaxiom-accent-muted transition-colors"
                          aria-label="Delete rule"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>

                    {/* Inline execution history panel — mobile */}
                    {isHistoryOpen && (
                      <div className="bg-aegisaxiom-base">
                        <ExecutionHistoryPanel
                          ruleId={ruleId}
                          onClose={() => toggleHistory(ruleId)}
                        />
                      </div>
                    )}
                  </React.Fragment>
                );
              })}
            </div>
          </>
        )}
      </div>

      {/* Builder modal */}
      {builderOpen && (
        <WorkflowModal
          rule={builderRule}
          modules={modules}
          allRules={rules}
          onSave={handleSaved}
          onClose={closeBuilder}
        />
      )}

      {/* Confirm delete */}
      <ConfirmModal
        isOpen={confirmState.open}
        title="Delete Workflow Rule"
        message={`Are you sure you want to delete "${confirmState.name}"? This cannot be undone.`}
        confirmText="Delete"
        variant="danger"
        onConfirm={handleDelete}
        onCancel={() => setConfirmState({ open: false, ruleId: null, name: '' })}
      />
    </div>
  );
}
