import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertOctagon, ChevronDown, ChevronRight, Link2, Pencil, Play, Plus, Square, Trash2, X,
} from 'lucide-react';
import { differenceInCalendarDays, format, parseISO } from 'date-fns';
import { toast } from 'sonner';
import { api, unwrap } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import ConfirmModal from '@/components/shared/ConfirmModal';
import TaskCard from '../../widgets/TaskCard';
import UserAvatar from '../../widgets/UserAvatar';
import TaskDetailDrawer from '../../widgets/TaskDetailDrawer';
import { extractTaskErrorMessage } from '@/lib/safeXhr';
import CreateWaveDialog from './CreateWaveDialog';
import EditWaveDialog from './EditWaveDialog';
import PostmortemBuilder from './PostmortemBuilder';
import PostmortemViewerModal from './PostmortemViewerModal';
import { usePermissions } from '@/hooks/usePermissions';

const LANE_WIDTH = 320;
const LANE_GUTTER = 16;

const WAVE_STATUS_STYLES = {
  planning:   { bg: 'bg-purple-500/15',  text: 'text-purple-400' },
  executing:  { bg: 'bg-yellow-500/15',  text: 'text-yellow-400' },
  postmortem: { bg: 'bg-orange-500/15',  text: 'text-orange-400' },
  closed:     { bg: 'bg-green-500/15',   text: 'text-green-400'  },
};

const BLOCKER_BAR_COLOR = {
  open: '#A02620',
  acknowledged: '#FFC107',
  resolved: '#4CAF50',
  escalated: '#6B2BA8',
};

export default function ThreadsView({ project, tasks, onTasksChanged, onTaskMutated, onTaskDeleted, onNavigateToStandup, onConflict }) {
  const { hasPermission } = usePermissions();
  const canCreateWave = hasPermission('projects.wave.create');
  const canEditWave = hasPermission('projects.wave.edit');
  const canChangeWaveStatus = hasPermission('projects.wave.change_status');
  const canCreateThread = hasPermission('projects.thread.create');
  const canEditThread = hasPermission('projects.thread.edit');
  const canDeleteThread = hasPermission('projects.thread.delete');
  const canCreateTask = hasPermission('projects.task.create');
  const canEditTask = hasPermission('projects.task.edit');
  const canCreateBlocker = hasPermission('projects.blocker_ticket.create');
  const canEditBlocker = hasPermission('projects.blocker_ticket.edit');
  const [waves, setWaves] = useState([]);
  const [activeWaveId, setActiveWaveId] = useState(null);
  const [openTask, setOpenTask] = useState(null);
  const [createWaveOpen, setCreateWaveOpen] = useState(false);
  const [editWaveOpen, setEditWaveOpen] = useState(false);
  const [blockerPanelOpen, setBlockerPanelOpen] = useState(false);
  const [postmortemView, setPostmortemView] = useState(null);
  const [pmBuilderOpen, setPmBuilderOpen] = useState(false);

  // Derive tasksState from the prop via useMemo — single canonical path.
  // Updates flow: mutation → onTaskMutated → parent tasks[] prop → re-render here.
  // Removed: local setTasksState write in handleTaskUpdated and the prop-sync useEffect
  // that caused the redundant third render (triple-write cleanup from v1.2 postmortem).
  const tasksState = useMemo(() => tasks ?? [], [tasks]);

  const projectId = project?.project_id ?? project?._id;
  const initRef = useRef(false);

  const fetchWaves = () => {
    if (!projectId) return;
    api.get(`${API_URL}/api/projects/waves?project_id=${projectId}`)
      .then((r) => r.json())
      .then((data) => {
        const list = unwrap(data);
        setWaves(list);
        if (!initRef.current) {
          initRef.current = true;
          const exec = list.find((w) => w.status === 'executing');
          setActiveWaveId(exec ? (exec.wave_id ?? exec._id) : list[0] ? (list[0].wave_id ?? list[0]._id) : null);
        }
      });
  };

  useEffect(() => { fetchWaves(); }, [projectId]);

  const activeWave = useMemo(
    () => waves.find((w) => (w.wave_id ?? w._id) === activeWaveId) ?? null,
    [waves, activeWaveId],
  );

  return (
    <div data-testid="threads-view" className="relative flex flex-col h-[calc(100vh-160px)]">
      <WaveStrip
        waves={waves}
        activeWaveId={activeWaveId}
        onSelect={setActiveWaveId}
        canCreateWave={canCreateWave}
        onNewWave={() => setCreateWaveOpen(true)}
      />

      <WaveActionsBar
        wave={activeWave}
        projectId={projectId}
        canChangeWaveStatus={canChangeWaveStatus}
        canEditWave={canEditWave}
        onEditWave={() => setEditWaveOpen(true)}
        onViewPostmortem={(w) => setPostmortemView(w)}
        onOpenPostmortemBuilder={() => setPmBuilderOpen(true)}
        onRunStandup={() => onNavigateToStandup?.(activeWave?.wave_id ?? activeWave?._id ?? null)}
        blockerPanelOpen={blockerPanelOpen}
        onToggleBlockers={() => setBlockerPanelOpen((o) => !o)}
        onWaveChanged={fetchWaves}
      />

      <div className="flex-1 overflow-hidden relative">
        {activeWave ? (
          <ThreadLanes
            project={project}
            wave={activeWave}
            tasks={tasksState}
            canCreateThread={canCreateThread}
            canEditThread={canEditThread}
            canDeleteThread={canDeleteThread}
            canCreateTask={canCreateTask}
            canEditTask={canEditTask}
            onOpenTask={setOpenTask}
            onParentTaskMutated={onTaskMutated}
            onParentTasksRefetch={onTasksChanged}
            onParentTaskDeleted={onTaskDeleted}
            onConflict={onConflict}
          />
        ) : (
          <EmptyWaves canCreateWave={canCreateWave} onNewWave={() => setCreateWaveOpen(true)} />
        )}

        {activeWave ? (
          <BlockerPanel
            open={blockerPanelOpen}
            onClose={() => setBlockerPanelOpen(false)}
            wave={activeWave}
            canCreateBlocker={canCreateBlocker}
            canEditBlocker={canEditBlocker}
          />
        ) : null}
      </div>

      <CreateWaveDialog
        open={createWaveOpen}
        onOpenChange={setCreateWaveOpen}
        projectId={projectId}
        projectModules={project?.methodology_config?.modules ?? []}
        waves={waves}
        onCreated={(id) => {
          fetchWaves();
          setActiveWaveId(id);
        }}
      />

      <EditWaveDialog
        open={editWaveOpen}
        onOpenChange={setEditWaveOpen}
        wave={activeWave}
        waves={waves}
        onWaveChanged={fetchWaves}
      />

      <PostmortemViewerModal
        wave={postmortemView}
        projectName={project?.name}
        onClose={() => setPostmortemView(null)}
      />

      {activeWave ? (
        <PostmortemBuilder
          open={pmBuilderOpen}
          onOpenChange={setPmBuilderOpen}
          project={project}
          wave={activeWave}
          tasks={tasksState}
          onClosed={(nextId) => {
            fetchWaves();
            if (nextId) setActiveWaveId(nextId);
            setPmBuilderOpen(false);
          }}
        />
      ) : null}

      <TaskDetailDrawer
        open={openTask !== null}
        onOpenChange={(v) => { if (!v) setOpenTask(null); }}
        task={openTask}
        project={project}
        allTasks={tasksState}
        onTaskUpdated={(updated) => {
          onTaskMutated?.(updated);
        }}
        onTaskDeleted={onTaskDeleted}
        onConflict={onConflict}
      />
    </div>
  );
}

