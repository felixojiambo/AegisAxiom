"""
Policy Engine — Synchronous Gatekeeper

Centralized rule evaluation engine that runs inline before saves.
Evaluates database-driven policies against request payloads and returns
block/warn/require_field results.

All policy configs are cached in-memory with a 60-second TTL.
"""
import re
import time
import logging
from datetime import datetime, date, time as time_type
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ==================== IN-MEMORY CACHE ====================
# Note: This cache is per-process. In multi-worker deployments (e.g., Gunicorn with
# multiple uvicorn workers), invalidate_policy_cache() only clears the current process.
# Other workers will pick up changes after the 60s TTL expires naturally.

_policy_cache = {"data": None, "timestamp": 0}
CACHE_TTL = 60  # seconds


async def _get_policies(db) -> list:
    """Load enabled, non-deleted policies from cache or DB."""
    now = time.time()
    if _policy_cache["data"] is not None and (now - _policy_cache["timestamp"]) < CACHE_TTL:
        return _policy_cache["data"]

    policies = await db.policies.find(
        {"enabled": True, "deleted_at": None},
        {"_id": 0}
    ).sort("priority", -1).to_list(1000)

    _policy_cache["data"] = policies
    _policy_cache["timestamp"] = now
    return policies


def invalidate_policy_cache():
    """Call after any policy CRUD operation."""
    _policy_cache["data"] = None
    _policy_cache["timestamp"] = 0


# ==================== MAIN EVALUATION ====================

async def evaluate_policies(
    db,
    module: str,
    entity: str,
    event: str,
    payload: dict,
    user: dict = None,
    user_role: str = None,
) -> dict:
    """
    Evaluate all enabled policies matching (module, entity, event) against the payload.

    The route handler is responsible for building the payload dict with all needed
    fields, including computed/derived values (e.g., is_locked, note_word_count).

    Args:
        db: Motor database instance
        module: Module name (e.g., "time_tracking")
        entity: Entity name (e.g., "time_entry")
        event: Trigger event (e.g., "on_create", "on_submit")
        payload: Flat dict of field values to evaluate against
        user: Optional user dict for context
        user_role: Optional role string for role-based policy targeting.
            If provided, policies with applies_to_roles will only evaluate
            when user_role is in the list. If None, role filtering is skipped.

    Returns:
        {
            "allowed": bool,
            "blocked": bool,
            "block_messages": [str],
            "warnings": [str],
            "required_fields_missing": [{"field": str, "message": str}]
        }
    """
    result = {
        "allowed": True,
        "blocked": False,
        "block_messages": [],
        "warnings": [],
        "required_fields_missing": [],
    }

    try:
        policies = await _get_policies(db)
    except Exception as e:
        logger.error(f"Failed to load policies: {e}")
        return result  # fail open — allow the action if we can't load policies

    # Filter to matching policies
    matching = [
        p for p in policies
        if p.get("module") == module
        and p.get("entity") == entity
        and p.get("trigger", {}).get("event") == event
    ]

    # Role-based filtering: skip policies targeting specific roles
    # when the current user's role doesn't match
    if user_role:
        matching = [
            p for p in matching
            if not p.get("applies_to_roles") or user_role in p.get("applies_to_roles")
        ]

    for policy in matching:
        try:
            conditions_met = _evaluate_conditions(policy, payload)
        except Exception as e:
            logger.warning(f"Error evaluating policy '{policy.get('name', '?')}': {e}")
            continue  # skip broken policies, don't block the user

        if not conditions_met:
            continue

        # Execute actions
        for action in policy.get("actions", []):
            action_type = action.get("type")
            message = action.get("message", f"Policy '{policy.get('name')}' triggered")

            if action_type == "block":
                result["blocked"] = True
                result["allowed"] = False
                result["block_messages"].append(message)

            elif action_type == "warn":
                result["warnings"].append(message)

            elif action_type == "require_field":
                field = action.get("field", "")
                field_value = payload.get(field)
                if field_value is None or field_value == "" or field_value == []:
                    result["blocked"] = True
                    result["allowed"] = False
                    field_msg = message or f"Field '{field}' is required"
                    result["required_fields_missing"].append({
                        "field": field,
                        "message": field_msg,
                    })
                    result["block_messages"].append(field_msg)

    return result


# ==================== CONDITION EVALUATION ====================

