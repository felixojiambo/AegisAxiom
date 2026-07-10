import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronsLeft, ChevronsRight, MousePointerSquareDashed, Plus, Settings, X } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import TaskCard from '../../widgets/TaskCard';
import TaskDetailDrawer from '../../widgets/TaskDetailDrawer';
import SprintSwitcher, { ALL_KEY, BACKLOG_KEY } from '../../widgets/SprintSwitcher';
import ColumnManager from '../../widgets/ColumnManager';
import BulkActionBar from '../../widgets/BulkActionBar';
import { OpenPopoverProvider } from '../../widgets/InlineEditPopover';
import { extractTaskErrorMessage } from '@/lib/safeXhr';
import { usePermissions } from '@/hooks/usePermissions';
import QuickFilterPills from '../../smartViews/QuickFilterPills';

// HTML5 drag-and-drop — no react-beautiful-dnd dependency.
const COLUMN_WIDTH = 280;
const COLLAPSED_WIDTH = 40;
const GUTTER = 16;

function hexToRgba(hex, alpha) {
  if (!hex || hex.length < 4) return `rgba(107, 43, 168, ${alpha})`;
  let h = hex.replace('#', '');
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function sortTasks(ts) {
  return [...ts].sort((a, b) => {
    const ai = a.sort_index ?? Number.MAX_SAFE_INTEGER;
    const bi = b.sort_index ?? Number.MAX_SAFE_INTEGER;
    if (ai !== bi) return ai - bi;
    return (a.created_at ?? '').localeCompare(b.created_at ?? '');
  });
}

export default function BoardView({ project, tasks, sprints, assignees, onTasksChanged, onTaskMutated, onTaskDeleted, onSprintsChanged, onProjectChanged, onConflict }) {
  const { hasPermission } = usePermissions();
  const canCreateTask = hasPermission('projects.task.create');
  const canChangeStatus = hasPermission('projects.task.change_status');
  const canEditProject = hasPermission('projects.project.edit');
  const canEditTask = hasPermission('projects.task.edit');
  const canDeleteTask = hasPermission('projects.task.delete');

  const [sprintKey, setSprintKey] = useState(ALL_KEY);
  const [openTask, setOpenTask] = useState(null);
  const [columnMgrOpen, setColumnMgrOpen] = useState(false);
  const initialised = useRef(false);

  // Bulk selection state
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState(new Set());

  // QuickFilterPills state — null means no filter active (show all)
  const [filteredTaskIds, setFilteredTaskIds] = useState(null);

  useEffect(() => {
    if (initialised.current) return;
    const active = (sprints ?? []).find((s) => s.status === 'active');
    if (active) setSprintKey(active.sprint_id);
    initialised.current = true;
  }, [sprints]);

  const columns = useMemo(
    () => (project?.methodology_config?.columns ?? []),
    [project?.methodology_config],
  );

  const storageKey = `aegisaxiom_pm_board_${project?.project_id ?? project?._id}`;
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed.collapsed)) return new Set(parsed.collapsed);
      }
    } catch {}
    return new Set();
  });
  useEffect(() => {
    localStorage.setItem(storageKey, JSON.stringify({ collapsed: Array.from(collapsed) }));
  }, [collapsed, storageKey]);

  const toggleCollapse = (cid) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(cid)) next.delete(cid); else next.add(cid);
      return next;
    });

  // Sprint filter
  const sprintFiltered = useMemo(() => {
    if (sprintKey === ALL_KEY) return tasks;
    if (sprintKey === BACKLOG_KEY) return tasks.filter((t) => !t.methodology_fields?.sprint_id);
    return tasks.filter((t) => t.methodology_fields?.sprint_id === sprintKey);
  }, [tasks, sprintKey]);

  // Quick-filter pills filter (combine with sprint filter)
  const filtered = useMemo(() => {
    if (!filteredTaskIds) return sprintFiltered;
    return sprintFiltered.filter((t) => filteredTaskIds.has(t.task_id ?? t._id));
  }, [sprintFiltered, filteredTaskIds]);

  const byColumn = useMemo(() => {
    const m = new Map();
    columns.forEach((c) => m.set(c.id, []));
    filtered.forEach((t) => {
      const cid = t.methodology_fields?.column_id;
      if (cid && m.has(cid)) m.get(cid).push(t);
      else if (columns[0]) m.get(columns[0].id).push(t);
    });
    for (const [k, v] of m.entries()) m.set(k, sortTasks(v));
    return m;
  }, [filtered, columns]);

  const handleDrop = async (e, targetColumnId) => {
    if (!canChangeStatus) {
      toast.error('You do not have permission to change task status.');
      return;
    }
    const taskId = e.dataTransfer.getData('taskId');
    if (!taskId) return;
    const task = filtered.find((t) => (t.task_id ?? t._id) === taskId);
    if (!task) return;
    const sourceColId = task.methodology_fields?.column_id;
    if (sourceColId === targetColumnId) return;
    try {
      const res = await api.patch(`${API_URL}/api/projects/tasks/${taskId}`, {
        methodology_fields: { ...task.methodology_fields, column_id: targetColumnId },
      });
      if (!res.ok) {
        toast.error(await extractTaskErrorMessage(res, 'Could not move task.'));
        return;
      }
      const data = await res.json().catch(() => ({}));
      if (onTaskMutated && (data?.task_id ?? data?._id)) {
        onTaskMutated(data);
      } else {
        onTasksChanged?.();
      }
    } catch {
      toast.error('Could not move task.');
    }
  };

  const addTask = async (columnId, title) => {
    const mf = { column_id: columnId };
    if (sprintKey !== ALL_KEY && sprintKey !== BACKLOG_KEY) mf.sprint_id = sprintKey;
    try {
      const res = await api.post(`${API_URL}/api/projects/tasks`, {
        project_id: project.project_id ?? project._id,
        title,
        methodology_fields: mf,
      });
      if (!res.ok) {
        toast.error(await extractTaskErrorMessage(res, 'Could not create task.'));
        return;
      }
      const data = await res.json().catch(() => ({}));
      if (onTaskMutated && (data?.task_id ?? data?._id)) {
        onTaskMutated(data);
      } else {
        onTasksChanged?.();
      }
    } catch {
      toast.error('Could not create task.');
    }
  };

  // Bulk selection helpers
  const toggleSelectMode = useCallback(() => {
    setSelectMode((v) => {
      if (v) setSelectedIds(new Set()); // clear on exit
      return !v;
    });
  }, []);

  const handleSelectToggle = useCallback((taskId) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(taskId)) next.delete(taskId); else next.add(taskId);
      return next;
    });
  }, []);

  const handleClearSelection = useCallback(() => {
    setSelectedIds(new Set());
    setSelectMode(false);
  }, []);

  const projectId = project?.project_id ?? project?._id;

  return (
    <OpenPopoverProvider>
      <div data-testid="board-view">
        {/* QuickFilterPills slot — rendered by Thread 2B once it lands */}
        {QuickFilterPills ? (
          <div className="px-6 pt-3">
            <QuickFilterPills
              view="board"
              tasks={tasks}
              projectId={projectId}
              onFilterChange={(ids) => setFilteredTaskIds(ids?.size > 0 ? ids : null)}
            />
          </div>
        ) : null}

        <SprintSwitcher
          projectId={projectId}
          sprints={sprints}
          onSprintsChange={onSprintsChanged}
          selectedSprintId={sprintKey}
          onChange={setSprintKey}
        />

        <div className="flex items-center gap-3 px-6 pt-3 pb-2">
          <span className="text-[11px] uppercase tracking-wide text-aegisaxiom-text-muted">
            {filtered.length} task{filtered.length === 1 ? '' : 's'} in view
          </span>

          {/* Select mode toggle */}
          {(canEditTask || canDeleteTask) ? (
            <button
              type="button"
              onClick={toggleSelectMode}
              className={`inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md text-xs transition-colors ${
                selectMode
                  ? 'bg-violet-600/20 border border-violet-500/40 text-violet-400'
                  : 'btn-secondary'
              }`}
              data-testid="board-select-mode-toggle"
              aria-pressed={selectMode}
            >
              <MousePointerSquareDashed size={12} />
              {selectMode ? (
                <>
                  {selectedIds.size > 0 ? `${selectedIds.size} selected` : 'Selecting'}
                  <X size={10} className="ml-0.5 opacity-60" />
                </>
              ) : 'Select'}
            </button>
          ) : null}

          {canEditProject ? (
            <div className="ml-auto">
              <button
                type="button"
                onClick={() => setColumnMgrOpen(true)}
                className="btn-secondary h-8 px-3 inline-flex items-center gap-1.5 text-xs"
                data-testid="board-manage-columns"
              >
                <Settings size={12} /> Manage columns
              </button>
            </div>
          ) : null}
        </div>

        <div className="px-6 pb-8 overflow-x-auto" data-testid="board-scroll-container">
          <div className="flex items-start" style={{ gap: GUTTER }}>
            {columns.map((c) => (
              <BoardColumn
                key={c.id}
                column={c}
                project={project}
                tasks={byColumn.get(c.id) ?? []}
                assignees={assignees}
                collapsed={collapsed.has(c.id)}
                canCreateTask={canCreateTask}
                canChangeStatus={canChangeStatus}
                onToggleCollapse={() => toggleCollapse(c.id)}
                onOpen={setOpenTask}
                onAdd={(title) => addTask(c.id, title)}
                onDrop={(e) => handleDrop(e, c.id)}
                onTaskMutated={onTaskMutated}
                onConflict={onConflict}
                selectMode={selectMode}
                selectedIds={selectedIds}
                onSelectToggle={handleSelectToggle}
              />
            ))}
          </div>
        </div>

        <ColumnManager
          open={columnMgrOpen}
          onOpenChange={setColumnMgrOpen}
          project={project}
          tasks={tasks}
          onSaved={() => {
            onProjectChanged?.();
            onTasksChanged?.();
          }}
        />

        <TaskDetailDrawer
          open={openTask !== null}
          onOpenChange={(v) => { if (!v) setOpenTask(null); }}
          task={openTask}
          project={project}
          allTasks={tasks}
          onTaskUpdated={onTaskMutated ?? onTasksChanged}
          onTaskDeleted={onTaskDeleted}
          onConflict={onConflict}
        />

        {/* Floating bulk bar */}
        <BulkActionBar
          selectedIds={selectedIds}
          onClear={handleClearSelection}
          onTaskMutated={onTaskMutated}
          onTaskDeleted={onTaskDeleted}
          assignees={assignees}
          sprints={sprints}
          canDelete={canDeleteTask}
          tasks={tasks}
          project={project}
        />
      </div>
    </OpenPopoverProvider>
  );
}

