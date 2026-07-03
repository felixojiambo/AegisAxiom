import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Edit2, RefreshCw, Trash2, Upload } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';
import TabStrip from '@/components/shared/TabStrip';
import ConfirmModal from '@/components/shared/ConfirmModal';
import ExportProjectButton from '@/components/projects/widgets/ExportProjectButton';
import ImportTasksModal from '@/components/projects/widgets/ImportTasksModal';
import SaveAsTemplateButton from '@/components/projects/templates/SaveAsTemplateButton';
import { useProjectEventStream } from '@/components/projects/hooks/useProjectEventStream';
import { usePresence } from '@/components/projects/hooks/usePresence';
import CommandPalette from '@/components/CommandPalette';
import PresenceBar from '@/components/projects/widgets/PresenceBar';
import ConflictDialog from '@/components/projects/widgets/ConflictDialog';
import SOWScopeChecklist from '@/components/projects/SOWScopeChecklist';

// Methodology view placeholders — Thread 1G owns the real implementations.
// Import paths must match the convention Thread 1G uses.
let WaterfallPhasesView, WaterfallGanttView, WaterfallCalendarView;
let AgileBoardView, AgileBacklogView;
let WTWaveTimelineView, WTThreadsView, WTPostmortemBuilder, WTStandupView;

try {
  WaterfallPhasesView = require('@/components/projects/views/waterfall/PhasesView').default;
} catch {
  WaterfallPhasesView = null;
}
try {
  WaterfallGanttView = require('@/components/projects/views/waterfall/GanttView').default;
} catch {
  WaterfallGanttView = null;
}
try {
  WaterfallCalendarView = require('@/components/projects/views/waterfall/CalendarView').default;
} catch {
  WaterfallCalendarView = null;
}
let WaterfallMilestonesView;
try {
  WaterfallMilestonesView = require('@/components/projects/views/waterfall/MilestonesView').default;
} catch {
  WaterfallMilestonesView = null;
}
try {
  AgileBoardView = require('@/components/projects/views/agile/BoardView').default;
} catch {
  AgileBoardView = null;
}
try {
  AgileBacklogView = require('@/components/projects/views/agile/BacklogView').default;
} catch {
  AgileBacklogView = null;
}
let AgileSprintsView;
try {
  AgileSprintsView = require('@/components/projects/views/agile/SprintsView').default;
} catch {
  AgileSprintsView = null;
}
let AgileSprintPlanningView;
try {
  AgileSprintPlanningView = require('@/components/projects/views/agile/SprintPlanningView').default;
} catch {
  AgileSprintPlanningView = null;
}
let AgileSprintReviewView;
try {
  AgileSprintReviewView = require('@/components/projects/views/agile/SprintReviewView').default;
} catch {
  AgileSprintReviewView = null;
}
try {
  WTWaveTimelineView = require('@/components/projects/views/waves_threads/WaveTimelineView').default;
} catch {
  WTWaveTimelineView = null;
}
try {
  WTThreadsView = require('@/components/projects/views/waves_threads/ThreadsView').default;
} catch {
  WTThreadsView = null;
}
try {
  WTPostmortemBuilder = require('@/components/projects/views/waves_threads/PostmortemBuilder').default;
} catch {
  WTPostmortemBuilder = null;
}
try {
  // Wave 5 / Thread C: StandupHelper is superseded by the StandupView tab
  // (per-tab feed) + NewStandupModal (per-action capture).
  WTStandupView = require('@/components/projects/views/waves_threads/StandupView').default;
} catch {
  WTStandupView = null;
}
let WTPostmortemsView;
try {
  WTPostmortemsView = require('@/components/projects/views/waves_threads/PostmortemsView').default;
} catch {
  WTPostmortemsView = null;
}

// ---- Helpers ----

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    const m = typeof iso === 'string' && /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    const d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return iso;
  }
}

const HEALTH_DOT = { green: '#4ade80', yellow: '#facc15', red: '#f87171' };
const STATUS_PILL = {
  planning: 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted',
  active: 'bg-blue-500/15 text-blue-400',
  on_hold: 'bg-yellow-500/15 text-yellow-400',
  closed: 'bg-green-500/15 text-green-400',
};

function tabsFor(type) {
  // Scope Checklist is appended downstream in the tabs useMemo only
  // when the project carries a linked opportunity_id; the projects
  // module itself does not own a SOW concept.
  if (type === 'waterfall') {
    return [
      { id: 'overview', label: 'Overview' },
      { id: 'phases', label: 'Phases' },
      { id: 'gantt', label: 'Gantt' },
      { id: 'calendar', label: 'Calendar' },
      { id: 'milestones', label: 'Milestones' },
      { id: 'settings', label: 'Settings' },
    ];
  }
  if (type === 'agile') {
    return [
      { id: 'overview', label: 'Overview' },
      { id: 'board', label: 'Board' },
      { id: 'backlog', label: 'Backlog' },
      { id: 'sprints', label: 'Sprints' },
      { id: 'planning', label: 'Planning' },
      { id: 'review', label: 'Review' },
      { id: 'settings', label: 'Settings' },
    ];
  }
  // waves_threads
  return [
    { id: 'overview', label: 'Overview' },
    { id: 'threads', label: 'Threads' },
    { id: 'timeline', label: 'Wave Timeline' },
    { id: 'postmortem', label: 'Postmortems' },
    { id: 'standup', label: 'Standup' },
    { id: 'settings', label: 'Settings' },
  ];
}

