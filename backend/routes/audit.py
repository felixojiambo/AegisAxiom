import time
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional

from database import get_db
from auth_helper import get_current_user, has_perm
from services.field_registry import register_fields, register_module_events, register_module_config

router = APIRouter()

# Process-local cache for /known-modules. Admin-only, low-traffic endpoint, but
# the audit_logs distinct + policy_targets union runs on every page render of
# the audit admin UI filter — caching for 60s keeps it cheap without hiding
# new ingested modules for long. In multi-worker deployments each worker holds
# its own copy; staleness window is bounded by TTL.
_known_modules_cache: dict = {"data": None, "timestamp": 0.0}
_KNOWN_MODULES_TTL = 60  # seconds

register_module_config("audit", "audit_logs", "audit_id", "Admin")
register_module_events("audit", "audit_log", [])
register_fields("audit", "audit_log", [
    {"name": "action", "type": "string", "label": "Action", "description": "What action was performed (e.g., policy_updated, user_created)"},
    {"name": "actor_email", "type": "string", "label": "Actor", "description": "Email of the user who performed the action"},
    {"name": "resource_type", "type": "string", "label": "Resource Type", "description": "Type of resource affected (policy, user, contract, etc.)"},
    {"name": "resource_id", "type": "string", "label": "Resource ID", "description": "ID of the affected resource"},
    {"name": "event_category", "type": "string", "label": "Category", "description": "activity (CRUD events) or security (RBAC denials, auth failures, role changes)"},
    {"name": "event_type", "type": "string", "label": "Event Type", "description": "Specific security event such as authz_failed, auth_failed, role_changed"},
    {"name": "severity", "type": "string", "label": "Severity", "description": "info, warning, or critical (security events only)"},
    {"name": "outcome", "type": "string", "label": "Outcome", "description": "success, denied, or error (security events only)"},
])


