"""Phase 1B.4 — Migrate existing AI Employees into catalog/assignment/instance shape.

For every row in `ai_employees` that lacks tenant linkage:

  1. Find or create an `ai_employee_definitions` blueprint matching the
     row's `name` (deduped within the SimpliMeta root tenant scope).
  2. Find or create a `tenant_app_assignments` entitlement granting the
     SimpliMeta root tenant access to that definition.
  3. `$set` `definition_id`, `tenant_id`, `assignment_id` on the
     instance row so the marketplace + frontend now treat it as a
     properly-linked catalog instance.

All rows migrate to `ten_simplimeta_root`. Future tenants get their own
assignments + instances via the catalog/provisioning APIs (1B.5+).

## Properties

**Idempotent.** Safe to re-run on every server startup:
  * Rows that are already fully linked are skipped.
  * Definitions are deduped by `(name, created_by_tenant_id=root)` —
    a second pass reuses the first pass's definition.
  * Assignments are deduped by `(definition_id, tenant_id=root)` —
    same reuse on a second pass.
  * Partial-linkage rows (e.g. has `tenant_id` but no `definition_id`)
    are completed in-place rather than re-created.

**Discovery-based.** No hardcoded employee count or naming. The same
script runs against localhost (2 rows), dev (5 rows), prod (6 rows)
and produces the right result in each environment because each writes
into its own database.

**Conservative.** ADDS the three linkage fields onto instance rows;
DOES NOT remove any existing fields from them. The override semantic
(instance value wins over definition default) makes the redundant
values harmless, and keeping them preserves a trivial rollback path:
strip `definition_id`/`tenant_id`/`assignment_id` and the row is back
to its pre-1B.4 shape.

## Rollout window

This startup hook should be **removed in a follow-up cleanup commit**
once rollout is verified across localhost / dev / prod. After that,
new instances are created exclusively via the 1B.5 provisioning API,
which sets all three linkage fields at creation time. The hook
remaining indefinitely is a low-cost safety net but adds startup
noise; cleanup is the right end state.

## What is NOT migrated

* **Backing users.** Each AI employee has a backing user record stamped
  with `tenant_id=ten_simplimeta_root` already (see Phase 1A's user
  backfill). The relationship via `user_id` on the employee continues
  to work, so no additional backing-user changes are needed here.
* **`MOCK_AI_EMPLOYEES` in the frontend.** Those are frontend-only
  display cards, not real DB rows. Their cleanup is a 1B.6 concern.
* **`ai_employee_knowledge` / `ai_employee_user_state` documents.**
  These collections reference instances by `employee_id`, which is
  unchanged. They continue to work without modification.

Refs: Phase 1B Locked Decisions, 8 May 2026.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from seeds.seed_tenants import SIMPLIMETA_ROOT_TENANT_ID
from models import gen_id

logger = logging.getLogger(__name__)

# Identity stamped on definition + assignment records that were created
# by this migration. Useful for forensics ("which rows came from the
# big-bang migration vs the 1B.5 provisioning API?") and for the future
# cleanup commit that removes this hook.
MIGRATION_USER_ID = "system_migration_1b4"


def _build_definition_doc(employee: dict) -> dict:
    """Construct an `ai_employee_definitions` document from an existing
    `ai_employees` row, mapping instance fields to their `default_*`
    counterparts.

    The instance row stays untouched after migration — we just lift its
    current shape into a blueprint that future tenants will instantiate.
    """
    type_ = employee.get("type", "n8n")

    # Metric labels live on the instance under metrics_config; lift them
    # into the definition's default_metric_labels dict keyed by slot.
    mc = employee.get("metrics_config", {}) or {}
    default_metric_labels = {}
    for slot in ("metric1", "metric2", "metric3"):
        label = mc.get(f"{slot}_label")
        if label:
            default_metric_labels[slot] = label

    # Provisioning mode picker:
    #   * n8n employees need a workflow clone per tenant when provisioned
    #     in the future → "n8n_clone".
    #   * Conversational employees are native runtime entities → "native".
    # Pre-existing instances are unaffected — this field describes how
    # FUTURE instances of the blueprint get provisioned.
    if type_ == "n8n":
        provisioning_mode = "n8n_clone"
    else:
        provisioning_mode = "native"

    # Knowledge base filter is nested under "knowledge_base.filter" on
    # the instance — lift just the filter portion to the definition.
    kb = employee.get("knowledge_base") or {}
    default_knowledge_base_filter = (
        kb.get("filter") if isinstance(kb, dict) else None
    )

    now = datetime.now(timezone.utc)

    return {
        "definition_id": gen_id("def_"),
        "name": employee.get("name", "Unnamed"),
        "role": employee.get("role", "AI Employee"),
        "type": type_,
        "description": employee.get("description", "") or "",
        "default_avatar": employee.get("avatar") or "🤖",

        # Conversational defaults (None is fine; the schema allows it)
        "default_system_prompt": employee.get("system_prompt"),
        "default_router_description": employee.get("router_description"),
        "default_model_provider": employee.get("model_provider") or "google",
        "default_model_name": employee.get("model_name") or "gemini-2.5-flash",
        "default_tools": employee.get("tools") or [],
        "default_knowledge_base_filter": default_knowledge_base_filter,
        "default_state_schema": employee.get("state_schema"),

        # N8N template URL — migrated employees are running instances, not
        # templates. Future n8n_clone provisioning needs a template URL on
        # the definition; SimpliMeta can set this manually post-migration
        # for definitions that should support new-tenant provisioning.
        "default_n8n_template_url": None,

        # Catalog metadata
        "default_capabilities": employee.get("specialties") or [],
        "default_metric_labels": default_metric_labels,
        "default_metrics_source": mc.get("source", "manual"),

        # Provisioning + lifecycle
        "provisioning_mode": provisioning_mode,
        "is_active": True,
        "is_published": True,

        # Authorship — flagged as migration-origin for future forensics
        "created_by_user_id": MIGRATION_USER_ID,
        "created_by_tenant_id": SIMPLIMETA_ROOT_TENANT_ID,

        "created_at": now,
        "updated_at": now,
    }


async def _get_or_create_definition(db, employee: dict) -> str:
    """Return the `definition_id` for this employee's blueprint, creating
    it if it doesn't exist.

    Dedup key: `(name, created_by_tenant_id=root)`. Within a single
    environment, two `ai_employees` rows with the same `name` are
    treated as instances of the same blueprint. Across environments,
    each migration writes into its own database — there's no
    cross-environment dedup to worry about.
    """
    name = employee.get("name", "Unnamed")
    existing = await db.ai_employee_definitions.find_one(
        {
            "name": name,
            "created_by_tenant_id": SIMPLIMETA_ROOT_TENANT_ID,
        },
        {"_id": 0, "definition_id": 1},
    )
    if existing:
        return existing["definition_id"]

    doc = _build_definition_doc(employee)
    await db.ai_employee_definitions.insert_one(doc)
    logger.info(
        "Created catalog definition for migration: %s (definition_id=%s, type=%s)",
        doc["name"], doc["definition_id"], doc["type"],
    )
    return doc["definition_id"]


async def _get_or_create_assignment(db, definition_id: str) -> str:
    """Return the `assignment_id` for `(definition, root tenant)`,
    creating it if it doesn't exist.

    The unique index on `(tenant_id, definition_id)` (added in 1B.1)
    enforces at-most-one assignment per pair at the database level too,
    so this dedup is belt-and-suspenders.
    """
    existing = await db.tenant_app_assignments.find_one(
        {
            "definition_id": definition_id,
            "tenant_id": SIMPLIMETA_ROOT_TENANT_ID,
        },
        {"_id": 0, "assignment_id": 1},
    )
    if existing:
        return existing["assignment_id"]

    now = datetime.now(timezone.utc)
    doc = {
        "assignment_id": gen_id("asn_"),
        "definition_id": definition_id,
        "tenant_id": SIMPLIMETA_ROOT_TENANT_ID,
        "granted_by_user_id": MIGRATION_USER_ID,
        "granted_by_tenant_id": SIMPLIMETA_ROOT_TENANT_ID,
        "status": "active",
        "granted_at": now,
        "revoked_at": None,
        "expires_at": None,
        "max_instances": None,
        "notes": "Auto-generated by Phase 1B.4 migration",
        "updated_at": now,
    }
    await db.tenant_app_assignments.insert_one(doc)
    logger.info(
        "Created tenant assignment for migration: definition_id=%s → assignment_id=%s",
        definition_id, doc["assignment_id"],
    )
    return doc["assignment_id"]


async def _migrate_instance(db, employee: dict) -> bool:
    """Migrate a single `ai_employees` row into the catalog/assignment/
    instance shape.

    Returns:
        True  — the row was modified by this call (full or partial migration).
        False — the row was already fully linked; nothing to do.

    Handles partial-linkage rows (e.g. `tenant_id` already present from
    a prior aborted run) by only filling in the missing pieces.
    """
    employee_id = employee.get("employee_id")
    if not employee_id:
        logger.warning(
            "Skipping ai_employees row with no employee_id during migration; "
            "shape: %s",
            sorted(list(employee.keys())),
        )
        return False

    has_definition = bool(employee.get("definition_id"))
    has_tenant = bool(employee.get("tenant_id"))
    has_assignment = bool(employee.get("assignment_id"))

    if has_definition and has_tenant and has_assignment:
        return False  # already fully migrated, no-op

    # Resolve the three linkage IDs. Reuse whatever's already on the row;
    # create/lookup the missing ones.
    if has_definition:
        definition_id = employee["definition_id"]
    else:
        definition_id = await _get_or_create_definition(db, employee)

    if has_assignment:
        assignment_id = employee["assignment_id"]
    else:
        assignment_id = await _get_or_create_assignment(db, definition_id)

    tenant_id = employee.get("tenant_id") or SIMPLIMETA_ROOT_TENANT_ID

    # Build a minimal $set: only the fields that were missing. This keeps
    # the change observable in audit logs and avoids touching fields the
    # caller didn't ask to change.
    patch: dict = {"updated_at": datetime.now(timezone.utc)}
    if not has_definition:
        patch["definition_id"] = definition_id
    if not has_tenant:
        patch["tenant_id"] = tenant_id
    if not has_assignment:
        patch["assignment_id"] = assignment_id

    await db.ai_employees.update_one(
        {"employee_id": employee_id},
        {"$set": patch},
    )
    logger.info(
        "Migrated AI Employee: %s (employee_id=%s) → tenant=%s, "
        "definition=%s, assignment=%s",
        employee.get("name", "?"), employee_id, tenant_id,
        definition_id, assignment_id,
    )
    return True


async def seed_ai_employee_catalog(db) -> None:
    """Top-level Phase 1B.4 migration entry point — called from server
    startup right after `seed_default_roles`.

    Discovers all `ai_employees` rows and migrates any that aren't
    fully linked. Logs a single summary line whether work happened or
    not, so the boot logs are informative without being noisy on
    re-runs.
    """
    total = await db.ai_employees.count_documents({})
    if total == 0:
        logger.info("AI Employee catalog migration: no rows present; skipping.")
        return

    rows = await db.ai_employees.find({}, {"_id": 0}).to_list(1000)

    migrated = 0
    for emp in rows:
        if await _migrate_instance(db, emp):
            migrated += 1

    if migrated:
        logger.info(
            "AI Employee catalog migration: %d/%d rows migrated this boot.",
            migrated, total,
        )
    else:
        logger.info(
            "AI Employee catalog migration: %d rows already fully linked, no-op.",
            total,
        )