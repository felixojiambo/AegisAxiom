import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { X, Check, GitBranch, Kanban, Layers } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import DateInput from '@/components/ui/DateInput';

const SERVICE_LINES = [
  { value: 'ucaas', label: 'UCaaS' },
  { value: 'networking', label: 'Networking' },
  { value: 'cloud', label: 'Cloud' },
  { value: 'security', label: 'Security' },
  { value: 'ai_product', label: 'AI Product' },
  { value: 'other', label: 'Other' },
];

// Render an API error response into a string the user can read. Handles:
//   - string detail                       — returned as-is
//   - Pydantic 422 array of {loc, msg}    — joined as 'field.path: msg; …'
//   - object detail with .message         — returned
//   - any other object                    — JSON.stringify fallback
//   - missing detail                      — d.message or 'Failed'
function extractError(d) {
  const detail = d?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((e) => {
        if (typeof e === 'string') return e;
        if (e?.msg) {
          const path = Array.isArray(e?.loc) && e.loc.length > 1
            ? e.loc.slice(1).join('.')
            : null;
          return path ? `${path}: ${e.msg}` : e.msg;
        }
        return null;
      })
      .filter(Boolean);
    if (msgs.length > 0) return msgs.join('; ');
  }
  if (detail?.message) return detail.message;
  if (detail) try { return JSON.stringify(detail); } catch { /* fallthrough */ }
  return d?.message || 'Failed';
}

// ---- ChipInput (port of source ChipInput.tsx) ----

function ChipInput({ values, onChange, placeholder, testid, hint }) {
  const [draft, setDraft] = useState('');
  const inputRef = useRef(null);

  const add = (raw = draft) => {
    const v = (raw ?? '').trim();
    if (!v || values.includes(v)) return false;
    onChange([...values, v]);
    setDraft('');
    return true;
  };

  // Support pasting "A, B, C" — split on commas and add each chip.
  const handlePaste = (e) => {
    const text = e.clipboardData?.getData('text');
    if (text && text.includes(',')) {
      e.preventDefault();
      const parts = text.split(',').map((s) => s.trim()).filter(Boolean);
      const merged = [...values];
      for (const p of parts) if (!merged.includes(p)) merged.push(p);
      onChange(merged);
      setDraft('');
    }
  };

  const remove = (v) => onChange(values.filter((x) => x !== v));

  return (
    <>
      <div
        className="aegisaxiom-input min-h-[40px] flex flex-wrap gap-1.5 p-1.5 cursor-text"
        onClick={() => inputRef.current?.focus()}
        data-testid={testid}
      >
        {values.map((v) => (
          <span key={v} className="inline-flex items-center gap-1 h-6 px-2 rounded bg-violet-500/15 text-violet-400 text-xs font-medium">
            {v}
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); remove(v); }}
              aria-label={`Remove ${v}`}
              className="hover:text-violet-300 transition-colors"
            >
              ×
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onPaste={handlePaste}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(); }
            if (e.key === 'Tab' && draft.trim()) { add(); }
            if (e.key === 'Backspace' && !draft && values.length) {
              remove(values[values.length - 1]);
            }
          }}
          onBlur={() => { if (draft.trim()) add(); }}
          placeholder={values.length === 0 ? placeholder : ''}
          className="flex-1 min-w-[120px] bg-transparent outline-none text-sm text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted"
        />
      </div>
      {hint && (
        <p className="text-[11px] text-aegisaxiom-text-muted mt-1">{hint}</p>
      )}
    </>
  );
}

// ---- Step indicator ----

