"""Pure resolution + filter + pagination helper for the
Contract Approval Access admin page.

The page answers one question:

    "Who in this tenant can approve contracts, and why?"

Two paths grant the permission today:

    1. ROLE   — user's role document carries ``contracts.approve``
                in its ``permissions`` array (or carries the ``*``
                wildcard).
    2. OVERRIDE — user document carries ``contracts.approve`` in
                  its ``extra_permissions`` array.

A user may carry one, both, or neither; the page must display
all four states distinctly so admins know what mutating the
override would actually accomplish.

This module is intentionally a pure-Python module:

    * no DB driver imports
    * no FastAPI / Pydantic types
    * inputs are plain lists of dicts
    * outputs are plain dicts

The route in ``routes/admin.py`` is the only orchestrator —
fetches users + roles, calls ``resolve_approval_access(...)`` and
then ``paginate_access_rows(...)``. Keeping the resolution logic
out of the route keeps it directly unit-testable without spinning
up FastAPI or a Mongo client.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

# The single permission key managed on the Contract Approval Access
# page. Co-located here (instead of imported from the route) so the
# pure helper has no upstream dependency.
CONTRACT_APPROVE_KEY = "contracts.approve"

# Approval source values exposed in the API contract. Mirrors the
# four states the page renders distinctly. "role_and_override" is
# rendered as "Role + Override" in the UI; keeping the wire value
# snake_case stays consistent with the rest of the AEGISAXIOM API.
APPROVAL_SOURCE_ROLE = "role"
APPROVAL_SOURCE_OVERRIDE = "override"
APPROVAL_SOURCE_ROLE_AND_OVERRIDE = "role_and_override"
APPROVAL_SOURCE_NONE = "none"

VALID_APPROVAL_FILTERS = (
    "all",
    "can_approve",
    "role",
    "override",
    "none",
)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
ALLOWED_PAGE_SIZES = (25, 50, 100)


# ---------------------------------------------------------------------------
# Per-user resolution
# ---------------------------------------------------------------------------


def _build_role_permission_index(
    roles: Iterable[Dict[str, Any]],
) -> Dict[str, set]:
    """Map role name -> set of permission strings.

    Tolerant of malformed role docs — missing/None ``permissions``
    becomes an empty set, missing/None ``name`` is skipped.
    """
    index: Dict[str, set] = {}
    if not roles:
        return index
    for role in roles:
        if not isinstance(role, dict):
            continue
        name = role.get("name")
        if not isinstance(name, str) or not name:
            continue
        perms = role.get("permissions")
        if isinstance(perms, list):
            index[name] = {p for p in perms if isinstance(p, str)}
        else:
            index[name] = set()
    return index


def _role_grants_approve(role_perms: set) -> bool:
    """Role grants contract approval if it has ``contracts.approve``
    explicitly or carries the ``*`` wildcard."""
    return CONTRACT_APPROVE_KEY in role_perms or "*" in role_perms


def _user_has_override(user: Dict[str, Any]) -> bool:
    extras = user.get("extra_permissions")
    if not isinstance(extras, list):
        return False
    return CONTRACT_APPROVE_KEY in extras


def _approval_source(has_role: bool, has_override: bool) -> str:
    if has_role and has_override:
        return APPROVAL_SOURCE_ROLE_AND_OVERRIDE
    if has_role:
        return APPROVAL_SOURCE_ROLE
    if has_override:
        return APPROVAL_SOURCE_OVERRIDE
    return APPROVAL_SOURCE_NONE


def resolve_approval_access(
    users: Iterable[Dict[str, Any]],
    roles: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Resolve approval source for every user.

    Returns a list of rows in the API response shape — caller
    handles search, filter, and pagination separately.

    The output shape matches the spec:

        {
            "user_id": str,
            "name": str,
            "email": str,
            "role": str,
            "can_approve": bool,
            "approval_source": "role" | "override"
                              | "role_and_override" | "none",
            "has_role_permission": bool,
            "has_override_permission": bool,
        }
    """
    role_index = _build_role_permission_index(roles)
    rows: List[Dict[str, Any]] = []
    for user in users:
        if not isinstance(user, dict):
            continue
        user_id = user.get("user_id")
        if not isinstance(user_id, str) or not user_id:
            continue
        role_name = user.get("role") or ""
        role_perms = role_index.get(role_name, set())
        has_role = _role_grants_approve(role_perms)
        has_override = _user_has_override(user)
        rows.append({
            "user_id": user_id,
            "name": user.get("name") or "",
            "email": user.get("email") or "",
            "role": role_name,
            "can_approve": has_role or has_override,
            "approval_source": _approval_source(has_role, has_override),
            "has_role_permission": has_role,
            "has_override_permission": has_override,
        })
    return rows


