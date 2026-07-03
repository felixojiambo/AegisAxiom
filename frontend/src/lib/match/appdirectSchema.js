// AppDirect canonical field paths — AEGISAXIOM MATCH module.
// Thin projection of contractH.js for consumers that only need
// {path, label, required} (e.g. the template editor's AppDirect Field dropdown).
// To rename or add a field, edit contractH.js — this module derives from it.

import { ALL_FIELDS } from "./contractH";

export const APPDIRECT_FIELD_PATHS = ALL_FIELDS.map((f) => {
  const out = { path: f.path, label: f.label };
  if (f.required) out.required = true;
  return out;
});

export const TRANSFORM_OPTIONS = [
  { value: "",                   label: "None" },
  { value: "strip_usd_prefix",   label: "Strip $ prefix" },
  { value: "normalize_prc",      label: "Normalize PRC" },
  { value: "parse_date",         label: "Parse date" },
];

export function findFieldByPath(path) {
  return APPDIRECT_FIELD_PATHS.find((f) => f.path === path) || null;
}
