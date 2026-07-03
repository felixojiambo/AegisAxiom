"""Smart defaults for new AI employees.

When an admin creates a new AI employee they can pick a persona
(`sales_assistant`, `ops_assistant`, `finance_assistant`, `support_assistant`,
`general_assistant`, `pmo_assistant`) and the employee's `app_access` is
auto-populated with a sensible permission set rather than starting empty.

The PMO Assistant default employee is seeded on boot by
`backend/seeds/seed_pmo.py`, gated on `SEED_AI_EMPLOYEES=true`. That
seed applies `pmo_assistant` persona defaults via
`apply_persona_defaults` so newly-registered projects.* permissions
flow through automatically on every boot.

The persona -> permission map below uses simple glob patterns
(`contacts.*` -> `contacts.view.all`, `contacts.create`, ...) which are
resolved at apply-time against the live permission registry. That way when
new permissions are ingested or registered the defaults extend automatically
without code changes.

`apply_persona_defaults` is idempotent: applying the same persona twice
produces the same final state, and re-applying onto an employee that already
has manual `app_access` MERGES rather than overwrites.
"""
from __future__ import annotations

import fnmatch
import logging
from collections import defaultdict
from datetime import datetime, timezone

from database import get_db
from services.permission_registry import get_all_permissions

logger = logging.getLogger(__name__)


# Friendly display names for native module slugs. Anything not listed here
# gets a title-cased fallback derived from the slug itself.
_NATIVE_APP_NAMES: dict[str, str] = {
    "contacts": "Contacts",
    "accounts": "Accounts",
    "deals": "Deals",
    "opportunities": "Opportunities",
    "commissions": "Commissions",
    "invoices": "Invoices",
    "contracts": "Contracts",
    "projects": "Projects",
    "field_jobs": "Field Jobs",
    "time": "Time Management",
    "dashboards": "Custom Dashboards",
    "finance": "Finance",
    "ai_employees": "AI Employees",
    "chat": "AI Chat",
}


# Persona -> {app_slug: [permission_pattern, ...]}.
# Patterns may include simple globs like "contacts.*" — resolved against the
# live registry at apply time so newly-ingested perms auto-extend the default.
DEFAULT_APP_ACCESS_BY_PERSONA: dict[str, dict[str, list[str]]] = {
    # Sales — CRM read/write across contacts, accounts, deals, opportunities.
    "sales_assistant": {
        "contacts": [
            "contacts.view.all",
            "contacts.create",
            "contacts.edit.all",
        ],
        "accounts": [
            "accounts.view.all",
            "accounts.create",
            "accounts.edit.all",
        ],
        "deals": [
            "deals.view.all",
            "deals.create",
            "deals.edit.all",
            "deals.stage.change",
        ],
        "opportunities": [
            "opportunities.view.all",
            "opportunities.create",
            "opportunities.edit.all",
        ],
    },
    # Ops — projects + field jobs visibility, no edit by default.
    # Wave 1E (Projects ingestion): legacy `projects.view.all/own` keys were
    # retired in favor of the entity-scoped `projects.<entity>.view.*`
    # namespace. Ops gets read on projects + tasks + the dashboard.
    "ops_assistant": {
        "projects": [
            "projects.project.view.*",
            "projects.task.view.*",
            "projects.dashboard.view",
        ],
        "field_jobs": [
            "field_jobs.view.all",
            "field_jobs.view.own",
        ],
    },
    # PMO — projects + tasks + sprints + waves + threads + blockers +
    # templates + dashboard. Spec'd by `.ingestion/mappings/projects.json`
    # under `rbac.ai_personas.new.pmo_assistant`.
    "pmo_assistant": {
        "projects": [
            "projects.project.view.*",
            "projects.project.create",
            "projects.project.edit.*",
            "projects.project.change_status",
            "projects.task.view.*",
            "projects.task.create",
            "projects.task.edit.*",
            "projects.task.change_status",
            "projects.task.assign",
            "projects.sprint.view.*",
            "projects.sprint.create",
            "projects.sprint.edit.*",
            "projects.wave.view.*",
            "projects.wave.create",
            "projects.wave.edit.*",
            "projects.thread.view.*",
            "projects.thread.create",
            "projects.thread.edit.*",
            "projects.thread.assign",
            "projects.blocker_ticket.view.*",
            "projects.blocker_ticket.create",
            "projects.blocker_ticket.edit.*",
            "projects.blocker_ticket.change_status",
            "projects.template.view.*",
            "projects.dashboard.view",
        ],
    },
    # Finance — full read access across all FCC modules (Wave 3A: updated for FCC ingestion).
    # Legacy keys finance.portal / finance.analytics are PRESERVED (live RBAC keys used
    # by roles.py and response_filter.py — cannot be removed without a separate cleanup PR).
    # FCC entity-scoped keys are ADDED alongside them so Nova resolves against the
    # new analytical endpoints. operations.contracts.view included for contract financials.
    "finance_assistant": {
        "finance": [
            "finance.portal",
            "finance.analytics",
            "finance.access",
            "finance.kpis.view",
            "finance.statements.view",
            "finance.working_capital.view",
            "finance.gl.view",
            "finance.lists.view",
            "finance.coa_mapping.view",
            "finance.coa.view",
            "finance.revenue.view",
            "finance.meta.view",
            "finance.draft.view",
            "finance.commissions.view",
            "finance.referral_mrr.view",
            "finance.dashboard.view",
            "finance.widget.view",
        ],
        "operations": [
            "operations.contracts.view",
        ],
    },
    # Support — read access to customer-facing entities so the assistant can
    # answer questions but not mutate state without explicit grants.
    # Wave 1E (Projects ingestion): legacy `projects.view.all` retired in
    # favor of the entity-scoped namespace.
    "support_assistant": {
        "contacts": [
            "contacts.view.all",
        ],
        "accounts": [
            "accounts.view.all",
        ],
        "projects": [
            "projects.project.view.*",
            "projects.task.view.*",
        ],
        "field_jobs": [
            "field_jobs.view.all",
        ],
    },
    # General — minimal: dashboards.view + AI chat access.
    "general_assistant": {
        "dashboards": [
            "dashboards.view",
        ],
        "chat": [
            "chat.access",
        ],
    },
}


