"""Backfill ``discovery_mode`` on existing vega_discovery_sessions.

Background:
    Vega v2 introduces a ``discovery_mode`` field on every Vega session
    (three_call | single_call | freeflow). Sessions created before v2 don't
    carry this field. The conductor's READ sites tolerate missing values
    (defaulting to three_call), but downstream analytics, the Apps tab
    visibility heuristic, and the Wave 5 dashboards all expect the field
    to exist on every session — so we backfill it explicitly.

Classification rules:
    - status='complete' (any age) → set discovery_mode='three_call'.
      Backfill only — no behavioural change. The session is already done.
    - status='in_progress' AND has Call 3 analysis (calls.3.analysis exists)
      → set discovery_mode='three_call' AND status='complete'. The session
      already completed the legacy three-call flow; the v1 code never marked
      it complete because there was no completion signal in v1. Classifying
      as `single_call` would corrupt mode-segmented analytics — these reps
      ran the full Call 1/2/3 pipeline.
    - status='in_progress' AND only Calls 1-2 → leave status='in_progress',
      set discovery_mode='three_call'. Let it finish on the legacy 3-call
      flow — the rep will pick it up where they left off.

Safety:
    Default mode is --dry-run, which logs intended changes but never writes.
    --apply requires explicit confirmation (typed 'apply' at the prompt) before
    a single document is touched. --session-id <id> targets a single session
    for one-off migration.

Usage:
    # Inspect what would change:
    docker exec -it aegisaxiom-backend python -m scripts.migrate_vega_v1_to_v2

    # Target a single session:
    docker exec -it aegisaxiom-backend python -m scripts.migrate_vega_v1_to_v2 \
        --session-id vega_abc123

    # Apply to dev (interactive confirmation):
    docker exec -it aegisaxiom-backend python -m scripts.migrate_vega_v1_to_v2 --apply

Run against dev first against a snapshot. Only run against prod with Aidan
present per .waves/vega-v2/wave-5-plan.md.
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("migrate_vega_v1_to_v2")


def _classify(session: dict) -> tuple[dict, str]:
    """Return (updates_dict, human_readable_classification)."""
    status = session.get("status")
    calls = session.get("calls") or {}
    has_call3 = bool(calls.get("3", {}).get("analysis")) or bool(calls.get(3, {}).get("analysis"))

    if status == "complete":
        return ({"discovery_mode": "three_call"}, "complete → three_call (backfill only)")

    if status == "in_progress" and has_call3:
        return (
            {"discovery_mode": "three_call", "status": "complete"},
            "in_progress + has Call 3 analysis → three_call + status=complete",
        )

    return ({"discovery_mode": "three_call"}, "in_progress → three_call (will finish on legacy flow)")


async def _run(apply_changes: bool, single_session_id: str | None):
    db = database.get_db()
    coll = db.vega_discovery_sessions

    base_query: dict = {
        "$or": [
            {"discovery_mode": {"$exists": False}},
            {"discovery_mode": None},
            {"discovery_mode": ""},
        ]
    }
    if single_session_id:
        base_query = {"$and": [base_query, {"session_id": single_session_id}]}

    cursor = coll.find(
        base_query,
        {"_id": 0, "session_id": 1, "user_id": 1, "client_company": 1,
         "status": 1, "calls": 1},
    )
    candidates = await cursor.to_list(length=10_000)

    if not candidates:
        logger.info("No sessions need migration. Nothing to do.")
        return 0

    logger.info("Found %d session(s) needing migration:", len(candidates))
    for s in candidates:
        updates, classification = _classify(s)
        client = s.get("client_company") or "(no client)"
        logger.info(
            "  %s | %s | BEFORE status=%s → %s",
            s.get("session_id"),
            client,
            s.get("status"),
            classification,
        )

    if not apply_changes:
        logger.info("DRY RUN — no documents were modified. Re-run with --apply to write changes.")
        return 0

    # Apply mode — require explicit confirmation
    print(
        f"\nAbout to update {len(candidates)} session(s) in the live database. "
        "Type 'apply' to proceed, anything else to abort: ",
        end="",
        flush=True,
    )
    confirmation = input().strip().lower()
    if confirmation != "apply":
        logger.info("Aborted at confirmation prompt — no changes written.")
        return 1

    now = datetime.now(timezone.utc).isoformat()
    written = 0
    for s in candidates:
        updates, classification = _classify(s)
        updates["updated_at"] = now
        result = await coll.update_one(
            {"session_id": s["session_id"], "user_id": s["user_id"]},
            {"$set": updates},
        )
        if result.modified_count == 1:
            written += 1
            logger.info("  ✓ %s — %s", s["session_id"], classification)
        else:
            logger.warning("  - %s — update_one matched=%d modified=%d (concurrent change?)",
                           s["session_id"], result.matched_count, result.modified_count)

    logger.info("Migration complete: %d/%d session(s) updated.", written, len(candidates))
    return 0


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true",
                   help="Actually write changes (requires interactive confirmation). Default is dry-run.")
    p.add_argument("--session-id", default=None,
                   help="Target a single session_id instead of every legacy session.")
    return p.parse_args()


def main():
    args = _parse_args()
    return asyncio.run(_run(apply_changes=args.apply, single_session_id=args.session_id))


if __name__ == "__main__":
    sys.exit(main())