function StepIndicator({ step }) {
  const labels = ['Methodology', 'Template', 'Details'];
  return (
    <div className="flex items-center px-6 py-4 border-b border-aegisaxiom-border" data-testid="wizard-stepper">
      {labels.map((l, idx) => {
        const n = idx + 1;
        const done = n < step;
        const active = n === step;
        const bg = active
          ? 'bg-violet-600 text-white'
          : done
            ? 'bg-violet-400 text-white'
            : 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted';
        return (
          <div key={l} className="flex items-center flex-1 last:flex-initial">
            <div className="flex items-center gap-2">
              <span className={`w-6 h-6 rounded-full inline-flex items-center justify-center text-[11px] font-semibold ${bg}`}>
                {done ? <Check size={12} /> : n}
              </span>
              <span className={`text-xs font-medium ${active || done ? 'text-aegisaxiom-text' : 'text-aegisaxiom-text-muted'}`}>
                {l}
              </span>
            </div>
            {idx < labels.length - 1 && (
              <span className={`flex-1 h-px mx-3 ${done ? 'bg-violet-400' : 'bg-aegisaxiom-border'}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---- Step 1: Methodology picker ----

function MethodologyCard({ icon, title, desc, selected, onClick, testid }) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testid}
      className={`w-[210px] h-[130px] p-4 rounded-lg flex flex-col items-start justify-between transition-colors ${
        selected
          ? 'border-2 border-violet-500 bg-violet-500/10'
          : 'border border-aegisaxiom-border hover:border-violet-400 bg-aegisaxiom-elevated'
      }`}
    >
      <div className="text-violet-400">{icon}</div>
      <div className="w-full">
        <p className="text-sm font-semibold text-aegisaxiom-text text-left">{title}</p>
        <p className="text-xs text-aegisaxiom-text-muted mt-1 text-left">{desc}</p>
      </div>
    </button>
  );
}

function Step1({ methodology, setMethodology, triedAdvance = false }) {
  return (
    <div>
      <p className="text-sm text-aegisaxiom-text-secondary mb-4">
        Choose the methodology that best fits this project. This cannot be changed after creation.
      </p>
      <div className="flex gap-3 flex-wrap">
        <MethodologyCard
          icon={<GitBranch size={24} />}
          title="Waterfall"
          desc="Phases and milestones. Deliver sequentially."
          selected={methodology === 'waterfall'}
          onClick={() => setMethodology('waterfall')}
          testid="methodology-card-waterfall"
        />
        <MethodologyCard
          icon={<Kanban size={24} />}
          title="Agile (Kanban)"
          desc="Columns and flow. Ship continuously."
          selected={methodology === 'agile'}
          onClick={() => setMethodology('agile')}
          testid="methodology-card-agile"
        />
        <MethodologyCard
          icon={<Layers size={24} />}
          title="Waves & Threads"
          desc="Coordinated waves across module threads."
          selected={methodology === 'waves_threads'}
          onClick={() => setMethodology('waves_threads')}
          testid="methodology-card-waves_threads"
        />
      </div>
      {triedAdvance && methodology === null && (
        <p className="text-xs text-red-400 mt-4" data-testid="wizard-step1-hint">
          Pick a methodology to continue.
        </p>
      )}
    </div>
  );
}

// ---- Step 2: Template picker ----

function Step2({ methodology, templates, templatesLoading, choice, templateId, setChoice, setTemplateId, triedAdvance = false }) {
  const filtered = templates.filter((t) => t.project_type === methodology);
  const hintTone = triedAdvance ? 'text-red-400' : 'text-aegisaxiom-text-muted';
  return (
    <div>
      <div className="grid grid-cols-2 gap-3 mb-4">
        <button
          type="button"
          onClick={() => { setChoice('blank'); setTemplateId(null); }}
          data-testid="template-choice-blank"
          className={`h-16 px-4 rounded-md text-left transition-colors ${
            choice === 'blank' ? 'border-2 border-violet-500 bg-violet-500/10' : 'border border-aegisaxiom-border hover:border-violet-400 bg-aegisaxiom-elevated'
          }`}
        >
          <p className="text-sm font-semibold text-aegisaxiom-text">Start from blank</p>
          <p className="text-xs text-aegisaxiom-text-muted mt-0.5">Define phases / columns yourself.</p>
        </button>
        <button
          type="button"
          onClick={() => setChoice('template')}
          data-testid="template-choice-template"
          className={`h-16 px-4 rounded-md text-left transition-colors ${
            choice === 'template' ? 'border-2 border-violet-500 bg-violet-500/10' : 'border border-aegisaxiom-border hover:border-violet-400 bg-aegisaxiom-elevated'
          }`}
        >
          <p className="text-sm font-semibold text-aegisaxiom-text">Use a template</p>
          <p className="text-xs text-aegisaxiom-text-muted mt-0.5">Pre-seed phases / columns / tasks.</p>
        </button>
      </div>
      {choice === null && (
        <p className={`text-xs ${hintTone}`} data-testid="wizard-step2-hint">
          Pick an option above to continue.
        </p>
      )}
      {choice === 'template' && (
        <div data-testid="template-list">
          {templatesLoading ? (
            <div className="py-6 flex justify-center">
              <div className="animate-spin w-6 h-6 rounded-full border-2 border-violet-500 border-t-transparent" />
            </div>
          ) : filtered.length === 0 ? (
            <p className="text-xs text-aegisaxiom-text-muted py-4">
              No templates for this methodology yet. Pick "Start from blank" above to continue.
            </p>
          ) : (
            <ul className="max-h-[260px] overflow-auto rounded-md border border-aegisaxiom-border">
              {filtered.map((t) => (
                <li key={t.template_id ?? t._id}>
                  <button
                    type="button"
                    onClick={() => setTemplateId(t.template_id ?? t._id)}
                    data-testid={`template-row-${t.template_id ?? t._id}`}
                    className={`w-full text-left px-3 py-2 flex items-center gap-3 border-b last:border-b-0 border-aegisaxiom-border transition-colors ${
                      templateId === (t.template_id ?? t._id) ? 'bg-violet-500/10' : 'hover:bg-aegisaxiom-elevated'
                    }`}
                  >
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-semibold text-aegisaxiom-text truncate">{t.name}</p>
                      <p className="text-[11px] text-aegisaxiom-text-muted capitalize">
                        {t.service_line?.replace(/_/g, ' ')} · {t.task_definitions?.length ?? 0} tasks
                      </p>
                    </div>
                    {t.is_system && (
                      <span className="shrink-0 inline-flex items-center h-5 px-2 rounded bg-violet-500/15 text-violet-400 text-[10px] font-semibold">
                        System
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {!templatesLoading && filtered.length > 0 && !templateId && (
            <p className={`text-xs mt-3 ${hintTone}`} data-testid="wizard-step2-template-hint">
              Pick a template from the list to continue.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ---- Step 3: Details ----

function FieldLabel({ children, required }) {
  return (
    <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">
      {children}{required && <span className="text-red-400 ml-0.5">*</span>}
    </label>
  );
}

function Step3({
  methodology, templateChoice, selectedTemplate,
  users,
  contracts, contractsLoading,
  contractId, setContractId,
  name, setName,
  description, setDescription,
  serviceLine, setServiceLine,
  pmId, setPmId,
  startDate, setStartDate,
  targetEndDate, setTargetEndDate,
  phases, setPhases,
  columns, setColumns,
  createInitialWave, setCreateInitialWave,
  waveNumber, setWaveNumber,
  waveStart, setWaveStart,
  waveEnd, setWaveEnd,
  waveGoal, setWaveGoal,
  waveCountTarget, setWaveCountTarget,
  wavesThreadsModules, setWavesThreadsModules,
  dateError,
  triedAdvance = false,
  missingFields = [],
}) {
  const prefilledServiceLine = selectedTemplate?.service_line ?? '';
  const effectiveServiceLine = serviceLine || prefilledServiceLine;
  const fromTemplate = templateChoice === 'template' && selectedTemplate;
  const showValidationBanner = triedAdvance && missingFields.length > 0;

  return (
    <div className="space-y-4" data-testid="wizard-details-form">
      {showValidationBanner && (
        <div
          className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300"
          data-testid="wizard-step3-validation-banner"
          role="alert"
        >
          <p className="font-medium mb-1">Fix these before creating the project:</p>
          <ul className="list-disc pl-4 space-y-0.5">
            {missingFields.map((f) => <li key={f}>{f}</li>)}
          </ul>
        </div>
      )}
      <div>
        <FieldLabel required>Name</FieldLabel>
        <input
          type="text"
          data-testid="wizard-field-name"
          value={name}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Acme Cloud Migration"
          className="aegisaxiom-input w-full"
        />
      </div>
      <div>
        <FieldLabel>Description</FieldLabel>
        <textarea
          data-testid="wizard-field-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          className="aegisaxiom-input w-full"
          placeholder="Optional"
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <FieldLabel required>Service line</FieldLabel>
          <select
            data-testid="wizard-field-service-line"
            value={effectiveServiceLine}
            onChange={(e) => setServiceLine(e.target.value)}
            className="aegisaxiom-input w-full"
          >
            <option value="">— Select —</option>
            {SERVICE_LINES.map((s) => (<option key={s.value} value={s.value}>{s.label}</option>))}
          </select>
        </div>
        <div>
          <FieldLabel>Project manager</FieldLabel>
          <select
            data-testid="wizard-field-pm"
            value={pmId}
            onChange={(e) => setPmId(e.target.value)}
            className="aegisaxiom-input w-full"
          >
            <option value="">— Defaults to me —</option>
            {users
              .slice()
              .sort((a, b) => (a.name ?? a.display_name ?? '').localeCompare(b.name ?? b.display_name ?? ''))
              .map((u) => (
                <option key={u.user_id ?? u._id} value={u.user_id ?? u._id}>
                  {u.name ?? u.display_name}
                </option>
              ))}
          </select>
        </div>
      </div>
      <div>
        <FieldLabel>Linked contract</FieldLabel>
        <select
          data-testid="wizard-field-contract"
          value={contractId}
          onChange={(e) => setContractId(e.target.value)}
          disabled={contractsLoading}
          className="aegisaxiom-input w-full"
        >
          <option value="">— No contract —</option>
          {contracts
            .slice()
            .sort((a, b) => (a.name ?? '').localeCompare(b.name ?? ''))
            .map((c) => {
              const purchased = Number(c.total_hours_purchased ?? 0);
              const consumed = Number(
                c.hours_consumed ?? c.imported_consumed_hours ?? 0,
              );
              const remaining = purchased - consumed;
              const ctype = c.contract_type || 'contract';
              const hoursSummary = purchased > 0
                ? ` · ${purchased.toFixed(0)}h purchased, ${remaining.toFixed(0)}h left`
                : '';
              return (
                <option
                  key={c.contract_id}
                  value={c.contract_id}
                  data-testid={`wizard-contract-option-${c.contract_id}`}
                >
                  {c.name}
                  {c.account_name ? ` · ${c.account_name}` : ''}
                  {` · ${ctype}`}
                  {hoursSummary}
                </option>
              );
            })}
        </select>
        {!contractId && (
          <p
            className="mt-1 text-[11px] text-amber-300"
            data-testid="wizard-contract-warning"
          >
            This project is not linked to a contract. Approved billable
            time will not consume contract hours.
          </p>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <FieldLabel required>Start date</FieldLabel>
          <DateInput
            data-testid="wizard-field-start-date"
            value={startDate}
            onChange={setStartDate}
            placeholder="Pick a start date"
          />
        </div>
        <div>
          <FieldLabel required>Target end date</FieldLabel>
          <DateInput
            data-testid="wizard-field-target-end-date"
            value={targetEndDate}
            onChange={setTargetEndDate}
            placeholder="Pick a target end date"
            min={startDate || undefined}
          />
        </div>
      </div>
      {dateError && (startDate || targetEndDate) && (
        <p className="text-xs text-red-400" data-testid="wizard-date-error">{dateError}</p>
      )}

      {/* Methodology-specific extras */}
      {methodology === 'waterfall' && templateChoice === 'blank' && (
        <div>
          <FieldLabel required>Phases (≥1)</FieldLabel>
          <ChipInput
            values={phases}
            onChange={setPhases}
            placeholder="Type a phase name and press Enter"
            testid="wizard-phases-input"
            hint="Press Enter, Tab, or comma to add each phase. Example: Plan, Build, Ship."
          />
        </div>
      )}
      {methodology === 'agile' && templateChoice === 'blank' && (
        <div>
          <FieldLabel required>Columns (≥1)</FieldLabel>
          <ChipInput
            values={columns}
            onChange={setColumns}
            placeholder="Type a column name and press Enter"
            testid="wizard-columns-input"
            hint="Press Enter, Tab, or comma to add each column. Example: To Do, In Progress, Done."
          />
        </div>
      )}

      {fromTemplate && methodology === 'waterfall' && (
        <div className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-elevated px-3 py-2">
          <p className="text-[11px] text-aegisaxiom-text-muted mb-1">Phases (from template — edit after creation)</p>
          <div className="flex flex-wrap gap-1.5">
            {(selectedTemplate?.default_phases ?? []).map((ph) => (
              <span key={ph.name} className="inline-flex items-center h-5 px-2 rounded bg-violet-500/15 text-violet-400 text-[11px] font-medium">
                {ph.name}
              </span>
            ))}
          </div>
        </div>
      )}
      {fromTemplate && methodology === 'agile' && (
        <div className="rounded-md border border-aegisaxiom-border bg-aegisaxiom-elevated px-3 py-2">
          <p className="text-[11px] text-aegisaxiom-text-muted mb-1">Columns (from template — edit after creation)</p>
          <div className="flex flex-wrap gap-1.5">
            {(selectedTemplate?.default_columns ?? []).map((c) => (
              <span key={c.id} className="inline-flex items-center h-5 px-2 rounded bg-violet-500/15 text-violet-400 text-[11px] font-medium">
                {c.name}
              </span>
            ))}
          </div>
        </div>
      )}

      {methodology === 'waves_threads' && !fromTemplate && (
        <div className="rounded-md border border-aegisaxiom-border p-3 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <FieldLabel required>Planned wave count</FieldLabel>
              <input
                type="number"
                min={1}
                value={waveCountTarget}
                onChange={(e) => setWaveCountTarget(Number(e.target.value) || 1)}
                className="aegisaxiom-input w-full"
                data-testid="wizard-wave-count-target"
              />
            </div>
            <div>
              <FieldLabel required>Modules</FieldLabel>
              <ChipInput
                values={wavesThreadsModules}
                onChange={setWavesThreadsModules}
                placeholder="Type a module name and press Enter"
                testid="wizard-wt-modules"
                hint="Press Enter, Tab, or comma to add each module. Example: frontend, backend, data, infra."
              />
            </div>
          </div>
        </div>
      )}

      {methodology === 'waves_threads' && (
        <div className="rounded-md border border-aegisaxiom-border p-3">
          <label className="inline-flex items-center gap-2 text-sm font-medium text-aegisaxiom-text cursor-pointer">
            <input
              type="checkbox"
              checked={createInitialWave}
              onChange={(e) => setCreateInitialWave(e.target.checked)}
              data-testid="wizard-initial-wave-toggle"
              className="rounded"
            />
            Create initial wave
          </label>
          {createInitialWave && (
            <div className="mt-3 grid grid-cols-2 gap-3">
              <div>
                <FieldLabel>Wave number</FieldLabel>
                <input
                  type="number"
                  min={1}
                  value={waveNumber}
                  onChange={(e) => setWaveNumber(Number(e.target.value) || 1)}
                  className="aegisaxiom-input w-full"
                  data-testid="wizard-wave-number"
                />
              </div>
              <div>
                <FieldLabel>Goal</FieldLabel>
                <input
                  type="text"
                  value={waveGoal}
                  onChange={(e) => setWaveGoal(e.target.value)}
                  placeholder="Wave goal"
                  className="aegisaxiom-input w-full"
                  data-testid="wizard-wave-goal"
                />
              </div>
              <div>
                <FieldLabel>Wave start</FieldLabel>
                <DateInput
                  value={waveStart}
                  onChange={setWaveStart}
                  data-testid="wizard-wave-start"
                  placeholder="Wave start"
                />
              </div>
              <div>
                <FieldLabel>Wave end</FieldLabel>
                <DateInput
                  value={waveEnd}
                  onChange={setWaveEnd}
                  data-testid="wizard-wave-end"
                  placeholder="Wave end"
                  min={waveStart || undefined}
                />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---- Main wizard ----

export default function CreateProjectWizard({ open, onClose, onCreated }) {
  const [step, setStep] = useState(1);
  const [methodology, setMethodology] = useState(null);
  const [choice, setChoice] = useState(null);
  const [templateId, setTemplateId] = useState(null);
  const [templates, setTemplates] = useState([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [users, setUsers] = useState([]);
  // Contract list for the Linked Contract selector. Loaded on
  // wizard open; failing to load shows the field as an empty
  // <select> so the user can still create an unlinked project
  // (with the visible "no contract" warning) instead of being
  // blocked.
  const [contracts, setContracts] = useState([]);
  const [contractsLoading, setContractsLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [triedAdvanceStep1, setTriedAdvanceStep1] = useState(false);
  const [triedAdvanceStep2, setTriedAdvanceStep2] = useState(false);
  const [triedAdvanceStep3, setTriedAdvanceStep3] = useState(false);

  // Details form state
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [serviceLine, setServiceLine] = useState('');
  const [pmId, setPmId] = useState('');
  const [startDate, setStartDate] = useState('');
  const [targetEndDate, setTargetEndDate] = useState('');
  const [phases, setPhases] = useState([]);
  const [columns, setColumns] = useState([]);
  const [createInitialWave, setCreateInitialWave] = useState(false);
  const [waveNumber, setWaveNumber] = useState(1);
  const [waveStart, setWaveStart] = useState('');
  const [waveEnd, setWaveEnd] = useState('');
  const [waveGoal, setWaveGoal] = useState('');
  const [waveCountTarget, setWaveCountTarget] = useState(4);
  const [wavesThreadsModules, setWavesThreadsModules] = useState(['planning']);
  // Linked Contract — empty string = "no contract linked".
  // Approved billable time on a project without a contract will
  // NOT roll up to any contract's hours_consumed (debit service
  // short-circuits on time_entry.contract_id = None). The wizard
  // warns about this in Step 3 but does not block submission;
  // some projects (internal R&D, marketing, etc.) legitimately
  // run without a customer contract.
  const [contractId, setContractId] = useState('');

  // Fetch contracts once the wizard opens so the Linked Contract
  // <select> has options. Read-only — we never mutate contracts
  // from here. Active-only filter so a closed/cancelled contract
  // can't be picked as the home for a brand-new project.
  useEffect(() => {
    if (!open) return;
    setContractsLoading(true);
    api.get(`${API_URL}/api/contracts?status=active`).then(async (res) => {
      if (!res.ok) {
        // Soft failure — wizard still works, the field just shows
        // an empty option list. Don't toast — most users without
        // contract-view permission shouldn't see a scary error.
        setContracts([]);
        return;
      }
      const data = await res.json();
      const items = Array.isArray(data) ? data : data?.items ?? [];
      setContracts(items);
    }).catch(() => {
      setContracts([]);
    }).finally(() => setContractsLoading(false));
  }, [open]);

  // Fetch PM-eligible users and templates when methodology chosen
  useEffect(() => {
    api.get(`${API_URL}/api/projects/assignable-users?role=pm`).then(async (res) => {
      if (!res.ok) {
        toast.error('Could not load PM list. Ask an admin to grant projects.project.view.');
        return;
      }
      const data = await res.json();
      setUsers(Array.isArray(data) ? data : data.items ?? []);
    }).catch(() => {
      toast.error('Could not load PM list.');
    });
  }, []);

  useEffect(() => {
    if (!methodology) return;
    setTemplatesLoading(true);
    api.get(`${API_URL}/api/projects/templates?project_type=${methodology}&limit=100`).then(async (res) => {
      if (!res.ok) return;
      const data = await res.json();
      setTemplates(Array.isArray(data) ? data : data.items ?? []);
    }).catch(() => {}).finally(() => setTemplatesLoading(false));
  }, [methodology]);

  const selectedTemplate = useMemo(
    () => templates.find((t) => (t.template_id ?? t._id) === templateId) ?? null,
    [templates, templateId],
  );

  const validateDates = () => {
    if (!startDate) return 'Start date is required.';
    if (!targetEndDate) return 'Target end date is required.';
    if (new Date(targetEndDate) < new Date(startDate)) return 'Target end date must be on or after start date.';
    return null;
  };

  const prefilledServiceLine = selectedTemplate?.service_line ?? '';
  const effectiveServiceLine = serviceLine || prefilledServiceLine;

  const step3Valid = useMemo(() => {
    if (!name.trim() || name.length > 120) return false;
    if (!effectiveServiceLine) return false;
    // pmId is optional — backend defaults to the caller (Wave 4.5 / SF8).
    if (validateDates() !== null) return false;
    if (choice === 'blank') {
      if (methodology === 'waterfall' && phases.length < 1) return false;
      if (methodology === 'agile' && columns.length < 1) return false;
      if (methodology === 'waves_threads') {
        if (!Number.isInteger(Number(waveCountTarget)) || Number(waveCountTarget) < 1) return false;
        if (!Array.isArray(wavesThreadsModules) || wavesThreadsModules.length < 1) return false;
      }
    }
    return true;
  }, [name, effectiveServiceLine, startDate, targetEndDate, choice, methodology, phases, columns, waveCountTarget, wavesThreadsModules]);

  const canAdvance =
    (step === 1 && methodology !== null) ||
    (step === 2 && choice !== null && (choice === 'blank' || !!templateId)) ||
    (step === 3 && step3Valid);

  const step3MissingFields = useMemo(() => {
    const missing = [];
    if (!name.trim()) missing.push('Name');
    else if (name.length > 120) missing.push('Name (max 120 chars)');
    if (!effectiveServiceLine) missing.push('Service line');
    const dateErr = validateDates();
    if (dateErr) missing.push(dateErr);
    if (choice === 'blank') {
      if (methodology === 'waterfall' && phases.length < 1) missing.push('At least one phase');
      if (methodology === 'agile' && columns.length < 1) missing.push('At least one column');
      if (methodology === 'waves_threads') {
        if (!Number.isInteger(Number(waveCountTarget)) || Number(waveCountTarget) < 1) missing.push('Planned wave count');
        if (!Array.isArray(wavesThreadsModules) || wavesThreadsModules.length < 1) missing.push('At least one module');
      }
    }
    return missing;
  }, [name, effectiveServiceLine, startDate, targetEndDate, choice, methodology, phases, columns, waveCountTarget, wavesThreadsModules]);

  // Reset tried-advance flags as soon as the user makes the step valid.
  useEffect(() => { if (methodology) setTriedAdvanceStep1(false); }, [methodology]);
  useEffect(() => { if (step3Valid) setTriedAdvanceStep3(false); }, [step3Valid]);

  const submit = async () => {
    setSubmitError(null);
    const dateErr = validateDates();
    if (dateErr) { setSubmitError(dateErr); return; }
    setSubmitting(true);
    const extractError = (d) => {
      const detail = d?.detail;
      if (typeof detail === 'string') return detail;
      // Pydantic 422 returns detail as an array of {loc, msg, type, ...}.
      if (Array.isArray(detail)) {
        const msgs = detail
          .map((e) => {
            if (typeof e === 'string') return e;
            if (e?.msg) {
              const path = Array.isArray(e?.loc) && e.loc.length > 1
                ? e.loc.slice(1).join('.')
                : null;
              return path ? `${path}: ${e.msg}` : e.msg;
            }
            return null;
          })
          .filter(Boolean);
        if (msgs.length > 0) return msgs.join('; ');
      }
      if (detail?.message) return detail.message;
      if (detail) try { return JSON.stringify(detail); } catch { /* fallthrough */ }
      return d?.message || 'Failed';
    };
    try {
      let body;
      if (choice === 'template' && templateId) {
        body = {
          template_id: templateId,
          name: name.trim(),
          // project_type is required by ProjectFromTemplateCreate on the
          // backend (extra="forbid" + no default). Without it the POST
          // 422s with "project_type: Field required" even though the
          // wizard's Methodology step has already collected it. Send
          // the same `methodology` value the blank path sends so the
          // route's template-vs-payload type check (line 535-539 of
          // routes/projects_project.py) is satisfied.
          project_type: methodology,
          description: description || undefined,
          service_line: effectiveServiceLine,
          pm_id: pmId || undefined,
          // Linked Contract. Sent only when non-empty so the backend
          // sees `undefined` (treated as "no link") rather than an
          // empty string the route would still store.
          ...(contractId ? { contract_id: contractId } : {}),
          start_date: startDate,
          target_end_date: targetEndDate,
          ...(methodology === 'waves_threads' && createInitialWave
            ? { initial_wave: { wave_number: waveNumber, start_date: waveStart || undefined, end_date: waveEnd || undefined, goal: waveGoal || undefined } }
            : {}),
        };
        const res = await api.post(`${API_URL}/api/projects/projects/from-template`, body);
        if (!res.ok) { const d = await res.json(); throw new Error(extractError(d)); }
        const created = await res.json();
        onCreated(created.project?.project_id ?? created.project?._id ?? created.project_id ?? created._id);
        return;
      }

      // Blank flow
      const mc = {};
      if (methodology === 'waterfall') mc.phases = phases;
      else if (methodology === 'agile') mc.columns = columns.map((c) => ({ id: c.toLowerCase().replace(/\s+/g, '_'), name: c }));
      else if (methodology === 'waves_threads') {
        mc.wave_count_target = Number(waveCountTarget) || 1;
        mc.modules = (wavesThreadsModules ?? []).filter((m) => typeof m === 'string' && m.trim().length > 0);
      }

      body = {
        name: name.trim(),
        description: description || undefined,
        project_type: methodology,
        service_line: effectiveServiceLine,
        pm_id: pmId || undefined,
        // Linked Contract. Only sent when non-empty.
        ...(contractId ? { contract_id: contractId } : {}),
        start_date: startDate,
        target_end_date: targetEndDate,
        methodology_config: mc,
      };
      const res = await api.post(`${API_URL}/api/projects/projects`, body);
      if (!res.ok) { const d = await res.json(); throw new Error(extractError(d)); }
      const created = await res.json();
      onCreated(created.project_id ?? created._id);
    } catch (e) {
      setSubmitError(e.message ?? "Couldn't create project.");
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="wizard-overlay"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        data-testid="wizard-modal"
        className="w-full max-w-[720px] bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl flex flex-col max-h-[90vh]"
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-6 pt-5 pb-4 border-b border-aegisaxiom-border flex items-center justify-between">
          <h2 className="text-base font-semibold text-aegisaxiom-text" data-testid="wizard-title">Create project</h2>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            data-testid="wizard-close"
            className="h-7 w-7 inline-flex items-center justify-center rounded-md text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors"
          >
            <X size={14} />
          </button>
        </div>

        <StepIndicator step={step} />

        {/* Body */}
        <div className="flex-1 overflow-auto px-6 py-5" data-testid={`wizard-step-${step}`}>
          {step === 1 && (
            <Step1
              methodology={methodology}
              setMethodology={(m) => {
                setMethodology(m);
                setChoice(null);
                setTemplateId(null);
                setPhases([]);
                setColumns([]);
              }}
              triedAdvance={triedAdvanceStep1}
            />
          )}
          {step === 2 && methodology && (
            <Step2
              methodology={methodology}
              templates={templates}
              templatesLoading={templatesLoading}
              choice={choice}
              templateId={templateId}
              setChoice={(c) => { setChoice(c); setTriedAdvanceStep2(false); }}
              setTemplateId={(id) => { setTemplateId(id); setTriedAdvanceStep2(false); }}
              triedAdvance={triedAdvanceStep2}
            />
          )}
          {step === 3 && methodology && (
            <Step3
              methodology={methodology}
              templateChoice={choice}
              selectedTemplate={selectedTemplate}
              users={users}
              contracts={contracts}
              contractsLoading={contractsLoading}
              contractId={contractId}
              setContractId={setContractId}
              name={name} setName={setName}
              description={description} setDescription={setDescription}
              serviceLine={serviceLine} setServiceLine={setServiceLine}
              pmId={pmId} setPmId={setPmId}
              startDate={startDate} setStartDate={setStartDate}
              targetEndDate={targetEndDate} setTargetEndDate={setTargetEndDate}
              phases={phases} setPhases={setPhases}
              columns={columns} setColumns={setColumns}
              createInitialWave={createInitialWave} setCreateInitialWave={setCreateInitialWave}
              waveNumber={waveNumber} setWaveNumber={setWaveNumber}
              waveStart={waveStart} setWaveStart={setWaveStart}
              waveEnd={waveEnd} setWaveEnd={setWaveEnd}
              waveGoal={waveGoal} setWaveGoal={setWaveGoal}
              waveCountTarget={waveCountTarget} setWaveCountTarget={setWaveCountTarget}
              wavesThreadsModules={wavesThreadsModules} setWavesThreadsModules={setWavesThreadsModules}
              dateError={validateDates()}
              triedAdvance={triedAdvanceStep3}
              missingFields={step3MissingFields}
            />
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-aegisaxiom-border flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={onClose}
            data-testid="wizard-cancel"
            className="btn-secondary text-sm"
          >
            Cancel
          </button>
          <div className="flex items-center gap-3">
            {submitError && (
              <span className="text-xs text-red-400 font-medium" data-testid="wizard-error">
                {submitError}
              </span>
            )}
            {step > 1 && (
              <button
                type="button"
                onClick={() => setStep((s) => Math.max(1, s - 1))}
                data-testid="wizard-back"
                className="btn-secondary text-sm"
              >
                Back
              </button>
            )}
            {step < 3 ? (
              <button
                type="button"
                onClick={() => {
                  if (step === 1 && !canAdvance) { setTriedAdvanceStep1(true); return; }
                  if (step === 2 && !canAdvance) { setTriedAdvanceStep2(true); return; }
                  setStep((s) => Math.min(3, s + 1));
                }}
                aria-disabled={!canAdvance}
                data-testid="wizard-next"
                className={`btn-primary text-sm ${!canAdvance ? 'opacity-50' : ''}`}
              >
                Next
              </button>
            ) : (
              <button
                type="button"
                onClick={() => {
                  if (!step3Valid) { setTriedAdvanceStep3(true); return; }
                  submit();
                }}
                disabled={submitting}
                aria-disabled={!step3Valid || submitting}
                data-testid="wizard-create"
                className={`btn-primary inline-flex items-center gap-2 text-sm disabled:opacity-50 disabled:cursor-not-allowed ${!step3Valid ? 'opacity-50' : ''}`}
              >
                {submitting && (
                  <div className="animate-spin w-3.5 h-3.5 rounded-full border-2 border-white border-t-transparent" />
                )}
                Create
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