// ---- Wave selector strip ----
function WaveStrip({ waves, activeWaveId, onSelect, canCreateWave, onNewWave }) {
  return (
    <div
      className="flex items-center gap-2 px-4 py-2 overflow-x-auto border-b border-aegisaxiom-border bg-aegisaxiom-surface shrink-0"
      data-testid="wave-strip"
    >
      {waves.map((w) => {
        const wid = w.wave_id ?? w._id;
        const active = wid === activeWaveId;
        const styles = WAVE_STATUS_STYLES[w.status] ?? WAVE_STATUS_STYLES.planning;
        return (
          <button
            key={wid}
            type="button"
            onClick={() => onSelect(wid)}
            data-testid={`wave-pill-${w.wave_number}`}
            className={`shrink-0 rounded-md border transition-colors px-3 py-1.5 text-left ${
              active
                ? 'border-aegisaxiom-border bg-aegisaxiom-elevated'
                : 'border-aegisaxiom-border bg-aegisaxiom-surface hover:bg-aegisaxiom-elevated/50'
            }`}
            style={{ width: 200, minHeight: 52 }}
          >
            <div className="font-mono text-[12px] font-bold text-aegisaxiom-text">WAVE {w.wave_number}</div>
            <div className="text-[10px] text-aegisaxiom-text-muted">
              {w.start_date ? format(parseISO(w.start_date), 'MMM d') : '—'} → {w.end_date ? format(parseISO(w.end_date), 'MMM d') : '—'}
            </div>
            <span className={`inline-flex items-center h-4 px-1.5 rounded text-[9px] font-bold uppercase mt-0.5 ${styles.bg} ${styles.text}`}>
              {w.status === 'executing' ? 'exec' : w.status}
            </span>
          </button>
        );
      })}
      {canCreateWave ? (
        <button
          type="button"
          onClick={onNewWave}
          className="shrink-0 inline-flex items-center justify-center gap-1.5 rounded-md border border-dashed border-aegisaxiom-border px-4 text-xs text-aegisaxiom-text-muted hover:bg-aegisaxiom-elevated hover:text-aegisaxiom-text"
          style={{ minWidth: 120, minHeight: 52 }}
          data-testid="wave-strip-new"
        >
          <Plus size={14} /> New Wave
        </button>
      ) : null}
    </div>
  );
}

