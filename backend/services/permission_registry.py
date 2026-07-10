"""Dynamic permission registry backed by MongoDB.

Replaces the hardcoded ALL_PERMISSIONS list as the source of truth for
permission discovery and role-creation validation. Native AEGISAXIOM modules
self-register their permissions via the @register_module_permissions
decorator at import time; ingested apps register theirs at apply_rbac()
time. seed_system_permissions writes the union to MongoDB on startup.
"""
from datetime import datetime, timezone
import logging

from database import get_db

logger = logging.getLogger(__name__)


SOURCE_SYSTEM = "system"
SOURCE_INGESTION = "ingestion"
SOURCE_CUSTOM = "custom"
SOURCE_MANUAL_OVERRIDE = "manual_override"
SOURCE_RECONCILE_AUTO = "reconcile_auto"
VALID_SOURCES = {
    SOURCE_SYSTEM,
    SOURCE_INGESTION,
    SOURCE_CUSTOM,
    SOURCE_MANUAL_OVERRIDE,
    SOURCE_RECONCILE_AUTO,
}


# In-memory registry of system permissions declared by native modules via
# @register_module_permissions. Populated at import time as route modules
# are loaded; consumed by seed_system_permissions on startup.
_module_registry: dict = {}


_REQUIRED_PERM_FIELDS = ("key", "group", "label")


def register_module_permissions(module_name: str):
    """Decorator that registers a native module's permission declarations.

    The decorated callable must return a list of permission dicts shaped
    ``{"key": str, "group": str, "label": str}``. Validation runs at
    decoration time so misconfigured modules fail fast on import rather
    than seeding incomplete documents into MongoDB. Re-decorating with
    the same module_name overwrites the previous entry, which keeps
    reload cycles in tests clean.

    Usage:
        @register_module_permissions("core")
        def _core_permissions():
            return [
                {"key": "deals.view.all", "group": "Deals", "label": "..."},
                ...
            ]
    """
    def decorator(fn):
        perms = fn()
        if not isinstance(perms, list):
            raise TypeError(
                f"@register_module_permissions('{module_name}') expected a list, "
                f"got {type(perms).__name__}"
            )
        bad_indices = [
            i for i, p in enumerate(perms)
            if not isinstance(p, dict)
            or any(not p.get(field) for field in _REQUIRED_PERM_FIELDS)
        ]
        if bad_indices:
            raise ValueError(
                f"@register_module_permissions('{module_name}') requires every "
                f"entry to be a dict with non-empty 'key', 'group', and 'label' "
                f"fields; offending indices: {bad_indices}"
            )
        bad_implies = [
            i for i, p in enumerate(perms)
            if "implies" in p and not (
                isinstance(p["implies"], list)
                and all(isinstance(k, str) and k for k in p["implies"])
            )
        ]
        if bad_implies:
            raise ValueError(
                f"@register_module_permissions('{module_name}') 'implies' must be "
                f"a list of non-empty strings when present; offending indices: "
                f"{bad_implies}"
            )
        _module_registry[module_name] = perms
        return fn
    return decorator


def get_registered_module_permissions() -> list:
    """Flatten every module's registered permissions into a single list.

    Used by seed_system_permissions to know what to write to the database
    on startup. Order matches module-registration order (insertion order
    on Python 3.7+ dicts), which is import order in server.py.
    """
    flat = []
    for perms in _module_registry.values():
        flat.extend(perms)
    return flat


