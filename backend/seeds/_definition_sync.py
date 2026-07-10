"""Phase 1E.3e — Sync `ai_employee_definitions` defaults from the live
`ai_employees` row for seeded conversational AI employees.

## The problem this solves

The Phase 1B.4 catalog migration (`seed_ai_employee_catalog.py`) created
one `ai_employee_definitions` row per seeded AI Employee by snapshotting
the employee's then-current shape into `default_*` fields. After that
first boot, the catalog seeder's `_get_or_create_definition` returns
the existing row without refreshing it. Meanwhile, the per-employee
seeders (`seed_vega.py`, `seed_orion.py`, `seed_arc.py`) continue to
`$set` `system_prompt`, `model_name`, `tools`, etc. onto the live
`ai_employees` row on every boot.

The result: the definition's `default_*` fields drift out of sync with
the latest seed-file values. When SimpliMeta grants a definition to a
new tenant via the 1B.5 provisioning API, the tenant's instance is
snapshotted from the **stale** definition — not from the latest source.
Surfaced during the 2026-05-20 standup demo when Aidan Harrelson
observed that Gicheru Test's provisioned Vega was running on an older
prompt/tool list than the root-tenant Vega.

## What this module does

`sync_definition_defaults_from_employee(db, employee_id)` is called
from each per-employee seeder after its `ai_employees` upsert. It:

  1. Loads the just-upserted employee row.
  2. Finds the matching definition by `(name, created_by_tenant_id=root)`
     — the same dedup key the catalog migration uses.
  3. `$set`s the relevant `default_*` fields on the definition with the
     employee's current values.

If no matching definition is found, logs a warning and returns. The
catalog migration hook was removed in 1B.6 (it's now manual-only), so
a missing definition is a real signal that the catalog seed needs to
be re-run — not a silent fallback we should auto-create.

## What it deliberately does NOT do

  * Does NOT update existing tenant **instances** (`ai_employees` rows
    with `tenant_id != root`). Provisioned instances are authoritative
    for their own configuration — see the docstring on the PATCH
    `/ai-employee-definitions/{id}` endpoint. A tenant that wants the
    latest prompt/tools must reprovision (delete + provision via the
    1B.5 API). This is the conservative behaviour partners expect: we
    don't silently swap their AI's prompt out from under them on each
    SimpliMeta deploy.
  * Does NOT touch `is_published`, `is_active`, `provisioning_mode`,
    `default_n8n_template_url`. Those are operator-managed lifecycle
    fields, not derived from the seed source.
  * Does NOT create a definition if one is missing. Definition creation
    remains the catalog seeder's job (or the manual POST endpoint).

Refs: Phase 1B.4 catalog migration; Phase 1B.5 provisioning API;
2026-05-20 standup demo (Aidan Harrelson).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from seeds.seed_tenants import SIMPLIMETA_ROOT_TENANT_ID

logger = logging.getLogger(__name__)


# Fields on `ai_employees` that map onto `default_*` fields on the
# definition. Conversational-only fields are filtered out at sync time
# if the employee row doesn't carry them (e.g., n8n-type employees
# wouldn't have `system_prompt`, so we don't $set
# `default_system_prompt: None`).
#
# Tuple shape: (employee_field, definition_field, conversational_only)
_FIELD_MAP: list[tuple[str, str, bool]] = [
    ("system_prompt",       "default_system_prompt",       True),
    ("router_description",  "default_router_description",  True),
    ("model_provider",      "default_model_provider",      True),
    ("model_name",          "default_model_name",          True),
    ("tools",               "default_tools",               True),
    ("state_schema",        "default_state_schema",        True),
    ("description",         "description",                 False),
    ("role",                "role",                        False),
    ("avatar",              "default_avatar",              False),
    ("specialties",         "default_capabilities",        False),
]


def _build_patch(employee: dict) -> dict[str, Any]:
    """Build the $set payload for the definition update.

    Only includes fields actually present on the employee row — we don't
    want to $set `None` over a value an operator might have customised
    on the definition (e.g., a hand-tuned `default_router_description`
    on a definition cloned from a conversational employee that later
    dropped the field).
    """
    patch: dict[str, Any] = {}

    type_ = employee.get("type", "n8n")
    is_conversational = (type_ == "conversational")

    for emp_field, def_field, conv_only in _FIELD_MAP:
        if conv_only and not is_conversational:
            continue
        value = employee.get(emp_field)
        if value is None:
            continue
        patch[def_field] = value

    # Knowledge base filter — nested on employee, flat on definition.
    kb = employee.get("knowledge_base") or {}
    kb_filter = kb.get("filter") if isinstance(kb, dict) else None
    if kb_filter is not None:
        patch["default_knowledge_base_filter"] = kb_filter

    # Metric labels + source — nested under metrics_config on employee,
    # split into default_metric_labels (dict) + default_metrics_source
    # (string) on the definition.
    mc = employee.get("metrics_config") or {}
    if isinstance(mc, dict):
        default_metric_labels: dict[str, str] = {}
        for slot in ("metric1", "metric2", "metric3"):
            label = mc.get(f"{slot}_label")
            if label:
                default_metric_labels[slot] = label
        if default_metric_labels:
            patch["default_metric_labels"] = default_metric_labels

        source = mc.get("source")
        if source:
            patch["default_metrics_source"] = source

    return patch


async def sync_definition_defaults_from_employee(
        db,
        employee_id: str,
) -> bool:
    """Sync the matching definition's `default_*` fields from the live
    `ai_employees` row for `employee_id`.

    Call this from per-employee seeders (`seed_vega`, `seed_orion`,
    `seed_arc`) after the `ai_employees` upsert. Idempotent — re-runs
    against an already-synced definition produce a no-op `$set`.

    Returns:
        True  — definition was located and patched (or patched a no-op).
        False — no matching definition found; caller may want to log
                or alert. The catalog seed needs to be re-run.
    """
    employee = await db.ai_employees.find_one(
        {"employee_id": employee_id}, {"_id": 0}
    )
    if not employee:
        logger.warning(
            "sync_definition_defaults: employee_id=%s not found in ai_employees; "
            "nothing to sync.",
            employee_id,
        )
        return False

    name = employee.get("name")
    if not name:
        logger.warning(
            "sync_definition_defaults: employee_id=%s has no `name`; "
            "cannot locate matching definition.",
            employee_id,
        )
        return False

    definition = await db.ai_employee_definitions.find_one(
        {
            "name": name,
            "created_by_tenant_id": SIMPLIMETA_ROOT_TENANT_ID,
        },
        {"_id": 0, "definition_id": 1},
    )
    if not definition:
        logger.warning(
            "sync_definition_defaults: no `ai_employee_definitions` row for "
            "name=%r (created_by_tenant_id=%s). Skipping. "
            "Re-run `seed_ai_employee_catalog.seed_ai_employee_catalog(db)` "
            "to create the missing definition.",
            name, SIMPLIMETA_ROOT_TENANT_ID,
        )
        return False

    patch = _build_patch(employee)
    if not patch:
        logger.info(
            "sync_definition_defaults: nothing to sync for employee_id=%s "
            "(no relevant fields populated on employee row).",
            employee_id,
        )
        return True

    patch["updated_at"] = datetime.now(timezone.utc)

    await db.ai_employee_definitions.update_one(
        {"definition_id": definition["definition_id"]},
        {"$set": patch},
    )
    logger.info(
        "sync_definition_defaults: definition_id=%s (name=%r) synced from "
        "employee_id=%s — fields=%s",
        definition["definition_id"], name, employee_id, sorted(patch.keys()),
    )
    return True