// ---- Wave actions bar ----
function WaveActionsBar({
  wave, projectId, canChangeWaveStatus, canEditWave, onEditWave,
  onViewPostmortem, onOpenPostmortemBuilder, onRunStandup,
  blockerPanelOpen, onToggleBlockers, onWaveChanged,
}) {
  const [blockers, setBlockers] = useState([]);
  const [confirmState, setConfirmState] = useState(null); // { type, waveId }
  const [actioning, setActioning] = useState(false);

  const waveId = wave?.wave_id ?? wave?._id;

  useEffect(() => {
    if (!waveId) { setBlockers([]); return; }
    api.get(`${API_URL}/api/projects/blocker-tickets?wave_id=${waveId}`)
      .then((r) => r.json())
      .then((data) => setBlockers(unwrap(data)));
  }, [waveId]);

  const openCount = blockers.filter((b) => b.status === 'open' || b.status === 'acknowledged').length;

  const handleStartWave = async () => {
    setActioning(true);
    try {
      const res = await api.patch(`${API_URL}/api/projects/waves/${waveId}`, { status: 'executing' });
      if (!res.ok) { toast.error('Could not start wave.'); return; }
      toast.success(`Wave ${wave.wave_number} started`);
      onWaveChanged?.();
    } catch {
      toast.error('Could not start wave.');
    } finally {
      setActioning(false);
    }
  };

  const handleBeginPostmortem = async () => {
    setActioning(true);
    try {
      const res = await api.patch(`${API_URL}/api/projects/waves/${waveId}`, { status: 'postmortem' });
      if (!res.ok) { toast.error('Could not begin postmortem.'); return; }
      toast.success('Wave moved to postmortem');
      onWaveChanged?.();
    } catch {
      toast.error('Could not begin postmortem.');
    } finally {
      setActioning(false);
      setConfirmState(null);
    }
  };

  if (!wave) {
    return (
      <div className="px-6 py-3 flex items-center justify-end border-b border-aegisaxiom-border shrink-0">
        <BlockerToggle open={blockerPanelOpen} onToggle={onToggleBlockers} count={0} />
      </div>
    );
  }

  // Wave dates are nullable per the W&T mapping — guard before parseISO,
  // otherwise parseISO(null) crashes inside date-fns.
  const days = wave.end_date
    ? differenceInCalendarDays(parseISO(wave.end_date), new Date())
    : null;
  const chipClass = days === null
    ? 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted'
    : days > 3
    ? 'bg-green-500/15 text-green-400'
    : days >= 0
    ? 'bg-yellow-500/15 text-yellow-400'
    : 'bg-red-500/15 text-red-400';
  const chipText = days === null
    ? 'No end date'
    : days > 0
    ? `${days} day${days === 1 ? '' : 's'} left`
    : days === 0 ? 'Ends today' : `${-days}d overdue`;

  return (
    <>
      <div className="px-4 py-2 flex items-center gap-3 border-b border-aegisaxiom-border shrink-0 bg-aegisaxiom-surface">
        <span className="text-[11px] uppercase tracking-wide text-aegisaxiom-text-muted">Wave {wave.wave_number}</span>
        <span className="text-sm text-aegisaxiom-text font-medium truncate max-w-[480px]">{wave.goal}</span>
        <div className="ml-auto flex items-center gap-2">
          {wave.status === 'executing' ? (
            <span className={`h-6 px-2 rounded-full text-[10px] font-semibold inline-flex items-center ${chipClass}`}>
              {chipText}
            </span>
          ) : null}

          {canChangeWaveStatus && wave.status === 'planning' ? (
            <button
              type="button"
              onClick={handleStartWave}
              disabled={actioning}
              className="btn-primary h-8 px-3 inline-flex items-center gap-1.5 text-xs disabled:opacity-60"
              data-testid="wave-start"
            >
              <Play size={12} /> Start Wave
            </button>
          ) : null}

          {canChangeWaveStatus && wave.status === 'executing' ? (
            <button
              type="button"
              onClick={() => setConfirmState({ type: 'postmortem', waveId })}
              className="btn-secondary h-8 px-3 inline-flex items-center gap-1.5 text-xs"
              data-testid="wave-begin-postmortem"
            >
              <Square size={12} /> Begin Postmortem
            </button>
          ) : null}

          {wave.status === 'postmortem' ? (
            <button
              type="button"
              onClick={onOpenPostmortemBuilder}
              className="btn-primary h-8 px-3 inline-flex items-center gap-1.5 text-xs"
              data-testid="wave-postmortem-builder"
            >
              Open Postmortem Builder
            </button>
          ) : null}

          {wave.status === 'closed' ? (
            <button
              type="button"
              onClick={() => onViewPostmortem(wave)}
              className="btn-secondary h-8 px-3 inline-flex items-center gap-1.5 text-xs"
              data-testid="wave-view-postmortem"
            >
              View Postmortem
            </button>
          ) : null}

          {canEditWave ? (
            <button
              type="button"
              onClick={onEditWave}
              className="btn-secondary h-8 px-3 inline-flex items-center gap-1.5 text-xs"
              data-testid="wave-edit"
              title="Edit wave goal, dates, or dependencies"
            >
              <Pencil size={12} /> Edit
            </button>
          ) : null}

          <button
            type="button"
            onClick={onRunStandup}
            disabled={wave.status !== 'executing'}
            title={wave.status === 'executing' ? 'Capture today\'s standup for this wave' : 'Standup is for executing waves'}
            className="btn-secondary h-8 px-3 inline-flex items-center text-xs disabled:opacity-60 disabled:cursor-not-allowed"
            data-testid="wave-run-standup"
          >
            Run Standup
          </button>

          <BlockerToggle open={blockerPanelOpen} onToggle={onToggleBlockers} count={openCount} />
        </div>
      </div>

      <ConfirmModal
        isOpen={confirmState?.type === 'postmortem'}
        title="Begin postmortem?"
        message="The wave will move to postmortem state. This cannot be undone."
        confirmText="Begin Postmortem"
        cancelText="Cancel"
        onConfirm={handleBeginPostmortem}
        onCancel={() => setConfirmState(null)}
      />
    </>
  );
}