def _evaluate_conditions(policy: dict, payload: dict) -> bool:
    """Evaluate all conditions for a policy using its condition_logic."""
    conditions = policy.get("conditions", [])
    if not conditions:
        return True  # no conditions = always matches

    logic = policy.get("condition_logic", "AND")

    if logic == "AND":
        return all(_evaluate_condition(c, payload) for c in conditions)
    elif logic == "OR":
        return any(_evaluate_condition(c, payload) for c in conditions)
    elif logic == "CUSTOM":
        pattern = policy.get("condition_pattern", "")
        return _evaluate_custom_pattern(pattern, conditions, payload)
    else:
        logger.warning(f"Unknown condition_logic '{logic}' in policy '{policy.get('name')}'")
        return False


def _evaluate_condition(condition: dict, payload: dict) -> bool:
    """Evaluate a single condition against the payload."""
    field = condition.get("field", "")
    operator = condition.get("operator", "")
    expected = condition.get("value")
    value_type = condition.get("value_type", "string")

    actual = payload.get(field)

    # Handle is_empty / is_not_empty first (don't need value comparison)
    if operator == "is_empty":
        return actual is None or actual == "" or actual == []
    if operator == "is_not_empty":
        return actual is not None and actual != "" and actual != []

    # Handle is_true / is_false
    if operator == "is_true":
        return bool(actual) is True
    if operator == "is_false":
        return bool(actual) is False

    # For remaining operators, coerce types for comparison
    actual = _coerce_value(actual, value_type)

    # Handle `between` before coercing expected (expected is a list [low, high])
    if operator == "between":
        if isinstance(expected, (list, tuple)) and len(expected) == 2:
            low = _coerce_value(expected[0], value_type)
            high = _coerce_value(expected[1], value_type)
            return low is not None and high is not None and actual is not None and low <= actual <= high
        return False

    # Coerce expected for all other operators
    expected = _coerce_value(expected, value_type)

    if operator == "equals":
        return actual == expected
    elif operator == "not_equals":
        return actual != expected
    elif operator == "greater_than":
        return _safe_compare(actual, expected, ">")
    elif operator == "greater_than_or_equal":
        return _safe_compare(actual, expected, ">=")
    elif operator == "less_than":
        return _safe_compare(actual, expected, "<")
    elif operator == "less_than_or_equal":
        return _safe_compare(actual, expected, "<=")
    elif operator == "contains":
        return isinstance(actual, str) and isinstance(expected, str) and expected.lower() in actual.lower()
    elif operator == "starts_with":
        return isinstance(actual, str) and isinstance(expected, str) and actual.lower().startswith(expected.lower())
    elif operator == "before":
        return _safe_compare(actual, expected, "<")
    elif operator == "after":
        return _safe_compare(actual, expected, ">")
    elif operator == "in":
        if isinstance(expected, list):
            return actual in expected
        return False
    elif operator == "not_in":
        if isinstance(expected, list):
            return actual not in expected
        return False
    else:
        logger.warning(f"Unknown operator '{operator}' for field '{field}'")
        return False


def _safe_compare(a, b, op: str) -> bool:
    """Safe numeric/comparable comparison."""
    if a is None or b is None:
        return False
    try:
        if op == ">":
            return a > b
        elif op == ">=":
            return a >= b
        elif op == "<":
            return a < b
        elif op == "<=":
            return a <= b
        return False
    except TypeError:
        return False


def _coerce_value(value: Any, value_type: str) -> Any:
    """Coerce a value to the appropriate Python type for comparison."""
    if value is None:
        return None

    try:
        if value_type == "number":
            return float(value)
        elif value_type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            return bool(value)
        elif value_type == "time":
            if isinstance(value, str):
                # Parse "HH:MM" or "HH:MM:SS"
                parts = value.split(":")
                return int(parts[0]) * 60 + int(parts[1])
            elif isinstance(value, (int, float)):
                return int(value)
            return value
        elif value_type == "date":
            if isinstance(value, str):
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
        elif value_type == "list":
            if isinstance(value, list):
                return value
            if isinstance(value, str):
                return [v.strip() for v in value.split(",")]
            return [value]
        else:
            # string — return as-is
            return str(value) if value is not None else None
    except (ValueError, TypeError, IndexError):
        return value


# ==================== CUSTOM PATTERN PARSER ====================