async def seed_system_permissions(db=None) -> int:
    """Upsert every registered module permission as source='system'.

    Idempotent: safe to run on every server startup. Only inserts/updates
    documents that don't exist or are already source='system' — never
    overwrites an ingestion or custom permission whose key happens to
    collide with a system one.

    Reads from the in-memory module registry populated by the
    @register_module_permissions decorator. Route modules must be imported
    before this runs (which server.py guarantees by importing all routers
    at module load, well before the on_startup hook fires).
    """
    if db is None:
        db = get_db()

    permissions = get_registered_module_permissions()
    if not permissions:
        # Fail loudly: if the registry is empty when seeding runs, role
        # validation will reject every assignable key on next request, and
        # the admin UI will render an empty permission list. That's a
        # configuration bug — never let it slide as a quiet warning.
        raise RuntimeError(
            "Permission registry is empty — no native modules registered "
            "permissions before seed_system_permissions ran. Import the "
            "route modules that declare permissions before calling this "
            "function (server.py guarantees this by importing all routers "
            "at module load)."
        )
    logger.debug(
        "Permission registry: seeding from %d module(s): %s",
        len(_module_registry),
        list(_module_registry.keys()),
    )

    now = datetime.now(timezone.utc)
    seeded = 0
    promoted = 0
    for perm in permissions:
        # If a key was previously registered by an ingestion run AND is now
        # also declared by the canonical system list (because the ingested app
        # was promoted to a first-class module), promote the row: source goes
        # to system, group/label/implies come from the system declaration. The
        # FCC ingestion is the original case — it claimed several keys before
        # they landed in _core_permissions(), and the picker rendered them in
        # an "Financial Command Center" group that should now read "Finance".
        existing = await db.permissions.find_one(
            {"key": perm["key"]}, {"_id": 0, "source": 1, "app_id": 1}
        )
        if existing and existing.get("source") not in (None, SOURCE_SYSTEM):
            promoted += 1
            logger.info(
                "Permission registry: promoting key '%s' from source=%s app_id=%s "
                "to source=system (now declared in core registry)",
                perm["key"], existing.get("source"), existing.get("app_id"),
            )
        set_fields = {
            "key": perm["key"],
            "group": perm.get("group"),
            "label": perm.get("label"),
            "source": SOURCE_SYSTEM,
            "app_id": None,
            "is_active": True,
            "updated_at": now,
        }
        # Only round-trip `implies` when the declaration includes it; omitting
        # it from $set on perms without the field keeps documents clean rather
        # than persisting an empty list everywhere.
        if "implies" in perm:
            set_fields["implies"] = list(perm["implies"])
        await db.permissions.update_one(
            {"key": perm["key"]},
            {
                "$set": set_fields,
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        seeded += 1
    logger.info(
        f"Permission registry: seeded {seeded} system permissions"
        + (f" (promoted {promoted} from non-system source)" if promoted else "")
    )
    return seeded


async def deactivate_keys(keys: set, db=None) -> int:
    """Mark a set of permission keys as is_active=false.

    Used at startup to retire deprecated keys without deleting their rows
    (deletion would orphan any role still referencing them). Idempotent:
    rows already inactive are not re-touched. Returns the number of newly
    deactivated rows.
    """
    if not keys:
        return 0
    if db is None:
        db = get_db()
    now = datetime.now(timezone.utc)
    result = await db.permissions.update_many(
        {"key": {"$in": list(keys)}, "is_active": True},
        {"$set": {"is_active": False, "updated_at": now}},
    )
    if result.modified_count:
        logger.info(
            "Permission registry: deactivated %d deprecated key(s): %s",
            result.modified_count,
            sorted(keys),
        )
    return result.modified_count


async def strip_keys_from_roles(keys: set, db=None) -> int:
    """Remove a set of permission keys from every role document's `permissions` array.

    Used after deactivating deprecated keys: the role validator (`get_valid_keys`)
    only returns active keys, so any role still carrying a deactivated key would
    fail validation on the next save with a confusing "invalid permission keys"
    error. This sweep cleans them out idempotently. Returns the count of role
    documents modified.
    """
    if not keys:
        return 0
    if db is None:
        db = get_db()
    now = datetime.now(timezone.utc)
    result = await db.roles.update_many(
        {"permissions": {"$in": list(keys)}},
        {"$pull": {"permissions": {"$in": list(keys)}}, "$set": {"updated_at": now}},
    )
    if result.modified_count:
        logger.info(
            "Permission registry: stripped %d deprecated key(s) from %d role document(s)",
            len(keys), result.modified_count,
        )
    return result.modified_count


async def register_permissions(
    permissions: list,
    source: str,
    app_id: str = None,
) -> int:
    """Bulk upsert permissions with source tracking.

    Each permission must be a dict with at least `key`. `group` and `label`
    are optional but recommended. Used by the ingestion pipeline to register
    app-generated permissions and by future admin tooling for custom perms.

    Manual overrides are protected: a permission whose existing `source` is
    `manual_override` will not be overwritten. This mirrors the protection
    `seed_system_permissions` applies for system perms and matches the
    documented contract that admin overrides survive both re-ingestion and
    reconciliation.
    """
    if source not in VALID_SOURCES:
        raise ValueError(f"Invalid source '{source}'. Must be one of: {sorted(VALID_SOURCES)}")
    db = get_db()
    now = datetime.now(timezone.utc)
    registered = 0
    skipped_overrides = 0
    skipped_reconcile_auto = 0
    for perm in permissions:
        key = perm.get("key")
        if not key:
            continue
        implies = perm.get("implies")
        if implies is not None:
            if not (
                isinstance(implies, list)
                and all(isinstance(k, str) and k for k in implies)
            ):
                raise ValueError(
                    f"register_permissions: 'implies' for key '{key}' must be "
                    f"a list of non-empty strings, got {implies!r}"
                )
        # Skip when an admin has manually overridden this key. We never want
        # ingestion / reconcile / custom registrations to silently flip a
        # manual_override back to an automated source. Manual writes go
        # through the explicit override path (add_app_permission, etc.).
        existing = await db.permissions.find_one(
            {"key": key}, {"_id": 0, "source": 1}
        )
        if existing and existing.get("source") == SOURCE_MANUAL_OVERRIDE:
            skipped_overrides += 1
            logger.info(
                f"Permission registry: key '{key}' has source='manual_override'; "
                f"skipping {source} re-registration to preserve override."
            )
            continue
        # Don't let a coarser ingestion re-registration flatten the audit
        # trail of a key that the reconciler discovered. reconcile_auto is
        # a finer-grained source than ingestion (the reconciler synthesized
        # it from runtime field_registry state). On the next /ingest-app
        # run, register_permissions(..., source="ingestion") would otherwise
        # overwrite the reconcile_auto attribution silently.
        if (
            existing
            and existing.get("source") == SOURCE_RECONCILE_AUTO
            and source == SOURCE_INGESTION
        ):
            skipped_reconcile_auto += 1
            logger.info(
                f"Permission registry: key '{key}' has source='reconcile_auto'; "
                f"skipping ingestion re-registration to preserve attribution."
            )
            continue
        set_fields = {
            "key": key,
            "group": perm.get("group"),
            "label": perm.get("label"),
            "source": source,
            "app_id": app_id,
            "is_active": True,
            "updated_at": now,
        }
        if implies is not None:
            set_fields["implies"] = list(implies)
        await db.permissions.update_one(
            {"key": key},
            {
                "$set": set_fields,
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        registered += 1
    if registered:
        logger.info(
            f"Permission registry: registered {registered} permissions "
            f"(source={source}, app_id={app_id})"
        )
    if skipped_overrides:
        logger.info(
            f"Permission registry: skipped {skipped_overrides} manual_override "
            f"permission(s) during {source} registration."
        )
    if skipped_reconcile_auto:
        logger.info(
            f"Permission registry: skipped {skipped_reconcile_auto} reconcile_auto "
            f"permission(s) during {source} registration to preserve attribution."
        )
    return registered


async def deactivate_permissions(app_id: str, keys_to_keep: set) -> int:
    """Mark this app's permissions inactive when they no longer appear in
    the latest generated set. Used during re-ingestion so removed entities
    don't leave stale permission keys live in the registry.

    Manual-override permissions (source=manual_override) are immune from
    deactivation: an admin who hand-flipped a permission's source to
    manual_override is asserting it must persist across re-ingest cycles
    regardless of whether the generator still emits it. Skipping them in
    the query preserves that intent.

    Does NOT delete documents — keeping them lets historical role
    assignments stay readable for audit.
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    keys_list = list(keys_to_keep) if keys_to_keep else []
    # `source: SOURCE_INGESTION` already excludes manual_override docs,
    # but we add an explicit $and clause with $ne SOURCE_MANUAL_OVERRIDE
    # so the immunity is enforced even if some future refactor relaxes
    # the source equality filter. Manual overrides must never be flipped
    # inactive by re-ingestion.
    result = await db.permissions.update_many(
        {
            "app_id": app_id,
            "is_active": True,
            "key": {"$nin": keys_list},
            "$and": [
                {"source": SOURCE_INGESTION},
                {"source": {"$ne": SOURCE_MANUAL_OVERRIDE}},
            ],
        },
        {"$set": {"is_active": False, "updated_at": now}},
    )
    return result.modified_count


async def get_all_permissions(
    source: str = None,
    group: str = None,
    app_id: str = None,
    is_active: bool = True,
) -> list:
    """Query the registry with optional filters.

    Returns documents shaped like the hardcoded ALL_PERMISSIONS entries
    (`key`, `group`, `label`) plus registry metadata (`source`, `app_id`,
    `is_active`) so callers can render or filter by origin.
    """
    db = get_db()
    query = {}
    if is_active is not None:
        query["is_active"] = is_active
    if source:
        query["source"] = source
    if group:
        query["group"] = group
    if app_id:
        query["app_id"] = app_id

    cursor = db.permissions.find(query, {"_id": 0}).sort("key", 1)
    return await cursor.to_list(length=None)


async def get_valid_keys(is_active: bool = True) -> set:
    """Return the set of registered permission keys for role-validation."""
    db = get_db()
    query = {}
    if is_active is not None:
        query["is_active"] = is_active
    cursor = db.permissions.find(query, {"_id": 0, "key": 1})
    docs = await cursor.to_list(length=None)
    return {d["key"] for d in docs if d.get("key")}


async def get_implication_graph(db=None) -> dict:
    """Return ``{perm_key: [implied_keys, ...]}`` for every active permission.

    Only registry-defined `implies` edges are reflected here. Static
    convention rules (e.g., `*.update` implies `*.view`) live in
    `permission_implications.py` and are NOT merged into this graph —
    callers that need the full closure must combine both sources.

    Permissions without an `implies` field are returned with an empty list,
    so the caller always gets a complete adjacency map keyed by every active
    permission. Edges that point to keys not present in the registry are
    preserved as-is; the DAG validator treats them as terminal nodes.
    """
    if db is None:
        db = get_db()
    cursor = db.permissions.find(
        {"is_active": True},
        {"_id": 0, "key": 1, "implies": 1},
    )
    graph: dict = {}
    async for doc in cursor:
        key = doc.get("key")
        if not key:
            continue
        edges = doc.get("implies") or []
        # Defensive copy + filter to drop any non-string or empty entries
        # that may have slipped past validation in older docs.
        graph[key] = [e for e in edges if isinstance(e, str) and e]
    return graph


async def validate_dag(db=None) -> None:
    """Walk the implication graph; raise RuntimeError on the first cycle.

    Run at startup right after seed_system_permissions. Static convention
    rules are NOT in scope — only registry-defined `implies` edges are
    validated here.

    Uses iterative DFS with a recursion stack so cycle detection works on
    arbitrarily deep graphs without blowing the Python stack. The error
    message includes the full cycle path (e.g., ``A -> B -> C -> A``) so
    operators can pinpoint the offending declarations quickly.
    """
    graph = await get_implication_graph(db=db)

    # Tri-state coloring: 0=unvisited, 1=on current DFS path, 2=fully explored.
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict = {node: WHITE for node in graph}

    for root in graph:
        if color[root] != WHITE:
            continue
        # Each stack frame: (node, iterator over its outgoing edges)
        stack = [(root, iter(graph.get(root, [])))]
        path = [root]
        color[root] = GRAY
        while stack:
            node, edges = stack[-1]
            next_edge = next(edges, None)
            if next_edge is None:
                color[node] = BLACK
                stack.pop()
                path.pop()
                continue
            # Edges to keys not in the registry are terminal — skip cleanly.
            if next_edge not in color:
                continue
            state = color[next_edge]
            if state == GRAY:
                # Found a back-edge: cycle from next_edge through path back to itself.
                cycle_start = path.index(next_edge)
                cycle = path[cycle_start:] + [next_edge]
                raise RuntimeError(
                    "Permission registry: cycle detected in implication graph: "
                    + " -> ".join(cycle)
                )
            if state == WHITE:
                color[next_edge] = GRAY
                path.append(next_edge)
                stack.append((next_edge, iter(graph.get(next_edge, []))))
            # state == BLACK: already fully explored, no cycle through here.
    logger.info(
        "Permission registry: implication DAG validated (%d nodes)",
        len(graph),
    )