@router.get("")
async def get_audit_logs(
    skip: int = 0,
    limit: int = 50,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    actor_id: Optional[str] = None,
    event_category: Optional[str] = None,
    event_type: Optional[str] = None,
    severity: Optional[str] = None,
    outcome: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    """Paginated audit log query.

    Reads from the unified audit_logs collection. Activity events
    (event_category="activity") come from services.audit_service;
    security events (event_category="security") from
    services.security_logger. Legacy entries written before the schema
    unification have no event_category — when the caller asks for
    event_category="activity", those legacy entries are included
    (treated as activity by default) so the existing UI shows the same
    history it always did.
    """
    if not has_perm(current_user, "audit.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    # Build the filter as a list of conjunct clauses so any future addition
    # (e.g., date-range, multi-condition tag) can append safely without
    # silently overwriting an existing $or clause. Falls back to a flat
    # filter when only one clause is present so simple queries don't carry
    # extra Mongo machinery.
    clauses: list = []
    if action:
        clauses.append({"action": action})
    if resource_type:
        # Trailing-dot value = module-only filter (e.g. "davies_pmo.") sent by
        # the admin UI when a module is selected without an entity. Translate
        # to a prefix match so every entity under that module surfaces.
        if resource_type.endswith("."):
            import re as _re
            clauses.append({
                "resource_type": {"$regex": f"^{_re.escape(resource_type)}"}
            })
        else:
            clauses.append({"resource_type": resource_type})
    if actor_id:
        clauses.append({"actor_id": actor_id})
    if event_category:
        if event_category == "activity":
            # Legacy entries written before the schema unification have no
            # event_category — surface them under "Activity" so the
            # existing UI keeps showing the same history.
            clauses.append({
                "$or": [
                    {"event_category": "activity"},
                    {"event_category": {"$exists": False}},
                ]
            })
        else:
            clauses.append({"event_category": event_category})
    if event_type:
        clauses.append({"event_type": event_type})
    if severity:
        clauses.append({"severity": severity})
    if outcome:
        clauses.append({"outcome": outcome})
    if not clauses:
        filt = {}
    elif len(clauses) == 1:
        filt = clauses[0]
    else:
        filt = {"$and": clauses}
    docs = (
        await db.audit_logs.find(filt, {"_id": 0})
        .sort("timestamp", -1)
        .skip(skip)
        .limit(min(limit, 200))
        .to_list(min(limit, 200))
    )
    total = await db.audit_logs.count_documents(filt)
    return {"items": docs, "total": total, "skip": skip, "limit": limit}


@router.get("/known-modules")
async def get_known_modules(current_user=Depends(get_current_user)):
    """Module/entity catalog for the audit log filter dropdown.

    Combines two sources:
      - Distinct `resource_type` values from `audit_logs` shaped as
        `module.entity` (skipping native single-token resource_types like
        `contract` or `user`, which use a different filter path).
      - The `policy_targets` collection — surfaces ingested modules even
        when no audit activity has been recorded yet.

    For each (module, entity), `event_count` is the number of distinct
    `event_type` values seen in `audit_logs`. `source` is `ingestion` if
    the entity appears in `policy_targets`, otherwise `native`.

    Cached process-locally with a 60s TTL — admin-only, low traffic, but
    invoked on every Audit Log admin UI render.
    """
    if not has_perm(current_user, "audit.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    now = time.time()
    cached = _known_modules_cache["data"]
    if cached is not None and (now - _known_modules_cache["timestamp"]) < _KNOWN_MODULES_TTL:
        return cached

    db = get_db()

    # 1. Pull distinct resource_types from audit_logs and keep only the
    #    `module.entity` shaped ones. Native single-token resource types
    #    (e.g. "contract", "user", "policy") are surfaced through a
    #    separate filter and don't belong in the module-level dropdown.
    audit_resource_types = await db.audit_logs.distinct("resource_type")
    audit_pairs: dict = {}  # (module, entity) -> True
    for rt in audit_resource_types:
        if not rt or not isinstance(rt, str) or "." not in rt:
            continue
        module, _, entity = rt.partition(".")
        if not module or not entity or "." in entity:
            continue
        audit_pairs[(module, entity)] = True

    # 2. Pull active rows from policy_targets so freshly-ingested modules
    #    appear before any audit activity exists. Tolerant of a missing
    #    collection on fresh deploys.
    ingestion_pairs: set = set()
    try:
        collections = await db.list_collection_names()
        if "policy_targets" in collections:
            target_docs = await db.policy_targets.find(
                {"is_active": True},
                {"_id": 0, "module": 1, "entity": 1},
            ).to_list(5000)
            for doc in target_docs:
                module = doc.get("module")
                entity = doc.get("entity")
                if module and entity:
                    ingestion_pairs.add((module, entity))
    except Exception:
        # Don't fail the dropdown if policy_targets is unreachable —
        # the audit_logs-derived pairs are still useful on their own.
        ingestion_pairs = set()

    # 3. Union, then group by module.
    all_pairs: set = set(audit_pairs.keys()) | ingestion_pairs
    by_module: dict = {}
    for module, entity in all_pairs:
        by_module.setdefault(module, set()).add(entity)

    # 4. event_count per (module, entity): distinct event_type values
    #    seen in audit_logs filtered by that resource_type. Done as one
    #    distinct call per pair — tiny number of pairs, indexed query.
    result: list = []
    for module in sorted(by_module.keys()):
        entities = sorted(by_module[module])
        # source: prefer "ingestion" when policy_targets registers any
        # entity in this module. A module with even one ingested entity
        # is treated as ingestion-sourced overall, since that's the more
        # specific signal for the admin UI.
        module_source = (
            "ingestion"
            if any((module, e) in ingestion_pairs for e in entities)
            else "native"
        )
        event_count = 0
        for entity in entities:
            rt = f"{module}.{entity}"
            event_types = await db.audit_logs.distinct("event_type", {"resource_type": rt})
            # distinct() returns [] when nothing matches; filter out
            # empty/None entries so legacy rows missing event_type don't
            # inflate the count.
            event_count += sum(1 for et in event_types if et)
        result.append({
            "module": module,
            "entities": entities,
            "event_count": event_count,
            "source": module_source,
        })

    _known_modules_cache["data"] = result
    _known_modules_cache["timestamp"] = now
    return result