def _evaluate_custom_pattern(pattern: str, conditions: list, payload: dict) -> bool:
    """
    Evaluate a custom condition pattern like "(1 AND 2) OR 3".

    Indices are 1-based, referring to positions in the conditions list.
    Supports AND, OR, NOT, and parentheses.

    Uses ast.parse + node whitelist instead of eval() for safety.
    """
    import ast

    if not pattern or not pattern.strip():
        return True

    # Evaluate each condition and store results
    condition_results = {}
    for i, condition in enumerate(conditions, 1):
        condition_results[i] = _evaluate_condition(condition, payload)

    # Replace condition indices with True/False
    expr = pattern.strip()

    # Validate: only allow digits, AND, OR, NOT, parens, spaces
    if not re.match(r'^[\d\s()ANDORNTandornt]+$', expr):
        logger.warning(f"Invalid custom pattern: {expr}")
        return False

    # Replace indices (largest first to avoid partial replacement of e.g. "10" → "1""0")
    for idx in sorted(condition_results.keys(), reverse=True):
        expr = re.sub(rf'\b{idx}\b', str(condition_results[idx]), expr)

    # Normalize boolean operators to Python keywords
    expr = re.sub(r'\bAND\b', ' and ', expr, flags=re.IGNORECASE)
    expr = re.sub(r'\bOR\b', ' or ', expr, flags=re.IGNORECASE)
    expr = re.sub(r'\bNOT\b', ' not ', expr, flags=re.IGNORECASE)

    try:
        tree = ast.parse(expr, mode='eval')
    except SyntaxError:
        logger.warning(f"Failed to parse custom pattern '{pattern}' (after substitution: '{expr}')")
        return False

    # Whitelist AST node types — only boolean expressions with True/False literals
    ALLOWED_NODES = (
        ast.Expression, ast.BoolOp, ast.UnaryOp,
        ast.And, ast.Or, ast.Not, ast.Constant,
    )

    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            logger.warning(f"Disallowed AST node {type(node).__name__} in pattern '{pattern}'")
            return False
        # Constants must be boolean only
        if isinstance(node, ast.Constant) and not isinstance(node.value, bool):
            logger.warning(f"Non-boolean constant {node.value!r} in pattern '{pattern}'")
            return False

    try:
        code = compile(tree, "<policy_pattern>", "eval")
        return bool(eval(code, {"__builtins__": {}}, {}))  # noqa: S307 — AST-validated safe eval
    except Exception as e:
        logger.warning(f"Failed to evaluate custom pattern '{pattern}': {e}")
        return False


# ==================== EVENT-BUS BRIDGE ====================

async def evaluate_for_event(event: dict, db) -> dict:
    """Look up policies for (event.module, event.entity, event.action) and
    evaluate them against the event payload.

    Bridges the event-bus event shape (see services/event_bus.build_event)
    to the existing `evaluate_policies` engine. The route layer continues to
    call `evaluate_policies` directly for inline gatekeeping; this helper is
    for async event-driven evaluation (e.g., audit trails, automation).

    Decision rules:
      - allow if no policies are registered for the target (default-allow,
        fast-path before any DB read)
      - deny if ANY matching policy blocks (any-deny short-circuit)

    Args:
        event: dict with at least {module, action, record}. Optional keys:
            - entity: str (required for the lookup; falls back to record-level
              hint if absent — allow on miss to stay non-blocking)
            - payload: dict (overrides record as the eval payload if provided)
            - record: dict (default eval payload)
            - user_id, user_role: forwarded to policy evaluation
        db: Motor database instance

    Returns:
        {
          "decision": "allow" | "deny",
          "reason": str | None,
          "policies_evaluated": [policy_id, ...],
          "mutations": dict | None,
        }
    """
    module = event.get("module")
    entity = event.get("entity")
    action = event.get("action") or event.get("event")

    # Fast path — if any of the lookup keys are missing, default-allow.
    # We can't look up policies without all three.
    if not (module and entity and action):
        return {
            "decision": "allow",
            "reason": None,
            "policies_evaluated": [],
            "mutations": None,
        }

    # Fast path — check if any policies exist for this target before doing
    # the full evaluation. This keeps latency under 20ms for the no-policies
    # case (hits the cached _get_policies result on second call).
    # Re-raise on load failure rather than silently allowing — the caller
    # (route_helpers.crud_envelope) translates an exception here into a
    # 503, preserving end-to-end fail-closed semantics. Logging-and-allowing
    # would silently bypass compliance policies on every transient DB blip.
    all_policies = await _get_policies(db)

    matching_ids = [
        p.get("id") or p.get("policy_id")
        for p in all_policies
        if p.get("module") == module
        and p.get("entity") == entity
        and p.get("trigger", {}).get("event") == action
    ]

    if not matching_ids:
        return {
            "decision": "allow",
            "reason": None,
            "policies_evaluated": [],
            "mutations": None,
        }

    # Build eval payload — prefer explicit payload, fall back to record.
    payload = event.get("payload")
    if payload is None:
        payload = event.get("record") or {}

    # Reuse the existing evaluation engine — single source of truth for
    # condition logic, role filtering, and action handling.
    eval_result = await evaluate_policies(
        db,
        module=module,
        entity=entity,
        event=action,
        payload=payload,
        user=event.get("user"),
        user_role=event.get("user_role"),
    )

    if eval_result.get("blocked"):
        reason = "; ".join(eval_result.get("block_messages") or []) or "blocked by policy"
        return {
            "decision": "deny",
            "reason": reason,
            "policies_evaluated": [pid for pid in matching_ids if pid],
            "mutations": None,
        }

    return {
        "decision": "allow",
        "reason": None,
        "policies_evaluated": [pid for pid in matching_ids if pid],
        "mutations": None,
    }


