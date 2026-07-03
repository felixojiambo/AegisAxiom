import { useState, useEffect, useCallback } from 'react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';
import { toast } from 'sonner';
import {
  CheckCircle2,
  Clock,
  XCircle,
  Loader2,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  Package,
  Inbox,
  ThumbsUp,
  ThumbsDown,
  RotateCcw,
} from 'lucide-react';
import ConfirmModal from '@/components/shared/ConfirmModal';

const API = API_URL;

const STATUS_COLORS = {
  pending:
    'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700',
  analyzing:
    'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-700/40',
  awaiting_review:
    'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-700/40',
  adapting:
    'bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300 border border-violet-200 dark:border-violet-700/40',
  extracting:
    'bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-700/40',
  registering:
    'bg-cyan-100 dark:bg-cyan-900/30 text-cyan-700 dark:text-cyan-300 border border-cyan-200 dark:border-cyan-700/40',
  configured:
    'bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-700/40',
  active:
    'bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-300 border border-green-200 dark:border-green-700/40',
  failed:
    'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-700/40',
  archived:
    'bg-zinc-200 dark:bg-zinc-800/60 text-zinc-500 dark:text-zinc-400 border border-zinc-300 dark:border-zinc-700',
};

const STAGE_ICONS = {
  success: <CheckCircle2 className="w-4 h-4 text-green-600 dark:text-green-400" />,
  in_progress: <Loader2 className="w-4 h-4 text-blue-500 dark:text-blue-400 animate-spin" />,
  failed: <XCircle className="w-4 h-4 text-red-500 dark:text-red-400" />,
  pending: <Clock className="w-4 h-4 text-aegisaxiom-text-muted" />,
};

// Confirmation state shape
const CONFIRM_NONE = { open: false, type: null, stepId: null };

