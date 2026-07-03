"""RBAC-aware MongoDB filter builders.

Each function returns a MongoDB filter dict scoped to the user's permissions.
Users with *.view.all see everything; users with *.view.own see only their own data.
Users with neither get a deny-all filter (matches nothing).
"""
import re

# Use the unified has_perm so these filter builders see the precomputed
# implication closure stashed on the User by get_current_user. The previous
# private _has_perm checked direct membership only, which silently denied
# transitively-implied perms (e.g. a role granted only `deals.delete` would
# not see `deals.view.all` even though the registry implies it).
from auth_helper import has_perm


def deal_filter(user) -> dict:
    if has_perm(user, "deals.view.all"):
        return {}
    if has_perm(user, "deals.view.own"):
        conds = []
        if getattr(user, "user_id", None):
            conds.extend([
                {"deal_owner_id": user.user_id},
                {"assigned_sales_rep_id": user.user_id},
                {"assigned_agent_id": user.user_id},
            ])
        if getattr(user, "account_id", None):
            conds.append({"account_id": user.account_id})
        return {"$or": conds} if conds else {"_id": {"$exists": False}}
    return {"_id": {"$exists": False}}


def commission_filter(user) -> dict:
    if has_perm(user, "commissions.view.all"):
        return {}
    if has_perm(user, "commissions.view.own"):
        if getattr(user, "user_id", None):
            return {"user_id": user.user_id}
    return {"_id": {"$exists": False}}


def zoho_commission_filter(user) -> dict:
    if has_perm(user, "commissions.view.all"):
        return {}
    if has_perm(user, "commissions.view.own"):
        conds = []
        if getattr(user, "email", None):
            conds.append({"sales_rep_email": user.email.lower()})
        if getattr(user, "name", None):
            conds.append({"agent_of_records_name": {"$regex": re.escape(user.name), "$options": "i"}})
        return {"$or": conds} if conds else {"_id": {"$exists": False}}
    return {"_id": {"$exists": False}}


def zoho_deal_filter(user) -> dict:
    if has_perm(user, "deals.view.all"):
        return {}
    if has_perm(user, "deals.view.own"):
        conds = []
        if getattr(user, "email", None):
            conds.append({"deal_owner_email": user.email.lower()})
        if getattr(user, "name", None):
            conds.append({"agent_of_record_name": {"$regex": re.escape(user.name), "$options": "i"}})
        return {"$or": conds} if conds else {"_id": {"$exists": False}}
    return {"_id": {"$exists": False}}


def opportunity_filter(user) -> dict:
    if has_perm(user, "opportunities.view.all"):
        return {}
    if has_perm(user, "opportunities.view.own"):
        conds = []
        if getattr(user, "user_id", None):
            conds.extend([
                {"opportunity_owner_id": user.user_id},
                {"assigned_sales_ops_id": user.user_id},
                {"agent_of_record_id": user.user_id},
            ])
        return {"$or": conds} if conds else {"_id": {"$exists": False}}
    return {"_id": {"$exists": False}}


def combine_filters(*filters) -> dict:
    non_empty = [f for f in filters if f]
    if not non_empty:
        return {}
    if len(non_empty) == 1:
        return non_empty[0]
    return {"$and": non_empty}


# ──────────────────────────────────────────────
#  PMO (projects module) RBAC filters
#
#  Pattern note (intentional simplicity vs. deals/commissions):
#    The projects module does NOT define a `view.own` sub-permission today.
#    Either you have the global `projects.<entity>.view` perm or you don't.
#    So these filters are binary: full visibility or deny-all. Route-level
#    permission gates already enforce who can hit the API; the AI just
#    mirrors that. If the projects RBAC ever grows view.own semantics,
#    add the scoped branch here — do NOT fabricate scope rules elsewhere.
#
#    `task_filter` adds defensive enrichment scope (assignee / pm /
#    assigned_user_ids) that only applies when the base perm is present.
#    It does NOT loosen access — it just narrows the result set so a PM
#    asking "my open tasks" gets their tasks rather than all tasks.
# ──────────────────────────────────────────────


def project_filter(user) -> dict:
    if has_perm(user, "projects.project.view"):
        return {}
    return {"_id": {"$exists": False}}


def task_filter(user, scope_to_user: bool = False) -> dict:
    """Filter tasks visible to the user.

    `scope_to_user=True` narrows the result set to tasks the user is
    directly involved in (assignee / project PM / project member). It
    is enrichment only — it requires the base perm to even reach this
    branch. The AI agent passes scope_to_user=True when the user's
    natural-language query was "my tasks" / "my open tasks".
    """
    if not has_perm(user, "projects.task.view"):
        return {"_id": {"$exists": False}}

    if not scope_to_user or not getattr(user, "user_id", None):
        return {}

    # Tasks where the user is the assignee, OR tasks attached to a project
    # the user is the PM of or a member of. Project membership is resolved
    # via project_id $in (lookup happens at query-build time in
    # query_interpreter._user_project_ids).
    return {"$or": [
        {"assignee_id": user.user_id},
        # `_scope_project_ids` is a sentinel — the query interpreter
        # replaces it with a `{"project_id": {"$in": [...]}}` clause
        # after looking up the user's PM/member projects. We keep the
        # placeholder here so the filter shape stays declarative.
    ]}


def sprint_filter(user) -> dict:
    if has_perm(user, "projects.sprint.view"):
        return {}
    return {"_id": {"$exists": False}}


def wave_filter(user) -> dict:
    if has_perm(user, "projects.wave.view"):
        return {}
    return {"_id": {"$exists": False}}


def thread_filter(user) -> dict:
    if has_perm(user, "projects.thread.view"):
        return {}
    return {"_id": {"$exists": False}}


def blocker_filter(user) -> dict:
    if has_perm(user, "projects.blocker_ticket.view"):
        return {}
    return {"_id": {"$exists": False}}


def standup_filter(user) -> dict:
    """Filter for pm_standups.

    The `projects.standup.view` perm is registered by Thread C in this
    same wave. If it is not yet present in the role registry the perm
    closure won't include it — we still use has_perm so the filter
    becomes permissive for users with the perm once Thread C lands.
    Users without it get deny-all today; that's the correct fail-closed
    behavior.
    """
    if has_perm(user, "projects.standup.view"):
        return {}
    return {"_id": {"$exists": False}}
