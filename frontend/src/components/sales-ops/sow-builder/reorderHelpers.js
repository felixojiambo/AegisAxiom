/**
 * Pure helpers for SOW Builder reorder math.
 *
 * Extracted from SolutionsDesignTab.js so the reorder logic is
 * unit-testable in isolation and shared between the up/down arrow
 * path (keyboard fallback) and the new native HTML5 drag/drop path.
 *
 * Why native HTML5 DnD instead of @dnd-kit:
 *   - The repo does not currently install @dnd-kit. Adding it
 *     requires a `yarn install` to regenerate yarn.lock; that step
 *     is blocked in the sandbox the implementation ran in. Native
 *     HTML5 DnD is the smallest stable option that requires no
 *     dependency change and no lockfile bump.
 *   - The up/down arrow buttons remain in place as the keyboard
 *     fallback for accessibility.
 *
 * Both helpers return a NEW array — never mutate the input.
 */

/**
 * Move the item with the given id from its current position to
 * `toIndex`. ``order`` array contains string ids (or kinds). When
 * the id is missing or `toIndex` is out of range, the original
 * array is returned unchanged (callers can compare references to
 * detect a no-op).
 */
export function moveItemToIndex(order, id, toIndex) {
  if (!Array.isArray(order)) return [];
  const fromIndex = order.indexOf(id);
  if (fromIndex === -1) return order;
  const clamped = Math.max(0, Math.min(toIndex, order.length - 1));
  if (clamped === fromIndex) return order;
  const next = order.slice();
  next.splice(fromIndex, 1);
  next.splice(clamped, 0, id);
  return next;
}

/**
 * Swap-by-one helper used by the up/down arrow buttons. Returns a
 * NEW array; returns the original array reference unchanged when
 * the move would step off either end. ``direction`` is 'up' or
 * 'down'.
 */
export function swapByOne(order, id, direction) {
  if (!Array.isArray(order)) return [];
  const idx = order.indexOf(id);
  if (idx === -1) return order;
  const swapIdx = direction === 'up' ? idx - 1 : idx + 1;
  if (swapIdx < 0 || swapIdx >= order.length) return order;
  const next = order.slice();
  [next[idx], next[swapIdx]] = [next[swapIdx], next[idx]];
  return next;
}
