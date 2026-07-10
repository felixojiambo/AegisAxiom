import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ChatArtifact from './chat/ChatArtifact';

const ARTIFACT_PATTERN = /(\[\[artifact:(art_\w+)\]\])/g;

export default function ChatMarkdown({ content }) {
  if (!content) return null;

  if (!content.includes('[[artifact:')) {
    return <MarkdownBlock content={content} />;
  }

  const parts = content.split(ARTIFACT_PATTERN);
  const elements = [];
  let i = 0;

  while (i < parts.length) {
    const part = parts[i];

    if (part && part.startsWith('[[artifact:')) {
      const artifactId = parts[i + 1];
      if (artifactId) {
        elements.push(<ChatArtifact key={`art-${artifactId}`} artifactId={artifactId} />);
      }
      i += 2; // Skip both the full marker and the captured group (two entries per split match)
    } else if (part && part.trim()) {
      elements.push(<MarkdownBlock key={`md-${i}`} content={part} />);
      i += 1;
    } else {
      i += 1;
    }
  }

  return <>{elements}</>;
}

function MarkdownBlock({ content }) {
  return (
    // Container hooks a class for any future scoped CSS overrides and
    // a `break-words` baseline so long unbroken tokens (URLs, dotted
    // identifiers like data_status.general_ledger.latest_synced_at)
    // wrap inside the bubble instead of pushing it wider.
    <div className="chat-message-content break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => (
            <p className="mb-2 last:mb-0 break-words">{children}</p>
          ),
          strong: ({ children }) => (
            <strong className="font-semibold text-aegisaxiom-text">{children}</strong>
          ),
          em: ({ children }) => (
            <em className="italic text-aegisaxiom-text-secondary">{children}</em>
          ),
          h1: ({ children }) => (
            <h1 className="text-base font-bold text-aegisaxiom-text mb-2 mt-3 first:mt-0 break-words">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-sm font-bold text-aegisaxiom-text mb-1.5 mt-2.5 first:mt-0 break-words">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-sm font-semibold text-aegisaxiom-text mb-1 mt-2 first:mt-0 break-words">{children}</h3>
          ),
          ul: ({ children }) => (
            <ul className="list-disc list-inside mb-2 space-y-0.5">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="list-decimal list-inside mb-2 space-y-0.5">{children}</ol>
          ),
          li: ({ children }) => (
            <li className="text-aegisaxiom-text-secondary break-words">{children}</li>
          ),
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 underline break-words"
            >
              {children}
            </a>
          ),
          // Code rendering split cleanly into inline vs block so the
          // <pre><code>…</code></pre> shape react-markdown emits for
          // fenced blocks doesn't double-style or trap long tokens.
          //
          // Inline: short metadata / field names. Padding sized so a
          // single short token ("accrual") doesn't render as a tiny
          // empty-looking box; break-words wraps long technical
          // identifiers without forcing horizontal scroll on the
          // bubble.
          code: ({ inline, className, children }) => {
            if (inline) {
              return (
                <code className="bg-aegisaxiom-elevated text-violet-700 dark:text-violet-300 px-1.5 py-0.5 rounded text-[0.85em] font-mono break-words">
                  {children}
                </code>
              );
            }
            // Block code path: <pre> below owns the container styling.
            // Keep this minimal so multi-line code stays readable and
            // we don't double-render padding/borders.
            return (
              <code className={`text-xs font-mono ${className || ''}`}>
                {children}
              </code>
            );
          },
          // Real fenced code block container. overflow-x-auto handles
          // wide lines (real source code) without pushing the bubble
          // wider; max-w-full + min-w-0 keep the scroll INSIDE the
          // bubble in flex layouts.
          pre: ({ children }) => (
            <pre className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-3 my-2 overflow-x-auto max-w-full text-xs font-mono text-aegisaxiom-text-secondary whitespace-pre">
              {children}
            </pre>
          ),
          // Tables: the overflow-x wrapper lets wide tables scroll
          // inside the bubble. max-w-full + min-w-0 prevent the
          // wrapper from pushing the parent flex container wider.
          table: ({ children }) => (
            <div className="overflow-x-auto max-w-full my-2 min-w-0">
              <table className="w-full text-xs border-collapse">{children}</table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="border-b border-aegisaxiom-border-hover">{children}</thead>
          ),
          th: ({ children }) => (
            <th className="text-left py-1.5 px-2 text-aegisaxiom-text-secondary font-medium text-[11px] uppercase tracking-wider whitespace-nowrap">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="py-1.5 px-2 text-aegisaxiom-text-secondary border-b border-aegisaxiom-border align-top">
              {children}
            </td>
          ),
          hr: () => <hr className="border-aegisaxiom-border my-3" />,
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-violet-500/50 pl-3 my-2 text-aegisaxiom-text-secondary italic break-words">
              {children}
            </blockquote>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