# ---------------------------------------------------------------------------
# Search + filter
# ---------------------------------------------------------------------------


def _normalize_search(text: Optional[str]) -> str:
    if not isinstance(text, str):
        return ""
    return text.strip().lower()


def _row_matches_search(row: Dict[str, Any], needle: str) -> bool:
    if not needle:
        return True
    blob = " ".join(
        str(row.get(field) or "").lower()
        for field in ("name", "email", "role", "user_id")
    )
    return needle in blob


def _row_matches_filter(row: Dict[str, Any], approval_filter: str) -> bool:
    """Approval-source filter. Unknown values are coerced to 'all'
    by the caller — here we only handle the validated set."""
    if approval_filter == "all":
        return True
    if approval_filter == "can_approve":
        return bool(row.get("can_approve"))
    if approval_filter == "role":
        # "role" means SOURCE = role only. Role + override doesn't
        # match because the user has explicitly elected an override.
        # That keeps the four filter buckets mutually exclusive.
        return row.get("approval_source") == APPROVAL_SOURCE_ROLE
    if approval_filter == "override":
        # Same idea — overrides bucket includes only pure overrides.
        return row.get("approval_source") == APPROVAL_SOURCE_OVERRIDE
    if approval_filter == "none":
        return row.get("approval_source") == APPROVAL_SOURCE_NONE
    return True


def coerce_approval_filter(value: Optional[str]) -> str:
    """Map an arbitrary input to one of VALID_APPROVAL_FILTERS,
    falling back to 'all'. Whitespace and case-insensitive."""
    if not isinstance(value, str):
        return "all"
    cleaned = value.strip().lower()
    if cleaned in VALID_APPROVAL_FILTERS:
        return cleaned
    return "all"


def coerce_page(value: Any) -> int:
    """Inputs may arrive as strings from query params; floor to 1."""
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    return page if page >= 1 else 1


def coerce_page_size(value: Any) -> int:
    """Map an arbitrary input to one of ALLOWED_PAGE_SIZES.

    Anything not in the allowed set (including non-numeric, zero,
    negative, or arbitrary positive numbers like 33 or 200) falls
    back to DEFAULT_PAGE_SIZE so the response shape stays
    deterministic and matches what the UI's <select> can emit.
    """
    try:
        size = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    if size in ALLOWED_PAGE_SIZES:
        return size
    return DEFAULT_PAGE_SIZE


# ---------------------------------------------------------------------------
# Sort + paginate
# ---------------------------------------------------------------------------


def _sort_key(row: Dict[str, Any]) -> Tuple[int, str, str]:
    """Stable, deterministic order:

      1. can_approve users first (True -> 0, False -> 1)
      2. then by name (case-insensitive)
      3. then by email as a tiebreaker
    """
    return (
        0 if row.get("can_approve") else 1,
        (row.get("name") or "").lower(),
        (row.get("email") or "").lower(),
    )


def filter_and_paginate(
    rows: List[Dict[str, Any]],
    *,
    search: Optional[str] = None,
    approval_filter: Optional[str] = None,
    page: Any = 1,
    page_size: Any = DEFAULT_PAGE_SIZE,
) -> Dict[str, Any]:
    """End-to-end search → filter → sort → paginate.

    Returns the API response envelope:

        {
            "items": [...rows for current page...],
            "page": int,
            "page_size": int,
            "total": int,
        }
    """
    needle = _normalize_search(search)
    coerced_filter = coerce_approval_filter(approval_filter)
    coerced_page = coerce_page(page)
    coerced_size = coerce_page_size(page_size)

    matched = [
        row for row in rows
        if _row_matches_search(row, needle)
        and _row_matches_filter(row, coerced_filter)
    ]
    matched.sort(key=_sort_key)

    total = len(matched)
    start = (coerced_page - 1) * coerced_size
    end = start + coerced_size
    page_rows = matched[start:end]

    return {
        "items": page_rows,
        "page": coerced_page,
        "page_size": coerced_size,
        "total": total,
    }
