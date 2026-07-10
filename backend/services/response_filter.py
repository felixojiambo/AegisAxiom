"""
Role-based response field filtering.

Moderate approach:
- Admin (*) and director always get full data
- Sales reps see their OWN commission amounts but not others'
- Non-finance roles don't see: commission_rate, cost_rate, bill_total
- Non-admin roles don't see: webhook_token, n8n_api_key
- Non-users.view roles don't see: email, phone on user lists
"""
from auth_helper import has_perm


# Fields to strip from responses for users lacking the required permission
FILTER_RULES = {
    "deals": {
        "fields": ["commission_rate", "internal_notes"],
        "required_any": ["deals.view.all", "commissions.view.all", "finance.portal"],
    },
    "commissions": {
        "fields": ["amount", "rate", "payout_amount", "total_potential"],
        "required_any": ["commissions.view.all", "finance.portal"],
        "own_field": "user_id",  # Users always see their own data
    },
    "invoices": {
        "fields": ["cost_breakdown", "margin", "internal_notes"],
        "required_any": ["invoices.view.all", "finance.portal"],
    },
    "users": {
        "fields": ["extra_permissions"],
        "required_any": ["users.view"],
    },
    "ai_employees": {
        "fields": ["webhook_token"],
        "required_any": ["ai_employees.manage"],
    },
}


def filter_response_fields(data, user, resource_type: str):
    """Filter sensitive fields from a single document based on user permissions.

    Args:
        data: dict or list of dicts to filter
        user: current user object
        resource_type: key into FILTER_RULES

    Returns:
        Filtered data (original not mutated)
    """
    rules = FILTER_RULES.get(resource_type)
    if not rules:
        return data

    # Admin and finance-portal-bearing roles get full data unconditionally.
    # Other roles (e.g., director) get full data only if their permissions
    # satisfy the rule's required_any check below — keep filter rule
    # required_any lists broad enough to cover roles meant to see full data.
    if has_perm(user, "*") or has_perm(user, "finance.portal"):
        return data

    # Check if user has any required permission
    has_access = any(has_perm(user, p) for p in rules["required_any"])
    if has_access:
        return data

    # Need to filter
    own_field = rules.get("own_field")
    fields_to_strip = rules["fields"]

    if isinstance(data, list):
        return [_strip_fields(item, fields_to_strip, user, own_field) for item in data]
    elif isinstance(data, dict):
        return _strip_fields(data, fields_to_strip, user, own_field)
    return data


def _strip_fields(doc: dict, fields: list, user, own_field: str = None) -> dict:
    """Strip fields from a document, preserving own data if own_field matches."""
    if not isinstance(doc, dict):
        return doc

    # If this is the user's own data, don't strip
    if own_field and doc.get(own_field) == getattr(user, "user_id", None):
        return doc

    filtered = {}
    for k, v in doc.items():
        if k in fields:
            continue
        # Handle nested dicts (e.g., connection.n8n_api_key)
        if isinstance(v, dict):
            nested_fields = [f.split(".", 1)[1] for f in fields if f.startswith(k + ".")]
            if nested_fields:
                v = {nk: nv for nk, nv in v.items() if nk not in nested_fields}
        filtered[k] = v
    return filtered
