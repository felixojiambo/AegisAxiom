"""Dynamic permission registry backed by PostgreSQL (Phase 2b).

Replaces the hardcoded ALL_PERMISSIONS list as the source of truth for
permission discovery and role-creation validation. Native AEGISAXIOM modules
self-register their permissions via the @register_module_permissions
decorator at import time; ingested apps register theirs at apply_rbac()
time. seed_system_permissions writes the union to the ``permissions`` table
on startup through ``repositories/permissions.py``.
"""
from contextlib import asynccontextmanager
import logging
import os
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_sessionmaker
from repositories.permissions import PermissionsRepository
from repositories.roles import RolesRepository

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
    than seeding incomplete rows into the registry. Re-decorating with
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


# ---------------------------------------------------------------------------
# Session scope (Phase 2b)
# ---------------------------------------------------------------------------
# Every persisted-registry function takes an optional ``session``. Request
# handlers pass their ``pg = Depends(get_session)`` so the work joins the
# request transaction; startup / ingestion / background callers pass nothing
# and get a short-lived session that commits on success. The legacy ``db``
# (Mongo) positional parameter is still ACCEPTED and IGNORED so callers outside
# this phase's scope (e.g. seeds/seed_all.py) keep working unchanged.
@asynccontextmanager
async def _scope(session: Optional[AsyncSession]):
    if session is not None:
        yield session
        return
    async with get_sessionmaker()() as own:
        try:
            yield own
            await own.commit()
        except Exception:
            await own.rollback()
            raise


async def seed_system_permissions(db=None, *, session: Optional[AsyncSession] = None) -> int:
    """Upsert every registered module permission as source='system'.

    Idempotent: safe to run on every server startup. A key previously
    registered by ingestion/custom is PROMOTED to system when the canonical
    registry now declares it (logged).

    Reads from the in-memory module registry populated by the
    @register_module_permissions decorator. Route modules must be imported
    before this runs (which server.py guarantees by importing all routers
    at module load, well before the on_startup hook fires).
    """
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

    seeded = 0
    promoted = 0
    async with _scope(session) as s:
        repo = PermissionsRepository(s)
        for perm in permissions:
            # If a key was previously registered by an ingestion run AND is now
            # also declared by the canonical system list (because the ingested
            # app was promoted to a first-class module), promote the row:
            # source goes to system, group/label/implies come from the system
            # declaration. The FCC ingestion is the original case.
            existing = await repo.get_permission(perm["key"])
            if existing and existing.source not in (None, SOURCE_SYSTEM):
                promoted += 1
                logger.info(
                    "Permission registry: promoting key '%s' from source=%s app_id=%s "
                    "to source=system (now declared in core registry)",
                    perm["key"], existing.source, existing.app_id,
                )
            # Only round-trip `implies` when the declaration includes it.
            kwargs = {"implies": list(perm["implies"])} if "implies" in perm else {}
            await repo.upsert_permission(
                perm["key"],
                source=SOURCE_SYSTEM,
                group=perm.get("group"),
                label=perm.get("label"),
                app_id=None,
                **kwargs,
            )
            seeded += 1
    logger.info(
        f"Permission registry: seeded {seeded} system permissions"
        + (f" (promoted {promoted} from non-system source)" if promoted else "")
    )
    return seeded


async def deactivate_keys(keys: set, db=None, *, session: Optional[AsyncSession] = None) -> int:
    """Mark a set of permission keys as is_active=false.

    Used at startup to retire deprecated keys without deleting their rows
    (deletion would orphan any role still referencing them). Idempotent:
    rows already inactive are not re-touched. Returns the number of newly
    deactivated rows.
    """
    if not keys:
        return 0
    async with _scope(session) as s:
        n = await PermissionsRepository(s).deactivate_keys(keys)
    if n:
        logger.info(
            "Permission registry: deactivated %d deprecated key(s): %s", n, sorted(keys)
        )
    return n


async def strip_keys_from_roles(keys: set, db=None, *, session: Optional[AsyncSession] = None) -> int:
    """Remove a set of permission keys from every role's ``permissions`` array.

    Used after deactivating deprecated keys: the role validator (`get_valid_keys`)
    only returns active keys, so any role still carrying a deactivated key would
    fail validation on the next save with a confusing "invalid permission keys"
    error. One atomic UPDATE (RolesRepository). Returns the count of roles modified.
    """
    if not keys:
        return 0
    async with _scope(session) as s:
        names = await RolesRepository(s).strip_permissions_from_all_roles(keys)
    if names:
        logger.info(
            "Permission registry: stripped %d deprecated key(s) from %d role(s)",
            len(keys), len(names),
        )
    return len(names)