function BlockerToggle({ open, onToggle, count }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`relative h-8 w-8 inline-flex items-center justify-center rounded-md border transition-colors ${
        open ? 'border-aegisaxiom-border bg-aegisaxiom-elevated' : 'border-aegisaxiom-border hover:bg-aegisaxiom-elevated'
      }`}
      data-testid="blocker-panel-toggle"
      aria-label="Toggle blocker panel"
    >
      <AlertOctagon size={16} className="text-aegisaxiom-text-muted" />
      {count > 0 ? (
        <span
          className="absolute -top-1 -right-1 inline-flex items-center justify-center h-4 min-w-[16px] px-1 rounded-full bg-red-600 text-white text-[9px] font-bold"
          data-testid="blocker-count"
        >
          {count}
        </span>
      ) : null}
    </button>
  );
}

// ---- Thread lanes ----
function ThreadLanes({ project, wave, tasks, canCreateThread, canEditThread, canDeleteThread, canCreateTask, canEditTask, onOpenTask, onParentTaskMutated, onParentTasksRefetch, onParentTaskDeleted, onConflict }) {
  const [threads, setThreads] = useState([]);
  const [users, setUsers] = useState([]);
  const [loadingThreads, setLoadingThreads] = useState(false);
  const [dragOverThreadId, setDragOverThreadId] = useState(null);

  const projectId = project?.project_id ?? project?._id;
  const waveId = wave?.wave_id ?? wave?._id;

  const fetchThreads = () => {
    if (!waveId) return;
    setLoadingThreads(true);
    api.get(`${API_URL}/api/projects/threads?wave_id=${waveId}`)
      .then((r) => r.json())
      .then((data) => setThreads(unwrap(data)))
      .finally(() => setLoadingThreads(false));
  };

  useEffect(() => { fetchThreads(); }, [waveId]);

  useEffect(() => {
    api.get(`${API_URL}/api/projects/assignable-users?role=member`)
      .then((r) => {
        if (!r.ok) {
          toast.error('Could not load assignable users.');
          return [];
        }
        return r.json();
      })
      .then((data) => setUsers(unwrap(data)))
      .catch(() => toast.error('Could not load assignable users.'));
  }, []);

  const tasksInWave = useMemo(
    () => tasks.filter((t) => t.methodology_fields?.wave_id === waveId),
    [tasks, waveId],
  );

  const byThread = useMemo(() => {
    const m = new Map();
    threads.forEach((th) => m.set(th.thread_id ?? th._id, []));
    for (const t of tasksInWave) {
      const tid = t.methodology_fields?.thread_id;
      if (tid && m.has(tid)) m.get(tid).push(t);
    }
    return m;
  }, [threads, tasksInWave]);

  // HTML5 DnD handlers
  const handleDragStart = (e, taskId) => {
    e.dataTransfer.setData('taskId', taskId);
    e.dataTransfer.effectAllowed = 'move';
  };

  const handleDragOver = (e, threadId) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragOverThreadId(threadId);
  };

  const handleDragLeave = () => setDragOverThreadId(null);

  const handleDrop = async (e, targetThreadId) => {
    e.preventDefault();
    setDragOverThreadId(null);
    const taskId = e.dataTransfer.getData('taskId');
    if (!taskId) return;
    const task = tasksInWave.find((t) => (t.task_id ?? t._id) === taskId);
    if (!task) return;
    const sourceThreadId = task.methodology_fields?.thread_id;
    if (sourceThreadId === targetThreadId) return;
    const targetThread = threads.find((th) => (th.thread_id ?? th._id) === targetThreadId);
    if (!targetThread) return;
    try {
      const res = await api.patch(`${API_URL}/api/projects/tasks/${taskId}`, {
        methodology_fields: {
          ...task.methodology_fields,
          thread_id: targetThreadId,
          module_owned: targetThread.module,
        },
      });
      if (!res.ok) {
        toast.error(await extractTaskErrorMessage(res, 'Could not move task.'));
        return;
      }
      const updated = await res.json();
      const updatedDoc = updated.task ?? updated;
      onParentTaskMutated?.(updatedDoc);
      onParentTasksRefetch?.();
      toast.success(`Moved to ${targetThread.module}`);
    } catch {
      toast.error('Could not move task.');
    }
  };

  const addTaskInThread = async (thread, title) => {
    const threadId = thread.thread_id ?? thread._id;
    try {
      const res = await api.post(`${API_URL}/api/projects/tasks`, {
        project_id: projectId,
        title,
        methodology_fields: {
          wave_id: waveId,
          thread_id: threadId,
          module_owned: thread.module,
          scope_drift: false,
        },
      });
      if (!res.ok) {
        toast.error(await extractTaskErrorMessage(res, 'Could not create task.'));
        return;
      }
      const created = await res.json();
      const createdDoc = created.task ?? created;
      onParentTaskMutated?.(createdDoc);
      onParentTasksRefetch?.();
    } catch {
      toast.error('Could not create task.');
    }
  };

  const patchThread = async (threadId, patch) => {
    const res = await api.patch(`${API_URL}/api/projects/threads/${threadId}`, patch);
    if (!res.ok) {
      const msg = await extractTaskErrorMessage(res, 'Could not update thread.');
      toast.error(msg);
      return;
    }
    fetchThreads();
  };

  const deleteThread = async (threadId) => {
    const res = await api.delete(`${API_URL}/api/projects/threads/${threadId}`);
    if (!res.ok) { toast.error('Could not delete thread.'); return; }
    toast.success('Thread deleted');
    fetchThreads();
  };

  const createThread = async (seed) => {
    const res = await api.post(`${API_URL}/api/projects/threads`, {
      wave_id: waveId,
      project_id: projectId,
      ...seed,
    });
    if (!res.ok) { toast.error('Could not create thread.'); return; }
    fetchThreads();
  };

  if (loadingThreads) {
    return <div className="p-6 text-sm text-aegisaxiom-text-muted">Loading threads…</div>;
  }

  if (threads.length === 0) {
    return (
      <div className="px-6 py-12 text-center" data-testid="threads-empty">
        <p className="text-sm text-aegisaxiom-text-muted mb-3">No threads in this wave yet.</p>
        <NewThreadInline users={users} onCreate={createThread} projectModules={project?.methodology_config?.modules ?? []} />
      </div>
    );
  }

  return (
    <div className="overflow-x-auto h-full overflow-y-auto" data-testid="threads-scroll">
      <div className="flex items-start p-4" style={{ gap: LANE_GUTTER }}>
        {threads.map((th) => {
          const thId = th.thread_id ?? th._id;
          return (
            <ThreadLane
              key={thId}
              thread={th}
              project={project}
              tasks={byThread.get(thId) ?? []}
              users={users}
              integrationPeers={threads.filter((other) => (other.thread_id ?? other._id) !== thId && other.integration_required)}
              isOver={dragOverThreadId === thId}
              canEditThread={canEditThread}
              projectModules={project?.methodology_config?.modules ?? []}
              onOpenTask={onOpenTask}
              onAddTask={(title) => addTaskInThread(th, title)}
              onChangeStatus={(s) => patchThread(thId, { status: s })}
              onChangeOwner={(uid) => patchThread(thId, { owner_id: uid || null })}
              onChangeModule={(m) => m && patchThread(thId, { module: m })}
              onDeleteThread={() => deleteThread(thId)}
              onDragStart={handleDragStart}
              onDragOver={(e) => handleDragOver(e, thId)}
              onDragLeave={handleDragLeave}
              onDrop={(e) => handleDrop(e, thId)}
              onTaskMutated={onParentTaskMutated}
              onTaskDeleted={onParentTaskDeleted}
              onConflict={onConflict}
            />
          );
        })}
        <NewThreadInline users={users} onCreate={createThread} projectModules={project?.methodology_config?.modules ?? []} />
      </div>
    </div>
  );
}