const DEFAULT_VIEW = {
  waterfall: 'overview',
  agile: 'overview',
  waves_threads: 'overview',
};

// ---- Overview sub-tab ----

function OverviewTab({ project }) {
  const descLines = [
    { label: 'Type', value: project.project_type?.replace(/_/g, ' ') },
    { label: 'Service line', value: project.service_line?.replace(/_/g, ' ') },
    { label: 'Status', value: project.status },
    { label: 'Health', value: project.health },
    { label: 'Start', value: fmtDate(project.start_date) },
    { label: 'Target end', value: fmtDate(project.target_end_date) },
    { label: 'Actual end', value: fmtDate(project.actual_end_date) },
  ];

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-4">
      <div className="glass-card p-5">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted mb-4">Project Details</h3>
        <dl className="space-y-3">
          {descLines.map(({ label, value }) => value && value !== '—' && (
            <div key={label} className="flex gap-3 text-sm">
              <dt className="w-28 shrink-0 text-aegisaxiom-text-muted capitalize">{label}</dt>
              <dd className="text-aegisaxiom-text capitalize">{value}</dd>
            </div>
          ))}
        </dl>
      </div>
      {project.description && (
        <div className="glass-card p-5">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted mb-3">Description</h3>
          <p className="text-sm text-aegisaxiom-text-secondary leading-relaxed whitespace-pre-wrap">{project.description}</p>
        </div>
      )}
    </div>
  );
}

// ---- Settings sub-tab ----

function ModuleChipEditor({ values, onChange }) {
  const [draft, setDraft] = useState('');
  const inputRef = useRef(null);

  const add = () => {
    const v = draft.trim();
    if (!v || values.includes(v)) { setDraft(''); return; }
    onChange([...values, v]);
    setDraft('');
  };
  const remove = (v) => onChange(values.filter((x) => x !== v));

  return (
    <div
      className="aegisaxiom-input min-h-[40px] flex flex-wrap gap-1.5 p-1.5 cursor-text"
      onClick={() => inputRef.current?.focus()}
      data-testid="settings-modules-input"
    >
      {values.map((v) => (
        <span key={v} className="inline-flex items-center gap-1 h-6 px-2 rounded bg-violet-500/15 text-violet-400 text-xs font-medium">
          {v}
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); remove(v); }}
            aria-label={`Remove ${v}`}
            className="hover:text-violet-300"
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
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(); }
          if (e.key === 'Backspace' && !draft && values.length) {
            remove(values[values.length - 1]);
          }
        }}
        placeholder={values.length === 0 ? 'Type a module name and press Enter' : ''}
        className="flex-1 min-w-[160px] bg-transparent outline-none text-sm text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted"
      />
    </div>
  );
}

