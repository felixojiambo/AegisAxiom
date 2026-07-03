import React, { useCallback, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Bold,
  ChevronDown,
  ChevronUp,
  Eye,
  GripVertical,
  Italic,
  Link as LinkIcon,
  List,
  ListOrdered,
  Pencil,
  Sparkles,
  Trash2,
} from 'lucide-react';
import BlockShell from './BlockShell';
import {
  BLOCK_TYPES,
  isRegenerable,
  isRemovable,
  isReorderable,
} from './blockRegistry';

// Markdown link allowlist. The proposal pipeline persists the prose
// section as a markdown string and the preview renders it via
// react-markdown. react-markdown 10.x already runs URLs through its
// own urlTransform safelist (http/https/mailto/tel + relative), but
// we layer a second explicit check here so the rule the ticket asked
// for — no javascript:, no data: — is enforced on the editor surface
// regardless of any future react-markdown upgrade that loosens the
// default.
const LINK_ALLOWED_SCHEMES = /^(https?:|mailto:|tel:)/i;
const safeUrl = (raw) => {
  if (raw === null || raw === undefined) return undefined;
  const value = String(raw).trim();
  if (!value) return undefined;
  // Reject protocol-relative URLs (//evil.com) — these resolve to the
  // page's protocol at click time, which is an open-redirect /
  // phishing vector even though it's not a script-execution vector.
  if (value.startsWith('//')) return undefined;
  // In-page anchors and root-relative paths are safe; they never
  // resolve to a third-party origin.
  if (value.startsWith('#') || value.startsWith('/')) return value;
  return LINK_ALLOWED_SCHEMES.test(value) ? value : undefined;
};

// Reusable markdown components map: mirrors the aegisaxiom theme classes
// the rest of the SOW Builder uses, no new tokens. Tables stay
// scrollable so a wide column doesn't blow out the section card.
const MARKDOWN_COMPONENTS = {
  p: ({ children }) => (
    <p className="mb-2 last:mb-0 text-aegisaxiom-text leading-relaxed">{children}</p>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-aegisaxiom-text">{children}</strong>
  ),
  em: ({ children }) => (
    <em className="italic text-aegisaxiom-text-secondary">{children}</em>
  ),
  h1: ({ children }) => (
    <h3 className="text-base font-bold text-aegisaxiom-text mt-2 mb-1.5 first:mt-0">{children}</h3>
  ),
  h2: ({ children }) => (
    <h4 className="text-sm font-bold text-aegisaxiom-text mt-2 mb-1 first:mt-0">{children}</h4>
  ),
  h3: ({ children }) => (
    <h5 className="text-sm font-semibold text-aegisaxiom-text mt-1.5 mb-1 first:mt-0">{children}</h5>
  ),
  ul: ({ children }) => (
    <ul className="list-disc pl-5 mb-2 space-y-0.5">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal pl-5 mb-2 space-y-0.5">{children}</ol>
  ),
  li: ({ children }) => (
    <li className="text-aegisaxiom-text-secondary break-words">{children}</li>
  ),
  a: ({ href, children }) => {
    const safe = safeUrl(href);
    if (!safe) {
      return <span className="text-aegisaxiom-text-muted">{children}</span>;
    }
    return (
      <a
        href={safe}
        target="_blank"
        rel="noopener noreferrer"
        className="text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:hover:text-violet-300 underline break-words"
      >
        {children}
      </a>
    );
  },
  code: ({ inline, children }) =>
    inline ? (
      <code className="bg-aegisaxiom-elevated text-violet-700 dark:text-violet-300 px-1.5 py-0.5 rounded text-[0.85em] font-mono break-words">
        {children}
      </code>
    ) : (
      <code className="text-xs font-mono">{children}</code>
    ),
  pre: ({ children }) => (
    <pre className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-md p-3 my-2 overflow-x-auto max-w-full text-xs font-mono text-aegisaxiom-text-secondary whitespace-pre">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto max-w-full my-2 min-w-0">
      <table className="w-full text-xs border-collapse">{children}</table>
    </div>
  ),
};