def _slug_for_perm_key(key: str) -> str:
    """Extract the app slug from a permission key.

    Native perms are flat (`contacts.view.all` -> `contacts`). Ingested-app
    perms follow the same convention since `rbac_generator` writes keys
    prefixed with the app slug.
    """
    return key.split(".", 1)[0] if "." in key else key


def _app_name_for_slug(slug: str, registry_perms: list[dict]) -> str:
    """Best-effort friendly name for an app slug.

    Prefers the explicit map, then falls back to the registry's `group`
    field (matches the slug's first perm), and finally a title-cased
    rendering of the slug itself.
    """
    if slug in _NATIVE_APP_NAMES:
        return _NATIVE_APP_NAMES[slug]
    for perm in registry_perms:
        if _slug_for_perm_key(perm.get("key", "")) == slug and perm.get("group"):
            return perm["group"]
    return slug.replace("_", " ").title()


def _resolve_patterns(
    patterns: list[str],
    active_keys: set[str],
) -> list[str]:
    """Match each pattern against the set of active permission keys.

    Literal keys (no glob characters) only resolve if they're active —
    silently dropped otherwise so we don't write inactive perms onto
    employees.
    """
    matched: set[str] = set()
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            matched.update(fnmatch.filter(active_keys, pattern))
        elif pattern in active_keys:
            matched.add(pattern)
    return sorted(matched)


async def apply_persona_defaults(employee_id: str, persona: str) -> None:
    """Resolve glob patterns against the current registry and write
    `app_access` entries onto the employee.

    Idempotent — applying the same persona twice produces the same final
    state. If the employee already has `app_access` for an app the new
    permissions are MERGED (set-union, no duplicates) rather than
    overwritten; entries for apps not in the persona map are left alone.

    Silently no-ops on unknown personas so callers don't need to defensively
    validate before calling. The unknown-persona case is logged at debug
    level rather than raising so a misconfigured frontend doesn't break
    employee creation.
    """
    persona_map = DEFAULT_APP_ACCESS_BY_PERSONA.get(persona)
    if not persona_map:
        logger.debug(
            "apply_persona_defaults: unknown persona '%s', skipping", persona
        )
        return

    db = get_db()
    employee = await db.ai_employees.find_one({"employee_id": employee_id})
    if not employee:
        logger.warning(
            "apply_persona_defaults: employee %s not found", employee_id
        )
        return

    registry_perms = await get_all_permissions(is_active=True)
    active_keys = {p["key"] for p in registry_perms if p.get("key")}

    # Resolve persona patterns -> {slug: set(keys)}, regrouping by the slug
    # extracted from each resolved key (a pattern listed under one slug
    # could in theory match keys from another, though in practice persona
    # patterns are slug-scoped).
    resolved: dict[str, set[str]] = defaultdict(set)
    for slug, patterns in persona_map.items():
        for key in _resolve_patterns(patterns, active_keys):
            resolved[_slug_for_perm_key(key)].add(key)

    if not resolved:
        logger.info(
            "apply_persona_defaults: persona '%s' resolved to no active "
            "permissions for employee %s",
            persona, employee_id,
        )
        return

    # Merge into existing app_access. Match on app_slug; for ingested apps
    # the same slug also implies the same app_id so this is safe.
    existing_access: list[dict] = list(employee.get("app_access") or [])
    by_slug: dict[str, dict] = {
        entry["app_slug"]: entry
        for entry in existing_access
        if entry.get("app_slug")
    }

    now = datetime.now(timezone.utc).isoformat()
    for slug, keys in resolved.items():
        if slug in by_slug:
            entry = by_slug[slug]
            merged = sorted(set(entry.get("permissions") or []) | keys)
            entry["permissions"] = merged
            # Refresh linked_at so audit shows the most recent grant; do
            # NOT change app_id/app_name to avoid clobbering an ingested
            # app's metadata.
            entry["linked_at"] = now
        else:
            existing_access.append({
                "app_id": None,  # native module — no app registry row
                "app_slug": slug,
                "app_name": _app_name_for_slug(slug, registry_perms),
                "permissions": sorted(keys),
                "linked_at": now,
                "source": "persona_default",
                "persona": persona,
            })

    await db.ai_employees.update_one(
        {"employee_id": employee_id},
        {"$set": {"app_access": existing_access, "updated_at": now}},
    )

    logger.info(
        "apply_persona_defaults: applied persona '%s' to employee %s "
        "(%d apps, %d total perms)",
        persona, employee_id,
        len(resolved), sum(len(v) for v in resolved.values()),
    )
