"""
cleanup_test_delegation.py — remove the Phase 1D.7 test delegation.

Removes a delegation row inserted by seed_test_delegation.py. Looks up
the row by (child tenant_id, definition_id, granted_by_tenant_id =
parent tenant_id) so it only deletes the specific delegation, not any
other assignments the child may have.

Run from the backend folder so dotenv finds the same `.env`:

    cd ~/Desktop/AEGISAXIOM/backend
    python scripts/cleanup_test_delegation.py

Defaults match the seed script's defaults.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")

if not MONGO_URL or not DB_NAME:
    print("ERROR: MONGO_URL and DB_NAME must be set in your .env", file=sys.stderr)
    sys.exit(1)


async def main(parent_name: str, child_name: str, definition_name: str):
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    parent = await db.tenants.find_one({"name": parent_name})
    child = await db.tenants.find_one({"name": child_name})
    definition = await db.ai_employee_definitions.find_one(
        {"name": definition_name}
    )

    if not parent or not child or not definition:
        print(
            "ERROR: one or more of the referenced tenants/definition not found.",
            file=sys.stderr,
        )
        sys.exit(2)

    # Match exactly the row the seed script would have inserted: the
    # child's assignment for the definition, granted by the parent.
    query = {
        "tenant_id": child.get("tenant_id"),
        "definition_id": definition.get("definition_id"),
        "granted_by_tenant_id": parent.get("tenant_id"),
    }
    existing = await db.tenant_app_assignments.find_one(query)
    if not existing:
        print(
            "Nothing to clean up — no delegation matching "
            f"'{parent_name}' → '{child_name}' for '{definition_name}'.",
        )
        return

    print("Found delegation to remove:")
    print(f"  assignment_id = {existing.get('assignment_id')}")
    print(f"  status        = {existing.get('status')}")
    print(f"  granted_at    = {existing.get('granted_at')}")

    result = await db.tenant_app_assignments.delete_one(query)
    print(f"✓ Deleted {result.deleted_count} row(s).")

    # Also clean up any instances provisioned against this delegation,
    # in case the user did test provisions. They'd otherwise dangle.
    inst_result = await db.ai_employees.delete_many(
        {"assignment_id": existing.get("assignment_id")}
    )
    if inst_result.deleted_count:
        print(
            f"✓ Also deleted {inst_result.deleted_count} instance(s) "
            "provisioned against this delegation."
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--parent", default="Beta Employees")
    p.add_argument("--child", default="Beta Employees 1")
    p.add_argument("--definition", default="Nirel's Email Classifier")
    args = p.parse_args()
    asyncio.run(main(args.parent, args.child, args.definition))