// Markdown insertion helpers. Each accepts the current textarea
// value + selection range and returns the next value plus the new
// selection position. Toolbar buttons call into these so the user
// gets predictable in-place editing without an external editor lib.
function wrapSelection(value, start, end, before, after) {
  const selected = value.slice(start, end);
  const next = value.slice(0, start) + before + selected + after + value.slice(end);
  const newStart = start + before.length;
  const newEnd = newStart + selected.length;
  return { value: next, selectionStart: newStart, selectionEnd: newEnd };
}

function applyLinePrefix(value, start, end, prefix) {
  const lineStart = value.lastIndexOf('\n', start - 1) + 1;
  const before = value.slice(0, lineStart);
  const target = value.slice(lineStart, end);
  const after = value.slice(end);
  const lines = target.split('\n');
  const transformed = lines.map((line) => (line ? prefix + line : prefix)).join('\n');
  const next = before + transformed + after;
  const delta = prefix.length * lines.length;
  return {
    value: next,
    selectionStart: lineStart + prefix.length,
    selectionEnd: end + delta,
  };
}

function applyHeading(value, start, end, hashes) {
  const lineStart = value.lastIndexOf('\n', start - 1) + 1;
  const before = value.slice(0, lineStart);
  const lineEnd = value.indexOf('\n', start);
  const realLineEnd = lineEnd === -1 ? value.length : lineEnd;
  const target = value.slice(lineStart, realLineEnd);
  const stripped = target.replace(/^#+\s*/, '');
  const next = before + `${hashes} ${stripped}` + value.slice(realLineEnd);
  return {
    value: next,
    selectionStart: lineStart + hashes.length + 1,
    selectionEnd: lineStart + hashes.length + 1 + stripped.length,
  };
}

// ---------------------------------------------------------------
// Regenerate Section modal — preview-by-default per the ticket
// spec. The user enters guidance, clicks Generate, reviews the
// candidate side-by-side with the current content, and explicitly
// clicks Replace to apply. Cancel closes without writing. The
// modal uses the existing fixed-overlay pattern already in use by
// the Procurement Tab notification modal.
// ---------------------------------------------------------------
export function RegenerateSectionModal({
  open,
  onClose,
  sectionLabel,
  currentContent,
  onGenerate,
  onReplace,
  generating,
  candidate,
  citations,
}) {
  const [instruction, setInstruction] = useState('');
  const safeCitations = Array.isArray(citations)
    ? citations.filter((c) => typeof c === 'string' && c.trim())
    : [];

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      data-testid="ps-section-regenerate-modal"
    >
      <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-xl w-full max-w-3xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-5 border-b border-aegisaxiom-border">
          <div>
            <h3 className="text-base font-bold text-aegisaxiom-text">
              Regenerate “{sectionLabel}”
            </h3>
            <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">
              Synthesizes a fresh draft from this SOW's context. Preview before replacing.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text text-sm"
            data-testid="ps-section-regenerate-close"
          >
            Cancel
          </button>
        </div>

        <div className="p-5 space-y-4">
          <div>
            <label className="block text-aegisaxiom-text text-sm font-medium mb-2">
              Optional guidance
            </label>
            <textarea
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder="e.g. make this more executive, shorten, make client-facing"
              rows={2}
              className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500 resize-y"
              data-testid="ps-section-regenerate-instruction"
            />
            <p className="text-[11px] text-aegisaxiom-text-muted mt-1">
              Tone hints only — the generator never pastes your instruction text into the section body.
            </p>
          </div>

          <button
            type="button"
            onClick={() => onGenerate(instruction)}
            disabled={generating}
            className="px-3 py-1.5 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white text-xs rounded transition inline-flex items-center gap-1.5"
            data-testid="ps-section-regenerate-generate"
          >
            <Sparkles size={12} />
            {generating ? 'Generating…' : candidate ? 'Regenerate again' : 'Generate preview'}
          </button>

          {candidate && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-2">
              <div>
                <h4 className="text-[11px] uppercase tracking-wide text-aegisaxiom-text-muted mb-1">
                  Current content
                </h4>
                <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-md p-3 max-h-[40vh] overflow-y-auto text-xs">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
                    {currentContent || '_(empty)_'}
                  </ReactMarkdown>
                </div>
              </div>
              <div>
                <h4 className="text-[11px] uppercase tracking-wide text-aegisaxiom-text-muted mb-1">
                  Candidate
                </h4>
                <div
                  className="bg-aegisaxiom-surface border border-violet-500/40 rounded-md p-3 max-h-[40vh] overflow-y-auto text-xs"
                  data-testid="ps-section-regenerate-candidate"
                >
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
                    {candidate}
                  </ReactMarkdown>
                </div>
                {safeCitations.length > 0 && (
                  <p
                    className="text-[11px] text-aegisaxiom-text-muted mt-1.5"
                    data-testid="ps-section-regenerate-citations"
                  >
                    Composed from:{' '}
                    <span className="text-aegisaxiom-text-secondary">
                      {safeCitations.join(', ')}
                    </span>
                  </p>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 p-4 border-t border-aegisaxiom-border">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text text-xs"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onReplace}
            disabled={!candidate || generating}
            className="px-3 py-1.5 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white text-xs rounded transition"
            data-testid="ps-section-regenerate-replace"
          >
            Replace section
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Rich Prose Section block. One card per SOW section. Toolbar +
// markdown textarea + sanitized live preview. Reuses the existing
// SOW Builder visual language — no new tokens, no new theme.
//
// Storage stays as markdown string on SOWProseSection.content. The
// proposal pipeline (backend/services/proposal_generation.py) and
// Google Doc export both already consume that string.
// ---------------------------------------------------------------
export default function RichProseSection({
  kind,
  label,
  placeholder,
  content,
  removable,
  hasMoveControls,
  canMoveUp,
  canMoveDown,
  canRegenerate,
  onContentChange,
  onMoveUp,
  onMoveDown,
  onRemove,
  onOpenRegenerate,
  // Drag-and-drop opt-in. When ``isDraggable`` is true, the outer
  // card carries draggable=true and the supplied DnD handlers, and
  // a GripVertical handle renders next to the up/down buttons as an
  // affordance. The up/down buttons remain for keyboard a11y.
  isDraggable = false,
  isDragOver = false,
  onDragStart,
  onDragOver: onDragOverHandler,
  onDragEnd,
  onDragLeave,
  onDrop,
}) {
  const [mode, setMode] = useState('edit'); // 'edit' | 'preview' | 'split'
  const textareaRef = useRef(null);

  const safeContent = content || '';

  const applyTransform = useCallback(
    (transform) => {
      const el = textareaRef.current;
      if (!el) return;
      const { selectionStart = 0, selectionEnd = 0 } = el;
      const result = transform(safeContent, selectionStart, selectionEnd);
      onContentChange(result.value);
      // Restore selection on the next tick so React's re-render has
      // committed before we re-focus the textarea.
      requestAnimationFrame(() => {
        if (textareaRef.current) {
          textareaRef.current.focus();
          textareaRef.current.setSelectionRange(
            result.selectionStart,
            result.selectionEnd,
          );
        }
      });
    },
    [safeContent, onContentChange],
  );

  const insertBold = () =>
    applyTransform((v, s, e) => wrapSelection(v, s, e, '**', '**'));
  const insertItalic = () =>
    applyTransform((v, s, e) => wrapSelection(v, s, e, '_', '_'));
  const insertH2 = () => applyTransform((v, s, e) => applyHeading(v, s, e, '##'));
  const insertH3 = () => applyTransform((v, s, e) => applyHeading(v, s, e, '###'));
  const insertBullet = () =>
    applyTransform((v, s, e) => applyLinePrefix(v, s, e, '- '));
  const insertNumbered = () =>
    applyTransform((v, s, e) => applyLinePrefix(v, s, e, '1. '));
  const insertLink = () => {
    const raw = window.prompt('Link URL (http/https/mailto only):', 'https://');
    const safe = safeUrl(raw);
    if (!safe) {
      if (raw !== null) {
        // Soft fail — user attempted an unsafe URL. Don't insert.
        // The toast pattern is owned by the caller; keep this
        // component self-contained.
        // eslint-disable-next-line no-alert
        window.alert('Only http, https, and mailto links are allowed.');
      }
      return;
    }
    applyTransform((v, s, e) =>
      wrapSelection(v, s, e, '[', `](${safe})`),
    );
  };

  const previewContent = useMemo(() => safeContent || '_No content yet._', [safeContent]);

  // Resolve block type from the existing `removable` prop so the
  // canvas chrome (data-block-type, capability gating, label badge)
  // is driven by the registry rather than scattered ternaries. The
  // canvas treats required vs optional prose as two distinct block
  // types with different capability flags — see blockRegistry.js.
  const blockType = removable ? BLOCK_TYPES.PROSE_OPTIONAL : BLOCK_TYPES.PROSE_REQUIRED;
  // The registry is the source of truth for whether this block type
  // can be removed, reordered, and regenerated. The caller can still
  // opt in/out via the explicit props (canRegenerate, hasMoveControls,
  // isDraggable) — we only consult the registry as a defensive sanity
  // check so a buggy caller cannot, e.g., show a Regenerate button on
  // a block type that has no per-block regenerate path.
  const registrySaysRemovable = isRemovable(blockType);
  const registrySaysReorderable = isReorderable(blockType);
  const registrySaysRegenerable = isRegenerable(blockType);
  const showRemove = removable && registrySaysRemovable;
  const showMoveControls = hasMoveControls && registrySaysReorderable;
  const showRegenerate = canRegenerate && registrySaysRegenerable;

  const header = (
    <div className="flex items-center justify-between gap-2 flex-wrap">
      <div className="flex items-center gap-2 min-w-0">
        {isDraggable && (
          <span
            className="cursor-grab active:cursor-grabbing text-aegisaxiom-text-muted hover:text-aegisaxiom-text flex-shrink-0"
            role="img"
            aria-label="Drag handle — reorder this section"
            title="Drag to reorder (keyboard users: use the up/down buttons on the right)"
            data-testid={`rich-prose-drag-handle-${kind}`}
          >
            <GripVertical size={14} />
          </span>
        )}
        <h4 className="font-semibold text-aegisaxiom-text text-sm truncate">{label}</h4>
        {removable ? (
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-aegisaxiom-surface text-aegisaxiom-text-muted border border-aegisaxiom-border">
            optional
          </span>
        ) : (
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-violet-50 dark:bg-violet-500/20 text-violet-700 dark:text-violet-300 border border-violet-500/30">
            required
          </span>
        )}
      </div>
      <div className="flex items-center gap-1 flex-wrap">
        {/* Edit/preview toggle */}
        <div className="inline-flex rounded-md border border-aegisaxiom-border overflow-hidden">
          <button
            type="button"
            onClick={() => setMode('edit')}
            className={`px-2 py-1 text-[11px] inline-flex items-center gap-1 ${
              mode === 'edit'
                ? 'bg-violet-600 text-white'
                : 'bg-aegisaxiom-base text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated'
            }`}
            data-testid={`rich-prose-mode-edit-${kind}`}
          >
            <Pencil size={11} /> Edit
          </button>
          <button
            type="button"
            onClick={() => setMode('split')}
            className={`px-2 py-1 text-[11px] border-l border-aegisaxiom-border ${
              mode === 'split'
                ? 'bg-violet-600 text-white'
                : 'bg-aegisaxiom-base text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated'
            }`}
            data-testid={`rich-prose-mode-split-${kind}`}
          >
            Split
          </button>
          <button
            type="button"
            onClick={() => setMode('preview')}
            className={`px-2 py-1 text-[11px] border-l border-aegisaxiom-border inline-flex items-center gap-1 ${
              mode === 'preview'
                ? 'bg-violet-600 text-white'
                : 'bg-aegisaxiom-base text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated'
            }`}
            data-testid={`rich-prose-mode-preview-${kind}`}
          >
            <Eye size={11} /> Preview
          </button>
        </div>
        {showMoveControls && (
          <>
            <button
              type="button"
              onClick={onMoveUp}
              disabled={!canMoveUp}
              className="p-1 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text disabled:opacity-30 disabled:cursor-not-allowed"
              title="Move up"
              data-testid={`rich-prose-move-up-${kind}`}
            >
              <ChevronUp size={14} />
            </button>
            <button
              type="button"
              onClick={onMoveDown}
              disabled={!canMoveDown}
              className="p-1 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text disabled:opacity-30 disabled:cursor-not-allowed"
              title="Move down"
              data-testid={`rich-prose-move-down-${kind}`}
            >
              <ChevronDown size={14} />
            </button>
          </>
        )}
        {showRegenerate && (
          <button
            type="button"
            onClick={onOpenRegenerate}
            className="px-2 py-1 bg-aegisaxiom-surface hover:bg-aegisaxiom-elevated border border-aegisaxiom-border text-aegisaxiom-text text-[11px] rounded inline-flex items-center gap-1 transition"
            title="Regenerate this section"
            data-testid={`rich-prose-regenerate-${kind}`}
          >
            <Sparkles size={11} /> Regenerate
          </button>
        )}
        {showRemove && (
          <button
            type="button"
            onClick={onRemove}
            className="p-1 text-aegisaxiom-text-muted hover:text-red-500"
            title="Remove section (clears content)"
            data-testid={`rich-prose-remove-${kind}`}
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>
    </div>
  );

  return (
    <BlockShell
      blockType={blockType}
      blockId={kind}
      testId={`rich-prose-${kind}`}
      isDragOver={isDragOver}
      draggable={isDraggable}
      onDragStart={onDragStart}
      onDragOver={onDragOverHandler}
      onDragLeave={onDragLeave}
      onDragEnd={onDragEnd}
      onDrop={onDrop}
      header={header}
    >
      {mode !== 'preview' && (
        <div className="flex items-center gap-1 px-3 py-1.5 bg-aegisaxiom-surface border-b border-aegisaxiom-border flex-wrap">
          <button
            type="button"
            onClick={insertBold}
            title="Bold (markdown **text**)"
            className="p-1 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated rounded"
            data-testid={`rich-prose-toolbar-bold-${kind}`}
          >
            <Bold size={13} />
          </button>
          <button
            type="button"
            onClick={insertItalic}
            title="Italic"
            className="p-1 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated rounded"
            data-testid={`rich-prose-toolbar-italic-${kind}`}
          >
            <Italic size={13} />
          </button>
          <button
            type="button"
            onClick={insertH2}
            title="Heading 2"
            className="px-1.5 py-0.5 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated rounded text-xs font-semibold"
            data-testid={`rich-prose-toolbar-h2-${kind}`}
          >
            H2
          </button>
          <button
            type="button"
            onClick={insertH3}
            title="Heading 3"
            className="px-1.5 py-0.5 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated rounded text-xs font-semibold"
            data-testid={`rich-prose-toolbar-h3-${kind}`}
          >
            H3
          </button>
          <button
            type="button"
            onClick={insertBullet}
            title="Bulleted list"
            className="p-1 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated rounded"
            data-testid={`rich-prose-toolbar-ul-${kind}`}
          >
            <List size={13} />
          </button>
          <button
            type="button"
            onClick={insertNumbered}
            title="Numbered list"
            className="p-1 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated rounded"
            data-testid={`rich-prose-toolbar-ol-${kind}`}
          >
            <ListOrdered size={13} />
          </button>
          <button
            type="button"
            onClick={insertLink}
            title="Insert link (http/https/mailto only)"
            className="p-1 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated rounded"
            data-testid={`rich-prose-toolbar-link-${kind}`}
          >
            <LinkIcon size={13} />
          </button>
        </div>
      )}

      <div className={mode === 'split' ? 'grid grid-cols-1 md:grid-cols-2 gap-0' : ''}>
        {mode !== 'preview' && (
          <div className="px-4 py-3">
            <textarea
              ref={textareaRef}
              value={safeContent}
              onChange={(e) => onContentChange(e.target.value)}
              placeholder={placeholder || ''}
              rows={6}
              className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm font-mono focus:outline-none focus:border-violet-500 resize-y leading-relaxed"
              data-testid={`ps-prose-${kind}`}
              spellCheck
            />
          </div>
        )}
        {(mode === 'preview' || mode === 'split') && (
          <div
            className={`px-4 py-3 ${mode === 'split' ? 'border-l border-aegisaxiom-border' : ''}`}
          >
            <div className="text-[10px] uppercase tracking-wide text-aegisaxiom-text-muted mb-1">
              Preview
            </div>
            <div
              className="text-aegisaxiom-text text-sm leading-relaxed"
              data-testid={`rich-prose-preview-${kind}`}
            >
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
                {previewContent}
              </ReactMarkdown>
            </div>
          </div>
        )}
      </div>
    </BlockShell>
  );
}
