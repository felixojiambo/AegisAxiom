"""One-shot backfill: assign sequential `task_number` to legacy pm_tasks rows.

This script is OPT-IN. It is NOT auto-run on backend boot. Run it manually
after a migration that imports pre-existing tasks without task_number values.

Usage (from inside the backend container):

    python -m seeds.seed_task_numbers --project_id proj_<id>      # one project
    python -m seeds.seed_task_numbers --all                       # every project

Behavior:
  * For each project_id, finds pm_tasks rows missing `task_number`.
  * Sorts them by `created_at` ascending.
  * Assigns sequential numbers starting at max(existing task_number) + 1.
  * Updates the per-project counter in `db.pm_counters` with the final seq
    via `$max` so subsequent creates don't collide with the backfilled range.
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Any

from database import get_db


def _counter_key(project_id: str) -> str:
    return f"project:{project_id}:task"


async def backfill_project(db: Any, project_id: str) -> int:
    missing = (
        await db["pm_tasks"]
        .find(
            {"project_id": project_id, "task_number": {"$exists": False}},
            {"_id": 0, "task_id": 1, "created_at": 1},
        )
        .sort("created_at", 1)
        .to_list(length=None)
    )
    if not missing:
        return 0
    existing_max_doc = await db["pm_tasks"].find_one(
        {"project_id": project_id, "task_number": {"$exists": True}},
        {"_id": 0, "task_number": 1},
        sort=[("task_number", -1)],
    )
    start = int(existing_max_doc["task_number"]) + 1 if existing_max_doc else 1
    for i, t in enumerate(missing):
        await db["pm_tasks"].update_one(
            {"task_id": t["task_id"], "task_number": {"$exists": False}},
            {"$set": {"task_number": start + i}},
        )
    final_seq = start + len(missing) - 1
    await db["pm_counters"].update_one(
        {"_id": _counter_key(project_id)},
        {"$max": {"seq": final_seq}},
        upsert=True,
    )
    return len(missing)


async def backfill_all(db: Any) -> dict:
    project_ids = [
        d["project_id"]
        async for d in db["projects"].find({}, {"project_id": 1, "_id": 0})
    ]
    summary: dict[str, int] = {}
    for pid in project_ids:
        n = await backfill_project(db, pid)
        if n:
            summary[pid] = n
    return summary


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project_id", help="Backfill a single project")
    parser.add_argument("--all", action="store_true", help="Backfill every project")
    args = parser.parse_args()

    db = get_db()
    if args.all:
        summary = await backfill_all(db)
        print(f"Backfilled {sum(summary.values())} tasks across {len(summary)} projects")
        for pid, n in summary.items():
            print(f"  {pid}: {n}")
    elif args.project_id:
        n = await backfill_project(db, args.project_id)
        print(f"Backfilled {n} tasks for {args.project_id}")
    else:
        parser.error("Pass --project_id <id> or --all")


if __name__ == "__main__":
    asyncio.run(_main())
