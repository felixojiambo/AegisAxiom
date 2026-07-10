import React from 'react';

/**
 * BlockShell — opt-in wrapper component for SOW Builder blocks.
 *
 * The editor architecture ADR (docs/build/adr-sow-editor-architecture.md)
 * commits to a block-based editor on top of the existing structured
 * SOW data model. This component is the minimal common outline that
 * block types opt into so the SOW Builder shares one card chrome
 * (border, drag-over highlight, header slot, body slot, data-testid)
 * without re-implementing it per block.
 *
 * Current consumers:
 *   - RichProseSection — PS prose blocks (required + optional). The
 *     outer card itself is the drag source, so it passes both
 *     `draggable={isDraggable}` and the drop handlers.
 *   - MilestoneCard    — PS milestone (phase) blocks. The outer
 *     card is a drop target only; the GripVertical button inside
 *     the header is the drag source.
 *   - BulletRow        — PS task blocks. Same pattern as milestones:
 *     outer is drop target only, inner grip button is drag source.
 *
 * Drag-source vs drop-target wiring:
 *   - `draggable={true}` gates only the drag-SOURCE attributes
 *     (`draggable={true}` HTML attr + `onDragStart` + `onDragEnd`).
 *   - Drop-target handlers (`onDragOver`, `onDragLeave`, `onDrop`)
 *     attach whenever the caller provides them, independent of
 *     `draggable`. That lets a block be a drop target without
 *     having to also be a drag source.
 *
 * Background variants:
 *   - `variant="base"`    → `bg-aegisaxiom-base` (default; phase cards,
 *     prose sections, top-level blocks).
 *   - `variant="surface"` → `bg-aegisaxiom-surface` (nested rows; bullets
 *     sit on the lighter surface inside a phase card).
 *
 * Storage format is unchanged. This component never touches data —
 * it only renders the outer card.
 */
export default function BlockShell({
  blockType,
  blockId,
  isDragOver = false,
  draggable = false,
  onDragStart,
  onDragOver,
  onDragLeave,
  onDragEnd,
  onDrop,
  header,
  headerClassName,
  children,
  testId,
  className = '',
  variant = 'base',
}) {
  const backgroundClass =
    variant === 'surface' ? 'bg-aegisaxiom-surface' : 'bg-aegisaxiom-base';
  const baseClass = `${backgroundClass} border rounded-lg overflow-hidden transition-colors`;
  const stateClass = isDragOver
    ? 'border-violet-500 ring-2 ring-violet-500/40'
    : 'border-aegisaxiom-border';
  const headerChromeClass =
    headerClassName || 'bg-aegisaxiom-elevated px-4 py-2.5 border-b border-aegisaxiom-border';

  return (
    <div
      className={`${baseClass} ${stateClass} ${className}`.trim()}
      data-block-type={blockType}
      data-block-id={blockId || undefined}
      data-testid={testId || (blockType && blockId ? `block-${blockType}-${blockId}` : undefined)}
      draggable={draggable || undefined}
      onDragStart={draggable ? onDragStart : undefined}
      onDragEnd={draggable ? onDragEnd : undefined}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {header ? <div className={headerChromeClass}>{header}</div> : null}
      <div>{children}</div>
    </div>
  );
}