function ThreadLane({
  thread, project, tasks, users, integrationPeers, isOver, canEditThread,
  projectModules = [],
  onOpenTask, onAddTask, onChangeStatus, onChangeOwner, onChangeModule, onDeleteThread,
  onDragStart, onDragOver, onDragLeave, onDrop,
  onTaskMutated, onTaskDeleted, onConflict,
}) {
  const thId = thread.thread_id ?? thread._id;
  const owner = users.find((u) => (u.user_id ?? u._id) === thread.owner_id) ?? null;
  const [contractOpen, setContractOpen] = useState(false);
  const [composing, setComposing] = useState(false);
  const [draft, setDraft] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);

  const contractText = typeof thread.interface_contract?.markdown === 'string'
    ? thread.interface_contract.markdown
    : '';

  const handleAddSubmit = async (e) => {
    e?.preventDefault();
    const v = draft.trim();
    if (!v) { setComposing(false); return; }
    await onAddTask(v);
    setDraft('');
    setComposing(false);
  };

  return (
    <>
      <div
        data-testid={`thread-lane-${thId}`}
        style={{ width: LANE_WIDTH }}
        className={`shrink-0 rounded-lg border flex flex-col bg-aegisaxiom-surface transition-colors ${
          isOver ? 'border-blue-500/60 bg-aegisaxiom-elevated' : 'border-aegisaxiom-border'
        }`}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        {/* Lane header */}
        <header className="p-3 rounded-t-lg border-b border-aegisaxiom-border bg-aegisaxiom-elevated">
          <div className="flex items-center gap-2">
            <UserAvatar user={owner} size="sm" />
            {canEditThread ? (
              <select
                value={thread.owner_id ?? ''}
                onChange={(e) => onChangeOwner?.(e.target.value)}
                className="aegisaxiom-input h-6 text-xs px-1 flex-1 min-w-0 truncate"
                data-testid={`thread-owner-${thId}`}
                aria-label="Thread owner"
                title="Reassign thread owner"
              >
                <option value="">— Unassigned —</option>
                {users.map((u) => {
                  const uid = u.user_id ?? u._id;
                  return (
                    <option key={uid} value={uid}>
                      {u.name ?? u.display_name ?? u.email ?? uid}
                    </option>
                  );
                })}
              </select>
            ) : (
              <span className="text-xs text-aegisaxiom-text-muted truncate">
                {owner?.name ?? owner?.display_name ?? 'Unassigned'}
              </span>
            )}
            <button
              type="button"
              onClick={() => setConfirmDelete(true)}
              className="ml-auto h-6 w-6 inline-flex items-center justify-center text-aegisaxiom-text-muted hover:text-red-400"
              data-testid={`thread-delete-${thId}`}
              aria-label="Delete thread"
            >
              <Trash2 size={12} />
            </button>
          </div>

          <div className="flex items-center gap-1.5 mt-1.5">
            {canEditThread && projectModules.length > 0 ? (
              <select
                value={projectModules.includes(thread.module) ? thread.module : ''}
                onChange={(e) => onChangeModule?.(e.target.value)}
                className="aegisaxiom-input h-6 text-sm font-bold px-1 flex-1 min-w-0 truncate"
                data-testid={`thread-module-${thId}`}
                aria-label="Thread module"
                title="Change thread module"
              >
                {!projectModules.includes(thread.module) ? (
                  <option value="" disabled>
                    {thread.module} (invalid)
                  </option>
                ) : null}
                {projectModules.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            ) : (
              <h3
                className="text-sm font-bold text-aegisaxiom-text truncate"
                data-testid={`thread-module-${thId}`}
              >
                {thread.module}
              </h3>
            )}
            {thread.integration_required ? (
              <span
                title={
                  integrationPeers.length
                    ? `Other integrating threads: ${integrationPeers.map((p) => p.module).join(', ')}`
                    : 'Cross-thread interface required'
                }
                className="inline-flex items-center text-blue-400"
                data-testid={`thread-integration-icon-${thId}`}
              >
                <Link2 size={14} />
              </span>
            ) : null}
          </div>

          <div className="mt-1.5 flex items-center gap-2">
            <select
              value={thread.status}
              onChange={(e) => void onChangeStatus(e.target.value)}
              className="aegisaxiom-input h-5 text-[10px] font-bold uppercase px-1"
              data-testid={`thread-status-${thId}`}
            >
              <option value="scoped">Scoped</option>
              <option value="in_progress">In Progress</option>
              <option value="shipped">Shipped</option>
              <option value="blocked">Blocked</option>
            </select>
            <span className="text-[10px] text-aegisaxiom-text-muted">
              {tasks.length} task{tasks.length === 1 ? '' : 's'}
            </span>
          </div>

          {contractText ? (
            <button
              type="button"
              onClick={() => setContractOpen((o) => !o)}
              className="mt-2 inline-flex items-center gap-1 text-[11px] text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
              data-testid={`thread-contract-toggle-${thId}`}
            >
              {contractOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              Interface contract
            </button>
          ) : null}

          {contractOpen && contractText ? (
            <pre
              className="mt-1 max-h-[160px] overflow-y-auto rounded bg-aegisaxiom-bg p-2 text-[11px] text-aegisaxiom-text whitespace-pre-wrap break-words"
              data-testid={`thread-contract-${thId}`}
            >
              {contractText}
            </pre>
          ) : null}
        </header>

        {/* Task list */}
        <div className="flex-1 p-3 space-y-2 min-h-[80px]">
          {tasks.length === 0 ? (
            <div className="h-16 rounded-md border border-dashed border-aegisaxiom-border flex items-center justify-center text-xs text-aegisaxiom-text-muted">
              No tasks yet
            </div>
          ) : (
            tasks.map((t) => {
              const tid = t.task_id ?? t._id;
              return (
                <div
                  key={tid}
                  draggable
                  onDragStart={(e) => onDragStart(e, tid)}
                  className="cursor-grab active:cursor-grabbing"
                >
                  <TaskCard task={t} project={project} onClick={onOpenTask} onTaskMutated={onTaskMutated} onTaskDeleted={onTaskDeleted} onConflict={onConflict} />
                </div>
              );
            })
          )}
        </div>

        {/* Add task footer */}
        <footer className="p-2 border-t border-aegisaxiom-border">
          {composing ? (
            <form onSubmit={handleAddSubmit}>
              <input
                autoFocus
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={() => { if (!draft.trim()) setComposing(false); }}
                onKeyDown={(e) => {
                  if (e.key === 'Escape') { setComposing(false); setDraft(''); }
                }}
                placeholder={`New task in ${thread.module}…`}
                className="aegisaxiom-input w-full h-8 text-xs"
                data-testid={`thread-task-input-${thId}`}
              />
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setComposing(true)}
              data-testid={`thread-add-task-${thId}`}
              className="w-full h-8 inline-flex items-center gap-1.5 px-2 rounded-md text-xs text-aegisaxiom-text-muted hover:bg-aegisaxiom-elevated hover:text-aegisaxiom-text"
            >
              <Plus size={12} /> Add task
            </button>
          )}
        </footer>
      </div>

      <ConfirmModal
        isOpen={confirmDelete}
        title={`Delete thread "${thread.module}"?`}
        message="Tasks assigned to this thread will be detached but not deleted."
        confirmText="Delete"
        cancelText="Cancel"
        variant="danger"
        onConfirm={() => { setConfirmDelete(false); onDeleteThread(); }}
        onCancel={() => setConfirmDelete(false)}
      />
    </>
  );
}

