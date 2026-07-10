import React, { useEffect, useState } from 'react';
import { X, ChevronDown, ChevronUp, CheckCircle, XCircle, Minus } from 'lucide-react';
import { formatDistanceToNow, parseISO, format } from 'date-fns';

function formatReceived(iso) {
  if (!iso) return '—';
  try {
    const date = parseISO(iso);
    return `${format(date, 'MMM d, yyyy h:mm a')} (${formatDistanceToNow(date, { addSuffix: true })})`;
  } catch {
    return iso;
  }
}

function MatchIcon({ val }) {
  if (val === true) return <CheckCircle size={14} className="text-green-400 inline mr-1" />;
  if (val === false) return <XCircle size={14} className="text-red-400 inline mr-1" />;
  return <Minus size={14} className="text-aegisaxiom-text-muted inline mr-1" />;
}

function ConfidenceBadge({ val }) {
  if (val == null) return <span className="text-aegisaxiom-text-muted">—</span>;
  const pct = Math.round(val * 100);
  let cls = 'bg-green-500/15 text-green-400';
  if (val < 0.7) cls = 'bg-red-500/15 text-red-400';
  else if (val < 0.85) cls = 'bg-amber-500/15 text-amber-400';
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${cls}`}>{pct}%</span>
  );
}

function CollapsibleSection({ title, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-aegisaxiom-border rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface transition-colors text-sm font-medium text-aegisaxiom-text"
      >
        <span>{title}</span>
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>
      {open && <div className="px-4 py-3 border-t border-aegisaxiom-border">{children}</div>}
    </div>
  );
}

function Row({ label, children }) {
  return (
    <div className="flex gap-3 py-1.5 border-b border-aegisaxiom-border last:border-0">
      <span className="text-xs text-aegisaxiom-text-secondary shrink-0 w-28">{label}</span>
      <span className="text-xs text-aegisaxiom-text flex-1">{children}</span>
    </div>
  );
}

export default function DecisionDrawer({ decision: d, onClose }) {
  const splits = Array.isArray(d.splits) ? d.splits : [];
  const flags = Array.isArray(d.flags) ? d.flags : [];
  const llmCalls = Array.isArray(d.llm_calls) ? d.llm_calls : [];

  // Close on Escape
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/50"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer panel */}
      <div
        className="fixed inset-y-0 right-0 z-50 w-full max-w-lg bg-aegisaxiom-surface border-l border-aegisaxiom-border shadow-2xl overflow-y-auto"
        role="dialog"
        aria-modal="true"
        aria-label="Decision details"
      >
        {/* Header */}
        <div className="sticky top-0 bg-aegisaxiom-surface border-b border-aegisaxiom-border px-5 py-4 flex items-center justify-between z-10">
          <div>
            <h2 className="text-sm font-semibold text-aegisaxiom-text">Decision Details</h2>
            <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">{formatReceived(d.received_at)}</p>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-aegisaxiom-elevated transition-colors text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <div className="px-5 py-5 space-y-5">
          {/* Email summary */}
          <section className="space-y-0">
            <h3 className="text-xs font-semibold text-aegisaxiom-text-secondary uppercase tracking-wider mb-2">
              Email
            </h3>
            <div className="glass-card p-3 text-sm space-y-0">
              <Row label="Subject">{d.subject || '—'}</Row>
              <Row label="Sender">{d.sender_address || '—'}</Row>
              <Row label="Distro">{d.distro || '—'}</Row>
              <Row label="Advisor match">{d.advisor_match || '—'}</Row>
              <Row label="Thread state">{d.thread_state || '—'}</Row>
            </div>
          </section>

          {/* AI decision */}
          <section>
            <h3 className="text-xs font-semibold text-aegisaxiom-text-secondary uppercase tracking-wider mb-2">
              AI Decision
            </h3>
            <div className="glass-card p-3 space-y-0">
              <Row label="Department">{d.ai_department || '—'}</Row>
              <Row label="Ticket type">{d.ai_ticket_type || '—'}</Row>
              <Row label="Owner">{d.ai_owner || '—'}</Row>
              <Row label="Confidence"><ConfidenceBadge val={d.ai_confidence} /></Row>
              <Row label="Dept source">{d.dept_source || '—'}</Row>
              <Row label="Type source">{d.type_source || '—'}</Row>
              <Row label="Owner mode">{d.owner_mode || '—'}</Row>
              {d.is_multi_request && (
                <Row label="Multi-request">
                  <span className="bg-amber-500/15 text-amber-400 px-1.5 py-0.5 rounded text-xs">
                    Yes
                  </span>
                </Row>
              )}
            </div>
          </section>

          {/* Reasoning */}
          {d.reasoning && (
            <section>
              <h3 className="text-xs font-semibold text-aegisaxiom-text-secondary uppercase tracking-wider mb-2">
                Reasoning
              </h3>
              <p className="text-sm text-aegisaxiom-text leading-relaxed bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg px-4 py-3">
                {d.reasoning}
              </p>
            </section>
          )}

          {/* Flags */}
          {flags.length > 0 && (
            <section>
              <h3 className="text-xs font-semibold text-aegisaxiom-text-secondary uppercase tracking-wider mb-2">
                Flags
              </h3>
              <div className="flex flex-wrap gap-1.5">
                {flags.map((f) => (
                  <span
                    key={f}
                    className="bg-amber-500/15 text-amber-400 px-2 py-0.5 rounded text-xs"
                  >
                    {f}
                  </span>
                ))}
              </div>
            </section>
          )}

          {/* Splits (multi-request) */}
          {splits.length > 0 && (
            <CollapsibleSection title={`Splits (${splits.length})`} defaultOpen>
              <div className="space-y-2">
                {splits.map((s, i) => (
                  <div key={i} className="bg-aegisaxiom-elevated rounded-lg p-3 text-xs space-y-1">
                    {s.provider && (
                      <p><span className="text-aegisaxiom-text-secondary">Provider:</span> <span className="text-aegisaxiom-text">{s.provider}</span></p>
                    )}
                    <p><span className="text-aegisaxiom-text-secondary">Dept:</span> <span className="text-aegisaxiom-text">{s.department}</span></p>
                    <p><span className="text-aegisaxiom-text-secondary">Ticket type:</span> <span className="text-aegisaxiom-text">{s.ticket_type}</span></p>
                    <p><span className="text-aegisaxiom-text-secondary">Confidence:</span> <ConfidenceBadge val={s.confidence} /></p>
                  </div>
                ))}
              </div>
            </CollapsibleSection>
          )}

          {/* Human reconciliation */}
          <section>
            <h3 className="text-xs font-semibold text-aegisaxiom-text-secondary uppercase tracking-wider mb-2">
              Human Review
            </h3>
            {(d.human_department || d.human_ticket_type || d.human_owner) ? (
              <div className="glass-card p-3 space-y-0">
                <Row label="H. Department">
                  <MatchIcon val={d.dept_match} />{d.human_department}
                </Row>
                <Row label="H. Ticket type">
                  <MatchIcon val={d.type_match} />{d.human_ticket_type}
                </Row>
                <Row label="H. Owner">
                  <MatchIcon val={d.owner_match} />{d.human_owner || '—'}
                </Row>
              </div>
            ) : (
              <p className="text-xs text-aegisaxiom-text-muted italic">
                Not yet reconciled — human routing decision pending.
              </p>
            )}
          </section>

          {/* LLM calls */}
          {llmCalls.length > 0 && (
            <CollapsibleSection title={`LLM Calls (${llmCalls.length})`}>
              <div className="space-y-3">
                {llmCalls.map((call, i) => (
                  <div key={i} className="space-y-2">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-aegisaxiom-text font-medium">{call.model || `Call ${i + 1}`}</span>
                      <span className="text-aegisaxiom-text-muted">
                        {call.tokens_in != null && `${call.tokens_in} in`}
                        {call.tokens_out != null && ` / ${call.tokens_out} out`}
                      </span>
                    </div>
                    {call.raw_response && (
                      <CollapsibleSection title="Raw response">
                        <pre className="text-xs text-aegisaxiom-text-secondary overflow-auto max-h-48 whitespace-pre-wrap break-all">
                          {typeof call.raw_response === 'string'
                            ? call.raw_response
                            : JSON.stringify(call.raw_response, null, 2)}
                        </pre>
                      </CollapsibleSection>
                    )}
                  </div>
                ))}
              </div>
            </CollapsibleSection>
          )}

          {/* SLA */}
          {d.sla_due_at && (
            <section>
              <h3 className="text-xs font-semibold text-aegisaxiom-text-secondary uppercase tracking-wider mb-2">
                SLA
              </h3>
              <p className="text-sm text-aegisaxiom-text">Due: {formatReceived(d.sla_due_at)}</p>
            </section>
          )}
        </div>
      </div>
    </>
  );
}