async def list_targets_for_admin_ui(db) -> list:
    """Enumerate every (module, entity, event) tuple available for policy creation.

    Sources:
      1. Native modules registered via field_registry at import time
      2. Ingested apps from the `policy_targets` collection (populated by
         Wave 2 — tolerant of missing/empty collection on fresh deploys)

    Returns:
        list of dicts shaped:
          {
            "module": str,
            "entity": str,
            "events": [str, ...],
            "source": "native" | "ingestion",
            "label": "<Module> / <Entity> / <events>"
          }
    """
    from services.field_registry import list_modules_and_entities

    targets: list = []

    # 1. Native modules from the in-memory registry.
    for combo in list_modules_and_entities():
        module = combo["module"]
        entity = combo["entity"]
        events = combo.get("events") or []
        targets.append({
            "module": module,
            "entity": entity,
            "events": list(events),
            "source": "native",
            "label": _format_target_label(module, entity, events),
        })

    # 2. Ingested apps from policy_targets collection (Wave 2 schema:
    #    {app_id, module, entity, event, is_active}). Tolerant of
    #    missing collection — collections in Mongo are auto-created on
    #    first write, so a fresh deploy may not have it.
    try:
        collections = await db.list_collection_names()
        if "policy_targets" in collections:
            ingested_docs = await db.policy_targets.find(
                {"is_active": True},
                {"_id": 0, "module": 1, "entity": 1, "event": 1},
            ).to_list(5000)

            # Group by (module, entity) so multiple events on the same
            # entity collapse into one target row.
            grouped: dict = {}
            for doc in ingested_docs:
                module = doc.get("module")
                entity = doc.get("entity")
                evt = doc.get("event")
                if not (module and entity and evt):
                    continue
                key = (module, entity)
                grouped.setdefault(key, []).append(evt)

            for (module, entity), events in sorted(grouped.items()):
                # Dedupe events while preserving order.
                seen = set()
                unique_events = [e for e in events if not (e in seen or seen.add(e))]
                targets.append({
                    "module": module,
                    "entity": entity,
                    "events": unique_events,
                    "source": "ingestion",
                    "label": _format_target_label(module, entity, unique_events),
                })
    except Exception as e:
        # Log and continue — the admin UI should still get native targets
        # even if the ingestion lookup fails.
        logger.warning(f"list_targets_for_admin_ui: ingestion lookup failed: {e}")

    return targets


def _format_target_label(module: str, entity: str, events: list) -> str:
    """Human-friendly label, e.g. 'CRM / Contact / on_create, on_update'."""
    module_part = module.replace("_", " ").title() if module else "?"
    entity_part = entity.replace("_", " ").title() if entity else "?"
    if events:
        events_part = ", ".join(events)
        return f"{module_part} / {entity_part} / {events_part}"
    return f"{module_part} / {entity_part}"
