"""One-shot backfill: populate `pm_capacity_tier` + derived assignability
flags on existing user documents.

Wave 4 / Thread A1 introduced `is_assignable_as_pm` / `is_assignable_as_member`
flags directly on the user document so the new picker endpoint
`/api/projects/assignable-users` could function without `users.view`.

Wave 5 / Thread B replaces those raw flags with a single source of truth:
`pm_capacity_tier` ∈ {main_pm, overflow_pm, member_only, none}. The two
booleans are now DERIVED from the tier, so admin edits on the Capacity tab
flow through one PATCH endpoint and stay consistent.

Backfill rules (idempotent):
    role = admin              -> tier = main_pm
    role = project_manager    -> tier = main_pm
    role = project_contributor-> tier = member_only
    role = contractor         -> tier = none
    is_active = false         -> tier = none
    everyone else (active)    -> tier = none

Once the tier is set:
    is_assignable_as_pm     = (tier in {main_pm, overflow_pm})
    is_assignable_as_member = (tier != "none" AND is_active)

Idempotency: if `pm_capacity_tier` already exists on the user document, the
script DERIVES the flags from the existing tier rather than recomputing from
role. This protects admin tier overrides applied via the Capacity tab from
being silently reverted if seeding runs again on boot.

Usage (from inside the backend container):

    python -m seeds.seed_assignable_flags          # backfill once
    python -m seeds.seed_assignable_flags --force  # re-derive every user from role

Re-running without --force preserves admin-edited tiers; --force re-derives
from role for every user (used during dev resets).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from typing import Any, Dict, Optional

from db.session import get_sessionmaker
from repositories.users import UserRepository

logger = logging.getLogger(__name__)


_PM_CAPACITY_TIERS = {"main_pm", "overflow_pm", "member_only", "none"}


def _derive_tier_from_role(user: Dict[str, Any]) -> str:
    """Pick a sensible tier default based on the user's existing role."""
    role = (user.get("role") or "").strip()
    is_active = user.get("is_active", True)
    is_contractor = bool(user.get("is_contractor")) or role == "contractor"

    if not is_active:
        return "none"
    if is_contractor:
        return "none"
    if role == "admin":
        return "main_pm"
    if role == "project_manager":
        return "main_pm"
    if role == "project_contributor":
        return "member_only"
    return "none"


def _flags_for_tier(tier: str, is_active: bool) -> Dict[str, bool]:
    """Derive the two raw assignability flags from the tier + is_active."""
    pm_eligible = tier in {"main_pm", "overflow_pm"}
    member_eligible = (tier != "none") and bool(is_active)
    return {
        "is_assignable_as_pm": bool(pm_eligible),
        "is_assignable_as_member": bool(member_eligible),
    }


def _resolve_tier(user: Dict[str, Any], *, force: bool) -> str:
    """Pick the tier to apply. If the user already has a tier and we're not
    forcing, keep it; otherwise derive from role."""
    existing: Optional[str] = user.get("pm_capacity_tier")
    if not force and existing in _PM_CAPACITY_TIERS:
        return existing
    return _derive_tier_from_role(user)


async def backfill_users(users: UserRepository, *, force: bool = False) -> Dict[str, int]:
    """Re-derive tier + flags for every user. Idempotent; see module docstring.

    Phase 2a completion: `users` lives in Postgres, so this takes a
    `UserRepository` rather than the Mongo handle. The caller owns the
    transaction — `_main` commits once at the end, so a failed run leaves no
    half-stamped user set (the Mongo version had no such guarantee).
    """
    summary = {"tier_set": 0, "pm_set": 0, "member_set": 0, "scanned": 0}
    for user in await users.list_capacity_backfill_rows():
        summary["scanned"] += 1
        tier = _resolve_tier(user, force=force)
        flags = _flags_for_tier(tier, user.get("is_active", True))

        updates: Dict[str, Any] = {}
        if force or user.get("pm_capacity_tier") not in _PM_CAPACITY_TIERS:
            updates["pm_capacity_tier"] = tier
        # Derived flags ALWAYS follow tier — re-stamp on every run so
        # the two stay consistent.
        updates["is_assignable_as_pm"] = flags["is_assignable_as_pm"]
        updates["is_assignable_as_member"] = flags["is_assignable_as_member"]

        await users.update_user(user["user_id"], updates)
        if "pm_capacity_tier" in updates:
            summary["tier_set"] += 1
        summary["pm_set"] += 1
        summary["member_set"] += 1
    return summary


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-derive tier from role for every user (overrides admin Capacity-tab edits)",
    )
    args = parser.parse_args()

    # Stand-alone Postgres session. `get_sessionmaker()` builds the engine
    # lazily from DATABASE_URL and raises a clear error if it is unset, so
    # there is no init/bootstrap dance to do here.
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit(
            "DATABASE_URL not set; run from inside the backend container or "
            "export DATABASE_URL before invoking the script."
        )
    sm = get_sessionmaker()
    async with sm() as pg:
        summary = await backfill_users(UserRepository(pg), force=args.force)
        # One commit for the whole backfill — all users get the new tier/flags,
        # or none do.
        await pg.commit()
    print(
        f"Scanned {summary['scanned']} users; "
        f"set pm_capacity_tier on {summary['tier_set']}, "
        f"is_assignable_as_pm on {summary['pm_set']}, "
        f"is_assignable_as_member on {summary['member_set']}."
    )


if __name__ == "__main__":
    asyncio.run(_main())
