/**
 * Canonical Vega flag taxonomy — JS mirror of backend/services/vega/flags.py.
 *
 * Keys match the Python VegaFlag enum values exactly so that backend → frontend
 * serialisation is a straight string pass-through with no mapping step.
 *
 * Wave 3d will build <FlagPill kind={pillKind(flag)} /> using the kind values
 * returned here.  Do not add styling or rendering logic to this module.
 */

/** @readonly */
export const VegaFlag = Object.freeze({
  MISSING: "MISSING",
  NOT_DISCUSSED: "NOT_DISCUSSED",
  NOT_STATED: "NOT_STATED",
  INFERRED_CONFIRM: "INFERRED_CONFIRM",
  NEEDS_TECHNICAL_FOLLOWUP: "NEEDS_TECHNICAL_FOLLOWUP",
  SOURCE_UNCLEAR: "SOURCE_UNCLEAR",
});

// ---------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------

const DISPLAY_LABELS = {
  [VegaFlag.MISSING]: "MISSING",
  [VegaFlag.NOT_DISCUSSED]: "NOT DISCUSSED",
  [VegaFlag.NOT_STATED]: "NOT STATED",
  [VegaFlag.INFERRED_CONFIRM]: "INFERRED — CONFIRM",
  [VegaFlag.NEEDS_TECHNICAL_FOLLOWUP]: "NEEDS TECHNICAL FOLLOW-UP",
  [VegaFlag.SOURCE_UNCLEAR]: "SOURCE UNCLEAR",
};

const LONG_EXPLANATIONS = {
  [VegaFlag.MISSING]:
    "Field not found in transcript. Customer follow-up required.",
  [VegaFlag.NOT_DISCUSSED]:
    "Topic was not raised during the call. Confirm whether it applies before handoff.",
  [VegaFlag.NOT_STATED]:
    "Topic was not explicitly stated in the transcript. Do not assume a default value.",
  [VegaFlag.INFERRED_CONFIRM]:
    "Vega inferred this value from context (e.g. industry, role, or tone). " +
    "Must be confirmed — do not treat as locked.",
  [VegaFlag.NEEDS_TECHNICAL_FOLLOWUP]:
    "Defer to a technical follow-up call. Not expected to surface in a business discovery call.",
  [VegaFlag.SOURCE_UNCLEAR]:
    "Vega could not identify a clear transcript source for this item. " +
    "Verify with the customer before handoff.",
};

/**
 * CSS class suffix for the <FlagPill> component.
 *
 * Matches the .pill.* selectors already defined in option-a-tabbed-artifact.html:
 *   .pill.missing        — amber / warning
 *   .pill.tech           — blue
 *   .pill.infer          — violet / purple
 *   .pill.notdiscussed   — muted grey (covers both NOT_DISCUSSED and NOT_STATED)
 *   .pill.sourceunclear  — reserved; Wave 3d adds the selector
 *
 * @type {Record<string, "missing"|"tech"|"infer"|"notdiscussed"|"sourceunclear">}
 */
const PILL_KINDS = {
  [VegaFlag.MISSING]: "missing",
  [VegaFlag.NOT_DISCUSSED]: "notdiscussed",
  [VegaFlag.NOT_STATED]: "notdiscussed",
  [VegaFlag.INFERRED_CONFIRM]: "infer",
  [VegaFlag.NEEDS_TECHNICAL_FOLLOWUP]: "tech",
  [VegaFlag.SOURCE_UNCLEAR]: "sourceunclear",
};

// Flags that block dev handoff until resolved.
const BLOCKING_FLAGS = new Set([
  VegaFlag.MISSING,
  VegaFlag.NEEDS_TECHNICAL_FOLLOWUP,
]);

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Short text rendered inside the pill, e.g. "NEEDS TECHNICAL FOLLOW-UP".
 * @param {string} flag — one of the VegaFlag values
 * @returns {string}
 */
export function displayLabel(flag) {
  return DISPLAY_LABELS[flag] ?? flag;
}

/**
 * Full explanatory sentence used in tooltips and PDF exports.
 * @param {string} flag — one of the VegaFlag values
 * @returns {string}
 */
export function longExplanation(flag) {
  return LONG_EXPLANATIONS[flag] ?? "";
}

/**
 * Returns the CSS kind string for <FlagPill kind={...} />.
 * @param {string} flag — one of the VegaFlag values
 * @returns {"missing"|"tech"|"infer"|"notdiscussed"|"sourceunclear"|undefined}
 */
export function pillKind(flag) {
  return PILL_KINDS[flag];
}

/**
 * True when the flag indicates dev cannot proceed without resolving this item.
 *
 * Blocking:     MISSING, NEEDS_TECHNICAL_FOLLOWUP
 * Non-blocking: INFERRED_CONFIRM, NOT_DISCUSSED, NOT_STATED, SOURCE_UNCLEAR
 *
 * @param {string} flag — one of the VegaFlag values
 * @returns {boolean}
 */
export function isBlockingForHandoff(flag) {
  return BLOCKING_FLAGS.has(flag);
}