export default function AppIngestionAdmin() {
  const [ingestions, setIngestions] = useState([]);
  const [selectedApp, setSelectedApp] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [expandedSections, setExpandedSections] = useState({
    pipeline: true,
    modules: true,
    logs: false,
  });

  // Rejection reason input
  const [rejectReason, setRejectReason] = useState('');

  // Confirm modal state
  const [confirm, setConfirm] = useState(CONFIRM_NONE);

  const fetchIngestions = useCallback(async () => {
    setLoading(true);
    const res = await api.get(`${API}/api/app-ingestion/ingestions`);
    if (res.ok) {
      const data = await res.json();
      setIngestions(Array.isArray(data) ? data : []);
    } else {
      toast.error('Failed to load ingestions');
    }
    setLoading(false);
  }, []);

  const fetchDetail = useCallback(async (appId) => {
    setDetailLoading(true);
    const res = await api.get(`${API}/api/app-ingestion/ingestions/${appId}`);
    if (res.ok) {
      const data = await res.json();
      setDetail(data);
    } else {
      toast.error('Failed to load ingestion details');
    }
    setDetailLoading(false);
  }, []);

  useEffect(() => {
    fetchIngestions();
  }, [fetchIngestions]);

  useEffect(() => {
    if (selectedApp) fetchDetail(selectedApp);
  }, [selectedApp, fetchDetail]);

  const toggleSection = (key) => {
    setExpandedSections((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  // ==================== ACTIONS ====================

  const handleActivate = async (appId) => {
    const res = await api.post(`${API}/api/app-ingestion/ingestions/${appId}/activate`);
    if (res.ok) {
      toast.success('App activated');
      fetchDetail(appId);
      fetchIngestions();
    } else {
      const err = await res.json().catch(() => ({}));
      toast.error(err.detail || 'Activation failed');
    }
  };

  const handleApprove = async (appId) => {
    setActionLoading(true);
    const res = await api.post(`${API}/api/app-ingestion/ingestions/${appId}/approve`);
    setActionLoading(false);
    if (res.ok) {
      toast.success('Ingestion approved — status advanced to configured');
      fetchDetail(appId);
      fetchIngestions();
    } else {
      const err = await res.json().catch(() => ({}));
      toast.error(err.detail || 'Approval failed');
    }
  };

  const handleReject = async (appId, reason) => {
    if (!reason || !reason.trim()) {
      toast.error('A rejection reason is required');
      return;
    }
    setActionLoading(true);
    const res = await api.post(`${API}/api/app-ingestion/ingestions/${appId}/reject`, {
      reason: reason.trim(),
    });
    setActionLoading(false);
    if (res.ok) {
      toast.success('Ingestion rejected and archived');
      setRejectReason('');
      fetchDetail(appId);
      fetchIngestions();
    } else {
      const err = await res.json().catch(() => ({}));
      toast.error(err.detail || 'Rejection failed');
    }
  };

  const handleRerunStep = async (appId, stepId) => {
    setActionLoading(true);
    const res = await api.post(`${API}/api/app-ingestion/ingestions/${appId}/rerun-step`, {
      step_id: stepId,
    });
    setActionLoading(false);
    if (res.ok) {
      const data = await res.json().catch(() => ({}));
      toast.success(
        `Step "${stepId}" marked as resolved (${data.resolved_count ?? 0} failed entries resolved)`
      );
      fetchDetail(appId);
    } else {
      const err = await res.json().catch(() => ({}));
      toast.error(err.detail || `Failed to rerun step "${stepId}"`);
    }
  };

  // ==================== CONFIRM MODAL HANDLERS ====================

  const openConfirmApprove = () => setConfirm({ open: true, type: 'approve', stepId: null });
  const openConfirmReject = () => setConfirm({ open: true, type: 'reject', stepId: null });
  const openConfirmRerun = (stepId) => setConfirm({ open: true, type: 'rerun', stepId });
  const closeConfirm = () => setConfirm(CONFIRM_NONE);

  const onConfirm = () => {
    if (!detail?.app?.app_id) return;
    const appId = detail.app.app_id;
    if (confirm.type === 'approve') {
      handleApprove(appId);
    } else if (confirm.type === 'reject') {
      handleReject(appId, rejectReason);
    } else if (confirm.type === 'rerun' && confirm.stepId) {
      handleRerunStep(appId, confirm.stepId);
    }
  };

  // Build the confirm modal props based on current action type
  const confirmModalProps = (() => {
    if (confirm.type === 'approve') {
      return {
        title: 'Approve ingestion?',
        message:
          'This will advance the ingestion to "configured" status, making it ready for activation.',
        confirmText: 'Approve',
        variant: 'primary',
      };
    }
    if (confirm.type === 'reject') {
      return {
        title: 'Reject ingestion?',
        message: `This will archive the ingestion. Reason:\n"${rejectReason}"`,
        confirmText: 'Reject & Archive',
        variant: 'danger',
      };
    }
    if (confirm.type === 'rerun') {
      return {
        title: `Rerun step "${confirm.stepId}"?`,
        message:
          'Prior failed log entries for this step will be marked as resolved in the activity log.',
        confirmText: 'Rerun Step',
        variant: 'warning',
      };
    }
    return { title: '', message: '', confirmText: 'Confirm', variant: 'danger' };
  })();

  // ==================== RENDER HELPERS ====================

  const renderStatCards = () => {
    // Prefer live actual_counts; fall back to summary zeroes gracefully
    const counts = detail?.actual_counts || {};
    const summary = detail?.summary || {};

    const stats = [
      { label: 'Entities', value: counts.entities ?? summary.entities_extracted ?? 0 },
      { label: 'Fields', value: counts.fields ?? summary.fields_catalogued ?? 0 },
      { label: 'Routes', value: counts.routes ?? summary.routes_mapped ?? 0 },
      { label: 'Modules', value: counts.modules ?? summary.modules_registered ?? 0 },
      { label: 'Permissions', value: counts.permissions ?? summary.permissions_generated ?? 0 },
      { label: 'Log Entries', value: summary.log_entries ?? 0 },
    ];

    return (
      <div className="grid grid-cols-3 md:grid-cols-6 gap-3 mt-5">
        {stats.map(({ label, value }) => (
          <div
            key={label}
            className="text-center p-2.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg"
          >
            <div className="text-lg font-bold text-aegisaxiom-text leading-tight">{value}</div>
            <div className="text-[10px] sm:text-xs text-aegisaxiom-text-muted mt-0.5">{label}</div>
          </div>
        ))}
      </div>
    );
  };

  const renderActionButtons = () => {
    const actionable = detail?.actionable_status;
    const appId = detail?.app?.app_id;
    if (!appId) return null;

    return (
      <div className="flex flex-wrap items-center gap-2">
        {actionable === 'awaiting_review' && (
          <>
            <Button
              size="sm"
              className="bg-emerald-600 hover:bg-emerald-500 text-white"
              onClick={openConfirmApprove}
              disabled={actionLoading}
              data-testid="approve-ingestion-btn"
            >
              <ThumbsUp className="w-3.5 h-3.5 mr-1.5" />
              Approve
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="border-red-400 text-red-600 hover:bg-red-50 dark:hover:bg-red-600/10"
              onClick={openConfirmReject}
              disabled={actionLoading || !rejectReason.trim()}
              data-testid="reject-ingestion-btn"
            >
              <ThumbsDown className="w-3.5 h-3.5 mr-1.5" />
              Reject
            </Button>
          </>
        )}
        {actionable === 'configured' && (
          <Button
            size="sm"
            onClick={() => handleActivate(appId)}
            disabled={actionLoading}
            data-testid="activate-app-btn"
          >
            Activate
          </Button>
        )}
        {actionLoading && <Loader2 className="w-4 h-4 animate-spin text-aegisaxiom-text-muted" />}
      </div>
    );
  };

  const renderRejectReasonInput = () => {
    if (detail?.actionable_status !== 'awaiting_review') return null;
    return (
      <div className="mt-3">
        <label className="block text-xs text-aegisaxiom-text-muted mb-1">
          Rejection reason (required before rejecting)
        </label>
        <input
          type="text"
          value={rejectReason}
          onChange={(e) => setRejectReason(e.target.value)}
          placeholder="e.g. Missing required entities, naming conflicts..."
          className="aegisaxiom-input w-full text-sm"
          data-testid="reject-reason-input"
        />
      </div>
    );
  };

  const renderRegisteredModules = () => {
    const modules = detail?.registered_modules;
    const hasModules = Array.isArray(modules) && modules.length > 0;

    return (
      <Card>
        <button
          onClick={() => toggleSection('modules')}
          className="w-full flex items-center justify-between px-6 py-4 hover:bg-aegisaxiom-accent-muted transition-colors"
        >
          <span className="text-sm font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
            Registered Modules ({hasModules ? modules.length : 0})
          </span>
          {expandedSections.modules ? (
            <ChevronDown className="w-4 h-4 text-aegisaxiom-text-muted" />
          ) : (
            <ChevronRight className="w-4 h-4 text-aegisaxiom-text-muted" />
          )}
        </button>
        {expandedSections.modules && (
          <CardContent className="pt-0 pb-4">
            {!hasModules ? (
              <p className="text-xs text-aegisaxiom-text-muted px-1 py-2">
                No modules registered yet. Complete the field registration step to populate this list.
              </p>
            ) : (
              <div className="overflow-x-auto -mx-2 sm:mx-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Module</TableHead>
                      <TableHead>Entity</TableHead>
                      <TableHead className="text-right">Fields</TableHead>
                      <TableHead className="text-right">Routes</TableHead>
                      <TableHead className="text-right">Perms</TableHead>
                      <TableHead>Route Prefix</TableHead>
                      <TableHead>Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {modules.map((mod) => (
                      <TableRow key={mod.module_id || mod.name}>
                        <TableCell className="font-medium text-aegisaxiom-text">{mod.name}</TableCell>
                        <TableCell className="text-aegisaxiom-text-secondary">{mod.entity_name}</TableCell>
                        <TableCell className="text-right tabular-nums text-aegisaxiom-text-secondary">
                          {mod.field_count ?? 0}
                        </TableCell>
                        <TableCell className="text-right tabular-nums text-aegisaxiom-text-secondary">
                          {mod.route_count ?? 0}
                        </TableCell>
                        <TableCell className="text-right tabular-nums text-aegisaxiom-text-secondary">
                          {mod.permission_count ?? 0}
                        </TableCell>
                        <TableCell>
                          <code className="text-xs bg-aegisaxiom-elevated border border-aegisaxiom-border text-aegisaxiom-text px-1.5 py-0.5 rounded font-mono">
                            {mod.route_prefix}
                          </code>
                        </TableCell>
                        <TableCell>
                          <Badge
                            className={
                              mod.status === 'active'
                                ? STATUS_COLORS.active
                                : STATUS_COLORS.pending
                            }
                          >
                            {mod.status}
                          </Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        )}
      </Card>
    );
  };

  const renderPipeline = () => {
    if (!detail?.pipeline) return null;
    return (
      <Card>
        <button
          onClick={() => toggleSection('pipeline')}
          className="w-full flex items-center justify-between px-6 py-4 hover:bg-aegisaxiom-accent-muted transition-colors"
        >
          <span className="text-sm font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
            Pipeline Progress
          </span>
          {expandedSections.pipeline ? (
            <ChevronDown className="w-4 h-4 text-aegisaxiom-text-muted" />
          ) : (
            <ChevronRight className="w-4 h-4 text-aegisaxiom-text-muted" />
          )}
        </button>
        {expandedSections.pipeline && (
          <CardContent className="pt-0 pb-4">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              {detail.pipeline.map(({ stage, status }) => (
                <div
                  key={stage}
                  className="flex items-center justify-between gap-2 px-2.5 py-2 rounded-md bg-aegisaxiom-elevated border border-aegisaxiom-border text-xs"
                  data-testid={`pipeline-stage-${stage}`}
                >
                  <div className="flex items-center gap-1.5 min-w-0">
                    {STAGE_ICONS[status] || STAGE_ICONS.pending}
                    <span
                      className={
                        status === 'pending' ? 'text-aegisaxiom-text-muted truncate' : 'text-aegisaxiom-text truncate'
                      }
                    >
                      {stage.replace(/_/g, ' ')}
                    </span>
                  </div>
                  {status === 'failed' && (
                    <button
                      onClick={() => openConfirmRerun(stage)}
                      className="shrink-0 p-1 rounded hover:bg-aegisaxiom-accent-muted text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition"
                      title={`Rerun / resolve step "${stage}"`}
                      aria-label={`Rerun step ${stage}`}
                      data-testid={`rerun-step-${stage}`}
                    >
                      <RotateCcw className="w-3 h-3" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        )}
      </Card>
    );
  };

  const renderActivityLog = () => {
    // Show all_logs if available, else fall back to recent_logs
    const logs = detail?.all_logs || detail?.recent_logs;
    const logCount = logs?.length ?? 0;

    return (
      <Card>
        <button
          onClick={() => toggleSection('logs')}
          className="w-full flex items-center justify-between px-6 py-4 hover:bg-aegisaxiom-accent-muted transition-colors"
        >
          <span className="text-sm font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
            Activity Log ({logCount})
          </span>
          {expandedSections.logs ? (
            <ChevronDown className="w-4 h-4 text-aegisaxiom-text-muted" />
          ) : (
            <ChevronRight className="w-4 h-4 text-aegisaxiom-text-muted" />
          )}
        </button>
        {expandedSections.logs && logCount > 0 && (
          <CardContent className="pt-0 pb-4">
            <div>
              {logs.map((log, i) => {
                const isResolved = !!log.resolved_by;
                return (
                  <div
                    key={log.log_id || i}
                    className={`flex items-start justify-between gap-3 py-2.5 border-b border-aegisaxiom-border last:border-0 text-xs ${
                      isResolved ? 'opacity-50' : ''
                    }`}
                    data-testid={isResolved ? `log-resolved-${log.log_id}` : undefined}
                  >
                    <div className="flex items-start gap-2 min-w-0 flex-1">
                      {log.status === 'success' ? (
                        <CheckCircle2 className="w-3.5 h-3.5 text-green-600 dark:text-green-400 shrink-0 mt-0.5" />
                      ) : (
                        <XCircle className="w-3.5 h-3.5 text-red-600 dark:text-red-400 shrink-0 mt-0.5" />
                      )}
                      <div className="min-w-0">
                        <span
                          className={`font-medium text-aegisaxiom-text ${isResolved ? 'line-through' : ''}`}
                        >
                          {log.action}
                        </span>
                        {isResolved && (
                          <span className="ml-2 text-[10px] text-aegisaxiom-text-muted not-italic bg-aegisaxiom-elevated border border-aegisaxiom-border px-1 py-0.5 rounded">
                            resolved
                          </span>
                        )}
                        <div className="flex items-center gap-2 mt-0.5">
                          <Badge variant="outline" className="text-[10px] shrink-0">
                            {log.stage}
                          </Badge>
                          {log.details?.error && (
                            <span className="text-red-500 dark:text-red-400 truncate max-w-xs">
                              {log.details.error}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                    {log.duration_ms && (
                      <span className="text-aegisaxiom-text-muted font-mono shrink-0">
                        {log.duration_ms}ms
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </CardContent>
        )}
      </Card>
    );
  };

  // ==================== MAIN RENDER ====================

  return (
    <>
      <ConfirmModal
        isOpen={confirm.open}
        onConfirm={onConfirm}
        onCancel={closeConfirm}
        title={confirmModalProps.title}
        message={confirmModalProps.message}
        confirmText={confirmModalProps.confirmText}
        variant={confirmModalProps.variant}
      />

      <div className="space-y-6" data-testid="app-ingestion-admin">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold text-aegisaxiom-text flex items-center gap-2">
              <Package size={22} className="text-violet-600 dark:text-violet-400" />
              App Ingestion
            </h1>
            <p className="text-sm text-aegisaxiom-text-muted mt-1">
              Manage imported applications and their integration status
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={fetchIngestions}
            disabled={loading}
            data-testid="refresh-ingestions"
            className="w-full sm:w-auto shrink-0"
          >
            <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left: Ingestion List */}
          <div className="lg:col-span-1">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-semibold uppercase tracking-wider text-aegisaxiom-text-muted">
                  Ingested Apps ({ingestions.length})
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                {loading ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="w-5 h-5 animate-spin text-aegisaxiom-text-muted" />
                  </div>
                ) : ingestions.length === 0 ? (
                  <div className="px-4 py-10 text-center">
                    <div className="w-11 h-11 rounded-full bg-violet-100 dark:bg-violet-600/15 flex items-center justify-center mx-auto mb-3">
                      <Inbox className="w-5 h-5 text-violet-600 dark:text-violet-300" />
                    </div>
                    <p className="text-aegisaxiom-text font-medium text-sm mb-1">No apps ingested yet</p>
                    <p className="text-xs text-aegisaxiom-text-muted leading-relaxed">
                      Use{' '}
                      <code className="bg-aegisaxiom-elevated border border-aegisaxiom-border text-aegisaxiom-text px-1.5 py-0.5 rounded font-mono">
                        /ingest-app
                      </code>{' '}
                      to import one.
                    </p>
                  </div>
                ) : (
                  <div className="divide-y divide-aegisaxiom-border">
                    {ingestions.map((app) => {
                      const isSelected = selectedApp === app.app_id;
                      return (
                        <button
                          key={app.app_id}
                          onClick={() => setSelectedApp(app.app_id)}
                          className={`w-full text-left px-4 py-3 hover:bg-aegisaxiom-accent-muted transition-colors ${
                            isSelected
                              ? 'bg-violet-50 dark:bg-violet-600/10 border-l-2 border-violet-600 dark:border-violet-400'
                              : 'border-l-2 border-transparent'
                          }`}
                          data-testid={`ingestion-row-${app.slug}`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span
                              className={`font-medium text-sm truncate ${isSelected ? 'text-violet-700 dark:text-violet-300' : 'text-aegisaxiom-text'}`}
                            >
                              {app.name}
                            </span>
                            <Badge
                              className={`text-xs shrink-0 ${STATUS_COLORS[app.status] || STATUS_COLORS.pending}`}
                            >
                              {app.status}
                            </Badge>
                          </div>
                          <span className="text-xs text-aegisaxiom-text-muted mt-1 block font-mono truncate">
                            {app.slug}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          {/* Right: Detail View */}
          <div className="lg:col-span-2">
            {!selectedApp ? (
              <Card>
                <CardContent className="py-16 px-6 text-center">
                  <div className="w-12 h-12 rounded-full bg-aegisaxiom-elevated border border-aegisaxiom-border flex items-center justify-center mx-auto mb-3">
                    <Package className="w-5 h-5 text-aegisaxiom-text-muted" />
                  </div>
                  <p className="text-aegisaxiom-text font-medium text-sm mb-1">No app selected</p>
                  <p className="text-sm text-aegisaxiom-text-muted">
                    Select an app from the list to view its ingestion status
                  </p>
                </CardContent>
              </Card>
            ) : detailLoading ? (
              <Card>
                <CardContent className="py-16 flex items-center justify-center">
                  <Loader2 className="w-6 h-6 animate-spin text-aegisaxiom-text-muted" />
                </CardContent>
              </Card>
            ) : detail ? (
              <div className="space-y-4">
                {/* App Header */}
                <Card>
                  <CardContent className="pt-6">
                    <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                      <div className="min-w-0">
                        <h2 className="text-xl font-bold text-aegisaxiom-text truncate">
                          {detail.app?.name}
                        </h2>
                        <p className="text-sm text-aegisaxiom-text-muted mt-1">
                          {detail.app?.description || 'No description'}
                        </p>
                        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 mt-3 text-xs text-aegisaxiom-text-muted">
                          <span>
                            Slug:{' '}
                            <code className="bg-aegisaxiom-elevated border border-aegisaxiom-border text-aegisaxiom-text px-1.5 py-0.5 rounded font-mono">
                              {detail.app?.slug}
                            </code>
                          </span>
                          <span>Version: {detail.app?.ingestion_version}</span>
                          {detail.app?.source_commit_sha && (
                            <span>
                              SHA:{' '}
                              <code className="bg-aegisaxiom-elevated border border-aegisaxiom-border text-aegisaxiom-text px-1.5 py-0.5 rounded font-mono">
                                {detail.app.source_commit_sha?.substring(0, 8)}
                              </code>
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="flex flex-col items-end gap-2 shrink-0">
                        <Badge
                          className={`${STATUS_COLORS[detail.app?.status] || STATUS_COLORS.pending}`}
                        >
                          {detail.app?.status}
                        </Badge>
                        {renderActionButtons()}
                      </div>
                    </div>

                    {/* Rejection reason input — visible only when awaiting_review */}
                    {renderRejectReasonInput()}

                    {/* Summary Stat Cards — reads from actual_counts */}
                    {renderStatCards()}
                  </CardContent>
                </Card>

                {/* Pipeline Progress */}
                {renderPipeline()}

                {/* Registered Modules */}
                {renderRegisteredModules()}

                {/* Activity Log */}
                {renderActivityLog()}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </>
  );
}
