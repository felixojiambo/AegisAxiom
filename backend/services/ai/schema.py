"""Schema introspection — auto-discovers MongoDB collection fields at startup.

Samples documents from each collection, infers field types, and caches
the results. Field descriptions are resolved with a 3-tier fallback:
  1. Per-agent curated descriptions (richest — AI-specific context)
  2. Field registry descriptions (good default — written once during module registration)
  3. Auto-discovered types from MongoDB sampling (bare minimum — just the type)
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Module-level cache: {collection_name: {field_name: inferred_type_str}}
_SCHEMA_CACHE: dict[str, dict[str, str]] = {}


def _infer_type(value) -> str:
    """Infer a human-readable type string from a Python value."""
    if value is None:
        return "string"  # Can't infer from None, default to string
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "number"
    if isinstance(value, float):
        return "number"
    if isinstance(value, datetime):
        return "date"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return "string"


async def discover(db, collections: list[str] = None):
    """Sample documents from collections and cache field schemas.

    Args:
        db: Motor async database instance.
        collections: Optional list of collection names to discover.
                     If None, discovers all collections.
    """
    global _SCHEMA_CACHE

    try:
        if collections is None:
            collections = await db.list_collection_names()

        for name in collections:
            fields: dict[str, str] = {}
            # Sample up to 5 docs to get a broader view of fields
            docs = await db[name].find({}, {"_id": 0}).limit(5).to_list(5)

            for doc in docs:
                for field_name, value in doc.items():
                    if field_name not in fields and value is not None:
                        fields[field_name] = _infer_type(value)
                    elif field_name not in fields:
                        fields[field_name] = "string"

            if fields:
                _SCHEMA_CACHE[name] = fields

        logger.info(f"Schema discovery complete: {len(_SCHEMA_CACHE)} collections, "
                     f"{sum(len(f) for f in _SCHEMA_CACHE.values())} total fields")

    except Exception as e:
        logger.warning(f"Schema discovery failed: {e}")


def get_schema(collection_name: str) -> dict[str, str]:
    """Return cached field→type map for a collection, or empty dict."""
    return _SCHEMA_CACHE.get(collection_name, {})


def build_collection_schema(
    collection_name: str,
    label: str,
    field_descriptions: dict[str, str] = None,
) -> str:
    """Build a schema string for one collection.

    Combines auto-discovered fields with curated descriptions.
    Curated descriptions override auto-discovered types.

    Args:
        collection_name: MongoDB collection name.
        label: Human-readable label (e.g. "primary deal data").
        field_descriptions: Optional {field_name: "description string"} overrides.

    Returns:
        Formatted schema string for the query builder prompt.
    """
    from services.field_registry import get_fields_for_collection

    discovered = get_schema(collection_name)
    registry = get_fields_for_collection(collection_name)
    descriptions = field_descriptions or {}

    # 3-tier merge: curated > field registry > auto-discovered
    all_fields = {}
    # Tier 1: per-agent curated descriptions (richest)
    for field, desc in descriptions.items():
        all_fields[field] = desc
    # Tier 2: field registry descriptions (good default)
    for field, desc in registry.items():
        if field not in all_fields:
            all_fields[field] = desc
    # Tier 3: auto-discovered types (bare minimum)
    for field, inferred_type in discovered.items():
        if field not in all_fields:
            all_fields[field] = f"({inferred_type})"

    if not all_fields:
        return ""

    lines = [f"**{collection_name}** collection ({label}):"]
    for field, desc in all_fields.items():
        lines.append(f"- {field} {desc}")

    return "\n".join(lines)
