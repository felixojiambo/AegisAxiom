import React, { useEffect, useMemo, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import {
  X,
  AlertTriangle,
  ChevronDown,
  CheckCircle2,
  XCircle,
  UserPlus,
  FilePlus,
  Pencil,
  Clipboard,
  Loader2,
} from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';
import { FieldGrid } from '@/components/match/board/FieldGrid';
import { FIELDS_BY_TAB, TABS } from '@/lib/match/contractH';
import { FlagChip } from '@/components/match/board/FlagChip';
import { ConfidenceBar } from '@/components/match/board/ConfidenceBar';
import CreateNewCustomerModal from '@/components/match/board/CreateNewCustomerModal';
import CreateNewOrderModal from '@/components/match/board/CreateNewOrderModal';
import PushHistorySection from '@/components/match/board/PushHistorySection';
import ScoreBreakdown from '@/components/match/board/ScoreBreakdown';
import CandidatesConsidered from '@/components/match/board/CandidatesConsidered';

// ── Helpers ───────────────────────────────────────────────────────────────────

function getDot(obj, path) {
  if (!obj || !path) return undefined;
  return path.split('.').reduce((acc, k) => (acc == null ? acc : acc[k]), obj);
}

function setDot(obj, path, value) {
  const parts = path.split('.');
  const last  = parts.pop();
  const next  = JSON.parse(JSON.stringify(obj || {}));
  let cur     = next;
  for (const p of parts) {
    if (!(p in cur) || typeof cur[p] !== 'object' || cur[p] === null) cur[p] = {};
    cur = cur[p];
  }
  cur[last] = value;
  return next;
}

// Status display helpers ──────────────────────────────────────────────────────

const STATUS_LABEL = {
  ready:            'Ready',
  ready_to_draft:   'Ready to Draft',
  needs_review:     'Needs Review',
  approved:         'Approved',
  edited:           'Edited',
  rejected:         'Rejected',
  escalated:        'Escalated',
  create_new:       'Create New',
  create_new_order: 'Create New Order',
  error:            'Error',
};

function statusClass(status) {
  switch (status) {
    case 'approved':
    case 'edited':
    case 'create_new':
    case 'create_new_order':
      return 'bg-aegisaxiom-status-success-bg text-aegisaxiom-status-success border-aegisaxiom-status-success/30';
    case 'needs_review':
      return 'bg-aegisaxiom-status-warn-bg text-aegisaxiom-status-warn border-aegisaxiom-status-warn/30';
    case 'rejected':
    case 'error':
      return 'bg-aegisaxiom-status-danger-bg text-aegisaxiom-status-danger border-aegisaxiom-status-danger/30';
    case 'escalated':
      return 'bg-aegisaxiom-status-warn-bg text-aegisaxiom-status-warn border-aegisaxiom-status-warn/30';
    default:
      return 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted border-aegisaxiom-border';
  }
}

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * RowDetailDrawer — schema-driven 5-tab side-by-side match review.
 *
 * Props:
 *   open          {boolean}        — controlled by BoardTab
 *   onOpenChange  {(boolean)=>void}
 *   rowId         {string|null}    — m_row_… or null
 *   onActioned    {()=>void}       — parent refetches rows after action
 */
export default function RowDetailDrawer({ open, onOpenChange, rowId, onActioned }) {
  // ── Data ────────────────────────────────────────────────────────────────────
  const [row,       setRow]       = useState(null);
  const [edited,    setEdited]    = useState(null);
  const [loading,   setLoading]   = useState(false);
  const [activeTab, setActiveTab] = useState('top_level');

  // ── Action state ────────────────────────────────────────────────────────────
  const [submitting, setSubmitting] = useState(false);

  // Reason dialog (Dismiss)
  const [reasonDialog, setReasonDialog] = useState({ open: false, action: null });
  const [reasonText,   setReasonText]   = useState('');

  // Create New Customer modal
  const [createCustomerModal, setCreateCustomerModal] = useState({
    open: false, draft: null,
  });

  // Create New Order modal
  const [createOrderModal, setCreateOrderModal] = useState({
    open: false, draft: null,
  });

  const { hasPermission } = usePermissions();

  // ── Fetch row on open ───────────────────────────────────────────────────────
  useEffect(() => {
    if (!open || !rowId) return;
    let cancelled = false;
    setLoading(true);
    (async () => {
      const res = await api.get(`${API_URL}/api/match/rows/${rowId}`);
      if (cancelled) return;
      if (res.ok) {
        const data = await res.json();
        setRow(data);
        setEdited(structuredClone(data.output || {}));
      } else {
        const err = await res.json();
        toast.error(err?.detail || 'Failed to load row');
      }
      setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [open, rowId]);

  // ── Derived ─────────────────────────────────────────────────────────────────
  const isEdited = useMemo(() => {
    if (!row || !edited) return false;
    return JSON.stringify(row.output || {}) !== JSON.stringify(edited);
  }, [row, edited]);

  const isNewCustomer = useMemo(() => {
    if (!row) return false;
    return (
      (row.audit_metadata?.flags || []).includes('new_customer') ||
      !row.audit_metadata?.matched_candidate_id
    );
  }, [row]);

  const matchedCandidateId = row?.audit_metadata?.matched_candidate_id || null;

  const detailId = row
    ? `DT-${String(row.row_index ?? 0).padStart(6, '0')}`
    : '';

  // Header address — prefer AI-populated location, fall back to source row
  const headerAddress = useMemo(() => {
    if (!row) return null;
    const out = row.output || {};
    const src = row.source_row || {};
    const street =
      getDot(out, 'top_level.location.street') ||
      src['Billing Address'] || src['Service Address'] || src['Street'] || src['street'];
    const city =
      getDot(out, 'top_level.location.city') ||
      src['Billing City'] || src['Service City'] || src['City'] || src['city'];
    const state =
      getDot(out, 'top_level.location.state') ||
      src['Billing State/Prov'] || src['Service State'] || src['State'] || src['state'];
    const zip =
      getDot(out, 'top_level.location.zip') ||
      src['Billing Zip'] || src['Service Zip'] || src['Zip'] || src['ZIP'] || src['zip'];
    const parts = [street, [city, state].filter(Boolean).join(', '), zip].filter(Boolean);
    return parts.length > 0 ? parts.join(' ') : null;
  }, [row]);

  // ── Reset helper ─────────────────────────────────────────────────────────────
  function reset() {
    setRow(null);
    setEdited(null);
    setReasonText('');
    setActiveTab('top_level');
    setReasonDialog({ open: false, action: null });
    setCreateCustomerModal({ open: false, draft: null });
    setCreateOrderModal({ open: false, draft: null });
  }

  // ── Action handler ──────────────────────────────────────────────────────────
  async function handleAction(action, extras = {}) {
    if (!rowId) return;
    setSubmitting(true);
    try {
      const body = { action, ...extras };
      if (isEdited && !body.edited_output) {
        body.edited_output = edited;
      }
      const res = await api.post(`${API_URL}/api/match/rows/${rowId}/action`, body);
      if (res.ok) {
        toast.success(actionSuccessMsg(action));
        onActioned?.();
        onOpenChange(false);
        reset();
      } else {
        const err = await res.json();
        toast.error(err?.detail || 'Action failed');
      }
    } catch (e) {
      toast.error('Action failed');
    } finally {
      setSubmitting(false);
    }
  }

  function actionSuccessMsg(action) {
    switch (action) {
      case 'approved':      return 'Row approved';
      case 'edited':        return 'Row approved with edits';
      case 'rejected':      return 'Row dismissed';
      case 'create_new':    return 'Create new customer submitted';
      case 'create_new_order': return 'Create new order submitted';
      default:              return 'Action recorded';
    }
  }

  // ── Dismiss (reason dialog) ─────────────────────────────────────────────────
  function openReasonDialog(action) {
    setReasonText('');
    setReasonDialog({ open: true, action });
  }

  function submitReasonAction() {
    if (reasonDialog.action === 'rejected' && !reasonText.trim()) {
      toast.error('Please provide a dismissal reason.');
      return;
    }
    const action = reasonDialog.action;
    setReasonDialog({ open: false, action: null });
    handleAction(action, { reason: reasonText });
  }

  // ── Create New Customer ──────────────────────────────────────────────────────
  function openCreateCustomerModal() {
    if (!row) return;
    setCreateCustomerModal({
      open:  true,
      draft: row.output?.top_level || {},
    });
  }

  async function handleCustomerConfirm(editedDraft) {
    // editedDraft is flat: { name, advisor_company, sales_rep, street, city, state, zip }
    // Rebuild the nested top_level shape and merge into edited output.
    const nestedTopLevel = {
      advisor_company: editedDraft.advisor_company,
      sales_rep:       editedDraft.sales_rep,
      customer:        { name: editedDraft.name },
      location: {
        street: editedDraft.street,
        city:   editedDraft.city,
        state:  editedDraft.state,
        zip:    editedDraft.zip,
      },
    };
    const newEdited = { ...(edited || {}), top_level: nestedTopLevel };
    setEdited(newEdited);
    setCreateCustomerModal({ open: false, draft: null });
    await handleAction('create_new', {
      edited_output: newEdited,
    });
  }

  // ── Create New Order ─────────────────────────────────────────────────────────
  function openCreateOrderModal() {
    if (!row) return;
    setCreateOrderModal({
      open:  true,
      draft: row.output?.order_details || {},
    });
  }

  async function handleOrderConfirm(editedDraft) {
    const newEdited = {
      ...(edited || {}),
      order_details: { ...(edited?.order_details || {}), ...editedDraft },
    };
    setEdited(newEdited);
    setCreateOrderModal({ open: false, draft: null });
    await handleAction('create_new_order', {
      edited_output: newEdited,
    });
  }

  // ── Render ───────────────────────────────────────────────────────────────────
  return (
    <>
      <Dialog
        open={open}
        onOpenChange={(v) => {
          onOpenChange(v);
          if (!v) reset();
        }}
      >
        <DialogContent
          className="!max-w-5xl w-[90vw] max-h-[92vh] p-0 flex flex-col bg-aegisaxiom-surface border border-aegisaxiom-border text-aegisaxiom-text overflow-hidden"
          data-testid="row-detail-drawer"
        >
          {/* ── Header ─────────────────────────────────────────────────────── */}
          <DialogHeader className="px-6 py-4 border-b border-aegisaxiom-border shrink-0">
            <div className="flex items-start justify-between gap-4">
              {/* Left: row title + meta */}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 mb-1">
                  {/* Row N label */}
                  <span className="text-[12px] font-medium text-aegisaxiom-text-muted font-mono">
                    Row {row?.row_index ?? '…'}
                  </span>
                  {/* Status pill */}
                  {row && (
                    <span
                      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${statusClass(row.status)}`}
                      data-testid="row-detail-status-pill"
                    >
                      {STATUS_LABEL[row.status] || row.status}
                    </span>
                  )}
                  {/* row_id in mono */}
                  {row && (
                    <span
                      className="text-[10px] font-mono text-aegisaxiom-text-muted hidden sm:inline"
                      data-testid="row-detail-row-id"
                    >
                      {row.row_id}
                    </span>
                  )}
                  {row?.session_id && (
                    <span className="text-[10px] font-mono text-aegisaxiom-text-muted hidden md:inline">
                      Session: {row.session_id}
                    </span>
                  )}
                </div>

                {/* Address title */}
                <DialogTitle
                  className="text-[18px] font-semibold leading-tight text-aegisaxiom-text truncate"
                  data-testid="row-detail-header-address"
                >
                  {row
                    ? headerAddress ||
                      getDot(row.output, 'top_level.customer.name') ||
                      `Row #${row.row_index}`
                    : 'Loading…'}
                </DialogTitle>

                {/* Provider · Product · Customer trio */}
                <DialogDescription asChild>
                  <div>
                    {row && (
                      <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[12px]">
                        <MetaBadge label="Provider" value={getDot(row.output, 'order_details.provider')} />
                        <MetaBadge label="Product"  value={getDot(row.output, 'order_details.product')} />
                        <MetaBadge label="Customer" value={getDot(row.output, 'top_level.customer.name')} />
                      </div>
                    )}
                  </div>
                </DialogDescription>
              </div>

              {/* Right: PSO pill + Detail ID + Confidence + close */}
              {row && (
                <div className="flex flex-col items-end gap-2 shrink-0">
                  {/* Provider Sales Order ID */}
                  <PsoCopyPill value={getDot(row.output, 'order_details.provider_order_id')} />

                  {/* Detail ID */}
                  <span
                    className="text-[11px] font-mono uppercase tracking-wider text-aegisaxiom-text-muted"
                    data-testid="row-detail-header-detail-id"
                  >
                    Detail ID: {detailId}
                  </span>

                  {/* Confidence bar */}
                  <div className="flex items-center gap-2">
                    <ConfidenceBar
                      score={row.audit_metadata?.confidence_score ?? 0}
                      rowId={row.row_id}
                      size="md"
                    />
                    {(row.audit_metadata?.flags || []).includes('needs_review') && (
                      <span className="inline-flex items-center gap-1 rounded-full border border-aegisaxiom-status-warn/40 bg-aegisaxiom-status-warn-bg px-2 py-0.5 text-[11px] text-aegisaxiom-status-warn">
                        <AlertTriangle className="h-3 w-3" />
                        Review
                      </span>
                    )}
                  </div>
                </div>
              )}
            </div>
          </DialogHeader>

          {/* ── Scrollable body ────────────────────────────────────────────── */}
          <div className="flex-1 overflow-y-auto">
            {loading || !row ? (
              <div className="flex items-center justify-center py-16 text-aegisaxiom-text-muted">
                <Loader2 className="h-5 w-5 animate-spin mr-2" />
                <span className="text-[13px]">Loading row…</span>
              </div>
            ) : (
              <div className="px-6 py-4 space-y-4">
                {/* Review reason banner */}
                {row.review_reason && (
                  <div
                    className="rounded-lg border border-aegisaxiom-status-warn/40 bg-aegisaxiom-status-warn-bg px-3 py-2.5 text-[13px] text-aegisaxiom-status-warn flex gap-2 items-start"
                    data-testid="row-detail-review-reason"
                  >
                    <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
                    <div>
                      <div className="font-semibold mb-0.5">Needs review</div>
                      <div>{row.review_reason}</div>
                    </div>
                  </div>
                )}

                {/* Flag chips */}
                {row.flags?.length > 0 && (
                  <div className="flex flex-wrap gap-1.5" data-testid="row-detail-flags">
                    {row.flags.map((f) => (
                      <FlagChip key={f} flag={f} rowId={row.row_id} />
                    ))}
                  </div>
                )}

                {/* Score breakdown */}
                <ScoreBreakdown row={row} />

                {/* 5-tab comparison grid */}
                <Tabs
                  value={activeTab}
                  onValueChange={setActiveTab}
                  className="w-full"
                >
                  <TabsList
                    className="h-9 bg-aegisaxiom-elevated border border-aegisaxiom-border"
                    data-testid="row-detail-tabs"
                  >
                    {TABS.map((t) => (
                      <TabsTrigger
                        key={t.key}
                        value={t.key}
                        className="text-[12px] data-[state=active]:bg-aegisaxiom-surface data-[state=active]:text-aegisaxiom-text text-aegisaxiom-text-muted"
                        data-testid={`row-detail-tab-${t.key}`}
                      >
                        {t.label}
                      </TabsTrigger>
                    ))}
                  </TabsList>

                  {TABS.map((t) => (
                    <TabsContent key={t.key} value={t.key} className="pt-4">
                      <FieldGrid
                        fields={FIELDS_BY_TAB[t.key] || []}
                        source={row.source_row || {}}
                        edited={edited || {}}
                        original={row.output || {}}
                        flags={row.audit_metadata?.flags || []}
                        isNewCustomer={isNewCustomer}
                        matchedCandidateId={matchedCandidateId}
                        onChange={(path, value) =>
                          setEdited((cur) => setDot(cur || {}, path, value))
                        }
                      />
                    </TabsContent>
                  ))}
                </Tabs>

                {/* Candidates considered (collapsible) */}
                {Array.isArray(row.audit_metadata?.candidates_considered) &&
                  row.audit_metadata.candidates_considered.length > 0 && (
                    <Collapsible defaultOpen={false}>
                      <CollapsibleTrigger asChild>
                        <button
                          type="button"
                          className="flex items-center gap-2 text-[13px] text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition-colors"
                          data-testid="row-detail-candidates-toggle"
                        >
                          <ChevronDown className="h-4 w-4" />
                          Candidates considered (
                          {row.audit_metadata.candidates_considered.length})
                        </button>
                      </CollapsibleTrigger>
                      <CollapsibleContent>
                        <div className="mt-3">
                          <CandidatesConsidered
                            candidates={row.audit_metadata.candidates_considered}
                            flags={row.audit_metadata?.flags || []}
                          />
                        </div>
                      </CollapsibleContent>
                    </Collapsible>
                  )}

                {/* Push history */}
                <div className="pt-2">
                  <PushHistorySection rowId={row.row_id} />
                </div>
              </div>
            )}
          </div>

          {/* ── Sticky action bar ──────────────────────────────────────────── */}
          {row && (
            <div className="shrink-0 border-t border-aegisaxiom-border bg-aegisaxiom-surface px-6 py-4 flex items-center justify-between gap-3">
              {/* Edited indicator */}
              <div className="text-[12px] text-aegisaxiom-text-muted">
                {isEdited ? (
                  <span className="inline-flex items-center gap-1 text-aegisaxiom-status-info">
                    <Pencil className="h-3 w-3" />
                    Edited — changes saved on Confirm
                  </span>
                ) : (
                  <span className="text-aegisaxiom-text-muted">Pristine</span>
                )}
              </div>

              {/* Action buttons — gated on match.row.change_status (the server-side
                  slug that enforces all four mutations). Backend enforces too; the
                  client gate is defense-in-depth + visibly disables for view-only. */}
              <div className="flex items-center gap-2 flex-wrap justify-end">
                {/* Dismiss */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => openReasonDialog('rejected')}
                  disabled={submitting || !hasPermission('match.row.change_status')}
                  className="border-aegisaxiom-status-danger/40 text-aegisaxiom-status-danger hover:bg-aegisaxiom-status-danger-bg"
                  data-testid="row-detail-reject-button"
                >
                  <XCircle className="h-4 w-4 mr-1" />
                  Dismiss
                </Button>

                {/* Create New Customer */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={openCreateCustomerModal}
                  disabled={submitting || !hasPermission('match.row.change_status')}
                  className="border-aegisaxiom-status-info/40 text-aegisaxiom-status-info hover:bg-aegisaxiom-status-info-bg"
                  data-testid="row-detail-create-new-button"
                >
                  <UserPlus className="h-4 w-4 mr-1" />
                  {isNewCustomer ? 'Create New Customer' : 'No Match — Create Customer'}
                </Button>

                {/* Create New Order */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={openCreateOrderModal}
                  disabled={submitting || !hasPermission('match.row.change_status')}
                  className="border-aegisaxiom-status-info/40 text-aegisaxiom-status-info hover:bg-aegisaxiom-status-info-bg"
                  data-testid="row-detail-create-new-order-button"
                >
                  <FilePlus className="h-4 w-4 mr-1" />
                  Create New Order
                </Button>

                {/* Confirm Match — Enrich */}
                <Button
                  size="sm"
                  onClick={() => handleAction(isEdited ? 'edited' : 'approved')}
                  disabled={submitting || !hasPermission('match.row.change_status')}
                  className="bg-aegisaxiom-status-success text-white hover:bg-aegisaxiom-status-success/90"
                  data-testid="row-detail-approve-button"
                >
                  {submitting ? (
                    <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                  ) : isEdited ? (
                    <Pencil className="h-4 w-4 mr-1" />
                  ) : (
                    <CheckCircle2 className="h-4 w-4 mr-1" />
                  )}
                  Confirm Match — Enrich
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ── Reason dialog (Dismiss) ─────────────────────────────────────────── */}
      <Dialog
        open={reasonDialog.open}
        onOpenChange={(v) => setReasonDialog({ open: v, action: reasonDialog.action })}
      >
        <DialogContent
          className="sm:max-w-[480px] bg-aegisaxiom-surface border border-aegisaxiom-border text-aegisaxiom-text"
          data-testid="row-detail-reason-dialog"
        >
          <DialogHeader>
            <DialogTitle className="text-aegisaxiom-text">
              {reasonDialog.action === 'rejected' ? 'Dismiss row' : 'Reason'}
            </DialogTitle>
            <DialogDescription className="text-aegisaxiom-text-secondary">
              Add a brief reason. This will be recorded with the action so the AI can learn.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            value={reasonText}
            onChange={(e) => setReasonText(e.target.value)}
            placeholder="Reason…"
            className="aegisaxiom-input min-h-[120px]"
            data-testid="row-detail-reason-input"
          />
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setReasonDialog({ open: false, action: null })}
              className="border-aegisaxiom-border text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
            >
              Cancel
            </Button>
            <Button
              onClick={submitReasonAction}
              className="btn-primary"
              data-testid="row-detail-reason-submit"
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Create New Customer modal ───────────────────────────────────────── */}
      <CreateNewCustomerModal
        open={createCustomerModal.open}
        onOpenChange={(v) => !v && setCreateCustomerModal({ open: false, draft: null })}
        draft={createCustomerModal.draft}
        submitting={submitting}
        onConfirm={handleCustomerConfirm}
      />

      {/* ── Create New Order modal ──────────────────────────────────────────── */}
      <CreateNewOrderModal
        open={createOrderModal.open}
        onOpenChange={(v) => !v && setCreateOrderModal({ open: false, draft: null })}
        draft={createOrderModal.draft}
        submitting={submitting}
        onConfirm={handleOrderConfirm}
      />
    </>
  );
}

// ── Internal sub-components ───────────────────────────────────────────────────

function MetaBadge({ label, value }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-aegisaxiom-elevated border border-aegisaxiom-border px-2 py-0.5 text-[12px] text-aegisaxiom-text-muted">
      {label}:{' '}
      <strong className="font-medium text-aegisaxiom-text">{value || '—'}</strong>
    </span>
  );
}

function PsoCopyPill({ value }) {
  const hasPso = value != null && value !== '';
  return (
    <div
      className={`flex items-baseline gap-2 rounded-lg border px-3 py-1.5 ${
        hasPso
          ? 'border-aegisaxiom-status-info/40 bg-aegisaxiom-status-info-bg'
          : 'border-dashed border-aegisaxiom-border bg-aegisaxiom-elevated/60'
      }`}
      data-testid="row-detail-header-pso"
    >
      <span className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted font-medium">
        Provider Sales Order ID
      </span>
      {hasPso ? (
        <button
          type="button"
          onClick={() => {
            navigator.clipboard.writeText(String(value)).catch(() => null);
            toast.success('Copied to clipboard');
          }}
          className="inline-flex items-center gap-1 font-mono text-[14px] font-semibold text-aegisaxiom-text hover:text-aegisaxiom-status-info transition-colors"
          data-testid="row-detail-pso-copy-btn"
          aria-label={`Copy Provider Sales Order ID ${value}`}
        >
          <Clipboard className="h-3 w-3" />
          {value}
        </button>
      ) : (
        <span className="font-mono text-[13px] text-aegisaxiom-text-muted italic">
          — pending —
        </span>
      )}
    </div>
  );
}