function BoardColumn({
  column, tasks, project, assignees, collapsed, canCreateTask, canChangeStatus,
  onToggleCollapse, onOpen, onAdd, onDrop, onTaskMutated, onConflict,
  selectMode, selectedIds, onSelectToggle,
}) {
  const [composing, setComposing] = useState(false);
  const [draft, setDraft] = useState('');
  const [isOver, setIsOver] = useState(false);

  const headerBg = hexToRgba(column.color ?? '#9B5DD1', 0.15);
  const accent = column.color ?? '#9B5DD1';
  const width = collapsed ? COLLAPSED_WIDTH : COLUMN_WIDTH;

  if (collapsed) {
    return (
      <div
        data-testid={`board-column-${column.id}`}
        style={{ width, transition: 'width 200ms ease-in-out' }}
        className="shrink-0 h-[200px] rounded-lg border border-aegisaxiom-border bg-aegisaxiom-surface flex flex-col items-center py-2 gap-2"
      >
        <button
          type="button"
          onClick={onToggleCollapse}
          aria-label="Expand column"
          className="h-6 w-6 inline-flex items-center justify-center text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
        >
          <ChevronsRight size={14} />
        </button>
        <div className="text-[11px] font-semibold text-aegisaxiom-text" style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)' }}>
          {column.name}
        </div>
        <span className="mt-auto text-[10px] font-semibold text-white rounded-full px-1.5 py-0.5" style={{ backgroundColor: accent }}>
          {tasks.length}
        </span>
      </div>
    );
  }

  return (
    <div
      data-testid={`board-column-${column.id}`}
      style={{ width, transition: 'width 200ms ease-in-out' }}
      className={`shrink-0 rounded-lg border ${isOver ? 'border-blue-500/40' : 'border-aegisaxiom-border'} bg-aegisaxiom-surface flex flex-col`}
      onDragOver={(e) => { if (!canChangeStatus) return; e.preventDefault(); setIsOver(true); }}
      onDragLeave={() => setIsOver(false)}
      onDrop={(e) => { setIsOver(false); if (canChangeStatus) onDrop(e); }}
    >
      <header className="h-10 px-3 flex items-center gap-2 rounded-t-lg" style={{ backgroundColor: headerBg }}>
        <button
          type="button"
          onClick={onToggleCollapse}
          aria-label="Collapse column"
          className="h-6 w-6 inline-flex items-center justify-center text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
        >
          <ChevronsLeft size={14} />
        </button>
        <span className="text-sm font-semibold text-aegisaxiom-text truncate" data-testid={`board-column-name-${column.id}`}>
          {column.name}
        </span>
        {column.is_done ? (
          <span className="inline-flex items-center h-4 px-1.5 rounded-full bg-aegisaxiom-surface text-[9px] font-bold" style={{ color: accent }}>
            DONE
          </span>
        ) : null}
        <span className="ml-auto inline-flex items-center h-5 px-2 rounded-full text-white text-[10px] font-semibold" style={{ backgroundColor: accent }}>
          {tasks.length}
        </span>
      </header>

      <div className="flex-1 p-3 space-y-3 min-h-[80px]">
        {tasks.length === 0 ? (
          <div className="h-20 rounded-md border border-dashed border-aegisaxiom-border flex items-center justify-center text-xs text-aegisaxiom-text-muted">
            Drop tasks or add one below
          </div>
        ) : (
          tasks.map((t) => {
            const tid = t.task_id ?? t._id;
            const assignee = (assignees ?? []).find((u) => (u.user_id ?? u._id) === t.assignee_id);
            return (
              <div
                key={tid}
                draggable={!selectMode}
                onDragStart={(e) => !selectMode && e.dataTransfer.setData('taskId', tid)}
              >
                <TaskCard
                  task={t}
                  project={project}
                  onClick={onOpen}
                  assignee={assignee}
                  assignees={assignees}
                  onTaskMutated={onTaskMutated}
                  onConflict={onConflict}
                  selectMode={selectMode}
                  selected={selectedIds?.has(tid)}
                  onSelectToggle={onSelectToggle}
                />
              </div>
            );
          })
        )}
      </div>

      {canCreateTask && !selectMode ? (
        <footer className="p-2 border-t border-aegisaxiom-border/70">
          {composing ? (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                const v = draft.trim();
                if (!v) { setComposing(false); return; }
                await onAdd(v);
                setDraft('');
                setComposing(false);
              }}
            >
              <input
                autoFocus
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={(e) => {
                  const next = e.relatedTarget;
                  if (next && e.currentTarget.form && e.currentTarget.form.contains(next)) return;
                  setTimeout(() => setComposing(false), 0);
                }}
                onKeyDown={(e) => { if (e.key === 'Escape') setComposing(false); }}
                placeholder={`New task in ${column.name}…`}
                className="aegisaxiom-input w-full h-8 text-xs"
                data-testid={`board-column-input-${column.id}`}
              />
            </form>
          ) : (
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => setComposing(true)}
              className="w-full h-8 inline-flex items-center gap-1.5 px-2 rounded-md text-xs text-aegisaxiom-text-muted hover:bg-aegisaxiom-elevated hover:text-aegisaxiom-text transition-colors"
              data-testid={`board-column-add-${column.id}`}
            >
              <Plus size={12} /> Add task
            </button>
          )}
        </footer>
      ) : null}
    </div>
  );
}
