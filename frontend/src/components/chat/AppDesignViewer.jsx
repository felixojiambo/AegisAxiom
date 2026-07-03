import React, { useState } from 'react';
import { Copy, Check, ChevronDown, ChevronUp, AlertCircle } from 'lucide-react';

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      } finally {
        document.body.removeChild(ta);
      }
    }
  };
  return (
    <button
      type="button"
      onClick={onCopy}
      disabled={!text}
      className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs font-medium transition-colors"
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
      {copied ? 'Copied' : 'Copy starter prompt'}
    </button>
  );
}

function AppCard({ app }) {
  const [expanded, setExpanded] = useState(false);
  const failed = app?.status === 'failed';

  return (
    <div className={`rounded-xl border ${failed ? 'border-red-500/30 bg-red-500/5' : 'border-aegisaxiom-border bg-aegisaxiom-elevated'} overflow-hidden`}>
      <div className="px-4 py-3 flex items-start justify-between gap-3 border-b border-aegisaxiom-border">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h4 className="text-sm font-semibold text-aegisaxiom-text truncate">{app.app_name || '(unnamed app)'}</h4>
            {failed && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-red-500/10 text-red-600 dark:text-red-400 text-[10px] font-medium uppercase tracking-wider">
                <AlertCircle size={10} /> Generation failed
              </span>
            )}
          </div>
          {app.app_pitch_summary && (
            <p className="text-xs text-aegisaxiom-text-secondary mt-1">{app.app_pitch_summary}</p>
          )}
          {app.primary_persona_hint && (
            <p className="text-xs text-aegisaxiom-text-muted mt-1">For: {app.primary_persona_hint}</p>
          )}
        </div>
        {!failed && <CopyButton text={app.starter_prompt} />}
      </div>

      {failed ? (
        <div className="px-4 py-3 text-xs text-red-600 dark:text-red-400">
          {app.failure_reason || 'Generation failed without a reason.'}
        </div>
      ) : (
        <>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="w-full px-4 py-2 flex items-center justify-between text-xs font-medium text-aegisaxiom-text-secondary hover:bg-aegisaxiom-accent-soft transition-colors"
          >
            <span>{expanded ? 'Hide details' : 'Show details'}</span>
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>

          {expanded && (
            <div className="px-4 py-3 space-y-3 text-xs border-t border-aegisaxiom-border max-h-[60vh] overflow-y-auto">
              {app.core_flows?.length > 0 && (
                <div>
                  <p className="font-semibold text-aegisaxiom-text mb-1">Core flows</p>
                  <ul className="list-disc list-inside text-aegisaxiom-text-secondary space-y-1">
                    {app.core_flows.map((f, i) => {
                      if (typeof f === 'string') return <li key={i}>{f}</li>;
                      if (!f || typeof f !== 'object') return <li key={i}>{String(f)}</li>;
                      const name = f.name || f.title || `Flow ${i + 1}`;
                      const success = f.success_when || f.success_criteria || f.acceptance_criteria;
                      return (
                        <li key={i}>
                          <span className="font-medium text-aegisaxiom-text">{name}</span>
                          {f.description ? <span> — {f.description}</span> : null}
                          <div className="ml-5 text-[11px] text-aegisaxiom-text-muted">
                            <strong>Success when:</strong>{' '}
                            {success || <em>(not captured)</em>}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}

              {app.data_model?.length > 0 && (
                <div>
                  <p className="font-semibold text-aegisaxiom-text mb-1">Data model</p>
                  <ul className="text-aegisaxiom-text-secondary space-y-0.5">
                    {app.data_model.map((e, i) => (
                      <li key={i}>
                        <span className="font-medium text-aegisaxiom-text">{e.entity}</span>
                        {e.key_fields?.length > 0 && <span> — {e.key_fields.join(', ')}</span>}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {app.integrations?.length > 0 && (
                <div>
                  <p className="font-semibold text-aegisaxiom-text mb-1">Integrations</p>
                  <ul className="list-disc list-inside text-aegisaxiom-text-secondary space-y-0.5">
                    {app.integrations.map((ig, i) => (
                      <li key={i}><span className="font-medium text-aegisaxiom-text">{ig.system}</span> — {ig.purpose}</li>
                    ))}
                  </ul>
                </div>
              )}

              {app.out_of_scope?.length > 0 && (
                <div>
                  <p className="font-semibold text-aegisaxiom-text mb-1">Out of scope</p>
                  <ul className="list-disc list-inside text-aegisaxiom-text-muted space-y-0.5">
                    {app.out_of_scope.map((o, i) => <li key={i}>{o}</li>)}
                  </ul>
                </div>
              )}

              {app.visual_style && (
                <div>
                  <p className="font-semibold text-aegisaxiom-text mb-1">Visual style</p>
                  <p className="text-aegisaxiom-text-secondary">{app.visual_style}</p>
                </div>
              )}

              {app.linked_workflow_ids?.length > 0 && (
                <div>
                  <p className="font-semibold text-aegisaxiom-text mb-1">Linked workflows</p>
                  <p className="text-aegisaxiom-text-muted">{app.linked_workflow_ids.join(', ')}</p>
                </div>
              )}

              {app.starter_prompt && (
                <div>
                  <p className="font-semibold text-aegisaxiom-text mb-1">Starter prompt</p>
                  <pre className="text-[11px] bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-3 overflow-x-auto whitespace-pre-wrap text-aegisaxiom-text-secondary">{app.starter_prompt}</pre>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function AppDesignViewer({ apps }) {
  if (!apps || apps.length === 0) {
    return (
      <p className="text-xs text-aegisaxiom-text-muted italic">
        No App Designs were surfaced in this session.
      </p>
    );
  }
  return (
    <div className="space-y-3">
      {apps.map((app, i) => (
        <AppCard key={app.app_id || `app_${i}`} app={app} />
      ))}
    </div>
  );
}
