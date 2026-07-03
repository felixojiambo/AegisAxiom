"""
Policy Engine Field Registry

Central registry where modules register their available fields for policy evaluation
and AI agent schema discovery. Each module calls register_fields() at import time to
declare what fields are available. Fields become available in the Policy Admin GUI
and AI agent prompts immediately.
"""

_registry = {}
_event_registry = {}
_module_config = {}


def register_fields(module: str, entity: str, fields: list):
    """
    Register fields for a module/entity pair. Called at import time by each module's route file.

    Args:
        module: Module name (e.g., "time_tracking", "field_services")
        entity: Entity name (e.g., "time_entry", "field_job")
        fields: List of field definitions, each a dict with:
            - name: str (field key in the policy evaluation payload)
            - type: str (string, number, time, date, boolean, list)
            - label: str (human-readable label for the admin GUI)
            - description: str (optional, semantic description for AI agents and admin tooltips)
            - computed: bool (optional, True if field is calculated by backend at evaluation time)
            - options: list (optional, list of {value, label} dicts for picklist fields)
    """
    _registry[(module, entity)] = fields


def get_fields(module: str, entity: str) -> list:
    """Get registered fields for a module/entity pair."""
    return _registry.get((module, entity), [])


def get_all_modules() -> list:
    """Get all registered module names, sorted."""
    return sorted(set(m for m, _ in _registry.keys()))


def get_entities_for_module(module: str) -> list:
    """Get all entity names registered under a module."""
    return sorted(e for m, e in _registry.keys() if m == module)


def get_all_registrations() -> dict:
    """Get the full registry as {module: [entities]}."""
    result = {}
    for (module, entity) in _registry.keys():
        result.setdefault(module, []).append(entity)
    for module in result:
        result[module] = sorted(result[module])
    return result


# ==================== EVENT REGISTRATION ====================

def register_module_events(module: str, entity: str, events: list):
    """Register available events for a module/entity pair.
    Called at import time by each route file alongside register_fields().
    """
    _event_registry[(module, entity)] = events


def get_module_events(module: str, entity: str) -> list:
    """Get registered events for a module/entity pair."""
    return _event_registry.get((module, entity), [])


def get_all_module_events() -> dict:
    """Get all registered events as {module: {entity: [events]}}."""
    result = {}
    for (module, entity), events in _event_registry.items():
        result.setdefault(module, {})[entity] = events
    return result


def list_modules_and_entities() -> list:
    """Return every registered (module, entity) combo with its events and fields.

    Walks the in-memory _registry (fields) and _event_registry (events) to
    produce a unified list. Either side alone is sufficient to surface a target
    — a module/entity registered for fields but with no events declared still
    appears (with events=[]), and vice versa.

    Returns:
        list of dicts: [{module, entity, events, fields}, ...]
    """
    combos = set(_registry.keys()) | set(_event_registry.keys())
    result = []
    for (module, entity) in sorted(combos):
        result.append({
            "module": module,
            "entity": entity,
            "events": _event_registry.get((module, entity), []),
            "fields": _registry.get((module, entity), []),
        })
    return result


# ==================== MODULE CONFIG (collection mapping) ====================

def register_module_config(module: str, collection: str, id_field: str, category: str = "Other"):
    """Register the MongoDB collection name, ID field, and UI category for a module.
    Used by the workflow engine to resolve module -> collection dynamically.
    Category is used by the admin GUI to group modules in dropdown menus.
    """
    _module_config[module] = {"collection": collection, "id_field": id_field, "category": category}


def get_module_config(module: str) -> dict:
    """Get the collection config for a module."""
    return _module_config.get(module, {})


def get_all_module_configs() -> dict:
    """Get all module configs."""
    return dict(_module_config)


# ==================== AI SCHEMA BRIDGE ====================

def get_fields_for_collection(collection_name: str) -> dict[str, str]:
    """Resolve collection_name -> all matching modules -> merged fields as {name: "(type) — description"}.

    Used by the AI schema layer (services/ai/schema.py) as a fallback between
    per-agent curated descriptions and auto-discovered types.

    Handles multiple modules mapping to the same collection (e.g., admin,
    agent_dashboard, user_resources all map to 'users') by merging fields
    from all matching modules.

    Returns:
        Dict mapping field names to formatted description strings.
        Fields with a description: "(type) — description"
        Fields without: "(type)"
    """
    # Find ALL modules that map to this collection
    matching_modules = [
        mod for mod, config in _module_config.items()
        if config.get("collection") == collection_name
    ]
    if not matching_modules:
        return {}

    # Gather fields from all entities under all matching modules
    result = {}
    for module in matching_modules:
        for (mod, entity), fields in _registry.items():
            if mod != module:
                continue
            for f in fields:
                name = f.get("name", "")
                if name in result:
                    continue  # first registration wins for duplicate field names
                ftype = f.get("type", "string")
                desc = f.get("description", "")
                if desc:
                    result[name] = f"({ftype}) — {desc}"
                else:
                    result[name] = f"({ftype})"
    return result
