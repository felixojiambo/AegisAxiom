/**
 * SOW block registry.
 *
 * Single source of truth for "what is a SOW block" — the block
 * taxonomy + per-type capability flags + addressing rules. The
 * editor architecture ADR (docs/build/adr-sow-editor-architecture.md)
 * names this module as the contract that any future editor surface
 * must read against, rather than hard-coding "can this block be
 * reordered / regenerated / removed."
 *
 * This module mirrors the live Pydantic models in backend/models.py:
 *   - SOWProseSection (backend/models.py:1814)
 *   - SOWMilestone   (backend/models.py:1751)
 *   - SOWBullet      (backend/models.py:1742)
 *   - SOWProductItem (backend/models.py:1768)
 *
 * It does not invent fields and does not change storage. If the
 * Pydantic models change, this file is the place to update — both
 * sides reference each other in their respective doc comments.
 */

// Block type tokens. Stable strings so callers can switch on them
// or look them up in the registry without importing constants from
// elsewhere.
export const BLOCK_TYPES = Object.freeze({
  PROSE_REQUIRED: 'prose_required',
  PROSE_OPTIONAL: 'prose_optional',
  MILESTONE: 'milestone',
  BULLET: 'bullet',
  SUBTASK: 'subtask',
  PRODUCT_ITEM: 'product_item',
});

// Addressing rules. Each block type has exactly one stable address
// field. Editor surfaces must use this field — never an array index
// — when referring to a block in URLs, audit rows, or analytics.
//
// PROSE_REQUIRED and PROSE_OPTIONAL share the same address field
// (`kind`) because they live in the same `prose_sections[]` array
// on the SOW; the only difference is whether the proposal contract
// allows the section to be removed.
const ADDRESS_FIELDS = Object.freeze({
  [BLOCK_TYPES.PROSE_REQUIRED]: 'kind',
  [BLOCK_TYPES.PROSE_OPTIONAL]: 'kind',
  [BLOCK_TYPES.MILESTONE]: 'milestone_id',
  [BLOCK_TYPES.BULLET]: 'bullet_id',
  [BLOCK_TYPES.SUBTASK]: 'subtask_id',
  [BLOCK_TYPES.PRODUCT_ITEM]: 'item_id',
});

// Capability flags. Each entry is the smallest honest description of
// what the current SOW Builder supports for a block of that type.
// The flags are descriptive (what is currently true) rather than
// prescriptive (what we wish were true) — anyone adding a new
// capability flips a flag here and threads it through.
//
// `reorderable`:   user-initiated reorder UI exists and persists
//                  through the canonical order field.
// `regenerable`:   a per-block regenerate path exists end-to-end
//                  (route + composer + UI modal).
// `removable`:     the block can be removed from the SOW without
//                  invalidating the proposal contract. PS required
//                  prose sections are not removable.
// `editable`:      content is user-editable through the SOW Builder.
//                  PRODUCT_ITEM is editable for SOW staff but
//                  carries a procurement lifecycle that the SOW
//                  edit path must preserve, not overwrite.
// `cross-parent`:  the block can move across parent containers
//                  (e.g. bullet moving to a different milestone).
//                  Currently false everywhere — cross-parent moves
//                  would invent ownership semantics the data model
//                  does not express.
const CAPABILITIES = Object.freeze({
  [BLOCK_TYPES.PROSE_REQUIRED]: Object.freeze({
    reorderable: false, // required order is part of the proposal contract
    regenerable: true,
    removable: false,
    editable: true,
    crossParent: false,
  }),
  [BLOCK_TYPES.PROSE_OPTIONAL]: Object.freeze({
    reorderable: true,
    regenerable: true,
    removable: true,
    editable: true,
    crossParent: false,
  }),
  [BLOCK_TYPES.MILESTONE]: Object.freeze({
    reorderable: true,
    regenerable: false, // out-of-model per master tickboard row 5
    removable: true,
    editable: true,
    crossParent: false,
  }),
  [BLOCK_TYPES.BULLET]: Object.freeze({
    reorderable: true, // within parent milestone only
    regenerable: false, // out-of-model per master tickboard row 5
    removable: true,
    editable: true,
    crossParent: false, // explicitly not supported — would invent ownership
  }),
  [BLOCK_TYPES.SUBTASK]: Object.freeze({
    reorderable: true, // within parent bullet only
    regenerable: false, // no per-block regenerate path today
    removable: true,
    editable: true,
    crossParent: false, // subtasks cannot move to a different parent bullet
  }),
  [BLOCK_TYPES.PRODUCT_ITEM]: Object.freeze({
    reorderable: false, // not currently exposed in the SOW Builder UI
    regenerable: false,
    removable: true,
    editable: true,
    crossParent: false,
  }),
});

/**
 * Resolve a block type token to its address field name.
 *
 * Returns `null` for unknown block types so callers can refuse to
 * persist a block they cannot stably address rather than guessing.
 */
export function addressFieldFor(blockType) {
  return ADDRESS_FIELDS[blockType] ?? null;
}

/**
 * Read the canonical address from a block payload, given the block
 * type token. Returns the address string when present, or `null`
 * when the block lacks its address field — the editor must treat
 * that as a hard error rather than fall back to an array index.
 */
export function addressOf(blockType, block) {
  const field = addressFieldFor(blockType);
  if (!field || !block || typeof block !== 'object') return null;
  const raw = block[field];
  if (typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  return trimmed ? trimmed : null;
}

/**
 * Resolve a block type token to its capability flags. Returns a
 * frozen object so callers cannot accidentally mutate the registry.
 */
export function capabilitiesFor(blockType) {
  return CAPABILITIES[blockType] ?? null;
}

/**
 * Convenience guards — short, readable, intent-revealing. Each
 * returns `false` for unknown block types so future block types
 * default to "not supported" rather than silently inheriting a
 * permissive default.
 */
export function isReorderable(blockType) {
  return Boolean(CAPABILITIES[blockType]?.reorderable);
}

export function isRegenerable(blockType) {
  return Boolean(CAPABILITIES[blockType]?.regenerable);
}

export function isRemovable(blockType) {
  return Boolean(CAPABILITIES[blockType]?.removable);
}

export function isEditable(blockType) {
  return Boolean(CAPABILITIES[blockType]?.editable);
}

export function allowsCrossParent(blockType) {
  return Boolean(CAPABILITIES[blockType]?.crossParent);
}

/**
 * Returns the full list of registered block type tokens — useful for
 * tests, exhaustive switch coverage, and debugging.
 */
export function listBlockTypes() {
  return Object.values(BLOCK_TYPES);
}

export default {
  BLOCK_TYPES,
  addressFieldFor,
  addressOf,
  capabilitiesFor,
  isReorderable,
  isRegenerable,
  isRemovable,
  isEditable,
  allowsCrossParent,
  listBlockTypes,
};