function SettingsTab({ project, onSave, saving, onDirtyChange, canDelete, onRequestDelete, deleting }) {
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description ?? '');
  const [health, setHealth] = useState(project.health);
  const [status, setStatus] = useState(project.status);
  // PM reassignment. Backend PATCH /projects/{id} already accepts pm_id;
  // the UI was the gap that prevented PM changes on existing projects.
  // Empty string represents "no PM assigned" so the picker can clear
  // a PM the same way the wizard's create form supports.
  const [pmId, setPmId] = useState(project.pm_id ?? '');
  const [pmCandidates, setPmCandidates] = useState([]);
  // Linked Contract reassignment. Same shape as the PM picker:
  // empty string = "no contract linked"; on save we translate
  // that to `contract_id: null` so the backend's partial PATCH
  // explicitly clears the field instead of leaving it untouched.
  // ProjectUpdate already accepts contract_id (routes/
  // projects_project.py:173) — the UI was the gap.
  const [contractId, setContractId] = useState(project.contract_id ?? '');
  const [contracts, setContracts] = useState([]);

  useEffect(() => {
    let cancelled = false;
    api.get(`${API_URL}/api/projects/assignable-users?role=pm`).then(async (res) => {
      if (!res.ok) return;
      const data = await res.json();
      if (cancelled) return;
      setPmCandidates(Array.isArray(data) ? data : data.items ?? []);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  // Fetch active contracts for the Linked Contract <select>. Soft-
  // fails to an empty list so a user without contracts.view still
  // sees the settings tab — they just can't change the link.
  useEffect(() => {
    let cancelled = false;
    api.get(`${API_URL}/api/contracts?status=active`).then(async (res) => {
      if (!res.ok) return;
      const data = await res.json();
      if (cancelled) return;
      const items = Array.isArray(data) ? data : data?.items ?? [];
      setContracts(items);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const isWavesThreads = project.project_type === 'waves_threads';
  const initialModules = isWavesThreads
    ? (project.methodology_config?.modules ?? [])
    : [];
  const initialWaveCount = isWavesThreads
    ? (project.methodology_config?.wave_count_target ?? 1)
    : 1;
  const [modules, setModules] = useState(initialModules);
  const [waveCountTarget, setWaveCountTarget] = useState(initialWaveCount);

  const modulesDirty = isWavesThreads && (
    JSON.stringify(modules) !== JSON.stringify(initialModules) ||
    Number(waveCountTarget) !== Number(initialWaveCount)
  );

  const pmDirty = (pmId || '') !== (project.pm_id ?? '');
  const contractDirty = (contractId || '') !== (project.contract_id ?? '');

  const isDirty = (
    name !== project.name ||
    (description ?? '') !== (project.description ?? '') ||
    health !== project.health ||
    status !== project.status ||
    pmDirty ||
    contractDirty ||
    modulesDirty
  );

  useEffect(() => { onDirtyChange?.(isDirty); }, [isDirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  const handleSave = async () => {
    const patch = { name: name.trim(), description, health, status };
    if (pmDirty) {
      // Backend ProjectUpdate accepts pm_id: Optional[str]. Send null
      // (not empty string) to clear the PM so the FK validation in
      // routes/projects_project.update_project skips correctly.
      patch.pm_id = pmId || null;
    }
    if (contractDirty) {
      // Same null-vs-empty-string treatment as pm_id. ProjectUpdate
      // accepts contract_id: Optional[str]; null clears the link.
      patch.contract_id = contractId || null;
    }
    if (isWavesThreads && modulesDirty) {
      const cleaned = modules
        .map((m) => (typeof m === 'string' ? m.trim() : ''))
        .filter((m) => m.length > 0);
      patch.methodology_config = {
        ...(project.methodology_config ?? {}),
        wave_count_target: Number(waveCountTarget) || 1,
        modules: cleaned,
      };
    }
    await onSave(patch);
    onDirtyChange?.(false);
  };

  const wavesThreadsValid = !isWavesThreads || (
    modules.filter((m) => typeof m === 'string' && m.trim().length > 0).length >= 1 &&
    Number.isInteger(Number(waveCountTarget)) && Number(waveCountTarget) >= 1
  );

  return (
    <div className="glass-card p-5 mt-4 max-w-lg">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-aegisaxiom-text-muted mb-4">Settings</h3>
      <div className="space-y-4">
        <div>
          <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={120}
            className="aegisaxiom-input w-full"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">Description</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className="aegisaxiom-input w-full"
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">Status</label>
            <select value={status} onChange={(e) => setStatus(e.target.value)} className="aegisaxiom-input w-full">
              <option value="planning">Planning</option>
              <option value="active">Active</option>
              <option value="on_hold">On hold</option>
              <option value="closed">Closed</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">Health</label>
            <select value={health} onChange={(e) => setHealth(e.target.value)} className="aegisaxiom-input w-full">
              <option value="green">Green</option>
              <option value="yellow">Yellow</option>
              <option value="red">Red</option>
            </select>
          </div>
        </div>
        <div>
          <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">Project manager</label>
          {(() => {
            // Defensive UX: if the project already has a pm_id but the
            // candidate fetch is still loading OR the assigned PM is no
            // longer in the assignable list (left the org, tier moved),
            // the <select> would otherwise display blank because no
            // <option value=pmId> exists. Inject a synthetic option so
            // the picker reflects the real assignment while loading,
            // and so the user can still see who is currently assigned
            // even when that PM is no longer assignable.
            const currentPmId = project.pm_id ?? '';
            const currentPmInList = currentPmId
              ? pmCandidates.some((u) => (u.user_id ?? u._id) === currentPmId)
              : true;
            const synthLabel = currentPmId
              ? `${project.pm_name ?? currentPmId} (current)`
              : '';
            return (
              <select
                data-testid="settings-field-pm"
                value={pmId}
                onChange={(e) => setPmId(e.target.value)}
                className="aegisaxiom-input w-full"
              >
                <option value="">— Unassigned —</option>
                {currentPmId && !currentPmInList && (
                  <option value={currentPmId} data-testid="settings-field-pm-current">
                    {synthLabel}
                  </option>
                )}
                {pmCandidates
                  .slice()
                  .sort((a, b) => (a.name ?? a.display_name ?? '').localeCompare(b.name ?? b.display_name ?? ''))
                  .map((u) => (
                    <option key={u.user_id ?? u._id} value={u.user_id ?? u._id}>
                      {u.name ?? u.display_name}
                    </option>
                  ))}
              </select>
            );
          })()}
          <p className="mt-1 text-[11px] text-aegisaxiom-text-muted">
            Reassign the Project Manager. Choose <em>— Unassigned —</em> to clear the
            assignment (saved as <code>pm_id: null</code>).
          </p>
        </div>
        <div>
          <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">Linked contract</label>
          {(() => {
            // Defensive UX (mirrors the PM picker): if the project is
            // already linked to a contract that isn't in the active
            // list (closed/cancelled/imported from Zoho without an
            // active status), inject a synthetic option so the user
            // can see the current link and choose to keep it,
            // change it, or clear it.
            const currentContractId = project.contract_id ?? '';
            const currentContractInList = currentContractId
              ? contracts.some((c) => c.contract_id === currentContractId)
              : true;
            const synthLabel = currentContractId
              ? `${project.contract_name ?? currentContractId} (current)`
              : '';
            return (
              <select
                data-testid="settings-field-contract"
                value={contractId}
                onChange={(e) => setContractId(e.target.value)}
                className="aegisaxiom-input w-full"
              >
                <option value="">— No contract —</option>
                {currentContractId && !currentContractInList && (
                  <option
                    value={currentContractId}
                    data-testid="settings-field-contract-current"
                  >
                    {synthLabel}
                  </option>
                )}
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
                        data-testid={`settings-contract-option-${c.contract_id}`}
                      >
                        {c.name}
                        {c.account_name ? ` · ${c.account_name}` : ''}
                        {` · ${ctype}`}
                        {hoursSummary}
                      </option>
                    );
                  })}
              </select>
            );
          })()}
          {!contractId ? (
            <p
              className="mt-1 text-[11px] text-amber-300"
              data-testid="settings-field-contract-warning"
            >
              This project is not linked to a contract. Approved billable
              time will not consume contract hours.
            </p>
          ) : (
            <p className="mt-1 text-[11px] text-aegisaxiom-text-muted">
              Choose <em>— No contract —</em> to clear the link
              (saved as <code>contract_id: null</code>).
            </p>
          )}
        </div>
        {isWavesThreads ? (
          <div className="pt-2 border-t border-aegisaxiom-border space-y-3">
            <h4 className="text-[11px] font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
              Waves &amp; Threads configuration
            </h4>
            <div>
              <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">
                Planned wave count
              </label>
              <input
                type="number"
                min={1}
                value={waveCountTarget}
                onChange={(e) => setWaveCountTarget(Number(e.target.value) || 1)}
                className="aegisaxiom-input w-32"
                data-testid="settings-wave-count-target"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1">
                Modules
              </label>
              <ModuleChipEditor values={modules} onChange={setModules} />
              <p className="mt-1.5 text-[11px] text-aegisaxiom-text-muted">
                Modules are the parallel workstreams threads attach to (e.g. <code>auth</code>, <code>billing</code>).
                Removing a module that an existing thread uses will leave that thread invalid until you reassign its module from the Threads view.
              </p>
            </div>
          </div>
        ) : null}

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || !name.trim() || !isDirty || !wavesThreadsValid}
            className="btn-primary inline-flex items-center gap-2 text-sm disabled:opacity-50"
          >
            {saving && <div className="animate-spin w-3.5 h-3.5 rounded-full border-2 border-white border-t-transparent" />}
            Save changes
          </button>
          {isDirty ? (
            <span className="text-[11px] text-yellow-400" data-testid="settings-dirty-indicator">Unsaved changes</span>
          ) : null}
        </div>
      </div>

      {canDelete ? (
        <div className="mt-6 pt-4 border-t border-aegisaxiom-border">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-red-400 mb-2">Danger zone</h4>
          <p className="text-xs text-aegisaxiom-text-muted mb-3">
            Deleting a project also removes its tasks, sprints, waves, threads, and blockers.
            Audit history is preserved. Cross-module references (time entries, contracts, etc.)
            block deletion until cleared.
          </p>
          <button
            type="button"
            onClick={onRequestDelete}
            disabled={deleting}
            data-testid="project-delete"
            className="h-8 px-3 rounded-md border border-aegisaxiom-border text-xs inline-flex items-center gap-1.5 text-red-400 hover:bg-red-500/10 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent transition-colors"
          >
            <Trash2 size={12} />
            {deleting ? 'Deleting…' : 'Delete project'}
          </button>
        </div>
      ) : null}
    </div>
  );
}

// ---- Placeholder for methodology views not yet built ----

function MethodologyViewPlaceholder({ view, type }) {
  return (
    <div className="glass-card mt-4 p-8 text-center text-aegisaxiom-text-muted text-sm">
      <p className="font-medium text-aegisaxiom-text-secondary mb-1">{view} view</p>
      <p>This view is being built by Thread 1G. Import path: <code className="text-xs font-mono bg-aegisaxiom-elevated px-1.5 py-0.5 rounded">{`@/components/projects/views/${type}/${view}`}</code></p>
    </div>
  );
}

// ---- Active methodology view dispatcher ----

function ActiveView({
  view,
  project,
  tasks,
  sprints,
  assignees,
  loading,
  onTasksChanged,
  onTaskMutated,
  onTaskDeleted,
  onSprintsChanged,
  onProjectChanged,
  onNavigateToThreads,
  onNavigateToStandup,
  pendingStandupAutoOpen,
  pendingStandupWaveId,
  onStandupAutoOpenConsumed,
  onConflict,
}) {
  const type = project.project_type;

  if (view === 'overview') return <OverviewTab project={project} />;

  if (view === 'scope_checklist') {
    return (
      <div className="mt-2">
        <SOWScopeChecklist projectId={project.project_id} />
      </div>
    );
  }

  if (type === 'waterfall') {
    if (view === 'phases') {
      return WaterfallPhasesView
        ? <WaterfallPhasesView
            project={project}
            tasks={tasks}
            assignees={assignees}
            onTasksChanged={onTasksChanged}
            onTaskMutated={onTaskMutated}
            onTaskDeleted={onTaskDeleted}
            onProjectChanged={onProjectChanged}
            onConflict={onConflict}
          />
        : <MethodologyViewPlaceholder view="PhasesView" type="waterfall" />;
    }
    if (view === 'gantt') {
      return WaterfallGanttView
        ? <WaterfallGanttView
            project={project}
            tasks={tasks}
            assignees={assignees}
            loading={loading}
            onTasksChanged={onTasksChanged}
            onTaskMutated={onTaskMutated}
            onTaskDeleted={onTaskDeleted}
            onConflict={onConflict}
          />
        : <MethodologyViewPlaceholder view="GanttView" type="waterfall" />;
    }
    if (view === 'calendar') {
      return WaterfallCalendarView
        ? <WaterfallCalendarView
            project={project}
            tasks={tasks}
            onTasksChanged={onTasksChanged}
            onTaskMutated={onTaskMutated}
            onTaskDeleted={onTaskDeleted}
            onConflict={onConflict}
          />
        : <MethodologyViewPlaceholder view="CalendarView" type="waterfall" />;
    }
    if (view === 'milestones') {
      return WaterfallMilestonesView
        ? <WaterfallMilestonesView
            project={project}
            tasks={tasks}
            assignees={assignees}
            onTasksChanged={onTasksChanged}
            onTaskMutated={onTaskMutated}
            onTaskDeleted={onTaskDeleted}
            onConflict={onConflict}
          />
        : <MethodologyViewPlaceholder view="MilestonesView" type="waterfall" />;
    }
  }

  if (type === 'agile') {
    if (view === 'board') {
      return AgileBoardView
        ? <AgileBoardView
            project={project}
            tasks={tasks}
            sprints={sprints}
            assignees={assignees}
            onTasksChanged={onTasksChanged}
            onTaskMutated={onTaskMutated}
            onTaskDeleted={onTaskDeleted}
            onSprintsChanged={onSprintsChanged}
            onProjectChanged={onProjectChanged}
            onConflict={onConflict}
          />
        : <MethodologyViewPlaceholder view="BoardView" type="agile" />;
    }
    if (view === 'backlog') {
      return AgileBacklogView
        ? <AgileBacklogView
            project={project}
            tasks={tasks}
            sprints={sprints}
            assignees={assignees}
            onTasksChanged={onTasksChanged}
            onTaskMutated={onTaskMutated}
            onTaskDeleted={onTaskDeleted}
            onConflict={onConflict}
          />
        : <MethodologyViewPlaceholder view="BacklogView" type="agile" />;
    }
    if (view === 'sprints') {
      return AgileSprintsView
        ? <AgileSprintsView
            project={project}
            tasks={tasks}
            sprints={sprints}
            assignees={assignees}
            onTasksChanged={onTasksChanged}
            onTaskMutated={onTaskMutated}
            onTaskDeleted={onTaskDeleted}
            onConflict={onConflict}
          />
        : <MethodologyViewPlaceholder view="SprintsView" type="agile" />;
    }
    if (view === 'planning') {
      return AgileSprintPlanningView
        ? <AgileSprintPlanningView
            project={project}
            tasks={tasks}
            sprints={sprints}
            assignees={assignees}
            onTasksChanged={onTasksChanged}
            onTaskMutated={onTaskMutated}
            onTaskDeleted={onTaskDeleted}
            onSprintsChanged={onSprintsChanged}
            onConflict={onConflict}
          />
        : <MethodologyViewPlaceholder view="SprintPlanningView" type="agile" />;
    }
    if (view === 'review') {
      return AgileSprintReviewView
        ? <AgileSprintReviewView
            project={project}
            tasks={tasks}
            sprints={sprints}
            assignees={assignees}
            onTasksChanged={onTasksChanged}
            onTaskMutated={onTaskMutated}
            onTaskDeleted={onTaskDeleted}
            onSprintsChanged={onSprintsChanged}
            onConflict={onConflict}
          />
        : <MethodologyViewPlaceholder view="SprintReviewView" type="agile" />;
    }
  }

  if (type === 'waves_threads') {
    if (view === 'threads') {
      return WTThreadsView
        ? <WTThreadsView
            project={project}
            tasks={tasks}
            assignees={assignees}
            onTasksChanged={onTasksChanged}
            onTaskMutated={onTaskMutated}
            onTaskDeleted={onTaskDeleted}
            onNavigateToStandup={onNavigateToStandup}
            onConflict={onConflict}
          />
        : <MethodologyViewPlaceholder view="ThreadsView" type="waves_threads" />;
    }
    if (view === 'timeline') {
      return WTWaveTimelineView
        ? <WTWaveTimelineView
            project={project}
            onNavigateToThreads={onNavigateToThreads}
          />
        : <MethodologyViewPlaceholder view="WaveTimelineView" type="waves_threads" />;
    }
    if (view === 'postmortem') {
      return WTPostmortemsView
        ? <WTPostmortemsView project={project} />
        : <MethodologyViewPlaceholder view="PostmortemsView" type="waves_threads" />;
    }
    if (view === 'standup') {
      return WTStandupView
        ? <WTStandupView
            project={project}
            activeWaveId={pendingStandupWaveId}
            autoOpenNew={pendingStandupAutoOpen}
            onAutoOpenConsumed={onStandupAutoOpenConsumed}
          />
        : <MethodologyViewPlaceholder view="StandupView" type="waves_threads" />;
    }
  }

  return null;
}

// ---- Main component ----

export default function ProjectDetailPage({ projectId, onBack }) {
  const perms = usePermissions();
  const canEdit = perms.hasPermission('projects.project.edit');
  const canDelete = perms.hasPermission('projects.project.delete');
  const canImport = perms.hasPermission('projects.task.create');
  const [importOpen, setImportOpen] = useState(false);

  const [project, setProject] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [sprints, setSprints] = useState([]);
  const [assignees, setAssignees] = useState([]);
  const [loading, setLoading] = useState(true);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [error, setError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [activeView, setActiveView] = useState('overview');
  const [settingsDirty, setSettingsDirty] = useState(false);
  // pendingAction discriminates the unsaved-changes prompt — either a tab
  // switch (`{type: 'tab', target: <id>}`) or an in-place refetch
  // (`{type: 'refresh'}`). Both flows would otherwise drop edits silently.
  const [pendingAction, setPendingAction] = useState(null);
  // Wave 5 / Thread C: when the W&T Threads tab's "Run Standup" button is
  // clicked, we navigate to the Standup tab AND auto-open NewStandupModal
  // pre-filled with the active wave_id. These two pieces of state survive
  // the cross-tab handoff.
  const [pendingStandupAutoOpen, setPendingStandupAutoOpen] = useState(false);
  const [pendingStandupWaveId, setPendingStandupWaveId] = useState(null);
  const firstLoadRef = useRef(true);

  // Command palette (Ctrl/Cmd + K)
  const [cmdOpen, setCmdOpen] = useState(false);

  useEffect(() => {
    function handleKeyDown(e) {
      const isMac = navigator.platform?.toUpperCase().includes('MAC');
      const trigger = isMac ? (e.metaKey && e.key === 'k') : (e.ctrlKey && e.key === 'k');
      if (trigger) {
        e.preventDefault();
        setCmdOpen((o) => !o);
      }
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, []);

  // Conflict dialog state — populated when a conditional PATCH returns 409.
  const [conflictState, setConflictState] = useState(null);
  // { myDoc, theirDoc, retryFn }

  // Presence: report current viewport to the server every 30s.
  const { viewing } = usePresence(project ? projectId : null, activeView);

  const fetchProject = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const res = await api.get(`${API_URL}/api/projects/projects/${projectId}`);
      if (!res.ok) throw new Error('Not found');
      const data = await res.json();
      setProject(data);
      // Only set the default view on the very first load — never clobber the
      // user's tab choice on subsequent refetches.
      if (firstLoadRef.current) {
        setActiveView(DEFAULT_VIEW[data.project_type] ?? 'overview');
        firstLoadRef.current = false;
      }
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  const fetchTasks = useCallback(async () => {
    setTasksLoading(true);
    try {
      const res = await api.get(`${API_URL}/api/projects/tasks?project_id=${projectId}&limit=500`);
      if (!res.ok) {
        toast.error('Could not load tasks.');
        return;
      }
      const data = await res.json();
      setTasks(Array.isArray(data) ? data : data.items ?? []);
    } catch {
      toast.error('Could not load tasks.');
    } finally {
      setTasksLoading(false);
    }
  }, [projectId]);

  const fetchSprints = useCallback(async () => {
    try {
      const res = await api.get(`${API_URL}/api/projects/sprints?project_id=${projectId}&limit=200`);
      if (!res.ok) return;
      const data = await res.json();
      setSprints(Array.isArray(data) ? data : data.items ?? []);
    } catch {
      // non-fatal — sprints only used by agile views
    }
  }, [projectId]);

  const fetchAssignees = useCallback(async () => {
    try {
      const res = await api.get(`${API_URL}/api/projects/assignable-users?role=member`);
      if (!res.ok) {
        // Endpoint may not be deployed yet — fail silently rather than spam.
        return;
      }
      const data = await res.json();
      setAssignees(Array.isArray(data) ? data : data.items ?? []);
    } catch {
      // non-fatal
    }
  }, []);

  const onTaskMutated = useCallback((updated) => {
    if (!updated) { fetchTasks(); return; }
    setTasks((prev) => {
      const id = updated.task_id ?? updated._id;
      const idx = prev.findIndex((t) => (t.task_id ?? t._id) === id);
      if (idx === -1) return [...prev, updated];
      const next = prev.slice();
      next[idx] = updated;
      return next;
    });
  }, [fetchTasks]);

  const onTaskDeleted = useCallback((taskId) => {
    if (!taskId) return;
    setTasks((prev) => prev.filter((t) => (t.task_id ?? t._id) !== taskId));
  }, []);

  const onTaskUpdated = useCallback(() => { fetchTasks(); }, [fetchTasks]);

  // SSE event stream — receives real-time mutations from other users.
  useProjectEventStream(projectId, {
    onEvent: useCallback((event) => {
      if (event.entity_type === 'task') {
        if (event.type === 'task.deleted') {
          onTaskDeleted(event.entity_id);
        } else if (event.payload) {
          onTaskMutated(event.payload);
        }
      }
      // Future: project.updated, sprint.updated, etc.
    }, [onTaskMutated, onTaskDeleted]),
    onGap: useCallback(() => {
      // Missed events during a reconnect gap — backfill from server.
      fetchTasks();
    }, [fetchTasks]),
  });

  useEffect(() => {
    firstLoadRef.current = true;
    fetchProject();
    fetchTasks();
    fetchAssignees();
  }, [fetchProject, fetchTasks, fetchAssignees]);

  // Sprints only matter for agile projects.
  useEffect(() => {
    if (project?.project_type === 'agile') fetchSprints();
  }, [project?.project_type, fetchSprints]);

  const handleSave = useCallback(async (patch) => {
    setSaving(true);
    try {
      const res = await api.patch(`${API_URL}/api/projects/projects/${projectId}`, patch);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        toast.error(data?.detail ?? 'Could not save changes.');
        return;
      }
      const updated = await res.json();
      setProject(updated);
      toast.success('Changes saved');
    } catch {
      toast.error('Could not save changes.');
    } finally {
      setSaving(false);
    }
  }, [projectId]);

  const handleDelete = useCallback(async () => {
    setDeleting(true);
    setConfirmDeleteOpen(false);
    try {
      const res = await api.delete(`${API_URL}/api/projects/projects/${projectId}`);
      if (!res.ok) {
        // Surface the backend's structured detail — the FK guard returns a
        // 409 with a specific reason (e.g. "Project still referenced by N
        // time entries"). Fall back to a generic message if shape unknown.
        let msg = 'Could not delete project.';
        try {
          const data = await res.json();
          if (typeof data?.detail === 'string') msg = data.detail;
          else if (data?.detail?.message) msg = data.detail.message;
        } catch { /* ignore */ }
        toast.error(msg);
        return;
      }
      toast.success('Project deleted');
      onBack?.();
    } catch {
      toast.error('Could not delete project.');
    } finally {
      setDeleting(false);
    }
  }, [projectId, onBack]);

  const tabs = useMemo(() => {
    if (!project) return [];
    const all = tabsFor(project.project_type);
    const visible = canEdit ? all : all.filter((t) => t.id !== 'settings');

    // The Scope Checklist surface is PMO-facing: it renders the
    // approved Field Services SOW so technician delivery can be
    // ticked off against scope. Only meaningful when the project is
    // linked to an opportunity; the backend route returns empty
    // otherwise.
    if (project.opportunity_id) {
      const settingsIdx = visible.findIndex((t) => t.id === 'settings');
      const insertAt = settingsIdx >= 0 ? settingsIdx : visible.length;
      const next = visible.slice();
      next.splice(insertAt, 0, { id: 'scope_checklist', label: 'Scope Checklist' });
      return next;
    }

    return visible;
  }, [project, canEdit]);

  const doRefreshAll = useCallback(() => {
    fetchProject();
    fetchTasks();
    fetchAssignees();
    if (project?.project_type === 'agile') fetchSprints();
  }, [fetchProject, fetchTasks, fetchAssignees, fetchSprints, project?.project_type]);

  const handleTabChange = useCallback((nextId) => {
    if (settingsDirty && activeView === 'settings' && nextId !== 'settings') {
      setPendingAction({ type: 'tab', target: nextId });
      return;
    }
    setActiveView(nextId);
  }, [settingsDirty, activeView]);

  const handleRefreshClick = useCallback(() => {
    if (settingsDirty && activeView === 'settings') {
      setPendingAction({ type: 'refresh' });
      return;
    }
    doRefreshAll();
  }, [settingsDirty, activeView, doRefreshAll]);

  const pm = useMemo(() => assignees.find((u) => (u.user_id ?? u._id) === project?.pm_id), [assignees, project?.pm_id]);

  if (loading) {
    return (
      <div className="min-h-[50vh] flex items-center justify-center">
        <div className="animate-spin w-8 h-8 rounded-full border-2 border-violet-500 border-t-transparent" />
      </div>
    );
  }

  if (error || !project) {
    return (
      <div className="glass-card p-8 text-center" data-testid="project-detail-not-found">
        <p className="text-aegisaxiom-text font-medium mb-1">Project not found.</p>
        <p className="text-sm text-aegisaxiom-text-muted mb-4">It may have been deleted, or you don&apos;t have access.</p>
        <button onClick={onBack} className="btn-secondary text-sm">Go back</button>
      </div>
    );
  }

  const statusCls = STATUS_PILL[project.status] ?? 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted';

  return (
    <section data-testid="project-detail-page">
      <CommandPalette open={cmdOpen} onOpenChange={setCmdOpen} />

      {/* Back nav */}
      <button
        type="button"
        onClick={onBack}
        className="inline-flex items-center gap-1.5 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text mb-4 transition-colors"
        data-testid="project-back"
      >
        <ArrowLeft size={14} />
        All Projects
      </button>

      {/* Project header */}
      <div className="glass-card p-5 mb-4" data-testid="project-header">
        <div className="flex items-start gap-4">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 flex-wrap mb-2">
              <h2 className="text-xl font-bold text-aegisaxiom-text truncate">{project.name}</h2>
              <span className={`inline-flex items-center h-5 px-2 rounded text-[10px] font-medium ${statusCls}`}>
                {project.status?.replace(/_/g, ' ')}
              </span>
              <span className="inline-flex items-center gap-1.5 text-xs text-aegisaxiom-text-secondary">
                <span
                  className="inline-block w-2 h-2 rounded-full"
                  style={{ backgroundColor: HEALTH_DOT[project.health] ?? '#94a3b8' }}
                />
                {project.health}
              </span>
            </div>
            <div className="flex flex-wrap gap-4 text-xs text-aegisaxiom-text-muted">
              <span className="capitalize">{project.project_type?.replace(/_/g, ' ')}</span>
              <span>·</span>
              <span className="capitalize">{project.service_line?.replace(/_/g, ' ')}</span>
              {pm ? (
                <>
                  <span>·</span>
                  <span data-testid="project-header-pm">PM: <span className="text-aegisaxiom-text-secondary font-medium">{pm.name ?? pm.display_name}</span></span>
                </>
              ) : null}
              {project.start_date && <><span>·</span><span>{fmtDate(project.start_date)} → {fmtDate(project.target_end_date)}</span></>}
            </div>
          </div>
          {canImport && (
            <button
              type="button"
              onClick={() => setImportOpen(true)}
              className="btn-secondary inline-flex items-center gap-1.5 text-xs shrink-0"
              aria-label="Import tasks from CSV"
              data-testid="project-import-button"
            >
              <Upload size={12} />
              Import
            </button>
          )}
          <PresenceBar viewing={viewing} />
          <ExportProjectButton project={project} />
          {canEdit && (
            <SaveAsTemplateButton project={project} />
          )}
          {canEdit && (
            <button
              type="button"
              onClick={() => { setActiveView('settings'); }}
              className="btn-secondary inline-flex items-center gap-1.5 text-xs shrink-0"
              aria-label="Edit project settings"
            >
              <Edit2 size={12} />
              Edit
            </button>
          )}
          <button
            type="button"
            onClick={handleRefreshClick}
            className="h-8 w-8 inline-flex items-center justify-center rounded-md text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-colors"
            aria-label="Refresh"
            data-testid="project-refresh"
          >
            <RefreshCw size={13} />
          </button>
        </div>
      </div>

      {/* Sub-tab strip */}
      <TabStrip
        tabs={tabs}
        activeId={activeView}
        onChange={handleTabChange}
        variant="underline"
        className="mb-2"
      />

      {/* Active view */}
      {activeView === 'settings' ? (
        canEdit ? (
          <SettingsTab
            project={project}
            onSave={handleSave}
            saving={saving}
            onDirtyChange={setSettingsDirty}
            canDelete={canDelete}
            deleting={deleting}
            onRequestDelete={() => setConfirmDeleteOpen(true)}
          />
        ) : (
          <div className="glass-card p-5 mt-4 text-xs text-aegisaxiom-text-muted">
            Settings is only available to project managers and admins.
          </div>
        )
      ) : (
        <ActiveView
          view={activeView}
          project={project}
          tasks={tasks}
          sprints={sprints}
          assignees={assignees}
          loading={tasksLoading}
          onTasksChanged={onTaskUpdated}
          onTaskMutated={onTaskMutated}
          onTaskDeleted={onTaskDeleted}
          onSprintsChanged={fetchSprints}
          onProjectChanged={fetchProject}
          onNavigateToThreads={() => setActiveView('threads')}
          onNavigateToStandup={(waveId) => {
            setPendingStandupWaveId(waveId ?? null);
            setPendingStandupAutoOpen(true);
            setActiveView('standup');
          }}
          pendingStandupAutoOpen={pendingStandupAutoOpen}
          pendingStandupWaveId={pendingStandupWaveId}
          onStandupAutoOpenConsumed={() => setPendingStandupAutoOpen(false)}
          onConflict={setConflictState}
        />
      )}

      <ConfirmModal
        isOpen={confirmDeleteOpen}
        title={`Delete "${project.name}"?`}
        message="This permanently removes the project and cascades into its tasks, sprints, waves, threads, and blockers. Audit history is preserved. This cannot be undone."
        confirmText="Delete project"
        cancelText="Cancel"
        variant="danger"
        onConfirm={handleDelete}
        onCancel={() => setConfirmDeleteOpen(false)}
      />

      <ImportTasksModal
        isOpen={importOpen}
        onClose={() => setImportOpen(false)}
        project={project}
        onImported={fetchTasks}
      />

      <ConflictDialog
        isOpen={conflictState !== null}
        myDoc={conflictState?.myDoc}
        theirDoc={conflictState?.theirDoc}
        onDiscard={() => {
          // Apply the server's version to local task list and close.
          if (conflictState?.theirDoc) onTaskMutated(conflictState.theirDoc);
          setConflictState(null);
        }}
        onOverwrite={() => {
          // Re-PATCH without If-Match (force-write the user's version).
          conflictState?.retryFn?.();
          setConflictState(null);
        }}
        onMerge={() => {
          // Apply server doc to local state so user sees both changes and
          // can re-edit from the merged base.
          if (conflictState?.theirDoc) onTaskMutated(conflictState.theirDoc);
          setConflictState(null);
          toast('Task updated with latest server version. Re-apply your changes.');
        }}
      />

      <ConfirmModal
        isOpen={pendingAction !== null}
        title="Discard unsaved changes?"
        message={
          pendingAction?.type === 'refresh'
            ? 'You have unsaved changes in Settings. Refresh anyway?'
            : 'You have unsaved changes in Settings. Switch tabs anyway?'
        }
        confirmText={pendingAction?.type === 'refresh' ? 'Discard & refresh' : 'Discard & switch'}
        cancelText="Keep editing"
        variant="danger"
        onConfirm={() => {
          setSettingsDirty(false);
          if (pendingAction?.type === 'tab') {
            setActiveView(pendingAction.target);
          } else if (pendingAction?.type === 'refresh') {
            doRefreshAll();
          }
          setPendingAction(null);
        }}
        onCancel={() => setPendingAction(null)}
      />
    </section>
  );
}