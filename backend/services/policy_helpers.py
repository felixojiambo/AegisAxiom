"""
Policy Engine Response Helpers

Utility for route handlers to enforce policy evaluation results consistently.
"""
from fastapi import HTTPException


def enforce_policy_result(result: dict) -> list:
    """
    Given a policy evaluation result, raise HTTPException(400) if blocked.
    Returns warnings list for the caller to include in the response.

    Note: require_field actions set blocked=True and append to block_messages,
    so they are handled by the blocked branch below.

    The detail is always a string so that frontend error handlers using
    `throw new Error(e.detail)` work correctly across the codebase.

    Args:
        result: Dict from evaluate_policies() with keys:
            allowed, blocked, block_messages, warnings, required_fields_missing

    Returns:
        List of warning message strings (empty if none).

    Raises:
        HTTPException(400) if any block or require_field action fired.
    """
    if result["blocked"]:
        messages = result["block_messages"]
        # Always return detail as a string — the frontend pattern is:
        # `const e = await res.json(); throw new Error(e.detail)`
        # If detail is an object, Error() produces "[object Object]"
        detail = " | ".join(messages) if messages else "Action blocked by policy"
        raise HTTPException(status_code=400, detail=detail)

    return result["warnings"]