// ---- New thread inline form ----
function NewThreadInline({ users, onCreate, projectModules = [] }) {
  const [open, setOpen] = useState(false);
  const [moduleName, setModuleName] = useState('');
  const [ownerId, setOwnerId] = useState('');
  const [saving, setSaving] = useState(false);
  const hasModules = Array.isArray(projectModules) && projectModules.length > 0;

  const submit = async () => {
    if (!moduleName.trim()) { toast.error('Module name required.'); return; }
    if (hasModules && !projectModules.includes(moduleName.trim())) {
      toast.error(`Module must be one of: ${projectModules.join(', ')}`);
      return;
    }
    setSaving(true);
    try {
      await onCreate({
        module: moduleName.trim(),
        owner_id: ownerId || undefined,
        integration_required: false,
      });
      setOpen(false);
      setModuleName('');
      setOwnerId('');
    } catch {
      toast.error('Could not create thread.');
    } finally {
      setSaving(false);
    }
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="shrink-0 inline-flex items-center justify-center gap-1.5 rounded-md border border-dashed border-aegisaxiom-border text-xs text-aegisaxiom-text-muted hover:bg-aegisaxiom-elevated hover:text-aegisaxiom-text"
        style={{ width: LANE_WIDTH, minHeight: 120 }}
        data-testid="new-thread-inline"
      >
        <Plus size={14} /> Add thread
      </button>
    );
  }

  return (
    <div
      className="shrink-0 rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface p-3 space-y-2"
      style={{ width: LANE_WIDTH }}
      data-testid="new-thread-form"
    >
      {hasModules ? (
        <select
          autoFocus
          className="aegisaxiom-input w-full h-8 text-sm"
          value={moduleName}
          onChange={(e) => setModuleName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void submit(); if (e.key === 'Escape') setOpen(false); }}
          data-testid="new-thread-module-input"
        >
          <option value="">— Select module —</option>
          {projectModules.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
      ) : (
        <input
          autoFocus
          className="aegisaxiom-input w-full h-8 text-sm"
          placeholder="Module name (e.g. auth)"
          value={moduleName}
          onChange={(e) => setModuleName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void submit(); if (e.key === 'Escape') setOpen(false); }}
          data-testid="new-thread-module-input"
        />
      )}
      <select
        value={ownerId}
        onChange={(e) => setOwnerId(e.target.value)}
        className="aegisaxiom-input w-full h-8 text-xs"
        data-testid="new-thread-owner-select"
      >
        <option value="">— Unassigned —</option>
        {users.map((u) => (
          <option key={u.user_id ?? u._id} value={u.user_id ?? u._id}>{u.name ?? u.display_name}</option>
        ))}
      </select>
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="btn-secondary h-7 px-2 text-[11px]"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => void submit()}
          disabled={saving}
          className="btn-primary h-7 px-2 text-[11px] disabled:opacity-60"
        >
          Add
        </button>
      </div>
    </div>
  );
}

