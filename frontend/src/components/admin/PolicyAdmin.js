import React, { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import {
  AlertTriangle,
  ChevronDown,
  Edit2,
  Lock,
  Plus,
  Search,
  Shield,
  Trash2,
  X,
  Zap,
} from 'lucide-react';
import { toast } from 'sonner';
import API_URL from '../../lib/apiConfig';
import ConfirmModal from '../shared/ConfirmModal';
import SearchableSelect from '../shared/SearchableSelect';

const API = API_URL;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Fallback events used when a native target has no events list from the API.
const FALLBACK_TRIGGER_EVENTS = [
  { value: 'on_submit', label: 'On Submit' },
  { value: 'on_create', label: 'On Create' },
  { value: 'on_update', label: 'On Update' },
  { value: 'on_delete', label: 'On Delete' },
  { value: 'on_status_change', label: 'On Status Change' },
];

const ACTION_TYPES = [
  { value: 'block', label: 'Block' },
  { value: 'warn', label: 'Warn' },
  { value: 'require_field', label: 'Require Field' },
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

const EMPTY_POLICY = {
  name: '',
  description: '',
  module: '',
  entity: '',
  trigger: { event: 'on_submit' },
  condition_logic: 'AND',
  condition_pattern: '',
  conditions: [],
  actions: [],
  applies_to_roles: [],
  priority: 10,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function humanize(str) {
  return str.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function fieldTypeFor(fieldName, fieldDefs) {
  const def = fieldDefs.find((f) => f.name === fieldName);
  return def?.type ?? 'string';
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

function ActionBadge({ type }) {
  const styles = {
    block:
      'bg-red-50 dark:bg-red-900/40 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-700/40',
    warn: 'bg-amber-50 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-700/40',
    require_field:
      'bg-blue-50 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-700/40',
  };
  return (
    <span
      className={`px-2 py-0.5 rounded-full text-xs font-medium ${styles[type] ?? 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border'}`}
    >
      {humanize(type)}
    </span>
  );
}

function ModuleBadge({ module, entity }) {
  return (
    <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300 border border-violet-200 dark:border-violet-700/40">
      {humanize(module)} / {humanize(entity)}
    </span>
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

function InputField({ label, value, onChange, placeholder, type = 'text', required, disabled }) {
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
        className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg px-3 py-2 text-aegisaxiom-text text-sm
          focus:border-violet-500 focus:outline-none w-full placeholder-aegisaxiom-text-muted
          disabled:opacity-50 disabled:cursor-not-allowed"
      />
    </div>
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
    label: f.computed ? `${f.label} (computed)` : f.label,
    subtitle: f.description || undefined,
  }));

  const operatorOptions = operators.map((op) => ({
    value: op,
    label: OPERATOR_LABELS[op] ?? humanize(op),
  }));

  // Determine value input type
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
        {/* Field */}
        <div className="col-span-4">
          <SearchableSelect
            value={condition.field}
            onChange={handleFieldChange}
            options={fieldOptions}
            placeholder="Select field..."
          />
        </div>
        {/* Operator */}
        <div className="col-span-4">
          <SelectField
            value={condition.operator}
            onChange={handleOperatorChange}
            options={operatorOptions}
            placeholder="Operator..."
            disabled={!condition.field}
          />
        </div>
        {/* Value */}
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
// Action Row
// ---------------------------------------------------------------------------

function ActionRow({ action, index, fieldDefs, onChange, onRemove }) {
  const fieldOptions = fieldDefs.map((f) => ({ value: f.name, label: f.label }));
  const showMessage = action.type === 'block' || action.type === 'warn';
  const showField = action.type === 'require_field';

  return (
    <div className="flex items-start gap-2 p-3 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg">
      <div className="flex-1 grid grid-cols-12 gap-2 min-w-0">
        {/* Type */}
        <div className="col-span-3">
          <SelectField
            value={action.type}
            onChange={(val) => onChange(index, { ...action, type: val, message: '', field: null })}
            options={ACTION_TYPES}
            placeholder="Type..."
          />
        </div>
        {/* Message / Field */}
        <div className="col-span-9">
          {showMessage && (
            <input
              type="text"
              value={action.message ?? ''}
              onChange={(e) => onChange(index, { ...action, message: e.target.value })}
              placeholder="Message shown to user..."
              className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg px-3 py-2 text-aegisaxiom-text text-sm
                focus:border-violet-500 focus:outline-none w-full placeholder-aegisaxiom-text-muted"
            />
          )}
          {showField && (
            <SelectField
              value={action.field ?? ''}
              onChange={(val) => onChange(index, { ...action, field: val })}
              options={fieldOptions}
              placeholder="Select required field..."
            />
          )}
        </div>
      </div>
      <button
        type="button"
        onClick={() => onRemove(index)}
        className="text-aegisaxiom-text-muted hover:text-red-600 dark:text-red-400 transition-colors p-1.5 rounded mt-0.5 shrink-0"
        aria-label="Remove action"
      >
        <X size={14} />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Policy Builder Modal
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Target selection helpers
// ---------------------------------------------------------------------------

/**
 * Build the unified target options list from policyTargets (API) with a
 * fallback to modulesMap (native field registry) when the backend endpoint
 * is not yet wired.
 *
 * Option value: "module:entity" composite key.
 * Option label: the `label` field from the API, or a humanized fallback.
 * Grouped: { "Native Modules": [...values], "Ingested Apps": [...values] }
 */
function buildTargetOptions(policyTargets, modulesMap) {
  // Prefer the rich API response when available.
  if (policyTargets.length > 0) {
    const options = policyTargets.map((t) => ({
      value: `${t.module}:${t.entity}`,
      label: t.label ?? `${humanize(t.module)} / ${humanize(t.entity)}`,
      subtitle: t.source === 'ingestion' ? 'Ingested App' : undefined,
      module: t.module,
      entity: t.entity,
      events: t.events ?? [],
      source: t.source,
    }));
    const grouped = {
      'Native Modules': options.filter((o) => o.source !== 'ingestion').map((o) => o.value),
      'Ingested Apps': options.filter((o) => o.source === 'ingestion').map((o) => o.value),
    };
    return { options, grouped };
  }

  // Fallback: derive from the native-only modulesMap.
  const options = [];
  for (const [module, info] of Object.entries(modulesMap)) {
    const entities = Array.isArray(info) ? info : (info?.entities ?? []);
    for (const entity of entities) {
      options.push({
        value: `${module}:${entity}`,
        label: `${humanize(module)} / ${humanize(entity)}`,
        module,
        entity,
        events: [],
        source: 'native',
      });
    }
  }
  const grouped = {
    'Native Modules': options.map((o) => o.value),
    'Ingested Apps': [],
  };
  return { options, grouped };
}

function PolicyModal({ policy, modulesMap, policyTargets = [], roleOptions = [], onSave, onClose }) {
  const isEditing = !!policy?.policy_id;

  // Encode the pre-selected module+entity into a composite target key so that
  // editing an existing policy correctly pre-selects the target dropdown.
  const initialTargetKey =
    policy?.module && policy?.entity ? `${policy.module}:${policy.entity}` : '';

  const [form, setForm] = useState(() => ({
    ...EMPTY_POLICY,
    ...(policy ?? {}),
    trigger: policy?.trigger ?? { event: 'on_submit' },
    conditions: policy?.conditions ? [...policy.conditions] : [],
    actions: policy?.actions ? [...policy.actions] : [],
    applies_to_roles: policy?.applies_to_roles ?? [],
  }));
  // Separate piece of state just for the target composite key — keeps form
  // shape compatible with existing save logic (still writes module/entity).
  const [selectedTargetKey, setSelectedTargetKey] = useState(initialTargetKey);

  const [fieldDefs, setFieldDefs] = useState([]);
  const [loadingFields, setLoadingFields] = useState(false);
  const [saving, setSaving] = useState(false);

  const { options: targetOptions, grouped: targetGrouped } = buildTargetOptions(
    policyTargets,
    modulesMap,
  );
  const hasIngestedApps = (targetGrouped['Ingested Apps'] ?? []).length > 0;

  // The currently selected target object (drives event list).
  const selectedTarget = targetOptions.find((o) => o.value === selectedTargetKey);
  const triggerEventOptions =
    selectedTarget?.events?.length > 0
      ? selectedTarget.events.map((ev) => ({
          value: ev,
          label: humanize(ev),
        }))
      : FALLBACK_TRIGGER_EVENTS;

  const fetchFieldDefs = useCallback(async (module, entity) => {
    if (!module || !entity) {
      setFieldDefs([]);
      return;
    }
    setLoadingFields(true);
    try {
      const res = await api.get(`${API}/api/admin/field-definitions/${module}/${entity}`);
      if (res.ok) {
        const data = await res.json();
        setFieldDefs(data.fields ?? []);
      }
    } catch {
      // silently fail — builder still usable without field hints
    } finally {
      setLoadingFields(false);
    }
  }, []);

  useEffect(() => {
    if (form.module && form.entity) {
      fetchFieldDefs(form.module, form.entity);
    }
  }, [form.module, form.entity, fetchFieldDefs]);

  function setField(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function handleTargetChange(compositeKey) {
    setSelectedTargetKey(compositeKey);
    const [module = '', entity = ''] = compositeKey.split(':');
    // Reset conditions/actions when scope changes; pick first available event.
    const target = targetOptions.find((o) => o.value === compositeKey);
    const firstEvent =
      target?.events?.length > 0 ? target.events[0] : FALLBACK_TRIGGER_EVENTS[0].value;
    setForm((prev) => ({
      ...prev,
      module,
      entity,
      conditions: [],
      actions: [],
      trigger: { event: firstEvent },
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

  // Actions
  function addAction() {
    setForm((prev) => ({
      ...prev,
      actions: [...prev.actions, { type: 'block', message: '', field: null }],
    }));
  }

  function updateAction(idx, updated) {
    setForm((prev) => {
      const actions = [...prev.actions];
      actions[idx] = updated;
      return { ...prev, actions };
    });
  }

  function removeAction(idx) {
    setForm((prev) => ({ ...prev, actions: prev.actions.filter((_, i) => i !== idx) }));
  }

  // Role targeting
  function toggleRole(role) {
    setForm((prev) => {
      const roles = prev.applies_to_roles || [];
      return {
        ...prev,
        applies_to_roles: roles.includes(role) ? roles.filter((r) => r !== role) : [...roles, role],
      };
    });
  }

  async function handleSave() {
    if (!form.name.trim()) {
      toast.error('Policy name is required');
      return;
    }
    if (!selectedTargetKey || !form.module || !form.entity) {
      toast.error('Target (module / entity) is required');
      return;
    }
    if (form.condition_logic === 'CUSTOM' && !form.condition_pattern?.trim()) {
      toast.error('Custom condition pattern is required (e.g., "(1 AND 2) OR 3")');
      return;
    }

    const payload = {
      name: form.name.trim(),
      description: form.description ?? '',
      module: form.module,
      entity: form.entity,
      trigger: form.trigger,
      condition_logic: form.condition_logic,
      condition_pattern: form.condition_logic === 'CUSTOM' ? form.condition_pattern : null,
      conditions: form.conditions,
      actions: form.actions,
      applies_to_roles: form.applies_to_roles ?? [],
      priority: Number(form.priority) || 10,
    };

    setSaving(true);
    try {
      let res;
      if (isEditing) {
        res = await api.put(`${API}/api/admin/policies/${policy.policy_id}`, payload);
      } else {
        res = await api.post(`${API}/api/admin/policies`, payload);
      }
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail ?? 'Failed to save policy');
      }
      const saved = await res.json();
      toast.success(isEditing ? 'Policy updated' : 'Policy created');
      onSave(saved);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="policy-modal-title"
    >
      {/* Backdrop */}
      <div className="absolute inset-0" onClick={onClose} aria-hidden="true" />

      <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border rounded-2xl w-full max-w-3xl max-h-[90vh] overflow-y-auto">
        {/* Modal header */}
        <div className="sticky top-0 bg-aegisaxiom-surface border-b border-aegisaxiom-border px-6 py-4 flex items-center justify-between z-10">
          <h2
            id="policy-modal-title"
            className="text-lg font-bold text-aegisaxiom-text"
            style={{ fontFamily: 'Manrope, sans-serif' }}
          >
            {isEditing ? 'Edit Policy' : 'New Policy'}
          </h2>
          <button
            onClick={onClose}
            className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition-colors"
            aria-label="Close modal"
          >
            <X size={20} />
          </button>
        </div>

        <div className="p-6 space-y-6">
          {/* System policy warning */}
          {isEditing && policy?.is_system && (
            <div className="flex items-start gap-3 bg-amber-950/40 border border-amber-200 dark:border-amber-700/50 rounded-lg px-4 py-3">
              <AlertTriangle
                size={18}
                className="text-amber-600 dark:text-amber-400 mt-0.5 flex-shrink-0"
              />
              <div className="text-sm">
                <p className="text-amber-700 dark:text-amber-300 font-medium">System Policy</p>
                <p className="text-amber-600 dark:text-amber-400/80 mt-0.5">
                  This is a default system policy. Changes may be reset if the system is re-seeded.
                  Editing requires the &quot;Manage System Policies&quot; permission.
                </p>
              </div>
            </div>
          )}
          {/* Basic info */}
          <div className="space-y-4">
            <InputField
              label="Policy Name"
              value={form.name}
              onChange={(v) => setField('name', v)}
              placeholder="e.g. Max 4 Hours Per Time Entry"
              required
            />
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
                Description
              </label>
              <textarea
                value={form.description ?? ''}
                onChange={(e) => setField('description', e.target.value)}
                placeholder="Optional description of what this policy enforces..."
                rows={2}
                className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg px-3 py-2 text-aegisaxiom-text text-sm
                  focus:border-violet-500 focus:outline-none w-full placeholder-aegisaxiom-text-muted resize-none"
              />
            </div>
          </div>

          {/* Target / Trigger */}
          <div>
            <p className="text-xs font-semibold text-aegisaxiom-text-secondary uppercase tracking-wider mb-3">
              Scope &amp; Trigger
            </p>
            <div className="grid grid-cols-2 gap-4">
              <SearchableSelect
                label="Target (Module / Entity)"
                value={selectedTargetKey}
                onChange={handleTargetChange}
                options={targetOptions}
                placeholder="Select target..."
                required
                grouped={targetGrouped}
              />
              <SelectField
                label="Trigger Event"
                value={form.trigger?.event ?? triggerEventOptions[0]?.value ?? 'on_submit'}
                onChange={(v) => setField('trigger', { event: v })}
                options={triggerEventOptions}
                disabled={!selectedTargetKey}
              />
            </div>
            {/* Empty-state notice when no ingested apps are registered */}
            {!hasIngestedApps && (
              <p className="text-xs text-aegisaxiom-text-muted mt-2">
                No ingested apps registered. Run{' '}
                <span className="font-mono text-violet-500">/ingest-app</span> to add one.
              </p>
            )}
            {loadingFields && (
              <p className="text-xs text-aegisaxiom-text-muted mt-2">Loading field definitions...</p>
            )}
          </div>

          {/* Role targeting */}
          <div>
            <p className="text-xs font-semibold text-aegisaxiom-text-secondary uppercase tracking-wider mb-3">
              Applies to Roles
            </p>
            <div className="flex flex-wrap items-center gap-2 mb-2">
              {roleOptions.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => toggleRole(opt.value)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors border
                    ${
                      (form.applies_to_roles || []).includes(opt.value)
                        ? 'bg-violet-50 dark:bg-violet-600/20 border-violet-500/50 text-violet-700 dark:text-violet-300'
                        : 'bg-aegisaxiom-elevated border-aegisaxiom-border-hover text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover hover:text-aegisaxiom-text-secondary'
                    }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <p className="text-xs text-aegisaxiom-text-muted">
              {!form.applies_to_roles || form.applies_to_roles.length === 0
                ? 'No roles selected \u2014 this policy applies to all users.'
                : `Applies to: ${form.applies_to_roles.map((r) => roleOptions.find((o) => o.value === r)?.label ?? r).join(', ')}`}
            </p>
          </div>

          {/* Condition logic */}
          <div>
            <p className="text-xs font-semibold text-aegisaxiom-text-secondary uppercase tracking-wider mb-3">
              Conditions
            </p>
            <div className="flex items-center gap-3 mb-3">
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
              <div className="mb-3">
                <InputField
                  label="Pattern (e.g. (1 AND 2) OR 3)"
                  value={form.condition_pattern ?? ''}
                  onChange={(v) => setField('condition_pattern', v)}
                  placeholder="(1 AND 2) OR 3"
                />
              </div>
            )}
            <div className="space-y-2">
              {form.conditions.map((cond, idx) => (
                <ConditionRow
                  key={idx}
                  condition={cond}
                  index={idx}
                  fieldDefs={fieldDefs}
                  onChange={updateCondition}
                  onRemove={removeCondition}
                />
              ))}
            </div>
            <button
              type="button"
              onClick={addCondition}
              disabled={!form.entity}
              className="mt-2 flex items-center gap-1.5 text-sm text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300
                disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <Plus size={14} />
              Add Condition
            </button>
            {!form.entity && (
              <p className="text-xs text-aegisaxiom-text-muted mt-1">Select a module and entity first</p>
            )}
          </div>

          {/* Actions */}
          <div>
            <p className="text-xs font-semibold text-aegisaxiom-text-secondary uppercase tracking-wider mb-3">
              Actions
            </p>
            <div className="space-y-2">
              {form.actions.map((action, idx) => (
                <ActionRow
                  key={idx}
                  action={action}
                  index={idx}
                  fieldDefs={fieldDefs}
                  onChange={updateAction}
                  onRemove={removeAction}
                />
              ))}
            </div>
            <button
              type="button"
              onClick={addAction}
              disabled={!form.entity}
              className="mt-2 flex items-center gap-1.5 text-sm text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300
                disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <Plus size={14} />
              Add Action
            </button>
          </div>

          {/* Priority */}
          <div className="grid grid-cols-3">
            <InputField
              label="Priority (lower = higher priority)"
              value={String(form.priority ?? 10)}
              onChange={(v) => setField('priority', v)}
              type="number"
            />
          </div>
        </div>

        {/* Modal footer */}
        <div className="sticky bottom-0 bg-aegisaxiom-surface border-t border-aegisaxiom-border px-6 py-4 flex items-center justify-end gap-3">
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
            {saving ? 'Saving...' : 'Save Policy'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function PolicyAdmin() {
  const [policies, setPolicies] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [modulesMap, setModulesMap] = useState({});
  const [policyTargets, setPolicyTargets] = useState([]);
  const [roleOptions, setRoleOptions] = useState([]);

  // Filters
  const [filterModule, setFilterModule] = useState('');
  const [search, setSearch] = useState('');

  // Modal state
  const [builderPolicy, setBuilderPolicy] = useState(null); // null = closed, {} = new, policy obj = edit
  const [builderOpen, setBuilderOpen] = useState(false);

  // Confirm delete
  const [confirmState, setConfirmState] = useState({
    open: false,
    policyId: null,
    name: '',
    isSystemWarning: false,
  });

  // ---------------------------------------------------------------------------
  // Data loading
  // ---------------------------------------------------------------------------

  const loadModules = useCallback(async () => {
    try {
      const res = await api.get(`${API}/api/admin/field-definitions/modules`);
      if (res.ok) {
        const data = await res.json();
        setModulesMap(data.modules ?? {});
      } else {
        toast.error('Failed to load modules');
      }
    } catch {
      toast.error('Failed to load modules');
    }
  }, []);

  // TODO(backend-gap): This calls GET /api/admin/policy-targets which does not yet exist.
  // The backend needs to expose list_targets_for_admin_ui() from services/policy_engine.py
  // as a route in backend/routes/policies.py (e.g. GET /policy-targets returning
  // [{ module, entity, events, source, label }]). Until that endpoint is wired, this
  // fetch silently falls back to the native-only modulesMap loaded above.
  const loadPolicyTargets = useCallback(async () => {
    try {
      const res = await api.get(`${API}/api/admin/policy-targets`);
      if (res.ok) {
        const data = await res.json();
        setPolicyTargets(data.targets ?? []);
      }
      // Non-OK is intentionally silent — fallback to modulesMap-derived targets.
    } catch {
      // Endpoint not yet wired; fall through to modulesMap fallback.
    }
  }, []);

  const loadRoles = useCallback(async () => {
    try {
      const res = await api.get(`${API}/api/admin/policy-options/roles`);
      if (res.ok) {
        const data = await res.json();
        setRoleOptions(data);
      }
    } catch {
      // silently fail — role targeting still usable without dynamic roles
    }
  }, []);

  const loadPolicies = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filterModule) params.set('module', filterModule);
      if (search.trim()) params.set('search', search.trim());
      const qs = params.toString() ? `?${params.toString()}` : '';
      const res = await api.get(`${API}/api/admin/policies${qs}`);
      if (res.ok) {
        const data = await res.json();
        setPolicies(data.items ?? []);
        setTotal(data.total ?? 0);
      } else {
        toast.error('Failed to load policies');
      }
    } finally {
      setLoading(false);
    }
  }, [filterModule, search]);

  useEffect(() => {
    loadModules();
  }, [loadModules]);
  useEffect(() => {
    loadPolicyTargets();
  }, [loadPolicyTargets]);
  useEffect(() => {
    loadRoles();
  }, [loadRoles]);
  useEffect(() => {
    loadPolicies();
  }, [loadPolicies]);

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  async function handleToggle(policy) {
    const originalEnabled = policy.enabled;
    // Optimistic update
    setPolicies((ps) =>
      ps.map((p) => (p.policy_id === policy.policy_id ? { ...p, enabled: !p.enabled } : p)),
    );
    const res = await api.patch(`${API}/api/admin/policies/${policy.policy_id}/toggle`);
    if (res.ok) {
      const data = await res.json();
      setPolicies((ps) =>
        ps.map((p) => (p.policy_id === policy.policy_id ? { ...p, enabled: data.enabled } : p)),
      );
    } else {
      // Rollback only the affected policy, not the entire list
      setPolicies((ps) =>
        ps.map((p) => (p.policy_id === policy.policy_id ? { ...p, enabled: originalEnabled } : p)),
      );
      toast.error('Failed to toggle policy');
    }
  }

  function openDeleteConfirm(policy) {
    setConfirmState({ open: true, policyId: policy.policy_id, name: policy.name });
  }

  async function handleDelete() {
    const { policyId } = confirmState;
    if (!policyId) return;
    setConfirmState((s) => ({ ...s, open: false }));
    const res = await api.delete(`${API}/api/admin/policies/${policyId}`);
    if (res.ok) {
      toast.success('Policy deleted');
      loadPolicies();
    } else {
      let message = 'Failed to delete policy';
      try {
        message = (await res.json()).detail ?? message;
      } catch {}
      toast.error(message);
    }
    setConfirmState({ open: false, policyId: null, name: '', isSystemWarning: false });
  }

  function openCreate() {
    setBuilderPolicy(null);
    setBuilderOpen(true);
  }

  function openEdit(policy) {
    if (policy.is_system) {
      toast.warning(
        'Editing a system policy — changes require the "Manage System Policies" permission.',
      );
    }
    setBuilderPolicy(policy);
    setBuilderOpen(true);
  }

  function handleSaved() {
    setBuilderOpen(false);
    setBuilderPolicy(null);
    loadPolicies();
  }

  function closeBuilder() {
    setBuilderOpen(false);
    setBuilderPolicy(null);
  }

  // ---------------------------------------------------------------------------
  // Derived data for filter dropdowns
  // ---------------------------------------------------------------------------

  const moduleFilterOptions = [
    { value: '', label: 'All Modules' },
    ...Object.keys(modulesMap).map((m) => ({ value: m, label: humanize(m) })),
  ];

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="space-y-5" data-testid="policy-admin-page">
      {/* Page header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="page-title flex items-center gap-2">
            <Zap size={22} className="text-violet-600 dark:text-violet-400" />
            Policy Engine
          </h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">
            Define validation rules, warnings, and field requirements for each module
          </p>
        </div>
        <button
          onClick={openCreate}
          className="btn-primary flex items-center justify-center gap-2 w-full sm:w-auto shrink-0"
          data-testid="new-policy-btn"
        >
          <Plus size={16} />
          New Policy
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
        <div className="relative w-full sm:flex-1 sm:max-w-xs">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted pointer-events-none"
          />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search policies..."
            aria-label="Search policies"
            className="bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg pl-9 pr-3 py-2 text-sm text-aegisaxiom-text
              focus:border-violet-500 focus:outline-none w-full placeholder-aegisaxiom-text-muted"
          />
        </div>
        {total > 0 && (
          <span className="text-xs text-aegisaxiom-text-muted sm:ml-auto">
            {total} {total === 1 ? 'policy' : 'policies'}
          </span>
        )}
      </div>

      {/* Table */}
      <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-xl overflow-hidden">
        {loading ? (
          <div className="p-16 text-center">
            <div className="w-6 h-6 border-2 border-violet-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
            <p className="text-aegisaxiom-text-muted text-sm">Loading policies...</p>
          </div>
        ) : policies.length === 0 ? (
          <div className="p-16 text-center">
            <Shield size={40} className="text-aegisaxiom-text-muted mx-auto mb-3" />
            <p className="text-aegisaxiom-text-secondary font-medium mb-1">No policies found</p>
            <p className="text-aegisaxiom-text-muted text-sm mb-4">
              {search || filterModule
                ? 'Try adjusting your filters.'
                : 'Create your first policy to start enforcing business rules.'}
            </p>
            {!search && !filterModule && (
              <button
                onClick={openCreate}
                className="bg-violet-600 hover:bg-violet-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
              >
                <Plus size={14} className="inline mr-1.5 -mt-0.5" />
                New Policy
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
                    <th className="text-left px-4 py-3 font-medium">Policy</th>
                    <th className="text-left px-4 py-3 font-medium">Module / Entity</th>
                    <th className="text-left px-4 py-3 font-medium">Trigger</th>
                    <th className="text-left px-4 py-3 font-medium">Actions</th>
                    <th className="text-left px-4 py-3 font-medium">Enabled</th>
                    <th className="hidden lg:table-cell text-left px-4 py-3 font-medium w-8">
                      <span className="sr-only">System</span>
                    </th>
                    <th className="text-right px-4 py-3 font-medium">
                      <span className="sr-only">Row actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {policies.map((policy) => (
                    <tr
                      key={policy.policy_id}
                      className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted transition-colors"
                      data-testid={`policy-row-${policy.policy_id}`}
                    >
                      {/* Name + description + role badges */}
                      <td className="px-4 py-3">
                        <p className="font-semibold text-aegisaxiom-text">{policy.name}</p>
                        {policy.description && (
                          <p className="text-xs text-aegisaxiom-text-muted mt-0.5 max-w-xs truncate">
                            {policy.description}
                          </p>
                        )}
                        {policy.applies_to_roles?.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1">
                            {policy.applies_to_roles.map((r) => (
                              <span
                                key={r}
                                className="inline-block px-1.5 py-0.5 rounded text-[10px] font-medium bg-violet-50 dark:bg-violet-600/15 text-violet-600 dark:text-violet-400 border border-violet-500/20"
                              >
                                {roleOptions.find((o) => o.value === r)?.label ?? r}
                              </span>
                            ))}
                          </div>
                        )}
                        {/* Inline System indicator on tablet (where the System column is hidden) */}
                        {policy.is_system && (
                          <span className="lg:hidden inline-flex items-center gap-1 mt-1.5 text-[11px] text-aegisaxiom-text-muted">
                            <Lock size={10} />
                            <span>System</span>
                          </span>
                        )}
                      </td>

                      {/* Module / Entity */}
                      <td className="px-4 py-3">
                        <ModuleBadge module={policy.module} entity={policy.entity} />
                      </td>

                      {/* Trigger */}
                      <td className="px-4 py-3">
                        <span className="text-aegisaxiom-text-secondary text-xs font-mono">
                          {policy.trigger?.event ?? '—'}
                        </span>
                      </td>

                      {/* Action badges */}
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap gap-1">
                          {(policy.actions ?? []).map((action, i) => (
                            <ActionBadge key={i} type={action.type} />
                          ))}
                          {(!policy.actions || policy.actions.length === 0) && (
                            <span className="text-aegisaxiom-text-muted text-xs">—</span>
                          )}
                        </div>
                      </td>

                      {/* Enabled toggle */}
                      <td className="px-4 py-3">
                        <Toggle enabled={policy.enabled} onChange={() => handleToggle(policy)} />
                      </td>

                      {/* System badge */}
                      <td className="hidden lg:table-cell px-4 py-3">
                        {policy.is_system && (
                          <span className="inline-flex items-center gap-1 text-xs text-aegisaxiom-text-muted">
                            <Lock size={11} />
                            <span>System</span>
                          </span>
                        )}
                      </td>

                      {/* Row actions */}
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={() => openEdit(policy)}
                            className="p-1.5 text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 transition-colors rounded"
                            aria-label={`Edit ${policy.name}`}
                          >
                            <Edit2 size={14} />
                          </button>
                          <button
                            onClick={() => {
                              if (policy.is_system) {
                                setConfirmState({
                                  open: true,
                                  policyId: policy.policy_id,
                                  name: policy.name,
                                  isSystemWarning: true,
                                });
                              } else {
                                openDeleteConfirm(policy);
                              }
                            }}
                            className="p-1.5 text-aegisaxiom-text-muted hover:text-red-600 dark:hover:text-red-400 transition-colors rounded"
                            aria-label={`Delete ${policy.name}`}
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mobile card list */}
            <div className="md:hidden divide-y divide-aegisaxiom-border">
              {policies.map((policy) => (
                <div
                  key={policy.policy_id}
                  className="p-4"
                  data-testid={`policy-card-${policy.policy_id}`}
                >
                  {/* Header: name + enabled toggle */}
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <div className="min-w-0 flex-1">
                      <p className="font-semibold text-aegisaxiom-text text-sm leading-tight">
                        {policy.name}
                      </p>
                      {policy.description && (
                        <p className="text-xs text-aegisaxiom-text-muted mt-1 leading-snug">
                          {policy.description}
                        </p>
                      )}
                    </div>
                    <div className="shrink-0 pt-0.5">
                      <Toggle enabled={policy.enabled} onChange={() => handleToggle(policy)} />
                    </div>
                  </div>

                  {/* Module + trigger */}
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 mb-2.5">
                    <ModuleBadge module={policy.module} entity={policy.entity} />
                    <span className="text-aegisaxiom-text-secondary text-xs font-mono">
                      {policy.trigger?.event ?? '—'}
                    </span>
                  </div>

                  {/* Action badges */}
                  {policy.actions?.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-2.5">
                      {policy.actions.map((action, i) => (
                        <ActionBadge key={i} type={action.type} />
                      ))}
                    </div>
                  )}

                  {/* Roles */}
                  {policy.applies_to_roles?.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-2.5">
                      {policy.applies_to_roles.map((r) => (
                        <span
                          key={r}
                          className="inline-block px-1.5 py-0.5 rounded text-[10px] font-medium bg-violet-50 dark:bg-violet-600/15 text-violet-600 dark:text-violet-400 border border-violet-500/20"
                        >
                          {roleOptions.find((o) => o.value === r)?.label ?? r}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Footer: System tag (if any) + actions */}
                  <div className="flex items-center justify-between gap-2 pt-2 border-t border-aegisaxiom-border">
                    {policy.is_system ? (
                      <span className="inline-flex items-center gap-1 text-[11px] text-aegisaxiom-text-muted">
                        <Lock size={10} />
                        <span>System</span>
                      </span>
                    ) : (
                      <span aria-hidden="true" />
                    )}
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => openEdit(policy)}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-accent-muted transition-colors"
                        aria-label={`Edit ${policy.name}`}
                      >
                        <Edit2 size={13} />
                        Edit
                      </button>
                      <button
                        onClick={() => {
                          if (policy.is_system) {
                            setConfirmState({
                              open: true,
                              policyId: policy.policy_id,
                              name: policy.name,
                              isSystemWarning: true,
                            });
                          } else {
                            openDeleteConfirm(policy);
                          }
                        }}
                        className="inline-flex items-center justify-center w-8 h-8 rounded-md text-aegisaxiom-text-muted hover:text-red-600 dark:hover:text-red-400 hover:bg-aegisaxiom-accent-muted transition-colors"
                        aria-label={`Delete ${policy.name}`}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Policy builder modal */}
      {builderOpen && (
        <PolicyModal
          policy={builderPolicy}
          modulesMap={modulesMap}
          policyTargets={policyTargets}
          roleOptions={roleOptions}
          onSave={handleSaved}
          onClose={closeBuilder}
        />
      )}

      {/* Delete confirm */}
      <ConfirmModal
        isOpen={confirmState.open}
        title={confirmState.isSystemWarning ? 'Delete System Policy' : 'Delete Policy'}
        message={
          confirmState.isSystemWarning
            ? `"${confirmState.name}" is a default system policy. Deleting it may affect core business rule enforcement. This requires the "Manage System Policies" permission. Are you sure?`
            : `Delete policy "${confirmState.name}"? This cannot be undone.`
        }
        confirmText="Delete"
        variant="danger"
        onConfirm={handleDelete}
        onCancel={() =>
          setConfirmState({ open: false, policyId: null, name: '', isSystemWarning: false })
        }
      />
    </div>
  );
}
