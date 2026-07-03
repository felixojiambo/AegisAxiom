"""
AI Dispatcher seed + index helper.

Idempotent. Called from `server.on_startup` after the role/permission seeds
have run. Creates:

  * Mongo indexes for `dispatcher_teams`, `dispatcher_decisions`, and
    `dispatcher_settings`. Critically, `dispatcher_decisions.message_id`
    is unique so re-runs of the n8n workflow upsert (not duplicate) a
    decision row for the same email Message-ID.
  * 4 POC teams from spec Part C.4 with realistic-looking dispatcher names.
    Skips any team whose `team_name` already exists in the collection so
    re-boots don't trample admin edits.

Names are hardcoded (no faker dep) so the n8n agent + frontend get the
exact same display strings across environments.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from models import gen_id

logger = logging.getLogger(__name__)

TEAMS_COLLECTION = "dispatcher_teams"
DECISIONS_COLLECTION = "dispatcher_decisions"
SETTINGS_COLLECTION = "dispatcher_settings"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _seed_teams() -> List[dict]:
    """Return the 4 POC team blueprints. Pure — no DB access."""
    now = _now()

    def _team(name: str, dept: str, mode: str, members_spec: List[dict]) -> dict:
        members = []
        for idx, m in enumerate(members_spec):
            members.append({
                "member_id": gen_id("dm_"),
                "name": m["name"],
                "order_index": idx,
                "skill_description": m.get("skill_description", ""),
                "availability": "on_duty",
                "created_at": now,
                "updated_at": now,
            })
        return {
            "team_id": gen_id("dt_"),
            "team_name": name,
            "department": dept,
            "assignment_mode": mode,
            "rr_pointer": 0,
            "members": members,
            "created_at": now,
            "updated_at": now,
            "created_by": "system_seed",
            "deleted_at": None,
        }

    return [
        _team(
            "Orders Pool",
            "Orders",
            "round_robin",
            [
                {"name": "Devon Marsh"},
                {"name": "Priya Ramaswamy"},
                {"name": "Marcus Chen"},
                {"name": "Sarah Okafor"},
            ],
        ),
        _team(
            "Quotes Pool",
            "Quotes",
            "round_robin",
            [
                {"name": "Jamie Lindgren"},
                {"name": "Tomas Reyes"},
                {"name": "Aisha Patel"},
            ],
        ),
        _team(
            "Cable Support",
            "Cable Support",
            "skill_based",
            [
                {
                    "name": "Hannah Brooks",
                    "skill_description": "Comcast escalations, porting issues, MACDs on cable circuits",
                },
                {
                    "name": "Lucas Petrov",
                    "skill_description": "Spectrum business circuit outages, DOCSIS troubleshooting",
                },
                {
                    "name": "Renee Caldwell",
                    "skill_description": "Cox commercial billing disputes, contract renewals on cable accounts",
                },
            ],
        ),
        _team(
            "Specialized Teams",
            "Specialized Teams",
            "skill_based",
            [
                {
                    "name": "Alex Nakamura",
                    "skill_description": "Multi-location SD-WAN deployments, complex hardware staging",
                },
                {
                    "name": "Bryn Vaughn",
                    "skill_description": "Carrier portability, LNP, large MACD batches across providers",
                },
                {
                    "name": "Diego Salazar",
                    "skill_description": "Enterprise contract negotiations, custom pricing escalations",
                },
            ],
        ),
    ]


async def ensure_indexes(db) -> None:
    """Create indexes on the dispatcher collections. Idempotent — MongoDB
    treats matching-spec create_index calls as no-ops, so this is safe to
    call on every boot.
    """
    try:
        # Decisions — message_id is the idempotency key for n8n re-runs.
        await db[DECISIONS_COLLECTION].create_index("message_id", unique=True)
        await db[DECISIONS_COLLECTION].create_index([("decided_at", -1)])
        await db[DECISIONS_COLLECTION].create_index("distro")
        await db[DECISIONS_COLLECTION].create_index("ai_department")

        # Teams — team_id lookups and soft-delete-aware list scans.
        await db[TEAMS_COLLECTION].create_index("team_id", unique=True)
        await db[TEAMS_COLLECTION].create_index([("deleted_at", 1), ("team_name", 1)])

        # Settings is a singleton (_id="dispatcher_settings_singleton") — no
        # extra index needed beyond Mongo's automatic _id index.
        logger.info("AI Dispatcher indexes ensured")
    except Exception as e:
        logger.warning("AI Dispatcher index creation warning (non-fatal): %s", e)


async def seed_dispatcher_teams(db) -> None:
    """Insert the 4 POC teams if they don't already exist (by team_name).

    We dedupe by team_name rather than team_id so that if a prior boot
    inserted a team and an admin edited it, the edit survives. Names are
    canonical for the POC and frontend / n8n read them by department slug
    anyway.
    """
    seeds = _seed_teams()
    inserted = 0
    for team in seeds:
        existing = await db[TEAMS_COLLECTION].find_one(
            {"team_name": team["team_name"], "deleted_at": None},
            {"_id": 0, "team_id": 1},
        )
        if existing:
            continue
        await db[TEAMS_COLLECTION].insert_one(team)
        inserted += 1
    if inserted:
        logger.info("AI Dispatcher seed: inserted %d new POC team(s)", inserted)
    else:
        logger.info("AI Dispatcher seed: all POC teams already present")


async def bootstrap_ai_dispatcher(db) -> None:
    """Single-call entry point for server.on_startup.

    Combines index creation and POC team seeding. Both halves are
    idempotent — safe to call on every boot.
    """
    await ensure_indexes(db)
    await seed_dispatcher_teams(db)