// ---- Blocker slide-in panel ----
function BlockerPanel({ open, onClose, wave, canCreateBlocker, canEditBlocker }) {
  const [blockers, setBlockers] = useState([]);
  const [threads, setThreads] = useState([]);
  const [composeOpen, setComposeOpen] = useState(false);
  const [draft, setDraft] = useState({ thread_id: '', target_module: '', target_thread_id: '', description: '' });
  const [saving, setSaving] = useState(false);

  const waveId = wave?.wave_id ?? wave?._id;

  const fetchData = () => {
    if (!waveId) return;
    Promise.all([
      api.get(`${API_URL}/api/projects/blocker-tickets?wave_id=${waveId}`).then((r) => r.json()).then(unwrap),
      api.get(`${API_URL}/api/projects/threads?wave_id=${waveId}`).then((r) => r.json()).then(unwrap),
    ]).then(([bl, th]) => { setBlockers(bl); setThreads(th); });
  };

  useEffect(() => { if (open) fetchData(); }, [open, waveId]);

  const grouped = useMemo(() => {
    const map = new Map();
    for (const b of blockers) {
      const key = b.target_thread_id ?? null;
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(b);
    }
    return map;
  }, [blockers]);

  const patchBlocker = async (blockerId, patch) => {
    const res = await api.patch(`${API_URL}/api/projects/blocker-tickets/${blockerId}`, patch);
    if (!res.ok) { toast.error('Could not update blocker.'); return; }
    setBlockers((prev) => prev.map((b) => (b.blocker_ticket_id ?? b._id) === blockerId ? { ...b, ...patch } : b));
  };

  const submit = async () => {
    if (!draft.thread_id || !draft.target_module.trim() || !draft.description.trim()) {
      toast.error('Source thread, target module and description are required.');
      return;
    }
    setSaving(true);
    try {
      const res = await api.post(`${API_URL}/api/projects/blocker-tickets`, {
        wave_id: waveId,
        thread_id: draft.thread_id,
        target_module: draft.target_module.trim(),
        target_thread_id: draft.target_thread_id || undefined,
        description: draft.description.trim(),
      });
      const data = await res.json();
      if (!res.ok) { toast.error(data.detail ?? 'Could not raise blocker.'); return; }
      toast.success('Blocker raised');
      setBlockers((prev) => [...prev, data]);
      setComposeOpen(false);
      setDraft({ thread_id: '', target_module: '', target_thread_id: '', description: '' });
    } catch {
      toast.error('Could not raise blocker.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <aside
      className={`absolute top-0 right-0 bottom-0 w-[360px] bg-aegisaxiom-surface border-l border-aegisaxiom-border z-30 flex flex-col transition-transform duration-200 ease-in-out ${
        open ? 'translate-x-0' : 'translate-x-full'
      }`}
      data-testid="blocker-panel"
    >
      <header className="px-4 py-3 border-b border-aegisaxiom-border flex items-center gap-2 shrink-0">
        <h3 className="text-sm font-bold text-aegisaxiom-text">Blockers · Wave {wave.wave_number}</h3>
        <button
          type="button"
          onClick={() => setComposeOpen(true)}
          className="ml-auto btn-primary h-7 px-2 inline-flex items-center gap-1 text-[11px]"
          data-testid="blocker-new"
        >
          <Plus size={11} /> New
        </button>
        <button
          type="button"
          onClick={onClose}
          className="h-7 w-7 inline-flex items-center justify-center text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
          aria-label="Close blocker panel"
        >
          <X size={14} />
        </button>
      </header>

      <div className="overflow-y-auto flex-1">
        {blockers.length === 0 ? (
          <p className="p-4 text-xs text-aegisaxiom-text-muted" data-testid="blockers-empty">
            No blockers in this wave.
          </p>
        ) : (
          Array.from(grouped.entries()).map(([targetId, list]) => {
            const targetThread = threads.find((t) => (t.thread_id ?? t._id) === targetId);
            const groupLabel = targetThread ? `→ ${targetThread.module}` : 'Unassigned target';
            return (
              <section key={targetId ?? 'none'}>
                <header className="h-8 px-3 bg-aegisaxiom-elevated flex items-center justify-between text-[11px] font-bold uppercase text-aegisaxiom-text-muted border-b border-aegisaxiom-border">
                  <span>{groupLabel}</span>
                  <span>{list.length}</span>
                </header>
                <ul>
                  {list.map((b) => {
                    const bid = b.blocker_ticket_id ?? b._id;
                    return (
                      <li
                        key={bid}
                        className="px-3 py-2 border-b border-aegisaxiom-border flex gap-2 items-start"
                        data-testid={`blocker-row-${bid}`}
                      >
                        <div
                          className="w-1 self-stretch rounded-sm shrink-0"
                          style={{ backgroundColor: BLOCKER_BAR_COLOR[b.status] ?? '#6B7280' }}
                        />
                        <div className="flex-1 min-w-0">
                          <div className="text-xs text-aegisaxiom-text truncate">{b.description.slice(0, 80)}</div>
                          <div className="mt-1 flex items-center gap-2 text-[10px] text-aegisaxiom-text-muted">
                            <span className="font-mono">{b.target_module}</span>
                            <select
                              value={b.status}
                              onChange={(e) => patchBlocker(bid, { status: e.target.value })}
                              className="aegisaxiom-input h-5 text-[10px] px-1"
                              data-testid={`blocker-status-${bid}`}
                            >
                              <option value="open">open</option>
                              <option value="acknowledged">acknowledged</option>
                              <option value="resolved">resolved</option>
                              <option value="escalated">escalated</option>
                            </select>
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </section>
            );
          })
        )}
      </div>

      {/* Compose blocker dialog */}
      {composeOpen ? (
        <div
          className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center"
          onClick={() => setComposeOpen(false)}
        >
          <div
            className="glass-card w-[480px] p-5 space-y-3"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-label="Raise blocker"
            data-testid="blocker-compose-modal"
          >
            <div className="flex items-center gap-2 mb-1">
              <h3 className="text-sm font-bold text-aegisaxiom-text">Raise blocker</h3>
              <button type="button" onClick={() => setComposeOpen(false)} className="ml-auto text-aegisaxiom-text-muted hover:text-aegisaxiom-text" aria-label="Close">
                <X size={14} />
              </button>
            </div>
            <div>
              <label className="block text-xs text-aegisaxiom-text-muted mb-1">Source thread</label>
              <select value={draft.thread_id} onChange={(e) => setDraft({ ...draft, thread_id: e.target.value })} className="aegisaxiom-input w-full">
                <option value="">— Pick a thread —</option>
                {threads.map((t) => <option key={t.thread_id ?? t._id} value={t.thread_id ?? t._id}>{t.module}</option>)}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-aegisaxiom-text-muted mb-1">Target module</label>
                <input className="aegisaxiom-input w-full" value={draft.target_module} onChange={(e) => setDraft({ ...draft, target_module: e.target.value })} placeholder="payments" />
              </div>
              <div>
                <label className="block text-xs text-aegisaxiom-text-muted mb-1">Target thread (optional)</label>
                <select value={draft.target_thread_id} onChange={(e) => setDraft({ ...draft, target_thread_id: e.target.value })} className="aegisaxiom-input w-full">
                  <option value="">—</option>
                  {threads.map((t) => <option key={t.thread_id ?? t._id} value={t.thread_id ?? t._id}>{t.module}</option>)}
                </select>
              </div>
            </div>
            <div>
              <label className="block text-xs text-aegisaxiom-text-muted mb-1">Description</label>
              <textarea rows={4} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} className="aegisaxiom-input w-full resize-y" />
            </div>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setComposeOpen(false)} className="btn-secondary h-8 px-3 text-xs">Cancel</button>
              <button type="button" onClick={submit} disabled={saving} className="btn-primary h-8 px-3 text-xs disabled:opacity-60">
                {saving ? 'Raising…' : 'Raise blocker'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </aside>
  );
}

// ---- Empty state when no waves exist ----
function EmptyWaves({ canCreateWave, onNewWave }) {
  return (
    <div className="px-6 py-16 text-center" data-testid="threads-no-waves">
      <p className="text-sm text-aegisaxiom-text-muted mb-3">
        No waves yet. {canCreateWave ? 'Create your first one to start coordinating threads.' : 'Ask a project lead to create one.'}
      </p>
      {canCreateWave ? (
        <button
          type="button"
          onClick={onNewWave}
          className="btn-primary h-8 px-3 inline-flex items-center gap-1.5 text-xs"
          data-testid="empty-new-wave"
        >
          <Plus size={12} /> New Wave
        </button>
      ) : null}
    </div>
  );
}