async def register_permissions(
    permissions: list,
    source: str,
    app_id: str = None,
    *,
    session: Optional[AsyncSession] = None,
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
    registered = 0
    skipped_overrides = 0
    skipped_reconcile_auto = 0
    async with _scope(session) as s:
        repo = PermissionsRepository(s)
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
            # Skip when an admin has manually overridden this key. Manual writes
            # go through the explicit override path (add_app_permission, etc.).
            existing = await repo.get_permission(key)
            if existing and existing.source == SOURCE_MANUAL_OVERRIDE:
                skipped_overrides += 1
                logger.info(
                    f"Permission registry: key '{key}' has source='manual_override'; "
                    f"skipping {source} re-registration to preserve override."
                )
                continue
            # Don't let a coarser ingestion re-registration flatten the audit
            # trail of a key the reconciler discovered (reconcile_auto is finer
            # grained than ingestion).
            if (
                existing
                and existing.source == SOURCE_RECONCILE_AUTO
                and source == SOURCE_INGESTION
            ):
                skipped_reconcile_auto += 1
                logger.info(
                    f"Permission registry: key '{key}' has source='reconcile_auto'; "
                    f"skipping ingestion re-registration to preserve attribution."
                )
                continue
            kwargs = {"implies": list(implies)} if implies is not None else {}
            await repo.upsert_permission(
                key,
                source=source,
                group=perm.get("group"),
                label=perm.get("label"),
                app_id=app_id,
                **kwargs,
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


async def deactivate_permissions(
    app_id: str, keys_to_keep: set, *, session: Optional[AsyncSession] = None
) -> int:
    """Mark this app's permissions inactive when they no longer appear in
    the latest generated set. Used during re-ingestion so removed entities
    don't leave stale permission keys live in the registry.

    Only source='ingestion' rows are touched, so manual_override (and
    reconcile_auto) permissions are immune — an admin who hand-flipped a key
    to manual_override is asserting it must persist across re-ingest cycles.

    Does NOT delete rows — keeping them lets historical role assignments stay
    readable for audit.
    """
    async with _scope(session) as s:
        return await PermissionsRepository(s).deactivate_stale_ingestion_keys(
            app_id, keys_to_keep or set(), ingestion_source=SOURCE_INGESTION
        )


async def get_all_permissions(
    source: str = None,
    group: str = None,
    app_id: str = None,
    is_active: bool = True,
    *,
    session: Optional[AsyncSession] = None,
) -> list:
    """Query the registry with optional filters.

    Returns documents shaped like the hardcoded ALL_PERMISSIONS entries
    (`key`, `group`, `label`) plus registry metadata (`source`, `app_id`,
    `is_active`) so callers can render or filter by origin. Sorted by key.
    """
    async with _scope(session) as s:
        records = await PermissionsRepository(s).list_permissions(
            source=source, group=group, app_id=app_id, is_active=is_active
        )
    return [r.to_api_dict() for r in records]


async def get_valid_keys(is_active: bool = True, *, session: Optional[AsyncSession] = None) -> set:
    """Return the set of registered permission keys for role-validation."""
    async with _scope(session) as s:
        return await PermissionsRepository(s).get_valid_keys(is_active=is_active)


async def get_implication_graph(db=None, *, session: Optional[AsyncSession] = None) -> dict:
    """Return ``{perm_key: [implied_keys, ...]}`` for every active permission.

    Only registry-defined `implies` edges are reflected here. Static
    convention rules (e.g., `*.update` implies `*.view`) live in
    `permission_implications.py` and are NOT merged into this graph —
    callers that need the full closure must combine both sources.

    Permissions without an `implies` declaration are returned with an empty
    list, so the caller always gets a complete adjacency map keyed by every
    active permission. Edges that point to keys not present in the registry
    are preserved as-is; the DAG validator treats them as terminal nodes.
    """
    async with _scope(session) as s:
        return await PermissionsRepository(s).get_implication_graph()


def _registry_closure(keys: set, graph: dict) -> set:
    """Transitive closure of ``keys`` over registry ``implies`` edges only."""
    out = set(keys)
    stack = list(keys)
    while stack:
        for nxt in graph.get(stack.pop(), []):
            if nxt not in out:
                out.add(nxt)
                stack.append(nxt)
    return out


async def retire_orphaned_keys(keys: set, *, session: Optional[AsyncSession] = None) -> dict:
    """DELETE orphaned permission keys without changing any role's effective access.

    In ONE transaction, in this order (params sweep orphaned-nova-permissions):
      1. Read the implication graph while the keys still exist; for every role
         holding a retiring key, grant the SURVIVING keys those retiring keys
         implied (e.g. finance.nova.run_projection -> finance.cashflow.view),
         so dropping the key cannot silently revoke access it conferred.
      2. Delete the registry rows.
      3. Strip the keys from every role's permissions array.
      4. Re-run DAG validation on the post-delete graph; a cycle raises and the
         whole transaction rolls back.
    Idempotent: a second run finds nothing to do. Returns a summary.
    """
    keys = set(keys or ())
    if not keys:
        return {"deleted": [], "stripped_from": [], "granted": {}}
    async with _scope(session) as s:
        perms = PermissionsRepository(s)
        roles = RolesRepository(s)

        graph_before = await perms.get_implication_graph()
        granted: dict = {}
        for role in await roles.list_roles():
            held = keys & set(role.permissions)
            if not held:
                continue
            implied = _registry_closure(held, graph_before) - keys - set(role.permissions)
            if implied:
                await roles.add_permissions(sorted(implied), role_id=role.role_id)
                granted[role.name] = sorted(implied)

        deleted = await perms.delete_keys(keys)
        stripped_from = await roles.strip_permissions_from_all_roles(keys)

        cycle = find_cycle(await perms.get_implication_graph())
        if cycle:
            raise RuntimeError(
                "Permission registry: cycle detected after retiring orphaned keys: "
                + " -> ".join(cycle)
            )
    if deleted or stripped_from or granted:
        logger.info(
            "Permission registry: retired orphaned keys %s (deleted rows=%s; "
            "stripped from roles=%s; preserved implied access=%s)",
            sorted(keys), deleted, stripped_from, granted,
        )
    return {"deleted": deleted, "stripped_from": stripped_from, "granted": granted}


def find_cycle(graph: dict) -> Optional[list]:
    """Return the first cycle in ``graph`` as a closed path, or None.

    Iterative DFS with tri-state coloring (0=unvisited, 1=on the current DFS
    path, 2=fully explored), so arbitrarily deep graphs never blow the Python
    stack. Edges to keys absent from ``graph`` are terminal. Pure function —
    unchanged logic from the Mongo era, lifted out so it is testable without a
    database.
    """
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
                # Back-edge: cycle from next_edge through path back to itself.
                cycle_start = path.index(next_edge)
                return path[cycle_start:] + [next_edge]
            if state == WHITE:
                color[next_edge] = GRAY
                path.append(next_edge)
                stack.append((next_edge, iter(graph.get(next_edge, []))))
            # state == BLACK: already fully explored, no cycle through here.
    return None


async def validate_dag(db=None, *, session: Optional[AsyncSession] = None) -> None:
    """Walk the implication graph; raise RuntimeError on the first cycle.

    Static convention rules are NOT in scope — only registry-defined
    `implies` edges are validated here. The error message includes the full
    cycle path (e.g., ``A -> B -> C -> A``) so operators can pinpoint the
    offending declarations quickly.
    """
    graph = await get_implication_graph(session=session)
    cycle = find_cycle(graph)
    if cycle:
        raise RuntimeError(
            "Permission registry: cycle detected in implication graph: "
            + " -> ".join(cycle)
        )
    logger.info(
        "Permission registry: implication DAG validated (%d nodes)",
        len(graph),
    )


def _skip_startup_dag_validation() -> bool:
    return os.getenv("SKIP_STARTUP_DAG_VALIDATION", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


async def validate_dag_at_startup(*, session: Optional[AsyncSession] = None) -> str:
    """Boot-survivable DAG validation (plan §A7). Returns the outcome label.

    * ``SKIP_STARTUP_DAG_VALIDATION`` set → log and return ``"skipped"``.
    * Postgres unreachable / unreadable → log an error and return
      ``"unavailable"``: an infrastructure fault must not hard-crash boot
      (the audit §5d "Atlas DAG-validation crash").
    * Empty registry → log a warning and return ``"empty"``.
    * A REAL cycle on a reachable registry → raise RuntimeError. A corrupt
      permission DAG is a correctness bug and still fails boot loudly.
    * Otherwise ``"ok"``.

    Pattern for future startup validators: SKIP_* flag + catch only the
    READ failure, never the validation verdict (CONVENTIONS, Phase 2b).
    """
    if _skip_startup_dag_validation():
        logger.warning(
            "Permission registry: DAG validation SKIPPED at startup "
            "(SKIP_STARTUP_DAG_VALIDATION is set)"
        )
        return "skipped"
    try:
        graph = await get_implication_graph(session=session)
    except Exception as exc:  # noqa: BLE001 — the read, not the verdict
        logger.error(
            "Permission registry: DAG validation could not read the registry "
            "(%s: %s); continuing boot without validation",
            type(exc).__name__, exc,
        )
        return "unavailable"
    if not graph:
        logger.warning(
            "Permission registry: DAG validation found an empty registry; "
            "nothing to validate (did seed_system_permissions run?)"
        )
        return "empty"
    cycle = find_cycle(graph)
    if cycle:
        raise RuntimeError(
            "Permission registry: cycle detected in implication graph: "
            + " -> ".join(cycle)
        )
    logger.info(
        "Permission registry: implication DAG validated (%d nodes)", len(graph)
    )
    return "ok"
