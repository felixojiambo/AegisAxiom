"""One-shot migration: legacy waves_threads tasks with `status="not_started"`
are promoted to `status="scoped"`.

Wave 4 / Thread A2 backfill. Before A2, the from-template create path
bypassed `validate_task_methodology_fields` and silently stamped tasks with
the waterfall default `not_started` even on waves_threads projects. Those
rows are dropped from the W&T dashboard bucket aggregation
(`_tasks_by_status`) because they don't belong to `WAVES_THREADS_STATUSES`.

This script walks every waves_threads project, finds tasks whose status is
`not_started`, and rewrites them to `scoped` (the W&T default). Audit row
is appended to `pm_task_status_history` so the change is visible.

Run from inside the backend container:

    python -m seeds.seed_wt_status_migrate          # report-only by default
    python -m seeds.seed_wt_status_migrate --apply  # actually mutate

Idempotent: a second run finds zero rows because all matching tasks already
have status `scoped`.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

from database import get_db, init as db_init

logger = logging.getLogger(__name__)


async def _wt_project_ids(db: Any) -> list[str]:
    return [
        d["project_id"]
        async for d in db["projects"].find(
            {"project_type": "waves_threads"},
            {"_id": 0, "project_id": 1},
        )
    ]


async def migrate(db: Any, *, apply: bool) -> Dict[str, int]:
    summary = {"projects_scanned": 0, "tasks_updated": 0}
    project_ids = await _wt_project_ids(db)
    summary["projects_scanned"] = len(project_ids)
    if not project_ids:
        return summary

    cursor = db["pm_tasks"].find(
        {
            "project_id": {"$in": project_ids},
            "status": "not_started",
        },
        {"_id": 0, "task_id": 1, "project_id": 1, "status": 1},
    )
    now = datetime.now(timezone.utc)
    async for t in cursor:
        if not apply:
            print(
                f"[dry-run] would migrate task_id={t['task_id']} "
                f"project_id={t['project_id']} not_started -> scoped"
            )
            summary["tasks_updated"] += 1
            continue
        await db["pm_tasks"].update_one(
            {"task_id": t["task_id"]},
            {
                "$set": {
                    "status": "scoped",
                    "updated_at": now.isoformat(),
                }
            },
        )
        await db["pm_task_status_history"].insert_one(
            {
                "task_id": t["task_id"],
                "project_id": t["project_id"],
                "from_status": "not_started",
                "to_status": "scoped",
                "changed_at": now,
                "changed_by": "system:wt_status_migrate",
            }
        )
        summary["tasks_updated"] += 1
    return summary


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually mutate (default is dry-run with stdout report).",
    )
    args = parser.parse_args()

    db = get_db()
    if db is None:
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME", "test_database")
        if not mongo_url:
            raise SystemExit(
                "MONGO_URL not set; run from inside the backend container."
            )
        db_init(mongo_url, db_name)
        db = get_db()

    summary = await migrate(db, apply=args.apply)
    mode = "applied" if args.apply else "dry-run"
    print(
        f"[{mode}] projects_scanned={summary['projects_scanned']} "
        f"tasks_updated={summary['tasks_updated']}"
    )


if __name__ == "__main__":
    asyncio.run(_main())
